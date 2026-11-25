#!/usr/bin/env python3
"""Read a q2-usbtrace log back (patch/q2usbtrace.c, docs/internals.md#usb-tracer): one block per plug cycle with its
type, the time from the cycle's start to each milestone, what stock did, what the shadow state machine
(patch/usb_fsm.c) would have done, and every disagreement; then a tally. With the unified log beside it (--ulog), stock's
own words for the cycle (the `usb` lines: cable in and out, otg-on and otg-off with their why, the verdicts) are
printed in it, matched by uptime.

    python3 tools/usbtrace_report.py q2-usbtrace.1 q2-usbtrace.0          # oldest first, from the card's .q2muse/logs
    python3 tools/usbtrace_report.py q2-usbtrace.0 --ulog q2-log.1 q2-log.0
    adb exec-out cat /mnt/mmc/.q2muse/logs/q2-usbtrace.1 /mnt/mmc/.q2muse/logs/q2-usbtrace.0 | python3 tools/usbtrace_report.py -

A trace line is `UPTIME KIND ...`, UPTIME CLOCK_MONOTONIC seconds with six decimals (the unified log's uptime, in
seconds). The kinds are in docs/internals.md#usb-tracer. Exit 1 when a cycle disagreed or was unsafe.

The module also turns a trace into the state machine's events (fsm_events), as q2usbtrace_core.c feeds them, which
test/unit/test_usb_fsm.py replays recorded traces with, and lists stock's grants with their devices' measured
timings (grants), which it replays against grants the machine makes itself."""
from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field
from typing import Iterable, Iterator, TextIO

# usb_fsm.h's events
VBUS, IDLOW, ONLINE, POWER, HOSTDEV, PCM, UDC, TIMER, WAKE, CONNECT = range(10)
ADDRESSED = ('addressed', 'configured')


@dataclass
class Line:
    t: int  # ns
    kind: str
    rest: str

    def fields(self) -> dict[str, str]:
        return dict(w.split('=', 1) for w in self.rest.split() if '=' in w)


def stamp(text: str) -> int | None:
    """'1234.567890' as ns, or None when it is not an uptime."""
    m = re.fullmatch(r'(\d+)(?:\.(\d{1,9}))?', text)
    if not m:
        return None
    return int(m.group(1)) * 1_000_000_000 + int((m.group(2) or '0').ljust(9, '0'))


def parse(lines: Iterable[str]) -> list[Line]:
    """The trace's lines; comments (#), card notes (==) and anything without an uptime skipped."""
    out = []
    for raw in lines:
        parts = raw.rstrip('\n').split(' ', 2)
        if len(parts) < 2 or raw.startswith(('#', '==')):
            continue
        t = stamp(parts[0])
        if t is None:
            continue
        out.append(Line(t, parts[1], parts[2] if len(parts) > 2 else ''))
    return out


def fsm_events(lines: Iterable[Line]) -> Iterator[tuple[int, int, int] | tuple[int, str, int]]:
    """(t, event, value) for the state machine, as q2usbtrace_core.c feeds it: a level is fed once per change whatever
    source gave it (an edge, a uevent, a sample), each online read always, and a `start` line yields (t, 'start', 0),
    a new run whose machine starts afresh."""
    fed: dict[int, int] = {}

    def level(ev: int, v: int, t: int):
        if fed.get(ev) != v:
            fed[ev] = v
            yield t, ev, v

    last_on: int | None = None
    for ln in lines:
        k, t = ln.kind, ln.t
        if k == 'start':
            fed.clear()
            last_on = None
            yield t, 'start', 0
        elif k == 'e' and ln.rest.split()[0] in ('PD17', 'PD18'):
            pin, v = ln.rest.split()[:2]
            yield from level(VBUS if pin == 'PD17' else IDLOW, 1 - int(v), t)
        elif k == 'on':
            last_on = int(ln.rest)
            yield t, ONLINE, last_on
        elif k == 's':
            f = ln.fields()
            if f.get('PC20', '?') != '?':  # the bus power first, as q2usbtrace_core.c feeds it
                yield from level(POWER, int(f['PC20']), t)
            before = fed.get(VBUS)
            if f.get('PD17', '?') != '?':
                yield from level(VBUS, 1 - int(f['PD17']), t)
            on = f.get('on', '?')
            if on != '?' and (fed.get(VBUS) != before or int(on) != last_on):
                last_on = int(on)
                yield t, ONLINE, last_on
            elif fed.get(VBUS) != before and last_on is not None:
                yield t, ONLINE, last_on  # the read the change asks for: the sample's, unchanged
            if f.get('PD18', '?') != '?':
                yield from level(IDLOW, 1 - int(f['PD18']), t)
            if f.get('dev', '?') != '?':
                yield from level(HOSTDEV, int(f['dev']), t)
        elif k == 'u':
            w = ln.rest.split()
            if len(w) >= 3 and w[0] in ('add', 'remove'):
                if w[1] == 'usb' and w[2] == '1-1':
                    yield from level(HOSTDEV, int(w[0] == 'add'), t)
                elif w[1] == 'sound' and w[2] == 'pcmC1D0p':
                    yield from level(PCM, int(w[0] == 'add'), t)
        elif k == 'udc':
            yield from level(UDC, int(ln.rest.split()[0] in ADDRESSED), t)
        elif k == 'otg':
            hprt0 = ln.fields().get('hprt0', '?')
            if hprt0 != '?':  # its connect status: a device's pull-up on the host port
                yield from level(CONNECT, int(hprt0, 16) & 1, t)
        elif k == 'wake':
            yield t, WAKE, 0
            fed.clear()


def runs(lines: list[Line]) -> list[list[Line]]:
    """The lines split at each `start`, in file order: each run of the tracer, with its own clock (a boot's)."""
    out: list[list[Line]] = []
    for ln in lines:
        if ln.kind == 'start' or not out:
            out.append([])
        out[-1].append(ln)
    return out


@dataclass
class Grant:
    on: int  # stock's otg-on (a sample's PC20=1), ns
    off: int | None  # its otg-off
    connect: int | None  # the first HPRT0 read with its connect status set after on
    dev: int | None  # the host port's device (add usb 1-1)
    pcm: int | None  # the USB DAC's playback PCM (add sound pcmC1D0p)


def grants(lines: Iterable[Line]) -> list[Grant]:
    """Stock's grants in one run's lines, each with when the device on the host port connected, enumerated and gave
    its PCM: the measured timings a replay applies to a grant made at another time (test/unit/test_usb_fsm.py)."""
    out: list[Grant] = []
    for ln in lines:
        g = out[-1] if out and out[-1].off is None else None
        if ln.kind == 's' and ln.fields().get('PC20') in ('0', '1'):
            if ln.fields()['PC20'] == '1' and g is None:
                out.append(Grant(ln.t, None, None, None, None))
            elif ln.fields()['PC20'] == '0' and g is not None:
                g.off = ln.t
        elif g is None:
            continue
        elif ln.kind == 'otg' and ln.fields().get('hprt0', '?') != '?' and int(ln.fields()['hprt0'], 16) & 1:
            g.connect = g.connect or ln.t
        elif ln.kind == 'u' and ln.rest.split()[:3] == ['add', 'usb', '1-1']:
            g.dev = g.dev or ln.t
        elif ln.kind == 'u' and ln.rest.split()[:3] == ['add', 'sound', 'pcmC1D0p']:
            g.pcm = g.pcm or ln.t
    return out


@dataclass
class Cycle:
    n: int
    summary: dict[str, str]
    lines: list[Line] = field(default_factory=list)
    closed: int = 0  # the summary's time: the cycle's quiet end

    @property
    def start(self) -> int:
        """The cycle's start as the tracer counted it (its `at`), else its first line's time."""
        at = stamp(self.summary.get('at', ''))
        return at if at is not None else self.lines[0].t if self.lines else 0

    @property
    def end(self) -> int:
        return max(self.lines[-1].t if self.lines else 0, self.closed)

    @property
    def bad(self) -> bool:
        return 'DISAGREE' in self.summary.get('verdict', '') or 'UNSAFE' in self.summary.get('verdict', '')


def cycles(lines: list[Line]) -> list[Cycle]:
    """The lines grouped by the tracer's `cycle` summaries: a cycle's lines are those since the last summary (a run's
    `start` line begins afresh); lines after the last summary are an open cycle, n 0."""
    out, pending = [], []
    for ln in lines:
        if ln.kind == 'start':
            pending = []
        if ln.kind != 'cycle':
            pending.append(ln)
            continue
        f = ln.fields()
        verdict = ' '.join(w for w in ln.rest.split() if '=' not in w)
        out.append(Cycle(int(f.get('n', '0')), {**f, 'verdict': verdict}, [p for p in pending if p.kind != 'start'],
                         ln.t))
        pending = []
    rest = [p for p in pending if p.kind not in ('start', 'clock')]
    if any(p.kind in ('e', 'u', 'udc', 'stock') for p in rest):
        out.append(Cycle(0, {'type': 'open', 'verdict': 'open'}, rest))
    return out


@dataclass
class Ulog:
    up_ms: int
    level: str
    src: str
    msg: str


ULOG = re.compile(r'^\S+Z (\d+) ([EWI]) (\S+) (.*)$')


def read_ulog(lines: Iterable[str]) -> list[Ulog]:
    """The unified log's usb and usbtrace lines (patch/ulog_line.h)."""
    out = []
    for raw in lines:
        m = ULOG.match(raw.rstrip('\n'))
        if m and m.group(3) in ('usb', 'usbtrace'):
            out.append(Ulog(int(m.group(1)), m.group(2), m.group(3), m.group(4)))
    return out


MILESTONES = ('vbus+', 'vbus-', 'id-', 'id+', 'on1', 'addr', 'conf', 'dev+', 'dev-', 'pcm+', 'stock-on', 'stock-off',
              'shadow-on', 'shadow-replug')


def rel(t: int, start: int) -> str:
    return f'{(t - start) / 1e9:+.3f}'


def report(cs: list[Cycle], ulog: list[Ulog], out: TextIO) -> int:
    """Each cycle's block, then the tally; the number of cycles that disagreed or were unsafe."""
    bad = 0
    tally: dict[str, int] = {}
    for c in cs:
        s = c.summary
        tally[s.get('type', '?')] = tally.get(s.get('type', '?'), 0) + 1
        bad += c.bad
        out.write(f"cycle {c.n} {s.get('type', '?')} at {c.start / 1e9:.3f} (from {s.get('from', '?')}): "
                  f"stock {s.get('stock', '?')}, shadow {s.get('shadow', '?')}, {s['verdict'] or '?'}\n")
        times = [f'{k} {s[k]}' for k in MILESTONES if s.get(k, '-') != '-']
        if times:
            out.write('  milestones: ' + ', '.join(times) + '\n')
        for ln in c.lines:
            if ln.kind == 'stock' or ln.kind == 'unsafe:' or (ln.kind == 'shadow:' and ln.rest.startswith(('would', 'disagree'))):
                out.write(f'  {rel(ln.t, c.start)} {ln.kind} {ln.rest}\n')
        for u in ulog:
            if c.start - 1_000_000_000 <= u.up_ms * 1_000_000 <= c.end + 1_000_000_000:
                out.write(f'  {rel(u.up_ms * 1_000_000, c.start)} ulog {u.level} {u.src} {u.msg}\n')
    out.write(f"{len(cs)} cycles: " + ', '.join(f'{v} {k}' for k, v in sorted(tally.items())) +
              f"; {bad} disagreed or unsafe\n")
    return bad


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    ap.add_argument('logs', nargs='+', help='q2-usbtrace.1 then q2-usbtrace.0, or - for stdin')
    ap.add_argument('--ulog', nargs='*', default=[], help='the unified log files, oldest first (q2-log.3 .. q2-log.0)')
    a = ap.parse_args(argv)
    text: list[str] = []
    for name in a.logs:
        if name == '-':
            text += sys.stdin.read().splitlines()
        else:
            with open(name, encoding='utf-8', errors='replace') as f:
                text += f.read().splitlines()
    ulog: list[Ulog] = []
    for name in a.ulog:
        with open(name, encoding='utf-8', errors='replace') as f:
            ulog += read_ulog(f)
    return 1 if report(cycles(parse(text)), ulog, sys.stdout) else 0


if __name__ == '__main__':
    sys.exit(main())
