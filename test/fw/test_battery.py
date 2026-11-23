"""battery.c: Battery History's record on the card (a fall appends, an unplug starts again, its bound, a reload,
a damaged file), the log it writes for LocalSend to share, and its page: the graph, the reading the wheel is on and
Share Log."""
import calendar
import ctypes as C
import struct
import time

import pytest

from conftest import O, Files
from test_peq_page import Canvas  # the EQ graph's canvas: the rectangles filled and the texts drawn

RECORD, LOG = '/mnt/mmc/.q2pod/q2-battery', '/tmp/battery-log.txt'
FLASH = '/mnt/data/q2-battery'  # where releases before card_store.c kept it
MAGIC, ROWS = 0x31484251, 100
# Battery History's window and a toast's, each a zeroed widget (no parent, no name): OLED's toast and title checks read
# the window on top
_WINDOWS = C.create_string_buffer(0x400), C.create_string_buffer(0x400)
PAGE, TOAST = (C.addressof(w) for w in _WINDOWS)
struct.pack_into('<ii', _WINDOWS[0], O['W_W'], 375, 320)  # the page's window: the whole screen (full-bleed)
EVT_CLICK = O['EVT_CLICK']
SEL, COUNT = b'_ringnav_index', b'_ringnav_count'
NOON = calendar.timegm((2026, 11, 11, 12, 0, 0))  # the clock in UTC, as localtime here reads it
MOUNTS = b'/dev/mmcblk0p1 /mnt/mmc vfat rw 0 0\n'


class Q2:
    """The flash's and card's files, the wall clock (UTC as the local time), the cable and the level."""
    def __init__(self, fw, files=None):
        self.fw, self.files, self.now = fw, Files(fw, files), NOON
        self.files.files.setdefault('/proc/mounts', MOUNTS)  # the card in, unless a test takes it out
        fw.mock('fw_time', lambda *_: self.now)
        fw.mock('fw_localtime_r', self.local)
        self.tick = fw.fn('battery_tick', None, C.c_int, C.c_int)

    def local(self, at, out, *_):
        tm = time.gmtime(C.c_long.from_address(at).value)
        for k, v in enumerate([tm.tm_sec, tm.tm_min, tm.tm_hour, tm.tm_mday, tm.tm_mon - 1, tm.tm_year - 1900]):
            C.c_int.from_address(out + 4 * k).value = v
        return out

    def poll(self, level, cable=0, minutes=0):
        """charge_poll's pass, minutes after the last."""
        self.now += 60 * minutes
        self.tick(level, cable)

    def rows(self):
        """The record on the card: [(time, level)]."""
        data = self.files.files[RECORD]
        magic, n = struct.unpack_from('<II', data)
        assert magic == MAGIC and len(data) == 8 + 8 * n  # its head and its rows, no more
        return [struct.unpack_from('<II', data, 8 + 8 * k) for k in range(n)]

    def reboot(self):
        """A power-off and on: the payload's memory zeroed, the files kept."""
        bh = C.c_char.in_dll(self.fw.lib, 'battery__bh')
        C.memset(C.addressof(bh), 0, self.fw.sizes['battery__bh'])

    def log(self):
        out = C.create_string_buffer(8192)
        n = self.fw.fn('battery_log', C.c_uint, C.c_char_p, C.c_uint)(out, len(out))
        return out.raw[:n].decode()


@pytest.fixture
def q2(fw):
    return Q2(fw)


def test_each_fall_of_a_percent_is_a_row_and_one_write(fw, q2):
    q2.poll(90)
    q2.poll(90, minutes=5)
    q2.poll(89, minutes=5)
    q2.poll(89, minutes=5)
    q2.poll(87, minutes=5)
    assert q2.rows() == [(NOON, 90), (NOON + 600, 89), (NOON + 1200, 87)]  # a fall of two is one row
    assert len([a for _, a in fw.calls('fw_rename') if C.string_at(a[1]) == RECORD.encode()]) == 3


def test_a_level_back_up_a_little_is_no_row_and_no_write(fw, q2):
    q2.poll(80)
    q2.poll(79, minutes=1)
    writes = len(fw.calls('fw_rename'))
    q2.poll(81, minutes=1)  # the gauge's rebound
    q2.poll(79, minutes=1)
    assert q2.rows() == [(NOON, 80), (NOON + 60, 79)] and len(fw.calls('fw_rename')) == writes
    q2.poll(78, minutes=1)
    assert q2.rows()[-1] == (NOON + 240, 78)


def test_charging_keeps_the_history_and_the_unplug_starts_a_new_one(q2):
    q2.poll(50)
    q2.poll(40, minutes=60)
    q2.poll(60, cable=1, minutes=30)
    q2.poll(95, cable=1, minutes=60)
    assert q2.rows() == [(NOON, 50), (NOON + 3600, 40)]  # kept while on the charger
    q2.poll(95, minutes=1)
    assert q2.rows() == [(NOON + 9060, 95)]


def test_a_charge_no_larger_than_a_rebound_still_starts_a_new_history_at_the_unplug(q2):
    q2.poll(50)
    q2.poll(40, minutes=60)
    q2.poll(41, cable=1, minutes=5)  # on the charger for a moment
    q2.poll(41, minutes=1)
    assert q2.rows() == [(NOON + 3960, 41)]


def test_a_boot_on_the_charger_starts_at_the_unplug(q2):
    q2.poll(70, cable=1)
    assert RECORD not in q2.files.files
    q2.poll(71, minutes=10)
    assert q2.rows() == [(NOON + 600, 71)]


def test_a_full_discharge_stays_inside_its_bound(q2):
    for level in range(100, 0, -1):
        q2.poll(level, minutes=6)
        q2.poll(level + 2, minutes=1)  # rebounds never add a row
    assert [p for _, p in q2.rows()] == list(range(100, 0, -1))
    q2.poll(0, minutes=6)  # not a level: demo powers off first
    assert len(q2.rows()) == ROWS


def test_a_full_discharge_writes_each_record_as_far_as_its_rows(fw, q2):
    """Each row is one write of the record's head and rows: 41,200 bytes over a full discharge's 100 rows, where the
    whole 808-byte record each time was 80,800."""
    written = 0
    for level in range(100, 0, -1):
        q2.poll(level, minutes=6)
        written += len(q2.files.files[RECORD])
    assert len([a for _, a in fw.calls('fw_rename') if C.string_at(a[1]) == RECORD.encode()]) == ROWS
    assert written == 41200


def test_a_record_written_whole_by_an_earlier_release_reads(fw):
    rows = [(NOON, 60), (NOON + 600, 59)]
    whole = struct.pack('<II', MAGIC, 2) + b''.join(struct.pack('<II', *r) for r in rows) + bytes(8 * (ROWS - 2))
    q2 = Q2(fw, {RECORD: whole})
    q2.poll(58, minutes=20)
    assert q2.rows() == rows + [(NOON + 1200, 58)]


def test_the_history_survives_a_power_off(q2):
    q2.poll(60)
    q2.poll(59, minutes=10)
    q2.reboot()
    q2.poll(60, minutes=30)  # rested, the level reads a little higher
    q2.poll(58, minutes=5)
    assert q2.rows() == [(NOON, 60), (NOON + 600, 59), (NOON + 2700, 58)]


def test_a_charge_while_powered_off_starts_again(q2):
    q2.poll(30)
    q2.reboot()
    q2.poll(80, minutes=120)
    assert q2.rows() == [(NOON + 7200, 80)]


@pytest.mark.parametrize('damage', [
    lambda d: d[:-1],  # short
    lambda d: struct.pack('<I', 0) + d[4:],  # another layout
    lambda d: d[:4] + struct.pack('<I', ROWS + 1) + d[8:],  # more rows than it holds
    lambda d: d[:8 + 8 * 1 + 4] + struct.pack('<I', 61) + d[8 + 8 * 2:],  # a level that rose
    lambda d: d[:12] + struct.pack('<I', 101) + d[16:],  # over 100%
])
def test_a_damaged_record_is_ignored_and_the_next_poll_starts_again(fw, damage):
    first = Q2(fw)
    first.poll(60)
    first.poll(59, minutes=10)
    q2 = Q2(fw, {RECORD: damage(first.files.files[RECORD])})
    first.reboot()
    q2.poll(58, minutes=20)
    assert q2.rows() == [(NOON + 1200, 58)]


def test_the_log_dates_each_day_over_its_rows(fw, q2):
    q2.poll(99)
    q2.poll(98, minutes=75)
    q2.poll(97, minutes=12 * 60)  # 01:15 the next day
    fw.var('g_time24h_flag', C.c_ubyte).value = 1
    assert q2.log() == '11 Nov 2026\n99% | 12:00\n98% | 13:15\n\n12 Nov 2026\n97% | 01:15\n'
    fw.var('g_time24h_flag', C.c_ubyte).value = 0
    assert q2.log().split('\n')[1:3] == ['99% | 12:00 PM', '98% | 1:15 PM']


def test_a_12_hour_clock_reads_midnight_as_12_am_and_the_morning_as_am(fw, q2):
    q2.now = NOON - 12 * 3600
    q2.poll(99)
    q2.poll(98, minutes=9 * 60 + 5)
    fw.var('g_time24h_flag', C.c_ubyte).value = 0
    assert q2.log().split('\n')[1:3] == ['99% | 12:00 AM', '98% | 9:05 AM']


def test_a_clock_never_set_logs_the_time_since_the_start(q2):
    q2.now = 3600
    q2.poll(99)
    q2.poll(98, minutes=75)
    assert q2.log() == 'Clock not set: times since the start\n99% | +0:00\n98% | +1:15\n'


def test_a_clock_lost_after_the_start_logs_no_time_for_the_rows_since(fw, q2):
    fw.var('g_time24h_flag', C.c_ubyte).value = 1
    q2.poll(99)
    q2.now = 3600  # back at 1970: neither a time of day nor a time since the start
    q2.poll(98)
    assert q2.log() == '11 Nov 2026\n99% | 12:00\n98% | --:--\n'


def xs(fw, q2):
    """bh_x for each row and for now, over a plot from 0 to 300."""
    x = fw.fn('battery__bh_x', C.c_int, C.c_uint, C.c_int, C.c_int, C.c_uint, C.c_int)
    rows = q2.rows()
    sets = [a >= 1600000000 for a, _ in rows]  # CLOCK_SET's side
    forward = int(all(b >= a for (a, _), (b, _) in zip(rows, rows[1:])) and len(set(sets)) == 1)
    return [x(i, 0, 300, q2.now, forward) for i in range(len(rows) + 1)]


def test_the_graph_spreads_rows_kept_while_the_clock_was_unset_once_it_is_set(fw, q2):
    q2.now = 3600
    for k, level in enumerate((90, 80, 70)):
        q2.poll(level, minutes=60 * bool(k))
    q2.now = NOON  # the clock set since: now is no measure of these rows
    assert xs(fw, q2) == [0, 150, 300, 300]


def test_the_graph_runs_by_time_up_to_now(fw, q2):
    q2.poll(90)
    q2.poll(80, minutes=60)
    q2.now += 3600 * 2
    assert xs(fw, q2) == [0, 100, 300]


KEY_CENTER, KEY_PREV, KEY_NEXT = O['KEY_CENTER'], O['KEY_PREV'], O['KEY_NEXT']
TOP, BOT, BTN_CY, READ_CY = 84, 226, 263, 66  # battery.c BH_TOP, BH_BOT, BH_BTN_CY, BH_READ_CY: the screen's rows
CTA_H = 38  # ui_kit.h: the shared button's height
CLIENT_Y = 0  # the page's window on the screen, the whole of it (full-bleed): a view's y counts from it


class Page:
    """Power's Battery History page on stubs: its title, the graph's view, the wheel and Centre, a tap and the
    toasts."""
    def __init__(self, fw):
        self.fw, self.texts, self.toasts, self.boxes, self.visible = fw, {}, [], {}, {}
        self.buffers, self.lists, self.graph = [], [], None
        fw.mock('window_create', lambda *_: PAGE)
        self.top = PAGE  # the window on top: a toast's, while its modal loop runs
        fw.mock('window_manager_get_top_window', lambda *_: self.top)
        fw.mock('list_view_create', lambda parent, x, y, w, h, *_: self.widget(x, y, w, h))
        fw.mock('scroll_view_create', self.scroll)
        fw.mock('list_item_create', lambda *_: self.widget())
        fw.mock('hscroll_label_create', lambda *_: self.widget())
        fw.mock('widget_set_text_utf8', lambda w, s, *_: self.texts.__setitem__(w, C.string_at(s).decode()))
        fw.mock('widget_get_text_utf8', self.get_text)  # an empty page's note reads its caption back (page_empty)
        fw.mock('widget_get_prop_int', lambda w, key, default, *_: 290 if C.string_at(key) == b'h' else default)
        fw.mock('widget_set_visible', lambda w, on, *_: self.visible.__setitem__(w, on))
        fw.mock('widget_factory_create_widget', self.made)
        fw.mock('navigator_to_with_context', lambda page, info, *_: self.toasts.append(C.string_at(info + 8).decode()))
        fw.mock('navigator_window_is_exist', lambda *_: 0)
        fw.mock('fw_statfs', self.statfs)
        self.key_fn = fw.fn('battery_key', C.c_int, C.c_void_p, C.c_uint)
        self.free = 64 << 20

    def widget(self, x=0, y=0, w=0, h=0):
        self.buffers.append(C.create_string_buffer(0x400))
        p = C.addressof(self.buffers[-1])
        struct.pack_into('<iiii', self.buffers[-1], 0, x, y, w, h)
        self.boxes[p] = (x, y, w, h)
        return p

    def scroll(self, parent, x, y, w, h, *_):
        p = self.widget(x, y, w, h)
        self.lists.append(p)
        C.c_void_p.from_address(p + O['W_PARENT']).value = parent
        return p

    def made(self, factory, kind, parent, x, y, *_):
        """The graph's view: its size is a sixth and seventh argument, which a stub doesn't pass on."""
        self.graph = self.widget(x, y)
        return self.graph

    def get_text(self, w, out, n, *_):
        data = self.texts.get(w, '').encode()[:n - 1] + b'\0'
        C.memmove(out, data, len(data))
        return 0

    def statfs(self, path, out, *_):
        C.c_uint.from_address(out + 4).value, C.c_uint.from_address(out + 28).value = 1, self.free
        return 0

    def open(self):
        assert self.fw.fn('battery_history', C.c_int, C.c_void_p, C.c_void_p)(None, None) == 0

    def key(self, key, top=PAGE):
        return self.key_fn(top, key)

    def touch(self, x, y):
        (fn, ctx), = [(a[2], a[3]) for _, a in self.fw.calls('widget_on') if a[0] == PAGE and a[1] == O['EVT_POINTER_UP_BEFORE']]
        event = C.create_string_buffer(0x100)
        C.c_int.from_address(C.addressof(event) + O['EVENT_X']).value = x
        C.c_int.from_address(C.addressof(event) + O['EVENT_Y']).value = y
        return C.CFUNCTYPE(C.c_int, C.c_void_p, C.c_void_p)(fn)(ctx, C.addressof(event))


def test_the_page_titles_the_bar_and_gives_the_rest_to_the_graph(fw, q2):
    q2.poll(99)
    q2.poll(98, minutes=10)
    page = Page(fw)
    page.open()
    assert 'Battery History' in page.texts.values()
    lv = C.c_void_p.from_address(page.lists[-1] + O['W_PARENT']).value  # the list, the scroll view's parent
    assert page.visible.get(lv) == 0  # no rows: the list only titles the bar
    (_, kind, parent, x, y, *_), = [a for _, a in fw.calls('widget_factory_create_widget')]
    assert parent == PAGE and (x, y) == (0, page.boxes[lv][1])


def origin(page, widget=None):
    """A widget of the page's window on the screen: its y and the window's."""
    return CLIENT_Y + page.boxes[widget or page.graph][1]


def paint(fw, widget, y=0):
    """ringnav_paint's call for one widget, on a canvas of its own whose origin is the widget's place on the screen, y:
    AWTK's canvas as it paints a widget."""
    canvas = Canvas(fw)
    struct.pack_into('<i', canvas.raw, O['CANVAS_Y'], y)
    fw.fn('battery_paint', None, C.c_void_p, C.c_void_p)(widget, canvas.p)
    return canvas


def graph(fw, page):
    """The open page painted: its three guide lines top to bottom, each row's level line left to right and the
    steps between them, as (x, y, w, h) on screen, the reading's hairline, and the canvas. 100% is TOP, 0% BOT."""
    dy = origin(page)
    canvas = paint(fw, page.graph, dy)
    rects = [(c, x, y + dy, w, h) for c, x, y, w, h in canvas.rects]
    guides = sorted((r[1:] for r in rects if r[4] == 1), key=lambda r: r[1])
    levels = sorted((r[1:] for r in rects if r[4] == 2), key=lambda r: r[0])
    steps = sorted(r[1:] for r in rects if r[3] == 2 and r[4] > 2)
    hair = [r[1:] for r in rects if r[3] == 1 and r[4] > 2]
    return guides, levels, steps, hair, canvas


def said(canvas, dy):
    """The texts drawn, each with its row's middle on screen."""
    return {t[0]: t[3][1] + t[3][3] // 2 + dy for t in canvas.texts}


def test_the_page_stands_where_the_mock_up_has_it_on_the_screen_clear_of_the_corners(fw, q2):
    """The user's mock-up of 2026-11-18 (docs/oled-design.md#battery-history), measured on the screen, the canvas at the
    graph's own place: the readout's middle on y 66, the plot from y 84 to 226 and Share Log from y 244 to 282, the
    list end, so the glass's corners hide none of it. The view's y counts from its window, which stands under the
    status row: the device drew all of it 58 px low, the button's foot 6 px from the screen's edge."""
    q2.poll(99)
    q2.poll(98, minutes=10)
    page = Page(fw)
    page.open()
    (top, half, bottom), _, _, _, canvas = graph(fw, page)
    texts = said(canvas, origin(page))
    assert (texts['Nov 11, 12:10 PM \u00b7 98%'], top[1], bottom[1], texts['Share Log']) == (READ_CY, TOP, BOT, BTN_CY)
    assert texts['100%'] == TOP and texts['0%'] == BOT
    assert BOT - TOP >= 140 and BTN_CY - CTA_H // 2 - BOT >= 16  # a taller plot, the button lifted clear of its axis
    assert BTN_CY + CTA_H // 2 <= 282  # the button's foot on the list end, 38 px above the screen's edge
    page.touch(187, BTN_CY + CTA_H // 2 + 1)  # just under the button: nothing
    assert 'lsshare_page' not in shared(fw)
    page.touch(187, BTN_CY + CTA_H // 2 - 1)  # the button's last row: where it is drawn is where it is tapped
    assert 'lsshare_page' in shared(fw)


def test_the_graph_steps_down_through_each_level_from_the_start_to_now(fw, q2):
    q2.poll(90)
    q2.poll(60, minutes=60)
    q2.now += 2 * 3600  # the second level has held twice as long as the first did
    page = Page(fw)
    page.open()
    (top, half, bottom), (first, last), steps, hair, canvas = graph(fw, page)
    texts = said(canvas, origin(page))
    assert {'100%', '50%', '0%', 'Share Log'} <= set(texts)
    assert (top[1], half[1], bottom[1]) == (TOP, (TOP + BOT) // 2, BOT) and top[0] == half[0] == bottom[0]
    left, width = top[0], top[2]
    assert [bottom[1] - (y + 1) for _, y, _, _ in (first, last)] == [90 * (BOT - TOP) // 100, 60 * (BOT - TOP) // 100]
    # from the plot's left edge to its right, the one step down a third of the way along
    assert first[0] == left and first[0] + first[2] - 1 == last[0] == left + width // 3
    assert last[0] + last[2] - 1 == left + width
    assert [(x + 1, y, y + h - 2) for x, y, _, h in steps] == [(last[0], first[1], last[1])]
    assert hair == [(last[0], TOP, 1, BOT - TOP + 1)]  # on the newest reading
    assert canvas.fill == canvas.lcd_fill  # the LCD's own fill back


@pytest.mark.parametrize('clock', [
    (3600, 7200, NOON, NOON + 3600),  # set mid-history
    (NOON, NOON + 3600, NOON - 3600, NOON + 1800),  # put back to an hour before the start, then on past it
])
def test_the_graph_spaces_the_rows_evenly_when_their_times_don_t_run_forward_on_one_clock(fw, q2, clock):
    for at, level in zip(clock, (90, 80, 70, 60)):
        q2.now = at
        q2.poll(level)
    page = Page(fw)
    page.open()
    (top, _, _), levels, _, _, _ = graph(fw, page)
    assert [x - top[0] for x, _, _, _ in levels] == [k * top[2] // 4 for k in range(4)]


@pytest.mark.parametrize('h24, newest, older', [
    (1, 'Nov 12, 00:30 \u00b7 98%', 'Nov 11, 12:00 \u00b7 99%'),
    (0, 'Nov 12, 12:30 AM \u00b7 98%', 'Nov 11, 12:00 PM \u00b7 99%'),
])
def test_the_wheel_moves_the_reading_and_the_readout_follows(fw, q2, h24, newest, older):
    fw.var('g_time24h_flag', C.c_ubyte).value = h24
    q2.poll(99)
    q2.poll(98, minutes=12 * 60 + 30)
    page = Page(fw)
    page.open()
    dy = origin(page)
    (top, _, _), (first, last), _, hair, canvas = graph(fw, page)
    assert said(canvas, dy).get(newest) == READ_CY and hair[0][0] == last[0]
    assert page.key(KEY_PREV) == 1  # anticlockwise: the older
    (top, _, _), _, _, hair, canvas = graph(fw, page)
    assert older in said(canvas, dy) and hair[0][0] == first[0] == top[0]
    assert page.key(KEY_PREV) == 1 and graph(fw, page)[3][0][0] == top[0]  # held at the first
    assert page.key(KEY_NEXT) == 1 and newest in said(graph(fw, page)[4], dy)
    assert page.key(KEY_NEXT, top=TOAST) == 0  # another window's key


def test_a_clock_never_set_reads_the_time_since_the_start(fw, q2):
    q2.now = 3600
    q2.poll(99)
    q2.poll(97, minutes=83)
    page = Page(fw)
    page.open()
    assert '+1:23 \u00b7 97%' in said(graph(fw, page)[4], 0)


def test_the_newest_reading_follows_a_new_row_and_an_older_one_stays(fw, q2):
    lit(fw, 1)
    q2.poll(99)
    q2.poll(98, minutes=10)
    page = Page(fw)
    page.open()
    invalid = len(fw.calls('widget_invalidate_force'))
    q2.poll(97, minutes=10)
    assert len(fw.calls('widget_invalidate_force')) > invalid  # the open page drawn again
    assert any(t.endswith('97%') for t in said(graph(fw, page)[4], 0))
    page.key(KEY_PREV)
    q2.poll(96, minutes=10)
    assert any(t.endswith('98%') for t in said(graph(fw, page)[4], 0))


def test_only_the_open_page_s_graph_is_painted_on(fw, q2):
    q2.poll(90)
    page = Page(fw)
    page.open()
    canvas = paint(fw, page.lists[-1])  # ringnav_paint passes every widget
    assert not canvas.rects and not canvas.texts
    (fn, ctx), = [(a[2], a[3]) for _, a in fw.calls('widget_on') if a[0] == PAGE and a[1] == O['EVT_DESTROY']]
    C.CFUNCTYPE(C.c_int, C.c_void_p, C.c_void_p)(fn)(ctx, None)
    for widget in (page.graph, None):  # the page gone, its graph's widget may be another's by now
        assert not paint(fw, widget).rects
    assert page.key(KEY_CENTER) == 0 and page.toasts == []


def test_no_history_says_to_unplug(fw, q2):
    q2.poll(70, cable=1)
    page = Page(fw)
    page.open()
    assert 'Unplug to start' in page.texts.values()
    assert not fw.calls('widget_factory_create_widget')  # no graph
    assert page.key(KEY_CENTER) == 0 and page.key(KEY_NEXT) == 0  # nothing to scrub or share


def shared(fw):
    """The names the payload's pages were given (page_open): LocalSend's share sheet's is lsshare_page."""
    return [C.string_at(a[1]).decode() for _, a in fw.calls('widget_set_name') if a[1]]


def test_centre_writes_the_log_to_tmp_and_opens_the_share_sheet(fw, q2):
    """Share Log (docs/localsend.md#sending): the log whole in /tmp, never the card, then the share sheet."""
    q2.poll(99)
    q2.poll(98, minutes=60)
    page = Page(fw)
    page.open()
    assert page.key(KEY_CENTER) == 1
    assert q2.files.files.get(LOG) == q2.log().encode() and LOG + '.tmp' not in q2.files.files
    assert not [f for f in q2.files.files if f.startswith('/mnt/mmc/battery-log')]
    assert 'lsshare_page' in shared(fw) and page.toasts == []


def test_a_log_that_cannot_be_written_says_so_and_shares_nothing(fw, q2):
    q2.poll(99)
    page = Page(fw)
    q2.files.fail_rename = True
    unlinked = []  # read while the call runs: the path is battery_log_write's stack buffer, gone once it returns
    fw.mock('fw_unlink', lambda path, *_: unlinked.append(C.string_at(path).decode()) or q2.files.files.pop(unlinked[-1], None) and 0)
    page.open()
    page.key(KEY_CENTER)
    assert page.toasts == ["Couldn't write the log"] and LOG not in q2.files.files
    assert unlinked == [LOG + '.tmp']
    assert 'lsshare_page' not in shared(fw)


def test_a_tap_on_the_button_shares_and_elsewhere_does_not(fw, q2):
    q2.poll(99)
    page = Page(fw)
    page.open()
    graph(fw, page)  # painted: the button's place known
    page.touch(187, 120)  # the graph
    assert 'lsshare_page' not in shared(fw)
    page.touch(187, BTN_CY)
    assert 'lsshare_page' in shared(fw)


def lit(fw, on):
    fw.var('g_backlight_status', C.c_ubyte).value = on


def test_the_button_gives_way_to_a_toast(fw, q2):
    """The toasts show in the button's place (y 236 to 268): while one is on top, no button, and no tap on it."""
    q2.poll(99)
    page = Page(fw)
    page.open()
    texts = said(graph(fw, page)[4], 0)
    assert 'Share Log' in texts
    page.top = TOAST
    assert 'Share Log' not in said(graph(fw, page)[4], 0)
    page.touch(187, BTN_CY)
    assert page.toasts == []
    page.top = PAGE
    assert 'Share Log' in said(graph(fw, page)[4], 0)


def test_with_the_card_away_rows_are_kept_in_ram_and_written_when_it_is_back(fw, q2):
    q2.poll(90)
    q2.files.files.pop('/proc/mounts')  # USB storage, or the card out
    q2.poll(89, minutes=10)
    q2.poll(88, minutes=10)
    assert q2.rows() == [(NOON, 90)]  # the card's copy is the one before it went
    assert FLASH not in q2.files.files  # and nothing went to the flash instead
    q2.files.files['/proc/mounts'] = MOUNTS
    q2.poll(88, minutes=1)  # the next poll, no new row: the held one is written
    assert q2.rows() == [(NOON, 90), (NOON + 600, 89), (NOON + 1200, 88)]


def test_the_flash_record_is_read_with_no_card_and_copied_to_it_once(fw):
    first = Q2(fw)
    first.poll(70)
    first.poll(69, minutes=10)
    old = first.files.files.pop(RECORD)
    q2 = Q2(fw, {FLASH: old})
    first.reboot()
    q2.files.files.pop('/proc/mounts')
    q2.poll(69, minutes=1)  # boot with no card: the flash's history reads, and nothing is written
    assert q2.log().count('%') == 2 and RECORD not in q2.files.files
    q2.files.files['/proc/mounts'] = MOUNTS
    q2.poll(68, minutes=9)  # this Q2's clock: NOON + 600
    assert q2.rows() == [(NOON, 70), (NOON + 600, 69), (NOON + 600, 68)]
    assert q2.files.files[FLASH] == old  # left as it was, never written again
