#!/usr/bin/env python3
"""q2-iap, the iPhone gadget's daemon and route 1's probe (patch/q2_iap.c, docs/iphone-mode.md), built for this machine.

Each kind's FunctionFS blobs are parsed by the rules Linux 5.10's f_fs.c applies (ffs_do_descs, ffs_do_single_desc,
__ffs_data_do_entity, __ffs_data_got_strings), and the probe is run against a stand-in ep0, a pseudo-terminal in raw
mode whose other end plays FunctionFS: the probe's descriptor writes arrive there, and events and data stages are
written back. Checked: what it answers to each request, the echo of iAP2's detect on ep1 (a FIFO), sessions and the
exit status. Run it as python3 test/iap_probe.py (pytest, host cc)."""
import os
import pathlib
import select
import struct
import subprocess
import sys
import time
import tty

import pytest

SRC = pathlib.Path(__file__).resolve().parents[1]/'patch/q2_iap.c'
SETUP, ENABLE, DISABLE = 4, 2, 3


@pytest.fixture(scope='module')
def probe(tmp_path_factory):
    exe = tmp_path_factory.mktemp('probe')/'q2-iap-probe'
    subprocess.run(['cc', '-DPROBE_HOST', '-O2', '-Wall', '-Wextra', '-Werror', '-Wno-unused-parameter',
                    '-Wno-unused-result', str(SRC), '-o', str(exe)], check=True)
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
            time.sleep(0.05)
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
        assert got[3][1][0] == b'Q2 Muse\0' and got[4][1][0] == b'\x00\x05' and 2 in got[5][1]
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
    files = {'car': tmp_path/'q2-iap.audio', 'cmd': tmp_path/'coda-rc.cmd', 'np': tmp_path/'now', 'art': tmp_path/'art'}
    files['art'].mkdir()
    files['np'].write_text('state=1\npos=61000\nlen=245\ntitle=Teardrop\nartist=Massive Attack\nalbum=Mezzanine\n'
                           'path=/nowhere/a.flac\nshuffle=0\nrepeat=2\n')
    p, ffs, log = start(probe, tmp_path, '--iap2', '--np', str(files['np']), '--car-pcm', 'hw:CARD=UAC1Gadget,0',
                        '--car-file', str(files['car']), '--cmd-file', str(files['cmd']), '--art-dir', str(files['art']),
                        *more)
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
    the skip; held past 500 ms the song moves through itself from where it is (61 s of 245), a seek every 500 ms, 2 s
    a step for 2 s, then 4 s, and nothing else at the release."""
    p, car, fd, log, files = bmw_up(probe, tmp_path)

    def hid(b):
        car.send(message(0x6802, prm(0, b'\x00\x01'), prm(1, bytes([b]))))

    def seeks():
        return [int(line.rsplit(' ', 1)[1]) for line in log.read_text().splitlines() if ': seek ' in line and 'held' in line]
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
        hid(0x04)                                    # Next held 3.2 s
        time.sleep(3.2)
        hid(0x00)
        time.sleep(0.4)
        assert seeks() == [63, 65, 67, 69, 73, 77], seeks()
        assert files['cmd'].read_text().split(' ', 1)[1].strip() == 'seek 77', 'no skip after a scan'
        hid(0x08)                                    # Previous held 1.2 s, from where Now Playing says it is
        time.sleep(1.2)
        hid(0x00)
        time.sleep(0.4)
        assert seeks()[6:] == [59, 57], seeks()
    finally:
        stop(p, fd)
    text = log.read_text()
    assert 'hid: usage b5 released after' in text and 'the scan ends' in text


def test_bmw_audio_and_seek(probe, tmp_path):
    """StartUSBDeviceModeAudio writes the player's car file (its device, then 44100), StopUSBDeviceModeAudio removes
    it; the car's seek is a seek in seconds."""
    p, car, fd, log, files = bmw_up(probe, tmp_path)
    try:
        car.send(message(0xDA00))
        mid, ps = car.msg()
        assert mid == 0xDA01 and ps[0] == b'\x07'
        time.sleep(0.1)
        assert files['car'].read_text() == 'hw:CARD=UAC1Gadget,0\n44100\n'
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
        udc.write_text('addressed\n')
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
    (counters/'in_sent').write_text('1000\n')
    (counters/'in_missed').write_text('7\n')
    p, car, fd, log, files = bmw_up(probe, tmp_path, '--state', str(tmp_path/'state'), '--counters', str(counters),
                                    '--under', str(tmp_path/'under'))
    state = tmp_path/'state'
    try:
        time.sleep(1.2)
        assert verdict(state)['verdict'] == 'car' and verdict(state)['name'] == 'BMW 83828'
        car.send(message(0xDA00))
        car.msg()
        time.sleep(1.2)
        (counters/'in_sent').write_text('2000\n')
        time.sleep(1.2)
        v = verdict(state)
        assert (v['audio'], v['flowing'], v['trouble'], v['troubles']) == ('1', '1', '', '0')
        (counters/'in_sent').write_text('2990\n')
        (counters/'in_missed').write_text('17\n')       # 10 of 1000
        time.sleep(1.2)
        assert verdict(state)['troubles'] == '0', '1% is not over 1%'
        (counters/'in_sent').write_text('3970\n')
        (counters/'in_missed').write_text('37\n')       # 20 of 1000
        time.sleep(1.2)
        v = verdict(state)
        assert (v['trouble'], v['troubles']) == ('dropouts', '1')
        (counters/'in_sent').write_text('4970\n')
        (tmp_path/'under').write_text('1\n')
        time.sleep(1.2)
        v = verdict(state)
        assert (v['trouble'], v['troubles']) == ('underruns', '2')
    finally:
        stop(p, fd)
    text = log.read_text()
    assert 'summary: host car (iAP2\'s detect' in text and 'car "BMW 83828", identified yes' in text
    assert 'in_sent 3970 in_missed 30 (0.7%), underruns 1, problems 2' in text


def test_no_sound_when_the_car_asks_and_takes_nothing(probe, tmp_path):
    counters = tmp_path/'params'
    counters.mkdir()
    (counters/'in_sent').write_text('5\n')
    (counters/'in_missed').write_text('0\n')
    p, car, fd, log, files = bmw_up(probe, tmp_path, '--state', str(tmp_path/'state'), '--counters', str(counters))
    try:
        car.send(message(0xDA00))
        car.msg()
        time.sleep(7.5)
        v = verdict(tmp_path/'state')
        assert (v['flowing'], v['trouble']) == ('0', 'nostream')
    finally:
        stop(p, fd)


if __name__ == '__main__':
    sys.exit(pytest.main([__file__, '-q', *sys.argv[1:]]))
