"""q2bt's core (patch/q2bt_core.c, docs/internals.md#bluetooth-link-monitor): when a stream counts as running, the
kernel's hci_dev_info and connection list as laid out, the four HCI commands and their answers, the A2DP socket's
queue from /proc/net/l2cap, the codec from bluealsa's GetPCMs, the log line and its rotation. Built for this machine
with test/unit/q2guard_host.c."""
import ctypes as C
import subprocess

import pytest

from test_q2guard import ROOT

UNSET = -1000
EV_NONE, EV_START, EV_STOP = range(3)


@pytest.fixture(scope='module')
def L(tmp_path_factory):
    out = tmp_path_factory.mktemp('q2bt') / 'q2bt.so'
    subprocess.run(['clang', '-shared', '-fPIC', '-O1', '-DQ2GUARD_HOST', '-Wall', '-Werror', '-Wno-unused-parameter',
                    f'-DPEQ_ROOT="{out.parent}"', ROOT / 'patch/q2bt_core.c', ROOT / 'patch/q2guard_core.c',
                    ROOT / 'patch/card_store.c', ROOT / 'test/unit/q2guard_host.c', '-o', out], check=True)
    lib = C.CDLL(str(out))
    lib.bt_detect_step.argtypes = [C.c_void_p, C.c_uint, C.c_uint]
    lib.bt_l2cap_wmem.argtypes = [C.c_char_p]
    lib.bt_pcms.argtypes = [C.c_char_p, C.c_char_p, C.c_int, C.POINTER(C.c_uint), C.POINTER(C.c_uint)]
    lib.bt_event.argtypes = [C.c_void_p, C.c_char_p, C.c_int]
    lib.bt_cmd.argtypes = [C.c_void_p, C.c_int, C.c_int]
    lib.bt_link_clear.argtypes = [C.c_void_p, C.c_int]
    lib.bt_devinfo.argtypes = [C.c_char_p, C.c_void_p]
    lib.bt_acl_handle.argtypes = [C.c_char_p, C.c_int]
    lib.bt_line.argtypes = [C.c_void_p, C.c_int, C.c_void_p]
    lib.bt_rate.argtypes = lib.bt_ticks_pct.argtypes = [C.c_uint, C.c_uint]
    lib.bt_handle_follow.argtypes = [C.c_void_p, C.c_int]
    lib.bt_schedstat.argtypes = [C.c_char_p, C.POINTER(C.c_uint), C.POINTER(C.c_uint)]
    lib.bt_encoder_thread.argtypes = [C.c_char_p]
    lib.log_named.restype = C.c_void_p
    lib.log_named.argtypes = [C.c_char_p, C.c_char_p, C.c_uint]
    lib.heat_add.argtypes = [C.c_void_p, C.c_char_p]
    lib.bt_flush.argtypes = [C.c_void_p, C.c_char_p]
    return lib


class Detect(C.Structure):
    _fields_ = [('on', C.c_int), ('have', C.c_int), ('at', C.c_uint), ('bytes', C.c_uint), ('quiet', C.c_uint)]


class Link(C.Structure):
    _fields_ = [(n, C.c_int) for n in ('handle', 'rssi', 'lq', 'tpl', 'afh', 'answered')]


class Counts(C.Structure):
    _fields_ = [('acl_tx', C.c_uint), ('byte_tx', C.c_uint), ('err_tx', C.c_uint), ('acl_mtu', C.c_int),
                ('acl_pkts', C.c_int)]


class Sample(C.Structure):
    _fields_ = [('wall', C.c_long), ('up', C.c_uint)] + [(n, C.c_int) for n in (
        'kbps', 'acl', 'wq', 'rssi', 'lq', 'tpl', 'afh', 'err', 'wtx', 'wrx', 'cpu', 'ba', 'own', 'h', 'er', 'ew')]


# ---- is a stream running ----

def run(L, rates, d=None, dt=1000):
    """Feed one count a call, each interval's bytes a second given; the events."""
    d = d or Detect()
    now, total, events = 1000, 50000, [L.bt_detect_step(C.byref(d), 1000, 50000)]
    for r in rates:
        now += dt
        total = (total + r * dt // 1000) & 0xffffffff
        events.append(L.bt_detect_step(C.byref(d), now, total))
    return events, d


def test_a_stream_starts_on_the_first_busy_interval_and_not_on_a_trickle(L):
    events, d = run(L, [100, 3999, 72000, 72000])  # AVRCP and HFP chatter; then aptX HD's 576 kbps
    assert events == [EV_NONE, EV_NONE, EV_NONE, EV_START, EV_NONE] and d.on


def test_a_stall_is_sampled_through_and_the_stream_ends_after_30_quiet_seconds(L):
    events, d = run(L, [72000] + [0] * 29)
    assert EV_STOP not in events and d.on  # a radio that took nothing for 29 s is still the stream
    events, d = run(L, [72000] + [0] * 29 + [72000] + [0] * 30)
    assert events.count(EV_STOP) == 1 and events[-1] == EV_STOP and not d.on


def test_while_off_it_watches_at_q2guards_pace(L):
    """Off, q2guard calls it every 5 s: a 5 s interval at stream rate starts it as well."""
    events, _ = run(L, [0, 72000], dt=5000)
    assert events == [EV_NONE, EV_NONE, EV_START]


def test_a_counter_that_restarts_is_not_a_burst(L):
    d = Detect()
    L.bt_detect_step(C.byref(d), 0, 900_000_000)
    assert L.bt_detect_step(C.byref(d), 1000, 10) == EV_NONE and not d.on  # the controller came up again
    d2 = Detect()
    L.bt_detect_step(C.byref(d2), 0, 0xffffff00)
    assert L.bt_detect_step(C.byref(d2), 1000, 72000) == EV_START  # a true wrap is counted


# ---- the kernel's structs ----

class HciDevStats(C.Structure):
    _fields_ = [(n, C.c_uint32) for n in ('err_rx', 'err_tx', 'cmd_tx', 'evt_rx', 'acl_tx', 'acl_rx', 'sco_tx',
                                          'sco_rx', 'byte_rx', 'byte_tx')]


class HciDevInfo(C.Structure):  # include/net/bluetooth/hci_sock.h, bdaddr_t is __packed
    _fields_ = [('dev_id', C.c_uint16), ('name', C.c_char * 8), ('bdaddr', C.c_uint8 * 6), ('flags', C.c_uint32),
                ('type', C.c_uint8), ('features', C.c_uint8 * 8), ('pkt_type', C.c_uint32),
                ('link_policy', C.c_uint32), ('link_mode', C.c_uint32), ('acl_mtu', C.c_uint16),
                ('acl_pkts', C.c_uint16), ('sco_mtu', C.c_uint16), ('sco_pkts', C.c_uint16), ('stat', HciDevStats)]


def test_hci_dev_info_is_read_where_the_kernel_puts_it(L):
    assert C.sizeof(HciDevInfo) == 92
    di = HciDevInfo(acl_mtu=1021, acl_pkts=8)
    di.stat.err_tx, di.stat.acl_tx, di.stat.byte_tx, di.stat.byte_rx = 3, 70000, 123456789, 999
    c = Counts()
    L.bt_devinfo(bytes(di), C.byref(c))
    assert (c.acl_mtu, c.acl_pkts, c.err_tx, c.acl_tx, c.byte_tx) == (1021, 8, 3, 70000, 123456789)


class ConnInfo(C.Structure):
    _fields_ = [('handle', C.c_uint16), ('bdaddr', C.c_uint8 * 6), ('type', C.c_uint8), ('out', C.c_uint8),
                ('state', C.c_uint16), ('link_mode', C.c_uint32)]


def test_the_first_acl_link_s_handle(L):
    assert C.sizeof(ConnInfo) == 16
    req = (C.c_uint16 * 2)(0, 2)
    sco, acl = ConnInfo(handle=0x0101, type=0), ConnInfo(handle=0x000c, type=1)
    assert L.bt_acl_handle(bytes(req) + bytes(sco) + bytes(acl), 4) == 0x000c
    assert L.bt_acl_handle(bytes((C.c_uint16 * 2)(0, 1)) + bytes(sco), 4) == -1
    assert L.bt_acl_handle(bytes((C.c_uint16 * 2)(0, 0)), 4) == -1


# ---- the four commands ----

def cmd(L, op, handle):
    out = C.create_string_buffer(8)
    n = L.bt_cmd(out, op, handle)
    return out.raw[:n]


def test_the_commands_are_the_spec_s_bytes(L):
    assert cmd(L, 0x1405, 0x000c) == bytes([1, 0x05, 0x14, 2, 0x0c, 0])        # Read RSSI
    assert cmd(L, 0x1403, 0x0e0c) == bytes([1, 0x03, 0x14, 2, 0x0c, 0x0e])     # Read Link Quality, 12 bits
    assert cmd(L, 0x0c2d, 0x000c) == bytes([1, 0x2d, 0x0c, 3, 0x0c, 0, 0])     # Read Transmit Power Level, current
    assert cmd(L, 0x1406, 0x000c) == bytes([1, 0x06, 0x14, 2, 0x0c, 0])        # Read AFH Channel Map


def complete(op, params):
    body = bytes([1, op & 0xff, op >> 8]) + bytes(params)
    return bytes([4, 0x0e, len(body)]) + body


def test_the_answers_fill_the_link(L):
    k = Link()
    L.bt_link_clear(C.byref(k), 0x0c)
    afh = [0xff] * 5 + [0] * 4 + [0x80]  # channels 0-39 in use; bit 79 is not a channel
    for ev in (complete(0x1405, [0, 0x0c, 0, 0xf4]), complete(0x1403, [0, 0x0c, 0, 0xff]),
               complete(0x0c2d, [0, 0x0c, 0, 4]), complete(0x1406, [0, 0x0c, 0, 1] + afh)):
        assert L.bt_event(C.byref(k), ev, len(ev)) == 1
    assert (k.rssi, k.lq, k.tpl, k.afh, k.answered) == (-12, 255, 4, 40, 4)


def test_other_events_handles_and_refusals(L):
    k = Link()
    L.bt_link_clear(C.byref(k), 0x0c)
    for ev in (complete(0x1405, [0, 0x0d, 0, 0xf4]),        # another link's
               complete(0x0c03, [0]),                        # the kernel's own Reset
               bytes([4, 0x13, 5, 1, 0x0c, 0, 1, 0]),        # Number Of Completed Packets
               complete(0x1405, [0, 0x0c, 0])[:-1]):          # cut short
        assert L.bt_event(C.byref(k), ev, len(ev)) == 0
    ev = complete(0x1405, [0x02, 0x0c, 0, 0xf4])            # Unknown Connection Identifier
    assert L.bt_event(C.byref(k), ev, len(ev)) == 1 and k.rssi == UNSET and k.answered == 1


# ---- the A2DP socket's queue ----

L2CAP = b"""sk               RefCnt Rmem   Wmem   User   Inode  Parent
00000000 2      0      0      0      14021  0
00000000 2      0      2730   0      15990  0
00000000 2      0      120    0      15991  0
"""
L2CAP_OLD = b"""sk               RefCnt Rmem   Wmem   User   Inode  Src Dst Parent
c7d1c000 2      0      1820   0      14021  00:11:22:33:44:55 66:77:88:99:aa:bb 0
"""


def test_the_largest_wmem_is_the_a2dp_socket_s_queue(L):
    assert L.bt_l2cap_wmem(L2CAP) == 2730
    assert L.bt_l2cap_wmem(L2CAP_OLD) == 1820
    assert L.bt_l2cap_wmem(L2CAP.split(b'\n')[0] + b'\n') == 0  # no sockets: nothing queued
    assert L.bt_l2cap_wmem(b'') == -1 and L.bt_l2cap_wmem(b'no such table\n1 2 3\n') == -1


# ---- the codec ----

PCMS = b'''method return time=1795464061.123 sender=:1.3 -> destination=:1.20 serial=40 reply_serial=2
   array [
      dict entry(
         object path "/org/bluealsa/hci0/dev_00_11_22_33_44_55/hfpag/sink"
         array [
            dict entry(
               string "Codec"
               variant                   string "CVSD"
            )
         ]
      )
      dict entry(
         object path "/org/bluealsa/hci0/dev_00_11_22_33_44_55/a2dpsrc/sink"
         array [
            dict entry(
               string "Device"
               variant                   object path "/org/bluez/hci0/dev_00_11_22_33_44_55"
            )
            dict entry(
               string "Sampling"
               variant                   uint32 48000
            )
            dict entry(
               string "Codec"
               variant                   string "aptX-HD"
            )
            dict entry(
               string "Delay"
               variant                   uint16 2357
            )
            dict entry(
               string "SoftVolume"
               variant                   boolean false
            )
         ]
      )
   ]
'''


def pcms(L, text):
    codec, hz, delay = C.create_string_buffer(24), C.c_uint(), C.c_uint()
    ok = L.bt_pcms(text, codec, 24, C.byref(hz), C.byref(delay))
    return ok, codec.value.decode(), hz.value, delay.value


def test_the_a2dp_source_s_codec_rate_and_delay(L):
    assert pcms(L, PCMS) == (1, 'aptX-HD', 48000, 2357)  # the Px7 on LDAC Auto, 2026-11-17


def test_no_a2dp_pcm_or_no_codec_is_none(L):
    assert pcms(L, PCMS.split(b'a2dpsrc')[0]) == (0, '', 0, 0)
    assert pcms(L, b'')[0] == 0
    no_codec = PCMS.replace(b'string "Codec"', b'string "Other"')
    assert pcms(L, no_codec)[:3] == (0, '', 48000)


# ---- the line ----

def line(L, **f):
    s = Sample(wall=1795464061, up=4242, **{k: f.get(k, -1) for k in (
        'kbps', 'acl', 'wq', 'err', 'wtx', 'wrx', 'cpu', 'ba', 'own', 'h', 'er', 'ew')},
        **{k: f.get(k, UNSET) for k in ('rssi', 'lq', 'tpl', 'afh')})
    out = C.create_string_buffer(256)
    n = L.bt_line(out, 256, C.byref(s))
    assert n == len(out.value)
    return out.value.decode()


def test_a_line_has_every_field(L):
    text = line(L, kbps=612, acl=95, wq=2730, rssi=-12, lq=255, tpl=4, afh=40, err=0, wtx=3, wrx=120, cpu=23,
                ba=81, own=4, h=2, er=88, ew=3)
    assert text == ('2026-11-23T20:01:01Z up=4242 kbps=612 acl=95 wq=2730 rssi=-12 lq=255 tpl=4 afh=40 err=0 wtx=3 '
                    'wrx=120 cpu=23 ba=81 own=4 h=2 er=88 ew=3\n')
    assert len(text) < 140  # about 9 minutes in the 64 KB ring


def test_a_field_not_read_is_a_dash(L):
    assert line(L) == ('2026-11-23T20:01:01Z up=4242 kbps=- acl=- wq=- rssi=- lq=- tpl=- afh=- err=- wtx=- wrx=- '
                       'cpu=- ba=- own=- h=- er=- ew=-\n')


def test_rates_and_shares(L):
    assert L.bt_rate(72000, 1000) == 72000 and L.bt_rate(72000, 1250) == 57600 and L.bt_rate(5, 0) == 0
    assert L.bt_rate(100_000_000, 5000) == 20_000_000  # past 32 bits times 1000
    assert L.bt_rate(0xfffffff0, 1000) == 2_000_000_000  # held, not wrapped negative
    assert L.bt_ticks_pct(1, 1000) == 10 and L.bt_ticks_pct(100, 1000) == 1000  # 1 tick of 100: 1.0%
    assert L.bt_ticks_pct(0, 1000) == 0 and L.bt_ticks_pct(3, 0) == 0


def test_the_log_rotates_at_a_megabyte(L, tmp_path):
    d = tmp_path / 'logs'
    log = L.log_named(str(d).encode(), b'q2-bt.0', 65536)
    L.heat_add(log, b'2026-11-23T04:01:01Z START h=12\n')
    d.mkdir()
    (d / 'q2-bt.0').write_bytes(b'x' * 1048576)
    L.card(1)  # the writing child's flush: the card is up
    try:
        assert L.bt_flush(log, str(d).encode()) == 0
    finally:
        L.card(0)
    assert (d / 'q2-bt.1').stat().st_size == 1048576
    assert (d / 'q2-bt.0').read_bytes() == b'2026-11-23T04:01:01Z START h=12\n'


# ---- the link's handle, followed across a reconnect ----

def test_a_new_handle_is_followed_and_reported_once(L):
    """A codec change reconnects the headset: the ACL link comes back on a new handle while the stream runs on (2 s
    with nothing sent is no STOP). The walk of 2026-11-18 read nothing from 04:47:13 on, asking for handle 1."""
    k = Link()
    L.bt_link_clear(C.byref(k), 1)
    assert L.bt_handle_follow(C.byref(k), 1) == 0 and k.handle == 1
    assert L.bt_handle_follow(C.byref(k), 2) == 1 and k.handle == 2
    assert L.bt_handle_follow(C.byref(k), 2) == 0
    assert L.bt_handle_follow(C.byref(k), -1) == 1 and k.handle == -1  # gone for now: nothing asked
    assert L.bt_handle_follow(C.byref(k), 3) == 1 and k.handle == 3


# ---- the encoder thread ----

def schedstat(L, text):
    run, wait = C.c_uint(), C.c_uint()
    ok = L.bt_schedstat(text, C.byref(run), C.byref(wait))
    return ok, run.value, wait.value


def test_schedstat_s_nanoseconds_as_milliseconds(L):
    """/proc/PID/task/TID/schedstat: time on a CPU, time waiting for one (both ns, past 32 bits), timeslices."""
    assert schedstat(L, b'98765432109 1234567890 4321\n') == (1, 98765, 1234)
    assert schedstat(L, b'999999 5 7\n') == (1, 0, 0)
    assert schedstat(L, b'18446744073709551615 0 1\n')[0] == 1  # u64's largest: wraps, as a delta allows
    assert schedstat(L, b'')[0] == 0 and schedstat(L, b'x y z')[0] == 0 and schedstat(L, b'12')[0] == 0


def test_bluealsa_s_encoder_threads_by_name(L):
    for name in (b'ba-a2dp-sbc\n', b'ba-a2dp-aptx-hd\n', b'ba-a2dp-aac', b'ba-a2dp-ldac\n'):
        assert L.bt_encoder_thread(name) == 1
    for name in (b'bluealsa\n', b'ba-th-manager\n', b'ba-rfcomm\n', b'ba-sco-enc\n', b''):
        assert L.bt_encoder_thread(name) == 0
