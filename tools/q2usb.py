#!/usr/bin/env python3
"""Read the USB record (patch/usb_record.c, docs/internals.md#usb-record) back, and flag what matters for the OTG
back-feed: the Q2 powering the bus (PC20) with a computer's VBUS or a computer's answer seen, power to a port with
nothing enumerated on it, and a plug that never enumerated.

    python3 tools/q2usb.py q2-usb.1.log q2-usb.log        # oldest file first, from the card's .q2pod folder
    adb shell cat /mnt/mmc/.q2pod/q2-usb.1.log /mnt/mmc/.q2pod/q2-usb.log | python3 tools/q2usb.py -

A line is `WALL MS EVENT PD18=.. PD17=.. PD19=.. PC20=.. PE22=.. otg=.. id=.. mode=.. conn=.. vbus=.. udc=.. hostdev=..`:
WALL is time()'s seconds, MS demo's millisecond clock, EVENT in, out, in+5s, out+5s, otg-on, otg-off or resume.
Other lines (the card store's `== N lines lost` note) are skipped. Exit 1 when anything is flagged."""
import argparse
import datetime
import re
import sys
from dataclasses import dataclass, field

LINE = re.compile(r'^(-?\d+) (\d+) (\S+)((?: \w+=\S*)+)$')
COMPUTER_MS = 3000  # a computer answering this soon after the bus power went off was on the cable while it was on
ENUMERATED = ('configured', 'addressed', 'default')


@dataclass
class Row:
    wall: int
    ms: int
    event: str
    f: dict = field(default_factory=dict)

    def when(self):
        if self.wall < 1600000000:  # a clock never set (patch/peq.h CLOCK_SET)
            return f'+{self.ms / 1000:.1f}s'
        return datetime.datetime.fromtimestamp(self.wall, datetime.timezone.utc).strftime('%Y-%m-%d %H:%M:%SZ')

    def computer(self):
        return self.f.get('vbus') == '1' or self.f.get('udc') in ENUMERATED


def parse(lines):
    rows = []
    for line in lines:
        m = LINE.match(line.rstrip('\n'))
        if m:
            rows.append(Row(int(m[1]), int(m[2]), m[3], dict(kv.split('=', 1) for kv in m[4].split())))
    return rows


def flags(rows):
    """[(row, text)] for each thing worth a look, in order."""
    out = []
    on = None  # the otg-on row while the Q2 powers the bus
    for i, r in enumerate(rows):
        if r.f.get('PC20') == '1' and r.computer():
            out.append((r, f'BACKFEED bus power on with a computer seen ({r.event}: vbus={r.f.get("vbus")} '
                           f'udc={r.f.get("udc")})'))
        if r.event == 'otg-on':
            on = r
        elif r.event == 'otg-off' and on is not None:
            held = (r.ms - on.ms) / 1000
            later = [x for x in rows[i + 1:] if 0 <= x.ms - r.ms <= COMPUTER_MS]
            if any(x.computer() or x.event == 'in' for x in later):
                out.append((on, f'BACKFEED bus power on {held:.1f}s, then a computer within {COMPUTER_MS // 1000}s '
                                f'of it going off'))
            elif on.f.get('hostdev', '?') == '?' and r.f.get('hostdev', '?') == '?':
                out.append((on, f'EMPTY bus power on {held:.1f}s with nothing enumerated on the host port'))
            on = None
        elif r.event == 'in+5s' and r.f.get('udc') not in ENUMERATED:
            out.append((r, f'NOENUM a plug not enumerated 5s on (udc={r.f.get("udc")}, vbus={r.f.get("vbus")}): '
                           f'a charger, or a plug that failed'))
        elif r.event == 'out+5s' and r.f.get('PD18') == '0':
            out.append((r, 'IDLOW the ID pin low 5s after an unplug'))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('files', nargs='+', help='record files, oldest first; - for stdin')
    args = ap.parse_args(argv)
    lines = []
    for name in args.files:
        lines += sys.stdin.readlines() if name == '-' else open(name, encoding='utf-8', errors='replace').readlines()
    rows = parse(lines)
    found = flags(rows)
    plugs = sum(r.event == 'in' for r in rows)
    print(f'{len(rows)} lines, {plugs} plugs, {sum(r.event == "otg-on" for r in rows)} times the Q2 powered the bus')
    for r, text in found:
        print(f'{r.when()}  {text}')
    if not found:
        print('nothing flagged')
    return 1 if found else 0


if __name__ == '__main__':
    sys.exit(main())
