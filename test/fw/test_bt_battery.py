"""bt_battery.c: a connected headset's battery for the status row, from BlueZ's Battery1 through a shell loop that
writes "MAC PCT" to /tmp/q2-btbatt while the device stays connected."""
import ctypes as C
import os
import pathlib
import subprocess

import pytest

from conftest import Files

MAC = 'AA:BB:CC:DD:EE:0F'
FILE = '/tmp/q2-btbatt'


class Bt:
    """Stock's link list (MAC, status), Bluetooth's switch, the clock, the files and the commands run."""
    def __init__(self, fw, links=((MAC, 3),), on=1):
        self.fw, self.links, self.now, self.commands = fw, list(links), 1000, []
        self.files = Files(fw, {})
        fw.var('g_bluetoothflag', C.c_ubyte).value = on
        fw.mock('getBtLinkSize', lambda *_: len(self.links))
        fw.mock('getBtLinkItem', self.item)
        fw.mock('time_now_ms', lambda *_: self.now)
        fw.mock('fw_system', lambda cmd, *_: self.commands.append(C.string_at(cmd).decode()) or 0)
        self.level = fw.fn('bt_headset_battery', C.c_int)

    def item(self, i, status, mac, *_):
        m, s = self.links[i]
        C.c_int.from_address(status).value = s
        C.memmove(mac, m.encode() + b'\0', len(m) + 1)
        return 0

    def say(self, text, ms=5000):
        self.files.files[FILE] = text.encode()
        self.now += ms
        return self.level()


@pytest.mark.parametrize('links, on', [((), 1), (((MAC, 1),), 1), (((MAC, 3),), 0), ((('AA:BB:CC:DD:EE:0F;x', 3),), 1)])
def test_none_without_a_connected_headset(fw, links, on):
    b = Bt(fw, links, on)
    assert b.level() == -1 and not b.commands


def test_one_loop_per_connection_and_its_level(fw):
    b = Bt(fw)
    assert b.level() == -1  # nothing known yet
    assert len(b.commands) == 1 and '/org/bluez/hci0/dev_AA_BB_CC_DD_EE_0F' in b.commands[0]
    assert b.say(f'{MAC} 80\n') == 80
    assert b.say(f'{MAC} 75\n', 1000) == 80  # read again only every 5 s
    assert b.say(f'{MAC} 75\n') == 75 and len(b.commands) == 1
    b.links = []
    assert b.level() == -1
    b.links = [(MAC, 3)]
    b.level()
    assert len(b.commands) == 2  # connected again: a loop of its own


@pytest.mark.parametrize('text', [f'{MAC} \n', 'AA:BB:CC:DD:EE:00 80\n', f'{MAC} 101\n', f'{MAC} 1000\n', f'{MAC}80\n'])
def test_another_device_s_none_or_a_bad_level_is_none(fw, text):
    b = Bt(fw)
    b.level()
    assert b.say(text) == -1


def test_a_loop_that_ended_starts_again_after_a_minute(fw):
    b = Bt(fw)
    b.level()
    assert b.say('', 30000) == -1 and len(b.commands) == 1
    assert b.say('', 30000) == -1 and len(b.commands) == 2


def test_the_loop_under_sh(fw, tmp_path):
    """The command, a stand-in dbus-send answering as BlueZ does: Connected true twice, then false; Percentage 80.
    It writes "MAC 80" while connected and empties the file at the end."""
    b = Bt(fw)
    b.level()
    bin_ = tmp_path / 'bin'
    bin_.mkdir()
    count, out = tmp_path / 'count', tmp_path / 'btbatt'
    (bin_ / 'dbus-send').write_text(
        '#!/bin/sh\n'
        f'[ "$4" = /org/bluez/hci0/dev_AA_BB_CC_DD_EE_0F ] || exit 1\n'
        'case "$7" in\n'
        f'  string:Connected) n=$(cat {count} 2>/dev/null || echo 0); echo $((n+1)) > {count}\n'
        f'    [ $n -lt 2 ] && echo "   variant       boolean true" || echo "   variant       boolean false";;\n'
        '  string:Percentage) echo "method return time=1 sender=:1.2"; echo "   variant       byte 80";;\n'
        'esac\n')
    (bin_ / 'sleep').write_text(f'#!/bin/sh\ncp {out} {tmp_path}/seen 2>/dev/null\n')
    for f in bin_.iterdir():
        f.chmod(0o755)
    command = b.commands[0].replace(FILE, str(out)).replace(') >/dev/null 2>&1 &', ') >/dev/null 2>&1')
    subprocess.run(['sh', '-c', command], check=True, env={**os.environ, 'PATH': f'{bin_}:{os.environ["PATH"]}'})
    assert (tmp_path / 'seen').read_text() == f'{MAC} 80\n' and out.read_text() == ''
    assert pathlib.Path(count).read_text().strip() == '3'
