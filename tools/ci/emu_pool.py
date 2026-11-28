#!/usr/bin/env python3
"""Lend agents one of K shared q2-emu copies instead of each cloning its own.

    python3 tools/ci/emu_pool.py get BUILD [--as NAME] [--hours H]   # lease a copy, boot BUILD on it
    python3 tools/ci/emu_pool.py put SLOT                             # stop it, end the lease
    python3 tools/ci/emu_pool.py renew SLOT [--hours H]
    python3 tools/ci/emu_pool.py status
    python3 tools/ci/emu_pool.py fixture [DIR] [--library DB] [--data DDIR]  # (re)make the card and/or data fixture, hardlinked
    python3 tools/ci/emu_pool.py reserve SLOT [--off]                 # its lease never expires to another agent

`get` prints the slot, its folder and its viewer's URL; drive it with that folder's own q2emu
(`~/q2-emu-pool2/q2emu shot out.png`, `... key centre`; Q2EMU_PORT is set in its q2emu). A lease
lasts --hours (2) and is renewed with `renew`; an expired one is stopped and handed to the next `get`.
A lease asked for a build a copy already runs, by the same NAME, reuses it without a reboot.
Every lease's card (state/mmc, /mnt/mmc) starts as the fixture, ~/.cache/q2-emu-pool/card (Q2_EMU_CARD): `get` removes
whatever the last lease left there and hardlinks the fixture's tree in (copies it, about 6 MB, when the copy is on
another filesystem than the fixture), so a changed card never outlives its lease. With the library the fixture was scanned into (`fixture DIR --library DB`, kept as card.db beside
it), `get` copies that over the copy's state/data/database.db too, so Library > Songs lists the card's songs and no
other; a new fixture without one drops the old library, and the copy's database.db is then left as it was.
A fresh lease also starts with the settings of state/data (/mnt/data) the data fixture holds, ~/.cache/q2-emu-pool/data
(Q2_EMU_DATA; `fixture --data DDIR`, a clean default config.ini with BLUETOOTH=0): `get` copies its files over the
copy's and removes the per-run files (DATA_PER_RUN: the queue, play counts, ratings, Memory Playback, the state record,
the choice of q2ui as the UI and q2ui's options)
the last lease left; Wi-Fi, the pool's own files and the rest are kept. With no data fixture state/data is left alone.
A lease that is reused (same NAME and build, emulator running) and a lease already reserved keep their card, library and data.
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
import errno
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
# the songs a card must hold for the Songs list to have anything in it (tools/ci/smoke.py)
AUDIO = {'.flac', '.mp3', '.wav', '.ogg', '.m4a', '.ape', '.wma', '.aac', '.opus', '.dsf', '.dff'}
SKIP = {'rootfs', 'state'}  # Shanling's files and a copy's own state: never copied


def copy_dir(k):
    return HOME/f'q2-emu-pool{k}'


def port(k):
    return BASE_PORT + k


def coda_port(k):
    return CODA_RC_BASE + k


# run.sh's stand-in for /usr/bin/coda-rc, which demo starts with no arguments (patch/coda_rc_shim.c): the build's
# own daemon, copied beside the state, run with --port and --iroh-port. Put before run.sh's bwrap, whose arguments
# gain CODA. The copies share this machine's network, so each copy's q2-iroh port is its own too, Q2EMU_CODA_PORT +
# IROH_PORT_OFFSET: a daemon ends when its q2-iroh port is taken (patch/coda_rc.c main), and with every copy on
# 7721 only the first emulator's daemon ran.
CODA_BLOCK = '''# Car Remote (coda-rc) on this copy's own ports, Q2EMU_CODA_PORT and + 100 (tools/ci/emu_pool.py CODA_RC_BASE)
CODA=()
if [ -n "${Q2EMU_CODA_PORT:-}" ] && [ -e "$E/rootfs/usr/bin/coda-rc" ]; then
    cp -f "$E/rootfs/usr/bin/coda-rc" "$S/coda-rc.real"
    printf '%s\\n' '#!/bin/sh' "exec /emu/coda-rc.real --port $Q2EMU_CODA_PORT --iroh-port $((Q2EMU_CODA_PORT + 100)) \\"\\$@\\"" > "$S/coda-rc"; chmod +x "$S/coda-rc"
    CODA=(--ro-bind "$S/coda-rc" /usr/bin/coda-rc)
fi
'''
# And for q2-iroh, which demo starts beside coda-rc (docs/remote.md#car-remote-across-networks): its streams go to
# this copy's daemon, named on its q2-iroh port (--forward-named), and for a build whose q2-iroh predates that, as
# plain TCP to its Car Remote port (--forward), never to 7720 or 7721, where another emulator's (or the user's)
# daemon may listen. Its first argument (serve) stays first; the ones put after it are the ones it takes, being first.
IROH_BLOCK = '''# q2-iroh forwarding to this copy's coda-rc (tools/ci/emu_pool.py IROH_BLOCK)
IROH=()
if [ -n "${Q2EMU_CODA_PORT:-}" ] && [ -e "$E/rootfs/usr/bin/q2-iroh" ]; then
    cp -f "$E/rootfs/usr/bin/q2-iroh" "$S/q2-iroh.real"
    printf '%s\\n' '#!/bin/sh' 'c=$1; shift' "exec /emu/q2-iroh.real \\"\\$c\\" --forward 127.0.0.1:$Q2EMU_CODA_PORT --forward-named 127.0.0.1:$((Q2EMU_CODA_PORT + 100)) \\"\\$@\\"" > "$S/q2-iroh"; chmod +x "$S/q2-iroh"
    IROH=(--ro-bind "$S/q2-iroh" /usr/bin/q2-iroh)
fi
'''
# The blocks a copy fitted before each had its q2-iroh port has: fit puts the ones above in their place.
# run.sh's stand-in for /usr/bin/coda-rc, which demo starts with no arguments (patch/coda_rc_shim.c): the build's
# own daemon, copied beside the state, run with --port. Put before run.sh's bwrap, whose arguments gain CODA.
CODA_BLOCK_V1 = '''# Car Remote (coda-rc) on this copy's own port, Q2EMU_CODA_PORT (tools/ci/emu_pool.py CODA_RC_BASE)
CODA=()
if [ -n "${Q2EMU_CODA_PORT:-}" ] && [ -e "$E/rootfs/usr/bin/coda-rc" ]; then
    cp -f "$E/rootfs/usr/bin/coda-rc" "$S/coda-rc.real"
    printf '%s\\n' '#!/bin/sh' "exec /emu/coda-rc.real --port $Q2EMU_CODA_PORT \\"\\$@\\"" > "$S/coda-rc"; chmod +x "$S/coda-rc"
    CODA=(--ro-bind "$S/coda-rc" /usr/bin/coda-rc)
fi
'''
# And for q2-iroh, which demo starts beside coda-rc (docs/remote.md#car-remote-across-networks): its streams go to
# this copy's daemon, never to TCP 7720, where another emulator's (or the user's) daemon may listen. Its first
# argument (serve) stays first; the --forward put after it is the one it takes, being the first.
IROH_BLOCK_V1 = '''# q2-iroh forwarding to this copy's coda-rc (tools/ci/emu_pool.py IROH_BLOCK)
IROH=()
if [ -n "${Q2EMU_CODA_PORT:-}" ] && [ -e "$E/rootfs/usr/bin/q2-iroh" ]; then
    cp -f "$E/rootfs/usr/bin/q2-iroh" "$S/q2-iroh.real"
    printf '%s\\n' '#!/bin/sh' 'c=$1; shift' "exec /emu/q2-iroh.real \\"\\$c\\" --forward 127.0.0.1:$Q2EMU_CODA_PORT \\"\\$@\\"" > "$S/q2-iroh"; chmod +x "$S/q2-iroh"
    IROH=(--ro-bind "$S/q2-iroh" /usr/bin/q2-iroh)
fi
'''
RUN_BEFORE = '# Q2EMU_CORE=1: qemu writes'
RUN_BWRAP = '    "${BOOT[@]}"'


def fit(d, k):
    """Copy k's scripts given its Car Remote port (CODA_RC_BASE + k): q2emu exports it, run.sh passes it on to
    coda-rc and q2-iroh; made once, on a copy made before this existed too."""
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
    for was, now in ((CODA_BLOCK_V1, CODA_BLOCK), (IROH_BLOCK_V1, IROH_BLOCK)):
        if was in run:  # fitted before each copy had its own q2-iroh port
            run = run.replace(was, now)
            (d/'run.sh').write_text(run)
    if 'IROH=()' not in run:  # a copy fitted before q2-iroh too
        coda = '    "${CODA[@]}" "${BOOT[@]}"'
        assert run.count(RUN_BEFORE) == 1 and run.count(coda) == 1, 'run.sh has changed: update IROH_BLOCK'
        run = run.replace(RUN_BEFORE, IROH_BLOCK + RUN_BEFORE).replace(coda, '    "${CODA[@]}" "${IROH[@]}" "${BOOT[@]}"')
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


def fixture_dir():
    """The card every lease starts from: Q2_EMU_CARD, else ~/.cache/q2-emu-pool/card."""
    return pathlib.Path(os.environ.get('Q2_EMU_CARD') or STATE/'card')


def link_or_copy(src, dst):
    """dst as a hardlink of src, or a copy of it (mode kept, so a read-only fixture file stays read-only) when the
    two are on different filesystems: the pool copies can live on another disk than ~/.cache through symlinks
    (~/q2-emu-poolK -> /mnt/wsl/q2scratch/..., docs/building.md#emulator-pool), where a link fails with EXDEV."""
    try:
        os.link(src, dst)
    except OSError as e:
        if e.errno != errno.EXDEV:
            raise SystemExit(f'cannot hardlink {src} to {dst}: {e.strerror}') from None
        shutil.copy2(src, dst)


def link_tree(src, dst):
    """dst made to hold src's tree: directories new, files hardlinked (copied across filesystems), symlinks kept."""
    dst.mkdir()
    for entry in sorted(src.iterdir()):
        to = dst/entry.name
        if entry.is_symlink():
            to.symlink_to(os.readlink(entry))
        elif entry.is_dir():
            link_tree(entry, to)
        else:
            link_or_copy(entry, to)


def library_fixture():
    """Stock's library (/mnt/data/database.db) as a scan of the card fixture left it: card.db beside the card."""
    fx = fixture_dir()
    return fx.with_name(fx.name + '.db')


def make_fixture(src, library=None):
    """The card fixture as hardlinks of src's tree, its files read-only so that a lease writing a file in place
    fails instead of changing every card; the tree it replaces is unlinked, never written to. library: a
    database.db that lists src's songs (Scan Music Now on a lease of it), kept as library_fixture(); without one
    the old library goes, as it lists another card."""
    fx = fixture_dir()
    src = pathlib.Path(src)
    if not src.is_dir():
        raise SystemExit(f'{src} is not a directory')
    fx.parent.mkdir(parents=True, exist_ok=True)
    new = fx.with_name(fx.name + '.new')
    if new.exists():
        shutil.rmtree(new)
    link_tree(src, new)
    for root, _, files in os.walk(new):
        for f in files:
            p = pathlib.Path(root)/f
            if not p.is_symlink():
                p.chmod(p.stat().st_mode & ~0o222)
    if fx.exists():
        shutil.rmtree(fx)
    new.rename(fx)
    lib = library_fixture()
    if library:
        shutil.copyfile(library, lib.with_name(lib.name + '.new'))
        lib.with_name(lib.name + '.new').rename(lib)
    else:
        lib.unlink(missing_ok=True)
    return fx


# state/data's files that are one run's own state, not settings or something the copy needs: removed at each fresh
# lease. Everything else there stays: wpa_supplicant.conf and q2-defaults (boot and Wi-Fi), q2-battery, coda-rc (the
# pool's stand-in), userequalizer and the logs; database.db is the library fixture's (reset_card).
# ui-target and q2ui's files: a lease that chose q2ui as the UI (docs/ui-rewrite.md#cutover) must not choose it for the next
DATA_PER_RUN = ('memoryfile', 'q2-state', 'q2-stall', 'q2-cf-trace', 'q2-library-scope', 'peq-active', 'ringnav-*',
                'ui-target', 'q2ui.args', 'q2ui-stats.json')


def data_fixture_dir():
    """The settings every fresh lease starts from: Q2_EMU_DATA, else ~/.cache/q2-emu-pool/data."""
    return pathlib.Path(os.environ.get('Q2_EMU_DATA') or STATE/'data')


def make_data_fixture(src):
    """The data fixture as hardlinks of src's files (top level only; database.db is the library fixture's, not
    here), read-only; the tree it replaces is unlinked, never written to."""
    fx = data_fixture_dir()
    src = pathlib.Path(src)
    if not src.is_dir():
        raise SystemExit(f'{src} is not a directory')
    fx.parent.mkdir(parents=True, exist_ok=True)
    new = fx.with_name(fx.name + '.new')
    if new.exists():
        shutil.rmtree(new)
    new.mkdir()
    for f in sorted(src.iterdir()):
        if f.is_file() and f.name != 'database.db':
            link_or_copy(f, new/f.name)
            (new/f.name).chmod((new/f.name).stat().st_mode & ~0o222)
    if not any(new.iterdir()):
        shutil.rmtree(new)
        raise SystemExit(f'{src} holds no files to make a data fixture from')
    if fx.exists():
        shutil.rmtree(fx)
    new.rename(fx)
    return fx


def reset_data(k):
    """Copy k's state/data given the data fixture's settings: DATA_PER_RUN's files removed, each of the fixture's files
    copied over (not linked: stock writes config.ini in place, which would change the fixture for every lease).
    Called for a copy this lease holds, its emulator stopped; with no data fixture, nothing changes."""
    fx = data_fixture_dir()
    if not fx.is_dir():
        return
    data = copy_dir(k)/'state/data'
    data.mkdir(parents=True, exist_ok=True)
    for pattern in DATA_PER_RUN:
        for f in data.glob(pattern):
            if f.is_file() or f.is_symlink():
                f.unlink()
    for f in sorted(fx.iterdir()):
        if f.is_file():
            tmp = data/(f.name + '.new')
            shutil.copyfile(f, tmp)
            tmp.chmod(0o644)
            tmp.replace(data/f.name)


def card_songs(k):
    """How many audio files copy k's card holds."""
    return sum(1 for _, _, files in os.walk(copy_dir(k)/'state/mmc') for f in files if pathlib.Path(f).suffix.lower() in AUDIO)


def reset_card(k):
    """Copy k's card (state/mmc) put back to the fixture: whatever the last lease added, switched or removed is
    unlinked and the fixture's tree hardlinked (or, across filesystems, copied) in, and its library (state/data/database.db) a copy of the
    fixture's, when there is one: stock's database.db is not rescanned at boot, so one left by another card lists
    songs this card does not hold. Called for a copy this lease holds, its emulator stopped; the rest of
    state/data (/mnt/data) is reset_data's."""
    fx = fixture_dir()
    if not fx.is_dir() or not any(fx.iterdir()):
        raise SystemExit(f'no card fixture at {fx}: make it with `python3 {pathlib.Path(__file__).resolve()} fixture DIR` '
                         f'(DIR: a card to start every lease from; its files are hardlinked, or copied across filesystems)')
    card = copy_dir(k)/'state/mmc'
    if card.is_symlink() or card.is_file():
        card.unlink()
    elif card.exists():
        shutil.rmtree(card)
    card.parent.mkdir(parents=True, exist_ok=True)
    link_tree(fx, card)
    lib = library_fixture()
    if lib.is_file():  # copied, not linked: stock writes its library (play counts, Recent, rescans)
        db = copy_dir(k)/'state/data/database.db'
        db.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(lib, db.with_name(db.name + '.new'))
        db.with_name(db.name + '.new').rename(db)


def q2emu(k, *args):
    return subprocess.run([str(copy_dir(k)/'q2emu'), *args], capture_output=True, text=True,
                          env={**os.environ, 'Q2EMU_PORT': str(port(k)), 'Q2EMU_CODA_PORT': str(coda_port(k))})


def expired(lease):
    return lease['until'] < time.time()


def reclaimable(lease):
    """An expired lease that isn't reserved: its copy may go to the next `get`."""
    return expired(lease) and not lease.get('reserved')


def get(build, name, hours, wait, reserve=False, avoid=(), prepare=None):
    """Lease a copy and boot build on it; its slot. avoid: slots never taken (tools/ci/smoke.py keeps off the
    user's slot 2 even when it is free). prepare(slot): called before the boot, on the copy's state."""
    build = str(pathlib.Path(build).resolve())
    said = False
    while True:
        with Locked() as t:
            for k in map(str, range(1, K + 1)):  # an expired lease: stop its emulator, free the copy
                if k in t.leases and reclaimable(t.leases[k]):
                    q2emu(int(k), 'stop')
                    del t.leases[k]
            mine = [k for k, v in t.leases.items() if v['name'] == name and v['build'] == build and int(k) not in avoid]
            free = [k for k in map(str, range(1, K + 1)) if k not in t.leases and int(k) not in avoid]
            k = (mine or free or [None])[0]
            if k is not None:
                was_reserved = bool(t.leases.get(k, {}).get('reserved'))  # the user's rig: its card is its own
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
        try:
            if not was_reserved:
                q2emu(k, 'stop')  # the card changes under no running emulator
                reset_card(k)
                reset_data(k)
            if prepare:
                prepare(k)
            p = q2emu(k, 'start', build)
        except BaseException:  # a signal mid-boot too: nothing left running unleased
            q2emu(k, 'stop')
            with Locked() as t:
                t.leases.pop(str(k), None)
            raise
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
    f = sub.add_parser('fixture', help='make the card and/or the data every lease starts from, hardlinked from DIR / DDIR')
    f.add_argument('dir', nargs='?', help='the card (omit it to change only --data)')
    f.add_argument('--data', metavar='DDIR', help="the settings (config.ini, ...) each fresh lease puts in state/data; seed it from a build's default config with BLUETOOTH=0")
    f.add_argument('--library', help="a database.db that lists DIR's songs, copied to each lease's state/data")
    sub.add_parser('status')
    args = ap.parse_args()
    if args.cmd == 'get':
        get(args.build, args.name, args.hours, args.wait, args.reserve)
    elif args.cmd == 'put':
        put(args.slot)
    elif args.cmd == 'reserve':
        reserve(args.slot, not args.off)
    elif args.cmd == 'fixture':
        if not args.dir and not args.data:
            raise SystemExit('fixture needs a card DIR, --data DDIR, or both')
        if args.data:
            print(f'data fixture {make_data_fixture(args.data)}: {", ".join(sorted(p.name for p in data_fixture_dir().iterdir()))}')
        if args.dir:
            print(f'card fixture {make_fixture(args.dir, args.library)}: {sum(len(f) for _, _, f in os.walk(fixture_dir()))} files, '
                  f'hardlinked from {args.dir}; library {library_fixture() if args.library else "none"}')
    elif args.cmd == 'renew':
        renew(args.slot, args.hours)
    else:
        status()
    return 0


if __name__ == '__main__':
    sys.exit(main())
