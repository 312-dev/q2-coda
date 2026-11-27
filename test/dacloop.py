#!/usr/bin/env python3
"""q2dacloop (dacloop/q2dacloop.c, docs/dacloop.md) built for this machine against its libasound and run between two
of alsa-lib's file PCMs: the capture side reads a file of 32-bit stereo frames, as the USB gadget's capture would give
what a computer plays, and the playback side writes what reaches the DAC to another. The computer's audio arrives bit
for bit, after a prebuffer sized by the rate; a rate change opens both again at the new rate; a computer that stops
streaming closes them; SIGTERM ends it. The fill level's controller (dacloop/pitch.h) runs in test/dacloop_pitch.c's
simulation against a computer whose clock is off, and the loop's pitch through --pitch-file. Run it as
python3 test/dacloop.py (pytest). Q2DACLOOP_SOURCE builds another copy of q2dacloop.c and Q2DACLOOP_PITCH of pitch.h,
to show a broken one fails here."""
import os
import pathlib
import shutil
import struct
import subprocess
import sys
import time

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SOURCE = pathlib.Path(os.environ.get('Q2DACLOOP_SOURCE') or ROOT/'dacloop/q2dacloop.c')
PITCH = pathlib.Path(os.environ.get('Q2DACLOOP_PITCH') or ROOT/'dacloop/pitch.h')
LIBASOUND = next(iter(sorted(pathlib.Path('/usr/lib').glob('*-linux-gnu/libasound.so.2'))), None)


@pytest.fixture(scope='session')
def binary(tmp_path_factory):
    if not LIBASOUND:
        pytest.skip('no libasound.so.2 on this machine')
    out = tmp_path_factory.mktemp('dacloop')
    shutil.copy(SOURCE, out/'q2dacloop.c')
    if PITCH.exists():
        shutil.copy(PITCH, out/'pitch.h')
    subprocess.run(['cc', '-std=c11', '-Wall', '-Wextra', '-Werror', '-O2', f'-I{ROOT/"player"}', '-o', str(out/'q2dacloop'),
                    str(out/'q2dacloop.c'), str(LIBASOUND)], check=True)
    return out/'q2dacloop'


def frames(n, start=0):
    """n stereo S32_LE frames, each sample its own number, so a lost, doubled or reordered frame shows"""
    return b''.join(struct.pack('<ii', 2 * i, 2 * i + 1) for i in range(start, start + n))


class Feed:
    """the gadget's capture: a FIFO the file PCM reads its frames from, so the loop goes no faster than the test feeds
    it (alsa-lib's null PCM has no clock of its own)"""
    def __init__(self, path):
        os.mkfifo(path)
        self.fd = os.open(path, os.O_RDWR)
        self.sent = b''

    def send(self, b):
        os.write(self.fd, b)
        self.sent += b


@pytest.fixture
def loop(binary, tmp_path):
    feed = Feed(tmp_path/'in.fifo')
    (tmp_path/'.asoundrc').write_text(
        f'pcm.cap {{ type file slave.pcm "null" file "/dev/null" infile "{tmp_path}/in.fifo" format "raw" }}\n'
        f'pcm.play {{ type file slave.pcm "null" file "{tmp_path}/out.raw" format "raw" }}\n')
    rate = tmp_path/'rate'
    procs = []

    def go(hz, *extra):
        rate.write_text(f'{hz}\n')
        p = subprocess.Popen([str(binary), '--capture', 'cap', '--playback', 'play', '--rate-file', str(rate), *extra],
                             stderr=subprocess.PIPE, text=True, env=dict(os.environ, HOME=str(tmp_path)))
        procs.append(p)
        return p
    yield tmp_path, rate, go, feed
    for p in procs:
        if p.poll() is None:
            p.kill()
            p.wait()
    os.close(feed.fd)


def feed_for(feed, seconds, hz, start=0):
    """about seconds of frames at hz, in pieces of whole 1024-frame reads (alsa-lib's file PCM fills a read from its
    file with one read() and leaves the rest of a short one as it was, so a piece cut mid-read would show here as a
    repeat the loop never made); the next frame's number"""
    step = max(1024, hz // 20 // 1024 * 1024)
    for _ in range(max(1, round(seconds * hz / step))):
        feed.send(frames(step, start))
        start += step
        time.sleep(step / hz)
    return start


def end(p):
    p.send_signal(15)
    out = p.communicate(timeout=10)[1]
    return p.returncode, out


def test_the_computer_s_audio_reaches_the_dac_bit_for_bit(loop):
    root, rate, go, feed = loop
    p = go(96000)
    n = feed_for(feed, 1.0, 96000)
    rate.write_text('0\n')  # the computer stops streaming: the session closes at the next look
    feed_for(feed, 0.05, 96000, n)  # one last piece, which the PCM reads before its look; then none
    time.sleep(0.5)
    code, log = end(p)
    out = (root/'out.raw').read_bytes()
    first = next((i for i in range(0, min(len(out), len(feed.sent)), 8) if out[i:i + 8] != feed.sent[i:i + 8]), None)
    assert code == 0 and len(out) >= n * 8 and first is None, (log, first, len(out), len(feed.sent), out[first:first + 32] if first is not None else b'')
    assert 'session 96000 Hz: cap to play' in log and 'prebuffered 9600 frames (100 ms), feeding' in log
    assert 'rate 96000 -> 0' in log and 'session 96000 Hz closed' in log


def test_a_rate_change_opens_both_again_with_its_own_prebuffer(loop):
    root, rate, go, feed = loop
    p = go(44100)
    n = feed_for(feed, 1.0, 44100)
    rate.write_text('192000\n')
    n = feed_for(feed, 0.5, 44100, n)
    feed_for(feed, 1.0, 192000, n)
    code, log = end(p)
    assert code == 0, log
    assert 'prebuffered 4410 frames (100 ms)' in log and 'rate 44100 -> 192000' in log
    assert 'session 192000 Hz: cap to play' in log and 'prebuffered 38400 frames (200 ms)' in log


def test_nothing_opens_while_the_computer_is_not_streaming(loop):
    root, rate, go, feed = loop
    p = go(0)
    time.sleep(0.8)
    code, log = end(p)
    assert code == 0 and 'session' not in log and not (root/'out.raw').exists()



# ---- the fill level (docs/dacloop.md#holding-the-fill-level) ----

@pytest.fixture(scope='session')
def sim(tmp_path_factory):
    out = tmp_path_factory.mktemp('pitch')
    shutil.copy(PITCH, out/'pitch.h')
    shutil.copy(ROOT/'test/dacloop_pitch.c', out/'dacloop_pitch.c')
    subprocess.run(['cc', '-std=c11', '-Wall', '-Wextra', '-Werror', '-O2', '-o', str(out/'sim'), str(out/'dacloop_pitch.c')], check=True)

    def run(*args):
        got = subprocess.run([str(out/'sim'), *map(str, args)], capture_output=True, text=True, check=True).stdout.split()
        return {k: float(v) for k, v in zip(got[::2], got[1::2])}
    return run


@pytest.mark.parametrize('hz', [44100, 96000, 384000])
@pytest.mark.parametrize('ppm', [-500, -100, 0, 100, 500])
def test_the_fill_level_is_held_against_a_computer_s_clock(sim, hz, ppm):
    """half an hour of a computer off by ppm (USB allows 500): the fill never strays 20 ms from the prebuffer's, and over
    the last ten minutes it sits on it, within the measurement's own noise (half a 1024-frame read)"""
    r = sim(hz, ppm, 1800)
    assert -20 < r['min'] and r['max'] < 20, r
    assert abs(r['tail_mean']) < 0.5 and r['tail_max'] < 512 / hz * 1000 + 1, r
    assert abs(r['pitch'] - 1e12 / (1e6 + ppm)) < 10, f'the integral has learned the computer\'s error: {r}'


def test_a_control_that_only_speeds_up_still_holds_a_slow_computer(sim):
    """a range above nominal alone (the control's own min and max are the kernel's): a slow computer is still caught"""
    r = sim(96000, -300, 1800, 1000000, 1005000)
    assert -20 < r['min'] and r['max'] < 20 and abs(r['tail_mean']) < 0.5, r



def test_a_jump_in_the_fill_with_a_narrow_control_does_not_overshoot(sim):
    """the fill 50 ms over at once (a burst of packets, an xrun's recovery) with a control of 100 ppm either way: held
    at its limit the while, the loop comes back to the target without swinging past it (an integral that kept growing
    at the limit would carry it 39 ms below)"""
    r = sim(96000, 0, 1800, 999900, 1000100, 50)
    assert r['min'] > -5 and abs(r['tail_mean']) < 0.5, r

def test_the_loop_sets_the_pitch_nominal_then_each_second_then_nominal(loop):
    root, rate, go, feed = loop
    pitch = root/'pitch'
    p = go(96000, '--pitch-file', str(pitch))
    seen = []
    n = 0
    for _ in range(12):  # three seconds of the computer's audio, the pitch file read between pieces
        n = feed_for(feed, 0.25, 96000, n)
        if pitch.exists() and (v := pitch.read_text().strip()) and (not seen or seen[-1] != v):
            seen.append(v)
    code, log = end(p)
    assert code == 0, log
    assert seen and seen[0] == '1000000', f'nominal at the start: {seen}'
    # alsa-lib's null PCMs have no clock, so the fill read here is no computer's: only that it is stepped, within
    # pitch.h's span (PITCH_SPAN_PPM), is checked; the controller's own behaviour is the simulation's
    assert len(seen) > 1 and all(998000 <= int(v) <= 1002000 for v in seen), f'a step a second, within the span: {seen}'
    assert pitch.read_text().strip() == '1000000' and 'back to 1000000' in log, 'nominal again at the end'


def test_without_the_control_the_loop_plays_as_before(loop):
    root, rate, go, feed = loop
    p = go(96000)
    feed_for(feed, 1.0, 96000)
    code, log = end(p)
    assert code == 0 and 'no Capture Pitch 1000000: the fill level is not held' in log and 'prebuffered 9600' in log, log

if __name__ == '__main__':
    sys.exit(pytest.main([__file__, '-q', '-p', 'no:cacheprovider']))
