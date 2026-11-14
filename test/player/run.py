#!/usr/bin/env python3
"""Host tests of q2player (player/): `python3 test/player/run.py [-k NAME]`.

Needs ffmpeg and lame, no stock ZIP and no Q2. The player is built for this machine by tools/player.py
(its FFmpeg on first use, some minutes) and driven through its socket by fakedemo.py, writing PCM to
files in place of ALSA.

- exact: what reaches the output is, bit for bit, the reference decode (the system's ffmpeg with its
  assembly off): two FLACs and two MP3s through a preloaded change come out as one stream with no
  sample added or lost at the cut, and a 24-bit 96 kHz FLAC as S32.
- formats: each output is opened in the width stock opens for the same file.
- protocol: for each transcript in fixtures/ (stock's and Q2 Pod's hciplayer under qemu, captured by
  fakedemo.py), the same commands get the same events and answers from q2player.
"""
import array, concurrent.futures, math, pathlib, re, struct, subprocess, sys, tempfile

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


def play(player, songs, scenario, out, speed=1):
    s = fakedemo.Session(out)
    s.music = songs
    s.run(fakedemo.scenarios(str(songs))[scenario], fakedemo.launch_host(player, str(songs), speed), speed)
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
    s, pcm, names = play(player, songs, scenario, tmp/scenario, speed=8)
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
    s, pcm, names = play(player, songs, scenario, tmp/scenario, speed=8)
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
    return f'{len(want) // 4} frames from frame {7 * 44100}'


def test_seek_exact(player, songs, tmp):
    """A seek to 9 s: what follows it is the reference from frame 9 x 44100, to the end; what came before is the
    reference's start."""
    s, pcm, names = play(player, songs, 'seek', tmp/'seek', speed=8)
    got, ref = b''.join(pcm), reference(songs/'a1.flac')
    tail = ref[9 * 44100 * 4:]
    assert got.endswith(tail), f'the PCM does not end with the reference from 9 s ({len(got) // 4} frames)'
    head = got[:len(got) - len(tail)]
    assert ref.startswith(head) and len(head) > 0, f'{len(head) // 4} frames before the seek are not the reference start'
    return f'{len(head) // 4} frames, then {len(tail) // 4} from frame {9 * 44100}'


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
    pathlib.Path('/tmp/notsupport').unlink(missing_ok=True)
    s, pcm, names = play(player, songs, 'badnext', tmp/'badnext', speed=8)
    assert b''.join(pcm).endswith(reference(songs/'a1.flac')[12 * 44100 * 4:]), 'the song did not play to its end'
    events = [l.split()[-1] for l in s.lines if 'exit}' in l or 'error}' in l]
    assert events == ['{player-status\\exit}', '{player-status\\error}'], events
    assert pathlib.Path('/tmp/notsupport').exists(), '/tmp/notsupport not made'
    return 'played to the end, then exit, error and /tmp/notsupport'


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
    s, pcm, names = play(player, songs, 'outrun', tmp/'outrun', speed=4)
    a1, a2 = reference(songs/'a1.flac'), reference(songs/'a2.flac')
    got = b''.join(pcm)
    assert got.endswith(a1[12 * 44100 * 4:] + a2), 'not a1 to its end, then the whole of a2'
    events = [(float(l.split()[0]), l.split()[-1]) for l in s.lines if 'exit}' in l]
    assert [e for _, e in events] == ['{player-status\\exit}'] * 2 and events[0][0] < 12, events
    return f'exit at {events[0][0]:.1f} s, before the file arrived at 12 s; then all {len(a2) // 4} frames of it'


def test_exits_apart(player, songs, tmp):
    """A preloaded file with no audio ends as it starts: its exit comes at least 300 ms after the one before."""
    s, pcm, names = play(player, songs, 'emptynext', tmp/'emptynext')
    t = [float(l.split()[0]) for l in s.lines if l.endswith('{player-status\\exit}')]
    assert len(t) == 2 and t[1] - t[0] >= 0.3, t
    return f'exits {t[1] - t[0]:.3f} s apart'


def test_formats_open_as_stock(player, songs, tmp):
    """Stock's ALSA requests for the same scenario (the emulator shim's log): S32 for 24-bit FLAC, FLOAT for AAC and Vorbis.
    The Vorbis file follows the AAC one in the same PCM, which stock reopens (docs/player-plan.md#deviations)."""
    s, pcm, names = play(player, songs, 'formats', tmp/'formats')
    assert names == ['0000_96000_2_s32.raw', '0001_44100_2_f32.raw'], names
    return ' '.join(names)


# ---- the DSP (phase 3)

def tool(tmp, name, *defines):
    """test/player/NAME.c with player/dsp.c and patch/peq.c as the player builds them, for this machine."""
    out = tmp/name
    if not out.exists():
        subprocess.run(['cc', '-std=c11', '-D_GNU_SOURCE', '-DPEQ_HOST', *defines, '-O2', '-Wall', '-Wextra', '-Werror', '-Wno-unused-parameter',
                        '-I', ROOT/'player', '-I', ROOT/'patch', HERE/f'{name}.c', ROOT/'player/dsp.c', ROOT/'patch/peq.c',
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
    s, pcm, names = play(player, songs, 'xfade', tmp/'xfade', speed=8)
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
    what was playing; nothing while paused or after the stop. Into a full-scale square, the click makes room: the
    mix stays inside 16 bits, as y = x (1 - e) + s."""
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
    zeros are q2player's, a deviation."""
    out = []
    for t, d, name, arg in lines:
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
    'stock-transport': 'stock ends this FLAC half a second after a seek to 15 s of its 20; Q2 Pod fixes it and so does q2player',
    'stock-gapless-flac': 'the same: after the seek to 12 s stock ends the song at 15 s and is into the next by 6 s',
    'stock-start': 'the same fault after a start at 7 s: stock ends the song at 11 s',
    'stock-seek': 'the same after a seek to 9 s: stock ends the song at 13 s',
    'stock-cancel': 'the same after the seek to 12 s, before the preload is cancelled',
    'stock-emptynext': 'the same after the seek to 14 s, before the preload is sent',
    'stock-badnext': 'stock stops the song when its preload is not audio; Q2 Pod plays it to its end, and so does q2player',
    'stock-formats-all': 'compared per file by test_media_info_per_format, with its deviations',
    'pod-formats-all': 'compared per file by test_media_info_per_format, with its deviations',
}


# Where q2player's media-info differs from Q2 Pod's hciplayer's for a file of formats-all: docs/player-plan.md#deviations.
MEDIA_INFO_DEVIATIONS = {
    'k24.wv': 'BITS is the file\'s 24, where stock says 32',
    'z.dts': 'it plays; stock refuses this file (FFmpeg\'s own DTS encoder\'s) with an error',
}


def test_media_info_per_format(player, songs, tmp):
    """formats-all: for each file, the media-info Q2 Pod's hciplayer gave (fixtures/pod-formats-all.txt): RATE, LENGTH,
    CODEC and BITS the same and BITRATE within 2%, but for MEDIA_INFO_DEVIATIONS."""
    def per_file(lines):
        out = {}
        for t, d, name, arg in lines:
            i = int((t - 1.0) // 2.5)
            if d == '<' and name == 'media-info' and '|' in arg and 0 <= i < len(fakedemo.ALL_FORMATS):
                out.setdefault(fakedemo.ALL_FORMATS[i], arg.split('|'))
        return out
    want = per_file(parse((HERE/'fixtures/pod-formats-all.txt').read_text())[0])
    play(player, songs, 'formats-all', tmp/'formats-all', speed=4)
    got = per_file(parse((tmp/'formats-all/transcript.txt').read_text())[0])
    # a transcript writes an answer only when it changes: a file with none of its own answered as the one before
    for d in want, got:
        for a, b in zip(fakedemo.ALL_FORMATS, fakedemo.ALL_FORMATS[1:]): d.setdefault(b, d.get(a))
    bad = []
    for f in fakedemo.ALL_FORMATS:
        w, g = want.get(f), got.get(f)
        same = w and g and w[1:3] + w[4:] == g[1:3] + g[4:] and abs(int(w[0]) - int(g[0])) <= max(2000, int(w[0]) // 50)
        if bool(same) == (f in MEDIA_INFO_DEVIATIONS): bad.append(f'{f}: q2player {g}, Q2 Pod {w}')
    assert not bad, '\n  ' + '\n  '.join(bad)
    return f'{len(fakedemo.ALL_FORMATS)} files, {len(MEDIA_INFO_DEVIATIONS)} deviations as listed'


def check_protocol(player, songs, tmp, fixture):
    name = fixture.stem
    scenario = name.split('-', 1)[1]
    want, want_pos = parse(fixture.read_text())
    s, _, _ = play(player, songs, scenario, tmp/f'p-{name}')
    got, got_pos = parse((tmp/f'p-{name}/transcript.txt').read_text())
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


def main():
    only = sys.argv[sys.argv.index('-k') + 1] if '-k' in sys.argv else ''
    player = subprocess.run([sys.executable, ROOT/'tools/player.py', 'build', 'host'], check=True, capture_output=True, text=True).stdout.strip()
    songs = music.make(CACHE/'music')
    failed = 0
    with tempfile.TemporaryDirectory(prefix='q2player-') as t:
        tmp = pathlib.Path(t)
        tests = [(n, f) for n, f in globals().items() if n.startswith('test_') and only in n]
        for n, f in tests:
            try: print(f'ok   {n}: {f(player, songs, tmp)}')
            except AssertionError as e: failed += 1; print(f'FAIL {n}: {e}')
        fixtures = [f for f in sorted((HERE/'fixtures').glob('*.txt')) if only in 'protocol-' + f.stem and f.stem not in KNOWN]
        with concurrent.futures.ThreadPoolExecutor(8) as pool:  # each plays in real time, so side by side
            for f, r in [(f, pool.submit(check_protocol, player, songs, tmp, f)) for f in fixtures]:
                try: print(f'ok   protocol {f.stem}: {r.result()}')
                except AssertionError as e: failed += 1; print(f'FAIL protocol {f.stem}: {e}')
        for name, why in KNOWN.items():
            if only in 'protocol-' + name: print(f'skip protocol {name}: {why}')
    sys.exit(1 if failed else 0)


if __name__ == '__main__':
    main()
