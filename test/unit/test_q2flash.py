"""tools/q2flash.py: which files are ours, the battery rule, and the push, the check of its SHA-256 on the Q2 and the
install through q2update, against a fake adb that keeps the Q2's card and answers q2update as demo would. The Q2's
side is test/fw/test_q2update.py and test/q2update.py."""
import hashlib
import io
import json
import pathlib
import sys
import tarfile

import pytest

import signing
from test_signing import keypair

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]/'tools'))
import q2flash  # noqa: E402

INSTALLED, NEW = 'V0JYi', 'V0JZi'


def update_tar(path, version=NEW, model='Shanling Q2', image=b'k' * 700, rootfs=b'r' * 1500, md5s=None, first=None):
    """An update.tar as tools/build.py writes one (test/fw/test_sw_update.py's shape)."""
    sums = md5s or [hashlib.md5(image).hexdigest(), hashlib.md5(rootfs).hexdigest()]
    info = f'{model}\n{version}\n{sums[0]}  recovery-update/xImage\n{sums[1]}  recovery-update/rootfs.squashfs\n'.encode()
    files = [('firmware_v20.info', info), ('recovery-update/xImage', image), ('recovery-update/rootfs.squashfs', rootfs)]
    if first:
        files = [files[1], files[0], files[2]]
    with tarfile.open(path, 'w', format=tarfile.GNU_FORMAT) as t:
        for name, data in files:
            m = tarfile.TarInfo(name)
            m.size = len(data)
            t.addfile(m, io.BytesIO(data))
    return path


def build_dir(tmp_path, version=NEW, **kw):
    """A build directory: update.tar and the manifest.json naming it."""
    d = tmp_path/'build'
    d.mkdir(exist_ok=True)
    tar = update_tar(d/'update.tar', version, **kw)
    (d/'manifest.json').write_text(json.dumps({'update_sha256': hashlib.sha256(tar.read_bytes()).hexdigest(),
                                               'version': version}))
    return d


def test_a_build_with_its_manifest_is_ours(tmp_path):
    found = q2flash.ours(build_dir(tmp_path))
    assert found['tag'] == NEW and found['source'] == 'build'
    assert found['sha256'] == hashlib.sha256((tmp_path/'build/update.tar').read_bytes()).hexdigest()


def test_a_manifest_naming_another_file_is_not(tmp_path):
    d = build_dir(tmp_path)
    update_tar(d/'update.tar', rootfs=b'x' * 1500)
    with pytest.raises(q2flash.Refused, match='not a build of ours'):
        q2flash.ours(d)


def test_a_release_signed_by_the_release_key_is_ours(tmp_path, monkeypatch):
    pem, pub = keypair(tmp_path)
    monkeypatch.setattr(signing, 'PUBLIC_KEY', pub)
    tar = update_tar(tmp_path/'update.tar', version='V1830')
    (tmp_path/'update.tar.sig').write_bytes(signing.sign(signing.update_message('1.8.3', tar), pem))
    assert q2flash.ours(tar)['source'] == 'release'
    other, _ = keypair(tmp_path, 'other')
    (tmp_path/'update.tar.sig').write_bytes(signing.sign(signing.update_message('1.8.3', tar), other))
    with pytest.raises(q2flash.Refused, match='release key'):
        q2flash.ours(tar)


@pytest.mark.parametrize('kw, why', [(dict(version='V1.36'), 'not a build of ours'),  # stock's own
                                     (dict(model='Shanling Q3'), 'Shanling Q2'),
                                     (dict(md5s=['0' * 32, '0' * 32]), 'damaged'),
                                     (dict(first=True), 'first member')])
def test_stock_and_damaged_files_are_refused(tmp_path, kw, why):
    with pytest.raises(q2flash.Refused, match=why):
        q2flash.ours(build_dir(tmp_path, **kw))


def test_no_file_and_no_tar_are_refused(tmp_path):
    with pytest.raises(q2flash.Refused, match='no update.tar'):
        q2flash.ours(tmp_path)
    (tmp_path/'update.tar').write_bytes(b'not a tar' * 100)
    with pytest.raises(q2flash.Refused):
        q2flash.ours(tmp_path)


@pytest.mark.parametrize('level, status, ok', [(30, 'Discharging', True), (29, 'Discharging', False),
                                               (5, 'Charging', True), (29, 'Full', True), (29, 'Not charging', False),
                                               (29, '', False)])
def test_the_battery_rule(level, status, ok):
    assert q2flash.battery_ok(level, status) == ok


class Q2:
    """A fake adb and the Q2 behind it: its card's files, its battery, and q2update's answers."""
    def __init__(self, level=80, status='Discharging', installed=INSTALLED, q2update=True, check='ok', install='installing',
                 mounted=True, free_kb=4_000_000, corrupt=False):
        self.files, self.level, self.status, self.installed = {}, level, status, installed
        self.q2update, self.answers = q2update, {'check': check, 'install': install}
        self.mounted, self.free_kb, self.corrupt = mounted, free_kb, corrupt
        self.log, self.online, self.after_install = [], True, None
        self.push_fails, self.df, self.versions = False, None, []  # versions: answers to version first, in turn
        self.rename_fails = False  # mv prints nothing and adb drops its status, so a failed rename is silent

    def card_tag(self):
        """The tag of the card's update.tar, as Software Update reads it, or -."""
        data = self.files.get(q2flash.TARGET)
        if data is None:
            return '-'
        with tarfile.open(fileobj=io.BytesIO(data)) as t:
            head = t.extractfile('firmware_v20.info')
            return head.read().decode().splitlines()[1] if head else '-'

    def state(self):
        return 'device' if self.online else ''

    def run(self, *args, timeout=60):
        self.log.append(args)
        if args[0] == 'push':
            if self.push_fails:
                return 1, 'adb: error: failed to copy\n'
            data = pathlib.Path(args[1]).read_bytes()
            self.files[args[2]] = data[:-1] + b'x' if self.corrupt else data
            return 0, '1 file pushed\n'
        raise AssertionError(args)

    def sh(self, command, timeout=60):
        self.log.append(('exec-out', command))
        if command == 'cat /tmp/q2update.ack 2>/dev/null':
            return '1 failed V0JYi V0JZi\n'
        if 'q2update' in command:
            # a refusal exits 1: `q2update op || echo missing` would read it as no q2update (seen in the emulator)
            assert '||' not in command
            if not self.q2update:
                return 'q2update-missing\n'
            op = command.split('q2update ')[-1].split()[0]
            if op == 'version':
                return self.versions.pop(0) if self.versions else f'1 version {self.installed} -\n'
            word, tag = self.answers[op], self.card_tag()
            if word == 'installing':
                self.online = False
                self.installed = self.after_install or tag
            return f'1 {word} {INSTALLED} {tag}\n'
        if command.startswith('cat ' + q2flash.BATTERY):
            return f'{self.level}\n'
        if command.startswith('cat ' + q2flash.STATUS):
            return f'{self.status}\n'
        if command == 'cat /proc/mounts':
            return '/dev/mmcblk0p1 /mnt/mmc vfat rw 0 0\n' if self.mounted else 'rootfs / rootfs rw 0 0\n'
        if command.startswith('df -k') and self.df is not None:
            return self.df
        if command.startswith('df -k'):
            return f'Filesystem 1K-blocks Used Available Use% Mounted on\n/dev/mmcblk0p1 30000000 1 {self.free_kb} 1% /mnt/mmc\n'
        if 'wc -c' in command:
            return f'{len(self.files[q2flash.TARGET])}\n' if q2flash.TARGET in self.files else ''
        if command.startswith('sha256sum '):
            path = command.split()[1]
            return f'{hashlib.sha256(self.files[path]).hexdigest()}  {path}\n' if path in self.files else ''
        if command.startswith('mv -f '):
            src, dst = command.split()[2:4]
            if not self.rename_fails:
                self.files[dst] = self.files.pop(src)
            return ''
        if command.startswith('rm -f '):
            self.files.pop(command.split()[2], None)
            return ''
        raise AssertionError(command)


@pytest.fixture
def online(monkeypatch):
    """The Q2 back on adb once it has gone: wait_for's polls answered at once."""
    def wait_for(adb, gone, seconds):
        adb.online = not gone
        return True
    monkeypatch.setattr(q2flash, 'wait_for', wait_for)


def ops(q2):
    return [c[1].split('q2update ')[-1].split()[0] for c in q2.log if c[0] == 'exec-out' and 'q2update ' in c[1]]


def test_install_pushes_checks_the_copy_and_asks_the_q2s_own_update(tmp_path, online, capsys):
    q2 = Q2()
    assert q2flash.flash(build_dir(tmp_path), q2) == 0
    assert q2.files == {q2flash.TARGET: (tmp_path/'build/update.tar').read_bytes()}
    pushes = [c for c in q2.log if c[0] == 'push']
    assert pushes == [('push', str(tmp_path/'build/update.tar'), q2flash.PART)]
    assert ops(q2) == ['version', 'check', 'install', 'version'] and q2.installed == NEW
    assert 'installed: the Q2 runs V0JZi' in capsys.readouterr().out


def test_the_dry_run_does_everything_but_install(tmp_path, capsys):
    q2 = Q2()
    assert q2flash.flash(build_dir(tmp_path), q2, dry_run=True) == 0
    assert q2flash.TARGET in q2.files and ops(q2) == ['version', 'check'] and q2.online and q2.installed == INSTALLED
    assert 'nothing installed' in capsys.readouterr().out


@pytest.mark.parametrize('kw, why', [(dict(level=29), 'battery 29% and not charging'),
                                     (dict(installed=NEW), 'already runs'),
                                     (dict(mounted=False), 'not mounted'),
                                     (dict(free_kb=1000), 'not enough room')])
def test_refused_before_anything_is_copied(tmp_path, kw, why):
    q2 = Q2(**kw)
    with pytest.raises(q2flash.Refused, match=why):
        q2flash.flash(build_dir(tmp_path), q2)
    assert not [c for c in q2.log if c[0] == 'push'] and not q2.files and ops(q2) == ['version']


def test_a_low_battery_on_a_charger_goes_ahead(tmp_path):
    assert q2flash.flash(build_dir(tmp_path), Q2(level=10, status='Charging'), dry_run=True) == 0


def test_a_file_not_ours_never_reaches_adb(tmp_path):
    q2 = Q2()
    with pytest.raises(q2flash.Refused):
        q2flash.flash(build_dir(tmp_path, version='V1.36'), q2)
    assert not q2.log


def test_a_copy_that_differs_is_removed_and_never_installed(tmp_path):
    q2 = Q2(corrupt=True)
    with pytest.raises(q2flash.Refused, match='differs'):
        q2flash.flash(build_dir(tmp_path), q2)
    assert not q2.files and ops(q2) == ['version']


def test_a_failed_rename_never_installs_the_older_file(tmp_path, online):
    q2 = Q2()
    older = update_tar(tmp_path/'older.tar', version='V0JXi')
    q2.files[q2flash.TARGET] = older.read_bytes()  # an earlier flash's update.tar, still on the card
    q2.rename_fails = True  # the card remounted read-only between the push and the rename
    with pytest.raises(q2flash.Refused, match='rename'):
        q2flash.flash(build_dir(tmp_path), q2)
    assert 'install' not in ops(q2) and q2.installed == INSTALLED


@pytest.mark.parametrize('tags, why', [(['V0JXi'], 'reads /mnt/mmc/update.tar as V0JXi'),
                                       ([NEW, 'V0JXi'], 'is installing V0JXi, not V0JZi')])
def test_an_answer_naming_another_file_stops_the_install(tmp_path, monkeypatch, tags, why):
    q2 = Q2()
    said = iter(tags)
    monkeypatch.setattr(q2, 'card_tag', lambda: next(said))  # the file the Q2's check and install each read
    with pytest.raises(q2flash.Refused, match=why):
        q2flash.flash(build_dir(tmp_path), q2)
    assert ops(q2)[:2] == ['version', 'check'] and ('install' in ops(q2)) == (len(tags) > 1)


def test_the_q2s_own_refusal_is_said(tmp_path):
    with pytest.raises(q2flash.Refused, match='refused it: 1 bad'):
        q2flash.flash(build_dir(tmp_path), Q2(check='bad'), dry_run=True)
    with pytest.raises(q2flash.Refused, match='did not start the install: 1 low'):
        q2flash.flash(build_dir(tmp_path), Q2(install='low'))


def test_a_build_without_q2update_is_pushed_and_left_for_a_press(tmp_path, capsys):
    q2 = Q2(q2update=False)
    assert q2flash.flash(build_dir(tmp_path), q2) == 3
    assert q2flash.TARGET in q2.files and 'Software Update' in capsys.readouterr().out


def test_back_on_another_build_is_a_failure(tmp_path, online):
    q2 = Q2()
    q2.after_install = INSTALLED  # the recovery did not take it
    with pytest.raises(q2flash.Refused, match='running V0JYi, not V0JZi'):
        q2flash.flash(build_dir(tmp_path), q2)


def test_main_says_refused_and_exits_1(tmp_path, capsys):
    assert q2flash.main([str(tmp_path)]) == 1
    assert 'q2flash: refused: no update.tar' in capsys.readouterr().err


def test_adb_runs_exec_out_with_the_serial(monkeypatch):
    runs = []

    class Done:
        returncode, stdout = 0, 'x\n'
    monkeypatch.setattr(q2flash.subprocess, 'run', lambda args, **kw: runs.append(args) or Done())
    assert q2flash.Adb('Q2CODA').sh('id') == 'x\n' and q2flash.Adb().sh('id') == 'x\n'
    assert runs == [['adb', '-s', 'Q2CODA', 'exec-out', 'id'], ['adb', 'exec-out', 'id']]


# Every refusal and wait, for the post tier's coverage floor (pyproject.toml): each path q2flash can take.

def tar_with(path, members):
    """A tar of members [(name, bytes or None for a directory)], firmware_v20.info's lines MD5s of nothing."""
    with tarfile.open(path, 'w', format=tarfile.GNU_FORMAT) as t:
        for name, data in members:
            m = tarfile.TarInfo(name)
            if data is None:
                m.type = tarfile.DIRTYPE
                t.addfile(m)
            else:
                m.size = len(data)
                t.addfile(m, io.BytesIO(data))
    return path


INFO = f'Shanling Q2\n{NEW}\n{hashlib.md5(b"k").hexdigest()}  x\n{hashlib.md5(b"r").hexdigest()}  y\n'.encode()


@pytest.mark.parametrize('members, why', [
    ([('firmware_v20.info', None)], 'firmware_v20.info is not a file'),
    ([('firmware_v20.info', INFO), ('recovery-update/rootfs.squashfs', b'r')], 'no recovery-update/xImage'),
    ([('firmware_v20.info', INFO), ('recovery-update/xImage', None)], 'xImage is not a file'),
    ([('firmware_v20.info', b'Shanling Q2\n')], 'Shanling Q2'),  # too short
])
def test_a_tar_shaped_wrong_is_refused(tmp_path, members, why):
    with pytest.raises(q2flash.Refused, match=why):
        q2flash.ours(tar_with(tmp_path/'update.tar', members))


def test_a_manifest_that_is_not_json_is_no_manifest(tmp_path):
    d = build_dir(tmp_path)
    (d/'manifest.json').write_text('{not json')
    with pytest.raises(q2flash.Refused, match='not a build of ours'):
        q2flash.ours(d)


def test_no_q2_on_adb(tmp_path):
    q2 = Q2()
    q2.online = False
    with pytest.raises(q2flash.Refused, match='no Q2 on adb'):
        q2flash.flash(build_dir(tmp_path), q2)


def test_demo_not_answering_version(tmp_path):
    q2 = Q2()
    q2.versions = ['\n']  # q2update's own complaint goes to stderr
    with pytest.raises(q2flash.Refused, match='gave no answer'):
        q2flash.flash(build_dir(tmp_path), q2)


@pytest.mark.parametrize('kw, why', [(dict(level='?'), 'cannot read the battery'),
                                     (dict(df='df: /mnt/mmc: no such file\n'), "cannot read the card's free space")])
def test_what_cannot_be_read_is_refused(tmp_path, kw, why):
    q2 = Q2(level=kw.get('level', 80))
    q2.df = kw.get('df')
    with pytest.raises(q2flash.Refused, match=why):
        q2flash.flash(build_dir(tmp_path), q2)
    assert not q2.files


def test_a_failed_push_leaves_nothing(tmp_path):
    q2 = Q2()
    q2.push_fails = True
    with pytest.raises(q2flash.Refused, match='adb push failed: adb: error: failed to copy'):
        q2flash.flash(build_dir(tmp_path), q2)
    assert not q2.files and ('exec-out', f'rm -f {q2flash.PART}') in q2.log


def test_no_wait_returns_once_installing(tmp_path):
    q2 = Q2()
    assert q2flash.flash(build_dir(tmp_path), q2, wait=False) == 0 and ops(q2) == ['version', 'check', 'install']


def clock(monkeypatch, step=1.0):
    """time.monotonic moving step seconds a call (conftest makes sleep return at once)."""
    now = [0.0]
    def tick():
        now[0] += step
        return now[0]
    monkeypatch.setattr(q2flash.time, 'monotonic', tick)


def test_wait_for_sees_the_q2_go_and_come_back(monkeypatch):
    clock(monkeypatch)
    q2 = Q2()
    states = iter(['device', 'device', ''])
    monkeypatch.setattr(q2, 'state', lambda: next(states))
    assert q2flash.wait_for(q2, True, 10)
    states = iter(['', 'device'])
    assert q2flash.wait_for(q2, False, 10)


def test_wait_for_gives_up_at_its_limit(monkeypatch):
    clock(monkeypatch)
    assert not q2flash.wait_for(Q2(), True, 5)


def test_a_q2_that_never_restarts_says_its_last_answer(tmp_path, monkeypatch):
    q2 = Q2()
    monkeypatch.setattr(q2flash, 'wait_for', lambda adb, gone, seconds: False)
    with pytest.raises(q2flash.Refused, match=r'did not restart within 120 s \(last answer: 1 failed V0JYi V0JZi\)'):
        q2flash.flash(build_dir(tmp_path), q2)


def test_a_q2_that_never_comes_back(tmp_path, monkeypatch):
    monkeypatch.setattr(q2flash, 'wait_for', lambda adb, gone, seconds: gone)
    with pytest.raises(q2flash.Refused, match='not back on adb within 900 s'):
        q2flash.flash(build_dir(tmp_path), Q2())


def test_back_demo_answers_once_it_has_started(tmp_path, online):
    q2 = Q2()
    q2.versions = [f'1 version {INSTALLED} -\n', '\n', '\n']  # before the install, then twice while demo starts
    assert q2flash.flash(build_dir(tmp_path), q2) == 0 and ops(q2).count('version') == 4


def test_back_but_demo_never_answers(tmp_path, online):
    q2 = Q2()
    q2.versions = [f'1 version {INSTALLED} -\n'] + ['\n'] * 30
    with pytest.raises(q2flash.Refused, match=r'running \(no answer\), not V0JZi'):
        q2flash.flash(build_dir(tmp_path), q2)


class Run:
    """subprocess.run's stand-in for Adb: the answers by the command's words after adb's serial, or an exception."""
    def __init__(self, answers):
        self.answers, self.args = answers, []

    def __call__(self, args, **kw):
        self.args.append(args)
        key = ' '.join(args[1:])
        answer = self.answers.get(key, (0, ''))
        if isinstance(answer, BaseException):
            raise answer

        class Done:
            returncode, stdout = answer
        return Done()


def test_adb_state_that_times_out_is_no_device(monkeypatch):
    monkeypatch.setattr(q2flash.subprocess, 'run', Run({'get-state': q2flash.subprocess.TimeoutExpired('adb', 10)}))
    assert q2flash.Adb().state() == ''
    monkeypatch.setattr(q2flash.subprocess, 'run', Run({'get-state': (0, 'device\n')}))
    assert q2flash.Adb().state() == 'device'


def test_main_with_no_adb_says_so(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(q2flash.subprocess, 'run', Run({'get-state': FileNotFoundError(2, 'No such file', 'adb')}))
    monkeypatch.setattr(q2flash.Adb, 'state', lambda self: self.run('get-state')[1])
    assert q2flash.main([str(build_dir(tmp_path)), '--adb', '/nowhere/adb']) == 1
    assert 'q2flash: adb:' in capsys.readouterr().err


def test_main_passes_its_options_through(tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr(q2flash, 'flash', lambda update, adb, dry, minimum, wait: seen.append((update, adb.base, dry, minimum, wait)) or 3)
    assert q2flash.main([str(tmp_path), '--dry-run', '-s', 'Q2CODA', '--min-battery', '50', '--no-wait', '--adb', 'my-adb']) == 3
    assert seen == [(str(tmp_path), ['my-adb', '-s', 'Q2CODA'], True, 50, False)]
