#!/usr/bin/env python3
"""Read q2guard's Bluetooth link log (patch/q2bt.c, docs/internals.md#bluetooth-link-monitor) back as a timeline:
each stream with its codec, the seconds it fell behind, and what every signal did in the 10 s before each.

    python3 tools/q2bt.py q2-bt.1 q2-bt.0              # oldest file first, as the Q2 keeps them
    adb shell cat /mnt/mmc/.q2muse/logs/q2-bt.1 /mnt/mmc/.q2muse/logs/q2-bt.0 | python3 tools/q2bt.py -
    python3 tools/q2bt.py --all q2-bt.0                # every second, not only the stalls

A line is `TIME up=S kbps= acl= wq= rssi= lq= tpl= afh= err= wtx= wrx= cpu= ba= own= h= er= ew=`, one a second
while a stream runs (`ba` and `own` in tenths of a percent of one core, printed here as percent; `er` and `ew` in ms a
second); a note is `TIME WORD ...` (START, ENCODER, CODEC, LINK, STOP). The card store's `== N lines lost while the
card was away` is a GAP. A stream is cut into one segment per codec, at each ENCODER note (bluealsa's encoder thread,
named for its codec, seen within a second of a change) or CODEC note that names another codec; a CODEC note can come up
to a minute late (older logs have no ENCODER notes), so the seconds since the last gap in sending before it go to the
new codec. A second is behind when the controller was handed under BEHIND of its segment's median rate:
- **link**: and something waited in the A2DP socket (wq > 0). The kernel queues only when the controller's ACL
  buffers are full, so the radio was not getting packets to the headset fast enough: distance, the body, Wi-Fi.
- **encoder**: nothing waiting, and bluealsa's encoder thread was on a CPU for ENC_RUN ms of the second or waited
  ENC_WAIT ms for one: it could not make packets fast enough (the codec's cost, or the CPU taken by others).
- **source**: nothing waiting and the encoder mostly asleep, but some sent: it was waiting for PCM, the player late.
- **switch**: a slow second in the SWITCH_S before a codec change: the reconnect, not a stall.
A second with nothing sent and nothing waiting is idle (a pause, the transport let go), not a stall, and so is a slow
second with nothing waiting beside one (the pause's or the resume's edge)."""
import argparse
import datetime
import re
import statistics
import sys
from dataclasses import dataclass, field
from typing import Optional

TIME = re.compile(r'^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ) (.*)$')
LOST = re.compile(r'^== (\d+) lines lost while the card was away$')  # patch/card_store.c's note
BEHIND = 0.8   # a second under this share of its segment's median kbps is behind
BEFORE = 10    # seconds shown before each stall
ENC_RUN = 800  # ms a second on a CPU: the encoder could not keep up
ENC_WAIT = 100  # ms a second waiting for a CPU: the encoder was kept from one
SWITCH_S = 3   # seconds before a codec change whose slow seconds are the reconnect
SIGNALS = ('kbps', 'acl', 'wq', 'rssi', 'lq', 'tpl', 'afh', 'err', 'wtx', 'wrx', 'cpu', 'ba', 'own', 'h', 'er', 'ew')
TENTHS = ('ba', 'own')  # tenths of a percent of one core, printed as percent
ENCODERS = {'sbc': 'SBC', 'aac': 'AAC', 'aptx': 'aptX', 'aptx-hd': 'aptX-HD', 'ldac': 'LDAC'}  # ba-a2dp-<name>


@dataclass
class Sample:
    when: datetime.datetime
    up: int
    f: dict  # each of SIGNALS: an int, or None for "-"
    stream: int = 0
    kind: str = ''  # '', 'link', 'source' or 'idle'


@dataclass
class Stream:
    """One codec's run within a stream: a stream that changes codec is cut into several."""
    n: int
    start: datetime.datetime
    head: str = ''      # START's text, or what cut it from the one before
    codec: str = '?'
    hz: int = 0
    samples: list = field(default_factory=list)
    end: Optional[datetime.datetime] = None
    cut: bool = False   # it ended at a codec change, not a STOP
    timely: bool = False  # its codec came from an ENCODER note (on time), not only a CODEC note


def value(v):
    try:
        return int(v)
    except ValueError:
        return None


def median_rate(samples):
    rates = [s.f['kbps'] for s in samples if s.f['kbps']]
    return statistics.median(rates) if rates else 0


def change(streams, cur, codec, when, late):
    """cur cut at a change to codec: the new segment. late (a CODEC note, up to a minute after the change): the
    seconds since cur's last gap in sending (under a tenth of its median) in that minute move to the new segment."""
    nxt = Stream(len(streams) + 1, when, f'codec change from {cur.codec}', codec)
    if late and cur.samples:
        low = 0.1 * median_rate(cur.samples)
        gap = [i for i, s in enumerate(cur.samples) if (when - s.when).total_seconds() <= 60
               and s.f['kbps'] is not None and s.f['kbps'] <= low]
        if gap:
            nxt.samples, cur.samples = cur.samples[gap[-1] + 1:], cur.samples[:gap[-1] + 1]
            if nxt.samples:
                nxt.start = nxt.samples[0].when
    for s in nxt.samples:
        s.stream = nxt.n
    cur.end, cur.cut = nxt.start, True
    streams.append(nxt)
    return nxt


def parse(lines):
    """The segments, in order, each with its samples; and the notes (time, text) that are not START, ENCODER, CODEC
    or STOP. A sample before any START (a log that begins mid-stream) opens a segment of its own."""
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
        parts = rest.split()
        if rest.startswith('up='):
            if cur is None:
                cur = Stream(len(streams) + 1, when, 'log begins mid-stream')
                streams.append(cur)
            kv = dict(item.split('=', 1) for item in parts if '=' in item)
            cur.samples.append(Sample(when, value(kv.get('up', '0')) or 0, {k: value(kv.get(k, '-')) for k in SIGNALS},
                                      cur.n))
        elif parts[0] == 'START':
            if cur:
                cur.end = when
            cur = Stream(len(streams) + 1, when, rest[5:].strip())
            streams.append(cur)
        elif parts[0] in ('ENCODER', 'CODEC') and cur and len(parts) > 1:
            timely = parts[0] == 'ENCODER'
            codec = ENCODERS.get(parts[1].removeprefix('ba-a2dp-'), parts[1]) if timely else parts[1]
            if cur.codec != codec and cur.codec != '?':
                cur = change(streams, cur, codec, when, late=not timely and not cur.timely)
            cur.codec, cur.timely = codec, cur.timely or timely
            if not timely:
                cur.hz = value(parts[2]) or 0 if len(parts) > 2 else 0
        elif parts[0] == 'STOP':
            if cur:
                cur.end = when
            cur = None
        else:
            notes.append((when, rest))
    return streams, notes


def classify(stream):
    """Each sample's kind, against the segment's median rate over the seconds anything was sent. The median."""
    median = median_rate(stream.samples)
    n = len(stream.samples)
    for s in stream.samples:
        kbps, wq = s.f['kbps'], s.f['wq'] or 0
        er, ew = s.f['er'] or 0, s.f['ew'] or 0
        if kbps is None or not median:
            s.kind = ''
        elif not kbps and not wq:
            s.kind = 'idle'
        elif kbps >= BEHIND * median:
            s.kind = ''
        elif wq:
            s.kind = 'link'
        elif er >= ENC_RUN or ew >= ENC_WAIT:
            s.kind = 'encoder'
        else:
            s.kind = 'source'
    # the edge of a pause or a resume: a slow second with nothing queued beside an idle one
    edge = [i for i, s in enumerate(stream.samples) if s.kind in ('source', 'encoder') and any(
        0 <= j < n and stream.samples[j].kind == 'idle' for j in (i - 1, i + 1))]
    for i in edge:
        stream.samples[i].kind = 'idle'
    # a codec change's reconnect: the slow and idle seconds with nothing queued that end a segment cut by one (the
    # idle gap, then up to SWITCH_S slow seconds before it) and the slow ones up to SWITCH_S that begin the next
    quiet = ('idle', 'source', 'encoder')
    if stream.cut:
        i, slow = n - 1, 0
        while i >= 0 and stream.samples[i].kind in quiet and (stream.samples[i].kind == 'idle' or slow < SWITCH_S):
            slow += stream.samples[i].kind != 'idle'
            stream.samples[i].kind, i = 'switch', i - 1
    if stream.head.startswith('codec change'):
        for s in stream.samples[:SWITCH_S]:
            if s.kind not in quiet:
                break
            s.kind = 'switch'
    return median


def episodes(stream):
    """Runs of seconds behind of one kind: (kind, first index, last index)."""
    out, i, ss = [], 0, stream.samples
    while i < len(ss):
        k = ss[i].kind
        if k in ('link', 'encoder', 'source'):
            j = i
            while j + 1 < len(ss) and ss[j + 1].kind == k:
                j += 1
            out.append((k, i, j))
            i = j + 1
        else:
            i += 1
    return out


def fmt(v, key=''):
    if v is None:
        return '-'
    return f'{v / 10:.1f}' if key in TENTHS else str(v)


def row(s, out):
    mark = {'link': 'LINK', 'encoder': 'ENC', 'source': 'SRC', 'switch': 'swch', 'idle': 'idle'}.get(s.kind, '')
    print(f'{s.when:%H:%M:%S}  {mark:<4} ' + ' '.join(f'{fmt(s.f[k], k):>5}' for k in SIGNALS), file=out)


def header(out):
    print('time      kind ' + ' '.join(f'{k:>5}' for k in SIGNALS), file=out)


def trend(window):
    """For each signal that moved in window, 'name first->last (min..max)'."""
    parts = []
    for k in SIGNALS:
        vals = [s.f[k] for s in window if s.f[k] is not None]
        if len(vals) >= 2 and min(vals) != max(vals):
            parts.append(f'{k} {fmt(vals[0], k)}->{fmt(vals[-1], k)} ({fmt(min(vals), k)}..{fmt(max(vals), k)})')
    return ', '.join(parts) or 'nothing moved'


def mean(vals):
    vals = [v for v in vals if v is not None]
    return sum(vals) / len(vals) if vals else None


def summary(streams):
    """Per codec: (codec, seconds, link, encoder and source stall seconds, seconds with the socket's queue backed up,
    median kbps, median and lowest rssi, bluealsa's and q2guard's mean CPU in percent of one core)."""
    by = {}
    for st in streams:
        by.setdefault(st.codec, []).extend(st.samples)
    out = []
    for codec, ss in by.items():
        live = [s for s in ss if s.kind not in ('idle', 'switch')]
        rssi = [s.f['rssi'] for s in live if s.f['rssi'] is not None]
        kbps = [s.f['kbps'] for s in live if s.f['kbps']]
        ba, own = mean(s.f['ba'] for s in live), mean(s.f['own'] for s in live)
        out.append((codec, len(live), *(sum(s.kind == k for s in live) for k in ('link', 'encoder', 'source')),
                    sum(bool(s.f['wq']) for s in live), statistics.median(kbps) if kbps else None,
                    statistics.median(rssi) if rssi else None, min(rssi) if rssi else None,
                    None if ba is None else ba / 10, None if own is None else own / 10))
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
        print(f'\nSegment {st.n}: {st.codec}{hz}, {st.start:%Y-%m-%d %H:%M:%S} to {end}, {len(st.samples)} s, '
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
    print('\ncodec      seconds  link  enc  source  queued  kbps  rssi(med/min)  bluealsa  q2guard (% of a core)', file=out)
    for codec, n, link, enc, source, queued, kbps, rssi, low, ba, own in summary(streams):
        pct = [('-' if v is None else f'{v:.1f}%') for v in (ba, own)]
        print(f'{codec:<10} {n:>7} {link:>5} {enc:>4} {source:>7} {queued:>7} {fmt(kbps):>5}  {fmt(rssi):>5}/{fmt(low):<6}  '
              f'{pct[0]:>8}  {pct[1]:>7}', file=out)
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
