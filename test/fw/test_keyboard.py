"""keyboard.c: stock's on-screen keyboard gone. The input method's two window_open calls (tools/build.py KB_OPEN_SITES)
open kb_host_open's bare keyboard window instead, with no keys, which the letter strip takes before kb_host_open
returns, so its first frame is the strip's; the input method's slide up is never set (kb_open_hint); and the window's
close lets the strip go."""
import ctypes as C

import pytest

from conftest import O
from test_nav_library_pages import Config, Ui
from test_search_strip import Ss

IM_KEYBOARD = 0x0c  # offsets.inc's IM_KEYBOARD, a define O can't read (two lines)


@pytest.fixture
def ui(fw):
    return Ui(fw)


class Host:
    """The input method with an edit to type for; open() is its window_open, as KB_OPEN_SITES make it."""
    def __init__(self, fw, ui):
        self.fw, self.ui = fw, ui
        Config(fw)
        self.made = []
        fw.mock('window_base_create', self.create)
        self.im = C.create_string_buffer(0x40)
        self.edit = ui.new('edit', 0, 0, 0, 200, 40)
        C.c_void_p.from_address(C.addressof(self.im) + O['IM_WIDGET']).value = self.edit
        fw.mock('input_method', lambda *_: C.addressof(self.im))
        self.ss = fw.var('search_strip__ss', Ss)

    def create(self, parent, vt, x, y, w, h):
        kb = self.ui.new('window_create', parent or 0, x, y, w, h)
        self.made.append((parent, vt, x, y, w, h, kb))
        return kb

    def open(self, name=b'kb_default_t9'):
        kb = self.fw.fn('kb_host_open', C.c_void_p, C.c_char_p)(name)
        C.c_void_p.from_address(C.addressof(self.im) + IM_KEYBOARD).value = kb  # the input method keeps it
        return kb

    def gone(self, kb):
        """The box closes: the window's EVT_DESTROY handlers run (stock frees it)."""
        for h in [h for h in self.ui.handlers if h[0] == kb and h[1] == O['EVT_DESTROY']]:
            self.ui.handlers.remove(h)
            C.CFUNCTYPE(C.c_int, C.c_void_p, C.c_void_p)(h[2])(h[3], None)


def test_the_input_method_s_keyboard_is_a_bare_keyboard_window_under_the_status_row(fw, ui):
    h = Host(fw, ui)
    kb = h.open()
    bar = 320 - fw.fn('client_h', C.c_int)()
    (parent, vt, x, y, w, hh, made), = h.made
    assert made == kb and not parent and vt == C.addressof(fw.var('fw_ctx_g_keyboard_vtable', C.c_ubyte * 172))  # a keyboard, as stock's was
    assert (x, y, w, hh) == (0, bar, 375, 320 - bar)
    assert ui.name.get(kb) == 'kb_default_t9'  # named as stock's
    assert h.ss.sheet in ui.kids[kb] and not [w for w in ui.subtree(kb) if ui.kind[w] == 'button']  # the strip's: no keys
    assert not fw.calls('window_create')  # the keyboard window alone, and no asset of stock's (the payload calls none)


def test_the_strip_has_the_keyboard_before_the_input_method_sees_it_and_no_slide_is_set(fw, ui):
    h = Host(fw, ui)
    kb = h.open()
    assert h.ss.page == kb and h.ss.box == h.edit
    bar = 320 - fw.fn('client_h', C.c_int)()
    assert (kb, 0, bar, 375, 320 - bar) in [tuple(a[:5]) for _, a in fw.calls('widget_move_resize')]
    hint = fw.fn('kb_open_hint', C.c_int, C.c_void_p, C.c_char_p, C.c_char_p)
    assert hint(kb, b'open_anim_hint', b'popup') == 0 and not fw.calls('widget_set_prop_str')


def test_a_keyboard_opened_again_at_the_same_address_is_the_strip_s_again(fw, ui):
    """A box opened a second time (Add Network's password, 2026-11-17): stock's input method frees the keyboard as it
    closes and the next is often at the same address; that one is the strip's too, and the strip lets the first go."""
    h = Host(fw, ui)
    for _ in range(2):
        kb = h.open()
        assert h.ss.page == kb and h.ss.box == h.edit
        h.gone(kb)
        assert not h.ss.page and not h.ss.box  # the strip let the closed window go


def test_only_the_keyboard_window_s_ground_is_painted_black(fw, ui):
    from test_nav_painters import Canvas
    h = Host(fw, ui)
    kb = h.open()
    bg = fw.fn('kb_host_bg', C.c_int, C.c_void_p, C.c_void_p)
    c = Canvas(fw)
    assert bg(kb, c.addr) == 1 and [r for r, *_ in c.boxes] == [(0, 0, 375, C.c_int.from_address(kb + O['W_H']).value)]
    other = ui.new('window_create', 0, 0, 0, 375, 320)
    assert bg(other, Canvas(fw).addr) == 0
