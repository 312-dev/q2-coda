"""Rounded corners at any radius are anti-aliased (corner_cover.c, docs/internals.md#rounded-corners): the shared
coverage table, and each painter that reads it (rows and pills, art corners over a colour, a bitmap's alpha, Cover
Flow's covers), checked pixel by pixel against tools/art.py's corner_cover."""
import ctypes as C
import struct
import sys

import pytest

import host
from test_coverflow import ART, CX, TOP, VIEW_H, VIEW_W, Ring, api, exact, libc, style  # noqa: F401 (fixture)
from test_nav_paint import (  # noqa: F401 (fixtures)
    ART_RADIUS, GEOMETRY, WHITE, Canvas, bitmaps, fill, image_add, layout, theme)

sys.path.insert(0, str(host.ROOT/'tools'))
import art  # noqa: E402

# Radii no table held before corner_cover.c (tools/art.py's CORNERS was 2, 3, 5 to 13 and 16), and some it did.
RADII = (4, 14, 20, 33, 9, 16)


class Rect(C.Structure):
    _fields_ = [(k, C.c_int) for k in 'xywh']


def raster(fills, w, h, under=0x000000):
    """The canvas's one-colour fills composited source-over on w x h of under, as 0xRRGGBB rows; fills off it are
    errors. A fill's colour is a color_t (bytes r, g, b, a)."""
    px = [[under] * w for _ in range(h)]
    for fx, fy, fw_, fh, c in fills:
        a, rgb = c >> 24, (c & 255) << 16 | (c & 0xff00) | (c >> 16 & 255)
        for y in range(fy, fy + fh):
            for x in range(fx, fx + fw_):
                assert 0 <= x < w and 0 <= y < h, (x, y)
                old = px[y][x]
                px[y][x] = sum(((rgb >> s & 255) * a + (old >> s & 255) * (255 - a) + 127) // 255 << s
                               for s in (0, 8, 16))
    return px


def corners(w, h, r):
    """Every pixel of a w x h box with radius r corners: ((x, y), coverage of 255) from tools/art.py, 255 off the
    corners."""
    cover = art.corner_cover(r)
    for y in range(h):
        for x in range(w):
            cx, cy = min(x, w - 1 - x), min(y, h - 1 - y)
            yield (x, y), cover[cy * r + cx] if cx < r and cy < r else 255


def test_corner_cover_is_tools_art_s_table_at_every_radius(fw):
    cover = fw.fn('corner_cover', C.c_void_p, C.c_int)
    assert not cover(0) and not cover(-3)
    for r in (*range(1, 41), 52, 75, 160):
        p = cover(r)
        assert p and C.string_at(p, r * r) == art.corner_cover(r), r
        assert cover(r) == p  # filled once, then kept
    assert C.string_at(cover(161), 160 * 160) == art.corner_cover(160)  # past the largest, the largest


@pytest.mark.parametrize('r', RADII)
def test_a_rounded_box_of_any_radius_is_antialiased(fw, r):
    """round_row, every row of a box: white over black, each corner pixel at its coverage (a hard step before, for a
    radius the build's table left out)."""
    theme(fw)
    canvas = Canvas(fw)
    w, h = 2 * r + 7, 2 * r + 3
    box = Rect(0, 0, w, h)
    for i in range(h):
        fw.fn('round_row', None, C.c_void_p, C.c_void_p, C.c_int, C.c_int, C.c_uint)(canvas.p, C.addressof(box), r, i,
                                                                                    0xffffffff)
    got = raster(canvas.fills, w, h)
    for (x, y), want in corners(w, h, r):
        assert abs((got[y][x] & 255) - want) <= 1, (r, x, y)


@pytest.mark.parametrize('r', RADII)
def test_art_corners_fade_into_what_is_behind(fw, r):
    """ar_corners over a square of white art (Control Center's, Up Next's, an artist's): what the arc leaves of each
    corner pixel is the colour behind, so the art keeps its coverage (whole pixels only, before)."""
    theme(fw)
    canvas = Canvas(fw)
    w = h = 2 * r + 6
    box = Rect(0, 0, w, h)
    fw.fn('ar_corners', None, C.c_void_p, C.c_void_p, C.c_int, C.c_uint)(canvas.p, C.addressof(box), r, 0)
    got = raster(canvas.fills, w, h, under=0xffffff)
    for (x, y), want in corners(w, h, r):
        assert abs((got[y][x] & 255) - want) <= 1, (r, x, y)


@pytest.mark.parametrize('r', (8, 16))
def test_a_selection_pill_is_antialiased_round_its_ends(fw, r):
    """The selection's gradient (navigation.c gradient) as one colour, its ends a pill of radius r: every end pixel at
    its coverage, along the flat of the arc too (one part pixel a row before)."""
    theme(fw)
    canvas = Canvas(fw)
    w, h = 4 * r + 10, 2 * r
    fw.fn('navigation__gradient', None, C.c_void_p, Rect, C.c_uint, C.c_uint, C.c_uint, C.c_int)(
        canvas.p, Rect(0, 0, w, h), 0xffffff, 0xffffff, 0xffffff, r)
    got = raster(canvas.fills, w, h)
    for (x, y), want in corners(w, h, r):
        assert abs((got[y][x] & 255) - want) <= 1, (r, x, y)


def test_a_list_thumbnail_s_alpha_is_its_corner_s_coverage(fw, bitmaps):
    """round_corners on a decoded thumbnail: each corner pixel's alpha its coverage, the shallow part too."""
    theme(fw)
    bm = bitmaps(fw, fill(166, 166, WHITE))
    image_add(fw, b'file:///mnt/mmc/.sldp/1f3a.jpg', bm)
    r = (166 * ART_RADIUS + GEOMETRY['art'] // 2) // GEOMETRY['art']  # round_corners'
    alpha = [[p[3] for p in row] for row in bm.rows()]
    for (x, y), want in corners(166, 166, r):
        assert abs(alpha[y][x] - want) <= 1, (x, y)


def test_cover_flow_s_centre_cover_has_antialiased_corners(fw, style):
    """The cover at rest, texel for texel: white on black, each corner pixel at its coverage, the steep part of the arc
    too (whole columns before, so the sides stepped)."""
    g = api(fw)
    r = ART * C.c_short.from_address(C.addressof(fw.var('cfs', C.c_char)) + style['radius']).value // 166  # cf_corners'
    assert r > 1
    tex = exact(C.c_uint, ART * ART)
    tex[:] = [0xffffffff] * (ART * ART)
    d = exact(C.c_uint, VIEW_W * VIEW_H)
    ring = Ring()
    ring[3] = C.addressof(tex)
    g.coverflow_render(d, VIEW_W, 0, ring.at)
    for (x, y), want in corners(ART, ART, r):
        got = d[(TOP + y) * VIEW_W + CX - 80 + x] & 255
        assert abs(got - want) <= 2, (r, x, y)
    for t in (tex, d):
        libc.free(C.addressof(t))


def test_a_small_box_the_canvas_will_not_round_is_antialiased(fw):
    """fill_box at radius 2 (the sheet's grabber, a playing row's dot), which AWTK's rounded rect declines under 3: its
    rows with antialiased corners (a square box, before)."""
    theme(fw)
    canvas = Canvas(fw)
    fw.mock('canvas_fill_rounded_rect', lambda *_: 1)  # declined, as stock does under radius 3
    box = Rect(0, 0, 36, 5)
    fw.fn('fill_box', None, C.c_void_p, C.c_void_p, C.c_uint, C.c_uint)(canvas.p, C.addressof(box), 0xffffffff, 2)
    got = raster(canvas.fills, 36, 5)
    for (x, y), want in corners(36, 5, 2):
        assert abs((got[y][x] & 255) - want) <= 1, (x, y)
