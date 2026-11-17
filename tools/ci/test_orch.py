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
