#!/usr/bin/env python3
"""Coverage of one tools/ module by the test/unit files that name it, against the floor the post tier uses.

    python3 tools/ci/covcheck.py tools/q2flash.py [--python PY]

The post tier's `unit-cov` (gate.py) runs all of test/unit under coverage, about five and a half minutes,
and fails the tip when tools/ drops under `fail_under` in pyproject.toml. A branch that adds or edits a
tools/ module is checked here instead, before it lands: only the test/unit files that import the module or
name its file (`tests_for`) run, with branch coverage as pyproject.toml configures it, and the module alone
is held to the same floor. The failure names the module and prints the lines and branches it misses.
affected.py plans this as `cov:tools/X.py` for each tools/ module a branch changes, gate.py runs it in a merge
batch, and docs/orchestration.md#the-workers-contract says why.
"""
import argparse
import os
import pathlib
import re
import subprocess
import sys
import tempfile
import tomllib

ROOT = pathlib.Path(__file__).resolve().parents[2]


def floor(root=ROOT):
    """pyproject.toml's [tool.coverage.report] fail_under: the post tier's floor."""
    return tomllib.loads((root/'pyproject.toml').read_text())['tool']['coverage']['report']['fail_under']


def tests_for(path, root=ROOT):
    """The test/unit files that exercise tools/<path>: test_<stem>.py, and any file that imports the module
    (`import q2flash`, `from q2flash import`, `tools.q2flash`) or names `<stem>.py`."""
    stem = pathlib.PurePath(path).stem
    named = re.compile(rf'^\s*(?:import|from)\s+(?:tools\.)?{re.escape(stem)}\b|\btools\.{re.escape(stem)}\b|'
                       rf'\b{re.escape(stem)}\.py\b', re.M)
    return sorted(p.name for p in (root/'test/unit').glob('test_*.py')
                  if p.name == f'test_{stem}.py' or named.search(p.read_text(errors='replace')))


def check(path, python, root=ROOT):
    """Run the tests for tools/<path> under coverage and hold the module to the floor; the exit status."""
    tests = tests_for(path, root)
    if not tests:
        print(f'covcheck: {path}: no test/unit file imports it or names it, so its coverage is 0 against '
              f'the floor of {floor(root)}; add test/unit/test_{pathlib.PurePath(path).stem}.py')
        return 1
    with tempfile.TemporaryDirectory(prefix='covcheck-') as tmp:
        env = {**os.environ, 'COVERAGE_FILE': f'{tmp}/.coverage'}
        argv = [python, '-m', 'coverage', 'run', '-m', 'pytest', '-q', '-p', 'no:cacheprovider',
                *[f'test/unit/{t}' for t in tests]]
        print(f'covcheck: {path}: {len(tests)} test files: {" ".join(tests)}', flush=True)
        rc = subprocess.run(argv, cwd=root, env=env).returncode
        if rc:
            print(f'covcheck: {path}: its tests failed (exit {rc})')
            return rc
        report = subprocess.run([python, '-m', 'coverage', 'report', f'--include=*/{path}', '--skip-covered',
                                 '--show-missing', f'--fail-under={floor(root)}'], cwd=root, env=env,
                                capture_output=True, text=True)
        print(report.stdout + report.stderr, end='')
        if report.returncode:
            # the last line of the log is what a merge batch quotes: the file, its coverage and what it misses
            row = re.search(rf'^(\S*{re.escape(path)})\s+\d+\s+\d+\s+\d+\s+\d+\s+([\d.]+)%\s*(.*)$', report.stdout, re.M)
            what = f'{row[2]}%, missing {row[3]}' if row else 'it is not measured at all'
            print(f'covcheck FAILED: {path} {what}; the post tier fails the trunk under {floor(root)}. Test the '
                  f'lines above, or mark one that cannot run `# pragma: no cover`')
        return 1 if report.returncode else 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('path', help='a tools/ module, from the repository root')
    ap.add_argument('--python', default=sys.executable)
    args = ap.parse_args()
    return 1 if check(args.path, args.python) else 0


if __name__ == '__main__':
    sys.exit(main())
