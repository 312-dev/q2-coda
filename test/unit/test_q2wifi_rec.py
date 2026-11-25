"""The Wi-Fi fault recorder's device side (patch/q2wifi.c, docs/internals.md#wi-fi-fault-recorder) against a fake
/proc, /sys and /dev/kmsg under PEQ_ROOT and a fake wpa_supplicant: a real Unix datagram socket at WIFI_CTRL, served
by a thread here, that answers STATUS, SCAN_RESULTS, LIST_NETWORKS and SIGNAL_POLL, takes an ATTACH and sends events
to it. The owner's two failures of 2026-11-25 are played: the driver that never came up (no wlan0, a vmalloc failure
in the kernel log), and the radio that went deaf (networks seen and not joined, then 40 minutes of empty scans, then
an association of its own). Each trigger fires once, repeats every 5 minutes, stamps its end; no network name or
address is written; nothing is sent that changes Wi-Fi's state; and with Wi-Fi off or joined it costs no file open."""
import ctypes as C
import os
import pathlib
import re
import socket
import threading

import pytest

from test_q2guard import ROOT
from test_q2wifi import build

SSID, BSSID, OWN = 'HomeNet-Private', 'a0:b1:c2:d3:e4:f5', '12:34:56:78:9a:bc'
HEADER = 'bssid / frequency / signal level / flags / ssid\n'
RESULTS = HEADER + f'{BSSID}\t5240\t-61\t[WPA2-PSK-CCMP][ESS]\t{SSID}\n00:11:22:33:44:55\t2437\t-55\t[ESS]\tNeighbour\n'
NETWORKS = f'network id / ssid / bssid / flags\n0\t{SSID}\tany\t[CURRENT]\n'
READ_ONLY = {'ATTACH', 'STATUS', 'SCAN_RESULTS', 'LIST_NETWORKS', 'SIGNAL_POLL'}


def status(state, freq=0):
    s = f'bssid={BSSID}\nfreq={freq}\nssid={SSID}\nid=0\nmode=station\nkey_mgmt=WPA2-PSK\nwpa_state={state}\n'
    return s + f'address={OWN}\nuuid=0b7c\n' + ('ip_address=192.168.1.20\n' if state == 'COMPLETED' else '')


class Wpa(threading.Thread):
    """wpa_supplicant's control socket: what each command answers, the monitor it was attached to, and every command
    it got."""

    def __init__(self, path):
        super().__init__(daemon=True)
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        self.sock.bind(path)
        self.sock.settimeout(0.05)
        self.path, self.mon, self.got, self.stop = path, None, [], False
        self.answers = {'STATUS': status('DISCONNECTED'), 'SCAN_RESULTS': HEADER, 'LIST_NETWORKS': NETWORKS,
                        'SIGNAL_POLL': 'RSSI=-58\nLINKSPEED=433\nNOISE=9999\nFREQUENCY=5240\n'}
        self.start()

    def run(self):
        while not self.stop:
            try:
                data, addr = self.sock.recvfrom(4096)
            except (socket.timeout, OSError):
                continue
            cmd = data.decode()
            self.got.append(cmd)
            if cmd == 'ATTACH':
                self.mon = addr
                self.sock.sendto(b'OK\n', addr)
            else:
                self.sock.sendto(self.answers.get(cmd, 'UNKNOWN COMMAND\n').encode(), addr)

    def event(self, msg):
        self.sock.sendto(f'<3>{msg}'.encode(), self.mon)

    def close(self):
        self.stop = True
        self.join()
        self.sock.close()


KMSG = [
    '6,1,1000,-;Linux version 5.10.21',
    '6,2,2500000,-;mmc1: new high speed SDIO card at address 0001',
    '6,3,3000000,-;usb 1-1: not ours',
    ' SUBSYSTEM=mmc',
    f'6,4,9000000,-;RTW: rtk_wlan_power_on mac={OWN}',
    '4,5,19000123,-;insmod: vmalloc: allocation failure: 0 bytes, mode:0xcc0(GFP_KERNEL), nodemask=(null)',
    '4,6,19000200,-;CPU: 0 PID: 120 Comm: insmod Not tainted 5.10.21',
    '4,7,19000300,-;Call Trace:',
    '4,8,19000400,-;[<80123456>] _rtw_zvmalloc+0x2c/0x40 [8733bs]',
    '4,9,19000500,-;[<80123457>] _rtw_init_mlme_priv+0x10/0x200 [8733bs]',
    '4,10,19000600,-;Mem-Info:',
    '4,11,19000700,-;Normal: 4*4kB 2*8kB = 32kB',
    '6,12,25000000,-;random: crng init done',
]


@pytest.fixture
def rig(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # WIFI_CTRL is "w.sock" in the working directory: a short socket path
    root = tmp_path / 'root'
    files = {
        'sys/class/net/wlan0/operstate': 'dormant\n',
        'proc/modules': 'snd 1 0 - Live 0x0\n8733bs 2101248 0 - Live 0xc0400000\n',
        'proc/sys/kernel/random/boot_id': '0b7c3a1e-boot\n',
        'proc/meminfo': 'MemTotal: 59000 kB\nMemFree: 4100 kB\nMemAvailable: 21000 kB\nCached: 15000 kB\n'
                        'Slab: 3000 kB\nVmallocUsed: 900 kB\n',
        'proc/buddyinfo': 'Node 0, zone   Normal    4    2    0    0\n',
        'proc/vmallocinfo': '0xc0000000-0xc0002000 8192 a+0x0/0x0 ioremap\n0xc0100000-0xc0101000 4096 b+0x0/0x0 vmalloc\n',
        'proc/interrupts': '      CPU0  CPU1\n 31:  123456  0  INTC  jzmmc_v1.2.1\n 40: 7 0 INTC uart\n',
        'proc/net/wireless': 'Inter-| sta-|\n face | tus |\n wlan0: 0000   0.  -61.  -256  0 0 0 0 0 0\n',
        'proc/net/dev': 'Inter-|\n face |\n  wlan0: 1000 10 0 0 0 0 0 0 2000 20 0 0 0 0 0 0\n    lo: 0 0\n',
        'proc/net/rtl8733bs/wlan0/fwstate': 'fwstate=0x8\n',
        'proc/net/rtl8733bs/wlan0/rx_signal': f'rssi:-61\nbssid={BSSID}\n',
        'proc/net/rtl8733bs/wlan0/trx_info': 'free_xmitbuf_cnt=8\n',
        'proc/net/rtl8733bs/wlan0/disconnect_info': 'last disconnect reason: Receiving Deauth frame from AP to cause '
                                                     'disconnection.\ndisconnect code = 3\n',
        'proc/net/rtl8733bs/wlan0/ap_info': f'SSID={SSID}\nbssid={BSSID}\n',  # never read: it names the network
        'proc/net/rtl8733bs/wlan0/scan_abort': '',  # never read: reading it aborts a scan
        'sys/class/power_supply/battery/temp': '77\n',
        'dev/kmsg': ''.join(r + '\n' for r in KMSG),
    }
    for name, body in files.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(body)
    L = build(tmp_path / 'q2wifi.so', root)
    for f in ('log_ram', 'ulog_ram', 'sent_at'):
        getattr(L, f).restype = C.c_char_p
    L.tick.argtypes = [C.c_uint, C.c_int, C.c_char_p]

    class Rig:
        pass
    r = Rig()
    r.L, r.root, r.wpa = L, root, None

    def start(again=0):
        L.start(str(tmp_path).encode(), again)
    r.start = start

    def up():
        r.wpa = Wpa('w.sock')
        return r.wpa
    r.up = up

    def down():
        r.wpa.close()
        os.unlink('w.sock')  # the socket file wpa_supplicant removes as it ends
        r.wpa = None
    r.down = down
    r.log = lambda: L.log_ram()[:L.log_used()].decode()
    r.ulog = lambda: L.ulog_ram()[:L.ulog_used()].decode()
    r.tick = lambda ms: L.tick(ms, 412, b'2026-11-25T20:31:00Z up=5 t=41.2 g=ok\n')
    r.sent = lambda: [L.sent_at(i).decode() for i in range(L.sent_count())]
    yield r
    if r.wpa:
        r.wpa.close()


def lines(log, word):
    return [l for l in log.splitlines() if re.match(rf'\S+ up=\d+ {word}', l)]


def private(r):
    for text in (r.log(), r.ulog()):
        assert SSID not in text and 'Neighbour' not in text
        assert BSSID not in text and OWN not in text and '192.168' not in text


# ---- the boot ----

def test_wlan0_there_at_30_s_is_one_line_once(rig):
    rig.start()
    for ms in range(1000, 31000, 5000):
        rig.tick(ms)
    assert not lines(rig.log(), 'BOOT')
    rig.tick(31000)
    rig.tick(36000)
    assert [l.split(' ', 1)[1] for l in lines(rig.log(), 'BOOT')] == \
        ['up=31 BOOT wlan0=yes after=30 s module=8733bs 2101248 0 - Live 0xc0400000']
    assert not lines(rig.log(), 'K') and rig.L.flush_due() == 1 and rig.L.flush_due() == 0


def test_no_wlan0_at_30_s_records_the_kernel_memory_and_temperature_once(rig):
    (rig.root / 'sys/class/net/wlan0/operstate').unlink()
    (rig.root / 'sys/class/net/wlan0').rmdir()
    (rig.root / 'proc/modules').write_text('snd 1 0 - Live 0x0\n')
    rig.start()
    rig.tick(1000), rig.tick(31000), rig.tick(36000)
    log = rig.log()
    assert len(lines(log, 'BOOT')) == 1 and 'BOOT wlan0=no after=30 s module=none' in log
    kernel = [l.split(' K ', 1)[1] for l in lines(log, 'K')]
    assert kernel[0] == '[2.500000] mmc1: new high speed SDIO card at address 0001'
    assert re.fullmatch(r'\[9\.000000\] RTW: rtk_wlan_power_on mac=#[0-9a-f]{6}', kernel[1])
    assert '[19.000123] insmod: vmalloc: allocation failure: 0 bytes, mode:0xcc0(GFP_KERNEL), nodemask=(null)' in kernel
    assert '[19.000400] [<80123456>] _rtw_zvmalloc+0x2c/0x40 [8733bs]' in kernel
    assert '[19.000700] Normal: 4*4kB 2*8kB = 32kB' in kernel  # the failure's memory dump, kept with it
    assert not any('usb 1-1' in k or 'Linux version' in k or 'SUBSYSTEM' in k for k in kernel)
    assert kernel[-1].startswith('read 12 records')
    assert 'F meminfo MemFree: 4100 kB' in log
    assert 'F buddyinfo Node 0, zone   Normal    4    2    0    0' in log
    assert 'F vmalloc areas=2 used=12kB vmalloc=4kB ioremap=8kB largest_gap=1016kB' in log
    assert 'F irq 31: 123456 0 INTC jzmmc_v1.2.1' in log
    assert 'F mem MemFree=4100 MemAvailable=21000 Cached=15000 Slab=3000 VmallocUsed=900' in log
    assert 'F temp t=412 raw=77 heat=up=5 t=41.2 g=ok' in log
    assert 'W wifi no wlan0 30 s after start' in rig.ulog()
    private(rig)
    rig.tick(41000)
    assert len(lines(rig.log(), 'K')) == len(kernel)  # once
    (rig.root / 'sys/class/net/wlan0').mkdir()
    rig.tick(46000)
    assert 'BOOT wlan0 appeared after=45 s' in rig.log()


def test_a_q2guard_started_again_does_not_check_the_boot(rig):
    rig.start(again=1)
    rig.tick(1000), rig.tick(40000)
    assert not lines(rig.log(), 'BOOT')


# ---- the deaf radio: failure 2 of 2026-11-25 ----

def play(rig, start, end, every=5000):
    for ms in range(start, end, every):
        rig.tick(ms)


def test_the_deaf_radio_of_2026_11_25(rig):
    """Networks seen, the saved one among them, not joined for 3 minutes; then every scan empty for 40 minutes; then
    it associated on its own. One period: nojoin at 2 min, empty at 3 min, a snapshot every 5 min, its end stamped."""
    rig.start(again=1)
    wpa = rig.up()
    wpa.answers['SCAN_RESULTS'] = RESULTS
    rig.tick(1000)
    assert rig.L.fd() >= 0 and wpa.mon  # attached
    assert lines(rig.log(), 'WPA up')
    play(rig, 6000, 121000)
    assert not lines(rig.log(), 'DEAF')
    rig.tick(121000)
    log = rig.log()
    assert [l.split(' up=', 1)[1] for l in lines(log, 'DEAF')] == \
        ['121 DEAF start kind=nojoin after=120 s state=DISCONNECTED nets=2 2.4=1/-55 5=1/-61 6=0 known=1']
    assert 'W wifi deaf radio (nojoin)' in rig.ulog()
    ap = re.search(r'ap=(#[0-9a-f]{6})', log).group(1)
    assert f'SNAP status state=DISCONNECTED freq=0 mode=station key_mgmt=WPA2-PSK id=0 ap={ap} ip=0' in log
    assert 'SNAP scan nets=2 2.4=1/-55 5=1/-61 6=0 known=1 scans=0 failed=0' in log
    assert re.search(r'F drvdir( \S+)+', log) and 'F fwstate fwstate=0x8' in log
    assert f'F rx_signal bssid={ap}' in log  # the driver's addresses hashed as wpa_supplicant's are
    assert 'F trx_info free_xmitbuf_cnt=8' in log and 'F irq 31: 123456 0 INTC jzmmc_v1.2.1' in log
    assert 'F wireless wlan0: 0000 0. -61. -256 0 0 0 0 0 0' in log and 'F netdev wlan0: 1000 10' in log
    assert 'F temp t=412' in log
    # the scans empty: 60 s later the period says so, with a snapshot
    wpa.answers['SCAN_RESULTS'] = HEADER
    for ms in range(126000, 191000, 5000):
        wpa.event('CTRL-EVENT-SCAN-STARTED ')
        wpa.event('CTRL-EVENT-SCAN-RESULTS ')
        rig.tick(ms)
    assert [l.split(' DEAF ', 1)[1] for l in lines(rig.log(), 'DEAF')][1:] == \
        ['empty n=2 for=65 s state=DISCONNECTED nets=0 2.4=0 5=0 6=0 known=0']
    assert 'SNAP scan nets=0 2.4=0 5=0 6=0 known=0 scans=13 failed=0' in rig.log()
    # 40 minutes of it: a snapshot every 5 minutes, no more
    play(rig, 191000, 191000 + 40 * 60000)
    deaf = [l.split(' DEAF ', 1)[1].split(' state')[0] for l in lines(rig.log(), 'DEAF')]
    assert deaf == ['start kind=nojoin after=120 s', 'empty n=2 for=65 s'] + \
        [f'again n={n} for={65 + 300 * (n - 2)} s' for n in range(3, 11)]
    assert rig.log().count('SNAP status') == 10
    # it associates on its own: the association line, and the period's end with a last snapshot
    wpa.answers['STATUS'] = status('COMPLETED', 5240)
    t = 191000 + 40 * 60000
    rig.tick(t)  # the tick asks STATUS: joined
    end = lines(rig.log(), 'DEAF end')
    assert len(end) == 1 and f'DEAF end by=assoc lasted={(t - 121000) // 1000} s state=COMPLETED' in end[0]
    assert 'SNAP signal RSSI=-58 LINKSPEED=433 NOISE=9999 FREQUENCY=5240' in rig.log()
    assert 'I wifi deaf radio ended (assoc)' in rig.ulog()
    play(rig, t + 5000, t + 600000)
    assert len(lines(rig.log(), 'DEAF')) == 11
    private(rig)


def test_empty_scans_alone_start_it_and_the_first_scan_with_networks_ends_it(rig):
    rig.start(again=1)
    wpa = rig.up()
    play(rig, 1000, 61000)
    assert not lines(rig.log(), 'DEAF')
    rig.tick(61000)
    assert 'DEAF start kind=empty after=60 s state=DISCONNECTED nets=0' in rig.log()
    wpa.answers['SCAN_RESULTS'] = RESULTS
    wpa.event('CTRL-EVENT-SCAN-RESULTS ')
    rig.tick(66000)
    assert 'DEAF end by=scan lasted=5 s state=DISCONNECTED nets=2' in rig.log()
    assert rig.log().count('SNAP status') == 2


def test_wifi_switched_off_while_deaf_ends_it_without_a_snapshot(rig):
    rig.start(again=1)
    rig.up()
    play(rig, 1000, 66000)
    snaps = rig.log().count('SNAP status')
    rig.down()
    rig.tick(71000)
    assert 'WPA gone' in rig.log() and 'DEAF end by=off lasted=10 s state=-' in rig.log()
    assert rig.log().count('SNAP status') == snaps and rig.L.fd() == -1


def test_no_saved_network_in_range_records_nothing(rig):
    rig.start(again=1)
    wpa = rig.up()
    wpa.answers['SCAN_RESULTS'] = HEADER + '00:11:22:33:44:55\t2437\t-55\t[ESS]\tNeighbour\n'
    play(rig, 1000, 3600000, 30000)
    assert not lines(rig.log(), 'DEAF')


# ---- associations ----

def test_each_association_and_disassociation_is_a_line_with_its_reasons(rig):
    rig.start(again=1)
    wpa = rig.up()
    rig.tick(1000)
    wpa.answers['STATUS'] = status('COMPLETED', 5240)
    wpa.event(f'CTRL-EVENT-CONNECTED - Connection to {BSSID} completed [id=0 id_str=]')
    rig.L.set_clock(4000)
    rig.L.drain()
    wpa.answers['STATUS'] = status('DISCONNECTED')
    wpa.event(f'CTRL-EVENT-DISCONNECTED bssid={BSSID} reason=3 locally_generated=1')
    rig.L.set_clock(124000)
    rig.L.drain()
    wpa.event(f'CTRL-EVENT-CONNECTED - Connection to {BSSID} completed [id=0 id_str=]')
    rig.L.set_clock(134000)
    rig.L.drain()
    ap = re.search(r'ap=(#[0-9a-f]{6})', rig.log()).group(1)
    assert [l.split(' up=', 1)[1] for l in lines(rig.log(), '(ASSOC|DISASSOC)')] == [
        f'4 ASSOC ap={ap} id=0 freq=5240',
        f'124 DISASSOC bssid={ap} reason=3 locally_generated=1 up=120 s drv=last disconnect reason: Receiving Deauth '
        'frame from AP to cause disconnection. | disconnect code = 3',
        f'134 ASSOC ap={ap} id=0 freq=0 down=10 s']
    wpa.event(f'CTRL-EVENT-SSID-TEMP-DISABLED id=0 ssid="{SSID}" auth_failures=1 duration=10 reason=WRONG_KEY')
    wpa.event('CTRL-EVENT-SCAN-FAILED ret=-16')
    wpa.event('CTRL-EVENT-SCAN-FAILED ret=-16')
    wpa.event('CTRL-EVENT-TERMINATING')
    rig.L.drain()
    log = rig.log()
    assert 'TEMP-DISABLED id=0 auth_failures=1 duration=10 reason=WRONG_KEY' in log
    assert log.count('SCAN-FAILED ret=-16') == 1  # the first of a run
    assert 'WPA terminating' in log and rig.L.fd() == -1
    private(rig)


# ---- what it never does, and what it costs ----

def test_only_read_only_commands_are_ever_sent(rig):
    """Every test's traffic goes through this rig; here a whole day of it: off, on, deaf, joined, dropped."""
    rig.start()
    play(rig, 1000, 40000)
    wpa = rig.up()
    play(rig, 40000, 800000)
    wpa.answers['STATUS'] = status('COMPLETED', 2412)
    wpa.event(f'CTRL-EVENT-CONNECTED - Connection to {BSSID} completed [id=0 id_str=]')
    play(rig, 800000, 1000000)
    wpa.event(f'CTRL-EVENT-DISCONNECTED bssid={BSSID} reason=4')
    rig.down()
    play(rig, 1000000, 1100000)
    assert set(rig.sent()) <= READ_ONLY and set(wpa.got) <= READ_ONLY
    assert {'ATTACH', 'STATUS', 'SCAN_RESULTS', 'LIST_NETWORKS', 'SIGNAL_POLL'} == set(wpa.got)


def test_the_source_runs_nothing_and_writes_no_file(rig):
    src = (ROOT / 'patch/q2wifi.c').read_text() + (ROOT / 'patch/q2wifi_core.c').read_text()
    for call in ('system(', 'execl(', 'fork(', 'popen(', 'O_WRONLY', 'O_RDWR', 'ioctl(', 'unlink(', 'rename('):
        assert call not in src, call
    sent = set(re.findall(r'ask\("([A-Z_]+)"', src)) | set(re.findall(r'send\(s, "([A-Z_]+)"', src))
    assert sent == READ_ONLY


def test_wifi_off_and_joined_cost_no_file_open_and_joined_one_status_a_minute(rig):
    rig.start()
    play(rig, 1000, 40000)  # past the boot check
    opens = rig.L.open_count()
    play(rig, 40000, 400000)  # Wi-Fi off: no socket
    assert rig.L.open_count() == opens and rig.L.sent_count() == 0
    wpa = rig.up()
    wpa.answers['STATUS'] = status('COMPLETED', 5240)
    rig.tick(400000)
    sent = rig.L.sent_count()
    opens = rig.L.open_count()
    play(rig, 405000, 460000)
    assert rig.L.sent_count() == sent and rig.L.open_count() == opens
    play(rig, 460000, 520000)
    assert rig.sent()[sent:] == ['STATUS'] and rig.L.open_count() == opens


def test_nothing_to_join_is_asked_once_a_minute(rig):
    rig.start(again=1)
    wpa = rig.up()
    wpa.answers['STATUS'] = status('INACTIVE')
    play(rig, 1000, 121000)
    assert wpa.got.count('STATUS') == 2 and 'SCAN_RESULTS' not in wpa.got


def test_the_release_build_has_it(rig):
    """q2guard, in release and --dev builds alike, is built from the recorder's two files (tools/build.py GUARD)."""
    build_py = (ROOT / 'tools/build.py').read_text()
    line = next(l for l in build_py.splitlines() if 'GUARD: (HELPER_LIKE, compile_helper' in l)
    assert "'q2wifi', 'q2wifi_core'" in line and 'if dev' not in line
    guard = (ROOT / 'patch/q2guard.c').read_text()
    assert 'wifi_tick(' in guard and 'wifi_flush(&wifilog' in guard and 'if (dev_build) wifi' not in guard
