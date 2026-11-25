#!/usr/bin/env python3
"""Read the Wi-Fi fault recorder's log (patch/q2wifi.c, docs/internals.md#wi-fi-fault-recorder) back: each boot's
check of wlan0 (with the kernel's failure lines when it was missing), each deaf period with its start, length and
cause of its end and its snapshots' key fields side by side, and the associations with their reasons.

    python3 tools/q2wifi.py q2-wifi.3 q2-wifi.2 q2-wifi.1 q2-wifi.0   # oldest file first, as the Q2 keeps them
    adb exec-out 'cd /mnt/mmc/.q2muse/logs; cat q2-wifi.3 q2-wifi.2 q2-wifi.1 q2-wifi.0 2>/dev/null' | python3 tools/q2wifi.py -

A line is `TIME up=S TAG ...`: BOOT, K (a kernel line), F (a file's line), WPA, DEAF, SNAP, ASSOC, DISASSOC and
wpa_supplicant's other events. Lines that are none of these are skipped."""
import argparse
import re
import sys
from dataclasses import dataclass, field
from typing import Optional

LINE = re.compile(r'^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ) up=(\d+) (\S+) ?(.*)$')
KV = re.compile(r'(\w[\w.]*)=(\S+)')
# IEEE 802.11's reason codes as wpa_supplicant's CTRL-EVENT-DISCONNECTED gives them
REASONS = {1: 'unspecified', 2: 'authentication no longer valid', 3: 'deauthenticated: station leaving',
           4: 'inactivity', 5: 'AP cannot handle more stations', 6: 'class 2 frame from an unauthenticated station',
           7: 'class 3 frame from an unassociated station', 8: 'disassociated: station leaving',
           9: 'not authenticated', 14: 'MIC failure', 15: '4-way handshake timeout', 16: 'group key timeout',
           23: '802.1X authentication failed', 34: 'poor channel conditions'}


@dataclass
class Snap:
    when: str
    up: int
    what: str
    fields: dict = field(default_factory=dict)


@dataclass
class Period:
    kind: str
    start: Snap
    snaps: list = field(default_factory=list)
    end: Optional[str] = None
    lasted: Optional[int] = None


@dataclass
class Boot:
    when: str
    up: int
    text: str
    kernel: list = field(default_factory=list)


def kv(text):
    return dict(KV.findall(text))


def parse(lines):
    """The log's boots, deaf periods and association lines, in order."""
    boots, periods, assoc = [], [], []
    period, snap, boot = None, None, None
    for raw in lines:
        m = LINE.match(raw.rstrip('\n'))
        if not m:
            continue
        when, up, tag, rest = m.group(1), int(m.group(2)), m.group(3), m.group(4)
        if tag == 'BOOT':
            boot = Boot(when, up, rest)
            boots.append(boot)
        elif tag == 'K' and boot:
            boot.kernel.append(rest)
        elif tag == 'DEAF':
            word, _, more = rest.partition(' ')
            snap = Snap(when, up, word, kv(more))
            if word == 'start':
                period = Period(snap.fields.get('kind', '?'), snap)
                periods.append(period)
            elif period:
                if word == 'end':
                    period.end, period.lasted = snap.fields.get('by'), int(snap.fields.get('lasted', '0'))
                period.snaps.append(snap)
                if word == 'end':
                    period = None
        elif tag in ('SNAP', 'F') and snap:
            name, _, more = rest.partition(' ')
            if tag == 'SNAP':
                snap.fields.update({f'{name}.{k}': v for k, v in kv(more).items()})
            elif name == 'irq':
                c = re.match(r'\d+:\s+(\d+)', more)
                if c:
                    snap.fields['irq'] = int(c.group(1))
            elif name == 'mem':
                snap.fields.update(kv(more))
            elif name == 'temp':  # q2guard's reading in tenths of a degree, before the heat line it quotes
                t = int(kv(more.split(' heat=')[0]).get('t', '-1'))
                snap.fields['t'] = f'{t / 10:g}' if t >= 0 else '-'
        elif tag in ('ASSOC', 'DISASSOC'):
            assoc.append((when, up, tag, kv(rest), rest))
    return boots, periods, assoc


def report(boots, periods, assoc, out=None):
    out = out or sys.stdout
    print('Boots', file=out)
    if not boots:
        print('  none in the log', file=out)
    for b in boots:
        print(f'  {b.when}  up {b.up} s  {b.text}', file=out)
        for k in b.kernel:
            if re.search(r'allocation failure|vmalloc|8733|mmc1|sdio|SDIO|Call Trace', k):
                print(f'      {k}', file=out)
    print(file=out)
    print('Deaf periods', file=out)
    if not periods:
        print('  none', file=out)
    for i, p in enumerate(periods, 1):
        how = f'lasted {p.lasted} s, ended by {p.end}' if p.end else 'not ended in the log'
        print(f'  {i}. {p.start.when}  up {p.start.up} s  kind {p.kind}  {how}', file=out)
        print(f'     {"time":<20} {"up":>6} {"what":<6} {"state":<13} {"nets":>4} {"2.4":>7} {"5":>7} '
              f'{"scans":>5} {"fail":>4} {"irq":>9} {"+irq":>7} {"free kB":>7} {"t":>5}', file=out)
        last_irq = None
        for s in [p.start] + p.snaps:
            f = s.fields
            irq = f.get('irq')
            step = '-' if irq is None or last_irq is None else f'{irq - last_irq:+d}'
            last_irq = irq if irq is not None else last_irq
            print(f'     {s.when:<20} {s.up:>6} {s.what:<6} {f.get("status.state", f.get("state", "-")):<13} '
                  f'{f.get("scan.nets", f.get("nets", "-")):>4} {f.get("scan.2.4", f.get("2.4", "-")):>7} '
                  f'{f.get("scan.5", f.get("5", "-")):>7} {f.get("scan.scans", "-"):>5} {f.get("scan.failed", "-"):>4} '
                  f'{"-" if irq is None else irq:>9} {step:>7} {f.get("MemFree", "-"):>7} {f.get("t", "-"):>5}',
                  file=out)
    print(file=out)
    print('Associations', file=out)
    if not assoc:
        print('  none', file=out)
    for when, up, tag, f, rest in assoc:
        if tag == 'ASSOC':
            down = f' after {f["down"]} s down' if 'down' in f else ''
            print(f'  {when}  up {up:>6} s  joined {f.get("ap", "?")} on {f.get("freq", "-")} MHz{down}', file=out)
        else:
            r = int(f.get('reason', '-1'))
            who = 'by the Q2' if f.get('locally_generated') == '1' else 'by the AP'
            held = f' after {f["up"]} s joined' if 'up' in f else ''
            drv = rest.split(' drv=', 1)[1] if ' drv=' in rest else '-'
            print(f'  {when}  up {up:>6} s  left, reason {r} ({REASONS.get(r, "other")}), {who}{held}; driver: {drv}',
                  file=out)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    ap.add_argument('files', nargs='+', help='q2-wifi.3 to q2-wifi.0, oldest first, or - for standard input')
    args = ap.parse_args(argv)
    lines = []
    for f in args.files:
        lines += sys.stdin.readlines() if f == '-' else open(f, encoding='utf-8', errors='replace').readlines()
    report(*parse(lines))


if __name__ == '__main__':
    main()
