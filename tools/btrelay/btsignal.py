#!/usr/bin/env python3
"""btsignal: the Bluetooth relay's test signal, a WAV that wavcheck.py knows how to read back.
See docs/bt-relay.md#checking-the-audio.

usage: btsignal.py OUT.wav [--rate 44100|48000|...] [--bits 16|24]

SECONDS long, stereo: silence, then TONE_S of a sine in each channel (LEFT_HZ left, RIGHT_HZ right, so swapped
channels show) at TONE_DBFS, silence, then NOISE_S of white noise (the same in both channels) whose upper edge
after a codec is the band the codec kept. The noise is seeded, so the file is the same every time."""
import functools
import math
import sys

import wavcheck

LEFT_HZ, RIGHT_HZ = 1000.0, 1500.0
TONE_DBFS = -6.0
NOISE_DBFS = -12.0        # the noise's peak
TONE_AT, TONE_S = 0.5, 4.0
NOISE_AT, NOISE_S = 5.0, 3.0
SECONDS = 8.5
FADE_S = 0.005            # each section's raised-cosine edges, so a codec sees no click


def _gain(t, start, length):
    edge = min(t - start, start + length - t)
    if edge <= 0:
        return 0.0
    return 1.0 if edge >= FADE_S else 0.5 - 0.5 * math.cos(math.pi * edge / FADE_S)


@functools.lru_cache(maxsize=4)
def samples(rate):
    """(left, right), each a list of floats in -1..1. Cached: do not change the lists."""
    tone, noise = 10 ** (TONE_DBFS / 20), 10 ** (NOISE_DBFS / 20)
    left, right, seed = [], [], 0x2545f491
    for n in range(int(SECONDS * rate)):
        t = n / rate
        if TONE_AT <= t < TONE_AT + TONE_S:
            g = tone * _gain(t, TONE_AT, TONE_S)
            left.append(g * math.sin(2 * math.pi * LEFT_HZ * (t - TONE_AT)))
            right.append(g * math.sin(2 * math.pi * RIGHT_HZ * (t - TONE_AT)))
        elif NOISE_AT <= t < NOISE_AT + NOISE_S:
            seed = (seed * 1664525 + 1013904223) & 0xffffffff  # Numerical Recipes' linear congruential generator
            v = noise * _gain(t, NOISE_AT, NOISE_S) * (seed / 0x80000000 - 1.0)
            left.append(v)
            right.append(v)
        else:
            left.append(0.0)
            right.append(0.0)
    return left, right


def wav(rate, bits):
    if bits not in (16, 24):
        raise ValueError('--bits is 16 or 24')
    return wavcheck.encode(samples(rate), rate, bits)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    opts = {'--rate': 48000, '--bits': 16}
    while len(argv) >= 3 and argv[-2] in opts:
        opts[argv[-2]] = int(argv[-1])
        argv = argv[:-2]
    if len(argv) != 1 or argv[0].startswith('-'):
        raise ValueError(__doc__[__doc__.index('usage:'):])
    with open(argv[0], 'wb') as f:
        f.write(wav(opts['--rate'], opts['--bits']))
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (ValueError, OSError) as e:
        print(f'btsignal: {e}', file=sys.stderr)
        sys.exit(2)
