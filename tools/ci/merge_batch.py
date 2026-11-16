#!/usr/bin/env python3
"""Integrate many branches at once: merge them all, build once, test once, and bisect only on a failure.

    python3 tools/ci/merge_batch.py feat/a feat/b fix/c ...   # in the integration branch's clean worktree
    python3 tools/ci/merge_batch.py --from-ready              # every branch workers marked ready
    python3 tools/ci/merge_batch.py --dry-run --from-ready    # the plan: conflicts and tests; merges nothing
    python3 tools/ci/merge_batch.py --dry-run --onto release/p31 feat/a   # the same from any worktree
    python3 tools/ci/merge_batch.py --mark-ready              # in a feature worktree: queue this branch

Run it with ~/repos/q2-pod/.venv/bin/python (gate.py's suites need pytest-xdist, coverage and mypy) in a
clean worktree of the integration branch (release/p31's is ~/repos/q2-pod-wt/release). It never touches that worktree's files until the end; the work happens in
worktrees of its own, ~/.cache/q2-merge-train/wt/<integration>-L<level>, one per bisection level, kept
between runs, each with its one reused build (tools/ci/rebuild.py, ~/.cache/q2-builds/<that name>-dev).

1. Every branch is merged (`git merge --no-ff`, rerere as in merge_train.py) onto the integration tip in
   the level-0 worktree, as the branch train/batch-<time>. A branch that conflicts (rerere cannot resolve
   it) or is already in the tip is skipped and reported; the rest go on.
2. One build, then one test run: what tools/ci/affected.py says the batch's whole diff needs, through
   tools/ci/gate.py (every suite at once as memory allows, under q2-heavy); a plan that runs everything
   is the whole release gate.
3. Green: the integration branch is fast-forwarded to the batch's tip (merge commits only; no branch is
   rebased) and the landed branches are listed.
4. Red: the batch is split in halves and only the failing suites run on each, on top of what has passed
   so far, down to the branch each failure needs; a half known to fail (its sibling passed) is not tested
   again. The culprits are left out, the remainder is tested again in full (what its own diff needs),
   and lands when that is green. Before any of that, the failing suites run on the tip alone: when the tip
   fails them too, nothing lands, as every branch would look guilty.

A branch is merged at the commit it was queued at (the ready list records it), so its owner can keep
committing while a batch runs. Logs and a summary go to ~/.cache/q2-merge-train/<time>/. Exit status: 0
when every branch landed, 1 when any was left out, 2 when nothing could land (the tip moved, a bad state).
docs/building.md#merge-batch.
"""
import argparse
import contextlib
import dataclasses
import fcntl
import json
import os
import pathlib
import re
import signal
import subprocess
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[2]
CACHE = pathlib.Path.home()/'.cache/q2-merge-train'
READY = CACHE/'ready.txt'
SUITES = ('unit', 'fw', 'patch')  # a plan's keyed suites; each path in its 'other' list is a suite too


# --- what the batch does with the repository and the tests (Real below; tests use a fake) ---

@dataclasses.dataclass
class Suite:
    ok: bool
    log: str = ''
    failures: list = dataclasses.field(default_factory=list)


@dataclasses.dataclass
class Report:
    landed: list = dataclasses.field(default_factory=list)
    skipped: dict = dataclasses.field(default_factory=dict)    # branch: why (a conflict, already in)
    culprits: dict = dataclasses.field(default_factory=dict)   # branch: {suite: Suite}
    head: object = None       # the commit to land, None when nothing passed
    tests: int = 0            # test runs made, the first included


def restrict(plan, suites):
    """plan (affected.py's dict) cut to the named suites; 'build' keeps nothing but asks for the build."""
    out = {k: (plan[k] if k in suites else []) for k in SUITES}
    out['other'] = [o for o in plan['other'] if o in suites]
    out['why'] = plan.get('why', [])
    return out


def suites_of(plan):
    return [k for k in SUITES if plan[k]] + list(plan['other'])


def integrate(ops, tip, branches, log=print):
    """The batch's whole logic over ops (assemble, test); returns a Report. Each test runs on a commit made
    by merging branches onto one that already passed (tip at first), so what lands is a tested commit."""
    r = Report()

    def assemble(level, base, cands):
        head, merged, skipped = ops.assemble(level, base, cands)
        r.skipped.update(skipped)
        return head, merged

    def test(level, head, suites, tag):
        r.tests += 1
        res = ops.test(level, head, suites, tag)
        bad = {s: v for s, v in res.items() if not v.ok}
        log(f'  {tag}: {"passed" if not bad else "FAILED " + ", ".join(bad)} ({", ".join(res) or "nothing to run"})')
        return bad

    head, todo = assemble(0, tip, branches)
    round_ = 0
    while todo:
        round_ += 1
        bad = test(0, head, None, f'round {round_}: all {len(todo)} together')
        if not bad:
            r.head, r.landed = head, todo
            return r
        suites = sorted(bad)
        # A red tip would make every branch that reaches the failing suites look guilty: check it first.
        if test(0, tip, suites, f'round {round_}: the tip alone'):
            log(f'round {round_}: {", ".join(suites)} fail on the integration tip without any branch: '
                f'nothing lands; make the tip green first')
            r.skipped.update({b: 'not landed: the integration tip fails the same suites' for b in todo})
            return r
        good = {'head': tip, 'branches': []}
        before = len(r.culprits)

        def resolve(level, cands, known, inferred=False):
            """Find the culprits among cands on top of good. known: the failures good+cands has, from a test
            of exactly that, or inferred (its first half passed after good+cands failed), else None. A single
            branch is never convicted on an inference: a flaky failure would blame the batch's last branch."""
            if not cands:
                return
            if known is None or (inferred and len(cands) == 1):
                h, merged = assemble(level, good['head'], cands)
                if not merged:
                    return
                bad = test(level, h, suites, f'level {level}: {", ".join(merged)}')
                if not bad:
                    good['head'] = h
                    good['branches'] += merged
                    return
                cands, known = merged, bad
            if len(cands) == 1:
                r.culprits[cands[0]] = known
                log(f'  culprit: {cands[0]} ({", ".join(known)})')
                return
            half = len(cands) // 2
            n = len(r.culprits)
            resolve(level + 1, cands[:half], None)
            # the first half passed whole, so good+second half is the commit that failed: no test needed
            # until it is down to one branch
            if len(r.culprits) == n:
                resolve(level + 1, cands[half:], known, inferred=True)
            else:
                resolve(level + 1, cands[half:], None)

        log(f'round {round_}: bisecting {len(todo)} branches on {", ".join(suites)}')
        resolve(0, todo, bad)
        if len(r.culprits) == before:
            log(f'round {round_}: no half fails on its own: nothing lands (a flaky test, or a failure the '
                f'bisection runs cannot see); logs above')
            r.skipped.update({b: 'not landed: the batch failed and no culprit was found' for b in todo})
            return r
        # the remainder is a tested commit already, but only on the failing suites: test it whole
        head, todo = good['head'], good['branches']
    return r


# --- the real repository and tests ---

def git(*args, cwd=ROOT, check=True):
    return subprocess.run(['git', '-C', str(cwd), *args], capture_output=True, text=True, check=check)


def failures(log_text, wt):
    """The failing test names in a suite's log: pytest's FAILED/ERROR lines, patch_shards.py's line (with
    test/suite_source.py's FILE:LINE for it), else the log's last lines."""
    out = []
    for line in log_text.splitlines():
        if re.match(r'(FAILED|ERROR) \S+::', line):
            out.append(line[:240])
        m = re.match(r'FAILED at line (\d+)', line)
        if m:
            src = subprocess.run([sys.executable, str(wt/'test/suite_source.py'), m[1]], capture_output=True,
                                 text=True, cwd=wt)
            out.append(f'test/patch.py joined line {m[1]} = {src.stdout.strip() or "?"}')
    return out or [line[:240] for line in log_text.splitlines() if line.strip()][-3:]


class Real:
    def __init__(self, target, tip, logs, dev=True, log=print):
        self.target, self.tip, self.logs, self.dev, self.log = target, tip, logs, dev, log
        self.shas = {}
        self.runs = 0  # numbers each test's log directory
        self.batch_plan = None  # the first plan made, the whole batch's: what the tip alone is checked with
        self.build_args = ['--dev', '--', '--new-player'] if dev else ['--', '--new-player']  # gate.py's default

    def wt(self, level):
        path = CACHE/'wt'/f'{self.target.replace("/", "_")}-L{level}'
        if not (path/'.git').exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            git('worktree', 'add', '-q', '--detach', str(path), self.tip)
        return path

    def assemble(self, level, base, branches):
        wt = self.wt(level)
        git('checkout', '-q', '--detach', '-f', base, cwd=wt)
        git('clean', '-q', '-ffdx', cwd=wt)
        merged, skipped = [], {}
        for b in branches:
            sha = self.shas[b]
            if git('merge-base', '--is-ancestor', sha, 'HEAD', cwd=wt, check=False).returncode == 0:
                skipped[b] = 'already in the integration branch'
                continue
            m = git('merge', '--no-ff', '--no-edit', '-m', f"Merge branch '{b}' into {self.target}", sha,
                    cwd=wt, check=False)
            if m.returncode:
                unmerged = git('diff', '--name-only', '--diff-filter=U', cwd=wt).stdout.split()
                if unmerged or not git('rev-parse', '-q', '--verify', 'MERGE_HEAD', cwd=wt, check=False).stdout:
                    git('merge', '--abort', cwd=wt, check=False)
                    git('reset', '-q', '--hard', cwd=wt)
                    why = f'conflicts rerere could not resolve, in {", ".join(unmerged)}' if unmerged else \
                        f'merge failed: {(m.stderr or m.stdout).strip()[:200]}'
                    skipped[b] = why + (f' (merging onto {base[:8]} plus {", ".join(merged)})' if merged else '')
                    self.log(f'  skipped {b}: {skipped[b]}')
                    continue
                git('commit', '-q', '--no-edit', cwd=wt)  # rerere resolved every file
                self.log(f'  {b}: conflicts resolved by rerere, as recorded before')
            merged.append(b)
        return git('rev-parse', 'HEAD', cwd=wt).stdout.strip(), merged, skipped

    def plan(self, wt):
        p = subprocess.run([sys.executable, str(wt/'tools/ci/affected.py'), '--base', self.tip, '--json'],
                           cwd=wt, capture_output=True, text=True, check=True)
        return json.loads(p.stdout)

    def test(self, level, head, suites, tag):
        """head's tests through the worktree's tools/ci/gate.py: the plan affected.py makes for head against the
        tip (the whole gate when that plan is everything), cut to suites when given (gate.py --only: a suite
        the plan lacks is skipped, as head's changes cannot reach it)."""
        wt = self.wt(level)
        if git('rev-parse', 'HEAD', cwd=wt).stdout.strip() != head:
            git('checkout', '-q', '--detach', '-f', head, cwd=wt)
            git('clean', '-q', '-ffdx', cwd=wt)
        self.runs += 1
        out = self.logs/f'{self.runs:02d}-{re.sub(r"[^\w.-]+", "_", tag)[:60]}'
        out.mkdir(parents=True, exist_ok=True)
        if head == self.tip:
            # the tip's own diff is empty: check it with the batch's plan, as far as the tip has those files
            # (the joined suite's line numbers are the batch's, so test/patch.py runs whole)
            plan = dict(self.batch_plan, patch='all' if self.batch_plan['patch'] else [])
            for k in ('unit', 'fw'):
                if plan[k] != 'all':
                    plan[k] = [f for f in plan[k] if (wt/'test'/k/f).exists()]
            plan['other'] = [o for o in plan['other'] if (wt/o).exists()]
        else:
            plan = self.plan(wt)
            if self.batch_plan is None:
                self.batch_plan = plan
        (out/'plan.json').write_text(json.dumps(plan))
        cmd = [sys.executable, str(wt/'tools/ci/gate.py'), '--plan-json', str(out/'plan.json'), '--logs', str(out),
               '--results', str(out/'results.json'), *(['--only', ','.join(suites)] if suites else []),
               '--', *self.build_args]
        t = time.perf_counter()
        with open(out/'gate.log', 'w') as f:
            p = subprocess.Popen(cmd, cwd=wt, stdout=f, stderr=subprocess.STDOUT)
            try:
                p.wait()
            finally:
                if p.poll() is None:  # this batch was stopped: so is its gate, which stops its suites
                    p.terminate()
                    p.wait()
        try:
            results = json.loads((out/'results.json').read_text())
        except (OSError, ValueError):
            return {'gate': Suite(False, str(out/'gate.log'), failures((out/'gate.log').read_text(), wt))}
        self.log(f'  gate: {time.perf_counter() - t:.0f} s')
        self.log('\n'.join('    ' + line for line in (out/'summary.txt').read_text().splitlines()))
        return {s: Suite(r['rc'] == 0, r['log'], [] if r['rc'] == 0 else failures(pathlib.Path(r['log']).read_text(), wt))
                for s, r in results.items()}


# --- the ready list ---

@contextlib.contextmanager
def locked(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path.with_suffix(path.suffix + '.lock'), 'w') as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)
        yield


def read_ready(path):
    """[(branch, sha or None)] from the ready list, the last line for a branch winning, in first-seen order."""
    out = {}
    if path.exists():
        for line in path.read_text().splitlines():
            f = line.split('#')[0].split()
            if f:
                out[f[0]] = f[1] if len(f) > 1 else None
    return list(out.items())


def take_ready(path, keep):
    """Read and empty the ready list, keeping a copy at keep."""
    with locked(path):
        items = read_ready(path)
        if path.exists():
            keep.write_text(path.read_text())
            path.write_text('')
    return items


def append_ready(path, items):
    with locked(path):
        with open(path, 'a') as f:
            for b, sha in items:
                f.write(f'{b} {sha}\n' if sha else f'{b}\n')


def mark_ready(path, branch):
    if git('status', '--porcelain', '--untracked-files=no').stdout.strip():
        raise SystemExit(f'{ROOT} has uncommitted changes: commit them, run affected.py, then mark it ready')
    branch = branch or git('rev-parse', '--abbrev-ref', 'HEAD').stdout.strip()
    sha = git('rev-parse', f'{branch}^{{commit}}').stdout.strip()
    append_ready(path, [(branch, sha)])
    print(f'{branch} {sha[:8]} queued in {path}')
    return 0


# --- the dry run ---

def dry_run(target, tip, items):
    """Each branch merged in memory (git merge-tree) onto the last: what conflicts, what the batch tests."""
    sys.path.insert(0, str(ROOT/'tools/ci'))
    import affected  # noqa: E402
    head, merged = tip, []
    for b, sha in items:
        if git('merge-base', '--is-ancestor', sha, head, check=False).returncode == 0:
            print(f'{b}: already in {target}: skipped')
            continue
        m = git('merge-tree', '--write-tree', '--name-only', '--no-messages', head, sha, check=False)
        lines = m.stdout.split()
        if m.returncode:
            print(f'{b}: CONFLICTS in {", ".join(lines[1:])}: skipped (unless rerere has this resolution)')
            continue
        head = git('commit-tree', lines[0], '-p', head, '-p', sha, '-m', f'dry run: {b}').stdout.strip()
        files = git('diff', '--name-only', f'{tip}...{sha}').stdout.split()
        merged.append(b)
        print(f'{b} {sha[:8]}: merges cleanly; {len(files)} files')
    diff = git('diff', '--name-only', tip, head).stdout.split()
    plan = affected.plan_for({p: set() for p in diff})
    level0 = f'{target.replace("/", "_")}-L0'
    print(f'\nbatch of {len(merged)} onto {target} {tip[:8]}: {len(diff)} files; one build '
          f'(~/.cache/q2-builds/{level0}-dev) and one run of: {plan.summary()}'
          f'\non a failure, bisection: at most {2 * max(1, len(merged) - 1)} runs of the failing suites, '
          f'then the remainder once more in full')
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('branches', nargs='*')
    ap.add_argument('--from-ready', action='store_true', help='also take every branch in the ready list (and empty it)')
    ap.add_argument('--ready', type=pathlib.Path, default=READY, help=f'the ready list (default {READY})')
    ap.add_argument('--mark-ready', nargs='?', const='', metavar='BRANCH',
                    help='append BRANCH (default: this worktree\'s) at its current commit to the ready list')
    ap.add_argument('--dry-run', action='store_true', help='print the plan; merge, build and test nothing')
    ap.add_argument('--onto', metavar='BRANCH', help='with --dry-run: plan onto BRANCH, not this worktree\'s')
    ap.add_argument('--release', dest='dev', action='store_false', help='test a release build (default: --dev)')
    args = ap.parse_args()
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))  # so a running gate is stopped too
    if args.mark_ready is not None:
        return mark_ready(args.ready, args.mark_ready)
    if args.onto and not args.dry_run:
        raise SystemExit('--onto is for --dry-run: a real batch lands on the branch of the worktree it runs in')
    target = args.onto or git('rev-parse', '--abbrev-ref', 'HEAD').stdout.strip()
    tip = git('rev-parse', f'{args.onto or "HEAD"}^{{commit}}').stdout.strip()
    stamp = time.strftime('%Y%m%d-%H%M%S')
    logs = CACHE/stamp
    queued = read_ready(args.ready) if args.from_ready else []
    if args.dry_run:
        items = [(b, None) for b in args.branches] + queued
    else:
        if subprocess.run([sys.executable, '-c', 'import pytest, unicorn, xdist, coverage, mypy'], capture_output=True).returncode:
            raise SystemExit(f'{sys.executable} lacks pytest, unicorn, pytest-xdist, coverage or mypy: run the batch '
                             'with ~/repos/q2-pod/.venv/bin/python')
        if git('status', '--porcelain', '--untracked-files=no').stdout.strip():
            raise SystemExit(f'{ROOT} has uncommitted changes: the batch needs a clean worktree of the integration branch')
        logs.mkdir(parents=True, exist_ok=True)
        lock = open(CACHE/f'batch-{target.replace("/", "_")}.lock', 'w')
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise SystemExit(f'a batch onto {target} is running already (lock {lock.name})')
        if args.from_ready:
            queued = take_ready(args.ready, logs/'ready.taken')
        items = [(b, None) for b in args.branches] + queued
    seen, uniq = set(), []
    for b, sha in items:
        if b not in seen:
            seen.add(b)
            resolved = git('rev-parse', '-q', '--verify', f'{sha or b}^{{commit}}', check=False).stdout.strip()
            if not resolved:
                print(f'{b}: no such branch or commit: skipped')
                continue
            uniq.append((b, resolved))
    if not uniq:
        print('no branches to integrate')
        return 0
    if args.dry_run:
        return dry_run(target, tip, uniq)

    summary = logs/'summary.txt'

    def log(msg):
        print(msg, flush=True)
        with open(summary, 'a') as f:
            f.write(msg + '\n')
    t0 = time.perf_counter()
    log(f'batch of {len(uniq)} onto {target} {tip[:8]} in {ROOT}; logs in {logs}')
    real = Real(target, tip, logs, args.dev, log)
    real.shas = dict(uniq)
    r = integrate(real, tip, [b for b, _ in uniq], log)
    batch = f'train/batch-{stamp}'
    for b, why in r.culprits.items():
        git('branch', '-f', f'train/failed/{b}', real.shas[b])
    status = 1 if (r.skipped or r.culprits) else 0
    if r.head:
        git('branch', '-f', batch, r.head)
        m = git('merge', '--ff-only', '-q', r.head, check=False)
        if m.returncode:
            log(f'\nCOULD NOT LAND: {target} moved during the batch ({m.stderr.strip()[:200]}). The tested '
                f'commit is {batch}; the branches go back on the ready list.')
            append_ready(args.ready, [(b, real.shas[b]) for b in r.landed])
            r.landed, status = [], 2
        else:
            git('branch', '-D', batch, check=False)
    elif not r.culprits:
        status = 2
    elapsed = time.perf_counter() - t0
    log(f'\n=== batch onto {target}: {len(r.landed)} landed, {len(r.culprits)} failed, {len(r.skipped)} skipped; '
        f'{r.tests} test runs, {elapsed / 60:.1f} min ===')
    if r.landed:
        log(f'{target} now {git("rev-parse", "--short", "HEAD").stdout.strip()}; landed: {", ".join(r.landed)}')
    for b, bad in r.culprits.items():
        log(f'FAILED {b} (kept as train/failed/{b}):')
        for s, v in bad.items():
            log(f'  {s}: {v.log}')
            for line in v.failures[:5]:
                log(f'    {line}')
    for b, why in r.skipped.items():
        log(f'SKIPPED {b}: {why}')
    log(f'summary: {summary}')
    return status


if __name__ == '__main__':
    sys.exit(main())
