#!/usr/bin/env python3
"""Which tests a feature branch's changes need, from the files it changed against a base.

    python3 tools/ci/affected.py                      # changes against the trunk's merge base, as a plan
    python3 tools/ci/affected.py --base main --json   # the same plan as JSON
    python3 tools/ci/affected.py --run BUILD          # and run it (all of test/patch.py serially)
    python3 tools/ci/affected.py --all --run BUILD    # everything: what an integration branch runs

A feature branch runs what its files map to; an integration branch (release/*, main) runs everything, as
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
  test/patch.py and test/fw;
- a test file runs itself;
- a .py under tools/ or test/unit/ also runs gate.py's mypy-tools suite, one under test/fw/ mypy-fw, and
  pyproject.toml (the mypy config) both: the merge queue runs them, so a plan without them passes here and is
  rejected there. They are run as gate.py defines them (`gate.full_suite`);
- a payload change (patch/) also plans `smoke`, tools/ci/smoke.py on a pooled emulator, but runs it only with
  --smoke: it is optional for a worker, and a merge batch leaves it out (gate.OPTIONAL), as the queue runs it
  on the trunk's tip after landing, where a failure turns the tip red.

Documented in docs/building.md#fast-test-runs; tools/ci/test_ci.py checks the rules.
"""
import argparse
import fnmatch
import ast
import json
import os
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'tools'/'ci'))
import gate  # noqa: E402
import patch_shards  # noqa: E402
import q2ci  # noqa: E402

ALL = 'all'
# tools/ modules the build or the fw host build imports: a change reaches the payload or the fw library.
BUILD_INPUTS = {'build', 'ipod', 'theme', 'art', 'peq', 'aic', 'rotator', 'pwm', 'mcu_park', 'module_elf',
                'splash', 'player', 'thirdparty', 'signing'}
# test/coverflow.py compiles patch/coverflow*.c and .h on the host with stand-ins for the behaviour of home.c,
# page_parallax.c and playlist_art.c, so a change to any of these plans it (in `other`). It is in the gate's
# merge tier as well (gate.PLANNED), which a plan that runs unit, fw and patch whole gets.
COVERFLOW_SOURCES = ('patch/coverflow*.[ch]', 'patch/home.c', 'patch/page_parallax.c', 'patch/playlist_art.c')
# gate.py's type-check suites, planned in `other`: a .py under tools/ or test/unit/ (or the mypy config)
# needs mypy-tools, one under test/fw/ needs mypy-fw. A merge batch runs them, so a branch that skips them
# here is rejected there.
MYPY_TOOLS, MYPY_FW = 'mypy-tools', 'mypy-fw'
MYPY_SUITES = (MYPY_TOOLS, MYPY_FW)
MYPY_CONFIG = 'pyproject.toml'
SMOKE = 'smoke'  # tools/ci/smoke.py: optional here (run with --smoke), the post tier's on the trunk
INTEGRATION = re.compile(r'^(release/|integrate/|main$|master$)')
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
        """unit, fw and patch whole, and every other suite of the gate's merge tier and smoke: what the
        merge queue runs for such a plan, so --run here passes only where the queue would."""
        self.unit, self.fw, self.patch = ALL, ALL, ALL
        self.other.update({*gate.MERGE} - {'unit', 'fw', 'patch'}, {SMOKE})
        self.why.append(why)

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


def mypy_argv(name, python):
    """The command gate.py runs for the type-check suite `name`."""
    return gate.full_suite(name, python, pathlib.Path('.'))().argv


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
        for folder, suffixes, test in SCANNERS:
            if path.startswith(folder) and (suffixes is None or suffix in suffixes):
                p.add('unit', {test}, f'{path}: {test} checks every file of {folder}')
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
            p.other.update({'test/build.py', SMOKE})
            if any(fnmatch.fnmatchcase(path, g) for g in COVERFLOW_SOURCES):
                p.other.add('test/coverflow.py')
                p.why.append(f'{path}: test/coverflow.py')
            if len(parts) == 2 and (named := unit_files_for(name, root)):
                p.add('unit', named, f'{path}: test/unit ' + ', '.join(sorted(named)))
        elif parts[0] == 'tools' and len(parts) == 2:
            unit = root/'test/unit'/f'test_{stem}.py'
            p.add('unit', {unit.name} if unit.exists() else ALL, f'{path}: its unit tests')
            if stem in BUILD_INPUTS or suffix in ('.c', '.h', '.S', '.tsv', '.patch'):
                p.add('patch', ALL, f'{path}: a build input')
                p.add('fw', ALL, f'{path}: a build input (test/fw builds from tools/ lists)')
                p.other.add('test/build.py')
        elif parts[:2] == ('tools', 'ci'):
            p.other.update({'tools/ci/test_ci.py', 'tools/ci/test_orch.py'})
            p.why.append(f'{path}: the CI tools\' own tests')
        elif parts[0] == 'assets':
            p.add('patch', ALL, f'{path}: an asset the build rewrites')
            p.add('fw', ALL, f'{path}: an asset the fw build reads (themes)')
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


def run(plan, build, python, smoke=False):
    """Run the plan; returns the number of suites that failed. smoke: run a planned smoke test too."""
    failed = 0
    q2heavy = os.environ.get('Q2_HEAVY', str(pathlib.Path.home()/'.local/bin/q2-heavy'))
    heavy = [q2heavy] if pathlib.Path(q2heavy).exists() else []

    def go(name, cmd, is_heavy=False):
        nonlocal failed
        print(f'== {name}: {" ".join(cmd)}', flush=True)
        rc = subprocess.run((heavy if is_heavy else []) + cmd, cwd=ROOT).returncode
        print(f'== {name}: {"ok" if not rc else f"FAILED (exit {rc})"}', flush=True)
        failed += bool(rc)

    d = plan.as_dict()
    if d['unit']:
        go('unit', [python, '-m', 'pytest', '-q', '-p', 'no:cacheprovider',
                    *(['test/unit'] if d['unit'] == ALL else [f'test/unit/{f}' for f in d['unit']])])
    if d['fw']:
        go('fw', [python, str(ROOT/'tools/ci/fw.py'), *([] if d['fw'] == ALL else [f'test/fw/{f}' for f in d['fw']])])
    if d['patch'] and build:
        if d['patch'] == ALL:  # one process: a shard set replays most of the suite in every shard
            go('patch', [python, 'test/patch.py', build], is_heavy=True)
        else:
            go('patch', [python, str(ROOT/'tools/ci/patch_shards.py'), build, '--lines', ','.join(d['patch'])])
    for other in d['other']:
        if other == SMOKE:
            if smoke and build:
                go(other, [python, str(ROOT/'tools/ci/smoke.py'), build], is_heavy=True)
            else:
                print(f'== {other}: optional; --run BUILD --smoke walks the build on a pooled emulator (about 2 min)')
        elif other in MYPY_SUITES:
            go(other, mypy_argv(other, python))
        elif other.startswith('tools/ci/test_'):
            go(other, [python, '-m', 'pytest', '-q', '-p', 'no:cacheprovider', other])
        elif other == 'test/build.py':
            go(other, [python, other] + (['--build', build] if build else []))
        elif other in gate.NO_BUILD_ARG:
            go(other, [python, other], is_heavy=True)
        elif other.endswith('.py') and build:
            go(other, [python, other, build], is_heavy=True)
        else:
            print(f'== {other}: run it by hand (docs/building.md#tests says what it needs)')
    return failed


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--base', default=q2ci.trunk(), help='what the branch is compared with (default: the trunk, '
                    f'now {q2ci.trunk()})')
    ap.add_argument('--all', action='store_true', help='everything, as an integration branch runs')
    ap.add_argument('--json', action='store_true')
    ap.add_argument('--run', metavar='BUILD', nargs='?', const='', help='run the plan; BUILD for the suites that need one')
    ap.add_argument('--smoke', action='store_true', help='with --run, also run a planned smoke test (tools/ci/smoke.py)')
    ap.add_argument('--python', default=sys.executable)
    ap.add_argument('--plan-json', metavar='FILE', help='take the plan from FILE (- for stdin), as merge_batch.py and merge_train.py hand it')
    args = ap.parse_args()
    branch = subprocess.run(['git', '-C', str(ROOT), 'rev-parse', '--abbrev-ref', 'HEAD'], capture_output=True,
                            text=True).stdout.strip()
    if args.plan_json:
        plan = Plan.from_json(sys.stdin.read() if args.plan_json == '-' else pathlib.Path(args.plan_json).read_text())
    elif args.all or INTEGRATION.match(branch):
        plan = Plan()
        plan.everything('--all' if args.all else f'{branch} is an integration branch: everything')
    else:
        plan = plan_for(changed(args.base))
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
        return 1 if run(plan, args.run or None, args.python, args.smoke) else 0
    return 0


if __name__ == '__main__':
    sys.exit(main())
