# Orchestration

How work gets from "the user wants X" to "X is in a release image" when agents do it, many at once, on one 16-core, 16 GB laptop. This page is the one place that says who does what: the main thread, the workers, the merge queue, and what stops them. The tools are described in [building: Fast test runs](building.md#fast-test-runs); this page is how they are used together.

## The shape

```text
user <-> main thread ---- spawns, briefs, stops ----> workers (3 or 4, one item each, one worktree each)
              ^                                           |
              | events.log (a Monitor)                    | merge_batch.py --mark-ready
              |                                           v
         merge queue  <----------------------------  ready list
         (a service, no agent: merge, build, test once, land or bounce)
              |
              v
            trunk  --->  q2 release steps, on demand
```

- **The main thread** talks to the user, keeps the backlog, spawns and stops workers, and reacts to the queue's events. It does not merge, does not run suites for other people's branches, and does not wait on anything.
- **A worker** is one agent doing one backlog item in one worktree. It ends when the item is queued, blocked or handed off. It never waits for its merge.
- **The merge queue** (`tools/ci/merge_queue.py`, a systemd user service) is a script. It lands everything that is ready, a batch at a time, and reports. No agent decides when to merge.
- **There is no coordinator agent.** Workers are spawned by the main thread directly, so every agent is in the main session's own list, where the main thread (`TaskStop`) and the user (`/tasks`, then `x`; `Ctrl+X Ctrl+K` for all) can stop it. A worker does not spawn agents.

The numbers this is sized by, all measured on this machine on 2026-11-16: a build is 36 to 56 s; the MIPS suite is 4:39 serially on one core; the whole merge tier is about five and a half minutes of wall time and under 4 GB; the post tier (sanitizers, coverage, the smoke test and the leak hunt, which run beside each other) about another five. Seven CPU-bound jobs at once slow each other by 1.3 to 1.7 times, and long-lived branches on the same hot files (`docs/internals.md`, `tools/build.py`, `patch/contexts.inc`) cost 8 to 14 conflicted files each to merge: so three or four workers, not seven.

## The flow of one item

1. The user asks. The main thread writes one backlog line (what, where, done when) and, if a worker slot is free, spawns a worker with the [brief](#the-brief).
2. The worker registers (`q2 agent start`), works in its worktree, commits and pushes its branch at each milestone.
3. When the item is done, the worker merges the trunk into its branch, builds, runs `affected.py --run "$B"` in the foreground, and queues the branch: `merge_batch.py --mark-ready`. It signs off (`q2 agent end NAME --state READY`) and ends with `READY <branch> <sha>`.
4. The queue takes the branch in its next batch, at most one batch away. `LANDED`, `REJECTED` or `CONFLICT` appears in the event log.
5. The main thread sees the event. `LANDED`: the backlog line is closed; the user is told if he asked. `REJECTED` or `CONFLICT`: the main thread resumes that worker (`SendMessage`) or spawns a fresh one with the event's suite, first failure and log path as the whole brief.

From `--mark-ready` to `LANDED` is one batch, about seven minutes, or two when a batch was already running.

## The worker's contract

A worker **must**:

- Start with `q2 agent start NAME --item 'one line'` in its worktree. If that is refused, another worker is registered there: stop and report, do not work beside it.
- Keep `STATUS.md` in its worktree current (the item, what is done, what is left, anything a successor needs), and commit and push its branch at every milestone and at least every 30 minutes.
- Use the shared tools: `rebuild.py` for builds, `affected.py --run "$B"` for tests, `q2-heavy` for anything else heavy, `emu_pool.py` for an emulator, `q2-wait` for any wait.
- A `test/X.py` the branch changed is run by `affected.py --run` (and the gate) with the argv its own parser takes, from the `SCRIPTS` table in `tools/ci/gate.py`: the build, qemu or the stock rootfs's files (`{stockfs}`, extracted from the build's `stock.squashfs`) where it needs them, nothing where it takes nothing. `test/coda_rc_e2e.py` leases a pooled emulator itself (`--pool`), with Car Remote on for the lease. A script that cannot run there (`test/server_catalogue.py` is a generator) is skipped with a printed `skipped ...: reason`, never silently. A new `test/X.py` needs a row, which `tools/ci/test_ci.py` enforces.
- A change to the firmware's side of the bundled Subsonic app (`patch/addon.c`, `catalogue.c`, `stream_state.c`, `playback.c`, `player/`, the app's pin in `tools/build.py`) plans `test/subsonic_seam.py`, the real app run against a fake server and the firmware's own C ([building: Seam test](building.md#seam-test)); the app's developer runs it with `--app DIR` before cutting a zip.
- Expect a coverage check for every `tools/` module the branch changes: `affected.py` plans `cov:tools/X.py` (`tools/ci/covcheck.py`), which runs the `test/unit` files that import or name `X.py` under coverage and holds `X.py` alone to the post tier's `fail_under` (`pyproject.toml`), in seconds rather than the five and a half minutes of `unit-cov`. A new module needs a `test/unit/test_X.py` that covers it, branches included. The check also runs in the merge batch, so a branch that lowers a module's coverage is `REJECTED` with the file and its missed lines in the log, instead of landing and turning the post tier `POST_RED` on `unit-cov` (the keyboard check in `tools/build.py`, `tools/q2flash.py` and `tools/iroh.py` did, on 2026-11-18 and 19). A failure is yours to fix: add the tests, never lower the floor.
- Run tests in the foreground with a timeout. The longest, `test/player/run.py`, `test/unit` and the serial MIPS suite, take about five minutes each; nothing a worker runs needs a background task.
- Before queuing: merge the trunk into the branch (`git merge "$(q2 trunk)"`), resolve what conflicts, rebuild, run `affected.py --run "$B"` again, push.
- Queue with `python3 tools/ci/merge_batch.py --mark-ready` (`--fixes-tip` only for a branch whose purpose is to repair a failure the trunk has).
- End with `q2 agent end NAME --state READY|BLOCKED|HANDOFF` as its last command, then one final message that starts with that word. `q2 agent end` is refused while a heavy job or a process of the worktree is still running, and for `READY` while there are uncommitted changes or unpushed commits: stop or finish them, then end.

A worker **may**: read anything in the repositories; build and test in its own worktree; borrow one emulator; run the smoke test its plan names for a `patch/` change (`affected.py --run "$B" --smoke`, about two minutes on a pooled emulator; optional, as the queue runs it on the tip after landing and a failure there holds the trunk); ask the user a question by ending `BLOCKED` with the question in its final message and in `QUESTIONS.md` in its worktree.

A worker **must not**: commit, merge or push to the trunk or to another worker's branch; wait for its branch to land; spawn agents; start a background task that outlives its turn; run `tools/ci/patch_shards.py` on the whole suite; touch emulator slot 2, `~/q2-emu-server` or port 2309; use `pkill`, `killall`, `git stash` or `pytest -n auto`; fix a failure it has not reproduced.

The three endings:

| Final message          | Means                                                     | The main thread then                                                                        |
| ---------------------- | --------------------------------------------------------- | ------------------------------------------------------------------------------------------- |
| `READY <branch> <sha>` | queued; the worker is finished with it                    | closes the item on `LANDED`; on `REJECTED` or `CONFLICT` resumes or respawns with the event |
| `BLOCKED <question>`   | needs a decision only the user can make                   | asks the user, one question at a time, then resumes the worker with the answer              |
| `HANDOFF`              | out of turns or context; `STATUS.md` says where it stands | spawns a successor in the same worktree with `q2 agent start ... --takeover`                |

### Worker hygiene

Each rule was broken on 2026-11-17 or 18 with other workers' runs lost or killed. A brief names this section; it does not retype it.

1. **Scratch files go in your own subfolder** of the session scratchpad, named for your `NAME`, and never in another worker's: a shared folder is overwritten without a word.
2. **One wait monitor at a time.** Cancel it when the run it waits on reports, and leave none after `q2 agent end`: a forgotten monitor keeps watching, and fires into a later turn.
3. **Run commands only from your own worktree.** A shell whose cwd drifted into another worker's worktree ran other agents' commands there and had their `q2-wait` processes killed. Check `pwd` after any `cd`; use absolute paths.
4. **Kill only your own processes, by PID.** Never match a `q2-wait` or any process in a worktree to its owner by guesswork; never `pkill`, never `pkill -f`. If you cannot say whose a process is, leave it and report it.
5. **Suites run through `affected.py --run` or `q2-heavy`.** Never a bare `pytest` over `test/fw`, never `-n auto`: they ignore the CPU and memory budget the queue is sized by.
6. **Write `STATUS.md` as you go**, even where it is gitignored: a `HANDOFF` is resumed from it, and a file written at the end is not there when the context runs out first.
7. **Emulators come from the pool only** (`emu_pool.py get` and `put`): never slot 2, `~/q2-emu-server` or port 2309. A lease resets the card from the fixture (`infra/pool-card-drift`), the library (`database.db`) from the one scanned from it, and `state/data`'s settings (`config.ini`) from a data fixture while its per-run files (queue, play counts, ratings, Memory Playback) are removed, so nothing you left on the card, in the library or in the settings survives it; return the lease when done.
8. **Report the static bytes you add.** The scratch state is 128 KB and `.bss` counts in it; the build prints `payload room:`. Put the figure before and after in the commit message or `STATUS.md`, so the queue's `scratch_free_bytes` has no surprise.
9. **Know the flaky tests.** The `0x5e000000` mmap clash between `test_nav_loop.py` and `test_e2e_network.py` was fixed on 2026-11-18 by moving the `Wait` helper's page to `0x5d000000`; a clash of that kind is no longer a flake, so reproduce it before calling it one. The post tier's `smoke` failing at boot with an unnamed `sortselect_dialog` (`card None`) was a race in the test, fixed on 2026-11-18 ([building: smoke test](building.md#smoke-test)). Still open: the `fw` suite's occasional first-run failure, recorded in `~/.cache/q2-queue/flakes.jsonl`, cause not yet pinned; `test/known_failures.tsv` is empty. A failure on your branch that passes alone is not yours to fix, but name it in your final message.

## Integration branches

When the user has an integrator assemble an `integ/*` branch (an image ahead of the trunk), its work is tested once, at each end, never at every step:

- **A feature branch** for it runs `python3 tools/ci/affected.py --base <its base> --run "$B"` before it ends `READY`, where the base is the integration branch it will be merged into. It is not queued (`--mark-ready`): the integrator merges it.
- **The integrator** merges the `READY` branches with only a build (`rebuild.py`, which compiles everything) after each merge, to name the merge that broke it. A targeted run after a merge passes `--base` or `--plan-json` to `affected.py`: on an `integ/*` branch a bare run plans everything. On the final merge, before the image, it runs `affected.py --all --run "$B"` and `tools/ci/smoke.py "$B"` once. `--all` holds every suite in `gate.PLANNED`, the car's two among them: `test/iap_probe.py` (about nine minutes) and `test/coda_rc_e2e.py` (a pooled emulator of its own); `test/coda_rc_iroh_e2e.py` needs n0's public relay and is run by hand. `--run` starts the suites side by side as memory allows (`gate.run`), so the whole run takes about as long as its longest suite, `test/iap_probe.py` (about nine minutes; `test/unit`, `test/patch.py` and `test/player/run.py` about five each, 2026-11-22), when `q2-heavy` has slots free, not the sum of them ([building: Fast test runs](building.md#fast-test-runs)).
- **A failure** in that run is classified by the run itself: `affected.py --run` runs a failed suite once more, alone. One that then passes on the same tree and build (`gate.tree_state`: HEAD, the work tree's changes, untracked files and the build's manifest, taken at each try's start) is `flaky` in its table, a flake: report it, with the suite and its log (the first try's is `<suite>.first.log`). One that passes only after the tree or the build changed, as when a fix was saved while the run went on, is `FAILED, then passed after a change`: it counts as failed, as a pass on other code says nothing about the first failure, and its first try's log is printed. Either way the table's last line names every suite whose first try failed and the tests that log names. `test/player/run.py` runs its tests side by side and a test that fails there again alone; one that then passes is counted in its last line (`N failed side by side and passed alone: names`), and the table names those too (`failed side by side and passed alone: test/player/run.py (N: names)`), though the suite passed: report them as flakes. One that fails again is `FAILED`, real, and goes back to the branch whose change it is.

## The brief

A brief has four parts and nothing else; the standing rules are in this page and in the hooks, not retyped each time.

```text
You are a worker on Q2 Muse. Read docs/orchestration.md (The worker's contract and Worker hygiene) before
anything else and follow it exactly; AGENTS.md is the index to the code.

ITEM: <one backlog item, one or two sentences: what the user should see when it is done>
WORKTREE: <absolute path>   BRANCH: <name, off the trunk's tip>   NAME: <short name for q2 agent start>
READ: <the files, doc anchors, mock-ups (absolute paths) and decisions that bear on it; quote the user's own words>
DONE WHEN: <the observable result, the tests that must exist, the docs that must change>

End as the contract says: READY, BLOCKED or HANDOFF.
```

Size an item so that one agent finishes it in one context: one screen, one bug, one subsystem change. An item that needs "and then" is two items. A brief for a rejected branch is the event line (suite, first failure, log path) and "make it green, queue it again".

Workers get an explicit `model:` (never the main thread's by inheritance), and a turn limit, so a worker that is going nowhere returns `HANDOFF` instead of degrading.

## How the queue reports

One line per outcome in `~/.cache/q2-queue/events.log`; the main thread keeps one Monitor on it (`tail -n 0 -F ~/.cache/q2-queue/events.log`) and nothing else polls.

| Event                                         | Means                                                                                                         | Who acts                                                                                                                                 |
| --------------------------------------------- | ------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------- |
| `READY branch`                                | a worker queued it                                                                                            | nobody                                                                                                                                   |
| `BATCH n branches`                            | a batch started                                                                                               | nobody                                                                                                                                   |
| `LANDED branch`                               | it is in the trunk                                                                                            | main thread closes the item                                                                                                              |
| `REJECTED branch suite=... first=... log=...` | the batch failed because of this branch; the others landed                                                    | main thread resumes or respawns the owner with this line                                                                                 |
| `CONFLICT branch`                             | it no longer merges with the trunk                                                                            | the same: merge the trunk in, test, queue again                                                                                          |
| `REQUEUED branch`                             | not its fault (a crash, a red tip, an undecided failure); it runs again                                       | nobody                                                                                                                                   |
| `TIP_RED suites`                              | the trunk fails on its own (a merge suite, or the post tier's `smoke`); only `--fixes-tip` branches are taken | main thread spawns one worker to fix it, first                                                                                           |
| `INFRA suites`                                | suites crashed twice; no branch is blamed                                                                     | nobody, once; see `QUEUE_STOPPED`                                                                                                        |
| `QUEUE_STOPPED why`                           | two batches in a row crashed, or `q2 stop-all`                                                                | main thread reads the last `batch.log`, tells the user, `merge_queue.py resume` when fixed                                               |
| `RESUMED`                                     | the running queue saw `merge_queue.py resume` (a stop, a red tip's hold, the crash count cleared)             | nobody                                                                                                                                   |
| `POST_OK` / `POST_RED suites`                 | the sanitizers, coverage, the emulator smoke test and the leak hunt on the tip                                | `POST_RED`: main thread adds a backlog item for it (a red `unit-cov` is a `tools/` module under the floor that a branch's `cov:` check should have caught: name that branch; a red `smoke` is also `TIP_RED`; a `leak-hunt` names the metric and loop in its log) |
| `PUSHED` / `PUSH_FAILED`                      | the trunk on origin                                                                                           | `PUSH_FAILED`: main thread tells the user                                                                                                |

What blocks a branch: a conflict with the trunk, or a failure the bisection pins on it. What does not: another branch's failure, a harness crash, a test on `test/known_failures.tsv`, the post tier, a flaky test that passes on its second try.

## Status

`q2 status` is the whole picture in about twenty lines: the machine (load, memory, the Windows drive), the queue (state, trunk tip, the running batch and since when, the ready list, the last landing, the post tier, the last events), the workers (name, branch, since when, the last commit and its age, the item), the heavy jobs (class, age against deadline, worktree, command), and any shell a session started more than 15 minutes ago, which should not exist. The user asks for it in a word; the main thread runs it instead of asking agents how they are.

A worker whose last commit is older than 30 minutes is stalled or lost: the main thread messages it once, and if nothing comes back, stops it and hands the item on.

## Rules

Enforced, not requested. Each of these was in a brief on 2026-11-16 and was broken anyway.

| Rule                                                                                                                                       | Enforced by                                                                                                                                                |
| ------------------------------------------------------------------------------------------------------------------------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------- |
| The trunk moves only to a commit the gate passed                                                                                           | `tools/ci/hooks/reference-transaction` in the shared repository: any other update of `release/*` or `main` fails                                           |
| A branch that conflicts is its author's to fix                                                                                             | `merge_batch.py --mark-ready` refuses it, naming the files                                                                                                 |
| No wait without a limit; no wait on a process name                                                                                         | `q2-wait` requires `--timeout`; `tools/ci/hooks/guard.py` (a Claude Code `PreToolUse` hook) refuses a loop around `pgrep` and a polling loop with no bound |
| No `pkill`, `killall`, `git stash`, `pytest -n auto`, no push to the trunk, no full `patch_shards.py` run, nothing done to emulator slot 2 | `guard.py`                                                                                                                                                 |
| One worker to a worktree                                                                                                                   | `q2 agent start`                                                                                                                                           |
| A worker ends with nothing left running                                                                                                    | `q2 agent end`; `tools/ci/hooks/subagent_stop.py` (a `SubagentStop` hook) holds a registered worker that still has a job or a process in its worktree      |
| Nothing heavy runs for ever                                                                                                                | `q2-heavy`: every job is a systemd scope stopped at its deadline (30 minutes)                                                                              |
| Workers do not starve the queue                                                                                                            | `q2-heavy`: six branch-class jobs at most (four on battery or short of memory), none started under CPU pressure, at a fifth of the queue's CPU weight                                                                      |

`python3 tools/ci/hooks/guard.py --check 'COMMAND'` says whether a command would be refused and why.

## Stopping and pausing

There is no "pause" that leaves things running. To stop everything:

1. The main thread stops every agent it started (`TaskStop` each; the user can press `Ctrl+X Ctrl+K`). Agents are not processes: only the session can stop them.
2. `q2 stop-all --yes`. It holds the queue (the `STOP` file: the running batch ends within seconds and its branches go back on the ready list), stops every `q2-heavy` job by its scope, then ends every remaining q2 process by id, and prints what survived. `q2 stop-all` alone lists what it would stop.
3. `q2 status` shows nothing running. Emulator leases are left to expire or are returned with `emu_pool.py put N`; slot 2 is never touched.

To resume: `python3 tools/ci/merge_queue.py resume` (a running service picks it up within one turn and logs `RESUMED`; it needs no stop and start, and clears a `TIP_RED` hold as well), then spawn workers from the backlog; each reads its worktree's `STATUS.md`. Nothing is lost by a stop: the ready list, the registry and every branch are on disk, and branches are on origin.

An interruption by the user is not a stop: a worker the interruption ended is spawned again from its `STATUS.md`.

## When a worker runs out of context

Planned for, not recovered from: items are small, `STATUS.md` and the pushed branch are always current, and a worker that reaches its turn limit ends `HANDOFF`. The successor is a fresh agent with the same brief plus "continue from STATUS.md and git log", started with `--takeover` after the old one is confirmed stopped. Never spawn into a worktree without checking `q2 agent list`: two workers in one worktree overwrite each other.

## A release

On the trunk's tip, when the user asks:

1. `q2 status`: the queue idle, the post tier ok for the tip.
2. `python3 tools/ci/gate.py` in the trunk's worktree: every suite that already passed on this tree is `cached`, so this is seconds when the post tier has run, and it is the proof for the release notes.
3. The release build and its checks as [releasing](releasing.md) says; the image and its SHA-256 to `/mnt/c/Users/Public/`.

A new release line is the same trunk continuing. When a trunk is renamed (`release/p31` to `release/p32`), the user creates the branch (`Q2_TRUNK_ADMIN=1 git branch release/p32 release/p31`), writes its name to `~/.config/q2/trunk`, and restarts the queue in a worktree of it.

## Install

Everything here lives outside the repository, so it is done once per machine, by hand. Run from the trunk's worktree (`W`); installs are by rename, never over a file in use.

```sh
W=~/repos/q2-pod-wt/release          # the trunk's worktree
PY=~/repos/q2-pod/.venv/bin/python   # has pytest, pytest-xdist, coverage, mypy, unicorn

# 1. the commands
install -m 755 "$W/tools/ci/q2-heavy" ~/.local/bin/q2-heavy.new && mv ~/.local/bin/q2-heavy.new ~/.local/bin/q2-heavy
for t in q2 q2-wait; do
    printf '#!/bin/sh\nexec %s %s/tools/ci/%s "$@"\n' "$PY" "$W" "$t" > ~/.local/bin/$t.new
    chmod 755 ~/.local/bin/$t.new && mv ~/.local/bin/$t.new ~/.local/bin/$t
done

# 2. the trunk's name
mkdir -p ~/.config/q2 && echo release/p31 > ~/.config/q2/trunk

# 3. the trunk's guard. A global core.hooksPath hides a repository's own hooks, so the repository gets a
#    hooks directory of its own that also holds the global hooks.
H=~/repos/q2-pod/.git/q2-hooks
mkdir -p "$H"
for g in ~/.config/git/hooks/*; do [ -e "$g" ] && ln -sf "$g" "$H/"; done
install -m 755 "$W/tools/ci/hooks/reference-transaction" "$H/reference-transaction"
git -C ~/repos/q2-pod config core.hooksPath "$H"

# 4. the queue, as a user service
mkdir -p ~/.config/systemd/user
cat > ~/.config/systemd/user/q2-queue.service <<EOF
[Unit]
Description=Q2 Muse merge queue

[Service]
ExecStart=$PY $W/tools/ci/merge_queue.py run --worktree $W --push
Restart=on-failure
RestartSec=10
KillMode=control-group
TimeoutStopSec=40

[Install]
WantedBy=default.target
EOF
systemctl --user daemon-reload && systemctl --user enable --now q2-queue.service
```

Check each, and see it refuse before trusting it:

```sh
q2 status                                                    # the queue is idle on the trunk
git -C "$W" commit --allow-empty -m probe                    # refused: "moves only through the merge queue"
python3 "$W/tools/ci/hooks/guard.py" --check 'until ! pgrep -f x; do sleep 1; done'   # refused
q2-wait --pid $$ --timeout 2; echo $?                        # 124
```

The Claude Code hooks are project settings: `.claude/settings.json` runs `tools/ci/hooks/guard.py` before every Bash command and `tools/ci/hooks/subagent_stop.py` when a subagent stops. They are part of the repository once the user has approved that file; the session must be restarted (or `/hooks` reviewed) to load them.

The trunk's worktree must hold nothing git does not know, or the gate records and reuses no passes there: add stray files to `.git/info/exclude` or move them out.

To take the queue down: `systemctl --user stop q2-queue.service`. To remove the guard: `git -C ~/repos/q2-pod config --unset core.hooksPath`.
