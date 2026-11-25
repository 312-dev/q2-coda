#!/usr/bin/env python3
"""A stand-in for demo's side of the player socket (docs/player.md#the-socket).

demo listens on /tmp/sock_play, starts the player, and the player connects. This does the same for
either player, sends a scenario's commands at their times, polls as demo's status thread does
(player-status and media-info every 200 ms while it believes a song is playing) and writes
everything sent and received, with times, as a transcript.

    # the new player, built for this machine
    python3 test/player/fakedemo.py --player player/build/host/q2player --music DIR --out OUT SCENARIO
    # stock hciplayer under qemu in a sandbox of an unpacked rootfs (see docs/player.md#capturing)
    python3 test/player/fakedemo.py --emu ~/q2-emu-player --hciplayer STOCKFS/usr/bin/hciplayer --music DIR --out OUT SCENARIO

OUT gets transcript.txt, player.log and, when the sandbox's shim captures it, pcm/*.raw.
Transcript lines are `seconds direction text`: `>` is demo to player, `<` player to demo. An answer to
the 200 ms poll is written only when it changes; media-info's position is written as POS and
sampled in the last line.
"""
import argparse, os, pathlib, re, select, shutil, signal, socket, subprocess, sys, threading, time

POLL = ('{player-status\\request}', '{media-info\\get-music-info}')
ENDS = ('{player-status\\exit}', '{player-status\\error}', '{player-nextstatus\\exit}', '{player-stopstatus\\exit}')
EXIT_WAIT_S = 60  # real seconds a run waits past its last step for the exits it wants (Session.wait_exits)


def song(cmd, path, start=0, cache=512, cache_min=20, flag=0, hw=17):
    """set-songurl or set-nexturl as demo's mclStartPlayer builds it (demo 0x5bf504): the start in seconds, the
    cache in KB and its fill percent (512 and 20 for a file on the card), demo's Gapless setting, the hardware
    type (17 on a Q2) and a constant 90."""
    return '{%s\\%s|ss=%d|%d|%d|%d|%d|90}' % (cmd, path.replace('}', '%0x7d'), start, cache, cache_min, flag, hw)


def gapless(path, start=0, flag=0):
    """set-gapless as demo's preload sender builds it (demo 0x5baff8): cache -1, 20 percent."""
    return '{set-gapless\\%s|ss=%d|-1|20|%d|90}' % (path.replace('}', '%0x7d'), start, flag)


sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from music import FORMATS, PAIRS, REPLAYGAIN  # noqa: E402
import music  # noqa: E402
import httpd  # noqa: E402

# A scenario's URLs name the session's web servers (httpd.py) by these, which the session puts its servers' addresses
# for as it sends: transcripts keep the names, so a fixture holds whatever ports a run had. WEB is plain HTTP; GOOD
# is HTTPS with a certificate the test CA signed for localhost, WRONG one it signed for another name, SELF a
# self-signed one for localhost.
WEB_NAMES = ('http://WEB', 'https://GOOD', 'https://WRONG', 'https://SELF')
CERTS = pathlib.Path(os.environ.get('Q2_PLAYER_CERTS') or pathlib.Path.home() / '.cache/q2-pytest/player/certs')

# formats-all's files, in the order they play
ALL_FORMATS = ['a1.flac', 'f24.flac', 'b1.mp3', 'd.m4a', 'e.ogg', *(n for n, _ in FORMATS)]


# patch/peq_ui.c peq_stock_eq's call of stock's set_equalizer_value(1): its ten gains and the flag 1
SETEQ = '{mcl-seteq\\' + '|'.join(['0.000000'] * 10) + '|1}'

# A scenario is a list of (seconds from start, command or callable(session)). Paths are the player's.
def scenarios(m):
    a1, a2, b1, b2, c96, m4a, ogg = (f'{m}/{n}' for n in ('a1.flac', 'a2.flac', 'b1.mp3', 'b2.mp3', 'c96.flac', 'd.m4a', 'e.ogg'))
    boot = [(0.0, '{mcl-dsdmode\\0}'), (0.0, '{mcl-replaygain\\0}'), (0.1, '{mcl-setoutputway\\0}'), (0.1, '{mcl-setvol\\60}')]
    usb = [(0.0, '{mcl-dsdmode\\0}'), (0.0, '{mcl-replaygain\\0}'), (0.1, '{mcl-setoutputway\\2}'), (0.1, '{mcl-softvolflag\\0}')]
    return {
        # start, pause, resume, seek forward and back, stop
        'transport': boot + [(1.0, song('set-songurl', a1)), (4.0, '{mcl-pause\\null}'), (6.0, '{mcl-play\\null}'),
                             (8.0, '{mcl-seek\\15}'), (10.0, '{mcl-seek\\2}'), (12.0, '{mcl-stop\\null}'), (14.0, None)],
        # a preloaded natural change, as Gapless on sends it about 6 s before the end
        'gapless-flac': boot + [(1.0, song('set-songurl', a1, flag=1)), (2.0, '{mcl-seek\\12}'), (4.0, gapless(a2, flag=1)), (14.0, '{mcl-seek\\17}'), (20.0, None)],
        'gapless-mp3': boot + [(1.0, song('set-songurl', b1, flag=1)), (2.0, '{mcl-seek\\12}'), (4.0, gapless(b2, flag=1)), (14.0, '{mcl-seek\\17}'), (20.0, None)],
        # the whole of two tracks, no seek: the PCM is compared with a reference decode
        'gapless-flac-full': boot + [(1.0, song('set-songurl', a1, flag=1)), (16.0, gapless(a2, flag=1)), (44.0, None)],
        'gapless-mp3-full': boot + [(1.0, song('set-songurl', b1, flag=1)), (16.0, gapless(b2, flag=1)), (56.0, None)],
        # Next while playing (set-nexturl), then a natural end with nothing preloaded
        'skip': boot + [(1.0, song('set-songurl', a1)), (4.0, song('set-nexturl', b1)), (7.0, song('set-nexturl', a2)), (9.0, '{mcl-seek\\16}'), (16.0, None)],
        # volume, EQ, the meters and the odd queries while playing
        'controls': boot + [(1.0, song('set-songurl', a1)), (3.0, '{mcl-setvol\\40}'), (3.5, '{mcl-softvolflag\\1}'), (4.0, '{mcl-setvol\\25700}'),
                            (5.0, '{mcl-seteq\\6.000000|6.000000|6.000000|6.000000|6.000000|6.000000|6.000000|6.000000|6.000000|6.000000|1}'),
                            (6.0, '{mcl-seteq\\6.000000|6.000000|6.000000|6.000000|6.000000|6.000000|6.000000|6.000000|6.000000|6.000000|0}'),
                            (6.5, '{mcl-setexplayinfo\\1}'), (7.0, '{mcl-getuvmeter\\null}'), (7.2, '{mcl-setexplayinfo\\2}'), (7.6, '{mcl-getspectrum\\null}'),
                            (8.0, '{mcl-pcmstate\\null}'), (8.2, '{get_cache_buffer\\null}'), (8.4, '{mcl-playspeed\\1}'), (8.6, '{mcl-replaygain\\1}'),
                            (8.8, '{mcl-closegapless\\null}'), (9.0, '{mcl-mqadecodemode\\0}'), (9.5, '{mcl-orderstop\\null}'), (10.0, '{mcl-stop\\null}'), (12.0, None)],
        # a file that is not there, and one that is not audio
        'errors': boot + [(1.0, song('set-songurl', f'{m}/missing.flac')), (4.0, song('set-songurl', f'{m}/junk.flac')), (7.0, song('set-songurl', a1)), (10.0, '{mcl-stop\\null}'), (12.0, None)],
        # one song to its end with nothing preloaded, then what demo sends at a queue's end
        'end': boot + [(1.0, song('set-songurl', a1)), (2.0, '{mcl-seek\\16}'), (7.5, '{mcl-orderstop\\null}'), (9.0, None)],
        # pause, seek while paused, play
        'pause-seek': boot + [(1.0, song('set-songurl', a1)), (3.0, '{mcl-pause\\null}'), (4.0, '{mcl-seek\\10}'), (6.0, '{mcl-play\\null}'), (8.0, '{mcl-stop\\null}'), (10.0, None)],
        # set-songurl while a song plays, which demo sends only when it believes nothing is playing
        'replace': boot + [(1.0, song('set-songurl', a1)), (4.0, song('set-songurl', b1)), (7.0, '{mcl-stop\\null}'), (9.0, None)],
        # a set-nexturl and a stop 20 ms on, before the player has taken that song, as a press of a server song sent
        # them before patch/playback.c playback_start_refused. Stock answers nextstatus, then stopstatus, and plays
        # nothing (captured under qemu, 2026-11-18)
        'restart-stop': boot + [(1.0, song('set-songurl', a1)), (4.0, song('set-nexturl', a1)), (4.02, '{mcl-stop\\null}'), (7.0, None)],
        # a song paused, then another picked from a list: demo's mclStartPlayer sees a song loaded and sends
        # set-nexturl, with nothing before it (demo 0x5bf6ac-0x5bf714; on the DAC its own mute and 100 ms first)
        'pause-idle': boot + [(0.5, '{mcl-pause\\null}'), (1.0, song('set-songurl', a1)), (3.0, '{mcl-pause\\null}'), (4.0, None)],
        # Memory Playback's paused boot and a restart after an output change (player_save_start): a song, then a pause
        # at once, before this player has the song open (the device, 2026-11-20: the pause was dropped and the song
        # played under a paused Now Playing)
        'start-paused': boot + [(1.0, song('set-songurl', a1, start=5)), (1.0, '{mcl-pause\\null}'), (3.0, '{mcl-play\\null}'), (5.0, None)],
        'pause-pick': boot + [(1.0, song('set-songurl', a1)), (3.0, '{mcl-pause\\null}'), (5.0, song('set-nexturl', a2)), (8.0, None)],
        # other formats: what media-info names them and how the PCM is opened
        'formats': boot + [(1.0, song('set-songurl', c96)), (4.0, song('set-nexturl', m4a)), (7.0, song('set-nexturl', ogg)), (10.0, '{mcl-stop\\null}'), (12.0, None)],
        # a whole 24-bit 96 kHz song
        'hires': boot + [(1.0, song('set-songurl', c96)), (19.0, None)],
        # every format demo lists, one after another by set-nexturl: CODEC, BITS, LENGTH, BITRATE and the PCM opened
        # each next song sent once the one before has said its figures (figures): on the host only, where run.py's
        # test_media_info_per_format reads them; stock's captures (fixtures/*-formats-all.txt) sent at the times alone
        'formats-all': boot + [x for i, f in enumerate(ALL_FORMATS) for x in
                               ([(1.0 + 2.5 * i, figures())] if i else []) + [(1.0 + 2.5 * i, song('set-songurl' if i == 0 else 'set-nexturl', f'{m}/{f}'))]]
                       + [(1.0 + 2.5 * len(ALL_FORMATS), figures()), (1.0 + 2.5 * len(ALL_FORMATS), '{mcl-stop\\null}'),
                          (3.0 + 2.5 * len(ALL_FORMATS), None)],
        # a held Next heard at 4 times (patch/seek.c's mcl-scan), let go, then a held Previous heard backward at twice
        'scan': boot + [(1.0, song('set-songurl', f'{m}/pos.flac', start=10)), (3.0, '{mcl-scan\\400}'), (5.0, '{mcl-scan\\0}'),
                        (7.0, '{mcl-scan\\-200}'), (9.0, '{mcl-scan\\0}'), (11.0, '{mcl-stop\\null}'), (12.0, None)],
        # a start offset (a resumed song): from 7 s to the end
        'start': boot + [(1.0, song('set-songurl', a1, start=7)), (16.0, None)],
        # a CUE sheet's three tracks of one file: demo starts each by ss= and cuts the one before with set-nexturl when
        # the position reaches the next track's start (demo 0x5c126c)
        'cue': boot + [(1.0, song('set-songurl', a1)), (6.0, song('set-nexturl', a1, start=5)), (11.0, song('set-nexturl', a1, start=10)), (24.0, None)],
        # a seek, then to the end: the PCM after it is the reference from the second asked
        'seek': boot + [(1.0, song('set-songurl', a1)), (3.0, '{mcl-seek\\9}'), (16.0, None)],
        # a song whose output fails while a newer song and a pause are already waiting, as a Bluetooth headset lost
        # mid-song (2026-11-21 20:32: bt_reopen gave up after demo had reloaded the song paused); then a song whose
        # output fails with nothing waiting, as any song that fails while it plays
        'replaced-failure': boot + [(0.5, stall_sink('0000_44100_2_s16.raw')), (1.0, song('set-songurl', a1)),
                                    (2.0, song('set-songurl', a2, start=5)), (2.0, '{mcl-pause\\null}'),
                                    (3.0, break_sink('0000_44100_2_s16.raw')), (5.0, None)],
        'play-failure': boot + [(0.5, stall_sink('0000_44100_2_s16.raw')), (1.0, song('set-songurl', a1)),
                                (2.0, break_sink('0000_44100_2_s16.raw')), (4.0, None)],
        # the drive of 2026-11-23: a song's write held up (the car's PCM, 10.8 s), and meanwhile demo's restart after an
        # output change (player_save_start): mcl-stop, then the same song from where it was. At that song's end demo
        # sends the next song by set-songurl (no preload). '-paused' adds the car's pause before the stop and demo's
        # pause sent with the restart, as on the drive; '-now' sends the stop and the restart in one instant to a
        # song playing, its write not held. Each ends 2 s into the next song, after the restart's exit (after_exits): at a
        # fixed time a starved player had not reached it
        'stop-restart': boot + [(0.5, stall_sink('0000_44100_2_s16.raw')), (0.5, then_song(a2)), (1.0, song('set-songurl', a1)),
                                (2.0, '{mcl-stop\\null}'), (2.5, song('set-songurl', a1, start=5)),
                                (3.0, drain_sink('0000_44100_2_s16.raw')), (3.0, after_exits(1, 2.0)), (3.0, None)],
        'stop-restart-paused': boot + [(0.5, stall_sink('0000_44100_2_s16.raw')), (0.5, then_song(a2)),
                                       (1.0, song('set-songurl', a1)), (1.5, '{mcl-pause\\null}'), (2.0, '{mcl-stop\\null}'),
                                       (2.5, song('set-songurl', a1, start=5)), (2.5, '{mcl-pause\\null}'),
                                       (3.0, drain_sink('0000_44100_2_s16.raw')), (4.0, '{mcl-play\\null}'), (4.0, after_exits(1, 2.0)),
                                       (4.0, None)],
        'stop-restart-now': boot + [(0.5, then_song(a2)), (1.0, song('set-songurl', a1)), (3.0, '{mcl-stop\\null}'),
                                    (3.0, song('set-songurl', a1, start=5)), (3.0, after_exits(1, 2.0)), (3.0, None)],
        # a preload that is not audio: the song plays to its end, then the error
        'badnext': boot + [(1.0, song('set-songurl', a1, flag=1)), (2.0, '{mcl-seek\\12}'), (4.0, gapless(f'{m}/junk.flac', flag=1)), (14.0, None)],
        # a preload cancelled by a queue edit: the song ends with nothing after it, and demo starts the next itself
        'cancel': boot + [(1.0, song('set-songurl', a1, flag=1)), (2.0, '{mcl-seek\\12}'), (4.0, gapless(a2, flag=1)), (5.0, '{mcl-closegapless\\null}'),
                          (13.0, song('set-songurl', b1)), (16.0, '{mcl-stop\\null}'), (18.0, None)],
        # a preload that opens and has no audio: two ends, which must reach demo at least 300 ms apart
        'emptynext': boot + [(1.0, song('set-songurl', a1, flag=1)), (2.0, '{mcl-seek\\14}'), (4.0, gapless(f'{m}/empty.flac', flag=1)), (12.0, None)],
        # an MP3 with 3 MB of junk after its frames, whole: the junk does not play
        'tail-full': boot + [(1.0, song('set-songurl', f'{m}/tail.mp3')), (25.0, None)],
        # a preload whose open blocks (a FIFO no one writes yet), cancelled while it blocks; the file then arrives and
        # must not play. Host only: feed() writes the FIFO from this side.
        'cancel-open': boot + [(1.0, song('set-songurl', a1, flag=1)), (2.0, '{mcl-seek\\12}'), (4.0, gapless(f'{m}/slow.flac', flag=1)),
                               (5.0, '{mcl-closegapless\\null}'), (6.0, feed('a2.flac')), (13.0, song('set-songurl', b1)), (16.0, '{mcl-stop\\null}'), (18.0, None)],
        # a preload the song outruns: its file arrives two seconds after the song's end, and plays then
        'outrun': boot + [(1.0, song('set-songurl', a1, flag=1)), (2.0, '{mcl-seek\\12}'), (4.0, gapless(f'{m}/slow.flac', flag=1)),
                          (4.0, after_exits(1, 2.0)), (4.0, feed('a2.flac')), (4.0, None)],
        # a path with a } in it, which demo writes as %0x7d
        'brace': boot + [(1.0, song('set-songurl', f'{m}/a}}1.flac')), (3.0, '{mcl-stop\\null}'), (5.0, None)],
        # every lossless and every lossy format but MP3 and FLAC at 16 bits, whole, each preloaded half a second into
        # the one before: the PCM is compared with the reference decodes
        'chain-lossless': chain(m, [n for n, _ in FORMATS if n.rsplit('.', 1)[1] in ('wav', 'aiff', 'm4a', 'wv', 'flac')]),
        'chain-lossy': chain(m, [n for n, _ in FORMATS if n.rsplit('.', 1)[1] in ('mp2', 'aac', 'opus', 'wma', 'ac3', 'dts')]),
        **{f'pair-{n}': boot + [(1.0, song('set-songurl', f'{m}/{n}1.{e}', flag=1)), (16.0, gapless(f'{m}/{n}2.{e}', flag=1)), (44.0, None)]
           for n, e in PAIRS.items()},
        # the DSP (docs/player-plan.md, phase 3). put() steps write the files the payload writes: the EQ state in
        # /mnt/data, the fade's length in /tmp, under the session's directories (the player's /mnt/data and /tmp).
        # Each sends the mcl-seteq that puts Vamp's filter in hciplayer's chain: demo's at boot with the EQ on,
        # or patch/crossfade.c's peq_chain while a fade is set.
        # Crossfade: a 2 s fade asked before the first song, the second preloaded as Gapless would
        'xfade': boot + [(0.2, put('tmp/q2xfade', (2000).to_bytes(4, 'little'))), (0.3, SETEQ), (1.0, song('set-songurl', a1, flag=1)),
                         (10.0, gapless(a2, flag=1)), (24.0, put('tmp/q2xfade', bytes(4), in_place=True)), (44.0, None)],
        # the EQ: the state written before the song (eq.state, made by the test), then another and mcl-seteq, as the
        # payload's peq_stock_eq sends it after each change, and the EQ per Output swap does
        'eq': boot + [(0.2, put('mnt/data/peq-active', 'eq.state')), (0.3, SETEQ), (1.0, song('set-songurl', a1)), (23.0, None)],
        'eq-swap': boot + [(0.2, put('mnt/data/peq-active', 'eq.state')), (0.3, SETEQ), (1.0, song('set-songurl', a1)),
                           (8.0, put('mnt/data/peq-active', 'eq2.state')), (8.1, SETEQ), (23.0, None)],
        # ReplayGain, track and album, every tag form, then an MP3 whose only gain is its LAME header's
        **{f'rg-{mode}': [(0.0, '{mcl-dsdmode\\0}'), (0.0, '{mcl-replaygain\\%d}' % n), (0.1, '{mcl-setoutputway\\0}')]
           + chain(m, [f for f, _, _ in REPLAYGAIN] + ['rglame.mp3'])[4:] for mode, n in (('track', 1), ('album', 2))},
        # the Clicker's click: one of each into silence, a wheel's into a full-scale square
        'click': boot + [(1.0, song('set-songurl', f'{m}/silence.wav')), (2.0, '{mcl-click\\wheel}'), (3.0, '{mcl-click\\button}'),
                         (4.0, '{mcl-click\\beep}'), (4.5, '{mcl-stop\\null}'), (5.0, None)],
        # and with nothing playing: two wheel clicks 300 ms apart (past the DAC's 130 ms release, waited for before the
        # first) while a song is paused, the song resumed after the hold has run out, then two more after the stop, in a
        # PCM of their own, held open CLICK_HOLD_MS and closed
        'click-idle': boot + [(1.0, song('set-songurl', f'{m}/a1.flac')), (3.0, '{mcl-pause\\null}'), (3.5, '{mcl-click\\wheel}'),
                              (3.8, '{mcl-click\\wheel}'), (7.5, '{mcl-play\\null}'), (8.5, '{mcl-stop\\null}'),
                              (9.5, '{mcl-click\\wheel}'), (9.8, '{mcl-click\\wheel}'), (14.0, None)],
        # the plug-in chirp (patch/charging.c): once into silence, then none while paused
        'chirp': boot + [(1.0, song('set-songurl', f'{m}/silence.wav')), (2.0, '{mcl-click\\charge}'), (3.0, '{mcl-pause\\null}'),
                         (3.5, '{mcl-click\\charge}'), (4.0, '{mcl-stop\\null}'), (4.5, None)],
        'click-full': boot + [(1.0, song('set-songurl', f'{m}/full.wav')), (2.0, '{mcl-click\\wheel}'), (4.0, '{mcl-stop\\null}'), (4.5, None)],
        # the level meter (mcl-setexplayinfo 1): a full-scale square, silence, a 1 kHz sine at -20 dBFS, asked twice each
        'meter': boot + [(0.5, '{mcl-setexplayinfo\\1}'), (1.0, song('set-songurl', f'{m}/full.wav')), (2.5, '{mcl-getuvmeter\\null}'),
                         (3.0, '{mcl-getuvmeter\\null}'), (3.5, song('set-nexturl', f'{m}/silence.wav')), (5.0, '{mcl-getuvmeter\\null}'),
                         (5.5, '{mcl-getuvmeter\\null}'), (6.0, song('set-nexturl', f'{m}/tone20.wav')), (7.5, '{mcl-getuvmeter\\null}'),
                         (8.0, '{mcl-getuvmeter\\null}'), (8.5, '{mcl-stop\\null}'), (9.5, '{mcl-getuvmeter\\null}'), (10.0, None)],
        # Outputs and DSD (docs/player-plan.md, phase 4). vol(l, r) is mcl-setvol's L << 8 | R, as demo's mclSetBtVol
        # and mclUsbAudioSetVol send it; 100 is unity in stock's law. DSD per output way and mode: a DSD64 DSF, the
        # DFF, a DSD128 DSF, one after another
        **{f'dsd-{way}-{mode}': [(0.0, '{mcl-dsdmode\\%d}' % mode), (0.0, '{mcl-replaygain\\0}'), (0.1, '{mcl-setoutputway\\%d}' % way),
                                 (0.1, vol(100, 100)), (1.0, song('set-songurl', f'{m}/d64.dsf', cache=4096, cache_min=50)),
                                 (3.5, song('set-nexturl', f'{m}/d64.dff', cache=4096, cache_min=50)),
                                 (6.0, song('set-nexturl', f'{m}/d128.dsf', cache=4096, cache_min=50)), (8.5, '{mcl-stop\\null}'), (9.5, None)]
           for way, mode in ((0, 0), (0, 2), (1, 2), (2, 0), (2, 1), (2, 2))},
        # the same three DSD files whole, one after another, for the bits (host): DSD64 DSF, DFF, DSD128 DSF
        **{f'dsd-full-{way}-{mode}': [(0.0, '{mcl-dsdmode\\%d}' % mode), (0.0, '{mcl-replaygain\\0}'), (0.1, '{mcl-setoutputway\\%d}' % way),
                                      (0.1, vol(100, 100)), (1.0, song('set-songurl', f'{m}/d64.dsf', cache=4096, cache_min=50)),
                                      (6.5, song('set-nexturl', f'{m}/d64.dff', cache=4096, cache_min=50)),
                                      (12.0, song('set-nexturl', f'{m}/d128.dsf', cache=4096, cache_min=50)), (18.0, None)]
           for way, mode in ((0, 0), (2, 0), (2, 1), (2, 2), (1, 0))},
        # Bluetooth: 16-bit, 24-bit 96 kHz and float songs, and the volume at unity, half, a tenth, past unity, unbalanced
        'bt': [(0.0, '{mcl-dsdmode\\0}'), (0.0, '{mcl-replaygain\\0}'), (0.1, '{mcl-setoutputway\\1}'), (0.1, vol(100, 100)),
               (1.0, song('set-songurl', a1)), (2.0, vol(50, 50)), (3.0, vol(20, 20)), (4.0, vol(150, 150)), (5.0, vol(80, 40)),
               (6.0, vol(100, 100)), (6.5, song('set-nexturl', c96)), (9.0, song('set-nexturl', m4a)), (11.5, '{mcl-stop\\null}'), (12.5, None)],
        # the same volumes, each sent two seconds of the song after the one before (at_position), not of the session's
        # clock: run.py test_bt_formats_and_volume measures each level over the song's frames (a second of the song,
        # read from whole-second positions, was under 0.75 s once, 1 of 20 runs starved)
        'bt-levels': [(0.0, '{mcl-dsdmode\\0}'), (0.0, '{mcl-replaygain\\0}'), (0.1, '{mcl-setoutputway\\1}'), (0.1, vol(100, 100)),
                      (1.0, song('set-songurl', a1)), (1.0, at_position(2)), (1.0, vol(50, 50)), (1.0, at_position(4)), (1.0, vol(20, 20)),
                      (1.0, at_position(6)), (1.0, vol(150, 150)), (1.0, at_position(8)), (1.0, vol(80, 40)), (1.0, at_position(10)),
                      (1.0, vol(100, 100)), (1.0, at_position(10, 0.5)), (1.0, song('set-nexturl', c96)), (1.0, at_position(-1, 2.5)),
                      (1.0, song('set-nexturl', m4a)), (1.0, at_position(-1, 2.5)), (1.0, '{mcl-stop\\null}'), (1.0, at_position(-1, 1.0)),
                      (1.0, None)],
        # Bluetooth across a gapless change: the 44.1 kHz pair and the 96 kHz pair, whole, at unity
        'bt-pair': [(0.0, '{mcl-dsdmode\\0}'), (0.0, '{mcl-replaygain\\0}'), (0.1, '{mcl-setoutputway\\1}'), (0.1, vol(100, 100)),
                    (1.0, song('set-songurl', a1, flag=1)), (16.0, gapless(a2, flag=1)), (44.0, None)],
        'bt-whole': [(0.0, '{mcl-dsdmode\\0}'), (0.0, '{mcl-replaygain\\0}'), (0.1, '{mcl-setoutputway\\1}'), (0.1, vol(100, 100)),
                     (1.0, song('set-songurl', f'{m}/whole.wav')), (44.0, None)],
        'bt-pair96': [(0.0, '{mcl-dsdmode\\0}'), (0.0, '{mcl-replaygain\\0}'), (0.1, '{mcl-setoutputway\\1}'), (0.1, vol(100, 100)),
                      (1.0, song('set-songurl', f'{m}/h1.flac', flag=1)), (6.0, gapless(f'{m}/h2.flac', flag=1)), (24.0, None)],
        'bt-whole96': [(0.0, '{mcl-dsdmode\\0}'), (0.0, '{mcl-replaygain\\0}'), (0.1, '{mcl-setoutputway\\1}'), (0.1, vol(100, 100)),
                       (1.0, song('set-songurl', f'{m}/h96.flac')), (24.0, None)],
        # a USB DAC: its volume in software only after mcl-softvolflag 1, then the songs of each width
        'usb': [(0.0, '{mcl-dsdmode\\0}'), (0.0, '{mcl-replaygain\\0}'), (0.1, '{mcl-setoutputway\\2}'), (0.1, '{mcl-softvolflag\\0}'),
                (0.1, vol(50, 50)), (1.0, song('set-songurl', a1)), (2.5, vol(40, 40)), (3.5, '{mcl-softvolflag\\1}'), (4.0, vol(50, 50)),
                (5.0, song('set-nexturl', a2)), (6.5, song('set-nexturl', f'{m}/f24.flac')), (8.0, song('set-nexturl', m4a)),
                (9.5, '{mcl-stop\\null}'), (10.5, None)],
        # a USB DAC at unity: a song of each width, for the formats a DAC takes
        'usb-formats': [(0.0, '{mcl-dsdmode\\0}'), (0.0, '{mcl-replaygain\\0}'), (0.1, '{mcl-setoutputway\\2}'), (0.1, '{mcl-softvolflag\\0}'),
                        (1.0, song('set-songurl', a1)), (3.0, song('set-nexturl', f'{m}/f24.flac')), (10.0, song('set-nexturl', m4a)), (13.0, '{mcl-stop\\null}'), (14.0, None)],
        # the click on a USB DAC with its volume in software at 50 (25/256): the music is scaled, the click is not
        'click-usb': [(0.0, '{mcl-dsdmode\\0}'), (0.0, '{mcl-replaygain\\0}'), (0.1, '{mcl-setoutputway\\2}'), (0.1, '{mcl-softvolflag\\1}'),
                      (0.1, vol(50, 50)), (1.0, song('set-songurl', f'{m}/silence.wav')), (2.0, '{mcl-click\\wheel}'), (3.0, '{mcl-stop\\null}'), (3.5, None)],
        # a USB DAC with mcl-softvolflag 0: an mcl-setvol mid-song scales it (stock's quirk), and the next song starts at unity
        'usb-next': [(0.0, '{mcl-dsdmode\\0}'), (0.0, '{mcl-replaygain\\0}'), (0.1, '{mcl-setoutputway\\2}'), (0.1, '{mcl-softvolflag\\0}'),
                     (1.0, song('set-songurl', a1)), (2.0, vol(50, 50)), (3.5, song('set-nexturl', a2)), (5.0, '{mcl-stop\\null}'), (6.0, None)],
        # a USB DAC that takes 48 kHz alone (run.py Q2PLAYER_RATES): the 44.1 kHz pair through a preloaded change and
        # the whole file; a 1 kHz tone then a 24-bit 96 kHz song preloaded after it; the tone with the volume in software
        **{k: usb + v for k, v in {
            'usb48-pair': [(1.0, song('set-songurl', a1, flag=1)), (16.0, gapless(a2, flag=1)), (44.0, None)],
            'usb48-whole': [(1.0, song('set-songurl', f'{m}/whole.wav')), (44.0, None)],
            'usb48-rates': [(1.0, song('set-songurl', f'{m}/tone20.wav', flag=1)), (2.0, gapless(c96, flag=1)), (24.0, None)],
            'usb48-soft': [(0.1, '{mcl-softvolflag\\1}'), (0.1, vol(50, 50)), (1.0, song('set-songurl', f'{m}/tone20.wav')), (8.0, None)],
            # a DAC that takes every rate: each song at its own, bit for bit
            'usb-capable': [(1.0, song('set-songurl', a1)), (3.0, song('set-nexturl', c96)), (20.0, None)],
            # unplugged (Q2PLAYER_GONE) mid-song, Next while it is out, then back; and out and back within one song
            'usb-gone': [(1.0, song('set-songurl', a1)), (2.5, lambda s: (s.out/'usb.gone').touch()), (4.0, song('set-nexturl', a2)),
                         (6.0, lambda s: (s.out/'usb.gone').unlink()), (9.0, '{mcl-stop\\null}'), (10.0, None)],
            'usb-gone-back': [(1.0, song('set-songurl', a1)), (2.5, lambda s: (s.out/'usb.gone').touch()),
                              (4.0, lambda s: (s.out/'usb.gone').unlink()), (7.0, '{mcl-stop\\null}'), (8.0, None)]}.items()},
        # the output way changed while a song plays: the DAC, then Bluetooth, then USB, then the DAC
        'way': boot + [(1.0, song('set-songurl', a1)), (3.0, '{mcl-setoutputway\\1}'), (3.0, vol(100, 100)), (6.0, '{mcl-setoutputway\\2}'),
                       (9.0, '{mcl-setoutputway\\0}'), (12.0, '{mcl-stop\\null}'), (13.0, None)],
        # queries with nothing loaded
        'idle': boot + [(1.0, '{player-status\\request}'), (1.2, '{media-info\\get-music-info}'), (1.4, '{mcl-pcmstate\\null}'),
                        (1.6, '{get_cache_buffer\\null}'), (1.8, '{mcl-getuvmeter\\null}'), (2.0, '{mcl-getspectrum\\null}'), (2.2, '{mcl-pause\\null}'),
                        (2.4, '{mcl-play\\null}'), (2.6, '{mcl-seek\\5}'), (2.8, '{mcl-stop\\null}'), (4.0, None)],
        **streams(boot),
        # SACD (music.py sacd.iso): the stereo area from its first track and from its second, as demo sends an .iso's
        # tracks (ss= the track's start, 4096 KB at 50%); on a USB DAC as D2P, the image's DSD as PCM
        'sacd': boot + [(1.0, song('set-songurl', f'{m}/sacd.iso', cache=4096, cache_min=50)), (7.0, None)],
        'sacd-2': boot + [(1.0, song('set-songurl', f'{m}/sacd.iso', start=2, cache=4096, cache_min=50)), (5.0, None)],
        'sacd-dst': boot + [(1.0, song('set-songurl', f'{m}/sacd-dst.iso', cache=4096, cache_min=50)), (7.0, None)],
        'sacd-dst-2': boot + [(1.0, song('set-songurl', f'{m}/sacd-dst.iso', start=2, cache=4096, cache_min=50)), (5.0, None)],
        'sacd-usb': [(0.0, '{mcl-dsdmode\\0}'), (0.0, '{mcl-replaygain\\0}'), (0.1, '{mcl-setoutputway\\2}'), (0.1, '{mcl-setvol\\25700}'),
                     (1.0, song('set-songurl', f'{m}/sacd.iso', cache=4096, cache_min=50)), (7.0, None)],
    }


def web(plan, name, base='http://WEB'):
    """A URL of the session's server (httpd.py) for the music directory's name, misbehaving as plan says."""
    return f'{base}/s/{plan}/{name}'


def cache_polls(start, end, every=0.5):
    """get_cache_buffer from start to end, as a page that shows a stream's buffering asks."""
    return [(round(start + i * every, 3), '{get_cache_buffer\\null}') for i in range(int((end - start) / every) + 1)]


def streams(boot):
    """Phase 5's scenarios (docs/player-plan.md): songs from the session's web servers, as DLNA, the phone's push
    and Tidal hand them over, with demo's cache fields for each: 1024 KB at 20% for a FLAC, 2048 for WAV, -1 none."""
    a1, a2, p1 = (web('-', n) for n in ('a1.flac', 'a2.flac', 'p1.wav'))
    def one(url, cache=1024, pct=20, until=24.0, more=()):
        return boot + sorted([(1.0, song('set-songurl', url, cache=cache, cache_min=pct))] + cache_polls(1.2, until - 1) + list(more),
                             key=lambda x: x[0]) + [(until, None)]
    def qq(songid, key, until, more=()):
        keyfile = [(0.5, put(f'tmp/qm_{songid}', key.encode() + b'\n'))] if key else []
        return one(web('-', f'qq.flac?vkey=x&qm_songid={songid}'), until=until, more=keyfile + list(more))
    return {
        # a FLAC over HTTP played to its end, a seek on the way
        'http': one(a1, more=[(6.0, '{mcl-seek\\15}')]),
        # no cache asked (demo's -1), then stop
        'http-nocache': one(a1, cache=-1, until=8.0, more=[(6.0, '{mcl-stop\\null}')]),
        # a WAV from a server slower than it plays (150,000 B/s against 176,400): the cache fills, drains, waits
        'http-slow': one(web('rate=150000', 'p1.wav'), cache=2048, until=16.0, more=[(14.0, '{mcl-stop\\null}')]),
        # a FLAC at 50,000 B/s: the first fill, 20% of 1024 KB, takes 4.2 s
        'http-fill': one(web('rate=50000', 'a1.flac'), until=12.0, more=[(10.0, '{mcl-stop\\null}')]),
        # the same at 4096 KB and 50%, as demo asks for DSD
        'http-big': one(web('rate=400000', 'p1.wav'), cache=4096, pct=50, until=16.0, more=[(14.0, '{mcl-stop\\null}')]),
        # the server stops for 8 s at 1 MB (about 5.7 s of the WAV) and then goes on
        'http-stall': one(web('stall=1000000:8', 'p1.wav'), cache=2048, until=32.0),
        # the first connection closes at 1 MB; a reconnect from there gets the rest
        'http-drop': one(web('drop=1000000', 'p1.wav'), cache=2048, until=26.0),
        # every connection ends at 1 MB of 3.5: the stream ends early
        'http-cut': one(web('cut=1000000', 'p1.wav'), cache=2048, until=16.0),
        # a redirect, and a server without ranges with a seek
        'http-redirect': one(web('redirect', 'a1.flac'), until=10.0, more=[(8.0, '{mcl-stop\\null}')]),
        'http-norange': one(web('norange', 'a1.flac'), until=12.0, more=[(4.0, '{mcl-seek\\12}'), (10.0, '{mcl-stop\\null}')]),
        # not there (404)
        'http-missing': one(web('-', 'nothing.flac'), until=4.0),
        # a preloaded change from one stream to the next
        'http-gapless': boot + [(1.0, song('set-songurl', a1, cache=1024, flag=1)), (2.0, '{mcl-seek\\12}'),
                                (4.0, gapless(a2, flag=1)), (14.0, '{mcl-seek\\17}'), (20.0, None)],
        # Next from one stream to another
        'http-next': boot + [(1.0, song('set-songurl', a1, cache=1024)), (4.0, song('set-nexturl', p1, cache=2048)),
                             (8.0, '{mcl-stop\\null}'), (10.0, None)],
        # the whole of a FLAC and a WAV over HTTP, for the PCM: no cache asked (demo's -1), and 2048 KB
        'http-whole': one(a1, cache=-1, until=23.0),
        'http-whole-wav': one(p1, cache=2048, until=23.0),
        # a stop while the first fill waits on a slow server (2048 KB at 20% at 20,000 B/s: 20 s)
        'http-stop-open': one(web('rate=20000', 'p1.wav'), cache=2048, until=6.0, more=[(3.0, '{mcl-stop\\null}')]),
        # QQ Music: the key in /tmp/qm_ID as demo writes it, an encrypted FLAC (music.py qq.flac); a seek; no key file; a
        # key the library refuses; a song id with a character stock refuses
        'qq': qq('qq1', music.QQ_KEY, until=23.0),
        'qq-seek': qq('qq1', music.QQ_KEY, until=12.0, more=[(4.0, '{mcl-seek\\12}')]),
        'qq-nokey': qq('qq1', None, until=4.0),
        'qq-badkey': qq('qq1', 'bad', until=4.0),
        'qq-badid': qq('qq.1', music.QQ_KEY, until=4.0),
        # HTTPS: a certificate the bundle's CA signed for the name, one for another name, a self-signed one
        'https-good': one(web('-', 'a1.flac', 'https://GOOD'), until=8.0, more=[(6.0, '{mcl-stop\\null}')]),
        'https-wrong': one(web('-', 'a1.flac', 'https://WRONG'), until=6.0, more=[(5.0, '{mcl-stop\\null}')]),
        'https-self': one(web('-', 'a1.flac', 'https://SELF'), until=6.0, more=[(5.0, '{mcl-stop\\null}')]),
    }


def vol(l, r):
    return '{mcl-setvol\\%d}' % (l << 8 | r)


def chain(m, names, length=6):
    """names, each about `length` seconds, one after another: the second's set-gapless half a second into the first,
    each later one's as the song before it starts (at the exit before), since a change of width drains the PCM first
    and the songs drift from any fixed times."""
    boot = [(0.0, '{mcl-dsdmode\\0}'), (0.0, '{mcl-replaygain\\0}'), (0.1, '{mcl-setoutputway\\0}'), (0.1, '{mcl-setvol\\60}')]
    def queue(s): s.on_exit = [gapless(f'{m}/{n}', flag=1) for n in names[2:]]
    steps = [(1.0, song('set-songurl', f'{m}/{names[0]}', flag=1)), (1.5, gapless(f'{m}/{names[1]}', flag=1)), (1.6, queue)]
    return boot + steps + [(3.0 + (length + 1) * len(names), None)]


def put(path, data, in_place=False):
    """A step: write data (bytes, or the name of a file in the session's directory) to path under the session's
    directory, as the payload does: whole, under a temporary name and renamed; or in_place, into the file the player
    has mapped, as demo changes /tmp/q2xfade once made."""
    def go(s):
        out = s.out / path
        out.parent.mkdir(parents=True, exist_ok=True)
        body = data if isinstance(data, bytes) else (s.out / data).read_bytes()
        if in_place:
            with open(out, 'r+b') as f: f.write(body)
            return
        tmp = out.with_name(out.name + '.tmp')
        tmp.write_bytes(body)
        tmp.replace(out)
    return go


def stall_sink(name):
    """A step: the sink file the next PCM open makes (output.c sink_open's name), made a FIFO that this side holds open
    and never reads, so once its 64 KB are full the player's write waits, as a write waits in bt_reopen for bluealsa's
    PCM after a headset's loss."""
    def go(s):
        os.mkfifo(s.out / 'pcm' / name)
        s.stall_fd = os.open(s.out / 'pcm' / name, os.O_RDONLY | os.O_NONBLOCK)
    return go


def break_sink(name):
    """A step: the stalled sink's reader closed, so the waiting write fails (EPIPE; the player ignores SIGPIPE), as a
    PCM that did not come back. The FIFO goes with it: the session reads every .raw left at its end."""
    def go(s):
        os.close(s.stall_fd)
        os.unlink(s.out / 'pcm' / name)
    return go


def then_song(path):
    """A step: the song demo sends by set-songurl at the next {player-status\\exit}, as mclNextSong(1) does with no
    preload."""
    def go(s): s.on_exit.append(song('set-songurl', path))
    return go


def after_exits(n, extra=0.0, heard=None):
    """A step: wait (pumping, at most EXIT_WAIT_S of real time) for the player's n-th {player-status\\exit}, then
    `extra` seconds more of the player's clock, or with heard, until a media-info answer after that exit puts the next
    song at `heard` seconds or more. The steps after it are due at once: a step that must follow a song's end, on a
    machine whose stalls leave the player's clock behind the session's."""
    def go(s):
        deadline = time.monotonic() + EXIT_WAIT_S
        while s.exits() < n and time.monotonic() < deadline:
            s.pump(min(deadline, time.monotonic() + 0.1))
        if heard is not None:
            at = [k for m, k, _ in s.ends if m == '{player-status\\exit}'][n - 1] if s.exits() >= n else len(s.answers)
            def there():
                return any(m.startswith('{media-info') and '|' in m and int(m.split('|')[3]) >= heard for _, m in s.answers[at:])
            while not there() and time.monotonic() < deadline:
                s.pump(min(deadline, time.monotonic() + 0.05))
        s.pump(time.monotonic() + extra / s.speed)
    return go


def at_position(p, extra=0.0):
    """A step: wait (pumping, at most EXIT_WAIT_S of real time) until media-info's position reads p seconds or more,
    then `extra` seconds more of the player's clock (p below 0: those seconds alone): a step tied to where the song is
    heard, not to the session's clock, which a starved player falls behind."""
    def go(s):
        deadline = time.monotonic() + EXIT_WAIT_S
        while not (s.positions and s.positions[-1][1] >= p) and time.monotonic() < deadline:
            s.pump(min(deadline, time.monotonic() + 0.05))
        s.pump(time.monotonic() + extra / s.speed)
    return go


def logged(text, extra=0.0):
    """A step: wait (pumping, at most EXIT_WAIT_S of real time) until the player's log (player.log) has text, then
    `extra` seconds more of the player's clock: a step after something the player does on its own time."""
    def go(s):
        deadline, log = time.monotonic() + EXIT_WAIT_S, s.out / 'player.log'
        while time.monotonic() < deadline and text not in (log.read_text(errors='replace') if log.exists() else ''):
            s.pump(min(deadline, time.monotonic() + 0.05))
        s.pump(time.monotonic() + extra / s.speed)
    return go


def figures():
    """A step: wait (pumping, at most EXIT_WAIT_S of real time) for media-info's figures of the song last sent: an
    answer to a query asked under its command, after the song before it was cut (Session.ends, by order on the
    socket), so the next command never comes before this song has said what it is."""
    def go(s):
        deadline = time.monotonic() + EXIT_WAIT_S
        def there():
            n = s.songs_sent
            cuts = [k for m, _, k in s.ends if m in ('{player-nextstatus\\exit}', '{player-status\\exit}')]
            if n > 1 and len(cuts) < n - 1: return False
            start = cuts[n - 2] if n > 1 else 0
            return any(a == n and '|' in m for a, m in list(zip(s.mi_asked, s.mi_answers))[start:])
        while not there() and time.monotonic() < deadline:
            s.pump(min(deadline, time.monotonic() + 0.05))
    return go


def drain_sink(name):
    """A step: the stalled sink read again, so the waiting write goes on, as the car's write did when its PCM came
    free (2026-11-23). The FIFO is unlinked and what it carries, until the player closes it, is copied to a file of
    the same name on a thread (s.drain), which the caller joins before reading the session's .raw files."""
    def go(s):
        path, fd = s.out / 'pcm' / name, s.stall_fd
        os.unlink(path)
        def copy():
            os.set_blocking(fd, True)
            with open(path, 'wb') as f:
                while (b := os.read(fd, 65536)): f.write(b)
            os.close(fd)
        s.drain = threading.Thread(target=copy, daemon=True)
        s.drain.start()
    return go


def feed(name):
    """A step: write the music directory's `name` into the session's own FIFO for slow.flac (Session.slow), on a
    thread, since the write waits for the player to open the FIFO."""
    def go(s):
        def write():
            try:
                with open(s.slow, 'wb') as f: f.write((pathlib.Path(s.music)/name).read_bytes())
            except BrokenPipeError: pass  # the player gave up reading: a cancelled preload
        threading.Thread(target=write, daemon=True).start()
    return go


def starve(proc):
    """Q2_TEST_STARVE=STOP_MS/EVERY_MS: the player stopped (SIGSTOP) for STOP_MS of every EVERY_MS of real time, then
    let go (SIGCONT), as a machine busy with a build leaves it unscheduled, but every time. The returned event, once
    set, ends it with the player let go."""
    done = threading.Event()
    spec = os.environ.get('Q2_TEST_STARVE')
    if not spec: return done
    stop_ms, every_ms = (int(x) for x in spec.split('/'))
    def go():
        while not done.wait((every_ms - stop_ms) / 1000):
            try: os.kill(proc.pid, signal.SIGSTOP)
            except ProcessLookupError: return
            done.wait(stop_ms / 1000)
            try: os.kill(proc.pid, signal.SIGCONT)
            except ProcessLookupError: return
    threading.Thread(target=go, daemon=True).start()
    return done


def cpu_seconds(pid):
    """User and system time so far of pid and everything under it (the sandbox's qemu is a grandchild)."""
    tick, total, kids = os.sysconf('SC_CLK_TCK'), 0.0, {}
    for d in pathlib.Path('/proc').iterdir():
        if not d.name.isdigit(): continue
        try: f = (d / 'stat').read_text().rsplit(')', 1)[1].split()
        except OSError: continue
        kids.setdefault(int(f[1]), []).append((int(d.name), (int(f[11]) + int(f[12])) / tick))
    todo = [pid]
    while todo:
        for child, t in kids.get(todo.pop(), []):
            total += t; todo.append(child)
    return total


class Session:
    def __init__(self, out):
        self.out = pathlib.Path(out); self.out.mkdir(parents=True, exist_ok=True)
        self.tmp = self.out / 'tmp'; shutil.rmtree(self.tmp, ignore_errors=True); self.tmp.mkdir()
        # the music directory's slow.flac, as a scenario names it, is this session's own FIFO (url): one shared by tests
        # side by side would give one test's bytes to another's player
        self.slow = self.out / 'slow.flac'
        if self.slow.exists() or self.slow.is_symlink(): self.slow.unlink()
        os.mkfifo(self.slow)
        self.srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.srv.bind(str(self.tmp / 'sock_play')); self.srv.listen(1)
        # q2player's status push (docs/internals.md#player-status-push), bound as demo binds it: each datagram's time
        self.push = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        self.push.bind(str(self.tmp / 'q2player.push')); self.push.setblocking(False)
        self.pushes = []
        self.lines, self.seen, self.t0, self.conn, self.playing, self.preload, self.speed = [], {}, None, None, False, False, 1
        self.music = None  # the music directory on this machine, for steps that act on its files
        # set-gapless commands sent one at each {player-status\\exit}, as demo sends one per song; a callable is called
        # with the session instead (demo's own work at a change, such as /tmp/q2xfade's next word)
        self.on_exit = []
        self.wait_exits = 0  # at the last step, wait for this many {player-status\\exit} first (run): 0, none
        self.wait_ends, self.ended = 0, 0  # the same for every event that ends a song (ENDS), and their count so far
        self.ends = []  # each such event, and how many answers (self.answers) and media-info answers came before it: order, not time
        certs = httpd.certificates(CERTS)
        root = lambda: pathlib.Path(self.music)
        self.web = [httpd.serve(root)] + [httpd.serve(root, (certs / f'{n}.pem', certs / f'{n}.key')) for n in ('good', 'wrong', 'self')]
        self.ca = certs / 'ca.pem'

    def url(self, text):
        """text with each of WEB_NAMES made its server's address, and the music directory's slow.flac this session's."""
        if self.music: text = text.replace(f'{self.music}/slow.flac', str(self.slow))
        for name, srv in zip(WEB_NAMES, self.web):
            text = text.replace(name, f'{name.split(":")[0]}://{"127.0.0.1" if srv is self.web[0] else "localhost"}:{srv.port}')
        return text

    def note(self, direction, text):
        self.lines.append(f'{self.now():8.3f} {direction} {text}')

    def exits(self):
        """The {player-status\\exit} events so far."""
        return sum(m == '{player-status\\exit}' for _, m in self.answers)  # every one, where lines keeps changes

    def now(self):
        return (time.monotonic() - self.t0) * self.speed  # in the player's seconds

    def send(self, text, quiet=False):
        if not quiet: self.note('>', text)
        # which song each media-info query was asked under: its answer is the n-th (the player answers each once, in
        # order), so an answer is tied to the song commands sent before its query, not to when this side read it
        if text.startswith(('{set-songurl', '{set-nexturl')): self.songs_sent += 1
        if text == POLL[1]: self.mi_asked.append(self.songs_sent)
        try: self.conn.sendall(self.url(text).encode())
        except OSError: pass

    def pump(self, until):
        """Read replies until the given monotonic time, polling as demo does while a song plays."""
        next_poll = last = time.monotonic()
        while True:
            now = time.monotonic()
            self.worst, last = max(self.worst, now - last), now
            if now >= until: return
            if self.playing and now >= next_poll:
                for p in POLL: self.send(p, quiet=True)
                next_poll = now + 0.2 / self.speed
            r, _, _ = select.select([self.conn], [], [], min(0.02 / self.speed, until - now))
            # pushes are taken on the loop's own passes, never waking it: the poll's timing, which the protocol
            # fixtures' once-a-second positions are sampled from, stays what it was without them; a push sent
            # before an answer is taken before that answer
            while True:
                try: self.push.recv(16)
                except BlockingIOError: break
                self.pushes.append(self.now())
            if not r: continue
            data = self.conn.recv(4096)
            if not data:
                self.note('<', '(closed)'); self.playing = False; time.sleep(0.05); continue
            for m in re.findall(r'\{[^}]*\}', data.decode(errors='replace')):
                name = m[1:m.index('\\')]
                shown = m
                if name == 'media-info' and '|' in m:
                    # bitrate|rate|length|position|codec|bits: the position moves every poll, so it is kept apart
                    f = m[12:-1].split('|')
                    self.positions.append((self.now(), int(f[3])))
                    shown = '{media-info\\' + '|'.join(f[:3] + ['POS'] + f[4:]) + '}'
                if name in ('media-info', 'player-status'): self.answers.append((self.now(), m))  # as answered, position and all
                if name == 'media-info': self.mi_answers.append(m)
                if m in ENDS: self.ended += 1; self.ends.append((m, len(self.answers), len(self.mi_answers)))
                # an answer to the 200 ms poll is written only when it differs from the last one of its kind
                if name in ('media-info', 'player-status') and self.seen.get(name) == shown: pass
                else: self.note('<', shown)
                self.seen[name] = shown
                # demo stops polling at an exit unless it had sent a preload: then mclNextSong(1) sends nothing and
                # the player goes on with the preloaded file alone
                if m == '{player-status\\exit}' and self.on_exit:
                    nxt = self.on_exit.pop(0)
                    nxt(self) if callable(nxt) else self.send(nxt)
                    continue
                if m == '{player-status\\exit}' and self.preload: self.preload = False
                elif m in ('{player-status\\exit}', '{player-stopstatus\\exit}', '{player-status\\error}'): self.playing = False

    def run(self, steps, launch, speed=1):
        """speed: the player's clock runs this many times faster (its file sink's Q2PLAYER_SPEED), so the steps' times
        and the poll's period shrink by it."""
        self.positions, self.answers, self.speed = [], [], speed
        self.songs_sent, self.mi_asked, self.mi_answers = 0, [], []  # media-info's queries and answers, in order (send)
        self.ran = []  # (its time in the steps, the time it ran) of each callable step: late when this side was held up
        self.worst = 0.0  # the longest this side's read loop went between passes, real seconds (pump)
        proc = self.proc = launch(self)  # a step may signal it (run.py test_car_release_while_paused)
        starving = starve(proc)
        self.srv.settimeout(30)
        self.conn, _ = self.srv.accept()
        self.t0 = time.monotonic()
        for at, what in steps:
            self.pump(self.t0 + at / speed)
            if what is None:
                # a test that wants the whole of what plays (run.py check_exact, check_chain) waits for the player's
                # own end, its wait_exits'th {player-status\exit}, sent once the PCM is closed: the step's time is
                # the player's clock, which a starved machine leaves behind (the file sink's 500 ms is 62 ms at
                # speed 8, and the time a stall runs it dry is not caught up), so a capture ended there came up short
                deadline = time.monotonic() + EXIT_WAIT_S
                while (self.exits() < self.wait_exits or self.ended < self.wait_ends) and time.monotonic() < deadline:
                    self.pump(min(deadline, time.monotonic() + 0.1))
                break
            if callable(what): what(self); self.ran.append((at, self.now())); continue  # when it ran, for a test to read
            if what.startswith(('{set-songurl', '{set-nexturl')): self.playing, self.preload = True, False
            if what.startswith('{set-gapless'): self.preload = True
            if what.startswith('{mcl-closegapless'): self.preload = False
            self.send(what)
        self.end_at = self.now()  # when the player is stopped: the run's last moment, as this side counts
        starving.set()
        cpu = cpu_seconds(proc.pid)
        self.conn.close(); proc.terminate()
        for srv in self.web: srv.shutdown(); srv.server_close()
        try: proc.wait(5)
        except subprocess.TimeoutExpired: proc.kill()
        pos = ' '.join(f'{t:.1f}:{p}' for t, p in self.positions[::5])  # one a second
        (self.out / 'transcript.txt').write_text('\n'.join(self.lines) + f'\n# positions (every fifth poll, time:seconds) {pos}\n# cpu {cpu:.2f} s\n')


def launch_host(player, music, speed=1, env=None):
    """env: more of the player's environment, such as the file sink's Q2PLAYER_BT_RATE and Q2PLAYER_FORMATS."""
    def go(s):
        (s.out / 'pcm').mkdir(exist_ok=True)
        for f in (s.out / 'pcm').glob('*'): f.unlink()
        shutil.rmtree(s.out / 'mnt/data', ignore_errors=True); (s.out / 'mnt/data').mkdir(parents=True)
        # the CA bundle q2player checks HTTPS against (Vamp's player's, docs/internals.md#stock-https): the test CA's
        (s.out / 'etc/ssl/certs').mkdir(parents=True, exist_ok=True)
        shutil.copy(s.ca, s.out / 'etc/ssl/certs/ca-certificates.crt')
        e = dict(os.environ, Q2PLAYER_SOCK=str(s.tmp / 'sock_play'), Q2PLAYER_PUSH=str(s.tmp / 'q2player.push'), Q2PLAYER_PCM=str(s.out / 'pcm'), Q2PLAYER_SPEED=str(speed), **(env or {}))
        # the host build reads patch/peq.h's files (/mnt/data/peq-active, /tmp/q2xfade, ...) under its directory
        return subprocess.Popen([player], stdout=open(s.out / 'player.log', 'w'), stderr=subprocess.STDOUT, env=e, cwd=s.out)
    return go


def launch_emu(emu, binary, music, name='hciplayer', args=('-ao', 'alsa'), killall=None, buffer_ms=None, rate=None):
    """binary inside a bwrap sandbox of emu/rootfs under qemu, as run.sh starts demo; /tmp is the session's.
    killall is when, in seconds, a shell in the sandbox runs demo's `killall -9 hciplayer`, after writing each
    process's name and command line to ps.txt. buffer_ms has the shim play the
    capture as a card with that much buffer (Q2EMU_PCM_BUFFER_MS: a delay, and a rewind that takes frames back). rate has
    every PCM take that rate whatever is asked, as bluealsa offers only its transport's (Q2EMU_RATE)."""
    emu = pathlib.Path(emu)
    def go(s):
        (s.out / 'pcm').mkdir(exist_ok=True)
        for f in (s.out / 'pcm').glob('*'): f.unlink()
        data = s.out / 'mnt/data'
        shutil.rmtree(data, ignore_errors=True); data.mkdir(parents=True)
        extra = ['--bind', data, '/mnt/data']
        if (emu / 'rootfs/etc/ssl/certs/ca-certificates.crt').exists():  # the test CA as the bundle HTTPS is checked against
            extra += ['--ro-bind', s.ca, '/etc/ssl/certs/ca-certificates.crt']
        run = [f'/usr/bin/{name}', *args]
        if killall is not None:
            run = ['/bin/sh', '-c', f'{" ".join(run)} & sleep {killall}; for p in /proc/[0-9]*; do echo "$(cat $p/comm): '
                   f'$(tr "\\0" " " < $p/cmdline)"; done > /emu-log/ps.txt; killall -9 {name}; echo "killall $?" >> /emu-log/ps.txt; wait']
        cmd = ['bwrap', '--die-with-parent', '--unshare-pid', '--unshare-user', '--uid', '0', '--gid', '0',
               '--bind', emu / 'rootfs', '/', '--proc', '/proc', '--dev', '/dev', '--bind', s.tmp, '/tmp',
               '--ro-bind', music, '/mnt/mmc', '--bind', s.out, '/emu-log', '--ro-bind', emu / 'state', '/emu',
               '--ro-bind', emu / 'state/mtd_debug', '/usr/sbin/mtd_debug',
               '--ro-bind', emu / 'qemu-mipsel-static', '/usr/libexec/qemu-binfmt/mipsel-binfmt-P',
               '--ro-bind', emu / 'shim/libq2emu.so', '/lib/libq2emu.so', '--ro-bind', binary, f'/usr/bin/{name}', *extra,
               '--setenv', 'PATH', '/usr/bin:/bin:/usr/sbin:/sbin', '--setenv', 'HOME', '/root',
               '--setenv', 'Q2EMU_LOG', '/emu-log/shim.log', '--setenv', 'Q2EMU_PCM', 'null', '--setenv', 'Q2EMU_PCMDIR', '/emu-log/pcm',
               *(['--setenv', 'Q2EMU_PCM_BUFFER_MS', str(buffer_ms)] if buffer_ms else []),
               *(['--setenv', 'Q2EMU_RATE', str(rate)] if rate else []),
               '--setenv', 'LD_PRELOAD', '/lib/libq2emu.so', '--chdir', '/tmp', *run]
        return subprocess.Popen([str(c) for c in cmd], stdout=open(s.out / 'player.log', 'w'), stderr=subprocess.STDOUT,
                                env={k: v for k, v in os.environ.items() if k != 'LD_PRELOAD'})  # q2-heavy's nodump.so makes bwrap fail its uid map
    return go


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('scenario')
    ap.add_argument('--out', required=True)
    ap.add_argument('--music', required=True, help='the songs test/player/music.py makes')
    ap.add_argument('--player', help='a player built for this machine')
    ap.add_argument('--emu', help='an emulator directory (rootfs/, state/, shim/, qemu-mipsel-static)')
    ap.add_argument('--hciplayer', help='with --emu: the MIPS player binary to run as /usr/bin/hciplayer')
    ap.add_argument('--killall', type=float, help="with --emu: run demo's killall -9 hciplayer this many seconds in")
    ap.add_argument('--buffer-ms', type=int, help='with --emu: the capture plays as a card with this much buffer')
    ap.add_argument('--rate', type=int, help="with --emu: every PCM takes this rate, as bluealsa's transport does")
    a = ap.parse_args()
    music = str(pathlib.Path(a.music).resolve())
    if a.emu:
        launch, m = launch_emu(a.emu, pathlib.Path(a.hciplayer).resolve(), music,
                               killall=a.killall, buffer_ms=a.buffer_ms, rate=a.rate), '/mnt/mmc'
    else:
        launch, m = launch_host(a.player, music), music
    s = Session(a.out)
    s.music = pathlib.Path(a.music).resolve()
    s.run(scenarios(m)[a.scenario], launch)
    print((s.out / 'transcript.txt').read_text())


if __name__ == '__main__':
    main()
