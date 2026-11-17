"""Checks of tools/ci: the affected-tests rules and patch_shards.py's slices, on small made-up files.

    python3 -m pytest tools/ci -q     # no build, no stock ZIP; a second or two
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import affected  # noqa: E402
import emu_pool  # noqa: E402
import json  # noqa: E402
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


def test_a_reserved_lease_is_never_reclaimed_or_handed_on(tmp_path, monkeypatch):
    """The user's rig (slot 2) was handed to an agent once its lease ran out (2026-11-16 18:30): a reserved lease
    stays with its holder past its end, and `get` by another name takes a free copy or waits."""
    monkeypatch.setattr(emu_pool, 'STATE', tmp_path)
    monkeypatch.setattr(emu_pool, 'LEASES', tmp_path/'leases.json')
    monkeypatch.setattr(emu_pool, 'K', 2)
    monkeypatch.setattr(emu_pool, 'q2emu', lambda *a: type('P', (), {'returncode': 0, 'stdout': 'running', 'stderr': ''})())
    monkeypatch.setattr(emu_pool, 'make_copy', lambda k: None)
    past = 1.0
    (tmp_path/'leases.json').write_text(json.dumps({'1': {'name': 'aa-dhu', 'build': '/b', 'since': 0, 'until': past, 'reserved': True},
                                                    '2': {'name': 'old', 'build': '/b', 'since': 0, 'until': past}}))
    assert emu_pool.get('/b', 'agent', 1, wait=False) == 2  # the unreserved expired one, not the user's
    leases = json.loads((tmp_path/'leases.json').read_text())
    assert leases['1']['name'] == 'aa-dhu' and leases['1']['reserved'] and leases['2']['name'] == 'agent'
    try:
        emu_pool.get('/c', 'other', 1, wait=False)
    except SystemExit as e:
        assert 'leased' in str(e)
    else:
        raise AssertionError('a reserved copy was handed on')
    emu_pool.reserve(1, on=False)
    assert emu_pool.get('/c', 'other', 1, wait=False) == 1  # unreserved and expired: reclaimed
