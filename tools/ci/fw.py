#!/usr/bin/env python3
"""Run test/fw in parallel: the library built once, then pytest-xdist workers sharing it.

    python3 tools/ci/fw.py                       # all of test/fw
    python3 tools/ci/fw.py test/fw/test_seek.py  # some files (what tools/ci/affected.py hands it)
    python3 tools/ci/fw.py --asan                # AddressSanitizer and UBSan (an integration branch's run)
    python3 tools/ci/fw.py -n 1                  # one worker, no xdist

Each test loads its own copy of the library and the C runs at stock's fixed addresses in its process, so
tests in different worker processes share nothing; files go whole to one worker (--dist loadfile) as a
file's tests may share a module's state. The workers would each build the library (conftest's session
fixture); this builds it once and hands it over through Q2_FW_LIBRARY, which conftest.py honours.

Workers: MemAvailable less a 5 GB reserve over WORKER_RSS (measured), capped at CAP and the CPU count,
never xdist's `-n auto`. The run is one q2-heavy job sized for its workers. Needs pytest-xdist
(pip install pytest-xdist); without it the run is serial.
"""
import argparse
import importlib.util
import os
import pathlib
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[2]
HEAVY = pathlib.Path.home()/'.local/bin/q2-heavy'
CAP = 6
WORKER_RSS = {False: 0.5e9, True: 0.9e9}  # one worker's peak, plain and under ASan (docs/building.md#fast-test-runs)
RESERVE = 5e9


def workers(asan):
    avail = 0
    with open('/proc/meminfo') as f:
        for line in f:
            if line.startswith('MemAvailable:'):
                avail = int(line.split()[1]) * 1024
    return max(1, min(CAP, os.cpu_count() or 1, int((avail - RESERVE) // WORKER_RSS[asan])))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('paths', nargs='*', default=['test/fw'])
    ap.add_argument('-n', type=int, help=f'workers (default: by memory, at most {CAP})')
    ap.add_argument('--asan', action='store_true', help='AddressSanitizer and UBSan, as Q2_FW_SANITIZE=1')
    ap.add_argument('--python', default=sys.executable)
    ap.add_argument('--basetemp', help='default: ~/.cache/q2-pytest/fw-<worktree>[-asan], emptied by pytest each run')
    args, rest = ap.parse_known_args()
    args.basetemp = args.basetemp or str(pathlib.Path.home()/'.cache/q2-pytest'/f'fw-{ROOT.name}{"-asan" * args.asan}')
    n = args.n or workers(args.asan)
    if importlib.util.find_spec('xdist') is None:
        if n > 1:
            print('pytest-xdist is not installed: running serially (pip install pytest-xdist)', flush=True)
        n = 1
    env = dict(os.environ)
    extra = []
    if args.asan:
        env['Q2_FW_SANITIZE'] = '1'
        env['ASAN_OPTIONS'] = env.get('ASAN_OPTIONS', 'detect_leaks=0:allocator_may_return_null=1')
        extra = ['-fsanitize=address,undefined', '-fno-sanitize-recover=undefined', '-fno-sanitize=alignment']
        asan = subprocess.run(['cc', '-print-file-name=libasan.so'], capture_output=True, text=True, check=True).stdout.strip()
        env['LD_PRELOAD'] = asan + (':' + env['LD_PRELOAD'] if env.get('LD_PRELOAD') else '')
    pathlib.Path(args.basetemp).parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='q2-fw-lib-', dir=pathlib.Path(args.basetemp).parent) as lib:
        if n > 1 and 'Q2_FW_LIBRARY' not in env and not env.get('Q2_FW_COVERAGE'):
            # The library, built here once: the same call conftest.py's fixture makes.
            build = [args.python, '-c', 'import pathlib, sys; sys.path.insert(0, "test/fw"); import host; '
                     f'print(host.build(pathlib.Path({lib!r}), {extra!r}))']
            env['Q2_FW_LIBRARY'] = subprocess.run(build, cwd=ROOT, env={**env, 'LD_PRELOAD': ''}, check=True,
                                                  capture_output=True, text=True).stdout.strip().splitlines()[-1]
        cmd = [args.python, '-m', 'pytest', '-q', '-p', 'no:cacheprovider', '--basetemp', args.basetemp,
               *(['-n', str(n), '--dist', 'loadfile'] if n > 1 else []), *rest, *args.paths]
        if HEAVY.exists() and not env.get('Q2_HEAVY_JOB'):
            env['Q2_HEAVY_MEM'] = f'{max(3, int(n * WORKER_RSS[args.asan] / 1e9) + 2)}G'
            cmd = [str(HEAVY)] + cmd
        print(f'fw: {n} worker{"s" * (n > 1)}{" (ASan)" if args.asan else ""}: {" ".join(cmd)}', flush=True)
        return subprocess.run(cmd, cwd=ROOT, env=env).returncode


if __name__ == '__main__':
    sys.exit(main())
