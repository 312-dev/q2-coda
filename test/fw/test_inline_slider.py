"""inline_slider.c (docs/oled-design.md#inline-sliders): Settings' one-value rows change in their list. A row's click
grabs it, the row grows over 150 ms (at once under Reduce Motion), the wheel sets the value through stock's own setter
at the wheel's speed, and Centre or Return sets it down; a setting with a switch is a switch row with its slider row
under it, shown only while the switch is on."""
import ctypes as C

import pytest

from conftest import O
from test_nav_display_library import Config, Pages, Ui, button_of, display, writes
from test_nav_painters import Canvas

KEY_CENTER, KEY_RETURN, KEY_NEXT, KEY_PREV = O['KEY_CENTER'], O['KEY_RETURN'], O['KEY_NEXT'], O['KEY_PREV']
PITCH, GROWN, GROWN_ENDS = 32, 64, 82


class Playback:
    """Settings > Playback as stock opens it, with the screen on (usable), the clock at now."""
    def __init__(self, fw, **config):
        self.fw, self.now = fw, 1000
        fw.mock('time_now_ms', lambda *_: self.now)
        self.ui = Ui(fw)
        Config(fw, **config)
        self.page = Pages(fw, self.ui)
        fw.var('g_backlight_status', C.c_ubyte).value = 1
        self.page.open('playset/playset_page')
        self.view = self.page.view()

    def g(self, name, value=None):
        v = self.fw.var(name, C.c_ubyte)
        if value is not None:
            v.value = value
        return v.value

    def item(self, title):
        return button_of(self.view, title).parent

    def click(self, title):
        return self.ui.click(button_of(self.view, title))

    def key(self, code, owned=0):
        top = self.ui.wm.children[-1].addr
        return self.fw.fn('inline_slider_key', C.c_int, C.c_void_p, C.c_uint, C.c_int)(top, code, owned)

    def sel(self, item, after=200):
        """A paint of the list's selection on item, then the change's timer, after ms: the heights follow the
        timer alone (a selection paint can stop coming while a row grows, seen on the emulator)."""
        self.now += after
        self.fw.fn('inline_slider_sel', None, C.c_void_p, C.c_void_p)(self.view.addr, item.addr if item else None)
        self.step()

    def step(self):
        return self.fw.fn('inline_slider__is_step', C.c_int, C.c_void_p)(None)


def titles(view):
    return [(item.labels()[0].text if item.labels() else item.name, bool(item.visible)) for item in view.children]


def test_playback_s_values_are_inline_rows_and_each_switch_holds_its_slider(fw):
    p = Playback(fw)  # Default Volume off
    t = titles(p.view)
    names = [n for n, _ in t]
    at = names.index
    assert names[at('Max Volume') + 1] == 'Default Volume' and names[at('Default Volume') + 1] == 'Level'
    assert dict(t)['Level'] is False  # under a switch that is off
    assert dict(t)['Max Volume'] and dict(t)['Balance'] and dict(t)['stock_10'] is False  # stock's Balance, hidden
    for title in ('Max Volume', 'Balance', 'Level'):  # no chevron: they change in place
        assert not any(c.props.get('image') == b'list_into' for c in p.item(title).children[0].children)


def test_a_grab_grows_the_row_and_the_wheel_sets_max_volume_pulling_the_volume_down(fw):
    p = Playback(fw)
    p.g('g_maxvolume', 60), p.g('g_volume', 60)
    item = p.item('Max Volume')
    assert p.key(KEY_NEXT) == 0  # nothing grabbed: the wheel is the list's
    p.click('Max Volume')
    p.sel(item, after=0)
    assert item.box[3] == PITCH  # growing from the pitch...
    p.sel(item, after=75)
    assert PITCH < item.box[3] < GROWN
    p.sel(item, after=100)
    assert item.box[3] == GROWN  # ... over 150 ms
    assert p.key(KEY_PREV, owned=1) == 1
    assert p.g('g_maxvolume') < 60 and p.g('g_volume') == p.g('g_maxvolume')
    assert ('VOLUME', p.g('g_volume')) in writes(fw) and writes(fw)[-1] == ('MAXVOLUME', p.g('g_maxvolume'))
    assert fw.calls('device_set_volume')[-1][1][:2] == (p.g('g_volume'), 1)
    assert fw.calls('buzzeer_switch')  # a step clicks, its press silenced
    assert p.key(KEY_CENTER) == 1  # set down
    p.sel(item)
    assert item.box[3] == PITCH and p.key(KEY_NEXT) == 0


def test_max_volume_stops_at_its_ends_and_return_sets_it_down(fw):
    p = Playback(fw)
    p.g('g_maxvolume', 100), p.g('g_volume', 20)
    p.click('Max Volume')
    assert p.key(KEY_NEXT) == 1 and p.g('g_maxvolume') == 100 and not writes(fw)  # its top: nothing to set
    assert p.key(KEY_RETURN) == 1 and p.key(KEY_RETURN) == 0  # the second Return is the page's


def test_balance_steps_through_config_balance_within_its_range_and_shows_its_ends(fw):
    p = Playback(fw)
    balance = [10]
    fw.mock('mclGetBalance', lambda *_: balance[0])
    fw.mock('config_balance', lambda v, *_: balance.__setitem__(0, C.c_int(v).value) or 0)
    item = p.item('Balance')
    p.click('Balance')
    p.sel(item)
    assert item.box[3] == GROWN_ENDS  # Left and Right under its track
    p.key(KEY_NEXT)
    assert balance[0] == 10 and not fw.calls('config_balance')  # already right
    p.key(KEY_PREV)
    assert balance[0] == 9


def test_inline_value_text(fw):
    text = fw.fn('inline_value_text', None, C.c_int, C.c_int, C.c_char_p, C.c_uint)

    def t(kind, v):
        out = C.create_string_buffer(16)
        text(kind, v, out, 16)
        return out.value.decode()
    assert [t(1, 0), t(1, -2), t(1, 10)] == ['Center', 'L 20%', 'R 100%']
    assert [t(0, 80), t(2, 65), t(3, 6)] == ['80', '65%', '6']


def test_brightness_leads_display_and_sets_the_backlight_through_stock(fw):
    ui = Ui(fw)
    fw.mock('time_now_ms', lambda *_: 1000)
    fw.var('g_backlight_status', C.c_ubyte).value = 1
    page = display(fw, ui)
    fw.var('g_lightness', C.c_ubyte).value = 50
    ui.click(button_of(page.view(), 'Brightness'))
    top = ui.wm.children[-1].addr
    assert fw.fn('inline_slider_key', C.c_int, C.c_void_p, C.c_uint, C.c_int)(top, KEY_NEXT, 0) == 1
    (call,) = fw.calls('config_lightness')
    assert call[1][0] > 50


def test_default_volume_s_switch_shows_its_level_and_the_level_saves(fw):
    p = Playback(fw)
    p.g('g_bootvol_flag', 0), p.g('g_bootvolume', 30)
    p.click('Default Volume')
    assert p.g('g_bootvol_flag') == 1 and writes(fw) == [('BOOTVOLFLAG', 1)]
    assert p.item('Level').visible
    sw = button_of(p.view, 'Default Volume').find('_setting_switch')
    assert sw.props['image'] == b'switch_on'
    p.click('Level')
    p.key(KEY_NEXT)
    assert p.g('g_bootvolume') > 30 and writes(fw)[-1] == ('BOOTVOLUME', p.g('g_bootvolume'))
    p.key(KEY_CENTER)
    p.click('Default Volume')
    assert not p.item('Level').visible and writes(fw)[-1] == ('BOOTVOLFLAG', 0)


def test_the_row_grows_on_its_timer_with_no_selection_paint(fw):
    p = Playback(fw)
    item = p.item('Max Volume')
    p.click('Max Volume')
    p.now += 75
    assert p.step() == 8 and PITCH < item.box[3] < GROWN  # repeating, part way
    p.now += 100
    assert p.step() == 7 and item.box[3] == GROWN  # grown, and the timer ends


def test_reduce_motion_grows_the_row_at_once(fw):
    p = Playback(fw, REDUCEMOTION=b'1')
    item = p.item('Max Volume')
    p.click('Max Volume')
    p.sel(item, after=0)
    assert item.box[3] == GROWN


def test_the_selection_moving_off_sets_the_row_down(fw):
    p = Playback(fw)
    item = p.item('Max Volume')
    p.click('Max Volume')
    p.sel(item)
    p.sel(p.item('Balance'), after=0)  # a tap on another row took the selection
    assert p.key(KEY_NEXT) == 0
    p.sel(p.item('Balance'))
    assert item.box[3] == PITCH


@pytest.mark.parametrize('grabbed', [False, True])
def test_a_row_paints_its_value_and_track_at_rest_and_grabbed(fw, grabbed):
    p = Playback(fw)
    p.g('g_maxvolume', 80)
    item = p.item('Max Volume')
    if grabbed:
        p.click('Max Volume')
        p.sel(item)
    canvas = Canvas(fw)
    fw.fn('inline_slider_paint', None, C.c_void_p, C.c_void_p)(item.addr, canvas.addr)
    assert [t for t, *_ in canvas.texts] == ['80']
    tracks = [b for b in canvas.boxes if b[0][3] == 4]  # the track and its fill
    x0 = 190 if not grabbed else None
    if x0:
        assert tracks[0][0][0] == x0 and tracks[0][0][1] == PITCH // 2 - 2
    else:
        assert tracks[0][0][1] == 43 - 2 and tracks[0][0][2] > 250  # full width, under the title
    assert canvas.restored()



def test_a_swipe_back_takes_the_value_and_track_along_with_the_page(fw):
    """docs/internals.md#swipe-back: the swipe moves the page's window (widget_move), so every canvas in it starts
    that far right. The value and the track are put on the page's columns and must ride with it, not stay at their
    screen columns over the page uncovered under it (seen on the device: the value stayed put mid-swipe)."""
    p = Playback(fw)
    p.g('g_maxvolume', 80)
    item = p.item('Max Volume')
    paint = fw.fn('inline_slider_paint', None, C.c_void_p, C.c_void_p)
    rest = Canvas(fw)
    paint(item.addr, rest.addr)
    off = 120
    win = p.page.pages[-1]
    win.box = (off,) + win.box[1:]  # where swipe_back.c's place() puts it
    moved = Canvas(fw, x=off)
    paint(item.addr, moved.addr)
    assert [r for _, r, _ in moved.texts] == [r for _, r, _ in rest.texts]  # the value, on the page's column
    assert [b[0] for b in moved.boxes] == [b[0] for b in rest.boxes]  # the track and its fill
