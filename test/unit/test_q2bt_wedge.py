"""q2guard's Bluetooth controller watchdog (patch/q2bt_core.c bt_wedge, docs/internals.md#bluetooth-controller-watchdog):
which kernel lines are signs, when they make the controller suspect, the probe that confirms it, the relaunch asked of
demo and its backoff. Two recorded kernel logs from a Q2 (test/fixtures/h5-*.dmesg) are replayed through it as q2guard
reads them, a batch each 5 s tick."""
import ctypes as C
import re
import subprocess

import pytest

from test_q2guard import ROOT

STORM, WINDOW = 20, 10000
SIGN_NONE, SIGN_H5, SIGN_TIMEOUT = range(3)
W_NONE, W_PROBE, W_RESET, W_HEALTHY, W_GIVEUP, W_DEFER, W_LATE = range(7)
TICK = 5000
CHECKSUM = 'Bluetooth: Error in H5 hdr checksum'
ACKED = 'Bluetooth: Peer acked invalid packet'
TIMEOUT = 'Bluetooth: hci0: command 0x0405 tx timeout'


class Wedge(C.Structure):
    _fields_ = [('h5', C.c_uint * STORM), ('h5_n', C.c_int), ('h5_i', C.c_int), ('suspect', C.c_int),
                ('storm', C.c_int), ('timeout', C.c_int), ('first', C.c_uint), ('last', C.c_uint),
                ('fails', C.c_int), ('probed', C.c_int), ('probed_at', C.c_uint), ('calm_until', C.c_uint),
                ('calm', C.c_int), ('resets', C.c_int), ('have_reset', C.c_int), ('gave_up', C.c_int),
                ('deferred', C.c_int), ('reset_at', C.c_uint), ('asked', C.c_int), ('req', C.c_int)]


@pytest.fixture(scope='module')
def L(tmp_path_factory):
    out = tmp_path_factory.mktemp('q2bt_wedge') / 'q2bt.so'
    subprocess.run(['clang', '-shared', '-fPIC', '-O1', '-DQ2GUARD_HOST', '-Wall', '-Werror', '-Wno-unused-parameter',
                    f'-DPEQ_ROOT="{out.parent}"', ROOT / 'patch/q2bt_core.c', ROOT / 'patch/q2guard_core.c',
                    ROOT / 'patch/card_store.c', ROOT / 'test/unit/q2guard_host.c', '-o', out], check=True)
    lib = C.CDLL(str(out))
    lib.bt_kmsg_sign.argtypes = [C.c_char_p]
    lib.bt_kmsg_split.argtypes = [C.c_char_p, C.POINTER(C.c_uint), C.POINTER(C.c_char_p)]
    lib.bt_wedge_line.argtypes = [C.c_void_p, C.c_uint, C.c_uint, C.c_char_p]
    lib.bt_wedge_step.argtypes = [C.c_void_p, C.c_uint]
    lib.bt_wedge_probed.argtypes = [C.c_void_p, C.c_uint, C.c_int, C.c_int]
    lib.bt_wedge_done.argtypes = [C.c_void_p, C.c_uint, C.c_int]
    lib.bt_probe_answer.argtypes = [C.c_char_p, C.c_int]
    lib.bt_dev_up.argtypes = [C.c_char_p]
    lib.bt_answer_parse.argtypes = [C.c_char_p, C.POINTER(C.c_int)]
    assert C.sizeof(Wedge) == lib.bt_wedge_size()
    return lib


def dmesg(name):
    """A fixture's lines as (the kernel's ms, the message)."""
    out = []
    for line in (ROOT / 'test/fixtures' / name).read_text().splitlines():
        m = re.match(r'\[\s*(\d+)\.(\d{6})\] (.*)', line)
        if m:
            out.append((int(m[1]) * 1000 + int(m[2]) // 1000, m[3]))
    return out


class Guard:
    """q2guard's side as q2bt.c runs it: the kernel lines read each tick, then the step and the probe. now is
    q2guard's clock (here the kernel's plus an offset), wedged says how a probe goes (1 answered, 0 not, -1 down)."""
    def __init__(self, L, offset=1000000):
        self.L, self.w, self.offset, self.busy = L, Wedge(), offset, False
        self.wedged: 'bool | None' = True
        self.events: 'list[tuple[int, int]]' = []
        self.now = 0

    def lines(self, now, lines):
        for kms, msg in lines:
            self.L.bt_wedge_line(C.byref(self.w), now, kms & 0xffffffff, msg.encode())

    def tick(self, now):
        self.now = now
        got = self.L.bt_wedge_step(C.byref(self.w), now)
        if got == W_PROBE:
            answered = -1 if self.wedged is None else int(not self.wedged)
            got = self.L.bt_wedge_probed(C.byref(self.w), now, answered, int(self.busy))
        if got != W_NONE:
            self.events.append((now, got))
        return got

    def replay(self, lines, start=None, until=None):
        """Lines read a tick's worth at a time, from start (q2guard's clock) to until."""
        t = start if start is not None else lines[0][0] + self.offset
        end = until if until is not None else lines[-1][0] + self.offset + 30000
        i = 0
        while t <= end:
            batch = []
            while i < len(lines) and lines[i][0] + self.offset <= t:
                batch.append(lines[i])
                i += 1
            self.lines(t, batch)
            self.tick(t)
            t += TICK
        return t


def test_the_signs(L):
    assert L.bt_kmsg_sign(ACKED.encode()) == SIGN_H5
    assert L.bt_kmsg_sign(CHECKSUM.encode()) == SIGN_H5
    assert L.bt_kmsg_sign(b'Bluetooth: Short H5 packet') == SIGN_H5
    assert L.bt_kmsg_sign(b'Bluetooth: hci0: command 0x0402 tx timeout') == SIGN_TIMEOUT
    for other in (b'Bluetooth: hci0: sending frame failed (-42)', b'IRQ Error, cpu: 1 Cause:0x08800000',
                  b"debugfs: File 'force_bredr_smp' in directory 'hci0' already present!"):
        assert L.bt_kmsg_sign(other) == SIGN_NONE


def test_a_kmsg_record_split(L):
    ms, msg = C.c_uint(), C.c_char_p()
    assert L.bt_kmsg_split(b'3,4021,44312330044,-;Bluetooth: hci0: command 0x0402 tx timeout\n', C.byref(ms),
                           C.byref(msg))
    assert ms.value == 44312330 and (msg.value or b'').startswith(b'Bluetooth: hci0: command 0x0402')
    # microseconds past 2^32 (72 minutes up) and past 2^32 ms (49 days): taken modulo 2^32, never divided in 64 bits
    assert L.bt_kmsg_split(b'3,1,4300000000123456,-;x', C.byref(ms), C.byref(msg))
    assert ms.value == 4300000000123 % 2**32
    for bad in (b'', b'3,1;x', b'3,1,,-;x', b'3,1,123,-', b'continuation line'):
        assert not L.bt_kmsg_split(bad, C.byref(ms), C.byref(msg))


def test_an_isolated_checksum_error_is_not_a_sign_of_trouble(L):
    """2026-11-18 16:09:35Z: one bad header, nothing after it, Bluetooth fine. Nothing is probed."""
    g = Guard(L)
    g.lines(1000000, [(913966, CHECKSUM)])
    for t in range(1000000, 1000000 + 300000, TICK):
        assert g.tick(t) == W_NONE
    assert not g.w.suspect


def test_a_storm_is_twenty_h5_lines_within_ten_seconds(L):
    g = Guard(L)
    g.lines(1000000, [(k * 600, ACKED) for k in range(STORM - 1)])  # 19: too few however close
    assert not g.w.suspect
    g.lines(1000000, [(19 * 600, ACKED)])  # the 20th: the last 20 span 11.4 s
    assert not g.w.suspect
    g.lines(1000000, [(19 * 600 + 300, ACKED)])  # the last 20 span 11.1 s
    assert not g.w.suspect
    burst = [(20000 + k * 100, ACKED) for k in range(STORM)]  # 20 in 1.9 s
    g.lines(1000000, burst)
    assert g.w.suspect == ord('s') and g.w.storm and not g.w.timeout


def test_the_lockup_replayed_is_relaunched_within_seconds(L):
    """2026-11-19 04:12:51Z (h5-wedge fixture): one bad header, then Inquiry Cancel unanswered, a storm of about 64
    lines a second that ended by itself after 6 s, and the commands after it still unanswered. The timeout is the
    first sign; two probes 5 s apart confirm; the relaunch is asked for about 10 s after it."""
    lines = dmesg('h5-wedge-20261119.dmesg')
    g = Guard(L)
    g.replay(lines)
    resets = [t for t, e in g.events if e == W_RESET]
    assert len(resets) == 1 and g.w.req == 1 and g.w.asked
    first_sign = 44312330 + g.offset
    assert g.w.suspect == 0 and g.w.timeout and g.w.storm  # both seen before the relaunch
    assert g.w.first - first_sign < TICK  # read at the next tick
    assert 0 < resets[0] - first_sign <= 3 * TICK


def test_the_long_storm_replayed_is_relaunched(L):
    """2026-11-19 (h5-storm fixture): the storm that ran 47 minutes with nothing else in the log but hciconfig's own
    timeouts. The storm alone makes it suspect, and the probes confirm."""
    lines = dmesg('h5-storm-20261119.dmesg')
    g = Guard(L)
    g.replay(lines)
    assert [e for _, e in g.events] == [W_RESET] and g.w.storm


def test_a_storm_the_controller_answers_through_is_left_alone(L):
    g = Guard(L)
    g.wedged = False
    g.replay(dmesg('h5-storm-20261119.dmesg'))
    assert [e for _, e in g.events] == [W_HEALTHY] and g.w.req == 0
    # answered: the next probe no sooner than a minute on, however the storm goes on
    t = g.now + TICK
    g.lines(t, [(10**8 + k * 80, ACKED) for k in range(60)])
    assert g.tick(t) == W_NONE and g.w.suspect


def test_no_verdict_never_relaunches(L):
    """hci0 down (Bluetooth off, or the stack mid-start): the probe says nothing, and nothing is asked for."""
    g = Guard(L)
    g.wedged = None
    g.replay(dmesg('h5-wedge-20261119.dmesg'))
    assert g.events == [] and g.w.req == 0


def wedge_now(g, t):
    """A tx timeout read at t (q2guard's clock), then ticks until something other than nothing happens."""
    g.lines(t, [(t - g.offset, TIMEOUT)])
    while True:
        got = g.tick(t)
        if got != W_NONE:
            return t, got
        t += TICK


def test_backoff_then_give_up_then_an_answer_clears_it(L):
    g = Guard(L)
    t, got = wedge_now(g, 2000000)
    assert got == W_RESET and g.w.req == 1
    L.bt_wedge_done(C.byref(g.w), t + 3000, 1)
    done = t + 3000
    # the stack starting: its lines are not counted for BT_RESET_SETTLE_MS
    g.lines(done + 1000, [(done + 1000 - g.offset, TIMEOUT)])
    assert not g.w.suspect
    # wedged again after the settle, 70 s on: due, but within 2 minutes of the last: waits (said once)
    t, got = wedge_now(g, done + 70000)
    assert got == W_DEFER
    for _ in range(4):
        t += TICK
        assert g.tick(t) == W_NONE
    while g.tick(t) != W_RESET:
        t += TICK
    assert t - done >= 120000 and g.w.req == 2 and g.w.resets == 2
    L.bt_wedge_done(C.byref(g.w), t, 2)
    second = t
    # the third waits 4 minutes (the gap doubled)
    t, got = wedge_now(g, second + 70000)
    assert got == W_DEFER
    while g.tick(t) != W_RESET:
        t += TICK
    assert t - second >= 240000 and g.w.req == 3
    L.bt_wedge_done(C.byref(g.w), t, 3)
    # a fourth in a row: given up, said once, and asked for no more
    t, got = wedge_now(g, t + 70000)
    assert got == W_GIVEUP and g.w.gave_up
    for _ in range(100):
        t += TICK
        g.lines(t, [(t - g.offset, TIMEOUT)])
        assert g.tick(t) == W_NONE
    assert g.w.req == 3
    # someone relaunched it (Bluetooth switched off and on): the next probe is answered, and it starts afresh
    g.wedged = False
    while g.tick(t) == W_NONE:
        t += TICK
    assert g.events[-1][1] == W_HEALTHY and not g.w.gave_up


def test_a_wedge_long_after_the_last_relaunch_is_not_in_a_row(L):
    g = Guard(L)
    t, got = wedge_now(g, 2000000)
    L.bt_wedge_done(C.byref(g.w), t, 1)
    t, got = wedge_now(g, t + 600000)
    assert got == W_RESET and g.w.resets == 1


def test_never_during_a_codec_switch(L):
    g = Guard(L)
    g.busy = True
    t, got = wedge_now(g, 2000000)
    assert got == W_DEFER
    for _ in range(5):
        t += TICK
        assert g.tick(t) == W_NONE
    g.busy = False
    t += TICK
    assert g.tick(t) == W_RESET


def test_demo_not_answering(L):
    g = Guard(L)
    t, got = wedge_now(g, 2000000)
    assert got == W_RESET
    for _ in range(11):
        t += TICK
        assert g.tick(t) == W_NONE
    t += TICK
    assert g.tick(t) == W_LATE and not g.w.asked
    # an answer to another number is not this one's
    L.bt_wedge_done(C.byref(g.w), t, 7)
    assert g.w.req == 1


def test_the_probe_and_its_answer(L):
    # Command Complete (0x0e) to Read Local Version (opcode 0x1001), as the raw socket reads it
    assert L.bt_probe_answer(bytes([4, 0x0e, 12, 1, 0x01, 0x10, 0]) + bytes(8), 15)
    assert not L.bt_probe_answer(bytes([4, 0x0e, 4, 1, 0x05, 0x14, 0]), 7)  # Read RSSI's
    assert not L.bt_probe_answer(bytes([4, 0x0f, 4, 0, 1, 0x01, 0x10]), 7)  # a Command Status
    assert not L.bt_probe_answer(bytes([4, 0x0e, 12, 1, 0x01]), 5)
    di = bytearray(92)
    assert not L.bt_dev_up(bytes(di))
    di[16] = 1 | 4  # HCI_UP | HCI_RUNNING
    assert L.bt_dev_up(bytes(di))


def test_demo_answer_parse(L):
    off = C.c_int()
    assert L.bt_answer_parse(b'ack -1\ncancel -1\ncard -\nbt 3 done\n', C.byref(off)) == 3 and not off.value
    assert L.bt_answer_parse(b'ack 0\ncancel 0\ncard -\nbt 4 off\n', C.byref(off)) == 4 and off.value
    assert L.bt_answer_parse(b'ack 0\ncancel 0\ncard -\n', C.byref(off)) == 0
    assert L.bt_answer_parse(b'', C.byref(off)) == 0
