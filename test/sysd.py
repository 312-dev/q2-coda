#!/usr/bin/env python3
"""q2sysd (sysd/q2sysd.c, docs/sysd.md) built for this machine against a stand-in /sys and /dev/input: the power key's
short press turning the screen off and on (the backlight's bl_power and the touchscreen's gesture mode), its hold
powering off, other keys left alone, the battery's file, and Q2SYS/1. Run it as python3 test/sysd.py (pytest).
Q2SYSD_SOURCE builds another copy of q2sysd.c, to show a broken one fails here."""
import os
import pathlib
import socket
import struct
import subprocess
import sys
import threading
import time

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import conformance  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
SOURCE = pathlib.Path(os.environ.get('Q2SYSD_SOURCE') or ROOT/'sysd/q2sysd.c')
KEY_POWER, KEY_VOLUMEUP, EV_KEY = 116, 115, 1


@pytest.fixture(scope='session')
def binary(tmp_path_factory):
    out = tmp_path_factory.mktemp('sysd')
    src = out/'q2sysd.c'
    src.write_bytes(SOURCE.read_bytes())
    subprocess.run(['cc', '-std=c11', '-Wall', '-Wextra', '-Werror', '-O2', '-I', str(conformance.PERM), '-o', str(out/'q2sysd'), str(src)], check=True)
    return out/'q2sysd'


def put(root, rel, text):
    p = root/rel.lstrip('/')
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)


@pytest.fixture
def tree(tmp_path):
    put(tmp_path, '/sys/class/backlight/lcd/bl_power', '0\n')
    put(tmp_path, '/sys/class/backlight/lcd/brightness', '200\n')
    put(tmp_path, '/sys/class/backlight/lcd/max_brightness', '255\n')
    put(tmp_path, '/sys/hynitron_debug/hyngescontrol', '0\n')
    put(tmp_path, '/sys/devices/i2c-0/0-0062/cw2015_capacity', '73\n')
    put(tmp_path, '/sys/devices/i2c-0/0-0062/cw2015_voltage', '3912\n')
    put(tmp_path, '/sys/class/power_supply/battery/status', 'Discharging\n')
    put(tmp_path, '/sys/class/power_supply/ac/online', '0\n')
    (tmp_path/'dev/input').mkdir(parents=True)
    os.mkfifo(tmp_path/'dev/input/event1')
    conformance.accounts(tmp_path)
    return tmp_path


TOOL = '#!/bin/sh\necho "$(basename "$0") $*" >> {root}/tools.log\nexit $(cat {root}/$(basename "$0").status 2>/dev/null || echo 0)\n'


@pytest.fixture
def tools(tree):
    """mount, umount, sync, q2adb-gadget, sv and q2car as stand-ins that log to tools.log; NAME.status sets one's exit status"""
    for rel in ('bin/mount', 'bin/umount', 'bin/sync', 'usr/bin/q2adb-gadget', 'usr/bin/sv', 'usr/bin/q2car'):
        put(tree, rel, TOOL.format(root=tree))
        (tree/rel).chmod(0o755)
    return tree


class Service(threading.Thread):
    """another service's socket (q2playd's, q2netd's): greets with its version and answers ok, keeping each line"""
    def __init__(self, path, version):
        super().__init__(daemon=True)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.s.bind(str(path))
        self.s.listen(4)
        self.version, self.lines = version, []
        self.start()

    def run(self):
        while True:
            c, _ = self.s.accept()
            f = c.makefile('rw')
            try:
                for line in f:
                    line = line.strip()
                    f.write((f'{self.version} ok' if line == self.version else 'ok') + '\n')
                    f.flush()
                    if line != self.version:
                        self.lines.append(line)
            except OSError:  # q2sysd went away mid-line (its end at a test's close)
                pass
            c.close()


@pytest.fixture
def run(binary, tree, tools, request):
    extra = getattr(request, 'param', [])
    p = subprocess.Popen([str(binary), '--root', str(tree), '--poweroff', f'touch {tree}/powered-off', *extra], stderr=subprocess.PIPE)
    end = time.monotonic() + 5
    while not (tree/'run/vamp/sys.sock').exists() and time.monotonic() < end:
        time.sleep(0.02)
    time.sleep(0.2)
    keys = os.open(tree/'dev/input/event1', os.O_WRONLY)
    yield tree, keys
    os.close(keys)
    p.terminate()
    assert p.wait(5) == 0, 'q2sysd ends on SIGTERM'


def key(fd, code, value):
    t = time.time()
    os.write(fd, struct.pack('@qqHHi', int(t), int(t * 1e6) % 1000000, EV_KEY, code, value))  # this machine's input_event


def press(fd, code=KEY_POWER, hold=0.1):
    key(fd, code, 1)
    time.sleep(hold)
    key(fd, code, 0)
    time.sleep(0.3)


def read(root, rel):
    return (root/rel.lstrip('/')).read_text().strip()


def test_a_short_press_turns_the_screen_off_then_on(run):
    root, keys = run
    press(keys)
    assert read(root, '/sys/class/backlight/lcd/bl_power') == '4' and read(root, '/sys/hynitron_debug/hyngescontrol') == '1'
    press(keys)
    assert read(root, '/sys/class/backlight/lcd/bl_power') == '0' and read(root, '/sys/hynitron_debug/hyngescontrol') == '0'
    assert not (root/'powered-off').exists()


def test_a_hold_powers_off_and_leaves_the_screen(run):
    root, keys = run
    key(keys, KEY_POWER, 1)
    time.sleep(2.0)
    assert not (root/'powered-off').exists(), 'not before the hold is long enough'
    end = time.monotonic() + 3
    while not (root/'powered-off').exists() and time.monotonic() < end:
        time.sleep(0.05)
    assert (root/'powered-off').exists()
    key(keys, KEY_POWER, 0)
    time.sleep(0.3)
    assert read(root, '/sys/class/backlight/lcd/bl_power') == '0', 'the release after a power off is no press'


def test_other_keys_and_a_lone_release_do_nothing(run):
    root, keys = run
    press(keys, KEY_VOLUMEUP)
    key(keys, KEY_POWER, 0)
    time.sleep(0.3)
    assert read(root, '/sys/class/backlight/lcd/bl_power') == '0'


def test_the_battery_file(run):
    root, _ = run
    text = dict(line.split('=') for line in read(root, '/run/vamp/battery').splitlines())
    assert text == {'level': '73', 'mv': '3912', 'status': 'Discharging', 'cable': '0'}


class Api:
    def __init__(self, root, hello='Q2SYS/1'):
        self.s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.s.connect(str(root/'run/vamp/sys.sock'))
        self.s.settimeout(3)
        self.f = self.s.makefile('rw')
        self.say(hello)

    def say(self, line):
        self.f.write(line + '\n')
        self.f.flush()

    def line(self):
        return self.f.readline().rstrip('\n')


def test_q2sys1(run):
    root, keys = run
    a = Api(root)
    assert a.line() == 'Q2SYS/1 ok'
    a.say('screen off')
    assert a.line() == 'ok' and read(root, '/sys/class/backlight/lcd/bl_power') == '4'
    a.say('screen')
    assert a.line() == 'screen off'
    a.say('screen toggle')
    assert a.line() == 'ok' and read(root, '/sys/class/backlight/lcd/bl_power') == '0'
    a.say('brightness 50')
    assert a.line() == 'ok' and read(root, '/sys/class/backlight/lcd/brightness') == '128'
    a.say('brightness 101')
    assert a.line() == 'err brightness'
    put(root, '/sys/devices/i2c-0/0-0062/cw2015_capacity', '72\n')
    a.say('battery')
    assert a.line() == 'battery level=72 mv=3912 status=Discharging cable=0'
    put(root, '/sys/class/power_supply/battery/status', 'Not charging\n')
    a.say('battery')  # the kernel's text has a space: percent-encoded, so the line still splits into four fields
    assert a.line() == 'battery level=72 mv=3912 status=Not%20charging cable=0'
    assert 'status=Not charging' in read(root, '/run/vamp/battery').splitlines()
    a.say('watch')
    assert a.line() == 'ok'
    press(keys)
    assert a.line() == 'screen off'
    b = Api(root, 'Q2SYS/2')
    assert b.line() == 'err version'


def test_charge_limit_holds_at_80_and_allows_at_75(binary, tree, tools):
    put(tree, '/mnt/data/config.ini', '[Q2POD]\nCHARGELIMIT=1\n')
    put(tree, '/sys/class/gpio/export', '')
    put(tree, '/sys/class/gpio/gpio150/direction', 'in')
    put(tree, '/sys/class/gpio/gpio150/value', '0')
    p = subprocess.Popen([str(binary), '--root', str(tree)], stderr=subprocess.PIPE)
    try:
        time.sleep(0.5)
        a = Api(tree)
        a.line()
        a.say('watch')
        a.line()
        assert read(tree, '/sys/class/gpio/gpio150/value') == '0', 'below the limit: left alone'
        put(tree, '/sys/devices/i2c-0/0-0062/cw2015_capacity', '81\n')
        a.say('battery')
        lines = [a.line(), a.line(), a.line()]
        assert read(tree, '/sys/class/gpio/gpio150/value') == '1' and 'charge held level=81' in lines
        put(tree, '/sys/devices/i2c-0/0-0062/cw2015_capacity', '76\n')
        a.say('battery')
        time.sleep(0.3)
        assert read(tree, '/sys/class/gpio/gpio150/value') == '1', 'held until 75'
        put(tree, '/sys/devices/i2c-0/0-0062/cw2015_capacity', '75\n')
        a.say('battery')
        time.sleep(0.3)
        assert read(tree, '/sys/class/gpio/gpio150/value') == '0'
    finally:
        p.terminate()
        p.wait(5)


def test_no_charge_limit_never_touches_the_charger(run):
    root, _ = run
    put(root, '/sys/devices/i2c-0/0-0062/cw2015_capacity', '95\n')
    a = Api(root)
    a.line()
    a.say('battery')
    a.line()
    assert not (root/'sys/class/gpio').exists()


def usb_tree(root):
    put(root, '/proc/mounts', '/dev/mmcblk4p1 /mnt/mmc vfat rw 0 0\n')
    (root/'sys/kernel/config/usb_gadget').mkdir(parents=True)
    put(root, '/sys/class/udc/13500000.otg/state', 'configured')
    return Service(root/'run/vamp/play.sock', 'Q2PLAY/1')


def test_usb_storage_stops_the_player_unmounts_the_card_and_shares_it(run):
    root, _ = run
    play = usb_tree(root)
    a = Api(root)
    a.line()
    a.say('usb storage')
    assert a.line() == 'ok'
    assert play.lines == ['stop']
    assert read(root, '/tools.log').splitlines()[:3] == ['sync ', 'umount /mnt/mmc', 'q2adb-gadget park']
    g = root/'sys/kernel/config/usb_gadget/q2storage'
    assert read(root, str(g/'functions/mass_storage.msg/lun.0/file').replace(str(root), '')) == '/dev/mmcblk4p1'
    assert (g/'UDC').read_text().strip() == '13500000.otg' and (g/'configs/c.1/mass_storage.msg').is_symlink()
    a.say('usb')
    assert a.line() == 'usb storage'
    a.say('usb adb')
    assert a.line() == 'ok'
    assert read(root, '/tools.log').splitlines()[3:] == ['mount /dev/mmcblk4p1 /mnt/mmc', 'q2adb-gadget unpark']
    assert (g/'UDC').read_text().strip() == ''


def test_a_busy_card_is_not_shared(run):
    root, _ = run
    usb_tree(root)
    put(root, '/umount.status', '1')
    a = Api(root)
    a.line()
    a.say('usb storage')
    assert a.line() == 'err usb'
    assert 'q2adb-gadget park' not in read(root, '/tools.log') and not (root/'sys/kernel/config/usb_gadget/q2storage').exists()
    a.say('usb')
    assert a.line() == 'usb adb'


@pytest.mark.parametrize('run, card', [([], False), (['--card-boot'], True)], indirect=['run'])
def test_standby_pauses_turns_off_and_resumes(run, card):
    root, _ = run
    play, net = Service(root/'run/vamp/play.sock', 'Q2PLAY/1'), Service(root/'run/vamp/net.sock', 'Q2NET/1')
    put(root, '/sys/power/state', '')
    a = Api(root)
    a.line()
    a.say('watch')
    a.line()
    a.say('standby')
    assert a.line() == 'ok'
    got = [a.line() for _ in range(4)]
    assert got == ['screen off', 'standby', 'screen on', 'resumed']
    assert read(root, '/sys/power/state') == 'mem' and play.lines == ['pause']
    assert net.lines == ([] if card else ['off', 'wake']), "a card boot's Wi-Fi is never turned off"


# ---- Vamp API 1 (docs/platform/system.md) ----

def test_conformance_every_command_is_on_the_page():
    conformance.check_coverage('system.md')


@pytest.mark.parametrize('n', range(len(conformance.blocks('system.md'))))
def test_conformance_system(run, n):
    root, keys = run
    play, net = Service(root/'run/vamp/play.sock', 'Q2PLAY/1'), Service(root/'run/vamp/net.sock', 'Q2NET/1')
    conformance.replay(root/'run/vamp/sys.sock', conformance.blocks('system.md')[n])


def test_usb_dac_stops_the_player_binds_stock_s_uac2_gadget_and_starts_the_loop(run):
    root, _ = run
    play = usb_tree(root)
    g = root/'sys/kernel/config/usb_gadget/q2dac'
    # sv notes whether the gadget was still bound when it was called: the loop must close its PCM before the gadget goes
    put(root, '/usr/bin/sv', f'#!/bin/sh\necho "sv $* udc=$(cat {g}/UDC 2>/dev/null)" >> {root}/tools.log\n')
    a = Api(root)
    a.line()
    a.say('usb dac')
    assert a.line() == 'ok'
    assert play.lines == ['stop']
    assert read(root, '/tools.log').splitlines() == ['q2adb-gadget park', 'sv up /etc/sv/q2dacloop udc=13500000.otg']
    f = g/'functions/uac2.a'
    got = {n: (f/n).read_text() for n in ('c_chmask', 'c_ssize', 'c_srate', 'p_chmask', 'c_sync', 'c_volume_present')}
    assert got == {'c_chmask': '0x03', 'c_ssize': '4', 'c_srate': '32000,44100,48000,64000,88200,96000,128000,176400,192000,352800,384000',
                   'p_chmask': '0', 'c_sync': 'async', 'c_volume_present': '0'}
    assert ((g/'idVendor').read_text(), (g/'idProduct').read_text(), (g/'strings/0x409/product').read_text()) == ('0x20B1', '0x301F', 'Q2')
    assert (g/'UDC').read_text().strip() == '13500000.otg' and (g/'configs/c.1/uac2.a').is_symlink()
    a.say('usb')
    assert a.line() == 'usb dac'
    a.say('usb storage')  # from the DAC to the card: through adb's, the loop down before its gadget goes
    assert a.line() == 'ok'
    assert read(root, '/tools.log').splitlines()[2:5] == ['sv down /etc/sv/q2dacloop udc=13500000.otg', 'q2adb-gadget unpark', 'sync ']
    assert (g/'UDC').read_text().strip() == '' and not (g/'configs/c.1/uac2.a').exists()
    a.say('usb')
    assert a.line() == 'usb storage'


def test_usb_car_shows_the_iphone_and_follows_q2_iap_s_verdict(run):
    root, _ = run
    play = usb_tree(root)
    (root/'tmp').mkdir(exist_ok=True)
    a = Api(root)
    a.line()
    a.say('watch')
    assert a.line() == 'ok'
    a.say('usb car')
    assert [a.line(), a.line()] == ['usb car', 'ok'], 'the watchers told first, then the answer'
    assert play.lines == ['pause'] and read(root, '/tools.log').splitlines() == ['q2car start']
    put(root, '/tmp/q2car.state', 'host=car\nverdict=car\n')
    assert a.line() == 'car', 'a car: told once, and the mode stays'
    a.say('usb')
    assert a.line() == 'usb car'
    a.say('usb adb')  # leaving it: q2car stops (it unparks adb's gadget itself)
    assert [a.line(), a.line()] == ['usb adb', 'ok']
    assert read(root, '/tools.log').splitlines()[-1] == 'q2car stop'


def test_not_a_car_is_adb_once_q2car_has_removed_its_gadget(run):
    root, _ = run
    usb_tree(root)
    (root/'tmp').mkdir(exist_ok=True)
    a = Api(root)
    a.line()
    a.say('usb car')
    assert a.line() == 'ok'
    put(root, '/tmp/q2car.state', 'verdict=computer\n')
    time.sleep(1.2)
    a.say('usb')
    assert a.line() == 'usb car', 'the gadget is still q2car\'s until it says down=1'
    put(root, '/tmp/q2car.state', 'verdict=computer\ndown=1\n')
    time.sleep(1.2)
    a.say('usb')
    assert a.line() == 'usb adb'
    assert read(root, '/tools.log').splitlines() == ['q2car start'], 'q2car stopped itself: no stop sent'


if __name__ == '__main__':
    sys.exit(pytest.main([__file__, '-q', '-p', 'no:cacheprovider']))
