#!/usr/bin/env python3
"""Two screens of the same state, demo's and q2ui's, compared in numbers (docs/ui-rewrite.md#parity):

    python3 ui/tools/compare.py DEMO.png Q2UI.png [--top Y] [--pitch P] [--rows N] [--left X0 --right X1]

Prints, for the whole screen and for the status row's band (above y 58) and the page under it, the mean
absolute difference per channel and the share of pixels more than 24 apart; then where the selection's
bar is in each (the box of accent pixels); then, row by row from --top every --pitch px between x --left
and --right, the box of each row's ink (pixels brighter than 96 that are not the bar's blue) in each and
how far apart their edges are. A text row a pixel or two off reads as small deltas; a missing glyph or a
different font size as a width difference. Reads 8-bit RGB or RGBA PNGs (what `q2emu shot` writes) with
nothing but the standard library.
"""
import argparse
import struct
import sys
import zlib

ACCENT = (0x21, 0x60, 0xd8)


def png(path):
    """(w, h, rows of (r, g, b)) from a non-interlaced 8-bit RGB or RGBA PNG."""
    data = open(path, 'rb').read()
    if data[:8] != b'\x89PNG\r\n\x1a\n':
        sys.exit(f'{path}: not a PNG')
    at, idat, w = 8, b'', 0
    while at < len(data):
        n, kind = struct.unpack('>I4s', data[at:at + 8])
        body = data[at + 8:at + 8 + n]
        if kind == b'IHDR':
            w, h, depth, colour, _, _, lace = struct.unpack('>IIBBBBB', body)
            if depth != 8 or colour not in (2, 6) or lace:
                sys.exit(f'{path}: only 8-bit RGB or RGBA, not interlaced')
            bpp = 3 if colour == 2 else 4
        elif kind == b'IDAT':
            idat += body
        at += 12 + n
    raw, stride, prev, rows = zlib.decompress(idat), w * bpp, bytearray(w * bpp), []
    for y in range(h):
        f, line = raw[y * (stride + 1)], bytearray(raw[y * (stride + 1) + 1:(y + 1) * (stride + 1)])
        for i in range(stride):
            a = line[i - bpp] if i >= bpp else 0
            b, c = prev[i], prev[i - bpp] if i >= bpp else 0
            if f == 1:
                line[i] = (line[i] + a) & 255
            elif f == 2:
                line[i] = (line[i] + b) & 255
            elif f == 3:
                line[i] = (line[i] + (a + b) // 2) & 255
            elif f == 4:
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                line[i] = (line[i] + (a if pa <= pb and pa <= pc else b if pb <= pc else c)) & 255
        rows.append([tuple(line[x * bpp:x * bpp + 3]) for x in range(w)])
        prev = line
    return w, h, rows


def diff(a, b, y0, y1):
    total, far, n = [0, 0, 0], 0, 0
    for y in range(y0, y1):
        for p, q in zip(a[y], b[y]):
            d = [abs(p[k] - q[k]) for k in range(3)]
            total = [t + v for t, v in zip(total, d)]
            far += max(d) > 24
            n += 1
    return [round(t / n, 2) for t in total], round(100 * far / n, 2)


def accent(p):
    return abs(p[0] - ACCENT[0]) < 40 and abs(p[1] - ACCENT[1]) < 40 and abs(p[2] - ACCENT[2]) < 40 and p[2] > 150


def box(rows, x0, x1, y0, y1, test):
    xs, ys = [], []
    for y in range(y0, y1):
        for x in range(x0, x1):
            if test(rows[y][x]):
                xs.append(x), ys.append(y)
    return (min(xs), min(ys), max(xs), max(ys)) if xs else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('demo'), ap.add_argument('q2ui')
    ap.add_argument('--top', type=int, default=58), ap.add_argument('--pitch', type=int, default=32)
    ap.add_argument('--rows', type=int, default=7), ap.add_argument('--left', type=int, default=0)
    ap.add_argument('--right', type=int, default=375)
    o = ap.parse_args()
    (w, h, a), (w2, h2, b) = png(o.demo), png(o.q2ui)
    if (w, h) != (w2, h2):
        sys.exit(f'sizes differ: {w}x{h} and {w2}x{h2}')
    for name, y0, y1 in (('screen', 0, h), ('status row', 0, 58), ('page', 58, h)):
        mean, far = diff(a, b, y0, y1)
        print(f'{name:11} mean |d| r,g,b {mean}  far {far}%')
    bars = [box(r, o.left, o.right, 0, h, accent) for r in (a, b)]
    print(f'bar         demo {bars[0]}  q2ui {bars[1]}')
    ink = lambda p: max(p) > 96 and not accent(p)  # noqa: E731
    for k in range(o.rows):
        y0 = o.top + k * o.pitch
        if y0 + o.pitch > h:
            break
        boxes = [box(r, o.left, o.right, y0, y0 + o.pitch, ink) for r in (a, b)]
        d = [q - p for p, q in zip(*boxes)] if all(boxes) else None
        print(f'row {k} y{y0:<4} demo {boxes[0]}  q2ui {boxes[1]}  q2ui-demo {d}')


if __name__ == '__main__':
    main()
