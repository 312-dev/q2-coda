"""patch/status_row.c: the status row's glyph slots, right to left the battery, the level bars, Wi-Fi, a card app's
sync, Bluetooth (its link, a headset's battery, a car) and Buttons Lock's padlock; their layout (the clock's edge), paint, taps and repaints."""
import ctypes as C
import struct

import pytest

from conftest import O
from test_addon import Ap
from test_nav_painters import Canvas, StatusRow, i32, scratch, st  # noqa: F401 (fixtures)

BODY = 322  # StatusRow's view_battery on the bar: the battery's body starts here
CY = 23  # OLED's status_y: the glyphs' centre row
SLOT_BATTERY, SLOT_PLAY, SLOT_WIFI, SLOT_SYNC, SLOT_HEADSET, SLOT_LOCK = range(6)
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
        self.timers: list = []
        fw.mock('timer_add', lambda fn, ctx, ms, *_: self.timers.append((fn, i32(ms))) or len(self.timers))
        self.dirty: list = []
        fw.mock('widget_invalidate_force', lambda w, r, *_: self.dirty.append(struct.unpack('<4i', C.string_at(r, 16)) if r else None))
        fw.var('g_backlight_status', C.c_ubyte).value = 1
        self.ap = fw.var('addon__ap', Ap)
        sr = C.c_char.in_dll(fw.lib, 'status_row__sr')  # StatusRow's first sync ran under its own timer_add
        C.memset(C.addressof(sr), 0, fw.sizes['status_row__sr'])

    def wifi(self, level):
        node = self.w['img_wifi']
        node.visible = int(level is not None)
        node.props['image'] = f'bar_wifi{level}'

    def app(self, sync, permille=0):
        self.ap.n = 1
        self.ap.a[0].launched, self.ap.a[0].sync, self.ap.a[0].sync_pm = 1, sync, permille

    def left(self):
        return self.fw.fn('status_slots_left', C.c_int)()

    def paint(self):
        canvas = Canvas(self.fw)
        names: list = []
        load = canvas.load_image
        def loaded(w, name, bitmap, *_):
            names.append(C.string_at(name).decode())
            return load(w, name, bitmap)
        self.fw.mock('widget_load_image', loaded)
        canvas.art = {n: (14, 14) for n in ('oled_status_wifi_3_d', 'oled_status_wifi_2_d', 'oled_status_wifi_1_d', 'oled_status_wifi_x_d',
                                            'oled_keylock_d', 'oled_status_bt_d', 'oled_status_down_d')}
        canvas.art |= {f'oled_status_sync_{f}_d': (14, 14) for f in range(8)}
        self.fw.fn('status_slots_paint', None, C.c_void_p, C.c_void_p)(self.bar.addr, canvas.addr)
        return canvas, names

    def sync_sig(self):
        return self.fw.fn('status_row_sync', C.c_int)()


@pytest.fixture
def row(fw, st):
    return Row(fw, st)


def test_the_slots_lay_out_right_to_left_from_the_battery(fw, row):
    """Each shown slot's box, then its gap to the one on its right (mock_status_bars.py, mock_wifi_status.py,
    mock_sync_status.py): the bars 10 wide 4 left of the body, Wi-Fi 14 wide 5 left of them, a sync's ring 16 wide 4
    left, Buttons Lock's padlock 13 wide 5 left."""
    assert row.left() == BODY - 4 - 10
    row.wifi(3)
    assert row.left() == BODY - 4 - 10 - 5 - 14
    row.app(1)
    assert row.left() == BODY - 4 - 10 - 5 - 14 - 4 - 16
    fw.var('g_keylock_flag', C.c_ubyte).value = 1
    assert row.left() == BODY - 4 - 10 - 5 - 14 - 4 - 16 - 5 - 13


@pytest.mark.parametrize('status, wifi, left', [(1, None, BODY), (0, 3, BODY - 5 - 14), (3, 0, BODY - 4 - 10),
                                                (2, 5, BODY - 4 - 10), (2, None, BODY - 4 - 10)])
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
    lv = '' if level is None else level
    files = Files(fw, {'/tmp/q2-btbatt': f'{HEAD} {lv} 48000 {cls or ""}\n'.encode()})
    fw.fn('bt_headset_battery', C.c_int)()  # its loop started; the file read at the next look
    now = [100000]
    fw.mock('time_now_ms', lambda *_: now[0])
    return files


@pytest.mark.parametrize('status, thread, level, cls, width, art', [
    (None, 0, None, None, 0, []),  # off or idle: nothing
    (1, 1, None, None, 14, ['oled_status_btdim_d']),  # connecting: the dim rune
    (3, 0, None, None, 14, ['oled_status_bt_d']),  # connected, no level: the white rune
    (3, 0, 72, 0x240404, 14 + 2 + 15 + 2, ['oled_status_headphones_d']),  # a headset's level: headphones, battery
    (3, 0, None, 0x200420, 14, ['oled_status_car_d']),  # a car
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


def test_the_slot_s_kind_repaints_the_row(fw, row):
    bt_link(fw, 1, None, None, 1)
    row.sync_sig()
    assert row.sync_sig() == 0
    bt_link(fw, 3, None, None, 0)
    assert row.sync_sig() == 1


def test_the_bars_stand_on_the_row_white_while_playing_and_grey_when_paused(fw, row):
    canvas, _ = row.paint()
    x = BODY - 4 - 10
    assert [b for b in canvas.boxes] == [((x + 4 * i, CY + 6 - h, 2, h), 0xffffffff, 1) for i, h in enumerate(BARS[0])]
    assert canvas.restored()
    row.status = 3
    canvas, _ = row.paint()
    assert [b[1] for b in canvas.boxes] == [0xffababab] * 3  # Text Secondary, at the same heights
    assert [b[0][3] for b in canvas.boxes] == list(BARS[0])


def test_the_bars_move_a_frame_a_tick_repainting_only_their_box_and_hold_when_paused(fw, row):
    assert row.sync_sig() == 1 and [ms for _, ms in row.timers] == [125]
    tick = C.CFUNCTYPE(C.c_int, C.c_void_p)(row.timers[0][0])
    assert tick(None) == 8 and row.dirty == [(BODY - 14, 0, 10, 26)]
    canvas, _ = row.paint()
    assert [b[0][3] for b in canvas.boxes] == list(BARS[1])
    row.status = 3  # paused: the timer ends, the frame stays
    assert tick(None) == 0
    canvas, _ = row.paint()
    assert [b[0][3] for b in canvas.boxes] == list(BARS[1]) and row.dirty == [(BODY - 14, 0, 10, 26)]
    assert row.sync_sig() == 1 and len(row.timers) == 1  # a new state, and nothing moves: no timer


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
    x = BODY - 4 - 10 - 5 - 14
    assert names == [art] and canvas.images == [(x, CY - 7, 14, 14)]


def test_a_sync_check_turns_the_arrows_and_a_download_fills_its_ring(fw, row):
    row.status = 0
    row.app(1)
    canvas, names = row.paint()
    assert names == ['oled_status_sync_0_d'] and canvas.images == [(BODY - 4 - 16 + 1, CY - 7, 14, 14)]
    assert row.sync_sig() == 1 and len(row.timers) == 1
    tick = C.CFUNCTYPE(C.c_int, C.c_void_p)(row.timers[0][0])
    assert tick(None) == 8 and row.dirty == [(BODY - 4 - 16, 0, 16, 26)]
    assert row.paint()[1] == ['oled_status_sync_1_d']
    row.app(2, 250)
    fw.mock('canvas_get_vgcanvas', lambda *_: 1)
    canvas, names = row.paint()
    assert names == ['oled_status_down_d'] and canvas.images == [(BODY - 4 - 16 + 4, CY - 4, 14, 14)]
    assert len(fw.calls('vgcanvas_arc')) == 2 and len(fw.calls('vgcanvas_stroke')) == 2  # the track, then the fill
    assert tick(None) == 0  # a download does not turn


def test_the_padlock_sits_where_buttons_lock_put_it(fw, row):
    """The 13 px padlock is drawn where the stock image widget centred it: its box's top a row above the
    glyph's half (16 on a 23 centre)."""
    fw.var('g_keylock_flag', C.c_ubyte).value = 1
    row.status = 0
    canvas, names = row.paint()
    assert names == ['oled_keylock_d'] and canvas.images == [(BODY - 5 - 13, CY - 7, 14, 14)]


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


@pytest.mark.parametrize('x, slot', [(BODY + 12, SLOT_BATTERY), (BODY - 9, SLOT_PLAY), (BODY + 2, SLOT_BATTERY), (BODY + 1, SLOT_PLAY),
                                     (BODY - 1, SLOT_PLAY), (BODY - 9 - 22, SLOT_PLAY), (BODY - 9 - 23, -1), (BODY + 12 + 22, -1)])
def test_a_touch_finds_the_nearest_slot_inside_its_target(fw, row, x, slot):
    """44 x 40 round each centre: the battery's body's (12 in), the bars' (5 in); where they meet, the nearer."""
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


def test_a_centred_clock_that_would_reach_the_slots_goes_beside_them(fw, row):
    fw.mock('canvas_measure_text', lambda *_: 250.0)  # centred, it would end at 313
    clock = row.place()
    edge = BODY - 4 - 10 - 6
    assert clock.box[0] + clock.box[2] - 2 == edge


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
