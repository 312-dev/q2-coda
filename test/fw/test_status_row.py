"""patch/status_row.c: the status row's glyph slots, right to left the battery, the level bars, Wi-Fi, a card app's
sync, wired headphones, Bluetooth (its link, a headset's battery, a car) and Buttons Lock's padlock; their layout (the
clock's edge), paint, taps and repaints."""
import ctypes as C
import struct

import pytest

from conftest import O, Files, threads_now
from test_addon import Ap
from test_nav_painters import Canvas, StatusRow, i32, scratch, st  # noqa: F401 (fixtures)

BODY = 322  # StatusRow's view_battery on the bar: the battery's body starts here
CY = 23  # OLED's status_y: the glyphs' centre row
GAP = 6  # SR_GAP: the ground between two glyphs
INK = 11  # SR_INK: every glyph's height, the clock's digits' (rows 17 to 27)
WIFI_W = 16  # SR_WIFI_W
SLOT_BATTERY, SLOT_PLAY, SLOT_WIFI, SLOT_SYNC, SLOT_WIRED, SLOT_HEADSET, SLOT_LOCK, SLOT_OFFLINE = range(8)
RUNE_W = 9  # SR_RUNE_W: the Bluetooth rune, 12 px tall (tools/art.py STATUS_TALL_FIT)
TOUCH_DOWN, TOUCH_UP = 0xff, 0x103
BARS = [(7, 10, 3), (9, 8, 3), (11, 5, 5), (11, 3, 7), (9, 3, 10), (7, 4, 11), (5, 6, 11), (3, 9, 9), (3, 11, 7), (5, 11, 4)]


class Row(StatusRow):
    """StatusRow (Home, the play state 2) with Wi-Fi's stock widget, the network monitor, card apps, the lock and
    the frames' timers in reach."""
    def __init__(self, fw, st, title='', status=2):
        super().__init__(fw, st, title)
        self.status = status
        self.online = 1
        fw.mock('networkGetConnectStatus', lambda *_: self.online)
        # the player's output: 0 the Q2's DAC (its jacks), 1 Bluetooth, 2 a USB DAC, 3 the car's USB audio: an output
        # with no glyph of its own, so the other slots lay out alone (0 with nothing in would show the deaf ear)
        self.way = 3
        fw.mock('mclGetOutputWay', lambda *_: self.way)
        self.timers: list = []
        fw.mock('timer_add', lambda fn, ctx, ms, *_: self.timers.append((fn, i32(ms))) or len(self.timers))
        self.dirty: list = []
        fw.mock('widget_invalidate_force', lambda w, r, *_: self.dirty.append(struct.unpack('<4i', C.string_at(r, 16)) if r else None))
        fw.var('g_backlight_status', C.c_ubyte).value = 1
        self.ap = fw.var('addon__ap', Ap)
        sr = C.c_char.in_dll(fw.lib, 'status_row__sr')  # StatusRow's first sync ran under its own timer_add
        C.memset(C.addressof(sr), 0, fw.sizes['status_row__sr'])
        loops = C.c_char.in_dll(fw.lib, 'ui_kit__loops')  # ... and may have started the shared loop
        C.memset(C.addressof(loops), 0, fw.sizes['ui_kit__loops'])

    def wifi(self, level):
        node = self.w['img_wifi']
        node.visible = int(level is not None)
        node.props['image'] = f'bar_wifi{level}'

    def app(self, sync, permille=0):
        self.ap.n = 1
        self.ap.a[0].launched, self.ap.a[0].sync, self.ap.a[0].sync_pm = 1, sync, permille

    def left(self):
        return self.fw.fn('status_slots_left', C.c_int)()

    def paint(self, clips=None):
        """The slots painted on a fresh canvas: it and the art names loaded; clips, a list, gets each clip set."""
        canvas = Canvas(self.fw)
        if clips is not None:
            set_clip = lambda c, r, *_: clips.append(struct.unpack('<4i', C.string_at(r, 16))) or setattr(canvas, 'clip', list(clips[-1]))
            self.fw.mock('canvas_set_clip_rect', set_clip)
        names: list = []
        load = canvas.load_image
        def loaded(w, name, bitmap, *_):
            names.append(C.string_at(name).decode())
            return load(w, name, bitmap)
        self.fw.mock('widget_load_image', loaded)
        canvas.art = {n: (WIFI_W, 14) for n in ('oled_status_wifi_3_d', 'oled_status_wifi_2_d', 'oled_status_wifi_1_d', 'oled_status_wifi_x_d')}
        canvas.art |= {'oled_keylock_d': (14, 14), 'oled_status_bt_d': (RUNE_W, 14), 'oled_status_down_d': (14, 14), 'oled_status_wired_d': (14, 14),
                       'oled_status_offline_d': (14, 14)}
        canvas.art |= {f'oled_status_sync_{f}_d': (14, 14) for f in range(30)}
        self.fw.fn('status_slots_paint', None, C.c_void_p, C.c_void_p)(self.bar.addr, canvas.addr)
        return canvas, names

    def bars(self, x=100):
        """The play state's bars at x on CY (play_bars) for the state now: (left, top, width, height, radius) each."""
        out = (C.c_float * 5 * 3)()
        n = self.fw.fn('status_row__play_bars', C.c_int, C.c_int, C.c_int, C.c_int, C.c_void_p)(x, CY, int(self.status != 2), out)
        return [tuple(round(v, 3) for v in out[i]) for i in range(n)]

    def sync_sig(self):
        return self.fw.fn('status_row_sync', C.c_int)()


@pytest.fixture
def row(fw, st):
    return Row(fw, st)


def test_the_slots_lay_out_right_to_left_from_the_battery(fw, row):
    """Each shown slot's box, then GAP to the one on its right (mock_status_icons.py, iOS's spacing): the bars 10
    wide, Wi-Fi's fan 16, a sync's arrows 12 (their upright ink) and its ring 16, Buttons Lock's padlock 13."""
    assert row.left() == BODY - GAP - 10
    row.wifi(3)
    assert row.left() == BODY - GAP - 10 - GAP - WIFI_W
    row.app(1)
    assert row.left() == BODY - GAP - 10 - GAP - WIFI_W - GAP - 12
    row.app(2, 500)
    assert row.left() == BODY - GAP - 10 - GAP - WIFI_W - GAP - 16
    fw.var('g_keylock_flag', C.c_ubyte).value = 1
    assert row.left() == BODY - GAP - 10 - GAP - WIFI_W - GAP - 16 - GAP - 13


@pytest.mark.parametrize('status, wifi, left', [(1, None, BODY), (0, 3, BODY - GAP - WIFI_W), (3, 0, BODY - GAP - 10),
                                                (2, 5, BODY - GAP - 10), (2, None, BODY - GAP - 10)])
def test_a_hidden_slot_takes_no_room(fw, st, status, wifi, left):
    """Stopped there are no bars; Wi-Fi off, not connected (bar_wifi0) or an image stock never names shows none:
    the clock moves up to what is left."""
    row = Row(fw, st, status=status)
    row.wifi(wifi)
    assert row.left() == left


HEAD = 'AA:BB:CC:DD:EE:01'


def bt_link(fw, status=None, level=None, cls=None, thread=0):
    """Stock's link list (its last item HEAD at status, or none), a link thread running, Bluetooth on and the battery
    loop's file ("MAC PCT RATE CLASS")."""
    from conftest import Files
    links = [(HEAD, status)] if status is not None else []
    fw.var('g_bluetoothflag', C.c_ubyte).value = 1
    fw.var('fw_ctx_bt_connectstatus', C.c_int).value = thread
    fw.mock('getBtLinkSize', lambda *_: len(links))

    def item(i, st_, mac, *_):
        C.c_int.from_address(st_).value = links[i][1]
        C.memmove(mac, HEAD.encode() + b'\0', len(HEAD) + 1)
        return 0
    fw.mock('getBtLinkItem', item)
    fw.mock('fw_system', lambda *_: 0)
    threads_now(fw)  # the battery run (bt_battery.c) ends at once; its answer is the file
    lv = '' if level is None else level
    files = Files(fw, {'/tmp/q2-btbatt': f'{HEAD} {lv} 48000 {cls or ""}\n'.encode()})
    fw.fn('bt_headset_battery', C.c_int)()  # its run started and ended; the file read at the next look
    now = [100000]
    fw.mock('time_now_ms', lambda *_: now[0])
    return files


@pytest.mark.parametrize('status, thread, level, cls, width, art', [
    (None, 0, None, None, 0, []),  # off or idle: nothing
    (1, 1, None, None, RUNE_W, ['oled_status_btdim_d']),  # connecting: the dim rune
    (3, 0, None, None, RUNE_W, ['oled_status_bt_d']),  # connected, no level: the white rune
    (3, 0, 72, 0x240404, 14 + 2 + 15 + 2, ['oled_status_headphones_d']),  # a headset's level: headphones, battery
    (3, 0, None, 0x200420, 19, ['oled_status_car_d']),  # a car: 11 px tall, 19 wide (SR_CAR_W)
])
def test_the_bluetooth_slot_says_what_the_link_is(fw, row, status, thread, level, cls, width, art):
    bt_link(fw, status, level, cls, thread)
    assert fw.fn('status_row__headset_shown', C.c_int)() == width
    canvas = Canvas(fw)
    names: list = []
    load = canvas.load_image
    fw.mock('widget_load_image', lambda w, n, b, *_: names.append(C.string_at(n).decode()) or load(w, n, b))
    canvas.art = {n: (14, 14) for n in art}
    fw.fn('status_row__headset_paint', None, C.c_void_p, C.c_void_p, C.c_int, C.c_int)(row.bar.addr, canvas.addr, 200, CY)
    assert names == art


@pytest.mark.parametrize('level, fill', [(72, 0xffffff), (20, 0xffffff), (19, 0xff453a)])
def test_the_headset_s_battery_is_15_by_8_and_red_under_20(fw, row, level, fill):
    from test_peq_page import rgb
    bt_link(fw, 3, level, 0x240404)
    fw.mock('canvas_get_vgcanvas', lambda *_: 1)
    canvas = Canvas(fw)
    canvas.art = {'oled_status_headphones_d': (14, 14)}
    fw.fn('status_row__headset_paint', None, C.c_void_p, C.c_void_p, C.c_int, C.c_int)(row.bar.addr, canvas.addr, 200, CY)
    assert rgb(fw.calls('vgcanvas_set_fill_color')[0][1][1]) == fill  # the level's fill, then the nub's grey
    assert len(fw.calls('vgcanvas_stroke')) == 1  # the body's outline


def jacks(fw, po=0, bal=0):
    """The 3.5 mm (g_po_status) and 4.4 mm (g_bal_status) jacks: 1 plugged."""
    fw.var('g_po_status', C.c_ubyte).value = po
    fw.var('g_bal_status', C.c_ubyte).value = bal


@pytest.mark.parametrize('way, po, bal, shown', [
    (0, 1, 0, 14),  # the 3.5 mm jack
    (0, 0, 1, 14),  # the 4.4 mm jack
    (2, 0, 0, 14),  # a USB DAC, USB-C headphones among them
    (1, 1, 0, 0),  # Bluetooth takes the sound, a jack in or not
    (3, 0, 0, 0),  # the car's USB audio (usb_car.c): no headphones
])
def test_wired_headphones_show_while_they_take_the_sound(fw, row, way, po, bal, shown):
    """The wired slot (the user's, 2026-11-25): Phosphor's headphones while the player's output is a jack or a USB
    DAC, 14 px wide, GAP left of the slot on its right (Wi-Fi's here); not otherwise (here a headset is linked, so no
    deaf ear either: the test below)."""
    bt_link(fw, 3)
    row.wifi(3)
    row.way = way
    jacks(fw, po, bal)
    assert bool(fw.fn('status_row__wired_on', C.c_int)()) == bool(shown)
    assert fw.fn('status_row__wired_shown', C.c_int)() == shown
    canvas, names = row.paint()
    assert ('oled_status_wired_d' in names) == bool(shown)
    assert 'oled_status_noaudio_d' not in names


EAR_W = 10  # SR_EAR_W: ear-slash 12 px tall is 9.4 wide (tools/art.py STATUS_TALL_FIT)


class Air(C.Structure):
    """patch/airplay.c's air, whole (fw.var checks its size): on is a speaker playing."""
    _fields_ = [('t', C.c_void_p), ('look', C.c_uint), ('on', C.c_int), ('local', C.c_int), ('limit', C.c_int),
                ('way', C.c_int), ('spk_seq', C.c_uint), ('song', C.c_uint), ('len', C.c_int), ('arriving', C.c_int),
                ('len_at', C.c_uint), ('asked', C.c_int), ('checked', C.c_int)]


@pytest.mark.parametrize('way, po, bal, art, width', [
    (0, 0, 0, 'oled_status_noaudio_d', EAR_W),  # nothing plugged in, nothing linked: the deaf ear
    (0, 1, 0, 'oled_status_wired_d', 14),  # the 3.5 mm jack: headphones
    (0, 0, 1, 'oled_status_wired_d', 14),  # the 4.4 mm jack
    (2, 0, 0, 'oled_status_wired_d', 14),  # a USB DAC
    (3, 0, 0, None, 0),  # the car's USB audio (usb_car.c) plays: no ear
    (1, 1, 0, 'oled_status_noaudio_d', EAR_W),  # Bluetooth's way with no device linked plays to no one: the ear
])
def test_with_no_output_connected_the_wired_slot_shows_a_deaf_ear(fw, row, way, po, bal, art, width):
    """The user's, 2026-11-25 ("a deaf ear icon ... ship it"): with no jack, no USB DAC and no Bluetooth device
    connected, Phosphor's ear-slash (regular) in the wired slot, EAR_W wide, GAP left of Wi-Fi; any output connected,
    the slot is as before."""
    row.wifi(3)
    row.way = way
    jacks(fw, po, bal)
    assert fw.fn('status_row__wired_shown', C.c_int)() == width
    assert row.left() == BODY - GAP - 10 - GAP - WIFI_W - (GAP + width if width else 0)
    canvas, names = row.paint()
    shown = [n for n in names if n in ('oled_status_noaudio_d', 'oled_status_wired_d')]
    assert shown == ([art] if art else [])


@pytest.mark.parametrize('status, thread, ear', [(3, 0, 0), (1, 1, 1), (None, 0, 1)])
def test_a_bluetooth_device_connected_takes_the_ear_away(fw, row, status, thread, ear):
    """Connected (the rune, a headset, a car) is an output: no ear. Connecting is not yet: the ear and the dim rune."""
    row.way = 0
    jacks(fw)
    bt_link(fw, status, None, None, thread)
    assert fw.fn('status_row__wired_shown', C.c_int)() == (EAR_W if ear else 0)


def test_a_guest_or_an_airplay_speaker_playing_takes_the_ear_away(fw, row):
    """A guest's phone plays through the Q2 (Guest Play), and an AirPlay speaker is an output of its own."""
    row.way = 0
    jacks(fw)
    assert fw.fn('status_row__wired_shown', C.c_int)() == EAR_W
    air = fw.var('airplay__air', Air)
    air.on = 1
    assert fw.fn('status_row__wired_shown', C.c_int)() == 0
    air.on = 0
    from test_guest import Rig
    rig = Rig(fw)
    rig.state(state='playing', out='jack')
    rig.poll()
    assert fw.fn('status_row__wired_shown', C.c_int)() == 0


def test_the_ear_coming_or_going_repaints_the_row(fw, row):
    """The car's USB audio stopping (way 3 to 0) changes nothing else the signature holds but the ear."""
    jacks(fw)
    row.sync_sig()
    assert row.sync_sig() == 0
    row.way = 0
    assert row.sync_sig() == 1
    assert row.sync_sig() == 0


@pytest.mark.parametrize('way, po, wired, rune', [(0, 1, 1, 0), (2, 0, 1, 0), (1, 1, 0, 1)])
def test_with_a_headset_linked_and_a_wire_in_the_row_shows_where_the_sound_goes(fw, row, way, po, wired, rune):
    """Bluetooth and a wire both there: the slot of the output the player uses (stock plays to the jack last plugged
    in, over a linked headset), the other hidden. A guest's phone stays (Guest Play is its own output)."""
    bt_link(fw, 3)
    row.way = way
    jacks(fw, po)
    assert bool(fw.fn('status_row__wired_shown', C.c_int)()) == bool(wired)
    assert fw.fn('status_row__headset_shown', C.c_int)() == (RUNE_W if rune else 0)


def test_wired_headphones_coming_or_going_repaint_the_row(fw, row):
    row.way = 0
    jacks(fw)
    row.sync_sig()
    assert row.sync_sig() == 0
    jacks(fw, po=1)
    assert row.sync_sig() == 1
    assert row.sync_sig() == 0


def test_a_slot_that_changes_on_stock_s_tick_repaints_the_row(fw, row):
    """Stock's showface each second (ringnav_showface): a slot that changed (here wired headphones plugged in) repaints
    the bar then, not at whatever paints it next; a tick with nothing new repaints nothing."""
    row.way = 0
    jacks(fw)
    row.sync()
    row.dirty.clear()
    row.sync()
    assert row.dirty == []
    jacks(fw, po=1)
    row.sync()
    assert row.dirty == [None]


def test_the_slot_s_kind_repaints_the_row(fw, row):
    bt_link(fw, 1, None, None, 1)
    row.sync_sig()
    assert row.sync_sig() == 0
    bt_link(fw, 3, None, None, 0)
    assert row.sync_sig() == 1


def test_the_play_state_stands_on_the_digits_baseline_in_text_primary_playing_or_paused(fw, st, row):
    """Playing, the level bars: 2 px wide with round ends, 4 px apart, up to 11 tall, their feet on CY + 5 (row 27's
    bottom, the digits' baseline); paused, the pause: two 3 px bars 11 tall at 0.5 and 6.5 of the same 10 px box.
    Both in Text Primary, as Wi-Fi and the battery (Text Secondary grey made the paused bars read as signal bars).
    Under Reduce Motion, so each change is a cut and a paint shows one glyph."""
    st('reduce_motion').value = 1
    bars = row.bars()
    assert bars == [(100 + 4 * i, CY + 5 - h, 2, h, 1) for i, h in enumerate(BARS[0])]
    assert max(h for *_, h, _ in bars) <= INK
    fills: list = []
    fw.mock('canvas_get_vgcanvas', lambda *_: 1)
    fw.mock('vgcanvas_set_fill_color', lambda vg, c, *_: fills.append(c & 0xffffffff))
    row.sync_sig()
    row.paint()
    row.status = 3
    assert row.bars() == [(100.5, CY + 5 - INK, 3, INK, 0.9), (106.5, CY + 5 - INK, 3, INK, 0.9)]
    row.sync_sig()
    row.paint()
    assert len(fw.calls('vgcanvas_fill')) == 3 + 2 and fills == [0xffffffff] * 2  # white: Text Primary each time
    st('light').value = 1  # Light Mode: black, paused and playing
    row.paint()
    row.status = 2
    row.sync_sig()
    row.paint()
    assert fills[2:] == [0xff000000] * 2


class Xfade(Row):
    """Row with the clock in hand, the shared frame timer's hook in reach and play_paint's fills recorded:
    [(alpha, [bar boxes])] a paint, one entry per glyph drawn."""
    def __init__(self, fw, st):
        super().__init__(fw, st)
        self.now = 5000
        fw.mock('time_now_ms', lambda *_: self.now)
        fw.mock('canvas_get_vgcanvas', lambda *_: 1)
        self.ink: list = []  # [colour, fills] per vgcanvas_set_fill_color
        fw.mock('vgcanvas_set_fill_color', lambda vg, c, *_: self.ink.append([c & 0xffffffff, 0]))
        fw.mock('vgcanvas_fill', lambda *_: self.ink[-1].__setitem__(1, self.ink[-1][1] + 1))
        hooks = (C.c_void_p * 2).in_dll(fw.lib, 'ui_kit__mo_hooks')
        C.memset(C.addressof(hooks), 0, C.sizeof(hooks))
        self.hooks = hooks

    def glyphs(self):
        """What play_paint draws now: [(alpha 0-255, colour 0xRRGGBB, n bars)], the bars first."""
        self.ink.clear()
        self.fw.fn('ui_ink_raw', C.c_uint, C.c_uint)(1)  # final colours, as status_slots_paint paints the slots
        self.fw.fn('status_row__play_paint', None, C.c_void_p, C.c_void_p, C.c_int, C.c_int)(None, Canvas(self.fw).addr, 100, CY)
        return [(c >> 24, c & 0xffffff, n) for c, n in self.ink if n]

    def bars_of(self, pause):
        """play_bars' boxes for the bars (0) or the pause (1), whatever the state."""
        out = (C.c_float * 5 * 3)()
        n = self.fw.fn('status_row__play_bars', C.c_int, C.c_int, C.c_int, C.c_int, C.c_void_p)(100, CY, pause, out)
        return [tuple(round(v, 3) for v in out[i]) for i in range(n)]

    def frame(self):
        """The shared timer's frame: each hook motion_hook added."""
        for h in self.hooks:
            if h:
                C.CFUNCTYPE(None)(h)()


@pytest.fixture
def xf(fw, st):
    return Xfade(fw, st)


PLAY_BOX = (BODY - GAP - 10, 0, 10, 26)  # the play slot's box on the bar, the only rect a dissolve repaints


def test_pausing_stops_the_bars_and_cross_dissolves_them_into_the_pause_in_their_box(fw, xf):
    """The user's (21:52): paused, the bars stop and cross-dissolve into the pause over SR_XFADE_MS (250 ms, the
    house cubic ease-out), the frames coming from the shared timer's hook and repainting only the play box; the
    slot keeps its place and width, so nothing else in the row moves."""
    xf.sync_sig()
    assert xf.glyphs() == [(255, 0xffffff, 3)]
    left = xf.left()
    xf.status = 3
    assert xf.sync_sig() == 1  # the bar repaints whole once, as for any change
    xf.dirty.clear()
    assert xf.glyphs() == [(255, 0xffffff, 3)]  # the dissolve's start: the bars, whole
    xf.now += 125
    xf.frame()
    assert xf.dirty == [PLAY_BOX]  # a frame repaints the play box only
    assert xf.glyphs() == [(31, 0xffffff, 3), (223, 0xffffff, 2)]  # half way, eased to 875: bars out, pause in
    xf.now += 125
    assert xf.glyphs() == [(255, 0xffffff, 2)]  # the pause alone at 250 ms
    xf.frame()
    xf.frame()
    assert xf.dirty == [PLAY_BOX, PLAY_BOX]  # the end's repaint, then no more
    assert xf.left() == left


def test_resuming_mid_dissolve_turns_back_from_where_it_stands(fw, xf):
    xf.sync_sig()
    xf.status = 3
    xf.sync_sig()
    xf.now += 125
    mid = xf.glyphs()
    xf.status = 2
    xf.sync_sig()
    assert xf.glyphs() == mid  # back from where it stood, no jump
    xf.now += 250
    assert xf.glyphs() == [(255, 0xffffff, 3)]


def test_under_reduce_motion_the_swap_is_a_cut(fw, st, xf):
    st('reduce_motion').value = 1
    xf.sync_sig()
    xf.status = 3
    xf.sync_sig()
    assert xf.glyphs() == [(255, 0xffffff, 2)]
    assert not any(xf.hooks)  # no dissolve: no frames from the shared timer
    xf.status = 2
    xf.sync_sig()
    assert xf.glyphs() == [(255, 0xffffff, 3)]


def test_a_slot_that_appears_shows_its_state_at_once(fw, xf):
    """A page opened on a paused song (or Home's first row leaving the slot) shows the pause, with no dissolve."""
    xf.status = 3
    xf.sync_sig()
    assert xf.glyphs() == [(255, 0xffffff, 2)] and not any(xf.hooks)
    xf.status = 0  # stopped: the slot goes
    xf.sync_sig()
    xf.status = 2
    xf.sync_sig()
    assert xf.glyphs() == [(255, 0xffffff, 3)] and not any(xf.hooks)


@pytest.mark.parametrize('light, rgb', [(0, 0xffffff), (1, 0x000000)])
def test_both_glyphs_dissolve_in_the_row_s_ink(fw, st, xf, light, rgb):
    """The same colour as every status glyph (Text Primary: white, black in Light Mode) through the dissolve."""
    st('light').value = light
    xf.sync_sig()
    xf.status = 3
    xf.sync_sig()
    xf.now += 100
    assert {c for _, c, _ in xf.glyphs()} == {rgb} and len(xf.glyphs()) == 2


def test_a_dark_screen_starts_no_frames(fw, row):
    fw.var('g_backlight_status', C.c_ubyte).value = 0
    row.sync_sig()
    assert row.timers == []


@pytest.mark.parametrize('level, online, art', [(4, 1, 'oled_status_wifi_3_d'), (3, 1, 'oled_status_wifi_3_d'),
                                                (2, 1, 'oled_status_wifi_2_d'), (1, 1, 'oled_status_wifi_1_d'),
                                                (4, 0, 'oled_status_wifi_x_d')])
def test_wifi_lights_arcs_by_its_bars_and_crosses_out_with_no_internet(fw, row, level, online, art):
    row.wifi(level)
    row.online = online
    canvas, names = row.paint()
    x = BODY - GAP - 10 - GAP - WIFI_W
    assert names == [art] and canvas.images == [(x, CY - 7, WIFI_W, 14)]


def test_a_sync_check_turns_the_arrows_and_a_download_fills_its_ring(fw, row):
    """The user's (2026-11-20): the arrows at 30 fps, a half turn a second as before. They ride ui_kit's one 33 ms loop
    timer (no timer of their own), their frame the clock's place in the half turn's second (SR_TURN_MS over SR_SPIN
    frames), and each new frame repaints only their art's box; a download's ring does not turn."""
    now = [10_000]
    fw.mock('time_now_ms', lambda *_: now[0])
    row.status = 0
    row.app(1)
    canvas, names = row.paint()
    # the 14 px art a px either side of the arrows' 12 px box, and that art's box repainted each frame
    assert names == ['oled_status_sync_0_d'] and canvas.images == [(BODY - GAP - 12 - 1, CY - 7, 14, 14)]
    assert row.sync_sig() == 1 and [ms for _, ms in row.timers] == [33]
    tick = C.CFUNCTYPE(C.c_int, C.c_void_p)(row.timers[0][0])
    now[0] += 34
    assert tick(None) == 8 and row.dirty == [(BODY - GAP - 12 - 1, 0, 14, 26)]
    assert row.paint()[1] == ['oled_status_sync_1_d']
    row.dirty.clear()
    assert tick(None) == 8 and row.dirty == []  # the same frame: nothing repainted
    now[0] += 500
    assert tick(None) == 8 and row.paint()[1] == ['oled_status_sync_16_d']
    now[0] += 466
    assert tick(None) == 8 and row.paint()[1] == ['oled_status_sync_0_d']  # a second: the half turn again
    row.sync_sig()
    assert len(row.timers) == 1  # one loop, added once
    row.app(2, 250)
    fw.mock('canvas_get_vgcanvas', lambda *_: 1)
    canvas, names = row.paint()
    assert names == ['oled_status_down_d'] and canvas.images == [(BODY - GAP - 16 + 4, CY - 4, 14, 14)]
    assert len(fw.calls('vgcanvas_arc')) == 2 and len(fw.calls('vgcanvas_stroke')) == 2  # the track, then the fill
    assert tick(None) == 7  # a download does not turn: the loop drops the arrows and, the last, ends


def test_the_padlock_sits_where_buttons_lock_put_it(fw, row):
    """The 13 px padlock is drawn where the stock image widget centred it: its box's top a row above the
    glyph's half (16 on a 23 centre)."""
    fw.var('g_keylock_flag', C.c_ubyte).value = 1
    row.status = 0
    canvas, names = row.paint()
    assert names == ['oled_keylock_d'] and canvas.images == [(BODY - GAP - 13, CY - 7, 14, 14)]


@pytest.mark.parametrize('change', ['wifi', 'offline', 'lock', 'status', 'sync', 'ring'])
def test_a_change_repaints_the_bar_once(fw, row, change):
    row.wifi(2)
    row.app(2, 100)
    assert row.sync_sig() == 1 and row.sync_sig() == 0
    {'wifi': lambda: row.wifi(3), 'offline': lambda: setattr(row, 'online', 0),
     'lock': lambda: setattr(fw.var('g_keylock_flag', C.c_ubyte), 'value', 1), 'status': lambda: setattr(row, 'status', 3),
     'sync': lambda: row.app(1), 'ring': lambda: row.app(2, 140)}[change]()
    assert row.sync_sig() == 1 and row.sync_sig() == 0


def test_a_ring_moving_less_than_a_fiftieth_waits(fw, row):
    row.app(2, 100)
    row.sync_sig()
    row.app(2, 119)
    assert row.sync_sig() == 0


@pytest.mark.parametrize('x, slot', [(BODY + 12, SLOT_BATTERY), (BODY - 11, SLOT_PLAY), (BODY + 1, SLOT_BATTERY), (BODY, SLOT_PLAY),
                                     (BODY - 1, SLOT_PLAY), (BODY - 11 - 22, SLOT_PLAY), (BODY - 11 - 23, -1), (BODY + 12 + 22, -1)])
def test_a_touch_finds_the_nearest_slot_inside_its_target(fw, row, x, slot):
    """44 x 40 round each centre: the battery's body's (12 in), the bars' (5 in, GAP left of the body); where they
    meet, the nearer."""
    assert fw.fn('status_slot_at', C.c_int, C.c_int, C.c_int)(x, 13) == slot


def test_a_tap_on_the_bars_opens_now_playing_after_the_dispatch(fw, row):
    opened: list = []
    fw.mock('navigator_switch_to_with_context', lambda page, *_: opened.append(C.string_at(page)) or 0)
    touch = fw.fn('status_touch', None, C.c_uint, C.c_int, C.c_int)
    touch(TOUCH_DOWN, BODY - 9, 13)
    touch(TOUCH_UP, BODY - 8, 15)
    assert opened == [] and len(row.timers) == 1
    C.CFUNCTYPE(C.c_int, C.c_void_p)(row.timers[0][0])(None)
    assert opened == [b'playing_page']
    touch(TOUCH_DOWN, BODY - 9, 13)
    touch(TOUCH_UP, BODY - 9, 13 + 13)  # a pull down from the row: Control Center's
    assert len(row.timers) == 1


CLOCK_W = 38  # test_nav_painters.StatusRow's widest clock, "00:00"
LEFT_ROOM = 62 + CLOCK_W + 7  # the strip's left edge: CLOCK_BESIDE past the widest clock at the row's left
ROOM = 214  # a room narrower than the crowd below, for the marquee's tests: 30 px short
OFFLINE_W = 14  # SR_OFFLINE: cloud-slash's 13.6 px of ink at 11 tall


def crowd(fw, row, room=ROOM):
    """Every slot shown: bars 10, Wi-Fi 16, a download's ring 16, a headset 33, the padlock 13 and the offline cloud
    14, with their six gaps: 138 px left of the battery's body, 30 more than ROOM leaves; the strip's room then set
    to room."""
    row.wifi(3)
    row.app(2, 500)
    row.ap.a[0].reach = 1
    bt_link(fw, status=3, level=70)
    fw.var('g_keylock_flag', C.c_ubyte).value = 1
    row.place()
    fw.fn('status_strip_room', None, C.c_int)(room)
    return 10 + WIFI_W + 16 + 33 + 13 + OFFLINE_W + 6 * GAP


def test_the_clock_at_the_row_s_left_leaves_every_glyph_room(fw, row):
    """The clock stands at the row's left (the user's layout D, 2026-11-25): the strip's room starts CLOCK_BESIDE past
    the widest clock there, so every slot shown at once fits with no marquee."""
    width = crowd(fw, row, room=0)
    clock = row.place()
    assert clock.box[0] + 2 == 62
    assert row.left() == BODY - width and BODY - width > LEFT_ROOM
    assert fw.fn('status_row__strip_ends', C.c_int)() == 0


def test_a_strip_wider_than_its_room_marquees(fw, row):
    """A strip wider than its room rests with its left end (the first glyph after the clock) in view, the rest under
    the battery's gap."""
    over = ROOM - (BODY - crowd(fw, row))
    assert over == 30 and row.left() == ROOM
    sr_x = (C.c_int * 8).in_dll(fw.lib, 'status_row__sr')
    assert sr_x[SLOT_OFFLINE] == ROOM  # at rest: the leftmost glyph at the room's edge
    assert sr_x[SLOT_PLAY] == BODY - GAP - 10 + over  # the bars, past the strip's box, out of sight


def test_a_strip_that_fits_keeps_its_place_by_the_battery(fw, row):
    row.wifi(3)
    row.place()
    assert row.left() == BODY - GAP - 10 - GAP - WIFI_W
    assert fw.fn('status_row__strip_off', C.c_int, C.c_int, C.c_uint)(0, 99999) == 0


@pytest.mark.parametrize('t, off', [(0, 0), (2749, 0), (2750, 0), (2750 + 1000, 35), (2750 + 2000, 60),
                                    (2750 + 1714 + 1499, 60), (2750 + 1714 + 1500, 0), (2750 + 1714 + 1500 + 2750 + 1000, 35)])
def test_the_strip_rests_slides_at_35_px_a_second_holds_at_its_end_and_starts_again(fw, t, off):
    """As Now Playing's lines (np_marquee.c): 2.75 s at rest, 35 px/s until its right end is in view (60 px: 1714 ms),
    MARQUEE_HOLD_MS there, then back to rest."""
    assert fw.fn('status_row__strip_off', C.c_int, C.c_int, C.c_uint)(60, t) == off


def test_the_strip_paints_inside_its_box_with_6_px_fades_on_its_cut_ends(fw, row):
    crowd(fw, row)
    clips: list = []
    canvas, names = row.paint(clips)
    assert clips[0][:3] == (ROOM, 0, BODY - GAP - ROOM) and clips[-1] == (0, 0, 375, 320)  # the strip's box, then the frame's clip again
    assert names[-1] == 'oled_status_offline_d'
    ends = fw.fn('status_row__strip_ends', C.c_int)
    assert ends() == 2  # at rest only the right end is cut
    sr_off = C.c_int.from_address(C.addressof(C.c_char.in_dll(fw.lib, 'status_row__sr')) + fw.sizes['status_row__sr'] - 8)  # sr.off, before marq_at
    sr_off.value = 12
    assert ends() == 3  # mid-slide both are
    sr_off.value = 30
    assert ends() == 1  # at its end, the left


def test_a_glyph_scrolled_out_of_the_strip_takes_no_tap(fw, row):
    crowd(fw, row)
    at = fw.fn('status_slot_at', C.c_int, C.c_int, C.c_int)
    assert at(ROOM + 7, 13) == SLOT_OFFLINE
    assert at(BODY - GAP - 5, 13) not in (SLOT_PLAY,)  # the bars' place by the battery, scrolled away at rest


def test_the_marquee_runs_on_the_shared_loop_repainting_only_the_strip(fw, row):
    crowd(fw, row)
    now = [1000]
    fw.mock('time_now_ms', lambda *_: now[0])
    row.sync_sig()
    loops = (C.c_void_p * 4).in_dll(fw.lib, 'ui_kit__loops')
    frame = fw.fn('status_row__sr_strip_frame', C.c_int)
    assert C.cast(fw.lib.status_row__sr_strip_frame, C.c_void_p).value in list(loops)
    row.dirty.clear()
    assert frame() == 1 and row.dirty == []  # at rest: nothing to repaint
    now[0] += 2750 + 400
    assert frame() == 1 and row.dirty == [(ROOM, 0, BODY - GAP - ROOM, 26)]
    fw.var('g_keylock_flag', C.c_ubyte).value = 0
    bt_link(fw)
    assert frame() == 0  # it fits again: the loop drops it


def test_a_server_out_of_reach_shows_the_offline_glyph_first_after_the_clock(fw, row):
    """Phosphor cloud-slash (bold) in Warning, the leftmost slot, while a running app says reach=down and Wi-Fi is
    connected; with no Wi-Fi the missing fan says why, so it hides."""
    row.status = 0
    row.wifi(3)
    row.app(0)
    row.ap.a[0].reach = 1
    assert row.left() == BODY - GAP - WIFI_W - GAP - OFFLINE_W
    canvas, names = row.paint()
    assert names == ['oled_status_wifi_3_d', 'oled_status_offline_d']
    assert canvas.images[-1] == (BODY - GAP - WIFI_W - GAP - OFFLINE_W, CY - 7, OFFLINE_W, 14)
    assert row.sync_sig() == 1 and row.sync_sig() == 0
    row.wifi(None)
    assert row.left() == BODY and row.sync_sig() == 1
    row.wifi(3)
    row.ap.a[0].launched = 0  # a stopped app says nothing
    assert row.left() == BODY - GAP - WIFI_W


class Later(C.Structure):
    _fields_ = [('glyph', C.c_char * 32), ('text', C.c_char * 96)]


@pytest.mark.parametrize('raw, toast', [(b'\nreach=down 502\nsrverr=1700000000 502\n', b'Server error 502'),
                                        (b'\nsrverr=1700000000 none\n', b'Server not answering'),
                                        (b'\nsrverr=1700000000 dns\n', b'Server not found'),
                                        (b'\nsrverr=1699999989 502\n', b''),  # older than 10 s: a demo started late
                                        (b'\nsrverr=1700000000 what\n', b''),
                                        (b'\nreach=down none\n', b'')])
def test_an_apps_server_error_toasts_once_with_the_cloud(fw, row, raw, toast):
    row.wifi(3)
    row.app(0)
    fw.mock('fw_time', lambda *_: 1700000000)
    later = fw.var('feedback__later', Later)
    read = fw.fn('addon__reach_read', None, C.c_void_p, C.c_char_p)
    read(C.addressof(row.ap.a[0]), raw)
    assert later.text == toast and later.glyph == (b'srvtoast' if toast else b'')
    assert row.ap.a[0].reach == int(raw.startswith(b'\nreach=down'))
    later.text = b''
    read(C.addressof(row.ap.a[0]), raw)
    assert later.text == b''  # the same one again: said once


def test_no_toast_without_wifi(fw, row):
    row.app(0)
    fw.mock('fw_time', lambda *_: 1700000000)
    fw.fn('addon__reach_read', None, C.c_void_p, C.c_char_p)(C.addressof(row.ap.a[0]), b'\nsrverr=1700000000 502\n')
    assert fw.var('feedback__later', Later).text == b''


def test_the_percentage_gives_way_to_the_icon_where_the_glyphs_would_reach_the_clock(fw, st):
    """batt_room: from the battery's end to the strip's room past the clock at the row's left. Percent alone (42 px)
    and the play state's bars fit it; with Wi-Fi, a sync and the padlock too they do not, and the icon stays."""
    row = Row(fw, st, 'A Title')
    st('battery').value = 1
    st('pct_digit').value = st('pct_sign').value = 10
    fw.mock('canvas_measure_text', lambda *_: 150.0)  # the widest clock: a room of 375 - 62 - (62 + 150 + 7) = 94
    sync = lambda: fw.fn('navigation__bar_sync', None, C.c_void_p)(row.bar.addr)
    width = lambda: row.w['view_battery'].box[2]
    sync()
    percent = width()
    assert percent > O['BATT_NUB_W'] + 10  # the digits: the bars alone leave room (16 px of 94)
    row.wifi(3)
    row.app(1)
    fw.var('g_keylock_flag', C.c_ubyte).value = 1
    sync()
    assert width() > percent  # 42 + 75 px of glyphs: the icon, the set's body and its nub


@pytest.mark.parametrize('raw, kind, permille', [(b'\nsync=check\n', 1, 0), (b'\nsync=down 1 4\n', 2, 250),
                                                 (b'\nsync=down 5000000000 10000000000\n', 2, 499), (b'\nsync=down 9 4\n', 2, 1000),
                                                 (b'\nsync=down\n', 2, 0), (b'\nhold=1\n', 0, 0), (b'\nsync=idle\n', 0, 0)])
def test_an_apps_sync_key_reads_as_its_kind_and_permille(fw, row, raw, kind, permille):
    """Any unit: past 20 bits both halve together until the total fits (5e9 of 1e10 truncates to 499)."""
    row.app(0)
    fw.fn('addon__sync_read', None, C.c_void_p, C.c_char_p)(C.addressof(row.ap.a[0]), raw)
    got = C.c_int(-1)
    assert fw.fn('addon_sync', C.c_int, C.POINTER(C.c_int))(C.byref(got)) == kind
    assert (got.value if kind == 2 else 0) == permille


def test_a_download_outranks_a_check_and_a_stopped_app_says_nothing(fw, row):
    row.ap.n = 2
    row.ap.a[0].launched, row.ap.a[0].sync = 1, 1
    row.ap.a[1].launched, row.ap.a[1].sync, row.ap.a[1].sync_pm = 1, 2, 600
    got = C.c_int(0)
    assert fw.fn('addon_sync', C.c_int, C.POINTER(C.c_int))(C.byref(got)) == 2 and got.value == 600
    row.ap.a[1].launched = 0
    assert fw.fn('addon_sync', C.c_int, C.POINTER(C.c_int))(None) == 1


@pytest.mark.parametrize('out, width, art', [('BMW 6R81', 14 + 2 + 8 + 2 + 14, ['oled_status_guest', 'oled_status_relay_d', 'oled_status_headphones_d']),
                                            ('jack', 14, ['oled_status_guest'])])
def test_a_guest_playing_shows_its_phone_and_where_it_plays(fw, row, out, width, art):
    """While a guest plays (guest.c's state), the slot is its phone in the accent, and relayed to the Q2's Bluetooth
    device the relay mark and that device's glyph (a car's car; this one reports no Class: headphones)."""
    from test_guest import Rig
    rig = Rig(fw)
    rig.state(state='playing', out=out)
    rig.poll()
    assert fw.fn('status_row__headset_shown', C.c_int)() == width
    canvas = Canvas(fw)
    names: list = []
    load = canvas.load_image
    fw.mock('widget_load_image', lambda w, n, b, *_: names.append(C.string_at(n).decode()) or load(w, n, b))
    canvas.art = {n: (14, 14) for n in art}
    fw.fn('status_row__headset_paint', None, C.c_void_p, C.c_void_p, C.c_int, C.c_int)(row.bar.addr, canvas.addr, 200, CY)
    assert names == art


def test_a_dev_builds_stand_in_shows_wifi_and_a_charger_the_emulator_lacks(fw_dev):
    """--dev only: /mnt/data/q2-status.fake's keys stand in for the radio and a charger, so the emulator can show
    those slots; a key it does not name is -1, and stock's state stands."""
    files = Files(fw_dev, {})
    clock = [1000]
    fw_dev.mock('time_now_ms', lambda *_: clock[0])
    fake = fw_dev.fn('status_fake', C.c_int, C.c_char)
    files.files['/mnt/data/q2-status.fake'] = b'wifi=2 charge=1\n'
    assert (fake(b'w'), fake(b'c')) == (2, 1)
    files.files['/mnt/data/q2-status.fake'] = b'charge=0 wifi'
    assert fake(b'w') == 2  # read again only after STATUS_FAKE_MS
    clock[0] += 500
    assert (fake(b'w'), fake(b'c')) == (-1, 0)  # "wifi" with no value: none


def test_the_bars_move_smoothly_on_the_shared_loop_repainting_only_their_box_and_stop_when_paused(fw, xf):
    """The user's (08:35): smoother bars. They ride ui_kit's one 33 ms loop timer (no 125 ms timer of their own), each
    frame moving them a fraction of a pixel along lvl_h16's curve and repainting the play box alone; paused, the loop
    drops them and they hold."""
    xf.sync_sig()
    assert [ms for _, ms in xf.timers] == [33]
    loop = C.CFUNCTYPE(C.c_int, C.c_void_p)(xf.timers[0][0])
    before = xf.bars_of(0)
    xf.now += 33
    xf.dirty.clear()
    assert loop(None) == 8 and xf.dirty == [PLAY_BOX]
    after = xf.bars_of(0)
    assert after != before and max(abs(a[3] - b[3]) for a, b in zip(before, after)) <= 1.5
    assert any(b[3] != int(b[3]) for b in after)  # between whole pixels: vgcanvas draws the part
    xf.status = 3
    xf.dirty.clear()
    assert loop(None) == 7 and xf.dirty == []  # RET_REMOVE: nothing else loops
    held = xf.bars_of(0)
    xf.now += 500
    assert xf.bars_of(0) == held


def test_the_bars_stand_still_while_they_dissolve_and_move_on_from_there_after(fw, xf):
    xf.sync_sig()
    xf.now += 100
    frozen = xf.bars_of(0)
    xf.status = 3
    xf.sync_sig()
    xf.now += 100
    assert xf.bars_of(0) == frozen  # paused: the dissolving bars are where they stopped
    xf.status = 2  # resumed mid-dissolve: still until the dissolve back ends
    xf.sync_sig()
    xf.now += 100
    assert xf.bars_of(0) == frozen
    xf.now += 200
    xf.frame()  # the dissolve's last frame starts their clock again, from where it held
    xf.now += 33
    moved = xf.bars_of(0)
    assert moved != frozen and max(abs(a[3] - b[3]) for a, b in zip(frozen, moved)) <= 1.5  # no jump


def test_under_reduce_motion_the_bars_stand_still(fw, st, xf):
    st('reduce_motion').value = 1
    xf.sync_sig()
    still = xf.bars_of(0)
    xf.now += 400
    assert xf.sync_sig() == 0 and xf.bars_of(0) == still and 33 not in [ms for _, ms in xf.timers]


def state(fw):
    """status_row_state's lines for q2ui (q2ui_gate.c tell), as a dict."""
    b = C.create_string_buffer(200)
    n = fw.fn('status_row_state', C.c_uint, C.c_char_p, C.c_uint)(b, 200)
    text = b.raw[:n].decode()
    assert text.endswith('\n') and n == len(b.value)
    return dict(line.split('=', 1) for line in text.splitlines())


@pytest.mark.parametrize('way, po, link, level, cls, out, bt, btbatt', [
    (0, 0, None, None, None, 'none', 'none', '-1'),  # nothing in, nothing linked: the deaf ear
    (0, 1, None, None, None, 'wire', 'none', '-1'),  # the 3.5 mm jack
    (2, 0, None, None, None, 'wire', 'none', '-1'),  # a USB DAC
    (3, 0, None, None, None, 'other', 'none', '-1'),  # the car's USB audio
    (1, 0, 3, None, None, 'other', 'rune', '-1'),  # a headset linked that says no level
    (1, 0, 3, 72, 0x240404, 'other', 'headset', '72'),  # one that says its level
    (1, 0, 3, None, 0x200420, 'other', 'car', '-1'),  # a car
])
def test_q2ui_is_told_where_the_sound_goes_and_what_bluetooth_links(fw, row, way, po, link, level, cls, out, bt, btbatt):
    """q2ui draws its own status row while it has the screen; these are the states it cannot read itself
    (docs/ui-rewrite.md#cutover), by the same tests demo's row paints from: what the line says is what the slots
    above show."""
    row.way = way
    jacks(fw, po, 0)
    if link is not None:
        bt_link(fw, link, level, cls)
    said = state(fw)
    assert (said['out'], said['bt'], said['btbatt'], said['relay']) == (out, bt, btbatt, 'none')
    assert fw.fn('status_row__wired_shown', C.c_int)() == (14 if out == 'wire' else EAR_W if out == 'none' else 0)


def test_q2ui_is_told_of_the_lock_a_sync_a_server_out_of_reach_and_wifi_with_no_internet(fw, row):
    row.wifi(3)
    said = state(fw)
    assert (said['lock'], said['sync'], said['syncpm'], said['offline'], said['net']) == ('0', '0', '0', '0', '1')
    fw.var('g_keylock_flag', C.c_ubyte).value = 1
    row.app(2, 450)
    row.ap.a[0].reach = 1
    row.online = 0
    said = state(fw)
    assert (said['lock'], said['sync'], said['syncpm'], said['offline'], said['net']) == ('1', '2', '450', '1', '0')
