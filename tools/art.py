"""Original raster art the theme files state in numbers, drawn at build time: small vector shapes, antialiased
over sub-rows, written as 8-bit RGBA PNGs. No Apple assets or reference pixels; every shape is drawn here."""
import bisect
import functools
import math
import re
import struct
import zlib
from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen
from fontTools.ttLib import TTFont
from build import ROOT, check, need
from ipod import inc
from theme import CHOICES, token

SUB = 8  # sub-rows per pixel; columns are exact within a sub-row


def _arc(p0, rx, ry, phi, large, sweep, p1, steps=48):
    """SVG's endpoint arc as points after p0."""
    c, s = math.cos(math.radians(phi)), math.sin(math.radians(phi))
    dx, dy = (p0[0] - p1[0]) / 2, (p0[1] - p1[1]) / 2
    x1, y1 = c * dx + s * dy, -s * dx + c * dy
    k = x1**2 / rx**2 + y1**2 / ry**2
    if k > 1:
        rx, ry = rx * math.sqrt(k), ry * math.sqrt(k)
    num = max(0, rx**2 * ry**2 - rx**2 * y1**2 - ry**2 * x1**2)
    f = math.sqrt(num / (rx**2 * y1**2 + ry**2 * x1**2)) * (1 if large != sweep else -1)
    cx1, cy1 = f * rx * y1 / ry, -f * ry * x1 / rx
    cx, cy = c * cx1 - s * cy1 + (p0[0] + p1[0]) / 2, s * cx1 + c * cy1 + (p0[1] + p1[1]) / 2
    a0 = math.atan2((y1 - cy1) / ry, (x1 - cx1) / rx)
    a1 = math.atan2((-y1 - cy1) / ry, (-x1 - cx1) / rx)
    d = a1 - a0
    if sweep and d < 0:
        d += 2 * math.pi
    elif not sweep and d > 0:
        d -= 2 * math.pi
    out = []
    for i in range(1, steps + 1):
        a = a0 + d * i / steps
        x, y = rx * math.cos(a), ry * math.sin(a)
        out.append((c * x - s * y + cx, s * x + c * y + cy))
    return out


def path(d):
    """An SVG path's d (M L H V C S Q A Z, absolute or relative, an M's further pairs lines) as closed polylines. S's
    first control point is the last C or S one's mirrored through the current point, else the current point."""
    tokens = re.findall(r'[MLHVCSQAZmlhvcsqaz]|-?[0-9]*\.?[0-9]+', d)
    polys, pts, i, cmd = [], [], 0, None
    cur = (0.0, 0.0)
    mirror = None  # the last C or S's second control point, while the command just drawn was one

    def num():
        nonlocal i
        i += 1
        return float(tokens[i - 1])

    def at(rel):  # the next pair, from the current point in a lower-case command
        x, y = num(), num()
        return (cur[0] + x, cur[1] + y) if rel else (x, y)
    while i < len(tokens):
        if tokens[i].isalpha():
            cmd = tokens[i]
            i += 1
        cmd = need(cmd, 'SVG path data before its first command')
        rel, op = cmd.islower(), cmd.upper()
        smooth, mirror = mirror, None
        if op == 'Z':
            polys.append(pts)
            cur, pts = pts[0], []
            continue
        if op == 'M':
            cur = at(rel)
            pts = [cur]
            cmd = 'l' if rel else 'L'
        elif op == 'L':
            cur = at(rel)
            pts.append(cur)
        elif op == 'H':
            cur = (num() + (cur[0] if rel else 0), cur[1])
            pts.append(cur)
        elif op == 'V':
            cur = (cur[0], num() + (cur[1] if rel else 0))
            pts.append(cur)
        elif op in 'CSQ':
            first = [(2 * cur[0] - smooth[0], 2 * cur[1] - smooth[1]) if smooth else cur] if op == 'S' else []
            ctrl = [cur, *first, *(at(rel) for _ in range(2 if op == 'Q' else 3 - len(first)))]
            if op != 'Q':
                mirror = ctrl[-2]
            n = len(ctrl) - 1
            for k in range(1, 25):
                t = k / 24
                weights = [math.comb(n, m) * (1 - t)**(n - m) * t**m for m in range(n + 1)]
                pts.append(tuple(sum(w * p[a] for w, p in zip(weights, ctrl)) for a in (0, 1)))
            cur = pts[-1]
        else:  # A, the one command the tokens can hold that is left
            rx, ry, phi, large, sweep = num(), num(), num(), num(), num()
            end = at(rel)
            pts += _arc(cur, rx, ry, phi, large, sweep, end)
            cur = pts[-1]
    return polys + ([pts] if pts else [])


def stroke(polys, width):
    """Open polylines' stroke, round joined and capped, as polygons a union fills."""
    r, out = width / 2, []
    circle = lambda x, y: [(x + r * math.cos(a * math.pi / 8), y + r * math.sin(a * math.pi / 8)) for a in range(16)]
    for poly in polys:
        out.append(circle(*poly[0]))
        for (x0, y0), (x1, y1) in zip(poly, poly[1:]):
            n = math.hypot(x1 - x0, y1 - y0)
            if n:
                nx, ny = (y1 - y0) / n * r, (x0 - x1) / n * r
                out.append([(x0 + nx, y0 + ny), (x1 + nx, y1 + ny), (x1 - nx, y1 - ny), (x0 - nx, y0 - ny)])
            out.append(circle(x1, y1))
    return out


def _edges(poly):
    """poly's edges that are not level, each (low y, high y, ax, ay, bx, by): a scanline at y crosses one when
    low <= y < high, which is (ay <= y) != (by <= y)."""
    return [(min(ay, by), max(ay, by), ax, ay, bx, by) for (ax, ay), (bx, by) in zip(poly, poly[1:] + poly[:1]) if ay != by]


def coverage(polys, box):
    """Each pixel's covered fraction of box (x, y, w, h), the polygons' union, each polygon even-odd. Each polygon's
    edges are listed once, and a scanline tests only those of the polygons whose span it is in."""
    x0, y0, w, h = box
    cov = [0.0] * (w * h)
    each = [(min(e[0] for e in es), max(e[1] for e in es), es) for es in map(_edges, polys) if es]
    for sub in range(h * SUB):
        y = y0 + (sub + 0.5) / SUB
        spans = []
        for low, high, es in each:
            if low <= y < high:
                xs = sorted(ax + (y - ay) * (bx - ax) / (by - ay) for lo, hi, ax, ay, bx, by in es if lo <= y < hi)
                spans += zip(xs[::2], xs[1::2])
        spans.sort()
        row, end = sub // SUB * w, None
        for a, b in spans:
            if end is not None and a < end:
                a = end
            if end is None or b > end:
                end = b
            a, b = max(a - x0, 0), min(b - x0, w)
            if a >= b:  # inside the union so far
                continue
            for px in range(int(a), math.ceil(b)):
                cov[row + px] += (min(b, px + 1) - max(a, px)) / SUB
    return cov


def coverage_nonzero(polys, box):
    """As coverage, but the polygons filled together by the nonzero rule, as an SVG path's default: a ring's inner
    contour, wound the other way, is a hole. The edges are sorted by their low y once, and a scanline tests only
    those that start at or above it."""
    x0, y0, w, h = box
    cov = [0.0] * (w * h)
    es = sorted(e for poly in polys for e in _edges(poly))
    lows = [e[0] for e in es]
    for sub in range(h * SUB):
        y = y0 + (sub + 0.5) / SUB
        cross = sorted((ax + (y - ay) * (bx - ax) / (by - ay), 1 if by > ay else -1)
                       for lo, hi, ax, ay, bx, by in es[:bisect.bisect_right(lows, y)] if y < hi)
        row, wind = sub // SUB * w, 0
        for (xa, d), (xb, _) in zip(cross, cross[1:]):
            wind += d
            if not wind:
                continue
            a, b = max(xa - x0, 0), min(xb - x0, w)
            for px in range(int(a), math.ceil(b)) if a < b else ():
                cov[row + px] += (min(b, px + 1) - max(a, px)) / SUB
    return cov


# Phosphor Icons (MIT, assets/icons/phosphor/LICENSE), @phosphor-icons/core 2.1.1: each file a 256 unit viewBox of
# filled paths. A theme's icons are these, drawn at its sizes, and q2-coda's own few in Phosphor's regular style
# (assets/icons/q2coda, the same 256 unit filled paths, under the repo's licence), named here by that folder.
PHOSPHOR = ROOT/'assets/icons/phosphor'


def optical(polys, px, ink, ref=None):
    """polys, a glyph laid in a px box, at its optical size: scaled about its ink's centre, put on the box's, until its
    ink's longer side is ink px (docs/oled-design.md Icons), so glyphs of one size read as one size. ref: another
    glyph's polygons whose ink sets the scale and centre instead, so parts drawn apart (Wi-Fi's lit and unlit arcs)
    stay in register."""
    xs, ys = [x for poly in ref or polys for x, _ in poly], [y for poly in ref or polys for _, y in poly]
    k, cx, cy = ink / max(max(xs) - min(xs), max(ys) - min(ys)), (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
    return [[(px / 2 + (x - cx) * k, px / 2 + (y - cy) * k) for x, y in poly] for poly in polys]


def glyph_png(polys, px, rgb, ink=0.0):
    """polys, a glyph laid in a px box, as a px square PNG in rgb, filled nonzero; ink, its optical size (optical)."""
    if ink:
        polys = optical(polys, px, ink)
    return png(px, px, laid(px, px, [(coverage_nonzero(polys, (0, 0, px, px)), lambda x, y: (rgb, 1))]))


def radio_tower(px, rgb):
    """The broadcast tower (RADIO_TOWER, a 24 unit box) as a px square PNG in rgb."""
    return glyph_png([[(x * px / 24, y * px / 24) for x, y in poly] for poly in path(RADIO_TOWER)], px, rgb)


def tidal_mark(px, rgb, ink=0.0):
    """TIDAL's mark (TIDAL, a 20 unit box) as a px square PNG in rgb (glyph_png's ink)."""
    return glyph_png([[(x * px / 20, y * px / 20) for x, y in poly] for poly in path(TIDAL)], px, rgb, ink)


def spinner(f, spokes=8, px=15, radii=(3.4, 6.4), width=1.9, fade=0.1, rgb=0xffffff):
    """A spinner's frame f, a px square PNG: spokes round capped from radii[0] to radii[1] from the centre, width wide,
    in rgb, the head at f spokes' turn clockwise from 12 o'clock and each spoke behind it fade dimmer. The defaults
    are the list's (SPIN_* below); SW_SPIN_* the software update's."""
    c, layers = px / 2, []
    for k in range(spokes):
        a = 2 * math.pi * k / spokes - math.pi / 2
        ends = [(c + r * math.cos(a), c + r * math.sin(a)) for r in radii]
        alpha = 1 - fade * ((f - k) % spokes)
        layers.append((stroke([ends], width), lambda x, y, alpha=alpha: (rgb, alpha)))
    return png(px, px, compose((0, 0, px, px), layers))


def phosphor_polys(name, px):
    """Phosphor's icon name (its file's stem, the weight in it; 'q2coda/<stem>' for one of ours) as polygons in a px
    box."""
    svg = (PHOSPHOR.parent/f'{name}.svg' if name.startswith('q2coda/') else PHOSPHOR/f'{name}.svg').read_text()
    check('viewBox="0 0 256 256"' in svg, f'{name}: expected a 256 unit viewBox')
    return [[(x * px / 256, y * px / 256) for x, y in poly] for d in re.findall(r'\sd="([^"]+)"', svg) for poly in path(d)]


def phosphor(name, px, rgb, ink=0.0):
    """Phosphor's icon name as a px square PNG in rgb, its paths filled nonzero (glyph_png's ink)."""
    return glyph_png(phosphor_polys(name, px), px, rgb, ink)


def status_fit(polys, w):
    """polys, a glyph laid in a STATUS_PX box, as the status row sizes it: scaled until its ink is STATUS_INK px tall
    (its height, not its longer side, so a wide glyph such as wifi-x is as tall as the clock's digits too), its ink's
    top on STATUS_TOP and centred across a w px wide box."""
    xs, ys = [x for poly in polys for x, _ in poly], [y for poly in polys for _, y in poly]
    k, cx = STATUS_INK / (max(ys) - min(ys)), (min(xs) + max(xs)) / 2
    return [[(w / 2 + (x - cx) * k, STATUS_TOP + (y - min(ys)) * k) for x, y in poly] for poly in polys]


def status_glyph(name, w, rgb):
    """Phosphor's icon name at the status row's size (status_fit), a w x STATUS_PX PNG in rgb."""
    box = (0, 0, w, STATUS_PX)
    return png(w, STATUS_PX, laid(w, STATUS_PX, [(coverage_nonzero(status_fit(phosphor_polys(name, STATUS_PX), w), box),
                                                  lambda x, y: (rgb, 1))]))


def wifi_fan(w):
    """iOS's Wi-Fi fan in a w x STATUS_PX box, as polygons per band: a STATUS_FAN_DEG sector from an apex on the
    ink's bottom (STATUS_TOP + STATUS_INK, the clock's baseline), radius STATUS_INK, cut at STATUS_FAN into the dot
    and two bands, centred across w."""
    ax, ay, half = w / 2, STATUS_TOP + STATUS_INK, STATUS_FAN_DEG / 2
    angles = [math.radians(a - 90) for a in (half * (2 * i / 30 - 1) for i in range(31))]
    return [[(ax + r1 * math.cos(t), ay + r1 * math.sin(t)) for t in angles] +
            [(ax + r0 * math.cos(t), ay + r0 * math.sin(t)) for t in reversed(angles)] for r0, r1 in STATUS_FAN]


def status_wifi(level, lit, unlit):
    """The status row's Wi-Fi at level (1 to 3 lit of wifi_fan's dot and bands, from the dot), a STATUS_WIFI_W x
    STATUS_PX PNG: the lit part in lit, the rest in unlit, so the shape keeps its size at every level."""
    w, box = STATUS_WIFI_W, (0, 0, STATUS_WIFI_W, STATUS_PX)
    return png(w, STATUS_PX, laid(w, STATUS_PX, [(coverage_nonzero([band], box), lambda x, y, c=lit if i < level else unlit: (c, 1))
                                                 for i, band in enumerate(wifi_fan(w))]))


def status_turn(name, px, ink, rgb, turn):
    """name at its optical size, turned clockwise by turn (a fraction of a whole turn) about the box's centre, a px
    square PNG in rgb: a frame of the status row's sync arrows."""
    a, c = 2 * math.pi * turn, px / 2
    polys = [[(c + (x - c) * math.cos(a) - (y - c) * math.sin(a), c + (x - c) * math.sin(a) + (y - c) * math.cos(a)) for x, y in poly]
             for poly in optical(phosphor_polys(name, px), px, ink)]
    return png(px, px, laid(px, px, [(coverage_nonzero(polys, (0, 0, px, px)), lambda x, y: (rgb, 1))]))




def inks(name, *keys):
    """A colour token's (tools/theme.py TOKENS) inks by suffix: d its dark half, l its light half, w white on the
    selection, td and tl the halves again under a t prefix."""
    ink = {'d': token(name), 'l': token(name, True), 'w': 0xffffff}
    return {k: ink[k[-1]] for k in keys}


# A theme's own glyphs (docs/oled-design.md Icons), in final colours per half: _d on black, _l on paper, _w on the
# selection. The list's caret-right (list_marks.c chevron_icon) and the status row's play state.
CARET_PX = 15
STATE_PX = 13
CHEVRON_INKS = inks('chevron', 'd', 'l', 'w')
CHEVRON_MUTED_INKS = inks('tertiary', 'td', 'tl')  # a disabled row's (list_intodown, list_marks.c chevron_muted): Text Tertiary per half
# The activity spinner in the selected row's chevron place while Cover Flow opens (coverflow.c coverflow_spinner,
# list_marks.c spinner_icon): iOS's eight spokes, white on the selection, frame f's head at f eighths of a turn
# clockwise from 12 o'clock, each spoke behind it SPIN_FADE dimmer.
SPIN_SPOKES = 8
SPIN_R = (3.4, 6.4)  # a spoke's ends' distances from the centre, round capped
SPIN_STROKE = 1.9
SPIN_FADE = 0.1
STATE_INKS = inks('primary', 'd', 'l')
# The status row's glyphs by role (status_row.c, docs/oled-design.md#status-row), each in Text Primary per half
# (STATE_INKS). Every one is STATUS_INK px tall, the clock's digits' height, with its ink's top STATUS_TOP rows into a
# STATUS_PX tall box that status_row.c puts on the row's centre less 7, so the glyphs stand on rows 17 to 27 with the
# digits. STATUS_FIT's are Phosphor's, sized by their ink's height (status_fit), centred across their box: STATUS_PX
# square, or wider where the ink is (wifi-x, the car); the volume pill's speakers among them. The Bluetooth rune dims while
# a device connects (BT_SPIN_INKS).
# Wi-Fi is iOS's fan, drawn here (wifi_fan): its levels light 1 to 3 of the dot and two bands over the rest in
# STATUS_UNLIT. The play state and the padlock are 13 px boxes drawn as they were (their ink 10.6 and 11 px); the
# sync arrows turn through STATUS_SPIN frames of a half turn (the glyph repeats every half turn), sized by their
# longer side as they turn, in Text Primary (the user's, 2026-11-20); the download's arrow sits in its ring, white.
STATUS_PX = 14
STATUS_INK = 11.0
STATUS_TOP = 1.0
STATUS_WIFI_W = 16  # the fan's 15.6 px, status_row.c SR_WIFI_W
STATUS_CAR_W = 19  # car-profile-bold's 18.5 px at 11 tall, status_row.c SR_CAR_W
STATUS_FAN_DEG = 90
STATUS_FAN = ((0, 3.6), (5.0, 7.3), (8.7, 11.0))  # the dot and the bands' radii: 1.4 px of ground between
STATUS_GLYPHS = {'play': ('play-fill', STATE_PX, 0), 'pause': ('pause-fill', STATE_PX, 0), 'keylock': ('lock-simple-fill', STATE_PX, 0),
                 'status_down': ('arrow-down-bold', 8, 0)}
STATUS_FIT = {'status_wifi_x': ('wifi-x-bold', STATUS_WIFI_W), 'status_bt': ('bluetooth-bold', STATUS_PX),
              'status_headphones': ('headphones-bold', STATUS_PX), 'status_car': ('car-profile-bold', STATUS_CAR_W),
              # the volume pill's speaker, and muted at 0 (vol_pill.c)
              'status_vol': ('speaker-simple-high-fill', STATUS_PX), 'status_mute': ('speaker-simple-x-fill', STATUS_PX)}
STATUS_UNLIT = {'d': 0x3a3a3c, 'l': 0xc7c7cc}
STATUS_SECONDARY = inks('secondary', 'd', 'l')
STATUS_SPIN = 30  # status_row.c SR_SPIN: a half turn a second at 30 fps
KEYLOCK_CARD_PX = 56  # Buttons Lock's card's padlock (keylock.c CARD_GLYPH)
# Now Playing's shuffle and repeat (playing.modes.icons, np_bar.c np_modes): bold, MODE_PX, off in Text Secondary per
# half, on in the theme's own accent's top tone in both; repeat-once is repeat's on glyph for Repeat One.
MODE_PX = 22
MODE_INKS = inks('secondary', 'd', 'l')
MODE_DIM_INKS = {'d': 0x444446, 'l': 0xc5c5c5}  # shuffle and repeat while a radio plays: the app picks the order
MODE_PILL_PX = 18  # the antenna in the radio's pill
# A row's leading glyph (Songs' Shuffle row, list.shuffle.row) and Now Playing's heart and More, regular weight (the
# full heart fill) at the 20 px row type's size
ROW_ICON_PX = 20
ROW_INK = 15.5  # a 20 px symbol's optical size: its ink's longer side (phosphor's ink)
NP_HEART_PX = 34  # Now Playing's heart's image, centred in its target under the album (tools/ipod.py NP_GRID_HEART)
# ... and its ink in that image (the user's mock-up, design/mockups/mock_np_heart.py, 2026-11-19): NP_HEART_INK px tall,
# its left NP_HEART_LEFT px in, so on screen at x 194, the text column's left less 2, and its middle NP_HEART_MID down,
# y 164; off, Phosphor's heart (bold) in Text Secondary; on, heart (fill) in the Favorite token
NP_HEART_INK, NP_HEART_LEFT, NP_HEART_MID = 18, 1, 18


def np_heart(name, rgb):
    """Now Playing's heart, Phosphor's name, as an NP_HEART_PX square PNG in rgb: NP_HEART_INK px of ink tall, its
    left NP_HEART_LEFT px in and its middle NP_HEART_MID px down."""
    polys = phosphor_polys(name, 256)
    xs, ys = [x for poly in polys for x, _ in poly], [y for poly in polys for _, y in poly]
    k, mid = NP_HEART_INK / (max(ys) - min(ys)), (min(ys) + max(ys)) / 2
    laid_polys = [[(NP_HEART_LEFT + (x - min(xs)) * k, NP_HEART_MID + (y - mid) * k) for x, y in poly] for poly in polys]
    return glyph_png(laid_polys, NP_HEART_PX, rgb)
VOL_SPK_PX = 16  # Now Playing's volume speakers (patch/offsets.inc VOL_SPK_PX, the same)
FOLDER_INKS = inks('secondary', 'd', 'l', 'w')  # Text Secondary in each half, white on the bar
CTA_RING_INKS = inks('primary', 'd', 'l')  # the buttons' focus ring (cta_ring_end), Text Primary per half
SORT = 'M0 0H16V2H0ZM0 5H11V7H0ZM0 10H6V12H0Z'
SORT_BOX = (0, 0, 16, 12)  # Songs' Sort (navigation.c action_row), white as stock's glyphs: Light Mode flips it to the ink
CIRCLE_CHECK_PX = 11  # the row editors' check circle's glyphs (list_marks.c CIRCLE_CHECK_PX, CIRCLE_LOCK_PX)
CIRCLE_LOCK_PX = 10
CHECK_PX = 12  # a picker's current choice (settings.values.switch, navigation.c paint_radio): the accent, white on the bar
# The confirmation's glyph after a shuffle (feedback.c feedback_shuffled): bold, the accent's top tone in both halves
CONFIRM_GLYPH_PX = 56
# A Ratings row's stars (ratings.c rate_stars_paint): star-fill for the song's, star for the rest, regular, in Text
# Primary in each half and white on the bar (RATE_INKS), no colour
RATE_PX = 14
RATE_INKS = inks('primary', 'd', 'l', 'w')
# Now Playing's rating page (np_rate.c NR_STAR, NR_STAR_BIG): star-fill up to the rating and star for the rest,
# regular, Text Primary in each half (STATE_INKS); the payload draws the rest's outlines at 30% over the glow. The
# wheel's target draws at NP_STAR_BIG_PX, 1.18 times as large (the approved mock-up's scale)
NP_STAR_PX = 44
NP_STAR_BIG_PX = 52
# Streaming's service marks (home.c stream_marks): monochrome at 20 px, no disc, in the folder's inks; Phosphor's
# regular glyphs, TIDAL's own mark (Phosphor has none) drawn here as its four diamonds, three across and one under
# the middle, each corner to corner with the next, and the q2-subsonic card app's (home.c stream_apps) our own
# submarine with its sonar, its strokes outlined
STREAM_PX = 20
STREAM_MARKS = {'spotify': 'spotify-logo', 'app': 'app-window', 'radio': 'radio', 'airplay': 'airplay', 'config': 'sliders-horizontal',
                'subsonic': 'q2coda/subsonic'}
# A broadcast tower, a mast under a dot and two pairs of waves, in a 24 unit box: the Car Remote app's ic_radio.xml
# (q2-auto-remote app/src/main/res/drawable), the glyph in Home's pane while Radio is Now Playing (radio_pane.c), drawn
# white at RADIO_TOWER_PX, 62% of its 64 px badge (approved 2026-11-20, design/radio-tuner/radio-now-playing/pane-v6)
RADIO_TOWER = 'M8.19,18.35L11.14,11.74C10.72,11.47 10.45,10.99 10.45,10.46C10.45,9.6 11.15,8.92 12,8.92C12.85,8.92 13.55,9.6 13.55,10.46C13.55,10.99 13.28,11.47 12.86,11.74L15.81,18.35Q16.12,19.06 15.41,19.37Q14.71,19.69 14.39,18.98L13.56,17.12L10.44,17.12L9.61,18.98Q9.46,19.33 9.2,19.43Q8.94,19.54 8.59,19.37Q7.88,19.06 8.19,18.35ZM17.65,4.8Q20,7.15 20,10.46Q20,13.77 17.65,16.12Q17.11,16.67 16.57,16.12Q16.01,15.57 16.57,15.02Q18.45,13.14 18.45,10.46Q18.45,7.79 16.57,5.9Q16.28,5.63 16.28,5.35Q16.28,5.07 16.57,4.8Q17.11,4.26 17.65,4.8ZM7.43,5.9Q5.55,7.79 5.55,10.46Q5.55,13.14 7.43,15.02Q7.99,15.57 7.43,16.12Q7.16,16.39 6.89,16.39Q6.62,16.39 6.35,16.12Q4,13.77 4,10.46Q4,7.15 6.35,4.8Q6.89,4.26 7.43,4.8Q7.99,5.35 7.43,5.9ZM15.48,6.97Q16.93,8.41 16.93,10.46Q16.93,12.5 15.48,13.95Q14.94,14.5 14.4,13.95Q13.85,13.41 14.4,12.85Q15.38,11.86 15.38,10.46Q15.38,9.06 14.4,8.06Q14.12,7.79 14.12,7.52Q14.12,7.24 14.4,6.97Q14.94,6.43 15.48,6.97ZM9.6,8.06Q8.62,9.06 8.62,10.46Q8.62,11.86 9.6,12.85Q10.15,13.41 9.6,13.95Q9.33,14.22 9.06,14.22Q8.79,14.22 8.52,13.95Q7.07,12.5 7.07,10.46Q7.07,8.41 8.52,6.97Q9.06,6.43 9.6,6.97Q9.88,7.24 9.88,7.52Q9.88,7.79 9.6,8.06ZM12.88,15.57L12,13.6L11.12,15.57Z'
RADIO_TOWER_PX = 39
TIDAL = ''.join(f'M{x - 3.3} {y}L{x} {y - 3.3}L{x + 3.3} {y}L{x} {y + 3.3}Z' for x, y in ((3.4, 6.7), (10, 6.7), (16.6, 6.7), (10, 13.3)))
# The menu sheet's symbols (menu_modal.c, a set with modal): regular weight at MENU_ICON_PX in FOLDER_INKS, and the
# picker's check in the accent off the bar
MENU_ICON_PX = 19
MENU_ICONS = ('funnel-simple', 'arrow-bend-down-right', 'queue', 'heart', 'list-plus', 'shuffle',
              'vinyl-record', 'microphone-stage', 'folder', 'clock-counter-clockwise', 'list-numbers',
              'bookmark-simple', 'dots-three', 'plus', 'list-checks', 'skip-forward', 'skip-back', 'repeat', 'stack',
              'airplay', 'download-simple', 'cloud-arrow-down', 'trash', 'speedometer', 'pencil-simple',
              'caret-up', 'caret-down', 'check', 'sliders-horizontal', 'info', 'sneaker-move', 'play', 'cloud-slash')
PAIR_ICON_PX = 16  # an artist's or album's page's action pair (artist_page.c PAIR_ICON)
BUMP_PX = 16  # Up Next's bump arrow (upnext.c)
BUMP_INKS = inks('tertiary', 'd', 'l', 'w')
PAIR_ICONS = ('shuffle', 'radio', 'play')
# Where a song lives (catalogue.c catalogue_row_mark, a song row's right column): the cloud for a server song that
# streams, the arrow for one a card app downloaded, regular, in Text Tertiary (each half's) and white on the bar; and
# Storage > Downloads' items (storage.c storage_paint): the arrow for one the user picked in the accent's top tone,
# the circling arrows for one the app keeps on its own in Text Secondary (each half's), both white on the bar
MARK_PX = 18
MARKS = {'cloud': 'cloud', 'down': 'arrow-circle-down'}
MARK_INKS = BUMP_INKS
QUALITY_PX = 12  # Now Playing's About, its quality badge's glyph (np_about.c AB_GLYPH): Text Primary per half
# Playlists (playlists.c pl_tile_paint, playlist_pages.c): a playlist's and On-The-Go's 36 px tile glyph in Text
# Tertiary per half, white on the selection; the pages' action rows' glyphs in the accent, white on the selection; a
# song's mark against the playlist in Add Songs (already in, added now, taken out now: Text Secondary, green, red)
TILE_PX = 20
TILE_GLYPHS = {'playlist': 'playlist-fill', 'otg': 'sneaker-move-fill'}
TILE_INKS = BUMP_INKS
PL_ACTION_PX = 18
PL_ACTIONS = {'shuffle': 'shuffle', 'add': 'plus-circle', 'save': 'arrow-down', 'clear': 'x', 'search': 'magnifying-glass'}
PL_MARK_PX = 20
# Control Center's glyphs (control_center.c cc_glyph, from mock_cc4_tight.py): Phosphor fill, white on its toggles
# and transport (Play/Pause the larger), the sliders' sun and speaker in the block's #1C1C1E on their light fill. The
# Wi-Fi circle's is the link's (cc_wifi): wifi-slash off, wifi-none on but not joined, wifi-low to wifi-high by the
# status row's level (Phosphor's fill levels draw the whole fan and fill as much of it as the signal), wifi-x with no
# internet. The toggles' glyphs are at CC_INK, their optical size: each ink's longer side 18 px, so the wide fan and
# the tall Bluetooth rune read as one size. The USB row (while a cable is in, design/usb-row mock_usb_row.py) has
# Phosphor's usb at 20 px in Text Primary and a 13 px bold caret-right in the chevron's grey.
CC_ICONS = {'wifi_slash': ('wifi-slash-fill', 22, 0xffffff), 'wifi_0': ('wifi-none-fill', 22, 0xffffff),
            'wifi_1': ('wifi-low-fill', 22, 0xffffff), 'wifi_2': ('wifi-medium-fill', 22, 0xffffff),
            'wifi_3': ('wifi-high-fill', 22, 0xffffff), 'wifi_x': ('wifi-x-fill', 22, 0xffffff),
            'bluetooth': ('bluetooth-fill', 22, 0xffffff), 'airplay': ('airplay-fill', 22, 0xffffff),
            'car': ('car-profile-fill', 22, 0xffffff), 'leaf': ('leaf-fill', 22, 0xffffff),
            'eq': ('equalizer-fill', 22, 0xffffff),
            'sun': ('sun-fill', 18, token('raised')), 'speaker': ('speaker-high-fill', 18, token('raised')),
            'back': ('skip-back-fill', 22, 0xffffff), 'forward': ('skip-forward-fill', 22, 0xffffff),
            'pause': ('pause-fill', 30, 0xffffff), 'play': ('play-fill', 30, 0xffffff),
            'usb': ('usb-fill', 20, 0xffffff), 'chev': ('caret-right-bold', 13, token('chevron'))}
# Light Mode's (control_center.c cc_glyph, <name>_l): the art is final, so the light half has its own: a toggle
# off, the transport and the USB glyph in the light half's Text Primary on the light block, the USB row's chevron in
# its chevron grey, the sliders' sun and speaker white on the fill that is dark there. A toggle on keeps the white on
# Accent Blue.
CC_LIGHT = {**{n: token('primary', True) for n in CC_ICONS if n not in ('sun', 'speaker')},
            'sun': 0xffffff, 'speaker': 0xffffff, 'chev': token('chevron', True)}
CC_INK = {n: 18.0 for n in ('wifi_slash', 'wifi_0', 'wifi_1', 'wifi_2', 'wifi_3', 'wifi_x', 'bluetooth', 'airplay',
                            'car', 'leaf', 'eq')}

# Every mode page's glyph by role (feedback.c mode_page_paint, docs/oled-design.md#usb): fill, white on its solid
# circle, MODE_GLYPH_PX: a card app's sign-in steps (addon.c qr_paint: phone, check, done, fail, error), USB's modes
# (usb_ui.c: files, audio, debug; ejected is done), Bluetooth's receiver (bt_recv.c: receive) and Software Update's
# results (sw_update.c: done for up to date, none, low, bad, restart). Each glyph is drawn once; the bold check is
# done for all three that show it.
MODE_GLYPH_PX = 44
MODE_GLYPHS = {'phone': 'device-mobile-fill', 'check': 'arrows-clockwise-fill', 'done': 'check-bold',
               'fail': 'cloud-slash-fill', 'error': 'x-bold', 'files': 'folder-simple-fill', 'audio': 'speaker-hifi-fill',
               'debug': 'terminal-window-fill', 'receive': 'bluetooth-fill', 'none': 'file-arrow-down-fill',
               'low': 'battery-warning-fill', 'bad': 'warning-fill', 'restart': 'arrow-clockwise-bold'}
# The USB pages (usb_ui.c): the chooser's row symbols (regular, Text Secondary per half, white on the selection) and
# the USB toasts' glyphs (fill, 15 px beside the toast's words, each in its colour: plug in Text Secondary, the check
# in Positive green, the speaker white).
USB_ROW_PX = 19
USB_ROWS = {'charge': 'lightning', 'files': 'folder-simple', 'audio': 'speaker-hifi', 'debug': 'terminal-window'}
USB_TOAST_PX = 15
PAIR_GLYPH_PX = 28  # the pairing card's phone, over its title (pair_prompt.c, feedback.c CARD_GLYPH)
# adb's Allow this computer? (usb_ui.c auth_open): Phosphor laptop, regular, Text Primary per half, beside the card's
# title (feedback.c AC_GLYPH) and in the status pill after Allow (vol_pill.c VP_MSG_GLYPH)
ADB_CARD_PX = 24
ADB_PILL_PX = 15
# Settings > Bluetooth (bt_ui.c, docs/oled-design.md#bluetooth): a device row's kind at the row symbols' size (regular,
# Text Secondary per half, white on the selection), the scan's spinner beside Other Devices in Text Tertiary per half,
# and the connection toasts' glyphs (fill, 15 px, white, a drop's in Text Secondary; the low headset battery's in the
# battery's red).
BT_ROW_GLYPHS = {'bt': 'bluetooth', 'headphones': 'headphones', 'car': 'car-profile', 'speaker': 'speaker-hifi',
                 'phone': 'device-mobile'}
BT_SPIN_INKS = {'d': 0x858585, 'l': 0x6e6e6e}
BT_TOASTS = {'headphones': ('headphones-fill', 0xffffff), 'car': ('car-profile-fill', 0xffffff),
             'speaker': ('speaker-hifi-fill', 0xffffff), 'phone': ('device-mobile-fill', 0xffffff),
             'bt': ('bluetooth-fill', 0xffffff), 'low': ('battery-warning-fill', 0xff453a),
             'warn': ('warning-fill', 0xff9f0a), 'no': ('prohibit-fill', 0xababab)}  # Guest Play's (guest_ui.c)
# Settings > Car Remote's phone rows (carremote_page.c): Phosphor device-mobile, regular, Text Secondary per half,
# white on the selection
CR_PHONE_PX = 20
# Settings > Network > LocalSend (localsend_ui.c, docs/localsend.md): Send Logs' paper plane (fill, the accent, white on
# the selection) as Pair a Phone's plus; a device's laptop or phone (regular, Text Secondary per half, white on the
# selection) at CR_PHONE_PX on its row and LS_BIG_PX on a transfer's page; the Accept card's at PAIR_GLYPH_PX in the
# accent; its toast's (fill, white, USB_TOAST_PX); the share sheet's file line (file-text, Text Tertiary per half).
LS_DEVICES = {'laptop': 'laptop', 'phone': 'device-mobile'}
LS_BIG_PX = 48
LS_SEND_PX = 18
LS_FILE_PX = 15
# The software update's check (sw_update.c, docs/oled-design.md Software Update): iOS's twelve-spoke spinner in the
# half's Text Primary (_d white, _l black) on no circle, 48 px across (its results' glyphs are MODE_GLYPHS).
SW_SPIN_SPOKES = 12
SW_SPIN_PX = 48
SW_SPIN_R = (13.2, 22)  # round capped: the outer cap reaches 24, the mock-up's radius
SW_SPIN_STROKE = 4
SW_SPIN_FADE = 0.75 / 11  # the spoke behind the head's head a quarter as bright
# Now Playing's loading spinner (np_stream.c, docs/server-library.md#download-ahead-and-the-streaming-state): the
# software update's twelve spokes and fade at 14 px, Text Secondary per half, as streaming-v3's variant D draws it.
NP_SPIN_PX = 14
NP_SPIN_R = (3.25, 6.5)
NP_SPIN_STROKE = 1.5
NP_SPIN_INKS = inks('secondary', 'd', 'l')
# Software Update's offer (sw_update.c sw_offer_paint): its source's glyph, white on the accent's 60 px circle: the
# internet's download tray, the card's file.
SW_OFFER_PX = 30
SW_OFFER = {'net': 'download-simple-fill', 'sd': 'file-arrow-down-fill'}
# The library scan's page (scan_page.c, docs/oled-design.md#update-library): its ending's figure, regular weight,
# SCAN_GLYPH_PX in Text Secondary per half (done the check circle; stopped short the memory chip, as the empty
# state's card), and the folder glyph before the scanned folder's name, SCAN_FOLDER_PX. Scanning is SW_SPIN's spinner.
SCAN_GLYPH_PX = 60
SCAN_FOLDER_PX = 15
SCAN_GLYPHS = {'done': 'check-circle', 'card': 'memory', 'folder': 'folder'}
SCAN_INKS = inks('secondary', 'd', 'l')
HOLD_BACK_PX = 20  # search_strip.c's Return-held card: Phosphor arrow-u-up-left, bold, white
# The empty state (feedback.c empty_state): its glyph in outline, Phosphor's regular weight at EMPTY_PX, in Text
# Secondary per half (_d, _l), by the glyph a page names (empty_t.glyph; docs/oled-design.md#empty-and-offline has the
# pages; the user's outline style, 2026-11-16). Phosphor has no sd-card: the card's is its memory chip, as the
# mock-up's fallback (mock_placeholders.py FALLBACK). Nor a heart-slash: Favorites' is q2-coda's own, traced from the
# approved mock-up (assets/icons/q2coda/heart-slash.svg).
EMPTY_PX = 60
EMPTY_GLYPHS = {g: g for g in (
    'cards', 'music-notes', 'vinyl-record', 'microphone-stage', 'music-notes-simple', 'guitar', 'folder', 'playlist',
    'clock-counter-clockwise', 'chart-bar', 'sparkle', 'timer', 'calendar-blank', 'star', 'magnifying-glass',
    'radio', 'cloud-slash', 'download-simple', 'list-numbers', 'car-profile', 'broadcast', 'wifi-slash',
    'arrows-clockwise', 'check-circle', 'arrow-circle-down', 'cloud')} | {'sd-card': 'memory',  # the last four: Sync's states
    'heart-slash': 'q2coda/heart-slash'}
USB_TOASTS = {'unplugged': ('plug-fill', token('secondary')), 'ejected': ('check-circle-fill', token('positive')), 'audio': ('speaker-hifi-fill', 0xffffff),
              'car': ('car-profile-fill', 0xffffff), 'carwarn': ('warning-fill', token('warning'))}  # Car Audio's (usb_car.c)
# The status row's battery (list_marks.c battery_slim): charging, Phosphor's lightning, too weak a charger its warning,
# fill weight at BATT_GLYPH_PX in Text Primary, with a BATT_GLYPH_GAP px gap in the Canvas cut round it (iOS's way of
# keeping it off the charge's fill; over the body's empty black it is not seen), per half. The image is BATT_GLYPH_BOX,
# the glyph centred BATT_GLYPH_AT in from its corner, so it lands on the body's centre, a half pixel included.
BATT_GLYPH_PX = 7
BATT_GLYPH_GAP = 1
BATT_GLYPH_BOX = (9, 10)
BATT_GLYPH_AT = (4.5, 5.0)
# Full or held at Charge Limit on the cable, an upright plug in the bolt's place, drawn on whole pixels in the box
# (Phosphor's plug is diagonal, and turned upright at 7 px its prongs merge into a blob): two prongs 1 x 2.5 at x 3
# and 5, their tops rounded 0.5; the body 5 x 3.5 at x 2, y 4, its lower corners rounded 1.5; the cord 1 x 2 at x 4,
# y 7. Its ink is 5 x 7, centred on the box's 4.5 column, so on the body's centre (approved option A,
# ~/q2-emu/design/plugged-full/NOTES.md).


def plug_glyph(x, y):
    """1 where box point (x, y), in box px, is inside the plug."""
    def rounded(x0, y0, x1, y1, r, top):
        if not (x0 <= x <= x1 and y0 <= y <= y1):
            return False
        cy = y0 + r if top else y1 - r
        if (y >= cy) if top else (y <= cy):
            return True
        cx = min(max(x, x0 + r), x1 - r)
        return (x - cx) ** 2 + (y - cy) ** 2 <= r * r
    return (rounded(3, 2, 4, 4.5, 0.5, True) or rounded(5, 2, 6, 4.5, 0.5, True)
            or rounded(2, 4, 7, 7.5, 1.5, False) or (4 <= x <= 5 and 7 <= y <= 9))


BATT_GLYPHS = {'bolt': 'lightning-fill', 'warn': 'warning-fill', 'plug': plug_glyph}
BATT_GLYPH_INKS = {'d': (0xffffff, 0x000000), 'l': (0x000000, 0xffffff)}  # (glyph, gap) per half
# Charging's toasts (charging.c): their 15 px glyph beside the words, fill, each in its colour; the battery's own in
# Text Primary per half.
CHARGE_TOASTS = {'charging': ('lightning-fill', token('positive')), 'weak': ('warning-fill', token('warning')),
                 'full': ('battery-full-fill', token('positive')), 'low': ('battery-warning-fill', token('negative'))}
CHARGE_TOAST_BATTERY = STATE_INKS
# The plug-in screen's bolt beside its percentage (charging.c S1_BOLT), fill, Positive green
CHARGE_BOLT_PX = 26
PL_MARKS = {'in': ('check-circle-fill', inks('secondary', 'd', 'l')), 'add': ('plus-circle-fill', inks('positive', 'd', 'l')),
            'out': ('minus-circle-fill', {'d': 0xff6b5e, 'l': 0xff6b5e})}


def inset_glyph(name, ink, gap):
    """Phosphor's icon name at BATT_GLYPH_PX in ink, centred BATT_GLYPH_AT in a BATT_GLYPH_BOX PNG, over a gap in
    gap round it, BATT_GLYPH_GAP px: the glyph's shape grown that far, measured on an 8x finer grid. name may instead
    be a shape drawn in the box itself, (x, y) in box px -> inside or not (plug_glyph)."""
    k, (w, h), (cx, cy) = 8, BATT_GLYPH_BOX, BATT_GLYPH_AT
    if callable(name):
        fine = [float(name((i % (w * k) + 0.5) / k, (i // (w * k) + 0.5) / k)) for i in range(w * k * h * k)]
    else:
        svg = (PHOSPHOR/f'{name}.svg').read_text()
        scale, x0, y0 = BATT_GLYPH_PX * k / 256, (cx - BATT_GLYPH_PX / 2) * k, (cy - BATT_GLYPH_PX / 2) * k
        polys = [[(x0 + x * scale, y0 + y * scale) for x, y in poly] for d in re.findall(r'\sd="([^"]+)"', svg) for poly in path(d)]
        fine = coverage_nonzero(polys, (0, 0, w * k, h * k))
    r = BATT_GLYPH_GAP * k
    disc = [(dx, dy) for dx in range(-r, r + 1) for dy in range(-r, r + 1) if dx * dx + dy * dy <= r * r]
    inside = {(i % (w * k), i // (w * k)) for i, c in enumerate(fine) if c >= 0.5}
    grown = {(x + dx, y + dy) for x, y in inside for dx, dy in disc}
    def down(on):
        return [sum(on(x * k + i, y * k + j) for i in range(k) for j in range(k)) / (k * k) for y in range(h) for x in range(w)]
    return png(w, h, laid(w, h, [(down(lambda x, y: (x, y) in grown), lambda x, y: (gap, 1)),
                                 (down(lambda x, y: fine[y * w * k + x]), lambda x, y: (ink, 1))]))


def gradient(stops, y0, y1):
    """A y -> 0xRRGGBB function: stops (pos %, 0xRRGGBB) from y0 to y1, as an SVG linearGradient's."""
    def at(y):
        p = max(stops[0][0], min(100, (y - y0) * 100 / (y1 - y0)))  # before the first stop, its colour
        for (pa, ca), (pb, cb) in zip(stops, stops[1:]):
            if p <= pb:
                t = (p - pa) / (pb - pa)
                return sum(round((ca >> s & 255) + ((cb >> s & 255) - (ca >> s & 255)) * t) << s for s in (16, 8, 0))
        return stops[-1][1]
    return at


def laid(w, h, layers):
    """RGBA bytes: layers (coverage per pixel, paint(x, y) -> (0xRRGGBB, alpha 0 to 1)) source-over, straight alpha."""
    px = [(0.0, 0.0, 0.0, 0.0)] * (w * h)
    for cov, paint in layers:
        for i, c in enumerate(cov):
            if c:
                rgb, a = paint(i % w + 0.5, i // w + 0.5)
                a *= min(c, 1)
                if not a:
                    continue
                r, g, b, d = px[i]
                out = a + d * (1 - a)  # never 0: a is above 0 here
                px[i] = (*((v * d * (1 - a) + (rgb >> s & 255) * a) / out for v, s in ((r, 16), (g, 8), (b, 0))), out)
    return b''.join(bytes((round(r), round(g), round(b), round(a * 255))) for r, g, b, a in px)


def compose(box, layers):
    """box's RGBA bytes: layers (polygons, paint(x, y) -> (0xRRGGBB, alpha 0 to 1)) source-over, straight alpha."""
    x0, y0, w, h = box
    def layer(polys, paint):
        return coverage(polys, box), lambda x, y: paint(x0 + x, y0 + y)
    return laid(w, h, [layer(*l) for l in layers])


def png(w, h, rgba):
    def chunk(tag, data):
        return struct.pack('>I', len(data)) + tag + data + struct.pack('>I', zlib.crc32(tag + data))
    rows = b''.join(b'\0' + rgba[y * 4 * w:(y + 1) * 4 * w] for y in range(h))
    return b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', w, h, 8, 6, 0, 0, 0)) + chunk(b'IDAT', zlib.compress(rows, 9)) + chunk(b'IEND', b'')


# A radius r corner's coverage table, the one patch/corner_cover.c works out at run time for any radius the payload
# rounds (docs/internals.md#rounded-corners): the tests hold the C to this.
def corner_cover(r):
    """A radius r corner's coverage out of 255, r rows of r pixels, the outermost first."""
    n = 16
    inside = lambda x, y: sum(math.hypot(r - x - (i + .5) / n, r - y - (j + .5) / n) <= r for i in range(n) for j in range(n))
    return bytes(round(inside(x, y) * 255 / n ** 2) for y in range(r) for x in range(r))


# The buttons' focus ring (ui_kit.c cta_capsule, docs/oled-design.md#buttons): CTA_RING_W px in Text Primary, 3 px out
# from the 38 px capsule (ui_kit.h CTA_RING_OFF), so CTA_RING_H tall, its ends half circles of radius CTA_RING_H / 2
# outside and CTA_RING_W less inside. Its two ends are these images, 16 x 16 samples a pixel as corner_cover's; the
# straight runs between them are whole pixels, which the payload fills.
CTA_RING_H, CTA_RING_W = 48, 2
def cta_ring_end(right, rgb):
    """The ring's left (right False) or right end, CTA_RING_H / 2 wide and CTA_RING_H tall, as a PNG in rgb."""
    r, n = CTA_RING_H // 2, 16
    def cover(x, y):
        return sum(r - CTA_RING_W < math.hypot(x + (i + .5) / n - r, y + (j + .5) / n - r) <= r
                   for i in range(n) for j in range(n)) / n ** 2
    cov = [cover(r - 1 - x if right else x, y) for y in range(CTA_RING_H) for x in range(r)]
    return png(r, CTA_RING_H, laid(r, CTA_RING_H, [(cov, lambda x, y: (rgb, 1))]))


# Search's key pop (search_strip.c, list.entry.ipod): when the wheel lands on a character, the lit key grows a bubble
# holding it at POP_LETTER_PX (the key keeps it at the strip's size, dimmed, drawn by the payload). Panel px.
# The bubble is one closed path: a POP_HEAD head with POP_HEAD_R corners whose sides each run one cubic S-curve
# over POP_NECK px, leaving the head and meeting the key vertically (tangents continuous, so the edge never kinks),
# ending inside the key's top 6 px; then the POP_KEY key with POP_KEY_R corners. Even widths with an even
# difference, so head, key and character share one centre. The image is the head's width and reaches the key's foot.
POP_KEY_W, POP_KEY_H, POP_KEY_R = 18, 24, 5
POP_HEAD_W, POP_HEAD_H, POP_HEAD_R = 30, 30, 9
POP_NECK = 18
POP_H = POP_HEAD_H + POP_NECK + POP_KEY_H - 6
POP_LETTER_PX, POP_LETTER_DY = 24, 2  # the big character's ink centred on the head, 2 px below its middle
# every character of the strip's sets but the space, in their order (search_strip.c ss_sets): abc, ABC, 123, #+=
POP_CHARS = ('abcdefghijklmnopqrstuvwxyz' 'ABCDEFGHIJKLMNOPQRSTUVWXYZ' '0123456789'
             '!@#$%&*-_.?/+=:;,\'"()[]{}<>^~`\\|')
# the strip's return key's marks (search_strip.c SS_RET_MARK, ss_ret_kind), by the field it submits: a magnifier for a
# search, a check for a name or value saved, an arrow right for anything else. The user's mock-up's glyphs (returnkey
# context.html) on a 24-unit grid, round capped and joined, scaled to a RETURN_PX box: the mock-up draws them 22 px on
# its 38 px key, the strip's key is 34 px, so 20. White on the accent's circle, the accent on white.
RETURN_PX, RETURN_GRID = 20, 24
RETURN_MARKS: 'dict[str, tuple[float, list[list[tuple[float, float]]]]]' = {
    'search': (2.4, [[(10.5 + 5.5 * math.cos(a * math.pi / 24), 10.5 + 5.5 * math.sin(a * math.pi / 24)) for a in range(49)],
                     [(14.6, 14.6), (19, 19)]]),
    'go': (2.4, [[(5, 12), (19, 12)], [(13.5, 6.5), (19, 12), (13.5, 17.5)]]),
    'save': (2.6, [[(5.5, 12.5), (9.7, 16.7), (18.5, 7.8)]]),
}


def return_mark(kind, rgb):
    """The return key's mark for kind (RETURN_MARKS) as a RETURN_PX square PNG in rgb."""
    width, lines = RETURN_MARKS[kind]
    k = RETURN_PX / RETURN_GRID
    polys = stroke([[(x * k, y * k) for x, y in line] for line in lines], width * k)
    return png(RETURN_PX, RETURN_PX, laid(RETURN_PX, RETURN_PX, [(coverage_nonzero(polys, (0, 0, RETURN_PX, RETURN_PX)), lambda x, y: (rgb, 1))]))


def pop_bubble_d():
    """The bubble's SVG path, in the image's own pixels."""
    w, s0, n, r, kr = POP_HEAD_W, POP_HEAD_H, POP_NECK, POP_HEAD_R, POP_KEY_R
    k0, s1 = (w - POP_KEY_W) / 2, POP_HEAD_H + POP_NECK
    k1, foot = k0 + POP_KEY_W, POP_H
    return (f'M0,{r} A{r},{r} 0 0 1 {r},0 H{w - r} A{r},{r} 0 0 1 {w},{r} '
            f'V{s0} C{w},{s0 + n * 0.55} {k1},{s1 - n * 0.55} {k1},{s1} '
            f'V{foot - kr} A{kr},{kr} 0 0 1 {k1 - kr},{foot} H{k0 + kr} A{kr},{kr} 0 0 1 {k0},{foot - kr} '
            f'V{s1} C{k0},{s1 - n * 0.55} 0,{s0 + n * 0.55} 0,{s0} Z')


@functools.lru_cache(maxsize=None)
def inter(face='Medium'):
    return TTFont(ROOT/f'assets/fonts/Inter-{face}.ttf')


def ink_polys(ch, px, cx, cy=None, baseline=None, face='Medium'):
    """ch in Inter face (Medium or Regular) at px (the em), its ink's horizontal centre on cx exactly, half pixels
    included (no snapping: the rasteriser covers sub-pixels, as stb_truetype's subpixel bitmaps do); vertically its
    ink centred on cy, or its baseline on baseline. One polygon per contour, filled nonzero."""
    font = inter(face)
    name = font.getBestCmap()[ord(ch)]
    g, k = font['glyf'][name], px / font['head'].unitsPerEm
    x = cx - (g.xMin + g.xMax) / 2 * k
    y = baseline if cy is None else cy + (g.yMin + g.yMax) / 2 * k
    pen = SVGPathPen(font.getGlyphSet(), ntos=lambda v: f'{v:.4f}')
    font.getGlyphSet()[name].draw(TransformPen(pen, (k, 0, 0, -k, x, y)))
    return path(pen.getCommands())


def pop_images(accent):
    """{name: PNG}: the bubble in the accent, a strip of the characters in POP_CHARS' order, white, one head's box each
    (one image, not one a character: a third of the bytes), and the return key's marks in white and in the accent."""
    n, out = len(POP_CHARS), {}
    out['search_pop'] = png(POP_HEAD_W, POP_H, laid(POP_HEAD_W, POP_H, [
        (coverage_nonzero(path(pop_bubble_d()), (0, 0, POP_HEAD_W, POP_H)), lambda x, y: (accent, 1))]))
    polys = [p for i, ch in enumerate(POP_CHARS)
             for p in ink_polys(ch, POP_LETTER_PX, POP_HEAD_W * i + POP_HEAD_W / 2, cy=POP_HEAD_H / 2 + POP_LETTER_DY)]
    out['search_pop_letters'] = png(POP_HEAD_W * n, POP_HEAD_H, laid(POP_HEAD_W * n, POP_HEAD_H, [
        (coverage_nonzero(polys, (0, 0, POP_HEAD_W * n, POP_HEAD_H)), lambda x, y: (0xffffff, 1))]))
    out |= {f'search_return_{kind}_{k}': return_mark(kind, c) for kind in RETURN_MARKS for k, c in {'w': 0xffffff, 'a': accent}.items()}
    return out


def status_images(prefix):
    """The status row's glyphs (STATUS_GLYPHS and the rest of its role), {name: PNG bytes}, per half."""
    out = {f'{prefix}_{n}_{k}': phosphor(g, px, c, ink) for n, (g, px, ink) in STATUS_GLYPHS.items() for k, c in STATE_INKS.items()}
    out |= {f'{prefix}_{n}_{k}': status_glyph(g, w, c) for n, (g, w) in STATUS_FIT.items() for k, c in STATE_INKS.items()}
    out |= {f'{prefix}_status_wifi_{n}_{k}': status_wifi(n, c, STATUS_UNLIT[k])
            for n in (1, 2, 3) for k, c in STATE_INKS.items()}
    # the Bluetooth slot while a device connects: the rune in Text Tertiary per half (status_row.c headset_paint)
    out |= {f'{prefix}_status_btdim_{k}': status_glyph('bluetooth-bold', STATUS_PX, c) for k, c in BT_SPIN_INKS.items()}
    # a card app's server out of reach (status_row.c SLOT_OFFLINE): cloud-slash in Warning, the same in both halves;
    # its ink 13.6 px wide at 11 tall, so a STATUS_PX box
    out |= {f'{prefix}_status_offline_{k}': status_glyph('cloud-slash-bold', STATUS_PX, token('warning')) for k in STATE_INKS}
    out |= {f'{prefix}_status_sync_{f}_{k}': status_turn('arrows-clockwise-bold', STATUS_PX, STATUS_INK, c, f / STATUS_SPIN / 2)
            for f in range(STATUS_SPIN) for k, c in STATE_INKS.items()}
    return out


def theme_images(themes):
    """Every theme's images, {name: PNG bytes}, each name once. A theme that draws another's art (its art attribute)
    makes that theme's names, so its values must draw the same bytes: the payload has one image per name."""
    out = {}
    for t in themes:
        for n, d in images(t).items():
            check(out.setdefault(n, d) == d, f"{t['id']}: {n} differs from {t['art_id']}'s, whose art it draws by name")
    return out


def images(t):
    """Theme t's generated images, {name: PNG bytes}; those its values state, named by the theme whose art it draws
    (art_id: its own id, or the theme its art attribute names), as the payload loads them by name."""
    out, prefix = {}, t['art_id'].replace('-', '_')
    # Songs' Sort bars, unprefixed: not the theme's final art, so Light Mode flips them as stock's glyphs
    out['sort_bars'] = png(*SORT_BOX[2:], compose(SORT_BOX, [(path(SORT), lambda x, y: (0xffffff, 1))]))
    on = t['schemes'][t['accents'].bit_length() - 1]['tones'][0]  # the theme's own accent's top tone
    # every drill row's caret (list_marks.c chevron_icon), the spinner in its place while Cover Flow opens, and the
    # status row's play state
    out |= {f'{prefix}_chevron_{k}': phosphor('caret-right-bold', CARET_PX, c) for k, c in (CHEVRON_INKS | CHEVRON_MUTED_INKS).items()}
    out |= {f'{prefix}_spin_{f}': spinner(f, SPIN_SPOKES, CARET_PX, SPIN_R, SPIN_STROKE, SPIN_FADE) for f in range(SPIN_SPOKES)}
    out |= status_images(prefix)
    out[f'{prefix}_radio_tower'] = radio_tower(RADIO_TOWER_PX, 0xffffff)  # Home's pane for Radio (radio_pane.c)
    # Buttons Lock's card (keylock.c keylock_paint)
    out |= {f'{prefix}_keylock_card_{k}': phosphor('lock-simple-fill', KEYLOCK_CARD_PX, c) for k, c in STATE_INKS.items()}
    if t['np_modes']:
        for n in ('shuffle', 'repeat'):
            out |= {f'{prefix}_mode_{n}_{k}': phosphor(f'{n}-bold', MODE_PX, c) for k, c in {**MODE_INKS, 'on': on}.items()}
        out[f'{prefix}_mode_repeat1_on'] = phosphor('repeat-once-bold', MODE_PX, on)
        # radio's and Up Next's buttons beside them, the antenna white in the radio's pill, and shuffle and repeat
        # dimmed while a radio plays (np_bar.c np_modes)
        out |= {f'{prefix}_mode_{n}_{k}': phosphor(f'{g}-bold', MODE_PX, c) for n, g in (('radio', 'broadcast'), ('queue', 'queue'))
                for k, c in MODE_INKS.items()}
        out[f'{prefix}_mode_radio_w'] = phosphor('broadcast-bold', MODE_PILL_PX, 0xffffff)
        out |= {f'{prefix}_mode_{n}_x{k}': phosphor(f'{n}-bold', MODE_PX, c) for n in ('shuffle', 'repeat') for k, c in MODE_DIM_INKS.items()}
        # and its heart and More (np_bar.c np_glyphs), named after stock's play_fav, play_unfav, play_more and
        # play_moredown: the heart (np_heart), outlined in Text Secondary, or filled while liked in Favorite; More in
        # Text Primary, pressed in Text Secondary; each half's
        for k, c in STATE_INKS.items():
            out |= {f'{prefix}_np_fav_{k}': np_heart('heart-fill', inks('favorite', k)[k]),
                    f'{prefix}_np_unfav_{k}': np_heart('heart-bold', MODE_INKS[k]),
                    f'{prefix}_np_more_{k}': phosphor('dots-three', ROW_ICON_PX, c, ROW_INK),
                    f'{prefix}_np_moredown_{k}': phosphor('dots-three', ROW_ICON_PX, MODE_INKS[k], ROW_INK)}
        # the volume's speakers at the bar's ends (volume.c vol_paint, VOL_SPK_PX): Text Secondary per half
        out |= {f'{prefix}_vol_{n}_{k}': phosphor(f'speaker-simple-{g}-fill', VOL_SPK_PX, c)
                for n, g in (('lo', 'none'), ('hi', 'high')) for k, c in MODE_INKS.items()}
    if t['shuffle_row']:  # Songs' Shuffle row: the accent's top tone at rest, white on the selection, in both halves
        out |= {f'{prefix}_shuffle_{k}': phosphor('shuffle', ROW_ICON_PX, c, ROW_INK) for k, c in {'a': on, 'w': 0xffffff}.items()}
    if t['set_switch']:  # a picker's check on the right column: the accent's top tone at rest, white on the selection
        out |= {f'{prefix}_check_{k}': phosphor('check-bold', CHECK_PX, c) for k, c in {'a': on, 'w': 0xffffff}.items()}
        # the Wi-Fi page's network rows (wifi_rows.c): signal, lock and the connected one's trash in Text Primary per
        # half, white on the selection
        for name, glyph in (('wifi_3', 'wifi-high'), ('wifi_2', 'wifi-medium'), ('wifi_1', 'wifi-low'), ('lock', 'lock-simple'),
                            ('trash', 'trash')):
            out |= {f'{prefix}_{name}_{k}': phosphor(glyph, ROW_ICON_PX, c, ROW_INK) for k, c in STATE_INKS.items()}
        # ... and a failed join's mark before them: warning-circle in Negative per half, white on the selection
        out |= {f'{prefix}_wifi_warn_{k}': phosphor('warning-circle', ROW_ICON_PX, c, ROW_INK)
                for k, c in inks('negative', 'd', 'l', 'w').items()}
    # the menu sheet's symbols (menu_modal.c)
    out |= {f'{prefix}_menu_{n}_{k}': phosphor(n, MENU_ICON_PX, c) for n in MENU_ICONS for k, c in FOLDER_INKS.items()}
    out[f'{prefix}_menu_check_a'] = phosphor('check', MENU_ICON_PX, on)
    # Unfavorite's heart (hold_menu.c, the user's 2026-11-20): filled in Favorite, as Now Playing's liked heart, both
    # halves, and white on the selection as every symbol there
    out |= {f'{prefix}_menu_heart-fill_{k}': phosphor('heart-fill', MENU_ICON_PX, c) for k, c in inks('favorite', 'd', 'l', 'w').items()}
    # Move's grip (hold_menu.c, Up Next's Playing Next rows): q2-coda's own three bars, as the lifted rows show
    out |= {f'{prefix}_menu_grip_{k}': phosphor('q2coda/grip', MENU_ICON_PX, c) for k, c in FOLDER_INKS.items()}
    # an artist's Shuffle and Radio panels and an album's Play (artist_page.c pair_paint): the accent's top tone at rest, white lit
    out |= {f'{prefix}_pair_{n}_{k}': phosphor(n, PAIR_ICON_PX, c) for n in PAIR_ICONS for k, c in {'a': on, 'w': 0xffffff}.items()}
    out |= {f'{prefix}_quality_{k}': phosphor('waveform-bold', QUALITY_PX, c) for k, c in STATE_INKS.items()}
    # Up Next's bump arrow on a queued song (upnext.c): Text Tertiary per half, white on the selection
    out |= {f'{prefix}_bump_{k}': phosphor('arrow-up', BUMP_PX, c) for k, c in BUMP_INKS.items()}
    # the playlist pages' tiles, action glyphs and Add Songs' marks
    out |= {f'{prefix}_pltile_{n}_{k}': phosphor(g, TILE_PX, c) for n, g in TILE_GLYPHS.items() for k, c in TILE_INKS.items()}
    out |= {f'{prefix}_plact_{n}_{k}': phosphor(g, PL_ACTION_PX, c) for n, g in PL_ACTIONS.items()
            for k, c in {'a': on, 'w': 0xffffff}.items()}
    out |= {f'{prefix}_plmark_{n}_{k}': phosphor(g, PL_MARK_PX, c) for n, (g, inks) in PL_MARKS.items()
            for k, c in {**inks, 'w': 0xffffff}.items()}
    if t['entry_ipod']:  # Search's key pop (search_strip.c): the accent's top tone, as the lit key's
        out |= {f'{prefix}_{n}': d for n, d in pop_images(on).items()}
        out[f'{prefix}_holdback'] = phosphor('arrow-u-up-left-bold', HOLD_BACK_PX, 0xffffff)  # Return held's card
    # the row editors' check circles (list_marks.c check_circle): a bold check, white on the accent's disc and the
    # accent's on the selection's white one, and the locked row's padlock in Text Secondary per half
    out |= {f'{prefix}_circle_check_{k}': phosphor('check-bold', CIRCLE_CHECK_PX, c) for k, c in {'a': on, 'w': 0xffffff}.items()}
    out |= {f'{prefix}_circle_lock_{k}': phosphor('lock-simple-fill', CIRCLE_LOCK_PX, c) for k, c in FOLDER_INKS.items() if k != 'w'}
    # Folder view's folder glyph (song_rows.c folder_row): Text Secondary, white on the selection
    out |= {f'{prefix}_folder_{k}': phosphor('folder', ROW_ICON_PX, c, ROW_INK) for k, c in FOLDER_INKS.items()}
    # feedback.c's confirmation after a shuffle, a Ratings row's stars and Streaming's service marks
    out[f'{prefix}_confirm_shuffle'] = phosphor('shuffle-bold', CONFIRM_GLYPH_PX, on)
    out |= {f'{prefix}_rate_{state}_{k}': phosphor(glyph, RATE_PX, c) for state, glyph in (('on', 'star-fill'), ('off', 'star'))
            for k, c in RATE_INKS.items()}
    out |= {f'{prefix}_npstar_{state}{big}_{k}': phosphor(glyph, px, c) for state, glyph in (('on', 'star-fill'), ('off', 'star'))
            for big, px in (('', NP_STAR_PX), ('_big', NP_STAR_BIG_PX)) for k, c in STATE_INKS.items()}
    for k, c in FOLDER_INKS.items():
        out |= {f'{prefix}_stream_{n}_{k}': phosphor(glyph, STREAM_PX, c, ROW_INK) for n, glyph in STREAM_MARKS.items()}
        out[f'{prefix}_stream_tidal_{k}'] = tidal_mark(STREAM_PX, c, ROW_INK)
    out |= {f'{prefix}_mark_{n}_{k}': phosphor(glyph, MARK_PX, c) for n, glyph in MARKS.items() for k, c in MARK_INKS.items()}
    out |= {f'{prefix}_store_pick_{k}': phosphor('arrow-circle-down', MARK_PX, c) for k, c in {'a': on, 'w': 0xffffff}.items()}
    out |= {f'{prefix}_store_auto_{k}': phosphor('arrows-clockwise', MARK_PX, c) for k, c in FOLDER_INKS.items()}
    out |= {f'{prefix}_modepage_{n}': phosphor(g, MODE_GLYPH_PX, 0xffffff) for n, g in MODE_GLYPHS.items()}
    out |= {f'{prefix}_swoffer_{n}': phosphor(g, SW_OFFER_PX, 0xffffff) for n, g in SW_OFFER.items()}
    out |= {f'{prefix}_cc_{n}': phosphor(g, px, c, CC_INK.get(n, 0.0)) for n, (g, px, c) in CC_ICONS.items()}
    out |= {f'{prefix}_cc_{n}_l': phosphor(g, px, CC_LIGHT[n], CC_INK.get(n, 0.0)) for n, (g, px, _) in CC_ICONS.items()}
    out |= {f'{prefix}_usb_{n}_{k}': phosphor(g, USB_ROW_PX, c) for n, g in USB_ROWS.items() for k, c in FOLDER_INKS.items()}
    out |= {f'{prefix}_crphone_{k}': phosphor('device-mobile', CR_PHONE_PX, c) for k, c in FOLDER_INKS.items()}
    out |= {f'{prefix}_lsdev_{n}_{k}': phosphor(g, CR_PHONE_PX, c) for n, g in LS_DEVICES.items() for k, c in FOLDER_INKS.items()}
    out |= {f'{prefix}_lsbig_{n}_{k}': phosphor(g, LS_BIG_PX, c) for n, g in LS_DEVICES.items()
            for k, c in inks('secondary', 'd', 'l').items()}
    out |= {f'{prefix}_lscard_{n}': phosphor(g, PAIR_GLYPH_PX, on) for n, g in LS_DEVICES.items()}
    out |= {f'{prefix}_lssend_{k}': phosphor('paper-plane-tilt-fill', LS_SEND_PX, c) for k, c in {'a': on, 'w': 0xffffff}.items()}
    out |= {f'{prefix}_lstoast_{n}': phosphor(f'{g}-fill', USB_TOAST_PX, 0xffffff) for n, g in LS_DEVICES.items()}
    out |= {f'{prefix}_lsfile_{k}': phosphor('file-text', LS_FILE_PX, c) for k, c in inks('tertiary', 'd', 'l').items()}
    # the buttons' focus ring's ends (ui_kit.c cta_capsule), Text Primary per half
    out |= {f'{prefix}_ctaring_{s}_{k}': cta_ring_end(s == 'r', c) for s in 'lr' for k, c in CTA_RING_INKS.items()}
    out |= {f'{prefix}_usbtoast_{n}': phosphor(g, USB_TOAST_PX, c) for n, (g, c) in USB_TOASTS.items()}
    out |= {f'{prefix}_btdev_{n}_{k}': phosphor(g, ROW_ICON_PX, c, ROW_INK) for n, g in BT_ROW_GLYPHS.items() for k, c in FOLDER_INKS.items()}
    out |= {f'{prefix}_btspin_{k}_{f}': spinner(f, SPIN_SPOKES, CARET_PX, SPIN_R, SPIN_STROKE, SPIN_FADE, c)
            for k, c in BT_SPIN_INKS.items() for f in range(SPIN_SPOKES)}
    out |= {f'{prefix}_bttoast_{n}': phosphor(g, USB_TOAST_PX, c) for n, (g, c) in BT_TOASTS.items()}
    out |= {f'{prefix}_bttoast_{n}_off': phosphor(g, USB_TOAST_PX, 0xababab) for n, (g, _) in BT_TOASTS.items() if n != 'low'}
    # Guest Play (guest_ui.c, status_row.c): the guest's phone in the accent, beside "Playing from" and in the status row,
    # and the status row's relay mark (phone › car) in Text Secondary per half
    out[f'{prefix}_guestphone_a'] = phosphor('device-mobile-fill', USB_TOAST_PX, on)
    out[f'{prefix}_pairing_phone'] = phosphor('device-mobile', PAIR_GLYPH_PX, on)  # pair_prompt.c's card
    out |= {f'{prefix}_adb{n}_{k}': phosphor('laptop', px, c) for n, px in (('card', ADB_CARD_PX), ('pill', ADB_PILL_PX))
            for k, c in inks('primary', 'd', 'l').items()}
    out[f'{prefix}_status_guest'] = phosphor('device-mobile-fill', STATUS_PX, on, STATUS_INK)
    out |= {f'{prefix}_status_relay_{k}': phosphor('caret-right-bold', 8, c) for k, c in STATUS_SECONDARY.items()}
    out |= {f'{prefix}_batt{n}_{k}': inset_glyph(g, *inks) for n, g in BATT_GLYPHS.items() for k, inks in BATT_GLYPH_INKS.items()}
    out |= {f'{prefix}_chargetoast_{n}': phosphor(g, USB_TOAST_PX, c) for n, (g, c) in CHARGE_TOASTS.items()}
    # a card app's server error (addon.c reach_read): the icon toast's cloud-slash, Warning
    out[f'{prefix}_srvtoast'] = phosphor('cloud-slash-fill', USB_TOAST_PX, token('warning'))
    out |= {f'{prefix}_chargetoast_battery_{k}': phosphor('battery-high-fill', USB_TOAST_PX, c) for k, c in CHARGE_TOAST_BATTERY.items()}
    out[f'{prefix}_chargebolt'] = phosphor('lightning-fill', CHARGE_BOLT_PX, token('positive'))
    out |= {f'{prefix}_swspin_{k}_{f}': spinner(f, SW_SPIN_SPOKES, SW_SPIN_PX, SW_SPIN_R, SW_SPIN_STROKE, SW_SPIN_FADE, c)
            for k, c in STATE_INKS.items() for f in range(SW_SPIN_SPOKES)}
    out |= {f'{prefix}_npspin_{k}_{f}': spinner(f, SW_SPIN_SPOKES, NP_SPIN_PX, NP_SPIN_R, NP_SPIN_STROKE, SW_SPIN_FADE, c)
            for k, c in NP_SPIN_INKS.items() for f in range(SW_SPIN_SPOKES)}
    out |= {f'{prefix}_empty_{n}_{k}': phosphor(g, EMPTY_PX, c) for n, g in EMPTY_GLYPHS.items()
            for k, c in inks('secondary', 'd', 'l').items()}
    out |= {f'{prefix}_scan_{n}_{k}': phosphor(g, SCAN_FOLDER_PX if n == 'folder' else SCAN_GLYPH_PX, c)
            for n, g in SCAN_GLYPHS.items() for k, c in SCAN_INKS.items()}
    return out
