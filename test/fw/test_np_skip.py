"""np_skip.c: on a track change Now Playing's old art and lines slide 16 px the way the queue moved and fade out
while the new ones come in from the other side (docs/oled-design.md#motion); under Reduce Motion the two cross-fade in
place. The old is the screen's last frame, copied as the page's window starts the frame that shows new lines."""
import ctypes as C
import struct

import pytest

from conftest import O
from test_nav_lists import ui  # noqa: F401  (ui: fixture)
from test_nav_painters import scratch, st  # noqa: F401  (fixtures)
from test_ui_kit import reduce_motion

W, H = 375, 320
CANVAS_LCD = O['CANVAS_LCD']
NEXT, PREV = -1, 1


def at(fw, t, reduced=0, d=NEXT):
    out = [C.c_int() for _ in range(4)]
    fw.fn('np_skip_at', None, C.c_int, C.c_int, C.c_int, *[C.c_void_p] * 4)(t, reduced, d, *map(C.addressof, out))
    return tuple(v.value for v in out)  # old alpha, old shift, new alpha, new shift


def test_the_old_leaves_over_120_ms_and_the_new_arrives_from_60_to_260(fw):
    assert at(fw, 0) == (256, 0, 0, 16)  # Next: the new waits 16 px to the right
    ao, xo, an, xn = at(fw, 60)
    assert 128 < ao < 256 and -16 < xo < 0 and an == 0  # cubic_in: the old has left slowly so far
    assert at(fw, 120)[:2] == (0, -16)
    ao, xo, an, xn = at(fw, 160)
    assert an > 128 and 0 < xn < 8  # cubic_out: the new is mostly in at half its time
    assert at(fw, 260)[2:] == (256, 0)
    assert at(fw, 120, d=PREV)[1] == 16 and at(fw, 0, d=PREV)[3] == -16  # Previous: the other way


def test_under_reduce_motion_the_two_cross_fade_in_place_over_150_ms(fw):
    assert at(fw, 0, 1) == (256, 0, 0, 0)
    assert at(fw, 75, 1) == (128, 0, 128, 0)
    assert at(fw, 150, 1) == (0, 0, 256, 0)


class Page:
    """Now Playing's window on a 375 x 320 screen, Dark Mode: the art at (16, 60), the lines from x 178."""
    def __init__(self, fw, ui, st, ground=0xff000000):
        self.fw, self.ui = fw, ui
        self.win = ui.widget('window', ui.wm, (0, 0, W, H), name='playing_page')
        self.cover = ui.widget('image', self.win, (16, 60, 150, 150), name='img_cover')
        self.lines = [ui.widget('hscroll_label', self.win, (178, 70 + 30 * i, 181, 24), text=t)
                      for i, t in enumerate(('Song A', 'Artist', 'Album'))]
        for name, w in (('np_win', self.win), ('np_cover', self.cover), ('np_title', self.lines[0]),
                        ('np_artist', self.lines[1]), ('np_album', self.lines[2]), ('np_slide', None)):
            st(name, C.c_void_p).value = w.at if w else 0
        self.fb = (C.c_uint * (W * H))()
        self.lcd, self.canvas = C.create_string_buffer(0x200), C.create_string_buffer(0x100)
        struct.pack_into('<Q', self.canvas, CANVAS_LCD, C.addressof(self.lcd))
        struct.pack_into('<Q', self.lcd, O['LCD_OFFLINE_FB'], C.addressof(self.fb))
        for k, v in (('LCD_W', W), ('LCD_H', H), ('LCD_LINE_LENGTH', 4 * W), ('LCD_FORMAT', O['LCD_BGRA8888'])):
            struct.pack_into('<i', self.lcd, O[k], v)
        fw.mock('canvas_get_clip_rect', lambda c, out, *_: C.memmove(out, struct.pack('<4i', 0, 0, W, H), 16) and 0)
        self.ground = ground
        self.draw(0xff111111)
        self.frame()  # the page as first painted: nothing runs

    def draw(self, ink):
        """The window's children: the art's box and the lines' in ink, the rest the ground."""
        for i in range(W * H):
            self.fb[i] = self.ground
        for x0, y0, w, h in (self.cover.box, *(ln.box for ln in self.lines)):
            for y in range(y0, y0 + h):
                for x in range(x0, x0 + w):
                    self.fb[y * W + x] = ink

    def frame(self, ink=None):
        self.fw.fn('np_skip_begin', None, C.c_void_p, C.c_void_p)(self.win.at, C.addressof(self.canvas))
        if ink is not None:
            self.draw(ink)
        self.fw.fn('np_skip_end', None, C.c_void_p, C.c_void_p)(self.win.at, C.addressof(self.canvas))

    def px(self, x, y):
        return self.fb[y * W + x]


def channel(p):
    return p & 0xff


@pytest.mark.parametrize('reduced', [0, 1])
def test_new_lines_start_a_run_from_the_frame_on_screen_and_it_ends_on_the_new(fw, ui, st, reduced):
    reduce_motion(fw, reduced)
    page = Page(fw, ui, st)
    page.lines[0].text = 'Song B'
    page.frame(0xff999999)  # the new song's art and lines, drawn
    assert page.px(100, 130) == 0xff111111  # the first frame is the old, whole
    ui.now += 90
    page.frame(0xff999999)
    mid = page.px(100, 130)
    assert channel(0x11) < channel(mid) < channel(0x99)  # the two mixed
    ui.now += 200
    page.frame(0xff999999)
    assert page.px(100, 130) == 0xff999999  # done: the window's own paint stands
    ui.now += 16
    page.frame(0xff999999)
    assert page.px(100, 130) == 0xff999999


def test_the_old_art_leaves_left_on_next_and_the_new_waits_right(fw, ui, st):
    reduce_motion(fw, 0)
    page = Page(fw, ui, st)
    page.lines[0].text = 'Song B'
    page.frame(0xff999999)
    ui.now += 100  # old nearly gone, at about -15 px; the new at about +14 px, faint
    page.frame(0xff999999)
    left, right = page.px(16 - 8, 130), page.px(16 + 150 - 4, 130)
    assert channel(left) > 0  # the old has moved into the margin on the left
    assert channel(right) < 0x99  # the new's right edge is not there yet


def test_the_same_lines_painted_again_start_nothing(fw, ui, st):
    reduce_motion(fw, 0)
    page = Page(fw, ui, st)
    ui.now += 50
    page.frame(0xff999999)
    assert page.px(100, 130) == 0xff999999


def test_off_the_art_page_a_new_song_just_shows(fw, ui, st):
    reduce_motion(fw, 0)
    page = Page(fw, ui, st)
    slide = ui.widget('slide_view', page.win, (0, 0, W, 186))
    slide.props[b'value'] = 1  # the lyrics page
    st('np_slide', C.c_void_p).value = slide.at
    page.lines[0].text = 'Song B'
    page.frame(0xff999999)
    assert page.px(100, 130) == 0xff999999
