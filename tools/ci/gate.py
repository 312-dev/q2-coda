#!/usr/bin/env python3
"""The integration gate: one build, then every suite at once, as memory allows, with a table at the end.

    python3 tools/ci/gate.py                          # the release gate: everything, on a --dev --new-player build
    python3 tools/ci/gate.py --only fw-asan,unit      # some of it
    python3 tools/ci/gate.py --plan-json plan.json    # what an affected.py plan names (merge_batch.py's runs)
    python3 tools/ci/gate.py -- --dev                 # rebuild.py's options (default: --dev -- --new-player)

Run it with a python that has pytest, pytest-xdist, coverage, mypy, unicorn and fontTools (the main
checkout's venv, ~/repos/q2-pod/.venv, has them all). The whole gate's suites:

    unit           test/unit under coverage, then `coverage report` (the floor in pyproject.toml)
    mypy-tools     mypy tools test/unit
    mypy-fw        mypy test/fw
    fw             tools/ci/fw.py: test/fw on xdist workers
    fw-asan        tools/ci/fw.py --asan
    patch          tools/ci/patch_shards.py BUILD: test/patch.py in shards
    test/build.py  test/build.py --build BUILD
    test/peq.py    test/peq.py

The build (tools/ci/rebuild.py: this worktree's reused build) starts at once, beside the suites that need
none; the rest start when it is done. The longest suites start first. A suite starts only when
MemAvailable, less what suites started in the last RAMP seconds will still take, stays at least FLOOR GB
after its own estimate (MEM_GB); else it waits for one to finish. When nothing is running it starts anyway
and q2-heavy's own checks hold it. patch_shards.py's shard count comes from the memory left when it starts.
Heavy suites run under q2-heavy (fw.py and patch_shards.py wrap their own processes). Each suite writes
its own log; the table gives each one's exit status and time. Exit status: 0 when all passed, else 1.

A plan (affected.py --json) that runs unit, fw and patch whole is the whole gate; any other plan runs what
it names, each `other` path a suite of its own. --only keeps just those names: a plan's suite as the plan
has it, one only the whole gate has (GATE_ONLY: fw-asan, mypy-tools, mypy-fw, test/peq.py) whole, and
any other name the plan lacks not at all; the name `build` builds even when no suite needs it.
docs/building.md#gate.
"""
import argparse
import contextlib
import json
import os
import pathlib
import shlex
import signal
import subprocess
import sys
import time
from typing import Any

ROOT = pathlib.Path(__file__).resolve().parents[2]
HEAVY = pathlib.Path(os.environ.get('Q2_HEAVY', pathlib.Path.home()/'.local/bin/q2-heavy'))
FLOOR = 4.0  # GB of MemAvailable a new suite must leave
RAMP = 30    # s: a suite this young may not have reached its peak yet, so its estimate still counts
SHARD_GB = 1.5  # patch_shards.py's SHARD_RSS
# A suite's peak memory (GB) and how long it takes alone (s), from the gate's runs on 2026-11-16: the
# order they start in and what each needs free.
MEM_GB = {'build': 2.0, 'unit': 1.5, 'mypy-tools': 1.0, 'mypy-fw': 1.0, 'fw': 3.0, 'fw-asan': 6.0,
          'test/build.py': 1.0, 'test/peq.py': 1.0}
SECONDS = {'fw-asan': 720, 'unit': 330, 'patch': 270, 'fw': 100, 'test/build.py': 60, 'test/peq.py': 60,
           'mypy-tools': 40, 'mypy-fw': 30}
FULL = tuple(SECONDS)
GATE_ONLY = ('fw-asan', 'mypy-tools', 'mypy-fw', 'test/peq.py')  # in the whole gate, never in a narrower plan


class Suite:
    def __init__(self, name, argv, needs_build=False, heavy=False, env=None, after=None):
        self.name, self.argv, self.needs_build, self.heavy = name, argv, needs_build, heavy
        self.env, self.after = env or {}, after  # after: a command run when it passed, its output appended
        self.mem = MEM_GB.get(name, 1.5)
        self.proc: Any = None  # the Popen, once started
        self.start: Any = None
        self.log: Any = None
        self.rc: Any = None
        self.seconds = 0.0

    def command(self, build, jobs=None):
        argv = [build if a == '{build}' else a for a in self.argv]
        if self.name == 'patch' and jobs:
            argv += ['-j', str(jobs)]
        return ([str(HEAVY)] if self.heavy and HEAVY.exists() else []) + argv


def everything(plan):
    return plan is None or all(plan.get(k) == 'all' for k in ('unit', 'fw', 'patch'))


def full_suite(name, py, logs):
    cov = {'COVERAGE_FILE': str(logs/'.coverage')}
    return {
        'unit': lambda: Suite('unit', [py, '-m', 'coverage', 'run', '-m', 'pytest', 'test/unit', '-q', '-p',
                                       'no:cacheprovider'], heavy=True, env=cov,
                              after=[py, '-m', 'coverage', 'report']),
        'mypy-tools': lambda: Suite('mypy-tools', [py, '-m', 'mypy', 'tools', 'test/unit']),
        'mypy-fw': lambda: Suite('mypy-fw', [py, '-m', 'mypy', 'test/fw']),
        'fw': lambda: Suite('fw', [py, str(ROOT/'tools/ci/fw.py')]),
        'fw-asan': lambda: Suite('fw-asan', [py, str(ROOT/'tools/ci/fw.py'), '--asan']),
        'patch': lambda: Suite('patch', [py, str(ROOT/'tools/ci/patch_shards.py'), '{build}'], needs_build=True),
        'test/build.py': lambda: Suite('test/build.py', [py, 'test/build.py', '--build', '{build}'], needs_build=True,
                                       heavy=True),
        'test/peq.py': lambda: Suite('test/peq.py', [py, 'test/peq.py'], heavy=True),
    }.get(name)


def other_suite(path, py):
    """A plan's `other` path, run as tools/ci/affected.py runs it."""
    if path.startswith('tools/ci/test_'):
        return Suite(path, [py, '-m', 'pytest', '-q', '-p', 'no:cacheprovider', path])
    if path == 'test/build.py':
        return Suite(path, [py, path, '--build', '{build}'], needs_build=True, heavy=True)
    return Suite(path, [py, path, '{build}'], needs_build=True, heavy=True)


def suites_for(plan, only, py, logs):
    """The suites to run: the whole gate for an everything plan, else the plan's, cut to only."""
    out = {}
    if everything(plan):
        out = {n: full_suite(n, py, logs)() for n in FULL}
    else:
        if plan['unit']:
            files = ['test/unit'] if plan['unit'] == 'all' else [f'test/unit/{f}' for f in plan['unit']]
            out['unit'] = Suite('unit', [py, '-m', 'pytest', '-q', '-p', 'no:cacheprovider', *files], heavy=True)
        if plan['fw']:
            files = [] if plan['fw'] == 'all' else [f'test/fw/{f}' for f in plan['fw']]
            out['fw'] = Suite('fw', [py, str(ROOT/'tools/ci/fw.py'), *files])
        if plan['patch']:
            lines = [] if plan['patch'] == 'all' else ['--lines', ','.join(plan['patch'])]
            out['patch'] = Suite('patch', [py, str(ROOT/'tools/ci/patch_shards.py'), '{build}', *lines],
                                 needs_build=True)
        for o in plan['other']:
            out[o] = full_suite(o, py, logs)() if o in FULL else other_suite(o, py)
    if only:
        # a plan's suite is cut to only; a suite no plan names (GATE_ONLY) runs whole; any other name the plan
        # lacks is left out, as nothing the plan's changes touch reaches it
        out = {n: out[n] if n in out else full_suite(n, py, logs)() for n in only if n in out or n in GATE_ONLY}
    return out


def avail_gb():
    with open('/proc/meminfo') as f:
        for line in f:
            if line.startswith('MemAvailable:'):
                return int(line.split()[1]) / 1048576
    return 0.0


def run(suites, build_args, logs, py, build=None, log=print, mem=avail_gb, clock=time.monotonic, force_build=False,
        poll=2.0):
    """Build (unless build is given, or nothing needs it and not force_build) and run the suites, as memory
    allows; returns {name: {'rc', 'seconds', 'log'}} with 'build' among them when it ran."""
    logs.mkdir(parents=True, exist_ok=True)
    pending: list[Suite] = sorted(suites.values(), key=lambda s: -SECONDS.get(s.name, 60))
    running: list[Suite] = []
    done: dict = {}
    builder: Suite | None = None
    if build is None and (force_build or any(s.needs_build for s in pending)):
        builder = Suite('build', [py, str(ROOT/'tools/ci/rebuild.py'), *build_args], heavy=True)
        pending.insert(0, builder)

    def launch(s, jobs=None):
        s.log = logs/f'{s.name.replace("/", "_")}.log'
        s.start = clock()
        cmd = s.command(build or '', jobs)
        log_file = open(s.log, 'w')
        s.proc = subprocess.Popen(cmd, cwd=ROOT, env={**os.environ, **s.env},
                                  stdout=subprocess.PIPE if s is builder else log_file,  # the build's path
                                  stderr=log_file if s is builder else subprocess.STDOUT, text=True,
                                  start_new_session=True)  # its own group, which the finally below ends whole
        log(f'start {s.name}{f" ({jobs} shards)" if jobs else ""}: {shlex.join(cmd)}')
        running.append(s)

    try:
        while pending or running:
            for s in list(running):
                if s.proc.poll() is None:
                    continue
                running.remove(s)
                s.rc, s.seconds = s.proc.returncode, clock() - s.start
                if s is builder:
                    lines = s.proc.stdout.read().strip().splitlines()
                    build = lines[-1] if s.rc == 0 and lines else None
                elif s.rc == 0 and s.after:
                    with open(s.log, 'a') as f:
                        f.write(f'== {shlex.join(s.after)}\n')
                        f.flush()
                        s.rc = subprocess.run(s.after, cwd=ROOT, env={**os.environ, **s.env}, stdout=f,
                                              stderr=subprocess.STDOUT).returncode
                done[s.name] = {'rc': s.rc, 'seconds': round(s.seconds), 'log': str(s.log)}
                log(f'{"ok  " if s.rc == 0 else "FAIL"} {s.name} ({s.seconds:.0f} s)')
            if builder and builder.rc not in (None, 0):
                for s in [p for p in pending if p.needs_build]:
                    pending.remove(s)
                    done[s.name] = {'rc': 'no build', 'seconds': 0, 'log': str(builder.log)}
            now = clock()
            young = sum(s.mem for s in running if now - s.start < RAMP)
            for s in list(pending):
                if s.needs_build and not build:
                    continue
                free = mem() - young
                jobs = None
                if s.name == 'patch':
                    jobs = max(1, min(8, int((free - FLOOR) // SHARD_GB)))  # patch_shards.py's own cap
                    s.mem = jobs * SHARD_GB
                if free - s.mem >= FLOOR or not running:
                    pending.remove(s)
                    launch(s, jobs)
                    young += s.mem
            if pending or running:
                time.sleep(poll)
    finally:
        for s in running:  # stopped early: end each suite with what it started (q2-heavy ends its job on TERM)
            with contextlib.suppress(ProcessLookupError):
                os.killpg(s.proc.pid, signal.SIGTERM)
    return done


def table(results, wall):
    rows = [f'{"suite":15} {"rc":>8} {"time":>7}  log']
    for name, r in results.items():
        rows.append(f'{name:15} {str(r["rc"]):>8} {r["seconds"] // 60:4d}:{r["seconds"] % 60:02d}  {r["log"]}')
    bad = [n for n, r in results.items() if r['rc'] != 0]
    rows.append(f'{"wall":15} {"":>8} {int(wall) // 60:4d}:{int(wall) % 60:02d}  '
                + (f'FAILED: {", ".join(bad)}' if bad else 'all passed'))
    return '\n'.join(rows)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--plan-json', metavar='FILE', help='an affected.py plan (- for stdin): run what it names')
    ap.add_argument('--only', help='comma-separated suite names: just these')
    ap.add_argument('--build', help='use this build, do not build')
    ap.add_argument('--logs', type=pathlib.Path, help='default: ~/.cache/q2-gate/<worktree>-<time>/')
    ap.add_argument('--results', type=pathlib.Path, help='also write the results as JSON here')
    ap.add_argument('--python', default=sys.executable)
    args, rest = ap.parse_known_args()
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))  # so run() ends its suites
    build_args = [a for a in rest] or ['--dev', '--', '--new-player']
    if build_args and build_args[0] == '--':
        build_args = build_args[1:]
    plan = None
    if args.plan_json:
        plan = json.loads(sys.stdin.read() if args.plan_json == '-' else pathlib.Path(args.plan_json).read_text())
    logs = args.logs or pathlib.Path.home()/'.cache/q2-gate'/f'{ROOT.name}-{time.strftime("%Y%m%d-%H%M%S")}'
    only = [n for n in (args.only or '').split(',') if n]
    suites = suites_for(plan, [n for n in only if n != 'build'], args.python, logs)
    t = time.monotonic()
    head = subprocess.run(['git', '-C', str(ROOT), 'rev-parse', '--short', 'HEAD'], capture_output=True, text=True).stdout.strip()
    print(f'gate on {ROOT.name} {head}: {", ".join(suites) or "nothing"}; logs in {logs}', flush=True)
    results = run(suites, build_args, logs, args.python, args.build, log=lambda m: print(m, flush=True),
                  force_build='build' in only)
    out = table(results, time.monotonic() - t)
    print('\n' + out)
    (logs/'summary.txt').write_text(out + '\n')
    if args.results:
        args.results.write_text(json.dumps(results))
    return 1 if any(r['rc'] != 0 for r in results.values()) else 0


if __name__ == '__main__':
    sys.exit(main())
