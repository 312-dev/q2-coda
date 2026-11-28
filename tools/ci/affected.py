#!/usr/bin/env python3
"""Which tests a feature branch's changes need, from the files it changed against a base.

    python3 tools/ci/affected.py                      # changes against the trunk's merge base, as a plan
    python3 tools/ci/affected.py --base main --json   # the same plan as JSON
    python3 tools/ci/affected.py --run BUILD          # and run it, the suites at once (test/patch.py in one process)
    python3 tools/ci/affected.py --all --run BUILD    # everything: what an integration branch runs

A feature branch runs what its files map to; an integration branch (release/*, integ/*, main) runs everything unless
--base or --plan-json names what to run (an integrator's run after one merge: its diff, or a computed plan), as
does any branch whose changes include a file no rule knows (`RULES`: an unknown file means all). The
mapping is conservative: a change to the payload (patch/, a build input in tools/, assets/) runs all of
test/patch.py, which executes the payload as one program. What gets narrowed:

- an edit to a test/patch_suite/ file runs only the tests (passed() to passed()) holding a changed statement,
  plus every test that names a changed def or class, through patch_shards.py --lines (the joined suite's
  lines); an edit to the runner (test/patch.py, test/suite_source.py) runs all of it;
- patch/X.c runs the test/fw files that name X.c, its X__ statics, or a function X.c defines, and the
  end-to-end files; a source no fw file names runs all of test/fw; and the test/unit files that name X.c
  (they read or compile it);
- a file in a folder a test/unit file checks whole (`SCANNERS`: test_theme.py's and test_art.py's rules
  over patch/, test_docs.py's citations in patch/, tools/ and test/) runs that test too;
- tools/X.py runs test/unit/test_X.py when it exists (else all of test/unit), and, for a build input,
  test/patch.py and test/fw; for a module whose only output is a pinned file in the image (`IMAGE_INPUTS`:
  iroh.py, uac1.py), test/build.py;
- a file in a tools/ folder a rule knows (`TOOL_DIRS`) runs the tests that read that folder: tools/uac1_module/
  the module's pin, build and tests, tools/iap_probe/ the probe's; a file in any other tools/ folder runs
  everything, and tools/ci/ runs the CI tools' own tests;
- a remote/ file a rule knows (`REMOTE`) runs the tests that read it: remote/iroh-sidecar/ the sidecar's pin and
  the build, remote/coda-rc.proto the protocol's tables; any other remote/ file runs everything;
- a test file runs itself;
- a .py under tools/ or test/unit/ also runs gate.py's mypy-tools suite, one under test/fw/ mypy-fw, and
  pyproject.toml (the mypy config) both: the merge queue runs them, so a plan without them passes here and is
  rejected there. They are run as gate.py defines them (`gate.full_suite`);
- a tools/ module (not tools/ci/), or its test/unit/test_X.py, also plans `cov:tools/X.py` (`cov_target`): tools/ci/covcheck.py
  runs the test/unit files that name the module under coverage and holds it to the post tier's `fail_under`,
  as a merge batch does, so a branch that drops a module under the floor is rejected there, not POST_RED;
- patch/addon.c, catalogue.c, stream_state.c, player/ and tools/build.py (the app's pin) also plan
  test/subsonic_contract.py (`CONTRACT_SOURCES`): the words and files the bundled Subsonic app shares with them;
  those, patch/playback.c and the seam test's own files (`SEAM_SOURCES`) plan test/subsonic_seam.py, the real app
  run against them;
- a source a suite outside test/fw and test/patch.py builds, runs or reads plans that suite (`SUITE_SOURCES`, patterns
  so a split's new files are covered; for a suite that compiles them, every header and .inc they include too):
  patch/coda_rc*, remote/coda-rc.proto, tools/coda_rc_client.py and tools/ci/emu_pool.py test/coda_rc_e2e.py;
  patch/q2_iap*, patch/iap_*, patch/iap2* and patch/q2car.sh test/iap_probe.py; patch/peq*, hash.c and the other sources it builds
  test/peq.py; the Cover Flow sources and theirs test/coverflow.py; patch/peq.c test/player/run.py;
- a payload change (patch/, a build input in tools/, assets/) also plans test/playback.py and test/headset_io.py
  (`PAYLOAD_SUITES`), which run the build's payload as test/patch.py does;
- a payload change (patch/) also plans `smoke`, tools/ci/smoke.py on a pooled emulator, but runs it only with
  --smoke: it is optional for a worker, and a merge batch leaves it out (gate.OPTIONAL), as the queue runs it
  on the trunk's tip after landing, where a failure turns the tip red.

--run hands the plan's suites to gate.run, as gate.py --plan-json would (gate.suites_for): they start at once as
memory allows, the longest first, heavy ones under q2-heavy, test/patch.py whole as one serial process; each
writes its own log under ~/.cache/q2-gate/, a failure's end is printed, and a table gives each suite's result
and seconds. A suite that fails runs once more alone, and a pass then is `flaky`, counted as passed and logged
to the flakes file (--no-retry: not); one that passed on this exact tree is `cached`, not run (--no-cache: run).
Without BUILD only the suites that need none run. Exit status: 1 when any suite failed.

Documented in docs/building.md#fast-test-runs; tools/ci/test_ci.py checks the rules.
"""
import argparse
import fnmatch
import functools
import ast
import json
import pathlib
import re
import signal
import subprocess
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'tools'/'ci'))
import gate  # noqa: E402
import patch_shards  # noqa: E402
import q2ci  # noqa: E402

ALL = 'all'
# tools/ modules the build or the fw host build imports: a change reaches the payload or the fw library.
BUILD_INPUTS = {'build', 'ipod', 'theme', 'art', 'peq', 'aic', 'rotator', 'pwm', 'mcu_park', 'module_elf',
                'splash', 'player', 'thirdparty', 'signing'}
# tools/ modules the build imports only to put a pinned file in the image: iroh.binary() is q2-iroh, uac1.module()
# usb_f_uac1.ko, each taken from the third-party cache by its SHA-256, and ui.build() is q2ui, a program of its own
# that only a --ui-spike build takes (docs/ui-rewrite.md). None reaches the payload's code or
# test/fw, so a change runs the build's own checks (test/build.py), not test/patch.py. tools/ci/test_ci.py checks
# that every tools/ module tools/build.py imports, directly or through another, is here or in BUILD_INPUTS.
IMAGE_INPUTS = {'iroh', 'uac1', 'ui'}
# tools/ folders a rule knows (any other, tools/ci/ aside, runs everything): (the test/unit files that read them,
# the test/X.py suites that do). Neither folder is in source_sha256 or in what tools/build.py reads itself.
TOOL_DIRS = {
    # uac1_build.py and the kernel sources it builds usb_f_uac1.ko from. tools/uac1.py hashes every file here
    # into its PIN, and the build stops (uac1.module()) when the hash is not the pinned one: test_uac1.py checks
    # the pin, test/build.py the build. test_iap_tools.py and test/uac1_bind.py load uac1_build.py; uac1_bind.py
    # and uac1_isoc.py run on the module the build carries (gate.SCRIPTS).
    'uac1_module': ({'test_uac1.py', 'test_iap_tools.py'}, ('test/build.py', 'test/uac1_bind.py', 'test/uac1_isoc.py')),
    # route 1's probe on a Q2 by hand: probe_build.py (test_iap_tools.py loads it) builds patch/q2_iap.c as the
    # probe, which test/iap_probe.py builds and runs on the host; the shell and PowerShell scripts run on the
    # devices. Nothing here goes into an image.
    'iap_probe': ({'test_iap_tools.py'}, ('test/iap_probe.py',)),
}
# remote/ paths a rule knows (a file or a folder's prefix; any other remote/ file runs everything): (the test/unit
# files that read them, the test/X.py suites that do). Nothing here is in source_sha256 or compiled into the payload.
REMOTE = {
    # the iroh sidecar's crate: tools/iroh.py hashes it into PIN (test_iroh.py fails on a crate edit with a stale pin)
    # and the build stops when the crate is not the pinned one, or with --build-iroh builds it (test/build.py)
    'remote/iroh-sidecar/': ({'test_iroh.py'}, ('test/build.py',)),
    # coda-rc's wire format: its message table against patch/coda_rc.h and the library's tags
    'remote/coda-rc.proto': ({'test_coda_rc.py', 'test_coda_rc_lib.py'}, ()),
}
# test/coverflow.py compiles patch/coverflow*.c and .h on the host with stand-ins for the behaviour of home.c,
# page_parallax.c and playlist_art.c, so a change to any of these plans it (in `other`). It is one of
# gate.PLANNED, which a plan that runs everything also names.
COVERFLOW_SOURCES = ('patch/coverflow*.[ch]', 'patch/home.c', 'patch/page_parallax.c', 'patch/playlist_art.c')
# gate.py's type-check suites, planned in `other`: a .py under tools/ or test/unit/ (or the mypy config)
# needs mypy-tools, one under test/fw/ needs mypy-fw. A merge batch runs them, so a branch that skips them
# here is rejected there.
MYPY_TOOLS, MYPY_FW = 'mypy-tools', 'mypy-fw'
MYPY_SUITES = (MYPY_TOOLS, MYPY_FW)
MYPY_CONFIG = 'pyproject.toml'
# docs/rebasing/sitenotes.py writes tools/stock_sites.tsv from every patch/ and tools/ file's stock addresses, so a
# change to any of them (or to the script) can leave the table stale: test/stock_sites.py runs its --check (12 s, no
# build). One of gate.PLANNED. A release's package step runs the same check, too late to name the branch at fault.
STOCK_SITES = 'test/stock_sites.py'
SITENOTES = 'docs/rebasing/sitenotes.py'
# The firmware's side of the files-on-card contract with the Subsonic app it carries (the caps line, .catplay, .playing,
# .ahead, .stream) and the pin of the app's build: test/subsonic_contract.py checks both sides still name the same
# words and files. One of gate.PLANNED; player/ too, as the player is what Download Ahead's .stream follows.
CONTRACT = 'test/subsonic_contract.py'
CONTRACT_SOURCES = ('patch/addon.c', 'patch/catalogue.c', 'patch/stream_state.c', 'tools/build.py', 'player/*')
# The same seam run for real: test/subsonic_seam.py runs the pinned app against a fake server with the firmware's own
# C on the other side of each file (docs/building.md#seam-test). One of gate.PLANNED. Its sides: the contract's, the
# play order playback.c gives .ahead, and the test's own harness (a change there runs the seam, not only test/fw).
SEAM = 'test/subsonic_seam.py'
SEAM_SOURCES = (*CONTRACT_SOURCES, 'patch/playback.c', 'test/fw/seam.py', 'test/fw/test_seam_subsonic.py')
# The suites that build or read sources outside test/fw and test/patch.py, which a change to one of those sources must
# plan: {suite: (the globs of what it builds, runs or reads, whether it compiles them)}. For one that compiles them the
# headers and .inc files they include, transitively (`included`), count as well, so a header shared with the payload
# plans it. Patterns, not names, so a file split out under the same prefix is covered (refactor/iap2-split's).
# test/coda_rc_e2e.py drives the Car Remote daemon and demo's shim over TCP on a pooled emulator, with
# tools/coda_rc_client.py as the phone and tools/ci/emu_pool.py's lease; test/iap_probe.py builds q2-iap from its
# sources and runs it as q2car.sh does; test/peq.py builds the EQ, its editor, games, video, Internet Radio,
# Spotify, the App Store's list, online lyrics, Tidal and scrobbling on the host and reads OLED's theme; test/coverflow.py
# the Cover Flow sources with the shared ones they include; test/player/run.py patch/peq.c as the player builds it;
# test/bt_launch.py runs the Bluetooth launcher and reads the plugins' ALSA file from a build.
# tools/ci/test_ci.py checks that every source a suite names in its code plans it.
SUITE_SOURCES = {
    'test/coda_rc_e2e.py': (('patch/coda_rc*', 'remote/coda-rc.proto', 'tools/coda_rc_client.py', 'tools/ci/emu_pool.py'),
                            True),
    'test/iap_probe.py': (('patch/q2_iap*.[ch]', 'patch/iap_*.[ch]', 'patch/iap2*.[ch]', 'patch/q2car.sh'), True),
    'test/peq.py': (('patch/peq*.[ch]', 'patch/hash.c', 'patch/games.c', 'patch/video.c', 'patch/radio.c', 'patch/spotify.c',
                     'patch/netupdate.c', 'patch/onlinematch.c', 'patch/tidal.c', 'patch/scrobble.c',
                     'assets/themes/oled/theme.xml'), True),
    'test/coverflow.py': ((*COVERFLOW_SOURCES, 'patch/coverflow_position.c', 'patch/corner_cover.c', 'patch/wheel_accel.c',
                           'patch/hash.c', 'tools/build.py', 'tools/theme.py'), True),
    'test/player/run.py': (('patch/peq.c',), True),
    'test/bt_launch.py': (('patch/bt_launch.sh', 'assets/alsa/25-vamp-bluealsa.conf'), False),
}
# The suites that run the build's payload as test/patch.py does (under Unicorn, test/patch_suite's Machines): any change
# to the payload or a build input plans them with it.
PAYLOAD_SUITES = ('test/playback.py', 'test/headset_io.py')
INCLUDE = re.compile(r'^\s*#\s*include\s+"([^"]+)"', re.M)


@functools.lru_cache(maxsize=None)
def included(root, globs):
    """The files globs name under root, with every file they #include "...", transitively, as root-relative paths."""
    seen, todo = set(), [p for g in globs for p in root.glob(g) if p.is_file()]
    while todo:
        f = todo.pop()
        rel = f.relative_to(root).as_posix()
        if rel in seen:
            continue
        seen.add(rel)
        if f.suffix in ('.c', '.h', '.inc', '.S'):
            todo += [q for q in (f.parent/m for m in INCLUDE.findall(f.read_text(errors='replace'))) if q.is_file()]
    return frozenset(seen)


def suite_needs(suite, path, root=ROOT):
    """Whether a change to path plans suite by SUITE_SOURCES."""
    globs, compiles = SUITE_SOURCES[suite]
    return any(fnmatch.fnmatchcase(path, g) for g in globs) or (compiles and path in included(pathlib.Path(root), globs))
SMOKE = 'smoke'  # tools/ci/smoke.py: optional here (run with --smoke), the post tier's on the trunk
INTEGRATION = re.compile(r'^(release/|integ/|main$|master$)')  # integ/*: the integrator's (docs/orchestration.md)
# test/unit files that check a rule over every file of a folder: (folder, suffixes or None for any, test).
# A change there runs the test whatever else the file maps to. tools/ci/test_ci.py checks that each folder a
# test/unit file globs is here.
SCANNERS = (
    ('patch/', ('.c', '.h', '.inc'), 'test_theme.py'),  # colours and type sizes named by token, not restated
    ('patch/', ('.c',), 'test_art.py'),                  # corner radii in the table, empty-state glyphs drawn
    ('patch/', None, 'test_docs.py'),                    # every doc anchor a source cites resolves
    ('tools/', None, 'test_docs.py'),
    ('test/', None, 'test_docs.py'),
)


class Plan:
    """What to run: each suite is ALL, a sorted list of its parts, or empty (nothing)."""
    def __init__(self):
        self.unit: set | str = set()
        self.fw: set | str = set()
        self.patch: set | str = set()
        self.other: set = set()
        self.why: list = []

    def everything(self, why):
        """unit, fw and patch whole, every other suite of the gate's merge tier, every suite a rule here plans
        (gate.PLANNED), every script gate.SCRIPTS declares runnable, and smoke: what a narrower plan could run, and
        what the merge queue then runs."""
        self.unit, self.fw, self.patch = ALL, ALL, ALL
        self.other.update({*gate.MERGE} - {'unit', 'fw', 'patch'}, gate.PLANNED, {SMOKE})
        # every script gate.SCRIPTS says can run here, and a line in why for each it says cannot, so a log shows
        # what --all left out and why
        self.other.update(gate.declared())
        self.why.append(why)
        self.why.extend(f'not run: {n}: {v}' for n, v in gate.SCRIPTS.items() if isinstance(v, str))

    def add(self, suite, parts, why):
        current = getattr(self, suite)
        if current != ALL:
            setattr(self, suite, ALL if parts == ALL else current | set(parts))
        self.why.append(why)

    def summary(self):
        d = self.as_dict()
        parts = [f'{k} {"all" if d[k] == ALL else len(d[k])}' for k in ('unit', 'fw', 'patch') if d[k]]
        return ', '.join(parts + d['other']) or 'nothing'

    def to_json(self):
        return json.dumps(self.as_dict())

    @classmethod
    def from_json(cls, text):
        p, d = cls(), json.loads(text)
        for k in ('unit', 'fw', 'patch'):
            setattr(p, k, ALL if d[k] == ALL else set(d[k]))
        p.other, p.why = set(d['other']), d['why']
        return p

    def as_dict(self):
        def show(v):
            return v if v == ALL else sorted(v)
        return {'unit': show(self.unit), 'fw': show(self.fw), 'patch': show(self.patch),
                'other': sorted(self.other), 'why': self.why}


def fw_files_for(source, root=ROOT):
    """The test/fw files that exercise patch/<source>: named for it, or naming the file, its statics or a
    function it defines. ALL when none does."""
    stem = pathlib.PurePath(source).stem
    text = (root/'patch'/source).read_text(errors='replace') if (root/'patch'/source).exists() else ''
    defined = {m for m in re.findall(r'^[A-Za-z_][\w \t\*]*?\b([A-Za-z_]\w{4,})\s*\([^;{]*\)\s*\{', text, re.M)}
    hits = set()
    for path in sorted((root/'test/fw').glob('test_*.py')):
        body = path.read_text(errors='replace')
        if (path.name == f'test_{stem}.py' or f'{stem}.c' in body or f'{stem}__' in body or
                any(f"'{d}'" in body or f'"{d}"' in body for d in defined)):
            hits.add(path.name)
    if not hits:
        return ALL
    return hits | {p.name for p in (root/'test/fw').glob('test_e2e_*.py')}


def unit_files_for(name, root=ROOT):
    """The test/unit files that name patch/<name>: those that read or compile it."""
    return {p.name for p in sorted((root/'test/unit').glob('test_*.py')) if name in p.read_text(errors='replace')}


def patch_tests_for(changed_lines, source):
    """Joined-suite line ranges (A:B) holding the tests a change to those lines (of the new suite) touches."""
    stmts = patch_shards.analyse(source)
    ends = [s.index for s in stmts if isinstance(s.node, ast.Expr) and ast.unparse(s.node) == 'passed()']
    touched = [s for s in stmts if any(s.line <= n <= s.end for n in changed_lines)]
    if not touched:
        return set()
    bound = set().union(*(s.kills for s in touched if s.definition))
    users = {s.index for s in stmts if not s.definition and s.loads & bound}
    want = {s.index for s in touched if not s.definition} | users
    if len(want) > len(stmts) // 3:
        return ALL
    ranges = set()
    for i in want:  # the whole test around it: from the previous passed() to the next
        before = [e for e in ends if e < i]
        after = [e for e in ends if e >= i]
        a = before[-1] + 1 if before else 0
        b = after[0] if after else len(stmts) - 1
        ranges.add((stmts[a].line, stmts[b].end))
    merged = []
    for a, b in sorted(ranges):
        if merged and a <= merged[-1][1] + 1:
            merged[-1] = (merged[-1][0], max(b, merged[-1][1]))
        else:
            merged.append((a, b))
    return {f'{a}:{b}' for a, b in merged}


def suite_starts(root):
    """{file name in test/patch_suite/: the joined suite's line its first line is}."""
    out, line = {}, 1
    for f in sorted((root/'test/patch_suite').glob('[0-9][0-9]_*.py')):
        out[f.name] = line
        line += f.read_text().count('\n')
    return out


def cov_target(path, root=ROOT):
    """The tools/ module whose coverage a change to `path` can lower, else None: the module itself (not
    tools/ci/, which test/unit does not cover), or the test/unit/test_X.py of tools/X.py (a test dropped or
    weakened). One a branch deleted is not there to measure."""
    parts, stem = pathlib.PurePath(path).parts, pathlib.PurePath(path).stem
    if path.endswith('.py') and parts[0] == 'tools' and parts[1:2] != ('ci',) and len(parts) > 1:
        return path if (root/path).exists() else None
    if path.endswith('.py') and parts[:2] == ('test', 'unit') and stem.startswith('test_'):
        mod = f'tools/{stem[5:]}.py'
        return mod if (root/mod).exists() else None
    return None


def plan_for(changes, root=ROOT, patch_source=None):
    """A Plan for changes: {path: set of changed line numbers in the new file (empty if deleted or unknown)}."""
    p = Plan()
    for path, lines in sorted(changes.items()):
        parts = pathlib.PurePath(path).parts
        name, stem, suffix = parts[-1], pathlib.PurePath(path).stem, pathlib.PurePath(path).suffix
        if suffix == '.py' and (path.startswith(('tools/', 'test/unit/')) or path == MYPY_CONFIG):
            p.add('other', {MYPY_TOOLS}, f'{path}: type-checked by {MYPY_TOOLS}')
        if suffix == '.py' and path.startswith('test/fw/') or path == MYPY_CONFIG:
            p.add('other', {MYPY_FW}, f'{path}: type-checked by {MYPY_FW}')
        if (cov := cov_target(path, root)):
            p.other.add(gate.COV + cov)
            p.why.append(f'{path}: {cov} under the post tier\'s coverage floor (tools/ci/covcheck.py)')
        if parts[0] == 'patch' or (parts[0] == 'tools' and parts[1:2] != ('ci',)) or path == SITENOTES:
            p.other.add(STOCK_SITES)
            p.why.append(f'{path}: {STOCK_SITES} (sitenotes.py --check)')
        for folder, suffixes, test in SCANNERS:
            if path.startswith(folder) and (suffixes is None or suffix in suffixes):
                p.add('unit', {test}, f'{path}: {test} checks every file of {folder}')
        if any(fnmatch.fnmatchcase(path, g) for g in CONTRACT_SOURCES):
            p.other.add(CONTRACT)
            p.why.append(f'{path}: {CONTRACT} (the Subsonic app\'s files-on-card contract)')
        if any(fnmatch.fnmatchcase(path, g) for g in SEAM_SOURCES):
            p.other.add(SEAM)
            p.why.append(f'{path}: {SEAM} (the real Subsonic app against the firmware\'s C)')
        for suite in SUITE_SOURCES:
            if suite_needs(suite, path, root):
                p.other.add(suite)
                p.why.append(f'{path}: {suite}')
        if path.startswith('test/patch_suite/') and suffix == '.py':  # one file of the suite: its lines, joined
            starts = suite_starts(root)
            if patch_source is None:
                patch_source = patch_shards.suite_text(root/'test/patch.py')
            first = starts.get(name)
            p.other.update({'test/playback.py', 'test/headset_io.py'})  # they load the suite's Machines
            if not lines or first is None:
                p.add('patch', ALL, f'{path}: changed, lines unknown')
            else:
                ranges = patch_tests_for({first + n - 1 for n in lines}, patch_source)
                p.add('patch', ranges, f'{path}: the tests on joined lines {", ".join(sorted(ranges)) if ranges != ALL else "(most)"}')
        elif path in ('test/patch.py', 'test/suite_source.py'):  # the runner: every test
            p.add('patch', ALL, f'{path}: the suite\'s runner')
        elif parts[0] == 'patch':
            p.add('patch', ALL, f'{path}: the payload')
            fw = fw_files_for(name, root) if suffix == '.c' and len(parts) == 2 else ALL
            p.add('fw', fw, f'{path}: test/fw ' + ('all' if fw == ALL else ', '.join(sorted(fw))))
            p.other.update({'test/build.py', SMOKE, *PAYLOAD_SUITES})
            if len(parts) == 2 and (named := unit_files_for(name, root)):
                p.add('unit', named, f'{path}: test/unit ' + ', '.join(sorted(named)))
        elif parts[0] == 'tools' and len(parts) > 2 and parts[1] in TOOL_DIRS:
            unit, scripts = TOOL_DIRS[parts[1]]
            have = {u for u in unit if (root/'test/unit'/u).exists()}
            p.add('unit', have or ALL, f'{path}: test/unit ' + (', '.join(sorted(have)) if have else 'all'))
            p.other.update(s for s in scripts if (root/s).exists())
            p.why.append(f'{path}: ' + ', '.join(s for s in scripts if (root/s).exists()))
        elif parts[0] == 'tools' and len(parts) == 2:
            unit = root/'test/unit'/f'test_{stem}.py'
            p.add('unit', {unit.name} if unit.exists() else ALL, f'{path}: its unit tests')
            if stem in BUILD_INPUTS or suffix in ('.c', '.h', '.S', '.tsv', '.patch'):
                p.add('patch', ALL, f'{path}: a build input')
                p.add('fw', ALL, f'{path}: a build input (test/fw builds from tools/ lists)')
                p.other.update({'test/build.py', *PAYLOAD_SUITES})
            elif stem in IMAGE_INPUTS and suffix == '.py':
                p.other.add('test/build.py')
                p.why.append(f'{path}: a file the build puts in the image (test/build.py)')
        elif parts[0] == 'remote' and (rule := next((v for k, v in REMOTE.items() if path.startswith(k)), None)):
            unit, scripts = rule
            have = {u for u in unit if (root/'test/unit'/u).exists()}
            p.add('unit', have or ALL, f'{path}: test/unit ' + (', '.join(sorted(have)) if have else 'all'))
            p.other.update(s for s in scripts if (root/s).exists())
            if scripts:
                p.why.append(f'{path}: ' + ', '.join(s for s in scripts if (root/s).exists()))
        elif parts[:2] == ('tools', 'ci'):
            p.other.update({'tools/ci/test_ci.py', 'tools/ci/test_orch.py'})
            p.why.append(f'{path}: the CI tools\' own tests')
        elif parts[0] == 'assets':
            p.add('patch', ALL, f'{path}: an asset the build rewrites')
            p.add('fw', ALL, f'{path}: an asset the fw build reads (themes)')
            p.other.update(PAYLOAD_SUITES)
            p.add('unit', ALL, f'{path}: tools/ tests read the assets')
        elif parts[:2] == ('test', 'fw'):
            if name.startswith('test_'):
                p.add('fw', {name}, f'{path}: itself')
            else:
                p.add('fw', ALL, f'{path}: fw infrastructure')
        elif parts[:2] == ('test', 'unit'):
            if name.startswith('test_'):
                p.add('unit', {name}, f'{path}: itself')
            else:
                p.add('unit', ALL, f'{path}: unit infrastructure')
        elif parts[0] == 'test' and len(parts) == 2 and suffix in ('.py', '.sh'):
            if (root/path).exists():  # one the branch deleted is not there to run
                p.other.add(path)
                p.why.append(f'{path}: itself')
        elif parts[:2] == ('test', 'player') or parts[0] == 'player':
            p.other.add('test/player/run.py')
            p.why.append(f'{path}: the new player')
        elif parts[0] == 'ui':
            p.other.add('test/ui.py')
            p.why.append(f'{path}: the menu UI spike')
        elif parts[:2] == ('docs', 'manual'):
            p.other.add('test/build.py')
            p.why.append(f'{path}: packaged in the build')
        elif suffix == '.md' or parts[0] == 'docs':
            p.add('unit', {'test_docs.py'}, f'{path}: doc anchors')
        elif name in ('.gitattributes', '.gitignore', 'LICENSE', 'CODE_OF_CONDUCT.md') or parts[0] == '.github':
            p.why.append(f'{path}: no test')
        else:
            p.everything(f'{path}: no rule, so everything')
    return p


def runs_everything(all_, base, branch):
    """Whether a run plans everything: --all, or an integration branch given no --base (a --plan-json is read
    before this is asked)."""
    return bool(all_ or (base is None and INTEGRATION.match(branch)))


def changed(base, head='HEAD', root=ROOT):
    """{path: changed line numbers in head} between base's merge base with head and head, plus the work tree."""
    merge_base = subprocess.run(['git', '-C', str(root), 'merge-base', base, head], capture_output=True, text=True,
                                check=True).stdout.strip()
    diff = subprocess.run(['git', '-C', str(root), 'diff', '-U0', '--no-color', '--no-renames', merge_base],
                          capture_output=True, text=True, check=True).stdout
    out = {}
    path = None
    for line in diff.splitlines():
        if line.startswith('+++ '):
            path = None if line[4:] == '/dev/null' else line[6:]
            if path:
                out.setdefault(path, set())
        elif line.startswith('--- ') and line[4:] != '/dev/null':
            out.setdefault(line[6:], set())
        elif line.startswith('@@') and path:
            m = re.match(r'@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@', line)
            if m:
                start, count = int(m[1]), int(m[2] if m[2] is not None else 1)
                out[path] |= set(range(start, start + max(count, 1)))  # a pure deletion marks the line after it
    untracked = subprocess.run(['git', '-C', str(root), 'ls-files', '--others', '--exclude-standard'],
                               capture_output=True, text=True, check=True).stdout.split()
    for path in untracked:
        out.setdefault(path, set())
    return out


def build_options(build):
    """The build's options as gate.py keys a pass by them: rebuild.py's record (rebuild.json) of what it was built
    with, else the build's own path, so a pass is never taken for another build's. No build: the gate's default,
    as only suites that need none then run, and their passes are then the gate's too."""
    if build is None:
        return ['--dev']
    try:
        return json.loads((pathlib.Path(build)/'rebuild.json').read_text())['options']
    except (OSError, ValueError, KeyError):
        return [f'build:{pathlib.Path(build).resolve()}']


def suites(plan, build, python, logs, smoke=False):
    """The gate's suites for the plan (gate.suites_for: each runs as a merge batch runs it, test/patch.py whole in
    one process), with smoke when asked for and given a build, and without a build only what needs none."""
    d = plan.as_dict()
    out = gate.suites_for(d, [], python, logs)
    if SMOKE in d['other']:
        if smoke and build:
            out[SMOKE] = gate.full_suite(SMOKE, python, logs)()
        else:
            print(f'== {SMOKE}: optional; --run BUILD --smoke walks the build on a pooled emulator (about 2 min)')
    if not build:
        for name, s in list(out.items()):
            if name == 'test/build.py':  # its checks of the tree need no build
                out[name] = gate.Suite(name, [python, name], heavy=True)
            elif s.needs_build:
                del out[name]
                print(f'== {name}: needs a build: run --run BUILD')
    return out


def summary(results, wall):
    """A row a suite: ok, FAILED (and the kind), FAILED, then passed after a change (the retry ran on another tree or
    build), flaky (failed, then passed alone on the same one) or cached, and its seconds; then every first try that
    failed, with the tests its log named."""
    rows = [f'{"suite":24} {"result":14} {"seconds":>7}']
    for name, r in results.items():
        state = ('cached' if r.get('cached') else f'flaky ({r["flaky"]})' if r.get('flaky') else 'ok') \
            if r['rc'] == 0 else 'FAILED, then passed after a change' if r.get('changed') else \
            f'FAILED ({r.get("kind", r["rc"])})'
        rows.append(f'{name:24} {state:14} {r["seconds"]:7d}' + (f'  {r["log"]}' if r['rc'] != 0 else ''))
    bad = [n for n, r in results.items() if r['rc'] != 0]
    rows.append(f'{"wall":24} {"":14} {round(wall):7d}  ' + (f'FAILED: {", ".join(bad)}' if bad else 'all passed'))
    return '\n'.join(rows + gate.first_tries(results))


def run(plan, build, python, smoke=False, retry=True, cache=True, logs=None, poll=2.0):
    """Run the plan through gate.run: the suites at once as memory allows, the longest first, heavy ones under
    q2-heavy; a failure run once more alone (retry), a pass then reported as flaky; a suite that passed on this
    tree (gate.tree_key, a clean worktree only) skipped as cached (cache). Returns the number that failed."""
    logs = logs or pathlib.Path.home()/'.cache/q2-gate'/f'{ROOT.name}-affected-{time.strftime("%Y%m%d-%H%M%S")}'
    todo = suites(plan, build, python, logs, smoke)
    if not todo:
        return 0
    print(f'== {len(todo)} suites, as memory allows: {", ".join(todo)}; logs in {logs}', flush=True)
    t = time.monotonic()
    results = gate.run(todo, build_options(build), logs, python, build=build, log=lambda m: print(f'== {m}', flush=True),
                       poll=poll, retry=retry, tree=gate.tree_key() if cache else None, flakes=gate.FLAKES)
    for name, r in results.items():
        if r['rc'] != 0 and pathlib.Path(r['log']).exists():
            tail = pathlib.Path(r['log']).read_text(errors='replace').splitlines()[-30:]
            # `== NAME: FAILED` is how merge_train.py names the suites that failed
            print(f'\n== {name}: FAILED; the end of {r["log"]}:\n' + '\n'.join(tail), flush=True)
    print('\n' + summary(results, time.monotonic() - t), flush=True)
    return sum(r['rc'] != 0 for r in results.values())


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--base', help='what the branch is compared with (default: the trunk, '
                    f'now {q2ci.trunk()}); given, it is used on an integration branch too')
    ap.add_argument('--all', action='store_true', help='everything, as an integration branch runs')
    ap.add_argument('--json', action='store_true')
    ap.add_argument('--run', metavar='BUILD', nargs='?', const='', help='run the plan; BUILD for the suites that need one')
    ap.add_argument('--smoke', action='store_true', help='with --run, also run a planned smoke test (tools/ci/smoke.py)')
    ap.add_argument('--no-cache', action='store_true', help='with --run, run a suite even when it passed on this tree')
    ap.add_argument('--no-retry', action='store_true', help='with --run, do not run a failed suite a second time')
    ap.add_argument('--python', default=sys.executable)
    ap.add_argument('--plan-json', metavar='FILE', help='take the plan from FILE (- for stdin), as merge_batch.py and merge_train.py hand it')
    args = ap.parse_args()
    branch = subprocess.run(['git', '-C', str(ROOT), 'rev-parse', '--abbrev-ref', 'HEAD'], capture_output=True,
                            text=True).stdout.strip()
    if args.plan_json:
        plan = Plan.from_json(sys.stdin.read() if args.plan_json == '-' else pathlib.Path(args.plan_json).read_text())
    elif runs_everything(args.all, args.base, branch):
        plan = Plan()
        plan.everything('--all' if args.all else f'{branch} is an integration branch: everything')
    else:
        plan = plan_for(changed(args.base or q2ci.trunk()))
    d = plan.as_dict()
    if args.json:
        print(json.dumps(d, indent=1))
    else:
        for suite in ('unit', 'fw', 'patch', 'other'):
            v = d[suite]
            print(f'{suite:6} {"all" if v == ALL else ", ".join(v) if v else "-"}')
        for why in d['why']:
            print(f'  {why}')
    if args.run is not None:
        signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))  # so gate.run ends its suites
        return 1 if run(plan, args.run or None, args.python, args.smoke, retry=not args.no_retry,
                        cache=not args.no_cache) else 0
    return 0


if __name__ == '__main__':
    sys.exit(main())
