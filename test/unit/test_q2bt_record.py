"""The link monitor's recorder (patch/q2bt_core.c, docs/internals.md#bluetooth-link-monitor, the sampler of
docs/bluetooth.md): every connected classic link, streaming or idle, its RSSI and AFH map every 10 s and what the
controller agreed for it once; each second with no radio traffic the UART's error counts, the byte counters and the
A2DP socket's queue; every minute the Wi-Fi band and the coex lines that move; and the pacing state the firmware's
and the apps' downloads obey while A2DP streams on 2.4 GHz. Built for this machine with test/unit/q2guard_host.c."""
import ctypes as C
import subprocess

import pytest

from test_q2guard import ROOT
from test_q2bt import UNSET, ConnInfo, Link, Sample

WALL = 1795464061  # 2026-11-23T20:01:01Z


@pytest.fixture(scope='module')
def L(tmp_path_factory):
    out = tmp_path_factory.mktemp('q2btrec') / 'q2btrec.so'
    subprocess.run(['clang', '-shared', '-fPIC', '-O1', '-DQ2GUARD_HOST', '-Wall', '-Werror', '-Wno-unused-parameter',
                    f'-DPEQ_ROOT="{out.parent}"', ROOT / 'patch/q2bt_core.c', ROOT / 'patch/q2guard_core.c',
                    ROOT / 'patch/card_store.c', ROOT / 'test/unit/q2guard_host.c', '-o', out], check=True)
    lib = C.CDLL(str(out))
    lib.bt_acl_links.argtypes = [C.c_char_p, C.c_int, C.c_void_p, C.c_int]
    lib.bt_idle_line.argtypes = [C.c_char_p, C.c_int, C.c_void_p]
    lib.bt_acl_line.argtypes = [C.c_char_p, C.c_int, C.c_long, C.c_void_p, C.c_char_p, C.c_int]
    lib.bt_coex_filter.argtypes = [C.c_char_p, C.c_char_p, C.c_int]
    lib.bt_wpa_freq.argtypes = [C.c_char_p, C.c_char_p, C.c_int]
    lib.bt_wifi_line.argtypes = [C.c_char_p, C.c_int, C.c_long, C.c_int, C.c_char_p]
    lib.bt_pace.argtypes = [C.c_int, C.c_int]
    lib.bt_pace_text.argtypes = [C.c_char_p, C.c_int, C.c_int, C.c_int]
    lib.bt_line.argtypes = [C.c_char_p, C.c_int, C.c_void_p]
    return lib


class Acl(C.Structure):
    _fields_ = [('handle', C.c_int), ('addr', C.c_uint8 * 6)]


class Idle(C.Structure):
    _fields_ = [('wall', C.c_long), ('up', C.c_uint)] + [(n, C.c_int) for n in (
        'btx', 'brx', 'wtx', 'wrx', 'wq')] + [('ue', C.c_int * 5), ('ue_ok', C.c_int)]


AIRPODS = bytes([0x6d, 0x96, 0xd3, 0x6e, 0x00, 0x04])  # 04:00:6E:D3:96:6D, last byte first as bdaddr_t holds it
CAR = bytes([0x01, 0x02, 0x03, 0x04, 0x05, 0x06])


def conns(*links):
    """HCIGETCONNLIST's answer: (handle, bdaddr, type) each, type 1 ACL, 0 SCO, 0x80 LE."""
    req = bytes((C.c_uint16 * 2)(0, len(links)))
    for h, a, kind in links:
        c = ConnInfo(handle=h, type=kind)
        c.bdaddr[:] = list(a)
        req += bytes(c)
    return req


# ---- the links ----

def test_every_classic_acl_link_is_listed_and_no_other(L):
    """A headset and a car at once (a phone on LE, the headset's SCO for a call): both ACL links, in the kernel's
    order, at most the room given."""
    req = conns((0x0b, AIRPODS, 1), (0x41, CAR, 0x80), (0x0c, CAR, 0), (0x0e, CAR, 1))
    out = (Acl * 4)()
    assert L.bt_acl_links(req, 4, out, 4) == 2
    assert (out[0].handle, bytes(out[0].addr), out[1].handle, bytes(out[1].addr)) == (0x0b, AIRPODS, 0x0e, CAR)
    assert L.bt_acl_links(req, 4, out, 1) == 1  # room for one
    assert L.bt_acl_links(conns(), 4, out, 4) == 0  # Bluetooth on, nothing connected


def link(**f):
    k = Link(handle=f.get('handle', 11), answered=f.get('answered', 2))
    for name in ('rssi', 'afh', 'role', 'lsto', 'flush', 'txmax'):
        setattr(k, name, f.get(name, UNSET))
    return k


def acl_line(L, k, info=0, addr=AIRPODS):
    out = C.create_string_buffer(160)
    n = L.bt_acl_line(out, 160, WALL, C.byref(k), addr, info)
    assert n == len(out.value)
    return out.value.decode()


def test_an_idle_link_s_line_every_ten_seconds(L):
    assert acl_line(L, link(rssi=-7, afh=50)) == '2026-11-23T20:01:01Z ACL h=11 dev=04:00:6E:D3:96:6D rssi=-7 afh=50\n'
    assert acl_line(L, link()) == '2026-11-23T20:01:01Z ACL h=11 dev=04:00:6E:D3:96:6D rssi=- afh=-\n'


def test_what_the_controller_agreed_for_a_link_once(L):
    """Role Discovery 0 is the Q2 central (the hopping is its AFH map's), supervision and flush timeouts in slots of
    0.625 ms written in ms (BlueZ's default 0x7d00 is 20 s; a flush timeout of 0 is none, retried for ever), the
    maximum transmit power in dBm."""
    k = link(role=0, lsto=0x7d00, flush=0, txmax=4, answered=4)
    assert acl_line(L, k, 1) == ('2026-11-23T20:01:01Z ACLINFO h=11 dev=04:00:6E:D3:96:6D role=central lsto=20000ms '
                                 'flush=none txmax=4\n')
    k = link(role=1, lsto=0x0c80, flush=0x20, answered=3)
    assert acl_line(L, k, 1) == ('2026-11-23T20:01:01Z ACLINFO h=11 dev=04:00:6E:D3:96:6D role=peripheral '
                                 'lsto=2000ms flush=20ms txmax=-\n')


def idle_line(L, ue=None, **f):
    s = Idle(wall=WALL, up=4242, **{k: f.get(k, -1) for k in ('btx', 'brx', 'wtx', 'wrx', 'wq')})
    if ue is not None:
        s.ue[:], s.ue_ok = ue, 1
    out = C.create_string_buffer(160)
    n = L.bt_idle_line(out, 160, C.byref(s))
    assert n == len(out.value)
    return out.value.decode()


def test_each_second_of_an_idle_link_with_no_radio_traffic(L):
    """Bytes a second each way to and from the controller (hci_dev_stats), Wi-Fi's kB/s, the A2DP socket's queue and
    the UART's framing, parity, break and overrun counts since the second before: files and ioctls only."""
    assert idle_line(L, btx=34, brx=60, wtx=0, wrx=12, wq=0, ue=[0, 0, 0, 0, 0]) == (
        '2026-11-23T20:01:01Z IDLE up=4242 btx=34 brx=60 wtx=0 wrx=12 wq=0 ue=0\n')  # none moved
    assert idle_line(L) == '2026-11-23T20:01:01Z IDLE up=4242 btx=- brx=- wtx=- wrx=- wq=- ue=-\n'
    assert idle_line(L, ue=[2, 5, 1, 0, 0]).endswith(' ue=2/5/1/0/0\n')


# ---- Wi-Fi: wpa_supplicant's STATUS, and Realtek's coex dump ----

STATUS_24 = b"""bssid=a0:b1:c2:d3:e4:f5
freq=2412
ssid=home
id=0
mode=station
pairwise_cipher=CCMP
key_mgmt=WPA2-PSK
wpa_state=COMPLETED
ip_address=192.168.1.40
"""


def wpa(L, text):
    state = C.create_string_buffer(24)
    return L.bt_wpa_freq(text, state, 24), state.value.decode()


def test_the_band_from_wpa_supplicant_s_status(L):
    assert wpa(L, STATUS_24) == (2412, 'COMPLETED')
    assert wpa(L, STATUS_24.replace(b'freq=2412', b'freq=5180')) == (5180, 'COMPLETED')
    assert wpa(L, b'wpa_state=SCANNING\n') == (0, 'SCANNING')  # not joined: no freq line
    assert wpa(L, b'') == (0, '')


def wifi_line(L, freq, state):
    out = C.create_string_buffer(96)
    L.bt_wifi_line(out, 96, WALL, freq, state)
    return out.value.decode()


def test_the_wifi_line_names_band_and_channel(L):
    assert wifi_line(L, 2412, b'COMPLETED') == '2026-11-23T20:01:01Z WIFI freq=2412 band=2.4 ch=1 state=COMPLETED\n'
    assert wifi_line(L, 2484, b'COMPLETED').count('ch=14 ')
    assert wifi_line(L, 5180, b'COMPLETED') == '2026-11-23T20:01:01Z WIFI freq=5180 band=5 ch=36 state=COMPLETED\n'
    assert wifi_line(L, 5955, b'COMPLETED').count('band=6 ch=1 ')
    assert wifi_line(L, 0, b'') == '2026-11-23T20:01:01Z WIFI freq=- band=- ch=- state=-\n'


# What the Q2's driver prints (rtl8733bs.ko's labels, docs/bluetooth.md#coexistence), cut to a few that do not move.
BTCOEX = b"""============[BT Coexist info 8733B]============
 Ant PG Num/ Mech/ Pos/ RFE/ Dist      = 2/ Non-Shared/ Main / 5/ 10
 Ver Coex/ Para/ BT_Dez/ BT_Rpt         = 20260527_39/ 20241204_0f/ 0x0c/ 0x0e (Match)
 W_FW/ B_FW                             = 0x10026(Match)/ 0x10c81143
============[Wifi Status]============
 RSSI/ STA_Chnl/ P2P_Chnl/ BI          = -23 dBm/ 1/ 0/ 100
 Band/ BW/ Traffic/ APCnt               = 2.4G / HT20/ idle/ 1
 Power Status                           = IPS OFF, LPS OFF
 Rx_Tp/ Tx_Tp (Mbps)                    = 0/ 0
============[BT Status]============
 BT status/ rssi/ retryCnt/ popCnt      = connected-idle/ -56dBm/ 0/ 0
 Profiles                               = None
 Role/RoleSwCnt/IgnWla/Feature          = Master/ 0/ 0/ 0x07
 AFH MAP                                = 0xffff_ffffffff_fe00000f
============[Mechanism]============
 TDMA                                   = 61 30 03 11 11 (case-3, TDMA-On)
 Coex_Mode/Free_Run/Timer_base          = 2G-SP/ No/ 1
 AFH Map to BT                          = 01 01 24 (RF-Ch = 1)
 Null All/Retry/Ack/BT_Empty/BT_Late    = 120/ 4/ 116/ 0/ 2
 Cnt TDMA_Togg/LkRx/LKAP_On/fw          = 19/ 3/ 2/ 0
 BT slot avg/BT slot max/BT slot min    = 49/ 144/ 44
============[HW Status]============
 0x770(Hi-pri rx/tx)                    = 290/ 46
 0x774(Lo-pri rx/tx)                    = 0/ 0
 Path Owner                             = WL
 WL_TxPw                                = 0
"""


def coex(L, text):
    out = C.create_string_buffer(2048)
    n = L.bt_coex_filter(text, out, 2048)
    return n, out.value.decode().splitlines()


def test_only_the_sixteen_coex_lines_that_move(L):
    """The study's filter: Wi-Fi channel, band and traffic, power state, throughput, BT status, profiles, role, the
    BT side's AFH map, TDMA, coex mode, null frames, TDMA toggles, BT slots, the PTA counts and path owner; the
    versions, antennas and the rest of the 4 KB dump never change. Runs of spaces are one."""
    n, lines = coex(L, BTCOEX)
    assert n == 16 == len(lines)
    assert lines[0] == 'RSSI/ STA_Chnl/ P2P_Chnl/ BI = -23 dBm/ 1/ 0/ 100'
    assert lines[8] == 'TDMA = 61 30 03 11 11 (case-3, TDMA-On)'
    assert lines[-1] == 'Path Owner = WL'
    assert not any(l.startswith(('Ant PG', 'Ver Coex', 'W_FW', 'AFH Map to BT', 'WL_TxPw')) for l in lines)
    assert coex(L, b'') == (0, [])


# ---- pacing (docs/internals.md#bluetooth-coexistence-pacing) ----

def test_paced_only_while_a2dp_streams_and_wifi_is_on_2_4_ghz(L):
    """The 2026-11-24 stall cluster: the Q2's own Wi-Fi receiving 1.5 to 2 MB/s on 2.4 GHz while AAC streamed. On
    5 GHz, with Wi-Fi not joined, or with nothing streaming, nothing is paced."""
    assert L.bt_pace(1, 2412) == 1 and L.bt_pace(1, 2484) == 1
    assert L.bt_pace(1, 5180) == 0 and L.bt_pace(1, 5955) == 0
    assert L.bt_pace(1, 0) == 0  # not joined
    assert L.bt_pace(0, 2412) == 0


def pace_text(L, paced, freq):
    out = C.create_string_buffer(64)
    L.bt_pace_text(out, 64, paced, freq)
    return out.value.decode()


def test_the_pace_file_says_whether_and_how_fast(L):
    assert pace_text(L, 1, 2412) == 'paced=1 rate=204800 freq=2412\n'  # 200 KB/s
    assert pace_text(L, 0, 5180) == 'paced=0 rate=0 freq=5180\n'
    assert pace_text(L, 0, 0) == 'paced=0 rate=0 freq=0\n'


# ---- what it costs on the card ----

def hour(L, kbps, wtx, wrx, cpu, dk, ue):
    """An hour's bytes, streaming and idle, with these widths."""
    s = Sample(wall=WALL, up=1234567, kbps=kbps, brx=12, acl=109, wq=3648, rssi=-38, afh=74, wtx=wtx, wrx=wrx,
               cpu=cpu, ba=137, own=12, h=11, er=880, done=109, fly=6, bp=53, pl=125, dk=dk, gw=0)
    s.ue[:], s.ue_ok = ue, 1
    out = C.create_string_buffer(256)
    stream = L.bt_line(out, 256, C.byref(s))
    idle = len(idle_line(L, btx=12345, brx=12345, wtx=wtx, wrx=wrx, wq=3648, ue=ue))
    acl = len(acl_line(L, link(handle=3839, rssi=-128, afh=79)))
    wifi = len(wifi_line(L, 5825, b'COMPLETED'))
    n, lines = coex(L, BTCOEX)
    minute = wifi + sum(len('2026-11-23T20:01:01Z COEX ') + len(x) + 1 for x in lines)
    return 3600 * stream + 360 * acl + 60 * minute, 3600 * idle + 360 * acl + 60 * minute


def test_an_hour_of_lines_fits_the_budget(L):
    """About 0.7 MB an hour with the rotation as it is (1 MB files, four, so about five and a half hours on the
    card): a stream's second, or an idle link's, each second; an ACL line per link every 10 s; WIFI and 16 COEX lines
    a minute. The usual stream as the 2026-11-24 logs have it (AAC at 240 kbps, Wi-Fi quiet, no UART error), and the
    widest every field ever got there (aptX HD at 614, Wi-Fi at 2 MB/s both ways, the card busy the whole second,
    UART errors in every second), which stays under 0.8 MB."""
    streaming, idle = hour(L, 240, 0, 20, 21, 12, [0] * 5)
    assert streaming < 0.7 * 2**20 and idle < 0.4 * 2**20, (streaming, idle)
    streaming, idle = hour(L, 614, 2074, 2074, 100, 1000, [12, 15, 3, 0, 0])
    assert streaming < 0.8 * 2**20 and idle < 0.45 * 2**20, (streaming, idle)
