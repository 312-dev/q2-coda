"""Checks of tools/ci: the affected-tests rules, patch_shards.py's slices and merge_batch.py's bisection, on
small made-up files and a fake repository.

    python3 -m pytest tools/ci -q     # no build, no stock ZIP; a second or two
"""
import os
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import affected  # noqa: E402
import emu_pool  # noqa: E402
import gate  # noqa: E402
import merge_batch  # noqa: E402
import patch_shards  # noqa: E402

ALL = affected.ALL

# A test/patch.py in miniature: a helper, then three tests, each ending at passed().
PATCH = '''\
import os
CONFIG = {}
def helper(m): return m + 1
def other(m): return m
m = 1
assert helper(m) == 2
passed()
CONFIG['A'] = 1
m = 5
assert other(m) == 5
passed()
n = helper(3)
assert n == 4
passed()
'''


def tree(tmp_path, files):
    for name, text in files.items():
        (tmp_path/name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path/name).write_text(text)
    return tmp_path


def test_test_edit_runs_its_own_test():
    # line 9 (m = 5) is in the second test, lines 8-11
    assert affected.patch_tests_for({9}, PATCH) == {'8:11'}


def test_helper_edit_runs_every_test_that_calls_it():
    # helper (line 3) is called by the first test (from the file's head to line 7) and the third (12-14)
    assert affected.patch_tests_for({3}, PATCH) == {'1:7', '12:14'}


def test_payload_change_runs_patch_and_its_fw_files(tmp_path):
    root = tree(tmp_path, {'patch/seek.c': 'int seek_step(int x) {\n  return x;\n}\n',
                           'test/fw/test_seek.py': 'def test(fw): fw.fn("seek_step")\n',
                           'test/fw/test_other.py': 'def test(fw): fw.fn("unrelated")\n',
                           'test/fw/test_e2e_system.py': ''})
    p = affected.plan_for({'patch/seek.c': {2}}, root).as_dict()
    assert p['patch'] == ALL
    assert p['fw'] == ['test_e2e_system.py', 'test_seek.py']


def test_fw_file_found_by_a_function_the_source_defines(tmp_path):
    root = tree(tmp_path, {'patch/np_marquee.c': 'static void marquee_tick(void) {\n}\n',
                           'test/fw/test_nav_paint.py': "lib = 'marquee_tick'\n"})
    assert affected.plan_for({'patch/np_marquee.c': {1}}, root).as_dict()['fw'] == ['test_nav_paint.py']


def test_source_no_fw_file_names_runs_all_fw(tmp_path):
    root = tree(tmp_path, {'patch/tilt.c': 'int tilt_now(void) {\n  return 0;\n}\n', 'test/fw/test_x.py': ''})
    assert affected.plan_for({'patch/tilt.c': {1}}, root).as_dict()['fw'] == ALL


def test_unknown_file_runs_everything(tmp_path):
    p = affected.plan_for({'Makefile.new': set()}, tmp_path).as_dict()
    assert p['unit'] == p['fw'] == p['patch'] == ALL


def test_tool_maps_to_its_unit_test_and_a_build_input_to_the_payload(tmp_path):
    root = tree(tmp_path, {'test/unit/test_shelf.py': '', 'test/unit/test_theme.py': ''})
    p = affected.plan_for({'tools/shelf.py': {1}}, root).as_dict()
    assert p['unit'] == ['test_shelf.py'] and not p['patch'] and not p['fw']
    p = affected.plan_for({'tools/theme.py': {1}}, root).as_dict()
    assert p['unit'] == ['test_theme.py'] and p['patch'] == ALL and p['fw'] == ALL


def test_docs_only_run_the_anchor_check(tmp_path):
    p = affected.plan_for({'docs/ipod.md': {4}}, tmp_path).as_dict()
    assert p['unit'] == ['test_docs.py'] and not p['patch'] and not p['fw']


def test_slice_keeps_what_a_range_reads_and_drops_what_it_rebinds():
    stmts = patch_shards.analyse(PATCH)
    start = next(s.index for s in stmts if s.line == 12)  # the third test reads helper, nothing else
    lines = {stmts[i].line for i in patch_shards.slice_of(stmts, start, len(stmts))}
    assert lines == {1, 3, 4}  # the import and the two defs; no earlier test
    start = next(s.index for s in stmts if s.line == 10)  # reads m and other: m = 5 and CONFIG's change ride along
    lines = {stmts[i].line for i in patch_shards.slice_of(stmts, start, len(stmts))}
    assert {9, 4} <= lines and 5 not in lines and 6 not in lines


def test_a_call_that_is_passed_a_name_counts_as_changing_it():
    source = 'def lay(v): v.append(1)\nrows = []\nlay(rows)\nassert rows == [1]\n'
    stmts = patch_shards.analyse(source)
    assert {stmts[i].line for i in patch_shards.slice_of(stmts, 3, 4)} == {1, 2, 3}


def test_plan_cuts_after_a_test():
    stmts = patch_shards.analyse(PATCH)
    patch_shards.cost(stmts, {})
    ranges = patch_shards.plan(stmts, 2)
    assert len(ranges) == 2 and ranges[0][0] == 0 and ranges[-1][1] == len(stmts)
    cut = ranges[1][0]
    assert patch_shards.ast.unparse(stmts[cut - 1].node) == 'passed()'


def test_calling_a_named_lambda_counts_as_its_body():
    source = 'm = []\npaint = lambda: m.append(1)\npaint()\nassert m == [1]\n'
    stmts = patch_shards.analyse(source)
    assert {stmts[i].line for i in patch_shards.slice_of(stmts, 3, 4)} == {1, 2, 3}


def test_shard_traces_join_with_their_checks_numbered_on():
    shards = [b'draw a\n== 1\n== 2\n', b'draw b\n== 1\n']
    assert patch_shards.join_traces(shards) == b'draw a\n== 1\n== 2\ndraw b\n== 3\n'


def test_a_suite_file_maps_its_lines_onto_the_joined_suite(tmp_path):
    """A change to test/patch_suite/01_x.py names the joined suite's lines: its file's line 2 is joined line 9."""
    head = PATCH.split('\n')
    first, second = '\n'.join(head[:7]) + '\n', '\n'.join(head[7:])
    root = tree(tmp_path, {'test/patch_suite/00_a.py': first, 'test/patch_suite/01_b.py': second,
                           'test/patch.py': 'runner\n', 'test/suite_source.py': ''})
    p = affected.plan_for({'test/patch_suite/01_b.py': {2}}, root, patch_source=PATCH).as_dict()
    assert p['patch'] == sorted(affected.patch_tests_for({9}, PATCH)) and 'test/playback.py' in p['other']
    assert affected.plan_for({'test/patch.py': {1}}, root).as_dict()['patch'] == ALL


# A pool copy's two scripts as q2-emu-server's are, after make_copy set its viewer port.
Q2EMU = '#!/bin/bash\nE=$(dirname "$0")\nPORT=${Q2EMU_PORT:-2403}\nsetsid nohup "$E/run.sh" "$1" &\n'
RUN = ('#!/bin/bash\nE=x\nS=$E/state\n# Q2EMU_CORE=1: qemu writes the guest\'s core\n'
       'bwrap --die-with-parent \\\n    --setenv PATH /usr/bin \\\n    "${BOOT[@]}"\n')


def test_a_pool_copy_runs_car_remote_on_its_own_port_once(tmp_path):
    (tmp_path/'q2emu').write_text(Q2EMU)
    (tmp_path/'run.sh').write_text(RUN)
    emu_pool.fit(tmp_path, 3)
    script, run = (tmp_path/'q2emu').read_text(), (tmp_path/'run.sh').read_text()
    assert 'PORT=${Q2EMU_PORT:-2403}\nexport Q2EMU_CODA_PORT=${Q2EMU_CODA_PORT:-7703}\n' in script
    assert run.index('CODA=()') < run.index('# Q2EMU_CORE=1') and run.endswith('    "${CODA[@]}" "${BOOT[@]}"\n')
    assert 'exec /emu/coda-rc.real --port $Q2EMU_CODA_PORT' in run and '--ro-bind "$S/coda-rc" /usr/bin/coda-rc' in run
    emu_pool.fit(tmp_path, 3)  # a second time changes nothing
    assert (tmp_path/'q2emu').read_text() == script and (tmp_path/'run.sh').read_text() == run


def test_a_changed_run_script_is_refused(tmp_path):
    (tmp_path/'q2emu').write_text(Q2EMU)
    (tmp_path/'run.sh').write_text(RUN.replace('"${BOOT[@]}"', 'demo'))
    try:
        emu_pool.fit(tmp_path, 3)
    except AssertionError as e:
        assert 'run.sh has changed' in str(e)
    else:
        raise AssertionError('fit took a run.sh it cannot place the stand-in in')


class FakeBatch:
    """merge_batch.integrate's ops on a pretend repository: a commit is the tuple of branches merged onto the
    tip; a suite fails when the commit holds a branch that breaks it (bad) or a pair that breaks it together
    (pairs); a branch in conflicts never merges."""
    def __init__(self, bad=None, pairs=(), conflicts=(), flaky=0, red_tip=()):
        self.bad, self.pairs, self.conflicts, self.flaky = bad or {}, pairs, set(conflicts), flaky
        self.red_tip = set(red_tip)  # suites the tip fails on its own
        self.runs = []

    def assemble(self, level, base, branches):
        merged = [b for b in branches if b not in self.conflicts and b not in base]
        return base + tuple(merged), merged, {b: 'conflicts' for b in branches if b in self.conflicts}

    def test(self, level, head, suites, tag):
        self.runs.append((level, head, suites))
        broken = {s for b in head for s in self.bad.get(b, ())}
        broken |= {s for a, b, s in self.pairs if a in head and b in head}
        broken |= self.red_tip
        if self.flaky:
            self.flaky -= 1
            broken.add('unit')
        everything = ['unit', 'fw', 'patch']
        return {s: merge_batch.Suite(s not in broken, f'{s}.log') for s in (suites or everything)}


def integrate(fake, n=10):
    return merge_batch.integrate(fake, (), [f'b{i}' for i in range(n)], log=lambda m: None)


def test_a_green_batch_lands_whole_after_one_run():
    fake = FakeBatch()
    r = integrate(fake)
    assert r.landed == [f'b{i}' for i in range(10)] and r.head == tuple(r.landed) and not r.culprits
    assert len(fake.runs) == 1 and fake.runs[0][2] is None  # one run, of everything


def test_one_culprit_is_found_and_the_rest_land_on_a_tested_commit():
    fake = FakeBatch(bad={'b6': {'patch'}})
    r = integrate(fake)
    assert list(r.culprits) == ['b6'] and set(r.culprits['b6']) == {'patch'}
    assert r.landed == [b for b in (f'b{i}' for i in range(10)) if b != 'b6']
    assert r.head == tuple(r.landed) and fake.runs[-1] == (0, r.head, None)  # the remainder, tested in full
    bisect = [run for run in fake.runs if run[2] is not None]
    assert all(s == ['patch'] for _, _, s in bisect)  # only the failing suite while bisecting


def test_one_culprit_costs_a_few_runs_wherever_it_is():
    for i in range(10):
        fake = FakeBatch(bad={f'b{i}': {'unit'}})
        r = integrate(fake)
        bisect = [run for run in fake.runs if run[2] is not None]
        assert list(r.culprits) == [f'b{i}'] and len(bisect) <= 7, (i, len(bisect))  # the tip, then about log2(10) + 2


def test_two_culprits_in_different_halves():
    fake = FakeBatch(bad={'b1': {'unit'}, 'b8': {'fw'}})
    r = integrate(fake)
    assert set(r.culprits) == {'b1', 'b8'} and len(r.landed) == 8
    assert r.culprits['b1'].keys() == {'unit'} and r.culprits['b8'].keys() == {'fw'}


def test_a_pair_that_fails_together_rejects_the_later_one():
    fake = FakeBatch(pairs=[('b2', 'b7', 'patch')])
    r = integrate(fake)
    assert list(r.culprits) == ['b7'] and 'b2' in r.landed


def test_a_conflict_is_skipped_not_a_stop():
    fake = FakeBatch(conflicts=['b3'])
    r = integrate(fake)
    assert r.skipped == {'b3': 'conflicts'} and len(r.landed) == 9 and len(fake.runs) == 1


def test_a_failure_bisection_cannot_find_lands_nothing():
    fake = FakeBatch(flaky=1)  # the batch run fails, every half passes
    r = integrate(fake)
    assert r.head is None and not r.landed and not r.culprits and len(r.skipped) == 10


def test_a_red_tip_lands_nothing_and_blames_no_branch():
    fake = FakeBatch(red_tip={'unit'})
    r = integrate(fake)
    assert not r.culprits and not r.landed and len(r.skipped) == 10
    assert [h for _, h, _ in fake.runs] == [tuple(f'b{i}' for i in range(10)), ()]  # the batch, then the tip


def test_a_half_known_to_fail_is_not_tested_again():
    fake = FakeBatch(bad={'b9': {'unit'}})
    integrate(fake)
    heads = [h for _, h, suites in fake.runs if suites is not None]
    assert len(heads) == len(set(heads))  # no commit tested twice while bisecting


def test_restrict_keeps_the_named_suites():
    plan = {'unit': ['test_docs.py'], 'fw': 'all', 'patch': 'all', 'other': ['test/build.py'], 'why': []}
    cut = merge_batch.restrict(plan, ['fw', 'test/build.py'])
    assert cut['unit'] == [] and cut['patch'] == [] and cut['fw'] == 'all' and cut['other'] == ['test/build.py']


def test_ready_list_last_line_for_a_branch_wins(tmp_path):
    ready = tmp_path/'ready.txt'
    ready.write_text('feat/a 111\n# a comment\nfeat/b\nfeat/a 222  # again\n')
    assert merge_batch.read_ready(ready) == [('feat/a', '222'), ('feat/b', None)]
    assert merge_batch.take_ready(ready, tmp_path/'taken') == [('feat/a', '222'), ('feat/b', None)]
    assert ready.read_text() == '' and 'feat/b' in (tmp_path/'taken').read_text()


def fake_suite(name, seconds, rc=0, needs_build=False):
    return gate.Suite(name, [sys.executable, '-c', f'import time, sys; time.sleep({seconds}); sys.exit({rc})'],
                      needs_build=needs_build)


def test_gate_holds_a_suite_that_would_leave_too_little_memory(tmp_path):
    a, b = fake_suite('a', 0.3), fake_suite('b', 0.1)
    a.mem = b.mem = 1.5
    events = []
    results = gate.run({'a': a, 'b': b}, [], tmp_path, sys.executable, build='B', log=events.append, mem=lambda: 6.0,
                       poll=0.02)
    starts = [e for e in events if e.startswith(('start', 'ok'))]
    # 6 GB less a's 1.5 leaves 4.5, and b's 1.5 more would go under the 4 GB floor: b waits for a
    assert [e.split()[1].rstrip(':') for e in starts] == ['a', 'a', 'b', 'b']
    assert results['a']['rc'] == 0 and results['b']['rc'] == 0


def test_gate_runs_suites_together_when_memory_allows(tmp_path):
    a, b = fake_suite('a', 0.3), fake_suite('b', 0.3)
    events = []
    gate.run({'a': a, 'b': b}, [], tmp_path, sys.executable, build='B', log=events.append, mem=lambda: 12.0, poll=0.02)
    assert [e.split()[0] for e in events[:2]] == ['start', 'start']


def test_gate_skips_what_needs_a_failed_build(tmp_path, monkeypatch):
    monkeypatch.setattr(gate, 'ROOT', tmp_path)
    (tmp_path/'tools/ci').mkdir(parents=True)
    (tmp_path/'tools/ci/rebuild.py').write_text('import sys; sys.exit(3)\n')
    monkeypatch.setattr(gate, 'HEAVY', tmp_path/'none')
    suites = {'p': fake_suite('p', 0, needs_build=True), 'u': fake_suite('u', 0, rc=1)}
    results = gate.run(suites, [], tmp_path/'logs', sys.executable, log=lambda m: None, mem=lambda: 12.0, poll=0.02)
    assert results['build']['rc'] == 3 and results['p']['rc'] == 'no build' and results['u']['rc'] == 1
    assert 'FAILED: build, u, p' in gate.table(results, 1) or 'FAILED: build, p, u' in gate.table(results, 1)


def test_gate_plan_that_runs_everything_is_the_whole_gate(tmp_path):
    every = {'unit': 'all', 'fw': 'all', 'patch': 'all', 'other': ['test/build.py'], 'why': []}
    assert set(gate.suites_for(every, [], 'py', tmp_path)) == set(gate.FULL)
    docs = {'unit': ['test_docs.py'], 'fw': [], 'patch': [], 'other': [], 'why': []}
    assert list(gate.suites_for(docs, [], 'py', tmp_path)) == ['unit']
    # --only: the plan's unit as the plan has it, fw-asan (only the whole gate has it) whole, patch (the
    # plan lacks it: its changes cannot reach it) not at all
    only = gate.suites_for(docs, ['fw-asan', 'unit', 'patch'], 'py', tmp_path)
    assert list(only) == ['fw-asan', 'unit']
    assert only['fw-asan'].argv[-1] == '--asan' and only['unit'].argv[-1] == 'test/unit/test_docs.py'


def test_gate_stopped_early_ends_what_its_suites_started(tmp_path):
    pidfile = tmp_path/'grandchild'
    s = gate.Suite('s', ['bash', '-c', f'sleep 30 & echo $! > {pidfile}; wait'])
    def clock():  # the loop's turn after the grandchild started is when the gate is stopped
        if pidfile.exists() and pidfile.read_text().strip():
            raise KeyboardInterrupt
        return time.monotonic()
    try:
        gate.run({'s': s}, [], tmp_path, sys.executable, build='B', log=lambda m: None, mem=lambda: 12.0,
                 clock=clock, poll=0.05)
    except KeyboardInterrupt:
        pass
    pid = int(pidfile.read_text())
    for _ in range(50):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.02)
    raise AssertionError(f'suite s\'s sleep ({pid}) outlived the gate')


def test_bisection_never_takes_more_than_the_dry_run_says():
    for n in (1, 2, 5, 10):
        fake = FakeBatch(bad={f'b{i}': {'unit'} for i in range(n)})  # every branch bad: the worst case
        integrate(fake, n)
        assert len([run for run in fake.runs if run[2] is not None]) <= 2 * n - 1  # merge_batch.dry_run's bound
