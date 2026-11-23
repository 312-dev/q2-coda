"""bt_battery.c: a connected headset's battery for the status row, from BlueZ's Battery1 through a shell run off the
UI thread that writes "MAC PCT RATE CLASS NAME" to /tmp/q2-btbatt: once as the headset connects, then, with the screen
lit, every five minutes (every 30 s while no level came yet) and as the codec changes; nothing while dark."""
import ctypes as C
import os
import pathlib
import re
import subprocess

import pytest

from conftest import Files, threads_now

MAC = 'AA:BB:CC:DD:EE:0F'
FILE = '/tmp/q2-btbatt'
POLL_MS, RETRY_MS, TRIES = 300000, 30000, 10
class Bt:
    """Stock's link list (MAC, status), Bluetooth's switch, the screen, the clock, the files and the commands run; each
    run ends at once (threads_now), its answer what BlueZ says now: answer, (level, rate, class, name), a field None for
    none, the Class and name only for a run that asks for them."""
    def __init__(self, fw, links=((MAC, 3),), on=1, answer=(80, 48000, 2360324, 'WH-1000XM5')):
        self.fw, self.links, self.now, self.commands, self.answer = fw, list(links), 1000, [], answer
        self.files = Files(fw, {})
        self.runs = threads_now(fw)
        fw.var('g_bluetoothflag', C.c_ubyte).value = on
        self.lit(1)
        fw.mock('getBtLinkSize', lambda *_: len(self.links))
        fw.mock('getBtLinkItem', self.item)
        fw.mock('time_now_ms', lambda *_: self.now)
        fw.mock('fw_system', self.system)
        self.level = fw.fn('bt_headset_battery', C.c_int)

    def lit(self, on):
        self.fw.var('g_backlight_status', C.c_ubyte).value = on

    def item(self, i, status, mac, *_):
        m, s = self.links[i]
        C.c_int.from_address(status).value = s
        C.memmove(mac, m.encode() + b'\0', len(m) + 1)
        return 0

    def system(self, cmd, *_):
        command = C.string_at(cmd).decode()
        self.commands.append(command)
        if FILE in command:
            said = re.search(r'echo "(\S+) ', command)
            assert said, command
            mac = said[1]
            level, rate, cls, name = (('' if v is None else str(v)) for v in self.answer)
            if 'string:Class' not in command:
                cls = name = ''
            self.files.files[FILE] = f'{mac} {level} {rate} {cls} {name}\n'.encode()
        return 0

    def at(self, ms):
        """ms on: bt_headset_battery's answer then."""
        self.now += ms
        return self.level()


@pytest.mark.parametrize('links, on', [((), 1), (((MAC, 1),), 1), (((MAC, 3),), 0), ((('AA:BB:CC:DD:EE:0F;x', 3),), 1)])
def test_none_without_a_connected_headset(fw, links, on):
    b = Bt(fw, links, on)
    assert b.level() == -1 and not b.commands


def test_a_connection_is_asked_once_off_the_ui_thread_and_its_answer_read_once(fw):
    b = Bt(fw)
    assert b.level() == 80  # asked as it connected; the run's answer read as it ended
    assert len(b.commands) == 1 and len(b.runs) == 1 and '/org/bluez/hci0/dev_AA_BB_CC_DD_EE_0F' in b.commands[0]
    assert not b.commands[0].rstrip().endswith('&')  # the thread waits for it: its end is the answer's
    b.answer = (75, 48000, None, None)
    for _ in range(100):
        assert b.at(500) == 80  # nothing asked, nothing read, for minutes
    assert len(b.commands) == 1
    assert b.at(POLL_MS) == 75 and len(b.commands) == 2  # five minutes on, lit: asked again, read as it ended
    assert fw.fn('bt_headset_class', C.c_uint)() == 2360324  # a later run's: the Class and name kept
    assert fw.fn('bt_headset_name', C.c_char_p)() == b'WH-1000XM5'
    b.links = []
    assert b.level() == -1
    b.links = [(MAC, 3)]
    b.level()
    assert len(b.commands) == 3 and 'string:Class' in b.commands[-1]  # connected again: asked whole


def test_dark_nothing_is_asked(fw):
    b = Bt(fw)
    b.level()
    b.lit(0)
    for _ in range(20):
        b.at(POLL_MS)
    assert len(b.commands) == 1
    b.lit(1)
    b.at(500)
    assert len(b.commands) == 2  # lit again with an old answer: asked at once


def test_no_level_yet_is_asked_every_30_s_lit_ten_times_then_every_five_minutes(fw):
    b = Bt(fw, answer=(None, 48000, 2360324, 'WH-1000XM5'))
    assert b.level() == -1
    for _ in range(15):
        b.at(RETRY_MS)
    assert len(b.commands) == TRIES  # 30 s apart, then no more for a while
    b.at(POLL_MS - 6 * RETRY_MS)  # five minutes after the last
    assert len(b.commands) == TRIES + 1


def test_a_codec_change_is_asked_at_once_for_its_rate(fw):
    b = Bt(fw)
    fw.var('fw_ctx_bt_showcoding', C.c_int).value = 0
    b.level()
    b.at(500)
    assert len(b.commands) == 1
    b.answer = (80, 96000, None, None)
    fw.var('fw_ctx_bt_showcoding', C.c_int).value = 2  # streaming now: AAC
    b.at(500)
    assert len(b.commands) == 2 and fw.fn('bt_headset_rate', C.c_uint)() == 96000


def test_a_run_still_going_is_not_started_again(fw):
    b = Bt(fw)
    b.level()
    started = []
    fw.mock('fw_pthread_create', lambda t, *_: started.append(1) or C.c_ulong.from_address(t).__setattr__('value', 1) or 0)
    b.links = [('AA:BB:CC:DD:EE:01', 3)]
    for _ in range(5):
        b.at(POLL_MS)
    assert len(started) == 1  # the new connection's run, which never ends: no other started


def programs(command):
    """The programs a run starts: the shell, and each dbus-send ($g is one, named once as g='dbus-send ...'), sed,
    grep, head, mv and sleep."""
    words = re.findall(r'\$g |\bdbus-send\b|\bsed\b|\bgrep\b|\bhead\b|\bmv\b|\bsleep\b', command)
    return 1 + len(words) - ("g='dbus-send" in command)


def test_ten_minutes_connected_start_this_many_processes(fw):
    """Ten minutes with a headset connected, link_poll asking every 500 ms: 18 programs lit, 11 dark."""
    b = Bt(fw)
    for _ in range(1200):
        b.at(500)
    assert [programs(c) for c in b.commands] == [11, 7]  # the connection's whole run, the level's five minutes on
    b = Bt(fw)
    b.links = []
    b.level()  # the last connection gone
    b.links = [(MAC, 3)]
    b.level()
    b.lit(0)
    for _ in range(1200):
        b.at(500)
    assert [programs(c) for c in b.commands] == [11]  # dark: the connection's run alone


@pytest.mark.parametrize('text', [f'{MAC} \n', 'AA:BB:CC:DD:EE:00 80\n', f'{MAC} 101\n', f'{MAC} 1000\n', f'{MAC}80\n'])
def test_another_device_s_none_or_a_bad_level_is_none(fw, text):
    b = Bt(fw, answer=(None, None, None, None))
    b.level()
    b.files.files[FILE] = text.encode()
    fw.mock('fw_system', lambda *_: 0)  # the next run leaves the file as it is
    assert b.at(RETRY_MS) == -1 and b.at(500) == -1


def test_the_run_under_sh(fw, tmp_path):
    """The command, a stand-in dbus-send answering as BlueZ does: Percentage 80, the Class, the Alias, and bluealsa's
    PCMs with this device's A2DP source at 96 kHz. It writes "MAC 80 96000 CLASS NAME"; a later run the level and
    rate alone."""
    b = Bt(fw)
    b.level()
    bin_ = tmp_path / 'bin'
    bin_.mkdir()
    out = tmp_path / 'btbatt'
    (bin_ / 'dbus-send').write_text(
        '#!/bin/sh\n'
        f'[ "$4" = /org/bluealsa ] && [ "$5" = org.bluealsa.Manager1.GetPCMs ] && cat {tmp_path}/pcms && exit 0\n'
        f'[ "$4" = /org/bluez/hci0/dev_AA_BB_CC_DD_EE_0F ] || exit 1\n'
        'case "$7" in\n'
        '  string:Class) echo "   variant       uint32 2360324";;\n'
        '  string:Alias) echo \'   variant       string "Gray\'"\'"\'s \\"Buds\\""\';;\n'
        '  string:Percentage) echo "method return time=1 sender=:1.2"; echo "   variant       byte 80";;\n'
        'esac\n')
    # bluealsa's GetPCMs: another device's PCM first (its Sampling 44100), then this one's A2DP source at 96 kHz
    pcm = ('   dict entry(\n      object path "/org/bluealsa/hci0/dev_{}/a2dpsrc/sink"\n      array [\n'
           '         dict entry(\n            string "Device"\n            variant object path "/org/bluez/hci0/dev_{}"\n'
           '         )\n         dict entry(\n            string "Sampling"\n            variant uint32 {}\n         )\n'
           '      ]\n   )\n')
    (tmp_path / 'pcms').write_text('method return\n   array [\n' + pcm.format('11_22_33_44_55_66', '11_22_33_44_55_66', 44100)
                                   + pcm.format('AA_BB_CC_DD_EE_0F', 'AA_BB_CC_DD_EE_0F', 96000) + '   ]\n')
    (bin_ / 'dbus-send').chmod(0o755)
    env = {**os.environ, 'PATH': f'{bin_}:{os.environ["PATH"]}'}
    subprocess.run(['sh', '-c', b.commands[0].replace(FILE, str(out))], check=True, env=env)
    assert out.read_text() == f'{MAC} 80 96000 2360324 Gray\'s \\"Buds\\"\n'
    b.at(POLL_MS)
    subprocess.run(['sh', '-c', b.commands[1].replace(FILE, str(out))], check=True, env=env)
    assert out.read_text() == f'{MAC} 80 96000  \n'


@pytest.mark.parametrize('text, level, rate, cls, name', [
    (f'{MAC} 80 96000 2360324 WH-1000XM5\n', 80, 96000, 2360324, b'WH-1000XM5'),
    (f'{MAC}  48000 2098208 BMW 6R81\n', -1, 48000, 2098208, b'BMW 6R81'),
    (f'{MAC} 5   \n', 5, 0, 0, b''), (f'{MAC} 5 999999999  \n', 5, 0, 0, b'')])
def test_the_rate_class_and_name_ride_with_the_level(fw, text, level, rate, cls, name):
    b = Bt(fw)
    fw.mock('fw_system', lambda *_: b.files.files.__setitem__(FILE, text.encode()) or 0)  # the run's answer
    assert b.level() == level  # asked, and read as the run ended
    assert fw.fn('bt_headset_rate', C.c_uint)() == rate and fw.fn('bt_headset_class', C.c_uint)() == cls
    assert fw.fn('bt_headset_name', C.c_char_p)() == name
