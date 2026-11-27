#!/usr/bin/env python3
"""q2btd (btd/q2btd.c, docs/btd.md) built for this machine against test/btd_standin.c, a stand-in for BlueZ and
bluez-alsa on a dbus-daemon of the test's own, and a stand-in q2playd on the Q2PLAY/1 socket: the radio's power and
its keeping, the auto-reconnect at the start and after a drop (and none after a disconnect asked for), scanning,
pairing through the agent and the agent's refusals, the codec chosen and kept, the battery, the output q2playd is
told and the pause before the jack, absolute volume both ways, the headset's buttons and its track display, forget,
a daemon that comes back, and Q2BT/1's transcripts (docs/platform/bt.md). Run it as python3 test/btd.py (pytest).
Q2BTD_SOURCE builds another copy, to show a broken one fails here. Needs cc, libdbus-1's headers and dbus-daemon;
without them it skips."""
import os
import pathlib
import shutil
import socket
import subprocess
import sys
import threading
import time

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import conformance  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
SOURCE = pathlib.Path(os.environ.get('Q2BTD_SOURCE') or ROOT/'btd/q2btd.c')
BUDS, SPEAKER = 'AA:BB:CC:DD:EE:01', 'AA:BB:CC:DD:EE:02'  # made up
# headphones (Audio/Video, minor 6) and a loudspeaker (minor 5), with the Rendering and Audio service bits
BUDS_DEV = f'{BUDS},Fixture Buds,240418,1,1,80,SBC+aptX+LDAC'
SPEAKER_FOUND = f'{SPEAKER},Fixture Speaker,240414'
BUS_CONFIG = """<!DOCTYPE busconfig PUBLIC "-//freedesktop//DTD D-Bus Bus Configuration 1.0//EN"
 "http://www.freedesktop.org/standards/dbus/1.0/busconfig.dtd">
<busconfig><type>custom</type><listen>unix:path={path}</listen><auth>EXTERNAL</auth>
<policy context="default"><allow user="*"/><allow own="*"/><allow send_destination="*"/><allow eavesdrop="true"/>
<allow receive_sender="*"/><allow send_type="signal"/><allow receive_type="signal"/></policy></busconfig>"""

pytestmark = pytest.mark.skipif(not shutil.which('dbus-daemon') or subprocess.run(
    ['pkg-config', '--exists', 'dbus-1']).returncode, reason='no dbus-daemon or libdbus-1 headers')


@pytest.fixture(scope='session')
def binary(tmp_path_factory):
    out = tmp_path_factory.mktemp('btd')
    flags = subprocess.run(['pkg-config', '--cflags', '--libs', 'dbus-1'], capture_output=True, text=True, check=True).stdout.split()
    src = out/'q2btd.c'
    src.write_bytes(SOURCE.read_bytes())
    subprocess.run(['cc', '-std=c11', '-Wall', '-Wextra', '-Werror', '-O2', '-o', str(out/'q2btd'), str(src), *flags], check=True)
    subprocess.run(['cc', '-std=c11', '-Wall', '-Wextra', '-Werror', '-O1', '-o', str(out/'standin'),
                    str(ROOT/'test/btd_standin.c'), *flags], check=True)
    return out


class Lines:
    """a process's or a socket's lines, read on a thread; wait() for one matching, in order or anywhere"""
    def __init__(self):
        self.lines, self.cv = [], threading.Condition()

    def add(self, line):
        with self.cv:
            self.lines.append(line)
            self.cv.notify_all()

    def wait(self, pred, timeout=6.0, start=0):
        end = time.monotonic() + timeout
        with self.cv:
            while True:
                for i, line in enumerate(self.lines[start:], start):
                    if pred(line):
                        return i, line
                left = end - time.monotonic()
                if left <= 0:
                    raise AssertionError(f'no such line in {timeout} s; had: {self.lines[-25:]}')
                self.cv.wait(left)

    def has(self, text):
        with self.cv:
            return [line for line in self.lines if text in line]


class Play:
    """a stand-in q2playd: Q2PLAY/1's hello and an `ok` to every line, each line kept; push() writes a `now` line"""
    def __init__(self, sock):
        self.got, self.conns = Lines(), []
        self.s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.s.bind(str(sock))
        self.s.listen(4)
        threading.Thread(target=self.accept, daemon=True).start()

    def accept(self):
        while True:
            try:
                c, _ = self.s.accept()
            except OSError:
                return
            self.conns.append(c)
            threading.Thread(target=self.serve, args=(c,), daemon=True).start()

    def serve(self, c):
        try:
            for line in c.makefile('r'):
                line = line.rstrip('\n')
                self.got.add(line)
                c.sendall(b'Q2PLAY/1 ok\n' if line == 'Q2PLAY/1' else b'ok\n')
        except OSError:  # q2btd ended: its connection reset
            return

    def push(self, volume=40, state=1, title='Alpha'):
        for c in self.conns:
            c.sendall(f'now title={title} artist=Ann album=First state={state} pos=3 len=200 volume={volume} id=1\n'.encode())


class Q2:
    def __init__(self, tmp, binary, devs=(BUDS_DEV,), found=(SPEAKER_FOUND,), state='last=' + BUDS + '\n'):
        self.tmp, self.binary = tmp, binary
        for d in ('mnt/data', 'run/vamp'):
            (tmp/d).mkdir(parents=True, exist_ok=True)
        if state is not None:
            (tmp/'mnt/data/q2btd.state').write_text(state)
        (tmp/'bus.conf').write_text(BUS_CONFIG.format(path=tmp/'bus'))
        self.bus = subprocess.Popen(['dbus-daemon', '--nofork', '--config-file', str(tmp/'bus.conf')], stderr=subprocess.DEVNULL)
        end = time.monotonic() + 5
        while not (tmp/'bus').exists() and time.monotonic() < end:
            time.sleep(0.02)
        self.env = dict(os.environ, DBUS_SYSTEM_BUS_ADDRESS=f'unix:path={tmp/"bus"}')
        self.args = [a for d in devs for a in ('--dev', d)] + [a for f in found for a in ('--found', f)]
        self.play = Play(tmp/'run/vamp/play.sock')
        self.standin()
        self.log = Lines()
        self.btd = None
        self.start()

    def standin(self):
        self.si = subprocess.Popen([str(self.binary/'standin'), *self.args], env=self.env, stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE, text=True)
        self.out = Lines()
        threading.Thread(target=self.pump, args=(self.si.stdout, self.out), daemon=True).start()
        self.out.wait(lambda l: l == 'ready')

    def start(self):
        self.btd = subprocess.Popen([str(self.binary/'q2btd'), '--root', str(self.tmp)], env=self.env, stderr=subprocess.PIPE, text=True)
        threading.Thread(target=self.pump, args=(self.btd.stderr, self.log), daemon=True).start()
        end = time.monotonic() + 5
        while not (self.tmp/'run/vamp/bt.sock').exists() and time.monotonic() < end:
            time.sleep(0.02)

    def stop_btd(self):
        self.btd.terminate()
        self.btd.wait(5)
        (self.tmp/'run/vamp/bt.sock').unlink(missing_ok=True)

    @staticmethod
    def pump(f, lines):
        for line in f:
            lines.add(line.rstrip('\n'))

    def tell(self, line):
        self.si.stdin.write(line + '\n')
        self.si.stdin.flush()

    def api(self):
        return Api(self.tmp)

    def close(self):
        for p in (self.btd, self.si, self.bus):
            if p and p.poll() is None:
                p.terminate()
                try:
                    p.wait(5)
                except subprocess.TimeoutExpired:
                    p.kill()
        self.play.s.close()


class Api:
    def __init__(self, root):
        self.s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.s.connect(str(root/'run/vamp/bt.sock'))
        self.s.settimeout(30)
        self.f = self.s.makefile('rw')
        self.events = []
        assert self.ask('Q2BT/1') == 'Q2BT/1 ok'

    def line(self):
        return self.f.readline().rstrip('\n')

    def ask(self, line):
        """the answer to line, watch events before it kept in events"""
        self.f.write(line + '\n')
        self.f.flush()
        while True:
            got = self.line()
            if got.split(' ', 1)[0] in ('status', 'found', 'pair-request') and not line.startswith(('status', 'Q2BT')):
                self.events.append(got)
                continue
            return got

    def wait_event(self, pred, timeout=8.0):
        """a watch event matching pred, from those kept or the next lines read"""
        self.s.settimeout(timeout)
        try:
            while not any(pred(e) for e in self.events):
                self.events.append(self.line())
        finally:
            self.s.settimeout(30)
        return True

    def lines_until_ok(self, line):
        self.f.write(line + '\n')
        self.f.flush()
        out = []
        while (got := self.line()) != 'ok':
            out.append(got)
        return out

    def status(self):
        return dict(w.split('=', 1) for w in self.ask('status').split()[1:])


@pytest.fixture
def q2(tmp_path, binary):
    made = []

    def make(**kw):
        made.append(Q2(tmp_path/f'q{len(made)}', binary, **kw))
        return made[-1]
    yield make
    for q in made:
        q.close()


def until(pred, timeout=6.0, what=''):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        v = pred()
        if v:
            return v
        time.sleep(0.05)
    raise AssertionError(f'not within {timeout} s: {what}')


def connected(q):
    return until(lambda: q.api().status()['state'] == 'connected', what='connected')


def test_power_on_at_start_and_off_kept(q2):
    q = q2(state=None)
    q.out.wait(lambda l: l == 'set /org/bluez/hci0 Powered 1')
    a = q.api()
    until(lambda: a.status()['power'] == '1', what='powered')
    assert a.ask('power off') == 'ok'
    assert a.status()['state'] == 'off'
    assert 'power=0' in (q.tmp/'mnt/data/q2btd.state').read_text()
    q.stop_btd()
    n = len(q.out.lines)
    q.start()
    time.sleep(1.5)
    assert not [l for l in q.out.lines[n:] if 'Powered 1' in l], 'a radio turned off was turned on again at the start'


def test_auto_reconnect_at_start(q2):
    q = q2()
    q.out.wait(lambda l: l == f'Connect {BUDS}')
    connected(q)
    q.play.got.wait(lambda l: l == 'output bluetooth')


def test_last_device_from_stock_config_when_no_state(q2):
    q = q2(state=None)
    q.stop_btd()
    (q.tmp/'mnt/data/config.ini').write_text(f'[PLAYSET]\nVOLUME=30\n[SYSSET]\nBTLINKMAC={BUDS}\n')
    (q.tmp/'mnt/data/q2btd.state').unlink(missing_ok=True)
    q.start()
    q.out.wait(lambda l: l == f'Connect {BUDS}')


def test_reconnect_after_drop_and_pause_before_the_jack(q2):
    q = q2()
    connected(q)
    q.play.got.wait(lambda l: l == 'output bluetooth')
    q.play.push(state=1)
    time.sleep(0.3)
    n, m = len(q.play.got.lines), len(q.out.lines)
    q.tell(f'drop {BUDS}')
    i, _ = q.play.got.wait(lambda l: l == 'pause', start=n)
    j, _ = q.play.got.wait(lambda l: l == 'output dac', start=n)
    assert i < j, 'the song went to the jack before it was paused'
    q.out.wait(lambda l: l == f'Connect {BUDS}', start=m, timeout=6)
    q.play.got.wait(lambda l: l == 'output bluetooth', start=j)


def test_no_reconnect_after_a_disconnect_asked(q2):
    q = q2()
    connected(q)
    a = q.api()
    assert a.ask('disconnect') == 'ok'
    m = len(q.out.lines)
    time.sleep(4)
    assert not [l for l in q.out.lines[m:] if l.startswith('Connect')], 'reconnected after a disconnect the user asked for'


def test_reconnect_keeps_paging_an_absent_headset(q2):
    q = q2(devs=(BUDS_DEV.replace(',1,1,80', ',1,0,80'),))
    q.out.wait(lambda l: l == f'Connect {BUDS}')
    time.sleep(0.5)
    q.tell(f'reach {BUDS} 1')
    q.out.wait(lambda l: l.startswith(f'connected {BUDS}'), timeout=10)
    assert len(q.out.has(f'Connect {BUDS}')) >= 2


def test_scan_pair_and_a_speaker_gets_the_players_gain(q2):
    q = q2(devs=(), state='')
    a = q.api()
    until(lambda: a.status()['power'] == '1', what='powered')
    assert a.ask('watch') == 'ok'
    assert a.ask('scan') == 'ok'
    a.wait_event(lambda e: e == f'found addr={SPEAKER} name=Fixture%20Speaker kind=speaker')
    assert a.ask(f'pair {SPEAKER}') == 'ok'
    q.out.wait(lambda l: l == f'agent RequestConfirmation {SPEAKER}: yes')
    q.out.wait(lambda l: l == f'set {SPEAKER} Trusted 1')
    q.out.wait(lambda l: l.startswith(f'connected {SPEAKER}'))
    q.play.got.wait(lambda l: l == 'output bluetooth soft')
    q.play.push(volume=50)
    time.sleep(1)
    assert not q.out.has(f'set {SPEAKER} Volume'), "a speaker's own volume was written"
    assert f'last={SPEAKER}' in (q.tmp/'mnt/data/q2btd.state').read_text()


def test_agent_refuses_a_pairing_nobody_asked_for(q2):
    q = q2(devs=(), found=(SPEAKER_FOUND,), state='')
    a = q.api()
    until(lambda: a.status()['power'] == '1', what='powered')
    q.out.wait(lambda l: l.startswith('agent registered /vamp/q2btd/agent'))
    q.tell(f'pairin {SPEAKER}')
    q.out.wait(lambda l: l == f'agent RequestConfirmation {SPEAKER}: org.bluez.Error.Rejected')
    assert a.ask('scan') == 'ok'
    q.tell(f'pairin {SPEAKER}')
    q.out.wait(lambda l: l == f'agent RequestConfirmation {SPEAKER}: yes')


def test_codec_chosen_is_asked_for_and_kept(q2):
    q = q2()
    connected(q)
    a = q.api()
    until(lambda: 'available=SBC,aptX,LDAC' in a.ask('codec'), what='codecs')
    assert a.ask('codec AAC') == 'err codec'
    assert a.ask('codec LDAC') == 'ok'
    q.out.wait(lambda l: l == f'SelectCodec {BUDS} LDAC')
    until(lambda: a.status()['codec'] == 'LDAC', what='LDAC')
    assert 'codec=LDAC' in (q.tmp/'mnt/data/q2btd.state').read_text()
    assert a.ask('disconnect') == 'ok'  # the next connection starts on SBC again, and is moved to LDAC
    n = len(q.out.lines)
    assert a.ask(f'connect {BUDS}') == 'ok'
    q.out.wait(lambda l: l == f'SelectCodec {BUDS} LDAC', start=n)


def test_battery(q2):
    q = q2()
    connected(q)
    a = q.api()
    until(lambda: a.status()['battery'] == '80', what='battery 80')
    assert a.ask('watch') == 'ok'
    q.tell(f'battery {BUDS} 55')
    a.wait_event(lambda e: e.startswith('status ') and ' battery=55 ' in e)


def test_absolute_volume_both_ways(q2):
    q = q2()
    connected(q)
    q.play.got.wait(lambda l: l == 'output bluetooth')
    q.play.push(volume=50)
    q.out.wait(lambda l: l == f'set {BUDS} Volume 64')  # 127 * 50 / 100, rounded
    n = len(q.play.got.lines)
    q.tell(f'hvol {BUDS} 100')
    q.play.got.wait(lambda l: l == 'volume 79', start=n)  # 100 * 100 / 127, rounded
    q.play.push(volume=79)
    time.sleep(0.8)
    assert len(q.out.has(f'set {BUDS} Volume')) == 1, 'the headset\'s own level was written back to it'


def test_headset_buttons_and_track_display(q2):
    q = q2()
    connected(q)
    q.out.wait(lambda l: l == 'player registered /vamp/q2btd/player')
    for button, verb in (('Play', 'resume'), ('Pause', 'pause'), ('Next', 'next'), ('Previous', 'previous')):
        n = len(q.play.got.lines)
        q.tell(f'button {button}')
        q.out.wait(lambda l, b=button: l == f'button {b}: ok')
        q.play.got.wait(lambda l, v=verb: l == v, start=n)
    q.play.push(title='Gamma', state=1)
    q.out.wait(lambda l: l == 'player-changed status=Playing title=Gamma')


def test_forget(q2):
    q = q2()
    connected(q)
    a = q.api()
    assert a.ask(f'forget {BUDS}') == 'ok'
    q.out.wait(lambda l: l == f'RemoveDevice {BUDS}')
    assert f'last=\n' in (q.tmp/'mnt/data/q2btd.state').read_text()
    assert not [l for l in a.lines_until_ok('devices') if BUDS in l]
    q.play.got.wait(lambda l: l == 'output dac')


def test_daemons_that_come_back(q2):
    q = q2()
    connected(q)
    q.si.terminate()
    q.si.wait(5)
    q.play.got.wait(lambda l: l == 'output dac')
    q.standin()
    q.out.wait(lambda l: l.startswith('agent registered'))
    q.out.wait(lambda l: l == 'player registered /vamp/q2btd/player')
    q.out.wait(lambda l: l == f'Connect {BUDS}', timeout=8)


def test_conformance_every_command_is_on_the_page():
    conformance.check_coverage('bt.md')


@pytest.mark.parametrize('n', range(len(conformance.blocks('bt.md'))))
def test_conformance_bt(q2, n):
    q = q2()
    connected(q)
    a = q.api()
    until(lambda: 'available=SBC' in a.ask('codec') and a.status()['battery'] == '80', what='settled')
    conformance.replay(q.tmp/'run/vamp/bt.sock', conformance.blocks('bt.md')[n])


if __name__ == '__main__':
    sys.exit(pytest.main([__file__, '-q', '-p', 'no:cacheprovider', *sys.argv[1:]]))
