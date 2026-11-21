"""art_zoom.c: Now Playing opened from a page that shows the playing cover (Home's art pane, an album page's header)
carries that cover to its art as one card, its box, crop and corners morphing on a spring, and Return carries it back
(docs/oled-design.md#motion, docs/internals.md#now-playing-motion). Opened from anywhere else it opens at once."""
import ctypes as C
import struct

import pytest

from conftest import O, in_fork
from test_nav_lists import ui  # noqa: F401  (ui: fixture)
from test_nav_painters import scratch, st  # noqa: F401  (fixtures)
from test_ui_kit import reduce_motion

W, H = 375, 320
PANE, NP = (196, 0, 179, 320), (24, 58, 150, 150)  # Home's art pane and Now Playing's art, as on the Q2


class Rect(C.Structure):
    _fields_ = [('x', C.c_int), ('y', C.c_int), ('w', C.c_int), ('h', C.c_int)]

    def t(self):
        return (self.x, self.y, self.w, self.h)


class Frame(C.Structure):
    _fields_ = [('card', Rect)] + [(n, C.c_int) for n in
                                   'radius zoom pane vig np_a np_scale page_a page_dx shadow src_a np_art_a'.split()]


def frame(fw, t, open_, hero=1, src=PANE, src_r=0, src_z=1100):
    f, a, b = Frame(), Rect(*src), Rect(*NP)
    fw.fn('art_zoom_frame', None, C.c_int, C.c_int, C.c_int, C.c_void_p, C.c_int, C.c_int, C.c_void_p, C.c_void_p)(
        t, open_, hero, C.addressof(a), src_r, src_z, C.addressof(b), C.addressof(f))
    return f


def test_returning_home_the_card_runs_from_now_playings_art_to_the_whole_pane(fw):
    f = frame(fw, 0, 0)
    assert f.card.t() == NP and (f.radius, f.zoom, f.pane, f.vig) == (6, 1000, 0, 0)
    assert (f.np_a, f.np_scale, f.page_a, f.page_dx, f.shadow) == (1000, 1000, 0, -28, 0)
    assert (f.src_a, f.np_art_a) == (0, 1000)  # it starts on Now Playing's own art
    f = frame(fw, 1000, 0)
    assert f.card.t() == PANE and (f.radius, f.zoom, f.pane, f.vig) == (0, 1100, 1000, 1000)
    assert (f.np_a, f.np_scale, f.page_a, f.page_dx, f.shadow) == (0, 960, 1000, 0, 0)
    assert (f.src_a, f.np_art_a) == (1000, 0)  # and ends on the pane as Home shows it


def test_mid_flight_the_card_is_between_on_an_arc_with_its_shadow_and_now_playing_draws_back(fw):
    f = frame(fw, 500, 0)
    k = 885  # the spring at half the run (az_hero_spring[16])
    assert [f.card.x, f.card.w, f.card.h] == [a + (b - a) * k // 1000 for a, b in ((24, 196), (150, 179), (150, 320))]
    assert f.card.y < NP[1] + (PANE[1] - NP[1]) * k // 1000  # lifted off the straight line
    assert 0 < f.radius < 6 and 1000 < f.zoom < 1100 and 0 < f.shadow <= 200
    assert 960 < f.np_scale < 1000 and 0 < f.np_a < 1000 and 0 < f.page_a < 1000 and -28 < f.page_dx < 0
    assert (f.src_a, f.np_art_a) == (0, 0)


def test_opening_now_playing_is_the_same_card_the_other_way(fw):
    f = frame(fw, 0, 1)
    assert f.card.t() == PANE and (f.radius, f.zoom, f.vig, f.src_a) == (0, 1100, 1000, 1000)
    assert (f.np_a, f.page_a, f.page_dx, f.np_scale) == (0, 1000, 0, 960)
    f = frame(fw, 1000, 1)
    assert f.card.t() == NP and (f.radius, f.zoom, f.vig, f.np_art_a) == (6, 1000, 0, 1000)
    assert (f.np_a, f.page_a, f.page_dx, f.np_scale, f.src_a) == (1000, 0, -28, 1000, 0)


def test_the_lite_variant_has_the_morph_without_arc_shadow_scale_or_slide(fw):
    f, h = frame(fw, 500, 0, hero=0), frame(fw, 500, 0, hero=1)
    assert f.shadow == 0 and f.np_scale == 1000 and f.page_dx == 0
    k = (f.card.x - NP[0]) / (PANE[0] - NP[0])
    assert abs(f.card.y - (NP[1] + (PANE[1] - NP[1]) * k)) <= 1  # a straight line
    assert 0 < f.radius < 6 and 1000 < f.zoom < 1100 and h.card.y < f.card.y
    assert frame(fw, 1000, 0, hero=0).card.t() == PANE


def window(fw, w, h, zoom, tw, th, pane, pu, pv, pw=PANE[2], ph=PANE[3]):
    u, v, s = C.c_int(), C.c_int(), C.c_int()
    fw.fn('art_zoom_window', None, *[C.c_int] * 10, C.c_void_p, C.c_void_p, C.c_void_p)(
        w, h, zoom, tw, th, pane, pu, pv, pw, ph, C.byref(u), C.byref(v), C.byref(s))
    return u.value, v.value, s.value


def test_the_card_aspect_fills_and_lands_on_the_panes_own_window(fw):
    # Now Playing's end: the whole square cover in 150 px, centered
    u, v, s = window(fw, 150, 150, 1000, 352, 352, 0, 9 * 256, 4 * 256)
    assert s == round(352 * 65536 / 150) and 0 <= u < 32 and u == v  # the step's rounding, a 2000th of a texel
    # the pane's end: a texel a pixel, exactly where the pan has the pane
    assert window(fw, 179, 320, 1100, 352, 352, 1000, 9 * 256, 4 * 256) == (9 * 65536, 4 * 65536, 65536)
    # a tall card crops the sides, never squashes: one step both ways
    u, v, s = window(fw, 120, 240, 1000, 352, 352, 0, 0, 0)
    assert s == round(352 * 65536 / 240) and 0 <= v < 64 and u == (352 * 65536 - 120 * s) // 2


TW = TH = 352


class Card:
    """A pane texture (tw + 3 words a row, a texel of border round it, as slide_load makes it), every texel its own
    colour, and the screen's buffer the card is written into."""
    def __init__(self, fw):
        self.fw, self.ts = fw, TW + 3
        self.tex = (C.c_uint * (self.ts * (TH + 3)))()
        for j in range(TH + 3):
            for i in range(self.ts):
                self.tex[j * self.ts + i] = 0xff000000 | (min(j, TH) * 7 % 256) << 16 | (min(i, TW) * 5 % 256) << 8 | (i + j) % 256
        self.fb = (C.c_uint * (W * H))(*([0xff123456] * (W * H)))

    def texel(self, i, j):
        return self.tex[(j + 1) * self.ts + i + 1]

    def draw(self, r, radius, u, v, step, vig, rgba=0):
        rect = Rect(*r)
        self.fw.fn('art_zoom_card', None, C.c_void_p, C.c_int, C.c_void_p, C.c_int, C.c_void_p, C.c_int, C.c_int,
                   C.c_int, C.c_int, C.c_int, C.c_int)(self.fb, W, C.addressof(rect), radius,
                                                       C.addressof(self.tex) + 4 * (self.ts + 1), self.ts, u, v, step,
                                                       vig, rgba)

    def at(self, x, y):
        return self.fb[y * W + x]


def dim(fw, c, k):
    return c & 0xff000000 | (c & 0xff00ff) * k >> 8 & 0xff00ff | (c & 0xff00) * k >> 8 & 0xff00


def bgra(c):
    return c & 0xff00ff00 | c >> 16 & 0xff | (c & 0xff) << 16


def test_at_the_pane_the_card_is_the_panes_texels_at_its_pan_under_its_vignette(fw):
    """The pane's texture is RGBA8888 (slide_load's) and the screen BGRA8888: each texel lands with red and blue
    swapped (on the emulator, a gold cover flew blue before they were)."""
    card = Card(fw)
    u, v, s = window(fw, 179, 320, 1100, TW, TH, 1000, 9 * 256, 4 * 256)
    card.draw(PANE, 0, u, v, s, 1000, rgba=1)
    vig = fw.fn('coverflow_home_vig', C.c_uint, C.c_int, C.c_int)
    for y in range(H):
        for x in range(0, PANE[2], 7):
            assert card.at(PANE[0] + x, y) == dim(fw, bgra(card.texel(9 + x, 4 + y)), vig(y, 320)), (x, y)
    assert card.at(PANE[0] - 1, 100) == 0xff123456  # nothing left of the pane


def test_the_cards_corners_are_round(fw):
    card = Card(fw)
    u, v, s = window(fw, 150, 150, 1000, TW, TH, 0, 0, 0)
    card.draw(NP, 6, u, v, s, 0)
    assert card.at(NP[0], NP[1]) == 0xff123456 and card.at(NP[0] + 149, NP[1] + 149) == 0xff123456
    assert card.at(NP[0] + 6, NP[1]) != 0xff123456 and card.at(NP[0] + 75, NP[1] + 75) != 0xff123456


class Draws:
    """Stock's canvas calls an art_zoom frame makes, each image with the alpha it was drawn at."""
    def __init__(self, fw, ui, st):
        self.fw, self.images, self.alpha = fw, [], 255
        self.lcd, self.canvas = C.create_string_buffer(0x200), C.create_string_buffer(0x100)
        self.fb = (C.c_uint * (W * H))()
        struct.pack_into('<Q', self.canvas, O['CANVAS_LCD'], C.addressof(self.lcd))
        struct.pack_into('<Q', self.lcd, O['LCD_OFFLINE_FB'], C.addressof(self.fb))
        for k, v in (('LCD_W', W), ('LCD_H', H), ('LCD_LINE_LENGTH', 4 * W), ('LCD_FORMAT', O['LCD_BGRA8888'])):
            struct.pack_into('<i', self.lcd, O[k], v)
        self.prev, self.curr, self.px = C.create_string_buffer(0x48), C.create_string_buffer(0x48), (C.c_uint * (W * H))()
        fw.mock('canvas_set_global_alpha', lambda c, a, *_: setattr(self, 'alpha', a))
        fw.mock('canvas_draw_image', lambda c, b, src, dst, *_: self.images.append(
            (b, struct.unpack('<4i', C.string_at(src, 16)), struct.unpack('<4i', C.string_at(dst, 16)), self.alpha)))
        fw.mock('bitmap_lock_buffer_for_read', lambda *_: C.addressof(self.px))
        fw.mock('bitmap_get_line_length', lambda *_: 4 * W)
        self.cover = ui.widget('image', box=NP, name='img_cover')
        st('np_cover', C.c_void_p).value = self.cover.at

    def run(self, t, open_):
        self.images.clear()
        args = (C.c_void_p, C.c_void_p, C.c_int, C.c_int, C.c_int)
        self.fw.fn('art_zoom__az_draw_prev', None, *args)(C.addressof(self.canvas), C.addressof(self.prev), t, open_, 1)
        self.fw.fn('art_zoom__az_draw_curr', None, C.c_void_p, *args)(
            C.addressof(self.canvas), C.addressof(self.curr), C.addressof(self.prev), t, open_, 1)
        return self.images


def overlaps(a, b):
    return a[0] < b[0] + b[2] and b[0] < a[0] + a[2] and a[1] < b[1] + b[3] and b[1] < a[1] + a[3]


@pytest.mark.parametrize('open_', [0, 1])
def test_the_cover_is_never_on_the_screen_twice(fw, ui, st, open_):
    """Both snapshots are drawn with their own cover cut out: mid-run the card (written into the screen's buffer) is
    the only cover; each end's own cover is laid over the card alone, where the run starts or ends."""
    reduce_motion(fw, 0)
    album = ui.widget('window', ui.wm, (0, 0, W, H), name='albuminfo_page')
    album.props[b'_lb_page'] = 1  # dressed as the design's album page (library_album.c): its header's cover
    np = ui.widget('window', ui.wm, (0, 0, W, H), name='playing_page')
    ui.top = np
    fw.mock('window_animator_factory', lambda *_: 0x5000)
    fw.mock('window_animator_factory_register', lambda *_: 0)
    fw.fn('art_zoom_load', None, C.c_void_p)(np.at)
    src = Rect()
    assert fw.fn('art_zoom_source', C.c_int, C.c_void_p, C.c_void_p)(album.at, C.addressof(src)) == 2  # AZ_ALBUM
    d = Draws(fw, ui, st)
    for t in (300, 500, 700):
        for img, s, dst, alpha in d.run(t, open_):
            hole = NP if img == C.addressof(d.curr) else src.t()
            assert not overlaps(s, hole), (t, s, hole)
    assert any(d.fb), 'the card was written into the screen'
    last = d.run(1000, open_)[-1]
    end = (C.addressof(d.curr), NP, NP, 255) if open_ else (C.addressof(d.prev), src.t(), src.t(), 255)
    assert last == end  # the last frame's cover is the page's own, pixel for pixel


class Hints:
    def __init__(self, fw, ui):
        self.registered = []
        fw.mock('window_animator_factory', lambda *_: 0x5000)
        fw.mock('window_animator_factory_register', lambda f, t, fn, *_: self.registered.append(C.string_at(t)) or 0)
        self.ui, self.np = ui, None

    def load(self, fw):
        """Now Playing's window as it loads: already on top of the stack."""
        self.np = self.ui.widget('window', self.ui.wm, (0, 0, 375, 320), name='playing_page')
        self.ui.top = self.np
        fw.fn('art_zoom_load', None, C.c_void_p)(self.np.at)


@pytest.mark.parametrize('reduced,hero', [(0, 1), (0, 2), (1, 1)])
def test_now_playing_opened_over_an_album_page_takes_the_card_both_ways(fw, ui, reduced, hero):
    reduce_motion(fw, reduced)
    fw.fn('art_zoom_force', None, C.c_int)(hero)
    h = Hints(fw, ui)
    album = ui.widget('window', ui.wm, (0, 40, 375, 280), name='albuminfo_page')
    album.props[b'_lb_page'] = 1  # dressed as the design's album page (library_album.c)
    h.load(fw)
    run = b'art_zoom(duration=420)' if hero == 1 else b'art_zoom(duration=340)'
    want = b'fade(duration=150)' if reduced else run
    assert h.np is not None
    assert h.np.props[b'open_anim_hint'] == want and h.np.props[b'close_anim_hint'] == want
    assert h.registered == ([] if reduced else [b'art_zoom'])


@in_fork  # a NULL read here kills the process: the child's death fails the test instead
def test_now_playing_opened_from_a_list_opens_at_once(fw, ui):
    reduce_motion(fw, 0)
    h = Hints(fw, ui)
    ui.widget('window', ui.wm, (0, 40, 375, 280), name='allmusic_page')
    h.load(fw)
    assert h.np is not None
    assert b'open_anim_hint' not in h.np.props and h.registered == []  # nothing set: stock's immediate open


@in_fork
def test_now_playing_loaded_new_over_an_albums_tracks_opens_at_once(fw, ui):
    """Cover Flow > an album's tracks > Previous > Centre > wheel 4 > Centre loaded Now Playing's window new over the
    track list, which shows no cover. A new window's open_anim_hint is NULL from stock (Ui.prop_str), and
    art_zoom_load, first in motion_load, read it: demo died with SIGSEGV there (2026-11-17, release/p32)."""
    reduce_motion(fw, 0)
    h = Hints(fw, ui)
    ui.widget('window', ui.wm, (0, 40, 375, 280), name='albuminfo_page')  # not dressed: the plain track list
    h.np = ui.widget('window', ui.wm, (0, 0, 375, 320), name='playing_page')
    ui.top = h.np
    fw.fn('navigation__motion_load', None, C.c_void_p, C.c_int)(h.np.at, 1)  # ringnav_ui_load's call
    assert b'open_anim_hint' not in h.np.props and h.registered == []  # nothing set: stock's immediate open
