#!/usr/bin/env python3
"""ui/bluenoise.inc: the 64 x 64 blue-noise table q2ui dithers with (ui/draw.c, ui/fb.c dither64).

    uv run --with numpy python ui/tools/bluenoise.py [OUT]    # needs numpy; the table is committed, so a build does not

Made by filling voids: each next point goes where the points already placed are thinnest, by a
Gaussian (sigma 1.9) summed round the table's edges, so the pattern tiles and has no low
frequencies for the eye to see as blotches. A point's rank, 0 to 4095, over 16 is its value, so
every value from 0 to 255 is there 16 times and a threshold at any level lights that share of the
table. The first point's place is fixed, so the output is the same on every run."""
import pathlib
import sys

N, SIGMA = 64, 1.9


def table():
    import numpy as np
    d = np.minimum(np.arange(N), N - np.arange(N)).astype(np.float64)
    kernel = np.exp(-(d[:, None] ** 2 + d[None, :] ** 2) / (2 * SIGMA * SIGMA))
    energy = np.zeros((N, N))
    rank = np.zeros((N, N), dtype=np.int64)
    y = x = 21
    for k in range(N * N):
        if k:
            flat = int(np.argmin(energy))
            y, x = divmod(flat, N)
        rank[y, x] = k
        energy += np.roll(np.roll(kernel, y, 0), x, 1)
        energy[y, x] = np.inf
    return (rank // (N * N // 256)).astype(int)


def text(values):
    lines = ['/* ui/tools/bluenoise.py wrote this: 64 x 64 blue noise, each value 0 to 255 sixteen times. */']
    for row in values:
        lines.append(' '.join(f'{v},' for v in row))
    return '\n'.join(lines) + '\n'


if __name__ == '__main__':
    out = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else pathlib.Path(__file__).resolve().parent.parent/'bluenoise.inc')
    out.write_text(text(table()))
