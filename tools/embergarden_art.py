"""Ember Garden's art (patch/game_embergarden.c, docs/games-engine.md#ember-garden): the garden scene in its four
times of day, the sprites over it and the lettering, drawn by the design's own shapes and packed for the game.

The shapes, colours and places are the design's (the emulator project's design/games/sky-defense/src/sky.py and
kit.py, drawn there with Pillow at four times the panel's size and box-filtered down); the parts the game shows are
ported here. What it writes:

- assets/games/embergarden.bin, read once by the game when it opens (usr/share/q2games/embergarden/art.bin): the
  scene as one byte a pixel, indexes into a palette of its own for each band of BAND rows, with four colours an
  index, one for each time of day (dawn, day, dusk, night), found together (a k-means over the four colours as one
  point), so a time of day is a palette and a move between two is the palettes mixed; the scene's beds charred, the
  same way, as boxes the game copies over the scene's; the sprites, premultiplied 0xAARRGGBB, then each row's first
  and last-plus-one inked column (a byte each); the lettering, masks of 4 bits a pixel, coloured when drawn.
- patch/game_embergarden_art.h: the file's size, its parts' numbers and the scene's places, for the game.

The font is not in the repository (it is needed only to draw the lettering again): Baloo 2 (SIL OFL 1.1), from
Google Fonts; FONT names the file and the SHA-256 the committed art was drawn from.

usage: uvx -q --with pillow --with numpy python tools/embergarden_art.py FONT_DIR"""
import hashlib
import math
import pathlib
import random
import struct
import sys

import numpy as np
from PIL import Image, ImageDraw, ImageFont

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT_BIN, OUT_H = ROOT / 'assets/games/embergarden.bin', ROOT / 'patch/game_embergarden_art.h'
FONT = ('Baloo2.ttf', 'd47a6852548059b1db49a1319d06d499d546c3fa2237cf9eee9c43c8abb025c2')
MAGIC = 0x31414745  # "EGA1"
W, H, SS = 375, 320, 4
BAND, COLOURS = 32, 256  # a palette for each 32 rows, 256 colours in it
GLOW = 0.9  # the strength of the embers' and the fire's glow: the design's dusk (0.5 by day to 1.25 at night)
ORDER = ['dawn', 'day', 'dusk', 'night']

# ---------------------------------------------------------------------------------------------------- the canvas


def rgb(h):
    return tuple(int(h[i:i + 2], 16) for i in (1, 3, 5))


def hexc(t):
    return '#%02X%02X%02X' % tuple(max(0, min(255, round(v))) for v in t)


def mix(a, b, t):
    a, b = rgb(a), rgb(b)
    return hexc([a[i] + (b[i] - a[i]) * t for i in range(3)])


def mul(col, amb):
    c = rgb(col)
    return hexc([c[i] * amb[i] for i in range(3)])


_fonts: dict = {}
FONT_DIR = pathlib.Path('.')


def font(px):
    if px not in _fonts:
        f = ImageFont.truetype(str(FONT_DIR / FONT[0]), round(px * SS))
        f.set_variation_by_name('Bold')
        _fonts[px] = f
    return _fonts[px]


class C:
    """A canvas in panel pixels, drawn at SS times and box-filtered down."""

    def __init__(self, w=W, h=H, bg='#000000'):
        self.w, self.h = w, h
        self.im = Image.new('RGB', (w * SS, h * SS), bg)
        self.d = ImageDraw.Draw(self.im, 'RGBA')

    def _c(self, col, a=None):
        return col if a is None else rgb(col) + (int(max(0, min(255, a))),)

    def rect(self, x0, y0, x1, y1, col, a=None):
        self.d.rectangle([x0 * SS, y0 * SS, x1 * SS - 1, y1 * SS - 1], fill=self._c(col, a))

    def rr(self, x0, y0, x1, y1, r, col, a=None):
        self.d.rounded_rectangle([x0 * SS, y0 * SS, x1 * SS - 1, y1 * SS - 1], r * SS, fill=self._c(col, a))

    def ell(self, x, y, rx, ry=None, col='#FFFFFF', a=None):
        ry = rx if ry is None else ry
        self.d.ellipse([(x - rx) * SS, (y - ry) * SS, (x + rx) * SS, (y + ry) * SS], fill=self._c(col, a))

    def arc(self, x, y, r, a0, a1, col, w=1, a=None):
        self.d.arc([(x - r) * SS, (y - r) * SS, (x + r) * SS, (y + r) * SS], a0, a1, fill=self._c(col, a), width=round(w * SS))

    def poly(self, pts, col, a=None):
        self.d.polygon([(x * SS, y * SS) for x, y in pts], fill=self._c(col, a))

    def line(self, pts, col, w=1, a=None):
        self.d.line([(x * SS, y * SS) for x, y in pts], fill=self._c(col, a), width=max(1, round(w * SS)), joint='curve')

    def glow(self, x, y, r, col, a=60, n=6, ry=None):
        for i in range(n):
            k = 1 - i / n
            self.ell(x, y, r * k, None if ry is None else ry * k, col, a / n * 1.6)

    def text(self, x, y, s, px, col, anchor='mm', stroke=0, stroke_col=None):
        self.d.text((x * SS, y * SS), s, font=font(px), fill=col, anchor=anchor, stroke_width=round(stroke * SS),
                    stroke_fill=stroke_col)

    def out(self):
        return np.asarray(self.im.resize((self.w, self.h), Image.BOX)).astype(np.int32)


# ---------------------------------------------------------------------------------------------------- the scene
HORIZON, GROUND, SPR = 198, 258, (187.5, 270)
BEDS = [44, 79, 114, 149, 226, 261, 296, 331]
BED_W, BED_Y = 31, 252
TOD = {
    'dawn': dict(top='#4A5FA8', mid='#F0A0A8', hor='#FFE0B0', amb=(0.98, 0.86, 0.88), haze='#FFD8C0', stars=0.25,
                 sun=(64, 190, 17, '#FFF0C8', '#FFC890'), cone='#7A5A78', plume=('#5A4A64', '#B88A98'), glow=0.75,
                 sh=(9, 0.45), cloud='#FFC8C0'),
    'day': dict(top='#2F86D8', mid='#7EC4EE', hor='#D6F0F8', amb=(1.0, 1.0, 1.0), haze='#E4F4F8', stars=0.0,
                sun=(84, 62, 13, '#FFFFF0', '#FFF2A8'), cone='#6E6680', plume=('#5E5660', '#A89C9C'), glow=0.5,
                sh=(2.5, 0.5), cloud='#FFFFFF'),
    'dusk': dict(top='#35286E', mid='#D4507E', hor='#FFA050', amb=(0.96, 0.70, 0.62), haze='#FFA878', stars=0.45,
                 sun=(322, 189, 18, '#FFD890', '#FF7A3A'), cone='#5A3458', plume=('#3E2C48', '#C06A5E'), glow=0.9,
                 sh=(-9, 0.5), cloud='#FF9A70'),
    'night': dict(top='#070A22', mid='#161A46', hor='#33265A', amb=(0.40, 0.47, 0.72), haze='#3A2E66', stars=1.0,
                  sun=(92, 72, 10, '#F2F0FF', '#AAB4FF'), cone='#241A38', plume=('#1C1630', '#4A3050'), glow=1.25,
                  sh=(3, 0.25), cloud='#2A2A58'),
}
EMBER, EMBER_HOT, EMBER_CORE, LAVA = '#FF7A1A', '#FF9A2A', '#FFE27A', '#FF5A14'
PETALS = ['#FF5470', '#FFD23F', '#F15BB5', '#FFFFFF', '#A970F0', '#FF9F1C']


class S:
    """The design's scene (sky.py's S), the parts that do not move."""

    def __init__(self, tod, c=None):
        self.c = c or C()
        self.t = TOD[tod]
        self.tod = tod
        self.amb = self.t['amb']
        self.L = self.lit

    def lit(self, col):
        return mul(col, self.amb)

    def sky(self):
        c, t = self.c, self.t
        for y in range(0, HORIZON + 4):
            k = y / HORIZON
            col = mix(t['top'], t['mid'], min(1, k / 0.62)) if k < 0.62 else mix(t['mid'], t['hor'], (k - 0.62) / 0.38)
            c.rect(0, y, W, y + 1, col)
        rnd = random.Random(8)
        if t['stars']:
            for _ in range(70):
                x, y, r = rnd.uniform(0, W), rnd.uniform(0, 165), rnd.choice([0.4, 0.5, 0.7, 0.9])
                c.ell(x, y, r, col='#F4EEFF', a=210 * t['stars'] * (1 - y / 200))
        sx, sy, sr, core, halo = t['sun']
        if self.tod == 'night':
            c.glow(sx, sy, 26, halo, 60)
            c.ell(sx, sy, sr, col=core)
            c.ell(sx + 4.5, sy - 2.5, sr - 1.2, col=mix(t['top'], t['mid'], 0.45))
        else:
            c.glow(sx, sy, sr * 3.4, halo, 120, 8)
            c.ell(sx, sy, sr, col=core)
        if self.tod != 'night':
            for (x, y, w_) in [(40, 150, 36), (118, 168, 26), (300, 142, 40), (350, 170, 22)]:
                c.ell(x, y, w_, 3.2, t['cloud'], 150)
                c.ell(x - w_ * 0.3, y - 2.5, w_ * 0.5, 3, t['cloud'], 150)

    def volcano(self, pulse=0.5):
        c, t = self.c, self.t
        vx, vy = 214, 143
        cone, hz = t['cone'], t['haze']
        g = t['glow']
        dk, lt = t['plume']
        col_pts = [(vx + 1, vy - 6, 7), (vx - 1, vy - 18, 10), (vx - 6, vy - 31, 12.5), (vx - 13, vy - 45, 15), (vx - 22, vy - 59, 17.5),
                   (vx - 32, vy - 73, 20), (vx - 42, vy - 87, 22), (vx - 52, vy - 100, 24)]
        canopy = [(18, 22, 24), (52, 30, 22), (86, 24, 26), (122, 32, 23), (158, 25, 27), (196, 31, 24), (232, 23, 25), (268, 30, 22),
                  (302, 24, 24), (338, 30, 21), (366, 22, 22), (140, 44, 17), (190, 46, 15), (104, 46, 13), (236, 42, 13)]
        c.glow(vx, vy - 6, 38 + 6 * pulse, EMBER, 110 * g, 8, 30)
        for (x, y, r) in canopy:
            c.ell(x, y - 6, r + 1.5, col=dk)
        for (x, y, r) in col_pts:
            c.ell(x, y, r, col=dk)
        soft = mix(dk, lt, 0.26)
        for i, (x, y, r) in enumerate(canopy):
            c.ell(x - 4 + (i % 3) * 3, y + 1 + (i % 2) * 3, r * (0.5 + (i % 3) * 0.08), col=soft)
            c.ell(x + 6 - (i % 2) * 5, y + 6, r * 0.3, col=mix(dk, lt, 0.12))
        for (x, y, r) in col_pts:
            c.ell(x - r * 0.3, y - r * 0.25, r * 0.6, col=lt)
        for i, (x, y, r) in enumerate(col_pts[:4]):
            c.ell(x + r * 0.2, y + r * 0.35, r * 0.6, col=mix(dk, EMBER, 0.55 * g * (1 - i / 4) * (0.7 + 0.3 * pulse)))
        body, lit = mix(cone, hz, 0.34), mix(cone, hz, 0.56)
        base = HORIZON + 2
        c.poly([(vx - 7, vy), (vx + 7, vy), (vx + 24, vy + 25), (vx + 60, base), (vx - 57, base), (vx - 23, vy + 25)], body)
        c.poly([(vx - 7, vy), (vx - 2, vy), (vx - 12, vy + 25), (vx - 28, base), (vx - 57, base), (vx - 23, vy + 25)], lit)
        for k, pts in enumerate(([(vx - 3, vy + 1), (vx - 5, vy + 13), (vx - 3, vy + 25), (vx - 10, vy + 38), (vx - 8, vy + 51)],
                                 [(vx + 3, vy + 1), (vx + 6, vy + 12), (vx + 12, vy + 23), (vx + 14, vy + 36)],
                                 [(vx + 1, vy + 1), (vx + 1, vy + 10), (vx + 3, vy + 18)])):
            c.line(pts, EMBER, 3.0, 60 * g)
            c.line(pts, LAVA, 1.4)
            c.line(pts[:3 + (k == 0)], '#FFC04A', 0.7, 160 + 90 * pulse)
        c.ell(vx, vy, 7.6, 2.0, LAVA)
        c.ell(vx, vy, 5, 1.1, '#FFE27A')
        for a_, len_ in ((-1.95, 9), (-1.55, 13 + 3 * pulse), (-1.2, 8)):
            pts = [(vx + math.cos(a_) * s_ * 0.9, vy - 1 + math.sin(a_) * s_ + 0.02 * s_ * s_) for s_ in range(0, int(len_) + 1, 2)]
            c.line(pts, '#FFC04A', 1)

    def land(self):
        c, t, L = self.c, self.t, self.L
        hz = t['haze']
        c.ell(70, HORIZON + 6, 150, 13, mix(L('#5A7A8A'), hz, 0.62))
        c.ell(330, HORIZON + 7, 120, 12, mix(L('#5A7A8A'), hz, 0.62))
        for i in range(10):
            c.rect(0, HORIZON - 12 + i * 2.6, W, HORIZON - 12 + (i + 1) * 2.6, hz, 26 + i * 9)
        c.ell(250, HORIZON + 22, 190, 20, mix(L('#3F7A58'), hz, 0.34))
        c.ell(40, HORIZON + 24, 150, 18, mix(L('#3A7052'), hz, 0.26))
        rnd = random.Random(4)
        for i in range(26):
            x = rnd.uniform(0, W)
            y = HORIZON + 9 + rnd.uniform(0, 5) + (4 if 120 < x < 250 else 0)
            c.ell(x, y, 2.6, 3.4, mix(L('#2C5E44'), hz, 0.22))
        c.rect(0, HORIZON + 22, W, H, L('#4E9A46'))

    def recede(self, col, k=0.34):
        return mix(mul(col, self.amb), mul('#3E8450', self.amb), k)

    def hedge(self):
        c, L = self.c, self.L
        for i in range(15):
            x = -8 + i * 28
            r = 17 + (i * 7) % 5
            c.ell(x, 226 + (i % 3) * 2, r, r * 0.8, L('#2F7444'))
        for i in range(15):
            x = -8 + i * 28
            c.ell(x - 4, 219 + (i % 3) * 2, 9, 5, L('#3A8250'))
        c.rect(0, 228, W, 240, L('#2F7444'))
        for (x, y, r) in [(22, 203, 17), (354, 204, 17)]:
            c.rect(x - 1.5, y + r - 4, x + 1.5, y + r + 9, L('#4A3A2A'))
            c.ell(x, y, r, col=L('#2F7444'))
            c.ell(x - r * 0.25, y - r * 0.3, r * 0.6, col=L('#3A8250'))

    def lawn(self):
        c, L = self.c, self.L
        for y in range(237, H):
            k = (y - 237) / (H - 237)
            c.rect(0, y, W, y + 1, mix(L('#5CAE4A'), L('#3D8A3C'), k))
        for i in range(6):
            c.rect(0, 240 + i * 14, W, 247 + i * 14, L('#6CBC54'), 34)
        for (x, y) in [(187.5, 300), (176, 311), (199, 316)]:
            c.ell(x, y + 1, 9, 3.4, L('#3A7038'))
            c.ell(x, y, 9, 3.2, L('#B9B4A6'))
            c.ell(x - 1, y - 0.6, 6, 1.8, L('#D4CFC2'))

    def shadow(self, x, y, w_, h=3):
        dx, a = self.t['sh']
        self.c.ell(x + dx * 0.6, y + 0.5, w_ * (1 + abs(dx) * 0.08), h, '#0A2A14', 255 * a * 0.55)

    def sunflower(self, x, y, h=30, face=0.0, k_=1.0):
        c, L = self.c, self.L
        c.line([(x, y), (x + 1, y - h * 0.5), (x + face, y - h)], L('#3E8A3A'), 1.8)
        c.ell(x - 4, y - h * 0.38, 4.2, 2.2, L('#4EA048'))
        c.ell(x + 5, y - h * 0.58, 4.2, 2.2, L('#3E8A3A'))
        hx, hy = x + face, y - h
        for k in range(10):
            c.ell(hx + math.cos(k * 0.628) * 5.2 * k_, hy + math.sin(k * 0.628) * 5.2 * k_, 2.3 * k_, col=L('#FFC92E'))
        c.ell(hx, hy, 4.4 * k_, col=L('#FFE06A'))
        c.ell(hx, hy, 3.2 * k_, col=L('#6A3E1C'))

    def beanpole(self, x, y, h=30):
        c, L = self.c, self.L
        for dx in (-7, 0, 7):
            c.line([(x + dx, y), (x, y - h)], L('#B98A52'), 1.1)
        rnd = random.Random(int(x))
        for i in range(16):
            k = rnd.uniform(0.08, 0.92)
            xx = x + rnd.uniform(-7, 7) * (1 - k)
            c.ell(xx, y - h * k, 2.6, 2.2, L('#3C9244' if i % 3 else '#5CB45A'))
        for i in range(4):
            k = 0.2 + i * 0.2
            c.ell(x + (3 if i % 2 else -3) * (1 - k), y - h * k, 1.3, col=L('#FF5470'))

    def tulip(self, x, y, col, h=11):
        c, L = self.c, self.L
        c.line([(x, y), (x, y - h)], L('#3E8A3A'), 1.2)
        c.ell(x - 2, y - h * 0.35, 2.4, 1.2, L('#4EA048'))
        c.ell(x, y - h, 2.6, 3.0, L(col))
        c.poly([(x - 2.6, y - h - 1), (x - 1, y - h - 4.4), (x, y - h - 2), (x + 1, y - h - 4.4), (x + 2.6, y - h - 1)], L(col))

    def bush(self, x, y, r=9):
        c, L = self.c, self.L
        for (dx, dy, k) in [(-r * 0.7, 0, 0.7), (r * 0.7, 0.5, 0.72), (0, -r * 0.35, 1.0)]:
            c.ell(x + dx, y + dy - r * 0.5, r * k, col=L('#2F7E40'))
        c.ell(x - r * 0.25, y - r * 0.95, r * 0.55, col=L('#4CA452'))

    def crop(self, x, kind):
        c, L = self.c, self.L
        y = BED_Y + 7
        if kind == 'pumpkin':
            c.line([(x - 12, y - 1), (x - 4, y - 4), (x + 6, y - 2), (x + 12, y - 5)], L('#3C8E44'), 1.2)
            c.ell(x - 10, y - 3, 3.2, 2.2, L('#4EA048'))
            c.ell(x + 11, y - 6, 3, 2, L('#4EA048'))
            for (dx, r) in ((-4.5, 6.4), (6.5, 4.6)):
                c.ell(x + dx, y - r * 0.8, r, r * 0.82, L('#F5871F'))
                c.ell(x + dx, y - r * 0.8, r * 0.5, r * 0.82, L('#FFA63A'))
                c.ell(x + dx - r * 0.4, y - r * 1.1, r * 0.22, r * 0.3, L('#FFD08A'))
                c.rect(x + dx - 0.7, y - r * 1.75, x + dx + 0.7, y - r * 1.45, L('#4E7A2A'))
        elif kind == 'tomato':
            c.rect(x - 0.6, y - 19, x + 0.6, y, L('#B98A52'))
            for (dx, dy, r) in [(-5, -5, 5), (5, -6, 5), (0, -12, 5.5), (-3, -16, 3.6), (4, -15, 3.4)]:
                c.ell(x + dx, y + dy, r, col=L('#2F8A40'))
            c.ell(x - 1.5, y - 13, 3, col=L('#4EAA54'))
            for (dx, dy) in [(-6, -4), (5, -8), (-1, -9), (3, -2), (-4, -14)]:
                c.ell(x + dx, y + dy, 2.1, col=L('#F03A3A'))
                c.ell(x + dx - 0.6, y + dy - 0.6, 0.7, col=L('#FFB0A0'))
        elif kind == 'cabbage':
            for dx in (-7.5, 7.5):
                c.ell(x + dx, y - 5, 7, 5.6, L('#4C9E52'))
                c.ell(x + dx, y - 5.6, 5, 4.2, L('#86CC7A'))
                c.ell(x + dx - 0.6, y - 6.2, 2.6, 2.2, L('#C6EEAE'))
                c.arc(x + dx, y - 5, 5.6, 200, 320, L('#3C8646'), 0.8)
        elif kind == 'carrot':
            for dx in (-10, -3.5, 3.5, 10):
                for a_ in (-0.5, 0, 0.5):
                    c.line([(x + dx, y - 3), (x + dx + a_ * 6, y - 11 - (1 - abs(a_)) * 3)], L('#4EB04E'), 1.2)
                c.ell(x + dx, y - 2.6, 2.2, 1.8, L('#FF8A2A'))
        elif kind == 'strawberry':
            for dx in (-9, 0, 9):
                c.ell(x + dx, y - 4, 5.4, 3.6, L('#3A9448'))
                c.ell(x + dx - 1, y - 5, 3, 2, L('#5CB45A'))
                c.ell(x + dx - 2.6, y - 1.6, 1.6, 1.9, L('#F02A4A'))
                c.ell(x + dx + 2.8, y - 2.4, 1.5, 1.8, L('#F02A4A'))
                c.ell(x + dx + 0.5, y - 7, 1.1, col=L('#FFFFFF'))
        elif kind == 'lettuce':
            for dx in (-9, 0, 9):
                c.ell(x + dx, y - 4, 5, 4.4, L('#7CC850'))
                c.ell(x + dx, y - 4.8, 3.2, 2.8, L('#B4E87A'))
                c.arc(x + dx, y - 3.6, 4.6, 190, 350, L('#4E9E3C'), 0.8)
        elif kind == 'tulips':
            for i, dx in enumerate((-10, -5, 0, 5, 10)):
                self.tulip(x + dx, y - 1 - (i % 2), PETALS[(i * 2) % 6], 11 + (i % 2) * 2)
        elif kind == 'corn':
            for dx in (-8, 0, 8):
                c.line([(x + dx, y), (x + dx, y - 20)], L('#5AA844'), 1.6)
                for k in range(3):
                    yy = y - 5 - k * 5
                    c.line([(x + dx, yy), (x + dx - 5, yy - 4)], L('#4E9A40'), 1.3)
                    c.line([(x + dx, yy - 2), (x + dx + 5, yy - 6)], L('#6CBC54'), 1.3)
                c.ell(x + dx + 1.6, y - 10, 1.5, 3, L('#FFD84A'))

    def bed(self, x, kind, burnt):
        c, L = self.c, self.L
        w_ = BED_W / 2
        self.shadow(x, BED_Y + 20, w_ + 2, 2.6)
        c.rr(x - w_, BED_Y, x + w_, BED_Y + 13, 2, L('#8A5A32'))
        c.rr(x - w_ + 2, BED_Y + 1.5, x + w_ - 2, BED_Y + 11.5, 1.5, L('#2E1C14' if burnt else '#5A3A22'))
        for i in range(4):
            c.rect(x - w_ + 4, BED_Y + 3 + i * 2.6, x + w_ - 4, BED_Y + 4 + i * 2.6, L('#1E120C' if burnt else '#6E4A2C'))
        c.rr(x - w_, BED_Y + 12, x + w_, BED_Y + 20, 1.5, L('#A06A3A'))
        c.rect(x - w_ + 1, BED_Y + 13, x + w_ - 1, BED_Y + 14.2, L('#C08A52'))
        c.rect(x - w_ + 1, BED_Y + 16.4, x + w_ - 1, BED_Y + 17, L('#7A4E2A'))
        if burnt:
            c.rr(x - w_, BED_Y + 12, x + w_, BED_Y + 20, 1.5, '#1A0E08', 110)
            for dx in (-9, -3, 4, 10):
                c.line([(x + dx, BED_Y + 7), (x + dx + (dx % 3 - 1), BED_Y + 1 - abs(dx) % 4)], '#1E1612', 1.4)
            for i in range(3):
                c.ell(x - 4 + i * 4, BED_Y - 4 - i * 6, 2.6 + i * 1.1, col='#8A8490', a=120 - i * 32)
            c.ell(x - 5, BED_Y + 6.5, 1, col=EMBER_HOT)
            c.ell(x + 6, BED_Y + 5.5, 0.8, col=EMBER)
        else:
            self.crop(x, kind)

    def garden(self, burnt):
        self.hedge()
        self.lawn()
        c, L = self.c, self.L
        self.L = self.recede
        for (x, h, f) in [(34, 36, -1), (97, 40, 1), (278, 40, -1), (341, 36, 1)]:
            self.sunflower(x, 250, h, f, 1.45)
        self.beanpole(132, 250, 34)
        self.beanpole(243, 250, 34)
        self.L = self.lit
        kinds = ['pumpkin', 'tomato', 'cabbage', 'carrot', 'corn', 'strawberry', 'tulips', 'lettuce']
        for i, x in enumerate(BEDS):
            self.bed(x, kinds[i], burnt)
        for (x, y, r, col) in [(50, 318, 17, '#F15BB5'), (104, 326, 15, None), (148, 328, 11, '#FFFFFF'), (228, 328, 11, '#FFD23F'),
                               (272, 326, 15, None), (326, 318, 17, '#FF5470')]:
            self.bush(x, y, r)
            if col:
                for k in range(3):
                    bx, by = x + (k - 1) * r * 0.55, y - r * (0.75 + (k % 2) * 0.3)
                    for q in range(5):
                        c.ell(bx + math.cos(q * 1.257) * 2.4, by + math.sin(q * 1.257) * 2.4, 2.0, col=L(col))
                    c.ell(bx, by, 1.4, col=L('#FFD23F'))

    def critters(self):
        """The design's critters at rest: butterflies by day and dawn, bees by day, birds at dawn, fireflies after dusk."""
        c, L = self.c, self.L
        if self.tod in ('day', 'dawn'):
            for i, (x, y, col) in enumerate([(66, 236, '#FFB03A'), (300, 242, '#7AB8FF'), (240, 282, '#FF7AB8')]):
                yy = y + math.sin(i * 2) * 3
                op = abs(math.sin(i))
                c.ell(x - 2.2 * op, yy - 1, 2.4 * op + 0.6, 2.6, L(col))
                c.ell(x + 2.2 * op, yy - 1, 2.4 * op + 0.6, 2.6, L(col))
                c.rect(x - 0.4, yy - 2.4, x + 0.4, yy + 1.6, L('#3A2A22'))
        if self.tod == 'day':
            for i, (x, y) in enumerate([(126, 226), (322, 262)]):
                yy = y + math.sin(i) * 2
                c.ell(x, yy, 2.2, 1.6, '#FFD23F')
                c.rect(x - 0.4, yy - 1.5, x + 0.5, yy + 1.5, '#2A2018')
                c.ell(x - 0.6, yy - 2.2, 1.6, 1.0, '#FFFFFF', 180)
        if self.tod == 'dawn':
            for (x, y) in [(118, 96), (132, 90), (104, 104)]:
                c.line([(x - 4, y - 1.5), (x, y + 1), (x + 4, y - 1.5)], '#4A3A5A', 1.1)
        if self.tod in ('night', 'dusk'):
            rnd = random.Random(21)
            for i in range(11 if self.tod == 'night' else 5):
                x, y = rnd.uniform(24, 350), rnd.uniform(228, 300)
                if 160 < x < 215:
                    continue
                k = 0.5 + 0.5 * math.sin(i * 1.7)
                c.glow(x, y, 5, '#D8FF6A', 120 * k, 4)
                c.ell(x, y, 0.9, col='#F4FFB0', a=255 * (0.4 + 0.6 * k))

    def hose(self):
        c, L = self.c, self.L
        x, y = SPR
        pts = [(x + 3, y + 16), (x + 22, y + 22), (x + 44, y + 15), (x + 66, y + 24), (x + 92, y + 36), (x + 130, y + 50)]
        c.line(pts, L('#1E5A3A'), 4.2)
        c.line(pts, L('#2E8A56'), 2.6)
        c.line([(p[0], p[1] - 0.8) for p in pts], L('#6CCB8A'), 0.7)

    def stand(self):
        """The sprinkler's tripod and riser: the head that turns is the game's (draw_sprinkler)."""
        c, L = self.c, self.L
        x, y = SPR
        self.shadow(x, y + 17, 13, 3)
        for dx in (-11, 0, 11):
            c.line([(x, y + 3), (x + dx, y + 17)], L('#6E7480'), 2.0)
            c.ell(x + dx, y + 17, 1.6, col=L('#4A4E58'))
        c.rr(x - 3.2, y - 1, x + 3.2, y + 9, 1.5, L('#9A6E22'))
        c.rect(x - 3.2, y + 2, x + 3.2, y + 3.4, L('#6E4A14'))

    def board(self, x, y, w_=60, title=None):
        """A plant marker staked in the lawn (sky.py sign), its number left for the game to write."""
        c, L = self.c, self.L
        h = 17
        c.rect(x - 1.5, y + 4, x + 1.5, y + 21, L('#7A4E2A'))
        c.ell(x, y + 21.5, 7, 1.8, '#0A2A14', 90)
        c.rr(x - w_ / 2 - 1, y - h / 2, x + w_ / 2 + 1, y + h / 2 + 1.5, 4, L('#7A4E2A'))
        c.rr(x - w_ / 2, y - h / 2, x + w_ / 2, y + h / 2, 3.5, L('#FFF6DC'))
        if title:
            c.text(x - w_ / 2 + 5, y + 0.6, title, 8, L(mix('#3A2414', '#FFF6DC', 0.35)), 'lm')


def scene(tod, burnt):
    s = S(tod)
    s.sky()
    s.volcano()
    s.land()
    s.garden(burnt)
    s.critters()
    s.hose()
    s.board(84, 291)
    s.board(291, 291, title='WAVE')
    s.stand()
    return s.c.out()


# ---------------------------------------------------------------------------------------------------- the palettes


def kmeans(x, w, k, it=30):
    """k centres for the points x (weights w): k-means from a k-means++ start, the generator seeded."""
    if len(x) <= k:
        return x.copy(), np.arange(len(x))
    rng = np.random.default_rng(1)
    c = [x[np.argmax(w)]]
    d = ((x - c[0]) ** 2).sum(1)
    for _ in range(k - 1):
        p = d * w
        i = int(np.argmax(p)) if p.sum() == 0 else int(rng.choice(len(x), p=p / p.sum()))
        c.append(x[i])
        d = np.minimum(d, ((x - x[i]) ** 2).sum(1))
    cs = np.array(c)
    for _ in range(it):
        lab = np.argmin(((x[:, None, :] - cs[None]) ** 2).sum(2), 1)
        for j in range(k):
            m = lab == j
            if m.any():
                cs[j] = (x[m] * w[m, None]).sum(0) / w[m].sum()
    return cs, np.argmin(((x[:, None, :] - cs[None]) ** 2).sum(2), 1)


def quantize(main, charred, boxes):
    """The scene and its charred boxes as indexes into a palette a band, four colours an index (one a time of day)."""
    nb = H // BAND
    idx = np.zeros((H, W), np.uint8)
    pal = np.zeros((4, nb, COLOURS, 3), np.uint8)
    cidx = [np.zeros((b[3], b[2]), np.uint8) for b in boxes]
    worst = 0.0
    for b in range(nb):
        y0, y1 = b * BAND, (b + 1) * BAND
        pts = [main[y0:y1].reshape(-1, 12)]
        parts = []
        for k, (bx, by, bw, bh) in enumerate(boxes):
            r0, r1 = max(by, y0), min(by + bh, y1)
            if r0 < r1:
                parts.append((k, r0, r1))
                pts.append(charred[r0:r1, bx:bx + bw].reshape(-1, 12))
        allp = np.concatenate(pts).astype(np.float64)
        u, inv, cnt = np.unique(allp, axis=0, return_inverse=True, return_counts=True)
        cs, lab = kmeans(u, cnt.astype(np.float64), COLOURS)
        q = lab[inv.ravel()]
        worst = max(worst, float(np.abs(np.round(cs[q]) - allp).mean()))
        n = (y1 - y0) * W
        idx[y0:y1] = q[:n].reshape(y1 - y0, W)
        at = n
        for k, r0, r1 in parts:
            bx, by, bw, bh = boxes[k]
            m = (r1 - r0) * bw
            cidx[k][r0 - by:r1 - by] = q[at:at + m].reshape(r1 - r0, bw)
            at += m
        full = np.zeros((COLOURS, 12))
        full[:len(cs)] = cs
        for t in range(4):
            pal[t, b] = np.clip(np.round(full[:, 3 * t:3 * t + 3]), 0, 255).astype(np.uint8)
    return idx, pal, cidx, worst


# ---------------------------------------------------------------------------------------------------- sprites


def premultiplied(draw, w, h, ax, ay):
    """What draw(c, x, y) paints at (x, y) = (ax, ay) inside a w x h box, as premultiplied 0xAARRGGBB words: drawn over
    black and over white, the difference is what the paint lets through."""
    shots = []
    for bg in ('#000000', '#FFFFFF'):
        c = C(w, h, bg)
        draw(c, ax, ay)
        shots.append(c.out())
    blk, wht = shots
    a = np.clip(255 - (wht - blk).mean(2), 0, 255)
    pre = np.minimum(blk, a[..., None])
    return (a.astype(np.uint32) << 24) | (pre[..., 0].astype(np.uint32) << 16) | (pre[..., 1].astype(np.uint32) << 8) | pre[..., 2].astype(np.uint32)


def ember(c, x, y, size, sway, smoke=True):
    """sky.py's ember at rest (t 0), its glow at GLOW; without its smoke thread, the short tail the game gives the embers
    that fall while many others do (SPEC.md's frame cost: the smoke thread goes first)."""
    r = (3.0, 4.6, 7.0)[size]
    for i in range(1, 7 if smoke else 1):
        c.ell(x - sway * i * 0.9 + math.sin(i * 1.3) * 0.8, y - i * r * 1.05, max(0.6, r * (0.8 - i * 0.09)), col='#3A2E3A', a=95 - i * 12)
    for i in range(1, 5):
        c.ell(x - sway * i * 0.6, y - i * r * 0.75, r * (0.92 - i * 0.17), col=EMBER, a=200 - i * 42)
    c.glow(x, y, r + 5, EMBER, 60 + 50 * GLOW, 4)
    c.ell(x, y, r + 0.9, col='#B8380A')
    c.ell(x, y, r, col=EMBER_HOT)
    c.ell(x - r * 0.12, y + r * 0.1, r * 0.58, col=EMBER_CORE)
    if size == 2:
        c.ell(x - 1, y, r * 0.26, col='#FFFFFF')
        for k in range(3):
            c.ell(x + math.cos(k * 2.1 + 0.6) * r * 0.62, y + math.sin(k * 2.1 + 0.6) * r * 0.62, 1.2, col='#7A2A10')


def fire(c, x, y, f):
    """sky.py's fire, frame f of 3, without its ground glow (FIREGLOW, a mask the game draws in the ember's colour)."""
    lean = (-1.5, 1.2, 0)[f % 3]
    for (dx, h, w_) in ((-7, 10, 4), (6, 12, 4.4), (0, 17, 6)):
        h *= (1.0, 0.86, 1.1)[(f + int(dx)) % 3]
        c.poly([(x + dx - w_, y), (x + dx + w_, y), (x + dx + w_ * 0.4 + lean, y - h * 0.55), (x + dx + lean * 1.6, y - h),
                (x + dx - w_ * 0.55, y - h * 0.5)], EMBER)
        c.poly([(x + dx - w_ * 0.55, y), (x + dx + w_ * 0.55, y), (x + dx + lean, y - h * 0.62)], '#FFC23A')
    c.poly([(x - 2.2, y), (x + 2.2, y), (x + lean * 0.6, y - 7)], '#FFF2B0')


def puff(c, x, y, f):
    """sky.py's steam, frames 1 to 4 (the puff rising and thinning) of a medium ember's, in the day's haze; the game
    draws frame 0's flash, the flecks, the dead cinder and the points itself."""
    r0, k = 7, f / 4
    for (dx, dy, r) in [(0, 0, 1.0), (-0.9, -0.5, 0.78), (0.85, -0.7, 0.82), (-0.2, -1.4, 0.68), (1.0, -1.8, 0.5), (-1.2, -2.0, 0.46)]:
        c.ell(x + dx * r0 * (0.8 + k * 0.7), y + dy * r0 * (0.8 + k * 0.7) - k * 9, r * r0 * (0.75 + 0.5 * (1 - abs(k - 0.4))),
              col=mix('#FFFFFF', TOD['day']['haze'], 0.25), a=235 * (1 - k) ** 0.8 + 20)


def mask(draw, w, h, ax, ay):
    """What draw(c, x, y) paints in white at (ax, ay) inside w x h, as alpha 0-255."""
    c = C(w, h, '#000000')
    draw(c, ax, ay)
    return c.out()[..., 0]


def trimmed(a, ax, ay):
    """a cut to the box round its ink, and the anchor's place in it."""
    ys, xs = np.nonzero(a)
    if not len(xs):
        return a[:1, :1], ax, ay
    x0, x1, y0, y1 = xs.min(), xs.max() + 1, ys.min(), ys.max() + 1
    return a[y0:y1, x0:x1], ax - x0, ay - y0


def nibbles(a):
    """A mask at 4 bits a pixel, two pixels a byte (the left one high), each row whole bytes."""
    q = (a.astype(np.int32) * 15 + 127) // 255
    w = a.shape[1]
    if w % 2:
        q = np.concatenate([q, np.zeros((a.shape[0], 1), np.int32)], 1)
    return ((q[:, 0::2] << 4) | q[:, 1::2]).astype(np.uint8).tobytes()


# ---------------------------------------------------------------------------------------------------- the file


class Pack:
    def __init__(self):
        self.parts = []  # (name, kind, w, h, ax, ay, adv, data)

    def add(self, name, kind, w, h, ax, ay, data, adv=0):
        self.parts.append((name, kind, w, h, ax, ay, adv, data))

    def sprite(self, name, words, ax, ay):
        a = (words >> 24) & 255
        ys, xs = np.nonzero(a)
        x0, x1, y0, y1 = xs.min(), xs.max() + 1, ys.min(), ys.max() + 1
        cut = words[y0:y1, x0:x1]
        spans = b''  # each row's first and last-plus-one column with ink, so the game skips the clear ends
        for row in (cut >> 24) & 255:
            ink = np.nonzero(row)[0]
            spans += bytes((int(ink.min()), int(ink.max()) + 1) if len(ink) else (0, 0))
        self.add(name, 1, x1 - x0, y1 - y0, ax - x0, ay - y0, cut.astype('<u4').tobytes() + spans)

    def glyph(self, name, a, ax, ay, adv=0):
        a, ax, ay = trimmed(a, ax, ay)
        self.add(name, 2, a.shape[1], a.shape[0], ax, ay, nibbles(a), adv)

    def write(self):
        head = 16 + 16 * len(self.parts)
        body, table, at = b'', b'', head
        for (name, kind, w, h, ax, ay, adv, data) in self.parts:
            data = data + b'\0' * (-len(data) % 4)
            table += struct.pack('<IHHhhBBH', at, w, h, ax, ay, kind, 0, adv)
            body += data
            at += len(data)
        return struct.pack('<IIII', MAGIC, at, len(self.parts), BAND) + table + body


def text_parts(pk):
    """The lettering, Baloo 2 Bold at the design's sizes: a mask a word or glyph, and for an outlined one its outline as
    a second mask (stroke and fill together), drawn first in the outline's colour."""
    def word(name, s, px, stroke=0.0):
        w = int(font(px).getlength(s) / SS) + 8 + int(2 * stroke)
        h = int(px * 1.6) + 4

        def fill(c, x, y):
            c.text(x, y, s, px, '#FFFFFF', 'lm')
        pk.glyph(name, mask(fill, w, h, 4 + stroke, h / 2), 4 + int(stroke), h // 2)
        if stroke:
            def out(c, x, y):
                c.text(x, y, s, px, '#FFFFFF', 'lm', stroke, '#FFFFFF')
            pk.glyph(name + '_LINE', mask(out, w, h, 4 + stroke, h / 2), 4 + int(stroke), h // 2)

    def digits(name, px, stroke=0.0, chars='0123456789,+'):
        for ch in chars:
            label = {',': 'COMMA', '+': 'PLUS'}.get(ch, ch)
            adv = round(font(px).getlength(ch) / SS)
            word(f'{name}_{label}', ch, px, stroke)
            pk.parts[-1 - (1 if stroke else 0)] = pk.parts[-1 - (1 if stroke else 0)][:6] + (adv,) + pk.parts[-1 - (1 if stroke else 0)][7:]
    digits('SIGN', 13, chars='0123456789,')
    digits('CARD', 17, chars='0123456789,')
    digits('SMALL', 14, chars='0123456789,')
    digits('BIG', 30, 2.4, chars='0123456789')
    digits('POP', 11, 1.2, chars='0123456789+')
    word('T_WAVE_BIG', 'WAVE', 30, 2.4)
    for name, s in (('T_DAWN', 'dawn is breaking'), ('T_DAY', 'the sun is up'), ('T_DUSK', 'dusk is falling'),
                    ('T_NIGHT', 'night is here'), ('T_LULL', 'LULL  x2')):
        word(name, s, 13, 1.6)
    word('T_LAST', 'LAST BED', 9, 1.2)
    word('T_NEWBEST', 'NEW BEST', 9)
    word('T_TITLE', 'EMBER GARDEN', 19)
    word('T_LOST', 'GARDEN LOST', 19)
    word('T_KEEP', 'Keep the embers off the beds', 10)  # the design's 13 px runs past the card's 150
    for name, s in (('T_BEST', 'BEST'), ('T_BESTWAVE', 'BEST WAVE'), ('T_SCORE', 'SCORE'), ('T_WAVE', 'WAVE')):
        word(name, s, 11)
    word('T_START', 'CENTER: START', 10.5)
    word('T_REPLANT', 'CENTER: REPLANT', 10.5)


def build():
    mains = [scene(t, False) for t in ORDER]
    chars = [scene(t, True) for t in ORDER]
    main, charred = np.concatenate(mains, 2), np.concatenate(chars, 2)
    diff = np.nonzero(np.abs(main - charred).sum(2))
    y0, y1 = int(diff[0].min()), int(diff[0].max()) + 1
    half = max(max(abs(int(x) - b) for b in BEDS for x in diff[1] if abs(int(x) - b) < 18) + 1, 1)
    boxes = [(b - half, y0, 2 * half, y1 - y0) for b in BEDS]
    idx, pal, cidx, err = quantize(main, charred, boxes)
    print(f'scene quantized: mean error {err:.3f} of 255, bands of {BAND} rows, {COLOURS} colours', file=sys.stderr)
    pk = Pack()
    pk.add('SCENE', 0, W, H, 0, 0, idx.tobytes())
    pk.add('PALETTE', 0, COLOURS, H // BAND, 0, 0, pal.tobytes())
    pk.add('CHARRED', 0, boxes[0][2], boxes[0][3] * 8, 0, 0, b''.join(c.tobytes() for c in cidx))
    for smoke in (True, False):  # upright: the game leans a tail by shifting its rows (patch/game_embergarden.c sprite)
        for size in range(3):
            r = (3.0, 4.6, 7.0)[size]
            w, h = int(2 * r + 14), int(r * 7.5 + r + 14)
            ax, ay = w // 2, h - int(r) - 8
            pk.sprite(f'EMBER{size}' if smoke else f'SHORT{size}',
                      premultiplied(lambda c, x, y: ember(c, x + 0.5, y + 0.5, size, 0.0, smoke), w, h, ax, ay), ax, ay)
    pk.glyph('FIREGLOW', mask(lambda c, x, y: c.glow(x + 0.5, y - 4, 22, '#FFFFFF', 70 + 60 * GLOW, 6, 13), 50, 40, 25, 28),
             25, 28)
    for f in range(3):
        pk.sprite(f'FIRE{f}', premultiplied(lambda c, x, y: fire(c, x + 0.5, y, f), 50, 40, 25, 28), 25, 28)
    for f in range(1, 5):
        pk.sprite(f'PUFF{f}', premultiplied(lambda c, x, y: puff(c, x + 0.5, y + 0.5, f), 48, 56, 24, 36), 24, 36)
    text_parts(pk)
    data = pk.write()
    return data, pk, boxes


def header(data, pk, boxes):
    names = '\n'.join(f'    EG_{p[0]}, /* {p[2]} x {p[3]} */' for p in pk.parts)
    xs = ', '.join(str(b[0]) for b in boxes)
    return f"""/* Ember Garden's art (tools/embergarden_art.py writes this and assets/games/embergarden.bin; do not edit): the
 * file's size and parts, and the scene's places. */
#define EG_ART_BYTES {len(data)}u
#define EG_ART_MAGIC 0x{MAGIC:08x}u
#define EG_BAND {BAND}    /* rows a palette */
#define EG_COLOURS {COLOURS} /* colours a palette */
#define EG_CH_Y {boxes[0][1]}   /* the charred boxes: their first row, size and each one's first column */
#define EG_CH_W {boxes[0][2]}
#define EG_CH_H {boxes[0][3]}
#define EG_CH_X {{ {xs} }}

enum {{
{names}
    EG_PARTS
}};
"""


def main():
    global FONT_DIR
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    FONT_DIR = pathlib.Path(sys.argv[1])
    got = hashlib.sha256((FONT_DIR / FONT[0]).read_bytes()).hexdigest()
    if got != FONT[1]:
        sys.exit(f'{FONT[0]} is not the one the art was drawn from ({got})')
    data, pk, boxes = build()
    OUT_BIN.parent.mkdir(parents=True, exist_ok=True)
    OUT_BIN.write_bytes(data)
    OUT_H.write_text(header(data, pk, boxes))
    print(f'{OUT_BIN.relative_to(ROOT)}: {len(data):,} bytes, {len(pk.parts)} parts', file=sys.stderr)


if __name__ == '__main__':
    main()
