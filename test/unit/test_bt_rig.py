"""tools/bt_rig.py: the Bluetooth receiver rig's driver (docs/bt-rig.md), against a fake adb, a fake btrig.exe and a
fake coda-rc session; nothing here reaches a Q2, Windows or the network."""
import io
import json
import subprocess
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'tools'))
import bt_rig as br  # noqa: E402
import coda_rc_client as cr  # noqa: E402
import q2remote as qr  # noqa: E402

REAL_WIN_PATH = br.win_path

LAPTOP = '4C:A9:54:59:F0:0F'

BLUEZ = '''method return time=1 sender=:1.33 -> destination=:1.42 serial=65 reply_serial=2
   array [
      dict entry(
         object path "/org/bluez/hci0"
         array [
               string "Name"
               variant                   string "Q2 Muse"
         ]
      )
      dict entry(
         object path "/org/bluez/hci0/dev_4C_A9_54_59_F0_0F"
         array [
               string "Name"
               variant                   string "GRAY_THINKPAD"
               string "Paired"
               variant                   boolean %s
               string "Trusted"
               (a line between a key and its value)
               variant                   boolean false
               string "Connected"
               variant                   boolean %s
               string "UUIDs"
               variant                   array [ ]
         ]
      )
      dict entry(
         object path "/org/bluez/hci0/dev_20_AF_1B_18_FD_03"
         array [
               string "Name"
               variant                   string "Arctis"
               string "Paired"
               variant                   boolean true
               string "Connected"
               variant                   boolean false
         ]
      )
      dict entry(
         object path "/org/bluez/hci0/dev_20_AF_1B_18_FD_03/fd0"
         array [ ]
      )
   ]
'''

PCMS = '''method return
   array [
      dict entry(
         object path "/org/bluealsa/hci0/dev_4C_A9_54_59_F0_0F/a2dpsrc/sink"
         array [
               string "Device"
               variant                   object path "/org/bluez/hci0/dev_4C_A9_54_59_F0_0F"
               string "Codec"
               variant                   string "AAC"
               string "Sampling"
               variant                   uint32 48000
         ]
      )
   ]
'''

DEVICES = '''I: Bus=0019 Vendor=0001 Product=0001 Version=0100
N: Name="md-gpio-keys"
H: Handlers=kbd event1
B: KEY=1

I: Bus=0018
N: Name="hyn_ts"
H: Handlers=mouse0 event2

N: Name="hyn_sliderbar"
H: Handlers=mouse1 event3
'''

LOG = '''2026-11-23T22:42:00Z 1 I player q2player: before
2026-11-23T22:42:10Z 2 I player q2player: smart crossfade: plain, a stream
garbage line
2026-11-23T22:43:00Z 3 I bt after
'''


class Q2:
    """A fake adb: each exec-out script answered by what it asks for."""

    def __init__(self):
        self.paired = 'false'
        self.connected = 'true'
        self.scripts = []
        self.fail = None

    def answer(self, args):
        if self.fail and self.fail in ' '.join(args):
            return subprocess.CompletedProcess(args, 1, b'', b'error: device offline')
        if args[0] == 'forward':
            return subprocess.CompletedProcess(args, 0, b'', b'')
        cmd = args[1]
        self.scripts.append(cmd)
        out = ''
        if 'GetManagedObjects' in cmd:
            out = BLUEZ % (self.paired, self.connected)
        elif 'GetPCMs' in cmd:
            out = PCMS
        elif cmd == 'cat /proc/bus/input/devices':
            out = DEVICES
        elif cmd == 'cat /mnt/data/config.ini':
            out = 'VOLUME=52\nBTLINKMAC=20:AF:1B:18:FD:03\nOTHER=1\n[Q2POD]\nXFADE=1\n'
        elif cmd == 'cat /tmp/coda-rc.now':
            out = 'state=2\npos=0\nnoequals\n'
        elif cmd.startswith('q2update version'):
            out = '1234 version V1UQi -\n'
        elif cmd.startswith('dd if=/proc'):
            out = '01\n'
        elif cmd == 'date -u +%s':
            out = '1795473857\n'
        elif cmd.startswith('q2guard --dump'):
            out = LOG
        elif cmd.startswith('cat /tmp/coda-rc.pair'):
            out = '123456\nbt-rig\n'
        elif 'q2remote --shot' in cmd:
            import gzip
            return subprocess.CompletedProcess(args, 0, gzip.compress(b'Q2FB\0frame'), b'')
        return subprocess.CompletedProcess(args, 0, out.encode(), b'')


class FakeProc:
    """A btrig.exe started with Popen: its JSON lines on stdout, an exit status; stdin kept for pipe."""

    def __init__(self, lines, rc=0):
        self.stdout = io.StringIO(''.join(json.dumps(l) + '\n' if isinstance(l, dict) else l for l in lines))
        self.rc = rc
        self.stdin = io.BytesIO()

    def wait(self, timeout=None):
        return self.rc

    def communicate(self, timeout=None):
        return self.stdout.read(), ''


@pytest.fixture
def rig(tmp_path, monkeypatch):
    q2 = Q2()
    win = tmp_path / 'win'
    win.mkdir()
    monkeypatch.setattr(br, 'WIN_DIR', str(win))
    monkeypatch.setattr(br, 'TOOLS', tmp_path / 'tools')
    (tmp_path / 'tools').mkdir()
    (tmp_path / 'tools' / 'bt_rig.cs').write_text('// source')
    (win / 'BtRig.cs').write_text('// source')
    (win / 'btrig.exe').write_text('')
    runs, popens = [], []
    btrig_out = {}

    def run(cmd, **kw):
        runs.append(cmd)
        if cmd[0] == br.ADB_EXE:
            return q2.answer(cmd[3:])
        if cmd[0].endswith('btrig.exe'):
            lines = btrig_out.get(cmd[1], [])
            rc = 0
            if lines and isinstance(lines[-1], int):
                lines, rc = lines[:-1], lines[-1]
            return subprocess.CompletedProcess(cmd, rc, ''.join(json.dumps(l) + '\n' for l in lines) + 'noise\n', 'err')
        raise AssertionError(cmd)

    plans = {}

    def popen(cmd, **kw):
        popens.append(cmd)
        return plans[cmd[1]]() if callable(plans[cmd[1]]) else plans[cmd[1]]
    monkeypatch.setattr(br.subprocess, 'run', run)
    monkeypatch.setattr(br.subprocess, 'Popen', popen)
    monkeypatch.setattr(qr, 'framebuffer_png', lambda raw: b'PNG' + raw)
    monkeypatch.setattr(br, 'win_path', lambda p: 'C:' + str(p))
    clock = iter(range(0, 10 ** 6, 1))
    monkeypatch.setattr(br.time, 'time', lambda: 1795473857.0 + next(clock))

    return types.SimpleNamespace(q2=q2, win=win, runs=runs, popens=popens, btrig=btrig_out, plans=plans, tmp=tmp_path)


def test_adb_and_paths(rig, monkeypatch):
    assert br.q2sh('cat /tmp/coda-rc.now').startswith('state=2')
    rig.q2.fail = 'coda-rc.now'
    with pytest.raises(RuntimeError, match='device offline'):
        br.q2sh('cat /tmp/coda-rc.now')
    assert REAL_WIN_PATH('/mnt/c/Users/x/a b.wav') == 'C:\\Users\\x\\a b.wav'
    with pytest.raises(ValueError, match='not on a Windows drive'):
        REAL_WIN_PATH('/home/gray/x.wav')


def test_build_and_btrig(rig, monkeypatch, tmp_path):
    sdk = tmp_path / 'sdk'
    for v in ('9.0.316', '10.0.403', 'preview-x'):
        (sdk / v / 'Roslyn' / 'bincore').mkdir(parents=True)
        (sdk / v / 'Roslyn' / 'bincore' / 'csc.dll').write_text('')
    real_path = br.pathlib.Path
    monkeypatch.setattr(br.pathlib, 'Path', lambda p='.', *a: real_path(str(sdk)) if p == '/mnt/c/Program Files/dotnet/sdk' else real_path(p, *a))
    monkeypatch.setattr(br, 'win_path', lambda p: str(p))
    assert br.roslyn().endswith('10.0.403/Roslyn/bincore/csc.dll')
    calls = []

    def run(cmd, **kw):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0 if len(calls) == 1 else 1, 'out', 'csc error')
    monkeypatch.setattr(br.subprocess, 'run', run)
    assert br.build() == real_path(rig.win) / 'btrig.exe'
    assert calls[0][0] == br.DOTNET and '-langversion:latest' in calls[0] and calls[0][-1] == 'BtRig.cs'
    assert any(c.endswith('Windows.winmd') for c in calls[0])
    with pytest.raises(RuntimeError, match='csc failed'):
        br.build()
    monkeypatch.setattr(br.pathlib, 'Path', lambda p='.', *a: real_path(str(tmp_path / 'none')) if p == '/mnt/c/Program Files/dotnet/sdk' else real_path(p, *a))
    with pytest.raises(RuntimeError, match='no .NET SDK'):
        br.roslyn()


def test_btrig_exe_rebuilds_when_the_source_changed(rig, monkeypatch):
    built = []
    monkeypatch.setattr(br, 'build', lambda: built.append(1))
    br.btrig_exe()
    assert built == []
    (rig.tmp / 'tools' / 'bt_rig.cs').write_text('// changed')
    br.btrig_exe()
    assert built == [1]


def test_btrig_lines_and_failure(rig):
    rig.btrig['info'] = [{'adapter': LAPTOP}]
    assert br.btrig('info') == [{'adapter': LAPTOP}]
    rig.btrig['pair'] = [{'event': 'error', 'message': 'no'}, 1]
    with pytest.raises(RuntimeError, match="'message': 'no'"):
        br.btrig('pair')
    rig.btrig['x'] = [1]
    with pytest.raises(RuntimeError, match='err'):
        br.btrig('x')


def test_q2_input_writes_events_for_keys_taps_and_the_wheel(rig):
    br.q2_input('key', 'centre')
    br.q2_input('tap', 187, 280)
    br.q2_input('wheel', 1)
    writes = [s for s in rig.q2.scripts if 'printf' in s]
    assert '/dev/input/event1' in writes[0] and '/dev/input/event2' in writes[1] and '/dev/input/event3' in writes[2]
    assert writes[0] == qr.event_writer('/dev/input/event1', qr.key_events(116), 80000)


def test_q2_screen_backlight_settings_bluez_codec_clock_and_log(rig, tmp_path):
    br.q2_shot(tmp_path / 's.png')
    assert (tmp_path / 's.png').read_bytes() == b'PNGQ2FB\0frame'
    assert br.q2_backlight() == 1
    s = br.q2_settings()
    assert s['build'] == ['V1UQi'] and s['config'] == {'VOLUME': '52', 'BTLINKMAC': '20:AF:1B:18:FD:03', 'XFADE': '1'}
    assert s['now'] == {'state': '2', 'pos': '0'}
    assert s['bluetooth'] == {LAPTOP: {'name': 'GRAY_THINKPAD', 'paired': False, 'trusted': False, 'connected': True},
                              '20:AF:1B:18:FD:03': {'name': 'Arctis', 'paired': True, 'connected': False}}
    assert br.q2_codec() == [{'pcm': '/org/bluealsa/hci0/dev_4C_A9_54_59_F0_0F/a2dpsrc/sink', 'codec': 'AAC',
                              'sampling': '48000'}]
    assert abs(br.q2_clock_offset()) <= 2  # the fake clock ticks a second at each read
    assert br.parse_utc('2026-11-23T22:42:00Z') == 1795473720.0
    lines = br.q2_log(br.parse_utc('2026-11-23T22:42:05Z'), br.parse_utc('2026-11-23T22:42:30Z'))
    assert lines == ['2026-11-23T22:42:10Z 2 I player q2player: smart crossfade: plain, a stream']


def test_backlight_unreadable(rig, monkeypatch):
    monkeypatch.setattr(br, 'q2sh', lambda s, timeout=30: '\n')
    assert br.q2_backlight() is None


class Pipe:
    def __init__(self, data):
        import os
        self.r, self.w = os.pipe()
        os.write(self.w, data)
        self.stdout = open(self.r, 'rb', buffering=0)
        self.stdin = io.BytesIO()
        self.waited = False

    def wait(self, timeout=None):
        self.waited = True
        return 0


def test_pipe_sock(rig, monkeypatch):
    p = Pipe(b'hello')
    rig.plans['pipe'] = p
    s = br.coda_sock()
    assert rig.popens[-1][1:] == ['pipe', '127.0.0.1', str(br.CODA_PORT)]
    s.settimeout(1)
    s.sendall(b'abc')
    assert p.stdin.getvalue() == b'abc'
    assert s.recv(10) == b'hello'
    s.settimeout(0)
    with pytest.raises(TimeoutError):
        s.recv(10)
    stdin = p.stdin
    s.close()
    assert stdin.closed and p.waited


class Session:
    """A fake coda_rc_client.Session: answers and records calls."""
    made = []
    paired = True
    refuse_play = False

    def __init__(self, sock, name):
        self.sock, self.name = sock, name
        self.calls = []
        self.state = {'now_playing': {'track_id': 'song:1', 'title': 'A', 'artist': 'X', 'duration_ms': 100000,
                                      'play_state': 0}, 'queue': {'index': 0}}
        Session.made.append(self)

    def hello(self, peer_id=None):
        self.calls.append(('hello', peer_id))
        return {'device_id': 'dev', 'paired': Session.paired if peer_id else False}

    def auth(self, ltk):
        self.calls.append(('auth', ltk))

    def pair_start(self):
        return 'point'

    def pair_finish(self, code, point):
        self.calls.append(('pair', code))
        return b'p' * 8, b'k' * 32

    def __getattr__(self, name):
        def call(*a, **k):
            self.calls.append((name, a, k))
            if name == 'transport' and a[0] == 'play' and Session.refuse_play:
                raise cr.RemoteError(11, 'the Q2 did not take it')
            if name == 'get_state':
                return self.state
            return {'answer': name}
        return call


class Sock:
    def close(self):
        pass


@pytest.fixture
def coda(rig, monkeypatch, tmp_path):
    Session.made.clear()
    Session.paired = True
    Session.refuse_play = False
    monkeypatch.setattr(cr, 'Session', Session)
    monkeypatch.setattr(br, 'coda_sock', lambda: Sock())
    monkeypatch.setattr(br, 'KEYS_DIR', tmp_path / 'keys')
    return Session


def test_coda_pairs_once_then_authenticates(coda, rig):
    s = br.coda()
    assert ('pair', '123456') in s.calls and s.info == {'device_id': 'dev', 'paired': False}
    s = br.coda()
    assert s.calls == [('hello', b'p' * 8), ('auth', b'k' * 32)]
    coda.paired = False  # forgotten on the Q2: pairs again
    s = br.coda()
    assert ('pair', '123456') in s.calls


def test_coda_without_a_code(coda, rig, monkeypatch):
    monkeypatch.setattr(br, 'q2sh', lambda s, timeout=30: '')
    with pytest.raises(RuntimeError, match='no pairing code'):
        br.coda()


def test_play_actions(coda, rig, capsys):
    for action, args in [('pair', []), ('state', []), ('queue', ['5']), ('queue', []), ('browse', []), ('browse', ['n']),
                         ('search', ['a', 'b']), ('start', ['node']), ('start', ['node', 'song:2']), ('resume', []),
                         ('pause', []), ('next', []), ('previous', []), ('seek', ['12.5']), ('seek-end', ['40']),
                         ('seek-end', ['500']), ('volume', ['30']), ('shuffle', ['on']), ('repeat', ['all']),
                         ('jump', ['1', 'song:2'])]:
        assert br.play(action, args) == 0
    calls = [c for s in coda.made for c in s.calls if c[0] not in ('hello', 'auth', 'pair', 'get_state')]
    assert ('transport', ('play',), {}) in calls and ('transport', ('seek', 60.0), {}) in calls
    assert ('transport', ('seek', 0.0), {}) in calls and ('set_mode', (), {'shuffle': 1}) in calls
    assert ('queue', (0, 5), {}) in calls and ('search', ('a b',), {}) in calls and ('play', ('node', None, 'song:2'), {}) in calls
    assert '"to_s": 60.0' in capsys.readouterr().out


def test_ends_lets_songs_end_and_keeps_the_player_log(coda, rig, monkeypatch):
    states = iter(['song:1', 'song:1', 'song:2', 'song:2', 'song:3', 'song:3'] + ['song:3'] * 200)

    def get_state(self):
        tid = next(states)
        return {'now_playing': {'track_id': tid, 'title': tid, 'artist': 'X', 'duration_ms': 100000, 'play_state': 1},
                'queue': {'index': 0}}
    monkeypatch.setattr(Session, 'get_state', get_state, raising=False)
    monkeypatch.setattr(br, 'q2_log', lambda a, b, off=0: ['t 1 I player q2player: smart crossfade: body', 't 2 I bt other'])
    out = br.ends(3, 50)
    assert [o['to'] for o in out] == ['X - song:2', 'X - song:3', None]
    states = iter(['song:1', 'song:1', 'song:2', 'song:2', 'song:3'])
    assert [o['to'] for o in br.ends(2, 50)] == ['X - song:2', 'X - song:3']
    assert out[0]['log'] == ['t 1 I player q2player: smart crossfade: body']


def test_ends_starts_a_stopped_queue(coda, rig, monkeypatch):
    coda.refuse_play = True
    monkeypatch.setattr(br, 'q2_log', lambda a, b, off=0: [])
    out = br.ends(1, 10, timeout_s=3)
    calls = coda.made[-1].calls
    assert ('jump', (0, 'song:1'), {}) in calls and out[0]['to'] is None
    coda.refuse_play = False
    coda.made.clear()
    br.ends(1, 10, timeout_s=3)
    assert ('transport', ('play',), {}) in coda.made[-1].calls


def guard_proc(restored=True):
    lines = [{'event': 'guarding', 'role': 'eConsole'}]
    if restored:
        lines.append({'event': 'default_restored', 'role': 'eMultimedia', 'was': 'Speakers (Q2 Muse)'})
    return FakeProc(lines + [{'event': 'guard_end'}])


def test_pair_answers_only_the_laptop_and_turns_off_the_speakers_role(rig, capsys):
    rig.btrig['info'] = [{'adapter': LAPTOP}]
    rig.btrig['service'] = [{'event': 'service', 'win32': 0}]
    rig.plans['guard'] = guard_proc
    rig.plans['pair'] = FakeProc(['not json\n', {'event': 'device'}, {'event': 'pairing_requested', 'kind': 'ConfirmPinMatch', 'pin': '1'},
                                  {'event': 'pairing_requested', 'kind': 'ConfirmPinMatch', 'pin': '1'}, {'event': 'paired'}])
    rig.q2.paired = 'true'
    assert br.pair(rig.tmp / 'shots') == 0
    taps = [s for s in rig.q2.scripts if 'event2' in s]
    assert len(taps) == 1  # the second request is not answered again
    assert (rig.tmp / 'shots' / '1-dialog.png').exists() and (rig.tmp / 'shots' / '2-second-row-lit.png').exists()
    assert any(c[1:] == ['service', br.Q2_MAC, br.A2DP_SINK, 'off'] for c in rig.runs if c[0].endswith('btrig.exe'))
    result = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert result['q2_paired'] is True and result['defaults_put_back'][0]['was'] == 'Speakers (Q2 Muse)'
    assert not (rig.win / 'guard.stop').exists()


def test_pair_does_not_answer_with_another_device_linked(rig, capsys):
    rig.btrig['info'] = [{'adapter': 'AA:BB:CC:DD:EE:FF'}]
    rig.plans['guard'] = lambda: guard_proc(False)
    rig.plans['pair'] = FakeProc([{'event': 'pairing_requested', 'kind': 'ConfirmPinMatch'}], rc=1)
    assert br.pair(rig.tmp / 'shots') == 1
    assert not [s for s in rig.q2.scripts if 'printf' in s]
    assert 'not_answered' in capsys.readouterr().out


def test_unpair_names_the_laptop_alone(rig):
    rig.btrig['info'] = [{'adapter': LAPTOP}]
    rig.btrig['unpair'] = [{'event': 'unpaired'}]
    assert br.unpair() == 0
    assert rig.q2.scripts[-1].endswith('objpath:/org/bluez/hci0/dev_4C_A9_54_59_F0_0F')


def receive_proc(rig, rc=0, done=True):
    def make():
        wav = rig.win / 'rec' / 'take.wav'
        wav.write_bytes(b'RIFF')
        lines = [{'event': 'routed'}, {'event': 'tick', 'seconds': 1.0}, {'event': 'tick', 'seconds': 2.0},
                 {'event': 'tick', 'seconds': 3.0}, 'noise\n']
        if done:
            lines.append({'event': 'done', 'start_utc': '2026-11-23T22:42:00Z', 'first_packet_utc': '2026-11-23T22:42:00.1Z',
                          'rate': 48000, 'frames': 48000 * 60, 'gap_frames': 0, 'mode': 'process-loopback'})
        return FakeProc(lines, rc)
    return make


def test_record_writes_the_wav_its_facts_and_the_log(rig, tmp_path):
    rig.plans['receive'] = receive_proc(rig)
    side = br.record(5, tmp_path / 'out' / 'take.wav')
    assert (tmp_path / 'out' / 'take.wav').read_bytes() == b'RIFF' and not (rig.win / 'rec' / 'take.wav').exists()
    assert side['seconds'] == 60 and side['link'][0]['codec'] == 'AAC' and side['capture'] == 'process-loopback'
    assert '--route' in rig.popens[-1] and br.ROUTE in rig.popens[-1]
    assert 'smart crossfade' in (tmp_path / 'out' / 'take.q2.log').read_text()
    assert json.loads((tmp_path / 'out' / 'take.json').read_text())['q2_before']['config']['VOLUME'] == '52'


def test_record_in_place_on_the_windows_drive(rig):
    rig.plans['receive'] = receive_proc(rig)
    br.record(5, rig.win / 'rec' / 'take.wav', ['--endpoint'])
    assert (rig.win / 'rec' / 'take.wav').exists() and rig.popens[-1][-1] == '--endpoint'


def test_record_failure_keeps_what_it_knows(rig, tmp_path):
    rig.plans['receive'] = receive_proc(rig, rc=1, done=False)
    with pytest.raises(RuntimeError, match='btrig exited 1'):
        br.record(5, tmp_path / 'bad.wav')
    assert json.loads((tmp_path / 'bad.json').read_text())['error'] == 'btrig exited 1'


def test_main_dispatch(rig, monkeypatch, capsys, tmp_path):
    seen: list = []
    monkeypatch.setattr(br, 'build', lambda: 'built')
    monkeypatch.setattr(br, 'pair', lambda: seen.append('pair') or 0)
    monkeypatch.setattr(br, 'unpair', lambda: seen.append('unpair') or 0)
    monkeypatch.setattr(br, 'ends', lambda n, b: seen.append(('ends', n, b)))
    monkeypatch.setattr(br, 'play', lambda a, args: seen.append(('play', a, args)) or 0)
    monkeypatch.setattr(br, 'record', lambda s, o, e: seen.append(('record', s, o, e)) or
                        {'wav': o, 'start_utc': 'x', 'seconds': s, 'gap_frames': 0, 'capture': 'c', 'q2_log': 'l'})
    rig.btrig['info'] = [{'adapter': LAPTOP}]
    import bt_rig_analyse
    monkeypatch.setattr(bt_rig_analyse, 'analyse', lambda *a: {'analysed': a})
    monkeypatch.setattr(bt_rig_analyse, 'calibrate', lambda d, c: [('cut', 0.0, 0.0, True)])
    assert br.main(['build']) == 0 and 'built' in capsys.readouterr().out
    assert br.main(['status']) == 0 and '"adapter"' in capsys.readouterr().out
    assert br.main(['pair']) == 0 and br.main(['unpair']) == 0
    assert br.main(['record', '--seconds', '5', '--out', 'x.wav', '--endpoint']) == 0
    assert br.main(['record', '--seconds', '5', '--out', 'x.wav']) == 0
    assert br.main(['ends', '3', '--before', '40']) == 0
    assert br.main(['play', 'seek', '10']) == 0
    assert seen == ['pair', 'unpair', ('record', 5.0, 'x.wav', ['--endpoint']), ('record', 5.0, 'x.wav', []),
                    ('ends', 3, 40.0), ('play', 'seek', ['10'])]
    capsys.readouterr()
    assert br.main(['log', '--from', '2026-11-23T22:42:05Z', '--to', '2026-11-23T22:42:30Z']) == 0
    assert 'plain, a stream' in capsys.readouterr().out
    assert br.main(['analyse', 'a.wav', '--ref', 'r', '--at', '1']) == 0
    assert '"analysed"' in capsys.readouterr().out
    assert br.main(['calibrate', str(tmp_path)]) == 0 and 'pass' in capsys.readouterr().out
    monkeypatch.setattr(bt_rig_analyse, 'calibrate', lambda d, c: [('fade5', 0.0, 0.0, False)])
    assert br.main(['calibrate', str(tmp_path)]) == 1 and 'FAIL' in capsys.readouterr().out
