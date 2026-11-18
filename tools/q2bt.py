#!/usr/bin/env python3
"""Read q2guard's Bluetooth link log (patch/q2bt.c, docs/internals.md#bluetooth-link-monitor) back as a timeline:
each stream with its codec, the seconds it fell behind, and what every signal did in the 10 s before each.

    python3 tools/q2bt.py q2-bt.1 q2-bt.0              # oldest file first, as the Q2 keeps them
    adb shell cat /mnt/mmc/.q2muse/logs/q2-bt.1 /mnt/mmc/.q2muse/logs/q2-bt.0 | python3 tools/q2bt.py -
    python3 tools/q2bt.py --all q2-bt.0                # every second, not only the stalls

A line is `TIME up=S kbps= acl= wq= rssi= lq= tpl= afh= err= wtx= wrx= cpu= ba= own=`, one a second while a stream
runs; a note is `TIME WORD ...` (START, CODEC, STOP). The card store's `== N lines lost while the card was away` is a
GAP. A second is behind when the controller was handed under BEHIND of the stream's median rate:
- **link**: and something waited in the A2DP socket (wq > 0). The kernel queues only when the controller's ACL
  buffers are full, so the radio was not getting packets to the headset fast enough: distance, the body, Wi-Fi.
- **source**: with nothing waiting, but some sent. The Q2 itself did not feed the encoder in time (CPU, the player).
A second with nothing sent and nothing waiting is idle (a pause, the transport let go), not a stall."""
import argparse
import datetime
import re
import statistics
import sys
from dataclasses import dataclass, field
from typing import Optional

TIME = re.compile(r'^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ) (.*)$')
LOST = re.compile(r'^== (\d+) lines lost while the card was away$')  # patch/card_store.c's note
BEHIND = 0.8   # a second under this share of the stream's median kbps is behind
BEFORE = 10    # seconds shown before each stall
SIGNALS = ('kbps', 'acl', 'wq', 'rssi', 'lq', 'tpl', 'afh', 'err', 'wtx', 'wrx', 'cpu', 'ba', 'own')


@dataclass
class Sample:
    when: datetime.datetime
    up: int
    f: dict  # each of SIGNALS: an int, or None for "-"
    stream: int = 0
    kind: str = ''  # '', 'link', 'source' or 'idle'


@dataclass
class Stream:
    n: int
    start: datetime.datetime
    head: str = ''      # START's text: the handle, the controller's buffers
    codec: str = '?'
    hz: int = 0
    samples: list = field(default_factory=list)
    end: Optional[datetime.datetime] = None


def value(v):
    try:
        return int(v)
    except ValueError:
        return None


def parse(lines):
    """The streams, in order, each with its samples; and the notes (time, text) that are not START, CODEC or STOP.
    A sample before any START (a log that begins mid-stream) opens a stream of its own."""
    streams, notes, cur, last = [], [], None, None
    for raw in lines:
        text = raw.rstrip('\n')
        m = TIME.match(text)
        if not m:
            lost = LOST.match(text)
            if lost and last:
                notes.append((last, f'GAP card away, {lost[1]} lines lost'))
            continue
        when = datetime.datetime.strptime(m[1], '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=datetime.timezone.utc)
        rest, last = m[2], when
        if rest.startswith('up='):
            if cur is None:
                cur = Stream(len(streams) + 1, when, 'log begins mid-stream')
                streams.append(cur)
            kv = dict(item.split('=', 1) for item in rest.split() if '=' in item)
            cur.samples.append(Sample(when, value(kv.get('up', '0')) or 0, {k: value(kv.get(k, '-')) for k in SIGNALS},
                                      cur.n))
        elif rest.startswith('START'):
            if cur:
                cur.end = when
            cur = Stream(len(streams) + 1, when, rest[5:].strip())
            streams.append(cur)
        elif rest.startswith('CODEC') and cur:
            parts = rest.split()
            cur.codec = parts[1] if len(parts) > 1 else '?'
            cur.hz = value(parts[2]) or 0 if len(parts) > 2 else 0
        elif rest.startswith('STOP'):
            if cur:
                cur.end = when
            cur = None
        else:
            notes.append((when, rest))
    return streams, notes


def classify(stream):
    """Each sample's kind, against the stream's median rate over the seconds anything was sent. The median."""
    rates = [s.f['kbps'] for s in stream.samples if s.f['kbps']]
    median = statistics.median(rates) if rates else 0
    for s in stream.samples:
        kbps, wq = s.f['kbps'], s.f['wq'] or 0
        if kbps is None or not median:
            s.kind = ''
        elif not kbps and not wq:
            s.kind = 'idle'
        elif kbps < BEHIND * median:
            s.kind = 'link' if wq else 'source'
        else:
            s.kind = ''
    return median


def episodes(stream):
    """Runs of seconds behind of one kind: (kind, first index, last index)."""
    out, i, ss = [], 0, stream.samples
    while i < len(ss):
        k = ss[i].kind
        if k in ('link', 'source'):
            j = i
            while j + 1 < len(ss) and ss[j + 1].kind == k:
                j += 1
            out.append((k, i, j))
            i = j + 1
        else:
            i += 1
    return out


def fmt(v):
    return '-' if v is None else str(v)


def row(s, out):
    mark = {'link': 'LINK', 'source': 'SRC', 'idle': 'idle'}.get(s.kind, '')
    print(f'{s.when:%H:%M:%S}  {mark:<4} ' + ' '.join(f'{fmt(s.f[k]):>5}' for k in SIGNALS), file=out)


def header(out):
    print('time      kind ' + ' '.join(f'{k:>5}' for k in SIGNALS), file=out)


def trend(window):
    """For each signal that moved in window, 'name first->last (min..max)'."""
    parts = []
    for k in SIGNALS:
        vals = [s.f[k] for s in window if s.f[k] is not None]
        if len(vals) >= 2 and min(vals) != max(vals):
            parts.append(f'{k} {vals[0]}->{vals[-1]} ({min(vals)}..{max(vals)})')
    return ', '.join(parts) or 'nothing moved'


def mean(vals):
    vals = [v for v in vals if v is not None]
    return sum(vals) / len(vals) if vals else None


def summary(streams):
    """Per codec: (codec, seconds, link seconds, source seconds, median kbps, median rssi, min rssi, mean own)."""
    by = {}
    for st in streams:
        by.setdefault(st.codec, []).extend(st.samples)
    out = []
    for codec, ss in by.items():
        live = [s for s in ss if s.kind != 'idle']
        rssi = [s.f['rssi'] for s in live if s.f['rssi'] is not None]
        kbps = [s.f['kbps'] for s in live if s.f['kbps']]
        out.append((codec, len(live), sum(s.kind == 'link' for s in live), sum(s.kind == 'source' for s in live),
                    statistics.median(kbps) if kbps else None, statistics.median(rssi) if rssi else None,
                    min(rssi) if rssi else None, mean(s.f['own'] for s in live)))
    return out


def report(streams, notes, out=None, every=False):
    out = out or sys.stdout
    for when, text in notes:
        print(f'{when:%Y-%m-%d %H:%M:%S}  ** {text}', file=out)
    if not streams:
        print('No stream in the log.', file=out)
        return []
    found = []
    for st in streams:
        median = classify(st)
        end = f'{st.end:%H:%M:%S}' if st.end else 'log end'
        hz = f' {st.hz} Hz' if st.hz else ''
        print(f'\nStream {st.n}: {st.codec}{hz}, {st.start:%Y-%m-%d %H:%M:%S} to {end}, {len(st.samples)} s, '
              f'median {median:g} kbps ({st.head})', file=out)
        eps = episodes(st)
        found += [(st, e) for e in eps]
        if every:
            header(out)
            for s in st.samples:
                row(s, out)
            continue
        for kind, a, b in eps:
            first = st.samples[a]
            print(f'\n  {kind.upper()} stall {first.when:%H:%M:%S}, {b - a + 1} s; the {BEFORE} s before: '
                  f'{trend(st.samples[max(0, a - BEFORE):a + 1])}', file=out)
            header(out)
            for s in st.samples[max(0, a - BEFORE):b + 1]:
                row(s, out)
    print('\ncodec        seconds  link  source  kbps  rssi(med/min)  q2guard cpu', file=out)
    for codec, n, link, source, kbps, rssi, low, own in summary(streams):
        cpu = '-' if own is None else f'{own / 10:.2f}%'
        print(f'{codec:<12} {n:>7} {link:>5} {source:>7} {fmt(kbps):>5}  {fmt(rssi):>5}/{fmt(low):<6}  {cpu} of a core',
              file=out)
    return found


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    ap.add_argument('files', nargs='+', help='q2-bt.1 then q2-bt.0, or - for standard input')
    ap.add_argument('--all', action='store_true', help='every second of every stream')
    args = ap.parse_args(argv)
    lines = []
    for f in args.files:
        lines += sys.stdin.readlines() if f == '-' else open(f, encoding='utf-8', errors='replace').readlines()
    streams, notes = parse(lines)
    report(streams, notes, every=args.all)


if __name__ == '__main__':
    main()
