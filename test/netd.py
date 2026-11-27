#!/usr/bin/env python3
"""q2netd (netd/q2netd.c, docs/netd.md) built for this machine, with a stand-in for wpa_supplicant on its control socket
and commands standing in for ifconfig and udhcpc: the radio on with saved networks and off without, DHCP once
associated, Smart Wi-Fi's idle off and what holds the radio (the boot hold, wake, hold, the Wi-Fi adbd's use mark,
smart off), the saved networks edited in stock's file with the rest of it kept, scans, network time (a stand-in ntpd and
q2sysd), and Q2NET/1. Run it as
python3 test/netd.py (pytest). Q2NETD_SOURCE builds another copy, to show a broken one fails here."""
import os
import pathlib
import socket
import subprocess
import sys
import threading
import time

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import conformance  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
SOURCE = pathlib.Path(os.environ.get('Q2NETD_SOURCE') or ROOT/'netd/q2netd.c')
CONF = 'ctrl_interface=/var/run/wpa_supplicant\nupdate_config=0\n\nnetwork={\n\tssid="Home"\n\tpsk="secret123"\n}\n'

FAKE_WPA = r'''
import os, socket, sys, time
root = sys.argv[1]
if os.path.exists(root + '/wpa-delay'):  # a slow start: the control socket made this many seconds late
    time.sleep(float(open(root + '/wpa-delay').read()))
d = root + '/var/run/wpa_supplicant'
os.makedirs(d, exist_ok=True)
path = d + '/wlan0'
try: os.unlink(path)
except FileNotFoundError: pass
s = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
s.bind(path)
open(root + '/wpa-started', 'a').write(' '.join(sys.argv[2:]) + '\n')
while True:
    data, peer = s.recvfrom(4096)
    cmd = data.decode()
    open(root + '/wpa-log', 'a').write(cmd + '\n')
    if cmd == 'STATUS':
        ip = 'ip_address=192.168.1.5\n' if os.path.exists(root + '/dhcp-ran') else ''
        state = open(root + '/wpa-state').read().strip() if os.path.exists(root + '/wpa-state') else 'COMPLETED'
        out = f'wpa_state={state}\nssid=Home\n' + ip
    elif cmd == 'SCAN_RESULTS':
        out = ('bssid / frequency / signal level / flags / ssid\n'
               '00:11:22:33:44:55\t2412\t-40\t[WPA2-PSK-CCMP][ESS]\tMy Net\n'
               '66:77:88:99:aa:bb\t2437\t-70\t[ESS]\tCafe=Free\n')
    else:
        out = 'OK\n'
    s.sendto(out.encode(), peer)
'''

# ntpd -q's stand-in: logs the server it was given; exits 0 for the one named in ntp-answer, hangs with ntp-hang
FAKE_NTP = '''echo "$2" >> "$1/ntp-log"
[ -f "$1/ntp-hang" ] && exec sleep 1000
[ -f "$1/ntp-answer" ] && [ "$(cat "$1/ntp-answer")" = "$2" ] && exit 0
exit 1
'''


@pytest.fixture(scope='session')
def binary(tmp_path_factory):
    out = tmp_path_factory.mktemp('netd')
    src = out/'q2netd.c'
    src.write_bytes(SOURCE.read_bytes())
    subprocess.run(['cc', '-std=c11', '-Wall', '-Wextra', '-Werror', '-O2', '-I', str(conformance.PERM), '-o', str(out/'q2netd'), str(src)], check=True)
    (out/'fake_wpa.py').write_text(FAKE_WPA)
    (out/'fake_ntp.sh').write_text(FAKE_NTP)
    return out


def start(binary, root, *extra, conf=CONF):
    for d in ('mnt/data', 'tmp', 'run'):
        (root/d).mkdir(parents=True, exist_ok=True)
    if not (root/'etc/group').exists():
        conformance.accounts(root)
    if conf is not None:
        (root/'mnt/data/wpa_supplicant.conf').write_text(conf)
    cmd = [str(binary/'q2netd'), '--root', str(root), '--wpa', f'exec python3 {binary}/fake_wpa.py {root} -iwlan0',
           '--dhcp', f'touch {root}/dhcp-ran; exec sleep 1000', '--up', f'echo up >> {root}/ifconfig',
           '--down', f'echo down >> {root}/ifconfig', '--ntp', f'exec sh {binary}/fake_ntp.sh {root}', '--idle', '3', *extra]
    p = subprocess.Popen(cmd, stderr=subprocess.PIPE)
    end = time.monotonic() + 5
    while not (root/'run/vamp/net.sock').exists() and time.monotonic() < end:
        time.sleep(0.05)
    return p


class Api:
    def __init__(self, root, hello='Q2NET/1'):
        self.s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.s.connect(str(root/'run/vamp/net.sock'))
        self.s.settimeout(8)
        self.f = self.s.makefile('rw')
        self.say(hello)

    def say(self, line):
        self.f.write(line + '\n')
        self.f.flush()

    def line(self):
        return self.f.readline().rstrip('\n')

    def until_ok(self):
        out = []
        while (l := self.line()) not in ('ok', '') and not l.startswith('err'):
            out.append(l)
        return out, l


def status(a):
    a.say('status')
    words = a.line().split()
    return dict(w.split('=', 1) for w in words[1:])


def wait_for(pred, s=8):
    end = time.monotonic() + s
    while time.monotonic() < end and not pred():
        time.sleep(0.1)
    return pred()


@pytest.fixture
def run(binary, tmp_path):
    procs = []

    def go(*extra, **kw):
        procs.append(start(binary, tmp_path, *extra, **kw))
        return tmp_path
    yield go
    for p in procs:
        p.terminate()
        assert p.wait(10) == 0, 'q2netd ends on SIGTERM'


def test_saved_networks_turn_the_radio_on_and_dhcp_follows_association(run, tmp_path):
    (tmp_path/'wpa-state').write_text('SCANNING')
    root = run('--boot-hold', '60')
    a = Api(root)
    assert a.line() == 'Q2NET/1 ok'
    assert wait_for(lambda: status(a)['state'] == 'scanning')
    time.sleep(3.5)
    assert not (root/'dhcp-ran').exists(), 'no udhcpc before association'
    (root/'wpa-state').write_text('COMPLETED')
    assert wait_for(lambda: (root/'dhcp-ran').exists()), 'udhcpc once associated'
    assert (root/'ifconfig').read_text().split()[0] == 'up' and 'COMPLETED' not in (root/'wpa-started').read_text()
    assert wait_for(lambda: status(a)['state'] == 'connected')
    st = status(a)
    assert (st['ssid'], st['ip']) == ('Home', '192.168.1.5')


def test_no_saved_network_leaves_the_radio_off(run):
    root = run(conf=None)
    a = Api(root)
    assert a.line() == 'Q2NET/1 ok'
    time.sleep(1.5)
    assert status(a)['state'] == 'off' and not (root/'wpa-started').exists()


def test_smart_wifi_turns_it_off_when_nothing_holds_it_and_holds_keep_it(run):
    root = run('--boot-hold', '2')
    a = Api(root)
    a.line()
    assert wait_for(lambda: status(a)['state'] != 'off', 4)
    assert wait_for(lambda: status(a)['state'] == 'off', 6), 'off once the boot hold has run out'
    assert (root/'ifconfig').read_text().split()[-1] == 'down'
    a.say('wake')
    assert a.line() == 'ok' and status(a)['state'] != 'off'
    a.say('hold 8')
    assert a.line() == 'ok'
    time.sleep(5)
    assert status(a)['state'] != 'off', 'a hold past the idle time keeps it on'
    assert wait_for(lambda: status(a)['state'] == 'off', 8)
    (root/'tmp/q2remote.used.new').write_text('1')
    os.rename(root/'tmp/q2remote.used.new', root/'tmp/q2remote.used')  # a computer let in over Wi-Fi
    assert wait_for(lambda: status(a)['state'] != 'off', 4) and int(status(a)['held']) > 1700


def test_smart_off_keeps_it_on(run):
    root = run('--boot-hold', '1')
    a = Api(root)
    a.line()
    a.say('smart off')
    assert a.line() == 'ok'
    time.sleep(4)
    assert status(a)['state'] != 'off'
    a.say('off')
    assert a.line() == 'ok' and status(a)['state'] == 'off'


def test_saved_networks_are_edited_in_stock_s_file(run):
    root = run('--boot-hold', '60')
    a = Api(root)
    a.line()
    a.say('networks')
    assert a.until_ok() == (['network ssid=Home'], 'ok')
    a.say('add Caf%C3%A9%20Two pass%20word9')
    assert a.line() == 'ok'
    text = (root/'mnt/data/wpa_supplicant.conf').read_text()
    assert text == CONF + 'network={\n\tssid="Café Two"\n\tpsk="pass word9"\n}\n'
    assert wait_for(lambda: 'RECONFIGURE' in (root/'wpa-log').read_text())
    a.say('add Home secret123')
    assert a.line() == 'err saved already'
    for bad in ('add Bad%22Quote password1', 'add Short abc', 'add ' + 'x' * 33 + ' password1'):
        a.say(bad)
        assert a.line() == 'err network', bad
    a.say('forget Home')
    assert a.line() == 'ok'
    text = (root/'mnt/data/wpa_supplicant.conf').read_text()
    assert text == 'ctrl_interface=/var/run/wpa_supplicant\nupdate_config=0\n\nnetwork={\n\tssid="Café Two"\n\tpsk="pass word9"\n}\n'
    a.say('forget Nowhere')
    assert a.line() == 'err no such network'


def test_a_scan_s_networks_with_their_names_encoded(run):
    root = run('--boot-hold', '60')
    a = Api(root)
    a.line()
    assert wait_for(lambda: (root/'wpa-started').exists())
    a.say('scan')
    assert a.until_ok() == (['ap ssid=My%20Net signal=-40 secure=1', 'ap ssid=Cafe%3DFree signal=-70 secure=0'], 'ok')


@pytest.mark.parametrize('delay, answer', [(1.5, 'ok'), (8, 'err scan')])
def test_a_scan_as_the_radio_comes_on_waits_for_wpa_supplicant(run, tmp_path, delay, answer):
    """wpa_supplicant's control socket comes some time after it starts: a scan then waits for it (WPA_READY_MS), and
    one that never comes is answered err scan"""
    (tmp_path/'wpa-delay').write_text(str(delay))
    root = run('--boot-hold', '60')
    a = Api(root)
    a.line()
    a.say('scan')
    start = time.monotonic()
    lines, last = a.until_ok() if answer == 'ok' else ([], a.line())
    assert last == answer
    if answer == 'ok':
        assert lines == ['ap ssid=My%20Net signal=-40 secure=1', 'ap ssid=Cafe%3DFree signal=-70 secure=0']
    else:
        assert 4.5 < time.monotonic() - start < 7, 'given up after 5 s'


@pytest.fixture
def outer(tmp_path):
    """today's Vamp's wpa_supplicant on a card boot: a process of that name (sleep under another name)"""
    exe = tmp_path/'outer/wpa_supplicant'
    exe.parent.mkdir()
    exe.write_bytes(pathlib.Path('/bin/sleep').read_bytes())
    exe.chmod(0o755)
    p = subprocess.Popen([str(exe), '1000'])
    (tmp_path/'proc').symlink_to('/proc')
    yield p
    p.kill()
    p.wait()


def test_a_card_boot_leaves_today_s_wifi_exactly_as_it_is(run, outer):
    root = run('--card-boot', '--boot-hold', '1')
    a = Api(root)
    a.line()
    time.sleep(5)  # past the boot hold and the idle time
    st = status(a)
    assert st['state'] == 'external' and st['smart'] == '0'
    assert not (root/'wpa-started').exists() and not (root/'ifconfig').exists() and not (root/'dhcp-ran').exists()
    for line in ('off', 'smart on'):
        a.say(line)
        assert a.line() == 'err card boot'
    a.say('wake')
    assert a.line() == 'ok' and not (root/'ifconfig').exists(), 'a wake starts nothing beside it'
    assert outer.poll() is None, "today's wpa_supplicant still runs"


def test_a_card_boot_with_no_wifi_brings_it_up_and_keeps_it_up(run, tmp_path):
    (tmp_path/'proc').symlink_to('/proc')
    root = run('--card-boot', '--boot-hold', '1')
    a = Api(root)
    a.line()
    assert wait_for(lambda: (root/'wpa-started').exists())
    time.sleep(5)
    assert status(a)['state'] not in ('off', 'external') and 'down' not in (root/'ifconfig').read_text()
    a.say('off')
    assert a.line() == 'err card boot'


def test_another_version_is_refused(run):
    root = run(conf=None)
    a = Api(root, 'Q2NET/2')
    assert a.line() == 'err version'


# ---- network time (docs/netd.md#network-time) ----

class FakeSysd:
    """q2sysd's socket: Q2SYS/1's hello, `time save` answered ok, every line logged"""
    def __init__(self, root):
        (root/'run/vamp').mkdir(parents=True, exist_ok=True)
        self.lines = []
        self.s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.s.bind(str(root/'run/vamp/sys.sock'))
        self.s.listen(4)
        threading.Thread(target=self.serve, daemon=True).start()

    def serve(self):
        while True:
            c, _ = self.s.accept()
            f = c.makefile('rw')
            for line in f:
                line = line.rstrip('\n')
                self.lines.append(line)
                f.write('Q2SYS/1 ok\n' if line == 'Q2SYS/1' else 'ok\n')
                f.flush()
            c.close()


def ntp_log(root):
    p = root/'ntp-log'
    return p.read_text().split() if p.exists() else []


def time_status(a):
    a.say('time')
    words = a.line().split()
    assert words[0] == 'time', words
    return dict(w.split('=', 1) for w in words[1:])


STOCK_ORDER = ['time.nist.gov', 'time.apple.com', '0.pool.ntp.org', 'time.windows.com']


def test_a_connect_syncs_from_the_first_server_that_answers_then_writes_the_rtc(run, tmp_path):
    """networktime_adjust_thd: stock's servers in turn until one answers, then toolsSetRtcTime (q2sysd's time save)"""
    (tmp_path/'ntp-answer').write_text('0.pool.ntp.org')
    (tmp_path/'wpa-state').write_text('SCANNING')
    sysd = FakeSysd(tmp_path)
    root = run('--boot-hold', '60')
    a = Api(root)
    assert a.line() == 'Q2NET/1 ok'
    a.say('watch')
    assert a.line() == 'ok'
    time.sleep(1.5)
    assert ntp_log(root) == [], 'nothing before the connect'
    (root/'wpa-state').write_text('COMPLETED')
    assert wait_for(lambda: 'time save' in sysd.lines), sysd.lines
    assert ntp_log(root) == STOCK_ORDER[:3]
    while (l := a.line()).startswith(('status', 'radio')):
        pass
    assert l == 'time synced server=0.pool.ntp.org'
    st = time_status(a)
    assert (st['auto'], st['state'], st['server']) == ('1', 'idle', '0.pool.ntp.org') and int(st['synced']) > 1.7e9
    time.sleep(2.5)
    assert ntp_log(root) == STOCK_ORDER[:3] and sysd.lines.count('time save') == 1, 'one round a connect'
    (root/'wpa-state').write_text('SCANNING')
    assert wait_for(lambda: status(a)['state'] == 'scanning')
    (root/'wpa-state').write_text('COMPLETED')
    assert wait_for(lambda: sysd.lines.count('time save') == 2), 'a round at the next connect'
    assert ntp_log(root) == STOCK_ORDER[:3] * 2


def test_no_server_answering_still_writes_the_rtc(run, tmp_path):
    """stock writes the RTC after the round whether a server answered or not"""
    sysd = FakeSysd(tmp_path)
    root = run('--boot-hold', '60')
    a = Api(root)
    assert a.line() == 'Q2NET/1 ok'
    a.say('watch')
    assert a.line() == 'ok'
    assert wait_for(lambda: 'time save' in sysd.lines), sysd.lines
    assert ntp_log(root) == STOCK_ORDER
    while (l := a.line()).startswith(('status', 'radio')):
        pass
    assert l == 'time failed'
    assert time_status(a)['synced'] == '0'
    time.sleep(2.5)
    assert ntp_log(root) == STOCK_ORDER and sysd.lines.count('time save') == 1, 'one round a connect, answered or not'


@pytest.mark.parametrize('value', ['0', '2'])
def test_set_time_automatically_off_asks_no_server_and_turning_it_on_does(run, tmp_path, value):
    """config_init: AUTOSYNCTIME 1 is on, anything else off; the switch turned on asks at once (its flag)"""
    (tmp_path/'mnt/data').mkdir(parents=True)
    (tmp_path/'mnt/data/config.ini').write_text(f'[SYSSET]\nTIME24H=1\nAUTOSYNCTIME={value}\n[IPOD]\nAUTOSYNCTIME=1\n')
    sysd = FakeSysd(tmp_path)
    root = run('--boot-hold', '60')
    a = Api(root)
    assert a.line() == 'Q2NET/1 ok'
    assert wait_for(lambda: status(a)['state'] == 'connected')
    time.sleep(2.5)
    assert ntp_log(root) == [] and sysd.lines == [] and time_status(a)['auto'] == '0'
    (root/'mnt/data/config.ini').write_text('[SYSSET]\nTIME24H=1\nAUTOSYNCTIME=1\n')
    assert wait_for(lambda: 'time save' in sysd.lines), 'a round once the switch is on'
    assert time_status(a)['auto'] == '1'


def test_a_round_holds_the_radio_up(run, tmp_path):
    """Smart Wi-Fi's off waits for a round in progress (a server not answering yet)"""
    (tmp_path/'ntp-hang').write_text('')
    root = run('--boot-hold', '5')  # past the connect (about 3 s here), which starts the round
    a = Api(root)
    assert a.line() == 'Q2NET/1 ok'
    assert wait_for(lambda: time_status(a)['state'] == 'syncing')
    time.sleep(5)  # past the boot hold
    assert status(a)['state'] == 'connected' and time_status(a)['state'] == 'syncing'
    assert ntp_log(root) == STOCK_ORDER[:1]


def test_a_card_boot_asks_no_server(run, tmp_path):
    """today's Vamp syncs the clock on a card boot"""
    (tmp_path/'ntp-answer').write_text('time.nist.gov')
    root = run('--card-boot')
    a = Api(root)
    assert a.line() == 'Q2NET/1 ok'
    assert wait_for(lambda: status(a)['state'] == 'connected')
    time.sleep(2.5)
    assert ntp_log(root) == [] and time_status(a)['state'] == 'external'


# ---- Vamp API 1 (docs/platform/network.md) ----

def test_conformance_every_command_is_on_the_page():
    conformance.check_coverage('network.md')


@pytest.mark.parametrize('n', range(len(conformance.blocks('network.md'))))
def test_conformance_network(run, n):
    root = run()
    conformance.replay(root/'run/vamp/net.sock', conformance.blocks('network.md')[n])


if __name__ == '__main__':
    sys.exit(pytest.main([__file__, '-q', '-p', 'no:cacheprovider']))
