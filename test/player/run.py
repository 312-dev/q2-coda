#!/usr/bin/env python3
"""Host tests of q2player (player/): `python3 test/player/run.py [-k NAME]`.

Needs ffmpeg, lame, openssl (the HTTPS tests' certificates) and a C compiler, no stock ZIP and no Q2; the DST
SACD image needs FFmpeg's DST sample, fetched once (music.py DST_SAMPLE), and is left out without it. The player is built for this machine by tools/player.py
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


def play(player, songs, scenario, out, speed=1, env=None):
    s = fakedemo.Session(out)
    s.music = songs
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
    if not out.exists() and name == 'volume_test':  # output.c's law alone, with FFmpeg's headers for its includes
        ff = pathlib.Path(subprocess.run([sys.executable, ROOT/'tools/player.py', 'ffmpeg', 'host'], check=True, capture_output=True, text=True).stdout.strip())
        subprocess.run(['cc', '-std=c11', '-D_GNU_SOURCE', '-O2', '-Wall', '-Werror', '-I', ROOT/'player', '-I', ff/'include', HERE/'volume_test.c',
                        ROOT/'player/output.c', '-L', ff/'lib', '-lswresample', '-lavutil', '-lm', '-lpthread', '-o', out], check=True)
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


def check_bt_split(player, songs, tmp, pair, whole, source, rate, frames):
    """Bluetooth: a tone cut in two and played through a preloaded change comes out as the whole file resampled, in
    one PCM at the transport's rate, and that is FFmpeg 7.1.5's libswresample with lavcresample's settings, its tail
    flushed. docs/internals.md#bluetooth-resampler"""
    env = {'Q2PLAYER_BT_RATE': str(rate)}
    _, a, an = play(player, songs, pair, tmp/f'{pair}-{rate}', speed=4, env=env)
    _, b, bn = play(player, songs, whole, tmp/f'{whole}-{rate}', speed=4, env=env)
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


def test_bt_formats_and_volume(player, songs, tmp):
    """Bluetooth (the 'bt' scenario, as stock's capture of it): each song in its own format at the transport's 48 kHz,
    and the volume's levels in turn, measured against the song: 1, 25/256, 10/256, 484/256 (clipped), 163/256 left
    with the right silent. Stock's right channel there is about 8.4 times (its dB of a negative level)."""
    s, pcm, names = play(player, songs, 'bt', tmp/'bt')
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


def test_click_volume(player, songs, tmp):
    """A click where the volume is in software (a USB DAC at 50: 25/256) is scaled with what plays, within a step."""
    s, pcm, names = play(player, songs, 'click-usb', tmp/'click-usb')
    assert names == ['0000_44100_2_s16_usb.raw'], names
    x = array.array('h', pcm[0])
    at = next(i for i, v in enumerate(x) if v)
    want = [max(-32768, min(32767, round(c * 25 / 256 * 32768))) for _, c in click_sound('wheel')]
    got = list(x[at - at % 2::2][:len(want)])
    lead = next(i for i, w in enumerate(want) if w)  # the table's first frames round to 0 at this level
    far = max(abs(a - b) for a, b in zip(got, want[lead:]))
    assert far <= 1, f'the click is {far} steps from the table at 25/256'
    return f'{len(want)} frames within a step of the click at 25/256'


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
    end, and the song ends with exit (stock and Q2 Pod end it at the seek, as they end a file's: a deviation)."""
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
    return 'error'


def test_https_checks(player, songs, tmp):
    """HTTPS against the CA bundle: a certificate the bundle's CA signed for localhost plays; one it signed for another
    name and a self-signed one are refused (error, nothing played). Stock plays all three, Q2 Pod's player the first
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
    s, pcm, names = play(player, songs, 'qq', tmp/'qq', env=env)
    got, ref = b''.join(pcm), reference(songs/'a1.flac')
    assert got == ref, f'{len(got) // 4} frames, reference {len(ref) // 4}, first difference {first_difference(got, ref)}'
    s, pcm, names = play(player, songs, 'qq-seek', tmp/'qq-seek', env=env)
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
    cache thread last ran (stock and Q2 Pod's answer 9 and 0 to the same stream), demo only asks whether it is above 0
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
    'stock-transport': 'stock ends this FLAC half a second after a seek to 15 s of its 20; Q2 Pod fixes it and so does q2player',
    'stock-gapless-flac': 'the same: after the seek to 12 s stock ends the song at 15 s and is into the next by 6 s',
    'stock-start': 'the same fault after a start at 7 s: stock ends the song at 11 s',
    'stock-seek': 'the same after a seek to 9 s: stock ends the song at 13 s',
    'stock-cancel': 'the same after the seek to 12 s, before the preload is cancelled',
    'stock-emptynext': 'the same after the seek to 14 s, before the preload is sent',
    'stock-badnext': 'stock stops the song when its preload is not audio; Q2 Pod plays it to its end, and so does q2player',
    'stock-formats-all': 'compared per file by test_media_info_per_format, with its deviations',
    'pod-formats-all': 'compared per file by test_media_info_per_format, with its deviations',
    'stock-dsd-2-2': 'Native DSD on a USB DAC: stock asks ALSA for format 31, SND_PCM_FORMAT_SPECIAL, which is refused, and plays '
                     'nothing; q2player sends DSD_U32_BE (test_native_usb)',
    'pod-dsd-2-2': 'the same in Q2 Pod\'s hciplayer',
    # streams (phase 5)
    'stock-http-slow': 'a WAV from a server slower than it plays: stock starts 10 s on, after its demuxer has looked at the '
                       'file\'s end over two more connections; q2player starts once the first fill is in (test_cache_first_fill)',
    'pod-http-slow': 'the same in Q2 Pod\'s hciplayer',
    'stock-http-big': 'the same at 4096 KB: stock starts 9 s on', 'pod-http-big': 'the same in Q2 Pod\'s hciplayer',
    'stock-http-stall': 'the same: stock starts after the server\'s 8 s stall, q2player before it and waits in it (test_http_stall)',
    'pod-http-stall': 'the same in Q2 Pod\'s hciplayer',
    'stock-http-gapless': 'stock ends a FLAC stream half a second after a seek near its end, as it ends a file; Q2 Pod\'s fix '
                          'is for files alone, so its player does too; q2player plays on (test_http_seek)',
    'pod-http-gapless': 'the same in Q2 Pod\'s hciplayer',
    'stock-http-norange': 'the same after a seek to 12 s of a server without ranges', 'pod-http-norange': 'the same in Q2 Pod\'s hciplayer',
    'stock-https-wrong': 'stock checks no certificate and plays; q2player refuses a certificate for another name (test_https_checks)',
    'pod-https-wrong': 'Q2 Pod\'s hciplayer checks the chain, not the name, and plays; q2player refuses it',
    'stock-https-self': 'stock plays a self-signed certificate; q2player refuses it, as Q2 Pod\'s does',
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
