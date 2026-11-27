"""Tests of the orchestration tools: python3 -m pytest tools/ci -q. Fakes stand in for git, the suites and
the clock where a real one would take minutes; the hooks and the scripts run for real on scratch files."""
import json
import os
import pathlib
import shutil
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


def test_asan_run_logs_why_a_group_died(tmp_path, monkeypatch):
    """A sanitizer's report goes to pytest's capture of the test that tripped it and dies with the process:
    the group's log gets it from log_path's file, and a signal by name (post tier, 2026-11-18)."""
    for k in range(2):
        (tmp_path/f'test_{k}.py').write_text('x' * (k + 1))
    monkeypatch.setattr(fw, 'ROOT', tmp_path)
    monkeypatch.setattr(fw, 'HEAVY', tmp_path/'none')
    args = types.SimpleNamespace(paths=['.'], n=1, python='py', basetemp=str(tmp_path/'bt'))
    envs = []

    def run(cmd, env, **kw):
        envs.append(env)
        if cmd[-1].endswith('test_1.py'):  # an ASan abort: its report in log_path's file, the output cut short
            path = dict(o.split('=', 1) for o in env['ASAN_OPTIONS'].split(':'))['log_path']
            pathlib.Path(f'{path}.4242').write_text('ERROR: AddressSanitizer: stack-use-after-return\n')
            return types.SimpleNamespace(returncode=1, stdout='....', stderr='')
        return types.SimpleNamespace(returncode=-11, stdout='..', stderr='')
    lines = []
    assert fw.run_asan(args, [], {'ASAN_OPTIONS': 'detect_leaks=0'}, 'lib.so', run=run,
                       log=lambda *a, **k: lines.append(' '.join(map(str, a)))) == -11
    out = '\n'.join(lines)
    assert 'ERROR: AddressSanitizer: stack-use-after-return' in out and 'SIGSEGV' in out
    assert all(e['ASAN_OPTIONS'].startswith('detect_leaks=0:') for e in envs)  # the caller's options kept
    assert not list(tmp_path.glob('**/*.4242'))  # the report's file removed once logged


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


def test_nothing_starts_beside_a_second_try(tmp_path):
    marker = tmp_path/'ran'
    flaky = script_suite('flaky', tmp_path, f'if [ -e {marker} ]; then sleep 0.4; exit 0; fi; touch {marker}; exit 1\n')
    late = script_suite('late', tmp_path, 'true\n')
    flaky.mem, late.mem = 0.5, 3.0
    events = []
    # too little memory for late beside flaky's first run; plenty once that has failed
    gate.run({'flaky': flaky, 'late': late}, [], tmp_path/'logs', sys.executable, build='B', log=events.append,
             mem=lambda: 12.0 if marker.exists() else 5.0, poll=0.02, retry=True)
    order = [e.split(':')[0].split(' (')[0] for e in events]
    assert order.index('ok   flaky') < order.index('start late'), order


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


# Stand-ins for a worktree's tools/ci/affected.py and gate.py: a file's plan is PLANS[name]; the unit suite
# fails when its plan reaches test_theme.py and theme.c holds BREAK.
STUB_AFFECTED = '''import json, subprocess, sys
PLANS = {'a.txt': ['test_a.py'], 'theme.c': ['test_art.py'], 'stream.py': 'all'}
base = sys.argv[sys.argv.index('--base') + 1]
unit = []
for f in subprocess.run(['git', 'diff', '--name-only', base], capture_output=True, text=True).stdout.split():
    p = PLANS.get(f, [])
    unit = 'all' if 'all' in (p, unit) else sorted(set(unit) | set(p))
print(json.dumps({'unit': unit, 'fw': [], 'patch': [], 'other': [], 'why': []}))
'''
STUB_GATE = '''import json, os, pathlib, sys
a = sys.argv
plan = json.loads(pathlib.Path(a[a.index('--plan-json') + 1]).read_text())
logs, only = pathlib.Path(a[a.index('--logs') + 1]), a[a.index('--only') + 1].split(',') if '--only' in a else None
res = {}
if plan['unit'] and (only is None or 'unit' in only):
    reaches = plan['unit'] == 'all' or 'test_theme.py' in plan['unit']
    rc = int(reaches and os.path.exists('theme.c') and 'BREAK' in open('theme.c').read())
    (logs/'unit.log').write_text('FAILED test/unit/test_theme.py::test_tokens\\n' if rc else 'ok\\n')
    res['unit'] = {'rc': rc, 'log': str(logs/'unit.log')}
pathlib.Path(a[a.index('--results') + 1]).write_text(json.dumps(res))
(logs/'summary.txt').write_text(json.dumps(res))
'''


def test_bisection_blames_the_branch_that_breaks_a_test_only_a_later_branchs_plan_runs(tmp_path, monkeypatch):
    """Batch 20261116-232912's shape: row-press broke test_theme but its own plan ran only test_art, and
    streaming's plan ran all of test/unit. Every level runs the batch's plan, so row-press is the culprit."""
    for k in ('AUTHOR', 'COMMITTER'):
        monkeypatch.setenv(f'GIT_{k}_NAME', 't')
        monkeypatch.setenv(f'GIT_{k}_EMAIL', 't@t')
    repo = Repo(tmp_path/'r')
    repo.write('tools/ci/affected.py', STUB_AFFECTED, 'stub affected')
    repo.write('tools/ci/gate.py', STUB_GATE, 'stub gate')
    tip = repo.git('rev-parse', 'HEAD').stdout.strip()
    shas = {'fix/cta': repo.branch('fix/cta', 'a.txt', 'a\n'),
            'feat/row-press': repo.branch('feat/row-press', 'theme.c', 'BREAK\n'),
            'feat/streaming': repo.branch('feat/streaming', 'stream.py', 's\n')}
    git = merge_batch.git
    monkeypatch.setattr(merge_batch, 'git', lambda *a, cwd=repo.path, check=True: git(*a, cwd=cwd, check=check))
    monkeypatch.setattr(merge_batch, 'CACHE', tmp_path/'cache')
    (tmp_path/'logs').mkdir()
    real = merge_batch.Real(repo.trunk, tip, tmp_path/'logs', log=lambda m: None)
    real.shas = shas
    r = merge_batch.integrate(real, tip, list(shas), log=lambda m: None)
    assert list(r.culprits) == ['feat/row-press'] and r.culprits['feat/row-press'].keys() == {'unit'}
    assert sorted(r.landed) == ['feat/streaming', 'fix/cta']


def test_widen_joins_a_heads_plan_to_the_rounds_and_keeps_what_the_head_has():
    whole = {'unit': ['test_theme.py', 'test_gone.py'], 'fw': [], 'patch': ['3:9'], 'other': ['test/x.py'], 'why': []}
    own = {'unit': ['test_art.py'], 'fw': ['test_seek.py'], 'patch': [], 'other': [], 'why': []}
    w = merge_batch.widen(own, whole, lambda f: 'gone' not in f)
    assert w['unit'] == ['test_art.py', 'test_theme.py'] and w['fw'] == ['test_seek.py']
    assert w['patch'] == 'all' and w['other'] == ['test/x.py']  # the joined suite's lines differ by head
    assert merge_batch.widen(None, dict(whole, unit='all'), lambda f: True)['unit'] == 'all'  # the tip's check


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
        self.post_results: list = []  # what each post tier returns, (rc, failed, red); a pass once they run out

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
        return self.post_results.pop(0) if self.post_results else (0, [], [])

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


def test_a_smoke_test_that_fails_after_a_landing_turns_the_tip_red(tmp_path):
    # 2026-11-17: a crash on every Now Playing open passed every merge suite and sat on the trunk for 1.5 h
    q = FakeQueue(tmp_path, [land, land, land])
    q.post_results = [(1, ['smoke'], ['smoke'])]
    merge_batch.append_ready(q.ready, [('feat/np', 'a1')])
    assert q.turn() == 'batch' and q.turn() == 'post'
    assert q.events()[-2:] == ['POST_RED smoke', 'TIP_RED smoke'] and q.state['tip_red'] == ['smoke']
    merge_batch.append_ready(q.ready, [('feat/b', 'b1')])
    assert q.turn() == 'idle' and q.batches == [['feat/np']]  # held: the tip is red
    merge_batch.append_ready(q.ready, [('fix/np', 'f1', True)])
    assert q.turn() == 'batch' and q.batches[-1] == ['fix/np'] and q.state['tip_red'] == []
    q.repo.write('np.txt', 'fixed\n', 'fixed')  # the tip's tree moves, so the post tier runs on it again
    assert q.turn() == 'batch' and q.batches[-1] == ['feat/b']
    assert q.turn() == 'post' and q.events()[-1].startswith('POST_OK') and q.posts == 2


def test_a_post_failure_that_is_no_smoke_verdict_leaves_the_tip_alone(tmp_path):
    q = FakeQueue(tmp_path, [land, land])
    q.post_results = [(1, ['fw-asan', 'smoke'], [])]  # an ASan finding; smoke found no emulator (exit 3)
    merge_batch.append_ready(q.ready, [('feat/a', 'a1')])
    assert q.turn() == 'batch' and q.turn() == 'post'
    assert q.events()[-1] == 'POST_RED fw-asan,smoke' and q.state['tip_red'] == []
    merge_batch.append_ready(q.ready, [('feat/b', 'b1')])
    assert q.turn() == 'batch' and q.batches[-1] == ['feat/b']


def test_run_post_reads_which_failures_make_the_tip_red(tmp_path, monkeypatch):
    repo = Repo(tmp_path/'r')
    q = q2queue.Queue(repo.path, repo.trunk, tmp_path/'q', tmp_path/'ready.txt', log=lambda m: None)
    logs = tmp_path/'post'

    def gate(results):
        def wait(child):
            child.wait()
            (logs/'results.json').write_text(json.dumps(results))
            return 1 if any(r['rc'] for r in results.values()) else 0
        return wait
    monkeypatch.setattr(q, 'wait', gate({'smoke': {'rc': 1}, 'unit-cov': {'rc': 0}}))
    assert q.run_post(logs) == (1, ['smoke'], ['smoke'])
    monkeypatch.setattr(q, 'wait', gate({'smoke': {'rc': 3}, 'fw-asan': {'rc': 1}}))
    assert q.run_post(logs) == (1, ['fw-asan', 'smoke'], [])
    monkeypatch.setattr(q, 'wait', gate({'smoke': {'rc': 'no build'}}))
    assert q.run_post(logs) == (1, ['smoke'], [])
    # a leak found (exit 1) is POST_RED, a backlog item, and never the tip's hold
    monkeypatch.setattr(q, 'wait', gate({'smoke': {'rc': 0}, 'leak-hunt': {'rc': 1}}))
    assert q.run_post(logs) == (1, ['leak-hunt'], [])


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


def test_resume_reaches_a_running_queue_within_one_turn(tmp_path):
    def red(names):
        return {'tip_red': ['patch'], 'status': 2,
                'skipped': {n: 'not landed: the integration tip fails the same suites' for n in names}}
    q = FakeQueue(tmp_path, [red, land], post=False)  # one object throughout: a service that is never restarted
    merge_batch.append_ready(q.ready, [('feat/a', 'a1')])
    q.turn()
    assert q.state['tip_red'] == ['patch'] and q.turn() == 'idle' and len(q.batches) == 1
    q2queue.resume(q.dir)
    assert q.turn() == 'batch' and q.batches[-1] == ['feat/a'] and q.state['tip_red'] == []
    assert 'RESUMED' in q.events() and not (q.dir/'RESUME').exists()
    assert json.loads((q.dir/'state.json').read_text())['tip_red'] == []  # what the loop saves is not the old hold
    assert q.turn() == 'idle' and q.events().count('RESUMED') == 1  # one resume, one line


def test_resume_reaches_a_running_queue_that_stopped_itself(tmp_path):
    def crash(names):
        return {'infra': ['patch'], 'status': 2, 'skipped': {n: 'not landed: patch crashed (the harness or the machine)' for n in names}}
    q = FakeQueue(tmp_path, [crash, crash, land], post=False)
    merge_batch.append_ready(q.ready, [('feat/a', 'a1')])
    q.turn()
    q.turn()
    assert q.turn() == 'stopped' and q.state['crashes'] == 2
    q2queue.resume(q.dir)
    assert q.turn() == 'batch' and q.state['crashes'] == 0 and 'RESUMED' in q.events()


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


# --- the command guard (tools/ci/hooks/guard.py) ---

sys.path.insert(0, str(CI/'hooks'))
import guard  # noqa: E402


@pytest.mark.parametrize('command', [
    'until ! pgrep -f "rebuild.py --dev" >/dev/null; do sleep 5; done; tail -3 log',
    'nohup bash -c \'until ! pgrep -f GradleMain; do sleep 3; done; echo done\' &',
    'while kill -0 $P; do sleep 2; done',
    'until grep -q passed run.log; do sleep 5; done',
    'timeout 600 bash -c "until ! pgrep -f foo; do sleep 2; done"',  # bounded, and still waiting on itself
    'git stash', 'cd x && git stash pop', 'git -C /r stash push -u -m tag',
    'pkill -f patch.py', 'killall python3',
    'python3 -m pytest test/fw -q -n auto',
    'git push origin release/p31', 'git push origin HEAD:release/p32', 'git push origin +main',
    'Q2_QUEUE=1 git merge --ff-only x', 'env Q2_TRUNK_ADMIN=1 git branch -f release/p31 abc',
    'python3 tools/ci/patch_shards.py "$B"', 'q2-heavy python tools/ci/patch_shards.py $B -j 8',
    'python3 tools/ci/emu_pool.py put 2', 'rm -rf ~/q2-emu-server',
])
def test_the_guard_refuses(command):
    assert guard.refusal(command), command


@pytest.mark.parametrize('command', [
    'q2-wait --pid 123 --timeout 600',
    'timeout 600 bash -c "while kill -0 $P; do sleep 2; done"',
    'n=0; until [ -f done ] || [ $n -gt 30 ]; do sleep 5; n=$((n+1)); done',
    'for f in a b; do echo $f; sleep 1; done',
    'while read -r line; do echo "$line"; done < file',
    'git stash list', 'git commit -m "say why git stash is banned and pkill -f too"',
    'grep -rn "pkill" docs/', 'kill 12345',
    'python3 -m pytest test/fw -q -n 6',
    'git push -u origin infra/orchestration', 'git push origin feat/release-notes',
    'python3 tools/ci/patch_shards.py "$B" --lines 100:200', 'grep -n SHARD tools/ci/patch_shards.py',
    'git log --oneline -- tools/ci/patch_shards.py',
    'q2-heavy python3 test/patch.py "$B"',
    'python3 tools/ci/emu_pool.py put 3', 'python3 tools/ci/emu_pool.py get "$B"',
])
def test_the_guard_allows(command):
    assert guard.refusal(command) is None, command


def hook(script, event):
    return subprocess.run([sys.executable, str(CI/'hooks'/script)], input=json.dumps(event), capture_output=True, text=True)


def test_the_guard_as_a_hook_blocks_with_exit_2_and_passes_everything_else():
    p = hook('guard.py', {'tool_name': 'Bash', 'tool_input': {'command': 'until ! pgrep -f X; do sleep 3; done'}})
    assert p.returncode == 2 and 'q2-wait' in p.stderr
    assert hook('guard.py', {'tool_name': 'Bash', 'tool_input': {'command': 'ls'}}).returncode == 0
    assert hook('guard.py', {'tool_name': 'Read', 'tool_input': {'file_path': 'git stash'}}).returncode == 0
    assert subprocess.run([sys.executable, str(CI/'hooks/guard.py')], input='not json', capture_output=True,
                          text=True).returncode == 0


# --- q2-wait ---

def wait_cmd(*args):
    return subprocess.run([sys.executable, str(CI/'q2-wait'), *args], capture_output=True, text=True)


def test_q2_wait_ends_with_the_process_the_line_or_the_clock(tmp_path):
    child = subprocess.Popen(['sleep', '0.3'])
    assert wait_cmd('--pid', str(child.pid), '--timeout', '10', '--poll', '0.05').returncode == 0
    child.wait()
    t = time.monotonic()
    p = wait_cmd('--pid', str(os.getpid()), '--timeout', '0.4', '--poll', '0.05')
    assert p.returncode == 124 and 'timed out' in p.stdout and time.monotonic() - t < 5
    log = tmp_path/'run.log'
    log.write_text('starting\n12 passed in 3s\n')
    assert wait_cmd('--log', str(log), '--match', 'passed|failed', '--timeout', '5').stdout.strip() == '12 passed in 3s'
    log.write_text('starting\nTraceback (most recent call last):\n')
    assert wait_cmd('--log', str(log), '--match', 'passed', '--fail', 'Traceback', '--timeout', '5').returncode == 1
    log.write_text('starting\npass')  # a line not ended yet is not a match
    assert wait_cmd('--log', str(log), '--match', 'pass', '--timeout', '0.3', '--poll', '0.05').returncode == 124


def test_q2_wait_will_not_wait_without_a_limit():
    assert wait_cmd('--pid', '1').returncode == 2
    assert wait_cmd('--pid', '1', '--timeout', '99999').returncode == 2
    assert wait_cmd('--timeout', '5').returncode == 2


# --- q2: the registry, the stop ---

# What q2 reads of the machine that a test pins, so its outcome is the same run from a session's shell or detached
# (setsid, the merge queue's service): no process name counts as a Claude session, and the rig is the test's marker.
PINNED = {'Q2_SESSION_NAMES': 'q2-test-no-session', 'Q2_KEEP': f'q2-test-rig-{os.getpid()}'}


def q2(tmp_path, *args, **env):
    e = {**os.environ, 'Q2_QUEUE_DIR': str(tmp_path/'q'), 'Q2_HEAVY_LOCKDIR': str(tmp_path/'heavy'),
         'Q2_ROOTS': str(tmp_path/'work'), **PINNED, **env}
    return subprocess.run([sys.executable, str(CI/'q2'), *args], capture_output=True, text=True, env=e)


def test_a_second_worker_in_a_worktree_is_refused(tmp_path):
    repo = Repo(tmp_path/'work/r')
    assert q2(tmp_path, 'agent', 'start', 'w1', '--worktree', str(repo.path), '--item', 'the lyrics page').returncode == 0
    again = q2(tmp_path, 'agent', 'start', 'w2', '--worktree', str(repo.path))
    assert again.returncode != 0 and 'w1 has been working' in again.stderr
    assert q2(tmp_path, 'agent', 'start', 'w2', '--worktree', str(repo.path), '--takeover').returncode == 0
    rows = [line.split('\t') for line in q2(tmp_path, 'agent', 'list').stdout.splitlines()]
    assert [(r[0], r[4]) for r in rows] == [('w1', 'replaced'), ('w2', 'running')]


def test_a_worker_cannot_end_with_a_process_left_in_its_worktree(tmp_path):
    repo = Repo(tmp_path/'work/r')
    q2(tmp_path, 'agent', 'start', 'w1', '--worktree', str(repo.path))
    left = subprocess.Popen(['sleep', '60'], cwd=repo.path, start_new_session=True)
    try:
        refused = q2(tmp_path, 'agent', 'end', 'w1', '--state', 'BLOCKED')
        assert refused.returncode != 0 and f'process {left.pid}' in refused.stderr
        stop = hook('subagent_stop.py', {'cwd': str(repo.path), 'hook_event_name': 'SubagentStop'})
        # the hook asks the installed registry, so point it at this test's through the environment
        stop = subprocess.run([sys.executable, str(CI/'hooks/subagent_stop.py')], capture_output=True, text=True,
                              input=json.dumps({'cwd': str(repo.path)}),
                              env={**os.environ, 'Q2_QUEUE_DIR': str(tmp_path/'q'), 'Q2_HEAVY_LOCKDIR': str(tmp_path/'heavy')})
        assert stop.returncode == 2 and f'process {left.pid}' in stop.stderr
    finally:
        left.kill()
        left.wait()
    assert q2(tmp_path, 'agent', 'end', 'w1', '--state', 'BLOCKED').returncode == 0
    stop = subprocess.run([sys.executable, str(CI/'hooks/subagent_stop.py')], capture_output=True, text=True,
                          input=json.dumps({'cwd': str(repo.path)}), env={**os.environ, 'Q2_QUEUE_DIR': str(tmp_path/'q')})
    assert stop.returncode == 0
    # READY also needs the work committed and pushed
    q2(tmp_path, 'agent', 'start', 'w3', '--worktree', str(repo.path))
    assert 'not pushed' in q2(tmp_path, 'agent', 'end', 'w3', '--state', 'READY').stderr


def gone(pid, seconds=5):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        try:
            state = pathlib.Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()[0]
        except OSError:
            return True
        if state == 'Z':
            return True
        time.sleep(0.05)
    return False


def test_stop_all_ends_q2_processes_and_nothing_else(tmp_path):
    """stop-all scans every process of this user on the machine and takes those of the roots it is given (Q2_ROOTS,
    here this test's own folder), so another agent's q2 work is never in reach: a look-alike neighbour, the same
    script in another folder, is not among what it would stop, nor stopped. What it reads of the test's ancestors is
    pinned (PINNED): the rig it keeps is matched by Q2_KEEP against a process and each of its ancestors, and the
    default (emu_pool.py, dhu, Xvfb and the pool's folders) also matched the shell the test was started from when that
    shell's command named tools/ci/emu_pool.py, keeping the test's own processes; and a process with a Claude session
    among its ancestors is not listed as left alone, so the rig was listed run detached and not run from a session's
    shell (2026-11-22). The assertions are on the lines of what it would stop (`proc`), not on any mention."""
    work = tmp_path/'work'
    (work/'tools/ci').mkdir(parents=True)
    script = work/'tools/ci/fake_suite.sh'
    script.write_text('sleep 60 & wait\n')
    other = tmp_path/'elsewhere'
    (other/'tools/ci').mkdir(parents=True)
    neighbour_script = other/'tools/ci/fake_suite.sh'
    neighbour_script.write_text('sleep 60 & wait\n')
    keep = f'q2-test-rig-{os.getpid()}-{tmp_path.name}'
    ours = subprocess.Popen(['bash', str(script)], cwd=work, start_new_session=True)
    rig = subprocess.Popen(['bash', '-c', f'exec -a {keep} bash {script}'], cwd=work, start_new_session=True)
    bystander = subprocess.Popen(['sleep', '60'], cwd=other, start_new_session=True)
    neighbour = subprocess.Popen(['bash', str(neighbour_script)], cwd=other, start_new_session=True)
    try:
        time.sleep(0.3)
        listed = q2(tmp_path, 'stop-all', Q2_KEEP=keep)
        assert listed.returncode == 0 and f'proc  {ours.pid}' in listed.stdout and 'nothing was touched' in listed.stdout
        assert f'proc  {neighbour.pid}' not in listed.stdout and f'proc  {rig.pid}' not in listed.stdout
        assert ours.poll() is None  # a listing stops nothing
        done = q2(tmp_path, 'stop-all', '--yes', Q2_KEEP=keep)
        assert done.returncode == 0 and 'nothing q2 is left running' in done.stdout, done.stdout + done.stderr
        assert gone(ours.pid)
        # the rig, what is not q2's and another root's look-alike are left alone
        assert rig.poll() is None and bystander.poll() is None and neighbour.poll() is None
        assert (tmp_path/'q/STOP').exists()
    finally:
        for p in (ours, rig, bystander, neighbour):
            with __import__('contextlib').suppress(ProcessLookupError):
                os.killpg(p.pid, 9)
            p.wait()


def systemd_user_session():
    # Vamp's Mac container has no systemd at all (docs/building.md#building-on-macos), so no systemd-run either
    return (shutil.which('systemd-run') is not None
            and subprocess.run(['systemd-run', '--user', '--scope', '-q', 'true'], capture_output=True).returncode == 0)


@pytest.mark.skipif(not systemd_user_session(), reason='no systemd user session: q2-heavy runs its job in a scope')
def test_q2_heavy_records_its_job_and_stops_it_at_the_deadline(tmp_path):
    # no admission wait: this test is about the record and the deadline, whatever else the machine is doing
    env = {**os.environ, 'Q2_HEAVY_LOCKDIR': str(tmp_path), 'Q2_HEAVY_RAMP': '0', 'Q2_HEAVY_NODUMP_SRC': '/none',
           'Q2_HEAVY_RESERVE_GB': '0', 'Q2_HEAVY_PSI': str(tmp_path/'no-pressure'), 'Q2_HEAVY_DISK_START_GB': '0',
           'Q2_HEAVY_DISK_STOP_GB': '0', 'Q2_HEAVY_AC': str(tmp_path/'no-ac')}
    env.pop('Q2_HEAVY_JOB', None)
    env.pop('Q2_HEAVY_CLASS', None)  # the queue runs these tests too, with its own class in the environment
    job = subprocess.Popen(['bash', str(CI/'q2-heavy'), 'sleep', '30'], env={**env, 'Q2_HEAVY_MAX_S': '2'}, cwd=CI,
                           stderr=subprocess.PIPE, text=True)
    record = tmp_path/'q2-heavy/jobs'/str(job.pid)
    end = time.monotonic() + 30
    while not record.exists() and time.monotonic() < end:
        time.sleep(0.05)
    fields = dict(line.split('=', 1) for line in record.read_text().splitlines())
    assert fields['class'] == 'branch' and fields['command'] == 'sleep 30' and fields['worktree'] == str(CI.parents[1])
    t = time.monotonic()
    assert job.wait(15) == 143 and time.monotonic() - t < 10
    assert 'deadline' in (job.stderr.read() if job.stderr else '') and not record.exists()
    # the queue's class is recorded as such, and stop-all ends a job by its scope
    job = subprocess.Popen(['bash', str(CI/'q2-heavy'), 'sleep', '30'], env={**env, 'Q2_HEAVY_CLASS': 'queue'}, cwd=CI)
    record = tmp_path/'q2-heavy/jobs'/str(job.pid)
    end = time.monotonic() + 30
    while not record.exists() and time.monotonic() < end:
        time.sleep(0.05)
    assert 'class=queue' in record.read_text()
    done = subprocess.run([sys.executable, str(CI/'q2'), 'stop-all', '--yes'], capture_output=True, text=True,
                          env={**os.environ, 'Q2_QUEUE_DIR': str(tmp_path/'q'), 'Q2_HEAVY_LOCKDIR': str(tmp_path),
                               'Q2_ROOTS': str(tmp_path/'none'), **PINNED})
    assert job.wait(15) != 0 and 'nothing q2 is left running' in done.stdout, done.stdout
