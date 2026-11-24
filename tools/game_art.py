"""Sudoku's masks (patch/game_sudoku_art.inc, docs/games-engine.md#sudoku): the digits, the words and the stamp of
its newspaper page, drawn from four fonts at the sizes the design gives, each packed for the engine's eng_glyph.

The fonts are not in the repository (they are needed only to draw the masks again): Patrick Hand, Libre Baskerville
and Playfair Display (SIL OFL 1.1) and Special Elite (Apache 2.0), from Google Fonts; FONTS names each file and the
SHA-256 the committed masks were drawn from. A mask is the fonts' output, not the fonts.

usage: python3 tools/game_art.py FONT_DIR [OUT]   (OUT: patch/game_sudoku_art.inc)

A packed mask is its width and height (a byte each), then nibbles, the high one of a byte first, left to right and
top to bottom, crossing rows: 1 to 14 is one pixel of that alpha of 15, 0 and 15 take the next nibble as a run of
that many and one clear (0) or solid (15) pixels."""
import hashlib
import math
import pathlib
import sys
from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen
from fontTools.ttLib import TTFont
from art import coverage, coverage_nonzero, path, stroke

ROOT = pathlib.Path(__file__).resolve().parent.parent
FONTS = {'hand': ('PatrickHand-Regular.ttf', '0f173b3e6cb6d1af25babf7f0057c5ac4ee11f9992b0469bb817e967ef4ad0fc'),
         'serif': ('LibreBaskerville.ttf', '05a95421961341c5b2556285e8415df9db27dab4f4abe22b446b3c6a8b916c5d'),
         'display': ('PlayfairDisplay.ttf', 'c40f2293766a503bc70cce9e512ef844a4ccb7cbcde792fe2ea31d191917d8d6'),
         'type': ('SpecialElite-Regular.ttf', 'a776fcb4ceb8bdf03e2967688ebdad42680de5b91a7e62c17e718ae212d14bc4')}
RUN = 16  # the longest run a nibble holds
HAND = '0123456789:!abdefhilnoprstwyGS'  # Patrick Hand's small set: notes, times, bests, the level
CAPS = 'ABCDEFGHIJKLMNOPRSTUVWYZ0123456789:,'  # Special Elite's capitals for the small type


def outline(font, text, px, wght=None, x=0.0, y=0.0):
    """text's contours in font at px (the em), its baseline at y and its pen from x, in pixels with y down; and each
    character's pen position after it."""
    glyphs = font.getGlyphSet(location={'wght': wght} if wght else None)
    cmap, k, polys, pens = font.getBestCmap(), px / font['head'].unitsPerEm, [], []
    for ch in text:
        name = cmap[ord(ch)]
        pen = SVGPathPen(glyphs, ntos=lambda v: f'{v:.4f}')
        glyphs[name].draw(TransformPen(pen, (k, 0, 0, -k, x, y)))
        polys += path(pen.getCommands())
        x += glyphs[name].width * k
        pens.append(x)
    return polys, pens


def turn(polys, degrees, cx, cy):
    """polys turned by degrees round (cx, cy), anticlockwise on the screen (y down)."""
    c, s = math.cos(math.radians(degrees)), math.sin(math.radians(degrees))
    return [[(cx + (x - cx) * c + (y - cy) * s, cy - (x - cx) * s + (y - cy) * c) for x, y in p] for p in polys]


def mask(filled, lines=(), width=0.0, more=()):
    """The pixels the filled contours (nonzero) and the lines (width wide; more: further (lines, width)) cover, as
    (x, y, w, h, alpha 0-255) over the box round their ink."""
    groups = [stroke(list(ls), wd) for ls, wd in ((lines, width), *more) if ls]
    lined = [p for g in groups for p in g]
    pts = [pt for p in filled + lined for pt in p]
    x0, y0 = math.floor(min(x for x, _ in pts)), math.floor(min(y for _, y in pts))
    box = (x0, y0, math.ceil(max(x for x, _ in pts)) - x0, math.ceil(max(y for _, y in pts)) - y0)
    a = coverage_nonzero(filled, box) if filled else [0.0] * (box[2] * box[3])
    for g in groups:
        a = [max(p, q) for p, q in zip(a, coverage(g, box))]
    return (*box, bytes(min(255, round(v * 255)) for v in a))


def pack(w, h, alpha):
    """A mask as eng_glyph reads it (the module's docstring)."""
    q = [round(v * 15 / 255) for v in alpha]
    nib, i = [], 0
    while i < len(q):
        n = 1
        if q[i] in (0, 15):
            while i + n < len(q) and n < RUN and q[i + n] == q[i]:
                n += 1
            nib += [q[i], n - 1]
        else:
            nib.append(q[i])
        i += n
    nib += [0] * (len(nib) & 1)
    return bytes((w, h, *(nib[k] << 4 | nib[k + 1] for k in range(0, len(nib), 2))))


def unpack(data):
    """pack's inverse, to 0-15 an alpha: (w, h, alpha)."""
    w, h = data[0], data[1]
    nib = [n for b in data[2:] for n in (b >> 4, b & 15)]
    out, k = [], 0
    while len(out) < w * h:
        if nib[k] in (0, 15):
            out += [nib[k]] * (nib[k + 1] + 1)
            k += 2
        else:
            out.append(nib[k])
            k += 1
    assert len(out) == w * h and len(nib) - k < 2, 'a mask packed whole'
    return w, h, out


def glyph(font, ch, px, wght=None, thicken=0.0):
    """One character as (x offset, y offset, advance, packed): its mask's place from the pen on the baseline."""
    polys, pens = outline(font, ch, px, wght)
    x, y, w, h, a = mask(polys, polys if thicken else (), thicken)
    return x, y, round(pens[-1]), pack(w, h, a)


def picture(polys, lines=(), width=0.0, more=()):
    """A whole drawing as a glyph placed at 0, 0."""
    x, y, w, h, a = mask(polys, lines, width, more)
    return 0, 0, 0, pack(w, h, a)


def sets(fonts):
    """[(enum name, [glyphs])] in the order the .inc lists them, and the stamp's frame: the corners of its two boxes,
    clockwise from the top left, in 1/16 px from its mask's top left. fonts: the four TTFonts by FONTS' keys."""
    hand, serif, display, kind = fonts['hand'], fonts['serif'], fonts['display'], fonts['type']
    digits = '123456789'
    head, _ = outline(display, 'THE EVENING PUZZLE', 13, 800)
    title, _ = outline(display, 'The Evening Puzzle', 22, 800)
    word, pens = outline(display, 'SOLVED', 44, 900)
    cx, cy, w = pens[-1] / 2, -16, pens[-1]
    stamp = turn(word, 11, cx, cy)  # its frame is drawn by game_sudoku.c (pen lines), not packed
    boxes = turn([[(-m, cy - h), (w + m, cy - h), (w + m, cy + h), (-m, cy + h)] for m, h in ((7, 32), (4.8, 29.8))],
                 11, cx, cy)
    left, top = math.floor(min(x for p in stamp for x, _ in p)), math.floor(min(y for p in stamp for _, y in p))
    frame = [round((v - o) * 16) for b in boxes for pt in b for v, o in zip(pt, (left, top))]
    out = [('A_PRINT', [glyph(serif, d, 25, 700) for d in digits]),  # the clues
           ('A_PEN', [glyph(hand, d, 30, None, 0.66) for d in '0123456789:']),  # the entries, the hub, the result
           ('A_DIAL', [glyph(hand, d, 19, None, 0.42) for d in digits]),
           ('A_HAND', [glyph(hand, c, 12.5, None, 0.3) for c in HAND]),  # notes, times, bests
           ('A_TALLY', [glyph(serif, d, 11.5, 700) for d in digits]),
           ('A_CAPS', [glyph(kind, c, 7) for c in CAPS]),
           ('A_NAME', [picture(outline(serif, n, 16, 700)[0]) for n in ('Gentle', 'Steady', 'Sharp')]),
           ('A_TITLE', [picture(title)]),
           ('A_HEAD', [picture(turn(head, 90, 0, 0))]),  # set sideways, read from the foot up
           ('A_STAMP', [picture(stamp)])]
    return out, frame


def source(fonts):
    """patch/game_sudoku_art.inc's text."""
    lines, data, at, n = [], bytearray(), [], 0
    enums = []
    groups, frame = sets(fonts)
    for name, glyphs in groups:
        enums.append(f'{name} = {n}')
        for x, y, adv, packed in glyphs:
            at.append(len(data))
            data += bytes((x & 255, y & 255, adv)) + packed
            n += 1
    lines.append('/* Sudoku\'s masks: made by tools/game_art.py, not edited. Each is its x and y from the pen on the')
    lines.append(' * baseline (signed), its advance, then eng_glyph\'s packed mask. */')
    lines.append(f'#define A_HAND_SET "{HAND}"')
    lines.append(f'#define A_CAPS_SET "{CAPS}"')
    lines.append('enum { ' + ', '.join(enums) + f', A_COUNT = {n} }};')
    lines.append(f'static const short art_frame[16] = {{ {", ".join(map(str, frame))} }};')
    lines.append(f'static const unsigned short art_at[A_COUNT] = {{ {", ".join(map(str, at))} }};')
    lines.append(f'static const unsigned char art[{len(data)}] = {{')
    for k in range(0, len(data), 24):
        lines.append('    ' + ', '.join(str(b) for b in data[k:k + 24]) + ',')
    lines.append('};')
    return '\n'.join(lines) + '\n'


def load(folder):
    """The four fonts from folder, each checked against FONTS' hash."""
    out = {}
    for key, (name, digest) in FONTS.items():
        raw = (pathlib.Path(folder)/name).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == digest, f'{name} is not the font the masks were drawn from'
        out[key] = TTFont(pathlib.Path(folder)/name)
    return out


if __name__ == '__main__':
    target = pathlib.Path(sys.argv[2]) if len(sys.argv) > 2 else ROOT/'patch/game_sudoku_art.inc'
    target.write_text(source(load(sys.argv[1])))
