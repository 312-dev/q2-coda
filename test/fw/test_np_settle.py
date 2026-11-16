"""np_settle.c: Now Playing's art shrinks to 88% about its center when the song pauses and grows back when it plays
(docs/oled-design.md#motion), through the motion helper's ovm_t; under Reduce Motion it keeps its size."""
import ctypes as C

import pytest

from test_nav_lists import Ui, ui  # noqa: F401  (ui: fixture)
from test_nav_painters import Canvas, scratch, st  # noqa: F401  (fixtures)
from test_ui_kit import reduce_motion

PLAYING, PAUSED = 2, 3
ART = 150


class Np:
    """A Now Playing page's art (img_cover, 150 px) and the player's state the UI loop's pass reads."""
    def __init__(self, fw, ui, st, status=PLAYING):
        self.fw, self.ui, self.status = fw, ui, status
        self.cover = ui.widget('image', box=(16, 20, ART, ART), name='img_cover')
        self.cover.props[b'image'] = b'play_defaultcover'
        st('np_cover', C.c_void_p).value = self.cover.at
        fw.mock('mclGetPlayStatus', lambda *_: self.status)
        self.poll()

    def poll(self, ms=0):
        self.ui.now += ms
        self.fw.fn('np_settle_poll', None)()

    def scale(self):
        return self.fw.fn('np_settle_scale', C.c_int)()


@pytest.mark.parametrize('reduced', [0, 1])
def test_a_pause_shrinks_the_art_to_88_percent_over_280_ms_and_play_grows_it_back_over_320(fw, ui, st, reduced):
    reduce_motion(fw, reduced)
    np = Np(fw, ui, st)
    assert np.scale() == 1000
    np.status = PAUSED
    np.poll()
    np.poll(140)
    halfway = np.scale()
    np.poll(140)
    if reduced:  # the glyph is the cue: the art keeps its size
        assert halfway == np.scale() == 1000
        return
    assert 880 < halfway < 1000 - (1000 - 880) // 2  # cubic_out: more than half the way at half the time
    assert np.scale() == 880
    np.status = PLAYING
    np.poll()
    np.poll(240)
    assert np.scale() < 1000
    np.poll(80)
    assert np.scale() == 1000


def test_a_play_mid_shrink_turns_back_from_where_the_art_is(fw, ui, st):
    reduce_motion(fw, 0)
    np = Np(fw, ui, st)
    np.status = PAUSED
    np.poll()
    np.poll(100)
    there = np.scale()
    np.status = PLAYING
    np.poll()
    assert np.scale() == there  # no jump
    np.poll(320)
    assert np.scale() == 1000


def test_a_page_that_opens_on_a_paused_song_shows_the_art_small_at_once(fw, ui, st):
    reduce_motion(fw, 0)
    np = Np(fw, ui, st, status=PAUSED)
    assert np.scale() == 880
    other = ui.widget('image', box=(16, 20, ART, ART), name='img_cover')  # another page, playing
    st('np_cover', C.c_void_p).value = other.at
    np.status = PLAYING
    np.poll()
    assert np.scale() == 1000


def test_smaller_art_is_drawn_by_the_payload_into_its_centered_box_and_stock_paints_none(fw, ui, st):
    """np_settle_begin empties the clip before stock paints img_cover (its black box and image); np_settle_paint puts
    it back and draws the whole cover into the smaller box, centered, as stock's scale_auto draws it whole."""
    reduce_motion(fw, 0)
    np = Np(fw, ui, st, status=PAUSED)
    canvas = Canvas(fw)
    canvas.art['play_defaultcover'] = (300, 300)
    begin = fw.fn('np_settle_begin', None, C.c_void_p, C.c_void_p)
    paint = fw.fn('np_settle_paint', None, C.c_void_p, C.c_void_p)
    begin(np.cover.at, canvas.addr)
    assert canvas.clip[2:] == [0, 0]
    paint(np.cover.at, canvas.addr)
    side = ART * 880 // 1000
    assert canvas.clip == [0, 0, 375, 320] and canvas.images == [((ART - side) // 2, (ART - side) // 2, side, side)]
    box = (C.c_int * 4)()
    assert fw.fn('np_settle_box', C.c_int, C.c_void_p, C.c_void_p)(np.cover.at, box) == 1
    assert list(box) == [(ART - side) // 2, (ART - side) // 2, side, side]  # the dim and the corners follow it


def test_whole_art_is_left_to_stock(fw, ui, st):
    reduce_motion(fw, 0)
    np = Np(fw, ui, st)
    canvas = Canvas(fw)
    canvas.art['play_defaultcover'] = (300, 300)
    fw.fn('np_settle_begin', None, C.c_void_p, C.c_void_p)(np.cover.at, canvas.addr)
    fw.fn('np_settle_paint', None, C.c_void_p, C.c_void_p)(np.cover.at, canvas.addr)
    assert canvas.clip == [0, 0, 375, 320] and canvas.images == []


def test_a_cover_wider_than_tall_keeps_its_shape_centered_in_the_box(fw, ui, st):
    reduce_motion(fw, 0)
    np = Np(fw, ui, st, status=PAUSED)
    canvas = Canvas(fw)
    canvas.art['play_defaultcover'] = (400, 200)
    fw.fn('np_settle_begin', None, C.c_void_p, C.c_void_p)(np.cover.at, canvas.addr)
    fw.fn('np_settle_paint', None, C.c_void_p, C.c_void_p)(np.cover.at, canvas.addr)
    side = ART * 880 // 1000
    (x, y, w, h), = canvas.images
    assert (w, h) == (side, side // 2) and x == (ART - side) // 2 and y == (ART - side) // 2 + (side - h) // 2
