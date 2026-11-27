#!/usr/bin/env python3
"""q2netd (netd/q2netd.c, docs/netd.md) built for this machine, with a stand-in for wpa_supplicant on its control socket
and commands standing in for ifconfig and udhcpc: the radio on with saved networks and off without, DHCP once
associated, Smart Wi-Fi's idle off and what holds the radio (the boot hold, wake, hold, the Wi-Fi adbd's use mark,
smart off), the saved networks edited in stock's file with the rest of it kept, scans, and Q2NET/1. Run it as
python3 test/netd.py (pytest). Q2NETD_SOURCE builds another copy, to show a broken one fails here."""
import os
import pathlib
import socket
import subprocess
import sys
import time

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SOURCE = pathlib.Path(os.environ.get('Q2NETD_SOURCE') or ROOT/'netd/q2netd.c')
CONF = 'ctrl_interface=/var/run/wpa_supplicant\nupdate_config=0\n\nnetwork={\n\tssid="Home"\n\tpsk="secret123"\n}\n'

FAKE_WPA = r'''
import os, socket, sys
root = sys.argv[1]
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


@pytest.fixture(scope='session')
def binary(tmp_path_factory):
    out = tmp_path_factory.mktemp('netd')
    src = out/'q2netd.c'
    src.write_bytes(SOURCE.read_bytes())
    subprocess.run(['cc', '-std=c11', '-Wall', '-Wextra', '-Werror', '-O2', '-o', str(out/'q2netd'), str(src)], check=True)
    (out/'fake_wpa.py').write_text(FAKE_WPA)
    return out


def start(binary, root, *extra, conf=CONF):
    for d in ('mnt/data', 'tmp', 'run'):
        (root/d).mkdir(parents=True, exist_ok=True)
    if conf is not None:
        (root/'mnt/data/wpa_supplicant.conf').write_text(conf)
    cmd = [str(binary/'q2netd'), '--root', str(root), '--wpa', f'exec python3 {binary}/fake_wpa.py {root} -iwlan0',
           '--dhcp', f'touch {root}/dhcp-ran; exec sleep 1000', '--up', f'echo up >> {root}/ifconfig',
           '--down', f'echo down >> {root}/ifconfig', '--idle', '3', *extra]
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


if __name__ == '__main__':
    sys.exit(pytest.main([__file__, '-q', '-p', 'no:cacheprovider']))
