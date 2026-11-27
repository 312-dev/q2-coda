#!/usr/bin/env python3
"""q2pkgd, the launcher (docs/platform/packages.md#starting-and-stopping-q2pkg1): `q2pkgd serve` built for this
machine, starting apps under BusyBox 1.37.0's own runsv (the image's applet, built for this machine from the tarball
Buildroot fetched), with a stand-in for q2-subsonic on the card. Q2PKG/1's transcripts replayed as root, the API 0
capability line, stops, an API 1 app through app-run, a service kept up, the peer checks, and q2playd starting a
stopped app for a server play through it. Run it as python3 test/launch.py (pytest). Q2PKGD_SOURCE and
Q2PLAYD_SOURCE build other copies, Q2_BUSYBOX_TAR names another BusyBox tarball."""
import os
import pathlib
import socket
import subprocess
import sys
import tarfile
import time

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import conformance  # noqa: E402
import playd  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
PKGD = pathlib.Path(os.environ.get('Q2PKGD_SOURCE') or ROOT/'pkgd/q2pkgd.c')
BUSYBOX = pathlib.Path(os.environ.get('Q2_BUSYBOX_TAR') or pathlib.Path.home()/'.cache/q2-os/dl/busybox/busybox-1.37.0.tar.bz2')
CAPS = 'catalogue playlist\n'  # the words docs/platform/packages.md names, this image's for an API 0 app

# The stand-in API 0 app, as q2-subsonic's run script and daemon behave: the launcher returns once the loop runs in the
# background with its pid in /tmp/q2-navi; the app records the caps line it found, takes a .catplay there at its start
# as answered, then listens on its browse socket and answers each new .catplay with an m3u of its own.
NAVI_RUN = r'''#!/bin/sh
pid={root}/tmp/q2-navi
[ -f $pid ] && kill -0 "$(cat $pid)" 2>/dev/null && exit 0
( trap 'rm -f $pid; exit 0' TERM
  python3 {root}/navi.py &
  child=$!
  trap 'kill -TERM $child; wait $child; rm -f $pid; exit 0' TERM
  wait $child
  rm -f $pid ) </dev/null >/dev/null 2>&1 &
echo $! > $pid
'''
NAVI_PY = r'''import os, signal, socket, sys, time
root = {root!r}
signal.signal(signal.SIGTERM, lambda *a: (os.path.exists(root + '/tmp/q2-navi.browse.sock') and os.unlink(root + '/tmp/q2-navi.browse.sock'), sys.exit(0)))
caps = open(root + '/tmp/q2-addon.caps').read() if os.path.exists(root + '/tmp/q2-addon.caps') else None
open(root + '/navi.started', 'a').write(repr(caps) + '\n')
cp = root + '/tmp/q2-navi.catplay'
seen = open(cp).read() if os.path.exists(cp) else None
time.sleep(float(os.environ.get('NAVI_DELAY', '0.5')))
s = socket.socket(socket.AF_UNIX)
s.bind(root + '/tmp/q2-navi.browse.sock')
s.listen(1)
while True:
    now = open(cp).read() if os.path.exists(cp) else None
    if now and now != seen:
        seen = now
        seq = now.split()[0]
        os.makedirs(root + '/mnt/mmc/.navi/cache', exist_ok=True)  # the song whole before its line, as the app renames it in
        open(root + '/mnt/mmc/.navi/cache/s1.flac', 'wb').write(b'song')
        open(root + '/tmp/navi.m3u', 'w').write('#EXTM3U\n/mnt/mmc/.navi/cache/s1.flac\n')
        open(root + '/tmp/q2-navi.play.tmp', 'w').write(seq + ' /tmp/navi.m3u\n')
        os.rename(root + '/tmp/q2-navi.play.tmp', root + '/tmp/q2-navi.play')
    time.sleep(0.1)
'''
RADIO = 'name=Radio\napi=1\nversion=1.2.0\nkind=browse\nentry=q2-radio --quiet\npermissions=playback,network-hold\n'
CHIME = 'name=Chime\napi=1\nversion=1\nkind=service\nentry=q2-chime\npermissions=background\n'


@pytest.fixture(scope='session')
def bins(tmp_path_factory):
    out = tmp_path_factory.mktemp('launch')
    mono = ROOT/'patch/monocypher'
    subprocess.run(['cc', '-std=c11', '-Wall', '-Wextra', '-Werror', '-O2', f'-I{mono}', '-I', str(conformance.PERM), '-DRELEASE_KEY={0}',
                    '-o', str(out/'q2pkgd'), str(PKGD), str(mono/'monocypher.c'), str(mono/'monocypher-ed25519.c')], check=True)
    with tarfile.open(BUSYBOX) as t:
        t.extractall(out, filter='data')
    src = out/'busybox-1.37.0'
    subprocess.run(['make', 'allnoconfig'], cwd=src, check=True, capture_output=True)
    conf = (src/'.config').read_text()
    for o in ('RUNSV', 'SV'):
        conf = conf.replace(f'# CONFIG_{o} is not set', f'CONFIG_{o}=y')
    (src/'.config').write_text(conf)
    subprocess.run('yes "" | make oldconfig', shell=True, cwd=src, check=True, capture_output=True)
    subprocess.run(['make', f'-j{os.cpu_count() or 2}', 'busybox'], cwd=src, check=True, capture_output=True)
    (out/'runsv').symlink_to(src/'busybox')
    (out/'sv').symlink_to(src/'busybox')
    return {'pkgd': out/'q2pkgd', 'runsv': out/'runsv', 'sv': out/'sv', 'playd': playd.build(tmp_path_factory.mktemp('playd'))}


def card(root):
    """the examples' card: .navi (API 0), .radio (API 1, not added), .dusk (a theme), .chime (a service)"""
    for d in ('tmp', 'run/vamp', 'mnt/data/vamp', 'etc'):
        (root/d).mkdir(parents=True, exist_ok=True)
    (root/'etc/passwd.base').write_text('root:x:0:0:root:/root:/bin/sh\n')
    (root/'etc/group.base').write_text('root:x:0:\n')
    apps = {'navi': ('name=Navi\ncatalogue=1\n', {'run': NAVI_RUN.format(root=root)}),
            'radio': (RADIO, {'q2-radio': f'#!/bin/sh\nid -u > {root}/radio.ran\nexec sleep 1000\n'}),
            'dusk': ('name=Dusk\napi=1\nversion=1\nkind=theme\n', {}),
            'chime': (CHIME, {'q2-chime': f'#!/bin/sh\necho x >> {root}/chime.ran\nexit 1\n'})}
    for name, (manifest, files) in apps.items():
        d = root/'mnt/mmc'/f'.{name}'
        d.mkdir(parents=True, exist_ok=True)
        (d/'q2app').write_text(manifest)
        for f, text in files.items():
            (d/f).write_text(text)
            (d/f).chmod(0o755)
    (root/'navi.py').write_text(NAVI_PY.format(root=str(root)))


class Pkgd:
    """q2pkgd serve under root, in a user namespace where this suite's connections arrive as uid"""
    def __init__(self, bins, root, uid=0):
        self.root, self.bins = root, bins
        self.log = root/'pkgd.log'
        ns = ['bwrap', '--unshare-user', '--uid', str(uid), '--gid', str(uid), '--bind', '/', '/',
              '--dev-bind', '/dev', '/dev', '--proc', '/proc', '--die-with-parent']
        self.p = subprocess.Popen(ns + [str(bins['pkgd']), 'serve', '--root', str(root), '--runsv', str(bins['runsv']),
                                        '--self', str(bins['pkgd'])], stderr=open(self.log, 'wb'))
        sock = root/'run/vamp/pkg.sock'
        end = time.monotonic() + 5
        while not sock.exists() and time.monotonic() < end:
            time.sleep(0.05)

    def say(self, *lines):
        """the answers to Q2PKG/1 lines, the hello's first"""
        s = socket.socket(socket.AF_UNIX)
        s.connect(str(self.root/'run/vamp/pkg.sock'))
        s.settimeout(10)
        f = s.makefile('rw')
        out = []
        for line in ('Q2PKG/1',) + lines:
            f.write(line + '\n')
            f.flush()
            out.append(f.readline().rstrip('\n'))
        s.close()
        return out[1:] if out[0] == 'Q2PKG/1 ok' else out

    def stop(self):
        self.p.terminate()
        self.p.wait(10)
        return self.log.read_text()

    def clear(self):
        """every runsv it started told to stop its app and end (runit's d, then x), and navi's loop ended"""
        for sv in (self.root/'run/vamp/sv').glob('*/supervise/control'):
            try:
                fd = os.open(sv, os.O_WRONLY | os.O_NONBLOCK)
                os.write(fd, b'dx')
                os.close(fd)
            except OSError:
                pass
        try:
            os.kill(int((self.root/'tmp/q2-navi').read_text()), 15)
        except (OSError, ValueError):
            pass


@pytest.fixture
def pkgd(bins, tmp_path):
    started = []

    def go(uid=0):
        card(tmp_path)
        started.append(Pkgd(bins, tmp_path, uid))
        return started[-1]
    yield go
    for p in started:
        if p.p.poll() is None:
            p.stop()
        p.clear()


def until(pred, secs=5.0):
    end = time.monotonic() + secs
    while time.monotonic() < end and not pred():
        time.sleep(0.05)
    return pred()


# ---- the page's transcripts ----

def test_conformance_every_command_is_on_the_page():
    conformance.check_coverage('packages.md')


@pytest.mark.parametrize('n', range(len(conformance.blocks('packages.md'))))
def test_conformance_packages(pkgd, tmp_path, n):
    pkgd()
    conformance.replay(tmp_path/'run/vamp/pkg.sock', conformance.blocks('packages.md')[n], timeout=15)


# ---- API 0 ----

def test_an_api_0_app_finds_the_image_s_words_and_runs_under_runsv(bins, pkgd, tmp_path):
    p = pkgd()
    (tmp_path/'tmp/q2-addon.caps').write_text('playlist progress pages\n')  # a stale line: rewritten before the start
    assert p.say('start navi') == ['ok']
    assert until(lambda: (tmp_path/'navi.started').exists())
    assert (tmp_path/'navi.started').read_text() == repr(CAPS) + '\n', 'the caps line, written before the app started'
    st = subprocess.run([str(bins['sv']), 'status', str(tmp_path/'run/vamp/sv/navi')], capture_output=True, text=True).stdout
    assert st.startswith('run: '), f'runsv runs it ({st!r})'
    loop = int((tmp_path/'tmp/q2-navi').read_text())
    assert p.say('stop navi') == ['ok']
    assert not (tmp_path/'tmp/q2-navi').exists(), "the loop's TERM: it removed its pid file"
    assert not os.path.exists(f'/proc/{loop}'), 'and it is gone'


def test_an_api_0_app_s_idle_exit_stands(bins, pkgd, tmp_path):
    p = pkgd()
    assert p.say('start navi') == ['ok']
    assert until(lambda: (tmp_path/'navi.started').exists())
    os.kill(int((tmp_path/'tmp/q2-navi').read_text()), 15)  # the app ends itself, as an idle exit
    assert until(lambda: p.say('status navi') == ['status navi stopped'])
    time.sleep(2)
    assert p.say('status navi') == ['status navi stopped'], 'runsv does not start it again'
    assert (tmp_path/'navi.started').read_text().count('\n') == 1


def test_sv_down_ends_an_api_0_app_s_loop(bins, pkgd, tmp_path):
    p = pkgd()
    assert p.say('start navi') == ['ok']
    assert until(lambda: (tmp_path/'tmp/q2-navi').exists())
    subprocess.run([str(bins['sv']), 'down', str(tmp_path/'run/vamp/sv/navi')], check=True, capture_output=True)
    assert until(lambda: not (tmp_path/'tmp/q2-navi').exists())
    assert until(lambda: p.say('status navi') == ['status navi stopped'])


def test_an_api_0_app_started_elsewhere_counts_as_running(pkgd, tmp_path):
    p = pkgd()
    subprocess.run(['/bin/sh', str(tmp_path/'mnt/mmc/.navi/run')], check=True)
    assert until(lambda: (tmp_path/'tmp/q2-navi').exists())
    assert p.say('status navi', 'stop navi', 'status navi') == ['status navi running', 'ok', 'status navi stopped']


# ---- API 1 ----

def test_an_api_1_app_runs_through_app_run_as_its_user(bins, pkgd, tmp_path):
    p = pkgd()
    r = subprocess.run([str(bins['pkgd']), 'app-add', str(tmp_path/'mnt/mmc/.radio'), '--root', str(tmp_path)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert p.say('start radio') == ['ok']
    assert (tmp_path/'run/vamp/sv/radio/run').read_text().endswith(
        f'exec {bins["pkgd"]} app-run {tmp_path}/mnt/mmc/.radio --root {tmp_path} 2>&1\n')
    # here (one uid, a namespace mapping only it) it cannot become app-radio, so app-run runs nothing
    assert until(lambda: 'q2pkgd: cannot become app-radio' in p.log.read_text())
    assert not (tmp_path/'radio.ran').exists()
    assert until(lambda: p.say('status radio') == ['status radio stopped'])


def test_a_removed_api_1_app_is_not_started(bins, pkgd, tmp_path):
    p = pkgd()
    for cmd in (['app-add', str(tmp_path/'mnt/mmc/.radio')], ['app-remove', 'radio']):
        subprocess.run([str(bins['pkgd']), *cmd, '--root', str(tmp_path)], check=True, capture_output=True)
    assert p.say('start radio') == ['err not-added']


def test_a_service_is_kept_up_by_runsv(bins, pkgd, tmp_path):
    """chime's entry ends at once; as a service, runsv starts app-run again (which here cannot drop to its user, so
    each try is app-run's)"""
    p = pkgd()
    subprocess.run([str(bins['pkgd']), 'app-add', str(tmp_path/'mnt/mmc/.chime'), '--root', str(tmp_path)], check=True, capture_output=True)
    assert p.say('start chime') == ['ok']
    assert until(lambda: p.log.read_text().count('cannot become app-chime') >= 2, 6), 'runsv started it again'
    assert p.say('stop chime') == ['ok']


# ---- who may ask ----

def test_a_package_may_ask_after_and_stop_itself_alone(bins, pkgd, tmp_path):
    card(tmp_path)
    (tmp_path/'mnt/data/vamp/apps').write_text('radio 10000 playback,network-hold\n')
    p = pkgd(uid=10000)
    assert p.say('status radio', 'stop radio', 'start radio', 'start navi', 'status navi', 'stop navi') == [
        'status radio stopped', 'ok', 'err permission', 'err permission', 'err permission', 'err permission']
    log = p.stop()
    assert 'refused uid 10000 (app-radio): start navi' in log and 'refused uid 10000 (app-radio): start radio' in log
    assert not (tmp_path/'navi.started').exists()


def test_a_uid_with_no_package_is_refused(pkgd, tmp_path):
    p = pkgd(uid=10005)
    assert p.say('status navi') == ['err permission']
    assert 'refused uid 10005 (no package): status navi' in p.stop()


# ---- q2playd's server play of a stopped app (docs/playd.md#server-songs) ----

@pytest.fixture
def playing(bins, shim, pkgd, tmp_path):
    """q2pkgd serving the card, and q2playd with navi's two server rows; navi not running"""
    p = pkgd()
    for d in ('dev',):
        (tmp_path/d).mkdir(exist_ok=True)
    (tmp_path/'dev/shanling_dac').write_text('')
    (tmp_path/'mnt/data/config.ini').write_text(playd.CONFIG)
    conformance.accounts(tmp_path)
    import sqlite3
    db = sqlite3.connect(tmp_path/'mnt/data/database.db')
    db.execute('create table songtable(id integer primary key, fileurl, name, songer, album, track)')
    db.executemany('insert into songtable values (?, ?, ?, ?, ?, 0)', playd.SONGS)
    db.commit()
    db.close()
    playd.catalogue(tmp_path, listening=False)
    env = dict(os.environ, LD_PRELOAD=str(shim), Q2_IOCTL_LOG=str(tmp_path/'dac.log'))
    q = subprocess.Popen([str(bins['playd']), '--root', str(tmp_path)], stderr=open(tmp_path/'playd.log', 'wb'), env=env)
    yield p, playd.Player(tmp_path)
    q.terminate()
    q.wait(5)


shim = playd.shim


def test_a_server_play_starts_the_stopped_app_and_plays_its_answer(playing, tmp_path):
    p, pl = playing
    playd.play_list(tmp_path, [1, playd.SERVER + 1], 1)
    assert until(lambda: (tmp_path/'navi.started').exists()), 'q2playd asked q2pkgd, which started navi'
    pl.pump(4.0)
    assert pl.songs() == [('set-songurl', '/mnt/mmc/.navi/cache/s1.flac')], "the started app's m3u plays"
    assert (tmp_path/'tmp/q2-navi.catplay').read_text().splitlines()[0] == '1 1'
    assert p.say('status navi') == ['status navi running']


if __name__ == '__main__':
    sys.exit(pytest.main([__file__, '-q', '-p', 'no:cacheprovider']))
