"""One toast at a time (feedback.c toast_swap, through output_rule.c ringnav_nav_to; docs/oled-design.md#dialogs-and-toasts):
a toast asked for while one is up, the payload's or stock's, takes its place at once in the same dialog, with its words
and a fresh timer, and no second dialog opens over the first. The navigator is the device's: navigator_to_with_context
reaches ringnav_nav_to, whose stock half opens a window named for the page as stock's msginfo_dialog would."""
import ctypes as C
import struct

import pytest

from conftest import O
from test_nav_paint import World


@pytest.fixture
def world(fw):
    return World(fw)


class Toasts:
    def __init__(self, fw, world):
        self.fw, self.world, self.opened, self.quit = fw, world, [], []
        nav_to = fw.fn('ringnav_nav_to', C.c_int, C.c_void_p, C.c_void_p)
        fw.mock('navigator_to_with_context', lambda name, info, *_: nav_to(name, info))
        fw.mock('stock_nav_to_trampoline', self.open)
        fw.mock('dialog_quit', lambda w, code, *_: self.quit.append(w) or 0)
        self.toast = fw.fn('toast', None, C.c_char_p)
        self.toast_icon = fw.fn('toast_icon', None, C.c_char_p, C.c_char_p)

    def open(self, name, info, *_):
        """Stock's msginfo_dialog: a window over the page, its line, and its close timer in timer_idex. Not modal here:
        the call returns with the dialog up, as from inside its loop."""
        assert C.string_at(name) == b'dialog/msginfo_dialog'
        kind, ms = struct.unpack('<2i', C.string_at(info, 8))
        win = self.world.window('msginfo_dialog', 'dialog')
        self.world.add(win, None, 'label', text=C.string_at(info + 8).decode())
        self.world.timers.append((0, win, ms))  # stock's msginfo_dialog_on_timer
        self.world.set(win, 'timer_idex', len(self.world.timers))
        self.opened.append(win)
        return 0

    def up(self):
        """The toasts on screen: each msginfo_dialog's words."""
        return [self.world[k].text for w in self.world[self.world.wm].children if self.world[w].name == 'msginfo_dialog'
                for k in self.world[w].children]


def test_a_second_toast_takes_the_first_one_s_place_at_once(fw, world):
    world.window('settings_page')
    t = Toasts(fw, world)
    t.toast(b'Added to Favorites')
    (win,) = t.opened
    first = world[win].props['timer_idex']
    t.toast(b'Playing Next')
    assert t.opened == [win]  # no second dialog
    assert t.up() == ['Playing Next']  # the old words gone at once
    assert first in world.removed  # its timer stopped...
    fn, ctx, ms = world.timers[-1]
    assert (ctx, ms) == (win, 1500) and world[win].props['timer_idex'] == len(world.timers)  # ... and a new one armed
    assert fn == C.cast(fw.lib.feedback__toast_due, C.c_void_p).value
    assert world.fire(len(world.timers)) == 7 and t.quit == [win]  # which closes it as stock's own would


def test_stock_s_toast_over_ours_takes_its_place_and_its_glyph(fw, world):
    """A toast stock opens itself (a song it can't play, msg_noheadset) passes the same door: no stacking, and the
    replaced toast's glyph goes with it."""
    world.window('settings_page')
    t = Toasts(fw, world)
    t.toast_icon(b'oled_usb_audio', b'USB audio')
    info = C.create_string_buffer(0x408)
    struct.pack_into('<2i', info, 0, 1, 2000)
    info[8:8 + 13] = b'Not supported'
    fw.fn('navigator_to_with_context', C.c_int, C.c_char_p, C.c_void_p)(b'dialog/msginfo_dialog', info)
    assert len(t.opened) == 1 and t.up() == ['Not supported']
    assert world.timers[-1][2] == 2000
    assert C.string_at(C.addressof(fw.var('feedback__pill_glyph', C.c_char))) == b''


def test_with_no_toast_up_one_opens(fw, world):
    world.window('settings_page')
    t = Toasts(fw, world)
    t.toast(b'Saved')
    assert len(t.opened) == 1 and t.up() == ['Saved']


def test_a_toast_ended_by_its_number_goes_only_while_it_is_the_one_up(fw, world):
    """toast_end (Find My Vamp's): its own toast down at once, its timer stopped; one that another toast replaced
    stays."""
    world.window('settings_page')
    t = Toasts(fw, world)
    nxt, end = fw.fn('toast_next', C.c_uint), fw.fn('toast_end', None, C.c_uint)
    n = nxt()
    t.toast(b'Find My Vamp')
    (win,) = t.opened
    end(n)
    assert t.quit == [win] and world[win].props['timer_idex'] == -1
    n = nxt()
    t.toast(b'Find My Vamp')
    t.toast(b'Pixel 10 Pro paired')
    end(n)
    assert t.quit == [win]  # the newer toast stays


def test_another_page_opens_as_before(fw, world):
    t = Toasts(fw, world)
    fw.mock('stock_nav_to_trampoline', lambda name, *_: t.opened.append(C.string_at(name)) or 0)
    assert fw.fn('ringnav_nav_to', C.c_int, C.c_char_p, C.c_void_p)(b'settings_page', 0) == 0
    assert t.opened == [b'settings_page']
    assert O['TIMER_CTX'] == 0x20
