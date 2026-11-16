"""tools/splash.py's file format, and patch/splash.c playing it: built for this machine with
test/unit/splash_host.c as its framebuffer and clock, checked against the Python decoder."""
import ctypes
import pathlib
import random
import struct
import subprocess

import pytest

import splash

ROOT = pathlib.Path(__file__).resolve().parents[2]
N = splash.W * splash.H


def lut(level):
    return 0xff000000 | (level * 0xf2 + 7) // 15 << 16 | (level * 0xf2 + 7) // 15 << 8 | (level * 0xf0 + 7) // 15


def frames(count, seed=1):
    """count frames of random strokes: runs of every length, some one pixel apart, some at the edges."""
    rng, cur, out = random.Random(seed), bytearray(N), []
    for _ in range(count):
        for _ in range(40):
            at, n = rng.randrange(N), rng.choice((1, 2, 3, 7, 200, 5000))
            for i in range(at, min(N, at + n), rng.choice((1, 1, 2, 5))):
                cur[i] = rng.randrange(16)
        cur[0], cur[N - 1] = rng.randrange(16), rng.randrange(16)
        out.append(bytes(cur))
    return out


@pytest.fixture(scope='module')
def host(tmp_path_factory):
    so = tmp_path_factory.mktemp('splash') / 'splash_host.so'
    subprocess.run(['clang', '-shared', '-fPIC', '-O1', '-Wall', '-Werror', str(ROOT / 'test/unit/splash_host.c'), '-o', str(so)], check=True)
    return ctypes.CDLL(str(so))


def play(host, blob, gone=-1, refuse=0, mark=None):
    """(exit status, page 0's words, frames waited for, the clock in microseconds) after playing blob. gone: the
    frame before which demo removes the marker; refuse: 1 its open, 2 its lock, 3 its rename. mark, a list, gets
    [frames drawn without the marker there and locked, its unlinks, whether it is left]."""
    page, waits, clock, marks = (ctypes.c_uint * N)(), ctypes.c_long(), ctypes.c_uint(), (ctypes.c_int * 3)()
    status = host.splash_run(blob, ctypes.c_long(len(blob)), ctypes.c_long(gone), refuse, page, ctypes.byref(waits),
                             ctypes.byref(clock), marks)
    if mark is not None:
        mark[:] = list(marks)
    return status, list(page), waits.value, clock.value


def test_round_trip():
    fs = frames(6)
    fps, back = splash.unpack(splash.pack(fs, 30))
    assert fps == 30 and back == fs


def test_trailing_repeats_are_dropped_but_not_inner_ones():
    a, b = frames(2)
    fps, back = splash.unpack(splash.pack([a, a, b, b, b], 24))
    assert back == [a, a, b]


def test_an_unchanged_frame_costs_its_count_alone():
    a, b = frames(2)
    blob = splash.pack([a, a, b], 30)
    first, = struct.unpack_from('<I', blob, 12)
    assert struct.unpack_from('<I', blob, 16 + first) == (0,)


def test_committed_animation_decodes():
    fps, fs = splash.unpack((ROOT / 'assets/boot-anim.q2a').read_bytes())
    assert fps == 30 and len(fs) > 100
    assert not any(fs[0]) and max(fs[-1]) == 15  # from black to the lit emblem


def test_player_draws_the_last_frame(host):
    fs, mark = frames(5), []
    status, fb, waits, clock = play(host, splash.pack(fs, 30), mark=mark)
    assert status == 0 and fb == [lut(v) for v in fs[-1]]
    assert waits == 5 and clock == 5 * 1000000 // 30  # one frame time each
    assert mark == [0, 1, 0]  # every frame under the lock demo waits on, the marker removed at the end


def test_player_stops_when_demo_has_waited_its_longest(host):
    fs, mark = frames(5), []
    status, fb, waits, _ = play(host, splash.pack(fs, 30), 2, mark=mark)
    assert status == 0 and waits == 2 and fb == [lut(v) for v in fs[1]]
    assert mark == [0, 0, 0]  # demo removed it


@pytest.mark.parametrize('refuse', (1, 2, 3))
def test_player_fails_without_drawing_when_demo_could_not_wait_for_it(host, refuse):
    mark = []
    status, fb, waits, _ = play(host, splash.pack(frames(2), 30), refuse=refuse, mark=mark)
    assert status == 1 and waits == 0 and not any(v & 0xffffff for v in fb) and mark == [0, 0, 0]


def test_player_fails_without_drawing_on_a_bad_file(host):
    good = splash.pack(frames(2), 30)
    wrong_size = good[:8] + struct.pack('<2H', 375, 320) + good[12:]
    for blob in (b'', b'Q2AN', b'JFIF' + good[4:], wrong_size, good[:4] + b'\0\0' + good[6:]):
        status, fb, waits, _ = play(host, blob)
        assert status == 1 and waits == 0 and not any(v & 0xffffff for v in fb)


def test_player_stays_inside_the_page_on_a_run_past_its_end(host):
    frame = splash.varint(N - 2) + splash.varint(6) + bytes(3)
    blob = splash.MAGIC + struct.pack('<4H', 30, 1, splash.W, splash.H) + struct.pack('<I', len(frame)) + frame
    mark = []
    status, fb, waits, _ = play(host, blob, mark=mark)
    assert status == 1 and waits == 0  # host exits 3 if anything outside page 0's rows was written
    assert mark == [0, 1, 0]  # demo need not wait for it


def test_player_keeps_what_it_drew_when_the_file_is_cut_short(host):
    fs = frames(3)
    blob = splash.pack(fs, 30)
    first, = struct.unpack_from('<I', blob, 12)
    status, fb, waits, _ = play(host, blob[:16 + first + 6])
    assert status == 0 and waits == 1 and fb == [lut(v) for v in fs[0]]
