#!/usr/bin/env python3
"""Leak hunt: drive the same UI tour on a pooled emulator, loop after loop, and fail when demo grows.

    python3 tools/ci/leak_hunt.py BUILD             # exit 0 no growth, 1 a leak (metric and loop named), 3 could not run
    python3 tools/ci/leak_hunt.py BUILD --loops 6   # the warm-up loop plus 5 measured ones (default LOOPS)

smoke.py asks "does the UI work"; this asks "does it give back what it takes". It boots the build on a leased
pool emulator, runs TOUR once to warm up (lazy caches and first-use allocations settle there), takes a sample
back on Home as the baseline, then runs the tour again, LOOPS - 1 more times, sampling on Home after each.
A sample is read from demo's memory through /proc/PID/mem (smoke.py's windows(), plus the timer and idle
managers) and /proc/PID/status:

    windows    demo's window manager's children (a window opened per loop and never closed)
    widgets    every widget under them (a page that is closed but kept, a list that grows)
    timers     live timers and idle callbacks (a timer started by a page and never removed)
    rss        demo's resident set, in kB

`verdict` (a pure function of the samples) decides. A count (windows, widgets, timers) is a leak when it ended
above the baseline and rose in at least two loops: a leak adds each time the tour runs, while a cache that fills
once rises once and stops. Memory is a leak when it ended more than RSS_TOL_KB above the baseline; qemu's
translation cache and the allocator move it a little on their own. Each finding names the metric and the first
loop it grew in. A tour that cannot finish (the UI lost its way, demo died) is exit 3: smoke.py is the test for
that, and a run that never reached the measuring says nothing about growth. The emulator is leased (smoke.lease: as
`smoke-PID`), never on slot 2, with BLUETOOTH=0 for the run as smoke.py has it, and returned whatever happens.
A hard deadline (DEADLINE s after the lease) bounds the run. It runs in the queue's post tier, beside smoke,
and never in a merge batch (docs/building.md#leak-hunt).
"""
import argparse
import os
import pathlib
import resource
import signal
import struct
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import emu_pool  # noqa: E402
import smoke  # noqa: E402

LOOPS = 5            # tour runs: the first warms up, the rest are measured against the sample after it
DEADLINE = 240       # s from the lease to the verdict; the post tier's longest suite, unit-cov, takes about 330
RSS_TOL_KB = 12288   # kB demo's resident set may end above the baseline
COUNTS = ('windows', 'widgets', 'timers')
SETTLE_S = 1.5       # s on Home before a sample: toasts and slides are over
# Stock V1.36's timer and idle managers, as the window manager is found (smoke.WM_GOT): the GOT slot's data block
# holds them at these offsets; the timer list's head is at +0x14 in the manager and a list is {first, last,
# size}, each node's next at +0 and its item at +4.
TM_OFF, IM_OFF, TM_LIST = 0x15a0, 0x1550, 0x14
W_CHILDREN = smoke.WM_CHILDREN
MAX_DEPTH, MAX_KIDS = 40, 4000


class Mem:
    def __init__(self, pid):
        self.f = open(f'/proc/{pid}/mem', 'rb')

    def u32(self, a):
        self.f.seek(a)
        return struct.unpack('<I', self.f.read(4))[0]

    def close(self):
        self.f.close()


def widget_count(m, w, depth=0):
    """w and everything under it."""
    n = 1
    arr = m.u32(w + W_CHILDREN)
    if arr and depth < MAX_DEPTH:
        k = m.u32(arr)
        if 0 <= k < MAX_KIDS:
            elms = m.u32(arr + 8)
            n += sum(widget_count(m, c, depth + 1) for c in (m.u32(elms + 4 * i) for i in range(k)) if c)
    return n


def sample(pid):
    """{windows, widgets, timers, rss} of demo now; OSError or struct.error when its memory cannot be read."""
    m = Mem(pid)
    try:
        base = m.u32(smoke.WM_GOT)
        wm = m.u32(base + smoke.WM_OFF)
        arr = m.u32(wm + W_CHILDREN)
        n = m.u32(arr)
        if not 0 < n < 64:
            raise struct.error(f'the window manager holds {n} windows')
        elms = m.u32(arr + 8)
        wins = [m.u32(elms + 4 * i) for i in range(n)]
        timers = m.u32(m.u32(base + TM_OFF) + TM_LIST + 8) + m.u32(m.u32(base + IM_OFF) + 8)
        widgets = sum(widget_count(m, w) for w in wins)
    finally:
        m.close()
    status = pathlib.Path(f'/proc/{pid}/status').read_text()
    rss = int(next(ln for ln in status.splitlines() if ln.startswith('VmRSS')).split()[1])
    return {'windows': n, 'widgets': widgets, 'timers': timers, 'rss': rss}


def verdict(samples, rss_tol_kb=RSS_TOL_KB):
    """The leaks in samples, one string each (empty: none). samples[0] is the baseline, after the warm-up loop;
    samples[i] is after the tour's loop i + 1 (the warm-up is loop 1)."""
    out = []
    if len(samples) < 2:
        return out
    for metric in COUNTS:
        series = [s[metric] for s in samples]
        rises = [i for i in range(1, len(series)) if series[i] > series[i - 1]]
        if series[-1] > series[0] and len(rises) >= 2:
            out.append(f'{metric} grew from loop {rises[0] + 1}: {" -> ".join(map(str, series))} '
                       f'(after loops 1 to {len(series)})')
    rss = [s['rss'] for s in samples]
    over = [i for i in range(1, len(rss)) if rss[i] - rss[0] > rss_tol_kb]
    if over and rss[-1] - rss[0] > rss_tol_kb:
        out.append(f'rss grew from loop {over[0] + 1}: {" -> ".join(map(str, rss))} kB '
                   f'(tolerance {rss_tol_kb} kB over the baseline)')
    return out


def tour(e, say):
    """One loop of the UI, ending on Home: Now Playing from a list, Control Center and Search, about 33 s of the
    emulator's time. Cover Flow and Settings, which smoke.py walks, cost 34 s more a loop and are left out: this
    is about what a page leaves behind when it closes, and the run has to fit beside unit-cov (DEADLINE)."""
    H = smoke.HOME_FROM_END
    say('Library > Songs > Now Playing > back')
    e.row(H['library'], from_end=True)
    e.do('key', 'centre')
    e.expect('localmusic_page', 'Library')
    e.row(0)
    e.do('key', 'centre')
    e.expect('allmusic_page', 'Songs')
    e.do('key', 'centre')
    e.expect('playing_page', 'Center on a song')
    e.do('key', 'return')
    e.expect('allmusic_page', 'Return from Now Playing')
    e.home()
    say('Control Center')
    e.do('swipe', 187, 2, 187, 250, 300)
    e.expect('statusbar_dialog', 'Control Center')
    e.settle()
    e.do('key', 'return')
    e.expect('home_page', 'Return from Control Center')
    say('Search')
    e.row(H['search'], from_end=True)
    e.do('key', 'centre')
    e.expect('searchstrip_page', 'Search')
    e.settle()
    e.do('key', 'centre')
    e.do('wheel', 20)
    e.do('key', 'centre')
    e.settle()
    e.do('key', 'return', 900)
    e.expect('home_page', 'held Return from Search')
    e.home()


def hunt(e, loops, say):
    """Run the tour loops times; the sample taken on Home after each (the first, after the warm-up, is the baseline)."""
    samples = []
    for n in range(1, loops + 1):
        say(f'loop {n} of {loops}')
        tour(e, say)
        time.sleep(SETTLE_S)
        e.alive()
        s = sample(e.pid)
        say(f'  after loop {n}: ' + ', '.join(f'{k} {v}' for k, v in s.items()))
        samples.append(s)
    return samples


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('build', type=pathlib.Path)
    ap.add_argument('--loops', type=int, default=LOOPS, help=f'tour runs, the first a warm-up ({LOOPS})')
    ap.add_argument('--wait', type=float, default=600, help='seconds to wait for a free emulator (600)')
    args = ap.parse_args(argv)
    if not (args.build/'rootfs.squashfs').exists():
        ap.error(f'{args.build} is not a build (no rootfs.squashfs)')
    if args.loops < 3:
        ap.error('--loops is at least 3: the warm-up, a baseline and one more')
    t0 = time.monotonic()

    def log(msg):
        print(f'{time.monotonic() - t0:6.1f}  {msg}', flush=True)

    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    os.environ.pop('LD_PRELOAD', None)
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))
    saved = {}

    def prepare(k):
        ini = emu_pool.copy_dir(k)/'state/data/config.ini'
        if ini.exists():
            saved[k] = ini.read_bytes()
            ini.write_text(smoke.bluetooth_off(saved[k].decode('utf-8', 'replace')))

    slot, step = None, ['lease']

    def say(what):
        step[0] = what
        log(what)

    try:
        try:
            slot = smoke.lease(args.build, args.wait, prepare, log)
        except smoke.Unavailable as why:
            log(f'leak-hunt: could not run: {why}')
            return 3
        e = smoke.Emulator(slot, log)
        log(f'slot {slot}: {e.copy}')

        def overdue(*_):
            raise smoke.Deadline()
        signal.signal(signal.SIGALRM, overdue)
        signal.alarm(DEADLINE)
        say('boot: Home')
        e.boot()
        samples = hunt(e, args.loops, say)
        signal.alarm(0)
    except (smoke.Failed, smoke.Deadline, OSError, struct.error) as why:
        signal.alarm(0)
        reason = f'the deadline ({DEADLINE} s) passed' if isinstance(why, smoke.Deadline) else str(why)
        log(f'leak-hunt: could not run: at "{step[0]}": {reason}')
        return 3
    finally:
        if slot is not None:
            emu_pool.put(slot)
            if slot in saved:
                (emu_pool.copy_dir(slot)/'state/data/config.ini').write_bytes(saved[slot])
    found = verdict(samples)
    for line in found:
        log(f'leak-hunt: LEAK: {line}')
    log(f'leak-hunt: {"leak found" if found else "no growth"} in {time.monotonic() - t0:.0f} s')
    return 1 if found else 0


if __name__ == '__main__':
    sys.exit(main())
