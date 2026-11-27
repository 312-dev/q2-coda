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
import time

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SOURCE = pathlib.Path(os.environ.get('Q2SYSD_SOURCE') or ROOT/'sysd/q2sysd.c')
KEY_POWER, KEY_VOLUMEUP, EV_KEY = 116, 115, 1


@pytest.fixture(scope='session')
def binary(tmp_path_factory):
    out = tmp_path_factory.mktemp('sysd')
    src = out/'q2sysd.c'
    src.write_bytes(SOURCE.read_bytes())
    subprocess.run(['cc', '-std=c11', '-Wall', '-Wextra', '-Werror', '-O2', '-o', str(out/'q2sysd'), str(src)], check=True)
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
    return tmp_path


@pytest.fixture
def run(binary, tree):
    p = subprocess.Popen([str(binary), '--root', str(tree), '--poweroff', f'touch {tree}/powered-off'], stderr=subprocess.PIPE)
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


if __name__ == '__main__':
    sys.exit(pytest.main([__file__, '-q', '-p', 'no:cacheprovider']))
