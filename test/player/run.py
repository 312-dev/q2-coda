#!/usr/bin/env python3
"""Host tests of q2player (player/): `python3 test/player/run.py [-k NAME]`.

Needs ffmpeg, lame, openssl (the HTTPS tests' certificates), strace and a C compiler, no stock ZIP and no Q2; the DST
SACD image needs FFmpeg's DST sample, fetched once (music.py DST_SAMPLE), and is left out without it. The player is built for this machine by tools/player.py
(its FFmpeg on first use, some minutes) and driven through its socket by fakedemo.py, writing PCM to
files in place of ALSA.

- exact: what reaches the output is, bit for bit, the reference decode (the system's ffmpeg with its
  assembly off): two FLACs and two MP3s through a preloaded change come out as one stream with no
  sample added or lost at the cut, and a 24-bit 96 kHz FLAC as S32.
- formats: each output is opened in the width stock opens for the same file.
- status push: a datagram to demo's push socket for each change of what the status queries answer, none while
  paused (docs/internals.md#player-status-push).
- protocol: for each transcript in fixtures/ (stock's and Q2 Muse's hciplayer under qemu, captured by
  fakedemo.py), the same commands get the same events and answers from q2player.

The tests and the fixtures run side by side, WIDTH at once, each in a process and a folder of its own (AFTER's
reader in its writer's), as each mostly waits on a player running in real time or faster; one that fails there runs
again alone, and passing then is reported flaky and counted as passed. Then SERIAL's, one at a time: the tests
that bound real-time behaviour or read the host's /tmp, each tried again (TRIES in all) when it failed on a run the
machine starved (Stalls). Each line gives the test's seconds, and the run ends with the side-by-side part's and the
whole run's, the last line also counting what failed, and what failed side by side and passed alone, by name.
"""
import array, concurrent.futures, functools, math, multiprocessing, os, pathlib, re, signal, struct, subprocess, sys, tempfile, threading, time

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(HERE))
import fakedemo, music  # noqa: E402

CACHE = pathlib.Path.home()/'.cache/q2-pytest/player'


def reference(path, fmt='s16le'):
    """path decoded by the system's ffmpeg in C alone, as raw PCM: no dither, no resampling. MPEG audio by its integer
    decoder, the one the player is built with."""
    decoder = ['-c:a', pathlib.Path(path).suffix[1:]] if pathlib.Path(path).suffix in ('.mp3', '.mp2') else []
    return subprocess.run(['ffmpeg', '-v', 'error', '-cpuflags', '0', *decoder, '-i', str(path), '-f', fmt, '-'], check=True, capture_output=True).stdout


def put(path, text):
    """text as path's whole content, through a temporary name, as a card app and demo write the files the player
    reads while it runs: written in place, a read in the truncate window finds it empty (a growing song's .stream
    read so ends the song at the bytes it has)."""
    tmp = pathlib.Path(f'{path}.tmp')
    tmp.write_text(text)
    os.replace(tmp, path)


def play(player, songs, scenario, out, speed=1, env=None, exits=0, ends=0):
    """exits: the run's end waits for that many {player-status\\exit} (fakedemo Session.wait_exits), for a test that
    compares the whole of what played; ends, for that many events that end a song of any kind (wait_ends)."""
    s = fakedemo.Session(out)
    s.music = songs
    s.wait_exits, s.wait_ends = exits, ends
    s.run(fakedemo.scenarios(str(songs))[scenario], fakedemo.launch_host(player, str(songs), speed, env), speed)
    return s, [p.read_bytes() for p in sorted((pathlib.Path(out)/'pcm').glob('*.raw'))], sorted(p.name for p in (pathlib.Path(out)/'pcm').glob('*.raw'))


def first_difference(a, b):
    n = min(len(a), len(b))
    if a[:n] == b[:n]: return None if len(a) == len(b) else n
    lo, hi = 0, n
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if a[:mid] == b[:mid]: lo = mid
        else: hi = mid
    return lo


def check_exact(player, songs, tmp, scenario, files, fmt, name):
    """The scenario plays `files` one after another to the end; the output is their reference decodes joined, in one PCM."""
    s, pcm, names = play(player, songs, scenario, tmp/scenario, speed=8, exits=len(files))
    want = b''.join(reference(songs/f, fmt) for f in files)
    got = b''.join(pcm)
    frame = (4 if fmt == 's16le' else 8)
    at = first_difference(got, want)
    assert at is None, f'{scenario}: {len(got) // frame} frames, reference {len(want) // frame}; first difference at frame {(at or 0) // frame}'
    assert names == [name], f'{scenario}: PCM opens {names}, expected one, {name}'
    exits = [l for l in s.lines if 'exit}' in l or 'error}' in l]
    assert [l.split()[-1] for l in exits] == ['{player-status\\exit}'] * len(files), exits
    return f'{len(got) // frame} frames equal to the reference, one PCM open'


def test_exact_flac_gapless(player, songs, tmp):
    return check_exact(player, songs, tmp, 'gapless-flac-full', ['a1.flac', 'a2.flac'], 's16le', '0000_44100_2_s16.raw')


def test_exact_flac_is_the_source(player, songs, tmp):
    """The two FLACs are one WAV cut in two: joined they are that WAV's samples."""
    whole = reference(songs/'whole.wav')
    parts = reference(songs/'a1.flac') + reference(songs/'a2.flac')
    assert whole == parts and len(whole) == 40 * 44100 * 4
    return '1764000 frames'


def test_exact_mp3_gapless(player, songs, tmp):
    r = check_exact(player, songs, tmp, 'gapless-mp3-full', ['b1.mp3', 'b2.mp3'], 's16le', '0000_44100_2_s16.raw')
    # each half is exactly its 20 s: LAME's delay and padding are gone
    assert len(reference(songs/'b1.mp3')) == len(reference(songs/'b2.mp3')) == 20 * 44100 * 4
    return r


def test_exact_mp3_no_step_at_the_cut(player, songs, tmp):
    """The signal is smooth, so samples dropped or repeated at the cut show as a step in the second difference there.
    The two halves were encoded apart, so the cut is a little rougher than its surroundings; five frames dropped
    there, tried here on a copy, is several times rougher still."""
    pcm = (tmp/'gapless-mp3-full/pcm/0000_44100_2_s16.raw').read_bytes()
    cut = 20 * 44100
    def roughness(data, i):
        x = array.array('h'); x.frombytes(data[(i - 200) * 4:(i + 200) * 4])
        left = x[0::2]
        return max(abs(left[j + 1] - 2 * left[j] + left[j - 1]) for j in range(1, len(left) - 1))
    away = max(roughness(pcm, cut - 44100), roughness(pcm, cut + 44100))
    here = roughness(pcm, cut)
    dropped = roughness(pcm[:cut * 4] + pcm[(cut + 5) * 4:], cut)
    assert dropped > 6 * away, f'the check would not see 5 dropped frames: {dropped} against {away}'
    assert here <= 3 * away, (here, away)
    return f'second difference {here} at the cut, {away} a second away, {dropped} with 5 frames dropped'


def test_exact_flac_24_96(player, songs, tmp):
    return check_exact(player, songs, tmp, 'hires', ['c96.flac'], 's32le', '0000_96000_2_s32.raw')


FMT = {'s16': ('s16le', 4), 's32': ('s32le', 8), 'f32': ('f32le', 8)}  # a PCM file's suffix: ffmpeg's -f, bytes a frame


def check_chain(player, songs, tmp, scenario, files, cut=None):
    """files played one after another: each PCM open, in order, is the reference decodes of the files it carries, in
    its own width; cut[f] frames is how many of a file's reference play (the source's length, where ffmpeg leaves
    an encoder's padding in)."""
    s, pcm, names = play(player, songs, scenario, tmp/scenario, speed=8, exits=len(files))
    opens = [(int(n.split('_')[1]), n.rsplit('_', 1)[1][:3]) for n in names]
    got = b''.join(pcm)
    want, at, frames = b'', 0, 0
    for f, fmt in files:
        ref = reference(songs/f, FMT[fmt][0])
        if cut and f in cut: ref = ref[:cut[f] * FMT[fmt][1]]
        want += ref
        frames += len(ref) // FMT[fmt][1]
    # the opens, one for each run of files at the same rate and width
    keys = [(48000 if f.endswith('.opus') else 44100, fmt) for f, fmt in files]
    runs = [k for i, k in enumerate(keys) if i == 0 or k != keys[i - 1]]
    assert opens == runs, f'{scenario}: PCM opens {names}, expected {runs}'
    d = first_difference(got, want)
    assert d is None, f'{scenario}: {len(got)} bytes, reference {len(want)}; first difference at byte {d}'
    exits = [l.split()[-1] for l in s.lines if 'exit}' in l or 'error}' in l]
    assert exits == ['{player-status\\exit}'] * len(files), exits
    return f'{len(files)} files, {frames} frames equal to the references, {len(names)} PCM opens'


LOSSLESS = [('w16.wav', 's16'), ('w24.wav', 's32'), ('w32.wav', 's32'), ('wf.wav', 'f32'), ('i16.aiff', 's16'), ('i24.aiff', 's32'),
            ('i32.aiff', 's32'), ('l16.m4a', 's16'), ('l24.m4a', 's32'), ('k16.wv', 's16'), ('k24.wv', 's32'), ('k32.wv', 's32'), ('f24.flac', 's32')]
LOSSY = [('m.mp2', 's16'), ('x.aac', 'f32'), ('o.opus', 'f32'), ('x2.wma', 'f32'), ('x1.wma', 'f32'), ('z.ac3', 'f32'), ('z.dts', 'f32')]


def test_exact_lossless(player, songs, tmp):
    """WAV, AIFF, ALAC and WavPack at 16, 24 and 32 bits and float WAV, and 24-bit FLAC: bit for bit."""
    return check_chain(player, songs, tmp, 'chain-lossless', LOSSLESS)


def test_exact_lossy(player, songs, tmp):
    """MP2, ADTS AAC, Opus, WMA 1 and 2, AC-3 and DTS: the decoders' own output, unconverted."""
    return check_chain(player, songs, tmp, 'chain-lossy', LOSSY)


def check_pair(player, songs, tmp, name, ext, fmt, frames):
    """A lossy pair through a preloaded change: each file its source's `frames` exactly, its reference's samples, and
    the two in one PCM open."""
    return check_chain(player, songs, tmp, f'pair-{name}', [(f'{name}1.{ext}', fmt), (f'{name}2.{ext}', fmt)],
                       {f'{name}1.{ext}': frames, f'{name}2.{ext}': frames})


def test_gapless_m4a(player, songs, tmp):
    """FFmpeg's AAC in M4A: the edit list's priming and the last packet's short duration in stts."""
    return check_pair(player, songs, tmp, 'm4a', 'm4a', 'f32', 882000)


def test_gapless_m4a_itunes(player, songs, tmp):
    """iTunes' shape: whole frames and the iTunSMPB tag."""
    return check_pair(player, songs, tmp, 'itunes', 'm4a', 'f32', 882000)


def test_gapless_mp3_ffmpeg(player, songs, tmp):
    """MP3 from FFmpeg's LAME, its Info tag's delay and padding."""
    return check_pair(player, songs, tmp, 'lamef', 'mp3', 's16', 882000)


def test_gapless_vorbis(player, songs, tmp):
    return check_pair(player, songs, tmp, 'ogg', 'ogg', 'f32', 882000)


def test_gapless_opus(player, songs, tmp):
    """Opus's pre-skip, at its own 48 kHz."""
    return check_pair(player, songs, tmp, 'opus', 'opus', 'f32', 960000)


def test_pair_references_are_whole(player, songs, tmp):
    """The cut leaves ffmpeg's reference at least the source's length, so cutting it to that is only the padding: what
    a player must remove. Vorbis, Opus and MP3 references are exactly 20 s already."""
    out = []
    for name, ext, fmt, frames in ('m4a', 'm4a', 'f32', 882000), ('itunes', 'm4a', 'f32', 882000), ('lamef', 'mp3', 's16', 882000), \
                                  ('ogg', 'ogg', 'f32', 882000), ('opus', 'opus', 'f32', 960000):
        for n in '12':
            got = len(reference(songs/f'{name}{n}.{ext}', FMT[fmt][0])) // FMT[fmt][1]
            assert got >= frames, f'{name}{n}.{ext}: {got} frames, source {frames}'
            out.append(got - frames)
    return 'padding in the references: ' + ' '.join(map(str, out))


def test_mp3_tail_not_played(player, songs, tmp):
    """An MP3 with 3 MB of junk after its frames (FFmpeg then distrusts the frame count): its own frames, nothing more."""
    s, pcm, names = play(player, songs, 'tail-full', tmp/'tail-full', speed=8)
    want = reference(songs/'b1.mp3')
    d = first_difference(b''.join(pcm), want)
    assert d is None, f'{len(b"".join(pcm)) // 4} frames, reference {len(want) // 4}; first difference at frame {d // 4}'
    length = [a for a in answers(parse((tmp/'tail-full/transcript.txt').read_text())[0])][0][2]
    assert length == '20', f'LENGTH {length}'
    return f'{len(want) // 4} frames, LENGTH 20'


def test_start_offset(player, songs, tmp):
    """ss=7: from frame 7 x 44100 to the end, exactly."""
    s, pcm, names = play(player, songs, 'start', tmp/'start', speed=8)
    want = reference(songs/'a1.flac')[7 * 44100 * 4:]
    d = first_difference(b''.join(pcm), want)
    assert d is None and len(names) == 1, f'{names}: {len(b"".join(pcm)) // 4} frames, reference {len(want) // 4}, first difference {d}'
    # where the song starts, in the unified log (a restart after an output change asks for one)
    logged = re.findall(r' I player (q2player: .*)', (tmp/'start/player.log').read_text())
    assert logged == [f'q2player: set-songurl {songs}/a1.flac from 7 s'], logged
    return f'{len(want) // 4} frames from frame {7 * 44100}'


def test_a_pause_with_no_song_open_is_logged_as_ignored(player, songs, tmp):
    """The drive, 2026-11-19: a restart after an output change (player_save_start) asks for a pause right after its
    set-songurl, which this player ignores while no song is open. The log says which pauses were ignored."""
    s, pcm, names = play(player, songs, 'pause-idle', tmp/'pause-idle')
    logged = re.findall(r' I player (q2player: .*)', (tmp/'pause-idle/player.log').read_text())
    assert logged == ['q2player: mcl-pause ignored, no song open', f'q2player: set-songurl {songs}/a1.flac',
                      'q2player: mcl-pause'], logged
    return ', '.join(logged)


def test_a_pause_right_after_a_song_holds_for_it(player, songs, tmp):
    """Memory Playback's paused boot sends set-songurl and then mcl-pause at once (the device, 2026-11-20: the pause came
    before the player had the song open, was dropped, and the song played on under a paused Now Playing for minutes).
    The pause holds for the song asked for: nothing is written until mcl-play, then the song from its start (ss=5)."""
    s, pcm, names = play(player, songs, 'start-paused', tmp/'start-paused')
    logged = re.findall(r' I player (q2player: .*)', (tmp/'start-paused/player.log').read_text())
    assert logged == [f'q2player: set-songurl {songs}/a1.flac from 5 s', 'q2player: mcl-pause', 'q2player: mcl-play'], logged
    got, want = b''.join(pcm), reference(songs/'a1.flac')[5 * 44100 * 4:]
    # from mcl-play to the run's end as this side ran them (2 s idle) and the PCM's half second ahead, where a lost
    # pause gives 2 s more: a fixed 2.6 s missed a session that ended late side by side (115712 frames)
    played = next(float(l.split()[0]) for l in s.lines if l.endswith('{mcl-play\\null}'))
    frames, most = len(got) // 4, int((s.end_at - played + 0.6) * 44100)
    assert want.startswith(got) and 0 < frames < most, f'{frames} frames written, at most {most}'
    return f'{frames} frames, all after the play'


def test_seek_exact(player, songs, tmp):
    """A seek to 9 s: what follows it is the reference from frame 9 x 44100, to the end; what came before is the
    reference's start."""
    # to its end: the run waits for its exit (a fixed end cut the tail short side by side, 340380 frames)
    s, pcm, names = play(player, songs, 'seek', tmp/'seek', speed=8, exits=1)
    got, ref = b''.join(pcm), reference(songs/'a1.flac')
    tail = ref[9 * 44100 * 4:]
    assert got.endswith(tail), f'the PCM does not end with the reference from 9 s ({len(got) // 4} frames)'
    head = got[:len(got) - len(tail)]
    assert ref.startswith(head) and len(head) > 0, f'{len(head) // 4} frames before the seek are not the reference start'
    return f'{len(head) // 4} frames, then {len(tail) // 4} from frame {9 * 44100}'


def heard_seconds(pcm, rate=44100):
    """pos.flac's seconds heard in s16 stereo PCM, one a 20 ms window, each with its tone's error in Hz: the frequency
    from the window's first and last rising zero crossings, interpolated. None where silent, and where the gaps between
    crossings differ by more than a tenth of their median: a splice (a seek, a scan's turn) meets two tones' phases
    there, and the short gap at the join reads as a higher tone, a second or two on (test_heard_seconds_at_a_splice).
    A splice's neighbours still say where each side is, so a second skipped there still shows."""
    left = array.array('h', pcm)[::2]
    w, out = rate // 50, []
    for at in range(0, len(left) - w, w):
        x = left[at:at + w]
        ups = [i + x[i] / (x[i] - x[i + 1]) for i in range(w - 1) if x[i] < 0 <= x[i + 1]]
        if max(x) < 4000 or len(ups) < 3: out.append((None, 0)); continue
        gaps = sorted(b - a for a, b in zip(ups, ups[1:]))
        if gaps[-1] - gaps[0] > 0.1 * gaps[len(gaps) // 2]: out.append((None, 0)); continue
        f = (len(ups) - 1) * rate / (ups[-1] - ups[0])
        n = round((f - music.POS_BASE) / music.POS_STEP)
        out.append((n, f - (music.POS_BASE + music.POS_STEP * n)))
    return out


def test_heard_seconds_at_a_splice(player, songs, tmp):
    """heard_seconds across a splice of pos.flac's second 22 into its second 23 (a turn of the scan, a seek), at 20
    places in a window and 16 phases each: no window reads a second that is neither. A window across a phase jump has
    one short gap between zero crossings, which made the tone read high: up to 24 here, the "jumps of [2] s" test_scan
    failed with (integ/q2-1129, 2026-11-22: the scan went 22, 22, 24, 23, 23, its PCM 22 then 23)."""
    rate, w = 44100, 44100 // 50
    def tone(n, frames, phase):
        f = music.POS_BASE + music.POS_STEP * n
        return [int(16000 * math.sin(phase + 2 * math.pi * f * i / rate)) for i in range(frames)]
    wrong, read = [], 0
    for cut in range(20, w - 20, 37):
        for k in range(16):
            x = tone(22, w + cut, 0) + tone(23, 2 * w - cut, 2 * math.pi * k / 16)
            for n, e in heard_seconds(array.array('h', (v for s in x for v in (s, s))).tobytes()):
                if n is None or abs(e) > 3: continue
                read += 1
                if n not in (22, 23): wrong.append((cut, k, n, round(e, 1)))
    assert not wrong, f'{len(wrong)} windows read a second neither side of the splice, e.g. {wrong[:3]}'
    # a second skipped is still seen: 22 into 24 reads as 22s then 24s, a jump of 2 for test_scan to find
    x = tone(22, w + 300, 0) + tone(24, 3 * w - 300, 1.0)
    secs = [n for n, e in heard_seconds(array.array('h', (v for s in x for v in (s, s))).tobytes()) if n is not None and abs(e) <= 3]
    assert [b - a for a, b in zip(secs, secs[1:]) if b != a] == [2], f'22 into 24 read as {secs}'
    return f'{read} windows read, each 22 or 23; 22 into 24 still a jump of 2'


def test_scan(player, songs, tmp):
    """mcl-scan (player/stretch.c): from 10 s, 2 s as it is, 2 s heard at 4 times, 2 s as it is, 2 s backward at twice,
    then as it is. Every window is on its second's tone (the pitch kept), the scan reaches 20 s (8 s in 2) and runs
    back about 4, and nothing is skipped: from window to window the second moves by one at most, at the releases too."""
    s, pcm, names = play(player, songs, 'scan', tmp/'scan', speed=2)  # a turn's 150 ms wait is real time: more would eat the run back
    heard = [(n, e) for n, e in heard_seconds(b''.join(pcm)) if n is not None]
    # A window where the second changes holds two tones and reads between them, by up to half a step (10 Hz): where
    # the changes fall on the windows depends on where the scan and the turns landed, which a busy machine moves (6
    # such windows idle, 9 with the player stopped 300 ms a second, 11 side by side at load 10). Off its tone within
    # a run of one second is the pitch wrong.
    off = [e for i, (n, e) in enumerate(heard) if abs(e) > 3 and 0 < i < len(heard) - 1 and heard[i - 1][0] == n == heard[i + 1][0]]
    assert len(off) <= len(heard) // 100, f'{len(off)} of {len(heard)} windows off their tone within a second, by up to {max(map(abs, off))} Hz'
    secs = [n for n, e in heard if abs(e) <= 3]  # a window across a seek's splice reads between two tones
    turn = next((i for i in range(1, len(secs)) if secs[i] < secs[i - 1]), len(secs))  # the first step back
    top = secs.index(max(secs[:turn]))
    back = min(secs[top:])
    # The scan covers song in the time the player runs, between the commands as this side sent them (a busy machine
    # sends late: the backward hold went out a second late and lasted 1.25 s, 1 of 50 runs): forward at 4 times from
    # 10 s and on at 1 until the turn backward, plus the half second the file sink keeps of what the turn drops, and a
    # second for where a window falls. A player stopped 300 ms a second reached 17 s (1 of 20), as a busy Q2 would: 15
    # s is past what playing on would reach (14 s). Back at twice is twice its hold at most, less on a busy machine,
    # where it went back 1 s in a hold of 1.25 s: it must go back, and no further than that and the turn's 2 s, the
    # hold read from the transcript's milliseconds (10 ms: 22 s back to 16 s in a hold read as 1.996 s).
    sent = {l.split()[-1]: float(l.split()[0]) for l in reversed(s.lines) if l.split()[1] == '>' and 'mcl-scan' in l}
    sent0 = [float(l.split()[0]) for l in s.lines if l.endswith('{mcl-scan\\0}')]
    fwd, hold = sent0[0] - sent['{mcl-scan\\400}'], sent0[1] - sent['{mcl-scan\\-200}']
    most = 10 + (sent['{mcl-scan\\400}'] - 1.0) + 4 * fwd + (sent['{mcl-scan\\-200}'] - sent0[0]) + 1.5
    reached = secs[top]
    assert 15 <= reached <= most and reached - 2 * hold - 2.01 <= back < reached, \
        f'reached {reached} s (at most {most:.1f}), then back to {back} s (a hold of {hold:.2f} s backward)'
    steps = [b - a for a, b in zip(secs, secs[1:])]
    jumps = [d for d in steps if abs(d) > 1]
    assert jumps in ([], [-2]), f'jumps of {jumps} s'  # -2: the dropped half second, never heard on a Q2
    assert secs[0] == 10 and secs[-1] > back, f'from {secs[0]} s, ending at {secs[-1]} s'
    return f'{len(heard)} windows: 10 s to {max(secs)} s, back to {back} s, on to {secs[-1]} s; {len(off)} off their tone'


def test_cue_tracks(player, songs, tmp):
    """A CUE sheet's tracks of one file, started by ss= and cut by set-nexturl: each from its own start, the last to
    the file's end, in one PCM open (the same format)."""
    s, pcm, names = play(player, songs, 'cue', tmp/'cue', speed=8)
    ref = reference(songs/'a1.flac')
    got = b''.join(pcm)
    assert len(names) == 1, names
    # each cut is where the PCM leaves the track before (what was written ahead of the cut is in the file too, since
    # the file sink keeps what the PCM dropped); from there it is the next track's start
    starts, rest, at = [], got, 0
    for t in 5, 10:
        k = first_difference(rest, ref[at * 44100 * 4:])
        k = k - k % 4 if k is not None else None  # a frame's first bytes can match by chance
        assert k is not None and rest[k:].startswith(ref[t * 44100 * 4:][:4096]), f'no track from {t} s after the cut'
        starts.append(k // 4)
        rest, at = rest[k:], t
    assert rest == ref[10 * 44100 * 4:], 'the last track is not the reference from 10 s to the end'
    events = [l.split()[-1] for l in s.lines if 'exit}' in l]
    assert events == ['{player-nextstatus\\exit}', '{player-nextstatus\\exit}', '{player-status\\exit}'], events
    return f'{starts[0]}, {starts[1]} and {len(rest) // 4} frames from 0, 5 and 10 s'


def test_bad_preload(player, songs, tmp):
    """A preload that is not audio: the song plays to its last frame, then exit, then the error and /tmp/notsupport."""
    s, pcm, names = play(player, songs, 'badnext', tmp/'badnext', speed=8)
    assert b''.join(pcm).endswith(reference(songs/'a1.flac')[12 * 44100 * 4:]), 'the song did not play to its end'
    events = [l.split()[-1] for l in s.lines if 'exit}' in l or 'error}' in l]
    assert events == ['{player-status\\exit}', '{player-status\\error}'], events
    assert (tmp/'badnext/tmp/notsupport').exists(), '/tmp/notsupport not made'  # under the run's own root (PEQ_ROOT)
    # the song that would not open, with its path, an E line the Bluetooth event log copies (engine.c log_failed)
    failed = re.findall(r' E player (q2player: open failed.*)', (tmp/'badnext/player.log').read_text())
    assert failed == [f'q2player: open failed (unsupported): {songs}/junk.flac'], failed
    return 'played to the end, then exit, error and /tmp/notsupport, the path logged'


def test_a_failure_already_replaced_is_not_reported(player, songs, tmp):
    """2026-11-21 20:32: a Bluetooth headset lost mid-song, bt_reopen waited 5 s for its PCM, and meanwhile demo reloaded
    the song paused (set-songurl, mcl-pause). The write's failure was then reported as the song's error, and demo's
    error dialog advanced the queue and started the next song playing, the DAC powered on under a pause for hours.
    Now a song whose output fails once a newer song or a stop is waiting is logged, not reported: no error, no
    /tmp/notsupport, and the waiting song opens paused. A song that fails with nothing waiting reports as before."""
    s, pcm, names = play(player, songs, 'replaced-failure', tmp/'replaced')
    events = [l.split()[-1] for l in s.lines if 'exit}' in l or 'error}' in l]
    assert events == [], events
    # the host player's /tmp is its run's own (PEQ_ROOT "."), so another player on the machine cannot make it
    assert not (tmp/'replaced/tmp/notsupport').exists(), '/tmp/notsupport made for a song already replaced'
    log = (tmp/'replaced/player.log').read_text()
    assert f'q2player: play failed, already replaced (the output would not open or take audio): {songs}/a1.flac' in log
    assert b''.join(pcm) == b'', names  # a2 taken paused: nothing written
    s, pcm, names = play(player, songs, 'play-failure', tmp/'failure')
    events = [l.split()[-1] for l in s.lines if 'exit}' in l or 'error}' in l]
    assert events == ['{player-status\\error}'], events
    assert (tmp/'failure/tmp/notsupport').exists(), '/tmp/notsupport not made for a song that failed while it played'
    return 'replaced: no error, the waiting song paused; alone: the error and /tmp/notsupport'


def test_status_push(player, songs, tmp):
    """docs/internals.md#player-status-push: a datagram for each change of what player-status and media-info answer,
    so demo's status poll waits on it, and none while they stay the same (paused, with nothing moving)."""
    s, _, _ = play(player, songs, 'transport', tmp/'push')
    assert s.pushes, 'no status push'
    paused = [round(t, 2) for t in s.pushes if 4.3 < t < 6.0]  # mcl-pause at 4.0, mcl-play at 6.0
    assert not paused, f'pushes while paused: {paused}'
    playing = [t for t in s.pushes if 1.5 < t < 4.0]  # the position's whole seconds, one a second
    assert 1 <= len(playing) <= 4, f'{len(playing)} pushes in 2.5 s of playing'
    # every change the 200 ms poll saw had its push after the poll before it
    last, missed = {}, []
    for t, m in s.answers:
        name = m[1:m.index('\\')]
        if name in last and last[name][1] != m and not any(last[name][0] <= p <= t for p in s.pushes):
            missed.append((round(t, 2), m))
        last[name] = (t, m)
    assert not missed, f'changes with no push: {missed}'
    return f'{len(s.pushes)} pushes in 13 s, none paused, {len(playing)} in 2.5 s playing'


def crc8(b):
    c = 0
    for x in b:
        c ^= x
        for _ in range(8): c = (c << 1 ^ 0x07 if c & 0x80 else c << 1) & 0xff
    return c


def with_seektable(flac, every=44100):
    """flac (fixed blocks, as FFmpeg writes them) with a SEEKTABLE after its STREAMINFO, a point about every `every`
    samples, as the flac tool writes one and as server songs have (FFmpeg writes none)."""
    block = int.from_bytes(flac[8:10], 'big')
    at, last = 4, False
    while not last:
        last, size = flac[at] & 0x80, int.from_bytes(flac[at + 1:at + 4], 'big')
        at += 4 + size
    audio, points, i = at, [], at
    while (i := flac.find(b'\xff\xf8', i)) >= 0:
        n, j = flac[i + 4], i + 5  # the frame number, UTF-8 coded
        k = 0 if n < 0x80 else 1 if n < 0xe0 else 2 if n < 0xf0 else 3
        num = n & (0x7f >> k if k else 0x7f)
        for c in flac[j:j + k]: num = num << 6 | c & 0x3f
        j += k
        j += {6: 1, 7: 2}.get(flac[i + 2] >> 4, 0) + {12: 1, 13: 2, 14: 2}.get(flac[i + 2] & 15, 0)
        if crc8(flac[i:j]) == flac[j] and (not points or num * block >= points[-1][0] + every):
            points.append((num * block, i - audio))
        i += 2
    table = b''.join(struct.pack('>QQH', smp, off, block) for smp, off in points)
    si = bytes([flac[4] & 0x7f]) + flac[5:42]  # STREAMINFO, no longer the last block
    return flac[:4] + si + bytes([3]) + len(table).to_bytes(3, 'big') + table + flac[42:]


def grow(player, songs, out, head, land_at, extra=(), seektable=False, part=None, whole=None, speed=4, env=None, tail=24.0):
    """a1 arriving in place as a card app writes it (player/grow.c): head bytes of it on the card and its .stream saying
    get, the rest landing at land_at (the line then done); extra are more steps (a seek); seektable, a1 with one; part, (time, bytes): more of it landing earlier;
    whole, other bytes in a1's place; speed, env and tail (the run's length after land_at) as the session's. Returns the
    session, the PCM, and the beats' reads of /tmp/q2player.wait (False when there is none)."""
    card, status = out/'mnt/mmc/.foo', out/'tmp/q2-foo.stream'
    card.mkdir(parents=True, exist_ok=True)
    whole, path = whole or (songs/'a1.flac').read_bytes(), card/'grow.flac'
    if seektable: whole = with_seektable(whole)
    path.write_bytes(whole[:head])
    seen, have = [], [head]

    def line(state, n):
        put(status, f'#Q2STREAM/1\tseq=1\n{state}\t{n}\t{len(whole)}\t20000\t/mnt/mmc/.foo/grow.flac\n')

    def beat(s):  # the app rewrites its line twice a second while the song arrives
        if have[0] < len(whole): line('get', have[0])
        seen.append((out/'tmp/q2player.wait').exists() and (out/'tmp/q2player.wait').read_text())

    def land(s, n=None):
        n = n or len(whole)
        with open(path, 'ab') as f: f.write(whole[path.stat().st_size:n])  # from what is there: a test may land more itself
        have[0] = n
        line('done' if n == len(whole) else 'get', n)

    boot = [(0.0, '{mcl-dsdmode\\0}'), (0.0, '{mcl-replaygain\\0}'), (0.1, '{mcl-setoutputway\\0}'), (0.1, '{mcl-setvol\\60}')]
    steps = sorted(boot + [(1.0, fakedemo.song('set-songurl', str(path)))] + [(t / 2, beat) for t in range(4, 2 * int(land_at) + 2)]
                   + [(land_at, land), *extra] + ([(part[0], lambda s: land(s, part[1]))] if part else []), key=lambda st: st[0]) + [(land_at + tail, None)]
    s = fakedemo.Session(out)  # it empties out/tmp: the line after it
    s.music = songs
    s.wait_exits = 1  # each plays the song to its end: the run waits for it (a starved player's was after land_at + tail)
    line('get', head)
    s.run(steps, fakedemo.launch_host(player, str(songs), speed, env), speed)
    pcm = b''.join(p.read_bytes() for p in sorted((out/'pcm').glob('*.raw')))
    assert not (out/'tmp/q2player.wait').exists(), 'q2player.wait left after the song'
    exits = [l.split()[-1] for l in s.lines if 'exit}' in l or 'error}' in l]
    assert exits == ['{player-status\\exit}'], f'the song ended other than once, at its end: {exits}'
    return s, pcm, seen


def test_grow_plays_a_song_as_it_arrives(player, songs, tmp):
    """A card app's song still arriving in place (player/grow.c): a fifth of a1 is there and its .stream says get;
    the player plays what is there, waits at its end (naming the song in /tmp/q2player.wait) rather than ending,
    and once the rest lands and the line says done plays it to its end: the whole song, frame for frame."""
    whole = (songs/'a1.flac').read_bytes()
    s, got, seen = grow(player, songs, tmp/'grow', len(whole) // 5, 16.0)
    want = reference(songs/'a1.flac')
    at = first_difference(got, want)
    assert at is None, f'{len(got) // 4} frames, reference {len(want) // 4}; first difference at frame {(at or 0) // 4}'
    assert '/mnt/mmc/.foo/grow.flac\n' in seen, f'the wait was never named: {seen}'
    waited = seen.count('/mnt/mmc/.foo/grow.flac\n')
    return f'{len(got) // 4} frames equal to the reference; the wait named in {waited} of {len(seen)} beats'


def with_cover(flac, size):
    """flac with a front cover of size bytes after its STREAMINFO, as a server's FLACs have one: a JPEG by its MIME
    type and its header, its width and height 0 (unknown) in the block, as taggers often leave them. q2player's FFmpeg
    has no JPEG decoder, so the picture's size stays unknown to avformat_find_stream_info. Returns the FLAC and where
    its audio starts."""
    jpeg = b'\xff\xd8\xff\xe0' + bytes(size - 6) + b'\xff\xd9'
    mime = b'image/jpeg'
    body = struct.pack('>II', 3, len(mime)) + mime + struct.pack('>IIIIII', 0, 0, 0, 0, 0, len(jpeg)) + jpeg
    si = bytes([flac[4] & 0x7f]) + flac[5:42]  # STREAMINFO, no longer the last block
    out = flac[:4] + si + bytes([0x80 | 6]) + len(body).to_bytes(3, 'big') + body + flac[42:]
    return out, 42 + 4 + len(body)


def test_grow_opens_a_song_with_a_cover_on_its_first_audio(player, songs, tmp):
    """A song arriving in place with an embedded cover starts once its headers and a little of its audio are there,
    whatever the cover's size: the open reads the audio's headers and a frame or two, not 5 s of audio to size a
    picture its FFmpeg cannot decode (source.c GROW_ANALYZE_US). 3 s of a1's audio is there (the open's own look
    ahead reads about 1.3 s), the rest lands at 8 s: the song is playing well before then, and plays whole."""
    want, said = reference(songs/'a1.flac'), []
    for size in (2048, 160 * 1024):  # a thumbnail, and the largest seen on a Q2's stream folder (2026-11-22)
        whole, audio = with_cover((songs/'a1.flac').read_bytes(), size)
        head = audio + (len(whole) - audio) * 3 // 20  # a1 is 20 s: 3 s of it
        s, got, seen = grow(player, songs, tmp/f'grow-cover{size}', head, 8.0, whole=whole)
        at = first_difference(got, want)
        assert at is None, f'cover of {size} B: {len(got) // 4} frames, reference {len(want) // 4}; first difference at frame {(at or 0) // 4}'
        known = [t for t, p in s.positions]
        assert known and known[0] < 3.0, f'cover of {size} B: the song was opened only at {known[:1]} s (set at 1 s, the rest landing at 8 s)'
        said.append(f'{size} B cover: opened by {known[0]:.1f} s')
    return '; '.join(said) + ', the rest landing at 8 s'


def test_grow_slow_download_plays_in_few_long_stretches(player, songs, tmp):
    """A song arriving at three quarters of its own rate (player/grow.c refill), played into the car's PCM in real time,
    whose trace counts the PCM running dry (docs/iphone-mode.md#car-trace): after each stall the read waits until
    REFILL_S of the song is there ahead (then twice that, to REFILL_MAX_S) before it plays on, or the whole song is
    there, so the song breaks off a handful of times, not every time a packet comes; and plays whole, frame for frame."""
    whole = (songs/'a1.flac').read_bytes()
    per_s, out, car = len(whole) / 20, tmp/'grow-slow', tmp/'grow-slow.audio'
    car.write_text('hw:CARD=UAC1Gadget,0\n44100\n')
    head = int(per_s * 3)  # the start gate's 3 s
    path = out/'mnt/mmc/.foo/grow.flac'

    def land_to(n):  # the download's next bytes, up to n
        with open(path, 'ab') as f: f.write(whole[path.stat().st_size:n])
    steps = int((len(whole) - head) / (per_s * 0.75) * 10)  # tenths of a second to the last byte
    landing = [(1.0 + k / 10, (lambda n: lambda s: land_to(n))(head + int(per_s * 0.75 * k / 10))) for k in range(1, steps)]
    end = 1.0 + steps / 10
    s, got, seen = grow(car_player(), songs, out, head, end, extra=landing, speed=1, tail=4.0,
                        env={'Q2PLAYER_CAR': str(car), 'Q2PLAYER_FORMATS': 's16', 'Q2PLAYER_CAR_QUIET_MS': '1000'})
    want = reference(songs/'a1.flac')
    at = first_difference(got, want)
    assert at is None, f'{len(got) // 4} frames, reference {len(want) // 4}; first difference at frame {(at or 0) // 4}'
    lines = car_trace((out/'player.log').read_text())
    dry, silent = sum(l['dry'] for l in lines), sum(l['dry_ms'] for l in lines)
    assert 1 <= dry <= 6, f'the car\'s PCM ran dry {dry} times, {silent} ms of silence in all'
    return f'{dry} breaks ({silent} ms of silence in all) over a song arriving at three quarters of its rate, played whole'


def test_grow_seek_inside(player, songs, tmp):
    """A seek inside the part of a growing FLAC on the card, with no SEEKTABLE (FFmpeg then searches the file for the
    target) and with one (server songs have it): the song plays from the target at once, without waiting for the rest,
    and on to its end once the rest lands."""
    ref, said = reference(songs/'a1.flac'), []
    for table in (False, True):
        whole = (songs/'a1.flac').read_bytes()
        seek = [(2.0, '{mcl-seek\\9}')]  # nine tenths of a1 are there (past 16 s); the rest lands 6 s after the seek
        s, got, seen = grow(player, songs, tmp/f'grow-inside{int(table)}', len(whole) * 9 // 10, 8.0, seek, table)
        tail, kind = ref[9 * 44100 * 4:], 'with a SEEKTABLE' if table else 'without a SEEKTABLE'
        assert got.endswith(tail), f'{kind}: the PCM does not end with the reference from 9 s ({len(got) // 4} frames)'
        head = got[:len(got) - len(tail)]
        assert ref.startswith(head), f'{kind}: {len(head) // 4} frames before the seek are not the reference start'
        # what plays from 9 s before the rest lands (6 s after the seek: 4 s of it at the least)
        # from the seek to the landing as this side ran it (late on a busy machine): past the target, played without
        # the rest (12 s at the least idle; 10 s with the player stopped 300 ms a second, which a fixed 8 s missed)
        landed = max(u for at, u in s.ran if at == 8.0)
        after = [p for t, p in s.positions if 2.0 < t < landed]
        assert after and max(after) > 9, f'{kind}: the seek waited for the rest of the song: positions {after}'
        assert '/mnt/mmc/.foo/grow.flac\n' not in seen, f'{kind}: the player waited although the target was on the card'
        said.append(f'{kind} at {max(after)} s before the rest landed')
    return 'from 9 s at once, ' + '; '.join(said)


def test_grow_seek_beyond(player, songs, tmp):
    """A seek past the part of a growing FLAC on the card, with no SEEKTABLE and with one: the player waits there
    (naming the song for the spinner, its position the target's) rather than ending the song, and plays from the
    target as soon as its bytes land, not once the whole song has: the reference from 15 s to the end."""
    ref, said = reference(songs/'a1.flac'), []
    for table in (False, True):
        whole = (songs/'a1.flac').read_bytes()
        # a fifth of a1 is there (4 s); nine tenths of it 3 s after the seek (past 16 s), the rest 10 s after it
        seek = [(2.0, '{mcl-seek\\15}')]
        s, got, seen = grow(player, songs, tmp/f'grow-beyond{int(table)}', len(whole) // 5, 12.0, seek, table, (5.0, len(whole) * 9 // 10))
        tail, kind = ref[15 * 44100 * 4:], 'with a SEEKTABLE' if table else 'without a SEEKTABLE'
        assert got.endswith(tail), f'{kind}: the PCM does not end with the reference from 15 s ({len(got) // 4} frames)'
        head = got[:len(got) - len(tail)]
        assert ref.startswith(head), f'{kind}: {len(head) // 4} frames before the seek are not the reference start'
        assert '/mnt/mmc/.foo/grow.flac\n' in seen, f'{kind}: the wait was never named: {seen}'
        # the target's bytes not there: the bar shows it, from the seek as sent (and the 600 ms it takes) to the landing
        # as it ran, each late when this side was held up (empty in a fixed 2.6 s to 4.9 s, 1 of 20 runs starved)
        sought = next(float(l.split()[0]) for l in s.lines if l.endswith('{mcl-seek\\15}'))
        landed = next(u for at, u in s.ran if at == 5.0)
        waiting = [p for t, p in s.positions if sought + 0.6 < t < landed - 0.1]
        # a starved player may take the seek later than 600 ms: what it read before (1 s, 2 of 20 starved) is not a
        # wait; from its first 15 on, every answer is 15
        took = waiting.index(15) if 15 in waiting else len(waiting)
        assert waiting[took:] and set(waiting[took:]) == {15} and all(p <= 2 for p in waiting[:took]), \
            f'{kind}: the position while the player waits is not the target: {waiting}'
        rest = max(u for at, u in s.ran if at == 12.0)  # the rest landing, as this side ran it
        after = [p for t, p in s.positions if 2.0 < t < rest]  # a fixed 12 s missed a starved player's 16 s (1 of 20)
        assert after and max(after) >= 16, f'{kind}: the target was not played when its bytes came: positions {after}'
        said.append(f'{kind} {len(head) // 4} frames before, at {max(after)} s before the rest landed')
    return 'a wait, then the reference from 15 s to the end; ' + '; '.join(said)


def check_not_played(player, songs, tmp, scenario):
    s, pcm, names = play(player, songs, scenario, tmp/scenario, speed=4)
    a1, a2, b1 = (reference(songs/n) for n in ('a1.flac', 'a2.flac', 'b1.mp3'))
    got = b''.join(pcm)
    assert a2[:8192] not in got, f'{scenario}: the cancelled preload played'
    assert got.startswith(a1[:44100]) and a1[12 * 44100 * 4:] in got and b1[:44100 * 4] in got, f'{scenario}: not a1 to its end, then b1'
    return f'a1 from 12 s to its end, then b1; nothing of a2 in {len(got) // 4} frames'


def test_preload_cancelled(player, songs, tmp):
    """mcl-closegapless after a preload opened: it is not played, and demo's own set-songurl comes next."""
    return check_not_played(player, songs, tmp, 'cancel')


def test_preload_cancelled_while_opening(player, songs, tmp):
    """The same while the preload's open is blocked (a FIFO): its file arrives after the cancel and does not play."""
    return check_not_played(player, songs, tmp, 'cancel-open')


def test_preload_outrun(player, songs, tmp):
    """A preload still opening when the song ends: exit at the end, then the file plays when it has opened."""
    # The file comes 2 s after a1's exit (after_exits), and the run waits for a2's: at fixed times (the file at 12 s,
    # the end at 36 s) a starved player reached a1's end after the file had come, or a2's after the run had ended
    # (0 of 10 with it stopped 300 ms a second, and once alone at load 12 on integ/q2-1129)
    s, pcm, names = play(player, songs, 'outrun', tmp/'outrun', speed=4, exits=2)
    a1, a2 = reference(songs/'a1.flac'), reference(songs/'a2.flac')
    got = b''.join(pcm)
    assert got.endswith(a1[12 * 44100 * 4:] + a2), 'not a1 to its end, then the whole of a2'
    events = [(float(l.split()[0]), l.split()[-1]) for l in s.lines if 'exit}' in l]
    fed = max(u for at, u in s.ran if at == 4.0)  # the feed, the last of the steps at 4.0, after the wait for the exit
    assert [e for _, e in events] == ['{player-status\\exit}'] * 2 and events[0][0] < fed, (events, fed)
    return f'exit at {events[0][0]:.1f} s, before the file arrived at {fed:.1f} s; then all {len(a2) // 4} frames of it'


def test_exits_apart(player, songs, tmp):
    """A preloaded file with no audio ends as it starts: its exit comes at least 300 ms after the one before."""
    s, pcm, names = play(player, songs, 'emptynext', tmp/'emptynext')
    t = [float(l.split()[0]) for l in s.lines if l.endswith('{player-status\\exit}')]
    # read as this side took them: to the millisecond (300 ms apart reads 0.299, 7.992 and 8.292, 3 of 20 runs), and
    # closer by as long as this side went without reading (s.worst), when the first waited in the socket
    assert len(t) == 2 and t[1] - t[0] >= 0.3 - s.worst - 0.002, (t, s.worst)
    return f'exits {t[1] - t[0]:.3f} s apart'


def test_media_info_while_a_song_opens(player, songs, tmp):
    """media-info asked while the next song opens (set-nexturl to a FIFO nothing writes yet, then a2 written to it):
    every answer between the cut ({player-nextstatus\\exit}) and the new song's figures is no-playing-status, never the
    song before's figures (docs/player.md: stock answers no-playing-status until media-info has the song). A song's
    figures under the next song's name would show on Now Playing as its format."""
    fifo = tmp/'mi-open.flac'
    os.mkfifo(fifo)
    def feed(s):
        def write():
            try:
                with open(fifo, 'wb') as f: f.write((songs/'a2.flac').read_bytes())
            except BrokenPipeError: pass  # the stop at 7 s, before the reader took it all
        threading.Thread(target=write, daemon=True).start()
    boot = [(0.0, '{mcl-dsdmode\\0}'), (0.0, '{mcl-replaygain\\0}'), (0.1, '{mcl-setoutputway\\0}'), (0.1, '{mcl-setvol\\60}')]
    steps = boot + [(1.0, fakedemo.song('set-songurl', f'{songs}/b1.mp3')), (3.0, fakedemo.song('set-nexturl', str(fifo))),
                    (3.5, '{media-info\\get-music-info}'), (4.0, '{media-info\\get-music-info}'), (5.0, feed), (7.0, '{mcl-stop\\null}'),
                    (8.0, None)]
    s = fakedemo.Session(tmp/'mi-open')
    s.music = songs
    s.run(steps, fakedemo.launch_host(player, str(songs), 1), 1)
    # the answers after the cut by their order on the socket: by time, an answer read just before it could stamp later
    # than the transcript's rounded time of it (4 of 20 runs starved)
    cut = next(n for m, n, _ in s.ends if m == '{player-nextstatus\\exit}')
    after = [(t, m) for t, m in s.answers[cut:] if m.startswith('{media-info')]
    first = next(i for i, (_, m) in enumerate(after) if '|' in m)
    opening, (came, song) = after[:first], after[first]
    fed = next(u for at, u in s.ran if at == 5.0)  # when a2 was written, as this side ran it
    assert opening and all(m == '{media-info\\no-playing-status}' for _, m in opening), opening
    assert came >= fed and '|FLAC|' in song, (fed, came, song)  # no figures until a2 came, then a2's
    return f'{len(opening)} answers of no-playing-status over {opening[-1][0] - opening[0][0]:.1f} s of the open, then {song}'


def test_formats_open_as_stock(player, songs, tmp):
    """Stock's ALSA requests for the same scenario (the emulator shim's log): S32 for 24-bit FLAC, FLOAT for AAC and Vorbis.
    The Vorbis file follows the AAC one in the same PCM, which stock reopens (docs/player-plan.md#deviations)."""
    s, pcm, names = play(player, songs, 'formats', tmp/'formats')
    assert names == ['0000_96000_2_s32.raw', '0001_44100_2_f32.raw'], names
    return ' '.join(names)


# ---- the DSP (phase 3)

def tool(tmp, name, *defines):
    """test/player/NAME.c with patch/peq.c (and player/dsp.c for dsp_test) as the player builds them, for this
    machine."""
    out = tmp/name
    if not out.exists() and name in ('volume_test', 'alsa_test'):  # output.c and the car's trace it counts into, with FFmpeg's headers
        ff = pathlib.Path(subprocess.run([sys.executable, ROOT/'tools/player.py', 'ffmpeg', 'host'], check=True, capture_output=True, text=True).stdout.strip())
        subprocess.run(['cc', '-std=c11', '-D_GNU_SOURCE', '-O2', '-Wall', '-Werror', *defines, '-I', ROOT/'player', '-I', ff/'include', HERE/f'{name}.c',
                        ROOT/'player/output.c', ROOT/'player/cartrace.c', '-L', ff/'lib', '-lswresample', '-lavutil', '-lm', '-lpthread', '-o', out], check=True)
    if not out.exists():
        subprocess.run(['cc', '-std=c11', '-D_GNU_SOURCE', '-DPEQ_HOST', *defines, '-O2', '-Wall', '-Wextra', '-Werror', '-Wno-unused-parameter',
                        '-I', ROOT/'player', '-I', ROOT/'patch', HERE/f'{name}.c', *([ROOT/'player/dsp.c'] if name == 'dsp_test' else []), ROOT/'patch/peq.c',
                        '-Wl,--wrap=clock_gettime' if name == 'dsp_test' else '-lm', '-lm', '-o', out], check=True)
    return out


def test_dsp_chain(player, songs, tmp):
    """test/player/dsp_test.c: test/peq.py's checks of hciplayer's filter, made of the player's chain."""
    root = tmp/'dsp'
    root.mkdir()
    exe = tool(tmp, 'dsp_test', f'-DPEQ_ROOT="{root}"')
    return subprocess.run([exe], check=True, capture_output=True, text=True).stdout.strip()


def eq_reference(tmp, songs, name, state, out, at=None, state2=None):
    """songs/name's reference decode (S16 stereo, 44.1 kHz) through the EQ state, as peq_ref filter runs it."""
    src = tmp/f'{name}.s16'
    if not src.exists(): src.write_bytes(reference(songs/name))
    subprocess.run([tool(tmp, 'peq_ref', '-DPEQ_ROOT=""'), 'filter', state, '44100', src, out, *([str(at), state2] if at is not None else [])], check=True)
    return out.read_bytes()


def test_eq_plays(player, songs, tmp):
    """A PEQ in /mnt/data/peq-active before a song: what plays is the reference through patch/peq.c, every sample."""
    d = tmp/'eq'; d.mkdir()
    subprocess.run([tool(tmp, 'peq_ref', '-DPEQ_ROOT=""'), 'save', d/'eq.state', '6', '-3'], check=True)
    s, pcm, names = play(player, songs, 'eq', d, speed=8)
    want = eq_reference(tmp, songs, 'a1.flac', d/'eq.state', d/'want.s16')
    assert names == ['0000_44100_2_s16.raw'], names
    assert want != reference(songs/'a1.flac'), 'the EQ changes nothing'
    at = first_difference(pcm[0], want)
    assert at is None, f'{len(pcm[0]) // 4} frames, reference {len(want) // 4}; first difference at frame {(at or 0) // 4}'
    return f'{len(want) // 4} frames equal to the reference through the EQ'


def test_eq_swap(player, songs, tmp):
    """Another EQ state written while a song plays, then mcl-seteq (an EQ change, or EQ per Output's swap): from one
    block on, the song is the reference through the first EQ ramping into the second, as patch/peq.c's update."""
    d = tmp/'eq-swap'; d.mkdir()
    subprocess.run([tool(tmp, 'peq_ref', '-DPEQ_ROOT=""'), 'save', d/'eq.state', '6', '-3'], check=True)
    subprocess.run([tool(tmp, 'peq_ref', '-DPEQ_ROOT=""'), 'save', d/'eq2.state', '-6', '0'], check=True)
    s, pcm, names = play(player, songs, 'eq-swap', d, speed=8)
    first = eq_reference(tmp, songs, 'a1.flac', d/'eq.state', d/'first.s16')
    at = first_difference(pcm[0], first)
    assert at is not None and 6 * 44100 < at // 4 < 9 * 44100, f'the second EQ came in at frame {(at or 0) // 4}'
    block = int(subprocess.run(['ffprobe', '-v', 'error', '-select_streams', 'a', '-show_entries', 'packet=duration', '-of', 'csv=p=0',
                                songs/'a1.flac'], check=True, capture_output=True, text=True).stdout.split()[0])
    for f in range(at // 4 // block * block, at // 4 - 2 * block, -block):  # the block the reread came before
        want = eq_reference(tmp, songs, 'a1.flac', d/'eq.state', d/'want.s16', f, d/'eq2.state')
        if first_difference(pcm[0], want) is None: return f'the second EQ from frame {f}, through the ramp, equal to the reference'
    assert False, f'no block boundary before frame {at // 4} gives the output'


def test_crossfade(player, songs, tmp):
    """/tmp/q2xfade at 2000 ms, two 20 s FLACs, the second preloaded: the first plays exactly up to its last 2 s, which
    are mixed under the second's first 2 s at equal power, then the second plays exactly (demo's fade is 0 by then, its
    next being the queue's end); one PCM open."""
    s, pcm, names = play(player, songs, 'xfade', tmp/'xfade', speed=8, exits=2)
    assert names == ['0000_44100_2_s16.raw'], names
    a, b, got = (array.array('h', x) for x in (reference(songs/'a1.flac'), reference(songs/'a2.flac'), pcm[0]))
    n, h = len(a) // 2, 2 * 44100
    assert len(got) == len(a) + len(b) - 2 * h, f'{len(got) // 2} frames, expected {(len(a) + len(b)) // 2 - h}'
    assert got[:2 * (n - h)] == a[:2 * (n - h)], 'the first song before its tail'
    worst = 0
    for k in range(h):
        p = (k + 0.5) / h * math.pi / 2
        for c in (0, 1):
            want = b[2 * k + c] * math.sin(p) + a[2 * (n - h + k) + c] * math.cos(p)
            worst = max(worst, abs(got[2 * (n - h + k) + c] - want))
    assert worst <= 2, f'the mix is {worst} from equal power'
    assert got[2 * n:] == b[2 * h:], 'the second song after the mix'
    exits = [l.split()[-1] for l in s.lines if 'exit}' in l]
    assert exits == ['{player-status\\exit}'] * 2, exits
    return f'{len(got) // 2} frames: {n - h} exact, {h} mixed within {worst:.2f} of equal power, {len(b) // 2 - h} exact'


# ---- Smart Crossfade (player/smart.c, dsp.c smart_want; docs/internals.md#smart-crossfade)
SMART = 5000 | 0x8000  # peq.h: the plain fade's 5 s, XF_SMART


def synth(path, seconds, level, rate=44100, channels=2, hz=None):
    """A FLAC whose frame i is level(i / rate) times a sine of hz (a whole number of cycles a 50 ms window), every
    channel the same: the analysis's windows then measure the level exactly."""
    hz = hz or 440
    raw = array.array('h')
    for i in range(int(seconds * rate)):
        v = int(round(32767 * level(i / rate) * math.sin(2 * math.pi * hz * i / rate)))
        raw.extend([v] * channels)
    pcm = path.with_suffix('.raw')
    pcm.write_bytes(raw.tobytes())
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 's16le', '-ar', str(rate), '-ac', str(channels), '-i', pcm,
                    '-c:a', 'flac', path], check=True)
    pcm.unlink()
    return path


B_SECONDS = 55  # each B: over XF_SMART_LEAD_S long, so its own change's analysis is not asked for before demo's word
                # for it (0, the queue's end) is there


def smart_songs(d, rate_b=44100, channels_b=2, b_level=None):
    """A: a 440 Hz tone at half scale for 16 s, a 6 s linear fade to nothing (its part 12 dB under the tone 1.5 s
    long), 2 s of silence: shorter than the 30 s measured. B: 1.5 s of silence, then a 660 Hz tone at half scale from
    its first frame (a hard start), B_SECONDS in all."""
    d.mkdir(parents=True, exist_ok=True)
    a = synth(d/'sa.flac', 24, lambda t: 0.5 if t < 16 else max(0.0, 0.5 * (1 - (t - 16) / 6)) if t < 22 else 0.0)
    b = synth(d/'sb.flac', B_SECONDS, b_level or (lambda t: 0.0 if t < 1.5 else 0.5), rate=rate_b, channels=channels_b, hz=660)
    return a, b


def hard_end(d):
    """A 440 Hz tone at half scale for 40 s, to its last frame: no quiet run at all."""
    d.mkdir(parents=True, exist_ok=True)
    return synth(d/'sa_hard.flac', 40, lambda t: 0.5)


def soft_intro(d, seconds):
    """B: 1 s of silence, seconds of a 660 Hz tone at 0.04 (22 dB under what follows: a soft intro), then the tone at
    half scale, B_SECONDS in all."""
    d.mkdir(parents=True, exist_ok=True)
    return synth(d/f'sb_intro{seconds}.flac', B_SECONDS, lambda t: 0.0 if t < 1 else 0.04 if t < 1 + seconds else 0.5, hz=660)


def long_fade(d):
    """A: a 440 Hz tone at half scale for 10 s, then a 25 s fade of 2 dB a second (50 dB in all) to its end."""
    d.mkdir(parents=True, exist_ok=True)
    return synth(d/'sa_fade25.flac', 35, lambda t: 0.5 if t < 10 else 0.5 * 10 ** (-2 * (t - 10) / 20))


def windows(x, channels, w, from_end):
    """The analysis's windows of interleaved samples x (16-bit), in dB of full scale: from the end, newest first, or
    from the start."""
    n = len(x) // channels
    out = []
    for k in range(n // w):
        lo = n - (k + 1) * w if from_end else k * w
        s = sum((v / 32768) ** 2 for v in x[lo * channels:(lo + w) * channels])
        out.append(10 * math.log10(s / (w * channels)) if s > 0 else -200.0)
    return out


def smart_plan(a, b, ca=2, cb=2, ra=44100, rb=44100):
    """smart.c's rules, written again here from docs/internals.md#smart-crossfade: (overlap, trail, skip, fade) in
    frames, and what decided the overlap ('out run', 'next intro' or 'floor')."""
    wa, wb = ra // 20, rb // 20
    tail = windows(a, ca, wa, True)[:600]
    head = windows(b[:cb * 30 * rb], cb, wb, False)[:600]
    body = sorted(tail)[(len(tail) - 1) // 2]
    loud = max(-60, body - 45)
    te = next(k for k, v in enumerate(tail) if v > loud)
    run = te
    while run < len(tail) and tail[run] < body - 12: run += 1
    out = (run - te) * wa if run < len(tail) else 0  # a run that starts inside the span
    skip = 0
    while skip < len(head) and skip * 50 < 5000 and head[skip] < -60: skip += 1
    bb = sorted(head)[(len(head) - 1) // 2]
    after = head[skip + 300:]  # 15 s on from the first sound
    ref = sorted(after)[(len(after) - 1) // 2] if len(after) >= 100 else bb
    intro = skip
    while intro < len(head) and head[intro] < ref - 10: intro += 1
    intro = (intro - skip) * 50 * ra // 1000
    floor = 5 * ra
    overlap, why = (out, 'out run') if out >= intro and out >= floor else (intro, 'next intro') if intro >= floor else (floor, 'floor')
    start = max(head[skip:skip + 4])
    gap = bb - 6 - start
    fade_ms = 300 + int(700 * min(1, gap / 14)) if gap > 0 else 0
    return min(overlap, 15 * ra), te * wa, skip * wb, rb * fade_ms // 1000, why


def smart_scenario(a, b, gapless_at=12.0, end=80.0, word=SMART, next_text=None):
    """A then B, B preloaded at gapless_at, word in /tmp/q2xfade until A's exit, where demo writes B's own (0, the
    queue's end); the run ends at both songs' exits (smart_play), end only bounding it."""
    def at_exit(s): s.on_exit = [fakedemo.put('tmp/q2xfade', bytes(4), in_place=True)]
    boot = [(0.0, '{mcl-dsdmode\\0}'), (0.0, '{mcl-replaygain\\0}'), (0.1, '{mcl-setoutputway\\0}'), (0.1, '{mcl-setvol\\60}')]
    return boot + [(0.2, fakedemo.put('tmp/q2xfade', word.to_bytes(4, 'little'))),
                   (0.2, fakedemo.put('tmp/q2xfade.next', (next_text if next_text is not None else f'{a}\n{b}\n').encode())),
                   (0.3, fakedemo.SETEQ), (1.0, fakedemo.song('set-songurl', str(a), flag=1)),
                   (gapless_at, fakedemo.gapless(str(b), flag=1)), (gapless_at, at_exit), (end, None)]


def smart_play(player, songs, out, steps, speed=8, env=None, exits=2):
    """exits: as play's, the run's end waits for both songs' {player-status\\exit}."""
    s = fakedemo.Session(out)
    s.music = songs
    s.wait_exits = exits
    s.run(steps, fakedemo.launch_host(player, str(songs), speed, env), speed)
    pcm = sorted((pathlib.Path(out)/'pcm').glob('*.raw'))
    return s, [p.name for p in pcm], b''.join(p.read_bytes() for p in pcm), (pathlib.Path(out)/'player.log').read_text()


def check_smart(a, b, names, got, log):
    """got is a's and b's smart crossfade by smart_plan's rules: a exact up to out_point, then b from its first sound
    (at full level, or over its fade-in as sin) mixed with a's end fading out, as cos over its own quiet run and as cos
    squared over an overlap longer than that run (the next intro's or the floor's, which starts in a's body), a's
    trailing silence never played, then b exact; no step at either end of the overlap; one PCM; the plan's log line
    naming what decided the overlap. The plan and the largest step off."""
    A, B, Y = (array.array('h', x) for x in (reference(a), reference(b), got))
    overlap, trail, skip, fade, why = plan = smart_plan(A, B)
    na, nb, ny = len(A) // 2, len(B) // 2, len(Y) // 2
    cut = na - trail - overlap  # out_point
    assert names == ['0000_44100_2_s16.raw'], names
    assert not any(bad in log for bad in BAD_TAIL), log[-2000:]  # BAD_TAIL: below, with the tail's own tests
    assert f'overlap {overlap / 44100:.2f} s by the {why}' in log, f'{plan}: {log[-2000:]}'
    assert ny == cut + nb - skip, f'{ny} frames, expected {cut + nb - skip} (plan {plan}): {log[-1500:]}'
    assert Y[:2 * cut] == A[:2 * cut], f'A before out_point, first difference at frame {first_difference(Y[:2 * cut].tobytes(), A[:2 * cut].tobytes()) // 4}'
    worst = 0
    for k in range(overlap):
        go = math.cos((k + 0.5) / overlap * math.pi / 2) ** (1 if why == 'out run' else 2)
        gi = math.sin((k + 0.5) / fade * math.pi / 2) if k < fade else 1
        for c in (0, 1):
            want = B[2 * (skip + k) + c] * gi + A[2 * (cut + k) + c] * go
            worst = max(worst, abs(Y[2 * (cut + k) + c] - want))
    assert worst <= 2, f'the overlap is {worst} steps from B + A by the {why} (plan {plan})'
    assert Y[2 * (cut + overlap):] == B[2 * (skip + overlap):], 'B after the overlap'
    def step(i): return max(abs(Y[2 * i + c] - Y[2 * (i - 1) + c]) for c in (0, 1))
    around = max(max(step(i) for i in range(cut - 50, cut - 1)), max(step(i) for i in range(cut + overlap + 2, cut + overlap + 50)))
    assert step(cut) <= around and step(cut + overlap) <= around, (step(cut), step(cut + overlap), around)
    return plan, worst


def smart_case(player, songs, tmp, name, a, b, want):
    """A into B, smart: check_smart's output, the plan as want(plan) asserts; a line saying what played."""
    s, names, got, log = smart_play(player, songs, tmp/name, smart_scenario(a, b))
    plan, worst = check_smart(a, b, names, got, log)
    overlap, trail, skip, fade, why = plan
    assert want(plan), plan
    cpu = re.findall(r'analysis took (\d+) ms of CPU', log)
    return (f'plan {overlap / 44100:.2f} s overlap by the {why}, {trail / 44100:.2f} s trail, {skip / 44100:.2f} s skipped, '
            f'fade-in {fade * 1000 // 44100} ms, as the reference; A exact to out_point, the overlap within {worst:.1f} steps, '
            f'B exact after; analysis {cpu} ms CPU')


def test_smart_crossfade(player, songs, tmp):
    """A fades out into silence, its quiet run 1.5 s, B starts hard after 1.5 s of silence: the overlap is the 5 s
    floor, the plan the reference's (its rules written again in Python over the reference decodes) and the output
    check_smart's."""
    a, b = smart_songs(tmp/'smart')
    return smart_case(player, songs, tmp, 'smart/run', a, b,
                      lambda p: p[4] == 'floor' and p[0] == 5 * 44100 and p[2] == 66150 and p[3] == 0)


def test_smart_crossfade_intro(player, songs, tmp):
    """A loud to its last frame, B a 10 s soft intro after 1 s of silence: the overlap is the intro, 10 s, B's intro
    under A's last 10 s, B faded in (its start 22 dB under its body)."""
    a, b = hard_end(tmp/'smartintro'), soft_intro(tmp/'smartintro', 10)
    return smart_case(player, songs, tmp, 'smartintro/run', a, b,
                      lambda p: p[4] == 'next intro' and p[0] == 10 * 44100 and p[1] == 0 and p[2] == 44100 and p[3] == 44100)


def test_smart_crossfade_cap(player, songs, tmp):
    """A loud to its last frame, B a 20 s soft intro: the overlap is 15 s, XF_MAX_S, the rest of the intro B's own."""
    a, b = hard_end(tmp/'smartcap'), soft_intro(tmp/'smartcap', 20)
    return smart_case(player, songs, tmp, 'smartcap/run', a, b, lambda p: p[4] == 'next intro' and p[0] == 15 * 44100)


def test_smart_crossfade_long_fade(player, songs, tmp):
    """A fades for its last 25 s, all of it inside the 30 s measured, B hard: A's quiet run is the overlap, over 8 s."""
    a, b = long_fade(tmp/'smartfade'), smart_songs(tmp/'smartfade')[1]
    return smart_case(player, songs, tmp, 'smartfade/run', a, b, lambda p: p[4] == 'out run' and p[0] > 8 * 44100)


def test_smart_crossfade_starved(player, songs, tmp):
    """The player and its analysis thread on one core with two busy neighbours on it (the Q2's second core taken by
    demo, at worst), and stopped for 300 ms of every second besides (fakedemo's Q2_TEST_STARVE): the change is still
    whole, the smart fade or, logged, the plain one, never a tail cut short."""
    a, b = smart_songs(tmp/'smart')
    cpu = str(max(os.sched_getaffinity(0)))
    wrap = tmp/'starved.sh'
    wrap.write_text(f'#!/bin/sh\nexec taskset -c {cpu} {player}\n')
    wrap.chmod(0o755)
    busy = [subprocess.Popen(['taskset', '-c', cpu, sys.executable, '-c', 'while True: pass']) for _ in range(2)]
    was = os.environ.get('Q2_TEST_STARVE')
    os.environ['Q2_TEST_STARVE'] = '300/1000'
    try:
        s, names, got, log = smart_play(str(wrap), songs, tmp/'smartstarved', smart_scenario(a, b))
    finally:
        for p in busy: p.kill(), p.wait()
        if was is None: os.environ.pop('Q2_TEST_STARVE', None)
        else: os.environ['Q2_TEST_STARVE'] = was
    if 'smart crossfade: plain' in log:
        lines = [l for l in log.splitlines() if 'smart crossfade: plain' in l]
        assert len(lines) == 1, lines
        worst = check_plain(*(array.array('h', x) for x in (reference(a), reference(b), got)), 5 * 44100)
        return f'the plain fade, whole (within {worst:.1f} steps), logged: {lines[0].split("q2player: ")[-1]}'
    (overlap, *_, why), worst = check_smart(a, b, names, got, log)
    return f'the smart fade, whole: {overlap / 44100:.2f} s overlap by the {why} within {worst:.1f} steps, on one core shared with two busy loops'


def check_plain(A, B, Y, h):
    """Y is the plain fade of h frames: A exact to its last h, those under B's first h at equal power, B exact after;
    the largest step from equal power."""
    na = len(A) // 2
    assert len(Y) == len(A) + len(B) - 2 * h, f'{len(Y) // 2} frames, expected {(len(A) + len(B)) // 2 - h}'
    assert Y[:2 * (na - h)] == A[:2 * (na - h)], 'A before its tail'
    worst = 0
    for k in range(h):
        p = (k + 0.5) / h * math.pi / 2
        for c in (0, 1):
            worst = max(worst, abs(Y[2 * (na - h + k) + c] - (B[2 * k + c] * math.sin(p) + A[2 * (na - h + k) + c] * math.cos(p))))
    assert worst <= 2, f'the mix is {worst} from equal power'
    assert Y[2 * na:] == B[2 * h:], 'B after the mix'
    return worst


def smart_falls_back(player, songs, tmp, name, why, steps=None, env=None, b=None):
    """The scenario's change is the plain 5 s fade, with the log line saying why."""
    a, b0 = smart_songs(tmp/'smart')
    b = b or b0
    s, names, got, log = smart_play(player, songs, tmp/name, steps or smart_scenario(a, b), env=env)
    lines = [l for l in log.splitlines() if 'smart crossfade: plain' in l]
    assert len(lines) == 1 and why in lines[0], f'{lines}: {log[-1500:]}'
    worst = check_plain(*(array.array('h', x) for x in (reference(a), reference(b), got)), 5 * 44100)
    return f'the plain 5 s fade (within {worst:.1f} steps), logged once: {lines[0].split("q2player: ")[-1]}'


def test_smart_crossfade_next_missing(player, songs, tmp):
    """XFADE_NEXT names a file that is not on the card: the plain fade."""
    a, b = smart_songs(tmp/'smart')
    return smart_falls_back(player, songs, tmp, 'smartmiss', 'the next song is not on the card',
                            smart_scenario(a, b, next_text=f'{a}\n{tmp}/smart/gone.flac\n'))


def test_smart_crossfade_next_unknown(player, songs, tmp):
    """XFADE_NEXT names the next song of another change (demo's next has moved on): the plain fade."""
    a, b = smart_songs(tmp/'smart')
    return smart_falls_back(player, songs, tmp, 'smartother', 'follows another',
                            smart_scenario(a, b, next_text=f'{tmp}/smart/other.flac\n{b}\n'))


def test_smart_crossfade_too_slow(player, songs, tmp):
    """An analysis not done by the plain fade's hold (Q2PLAYER_SMART_DELAY_MS): the plain fade, and the late answer
    is thrown away."""
    return smart_falls_back(player, songs, tmp, 'smartslow', 'the analysis took too long', env={'Q2PLAYER_SMART_DELAY_MS': '30000'})


def test_smart_crossfade_next_arriving(player, songs, tmp):
    """The next song still arriving on the card (player/grow.c), a tenth of it there when the analysis looks: the
    plain fade; the rest lands before the change."""
    a, b0 = smart_songs(tmp/'smart')
    out = tmp/'smartgrow'
    card = out/'mnt/mmc/.foo'
    card.mkdir(parents=True, exist_ok=True)
    whole = b0.read_bytes()
    b = card/'sb.flac'
    def line(state, n):
        put(out/'tmp/q2-foo.stream', f'#Q2STREAM/1\tseq=1\n{state}\t{n}\t{len(whole)}\t20000\t/mnt/mmc/.foo/sb.flac\n')
    def start(s):
        b.write_bytes(whole[:len(whole) // 10])
        line('get', len(whole) // 10)
    def land(s):
        b.write_bytes(whole)
        line('done', len(whole))
    steps = smart_scenario(a, b)
    steps = sorted(steps[:5] + [(0.25, start)] + [(t / 2, lambda s: line('get', len(whole) // 10)) for t in range(1, 16)]
                   + [(8.0, land)] + steps[5:], key=lambda st: st[0])
    return smart_falls_back(player, songs, tmp, 'smartgrow', 'the next song is still downloading', steps, b=b)


def test_smart_crossfade_off_is_gapless(player, songs, tmp):
    """No fade asked (demo's word 0: an album in order, Repeat One, the queue's end, a DSD next) with XFADE_NEXT
    there: A then B, bit for bit, no analysis."""
    a, b = smart_songs(tmp/'smart')
    s, names, got, log = smart_play(player, songs, tmp/'smartoff', smart_scenario(a, b, word=0))
    assert got == reference(a) + reference(b), 'not the two songs joined'
    assert 'smart crossfade' not in log, log[-800:]
    return 'A and B joined bit for bit, nothing analysed'


# ---- the tail from the file (player/tail.c, dsp.c; docs/internals.md#the-tail-from-the-file)
BAD_TAIL = ('differs from the first decode', 'frames fewer than the tail held', 'differs from the first')
TAIL_B_S = 47  # each B here: over XF_SMART_LEAD_S long, as B_SECONDS
# Q2PLAYER_XF_WAIT (player/tail.c): the player waits for its analysis and for the second open's decode wherever it
# would otherwise go on without them, so what these tests compare does not depend on how busy the machine is
TAIL_WAIT = {'Q2PLAYER_XF_WAIT': '1'}


def tail_song(path, rate=44100, bits=16, kind='fade', codec=('-c:a', 'flac'), tags=()):
    """A song of noise and a tone, so no two stretches of it are alike. fade (A, 40 s): at 0.6 to 27.5 s, then a quiet
    run 14 dB down falling 1.44 dB a second to 38 s, then 2 s of silence: an out run of about 10.5 s and a 2 s trail,
    12.5 s held. hard (A, 36 s): at 0.6 to its last frame, so the overlap is the 5 s floor. next (B, TAIL_B_S): half a
    second of silence, then noise and a tone at 0.3."""
    seconds = {'fade': 40, 'hard': 36, 'next': TAIL_B_S}[kind]
    shape = {'fade': "volume=0.6,volume='if(lt(t,27.5),1,if(lt(t,38),0.2*pow(10,-(t-27.5)*1.44/20),0))':eval=frame",
             'hard': 'volume=0.6', 'next': 'volume=0.3,adelay=500:all=1'}[kind]
    fmt = [] if codec[1].startswith('pcm') or codec[1] == 'libmp3lame' else ['-sample_fmt', 's16' if bits == 16 else 's32', '-bits_per_raw_sample', str(bits)]
    if codec[1] == 'alac': fmt = ['-sample_fmt', 's16p' if bits == 16 else 's32p', '-bits_per_raw_sample', str(bits)]
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', f'anoisesrc=d={seconds}:c=pink:r={rate}:a=0.5:seed={3 if kind == "next" else 1}',
                    '-f', 'lavfi', '-i', f'sine=f={330 if kind == "next" else 220}:r={rate}:d={seconds}', '-filter_complex',
                    f'[0][1]amix=inputs=2:normalize=0,{shape},aformat=channel_layouts=stereo', '-t', str(seconds), *codec, *fmt, *tags, path], check=True)
    return path


def tail_runs(player, songs, d, a, b, ring_mb, steps=None, runs=('file', 'ring'), env=None):
    """a into b by smart_scenario, once for each of runs: 'file', the player as it is (a FLAC's, an ALAC's or a PCM
    file's tail read from the file a second time), with env; 'ring', every tail held in memory in a ring of up to
    ring_mb MB (Q2PLAYER_XF_RING); 'ring6', in the 6 MB ring a song has without a second open. Each with TAIL_WAIT.
    {run: (PCM names, PCM, log)}."""
    out = {}
    for run in runs:
        e = dict(TAIL_WAIT, **(env or {})) if run == 'file' else dict(TAIL_WAIT, Q2PLAYER_XF_RING='6' if run == 'ring6' else str(ring_mb))
        s, names, got, log = smart_play(player, songs, d/run, steps or smart_scenario(a, b, end=110.0), env=e)
        out[run] = (names, got, log)
    return out


def plan_line(log):
    lines = [l.split('q2player: ')[-1] for l in log.splitlines() if 'smart crossfade: body' in l]
    return lines[0] if lines else None


def check_tail(runs, planned=True):
    """The change with its tail read from the file is, byte for byte, the change with its tail held in memory: the
    same plan, the same PCM opens, the same output; the file's run held nothing, and no frame it read differed from
    the first decode's or was missing. The ring's size in KB."""
    (fn, fg, fl), (rn, rg, rl) = runs['file'], runs['ring']
    assert 'the tail read from the file' in fl and 'held in memory' not in fl, fl[-1500:]
    assert not any(bad in fl for bad in BAD_TAIL), fl[-1500:]
    held = re.search(r'the tail held in memory, (\d+) KB', rl)
    assert held and 'read from the file' not in rl, rl[-1500:]
    for log in (fl, rl):
        assert 'overlap cut' not in log and 'smart crossfade: plain' not in log, log[-1500:]
    assert plan_line(fl) == plan_line(rl) and (plan_line(fl) is not None) == planned, (plan_line(fl), plan_line(rl))
    assert fn == rn, (fn, rn)
    at = first_difference(fg, rg)
    assert at is None, f'{len(fg)} bytes read from the file, {len(rg)} held in memory; first difference at byte {at}'
    return int(held[1])


def overlap_s(log): return float(re.search(r'overlap ([\d.]+) s by the', plan_line(log))[1])


def test_tail_file_flac_16(player, songs, tmp):
    """FLAC, 44.1 kHz 16-bit: a 12.5 s hold read from the file is the 6 MB ring's, byte for byte."""
    d = tmp/'tail16'
    a, b = tail_song(d/'a.flac'), tail_song(d/'b.flac', kind='next')
    runs = tail_runs(player, songs, d, a, b, 6)
    kb = check_tail(runs)
    assert overlap_s(runs['file'][2]) > 10
    return f'{len(runs["file"][1]) // 4} frames the same, a {overlap_s(runs["file"][2]):.2f} s overlap; the ring was {kb} KB'


def test_tail_file_flac_24_96_past_the_ring(player, songs, tmp):
    """FLAC, 96 kHz 24-bit: the 12.5 s hold is more than the 6 MB ring has room for at this rate (7.5 s), so in that
    ring the overlap is cut, logged; read from the file it is whole, and byte for byte what a ring large enough gives."""
    d = tmp/'tail96'
    a, b = tail_song(d/'a.flac', 96000, 24), tail_song(d/'b.flac', 96000, 24, kind='next')
    runs = tail_runs(player, songs, d, a, b, 64, runs=('file', 'ring', 'ring6'))
    kb = check_tail(runs)
    assert runs['file'][0] == ['0000_96000_2_s32.raw'], runs['file'][0]
    cut = runs['ring6'][2]
    assert 'overlap cut to what the hold has room for' in cut and len(runs['ring6'][1]) > len(runs['file'][1]), cut[-1500:]
    return (f'{len(runs["file"][1]) // 8} frames the same as a {kb} KB ring\'s, a {overlap_s(runs["file"][2]):.2f} s overlap; '
            f'the 6 MB ring cut it and played {(len(runs["ring6"][1]) - len(runs["file"][1])) / 8 / 96000:.2f} s longer')


def test_tail_file_flac_24_192_floor(player, songs, tmp):
    """FLAC, 192 kHz 24-bit, loud to its last frame, into a 44.1 kHz 16-bit song: the overlap is the 5 s floor, more
    than the 6 MB ring holds at 192 kHz (3.75 s), which cuts it; read from the file it is whole, mixed under the next
    song at that song's rate after the PCM opens again, byte for byte what a ring large enough gives."""
    d = tmp/'tail192'
    a, b = tail_song(d/'a.flac', 192000, 24, kind='hard'), tail_song(d/'b.flac', kind='next')
    runs = tail_runs(player, songs, d, a, b, 64, runs=('file', 'ring', 'ring6'))
    kb = check_tail(runs)
    assert runs['file'][0] == ['0000_192000_2_s32.raw', '0001_44100_2_s16.raw'], runs['file'][0]
    assert overlap_s(runs['file'][2]) == 5.0 and 'overlap cut to what the hold has room for' in runs['ring6'][2]
    return f'{len(runs["file"][1])} bytes the same as a {kb} KB ring\'s: the 5 s floor whole at 192 kHz, cut by the 6 MB ring'


def test_tail_file_alac_and_wav(player, songs, tmp):
    """ALAC in MP4 and PCM in WAV, 44.1 kHz 16-bit: each tail read from the file is the ring's, byte for byte."""
    said = []
    for ext, codec in (('m4a', ('-c:a', 'alac')), ('wav', ('-c:a', 'pcm_s16le'))):
        d = tmp/f'tail{ext}'
        a, b = tail_song(d/f'a.{ext}', codec=codec), tail_song(d/f'b.{ext}', codec=codec, kind='next')
        runs = tail_runs(player, songs, d, a, b, 6)
        check_tail(runs)
        said.append(f'{ext}: {len(runs["file"][1]) // 4} frames the same')
    return ', '.join(said)


def test_tail_mp3_is_held_in_memory(player, songs, tmp):
    """MP3: FFmpeg's integer decoder carries its rounding from sample to sample, so a second decode is never the
    first's samples, and the player says so and holds the tail in a ring, mapped at the hold's own size; the output is
    the same whether or not it was let try the file."""
    d = tmp/'tailmp3'
    a, b = tail_song(d/'a.mp3', codec=('-c:a', 'libmp3lame', '-q:a', '2')), tail_song(d/'b.mp3', codec=('-c:a', 'libmp3lame', '-q:a', '2'), kind='next')
    runs = tail_runs(player, songs, d, a, b, 6)
    (fn, fg, fl), (rn, rg, rl) = runs['file'], runs['ring']
    assert 'a second decode of this codec is not the first\'s' in fl and 'read from the file' not in fl, fl[-1500:]
    held = re.search(r'the tail held in memory, (\d+) KB', fl)
    assert held and int(held[1]) < 6 * 1024 and plan_line(fl) == plan_line(rl) and plan_line(fl)
    at = first_difference(fg, rg)
    assert fn == rn and at is None, at
    return f'held in a {held[1]} KB ring, as said; {len(fg) // 4} frames the same either way'


def test_tail_file_eq_and_replaygain(player, songs, tmp):
    """The EQ on and a ReplayGain tag on the first song, read as track gain: the file's frames go through the same
    stages as the ring's did (the scale and its clip, the EQ from the memory it had at the hold's first frame), and
    the second song takes on the EQ's memory of the first's last frame: byte for byte the ring's, and not what plays
    with the EQ off."""
    d = tmp/'taileq'
    a = tail_song(d/'a.flac', tags=('-metadata', 'REPLAYGAIN_TRACK_GAIN=-4.00 dB', '-metadata', 'REPLAYGAIN_TRACK_PEAK=0.600000'))
    b = tail_song(d/'b.flac', kind='next')
    state = d/'eq.state'
    subprocess.run([tool(tmp, 'peq_ref', '-DPEQ_ROOT=""'), 'save', state, '6', '-3'], check=True)
    def steps(eq):
        st = smart_scenario(a, b, end=110.0)
        st = [x for x in st if not (isinstance(x[1], str) and x[1].startswith('{mcl-replaygain'))] + [(0.0, '{mcl-replaygain\\1}')]
        if eq: st.append((0.2, fakedemo.put('mnt/data/peq-active', state.read_bytes())))
        return sorted(st, key=lambda x: x[0])
    runs = tail_runs(player, songs, d, a, b, 6, steps=steps(True))
    check_tail(runs)
    s, names, flat, log = smart_play(player, songs, d/'flat', steps(False), env=TAIL_WAIT)
    assert flat != runs['file'][1], 'the EQ changed nothing'
    plain = tail_runs(player, songs, d/'norg', a, b, 6, runs=('file',))['file'][1]
    assert plain != flat, 'ReplayGain changed nothing'
    return f'{len(runs["file"][1]) // 4} frames the same through ReplayGain and the EQ'


def test_tail_file_plain_fade(player, songs, tmp):
    """demo's word with no XF_SMART, 3 s: the plain fade's tail read from the file is the ring's, byte for byte."""
    d = tmp/'tailplain'
    a, b = tail_song(d/'a.flac'), tail_song(d/'b.flac', kind='next')
    runs = tail_runs(player, songs, d, a, b, 6, steps=smart_scenario(a, b, end=110.0, word=3000))
    check_tail(runs, planned=False)
    return f'{len(runs["file"][1]) // 4} frames the same'


def test_tail_file_second_open_late(player, songs, tmp):
    """The second open comes too late (Q2PLAYER_TAIL_DELAY_MS, as a starved player's would), at 96 kHz 24-bit: the
    hold begins without it, said in the log, the tail is held in the 6 MB ring with its overlap cut, and the change
    is byte for byte the one a player with no second open gives."""
    d = tmp/'taillate'
    a, b = tail_song(d/'a.flac', 96000, 24), tail_song(d/'b.flac', 96000, 24, kind='next')
    runs = tail_runs(player, songs, d, a, b, 6, runs=('file', 'ring6'), env={'Q2PLAYER_TAIL_DELAY_MS': '600000'})
    (fn, fg, fl), (rn, rg, rl) = runs['file'], runs['ring6']
    assert 'the second open was not level when the hold began' in fl and 'read from the file' not in fl, fl[-1500:]
    assert 'overlap cut to what the hold has room for' in fl and 'overlap cut to what the hold has room for' in rl
    at = first_difference(fg, rg)
    assert fn == rn and at is None, at
    return f'held in the ring, logged; {len(fg) // 8} frames the same as with no second open'


TAIL_HOLD, TAIL_MIX = 13.1, 27.55  # tail_song's fade A: its hold begins about here, and its mix here, in its seconds


def tail_stalled(player, songs, tmp, name, at, frames):
    """tail_song's A into B, FLAC 44.1 kHz 16-bit, the second open's decode stopped at A's second `at` until `frames`
    more are taken (Q2PLAYER_TAIL_STALL; below 0, for good), and the same held in the 6 MB ring."""
    d = tmp/name
    a, b = tail_song(d/'a.flac'), tail_song(d/'b.flac', kind='next')
    return tail_runs(player, songs, d, a, b, 6, env={'Q2PLAYER_TAIL_STALL': f'{int(at * 44100)}:{frames}'})


def test_tail_file_card_stalls_2s(player, songs, tmp):
    """The card does not answer the second open for 2 s of the song, once inside the hold and once under the next
    song: the frames those 2 s need were decoded ahead, off the playback thread, so the song never finds the open
    short (its own count, logged), and the change is byte for byte the ring's."""
    said = []
    for where, at in (('hold', TAIL_HOLD + 7), ('mix', TAIL_MIX + 5)):
        runs = tail_stalled(player, songs, tmp, f'tailstall{where}', at, 2 * 44100)
        check_tail(runs)
        m = re.search(r'reader was stalled while (\d+) frames were taken, and the song found it short (\d+) times', runs['file'][2])
        assert m and int(m[1]) >= 2 * 44100 and m[2] == '0', runs['file'][2][-1500:]
        said.append(f'in the {where}: stalled while {m[1]} frames played, never short, {len(runs["file"][1]) // 4} frames the same')
    return '; '.join(said)


def test_tail_file_card_gone_before_the_hold(player, songs, tmp):
    """The card stops answering the second open for good just before the hold: the song does not wait, the tail is
    held in the ring, said in the log, and the change is byte for byte the ring's."""
    runs = tail_stalled(player, songs, tmp, 'tailgone0', TAIL_HOLD - 0.3, -1)
    (fn, fg, fl), (rn, rg, rl) = runs['file'], runs['ring']
    lines = [l.split('q2player: ')[-1] for l in fl.splitlines() if 'the tail held in memory, the second open' in l]
    assert lines and 'read from the file' not in fl and re.search(r'the tail held in memory, \d+ KB', fl), fl[-1500:]
    at = first_difference(fg, rg)
    assert fn == rn and at is None, at
    return f'{lines[0]}; {len(fg) // 4} frames the same as the ring\'s'


def test_tail_file_card_gone_in_the_hold(player, songs, tmp):
    """The card stops answering the second open for good 7 s into the hold: every frame it had decoded plays, in
    order (the ring's bytes up to there), then after XF_HOLD_WAIT_MS the hold is left as at a seek, logged, the song
    plays on from where its decode is, and the next song plays whole to its end."""
    at = TAIL_HOLD + 7
    runs = tail_stalled(player, songs, tmp, 'tailgone1', at, -1)
    (fn, fg, fl), (rn, rg, rl) = runs['file'], runs['ring']
    assert 'the second open stopped giving frames' in fl and 'the tail read from the file' in fl, fl[-1500:]
    upto = (int(at * 44100) - 8192) * 4
    assert fn == rn and fg[:upto] == rg[:upto], first_difference(fg[:upto], rg[:upto])
    assert len(fg) < len(rg) and fg[-20 * 44100 * 4:] == rg[-20 * 44100 * 4:], (len(fg), len(rg))
    return f'the ring\'s bytes to within a block of second {at:.1f}, then {(len(rg) - len(fg)) / 4 / 44100:.2f} s of the song left out; the next song\'s last 20 s the same'


def test_tail_file_card_gone_under_the_next_song(player, songs, tmp):
    """The card stops answering the second open for good 5 s into the mix: the tail fades out over its last decoded
    frames, logged; up to there the change is the ring's bytes, the next song loses no frame, and once the ring's
    own mix is over the two are the same again."""
    at = TAIL_MIX + 5
    runs = tail_stalled(player, songs, tmp, 'tailgone2', at, -1)
    (fn, fg, fl), (rn, rg, rl) = runs['file'], runs['ring']
    assert re.search(r'the tail faded out \d+ ms early', fl), fl[-1500:]
    upto = (int(at * 44100) - int(0.2 * 44100) - 8192) * 4
    after = (38 * 44100 + 8192) * 4  # the tail's last frame, and the limiter's recovery
    assert fn == rn and len(fg) == len(rg) and fg[:upto] == rg[:upto] and fg[after:] == rg[after:]
    assert fg[upto:after] != rg[upto:after]
    return f'the ring\'s bytes to {upto // 4 / 44100:.2f} s, the tail gone by {at:.1f} s, the same again from 38.2 s; {len(fg) // 4} frames either way'


def test_tail_file_starved(player, songs, tmp):
    """As test_smart_crossfade_starved, on one core with two busy loops and stopped 300 ms of every second, with no
    Q2PLAYER_XF_WAIT: whatever the starved player manages (the smart fade or, the analysis too slow, the plain one,
    logged; the tail read from the file or, the second open late or behind, held in memory, logged), the change is
    whole: byte for byte one of the two an unstarved player holding the tail in memory gives."""
    d = tmp/'tailstarved'
    a, b = tail_song(d/'a.flac'), tail_song(d/'b.flac', kind='next')
    ring = dict(TAIL_WAIT, Q2PLAYER_XF_RING='6')
    want = {'smart': smart_play(player, songs, d/'smart', smart_scenario(a, b, end=110.0), env=ring)[2],
            'plain': smart_play(player, songs, d/'plain', smart_scenario(a, b, end=110.0), env=dict(ring, Q2PLAYER_SMART_DELAY_MS='60000'))[2]}
    assert want['smart'] != want['plain']
    cpu = str(max(os.sched_getaffinity(0)))
    wrap = tmp/'tailstarved.sh'
    wrap.write_text(f'#!/bin/sh\nexec taskset -c {cpu} {player}\n')
    wrap.chmod(0o755)
    busy = [subprocess.Popen(['taskset', '-c', cpu, sys.executable, '-c', 'while True: pass']) for _ in range(2)]
    was = os.environ.get('Q2_TEST_STARVE')
    os.environ['Q2_TEST_STARVE'] = '300/1000'
    try:
        s, names, got, log = smart_play(str(wrap), songs, d/'starved', smart_scenario(a, b, end=110.0))
    finally:
        for p in busy: p.kill(), p.wait()
        if was is None: os.environ.pop('Q2_TEST_STARVE', None)
        else: os.environ['Q2_TEST_STARVE'] = was
    assert not any(bad in log for bad in BAD_TAIL), log[-1500:]
    assert 'stopped giving frames' not in log and 'faded out' not in log, log[-1500:]
    kind = 'plain' if 'smart crossfade: plain' in log else 'smart'
    at = first_difference(got, want[kind])
    assert at is None, f'starved, the {kind} fade differs from the unstarved one at byte {at} of {len(got)}, {len(want[kind])}: {log[-1500:]}'
    how = 'read from the file' if 'the tail read from the file' in log else 'held in memory'
    assert how == 'read from the file' or 'the tail held in memory, ' in log, log[-1500:]
    return f'the {kind} fade, its tail {how}: {len(got) // 4} frames, the same as unstarved'


# mcl-replaygain's scale for each file of music.REPLAYGAIN and b1.mp3, by mode: hciplayer's rule (player/source.c
# source_replaygain): the gain, never above 1 / peak, a missing peak counting as 1; a missing track gain falls back to
# the album's in mode 1; rglame.mp3's only gain is its LAME header's, which is not read.
def _rg(db, peak=1.0): return min(10 ** (db / 20), 1 / peak)
RG_SCALE = {'track': {'rg.flac': _rg(-6, .5), 'rg.ogg': _rg(-6, .5), 'rg23.mp3': _rg(-6, .5), 'rg24.mp3': _rg(-6, .5), 'rg.m4a': _rg(-6, .5),
                      'rgpeak.flac': _rg(6, .8), 'rgnopeak.flac': _rg(6), 'rgalbum.flac': _rg(-9, .5), 'rglame.mp3': 1},
            'album': {'rg.flac': _rg(-3.5, .6), 'rg.ogg': _rg(-3.5, .6), 'rg23.mp3': _rg(-3.5, .6), 'rg24.mp3': _rg(-3.5, .6), 'rg.m4a': _rg(-3.5, .6),
                      'rgpeak.flac': 1, 'rgnopeak.flac': 1, 'rgalbum.flac': _rg(-9, .5), 'rglame.mp3': 1}}


def check_replaygain(player, songs, tmp, mode):
    s, pcm, names = play(player, songs, f'rg-{mode}', tmp/f'rg-{mode}', speed=8)
    stream, out, worst = b''.join(pcm), [], {}
    at = 0
    for f, scale in RG_SCALE[mode].items():
        fmt = 'f32le' if f.endswith('.ogg') else 's16le'
        ref = reference(songs/f, fmt)
        part = stream[at:at + len(ref)]
        at += len(ref)
        x, y = (array.array('f' if fmt == 'f32le' else 'h', v) for v in (ref, part))
        assert len(x) == len(y), f'{f}: {len(y)} samples, reference {len(x)}'
        if fmt == 'f32le': err = max(abs(v - max(-1.0, min(1.0, u * scale))) for u, v in zip(x, y)) * 32768
        else: err = max(abs(v - max(-32768, min(32767, u * scale))) for u, v in zip(x, y))
        worst[f] = err
        assert err <= 1, f'{mode} {f}: {err:.2f} from {scale:.4f} times the reference'
        if scale == 1: assert x == y, f'{mode} {f}: changed, where there is no gain'
    assert at == len(stream), f'{len(stream) - at} bytes more than the files'
    return f'{len(worst)} files, each its scale times the reference within {max(worst.values()):.2f} of a 16-bit step'


def test_replaygain_track(player, songs, tmp): return check_replaygain(player, songs, tmp, 'track')
def test_replaygain_album(player, songs, tmp): return check_replaygain(player, songs, tmp, 'album')


# The Clicker's sounds as player/output.c makes them (click_table), in float as it does.
CLICKS = {'wheel': (1000, 4, 1.2), 'button': (1820, 3, 0.9), 'beep': (2280, 200, 60)}
def click_sound(kind, rate=44100):
    f32 = lambda v: struct.unpack('f', struct.pack('f', v))[0]
    hz, ms, decay = CLICKS[kind]
    n, level, rise, fall = int(ms * rate / 1000), 10 ** (-20 / 20), 0.25 * rate / 1000, 0.5 * rate / 1000
    out = []
    for i in range(n):
        t = i / rate
        env = level * math.exp(-t * 1000 / decay)
        if i < rise: env *= 0.5 - 0.5 * math.cos(math.pi * i / rise)
        if n - i < fall: env *= 0.5 - 0.5 * math.cos(math.pi * (n - i) / fall)
        out.append((f32(env), f32(env * math.sin(2 * math.pi * hz * t))))
    return out


def chirp_sound(rate=44100):
    """The plug-in chirp as player/output.c makes it (chirp_at, click_table's CLICK_CHARGE), in float as it does."""
    f32 = lambda v: struct.unpack('f', struct.pack('f', v))[0]
    n, level, tick, seed, raw = int(340 * rate / 1000), 10 ** (-12 / 20), 0.0, 7, []
    for i in range(n):
        t, y, e = i / rate, 0.0, 0.0
        if t < 0.003:
            seed = (seed * 1664525 + 1013904223) & 0xffffffff
            noise = (seed >> 8) / (1 << 23) - 1
            tick = 0.7 * noise + 0.3 * tick if i else 0.0
            env = 0.7 * math.exp(-t * 4000 / 3)
            y, e = y + env * tick, e + env
        for hz, at, length, decay in ((880, 30, 180, 50), (1320, 90, 250, 70)):
            u = t - at / 1000
            if 0 <= u < length / 1000:
                env, ph = 0.6 * min(u * 1000 / 2, 1) * math.exp(-u * 1000 / decay), 2 * math.pi * hz * u
                y, e = y + env * (math.sin(ph) + 0.15 * math.sin(2 * ph)), e + env * 1.15
        raw.append((f32(y), f32(e)))
    peak = max(abs(y) for y, _ in raw)
    return [(f32(level * min(e / peak, 1)), f32(level * y / peak)) for y, e in raw]


def test_chirp(player, songs, tmp):
    """mcl-click charge while silence plays: patch/charging.c's plug-in chirp, once, both channels, at its level (its
    peak -12 dBFS) and within a step of the sound made; none once paused."""
    s, pcm, names = play(player, songs, 'chirp', tmp/'chirp')
    x = array.array('h', b''.join(pcm))
    found, sound = runs(x), chirp_sound()
    first, n = found[0][0], found[-1][0] + found[-1][1] - found[0][0]  # the tick, a gap, then the two blips
    assert found and n <= len(sound), f'one chirp, {len(sound)} frames: {found}'
    lead = next(i for i, (e, c) in enumerate(sound) if abs(round(c * 32768)) >= 1)
    err = max(abs(x[2 * (first - lead + i) + c] - c2 * 32768) for i, (e, c2) in enumerate(sound) for c in (0, 1))
    assert err <= 1, f'{err:.2f} from the sound made'
    peak = max(abs(v) for v in x) / 32768
    assert abs(20 * math.log10(peak) + 12) < 0.1, f'peak {20 * math.log10(peak):.2f} dBFS'
    return f'{n} frames within {err:.2f} of the chirp made, its peak {20 * math.log10(peak):.2f} dBFS'


def runs(x):
    """The stretches of x (an S16 stereo array) that are not silent, as (first frame, frames)."""
    out, start = [], None
    for i in range(0, len(x), 2):
        if (x[i] or x[i + 1]) and start is None: start = i // 2
        if not (x[i] or x[i + 1]) and start is not None:
            if out and start - (out[-1][0] + out[-1][1]) < 8: out[-1] = (out[-1][0], i // 2 - out[-1][0])  # a zero crossing inside one
            else: out.append((start, i // 2 - start))
            start = None
    return out


def test_click(player, songs, tmp):
    """mcl-click while silence plays: each sound once, both channels, at its length and level, at about 20 ms past
    what was playing. Into a full-scale square, the click makes room: the mix stays inside 16 bits, as
    y = x (1 - e) + s. (With nothing playing: test_click_idle.)"""
    s, pcm, names = play(player, songs, 'click', tmp/'click')
    x = array.array('h', b''.join(pcm))
    found = runs(x)
    sent = [t for t, d, name, arg in parse('\n'.join(s.lines))[0] if d == '>' and name == 'mcl-click'][:3]
    start = [t for t, d, name, arg in parse('\n'.join(s.lines))[0] if d == '>' and name == 'set-songurl'][0]
    assert len(found) == 3, f'{len(found)} sounds: {found}'
    for (first, n), kind, t in zip(found, CLICKS, sent):
        sound = click_sound(kind)
        assert abs(n - len(sound)) <= 2, f'{kind}: {n} frames, {len(sound)} made'
        # the sound's first audible frame may come a frame or two into it, where it is still under half a step
        lead = next(i for i, (e, c) in enumerate(sound) if abs(round(c * 32768)) >= 1)
        err = max(abs(x[2 * (first - lead + i) + c] - c2 * 32768) for i, (e, c2) in enumerate(sound) for c in (0, 1))
        assert err <= 1, f'{kind}: {err:.2f} from the sound made'
        when = (first - lead) / 44100 - (t - start)
        assert -0.05 < when < 0.15, f'{kind}: {when * 1000:.0f} ms after its command, counted from the song\'s start'
    s, pcm, names = play(player, songs, 'click-full', tmp/'click-full')
    y, ref = array.array('h', b''.join(pcm)), array.array('h', reference(songs/'full.wav'))
    moved = [i // 2 for i in range(len(y)) if y[i] != ref[i]]
    assert moved and moved[-1] - moved[0] < len(click_sound('wheel')), f'{len(moved)} samples changed, frames {moved[:1]}..{moved[-1:]}'
    lead = next(i for i, (e, c) in enumerate(click_sound('wheel')) if e > 0)
    worst = 0
    for i, (e, c) in enumerate(click_sound('wheel')):
        k = moved[0] - lead + i
        for ch in (0, 1):
            v = (ref[2 * k + ch] / 32768 * (1 - e) + c) * 32768
            assert -32768 <= v <= 32767.5, f'the mix leaves 16 bits at frame {k}: {v}'
            worst = max(worst, abs(y[2 * k + ch] - v))
    assert worst <= 1, f'the mix is {worst:.2f} from y = x (1 - e) + s'
    return f'{len(found)} sounds at their length and level; into full scale, within {worst:.2f} of the mix, inside 16 bits'


# ---- protocol

# ---- phase 4: DSD and the outputs

def dsd_bits(path):
    """A DSD file's bits, each channel's bytes in the order heard with the first bit most significant: from a DSF's
    blocks (least significant bit first) or a DSDIFF's interleaved bytes."""
    b = pathlib.Path(path).read_bytes()
    rev = bytes(int(f'{i:08b}'[::-1], 2) for i in range(256))
    if b[:4] == b'DSD ':
        ch, = struct.unpack_from('<I', b, 28 + 24); count, = struct.unpack_from('<Q', b, 28 + 36); block, = struct.unpack_from('<I', b, 28 + 44)
        data = b[28 + 52 + 12:]
        out = [bytearray() for _ in range(ch)]
        for at in range(0, len(data), block * ch):
            for c in range(ch): out[c] += data[at + c * block:at + (c + 1) * block]
        return [bytes(o[:count // 8]).translate(rev) for o in out]
    at = 16  # the chunks after FRM8's header and form type, to the sound data's
    while b[at:at + 4] != b'DSD ':
        n, = struct.unpack_from('>Q', b, at + 4)
        at += 12 + n + n % 2
    n, = struct.unpack_from('>Q', b, at + 4)
    data = b[at + 12:at + 12 + n]
    return [data[c::2] for c in range(2)]


def unpack_dop(pcm, channels=2):
    """DoP frames (S24_3LE: the second DSD byte, the first, the marker, as stock writes them) back to each channel's
    DSD bytes; the markers must go 0x05, 0xFA in turn from the first frame, the same in every channel."""
    out, bad = [bytearray() for _ in range(channels)], 0
    for f in range(len(pcm) // (3 * channels)):
        for c in range(channels):
            lo, mid, marker = pcm[3 * (f * channels + c):3 * (f * channels + c) + 3]
            bad += marker != (0x05 if f % 2 == 0 else 0xfa)
            out[c] += bytes((mid, lo))
    return [bytes(o) for o in out], bad


def unpack_native(pcm, channels=2):
    """DSD_U32_BE frames: each channel's 4 bytes a frame, the first heard first."""
    return [b''.join(pcm[i:i + 4] for i in range(4 * c, len(pcm), 4 * channels)) for c in range(channels)]


def check_dsd_raw(player, songs, tmp, way, mode, kind):
    s, pcm, names = play(player, songs, f'dsd-full-{way}-{mode}', tmp/f'dsd-{way}-{mode}', speed=8)
    suffix = {0: '', 1: '_bt', 2: '_usb'}[way]
    rates = [2822400 // (16 if kind == 'dop' else 32), 2822400 // (16 if kind == 'dop' else 32), 5644800 // (16 if kind == 'dop' else 32)]
    assert len(pcm) == 3 and [n.split('_', 1)[1] for n in names] == [f'{r}_2_{kind}{suffix}.raw' for r in rates], names
    for f, got in zip(('d64.dsf', 'd64.dff', 'd128.dsf'), pcm):
        want = dsd_bits(songs/f)
        bits, bad = unpack_dop(got) if kind == 'dop' else (unpack_native(got), 0)
        assert not bad, f'{f}: {bad} DoP frames with a wrong marker'
        assert bits == want, f'{f}: {[len(b) for b in bits]} bytes a channel, the file {[len(b) for b in want]}; first difference at {first_difference(bits[0], want[0])}'
    return f'3 files, {sum(len(x) for x in pcm)} bytes, each channel the file\'s bits'


def test_dop_usb(player, songs, tmp): return check_dsd_raw(player, songs, tmp, 2, 1, 'dop')
def test_dop_dac(player, songs, tmp): return check_dsd_raw(player, songs, tmp, 0, 0, 'dop')  # a Q2's DAC: DoP whatever the mode
def test_native_usb(player, songs, tmp): return check_dsd_raw(player, songs, tmp, 2, 2, 'dsd')


def ffref(tmp, *args):
    """test/player/ffref.c's output, built against the player's FFmpeg: a decode or a resample by FFmpeg 7.1.5 itself."""
    out = tmp/'ffref'
    if not out.exists():
        ff = pathlib.Path(subprocess.run([sys.executable, ROOT/'tools/player.py', 'ffmpeg', 'host'], check=True, capture_output=True, text=True).stdout.strip())
        sys.path.insert(0, str(ROOT/'tools'))
        import player as tools_player  # its FFmpeg's https needs mbedTLS at the link
        tls = tools_player.mbedtls('host')
        subprocess.run(['cc', '-std=c11', '-O2', '-Wall', '-Werror', '-I', ff/'include', HERE/'ffref.c', '-L', ff/'lib', '-lavformat', '-lavcodec',
                        '-lswresample', '-lavutil', '-L', tls/'lib', '-lmbedtls', '-lmbedx509', '-lmbedcrypto', '-lm', '-lpthread', '-o', out], check=True)
    return subprocess.run([out, *map(str, args)], check=True, capture_output=True).stdout


def test_d2p_usb(player, songs, tmp):
    """D2P: FFmpeg's DSD decoder, float at the DSD rate over 8, exactly as FFmpeg 7.1.5 decodes the file (the
    system's ffmpeg, 6.1, decodes the same tone and differs from it in the last place)."""
    s, pcm, names = play(player, songs, 'dsd-full-2-0', tmp/'d2p', speed=8)
    want = [ffref(tmp, 'decode', songs/f) for f in ('d64.dsf', 'd64.dff', 'd128.dsf')]
    assert [n.split('_', 1)[1] for n in names] == ['352800_2_f32_usb.raw', '352800_2_f32_usb.raw', '705600_2_f32_usb.raw'], names
    for f, g, w in zip(('d64.dsf', 'd64.dff', 'd128.dsf'), pcm, want):
        assert g == w, f'{f}: {len(g) // 8} frames, reference {len(w) // 8}, first difference at frame {(first_difference(g, w) or 0) // 8}'
    return f'3 files, {sum(len(x) for x in pcm) // 8} frames'


def scaled16(pcm, gain):
    """16-bit samples through the volume as stock's af_volume scales them, and output.c too: (x * vol) >> 8 saturated,
    vol = (int)(255 * level); gain 1 is the samples as they are (the filter taken out)."""
    if gain == 1: return pcm
    vol = int(255 * array.array('f', [gain])[0])
    return array.array('h', (max(-32768, min(32767, (v * vol) >> 8)) for v in array.array('h', pcm))).tobytes()


def runs_of_gain(got, ref, gains, block=441):
    """For got against ref from the same first frame, each block of 16-bit stereo frames named by the gain in gains
    that makes it exactly; the runs of names in order, and where the first block no gain explains starts. A block a
    change falls in is explained by none, and is passed over when the next one is explained."""
    names, at, missed = [], 0, 0
    for at in range(0, min(len(got), len(ref)) - 4 * block, 4 * block):
        g = next((x for x in gains if scaled16(ref[at:at + 4 * block], x) == got[at:at + 4 * block]), None)
        if g is None:
            if missed: return names, at - 4 * block
            missed = 1
            continue
        missed = 0
        if not names or names[-1] != g: names.append(g)
    return names, at


def test_usb_volume(player, songs, tmp):
    """A USB DAC: mcl-setvol scales what plays from a block on, as stock's mixer_setvolume reaches it between songs;
    a song starts at unity unless mcl-softvolflag is 1 (stock's 0x4235a8). 40 is 20/256, 50 25/256, as stock's own capture
    of this scenario (fixtures: stock-usb) is sample for sample."""
    s, pcm, names = play(player, songs, 'usb', tmp/'usb')
    assert names[:1] == ['0000_44100_2_s16_usb.raw'], names
    a1, a2, got = reference(songs/'a1.flac'), reference(songs/'a2.flac'), pcm[0]
    seen, cut = runs_of_gain(got, a1, (1.0, 0.08, 0.1))
    assert seen == [1.0, 0.08, 0.1], f'gains in turn {seen}'
    # a2 follows in the same PCM from its first frame, at 0.1 from its start (soft volume on by then)
    head = scaled16(a2[:4000], 0.1)
    k = got.find(head, cut - 4 * 441)
    assert k >= 0 and k % 4 == 0, 'a2 at 0.1 not found after a1'
    n = min(len(got) - k, len(a2))
    assert got[k:k + n] == scaled16(a2[:n], 0.1), f'a2 at 0.1 differs at frame {first_difference(got[k:k + n], scaled16(a2[:n], 0.1)) // 4}'
    return f'a1 at {seen}, then a2 at 0.1 from its first frame for {n // 4} frames'


def test_usb_next_song_at_unity(player, songs, tmp):
    """With mcl-softvolflag 0 the next song starts at unity, though the one before was scaled by an mcl-setvol: stock
    builds each song's filters afresh (0x4235a8 puts the volume in only with the flag)."""
    s, pcm, names = play(player, songs, 'usb-next', tmp/'usb-next')
    a1, a2, got = reference(songs/'a1.flac'), reference(songs/'a2.flac'), b''.join(pcm)
    seen, cut = runs_of_gain(got, a1, (1.0, 0.1))
    assert seen == [1.0, 0.1], f'a1 at {seen}'
    k = got.find(a2[:4000], cut - 4 * 441)
    assert k >= 0 and k % 4 == 0, 'a2 at unity not found after a1'
    n = min(len(got) - k, len(a2))
    assert got[k:k + n] == a2[:n], f'a2 differs from unity at frame {first_difference(got[k:k + n], a2[:n]) // 4}'
    return f'a1 at {seen}, then a2 at unity for {n // 4} frames'


def test_volume_law(player, songs, tmp):
    """Stock's law, read in its mixer_setvolume (0x4287a4): either at 100 is unity; L chooses; 50 0.1, 40 0.08, 255 3.79."""
    want = {(100, 100): (1, 1), (100, 50): (1, 1), (50, 50): (0.1, 0.1), (40, 40): (0.08, 0.08), (255, 255): (3.79, 3.79), (80, 40): (0.64, 0), (0, 0): (0, 0)}
    tool_out = tool(tmp, 'volume_test')
    got = subprocess.run([tool_out], check=True, capture_output=True, text=True).stdout.split('\n')
    have = {tuple(map(int, l.split()[:2])): tuple(map(float, l.split()[2:])) for l in got if l}
    bad = [f'{k}: {have.get(k)} want {v}' for k, v in want.items() if not have.get(k) or any(abs(a - b) > 1e-6 for a, b in zip(have[k], v))]
    assert not bad, bad
    return f'{len(want)} levels'


def test_car_write_slow(player, songs, tmp):
    """The drive of 2026-11-23: one write to the car's PCM took 10.8 s (the trace's wmax=10862ms), across the car's
    pause, a stop and a restart. output.c against alsa_test.c's libasound, in real time:
    - a car that stopped reading with room for 50 frames left, and a stop at the first wait: the write of 100 frames
      never asks the kernel for more than the room, whose blocking write would wait for the rest until its timeout
      (10 s on 5.10; 1.2 s here), so the stop ends it at the next look, not after the timeout;
    - a write that took over a second, in waits for the car or in the write itself, logs one line saying where the
      time went: snd_pcm_wait's and snd_pcm_writei's last answers, errno, the PCM's state and avail; a quick one none."""
    run = subprocess.run([tool(tmp, 'alsa_test', '-DHAVE_ALSA', '-DQ2_CAR_USB'), 'car-slow'], check=True, capture_output=True,
                         text=True, env={**os.environ, 'Q2PLAYER_CAR': str(tmp/'alsa-slow.audio')})
    got = {l.split()[0]: l for l in run.stdout.split('\n') if l}
    said = [l[len('plog W '):] for l in run.stderr.split('\n') if l.startswith('plog W q2player: a car write took')]
    stall = got['car-stall'].split()
    # blocked is what says the kernel was asked for more than the room; the ms only that it did not wait out the
    # model's 1.2 s timeout, which a busy machine cannot stretch the quick path to
    assert stall[1:5] == ['r', '0', 'blocked', '0'] and int(stall[-1]) < 1200, \
        f'a write past the car\'s room waited in the kernel past the stop: {got["car-stall"]}'
    assert len(said) == 2, said
    # the naps are real time, 1.5 s of waits and 1.1 s of writing: the line says at least that, and a busy machine
    # adds to it (1609 ms side by side, 2026-11-23), so there is no upper bound
    form = (r'q2player: a car write took (\d+) ms: 100 frames, (\d+) ms waiting \(snd_pcm_wait 1\), (\d+) ms writing '
            r'\(snd_pcm_writei 100, errno 0\), RUNNING, avail \d+')
    wait, write = said
    w, r = re.fullmatch(form, wait), re.fullmatch(form, write)
    assert w and int(w[1]) >= 1250 and int(w[2]) >= 1250, wait
    assert r and int(r[1]) >= 1100 and int(r[3]) >= 1100, write
    return f'stall: {got["car-stall"]}; {wait}; {write}'


def test_usb_errors(player, songs, tmp):
    """The drive of 2026-11-23: the AB13X earbuds (48 kHz alone) on the host port, `q2player: hw:1,0 is gone` about 5 s
    before /sys/bus/usb/devices/1-1 went, and the line could not say which call found it gone, with which error, or
    whether the earbuds were back as another card. output.c against alsa_test.c's dongle: an underrun or a suspend on
    the first write is prepared and written again, nothing said; -EIO fails the song with the call and errno, the device
    not taken as gone; an open that finds no card, and -ENODEV under two writes, are each said once, naming the call,
    the error and the sound cards there (/proc/asound/cards, Q2PLAYER_CARDS here)."""
    cards = tmp/'cards'
    cards.write_text(' 0 [audiocodec     ]: audiocodec - audiocodec\n                      audiocodec\n'
                     ' 2 [AB13X          ]: USB-Audio - AB13X USB Audio\n                      Generic AB13X USB Audio at usb-1\n')
    run = subprocess.run([tool(tmp, 'alsa_test', '-DHAVE_ALSA', '-DQ2_CAR_USB'), 'usb-errors'], check=True, capture_output=True,
                         text=True, env={**os.environ, 'Q2PLAYER_CARDS': str(cards)})
    got = {l.split()[0]: l.split(' ', 1)[1] for l in run.stdout.split('\n') if l}
    said, case = {}, None
    for l in run.stderr.split('\n'):
        if l.startswith('case '): case = l[5:]
        elif l.startswith('q2player: ') and case: said.setdefault(case, []).append(l)
    want = {'usb-xrun': 'open 1 r 0 0', 'usb-suspend': 'open 1 r 0 0', 'usb-eio': 'open 1 r -1 0', 'usb-gone-open': 'open 0 r 0 0',
            'usb-gone-write': 'open 1 r -3 -3'}
    bad = [f'{k}: {got.get(k)}' for k, v in want.items() if not (got.get(k) or '').startswith(v)]
    assert not bad, bad
    gone = lambda k: [l for l in said.get(k, []) if ' is gone' in l]
    assert not gone('usb-xrun') and not gone('usb-suspend') and not gone('usb-eio'), said
    assert said.get('usb-eio') == ['q2player: test: snd_pcm_writei -5 (Input/output error)'], said.get('usb-eio')
    assert gone('usb-gone-open') == ['q2player: test is gone: snd_pcm_open -2 (No such file or directory); sound cards: 0 audiocodec, 2 AB13X'], said
    assert gone('usb-gone-write') == ['q2player: test is gone: snd_pcm_writei -19 (No such device); sound cards: 0 audiocodec, 2 AB13X'], said
    return f"{gone('usb-gone-open')[0]}; {gone('usb-gone-write')[0]}; {said['usb-eio'][0]}"


def test_alsa_sw_params(player, songs, tmp):
    """output.c against alsa_test.c's libasound: every way sets stock's software parameters (ao_alsa's init,
    0x438d64-0x438dc4: start at a period, stop_threshold and silence_size at the boundary), and off Bluetooth a write to
    a PCM that ran dry first moves appl_ptr to a period past hw_ptr (snd_pcm_forward by the deficit plus a period)."""
    got = subprocess.run([tool(tmp, 'alsa_test', '-DHAVE_ALSA', '-DQ2_CAR_USB')], check=True, capture_output=True, text=True,
                         env={**os.environ, 'Q2PLAYER_CAR': str(tmp/'alsa-car.audio')}).stdout.split('\n')
    lines = {' '.join(l.split()[:3] if l.startswith(('open', 'reread', 'device')) else l.split()[:1]): l for l in got if l}
    for way in range(3):
        assert lines[f'open way {way}'].endswith(f'calls 1 start 1378 stop {0x56220000} silence {0x56220000} boundary {0x56220000}'), lines[f'open way {way}']
    want = {'full': 'r 0 forward -1 avail_reads 1 writes 1', 'ring': 'r 0 forward -1 avail_reads 1 writes 1',
            'dry': 'r 0 forward 6378 avail_reads 1 writes 1', 'dry-usb': 'r 0 forward 45478 avail_reads 1 writes 1'}
    for k, v in want.items():
        assert lines[k] == f'{k} {v}', lines[k]
    close = lines['car-close'].split()
    assert int(close[2]) > 0 and close[4] == '1', f'way 3 closes on a write of silence: {lines["car-close"]}'
    bt = lines['dry-bt'].split()
    assert bt[1:7] == ['r', '0', 'forward', '-1', 'avail_reads', '0'], lines['dry-bt']
    # bluealsa's PCM gone under the stream (SelectCodec's new transport, docs/internals.md#bluetooth-link-monitor):
    # Bluetooth opens it again and writes on, waiting between looks; off Bluetooth, or with no PCM coming back, -1.
    # 'last' is the frames the last write handed the device: 100 unresampled, what the 48 kHz resampler gave else.
    gone = {'bt-gone': 'r 0 0 opens 5 writes 3 sleeps 3 last +', 'bt-gone-xrun': 'r 0 0 opens 2 writes 3 sleeps 0 last +',
            'bt-gone-44k': 'r 0 0 opens 2 writes 3 sleeps 0 last 100', 'bt-lost': 'r -1 -1 opens 51 writes 1 sleeps 49 last -1',
            'dac-gone': 'r -1 0 opens 1 writes 2 sleeps 0 last 100'}
    # a USB DAC unplugged under a write: the output's own answer, which the engine waits on (engine.c output_back)
    assert lines['usb-gone'].split()[1:3] == ['r', '-3'], lines['usb-gone']
    # the dongle of 2026-11-22 (48 kHz S16 stereo alone): a 44.1 kHz song asks for 48 kHz and is resampled; 24 bits at
    # 96 kHz come out at 48 kHz in 16; a mono song on both channels; two writes of 4410 frames are 9600 frames (4410
    # from 96 kHz) less what the resampler holds back; left and right the same tone, within the dither, at its level
    for k in ('usb48', 'usb48-96', 'usb48-mono'):
        got = lines[k].split()
        assert got[1:3] == ['asked', '48000'] and 0 <= 4410 * 2 * 48000 // (96000 if k.endswith('96') else 44100) - int(got[6]) < 64 \
            and int(got[8]) <= 2 and abs(int(got[10]) - 8192) < 100, lines[k]
    for k, v in gone.items():
        got, want = lines[k].split()[1:], v.split()
        assert got[:-1] == want[:-1] and (int(got[-1]) > 0 if want[-1] == '+' else got[-1] == want[-1]), lines[k]
    # Bluetooth's device is defaults.bluealsa.device, which demo rewrites in /mnt/data/asound.conf at each link; alsa-lib
    # re-reads a hook-loaded file only once its global configuration is freed, so every Bluetooth open frees it first,
    # as stock's ao_alsa does (hciplayer 0x438ffc). Off Bluetooth the device never changes and nothing is re-read.
    for way in range(3):
        want = f'reread way {way} opens 2 fresh {2 if way == 1 else 0}'
        assert lines[f'reread way {way}'] == want, (lines[f'reread way {way}'], want)
    # demo rewrites asound.conf only when its own link thread sets a headset up (SaveBlueAlsa), and on 2026-11-19 the
    # car's PCM was up 12 minutes before the file named it, so Bluetooth opens the headset connected last by name
    # (bluealsa's plugin: 00:00:00:00:00:00, the highest Sequence), not the file's address.
    # The car's way (way 3): the BMW asked for USB audio and began to read it 2011 ms later, and 3009 ms on the second
    # drive (2026-11-20); the write
    # that found no room for CAR_WAIT_MS was taken as the car gone, the car file removed and the song sent to Bluetooth
    # in the same millisecond. Now a write waits while the car file is there, a look at it every 250 ms; the file gone
    # ends the way (-2, OUTPUT_CAR_GONE); a stop or a skip (the engine's cut) ends the wait, the frames dropped.
    car = {'car-late': 'r 0 waits 9 ms 2250 writes 1 file 1', 'car-late3': 'r 0 waits 13 ms 3250 writes 1 file 1', 'car-left': 'r -2 waits 3 ms 750 writes 0 file 0',
           'car-cut': 'r 0 waits 3 ms 750 writes 0 file 1'}
    for k, v in car.items():
        assert lines[k] == f'{k} {v}', lines[k]
    # A pause the device cannot do (the car's gadget advertises one its trigger refuses, -EINVAL, and its read position
    # goes back to the ring's start): the frames not yet heard are held, the PCM stopped (the gadget then sends
    # silence) and prepared, and at the resume they are written again, from the first not heard (docs/player.md#pause).
    assert lines['car-pause'] == 'car-pause pauses 2 drops 0 prepares 0 resume writes 0 last -1 first 0', lines['car-pause']
    assert lines['car-pause-fails'] == 'car-pause-fails pauses 1 drops 1 prepares 1 resume writes 1 last 60 first 1040', \
        lines['car-pause-fails']
    devices = {0: 'default', 1: "plug:'bluealsa:DEV=00:00:00:00:00:00,PROFILE=a2dp'", 2: 'hw:1,0'}
    for way, name in devices.items():
        assert lines[f'device way {way}'] == f'device way {way} {name}', lines[f'device way {way}']
    return ('start 1378, stop and silence at the boundary on ways 0-2; a dry PCM forwarded by deficit + period, not on Bluetooth; '
            "bluealsa's PCM gone: reopened on Bluetooth, an error elsewhere; each Bluetooth open reads asound.conf afresh "
            'and plays to the headset connected last')


def test_usb_formats(player, songs, tmp):
    """A USB DAC that takes only S16_LE and S24_3LE: 16 bits as they are, a 24-bit FLAC as its 24 bits exactly
    (S24_3LE, not stock's S16_LE fallback), a float decoder's output rounded to 24 bits."""
    s, pcm, names = play(player, songs, 'usb-formats', tmp/'usbf', env={'Q2PLAYER_FORMATS': 's16,s24'})
    assert [n.split('_', 1)[1] for n in names] == ['44100_2_s16_usb.raw', '44100_2_s24_usb.raw', '44100_2_s24_usb.raw'], names
    a1 = reference(songs/'a1.flac')
    assert a1.startswith(pcm[0]) and len(pcm[0]) > 44100 * 4, 'a1 is not its reference'
    w = reference(songs/'f24.flac', 's32le')
    top = b''.join(w[i + 1:i + 4] for i in range(0, len(w), 4))
    assert pcm[1] == top, f'f24: {len(pcm[1]) // 6} frames, first difference at {first_difference(pcm[1], top)}'
    f = array.array('f', reference(songs/'d.m4a', 'f32le'))
    g = pcm[2]
    got = [int.from_bytes(g[i:i + 3], 'little', signed=True) for i in range(0, len(g), 3)]
    far = max(abs(x - max(-8388608, min(8388607, round(y * 8388608)))) for x, y in zip(got, f))
    assert far <= 1 and len(got) > 44100, f'm4a: {len(got)} samples, at most {far} steps off'
    return f'{len(names)} opens: S16_LE, S24_3LE from S32 exactly, S24_3LE from float within a step'


# A USB DAC of the file sink's own kind (output.c sink_open): the AB13X dongle of 2026-11-22 takes 48 kHz S16_LE stereo
# alone, and output.c resamples a USB DAC's song with libswresample at filter 32, cutoff 0.97 (its USB_FILTER).
USB48 = {'Q2PLAYER_RATES': '48000', 'Q2PLAYER_FORMATS': 's16'}
USB_RS = (32, 0.97)


def s16_far(got, want):
    """The largest difference between two runs of S16 samples, and whether they are as long."""
    a, b = array.array('h'), array.array('h')
    a.frombytes(got), b.frombytes(want)
    return max((abs(x - y) for x, y in zip(a, b)), default=0), len(a) == len(b)


def test_usb_48k_only(player, songs, tmp):
    """The dongle that takes 48 kHz alone: a 44.1 kHz song is resampled to it, where it was refused at every song
    ("hw:1,0 gave 48000 Hz for 44100", play failed, "Not Supported"). The pair through a preloaded change is the whole
    file resampled, in one PCM; that is FFmpeg's resample of the file within the 16-bit dither (TPDF, a step either way),
    with no step at the cut; what the device offers is logged once, not at each song."""
    # each whole: the run waits for the player's exits (a fixed end cut the whole short side by side)
    s, a, an = play(player, songs, 'usb48-pair', tmp/'usb48-pair', speed=4, env=USB48, exits=2)
    _, b, bn = play(player, songs, 'usb48-whole', tmp/'usb48-whole', speed=4, env=USB48, exits=1)
    errors = [l for l in s.lines if 'error}' in l]
    name = '0000_48000_2_s16_usb.raw'
    assert not errors and an == [name] and bn == [name], (an, bn, errors)
    assert a[0] == b[0], f'the pair: {len(a[0]) // 4} frames, the whole {len(b[0]) // 4}, first difference at frame {(first_difference(a[0], b[0]) or 0) // 4}'
    want = ffref(tmp, 'resample', songs/'whole.wav', 48000, 's16', *USB_RS)
    far, same = s16_far(b[0], want)
    assert same and far <= 2, f'{len(b[0]) // 4} frames, the reference {len(want) // 4}; at most {far} steps off'
    def roughness(data, i):
        x = array.array('h'); x.frombytes(data[(i - 200) * 4:(i + 200) * 4])
        left = x[0::2]
        return max(abs(left[j + 1] - 2 * left[j] + left[j - 1]) for j in range(1, len(left) - 1))
    cut = 20 * 48000
    away, here = max(roughness(a[0], cut - 48000), roughness(a[0], cut + 48000)), roughness(a[0], cut)
    assert here <= 2 * away, (here, away)
    offers = (tmp/'usb48-pair/player.log').read_text().count('hw:1,0 offers')
    assert offers == 1, f'the device\'s offer logged {offers} times'
    return f'{len(a[0]) // 4} frames at 48 kHz, the pair the whole file, within {far} steps of the reference; offer logged once'


def test_usb_48k_rates(player, songs, tmp):
    """Into the 48 kHz device, a 1 kHz tone at 44.1 kHz and then, preloaded, a 24-bit 96 kHz song: one PCM at 48 kHz
    S16 across the change of rate and width, each song its own resample within the dither, the tone still 1 kHz."""
    s, pcm, names = play(player, songs, 'usb48-rates', tmp/'usb48-rates', speed=4, env=USB48)
    assert names == ['0000_48000_2_s16_usb.raw'], names
    tone, hires = ffref(tmp, 'resample', songs/'tone20.wav', 48000, 's16', *USB_RS), ffref(tmp, 'resample', songs/'c96.flac', 48000, 's16', *USB_RS)
    far, same = s16_far(pcm[0], tone + hires)
    assert same and far <= 2, f'{len(pcm[0]) // 4} frames, the references {(len(tone) + len(hires)) // 4}; at most {far} steps off'
    x = array.array('h'); x.frombytes(pcm[0][48000 * 4:5 * 48000 * 4])
    left = x[0::2]
    ups = sum(1 for i in range(1, len(left)) if left[i - 1] < 0 <= left[i])
    assert abs(ups / 4 - 1000) <= 0.5, f'the tone at {ups / 4} Hz'
    exits = [l.split()[-1] for l in s.lines if 'exit}' in l or 'error}' in l]
    assert exits == ['{player-status\\exit}'] * 2, exits
    return f'{len(pcm[0]) // 4} frames in one 48 kHz PCM, at most {far} steps off; the tone at {ups / 4} Hz'


def test_usb_48k_soft_volume(player, songs, tmp):
    """The volume in software on that device (mcl-softvolflag 1, 50: 25/256) scales the song before the resample."""
    s, pcm, names = play(player, songs, 'usb48-soft', tmp/'usb48-soft', speed=4, env=USB48)
    assert names == ['0000_48000_2_s16_usb.raw'], names
    want = ffref(tmp, 'resample', songs/'tone20.wav', 48000, 's16', *USB_RS)
    rms = lambda b: math.sqrt(sum(v * v for v in array.array('h', b[48000 * 4:4 * 48000 * 4])) / (3 * 48000 * 2))
    ratio = rms(pcm[0]) / rms(want)
    assert abs(ratio - 25 / 256) < 0.002, f'level {ratio:.4f}, not 25/256'
    return f'level {ratio:.4f} of the reference (25/256 = {25 / 256:.4f})'


def test_usb_capable_exact(player, songs, tmp):
    """A USB DAC that takes every rate gets each song at its own rate and width, bit for bit: no resampler."""
    rates = '44100,48000,88200,96000,176400,192000,352800,384000'
    s, pcm, names = play(player, songs, 'usb-capable', tmp/'usb-capable', speed=4, env={'Q2PLAYER_RATES': rates})
    assert names == ['0000_44100_2_s16_usb.raw', '0001_96000_2_s32_usb.raw'], names
    assert reference(songs/'a1.flac').startswith(pcm[0]) and len(pcm[0]) > 44100 * 4, 'a1 is not its reference'
    c96 = reference(songs/'c96.flac', 's32le')
    assert pcm[1] == c96, f'c96: {len(pcm[1]) // 8} frames, the reference {len(c96) // 8}, first difference at {first_difference(pcm[1], c96)}'
    return f'44.1 kHz S16 and 96 kHz S32 as they are, {len(pcm[1]) // 8} frames of the second'


def test_usb_gone(player, songs, tmp):
    """The USB DAC unplugged mid-song, a Next while it is out, then plugged in again: no song is reported as not
    playing (each was "Not Supported" on 2026-11-22, the queue run through); the output's going is logged once; the
    song chosen while it was out plays from its first frame when it is back."""
    s, pcm, names = play(player, songs, 'usb-gone', tmp/'usb-gone', speed=1, env={'Q2PLAYER_GONE': 'usb.gone'})
    ev = [l.split()[-1] for l in s.lines if 'exit}' in l or 'error}' in l]
    assert ev == ['{player-nextstatus\\exit}', '{player-stopstatus\\exit}'], ev
    gone = (tmp/'usb-gone/player.log').read_text().count('hw:1,0 is gone')
    assert gone == 1, f'gone logged {gone} times'
    assert len(names) == 2 and reference(songs/'a1.flac').startswith(pcm[0]), names
    a2 = reference(songs/'a2.flac')
    assert len(pcm[1]) > 44100 * 4 and a2.startswith(pcm[1]), f'a2 at its return: {len(pcm[1]) // 4} frames, first difference {first_difference(pcm[1], a2[:len(pcm[1])])}'
    return f'events {ev}; a2 {len(pcm[1]) // 4} frames from its start once back'


def test_usb_gone_and_back(player, songs, tmp):
    """Out and back within a song: the song goes on from what was last heard (the half second the device held is
    played again), in a PCM opened again, with no error."""
    s, pcm, names = play(player, songs, 'usb-gone-back', tmp/'usb-gone-back', speed=1, env={'Q2PLAYER_GONE': 'usb.gone'})
    ev = [l.split()[-1] for l in s.lines if 'exit}' in l or 'error}' in l]
    assert ev == ['{player-stopstatus\\exit}'], ev
    a1 = reference(songs/'a1.flac')
    assert len(names) == 2 and a1.startswith(pcm[0]), names
    k = a1.find(pcm[1][:4000])
    assert k % 4 == 0 and len(pcm[0]) - 0.6 * 44100 * 4 <= k <= len(pcm[0]), f'went on at frame {k // 4}, {len(pcm[0]) // 4} written before'
    assert a1[k:k + len(pcm[1])] == pcm[1], 'the rest is not a1 from there'
    return f'{len(pcm[0]) // 4} frames, then on from frame {k // 4}'


def check_bt_split(player, songs, tmp, pair, whole, source, rate, frames):
    """Bluetooth: a tone cut in two and played through a preloaded change comes out as the whole file resampled, in
    one PCM at the transport's rate, and that is FFmpeg 7.1.5's libswresample with lavcresample's settings, its tail
    flushed. docs/internals.md#bluetooth-resampler"""
    env = {'Q2PLAYER_BT_RATE': str(rate)}
    # each whole: the run waits for the player's exits (a fixed end cut the pair short side by side)
    _, a, an = play(player, songs, pair, tmp/f'{pair}-{rate}', speed=4, env=env, exits=2)
    _, b, bn = play(player, songs, whole, tmp/f'{whole}-{rate}', speed=4, env=env, exits=1)
    name = f'0000_{rate}_2_s16_bt.raw'
    assert an == [name] and bn == [name], (an, bn)
    want = ffref(tmp, 'resample', songs/source, rate, 's16')
    assert b[0] == want, f'{whole}: {len(b[0]) // 4} frames, the reference {len(want) // 4}, first difference at {(first_difference(b[0], want) or 0) // 4}'
    assert a[0] == b[0], f'{pair}: {len(a[0]) // 4} frames, the whole {len(b[0]) // 4}, first difference at frame {(first_difference(a[0], b[0]) or 0) // 4}'
    assert len(a[0]) // 4 == frames, f'{len(a[0]) // 4} frames, not {frames}'
    return f'{frames} frames at {rate} Hz, the pair the whole file resampled'


def test_bt_resample_44_48(player, songs, tmp): return check_bt_split(player, songs, tmp, 'bt-pair', 'bt-whole', 'whole.wav', 48000, 1920000)
def test_bt_resample_96_48(player, songs, tmp): return check_bt_split(player, songs, tmp, 'bt-pair96', 'bt-whole96', 'h96.flac', 48000, 960000)
def test_bt_resample_44_96(player, songs, tmp): return check_bt_split(player, songs, tmp, 'bt-pair', 'bt-whole', 'whole.wav', 96000, 3840000)


@functools.cache
def car_player():
    """The player built with Car Audio's output (Q2_CAR_USB, as every image's is: docs/iphone-mode.md#car-audio)."""
    return subprocess.run([sys.executable, ROOT/'tools/player.py', 'build', 'host', 'car'], check=True, capture_output=True,
                          text=True).stdout.strip()


def car_steps(songs, car, at_on=None, at_off=None, flag=0):
    """Bluetooth at half volume (the car plays at unity whatever it is), a1 then c96; the car's file written at at_on
    and removed at at_off (None: there from the start, and to the end)."""
    on = lambda s: put(car, 'hw:CARD=UAC1Gadget,0\n44100\n')
    off = lambda s: car.unlink()
    steps = [(0.0, '{mcl-dsdmode\\0}'), (0.0, '{mcl-replaygain\\0}'), (0.1, '{mcl-setoutputway\\1}'),
             (0.1, fakedemo.vol(50, 50)), (1.0, fakedemo.song('set-songurl', f'{songs}/a1.flac', flag=flag))]
    if at_on is not None: steps.append((at_on, on))
    if at_off is not None: steps.append((at_off, off))
    return steps


def car_play(songs, tmp, name, steps, player, car, speed=4, env=None):
    s = fakedemo.Session(tmp/name)
    s.music = songs
    # the gadget's card takes S16_LE alone (q2-iap-probe.sh: p_ssize 2)
    s.run(steps, fakedemo.launch_host(player, str(songs), speed, {'Q2PLAYER_CAR': str(car), 'Q2PLAYER_FORMATS': 's16', **(env or {})}),
          speed)
    pcm = sorted((tmp/name/'pcm').glob('*.raw'))
    return [p.name for p in pcm], [p.read_bytes() for p in pcm], (tmp/name/'player.log').read_text()


def test_car_way(player, songs, tmp):
    """While the car wants its audio (the probe's car file), demo's Bluetooth way is the car's: one PCM at the car's
    44.1 kHz, 16-bit, at unity whatever Bluetooth's volume (the car's knob is its volume), a1 bit for bit, and a 96
    kHz song after it resampled into the same PCM, which stays open, so the car's stream never stops."""
    cp, car = car_player(), tmp/'car-on.audio'
    car.write_text('hw:CARD=UAC1Gadget,0\n44100\n')
    # the stop once c96 is heard 3 s in, after a1's end: at a fixed time a starved player had not finished a1 (0 of
    # 20 runs passed with it stopped 300 ms a second), its car PCM a1's exact prefix, and 4 s of the session's clock
    # after the exit held under 2 s of c96 (1 of 20)
    steps = car_steps(songs, car, flag=1) + [(16.0, fakedemo.gapless(f'{songs}/c96.flac', flag=1)),
                                             (16.0, fakedemo.after_exits(1, heard=3)), (16.0, '{mcl-stop\\null}'), (17.0, None)]
    names, pcm, log = car_play(songs, tmp, 'car-on', steps, cp, car, speed=8)
    assert [n.split('_', 1)[1] for n in names] == ['44100_2_s16_car.raw'], names
    a1 = reference(songs/'a1.flac')
    assert pcm[0][:len(a1)] == a1, f'a1 into the car is the reference at unity; first difference at frame {(first_difference(pcm[0], a1) or 0) // 4}'
    assert len(pcm[0]) > len(a1) + 44100 * 4 * 2, 'the 96 kHz song after it, gapless, resampled into the same PCM'
    return f'{len(pcm[0]) // 4} frames into one car PCM, a1 exact'


def test_car_way_follows_the_car(player, songs, tmp):
    """The car asks for its audio while a1 plays over Bluetooth, then stops: a1 moves to the car's PCM where it is, then
    back to Bluetooth's, three PCMs in turn, the car's a stretch of a1 itself, unbroken."""
    cp, car = car_player(), tmp/'car-switch.audio'
    # each move waited for in the player's log, then 2 s of the song where it went: at fixed times a starved player
    # had not moved back to Bluetooth by the stop (two PCMs, side by side)
    steps = sorted(car_steps(songs, car, at_on=3.0) + [(3.0, fakedemo.logged('output way 3 now', 2.0)), (3.0, lambda s: car.unlink()),
                                                       (3.0, fakedemo.logged('output way 1 now', 2.0)), (3.0, '{mcl-stop\\null}'),
                                                       (3.0, None)], key=lambda st: st[0])
    names, pcm, log = car_play(songs, tmp, 'car-switch', steps, cp, car)
    assert [n.split('_', 1)[1] for n in names] == ['48000_2_s16_bt.raw', '44100_2_s16_car.raw', '48000_2_s16_bt.raw'], names
    a1, mid = reference(songs/'a1.flac'), pcm[1]
    assert len(mid) > 44100 * 4 and mid[:len(mid) - len(mid) % 4] in a1, 'the car heard a1 itself, one stretch of it'
    assert 'output way 3 now' in log and 'output way 1 now' in log, log[-800:]
    return f'Bluetooth, {len(mid) // 4} frames to the car, Bluetooth'


def test_car_release_while_paused(player, songs, tmp):
    """docs/player.md#releasing-the-car: a1 paused on the car's way holds its PCM open, and the gadget's unbind waits for
    that close while it holds the UDC's lock. mcl-dropcar on the socket (mcl-carrelease, its other name, and SIGUSR2
    from a script the same) wakes the paused thread and closes the PCM within 0.4 s, the song still paused; the Play
    opens a PCM on the way the car's file says then, and the song goes on from the frame last heard, not from where
    the decode had got to. Kept, the car's file gives a second car PCM; removed (the cable out), a Bluetooth one. The
    car's PCM (the file sink's ..._car.raw) is open in the player while paused and closed after the release, read from
    /proc/PID/fd: the firmware waits for the gadget's PCM to read closed before it unbinds (patch/q2car.sh). The player
    runs without q2-heavy's preload here, which makes a process unreadable in /proc."""
    said = []

    def car_fds(s):  # the player's open files that are a car PCM
        fds = []
        for fd in pathlib.Path(f'/proc/{s.proc.pid}/fd').iterdir():
            try:
                if os.readlink(fd).endswith('_car.raw'): fds.append(fd.name)
            except OSError: pass
        return fds
    for how, kept in (('mcl-dropcar', True), ('mcl-carrelease', False), ('signal', False)):
        cp, car = car_player(), tmp/f'car-rel-{how}.audio'
        car.write_text('hw:CARD=UAC1Gadget,0\n44100\n')
        ask = f'{{{how}\\null}}' if how != 'signal' else (lambda s: os.kill(s.proc.pid, signal.SIGUSR2))
        held, after = [], []
        steps = car_steps(songs, car) + [(3.0, '{mcl-pause\\null}'), (4.5, lambda s: held.append(car_fds(s))), (5.0, ask),
                                         (5.4, lambda s: after.append(car_fds(s))), (6.0, '{mcl-play\\null}'),
                                         (8.0, '{mcl-stop\\null}'), (9.0, None)]
        if not kept: steps.insert(-3, (5.5, lambda s: car.unlink()))
        names, pcm, log = car_play(songs, tmp, f'car-rel-{how}', steps, cp, car, speed=1, env={'LD_PRELOAD': ''})
        assert held == [held[0]] and held[0] and after == [[]], f'{how}: car PCM fds paused {held}, 0.4 s after the release {after}'
        up = lambda text: int(re.search(rf'^\S+ (\d+) \w player {re.escape(text)}', log, re.M)[1])
        took = up("q2player: the car's PCM released") - up('q2player: mcl-pause')
        assert 1950 <= took <= 2400, f'{how}: released {took} ms after the pause, the command 2000 ms after it'
        assert up("q2player: the car's PCM released") < up('q2player: mcl-play'), f'{how}: not while paused'
        want = ['44100_2_s16_car.raw', '44100_2_s16_car.raw' if kept else '48000_2_s16_bt.raw']
        assert [n.split('_', 1)[1] for n in names] == want, (how, names)
        a1 = reference(songs/'a1.flac')
        if kept:  # the same format: where the second PCM starts in a1 is where the song went on
            at = a1.find(pcm[1][:4410 * 4]) // 4
            unheard = len(pcm[0]) // 4 - at
            assert at > 0 and 2205 <= unheard <= 26460, f'{how}: went on at frame {at}, {unheard} frames before the decode had got'
            said.append(f'{how}: released {took - 2000} ms after it, went on {unheard * 1000 // 44100} ms back, at the frame heard')
        else:
            said.append(f'{how}: released {took - 2000} ms after it, on Bluetooth at the Play')
    return '; '.join(said)


def test_car_way_needs_the_car_build(player, songs, tmp):
    """A player built without Q2_CAR_USB (the host's default) has no car way: the car's file changes nothing."""
    car = tmp/'car-release.audio'
    car.write_text('hw:CARD=UAC1Gadget,0\n44100\n')
    steps = car_steps(songs, car) + [(3.0, '{mcl-stop\\null}'), (4.0, None)]
    names, _, _ = car_play(songs, tmp, 'car-release', steps, player, car)
    assert [n.split('_', 1)[1] for n in names] == ['48000_2_s16_bt.raw'], names
    return 'Bluetooth, as without a car'


def bt_fds(s):
    """The player's open files that are a Bluetooth PCM (the file sink's ..._bt.raw)."""
    fds = []
    for fd in pathlib.Path(f'/proc/{s.proc.pid}/fd').iterdir():
        try:
            if os.readlink(fd).endswith('_bt.raw'): fds.append(fd.name)
        except OSError: pass
    return fds


def test_bt_pause_releases_the_pcm(player, songs, tmp):
    """docs/internals.md#bluetooth-pause: a song paused on Bluetooth keeps bluealsa's PCM for BT_PAUSE_MS (3 s here,
    Q2PLAYER_BT_PAUSE_MS), then closes it, so bluealsa's keep-alive lets the A2DP stream stop. A PCM held paused keeps
    the stream started with nothing in it for the whole pause, and AirPods held so for minutes (pauses of 2.5 and 6.4
    minutes in a device log, no AVDTP suspend in either) play nothing at the Play until a reconnect. A pause shorter
    than that keeps the PCM; after it, a click opens a PCM for itself (the click in it) that is closed again
    BT_PAUSE_MS after the click's hold, and the Play opens one more, the song on from the frame heard. The Bluetooth
    PCM (the file sink's ..._bt.raw) is read from /proc/PID/fd, the player run without q2-heavy's preload."""
    seen = {}
    look = lambda name: (lambda s: seen.setdefault(name, bt_fds(s)))
    steps = [(0.0, '{mcl-dsdmode\\0}'), (0.0, '{mcl-replaygain\\0}'), (0.1, '{mcl-setoutputway\\1}'),
             (0.1, fakedemo.vol(100, 100)), (1.0, fakedemo.song('set-songurl', f'{songs}/whole48.wav')),
             (2.0, '{mcl-pause\\null}'), (2.5, look('short pause')), (3.0, '{mcl-play\\null}'), (4.0, '{mcl-pause\\null}'),
             (6.5, look('2.5 s paused')), (7.6, look('3.6 s paused')), (8.0, '{mcl-click\\wheel}'), (8.6, look('click')),
             (14.6, look('after the click')), (15.0, '{mcl-play\\null}'), (15.6, look('playing again')),
             (17.0, '{mcl-stop\\null}'), (18.0, None)]
    s = fakedemo.Session(tmp/'bt-pause')
    s.music = songs
    s.run(steps, fakedemo.launch_host(player, str(songs), 1, {'LD_PRELOAD': '', 'Q2PLAYER_BT_PAUSE_MS': '3000'}), 1)
    open_ = {k: len(v) for k, v in seen.items()}
    assert open_ == {'short pause': 1, '2.5 s paused': 1, '3.6 s paused': 0, 'click': 1, 'after the click': 0,
                     'playing again': 1}, f'Bluetooth PCMs open: {open_}'
    names = sorted(p.name for p in (tmp/'bt-pause'/'pcm').glob('*.raw'))
    pcm = [(tmp/'bt-pause'/'pcm'/n).read_bytes() for n in names]
    assert [n.split('_', 1)[1] for n in names] == ['48000_2_s16_bt.raw'] * 3, names
    ref = reference(songs/'whole48.wav')
    assert pcm[0] == ref[:len(pcm[0])], 'the song up to the long pause, the short one in it, is not the song'
    click = array.array('h', pcm[1])
    firsts = wheel_clicks(click, 48000)
    assert len(firsts) == 1 and firsts[0] < 480, f'click at {firsts} in the PCM opened for it'
    at = ref.find(pcm[2][:4800 * 4]) // 4
    unheard = len(pcm[0]) // 4 - at
    assert at > 0 and 2400 <= unheard <= 28800, f'went on at frame {at}, {unheard} frames before the frame written last'
    assert pcm[2] == ref[4 * at:4 * at + len(pcm[2])], 'the song does not go on from the frame heard'
    log = (tmp/'bt-pause'/'player.log').read_text()
    stamp = lambda text: [int(m) for m in re.findall(rf'^\S+ (\d+) \w player {re.escape(text)}', log, re.M)]
    closed, opened = stamp('q2player: paused on Bluetooth for 3000 ms, its PCM closed'), stamp("q2player: Bluetooth's PCM opened again")
    pauses = stamp('q2player: mcl-pause')
    assert len(closed) == 2 and len(opened) == 2 and len(pauses) == 2, (closed, opened, pauses)
    assert 2950 <= closed[0] - pauses[1] <= 3400, f'closed {closed[0] - pauses[1]} ms after the pause'
    assert re.search(r"Bluetooth's PCM opened again for a click, \d+ ms after its close; the song held at frame \d+", log) \
        and re.search(r"Bluetooth's PCM opened again for the play, \d+ ms after its close; the song on from frame \d+", log), log[-1500:]
    return (f'kept through a 1 s pause, closed {closed[0] - pauses[1]} ms into a long one; a click in a PCM of its own, '
            f'closed again; the Play went on {unheard * 1000 // 48000} ms back, at the frame heard')


BT_PLAY_S = int(os.environ.get('Q2TEST_BT_PLAY_S', '10'))  # how long the click-and-play test listens after the Play


def test_bt_play_and_click_after_the_release(player, songs, tmp):
    """docs/internals.md#bluetooth-pause: demo sends a key click with the Play, so after a long pause on Bluetooth (the
    PCM closed BT_PAUSE_MS in, 3 s here) the click and the Play reach the player together, in either order. A click
    first opens the PCM paused for itself and its session ends at the Play; the song then goes on in that PCM, and
    the log says so (q2player: the play goes on in the Bluetooth PCM a click opened ...); the Play first opens it for
    the play and the click goes into the song. In each order, for BT_PLAY_S after the Play (10 s: past the pause's
    3 s and the click's 3 s hold, the two timers that close a PCM), the PCM stays open, the song's frames reach it
    at its rate, from the frame held at the close, and no close is logged after the Play."""
    orders = {'click, then the play 50 ms on': [(6.0, '{mcl-click\\wheel}'), (6.05, '{mcl-play\\null}')],
              'click and play in one read': [(6.0, '{mcl-click\\wheel}{mcl-play\\null}')],
              'play and click in one read': [(6.0, '{mcl-play\\null}{mcl-click\\wheel}')]}
    ref, said = reference(songs/'whole48.wav'), []
    for i, (name, act) in enumerate(orders.items()):
        out, looks = tmp/f'bt-click-play{i}', []
        look = lambda s: looks.append((len(bt_fds(s)), sum(p.stat().st_size for p in (out/'pcm').glob('*_bt.raw')) // 4))
        steps = [(0.0, '{mcl-dsdmode\\0}'), (0.0, '{mcl-replaygain\\0}'), (0.1, '{mcl-setoutputway\\1}'),
                 (0.1, fakedemo.vol(100, 100)), (1.0, fakedemo.song('set-songurl', f'{songs}/whole48.wav')),
                 (2.0, '{mcl-pause\\null}'), *act] + [(6.5 + k, look) for k in range(BT_PLAY_S + 1)] + \
                [(7.0 + BT_PLAY_S, '{mcl-stop\\null}'), (8.0 + BT_PLAY_S, None)]
        s = fakedemo.Session(out)
        s.music = songs
        s.run(steps, fakedemo.launch_host(player, str(songs), 1, {'LD_PRELOAD': '', 'Q2PLAYER_BT_PAUSE_MS': '3000'}), 1)
        log = (out/'player.log').read_text()
        assert all(n == 1 for n, _ in looks), f'{name}: Bluetooth PCMs open each second after the Play {[n for n, _ in looks]}'
        frames = looks[-1][1] - looks[0][1]
        assert frames >= 0.9 * BT_PLAY_S * 48000, f'{name}: {frames} frames written in the {BT_PLAY_S} s after the Play'
        held = int(re.search(r'paused on Bluetooth for 3000 ms, its PCM closed .* the song held at frame (\d+)', log)[1])
        last = (sorted((out/'pcm').glob('*_bt.raw'))[-1]).read_bytes()
        # from 0.2 s on: a click taken into the song (the Play first) is mixed into its first frames
        at = last.find(ref[4 * (held + 9600):4 * (held + 14400)])
        assert at >= 9600 * 4 and last[at:] == ref[4 * (held + 9600):4 * (held + 9600) + len(last) - at], \
            f'{name}: the song not on from frame {held}'
        after = log[log.index('q2player: mcl-play'):]
        assert 'its PCM closed' not in after, f'{name}: a close after the Play: {after[:800]}'
        said_play = re.search(r"q2player: (Bluetooth's PCM opened again for the play, \d+ ms after its close|the play goes on in the Bluetooth PCM a "
                              r"click opened, \d+ ms after it opened); the song on from frame (\d+)", after)
        assert said_play and int(said_play[2]) == held, f'{name}: the log does not say the play went on at {held}: {after[:800]}'
        said.append(f'{name}: {frames} frames in {BT_PLAY_S} s, ' + ('a click\'s PCM' if 'click' in said_play[1] else 'opened for the play'))
    return '; '.join(said)


def test_bt_song_stalled_in_its_download(player, songs, tmp):
    """A server song on Bluetooth whose download stops (player/grow.c): 4 s of a1 on the card, its .stream saying get
    twice a second with no more bytes, the rest landing at 14 s. While it waits the song is playing as demo sees it, the
    Bluetooth PCM stays open with nothing more written to it (on a Q2, the PCM's own silence goes to the headset),
    the position stands where the bytes ran out, /tmp/q2player.wait names it, and the unified log says so, once at the
    wait's start and once at its end (q2player: waiting for the song's download ...): the BT_PAUSE_MS close is a
    pause's, not a wait's. Once the rest lands the song plays on to its end, whole."""
    out = tmp/'bt-stall'
    card, status = out/'mnt/mmc/.foo', out/'tmp/q2-foo.stream'
    card.mkdir(parents=True, exist_ok=True)
    whole, path = (songs/'a1.flac').read_bytes(), card/'grow.flac'
    head, landed = len(whole) * 4 // 20, []
    path.write_bytes(whole[:head])
    line = lambda state, n: put(status, f'#Q2STREAM/1\tseq=1\n{state}\t{n}\t{len(whole)}\t20000\t/mnt/mmc/.foo/grow.flac\n')
    beat = lambda s: landed or line('get', head)
    def land(s):
        with open(path, 'ab') as f: f.write(whole[head:])
        landed.append(1), line('done', len(whole))
    looks = []
    look = lambda s: looks.append((len(bt_fds(s)), sum(p.stat().st_size for p in (out/'pcm').glob('*_bt.raw')) // 4,
                                   (out/'tmp/q2player.wait').exists(), s.positions[-1][1] if s.positions else None))
    steps = sorted([(0.0, '{mcl-dsdmode\\0}'), (0.0, '{mcl-replaygain\\0}'), (0.1, '{mcl-setoutputway\\1}'),
                    (0.1, fakedemo.vol(100, 100)), (1.0, fakedemo.song('set-songurl', str(path)))]
                   + [(k / 2, beat) for k in range(2, 28)] + [(6.0 + k, look) for k in range(7)] + [(14.0, land)],
                   key=lambda st: st[0]) + [(34.0, None)]
    s = fakedemo.Session(out)
    s.music = songs
    s.wait_exits = 1
    line('get', head)
    s.run(steps, fakedemo.launch_host(player, str(songs), 1, {'LD_PRELOAD': '', 'Q2PLAYER_BT_PAUSE_MS': '3000'}), 1)
    assert all(n == 1 for n, _, _, _ in looks), f'Bluetooth PCMs open while it waits: {[n for n, _, _, _ in looks]}'
    assert len({f for _, f, _, _ in looks}) == 1, f'frames written while it waits: {[f for _, f, _, _ in looks]}'
    assert all(w for _, _, w, _ in looks) and len({p for _, _, _, p in looks}) == 1, f'wait file, position: {looks}'
    log = (out/'player.log').read_text()
    waits = re.findall(r"q2player: waiting for the song's download: byte \d+ of (\d+)", log)
    ends = re.findall(r"q2player: the wait for the song's download ended after (\d+) ms", log)
    assert len(waits) == 1 and int(waits[0]) == len(whole) and len(ends) == 1 and int(ends[0]) >= 7000, (waits, ends, log[-1500:])
    assert 'its PCM closed' not in log, log[-1500:]
    pcm = b''.join(p.read_bytes() for p in sorted((out/'pcm').glob('*.raw')))
    assert abs(len(pcm) // 4 - 20 * 48000) < 4800, f'{len(pcm) // 4} frames, a1 is 20 s at 48 kHz'
    exits = [l.split()[-1] for l in s.lines if 'exit}' in l or 'error}' in l]
    assert exits == ['{player-status\\exit}'], exits
    return (f'waited {int(ends[0]) / 1000:.1f} s at {looks[0][1]} frames, position {looks[0][3]} s, the PCM open; '
            f'logged its start and end, then the song whole')


def tones(d, *rates, secs=5):
    """A 1 kHz sine at each rate (t44100.flac and so on), so that a stretch of any PCM says the speed it plays at."""
    d.mkdir(parents=True, exist_ok=True)
    for r in rates:
        if not (d/f't{r}.flac').exists():
            music.ff('-f', 'lavfi', '-i', f'sine=f=1000:r={r}:d={secs}', '-af', 'volume=0.5,aformat=channel_layouts=stereo',
                     '-c:a', 'flac', '-sample_fmt', 's16', d/f't{r}.flac')
    return [d/f't{r}.flac' for r in rates]


def tone_hz(pcm, rate, window=0.1):
    """The tone's frequency in each window of a car PCM (S16 stereo) heard at its file's rate, from the left channel's
    zero crossings; windows with silence in them (a song's edge, the pad before a rate change) are left out."""
    left = array.array('h', pcm[:len(pcm) - len(pcm) % 4])[::2]
    n, out = int(rate * window), []
    for at in range(0, len(left) - n, n):
        w = left[at:at + n]
        if max(map(abs, w)) < 1000 or any(not any(w[i:i + 8]) for i in range(0, n - 8, 8)): continue
        signs = [x > 0 for x in w if x]
        out.append(sum(a != b for a, b in zip(signs, signs[1:])) / 2 / window)
    return out


class Car:
    """The car and its gadget's module in a host test: the player's rate file (Q2PLAYER_CAR_RATE) read every 5 ms
    and, after delay_ms, the rate asked for written to p_rate (Q2PLAYER_P_RATE), as q2-iap's DA01 and the car's
    SET_CUR do; with follow off the car never sets it. set() is the car setting a rate by itself."""
    def __init__(self, d, follow=True, delay_ms=30, start=44100):
        import threading
        d.mkdir(parents=True, exist_ok=True)
        self.asked, self.p_rate, self.follow, self.delay = d/'q2-iap.rate', d/'p_rate', follow, delay_ms / 1000
        put(self.p_rate, f'{start}\n')
        self.asked.unlink(missing_ok=True)
        self.seen, self.stop = [], threading.Event()
        threading.Thread(target=self.run, daemon=True).start()

    def env(self): return {'Q2PLAYER_CAR_RATE': str(self.asked), 'Q2PLAYER_P_RATE': str(self.p_rate)}

    def set(self, rate): put(self.p_rate, f'{rate}\n')

    def run(self):
        import time
        while not self.stop.wait(0.005):
            try: want = int(self.asked.read_text())
            except (OSError, ValueError): continue
            if self.seen and self.seen[-1] == want: continue
            self.seen.append(want)
            if self.follow: time.sleep(self.delay); self.set(want)


def car_rates(player, songs, tmp, name, files, car, extra=()):
    """files played gapless one after another into the car's way at four times speed (each 5 s, its preload 2 s
    before its end); (pcm file names, their bytes, the log)."""
    cf = tmp/f'{name}.audio'
    cf.write_text('hw:CARD=UAC1Gadget,0\n44100\n')
    steps = [(0.0, '{mcl-dsdmode\\0}'), (0.0, '{mcl-replaygain\\0}'), (0.1, '{mcl-setoutputway\\1}'),
             (1.0, fakedemo.song('set-songurl', str(files[0]), flag=1))]
    steps += [(1.0 + 5 * i - 2, fakedemo.gapless(str(f), flag=1)) for i, f in enumerate(files) if i]
    steps = sorted(steps + list(extra), key=lambda st: st[0]) + [(1.0 + 5 * len(files) + 3, '{mcl-stop\\null}'),
                                                                 (1.0 + 5 * len(files) + 4, None)]
    s = fakedemo.Session(tmp/name)
    s.music = songs
    env = {'Q2PLAYER_CAR': str(cf), 'Q2PLAYER_FORMATS': 's16', **car.env()}
    try: s.run(steps, fakedemo.launch_host(player, str(songs), 4, env), 4)
    finally: car.stop.set()
    pcm = sorted((tmp/name/'pcm').glob('*.raw'))
    return [p.name.split('_', 1)[1] for p in pcm], [p.read_bytes() for p in pcm], (tmp/name/'player.log').read_text()


def at_speed(names, pcm):
    """Every window of every car PCM a 1 kHz tone at the PCM's own rate: nothing played off speed."""
    for name, b in zip(names, pcm):
        hz = tone_hz(b, int(name.split('_')[0]))
        assert hz and all(abs(h - 1000) <= 15 for h in hz), f'{name}: {[round(h) for h in hz if abs(h - 1000) > 15][:8]} of {len(hz)} windows'
    return sum(len(tone_hz(b, int(n.split('_')[0]))) for n, b in zip(names, pcm))


RATE_LINE = re.compile(r' I player car rate (\d+)->(\d+) ([^:]+): wait=(\d+)ms(?: gap=(\d+)ms)?$', re.M)


def test_car_rate_per_song(player, songs, tmp):
    """Per-song rate (docs/iphone-mode.md#per-song-rate): 44.1, 48, 96 then 44.1 kHz songs into the car's way, the car
    following each rate asked for 30 ms later. The player asks for 48 kHz at the 48 kHz song (the 96 kHz one maps to
    it, so no change there) and 44.1 kHz at the last; at each change it plays the last song's tail out, waits for
    the car's p_rate, and opens the PCM again at it: three PCMs, 44.1, 48 and 44.1 kHz, every stretch of each at
    its own speed, and a line for each change with the wait and the gap."""
    cp, car = car_player(), Car(tmp/'rate-follow')
    files = tones(tmp/'tones', 44100, 48000, 96000) + tones(tmp/'tones2', 44100)
    names, pcm, log = car_rates(cp, songs, tmp, 'rate-follow', files, car)
    assert names == ['44100_2_s16_car.raw', '48000_2_s16_car.raw', '44100_2_s16_car.raw'], (names, log[-1500:])
    assert car.seen == [44100, 48000, 44100], car.seen
    # the second PCM carries two songs (48 and 96 kHz, the latter resampled), the others one each
    assert [round(len(b) / 4 / int(n.split('_')[0])) for n, b in zip(names, pcm)] == [5, 10, 5], [len(b) for b in pcm]
    windows = at_speed(names, pcm)
    lines = RATE_LINE.findall(log)
    assert [(a, b, what) for a, b, what, *_ in lines] == [('44100', '48000', 'followed'), ('48000', '44100', 'followed')], lines
    assert all(int(w) < 400 and int(g) < 1000 for *_, w, g in lines), lines
    return f'{names}, {windows} windows at 1 kHz; switches {[(w + "ms", g + "ms") for *_, w, g in lines]}'


def test_car_rate_not_followed(player, songs, tmp):
    """A car that never sets the rate asked for: the 48 kHz song waits CAR_FOLLOW_MS for it once, then is resampled
    into the car's 44.1 kHz PCM, which stays open; the 44.1 kHz song after it asks for nothing new. One PCM, all of
    it at speed."""
    cp, car = car_player(), Car(tmp/'rate-stay', follow=False)
    files = tones(tmp/'tones', 44100, 48000) + tones(tmp/'tones2', 44100)
    names, pcm, log = car_rates(cp, songs, tmp, 'rate-stay', files, car)
    assert names == ['44100_2_s16_car.raw'], (names, log[-1500:])
    assert car.seen == [44100, 48000, 44100], car.seen  # each song's rate asked for, though the PCM stays at 44.1
    lines = RATE_LINE.findall(log)
    assert [(a, b, what) for a, b, what, *_ in lines] == [('44100', '48000', 'not followed, resampled')], lines
    assert 480 <= int(lines[0][3]) < 900, lines
    windows = at_speed(names, pcm)
    return f'one PCM, {windows} windows at 1 kHz; waited {lines[0][3]} ms'


def test_car_rate_set_by_the_car(player, songs, tmp):
    """The car sets 48 kHz by itself while a 44.1 kHz song plays: the PCM at 44.1 kHz would now play fast, so the
    player sees p_rate differ, logs it and opens the PCM again at 48 kHz, the song resampled to it: never off speed
    after it. The next song, at 44.1 kHz too, is the rate last asked for, which the car went over: it is not waited
    for again but resampled into the 48 kHz PCM, which stays open."""
    cp, car = car_player(), Car(tmp/'rate-car')
    files = tones(tmp/'tones', 44100) + tones(tmp/'tones2', 44100)
    names, pcm, log = car_rates(cp, songs, tmp, 'rate-car', files, car, extra=[(3.0, lambda s: car.set(48000))])
    assert names == ['44100_2_s16_car.raw', '48000_2_s16_car.raw'], (names, log[-1500:])
    lines = RATE_LINE.findall(log)
    assert [(a, b, what) for a, b, what, *_ in lines] == [('44100', '48000', 'set by the car, reopened')], lines
    assert round(len(pcm[1]) / 4 / 48000) >= 7, 'the rest of the first song and all of the second at 48 kHz'
    windows = at_speed(names[1:], pcm[1:])
    return f'{names}, {windows} windows after the car\'s change at 1 kHz'


CAR_LINE = re.compile(r' I player car (\d+)ms: frames=(\d+) writes=(\d+) wmax=(\d+)ms slow=(\d+) dry=(\d+)/(\d+)ms '
                      r'back=(\d+)/(\d+)ms avail=(-?\d+) delay=(-?\d+) rmax=(\d+)ms cpu=(\d+)ms tap=(\d+)/(\d+) under=(\d+) pos=(-?\d+)s played=(-?\d+)$', re.M)
CAR_FIELDS = ('ms', 'frames', 'writes', 'wmax', 'slow', 'dry', 'dry_ms', 'back', 'back_ms', 'avail', 'delay', 'rmax', 'cpu',
              'tap', 'tap_lost', 'under', 'pos', 'played')


def car_trace(log):
    """The car's trace lines in a player log (docs/iphone-mode.md#car-trace), each a dict of CAR_FIELDS."""
    return [dict(zip(CAR_FIELDS, map(int, m.groups()))) for m in CAR_LINE.finditer(log)]


def test_car_trace(player, songs, tmp):
    """The car's trace (docs/iphone-mode.md#car-trace): while way 3 plays, the quiet seconds are one line each
    Q2PLAYER_CAR_QUIET_MS (1.2 s here, a minute on a Q2) and a second with something wrong in it a line of its own, the
    lines' windows one after another so their frames add up to the car's PCM; an underrun counted where it happens,
    once also as Car Audio's health count (under=, Q2PLAYER_CAR_UNDER, written aside and renamed), and the position
    told demo beside the frames heard it comes from. a1 arrives as a card app writes it (player/grow.c), a quarter of
    it there at the start and the rest landing at 12 s, so the player waits in a read and the car's PCM runs dry:
    that second's line has the read's wait, the dry spell and the silence it put in. With car-pcm.on the tap holds
    the car's PCM, the first Q2PLAYER_CAR_TAP_S (3) seconds of it, frame for frame."""
    cp, car, out = car_player(), tmp/'car-trace.audio', tmp/'car-trace'
    car.write_text('hw:CARD=UAC1Gadget,0\n44100\n')
    card, status, logs = out/'mnt/mmc/.foo', out/'tmp/q2-foo.stream', out/'mnt/mmc/.q2muse/logs'
    card.mkdir(parents=True, exist_ok=True), logs.mkdir(parents=True, exist_ok=True)
    (logs/'car-pcm.on').write_text('')  # the tap is for an investigation, on only with this file
    whole, path = (songs/'a1.flac').read_bytes(), card/'grow.flac'
    head = len(whole) // 4
    path.write_bytes(whole[:head])
    line = lambda state, n: put(status, f'#Q2STREAM/1\tseq=1\n{state}\t{n}\t{len(whole)}\t20000\t/mnt/mmc/.foo/grow.flac\n')
    beat = lambda s: line('get', head)  # the app's line, rewritten twice a second while the song arrives

    def land(s):
        with open(path, 'ab') as f: f.write(whole[head:])
        line('done', len(whole))
    steps = sorted([(0.0, '{mcl-dsdmode\\0}'), (0.0, '{mcl-replaygain\\0}'), (0.1, '{mcl-setoutputway\\1}'),
                    (1.0, fakedemo.song('set-songurl', str(path))), (12.0, land)] + [(t / 2, beat) for t in range(4, 24)],
                   key=lambda st: st[0]) + [(30.0, None)]
    s = fakedemo.Session(out)
    s.music = songs
    # the run ends at a1's exit, its car PCM closed and the closing trace line written: at a fixed 30 s a starved
    # player was still in a1, and the frames traced fell short of those written (0 of 20 with it stopped 300 ms a second)
    s.wait_exits = 1
    line('get', head)
    s.run(steps, fakedemo.launch_host(cp, str(songs), 4, {'Q2PLAYER_CAR': str(car), 'Q2PLAYER_FORMATS': 's16',
                                                          'Q2PLAYER_CAR_TAP_S': '3', 'Q2PLAYER_CAR_UNDER': str(out/'under'),
                                                          'Q2PLAYER_CAR_QUIET_MS': '1200'}), 4)
    pcm = sorted((out/'pcm').glob('*.raw'))
    assert [p.name.split('_', 1)[1] for p in pcm] == ['44100_2_s16_car.raw'], pcm
    got, log = pcm[0].read_bytes(), (out/'player.log').read_text()
    lines = car_trace(log)
    assert len(lines) >= 3, f'{len(lines)} trace lines: {log[-1500:]}'
    assert sum(l['frames'] for l in lines) == len(got) // 4, f'{sum(l["frames"] for l in lines)} frames traced, {len(got) // 4} written'
    assert all(0 <= l['delay'] <= 22050 and l['avail'] == 22050 - l['delay'] for l in lines), lines
    dry = [l for l in lines if l['dry']]
    # the wrong second is a line of its own: a second, and the read in hand it waited for; every other line but the
    # one just before it (the quiet seconds up to it) and the closing one is QUIET_MS of quiet seconds
    at = lines.index(dry[0]) if dry else -1
    assert dry and dry[0]['ms'] >= 900 and dry[0]['ms'] - dry[0]['rmax'] <= 1500, [(l['ms'], l['rmax'], l['dry']) for l in lines]
    # A write over 50 ms (cartrace.c SLOW_MS) is a second of its own too, and so is the quiet stretch just before it:
    # on this machine that is its load, not the player (the file sink's write sleeps to keep pace, and a sleep that
    # overruns is a slow write; 10 of 10 runs with the player stopped 40 ms a second), and nothing is lost by it. A
    # dry second other than the stall's is an underrun, heard on a Q2, and still fails.
    quiet = [l for i, l in enumerate(lines[:-1]) if i not in (at, at - 1)]
    assert quiet and all(not l['dry'] and (l['ms'] >= 1200 or l['slow'] or lines[i + 1]['slow'])
                         for i, l in enumerate(lines[:-1]) if l in quiet), [(l['ms'], l['dry'], l['slow']) for l in lines]
    assert not (out/'under.tmp').exists(), 'the underrun count left aside, not renamed'
    # the read waits from the head's end to 12 s; at four times real speed the silence is four times that wait, less
    # the PCM's half second that played on into it
    assert len(dry) == 1 and dry[0]['dry'] == 1 and abs(dry[0]['dry_ms'] - (4 * dry[0]['rmax'] - 500)) < 1000 \
        and dry[0]['dry_ms'] >= 3000, f'dry spells: {dry}'
    slowest = max(l['rmax'] for l in lines)
    assert slowest >= 700 and slowest == max(l['rmax'] for l in lines if l is dry[0] or lines.index(l) == lines.index(dry[0]) - 1), \
        f'the read that waited is not the slowest by the dry spell: {[(l["rmax"], l["dry"]) for l in lines]}'
    tap = (logs/'car-pcm.raw').read_bytes()
    assert tap == got[:3 * 44100 * 4], f'tap {len(tap)} bytes, the car PCM\'s first 3 s {3 * 44100 * 4}; same: {tap == got[:len(tap)]}'
    assert lines[-1]['tap'] == 3 * 44100 * 4 and not any(l['tap_lost'] for l in lines), lines[-1]
    # the dry spell came while the song flowed (not a pause's, a seek's or the open's): Car Audio's health count
    assert lines[-1]['under'] == 1 and (out/'under').read_text() == '1\n', (lines[-1], list(out.glob('under')))
    # the position told demo, and the frames heard it comes from: they move together, a second a 44100 frames
    pos = [(l['pos'], l['played']) for l in lines]
    assert pos[-1][0] > pos[0][0] and all(abs(p - f // 44100) <= 1 for p, f in pos if f > 0), pos
    return (f'{len(lines)} lines (ms, dry: {[(l["ms"], l["dry"]) for l in lines]}), {len(got) // 4} frames; dry once, '
            f'{dry[0]["dry_ms"]} ms of silence, read {slowest} ms; tap {len(tap)} bytes')


def test_bt_formats_and_volume(player, songs, tmp):
    """Bluetooth (the 'bt' scenario's commands, as stock's capture of it): each song in its own format at the transport's 48 kHz,
    and the volume's levels in turn, measured against the song: 1, 25/256, 10/256, 484/256 (clipped), 163/256 left
    with the right silent. Stock's right channel there is about 8.4 times (its dB of a negative level)."""
    # bt-levels: the volumes sent a second of the song apart; 'bt' sends them a second of real time apart, which a
    # starved player had written less than a second of song in (0 of 20 with it stopped 300 ms a second)
    s, pcm, names = play(player, songs, 'bt-levels', tmp/'bt')
    assert [n.split('_', 1)[1] for n in names] == ['48000_2_s16_bt.raw', '48000_2_s32_bt.raw', '48000_2_f32_bt.raw'], names
    got, ref = array.array('h', pcm[0]), array.array('h', reference(songs/'a1.flac'))
    def rms(x): return math.sqrt(sum(v * v for v in x) / max(1, len(x)))
    levels = []
    for i in range(int(len(got) / 2 / 48000 / 0.25)):
        t = i * 0.25
        g, r = got[2 * int(t * 48000):2 * int((t + 0.25) * 48000)], ref[2 * int(t * 44100):2 * int((t + 0.25) * 44100)]
        levels.append(tuple(round(rms(g[c::2]) / rms(r[c::2]), 2) for c in (0, 1)))
    runs = [l for i, l in enumerate(levels) if 0 < i < len(levels) - 1 and levels[i - 1] == l == levels[i + 1]]
    order = [l for i, l in enumerate(runs) if not i or runs[i - 1] != l]
    want = [(1.0, 1.0), (0.1, 0.1), (0.04, 0.04), (1.89, 1.89), (0.64, 0.0)]  # unity's last half second is too short to count
    assert order == want, f'levels in turn {order}, want {want}'
    return f'3 opens at 48 kHz in the songs\' formats, levels {order}'


def wheel_clicks(x, rate):
    """The wheel clicks in x (S16 stereo), each checked against the sound made at rate: their first frames."""
    sound = click_sound('wheel', rate)
    lead = next(i for i, (e, c) in enumerate(sound) if abs(round(c * 32768)) >= 1)
    out = []
    for first, n in runs(x):
        assert abs(n - len(sound)) <= 2, f'{n} frames, {len(sound)} made'
        err = max(abs(x[2 * (first - lead + i) + c] - c2 * 32768) for i, (e, c2) in enumerate(sound) for c in (0, 1))
        assert err <= 1, f'{err:.2f} from the click made'
        out.append(first - lead)
    return out


def test_click_idle(player, songs, tmp):
    """The Clicker with nothing playing (docs/internals.md#key-tone). Paused: the song's frames not yet heard are taken
    back, the clicks play in silence in the same PCM, the first as it starts, once the DAC's mute line has let go
    (130 ms), the second 20 ms past what plays when it comes (300 - 130 + 20 ms after the first), and on the resume the song goes on from the
    frame where it paused, none lost or doubled. Stopped: the two clicks in a PCM of their own at 48 kHz, which closes
    once CLICK_HOLD_MS has passed with no click; nothing else is opened."""
    s, pcm, names = play(player, songs, 'click-idle', tmp/'click-idle')
    assert names == ['0000_44100_2_s16.raw', '0001_48000_2_s16.raw'], names
    out, ref = pcm[0], reference(songs/'a1.flac')
    at = (first_difference(out, ref) or 0) // 4
    assert 1.5 * 44100 < at < 2.6 * 44100, f'the pause at {at / 44100:.2f} s'
    back = next(j for j in range(at, len(out) // 4) if out[4 * j:4 * j + 4000] == ref[4 * at:4 * at + 4000])
    gap = array.array('h', out[4 * at:4 * back])
    firsts = wheel_clicks(gap, 44100)
    second = (300 - 130 + 20) / 1000
    assert len(firsts) == 2 and firsts[0] < 441 and abs(firsts[1] - firsts[0] - second * 44100) < 1800, f'clicks at {firsts}'
    assert out[4 * back:] == ref[4 * at:4 * at + len(out) - 4 * back], 'the song does not go on from where it paused'
    held = (back - at) / 44100
    assert 2.5 < held < 4.5, f'{held:.2f} s of silence held open'
    idle = array.array('h', pcm[1])
    firsts = wheel_clicks(idle, 48000)
    assert len(firsts) == 2 and firsts[0] < 480 and abs(firsts[1] - firsts[0] - second * 48000) < 1900, f'clicks at {firsts}'
    assert 2.5 < len(idle) / 2 / 48000 < 4.5, f'{len(idle) / 2 / 48000:.2f} s open'
    # one line a session in the unified log (engine.c click_session), so a click never heard leaves a trace
    said = re.findall(r' I player q2player: 2 clicks (paused|with nothing playing) on way 0, ([1-9]\d*) frames of silence',
                      (tmp/'click-idle'/'player.log').read_text())
    assert [w for w, _ in said] == ['paused', 'with nothing playing'], said
    return f'paused: 2 clicks, {held:.2f} s held, the song on from frame {at}; stopped: 2 clicks in a 48 kHz PCM'


def test_bt_open_waits_out_a_busy_pcm(player, songs, tmp):
    """docs/internals.md#bluetooth-pause: bluealsa's plugin claims its one client's place at hw_params and answers
    -EBUSY while another client has it, or while bluealsa has not yet seen the last one's close: a click session's
    PCM closed and another opened in the same moment met it on a Q2 (a click session's line and the busy line in the
    same millisecond, and the transport let go 5 s later, so nothing else held it). The open waits it out a bounded
    time, BT_BUSY_MS, and says how long, so a song or a click after a session's close is not lost to that moment; a
    PCM held for good is given up after it, with the wait in the log. output.c against alsa_test.c's libasound."""
    got = subprocess.run([tool(tmp, 'alsa_test', '-DHAVE_ALSA', '-DQ2_CAR_USB'), 'busy'], check=True, capture_output=True,
                         text=True)
    lines = {l.split()[0]: l.split() for l in got.stdout.split('\n') if l}
    # tries of hw_params, and the 50 ms waits between them (alsa_test.c counts usleep, no time passing)
    assert lines['busy0'][1:] == ['opened', 'tries', '1', 'sleeps', '0'], lines
    assert lines['busy6'][1:] == ['opened', 'tries', '7', 'sleeps', '6'], f'busy for 6 tries: {lines["busy6"]}'
    assert lines['busy1000'][1:] == ['failed', 'tries', '31', 'sleeps', '30'], f'held for good: {lines["busy1000"]}'
    assert "q2player: bluealsa's PCM was busy; it opened after 300 ms" in got.stderr, got.stderr[-800:]
    assert "q2player: bluealsa's PCM still busy after 1500 ms: another client holds it" in got.stderr, got.stderr[-800:]
    return 'free: at once; busy 6 tries: opened after 300 ms; held: given up after 1500 ms, said once'


def test_click_idle_alsa(player, songs, tmp):
    """A click with nothing playing, through output.c against alsa_test.c's counting ring: a song paused or none, on
    Bluetooth and on the headphone DAC, and a song paused on Bluetooth whose PCM is opened again for the click after
    BT_PAUSE_MS closed it (bt-reopened), IDLE_AHEAD_MS of silence (3840 frames at 48 kHz) goes into the ring with the
    click in it. bluealsa's plugin counts the headset's own delay in snd_pcm_delay (280 ms on the Pixel Buds,
    13440 frames) whether the PCM has started or not: a fill measured by that wrote nothing on Bluetooth, the PCM never
    started, and no click was heard paused or stopped, only into a song playing (2026-11-24)."""
    got = subprocess.run([tool(tmp, 'alsa_test', '-DHAVE_ALSA', '-DQ2_CAR_USB'), 'idle'], check=True, capture_output=True,
                         text=True).stdout.split('\n')
    lines = {l.split()[0]: l.split() for l in got if l}
    for k in ('bt-stopped', 'bt-paused', 'bt-reopened', 'dac-stopped', 'dac-paused'):
        got = lines[k]
        assert abs(int(got[2]) - 3840) <= 64 and int(got[4]) > 0, f'{" ".join(got)}: want about 3840 frames queued and the click in them'
    return ('paused, stopped and paused with the PCM opened again, on Bluetooth (280 ms of link delay), and the DAC: '
            '80 ms of silence queued, the click in it')


def test_click_volume(player, songs, tmp):
    """A click where the volume is in software (a USB DAC at 50: 25/256) is at its own level, not the music's: what
    plays is scaled, the click is not, within a step (docs/internals.md#key-tone)."""
    s, pcm, names = play(player, songs, 'click-usb', tmp/'click-usb')
    assert names == ['0000_44100_2_s16_usb.raw'], names
    x = array.array('h', pcm[0])
    at = next(i for i, v in enumerate(x) if v)
    want = [max(-32768, min(32767, round(c * 32768))) for _, c in click_sound('wheel')]
    got = list(x[at - at % 2::2][:len(want)])
    lead = next(i for i, w in enumerate(want) if w)  # the table's first frames round to 0
    far = max(abs(a - b) for a, b in zip(got, want[lead:]))
    assert far <= 1, f'the click is {far} steps from the table at its own level'
    return f'{len(want)} frames within a step of the click at its own level, the music at 25/256'


def test_way_change_waits_for_the_next_song(player, songs, tmp):
    """mcl-setoutputway while a song plays: it plays on in the same PCM, as stock's does (fixtures: stock-way)."""
    s, pcm, names = play(player, songs, 'way', tmp/'way')
    assert names == ['0000_44100_2_s16.raw'], names
    a1 = reference(songs/'a1.flac')
    assert a1.startswith(pcm[0]) and len(pcm[0]) > 10 * 44100 * 4, f'{len(pcm[0]) // 4} frames, not a1 from its start'
    return f'{len(pcm[0]) // 4} frames of a1 in one PCM through three changes of output'



# Phase 5: streams (httpd.py's servers, fakedemo.py's http* and https-* scenarios) and QQ Music (qmstub.c).

def events(s):
    """The events that end a song or answer one, in order: exit, error, nextstatus, stopstatus."""
    return [l.split()[-1] for l in s.lines if l.split()[-1].split('\\')[0] in ('{player-status', '{player-nextstatus', '{player-stopstatus')
            and l.split()[-1].endswith(('exit}', 'error}'))]


def wav_frames(path, nbytes):
    """The whole 16-bit stereo frames of a WAV's data in its first nbytes bytes."""
    data = pathlib.Path(path).read_bytes()
    at = data.index(b'data') + 8
    return (nbytes - at) // 4


def test_http_whole(player, songs, tmp):
    """A FLAC over HTTP with demo's cache -1 (3072 KB), and a WAV with 2048 KB: each its reference decode, one PCM."""
    a = check_exact(player, songs, tmp, 'http-whole', ['a1.flac'], 's16le', '0000_44100_2_s16.raw')
    b = check_exact(player, songs, tmp, 'http-whole-wav', ['p1.wav'], 's16le', '0000_44100_2_s16.raw')
    return f'{a}; {b}'


def test_http_seek(player, songs, tmp):
    """A seek to 15 s of a FLAC over HTTP, inside the cache: the rest is the reference from frame 15 x 44100, to the
    end, and the song ends with exit (stock and Q2 Muse end it at the seek, as they end a file's: a deviation)."""
    s, pcm, names = play(player, songs, 'http', tmp/'http')
    got, ref = b''.join(pcm), reference(songs/'a1.flac')
    tail = ref[15 * 44100 * 4:]
    assert got.endswith(tail), f'the PCM does not end with the reference from 15 s ({len(got) // 4} frames)'
    head = got[:len(got) - len(tail)]
    assert ref.startswith(head) and head, f'{len(head) // 4} frames before the seek are not the reference start'
    assert events(s) == ['{player-status\\exit}'], events(s)
    return f'{len(head) // 4} frames, then {len(tail) // 4} from frame {15 * 44100}'


def test_http_drop_resumes(player, songs, tmp):
    """The server closes the first connection at 1 MB: the player asks again from there (a range request) and the WAV
    plays whole, as stock's does (captured)."""
    s, pcm, names = play(player, songs, 'http-drop', tmp/'http-drop')
    want, got = reference(songs/'p1.wav'), b''.join(pcm)
    assert got == want, f'{len(got) // 4} frames, reference {len(want) // 4}, first difference {first_difference(got, want)}'
    ranges = [r for p, r in s.web[0].log if 'p1.wav' in p]
    assert 'bytes=1000000-' in ranges, ranges
    assert events(s) == ['{player-status\\exit}'], events(s)
    return f'{len(got) // 4} frames, requests {ranges}'


def test_http_stall(player, songs, tmp):
    """The server stops for 8 s at 1 MB and goes on: the WAV plays whole, nothing lost or doubled, one exit."""
    s, pcm, names = play(player, songs, 'http-stall', tmp/'http-stall')
    want, got = reference(songs/'p1.wav'), b''.join(pcm)
    assert got == want, f'{len(got) // 4} frames, reference {len(want) // 4}, first difference {first_difference(got, want)}'
    assert events(s) == ['{player-status\\exit}'], events(s)
    return f'{len(got) // 4} frames through the stall'


def test_http_cut(player, songs, tmp):
    """Every connection ends at 1 MB of the 3.5: what came plays, then the song ends with exit, as stock's (captured)."""
    s, pcm, names = play(player, songs, 'http-cut', tmp/'http-cut')
    want, got = reference(songs/'p1.wav'), b''.join(pcm)
    n = wav_frames(songs/'p1.wav', 1000000)
    assert got == want[:n * 4], f'{len(got) // 4} frames, {n} came; first difference {first_difference(got, want[:n * 4])}'
    assert events(s) == ['{player-status\\exit}'], events(s)
    return f'{n} frames, then exit'


def test_http_redirect(player, songs, tmp):
    """A 302 is followed: the FLAC plays from its start."""
    s, pcm, names = play(player, songs, 'http-redirect', tmp/'http-redirect')
    got, ref = b''.join(pcm), reference(songs/'a1.flac')
    assert len(got) > 44100 * 4 and ref.startswith(got), f'{len(got) // 4} frames, first difference {first_difference(got, ref)}'
    paths = [p for p, r in s.web[0].log]
    assert paths[0] == '/s/redirect/a1.flac' and '/s/-/a1.flac' in paths, paths
    return f'{len(got) // 4} frames of the reference after {paths[:2]}'


def test_http_missing(player, songs, tmp):
    """A 404: error, as stock's (which makes /tmp/notfile, captured)."""
    s, pcm, names = play(player, songs, 'http-missing', tmp/'http-missing')
    assert events(s) == ['{player-status\\error}'] and not pcm, (events(s), names)
    failed = re.findall(r' E player (q2player: open failed.*)', (tmp/'http-missing/player.log').read_text())
    assert len(failed) == 1 and failed[0].startswith('q2player: open failed (missing): http') and \
        failed[0].endswith('nothing.flac'), failed
    return 'error, ' + failed[0]


def test_https_checks(player, songs, tmp):
    """HTTPS against the CA bundle: a certificate the bundle's CA signed for localhost plays; one it signed for another
    name and a self-signed one are refused (error, nothing played). Stock plays all three, Q2 Muse's player the first
    two (it checks the chain, not the name): a deviation."""
    s, pcm, names = play(player, songs, 'https-good', tmp/'https-good')
    got, ref = b''.join(pcm), reference(songs/'a1.flac')
    assert len(got) > 44100 * 4 and ref.startswith(got), f'good: {len(got) // 4} frames, first difference {first_difference(got, ref)}'
    assert events(s) == ['{player-stopstatus\\exit}'], events(s)
    for bad in ('https-wrong', 'https-self'):
        s, pcm, names = play(player, songs, bad, tmp/bad)
        assert events(s) == ['{player-status\\error}'] and not pcm, (bad, events(s), names)
        assert not [p for p, r in s.web[('https-wrong', 'https-self').index(bad) + 2].log], f'{bad}: a request was sent'
    return f'good: {len(got) // 4} frames; wrong name and self-signed: error, no request sent'


def cache_answers(s):
    """(time, percent) of each get_cache_buffer answer."""
    return [(float(l.split()[0]), int(l.split('\\')[-1][:-1])) for l in s.lines if 'media-cache-buffer' in l]


def test_cache_first_fill(player, songs, tmp):
    """A 1024 KB cache at 20%, from a server at 50,000 B/s: nothing is heard before 209,715 bytes have come (4.2 s
    after the command), get_cache_buffer answers 0 until then, a percent of the cache while the song plays, 0 after
    the stop."""
    s, pcm, names = play(player, songs, 'http-fill', tmp/'http-fill')
    playing = [float(l.split()[0]) for l in s.lines if l.endswith('{player-status\\playing}')]
    assert playing and playing[0] >= 1.0 + 1024 * 1024 * 0.2 / 50000, f'playing at {playing[:1]}, before the first fill could have come'
    a = cache_answers(s)
    stop = 10.0
    before = [p for t, p in a if t < playing[0] - 0.3]
    during = [p for t, p in a if playing[0] + 0.3 < t < stop]
    after = [p for t, p in a if t > stop + 0.6]
    assert before and not any(before), f'before the song: {before}'
    assert during and all(0 < p <= 100 for p in during), f'while it plays: {during}'
    assert after and not any(after), f'after the stop: {after}'
    assert events(s) == ['{player-stopstatus\\exit}'], events(s)
    return f'playing at {playing[0]:.2f} s; answers {before[-1]}, then {during[0]}..{during[-1]}, then {after[0]}'


def test_stop_during_first_fill(player, songs, tmp):
    """A stop while the first fill waits (20 s of it at 20,000 B/s): stopstatus at once, nothing played, no error."""
    s, pcm, names = play(player, songs, 'http-stop-open', tmp/'http-stop-open')
    t = [float(l.split()[0]) for l in s.lines if l.endswith('{player-stopstatus\\exit}')]
    assert events(s) == ['{player-stopstatus\\exit}'] and t[0] - 3.0 < 0.5 and not pcm, (events(s), t, names)
    return f'stopstatus {t[0] - 3.0:.3f} s after the stop'


def test_stop_right_after_a_next(player, songs, tmp):
    """A stop 20 ms after a set-nexturl, before the player has taken the song it named (what a server song's press
    sent before patch/playback.c playback_start_refused): nothing plays after it, and the stop is answered. The device, 2026-11-18: the stop was lost, and the song playing started again from 0."""
    s, pcm, names = play(player, songs, 'restart-stop', tmp/'restart-stop')
    out, a1 = b''.join(pcm), reference(songs/'a1.flac')
    got = len(out) // 4
    assert out == a1[:len(out)], f'{got} frames: not a1 from its start alone (played again after the stop?)'
    assert got < 4.5 * 44100, f'{got} frames played, {got / 44100:.1f} s: a1 went on past the stop at 4 s'
    assert events(s) == ['{player-nextstatus\\exit}', '{player-stopstatus\\exit}'], events(s)
    # each song handed over, and the stop, in the unified log
    logged = re.findall(r' I player (q2player: .*)', (tmp/'restart-stop/player.log').read_text())
    a1_path = f'{songs}/a1.flac'
    assert logged == [f'q2player: set-songurl {a1_path}', f'q2player: set-nexturl {a1_path}', 'q2player: mcl-stop'], logged
    return f'{got / 44100:.1f} s played, then stopped; {events(s)}'


def test_a_stop_before_a_restart_is_kept(player, songs, tmp):
    """The drive of 2026-11-23: a write to the car's PCM held the playback thread 10.8 s, and meanwhile demo restarted the
    song after an output change, mcl-stop and then set-songurl of the same song from 46 s, with a pause. The
    set-songurl erased the stop the thread had not yet taken: the song went on, its end started it again from 46 s,
    and every song demo sent after was played one song late, under the next one's title. The stop is kept: it cuts
    the song playing (stopstatus), the restart plays from 5 s to its end, and the song sent at that exit follows.
    With the write held (a stalled sink), with the drive's pauses as well, and with the stop and the restart sent in
    one instant to a song playing."""
    a1, a2 = reference(songs/'a1.flac'), reference(songs/'a2.flac')

    def which(b):  # what a PCM holds: the song and the frame it starts at
        for n, ref in (('a1', a1), ('a2', a2)):
            at = ref.find(b[:4410 * 4]) if len(b) >= 4410 * 4 else -1
            if at >= 0 and at % 4 == 0: return f'{n} from {at // 4 / 44100:.2f} s, {len(b) // 4 / 44100:.2f} s of it'
        return f'{len(b) // 4} frames of neither'
    said, failed = [], []
    for name in ('stop-restart', 'stop-restart-paused', 'stop-restart-now'):
        s = fakedemo.Session(tmp/name)
        s.music = songs
        s.run(fakedemo.scenarios(str(songs))[name], fakedemo.launch_host(player, str(songs), 4), 4)
        if getattr(s, 'drain', None): s.drain.join(5)
        pcm = [p.read_bytes() for p in sorted((tmp/name/'pcm').glob('*.raw'))]
        got = [which(b) for b in pcm]
        said.append(f'{name}: {", then ".join(got)}; {events(s)}')
        ok = (len(pcm) == 3 and pcm[0] == a1[:len(pcm[0])] and len(pcm[0]) < 4 * 44100 * 4  # a1 cut by the stop
              and pcm[1] == a1[5 * 44100 * 4:]  # the restart, a1 from 5 s to its end
              and pcm[2] and a2.startswith(pcm[2])  # then a2
              and events(s)[:2] == ['{player-stopstatus\\exit}', '{player-status\\exit}'])
        if not ok: failed.append(name)
    assert not failed, f'{failed} wrong: ' + ' | '.join(said)
    return '; '.join(said)


def test_pause_then_pick(player, songs, tmp):
    """A song paused at 3 s, another picked at 5 s (set-nexturl): no frame of the paused song is written after the
    pick, only those before the pause (2 s heard and the PCM's 500 ms ahead, dropped at the cut), then the picked
    song from its start in the same PCM."""
    s, pcm, names = play(player, songs, 'pause-pick', tmp/'pause-pick')
    out, a1, a2 = b''.join(pcm), reference(songs/'a1.flac'), reference(songs/'a2.flac')
    old = (first_difference(out, a1) or 0) // 4
    assert old <= int(2.6 * 44100), f'{old} frames of the paused song, {old / 44100:.2f} s: some came after the pause'
    assert out[old * 4:] == a2[:len(out) - old * 4] and len(out) > old * 4, f'{names}: not a2 from its start after the paused song'
    assert names == ['0000_44100_2_s16.raw'] and events(s) == ['{player-nextstatus\\exit}'], (names, events(s))
    return f'{old / 44100:.2f} s of the paused song, then the pick'


def qq_env(tmp):
    """The environment that has q2player load qmstub.c as libQMDecryption.so."""
    lib = tmp/'qmstub'/'libQMDecryption.so'
    if not lib.exists():
        lib.parent.mkdir(exist_ok=True)
        subprocess.run(['cc', '-shared', '-fPIC', '-O2', '-o', lib, HERE/'qmstub.c'], check=True)
    return {'LD_LIBRARY_PATH': str(lib.parent)}


def test_qq(player, songs, tmp):
    """QQ Music over HTTP with the key in /tmp/qm_ID: decrypted by each byte's offset, the FLAC plays whole and
    exactly; a seek to 12 s lands exactly. No key file, a key the library refuses and a song id with a character stock
    refuses read the file as it is, which is not audio: error, as stock's, after its log line."""
    env = qq_env(tmp)
    # each played to its end: the run waits for its exit (qq-seek's fixed end, 12 s, was its end less the PCM's half
    # second and the open, at real speed, and a busy machine cut it short)
    s, pcm, names = play(player, songs, 'qq', tmp/'qq', env=env, exits=1)
    got, ref = b''.join(pcm), reference(songs/'a1.flac')
    assert got == ref, f'{len(got) // 4} frames, reference {len(ref) // 4}, first difference {first_difference(got, ref)}'
    s, pcm, names = play(player, songs, 'qq-seek', tmp/'qq-seek', env=env, exits=1)
    got = b''.join(pcm)
    assert got.endswith(ref[12 * 44100 * 4:]) and ref.startswith(got[:len(got) - len(ref[12 * 44100 * 4:])]), \
        f'qq-seek: {len(got) // 4} frames are not the reference with a seek to 12 s'
    logs = {'qq-nokey': 'ekey file ./tmp/qm_qq1 not found', 'qq-badkey': 'CreateDecryption failed (songid=qq1)',
            'qq-badid': 'bad songid char 0x2e'}
    for name, line in logs.items():
        s, pcm, names = play(player, songs, name, tmp/name, env=env)
        log = (tmp/name/'player.log').read_text()
        assert events(s) == ['{player-status\\error}'] and not pcm and line in log, (name, events(s), names, log[-300:])
    return f'{len(ref) // 4} frames exact, the seek exact, {len(logs)} refusals as stock logs them'



def test_sacd(player, songs, tmp):
    """An SACD image (music.py sacd.iso, the DSD64 tone in two tracks): on the DAC as DoP, each channel's bits the
    area's, from its first track and from its second (ss=2) to the area's end, with stock's media-info (CODEC ISO,
    LENGTH the area's 4 s; the bytes are stock's own capture's, docs/player-plan.md#phase-5-results); on a USB DAC as
    D2P, exactly FFmpeg 7.1.5's decode of d64.dff, the same tone; an area in DST refused, as stock refuses it."""
    want = dsd_bits(songs/'d64.dff')
    for name, start in (('sacd', 0), ('sacd-2', 2)):
        s, pcm, names = play(player, songs, name, tmp/name, speed=4)
        bits, bad = unpack_dop(b''.join(pcm))
        tail = [w[start * 352800:] for w in want]
        assert names == ['0000_176400_2_dop.raw'] and not bad and bits == tail, f'{name}: {names}, {bad} bad markers, {[len(b) for b in bits]} bytes a channel'
        info = [l.split()[-1] for l in s.lines if 'media-info\\5644800' in l]
        assert info[:1] == ['{media-info\\5644800|1411200|4|POS|ISO|1}'] and events(s) == ['{player-status\\exit}'], (name, info, events(s))
    s, pcm, names = play(player, songs, 'sacd-usb', tmp/'sacd-usb', speed=4)
    ref = ffref(tmp, 'decode', songs/'d64.dff')
    assert names == ['0000_352800_2_f32_usb.raw'] and pcm[0] == ref, f'sacd-usb: {names}, first difference {first_difference(b"".join(pcm), ref)}'
    if (songs/'sacd-dst.iso').exists():
        s, pcm, names = play(player, songs, 'sacd-dst', tmp/'sacd-dst')
        assert events(s) == ['{player-status\\error}'] and not pcm, events(s)
    return f'DoP from 0 s and 2 s the area\'s bits, D2P exact, DST {"refused" if (songs/"sacd-dst.iso").exists() else "not tried (no sample)"}'


def parse(text):
    """A transcript's lines as (seconds, direction, name, argument), and its position samples."""
    lines, positions = [], []
    for l in text.splitlines():
        if l.startswith('# positions'):
            positions = [(float(t), int(p)) for t, p in (x.split(':') for x in l.split(')', 1)[1].split())]
            continue
        m = re.match(r'\s*([\d.]+) ([<>]) \{([^\\]*)\\(.*)\}$', l)
        if m: lines.append((float(m[1]), m[2], m[3], m[4]))
    return lines, positions


def skeleton(lines, levels=True):
    """What demo acts on, in order: each command, each event that ends a song, and each answer to a one-off query. The
    200 ms poll's answers are compared apart (answers), since when "ready" turns to "playing" is not exact. The level
    meter's answers are compared with levels, but for a song whose peak moves within the meter's few ms (the
    controls scenario's tone under a sweep beats), where which block it measured decides; the spectrum's twelve
    zeros are q2player's, a deviation. get_cache_buffer's answers are left out: MPlayer's figure depends on when its
    cache thread last ran (stock and Q2 Muse's answer 9 and 0 to the same stream), demo only asks whether it is above 0
    (playerqueue_page_init 0x53d1d4), and test_cache_first_fill checks them."""
    out = []
    for t, d, name, arg in lines:
        if name in ('get_cache_buffer', 'media-cache-buffer'): continue
        if d == '>': out.append(f'> {name}')
        elif name in ('player-status', 'media-info') and arg in ('ready', 'playing', 'no-playing-status') or name == 'media-info': continue
        elif name == 'mcl-getspectrum' or name == 'mcl-getuvmeter' and not levels: out.append(f'< {name}')
        else: out.append(f'< {name}\\{arg}')
    return out


def answers(lines):
    """The distinct media-info answers, in order, bitrate apart."""
    out = []
    for t, d, name, arg in lines:
        if d == '<' and name == 'media-info' and '|' in arg:
            f = arg.split('|')
            if not out or out[-1][1:] != f[1:]: out.append(f)
    return out


# Where q2player differs from the fixture on purpose; docs/player-plan.md#deviations has each one's reason.
KNOWN = {
    'stock-transport': 'stock ends this FLAC half a second after a seek to 15 s of its 20; Q2 Muse fixes it and so does q2player',
    'stock-gapless-flac': 'the same: after the seek to 12 s stock ends the song at 15 s and is into the next by 6 s',
    'stock-start': 'the same fault after a start at 7 s: stock ends the song at 11 s',
    'stock-seek': 'the same after a seek to 9 s: stock ends the song at 13 s',
    'stock-cancel': 'the same after the seek to 12 s, before the preload is cancelled',
    'stock-emptynext': 'the same after the seek to 14 s, before the preload is sent',
    'stock-badnext': 'stock stops the song when its preload is not audio; Q2 Muse plays it to its end, and so does q2player',
    'stock-formats-all': 'compared per file by test_media_info_per_format, with its deviations',
    'pod-formats-all': 'compared per file by test_media_info_per_format, with its deviations',
    'stock-dsd-2-2': 'Native DSD on a USB DAC: stock asks ALSA for format 31, SND_PCM_FORMAT_SPECIAL, which is refused, and plays '
                     'nothing; q2player sends DSD_U32_BE (test_native_usb)',
    'pod-dsd-2-2': 'the same in Q2 Muse\'s hciplayer',
    # streams (phase 5)
    'stock-http-slow': 'a WAV from a server slower than it plays: stock starts 10 s on, after its demuxer has looked at the '
                       'file\'s end over two more connections; q2player starts once the first fill is in (test_cache_first_fill)',
    'pod-http-slow': 'the same in Q2 Muse\'s hciplayer',
    'stock-http-big': 'the same at 4096 KB: stock starts 9 s on', 'pod-http-big': 'the same in Q2 Muse\'s hciplayer',
    'stock-http-stall': 'the same: stock starts after the server\'s 8 s stall, q2player before it and waits in it (test_http_stall)',
    'pod-http-stall': 'the same in Q2 Muse\'s hciplayer',
    'stock-http-gapless': 'stock ends a FLAC stream half a second after a seek near its end, as it ends a file; Q2 Muse\'s fix '
                          'is for files alone, so its player does too; q2player plays on (test_http_seek)',
    'pod-http-gapless': 'the same in Q2 Muse\'s hciplayer',
    'stock-http-norange': 'the same after a seek to 12 s of a server without ranges', 'pod-http-norange': 'the same in Q2 Muse\'s hciplayer',
    'stock-https-wrong': 'stock checks no certificate and plays; q2player refuses a certificate for another name (test_https_checks)',
    'pod-https-wrong': 'Q2 Muse\'s hciplayer checks the chain, not the name, and plays; q2player refuses it',
    'stock-https-self': 'stock plays a self-signed certificate; q2player refuses it, as Q2 Muse\'s does',
}


# Where q2player's media-info differs from Q2 Muse's hciplayer's for a file of formats-all: docs/player-plan.md#deviations.
MEDIA_INFO_DEVIATIONS = {
    'k24.wv': 'BITS is the file\'s 24, where stock says 32',
    'z.dts': 'it plays; stock refuses this file (FFmpeg\'s own DTS encoder\'s) with an error',
}


def media_info_by_song(s):
    """q2player's media-info figures for each song the session sent (1 for the first), POS for the position: the last
    answer to a query asked under that song's command, and after the song before it was cut (its nextstatus or exit,
    by order on the socket). Not by when this side read it: side by side, the runner's other threads held the GIL,
    the session read and stamped late, and a song's answers landed in the next song's 2.5 s (6 of 6 runs beside seven
    threads of Python work). Not before the cut: until the playback thread cuts the song playing, the player answers
    for the song still playing (stock's cut comes 60 to 80 ms after the command, docs/player.md#captured-sequences),
    and a burst of queries taken all at once after the command (the player held through the song's 2.5 s,
    test_media_info_held) was all answered so."""
    cuts = [n for m, _, n in s.ends if m in ('{player-nextstatus\\exit}', '{player-status\\exit}')]
    got = {}
    for k, (asked, m) in enumerate(zip(s.mi_asked, s.mi_answers)):
        if '|' in m and (asked == 1 or (len(cuts) >= asked - 1 and k >= cuts[asked - 2])):
            got[asked] = m[12:-1].split('|')
            got[asked][3] = 'POS'
    return got


def test_media_info_held(player, songs, tmp):
    """The player held (SIGSTOP) from just before w16.wav's set-nexturl until 50 ms before w24.wav's is due, as a
    machine that does not run it for that long (integ/q2-1129, load 1 to 2: w16.wav reported as the OGG before it):
    the queries of w16's 2.5 s are all taken at once with the command and answered for e.ogg, which plays until the
    playback thread cuts it. The next command waits for w16's own figures (fakedemo.figures), and each song's
    figures are its own."""
    m = songs
    hold = lambda s: os.kill(s.proc.pid, signal.SIGSTOP)
    go = lambda s: os.kill(s.proc.pid, signal.SIGCONT)
    boot = [(0.0, '{mcl-dsdmode\\0}'), (0.0, '{mcl-replaygain\\0}'), (0.1, '{mcl-setoutputway\\0}'), (0.1, '{mcl-setvol\\60}')]
    steps = boot + [(1.0, fakedemo.song('set-songurl', f'{m}/e.ogg')), (3.45, hold), (3.5, fakedemo.song('set-nexturl', f'{m}/w16.wav')),
                    (5.95, go), (6.0, fakedemo.figures()), (6.0, fakedemo.song('set-nexturl', f'{m}/w24.wav')), (6.0, fakedemo.figures()),
                    (8.5, '{mcl-stop\\null}'), (9.0, None)]
    s = fakedemo.Session(tmp/'mi-held')
    s.music = songs
    s.run(steps, fakedemo.launch_host(player, str(songs), 1), 1)
    got = media_info_by_song(s)
    kinds = {k: (v[4], v[5]) for k, v in got.items()}
    assert kinds == {1: ('OGG', '32'), 2: ('WAV', '16'), 3: ('WAV', '24')}, got
    return ', '.join(f'{c} {b}' for c, b in kinds.values())


def test_media_info_per_format(player, songs, tmp):
    """formats-all: for each file, the media-info Q2 Muse's hciplayer gave (fixtures/pod-formats-all.txt): RATE, LENGTH,
    CODEC and BITS the same and BITRATE within 2%, but for MEDIA_INFO_DEVIATIONS."""
    def per_file(lines):
        out = {}
        for t, d, name, arg in lines:
            i = int((t - 1.0) // 2.5)
            if d == '<' and name == 'media-info' and '|' in arg and 0 <= i < len(fakedemo.ALL_FORMATS):
                out.setdefault(fakedemo.ALL_FORMATS[i], arg.split('|'))
        return out
    want = per_file(parse((HERE/'fixtures/pod-formats-all.txt').read_text())[0])
    # a fixture's transcript writes an answer only when it changes: a file with none of its own answered as the one before
    for a, b in zip(fakedemo.ALL_FORMATS, fakedemo.ALL_FORMATS[1:]): want.setdefault(b, want.get(a))
    s, _, _ = play(player, songs, 'formats-all', tmp/'formats-all', speed=4)
    got = media_info_by_song(s)
    got = {f: got.get(i + 1) for i, f in reversed(list(enumerate(fakedemo.ALL_FORMATS)))}  # f24.flac's first play
    bad = []
    for f in fakedemo.ALL_FORMATS:
        w, g = want.get(f), got.get(f)
        same = w and g and w[1:3] + w[4:] == g[1:3] + g[4:] and abs(int(w[0]) - int(g[0])) <= max(2000, int(w[0]) // 50)
        if bool(same) == (f in MEDIA_INFO_DEVIATIONS): bad.append(f'{f}: q2player {g}, Q2 Muse {w}')
    assert not bad, '\n  ' + '\n  '.join(bad)
    return f'{len(fakedemo.ALL_FORMATS)} files, {len(MEDIA_INFO_DEVIATIONS)} deviations as listed'


def check_protocol(player, songs, tmp, fixture):
    name = fixture.stem
    scenario = name.split('-', 1)[1]
    want, want_pos = parse(fixture.read_text())
    # the run waits for as many song ends as the fixture has: a fixed end cut a starved player's last event off
    # (pod-badnext's error, side by side)
    ends = sum(d == '<' and '{%s\\%s}' % (n, a) in fakedemo.ENDS for _, d, n, a in want)
    s, _, _ = play(player, songs, scenario, tmp/f'p-{name}', ends=ends)
    got, _ = parse((tmp/f'p-{name}/transcript.txt').read_text())
    # q2player's every position answer, not the transcript's one a second: a session the machine held up polls late, and
    # the one sample left near a song change (pod-gapless-*, 10.2 s) could be the next song's 0 alone
    got_pos = s.positions
    norm = lambda sk: [x.replace(str(songs), '/mnt/mmc') for x in sk]
    a, b = norm(skeleton(got, scenario != 'controls')), norm(skeleton(want, scenario != 'controls'))
    assert a == b, f'{name}: events differ\n  q2player: {a}\n  fixture:  {b}'
    ia, ib = answers(got), answers(want)
    assert [x[1:] for x in ia] == [x[1:] for x in ib], f'{name}: media-info differs\n  q2player: {ia}\n  fixture:  {ib}'
    for x, y in zip(ia, ib):
        assert abs(int(x[0]) - int(y[0])) <= max(2000, int(y[0]) // 50), f'{name}: bitrate {x[0]}, fixture {y[0]}'
    # positions: each of the fixture's samples within 2 s of q2player's at about that time. hciplayer under qemu is up
    # to a second and a half behind after a start or a seek (it mutes, sleeps 150 ms and reopens), so the window
    # reaches back that far; a sample after the last command has no counterpart once q2player has stopped.
    # hciplayer reads 0 until a song sounds, about 600 ms after its command under qemu: a start offset q2player has
    # already reached by then is not a difference.
    starts = [t for t, d, name, arg in want if d == '>' and name in ('set-songurl', 'set-nexturl')]
    for t, p in want_pos:
        if p == 0 and any(0 <= t - u < 1.0 for u in starts): continue
        near = [q for u, q in got_pos if t - 1.6 < u < t + 0.5]
        assert not near or min(abs(q - p) for q in near) <= 2, f'{name}: at {t} s position {near}, fixture {p}'
    return f'{len(b)} events, {len(ib)} media-info answers, {len(want_pos)} positions'


def test_crash_count(player, songs, tmp):
    """player/main.c: a fatal signal is counted in /tmp/q2player.crashes (one more within 60 s of the start) and logged
    to the unified log, and the process dies of it, as demo then starts the player again."""
    import shutil, signal, socket, time
    d = tmp/'crashes'
    for n in (1, 2, 3):
        if n == 1: shutil.rmtree(d, ignore_errors=True); (d/'tmp').mkdir(parents=True)
        (d/'tmp/sock_play').unlink(missing_ok=True)
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM); srv.bind(str(d/'tmp/sock_play')); srv.listen(1); srv.settimeout(5)
        p = subprocess.Popen([player, '-ao', 'alsa'], cwd=d, stdout=open(d/'player.log', 'w'), stderr=subprocess.STDOUT,
                             env={'PATH': '/usr/bin:/bin', 'Q2PLAYER_SOCK': str(d/'tmp/sock_play'), 'Q2PLAYER_PCM': str(d)})
        conn, _ = srv.accept(); time.sleep(0.2)
        p.send_signal(signal.SIGSEGV); assert p.wait(5) == -signal.SIGSEGV, p.returncode
        conn.close(); srv.close()
        assert (d/'tmp/q2player.crashes').read_text() == str(n), (d/'tmp/q2player.crashes').read_text()
        # a unified-log line (docs/internals.md#unified-log): time, uptime, level, source, then the text
        assert re.search(rf'^\d{{4}}-\d\d-\d\dT\d\d:\d\d:\d\dZ \d+ E player q2player: crashed, signal 11, crash {n} in a row$',
                         (d/'player.log').read_text(), re.M), (d/'player.log').read_text()
    return 'three crashes, each counted, logged and the process ended by its signal'



# ---- other sources for the car (player/feed.c, docs/iphone-mode.md#other-sources-over-usb): q2video's sound through
# ALSA's q2car device, raw 44.1 kHz frames in a FIFO the player reads while its own song is stopped ----

def tone(seconds, hz=441, rate=44100):
    """16-bit stereo, a sine whose every frame differs from silence's but one in fifty"""
    n = int(seconds * rate)
    a = array.array('h', (int(12000 * math.sin(2 * math.pi * hz * i / rate)) for i in range(n) for _ in (0, 1)))
    return a.tobytes()


class Feeder:
    """A source writing to the player's feed (as ALSA's file PCM does): blocking writes, so the player's reads pace it;
    each run's start and end times (wall clock) noted."""
    def __init__(self, path):
        self.path, self.runs, self.pool = path, [], concurrent.futures.ThreadPoolExecutor(1)

    def send(self, data):
        def go():
            t0 = time.monotonic()
            with open(self.path, 'wb', buffering=0) as f:
                for i in range(0, len(data), 4096):
                    f.write(data[i:i + 4096])
            self.runs.append((t0, time.monotonic()))
        return lambda s: self.pool.submit(go)


def feed_play(songs, tmp, name, steps, feed, car, speed=1, logs=None):
    cp = car_player()
    s = fakedemo.Session(tmp/name)
    s.music = songs
    if logs: logs.mkdir(parents=True, exist_ok=True)  # after the session's start, which empties its folder's tmp only
    s.run(steps, fakedemo.launch_host(cp, str(songs), speed, {'Q2PLAYER_CAR': str(car), 'Q2PLAYER_FORMATS': 's16',
                                                             'Q2PLAYER_FEED': str(feed)}), speed)
    pcm = sorted((tmp/name/'pcm').glob('*.raw'))
    return [p.name.split('_', 1)[1] for p in pcm], [p.read_bytes() for p in pcm], (tmp/name/'player.log').read_text()


def test_car_feed_reaches_the_car(player, songs, tmp):
    """Radio (q2video's receiver) while the car takes the audio and nothing of the player's plays: its frames reach the
    car's PCM at 44.1 kHz, whole and in order. The card's log folder is there without car-pcm.on: no tap is written
    (docs/iphone-mode.md#car-trace)."""
    car, feed = tmp/'feed1.audio', tmp/'feed1.fifo'
    car.write_text('hw:CARD=UAC1Gadget,0\n44100\n')
    logs = tmp/'feed1/mnt/mmc/.q2muse/logs'
    src, f = tone(2.0), Feeder(feed)
    steps = [(0.0, '{mcl-setoutputway\\1}'), (0.5, f.send(src)), (6.0, None)]
    names, pcm, log = feed_play(songs, tmp, 'feed1', steps, feed, car, logs=logs)
    assert logs.is_dir() and not list(logs.glob('car-pcm*')), list(logs.iterdir())
    assert names == ['44100_2_s16_car.raw'], (names, log[-600:])
    assert src in pcm[0], f'{len(pcm[0]) // 4} frames, the tone whole and in order: {(first_difference(pcm[0], src) or 0) // 4}'
    assert 'the car plays another source' in log
    return f'{len(src) // 4} frames of the source in one car PCM of {len(pcm[0]) // 4}'


def test_car_feed_hands_over_both_ways(player, songs, tmp):
    """a1 on the car, stopped for Radio, Radio's frames, then a1 again: three car PCMs in turn, each closed on its
    silence (silence written before each close), the source's frames whole in the middle one."""
    car, feed = tmp/'feed2.audio', tmp/'feed2.fifo'
    car.write_text('hw:CARD=UAC1Gadget,0\n44100\n')
    src, f = tone(2.0, 330), Feeder(feed)
    steps = [(0.0, '{mcl-dsdmode\\0}'), (0.0, '{mcl-replaygain\\0}'), (0.1, '{mcl-setoutputway\\1}'),
             (0.5, fakedemo.song('set-songurl', f'{songs}/a1.flac')), (3.0, '{mcl-stop\\null}'), (3.5, f.send(src)),
             (8.5, fakedemo.song('set-songurl', f'{songs}/a1.flac')), (11.0, '{mcl-stop\\null}'), (12.0, None)]
    names, pcm, log = feed_play(songs, tmp, 'feed2', steps, feed, car)
    assert names == ['44100_2_s16_car.raw'] * 3, (names, log[-800:])
    a1 = reference(songs/'a1.flac')
    assert pcm[0][:44100 * 4] == a1[:44100 * 4] and pcm[2][:44100 * 4] == a1[:44100 * 4], 'a1, before and after'
    assert src in pcm[1], 'the source between, whole'
    return 'a1, the source, a1: three car PCMs (each closed on silence: test_alsa_sw_params car-close)'


def test_car_feed_paused_is_silence_not_a_stall(player, songs, tmp):
    """Radio paused (nothing written) for a second, then on: one PCM, not closed, the gap a dry spell, which a
    device's never-stopped PCM plays as silence (output.c alsa_sw), the frames before and after it whole."""
    car, feed = tmp/'feed3.audio', tmp/'feed3.fifo'
    car.write_text('hw:CARD=UAC1Gadget,0\n44100\n')
    a, b, f = tone(1.5, 441), tone(1.5, 551), Feeder(feed)
    steps = [(0.0, '{mcl-setoutputway\\1}'), (0.5, f.send(a)), (3.0, f.send(b)), (7.0, None)]
    names, pcm, log = feed_play(songs, tmp, 'feed3', steps, feed, car)
    assert names == ['44100_2_s16_car.raw'], names
    # the file sink writes only what it is given: the pause is the car trace's dry spell, the silence a device plays
    assert pcm[0].index(a) == 0 and pcm[0].index(b) == len(a), (pcm[0].index(a), pcm[0].index(b))
    dry = re.search(r'dry=1/(\d+)ms', log)
    assert dry and 500 <= int(dry[1]) <= 2000, log[-400:]
    return f'one PCM through the pause, {dry[1]} ms of it dry (silence on a device)'


def test_car_feed_source_gone_leaves_the_song_resumable(player, songs, tmp):
    """q2video dies mid-stream: the player waits FEED_END_MS on silence, closes the car's PCM on its silence, and a1
    plays after it as ever; no hang."""
    car, feed = tmp/'feed4.audio', tmp/'feed4.fifo'
    car.write_text('hw:CARD=UAC1Gadget,0\n44100\n')
    src, f = tone(1.0), Feeder(feed)
    steps = [(0.0, '{mcl-dsdmode\\0}'), (0.0, '{mcl-replaygain\\0}'), (0.1, '{mcl-setoutputway\\1}'),
             (0.5, f.send(src)), (5.0, fakedemo.song('set-songurl', f'{songs}/a1.flac')), (8.0, '{mcl-stop\\null}'),
             (9.0, None)]
    names, pcm, log = feed_play(songs, tmp, 'feed4', steps, feed, car)
    assert names == ['44100_2_s16_car.raw'] * 2, names
    assert src in pcm[0] and 'the car\'s other source ended' in log
    assert pcm[1][:44100 * 4] == reference(songs/'a1.flac')[:44100 * 4]
    return 'the source ended, a1 after it'


def test_car_feed_keeps_the_car_close_behind(player, songs, tmp):
    """Radio's sound in the car runs at most FEED_LEAD_MS (150 ms) of PCM ahead of the car (engine.c feed_session), as a
    scan does, not the PCM's whole half second: with the FIFO's 23 ms and the ring's 100 ms, under 0.3 s behind the
    picture. The car trace's lines (a line a second here: a quiet window of 300 ms, looked at each second) give the
    PCM's delay as each is made: never over the lead and one write, and the PCM never dry while the source flows."""
    car, feed = tmp/'feedlead.audio', tmp/'feedlead.fifo'
    car.write_text('hw:CARD=UAC1Gadget,0\n44100\n')
    src, f = tone(6.0), Feeder(feed)
    cp, s = car_player(), fakedemo.Session(tmp/'feedlead')
    s.music = songs
    s.run([(0.0, '{mcl-setoutputway\\1}'), (0.5, f.send(src)), (9.0, None)],
          fakedemo.launch_host(cp, str(songs), 1, {'Q2PLAYER_CAR': str(car), 'Q2PLAYER_FORMATS': 's16', 'Q2PLAYER_FEED': str(feed),
                                                   'Q2PLAYER_CAR_QUIET_MS': '300'}), 1)
    pcm = b''.join(p.read_bytes() for p in sorted((tmp/'feedlead/pcm').glob('*.raw')))
    assert src in pcm, 'the source whole and in order'
    lines = car_trace((tmp/'feedlead/player.log').read_text())
    flowing = [l for l in lines[1:-1]]  # the first (the PCM filling) and the last (the source gone quiet) left out
    assert len(flowing) >= 4, [(l['ms'], l['delay']) for l in lines]
    worst = max(l['delay'] for l in flowing)
    assert worst <= 44100 * 150 // 1000 + 1024, f'the PCM {worst} frames ({worst * 1000 // 44100} ms) ahead of the car'
    assert not any(l['dry'] for l in flowing), [(l['delay'], l['dry']) for l in flowing]
    return f'the PCM at most {worst * 1000 // 44100} ms ahead over {len(flowing)} windows, never dry'


def test_car_feed_reader_sleeps_while_no_source_writes(player, songs, tmp):
    """The feed's reader (player/feed.c) waits in poll() with no timeout while nothing writes to the FIFO: q2player
    idle for 4 s makes one such poll and no timed-out one (it woke ten times a second, for the player's whole life).
    Counted with strace, so a sleep it takes anywhere else is not counted."""
    d, trace = tmp/'feedidle', tmp/'feedidle.strace'
    d.mkdir(parents=True, exist_ok=True)
    wrap = tmp/'feedidle.sh'
    wrap.write_text(f'#!/bin/sh\nexec strace -f -e trace=poll,ppoll -o {trace} {car_player()}\n')
    wrap.chmod(0o755)
    s = fakedemo.Session(d)
    s.music = songs
    s.run([(4.0, None)], fakedemo.launch_host(str(wrap), str(songs), 1, {'Q2PLAYER_CAR': str(tmp/'feedidle.audio'),
                                                                         'Q2PLAYER_FEED': str(tmp/'feedidle.fifo')}), 1)
    t = trace.read_text()
    fds = r'(?:\[\{fd=\d+, events=POLLIN\}\]|0x[0-9a-f]+)'  # strace may not be let read the array
    timed_out = re.findall(rf'poll\({fds}, 1, 100\) += 0 \(Timeout\)', t)
    waits = re.findall(rf'poll\({fds}, 1, -1', t)
    assert not timed_out and len(waits) == 1, f'{len(timed_out)} timed-out polls, {len(waits)} waits'
    return f'one wait, no timed-out poll in 4 s idle'


def test_car_feed_does_not_block_a_source_the_car_does_not_take(player, songs, tmp):
    """The car's way not the output (Bluetooth's): the feed's frames are read and dropped at the source's own rate,
    so a source writing 3 s of sound is done in about 3 s, never held, and nothing reaches any PCM."""
    car, feed = tmp/'feed5.audio', tmp/'feed5.fifo'
    src, f = tone(3.0), Feeder(feed)
    steps = [(0.0, '{mcl-setoutputway\\1}'), (0.5, f.send(src)), (6.5, None)]
    names, pcm, log = feed_play(songs, tmp, 'feed5', steps, feed, car)
    assert names == [], names
    assert len(f.runs) == 1, 'the source finished'
    took = f.runs[0][1] - f.runs[0][0]
    assert 2.5 < took < 3.8, f'{took:.2f} s for 3 s of sound'
    return f'3 s of sound dropped in {took:.2f} s'


# Tests that read another's output, run after it in its folder: {test: the test whose folder it reads}.
AFTER = {'test_exact_mp3_no_step_at_the_cut': 'test_exact_mp3_gapless'}
# Tests that run alone, after everything else, each tried again when it failed on a run the machine starved:
# those whose assertions bound what happens in real time (a scan's 150 ms turn, a feed's 3 s of sound, the car trace's
# lines a second, a preload's opening against the song's end, the clicks' 130 ms mute, the positions sampled in a
# window of seconds while a growing song's seek waits), each failed side by side (2026-11-22), and the one that
# asserts on the host's own /tmp/notsupport, which any player whose song fails writes.
SERIAL = ('test_scan', 'test_grow_seek_beyond', 'test_grow_seek_inside', 'test_car_trace', 'test_car_feed_reaches_the_car', 'test_car_feed_hands_over_both_ways',
          'test_car_feed_paused_is_silence_not_a_stall', 'test_car_feed_source_gone_leaves_the_song_resumable',
          'test_car_feed_does_not_block_a_source_the_car_does_not_take', 'test_preload_outrun', 'test_click_idle',
          'test_a_failure_already_replaced_is_not_reported',
          # fix/stream-start's: a song with a cover opened by 3.0 s, the feed's 150 ms lead, the feed reader's wakeups
          # counted over seconds, the car's PCM fds read from /proc right after each release
          'test_grow_opens_a_song_with_a_cover_on_its_first_audio', 'test_car_feed_keeps_the_car_close_behind',
          'test_car_feed_reader_sleeps_while_no_source_writes', 'test_car_release_while_paused',
          # smart-crossfade's: the player on one core shared with busy loops
          'test_smart_crossfade_starved', 'test_tail_file_starved')
WIDTH = 8  # tests and fixtures side by side: each mostly waits on a player running in real time or faster
STALL_S = 0.1  # a 5 ms sleep that took this much longer: the machine did not run this process (measured: 650 ms under
               # a nice-19 job on a busy core, under 10 ms when the core is shared fairly)
TRIES = 3


class Stalls:
    """The longest this process went unscheduled while it ran, from a thread sleeping 5 ms at a time."""
    def __enter__(self):
        self.worst, self.done = 0.0, False
        def watch():
            while not self.done:
                t = time.monotonic(); time.sleep(0.005); self.worst = max(self.worst, time.monotonic() - t - 0.005)
        self.thread = threading.Thread(target=watch, daemon=True); self.thread.start()
        return self

    def __exit__(self, *exc):
        self.done = True; self.thread.join()


def timed(f, *args):
    """f(*args)'s answer or AssertionError, and its seconds: (ok, text, seconds)."""
    t = time.monotonic()
    try: return True, f(*args), time.monotonic() - t
    except AssertionError as e: return False, str(e), time.monotonic() - t


def chain(n, names, player, songs, tmp):
    """The tests names, one after another in folder tmp/c<n> (AFTER's reader after its writer; short, as the player's
    socket path under it must fit a sockaddr_un): [(name, ok, text, s)]."""
    d = tmp/f'c{n}'; d.mkdir()
    return [(n, *timed(globals()[n], player, songs, d)) for n in names]


def alone(name, player, songs, tmp):
    """A SERIAL test, run again (TRIES in all) when it failed on a run the machine starved: (ok, text, s)."""
    for attempt in range(1, TRIES + 1):
        d = tmp/f's{SERIAL.index(name)}-{attempt}'; d.mkdir()
        with Stalls() as stalls:
            ok, text, secs = timed(globals()[name], player, songs, d)
        if ok or stalls.worst < STALL_S:
            break
        print(f'again {name}: failed while starved for {stalls.worst * 1000:.0f} ms ({text[:200]})', flush=True)
    if not ok and stalls.worst >= STALL_S:
        text += f' (every try starved, the last for {stalls.worst * 1000:.0f} ms)'
    return ok, text, secs


def report(name, ok, text, secs):
    print(f'{"ok  " if ok else "FAIL"} {name} ({secs:.1f} s): {text}', flush=True)
    return 0 if ok else 1


def main():
    only = sys.argv[sys.argv.index('-k') + 1] if '-k' in sys.argv else ''
    player = subprocess.run([sys.executable, ROOT/'tools/player.py', 'build', 'host'], check=True, capture_output=True, text=True).stdout.strip()
    car_player()  # built once here, so the tests that run it side by side never build it at the same time
    songs = music.make(CACHE/'music')
    failed, start = 0, time.monotonic()
    with tempfile.TemporaryDirectory(prefix='q2player-') as t:
        tmp = pathlib.Path(t)
        tests = [n for n in globals() if n.startswith('test_') and only in n]
        chains = [[n, *(r for r, w in AFTER.items() if w == n and r in tests)] for n in tests
                  if n not in SERIAL and (n not in AFTER or AFTER[n] not in tests)]
        fixtures = [f for f in sorted((HERE/'fixtures').glob('*.txt')) if only in 'protocol-' + f.stem and f.stem not in KNOWN]
        again = []  # what failed side by side, run again alone below
        # a process each, not a thread: a test's session (fakedemo) reads, stamps and sends on its own clock, and in
        # threads the others' Python held the GIL from it (test_media_info_per_format's answers landed in the next
        # song's window, side by side at low load). Forked, so the player built above and the music are shared.
        ctx = multiprocessing.get_context('fork')
        with concurrent.futures.ProcessPoolExecutor(WIDTH, mp_context=ctx) as pool:
            jobs = [pool.submit(chain, i, c, player, songs, tmp) for i, c in enumerate(chains)]
            runs = [(f, pool.submit(timed, check_protocol, player, songs, tmp, f)) for f in fixtures]
            for c, job in zip(chains, jobs):
                results = job.result()
                if all(ok for _, ok, _, _ in results):
                    for result in results: report(*result)
                else:
                    again.append((c, results))
            for f, r in runs:
                ok, text, secs = r.result()
                if ok: report(f'protocol {f.stem}', ok, text, secs)
                else: again.append((f, [(f'protocol {f.stem}', ok, text, secs)]))
        print(f'side by side: {time.monotonic() - start:.1f} s', flush=True)
        # A failure side by side runs again alone, as gate.run does a suite's: passing then, it is flaky (said, and
        # counted as passed); failing again, it fails.
        side = []  # what failed side by side and passed alone: counted in the summary, not hidden
        for i, (what, first) in enumerate(again):
            if isinstance(what, list):
                second = chain(f'a{i}', what, player, songs, tmp)
            else:
                (tmp/f'a{i}').mkdir()
                second = [(f'protocol {what.stem}', *timed(check_protocol, player, songs, tmp/f'a{i}', what))]
            for (name, ok1, text1, _), (_, ok, text, secs) in zip(first, second):
                if ok and not ok1:
                    print(f'flaky {name}: failed side by side ({text1[:200]}), passed alone', flush=True)
                    side.append(name)
                failed += report(name, ok, text, secs)
        for n in tests:
            if n in SERIAL:
                failed += report(n, *alone(n, player, songs, tmp))
        for name, why in KNOWN.items():
            if only in 'protocol-' + name: print(f'skip protocol {name}: {why}')
    print(f'all: {time.monotonic() - start:.1f} s; {failed} failed; {len(side)} failed side by side and passed alone'
          + (f': {", ".join(side)}' if side else ''), flush=True)
    sys.exit(1 if failed else 0)


if __name__ == '__main__':
    main()
