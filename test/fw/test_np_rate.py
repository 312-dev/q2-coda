"""Now Playing's rating page and the album art's glow (patch/np_rate.c, docs/ipod.md#rating-page): the slide_view's
page after the lyrics, full screen at rest; five stars the wheel, Centre and a tap set; the glow behind it and the
lyrics, made once for each cover. Lyrics here are placeholder lines, never a song's."""
import ctypes as C
import struct

import pytest

import host
from conftest import O, Files
from test_nav_library_pages import LIBRARY, RATINGS, listen_key, ratings
from test_nav_playing import KEY_CENTER, KEY_NEXT, KEY_PREV, STOP, Canvas, Lyrics, scratch, st, tap, theme, ui  # noqa: F401

NR = host.defines('np_rate.c', 'NR_')
PATH = b'/mnt/mmc/Music/02.flac'  # Now's playing track: the queue's third
DOWN, UP, ABORT = O['EVT_POINTER_DOWN'], O['EVT_POINTER_UP_BEFORE'], O['EVT_POINTER_ABORT']
RATE_PAGE = 2  # the slide_view's third page: art, lyrics, rating


class Rate(Lyrics):
    """Now Playing under OLED with the rating page after the lyrics (tools/ipod.py np_rate_page), on page, the
    playing library track's hearts kept (or none), My Fav as fav says."""
    def __init__(self, fw, ui, mcl, st, page=RATE_PAGE, kept=0, fav=False, **kwargs):
        theme(fw, st)
        mcl.type = LIBRARY
        self.files = Files(fw, {RATINGS: ratings((listen_key(PATH), kept))} if kept else {})
        self.fav = fav
        fw.mock('checkFavExist', lambda *_: self.fav)
        super().__init__(fw, ui, mcl, st, page=page, opened=False, lyrics=kwargs.pop('lyrics', [('Placeholder line', 0)]),
                         **kwargs)
        self.rate = ui.widget(self.slide, 'view_rate', 0, 0, 225, 198)
        assert self.open() == 41

    def stars(self):
        return self.fw.fn('rate_now', C.c_int)()

    def kept(self):
        return self.fw.fn('rate_kept', C.c_int, C.c_int, C.c_uint)(0, listen_key(PATH))

    def wheel(self, key):
        return self.fw.fn('np_rate_key', C.c_int, C.c_uint)(key)

    def centre(self, at=300000):
        self.ui.now = at
        self.st.center_down_at = at - 60
        return self.key(KEY_CENTER)

    def tap(self, x, y, lift=None):
        tap(self.ui, self.rate, DOWN, x, y)
        return tap(self.ui, self.rate, UP, *(lift or (x, y)))


def star_x(i):
    """Star i's (0 to 4) centre on the screen"""
    return 375 // 2 + (i - 2) * (NR['NR_STAR'] + NR['NR_STAR_GAP'])


STAR_Y = NR['NR_STARS_Y'] + NR['NR_STAR'] // 2


@pytest.fixture
def rate(fw, ui, mcl, st):
    return Rate(fw, ui, mcl, st)


def painted_stars(fw, rate, light=False):
    """The page painted: each star's art name and box, and the alpha it was drawn at."""
    canvas = Canvas(fw)
    alphas = []
    fw.mock('canvas_set_global_alpha', lambda c, a, *_: alphas.append(a) or 0)
    fw.fn('np_rate_bg', None, C.c_void_p, C.c_void_p)(rate.rate, canvas.p)
    stars = [(name.decode(), box) for name, box in canvas.images if b'npstar' in name]
    return stars, alphas, canvas


# The page: the slide_view's page after the lyrics, full screen at rest, back in the column off it.

def test_the_page_after_the_lyrics_rests_full_screen_and_a_swipe_off_it_puts_the_column_back(fw, ui, mcl, st):
    rate = Rate(fw, ui, mcl, st, page=1)
    _, _, win_w, win_h = ui.box(rate.win)
    col = ui.box(rate.col)
    assert ui.kids[rate.slide][-1] == rate.rate and ui.kids[rate.slide].index(rate.rate) == RATE_PAGE
    rate.poll()
    assert st.np_full == 0  # the lyrics stay in their column
    ui.ints[rate.slide, b'value'] = RATE_PAGE  # the swipe lands on the rating page
    rate.pressed = 1
    rate.poll()
    assert st.np_full == 0  # not while the finger is down
    rate.pressed = 0
    rate.poll()
    assert st.np_full == 2 and ui.box(rate.col) == (0, 0, win_w, win_h)
    ui.ints[rate.slide, b'value'] = 1  # swiped back to the lyrics
    rate.poll()
    assert st.np_full == 0 and ui.box(rate.col) == col


def test_full_screen_lyrics_stay_full_onto_the_rating_page_and_end_on_the_art(fw, ui, mcl, st):
    """A tap made the lyrics full screen: the rating page keeps it, and the art page ends it, as for the lyrics."""
    rate = Rate(fw, ui, mcl, st, page=1)
    tap(ui, rate.lyric, DOWN, 100, 50)  # the blank slot over the first line: a line's tap would seek there
    tap(ui, rate.lyric, UP, 100, 50)
    assert st.np_full == 2
    ui.ints[rate.slide, b'value'] = RATE_PAGE
    rate.poll()
    assert st.np_full == 2
    ui.ints[rate.slide, b'value'] = 0
    rate.poll()
    assert st.np_full == 0


def test_a_window_sliding_in_waits_for_full_screen(fw, ui, mcl, st):
    rate = Rate(fw, ui, mcl, st)
    rate.animating = 1
    rate.poll()
    assert st.np_full == 0
    rate.animating = 0
    rate.poll()
    assert st.np_full == 2


def test_the_rating_page_holds_no_screen_awake(fw, ui, mcl, st):
    """Smart Awake holds the screen on the lyrics page alone (now_playing.c keep_lit_poll)."""
    rate = Rate(fw, ui, mcl, st)  # Smart Awake is on by default ([IPOD] KEEPLIT), the song playing
    resets = []
    fw.mock('reset_poweroptions_timer', lambda *a: resets.append(a) or 0)
    keep = fw.fn('navigation__keep_lit_poll', None)
    ui.now += 2000
    keep()
    assert resets == []
    ui.ints[rate.slide, b'value'] = 1
    ui.now += 2000
    keep()
    assert len(resets) == 1


# The stars: the song's kept rating, the wheel's target, Centre, a tap.

@pytest.mark.parametrize('kept, fav, stars', [(0, False, 0), (1, False, 1), (3, False, 3), (4, False, 4), (5, False, 5),
                                              (2, True, 2), (0, True, 0)])
def test_the_kept_rating_is_the_stars_and_my_fav_adds_none(fw, ui, mcl, st, kept, fav, stars):
    """The ratings file is read as it is: its 1-4 (all it held before) are 1-4 stars, five now too; a liked song (My
    Fav, the heart) has only its own stars, none for the like."""
    rate = Rate(fw, ui, mcl, st, kept=kept, fav=fav)
    assert rate.stars() == stars
    drawn, _, _ = painted_stars(fw, rate)
    assert [n.split('_')[2] for n, _ in drawn] == ['on'] * stars + ['off'] * (5 - stars)


def test_the_stars_stand_centred_where_the_design_has_them(fw, ui, mcl, st):
    rate = Rate(fw, ui, mcl, st, kept=3)
    drawn, alphas, canvas = painted_stars(fw, rate)
    star = NR['NR_STAR']
    assert [n for n, _ in drawn] == ['oled_npstar_on_d'] * 3 + ['oled_npstar_off_d'] * 2
    top = ui.screen(rate.rate)[1]  # the page's top on screen: the design's rows are the screen's
    assert [box[:2] for _, box in drawn] == [(star_x(i) - star // 2, NR['NR_STARS_Y'] - top) for i in range(5)]
    assert abs(star_x(0) - star // 2 - (375 - (star_x(4) + star // 2))) <= 1  # centred on 375's odd middle
    assert alphas == [NR['NR_FAINT'], 0xff] * 2  # the two outlines at 30%, each put back
    texts = [t for t, *_ in canvas.texts]
    assert texts[:1] == ["Moment's Notice"]  # the title, then the artist, nothing else written


def test_the_wheel_moves_a_target_from_the_rating_and_centre_keeps_it(fw, ui, mcl, st):
    rate = Rate(fw, ui, mcl, st, kept=3)
    assert rate.wheel(KEY_NEXT) == 1
    drawn, alphas, _ = painted_stars(fw, rate)
    big = NR['NR_STAR_BIG']
    assert [n for n, _ in drawn] == ['oled_npstar_on_d'] * 3 + ['oled_npstar_off_big_d', 'oled_npstar_off_d']
    top = ui.screen(rate.rate)[1]
    assert drawn[3][1][:2] == (star_x(3) - big // 2, STAR_Y - big // 2 - top)  # the target, larger, in full white
    assert alphas == [NR['NR_FAINT'], 0xff]  # only the last outline is faint
    assert rate.kept() == 3  # nothing kept yet
    rate.wheel(KEY_NEXT), rate.wheel(KEY_NEXT)  # stays on the fifth
    rate.wheel(KEY_PREV)
    assert rate.centre() == STOP
    assert rate.kept() == 4 and rate.stars() == 4
    drawn, _, _ = painted_stars(fw, rate)
    assert all('big' not in n for n, _ in drawn)  # at rest again


def test_the_target_goes_back_to_rest_by_itself(fw, ui, mcl, st):
    rate = Rate(fw, ui, mcl, st, kept=2)
    rate.wheel(KEY_PREV)
    (t,) = ui.due('nr_target_end')
    assert ui.timers[t][2] == NR['NR_TARGET_MS']
    ui.run(t)
    drawn, _, _ = painted_stars(fw, rate)
    assert all('big' not in n for n, _ in drawn) and rate.kept() == 2


def test_centre_at_rest_puts_the_target_on_the_rating_s_top_and_again_on_it_clears(fw, ui, mcl, st):
    """The page's "0": the star that is the rating's top, kept again, clears the rating."""
    rate = Rate(fw, ui, mcl, st, kept=3)
    rate.centre()
    drawn, _, _ = painted_stars(fw, rate)
    assert drawn[2][0] == 'oled_npstar_on_big_d' and rate.kept() == 3
    rate.centre(301000)
    assert rate.kept() == 0 and rate.stars() == 0


def test_centre_on_an_unrated_song_starts_on_the_first_star(fw, ui, mcl, st):
    rate = Rate(fw, ui, mcl, st)
    rate.centre()
    drawn, _, _ = painted_stars(fw, rate)
    assert drawn[0][0] == 'oled_npstar_off_big_d'
    rate.centre(301000)
    assert rate.kept() == 1


@pytest.mark.parametrize('kept, star, after', [(0, 4, 4), (2, 5 - 1, 4), (4, 1, 1), (3, 3, 0), (1, 1, 0), (0, 5, 5),
                                               (5, 5, 0)])
def test_a_tap_on_a_star_keeps_it_at_once_and_the_rating_s_own_top_clears_it(fw, ui, mcl, st, kept, star, after):
    rate = Rate(fw, ui, mcl, st, kept=kept)
    assert rate.tap(star_x(star - 1) + 20, STAR_Y - 25) == [0]  # never a STOP: the slide_view keeps its grab
    assert rate.kept() == after and rate.stars() == after


def test_a_swipe_or_a_touch_off_the_stars_keeps_nothing(fw, ui, mcl, st):
    rate = Rate(fw, ui, mcl, st, kept=2)
    rate.tap(star_x(4), STAR_Y, lift=(star_x(4) - 80, STAR_Y))  # a swipe from the fifth star
    rate.tap(star_x(4), 260)  # under the stars
    tap(ui, rate.rate, DOWN, star_x(4), STAR_Y)
    tap(ui, rate.rate, ABORT, star_x(4), STAR_Y)  # the slide_view took it
    tap(ui, rate.rate, UP, star_x(4), STAR_Y)
    assert rate.kept() == 2


def test_the_wheel_and_centre_are_the_page_s_only_on_it(fw, ui, mcl, st):
    """On the lyrics the wheel goes on (the lyrics' or the volume's), and Centre starts a scrub as before."""
    rate = Rate(fw, ui, mcl, st, page=1, kept=2)
    assert rate.wheel(KEY_NEXT) == 0
    rate.centre()
    assert st.scrub == 1 and rate.kept() == 2


def test_a_song_nothing_can_rate_leaves_the_wheel_to_the_volume(fw, ui, mcl, st):
    rate = Rate(fw, ui, mcl, st)
    mcl.type = 0x2000  # a stream's queue
    assert rate.wheel(KEY_NEXT) == 0
    rate.tap(star_x(2), STAR_Y)
    assert rate.kept() == 0


def test_a_rating_changed_elsewhere_shows_within_the_poll(fw, ui, mcl, st):
    """A rating from the server (ratings_in) or Car Remote reaches the page on its next read."""
    rate = Rate(fw, ui, mcl, st, kept=1)
    rate.poll()
    fw.fn('rate_keep', C.c_int, C.c_int, C.c_uint, C.c_int)(0, listen_key(PATH), 4)
    ui.dirty.clear()
    rate.poll(1, ms=NR['NR_POLL_MS'])
    assert rate.rate in [d for d, _ in ui.dirty]
    drawn, _, _ = painted_stars(fw, rate)
    assert [n.split('_')[2] for n, _ in drawn] == ['on'] * 4 + ['off']


# The glow: made once for each cover a glow page shows, drawn a row at a time after; none in Light Mode.

class Cover:
    """A cover as stock's image manager holds it: w x h RGBA8888, every pixel rgb (or a function of x, y); the glow's
    bitmap the C makes, and every image drawn and its source and destination."""
    def __init__(self, fw, rgb=0xffffff, w=40, h=40):
        self.w, self.h, self.loads, self.drawn, self.alphas = w, h, [], [], []
        self.px = (C.c_ubyte * (w * h * 4))()
        for y in range(h):
            for x in range(w):
                c = rgb(x, y) if callable(rgb) else rgb
                self.px[(y * w + x) * 4:(y * w + x) * 4 + 4] = [c >> 16 & 255, c >> 8 & 255, c & 255, 255]
        self.fw, self.glow = fw, C.create_string_buffer(0x40)
        self.glow_px = (C.c_uint * 1)()  # the glow's pixels once the C makes it (create)
        self.size = (0, 0)
        self.mocks()

    def mocks(self):
        """Its mocks again, over a Canvas's (which loads images by name)."""
        fw = self.fw
        fw.mock('widget_load_image', self.load)
        fw.mock('bitmap_lock_buffer_for_read', lambda b, *_: C.addressof(self.px))
        fw.mock('bitmap_get_line_length', lambda b, *_: self.w * 4)
        fw.mock('bitmap_unlock_buffer', lambda *_: 0)
        fw.mock('bitmap_create_ex', self.create)
        fw.mock('bitmap_lock_buffer_for_write', lambda b, *_: C.addressof(self.glow_px))
        fw.mock('canvas_draw_image', lambda c, b, src, dst, *_: self.drawn.append(
            (b, struct.unpack_from('<4i', C.string_at(src, 16)), struct.unpack_from('<4i', C.string_at(dst, 16)))) or 0)
        fw.mock('canvas_set_global_alpha', lambda c, a, *_: self.alphas.append(a) or 0)

    def load(self, w, name, bitmap, *_):
        self.loads.append(C.string_at(name))
        struct.pack_into('<IIIHH', (C.c_char * 16).from_address(bitmap), 0, self.w, self.h, 0, 0, 1)  # format 1: RGBA
        return 0

    def create(self, w, h, line, fmt, *_):
        self.size = (w, h)
        self.glow_px = (C.c_uint * (w * h))()
        return C.addressof(self.glow)

    def pixels(self):
        w, h = self.size
        return [[self.glow_px[y * w + x] for x in range(w)] for y in range(h)]


def bg(fw, w, cover=None):
    canvas = Canvas(fw)
    if cover:
        cover.mocks()
    fw.fn('np_rate_bg', None, C.c_void_p, C.c_void_p)(w, canvas.p)
    return canvas


def test_the_glow_is_made_once_a_song_and_drawn_behind_both_pages(fw, ui, mcl, st):
    rate = Rate(fw, ui, mcl, st, page=1)
    ui.set_str(rate.cover, b'image', b'file:///tmp/coverpic.jpg')
    cover = Cover(fw, 0x3060c0)
    builds = fw.fn('np_rate_builds', C.c_uint)
    rate.poll(5)
    assert builds() == 0  # nothing painted a glow page yet: no glow made
    for _ in range(4):
        bg(fw, rate.lyric, cover), bg(fw, rate.rate, cover)
    covers = [n for n in cover.loads if b'npstar' not in n]  # the stars' art loads too
    assert builds() == 1 and covers == [b'file:///tmp/coverpic.jpg']  # read once, for both pages and every paint
    glow = C.addressof(cover.glow)
    lyric = [d for d in cover.drawn if d[0] == glow and d[2] == (0, 0, 225, 198)]
    assert len(lyric) == 8  # each paint a copy of the page's rows
    st.np_art = st.np_art + 1  # another song's cover (now_playing.c np_art_poll)
    bg(fw, rate.lyric, cover), bg(fw, rate.lyric, cover)
    assert builds() == 2


def test_the_art_page_and_another_window_never_make_a_glow(fw, ui, mcl, st):
    rate = Rate(fw, ui, mcl, st, page=0)
    cover = Cover(fw)
    bg(fw, rate.art_page, cover), bg(fw, rate.win, cover), bg(fw, rate.lrc, cover)
    assert fw.fn('np_rate_builds', C.c_uint)() == 0


def test_light_mode_has_no_glow(fw, ui, mcl, st):
    rate = Rate(fw, ui, mcl, st)
    theme(fw, st, light=1)
    cover = Cover(fw)
    bg(fw, rate.lyric, cover)
    assert fw.fn('np_rate_builds', C.c_uint)() == 0 and cover.drawn == []


def luminance(c):
    def lin(v):
        v /= 255
        return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4
    return 0.2126 * lin(c >> 16 & 255) + 0.7152 * lin(c >> 8 & 255) + 0.0722 * lin(c & 255)


@pytest.mark.parametrize('rgb', [0xffffff, 0xff2020, 0x20ff20, 0xffff00])
def test_the_glow_keeps_text_secondary_lyrics_at_four_and_a_half_to_one(fw, ui, mcl, st, rgb):
    """The brightest cover still leaves every glow pixel dark enough that Text Secondary (#ABABAB, the unsung lyric
    lines) reads at WCAG's 4.5:1 over it, runs to the screen's top edge under the status row (full-bleed), and fades
    to black at the foot's corners."""
    rate = Rate(fw, ui, mcl, st)
    ui.set_str(rate.cover, b'image', b'art')
    cover = Cover(fw, rgb)
    bg(fw, rate.rate, cover)
    px = cover.pixels()
    brightest = max(luminance(c) for row in px for c in row)
    assert brightest > 0.005  # there is a glow
    assert (luminance(0xababab) + 0.05) / (brightest + 0.05) >= 4.5
    assert any(c & 0xffffff for c in px[0])  # the top row: the glow, under the status row's glyphs
    assert px[-1][0] & 0xffffff == 0 and px[-1][-1] & 0xffffff == 0  # the corners at the foot: black


def test_the_glow_follows_the_cover_s_colours_across_the_window(fw, ui, mcl, st):
    """A cover red on its left half and blue on its right washes red to the left of the glow and blue to its right."""
    rate = Rate(fw, ui, mcl, st)
    ui.set_str(rate.cover, b'image', b'art')
    cover = Cover(fw, lambda x, y: 0xff0000 if x < 20 else 0x0000ff)
    bg(fw, rate.rate, cover)
    px = cover.pixels()
    y = NR['NR_GLOW_CY']
    left, right = px[y][100], px[y][275]
    assert left >> 16 & 255 > left & 255 and right & 255 > right >> 16 & 255


def test_the_lyrics_fade_into_the_glow_not_black(fw, ui, mcl, st):
    """lyrics.c lyrics_fade: over a glow, each fade row is the glow's own row at the fade's alpha."""
    rate = Rate(fw, ui, mcl, st, page=1, lyrics=[(f'Placeholder line {i}', 10 * i) for i in range(12)])
    ui.set_str(rate.cover, b'image', b'art')
    cover = Cover(fw)
    canvas = Canvas(fw)
    cover.mocks()  # over the canvas's
    from test_lyrics import state, table
    ly = state(fw)
    table(fw, [(10 * i, f'Placeholder line {i}', []) for i in range(12)])
    ly.lrc, ly.rows = rate.lrc, 12  # the rows built for these lines: lyrics_on
    fw.fn('lyrics_fade', None, C.c_void_p, C.c_void_p)(rate.lrc, canvas.p)
    rows = [d for d in cover.drawn if d[0] == C.addressof(cover.glow)]
    fade = host.defines('lyrics.c', 'LYRIC_')['LYRIC_FADE_PX']
    assert len(rows) == 2 * fade and all(d[1][3] == 1 for d in rows) and canvas.rects == []  # no fill of black
    steps = [255 * (fade - i) // fade for i in range(1, fade)]  # the edge row opaque, then clearer inward
    assert cover.alphas == [a for step in steps for a in (step, 0xff) * 2]  # the top's and the foot's, each put back


# The approved mock-up's wash (rating page, 2026-11-18: a CSS radial gradient at 50% 38% of the screen, blurred
# 30 px), its brightest channel down x 60 as measured on its PNG: row: level.
MOCK_X60 = {0: 17, 30: 28, 58: 37, 80: 42, 100: 44, 120: 44, 160: 39, 200: 26, 240: 11, 280: 2, 310: 0}


def level(c):
    return max(c >> 16 & 255, c >> 8 & 255, c & 255)


def test_the_glow_follows_the_mock_up_s_wash_down_the_screen(fw, ui, mcl, st):
    """A cover bright enough to meet the cap washes the screen as the mock-up does: down x 60 each row within 6 levels
    of the mock-up's, its peak too, the top row lit under the status row and the foot dark."""
    rate = Rate(fw, ui, mcl, st)
    ui.set_str(rate.cover, b'image', b'art')
    cover = Cover(fw, 0xffffff)
    bg(fw, rate.rate, cover)
    px = cover.pixels()
    assert cover.size == (375, 320)  # the whole screen
    column = [level(row[60]) for row in px]
    assert {y: column[y] for y in MOCK_X60} == pytest.approx(MOCK_X60, abs=6)
    assert abs(max(column) - max(MOCK_X60.values())) <= 6
    assert column[0] >= 10 and level(px[0][187]) >= 30  # the top edge: the wash, not black


def test_the_glow_runs_round_the_column_to_the_screen_s_edges(fw, ui, mcl, st):
    """On the lyrics page in its column the window's own ground above it (under the status row) and below it (behind
    the progress bar and the state row) is the glow's rows over the page's columns, so the wash reaches the screen's
    top and foot with no seam at the page's edges."""
    rate = Rate(fw, ui, mcl, st, page=1)
    ui.set_str(rate.cover, b'image', b'art')
    cover = Cover(fw, 0xffffff)
    bg(fw, rate.win, cover)
    glow = C.addressof(cover.glow)
    assert [d[1:] for d in cover.drawn if d[0] == glow] == [((80, 0, 225, 22),) * 2, ((80, 220, 225, 100),) * 2]
    cover.drawn.clear()
    bg(fw, rate.lyric, cover)
    assert [d[1] for d in cover.drawn if d[0] == glow] == [(80, 22, 225, 198)]  # the page's rows, from where the band ends
    assert fw.fn('np_rate_builds', C.c_uint)() == 1


@pytest.mark.parametrize('value, offset, src, dst', [
    (0, 0, None, None),  # the art's page at rest: no glow round it
    (0, 100, (80, 100), (205, 100)),  # the art dragged left: the lyrics come in from the right
    (1, -100, (80, 125), (180, 125)),  # the lyrics dragged right: the art's part is bare
    (1, 60, (140, 165), (80, 165)),  # the lyrics dragged left, the rating page coming in
])
def test_the_band_s_glow_moves_with_the_page_s_drag(fw, ui, mcl, st, value, offset, src, dst):
    """src and dst: the columns (x, width) the glow's rows above and below the column are drawn from and to."""
    rate = Rate(fw, ui, mcl, st, page=value)
    ui.ints[rate.slide, b'xoffset'] = offset  # AWTK's slide_view: the drag, its pages at -xoffset
    ui.set_str(rate.cover, b'image', b'art')
    cover = Cover(fw, 0xffffff)
    bg(fw, rate.win, cover)
    drawn = [d[1:] for d in cover.drawn if d[0] == C.addressof(cover.glow)]

    def strips(src, dst):
        return [((src[0], y, src[1], h), (dst[0], y, dst[1], h)) for y, h in ((0, 22), (220, 100))]
    if src is None:
        assert drawn == [] and fw.fn('np_rate_builds', C.c_uint)() == 0
    elif value == 1 and offset > 0:  # both glow pages show: the lyrics' part, then the rating page's
        assert drawn == strips(src, dst) + strips((80, 60), (245, 60))
    else:
        assert drawn == strips(src, dst)


def test_full_screen_and_light_mode_put_no_glow_over_the_window(fw, ui, mcl, st):
    rate = Rate(fw, ui, mcl, st, page=1)
    ui.set_str(rate.cover, b'image', b'art')
    st.np_full = 1  # the column the whole window: its own glow from the top
    cover = Cover(fw, 0xffffff)
    bg(fw, rate.win, cover)
    assert cover.drawn == []
    st.np_full = 0
    theme(fw, st, light=1)
    bg(fw, rate.win, cover)
    assert cover.drawn == []


def test_the_band_is_painted_again_as_the_drag_or_the_glow_changes(fw, ui, mcl, st):
    """Nothing else repaints the window round the column, so each lit pass asks for it when the slide_view's page, its
    drag or the glow changed, and only then."""
    rate = Rate(fw, ui, mcl, st, page=1)
    ui.set_str(rate.cover, b'image', b'art')
    band = (rate.win, (80, 220, 225, 100))  # below the column (above it too)
    rate.poll(1)
    ui.dirty.clear()
    rate.poll(3)
    assert band not in ui.dirty
    ui.ints[rate.slide, b'xoffset'] = 40
    rate.poll(1)
    assert ui.dirty.count(band) == 1 and ui.dirty.count((rate.win, (80, 0, 225, 22))) == 1
    ui.dirty.clear()
    bg(fw, rate.lyric, Cover(fw, 0x3060c0))  # a glow made
    rate.poll(2)
    assert ui.dirty.count(band) == 1



