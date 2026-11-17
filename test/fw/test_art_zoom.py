"""art_zoom.c: Now Playing opened from a page that shows the playing cover (Home's art pane, an album page's header)
grows its art from that cover while the page fades out and Now Playing fades in; Return shrinks it back. Opened from
anywhere else it opens at once (docs/oled-design.md#motion)."""
import ctypes as C

import pytest

from test_nav_lists import ui  # noqa: F401  (ui: fixture)
from test_nav_painters import scratch, st  # noqa: F401  (fixtures)
from test_ui_kit import reduce_motion


class Rect(C.Structure):
    _fields_ = [('x', C.c_int), ('y', C.c_int), ('w', C.c_int), ('h', C.c_int)]


def at(fw, k, a, b):
    page, np, art = C.c_uint(), C.c_uint(), Rect()
    fw.fn('art_zoom_at', None, C.c_int, C.c_void_p, C.c_void_p, C.c_void_p, C.c_void_p, C.c_void_p)(
        k, C.addressof(a), C.addressof(b), C.addressof(page), C.addressof(np), C.addressof(art))
    return page.value, np.value, (art.x, art.y, art.w, art.h)


def test_the_page_leaves_over_the_first_half_now_playing_comes_from_40_percent_and_the_art_flies_between(fw):
    a, b = Rect(200, 0, 175, 280), Rect(16, 64, 150, 150)
    assert at(fw, 0, a, b) == (255, 0, (200, 0, 175, 280))
    page, np, art = at(fw, 450, a, b)
    def lerp(u, v):
        return u + int((v - u) * 450 / 1000)  # C's division: toward zero
    assert 0 < page < 255 and 0 < np < 64 and art == (lerp(200, 16), lerp(0, 64), lerp(175, 150), lerp(280, 150))
    assert at(fw, 1000, a, b) == (0, 255, (16, 64, 150, 150))


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


@pytest.mark.parametrize('reduced', [0, 1])
def test_now_playing_opened_over_an_album_page_takes_the_zoom_both_ways(fw, ui, reduced):
    reduce_motion(fw, reduced)
    h = Hints(fw, ui)
    album = ui.widget('window', ui.wm, (0, 40, 375, 280), name='albuminfo_page')
    album.props[b'_lb_page'] = 1  # dressed as the design's album page (library_album.c)
    h.load(fw)
    want = b'fade(duration=150)' if reduced else b'art_zoom(duration=300)'
    assert h.np.props[b'open_anim_hint'] == want
    assert h.np.props[b'close_anim_hint'] == (b'fade(duration=150)' if reduced else b'art_zoom(duration=240)')
    assert h.registered == ([] if reduced else [b'art_zoom'])


def test_now_playing_opened_from_a_list_opens_at_once(fw, ui):
    reduce_motion(fw, 0)
    h = Hints(fw, ui)
    ui.widget('window', ui.wm, (0, 40, 375, 280), name='allmusic_page')
    h.load(fw)
    assert b'open_anim_hint' not in h.np.props and h.registered == []  # nothing set: stock's immediate open
