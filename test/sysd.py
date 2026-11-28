#!/usr/bin/env python3
"""q2sysd (sysd/q2sysd.c, docs/sysd.md) built for this machine against a stand-in /sys and /dev/input: the power key's
short press turning the screen off and on (the backlight's bl_power and the touchscreen's gesture mode), its hold
powering off, other keys left alone, the battery's file, and Q2SYS/1. Run it as python3 test/sysd.py (pytest).
Q2SYSD_SOURCE builds another copy of q2sysd.c, to show a broken one fails here."""
import os
import shutil
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

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]/'tools'))
import vampos  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
SOURCE = pathlib.Path(os.environ.get('Q2SYSD_SOURCE') or ROOT/'sysd/q2sysd.c')
KEY_POWER, KEY_VOLUMEUP, EV_KEY = 116, 115, 1


@pytest.fixture(scope='session')
def binary(tmp_path_factory):
    out = tmp_path_factory.mktemp('sysd')
    src = out/'q2sysd.c'
    src.write_bytes(SOURCE.read_bytes())
    patch = ROOT/'patch'
    guard = [str(patch/f) for f in ('q2guard_core.c', 'q2guard_pmic.c', 'card_store.c')]  # as sysd/Makefile builds it
    subprocess.run(['cc', '-std=gnu11', '-Wall', '-Wextra', '-Werror', '-O2', '-DQ2GUARD_HOST', '-I', str(conformance.PERM),
                    '-I', str(patch), '-o', str(out/'q2sysd'), str(src), *guard, '-pthread'], check=True)
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
    put(tmp_path, '/tmp/heat', '400\n')  # --heat-file's reading from the first tick, so none reads as a sensor lost
    (tmp_path/'dev/input').mkdir(parents=True)
    os.mkfifo(tmp_path/'dev/input/event1')
    conformance.accounts(tmp_path)
    names = vampos.zone_names()  # the image's table (os/board/q2/post-build.sh), and a stand-in zoneinfo file each
    put(tmp_path, '/usr/share/vamp/timezones', ''.join(n + '\n' for n in names))
    for n in names:
        put(tmp_path, f'/usr/share/zoneinfo/{n}', 'TZif')
    return tmp_path


TOOL = '#!/bin/sh\necho "$(basename "$0") $*" >> {root}/tools.log\nexit $(cat {root}/$(basename "$0").status 2>/dev/null || echo 0)\n'


@pytest.fixture
def tools(tree):
    """mount, umount, sync, q2adb-gadget, sv, q2car, hwclock and date as stand-ins that log to tools.log; NAME.status sets one's exit status"""
    for rel in ('bin/mount', 'bin/umount', 'bin/sync', 'usr/bin/q2adb-gadget', 'usr/bin/sv', 'usr/bin/q2car', 'sbin/hwclock', 'bin/date'):
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
    extra = [a.format(root=tree) for a in getattr(request, 'param', [])]
    p = subprocess.Popen([str(binary), '--root', str(tree), '--poweroff', f'touch {tree}/powered-off', *extra], stderr=subprocess.PIPE)
    end = time.monotonic() + 5
    while not (tree/'run/vamp/sys.sock').exists() and time.monotonic() < end:
        time.sleep(0.02)
    time.sleep(0.2)
    assert p.poll() is None, 'q2sysd took its arguments'  # else the FIFO's open below waits for a reader forever
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



# ---- the screen's idle timeout (docs/sysd.md#the-screen-off-time) ----

def lit(root):
    return read(root, '/sys/class/backlight/lcd/bl_power') == '0'


def until_dark(root, secs):
    end = time.monotonic() + secs
    while lit(root) and time.monotonic() < end:
        time.sleep(0.1)
    return not lit(root)


def test_the_screen_goes_off_after_screen_off_time_and_input_or_awake_holds_it(run):
    """[SYSSET] LIGHTTIME 0 is stock's 15 s (setlight_time's table: 15, 30, 60, 120, 300, 1800 s)"""
    root, keys = run
    put(root, '/mnt/data/config.ini', '[SYSSET]\nLANGUAGE=2\nLIGHTTIME=0\n')
    time.sleep(10)
    assert lit(root)
    press(keys, KEY_VOLUMEUP)  # any input is use, as any key resets stock's timer
    time.sleep(10)
    assert lit(root), 'a key at 10 s: not dark at 20 s'
    a = Api(root)
    assert a.line() == 'Q2SYS/1 ok'
    a.say('awake')
    assert a.line() == 'ok'
    time.sleep(12)
    assert lit(root), 'awake at 20 s: not dark at 32 s'
    a.say('watch')
    assert a.line() == 'ok'
    assert until_dark(root, 5), 'dark 15 s after the last use'
    assert a.line() == 'screen off' and read(root, '/sys/hynitron_debug/hyngescontrol') == '1', 'as a press turns it off'
    press(keys)
    assert lit(root), 'the power key lights it again'


def test_a_screen_off_time_past_stock_s_list_never_turns_it_off_and_a_change_starts_it_again(run):
    root, _ = run
    put(root, '/mnt/data/config.ini', '[SYSSET]\nLIGHTTIME=6\n')
    time.sleep(17)
    assert lit(root), 'past the six choices: never (setlight_time gives 0)'
    put(root, '/mnt/data/config.ini', '[SYSSET]\nLIGHTTIME=0\n')
    time.sleep(13)
    assert lit(root), 'a change starts the time again, as stock\'s config_lighttime resets it'
    assert until_dark(root, 5)


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



# ---- Auto Standby and Auto Power Off (docs/sysd.md#auto-standby-and-auto-power-off) ----
# --time-scale 20 runs stock's 300 s as 15 s and its 60 s as 3 s; the screen off time is not scaled (LIGHTTIME=6: never)

@pytest.mark.parametrize('run', [['--time-scale', '20']], indirect=True)
def test_auto_standby_after_five_unused_minutes_held_while_music_plays_or_a_cable_is_in(run):
    root, keys = run
    play, net = Service(root/'run/vamp/play.sock', 'Q2PLAY/1'), Service(root/'run/vamp/net.sock', 'Q2NET/1')
    put(root, '/sys/power/state', '')
    put(root, '/tmp/coda-rc.now', 'title=A\nstate=1\nid=1\n')
    put(root, '/mnt/data/config.ini', '[SYSSET]\nSTANDBYMODE=1\nLIGHTTIME=6\n')
    time.sleep(17)
    assert read(root, '/sys/power/state') == '', 'music playing holds it'
    put(root, '/tmp/coda-rc.now', 'title=A\nstate=2\nid=1\n')
    put(root, '/sys/class/power_supply/ac/online', '1\n')
    time.sleep(17)
    assert read(root, '/sys/power/state') == '', 'a cable in holds it'
    put(root, '/sys/class/power_supply/ac/online', '0\n')
    time.sleep(12)
    assert read(root, '/sys/power/state') == '', 'the time runs from the last hold'
    end = time.monotonic() + 6
    while read(root, '/sys/power/state') != 'mem' and time.monotonic() < end:
        time.sleep(0.2)
    assert read(root, '/sys/power/state') == 'mem' and 'pause' in play.lines, "Standby as Q2SYS/1's standby"


@pytest.mark.parametrize('run', [['--time-scale', '20']], indirect=True)
def test_auto_power_off_after_its_time_unused_and_a_key_holds_it(run):
    root, keys = run
    put(root, '/mnt/data/config.ini', '[SYSSET]\nSAVEPOWERMODE=1\nLIGHTTIME=6\n')
    time.sleep(2)
    press(keys, KEY_VOLUMEUP)
    time.sleep(2.2)
    assert not (root/'powered-off').exists(), 'a key at 2 s: not off at 4.5 s (60 s scaled: 3 s)'
    end = time.monotonic() + 3
    while not (root/'powered-off').exists() and time.monotonic() < end:
        time.sleep(0.1)
    assert (root/'powered-off').exists(), 'SAVEPOWERMODE 1 is stock\'s 60 s'


@pytest.mark.parametrize('run', [['--time-scale', '20']], indirect=True)
def test_neither_when_stock_s_settings_are_off(run):
    root, _ = run
    put(root, '/sys/power/state', '')
    put(root, '/mnt/data/config.ini', '[SYSSET]\nSTANDBYMODE=0\nSAVEPOWERMODE=0\nLIGHTTIME=6\n')
    time.sleep(20)
    assert read(root, '/sys/power/state') == '' and not (root/'powered-off').exists()


class Watch(threading.Thread):
    """a Q2SYS/1 watcher keeping every event line"""
    def __init__(self, root):
        super().__init__(daemon=True)
        self.a, self.lines = Api(root), []
        self.a.line()
        self.a.say('watch')
        self.a.line()
        self.a.s.settimeout(None)
        self.start()

    def run(self):
        for line in self.a.f:
            self.lines.append(line.rstrip('\n'))


def until_file(root, rel, want, secs):
    end = time.monotonic() + secs
    while time.monotonic() < end:
        p = root/rel.lstrip('/')
        if p.exists() and p.read_text().strip() == want:
            return True
        time.sleep(0.1)
    return False


# stock's check_to_poweroption (0x4f3c90) counts down only while nothing holds it, and a hold stops the count where it
# is: music that plays for a while leaves the rest of the time, not a whole one, once it ends
@pytest.mark.parametrize('run', [['--time-scale', '20']], indirect=True)
def test_music_stops_auto_standby_s_count_and_does_not_start_it_again(run):
    root, _ = run
    Service(root/'run/vamp/play.sock', 'Q2PLAY/1'), Service(root/'run/vamp/net.sock', 'Q2NET/1')
    put(root, '/sys/power/state', '')
    put(root, '/mnt/data/config.ini', '[SYSSET]\nSTANDBYMODE=1\nLIGHTTIME=6\n')
    time.sleep(9)
    put(root, '/tmp/coda-rc.now', 'title=A\nstate=1\nid=1\n')
    time.sleep(10)
    assert read(root, '/sys/power/state') == '', 'music playing holds it'
    put(root, '/tmp/coda-rc.now', 'title=A\nstate=2\nid=1\n')
    assert until_file(root, '/sys/power/state', 'mem', 9), 'the 6 s left of 15 when the music began, not 15 again'


# stock's screen_action starts the times again only as the screen comes on: the screen off time darkening it is no use
@pytest.mark.parametrize('run', [['--time-scale', '15']], indirect=True)
def test_the_screen_going_off_by_itself_does_not_start_auto_standby_again(run):
    root, _ = run
    Service(root/'run/vamp/play.sock', 'Q2PLAY/1'), Service(root/'run/vamp/net.sock', 'Q2NET/1')
    put(root, '/sys/power/state', '')
    put(root, '/mnt/data/config.ini', '[SYSSET]\nSTANDBYMODE=1\nLIGHTTIME=0\n')
    assert until(lambda: not lit(root), 17), 'LIGHTTIME 0: dark at 15 s'
    assert until_file(root, '/sys/power/state', 'mem', 8), '300 s scaled is 20 s from the last use, not from the dark'


# stock's Standby gate ends on g_charge_state, usb_detect's cable: a charger holds it (Charge Only: no USB mode to hold it)
@pytest.mark.parametrize('run', [['--time-scale', '40']], indirect=True)
def test_a_charger_holds_auto_standby(run):
    root, _ = run
    Service(root/'run/vamp/play.sock', 'Q2PLAY/1'), Service(root/'run/vamp/net.sock', 'Q2NET/1')
    put(root, '/sys/power/state', '')
    put(root, '/mnt/data/config.ini', '[SYSSET]\nSTANDBYMODE=1\nLIGHTTIME=6\nUSBPICK=1\n[Q2POD]\nUSBPICK=1\n')
    cable(root, 1)
    time.sleep(10)
    assert read(root, '/sys/power/state') == '' and mode(root) == 'usb adb', '300 s scaled is 7.5 s: held by the charger'
    cable(root, 0)
    assert until_file(root, '/sys/power/state', 'mem', 10), 'the cable out: Standby 7.5 s later'


# Auto Power Off is not held by a cable (stock has no g_charge_state in its gate): with one in, stock's poweroff_page
# stops the music and the radios and waits on the charge, then powers off at Full or the cable out
@pytest.mark.parametrize('run', [['--time-scale', '20', '--reboot', 'touch {root}/rebooted']], indirect=True)
def test_auto_power_off_on_a_cable_waits_for_the_charge_and_ends_at_full(run):
    root, _ = run
    play, net = Service(root/'run/vamp/play.sock', 'Q2PLAY/1'), Service(root/'run/vamp/net.sock', 'Q2NET/1')
    w = Watch(root)
    put(root, '/mnt/data/config.ini', '[SYSSET]\nSAVEPOWERMODE=1\nLIGHTTIME=6\n[Q2POD]\nUSBPICK=1\n')
    put(root, '/sys/class/power_supply/battery/status', 'Charging\n')
    cable(root, 1)
    assert until(lambda: 'charge wait' in w.lines, 6), 'a cable in: the charge wait at 60 s scaled (3 s)'
    assert 'pause' in play.lines and net.lines == ['off'], "stock's into_poweroff: the music and Wi-Fi stopped"
    time.sleep(3)
    assert not (root/'powered-off').exists(), 'still charging: still on'
    put(root, '/sys/class/power_supply/battery/status', 'Full\n')
    assert until(lambda: (root/'powered-off').exists(), 3), 'Full: off'
    assert 'power off' in w.lines


@pytest.mark.parametrize('run', [['--time-scale', '20', '--reboot', 'touch {root}/rebooted']], indirect=True)
def test_the_charge_wait_ends_at_the_cable_out(run):
    root, _ = run
    Service(root/'run/vamp/play.sock', 'Q2PLAY/1'), Service(root/'run/vamp/net.sock', 'Q2NET/1')
    w = Watch(root)
    put(root, '/mnt/data/config.ini', '[SYSSET]\nSAVEPOWERMODE=1\nLIGHTTIME=6\n[Q2POD]\nUSBPICK=1\n')
    put(root, '/sys/class/power_supply/battery/status', 'Charging\n')
    cable(root, 1)
    assert until(lambda: 'charge wait' in w.lines, 6)
    cable(root, 0)
    assert until(lambda: (root/'powered-off').exists(), 3), 'the cable out: off'


# stock's get_battery_capacity makes both times whole at a change of the cable, in as well as out
@pytest.mark.parametrize('run', [['--time-scale', '10']], indirect=True)
def test_a_cable_in_starts_auto_power_off_s_time_again(run):
    root, _ = run
    Service(root/'run/vamp/play.sock', 'Q2PLAY/1'), Service(root/'run/vamp/net.sock', 'Q2NET/1')
    w = Watch(root)
    put(root, '/mnt/data/config.ini', '[SYSSET]\nSAVEPOWERMODE=1\nLIGHTTIME=6\n[Q2POD]\nUSBPICK=1\n')
    put(root, '/sys/class/power_supply/battery/status', 'Charging\n')
    time.sleep(4)
    cable(root, 1)
    time.sleep(4)
    assert 'charge wait' not in w.lines, '60 s scaled is 6 s from the cable, not from the setting'
    assert until(lambda: 'charge wait' in w.lines, 4)


# stock's poweroff_page takes a long press as device_reboot: the Q2 on again, not off
@pytest.mark.parametrize('run', [['--time-scale', '20', '--reboot', 'touch {root}/rebooted']], indirect=True)
def test_a_hold_in_the_charge_wait_restarts(run):
    root, keys = run
    Service(root/'run/vamp/play.sock', 'Q2PLAY/1'), Service(root/'run/vamp/net.sock', 'Q2NET/1')
    w = Watch(root)
    put(root, '/mnt/data/config.ini', '[SYSSET]\nSAVEPOWERMODE=1\nLIGHTTIME=6\n[Q2POD]\nUSBPICK=1\n')
    put(root, '/sys/class/power_supply/battery/status', 'Charging\n')
    cable(root, 1)
    assert until(lambda: 'charge wait' in w.lines, 6)
    press(keys, hold=3.3)
    assert until(lambda: (root/'rebooted').exists(), 2) and not (root/'powered-off').exists()
    assert 'reboot' in w.lines


# stock's gate also waits while USB storage (g_usblink_status 2) or Car Audio holds the Q2
@pytest.mark.parametrize('run', [['--time-scale', '20']], indirect=True)
def test_usb_storage_holds_auto_power_off(run):
    root, _ = run
    usb_tree(root)
    Service(root/'run/vamp/net.sock', 'Q2NET/1')
    w = Watch(root)
    put(root, '/mnt/data/config.ini', '[SYSSET]\nSAVEPOWERMODE=1\nLIGHTTIME=6\nUSBPICK=1\n[Q2POD]\nUSBPICK=2\n')
    cable(root, 1)
    assert until(lambda: mode(root) == 'usb storage')
    time.sleep(6)
    assert 'charge wait' not in w.lines and not (root/'powered-off').exists()


# ---- heat (docs/sysd.md#heat): today's Vamp's guardian, its readings from --heat-file in place of the PMIC ----
# --time-scale 50: its 5 s tick is 100 ms, the load warning's 60 s 1.2 s, the power-off card's 10 s 0.2 s

HEAT = ['--time-scale', '50', '--heat-file', '/tmp/heat']


class Load(threading.Thread):
    """a stand-in /proc/stat whose counters move at a busy share of pct"""
    def __init__(self, root, pct):
        super().__init__(daemon=True)
        self.root, self.pct, self.all, self.idle, self.stop = root, pct, 1000, 500, False
        self.write()
        self.start()

    def write(self):
        put(self.root, '/proc/stat.tmp', f'cpu  {self.all - self.idle} 0 0 {self.idle} 0 0 0 0 0 0\ncpu0 0 0 0 0\n')
        os.replace(self.root/'proc/stat.tmp', self.root/'proc/stat')

    def run(self):
        while not self.stop:
            self.all += 100
            self.idle += 100 - self.pct
            self.write()
            time.sleep(0.02)


def heat(root, v):
    put(root, '/tmp/heat', f'{v}\n')


class FakeUi(threading.Thread):
    """q2ui's Q2UI/1 socket: answers ok, keeps each line, and answers an ask as told (answer_with)"""
    def __init__(self, root):
        super().__init__(daemon=True)
        p = root/'run/vamp/ui.sock'
        p.parent.mkdir(parents=True, exist_ok=True)
        self.s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.s.bind(str(p))
        self.s.listen(4)
        self.lines, self.conn = [], None
        self.start()

    def run(self):
        c, _ = self.s.accept()
        self.conn = c
        f = c.makefile('rw')
        for line in f:
            line = line.strip()
            f.write(('Q2UI/1 ok' if line == 'Q2UI/1' else 'ok') + '\n')
            f.flush()
            if line != 'Q2UI/1':
                self.lines.append(line)

    def answer_with(self, text):
        self.conn.sendall((text + '\n').encode())


@pytest.mark.parametrize('run', [HEAT], indirect=True)
def test_three_readings_at_55_c_warn_once(run):
    root, _ = run
    ui, w = FakeUi(root), Watch(root)
    for v in (530, 531, 543, 556):  # under 3 C a reading: a die moves slowly, and a jump is a misread
        heat(root, v)
        time.sleep(0.25)
    assert until(lambda: 'heat warn' in w.lines, 2), 'three readings at 55 C or more: the warning'
    assert until(lambda: any(l.startswith('toast Q2') for l in ui.lines), 1)
    time.sleep(1)
    assert w.lines.count('heat warn') == 1, 'once, until it has fallen below 50 C'


@pytest.mark.parametrize('run', [HEAT], indirect=True)
def test_62_c_pauses_the_music_and_powers_off_after_the_card(run):
    root, _ = run
    play, w = Service(root/'run/vamp/play.sock', 'Q2PLAY/1'), Watch(root)
    for v in (580, 590, 600, 610):
        heat(root, v)
        time.sleep(0.15)
    heat(root, 625)
    assert until(lambda: 'heat off' in w.lines, 2)
    assert 'pause' in play.lines and not (root/'powered-off').exists(), 'the music paused, the card counting down'
    assert until(lambda: (root/'powered-off').exists(), 2), 'off once the card has counted down'


@pytest.mark.parametrize('run', [HEAT], indirect=True)
def test_a_sensor_that_reads_nothing_is_said_and_one_that_comes_back_too(run):
    root, _ = run
    ui, w = FakeUi(root), Watch(root)
    heat(root, 'none')
    assert until(lambda: 'heat nosensor' in w.lines, 2), 'three readings with none: no sensor'
    assert until(lambda: any(l.startswith('ask heat-sensor ') for l in ui.lines), 1)
    for v in (400, 402, 404):
        heat(root, v)
        time.sleep(0.15)
    assert until(lambda: 'heat ok' in w.lines, 2) and until(lambda: 'withdraw heat-sensor' in ui.lines, 1)


@pytest.mark.parametrize('run', [HEAT], indirect=True)
def test_hard_work_with_no_sensor_warns_and_cancel_holds_it_off(run):
    root, _ = run
    ui, w = FakeUi(root), Watch(root)
    heat(root, 'none')
    load = Load(root, 95)
    assert until(lambda: 'heat loadwarn left=60' in w.lines, 6), "two minutes' window at 85% or more: the warning"
    assert until(lambda: any(l.startswith('ask heat-load ') for l in ui.lines), 1)
    ui.answer_with('answer heat-load 1')
    assert until(lambda: 'heat cancelled' in w.lines, 2), "the card's Cancel"
    time.sleep(1.5)
    assert not (root/'powered-off').exists(), 'past the 60 s: cancelled, still on'
    load.stop = True


@pytest.mark.parametrize('run', [HEAT], indirect=True)
def test_q2sys1_heat_cancel_holds_the_load_warning_off(run):
    root, _ = run
    w = Watch(root)
    heat(root, 'none')
    load = Load(root, 95)
    assert until(lambda: 'heat loadwarn left=60' in w.lines, 6)
    a = Api(root)
    a.line()
    a.say('heat cancel')
    assert a.line() == 'ok'
    assert until(lambda: 'heat cancelled' in w.lines, 2), 'Q2SYS/1 heat cancel, with no q2ui'
    load.stop = True


@pytest.mark.parametrize('run', [HEAT], indirect=True)
def test_hard_work_left_alone_powers_off_and_shut_down_at_once(run):
    root, _ = run
    ui, w = FakeUi(root), Watch(root)
    play = Service(root/'run/vamp/play.sock', 'Q2PLAY/1')
    heat(root, 'none')
    load = Load(root, 95)
    assert until(lambda: 'heat loadwarn left=60' in w.lines, 6)
    assert until(lambda: 'heat off' in w.lines, 3), "the load warning's 60 s (1.2 s scaled) run out: the power-off"
    assert until(lambda: (root/'powered-off').exists(), 2) and 'pause' in play.lines
    load.stop = True


@pytest.mark.parametrize('run', [HEAT], indirect=True)
def test_shut_down_on_the_load_warning_powers_off_at_once(run):
    root, _ = run
    ui, w = FakeUi(root), Watch(root)
    heat(root, 'none')
    load = Load(root, 95)
    assert until(lambda: any(l.startswith('ask heat-load ') for l in ui.lines), 6)
    ui.answer_with('answer heat-load 2')
    assert until(lambda: (root/'powered-off').exists(), 0.6), "the card's Shut Down: off now, not after its 60 s"
    load.stop = True


# a Standby between two readings leaves the last one too old to hold the next to (q2guard_core.c sensor_read): a die
# cooler after a suspend is no jump
@pytest.mark.parametrize('run', [HEAT], indirect=True)
def test_a_cooler_reading_after_standby_is_no_jump(run):
    root, _ = run
    Service(root/'run/vamp/play.sock', 'Q2PLAY/1'), Service(root/'run/vamp/net.sock', 'Q2NET/1')
    w = Watch(root)
    put(root, '/sys/power/state', '')
    for v in (401, 402, 403):
        heat(root, v)
        time.sleep(0.15)
    a = Api(root)
    a.line()
    heat(root, 343)
    a.say('standby')
    assert a.line() == 'ok'
    time.sleep(1)
    assert 'heat nosensor' not in w.lines, '6 C cooler after the suspend: taken, not three jumps'


# ---- the jacks (docs/sysd.md#the-jacks): PA07 and PA08 as the kernel's GPIO class gives them, 0 plugged ----

def jack(root, n, plugged):
    put(root, f'/sys/class/gpio/gpio{n}/value', '0\n' if plugged else '1\n')


def jacks(root):
    a = Api(root)
    a.line()
    a.say('jack')
    got = a.line()
    a.s.close()
    return got


def test_a_plug_counts_at_once_and_an_unplug_pauses_the_music(run):
    root, _ = run
    play, w = Service(root/'run/vamp/play.sock', 'Q2PLAY/1'), Watch(root)
    jack(root, 7, False), jack(root, 8, False)
    assert until(lambda: jacks(root) == 'jack po=0 bal=0', 2.5), 'PA07 and PA08 read: both out'
    jack(root, 7, True)
    assert until(lambda: 'jack po 1' in w.lines, 1.5), 'a plug at the next read'
    put(root, '/tmp/coda-rc.now', 'title=A\nstate=1\nid=1\n')
    jack(root, 7, False)
    assert until(lambda: 'jack po 0' in w.lines, 3), 'an unplug that lasts'
    assert play.lines == ['pause'], "music playing paused at the unplug, as stock's config_po_output does"
    jack(root, 8, True)
    assert until(lambda: 'jack bal 1' in w.lines, 1.5) and jacks(root) == 'jack po=0 bal=1'
    assert read(root, '/run/vamp/jack') == 'po=0 bal=1', "/run/vamp/jack for q2playd's output, written at each change"


def test_an_unplugged_read_that_does_not_last_is_no_unplug(run):
    root, _ = run
    play, w = Service(root/'run/vamp/play.sock', 'Q2PLAY/1'), Watch(root)
    jack(root, 7, True), jack(root, 8, False)
    assert until(lambda: jacks(root) == 'jack po=1 bal=0', 2.5)
    put(root, '/tmp/coda-rc.now', 'title=A\nstate=1\nid=1\n')
    # out 0.9 s, in 1.2 s, five times: shorter than q2sysd's 1 s between reads, so one read at most each time and never
    # two running, the DAC's power-up dip; the 2.1 s cycle drifts against the reads, so some land in a dip
    for _ in range(5):
        jack(root, 7, False)
        time.sleep(0.9)
        jack(root, 7, True)
        time.sleep(1.2)
    assert 'jack po 0' not in w.lines and play.lines == [], 'one unplugged read, then plugged: nothing'


def test_an_unplug_with_nothing_playing_sends_no_pause(run):
    root, _ = run
    play, w = Service(root/'run/vamp/play.sock', 'Q2PLAY/1'), Watch(root)
    jack(root, 7, True), jack(root, 8, False)
    assert until(lambda: jacks(root) == 'jack po=1 bal=0', 2.5)
    put(root, '/tmp/coda-rc.now', 'title=A\nstate=2\nid=1\n')
    jack(root, 7, False)
    assert until(lambda: 'jack po 0' in w.lines, 3)
    assert play.lines == [], 'paused already: nothing sent'


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



# ---- the cable (docs/sysd.md#the-cable): q2sysd follows ac/online and the USB setting, [Q2POD] USBPICK ----

def cable(root, on):
    put(root, '/sys/class/power_supply/ac/online', f'{int(on)}\n')


def until(fn, timeout=3.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        got = fn()
        if got:
            return got
        time.sleep(0.05)
    return fn()


def mode(root):
    try:
        a = Api(root)
    except ConnectionRefusedError:  # bound and not yet listening: q2sysd is still starting
        return ''
    a.line()
    a.say('usb')
    got = a.line()
    a.s.close()
    return got


def tools_log(root):
    p = root/'tools.log'
    return p.read_text().splitlines() if p.exists() else []


def test_a_cable_on_automatic_shows_the_iphone_and_a_computer_gets_the_card(run):
    root, _ = run
    usb_tree(root)
    (root/'tmp').mkdir(exist_ok=True)
    cable(root, 1)
    assert until(lambda: 'q2car start' in tools_log(root), 2.0), 'a cable in on Automatic (no USBPICK) is the iPhone within 2 s'
    assert mode(root) == 'usb car'
    put(root, '/tmp/q2car.state', 'verdict=computer\ndown=1\n')
    assert until(lambda: mode(root) == 'usb storage'), 'not a car, a computer: Transfer Files'
    assert tools_log(root)[1:4] == ['sync ', 'umount /mnt/mmc', 'q2adb-gadget park']
    cable(root, 0)
    assert until(lambda: mode(root) == 'usb adb'), 'the cable out: adb again'
    assert tools_log(root)[-2:] == ['mount /dev/mmcblk4p1 /mnt/mmc', 'q2adb-gadget unpark']


def test_a_car_keeps_the_iphone_until_the_cable_is_out(run):
    root, _ = run
    usb_tree(root)
    (root/'tmp').mkdir(exist_ok=True)
    put(root, '/mnt/data/config.ini', '[Q2POD]\nUSBPICK=0\n')
    cable(root, 1)
    assert until(lambda: 'q2car start' in tools_log(root), 2.0)
    put(root, '/tmp/q2car.state', 'verdict=car\n')
    time.sleep(1.2)
    assert mode(root) == 'usb car'
    cable(root, 0)
    assert until(lambda: mode(root) == 'usb adb')
    assert tools_log(root) == ['q2car start', 'q2car stop']


def test_a_charger_on_automatic_only_charges(run):
    root, _ = run
    usb_tree(root)
    (root/'tmp').mkdir(exist_ok=True)
    cable(root, 1)
    assert until(lambda: 'q2car start' in tools_log(root), 2.0)
    put(root, '/tmp/q2car.state', 'verdict=silent\ndown=1\n')
    assert until(lambda: mode(root) == 'usb adb')
    time.sleep(0.5)
    assert tools_log(root) == ['q2car start'], 'nothing answered: the card stays the Q2\'s'


@pytest.mark.parametrize('pick, want, log', [
    (2, 'usb storage', ['sync ', 'umount /mnt/mmc', 'q2adb-gadget park']),
    (3, 'usb dac', ['q2adb-gadget park', 'sv up /etc/sv/q2dacloop']),
])
def test_a_remembered_mode_at_the_cable_and_adb_at_its_end(run, pick, want, log):
    root, _ = run
    usb_tree(root)
    put(root, '/mnt/data/config.ini', f'[SYSSET]\nUSBPICK=1\n[Q2POD]\nUSBPICK={pick}\n')
    cable(root, 1)
    assert until(lambda: mode(root) == want), f'USBPICK={pick}: {want} at the cable'
    assert tools_log(root) == log
    cable(root, 0)
    assert until(lambda: mode(root) == 'usb adb')
    assert 'q2adb-gadget unpark' in tools_log(root)


@pytest.mark.parametrize('pick', [1, 4, 5])
def test_charge_only_debugging_and_ask_leave_the_usb_mode(run, pick):
    root, _ = run
    usb_tree(root)
    put(root, '/mnt/data/config.ini', f'[Q2POD]\nUSBPICK={pick}\n')
    a = Api(root)
    a.line()
    a.say('watch')
    assert a.line() == 'ok'
    cable(root, 1)
    got = [a.line() for _ in range(2 if pick == 5 else 1)]
    assert got[0] == 'battery level=73 mv=3912 status=Discharging cable=1', 'the cable told at once'
    if pick == 5:
        assert got[1] == 'usb ask', 'Ask Every Time: the chooser is the UI\'s'
    time.sleep(1.5)
    assert mode(root) == 'usb adb' and tools_log(root) == []


def test_a_cable_in_at_the_start_is_followed(binary, tree, tools):
    usb_tree(tree)
    (tree/'tmp').mkdir(exist_ok=True)
    put(tree, '/mnt/data/config.ini', '[Q2POD]\nUSBPICK=3\n')
    cable(tree, 1)
    p = subprocess.Popen([str(binary), '--root', str(tree)], stderr=subprocess.PIPE)
    try:
        assert until(lambda: (tree/'run/vamp/sys.sock').exists() and mode(tree) == 'usb dac'), 'started with a cable in'
    finally:
        p.terminate()
        p.wait(5)

# ---- a low battery (docs/sysd.md#a-low-battery) ----

def batt(root, level=None, mv=None, cable=None):
    if level is not None:
        put(root, '/sys/devices/i2c-0/0-0062/cw2015_capacity', f'{level}\n')
    if mv is not None:
        put(root, '/sys/devices/i2c-0/0-0062/cw2015_voltage', f'{mv}\n')
    if cable is not None:
        put(root, '/sys/class/power_supply/ac/online', f'{cable}\n')


def low_start(root, keys):
    """19%: the screen's change reads it (else 30 s), and below 20% the battery is read each second from then"""
    batt(root, 19, 3700)
    press(keys)
    press(keys)


def test_under_16_percent_lights_the_screen_and_says_low_battery_once(run):
    root, keys = run
    ui, w = FakeUi(root), Watch(root)
    low_start(root, keys)
    press(keys)  # dark
    assert read(root, '/sys/class/backlight/lcd/bl_power') == '4'
    batt(root, 15)
    assert until(lambda: 'lowbattery warn' in w.lines, 3), w.lines
    assert until(lambda: 'toast Low%20Battery' in ui.lines, 2), ui.lines
    assert read(root, '/sys/class/backlight/lcd/bl_power') == '0', "stock's awake_screen with the warning"
    time.sleep(3)
    assert w.lines.count('lowbattery warn') == 1, 'once a discharge'
    batt(root, 17)
    time.sleep(2)
    batt(root, 15)
    assert until(lambda: w.lines.count('lowbattery warn') == 2, 3), 'again once it has been 16% or more'
    assert not (root/'powered-off').exists()


@pytest.mark.parametrize('level, mv', [(19, 3400), (0, 3700)])
def test_five_seconds_at_3400_mv_or_0_percent_power_off(run, level, mv):
    root, keys = run
    ui, w = FakeUi(root), Watch(root)
    low_start(root, keys)
    batt(root, level, mv)
    time.sleep(3)
    assert not (root/'powered-off').exists(), 'five readings first'
    assert until(lambda: (root/'powered-off').exists(), 5), "check_lowbattery_state's fifth: the power off"
    assert 'lowbattery off' in w.lines and 'toast Shutdown' in ui.lines, (w.lines, ui.lines)


def test_a_cable_in_holds_the_low_battery_power_off(run):
    root, keys = run
    low_start(root, keys)
    batt(root, 19, 3300, cable=1)
    time.sleep(8)
    assert not (root/'powered-off').exists()


def test_a_good_reading_starts_the_count_again(run):
    root, keys = run
    low_start(root, keys)
    for mv, secs in ((3300, 3), (3700, 1.5), (3300, 3)):
        batt(root, 19, mv)
        time.sleep(secs)
    assert not (root/'powered-off').exists(), 'five low readings in a row, not in all'
    assert until(lambda: (root/'powered-off').exists(), 4)


def test_no_voltage_to_read_powers_nothing_off(run):
    root, keys = run
    low_start(root, keys)
    (root/'sys/devices/i2c-0/0-0062/cw2015_voltage').unlink()
    time.sleep(7)
    assert not (root/'powered-off').exists(), 'a reading that failed is not a low one'


# ---- the watchdog (docs/sysd.md#the-watchdog) ----

@pytest.fixture(scope='session')
def wdt_shim(tmp_path_factory):
    out = tmp_path_factory.mktemp('wdt')/'wdt_shim.so'
    subprocess.run(['cc', '-shared', '-fPIC', '-O2', '-Wall', '-Wextra', '-Werror', '-o', str(out), str(ROOT/'test/wdt_shim.c'), '-ldl'], check=True)
    return out


class Wdt:
    """q2sysd with the watchdog's stand-in (test/wdt_shim.c): dev/jz_watchdog there, each ioctl in wdt.log"""
    def __init__(self, binary, tree, shim, device=True, extra=()):
        self.root, self.log = tree, tree/'wdt.log'
        if device:
            put(tree, '/dev/jz_watchdog', '')
        put(tree, '/sys/power/state', '')
        env = dict(os.environ, LD_PRELOAD=str(shim), Q2WDT_LOG=str(self.log))
        self.p = subprocess.Popen([str(binary), '--root', str(tree), '--poweroff', f'cp {self.log} {tree}/log-at-poweroff', *extra],
                                  stderr=subprocess.PIPE, text=True, env=env)
        end = time.monotonic() + 5
        while not (tree/'run/vamp/sys.sock').exists() and time.monotonic() < end:
            time.sleep(0.02)
        time.sleep(0.2)
        self.keys = os.open(tree/'dev/input/event1', os.O_WRONLY)

    def calls(self):
        return [l.split(' ', 1)[1] for l in self.log.read_text().splitlines()] if self.log.exists() else []

    def end(self):
        os.close(self.keys)
        self.p.terminate()
        code = self.p.wait(5)
        return code, self.p.stderr.read()


def test_the_watchdog_started_at_60_s_and_fed_each_second(binary, tree, tools, wdt_shim):
    w = Wdt(binary, tree, wdt_shim)
    time.sleep(3.5)
    fed = w.calls()
    code, err = w.end()
    calls = w.calls()
    assert calls[:1] == ['start 60000'], calls
    assert 3 <= fed.count('feed') <= 5, f'a feed a second ({fed})'
    assert calls[-1] == 'stop' and code == 0, f'a clean end (SIGTERM) stops it: its close is a no-op in the driver ({calls})'


def test_the_watchdog_stopped_across_standby(binary, tree, tools, wdt_shim):
    os.unlink(tree/'sys/power/state') if (tree/'sys/power/state').exists() else None
    (tree/'sys/power').mkdir(parents=True, exist_ok=True)
    os.mkfifo(tree/'sys/power/state')  # the kernel's suspend: held open until read
    w = Wdt.__new__(Wdt)
    w.root, w.log = tree, tree/'wdt.log'
    put(tree, '/dev/jz_watchdog', '')
    env = dict(os.environ, LD_PRELOAD=str(wdt_shim), Q2WDT_LOG=str(w.log))
    w.p = subprocess.Popen([str(binary), '--root', str(tree), '--poweroff', 'true'], stderr=subprocess.PIPE, text=True, env=env)
    end = time.monotonic() + 5
    while not (tree/'run/vamp/sys.sock').exists() and time.monotonic() < end:
        time.sleep(0.02)
    time.sleep(0.2)
    w.keys = os.open(tree/'dev/input/event1', os.O_WRONLY)
    fd = os.open(tree/'sys/power/state', os.O_RDWR | os.O_NONBLOCK)  # a full pipe: q2sysd's write waits, as the suspend does
    filled = 0
    try:
        while True:
            filled += os.write(fd, b'x' * 4096)
    except BlockingIOError:
        pass
    c = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    c.connect(str(tree/'run/vamp/sys.sock'))
    c.sendall(b'Q2SYS/1\nstandby\n')
    time.sleep(1.5)
    at_suspend = w.calls()  # in the suspend
    got, end = b'', time.monotonic() + 5
    while not got.endswith(b'mem') and time.monotonic() < end:  # the resume: the write goes through
        try:
            got += os.read(fd, 65536)
        except BlockingIOError:
            time.sleep(0.05)
    got = got[filled:]
    time.sleep(1.5)
    os.close(fd)
    after = w.calls()
    c.close()
    code, err = w.end()
    assert got == b'mem'
    assert at_suspend[-1:] == ['stop'], f'stopped before the suspend: it counts through it ({at_suspend})'
    rest = after[len(at_suspend):]
    assert rest[:1] == ['start 60000'] and 'feed' in rest, f'started again at the resume, and fed ({rest})'


def test_the_watchdog_stopped_before_the_power_off(binary, tree, tools, wdt_shim):
    w = Wdt(binary, tree, wdt_shim)
    press(w.keys, hold=3.2)  # HOLD_MS: the power off
    end = time.monotonic() + 5
    while not (tree/'log-at-poweroff').exists() and time.monotonic() < end:
        time.sleep(0.1)
    w.end()
    at_off = [l.split(' ', 1)[1] for l in (tree/'log-at-poweroff').read_text().splitlines()]
    assert at_off[0] == 'start 60000' and at_off[-1] == 'stop', f'a clean power off stops it first ({at_off})'


def test_no_watchdog_device_is_said_and_nothing_else(binary, tree, tools, wdt_shim):
    w = Wdt(binary, tree, wdt_shim, device=False)
    time.sleep(1.5)
    code, err = w.end()
    assert code == 0 and 'no watchdog' in err and w.calls() == [], err


# ---- the clock (docs/sysd.md#the-clock) ----

def zone(root):
    p = root/'run/vamp/localtime'
    return os.readlink(p) if p.is_symlink() else None


def until(pred, secs=4):
    end = time.monotonic() + secs
    while not pred() and time.monotonic() < end:
        time.sleep(0.1)
    return pred()


def test_the_zone_is_config_inis_index_and_follows_a_change(run):
    """no TIMEZONE (a fresh /mnt/data) is Vamp's UTC; index 38 is Chicago (tools/build.py TIMEZONES), set within a
    second of the change and told"""
    root, _ = run
    assert until(lambda: zone(root) == '/usr/share/zoneinfo/UTC'), zone(root)
    a = Api(root)
    assert a.line() == 'Q2SYS/1 ok'
    a.say('time')
    assert a.line() == 'time zone=UTC'
    a.say('watch')
    assert a.line() == 'ok'
    put(root, '/mnt/data/config.ini', '[SYSSET]\nTIME24H=1\nTIMEZONE=38\n[IPOD]\nTIMEZONE=5\n')
    assert until(lambda: zone(root) == '/usr/share/zoneinfo/America/Chicago'), zone(root)
    assert a.line() == 'time zone=America/Chicago'
    put(root, '/mnt/data/config.ini', '[SYSSET]\nTIMEZONE=6\n')  # stock's Mexico City row, a POSIX rule in today's Vamp
    assert until(lambda: zone(root) == '/usr/share/zoneinfo/America/Mexico_City'), zone(root)


@pytest.mark.parametrize('value', ['44', '-1', '200'])
def test_a_zone_index_past_the_list_is_stocks_shanghai(run, value):
    """config_init takes an index past the list as 22, Asia/Shanghai"""
    root, _ = run
    put(root, '/mnt/data/config.ini', f'[SYSSET]\nTIMEZONE={value}\n')
    assert until(lambda: zone(root) == '/usr/share/zoneinfo/Asia/Shanghai'), zone(root)


def test_q2sysd_zone_sets_it_once_and_ends(binary, tree):
    """rcS's `q2sysd zone`, before any service: the link made, exit 0, no socket"""
    put(tree, '/mnt/data/config.ini', '[SYSSET]\nTIMEZONE=12\n')
    r = subprocess.run([str(binary), '--root', str(tree), 'zone'], capture_output=True, text=True, timeout=10)
    assert r.returncode == 0, r.stderr
    assert zone(tree) == '/usr/share/zoneinfo/Europe/London' and not (tree/'run/vamp/sys.sock').exists()
    (tree/'usr/share/vamp/timezones').unlink()  # no table: nothing set, a failure for rcS's log
    (tree/'run/vamp/localtime').unlink()
    r = subprocess.run([str(binary), '--root', str(tree), 'zone'], capture_output=True, text=True, timeout=10)
    assert r.returncode == 1 and zone(tree) is None


def test_time_save_writes_the_rtc_in_utc_and_time_set_sets_then_saves(run):
    """stock's toolsSetRtcTime: date -s, then hwclock -w, made -wu by today's Vamp (docs/internals.md#clock)"""
    root, _ = run
    a = Api(root)
    assert a.line() == 'Q2SYS/1 ok'
    a.say('time save')
    assert a.line() == 'ok'
    assert read(root, '/tools.log').splitlines()[-1] == 'hwclock -w -u'
    a.say('watch')
    assert a.line() == 'ok'
    a.say('time set 1767225600')
    assert a.line() == 'time set' and a.line() == 'ok'
    assert read(root, '/tools.log').splitlines()[-2:] == ['date -u -s @1767225600', 'hwclock -w -u']
    for bad in ('time set', 'time set 12x', 'time set -5', 'time set 0'):
        a.say(bad)
        assert a.line() == 'err time', bad
    put(root, '/date.status', '1')  # date refused (no CAP_SYS_TIME): no set, no RTC write
    before = read(root, '/tools.log')
    a.say('time set 1767225600')
    assert a.line() == 'err time'
    assert read(root, '/tools.log').splitlines()[len(before.splitlines()):] == ['date -u -s @1767225600']
    put(root, '/hwclock.status', '1')
    a.say('time save')
    assert a.line() == 'err time'


if __name__ == '__main__':
    sys.exit(pytest.main([__file__, '-q', '-p', 'no:cacheprovider']))


# ---- Find My Vamp (docs/sysd.md#find-my-vamp) ----

@pytest.fixture(scope='session')
def pwm_shim(tmp_path_factory):
    out = tmp_path_factory.mktemp('pwm')/'pwm_shim.so'
    subprocess.run(['cc', '-shared', '-fPIC', '-O2', '-Wall', '-Wextra', '-Werror', '-o', str(out), str(ROOT/'test/pwm_shim.c'), '-ldl'], check=True)
    return out


class Pwm:
    """q2sysd with the PWM driver's stand-in (test/pwm_shim.c): dev/jz_pwm there, each ioctl in pwm.log"""
    def __init__(self, binary, tree, shim, device=True, extra=(), env=None):
        self.root, self.log = tree, tree/'pwm.log'
        if device:
            put(tree, '/dev/jz_pwm', '')
        e = dict(os.environ, LD_PRELOAD=str(shim), Q2PWM_LOG=str(self.log), **(env or {}))
        self.p = subprocess.Popen([str(binary), '--root', str(tree), '--poweroff', 'true', *extra], stderr=subprocess.PIPE, text=True, env=e)
        end = time.monotonic() + 5
        while not (tree/'run/vamp/sys.sock').exists() and time.monotonic() < end:
            time.sleep(0.02)
        time.sleep(0.2)
        self.keys = os.open(tree/'dev/input/event1', os.O_WRONLY)

    def calls(self):
        return [l.split(' ', 1)[1] for l in self.log.read_text().splitlines()] if self.log.exists() else []

    def end(self):
        os.close(self.keys)
        self.p.terminate()
        return self.p.wait(5), self.p.stderr.read()


def ask_sys(root, line):
    a = Api(root)
    a.line()
    a.say(line)
    return a.line()


def test_find_plays_the_jingle_with_the_screen_lit_and_the_toast(binary, tree, tools, pwm_shim):
    """findmy.c / findmy_tune.h: the channel released then requested, each note level 0, CONFIG, level 50, rests at
    level 0; the screen lit, the toast, find on told, /run/vamp/finding while it plays"""
    ui = FakeUi(tree)
    f = Pwm(binary, tree, pwm_shim)
    w = Watch(tree)
    assert ask_sys(tree, 'screen off') == 'ok' and read(tree, '/sys/class/backlight/lcd/bl_power') == '4'
    assert ask_sys(tree, 'find') == 'ok'
    time.sleep(0.5)
    calls = f.calls()
    assert calls[:5] == ['release 1', 'request PC01', 'level 1 0', 'config 2349 1', 'level 1 50'], calls
    assert 'config 2960 1' in calls and 'level 1 0' in calls[5:], 'the next note after a rest'
    assert read(tree, '/sys/class/backlight/lcd/bl_power') == '0', 'the screen lit'
    assert (tree/'run/vamp/finding').exists() and ask_sys(tree, 'find state') == 'find 1'
    assert 'toast Find%20My%20Vamp' in ui.lines and 'find on' in w.lines
    assert ask_sys(tree, 'find stop') == 'ok'
    time.sleep(0.3)
    calls = f.calls()
    assert calls[-2:] == ['level 1 0', 'release 1'], 'the pin low and the channel given back, for the next click'
    assert not (tree/'run/vamp/finding').exists() and 'find off' in w.lines
    assert f.end()[0] == 0


def test_with_no_backlight_device_the_screen_is_stock_s_pwm_on_pc00(binary, tree, tools, pwm_shim):
    """The Q2 has no /sys/class/backlight: stock's toolSetBackLight (model 23) on /dev/jz_pwm, read on the owner's Q2
    (docs/sysd.md#the-backlight): PC00 taken at the first use, level 0 then CONFIG at 10 kHz over 111 steps; off is
    level 0, on is LIGHTNESS + 11, brightness P is LIGHTNESS P"""
    shutil.rmtree(tree/'sys/class/backlight/lcd')
    put(tree, '/mnt/data/config.ini', '[SYSSET]\nLIGHTNESS=40\n')
    f = Pwm(binary, tree, pwm_shim)
    assert f.calls() == [], 'nothing touched at the start: a card boot keeps the light demo left'
    assert ask_sys(tree, 'screen off') == 'ok' and ask_sys(tree, 'screen') == 'screen off'
    assert f.calls() == ['request PC00', 'level 1 0', 'config 10000 1 steps 111', 'level 1 0'], f.calls()
    assert read(tree, '/sys/hynitron_debug/hyngescontrol') == '1', 'double-tap wake while dark'
    assert ask_sys(tree, 'screen on') == 'ok' and f.calls()[-1] == 'level 1 51', f.calls()
    assert ask_sys(tree, 'brightness 100') == 'ok' and f.calls()[-1] == 'level 1 111'
    assert ask_sys(tree, 'screen off') == 'ok' and ask_sys(tree, 'brightness 0') == 'ok' and f.calls()[-1] == 'level 1 0', 'dark stays dark'
    assert ask_sys(tree, 'screen on') == 'ok' and f.calls()[-1] == 'level 1 11'
    assert f.calls().count('request PC00') == 1, 'the channel taken once'
    assert f.end()[0] == 0


def test_a_pwm_backlight_refused_is_err_screen_and_no_level(binary, tree, tools, pwm_shim):
    shutil.rmtree(tree/'sys/class/backlight/lcd')
    f = Pwm(binary, tree, pwm_shim, env={'Q2PWM_REFUSE': '1'})
    assert ask_sys(tree, 'screen off') == 'err screen'
    assert not [c for c in f.calls() if c.startswith('level') and c != 'level 1 0'], f.calls()
    f.end()


def test_any_key_or_the_wheel_stops_it_within_a_slice(binary, tree, tools, pwm_shim):
    f = Pwm(binary, tree, pwm_shim)
    assert ask_sys(tree, 'find') == 'ok'
    time.sleep(0.3)
    key(f.keys, KEY_VOLUMEUP, 1)
    at = time.monotonic()
    while f.calls()[-1:] != ['release 1'] and time.monotonic() < at + 1:
        time.sleep(0.005)
    assert f.calls()[-1:] == ['release 1'] and time.monotonic() - at < 0.15, f.calls()[-3:]
    key(f.keys, KEY_VOLUMEUP, 0)
    f.end()


def test_it_stops_by_itself_after_a_minute_and_a_find_starts_the_minute_again(binary, tree, tools, pwm_shim):
    """FM_MAX_MS, 60 s, run 30 times faster: 2 s"""
    f = Pwm(binary, tree, pwm_shim, extra=['--time-scale', '30'])
    assert ask_sys(tree, 'find') == 'ok'
    time.sleep(1.4)
    assert ask_sys(tree, 'find') == 'ok', 'again while it plays'
    time.sleep(1.4)
    assert ask_sys(tree, 'find state') == 'find 1', 'its minute started again'
    time.sleep(1.2)
    assert ask_sys(tree, 'find state') == 'find 0' and f.calls()[-1] == 'release 1'
    f.end()


def test_a_refused_config_takes_the_channel_again_once(binary, tree, tools, pwm_shim):
    f = Pwm(binary, tree, pwm_shim, env={'Q2PWM_REFUSE': '1'})
    assert ask_sys(tree, 'find') == 'ok'
    time.sleep(0.3)
    calls = f.calls()
    # fm_note lets the channel go, and fm_take releases once more before its REQUEST, as it does at the start
    assert calls[:9] == ['release 1', 'request PC01', 'level 1 0', 'config 2349 1 refused', 'release 1', 'release 1',
                         'request PC01', 'level 1 0', 'config 2349 1'], calls
    ask_sys(tree, 'find stop')
    f.end()


def test_with_no_pwm_driver_find_is_refused(binary, tree, tools, pwm_shim):
    f = Pwm(binary, tree, pwm_shim, device=False)
    assert ask_sys(tree, 'find') == 'err find' and not (tree/'run/vamp/finding').exists()
    f.end()


# ---- q2ask (docs/vamp-os-base.md#first-card-boot) ----

@pytest.fixture(scope='session')
def q2ask(tmp_path_factory):
    out = tmp_path_factory.mktemp('ask')/'q2ask'
    subprocess.run(['cc', '-std=gnu11', '-Wall', '-Wextra', '-Werror', '-O2', '-o', str(out), os.environ.get('Q2ASK_SOURCE') or str(ROOT/'sysd/q2ask.c')], check=True)  # Q2ASK_SOURCE: a broken copy
    return out


def test_q2ask_prints_the_greeting_and_an_answer_a_line(run, q2ask):
    root, _ = run
    sock = str(root/'run/vamp/sys.sock')
    r = subprocess.run([str(q2ask), sock, 'Q2SYS/1', 'battery', 'screen'], capture_output=True, text=True, timeout=5)
    assert (r.returncode, r.stdout) == (0, 'Q2SYS/1 ok\nbattery level=73 mv=3912 status=Discharging cable=0\nscreen on\n'), r
    r = subprocess.run([str(q2ask), sock, 'Q2SYS/9'], capture_output=True, text=True, timeout=5)
    assert (r.returncode, r.stdout) == (1, 'err version\n'), r
    r = subprocess.run([str(q2ask), str(root/'run/vamp/none.sock'), 'Q2SYS/1'], capture_output=True, text=True, timeout=5)
    assert r.returncode == 2


# ---- Key Tone (docs/sysd.md#key-tone) ----

def mcu_tree(tree, firmware=10000):
    """stock's MCU driver node and the firmware the image carries (/vendor/mcu, os/vendor/V1.36.tsv), FIRMWARE bytes"""
    put(tree, '/dev/mcu', '')
    (tree/'vendor/mcu').mkdir(parents=True, exist_ok=True)
    (tree/'vendor/mcu/libmcu-bare.bin').write_bytes(bytes(range(256)) * (firmware // 256) + bytes(firmware % 256))


def test_the_mcu_is_brought_up_at_the_start_as_demo_does(binary, tree, tools, pwm_shim):
    """demo's buzzer_init: cmd_mcu write_firmware /usr/data/libmcu-bare.bin (4096-byte pieces through mcu_write_mem),
    then cmd_mcu bootup, then cmd_mcu reset; nothing on the PWM"""
    mcu_tree(tree)
    f = Pwm(binary, tree, pwm_shim)
    assert f.calls() == ['mcu mem 0 4096', 'mcu mem 4096 4096', 'mcu mem 8192 1808', 'mcu bootup', 'mcu reset'], f.calls()
    f.end()


def test_a_click_is_the_mcu_s_buzzer_never_a_tone_and_one_while_it_sounds_is_dropped(binary, tree, tools, pwm_shim):
    """stock's buzzeer_switch: cmd_mcu write_str buzzer, the text with its NUL and a 300000 timeout; the PWM untouched"""
    mcu_tree(tree)
    f = Pwm(binary, tree, pwm_shim, env={'Q2MCU_MS': '150'})
    up = len(f.calls())
    a = Api(tree)
    a.line()
    a.say('click')
    first = a.line()
    a.say('click')
    second = a.line()
    time.sleep(0.3)
    assert (first, second) == ('ok', 'err click'), 'the second while the first is with the MCU'
    assert f.calls()[up:] == ['mcu write buzzer 7 300000'], f.calls()
    a.say('click')
    assert a.line() == 'ok', 'free again'
    assert ask_sys(tree, 'find') == 'ok'
    time.sleep(0.1)
    a.say('click')
    assert a.line() == 'err click', "the jingle's buzzer"
    ask_sys(tree, 'find stop')
    f.end()


def test_with_the_mcu_not_up_a_click_is_silent(binary, tree, tools, pwm_shim):
    """no firmware, or a driver refusing (ENODEV, as before the load): err click, no tone on the PWM"""
    put(tree, '/dev/mcu', '')
    f = Pwm(binary, tree, pwm_shim)
    assert ask_sys(tree, 'click') == 'err click'
    time.sleep(0.1)
    assert f.calls() == [], 'no firmware: nothing loaded, nothing sounded'
    f.end()
    mcu_tree(tree)
    f = Pwm(binary, tree, pwm_shim, env={'Q2MCU_DOWN': '1'})
    assert ask_sys(tree, 'click') == 'err click'
    time.sleep(0.1)
    assert f.calls() == [], 'the driver refusing: no tone either'
    f.end()
