#!/usr/bin/env python3
"""The gate: one build, then every suite at once, as memory allows, with a table at the end.

    python3 tools/ci/gate.py                          # a release's gate: the merge, planned and post suites, everything
    python3 tools/ci/gate.py --tier merge             # what a batch must pass before it lands
    python3 tools/ci/gate.py --tier post              # what runs on the trunk after a landing
    python3 tools/ci/gate.py --only fw-asan,unit      # some of it
    python3 tools/ci/gate.py --plan-json plan.json    # what an affected.py plan names (merge_batch.py's runs)
    python3 tools/ci/gate.py -- --dev                 # rebuild.py's options (default: --dev)

Run it with a python that has pytest, pytest-xdist, coverage, mypy, unicorn and fontTools (the main
checkout's venv, ~/repos/q2-pod/.venv, has them all). The suites, by tier:

    merge   patch              test/patch.py BUILD: the suite in one process, as it is written
            unit               test/unit
            fw                 tools/ci/fw.py: test/fw on xdist workers
            test/playback.py   test/playback.py BUILD
            test/build.py      test/build.py --build BUILD
            test/peq.py        test/peq.py
            test/headset_io.py test/headset_io.py BUILD
            mypy-tools         mypy tools test/unit
            mypy-fw            mypy test/fw
    planned test/coverflow.py, test/player/run.py, tools/ci/test_ci.py, tools/ci/test_orch.py, test/stock_sites.py,
            test/subsonic_contract.py, test/subsonic_seam.py, test/iap_probe.py, test/coda_rc_e2e.py (PLANNED): what
                               affected.py's rules plan for a change no merge suite covers; run when a plan
                               names them (an everything plan names all), and in a release's gate
    cov:tools/X.py             tools/ci/covcheck.py: one tools/ module's coverage by the test/unit files that name it,
                               against unit-cov's floor; a plan names one per changed module (COV), never a tier
    post    fw-asan            tools/ci/fw.py --asan: the sanitizers, a group of files to a process
            unit-cov           test/unit under coverage, then `coverage report` (the floor in pyproject.toml)
            smoke              tools/ci/smoke.py BUILD: the build on a pooled emulator, walked as a user would
                               (merge_queue.py makes the tip TIP_RED when it fails; exit 3, no emulator, is not)
            leak-hunt          tools/ci/leak_hunt.py BUILD: the same UI tour looped on a pooled emulator, failing on
                               growth in windows, widgets, timers or memory (POST_RED only: never the tip's hold)

The build (tools/ci/rebuild.py: this worktree's reused build) starts at once, beside the suites that need
none; the rest start when it is done. The longest suites start first. A suite starts only when
MemAvailable, less what suites started in the last RAMP seconds will still take, stays at least FLOOR GB
after its own estimate (MEM_GB); else it waits for one to finish. When nothing is running it starts anyway
and q2-heavy's own checks hold it. Heavy suites run under q2-heavy (fw.py wraps its own processes).
test/patch.py runs serially: its tests share one script's state, and a serial run (4:39 on one core,
1.2 GB) beat eight shards (10:26, 5.2 GB) on an idle machine on 2026-11-16. A plan that names lines of it
runs those through patch_shards.py --lines. Each suite writes its own log; the table gives each one's
exit status and time. Exit status: 0 when all passed, else 1.

A suite that passed on this exact tree is not run again (`cached` in the table): its pass is recorded in
~/.cache/q2-verified/ under the tree's hash, the suite's command and the build's options, and only when
the worktree has no change git does not hold. So a release's gate after a batch on the same commit costs
only what the batch did not run. --no-cache runs everything.

A suite that fails is run once more, alone (--no-retry: not). One that then passes on the same tree and build
(tree_state: HEAD, the work tree's changes, untracked files, the build's manifest) is `flaky` (the run counts as
passed) and is appended to ~/.cache/q2-queue/flakes.jsonl; one that passes only after either changed is a failure,
`failed, then passed after a change`, with the first try's log. The table ends with every suite whose first try
failed and the tests that log names, so a flake is never silent. A result's
`kind` says what a failure was: `crash` when the process died of a signal or its log shows one (a Unicorn
segfault, a memory limit's kill, a deadline's), else `fail`. merge_batch.py blames no branch for a crash.

test/known_failures.tsv lists pytest tests that fail on the trunk and whose fix is owed: the gate
deselects them, so they block nothing until the entry's date. A line past its date, or one the file's
rules refuse, fails the gate as the suite `known-failures`. test/patch.py has no such list: a later test
needs the state an earlier one left, so a failure there is fixed, not skipped.

A plan (affected.py --json) that runs unit, fw and patch whole is the merge tier and the plan's own
`other` suites beside it (a PLANNED suite its changes need, a test/X.py the branch changed), but not the
optional smoke; any other plan runs
what it names, each `other` path a suite of its own (a test/X.py as SCRIPTS says, or skipped with its reason). --only keeps just those names: a plan's suite as the plan
has it, one only a whole tier has (GATE_ONLY) whole, and any other name the plan lacks not at all; the
name `build` builds even when no suite needs it. docs/building.md#gate.
"""
import argparse
import contextlib
import datetime
import hashlib
import json
import os
import pathlib
import re
import shlex
import shutil
import signal
import subprocess
import sys
import time
from typing import Any

ROOT = pathlib.Path(__file__).resolve().parents[2]
HEAVY = pathlib.Path(os.environ.get('Q2_HEAVY', pathlib.Path.home()/'.local/bin/q2-heavy'))
FLOOR = 4.0  # GB of MemAvailable a new suite must leave
RAMP = 30    # s: a suite this young may not have reached its peak yet, so its estimate still counts
# A suite's peak memory (GB) and how long it takes alone (s), from the gate's runs on 2026-11-16 (unit's and
# test/player/run.py's from 2026-11-22; PLANNED's
# from their own runs on 2026-11-18, each under 0.25 GB; test/iap_probe.py's 9:02 and 77 MB and test/coda_rc_e2e.py's
# 17 s on a pooled emulator from 2026-11-22, its 1.0 GB the emulator's, as smoke's): the order they start in and
# what each needs free.
MEM_GB = {'build': 2.0, 'unit': 1.0, 'unit-cov': 1.5, 'mypy-tools': 1.0, 'mypy-fw': 1.0, 'fw': 3.0, 'fw-asan': 5.0,
          'patch': 1.5, 'test/build.py': 1.0, 'test/peq.py': 1.0, 'test/playback.py': 1.0, 'test/headset_io.py': 0.5,
          'smoke': 1.0, 'leak-hunt': 1.0, 'test/coverflow.py': 0.5, 'test/player/run.py': 0.5, 'tools/ci/test_ci.py': 0.5,
          'tools/ci/test_orch.py': 0.5, 'test/stock_sites.py': 0.5, 'test/subsonic_contract.py': 0.25,
          'test/subsonic_seam.py': 1.0, 'test/iap_probe.py': 0.25, 'test/coda_rc_e2e.py': 1.0}
SECONDS = {'unit-cov': 330, 'patch': 280, 'fw-asan': 150, 'unit': 280, 'smoke': 95, 'leak-hunt': 200, 'test/playback.py': 80, 'fw': 50,
           'mypy-tools': 40, 'mypy-fw': 30, 'test/build.py': 20, 'test/peq.py': 20, 'test/headset_io.py': 5,
           'test/coverflow.py': 20, 'test/player/run.py': 270, 'tools/ci/test_ci.py': 5, 'tools/ci/test_orch.py': 15, 'test/stock_sites.py': 12,
           'test/subsonic_contract.py': 2, 'test/subsonic_seam.py': 150, 'test/iap_probe.py': 540, 'test/coda_rc_e2e.py': 20}
# The suites a rule of affected.py plans for a change no merge-tier suite covers (patch/coverflow*.c, player/,
# tools/ci/, a stock address in patch/ or tools/). Not in the merge tier, as test/player/run.py alone is longer than all of it: a batch's plan names
# them when its diff needs them, and the gate runs a plan's `other` suites beside the tier. A plan that runs
# everything (affected.Plan.everything) and a release's gate run them all. tools/ci/test_ci.py checks that
# what an everything plan runs holds each rule's suites.
# A plan's `other` entry `cov:tools/X.py` (affected.py plans one for each tools/ module a branch changes) is
# tools/ci/covcheck.py on that module: the post tier's unit-cov floor, over the tests that name it, seconds
# not minutes. In a merge batch's run, so a branch that drops coverage is rejected, not landed and POST_RED.
COV = 'cov:'
PLANNED = ('test/coverflow.py', 'test/player/run.py', 'tools/ci/test_ci.py', 'tools/ci/test_orch.py', 'test/stock_sites.py',
           'test/subsonic_contract.py', 'test/subsonic_seam.py', 'test/iap_probe.py', 'test/coda_rc_e2e.py')
POST = ('fw-asan', 'unit-cov', 'smoke', 'leak-hunt')
MERGE = tuple(n for n in SECONDS if n not in POST + PLANNED)
FULL = MERGE + PLANNED + POST
TIERS = {'merge': MERGE, 'post': POST, 'release': FULL}
# in a whole tier, and in a narrower plan only for the sources that need them (the mypy suites for a .py, test/peq.py
# for the sources it builds, the payload suites for a payload change): --only runs these whole when the plan lacks them
GATE_ONLY = ('fw-asan', 'unit-cov', 'smoke', 'leak-hunt', 'mypy-tools', 'mypy-fw', 'test/peq.py', 'test/playback.py',
             'test/headset_io.py')
# post-tier suites an affected.py plan names for a worker to run if it likes (affected.py --run --smoke): a merge
# batch's plan leaves them out, as the queue runs them on the tip after it lands
OPTIONAL = ('smoke',)
VERIFIED = pathlib.Path(os.environ.get('Q2_VERIFIED', pathlib.Path.home()/'.cache/q2-verified'))
FLAKES = pathlib.Path(os.environ.get('Q2_FLAKES', pathlib.Path.home()/'.cache/q2-queue/flakes.jsonl'))
KNOWN = ROOT/'test/known_failures.tsv'
# which suites a known failure's first column reaches: both runs of the same tests
KNOWN_SUITES = {'unit': ('unit', 'unit-cov'), 'fw': ('fw', 'fw-asan')}
# Exit statuses that are a death, not a verdict: 128 + SIGABRT, SIGBUS, SIGFPE, SIGKILL (a memory limit),
# SIGSEGV, SIGTERM (a deadline), and timeout(1)'s 124. Python reports its own child's signal as negative.
CRASH_RC = (124, 134, 135, 136, 137, 139, 143)
CRASH_TEXT = ('Segmentation fault', 'Fatal Python error', 'CRASHED (exit', 'oom-kill')


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
        self.retried: Any = None  # the kind of its first failure, once it is being run again
        self.states: list = []  # tree_state at each try's start
        self.first_tests: list = []  # the tests its first failed try named

    @staticmethod
    def fill(arg, build):
        if '{stockfs}' in arg:
            arg = arg.replace('{stockfs}', stockfs(build))
        return arg.replace('{qemu_static}', static_qemu() or '').replace('{qemu}', shutil.which('qemu-mipsel') or '').replace('{build}', build)

    def command(self, build):
        argv = [self.fill(a, build) for a in self.argv]
        return ([str(HEAVY)] if self.heavy and HEAVY.exists() else []) + argv


def everything(plan):
    return plan is None or all(plan.get(k) == 'all' for k in ('unit', 'fw', 'patch'))


def full_suite(name, py, logs):
    cov = {'COVERAGE_FILE': str(logs/'.coverage')}
    return {
        # --durations: the log's slowest tests, the evidence for where the suite's time goes
        'unit': lambda: Suite('unit', [py, '-m', 'pytest', 'test/unit', '-q', '-p', 'no:cacheprovider', '--durations=30'],
                              heavy=True),
        'unit-cov': lambda: Suite('unit-cov', [py, '-m', 'coverage', 'run', '-m', 'pytest', 'test/unit', '-q', '-p',
                                               'no:cacheprovider'], heavy=True, env=cov,
                                  after=[py, '-m', 'coverage', 'report']),
        'test/playback.py': lambda: Suite('test/playback.py', [py, 'test/playback.py', '{build}'], needs_build=True,
                                          heavy=True),
        'test/headset_io.py': lambda: Suite('test/headset_io.py', [py, 'test/headset_io.py', '{build}'],
                                            needs_build=True, heavy=True),
        # each its own cache directory: gate.run starts the two together, and on one directory their sqlite cache
        # is sometimes locked ("database is locked", an INTERNAL ERROR: 2 of 18 runs side by side, none of 8 apart)
        'mypy-tools': lambda: Suite('mypy-tools', [py, '-m', 'mypy', '--cache-dir', '.mypy_cache/tools', 'tools', 'test/unit']),
        'mypy-fw': lambda: Suite('mypy-fw', [py, '-m', 'mypy', '--cache-dir', '.mypy_cache/fw', 'test/fw']),
        'fw': lambda: Suite('fw', [py, str(ROOT/'tools/ci/fw.py')]),
        'fw-asan': lambda: Suite('fw-asan', [py, str(ROOT/'tools/ci/fw.py'), '--asan']),
        'patch': lambda: Suite('patch', [py, 'test/patch.py', '{build}'], needs_build=True, heavy=True),
        'test/build.py': lambda: Suite('test/build.py', [py, 'test/build.py', '--build', '{build}'], needs_build=True,
                                       heavy=True),
        'test/peq.py': lambda: Suite('test/peq.py', [py, 'test/peq.py'], heavy=True),
        'smoke': lambda: Suite('smoke', [py, str(ROOT/'tools/ci/smoke.py'), '{build}'], needs_build=True, heavy=True),
        'leak-hunt': lambda: Suite('leak-hunt', [py, str(ROOT/'tools/ci/leak_hunt.py'), '{build}'], needs_build=True,
                                   heavy=True),
    }.get(name)


def static_qemu():
    """A statically linked qemu-mipsel, which runs inside a chroot that holds no host libraries (test/thirdparty.py's
    bwrap root): $Q2_QEMU_STATIC, qemu-mipsel-static on PATH, else the one the pool's copies carry (slot 2 is the
    user's rig and is never read from)."""
    found = os.environ.get('Q2_QEMU_STATIC') or shutil.which('qemu-mipsel-static')
    for slot in (1, 3, 4):
        if not found and (pathlib.Path.home()/f'q2-emu-pool{slot}/qemu-mipsel-static').is_file():
            found = str(pathlib.Path.home()/f'q2-emu-pool{slot}/qemu-mipsel-static')
    return found


# How the gate (and affected.py --run) runs each test/X.py a plan can name, as each script's own parser
# takes it: the argv after the script, in which `{build}` is the build, `{qemu}` qemu-mipsel, `{qemu_static}` a static one (`static_qemu`) and `{stockfs}` the
# stock rootfs's module_driver/ and usr/data/ (extracted from the build's stock.squashfs, once per stock). A string
# instead of a list is the reason the script cannot run here: it is skipped, with that reason printed.
# tools/ci/test_ci.py checks that each script is in the table and that no row is for a script that is gone.
SCRIPTS: dict[str, list[str] | str] = {
    # VampOS's services on this machine (docs/vamp-os-base.md), each pytest under its own main
    'test/api.py': [],  # Vamp API 1's transcripts, the conformance tests of the suites below
    'test/apps.py': [],
    'test/btd.py': [],
    'test/carrd.py': [],
    'test/dacloop.py': [],
    'test/dlnad.py': [],
    'test/gate.py': [],
    'test/launch.py': [],  # the real q2pkgd and runsv
    'test/libd.py': [],
    'test/netd.py': [],
    'test/perm.py': [],
    'test/playd.py': [],
    'test/q2vampos.py': [],
    'test/scrobd.py': [],
    'test/sendd.py': [],
    'test/sysd.py': [],
    'test/conformance.py': 'the transcript reader test/api.py and the suites import, with nothing to run',
    # VampOS's image suites need a tools/vampos.py build (vampos.squashfs), not the firmware build {build} names
    'test/os_image.py': 'needs a VampOS image (tools/vampos.py build --out DIR) and the stock ZIP: the VampOS sweep runs it',
    'test/os_bt.py': 'needs a VampOS image (DIR/vampos.squashfs): the VampOS sweep runs it',
    'test/card_e2e.py': 'needs a VampOS image and the stock ZIP: the VampOS sweep runs it',
    'test/pkgd.py': 'needs a VampOS image and the stock ZIP: the VampOS sweep runs it',
    'test/adb.py': ['{build}'],
    'test/aic.py': ['{stockfs}'],
    'test/aic_build.py': ['{stockfs}'],  # no builds: it checks the guard, the module's ABI and its packaging
    'test/alsa.py': ['{build}', '{qemu}'],
    'test/bluealsa.py': ['{build}'],
    'test/bluealsa_own.py': "needs the own bluealsa built first (tools/thirdparty.py build bluealsa, minutes) and its path; a spike's "
                             'check, run by hand (docs/bluealsa.md#what-the-emulator-shows)',
    'test/bluetoothd.py': ['{build}'],
    'test/bluez_emu.py': "test/bluez_port.py's Unicorn machine for bluetoothd's hooks, a library and no suite",
    'test/bluez_own.py': ['{build}'],  # the own bluetoothd beside stock's under qemu-mipsel; it builds bluez when the cache has none
    'test/bluez_port.py': [],  # Shanling's hooks in the own bluetoothd under Unicorn, from tools/thirdparty.py's build
    'test/bt_launch.py': ['{build}'],  # the Bluetooth launcher in the build's own rootfs; skips a build without it
    'test/build.py': ['--build', '{build}'],
    'test/chapters.py': [],  # --stock-root and --qemu go together and are optional: its parser refuses BUILD
    'test/coda_rc_e2e.py': ['--pool', '{build}'],  # a pooled emulator, Car Remote on for the lease, on its own port
    'test/coda_rc_iroh_e2e.py': "needs a host build of q2-iroh and n0's public relay (the network); run it by hand",
    'test/coverflow.py': [],
    'test/coverflow_position.py': [],
    'test/fmrx.py': [],  # the tuner's receiver against the prototype's figures; --mips OUT builds the Q2's bench
    'test/headset_io.py': ['{build}'],
    'test/iap_probe.py': [],  # route 1's probe on the host against a synthetic car (docs/iphone-mode.md)
    'test/ldac.py': ['{build}'],
    'test/library_scope.py': [],
    'test/localsend_e2e.py': ['{build}'],  # takes a build and finds the CLI itself; without one it prints skip and passes
    'test/m4b.py': ['{build}'],
    'test/mcu_park.py': ['{stockfs}/usr/data/libmcu-bare.bin'],
    'test/mcu_sleep.py': ['{stockfs}/usr/data/libmcu-bare.bin'],
    'test/mcu_sleep_host.py': 'still running after 200 s when probed on 2026-11-18 and never timed to the end: its invocation (STOCKFS) is right, its cost is unknown',
    'test/module_mips.py': 'a library the module tests import (test/rotator.py and others), with nothing to run',
    'test/module_regions.py': 'a library the module tests import, with nothing to run',
    'test/patch.py': ['{build}'],
    'test/peq.py': [],
    'test/q2dbus_bus.py': [],  # q2guard's D-Bus watcher against a dbus-daemon of its own; without one, skip
    'test/playback.py': ['{build}'],
    'test/pwm.py': ['{stockfs}'],
    'test/pwm_build.py': ['{stockfs}'],
    'test/pwm_mcu.py': ['{stockfs}'],
    'test/q2_guest.py': [],
    'test/q2bip.py': [],  # q2-bip on the host: a controller over a UNIX packet socket, a real ffmpeg, the L2CAP socket
    'test/q2eq.py': [],
    'test/q2update.py': [],
    'test/q2vampos.py': [],  # the card boot's hand-off; its FUSE tests skip without /dev/fuse, qemu-mipsel and the binary
    'test/radiofetch.py': [],  # recorded answers from a local server; --live (the real services) is by hand
    'test/release.py': [],
    'test/remote.py': [],
    'test/rotator.py': ['{stockfs}/module_driver/soc_rotator.ko'],
    'test/rotator_build.py': [],  # given builds it asserts those were built with the module options, which a --dev build was not
    'test/server_catalogue.py': 'a generator of a catalogue file for a card (CARD_ROOT), not a test',
    'test/stock_sites.py': [],  # finds the stock zip itself (Q2_STOCK_ZIP)
    'test/subsonic_seam.py': [],  # builds the pinned app and q2-fakesonic from ~/repos/q2-subsonic; skipped without
    'test/subsonic_contract.py': [],  # the pinned zip (~/.cache/q2-apps) and ~/repos/q2-subsonic; skipped without
    'test/suite_source.py': "test/patch.py's loader (and a line-number lookup by hand), not a test",
    'test/tuner.py': [],  # Radio's receiver on a dropout, its restarts and the dial's click (tuner_core.c with fmrx.c), host cc
    'test/uac1_bind.py': 'needs a usb_f_uac1.ko, which only tools/uac1_module/uac1_build.py makes (a cross gcc and a kernel tree); run it by hand',
    'test/uac1_isoc.py': 'needs a usb_f_uac1.ko, which only tools/uac1_module/uac1_build.py makes (a cross gcc and a kernel tree); run it by hand',
    'test/ui.py': [],  # q2ui built for this machine, a file for the panel; without the build's inputs it fails, naming them
    'test/thirdparty.py': ['{build}', '{qemu_static}', '--offline'],  # its TLS checks need the network, which a gate does not
}
STOCKFS_CACHE = pathlib.Path(os.environ.get('Q2_STOCKFS', pathlib.Path.home()/'.cache/q2-stockfs'))
STOCKFS_PATHS = ('module_driver', 'usr/data')


def stockfs(build):
    """The stock rootfs's files the module tests read, extracted from build's stock.squashfs: one directory per
    stock image (by its hash), so every build of the same stock shares it."""
    image = pathlib.Path(build)/'stock.squashfs'
    out = STOCKFS_CACHE/hashlib.sha256(image.read_bytes()).hexdigest()[:16]
    if not (out/'.done').exists():
        shutil.rmtree(out, ignore_errors=True)
        out.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(['unsquashfs', '-q', '-no-xattrs', '-d', str(out), str(image), *STOCKFS_PATHS], check=True)
        (out/'.done').write_text('')
    return str(out)


def other_suite(path, py):
    """A plan's `other` path, run as tools/ci/affected.py runs it; None for a script that cannot run here
    (SCRIPTS says why)."""
    if path.startswith('tools/ci/test_'):
        return Suite(path, [py, '-m', 'pytest', '-q', '-p', 'no:cacheprovider', path])
    if path.startswith(COV):  # `cov:tools/X.py`: that module's coverage by the test/unit files that name it
        return Suite(path, [py, str(ROOT/'tools/ci/covcheck.py'), path[len(COV):], '--python', py], heavy=True)
    args = SCRIPTS.get(path, ['{build}'])
    if isinstance(args, str):
        return None
    if '{qemu}' in ' '.join(args) and not shutil.which('qemu-mipsel'):
        return None
    if '{qemu_static}' in ' '.join(args) and not static_qemu():
        return None
    return Suite(path, [py, path, *args], needs_build=any('{build}' in a or '{stockfs}' in a for a in args), heavy=True)


def declared():
    """Every script SCRIPTS says can run here, but test/patch.py, which is the patch suite: what an everything plan
    and a release run besides their tier."""
    return {n for n, v in SCRIPTS.items() if isinstance(v, list) and n != 'test/patch.py'}


def skip_reason(path):
    """Why other_suite(path) is None."""
    args = SCRIPTS.get(path)
    if isinstance(args, str):
        return args
    return 'it needs a static qemu-mipsel, which this machine lacks' if '{qemu_static}' in ' '.join(args or []) else 'it needs qemu-mipsel, which this machine lacks'


def named_suite(name, py, logs):
    """A suite by its name: one gate.py defines, else a plan's `other` path; None (and a printed reason) for a
    script that cannot run here."""
    full = full_suite(name, py, logs)
    suite = full() if full else other_suite(name, py)
    if suite is None:
        print(f'skipped {name}: {skip_reason(name)}', flush=True)
    return suite


def suites_for(plan, only, py, logs, tier=None):
    """The suites to run: a whole tier for an everything plan (a release's with no plan, the merge tier
    with one) and the plan's own `other` suites with it, else the plan's, cut to only."""
    out = {}
    if everything(plan):
        out = {n: named_suite(n, py, logs) for n in TIERS[tier or ('release' if plan is None else 'merge')]}
        if plan is None and not tier:  # a release: every declared script too, as an everything plan names them
            out |= {n: named_suite(n, py, logs) for n in sorted(declared()) if n not in out}
        for o in plan['other'] if plan else ():  # a test/X.py the branch changed runs itself, whatever else
            if o not in out and o not in OPTIONAL:
                out[o] = named_suite(o, py, logs)
    else:
        if plan['unit']:
            files = ['test/unit'] if plan['unit'] == 'all' else [f'test/unit/{f}' for f in plan['unit']]
            out['unit'] = Suite('unit', [py, '-m', 'pytest', '-q', '-p', 'no:cacheprovider', '--durations=30', *files],
                                heavy=True)
        if plan['fw']:
            files = [] if plan['fw'] == 'all' else [f'test/fw/{f}' for f in plan['fw']]
            out['fw'] = Suite('fw', [py, str(ROOT/'tools/ci/fw.py'), *files])
        if plan['patch']:
            if plan['patch'] == 'all':
                out['patch'] = full_suite('patch', py, logs)()
            else:  # some tests of it: patch_shards.py replays what those lines need
                out['patch'] = Suite('patch', [py, str(ROOT/'tools/ci/patch_shards.py'), '{build}', '--lines',
                                               ','.join(plan['patch'])], needs_build=True)
        for o in plan['other']:
            if o not in OPTIONAL:
                out[o] = named_suite(o, py, logs)
    out = {n: s for n, s in out.items() if s}
    if only:
        # a plan's suite is cut to only; a suite no plan names (GATE_ONLY) runs whole; any other name the plan
        # lacks is left out, as nothing the plan's changes touch reaches it
        out = {n: out[n] if n in out else named_suite(n, py, logs) for n in only if n in out or n in GATE_ONLY}
    return out


def known_failures(path, today):
    """test/known_failures.tsv as ({suite: [pytest ids to deselect]}, [what is wrong with the file]). A line is
    `suite<TAB>test id<TAB>expires (YYYY-MM-DD)<TAB>owner<TAB>reason`; `#` starts a comment."""
    skip: dict = {}
    wrong = []
    if not path.exists():
        return skip, wrong
    for n, line in enumerate(path.read_text().splitlines(), 1):
        if not line.strip() or line.lstrip().startswith('#'):
            continue
        f = [c.strip() for c in line.split('\t')]
        if len(f) != 5 or not all(f):
            wrong.append(f'line {n}: needs five tab-separated fields (suite, test id, expires, owner, reason)')
            continue
        suite, test, expires = f[:3]
        if suite not in KNOWN_SUITES:
            wrong.append(f'line {n}: suite {suite!r} cannot skip a test (only {", ".join(KNOWN_SUITES)}); '
                         'a test/patch.py failure is fixed, not listed')
            continue
        if '::' not in test:
            wrong.append(f'line {n}: {test!r} is not a pytest id (test/unit/test_x.py::test_name)')
            continue
        try:
            last = datetime.date.fromisoformat(expires)
        except ValueError:
            wrong.append(f'line {n}: {expires!r} is not a date (YYYY-MM-DD)')
            continue
        if last < today:
            wrong.append(f'line {n}: {test} expired on {expires} (owner {f[3]}): fix it or renew the line')
            continue
        for reached in KNOWN_SUITES[suite]:
            skip.setdefault(reached, []).append(test)
    return skip, wrong


def classify(rc, text):
    """What a failed suite's end was: 'crash' (the process died, or says one of its own did) or 'fail'."""
    if isinstance(rc, int) and (rc < 0 or rc in CRASH_RC):
        return 'crash'
    return 'crash' if any(t in text for t in CRASH_TEXT) else 'fail'


def tree_key(root=ROOT):
    """The tree's hash when the worktree holds nothing else (no changed tracked file, no file git does not
    know outside its ignores), else None: only then does a pass say something about a commit."""
    def git(*a):
        return subprocess.run(['git', '-C', str(root), *a], capture_output=True, text=True)
    status = git('status', '--porcelain')
    tree = git('rev-parse', 'HEAD^{tree}')
    if status.returncode or tree.returncode or status.stdout.strip():
        return None
    return tree.stdout.strip()


def tree_state(build=None, root=ROOT):
    """What a suite's try ran against, dirty or not: HEAD, the work tree's changes from it, the files git does not
    know outside its ignores, and the build's manifest.json. Two tries of one suite with different states ran
    different code, so a pass on the second says nothing about the first's failure."""
    def git(*a):
        return subprocess.run(['git', '-C', str(root), *a], capture_output=True).stdout
    h = hashlib.sha256(git('rev-parse', 'HEAD') + git('diff', 'HEAD', '--binary'))
    for name in sorted(git('ls-files', '--others', '--exclude-standard', '-z').split(b'\0')):
        if name and (root/name.decode()).is_file():
            h.update(name + b'\0' + (root/name.decode()).read_bytes())
    manifest = pathlib.Path(build)/'manifest.json' if build else None
    if manifest and manifest.is_file():
        h.update(manifest.read_bytes())
    return h.hexdigest()


FAILED_TEST = re.compile(r'^(?:FAILED (\S+::\S+)|FAIL (?:protocol )?(\S+)[ :(])', re.M)


# test/player/run.py's last line: what failed side by side and then passed alone (counted, never hidden)
SIDE = re.compile(r'^all: .*; (\d+) failed side by side and passed alone(?:: (.*))?$', re.M)


def side_failures(text):
    """The tests a passing log says failed side by side and passed alone (run.py's summary), else []."""
    m = SIDE.findall(text)
    if not m or not int(m[-1][0]):
        return []
    return [t.strip() for t in m[-1][1].split(',') if t.strip()] or [f'{m[-1][0]} unnamed']


def failed_tests(text):
    """The tests a failed try's log names: pytest's FAILED lines, and test/player/run.py's and the host suites'
    FAIL lines."""
    return list(dict.fromkeys(a or b for a, b in FAILED_TEST.findall(text)))


def cache_file(tree, suite, build_args):
    """Where suite's pass on tree is recorded: by the tree, the suite's command and the build's options."""
    def plain(arg):  # the same command from another worktree or venv is the same command
        return 'python' if pathlib.PurePath(arg).name.startswith('python') else arg.replace(str(ROOT), '.')
    what = json.dumps([tree, suite.name, [plain(a) for a in suite.argv], sorted(suite.env),
                       [plain(a) for a in suite.after or []], build_args])
    return VERIFIED/(hashlib.sha256(what.encode()).hexdigest()[:40] + '.json')


def avail_gb():
    with open('/proc/meminfo') as f:
        for line in f:
            if line.startswith('MemAvailable:'):
                return int(line.split()[1]) / 1048576
    return 0.0


def run(suites, build_args, logs, py, build=None, log=print, mem=avail_gb, clock=time.monotonic, force_build=False,
        poll=2.0, retry=False, tree=None, flakes=None):
    """Build (unless build is given, or nothing needs it and not force_build) and run the suites, as memory
    allows; returns {name: {'rc', 'seconds', 'log'}} with 'build' among them when it ran. A failed result
    has 'kind' (classify). retry: a suite that fails runs once more, with nothing beside it, and a pass then
    is the result, marked 'flaky' with the first failure's kind and appended to the file flakes, when both tries
    ran on the same tree_state; after a change it fails with rc 'changed' and the first try's log. A retried
    result names the tests the first try's log failed ('first_tests'). tree (the
    hash tree_key gives): a suite recorded as passed on it is not run ('cached'), and each pass is recorded."""
    logs.mkdir(parents=True, exist_ok=True)
    done: dict = {}
    if tree:
        for s in list(suites.values()):
            hit = cache_file(tree, s, build_args)
            if hit.exists():
                done[s.name] = {**json.loads(hit.read_text()), 'cached': True, 'seconds': 0}
                log(f'ok   {s.name} (cached: passed on this tree, {done[s.name].get("when", "earlier")})')
        suites = {n: s for n, s in suites.items() if n not in done}
    pending: list[Suite] = sorted(suites.values(), key=lambda s: -SECONDS.get(s.name, 60))
    running: list[Suite] = []
    builder: Suite | None = None
    if build is None and (force_build or any(s.needs_build for s in pending)):
        builder = Suite('build', [py, str(ROOT/'tools/ci/rebuild.py'), *build_args], heavy=True)
        pending.insert(0, builder)

    def launch(s):
        s.log = logs/f'{s.name.replace("/", "_")}.log'
        s.states.append(tree_state(build))  # what this try runs against, before it starts
        s.start = clock()
        cmd = s.command(build or '')
        log_file = open(s.log, 'w')
        s.proc = subprocess.Popen(cmd, cwd=ROOT, env={**os.environ, **s.env},
                                  stdout=subprocess.PIPE if s is builder else log_file,  # the build's path
                                  stderr=log_file if s is builder else subprocess.STDOUT, text=True,
                                  start_new_session=True)  # its own group, which the finally below ends whole
        log(f'start {s.name}: {shlex.join(cmd)}')
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
                result = {'rc': s.rc, 'seconds': round(s.seconds), 'log': str(s.log)}
                if s.rc != 0:
                    result['kind'] = classify(s.rc, pathlib.Path(s.log).read_text(errors='replace')[-20000:])
                    if retry and s is not builder and s.retried is None:
                        s.retried = result['kind']
                        first = s.log.with_suffix('.first.log')
                        s.first_tests = failed_tests(s.log.read_text(errors='replace'))
                        s.log.replace(first)
                        log(f'FAIL {s.name} ({s.seconds:.0f} s, {s.retried}): running it once more, alone; '
                            f'the first log is {first}')
                        pending.insert(0, s)
                        continue
                elif s.retried and len(set(s.states)) > 1:
                    # passed only after the tree or the build changed: the first failure stands
                    result.update(rc='changed', kind=s.retried, changed=True, log=str(s.log.with_suffix('.first.log')))
                elif s.retried:
                    result['flaky'] = s.retried
                    if flakes:
                        flakes.parent.mkdir(parents=True, exist_ok=True)
                        with open(flakes, 'a') as f:
                            f.write(json.dumps({'when': time.strftime('%Y-%m-%dT%H:%M:%S'), 'suite': s.name,
                                                'first': s.retried, 'tree': tree, 'worktree': ROOT.name,
                                                'log': str(s.log.with_suffix('.first.log'))}) + '\n')
                if s.retried:
                    result['first_tests'] = s.first_tests
                if s is not builder and (side := side_failures(pathlib.Path(s.log).read_text(errors='replace')[-20000:])):
                    result['side'] = side
                if s.rc == 0 and tree and s is not builder and not s.retried:
                    # a pass after a failure is not recorded: the next run on this tree tests it again
                    hit = cache_file(tree, s, build_args)
                    hit.parent.mkdir(parents=True, exist_ok=True)
                    tmp = hit.with_suffix(f'.{os.getpid()}.tmp')
                    tmp.write_text(json.dumps({**result, 'when': time.strftime('%Y-%m-%d %H:%M'), 'tree': tree}))
                    tmp.replace(hit)
                done[s.name] = result
                note = ', failed, then passed after a change' if result.get('changed') else \
                    f', flaky: first {s.retried}' if s.rc == 0 and s.retried else \
                    f', {result["kind"]}' if s.rc != 0 else ''
                log(f'{"ok  " if result["rc"] == 0 else "FAIL"} {s.name} ({s.seconds:.0f} s{note})')
            if builder and builder.rc not in (None, 0):
                for s in [p for p in pending if p.needs_build]:
                    pending.remove(s)
                    done[s.name] = {'rc': 'no build', 'seconds': 0, 'log': str(builder.log)}
            now = clock()
            young = sum(s.mem for s in running if now - s.start < RAMP)
            alone = any(s.retried for s in running)  # a second try has the machine to itself
            for s in list(pending):
                if alone or (s.needs_build and not build):
                    continue
                if s.retried:
                    if not running:
                        pending.remove(s)
                        launch(s)
                    break  # and nothing starts ahead of it
                free = mem() - young
                if free - s.mem >= FLOOR or not running:
                    pending.remove(s)
                    launch(s)
                    young += s.mem
            if pending or running:
                time.sleep(poll)
    finally:
        for s in running:  # stopped early: end each suite with what it started (q2-heavy ends its job on TERM)
            with contextlib.suppress(ProcessLookupError):
                os.killpg(s.proc.pid, signal.SIGTERM)
    return done


def table(results, wall):
    rows = [f'{"suite":18} {"rc":>8} {"time":>7}  log']
    for name, r in results.items():
        note = ' (cached)' if r.get('cached') else f' (flaky: first {r["flaky"]})' if r.get('flaky') else \
            ' (failed, then passed after a change)' if r.get('changed') else \
            f' ({r["kind"]})' if r.get('kind') else ''
        rows.append(f'{name:18} {str(r["rc"]):>8} {r["seconds"] // 60:4d}:{r["seconds"] % 60:02d}  {r["log"]}{note}')
    bad = [n for n, r in results.items() if r['rc'] != 0]
    rows.append(f'{"wall":18} {"":>8} {int(wall) // 60:4d}:{int(wall) % 60:02d}  '
                + (f'FAILED: {", ".join(bad)}' if bad else 'all passed'))
    return '\n'.join(rows + first_tries(results))


def first_tries(results):
    """A line naming every suite whose first try failed, with the tests its log named, flaky or not, and one naming
    the tests a suite failed side by side and passed alone (test/player/run.py's count): none is silent."""
    firsts = [f'{n} ({", ".join(r["first_tests"]) or "no test named"})' for n, r in results.items() if 'first_tests' in r]
    sides = [f'{n} ({len(r["side"])}: {", ".join(r["side"])})' for n, r in results.items() if r.get('side')]
    return ([f'first tries failed: {"; ".join(firsts)}'] if firsts else []) + \
        ([f'failed side by side and passed alone: {"; ".join(sides)}'] if sides else [])


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--plan-json', metavar='FILE', help='an affected.py plan (- for stdin): run what it names')
    ap.add_argument('--only', help='comma-separated suite names: just these')
    ap.add_argument('--tier', choices=sorted(TIERS), help='which suites an everything plan means (default: release '
                    'with no plan, merge with one)')
    ap.add_argument('--no-cache', action='store_true', help='run a suite even when it passed on this tree before')
    ap.add_argument('--no-retry', action='store_true', help='do not run a failed suite a second time')
    ap.add_argument('--build', help='use this build, do not build')
    ap.add_argument('--logs', type=pathlib.Path, help='default: ~/.cache/q2-gate/<worktree>-<time>/')
    ap.add_argument('--results', type=pathlib.Path, help='also write the results as JSON here')
    ap.add_argument('--python', default=sys.executable)
    args, rest = ap.parse_known_args()
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))  # so run() ends its suites
    build_args = [a for a in rest] or ['--dev']
    if build_args and build_args[0] == '--':
        build_args = build_args[1:]
    plan = None
    if args.plan_json:
        plan = json.loads(sys.stdin.read() if args.plan_json == '-' else pathlib.Path(args.plan_json).read_text())
    logs = args.logs or pathlib.Path.home()/'.cache/q2-gate'/f'{ROOT.name}-{time.strftime("%Y%m%d-%H%M%S")}'
    only = [n for n in (args.only or '').split(',') if n]
    suites = suites_for(plan, [n for n in only if n != 'build'], args.python, logs, args.tier)
    skip, wrong = known_failures(KNOWN, datetime.date.today())
    for name, tests in skip.items():
        if name in suites:
            suites[name].argv += [f'--deselect={t}' for t in tests]
    t = time.monotonic()
    head = subprocess.run(['git', '-C', str(ROOT), 'rev-parse', '--short', 'HEAD'], capture_output=True, text=True).stdout.strip()
    print(f'gate on {ROOT.name} {head}: {", ".join(suites) or "nothing"}; logs in {logs}', flush=True)
    results = run(suites, build_args, logs, args.python, args.build, log=lambda m: print(m, flush=True),
                  force_build='build' in only, retry=not args.no_retry, tree=None if args.no_cache else tree_key(),
                  flakes=FLAKES)
    if wrong:
        logs.mkdir(parents=True, exist_ok=True)
        (logs/'known-failures.log').write_text('\n'.join(f'{KNOWN.name}: {w}' for w in wrong) + '\n')
        results['known-failures'] = {'rc': 1, 'seconds': 0, 'log': str(logs/'known-failures.log'), 'kind': 'fail'}
        print('\n'.join(f'{KNOWN.name}: {w}' for w in wrong), flush=True)
    out = table(results, time.monotonic() - t)
    print('\n' + out)
    (logs/'summary.txt').write_text(out + '\n')
    if args.results:
        args.results.write_text(json.dumps(results))
    return 1 if any(r['rc'] != 0 for r in results.values()) else 0


if __name__ == '__main__':
    sys.exit(main())
