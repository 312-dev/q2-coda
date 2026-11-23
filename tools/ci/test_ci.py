"""Checks of tools/ci: the affected-tests rules, patch_shards.py's slices and merge_batch.py's bisection, on
small made-up files and a fake repository.

    python3 -m pytest tools/ci -q     # no build, no stock ZIP; a second or two
"""
import ast
import errno
import os
import pathlib
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import affected  # noqa: E402
import covcheck  # noqa: E402
import emu_pool  # noqa: E402
import gate  # noqa: E402
import merge_batch  # noqa: E402
import json  # noqa: E402
import patch_shards  # noqa: E402
import q2live  # noqa: E402
import smoke  # noqa: E402

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


def test_a_deleted_host_suite_is_not_run(tmp_path):
    """A test script the branch deleted (the stock player's suites, 2026-11-18) is not planned: it is not there to run."""
    root = tree(tmp_path, {'test/kept.py': ''})
    p = affected.plan_for({'test/kept.py': {1}, 'test/gone.py': set()}, root).as_dict()
    assert 'test/kept.py' in p['other'] and 'test/gone.py' not in p['other'], p['other']


def test_unknown_file_runs_everything(tmp_path):
    p = affected.plan_for({'Makefile.new': set()}, tmp_path).as_dict()
    assert p['unit'] == p['fw'] == p['patch'] == ALL


def test_tool_maps_to_its_unit_test_and_a_build_input_to_the_payload(tmp_path):
    root = tree(tmp_path, {'test/unit/test_shelf.py': '', 'test/unit/test_theme.py': ''})
    p = affected.plan_for({'tools/shelf.py': {1}}, root).as_dict()
    assert p['unit'] == ['test_docs.py', 'test_shelf.py'] and not p['patch'] and not p['fw']  # docs: its citations
    p = affected.plan_for({'tools/theme.py': {1}}, root).as_dict()
    assert p['unit'] == ['test_docs.py', 'test_theme.py'] and p['patch'] == ALL and p['fw'] == ALL


REPO = pathlib.Path(affected.__file__).resolve().parents[2]


def test_a_uac1_module_change_plans_the_uac1_suites_not_everything():
    """feat/car-rate (8aab0ddc) changed tools/uac1_module/ and nothing else under tools/, and its plan was everything
    (tools/ folders had no rule), test/player/run.py's 13 minutes and the merge tier's host suites among it. What
    reads the folder: tools/uac1.py's pin (test_uac1.py), the build that refuses an unpinned change (test/build.py),
    and the module's own tests (test_iap_tools.py, test/uac1_bind.py, test/uac1_isoc.py)."""
    changes = {'tools/uac1_module/uac1_build.py': {1}, 'tools/uac1_module/patches/q2-uac1-playback-rates.patch': {1},
               'tools/uac1.py': {22}}
    plan = json.loads(affected.plan_for(changes, REPO).to_json())
    assert not gate.everything(plan) and not plan['patch'] and not plan['fw']
    assert plan['unit'] == ['test_docs.py', 'test_iap_tools.py', 'test_uac1.py']
    assert set(plan['other']) == {'test/build.py', 'test/uac1_bind.py', 'test/uac1_isoc.py', 'test/stock_sites.py',
                                  'mypy-tools', 'cov:tools/uac1.py', 'cov:tools/uac1_module/uac1_build.py'}
    # tools/uac1.py alone: the pinned module goes in the image, so the build's checks, and nothing of the payload
    plan = json.loads(affected.plan_for({'tools/uac1.py': {22}}, REPO).to_json())
    assert plan['unit'] == ['test_docs.py', 'test_uac1.py'] and 'test/build.py' in plan['other']
    assert not plan['patch'] and not plan['fw'] and 'test/uac1_bind.py' not in plan['other']


def test_an_iap_probe_change_plans_the_probe_suites_not_everything():
    """feat/car-mode and feat/car-library changed tools/iap_probe/, which went to everything. probe_build.py is
    loaded by test_iap_tools.py and builds patch/q2_iap.c as the probe, which test/iap_probe.py runs on the host."""
    changes = {'tools/iap_probe/probe_build.py': {1}, 'tools/iap_probe/q2-iap-probe.sh': {1}}
    plan = json.loads(affected.plan_for(changes, REPO).to_json())
    assert not gate.everything(plan) and not plan['patch'] and not plan['fw']
    assert plan['unit'] == ['test_docs.py', 'test_iap_tools.py']
    assert set(plan['other']) == {'test/iap_probe.py', 'test/stock_sites.py', 'mypy-tools',
                                  'cov:tools/iap_probe/probe_build.py'}


def test_a_build_input_still_runs_the_payload_and_an_unknown_tools_folder_everything(tmp_path):
    plan = affected.plan_for({'tools/build.py': {1}}, REPO).as_dict()
    assert plan['patch'] == plan['fw'] == ALL and 'test/build.py' in plan['other']
    assert affected.plan_for({'tools/newprobe/run.sh': {1}}, tmp_path).as_dict()['patch'] == ALL
    every = affected.plan_for({'tools/newprobe/run.sh': {1}}, tmp_path).as_dict()
    assert every['unit'] == every['fw'] == ALL and 'test/player/run.py' in every['other']


def test_every_tools_module_the_build_imports_is_a_build_or_image_input():
    """tools/uac1.py and tools/iroh.py were imported by tools/build.py and in neither set, so a change to the
    module the image carries ran its unit test and not the build."""
    mods = {p.stem for p in (REPO/'tools').glob('*.py')}

    def imports(name):
        tree_ = ast.parse((REPO/'tools'/f'{name}.py').read_text())
        found = set()
        for n in ast.walk(tree_):
            if isinstance(n, ast.Import):
                found |= {a.name.split('.')[0] for a in n.names}
            elif isinstance(n, ast.ImportFrom) and n.module and not n.level:
                found.add(n.module.split('.')[0])
        return found & mods
    seen, todo = set(), ['build']
    while todo:
        if (m := todo.pop()) not in seen:
            seen.add(m)
            todo += imports(m)
    assert {'uac1', 'iroh'} <= seen  # the check sees what it is for
    assert seen <= affected.BUILD_INPUTS | affected.IMAGE_INPUTS, sorted(seen - affected.BUILD_INPUTS - affected.IMAGE_INPUTS)


def test_payload_change_runs_the_unit_rules_over_every_patch_file(tmp_path):
    """feat/row-press changed patch/tilt.c and its plan left out test_theme.py, whose rule (no literal colour
    where a token exists) covers every patch/*.c: the queue's batch caught it, and blamed another branch."""
    root = tree(tmp_path, {'patch/tilt.c': 'int tilt_now(void) {\n  return 0;\n}\n', 'test/fw/test_x.py': '',
                           'test/unit/test_xiph.py': "src = ROOT/'patch/xiph.c'\n"})
    assert affected.plan_for({'patch/tilt.c': {1}}, root).as_dict()['unit'] == ['test_art.py', 'test_docs.py',
                                                                              'test_theme.py']
    assert affected.plan_for({'patch/theme.h': {1}}, root).as_dict()['unit'] == ['test_docs.py', 'test_theme.py']
    assert affected.plan_for({'patch/xiph.c': {1}}, root).as_dict()['unit'] == ['test_art.py', 'test_docs.py',
                                                                              'test_theme.py', 'test_xiph.py']


def test_a_python_change_plans_the_mypy_suite_that_checks_it(tmp_path):
    root = tree(tmp_path, {'test/unit/test_art.py': '', 'test/unit/test_shelf.py': ''})
    other = lambda path: affected.plan_for({path: {1}}, root).as_dict()['other']  # noqa: E731
    assert 'mypy-tools' in other('test/unit/test_art.py') and 'mypy-fw' not in other('test/unit/test_art.py')
    assert 'mypy-tools' in other('tools/shelf.py') and 'mypy-tools' in other('tools/ci/gate.py')
    assert 'mypy-fw' in other('test/fw/test_art_zoom.py') and 'mypy-tools' not in other('test/fw/test_art_zoom.py')
    assert 'mypy-fw' in other('test/fw/helpers.py')
    assert {'mypy-tools', 'mypy-fw'} <= set(other('pyproject.toml'))  # the mypy config; also an everything plan
    for path in ('patch/seek.c', 'docs/ipod.md', 'tools/pwm.c', 'test/fw/fixture.json'):
        assert not {'mypy-tools', 'mypy-fw'} & set(other(path)), path


def test_mypy_suites_run_as_the_gate_runs_them(tmp_path):
    plan = affected.Plan()
    plan.other.update({'mypy-tools', 'mypy-fw'})
    for name, suite in affected.suites(plan, None, 'py', tmp_path).items():
        assert suite.argv == gate.full_suite(name, 'py', tmp_path)().argv
    every = affected.Plan()
    every.everything('x')
    assert {'mypy-tools', 'mypy-fw'} <= every.other


def test_a_bisection_run_keeps_the_mypy_suites():
    whole = {'unit': [], 'fw': [], 'patch': [], 'other': ['mypy-fw', 'test/gone.py'], 'why': []}
    assert merge_batch.widen(None, whole, lambda f: False)['other'] == ['mypy-fw']


def test_every_folder_a_unit_test_globs_is_in_scanners():
    """A test/unit file that reads every file of patch/, tools/ or test/ must be in affected.SCANNERS for that
    folder, or a change there would skip its rule."""
    root = pathlib.Path(affected.__file__).resolve().parents[2]
    pattern = re.compile(r"(?:ROOT|parents\[2\])\s*/\s*'(patch|tools|test)'\)\s*\.r?glob\(")
    found = {(m[1] + '/', f.name) for f in (root/'test/unit').glob('test_*.py') for m in pattern.finditer(f.read_text())}
    assert ('patch/', 'test_theme.py') in found  # the pattern still sees the rule that was missed
    assert found <= {(folder, test) for folder, _, test in affected.SCANNERS}


def handed(monkeypatch):
    """Stands in for gate.run under affected.run: each call's (suites, build, kwargs), every suite passing."""
    calls: list = []

    def fake(suites, build_args, logs, py, build=None, **kw):
        calls.append((suites, build, kw))
        return {n: {'rc': 0, 'seconds': 0, 'log': ''} for n in suites}
    monkeypatch.setattr(affected.gate, 'run', fake)
    return calls


def test_coverflow_suite_is_run_with_an_argv_it_accepts(monkeypatch):
    # test/coverflow.py compiles its own host build and takes no BUILD; the plan once handed it one and argparse
    # refused it, so the suite had not run for a day when anyone noticed.
    import subprocess
    root = pathlib.Path(__file__).resolve().parents[2]
    real_run, calls = subprocess.run, handed(monkeypatch)
    plan = affected.Plan()
    plan.other.add('test/coverflow.py')
    assert affected.run(plan, '/some/build', 'python3') == 0
    assert calls[-1][0]['test/coverflow.py'].command('/some/build')[-1:] == ['test/coverflow.py']  # nothing after it
    assert gate.other_suite('test/coverflow.py', 'python3').argv[-1:] == ['test/coverflow.py']
    # and the suite does refuse an extra argument, so the line above is the one that matters
    refused = real_run([sys.executable, str(root/'test/coverflow.py'), '/some/build'], capture_output=True, text=True)
    assert refused.returncode == 2 and 'unrecognized arguments' in refused.stderr


def test_every_host_script_has_a_declared_invocation():
    """A plan can name any test/X.py: each is either run with an argv its parser takes, or skipped with a reason."""
    root = pathlib.Path(affected.__file__).resolve().parents[2]
    scripts = {f'test/{p.name}' for p in (root/'test').glob('*.py')}
    assert scripts <= set(gate.SCRIPTS), sorted(scripts - set(gate.SCRIPTS))
    assert set(gate.SCRIPTS) <= scripts, 'a table row for a script that is gone'
    for name, script in (('patch', 'test/patch.py'), ('test/playback.py',) * 2, ('test/build.py',) * 2,
                         ('test/peq.py',) * 2, ('test/headset_io.py',) * 2):
        # a suite gate.py defines whole is run with the same argv when a plan names its script
        assert gate.full_suite(name, 'py', pathlib.Path('/x'))().argv == gate.other_suite(script, 'py').argv, name


def test_scripts_are_run_the_way_their_parsers_take_them():
    py = 'python3'
    assert gate.other_suite('test/chapters.py', py).argv == [py, 'test/chapters.py']  # argparse refuses BUILD
    assert gate.other_suite('test/bluealsa.py', py).argv == [py, 'test/bluealsa.py', '{build}']
    aic = gate.other_suite('test/aic.py', py)
    assert aic.argv == [py, 'test/aic.py', '{stockfs}'] and aic.needs_build
    assert gate.other_suite('test/rotator.py', py).argv[-1] == '{stockfs}/module_driver/soc_rotator.ko'


def test_a_script_the_gate_cannot_run_is_skipped_with_its_reason(capsys, monkeypatch):
    for path in ('test/server_catalogue.py', 'test/suite_source.py', 'test/module_mips.py'):
        assert gate.other_suite(path, 'python3') is None, path
        assert gate.SCRIPTS[path], path  # the reason
    plan = affected.Plan()
    plan.other.add('test/server_catalogue.py')
    calls = handed(monkeypatch)
    assert affected.run(plan, '/b', 'python3') == 0
    assert calls == [] and 'skipped' in capsys.readouterr().out
    assert gate.suites_for({'unit': [], 'fw': [], 'patch': [], 'other': ['test/server_catalogue.py']}, None, 'python3',
                           pathlib.Path('/nowhere')) == {}
    assert 'test/server_catalogue.py' in capsys.readouterr().out


def test_car_remote_end_to_end_runs_on_a_pooled_emulator_with_the_build():
    """test/coda_rc_e2e.py leases its own emulator (--pool), so the gate runs it on the plan's build, under q2-heavy."""
    suite = gate.other_suite('test/coda_rc_e2e.py', 'python3')
    assert suite.argv == ['python3', 'test/coda_rc_e2e.py', '--pool', '{build}'] and suite.needs_build and suite.heavy


def test_a_stock_script_is_run_on_the_builds_stock_files(monkeypatch, tmp_path):
    calls = []
    def fake_unsquashfs(cmd, **kw):
        calls.append(cmd)
        pathlib.Path(cmd[cmd.index('-d') + 1]).mkdir(parents=True)
        return gate.subprocess.CompletedProcess(cmd, 0)
    monkeypatch.setattr(gate.subprocess, 'run', fake_unsquashfs)
    monkeypatch.setattr(gate, 'STOCKFS_CACHE', tmp_path)
    build = tmp_path/'b'
    build.mkdir()
    (build/'stock.squashfs').write_bytes(b'x')
    cmd = gate.other_suite('test/pwm.py', 'python3').command(str(build))
    assert cmd[-1].startswith(str(tmp_path)) and '{' not in cmd[-1]
    assert calls[0][0] == 'unsquashfs' and str(build/'stock.squashfs') in calls[0]


def test_a_coverflow_source_plans_the_coverflow_suite(tmp_path):
    # test/coverflow.py builds these on the host, so a change to any of them plans it, not only one to the script.
    for path in ('patch/coverflow.c', 'patch/coverflow_loader.c', 'patch/coverflow.h', 'patch/home.c',
                 'patch/page_parallax.c', 'patch/playlist_art.c'):
        assert 'test/coverflow.py' in affected.plan_for({path: {1}}, tmp_path).as_dict()['other'], path


def test_an_unrelated_payload_file_does_not_plan_the_coverflow_suite(tmp_path):
    assert 'test/coverflow.py' not in affected.plan_for({'patch/volume.c': {1}}, tmp_path).as_dict()['other']


def test_a_change_to_what_sitenotes_reads_plans_the_stock_sites_check(tmp_path):
    """tools/stock_sites.tsv went stale on the trunk (about 30 sites added by branches, none in the table) because
    nothing ran docs/rebasing/sitenotes.py --check but a release's package step. It reads patch/ and tools/, so a
    change to either, to the tables or to the script plans test/stock_sites.py, which runs it."""
    for path in ('tools/build.py', 'patch/bt_ui.c', 'tools/bt_silence.S', 'tools/stock_map.tsv', 'tools/stock_sites.tsv',
                 'docs/rebasing/sitenotes.py'):
        assert 'test/stock_sites.py' in affected.plan_for({path: {1}}, tmp_path).as_dict()['other'], path
    for path in ('docs/ipod.md', 'tools/ci/gate.py', 'test/unit/test_art.py'):
        assert 'test/stock_sites.py' not in affected.plan_for({path: {1}}, tmp_path).as_dict()['other'], path
    every = affected.Plan()
    every.everything('x')
    assert 'test/stock_sites.py' in every.other and 'test/stock_sites.py' in gate.FULL
    assert gate.other_suite('test/stock_sites.py', 'py').argv == ['py', 'test/stock_sites.py']


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
    assert run.index('CODA=()') < run.index('IROH=()') < run.index('# Q2EMU_CORE=1')
    assert run.endswith('    "${CODA[@]}" "${IROH[@]}" "${BOOT[@]}"\n')
    assert 'exec /emu/coda-rc.real --port $Q2EMU_CODA_PORT' in run and '--ro-bind "$S/coda-rc" /usr/bin/coda-rc' in run
    assert '--ro-bind "$S/q2-iroh" /usr/bin/q2-iroh' in run
    emu_pool.fit(tmp_path, 3)  # a second time changes nothing
    assert (tmp_path/'q2emu').read_text() == script and (tmp_path/'run.sh').read_text() == run


def test_a_pool_copys_q2_iroh_forwards_to_its_own_daemon(tmp_path):
    """docs/remote.md#car-remote-across-networks: demo starts q2-iroh with no --forward, which would reach TCP 7720,
    another emulator's daemon or the user's. The wrapper run.sh writes puts this copy's port first after serve; a copy
    fitted before q2-iroh existed gets it too."""
    import subprocess
    (tmp_path/'q2emu').write_text(Q2EMU)
    (tmp_path/'run.sh').write_text(RUN.replace(emu_pool.RUN_BEFORE, emu_pool.CODA_BLOCK + emu_pool.RUN_BEFORE)
                                   .replace(emu_pool.RUN_BWRAP, '    "${CODA[@]}" "${BOOT[@]}"'))
    emu_pool.fit(tmp_path, 3)
    run = (tmp_path/'run.sh').read_text()
    assert run.endswith('    "${CODA[@]}" "${IROH[@]}" "${BOOT[@]}"\n')
    block = run[run.index('IROH=()'):run.index('# Q2EMU_CORE=1')]
    (tmp_path/'rootfs/usr/bin').mkdir(parents=True)
    (tmp_path/'rootfs/usr/bin/q2-iroh').write_text('real')
    (tmp_path/'state').mkdir()
    out = subprocess.run(['bash', '-c', f'E={tmp_path}; S={tmp_path}/state; Q2EMU_CODA_PORT=7703\n{block}\necho "${{IROH[@]}}"'],
                         capture_output=True, text=True, check=True).stdout
    assert out.strip() == f'--ro-bind {tmp_path}/state/q2-iroh /usr/bin/q2-iroh'
    wrapper = (tmp_path/'state/q2-iroh').read_text()
    assert wrapper == '#!/bin/sh\nc=$1; shift\nexec /emu/q2-iroh.real "$c" --forward 127.0.0.1:7703 "$@"\n'


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
    # the table lists suites as they finished, and u (rc 1) races the build (rc 3): the set is what is fixed
    failed = gate.table(results, 1).rsplit('FAILED: ', 1)[1].split(', ')
    assert sorted(failed) == ['build', 'p', 'u']


def test_gate_plan_that_runs_everything_is_the_merge_tier(tmp_path):
    every = {'unit': 'all', 'fw': 'all', 'patch': 'all', 'other': ['test/build.py'], 'why': []}
    assert set(gate.suites_for(every, [], 'py', tmp_path)) == set(gate.MERGE)
    assert set(gate.suites_for(None, [], 'py', tmp_path)) == set(gate.FULL)  # no plan: a release's gate
    assert set(gate.suites_for(None, [], 'py', tmp_path, 'post')) == {'fw-asan', 'unit-cov', 'smoke', 'leak-hunt'}
    assert gate.suites_for(every, [], 'py', tmp_path)['patch'].argv == ['py', 'test/patch.py', '{build}']
    docs = {'unit': ['test_docs.py'], 'fw': [], 'patch': [], 'other': [], 'why': []}
    assert list(gate.suites_for(docs, [], 'py', tmp_path)) == ['unit']
    # --only: the plan's unit as the plan has it, fw-asan (only the whole gate has it) whole, patch (the
    # plan lacks it: its changes cannot reach it) not at all
    only = gate.suites_for(docs, ['fw-asan', 'unit', 'patch'], 'py', tmp_path)
    assert list(only) == ['fw-asan', 'unit']
    assert only['fw-asan'].argv[-1] == '--asan' and only['unit'].argv[-1] == 'test/unit/test_docs.py'


def test_an_everything_plan_runs_every_suite_a_narrower_plan_could(tmp_path):
    """fix/full-bleed-windows (d553a94b) changed patch/page_parallax.c and enough else that its plan ran unit, fw
    and patch whole: the gate ran the merge tier for it and dropped the plan's test/coverflow.py, which that
    change broke, and the trunk was red unseen until the next coverflow change (TIP_RED 2026-11-18 12:51)."""
    root = pathlib.Path(affected.__file__).resolve().parents[2]
    changes = {'patch/page_parallax.c': {1}, 'tools/build.py': {1}, 'tools/stock_map.tsv': {1}}  # three of its 44
    plan = json.loads(affected.plan_for(changes, root).to_json())
    assert gate.everything(plan) and 'test/coverflow.py' in plan['other']
    assert 'test/coverflow.py' in gate.suites_for(plan, [], 'py', tmp_path)
    # and so for every suite a rule plans: each narrower plan's own suites are in what an everything plan runs
    narrow = {'patch/coverflow.c': {1}, 'player/main.c': {1}, 'tools/ci/gate.py': {1}, 'test/patch_suite/00_machine.py': set(),
              'tools/build.py': {1}, 'pyproject.toml': set(), 'test/fw/test_home.py': {1}}
    names: set = set()
    for path, lines in narrow.items():
        names |= set(gate.suites_for(json.loads(affected.plan_for({path: lines}, root).to_json()), [], 'py', tmp_path))
    names = {n for n in names if not n.startswith(gate.COV)}  # a module's coverage rides with its change, as test/X.py does
    every = affected.Plan()
    every.everything('test')
    for full in (gate.suites_for(json.loads(every.to_json()), [], 'py', tmp_path), gate.suites_for(None, [], 'py', tmp_path)):
        assert names <= set(full), sorted(names - set(full))
    assert 'test/player/run.py' not in gate.MERGE  # longer than the whole tier: a batch runs it when planned
    # a suite only its own change plans (test/X.py: itself) rides along with an everything plan too
    plan = json.loads(affected.plan_for({'Makefile.new': set(), 'test/chapters.py': {1}}, root).to_json())
    assert gate.everything(plan) and 'test/chapters.py' in gate.suites_for(plan, [], 'py', tmp_path)
    assert 'smoke' not in gate.suites_for(plan, [], 'py', tmp_path)  # still the post tier's on a batch


COV_PYPROJECT = '[tool.coverage.run]\nsource = ["tools"]\nbranch = true\n[tool.coverage.report]\nfail_under = 99.5\n'
COV_MODULE = 'def flash(x):\n    if x:\n        return 1\n    return 2\n\n\ndef keyboard_check(x):\n    if x == "a":\n        return "A"\n    return "?"\n'
COV_TEST = 'import q2flash\n\n\ndef test_flash():\n    assert q2flash.flash(1) == 1\n    assert q2flash.flash(0) == 2\n'


def cov_tree(tmp_path, test=COV_TEST):
    return tree(tmp_path, {'pyproject.toml': COV_PYPROJECT, 'tools/q2flash.py': COV_MODULE,
                           'test/unit/test_q2flash.py': test, 'test/unit/test_other.py': 'x = 1\n',
                           'test/unit/conftest.py': "import sys\nsys.path.insert(0, 'tools')\n"})


def test_a_changed_tools_module_plans_its_coverage_check(tmp_path):
    """tools/build.py's keyboard check, tools/q2flash.py and tools/iroh.py each landed with test/unit covering
    under the floor and turned the post tier's unit-cov red: no worker's run and no merge batch ran the floor."""
    root = cov_tree(tmp_path)
    (root/'tools/ci').mkdir()
    (root/'tools/ci/gate.py').write_text('')
    plan = affected.plan_for({'tools/q2flash.py': {1, 2}}, root).as_dict()
    assert 'cov:tools/q2flash.py' in plan['other']
    assert any('covcheck' in w for w in plan['why'])
    # a test edit counts, as a dropped test lowers the module's coverage; tools/ci and a deleted module do not
    assert 'cov:tools/q2flash.py' in affected.plan_for({'test/unit/test_q2flash.py': {1}}, root).as_dict()['other']
    for path in ('tools/ci/gate.py', 'tools/gone.py', 'test/unit/test_gone.py', 'docs/building.md'):
        assert not any(o.startswith('cov:') for o in affected.plan_for({path: {1}}, root).as_dict()['other']), path
    # an everything plan keeps it beside the merge tier, and the gate runs covcheck.py on the module
    plan = json.loads(affected.plan_for({'tools/q2flash.py': {1}, 'Makefile.new': set()}, root).to_json())
    suite = gate.suites_for(plan, [], 'py', tmp_path)['cov:tools/q2flash.py']
    assert gate.everything(plan) and suite.argv[-3:] == ['tools/q2flash.py', '--python', 'py']
    # a bisection run keeps it while the module exists at that head
    whole = {'unit': [], 'fw': [], 'patch': [], 'other': ['cov:tools/q2flash.py', 'cov:tools/gone.py'], 'why': []}
    assert merge_batch.widen(None, whole, lambda f: f == 'tools/q2flash.py')['other'] == ['cov:tools/q2flash.py']


def test_coverage_check_selects_the_tests_that_name_the_module(tmp_path):
    root = cov_tree(tmp_path)
    (root/'test/unit/test_flow.py').write_text('import tools.q2flash\n')
    (root/'test/unit/test_files.py').write_text("P = 'tools/q2flash.py'\n")
    (root/'test/unit/test_near.py').write_text('import q2flash_extra\n')
    assert covcheck.tests_for('tools/q2flash.py', root) == ['test_files.py', 'test_flow.py', 'test_q2flash.py']


def test_coverage_check_rejects_a_module_under_the_floor_naming_its_lines(tmp_path, capfd):
    root = cov_tree(tmp_path)
    assert covcheck.floor(root) == 99.5
    assert covcheck.check('tools/q2flash.py', sys.executable, root) == 1
    out = capfd.readouterr().out
    assert 'Missing' in out and '8-10' in out  # the table names the uncovered lines
    last = out.strip().splitlines()[-1]
    assert last.startswith('covcheck FAILED: tools/q2flash.py 58%') and 'missing 8-10' in last, last
    # tested, it passes
    (root/'test/unit/test_q2flash.py').write_text(COV_TEST + '    assert q2flash.keyboard_check("a") == "A"\n'
                                                  '    assert q2flash.keyboard_check("b") == "?"\n')
    assert covcheck.check('tools/q2flash.py', sys.executable, root) == 0


def test_coverage_check_rejects_a_module_no_test_names(tmp_path, capfd):
    root = cov_tree(tmp_path)
    (root/'tools/iroh.py').write_text('def f():\n    return 1\n')
    assert covcheck.check('tools/iroh.py', sys.executable, root) == 1
    assert 'no test/unit file imports it or names it' in capfd.readouterr().out


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
def test_a_reserved_lease_is_never_reclaimed_or_handed_on(tmp_path, monkeypatch):
    """The user's rig (slot 2) was handed to an agent once its lease ran out (2026-11-16 18:30): a reserved lease
    stays with its holder past its end, and `get` by another name takes a free copy or waits."""
    monkeypatch.setattr(emu_pool, 'STATE', tmp_path)
    monkeypatch.setattr(emu_pool, 'LEASES', tmp_path/'leases.json')
    monkeypatch.setattr(emu_pool, 'K', 2)
    monkeypatch.setattr(emu_pool, 'q2emu', lambda *a: type('P', (), {'returncode': 0, 'stdout': 'running', 'stderr': ''})())
    monkeypatch.setattr(emu_pool, 'make_copy', lambda k: None)
    monkeypatch.setattr(emu_pool, 'reset_card', lambda k: None)
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


def card_tree(root, files):
    for rel, text in files.items():
        (root/rel).parent.mkdir(parents=True, exist_ok=True)
        (root/rel).write_text(text)


def test_each_lease_starts_with_the_card_as_the_fixture_hardlinked(tmp_path, monkeypatch):
    """A worker left a measured-lengths file on slot 3's card and others added .subsonic folders and switched
    files (2026-11-18); the next smoke run found no songs and exited 3. A lease now rebuilds its card from one fixture."""
    pool = tmp_path/'pool'
    monkeypatch.setattr(emu_pool, 'STATE', tmp_path/'state')
    monkeypatch.setattr(emu_pool, 'LEASES', tmp_path/'state/leases.json')
    monkeypatch.setattr(emu_pool, 'K', 2)
    monkeypatch.setattr(emu_pool, 'HOME', pool)
    monkeypatch.delenv('Q2_EMU_CARD', raising=False)
    monkeypatch.setattr(emu_pool, 'q2emu', lambda *a: type('P', (), {'returncode': 0, 'stdout': '', 'stderr': ''})())
    monkeypatch.setattr(emu_pool, 'make_copy', lambda k: None)
    source = tmp_path/'source'
    card_tree(source, {'Music/A/B/01 one.flac': 'one', 'Music/A/B/02 two.flac': 'two', 'Test/t.flac': 'tt'})
    (source/'Empty').mkdir()
    fx = emu_pool.make_fixture(source)
    assert fx == tmp_path/'state/card' and (fx/'Empty').is_dir()
    card = emu_pool.copy_dir(1)/'state/mmc'
    card_tree(card, {'Music/A/B/01 one.flac': 'DIRTY', 'Music/A/B/03 added.flac': 'x', '.subsonic/log.txt': 'x',
                     'lengths.cache': 'x', 'Test/t.flac': 'switched'})
    data = emu_pool.copy_dir(1)/'state/data'
    card_tree(data, {'config.ini': '[SYSSET]\nBLUETOOTH=0\n'})
    assert emu_pool.get('/b', 'w', 1, wait=False, avoid=(2,)) == 1

    def tree(root):
        return sorted(str(p.relative_to(root)) for p in root.rglob('*'))
    assert tree(card) == tree(fx)                                       # the extra files and folders are gone
    for rel in ('Music/A/B/01 one.flac', 'Music/A/B/02 two.flac', 'Test/t.flac'):
        assert (card/rel).stat().st_ino == (fx/rel).stat().st_ino       # hardlinks: no data copied
        assert (card/rel).stat().st_nlink >= 3                          # the source's, the fixture's and the card's
    assert (card/'Music/A/B/01 one.flac').read_text() == 'one' and (card/'Empty').is_dir()
    assert (data/'config.ini').read_text() == '[SYSSET]\nBLUETOOTH=0\n'  # /mnt/data is left alone
    assert emu_pool.card_songs(1) == 3
    assert not os.access(fx/'Test/t.flac', os.W_OK) or os.geteuid() == 0  # a write in place fails rather than reach every card


def test_each_lease_starts_with_the_library_that_lists_the_fixtures_songs(tmp_path, monkeypatch):
    """Slot 4's database.db still listed Test Artist's Song 1 and Song 2 after the card was reset from the 13-file
    fixture (2026-11-18): Library > Songs > Song 1 > Centre said File Not Found and opened no Now Playing, read as
    a firmware regression. A lease now starts with the library the fixture was scanned into, a copy stock may write."""
    monkeypatch.setattr(emu_pool, 'STATE', tmp_path/'state')
    monkeypatch.setattr(emu_pool, 'LEASES', tmp_path/'state/leases.json')
    monkeypatch.setattr(emu_pool, 'K', 2)
    monkeypatch.setattr(emu_pool, 'HOME', tmp_path/'pool')
    monkeypatch.delenv('Q2_EMU_CARD', raising=False)
    monkeypatch.setattr(emu_pool, 'q2emu', lambda *a: type('P', (), {'returncode': 0, 'stdout': '', 'stderr': ''})())
    monkeypatch.setattr(emu_pool, 'make_copy', lambda k: None)
    source = tmp_path/'source'
    card_tree(source, {'Music/A/B/01 one.flac': 'one'})
    (tmp_path/'scanned.db').write_text('songtable: one')
    emu_pool.make_fixture(source, library=tmp_path/'scanned.db')
    assert emu_pool.library_fixture().read_text() == 'songtable: one'
    data = emu_pool.copy_dir(1)/'state/data'
    card_tree(data, {'database.db': 'songtable: Song 1, Song 2', 'config.ini': 'ini'})
    assert emu_pool.get('/b', 'w', 1, wait=False, avoid=(2,)) == 1
    db = data/'database.db'
    assert db.read_text() == 'songtable: one' and (data/'config.ini').read_text() == 'ini'
    assert db.stat().st_ino != emu_pool.library_fixture().stat().st_ino and os.access(db, os.W_OK)  # a copy: stock writes it
    emu_pool.put(1)
    emu_pool.make_fixture(source)                                       # a new card with no library: none that lists the old one
    assert not emu_pool.library_fixture().exists()
    db.write_text('the last lease')
    assert emu_pool.get('/b', 'w', 1, wait=False, avoid=(2,)) == 1
    assert db.read_text() == 'the last lease'                           # nothing to put back: left as it is


def test_each_lease_starts_with_the_settings_and_per_run_state_of_the_data_fixture(tmp_path, monkeypatch):
    """A scripted tour left Light Mode, Smart Awake and Song Length changed in slot 1's config.ini and the next
    workers' runs started from them (2026-11-18). A lease now puts state/data's settings back from a data fixture and
    drops the per-run files; the pool's own files and the user's Wi-Fi stay, and the library is the library fixture's."""
    monkeypatch.setattr(emu_pool, 'STATE', tmp_path/'state')
    monkeypatch.setattr(emu_pool, 'LEASES', tmp_path/'state/leases.json')
    monkeypatch.setattr(emu_pool, 'K', 2)
    monkeypatch.setattr(emu_pool, 'HOME', tmp_path/'pool')
    monkeypatch.delenv('Q2_EMU_CARD', raising=False)
    monkeypatch.delenv('Q2_EMU_DATA', raising=False)
    monkeypatch.setattr(emu_pool, 'q2emu', lambda *a: type('P', (), {'returncode': 0, 'stdout': '', 'stderr': ''})())
    monkeypatch.setattr(emu_pool, 'make_copy', lambda k: None)
    source = tmp_path/'source'
    card_tree(source, {'Music/one.flac': 'one'})
    emu_pool.make_fixture(source)
    clean = '[SYSSET]\nBLUETOOTH=0\nLIGHTMODE=0\n[IPOD]\nKEEPLIT=1\nSONGLENGTH=0\n'
    ini = tmp_path/'seed/config.ini'
    card_tree(tmp_path/'seed', {'config.ini': clean})
    fx = emu_pool.make_data_fixture(tmp_path/'seed')
    assert fx == tmp_path/'state/data' and (fx/'config.ini').read_text() == clean
    assert not os.access(fx/'config.ini', os.W_OK) or os.geteuid() == 0
    data = emu_pool.copy_dir(1)/'state/data'
    card_tree(data, {'config.ini': '[SYSSET]\nBLUETOOTH=1\nLIGHTMODE=1\n[IPOD]\nKEEPLIT=0\nSONGLENGTH=1\n',
                     'memoryfile': 'old', 'q2-state': 'x', 'ringnav-plays': 'x', 'ringnav-queue': 'x', 'ringnav-ratings': 'x',
                     'wpa_supplicant.conf': 'wifi', 'q2-defaults': '1\n', 'coda-rc/x': 'pool'})
    assert emu_pool.get('/b', 'w', 1, wait=False, avoid=(2,)) == 1
    assert (data/'config.ini').read_text() == clean                     # settings back to the fixture's
    assert os.access(data/'config.ini', os.W_OK) and (data/'config.ini').stat().st_ino != (fx/'config.ini').stat().st_ino
    assert sorted(p.name for p in data.iterdir()) == ['coda-rc', 'config.ini', 'q2-defaults', 'wpa_supplicant.conf']
    (data/'config.ini').write_text('dirty again')                       # a lease of the same name and build, running
    monkeypatch.setattr(emu_pool, 'q2emu', lambda *a: type('P', (), {'returncode': 0, 'stdout': 'running', 'stderr': ''})())
    assert emu_pool.get('/b', 'w', 1, wait=False, avoid=(2,)) == 1
    assert (data/'config.ini').read_text() == 'dirty again'             # reused: not reset
    emu_pool.put(1)
    monkeypatch.setattr(emu_pool, 'q2emu', lambda *a: type('P', (), {'returncode': 0, 'stdout': '', 'stderr': ''})())
    assert emu_pool.get('/b', 'w', 1, wait=False, avoid=(2,)) == 1
    assert (data/'config.ini').read_text() == clean                     # a fresh lease: reset again
    slot2 = emu_pool.copy_dir(2)/'state/data'
    card_tree(slot2, {'config.ini': 'user'})
    assert (slot2/'config.ini').read_text() == 'user'                   # slot 2 is never leased here, so never touched


def test_a_card_is_not_reset_when_a_lease_is_reused_or_reserved_and_a_missing_fixture_is_said_or_reserved_and_a_missing_fixture_is_said(tmp_path, monkeypatch):
    monkeypatch.setattr(emu_pool, 'STATE', tmp_path/'state')
    monkeypatch.setattr(emu_pool, 'LEASES', tmp_path/'state/leases.json')
    monkeypatch.setattr(emu_pool, 'K', 2)
    monkeypatch.setattr(emu_pool, 'HOME', tmp_path/'pool')
    monkeypatch.delenv('Q2_EMU_CARD', raising=False)
    state = {'out': 'running'}
    monkeypatch.setattr(emu_pool, 'q2emu', lambda *a: type('P', (), {'returncode': 0, 'stdout': state['out'], 'stderr': ''})())
    monkeypatch.setattr(emu_pool, 'make_copy', lambda k: None)
    for k in (1, 2):
        card_tree(emu_pool.copy_dir(k)/'state/mmc', {'mine.txt': 'keep'})
    b = str(pathlib.Path('/b').resolve())
    (tmp_path/'state').mkdir()
    (tmp_path/'state/leases.json').write_text(json.dumps({
        '1': {'name': 'w', 'build': b, 'since': 0, 'until': time.time() + 99},
        '2': {'name': 'user', 'build': b, 'since': 0, 'until': 1.0, 'reserved': True}}))
    assert emu_pool.get('/b', 'w', 1, wait=False) == 1                  # reused, running: the worker's card stays
    assert (emu_pool.copy_dir(1)/'state/mmc/mine.txt').exists()
    state['out'] = ''
    assert emu_pool.get('/b', 'user', 1, wait=False) == 2               # the user's rig, restarted: its card stays too
    assert (emu_pool.copy_dir(2)/'state/mmc/mine.txt').exists()
    emu_pool.put(1)                                                     # no fixture: said, and no lease left
    try:
        emu_pool.get('/c', 'w2', 1, wait=False)
    except SystemExit as e:
        assert 'no card fixture' in str(e)
    else:
        raise AssertionError('a lease booted on a card nothing reset')
    assert '1' not in json.loads((tmp_path/'state/leases.json').read_text())


def pool_on_another_disk(tmp_path, monkeypatch, other):
    """The layout of 2026-11-20: ~/q2-emu-poolK symlinks onto other (the scratch disk), the fixture left in tmp_path."""
    home = tmp_path/'home'
    for k in (1, 2):
        (other/f'q2-emu-pool{k}').mkdir(parents=True)
        home.mkdir(exist_ok=True)
        (home/f'q2-emu-pool{k}').symlink_to(other/f'q2-emu-pool{k}')
    monkeypatch.setattr(emu_pool, 'STATE', tmp_path/'state')
    monkeypatch.setattr(emu_pool, 'LEASES', tmp_path/'state/leases.json')
    monkeypatch.setattr(emu_pool, 'K', 2)
    monkeypatch.setattr(emu_pool, 'HOME', home)
    monkeypatch.delenv('Q2_EMU_CARD', raising=False)
    monkeypatch.delenv('Q2_EMU_DATA', raising=False)
    monkeypatch.setattr(emu_pool, 'q2emu', lambda *a: type('P', (), {'returncode': 0, 'stdout': '', 'stderr': ''})())
    monkeypatch.setattr(emu_pool, 'make_copy', lambda k: None)
    source = tmp_path/'source'
    card_tree(source, {'Music/A/B/01 one.flac': 'one', 'Test/t.flac': 'tt'})
    card_tree(tmp_path/'datasrc', {'config.ini': '[SYSSET]\nBLUETOOTH=0\n'})
    return emu_pool.make_fixture(source), emu_pool.make_data_fixture(tmp_path/'datasrc')


def check_card_copied(fx):
    card = emu_pool.copy_dir(1)/'state/mmc'
    assert sorted(str(p.relative_to(card)) for p in card.rglob('*')) == sorted(str(p.relative_to(fx)) for p in fx.rglob('*'))
    assert (card/'Music/A/B/01 one.flac').read_text() == 'one' and emu_pool.card_songs(1) == 2
    assert not os.access(card/'Test/t.flac', os.W_OK) or os.geteuid() == 0  # read-only like the fixture's
    assert (emu_pool.copy_dir(1)/'state/data/config.ini').read_text() == '[SYSSET]\nBLUETOOTH=0\n'


def test_a_lease_copies_the_card_when_the_pool_is_on_another_filesystem(tmp_path, monkeypatch):
    """After the pool copies moved to /mnt/wsl/q2scratch behind symlinks (2026-11-20), every `get` failed with
    "Invalid cross-device link": the fixture stayed on / and a hardlink cannot cross filesystems. A file that
    cannot be linked is now copied; any other link error still stops the lease."""
    real = os.link

    def cross(src, dst):
        if pathlib.Path(dst).resolve().is_relative_to(tmp_path/'scratch'):
            raise OSError(errno.EXDEV, os.strerror(errno.EXDEV))
        return real(src, dst)
    monkeypatch.setattr(os, 'link', cross)
    fx, _ = pool_on_another_disk(tmp_path, monkeypatch, tmp_path/'scratch')
    assert emu_pool.get('/b', 'w', 1, wait=False, avoid=(2,)) == 1
    check_card_copied(fx)
    assert (emu_pool.copy_dir(1)/'state/mmc/Test/t.flac').stat().st_ino != (fx/'Test/t.flac').stat().st_ino

    def denied(src, dst):
        raise OSError(errno.EPERM, os.strerror(errno.EPERM))
    monkeypatch.setattr(os, 'link', denied)
    emu_pool.put(1)
    try:
        emu_pool.get('/b', 'w', 1, wait=False, avoid=(2,))
    except SystemExit as e:
        assert 'cannot hardlink' in str(e)
    else:
        raise AssertionError('a link error other than EXDEV was copied over')


def test_a_lease_copies_the_card_across_two_real_filesystems(tmp_path, monkeypatch):
    """The same, on two real filesystems where this machine has them (/dev/shm beside the test's tmp)."""
    shm = pathlib.Path('/dev/shm')
    if not shm.is_dir() or not os.access(shm, os.W_OK) or shm.stat().st_dev == tmp_path.stat().st_dev:
        pytest.skip('no second writable filesystem')
    other = pathlib.Path(tempfile.mkdtemp(dir=shm, prefix='q2-emu-pool-test-'))
    try:
        fx, _ = pool_on_another_disk(tmp_path, monkeypatch, other)
        assert emu_pool.get('/b', 'w', 1, wait=False, avoid=(2,)) == 1
        check_card_copied(fx)
    finally:
        shutil.rmtree(other)


def test_smoke_and_q2live_find_the_sandbox_of_a_copy_reached_through_a_symlink(tmp_path, monkeypatch):
    """q2emu binds its copy's rootfs by the real path (readlink -f); with ~/q2-emu-poolK a symlink onto the
    scratch disk (2026-11-20) smoke.py looked for the symlink's path, found no demo and failed every boot."""
    real = tmp_path/'scratch/q2-emu-pool1'
    real.mkdir(parents=True)
    (tmp_path/'home').mkdir()
    (tmp_path/'home/q2-emu-pool1').symlink_to(real)
    monkeypatch.setattr(emu_pool, 'HOME', tmp_path/'home')
    copy = emu_pool.copy_dir(1)
    assert copy.is_symlink()
    # a stand-in for run.sh's bwrap (its argv names the resolved rootfs) whose child looks like qemu running demo
    child = "exec -a 'qemu-mipsel-binfmt-P /release/bin/de''mo' sleep 30"  # split: the parent's own argv must not match
    sandbox = subprocess.Popen([sys.executable, '-c', 'import subprocess, sys; subprocess.run(["bash", "-c", sys.argv[1]])',
                                child, '--die-with-parent', '--bind', f'{real}/rootfs', '/'], start_new_session=True)
    try:
        end, pid = time.monotonic() + 10, None
        while pid is None and time.monotonic() < end:
            pid = smoke.demo_pid(copy)
            time.sleep(.05)
        assert pid is not None and pid != sandbox.pid
        assert pathlib.Path(f'/proc/{pid}/cmdline').read_bytes().startswith(b'qemu-mipsel-binfmt-P /release/bin/demo')
    finally:
        os.killpg(sandbox.pid, signal.SIGKILL)
        sandbox.wait()
    bwrap = f'bwrap --die-with-parent --unshare-pid --bind {real}/rootfs / --proc /proc'
    assert re.search(q2live.ours(q2live.copy_dir({'slot': 1})), bwrap)


def test_smoke_says_a_card_with_no_songs_apart_from_no_emulator(tmp_path, monkeypatch, capsys):
    build = tmp_path/'build'
    build.mkdir()
    (build/'rootfs.squashfs').write_text('')
    monkeypatch.setattr(emu_pool, 'HOME', tmp_path)
    card_tree(emu_pool.copy_dir(3)/'state/mmc', {'.subsonic/log.txt': 'x'})
    monkeypatch.setattr(smoke, 'lease', lambda *a: 3)
    monkeypatch.setattr(emu_pool, 'put', lambda k: None)
    assert smoke.main([str(build), '--logs', str(tmp_path/'logs'), '--no-stream']) == 3
    out = capsys.readouterr().out
    assert 'the card on slot 3 has no songs' in out and 'no pool emulator' not in out and 'no emulator' not in out


@pytest.mark.parametrize('ini, want', [
    ('[Q2POD]\nCARREMOTE=0\nAHEAD=2\n', '[Q2POD]\nCARREMOTE=1\nAHEAD=2\n'),
    ('[SYSSET]\nBLUETOOTH=0\n[Q2POD]\nAHEAD=2\n', '[SYSSET]\nBLUETOOTH=0\n[Q2POD]\nCARREMOTE=1\nAHEAD=2\n'),
    ('[SYSSET]\nBLUETOOTH=0\n', '[SYSSET]\nBLUETOOTH=0\n[Q2POD]\nCARREMOTE=1\n')])
def test_the_streaming_step_turns_car_remote_on_for_its_position(ini, want):
    """smoke.py reads a streamed song's position from Car Remote's /tmp/coda-rc.now: CARREMOTE=1 under [Q2POD]."""
    assert smoke.car_remote_on(ini) == want


def test_a_payload_change_plans_the_smoke_test_and_a_merge_batch_leaves_it_out(tmp_path, monkeypatch):
    root = tree(tmp_path, {'patch/seek.c': 'int seek_step(int x) {\n  return x;\n}\n'})
    plan = affected.plan_for({'patch/seek.c': {2}}, root)
    assert 'smoke' in plan.other
    assert 'smoke' not in affected.plan_for({'docs/ipod.md': {1}}, root).other
    # the queue runs it on the tip after landing (the post tier), not in the batch
    assert 'smoke' not in gate.suites_for(json.loads(plan.to_json()), [], 'py', tmp_path)
    assert gate.suites_for(None, [], 'py', tmp_path, 'post')['smoke'].argv[1:] == [str(gate.ROOT/'tools/ci/smoke.py'), '{build}']
    # the leak hunt is a post-tier suite and never a batch's, and never holds the tip (merge_queue.TIP_SUITES)
    assert 'leak-hunt' not in gate.suites_for(json.loads(plan.to_json()), [], 'py', tmp_path) and 'leak-hunt' not in gate.MERGE
    assert gate.suites_for(None, [], 'py', tmp_path, 'post')['leak-hunt'].argv[1:] == [str(gate.ROOT/'tools/ci/leak_hunt.py'), '{build}']
    # a worker runs it only when asked
    calls = handed(monkeypatch)
    only = affected.Plan()
    only.other.add('smoke')
    assert affected.run(only, '/b', 'py') == 0 and calls == []
    assert affected.run(only, '/b', 'py', smoke=True) == 0
    assert calls[-1][0]['smoke'].command('/b')[-2:] == [str(affected.ROOT/'tools/ci/smoke.py'), '/b']


def test_a_lease_can_keep_off_a_slot_and_prepare_the_copy_before_its_boot(tmp_path, monkeypatch):
    monkeypatch.setattr(emu_pool, 'STATE', tmp_path)
    monkeypatch.setattr(emu_pool, 'LEASES', tmp_path/'leases.json')
    monkeypatch.setattr(emu_pool, 'K', 3)
    calls: list = []
    monkeypatch.setattr(emu_pool, 'q2emu', lambda *a: calls.append(a) or type('P', (), {'returncode': 0, 'stdout': '', 'stderr': ''})())
    monkeypatch.setattr(emu_pool, 'make_copy', lambda k: None)
    monkeypatch.setattr(emu_pool, 'reset_card', lambda k: None)
    (tmp_path/'leases.json').write_text(json.dumps({'1': {'name': 'x', 'build': '/b', 'since': 0, 'until': time.time() + 99}}))
    k = emu_pool.get('/b', 'smoke-1', 1, wait=False, avoid=(2,), prepare=lambda k: calls.append(('prepare', k)))
    assert k == 3 and calls[-2:] == [('prepare', 3), (3, 'start', str(pathlib.Path('/b').resolve()))]
    try:
        emu_pool.get('/b', 'smoke-2', 1, wait=False, avoid=(2,))  # 2 is free, but avoided
    except SystemExit as e:
        assert 'leased' in str(e)
    else:
        raise AssertionError('an avoided slot was leased')

    def boom(k):
        raise RuntimeError('no config')
    emu_pool.put(3)
    try:
        emu_pool.get('/b', 'smoke-3', 1, wait=False, avoid=(2,), prepare=boom)
    except RuntimeError:
        pass
    assert '3' not in json.loads((tmp_path/'leases.json').read_text())  # a failed prepare leaves no lease


def test_smoke_counts_homes_and_settings_rows_as_ipod_lays_them_out():
    """Radio Tuner's Home row went in under the Library (be2c32f3) and smoke's Home > Library opened the tuner;
    Car Remote's Settings row before Display had its Settings > Display open Car Remote (2026-11-20)."""
    tree = ast.parse((pathlib.Path(__file__).resolve().parents[1]/'ipod.py').read_text())
    lists = {t.id: ast.literal_eval(n.value) for n in tree.body if isinstance(n, ast.Assign)
             for t in n.targets if isinstance(t, ast.Name) and t.id in ('HOME_ROWS', 'HOME_SETS')}
    rows, sets = lists['HOME_ROWS'], lists['HOME_SETS']
    ids = {'settings': 'settings', 'search': 'home_search', 'cover flow': 'coverflow', 'library': 'localmusic'}
    assert smoke.HOME_FROM_END == {label: len(rows) - 1 - rows.index(ids[label]) for label in ids}
    ids = {'General': 'sysset', 'Playback': 'playset', 'Display': 'set_display', 'Battery': 'set_power'}
    assert [(label, n) for label, n, _ in smoke.SETTINGS] == [(label, sets.index(ids[label])) for label, _, _ in smoke.SETTINGS]


def test_smoke_compares_the_screen_in_upright_boxes():
    blank = bytes(smoke.W * smoke.H * 4)

    def lit(x, y):  # upright (x, y) set, as q2remote.framebuffer_png turns the page
        page = bytearray(blank)
        i = (x * smoke.W + smoke.W - 1 - y) * 4
        page[i:i + 3] = b'\xff\xff\xff'
        return bytes(page)
    assert smoke.differ(blank, lit(100, 200)) == 1
    assert smoke.differ(blank, lit(100, 10)) == 0                     # the status row's clock: not counted
    assert smoke.differ(blank, lit(300, 200), smoke.HOME_LIST) == 0   # Home's art beside its list
    assert smoke.differ(blank, lit(100, 200), smoke.HOME_LIST) == 1


def test_smoke_tells_cover_flows_art_build_from_its_covers_and_an_albums_tracks():
    """Cover Flow's page as demo held it on a pool copy (2026-11-23): "Preparing artwork n/5" is a text list with its
    Cancel row and no carousel yet; the covers add a view holding the slide_menu; an album's tracks hide that view
    under the list. smoke's "Center on an album" once landed on Cancel while a cold card's art was still building,
    saw the screen change to the covers and passed, and the next step's Center opened the tracks instead of Now
    Playing: the failure was reported two steps late, as a play that never came."""
    def page(*views):
        return ('window', True, list(views))
    text_list = ('view', True, [('hscroll_label', True, []), ('list_view', True, [('scroll_view', True, [
        ('list_item', True, [('hscroll_label', True, [])])])])])

    def carousel(shown):
        return ('view', shown, [('slide_menu', True, [('image', True, [])] * 5), ('hscroll_label', True, [])])
    assert smoke.coverflow_screen(page(text_list)) == 'building'
    assert smoke.coverflow_screen(page(text_list, carousel(True))) == 'covers'
    assert smoke.coverflow_screen(page(text_list, carousel(False))) == 'tracks'
    assert smoke.coverflow_screen(None) is None


def test_smoke_looks_past_the_status_bar_and_a_toast():
    assert smoke.top([('system_bar', 'system_bar'), ('window', 'home_page'), ('dialog', 'msginfo_dialog')]) == 'home_page'
    assert smoke.top([('system_bar', 'system_bar'), ('window', 'home_page'), ('dialog', 'statusbar_dialog')]) == 'statusbar_dialog'
    assert smoke.top([('system_bar', 'system_bar')]) is None and smoke.top(None) is None


def test_smoke_turns_bluetooth_off_and_keeps_the_rest_of_the_config():
    assert smoke.bluetooth_off('[A]\nBLUETOOTH=1\nX=2\n') == '[A]\nBLUETOOTH=0\nX=2\n'
    assert smoke.bluetooth_off('[SYSSET]\nX=2\n') == '[SYSSET]\nBLUETOOTH=0\nX=2\n'
    assert smoke.bluetooth_off('[A]\nX=2\n') == '[A]\nX=2\n[SYSSET]\nBLUETOOTH=0\n'


def guard_run(monkeypatch, answers, stacks):
    """smoke's Emulator.guard() over q2guard answers and window stacks given in turn (the last of each repeats);
    the keys it pressed."""
    keys = []
    state = {'state': 'done', 'card': 'nosensor', 'seq': '1'}
    feed = iter(answers)
    last: list = [None]

    def files(pid):
        last[0] = next(feed, last[0])
        return state, dict(last[0])
    shown = iter(stacks)
    top: list = [None]

    def stack():
        top[0] = next(shown, top[0])
        return [('normal', 'system_bar'), ('normal', 'home_page')] + ([('dialog', top[0])] if top[0] else [])
    monkeypatch.setattr(smoke, 'guard_files', files)
    e = smoke.Emulator.__new__(smoke.Emulator)
    e.pid, e.log = 1, lambda m: None
    monkeypatch.setattr(e, 'stack', stack)
    monkeypatch.setattr(e, 'settle', lambda box=None: None)
    monkeypatch.setattr(e, 'q2emu', lambda *a: keys.append(a))
    e.guard()
    return keys


def test_smoke_waits_for_demo_to_name_a_card_it_has_just_shown(monkeypatch):
    """demo puts the guardian's card in its window stack a few ms before it writes `card nosensor` to its answer
    file (hg_info: the card, then hg_answer). A boot step that read the file just before the card came up and the
    stack just after saw an unnamed sortselect_dialog and failed: smoke's first-run flake on 2026-11-18 (02:46,
    03:05, 12:42), which a boot loop with a 20 ms poll hit on 8 boots of 10, and on none of 10 with this wait."""
    keys = guard_run(monkeypatch, [{}, {'card': 'nosensor'}, {'card': '-'}],
                     ['sortselect_dialog', 'sortselect_dialog', None])
    assert keys == [('key', 'centre')]
    # a card demo never names is still a failure, once demo has had the time to name it
    monkeypatch.setattr(smoke, 'ANSWER_S', .2)
    try:
        guard_run(monkeypatch, [{'card': '-'}], ['sortselect_dialog'])
    except smoke.Failed as why:
        assert 'a dialog over Home' in str(why)
    else:
        raise AssertionError('an unnamed confirm card passed')


def test_smoke_confirms_only_the_guardian_cards_demo_vouches_for_at_boot():
    """smoke.py's boot step (boot_rule): Home on top goes on; a confirm card demo's answer file names as one of
    q2guard's must-confirm cards is confirmed; the same dialog without that word, or any other window, fails."""
    home = [('normal', 'system_bar'), ('normal', 'home_page')]
    card = home + [('dialog', 'sortselect_dialog')]
    assert smoke.boot_rule(home, None) is None
    assert smoke.boot_rule(home + [('dialog', 'msginfo_dialog')], None) is None  # a toast passes over
    assert smoke.boot_rule(card, 'nosensor') == 'confirm'
    assert smoke.boot_rule(card, 'norun') == 'confirm'
    assert smoke.boot_rule(card, '-') == 'fail'                                 # a confirm card of another kind
    assert smoke.boot_rule(card, None) == 'fail'
    assert smoke.boot_rule(home + [('dialog', 'confirminfo_dialog')], 'nosensor') == 'fail'


def test_affected_run_hands_the_whole_plan_to_the_parallel_runner(monkeypatch, tmp_path):
    """--run is one gate.run over every suite the plan names, as gate.py --plan-json would run them; test/patch.py
    whole is the one serial process, never patch_shards.py."""
    calls = handed(monkeypatch)
    every = affected.Plan()
    every.everything('x')
    assert affected.run(every, '/b', 'py', logs=tmp_path) == 0
    assert len(calls) == 1
    suites, build, kw = calls[0]
    assert build == '/b' and kw['retry'] and kw['flakes'] == gate.FLAKES
    assert set(suites) == {*gate.MERGE, *gate.PLANNED}  # smoke only with --smoke
    assert set(suites) == set(gate.suites_for(json.loads(every.to_json()), [], 'py', tmp_path))
    assert suites['patch'].argv == ['py', 'test/patch.py', '{build}'] and suites['patch'].heavy
    assert not any('patch_shards' in a for s in suites.values() for a in s.argv)
    # a plan of some of its lines replays those alone, still one process
    some = affected.Plan()
    some.patch = {'10:20'}
    affected.run(some, '/b', 'py', logs=tmp_path)
    assert list(calls[-1][0]) == ['patch'] and calls[-1][0]['patch'].argv[-2:] == ['--lines', '10:20']
    # no build: only what needs none, and test/build.py's own checks
    affected.run(every, None, 'py', logs=tmp_path)
    suites = calls[-1][0]
    assert 'patch' not in suites and 'test/playback.py' not in suites and suites['test/build.py'].argv == ['py', 'test/build.py']
    assert not any(s.needs_build for s in suites.values()) and calls[-1][1] is None


def fake_python(tmp_path, fail):
    """A python for affected.run that logs its argv and fails test/coverflow.py by the file `fail`: 'always' or
    'once' (it then removes the file)."""
    py = tmp_path/'py'
    py.write_text(f'''#!/bin/sh
echo "$*" >> {tmp_path}/calls
case "$*" in *coverflow*)
  if [ -e {fail} ]; then [ "$(cat {fail})" = once ] && rm {fail}; exit 1; fi;;
esac
exit 0
''')
    py.chmod(0o755)
    return str(py)


def calls_of(tmp_path):
    f = tmp_path/'calls'
    return f.read_text().splitlines() if f.exists() else []


def test_affected_run_fails_retries_reports_flaky_and_skips_what_passed_on_this_tree(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(gate, 'HEAVY', tmp_path/'no-q2-heavy')
    monkeypatch.setattr(gate, 'VERIFIED', tmp_path/'verified')
    monkeypatch.setattr(gate, 'FLAKES', tmp_path/'flakes.jsonl')
    monkeypatch.setattr(gate, 'tree_key', lambda root=None: 'tree1')
    fail = tmp_path/'fail'
    py = fake_python(tmp_path, fail)
    plan = affected.Plan()
    plan.other.update({'test/coverflow.py', 'test/peq.py'})

    def go(**kw):
        n = affected.run(plan, None, py, logs=tmp_path/'logs', poll=0.01, **kw)
        return n, capsys.readouterr().out

    fail.write_text('always')  # a failure is a failure: run once more alone, still failed, and nothing cached
    n, out = go()
    assert n == 1 and [c for c in calls_of(tmp_path) if 'coverflow' in c] == ['test/coverflow.py'] * 2
    assert re.search(r'test/coverflow.py +FAILED \(fail\)', out) and 'FAILED: test/coverflow.py' in out
    assert re.search(r'test/peq.py +ok ', out) and not gate.FLAKES.exists()
    # merge_train.py names the failed suites from these lines
    assert [line.split(':')[0][3:] for line in out.splitlines() if line.startswith('== ') and 'FAILED' in line] == ['test/coverflow.py']
    fail.write_text('always')
    assert go(retry=False)[0] == 1 and len([c for c in calls_of(tmp_path) if 'coverflow' in c]) == 3
    # failed, then passed alone: flaky, counted as passed, logged, and not recorded as a pass on this tree
    fail.write_text('once')
    n, out = go()
    assert n == 0 and re.search(r'test/coverflow.py +flaky \(fail\)', out)
    assert json.loads(gate.FLAKES.read_text())['suite'] == 'test/coverflow.py'
    # test/peq.py passed on this tree in the first run: cached; test/coverflow.py only now passes cleanly
    before = len(calls_of(tmp_path))
    n, out = go()
    assert n == 0 and re.search(r'test/peq.py +cached ', out) and calls_of(tmp_path)[before:] == ['test/coverflow.py']
    n, out = go()
    assert n == 0 and len(calls_of(tmp_path)) == before + 1 and re.search(r'test/coverflow.py +cached ', out)
    assert go(cache=False)[0] == 0 and len(calls_of(tmp_path)) == before + 3  # --no-cache runs both
    # another tree is not this one
    monkeypatch.setattr(gate, 'tree_key', lambda root=None: 'tree2')
    fail.write_text('always')
    assert go()[0] == 1


def test_a_pass_is_keyed_by_the_options_the_build_was_made_with(tmp_path):
    (tmp_path/'rebuild.json').write_text(json.dumps({'source_sha256': 'x', 'options': ['--dev']}))
    assert affected.build_options(str(tmp_path)) == ['--dev'] == affected.build_options(None)  # the gate's key
    other = tmp_path/'other'
    other.mkdir()
    assert affected.build_options(str(other)) == [f'build:{other.resolve()}']


def test_rebuild_reuses_only_a_build_of_the_same_inputs(tmp_path, monkeypatch, capsys):
    """rebuild.py's reuse key: the sources build.py hashes, the options, and the iroh crate, which --build-iroh
    compiles and the pinned path checks against its PIN; a crate edit alone must not return the old image."""
    import rebuild
    sys.path.insert(0, str(rebuild.ROOT/'tools'))
    import iroh
    crate = tree(tmp_path/'crate', {'Cargo.toml': '[package]\n', 'Cargo.lock': '', 'src/main.rs': 'fn main() {}\n'})
    monkeypatch.setattr(iroh, 'CRATE', crate)
    monkeypatch.setattr(rebuild, 'BUILDS', tmp_path/'builds')
    monkeypatch.setattr(rebuild, 'source_sha256', lambda: 'src')
    builds = []

    def run(cmd, **kw):
        out = pathlib.Path(cmd[cmd.index('--out') + 1])
        out.mkdir(parents=True)
        (out/'update.tar').write_bytes(b'tar')
        builds.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout='payload room: x\n')
    monkeypatch.setattr(rebuild.subprocess, 'run', run)
    stamps = iter(range(10))
    monkeypatch.setattr(rebuild.time, 'strftime', lambda fmt: f'{next(stamps)}')  # one build directory a run

    def go(*argv):
        monkeypatch.setattr(sys, 'argv', ['rebuild.py', '--dev', *argv])
        assert rebuild.main() == 0
        return capsys.readouterr()
    go('--', '--build-iroh')
    assert len(builds) == 1 and 'reused' in go('--', '--build-iroh').err and len(builds) == 1
    (crate/'src/main.rs').write_text('fn main() { println!("changed"); }\n')
    assert 'reused' not in go('--', '--build-iroh').err and len(builds) == 2


def test_a_remote_change_plans_what_reads_it_not_everything():
    """remote/ had no rule, so a one-line edit to the iroh sidecar or the coda-rc proto planned every suite,
    test/player/run.py's ten minutes among them. What reads it: tools/iroh.py's PIN of the crate (test_iroh.py),
    the build that refuses an unpinned crate (test/build.py), and the proto's tables (test_coda_rc*.py)."""
    plan = json.loads(affected.plan_for({'remote/iroh-sidecar/src/main.rs': {1}}, REPO).to_json())
    assert not gate.everything(plan) and not plan['patch'] and not plan['fw']
    assert plan['unit'] == ['test_iroh.py'] and 'test/build.py' in plan['other']
    plan = json.loads(affected.plan_for({'remote/coda-rc.proto': {1}}, REPO).to_json())
    assert not gate.everything(plan) and plan['unit'] == ['test_coda_rc.py', 'test_coda_rc_lib.py']
    # a remote/ file no rule names still runs everything
    assert gate.everything(json.loads(affected.plan_for({'remote/other/x.go': {1}}, REPO).to_json()))


def test_every_unit_file_that_reads_remote_is_in_its_rule():
    """A test/unit file that names a remote/ path is planned by REMOTE's rule for it."""
    for path in sorted((REPO/'test/unit').glob('test_*.py')):
        for ref in sorted(set(re.findall(r"remote/[\w./-]+", path.read_text()))):
            unit, _ = next((v for k, v in affected.REMOTE.items() if ref.startswith(k) or k.startswith(ref)), (set(), ()))
            assert path.name in unit, f'{path.name} reads {ref}, which REMOTE does not plan it for'


@pytest.mark.parametrize('branch, whole', [('integ/q2-1128', True), ('release/p32', True), ('main', True),
                                           ('fix/integ-thing', False), ('integrate-x', False)])
def test_integration_branches_are_the_ones_the_docs_name(branch, whole):
    """docs/orchestration.md and CLAUDE.md name integration branches integ/*, the integrator's."""
    assert bool(affected.INTEGRATION.match(branch)) == whole


@pytest.mark.parametrize('path', ['patch/addon.c', 'patch/catalogue.c', 'patch/stream_state.c', 'player/engine.c',
                                  'tools/build.py'])
def test_the_subsonic_contract_is_planned_for_its_sides(path):
    """The firmware's side of the bundled app's files (.catplay, .playing, .ahead, .stream, the caps line) and its pin."""
    plan = json.loads(affected.plan_for({path: {1}}, REPO).to_json())
    assert 'test/subsonic_contract.py' in plan['other'] or gate.everything(plan)
    assert 'test/subsonic_contract.py' not in json.loads(affected.plan_for({'patch/home.c': {1}}, REPO).to_json())['other']


@pytest.mark.parametrize('path', ['patch/addon.c', 'patch/catalogue.c', 'patch/stream_state.c', 'patch/playback.c',
                                  'player/engine.c', 'tools/build.py', 'test/fw/seam.py', 'test/fw/test_seam_subsonic.py'])
def test_the_subsonic_seam_is_planned_for_its_sides(path):
    """The real app against the firmware's C: planned for each file on the firmware's side of it, the app's pin and
    the seam test's own harness, and for nothing else."""
    plan = json.loads(affected.plan_for({path: {1}}, REPO).to_json())
    assert 'test/subsonic_seam.py' in plan['other'] or gate.everything(plan)
    assert 'test/subsonic_seam.py' not in json.loads(affected.plan_for({'patch/home.c': {1}}, REPO).to_json())['other']


def test_an_integration_branch_given_a_base_runs_that_diffs_plan():
    """An integrator's run after one merge names its base (or a --plan-json); only a bare run plans everything."""
    assert affected.runs_everything(False, None, 'integ/q2-1129')
    assert not affected.runs_everything(False, 'integ/q2-1128', 'integ/q2-1129')
    assert affected.runs_everything(True, 'integ/q2-1128', 'fix/x') and not affected.runs_everything(False, None, 'fix/x')


def test_a_retry_that_passes_after_the_tree_changed_is_not_flaky(monkeypatch, tmp_path, capsys):
    """Three times on 2026-11-22 a suite was reported flaky when its first failure was real: the agent fixed the bug
    while the run went on, and the retry ran on the fixed tree. Only a retry on the same tree and build is flaky; one
    after a change fails, says so, and shows the first try's failure; every first-try failure is named at the end."""
    monkeypatch.setattr(gate, 'HEAVY', tmp_path/'no-q2-heavy')
    monkeypatch.setattr(gate, 'VERIFIED', tmp_path/'verified')
    monkeypatch.setattr(gate, 'FLAKES', tmp_path/'flakes.jsonl')
    monkeypatch.setattr(gate, 'tree_key', lambda root=None: None)
    state = tmp_path/'state'
    state.write_text('before the fix')
    monkeypatch.setattr(gate, 'tree_state', lambda build=None: state.read_text(), raising=False)
    fail = tmp_path/'fail'
    py = tmp_path/'py'
    py.write_text(f'''#!/bin/sh
case "$*" in *coverflow*)
  if [ -e {fail} ]; then
    echo "FAILED test/coverflow.py::test_the_wall_tilts - assert 1 == 2"
    [ "$(cat {fail})" = fixed ] && echo "after the fix" > {state}
    rm {fail}; exit 1
  fi;;
esac
exit 0
''')
    py.chmod(0o755)
    plan = affected.Plan()
    plan.other.update({'test/coverflow.py', 'test/peq.py'})
    fail.write_text('fixed')  # the first try fails, and the tree changes before the retry
    n = affected.run(plan, None, str(py), logs=tmp_path/'logs', poll=0.01)
    out = capsys.readouterr().out
    assert n == 1, out
    assert re.search(r'test/coverflow.py +FAILED, then passed after a change', out), out
    assert 'test_the_wall_tilts' in out.split('== test/coverflow.py')[-1] and not gate.FLAKES.exists()
    assert re.search(r'first tries failed: test/coverflow.py \(test/coverflow.py::test_the_wall_tilts\)', out), out
    # the same tree both times: flaky, as before, counted as passed, and still named at the end
    fail.write_text('once')
    n = affected.run(plan, None, str(py), logs=tmp_path/'logs2', poll=0.01)
    out = capsys.readouterr().out
    assert n == 0 and re.search(r'test/coverflow.py +flaky \(fail\)', out), out
    assert 'first tries failed: test/coverflow.py (test/coverflow.py::test_the_wall_tilts)' in out


@pytest.mark.parametrize('path', ['patch/coda_rc.c', 'patch/coda_rc_core.c', 'patch/coda_rc_shim.c', 'patch/coda_rc.h',
                                  'patch/coda_rc_new_part.c', 'remote/coda-rc.proto'])
def test_a_car_remote_change_plans_its_end_to_end_run(path):
    """test/coda_rc_e2e.py, the daemon and demo's shim driven over TCP on an emulator, was planned for nothing but
    itself: the integrator ran it by hand after a coda_rc_core.c change (2026-11-22). Any patch/coda_rc* file, one
    a later split adds among them, and the protocol's definition plan it."""
    assert 'test/coda_rc_e2e.py' in affected.plan_for({path: {1}}, REPO).as_dict()['other']


@pytest.mark.parametrize('path', ['patch/q2_iap.c', 'patch/q2_iap2.c', 'patch/q2_iap_lib.c', 'patch/q2_iap2_link.c',
                                  'patch/q2_iap_auth.h', 'patch/iap_session.c', 'patch/q2car.sh',
                                  'patch/iap2_link.c', 'patch/iap2_session.h', 'patch/iap2.h', 'patch/q2_iap_sys.h'])
def test_an_iphone_gadget_change_plans_the_probe_suite(path):
    """test/iap_probe.py builds q2-iap from patch/q2_iap*.c and runs it as q2car.sh does against a stand-in
    FunctionFS, and was planned only when its own file changed. Every q2-iap source, those under the same prefixes
    (q2_iap*, iap_*, and iap2*, the iAP2 files tools/build.py's Q2IAP_SOURCES names), and q2car.sh plan it."""
    assert 'test/iap_probe.py' in affected.plan_for({path: {1}}, REPO).as_dict()['other']


@pytest.mark.parametrize('path', ['patch/coverflow.c', 'patch/addon.c', 'remote/iroh-sidecar/src/main.rs'])
def test_other_files_plan_neither_car_suite(path):
    other = affected.plan_for({path: {1}}, REPO).as_dict()['other']
    other = other if other != ALL else ()
    assert 'test/coda_rc_e2e.py' not in other and 'test/iap_probe.py' not in other


def test_an_everything_plan_runs_the_car_suites_and_the_seam(monkeypatch, tmp_path):
    """affected.py --all and an integ/* run without a base plan everything: the image's last full run before the
    owner drives with it ran neither route 1's probe nor Car Remote end to end, the two suites the car depends on.
    They are PLANNED, so an everything plan and a release's gate hold them, as the Subsonic seam already was;
    coda_rc_e2e leases its own emulator (--pool BUILD), iap_probe builds q2-iap for this machine and needs no build.
    test/coda_rc_iroh_e2e.py stays by hand (n0's public relay)."""
    calls = handed(monkeypatch)
    every = affected.Plan()
    every.everything('x')
    assert affected.run(every, '/b', 'py', logs=tmp_path) == 0
    suites = calls[0][0]
    assert suites['test/coda_rc_e2e.py'].argv == ['py', 'test/coda_rc_e2e.py', '--pool', '{build}']
    assert suites['test/coda_rc_e2e.py'].needs_build and suites['test/coda_rc_e2e.py'].heavy
    assert suites['test/iap_probe.py'].argv == ['py', 'test/iap_probe.py']
    assert 'test/subsonic_seam.py' in suites and 'test/coda_rc_iroh_e2e.py' not in suites
    assert {'test/coda_rc_e2e.py', 'test/iap_probe.py', 'test/subsonic_seam.py'} <= set(gate.TIERS['release'])


@pytest.mark.parametrize('path', ['patch/peq_ui.c', 'patch/peq.c', 'patch/peq.h', 'patch/peq_types.h', 'patch/games.c',
                                  'patch/tidal.c', 'patch/ui_kit.h'])
def test_a_source_test_peq_builds_plans_it(path):
    """test/peq.py builds the EQ and its editor (and games, video, radio, Spotify, the App Store, online lyrics,
    Tidal and scrobbling) on the host, and was planned only by an everything plan: a change to patch/peq_ui.c that its
    host build could not link (hash_bytes) went unseen from 2f74c2c3 until a full run. A header one of them includes
    plans it too."""
    assert 'test/peq.py' in affected.plan_for({path: {1}}, REPO).as_dict()['other']


@pytest.mark.parametrize('path, suite', [('patch/corner_cover.c', 'test/coverflow.py'), ('patch/wheel_accel.c', 'test/coverflow.py'),
                                         ('tools/theme.py', 'test/coverflow.py'), ('patch/peq.c', 'test/player/run.py'),
                                         ('patch/stream_path.h', 'test/iap_probe.py'), ('tools/coda_rc_client.py', 'test/coda_rc_e2e.py'),
                                         ('tools/ci/emu_pool.py', 'test/coda_rc_e2e.py')])
def test_a_source_a_suite_builds_runs_or_reads_plans_it(path, suite):
    assert suite in affected.plan_for({path: {1}}, REPO).as_dict()['other']


@pytest.mark.parametrize('path', ['patch/navigation.c', 'tools/ipod.py', 'assets/themes/oled/theme.xml'])
def test_a_payload_change_plans_the_suites_that_run_the_payload(path):
    """test/playback.py and test/headset_io.py run the build's payload under Unicorn as test/patch.py does, so what
    plans test/patch.py plans them."""
    other = affected.plan_for({path: {1}}, REPO).as_dict()['other']
    assert {'test/playback.py', 'test/headset_io.py'} <= set(other)


def code_strings(path):
    """The string constants of path's code, its docstrings left out (a comment or docstring naming a file is not a
    use of it)."""
    tree = ast.parse(path.read_text())
    docs = {id(n.value) for n in ast.walk(tree) if isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant)}
    return [n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docs]


def test_every_source_a_suite_names_in_its_code_plans_it():
    """The planner audit's rule, kept: for each test/X.py of an everything plan (and test/player/'s files for
    test/player/run.py), every patch/, player/, tools/, remote/ or assets/ file its code names (a path in a string, or
    a #include "X" of patch/ in a C source it writes), and for a suite that compiles (a cc it runs, or C it writes)
    every file those include, must plan that suite. Comments and docstrings name files without using them, so they
    are not read."""
    src = re.compile(r'(?<![\w/.-])((?:patch|player|tools|remote|assets)/[A-Za-z0-9_./-]*[A-Za-z0-9_])')
    inc = re.compile(r'#include "([A-Za-z0-9_]+\.(?:c|h|inc))"')
    missing = []
    for suite in (s for s in (*gate.MERGE, *gate.PLANNED) if s.startswith('test/')):
        files = [REPO/suite] + (sorted((REPO/'test/player').glob('*.py')) if suite == 'test/player/run.py' else [])
        strings = [s for f in files for s in code_strings(f)]
        named = {m for s in strings for m in src.findall(s) if (REPO/m).is_file()}
        named |= {f'patch/{m}' for s in strings for m in inc.findall(s) if (REPO/'patch'/m).is_file()}
        if any(s == 'cc' or '#include' in s for s in strings):
            named |= affected.included(REPO, tuple(sorted(n for n in named if n.endswith(('.c', '.h', '.inc')))))
        for path in sorted(named):
            plan = affected.plan_for({path: {1}}, REPO).as_dict()
            if not (gate.everything(plan) or suite in plan['other'] or (suite == 'test/patch.py' and plan['patch'])):
                missing.append(f'{suite}: {path}')
    assert not missing, missing
