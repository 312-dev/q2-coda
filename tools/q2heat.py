#!/usr/bin/env python3
"""Read q2guard's heat log (patch/q2guard.c, docs/internals.md#overheat-guardian) back as a timeline, and flag the
minute the temperature rose fastest (with no temperature in the log, the CPU's busy share) and which processes used
the CPU then.

    python3 tools/q2heat.py q2-heat.1 q2-heat.0          # oldest file first, as the Q2 keeps them
    adb shell cat /mnt/mmc/.q2muse/logs/q2-heat.1 /mnt/mmc/.q2muse/logs/q2-heat.0 | python3 tools/q2heat.py -

A line is `TIME up=S t=C ... top=name:pct,... g=STATE`; a note is `TIME WORD ...` (BOOT, SENSOR, WARN, LOADWARN,
POWEROFF, NOANSWER, POWER, CARD). The card store's `== N lines lost while the card was away` line is read as
a GAP note at the time of the line before it. Lines that are none of these are skipped."""
import argparse
import datetime
import re
import sys
from dataclasses import dataclass, field
from typing import Optional

TIME = re.compile(r'^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ) (.*)$')
LOST = re.compile(r'^== (\d+) lines lost while the card was away$')  # patch/card_store.c's note


@dataclass
class Sample:
    when: datetime.datetime
    up: int
    temp: Optional[float]
    busy: Optional[int]
    top: list = field(default_factory=list)
    fields: dict = field(default_factory=dict)
    boot: int = 0  # which boot (BOOT notes counted) the sample is from


@dataclass
class Note:
    when: datetime.datetime
    text: str
    boot: int


def num(v):
    try:
        return float(v) if '.' in v else int(v)
    except ValueError:
        return None


def parse(lines):
    """Samples and notes, in the order given."""
    samples, notes, boot, last = [], [], 0, None
    for raw in lines:
        m = TIME.match(raw.rstrip('\n'))
        if not m:
            lost = LOST.match(raw.rstrip('\n'))
            if lost and last:
                notes.append(Note(last, f'GAP card away, {lost[1]} lines lost', boot))
            continue
        when = datetime.datetime.strptime(m[1], '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=datetime.timezone.utc)
        rest = m[2]
        last = when
        if not rest.startswith('up='):
            boot += rest.startswith('BOOT ')
            notes.append(Note(when, rest, boot))
            continue
        f = dict(kv.split('=', 1) for kv in rest.split() if '=' in kv)
        top = []
        for item in f.get('top', '-').split(','):
            name, _, pct = item.rpartition(':')
            if name:
                top.append((name, float(pct)))
        t = num(f.get('t', '-'))
        b = num(f.get('cpu', '-'))
        samples.append(Sample(when, int(f.get('up', 0)), t, b if isinstance(b, int) else None, top, f, boot))
    return samples, notes


def fastest_minute(samples):
    """(start, end, rise, kind) of the steepest rise over about a minute within one boot: temperature in C if
    any sample has one, else CPU busy percent. None with too few samples."""
    kind = 'temp' if any(s.temp is not None for s in samples) else 'cpu'
    pts = [(s, s.temp if kind == 'temp' else s.busy) for s in samples]
    pts = [(s, v) for s, v in pts if v is not None]
    best = None
    for i, (a, va) in enumerate(pts):
        for b, vb in pts[i + 1:]:
            if b.boot != a.boot:
                break
            span = b.up - a.up
            if span > 65:
                break
            if span >= 55 and (best is None or vb - va > best[2]):
                best = (a, b, vb - va, kind)
    if best is None and len(pts) >= 2:  # less than a minute of samples: the steepest step
        steps = [(a, b, vb - va, kind) for (a, va), (b, vb) in zip(pts, pts[1:]) if a.boot == b.boot]
        best = max(steps, key=lambda x: x[2], default=None)
    return best


def top_during(samples, a, b):
    """Each process's mean share over the samples from a to b, highest first."""
    span = [s for s in samples if a.when <= s.when <= b.when and s.boot == a.boot]
    total = {}
    for s in span:
        for name, pct in s.top:
            total[name] = total.get(name, 0.0) + pct
    return sorted(((n, t / max(len(span), 1)) for n, t in total.items()), key=lambda x: -x[1])


def report(samples, notes, out=None):
    out = out or sys.stdout  # looked up per call: a redirected stdout is the one written
    for n in notes:
        if n.text.startswith(('BOOT', 'POWEROFF', 'GAP', 'NOANSWER', 'SENSOR', 'WARN', 'LOADWARN', 'POWER', 'CARD')):
            print(f'{n.when:%Y-%m-%d %H:%M:%S}  ** {n.text}', file=out)
    print(file=out)
    print('time                 up      temp   cpu  lvl  st           cab udc          top', file=out)
    for s in samples:
        t = '-' if s.temp is None else f'{s.temp:.1f}'
        b = '-' if s.busy is None else str(s.busy)
        top = ', '.join(f'{n} {p:g}%' for n, p in s.top)
        print(f'{s.when:%Y-%m-%d %H:%M:%S}  {s.up:<7} {t:>5}  {b:>4}  {s.fields.get("lv", "-"):>3}  '
              f'{s.fields.get("st", "-"):<12} {s.fields.get("cab", "-"):>3} {s.fields.get("udc", "-"):<12} {top}',
              file=out)
    best = fastest_minute(samples)
    print(file=out)
    if not best:
        print('Too few samples to find a rise.', file=out)
        return None
    a, b, rise, kind = best
    unit = ' C' if kind == 'temp' else ' points of CPU busy (no temperature in the log)'
    print(f'Fastest rise: {rise:+g}{unit}, {a.when:%H:%M:%S} to {b.when:%H:%M:%S} (up {a.up} to {b.up} s)', file=out)
    for name, pct in top_during(samples, a, b)[:3]:
        print(f'  {name}: {pct:.1f}% of a core', file=out)
    return best


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    ap.add_argument('files', nargs='+', help='q2-heat.1 then q2-heat.0, or - for standard input')
    args = ap.parse_args(argv)
    lines = []
    for f in args.files:
        lines += sys.stdin.readlines() if f == '-' else open(f, encoding='utf-8', errors='replace').readlines()
    samples, notes = parse(lines)
    report(samples, notes)


if __name__ == '__main__':
    main()
