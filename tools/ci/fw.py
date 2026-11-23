#!/usr/bin/env python3
"""Run test/fw in parallel: the library built once, then pytest-xdist workers sharing it.

    python3 tools/ci/fw.py                       # all of test/fw
    python3 tools/ci/fw.py test/fw/test_seek.py  # some files (what tools/ci/affected.py hands it)
    python3 tools/ci/fw.py --asan                # AddressSanitizer and UBSan, in fresh processes (see below)
    python3 tools/ci/fw.py -n 1                  # one worker, no xdist

Each test loads its own copy of the library and the C runs at stock's fixed addresses in its process, so
tests in different worker processes share nothing; files go whole to one worker (--dist loadfile) as a
file's tests may share a module's state. The workers would each build the library (conftest's session
fixture); this builds it once and hands it over through Q2_FW_LIBRARY, which conftest.py honours.

Workers: MemAvailable less a 5 GB reserve over WORKER_RSS (measured), capped at CAP and the CPU count,
never xdist's `-n auto`. The run is one q2-heavy job sized for its workers. Needs pytest-xdist
(pip install pytest-xdist); without it the run is serial.

Under ASan a process's memory grows with every test it has run (two xdist workers reached 9.4 GB and
were killed on an idle machine, 2026-11-16), so --asan runs the files in ASAN_CHUNKS groups, each a
pytest process of its own that ends before it is large: eight groups, three at a time, peaked at 3.7 GB
in all (1.4 GB a process) and passed every test (9,052 then) in 2:31. Each group is a q2-heavy job.
"""
import argparse
import concurrent.futures
import importlib.util
import os
import pathlib
import signal
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[2]
HEAVY = pathlib.Path.home()/'.local/bin/q2-heavy'
CAP = 6
WORKER_RSS = 0.5e9  # one plain worker's share
SOLO = 3e9  # the scope one plain process needs on its own
RESERVE = 5e9
SCOPE = 3e9  # the most the plain workers' shares may add up to (their scope gets 2 GB more, SOLO at least)
ASAN_CHUNKS = 8  # groups of files under ASan, each a process of its own
ASAN_AT_ONCE = 3  # groups running together at most
ASAN_CHUNK_RSS = 1.5e9  # one group's peak, measured at 1.4 GB
ASAN_CHUNK_SCOPE = '3G'  # its q2-heavy scope: twice the measured peak
# A small quarantine: freed blocks are held this long to catch a use after free, and the default 256 MB
# a process is most of a group's memory.
ASAN_OPTIONS = 'detect_leaks=0:allocator_may_return_null=1:quarantine_size_mb=8:thread_local_quarantine_size_kb=64'


def available():
    with open('/proc/meminfo') as f:
        for line in f:
            if line.startswith('MemAvailable:'):
                return int(line.split()[1]) * 1024
    return 0


def workers():
    return max(1, min(CAP, os.cpu_count() or 1, int((available() - RESERVE) // WORKER_RSS), int(SCOPE // WORKER_RSS)))


def chunks(files, n):
    """files in at most n groups of about the same size on disk (the largest files first, dealt in turn), so
    no group holds all the long ones."""
    files = sorted(files, key=lambda f: (-f.stat().st_size, f.name))
    return [g for g in (files[i::n] for i in range(n)) if g]


def test_files(paths, root):
    """The test files paths name: a file as it is, a directory's test_*.py."""
    out = []
    for p in map(pathlib.Path, paths):
        full = p if p.is_absolute() else root/p
        out += sorted(full.glob('test_*.py')) if full.is_dir() else [full]
    return out


def sanitizer_log(env, folder):
    """env with ASan's and UBSan's reports written to files in folder (<name>.<pid>) as well as stderr. A
    report goes to stderr while pytest captures the test that tripped it, and the capture dies with the
    process: the post tier's fw-asan of 2026-11-18 stopped at 89% of a group with nothing said."""
    out = dict(env)
    for name, file in (('ASAN_OPTIONS', 'asan'), ('UBSAN_OPTIONS', 'ubsan')):
        out[name] = ':'.join(filter(None, [env.get(name, ''), f'log_path={folder/file}']))
    return out


def died(code, reports):
    """Why a group's process ended with code: a signal by name, a sanitizer's report, or a plain failure."""
    signo = -code if code < 0 else code - 128 if code > 128 else 0
    if signo:
        try:
            return f'killed by {signal.Signals(signo).name}' + (', sanitizer report below' if reports else '')
        except ValueError:
            return f'killed by signal {signo}'
    if reports:
        return 'a sanitizer stopped it, report below'
    return 'no signal and no sanitizer report: a test failed, or the process exited on its own'


def run_asan(args, rest, env, library, run=subprocess.run, log=print):
    """test/fw under the sanitizers, a group of files to a process; returns the worst exit status."""
    groups = chunks(test_files(args.paths, ROOT), ASAN_CHUNKS)
    at_once = args.n or max(1, min(ASAN_AT_ONCE, len(groups), int((available() - RESERVE) // ASAN_CHUNK_RSS)))
    env = {**env, 'Q2_FW_LIBRARY': library, 'Q2_HEAVY_MEM': env.get('Q2_HEAVY_MEM', ASAN_CHUNK_SCOPE)}
    heavy = [str(HEAVY)] if HEAVY.exists() and not env.get('Q2_HEAVY_JOB') else []
    log(f'fw: {len(groups)} groups of files (ASan), {at_once} at a time', flush=True)

    def one(k):
        cmd = heavy + [args.python, '-m', 'pytest', '-q', '-p', 'no:cacheprovider', '--basetemp',
                       f'{args.basetemp}-{k}', *rest, *map(str, groups[k])]
        with tempfile.TemporaryDirectory(prefix=f'q2-fw-san-{k}-', dir=pathlib.Path(args.basetemp).parent) as san:
            p = run(cmd, cwd=ROOT, env=sanitizer_log(env, pathlib.Path(san)), capture_output=True, text=True)
            reports = ''.join(f.read_text(errors='replace') for f in sorted(pathlib.Path(san).iterdir()))
        tail = (p.stdout.strip().splitlines() or ['no output'])[-1]
        log(f'fw group {k} ({len(groups[k])} files): {"ok" if p.returncode == 0 else f"FAILED (exit {p.returncode})"}: {tail}',
            flush=True)
        if p.returncode:
            log(p.stdout + p.stderr, flush=True)
            log(f'fw group {k}: {died(p.returncode, reports)}', flush=True)
            if reports:
                log(reports, flush=True)
        return p.returncode

    with concurrent.futures.ThreadPoolExecutor(at_once) as pool:
        codes = list(pool.map(one, range(len(groups))))
    bad = [c for c in codes if c]
    # a signal's status (negative, or 128 + the signal) outranks a test failure: the caller tells a crash by it
    return 0 if not bad else next((c for c in bad if c < 0 or c > 128), bad[0])


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('paths', nargs='*', default=['test/fw'])
    ap.add_argument('-n', type=int, help=f'workers (default: by memory, at most {CAP}); with --asan, groups at a time')
    ap.add_argument('--asan', action='store_true', help='AddressSanitizer and UBSan, as Q2_FW_SANITIZE=1')
    ap.add_argument('--python', default=sys.executable)
    ap.add_argument('--basetemp', help='default: ~/.cache/q2-pytest/fw-<worktree>[-asan], emptied by pytest each run')
    args, rest = ap.parse_known_args()
    args.basetemp = args.basetemp or str(pathlib.Path.home()/'.cache/q2-pytest'/f'fw-{ROOT.name}{"-asan" * args.asan}')
    n = args.n or workers()
    if importlib.util.find_spec('xdist') is None:
        if n > 1:
            print('pytest-xdist is not installed: running serially (pip install pytest-xdist)', flush=True)
        n = 1
    env = dict(os.environ)
    extra = []
    if args.asan:
        env['Q2_FW_SANITIZE'] = '1'
        env['ASAN_OPTIONS'] = env.get('ASAN_OPTIONS', ASAN_OPTIONS)
        extra = ['-fsanitize=address,undefined', '-fno-sanitize-recover=undefined', '-fno-sanitize=alignment']
        asan = subprocess.run(['cc', '-print-file-name=libasan.so'], capture_output=True, text=True, check=True).stdout.strip()
        env['LD_PRELOAD'] = asan + (':' + env['LD_PRELOAD'] if env.get('LD_PRELOAD') else '')
    pathlib.Path(args.basetemp).parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='q2-fw-lib-', dir=pathlib.Path(args.basetemp).parent) as lib:
        if (n > 1 or args.asan) and 'Q2_FW_LIBRARY' not in env and not env.get('Q2_FW_COVERAGE'):
            # The library, built here once: the same call conftest.py's fixture makes.
            build = [args.python, '-c', 'import pathlib, sys; sys.path.insert(0, "test/fw"); import host; '
                     f'print(host.build(pathlib.Path({lib!r}), {extra!r}))']
            env['Q2_FW_LIBRARY'] = subprocess.run(build, cwd=ROOT, env={**env, 'LD_PRELOAD': ''}, check=True,
                                                  capture_output=True, text=True).stdout.strip().splitlines()[-1]
        if args.asan and env.get('Q2_FW_LIBRARY'):
            return run_asan(args, rest, env, env['Q2_FW_LIBRARY'])
        cmd = [args.python, '-m', 'pytest', '-q', '-p', 'no:cacheprovider', '--basetemp', args.basetemp,
               *(['-n', str(n), '--dist', 'loadfile'] if n > 1 else []), *rest, *args.paths]
        if HEAVY.exists() and not env.get('Q2_HEAVY_JOB'):
            # one process alone needs SOLO; Q2_HEAVY_MEM already set (by hand) wins
            env.setdefault('Q2_HEAVY_MEM', f'{max(int(SOLO / 1e9), int(n * WORKER_RSS / 1e9) + 2)}G')
            cmd = [str(HEAVY)] + cmd
        print(f'fw: {n} worker{"s" * (n > 1)}{" (ASan)" if args.asan else ""}: {" ".join(cmd)}', flush=True)
        return subprocess.run(cmd, cwd=ROOT, env=env).returncode


if __name__ == '__main__':
    sys.exit(main())
