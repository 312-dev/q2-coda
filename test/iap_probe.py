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


if __name__ == '__main__':
    sys.exit(pytest.main([__file__, '-q', *sys.argv[1:]]))
