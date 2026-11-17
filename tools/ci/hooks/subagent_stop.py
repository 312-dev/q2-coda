#!/usr/bin/env python3
"""Claude Code SubagentStop hook: a registered worker may not stop while what it started still runs.

It reads the hook's JSON on stdin and asks `q2 agent check` about the directory in its `cwd`: when that is
the worktree of a worker registered as running (q2 agent start) and the worktree still has a q2-heavy
job or a process, it exits 2 with the list on stderr, which Claude Code hands back to the agent in place
of letting it stop. In every other case, and when anything about the input is not as expected, it lets
the stop through: a hook that blocks wrongly would keep an agent alive, the fault this exists to end.
The firm check is `q2 agent end`, which a worker's contract makes its last command. docs/orchestration.md#rules.
"""
import json
import pathlib
import subprocess
import sys


def main():
    try:
        event = json.load(sys.stdin)
    except ValueError:
        return 0
    if event.get('stop_hook_active'):  # already sent back once for this stop: do not hold it in a loop
        return 0
    cwd = event.get('cwd')
    if not cwd:
        return 0
    q2 = pathlib.Path(__file__).resolve().parents[1]/'q2'
    p = subprocess.run([sys.executable, str(q2), 'agent', 'check', '--worktree', cwd], capture_output=True, text=True)
    if p.returncode == 1 and p.stdout.strip():
        print('q2: this worker still has things running. Stop each (TaskStop for a background task, kill PID for a '
              'process), then run `q2 agent end NAME --state ...` and finish:\n' + p.stdout.strip(), file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main())
