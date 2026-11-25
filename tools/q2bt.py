#!/usr/bin/env python3
"""Read q2guard's Bluetooth link log (patch/q2bt.c, docs/internals.md#bluetooth-link-monitor) back as a timeline:
each stream with its codec, the seconds it fell behind, and what every signal did in the 10 s before each; then each
classic link the recorder saw, streaming or idle, with its RSSI before it went, and the Wi-Fi bands.

    python3 tools/q2bt.py q2-bt.3 q2-bt.2 q2-bt.1 q2-bt.0   # oldest file first, as the Q2 keeps them
    adb shell 'cd /mnt/mmc/.q2muse/logs; cat q2-bt.3 q2-bt.2 q2-bt.1 q2-bt.0 2>/dev/null' | python3 tools/q2bt.py -
    python3 tools/q2bt.py --all q2-bt.0                # every second, not only the stalls

A line is `TIME up=S kbps= brx= acl= wq= rssi= afh= wtx= wrx= cpu= ba= own= h= er= done= fly= bp= pl= dk= gw= ue=`,
one a second while a stream runs (`brx` kbit/s from the controller; `ba` and `own` in percent of one core, written in
tenths by older logs: 90 for 9.0; `er` in ms a second; `done` packets a second the controller delivered, `fly` packets in
it, so fly/done is how long one waits there; `bp` SBC's bitpool on the last frame; `pl` the player's CPU as `ba`, `dk` the
card's busy ms, `gw` 1 while the player waits for a song still arriving; `ue` the UART's framing, parity, break and
overrun counts in the second, `fe/pe/brk/oe/bo` or 0, read here as their sum). Logs before 2026-11-25 also have `lq`,
`tpl`, `err`, `ew`, `fcc`, `pr`, `pd` and `pw`, read when there. A note is `TIME WORD ...` (START, ENCODER, CODEC, LINK,
PLAY, STEP, UART, H5, PACE, STOP). The recorder's lines (`IDLE`, a link's second with no stream; `ACL` and `ACLINFO`,
each link every 10 s and once; `ACL gone`; `WIFI` and `COEX`, every minute) go to the links section, not the notes, but
for an `IDLE` second with UART errors. The card store's `== N lines lost while the card was away` is a GAP. A stream is cut into one segment per codec, at each ENCODER note (bluealsa's encoder thread,
named for its codec, seen within a second of a change) or CODEC note that names another codec; a CODEC note can come up
to a minute late (older logs have no ENCODER notes), so the seconds since the last gap in sending before it go to the
new codec. A second is behind when the controller was handed under BEHIND of its segment's median rate:
- **link**: and something waited in the A2DP socket (wq > 0). The kernel queues only when the controller's ACL
  buffers are full, so the radio was not getting packets to the headset fast enough: distance, the body, Wi-Fi.
- **encoder**: nothing waiting, and bluealsa's encoder thread was on a CPU for ENC_RUN ms of the second or waited
  ENC_WAIT ms for one: it could not make packets fast enough (the codec's cost, or the CPU taken by others).
- **source**: nothing waiting and the encoder mostly asleep, but some sent: it was waiting for PCM, the player late.
- **switch**: a slow second in the SWITCH_S before a codec change: the reconnect, not a stall.
Each segment with done and fly also gets its capacity: in the seconds the controller held all its ACL buffers (fly
at START's bufs) the radio was the limit, so the packets it delivered a second are what the link carries, set against
what basic rate (DH5, DH3, DH1) and EDR 2 Mbps (2-DH5, 2-DH3, 2-DH1) can carry for packets of that size.
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
SIGNALS = ('kbps', 'brx', 'acl', 'wq', 'rssi', 'afh', 'wtx', 'wrx', 'cpu', 'ba', 'own', 'h', 'er', 'done', 'fly', 'bp',
           'pl', 'dk', 'gw', 'ue',
           # older logs' (before 2026-11-25): read when there, shown only where a segment has them
           'lq', 'tpl', 'err', 'ew', 'fcc', 'pr', 'pd', 'pw')
RECORDER = ('IDLE', 'ACL', 'ACLINFO', 'WIFI', 'COEX')  # the recorder's lines: the links section, not the notes
TENTHS = ('ba', 'own', 'pl')  # percent of one core, held in tenths (older logs wrote the tenths: 90 for 9.0)
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
    bufs: int = 0         # the controller's ACL buffers, from START's bufs=


def value(v, key=''):
    """A field as an int, None for "-"; ba and own in tenths of a percent, whether written 9.0 or (older logs) 90; ue
    the sum of its five counts."""
    if key == 'ue' and '/' in v:
        parts = [value(x) for x in v.split('/')]
        return None if None in parts else sum(parts)
    try:
        return round(float(v) * 10) if key in TENTHS and '.' in v else int(v)
    except ValueError:
        return None


def median_rate(samples):
    rates = [s.f['kbps'] for s in samples if s.f['kbps']]
    return statistics.median(rates) if rates else 0


def change(streams, cur, codec, when, late):
    """cur cut at a change to codec: the new segment. late (a CODEC note, up to a minute after the change): the
    seconds since cur's last gap in sending (under a tenth of its median) in that minute move to the new segment."""
    nxt = Stream(len(streams) + 1, when, f'codec change from {cur.codec}', codec, bufs=cur.bufs)
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
            cur.samples.append(Sample(when, value(kv.get('up', '0')) or 0, {k: value(kv.get(k, '-'), k) for k in SIGNALS},
                                      cur.n))
        elif parts[0] == 'START':
            if cur:
                cur.end = when
            head = rest[5:].strip()
            bufs = re.search(r'\bbufs=(\d+)', head)
            cur = Stream(len(streams) + 1, when, head, bufs=int(bufs[1]) if bufs else 0)
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
        elif parts[0] == 'IDLE':
            ue = re.search(r'\bue=([\d/]+)', rest)
            if ue and value(ue[1], 'ue'):
                notes.append((when, f'UART errors on an idle link: ue={ue[1]} (fe/pe/brk/oe/bo)'))
        elif parts[0] in RECORDER and parts[:2] != ['ACL', 'gone']:
            continue
        else:
            notes.append((when, rest))
    return streams, notes


@dataclass
class Link:
    """One classic link the recorder saw (its handle and address), from its first ACL line to its last."""
    h: int
    dev: str
    first: datetime.datetime
    last: datetime.datetime
    info: str = ''        # ACLINFO's fields
    rssi: list = field(default_factory=list)  # (time, rssi), every 10 s
    gone: Optional[datetime.datetime] = None


def recorder(lines):
    """The recorder's lines: each link in the order it came, and the Wi-Fi bands as (time, band and channel) when it
    changed."""
    links, bands, by = [], [], {}
    for raw in lines:
        m = TIME.match(raw.rstrip('\n'))
        if not m:
            continue
        when = datetime.datetime.strptime(m[1], '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=datetime.timezone.utc)
        parts = m[2].split()
        kv = dict(x.split('=', 1) for x in parts if '=' in x)
        if parts[0] in ('ACL', 'ACLINFO') and 'h' in kv:
            key = (kv['h'], kv.get('dev', '-'))
            ln = by.get(key)
            if ln is None or ln.gone:
                ln = by[key] = Link(int(kv['h']), kv.get('dev', '-'), when, when)
                links.append(ln)
            ln.last = when
            if parts[0] == 'ACLINFO':
                ln.info = ' '.join(x for x in parts[1:] if not x.startswith(('h=', 'dev=')))
            elif parts[1] != 'gone':
                ln.rssi.append((when, value(kv.get('rssi', '-'))))
            else:
                ln.gone = when
        elif parts[0] == 'WIFI':
            band = f"{kv.get('band', '-')} GHz ch {kv.get('ch', '-')}" if kv.get('band', '-') != '-' else 'not joined'
            if not bands or bands[-1][1] != band:
                bands.append((when, band))
    return links, bands


def report_links(links, bands, out):
    if not links and not bands:
        return
    print('\nLinks (the recorder: streaming or idle, RSSI every 10 s)', file=out)
    for ln in links:
        vals = [v for _, v in ln.rssi if v is not None]
        rssi = f'rssi median {statistics.median(vals):g}, lowest {min(vals)}' if vals else 'rssi not read'
        end = f'gone {ln.gone:%H:%M:%S}' if ln.gone else f'last seen {ln.last:%H:%M:%S}'
        print(f'  {ln.dev} h={ln.h}: {ln.first:%Y-%m-%d %H:%M:%S} to {end}; {ln.info or "no ACLINFO"}; {rssi}', file=out)
        if ln.gone and ln.rssi:
            tail = ' '.join(fmt(v) for _, v in ln.rssi[-6:])
            print(f'    the last minute before it went: rssi {tail}', file=out)
    for when, band in bands:
        print(f'  Wi-Fi {band} from {when:%Y-%m-%d %H:%M:%S}', file=out)


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


def shown(samples):
    """The signals any of these seconds has: a log's own fields, an older log's dead ones left out of a newer's."""
    return [k for k in SIGNALS if any(s.f[k] is not None for s in samples)] or list(SIGNALS[:20])


def row(s, out, cols=SIGNALS):
    mark = {'link': 'LINK', 'encoder': 'ENC', 'source': 'SRC', 'switch': 'swch', 'idle': 'idle'}.get(s.kind, '')
    print(f'{s.when:%H:%M:%S}  {mark:<4} ' + ' '.join(f'{fmt(s.f[k], k):>5}' for k in cols), file=out)


def header(out, cols=SIGNALS):
    print('time      kind ' + ' '.join(f'{k:>5}' for k in cols), file=out)


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


# Baseband packets for an ACL packet's bytes, (payload, slots) largest first: basic rate's DH5, DH3, DH1 and EDR
# 2 Mbps's 2-DH5, 2-DH3, 2-DH1 (the Q2 has no 3 Mbps, docs/internals.md#bluetooth-link-monitor). Each is followed
# by the headset's own slot, so a second's 1600 slots carry 1600 / slots(n) packets of n bytes at most.
BASIC = ((339, 5), (183, 3), (27, 1))
EDR2 = ((679, 5), (367, 3), (54, 1))
SLOTS_S = 1600
OVERHEAD = 10  # bytes a packet carries to the controller beyond its baseband payload: HCI's and H5's headers


def slots(n, kinds):
    """Slots n bytes take, cut largest first, the last piece in the smallest packet that holds it."""
    used = 0
    while n > 0:
        fits = [k for k in kinds if k[0] >= n]
        payload, s = min(fits) if fits else kinds[0]
        used, n = used + s + 1, n - payload
    return used


@dataclass
class Capacity:
    seconds: int          # seconds with all the controller's ACL buffers full
    median: float         # packets a second it delivered then
    maximum: int
    payload: int          # bytes a packet, from the segment's rate and packet count
    ceiling_basic: int    # the most a second at basic rate, and at EDR 2 Mbps, for packets of that size
    ceiling_edr: int
    verdict: str


def capacity(stream):
    """What the link carried while the controller was full: with all its buffers taken (fly at bufs) the radio is the
    limit, so the packets it delivered a second (done) are its capacity, set against the basic-rate and EDR ceilings
    for packets of this size. None for a segment with no done and fly (a log from before them)."""
    if not stream.bufs or not any(s.f['done'] is not None and s.f['fly'] is not None for s in stream.samples):
        return None
    sizes = [s.f['kbps'] * 125 / s.f['acl'] for s in stream.samples if s.f['kbps'] and s.f['acl']]
    payload = round(statistics.median(sizes)) - OVERHEAD if sizes else 0
    basic = SLOTS_S // slots(payload, BASIC) if payload > 0 else 0
    edr = SLOTS_S // slots(payload, EDR2) if payload > 0 else 0
    full = [s.f['done'] for s in stream.samples if s.f['fly'] is not None and s.f['done'] is not None
            and s.f['fly'] >= stream.bufs]
    if len(full) < 5 or not basic:
        return Capacity(len(full), statistics.median(full) if full else 0, max(full, default=0), payload, basic, edr,
                        'too few saturated seconds')
    median = statistics.median(full)
    verdict = ('basic rate' if median <= 1.1 * basic else 'EDR 2 Mbps' if median > (basic + edr) / 2
               else 'undecided')
    return Capacity(len(full), median, max(full), payload, basic, edr, verdict)


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
        cap = capacity(st)
        if cap and cap.seconds:
            print(f'  saturated: {cap.seconds} s with all {st.bufs} ACL buffers full, the controller delivered '
                  f'{cap.median:g} packets/s (median; most {cap.maximum}) of about {cap.payload} bytes; for those '
                  f'basic rate carries {cap.ceiling_basic}/s at most, EDR 2 Mbps {cap.ceiling_edr}/s: {cap.verdict}',
                  file=out)
        eps = episodes(st)
        found += [(st, e) for e in eps]
        cols = shown(st.samples)
        if every:
            header(out, cols)
            for s in st.samples:
                row(s, out, cols)
            continue
        for kind, a, b in eps:
            first = st.samples[a]
            print(f'\n  {kind.upper()} stall {first.when:%H:%M:%S}, {b - a + 1} s; the {BEFORE} s before: '
                  f'{trend(st.samples[max(0, a - BEFORE):a + 1])}', file=out)
            header(out, cols)
            for s in st.samples[max(0, a - BEFORE):b + 1]:
                row(s, out, cols)
    print('\ncodec      seconds  link  enc  source  queued  kbps  rssi(med/min)  bluealsa  q2guard (% of a core)', file=out)
    for codec, n, link, enc, source, queued, kbps, rssi, low, ba, own in summary(streams):
        pct = [('-' if v is None else f'{v:.1f}%') for v in (ba, own)]
        print(f'{codec:<10} {n:>7} {link:>5} {enc:>4} {source:>7} {queued:>7} {fmt(kbps):>5}  {fmt(rssi):>5}/{fmt(low):<6}  '
              f'{pct[0]:>8}  {pct[1]:>7}', file=out)
    return found


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    ap.add_argument('files', nargs='+', help='q2-bt.3 to q2-bt.0, oldest first, or - for standard input')
    ap.add_argument('--all', action='store_true', help='every second of every stream')
    args = ap.parse_args(argv)
    lines = []
    for f in args.files:
        lines += sys.stdin.readlines() if f == '-' else open(f, encoding='utf-8', errors='replace').readlines()
    streams, notes = parse(lines)
    report(streams, notes, every=args.all)
    links, bands = recorder(lines)
    report_links(links, bands, sys.stdout)


if __name__ == '__main__':
    main()
