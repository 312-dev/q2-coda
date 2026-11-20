#!/usr/bin/env python3
"""Route 1's experiment 1 probe (tools/iap_probe/q2-iap-probe.c, docs/iphone-mode.md) built for this machine.

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

SRC = pathlib.Path(__file__).resolve().parents[1]/'tools/iap_probe/q2-iap-probe.c'
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


if __name__ == '__main__':
    sys.exit(pytest.main([__file__, '-q', *sys.argv[1:]]))
