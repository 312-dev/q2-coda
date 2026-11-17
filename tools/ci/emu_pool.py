#!/usr/bin/env python3
"""Lend agents one of K shared q2-emu copies instead of each cloning its own.

    python3 tools/ci/emu_pool.py get BUILD [--as NAME] [--hours H]   # lease a copy, boot BUILD on it
    python3 tools/ci/emu_pool.py put SLOT                             # stop it, end the lease
    python3 tools/ci/emu_pool.py renew SLOT [--hours H]
    python3 tools/ci/emu_pool.py status
    python3 tools/ci/emu_pool.py reserve SLOT [--off]                 # its lease never expires to another agent

`get` prints the slot, its folder and its viewer's URL; drive it with that folder's own q2emu
(`~/q2-emu-pool2/q2emu shot out.png`, `... key centre`; Q2EMU_PORT is set in its q2emu). A lease
lasts --hours (2) and is renewed with `renew`; an expired one is stopped and handed to the next `get`.
A lease asked for a build a copy already runs, by the same NAME, reuses it without a reboot.
A reserved lease (`get --reserve`, or `reserve SLOT` on a held one: the user's own rig) is never
reclaimed when it expires and never handed to another NAME; only `put` or `reserve --off` frees it.

The copies are ~/q2-emu-pool1 .. ~/q2-emu-poolK (K = Q2_EMU_POOL, 4), made on first use from
~/q2-emu-server's scripts and serial record (state/mtd5.bin; never its rootfs/ or the rest of state/,
and nothing there is changed), each with its
own port, 2401 up, and its Car Remote daemon its own, 7701 up (CODA_RC_BASE, fit). Each emulator is a QEMU user-mode demo of 200-400 MB; 14 of them and builds took the
laptop down on 2026-11-16, so `get` waits when every copy is leased (--wait, default) or fails (--no-wait).
Leases live in ~/.cache/q2-emu-pool/leases.json, changed under a lock. docs/building.md#emulator-pool.
"""
import argparse
import fcntl
import json
import os
import pathlib
import shutil
import subprocess
import sys
import time

HOME = pathlib.Path.home()
SOURCE = HOME/'q2-emu-server'
K = int(os.environ.get('Q2_EMU_POOL', '4'))
STATE = HOME/'.cache/q2-emu-pool'
LEASES = STATE/'leases.json'
BASE_PORT = 2400
# Car Remote's daemon in copy k listens on CODA_RC_BASE + k (7701 up), not the Q2's TCP 7720, so copies
# running side by side never fight over one port (docs/building.md#emulator-pool).
CODA_RC_BASE = 7700
SKIP = {'rootfs', 'state'}  # Shanling's files and a copy's own state: never copied


def copy_dir(k):
    return HOME/f'q2-emu-pool{k}'


def port(k):
    return BASE_PORT + k


def coda_port(k):
    return CODA_RC_BASE + k


# run.sh's stand-in for /usr/bin/coda-rc, which demo starts with no arguments (patch/coda_rc_shim.c): the build's
# own daemon, copied beside the state, run with --port. Put before run.sh's bwrap, whose arguments gain CODA.
CODA_BLOCK = '''# Car Remote (coda-rc) on this copy's own port, Q2EMU_CODA_PORT (tools/ci/emu_pool.py CODA_RC_BASE)
CODA=()
if [ -n "${Q2EMU_CODA_PORT:-}" ] && [ -e "$E/rootfs/usr/bin/coda-rc" ]; then
    cp -f "$E/rootfs/usr/bin/coda-rc" "$S/coda-rc.real"
    printf '%s\\n' '#!/bin/sh' "exec /emu/coda-rc.real --port $Q2EMU_CODA_PORT \\"\\$@\\"" > "$S/coda-rc"; chmod +x "$S/coda-rc"
    CODA=(--ro-bind "$S/coda-rc" /usr/bin/coda-rc)
fi
'''
RUN_BEFORE = '# Q2EMU_CORE=1: qemu writes'
RUN_BWRAP = '    "${BOOT[@]}"'


def fit(d, k):
    """Copy k's scripts given its Car Remote port (CODA_RC_BASE + k): q2emu exports it, run.sh passes it on;
    made once, on a copy made before this existed too."""
    script = (d/'q2emu').read_text()
    if 'Q2EMU_CODA_PORT' not in script:
        line = f'PORT=${{Q2EMU_PORT:-{port(k)}}}\n'
        assert line in script, 'q2emu has no PORT line to follow'
        (d/'q2emu').write_text(script.replace(line, line + f'export Q2EMU_CODA_PORT=${{Q2EMU_CODA_PORT:-{coda_port(k)}}}\n', 1))
    run = (d/'run.sh').read_text()
    if 'Q2EMU_CODA_PORT' not in run:
        assert run.count(RUN_BEFORE) == 1 and run.count(RUN_BWRAP) == 1, 'run.sh has changed: update CODA_BLOCK'
        run = run.replace(RUN_BEFORE, CODA_BLOCK + RUN_BEFORE).replace(RUN_BWRAP, '    "${CODA[@]}" "${BOOT[@]}"')
        (d/'run.sh').write_text(run)


class Locked:
    """The lease table, read and written under an exclusive lock."""
    def __enter__(self):
        STATE.mkdir(parents=True, exist_ok=True)
        self.lock = open(STATE/'lock', 'w')
        fcntl.flock(self.lock, fcntl.LOCK_EX)
        try:
            self.leases = json.loads(LEASES.read_text())
        except (OSError, ValueError):
            self.leases = {}
        return self

    def __exit__(self, *exc):
        if exc[0] is None:
            tmp = LEASES.with_suffix('.tmp')
            tmp.write_text(json.dumps(self.leases, indent=1))
            tmp.replace(LEASES)
        self.lock.close()


def make_copy(k):
    """The pool copy k: q2-emu's scripts, its default port set to the copy's own."""
    d = copy_dir(k)
    if (d/'q2emu').exists():
        fit(d, k)
        return d
    tmp = d.with_name(d.name + '.new')
    if tmp.exists():
        shutil.rmtree(tmp)
    shutil.copytree(SOURCE, tmp, symlinks=True, ignore=lambda where, names: SKIP & set(names) if pathlib.Path(where) == SOURCE else set())
    script = (tmp/'q2emu').read_text()
    script = script.replace('PORT=${Q2EMU_PORT:-2309}', f'PORT=${{Q2EMU_PORT:-{port(k)}}}')
    assert f'{port(k)}' in script, 'q2emu has no PORT=${Q2EMU_PORT:-...} line to set'
    (tmp/'q2emu').write_text(script)
    record = SOURCE/'state/mtd5.bin'  # the serial record authrecord.sh makes; its run boots /tmp/q2-dev only
    if record.exists():
        (tmp/'state').mkdir()
        shutil.copy2(record, tmp/'state/mtd5.bin')
    tmp.rename(d)
    fit(d, k)
    return d


def q2emu(k, *args):
    return subprocess.run([str(copy_dir(k)/'q2emu'), *args], capture_output=True, text=True,
                          env={**os.environ, 'Q2EMU_PORT': str(port(k)), 'Q2EMU_CODA_PORT': str(coda_port(k))})


def expired(lease):
    return lease['until'] < time.time()


def reclaimable(lease):
    """An expired lease that isn't reserved: its copy may go to the next `get`."""
    return expired(lease) and not lease.get('reserved')


def get(build, name, hours, wait, reserve=False):
    build = str(pathlib.Path(build).resolve())
    said = False
    while True:
        with Locked() as t:
            for k in map(str, range(1, K + 1)):  # an expired lease: stop its emulator, free the copy
                if k in t.leases and reclaimable(t.leases[k]):
                    q2emu(int(k), 'stop')
                    del t.leases[k]
            mine = [k for k, v in t.leases.items() if v['name'] == name and v['build'] == build]
            free = [k for k in map(str, range(1, K + 1)) if k not in t.leases]
            k = (mine or free or [None])[0]
            if k is not None:
                t.leases[k] = {'name': name, 'build': build, 'since': time.time(), 'until': time.time() + hours * 3600,
                               **({'reserved': True} if reserve or t.leases.get(k, {}).get('reserved') else {})}
                break
        if not wait:
            raise SystemExit(f'all {K} pool emulators are leased; `status` says by whom')
        if not said:
            print(f'all {K} pool emulators are leased; waiting', file=sys.stderr, flush=True)
            said = True
        time.sleep(10)
    k = int(k)
    make_copy(k)
    if not (mine and q2emu(k, 'status').stdout.strip() == 'running'):
        p = q2emu(k, 'start', build)
        if p.returncode:
            with Locked() as t:
                t.leases.pop(str(k), None)
            raise SystemExit(f'pool emulator {k} did not start:\n{p.stdout}{p.stderr}')
    print(f'slot {k}: {copy_dir(k)}, http://127.0.0.1:{port(k)}/, {build}, leased to {name} for {hours} h; '
          f'Car Remote on TCP {coda_port(k)}')
    print(f'  drive it: {copy_dir(k)}/q2emu shot out.png | key centre | wheel 3 | tap X Y')
    print(f'  give it back: python3 {pathlib.Path(__file__).resolve()} put {k}')
    return k


def put(k):
    with Locked() as t:
        q2emu(int(k), 'stop')
        t.leases.pop(str(k), None)
    print(f'slot {k}: stopped and free')


def renew(k, hours):
    with Locked() as t:
        if str(k) not in t.leases:
            raise SystemExit(f'slot {k} is not leased')
        t.leases[str(k)]['until'] = time.time() + hours * 3600
    print(f'slot {k}: leased for {hours} h more')


def reserve(k, on=True):
    with Locked() as t:
        if str(k) not in t.leases:
            raise SystemExit(f'slot {k} is not leased')
        if on:
            t.leases[str(k)]['reserved'] = True
        else:
            t.leases[str(k)].pop('reserved', None)
    print(f'slot {k}: ' + ('reserved: never reclaimed or handed to another agent' if on else 'no longer reserved'))


def status():
    with Locked() as t:
        for k in range(1, K + 1):
            lease = t.leases.get(str(k))
            run = q2emu(k, 'status').stdout.strip() if (copy_dir(k)/'q2emu').exists() else 'not made'
            if lease:
                left = (lease['until'] - time.time()) / 60
                print(f'{k}: {run}, port {port(k)}, {lease["name"]}, {lease["build"]}, '
                      + (f'{left:.0f} min left' if left > 0 else 'expired') + (', RESERVED' if lease.get('reserved') else ''))
            else:
                print(f'{k}: {run}, port {port(k)}, free')


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)
    g = sub.add_parser('get')
    g.add_argument('build')
    g.add_argument('--as', dest='name', default=os.environ.get('Q2_AGENT', pathlib.Path.cwd().name),
                   help='who holds it (default: $Q2_AGENT, else this folder\'s name)')
    g.add_argument('--hours', type=float, default=2)
    g.add_argument('--no-wait', dest='wait', action='store_false')
    g.add_argument('--reserve', action='store_true', help='never reclaimed on expiry nor handed to another name')
    p = sub.add_parser('put')
    p.add_argument('slot', type=int)
    r = sub.add_parser('renew')
    r.add_argument('slot', type=int)
    r.add_argument('--hours', type=float, default=2)
    v = sub.add_parser('reserve')
    v.add_argument('slot', type=int)
    v.add_argument('--off', action='store_true')
    sub.add_parser('status')
    args = ap.parse_args()
    if args.cmd == 'get':
        get(args.build, args.name, args.hours, args.wait, args.reserve)
    elif args.cmd == 'put':
        put(args.slot)
    elif args.cmd == 'reserve':
        reserve(args.slot, not args.off)
    elif args.cmd == 'renew':
        renew(args.slot, args.hours)
    else:
        status()
    return 0


if __name__ == '__main__':
    sys.exit(main())
