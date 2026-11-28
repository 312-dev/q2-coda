"""tools/btrelay/q2side.py, `vamp bt`: the Q2's half of the Bluetooth relay (docs/bt-relay.md#on-the-q2). The Q2
here is a recorder of the shell text each command would run and a table of what it answers; what the text does on
a real Q2 is the device procedure's to show (docs/bt-relay.md#procedure)."""
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'tools' / 'btrelay'))
import btsignal  # noqa: E402
import q2side  # noqa: E402

LAPTOP = 'AA:BB:CC:00:11:22'


class Q2:
    """Answers a command by the first pattern in `answers` found in its text; records every command."""
    def __init__(self, answers=()):
        self.answers, self.ran, self.files, self.pulled = list(answers), [], {}, []

    def shell(self, cmd, data=b'', timeout=30, **_):
        self.ran.append(cmd)
        for pattern, reply in self.answers:
            if re.search(pattern, cmd):
                return reply if isinstance(reply, bytes) else reply.encode()
        return b''

    def put(self, data, remote, mode=0o600):
        self.files[remote] = (data, mode)


def run(q2, *argv):
    lines = []
    status = q2side.main(q2, list(argv), out=lines.append, pull=lambda remote, local: q2.pulled.append((remote, local)))
    return status, '\n'.join(lines)


def test_an_address_is_checked_before_it_reaches_a_shell():
    assert q2side.device_path('aa:bb:cc:00:11:22') == '/org/bluez/hci0/dev_AA_BB_CC_00_11_22'
    for bad in ('AA:BB:CC:00:11', 'AA:BB:CC:00:11:2G', "AA:BB:CC:00:11:22'; reboot", ''):
        with pytest.raises(ValueError, match='address'):
            q2side.device_path(bad)


def test_the_pcms_bluealsa_lists_from_both_daemons():
    for name, rate in (('own-SBC.txt', 48000), ('stock-SBC.txt', 48000)):
        pcms = q2side.parse_pcms((ROOT / 'test/bt-fixtures' / name).read_text())
        source = [p for p in pcms if p['transport'] == 'A2DP-source']
        assert source and source[0]['codec'] == 'SBC' and source[0]['rate'] == rate
        assert source[0]['path'].endswith('/a2dpsrc/sink')
    assert q2side.parse_pcms('Error org.freedesktop.DBus.Error.ServiceUnknown') == []


def test_connect_and_disconnect_ask_bluetoothd_for_that_device_only():
    q2 = Q2()
    assert run(q2, 'connect', LAPTOP)[0] == 0
    assert 'dev_AA_BB_CC_00_11_22 org.bluez.Device1.Connect' in q2.ran[-1] and '--system' in q2.ran[-1]
    run(q2, 'disconnect', LAPTOP)
    assert 'dev_AA_BB_CC_00_11_22 org.bluez.Device1.Disconnect' in q2.ran[-1]
    run(q2, 'forget', LAPTOP)
    assert 'org.bluez.Adapter1.RemoveDevice objpath:/org/bluez/hci0/dev_AA_BB_CC_00_11_22' in q2.ran[-1]


def test_a_dbus_error_is_a_failure_with_its_text():
    q2 = Q2([('Device1.Connect', 'Error org.bluez.Error.Failed: Host is down\n')])
    status, said = run(q2, 'connect', LAPTOP)
    assert status == 1 and 'Host is down' in said


def test_discoverable_sets_the_time_before_the_switch_and_off_clears_it():
    q2 = Q2()
    run(q2, 'discoverable', 'on', '90')
    cmd = q2.ran[-1]
    assert cmd.index('DiscoverableTimeout variant:uint32:90') < cmd.index('Discoverable variant:boolean:true')
    run(q2, 'discoverable', 'off')
    assert 'Discoverable variant:boolean:false' in q2.ran[-1]
    with pytest.raises(ValueError):
        run(q2, 'discoverable', 'maybe')


def pcm_answer(codec='SBC'):
    return (ROOT / 'test/bt-fixtures/own-SBC.txt').read_text().replace('string "SBC"', f'string "{codec}"')


def test_codec_names_what_is_selected_and_selects_by_bluealsas_names():
    q2 = Q2([('GetManagedObjects', pcm_answer()), ('GetCodecs', 'string "SBC"\nstring "AAC"\nstring "aptX-HD"\n')])
    status, said = run(q2, 'codec')
    assert status == 0 and 'SBC 48000 Hz' in said and 'offered: SBC AAC aptX-HD' in said
    status, said = run(q2, 'codec', 'aptx-hd')
    pcm = '/org/bluealsa/hci0/dev_02_00_00_00_00_01/a2dpsrc/sink'
    assert any(f'q2bt-codec {pcm} aptX-HD' in c for c in q2.ran)
    with pytest.raises(ValueError, match='SBC, AAC, aptX, aptX-HD, LDAC'):
        run(q2, 'codec', 'opus')


def test_codec_without_a_stream_says_so():
    status, said = run(Q2(), 'codec', 'SBC')
    assert status == 1 and 'no A2DP source PCM' in said


def test_play_pushes_the_signal_at_the_streams_rate_and_plays_it_through_bluealsa():
    q2 = Q2([('GetManagedObjects', pcm_answer())])
    assert run(q2, 'play')[0] == 0
    data, _mode = q2.files[q2side.WAV]
    assert data == btsignal.wav(48000, 16)
    assert f'aplay -q -D "{q2side.PCM}" {q2side.WAV}' in q2.ran[-1]


def test_play_refuses_without_a_stream_and_reports_aplays_failure():
    assert run(Q2(), 'play')[0] == 1
    q2 = Q2([('GetManagedObjects', pcm_answer()), ('aplay', 'aplay: main:828: audio open error: Device or resource busy\nrc=1\n')])
    status, said = run(q2, 'play')
    assert status == 1 and 'busy' in said and 'pause' in said


def test_the_capture_needs_hcidump_and_is_one_at_a_time():
    q2 = Q2([('snoop.*start|hcidump -w', 'no /tmp/hcidump\nrc=1\n')])
    status, said = run(q2, 'snoop', 'start')
    assert status == 1 and 'snoop install' in said
    q2 = Q2()
    assert run(q2, 'snoop', 'start')[0] == 0
    start = q2.ran[-1]
    assert f'hcidump -w {q2side.SNOOP}' in start and 'setsid' in start and 'pidof hcidump' in start
    status, said = run(q2, 'snoop', 'stop', 'walk.snoop')
    assert 'kill -INT' in q2.ran[-1] and q2.pulled == [(q2side.SNOOP, 'walk.snoop')]
    assert 'btsnoop.py walk.snoop' in said


def test_snoop_install_pushes_stocks_hcidump_from_a_build(tmp_path, monkeypatch):
    (tmp_path / 'stock.squashfs').write_bytes(b'hsqs')
    asked = []

    def unsquash(argv, **_):
        asked.append(argv)
        return b'\x7fELF-hcidump'

    monkeypatch.setattr(q2side.subprocess, 'check_output', unsquash)
    q2 = Q2()
    assert run(q2, 'snoop', 'install', str(tmp_path))[0] == 0
    assert asked == [['unsquashfs', '-cat', str(tmp_path / 'stock.squashfs'), 'usr/bin/hcidump']]
    assert q2.files[q2side.HCIDUMP] == (b'\x7fELF-hcidump', 0o755)
    with pytest.raises(ValueError, match='stock.squashfs'):
        run(q2, 'snoop', 'install', str(tmp_path / 'none'))


def test_status_is_read_only():
    q2 = Q2([('GetManagedObjects', '== adapter\nhci0:\tType: Primary  Bus: UART\n== pcms\n' + pcm_answer('LDAC'))])
    status, said = run(q2, 'status')
    assert status == 0 and 'Bus: UART' in said and 'method return' not in said
    assert 'pcm A2DP-source sink: LDAC 48000 Hz  /org/bluealsa/hci0/dev_02_00_00_00_00_01/a2dpsrc/sink' in said
    assert 'pcm: none' in run(Q2(), 'status')[1]
    assert not any(re.search(r'Properties\.Set|Connect|kill|aplay|q2bt-codec', c) for c in q2.ran)


def test_usage():
    with pytest.raises(ValueError, match='usage: vamp bt'):
        run(Q2(), 'dance')
