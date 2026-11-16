#!/usr/bin/env python3
"""Lend agents one of K shared q2-emu copies instead of each cloning its own.

    python3 tools/ci/emu_pool.py get BUILD [--as NAME] [--hours H]   # lease a copy, boot BUILD on it
    python3 tools/ci/emu_pool.py put SLOT                             # stop it, end the lease
    python3 tools/ci/emu_pool.py renew SLOT [--hours H]
    python3 tools/ci/emu_pool.py status

`get` prints the slot, its folder and its viewer's URL; drive it with that folder's own q2emu
(`~/q2-emu-pool2/q2emu shot out.png`, `... key centre`; Q2EMU_PORT is set in its q2emu). A lease
lasts --hours (2) and is renewed with `renew`; an expired one is stopped and handed to the next `get`.
A lease asked for a build a copy already runs, by the same NAME, reuses it without a reboot.

The copies are ~/q2-emu-pool1 .. ~/q2-emu-poolK (K = Q2_EMU_POOL, 4), made on first use from
~/q2-emu-server's scripts and serial record (state/mtd5.bin; never its rootfs/ or the rest of state/,
and nothing there is changed), each with its
own port, 2401 up. Each emulator is a QEMU user-mode demo of 200-400 MB; 14 of them and builds took the
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
SKIP = {'rootfs', 'state'}  # Shanling's files and a copy's own state: never copied


def copy_dir(k):
    return HOME/f'q2-emu-pool{k}'


def port(k):
    return BASE_PORT + k


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
    return d


def q2emu(k, *args):
    return subprocess.run([str(copy_dir(k)/'q2emu'), *args], capture_output=True, text=True,
                          env={**os.environ, 'Q2EMU_PORT': str(port(k))})


def expired(lease):
    return lease['until'] < time.time()


def get(build, name, hours, wait):
    build = str(pathlib.Path(build).resolve())
    said = False
    while True:
        with Locked() as t:
            for k in map(str, range(1, K + 1)):  # an expired lease: stop its emulator, free the copy
                if k in t.leases and expired(t.leases[k]):
                    q2emu(int(k), 'stop')
                    del t.leases[k]
            mine = [k for k, v in t.leases.items() if v['name'] == name and v['build'] == build]
            free = [k for k in map(str, range(1, K + 1)) if k not in t.leases]
            k = (mine or free or [None])[0]
            if k is not None:
                t.leases[k] = {'name': name, 'build': build, 'since': time.time(), 'until': time.time() + hours * 3600}
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
    print(f'slot {k}: {copy_dir(k)}, http://127.0.0.1:{port(k)}/, {build}, leased to {name} for {hours} h')
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


def status():
    with Locked() as t:
        for k in range(1, K + 1):
            lease = t.leases.get(str(k))
            run = q2emu(k, 'status').stdout.strip() if (copy_dir(k)/'q2emu').exists() else 'not made'
            if lease:
                left = (lease['until'] - time.time()) / 60
                print(f'{k}: {run}, port {port(k)}, {lease["name"]}, {lease["build"]}, '
                      + (f'{left:.0f} min left' if left > 0 else 'expired'))
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
    p = sub.add_parser('put')
    p.add_argument('slot', type=int)
    r = sub.add_parser('renew')
    r.add_argument('slot', type=int)
    r.add_argument('--hours', type=float, default=2)
    sub.add_parser('status')
    args = ap.parse_args()
    if args.cmd == 'get':
        get(args.build, args.name, args.hours, args.wait)
    elif args.cmd == 'put':
        put(args.slot)
    elif args.cmd == 'renew':
        renew(args.slot, args.hours)
    else:
        status()
    return 0


if __name__ == '__main__':
    sys.exit(main())
