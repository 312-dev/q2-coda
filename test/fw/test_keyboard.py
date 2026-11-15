"""keyboard.c: the on-screen keyboard (stock's kb_default_t9) walked by the wheel under the OLED theme
(list.entry.ipod): the wheel lights and moves along the keys of the page showing, Centre presses the lit key as a
tap does, every other key goes on, and stock's grey fills are cleared so the keyboard sits on black."""
import ctypes as C

import pytest

from conftest import O
from test_nav_library_pages import Config, Ui

KEY_RETURN, KEY_CENTER, KEY_PREV, KEY_NEXT, KEY_PLAY = (
    O[k] for k in ('KEY_RETURN', 'KEY_CENTER', 'KEY_PREV', 'KEY_NEXT', 'KEY_PLAY'))
EVT_CLICK, IM_KEYBOARD = O['EVT_CLICK'], 0x0c  # offsets.inc's IM_KEYBOARD, a define O can't read (two lines)
OLED = b'3'


@pytest.fixture
def ui(fw):
    return Ui(fw)


class Keyboard:
    """A keyboard window as stock's: candidates, then pages whose active child holds rows of buttons; the input
    method names it as its open keyboard."""
    def __init__(self, fw, ui, theme=OLED, rows=(('123', 'abc', 'def'), ('ABC', 'ghi'))):
        self.fw, self.ui = fw, ui
        Config(fw, **({} if theme is None else {'THEME': theme}))
        self.kb = ui.new('window_create', 0, 0, 60, 375, 260)
        self.cand = ui.new('candidates', self.kb, 0, 0, 375, 40)
        pages = ui.new('pages', self.kb, 0, 40, 375, 221)
        self.pages = pages
        self.keys = []
        for p in range(2):
            page = ui.new('view', pages, 0, 0, 375, 221)
            keys = []
            for r, row in enumerate(rows):
                line = ui.new('view', page, 0, 50 * r, 375, 48)
                for c, text in enumerate(row):
                    b = ui.new('button', line, 70 * c, 0, 66, 48)
                    ui.name[b] = ('opt:' if p == 0 else 'key:') + text
                    keys.append(b)
            self.keys.append(keys)
        self.im = C.create_string_buffer(0x40)
        C.c_void_p.from_address(C.addressof(self.im) + IM_KEYBOARD).value = self.kb
        fw.mock('input_method', lambda *_: C.addressof(self.im))
        self.take = fw.fn('kb_wheel_key', C.c_int, C.c_uint)
        self.kw = fw.var('keyboard__kw', C.c_void_p * 3)

    def key(self, *keys):
        return [self.take(k) for k in keys]

    def lit(self):
        return self.kw[1]

    def clicked(self):
        return [a[2] for _, a in self.fw.calls('pointer_event_init') if a[1] == EVT_CLICK]


def test_the_first_turn_lights_the_first_key(fw, ui):
    k = Keyboard(fw, ui)
    assert not k.lit()
    assert k.key(KEY_NEXT) == [1] and k.lit() == k.keys[0][0]


def test_the_wheel_walks_the_keys_in_order_and_stops_at_the_ends(fw, ui):
    k = Keyboard(fw, ui)
    k.key(KEY_NEXT, KEY_NEXT, KEY_NEXT)
    assert k.lit() == k.keys[0][2]
    k.key(*[KEY_NEXT] * 9)
    assert k.lit() == k.keys[0][-1]
    k.key(*[KEY_PREV] * 9)
    assert k.lit() == k.keys[0][0]


def test_centre_presses_the_lit_key_as_a_tap_does(fw, ui):
    k = Keyboard(fw, ui)
    k.key(KEY_CENTER)  # the first press only lights a key: nothing was chosen yet
    assert not k.clicked() and k.lit() == k.keys[0][0]
    k.key(KEY_NEXT, KEY_CENTER)
    assert k.clicked() == [k.keys[0][1]]
    assert [a[0] for _, a in fw.calls('stock_dispatch_trampoline')] == [k.keys[0][1]]


def test_the_walk_follows_the_page_showing(fw, ui):
    k = Keyboard(fw, ui)
    k.key(KEY_NEXT, KEY_NEXT)
    ui.props[(k.pages, 'active')] = 1  # a page key showed the capitals
    k.key(KEY_NEXT)
    assert k.lit() == k.keys[1][2]  # the same place on the page now showing


def test_candidates_come_first(fw, ui):
    k = Keyboard(fw, ui)
    word = ui.new('button', k.cand, 0, 0, 80, 40)
    k.key(KEY_NEXT)
    assert k.lit() == word
    ui.hidden.add(k.cand)
    k.key(KEY_NEXT)
    assert k.lit() == k.keys[0][1]


@pytest.mark.parametrize('key', [KEY_RETURN, KEY_PLAY])
def test_return_and_play_go_on(fw, ui, key):
    k = Keyboard(fw, ui)
    assert k.key(key) == [0]


def test_with_no_keyboard_up_nothing_is_taken(fw, ui):
    k = Keyboard(fw, ui)
    C.c_void_p.from_address(C.addressof(k.im) + IM_KEYBOARD).value = 0
    assert k.key(KEY_NEXT, KEY_CENTER) == [0, 0]


def test_other_themes_keep_stock_s_keyboard(fw, ui):
    k = Keyboard(fw, ui, theme=None)
    assert k.key(KEY_NEXT, KEY_CENTER) == [0, 0] and not k.lit()


def test_stock_s_fills_are_cleared_once_per_keyboard(fw, ui):
    k = Keyboard(fw, ui)
    k.key(KEY_NEXT)
    key = k.keys[0][1]
    assert ui.props[(key, 'style:normal:bg_color')] == 0 and ui.props[(k.kb, 'style:normal:bg_color')] == 0
    assert ui.props[(key, 'style:pressed:bg_color')] == 0


def test_a_key_the_keyboard_takes_frees_stock_s_click_latch(fw, ui):
    """As the strip's (test_search_strip.py): stock's key-up, which clears the latch, never runs for these."""
    k = Keyboard(fw, ui)
    latch = C.c_byte.from_address(O['KEY_TONE_LATCH'])
    for key in (KEY_NEXT, KEY_NEXT, KEY_CENTER, KEY_PREV):
        latch.value = 1
        assert k.key(key) == [1] and latch.value == 0
