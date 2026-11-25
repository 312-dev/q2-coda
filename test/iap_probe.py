#!/usr/bin/env python3
"""q2-iap, the iPhone gadget's daemon and route 1's probe (patch/q2_iap.c, docs/iphone-mode.md), built for this machine.

Each kind's FunctionFS blobs are parsed by the rules Linux 5.10's f_fs.c applies (ffs_do_descs, ffs_do_single_desc,
__ffs_data_do_entity, __ffs_data_got_strings), and the probe is run against a stand-in ep0, a pseudo-terminal in raw
mode whose other end plays FunctionFS: the probe's descriptor writes arrive there, and events and data stages are
written back. Checked: what it answers to each request, the echo of iAP2's detect on ep1 (a FIFO), sessions and the
exit status. Run it as python3 test/iap_probe.py (pytest, host cc)."""
import fcntl
import os
import pathlib
import select
import struct
import subprocess
import sys
import time
import tty

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'tools/iap_probe'))
import probe_build  # noqa: E402  (q2-iap's sources, as tools/build.py's Q2IAP_SOURCES)

SRCS = [ROOT/'patch'/f'{s}.c' for s in probe_build.SOURCES]
SQLITE = '/usr/lib/x86_64-linux-gnu/libsqlite3.so.0'  # the library on iDrive (q2_iap_lib.c); stock's is 3.54
SETUP, ENABLE, DISABLE = 4, 2, 3


@pytest.fixture(scope='module')
def probe(tmp_path_factory):
    """q2-iap built for this machine, or Q2IAP_EXE: another to run instead, such as a build's MIPS q2-iap behind a
    script that runs it under qemu-mipsel-static with an emulator's rootfs (as the device runs it)."""
    if os.environ.get('Q2IAP_EXE'):
        return pathlib.Path(os.environ['Q2IAP_EXE'])
    exe = tmp_path_factory.mktemp('probe')/'q2-iap-probe'
    subprocess.run(['cc', '-DPROBE_HOST', '-O2', '-Wall', '-Wextra', '-Werror', '-Wno-unused-parameter',
                    '-Wno-unused-result', *map(str, SRCS), '-o', str(exe), SQLITE], check=True)
    return exe


def blobs(probe, kind):
    out = subprocess.run([probe, '--descs', kind], check=True, capture_output=True).stdout
    n = struct.unpack_from('<I', out, 4)[0]
    return out[:n], out[n:]


def parse_set(data):
    """One speed's descriptors as f_fs.c checks them: (descriptors, interface numbers, endpoint addresses in order)."""
    descs, intfs, eps, o = [], set(), [], 0
    while o < len(data):
        length, kind = data[o], data[o + 1]
        assert length >= 2 and o + length <= len(data), 'a descriptor runs past the blob'
        d = data[o:o + length]
        if kind == 4:
            assert length == 9
            intfs.add(d[2])
        elif kind == 5:
            assert length in (7, 9)
            eps.append(d[2])
        elif kind == 0x21:
            assert length == 9, 'HID descriptor: sizeof(struct hid_descriptor)'
        else:
            raise AssertionError(f'type {kind:#x} is not one FunctionFS takes')
        descs.append(d)
        o += length
    return descs, intfs, eps


@pytest.mark.parametrize('kind,intf_classes,ep_count', [
    ('ptp', [(0, 0, 6)], 3), ('hid', [(0, 0, 3)], 1), ('ptpmux', [(0, 0, 6), (1, 0, 0xff)], 5),
    ('ptpmuxeth', [(0, 0, 6), (1, 0, 0xff), (2, 0, 0xff), (2, 1, 0xff)], 7)])
def test_blobs_parse_as_functionfs_does(probe, kind, intf_classes, ep_count):
    d, s = blobs(probe, kind)
    magic, length, flags, nfs, nhs = struct.unpack_from('<5I', d)
    assert (magic, length, flags) == (3, len(d), 0xc3)  # v2; FS, HS, ALL_CTRL_RECIP, CONFIG0_SETUP
    rest = d[20:]
    # the full-speed set is the first nfs descriptors
    o = 0
    for _ in range(nfs):
        o += rest[o]
    fs, fi, fe = parse_set(rest[:o])
    hs, hi, he = parse_set(rest[o:])
    assert (len(fs), len(hs)) == (nfs, nhs)
    assert fe == he and len(fe) == ep_count, 'endpoints in the same order at both speeds'
    assert len(set(a & 0x0f for a in fe)) == len(fe), 'one number per endpoint'
    assert fi == hi == set(range(len(fi))), 'interface numbers from 0 with no gap'
    assert [(x[2], x[3], x[5]) for x in fs if x[1] == 4] == intf_classes
    for x in hs:
        if x[1] == 5:
            size = x[4] | x[5] << 8
            assert size == (64 if x[3] == 3 else 512) and (x[3] != 3 or x[6] == 7)
    for x in fs:
        if x[1] == 5:
            assert (x[4] | x[5] << 8) == 64 and (x[3] != 3 or x[6] == 8)
    smagic, slen, count, langs = struct.unpack_from('<4I', s)
    assert (smagic, slen, count, langs) == (2, len(s), 1, 1) and s[16:18] == b'\x09\x04' and s.endswith(b'\0')
    used = {x[8] for x in fs if x[1] == 4}
    assert used <= {0, 1}, 'string indexes within the one string given'


def test_hid_descriptors(probe):
    d, _ = blobs(probe, 'hid')
    fs, _, _ = parse_set(d[20:20 + 9 + 9 + 7])
    assert fs[0] == bytes([9, 4, 0, 0, 1, 3, 0, 0, 1])
    assert fs[1] == bytes.fromhex('092111010001 22d000')
    assert fs[2] == bytes.fromhex('07058103400008')


class FakeFfs:
    """A FunctionFS directory stand-in: ep0 a raw pty (the test holds the master), ep1 a FIFO."""

    def __init__(self, path):
        self.master, slave = os.openpty()
        tty.setraw(slave)
        self.slave = slave  # held open so the line settings stay
        path.mkdir()
        (path/'ep0').symlink_to(os.ttyname(slave))
        os.mkfifo(path/'ep1')
        self.path = path
        self.buf = b''
        self.data_delay = 0.05

    def read(self, n, timeout=3.0):
        end = time.time() + timeout
        while len(self.buf) < n and time.time() < end:
            r, _, _ = select.select([self.master], [], [], 0.05)
            if r:
                self.buf += os.read(self.master, 65536)
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def quiet(self, timeout=0.4):
        return self.read(1, timeout) == b''

    def event(self, kind, setup=bytes(8)):
        os.write(self.master, setup + bytes([kind, 0, 0, 0]))

    def setup(self, rt, rq, wv, wi, wl, data=b''):
        self.event(SETUP, struct.pack('<BBHHH', rt, rq, wv, wi, wl))
        if data:
            time.sleep(self.data_delay)  # the probe reads the event before the data (one stream stands for both)
            os.write(self.master, data)


def start(probe, tmp_path, *args):
    ffs = FakeFfs(tmp_path/'iap2')
    log = tmp_path/'probe.log'
    p = subprocess.Popen([probe, *args, str(log), f'{ffs.path}:hid'])
    d, s = blobs(probe, 'hid')
    assert ffs.read(len(d)) == d and ffs.read(len(s)) == s
    return p, ffs, log


def test_answers_like_an_iphone(probe, tmp_path):
    p, ffs, log = start(probe, tmp_path, '--sessions', '1', '--gap-ms', '300')
    try:
        ffs.event(ENABLE)
        ffs.setup(0x81, 6, 0x2200, 2, 0x1000)       # HID report descriptor, asked for more than it is
        assert len(ffs.read(208)) == 208
        ffs.setup(0xc0, 0x53, 0, 0, 4)              # Apple's vendor request
        assert ffs.read(4) == bytes(4)
        ffs.setup(0xa1, 1, 0x0101, 2, 6)            # GET_REPORT
        assert ffs.read(6) == bytes(6)
        ffs.setup(0xc0, 0x33, 0, 0, 4)              # unknown IN (the Mercedes' request): stalled, nothing written
        assert ffs.quiet()
        ffs.setup(0x40, 0x40, 500, 1000, 0)         # charging
        ffs.setup(0x21, 9, 0x020d, 2, 6, bytes.fromhex('0d00ff550200'))  # a SET_REPORT that is not the detect
        ffs.event(DISABLE)
        assert p.wait(5) == 10, 'exits 10 once its one session ends'
    finally:
        p.kill()
    text = log.read_text()
    for want in ('session 1 begins on configuration 2, echo off', 'SETUP 81 06 2200 0002 1000 -> answered 208 bytes',
                 'Apple C0 53: 00 (media mode)', 'SETUP c0 33 0000 0000 0004 -> stalled',
                 'Apple charging request', 'SET_REPORT type 2 id 13', 'data 0d 00 ff 55 02 00', 'session 1 ended'):
        assert want in text, want


def test_echoes_the_detect(probe, tmp_path):
    p, ffs, log = start(probe, tmp_path, '--echo', '--carplay')
    fd = os.open(ffs.path/'ep1', os.O_RDONLY | os.O_NONBLOCK)
    try:
        ffs.event(ENABLE)
        ffs.setup(0xc0, 0x53, 0, 0, 4)
        assert ffs.read(4) == b'\x01\0\0\0'
        ffs.setup(0x21, 9, 0x020d, 2, 6, bytes.fromhex('0d00ff550200ee10')[:6])  # short: no echo
        ffs.setup(0x21, 9, 0x020e, 2, 10, bytes.fromhex('0e00ff550200ee100000'))
        got, end = b'', time.time() + 3
        while len(got) < 10 and time.time() < end:
            try:
                got += os.read(fd, 64)
            except BlockingIOError:
                time.sleep(0.05)
        assert got == bytes.fromhex('0200ff550200ee100000'), 'input report 2: link control 0, the detect, zero padded'
    finally:
        p.terminate()
        p.wait(5)
        os.close(fd)
    text = log.read_text()
    assert 'detect seen: echoing it as input report 2' in text and 'end: signalled' in text


def test_echo_from_second_session(probe, tmp_path):
    p, ffs, log = start(probe, tmp_path, '--echo-from', '2', '--gap-ms', '200', '--for', '30')
    try:
        ffs.event(ENABLE)
        ffs.setup(0x21, 9, 0x020e, 2, 10, bytes.fromhex('0e00ff550200ee100000'))
        ffs.event(DISABLE)
        time.sleep(0.2)
        ffs.event(ENABLE)                            # a reset inside the gap: the same session
        ffs.event(DISABLE)
        time.sleep(1.2)
        ffs.event(ENABLE)
        time.sleep(0.3)
    finally:
        p.terminate()
        p.wait(5)
    text = log.read_text()
    assert 'detect seen: not echoed (echo off in this session)' in text
    assert 'session 2 begins on configuration 2, echo on' in text and 'session 3' not in text


def test_suspend_ends_a_session(probe, tmp_path):
    """In the car a cable out was a SUSPEND (the controller left suspended with no VBUS), not a DISABLE."""
    p, ffs, log = start(probe, tmp_path, '--sessions', '1', '--gap-ms', '300')
    try:
        ffs.event(ENABLE)
        ffs.event(5)                                 # SUSPEND
        ffs.event(6)                                 # RESUME inside the gap: the same session
        time.sleep(0.5)
        assert p.poll() is None
        ffs.event(5)
        assert p.wait(5) == 10
    finally:
        p.kill()
    assert 'session 1 ended' in log.read_text()


# ---- iAP2 (--iap2): a synthetic car ----

OUT_SIZES = {13: 5, 14: 9, 15: 13, 16: 17, 17: 25, 18: 49, 19: 95, 20: 193, 21: 255}
IN_SIZES = {1: 5, 2: 9, 3: 13, 4: 17, 5: 25, 6: 49, 7: 95, 8: 193, 9: 257, 10: 385, 11: 513, 12: 767}


def ck(b):
    return (-sum(b)) & 0xff


def packet(ctl, seq, ack, session, payload=b''):
    n = 9 + (len(payload) + 1 if payload else 0)
    h = bytes([0xff, 0x5a, n >> 8, n & 0xff, ctl, seq, ack, session])
    h += bytes([ck(h)])
    return h + (payload + bytes([ck(payload)]) if payload else b'')


def message(mid, *params):
    body = b''.join(params)
    return bytes([0x40, 0x40, (6 + len(body)) >> 8, (6 + len(body)) & 0xff, mid >> 8, mid & 0xff]) + body


def prm(pid, data=b''):
    return bytes([(4 + len(data)) >> 8, (4 + len(data)) & 0xff, pid >> 8, pid & 0xff]) + data


def params(body):
    out, off = {}, 0
    while off + 4 <= len(body):
        n = body[off] << 8 | body[off + 1]
        out.setdefault(body[off + 2] << 8 | body[off + 3], body[off + 4:off + n])
        off += n
    return out


class Car:
    """The car's side: output reports by SET_REPORT on the stand-in ep0, input reports read from ep1."""

    def __init__(self, ffs, fd):
        self.ffs, self.fd, self.buf, self.seq = ffs, fd, b'', 0x2a

    def write(self, data):
        off = 0
        while True:
            left = len(data) - off
            rid = next((i for i, n in OUT_SIZES.items() if n >= left + 1), 21)
            take = min(OUT_SIZES[rid] - 1, left)
            lcb = (1 if off else 0) | (2 if off + take < len(data) else 0)
            rep = bytes([rid, lcb]) + data[off:off + take]
            rep += bytes(1 + OUT_SIZES[rid] - len(rep))
            self.ffs.setup(0x21, 9, 0x0200 | rid, 2, len(rep), rep)
            time.sleep(0.02)
            off += take
            if off >= len(data):
                return

    def report(self, timeout=3.0):
        end = time.time() + timeout
        while time.time() < end:
            if self.buf and len(self.buf) >= 1 + IN_SIZES[self.buf[0]]:
                n = 1 + IN_SIZES[self.buf[0]]
                r, self.buf = self.buf[:n], self.buf[n:]
                return r
            try:
                self.buf += os.read(self.fd, 4096)
            except BlockingIOError:
                time.sleep(0.01)
        raise AssertionError('no input report')

    def packet(self, timeout=3.0):
        data = b''
        while True:
            r = self.report(timeout)
            data += r[2:]
            if not r[1] & 2:
                break
        assert data[:2] == b'\xff\x5a' and ck(data[:8]) == data[8], data[:12].hex()
        n = data[2] << 8 | data[3]
        pl = data[9:n - 1] if n > 9 else b''
        if pl:
            assert ck(pl) == data[n - 1], 'payload checksum'
        return data[4], data[5], data[6], data[7], pl

    def send(self, payload, session=10):
        self.seq = (self.seq + 1) & 0xff
        self.write(packet(0x40, self.seq, self.ack, session, payload))

    def msg(self, timeout=3.0):
        ctl, seq, ack, session, pl = self.packet(timeout)
        assert ctl & 0x40 and session == 10 and ack == self.seq, (hex(ctl), session, ack, self.seq)
        assert pl[:2] == b'\x40\x40' and (pl[2] << 8 | pl[3]) == len(pl)
        self.ack = seq
        return pl[4] << 8 | pl[5], params(pl[6:])


SYN = bytes([1, 0x05, 0x04, 0x00, 0x03, 0xe8, 0x00, 0x00, 0x00, 0x00]) + bytes([10, 0, 1, 11, 1, 1])


def test_iap2_through_identification(probe, tmp_path):
    np = tmp_path/'now'
    np.write_text('seq=3\nstate=1\npos=61000\nlen=245\nqpos=2\nqlen=11\ntitle=Teardrop\nartist=Massive Attack\n'
                  'album=Mezzanine\n')
    p, ffs, log = start(probe, tmp_path, '--iap2', '--np', str(np), '--uuid', 'ab' * 20)
    fd = os.open(ffs.path/'ep1', os.O_RDONLY | os.O_NONBLOCK)
    car = Car(ffs, fd)
    try:
        ffs.event(ENABLE)
        car.write(bytes([0xff, 0x55, 0x02, 0x00, 0xee, 0x10]))
        assert car.report() == bytes.fromhex('0200ff550200ee100000'), 'the detect echoed in report 2'
        car.write(packet(0x80, car.seq, 0, 0, SYN))
        ctl, syn_seq, ack, session, pl = car.packet()
        assert ctl == 0xc0 and ack == car.seq and pl == SYN, 'SYN+ACK with the car\'s own parameters'
        car.ack = syn_seq
        car.write(packet(0x40, car.seq, syn_seq, 0))
        assert car.msg()[0] == 0xAA00, 'RequestAuthenticationCertificate once the link is up'
        car.send(message(0xAA01, prm(0, bytes(range(256)) * 3 + bytes(132))))   # 900 bytes, many reports
        mid, ps = car.msg()
        assert mid == 0xAA02 and len(ps[0]) == 20, 'a 20-byte challenge to a 900-byte certificate'
        car.send(message(0xAA03, prm(0, bytes(128))))
        t0 = time.time()
        assert car.msg()[0] == 0xAA05
        assert car.msg()[0] == 0x1D00
        takes = b''.join(i.to_bytes(2, 'big') for i in (0x4E0C, 0x4E0A, 0x4E09, 0x0B00, 0x0B03, 0x5001, 0xAE01,
                                                         0x4C01, 0x4C04, 0xDA01, 0x4300, 0x4E04))
        sends = b''.join(i.to_bytes(2, 'big') for i in (0x5000, 0xAE00, 0xAE03, 0xDA00, 0x4C00, 0x4C03, 0x6800))
        car.send(message(0x1D01, prm(0, b'BMW MGU\0'), prm(2, b'BMW\0'), prm(6, sends), prm(7, takes),
                         prm(17, prm(0, b'\x00\x05'))))
        got = [car.msg() for _ in range(6)]
        assert [g[0] for g in got] == [0x1D02, 0x4E0C, 0x4E0A, 0x4E09, 0x0B00, 0x0B03]
        assert got[1][1][0] == b'abababab-abab-abab-abab-abababababab\0', 'the UUID from --uuid'
        assert got[3][1][0] == b'Vamp\0' and got[4][1][0] == b'\x00\x05' and 2 in got[5][1]
        assert time.time() - t0 < 2
        car.send(message(0xAE00, prm(0), prm(4), prm(5)))
        power = [car.msg() for _ in range(3)]
        assert [sorted(g[1]) for g in power] == [[4], [5], [0]] and power[2][1][0] == b'\x05\xdc'
        car.send(message(0x5000, prm(0, prm(1) + prm(4) + prm(12)), prm(1, prm(0) + prm(1) + prm(3))))
        mid, ps = car.msg()
        media, play = params(ps[0]), params(ps[1])
        assert mid == 0x5001 and media[1] == b'Teardrop\0' and media[12] == b'Massive Attack\0'
        assert int.from_bytes(media[4], 'big') == 245000 and play[0] == b'\x01'
        assert int.from_bytes(play[1], 'big') == 61000 and int.from_bytes(play[3], 'big') == 11
        car.send(message(0xDA00))
        mid, ps = car.msg()
        assert mid == 0xDA01 and ps[0] == b'\x07'
    finally:
        p.terminate()
        p.wait(5)
        os.close(fd)
    text = log.read_text()
    for want in ('detect: echoed', 'link parameters: version 1, window 5, max packet 1024, retransmit 1000 ms',
                 'link: control session 10, file transfer session 11', 'link: up',
                 "auth: the car's certificate is 900 bytes", 'ident: name "BMW MGU"', 'ident: manufacturer "BMW"',
                 'ident: the car takes: 4e0c DeviceUUIDUpdate', 'ident: accepted',
                 'msg < da00 StartUSBDeviceModeAudio'):
        assert want in text, want


def test_iap2_never_sends_again_to_a_car_that_gives_no_retries(probe, tmp_path):
    """PodBox's trap: a car whose SYN gives 0 retries stops reading at a packet sent again."""
    p, ffs, log = start(probe, tmp_path, '--iap2')
    fd = os.open(ffs.path/'ep1', os.O_RDONLY | os.O_NONBLOCK)
    car = Car(ffs, fd)
    try:
        ffs.event(ENABLE)
        car.write(bytes([0xff, 0x55, 0x02, 0x00, 0xee, 0x10]))
        car.report()
        car.write(packet(0x80, car.seq, 0, 0, SYN))
        _, syn_seq, _, _, _ = car.packet()
        car.ack = syn_seq
        car.write(packet(0x40, car.seq, syn_seq, 0))
        assert car.msg()[0] == 0xAA00
        with pytest.raises(AssertionError):            # never acknowledged, and never sent again
            car.report(timeout=1.5)
    finally:
        p.terminate()
        p.wait(5)
        os.close(fd)
    assert '(again)' not in log.read_text()


# The BMW's own: its SYN (link parameters: window 5, retransmit 200 ms, cumulative ACK 22 ms, 10 retries, ACK after
# 3; sessions 1 control, 2 file transfer, 3) and its StartHID, from the M1 car run's log (2026-11-19)
BMW_SYN = bytes.fromhex('0105ffff00c800160a03010001020102030201')
BMW_START_HID = bytes.fromhex('00060000000100060001 05ac00060002 023c00250004050c0901a1011500250175019505'
                              '09b009b109b509b609b9810275019503 8103c0'.replace(' ', ''))


def fnv_part(h, s):
    for c in s.encode():
        h = ((h ^ c) * 16777619) & 0xffffffff
    return (h * 16777619) & 0xffffffff


def jpeg_segments(b):
    """[(marker, body)] up to the scan."""
    out, i = [], 2
    while i + 4 <= len(b):
        mk, n = b[i + 1], b[i + 2] << 8 | b[i + 3]
        out.append((mk, b[i + 4:i + 2 + n]))
        if mk == 0xDA:
            break
        i += 2 + n
    return out


class BmwCar(Car):
    """The BMW's sessions and acknowledgements: it acknowledges what it takes unless told to hold back."""

    def __init__(self, ffs, fd):
        super().__init__(ffs, fd)
        self.hold = False
        self.files = []
        self.peer, self.seen = 0, set()

    def packet_any(self, timeout=3.0):
        """The next packet, or None for one sent again (this car acknowledged late): dropped, and acknowledged unless
        held, as a car does."""
        ctl, seq, ack, session, pl = self.packet(timeout)
        if pl and seq in self.seen:
            if not self.hold:
                self.write(packet(0x40, self.seq, self.peer, 0))
            return None
        if pl:
            self.seen.add(seq)
            self.seen.discard((seq + 128) % 256)  # sequence numbers wrap: a seq half the space back is a new packet
            self.peer = seq
            if not self.hold:
                self.write(packet(0x40, self.seq, seq, 0))
        return ctl, seq, ack, session, pl

    def msg(self, timeout=3.0):
        while True:
            got = self.packet_any(timeout)
            if got is None:
                continue
            ctl, seq, ack, session, pl = got
            if not pl:
                continue  # a bare ACK: the BMW's SYN asks for them within 22 ms
            if session == 2:
                self.files.append(pl)
                continue
            assert session == 1 and pl[:2] == b'\x40\x40', (session, pl[:6].hex())
            return pl[4] << 8 | pl[5], params(pl[6:])

    def send(self, payload, session=1):
        self.seq = (self.seq + 1) & 0xff
        self.write(packet(0x40, self.seq, self.peer, session, payload))  # acknowledging the Q2's last, as the BMW does


def bmw_up(probe, tmp_path, *more):
    """A probe in --iap2 up to identification with the BMW's link parameters; (process, car, log, files)."""
    files = {'car': tmp_path/'q2-iap.audio', 'cmd': tmp_path/'coda-rc.cmd', 'np': tmp_path/'now', 'art': tmp_path/'art',
             'rate': tmp_path/'q2-iap.rate', 'relink': tmp_path/'q2car.relink', 'cmdq': tmp_path/'cmdq'}
    files['art'].mkdir()
    put_whole(files['np'], 'state=1\npos=61000\nlen=245\ntitle=Teardrop\nartist=Massive Attack\nalbum=Mezzanine\n'
                           'path=/nowhere/a.flac\nshuffle=0\nrepeat=2\n')
    p, ffs, log = start(probe, tmp_path, '--iap2', '--np', str(files['np']), '--car-pcm', 'hw:CARD=UAC1Gadget,0',
                        '--car-file', str(files['car']), '--cmd-file', str(files['cmd']), '--art-dir', str(files['art']),
                        '--rate-file', str(files['rate']), '--relink-file', str(files['relink']),
                        '--cmdq-file', str(files['cmdq']), *more)
    fd = os.open(ffs.path/'ep1', os.O_RDONLY | os.O_NONBLOCK)
    car = BmwCar(ffs, fd)
    ffs.event(ENABLE)
    car.write(bytes([0xff, 0x55, 0x02, 0x00, 0xee, 0x10]))
    car.report()
    car.write(packet(0x80, car.seq, 0, 0, BMW_SYN))
    _, syn_seq, _, _, pl = car.packet()
    assert pl[1] == 0x7f, 'an iPhone offers a window of 0x7F to a car that retransmits'
    car.write(packet(0x40, car.seq, syn_seq, 0))
    assert car.msg()[0] == 0xAA00
    car.send(message(0xAA01, prm(0, bytes(608))))
    assert car.msg()[0] == 0xAA02
    car.send(message(0xAA03, prm(0, bytes(64))))
    assert [car.msg()[0], car.msg()[0]] == [0xAA05, 0x1D00]
    takes = b''.join(i.to_bytes(2, 'big') for i in (0x4E09, 0x4E0A, 0x4E0C, 0x5001, 0x6801, 0xDA01, 0x4C01, 0x4C04))
    car.send(message(0x1D01, prm(0, b'BMW 83828\0'), prm(7, takes)))
    assert [car.msg()[0] for _ in range(4)] == [0x1D02, 0x4E0C, 0x4E0A, 0x4E09]
    return p, car, fd, log, files


def stop(p, fd):
    p.terminate()
    p.wait(5)
    os.close(fd)


def test_bmw_buttons(probe, tmp_path):
    """The BMW's StartHID descriptor (Play, Pause, Next, Previous, Random as five bits) and its presses from the car
    run: 01 then 08 then 04, each released, as the shim's verbs in its command file."""
    p, car, fd, log, files = bmw_up(probe, tmp_path)
    try:
        car.send(bytes([0x40, 0x40, 0, 6 + len(BMW_START_HID), 0x68, 0x00]) + BMW_START_HID)
        seen = []
        for press in (0x01, 0x08, 0x04, 0x10):
            for b in (press, 0x00):
                car.send(message(0x6802, prm(0, b'\x00\x01'), prm(1, bytes([b]))))
                time.sleep(0.15)
            seen.append(files['cmd'].read_text().split(' ', 1)[1].strip())
        assert seen == ['resume', 'previous', 'next', 'mode 1 2'], seen
        assert oct(files['cmd'].stat().st_mode & 0o777) == '0o600'
    finally:
        stop(p, fd)
    assert 'hid: the car starts HID: 5 buttons in its report descriptor' in log.read_text()


def test_bmw_hold_to_scan(probe, tmp_path):
    """Next and Previous act at their release: a tap (the car's were up 13 to 120 ms after the press, 2026-11-20) is
    the skip; held past 500 ms it is the Q2's own scan of a held button (seek.c seek_car): "scan 1" (Previous "scan
    -1"), said again every 500 ms while held so the scan goes on, "scan 0" at the release, and no seek and no skip."""
    p, car, fd, log, files = bmw_up(probe, tmp_path)

    def hid(b):
        car.send(message(0x6802, prm(0, b'\x00\x01'), prm(1, bytes([b]))))

    def said(text=None):
        return [line.split(': ', 2)[2] for line in (text or log.read_text()).splitlines()
                if ' held ' in line and ': scan ' in line]
    try:
        car.send(bytes([0x40, 0x40, 0, 6 + len(BMW_START_HID), 0x68, 0x00]) + BMW_START_HID)
        time.sleep(0.15)
        for button, gap, verb in ((0x04, 0.013, 'next'), (0x08, 0.120, 'previous')):
            hid(button)
            time.sleep(gap)
            assert not files['cmd'].exists() or verb not in files['cmd'].read_text(), 'nothing until the release'
            hid(0x00)
            time.sleep(0.15)
            assert files['cmd'].read_text().split(' ', 1)[1].strip() == verb
            files['cmd'].unlink()
        hid(0x04)                                    # Next held 2.2 s
        time.sleep(2.2)
        hid(0x00)
        assert said(log_wait(log, lambda t: 'scan 0' in said(t))) == ['scan 1'] * 4 + ['scan 0'], said()
        assert files['cmd'].read_text().split(' ', 1)[1].strip() == 'scan 0', 'no skip after a scan'
        hid(0x08)                                    # Previous held 1.2 s
        time.sleep(1.2)
        hid(0x00)
        assert said(log_wait(log, lambda t: said(t)[-1:] == ['scan 0'] and len(said(t)) > 5))[5:] == \
            ['scan -1'] * 2 + ['scan 0'], said()
    finally:
        stop(p, fd)
    text = log.read_text()
    assert 'hid: usage b5 released after' in text and 'the scan ends' in text and ': seek ' not in text


def test_bmw_audio_and_seek(probe, tmp_path):
    """StartUSBDeviceModeAudio writes the player's car file (its device, then 44100), StopUSBDeviceModeAudio removes
    it; the car's seek is a seek in seconds."""
    p, car, fd, log, files = bmw_up(probe, tmp_path)
    files['relink'].write_text('22\n')  # a relink under way (q2-iap's last run ended with 22)
    try:
        car.send(message(0xDA00))
        mid, ps = car.msg()
        assert mid == 0xDA01 and ps[0] == b'\x07'
        time.sleep(0.1)
        assert files['car'].read_text() == 'hw:CARD=UAC1Gadget,0\n44100\n'
        assert not files['relink'].exists(), "the car's audio back: the relink over"
        car.send(message(0x5003, prm(0, (125300).to_bytes(4, 'big'))))
        time.sleep(0.15)
        assert files['cmd'].read_text().split(' ', 1)[1] == 'seek 125\n'
        car.send(message(0xDA02))
        time.sleep(0.15)
        assert not files['car'].exists(), 'the car file goes with StopUSBDeviceModeAudio'
        car.send(message(0xDA00))
        car.msg()
        time.sleep(0.1)
        assert files['car'].exists()
    finally:
        stop(p, fd)
    assert not files['car'].exists(), 'and with the probe'


def log_wait(log, ok, timeout=3.0):
    """The log's text once ok(text) holds, or as it is after timeout: q2-iap's lines reach the file through its log's
    writer, a child (q2_iap.c log_child), so a line can land after the event it tells of; a test that reads the log
    while q2-iap runs waits for the line rather than for a fixed time."""
    end = time.time() + timeout
    while True:
        text = log.read_text()
        if ok(text) or time.time() > end:
            return text
        time.sleep(0.02)


def put_whole(path, text):
    """A file q2-iap reads written as its writers write theirs, whole through a rename (the player's rate file,
    player/output.c car_ask; the shim's Now Playing and queue, coda_rc_shim.c blob_io): a read never finds it empty or
    half written."""
    tmp = path.with_name(path.name + '.tmp')
    tmp.write_text(text)
    os.replace(tmp, path)


def test_bmw_audio_rate_follows_the_player(probe, tmp_path):
    """USBDeviceModeAudioInformation carries the rate index of the song about to play, as the player writes it to the
    rate file (docs/iphone-mode.md#per-song-rate): at StartUSBDeviceModeAudio, the car file's second line the same
    rate, then again at each change of index while the car takes audio: 32, 44.1 and 48 kHz are 6, 7 and 8, the 48
    kHz family above it 8, 88.2 kHz and anything unreadable 7. Nothing is sent after StopUSBDeviceModeAudio."""
    p, car, fd, log, files = bmw_up(probe, tmp_path)
    try:
        put_whole(files['rate'], '48000\n')
        time.sleep(0.1)
        car.send(message(0xDA00))
        mid, ps = car.msg()
        assert mid == 0xDA01 and ps[0] == b'\x08' and ps[1] == ps[2] == bytes(4), (hex(mid), ps)
        time.sleep(0.1)
        assert files['car'].read_text() == 'hw:CARD=UAC1Gadget,0\n48000\n'
        seen = []
        for rate, index in (('44100', 7), ('96000', 8), ('192000', 8), ('88200', 7), ('32000', 6), ('junk', 7),
                            ('176400', 7)):
            put_whole(files['rate'], rate + '\n')
            if seen and seen[-1] == index:
                time.sleep(0.15)
                continue
            mid, ps = car.msg()
            assert mid == 0xDA01, hex(mid)
            seen.append(ps[0][0])
            assert seen[-1] == index, (rate, seen)
        car.send(message(0xDA02))
        time.sleep(0.15)
        put_whole(files['rate'], '48000\n')
        with pytest.raises(Exception):
            car.msg(timeout=0.4)
    finally:
        stop(p, fd)
    assert 'audio: 48000 Hz announced (DA01 index 8)' in log.read_text()


def test_bmw_artwork(probe, tmp_path):
    """The cover from Cover Flow's cache, named in attribute 26, set up on the file transfer session, sent in pieces
    when the car says go, each table in a segment of its own (PodBox's car's need), the bytes otherwise the file's;
    with the car withholding acknowledgements, never more than its window of 5 packets out."""
    p, car, fd, log, files = bmw_up(probe, tmp_path)
    jpeg = subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'testsrc=size=160x160', '-frames:v', '1', '-q:v', '2',
                           '-f', 'mjpeg', '-'], check=True, capture_output=True).stdout
    key = fnv_part(fnv_part(2166136261, 'Massive Attack'), 'Mezzanine')
    (files['art']/f'{key:08x}.jpg').write_bytes(jpeg)
    try:
        attrs = b''.join(prm(a) for a in (0, 1, 4, 6, 12, 0x1a, 0x1b))
        car.send(message(0x5000, prm(0, attrs), prm(1, prm(0) + prm(1))))
        mid, ps = car.msg()
        media = params(ps[0])
        assert mid == 0x5001 and media[0x1a] == b'\x80' and media[0x1b] == b'\x00\x00'
        deadline = time.time() + 3
        while not car.files and time.time() < deadline:
            if (got := car.packet_any(1.0)) and got[3] == 2:
                car.files.append(got[4])
        setup = car.files.pop(0)
        size = int.from_bytes(setup[2:10], 'big')
        assert setup[:2] == b'\x80\x04' and setup[10:] == b'\x00\x02' and size > len(jpeg) - 64
        car.hold = True
        car.send(bytes([0x80, 0x01]), session=2)
        held = time.time() + 0.6  # under the 10 tries of 200 ms after which the Q2 gives the link up
        try:
            while time.time() < held:  # new packets only: those sent again are dropped by packet_any
                got = car.packet_any(max(0.05, held - time.time()))
                if got and got[3] == 2:
                    car.files.append(got[4])
        except AssertionError:
            pass
        assert 0 < len(car.files) <= 5, f'{len(car.files)} packets out with none acknowledged: the car\'s window is 5'
        car.hold = False
        car.write(packet(0x40, car.seq, car.peer, 0))
        while sum(len(f) - 2 for f in car.files) < size:
            got = car.packet_any(3.0)
            if got and got[3] == 2:
                car.files.append(got[4])
        ops = [f[1] for f in car.files]
        assert all(f[0] == 0x80 for f in car.files) and ops[0] == 0x80 and ops[-1] == 0x40 and set(ops[1:-1]) <= {0}
        got = b''.join(f[2:] for f in car.files)
        assert len(got) == size and got[:2] == b'\xff\xd8'
        segs, orig = jpeg_segments(got), jpeg_segments(jpeg)
        for mk, body in segs:
            if mk == 0xDB:
                assert len(body) in (65, 129), 'one quantisation table a segment'
            if mk == 0xC4:
                assert len(body) == 17 + sum(body[1:17]), 'one Huffman table a segment'
        tables = lambda ss, m: b''.join(b for k, b in ss if k == m)
        assert tables(segs, 0xDB) == tables(orig, 0xDB) and tables(segs, 0xC4) == tables(orig, 0xC4)
        assert got[got.index(b'\xff\xda'):] == jpeg[jpeg.index(b'\xff\xda'):], 'the scan as it was'
        assert sum(1 for k, _ in orig if k in (0xDB, 0xC4)) < sum(1 for k, _ in segs if k in (0xDB, 0xC4)), \
            'the test JPEG had tables to split'
    finally:
        stop(p, fd)


def np_updates(car):
    """The car's StartNowPlayingUpdates for title, duration, artist, album and the cover, and the state and position;
    the first NowPlayingUpdate's (media, playback)."""
    attrs = b''.join(prm(a) for a in (0, 1, 4, 6, 12, 0x1a))
    car.send(message(0x5000, prm(0, attrs), prm(1, prm(0) + prm(1))))
    mid, ps = car.msg()
    assert mid == 0x5001
    return params(ps.get(0, b'')), params(ps[1])


def np_and_files(car, until, seconds=1.5):
    """Packets for up to seconds, acknowledged as the BMW does: [(media, playback)] of each NowPlayingUpdate and
    {transfer id: its setup's size} of the file transfer session, until until(updates) holds."""
    updates, setups, end = [], {}, time.time() + seconds
    while time.time() < end and not until(updates):
        try:
            got = car.packet_any(0.3)
        except AssertionError:
            continue
        if not got or not got[4]:
            continue
        session, pl = got[3], got[4]
        if session == 2 and pl[1] == 0x04:
            setups[pl[0]] = int.from_bytes(pl[2:10], 'big')
        elif session != 2 and pl[:2] == b'\x40\x40' and (pl[4] << 8 | pl[5]) == 0x5001:
            ps = params(pl[6:])
            updates.append((params(ps.get(0, b'')), params(ps.get(1, b''))))
    return updates, setups


def test_bmw_radio_is_live_and_clears_the_cover(probe, tmp_path):
    """A radio in Now Playing (coda_rc_shim.c cr_now: source=radio, live=1, no path) after a Library song with a cover:
    its song and artist, the station as the album, no duration and no position, and a new cover that is an empty file
    transfer, even with a cover cached for its words. Leaving the cover out kept the song's on the BMW's cluster
    (Radio over USB on q2muse-1123); an empty transfer is how an iPhone says there is none. No position
    after either, as it plays (docs/internals.md#now-playing-source)."""
    p, car, fd, log, files = bmw_up(probe, tmp_path)
    jpeg = subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'testsrc=size=160x160', '-frames:v', '1',
                           '-f', 'mjpeg', '-'], check=True, capture_output=True).stdout
    for artist, album in (('Massive Attack', 'Mezzanine'), ('System of a Down', '93XRT 93.1 FM')):
        (files['art']/f'{fnv_part(fnv_part(2166136261, artist), album):08x}.jpg').write_bytes(jpeg)
    car.hold = False
    try:
        attrs = b''.join(prm(a) for a in (0, 1, 4, 6, 12, 0x1a))
        car.send(message(0x5000, prm(0, attrs), prm(1, prm(0) + prm(1))))
        updates, setups = np_and_files(car, lambda u: False, 0.8)
        song = updates[0][0]
        assert song[1] == b'Teardrop\0' and setups[song[0x1a][0]] > 0, 'the Library song\'s cover first'
        put_whole(files['np'], 'state=1\npos=0\nlen=0\nsource=radio\nlive=1\nstation=93XRT\ntitle=Toxicity\n'
                               'artist=System of a Down\nalbum=93XRT 93.1 FM\npath=\n')
        updates, more = np_and_files(car, lambda u: False, 1.5)
        setups.update(more)
        radio = [(m, pb) for m, pb in updates if m.get(1) == b'Toxicity\0']
        assert radio, [m.get(1) for m, _ in updates]
        media, play = radio[0]
        assert media[6] == b'93XRT 93.1 FM\0' and media[12] == b'System of a Down\0'
        assert 4 not in media and play[0] == b'\x01' and 1 not in play, (media.keys(), play.keys())
        assert 0x1a in media, 'a cover named, or the car keeps the song\'s'
        art = media[0x1a][0]
        assert art != song[0x1a][0] and setups.get(art) == 0, (art, setups)
        assert not any(1 in pb for _, pb in updates[updates.index(radio[0]):]), 'no position for a stream'
    finally:
        stop(p, fd)


def test_bmw_a_seek_while_paused_is_sent(probe, tmp_path):
    """Paused, the position is sent when it moves (a seek from the Q2 or the phone), not only while playing: the car
    shows where the song will go on from."""
    p, car, fd, log, files = bmw_up(probe, tmp_path)
    put_whole(files['np'], files['np'].read_text().replace('state=1', 'state=2'))
    try:
        media, play = np_updates(car)
        assert play[0] == b'\x02' and int.from_bytes(play[1], 'big') == 61000
        put_whole(files['np'], files['np'].read_text().replace('pos=61000', 'pos=120000'))
        end, seen = time.time() + 1.5, []
        while time.time() < end and not seen:
            try:
                mid, ps = car.msg(0.3)
            except AssertionError:
                continue
            if mid == 0x5001 and 1 in ps and 1 in params(ps[1]):
                seen.append(int.from_bytes(params(ps[1])[1], 'big'))
        assert seen == [120000], seen
    finally:
        stop(p, fd)


# ---- Car Audio in the image (--state): what the host is, and the car's audio health ----

def car_daemon(probe, tmp_path, *more, kinds=('ptp', 'hid', 'ptpmux', 'ptpmuxeth'), udc='default'):
    """q2-iap as q2car.sh runs it: the four instances, the state file, the controller's state file; (process,
    {kind: FakeFfs}, log, state, udc)."""
    ffs, args = {}, []
    for k in kinds:
        ffs[k] = FakeFfs(tmp_path/k)
        args.append(f'{ffs[k].path}:{k}')
    log, state, udc_file = tmp_path/'q2car.log', tmp_path/'q2car.state', tmp_path/'udc'
    udc_file.write_text(udc + '\n')
    p = subprocess.Popen([probe, '--state', str(state), '--udc', str(udc_file), '--under', str(tmp_path/'under'),
                          *more, str(log), *args])
    for k in kinds:
        d, st = blobs(probe, k)
        assert ffs[k].read(len(d)) == d and ffs[k].read(len(st)) == st
    return p, ffs, log, state, udc_file


def verdict(state):
    return dict(line.split('=', 1) for line in state.read_text().splitlines())


@pytest.mark.parametrize('kind, setup, why', [
    ('ptp', None, 'configuration 1'), ('ptpmux', None, 'configuration 3'), ('ptpmuxeth', None, 'configuration 4'),
    ('ptp', (0xc0, 0x45, 0, 0, 4), "usbmux's C0 45"), ('ptp', (0x40, 0x51, 1, 0, 0), 'the role switch 40 51')])
def test_a_computer_ends_it_at_once(probe, tmp_path, kind, setup, why):
    """Windows with Apple's driver chose configuration 1 and sent C0 45 (the desk, 2026-11-19); a Mac or Linux picks a
    configuration with PTP; wired CarPlay's role switch is nothing the Q2 can be. Each ends q2-iap with 20."""
    p, ffs, log, state, _ = car_daemon(probe, tmp_path)
    try:
        if setup:
            ffs[kind].setup(*setup)
        else:
            ffs[kind].event(ENABLE)
        assert p.wait(5) == 20
    finally:
        p.kill()
    v = verdict(state)
    assert (v['verdict'], v['why'], v['host']) == ('computer', why, '1')
    assert 'summary: host computer (' + why in log.read_text()


def test_a_car_s_c0_53_keeps_it_but_a_computer_after_it_still_wins(probe, tmp_path):
    p, ffs, log, state, _ = car_daemon(probe, tmp_path)
    try:
        ffs['ptp'].setup(0xc0, 0x53, 0, 0, 4)   # before a configuration: CONFIG0_SETUP's instance
        assert ffs['ptp'].read(4) == bytes(4)
        ffs['ptp'].setup(0x40, 0x40, 500, 1000, 0)  # Apple's charging request decides nothing
        time.sleep(0.3)
        assert p.poll() is None and verdict(state)['verdict'] == 'car' and verdict(state)['why'] == "Apple's C0 53"
        ffs['ptp'].event(ENABLE)
        assert p.wait(5) == 20
    finally:
        p.kill()
    assert verdict(state)['verdict'] == 'computer'


def test_after_the_detect_it_is_a_car_for_good(probe, tmp_path):
    p, ffs, log, state, _ = car_daemon(probe, tmp_path, '--auto-ms', '300')
    try:
        ffs['hid'].event(ENABLE)
        ffs['hid'].setup(0x21, 9, 0x020e, 2, 10, bytes.fromhex('0e00ff550200ee100000'))
        time.sleep(0.3)
        ffs['ptp'].event(ENABLE)
        ffs['ptp'].setup(0xc0, 0x45, 0, 0, 4)
        time.sleep(0.8)
        assert p.poll() is None
        assert verdict(state)['verdict'] == 'car' and verdict(state)['why'] == "iAP2's detect"
    finally:
        p.terminate()
        p.wait(5)


def test_a_host_that_asks_nothing_apple_s_is_let_go(probe, tmp_path):
    """Addressed (the controller's state) and then nothing Apple's within --auto-ms: exit 21, a silent host."""
    p, ffs, log, state, udc = car_daemon(probe, tmp_path, '--auto-ms', '400')
    try:
        time.sleep(0.6)
        assert p.poll() is None and verdict(state)['host'] == '0', 'no host yet: a charger, nothing decided'
        put_whole(udc, 'addressed\n')
        t = time.time()
        assert p.wait(5) == 21
        assert 0.3 < time.time() - t < 2.0
    finally:
        p.kill()
    v = verdict(state)
    assert v['verdict'] == 'silent' and v['why'] == "nothing Apple's in 400 ms" and v['host'] == '1'


def test_configuration_2_waits_the_longer_car_window(probe, tmp_path):
    """Only an iPod accessory chooses the iPod USB Interface; a car that does may be silent a while (OrangeCar's Jaguar,
    22 to 27 s), so --car-ms, not --auto-ms."""
    p, ffs, log, state, udc = car_daemon(probe, tmp_path, '--auto-ms', '300', '--car-ms', '1500', udc='configured')
    try:
        ffs['hid'].event(ENABLE)
        time.sleep(0.8)
        assert p.poll() is None
        assert p.wait(5) == 21
    finally:
        p.kill()
    assert verdict(state)['why'] == "nothing Apple's in 1500 ms on configuration 2"


def test_the_car_s_audio_health(probe, tmp_path):
    """Once the car asks for audio: flowing when the module's in_sent moves, dropouts when over 1% of a second's
    packets were missed, underruns when the player's count moves, no sound when nothing was taken in 5 s; and the
    session's summary with the counts since the start."""
    counters = tmp_path/'params'
    counters.mkdir()
    put_whole(counters/'in_sent', '1000\n')
    put_whole(counters/'in_missed', '7\n')
    for k, v in (('p_rate', 48000), ('rate_sets', 2), ('rate_asked', 44100)):
        put_whole(counters/k, f'{v}\n')
    p, car, fd, log, files = bmw_up(probe, tmp_path, '--state', str(tmp_path/'state'), '--counters', str(counters),
                                    '--under', str(tmp_path/'under'))
    state = tmp_path/'state'
    try:
        time.sleep(1.2)
        assert verdict(state)['verdict'] == 'car' and verdict(state)['name'] == 'BMW 83828'
        car.send(message(0xDA00))
        car.msg()
        time.sleep(1.2)
        put_whole(counters/'in_sent', '2000\n')
        time.sleep(1.2)
        v = verdict(state)
        assert (v['audio'], v['flowing'], v['trouble'], v['troubles']) == ('1', '1', '', '0')
        put_whole(counters/'in_sent', '2990\n')
        put_whole(counters/'in_missed', '17\n')       # 10 of 1000
        time.sleep(1.2)
        assert verdict(state)['troubles'] == '0', '1% is not over 1%'
        put_whole(counters/'in_sent', '3970\n')
        put_whole(counters/'in_missed', '37\n')       # 20 of 1000
        time.sleep(1.2)
        v = verdict(state)
        assert (v['trouble'], v['troubles']) == ('dropouts', '1')
        put_whole(counters/'in_sent', '4970\n')
        put_whole(tmp_path/'under', '1\n')
        time.sleep(1.2)
        v = verdict(state)
        assert (v['trouble'], v['troubles']) == ('underruns', '2')
    finally:
        stop(p, fd)
    text = log.read_text()
    assert 'summary: host car (iAP2\'s detect' in text and 'car "BMW 83828", identified yes' in text
    assert 'in_sent 3970 in_missed 30 (0.7%), underruns 1, problems 2, p_rate 48000 rate_sets 2 rate_asked 44100' in text


def test_the_car_s_stream_a_line_a_second(probe, tmp_path):
    """While the car has asked for audio, a line a second from the module's counters (uac1_build.py COUNTED): that
    second's completions by status, resends, the frames the host took, packet sizes and the longest gap (windows the
    line restarts by writing them back), gaps over 2 ms, AS IN alternate settings, rate changes; and the cable's
    summary: the start latency, the longest gap, the toggles and the rate changes."""
    c = tmp_path/'params'
    c.mkdir()
    base = dict(in_sent=1000, in_missed=0, in_enodata=0, in_exdev=0, in_eshutdown=0, in_other=0, in_resent=0, in_bytes=0,
                in_zero=0, in_gaps2=0, in_alt_on=1, in_alt_off=0, rate_sets=1, in_pkt_min=4294967295, in_pkt_max=0,
                in_gap_max=0, p_rate=44100, rate_asked=44100)
    for k, v in base.items():
        put_whole(c/k, f'{v}\n')
    p, car, fd, log, files = bmw_up(probe, tmp_path, '--state', str(tmp_path/'state'), '--counters', str(c))
    try:
        car.send(message(0xDA00))
        car.msg()
        time.sleep(1.3)
        # a second as the 00:24 clip might have been: the host toggling the stream, one gap of 340 ms
        for k, v in dict(in_sent=1998, in_missed=3, in_enodata=2, in_other=1, in_resent=3, in_bytes=176400, in_zero=2,
                         in_gaps2=1, in_alt_on=3, in_alt_off=2, rate_sets=2, in_pkt_min=176, in_pkt_max=180,
                         in_gap_max=332031).items():
            put_whole(c/k, f'{v}\n')
        time.sleep(1.1)
    finally:
        stop(p, fd)
    lines = [l.split(' car ', 1)[1] for l in log.read_text().splitlines() if ' car usb ' in l]
    want = ('usb 1000ms: sent=998 missed=3 (enodata 2 exdev 0 shutdown 0 other 1) resent=3 zero=2 frames=44100 '
            'pkt=176/180 gapmax=339.9ms gaps2=1 alt=+2/-2 rate_sets=1')
    assert want in lines, lines
    assert (c/'in_gap_max').read_text().strip() == '0' and (c/'in_pkt_min').read_text().strip() == '4294967295', \
        'the windows restarted'
    text = log.read_text()
    assert 'longest gap 339.9 ms, alt toggles 4, rate changes 1' in text, text[-600:]


def test_no_sound_when_the_car_asks_and_takes_nothing(probe, tmp_path):
    counters = tmp_path/'params'
    counters.mkdir()
    put_whole(counters/'in_sent', '5\n')
    put_whole(counters/'in_missed', '0\n')
    p, car, fd, log, files = bmw_up(probe, tmp_path, '--state', str(tmp_path/'state'), '--counters', str(counters))
    try:
        car.send(message(0xDA00))
        car.msg()
        time.sleep(7.5)
        v = verdict(tmp_path/'state')
        assert (v['flowing'], v['trouble']) == ('0', 'nostream')
    finally:
        stop(p, fd)


# ---- the library on iDrive, playing from it, and Up Next (q2_iap_lib.c) ----

def plist(body):
    """A message's parameters in order, repeats kept: [(id, data)]."""
    out, off = [], 0
    while off + 4 <= len(body):
        n = body[off] << 8 | body[off + 1]
        out.append((body[off + 2] << 8 | body[off + 3], body[off + 4:off + n]))
        off += n
    return out


SONGS = [  # id, title, album, artist, album artist, genre, track, disc, path
    (3, 'Teardrop', 'Mezzanine', 'Massive Attack', '', 'Trip Hop', 3, 1, '/mnt/mmc/m/03.flac'),
    (5, 'Angel', 'Mezzanine', 'Massive Attack', '', 'Trip Hop', 1, 1, '/mnt/mmc/m/01.flac'),
    (7, 'Risingson', 'Mezzanine', 'Massive Attack', '', 'Trip Hop', 2, 1, '/mnt/mmc/m/02.flac'),
    (9, 'Roads', 'Dummy', 'Portishead', '', 'Trip Hop', 6, 1, '/mnt/mmc/d/06.flac'),
    (11, 'A Book', '', '', '', '', 0, 0, '/mnt/mmc/audiobooks/a.mp3'),  # not music: never sent
]


# Stock's songtable as the Q2's /mnt/data/database.db has it (read off the user's Q2, 2026-11-20), and the catalogue's
# servertable and servermeta as /mnt/mmc/.q2pod/catalogue.db has them (patch/catalogue.c)
SONGTABLE = ('CREATE TABLE songtable(id INTEGER PRIMARY KEY AUTOINCREMENT, filename VARCHAR(512), fileurl VARCHAR(1024),'
             ' album VARCHAR(512), songer VARCHAR(256), style VARCHAR(128), name VARCHAR(512), composer VARCHAR(256),'
             ' albumsonger VARCHAR(256), year INTEGER, track INTEGER, cd_num INTEGER, time INTEGER, time_end INTEGER,'
             ' time_create INTEGER, Initial INTEGER, bitdepth INTEGER, samprate INTEGER)')
SERVERTABLE = ('CREATE TABLE servertable(id INT, filename, fileurl, album, songer, style, name, composer, albumsonger,'
               ' year INT, track INT, cd_num INT, time INT, time_end INT, time_create INT, Initial INT, bitdepth INT,'
               ' samprate INT)')
SERVERMETA = ('CREATE TABLE servermeta(id integer primary key, fileurl, app, sid, local, seconds integer, size integer,'
              ' mbid, suffix, dup, ntitle integer, nartist integer, naa integer, nalbum integer, track integer)')


def rows(db, table, songs):
    for i, t, al, ar, aa, g, tr, d, url in songs:
        db.execute(f'insert into {table} values (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                   (i, url.rsplit('/', 1)[1], url, al, ar, g, t, 'Composer ' + ar if ar else '', aa, 1998, tr, d, 0, 0,
                    0, 0, 16, 44100))


def song_db(path, songs=SONGS):
    import sqlite3
    path.unlink(missing_ok=True)
    db = sqlite3.connect(path)
    db.execute(SONGTABLE)
    rows(db, 'songtable', songs)
    db.commit()
    db.close()


def catalogue_db(path, songs, seconds=245):
    import sqlite3
    path.unlink(missing_ok=True)
    db = sqlite3.connect(path)
    db.execute(SERVERTABLE)
    db.execute(SERVERMETA)
    rows(db, 'servertable', songs)
    for i, *_, url in songs:
        db.execute('insert into servermeta (id, fileurl, app, sid, seconds) values (?,?,?,?,?)', (i, url, 'subsonic', 'x', seconds))
    db.commit()
    db.close()


def lib_car(probe, tmp_path, *more, run='run1'):
    """A car up to identification with the library's files: the database and the sent record in tmp_path, kept from
    one run (a cable) to the next, the rest in run."""
    db, sent = tmp_path/'database.db', tmp_path/'q2-iap.library'
    if not db.exists():
        song_db(db)
    (tmp_path/run).mkdir()
    p, car, fd, log, files = bmw_up(probe, tmp_path/run, '--lib-db', str(db), '--lib-cat', str(tmp_path/'catalogue.db'),
                                    '--lib-sent', str(sent), '--list-file',
                                    str(tmp_path/'list'), '--queue-file', str(tmp_path/'queue'), *more)
    return p, car, fd, log, files, db, sent


def library(car, rev=None, timeout=3.0):
    """The car's StartMediaLibraryUpdates and every update to the last, the one with progress 100 and the revision
    itself (not a first update's or a checkpoint's partial one): (items, deleted, revision, reset seen)."""
    car.send(message(0x4C03, prm(0, b'lib\0'), *([prm(1, rev.encode() + b'\0')] if rev else [])))
    items, gone, reset = [], [], False
    while True:
        mid, _ = car.msg(timeout)
        body = car.last
        assert mid == 0x4C04
        ps = plist(body)
        ids = [i for i, _ in ps]
        reset |= 6 in ids
        items += [params(d) for i, d in ps if i == 2]
        gone += [int.from_bytes(d, 'big') for i, d in ps if i == 4]
        got = dict(ps).get(1, b'').rstrip(b'\0').decode()
        if 7 in ids and dict(ps)[7] == b'\x64' and not got.startswith(('q2p.', 'q2d.')):
            return items, gone, got, reset


def test_the_library_whole_then_nothing_then_the_changes(probe, tmp_path, monkeypatch):
    """A car that holds nothing gets every song (audiobooks aside) with a reset, ids under 0x51320001, albums, artists
    and genres by name; one that holds that revision gets the revision alone; once a song is added, one changed and one
    removed, a car that holds the last revision sent whole gets those three alone, the removed one by id, no reset."""
    monkeypatch.setattr(BmwCar, 'msg', bmw_msg_keeping_body)
    p, car, fd, log, files, db, sent = lib_car(probe, tmp_path)
    try:
        items, gone, rev, reset = library(car)
        assert reset and not gone and [int.from_bytes(i[0], 'big') for i in items] == [0x5132000100000000 | r for r in (3, 5, 7, 9)]
        teardrop = items[0]
        assert teardrop[1] == b'Teardrop\0' and teardrop[6] == b'Mezzanine\0' and teardrop[12] == b'Massive Attack\0'
        assert teardrop[14] == b'Massive Attack\0' and teardrop[16] == b'Trip Hop\0' and teardrop[7] == b'\x00\x03'
        assert teardrop[5][:4] == b'\x51\x32\x00\x0a' and items[1][5] == teardrop[5], 'one album, one id'
        time.sleep(0.2)
        assert sent.read_text().splitlines()[0] == rev
        items, gone, rev2, reset = library(car, rev)
        assert (items, gone, rev2, reset) == ([], [], rev, False)
    finally:
        stop(p, fd)
    song_db(db, [s for s in SONGS if s[0] != 7] + [(13, 'Glory Box', 'Dummy', 'Portishead', '', 'Trip Hop', 11, 1, '/mnt/mmc/d/11.flac')])
    import sqlite3
    c = sqlite3.connect(db)
    c.execute("update songtable set name='Angel (Remastered)' where id=5")
    c.commit()
    c.close()
    p, car, fd, log, files, db, sent = lib_car(probe, tmp_path, run='run2')
    try:
        items, gone, rev3, reset = library(car, rev)
        assert not reset and rev3 != rev
        assert sorted(int.from_bytes(i[0], 'big') & 0xffffffff for i in items) == [5, 13]
        assert gone == [0x5132000100000007]
    finally:
        stop(p, fd)
    assert 'library: changes sent, 2 songs of 4' in log.read_text()


def bmw_msg_keeping_body(self, timeout=3.0):
    """BmwCar.msg, keeping the message's raw parameters (repeats and all) in self.last."""
    while True:
        got = self.packet_any(timeout)
        if got is None:
            continue
        ctl, seq, ack, session, pl = got
        if not pl:
            continue
        if session == 2:
            self.files.append(pl)
            continue
        self.last = pl[6:]
        return pl[4] << 8 | pl[5], params(pl[6:])


def test_the_library_is_the_card_s_and_the_server_s(probe, tmp_path, monkeypatch):
    """The user's Q2 (2026-11-20): songtable empty, every song a signed-in server's (9,199 in the catalogue), and iDrive
    got 0 songs. With the catalogue on the card its servertable's songs go too (rids from 0x40000000, a length from
    servermeta, composers), after the card's, and a server album plays from iDrive as the shim's list of those rids."""
    monkeypatch.setattr(BmwCar, 'msg', bmw_msg_keeping_body)
    song_db(tmp_path/'database.db', [SONGS[1]])
    server = [(0x40000000 + k, t, 'Vide Noir', 'Lord Huron', '', 'Rock', k + 1, 1,
               f'/mnt/mmc/.subsonic/cat/a/{k}.flac') for k, t in enumerate(('Lost in Time and Space', 'Never Ever'))]
    catalogue_db(tmp_path/'catalogue.db', server)
    p, car, fd, log, files, db, sent = lib_car(probe, tmp_path)
    try:
        items, gone, rev, reset = library(car)
        assert [int.from_bytes(i[0], 'big') & 0xffffffff for i in items] == [5, 0x40000000, 0x40000001]
        assert items[1][1] == b'Lost in Time and Space\0' and items[1][4] == (245000).to_bytes(4, 'big')
        assert items[1][18] == b'Composer Lord Huron\0' and 4 not in items[0], 'a card song: no length in the database'
        car.send(message(0x4C08, prm(0, items[1][5]), prm(1, b'\x02'), prm(2, (1).to_bytes(4, 'big'))))
        time.sleep(0.2)
        assert (tmp_path/'list').read_text() == f'{0x40000000}\n{0x40000001}\n'
        assert files['cmd'].read_text().split(' ', 1)[1] == 'play 1 0 2\n'
    finally:
        stop(p, fd)
    assert "3 songs read (the card's and the server's)" in log.read_text()


def test_the_link_keeps_to_the_car_s_pace(probe, tmp_path, monkeypatch):
    """The drive of 2026-11-21 (q2muse-1124): the BMW takes input reports at an interrupt endpoint's pace (64 bytes
    every 8 ms, so a 95-byte report 16 ms: --report-ms 16 makes the writer that slow), and with the library's 1,024-byte
    packets five deep in its window the Q2's acknowledgement of the car's own packet waited behind most of a second of
    them; the car sent its packet again 10 times, gave the link up, and the Q2's packets went unacknowledged too. Here the
    library flows (2,000 songs) and the car sends a packet; the Q2 must acknowledge it within the car's first two tries
    (400 ms), send none of its own again for nothing, and never lose the link."""
    monkeypatch.setattr(BmwCar, 'msg', bmw_msg_keeping_body)
    song_db(tmp_path/'database.db', [(i, f'Song {i}', f'Album {i // 10}', f'Artist {i // 50}', '', 'Rock', i % 10 + 1, 1,
                                      f'/mnt/mmc/m/{i}.flac') for i in range(1, 2001)])
    p, car, fd, log, files, db, sent = lib_car(probe, tmp_path, '--report-ms', '16')
    waits = []
    try:
        car.send(message(0x4C03, prm(0, b'lib\0')))
        for _ in range(3):
            end = time.time() + 1.5
            while time.time() < end:  # the library flowing, acknowledged as it comes
                car.packet_any(1.0)
            payload = message(0x5003, prm(0, (100000).to_bytes(4, 'big')))
            car.send(payload)
            mine, t0, last, tries, acked = car.seq, time.time(), time.time(), 1, None
            while acked is None and time.time() - t0 < 2.2:
                got = car.packet_any(1.0)
                if got and (got[2] - mine) % 256 < 128:
                    acked = time.time() - t0
                elif time.time() - last > 0.2 and tries < 10:  # the BMW's 200 ms, 10 tries
                    car.write(packet(0x40, mine, car.peer, 1, payload))
                    last, tries = time.time(), tries + 1
            waits.append(acked)
    finally:
        stop(p, fd)
    text = log.read_text()
    assert all(w is not None and w < 0.4 for w in waits), f"the car's packet acknowledged after {waits} s"
    assert 'link: lost' not in text
    assert text.count('(again)') <= 2, f"{text.count('(again)')} packets sent again"


def test_a_lost_link_shows_the_iphone_again(probe, tmp_path):
    """After the loss on 2026-11-21 the BMW sent nothing for two minutes, to the cable out. With --state a lost link
    ends q2-iap with 22, for q2car.sh to take the gadget off the controller and back, and the car to start afresh."""
    p, car, fd, log, files = bmw_up(probe, tmp_path, '--state', str(tmp_path/'state'))
    try:
        car.hold = True  # the car acknowledges nothing more
        car.send(message(0xDA00))
        assert p.wait(6) == 22
    finally:
        p.kill()
        os.close(fd)
    text = log.read_text()
    assert 'link: lost' in text and 'the iPhone shown again' in text and 'identified yes' in text
    assert files['relink'].read_text() == '22\n', "the relink's note, for demo to hold its pause (usb_car.c)"


def test_a_data_stage_that_never_comes_holds_nothing(probe, tmp_path):
    """The 2026-11-21 05:3x session: a SET_REPORT whose data never came held q2-iap in the read for 35 s, and the car
    dropped the link. The read gives up after 2 s and the next request is answered."""
    p, ffs, log = start(probe, tmp_path)
    try:
        ffs.event(ENABLE)
        ffs.setup(0x21, 9, 0x020e, 2, 10)  # no data follows
        time.sleep(2.5)  # past the read's 2 s (the stand-in ep0 is one stream: a request sooner would be read as data)
        ffs.setup(0x81, 6, 0x2200, 2, 0x1000)
        assert len(ffs.read(208, timeout=2)) == 208
    finally:
        p.terminate()
        p.wait(5)
    assert 'read -1 (errno 4' in log.read_text()


def acked_within(car, seconds):
    """The car's own packet sent and the seconds until the Q2 acknowledged it, or None."""
    car.send(message(0x5003, prm(0, (100000).to_bytes(4, 'big'))))
    mine, t0 = car.seq, time.time()
    while time.time() - t0 < seconds:
        try:
            got = car.packet_any(0.2)
        except AssertionError:
            continue
        if got and (got[2] - mine) % 256 < 128:
            return time.time() - t0
    return None


def test_writing_the_sent_record_holds_nothing(probe, tmp_path, monkeypatch):
    """The drive of 2026-11-21 (q2muse-1125): at "library: whole sent" (34110.909) q2-iap wrote the sent record, a line
    a song in a write each, 9,200 of them to /mnt/data, which is mounted sync, and its loop did nothing else for 12.3 s:
    the car's next SET_REPORT went unanswered until it gave the transfer up. The record's temporary name here is a FIFO,
    so its writing waits until the test reads it, as a slow write would; the car's packet must still be acknowledged
    within the BMW's first two tries (400 ms), and the record arrives whole once read."""
    monkeypatch.setattr(BmwCar, 'msg', bmw_msg_keeping_body)
    tmp_name = tmp_path/'q2-iap.library.iap'
    os.mkfifo(tmp_name)
    p, car, fd, log, files, db, sent = lib_car(probe, tmp_path)
    record, r = b'', -1
    try:
        items, gone, rev, reset = library(car)
        waited = acked_within(car, 2.0)
        r = os.open(tmp_name, os.O_RDONLY | os.O_NONBLOCK)  # the record's writer goes on
        end = time.time() + 3
        while time.time() < end and len(record.splitlines()) < 1 + len(items):
            try:
                record += os.read(r, 65536)
            except BlockingIOError:
                pass
            time.sleep(0.02)
    finally:
        if r < 0:
            r = os.open(tmp_name, os.O_RDONLY | os.O_NONBLOCK)
        stop(p, fd)
        os.close(r)
    assert waited is not None and waited < 0.4, f"the car's packet acknowledged after {waited} s"
    lines = record.decode().splitlines()
    assert lines[0] == rev and len(lines) == 1 + len(items), lines
    assert 'library: the sent record written' in log.read_text()


def test_a_request_read_as_data_shows_the_iphone_again(probe, tmp_path):
    """q2muse-1125 at 34123.221, the first line after the 12.3 s stall: the data stage of the car's SET_REPORT (14 bytes)
    came back as 8 bytes, 01 0b 00 00 01 00 00 00, a request of its own (SET_INTERFACE, alternate setting 0 of interface
    1): the car had given the transfer up and sent its next request, which the gadget never answered. Nothing came
    from the car again, over ep0 or for ep1, for the 23 minutes to the cable out. Those bytes are not iAP2's, and the
    link is lost: with --state, exit 22, for q2car.sh to show the iPhone again."""
    p, car, fd, log, files = bmw_up(probe, tmp_path, '--state', str(tmp_path/'state'))
    try:
        car.ffs.setup(0x21, 9, 0x020f, 2, 14, bytes.fromhex('010b000001000000'))
        assert p.wait(3) == 22
    finally:
        p.kill()
        os.close(fd)
    text = log.read_text()
    assert 'read 8 ' in text and 'not the report asked for' in text and 'the iPhone shown again' in text


def test_a_link_gone_quiet_with_packets_waiting_is_lost(probe, tmp_path):
    """q2muse-1125 from 34123: the car took no input report and sent nothing for 23 minutes. The reports already handed
    to the writer never went, so no packet went to the wire (pacing: only while no more than 4 wait), 2,555 were
    dropped from a full queue, and the count of tries, kept only while the wire is clear, never ran: nothing called the
    link lost. Here the car stops taking reports (ep1's pipe made small and filled) and says nothing; with packets
    waiting and nothing from the car for --quiet-ms, the link is lost: exit 22."""
    p, car, fd, log, files = bmw_up(probe, tmp_path, '--state', str(tmp_path/'state'), '--quiet-ms', '1500')
    w = os.open(car.ffs.path/'ep1', os.O_WRONLY | os.O_NONBLOCK)
    try:
        fcntl.fcntl(w, 1031, 4096)  # F_SETPIPE_SZ: the smallest pipe
        try:
            while True:
                os.write(w, bytes(512))
        except BlockingIOError:
            pass
        attrs = b''.join(prm(a) for a in (0, 1, 4, 6, 12))
        car.send(message(0x5000, prm(0, attrs), prm(1, prm(0) + prm(1))))  # its last word: Now Playing is owed it
        t0 = time.time()
        assert p.wait(6) == 22
        took = time.time() - t0
    finally:
        p.kill()
        os.close(w)
        os.close(fd)
    text = log.read_text()
    assert 'link: lost (nothing from the car' in text and 'the iPhone shown again' in text
    assert took > 1.0, took


def test_a_quiet_car_with_nothing_waiting_keeps_the_link(probe, tmp_path):
    """The other side of the quiet rule: a car that says nothing while the Q2 has nothing waiting (paused, everything
    acknowledged) keeps the link, and the Q2's next packet after a long quiet is not lost at once."""
    p, car, fd, log, files = bmw_up(probe, tmp_path, '--state', str(tmp_path/'state'), '--quiet-ms', '1000')
    try:
        put_whole(files['np'], files['np'].read_text().replace('state=1', 'state=2'))
        end = time.time() + 2.5
        while time.time() < end:  # the pause's update acknowledged, then nothing either way
            try:
                car.packet_any(0.2)
            except AssertionError:
                pass
        assert p.poll() is None
        assert acked_within(car, 1.0) is not None
        time.sleep(0.5)
        assert p.poll() is None
    finally:
        stop(p, fd)
    assert 'link: lost' not in log.read_text()


# ---- the log's diagnostics (docs/iphone-mode.md#the-loop-s-steps): what the next stall or dead link says of itself ----

def test_a_stall_names_its_step(probe, tmp_path):
    """A loop pass that holds 100 ms or more is logged with the step that took most of it. The cover's file is a FIFO
    nobody writes for 400 ms first: its read is in the cover's child, so it holds nothing. Then the Now Playing file is
    one, so its read holds the loop as a slow card would, and the stall names it."""
    p, car, fd, log, files = bmw_up(probe, tmp_path, '--stall-ms', '100')
    key = fnv_part(fnv_part(2166136261, 'Massive Attack'), 'Mezzanine')
    art = files['art']/f'{key:08x}.jpg'
    os.mkfifo(art)
    try:
        attrs = b''.join(prm(a) for a in (0, 1, 4, 6, 12, 0x1a))
        car.send(message(0x5000, prm(0, attrs), prm(1, prm(0) + prm(1))))
        time.sleep(0.4)
        w = os.open(art, os.O_WRONLY)  # the child's read goes on, to an empty file
        os.close(w)
        time.sleep(0.3)
        assert not [l for l in log.read_text().splitlines() if 'loop: a pass held' in l]
        files['np'].unlink()
        os.mkfifo(files['np'])
        time.sleep(0.7)  # past the next 500 ms look, which waits in its open from then
        w = os.open(files['np'], os.O_WRONLY)
        os.close(w)
        files['np'].unlink()
        put_whole(files['np'], 'state=0\n')  # a file again, so the next look does not wait for a writer
        time.sleep(0.3)
    finally:
        stop(p, fd)
    stalls = [l for l in log.read_text().splitlines() if 'loop: a pass held' in l]
    assert any("in the Now Playing file" in l for l in stalls), stalls
    held = max(int(l.split('held ')[1].split(' ms')[0]) for l in stalls)
    assert 200 <= held < 2000, stalls


def test_ep0_s_odd_reads_are_said(probe, tmp_path):
    """Every ep0 read that is not a whole event, and a SET_REPORT whose data stage never came, is a line saying what
    was read and what q2-iap did next."""
    p, car, fd, log, files = bmw_up(probe, tmp_path)
    try:
        car.ffs.setup(0x21, 9, 0x020f, 2, 14)  # no data stage
        time.sleep(2.5)
        os.write(car.ffs.master, b'\x01\x02\x03\x04\x05')  # five bytes where an event's twelve belong
        time.sleep(0.3)
    finally:
        stop(p, fd)
    text = log.read_text()
    assert 'SET_REPORT: no data stage' in text and 'the loop goes on' in text
    assert "ep0: read 5 of an event's 12 bytes" in text


def test_a_silence_is_seen_as_it_grows(probe, tmp_path):
    """The link's health every --health-ms (reports waiting, packets queued, the ages of the last send, word and
    acknowledgement), the tries said to be held while the wire is busy, and at the lost link the kernel's USB lines:
    the 1125 drive's silence, line by line."""
    p, car, fd, log, files = bmw_up(probe, tmp_path, '--state', str(tmp_path/'state'), '--quiet-ms', '3500',
                                    '--health-ms', '500')
    w = os.open(car.ffs.path/'ep1', os.O_WRONLY | os.O_NONBLOCK)
    try:
        fcntl.fcntl(w, 1031, 4096)  # F_SETPIPE_SZ
        try:
            while True:
                os.write(w, bytes(512))
        except BlockingIOError:
            pass
        attrs = b''.join(prm(a) for a in (0, 1, 4, 6, 12))
        car.send(message(0x5000, prm(0, attrs), prm(1, prm(0) + prm(1))))
        assert p.wait(8) == 22
    finally:
        p.kill()
        os.close(w)
        os.close(fd)
    text = log.read_text()
    health = [l for l in text.splitlines() if 'link: health: ' in l]
    assert len(health) >= 4 and 'reports waiting' in health[-1] and 'quiet' in health[-1], health
    heard = [int(l.split('heard ')[1].split(' ms')[0]) for l in health]
    assert heard[-1] > heard[0] + 1000, heard
    assert 'link: retries held: packet' in text
    assert 'kernel: ' in text and 'at the lost link' in text


def test_the_link_s_health_comes_at_its_default_pace(probe, tmp_path):
    """The drive of 2026-11-22 (q2muse-1126): 403 s with the link up and not one `link: health:` line. q2car.sh runs
    q2-iap with --state, whose audio check kept a file-scope health_at that was the link's too (two tentative
    definitions in one translation unit), set every second. With --state and no --health-ms, a line every 5 s."""
    p, car, fd, log, files = bmw_up(probe, tmp_path, '--state', str(tmp_path/'state'))
    try:
        end = time.time() + 11.5
        while time.time() < end:
            try:
                car.packet_any(0.3)
            except AssertionError:
                pass
    finally:
        stop(p, fd)
    health = [l for l in log.read_text().splitlines() if 'link: health: ' in l]
    assert len(health) >= 2, health


def test_a_lost_link_without_state_says_it_goes_on(probe, tmp_path):
    """Whether the lost-link path ends q2-iap is said either way: here, with no --state, it goes on."""
    p, car, fd, log, files = bmw_up(probe, tmp_path)
    try:
        car.hold = True
        car.send(message(0xDA00))
        time.sleep(3.5)
        assert p.poll() is None
    finally:
        stop(p, fd)
    text = log.read_text()
    assert 'link: lost (a packet went unacknowledged' in text and 'no --state, so q2-iap goes on' in text


def test_a_song_s_own_picture_is_the_car_s_cover(probe, tmp_path):
    """The drive of 2026-11-22 (q2muse-1126): after the first song every cover went empty ("0 as found"), though each
    streamed song carried its own picture (demo's ffmpeg probe: "stream 1 (Video: mjpeg)") and the Q2 showed it. With
    no other cover, ffmpeg fits the song's picture into 160 px in a child, and once it is there Now Playing goes again
    with it, a baseline JPEG the car takes."""
    import shutil
    ffmpeg = shutil.which('ffmpeg')
    cover, song = tmp_path/'pic.jpg', tmp_path/'05 Buzzcut Season [RjYzpcigs0gxaJeph5u83x].flac'
    subprocess.run([ffmpeg, '-v', 'error', '-f', 'lavfi', '-i', 'testsrc=size=600x600', '-frames:v', '1', '-q:v', '2',
                    str(cover)], check=True)
    subprocess.run([ffmpeg, '-v', 'error', '-f', 'lavfi', '-i', 'sine=d=1', '-i', str(cover), '-map', '0', '-map', '1',
                    '-c:v', 'copy', '-disposition:v', 'attached_pic', str(song)], check=True)
    (tmp_path/'run').mkdir()
    p, car, fd, log, files = bmw_up(probe, tmp_path/'run', '--art-made', str(tmp_path/'made'), '--ffmpeg', ffmpeg)
    try:
        put_whole(files['np'], f'state=1\npos=1000\nlen=208\ntitle=Buzzcut Season\nartist=Lorde\nalbum=Pure Heroine\n'
                               f'path={song}\nshuffle=0\nrepeat=0\n')
        media, _ = np_updates(car)
        updates, setups = np_and_files(car, lambda u: False, seconds=4.0)
        # the album's next song: its cover made already (keyed by the album), in its first update, no ffmpeg
        put_whole(files['np'], f'state=1\npos=1000\nlen=208\ntitle=Ribs\nartist=Lorde\nalbum=Pure Heroine\n'
                               f'path={tmp_path}/ribs.flac\nshuffle=0\nrepeat=0\n')
        _, next_setups = np_and_files(car, lambda u: False, seconds=2.0)
    finally:
        stop(p, fd)
    text = log.read_text()
    assert next_setups and min(next_setups.values()) > 1000, next_setups
    assert text.count('asked of ffmpeg') == 1, text.count('asked of ffmpeg')
    assert media.get(0x1a) and "art: the song's own picture made" in text, text[-600:]
    sizes = sorted(setups.values())
    assert sizes[0] == 0 and sizes[-1] > 1000, setups  # empty at first, then the made cover
    made = [l for l in text.splitlines() if 'art: transfer' in l][-1]
    assert int(made.split(', ')[1].split(' bytes')[0]) > 1000, made


def test_a_server_song_s_cover_from_its_app(probe, tmp_path):
    """The drive of 2026-11-21 (q2muse-1125): The Boys Of Summer, a server song played from the Subsonic app's stream
    cache (/mnt/mmc/.subsonic/stream/...), showed its cover on the Q2 (its app's covers/<album id>.jpg,
    catalogue.c catalogue_cover) and none on iDrive, "art: transfer 83, 0 bytes (0 as found)": Cover Flow had no copy
    of its album and nothing lay beside the file. The song's library id (its server row, from 0x40000000) names its
    row's path in the catalogue, whose album id names the app's cover."""
    card = tmp_path/'card'
    cover = card/'mnt/mmc/.subsonic/covers/al-77.jpg'
    cover.parent.mkdir(parents=True)
    jpeg = subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'testsrc=size=160x160', '-frames:v', '1', '-q:v', '2',
                           '-f', 'mjpeg', '-'], check=True, capture_output=True).stdout
    cover.write_bytes(jpeg)
    rid = 0x4000139a
    catalogue_db(tmp_path/'catalogue.db', [(rid, 'The Boys Of Summer', 'The Very Best Of', 'Don Henley', '', 'Rock', 5, 1,
                                            '/mnt/mmc/.subsonic/cat/al-77/05.flac')])
    (tmp_path/'run').mkdir()
    p, car, fd, log, files = bmw_up(probe, tmp_path/'run', '--lib-cat', str(tmp_path/'catalogue.db'),
                                    '--card-root', str(card))
    try:
        put_whole(files['np'], f'state=1\npos=1000\nlen=288\nid={rid}\ntitle=The Boys Of Summer\nartist=Don Henley\n'
                               'album=The Very Best Of\npath=/mnt/mmc/.subsonic/stream/11c3e037/original/5/the boys of '
                               'summer [6wXZ8JEGfmYO7FYDnHDAUi].flac\nshuffle=0\nrepeat=0\n')
        media, _ = np_updates(car)
        updates, setups = np_and_files(car, lambda u: False, seconds=1.0)
    finally:
        stop(p, fd)
    assert media.get(0x1a), 'a transfer named'
    assert setups and max(setups.values()) > len(jpeg) - 64, setups
    assert f'({len(jpeg)} as found)' in log.read_text()



def test_a_server_song_s_cover_asked_of_its_app(probe, tmp_path):
    """The drive of 2026-11-22: a server song whose album the app had no cover of yet went to the car 13.4 s late,
    ffmpeg's picture from the streamed file the only source. q2-iap now asks the app first, in the contract coda-rc's
    covers use with a file and key of its own: q2-<app>.coverask-iap holds "<seq> 160 <album>", the app saves the cover
    where it saves .coverask's and answers coverack-iap=<seq> 0 in its state file. Here the test is the app, answering
    300 ms after the ask; the cover goes to the car and ffmpeg is never run."""
    card, asks = tmp_path/'card', tmp_path/'asks'
    asks.mkdir()
    app = card/'mnt/mmc/.subsonic'
    app.mkdir(parents=True)
    (app/'q2app').write_text('name=Subsonic\ncatalogue=1\ncovers=1\n')
    jpeg = subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'testsrc=size=160x160', '-frames:v', '1', '-q:v', '2',
                           '-f', 'mjpeg', '-'], check=True, capture_output=True).stdout
    rid = 0x4000139a
    catalogue_db(tmp_path/'catalogue.db', [(rid, 'The Boys Of Summer', 'The Very Best Of', 'Don Henley', '', 'Rock', 5, 1,
                                            '/mnt/mmc/.subsonic/cat/al-77/05.flac')])
    never = tmp_path/'ffmpeg-ran'
    fake_ffmpeg = tmp_path/'ffmpeg'
    fake_ffmpeg.write_text(f'#!/bin/sh\ntouch {never}\nexit 1\n')
    fake_ffmpeg.chmod(0o755)
    (tmp_path/'run').mkdir()
    p, car, fd, log, files = bmw_up(probe, tmp_path/'run', '--lib-cat', str(tmp_path/'catalogue.db'),
                                    '--card-root', str(card), '--ask-dir', str(asks), '--ffmpeg', str(fake_ffmpeg),
                                    '--art-made', str(tmp_path/'made'))
    try:
        put_whole(files['np'], f'state=1\npos=1000\nlen=288\nid={rid}\ntitle=The Boys Of Summer\nartist=Don Henley\n'
                               'album=The Very Best Of\npath=/mnt/mmc/.subsonic/stream/11c3e037/original/5/the boys of '
                               'summer [6wXZ8JEGfmYO7FYDnHDAUi].flac\nshuffle=0\nrepeat=0\n')
        attrs = b''.join(prm(a) for a in (0, 1, 4, 6, 12, 0x1a))
        car.send(message(0x5000, prm(0, attrs), prm(1, prm(0) + prm(1))))
        ask = asks/'q2-subsonic.coverask-iap'
        end = time.time() + 3
        while not ask.exists() and time.time() < end:
            time.sleep(0.02)
        line = ask.read_text()
        seq, px, album = line.split()
        assert (px, album) == ('160', 'al-77') and line.endswith('\n')
        assert (asks/'coda-rc.wake').read_text() == 'subsonic\n', 'the app not running: named to be started'
        time.sleep(0.3)
        (app/'covers').mkdir()
        (app/'covers/al-77.jpg').write_bytes(jpeg)
        put_whole(asks/'q2-subsonic.state', f'server=up\ncoverack-iap={seq} 0\n')
        updates, setups = np_and_files(car, lambda u: False, seconds=2.0)
    finally:
        stop(p, fd)
    text = log.read_text()
    assert setups and max(setups.values()) > len(jpeg) - 64, setups
    assert f'art: asked subsonic for al-77 (ask {seq}): saved' in text
    assert not ask.exists(), 'the ask goes once answered'
    assert not never.exists(), 'ffmpeg run though the app had the cover'


PACE_MS =int(os.environ.get('Q2IAP_PACE_MS', '2'))


def fast_ffs(init):
    """FakeFfs with the car's SET_REPORT data 5 ms after its request: the BMW's acknowledgements come within 22 ms"""
    def wrapped(self, *a, **kw):
        init(self, *a, **kw)
        self.data_delay = 0.005
    return wrapped  # the wire's report pace for the long push; the BMW's is 16


@pytest.mark.timeout(900)
def test_the_whole_library_goes_at_the_wire_s_pace_and_resumes(probe, tmp_path, monkeypatch):
    """The user's 9,199 songs (2026-11-21): the push must finish at the wire's pace, partial updates naming where it
    reached; a cable out mid-push resumes there at the next plug, no reset, no song twice; and the car's own packets are
    acknowledged throughout. At --report-ms 2 (Q2IAP_PACE_MS) the time is an eighth of the BMW's (16 ms a report)."""
    monkeypatch.setattr(BmwCar, 'msg', bmw_msg_keeping_body)
    songs = [(i, f'Song number {i}', f'Album {i // 12}', f'Artist {i // 60}', '', 'Rock', i % 12 + 1, 1,
              f'/mnt/mmc/m/{i}.flac') for i in range(1, 9200)]
    song_db(tmp_path/'database.db', songs)
    monkeypatch.setattr(FakeFfs, '__init__', fast_ffs(FakeFfs.__init__))
    p, car, fd, log, files, db, sent = lib_car(probe, tmp_path, '--report-ms', str(PACE_MS))
    first, partial = [], None
    try:
        car.send(message(0x4C03, prm(0, b'lib\0')))
        while len(first) < 1500:  # a short plug: cut off part way
            mid, _ = car.msg(5.0)
            ps = plist(car.last)
            if mid == 0x4C04 and any(i == 2 for i, _ in ps):
                first += [int.from_bytes(params(d)[0], 'big') & 0xffffffff for i, d in ps if i == 2]
                partial = dict(ps)[1].rstrip(b'\0').decode()
    finally:
        stop(p, fd)
    assert partial.startswith('q2p.q2.9199.') and int(partial.rsplit('.', 1)[1]) == len(first)
    p, car, fd, log, files, db, sent = lib_car(probe, tmp_path, '--report-ms', str(PACE_MS), run='run2')
    try:
        t = time.time()
        items, gone, rev, reset = library(car, partial, timeout=10.0)
        took = time.time() - t
    finally:
        stop(p, fd)
    rest = [int.from_bytes(i[0], 'big') & 0xffffffff for i in items]
    assert not reset and first + rest == [s[0] for s in songs], (len(first), len(rest))
    assert rev.startswith('q2.9199.')
    # the time on the BMW's wire: every input report the push took, at 16 ms each (two 64-byte transactions of its
    # 8 ms interrupt endpoint); this harness's own acknowledgements, not the wire, set the time here
    text = log.read_text()
    sizes = {1: 5, 2: 9, 3: 13, 4: 17, 5: 25, 6: 49, 7: 95, 8: 193, 9: 257, 10: 385, 11: 513, 12: 767}
    wire = sum(-(-(sizes[int(l.split(' report > ')[1][:2], 16)] + 1) // 64) * 0.008
               for l in text.splitlines() if ' report > ' in l)  # each report's 64-byte transactions, 8 ms apart
    print(f'{len(rest)} songs in {took:.1f} s here; {wire:.0f} s on the BMW\'s wire, '
          f'{wire * len(songs) / len(rest):.0f} s for all {len(songs)}')
    assert wire * len(songs) / len(rest) < 400 and '(again)' not in text, (wire, text.count('(again)'))


def stock_lists(path, favs, recent, playlists):
    """Stock's myfav, recentplay and playlist tables in the database at path, as the user's Q2 has them (2026-11-20:
    40 favourites, 100 recently played, 32 playlist headers with 4,540 songs between 30 of them): favs and recent are
    song ids (recent in the order played, the last last), playlists [(name, song ids)]; a song's path is its
    '/mnt/mmc/m/<id>.flac'."""
    import sqlite3
    db = sqlite3.connect(path)
    for t in ('myfav', 'recentplay', 'playlist'):
        db.execute(SONGTABLE.replace('songtable', t))
    url = lambda i: f'/mnt/mmc/m/{i}.flac'
    db.executemany('insert into myfav (id, fileurl) values (?, ?)', [(k + 1, url(i)) for k, i in enumerate(favs)])
    db.executemany('insert into recentplay (id, fileurl) values (?, ?)', [(k + 1, url(i)) for k, i in enumerate(recent)])
    row = 101
    for h, (name, ids) in enumerate(playlists):
        db.execute("insert into playlist (id, filename, fileurl, name, cd_num) values (?, ?, '', ?, ?)", (h, name, name, h))
        for i in ids:
            db.execute('insert into playlist (id, fileurl, cd_num) values (?, ?, ?)', (row, url(i), h))
            row += 1
    db.commit()
    db.close()


# The user's playlists' sizes (2026-11-20), and two headers with no songs (stock's Audiobooks among them)
PLAYLIST_SIZES = [711, 34, 0, 961, 19, 30, 666, 32, 30, 30, 27, 30, 25, 235, 363, 32, 100, 36, 34, 25, 30, 33, 300, 81, 39,
                  229, 26, 84, 30, 235, 33, 0]


@pytest.mark.timeout(600)
def test_idrive_has_the_lists_first_within_seconds_at_the_bmw_s_pace(probe, tmp_path, monkeypatch):
    """The user's 9,199 songs took six minutes to reach iDrive, which shows a library only once an update completes
    (progress 100: PodBox found its BMW reloads at each one, and reads on while one is held back). At the BMW's pace
    (--report-ms 16) a first update must complete within 15 s of the car's StartMediaLibraryUpdates: the queue's songs
    from the playing one on, the favourites, the recently played and the first of the playlists' songs, and every
    list's record (Favorites, Recently Played, the playlists with songs), each list's songs' ids then following in a
    file transfer of type 4; the whole push goes on after it, and a cable out resumes it with no reset, every song
    sent, none of the push's twice."""
    monkeypatch.setattr(BmwCar, 'msg', bmw_msg_keeping_body)
    monkeypatch.setattr(FakeFfs, '__init__', fast_ffs(FakeFfs.__init__))
    import random
    rnd = random.Random(1120)
    n_songs = 9199
    song_db(tmp_path/'database.db', [(i, f'Song number {i}', f'Album {i // 12}', f'Artist {i // 60}', '', 'Rock',
                                      i % 12 + 1, 1, f'/mnt/mmc/m/{i}.flac') for i in range(1, n_songs + 1)])
    favs, recent = rnd.sample(range(1, n_songs + 1), 40), rnd.sample(range(1, n_songs + 1), 100)
    playlists = [(f'Playlist {k}', [rnd.randrange(1, n_songs + 1) for _ in range(size)])
                 for k, size in enumerate(PLAYLIST_SIZES)]
    stock_lists(tmp_path/'database.db', favs, recent, playlists)
    queue = rnd.sample(range(1, n_songs + 1), 300)
    put_whole(tmp_path/'queue', '7 10 300\n' + ''.join(f'{r}\n' for r in queue))
    p, car, fd, log, files, db, sent = lib_car(probe, tmp_path, '--report-ms', '16')
    put_whole(files['np'], files['np'].read_text() + f'id={queue[10]}\nqgen=7\nqpos=10\nqlen=300\n')
    jpeg = subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'testsrc=size=180x180', '-frames:v', '1',
                           '-q:v', '2', '-f', 'mjpeg', '-'], check=True, capture_output=True).stdout
    (files['art']/f'{fnv_part(fnv_part(2166136261, "Massive Attack"), "Mezzanine"):08x}.jpg').write_bytes(jpeg)
    want = {0x5132000F00000000 | 1001: favs, 0x5132000F00000000 | 1002: recent[::-1]}
    want.update({0x5132000F00000000 | k: ids for k, (_, ids) in enumerate(playlists) if ids})
    first, pushed, records, setups, pieces, done = [], [], {}, {}, {}, {}
    last_rev, t_first, t_lists = None, None, None
    try:
        t0 = time.time()
        car.send(message(0x5000, prm(0, b''.join(prm(a) for a in (0, 1, 4, 6, 12, 0x1a))), prm(1, prm(0) + prm(1))))
        car.send(message(0x4C03, prm(0, b'lib\0')))
        while time.time() - t0 < 60 and (t_lists is None or time.time() - t_lists < 3):
            got = car.packet_any(2.0)
            if not got or not got[4]:
                continue
            session, pl = got[3], got[4]
            if session == 2:
                tid, op = pl[0], pl[1]
                if op == 0x04:
                    setups[tid] = pl[2:]
                    car.send(bytes([tid, 0x01]), session=2)
                else:
                    pieces.setdefault(tid, b'')
                    pieces[tid] += pl[2:]
                    if op in (0x40, 0xC0):
                        done[tid] = True
                continue
            if (pl[4] << 8 | pl[5]) != 0x4C04:
                continue
            ps = plist(pl[6:])
            d = dict(ps)
            songs = [int.from_bytes(params(v)[0], 'big') & 0xffffffff for i, v in ps if i == 2]
            for i, v in ps:
                if i == 3:
                    r = params(v)
                    records[int.from_bytes(r[0], 'big')] = (r[1].rstrip(b'\0').decode(), r[5][0])
            lib_id = d[0]
            if 1 not in d:
                continue  # 8 00 and 9 01
            last_rev = d[1].rstrip(b'\0').decode()
            if t_first is None:
                assert 6 in d or first, 'the first update opens with a reset'
                first += songs
                if d.get(7) == b'\x64':
                    t_first = time.time() - t0
                    first_rev = last_rev
            else:
                pushed += songs
            lists_ids = {tid for _, tid in records.values()}
            if t_first and t_lists is None and lists_ids and all(done.get(t) for t in lists_ids):
                t_lists = time.time() - t0
    finally:
        stop(p, fd)
    text = log.read_text()
    assert t_first is not None and t_first < 15, f'the first update completed after {t_first} s'
    assert first_rev.startswith('q2p.q2.9199.') and first_rev.endswith('.0'), first_rev
    order = []
    for r in queue[10:60] + favs + recent[::-1]:  # the queue on from the playing song, the favourites, the last played
        if r not in order:
            order.append(r)
    assert first == order[:150], (len(first), first[:5], order[:5])
    assert {k: v[0] for k, v in records.items()} == {**{0x5132000F00000000 | 1001: 'Favorites',
                                                        0x5132000F00000000 | 1002: 'Recently Played'},
                                                     **{0x5132000F00000000 | k: n for k, (n, ids) in enumerate(playlists) if ids}}
    assert t_lists is not None, 'every list\'s songs sent'
    for key, (_, tid) in records.items():
        setup = setups[tid]
        assert setup[:4] == bytes(4) and int.from_bytes(setup[4:8], 'big') == 8 * len(want[key])
        assert setup[8:10] == b'\x00\x04' and setup[10:18] == key.to_bytes(8, 'big') and setup[18:] == lib_id
        assert pieces[tid] == b''.join((0x5132000100000000 | r).to_bytes(8, 'big') for r in want[key]), key
    assert pushed and not set(pushed) & set(first), 'the push leaves out the songs the first update had'
    # the same on the BMW's wire: each input report's 64-byte transactions, 8 ms apart (a report of 95 bytes 16 ms)
    sizes = {1: 5, 2: 9, 3: 13, 4: 17, 5: 25, 6: 49, 7: 95, 8: 193, 9: 257, 10: 385, 11: 513, 12: 767}
    upto = text[text.index('< 4c03 StartMediaLibraryUpdates'):text.index('library: the first update complete')]
    wire = sum(-(-(sizes[int(l.split(' report > ')[1][:2], 16)] + 1) // 64) * 0.008
               for l in upto.splitlines() if ' report > ' in l)
    print(f'first update ({wire:.1f} s on the BMW\'s wire): '
          f'{len(first)} songs and {len(records)} lists complete in {t_first:.1f} s at 16 ms a report; '
          f'every list\'s songs in {t_lists:.1f} s; then {len(pushed)} songs of the push before the cable came out '
          f'({last_rev})')
    assert 'link: lost' not in text and '(again)' not in text
    names = next(l for l in text.splitlines() if 'library: its lists:' in l)  # every name sent, in one line
    assert all(f'"{n}"' in names for n, ids in playlists if ids) and '"Favorites"' in names, names
    # the next plug: the push resumes where the car's revision says, with the lists first again
    p, car, fd, log, files, db, sent = lib_car(probe, tmp_path, '--report-ms', str(PACE_MS), run='run2')
    try:
        items, gone, rev, reset = library(car, last_rev, timeout=10.0)
    finally:
        stop(p, fd)
    rest = [int.from_bytes(i[0], 'big') & 0xffffffff for i in items]
    every = first + pushed + rest
    assert not reset and rev.startswith('q2.9199.') and set(every) == set(range(1, n_songs + 1))
    assert len(pushed + rest) == len(set(pushed + rest)), 'no song of the push twice'
    assert len(every) - n_songs <= 150, 'at most the first update\'s songs again'


def test_playing_from_idrive(probe, tmp_path, monkeypatch):
    """Songs picked on iDrive (4C07, their ids and where to start), an album (4C08, type 2: in disc and track order),
    every song shuffled (4C09) and the current selection (4C06) as the shim's list and play command."""
    monkeypatch.setattr(BmwCar, 'msg', bmw_msg_keeping_body)
    p, car, fd, log, files, db, sent = lib_car(probe, tmp_path)
    try:
        items, _, _, _ = library(car)
        keys = b''.join((0x5132000100000000 | r).to_bytes(8, 'big') for r in (9, 3))
        car.send(message(0x4C07, prm(0, keys), prm(1, (1).to_bytes(4, 'big'))))
        time.sleep(0.2)
        assert (tmp_path/'list').read_text() == '9\n3\n' and files['cmd'].read_text().split(' ', 1)[1] == 'play 1 0 2\n'
        album = items[0][5]
        car.send(message(0x4C08, prm(0, album), prm(1, b'\x02'), prm(2, (0).to_bytes(4, 'big'))))
        time.sleep(0.2)
        assert (tmp_path/'list').read_text() == '5\n7\n3\n' and files['cmd'].read_text().split(' ', 1)[1] == 'play 0 0 3\n'
        car.send(message(0x4C09, prm(0, b'lib\0'), prm(1)))
        time.sleep(0.2)
        assert (tmp_path/'list').read_text() == '3\n5\n7\n9\n' and files['cmd'].read_text().split(' ', 1)[1] == 'play 0 1 4\n'
        car.send(message(0x4C06, prm(0, b'lib\0')))
        time.sleep(0.2)
        assert files['cmd'].read_text().split(' ', 1)[1] == 'resume\n'
    finally:
        stop(p, fd)


def file_packet(car):
    """The next packet; one on the file transfer session kept in car.files."""
    got = car.packet_any()
    if got and got[4] and got[3] == 2:
        car.files.append(got[4])


def test_up_next_and_the_playing_song_s_id(probe, tmp_path, monkeypatch):
    """Now Playing names the song by its library id; when the queue changes (qgen) and holds still a second, PB_QUEUE_LIST
    1 with an empty PB_QUEUE_LIST_ID, then a file transfer of type 3 with the queue's ids in order; a queue with a song
    outside the library is PB_QUEUE_LIST 0 and no list."""
    monkeypatch.setattr(BmwCar, 'msg', bmw_msg_keeping_body)
    p, car, fd, log, files, db, sent = lib_car(probe, tmp_path)
    put_whole(tmp_path/'queue', '4 1 3\n5\n3\n9\n')
    put_whole(files['np'], files['np'].read_text() + 'id=3\nqgen=4\nqpos=1\nqlen=3\n')
    try:
        car.send(message(0x5000, prm(0, prm(0) + prm(1)), prm(1, prm(0) + prm(2) + prm(3) + prm(14) + prm(17))))
        mid, ps = car.msg()
        assert mid == 0x5001 and params(ps[0])[0] == (0x5132000100000003).to_bytes(8, 'big')
        deadline, announce = time.time() + 4, None
        while time.time() < deadline and announce is None:
            mid, ps = car.msg()
            if mid == 0x5001 and 1 in ps and 14 in params(ps[1]):
                announce = params(ps[1])
        assert announce and announce[14] == b'\x01' and announce[17] == b''
        while not car.files:
            file_packet(car)
        tid, op, setup = car.files[0][0], car.files[0][1], car.files[0][2:]
        assert op == 0x04 and setup == bytes(4) + (24).to_bytes(4, 'big') + b'\x00\x03'
        car.send(bytes([tid, 0x01]), session=2)
        while len(car.files) < 2:
            file_packet(car)
        assert car.files[1][1] == 0xC0 and car.files[1][2:] == b''.join((0x5132000100000000 | r).to_bytes(8, 'big') for r in (5, 3, 9))
        put_whole(tmp_path/'queue', '5 0 2\n5\n0\t/mnt/mmc/.app/x.flac\tX\tY\n')
        put_whole(files['np'], files['np'].read_text().replace('qgen=4', 'qgen=5'))
        deadline, announce = time.time() + 4, None
        while time.time() < deadline and announce is None:
            mid, ps = car.msg()
            if mid == 0x5001 and 1 in ps and 14 in params(ps[1]):
                announce = params(ps[1])
        assert announce and announce[14] == b'\x00' and 17 not in announce
    finally:
        stop(p, fd)


def test_up_next_of_server_songs_from_the_stream_cache(probe, tmp_path, monkeypatch):
    """The drive of 2026-11-22 (q2muse-1126): every queue was the Subsonic app's songs played from its stream cache,
    which the shim names by rid 0 (the device's /tmp/coda-rc.queue: `1 0 3` then three lines of 0, the stream path,
    title and artist; /tmp/coda-rc.now id=0), so every announce was "no list" and Now Playing named the song by a
    hash, though the library the car was sent has each as its server row. The stream path's app and sid name that row
    (servermeta), whose rid goes in the list and in Now Playing's id."""
    monkeypatch.setattr(BmwCar, 'msg', bmw_msg_keeping_body)
    import sqlite3
    song_db(tmp_path/'database.db', [SONGS[1]])
    server = [(0x40000000 + k, t, 'Pure Heroine', 'Lorde', '', 'Pop', k + 1, 1, f'/mnt/mmc/.subsonic/cat/al-9/{k}.flac')
              for k, t in enumerate(('Ribs', 'Buzzcut Season', 'Team'))]
    catalogue_db(tmp_path/'catalogue.db', server)
    c = sqlite3.connect(tmp_path/'catalogue.db')
    for k, sid in enumerate(('0S2sKobzoSngoRjq2oIqxT', 'RjYzpcigs0gxaJeph5u83x', 'Tm4xQ2pV')):
        c.execute('update servermeta set sid = ? where id = ?', (sid, 0x40000000 + k))
    c.commit()
    c.close()
    stream = '/mnt/mmc/.subsonic/stream/11c3e037/original/{}/{} [{}].flac'
    paths = [stream.format('f', 'ribs', '0S2sKobzoSngoRjq2oIqxT'), stream.format('5', 'buzzcut season', 'RjYzpcigs0gxaJeph5u83x'),
             stream.format('a', 'team', 'Tm4xQ2pV')]
    p, car, fd, log, files, db, sent = lib_car(probe, tmp_path)
    put_whole(tmp_path/'queue', '1 0 3\n' + ''.join(f'0\t{pa}\t{t}\tLorde\n' for pa, t in zip(paths, ('Ribs', 'Buzzcut Season', 'Team'))))
    put_whole(files['np'], f'state=1\npos=1000\nlen=208\nid=0\ntitle=Ribs\nartist=Lorde\nalbum=Pure Heroine\npath={paths[0]}\n'
                           'qgen=1\nqpos=0\nqlen=3\n')
    try:
        car.send(message(0x5000, prm(0, prm(0) + prm(1)), prm(1, prm(0) + prm(2) + prm(3) + prm(14) + prm(17))))
        mid, ps = car.msg()
        assert mid == 0x5001 and params(ps[0])[0] == (0x5132000140000000).to_bytes(8, 'big'), params(ps[0])[0].hex()
        deadline, announce = time.time() + 4, None
        while time.time() < deadline and announce is None:
            mid, ps = car.msg()
            if mid == 0x5001 and 1 in ps and 14 in params(ps[1]):
                announce = params(ps[1])
        assert announce and announce[14] == b'\x01', announce
        while not car.files:
            file_packet(car)
        tid = car.files[0][0]
        car.send(bytes([tid, 0x01]), session=2)
        while len(car.files) < 2:
            file_packet(car)
        assert car.files[1][2:] == b''.join((0x5132000100000000 | (0x40000000 + k)).to_bytes(8, 'big') for k in range(3))
    finally:
        stop(p, fd)


def big_catalogue(path, n=9199):
    """The owner's server library's size (q2car.1.log of 2026-11-22: 9,199 songs), each with its own sid: the stream
    path of song k is stream_of(k)."""
    import sqlite3
    path.unlink(missing_ok=True)
    db = sqlite3.connect(path)
    db.execute(SERVERTABLE)
    db.execute(SERVERMETA)
    for k in range(n):
        rid, url = 0x40000000 + k, f'/mnt/mmc/.subsonic/cat/al-{k // 12}/{k}.flac'
        db.execute('insert into servertable values (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                   (rid, f'{k}.flac', url, f'Album {k // 12}', f'Artist {k // 120}', '', 'Pop', f'Song {k}', '', 2001,
                    k % 12 + 1, 1, 0, 0, 0, 0, 16, 44100))
        db.execute('insert into servermeta (id, fileurl, app, sid, seconds) values (?,?,?,?,?)',
                   (rid, url, 'subsonic', f'sid{k:06d}x', 200))
    db.commit()
    db.close()


def stream_of(k):
    return f'/mnt/mmc/.subsonic/stream/11c3e037/original/{k % 16:x}/song {k} [sid{k:06d}x].flac'


def held(log, step):
    """The ms of each pass the stall detector logged as held mostly in step."""
    return [int(l.split('held ')[1].split(' ms')[0]) for l in log.read_text().splitlines()
            if 'loop: a pass held' in l and f'in {step};' in l]


def test_a_song_change_does_not_read_the_catalogue_in_the_loop(probe, tmp_path, monkeypatch):
    """q2car.1.log of 2026-11-22: 16 of the drive's 21 stalls, 102 to 664 ms, were a server song's change of Now Playing
    or of the queue, each reading all of servermeta (9,199 rows) from the card inside the loop to find one song's rid.
    The loop must find a stream-cache song's rid without that read. This machine reads the 9,199 rows in 2 to 5 ms
    (the Q2, from the card, 100 times that), so the stall detector is set at 2 ms: over ten song and queue changes
    at most one pass (a scheduler's hiccup) may be held that long in either step, and each song is still named by
    its rid."""
    monkeypatch.setattr(BmwCar, 'msg', bmw_msg_keeping_body)
    song_db(tmp_path/'database.db', [SONGS[1]])
    big_catalogue(tmp_path/'catalogue.db')
    p, car, fd, log, files, db, sent = lib_car(probe, tmp_path, '--stall-ms', '2')
    got = []
    try:
        car.send(message(0x5000, prm(0, prm(0) + prm(1)), prm(1, prm(0) + prm(2) + prm(3) + prm(14) + prm(17))))
        time.sleep(1.5)  # the index's making, at the cable's start, before any song needs it
        for i, k in enumerate(range(100, 9100, 900)):
            put_whole(tmp_path/'queue', f'{i + 1} 0 2\n0\t{stream_of(k)}\tSong {k}\tA\n'
                                          f'0\t{stream_of(k + 1)}\tSong {k + 1}\tA\n')
            put_whole(files['np'], f'state=1\npos=1000\nlen=200\nid=0\ntitle=Song {k}\nartist=A\nalbum=B\n'
                                   f'path={stream_of(k)}\nqgen={i + 1}\nqpos=0\nqlen=2\n')
            deadline, seen = time.time() + 3, None
            while time.time() < deadline and seen != 0x40000000 + k:
                mid, ps = car.msg()
                if mid == 0x5001 and 0 in ps and 0 in params(ps[0]):
                    seen = int.from_bytes(params(ps[0])[0], 'big') & 0xffffffff
            got.append(seen)
    finally:
        stop(p, fd)
    assert got == [0x40000000 + k for k in range(100, 9100, 900)], [hex(g) for g in got]
    slow = held(log, 'the Now Playing file') + held(log, "the shim's queue file")
    assert len(slow) <= 1, sorted(slow)


Q2CAR = pathlib.Path(__file__).resolve().parents[1]/'patch/q2car.sh'
Q2CAR_CAR = '12.5 car host: car (Apple\'s C0 53), 604 ms after it addressed the Q2\n'
Q2CAR_COMPUTER = '12.5 car host: computer (usbmux\'s C0 45), 13 ms after it addressed the Q2\n'


# patch/q2car.sh's rotate (docs/iphone-mode.md#car-audio-in-the-image): a car's cable log kept as q2car.1.log, the older
# ones moved up and the oldest going past CARLOG_MAX bytes or CARLOG_DAYS days; a computer's or a charger's never
# pushes a car's out. The drive of 2026-11-22 lost three car cables' logs to the next car, a second drive and the
# computer plugged in to read them, when the script kept three cables of any kind.
@pytest.fixture
def carlogs(tmp_path):
    (tmp_path / 'run').mkdir()
    (tmp_path / 'logs').mkdir()
    return tmp_path


def q2car_rotate(where, **extra):
    env = dict(os.environ, Q2CAR_RUN=str(where / 'run'), Q2CAR_LOGDIR=str(where / 'logs'), **extra)
    subprocess.run(['sh', str(Q2CAR), '_rotate'], env=env, check=True, timeout=30)


def car_cable(where, text, dmesg=None, **extra):
    """A cable's log as q2car.sh leaves it, then the next cable's start."""
    (where / 'logs/q2car.log').write_text(text)
    if dmesg is not None:
        (where / 'logs/q2car.log.dmesg').write_text(dmesg)
    q2car_rotate(where, **extra)


def carlog_names(where):
    return sorted(p.name for p in (where / 'logs').iterdir())


def test_cars_kept_and_others_never_push_them_out(carlogs):
    for i in range(4):
        car_cable(carlogs, Q2CAR_CAR + f'car {i}\n', dmesg=f'dmesg {i}\n')
    car_cable(carlogs, Q2CAR_COMPUTER)
    car_cable(carlogs, '0.1 sh start: kernel 5.10.21+\n')  # a charger: no host spoke
    assert carlog_names(carlogs) == ['q2car.1.log', 'q2car.1.log.dmesg', 'q2car.2.log', 'q2car.2.log.dmesg', 'q2car.3.log',
                           'q2car.3.log.dmesg', 'q2car.4.log', 'q2car.4.log.dmesg', 'q2car.other.log']
    assert (carlogs / 'logs/q2car.1.log').read_text().endswith('car 3\n')
    assert (carlogs / 'logs/q2car.4.log').read_text().endswith('car 0\n')
    assert (carlogs / 'logs/q2car.4.log.dmesg').read_text() == 'dmesg 0\n'
    assert 'sh start' in (carlogs / 'logs/q2car.other.log').read_text()


def test_the_oldest_go_past_the_size(carlogs):
    big = Q2CAR_CAR + 'x' * (9 << 20)
    for i in range(6):
        car_cable(carlogs, big + f'\ncar {i}\n')
    # each just over 9 MB: the fifth passes 40 MB, so it and the sixth go
    assert carlog_names(carlogs) == ['q2car.1.log', 'q2car.2.log', 'q2car.3.log', 'q2car.4.log']
    assert (carlogs / 'logs/q2car.1.log').read_text().endswith('car 5\n')
    assert (carlogs / 'logs/q2car.4.log').read_text().endswith('car 2\n')


def test_a_long_drive_s_log_survives_the_next_car(carlogs):
    """q2-iap lets one cable's log reach 12 MB (--log-cap): two such drives, then a third cable, and the drive before
    it is still there (with a 10 MB budget it went at once, and the evidence of the drive before with it)."""
    big = Q2CAR_CAR + 'x' * (12 << 20)
    car_cable(carlogs, big + '\ndrive 1\n')
    car_cable(carlogs, big + '\ndrive 2\n')
    car_cable(carlogs, Q2CAR_CAR + 'drive 3\n')
    assert carlog_names(carlogs) == ['q2car.1.log', 'q2car.2.log', 'q2car.3.log']
    assert (carlogs / 'logs/q2car.2.log').read_text().endswith('drive 2\n')
    assert (carlogs / 'logs/q2car.3.log').read_text().endswith('drive 1\n')


def test_the_newest_two_cars_stay_whatever_their_size(carlogs):
    """Past the budget, the older ones go but never the last two."""
    small = {'Q2CAR_CARLOG_MAX': str(1 << 20)}
    for i in range(3):
        car_cable(carlogs, Q2CAR_CAR + 'x' * (2 << 20) + f'\ncar {i}\n', **small)
    assert carlog_names(carlogs) == ['q2car.1.log', 'q2car.2.log']
    assert (carlogs / 'logs/q2car.2.log').read_text().endswith('car 1\n')


def test_the_oldest_go_past_the_days_but_the_last_car_stays(carlogs):
    car_cable(carlogs, Q2CAR_CAR + 'old\n')
    week = time.time() - 9 * 86400
    os.utime(carlogs / 'logs/q2car.1.log', (week, week))
    car_cable(carlogs, Q2CAR_CAR + 'new\n')
    assert carlog_names(carlogs) == ['q2car.1.log']
    assert (carlogs / 'logs/q2car.1.log').read_text().endswith('new\n')
    os.utime(carlogs / 'logs/q2car.1.log', (week, week))
    car_cable(carlogs, Q2CAR_COMPUTER)
    assert carlog_names(carlogs) == ['q2car.1.log', 'q2car.other.log']  # the only car's log, however old



# patch/q2car.sh's release, before gadget_down unbinds the function: the player told by SIGUSR2 to let go of the car's
# PCM, 2 s at most. On 2026-11-22 the unbind sat 6.4 s in snd_card_free while the PCM was held open.
def q2car_release(where, status, player=None):
    pcm = where / 'status'
    pcm.write_text(status)
    shim = where / 'bin'
    shim.mkdir(exist_ok=True)
    (shim / 'usleep').write_text('#!/bin/sh\nsleep $(awk "BEGIN { print $1 / 1000000 }")\n')  # busybox's, not the host's
    (shim / 'usleep').chmod(0o755)
    env = dict(os.environ, Q2CAR_RUN=str(where / 'run'), Q2CAR_LOGDIR=str(where / 'logs'), Q2CAR_PCM=str(pcm),
               Q2CAR_PLAYER=player or 'q2-no-such-player', PATH=f'{shim}:{os.environ["PATH"]}')
    start = time.monotonic()
    subprocess.run(['sh', str(Q2CAR), '_release'], env=env, check=True, timeout=30)
    return time.monotonic() - start, (where / 'logs/q2car.log').read_text() if (where / 'logs/q2car.log').exists() else ''


def test_a_closed_car_pcm_is_not_waited_for(carlogs):
    took, log = q2car_release(carlogs, 'closed\n')
    assert took < 1 and log == ''


def test_the_player_lets_go_of_the_car_pcm_before_the_unbind(carlogs):
    """A stand-in player that closes the PCM at SIGUSR2, as q2player does (docs/player.md)."""
    src = carlogs / 'player.c'
    src.write_text('#include <signal.h>\n#include <stdio.h>\n#include <unistd.h>\nstatic const char *pcm;\n'
                   'static void let_go(int s) { FILE *f = fopen(pcm, "w"); fputs("closed\\n", f); fclose(f); _exit(0); }\n'
                   'int main(int argc, char **argv) { pcm = argv[1]; signal(SIGUSR2, let_go); for (;;) pause(); }\n')
    subprocess.run([os.environ.get('CC', 'cc'), '-o', str(carlogs / 'q2fakeplayer'), str(src)], check=True, timeout=60)
    p = subprocess.Popen([str(carlogs / 'q2fakeplayer'), str(carlogs / 'status')])
    try:
        time.sleep(0.3)
        took, log = q2car_release(carlogs, 'state: PAUSED\n', player='q2fakeplayer')
    finally:
        p.kill()
        p.wait()
    assert took < 1.5 and "the car's PCM released in" in log, (took, log)


def test_a_car_pcm_never_let_go_is_given_2_s(carlogs):
    took, log = q2car_release(carlogs, 'state: RUNNING\n')
    assert 1.9 < took < 4 and "still open after 2 s" in log, (took, log)


def test_the_library_s_read_does_not_hold_the_loop(probe, tmp_path, monkeypatch):
    """q2car.1.log of 2026-11-22 at 22028.654: "a pass held 648 ms, 557 of it in the library's read", the car's
    StartMediaLibraryUpdates answered by reading all 9,199 of the owner's server songs and 31 lists inside the loop,
    once a cable. This machine reads them about 100 times faster than the Q2's card, so the stall detector is set at
    2 ms: at most one pass (a scheduler's hiccup, as in test_a_song_change_does_not_read_the_catalogue_in_the_loop)
    may be held mostly in the library's read, and the whole library still goes to the car. What the loop does there
    is bounded, whatever the library's size: the children sort, and a pass takes at most HANDOVER_MAX (64 KB) of their
    bytes (measured here, the worst pass: 28 to 311 us at 1,000, 9,199 and 20,000 songs, where installing the
    stream-path index's unsorted rows took 0.1, 0.6 to 0.9 and 1.9 to 5.2 ms)."""
    monkeypatch.setattr(BmwCar, 'msg', bmw_msg_keeping_body)
    monkeypatch.setattr(FakeFfs, '__init__', fast_ffs(FakeFfs.__init__))  # the data stage's 50 ms would hide it
    song_db(tmp_path/'database.db', [SONGS[1]])
    big_catalogue(tmp_path/'catalogue.db')
    p, car, fd, log, files, db, sent = lib_car(probe, tmp_path, '--stall-ms', '2')
    try:
        car.send(message(0x4C03, prm(0, b'lib\0')))
        deadline, first = time.time() + 10, None
        while time.time() < deadline and first is None:
            mid, ps = car.msg()
            if mid == 0x4C04 and 2 in ps:
                first = ps
    finally:
        stop(p, fd)
    assert first is not None, 'no song sent'
    text = log.read_text()
    assert '9200 songs read' in text
    assert len(held(log, "the library's read")) <= 1, held(log, "the library's read")



def test_a_session_s_end_takes_the_car_s_audio_and_the_link(probe, tmp_path):
    """uac1-power-app.md #19: a DISABLE or SUSPEND past the gap ended q2-iap's session (its summary written) but left
    the car's file, so the player kept writing to a way 3 nobody read, and the link, so a bus RESUME went on with
    active 0. The session's end now removes the car's file and forgets the link; the next ENABLE is session 2."""
    p, car, fd, log, files = bmw_up(probe, tmp_path, '--gap-ms', '300')
    try:
        car.send(message(0xDA00))
        assert car.msg()[0] == 0xDA01
        time.sleep(0.1)
        assert files['car'].exists()
        car.ffs.event(DISABLE)
        time.sleep(0.8)
        assert not files['car'].exists(), "the car's file outlived its session"
        car.ffs.event(ENABLE)
        time.sleep(0.2)
    finally:
        stop(p, fd)
    text = log.read_text()
    assert 'session 1 ended' in text and 'session 2 begins' in text
    assert 'iap2 link: forgotten with its session' in text


def test_a_sleep_ends_the_session_and_the_link(probe, tmp_path):
    """uac1-power-app.md #6: the session gap and the link's timers ran on CLOCK_MONOTONIC, which stops while the Q2 is
    suspended, so hours asleep after a DISABLE read as no time and the old session went on at the wake. They run on
    CLOCK_BOOTTIME now. --clock-file stands in for a sleep: the milliseconds it holds are added to the boot clock and
    not to the monotonic one. A sleep past the gap after a DISABLE ends the session; a sleep with the link up is a
    lost link."""
    clock = tmp_path/'clock'
    put_whole(clock, '0\n')
    p, car, fd, log, files = bmw_up(probe, tmp_path, '--clock-file', str(clock))
    try:
        car.send(message(0xDA00))
        assert car.msg()[0] == 0xDA01
        time.sleep(0.1)
        car.ffs.event(DISABLE)
        time.sleep(0.2)
        put_whole(clock, '20000\n')  # 20 s asleep, past the 8 s gap
        assert 'session 1 ended' in log_wait(log, lambda t: 'session 1 ended' in t)
        assert not files['car'].exists()
    finally:
        stop(p, fd)
    (tmp_path/'awake').mkdir()
    p, car, fd, log, files = bmw_up(probe, tmp_path/'awake', '--clock-file', str(clock))
    try:
        time.sleep(0.2)
        put_whole(clock, '60000\n')  # 40 s more, the link up and no DISABLE
        time.sleep(0.4)
    finally:
        stop(p, fd)
    text = log.read_text()
    assert 'link: lost (the Q2 slept 40' in text, [l for l in text.splitlines() if 'link' in l][-5:]
    assert 'session 1 ended' not in text


def test_a_catalogue_that_cannot_be_read_is_not_an_empty_one(probe, tmp_path, monkeypatch):
    """uac1-power-app.md #4: a car asking for the library with the card not mounted yet (a wake, a card lent to a
    computer) was told every server song was gone, and the record then said it held the smaller library, so the next
    drive pushed them all again. With the sent record holding server songs and the catalogue absent, the read waits
    for the card a bounded time (--card-wait-ms); a catalogue there by then is read as usual, and one still absent
    leaves the car with the library it holds: its revision back, complete, nothing gone, the record unchanged."""
    monkeypatch.setattr(BmwCar, 'msg', bmw_msg_keeping_body)
    cat = tmp_path/'catalogue.db'
    song_db(tmp_path/'database.db', [SONGS[1]])
    server = [(0x40000000 + k, t, 'Vide Noir', 'Lord Huron', '', 'Rock', k + 1, 1,
               f'/mnt/mmc/.subsonic/cat/a/{k}.flac') for k, t in enumerate(('Lost in Time and Space', 'Never Ever'))]
    catalogue_db(cat, server)
    p, car, fd, log, files, db, sent = lib_car(probe, tmp_path)
    try:
        items, gone, rev, reset = library(car)
        assert len(items) == 3
        time.sleep(0.2)
    finally:
        stop(p, fd)
    record = sent.read_text()
    cat.rename(tmp_path/'away.db')  # the card not mounted
    p, car, fd, log, files, db, sent = lib_car(probe, tmp_path, '--card-wait-ms', '600', run='run2')
    try:
        items, gone, rev2, reset = library(car, rev)
        time.sleep(0.2)
    finally:
        stop(p, fd)
    assert (items, gone, rev2, reset) == ([], [], rev, False)
    assert sent.read_text() == record
    assert 'the catalogue cannot be read' in log.read_text()
    p, car, fd, log, files, db, sent = lib_car(probe, tmp_path, '--card-wait-ms', '5000', run='run3')
    try:
        car.send(message(0x4C03, prm(0, b'lib\0'), prm(1, rev.encode() + b'\0')))
        time.sleep(0.5)
        (tmp_path/'away.db').rename(cat)  # mounted half a second into the read
        items, gone, rev3, reset = [], [], None, False
        while rev3 is None:
            mid, _ = car.msg(8)
            ps = plist(car.last)
            gone += [int.from_bytes(d, 'big') for i, d in ps if i == 4]
            if dict(ps).get(7) == b'\x64':
                rev3 = dict(ps).get(1, b'').rstrip(b'\0').decode()
    finally:
        stop(p, fd)
    assert (gone, rev3) == ([], rev)
    assert 'waited for the card' in log.read_text()



def wakeups(pid, seconds):
    """The process's voluntary context switches over seconds: each sleep in poll that ended is one."""
    def count():
        for line in pathlib.Path(f'/proc/{pid}/status').read_text().splitlines():
            if line.startswith('voluntary_ctxt_switches:'):
                return int(line.split()[1])
    a = count()
    time.sleep(seconds)
    return count() - a


def test_the_loop_sleeps_until_something_is_due(probe, tmp_path):
    """car-bt-power.md #6: q2-iap's loop woke every 10 ms whatever it was doing, a charger overnight included (the
    gadget stays up, unseen by it), and read the rate file 50 times a second: 6,000 wakeups a minute. It sleeps now
    until a request, a report gone, a child's bytes or the soonest deadline. On a charger (a host that never addresses
    the Q2) that is the controller's state every 100 ms; with a car identified, its link idle and its audio not asked
    for, the rate file every 250 ms. Counted over 2 s here: at most 30 wakeups each (the old loop's were about 200)."""
    p, ffs, log, state, udc = car_daemon(probe, tmp_path, '--iap2', kinds=('hid',))
    try:
        time.sleep(0.3)
        charger = wakeups(p.pid, 2.0)
    finally:
        p.terminate()
        p.wait(5)
    (tmp_path/'car').mkdir()
    p, car, fd, log, files = bmw_up(probe, tmp_path/'car')
    try:
        time.sleep(0.3)
        idle = wakeups(p.pid, 2.0)
    finally:
        stop(p, fd)
    assert charger <= 30 and idle <= 30, (charger, idle)



def test_the_children_hold_no_ep0(probe, tmp_path):
    """car-bt-power.md #9: no fd of q2-iap was close-on-exec, and its children (the report writer, the library's read,
    the cover's search and its ffmpeg) held every ep0 open: a lost link's teardown then found FunctionFS busy and the
    relink failed while one ran (ffmpeg on a 2026-11-22 cover ran 13.4 s). Here ffmpeg is a stand-in that waits 3 s;
    q2-iap is killed while it runs, and ep0's other end must see the last holder gone at once (a pty's master reads EIO
    once no fd of its slave is open), not when the stand-in ends."""
    wait = tmp_path/'ffmpeg'
    wait.write_text('#!/bin/sh\nsleep 3\n')
    wait.chmod(0o755)
    (tmp_path/'run').mkdir()
    p, car, fd, log, files = bmw_up(probe, tmp_path/'run', '--ffmpeg', str(wait), '--art-made', str(tmp_path/'made'))
    try:
        put_whole(files['np'], 'state=1\npos=1000\nlen=200\ntitle=T\nartist=A\nalbum=B\npath=/nowhere/x.flac\n')
        np_updates(car)
        assert 'asked of ffmpeg' in log_wait(log, lambda t: 'asked of ffmpeg' in t)
        time.sleep(0.2)  # ffmpeg's stand-in started
    finally:
        os.close(car.ffs.slave)  # the test's own hold on ep0
        p.kill()
        p.wait(5)
        os.close(fd)
    killed = time.time()
    gone, end = None, killed + 2.5
    while gone is None and time.time() < end:
        try:
            r, _, _ = select.select([car.ffs.master], [], [], 0.05)
            if r:
                os.read(car.ffs.master, 65536)
        except OSError:
            gone = time.time()
    assert gone is not None and gone - killed < 1.0, f'ep0 held {gone and gone - killed} s by a child of q2-iap after it was killed'



def test_a_slow_log_holds_nothing(probe, tmp_path, monkeypatch):
    """q2car.1.log of 2026-11-22: three stalls of 109 to 278 ms were a write to the card's log from the loop. The log
    is a FIFO here whose reader stops while the car sends 40 seeks, a page's pipe, as a card that stalls; the car's packet must
    still be acknowledged within the BMW's first two tries (400 ms), and every line arrives once the reader goes on."""
    import threading
    log = tmp_path/'probe.log'
    os.mkfifo(log)
    got, pause = [], threading.Event()

    def reader():
        r = os.open(log, os.O_RDONLY)
        fcntl.fcntl(r, 1031, 4096)  # F_SETPIPE_SZ: a pipe of one page
        while True:
            while pause.is_set():
                time.sleep(0.05)
            b = os.read(r, 4096)
            if not b:
                break
            got.append(b)
        os.close(r)
    t = threading.Thread(target=reader, daemon=True)
    t.start()
    p, car, fd, _, files = bmw_up(probe, tmp_path)
    try:
        pause.set()
        time.sleep(0.05)
        for k in range(40):  # 40 seeks: 40 lines or more each way, past the page
            car.send(message(0x5003, prm(0, (1000 * k).to_bytes(4, 'big'))))
        waited = acked_within(car, 1.0)
    finally:
        pause.clear()
        stop(p, fd)
        t.join(5)
    text = b''.join(got).decode(errors='replace')
    assert waited is not None and waited < 0.4, f"the car's packet acknowledged after {waited} s"
    assert text.count('seek ') >= 40 and 'end: signalled' in text


def test_position_updates_are_counted_not_logged(probe, tmp_path):
    """car-bt-power.md #4: q2car.log ran at 870 B/s, 85 % of it the position update a playing song sends twice a second
    and its acknowledgement, five lines each. They are counted in the link's health line now, and the rest stays: the
    first Now Playing's message by name, and every packet that is not a bare acknowledgement."""
    p, car, fd, log, files = bmw_up(probe, tmp_path, '--health-ms', '1000')
    try:
        np_updates(car)
        start = time.monotonic()  # the log's stamps are this clock's (CLOCK_MONOTONIC)
        np_and_files(car, lambda u: False, seconds=3.2)
        end = time.monotonic()
    finally:
        stop(p, fd)
    text = log.read_text()  # whole: q2-iap's end waits for its log's writer
    health = [l for l in text.splitlines() if 'link: health' in l and 'position updates' in l]
    assert health and sum(int(l.split('not logged: ')[1].split(' position')[0]) for l in health) >= 5, health
    assert 'msg > 5001 NowPlayingUpdate' in text
    window = [l for l in text.splitlines() if l[:1].isdigit() and start <= float(l.split(' ', 1)[0]) < end]
    rate = sum(len(l) + 1 for l in window) / (end - start)
    assert rate < 400, f'{rate:.0f} B/s while a song plays'



def test_the_log_is_capped_within_a_cable(probe, tmp_path):
    """car-bt-power.md #4: nothing limited q2car.log within a cable (3.1 MB an hour; a ten hour drive about 31 MB, in
    RAM when the card is away). Past the cap (6 MB, --log-cap here 20,000 bytes) the packet trace stops and q2-iap's
    own events and the link's go on; past twice the cap only those of q2-iap and the link."""
    p, car, fd, log, files = bmw_up(probe, tmp_path, '--log-cap', '20000')
    try:
        for k in range(150):
            car.send(message(0x5003, prm(0, (1000 * k).to_bytes(4, 'big'))))
        time.sleep(0.3)
    finally:
        stop(p, fd)
    text = log.read_text()
    after = text.split('no packet trace from here', 1)
    assert len(after) == 2, len(text)
    assert ' pkt ' not in after[1].split('only q2-iap')[0] and 'seek ' in after[1].split('only q2-iap')[0]
    assert 'end: signalled' in text, 'q2-iap\'s own last line kept'



def answered(ffs, timeout=1.0):
    """A GET_REPORT on ep0 answered (its 8 zero bytes) within timeout: the loop is alive."""
    ffs.buf = b''
    while ffs.read(1, 0.05):
        pass
    ffs.setup(0xa1, 1, 0x0101, 2, 8)
    return ffs.read(8, timeout) == bytes(8)


def test_a_tiny_max_packet_is_not_taken(probe, tmp_path):
    """car-bt-power.md #13: a SYN offering a maximum packet of 12 to 19 bytes left a file transfer's piece room 0, so
    the car's go for a cover spun the loop for good (at 11 the room wrapped). No car offers that; such a SYN is not
    taken now, and the loop goes on."""
    np = tmp_path/'now'
    np.write_text('state=1\npos=1000\nlen=200\ntitle=T\nartist=A\nalbum=B\npath=/nowhere/x.flac\n')
    p, ffs, log = start(probe, tmp_path, '--iap2', '--art-dir', str(tmp_path), '--np', str(np), '--ask-dir',
                        str(tmp_path), '--art-made', str(tmp_path/'made'))
    fd = os.open(ffs.path/'ep1', os.O_RDONLY | os.O_NONBLOCK)
    car = BmwCar(ffs, fd)
    jpeg = subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'testsrc=size=160x160', '-frames:v', '1',
                           '-f', 'mjpeg', '-'], check=True, capture_output=True).stdout
    try:
        ffs.event(ENABLE)
        car.write(bytes([0xff, 0x55, 0x02, 0x00, 0xee, 0x10]))
        car.report()
        car.write(packet(0x80, car.seq, 0, 0, BMW_SYN[:2] + (12).to_bytes(2, 'big') + BMW_SYN[4:]))
        try:
            _, syn_seq, _, _, _ = car.packet(1.0)
            car.write(packet(0x40, car.seq, syn_seq, 0))
        except AssertionError:
            pass
        (tmp_path/f"{fnv_part(fnv_part(2166136261, 'A'), 'B'):08x}.jpg").write_bytes(jpeg)
        takes = b''.join(i.to_bytes(2, 'big') for i in (0x5001, 0x4E09))
        car.send(message(0x1D01, prm(0, b'BMW 83828\0'), prm(7, takes)))  # identified, its answers too long to go
        time.sleep(0.5)
        car.send(message(0x5000, prm(0, prm(0x1a)), prm(1, prm(0))))
        time.sleep(1.5)
        car.send(bytes([0x80, 0x01]), session=2)
        time.sleep(0.3)
        alive = answered(ffs)
    finally:
        p.kill()
        p.wait(5)
        os.close(fd)
    assert alive, 'the loop stopped'
    assert 'max packet 12 bytes, under' in log.read_text()


def test_an_answer_ep0_does_not_take_is_a_lost_link(probe, tmp_path):
    """car-bt-power.md #12: the answer to an IN control request was written to ep0 with no time limit, where the OUT
    data stage's read has 2 s since a 35 s hold. Here ep0's other end stops reading, so the answers' writes wait;
    each now has DATA_STAGE_S, and one not taken loses the link (the control pipe is out of step with the car), as a
    data stage that is not its report does."""
    p, car, fd, log, files = bmw_up(probe, tmp_path)
    try:
        for _ in range(200):  # GET_REPORTs of 1 KB each, never read: past the pty's buffer
            car.ffs.setup(0xa1, 1, 0x0101, 2, 1024)
        deadline = time.time() + 8
        while time.time() < deadline and 'not taken in' not in log.read_text():
            time.sleep(0.1)
    finally:
        p.kill()
        p.wait(5)
        os.close(fd)
    assert 'link: lost (an answer on ep0 not taken in 2 s)' in log.read_text()



def test_two_presses_inside_a_poll_both_reach_the_shim(probe, tmp_path):
    """car-bt-power.md #8: the car's buttons went to the shim through one file, renamed over at each press and read
    every 500 ms, so two presses inside one read lost the first (two taps of Next 300 ms apart skipped one song). They
    go down the shim's queue now (coda_rc_cmdq.h, a FIFO demo's shim makes and reads), a line each; here the FIFO is
    the test's, and two Next presses 150 ms apart arrive as two lines."""
    q = tmp_path/'cmdq'
    os.mkfifo(q)
    r = os.open(q, os.O_RDWR | os.O_NONBLOCK)
    p, car, fd, log, files = bmw_up(probe, tmp_path)
    got = b''
    try:
        car.send(bytes([0x40, 0x40, 0, 6 + len(BMW_START_HID), 0x68, 0x00]) + BMW_START_HID)
        for b in (0x04, 0x00, 0x04, 0x00):
            car.send(message(0x6802, prm(0, b'\x00\x01'), prm(1, bytes([b]))))
            time.sleep(0.04)
        time.sleep(0.3)
        try:
            got = os.read(r, 4096)
        except BlockingIOError:
            pass
    finally:
        stop(p, fd)
        os.close(r)
    lines = [l.split(' ', 1)[1] for l in got.decode().splitlines()]
    assert lines == ['next', 'next'], (lines, files['cmd'].exists() and files['cmd'].read_text())


# patch/q2car.sh's ffs_unmount, in gadget_down: a FunctionFS mount that will not go says so, with the pids holding a
# file under it, where it failed silently and the relink then found the gadget still there.
def test_a_busy_functionfs_mount_says_who_holds_it(carlogs):
    ffs, shim = carlogs / 'ffs', carlogs / 'bin'
    shim.mkdir()
    for n in 1, 2, 3, 4:
        (ffs / f'iap{n}').mkdir(parents=True)
    (shim / 'mountpoint').write_text('#!/bin/sh\nexit 0\n')  # all four mounted
    (shim / 'umount').write_text('#!/bin/sh\ncase "$1" in */iap2) echo "umount: $1: target is busy" >&2; exit 1 ;; esac\n')
    for f in 'mountpoint', 'umount':
        (shim / f).chmod(0o755)
    # a child holds iap2, as q2-iap's child did; started without q2-heavy's no-core-dump preload (tools/ci/nodump.c),
    # whose PR_SET_DUMPABLE 0 makes a process's /proc/PID/fd unreadable even to its own user
    clean = {k: v for k, v in os.environ.items() if k != 'LD_PRELOAD'}
    holder = subprocess.Popen([sys.executable, '-c', 'import sys, time; f = open(sys.argv[1], "w"); print(1, flush=True); time.sleep(60)',
                               str(ffs / 'iap2' / 'ep0')], env=clean, stdout=subprocess.PIPE, text=True)
    try:
        assert holder.stdout and holder.stdout.readline().strip() == '1'
        env = dict(os.environ, Q2CAR_RUN=str(carlogs / 'run'), Q2CAR_LOGDIR=str(carlogs / 'logs'), Q2CAR_FFS=str(ffs),
                   PATH=f'{shim}:{os.environ["PATH"]}')
        subprocess.run(['sh', str(Q2CAR), '_unmount'], env=env, check=True, timeout=30)
    finally:
        holder.kill()
        holder.wait()
    log = (carlogs / 'logs/q2car.log').read_text()
    lines = [line for line in log.splitlines() if 'could not unmount' in line]
    assert len(lines) == 1 and f'{ffs}/iap2: busy (held by: ' in lines[0] and f' {holder.pid} ' in f' {lines[0]}', log
    assert 'target is busy' in log  # umount's own words kept too


if __name__ == '__main__':
    sys.exit(pytest.main([__file__, '-q', *sys.argv[1:]]))
