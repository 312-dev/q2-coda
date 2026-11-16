"""patch/charging.c: the status battery's tap and its toast, the cable's own toasts, the plug-in screen and sound."""
import ctypes as C

import pytest

from conftest import O
from test_nav_playing import Bar, Ui, scratch, st, theme  # noqa: F401 (fixtures)

KEY_LOW, KEY_CHARGING, KEY_PLUGGED, KEY_WEAK = 1, 2, 4, 8  # navigation.h BATT_KEY_*
TOUCH_DOWN, TOUCH_UP = 0xff, 0x103


@pytest.fixture
def ui(fw):
    return Ui(fw)


def words(fw, key):
    out = C.create_string_buffer(48)
    glyph = fw.fn('charging__charge_words', C.c_char_p, C.c_uint, C.c_char_p, C.c_int)(key, out, 48)
    return out.value.decode(), glyph.decode()


@pytest.mark.parametrize('key, held, text, glyph', [
    (62 << 4 | KEY_CHARGING, 0, 'Charging · 62%', 'chargetoast_charging'),
    (41 << 4 | KEY_WEAK, 0, 'Charger too weak · 41%', 'chargetoast_weak'),
    (80 << 4 | KEY_PLUGGED, 1, f"Charge Limit Reached ({O['CHARGE_STOP']}%)", 'chargetoast_full'),
    (100 << 4 | KEY_PLUGGED, 0, 'Fully charged', 'chargetoast_full'),
    (62 << 4, 0, 'Battery 62%', 'chargetoast_battery_d'),
    (20 << 4, 0, 'Battery 20%', 'chargetoast_battery_d'),
    (19 << 4 | KEY_LOW, 0, 'Low battery · 19%', 'chargetoast_low'),
    (12 << 4, 0, 'Low battery · 12%', 'chargetoast_low')])
def test_the_battery_says_its_state_in_words(fw, st, ui, key, held, text, glyph):
    """Low is under BATT_LOW_PCT (20), where the fill turns red."""
    theme(fw, st)
    st.charge_held = held
    assert words(fw, key) == (text, glyph)


class Tap:
    """The status bar's battery synced from stock's label and icon, the screen lit, toasts caught."""
    def __init__(self, fw, st, ui, text='62%', icon=b'bar_battery'):
        theme(fw, st, BATTERY=0)
        self.fw, self.toasts = fw, []
        fw.var('g_backlight_status', C.c_ubyte).value = 1
        self.bar = Bar(fw, ui)
        self.bar.level(text, icon)
        self.bar.sync()
        fw.mock('navigator_to_with_context', lambda page, info, *_: self.toasts.append(C.string_at(info + 8).decode()) or 0)
        # widget_to_local's stub leaves the point as it is: touches are in the slot's own pixels
        self.touch = fw.fn('charge_touch', None, C.c_uint, C.c_int, C.c_int)
        self.poll = fw.fn('charging_poll', None)

    def __call__(self, x0, y0, x1=None, y1=None):
        self.touch(TOUCH_DOWN, x0, y0)
        self.touch(TOUCH_UP, x0 if x1 is None else x1, y0 if y1 is None else y1)
        self.poll()
        return self.toasts


def test_a_tap_on_the_battery_toasts_its_state_on_the_next_pass(fw, st, ui):
    tap = Tap(fw, st, ui)
    tap.touch(TOUCH_DOWN, 12, 15)
    tap.touch(TOUCH_UP, 12, 15)
    assert tap.toasts == []  # not inside the input's dispatch
    tap.poll()
    assert tap.toasts == ['Battery 62%']
    tap.poll()
    assert tap.toasts == ['Battery 62%']  # once


@pytest.mark.parametrize('x, y, shown', [(12, 15, 1), (12 - 22, 15 - 20, 1), (12 + 21, 15 + 19, 1),
                                         (12 - 23, 15, 0), (12 + 22, 15, 0), (12, 15 - 21, 0), (12, 15 + 20, 0)])
def test_the_target_is_44_by_40_round_the_body(fw, st, ui, x, y, shown):
    """The body is OLED's batt_w wide (25 px): its centre 12 in from the slot's left, 15 down its 30 px row."""
    assert len(Tap(fw, st, ui)(x, y)) == shown


def test_a_drag_or_a_dark_screen_toasts_nothing(fw, st, ui):
    tap = Tap(fw, st, ui)
    assert tap(12, 15, 12, 15 + 13) == []  # a pull down from the row: Control Center's
    tap.fw.var('g_backlight_status', C.c_ubyte).value = 0
    assert tap(12, 15) == []


def test_charging_toasts_the_level_too(fw, st, ui):
    from test_usb_charge import Charging
    Charging(fw, pref=1, mode=1, cable=1).config()
    fw.var('fw_ctx_g_power_chargestate', C.c_int).value = 1
    assert Tap(fw, st, ui, '62%', b'bar_charge')(12, 15) == ['Charging · 62%']
