"""patch/keylock.c: Buttons Lock as the iPod's Hold switch under OLED (docs/internals.md#buttons-lock): a press the
lock holds in the dark lights the screen with the card, and the whole press goes; Centre on the card is the wake; the
status row's padlock follows the lock."""
import ctypes as C
import struct

import pytest

import host
from conftest import O

THEMES = host.tools_theme.builtins()
OLED, MINIMAL = (next(t['index'] for t in THEMES if t['id'] == i) for i in ('oled', 'minimal'))
DOWN, UP, LONG = O['EVT_KEY_DOWN'], O['EVT_KEY_UP'], O['EVT_KEY_LONG']
KEY_CENTER, KEY_PLAY, KEY_RETURN, KEY_PREV, KEY_NEXT, KEY_BACK_BTN, KEY_FWD_BTN = (
    O[k] for k in ('KEY_CENTER', 'KEY_PLAY', 'KEY_RETURN', 'KEY_PREV', 'KEY_NEXT', 'KEY_BACK_BTN', 'KEY_FWD_BTN'))
POINTER_DOWN = O['EVT_POINTER_FIRST']
TIMER = C.CFUNCTYPE(C.c_int, C.c_void_p)


def event(kind, key=0):
    e = C.create_string_buffer(0x40)
    struct.pack_into('<i', e, O['EVENT_TYPE'], kind)
    struct.pack_into('<i', e, O['EVENT_KEY'], key)
    return e


class Hold:
    """keylock_gate with the lock set, the screen dark and the theme chosen; the card's timer kept to run."""
    def __init__(self, fw, theme=OLED, mode=3, flag=1, lit=0):
        def config(path, section, key, out, default, *_):
            value = b'%d' % theme if C.string_at(key) == b'THEME' else C.string_at(default)
            C.memmove(out, value + b'\0', len(value) + 1)
        fw.mock('toolsReadConfig', config)
        self.timers = {}
        def add(fn, ctx, ms, *_):
            self.timers[len(self.timers) + 1] = (fn, ms)
            return len(self.timers)
        fw.mock('timer_add', add)
        self.fw = fw
        self.lit = fw.var('g_backlight_status', C.c_ubyte)
        self.lit.value = lit
        fw.var('g_keylock_flag', C.c_ubyte).value = flag
        fw.var('g_keylock_mode', C.c_ubyte).value = mode
        self.gate = fw.fn('keylock_gate', C.c_int, C.c_void_p, C.c_int)

    def __call__(self, kind, key=0, remote=0):
        return self.gate(event(kind, key), remote)

    def press(self, key):
        return [self(kind, key) for kind in (DOWN, LONG, UP)]

    def expire(self):
        """The last card timer armed runs, as the UI loop runs it once its time is up."""
        fn, ms = self.timers[max(self.timers)]
        assert ms == 1500
        return TIMER(fn)(None)

    def screen(self):
        return [a[0] for _, a in self.fw.calls('screen_action')]


@pytest.mark.parametrize('mode, key, held', [
    (3, KEY_PLAY, 1), (3, KEY_NEXT, 1), (3, KEY_FWD_BTN, 1), (3, KEY_BACK_BTN, 1),
    (2, KEY_PLAY, 1), (2, KEY_PREV, 0), (2, KEY_FWD_BTN, 1),
    (1, KEY_NEXT, 1), (1, KEY_PLAY, 0), (1, KEY_BACK_BTN, 0),
    (3, KEY_CENTER, 0)])
def test_a_press_the_lock_holds_in_the_dark_lights_the_card_and_goes_whole(fw, mode, key, held):
    """Stock's dark filter's sets: 1 the wheel, 2 Return, Play/Pause and the buttons, 3 both; Centre never."""
    h = Hold(fw, mode=mode)
    assert h.press(key) == [held] * 3
    assert h.lit.value == held and h.screen() == [1] * held and len(fw.calls('enable_fb')) == held


def test_the_card_ends_with_the_screen_off_as_stocks_timeout_turns_it_off(fw):
    h = Hold(fw)
    h.press(KEY_PLAY)
    assert h.expire() == 0 and h.lit.value == 0 and h.screen() == [1, 0]
    assert h.press(KEY_PLAY) == [1] * 3 and h.screen() == [1, 0, 1]  # the next held press shows it again


def test_centre_on_the_card_is_the_wake_and_the_buttons_then_work(fw):
    h = Hold(fw)
    h.press(KEY_PLAY)
    assert h.press(KEY_NEXT) == [1] * 3 and len(h.timers) == 2  # another held press: its time starts again
    assert h.press(KEY_CENTER) == [1] * 3  # the wake, its release too
    assert h.lit.value == 1 and h.screen() == [1] and fw.calls('reset_poweroptions_timer')
    assert [a[0] for _, a in fw.calls('timer_remove')][-1] == 2  # the card's end goes
    assert h.press(KEY_PLAY) == [0] * 3 and h(DOWN, KEY_CENTER) == 0  # lit: the lock is the dark screen's


def test_a_touch_on_the_card_reaches_nothing(fw):
    h = Hold(fw)
    assert h(POINTER_DOWN) == 0
    h.press(KEY_PLAY)
    assert h(POINTER_DOWN) == 1


@pytest.mark.parametrize('case', ['minimal', 'unlocked', 'lit', 'remote'])
def test_the_gate_leaves_alone(fw, case):
    """Another theme's held press stays stock's, dropped by its filter; no lock, a lit screen or SyncLink's post."""
    h = Hold(fw, theme=MINIMAL if case == 'minimal' else OLED, flag=case != 'unlocked', lit=case == 'lit')
    assert [h(k, KEY_PLAY, remote=case == 'remote') for k in (DOWN, LONG, UP)] == [0] * 3
    assert not fw.calls('screen_action') and h.lit.value == (case == 'lit')


def test_the_padlock_follows_the_lock_in_the_bar_themes_ink(fw):
    Hold(fw, flag=1, lit=1)
    lock, shown, image, empty = C.create_string_buffer(0x100), [0], [], C.create_string_buffer(b'')
    fw.mock('widget_lookup', lambda w, name, *_: C.addressof(lock) if C.string_at(name) == b'img_keylock' else 0)
    fw.mock('widget_get_visible', lambda *_: shown[0])
    fw.mock('widget_set_visible', lambda w, v, *_: shown.__setitem__(0, v))
    fw.mock('widget_get_prop_str', lambda *_: C.addressof(empty))
    fw.mock('image_base_set_image', lambda w, name, *_: image.append(C.string_at(name)))
    bar = fw.fn('keylock_bar', C.c_int, C.c_void_p)
    room = fw.fn('keylock_room', C.c_int)
    assert bar(C.addressof(lock)) == 1 and shown == [1] and image == [b'oled_keylock_d'] and room() == O['STATUS_ROW_LOCK_ROOM']
    fw.var('g_keylock_flag', C.c_ubyte).value = 0
    assert bar(C.addressof(lock)) == 1 and shown == [0] and room() == 0
