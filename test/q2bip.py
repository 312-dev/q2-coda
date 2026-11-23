#!/usr/bin/env python3
"""q2-bip, the process (patch/q2bip.c, docs/internals.md#bluetooth-cover-art), built for this machine. A UNIX packet
socket stands in for the L2CAP channel and a script for stock's ffmpeg: a controller connects, fetches the playing
song's cover by its handle, follows a track change and leaves; then the same through a real ffmpeg, whose JPEG must be
200 x 200 and 4:2:2; and on a kernel with Bluetooth sockets, the real listening socket (the PSM, Enhanced
Retransmission Mode, the security level). OBEX and BIP themselves are test/unit/test_q2bip.py's. Run it as
python3 test/q2bip.py (pytest, clang; ffmpeg for one test)."""
import ctypes
import errno
import os
import pathlib
import re
import shutil
import socket
import struct
import subprocess
import sys
import time

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
UUID = bytes.fromhex('7163DD544A7E11E2B47C0050C2490048')  # AVRCP 1.6's cover art target
CONNECT = bytes.fromhex('80 001a 10 00 ffff 46 0013') + UUID
CONNECTED = bytes.fromhex('a0 001f 10 00 03e8 cb 00000001 4a 0013') + UUID
CONN_ID = bytes.fromhex('cb 00000001')


def header(hid, value):
    return bytes([hid]) + struct.pack('>H', 3 + len(value)) + value


def parse(packet):
    """A response's headers {id: value}."""
    out, at = {}, 3
    while at < len(packet):
        hid = packet[at]
        n = {2: 2, 3: 5}.get(hid >> 6) or struct.unpack('>H', packet[at + 1:at + 3])[0]
        out[hid] = packet[at + (1 if hid >> 6 >= 2 else 3):at + n]
        at += n
    return out


def jpeg(n):
    return b'\xff\xd8' + bytes(i * 7 & 0xff for i in range(n - 4)) + b'\xff\xd9'


@pytest.fixture(scope='module')
def daemon_bin(tmp_path_factory):
    out = tmp_path_factory.mktemp('q2bip-bin') / 'q2-bip'
    subprocess.run(['clang', '-O1', '-Wall', '-Wextra', '-Werror', '-DCODA_HOST', ROOT / 'patch/q2bip.c',
                    ROOT / 'patch/q2bip_core.c', '-o', out], check=True)
    return out


class Daemon:
    """q2-bip with its files under tmp and ffmpeg a script that logs its arguments and writes the JPEG in `made`."""

    def __init__(self, binary, tmp, ffmpeg=None):
        self.tmp, self.now, self.log, self.made = tmp, tmp / 'now', tmp / 'ffmpeg.log', tmp / 'made.jpg'
        fake = tmp / 'ffmpeg'
        fake.write_text(f'#!/bin/sh\necho "$@" >> {self.log}\nfor last; do :; done\n'
                        f'[ -f {self.made} ] || exit 1\ncp {self.made} "$last"\n[ ! -f {tmp}/fails ] || exit 3\n')
        fake.chmod(0o755)
        self.sock, self.err = tmp / 'sock', tmp / 'stderr'
        env = dict(os.environ, Q2BIP_NOW=str(self.now), Q2BIP_TMP=str(tmp / 'out.jpg'), Q2BIP_FFMPEG=str(ffmpeg or fake))
        with open(self.err, 'wb') as err:  # demo's stderr file on a Q2, which q2guard reads into its logs
            self.proc = subprocess.Popen([binary, self.sock], env=env, stderr=err)
        for _ in range(100):
            if self.sock.exists():
                break
            time.sleep(0.02)

    def song(self, handle, cover, source=None, track=None):
        extra = '' if source is None else f'{source}\n{track}\n'  # bt_cover.c's third and fourth lines
        self.now.write_bytes(f'{handle}\n{cover}\n{extra}'.encode())

    def lines(self):
        """Its log lines, each the unified log's (patch/ulog_line.h): time, uptime, level, source, message."""
        out = []
        for line in self.err.read_text().splitlines():
            wall, up, level, src, msg = line.split(' ', 4)
            assert wall.endswith('Z') and up.isdigit() and level in 'EWI'
            out.append((level, src, msg))
        return out

    def connect(self):
        s = socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET)
        s.settimeout(20)
        s.connect(str(self.sock))
        return s

    def runs(self):
        return self.log.read_text().splitlines() if self.log.exists() else []

    def stop(self):
        self.proc.kill()
        self.proc.wait()


@pytest.fixture
def daemon(daemon_bin, tmp_path):
    d = Daemon(daemon_bin, tmp_path)
    yield d
    d.stop()


def ask(s, packet):
    s.send(packet)
    return s.recv(2000)


def fetch(s, type_, handle):
    """A whole GET over the socket: (the first answer's code, the body)."""
    h = CONN_ID + header(0x42, type_.encode() + b'\0') + header(0x30, (handle + '\0').encode('utf-16-be'))
    answer = ask(s, b'\x83' + struct.pack('>H', 3 + len(h)) + h)
    first, body = answer[0], b''
    while True:
        headers = parse(answer)
        body += headers.get(0x48, b'') + headers.get(0x49, b'')
        if answer[0] != 0x90:
            return first, body
        answer = ask(s, bytes.fromhex('83 0008') + CONN_ID)


def test_daemon_serves_the_playing_songs_cover(daemon):
    daemon.made.write_bytes(jpeg(7000))
    daemon.song('6136261', '/mnt/mmc/.coverflow/811c9dc5.jpg')
    s = daemon.connect()
    assert ask(s, CONNECT) == CONNECTED
    assert fetch(s, 'x-bt/img-thm', '6136261') == (0x90, jpeg(7000))
    run, = daemon.runs()  # stock ffmpeg, once: the cover in, a 200 x 200 4:2:2 JPEG out
    assert '-i /mnt/mmc/.coverflow/811c9dc5.jpg ' in run and 'scale=200:200' in run and 'pad=200:200' in run
    assert '-pix_fmt yuvj422p' in run and '-f mjpeg' in run
    code, xml = fetch(s, 'x-bt/img-properties', '6136261')
    assert code == 0xa0 and b'pixel="200*200" size="7000"' in xml
    assert fetch(s, 'x-bt/img-img', '6136261')[1] == jpeg(7000)
    assert len(daemon.runs()) == 1  # made once for the handle, not per request
    assert not (daemon.tmp / 'out.jpg').exists()  # and nothing of it left in /tmp


def test_daemon_follows_a_track_change(daemon):
    daemon.made.write_bytes(jpeg(3000))
    daemon.song('6136261', '/a/cover.jpg')
    s = daemon.connect()
    ask(s, CONNECT)
    assert fetch(s, 'x-bt/img-thm', '6136261')[1] == jpeg(3000)
    daemon.made.write_bytes(jpeg(4000))
    daemon.song('0000042', '/b/cover.jpg')  # the next song, another album
    assert fetch(s, 'x-bt/img-thm', '0000042')[1] == jpeg(4000)
    assert fetch(s, 'x-bt/img-thm', '6136261') == (0xc4, b'')  # the old handle is no image any more
    assert [r.split(' -i ')[1].split()[0] for r in daemon.runs()] == ['/a/cover.jpg', '/b/cover.jpg']
    daemon.now.unlink()  # a song with no cover
    assert fetch(s, 'x-bt/img-thm', '6136261') == (0xc4, b'')
    assert fetch(s, 'x-bt/img-thm', '0000042')[1] == jpeg(4000)  # kept: what was fetched stays fetchable


LOG = re.compile(r'cover (\d{7}) (thumbnail|image|properties) for (.*) from (\w+|\?): (\d+) bytes in (\d+) ms, (.*)')


def test_daemon_logs_one_line_per_fetch(daemon):
    """Each GET of a cover is one bt line: the handle, what was asked, the song and where its cover came from (as
    bt_cover.c wrote them), the bytes sent, the time it took and how it went."""
    daemon.made.write_bytes(jpeg(7000))
    song = '/mnt/mmc/.subsonic/stream/a/original/f/ribs [0S2].flac'
    daemon.song('6136261', song, 'own', song)
    s = daemon.connect()
    ask(s, CONNECT)
    fetch(s, 'x-bt/img-properties', '6136261')
    fetch(s, 'x-bt/img-thm', '6136261')
    fetch(s, 'x-bt/img-thm', '0000042')  # not the playing song's
    got = [(level, src, LOG.fullmatch(msg).groups()) for level, src, msg in daemon.lines()]
    assert [(g[0], g[1]) + g[2][:5] + g[2][6:] for g in got] == [
        ('I', 'bt', '6136261', 'properties', song, 'own', '7000', 'made'),
        ('I', 'bt', '6136261', 'thumbnail', song, 'own', '7000', 'kept'),
        ('W', 'bt', '0000042', 'thumbnail', song, 'own', '0', 'not the playing song\'s')]


def test_daemon_logs_a_failed_run_and_an_old_file(daemon):
    daemon.song('6136261', '/a/cover.jpg')  # no source lines: a file from before them
    s = daemon.connect()
    ask(s, CONNECT)
    fetch(s, 'x-bt/img-thm', '6136261')  # ffmpeg fails: it makes no file
    (level, src, msg), = daemon.lines()
    assert (level, src) == ('W', 'bt') and LOG.fullmatch(msg).groups()[2:5] == ('?', '?', '0')
    assert msg.endswith('ffmpeg failed')


@pytest.mark.parametrize('made', [None, b'', b'not a jpeg', b'\xff\xd8' + bytes(70000)])
def test_daemon_answers_not_found_when_the_cover_cannot_be_made(daemon, made):
    if made is not None:
        daemon.made.write_bytes(made)  # None: ffmpeg fails
    daemon.song('6136261', '/a/cover.jpg')
    s = daemon.connect()
    ask(s, CONNECT)
    assert fetch(s, 'x-bt/img-thm', '6136261') == (0xc4, b'')
    assert ask(s, bytes.fromhex('81 0003')) == bytes.fromhex('a0 0003')  # and it still answers


def test_daemon_trusts_no_jpeg_from_a_failed_run(daemon):
    daemon.made.write_bytes(jpeg(900))
    (daemon.tmp / 'fails').write_text('')  # ffmpeg writes its file, then exits 3
    daemon.song('6136261', '/a/cover.jpg')
    s = daemon.connect()
    ask(s, CONNECT)
    assert fetch(s, 'x-bt/img-thm', '6136261') == (0xc4, b'')
    assert not (daemon.tmp / 'out.jpg').exists()


def test_daemon_takes_the_next_controller_after_a_disconnect(daemon):
    daemon.made.write_bytes(jpeg(500))
    daemon.song('6136261', '/a/cover.jpg')
    s = daemon.connect()
    ask(s, CONNECT)
    assert ask(s, bytes.fromhex('81 0003')) == bytes.fromhex('a0 0003')
    assert s.recv(10) == b''  # the channel is closed after OBEX's disconnect
    t = daemon.connect()
    s.close()
    assert fetch(t, 'x-bt/img-thm', '6136261') == (0xd3, b'')  # a new channel is a new session: connect first
    assert ask(t, CONNECT) == CONNECTED and fetch(t, 'x-bt/img-thm', '6136261')[1] == jpeg(500)
    t.close()  # dropped without a disconnect
    assert ask(daemon.connect(), CONNECT) == CONNECTED


@pytest.mark.skipif(not shutil.which('ffmpeg'), reason='needs ffmpeg')
def test_daemon_with_a_real_ffmpeg_makes_200_by_200(daemon_bin, tmp_path):
    src = tmp_path / 'wide.jpg'  # 320 x 160: not square
    subprocess.run(['ffmpeg', '-v', 'quiet', '-f', 'lavfi', '-i', 'testsrc=size=320x160', '-frames:v', '1', src], check=True)
    d = Daemon(daemon_bin, tmp_path, shutil.which('ffmpeg'))
    try:
        d.song('1234567', src)
        s = d.connect()
        ask(s, CONNECT)
        code, body = fetch(s, 'x-bt/img-thm', '1234567')
    finally:
        d.stop()
    assert body[:2] == b'\xff\xd8' and body[-2:] == b'\xff\xd9' and len(body) < 65536
    at = body.index(b'\xff\xc0')  # the baseline frame header: precision, height, width, components and their sampling
    height, width, components = struct.unpack('>xHHB', body[at + 4:at + 10])
    assert (width, height, components) == (200, 200, 3)
    # luma 2 x 2 and chroma 1 x 2: half the colour across, all of it down, YCbCr 4:2:2 as BIP's thumbnail is
    assert (body[at + 11], body[at + 14], body[at + 17]) == (0x22, 0x12, 0x12)


def l2cap_bind(psm):
    """bind() of a fresh L2CAP socket to psm through libc (this Python may be built without Bluetooth): 0, an
    errno, or None when the kernel has no Bluetooth sockets."""
    libc = ctypes.CDLL(None, use_errno=True)
    fd = libc.socket(31, socket.SOCK_SEQPACKET, 0)  # AF_BLUETOOTH, BTPROTO_L2CAP
    if fd < 0:
        return None
    addr = struct.pack('<HH6sHB', 31, psm, bytes(6), 0, 0)  # sockaddr_l2: any adapter
    r = libc.bind(fd, addr, len(addr))
    err = ctypes.get_errno() if r else 0
    os.close(fd)
    return err


def test_daemon_listens_on_l2cap_in_ertm(daemon_bin):
    """On a kernel with Bluetooth (no controller needed): the PSM is taken while q2-bip runs, so the socket, Enhanced
    Retransmission Mode, the security level and listen() were all accepted: it ends at once when one is refused."""
    free = l2cap_bind(0x1025)
    if free is None:
        pytest.skip('no Bluetooth sockets on this kernel')
    if free:
        pytest.skip('the PSM is in use')
    proc = subprocess.Popen([daemon_bin])
    try:
        for _ in range(100):
            time.sleep(0.02)
            if l2cap_bind(0x1025) == errno.EADDRINUSE:
                break
        else:
            pytest.fail('q2-bip never took the PSM')
        time.sleep(0.1)
        assert proc.poll() is None  # still there: listening
    finally:
        proc.kill()
        proc.wait()


if __name__ == '__main__':
    sys.exit(pytest.main([__file__, '-q', '-p', 'no:cacheprovider', *sys.argv[1:]]))
