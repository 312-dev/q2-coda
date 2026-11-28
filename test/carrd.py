#!/usr/bin/env python3
"""Car Remote on VampOS (docs/carrd.md): q2carrd (carrd/q2carrd.c) running the coda-rc daemon (patch/coda_rc.c, built
for this machine), with q2playd (playd/q2playd.c) answering its commands and a stand-in for q2player on the player's
socket, beside stand-ins for Q2UI/1, Q2NET/1 and Q2PKG/1. A phone (tools/coda_rc_client.py) pairs with the code it
reads off the Q2UI/1 card, browses q2libd's library, plays, and sees Now Playing and the queue follow q2playd. Checked
besides: the switch starting and stopping coda-rc, Cancel ending a pairing, Q2UI/1's own timeout asked again, the
toasts after, a cover's app started, the radio held only while connected. Run it as python3 test/carrd.py (pytest).
Q2CARRD_SOURCE builds another copy of q2carrd.c, to show a broken one fails here."""
import os
import pathlib
import re
import socket
import sqlite3
import subprocess
import sys
import time
import urllib.parse

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'test')), sys.path.insert(0, str(ROOT/'tools'))
import coda_rc_client as client  # noqa: E402
import conformance  # noqa: E402
import playd  # noqa: E402
from sendd import Stand  # noqa: E402

SOURCE = pathlib.Path(os.environ.get('Q2CARRD_SOURCE') or ROOT/'carrd/q2carrd.c')
PATCH = ROOT/'patch'
CODA_RC = ['coda_rc.c', 'coda_rc_core.c', 'coda_rc_lib.c', 'coda_rc_mdns.c', 'coda_rc_ble.c', 'monocypher/monocypher.c',
           'monocypher/monocypher-ed25519.c']
SONGS = [(1, 'Music/a.flac', 'Alpha', 'Ann', 'First', 200), (2, 'Music/b.flac', 'Bravo', 'Ann', 'First', 150),
         (3, 'Music/c.flac', 'Charlie', 'Bob', 'Second', 90)]


@pytest.fixture(scope='session')
def build(tmp_path_factory):
    out = tmp_path_factory.mktemp('carrd')
    (out/'q2carrd.c').write_bytes(SOURCE.read_bytes())
    subprocess.run(['cc', '-std=c11', '-Wall', '-Wextra', '-Werror', '-O2', '-o', str(out/'q2carrd'), str(out/'q2carrd.c')], check=True)
    sqlite = next(pathlib.Path('/usr/lib').glob('*-linux-gnu/libsqlite3.so.0'))
    subprocess.run(['cc', '-O1', '-DCODA_HOST', '-Wall', '-Wno-unused-parameter', '-Wno-format-truncation', '-I', str(PATCH), *(str(PATCH/s) for s in CODA_RC),
                    str(sqlite), '-o', str(out/'coda-rc')], check=True)
    playd.build(out)
    (out/'shim.c').write_text(playd.SHIM)
    subprocess.run(['cc', '-shared', '-fPIC', '-o', str(out/'shim.so'), str(out/'shim.c'), '-ldl'], check=True)
    return out


def library(root):
    """q2libd's library.db (docs/libd.md): stock's songtable with every column, each song's length as time 0 and time_end"""
    (root/'mnt/data/vamp').mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(root/'mnt/data/vamp/library.db')
    db.execute('create table songtable(id integer primary key, filename, fileurl, album, songer, style, name, composer, '
               'albumsonger, year integer, track integer, cd_num integer, time integer, time_end integer, '
               'time_create integer, Initial, bitdepth integer, samprate integer)')
    db.execute('create table playlist(id integer, name, filename, fileurl, cd_num integer)')
    for i, f, title, artist, album, secs in SONGS:
        db.execute('insert into songtable(id, filename, fileurl, album, songer, style, name, albumsonger, year, track, cd_num, '
                   'time, time_end, time_create) values (?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                   (i, f.split('/')[-1], '/mnt/mmc/' + f, album, artist, 'Rock', title, artist, 2001, i, 1, 0, secs, i))
    db.commit()
    db.close()


# q2-iroh's stand-in: its pid in --pid's file and its arguments, one start a line, in that file's .args beside it
IROH = '''#!/bin/sh
pid=
prev=
for a in "$@"; do [ "$prev" = --pid ] && pid=$a; prev=$a; done
echo "$@" >> "$pid.args"
echo $$ > "$pid"
trap 'exit 0' TERM
while :; do sleep 0.05; done
'''


class Q2:
    def __init__(self, build, tmp, on=True, answer=None, net='connected', player=True, ui_timeout=59000, iroh=()):
        self.out, self.root = build, tmp
        self.socks = tmp/'run/vamp'
        for d in ('tmp', 'run/vamp', 'mnt/data', 'mnt/mmc', 'dev'):
            (tmp/d).mkdir(parents=True, exist_ok=True)
        (tmp/'dev/shanling_dac').write_text('')
        self.switch(on)
        library(tmp)
        conformance.accounts(tmp)
        self.answer, self.asks = answer, []

        def ui_reply(line):
            if line.startswith('ask '):
                self.asks.append(line)
                return ['ok'] + ([f'answer {line.split()[1]} {self.answer}'] if self.answer is not None else [])
            return ['ok']
        self.ui = Stand(self.socks/'ui.sock', 'Q2UI/1', ui_reply)
        self.net_state = net
        self.net = Stand(self.socks/'net.sock', 'Q2NET/1',
                         lambda line: [f'status state={self.net_state} ssid=Home ip=10.0.0.2 smart=1 held=0'] if line == 'status' else ['ok'])
        self.pkg = Stand(self.socks/'pkg.sock', 'Q2PKG/1', lambda line: ['ok'])
        self.playd = None
        if player:
            env = dict(os.environ, LD_PRELOAD=str(build/'shim.so'), Q2_IOCTL_LOG=str(tmp/'dac.log'))
            (tmp/'mnt/data/config.ini').write_text((tmp/'mnt/data/config.ini').read_text() + '[PLAYSET]\nVOLUME=30\nMAXVOLUME=100\n')
            self.playd = subprocess.Popen([str(build/'q2playd'), '--root', str(tmp), '--db', str(tmp/'mnt/data/vamp/library.db')],
                                          stderr=subprocess.DEVNULL, env=env)
            self.player = playd.Player(tmp)
            playd.wait_for(tmp/'tmp/coda-rc.cmdq', 5)
        self.log = open(tmp/'q2carrd.log', 'w')
        self.p = subprocess.Popen([str(build/'q2carrd'), '--root', str(tmp), '--sockets', str(self.socks), '--coda-rc', str(build/'coda-rc'),
                                   '--ui-timeout', str(ui_timeout), *iroh, '--no-ble', '--port', '0', '--iroh-port', '0'], stderr=self.log)

    def switch(self, on):
        (self.root/'mnt/data/config.ini').write_text(f'[Q2POD]\nTHEME=0\nCARREMOTE={1 if on else 0}\n')

    def port(self, s=10):
        """the port coda-rc's newest start line names (--port 0: the kernel's pick)"""
        log = self.root/'tmp/coda-rc.log'
        end = time.monotonic() + s
        while time.monotonic() < end:
            ports = re.findall(r'start: tcp (\d+) listening', log.read_text(errors='replace')) if log.exists() else []
            if ports:
                return int(ports[-1])
            time.sleep(0.05)
        raise AssertionError('coda-rc never said it listens')

    def code(self, _prompt=None, s=10):
        """the pairing code, read off the Q2UI/1 card's body as the owner reads it"""
        end = time.monotonic() + s
        while time.monotonic() < end:
            for line in reversed(self.asks):
                m = re.match(r'(\d{3}) (\d{3})\.', urllib.parse.unquote(line.split()[3]))
                if m:
                    return m[1] + m[2]
            time.sleep(0.05)
        raise AssertionError(f'no pairing card: {self.asks}')

    def pump(self, s=0.3):
        if self.playd:
            self.player.pump(s)

    def end(self):
        self.p.terminate()
        try:
            return self.p.wait(10)
        finally:
            if self.p.poll() is None:
                self.p.kill()
            if self.playd:
                self.playd.terminate(), self.playd.wait(5)
            self.log.close()


@pytest.fixture
def q2(build, tmp_path):
    made = []

    def make(**kw):
        made.append(Q2(build, tmp_path, **kw))
        return made[-1]
    yield make
    for q in made:
        q.end()


def wait(pred, s=10):
    end = time.monotonic() + s
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.05)
    return pred()


def listening(port):
    try:
        socket.create_connection(('127.0.0.1', port), timeout=0.3).close()
        return True
    except OSError:
        return False


def test_a_phone_pairs_off_the_card_browses_plays_and_follows_now_playing(q2, tmp_path):
    """The whole path: the code on a Q2UI/1 card titled with the phone's name, q2libd's songs with their lengths, a
    Play q2playd starts, Now Playing and the queue as q2playd writes them, and a Next the phone sees land"""
    q = q2()
    phone = client.connect('127.0.0.1', q.port(), tmp_path/'keys', name='Pixel 10', ask=q.code)
    ask = q.asks[-1].split()
    assert urllib.parse.unquote(ask[2]) == 'Pixel 10' and ask[4:] == ['Cancel'], q.asks
    assert wait(lambda: 'Pixel 10 paired' in [urllib.parse.unquote(l[6:]) for l in q.ui.lines if l.startswith('toast ')]), q.ui.lines
    tracks = phone.browse('tracks')
    assert [i['title'] for i in tracks['items']] == ['Alpha', 'Bravo', 'Charlie']
    assert [i.get('duration_ms') for i in tracks['items']] == [200000, 150000, 90000]
    phone.play('tracks', start='song:t2')
    q.pump(1)
    assert q.player.songs()[-1] == ('set-songurl', '/mnt/mmc/Music/b.flac'), q.player.got
    assert wait(lambda: phone.get_state()['now_playing']['title'] == 'Bravo'), phone.get_state()
    st = phone.get_state()
    assert st['queue']['index'] == 1 and st['queue']['total'] == 3, st
    assert [i['title'] for i in phone.queue()['items']] == ['Alpha', 'Bravo', 'Charlie']
    phone.transport('next')
    q.pump(1)
    assert wait(lambda: phone.get_state()['now_playing']['title'] == 'Charlie'), phone.get_state()
    phone.jump(0, 'song:t1')
    q.pump(1)
    assert wait(lambda: phone.get_state()['now_playing']['title'] == 'Alpha'), phone.get_state()


def test_the_switch_starts_and_stops_coda_rc(q2):
    q = q2(on=False, player=False)
    time.sleep(2)
    assert not (q.root/'tmp/coda-rc.log').exists(), 'nothing runs with Car Remote off'
    q.switch(True)
    port = q.port()
    assert listening(port)
    q.switch(False)
    assert wait(lambda: not listening(port), 5), 'off: coda-rc stopped at once'
    assert not (q.root/'tmp/coda-rc.pid').exists()


def test_cancel_on_the_card_ends_the_pairing(q2, tmp_path):
    """Cancel writes .pair-cancel, which coda-rc takes (crc_pair_cancelled): the code it showed no longer pairs"""
    q = q2(answer='1', player=False)

    def typed_late(_prompt):
        code = q.code()
        assert wait(lambda: not (q.root/'tmp/coda-rc.pair').exists()), 'the pairing ended'
        return code
    with pytest.raises((client.RemoteError, OSError, ValueError)):
        client.connect('127.0.0.1', q.port(), tmp_path/'keys', ask=typed_late)
    assert len(q.asks) == 1, 'a cancelled code is not asked again'
    assert 'pairing: ended' in (q.root/'tmp/coda-rc.log').read_text()


def test_q2ui_s_own_timeout_asks_again_while_the_pairing_stands(q2):
    """notice.c answers 0 at its TIMEOUT_S (60 s; here --ui-timeout 1000) and the pairing lasts 120 s (stock's card): a 0
    that late is asked again, a 0 sooner (Return) cancels"""
    q = q2(player=False, ui_timeout=1000)
    q.port()
    (q.root/'tmp/coda-rc.pair').write_text('123456\nPixel\n')
    assert wait(lambda: len(q.asks) == 1)
    time.sleep(1.2)
    q.ui.push(f'answer {q.asks[0].split()[1]} 0')  # Q2UI/1's own timeout
    assert wait(lambda: len(q.asks) == 2), q.asks
    assert not (q.root/'tmp/coda-rc.pair-cancel').exists()
    q.ui.push(f'answer {q.asks[1].split()[1]} 0')  # straight away: Return
    assert wait(lambda: (q.root/'tmp/coda-rc.pair-cancel').exists()), 'Return cancels'
    time.sleep(1.5)
    assert len(q.asks) == 2, 'not asked again once cancelled'


def test_the_pairing_s_end_is_toasted_and_the_card_withdrawn(q2):
    q = q2(player=False)
    q.port()
    (q.root/'tmp/coda-rc.pair').write_text('654321\nPixel 9\n')
    assert wait(lambda: len(q.asks) == 1)
    (q.root/'tmp/coda-rc.pair').unlink()
    (q.root/'tmp/coda-rc.pair-end').write_text('0\n')
    assert wait(lambda: f'withdraw {q.asks[0].split()[1]}' in q.ui.lines), q.ui.lines
    assert wait(lambda: 'toast Pairing%20failed' in q.ui.lines), q.ui.lines
    assert not (q.root/'tmp/coda-rc.pair-end').exists()


def test_a_cover_s_app_is_started_only_when_its_manifest_says_covers(q2):
    q = q2(player=False)
    q.port()
    for app, covers in (('navi', 'covers=1\n'), ('other', 'name=Other\n')):
        (q.root/f'mnt/mmc/.{app}').mkdir(parents=True)
        (q.root/f'mnt/mmc/.{app}/q2app').write_text(f'name={app}\n{covers}')
    (q.root/'tmp/coda-rc.wake').write_text('other\n')
    time.sleep(1.5)
    (q.root/'tmp/coda-rc.wake').write_text('navi\n')
    assert wait(lambda: 'start navi' in q.pkg.lines), q.pkg.lines
    assert 'start other' not in q.pkg.lines


def test_the_radio_is_held_while_connected_and_not_otherwise(q2):
    q = q2(player=False, net='searching')
    q.port()
    time.sleep(2)
    assert 'status' in q.net.lines and not any(l.startswith('hold') for l in q.net.lines), q.net.lines
    q.end()
    q2b = Q2(q.out, q.root/'b')
    try:
        assert wait(lambda: 'hold 60' in q2b.net.lines), q2b.net.lines
    finally:
        q2b.end()


def test_sigterm_stops_coda_rc_then_q2carrd(q2):
    q = q2(player=False)
    port = q.port()
    assert q.end() == 0 and not listening(port)


def iroh(tmp, *more):
    """q2carrd's options for IROH as q2-iroh: (its args file, the options)"""
    fake = tmp/'q2-iroh'
    fake.write_text(IROH)
    fake.chmod(0o755)
    return tmp/'tmp/q2-iroh.pid.args', ('--iroh', str(fake), *more)


def iroh_running(q):
    pid = q.root/'tmp/q2-iroh.pid'
    try:
        os.kill(int(pid.read_text()), 0)
        return True
    except (OSError, ValueError):
        return False


def test_q2_iroh_runs_while_wifi_has_a_link_with_the_shim_s_arguments(q2, tmp_path):
    """coda_rc_shim.c cr_iroh_poll: started while Car Remote is on and Q2NET/1 says connected, with CR_IROH_ARGS under
    --root; stopped CR_IROH_DOWN_MS (here --iroh-down 4000) after the link went, and started again when it is back"""
    args, opts = iroh(tmp_path, '--iroh-down', '4000')
    q = q2(player=False, iroh=opts)
    assert wait(lambda: iroh_running(q)), (q.root/'q2carrd.log').read_text()
    r = str(q.root)
    assert args.read_text().split() == [
        'serve', '--key', f'{r}/mnt/data/coda-rc/iroh.key', '--relay-file', f'{r}/mnt/data/coda-rc/relay',
        '--info', f'{r}/tmp/coda-rc.iroh', '--pid', f'{r}/tmp/q2-iroh.pid', '--nice', '19', '--log', f'{r}/tmp/q2-iroh.log',
        '--forward-named', '127.0.0.1:7721', '--allow-file', f'{r}/tmp/coda-rc.iroh-allow', '--open-while', f'{r}/tmp/coda-rc.qr']
    q.net_state = 'searching'  # the last look that saw the link was at most LINK_MS (1 s) before this
    time.sleep(2)
    assert iroh_running(q), 'a link gone for less than --iroh-down keeps it'
    assert wait(lambda: not iroh_running(q), 6), 'stopped once the link is gone that long'
    assert not (q.root/'tmp/q2-iroh.pid').exists() and not (q.root/'tmp/coda-rc.iroh').exists()
    q.net_state = 'connected'
    assert wait(lambda: iroh_running(q)), 'started again with the link back'
    assert len(args.read_text().splitlines()) == 2


def test_q2_iroh_waits_for_a_link_and_goes_with_car_remote(q2, tmp_path):
    args, opts = iroh(tmp_path)
    q = q2(player=False, net='searching', iroh=opts)
    q.port()
    time.sleep(2.5)
    assert not args.exists(), 'no link, no q2-iroh'
    q.net_state = 'connected'
    assert wait(lambda: iroh_running(q))
    q.switch(False)
    assert wait(lambda: not iroh_running(q), 5), 'Car Remote off stops it at once'
    q.switch(True)
    assert wait(lambda: iroh_running(q))
    assert q.end() == 0 and not iroh_running(q), 'TERM stops it with q2carrd'


def test_q2_iroh_that_ends_is_started_again_after_the_restart_wait(q2, tmp_path):
    args, opts = iroh(tmp_path, '--restart', '1500')
    q = q2(player=False, iroh=opts)
    assert wait(lambda: iroh_running(q))
    os.kill(int((q.root/'tmp/q2-iroh.pid').read_text()), 15)
    assert wait(lambda: not iroh_running(q), 2)
    assert wait(lambda: len(args.read_text().splitlines()) == 2, 5), 'started again'


def test_no_q2_iroh_in_the_image_is_no_error(q2, tmp_path):
    q = q2(player=False, iroh=('--iroh', str(tmp_path/'none')))
    q.port()
    time.sleep(2.5)
    assert 'iroh' not in (q.root/'q2carrd.log').read_text()


if __name__ == '__main__':
    sys.exit(pytest.main([__file__, '-q', '-p', 'no:cacheprovider']))
