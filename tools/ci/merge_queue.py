#!/usr/bin/env python3
"""The merge queue: lands every ready branch on the trunk, a batch at a time, with no agent in the loop.

    python3 tools/ci/merge_queue.py run [--worktree DIR] [--push] [--once]   # the service (docs/orchestration.md#install)
    python3 tools/ci/merge_queue.py status                                    # what it is doing, the last events
    python3 tools/ci/merge_queue.py resume                                    # after a stop, a red tip or two crashes

A worker queues its branch with `merge_batch.py --mark-ready`. The queue takes everything that is ready
(one branch or fifteen: it never waits to fill a batch), and runs merge_batch.py on it in the trunk's
worktree: merge, one build, the merge tier of tools/ci/gate.py once, bisection by halves on a failure.
What passed is fast-forwarded onto the trunk; nothing else can move the trunk
(tools/ci/hooks/reference-transaction lets a ref of the trunk change only for a process with Q2_QUEUE=1,
which is this one's batches). Branches that become ready during a batch go in the next.

Every outcome is one line in ~/.cache/q2-queue/events.log (q2ci.event), which the main thread watches:

    READY branch                    a worker queued it (written by --mark-ready)
    BATCH n branches                a batch started
    LANDED branch                   it is in the trunk
    REJECTED branch                 the batch failed because of it: suite, first failure and log follow
    CONFLICT branch                 it no longer merges: its owner merges the trunk in and marks it ready again
    SKIPPED branch                  already in the trunk, or no such commit
    REQUEUED branch                 not its fault (a crash, a red tip, an undecided failure): it runs again
    TIP_RED suites                  the trunk fails on its own: only `--mark-ready --fixes-tip` branches are taken
    INFRA suites                    suites crashed twice: the harness or the machine, no branch
    POST_OK / POST_RED              the post tier (sanitizers, coverage) on the trunk's tip
    PUSHED / PUSH_FAILED            the trunk on origin
    QUEUE_STOPPED / QUEUE_STARTED / QUEUE_ERROR

and its state is ~/.cache/q2-queue/status.json. It stops itself (the STOP file) after two batches in a row
that only crashed, since a third would crash too; `q2 stop-all` stops it the same way, and `resume`
clears the stop, the crash count and a red tip's hold.

When nothing is ready and the tip has not had it, the post tier runs on the tip: a failure there is
reported (POST_RED) against a commit that has already landed and is fixed by the next branch, as any bug.
A branch whose batch failed with no culprit found (a flaky test) is tried once more, then rejected.
"""
import argparse
import contextlib
import fcntl
import json
import os
import pathlib
import signal
import subprocess
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import merge_batch  # noqa: E402
import q2ci  # noqa: E402

UNDECIDED_TRIES = 2  # batches a branch may fail in with no culprit found before it is rejected
CRASH_STOP = 2       # batches in a row that only crashed before the queue stops itself


def git(wt, *args, check=False, env=None):
    return subprocess.run(['git', '-C', str(wt), *args], capture_output=True, text=True, check=check,
                          env={**os.environ, **(env or {})})


class Queue:
    """One trunk's queue. run_batch and run_post are the two slow steps; tests replace them."""

    def __init__(self, worktree, trunk, directory=None, ready=None, push=False, post=True, python=sys.executable,
                 log=print):
        self.wt, self.trunk, self.push, self.post, self.python, self.log = pathlib.Path(worktree), trunk, push, post, python, log
        self.dir = pathlib.Path(directory or q2ci.queue_dir())
        self.ready = pathlib.Path(ready or merge_batch.READY)
        self.child = None
        self.state = {'tip_red': [], 'crashes': 0, 'undecided': {}, 'post_tree': None, 'post_ok': None,
                      'last_landed': None, 'batches': 0}
        self.state_file = self.dir/'state.json'
        if self.state_file.exists():
            with contextlib.suppress(ValueError):
                self.state.update(json.loads(self.state_file.read_text()))

    # --- reporting ---

    def event(self, kind, text='', **fields):
        self.log(q2ci.event(kind, text, self.dir, **fields))

    def save(self, doing, **more):
        q2ci.write_json(self.state_file, self.state)
        q2ci.write_json(self.dir/'status.json', {
            'state': doing, 'trunk': self.trunk, 'tip': self.tip(), 'worktree': str(self.wt), 'pid': os.getpid(),
            'updated': time.strftime('%Y-%m-%dT%H:%M:%S'),
            'ready': [' '.join(x for x in (b, (sha or '')[:8], 'fix' if fix else '') if x)
                      for b, sha, fix in merge_batch.read_ready_lines(self.ready)],
            'tip_red': self.state['tip_red'], 'crashes': self.state['crashes'],
            'last_landed': self.state['last_landed'],
            'post': {'tree': self.state['post_tree'], 'ok': self.state['post_ok']}, **more})

    def tip(self):
        return git(self.wt, 'rev-parse', '-q', '--verify', f'{self.trunk}^{{commit}}').stdout.strip()

    def stopped(self):
        return (self.dir/'STOP').exists()

    def stop(self, why):
        (self.dir/'STOP').write_text(why + '\n')
        self.event('QUEUE_STOPPED', why)

    # --- the slow steps ---

    def run_batch(self, batch, ready):
        """merge_batch.py on the lines of ready, in the trunk's worktree; its exit status. Q2_QUEUE=1 is what
        lets its fast-forward move the trunk."""
        cmd = [self.python, str(self.wt/'tools/ci/merge_batch.py'), '--from-ready', '--ready', str(ready),
               '--result', str(batch/'result.json')]
        with open(batch/'batch.log', 'w') as f:
            return self.wait(subprocess.Popen(cmd, cwd=self.wt, stdout=f, stderr=subprocess.STDOUT,
                                              env={**os.environ, 'Q2_QUEUE': '1', 'Q2_HEAVY_CLASS': 'queue'},
                                              start_new_session=True))

    def run_post(self, logs):
        """The post tier on the tip; (exit status, the failed suites)."""
        results = logs/'results.json'
        cmd = [self.python, str(self.wt/'tools/ci/gate.py'), '--tier', 'post', '--logs', str(logs), '--results', str(results)]
        logs.mkdir(parents=True, exist_ok=True)
        with open(logs/'gate.log', 'w') as f:
            rc = self.wait(subprocess.Popen(cmd, cwd=self.wt, stdout=f, stderr=subprocess.STDOUT, start_new_session=True,
                                            env={**os.environ, 'Q2_HEAVY_CLASS': 'queue'}))
        try:
            bad = sorted(n for n, r in json.loads(results.read_text()).items() if r['rc'] != 0)
        except (OSError, ValueError):
            bad = ['gate'] if rc else []
        return rc, bad

    def wait(self, child):
        """child's exit status; a STOP file or this process's end stops it and everything it started."""
        self.child = child
        try:
            while child.poll() is None:
                if self.stopped():
                    self.kill()
                time.sleep(1)
            return child.returncode
        finally:
            self.kill()
            self.child = None

    def kill(self):
        c = self.child
        if c is not None and c.poll() is None:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(c.pid, signal.SIGTERM)  # merge_batch.py ends its gate, which ends its suites
            try:
                c.wait(20)
            except subprocess.TimeoutExpired:
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(c.pid, signal.SIGKILL)
                c.wait()

    # --- one turn ---

    def take(self, batch):
        """Move the lines this batch takes from the ready list to its own file; returns them. While the tip
        is red, only branches marked as its fix are taken."""
        with merge_batch.locked(self.ready):
            lines = merge_batch.read_ready_lines(self.ready)
            mine = [x for x in lines if x[2]] if self.state['tip_red'] else lines
            if not mine:
                return []
            batch.mkdir(parents=True, exist_ok=True)
            rest = [x for x in lines if x not in mine]
            self.ready.write_text(''.join(' '.join(y for y in (b, sha, 'fix' if fix else None) if y) + '\n'
                                          for b, sha, fix in rest))
        merge_batch.append_ready(batch/'ready.txt', mine)
        return mine

    def requeue(self, items, why):
        if items:
            merge_batch.append_ready(self.ready, items)
            for b, *_ in items:
                self.event('REQUEUED', b, why=why.replace(' ', '_'))

    def check(self):
        """Why the trunk's worktree cannot take a batch, or None."""
        head = git(self.wt, 'rev-parse', '--abbrev-ref', 'HEAD')
        if head.returncode:
            return f'{self.wt} is not a git worktree'
        if head.stdout.strip() != self.trunk:
            return f'{self.wt} is on {head.stdout.strip()}, not the trunk {self.trunk}'
        if git(self.wt, 'status', '--porcelain', '--untracked-files=no').stdout.strip():
            return f'{self.wt} has uncommitted changes'
        return None

    def turn(self):
        """Do the next thing; returns what it did: 'stopped', 'error', 'batch', 'post' or 'idle'."""
        if self.stopped():
            self.save('stopped', why=(self.dir/'STOP').read_text().strip())
            return 'stopped'
        wrong = self.check()
        if wrong:
            if self.state.get('error') != wrong:
                self.state['error'] = wrong
                self.event('QUEUE_ERROR', wrong)
            self.save('error', why=wrong)
            return 'error'
        self.state.pop('error', None)
        stamp = time.strftime('%Y%m%d-%H%M%S')
        batch = self.dir/'batches'/stamp
        mine = self.take(batch)
        if mine:
            self.batch(batch, mine)
            return 'batch'
        if self.post and not self.state['tip_red']:
            tree = git(self.wt, 'rev-parse', f'{self.trunk}^{{tree}}').stdout.strip()
            if tree and tree != self.state['post_tree']:
                self.save('post', since=time.strftime('%H:%M:%S'))
                rc, bad = self.run_post(self.dir/'post'/stamp)
                if self.stopped():
                    return 'stopped'
                self.state['post_tree'], self.state['post_ok'] = tree, rc == 0
                self.event('POST_OK' if rc == 0 else 'POST_RED', ','.join(bad), tip=self.tip()[:8],
                           logs=self.dir/'post'/stamp)
                self.save('idle')
                return 'post'
        self.save('idle')
        return 'idle'

    def batch(self, batch, mine):
        names = [b for b, *_ in mine]
        self.state['batches'] += 1
        self.event('BATCH', f'{len(mine)} branches', branches=','.join(names), log=batch/'batch.log')
        self.save('batch', batch={'branches': names, 'since': time.strftime('%H:%M:%S'), 'log': str(batch/'batch.log')})
        rc = self.run_batch(batch, batch/'ready.txt')
        # lines merge_batch.py put back in its ready file (the trunk moved under it) return to the real one
        self.requeue(merge_batch.read_ready_lines(batch/'ready.txt'), 'the trunk moved during the batch')
        try:
            result = json.loads((batch/'result.json').read_text())
        except (OSError, ValueError):
            result = None
        if self.stopped():
            self.requeue(mine, 'the queue was stopped during its batch')
            self.save('stopped')
            return
        if result is None:
            self.state['crashes'] += 1
            self.event('QUEUE_ERROR', f'the batch ended with no result (exit {rc})', log=batch/'batch.log')
            self.requeue(mine, 'its batch ended with no result')
            self.after_crash()
            return
        by_name = {b: (b, sha, fix) for b, sha, fix in mine}
        for b, sha in result['landed'].items():
            self.event('LANDED', b, sha=sha[:8], trunk=result['head'][:8])
            self.state['undecided'].pop(b, None)
        for b, bad in result['culprits'].items():
            suite, first = next(iter(bad.items()))
            self.event('REJECTED', b, suite=','.join(bad), log=first['log'],
                       first=(first['failures'] or ['?'])[0].replace(' ', '_')[:160])
        again = []
        for b, why in result['skipped'].items():
            if 'conflict' in why or 'merge failed' in why:
                self.event('CONFLICT', b, why=why.replace(' ', '_')[:200])
            elif 'tip fails' in why or 'crashed' in why:
                again.append((by_name[b], why.split(':')[-1].strip()))
            elif 'no culprit' in why:
                n = self.state['undecided'][b] = self.state['undecided'].get(b, 0) + 1
                if n < UNDECIDED_TRIES:
                    again.append((by_name[b], 'its batch failed and no culprit was found'))
                else:
                    self.state['undecided'].pop(b)
                    self.event('REJECTED', b, suite='undecided', log=batch/'batch.log',
                               first=f'its_batch_failed_{n}_times_with_no_culprit_found')
            else:
                self.event('SKIPPED', b, why=why.replace(' ', '_')[:200])
        for item, why in again:
            self.requeue([item], why)
        if result['landed']:
            self.state['tip_red'], self.state['crashes'] = [], 0
            self.state['last_landed'] = {'when': time.strftime('%Y-%m-%dT%H:%M:%S'), 'tip': result['head'],
                                         'branches': sorted(result['landed'])}
            if self.push:
                p = git(self.wt, 'push', 'origin', f'{result["head"]}:refs/heads/{self.trunk}')
                self.event('PUSHED' if p.returncode == 0 else 'PUSH_FAILED', self.trunk, tip=result['head'][:8],
                           why=None if p.returncode == 0 else p.stderr.strip().replace(' ', '_')[:160])
        if result['tip_red']:
            if not self.state['tip_red']:
                self.event('TIP_RED', ','.join(result['tip_red']), tip=result['tip'][:8], log=batch/'batch.log')
            self.state['tip_red'] = result['tip_red']
        if result['infra']:
            self.state['crashes'] += 1
            self.event('INFRA', ','.join(result['infra']), log=batch/'batch.log')
            self.after_crash()
        elif not result['tip_red']:
            self.state['crashes'] = 0
        self.save('idle')

    def after_crash(self):
        if self.state['crashes'] >= CRASH_STOP:
            self.stop(f'{self.state["crashes"]} batches in a row crashed: look at the last batch.log, then merge_queue.py resume')
        self.save('stopped' if self.stopped() else 'idle')


def resume(directory):
    """Clear a stop, the crash count and a red tip's hold: the next turn takes everything again."""
    d = pathlib.Path(directory)
    (d/'STOP').unlink(missing_ok=True)
    state = d/'state.json'
    if state.exists():
        s = json.loads(state.read_text())
        s.update(tip_red=[], crashes=0)
        q2ci.write_json(state, s)
    print(q2ci.event('QUEUE_RESUMED', directory=d))


def show(directory, out=print):
    d = pathlib.Path(directory)
    try:
        s = json.loads((d/'status.json').read_text())
    except (OSError, ValueError):
        out('the queue has never run here (no status.json)')
        return 1
    alive = pathlib.Path(f'/proc/{s["pid"]}').exists()
    out(f'queue: {s["state"]}{"" if alive else " (its process " + str(s["pid"]) + " is gone: not running)"}; '
        f'trunk {s["trunk"]} at {s["tip"][:8]}; updated {s["updated"]}')
    if s.get('why'):
        out(f'  why: {s["why"]}')
    if s.get('batch') and s['state'] == 'batch':
        out(f'  batch since {s["batch"]["since"]}: {", ".join(s["batch"]["branches"])} ({s["batch"]["log"]})')
    if s['tip_red']:
        out(f'  TIP RED on {", ".join(s["tip_red"])}: only --fixes-tip branches are taken')
    out(f'  ready: {", ".join(s["ready"]) or "nothing"}')
    if s['last_landed']:
        out(f'  last landed {s["last_landed"]["when"]}: {", ".join(s["last_landed"]["branches"])}')
    post = s['post']
    out(f'  post tier: {"never run" if post["ok"] is None else "ok" if post["ok"] else "RED"}')
    events = d/'events.log'
    if events.exists():
        out('  last events:')
        for line in events.read_text().splitlines()[-8:]:
            out(f'    {line[:200]}')
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('command', choices=('run', 'status', 'resume'))
    ap.add_argument('--worktree', type=pathlib.Path, default=pathlib.Path(__file__).resolve().parents[2],
                    help='the trunk\'s clean worktree (default: the one this file is in)')
    ap.add_argument('--trunk', default=q2ci.trunk(), help=f'default: {q2ci.trunk()} ($Q2_TRUNK, ~/.config/q2/trunk)')
    ap.add_argument('--push', action='store_true', help='push the trunk to origin after each landing')
    ap.add_argument('--no-post', dest='post', action='store_false', help='never run the post tier')
    ap.add_argument('--once', action='store_true', help='one turn, then exit (its name is printed)')
    ap.add_argument('--poll', type=float, default=10, help='seconds between looks at the ready list')
    ap.add_argument('--dir', type=pathlib.Path, default=q2ci.queue_dir())
    ap.add_argument('--ready', type=pathlib.Path, default=merge_batch.READY)
    args = ap.parse_args()
    if args.command == 'status':
        return show(args.dir)
    if args.command == 'resume':
        resume(args.dir)
        return 0
    args.dir.mkdir(parents=True, exist_ok=True)
    lock = open(args.dir/'queue.lock', 'w')
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        raise SystemExit(f'a queue is running already (lock {lock.name})')
    q = Queue(args.worktree, args.trunk, args.dir, args.ready, args.push, args.post, log=lambda m: print(m, flush=True))
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))  # wait()'s finally ends the batch
    q.event('QUEUE_STARTED', args.trunk, worktree=args.worktree, pid=os.getpid())
    if args.once:
        print(q.turn())
        return 0
    while True:
        did = q.turn()
        if did in ('idle', 'stopped', 'error'):
            time.sleep(args.poll)


if __name__ == '__main__':
    sys.exit(main())
