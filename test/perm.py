#!/usr/bin/env python3
"""Vamp API 1's run-time permissions (docs/platform/packages.md#permissions, perm/q2perm.h): q2playd, q2sysd and
q2netd, built for this machine, each refusing a caller whose user is not in its permission's group and letting root
and a member in; the caller's uid the kernel's (SO_PEERCRED), shown by running a service in a user namespace of its
own (bwrap --unshare-user --uid N), where this suite's connections arrive as uid N; and q2pkgd's package users:
app-add's uid and groups, accounts' files, app-remove, and app-run refusing to start an entry it cannot drop to its
user. Run it as python3 test/perm.py (pytest). Q2PLAYD_SOURCE, Q2SYSD_SOURCE, Q2NETD_SOURCE and Q2PKGD_SOURCE build
other copies, and Q2PERM_DIR another perm/, to show a broken one fails here."""
import os
import pathlib
import socket
import subprocess
import sys
import time

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import conformance  # noqa: E402
import netd  # noqa: E402
import pkgd as pkgd_test  # noqa: E402
import playd  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[1]
SOURCES = {'playd': os.environ.get('Q2PLAYD_SOURCE') or ROOT/'playd/q2playd.c',
           'sysd': os.environ.get('Q2SYSD_SOURCE') or ROOT/'sysd/q2sysd.c',
           'netd': os.environ.get('Q2NETD_SOURCE') or ROOT/'netd/q2netd.c'}
PKGD = pathlib.Path(os.environ.get('Q2PKGD_SOURCE') or ROOT/'pkgd/q2pkgd.c')
# each service: its socket, its hello, its permission
SERVICES = {'playd': ('play.sock', 'Q2PLAY/1', 'playback'), 'sysd': ('sys.sock', 'Q2SYS/1', 'settings'),
            'netd': ('net.sock', 'Q2NET/1', 'network-hold')}


@pytest.fixture(scope='session')
def bins(tmp_path_factory):
    out = tmp_path_factory.mktemp('perm')
    b = {'playd': playd.build(tmp_path_factory.mktemp('playd'))}
    patch = ROOT/'patch'
    guard = [str(patch/f) for f in ('q2guard_core.c', 'q2guard_pmic.c', 'card_store.c')]  # q2sysd's heat, as sysd/Makefile builds it
    for name, extra in (('sysd', ['-std=gnu11', '-DQ2GUARD_HOST', '-I', str(patch)]), ('netd', ['-std=c11'])):
        subprocess.run(['cc', *extra, '-Wall', '-Wextra', '-Werror', '-O2', '-I', str(conformance.PERM), '-o', str(out/f'q2{name}'),
                        str(SOURCES[name]), *(guard + ['-pthread'] if name == 'sysd' else [])], check=True)
        b[name] = out/f'q2{name}'
    (out/'fake_wpa.py').write_text(netd.FAKE_WPA)
    pkgd_test.compile_q2pkgd(out/'q2pkgd', source=PKGD, perm=conformance.PERM)
    b['pkgd'], b['dir'] = out/'q2pkgd', out
    return b


def as_uid(uid):
    """a command's prefix: a user namespace of its own where it is uid (and gid) uid"""
    return ['bwrap', '--unshare-user', '--uid', str(uid), '--gid', str(uid), '--bind', '/', '/', '--dev-bind', '/dev', '/dev',
            '--proc', '/proc', '--die-with-parent']


class Service:
    def __init__(self, bins, name, root, uid=None):
        self.name, self.root = name, root
        for d in ('tmp', 'run/vamp', 'mnt/data', 'dev/input'):
            (root/d).mkdir(parents=True, exist_ok=True)
        if name == 'playd':
            cmd = [str(bins['playd']), '--root', str(root)]
        elif name == 'sysd':
            cmd = [str(bins['sysd']), '--root', str(root), '--poweroff', 'true']
        else:
            cmd = [str(bins['netd']), '--root', str(root), '--wpa', f'exec python3 {bins["dir"]}/fake_wpa.py {root} -iwlan0',
                   '--dhcp', 'exec sleep 1000', '--up', 'true', '--down', 'true']
        self.log = root/f'{name}.log'
        self.p = subprocess.Popen((as_uid(uid) if uid is not None else []) + cmd, stderr=open(self.log, 'wb'))
        sock = root/'run/vamp'/SERVICES[name][0]
        end = time.monotonic() + 5
        while not sock.exists() and time.monotonic() < end:
            time.sleep(0.05)
        time.sleep(0.2)

    def hello(self):
        """the service's answer to the hello, and whether it then closed the connection"""
        s = socket.socket(socket.AF_UNIX)
        s.connect(str(self.root/'run/vamp'/SERVICES[self.name][0]))
        s.settimeout(3)
        f = s.makefile('rw')
        f.write(SERVICES[self.name][1] + '\n')
        f.flush()
        first = f.readline().rstrip('\n')
        if first.endswith(' ok'):  # let in: a command is answered
            f.write('caps\n')
            f.flush()
        closed = not f.readline().startswith('caps ')
        f.close(), s.close()
        return first, closed

    def stop(self):
        self.p.terminate()
        self.p.wait(10)
        return self.log.read_text()


@pytest.fixture
def start(bins, tmp_path):
    started = []

    def go(name, uid=None):
        started.append(Service(bins, name, tmp_path, uid))
        return started[-1]
    yield go
    for s in started:
        if s.p.poll() is None:
            s.stop()


# ---- each service's check ----

@pytest.mark.parametrize('name', SERVICES)
def test_a_caller_without_the_permission_is_refused_and_logged(start, tmp_path, name):
    perm = SERVICES[name][2]
    conformance.accounts(tmp_path, [p for p in conformance.PERMISSIONS if p != perm])
    s = start(name)
    assert s.hello() == ('err permission', True)
    assert f'refused uid {os.getuid()} (tester): no {perm} permission' in s.stop()


@pytest.mark.parametrize('name', SERVICES)
def test_a_member_of_the_permission_s_group_is_let_in(start, tmp_path, name):
    conformance.accounts(tmp_path, [SERVICES[name][2]])
    assert start(name).hello() == (f'{SERVICES[name][1]} ok', False)


@pytest.mark.parametrize('name', SERVICES)
def test_a_uid_with_no_user_is_refused(start, tmp_path, name):
    conformance.accounts(tmp_path, uid=os.getuid() + 1)  # every group, for another uid
    s = start(name)
    assert s.hello() == ('err permission', True)
    assert f'refused uid {os.getuid()} (no such user)' in s.stop()


@pytest.mark.parametrize('name', SERVICES)
def test_root_is_let_in_with_no_groups(start, tmp_path, name):
    """the service in a user namespace where this suite's uid is 0: its connections arrive as root's"""
    s = start(name, uid=0)
    assert s.hello() == (f'{SERVICES[name][1]} ok', False)


# ---- packages' users, end to end ----

APPS = {'radio': 'name=Radio\napi=1\nversion=1\nkind=browse\nentry=run\npermissions=playback,network-hold\n',
        'clock': 'name=Clock\napi=1\nversion=1\nkind=browse\nentry=run --face big\npermissions=settings\n',
        'third': 'name=Third\napi=1\nversion=1\nkind=browse\nentry=run\npermissions=\n'}
BASE_PASSWD = 'root:x:0:0:root:/root:/bin/sh\nnobody:x:65534:65534:nobody:/home:/bin/false\n'
BASE_GROUP = 'root:x:0:\naudio:x:29:\nnobody:x:65534:\n'


def pkgd(bins, root, *args):
    r = subprocess.run([str(bins['pkgd']), *args, '--root', str(root)], capture_output=True, text=True)
    return r.returncode, (r.stdout + r.stderr).strip()


@pytest.fixture
def image(tmp_path):
    """the image's accounts as post-fakeroot.sh leaves them: the .base files, /etc/passwd and /etc/group links into
    /run/vamp (relative here, to stay under the root)"""
    (tmp_path/'etc').mkdir()
    (tmp_path/'etc/passwd.base').write_text(BASE_PASSWD)
    (tmp_path/'etc/group.base').write_text(BASE_GROUP)
    (tmp_path/'etc/passwd').symlink_to('../run/vamp/passwd')
    (tmp_path/'etc/group').symlink_to('../run/vamp/group')
    for name, manifest in APPS.items():
        d = tmp_path/'mnt/mmc'/f'.{name}'
        d.mkdir(parents=True)
        (d/'q2app').write_text(manifest)
        (d/'run').write_text(f'#!/bin/sh\necho ran >> {tmp_path}/ran\n')
        (d/'run').chmod(0o755)
    return tmp_path


def test_app_add_gives_each_package_a_user_and_its_permissions_groups(bins, image):
    assert pkgd(bins, image, 'app-add', str(image/'mnt/mmc/.radio')) == (0, 'app radio: user app-radio (uid 10000), groups: playback,network-hold')
    assert pkgd(bins, image, 'app-add', str(image/'mnt/mmc/.clock')) == (0, 'app clock: user app-clock (uid 10001), groups: settings')
    assert (image/'mnt/data/vamp/apps').read_text() == 'radio 10000 playback,network-hold\nclock 10001 settings\n'
    assert (image/'etc/passwd').read_text() == BASE_PASSWD + (
        'app-radio:x:10000:10000:radio:/mnt/data/vamp/app/radio:/bin/false\napp-clock:x:10001:10001:clock:/mnt/data/vamp/app/clock:/bin/false\n')
    group = (image/'etc/group').read_text().splitlines()
    assert group[:5] == ['root:x:0:', 'audio:x:29:', 'nobody:x:65534:', 'app-radio:x:10000:', 'app-clock:x:10001:']
    assert group[5:] == ['playback:x:2000:app-radio', 'library:x:2001:', 'library-write:x:2002:', 'settings:x:2003:app-clock',
                         'network-hold:x:2004:app-radio', 'screen:x:2005:', 'music-files:x:2006:', 'background:x:2007:',
                         'notices:x:2008:', 'ask:x:2009:']
    assert oct((image/'mnt/data/vamp/app/radio').stat().st_mode & 0o777) == '0o700', 'its data folder, its own'
    assert pkgd(bins, image, 'app-add', str(image/'mnt/mmc/.radio'))[1].startswith('app radio: user app-radio (uid 10000)'), 'again: the same uid'


def test_a_removed_package_keeps_its_uid_and_no_other_gets_it(bins, image):
    pkgd(bins, image, 'app-add', str(image/'mnt/mmc/.radio'))
    assert pkgd(bins, image, 'app-remove', 'radio') == (0, 'app radio: removed; uid 10000 and its data folder stay its own')
    assert 'app-radio' not in (image/'etc/passwd').read_text() and 'app-radio' not in (image/'etc/group').read_text()
    assert pkgd(bins, image, 'app-add', str(image/'mnt/mmc/.third')) == (0, 'app third: user app-third (uid 10001), groups: none')
    assert pkgd(bins, image, 'app-remove', 'radio') == (1, 'q2pkgd: radio: not added')
    assert pkgd(bins, image, 'app-add', str(image/'mnt/mmc/.radio'))[1].startswith('app radio: user app-radio (uid 10000)'), 'back: its own uid'


def test_an_api_0_app_and_a_bad_manifest_get_no_user(bins, image):
    d = image/'mnt/mmc/.subsonic'
    d.mkdir()
    (d/'q2app').write_text('name=Subsonic\ncatalogue=1\n')
    assert pkgd(bins, image, 'app-add', str(d)) == (0, 'app subsonic: API 0, run as today with no user of its own')
    (image/'mnt/mmc/.radio/q2app').write_text(APPS['radio'].replace('playback', 'everything'))
    assert pkgd(bins, image, 'app-add', str(image/'mnt/mmc/.radio')) == (1, 'q2pkgd: q2app: everything is not a permission')
    assert not (image/'mnt/data/vamp/apps').exists()
    long_id = image/'mnt/mmc'/('.' + 'x' * 28)
    long_id.mkdir()
    (long_id/'q2app').write_text(APPS['third'])
    (long_id/'run').write_text('')
    assert pkgd(bins, image, 'app-add', str(long_id))[0] == 1


def test_rcs_s_accounts_with_no_packages_is_the_image_s_users_and_empty_groups(bins, image):
    assert pkgd(bins, image, 'accounts') == (0, '')
    assert (image/'etc/passwd').read_text() == BASE_PASSWD
    assert (image/'etc/group').read_text().splitlines()[3] == 'playback:x:2000:'


def test_app_run_starts_nothing_when_it_cannot_become_the_package_s_user(bins, image):
    pkgd(bins, image, 'app-add', str(image/'mnt/mmc/.radio'))
    code, out = pkgd(bins, image, 'app-run', str(image/'mnt/mmc/.radio'))
    assert code == 1 and out.startswith('q2pkgd: cannot become app-radio: ') and out.endswith('; nothing run')
    assert not (image/'ran').exists(), 'the entry never ran as this uid'
    assert pkgd(bins, image, 'app-run', str(image/'mnt/mmc/.clock')) == (1, 'q2pkgd: clock: not added (q2pkgd app-add)')


@pytest.mark.parametrize('name, uid, answer', [
    ('playd', 10000, 'Q2PLAY/1 ok'), ('playd', 10001, 'err permission'),
    ('sysd', 10001, 'Q2SYS/1 ok'), ('sysd', 10000, 'err permission'),
    ('netd', 10000, 'Q2NET/1 ok'), ('netd', 10001, 'err permission'), ('netd', 10002, 'err permission')])
def test_a_package_s_uid_reaches_only_the_services_it_was_given(bins, image, start, name, uid, answer):
    """q2pkgd's accounts, and each service seeing this suite's connections as the package's uid"""
    for a in ('radio', 'clock', 'third'):
        pkgd(bins, image, 'app-add', str(image/'mnt/mmc'/f'.{a}'))
    assert start(name, uid=uid).hello()[0] == answer


def test_a_removed_package_s_uid_is_refused(bins, image, start):
    pkgd(bins, image, 'app-add', str(image/'mnt/mmc/.radio'))
    pkgd(bins, image, 'app-remove', 'radio')
    s = start('playd', uid=10000)
    assert s.hello() == ('err permission', True)
    assert 'refused uid 10000 (no such user)' in s.stop()


if __name__ == '__main__':
    sys.exit(pytest.main([__file__, '-q', '-p', 'no:cacheprovider']))
