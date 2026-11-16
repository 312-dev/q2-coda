#!/usr/bin/env python3
"""Run test/patch.py in parallel shards, with the result a whole run gives.

    python3 tools/ci/patch_shards.py BUILD [-j N] [--from FILE]      # every check, N processes
    python3 tools/ci/patch_shards.py BUILD --lines 9000:9400          # only the statements on those lines
    python3 tools/ci/patch_shards.py BUILD --verify                   # also a serial run: the traces must match

test/patch.py is one script of about 13,000 top-level statements that share module state. A shard runs
a contiguous range of them, preceded by the statements before the range that it depends on (its slice):
every def, class and import, plus each earlier statement that binds or changes a name the range reads,
found by name from the source (`slice_of`), never by editing test/patch.py. Statements replayed for a
slice run with `passed()` counting nothing, the draw trace off and their failures ignored: the shard
that owns a statement reports its failure. Each shard stops at its first failure, as a whole run does,
so a sharded run reports at least the failure a serial run stops at (the earliest one) and maybe later
ones besides. The exit status is 0 only when every shard passes, and the total of checks printed on
success equals a serial run's.

Name analysis cannot see state shared through files or a C library's memory (the filter's `watch`
marks are one: a mark set by an earlier test is set for a later one). `--verify` runs the serial suite
too, both under Q2_TRACE, and requires the shards' traces, concatenated in order, to equal the serial
trace byte for byte: run it after a change to how test/patch.py is laid out, and an integration branch
runs it before a release (docs/building.md#fast-test-runs).

Shard boundaries balance measured time: each run records how long each statement took in
~/.cache/q2-patch-shards/profile.json (keyed by the statement's text), and the next run cuts by it.
N defaults to what memory allows: MemAvailable less a 5 GB reserve, over one shard's peak RSS (about
0.9 GB), at most the CPU count and 8.
"""
import argparse
import ast
import bisect
import concurrent.futures
import ctypes
import faulthandler
import gc
import hashlib
import json
import os
import pathlib
import resource
import subprocess
import sys
import symtable
import tempfile
import time
import traceback

ROOT = pathlib.Path(__file__).resolve().parents[2]
PROFILE = pathlib.Path.home()/'.cache/q2-patch-shards/profile.json'
HEAVY = pathlib.Path.home()/'.local/bin/q2-heavy'
SHARD_RSS = 1.5e9  # one shard's peak RSS, measured: 0.6-1.8 GB (a serial run: 1.2 GB)
RESERVE = 5e9  # what stays free for the rest of the machine: q2-heavy's floor
# Names the runner handles itself: passed()'s counter is per shard and summed, and the trace file is
# opened at the range's start, so neither ties a range to the statements before it.
OWNED = {'checks', 'TRACE', 'trace'}
# Methods that only read their receiver: a call of one on a module-level name does not change it.
READS = {'get', 'keys', 'values', 'items', 'copy', 'index', 'count', 'startswith', 'endswith', 'split', 'rsplit',
         'splitlines', 'encode', 'decode', 'format', 'join', 'strip', 'rstrip', 'lstrip', 'find', 'rfind', 'lower',
         'upper', 'hex', 'to_bytes', 'from_bytes', 'read_text', 'read_bytes', 'exists', 'is_file', 'is_dir',
         'relative_to', 'with_suffix', 'replace', 'partition', 'isdigit', 'isalpha', 'fromhex', 'search', 'match',
         'fullmatch', 'findall', 'finditer', 'sub', 'issubset', 'issuperset', 'union', 'intersection',
         'difference', 'most_common', 'bit_length', 'ljust', 'rjust', 'center', 'zfill', 'casefold', 'title'}


class Stmt:
    """One top-level statement: what it reads, binds outright (kills), may bind, and changes in place."""
    __slots__ = ('index', 'line', 'end', 'node', 'loads', 'kills', 'binds', 'mutates', 'definition', 'key', 'cost', 'pinned')

    def __init__(self, index, node, key):
        self.index, self.node, self.key = index, node, key
        self.line, self.end = node.lineno, node.end_lineno
        self.loads, self.kills, self.binds, self.mutates = set(), set(), set(), set()
        self.definition = isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Import, ast.ImportFrom))
        self.cost = 0.0
        self.pinned = False  # replayed in every shard after it: it changes a module's state (sys.path.insert)


def _targets(node):
    """Names an assignment target binds outright: a, (a, b), [a, *b]."""
    if isinstance(node, ast.Name):
        yield node.id
    elif isinstance(node, (ast.Tuple, ast.List)):
        for e in node.elts:
            yield from _targets(e)
    elif isinstance(node, ast.Starred):
        yield from _targets(node.value)


def _base(node):
    """The module-level name at the root of a.b[c].d, or None."""
    while isinstance(node, (ast.Attribute, ast.Subscript)):
        node = node.value
    return node.id if isinstance(node, ast.Name) else None


def _visit(node, local=frozenset()):
    """(node, names local there) for every node under a top-level statement: a lambda's parameters, a
    comprehension's targets and a nested def's parameters and assignments are not the module's."""
    if isinstance(node, ast.Lambda):
        args = node.args
        yield from ((d, l) for e in args.defaults + [d for d in args.kw_defaults if d] for d, l in _visit(e, local))
        inner = local | {a.arg for a in args.posonlyargs + args.args + args.kwonlyargs}
        inner |= {a.arg for a in (args.vararg, args.kwarg) if a}
        yield from _visit(node.body, frozenset(inner))
        return
    if isinstance(node, (ast.ListComp, ast.SetComp, ast.GeneratorExp, ast.DictComp)):
        inner = local | {n for g in node.generators for n in _targets(g.target)}
        for k, g in enumerate(node.generators):  # the first iterable is evaluated outside
            yield from _visit(g.iter, local if k == 0 else frozenset(inner))
            for c in g.ifs:
                yield from _visit(c, frozenset(inner))
        for e in ([node.key, node.value] if isinstance(node, ast.DictComp) else [node.elt]):
            yield from _visit(e, frozenset(inner))
        return
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        args = node.args
        for e in node.decorator_list + args.defaults + [d for d in args.kw_defaults if d]:
            yield from _visit(e, local)
        yield (node, local)
        declared = {n for x in ast.walk(node) if isinstance(x, ast.Global) for n in x.names}
        inner = local | {a.arg for a in args.posonlyargs + args.args + args.kwonlyargs}
        inner |= {a.arg for a in (args.vararg, args.kwarg) if a}
        inner |= {x.id for x in ast.walk(node) if isinstance(x, ast.Name) and isinstance(x.ctx, ast.Store)}
        inner -= declared
        for st in node.body:
            yield from _visit(st, frozenset(inner))
        return
    yield (node, local)
    for c in _in_order(node):
        yield from _visit(c, local)


def _in_order(node):
    """A node's children in the order they run, as far as binding goes: an assignment's value before its
    targets, a loop's iterable before its target and the target before the body."""
    if isinstance(node, (ast.Assign, ast.AnnAssign, ast.NamedExpr)):
        rest = node.targets if isinstance(node, ast.Assign) else [node.target]
        return ([node.value] if node.value else []) + list(rest) + (
            [node.annotation] if isinstance(node, ast.AnnAssign) else [])
    if isinstance(node, ast.AugAssign):
        return [node.value, node.target]
    if isinstance(node, ast.For):
        return [node.iter, node.target, *node.body, *node.orelse]
    if isinstance(node, ast.With):
        out = []
        for item in node.items:
            out += [item.context_expr] + ([item.optional_vars] if item.optional_vars else [])
        return out + node.body
    return list(ast.iter_child_nodes(node))


def _changes(nodes, is_module_name, modules):
    """Module-level names the nodes change in place: x[k] = v, x.a = v, del x[k], x += v, x.m(...)."""
    out = set()
    for n in nodes:
        if isinstance(n, (ast.Attribute, ast.Subscript)) and isinstance(n.ctx, (ast.Store, ast.Del)):
            b = _base(n)
            if b and is_module_name(b):
                out.add(b)
        elif isinstance(n, ast.AugAssign):
            b = _base(n.target)
            if b and is_module_name(b):
                out.add(b)
        elif isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute):
            b = _base(n.func.value)
            if b and is_module_name(b) and b not in modules and n.func.attr not in READS:
                out.add(b)
    return out


def _walk_tables(table):
    yield table
    for child in table.get_children():
        yield from _walk_tables(child)


def analyse(source):
    """test/patch.py's top-level statements, each with the names it depends on and affects, the effects
    of the module's functions and classes folded into the statements that name them."""
    tree = ast.parse(source)
    module = symtable.symtable(source, 'patch.py', 'exec')
    # Receivers whose method calls change nothing the analysis tracks: modules and imported names (their
    # state is a library's), and the module's own functions and classes (C.method(self) is a call, not a change).
    modules, assigned = set(), set()
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            modules |= {(a.asname or a.name).split('.')[0] for a in node.names}
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            modules.add(node.name)
        else:
            assigned |= {n.id for n in ast.walk(node) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store)}
    modules -= assigned
    # Each callable's module-level reads and in-place changes, through symtable's scopes.
    tables = {}
    for child in module.get_children():
        tables.setdefault((child.get_lineno(), child.get_name()), []).append(child)
    # Each definition's direct module-level reads and in-place changes. A name defined twice (helpers such as
    # paint or press are, test by test) means, at a statement, its latest definition before that statement.
    direct = {}  # statement index -> (reads, changes)
    defined_at = {}  # name -> [statement indexes]
    classes = []

    def effects(node):
        r, c = set(), set()
        for table in tables.get((node.lineno, node.name), []):
            for t in _walk_tables(table):
                r |= {s.get_name() for s in t.get_symbols() if s.is_global() and s.is_referenced()}
                c |= {s.get_name() for s in t.get_symbols() if s.is_declared_global() and s.is_assigned()}
        c |= _changes(ast.walk(node), lambda name: name in r, modules)
        if isinstance(node, ast.ClassDef):
            r |= {b for base in node.bases for b in [_base(base)] if b}
        return r - OWNED, c - OWNED

    for i, node in enumerate(tree.body):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            direct[i] = effects(node)
            defined_at.setdefault(node.name, []).append(i)
            if isinstance(node, ast.ClassDef):
                classes.append(i)
    redefined = sorted(i for idx in defined_at.values() if len(idx) > 1 for i in idx)
    closures = {}  # (epoch, names) -> (reads, changes), the transitive effect of calling those names

    def expand(names, at):
        """What naming `names` at statement `at` may read and change, through every definition reachable."""
        epoch = bisect.bisect_left(redefined, at)
        key = (epoch, frozenset(names))
        if key in closures:
            return closures[key]
        r, c, todo, done = set(), set(), [n for n in names if n in defined_at], set()
        while todo:
            name = todo.pop()
            if name in done:
                continue
            done.add(name)
            before = [i for i in defined_at[name] if i < at]
            for i in before[-1:] if before else defined_at[name][:1]:
                dr, dc = direct[i]
                r |= dr
                c |= dc
                todo.extend(n for n in dr if n in defined_at and n not in done)
        closures[key] = (r, c)
        return r, c

    method_reads = set().union(*(direct[i][0] for i in classes)) if classes else set()
    method_changes = set().union(*(direct[i][1] for i in classes)) if classes else set()

    lines = source.splitlines()
    seen = {}
    stmts = []
    for i, node in enumerate(tree.body):
        text = '\n'.join(lines[node.lineno - 1:node.end_lineno])
        digest = hashlib.sha256(text.encode()).hexdigest()[:16]
        seen[digest] = seen.get(digest, 0) + 1
        s = Stmt(i, node, f'{digest}.{seen[digest]}')
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            # A definition runs its decorators, defaults and bases now; its body is counted where it is named.
            s.kills = s.binds = {node.name}
            now = node.decorator_list + (node.bases + [k.value for k in node.keywords] if isinstance(node, ast.ClassDef)
                                         else node.args.defaults + [d for d in node.args.kw_defaults if d])
            if isinstance(node, ast.ClassDef):  # a class body runs now, its methods' bodies later
                now = now + [e for st in node.body for e, _ in _visit(st)
                             if not isinstance(st, (ast.FunctionDef, ast.AsyncFunctionDef))] + [
                    d for st in node.body if isinstance(st, (ast.FunctionDef, ast.AsyncFunctionDef))
                    for d in st.decorator_list + st.args.defaults]
            s.loads = {n.id for e in now for n in ast.walk(e) if isinstance(n, ast.Name)}
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            s.kills = s.binds = {(a.asname or a.name).split('.')[0] for a in node.names if a.name != '*'}
        else:
            # Bound outright: an assignment's targets, and a for loop's or with's (a loop that runs no
            # time leaves the old value, which a shard without it reports as a NameError, not a pass).
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                for t in (node.targets if isinstance(node, ast.Assign) else [node.target]):
                    s.kills |= set(_targets(t))
            elif isinstance(node, ast.For):
                s.kills |= set(_targets(node.target))
            elif isinstance(node, ast.With):
                s.kills |= {n for item in node.items if item.optional_vars for n in _targets(item.optional_vars)}
            seen_nodes = [n for n, local in _visit(node) if not (isinstance(n, ast.Name) and n.id in local)]
            mine = [(n, local) for n, local in _visit(node)]
            s.binds = {n.id for n, local in mine if isinstance(n, ast.Name) and isinstance(n.ctx, (ast.Store, ast.Del))
                       and n.id not in local} | s.kills
            # Everything named, inside lambdas and nested defs too (they may run during the statement).
            named = {n.id for n, local in mine if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load) and n.id not in local}
            # What it reads from before it: a name whose first use, in running order, is a read. (One bound
            # in one branch and read after it counts as bound: a shard missing it fails loudly, by NameError.)
            first = {}
            for n, local in mine:
                if isinstance(n, ast.AugAssign) and isinstance(n.target, ast.Name):
                    first.setdefault(n.target.id, True)
                elif isinstance(n, ast.Name) and n.id not in local:
                    first.setdefault(n.id, isinstance(n.ctx, ast.Load))
            s.loads = {name for name, read in first.items() if read}
            s.mutates = set()
            for n, local in mine:
                s.mutates |= _changes([n], lambda name, local=local: name not in local, modules)
            calls = (named - s.binds) & defined_at.keys()  # a loop variable named like a helper is not it
            # A method call may run any class's methods, and what they call.
            if any(isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) for n in seen_nodes):
                calls |= method_reads & defined_at.keys()
                s.loads |= method_reads
                s.mutates |= method_changes
            r, c = expand(calls, i)
            s.loads |= r
            s.mutates |= c
            imported = modules - defined_at.keys()
            for n, local in mine:
                target = n.func.value if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) else (
                    n if isinstance(n, (ast.Attribute, ast.Subscript)) and isinstance(n.ctx, (ast.Store, ast.Del)) else None)
                if isinstance(target, (ast.Attribute, ast.Subscript)) and _base(target) in imported and not (
                        isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr in READS):
                    s.pinned = True  # sys.path.insert(...), os.environ[k] = v
            # A call may change what it is passed: lay(m, view) lays out m's rows.
            s.mutates |= {a.id for n, local in mine if isinstance(n, ast.Call)
                          for a in [*n.args, *(k.value for k in n.keywords)]
                          if isinstance(a, ast.Name) and a.id not in local and a.id not in modules}
            # Calling a bound object that is not a def (watch = FILTER.watch) may change it.
            s.mutates |= {n.func.id for n, local in mine if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                          and n.func.id in assigned and n.func.id not in defined_at and n.func.id not in local}
        for group in ('loads', 'kills', 'binds', 'mutates'):
            setattr(s, group, getattr(s, group) - OWNED)
        stmts.append(s)
    return stmts


def slice_of(stmts, start, stop, cheap=0.0):
    """Indexes of the statements before `start` that statements[start:stop] depend on, in order. With
    `cheap`, every earlier statement a profiled run timed under that many seconds is replayed too: small
    statements (CONFIG.clear(), a constant) are where state the names miss is most often set, and cost
    next to nothing."""
    needed, killed = set(), set()
    for s in stmts[start:stop]:
        needed |= s.loads - killed
        killed |= s.kills
    keep = []
    for s in reversed(stmts[:start]):
        if s.definition or s.pinned or (s.binds | s.mutates) & needed or 0 < s.cost < cheap:
            keep.append(s.index)
            needed -= s.kills
            needed |= s.loads
    return keep[::-1]


CHEAP = 0.002  # seconds: a statement a profile timed under this is replayed in every later shard


def load_profile():
    try:
        return json.loads(PROFILE.read_text())
    except (OSError, ValueError):
        return {}


def save_profile(update):
    profile = load_profile()
    profile.update(update)
    PROFILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = PROFILE.with_suffix(f'.{os.getpid()}')
    tmp.write_text(json.dumps(profile))
    tmp.replace(PROFILE)


def cost(stmts, profile):
    """Each statement's measured seconds (an unmeasured one: 10 ms; a definition: 0)."""
    for s in stmts:
        s.cost = profile.get(s.key, 0.0 if s.definition else 0.01)


def plan(stmts, shards):
    """Cut statements into `shards` contiguous ranges of about equal time, own plus replayed, each cut
    placed just after a passed() (a test's end) where the slice after it is cheapest. Returns
    [(start, stop, slice)]."""
    if shards <= 1:
        return [(0, len(stmts), [])]
    ends = [s.index + 1 for s in stmts if isinstance(s.node, ast.Expr) and ast.unparse(s.node) == 'passed()']
    prefix = [0.0]
    for s in stmts:
        prefix.append(prefix[-1] + s.cost)
    total = prefix[-1]

    def replay(c):
        return sum(stmts[k].cost for k in slice_of(stmts, c, min(len(stmts), c + 300), CHEAP))

    # Later shards replay more, so they get less of their own: aim each at the same own + replayed time.
    cuts = [0]
    for k in range(1, shards):
        want = total * k / shards
        near = [c for c in ends if abs(prefix[c] - want) < total / shards / 4] or [min(ends, key=lambda c: abs(prefix[c] - want))]
        cuts.append(min(near, key=lambda c: replay(c) + abs(prefix[c] - want) / 4))
    cuts = sorted(set(cuts)) + [len(stmts)]
    return [(a, b, slice_of(stmts, a, b, CHEAP)) for a, b in zip(cuts, cuts[1:])]


def memory_shards():
    """How many shards fit: MemAvailable less the reserve, over one shard's peak."""
    avail = 0
    with open('/proc/meminfo') as f:
        for line in f:
            if line.startswith('MemAvailable:'):
                avail = int(line.split()[1]) * 1024
    return max(1, min(8, os.cpu_count() or 1, int((avail - RESERVE) // SHARD_RSS)))


def no_core_dump():
    """Make this process leave no core dump. WSL pipes a crashed process's core to Windows' disk whatever
    RLIMIT_CORE says (a pipe ignores the limit; a 33 GB dump filled C: on 2026-11-16), so mark the process
    non-dumpable, which every core_pattern honours, and zero the limit for a file pattern too."""
    resource.setrlimit(resource.RLIMIT_CORE, (0, resource.getrlimit(resource.RLIMIT_CORE)[1]))
    ctypes.CDLL(None).prctl(4, 0, 0, 0, 0)  # PR_SET_DUMPABLE, 0


def child(test, build, start, stop, trace_file, result_file, whole):
    """Run statements[start:stop] in this process after their slice (or, with `whole`, after every statement
    before them, as a serial run would), and write the result to result_file."""
    faulthandler.enable()  # a crash in C (unicorn) prints the Python stack that reached it
    no_core_dump()
    source = pathlib.Path(test).read_text()
    stmts = analyse(source)
    cost(stmts, load_profile())
    keep = set(range(start)) if whole else set(slice_of(stmts, start, stop, CHEAP))
    run = [(s.index, s.line, s.key, compile(ast.Module(body=[s.node], type_ignores=[]), test, 'exec'))
           for s in stmts[:stop] if s.index >= start or s.index in keep]
    del stmts, source
    gc.collect()
    sys.argv = [test, build]
    sys.path[0] = str(pathlib.Path(test).parent)  # as when run as a script: tools/ is found from it
    os.environ.pop('Q2_TRACE', None)
    g = {'__file__': test, '__name__': '__main__', '__builtins__': __builtins__, 'TRACE': None, 'checks': 0}
    times, status, failed_at, blocked, started = {}, 0, None, None, False
    index = line = None
    try:
        for index, line, key, code in run:
            if index == start:  # the range's own count and trace start here
                started = True
                g['checks'] = 0
                g['TRACE'] = open(trace_file, 'w') if trace_file else None
            t = time.perf_counter()
            if index < start:
                try:
                    exec(code, g)
                except Exception as e:
                    # Nothing after a failed statement is a serial run's state: a failed call can leave unicorn
                    # running garbage, and its next translation can crash the process (temp_load, NULL;
                    # docs/building.md#fast-test-runs). Stop: the runner decides what the failure means.
                    print(f'(replayed line {line} raised {type(e).__name__}: {str(e)[:200]}; stopping)', flush=True)
                    status, blocked = 3, (index, line)
                    break
                continue
            exec(code, g)
            times[key] = round(time.perf_counter() - t, 4)
    except BaseException as e:  # what a whole run would print, at test/patch.py's own line numbers
        if not (isinstance(e, SystemExit) and not e.code):
            traceback.print_exc()
            status, failed_at = 1, (index, line)
    if g.get('TRACE'):
        g['TRACE'].close()
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024
    pathlib.Path(result_file).write_text(json.dumps({'checks': g.get('checks', 0) if started else 0, 'status': status, 'times': times,
                                                     'rss': rss, 'failed_at': failed_at, 'blocked': blocked}))
    sys.stdout.flush()
    os._exit(status)


def run_child(test, build, python, a, b, trace=None, whole=False):
    with tempfile.TemporaryDirectory(prefix='q2-shard-') as tmp:
        result = pathlib.Path(tmp)/'result.json'
        cmd = [python, __file__, '--child', str(a), str(b), '--result', str(result), '--test', test, build]
        if HEAVY.exists() and not os.environ.get('Q2_HEAVY_JOB'):  # each shard a q2-heavy job: its memory guard
            cmd = [str(HEAVY)] + cmd
        cmd += ['--trace', trace] if trace else []
        cmd += ['--whole'] if whole else []
        t = time.perf_counter()
        env = {**os.environ, 'Q2_HEAVY_RAMP': os.environ.get('Q2_HEAVY_RAMP', '3')}  # a shard's memory is known
        p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, cwd=ROOT, env=env)
        try:
            r = json.loads(result.read_text())
        except (OSError, ValueError):  # killed, or crashed in C: nothing written
            r = {'checks': 0, 'status': 1, 'times': {}, 'rss': 0, 'failed_at': None, 'blocked': None}
    r.update(output=p.stdout, returncode=p.returncode, seconds=time.perf_counter() - t)
    return r


def run_shards(test, build, ranges, python, stmts, trace_dir=None, log=print):
    """Run each (start, stop, slice) range in its own process, then settle what the slices could get wrong:

    - a shard whose replayed statement L failed: if L's own shard failed at or before L, a serial run stops
      there and never reaches this shard ("not reached"); if L passed in its own shard, the slice lacked
      something, and the shard runs again after the whole prefix;
    - a shard that failed in its own range runs again after the whole prefix, and that result stands, so
      a failure only the slice caused is never reported;
    - a run after the whole prefix that stops on an earlier statement which passed in its own shard means
      that shard's slice hid a failure: reported as a failure ("slice gap").

    Returns a list of result dicts, each with `state`: ok, failed, not-reached or slice-gap."""
    owner = {}
    for k, (a, b, _) in enumerate(ranges):
        owner.update(dict.fromkeys(range(a, b), k))

    def launch(k, whole):
        a, b, sl = ranges[k]
        trace = str(pathlib.Path(trace_dir)/f'{k}.trace') if trace_dir else None
        r = run_child(test, build, python, a, b, trace, whole)
        r['lines'], r['whole'] = (stmts[a].line, stmts[b - 1].end), whole
        what = 'after the whole prefix' if whole else f'{b - a} statements + {len(sl)} replayed'
        end = (f'stopped: replayed line {r["blocked"][1]} failed' if r['blocked'] else
               f'FAILED at line {r["failed_at"][1]}' if r['failed_at'] else
               f'CRASHED (exit {r["returncode"]})' if r['returncode'] else 'ok')
        log(f'shard {k}: lines {r["lines"][0]}-{r["lines"][1]}, {what}, {r["checks"]} checks, '
            f'{r["seconds"]:.0f} s, {r["rss"] / 1e9:.2f} GB, {end}')
        return r

    def passed_line(index):
        """Did the statement at index pass in its own shard (it ran, and its shard failed later or not)?"""
        if index not in owner:  # --lines: no shard ran it
            return None
        r = results[owner[index]]
        if r['blocked']:
            return False
        return not r['returncode'] or (r['failed_at'] is not None and r['failed_at'][0] > index)

    with concurrent.futures.ThreadPoolExecutor(len(ranges)) as pool:
        results = list(pool.map(lambda k: launch(k, False), range(len(ranges))))
        again = []
        for k, r in enumerate(results):
            if r['blocked']:
                own = passed_line(r['blocked'][0])
                r['state'] = 'again' if own else 'not-reached' if own is False else 'failed'
            else:
                r['state'] = 'ok' if not r['returncode'] else ('failed' if k == 0 else 'again')
            if r['state'] == 'again':
                again.append(k)
        if again:
            log(f'running shard{"s" * (len(again) > 1)} {", ".join(map(str, again))} again after the whole prefix')
        for k, r in zip(again, pool.map(lambda k: launch(k, True), again)):
            r['first_try'] = results[k]
            r['times'] = {**results[k]['times'], **r['times']}
            if r['blocked']:
                r['state'] = 'slice-gap' if passed_line(r['blocked'][0]) else 'not-reached'
            else:
                r['state'] = 'ok' if not r['returncode'] else 'failed'
            results[k] = r
    return results


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('build')
    ap.add_argument('-j', '--jobs', type=int, help='shards (default: by MemAvailable, see the docstring)')
    ap.add_argument('--test', default=str(ROOT/'test/patch.py'), help=argparse.SUPPRESS)
    ap.add_argument('--python', default=sys.executable, help='the interpreter for the shards (default: this one)')
    ap.add_argument('--lines', help='A:B[,C:D...]: only the statements that start on those lines, each range a shard')
    ap.add_argument('--verify', action='store_true', help='also run serially; the traces must match byte for byte')
    ap.add_argument('--plan', action='store_true', help='print the shards and their slices, run nothing')
    ap.add_argument('--child', nargs=2, type=int, help=argparse.SUPPRESS)
    ap.add_argument('--whole', action='store_true', help=argparse.SUPPRESS)
    ap.add_argument('--result', help=argparse.SUPPRESS)
    ap.add_argument('--trace', help=argparse.SUPPRESS)
    args = ap.parse_args()
    test = str(pathlib.Path(args.test).resolve())
    build = str(pathlib.Path(args.build).resolve())
    if args.child:
        start, stop = args.child
        child(test, build, start, stop, args.trace, args.result, args.whole)
    stmts = analyse(pathlib.Path(test).read_text())
    cost(stmts, load_profile())
    if args.lines:
        ranges = []
        for part in args.lines.split(','):
            a, b = (int(x) for x in part.split(':'))
            idx = [s.index for s in stmts if a <= s.line <= b]
            if not idx:
                raise SystemExit(f'no statement starts on lines {a}-{b}')
            ranges.append((idx[0], idx[-1] + 1, slice_of(stmts, idx[0], idx[-1] + 1, CHEAP)))
    else:
        ranges = plan(stmts, args.jobs or memory_shards())
    total = sum(s.cost for s in stmts) or 1
    for k, (a, b, sl) in enumerate(ranges):
        own = sum(s.cost for s in stmts[a:b])
        rep = sum(stmts[i].cost for i in sl)
        print(f'shard {k}: lines {stmts[a].line}-{stmts[b - 1].end}, {b - a} statements, {len(sl)} replayed, '
              f'estimated {own:.0f} s + {rep:.0f} s replayed ({100 * (own + rep) / total:.0f}% of serial)', flush=True)
    if args.plan:
        return 0
    t0 = time.perf_counter()
    trace_dir = tempfile.mkdtemp(prefix='q2-shard-traces-') if args.verify else None
    results = run_shards(test, build, ranges, args.python, stmts, trace_dir, log=lambda m: print(m, flush=True))
    times = {}
    for r in results:
        times.update(r['times'])
    if times:
        save_profile(times)
    failed = [k for k, r in enumerate(results) if r['state'] in ('failed', 'slice-gap')]
    for k in failed:
        print(f'\n--- shard {k} (lines {results[k]["lines"][0]}-{results[k]["lines"][1]}) output ---\n{results[k]["output"]}', end='')
    reached = [r for r in results if r['state'] != 'not-reached']
    checks = sum(r['checks'] for r in reached)
    wall = time.perf_counter() - t0
    status = 1 if failed else 0
    if args.verify:
        status |= verify(test, build, args.python, trace_dir, len(ranges))
    if failed:
        first = results[failed[0]]
        gap = ' (a slice gap: the line passes in its own shard, fails after the whole prefix)' if first['state'] == 'slice-gap' else ''
        at = first['blocked'] or first['failed_at']
        print(f'\nFAILED at line {at[1] if at else "?"} (shard {failed[0]}){gap}: the failure a serial run stops at. '
              f'{len(failed)} of {len(ranges)} shards failed; {len(results) - len(reached)} not reached, being after it. '
              f'{checks} checks passed before it. {wall:.0f} s wall')
    elif len(reached) < len(results):  # cannot happen without a failure; say so rather than pass
        print(f'FAILED: shards not reached without a failure to stop them ({wall:.0f} s wall)')
        status = 1
    elif not args.lines:
        print(f'{checks} MIPS execution scenarios passed; toolkit services mocked, stock lock filter executed. '
              f'({len(ranges)} shards, {wall:.0f} s wall)')
    else:
        print(f'{checks} checks passed on lines {args.lines} ({wall:.0f} s wall)')
    return status


def verify(test, build, python, trace_dir, n):
    """The serial run under Q2_TRACE; its trace must equal the shards' traces in order."""
    serial = pathlib.Path(trace_dir)/'serial.trace'
    t = time.perf_counter()
    p = subprocess.run([python, test, build], env={**os.environ, 'Q2_TRACE': str(serial)}, cwd=ROOT,
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    last = p.stdout.strip().splitlines()[-1] if p.stdout.strip() else ''
    print(f'serial run: exit {p.returncode}, {time.perf_counter() - t:.0f} s: {last}')
    parts = [pathlib.Path(trace_dir)/f'{k}.trace' for k in range(n)]
    joined = b''.join(f.read_bytes() for f in parts if f.exists())
    expected = serial.read_bytes()
    if p.returncode:  # a serial run stops at its failure: compare up to there
        joined = joined[:len(expected)]
    if joined == expected:
        print(f'verify: the shards\' traces equal the serial trace ({len(expected)} bytes, {expected.count(b"\n")} lines)')
        return 0
    a, b = joined.splitlines(), expected.splitlines()
    k = next((i for i, (x, y) in enumerate(zip(a, b)) if x != y), min(len(a), len(b)))
    print(f'verify: TRACES DIFFER at line {k + 1}: shards {a[k:k + 1]!r}, serial {b[k:k + 1]!r}; traces in {trace_dir}')
    return 1


if __name__ == '__main__':
    sys.exit(main())
