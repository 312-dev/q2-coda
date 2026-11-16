#!/usr/bin/env python3
"""q2-avrcp (patch/q2_avrcp.c) on this machine against a stand-in bluetoothd (test/avrcp/fake_bluez.c) on a private
D-Bus bus: it registers its player with org.bluez.Media1 once BlueZ is on the bus, as BlueZ's media.c reads one
(MPRIS properties, artist a list, lengths in microseconds, CanControl false), sends PropertiesChanged when demo's
Now Playing file changes, registers again when bluetoothd comes back, and tries again after a refusal.
Needs cc, dbus-daemon and the host's libdbus-1.so.3; no build, no stock ZIP."""
import os
import pathlib
import queue
import subprocess as S
import sys
import tempfile
import threading

ROOT = pathlib.Path(__file__).resolve().parents[1]
CC = ['cc', '-Wall', '-Wextra', '-Werror']


def lines(proc, q):
    for line in proc.stdout:
        q.put(line.rstrip('\n'))


class Bus:
    """A private bus, the stand-in BlueZ on it, and q2-avrcp reading now."""
    def __init__(self, d):
        self.d, self.procs = d, []
        conf = d / 'bus.conf'
        conf.write_text(f'''<!DOCTYPE busconfig PUBLIC "-//freedesktop//DTD D-Bus Bus Configuration 1.0//EN"
 "http://www.freedesktop.org/standards/dbus/1.0/busconfig.dtd">
<busconfig><type>system</type><listen>unix:path={d}/bus</listen><auth>EXTERNAL</auth>
<policy context="default"><allow send_destination="*" eavesdrop="true"/><allow eavesdrop="true"/><allow own="*"/>
<allow user="*"/></policy></busconfig>''')
        self.daemon = S.Popen(['dbus-daemon', f'--config-file={conf}', '--nofork', '--nopidfile'])
        self.env = {**os.environ, 'DBUS_SYSTEM_BUS_ADDRESS': f'unix:path={d}/bus'}
        for _ in range(100):
            if (d / 'bus').exists():
                break
            S.run(['sleep', '0.05'])
        self.now = d / 'now'

    def bluez(self, *args):
        p = S.Popen([str(self.d / 'fake'), *args], stdout=S.PIPE, text=True, env=self.env)
        q: queue.Queue = queue.Queue()
        threading.Thread(target=lines, args=(p, q), daemon=True).start()
        assert q.get(timeout=5) == 'READY'
        self.procs.append(p)
        return p, q

    def avrcp(self):
        p = S.Popen([str(self.d / 'avrcp')], env=self.env)
        self.procs.append(p)
        return p

    def write(self, **kv):
        tmp = self.now.with_suffix('.tmp')
        tmp.write_text(''.join(f'{k}={v}\n' for k, v in kv.items()))
        tmp.rename(self.now)

    def close(self):
        for p in self.procs + [self.daemon]:
            p.kill()
            p.wait()


def check(what, ok):
    print(('ok   ' if ok else 'FAIL ') + what)
    if not ok:
        sys.exit(1)


def main():
    with tempfile.TemporaryDirectory(prefix='q2-avrcp-') as tmp:
        d = pathlib.Path(tmp)
        S.run(CC + ['-o', str(d / 'fake'), str(ROOT / 'test/avrcp/fake_bluez.c'), '-l:libdbus-1.so.3'], check=True)
        S.run(CC + ['-DCODA_HOST', f'-DQA_NOW="{d}/now"', '-o', str(d / 'avrcp'), str(ROOT / 'patch/q2_avrcp.c'),
                    str(ROOT / 'test/avrcp/host_main.c'), '-l:libdbus-1.so.3'], check=True)
        bus = Bus(d)
        try:
            bus.write(seq=1, state=1, pos=61000, len=245000, id=7, qpos=2, qlen=12, title='Lovely Day', artist='Bill Withers',
                      album='Menagerie')
            avrcp = bus.avrcp()
            S.run(['sleep', '1'])
            fake, q = bus.bluez()
            reg = q.get(timeout=10)
            check('registers once BlueZ is on the bus, at its player path', reg.startswith("REGISTER '/org/q2pod/player', {"))
            for want in ("'PlaybackStatus', 'Playing'", "'Position', x61000000", "'xesam:title', 'Lovely Day'",
                         "'xesam:artist', ['Bill Withers']", "'xesam:album', 'Menagerie'", "'mpris:length', x245000000",
                         "'xesam:trackNumber', i3", "'CanControl', false", "'Identity', 'Q2'"):
                check(f'registration holds {want}', want in reg)
            bus.write(seq=2, state=2, pos=70000, len=245000, id=7, qpos=2, qlen=12, title='Lovely Day', artist='Bill Withers',
                      album='Menagerie')
            got = q.get(timeout=5)
            check('a pause sends PropertiesChanged on the MPRIS player interface',
                  got.startswith("CHANGED 'org.mpris.MediaPlayer2.Player', {'PlaybackStatus', 'Paused', 'Position', x70000000"))
            check('... with nothing invalidated', got.endswith('}, []'))
            bus.write(seq=3, state=1, pos=0, len=0, id=8, qpos=3, qlen=12, title='Next One', artist='', album='')
            got = q.get(timeout=5)
            check('a new song sends its metadata, no length when none is known',
                  "'xesam:title', 'Next One'" in got and "'xesam:artist', ['']" in got and 'mpris:length' not in got)
            bus.write(seq=4, state=0, pos=0, len=0, id=0, qpos=0, qlen=0, title='', artist='', album='')
            got = q.get(timeout=5)
            check('stopped with nothing: Stopped and empty metadata', "'PlaybackStatus', 'Stopped'" in got and "'Metadata', {}" in got)
            fake.kill()
            fake.wait()
            fake, q = bus.bluez('refuse')
            check('bluetoothd back: registers again (refused here)', q.get(timeout=10).startswith('REFUSED '))
            check('... and again after the refusal', q.get(timeout=10).startswith('REGISTER '))
            check('q2-avrcp still running', avrcp.poll() is None)
        finally:
            bus.close()


if __name__ == '__main__':
    main()
