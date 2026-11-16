#!/usr/bin/env python3
"""Merge a list of branches into the integration branch one at a time, testing each, stopping at the first
failure.

    python3 tools/ci/merge_train.py feat/a feat/b fix/c            # in the integration branch's worktree
    python3 tools/ci/merge_train.py --dry-run feat/a feat/b         # what each would run, merging nothing
    python3 tools/ci/merge_train.py --no-final feat/a               # skip the whole run at the end

Run it in a clean worktree of the integration branch (release/p31's is ~/repos/q2-pod-wt/release). For
each branch, in order:

1. `git merge --no-ff BRANCH`. A conflict git rerere has seen resolved before is resolved the same way
   (rerere.enabled and rerere.autoupdate are on in this checkout); any other stops the train with the
   conflicted files listed, the merge left in progress for a person to finish (then run the train again
   with the branches after it).
2. The tests the merge's own changes need (tools/ci/affected.py on the merge's first parent against its
   result), through tools/ci/rebuild.py, patch_shards.py and fw.py.
3. A failure stops the train: the merge stays on the branch `train/failed/<branch>`, the integration
   branch goes back to the last merge that passed, and the report names the branch, the suite and its log.

After the last branch it runs everything once (affected.py --all), as an integration branch must.
Logs go to ~/.cache/q2-merge-train/<time>/. Branches are merged, never rebased: other worktrees have
them checked out. docs/building.md#merge-train.
"""
import argparse
import pathlib
import subprocess
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'tools/ci'))
import affected  # noqa: E402


def git(*args, check=True):
    return subprocess.run(['git', '-C', str(ROOT), *args], capture_output=True, text=True, check=check)


def build(dev, log):
    with open(log, 'a') as f:
        p = subprocess.run([sys.executable, str(ROOT/'tools/ci/rebuild.py'), *(['--dev'] if dev else [])],
                           cwd=ROOT, stdout=subprocess.PIPE, stderr=f, text=True)
    return p.stdout.strip().splitlines()[-1] if p.returncode == 0 and p.stdout.strip() else None


def test(plan, logs, name, dev):
    """Run plan with each suite's output in its own log; returns (failed suite or None, its log)."""
    d = plan.as_dict()
    needs_build = d['patch'] or any(o.startswith('test/') and o != 'test/build.py' for o in d['other'])
    b = None
    if needs_build or 'test/build.py' in d['other']:
        b = build(dev, logs/f'{name}.build.log')
        if not b:
            return 'build', logs/f'{name}.build.log'
    log = logs/f'{name}.tests.log'
    with open(log, 'w') as f:
        p = subprocess.run([sys.executable, str(ROOT/'tools/ci/affected.py'), '--plan-json', '-',
                            '--run', b or ''], input=plan.to_json(), cwd=ROOT, stdout=f, stderr=subprocess.STDOUT, text=True)
    if p.returncode:
        failed = [line.split(':')[0][3:] for line in log.read_text().splitlines() if line.startswith('== ') and 'FAILED' in line]
        return ', '.join(failed) or 'tests', log
    return None, log


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('branches', nargs='+')
    ap.add_argument('--dry-run', action='store_true', help='print what each merge would test; merge nothing')
    ap.add_argument('--no-final', dest='final', action='store_false', help='skip the whole run after the last merge')
    ap.add_argument('--release', dest='dev', action='store_false', help='test a release build (default: --dev)')
    args = ap.parse_args()
    if subprocess.run([sys.executable, '-c', 'import pytest, unicorn'], capture_output=True).returncode:
        raise SystemExit(f'{sys.executable} lacks pytest or unicorn: run the train with the test venv\'s python')
    head = git('rev-parse', '--abbrev-ref', 'HEAD').stdout.strip()
    if not args.dry_run and git('status', '--porcelain', '--untracked-files=no').stdout.strip():
        raise SystemExit(f'{ROOT} has uncommitted changes: the train needs a clean worktree of the integration branch')
    logs = pathlib.Path.home()/'.cache/q2-merge-train'/time.strftime('%Y%m%d-%H%M%S')
    logs.mkdir(parents=True, exist_ok=True)
    print(f'train onto {head} in {ROOT}; logs in {logs}', flush=True)
    done = []
    for branch in args.branches:
        t = time.perf_counter()
        before = git('rev-parse', 'HEAD').stdout.strip()
        if args.dry_run:
            base = git('merge-base', before, branch).stdout.strip()
            diff = git('diff', '--name-only', base, branch).stdout.split()
            plan = affected.plan_for({p: set() for p in diff})
            print(f'{branch}: {len(diff)} files; would run {plan.summary()}')
            continue
        m = git('merge', '--no-ff', '--no-edit', '-m', f"Merge branch '{branch}' into {head}", branch, check=False)
        if m.returncode:
            unmerged = git('diff', '--name-only', '--diff-filter=U').stdout.split()
            if unmerged:
                print(f'\nSTOPPED at {branch}: merge conflicts rerere could not resolve, in\n  ' + '\n  '.join(unmerged)
                      + f'\nThe merge is left in progress in {ROOT}: resolve, commit, then run the train again with '
                      f'the branches after {branch}. Merged before it: {", ".join(done) or "none"}.')
                return 2
            git('commit', '--no-edit')  # rerere resolved every file
            print(f'{branch}: conflicts resolved by rerere, as recorded before', flush=True)
        plan = affected.plan_for(affected.changed(before, 'HEAD'))
        print(f'{branch}: merged; testing {plan.summary()}', flush=True)
        failed, log = test(plan, logs, branch.replace('/', '_'), args.dev)
        if failed:
            git('branch', '-f', f'train/failed/{branch}', 'HEAD')
            git('reset', '--hard', before)
            print(f'\nSTOPPED at {branch}: {failed} failed after the merge ({time.perf_counter() - t:.0f} s). Log: {log}\n'
                  f'The merge is kept as train/failed/{branch}; {head} is back at {before[:8]}, after '
                  f'{done[-1] if done else "nothing this train merged"}. Merged and passing: {", ".join(done) or "none"}.')
            return 1
        done.append(branch)
        print(f'{branch}: passed ({time.perf_counter() - t:.0f} s)', flush=True)
    if args.final and done:
        plan = affected.Plan()
        plan.everything('the train\'s end: everything')
        print(f'all {len(done)} merged; the whole run', flush=True)
        failed, log = test(plan, logs, 'final', args.dev)
        if failed:
            print(f'\nFINAL RUN FAILED: {failed}. Log: {log}. Every merge passed its own tests; '
                  f'the failure comes from them together. {head} keeps the merges.')
            return 1
        print('final run: passed')
    return 0


if __name__ == '__main__':
    sys.exit(main())
