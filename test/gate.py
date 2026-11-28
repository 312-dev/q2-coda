#!/usr/bin/env python3
"""q2gate (gate/q2gate.c, docs/gate.md) built for this machine against stand-ins for q2sysd, q2netd, q2btd and q2playd:
q2ui's pipe's lines turned into each service's words, and the three files q2ui reads (the status row's and Control
Center's states, Wi-Fi's networks, Bluetooth's devices) in demo's gate's formats. Run it as python3 test/gate.py
(pytest). Q2GATE_SOURCE builds another copy of q2gate.c, to show a broken one fails here."""
import os
import pathlib
import socket
import subprocess
import threading
import time

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SOURCE = pathlib.Path(os.environ.get('Q2GATE_SOURCE') or ROOT/'gate/q2gate.c')


@pytest.fixture(scope='session')
def binary(tmp_path_factory):
    out = tmp_path_factory.mktemp('gate')
    src = out/'q2gate.c'
    src.write_bytes(SOURCE.read_bytes())
    subprocess.run(['cc', '-std=gnu11', '-Wall', '-Wextra', '-Werror', '-O2', '-o', str(out/'q2gate'), str(src), '-pthread'], check=True)
    return out/'q2gate'


class Service:
    """a service's socket: greets with its version, answers each command from answers (a line or lines; ok when not
    there) and keeps the commands"""
    def __init__(self, root, name, version, answers=None):
        self.version, self.answers, self.lines = version, dict(answers or {}), []
        (root/'run/vamp').mkdir(parents=True, exist_ok=True)
        self.s = socket.socket(socket.AF_UNIX)
        self.s.bind(str(root/f'run/vamp/{name}.sock'))
        self.s.listen(8)
        threading.Thread(target=self.serve, daemon=True).start()

    def serve(self):
        while True:
            try:
                c, _ = self.s.accept()
            except OSError:
                return
            threading.Thread(target=self.one, args=(c,), daemon=True).start()

    def one(self, c):
        f = c.makefile('rw')
        try:
            for line in f:
                line = line.rstrip('\n')
                if line == self.version:
                    f.write(f'{self.version} ok\n')
                else:
                    self.lines.append(line)
                    f.write(self.answers.get(line, 'ok') + '\n')
                f.flush()
        except OSError:
            pass
        c.close()

    def asked(self):
        return [l for l in self.lines if l not in ('status', 'jack', 'battery', 'now', 'output', 'devices', 'networks')]


CONFIG = '[SYSSET]\nTIME24H=0\nLIGHTNESS=60\n[PLAYSET]\nMAXVOLUME=80\n[Q2POD]\nCARREMOTE=1\nSMARTWIFI=1\nUSBPICK=2\nLIGHTNESS=10\n'  # a key read from its own section
HOME_AP = ('ap ssid=Home signal=-60 secure=1\nap ssid=Caf%C3%A9 signal=-78 secure=0\nap ssid=Far signal=-90 secure=1\n'
           'ap ssid=Near signal=-68 secure=1\nap ssid=Back\\slash signal=-82 secure=1\nok')


@pytest.fixture
def gate(binary, tmp_path):
    root = tmp_path
    (root/'mnt/data').mkdir(parents=True)
    (root/'tmp').mkdir()
    (root/'mnt/data/config.ini').write_text(CONFIG)
    s = {
        'sys': Service(root, 'sys', 'Q2SYS/1', {'jack': 'jack po=1 bal=0', 'battery': 'battery level=73 mv=3912 status=Charging cable=1'}),
        'net': Service(root, 'net', 'Q2NET/1', {'status': 'status state=connected ssid=Home ip=10.0.0.5 smart=1 held=0',
                                               'scan': HOME_AP, 'networks': 'network ssid=Home\nok'}),
        'bt': Service(root, 'bt', 'Q2BT/1', {
            'status': 'status power=1 state=connected device=AA:BB:CC:DD:EE:01 name=Buds kind=headphones codec=AAC battery=80 output=bluetooth',
            'devices': 'device addr=AA:BB:CC:DD:EE:09 name=Speaker kind=speaker paired=0 connected=0 battery=-1\n'
                       'device addr=AA:BB:CC:DD:EE:01 name=Buds%20Pro kind=headphones paired=1 connected=1 battery=80\n'
                       'device addr=AA:BB:CC:DD:EE:02 name=Car%0AKit kind=car paired=1 connected=0 battery=-1\nok'}),
        'play': Service(root, 'play', 'Q2PLAY/1', {'now': 'now title=A artist=B album=C state=1 pos=3 len=10 volume=37 id=1',
                                                  'output': 'output dac'}),
    }
    p = subprocess.Popen([str(binary), '--root', str(root)], stderr=subprocess.PIPE, text=True)
    end = time.monotonic() + 5
    while not (root/'run/vamp/ui.ctl').exists() and time.monotonic() < end:
        time.sleep(0.02)
    ctl = os.open(root/'run/vamp/ui.ctl', os.O_WRONLY | os.O_NONBLOCK)
    yield root, s, ctl
    os.close(ctl)
    p.terminate()
    assert p.wait(5) in (0, -15)


def send(ctl, *lines):
    os.write(ctl, ''.join(l + '\n' for l in lines).encode())


def until(f, secs=4.0):
    end = time.monotonic() + secs
    while time.monotonic() < end:
        v = f()
        if v:
            return v
        time.sleep(0.05)
    return f()


def text(root, name):
    p = root/'tmp'/name
    return p.read_text() if p.exists() else ''


def config(root):
    return (root/'mnt/data/config.ini').read_text()


STATUS = ('out=wire\nbt=headset\nbtbatt=80\nrelay=none\nlock=0\nsync=0\nsyncpm=0\noffline=0\nnet=1\nclock24=0\n'
          'wifi=1\nbton=1\nbtbusy=0\nairplay=0\ncar=1\nsmart=1\neq=0\nusb=Transfer Files\ncable=1\nbright=60\nvol=37\nvolmax=80\n')


def test_the_status_file_has_the_status_row_s_and_control_center_s_states(gate):
    """status_row.c status_row_state and control_center.c q2ui_cc_state, in their order"""
    root, s, ctl = gate
    assert until(lambda: text(root, 'q2ui.status') == STATUS), text(root, 'q2ui.status')


def test_a_jack_on_bluetooth_is_no_wire_and_a_car_is_the_car_slot(gate):
    root, s, ctl = gate
    s['play'].answers['output'] = 'output bluetooth'
    s['bt'].answers['status'] = 'status power=1 state=connected device=A name=BMW kind=car codec=SBC battery=-1 output=bluetooth'
    assert until(lambda: 'out=other\nbt=car\nbtbatt=-1\n' in text(root, 'q2ui.status')), text(root, 'q2ui.status')
    s['bt'].answers['status'] = 'status power=0 state=off device= name= kind= codec= battery=-1 output=dac'
    assert until(lambda: 'bt=none\n' in text(root, 'q2ui.status') and 'bton=0\n' in text(root, 'q2ui.status'))


def test_the_switches_and_settings_are_set_as_demo_sets_them(gate):
    root, s, ctl = gate
    send(ctl, 'set wifi 0', 'set bt 1', 'set smart 0', 'set light 2', 'set motion 1', 'set home 1', 'set songartist 3',
         'set songlength 2', 'set night 2', 'set nightk 3100', 'set gapless 0', 'set xfade 1', 'set car 0', 'set bright 40',
         'set light 9', 'set eq 1')
    assert until(lambda: 'brightness 40' in s['sys'].asked())
    assert s['net'].asked() == ['off', 'smart off'] and s['bt'].asked() == ['power on'], (s['net'].asked(), s['bt'].asked())
    c = config(root)
    for line in ('LIGHT=2', 'REDUCEMOTION=1', 'HOME=1', 'SONGARTIST=3', 'SONGLENGTH=2', 'NIGHTSHIFT=2', 'NIGHTK=3100',
                 'GAPLESS=0', 'XFADE=1', 'CARREMOTE=0', 'SMARTWIFI=0', 'LIGHTNESS=40'):
        assert f'\n{line}\n' in c, (line, c)
    assert 'LIGHT=9' not in c, 'a value out of its range refused'
    assert c.index('[PLAYSET]') < c.index('GAPLESS=0') < c.index('[Q2POD]'), 'in its own section'
    assert c.index('[IPOD]') < c.index('LIGHT=2'), 'a missing section added'


def test_a_search_lists_the_networks_the_joined_first_with_stock_s_levels(gate):
    """wifi_flows.c wifi_flows_networks: "scan N", then LEVEL (stock's steps from the signal) LOCK STATE NAME, the one
    joined first, a name's \\ and newline escaped"""
    root, s, ctl = gate
    send(ctl, 'scan')
    assert until(lambda: 'scan' in s['net'].asked())
    want = 'scan 0\n4 1 2 Home\n2 0 0 Café\n1 1 0 Far\n3 1 0 Near\n1 1 0 Back\\\\slash\n'  # -68 dBm 3, -82 1
    assert until(lambda: text(root, 'q2ui.wifi') == want), text(root, 'q2ui.wifi')


def test_join_saves_an_open_network_and_follows_it_to_connected(gate):
    root, s, ctl = gate
    send(ctl, 'scan')
    assert until(lambda: '0 0 Caf' in text(root, 'q2ui.wifi'))
    send(ctl, 'join Café')
    assert until(lambda: s['net'].asked()[1:] == ['add Caf\u00e9', 'wake']), s['net'].asked()
    assert until(lambda: '2 0 1 Café\n' in text(root, 'q2ui.wifi')), 'Connecting while it joins'
    s['net'].answers['status'] = 'status state=connected ssid=Caf%C3%A9 ip=10.0.0.9 smart=1 held=0'
    assert until(lambda: text(root, 'q2ui.wifi').startswith('scan 0\n2 0 2 Café\n')), text(root, 'q2ui.wifi')


def test_join_of_a_secured_network_not_saved_says_it_could_not(gate):
    """its password is typed on demo's kbwifipass_dialog, which Vamp OS has no page for yet"""
    root, s, ctl = gate
    send(ctl, 'scan')
    assert until(lambda: '1 1 0 Far' in text(root, 'q2ui.wifi'))
    send(ctl, 'join Far')
    assert until(lambda: '1 1 3 Far\n' in text(root, 'q2ui.wifi')), text(root, 'q2ui.wifi')
    assert not [l for l in s['net'].asked() if l.startswith('add')]


def test_the_devices_paired_first_and_btlink_does_what_the_row_click_does(gate):
    """bt_ui.c bt_q2ui_devices: "scan N", then PAIRED STATE MAC NAME, the paired first; btlink disconnects the
    connected one, connects a paired one and pairs a found one"""
    root, s, ctl = gate
    want = 'scan 0\n1 2 AA:BB:CC:DD:EE:01 Buds Pro\n1 0 AA:BB:CC:DD:EE:02 Car\\nKit\n0 0 AA:BB:CC:DD:EE:09 Speaker\n'
    assert until(lambda: text(root, 'q2ui.bt') == want), text(root, 'q2ui.bt')
    send(ctl, 'btscan')
    assert until(lambda: text(root, 'q2ui.bt').startswith('scan 1\n')) and 'scan' in s['bt'].asked()
    send(ctl, 'btlink AA:BB:CC:DD:EE:01', 'btlink AA:BB:CC:DD:EE:02', 'btlink AA:BB:CC:DD:EE:09', 'btforget AA:BB:CC:DD:EE:02',
         'btlink nonsense')
    assert until(lambda: len(s['bt'].asked()) >= 5)
    assert sorted(s['bt'].asked()[1:]) == ['connect AA:BB:CC:DD:EE:02', 'disconnect AA:BB:CC:DD:EE:01', 'forget AA:BB:CC:DD:EE:02',
                                          'pair AA:BB:CC:DD:EE:09'], s['bt'].asked()


def test_the_screen_s_two_words_and_a_line_too_long_dropped(gate):
    root, s, ctl = gate
    send(ctl, 'x' * 100 + 'screenoff', 'screenoff', 'wake')
    assert until(lambda: s['sys'].asked() == ['screen off', 'screen on']), s['sys'].asked()


if __name__ == '__main__':
    import sys
    sys.exit(pytest.main([__file__, '-q', '-p', 'no:cacheprovider']))
