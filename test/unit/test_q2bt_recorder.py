"""The recorder on the device's side (patch/q2bt.c, docs/internals.md#bluetooth-link-monitor, docs/bluetooth.md's
sampler): every classic link recorded whether it streams or not, what it sends the controller and how often, what it
costs with nothing connected, nothing at all with Bluetooth off, and the pace file downloads obey. Built for this
machine with test/unit/q2bt_sleep_host.c, which plays the system calls, the controller's answers, wpa_supplicant and
the clock."""
import ctypes as C
import subprocess

import pytest

from test_q2guard import ROOT
from test_q2bt_record import BTCOEX

AIRPODS = bytes([0x6d, 0x96, 0xd3, 0x6e, 0x00, 0x04])  # 04:00:6E:D3:96:6D
CAR = bytes([0x01, 0x02, 0x03, 0x04, 0x05, 0x06])
RSSI, AFH, ROLE, LSTO, FLUSH, TPL = 0x1405, 0x1406, 0x0809, 0x0c36, 0x0c27, 0x0c2d
READS = {RSSI, AFH, ROLE, LSTO, FLUSH, TPL}


@pytest.fixture
def L(tmp_path):
    out = tmp_path / 'q2bt_rec.so'
    subprocess.run(['clang', '-shared', '-fPIC', '-O1', '-DQ2GUARD_HOST', '-Wall', '-Werror', '-Wno-unused-parameter',
                    '-Wno-unused-function', '-include', ROOT / 'test/unit/glibc228_snprintf.h',
                    f'-DPEQ_ROOT="{tmp_path}"', ROOT / 'test/unit/q2bt_sleep_host.c', ROOT / 'patch/q2bt_core.c',
                    ROOT / 'patch/q2dbus.c', ROOT / 'patch/q2guard_core.c', ROOT / 'patch/q2log_core.c',
                    ROOT / 'patch/card_store.c', '-o', out], check=True)
    lib = C.CDLL(str(out))
    lib.start.argtypes = [C.c_char_p]
    lib.sleep_for.argtypes = [C.c_uint, C.POINTER(C.c_uint)]
    lib.sleep_for.restype = C.c_uint
    lib.bt_ram.restype = lib.ulog_ram.restype = C.c_char_p
    lib.link_add.argtypes = [C.c_int, C.c_char_p]
    lib.set_files.argtypes = [C.c_int, C.c_char_p]
    lib.set_tx_rate.argtypes = lib.uart_errors.argtypes = lib.wlan_rx_bytes.argtypes = [C.c_uint]
    lib.op_count.restype = lib.connlists.restype = C.c_uint
    (tmp_path / 'tmp').mkdir()
    lib.set_files(1, BTCOEX)
    lib.start(str(tmp_path).encode())
    setattr(lib, 'pace', lambda: (tmp_path / 'tmp/q2-bt-coex').read_text())
    return lib


def run(L, seconds):
    """q2guard's waits of 5 s for this long"""
    for _ in range(seconds // 5):
        calls = C.c_uint()
        L.sleep_for(5000, C.byref(calls))


def lines(L, word=None):
    text = C.string_at(L.bt_ram(), L.bt_used()).decode()
    out = text.splitlines()
    return [x for x in out if x.split(' ', 2)[1] == word] if word else out


def ops(L):
    return {op: L.op_count(op) for op in range(0x10000) if L.op_count(op)}


def test_bluetooth_off_sends_nothing(L):
    """hci0 gone (Bluetooth switched off): in five minutes not one socket bound to the controller, not one command,
    not one line, and the pace file says nothing is paced."""
    L.hci_gone(1)
    L.set_tx_rate(0)
    L.set_wifi(2412)
    run(L, 300)
    assert L.command_count() == 0 and L.raw_sockets() == 0
    assert lines(L) == []
    assert L.pace() == 'paced=0 rate=0 freq=0\n'


def test_nothing_connected_is_two_ioctls_a_tick(L):
    """Bluetooth on, no link: each of q2guard's 5 s ticks reads the byte counter and the connection list and nothing
    else: no raw socket, no command, no wpa_supplicant, no line."""
    L.set_tx_rate(0)
    L.set_wifi(2412)
    was = L.connlists()
    for _ in range(12):
        calls = C.c_uint()
        took = L.sleep_for(5000, C.byref(calls))
        assert 5000 <= took <= 5010 and calls.value == 1
    assert L.connlists() - was == 12
    assert L.command_count() == 0 and L.raw_sockets() == 0 and L.wpa_count() == 0 and L.unix_sockets() == 0
    assert lines(L) == []


def test_an_idle_link_is_recorded(L):
    """The AirPods connected and paused, the case of the three link losses of 2026-11-24: a line a second with no
    radio traffic; RSSI and the AFH map every 10 s; role, timeouts and the maximum power once; the band and the coex
    lines every minute. Only those six read-only commands ever go to the controller."""
    L.set_tx_rate(40)  # AVRCP and the odd L2CAP signal: no stream
    L.set_wifi(2412)
    L.link_add(11, AIRPODS)
    run(L, 5)  # the tick that sees the link
    L.uart_errors(3)  # three framing errors in a second: the storms' signature
    run(L, 120)
    assert not L.streaming()
    idle = lines(L, 'IDLE')
    assert 115 <= len(idle) <= 121, len(idle)
    assert all(' btx=40 brx=0 ' in x for x in idle[1:])
    assert sum(' ue=3/0/0/0/0' in x for x in idle) == 1 and sum(x.endswith(' ue=0') for x in idle) >= len(idle) - 2
    acl = lines(L, 'ACL')
    assert 12 <= len(acl) <= 14 and all(x.endswith('ACL h=11 dev=04:00:6E:D3:96:6D rssi=-7 afh=48') for x in acl)
    info = lines(L, 'ACLINFO')
    assert len(info) == 1 and info[0].endswith(' role=central lsto=20000ms flush=none txmax=4')
    assert len(lines(L, 'WIFI')) in (2, 3) and lines(L, 'WIFI')[0].endswith(' freq=2412 band=2.4 ch=1 state=COMPLETED')
    assert len(lines(L, 'COEX')) == 16 * len(lines(L, 'WIFI'))
    sent = ops(L)
    assert set(sent) <= READS
    assert sent[RSSI] == sent[AFH] == len(acl)
    assert sent[ROLE] == sent[LSTO] == sent[FLUSH] == sent[TPL] == 1
    assert L.pace() == 'paced=0 rate=0 freq=0\n'  # nothing streams: nothing is paced


def test_two_links_each_get_theirs_and_a_gone_one_is_noted(L):
    L.set_tx_rate(0)
    L.set_wifi(0)  # no wpa_supplicant
    L.link_add(11, AIRPODS)
    L.link_add(12, CAR)
    run(L, 30)
    assert {x.split(' dev=')[1].split()[0] for x in lines(L, 'ACLINFO')} == {'04:00:6E:D3:96:6D', '06:05:04:03:02:01'}
    assert ops(L)[ROLE] == 2
    assert lines(L, 'WIFI')[0].endswith(' freq=- band=- ch=- state=-')
    L.links_clear()
    run(L, 10)
    gone = [x for x in lines(L) if ' ACL gone ' in x]
    assert len(gone) == 2 and gone[0].endswith('ACL gone h=11 dev=04:00:6E:D3:96:6D')
    n, sent = len(lines(L)), L.command_count()
    run(L, 60)
    assert len(lines(L)) == n and L.command_count() == sent  # nothing left to record: nothing written or sent
    assert not L.raw_open_now()


def test_a_stream_reads_two_commands_a_second_and_drops_the_dead_fields(L):
    """While A2DP streams the link is read each second as before, but for RSSI and the AFH map only: Link Quality,
    the current transmit power and the Failed Contact Counter are never sent. The 10 s ACL line takes the stream's
    own reading, so the link is asked no more often than before."""
    L.set_wifi(5180)
    L.link_add(11, AIRPODS)
    run(L, 70)
    assert L.streaming()
    sent = ops(L)
    assert set(sent) <= READS and 0x1403 not in sent and 0x1401 not in sent
    stream = [x for x in lines(L) if ' up=' in x and ' IDLE ' not in x]
    assert 55 <= len(stream) <= 66
    assert sent[RSSI] == sent[AFH] and abs(sent[RSSI] - len(stream)) <= 1
    for gone in ('lq', 'tpl', 'err', 'fcc', 'ew', 'pr', 'pd', 'pw'):
        assert not any(f' {gone}=' in x for x in stream)
    assert all(' brx=0 ' in x and x.endswith(' ue=0') for x in stream[1:])
    assert lines(L, 'ACL') and lines(L, 'ACLINFO')


def test_paced_while_a_stream_runs_on_2_4_ghz_and_not_on_5(L):
    """A stream on 2.4 GHz: the pace file says 200 KB/s within a second of the START, a PACE note in both logs. The
    network rejoined on 5 GHz: unpaced within 10 s. The stream over (30 s quiet): unpaced."""
    L.set_wifi(2437)
    L.link_add(11, AIRPODS)
    run(L, 10)
    assert L.streaming() and L.pace() == 'paced=1 rate=204800 freq=2437\n'
    assert [x for x in lines(L) if 'PACE on freq=2437' in x]
    assert 'PACE on freq=2437' in C.string_at(L.ulog_ram(), L.ulog_used()).decode()
    L.set_wifi(5180)
    run(L, 10)
    assert L.pace() == 'paced=0 rate=0 freq=5180\n'
    L.set_wifi(2437)
    run(L, 10)
    assert L.pace().startswith('paced=1 ')
    L.set_tx_rate(0)
    run(L, 40)
    assert not L.streaming() and L.pace() == 'paced=0 rate=0 freq=2437\n'


def test_a_restarted_guard_clears_a_stale_pace(tmp_path, L):
    """q2guard ended while paced (a crash, S90play's respawn 2 s later): the new one's first act says unpaced, so no
    download stays held by a stream that is not running."""
    (tmp_path / 'tmp/q2-bt-coex').write_text('paced=1 rate=204800 freq=2412\n')
    L.start(str(tmp_path).encode())
    assert L.pace() == 'paced=0 rate=0 freq=0\n'


def test_switched_off_with_a_link_up_stops_at_once(L):
    """Bluetooth switched off while a headset sat connected and idle: the recorder lets go of the controller at the
    next tick, sends nothing more and writes nothing more, and its wait is a sleep again."""
    L.set_tx_rate(0)
    L.set_wifi(2412)
    L.link_add(11, AIRPODS)
    run(L, 30)
    assert lines(L, 'IDLE') and L.raw_open_now()
    L.hci_gone(1)
    run(L, 5)
    n, sent = len(lines(L)), L.command_count()
    for _ in range(12):
        calls = C.c_uint()
        assert 5000 <= L.sleep_for(5000, C.byref(calls)) <= 5010 and calls.value <= 1
    assert L.command_count() == sent and len(lines(L)) == n
    assert not L.raw_open_now()
