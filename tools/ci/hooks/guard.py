#!/usr/bin/env python3
"""Claude Code PreToolUse hook for Bash: refuse the commands that have cost this project hours.

It reads the hook's JSON on stdin ({"tool_name": "Bash", "tool_input": {"command": ...}}) and exits 2 with
the reason on stderr to refuse, which Claude Code shows the agent in place of running the command; any
other tool, and any command no rule names, passes (exit 0). A rule here is a wall, where a line in a brief
is a request: each one below was in a brief on 2026-11-16 and was broken anyway. docs/orchestration.md#rules.

    python3 tools/ci/hooks/guard.py --check 'until ! pgrep -f foo; do sleep 5; done'   # try a command by hand
"""
import json
import re
import sys

BOUND = re.compile(r'\btimeout\s+(-\S+\s+)*\d|\bq2-wait\b|-(gt|ge|lt|le)\b|\(\(|\bSECONDS\b')
TRUNK = re.compile(r'^\+?([^:\s]*:)?(refs/heads/)?(release/[\w./-]+|main|master)$')


def unquoted(command):
    """command without its quoted strings and here-documents: what the shell itself would run, near enough
    to tell `git stash` from a commit message that mentions it."""
    text = re.sub(r"<<-?\s*(['\"]?)(\w+)\1.*?^\s*\2\s*$", ' ', command, flags=re.S | re.M)
    text = re.sub(r"'[^']*'", "''", text)
    return re.sub(r'"(\\.|[^"\\])*"', '""', text)


def refusal(command):
    """Why command may not run, or None."""
    bare = unquoted(command)
    # loops are matched in the whole text: most of them are written inside bash -c '...'
    if re.search(r'\b(until|while)\b[^\n]{0,400}?\bpgrep\b', command):
        return ('a loop that waits on pgrep never ends when the pattern is in its own command line (26 such shells '
                'sat for up to 4 hours on 2026-11-16). Wait on the process id or on a log line, with a time limit: '
                'q2-wait --pid PID --timeout 600, or q2-wait --log FILE --match REGEX --timeout 600')
    if re.search(r'\b(until|while)\b.{0,600}?\bsleep\b.{0,400}?\bdone\b', command, re.S) and not BOUND.search(command):
        return ('this polling loop has no time limit, so a condition that never comes true leaves it running for '
                'ever. Use q2-wait --pid PID|--log FILE --match REGEX --timeout SECONDS, or put the loop under '
                'timeout N, or count its turns')
    if re.search(r'\bpkill\b|\bkillall\b', bare):
        return ('pkill and killall match by name and have ended the wrong processes here (once the shell that ran '
                'them). Stop a process by its id (kill PID), a heavy job with q2 stop-all or its scope')
    if re.search(r'\bgit\b[^|;&\n]*\bstash\b(?!\s+(list|show)\b)', bare):
        return ('git stash is shared by every worktree and a failed pop lost 5,500 lines. Commit to a WIP branch, or '
                'use a worktree')
    if re.search(r'\bpytest\b[^|;&\n]*\s-n\s*auto\b', bare):
        return 'pytest -n auto starts a worker a core and ran the machine out of memory. Use tools/ci/fw.py, or -n 6 at most'
    if re.search(r'\bQ2_(QUEUE|TRUNK_ADMIN)=', bare):
        return ('Q2_QUEUE and Q2_TRUNK_ADMIN are what let a process move the trunk: only the merge queue and the user '
                'set them. Queue the branch: python3 tools/ci/merge_batch.py --mark-ready')
    m = re.search(r'\bgit\b[^|;&\n]*?\bpush\b([^|;&\n]*)', bare)
    if m and any(TRUNK.match(word) for word in m[1].split()):
        return ('the trunk is pushed by the merge queue after a batch passes, never by hand. Push your own branch and '
                'queue it: python3 tools/ci/merge_batch.py --mark-ready')
    if re.search(r'\bpatch_shards\.py\b', bare) and '--lines' not in bare and '--help' not in bare and not re.search(
            r'\b(grep|rg|cat|sed|head|tail|wc|ls|git|less|vim?|nano|python3? -m (pytest|mypy))\b[^|;&\n]*patch_shards\.py', bare):
        return ('a full run of patch_shards.py takes twice as long as the serial suite and seven times its memory '
                '(measured 2026-11-16). Run the suite itself: q2-heavy python3 test/patch.py "$B"')
    if re.search(r'\bemu_pool\.py\s+(put|reserve|renew|get\s+--slot)\s+2\b', bare) or re.search(
            r'\b(rm|mv|kill)\b[^|;&\n]*q2-emu-(server|pool2)\b', bare):
        return 'emulator slot 2 and ~/q2-emu-server are the user\'s own rig: leave them as they are'
    return None


def main():
    if len(sys.argv) > 2 and sys.argv[1] == '--check':
        why = refusal(sys.argv[2])
        print(f'refused: {why}' if why else 'allowed')
        return 2 if why else 0
    try:
        event = json.load(sys.stdin)
    except ValueError:
        return 0  # not this hook's input: say nothing rather than block every command
    if event.get('tool_name') != 'Bash':
        return 0
    why = refusal(str((event.get('tool_input') or {}).get('command', '')))
    if why:
        print(f'q2 guard: {why}. (docs/orchestration.md#rules)', file=sys.stderr)
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main())
