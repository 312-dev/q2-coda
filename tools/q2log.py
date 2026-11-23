#!/usr/bin/env python3
"""Read the Q2's logs back as one timeline: the unified log (q2-log, patch/q2log_core.c, docs/internals.md#unified-log)
merged by time with the heat log (q2-heat, tools/q2heat.py) and the Bluetooth link log (q2-bt, tools/q2bt.py).

    python3 tools/q2log.py --adb                                   # all three from a --dev Q2 over adb
    python3 tools/q2log.py q2-log.3 q2-log.2 q2-log.1 q2-log.0     # the unified log alone, oldest file first
    python3 tools/q2log.py q2-log.* --heat q2-heat.3 q2-heat.2 q2-heat.1 q2-heat.0 --bt q2-bt.3 q2-bt.2 q2-bt.1 q2-bt.0
    python3 tools/q2log.py --adb --level W --src bt,usb            # warnings and errors of two sources only
    python3 tools/q2log.py --adb --btev                            # the Bluetooth event log alone (q2-btev), days of it
    python3 tools/q2log.py q2-btev.3 q2-btev.2 q2-btev.1 q2-btev.0 # the same from saved files: its lines are unified ones

A unified line is `TIME UPTIME_MS LEVEL SOURCE message`; a heat or Bluetooth line `TIME up=S ...` or a note
`TIME WORD ...`. Each line comes out as `TIME UPTIME LOG rest`, the uptime in seconds (with ms for the unified log's,
blank for a note's), sorted by the time and, within a second, by uptime; a note, which has none, comes first in its
second, and lines that tie keep their files' order. card_store.c's `== N lines lost while the card was away` is a GAP at the time of the line
before it. The Q2's clock may be unset (1970) until Wi-Fi sets it: such lines sort before everything else."""
import argparse
import re
import subprocess
import sys
from dataclasses import dataclass
from typing import Optional

LOGS = '/mnt/mmc/.q2muse/logs'
TIME = re.compile(r'^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ) (.*)$')
ULINE = re.compile(r'^(\d+) ([EWI]) (\S+) (.*)$')
UP = re.compile(r'^up=(\d+)\b')
LOST = re.compile(r'^== (\d+) lines lost while the card was away$')  # patch/card_store.c's note
LEVELS = 'EWI'


@dataclass
class Line:
    when: str  # the ISO time, which sorts as text
    up_ms: Optional[int]
    log: str  # log, heat or bt
    text: str
    level: str = 'I'
    src: str = ''


def parse(lines, log):
    """A file's lines, in its order; the unified log's with their level and source."""
    out = []
    for raw in lines:
        raw = raw.rstrip('\n')
        m = TIME.match(raw)
        if not m:
            lost = LOST.match(raw)
            if lost and out:
                out.append(Line(out[-1].when, out[-1].up_ms, log, f'GAP card away, {lost[1]} lines lost', 'W', 'card'))
            continue
        when, rest = m[1], m[2]
        u = ULINE.match(rest) if log == 'log' else None
        if u:
            out.append(Line(when, int(u[1]), log, f'{u[2]} {u[3]} {u[4]}', u[2], u[3]))
            continue
        up = UP.match(rest)
        out.append(Line(when, int(up[1]) * 1000 if up else None, log, rest))
    return out


def merge(*logs):
    """One timeline: by time, then uptime where both lines have one; otherwise the order given (a stable sort)."""
    tagged = [(line, i) for i, line in enumerate(line for lines in logs for line in lines)]
    return [line for line, _ in sorted(tagged, key=lambda t: (t[0].when, t[0].up_ms if t[0].up_ms is not None else -1,
                                                                t[1]))]


def keep(line, level, srcs):
    """The unified log's lines at or above level and of srcs; heat and Bluetooth lines only when nothing is asked."""
    if line.log != 'log':
        return level == 'I' and not srcs
    return LEVELS.index(line.level) <= LEVELS.index(level) and (not srcs or line.src in srcs)


def show(lines, out=None):
    out = out or sys.stdout
    for line in lines:
        up = '' if line.up_ms is None else (f'{line.up_ms / 1000:.3f}' if line.log == 'log' else f'{line.up_ms // 1000}')
        out.write(f'{line.when} {up:>11} {line.log:<4} {line.text}\n')


def adb(command, serial=None):
    """A command's stdout on the Q2, over adb exec-out (stderr dropped on the Q2's side)."""
    args = ['adb', *(['-s', serial] if serial else []), 'exec-out', command]
    return subprocess.run(args, check=True, capture_output=True).stdout.decode('utf-8', 'replace').splitlines(True)


def read(paths):
    lines = []
    for p in paths:
        with sys.stdin if p == '-' else open(p, encoding='utf-8', errors='replace') as f:
            lines += f.readlines()
    return lines


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    ap.add_argument('files', nargs='*', help='q2-log.3 to q2-log.0, oldest first, or - for standard input')
    ap.add_argument('--heat', nargs='+', default=[], help='q2-heat.3 to q2-heat.0, oldest first')
    ap.add_argument('--bt', nargs='+', default=[], help='q2-bt.3 to q2-bt.0, oldest first')
    ap.add_argument('--adb', action='store_true', help='read all three from the Q2 over adb (q2guard --dump first)')
    ap.add_argument('--btev', action='store_true',
                    help="with --adb, the Bluetooth event log (q2-btev, q2guard --dump-bt) alone: the unified log's "
                         'Bluetooth lines, kept longer')
    ap.add_argument('-s', '--serial', help="adb's -s")
    ap.add_argument('--level', choices=list(LEVELS), default='I', help='the unified log at this level or worse')
    ap.add_argument('--src', default='', help='the unified log of these sources only, comma-separated')
    a = ap.parse_args(argv)
    if a.adb and a.btev:
        # the same flush, then q2-btev.3 to .0; its lines are copies of the unified log's, so nothing to merge
        log, heat, bt = parse(adb('q2guard --dump-bt 2>/dev/null', a.serial), 'log'), [], []
    elif a.adb:
        # q2guard --dump writes out what the running q2guard holds in RAM, then prints q2-log.3 to .0
        log = parse(adb('q2guard --dump 2>/dev/null', a.serial), 'log')
        heat = parse(adb(f'cd {LOGS}; cat q2-heat.3 q2-heat.2 q2-heat.1 q2-heat.0 2>/dev/null', a.serial), 'heat')
        bt = parse(adb(f'cd {LOGS}; cat q2-bt.3 q2-bt.2 q2-bt.1 q2-bt.0 2>/dev/null', a.serial), 'bt')
    elif a.files or a.heat or a.bt:
        log, heat, bt = parse(read(a.files), 'log'), parse(read(a.heat), 'heat'), parse(read(a.bt), 'bt')
    else:
        ap.error('files, --heat, --bt or --adb')
    srcs = {s for s in a.src.split(',') if s}
    show([line for line in merge(log, heat, bt) if keep(line, a.level, srcs)])
    return 0


if __name__ == '__main__':
    sys.exit(main())
