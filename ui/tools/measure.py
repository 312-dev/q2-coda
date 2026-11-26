#!/usr/bin/env python3
"""Time a Songs list in a q2-emu copy: page flips and CPU time per wheel tick, for demo's list and q2ui's alike
(docs/ui-rewrite.md#measurements).

    python3 ui/tools/measure.py EMU_DIR demo|q2ui [--slow 20] [--gap 0.3] [--fast 100] [--json OUT]

The list must be on screen with the selection near its top. Three phases, each read from outside the guest:
idle (5 s, nothing sent), slow (SLOW single ticks, GAP seconds after each: long enough for the selection's
spring, short enough that the scroll indicator, shown for a second after a tick, never fades between two) and
fast (one turn of FAST ticks, then back up).
Flips are the shim's count in state/fb0.state. CPU is the on-CPU time of every thread of the guest program
(/proc/PID/task/*/schedstat), so it holds qemu's translation and the flush thread too: a ratio between two
programs under the same emulator, not a Q2's milliseconds.
"""
import argparse
import json
import pathlib
import subprocess
import time

NAMES = {'demo': '/release/bin/demo', 'q2ui': '/usr/bin/q2ui'}


def guest_pid(emu, program):
    """The qemu process running program in this copy's sandbox: a descendant of the bwrap bound to its rootfs."""
    roots = {f'{emu}/rootfs', f'{emu.resolve()}/rootfs'}
    procs = {}
    for proc in pathlib.Path('/proc').iterdir():
        if not proc.name.isdigit():
            continue
        try:
            args = (proc/'cmdline').read_bytes().split(b'\0')
            parent = int((proc/'stat').read_text().rsplit(')', 1)[1].split()[1])
        except (OSError, ValueError, IndexError):
            continue
        procs[int(proc.name)] = (parent, [x.decode(errors='replace') for x in args])
    for pid, (parent, args) in procs.items():
        if NAMES[program] not in args:
            continue
        while parent in procs:
            up, upargs = procs[parent]
            if upargs[:1] == ['bwrap'] and roots & set(upargs):
                return pid
            parent = up
    raise SystemExit(f'no {program} running in {emu}')


def cpu_ms(pid):
    total = 0
    for task in pathlib.Path(f'/proc/{pid}/task').iterdir():
        try:
            total += int((task/'schedstat').read_text().split()[0])
        except OSError:
            pass
    return total / 1e6


def flips(emu):
    return int((emu/'state/fb0.state').read_text().split()[1])


def wheel(emu, n):
    subprocess.run([str(emu/'q2emu'), 'wheel', str(n)], check=True)


def phase(emu, pid, act, settle):
    f0, c0, t0 = flips(emu), cpu_ms(pid), time.time()
    act()
    time.sleep(settle)
    return dict(flips=flips(emu) - f0, cpu_ms=round(cpu_ms(pid) - c0, 1), wall_s=round(time.time() - t0, 2))


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('emu', type=pathlib.Path)
    ap.add_argument('program', choices=sorted(NAMES))
    ap.add_argument('--slow', type=int, default=20)
    ap.add_argument('--gap', type=float, default=0.3)
    ap.add_argument('--fast', type=int, default=100)
    ap.add_argument('--json')
    a = ap.parse_args()
    pid = guest_pid(a.emu, a.program)
    out = dict(program=a.program, idle=phase(a.emu, pid, lambda: None, 5.0))

    def slow():
        for _ in range(a.slow):
            wheel(a.emu, 1)
            time.sleep(a.gap)
    out['slow'] = phase(a.emu, pid, slow, 1.5)
    out['fast_down'] = phase(a.emu, pid, lambda: wheel(a.emu, a.fast), 2.0)
    out['fast_up'] = phase(a.emu, pid, lambda: wheel(a.emu, -a.fast), 2.0)
    idle_rate = out['idle']['cpu_ms'] / out['idle']['wall_s']  # the program's own polling, ms of CPU a second
    for name, ticks in (('slow', a.slow), ('fast_down', a.fast), ('fast_up', a.fast)):
        p = out[name]
        p['ticks'] = ticks
        p['flips_per_tick'] = round(p['flips'] / ticks, 2)
        net = p['cpu_ms'] - idle_rate * p['wall_s']
        p['cpu_ms_per_tick'] = round(net / ticks, 2)
        p['cpu_ms_per_flip'] = round(net / p['flips'], 2) if p['flips'] else None
    print(json.dumps(out, indent=1))
    if a.json:
        pathlib.Path(a.json).write_text(json.dumps(out, indent=1) + '\n')


if __name__ == '__main__':
    main()
