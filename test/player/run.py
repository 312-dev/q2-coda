#!/usr/bin/env python3
"""Host tests of q2player (player/): `python3 test/player/run.py [-k NAME]`.

Needs ffmpeg and lame, no stock ZIP and no Q2. The player is built for this machine by tools/player.py
(its FFmpeg on first use, some minutes) and driven through its socket by fakedemo.py, writing PCM to
files in place of ALSA.

- exact: what reaches the output is, bit for bit, the reference decode (the system's ffmpeg with its
  assembly off): two FLACs and two MP3s through a preloaded change come out as one stream with no
  sample added or lost at the cut, and a 24-bit 96 kHz FLAC as S32.
- protocol: for each transcript in fixtures/ (stock's and Q2 Pod's hciplayer under qemu, captured by
  fakedemo.py), the same commands get the same events and answers from q2player.
"""
import array, concurrent.futures, pathlib, re, subprocess, sys, tempfile

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(HERE))
import fakedemo, music  # noqa: E402

CACHE = pathlib.Path.home()/'.cache/q2-pytest/player'


def reference(path, fmt='s16le'):
    """path decoded by the system's ffmpeg in C alone, as raw PCM: no dither, no resampling."""
    return subprocess.run(['ffmpeg', '-v', 'error', '-cpuflags', '0', '-i', str(path), '-f', fmt, '-'], check=True, capture_output=True).stdout


def play(player, songs, scenario, out, speed=1):
    s = fakedemo.Session(out)
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
    return check_exact(player, songs, tmp, 'gapless-flac-full', ['a1.flac', 'a2.flac'], 's16le', '0000_44100_2_2.raw')


def test_exact_flac_is_the_source(player, songs, tmp):
    """The two FLACs are one WAV cut in two: joined they are that WAV's samples."""
    whole = reference(songs/'whole.wav')
    parts = reference(songs/'a1.flac') + reference(songs/'a2.flac')
    assert whole == parts and len(whole) == 40 * 44100 * 4
    return '1764000 frames'


def test_exact_mp3_gapless(player, songs, tmp):
    r = check_exact(player, songs, tmp, 'gapless-mp3-full', ['b1.mp3', 'b2.mp3'], 's16le', '0000_44100_2_2.raw')
    # each half is exactly its 20 s: LAME's delay and padding are gone
    assert len(reference(songs/'b1.mp3')) == len(reference(songs/'b2.mp3')) == 20 * 44100 * 4
    return r


def test_exact_mp3_no_step_at_the_cut(player, songs, tmp):
    """The signal is smooth, so samples dropped or repeated at the cut show as a step in the second difference there.
    The two halves were encoded apart, so the cut is a little rougher than its surroundings; five frames dropped
    there, tried here on a copy, is several times rougher still."""
    pcm = (tmp/'gapless-mp3-full/pcm/0000_44100_2_2.raw').read_bytes()
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
    return check_exact(player, songs, tmp, 'hires', ['c96.flac'], 's32le', '0000_96000_2_4.raw')


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


def skeleton(lines):
    """What demo acts on, in order: each command, each event that ends a song, and each answer to a one-off query. The
    200 ms poll's answers are compared apart (answers), since when "ready" turns to "playing" is not exact."""
    out = []
    for t, d, name, arg in lines:
        if d == '>': out.append(f'> {name}')
        elif name in ('player-status', 'media-info') and arg in ('ready', 'playing', 'no-playing-status') or name == 'media-info': continue
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
    'stock-transport': 'stock ends a FLAC after a seek near its end; Q2 Pod fixes it and so does q2player',
    'stock-gapless-flac': 'the same seek',
    'stock-skip': 'the same seek',
    'stock-end': 'the same seek',
}


def check_protocol(player, songs, tmp, fixture):
    name = fixture.stem
    scenario = name.split('-', 1)[1]
    want, want_pos = parse(fixture.read_text())
    s, _, _ = play(player, songs, scenario, tmp/f'p-{name}')
    got, got_pos = parse((tmp/f'p-{name}/transcript.txt').read_text())
    norm = lambda sk: [x.replace(str(songs), '/mnt/mmc') for x in sk]
    a, b = norm(skeleton(got)), norm(skeleton(want))
    assert a == b, f'{name}: events differ\n  q2player: {a}\n  fixture:  {b}'
    ia, ib = answers(got), answers(want)
    assert [x[1:] for x in ia] == [x[1:] for x in ib], f'{name}: media-info differs\n  q2player: {ia}\n  fixture:  {ib}'
    for x, y in zip(ia, ib):
        assert abs(int(x[0]) - int(y[0])) <= max(2000, int(y[0]) // 50), f'{name}: bitrate {x[0]}, fixture {y[0]}'
    # positions: at each of the fixture's samples, within 2 s (stock starts about half a second later, and rounds down)
    for t, p in want_pos:
        near = [q for u, q in got_pos if abs(u - t) < 0.5]
        assert near and min(abs(q - p) for q in near) <= 2, f'{name}: at {t} s position {near}, fixture {p}'
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
