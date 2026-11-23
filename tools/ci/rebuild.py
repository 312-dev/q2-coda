#!/usr/bin/env python3
"""Build this worktree into one reused place, fast, and print the build's path.

    python3 tools/ci/rebuild.py [--dev] [--out DIR] [-- tools/build.py's other options]
    B=$(python3 tools/ci/rebuild.py --dev | tail -1); python3 tools/ci/patch_shards.py "$B"

What it adds to tools/build.py, which it runs:
- one place per worktree, ~/.cache/q2-builds/<worktree>[-dev]/, holding this build and the one before it
  (a test still reading that one keeps it) and a `cur` link to the newest, not a new bN each run: two
  builds' disk, not dozens. The path printed is the build's own directory, never the link;
- nothing at all when cur was built from the same sources (tools/build.py's source_sha256, and the iroh
  crate's, tools/iroh.py's crate_sha256) and options: a test of an unchanged tree reuses it;
- ccache in front of clang and cc (/usr/lib/ccache, base_dir $HOME, hash_dir off; the build runs in one
  fixed directory and is renamed after, as the include path is hashed). A --dev build's number is in
  stock.h, which most sources include, so ccache pays off on a release build, not a --dev one;
- Q2_BUILD_CACHE for tools/build.py's font work (`memo`), which pays off on every build;
- the stock files every build writes unchanged (stock.squashfs, stock-demo, libc-2.28.so) hardlinked to
  one copy in ~/.cache/q2-builds/stock/, and root.pseudo, the build's 108 MB scratch, dropped.

tools/build.py's own output is shown only when it fails, but for its `payload room:` line, the bytes
free below SCRATCH and in the scratch state (docs/architecture.md#the-payload).

A release does not use this: tools/release.py's two reproducibility builds run tools/build.py bare, with
no caches (docs/building.md#fast-test-runs).
"""
import argparse
import hashlib
import json
import os
import pathlib
import shutil
import subprocess
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[2]
ZIP = pathlib.Path(os.environ.get('Q2_STOCK_ZIP', pathlib.Path.home()/'q2-stock/Q2 Firmware V1.36.zip'))
BUILDS = pathlib.Path.home()/'.cache/q2-builds'
STOCK_FILES = ('stock.squashfs', 'stock-demo', 'libc-2.28.so')


def source_sha256():
    sys.path.insert(0, str(ROOT/'tools'))
    import build  # noqa: E402  (tools/build.py: the hash test/patch.py checks a build against)
    return build.source_sha256()


def crate_sha256():
    """remote/iroh-sidecar's sources (tools/iroh.py), outside source_sha256: --build-iroh compiles them, and
    the pinned path stops a build whose crate is not PIN's, so neither may reuse an image made from another."""
    sys.path.insert(0, str(ROOT/'tools'))
    import iroh  # noqa: E402
    return iroh.crate_sha256()


def dedupe(out):
    """Hardlink the stock files to the shared copy (made from this build's when there is none yet)."""
    store = BUILDS/'stock'
    store.mkdir(parents=True, exist_ok=True)
    saved = 0
    for name in STOCK_FILES:
        f = out/name
        if not f.exists():
            continue
        digest = hashlib.sha256(f.read_bytes()).hexdigest()
        shared = store/f'{digest}-{name}'
        if not shared.exists():
            os.link(f, shared)
        elif not os.path.samefile(f, shared):
            tmp = f.with_suffix('.link')
            os.link(shared, tmp)
            tmp.replace(f)
            saved += shared.stat().st_size
    pseudo = out/'root.pseudo'
    if pseudo.exists():
        saved += pseudo.stat().st_size
        pseudo.unlink()
    return saved


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--dev', action='store_true')
    ap.add_argument('--out', type=pathlib.Path, help='default: ~/.cache/q2-builds/<worktree>[-dev]')
    ap.add_argument('--force', action='store_true', help='build even when cur matches the sources')
    ap.add_argument('--python', default=sys.executable)
    args, rest = ap.parse_known_args()
    rest = [a for a in rest if a != '--']
    place = args.out or BUILDS/f'{ROOT.name}{"-dev" * args.dev}'
    link = place/'cur'
    cur = link.resolve() if link.is_symlink() else link
    new = place/f'b{time.strftime("%Y%m%d-%H%M%S")}'
    options = [*(['--dev'] if args.dev else []), *rest]
    key = {'source_sha256': source_sha256(), 'crate_sha256': crate_sha256(), 'options': options}
    stamp = cur/'rebuild.json'
    try:
        old = json.loads(stamp.read_text())
    except (OSError, ValueError):
        old = None
    if not args.force and old == key and (cur/'update.tar').exists():
        print(f'unchanged since {cur} was built: reused', file=sys.stderr)
        print(cur)
        return 0
    place.mkdir(parents=True, exist_ok=True)
    # Built in one fixed place, then renamed: ccache hashes the include path (-I OUT), so a new directory
    # each time would miss every object.
    building = place/'building'
    if building.exists():
        shutil.rmtree(building)  # a build this tool started and did not finish
    env = dict(os.environ)
    ccache = pathlib.Path('/usr/lib/ccache')
    if ccache.is_dir() and shutil.which('ccache'):
        env['PATH'] = f'{ccache}:{env["PATH"]}'
        env.setdefault('CCACHE_BASEDIR', str(pathlib.Path.home()))
        env.setdefault('CCACHE_NOHASHDIR', '1')
    env.setdefault('Q2_BUILD_CACHE', str(BUILDS/'memo'))
    t = time.perf_counter()
    cmd = [args.python, str(ROOT/'tools/build.py'), str(ZIP), '--out', str(building), *options]
    p = subprocess.run(cmd, cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    if p.returncode:
        sys.stderr.write(p.stdout)
        print(f'build failed (exit {p.returncode}); {cur if cur.exists() else "no build"} left as it was', file=sys.stderr)
        return p.returncode
    building.rename(new)
    saved = dedupe(new)
    (new/'rebuild.json').write_text(json.dumps(key))
    tmp = place/'cur.new'
    tmp.unlink(missing_ok=True)
    tmp.symlink_to(new.name)
    tmp.replace(link)
    for old_build in [b for b in place.glob('b*') if b not in (new, cur)]:
        shutil.rmtree(old_build)  # all but this build and the one before it
    for line in p.stdout.splitlines():
        if line.startswith('payload room: '):  # tools/build.py check_layout's headroom
            print(line, file=sys.stderr)
    print(f'built in {time.perf_counter() - t:.0f} s; {saved / 1e6:.0f} MB shared or dropped', file=sys.stderr)
    print(new)
    return 0


if __name__ == '__main__':
    sys.exit(main())
