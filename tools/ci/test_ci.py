"""Checks of tools/ci: the affected-tests rules and patch_shards.py's slices, on small made-up files.

    python3 -m pytest tools/ci -q     # no build, no stock ZIP; a second or two
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import affected  # noqa: E402
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
