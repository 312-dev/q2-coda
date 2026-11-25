"""q2bt's core (patch/q2bt_core.c, docs/internals.md#bluetooth-link-monitor): when a stream counts as running, the
kernel's hci_dev_info and connection list as laid out, the four HCI commands and their answers, the A2DP socket's
queue from /proc/net/l2cap, the codec from bluealsa's GetPCMs, the log line and its rotation, the EDR
packet-type command to the A2DP sink's link, the step-down from aptX HD or aptX to SBC (bt_step, the config it obeys,
the PCM path SelectCodec goes to, and the commute replayed through it), SBC's bitpool and AAC's peak. Built for this machine
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
    lib.bt_acl_handle.argtypes = [C.c_char_p, C.c_int, C.c_void_p]
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
    lib.bt_down_event.argtypes = [C.c_char_p, C.c_int, C.c_int, C.c_char_p, C.c_void_p, C.c_int]
    lib.bt_pcms_sink.argtypes = [C.c_char_p, C.c_void_p]
    lib.bt_acl_handle_of.argtypes = [C.c_char_p, C.c_int, C.c_char_p]
    lib.bt_step.argtypes = [C.c_void_p, C.c_uint, C.c_int, C.c_int, C.c_void_p]
    lib.bt_codec_kbps.argtypes = [C.c_int, C.c_uint, C.c_int]
    lib.bt_codec_rank.argtypes = [C.c_char_p]
    lib.bt_codec_name.restype = C.c_void_p
    lib.bt_step_allowed.argtypes = [C.c_char_p]
    lib.bt_pcms_path.argtypes = [C.c_char_p, C.c_char_p, C.c_int]
    lib.log_named.restype = C.c_void_p
    lib.log_named.argtypes = [C.c_char_p, C.c_char_p, C.c_uint]
    lib.heat_add.argtypes = [C.c_void_p, C.c_char_p]
    lib.bt_flush.argtypes = [C.c_void_p, C.c_char_p]
    lib.bt_sbc_step.argtypes = [C.c_void_p, C.c_uint, C.c_int, C.c_int, C.c_int, C.c_int, C.c_int, C.c_int]
    lib.bt_sbc_next.argtypes = [C.c_int, C.c_int, C.c_int]
    lib.bt_sbc_allowed.argtypes = [C.c_char_p]
    lib.bt_aac_step.argtypes = [C.c_void_p, C.c_uint, C.c_int, C.c_int, C.c_int, C.c_int, C.c_int, C.c_int]
    lib.bt_aac_next.argtypes = [C.c_int, C.c_int, C.c_int]
    lib.bt_aac_allowed.argtypes = [C.c_char_p]
    lib.flush_every.argtypes = [C.c_int, C.c_int]
    lib.bt_proc_io.argtypes = [C.c_char_p, C.POINTER(C.c_uint), C.POINTER(C.c_uint), C.POINTER(C.c_uint)]
    lib.bt_disk_busy.argtypes = [C.c_char_p, C.c_char_p, C.POINTER(C.c_uint)]
    lib.bt_song_path.argtypes = [C.c_char_p]
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
        'fcc', 'done', 'fly', 'bp', 'pl', 'pr', 'pd', 'pw', 'dk', 'gw')]


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
    acl.bdaddr[:] = list(range(1, 7))
    addr = C.create_string_buffer(6)
    assert L.bt_acl_handle(bytes(req) + bytes(sco) + bytes(acl), 4, None) == 0x000c
    assert L.bt_acl_handle(bytes(req) + bytes(sco) + bytes(acl), 4, addr) == 0x000c
    assert addr.raw == bytes(range(1, 7))  # the ACL link's address, not the SCO link's before it
    assert L.bt_acl_handle(bytes((C.c_uint16 * 2)(0, 1)) + bytes(sco), 4, None) == -1
    assert L.bt_acl_handle(bytes((C.c_uint16 * 2)(0, 0)), 4, None) == -1


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
    assert pcms(L, PCMS) == (1, 'aptX-HD', 48000, 2357)  # the Px7 on Auto, 2026-11-17


def test_no_a2dp_pcm_or_no_codec_is_none(L):
    assert pcms(L, PCMS.split(b'a2dpsrc')[0]) == (0, '', 0, 0)
    assert pcms(L, b'')[0] == 0
    no_codec = PCMS.replace(b'string "Codec"', b'string "Other"')
    assert pcms(L, no_codec)[:3] == (0, '', 48000)


# ---- the line ----

def line(L, **f):
    s = Sample(wall=1795464061, up=4242, **{k: f.get(k, -1) for k in (
        'kbps', 'acl', 'wq', 'err', 'wtx', 'wrx', 'cpu', 'ba', 'own', 'h', 'er', 'ew')},
        **{k: f.get(k, UNSET) for k in ('rssi', 'lq', 'tpl', 'afh', 'fcc')}, **{k: f.get(k, -1) for k in ('done', 'fly', 'bp', 'pl', 'pr', 'pd', 'pw', 'dk', 'gw')})
    out = C.create_string_buffer(256)
    n = L.bt_line(out, 256, C.byref(s))
    assert n == len(out.value)
    return out.value.decode()


def test_a_line_has_every_field(L):
    text = line(L, kbps=612, acl=95, wq=2730, rssi=-12, lq=255, tpl=4, afh=40, err=0, wtx=3, wrx=120, cpu=23,
                ba=81, own=4, h=2, er=88, ew=3, fcc=0, done=96, fly=5, bp=29, pl=25, pr=176, pd=0, pw=172, dk=12, gw=0)
    assert text == ('2026-11-23T20:01:01Z up=4242 kbps=612 acl=95 wq=2730 rssi=-12 lq=255 tpl=4 afh=40 err=0 wtx=3 '
                    'wrx=120 cpu=23 ba=8.1 own=0.4 h=2 er=88 ew=3 fcc=0 done=96 fly=5 bp=29 pl=2.5 pr=176 pd=0 pw=172 '
                    'dk=12 gw=0\n')
    assert len(text) < 210  # about 5 minutes in the 64 KB ring


def test_a_field_not_read_is_a_dash(L):
    assert line(L) == ('2026-11-23T20:01:01Z up=4242 kbps=- acl=- wq=- rssi=- lq=- tpl=- afh=- err=- wtx=- wrx=- '
                       'cpu=- ba=- own=- h=- er=- ew=- fcc=- done=- fly=- bp=- pl=- pr=- pd=- pw=- dk=- gw=-\n')


def test_rates_and_shares(L):
    assert L.bt_rate(72000, 1000) == 72000 and L.bt_rate(72000, 1250) == 57600 and L.bt_rate(5, 0) == 0
    assert L.bt_rate(100_000_000, 5000) == 20_000_000  # past 32 bits times 1000
    assert L.bt_rate(0xfffffff0, 1000) == 2_000_000_000  # held, not wrapped negative
    assert L.bt_ticks_pct(1, 1000) == 10 and L.bt_ticks_pct(100, 1000) == 1000  # 1 tick of 100: 1.0%
    assert L.bt_ticks_pct(0, 1000) == 0 and L.bt_ticks_pct(3, 0) == 0


def test_the_log_rotates_at_a_megabyte_through_four_files(L, tmp_path):
    """q2-bt.0 moves on at 1 MB, .2 to .3 and .1 to .2 first (the old .3 goes): right after a move the card still
    holds 3 MB, about six hours of lines at 1 Hz, and never more than 4 MB and a flush."""
    d = tmp_path / 'logs'
    log = L.log_named(str(d).encode(), b'q2-bt.0', 65536)
    d.mkdir()
    for k in range(4):
        (d / f'q2-bt.{k}').write_bytes(bytes([0x30 + k]) * 1048576)
    L.heat_add(log, b'2026-11-23T04:01:01Z START h=12\n')
    L.card(1)  # the writing child's flush: the card is up
    try:
        assert L.bt_flush(log, str(d).encode()) == 0
    finally:
        L.card(0)
    assert [(d / f'q2-bt.{k}').read_bytes()[:1] for k in (1, 2, 3)] == [b'0', b'1', b'2']
    assert (d / 'q2-bt.0').read_bytes() == b'2026-11-23T04:01:01Z START h=12\n'
    assert sorted(f.name for f in d.iterdir()) == ['q2-bt.0', 'q2-bt.1', 'q2-bt.2', 'q2-bt.3']


def test_a_log_under_a_megabyte_is_appended_to(L, tmp_path):
    d = tmp_path / 'logs'
    log = L.log_named(str(d).encode(), b'q2-bt.0', 65536)
    d.mkdir()
    (d / 'q2-bt.0').write_bytes(b'x' * 1048575)
    L.heat_add(log, b'line\n')
    L.card(1)
    try:
        assert L.bt_flush(log, str(d).encode()) == 0
    finally:
        L.card(0)
    assert (d / 'q2-bt.0').stat().st_size == 1048580 and not (d / 'q2-bt.1').exists()


def test_a_streams_lines_reach_the_card_within_half_a_minute(L):
    """A sudden power loss loses what is in the RAM ring: at most FLUSH's period while a stream runs or the Q2 is
    warm or busy (q2guard.c), so under a minute of a walk; five minutes of the 30 s heat lines when idle."""
    assert L.flush_every(0, 1) == 30000 and L.flush_every(1, 0) == 30000 and L.flush_every(1, 1) == 30000
    assert L.flush_every(0, 0) == 300000


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


# ---- the step-down: aptX HD or aptX straight to SBC under Auto when the radio delivers short ----

class Step(C.Structure):
    _fields_ = [('top', C.c_int), ('stall', C.c_int), ('clean', C.c_int), ('have', C.c_int), ('last', C.c_uint),
                ('up_ms', C.c_uint), ('raised', C.c_int), ('raised_at', C.c_uint)]


class StepLine(C.Structure):
    _fields_ = [(n, C.c_int) for n in ('kbps', 'acl', 'done', 'wq', 'fly', 'bufs', 'afh', 'own', 'lowered')] + [
        ('hz', C.c_uint)]


SBC, APTX, APTX_HD = 1, 2, 3
BUFS = 8
RATE = {APTX_HD: 614, APTX: 412, SBC: 259}  # bt_codec_kbps at 48 kHz, SBC at bitpool 33


def step_line(rank, kbps=None, full=False, wq=None, afh=79, lowered=0, own=33, fly=None, hz=48000):
    """A line as q2bt.c hands it over: kbps delivered (all handed packets done), the codec's own rate when not
    given; full, every ACL buffer held with the socket queued."""
    kbps = RATE.get(rank, 0) if kbps is None else kbps
    return StepLine(kbps=kbps, acl=100, done=100, wq=(9000 if full else 0) if wq is None else wq,
                    fly=(BUFS if full else 2) if fly is None else fly, bufs=BUFS, afh=afh, own=own, lowered=lowered,
                    hz=hz)


def step(L, s, t, allowed, rank, **line):
    return L.bt_step(C.byref(s), int(t * 1000), allowed, rank, C.byref(step_line(rank, **line)))


def starve(L, s, t, rank=APTX_HD):
    """Two seconds delivering a third of the codec's rate with every buffer held, from t: the step's answer."""
    step(L, s, t, 1, rank, kbps=RATE[rank] // 3, full=True)
    return step(L, s, t + 1, 1, rank, kbps=RATE[rank] // 3, full=True)


def test_codec_ranks_names_and_rates(L):
    assert [L.bt_codec_rank(n) for n in (b'aptX-HD', b'aptX', b'SBC', b'LDAC', b'AAC', b'')] == [3, 2, 1, 0, 0, 0]
    assert [C.string_at(L.bt_codec_name(r)) for r in (1, 2, 3)] == [b'SBC', b'aptX', b'aptX-HD']
    # on the link at 48 kHz, as logged; SBC by its frame (13 + 2 x bitpool bytes), 33 when not known
    assert [L.bt_codec_kbps(r, 48000, 33) for r in (3, 2, 1)] == [614, 412, 259]
    assert [L.bt_codec_kbps(1, 48000, b) for b in (29, 19, 0)] == [233, 167, 259]
    assert L.bt_codec_kbps(3, 44100, 0) == 564 and L.bt_codec_kbps(3, 0, 0) == 614  # 0 Hz: not known, 48 kHz
    assert L.bt_codec_kbps(0, 48000, 0) == 0


@pytest.mark.parametrize('rank', [APTX_HD, APTX])
def test_two_starved_seconds_step_straight_to_sbc(L, rank):
    """No aptX stop: where aptX HD fell short on the commute aptX did too (docs/internals.md, the commute
    replayed)."""
    s = Step()
    assert step(L, s, 0, 1, rank) == 0
    assert step(L, s, 1, 1, rank, kbps=RATE[rank] * 85 // 100, full=True) == 0   # one second: not yet
    assert step(L, s, 2, 1, rank, kbps=RATE[rank] * 85 // 100, full=True) == SBC  # two in a row: SBC
    for t in range(3, 40):
        assert step(L, s, t, 1, SBC, kbps=50, full=True) == 0                     # nothing below SBC


def test_a_backlog_with_the_rate_whole_is_not_a_shortfall(L):
    """The trigger as first built fired on any backlog with the buffers full; on the home walk that was 224 s of
    746 with the rate whole in all but 11."""
    s = Step()
    for t in range(10):
        assert step(L, s, t, 1, APTX_HD, kbps=560, full=True) == 0               # over 90% of 614: delivered
    for t in range(10, 20):
        assert step(L, s, t, 1, APTX_HD, kbps=200, wq=9000, fly=BUFS - 1) == 0   # a buffer free: not the radio
    for t in range(20, 30):
        assert step(L, s, t, 1, APTX_HD, kbps=200, wq=0, fly=BUFS) == 0         # nothing waiting
    for t in range(30, 40, 2):                                                  # never two in a row
        assert step(L, s, t, 1, APTX_HD, kbps=200, full=True) == 0 and step(L, s, t + 1, 1, APTX_HD) == 0
    for t in range(40, 50):
        assert step(L, s, t, 1, APTX_HD, kbps=200, full=True, fly=-1) == 0      # fly not read


def test_back_up_after_a_clean_minute_and_three_minutes_since_the_switch(L):
    s = Step()
    assert starve(L, s, 1) == SBC
    up = next(t for t in range(3, 400) if step(L, s, t, 1, SBC) == APTX_HD)
    assert up == 182                                        # clean long since, but 3 minutes after the switch


@pytest.mark.parametrize('spoil, up', [
    (dict(afh=59), 150 + 60),                               # afh under 60: the band has not cleared
    (dict(afh=-1), 150 + 60),                               # afh not read
    (dict(kbps=200, full=True), 150 + 60),                  # SBC itself short
    (dict(lowered=1), 150 + 60),                            # the bitpool below its own
])
def test_back_up_needs_every_second_of_the_minute_clean(L, spoil, up):
    s = Step()
    assert starve(L, s, 1) == SBC
    assert next(t for t in range(3, 400) if step(L, s, t, 1, SBC, **(spoil if t == 150 else {})) == APTX_HD) == up


def test_the_bitpool_climbs_back_before_the_codec(L):
    s = Step()
    assert starve(L, s, 1) == SBC
    assert next(t for t in range(3, 400) if step(L, s, t, 1, SBC, lowered=int(t < 300)) == APTX_HD) == 359


def test_a_quiet_second_counts_neither_way(L):
    """Nothing handed and no buffer held (the player between songs, a switch's gap): no evidence either way."""
    s = Step()
    step(L, s, 0, 1, APTX_HD, kbps=200, full=True)
    assert step(L, s, 1, 1, APTX_HD, kbps=0) == 0
    assert step(L, s, 2, 1, APTX_HD, kbps=200, full=True) == SBC  # the quiet second did not break the two
    up = next(t for t in range(3, 400) if step(L, s, t, 1, SBC, kbps=0 if 100 <= t < 130 else None) == APTX_HD)
    assert up == 182                                              # nor the clean minute


def up_after(L, s, down):
    """Seconds from the switch down at down to the step back up, the link clean meanwhile."""
    return next(u for u in range(down + 1, down + 5000) if step(L, s, u, 1, SBC) == APTX_HD) - down


def test_a_step_up_undone_within_five_minutes_doubles_the_wait_to_24_minutes(L):
    s = Step()
    assert starve(L, s, 1) == SBC
    down, waits = 2, []
    for _ in range(6):
        waits.append(up_after(L, s, down))
        up = down + waits[-1]
        assert starve(L, s, up + 99) == SBC                 # undone 100 s after it
        down = up + 100
    assert waits == [180, 360, 720, 1440, 1440, 1440]


def test_a_step_up_that_held_five_minutes_keeps_the_wait(L):
    s = Step()
    assert starve(L, s, 1) == SBC
    assert up_after(L, s, 2) == 180
    for u in range(183, 482):                               # 182 up, held to 482
        assert step(L, s, u, 1, APTX_HD) == 0
    assert starve(L, s, 481) == SBC
    assert up_after(L, s, 482) == 180


def test_never_above_where_it_started(L):
    s = Step()
    for t in range(400):
        assert step(L, s, t, 1, APTX) == 0                  # the headset chose aptX: left there
    assert starve(L, s, 400, APTX) == SBC
    assert next((t, r) for t in range(402, 800) if (r := step(L, s, t, 1, SBC))) == (581, APTX)  # not aptX HD


def test_only_under_ldac_auto_with_the_setting_on(L):
    for allowed in (0, -1):                                 # the setting off, or another choice: never
        s = Step()
        for t in range(10):
            assert step(L, s, t, allowed, APTX_HD, kbps=100, full=True) == 0
    s = Step()
    assert starve(L, s, 1) == SBC
    assert step(L, s, 20, 0, SBC) == APTX_HD                # turned off while stepped down: back at once
    assert step(L, s, 21, 0, SBC) == 0                      # asked once


def test_ldac_aac_or_no_codec_is_left_alone(L):
    s = Step()
    for t in range(10):
        assert step(L, s, t, 1, 0, kbps=10, full=True) == 0


def test_another_choice_while_stepped_down_is_left_alone(L):
    """Quality changed from Auto to a fixed choice: stock selects that codec, and nothing is put back over it;
    Auto chosen again starts from what it streams then."""
    s = Step()
    assert starve(L, s, 1) == SBC
    for t in range(20, 30):
        assert step(L, s, t, -1, SBC) == 0
    for t in range(300, 700):
        assert step(L, s, t, 1, SBC) == 0                   # SBC is where Auto now started


INI = b"""[SYSSET]\r\nLANG=1\r\nBTCODING=2\r\n[PLAYSET]\r\nBTSTEPDOWN=1\r\n[Q2POD]\r\nBTNAME=Q2\r\n%s"""


@pytest.mark.parametrize('ini, allowed', [
    (INI % b'', 0),                                          # Auto, the switch never touched: off
    (INI % b'BTSTEPDOWN=1\r\n', 1),                          # turned on by hand
    (INI % b'BTSTEPDOWN=0\r\n', 0),                          # off; PLAYSET's key of that name is not it
    (INI % b'BTSTEPDOWN=\r\n', 0),
    (INI % b'BTSTEPDOWN=10\r\n', 0),
    (INI.replace(b'BTCODING=2', b'BTCODING=3') % b'BTSTEPDOWN=1\r\n', -1),   # aptX HD chosen
    (INI.replace(b'BTCODING=2', b'BTCODING=21') % b'', -1),
    (INI.replace(b'BTCODING=2', b'BTCODINGX=2') % b'', -1),
    (b'', -1),                                               # no config.ini read
])
def test_allowed_is_ldac_auto_and_the_setting(L, ini, allowed):
    assert L.bt_step_allowed(ini) == allowed


def test_the_a2dp_source_pcm_s_path_for_select_codec(L):
    out = C.create_string_buffer(96)
    assert L.bt_pcms_path(PCMS, out, 96) == 1
    assert out.value == b'/org/bluealsa/hci0/dev_00_11_22_33_44_55/a2dpsrc/sink'
    assert L.bt_pcms_path(PCMS, out, 40) == 0                               # no room
    assert L.bt_pcms_path(PCMS.split(b'a2dpsrc')[0], out, 96) == 0          # no A2DP source
    assert L.bt_pcms_path(PCMS.replace(b'dev_00', b"dev_0'"), out, 96) == 0  # never a quote to the shell


def test_a_codec_not_known_yet_keeps_where_it_started(L):
    """A stream's START asks bluealsa for the codec again: until it answers, the rank is 0, and a stream that comes
    back from a pause on SBC still climbs back to the aptX HD it started on."""
    s = Step()
    assert starve(L, s, 1) == SBC
    assert step(L, s, 100, 1, 0) == 0                       # paused, resumed: not known yet
    assert next(t for t in range(101, 400) if step(L, s, t, 1, SBC) == APTX_HD) == 182


# ---- SBC's bitpool: lowered frame to frame while the radio is the limit (bt_sbc_step) ----

class Sbc(C.Structure):
    _fields_ = [('rung', C.c_int), ('stall', C.c_int), ('clean', C.c_int), ('have', C.c_int), ('last', C.c_uint),
                ('up_ms', C.c_uint), ('raised', C.c_int), ('raised_at', C.c_uint)]


NONE = -1  # bt_sbc_step's "no change"


def sbc(L, s, t, wq, fly, own=33, allowed=1, rank=SBC, bufs=BUFS):
    return L.bt_sbc_step(C.byref(s), int(t * 1000), allowed, rank, own, wq, fly, bufs)


def test_the_bitpool_ladder_below_the_streams_own(L):
    """Down from where it is to the next lower rung, never at or above the thread's own; up a rung, and 0 (the
    thread's own) once the next rung would reach it."""
    down = [L.bt_sbc_next(p, own, -1) for own, p in ((53, 0), (53, 45), (53, 35), (53, 29), (53, 23), (53, 19))]
    assert down == [45, 35, 29, 23, 19, -1]
    assert [L.bt_sbc_next(p, 33, -1) for p in (0, 29, 23, 19)] == [29, 23, 19, -1]
    assert [L.bt_sbc_next(p, 33, 1) for p in (19, 23, 29, 0)] == [23, 29, 0, -1]
    assert [L.bt_sbc_next(p, 51, 1) for p in (19, 35, 45)] == [23, 45, 0]
    assert L.bt_sbc_next(0, 19, -1) == -1 and L.bt_sbc_next(0, 21, -1) == 19   # a low own: the floor only
    assert L.bt_sbc_next(0, 0, -1) == -1                                         # own not known: nothing


def test_sbc_steps_its_bitpool_down_while_queued_with_the_buffers_full(L):
    s = Sbc()
    assert sbc(L, s, 0, 0, 3) == NONE
    assert sbc(L, s, 1, 6080, BUFS) == NONE                 # one second: not yet
    assert sbc(L, s, 2, 6080, BUFS) == 29                   # two in a row: a rung down, no reconnect
    assert sbc(L, s, 3, 9000, BUFS) == NONE                 # 2 s to drain at the new rate before counting again
    assert sbc(L, s, 4, 9000, BUFS) == NONE
    assert sbc(L, s, 5, 9000, BUFS) == NONE
    assert sbc(L, s, 6, 9000, BUFS) == 23
    assert [sbc(L, s, t, 9000, BUFS) for t in range(7, 11)] == [NONE, NONE, NONE, 19]
    assert all(sbc(L, s, t, 9000, BUFS) == NONE for t in range(11, 60))   # the floor


def test_a_queue_without_full_buffers_is_not_the_radio_for_sbc_either(L):
    s = Sbc()
    assert all(sbc(L, s, t, 9000, BUFS - 1) == NONE for t in range(10))     # the encoder or the player
    assert all(sbc(L, s, t, 4096, BUFS) == NONE for t in range(10, 20))     # 4 kB is not over
    assert all(sbc(L, s, t, 9000, -1) == NONE for t in range(20, 30))       # fly not read


def test_sbc_climbs_back_a_rung_after_30_clean_seconds(L):
    s = Sbc()
    sbc(L, s, 0, 6080, BUFS)
    assert sbc(L, s, 1, 6080, BUFS) == 29
    ups = [(t, r) for t in range(2, 400) if (r := sbc(L, s, t, 0, 2)) != NONE]
    # clean from t=4 (the 2 s settle first), but no sooner than a minute after the last change: then each 60 s
    assert ups == [(61, 0)]
    s = Sbc()
    for t, wq in ((0, 6080), (1, 6080), (4, 6080), (5, 6080), (8, 6080), (9, 6080)):
        sbc(L, s, t, wq, BUFS)
    assert s.rung == 19
    ups = [(t, r) for t in range(10, 400) if (r := sbc(L, s, t, 0, 2)) != NONE]
    assert ups == [(69, 23), (129, 29), (189, 0)]


def test_a_rung_up_that_stalls_again_doubles_the_wait(L):
    """A link that carries 29 but not 33 would otherwise go up and down every minute."""
    s = Sbc()
    sbc(L, s, 0, 6080, BUFS)
    assert sbc(L, s, 1, 6080, BUFS) == 29
    assert next(t for t in range(2, 200) if sbc(L, s, t, 0, 2) != NONE) == 61     # up to 33 (0)
    sbc(L, s, 70, 6080, BUFS)
    assert sbc(L, s, 71, 6080, BUFS) == 29                                        # down again 10 s on
    assert next(t for t in range(72, 400) if sbc(L, s, t, 0, 2) != NONE) == 191   # two minutes now
    s.up_ms = 400000
    assert sbc(L, s, 194, 6080, BUFS) == NONE and sbc(L, s, 195, 6080, BUFS) == 29
    assert next(t for t in range(196, 900) if sbc(L, s, t, 0, 2) != NONE) == 195 + 480   # held at 8 minutes


def test_another_codec_or_the_setting_off_puts_the_bitpool_back(L):
    s = Sbc()
    sbc(L, s, 0, 6080, BUFS)
    assert sbc(L, s, 1, 6080, BUFS) == 29
    assert sbc(L, s, 5, 0, 2, allowed=0) == 0               # turned off: the thread's own at once
    assert sbc(L, s, 6, 0, 2, allowed=0) == NONE            # once
    assert all(sbc(L, s, t, 9000, BUFS, allowed=0) == NONE for t in range(7, 20))
    s = Sbc()
    sbc(L, s, 0, 6080, BUFS)
    sbc(L, s, 1, 6080, BUFS)
    assert sbc(L, s, 9, 0, 2, rank=APTX) == 0               # stepped up to aptX: a later SBC stream starts full
    assert all(sbc(L, s, t, 9000, BUFS, rank=APTX) == NONE for t in range(10, 20))   # aptX's is bt_step's
    assert all(sbc(L, s, t, 9000, BUFS, allowed=-1) == NONE for t in range(20, 30))  # another Quality choice


def test_an_own_bitpool_not_known_steps_nothing(L):
    s = Sbc()
    assert all(sbc(L, s, t, 9000, BUFS, own=0) == NONE for t in range(10))   # bluealsa unpatched or not read


INI_SBC = b"""[SYSSET]\r\nBTCODING=%s\r\n[Q2POD]\r\n%s"""


@pytest.mark.parametrize('coding, key, allowed', [
    (b'2', b'', 0),                      # Auto, the switch never touched: off
    (b'2', b'BTSTEPDOWN=1\r\n', 1),      # turned on
    (b'6', b'', 0),
    (b'6', b'BTSTEPDOWN=1\r\n', 1),      # SBC HQ chosen by hand
    (b'7', b'BTSTEPDOWN=1\r\n', 1),      # SBC
    (b'7', b'BTSTEPDOWN=0\r\n', 0),      # the switch off
    (b'2', b'BTSTEPDOWN=0\r\n', 0),
    (b'3', b'', -1),                     # aptX HD chosen: not SBC's to change
    (b'5', b'', -1),                     # AAC
    (b'0', b'', -1),                     # LDAC Best
    (b'70', b'', -1),
])
def test_sbc_is_adapted_under_ldac_auto_or_sbc_only_with_the_switch_on(L, coding, key, allowed):
    assert L.bt_sbc_allowed(INI_SBC % (coding, key)) == allowed
    assert L.bt_sbc_allowed(b'') == -1


def test_the_bitpool_byte_is_where_the_build_puts_it(L):
    """q2bt.c writes bluealsa's byte at BT_SBC_POOL through /proc/PID/mem; tools/build.py links bt_silence.S's
    sbc_pool there (BT_SBC_POOL), and the build checks its own side."""
    import re
    import sys
    sys.path.insert(0, str(ROOT / 'tools'))
    import build
    header = (ROOT / 'patch/q2bt.h').read_text()
    m = re.search(r'#define BT_SBC_POOL (0x[0-9a-f]+)', header)
    assert m and int(m[1], 16) == build.BT_SBC_POOL


def commute(L):
    """The 2026-11-18 commute (test/fixtures/q2bt-commute-20261118.log): SBC at 33 with the AFH map down to 20
    channels, the controller's 6 buffers full and the socket queued. Each line fed to bt_sbc_step as q2bt.c would."""
    import re
    s, steps, pool = Sbc(), [], 0
    for text in (ROOT / 'test/fixtures/q2bt-commute-20261118.log').read_text().splitlines():
        m = re.match(r'\S+T(\d\d):(\d\d):(\d\d)Z up=\d+ .*\bwq=(\d+) .*\bh=(\S+) .*\bfly=(\S+)', text)
        if not m or m[5] != '4':  # the SBC link, before the drop
            continue
        t = int(m[1]) * 3600 + int(m[2]) * 60 + int(m[3])
        r = sbc(L, s, t, int(m[4]), -1 if m[6] == '-' else int(m[6]), own=33, bufs=6)
        if r != NONE:
            steps.append((f'{m[1]}:{m[2]}:{m[3]}', pool or 33, r or 33))
            pool = r
    return steps


def test_the_commute_replayed_steps_sbc_down_to_the_floor(L):
    steps = commute(L)
    assert steps[:3] == [('14:30:40', 33, 29), ('14:30:44', 29, 23), ('14:30:50', 23, 19)]
    assert all(to <= 19 for _, _, to in steps[3:])    # held at the floor through the interference


# ---- the commute replayed through bt_step and bt_sbc_step (docs/internals.md, "The commute replayed") ----

ENCODERS = {'ba-a2dp-aptx-hd': 'aptX-HD', 'ba-a2dp-aptx': 'aptX', 'ba-a2dp-sbc': 'SBC'}
LOGGED = {'aptX-HD': 614, 'aptX': 412, 'SBC': 259}  # each logged codec's rate on the link
SBC_KBPS = {33: 259, 29: 234, 23: 195, 19: 170}     # SBC by its bitpool, as logged
GAP = 2                                             # a switch's silence, s: no Q2 has measured it


def capacity(first, last, bound):
    """The seconds of the logs (test/fixtures/q2bt-replay-20261118.log.gz and the commute's) from first to last
    (hh:mm:ss) as (capacity in kbit/s, afh). A second's capacity is what the controller delivered while it held
    every ACL buffer with the socket queued; otherwise at least the logged codec's rate ('lo') or unbounded ('hi'),
    as what a higher codec would have got there is not in the log. A second with little handed and no buffer held
    (a switch's own gap, a pause) says nothing, and is left out."""
    import gzip
    import re
    text = gzip.decompress((ROOT / 'test/fixtures/q2bt-replay-20261118.log.gz').read_bytes()).decode()
    text += (ROOT / 'test/fixtures/q2bt-commute-20261118.log').read_text()
    out: list[tuple[float, int]] = []
    codec, bufs = 'SBC', 0
    for line in text.splitlines():
        stamp, _, rest = line.partition(' ')
        if rest.startswith('START'):
            m = re.search(r'bufs=(\d+)', rest)
            bufs = int(m[1]) if m else 0
        elif rest.startswith(('CODEC', 'ENCODER')):
            word = rest.split()[1]
            codec = ENCODERS.get(word, codec) if rest.startswith('ENCODER') else word
        if not rest.startswith('up=') or not first <= stamp[11:19] <= last:
            continue
        f = {k: (-1.0 if v == '-' else float(v)) for k, v in (kv.split('=', 1) for kv in rest.split())}
        kbps, acl, done, fly, wq = (f.get(k, -1.0) for k in ('kbps', 'acl', 'done', 'fly', 'wq'))
        if kbps < 0 or acl < 0:
            continue
        held = fly >= 0 and fly >= bufs and wq > 0
        if kbps < 50 and not held:
            continue
        if held:
            cap = kbps * done / acl if acl and done > 0 else 0.0
        else:
            cap = LOGGED[codec] if bound == 'lo' else 1e9
        out.append((cap, int(f.get('afh', -1.0))))
    return out


def replay(L, seconds):
    """Auto on a headset whose best is aptX HD (the Px7), the switch on, over seconds: each fed to bt_sbc_step
    and bt_step as q2bt.c does, as the line the codec then in force would have logged. Its switches and bad
    seconds (short of 90% of the codec's rate, or silent in a switch)."""
    s, sbc_s = Step(), Sbc()
    rank, pool, gap, switches, bad = APTX_HD, 0, 0, 0, 0
    for i, (cap, afh) in enumerate(seconds):
        if gap:
            gap, bad = gap - 1, bad + 1
            continue
        rate = SBC_KBPS[pool or 33] if rank == SBC else LOGGED[bt_name(L, rank)]
        bad += cap < 0.9 * rate
        short = cap < rate
        kbps = int(cap) if short else rate
        line = StepLine(kbps=kbps, acl=100, done=100, wq=9000 if short else 0, fly=5 if short else 2, bufs=5,
                        afh=afh, own=33, lowered=0, hz=48000)
        to = L.bt_sbc_step(C.byref(sbc_s), i * 1000, 1, rank, 33 if rank == SBC else 0, line.wq, line.fly, 5)
        if to >= 0:
            pool = to
        line.lowered = int(pool != 0)
        to = L.bt_step(C.byref(s), i * 1000, 1, rank, C.byref(line))
        if to:
            rank, gap, switches = to, GAP, switches + 1
    return switches, bad


def bt_name(L, rank):
    return C.string_at(L.bt_codec_name(rank)).decode()


@pytest.mark.parametrize('first, last, expect', [
    ('14:25:11', '14:31:35', (1, 27)),   # the commute's tail: aptX by pick, then SBC; the band down to 20 of 79
    ('06:15:06', '06:28:24', (2, 11)),   # a walk at home, on aptX HD
])
@pytest.mark.parametrize('bound', ['lo', 'hi'])
def test_the_commute_replayed_through_the_step_down(L, first, last, expect, bound):
    """docs/internals.md's table, "the same, and up only with afh at 60 or more": staying on aptX HD was 91 to 379
    bad seconds on the commute and 11 at home; the ladder as first built 4 switches and 35 to 50, and 6 and 13."""
    assert replay(L, capacity(first, last, bound)) == expect


def down_event(L, ev, handle, bdaddr=None):
    note = C.create_string_buffer(72)
    r = L.bt_down_event(ev, len(ev), handle, bdaddr, note, 72)
    return r, note.value.decode()


def disconnected(status, handle, reason):
    return bytes([4, 0x05, 4, status, handle & 0xff, handle >> 8, reason])


def test_a_dropped_link_is_noted_with_who_ended_it(L):
    """The commute's link went 6 s after a codec switch (14:32:22Z, handle 5) and the log could not say which side
    ended it. Disconnection Complete carries the reason: the headset's own (0x13, and 0x14 and 0x15 for its low
    resources and power off), the Q2's host (0x16: bluetoothd, or stock's reconnect), or the radio (0x08 the
    supervision timeout, 0x22 an LMP answer that never came)."""
    assert down_event(L, disconnected(0, 5, 0x13), 5, PX7) == (
        1, 'LINK down h=5 reason=0x13 headset dev=EC:66:D1:CF:59:3F')  # the address while the link was up
    assert down_event(L, disconnected(0, 5, 0x15), 5) == (1, 'LINK down h=5 reason=0x15 headset dev=-')
    assert down_event(L, disconnected(0, 5, 0x16), 5) == (1, 'LINK down h=5 reason=0x16 q2 dev=-')
    assert down_event(L, disconnected(0, 5, 0x08), 5) == (1, 'LINK down h=5 reason=0x08 timeout dev=-')
    assert down_event(L, disconnected(0, 5, 0x22), 5) == (1, 'LINK down h=5 reason=0x22 timeout dev=-')
    assert down_event(L, disconnected(0, 5, 0x3d), 5) == (1, 'LINK down h=5 reason=0x3d other dev=-')


def test_only_the_followed_links_end_is_noted(L):
    """Another link's end (a phone, the Car Remote's LE link) is not the headset's; a Disconnection Complete with a
    status is a disconnect that did not happen; a short packet and another event are not one."""
    assert down_event(L, disconnected(0, 64, 0x13), 5)[0] == 0
    assert down_event(L, disconnected(0, 5, 0x13), -1)[0] == 0
    assert down_event(L, disconnected(0x0c, 5, 0x13), 5)[0] == 0
    assert down_event(L, disconnected(0, 5, 0x13)[:6], 5)[0] == 0
    assert down_event(L, ptype_changed(0, 5, 0x0018), 5)[0] == 0


# ---- the player's side (docs/internals.md#bluetooth-link-monitor: pl, pr, pd, pw, dk, gw and PLAY) ----

PROC_IO = (b'rchar: 4183209731\nwchar: 912345678\nsyscr: 81234\nsyscw: 70123\nread_bytes: 1052672\n'
           b'write_bytes: 0\ncancelled_write_bytes: 0\n')


def test_the_player_s_io_counters(L):
    r, w, d = C.c_uint(), C.c_uint(), C.c_uint()
    assert L.bt_proc_io(PROC_IO, C.byref(r), C.byref(w), C.byref(d)) == 1
    assert (r.value, w.value, d.value) == (4183209731, 912345678, 1052672)
    assert L.bt_proc_io(b'rchar: 5\n', C.byref(r), C.byref(w), C.byref(d)) == 0  # no task I/O accounting
    assert L.bt_proc_io(b'', C.byref(r), C.byref(w), C.byref(d)) == 0


DISKSTATS = (b' 179       0 mmcblk0 6213 1901 559914 8730 3411 2601 92200 30210 0 9440 38940 0 0 0 0\n'
             b' 179       1 mmcblk0p1 812 0 6496 960 0 0 0 0 0 980 960 0 0 0 0\n'
             b' 179      32 mmcblk4 90213 77 9412214 401230 2213 891 1501224 288100 1 123456 689330 0 0 0 0\n'
             b' 179      33 mmcblk4p1 90100 77 9411000 401100 2213 891 1501224 288100 1 123400 689200 0 0 0 0\n')


def test_the_card_s_busy_time(L):
    ms = C.c_uint()
    assert L.bt_disk_busy(DISKSTATS, b'mmcblk4', C.byref(ms)) == 1
    assert ms.value == 123456  # io_ticks, the tenth field after the name: the whole card, not its partition
    assert L.bt_disk_busy(DISKSTATS, b'mmcblk0', C.byref(ms)) == 1 and ms.value == 9440
    assert L.bt_disk_busy(DISKSTATS, b'mmcblk9', C.byref(ms)) == 0
    assert L.bt_disk_busy(b'', b'mmcblk4', C.byref(ms)) == 0


def test_a_song_is_a_card_file_the_player_holds(L):
    assert L.bt_song_path(b'/mnt/mmc/Music/A/01 One.flac') == 1
    assert L.bt_song_path(b'/mnt/mmc/.subsonic/stream/A/One [ab12].flac') == 1  # a server song's copy
    assert L.bt_song_path(b'/mnt/mmc/.q2muse/logs/q2-bt.0') == 0
    assert L.bt_song_path(b'/dev/shanling_dac') == 0
    assert L.bt_song_path(b'pipe:[1234]') == 0
    assert L.bt_song_path(b'socket:[99]') == 0
    assert L.bt_song_path(b'/mnt/data/config.ini') == 0


# ---- the CODEC note's cfg= and cap= (docs/internals.md#bluetooth-link-monitor) ----

CODECS = b'''method return time=1795464061.200 sender=:1.3 -> destination=:1.21 serial=41 reply_serial=2
   array [
      dict entry(
         string "SBC"
         array [
            dict entry(
               string "Capabilities"
               variant                   array of bytes [
                     ff ff 02 35
                  ]
            )
         ]
      )
      dict entry(
         string "AAC"
         array [
            dict entry(
               string "Capabilities"
               variant                   array of bytes [
                     c0 01 84 84 e2 00
                  ]
            )
         ]
      )
   ]
'''
TRANSPORT = b'''      dict entry(
         object path "/org/bluez/hci0/dev_04_00_6E_D3_96_6D/sep1/fd0"
         array [
            dict entry(
               string "org.bluez.MediaTransport1"
               array [
                  dict entry(
                     string "Codec"
                     variant                         byte 2
                  )
                  dict entry(
                     string "Configuration"
                     variant                         array of bytes [
                           80 00 84 83 a9 80
                        ]
                  )
               ]
            )
         ]
      )
'''


def dbus_bytes(L, text, after, key):
    out = C.create_string_buffer(64)
    n = L.bt_dbus_bytes(text, after, key, out, 64)
    return n, out.value.decode()


def test_the_codec_s_configuration_and_capability_are_read(L):
    """cfg=: BlueZ's MediaTransport1 Configuration, the six AAC bytes the stream was set up with (MPEG-4 LC, 48 kHz,
    stereo, VBR, 240000 here); cap=: bluealsa's GetCodecs entry for the codec in use, not another codec's."""
    L.bt_dbus_bytes.argtypes = [C.c_char_p, C.c_char_p, C.c_char_p, C.c_char_p, C.c_int]
    assert dbus_bytes(L, CODECS + TRANSPORT, b'/fd', b'"Configuration"') == (6, "800084" "83a980")
    assert dbus_bytes(L, CODECS, b'string "AAC"', b'"Capabilities"') == (6, 'c00184' '84e200')
    assert dbus_bytes(L, CODECS, b'string "SBC"', b'"Capabilities"') == (4, 'ffff0235')
    assert dbus_bytes(L, CODECS, b'string "LDAC"', b'"Capabilities"') == (0, '')  # not offered
    assert dbus_bytes(L, CODECS, None, b'"Configuration"') == (0, '')
    printed = b'string "Configuration"\n variant array of bytes "abc"\n'  # all printable: dbus-send's string form
    assert dbus_bytes(L, printed, None, b'"Configuration"') == (0, '')
    cut = TRANSPORT[:TRANSPORT.index(b'a9')]  # the read stopped mid-array
    assert dbus_bytes(L, cut, b'/fd', b'"Configuration"') == (0, '')


# ---- AAC's peak: lowered frame to frame while the radio is the limit (bt_aac_step) ----

def aac(L, s, t, wq, fly, own=240, allowed=1, on=1, bufs=BUFS):
    return L.bt_aac_step(C.byref(s), int(t * 1000), allowed, on, own, wq, fly, bufs)


def test_the_aac_ladder_below_the_streams_own(L):
    """192, 160 and the floor 128 kbit/s, each only below the stream's own (240 as the build sets it), up a rung and
    then 0 (its own)."""
    assert [L.bt_aac_next(r, 240, -1) for r in (0, 192, 160, 128)] == [192, 160, 128, -1]
    assert [L.bt_aac_next(r, 240, 1) for r in (128, 160, 192, 0)] == [160, 192, 0, -1]
    assert L.bt_aac_next(0, 200, -1) == 192 and L.bt_aac_next(192, 200, 1) == 0
    assert L.bt_aac_next(0, 160, -1) == 128 and L.bt_aac_next(128, 160, 1) == 0   # a sink's lower own
    assert L.bt_aac_next(0, 128, -1) == -1                                         # at the floor already: nothing
    assert L.bt_aac_next(0, 0, -1) == -1                                           # own not known: nothing


def test_aac_steps_its_peak_down_while_the_socket_backs_up_with_the_buffers_full(L):
    """The study's bad band: every ACL buffer held and the A2DP socket backed up, second after second."""
    s = Sbc()
    assert aac(L, s, 0, 0, 3) == NONE
    assert aac(L, s, 1, 6080, BUFS) == NONE                 # one second: not yet
    assert aac(L, s, 2, 6080, BUFS) == 192                  # two in a row: a rung down, the next frame
    assert [aac(L, s, t, 9000, BUFS) for t in range(3, 7)] == [NONE, NONE, NONE, 160]   # 2 s to drain first
    assert [aac(L, s, t, 9000, BUFS) for t in range(7, 11)] == [NONE, NONE, NONE, 128]
    assert all(aac(L, s, t, 9000, BUFS) == NONE for t in range(11, 60))   # the floor


def test_a_queue_without_full_buffers_does_not_lower_aac(L):
    s = Sbc()
    assert all(aac(L, s, t, 9000, BUFS - 1) == NONE for t in range(10))     # the encoder or the player
    assert all(aac(L, s, t, 4096, BUFS) == NONE for t in range(10, 20))     # 4 kB is not over
    assert all(aac(L, s, t, 9000, -1) == NONE for t in range(20, 30))       # fly not read


def test_aac_climbs_back_a_rung_after_30_clean_seconds_and_a_minute(L):
    s = Sbc()
    for t in (0, 1, 4, 5, 8, 9):
        aac(L, s, t, 6080, BUFS)
    assert s.rung == 128
    ups = [(t, r) for t in range(10, 400) if (r := aac(L, s, t, 0, 2)) != NONE]
    assert ups == [(69, 160), (129, 192), (189, 0)]
    s = Sbc()
    aac(L, s, 0, 6080, BUFS)
    assert aac(L, s, 1, 6080, BUFS) == 192
    assert next(t for t in range(2, 200) if aac(L, s, t, 0, 2) != NONE) == 61     # up to its own (0)
    aac(L, s, 70, 6080, BUFS)
    assert aac(L, s, 71, 6080, BUFS) == 192                                       # down again 10 s on
    assert next(t for t in range(72, 400) if aac(L, s, t, 0, 2) != NONE) == 191   # the wait doubled


def test_another_codec_or_the_setting_off_puts_aacs_peak_back(L):
    s = Sbc()
    aac(L, s, 0, 6080, BUFS)
    assert aac(L, s, 1, 6080, BUFS) == 192
    assert aac(L, s, 5, 0, 2, allowed=0) == 0               # turned off: its own at once
    assert aac(L, s, 6, 0, 2, allowed=0) == NONE            # once
    assert all(aac(L, s, t, 9000, BUFS, allowed=0) == NONE for t in range(7, 20))
    s = Sbc()
    aac(L, s, 0, 6080, BUFS)
    aac(L, s, 1, 6080, BUFS)
    assert aac(L, s, 9, 0, 2, on=0) == 0                    # another codec now: the next AAC stream starts whole
    assert all(aac(L, s, t, 9000, BUFS, on=0) == NONE for t in range(10, 20))
    assert all(aac(L, s, t, 9000, BUFS, allowed=-1) == NONE for t in range(20, 30))  # another Quality choice


def test_an_aac_own_not_known_or_at_the_floor_steps_nothing(L):
    s = Sbc()
    assert all(aac(L, s, t, 9000, BUFS, own=0) == NONE for t in range(10))     # unpatched, or a set that failed
    assert all(aac(L, s, t, 9000, BUFS, own=128) == NONE for t in range(10, 20))   # a sink capped at 128


@pytest.mark.parametrize('coding, key, allowed', [
    (b'2', b'', 0),                      # Auto, the switch never touched: off
    (b'2', b'BTSTEPDOWN=1\r\n', 1),      # Auto, on: AAC on AirPods
    (b'5', b'BTSTEPDOWN=1\r\n', 1),      # AAC chosen by hand, with the switch on
    (b'5', b'BTSTEPDOWN=0\r\n', 0),
    (b'6', b'BTSTEPDOWN=1\r\n', -1),     # SBC HQ: not AAC's
    (b'3', b'BTSTEPDOWN=1\r\n', -1),     # aptX HD
    (b'0', b'', -1),                     # LDAC Best
])
def test_aac_is_adapted_under_auto_or_aac_only_with_the_switch_on(L, coding, key, allowed):
    assert L.bt_aac_allowed(INI_SBC % (coding, key)) == allowed
    assert L.bt_aac_allowed(b'') == -1


def test_the_aac_bytes_are_where_the_build_puts_them(L):
    """q2bt.c writes bluealsa's byte at BT_AAC_RATE through /proc/PID/mem; tools/build.py links bt_silence.S's
    aac_ask there (BT_AAC_RATE), just past SBC's, and the build checks its own side."""
    import re
    import sys
    sys.path.insert(0, str(ROOT / 'tools'))
    import build
    header = (ROOT / 'patch/q2bt.h').read_text()
    m = re.search(r'#define BT_AAC_RATE (0x[0-9a-f]+)', header)
    assert m and int(m[1], 16) == build.BT_AAC_RATE == build.BT_SBC_POOL + 4
