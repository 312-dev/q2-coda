"""q2bt's core (patch/q2bt_core.c, docs/internals.md#bluetooth-link-monitor): when a stream counts as running, the
kernel's hci_dev_info and connection list as laid out, the four HCI commands and their answers, the A2DP socket's
queue from /proc/net/l2cap, the codec from bluealsa's GetPCMs, the log line and its rotation, and the EDR
packet-type command to the A2DP sink's link. Built for this machine
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
    lib.bt_completed.argtypes = [C.c_char_p, C.c_int, C.c_int]
    lib.bt_fly_step.argtypes = [C.c_void_p, C.c_uint, C.c_uint]
    lib.bt_edr_reset.argtypes = [C.c_void_p]
    lib.bt_edr_step.argtypes = [C.c_void_p, C.c_int, C.c_void_p]
    lib.bt_edr_event.argtypes = [C.c_void_p, C.c_char_p, C.c_int, C.c_void_p, C.c_int]
    lib.bt_pcms_sink.argtypes = [C.c_char_p, C.c_void_p]
    lib.bt_acl_handle_of.argtypes = [C.c_char_p, C.c_int, C.c_char_p]
    lib.bt_step.argtypes = [C.c_void_p, C.c_uint, C.c_int, C.c_int, C.c_int, C.c_int, C.c_int]
    lib.bt_codec_rank.argtypes = [C.c_char_p]
    lib.bt_codec_name.restype = C.c_void_p
    lib.log_named.restype = C.c_void_p
    lib.log_named.argtypes = [C.c_char_p, C.c_char_p, C.c_uint]
    lib.heat_add.argtypes = [C.c_void_p, C.c_char_p]
    lib.bt_flush.argtypes = [C.c_void_p, C.c_char_p]
    return lib


class Detect(C.Structure):
    _fields_ = [('on', C.c_int), ('have', C.c_int), ('at', C.c_uint), ('bytes', C.c_uint), ('quiet', C.c_uint)]


class Link(C.Structure):
    _fields_ = [(n, C.c_int) for n in ('handle', 'rssi', 'lq', 'tpl', 'afh', 'fcc', 'answered')]


class Fly(C.Structure):
    _fields_ = [('have', C.c_int), ('low', C.c_int)]


class Counts(C.Structure):
    _fields_ = [('acl_tx', C.c_uint), ('byte_tx', C.c_uint), ('err_tx', C.c_uint), ('acl_mtu', C.c_int),
                ('acl_pkts', C.c_int)]


class Sample(C.Structure):
    _fields_ = [('wall', C.c_long), ('up', C.c_uint)] + [(n, C.c_int) for n in (
        'kbps', 'acl', 'wq', 'rssi', 'lq', 'tpl', 'afh', 'err', 'wtx', 'wrx', 'cpu', 'ba', 'own', 'h', 'er', 'ew',
        'fcc', 'done', 'fly')]


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
    assert cmd(L, 0x1401, 0x000c) == bytes([1, 0x01, 0x14, 2, 0x0c, 0])        # Read Failed Contact Counter


def complete(op, params):
    body = bytes([1, op & 0xff, op >> 8]) + bytes(params)
    return bytes([4, 0x0e, len(body)]) + body


def test_the_answers_fill_the_link(L):
    k = Link()
    L.bt_link_clear(C.byref(k), 0x0c)
    afh = [0xff] * 5 + [0] * 4 + [0x80]  # channels 0-39 in use; bit 79 is not a channel
    for ev in (complete(0x1405, [0, 0x0c, 0, 0xf4]), complete(0x1403, [0, 0x0c, 0, 0xff]),
               complete(0x0c2d, [0, 0x0c, 0, 4]), complete(0x1406, [0, 0x0c, 0, 1] + afh),
               complete(0x1401, [0, 0x0c, 0, 0x2c, 0x01])):
        assert L.bt_event(C.byref(k), ev, len(ev)) == 1
    assert (k.rssi, k.lq, k.tpl, k.afh, k.fcc, k.answered) == (-12, 255, 4, 40, 300, 5)


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
        **{k: f.get(k, UNSET) for k in ('rssi', 'lq', 'tpl', 'afh', 'fcc')}, **{k: f.get(k, -1) for k in ('done', 'fly')})
    out = C.create_string_buffer(256)
    n = L.bt_line(out, 256, C.byref(s))
    assert n == len(out.value)
    return out.value.decode()


def test_a_line_has_every_field(L):
    text = line(L, kbps=612, acl=95, wq=2730, rssi=-12, lq=255, tpl=4, afh=40, err=0, wtx=3, wrx=120, cpu=23,
                ba=81, own=4, h=2, er=88, ew=3, fcc=0, done=96, fly=5)
    assert text == ('2026-11-23T20:01:01Z up=4242 kbps=612 acl=95 wq=2730 rssi=-12 lq=255 tpl=4 afh=40 err=0 wtx=3 '
                    'wrx=120 cpu=23 ba=8.1 own=0.4 h=2 er=88 ew=3 fcc=0 done=96 fly=5\n')
    assert len(text) < 170  # about 6 minutes in the 64 KB ring


def test_a_field_not_read_is_a_dash(L):
    assert line(L) == ('2026-11-23T20:01:01Z up=4242 kbps=- acl=- wq=- rssi=- lq=- tpl=- afh=- err=- wtx=- wrx=- '
                       'cpu=- ba=- own=- h=- er=- ew=- fcc=- done=- fly=-\n')


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


# ---- the controller's buffers: Number Of Completed Packets ----

def nocp(*pairs):
    body = bytes([len(pairs)]) + b''.join(bytes([h & 0xff, h >> 8, n & 0xff, n >> 8]) for h, n in pairs)
    return bytes([4, 0x13, len(body)]) + body


def test_completed_packets_for_the_link(L):
    ev = nocp((0x0c, 3), (0x0d, 9))
    assert L.bt_completed(ev, len(ev), 0x0c) == 3 and L.bt_completed(ev, len(ev), 0x0d) == 9
    assert L.bt_completed(ev, len(ev), 0x0e) == 0
    assert L.bt_completed(ev[:-2], len(ev) - 2, 0x0d) == 0  # cut short: none counted
    ev = complete(0x1405, [0, 0x0c, 0, 0xf4])
    assert L.bt_completed(ev, len(ev), 0x0c) == 0


def test_in_flight_is_sent_less_completed_from_the_least_seen(L):
    """The count at the socket's opening is not known (0 to the controller's buffers): the least difference seen is
    taken as empty, as the buffers drain whenever the stream pauses or the link keeps up."""
    f = Fly()
    assert L.bt_fly_step(C.byref(f), 1000, 0) == 0     # the first: the baseline
    assert L.bt_fly_step(C.byref(f), 1100, 96) == 4    # 100 sent, 96 done: 4 in the controller
    assert L.bt_fly_step(C.byref(f), 1190, 186) == 4   # 90 and 90
    assert L.bt_fly_step(C.byref(f), 1286, 286) == 0   # it caught up past the baseline: that is empty
    assert L.bt_fly_step(C.byref(f), 1291, 286) == 5   # five handed over, none done: full
    assert L.bt_fly_step(C.byref(f), 0x10, (0x10 - 1004) & 0xffffffff) == 4  # sent wrapped past 2^32, done not yet


def test_cpu_shares_are_written_as_percent(L):
    assert ' ba=9.0 own=0.0 ' in line(L, ba=90, own=0) and ' ba=12.3 ' in line(L, ba=123)


# ---- EDR: Change Connection Packet Type on the A2DP sink's link ----

class Edr(C.Structure):
    _fields_ = [(n, C.c_int) for n in ('sent', 'said', 'said_ptype', 'said_status')]


PX7 = bytes([0x3f, 0x59, 0xcf, 0xd1, 0x66, 0xec])  # EC:66:D1:CF:59:3F as bdaddr_t holds it, last byte first
PHONE = bytes([0x01, 0x02, 0x03, 0x04, 0x05, 0x06])


def edr(L):
    e = Edr()
    L.bt_edr_reset(C.byref(e))
    return e


def edr_step(L, e, handle):
    out = C.create_string_buffer(8)
    n = L.bt_edr_step(C.byref(e), handle, out)
    return out.raw[:n]


def edr_event(L, e, ev):
    note = C.create_string_buffer(64)
    r = L.bt_edr_event(C.byref(e), ev, len(ev), note, 64)
    return r, note.value.decode()


def ptype_changed(status, handle, ptype):
    return bytes([4, 0x1d, 5, status, handle & 0xff, handle >> 8, ptype & 0xff, ptype >> 8])


def cmd_status(status, op):
    return bytes([4, 0x0f, 4, status, 1, op & 0xff, op >> 8])


def test_the_edr_command_is_hcitool_cpt_dm1_dh1(L):
    """`hcitool cpt EC:66:D1:CF:59:3F DM1,DH1` moved the Px7's link onto EDR on 2026-11-18: Change Connection Packet
    Type (OGF 1, OCF 0x000F), DM1|DH1 with the EDR "shall not use" bits (0x3306) clear."""
    e = edr(L)
    assert edr_step(L, e, 0x000c) == bytes([1, 0x0f, 0x04, 4, 0x0c, 0x00, 0x18, 0x00])
    assert edr_step(L, edr(L), 0x0e0c)[4:6] == bytes([0x0c, 0x0e])  # 12 bits of handle
    assert 0x0018 & 0x3306 == 0 and 0x0018 != 0xcc18  # not the kernel's default mask


def test_the_edr_command_goes_once_to_each_handle(L):
    """A codec change reconnects on a new handle: that one gets it; the same handle never twice, answered or
    refused, so a controller that refuses is asked once, not each second."""
    e = edr(L)
    assert edr_step(L, e, -1) == b''                  # no sink listed: nothing
    assert len(edr_step(L, e, 1)) == 8 and e.sent == 1
    assert edr_step(L, e, 1) == b''                   # the next second's line
    r, _ = edr_event(L, e, cmd_status(0x0c, 0x040f))  # refused
    assert r and edr_step(L, e, 1) == b''
    assert edr_step(L, e, -1) == b'' and edr_step(L, e, 1) == b''  # a lookup that failed for a line is no reconnect
    assert len(edr_step(L, e, 2)) == 8 and e.sent == 2  # the codec switch's new handle
    assert len(edr_step(L, e, 1)) == 8                 # a handle number used again is a new link
    L.bt_edr_reset(C.byref(e))                         # the stream stopped: a reconnect in the pause may reuse 1
    assert len(edr_step(L, e, 1)) == 8


def test_the_sink_is_bluealsa_s_a2dp_source_device(L):
    """The headset is the device bluealsa streams to (its a2dpsrc PCM); a phone streaming to the Q2 is an a2dpsnk
    PCM, a call's is hfpag, and neither is a sink."""
    b = C.create_string_buffer(6)
    assert L.bt_pcms_sink(PCMS.replace(b'00_11_22_33_44_55', b'EC_66_D1_CF_59_3F'), b) == 1 and b.raw == PX7
    assert L.bt_pcms_sink(PCMS.split(b'a2dpsrc')[0], b) == 0       # hfpag only
    assert L.bt_pcms_sink(PCMS.replace(b'a2dpsrc', b'a2dpsnk'), b) == 0  # receiver mode: the phone is the source
    assert L.bt_pcms_sink(b'', b) == 0
    assert L.bt_pcms_sink(b'object path "/org/bluealsa/hci0/dev_EC_66_D1_ZZ_59_3F/a2dpsrc/sink"', b) == 0


def test_only_the_sink_s_classic_link_is_chosen(L):
    """The Car Remote's phone (LE, or a classic link of its own) and an LE link to the headset are never changed."""
    def conns(*cs):
        return bytes((C.c_uint16 * 2)(0, len(cs))) + b''.join(bytes(c) for c in cs)
    phone = ConnInfo(handle=0x0001, type=1)
    phone.bdaddr[:] = list(PHONE)
    le = ConnInfo(handle=0x0040, type=0x80)
    le.bdaddr[:] = list(PX7)
    sink = ConnInfo(handle=0x0002, type=1)
    sink.bdaddr[:] = list(PX7)
    assert L.bt_acl_handle_of(conns(phone, le, sink), 4, PX7) == 0x0002
    assert L.bt_acl_handle_of(conns(phone, le), 4, PX7) == -1
    assert L.bt_acl_handle_of(conns(phone, le, sink), 2, PX7) == -1  # only max read
    assert L.bt_acl_handle_of(conns(phone), 4, bytes(6)) == -1         # no sink known


def test_the_packet_type_change_is_read_and_noted(L):
    """Connection Packet Type Changed (0x1D) for the link it was sent to; a refusal comes as Command Status. The first
    outcome goes to the unified log too (2), the same again only to q2-bt (1)."""
    e = edr(L)
    edr_step(L, e, 2)
    assert edr_event(L, e, ptype_changed(0, 3, 0x0018)) == (0, '')          # another link's
    assert edr_event(L, e, cmd_status(0, 0x040f)) == (0, '')                # accepted: the change follows
    assert edr_event(L, e, cmd_status(0x0c, 0x0405)) == (0, '')             # another command's
    assert edr_event(L, e, complete(0x1405, [0, 2, 0, 0xf4])) == (0, '')
    assert edr_event(L, e, ptype_changed(0, 2, 0x0018)[:-1]) == (0, '')     # cut short
    assert edr_event(L, e, ptype_changed(0, 2, 0x0018)) == (2, 'LINK ptype=0x0018 status=0x00 h=2')
    edr_step(L, e, 3)
    assert edr_event(L, e, ptype_changed(0, 3, 0x0018)) == (1, 'LINK ptype=0x0018 status=0x00 h=3')
    edr_step(L, e, 4)
    assert edr_event(L, e, cmd_status(0x0c, 0x040f)) == (2, 'LINK ptype=- status=0x0c h=4')
    edr_step(L, e, 5)
    assert edr_event(L, e, ptype_changed(0x24, 5, 0xcc18)) == (2, 'LINK ptype=0xcc18 status=0x24 h=5')
    assert edr_event(L, edr(L), ptype_changed(0, 2, 0x0018)) == (0, '')     # nothing sent: not ours


# ---- the step-down: aptX HD to aptX to SBC under LDAC Auto when the radio is the limit ----

class Step(C.Structure):
    _fields_ = [('top', C.c_int), ('stall', C.c_int), ('clean', C.c_int), ('have', C.c_int), ('last', C.c_uint)]


SBC, APTX, APTX_HD = 1, 2, 3
BUFS = 8


def step(L, s, t, allowed, rank, wq, fly, bufs=BUFS):
    return L.bt_step(C.byref(s), t * 1000, allowed, rank, wq, fly, bufs)


def test_codec_ranks_and_names(L):
    assert [L.bt_codec_rank(n) for n in (b'aptX-HD', b'aptX', b'SBC', b'LDAC', b'AAC', b'')] == [3, 2, 1, 0, 0, 0]
    assert [C.string_at(L.bt_codec_name(r)) for r in (1, 2, 3)] == [b'SBC', b'aptX', b'aptX-HD']


def test_two_seconds_queued_with_the_buffers_full_steps_down(L):
    s = Step()
    assert step(L, s, 0, 1, APTX_HD, 0, 3) == 0
    assert step(L, s, 1, 1, APTX_HD, 5000, BUFS) == 0       # one second: not yet
    assert step(L, s, 2, 1, APTX_HD, 5000, BUFS) == APTX    # two in a row: aptX
    for t in range(3, 12):                                  # the switch's 10 s gap: stalls not counted
        assert step(L, s, t, 1, APTX, 9000, BUFS) == 0
    assert step(L, s, 12, 1, APTX, 9000, BUFS) == 0
    assert step(L, s, 13, 1, APTX, 9000, BUFS) == SBC       # aptX stalls too
    assert step(L, s, 30, 1, SBC, 9000, BUFS) == 0          # nothing below SBC


def test_a_queue_without_full_buffers_or_a_small_one_is_not_the_radio(L):
    s = Step()
    for t in range(10):
        assert step(L, s, t, 1, APTX_HD, 5000, BUFS - 1) == 0   # the encoder or the source, not the link
    for t in range(10, 20):
        assert step(L, s, t, 1, APTX_HD, 4096, BUFS) == 0       # 4 kB is not over
    for t in range(20, 30, 2):                                  # never two in a row
        assert step(L, s, t, 1, APTX_HD, 9000, BUFS) == 0 and step(L, s, t + 1, 1, APTX_HD, 0, 2) == 0
    assert step(L, s, 40, 1, APTX_HD, 9000, -1) == 0 and step(L, s, 41, 1, APTX_HD, 9000, -1) == 0  # fly not read


def test_back_up_after_a_clean_minute_at_most_every_three_minutes(L):
    s = Step()
    step(L, s, 0, 1, APTX_HD, 0, 0)
    step(L, s, 1, 1, APTX_HD, 9000, BUFS)
    assert step(L, s, 2, 1, APTX_HD, 9000, BUFS) == APTX
    ups = [t for t in range(3, 360) if step(L, s, t, 1, APTX, 0, 2) == APTX_HD]
    assert ups == [182]                                    # clean long since, but 3 minutes after the switch
    s2 = Step()
    step(L, s2, 0, 1, APTX_HD, 0, 0)
    step(L, s2, 1, 1, APTX_HD, 9000, BUFS)
    step(L, s2, 2, 1, APTX_HD, 9000, BUFS)
    for t in range(3, 300):                                # one queued second each 50 s: never a clean minute
        assert step(L, s2, t, 1, APTX, 9000 if t % 50 == 0 else 0, 2) == 0


def test_never_above_where_it_started(L):
    s = Step()
    for t in range(400):
        assert step(L, s, t, 1, APTX, 0, 1) == 0            # the headset chose aptX: left there


def test_only_under_ldac_auto_with_the_setting_on(L):
    s = Step()
    for t in range(10):
        assert step(L, s, t, 0, APTX_HD, 9000, BUFS) == 0   # another choice, or the setting off: never
    s = Step()
    step(L, s, 0, 1, APTX_HD, 0, 0)
    step(L, s, 1, 1, APTX_HD, 9000, BUFS)
    assert step(L, s, 2, 1, APTX_HD, 9000, BUFS) == APTX
    assert step(L, s, 20, 0, APTX, 0, 1) == APTX_HD         # turned off while stepped down: back at once
    assert step(L, s, 21, 0, APTX, 0, 1) == 0               # asked once


def test_ldac_aac_or_no_codec_is_left_alone(L):
    s = Step()
    for t in range(10):
        assert step(L, s, t, 1, 0, 9000, BUFS) == 0
