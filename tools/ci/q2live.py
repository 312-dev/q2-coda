#!/usr/bin/env python3
"""Edit patch/*.c, save, see it on a pooled emulator a few seconds later, back on the page you were on.

    python3 tools/ci/q2live.py                      # build, boot, then rebuild and reload on every save
    python3 tools/ci/q2live.py do wheel 7           # drive the emulator; each step is also kept as the route
    python3 tools/ci/q2live.py do key centre
    python3 tools/ci/q2live.py route                # the route replayed after each reload; `route clear` empties it
    python3 tools/ci/q2live.py route 'wheel 7;key centre;wheel 2;key centre'
    python3 tools/ci/q2live.py stop                 # give the emulator back to the pool

The first run builds this worktree once (tools/ci/rebuild.py --dev) and leases a pooled emulator
(tools/ci/emu_pool.py), started with Q2EMU_FASTBOOT. Then, on each save under patch/:

1. only demo is rebuilt (tools/build.py with Q2_DEMO_ONLY: no fonts, art, rootfs or update.tar), in one
   fixed directory, with the base build's dev number pinned (Q2_DEV_NUMBER) so stock.h does not change
   and ccache recompiles only the files that did;
2. the new demo (and hciplayer, when the player's sources changed it) is copied over the emulator's
   unpacked rootfs: no repack and no fresh unpack;
3. only the sandbox is restarted (fast boot, demo directly); the viewer keeps running, so the browser
   tab at the printed URL shows the new screen without a reload;
4. the route (the steps given through `do`, or set with `route`) is replayed, which puts you back on
   the page you were on.

A change outside patch/ (assets/, tools/) needs the whole build: q2live does it and reboots the
emulator from it. Each reload prints its steps' times. docs/building.md#live-reload.
"""
import argparse
import json
import os
import pathlib
import shutil
import subprocess
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'tools/ci'))
import emu_pool  # noqa: E402

PLACE = pathlib.Path.home()/'.cache/q2-builds'/f'{ROOT.name}-live'
STATE = PLACE/'state.json'
ZIP = pathlib.Path(os.environ.get('Q2_STOCK_ZIP', pathlib.Path.home()/'q2-stock/Q2 Firmware V1.36.zip'))
WATCH = ('patch',)
FULL = ('assets', 'tools', 'player', 'docs/manual')  # a change here needs the whole build


def load():
    try:
        return json.loads(STATE.read_text())
    except (OSError, ValueError):
        return {'route': []}


def save(state):
    PLACE.mkdir(parents=True, exist_ok=True)
    tmp = STATE.with_suffix('.tmp')
    tmp.write_text(json.dumps(state, indent=1))
    tmp.replace(STATE)


def env_for_build():
    env = dict(os.environ)
    if pathlib.Path('/usr/lib/ccache').is_dir() and shutil.which('ccache'):
        env['PATH'] = f'/usr/lib/ccache:{env["PATH"]}'
        env.setdefault('CCACHE_BASEDIR', str(pathlib.Path.home()))
        env.setdefault('CCACHE_NOHASHDIR', '1')
    env.setdefault('Q2_BUILD_CACHE', str(pathlib.Path.home()/'.cache/q2-builds/memo'))
    return env


def full_build():
    """The base build (tools/ci/rebuild.py --dev): its directory."""
    p = subprocess.run([sys.executable, str(ROOT/'tools/ci/rebuild.py'), '--dev'], cwd=ROOT,
                       stdout=subprocess.PIPE, text=True)
    if p.returncode:
        raise SystemExit('the build failed; see its output above')
    return p.stdout.strip().splitlines()[-1]


def demo_build(base):
    """demo alone, into PLACE/demo, with base's dev number; returns that directory, or None on a failed build."""
    out = PLACE/'demo'
    out.mkdir(parents=True, exist_ok=True)
    env = env_for_build()
    env['Q2_DEMO_ONLY'] = '1'
    env['Q2_DEV_NUMBER'] = str(json.loads((pathlib.Path(base)/'manifest.json').read_text())['dev'])
    p = subprocess.run([sys.executable, str(ROOT/'tools/build.py'), str(ZIP), '--out', str(out), '--dev'], cwd=ROOT,
                       env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    if p.returncode:
        print(p.stdout[-4000:], file=sys.stderr)
        return None
    return out


def copy_dir(state):
    return emu_pool.copy_dir(state['slot'])


def ours(e):
    return f'[b]wrap --die-with-parent .*--bind {e}/rootfs / '


def flips(e):
    try:
        f = (e/'state/fb0.state').read_text().split()
        return int(f[1])
    except (OSError, ValueError, IndexError):
        return -1


def settle(e, t0, quiet, limit=30, after=None):
    """Seconds from t0 to the last frame before `quiet` s without one (and past frame number `after`)."""
    last, last_t = flips(e), time.monotonic()
    while time.monotonic() - t0 < limit:
        n, now = flips(e), time.monotonic()
        if n != last:
            last, last_t = n, now
        elif now - last_t >= quiet and (after is None or n > after + 2):
            return last_t - t0
        time.sleep(0.02)
    return None


def restart(state, base):
    """The copy's sandbox again, fast boot, from its unpacked rootfs; the viewer is left running."""
    e = copy_dir(state)
    subprocess.run(['pkill', '-f', ours(e)])
    for _ in range(100):
        if subprocess.run(['pgrep', '-f', ours(e)], capture_output=True).returncode:
            break
        time.sleep(0.05)
    (e/'state/fb0.state').write_text('0 0\n')  # the frame count from 0, so the next frames are the new demo's
    env = {**os.environ, 'Q2EMU_FASTBOOT': '1', 'Q2EMU_PORT': str(emu_pool.port(state['slot']))}
    with open(e/'state/run.out', 'w') as log:
        subprocess.Popen(['setsid', str(e/'run.sh'), base], stdout=log, stderr=subprocess.STDOUT, env=env,
                         start_new_session=True)


def replay(state):
    """The route, after a wheel tick down and one up: demo's first wheel event after a boot moves nothing
    (measured: a recorded 'wheel 4' landed 3 rows down after every restart), and on Home's first row the
    pair ends where it began either way."""
    e = copy_dir(state)
    for step in ['wheel 1', 'wheel -1'] * bool(state['route']) + state['route']:
        t = time.monotonic()
        subprocess.run([str(e/'q2emu'), *step.split()], capture_output=True)
        settle(e, t, 0.35, 10)


def swap(state, out):
    """out's demo (and hciplayer, if it changed) over the copy's unpacked rootfs."""
    root = copy_dir(state)/'rootfs'
    for src, dst in ((out/'demo', root/'release/bin/demo'), (out/'peq/hciplayer', root/'usr/bin/hciplayer')):
        if src.exists() and (not dst.exists() or src.read_bytes() != dst.read_bytes()):
            tmp = dst.with_name(dst.name + '.q2live')
            shutil.copyfile(src, tmp)
            tmp.chmod(dst.stat().st_mode if dst.exists() else 0o755)  # the build writes it 0644; the rootfs's is 0755
            tmp.replace(dst)


def snapshot():
    """mtimes of the watched and full-build trees."""
    out = {}
    for top in (*WATCH, *FULL):
        for p in (ROOT/top).rglob('*'):
            if p.is_file() and '__pycache__' not in p.parts:
                out[str(p.relative_to(ROOT))] = p.stat().st_mtime_ns
    return out


def boot(state):
    base = full_build()
    os.environ['Q2EMU_FASTBOOT'] = '1'  # emu_pool's q2emu start inherits it
    if 'slot' not in state:
        state['slot'] = emu_pool.get(base, f'q2live-{ROOT.name}', 8, True)
    else:
        e = copy_dir(state)
        p = subprocess.run([str(e/'q2emu'), 'start', base], capture_output=True, text=True,
                           env={**os.environ, 'Q2EMU_PORT': str(emu_pool.port(state['slot']))})
        if p.returncode:
            raise SystemExit(p.stdout + p.stderr)
    state['base'] = base
    save(state)
    e = copy_dir(state)
    settle(e, time.monotonic(), 1.5, after=0)
    replay(state)
    return base


def reload(state, full):
    t0 = time.monotonic()
    times = []
    if full:
        boot(state)
        times.append(('full build and boot', time.monotonic() - t0))
    else:
        out = demo_build(state['base'])
        if out is None:
            print('build failed: the emulator keeps the last good demo', flush=True)
            return
        times.append(('demo build', time.monotonic() - t0))
        t = time.monotonic()
        swap(state, out)
        restart(state, state['base'])
        times.append(('swap and restart', time.monotonic() - t))
        t = time.monotonic()
        settle(copy_dir(state), t, 1.0, after=0)
        times.append(('boot to a still screen', time.monotonic() - t))
        t = time.monotonic()
        replay(state)
        times.append((f'route ({len(state["route"])} steps)', time.monotonic() - t))
    print(f'reloaded in {time.monotonic() - t0:.1f} s: ' + ', '.join(f'{w} {s:.1f} s' for w, s in times), flush=True)


def watch():
    state = load()
    boot(state)
    print(f'live: http://127.0.0.1:{emu_pool.port(state["slot"])}/ (slot {state["slot"]}); editing patch/ reloads. Ctrl-C stops '
          f'watching; `q2live.py stop` gives the emulator back', flush=True)
    seen = snapshot()
    while True:
        time.sleep(0.25)
        now = snapshot()
        changed = {p for p in now.keys() | seen.keys() if now.get(p) != seen.get(p)}
        if not changed:
            continue
        time.sleep(0.2)  # an editor's save can be several writes
        now = snapshot()
        changed |= {p for p in now.keys() | seen.keys() if now.get(p) != seen.get(p)}
        seen = now
        full = any(p.startswith(FULL) for p in changed)
        print(f'changed: {", ".join(sorted(changed)[:5])}{" ..." if len(changed) > 5 else ""}', flush=True)
        reload(load(), full)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('cmd', nargs='?', default='watch', choices=['watch', 'do', 'route', 'stop', 'reload'])
    ap.add_argument('args', nargs='*')
    a = ap.parse_args()
    state = load()
    if a.cmd == 'watch':
        watch()
    elif a.cmd == 'reload':  # one reload now, as a save would
        reload(state, False)
    elif a.cmd == 'do':
        if 'slot' not in state:
            raise SystemExit('no live emulator: run q2live.py first')
        p = subprocess.run([str(copy_dir(state)/'q2emu'), *a.args])
        if p.returncode == 0 and a.args[0] in ('key', 'wheel', 'tap', 'swipe'):
            state['route'].append(' '.join(a.args))
            save(state)
    elif a.cmd == 'route':
        if a.args == ['clear']:
            state['route'] = []
        elif a.args:
            state['route'] = [s.strip() for s in ' '.join(a.args).split(';') if s.strip()]
        save(state)
        print('; '.join(state['route']) or '(empty: a reload lands on Home)')
    elif a.cmd == 'stop':
        if 'slot' in state:
            emu_pool.put(state['slot'])
            del state['slot']
            save(state)
    return 0


if __name__ == '__main__':
    sys.exit(main())
