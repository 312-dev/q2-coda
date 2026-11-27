#!/usr/bin/env python3
"""q2dacloop (dacloop/q2dacloop.c, docs/dacloop.md) built for this machine against its libasound and run between two
of alsa-lib's file PCMs: the capture side reads a file of 32-bit stereo frames, as the USB gadget's capture would give
what a computer plays, and the playback side writes what reaches the DAC to another. The computer's audio arrives bit
for bit, after a prebuffer sized by the rate; a rate change opens both again at the new rate; a computer that stops
streaming closes them; SIGTERM ends it. Run it as python3 test/dacloop.py (pytest). Q2DACLOOP_SOURCE builds another
copy, to show a broken one fails here."""
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
LIBASOUND = next(iter(sorted(pathlib.Path('/usr/lib').glob('*-linux-gnu/libasound.so.2'))), None)


@pytest.fixture(scope='session')
def binary(tmp_path_factory):
    if not LIBASOUND:
        pytest.skip('no libasound.so.2 on this machine')
    out = tmp_path_factory.mktemp('dacloop')
    shutil.copy(SOURCE, out/'q2dacloop.c')
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

    def go(hz):
        rate.write_text(f'{hz}\n')
        p = subprocess.Popen([str(binary), '--capture', 'cap', '--playback', 'play', '--rate-file', str(rate)],
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


if __name__ == '__main__':
    sys.exit(pytest.main([__file__, '-q', '-p', 'no:cacheprovider']))
