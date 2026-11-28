#!/usr/bin/env python3
"""A panel file (two 320 x 375 BGRA pages, as /dev/fb0 and the emulator's state/fb0 are) as an upright
375 x 320 picture: page PAGE turned back a quarter.

    python3 ui/tools/shot.py FB PAGE OUT.png    # needs Pillow; `page` and `pixel` below need nothing
"""
import sys

W, H = 320, 375  # the panel's; the picture is H wide and W high


def page(fb, n):
    """Page n's bytes, as the panel holds them."""
    with open(fb, 'rb') as f:
        f.seek(n * W * H * 4)
        return f.read(W * H * 4)


def pixel(data, x, y):
    """The picture's (x, y) as (r, g, b): canvas (x, y) is the page's (319 - y, x)."""
    at = (x * W + (W - 1 - y)) * 4
    return data[at + 2], data[at + 1], data[at]


if __name__ == '__main__':
    if len(sys.argv) != 4:
        sys.exit(__doc__)
    from PIL import Image
    data = page(sys.argv[1], int(sys.argv[2]))
    # the page turned a quarter anticlockwise
    Image.frombuffer('RGBA', (W, H), data, 'raw', 'BGRA', 0, 1).convert('RGB').transpose(Image.Transpose.ROTATE_90).save(sys.argv[3])
