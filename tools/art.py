"""Original raster art the theme files state in numbers, drawn at build time: small vector shapes, antialiased
over sub-rows, written as 8-bit RGBA PNGs. No Apple assets or reference pixels; every shape is drawn here."""
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
from theme import CHOICES

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
    """An SVG path's d (M L H V C Q A Z, absolute or relative, an M's further pairs lines) as closed polylines."""
    tokens = re.findall(r'[MLHVCQAZmlhvcqaz]|-?[0-9]*\.?[0-9]+', d)
    polys, pts, i, cmd = [], [], 0, None
    cur = (0.0, 0.0)

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
        elif op in 'CQ':
            ctrl = [cur, *(at(rel) for _ in range(3 if op == 'C' else 2))]
            n = len(ctrl) - 1
            for k in range(1, 25):
                t = k / 24
                weights = [math.comb(n, m) * (1 - t)**(n - m) * t**m for m in range(n + 1)]
                pts.append(tuple(sum(w * p[a] for w, p in zip(weights, ctrl)) for a in (0, 1)))
            cur = pts[-1]
        elif op == 'A':
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


def coverage(polys, box):
    """Each pixel's covered fraction of box (x, y, w, h), the polygons' union, each polygon even-odd."""
    x0, y0, w, h = box
    cov = [0.0] * (w * h)
    for sub in range(h * SUB):
        y = y0 + (sub + 0.5) / SUB
        spans = []
        for poly in polys:
            xs = sorted(ax + (y - ay) * (bx - ax) / (by - ay)
                        for (ax, ay), (bx, by) in zip(poly, poly[1:] + poly[:1]) if (ay <= y) != (by <= y))
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
    contour, wound the other way, is a hole."""
    x0, y0, w, h = box
    cov = [0.0] * (w * h)
    for sub in range(h * SUB):
        y = y0 + (sub + 0.5) / SUB
        cross = sorted((ax + (y - ay) * (bx - ax) / (by - ay), 1 if by > ay else -1)
                       for poly in polys for (ax, ay), (bx, by) in zip(poly, poly[1:] + poly[:1]) if (ay <= y) != (by <= y))
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


def status_wifi(level, px, ink, lit, unlit):
    """The status row's Wi-Fi at level (1 to 3 arcs, wifi-low to wifi-high), a px square PNG: the full glyph in unlit,
    the level's arcs over it in lit, both at wifi-high's optical size and centre, so the shape keeps its width."""
    full = phosphor_polys('wifi-high-bold', px)
    part = phosphor_polys(f"wifi-{('low', 'medium', 'high')[level - 1]}-bold", px)
    box = (0, 0, px, px)
    return png(px, px, laid(px, px, [(coverage_nonzero(optical(full, px, ink), box), lambda x, y: (unlit, 1)),
                                     (coverage_nonzero(optical(part, px, ink, full), box), lambda x, y: (lit, 1))]))


def status_turn(name, px, ink, rgb, turn):
    """name at its optical size, turned clockwise by turn (a fraction of a whole turn) about the box's centre, a px
    square PNG in rgb: a frame of the status row's sync arrows."""
    a, c = 2 * math.pi * turn, px / 2
    polys = [[(c + (x - c) * math.cos(a) - (y - c) * math.sin(a), c + (x - c) * math.sin(a) + (y - c) * math.cos(a)) for x, y in poly]
             for poly in optical(phosphor_polys(name, px), px, ink)]
    return png(px, px, laid(px, px, [(coverage_nonzero(polys, (0, 0, px, px)), lambda x, y: (rgb, 1))]))


# A theme's own glyphs (docs/oled-design.md Icons), in final colours per half: _d on black, _l on paper, _w on the
# selection. The list's caret-right (list_marks.c chevron_icon) and the status row's play state.
CARET_PX = 15
STATE_PX = 13
CHEVRON_INKS = {'d': 0x999999, 'l': 0x8a8a8a, 'w': 0xffffff}
CHEVRON_MUTED_INKS = {'td': 0x858585, 'tl': 0x6e6e6e}  # a disabled row's (list_intodown, list_marks.c chevron_muted): Text Tertiary per half
# The activity spinner in the selected row's chevron place while Cover Flow opens (coverflow.c coverflow_spinner,
# list_marks.c spinner_icon): iOS's eight spokes, white on the selection, frame f's head at f eighths of a turn
# clockwise from 12 o'clock, each spoke behind it SPIN_FADE dimmer.
SPIN_SPOKES = 8
SPIN_R = (3.4, 6.4)  # a spoke's ends' distances from the centre, round capped
SPIN_STROKE = 1.9
SPIN_FADE = 0.1
STATE_INKS = {'d': 0xffffff, 'l': 0x000000}
# The status row's glyphs by role (status_row.c, docs/oled-design.md#status-row): name -> (Phosphor stem, box px,
# optical ink, 0 for none), each in Text Primary per half (STATE_INKS). The play state and the padlock are 13 px boxes
# drawn as they were (their ink 10.6 and 11 px); the rest are STATUS_PX boxes at STATUS_INK, the padlock's ink, so
# they read as one size. Wi-Fi's levels (status_wifi) light 1 to 3 of wifi-high's arcs over the rest in STATUS_UNLIT;
# the sync arrows turn through STATUS_SPIN frames of a half turn (the glyph repeats every half turn), in Text
# Secondary; the download's arrow sits in its ring, white.
STATUS_PX = 14
STATUS_INK = 11.0
STATUS_GLYPHS = {'play': ('play-fill', STATE_PX, 0), 'pause': ('pause-fill', STATE_PX, 0), 'keylock': ('lock-simple-fill', STATE_PX, 0),
                 'status_wifi_x': ('wifi-x-bold', STATUS_PX, STATUS_INK), 'status_bt': ('bluetooth-bold', STATUS_PX, STATUS_INK),
                 'status_down': ('arrow-down-bold', 8, 0)}
STATUS_UNLIT = {'d': 0x3a3a3c, 'l': 0xc7c7cc}
STATUS_SECONDARY = {'d': 0xababab, 'l': 0x5c5c5c}
STATUS_SPIN = 8
KEYLOCK_CARD_PX = 56  # Buttons Lock's card's padlock (keylock.c CARD_GLYPH)
# Now Playing's shuffle and repeat (playing.modes.icons, np_bar.c np_modes): bold, MODE_PX, off in Text Secondary per
# half, on in the theme's own accent's top tone in both; repeat-once is repeat's on glyph for Repeat One.
MODE_PX = 22
MODE_INKS = {'d': 0xababab, 'l': 0x5c5c5c}
MODE_DIM_INKS = {'d': 0x444446, 'l': 0xc5c5c5}  # shuffle and repeat while a radio plays: the app picks the order
MODE_PILL_PX = 18  # the antenna in the radio's pill
# A row's leading glyph (Songs' Shuffle row, list.shuffle.row) and Now Playing's heart and More, regular weight (the
# full heart fill) at the 20 px row type's size
ROW_ICON_PX = 20
ROW_INK = 15.5  # a 20 px symbol's optical size: its ink's longer side (phosphor's ink)
VOL_SPK_PX = 16  # Now Playing's volume speakers (patch/offsets.inc VOL_SPK_PX, the same)
FOLDER_INKS = {'d': 0xababab, 'l': 0x5c5c5c, 'w': 0xffffff}  # Text Secondary in each half, white on the bar
SORT = 'M0 0H16V2H0ZM0 5H11V7H0ZM0 10H6V12H0Z'
SORT_BOX = (0, 0, 16, 12)  # Songs' Sort (navigation.c action_row), white as stock's glyphs: Light Mode flips it to the ink
CIRCLE_CHECK_PX = 11  # the row editors' check circle's glyphs (list_marks.c CIRCLE_CHECK_PX, CIRCLE_LOCK_PX)
CIRCLE_LOCK_PX = 10
CHECK_PX = 12  # a picker's current choice (settings.values.switch, navigation.c paint_radio): the accent, white on the bar
# The confirmation's glyph after a shuffle (feedback.c feedback_shuffled): bold, the accent's top tone in both halves
CONFIRM_GLYPH_PX = 56
# A Ratings row's hearts (ratings.c rate_hearts_paint): heart-fill for the song's, heart for the rest, regular, in the
# folder's inks (Text Secondary in each half, white on the bar)
RATE_PX = 14
# Streaming's service marks (home.c stream_marks): monochrome at 20 px, no disc, in the folder's inks; Phosphor's
# regular glyphs, TIDAL's own mark (Phosphor has none) drawn here as its four diamonds, three across and one under
# the middle, each corner to corner with the next, and the q2-subsonic card app's (home.c stream_apps) our own
# submarine with its sonar, its strokes outlined
STREAM_PX = 20
STREAM_MARKS = {'spotify': 'spotify-logo', 'app': 'app-window', 'radio': 'radio', 'airplay': 'airplay', 'config': 'sliders-horizontal',
                'subsonic': 'q2coda/subsonic'}
TIDAL = ''.join(f'M{x - 3.3} {y}L{x} {y - 3.3}L{x + 3.3} {y}L{x} {y + 3.3}Z' for x, y in ((3.4, 6.7), (10, 6.7), (16.6, 6.7), (10, 13.3)))
# The inset modal's symbols (menu_modal.c, a set with modal): regular weight at MENU_ICON_PX in FOLDER_INKS, and the
# picker's check in the accent off the bar
MENU_ICON_PX = 19
MENU_ICONS = ('funnel-simple', 'arrow-bend-down-right', 'queue', 'heart', 'heart-break', 'list-plus', 'shuffle',
              'vinyl-record', 'microphone-stage', 'folder', 'heart-half', 'clock-counter-clockwise', 'list-numbers',
              'bookmark-simple', 'dots-three', 'plus', 'list-checks', 'skip-forward', 'skip-back', 'repeat', 'stack',
              'airplay', 'download-simple', 'cloud-arrow-down', 'trash', 'speedometer', 'pencil-simple',
              'caret-up', 'caret-down', 'check', 'sliders-horizontal', 'info', 'sneaker-move', 'play')
PAIR_ICON_PX = 16  # an artist's or album's page's action pair (artist_page.c PAIR_ICON)
BUMP_PX = 16  # Up Next's bump arrow (upnext.c)
BUMP_INKS = {'d': 0x858585, 'l': 0x6e6e6e, 'w': 0xffffff}
PAIR_ICONS = ('shuffle', 'radio', 'play')
# Where a song lives (catalogue.c catalogue_row_mark, a song row's right column): the cloud for a server song that
# streams, the arrow for one a card app downloaded, regular, in Text Tertiary (each half's) and white on the bar; and
# Storage > Downloads' items (storage.c storage_paint): the arrow for one the user picked in the accent's top tone,
# the circling arrows for one the app keeps on its own in Text Secondary (each half's), both white on the bar
MARK_PX = 18
MARKS = {'cloud': 'cloud', 'down': 'arrow-circle-down'}
MARK_INKS = {'d': 0x858585, 'l': 0x6e6e6e, 'w': 0xffffff}
QUALITY_PX = 12  # Now Playing's About, its quality badge's glyph (np_about.c AB_GLYPH): Text Primary per half
# Playlists (playlists.c pl_tile_paint, playlist_pages.c): a playlist's and On-The-Go's 36 px tile glyph in Text
# Tertiary per half, white on the selection; the pages' action rows' glyphs in the accent, white on the selection; a
# song's mark against the playlist in Add Songs (already in, added now, taken out now: Text Secondary, green, red)
TILE_PX = 20
TILE_GLYPHS = {'playlist': 'playlist-fill', 'otg': 'sneaker-move-fill'}
TILE_INKS = {'d': 0x858585, 'l': 0x6e6e6e, 'w': 0xffffff}
PL_ACTION_PX = 18
PL_ACTIONS = {'shuffle': 'shuffle', 'add': 'plus-circle', 'save': 'arrow-down', 'clear': 'x', 'search': 'magnifying-glass'}
PL_MARK_PX = 20
# Control Center's glyphs (control_center.c cc_glyph, from mock_cc3.py): fill, white on its toggles, tiles and transport
# (Play/Pause the larger), the sliders' sun and speaker in the block's #1C1C1E on their light fill.
CC_ICONS = {'wifi': ('wifi-high-fill', 22, 0xffffff), 'bluetooth': ('bluetooth-fill', 22, 0xffffff),
            'airplay': ('airplay-fill', 22, 0xffffff), 'car': ('car-profile-fill', 22, 0xffffff),
            'sun': ('sun-fill', 18, 0x1c1c1e), 'speaker': ('speaker-high-fill', 18, 0x1c1c1e),
            'lock': ('lock-simple-fill', 22, 0xffffff), 'timer': ('timer-fill', 22, 0xffffff),
            'moon': ('moon-stars-fill', 22, 0xffffff), 'eq': ('equalizer-fill', 22, 0xffffff),
            'leaf': ('leaf-fill', 22, 0xffffff), 'back': ('skip-back-fill', 22, 0xffffff),
            'forward': ('skip-forward-fill', 22, 0xffffff), 'pause': ('pause-fill', 30, 0xffffff),
            'play': ('play-fill', 30, 0xffffff)}

# A card app's sign-in steps on its QR page (addon.c qr_paint, the state file's step): fill, white on the step's solid
# circle (the done check bold), at the mode page's glyph size.
SIGNIN_PX = 44
SIGNIN_STEPS = {'phone': 'device-mobile-fill', 'check': 'arrows-clockwise-fill', 'done': 'check-bold',
                'fail': 'cloud-slash-fill', 'error': 'x-bold'}
# The USB pages (usb_ui.c): the chooser's row symbols (regular, Text Secondary per half, white on the selection),
# a mode page's glyph (fill, white on its solid circle; the eject's check bold) and the USB toasts' glyphs (fill, 15 px
# beside the toast's words, each in its colour: plug in Text Secondary, the check in Positive green, the speaker white).
USB_ROW_PX = 19
USB_ROWS = {'charge': 'lightning', 'files': 'folder-simple', 'audio': 'speaker-hifi', 'debug': 'terminal-window'}
USB_MODE_PX = 44
USB_MODES = {'files': 'folder-simple-fill', 'audio': 'speaker-hifi-fill', 'debug': 'terminal-window-fill', 'ejected': 'check-bold'}
USB_TOAST_PX = 15
# The software update's pages (sw_update.c, docs/oled-design.md Software Update): each result's glyph (fill, white on
# its solid circle; the up-to-date check and the restart's arrow bold) at the mode page's size, and the check's
# spinner, iOS's twelve spokes in the half's Text Primary (_d white, _l black) on no circle, 48 px across.
SW_MODES = {'latest': 'check-bold', 'none': 'file-arrow-down-fill', 'low': 'battery-warning-fill', 'bad': 'warning-fill',
            'restart': 'arrow-clockwise-bold'}
SW_SPIN_SPOKES = 12
SW_SPIN_PX = 48
SW_SPIN_R = (13.2, 22)  # round capped: the outer cap reaches 24, the mock-up's radius
SW_SPIN_STROKE = 4
SW_SPIN_FADE = 0.75 / 11  # the spoke behind the head's head a quarter as bright
HOLD_BACK_PX = 20  # search_strip.c's Return-held card: Phosphor arrow-u-up-left, bold, white
# The empty state (feedback.c empty_state): its glyph on the middle of its stacked tiles, fill, white at 92 %, by the
# glyph a page names (empty_t.glyph); Cover Flow's (cf_empty.c) so far
EMPTY_PX = 28
EMPTY_GLYPHS = {'cards': 'cards-fill'}
USB_TOASTS = {'unplugged': ('plug-fill', 0xababab), 'ejected': ('check-circle-fill', 0x34c759), 'audio': ('speaker-hifi-fill', 0xffffff)}
# The status row's battery (list_marks.c battery_slim): charging, Phosphor's lightning, too weak a charger its warning,
# fill weight at BATT_GLYPH_PX in Text Primary, with a BATT_GLYPH_GAP px gap in the Canvas cut round it (iOS's way of
# keeping it off the charge's fill; over the body's empty black it is not seen), per half. The image is BATT_GLYPH_BOX,
# the glyph centred BATT_GLYPH_AT in from its corner, so it lands on the body's centre, a half pixel included.
BATT_GLYPH_PX = 7
BATT_GLYPH_GAP = 1
BATT_GLYPH_BOX = (9, 10)
BATT_GLYPH_AT = (4.5, 5.0)
BATT_GLYPHS = {'bolt': 'lightning-fill', 'warn': 'warning-fill'}
BATT_GLYPH_INKS = {'d': (0xffffff, 0x000000), 'l': (0x000000, 0xffffff)}  # (glyph, gap) per half
# Charging's toasts (charging.c): their 15 px glyph beside the words, fill, each in its colour; the battery's own in
# Text Primary per half.
CHARGE_TOASTS = {'charging': ('lightning-fill', 0x34c759), 'weak': ('warning-fill', 0xff9f0a),
                 'full': ('battery-full-fill', 0x34c759), 'low': ('battery-warning-fill', 0xff453a)}
CHARGE_TOAST_BATTERY = {'d': 0xffffff, 'l': 0x000000}
# The plug-in screen's bolt beside its percentage (charging.c S1_BOLT), fill, Positive green
CHARGE_BOLT_PX = 26
PL_MARKS = {'in': ('check-circle-fill', {'d': 0xababab, 'l': 0x5c5c5c}), 'add': ('plus-circle-fill', {'d': 0x4fb477, 'l': 0x4fb477}),
            'out': ('minus-circle-fill', {'d': 0xff6b5e, 'l': 0xff6b5e})}


def inset_glyph(name, ink, gap):
    """Phosphor's icon name at BATT_GLYPH_PX in ink, centred BATT_GLYPH_AT in a BATT_GLYPH_BOX PNG, over a gap in
    gap round it, BATT_GLYPH_GAP px: the glyph's shape grown that far, measured on an 8x finer grid."""
    k, (w, h), (cx, cy) = 8, BATT_GLYPH_BOX, BATT_GLYPH_AT
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
                out = a + d * (1 - a)
                if not out:  # a soft layer's clear pixel, on nothing
                    continue
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


# A coverage table per radius of the round boxes the painter draws a row at a time (navigation.c paint_round_rows): their
# corners' pixels take the row's colour at this alpha, so they stay the scheme's.
CORNERS = (2, 3, 5, 6, 7, 8, 9, 10, 11, 12, 13, 16)
def corner_cover(r):
    """A radius r corner's coverage out of 255, r rows of r pixels, the outermost first."""
    n = 16
    inside = lambda x, y: sum(math.hypot(r - x - (i + .5) / n, r - y - (j + .5) / n) <= r for i in range(n) for j in range(n))
    return bytes(round(inside(x, y) * 255 / n ** 2) for y in range(r) for x in range(r))
def corner_table():
    """The tables as a C initialiser, by radius."""
    rows = (f'[{r}] = (const unsigned char[]){{ {",".join(map(str, corner_cover(r)))} }}' for r in CORNERS)
    return '{ ' + ', '.join(rows) + ' }'


# Search's key pop (search_strip.c, list.entry.ipod): when the wheel lands on a letter, the lit key grows a bubble
# holding the letter at POP_LETTER_PX, and the key keeps the letter at the strip's size, dimmed (the echo). Panel px.
# The bubble is one closed path: a POP_HEAD head with POP_HEAD_R corners whose sides each run one cubic S-curve
# over POP_NECK px, leaving the head and meeting the key vertically (tangents continuous, so the edge never kinks),
# ending inside the key's top 6 px; then the POP_KEY key with POP_KEY_R corners. Even widths with an even
# difference, so head, key and letter share one centre. The image is the head's width and reaches the key's foot.
POP_KEY_W, POP_KEY_H, POP_KEY_R = 18, 24, 5
POP_HEAD_W, POP_HEAD_H, POP_HEAD_R = 30, 30, 9
POP_NECK = 18
POP_H = POP_HEAD_H + POP_NECK + POP_KEY_H - 6
POP_LETTER_PX, POP_LETTER_DY = 26, 2  # the big letter's ink centred on the head, 2 px below its middle
POP_ECHO_PX, POP_ECHO_A = 19, 153  # the echo: Inter Regular at the strip's size, white at 60 %
POP_ECHO_H = 34  # the echo's box is the key's width by the strip's height, its baseline the strip letters' own
POP_ECHO_BASELINE = 23  # ... where the payload's text rule puts the strip's 19 px letters (measured)
POP_CHARS = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789'


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
    """{name: PNG}: the bubble in the accent, and two strips of the characters in POP_CHARS' order, white: the big
    letters, one head's box each, and the echoes, one key's box each (one image a strip, not one a character: a
    third of the bytes)."""
    n, out = len(POP_CHARS), {}
    out['search_pop'] = png(POP_HEAD_W, POP_H, laid(POP_HEAD_W, POP_H, [
        (coverage_nonzero(path(pop_bubble_d()), (0, 0, POP_HEAD_W, POP_H)), lambda x, y: (accent, 1))]))
    for name, cell, h, a, place in (
            ('search_pop_letters', POP_HEAD_W, POP_HEAD_H, 1,
             lambda ch, cx: ink_polys(ch, POP_LETTER_PX, cx, cy=POP_HEAD_H / 2 + POP_LETTER_DY)),
            ('search_pop_echoes', POP_KEY_W, POP_ECHO_H, POP_ECHO_A / 255,
             lambda ch, cx: ink_polys(ch, POP_ECHO_PX, cx, baseline=POP_ECHO_BASELINE, face='Regular'))):
        polys = [p for i, ch in enumerate(POP_CHARS) for p in place(ch, cell * i + cell / 2)]
        out[name] = png(cell * n, h, laid(cell * n, h, [(coverage_nonzero(polys, (0, 0, cell * n, h)), lambda x, y, a=a: (0xffffff, a))]))
    return out


def status_images(prefix):
    """The status row's glyphs (STATUS_GLYPHS and the rest of its role), {name: PNG bytes}, per half."""
    out = {f'{prefix}_{n}_{k}': phosphor(g, px, c, ink) for n, (g, px, ink) in STATUS_GLYPHS.items() for k, c in STATE_INKS.items()}
    out |= {f'{prefix}_status_wifi_{n}_{k}': status_wifi(n, STATUS_PX, STATUS_INK, c, STATUS_UNLIT[k])
            for n in (1, 2, 3) for k, c in STATE_INKS.items()}
    out |= {f'{prefix}_status_sync_{f}_{k}': status_turn('arrows-clockwise-bold', STATUS_PX, STATUS_INK, c, f / STATUS_SPIN / 2)
            for f in range(STATUS_SPIN) for k, c in STATUS_SECONDARY.items()}
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
        # play_moredown: the empty heart and More in Text Primary, More pressed in Text Secondary, the full heart in
        # the accent, each half's but the accent's
        out[f'{prefix}_np_fav'] = phosphor('heart-fill', ROW_ICON_PX, on, ROW_INK)
        for k, c in STATE_INKS.items():
            out |= {f'{prefix}_np_unfav_{k}': phosphor('heart', ROW_ICON_PX, c, ROW_INK), f'{prefix}_np_more_{k}': phosphor('dots-three', ROW_ICON_PX, c, ROW_INK),
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
    if any(g.modal for g in t['geoms'].values()):  # the inset modal's symbols (menu_modal.c)
        out |= {f'{prefix}_menu_{n}_{k}': phosphor(n, MENU_ICON_PX, c) for n in MENU_ICONS for k, c in FOLDER_INKS.items()}
        out[f'{prefix}_menu_check_a'] = phosphor('check', MENU_ICON_PX, on)
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
    # feedback.c's confirmation after a shuffle, a Ratings row's hearts and Streaming's service marks
    out[f'{prefix}_confirm_shuffle'] = phosphor('shuffle-bold', CONFIRM_GLYPH_PX, on)
    out |= {f'{prefix}_rate_{state}_{k}': phosphor(glyph, RATE_PX, c) for state, glyph in (('on', 'heart-fill'), ('off', 'heart'))
            for k, c in FOLDER_INKS.items()}
    for k, c in FOLDER_INKS.items():
        out |= {f'{prefix}_stream_{n}_{k}': phosphor(glyph, STREAM_PX, c, ROW_INK) for n, glyph in STREAM_MARKS.items()}
        out[f'{prefix}_stream_tidal_{k}'] = tidal_mark(STREAM_PX, c, ROW_INK)
    out |= {f'{prefix}_mark_{n}_{k}': phosphor(glyph, MARK_PX, c) for n, glyph in MARKS.items() for k, c in MARK_INKS.items()}
    out |= {f'{prefix}_store_pick_{k}': phosphor('arrow-circle-down', MARK_PX, c) for k, c in {'a': on, 'w': 0xffffff}.items()}
    out |= {f'{prefix}_store_auto_{k}': phosphor('arrows-clockwise', MARK_PX, c) for k, c in FOLDER_INKS.items()}
    out |= {f'{prefix}_signin_{n}': phosphor(g, SIGNIN_PX, 0xffffff) for n, g in SIGNIN_STEPS.items()}
    out |= {f'{prefix}_cc_{n}': phosphor(g, px, c) for n, (g, px, c) in CC_ICONS.items()}
    out |= {f'{prefix}_usb_{n}_{k}': phosphor(g, USB_ROW_PX, c) for n, g in USB_ROWS.items() for k, c in FOLDER_INKS.items()}
    out |= {f'{prefix}_usbmode_{n}': phosphor(g, USB_MODE_PX, 0xffffff) for n, g in USB_MODES.items()}
    out |= {f'{prefix}_usbtoast_{n}': phosphor(g, USB_TOAST_PX, c) for n, (g, c) in USB_TOASTS.items()}
    out |= {f'{prefix}_batt{n}_{k}': inset_glyph(g, *inks) for n, g in BATT_GLYPHS.items() for k, inks in BATT_GLYPH_INKS.items()}
    out |= {f'{prefix}_chargetoast_{n}': phosphor(g, USB_TOAST_PX, c) for n, (g, c) in CHARGE_TOASTS.items()}
    out |= {f'{prefix}_chargetoast_battery_{k}': phosphor('battery-high-fill', USB_TOAST_PX, c) for k, c in CHARGE_TOAST_BATTERY.items()}
    out[f'{prefix}_chargebolt'] = phosphor('lightning-fill', CHARGE_BOLT_PX, 0x34c759)
    out |= {f'{prefix}_swmode_{n}': phosphor(g, USB_MODE_PX, 0xffffff) for n, g in SW_MODES.items()}
    out |= {f'{prefix}_swspin_{k}_{f}': spinner(f, SW_SPIN_SPOKES, SW_SPIN_PX, SW_SPIN_R, SW_SPIN_STROKE, SW_SPIN_FADE, c)
            for k, c in STATE_INKS.items() for f in range(SW_SPIN_SPOKES)}
    out |= {f'{prefix}_empty_{n}': phosphor(g, EMPTY_PX, 0xebebeb) for n, g in EMPTY_GLYPHS.items()}
    return out
