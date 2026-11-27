"""patch/charging.c: the status battery's tap and its toast, the cable's own toasts, the plug-in screen and sound."""
import ctypes as C
import struct

import pytest

from conftest import O
from test_nav_playing import Bar, Ui, scratch, st, theme  # noqa: F401 (fixtures)

KEY_LOW, KEY_CHARGING, KEY_PLUGGED, KEY_WEAK = 1, 2, 4, 8  # navigation.h BATT_KEY_*
TOUCH_DOWN, TOUCH_UP = 0xff, 0x103
SLOT_X = 240 + 60  # test_nav_playing.Bar's view_battery on the bar: view_right's x and its own


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


def loop_pass(fw):
    """usb_ui_poll's part for charging, a loop pass: charging_poll, then the toast it left (feedback.c toast_later_poll)."""
    charging, later = fw.fn('charging_poll', None), fw.fn('toast_later_poll', None)
    return lambda: (charging(), later())


class Tap:
    """The status bar's battery synced from stock's label and icon, the screen lit, toasts caught."""
    def __init__(self, fw, st, ui, text='62%', icon=b'bar_battery', battery=0):
        theme(fw, st, BATTERY=battery)
        self.fw, self.toasts = fw, []
        fw.var('g_backlight_status', C.c_ubyte).value = 1
        self.bar = Bar(fw, ui)
        self.bar.level(text, icon)
        self.bar.sync()
        # the car's USB audio (way 3): an output with no glyph, so no slot's tap target meets the battery's
        fw.mock('mclGetOutputWay', lambda *_: 3)
        fw.mock('navigator_to_with_context', lambda page, info, *_: self.toasts.append(C.string_at(info + 8).decode()) or 0)
        # touches in the bar's pixels (status_row.c status_touch): the slot's own plus its x on the bar
        touch = fw.fn('status_touch', None, C.c_uint, C.c_int, C.c_int)
        self.touch = lambda kind, x, y: touch(kind, x + SLOT_X, y)
        self.poll = loop_pass(fw)

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


class Plug:
    """A cable in through usb_ui_status as a charger, the screen dark or lit, with the screen's calls and toasts
    caught; the plug-in screen paints over the top window."""
    def __init__(self, fw, st, ui, lit=0, level=62, state=1, **keys):
        theme(fw, st, BATTERY=0, **keys)
        self.fw, self.ui, self.toasts, self.screen = fw, ui, [], []
        self.lit = fw.var('g_backlight_status', C.c_ubyte)
        self.lit.value = lit
        self.bar = Bar(fw, ui)
        ui.top = ui.widget(ui.wm, 'home_page', 0, 0, 375, 320)
        fw.var('fw_ctx_g_power_capacity', C.c_int).value = level
        fw.var('fw_ctx_g_power_chargestate', C.c_int).value = state
        fw.var('g_usbdet_value', C.c_ubyte).value = 1
        fw.mock('navigator_to_with_context', lambda page, info, *_: self.toasts.append(C.string_at(info + 8).decode()) or 0)
        fw.mock('screen_action', lambda on, *_: self.screen.append(on) or 0)
        fw.mock('toolsReadConfig', lambda path, section, key, out, default, *_: C.memmove(out, C.string_at(default) + b'\0', len(C.string_at(default)) + 1))
        self.poll = loop_pass(fw)
        fw.fn('charge_plugged', None)()

    def frames(self, ms):
        """The plug-in screen's frames until ms after it lit."""
        start = self.ui.now
        while self.ui.due('s1_frame') and self.ui.now - start < ms:
            self.ui.now += 50
            self.ui.run(self.ui.due('s1_frame')[0])


def test_a_dark_plug_in_lights_the_screen_for_three_seconds_then_it_goes_off(fw, st, ui):
    plug = Plug(fw, st, ui)
    assert plug.screen == []  # not inside stock's USB pass
    plug.poll()
    assert plug.screen == [1] and plug.lit.value == 1 and len(ui.due('s1_frame')) == 1
    plug.frames(2900)
    assert plug.screen == [1] and plug.lit.value == 1
    plug.frames(200)
    assert plug.screen == [1, 0] and plug.lit.value == 0 and ui.due('s1_frame') == []


def test_a_lit_plug_in_has_no_screen_and_the_charger_toasts_the_level(fw, st, ui):
    plug = Plug(fw, st, ui, lit=1)
    plug.poll()
    assert plug.screen == [] and ui.due('s1_frame') == []
    fw.fn('usb_charger_in', None)()
    plug.poll()
    assert plug.toasts == ['Charging · 62%']


@pytest.mark.parametrize('level, state, toast', [(100, 2, 'Fully charged'), (41, 1, 'Charging · 41%')])
def test_the_chargers_toast_says_full_too(fw, st, ui, level, state, toast):
    plug = Plug(fw, st, ui, lit=1, level=level, state=state)
    fw.fn('usb_charger_in', None)()
    plug.poll()
    assert plug.toasts == [toast]


def test_a_page_opening_over_it_ends_it_and_keeps_the_screen_lit(fw, st, ui):
    """A computer's chooser opens about a second in: the screen stays on for it."""
    plug = Plug(fw, st, ui)
    plug.poll()
    ui.top = ui.widget(ui.wm, 'usb_chooser', 0, 0, 375, 320)
    plug.frames(100)
    assert plug.screen == [1] and plug.lit.value == 1 and ui.due('s1_frame') == []


def test_a_key_ends_it_lit_and_goes_no_further_than_its_release(fw, st, ui):
    plug = Plug(fw, st, ui)
    plug.poll()
    gate = fw.fn('charge_gate', C.c_int, C.c_void_p)
    keep = []
    def event(kind, key=0):
        raw = C.create_string_buffer(0x80)
        struct.pack_into('<i', raw, O['EVENT_TYPE'], kind)
        struct.pack_into('<i', raw, O['EVENT_KEY'], key)
        keep.append(raw)
        return gate(C.addressof(raw))
    assert [event(O['EVT_KEY_DOWN'], O['KEY_CENTER']), event(O['EVT_KEY_UP'], O['KEY_CENTER'])] == [1, 1]
    assert event(O['EVT_KEY_DOWN'], O['KEY_CENTER']) == 0  # the next press is the page's
    assert ui.due('s1_frame') == [] and plug.lit.value == 1 and plug.screen == [1]


def test_it_paints_the_battery_the_level_and_the_bolt(fw, st, ui):
    from test_nav_playing import Canvas
    plug = Plug(fw, st, ui)
    plug.poll()
    canvas = Canvas(fw)
    fw.fn('charge_paint', None, C.c_void_p, C.c_void_p)(ui.top, canvas.p)
    assert [t[0] for t in canvas.texts] == ['62%', 'Charging']
    assert [name for name, *_ in canvas.images] == [b'oled_chargebolt']
    assert canvas.rects[0][:4] == (0, 0, 375, 320)  # black over the page
    assert len(fw.calls('vgcanvas_stroke')) == 1 and len(fw.calls('vgcanvas_fill')) == 3  # nub, sweep, fill


@pytest.mark.parametrize('reduce, fills', [(0, 3), (1, 2)])
def test_under_reduce_motion_the_fill_stands_at_the_level_with_no_sweep(fw, st, ui, reduce, fills):
    """The sweep is MO_DECOR (ui_kit.h): with IPOD REDUCEMOTION on only the nub and the fill to the level are drawn."""
    from test_nav_playing import Canvas
    plug = Plug(fw, st, ui, REDUCEMOTION=reduce)
    plug.poll()
    fw.fn('charge_paint', None, C.c_void_p, C.c_void_p)(ui.top, Canvas(fw).p)
    assert len(fw.calls('vgcanvas_fill')) == fills


def test_charge_limit_toasts_charged_to_80_once_on_a_cable(fw, st, ui):
    plug = Plug(fw, st, ui, lit=1, level=O['CHARGE_STOP'])
    fw.fn('charge_limited', None)()
    plug.poll()
    assert plug.toasts == [f"Charged to {O['CHARGE_STOP']}%"]


# The plug-in chirp (clicker.c clicker_chirp): q2player mixes it into what plays under Headphones (2) or Both (0);
# Speaker (1), a pause, the stock player or Off sends nothing, and the buzzer never plays it.
@pytest.mark.parametrize('clicker, tone, there, status, sent', [
    ('2', 1, True, 2, True), ('0', 1, True, 2, True), ('1', 1, True, 2, False), ('0', 0, True, 2, False),
    ('0', 1, True, 3, False), ('0', 1, False, 2, False)])
def test_a_plug_in_chirps_in_what_plays(fw, st, ui, clicker, tone, there, status, sent):
    from test_nav_loop import Player
    from test_nav_playing import Config
    Config(fw, CLICKER=clicker)
    st.settings_read = 0  # accent() reads [IPOD] CLICKER again
    fw.var('g_keytone_flag', C.c_ubyte).value = tone
    player = Player(fw, there, status)
    fw.var('g_backlight_status', C.c_ubyte).value = 1
    fw.fn('charge_plugged', None)()
    assert player.clicks() == ([b'{mcl-click\\charge}'] if sent else [])
    assert not fw.calls('fw_pthread_create')  # no buzzer


def test_ringnav_input_turns_the_panels_portrait_touch_upright(fw, st, ui):
    """The panel reports portrait axes (x 0-319 across the short side): upright x is its y, upright y 319 less its x,
    so a tap on the battery at upright (12, 15) in its slot, SLOT_X + 12 on the bar, arrives as (304, SLOT_X + 12)."""
    tap = Tap(fw, st, ui)
    fw.mock('stock_input_trampoline', lambda *_: 0)
    send = fw.fn('ringnav_input', C.c_int, C.c_void_p, C.c_void_p)
    for kind in (TOUCH_DOWN, TOUCH_UP):
        e = C.create_string_buffer(0x40)
        struct.pack_into('<i', e, O['EVENT_TYPE'], kind)
        struct.pack_into('<i', e, O['EVENT_X'], 319 - 15)
        struct.pack_into('<i', e, O['EVENT_Y'], SLOT_X + 12)
        send(ui.wm, e)
    tap.poll()
    assert tap.toasts == ['Battery 62%']


def test_on_percent_a_tap_on_the_percentage_toasts_too(fw, st, ui):
    """Battery: Percent puts the level where the battery was; the target is round the slot's centre."""
    tap = Tap(fw, st, ui, battery=1)
    assert tap(ui.box(tap.bar.slot)[2] // 2, 15) == ['Battery 62%']


def test_a_cable_in_at_boot_is_no_plug_in(fw, st, ui):
    """usb_ui_status's first pass finds the cable already in: no plug-in screen (whose end would turn the screen off
    under the boot) and no chirp; one put in after a pass without it is."""
    plug = Plug(fw, st, ui)
    plug.poll()  # Plug's own charge_plugged
    plug.frames(4000)
    plug.screen.clear()
    det = fw.var('g_usbdet_value', C.c_ubyte)
    fw.mock('fw_system', lambda *_: 0)
    status = fw.fn('usb_ui_status', None)
    status()  # the first pass: in at boot
    plug.poll()
    assert plug.screen == []
    det.value = 0
    status()
    det.value = 1
    status()
    plug.poll()
    assert plug.screen == [1]
