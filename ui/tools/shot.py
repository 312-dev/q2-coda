#!/usr/bin/env python3
"""A panel file (two 320 x 375 BGRA pages, as /dev/fb0 and the emulator's state/fb0 are) as an upright
375 x 320 PNG: page PAGE turned back a quarter.

    python3 ui/tools/shot.py FB PAGE OUT.png
"""
import sys

from PIL import Image

W, H = 320, 375


def upright(fb, page):
    data = open(fb, 'rb').read()[page * W * H * 4:(page + 1) * W * H * 4]
    # canvas (x, y) is the page's (319 - y, x): the page turned a quarter anticlockwise
    return Image.frombuffer('RGBA', (W, H), data, 'raw', 'BGRA', 0, 1).convert('RGB').transpose(Image.Transpose.ROTATE_90)


if __name__ == '__main__':
    if len(sys.argv) != 4:
        sys.exit(__doc__)
    upright(sys.argv[1], int(sys.argv[2])).save(sys.argv[3])
