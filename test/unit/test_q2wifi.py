"""The Wi-Fi fault recorder's core (patch/q2wifi_core.c, docs/internals.md#wi-fi-fault-recorder): the deaf radio's
watch, the privacy scrub, the parsers of wpa_supplicant's answers and events and of the kernel's files, and the log's
lines. Built for this machine with test/unit/q2wifi_host.c, which also holds the device side (test_q2wifi_rec.py)."""
import ctypes as C
import subprocess

import pytest

from test_q2guard import ROOT

WS_NONE, WS_DISABLED, WS_INACTIVE, WS_SCANNING, WS_DISCONNECTED, WS_JOINING, WS_COMPLETED = range(7)
WE_NONE, WE_CONNECTED, WE_DISCONNECTED, WE_TERMINATING, WE_SCAN_STARTED, WE_SCAN_RESULTS, WE_SCAN_FAILED, WE_LOG = range(8)
DEAF_NONE, DEAF_EMPTY, DEAF_NOJOIN = range(3)
WA_NONE, WA_START, WA_AGAIN, WA_EMPTY, WA_END = range(5)
END_SCAN, END_ASSOC, END_OFF = 1, 2, 3
SALT = 0x12345678


def build(out, root, ctrl='w.sock'):
    subprocess.run(['clang', '-shared', '-fPIC', '-O1', '-DQ2GUARD_HOST', '-Wall', '-Werror', '-Wno-unused-parameter',
                    '-include', ROOT / 'test/unit/glibc228_snprintf.h', f'-DPEQ_ROOT="{root}"', f'-DWIFI_CTRL="{ctrl}"',
                    ROOT / 'test/unit/q2wifi_host.c', ROOT / 'patch/q2wifi_core.c', ROOT / 'patch/q2guard_core.c',
                    ROOT / 'patch/q2log_core.c', ROOT / 'patch/card_store.c', '-o', out], check=True)
    return C.CDLL(str(out))


@pytest.fixture(scope='module')
def L(tmp_path_factory):
    d = tmp_path_factory.mktemp('q2wifi')
    return build(d / 'q2wifi.so', d)


class Watch(C.Structure):
    _fields_ = [('empty_since', C.c_uint), ('nojoin_since', C.c_uint), ('deaf', C.c_int), ('deaf_at', C.c_uint),
                ('snap_at', C.c_uint), ('snaps', C.c_int), ('end', C.c_int), ('lasted', C.c_uint)]


class Obs(C.Structure):
    _fields_ = [('now', C.c_uint), ('state', C.c_int), ('nets', C.c_int), ('known', C.c_int)]


class Scan(C.Structure):
    _fields_ = [('n', C.c_int), ('band', C.c_int * 3), ('best', C.c_int * 3), ('known', C.c_int)]


class Vm(C.Structure):
    _fields_ = [('areas', C.c_int), ('used', C.c_ulong), ('vmalloc', C.c_ulong), ('ioremap', C.c_ulong),
                ('gap', C.c_ulong), ('lo', C.c_ulong), ('hi', C.c_ulong)]


def text(fn, *args, size=2048):
    b = C.create_string_buffer(size)
    n = fn(*args, b, size)
    return b.value.decode(), n


def hashed(L, mac):
    b = C.create_string_buffer(8)
    L.wifi_hash(SALT, mac.encode(), b)
    return b.value.decode()


# ---- privacy ----

def test_an_address_is_a_salted_hash_the_same_within_a_salt(L):
    a = hashed(L, 'a0:b1:c2:d3:e4:f5')
    assert a.startswith('#') and len(a) == 7
    assert hashed(L, 'A0:B1:C2:D3:E4:F5') == a  # case folded
    assert hashed(L, 'a0:b1:c2:d3:e4:f6') != a
    b = C.create_string_buffer(8)
    L.wifi_hash(SALT + 1, b'a0:b1:c2:d3:e4:f5', b)
    assert b.value.decode() != a  # another boot, another hash
    assert L.wifi_salt(b'0b7c-boot\n') == L.wifi_salt(b'0b7c-boot') != L.wifi_salt(b'other')


def test_the_scrub_hashes_addresses_and_drops_every_line_naming_an_ssid(L):
    raw = b'cur_ch=36\nSSID=HomeNet-Private\nbssid=a0:b1:c2:d3:e4:f5 rssi=-60\n  ap ssid: x\nend'
    out, n = text(L.wifi_scrub, raw, C.c_uint(SALT))
    assert 'HomeNet' not in out and 'a0:b1' not in out and 'ssid' not in out.lower().replace('bssid', '')
    assert out == f'cur_ch=36\nbssid={hashed(L, "a0:b1:c2:d3:e4:f5")} rssi=-60\nend\n' and n == len(out)
    out, _ = text(L.wifi_scrub, b'0123', C.c_uint(SALT), size=4)
    assert len(out) <= 3


# ---- wpa_supplicant ----

STATUS = (b'bssid=a0:b1:c2:d3:e4:f5\nfreq=5240\nssid=HomeNet-Private\nid=0\nmode=station\nwifi_generation=5\n'
          b'pairwise_cipher=CCMP\ngroup_cipher=CCMP\nkey_mgmt=WPA2-PSK\nwpa_state=COMPLETED\nip_address=192.168.1.20\n'
          b'p2p_device_address=12:34:56:78:9a:bc\naddress=12:34:56:78:9a:bc\nuuid=1234\n')


@pytest.mark.parametrize('state,ws', [('COMPLETED', WS_COMPLETED), ('DISCONNECTED', WS_DISCONNECTED),
                                      ('SCANNING', WS_SCANNING), ('INACTIVE', WS_INACTIVE),
                                      ('INTERFACE_DISABLED', WS_DISABLED), ('DISABLED', WS_DISABLED),
                                      ('ASSOCIATING', WS_JOINING), ('4WAY_HANDSHAKE', WS_JOINING)])
def test_wpa_state(L, state, ws):
    out, got = text(L.wifi_state, f'freq=0\nwpa_state={state}\n'.encode(), size=24)
    assert got == ws and out == state


def test_no_wpa_state(L):
    out, got = text(L.wifi_state, b'FAIL\n', size=24)
    assert got == WS_NONE and out == ''
    assert L.wifi_state(None, None, 0) == WS_NONE


def test_the_status_line_keeps_nothing_private(L):
    out, n = text(L.wifi_status_line, STATUS, C.c_uint(SALT))
    assert out == ('state=COMPLETED freq=5240 mode=station key_mgmt=WPA2-PSK pairwise_cipher=CCMP group_cipher=CCMP '
                   f'wifi_generation=5 id=0 ap={hashed(L, "a0:b1:c2:d3:e4:f5")} ip=1') and n == len(out)
    out, _ = text(L.wifi_status_line, b'wpa_state=SCANNING\n', C.c_uint(SALT))
    assert out == 'state=SCANNING ip=0'
    out, _ = text(L.wifi_status_line, STATUS, C.c_uint(SALT), size=20)
    assert len(out) <= 19


RESULTS = (b'bssid / frequency / signal level / flags / ssid\n'
           b'a0:b1:c2:d3:e4:f5\t5240\t-61\t[WPA2-PSK-CCMP][ESS]\tHomeNet-Private\n'
           b'a0:b1:c2:d3:e4:f6\t2412\t-70\t[WPA2-PSK-CCMP][ESS]\tHomeNet-Private\n'
           b'00:11:22:33:44:55\t2437\t-55\t[ESS]\tNeighbour\n'
           b'00:11:22:33:44:56\t5955\t-80\t[ESS]\t\n'
           b'broken line\n')
NETWORKS = (b'network id / ssid / bssid / flags\n0\tHomeNet-Private\tany\t[CURRENT]\n1\tNeighbour\tany\t[DISABLED]\n')


def scan(L, results, networks):
    s = Scan()
    L.wifi_scan_count(results, networks, C.byref(s))
    return s


def test_the_scan_is_counted_by_band_with_its_strongest_and_the_saved_ones(L):
    s = scan(L, RESULTS, NETWORKS)
    assert (s.n, list(s.band), list(s.best), s.known) == (4, [2, 1, 1], [-55, -61, -80], 2)
    assert scan(L, RESULTS, None).known == 0
    assert scan(L, b'bssid / frequency / signal level / flags / ssid\n', NETWORKS).n == 0
    assert scan(L, b'FAIL\n', NETWORKS).n == -1 and scan(L, None, None).n == -1
    out, n = text(L.wifi_scan_text, C.byref(s))
    assert out == 'nets=4 2.4=2/-55 5=1/-61 6=1/-80 known=2' and n == len(out)
    out, _ = text(L.wifi_scan_text, C.byref(scan(L, b'bssid\n', None)))
    assert out == 'nets=0 2.4=0 5=0 6=0 known=0'
    out, _ = text(L.wifi_scan_text, C.byref(s), size=12)
    assert len(out) <= 11


def event(L, msg):
    reason, local = C.c_int(), C.c_int()
    out = C.create_string_buffer(300)
    e = L.wifi_event(msg.encode(), C.c_uint(SALT), out, 300, C.byref(reason), C.byref(local))
    return e, out.value.decode(), reason.value, local.value


def test_events(L):
    ap = hashed(L, 'a0:b1:c2:d3:e4:f5')
    assert event(L, '<3>CTRL-EVENT-CONNECTED - Connection to a0:b1:c2:d3:e4:f5 completed [id=0 id_str=]') == \
        (WE_CONNECTED, f'ASSOC ap={ap} id=0', -1, 0)
    assert event(L, 'CTRL-EVENT-CONNECTED - Connection to nobody') == (WE_CONNECTED, 'ASSOC', -1, 0)
    assert event(L, '<3>CTRL-EVENT-DISCONNECTED bssid=a0:b1:c2:d3:e4:f5 reason=3 locally_generated=1') == \
        (WE_DISCONNECTED, f'DISASSOC bssid={ap} reason=3 locally_generated=1', 3, 1)
    assert event(L, '<3>CTRL-EVENT-DISCONNECTED bssid=a0:b1:c2:d3:e4:f5 reason=4') == \
        (WE_DISCONNECTED, f'DISASSOC bssid={ap} reason=4', 4, 0)
    assert event(L, '<3>CTRL-EVENT-TERMINATING')[:2] == (WE_TERMINATING, 'WPA terminating')
    assert event(L, '<3>CTRL-EVENT-SCAN-STARTED ')[0] == WE_SCAN_STARTED
    assert event(L, '<3>CTRL-EVENT-SCAN-RESULTS ')[0] == WE_SCAN_RESULTS
    assert event(L, '<3>CTRL-EVENT-SCAN-FAILED ret=-16 retry=1')[:2] == (WE_SCAN_FAILED, 'SCAN-FAILED ret=-16')
    assert event(L, '<3>CTRL-EVENT-ASSOC-REJECT bssid=a0:b1:c2:d3:e4:f5 status_code=17')[:2] == \
        (WE_LOG, f'ASSOC-REJECT bssid={ap} status_code=17')
    assert event(L, '<3>CTRL-EVENT-SSID-TEMP-DISABLED id=0 ssid="HomeNet-Private" auth_failures=1 duration=10 '
                    'reason=WRONG_KEY')[:2] == (WE_LOG, 'TEMP-DISABLED id=0 auth_failures=1 duration=10 reason=WRONG_KEY')
    assert event(L, '<3>CTRL-EVENT-SSID-REENABLED id=0 ssid="HomeNet-Private"')[:2] == (WE_LOG, 'REENABLED id=0')
    assert event(L, '<3>CTRL-EVENT-AUTH-REJECT a0:b1:c2:d3:e4:f5 auth_type=0 auth_transaction=2 status_code=1')[1] \
        == 'AUTH-REJECT auth_type=0 auth_transaction=2 status_code=1'
    assert event(L, '<3>CTRL-EVENT-BEACON-LOSS ')[:2] == (WE_LOG, 'BEACON-LOSS')
    assert event(L, '<3>CTRL-EVENT-REGDOM-CHANGE init=DRIVER type=COUNTRY alpha2=US')[1] == \
        'REGDOM init=DRIVER type=COUNTRY alpha2=US'
    for quiet in ('<3>CTRL-EVENT-NETWORK-NOT-FOUND', '<3>Trying to associate with SSID \'HomeNet-Private\'',
                  '<3>CTRL-EVENT-BSS-ADDED 0 a0:b1:c2:d3:e4:f5', '<3>CTRL-EVENT-SIGNAL-CHANGE above=1 signal=-60'):
        assert event(L, quiet) == (WE_NONE, '', -1, 0)


# ---- the deaf radio ----

class Watcher:
    def __init__(self, L):
        self.L, self.w = L, Watch()

    def __call__(self, now, state, nets=-1, known=0):
        return self.L.wifi_step(C.byref(self.w), C.byref(Obs(now, state, nets, known)))


def test_sixty_seconds_of_empty_scans_start_it_once_repeat_every_five_minutes_and_a_scan_with_networks_ends_it(L):
    w = Watcher(L)
    assert w(1000, WS_SCANNING, 0) == WA_NONE
    assert w(31000, WS_SCANNING, -1) == WA_NONE  # not counted this time: the last count holds
    assert w(60999, WS_SCANNING, 0) == WA_NONE
    assert w(61000, WS_SCANNING, 0) == WA_START and w.w.deaf == DEAF_EMPTY
    assert all(w(61000 + k * 5000, WS_SCANNING, 0) == WA_NONE for k in range(1, 60))
    assert w(361000, WS_SCANNING, 0) == WA_AGAIN and w.w.snaps == 2
    assert w(661000, WS_DISCONNECTED, 0) == WA_AGAIN and w.w.snaps == 3
    assert w(700000, WS_SCANNING, 12, 1) == WA_END and (w.w.end, w.w.lasted) == (END_SCAN, 639000)
    assert w.w.deaf == DEAF_NONE and w(705000, WS_SCANNING, 12, 1) == WA_NONE


def test_networks_seen_but_a_saved_one_not_joined_for_two_minutes_start_it_and_joining_ends_it(L):
    w = Watcher(L)
    assert w(0, WS_DISCONNECTED, 14, 1) == WA_NONE  # time 0 is taken as 1 (0 means none)
    assert w(60000, WS_SCANNING, 13, 1) == WA_NONE
    assert w(90000, WS_JOINING, 13, 1) == WA_NONE  # a failed try is still not joined
    assert w(120001, WS_DISCONNECTED, 12, 1) == WA_START and w.w.deaf == DEAF_NOJOIN
    assert w(130000, WS_DISCONNECTED, 0) == WA_NONE
    assert w(190000, WS_DISCONNECTED, 0) == WA_EMPTY and w.w.deaf == DEAF_EMPTY and w.w.snaps == 2
    assert w(490000, WS_DISCONNECTED, 0) == WA_AGAIN
    assert w(500000, WS_COMPLETED) == WA_END and (w.w.end, w.w.lasted) == (END_ASSOC, 500000 - 120001)


def test_a_scan_without_the_saved_network_restarts_the_count(L):
    w = Watcher(L)
    w(1000, WS_DISCONNECTED, 14, 1)
    assert w(60000, WS_SCANNING, 14, 0) == WA_NONE and w.w.nojoin_since == 0
    assert w(150000, WS_DISCONNECTED, 14, 1) == WA_NONE  # counted again from here
    assert w(270000, WS_DISCONNECTED, 14, 1) == WA_START


def test_no_saved_network_in_range_is_not_a_fault(L):
    w = Watcher(L)
    assert all(w(k * 5000, WS_SCANNING, 9, 0) == WA_NONE for k in range(200))


def test_wifi_off_ends_it_without_a_recovery_and_nothing_to_join_never_starts_it(L):
    w = Watcher(L)
    w(1000, WS_SCANNING, 0), w(61000, WS_SCANNING, 0)
    assert w(70000, WS_NONE) == WA_END and w.w.end == END_OFF
    assert w(80000, WS_NONE) == WA_NONE
    assert all(w(k * 5000, WS_INACTIVE, 0) == WA_NONE for k in range(100))
    assert all(w(k * 5000, WS_DISABLED, 0) == WA_NONE for k in range(100))
    assert w(1000, WS_COMPLETED) == WA_NONE  # joined while not deaf: nothing


def test_a_nojoin_period_with_networks_still_there_goes_on_until_a_join(L):
    w = Watcher(L)
    w(1000, WS_DISCONNECTED, 14, 1)
    assert w(121000, WS_DISCONNECTED, 14, 1) == WA_START
    assert w(130000, WS_DISCONNECTED, 14, 0) == WA_NONE and w.w.deaf == DEAF_NOJOIN  # the saved one missed a scan
    assert w(421000, WS_DISCONNECTED, 14, 1) == WA_AGAIN


# ---- the boot's evidence ----

@pytest.mark.parametrize('msg,keep', [
    ('insmod: vmalloc: allocation failure: 0 bytes, mode:0xcc0(GFP_KERNEL), nodemask=(null)', 2),
    ('Call Trace:', 2), ('Out of memory: Killed process 1', 2), ('WARNING: CPU: 0 PID: 1', 2),
    ('[<c0123456>] _rtw_zvmalloc+0x2c/0x40 [8733bs]', 2), ('rtk_wlan_power_on', 1), ('mmc1: new SDIO card', 1),
    ('RTW: rtw_drv_init', 1), ('usb 1-1: new device', 0)])
def test_kernel_lines_kept(L, msg, keep):
    assert L.wifi_kmsg_keep(msg.encode()) == keep


VMALLOC = (b'0xc0000000-0xc0002000    8192 jz_init+0x1c/0x40 ioremap\n'
           b'0xc0002000-0xc0005000   12288 load_module+0x10/0x20 pages=2 vmalloc\n'
           b'0xc0100000-0xc0110000   65536 _rtw_vmalloc+0x10/0x20 [8733bs] pages=15 vmalloc\n'
           b'not an area\n'
           b'0xc0108000-0xc0109000    4096 overlap+0x0/0x0 vmap\n')


def test_vmalloc_summary_with_its_largest_hole(L):
    v = Vm()
    L.wifi_vmalloc_sum(VMALLOC, C.byref(v))
    assert (v.areas, v.used, v.vmalloc, v.ioremap) == (4, 8192 + 12288 + 65536 + 4096, 12288 + 65536, 8192)
    assert (v.gap, v.lo, v.hi) == (0xc0100000 - 0xc0005000, 0xc0000000, 0xc0110000)
    out, n = text(L.wifi_vm_text, C.byref(v))
    assert out == ('vmalloc areas=4 used=88kB vmalloc=76kB ioremap=8kB largest_gap=1004kB from=0xc0000000 '
                   'to=0xc0110000') and n == len(out)
    L.wifi_vmalloc_sum(b'', C.byref(v))
    assert v.areas == 0


def test_meminfo_brief(L):
    mem = b'MemTotal: 60000 kB\nMemFree:    1234 kB\nMemAvailable: 20000 kB\nCached: 9 kB\nVmallocUsed: 512 kB\n'
    out, n = text(L.wifi_mem_brief, mem)
    assert out == 'MemFree=1234 MemAvailable=20000 Cached=9 VmallocUsed=512' and n == len(out)
    out, _ = text(L.wifi_mem_brief, mem, size=10)
    assert len(out) <= 9


def test_lines_with_words(L):
    irq = b'           CPU0       CPU1\n 31:     123456          0  INTC  jzmmc_v1.2.1\n 40:  7  0  INTC  uart\n' \
          b' 41:\t5\t0  INTC  sdio-irq\n'
    out, n = text(L.wifi_lines_with, irq, b'mmc\0sdio\0\0')
    assert n == 2 and out == '31: 123456 0 INTC jzmmc_v1.2.1 | 41: 5 0 INTC sdio-irq'
    out, n = text(L.wifi_lines_with, irq, b'nothing\0\0')
    assert (out, n) == ('', 0)


def test_a_line_is_time_uptime_and_text_and_a_long_one_is_cut_to_one_line(L):
    def line(wall, up, what, size=600):
        b = C.create_string_buffer(size)
        return b, L.wifi_line(b, size, C.c_long(wall), C.c_uint(up), what)
    b, n = line(1764100000, 42, b'BOOT wlan0=yes')
    assert b.value.decode() == '2025-11-25T19:46:40Z up=42 BOOT wlan0=yes\n' and n == len(b.value)
    b, n = line(0, 1, b'x' * 100, size=40)
    out = b.value.decode()
    assert out.endswith('x\n') and out.count('\n') == 1 and n == 39 == len(out)


def test_the_log_rotates_as_q2_wifi(L, tmp_path):
    """wifi_flush through the shared rotation (q2guard_core.c log_flush): q2-wifi.0, moved on at 512 KB."""
    L.start(str(tmp_path).encode(), 1)
    L.set_card(1)
    assert L.flush_to(str(tmp_path).encode()) == 0
    (tmp_path / 'q2-wifi.0').write_bytes(b'x' * 524288)
    assert L.flush_to(str(tmp_path).encode()) == 0
    assert (tmp_path / 'q2-wifi.1').stat().st_size == 524288
    L.set_card(0)
