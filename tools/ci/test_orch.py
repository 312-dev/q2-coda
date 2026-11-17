"""Tests of the orchestration tools: python3 -m pytest tools/ci -q. Fakes stand in for git, the suites and
the clock where a real one would take minutes; the hooks and the scripts run for real on scratch files."""
import json
import os
import pathlib
import subprocess
import sys
import time
import types

import pytest

CI = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(CI))
import fw  # noqa: E402


# --- fw.py: the sanitizers in fresh processes ---

def test_asan_groups_hold_every_file_once_and_share_the_large_ones(tmp_path):
    files = []
    for k in range(20):
        f = tmp_path/f'test_{k:02d}.py'
        f.write_text('x' * (1000 * (k + 1)))
        files.append(f)
    groups = fw.chunks(files, 8)
    assert sorted(f for g in groups for f in g) == sorted(files) and len(groups) == 8
    largest = sorted(files, key=lambda f: -f.stat().st_size)[:8]
    assert all(sum(f in g for f in largest) == 1 for g in groups)  # the eight largest: one to a group
    assert len(fw.chunks(files[:3], 8)) == 3  # fewer files than groups: no empty group


def test_asan_run_reports_a_crashed_group_ahead_of_a_failed_one(tmp_path, monkeypatch):
    for k in range(4):
        (tmp_path/f'test_{k}.py').write_text('x' * (k + 1))
    monkeypatch.setattr(fw, 'ROOT', tmp_path)
    monkeypatch.setattr(fw, 'HEAVY', tmp_path/'none')
    args = types.SimpleNamespace(paths=['.'], n=2, python='py', basetemp=str(tmp_path/'bt'))
    codes = {'test_3.py': 1, 'test_1.py': -11}
    seen = []

    def run(cmd, **kw):
        seen.append(cmd)
        return types.SimpleNamespace(returncode=codes.get(pathlib.Path(cmd[-1]).name, 0), stdout='1 passed\n', stderr='')
    assert fw.run_asan(args, [], {}, 'lib.so', run=run, log=lambda *a, **k: None) == -11
    assert len(seen) == 4 and len({c[c.index('--basetemp') + 1] for c in seen}) == 4  # a temp dir each
    codes.clear()
    assert fw.run_asan(args, [], {}, 'lib.so', run=run, log=lambda *a, **k: None) == 0


# --- gate.py: known failures, crashes, the second try, the record of passes ---

import datetime  # noqa: E402

import gate  # noqa: E402

DAY = datetime.date(2026, 11, 16)


def test_known_failures_deselect_until_their_date(tmp_path):
    f = tmp_path/'known.tsv'
    f.write_text('# comment\n'
                 'unit\ttest/unit/test_a.py::test_x\t2026-11-18\tgray\tflaky clock\n'
                 'fw\ttest/fw/test_b.py::test_y\t2026-11-16\tgray\towed fix\n')
    skip, wrong = gate.known_failures(f, DAY)
    assert wrong == []
    assert skip == {'unit': ['test/unit/test_a.py::test_x'], 'unit-cov': ['test/unit/test_a.py::test_x'],
                    'fw': ['test/fw/test_b.py::test_y'], 'fw-asan': ['test/fw/test_b.py::test_y']}
    skip, wrong = gate.known_failures(f, DAY + datetime.timedelta(days=1))  # the fw line's date has passed
    assert 'fw' not in skip and len(wrong) == 1 and 'expired on 2026-11-16' in wrong[0]


@pytest.mark.parametrize('line, says', [
    ('patch\ttest/patch.py::x\t2026-12-01\tgray\tr', 'cannot skip a test'),
    ('unit\ttest_a\t2026-12-01\tgray\tr', 'not a pytest id'),
    ('unit\ttest/unit/test_a.py::t\tsoon\tgray\tr', 'not a date'),
    ('unit\ttest/unit/test_a.py::t\t2026-12-01\tgray', 'five tab-separated fields'),
])
def test_known_failures_refuses_a_line_it_cannot_honour(tmp_path, line, says):
    f = tmp_path/'known.tsv'
    f.write_text(line + '\n')
    skip, wrong = gate.known_failures(f, DAY)
    assert skip == {} and says in wrong[0]


def test_the_repository_known_failures_file_is_valid():
    assert gate.known_failures(gate.KNOWN, datetime.date.today())[1] == []


def test_a_death_is_a_crash_and_a_verdict_is_a_failure():
    assert gate.classify(-11, '') == 'crash' and gate.classify(139, '') == 'crash' and gate.classify(137, '') == 'crash'
    assert gate.classify(1, '3 failed, 10 passed') == 'fail'
    assert gate.classify(1, 'shard 2: CRASHED (exit -11)') == 'crash'
    assert gate.classify(1, 'Fatal Python error: Segmentation fault') == 'crash'


def script_suite(name, tmp_path, body):
    script = tmp_path/f'{name}.sh'
    script.write_text(body)
    return gate.Suite(name, ['bash', str(script)])


def test_a_suite_that_fails_once_is_run_again_alone_and_counts_as_flaky(tmp_path):
    marker = tmp_path/'ran'
    flaky = script_suite('flaky', tmp_path, f'if [ -e {marker} ]; then exit 0; fi; touch {marker}; kill -SEGV $$\n')
    slow = script_suite('slow', tmp_path, 'sleep 0.4\n')
    events = []
    flakes = tmp_path/'flakes.jsonl'
    r = gate.run({'flaky': flaky, 'slow': slow}, [], tmp_path/'logs', sys.executable, build='B', log=events.append,
                 mem=lambda: 12.0, poll=0.02, retry=True, flakes=flakes)
    assert r['flaky']['rc'] == 0 and r['flaky']['flaky'] == 'crash' and r['slow']['rc'] == 0
    starts = [e for e in events if e.startswith(('start', 'ok', 'FAIL'))]
    # the second try starts only after slow has ended: nothing runs beside it
    assert starts.index('start flaky: bash ' + str(tmp_path/'flaky.sh'), 1) > next(
        i for i, e in enumerate(starts) if e.startswith('ok   slow'))
    assert json.loads(flakes.read_text())['suite'] == 'flaky'
    assert (tmp_path/'logs/flaky.first.log').exists()


def test_a_suite_that_fails_twice_fails_with_its_kind(tmp_path):
    bad = script_suite('bad', tmp_path, 'echo "1 failed"; exit 1\n')
    r = gate.run({'bad': bad}, [], tmp_path/'logs', sys.executable, build='B', log=lambda m: None, mem=lambda: 12.0,
                 poll=0.02, retry=True)
    assert r['bad']['rc'] == 1 and r['bad']['kind'] == 'fail' and 'flaky' not in r['bad']
    assert '(fail)' in gate.table(r, 1)


def test_a_pass_is_recorded_for_its_tree_and_not_run_again(tmp_path, monkeypatch):
    monkeypatch.setattr(gate, 'VERIFIED', tmp_path/'verified')
    count = tmp_path/'count'

    def suite():
        return script_suite('s', tmp_path, f'echo x >> {count}\n')
    kw = dict(build='B', log=lambda m: None, mem=lambda: 12.0, poll=0.02)
    first = gate.run({'s': suite()}, ['--dev'], tmp_path/'l1', sys.executable, tree='tree1', **kw)
    again = gate.run({'s': suite()}, ['--dev'], tmp_path/'l2', sys.executable, tree='tree1', **kw)
    assert first['s']['rc'] == 0 and 'cached' not in first['s'] and again['s']['cached'] and again['s']['rc'] == 0
    assert count.read_text().count('x') == 1
    gate.run({'s': suite()}, ['--dev'], tmp_path/'l3', sys.executable, tree='tree2', **kw)       # another tree
    gate.run({'s': suite()}, ['--release'], tmp_path/'l4', sys.executable, tree='tree1', **kw)  # another build
    gate.run({'s': suite()}, ['--dev'], tmp_path/'l5', sys.executable, tree=None, **kw)          # a dirty worktree
    assert count.read_text().count('x') == 4


def test_a_failure_and_a_flaky_pass_are_never_recorded(tmp_path, monkeypatch):
    monkeypatch.setattr(gate, 'VERIFIED', tmp_path/'verified')
    marker = tmp_path/'ran'
    flaky = script_suite('flaky', tmp_path, f'if [ -e {marker} ]; then exit 0; fi; touch {marker}; exit 1\n')
    bad = script_suite('bad', tmp_path, 'exit 1\n')
    gate.run({'flaky': flaky, 'bad': bad}, [], tmp_path/'logs', sys.executable, build='B', log=lambda m: None,
             mem=lambda: 12.0, poll=0.02, retry=True, tree='t')
    assert not list((tmp_path/'verified').glob('*.json'))


def test_the_tree_key_needs_a_worktree_with_nothing_else_in_it(tmp_path):
    def git(*a):
        subprocess.run(['git', '-C', str(tmp_path), '-c', 'user.name=t', '-c', 'user.email=t@t', *a], check=True,
                       capture_output=True)
    git('init', '-q')
    (tmp_path/'a').write_text('1')
    git('add', 'a')
    git('commit', '-q', '-m', 'a')
    key = gate.tree_key(tmp_path)
    assert key and len(key) == 40
    (tmp_path/'b').write_text('untracked')
    assert gate.tree_key(tmp_path) is None
    (tmp_path/'b').unlink()
    (tmp_path/'a').write_text('2')
    assert gate.tree_key(tmp_path) is None


# --- merge_batch.py: crashes, the ready list, conflicts ---

import merge_batch  # noqa: E402
import q2ci  # noqa: E402


class CrashBatch:
    """integrate's ops where suites end as told: {suite: kind} for every commit but the tip."""
    def __init__(self, ends):
        self.ends, self.runs = ends, []

    def assemble(self, level, base, branches):
        return base + tuple(branches), list(branches), {}

    def test(self, level, head, suites, tag):
        self.runs.append(head)
        return {s: merge_batch.Suite(s not in self.ends or not head, f'{s}.log', [], self.ends.get(s, 'fail'))
                for s in (suites or ['unit', 'fw', 'patch'])}


def test_a_batch_whose_suites_only_crashed_blames_nobody_and_is_not_bisected():
    ops = CrashBatch({'patch': 'crash'})
    r = merge_batch.integrate(ops, (), ['a', 'b', 'c'], log=lambda m: None)
    assert r.infra == ['patch'] and not r.culprits and not r.landed and r.head is None
    assert len(ops.runs) == 1  # no tip check, no halves
    assert set(r.skipped) == {'a', 'b', 'c'}


def test_a_crash_beside_a_real_failure_is_still_bisected():
    ops = CrashBatch({'patch': 'crash', 'unit': 'fail'})
    r = merge_batch.integrate(ops, (), ['a'], log=lambda m: None)
    assert not r.infra and list(r.culprits) == ['a']


def test_a_red_tip_is_reported_as_one():
    sys.path.insert(0, str(CI))
    import test_ci
    r = merge_batch.integrate(test_ci.FakeBatch(red_tip={'patch'}), (), ['a', 'b'], log=lambda m: None)
    assert r.tip_red == ['patch'] and not r.landed


def test_ready_lines_carry_the_fix_mark(tmp_path):
    ready = tmp_path/'ready.txt'
    merge_batch.append_ready(ready, [('feat/a', 'abc'), ('fix/tip', 'def', True), ('feat/b', None)])
    assert merge_batch.read_ready_lines(ready) == [('feat/a', 'abc', False), ('fix/tip', 'def', True), ('feat/b', None, False)]
    assert merge_batch.read_ready(ready) == [('feat/a', 'abc'), ('fix/tip', 'def'), ('feat/b', None)]


class Repo:
    """A scratch repository with a trunk and helpers to grow branches on it."""
    def __init__(self, path, trunk='release/t'):
        self.path, self.trunk = path, trunk
        path.mkdir(parents=True, exist_ok=True)
        self.git('init', '-q', '-b', trunk)
        self.write('base.txt', 'one\ntwo\nthree\n', 'base')

    def git(self, *a, check=True, env=None):
        return subprocess.run(['git', '-C', str(self.path), '-c', 'user.name=t', '-c', 'user.email=t@t', *a],
                              capture_output=True, text=True, check=check, env={**os.environ, **(env or {})})

    def write(self, name, text, message):
        (self.path/name).parent.mkdir(parents=True, exist_ok=True)
        (self.path/name).write_text(text)
        self.git('add', name)
        self.git('commit', '-q', '-m', message)
        return self.git('rev-parse', 'HEAD').stdout.strip()

    def branch(self, name, file, text, base=None):
        self.git('checkout', '-q', '-b', name, base or self.trunk)
        sha = self.write(file, text, name)
        self.git('checkout', '-q', self.trunk)
        return sha


def test_mark_ready_refuses_a_branch_that_conflicts_with_the_trunk(tmp_path, monkeypatch):
    monkeypatch.setenv('Q2_QUEUE_DIR', str(tmp_path/'q'))
    repo = Repo(tmp_path/'r')
    repo.branch('feat/clean', 'new.txt', 'x\n')
    repo.branch('feat/clash', 'base.txt', 'one\nTWO by the branch\nthree\n')
    repo.write('base.txt', 'one\nTWO by the trunk\nthree\n', 'trunk moves')
    ready = tmp_path/'ready.txt'
    assert merge_batch.mark_ready(ready, 'feat/clean', repo.trunk, cwd=repo.path) == 0
    with pytest.raises(SystemExit) as e:
        merge_batch.mark_ready(ready, 'feat/clash', repo.trunk, cwd=repo.path)
    assert 'conflicts with release/t' in str(e.value) and 'base.txt' in str(e.value)
    with pytest.raises(SystemExit) as e:  # the trunk itself, and a branch already in it
        merge_batch.mark_ready(ready, repo.trunk, repo.trunk, cwd=repo.path)
    assert 'integration branch' in str(e.value)
    repo.git('merge', '-q', '--no-edit', 'feat/clean')
    with pytest.raises(SystemExit) as e:
        merge_batch.mark_ready(ready, 'feat/clean', repo.trunk, cwd=repo.path)
    assert 'already' in str(e.value)
    assert [b for b, _ in merge_batch.read_ready(ready)] == ['feat/clean']
    assert 'READY feat/clean' in (tmp_path/'q/events.log').read_text()


def test_the_trunk_name_comes_from_the_environment_then_the_config_file(tmp_path, monkeypatch):
    monkeypatch.setenv('Q2_CONFIG', str(tmp_path))
    monkeypatch.delenv('Q2_TRUNK', raising=False)
    assert q2ci.trunk() == q2ci.DEFAULT_TRUNK
    (tmp_path/'trunk').write_text('release/p32\n')
    assert q2ci.trunk() == 'release/p32'
    monkeypatch.setenv('Q2_TRUNK', 'main')
    assert q2ci.trunk() == 'main'


# --- the trunk's guard (tools/ci/hooks/reference-transaction) ---

def guarded(tmp_path):
    repo = Repo(tmp_path/'r')
    hook = repo.path/'.git/hooks/reference-transaction'
    hook.write_text((CI/'hooks/reference-transaction').read_text())
    hook.chmod(0o755)
    repo.git('config', 'core.hooksPath', str(hook.parent))  # a global core.hooksPath would hide the repository's
    return repo


def test_the_trunk_moves_only_for_the_queue(tmp_path, monkeypatch):
    monkeypatch.delenv('Q2_QUEUE', raising=False)
    monkeypatch.delenv('Q2_TRUNK_ADMIN', raising=False)
    repo = guarded(tmp_path)
    tip = repo.git('rev-parse', 'HEAD').stdout.strip()
    (repo.path/'x').write_text('x')
    repo.git('add', 'x')
    refused = repo.git('commit', '-q', '-m', 'on the trunk', check=False)
    assert refused.returncode != 0 and 'moves only through the merge queue' in refused.stderr
    assert repo.git('rev-parse', 'HEAD').stdout.strip() == tip
    repo.git('reset', '-q', '--hard')
    # a feature branch is free, and so is everything that is not the trunk
    repo.git('checkout', '-q', '-b', 'feat/a')
    sha = repo.write('a.txt', 'a\n', 'a')
    repo.git('branch', 'train/x', sha)
    repo.git('checkout', '-q', repo.trunk)
    for cmd in (['merge', '-q', '--ff-only', 'feat/a'], ['branch', '-f', repo.trunk, sha], ['reset', '-q', '--hard', sha],
                ['branch', 'release/other', sha], ['update-ref', '-d', f'refs/heads/{repo.trunk}']):
        assert repo.git(*cmd, check=False).returncode != 0, cmd
        assert repo.git('rev-parse', repo.trunk).stdout.strip() == tip, cmd
    assert repo.git('merge', '-q', '--ff-only', 'feat/a', env={'Q2_QUEUE': '1'}).returncode == 0
    assert repo.git('rev-parse', repo.trunk).stdout.strip() == sha
    assert repo.git('branch', 'release/next', sha, env={'Q2_TRUNK_ADMIN': '1'}).returncode == 0
    # housekeeping that rewrites how refs are stored, not where they point, still works
    assert repo.git('pack-refs', '--all', check=False).returncode == 0
    assert repo.git('gc', '-q', check=False).returncode == 0
    assert repo.git('rev-parse', repo.trunk).stdout.strip() == sha


# --- merge_queue.py ---

import merge_queue as q2queue  # noqa: E402


class FakeQueue(q2queue.Queue):
    """A queue whose batches end as scripted: outcomes is a list of functions (names) -> result dict or None."""
    def __init__(self, tmp_path, outcomes, repo=None, **kw):
        self.repo = repo or Repo(tmp_path/'r')
        self.lines = []
        super().__init__(self.repo.path, self.repo.trunk, tmp_path/'q', tmp_path/'ready.txt', log=self.lines.append, **kw)
        self.outcomes, self.batches, self.posts = list(outcomes), [], 0

    def run_batch(self, batch, ready):
        names = [b for b, _ in merge_batch.take_ready(ready, batch/'ready.taken')]
        self.batches.append(names)
        result = self.outcomes.pop(0)(names)
        if result is not None:
            base = {'target': self.trunk, 'tip': 'a' * 40, 'head': 'b' * 40, 'status': 0, 'logs': str(batch),
                    'landed': {}, 'skipped': {}, 'infra': [], 'tip_red': [], 'tests': 1, 'shas': {}, 'culprits': {}}
            q2ci.write_json(batch/'result.json', {**base, **result})
        return 0

    def run_post(self, logs):
        self.posts += 1
        return 0, []

    def events(self):
        return [' '.join(line.split()[1:3]) for line in (self.dir/'events.log').read_text().splitlines()]


def land(names):
    return {'landed': {n: 'c' * 40 for n in names}}


def test_the_queue_takes_everything_ready_and_lands_it(tmp_path):
    q = FakeQueue(tmp_path, [land])
    merge_batch.append_ready(q.ready, [('feat/a', 'a1'), ('feat/b', 'b1'), ('feat/c', 'c1')])
    assert q.turn() == 'batch' and q.batches == [['feat/a', 'feat/b', 'feat/c']]
    assert q.events() == ['BATCH 3', 'LANDED feat/a', 'LANDED feat/b', 'LANDED feat/c']
    assert merge_batch.read_ready(q.ready) == []
    status = json.loads((q.dir/'status.json').read_text())
    assert status['state'] == 'idle' and status['last_landed']['branches'] == ['feat/a', 'feat/b', 'feat/c']
    assert q.turn() == 'post' and q.turn() == 'idle' and q.posts == 1  # the post tier once for a tip


def test_a_culprit_is_rejected_a_conflict_bounced_and_the_rest_land(tmp_path):
    def outcome(names):
        return {'landed': {'feat/a': 'c' * 40},
                'culprits': {'feat/bad': {'unit': {'log': '/l/unit.log', 'failures': ['FAILED test_x.py::t - boom']}}},
                'skipped': {'feat/clash': 'conflicts rerere could not resolve, in docs/internals.md'}}
    q = FakeQueue(tmp_path, [outcome])
    merge_batch.append_ready(q.ready, [('feat/a', 'a1'), ('feat/bad', 'b1'), ('feat/clash', 'c1')])
    q.turn()
    log = (q.dir/'events.log').read_text()
    assert 'LANDED feat/a' in log and 'CONFLICT feat/clash' in log
    assert 'REJECTED feat/bad suite=unit log=/l/unit.log first=FAILED_test_x.py::t_-_boom' in log
    assert merge_batch.read_ready(q.ready) == []  # neither goes round again: each has an owner to fix it


def test_a_red_tip_holds_everything_but_its_fix(tmp_path):
    def red(names):
        return {'tip_red': ['patch'], 'status': 2,
                'skipped': {n: 'not landed: the integration tip fails the same suites' for n in names}}
    q = FakeQueue(tmp_path, [red, land, land], post=False)
    merge_batch.append_ready(q.ready, [('feat/a', 'a1'), ('feat/b', 'b1')])
    q.turn()
    assert 'TIP_RED patch' in q.events() and q.state['tip_red'] == ['patch']
    assert [b for b, _ in merge_batch.read_ready(q.ready)] == ['feat/a', 'feat/b']  # waiting, not lost
    assert q.turn() == 'idle' and len(q.batches) == 1                               # and not tried again
    merge_batch.append_ready(q.ready, [('fix/tip', 'f1', True)])
    assert q.turn() == 'batch' and q.batches[-1] == ['fix/tip'] and q.state['tip_red'] == []
    assert q.turn() == 'batch' and q.batches[-1] == ['feat/a', 'feat/b']


def test_two_batches_that_only_crash_stop_the_queue_and_keep_the_branches(tmp_path):
    def crash(names):
        return {'infra': ['patch'], 'status': 2, 'skipped': {n: 'not landed: patch crashed (the harness or the machine)' for n in names}}
    q = FakeQueue(tmp_path, [crash, crash, land], post=False)
    merge_batch.append_ready(q.ready, [('feat/a', 'a1')])
    assert q.turn() == 'batch' and not q.stopped()
    assert q.turn() == 'batch' and q.stopped()
    assert q.turn() == 'stopped' and len(q.batches) == 2
    assert [b for b, _ in merge_batch.read_ready(q.ready)] == ['feat/a']
    assert 'QUEUE_STOPPED' in ' '.join(q.events()) and 'REJECTED' not in ' '.join(q.events())
    q2queue.resume(q.dir)
    q2 = FakeQueue(tmp_path, [land], repo=q.repo, post=False)  # a restart reads the state back
    assert q2.state['crashes'] == 0 and q2.turn() == 'batch' and 'LANDED feat/a' in q2.events()


def test_a_batch_that_dies_without_a_result_loses_no_branch(tmp_path):
    q = FakeQueue(tmp_path, [lambda names: None, land], post=False)
    merge_batch.append_ready(q.ready, [('feat/a', 'a1'), ('fix/t', 't1', True)])
    q.turn()
    assert merge_batch.read_ready_lines(q.ready) == [('feat/a', 'a1', False), ('fix/t', 't1', True)]
    assert 'QUEUE_ERROR the' in q.events()
    q.turn()
    assert 'LANDED feat/a' in q.events()


def test_an_undecided_failure_is_tried_once_more_then_rejected(tmp_path):
    def undecided(names):
        return {'status': 2, 'skipped': {n: 'not landed: the batch failed and no culprit was found' for n in names}}
    q = FakeQueue(tmp_path, [undecided, undecided], post=False)
    merge_batch.append_ready(q.ready, [('feat/a', 'a1')])
    q.turn()
    assert [b for b, _ in merge_batch.read_ready(q.ready)] == ['feat/a']
    q.turn()
    assert merge_batch.read_ready(q.ready) == [] and 'REJECTED feat/a' in q.events()


def test_the_queue_refuses_a_worktree_that_is_not_the_clean_trunk(tmp_path):
    q = FakeQueue(tmp_path, [land])
    merge_batch.append_ready(q.ready, [('feat/a', 'a1')])
    (q.repo.path/'base.txt').write_text('edited\n')
    assert q.turn() == 'error' and q.turn() == 'error' and q.batches == []
    assert q.events() == ['QUEUE_ERROR ' + str(q.repo.path)]  # said once, not every turn
    q.repo.git('checkout', '-q', '--', 'base.txt')
    assert q.turn() == 'batch'


def test_a_stop_file_stops_a_running_batch_and_what_it_started(tmp_path):
    repo = Repo(tmp_path/'r')
    q = q2queue.Queue(repo.path, repo.trunk, tmp_path/'q', tmp_path/'ready.txt', post=False, log=lambda m: None)
    pidfile = tmp_path/'grandchild'
    child = subprocess.Popen(['bash', '-c', f'sleep 60 & echo $! > {pidfile}; wait'], start_new_session=True)
    while not (pidfile.exists() and pidfile.read_text().strip()):
        time.sleep(0.02)
    (q.dir/'STOP').parent.mkdir(parents=True, exist_ok=True)
    (q.dir/'STOP').write_text('test\n')
    t = time.monotonic()
    q.wait(child)
    assert time.monotonic() - t < 10 and child.poll() is not None
    pid = int(pidfile.read_text())
    for _ in range(100):
        if not pathlib.Path(f'/proc/{pid}').exists():
            return
        time.sleep(0.02)
    raise AssertionError(f'the batch\'s sleep ({pid}) outlived the stop')
