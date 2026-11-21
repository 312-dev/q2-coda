"""radio_pane.c (docs/oled-design.md#homes-art-pane-while-radio-plays): Home's art pane while Radio is Now Playing, K1 as approved on 2026-11-20
(~/q2-emu/design/radio-tuner/radio-now-playing/pane-v6/mock.py has every number): the dark red ground with its glow
and left edge, the glossy badge, the vignette under the frequency, the frequency alone in words, and the rings, which
move only while Radio plays; and coverflow.c's pane, which shows it while Radio owns Now Playing, ticks only while it
moves and shows, and goes back to the covers when the Library takes over."""
import ctypes as C

import host

from test_games import Canvas, texts
from test_radio_tuner import Rt

W, H = 184, 320


class Words(C.Structure):
    _fields_ = [(k, C.c_int) for k in 'off badge_a badge_dy freq_a freq_dy cx cy'.split()]


class Pane:
    """Radio on 93.1 (playing unless told), the clock the test's, and a pane-sized RGBA8888 buffer."""
    def __init__(self, fw, playing=1):
        self.fw, self.now = fw, 100000
        self.rt = Rt()
        self.rt.playing, self.rt.audio, self.rt.freq, self.rt.feed = playing, 999, 9310, -1
        fw.var('radio_tuner__rt', C.c_void_p).value = C.addressof(self.rt)
        fw.mock('time_now_ms', lambda *_: self.now)
        self.buf = (C.c_uint * (W * H))()
        self.words = Words()
        self.frame_fn = fw.fn('rp_frame', C.c_int, C.c_void_p, C.c_int, C.c_int, C.c_int, C.c_int, C.c_uint, C.c_void_p)

    def frame(self, at=None):
        assert self.frame_fn(self.buf, W, W, H, 0, self.now if at is None else at, C.byref(self.words)) == 1
        return list(self.buf)

    def px(self, x, y):
        v = self.buf[y * W + x]
        return v & 255, v >> 8 & 255, v >> 16 & 255

    def done(self):
        self.fw.fn('rp_free', None)()
        self.fw.var('radio_tuner__rt', C.c_void_p).value = None


def test_the_k1_composition(fw):
    p = Pane(fw)
    try:
        p.frame()
        assert p.px(183, 0) == (20, 3, 7)  # the ground at its top, past the glow's 150 px
        assert p.px(0, 0) == (50, 10, 18)  # the faint lighter line down its left edge (+30, 7, 11)
        r, g, b = p.px(183, 319)
        assert (r, g, b) == (13, 2, 5)  # deepened 35% to the bottom
        r, g, b = p.px(92, 138)  # the badge's centre: its gradient's middle, red over everything
        assert r > 150 and g < 70 and b < 90
        assert p.px(92, 205)[0] < p.px(40, 205)[0]  # the vignette darkens under the frequency
        assert (p.words.cx, p.words.cy, p.words.off, p.words.badge_a, p.words.freq_a) == (92, 138, 0, 255, 255)
    finally:
        p.done()


def test_the_words_are_the_frequency_alone(fw):
    p = Pane(fw)
    try:
        p.frame()
        canvas = Canvas(fw)
        fw.fn('rp_words', None, C.c_void_p, C.c_void_p, C.c_void_p)(0x5000, canvas.p, C.byref(p.words))
        said = ''.join(texts(canvas.ops))
        assert '93.1' in said and 'FM' in said and said.replace('93.1', '').replace('FM', '').strip() == ''
    finally:
        p.done()


def test_the_rings_move_while_radio_plays_and_stand_while_it_is_paused(fw):
    p = Pane(fw)
    try:
        a, b = p.frame(100000), p.frame(100600)
        assert a != b and fw.fn('rp_moving', C.c_int)() == 1  # a ring 0.6 s further out
        p.rt.playing = 0
        a, b = p.frame(101000), p.frame(105000)
        assert a == b and fw.fn('rp_moving', C.c_int)() == 0
        p.rt.playing = 1
        c = p.frame(105000)
        assert c == a  # moving again from where they stood
    finally:
        p.done()


def test_under_reduce_motion_the_rings_stand_still(fw, library):
    offs = host.offsets(library.parent/'navigation.o', 'scratch_t')
    fw.fn('motion_reduced', C.c_int)()  # its config read once, which would set the field back
    C.c_int.from_address(C.addressof(fw.var('st', C.c_char)) + offs['reduce_motion']).value = 1
    p = Pane(fw)
    try:
        assert p.frame(100000) == p.frame(103000) and fw.fn('rp_moving', C.c_int)() == 0
    finally:
        p.done()


def test_the_way_in_slides_over_the_cover(fw):
    """The cover recedes while the pane comes in from the right on its spring; the badge and the frequency rise and
    fade in after it; it is over by 1.2 s."""
    p = Pane(fw)
    try:
        cover = (C.c_uint * (W * H))(*([0xff00ff00] * (W * H)))  # a green cover
        fw.fn('rp_enter', None, C.c_void_p, C.c_int, C.c_int)(cover, W, H)
        p.frame(p.now + 50)
        assert p.words.off > 100 and p.words.badge_a == 0 and p.px(10, 160)[1] > 100  # mostly the cover, dimming
        p.frame(p.now + 340)
        assert p.words.off < 30 and 0 < p.words.badge_a < 255 and p.words.badge_dy > 0
        p.frame(p.now + 1300)
        assert (p.words.off, p.words.badge_a, p.words.freq_a, p.words.badge_dy) == (0, 255, 255, 0)
        assert p.px(10, 160)[1] < 20  # the cover gone
    finally:
        p.done()
