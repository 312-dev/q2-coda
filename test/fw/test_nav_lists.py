"""patch/navigation.c's list engine: the wheel's pane, its rows, position memory and reveal, Centre's
confirm, the ends that stop it, the carousel's step, pull to search, the time zone rows, and what is
painted over a list (the chevrons, the fast-scroll letter, the art's corners)."""
import ctypes as C
import json
import math
import re
import sqlite3
import struct
import threading
import time
from collections import Counter
from typing import Callable

import pytest

import host
from conftest import NAVIGATION_SOURCE, O, Deques, Files, block, claim, in_fork, page_block, stock_prop_str, stock_start_with, ui_name
from test_coverflow import COVERS, TRACKS, Home, Rec, cf, deque_of  # coverflow.c's home struct, its page and an album
from test_nav_display_library import Ple  # playlists.c's editor state
from test_navigation import Config
from test_tilt import Tilt  # tilt.c's state
from test_corners import art as art_py  # tools/art.py: corner_cover, the corners' coverage

W_PARENT, W_EMITTER = O['W_PARENT'], O['W_EMITTER']
KEY_NEXT, KEY_PREV, KEY_CENTER, KEY_RETURN = O['KEY_NEXT'], O['KEY_PREV'], O['KEY_CENTER'], O['KEY_RETURN']
EVT_CLICK = O['EVT_CLICK']
SCROLL_X, SCROLL_Y = O['SCROLL_X'], O['SCROLL_Y']
# navigation.c's own tuning, read from its source so a retune moves the tests with it
NAV = {k: int(v) for k, v in re.findall(r'^#define (\w+) (\d+)\b', NAVIGATION_SOURCE, re.M)}
SEL, COUNT = b'_ringnav_index', b'_ringnav_count'
OLED, = host.tools_theme.builtins()  # assets/themes/oled/theme.xml, resolved: the one built-in theme


class Widget:
    """One AWTK widget: a zeroed block holding what the C reads at fixed offsets (its box, parent,
    emitter and view fields); its type, props, text and children are the stubs' to answer."""
    def __init__(self, kind, parent=None, box=(0, 0, 0, 0), name=None, text=None, click=False, visible=True):
        self.block = C.create_string_buffer(0x200)
        self.at = C.addressof(self.block)
        self.kind, self.children, self.visible, self.text = kind.encode(), [], visible, text
        self.props = {} if name is None else {b'name': name.encode()}
        struct.pack_into('<4i', self.block, 0, *box)
        self.parent = parent
        if parent is not None:
            struct.pack_into('<Q', self.block, W_PARENT, parent.at)
            parent.children.append(self)
        if click:  # an EVT_CLICK handler in its emitter: a tap target
            self.emitter, self.item = C.create_string_buffer(0x10), C.create_string_buffer(0x30)
            struct.pack_into('<i', self.item, O['EMIT_TYPE'], EVT_CLICK)
            struct.pack_into('<Q', self.emitter, 0, C.addressof(self.item))
            struct.pack_into('<Q', self.block, W_EMITTER, C.addressof(self.emitter))

    def int(self, offset, value=None):
        if value is not None:
            struct.pack_into('<i', self.block, offset, value)
        return struct.unpack_from('<i', self.block, offset)[0]

    def byte(self, offset, value=None):
        if value is not None:
            self.block[offset] = value
        return self.block[offset][0]

    def ptr(self, offset, value=None):
        if value is not None:
            struct.pack_into('<Q', self.block, offset, value)
        return struct.unpack_from('<Q', self.block, offset)[0]

    @property
    def box(self):
        return struct.unpack_from('<4i', self.block, 0)

    def origin(self):
        """Its top-left on screen: the boxes summed up to the window manager."""
        x = y = 0
        w = self
        while w is not None and w.kind != b'window_manager':
            x, y, w = x + w.box[0], y + w.box[1], w.parent
        return x, y


class Ui:
    """AWTK's window manager, widgets and timers over Widgets, and the clock (time_now_ms)."""
    def __init__(self, fw):
        self.fw, self.by, self.strings = fw, {}, {}
        self.wm = self.widget('window_manager')
        self.top = None
        self.animating = self.pressed = 0
        self.now = 10000
        self.glides, self.glide_arrives = [], True
        self.timers, self.next_timer, self.invalidated = {}, 1, []
        mock = fw.mock
        mock('window_manager', lambda *_: self.wm.at)
        mock('window_manager_get_top_window', lambda *_: self.top.at if self.top else 0)
        mock('window_manager_is_animating', lambda *_: self.animating)
        mock('window_manager_get_pointer_pressed', lambda *_: self.pressed)
        mock('widget_get_type', lambda w, *_: self.cstr(self.by[w].kind) if w in self.by else 0)
        mock('widget_get_prop_str', self.prop_str)
        mock('widget_get_prop_int', self.prop_int)
        mock('widget_get_prop_bool', self.prop_int)
        mock('widget_set_prop_int', lambda w, name, v, *_: self.by[w].props.__setitem__(C.string_at(name), C.c_int(v).value))
        mock('widget_set_prop_str', lambda w, name, v, *_: self.by[w].props.__setitem__(C.string_at(name), C.string_at(v)))
        mock('widget_get_visible', lambda w, *_: w in self.by and self.by[w].visible)
        mock('widget_count_children', lambda w, *_: len(self.by[w].children) if w in self.by else 0)
        mock('widget_get_child', self.child)
        mock('widget_get_text', self.text)
        mock('widget_lookup', lambda w, name, *_: self.lookup(self.by.get(w), C.string_at(name)))
        mock('widget_invalidate_force', lambda w, *_: self.invalidated.append(w))
        mock('timer_add', self.timer_add)
        mock('timer_remove', lambda t, *_: self.timers.pop(t, None) and 0)
        mock('time_now_ms', lambda *_: self.now)
        mock('scroll_view_set_offset', self.set_offset)
        mock('scroll_view_scroll_delta_to', self.glide)
        mock('tk_str_start_with', stock_start_with)

    def widget(self, kind, parent=None, box=(0, 0, 0, 0), **kwargs):
        w = Widget(kind, parent, box, **kwargs)
        self.by[w.at] = w
        return w

    def cstr(self, value):
        if value not in self.strings:
            self.strings[value] = C.create_string_buffer(value)
        return C.addressof(self.strings[value])

    def prop_str(self, w, name, default, *_):
        widget, key = self.by.get(w), C.string_at(name)
        if widget is None:
            return default  # stock: widget_get_prop fails on a NULL widget
        if key == b'text':  # the label's text, which stock answers as a wide string: NULL
            return 0
        value = stock_prop_str(widget.kind, key, widget.props.get(key), default)
        return 0 if value is None else self.cstr(value) if isinstance(value, bytes) else value

    def prop_int(self, w, name, default, *_):
        value = self.by[w].props.get(C.string_at(name)) if w in self.by else None
        return value if isinstance(value, int) else C.c_int(default).value

    def child(self, w, i, *_):
        children = self.by[w].children if w in self.by else []
        return children[i].at if 0 <= i < len(children) else 0

    def text(self, w, *_):
        widget = self.by.get(w)
        if widget is None or widget.text is None:
            return 0
        return self.cstr(widget.text.encode('utf-32-le') + b'\0' * 4)

    def lookup(self, w, name):
        for c in w.children if w else ():
            if c.props.get(b'name') == name:
                return c.at
            found = self.lookup(c, name)
            if found:
                return found
        return 0

    def set_offset(self, w, x, y, *_):
        self.by[w].int(SCROLL_X, x)
        self.by[w].int(SCROLL_Y, y)

    def glide(self, w, dx, dy, ms, *_):
        """scroll_view's animated scroll by dy over ms: arrived at once, or (glide_arrives off) cut short."""
        self.glides.append((dy, ms))
        if self.glide_arrives:
            self.by[w].int(SCROLL_Y, self.by[w].int(SCROLL_Y) + C.c_int(dy).value)

    def timer_add(self, fn, ctx, ms, *_):
        t, self.next_timer = self.next_timer, self.next_timer + 1
        self.timers[t] = (fn, ctx, ms)
        return t

    def due(self, fn_name):
        """The live timers whose callback is navigation.c's static fn_name."""
        address = C.cast(getattr(self.fw.lib, ui_name(self.fw.lib, fn_name)), C.c_void_p).value
        return [t for t, (fn, _, _) in self.timers.items() if fn == address]

    def fire(self, t):
        """Timer t's callback, as the loop runs it when it is due; gone unless it asks to repeat (8)."""
        fn, ctx, _ = self.timers[t]
        result = C.CFUNCTYPE(C.c_int, C.c_void_p)(fn)(ctx)
        if result != 8:
            self.timers.pop(t, None)
        return result


ROW_H, VIEW_H = 30, 240
BAR = 58  # OLED's status row: a full-bleed page's content starts at its foot


def scroll_list(ui, parent, titles, box=(0, 0, 375, VIEW_H), subtitles=None):
    """A vertical scroll_view in parent with a row ROW_H high per title: a tappable list_item, its
    label's text the row's title, a second label its subtitle."""
    view = ui.widget('scroll_view', parent, box)
    view.byte(O['VIEW_VERTICAL'], 1)
    view.int(O['VIEW_CONTENT_H'], ROW_H * len(titles))
    for i, title in enumerate(titles):
        row = ui.widget('list_item', view, (0, ROW_H * i, box[2], ROW_H), click=True)
        ui.widget('label', row, (10, 0, 300, 20), text=title)
        if subtitles:
            ui.widget('label', row, (10, 20, 300, 10), text=subtitles[i])
    return view


class Page:
    """A list page on top: its window, a scroll_list of titles and the view's offset. The window is the whole
    screen and the list starts under the status row, as a built page's does (docs/oled-design.md#full-bleed)."""
    def __init__(self, ui, titles, name='allmusic_page', height=VIEW_H, subtitles=None, top=True):
        self.ui = ui
        self.win = ui.widget('window', ui.wm, (0, 0, 375, 320), name=name)
        self.view = scroll_list(ui, self.win, titles, (0, BAR, 375, height), subtitles)
        self.rows = self.view.children[:]
        if top:
            ui.top = self.win

    @property
    def selected(self):
        return self.view.props.get(SEL, -1)

    @property
    def offset(self):
        return self.view.int(SCROLL_Y)

    def scroll(self, y):
        self.view.int(SCROLL_Y, y)

    def screen_y(self, i):
        return ROW_H * i - self.offset

    def shows(self, i):
        """Row i whole in the viewport."""
        return 0 <= self.screen_y(i) and self.screen_y(i) + ROW_H <= self.view.box[3]


STOCK_BYTES = [O['KEY_LOCKOUT'], O['BOOT_KEY_GUARD'], O['RETURN_RELEASE_LATCH']]


@pytest.fixture
def ui(fw):
    """A lit, unlocked Q2 whose stock key latches are clear; the latches are put back after."""
    for address in STOCK_BYTES + [O['SCAN_DONE']]:
        claim(address)
    saved = [C.string_at(a, 1) for a in STOCK_BYTES] + [C.string_at(O['SCAN_DONE'], 4)]
    for a in STOCK_BYTES:
        C.memset(a, 0, 1)
    C.memset(O['SCAN_DONE'], 0, 4)
    fw.var('g_backlight_status', C.c_ubyte).value = 1
    yield Ui(fw)
    for a, b in zip(STOCK_BYTES + [O['SCAN_DONE']], saved):
        C.memmove(a, b, len(b))


def key(fw, ui, code, at=None):
    """A key's release through the firmware's key filter (ringnav), at `at` ms."""
    if at is not None:
        ui.now = at
    event = C.create_string_buffer(0x40)
    struct.pack_into('<I', event, O['EVENT_KEY'], code)
    return fw.fn('ringnav', C.c_int, C.c_void_p, C.c_void_p)(None, event)


def paint(fw, page, canvas=None):
    """The list's own paint, which loads and settles its selection first (a canvas with no LCD draws
    nothing after that)."""
    canvas = canvas or C.create_string_buffer(0x100)
    fw.fn('navigation__paint_selection', None, C.c_void_p, C.c_void_p)(page.view.at, canvas)


def titles(n, start=0):
    return [f'Song {i:03d}' for i in range(start, start + n)]


def spin(fw, ui, code, ticks, gap):
    """ticks wheel detents gap ms apart; the selection after each."""
    seen = []
    for _ in range(ticks):
        key(fw, ui, code, ui.now + gap)
        seen.append(ui.top.children[0].props.get(SEL, -1))
    return seen


SLOW = NAV['WHEEL_RUN_MS'] + 1  # past a run: each tick starts over


# The wheel on a list.
def test_a_fresh_list_takes_its_first_whole_row_then_the_tick_moves_on(fw, ui):
    page = Page(ui, titles(40))
    page.scroll(ROW_H * 3 + 5)  # row 3 is cut at the top: row 4 is the first whole one
    paint(fw, page)
    assert page.selected == 4
    key(fw, ui, KEY_NEXT)
    assert page.selected == 5 and page.offset == ROW_H * 4  # OLED's whole rows: row 4 lands on the top


@pytest.mark.parametrize('row', [5, 39])  # mid-list, and the last row: the tick meets the end
def test_ringnav_takes_the_wheels_posted_tick_as_the_row_steps(fw, ui, row):
    """playback.c wheel_part holds a whole tick for one posted and not yet taken, so the bar leans full until the
    step (tilt.c); the tick's release through ringnav takes it, and the lean the step paints with is the carry again
    (0 here), or full against the list's end (tilt_end): never the bare carry for a frame on either row."""
    page = Page(ui, titles(40))
    paint(fw, page)
    for _ in range(row):
        key(fw, ui, KEY_NEXT, ui.now + SLOW)
    pending, held = fw.var('playback__wh_pending', C.c_int), fw.var('wheel_held', C.c_int)
    held.value, pending.value = 1, 1
    fw.var('playback__wh_pending_dir', C.c_int).value = 1
    fw.var('playback__wh_units', C.c_int).value = 448
    fw.var('playback__wh_tick_at', C.c_uint).value = ui.now
    part, tilt = fw.fn('wheel_part', C.c_int), fw.var('tilt__tilt', Tilt)
    tilt.w, tilt.win = page.view.at, ui.top.at  # the bar tilt_bar last painted: this list's, in the top window
    fw.fn('tilt_poll', None)()  # the UI loop's pass before the key
    assert part() == 256 and tilt.p == 256
    key(fw, ui, KEY_NEXT, ui.now + 1)
    assert pending.value == 0 and part() == 0
    assert page.selected == min(row + 1, 39)
    assert tilt.p == (256 if row == 39 else 0)  # what the step's paint shows, before the next pass
    held.value = 0


def test_ticks_scroll_only_as_far_as_the_row_needs(fw, ui):
    page = Page(ui, titles(40))
    paint(fw, page)
    seen = []
    for _ in range(20):
        key(fw, ui, KEY_NEXT, ui.now + SLOW)
        seen.append((page.selected, page.offset, page.screen_y(page.selected)))
    assert [s for s, _, _ in seen] == list(range(1, 21))
    assert all(0 <= y and y + ROW_H <= VIEW_H for _, _, y in seen), seen
    moved = [i for i, (_, offset, _) in enumerate(seen) if offset]
    first = moved[0]
    assert all(seen[i][1] == seen[i - 1][1] + ROW_H for i in moved[1:])  # once it moves: a row a tick
    held = {y for _, _, y in seen[first:]}
    assert len(held) == 1  # the row stays put on screen as the list scrolls under it
    assert held.pop() + ROW_H < VIEW_H  # short of the bottom: a reading margin


def test_a_tick_up_from_the_top_row_of_a_scrolled_list_brings_it_in(fw, ui):
    page = Page(ui, titles(40))
    page.scroll(ROW_H * 10)
    paint(fw, page)
    assert page.selected == 10
    key(fw, ui, KEY_PREV, ui.now + SLOW)
    assert page.selected == 9 and page.shows(9) and page.screen_y(9) == 0  # whole on the top: OLED's whole rows keep no margin


def test_a_spin_steps_by_the_documented_ramp(fw, ui):
    first, more, cap, gap = NAV['LIST_FIRST_MS'], NAV['LIST_RAMP_MS'], NAV['WHEEL_MAX_STEP'], 100
    page = Page(ui, titles(400))
    paint(fw, page)
    key(fw, ui, KEY_NEXT, ui.now + SLOW)  # a run's first tick: it has spun 0 ms
    seen = [0, page.selected] + spin(fw, ui, KEY_NEXT, 24, gap)
    steps = [b - a for a, b in zip(seen, seen[1:])]
    # one row until the run has spun `first` ms, then two and one more each `more` ms, up to cap
    want = [1 if gap * k < first else min(cap, 2 + (gap * k - first) // more) for k in range(25)]
    assert steps == want and want[-1] == cap


@pytest.mark.parametrize('second', [KEY_NEXT, KEY_PREV])
def test_a_pause_or_a_turn_back_starts_the_ramp_over(fw, ui, second):
    page = Page(ui, titles(400))
    paint(fw, page)
    key(fw, ui, KEY_NEXT, ui.now + SLOW)
    spin(fw, ui, KEY_NEXT, 12, 100)
    before = page.selected
    gap = 100 if second == KEY_PREV else SLOW
    key(fw, ui, second, ui.now + gap)
    assert abs(page.selected - before) == 1


def test_a_short_list_always_steps_one_row(fw, ui):
    page = Page(ui, titles(NAV['SHORT_LIST_MAX']))
    paint(fw, page)
    seen = spin(fw, ui, KEY_NEXT, 12, 50)
    assert seen == list(range(1, 13))


def test_a_spin_shows_the_letter_until_it_lapses(fw, ui):
    page = Page(ui, titles(400))
    paint(fw, page)
    key(fw, ui, KEY_NEXT, ui.now + SLOW)
    gap = NAV['LIST_FIRST_MS'] // 3
    spin(fw, ui, KEY_NEXT, 2, gap)
    assert not ui.due('letter_expire')  # still one row a tick
    spin(fw, ui, KEY_NEXT, 1, gap)
    (timer,) = ui.due('letter_expire')
    assert ui.timers[timer][2] == O['LETTER_MS']
    ui.invalidated.clear()
    ui.fire(timer)
    assert ui.invalidated == [page.view.at]  # the letter goes with a repaint


@pytest.mark.parametrize('lit, lock, power', [(0, 0, 0), (1, 1, 0), (1, 0, 2)])
def test_a_tick_on_a_dark_locked_or_powering_off_screen_moves_nothing(fw, ui, lit, lock, power):
    page = Page(ui, titles(40))
    paint(fw, page)
    fw.var('g_backlight_status', C.c_ubyte).value = lit
    fw.var('g_lockscreen_pageflag', C.c_ubyte).value = lock
    fw.var('g_poweroff_state', C.c_ubyte).value = power
    key(fw, ui, KEY_NEXT, ui.now + SLOW)
    assert page.selected == 0


def test_a_page_outside_the_audited_windows_is_left_to_stock(fw, ui):
    page = Page(ui, titles(40), name='someone_elses_page')
    key(fw, ui, KEY_NEXT)
    key(fw, ui, KEY_NEXT, ui.now + SLOW)
    assert page.selected == -1 and page.offset == 0


# The ends.
def to_end(fw, ui, page):
    paint(fw, page)
    while page.selected < len(page.rows) - 1:
        key(fw, ui, KEY_NEXT, ui.now + SLOW)


# Ticks past an end at every spacing: inside a spin, a run's gap, and pauses up to a second.
PAST_END = (SLOW, 1, 300, 301, 1000, SLOW, 2000)


@pytest.mark.parametrize('name', ['allmusic_page', 'sortselect_dialog', 'playset_page', 'about_page'])
def test_every_list_stops_at_its_last_row_however_the_wheel_keeps_turning(fw, ui, name):
    page = Page(ui, titles(30), name=name)  # a music list, the hold menu, a settings list, a plain one
    to_end(fw, ui, page)
    offset = page.offset
    for gap in PAST_END:
        key(fw, ui, KEY_NEXT, ui.now + gap)
        assert page.selected == 29 and page.offset == offset  # no wrap, the view still


def test_every_list_stops_at_its_first_row(fw, ui):
    page = Page(ui, titles(30))
    paint(fw, page)
    key(fw, ui, KEY_NEXT, ui.now + SLOW)
    key(fw, ui, KEY_PREV, ui.now + SLOW)
    for gap in PAST_END:
        key(fw, ui, KEY_PREV, ui.now + gap)
        assert page.selected == 0 and page.offset == 0


def test_a_tick_at_the_end_repaints_the_list(fw, ui):  # a wheel wake still clears touch hiding
    page = Page(ui, titles(30))
    to_end(fw, ui, page)
    ui.invalidated.clear()
    key(fw, ui, KEY_NEXT, ui.now + 1000)
    assert page.selected == 29 and ui.invalidated == [page.view.at]


def test_turning_back_from_the_end_moves_at_once(fw, ui):
    page = Page(ui, titles(30))
    to_end(fw, ui, page)
    key(fw, ui, KEY_NEXT, ui.now + SLOW)
    key(fw, ui, KEY_PREV, ui.now + SLOW)
    assert page.selected == 28


def gapped(ui, n=10, at=8, gap=12, height=210):
    """A Page of n rows, a gap above row at (Power's before Restart), its view seven rows high."""
    page = Page(ui, titles(n), name='about_page', height=height)
    for row in page.rows[at:]:
        x, y, w, h = row.box
        struct.pack_into('<4i', row.block, 0, x, y + gap, w, h)
    page.view.int(O['VIEW_CONTENT_H'], ROW_H * n + gap)
    return page


def test_a_whole_row_list_with_a_gap_ends_on_a_whole_row(fw, ui, st):
    """OLED's whole rows: the view's end would cut a row at the top, so it stops on the next row's top
    and the content grows to allow it."""
    end, content = 4 * ROW_H, 4 * ROW_H + 210
    theme(fw)
    page = gapped(ui)
    to_end(fw, ui, page)
    assert page.offset == end and page.view.int(O['VIEW_CONTENT_H']) == content
    y = page.rows[9].box[1] - page.offset
    assert 0 <= y and y + ROW_H <= 210  # the last row whole
    key(fw, ui, KEY_PREV, ui.now + SLOW)
    key(fw, ui, KEY_PREV, ui.now + SLOW)  # Restart, then the row above the gap: still in view
    assert page.offset == end


@pytest.mark.parametrize('over', [1, 20, 40])
def test_a_page_with_no_rows_just_past_the_list_end_scrolls_as_far_as_a_drag(fw, ui, st, over):
    """A page with nothing to select (an info page's text) whose content runs `over` px past OLED's list end (y 282)
    but not past its box, which runs on to the screen's foot: the wheel scrolls as far as a drag does, to the
    content's foot on the list end (the user's, 2026-11-16, About on b4: the wheel showed the bar and stayed)."""
    theme(fw)
    bar, end = 58, 282  # OLED's status row and list end on screen
    win = ui.widget('window', ui.wm, (0, 0, 375, 320), name='about_page')  # full-bleed, as every page's window
    view = ui.widget('scroll_view', win, (0, bar, 375, 320 - bar))  # from the status row's foot
    view.byte(O['VIEW_VERTICAL'], 1)
    content, pad = end - bar + over, 320 - end
    ui.widget('label', view, (0, 0, 375, content), text='A long text')
    # as list_fade (the first paint) leaves it: the content padded by the box's rows past the end
    view.int(O['VIEW_CONTENT_H'], content + pad)
    view.props.update({b'_list_pad': pad, b'_list_padded': content + pad})
    ui.top = win
    for _ in range(4):
        key(fw, ui, KEY_NEXT, ui.now + SLOW)
    assert view.int(SCROLL_Y) == content + pad - (320 - bar) == over  # a drag's end: the content's foot on y 282


def home_page(ui, fw, st, rows=9, hidden=()):
    """Home's window under OLED: its main list's scroll view runs on to the window's foot, the rest's seven rows
    and two more; coverflow.c's home.list is its list."""
    theme(fw)
    win = ui.widget('window', ui.wm, (0, 58, 375, 262), name='home_page')
    lst = ui.widget('list_view', win, (0, 0, 375, 262))
    view = scroll_list(ui, lst, titles(rows), (0, 0, 375, 262))
    for i in hidden:
        view.children[i].visible = False
    ui.top = win
    fw.var('coverflow__home', Home).list = lst.at
    return view


@pytest.mark.parametrize('hidden, end', (((), 2 * ROW_H), ((8,), ROW_H)))
def test_home_s_list_rests_on_seven_rows_and_scrolls_the_rest(fw, ui, st, hidden, end):
    """sel_height is the rest's 7 rows, not the box's; the end is the shown rows' foot less them (home.c home_rest),
    whatever stock's layout keeps for a hidden row."""
    view = home_page(ui, fw, st, hidden=hidden)
    fw.fn('navigation__paint_selection', None, C.c_void_p, C.c_void_p)(view.at, C.create_string_buffer(0x100))
    seen = []
    for _ in range(9 - len(hidden) - 1):
        key(fw, ui, KEY_NEXT, ui.now + SLOW)
        seen.append((view.props.get(SEL), view.int(SCROLL_Y)))
    assert [o for _, o in seen] == [0] * 6 + [ROW_H, 2 * ROW_H][:len(seen) - 6]
    assert seen[-1] == (8 - len(hidden), end)
    assert all(0 <= ROW_H * s - o and ROW_H * (s + 1) - o <= 7 * ROW_H for s, o in seen)  # inside the rest


HOME_ROW, SPLIT_TOP, FULL_TOP = 32, 40, 58  # OLED's row pitch; Split's list top (coverflow.c HOME_SPLIT_TOP), Full's
CLOCK = (62, 17, 188, 28)  # the status row's clock in Split (x0, y0, x1, y1): its text 62 px in, its figures on rows 17 to 27


def split_home(ui, fw, rows, top=SPLIT_TOP, width=188):
    """Home as a Q2 shows it: the window the whole screen, the main list from top down to the screen's foot, rows
    HOME_ROW high (Paused or Playing first while a song is loaded: eight; seven without), and the status row."""
    theme(fw)
    win = ui.widget('window', ui.wm, (0, 0, 375, 320), name='home_page')
    lst = ui.widget('list_view', win, (0, top, width, 320 - top))
    view = ui.widget('scroll_view', lst, (0, 0, width, 320 - top))
    view.byte(O['VIEW_VERTICAL'], 1)
    view.int(O['VIEW_CONTENT_H'], HOME_ROW * rows)
    for i in range(rows):
        row = ui.widget('list_item', view, (0, HOME_ROW * i, width, HOME_ROW), click=True)
        ui.widget('label', row, (28, 0, 150, HOME_ROW), text=f'Row {i}')
    bar = ui.widget('system_bar', ui.wm, (0, 0, 375, FULL_TOP), name='system_bar')
    fw.var('fw_ctx_system_bar', C.c_void_p).value = bar.at
    ui.top = win
    fw.var('coverflow__home', Home).list = lst.at
    return win, view, bar


def selected_rect(view, top):
    """The selected row's box on the screen (x0, y0, x1, y1)."""
    y = top + HOME_ROW * view.props[SEL] - view.int(SCROLL_Y)
    return 0, y, view.box[2], y + HOME_ROW


def meets(a, b):
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


@pytest.mark.parametrize('rows', [7, 8])
@pytest.mark.parametrize('top', [SPLIT_TOP, FULL_TOP])
def test_homes_selected_row_never_meets_the_clock_as_the_wheel_walks_it(fw, ui, st, rows, top):
    """Down every row of Home's main list and back, seven rows or eight (a song loaded): the selected row always
    stands whole below the status row's clock (docs/oled-design.md, Home (Split) and Home (Full))."""
    _, view, _ = split_home(ui, fw, rows, top)
    fw.fn('navigation__paint_selection', None, C.c_void_p, C.c_void_p)(view.at, C.create_string_buffer(0x100))
    seen = [(view.props[SEL], selected_rect(view, top))]
    for code in [KEY_NEXT] * (rows - 1) + [KEY_PREV] * (rows - 1):
        key(fw, ui, code, ui.now + SLOW)
        seen.append((view.props[SEL], selected_rect(view, top)))
    assert [s for s, _ in seen] == list(range(rows)) + list(range(rows - 2, -1, -1))
    assert not [s for s, r in seen if meets(r, CLOCK) or r[1] < top], seen


@pytest.mark.parametrize('rows', [7, 8, 12])
def test_a_list_pages_selected_row_never_meets_the_clock(fw, ui, rows):
    """A page's list (Library, Settings) from the status row's foot: down every row and back by the wheel, and after
    a drag that scrolls its first rows under the status row, the selected row stands whole below the clock, which is
    centred on such a page."""
    page = Page(ui, titles(rows), height=320 - BAR)
    clock = (160, 17, 215, 28)
    paint(fw, page)
    seen = []
    for code in [KEY_NEXT] * (rows - 1) + [KEY_PREV] * (rows - 1):
        key(fw, ui, code, ui.now + SLOW)
        seen.append((page.selected, BAR + page.screen_y(page.selected)))
    page.scroll(ROW_H * 3)  # a drag's end: rows 0 to 2 under the status row
    paint(fw, page)
    seen.append((page.selected, BAR + page.screen_y(page.selected)))
    assert not [(s, y) for s, y in seen if y < BAR or meets((0, y, 375, y + ROW_H), clock)], seen


@pytest.mark.parametrize('rows', [7, 8])
def test_a_drag_that_moves_homes_selection_repaints_it_and_the_band(fw, ui, st, rows):
    """A drag that leaves Home's selected row under the status row: the list's next settled paint moves the
    selection to a row in sight (reconcile), and the frame after it repaints Home's page and the status row's band,
    so no bar stays drawn on the row under the clock. Home shows its bar while touch is the modality, and the paint
    that moves it may be one clipped to the art beside the list (Split's pan), which repaints neither."""
    win, view, bar = split_home(ui, fw, rows)
    paint_sel = fw.fn('navigation__paint_selection', None, C.c_void_p, C.c_void_p)
    due = fw.fn('list_reach_due', None)
    paint_sel(view.at, C.create_string_buffer(0x100))
    assert view.props[SEL] == 0
    end = HOME_ROW * (rows - 7)
    view.int(SCROLL_Y, end)  # the drag's end, the finger up: as far as the list scrolls (none with seven rows)
    due()  # the frame the drag ended on
    ui.invalidated.clear()
    paint_sel(view.at, C.create_string_buffer(0x100))  # the next paint, the art's
    assert not meets(selected_rect(view, SPLIT_TOP), CLOCK)
    moved = view.props[SEL] != 0
    assert moved == (rows == 8)
    due()  # before the next frame
    assert (win.at in ui.invalidated and bar.at in ui.invalidated) == moved, ui.invalidated


@pytest.mark.parametrize('sel, off', [(0, 15), (0, 8), (7, 0), (7, 20), (6, 1)])
def test_a_drag_that_stops_between_rows_leaves_homes_selection_whole_in_its_rest(fw, ui, st, sel, off):
    """A short drag stops Home's eight-row list between rows: the selected row, its bar shown under touch, moves
    to a row standing whole in the seven rows the list rests on (y 40 to 264 in Split) rather than staying cut
    under the status row's clock or out in the fade past the rest, where the wheel never leaves it."""
    _, view, _ = split_home(ui, fw, 8)
    paint_sel = fw.fn('navigation__paint_selection', None, C.c_void_p, C.c_void_p)
    paint_sel(view.at, C.create_string_buffer(0x100))
    for _ in range(sel):
        key(fw, ui, KEY_NEXT, ui.now + SLOW)
    assert view.props[SEL] == sel
    view.int(SCROLL_Y, off)  # the drag's end, the finger up
    paint_sel(view.at, C.create_string_buffer(0x100))
    r = selected_rect(view, SPLIT_TOP)
    assert SPLIT_TOP <= r[1] and r[3] <= SPLIT_TOP + 7 * HOME_ROW, (view.props[SEL], r)
    assert not meets(r, CLOCK)


@pytest.mark.parametrize('sel', [7, 6, 1])
def test_the_bands_paint_of_homes_rows_keeps_the_selection(fw, ui, st, sel):
    """The status row's band paints Home's scrolled rows with the view moved up under it (list_reach_paint): that
    paint draws the selected row the wheel left, whole in the rest, and never picks another by the moved view."""
    win, view, bar = split_home(ui, fw, 8)
    paint_sel = fw.fn('navigation__paint_selection', None, C.c_void_p, C.c_void_p)
    paint_sel(view.at, C.create_string_buffer(0x100))
    for _ in range(7):
        key(fw, ui, KEY_NEXT, ui.now + SLOW)
    for _ in range(7 - sel):
        key(fw, ui, KEY_PREV, ui.now + SLOW)
    assert view.props[SEL] == sel and view.int(SCROLL_Y) == HOME_ROW
    # stock's widget_paint of the view runs its background paint (ringnav_paint_bg), which paints the selection
    fw.mock('widget_paint', lambda w, canvas, *_: paint_sel(w, canvas) or 0)
    fw.fn('list_reach_paint', None, C.c_void_p, C.c_void_p, C.c_void_p)(bar.at, win.at, Canvas(fw).at)
    assert view.props[SEL] == sel and view.int(SCROLL_Y) == HOME_ROW


@pytest.mark.parametrize('cls', [O['CLASS_ALBUMS'], 0xff10])
def test_album_page_stops_at_its_end_as_albums_or_songs(fw, ui, cls):
    fw.var('fw_ctx_g_class_type', C.c_int).value = cls
    page = Page(ui, titles(30), name='album_page')
    to_end(fw, ui, page)
    for gap in PAST_END:
        key(fw, ui, KEY_NEXT, ui.now + gap)
    assert page.selected == 29


# Centre.
class Clicks:
    """Stock's dispatch of the click Centre confirms: the widgets clicked."""
    def __init__(self, fw):
        self.seen = []
        fw.mock('pointer_event_init', self.init)
        fw.mock('stock_dispatch_trampoline', lambda target, e, *_: self.seen.append((target, C.c_int.from_address(e).value)))

    @staticmethod
    def init(e, kind, *_):
        C.c_int.from_address(e).value = kind
        return e


def centre(fw, ui, page, row):
    paint(fw, page)
    while page.selected < row:
        key(fw, ui, KEY_NEXT, ui.now + SLOW)
    clicks = Clicks(fw)
    key(fw, ui, KEY_CENTER, ui.now + SLOW)
    return clicks


def test_centre_clicks_its_row_once_the_double_press_window_has_passed(fw, ui):
    page = Page(ui, titles(30))
    clicks = centre(fw, ui, page, 3)
    (confirm,) = ui.due('confirm_center')
    assert ui.timers[confirm][2] == O['DOUBLE_CLICK_MS'] and not clicks.seen
    C.memset(O['KEY_LOCKOUT'], 8, 1)
    ui.now += O['DOUBLE_CLICK_MS']
    ui.fire(confirm)
    assert clicks.seen == [(page.rows[3].at, EVT_CLICK)]
    assert C.string_at(O['KEY_LOCKOUT'], 1) == b'\0'  # the page it opens takes the wheel at once


def test_a_second_centre_in_the_window_leaves_the_press_to_stock(fw, ui):
    page = Page(ui, titles(30))
    clicks = centre(fw, ui, page, 3)
    key(fw, ui, KEY_CENTER, ui.now + O['DOUBLE_CLICK_MS'] - 1)
    assert not ui.due('confirm_center') and not clicks.seen


def test_a_row_rebound_before_the_confirm_is_not_clicked(fw, ui):
    page = Page(ui, titles(30))
    clicks = centre(fw, ui, page, 3)
    page.rows[3].children[0].text = 'Another song'  # the list was rebuilt under the press
    ui.fire(ui.due('confirm_center')[0])
    assert not clicks.seen


@pytest.mark.parametrize('flag', ['pressed', 'g_power_longkey', 'g_ingore_bootkey_flag', 'guard'])
def test_a_finger_down_or_a_power_key_cancels_the_click(fw, ui, flag):
    page = Page(ui, titles(30))
    clicks = centre(fw, ui, page, 3)
    if flag == 'pressed':
        ui.pressed = 1
    elif flag == 'guard':
        C.memset(O['BOOT_KEY_GUARD'], 1, 1)
    else:
        fw.var(flag, C.c_ubyte).value = 1
    ui.fire(ui.due('confirm_center')[0])
    assert not clicks.seen


def test_a_new_page_on_top_cancels_the_click(fw, ui):
    page = Page(ui, titles(30))
    clicks = centre(fw, ui, page, 3)
    confirm = ui.due('confirm_center')[0]
    Page(ui, titles(30))
    ui.fire(confirm)
    assert not clicks.seen


# Position memory: a recreated page opens where the user left it.
def chosen(fw, ui, page, row):
    """The user's pick on page: the wheel to row, then the page's paint."""
    paint(fw, page)
    while page.selected < row:
        key(fw, ui, KEY_NEXT, ui.now + SLOW)
    paint(fw, page)
    return page


def theme(fw, **keys):
    """config.ini's IPOD keys given, THEME 3 (OLED, the value theme_boot keeps); the rest missing, so
    their defaults."""
    values = {b'THEME': b'3', **{k.encode(): str(v).encode() for k, v in keys.items()}}
    def read(path, section, name, out, default, *_):
        value = values.get(C.string_at(name), C.string_at(default))
        C.memmove(out, value + b'\0', len(value) + 1)
    fw.mock('toolsReadConfig', read)


def test_a_recreated_page_glides_to_the_row_last_chosen_in_it(fw, ui):
    chosen(fw, ui, Page(ui, titles(40)), 25)
    ui.glides.clear()
    again = Page(ui, titles(40))
    paint(fw, again)
    assert again.selected == 25 and again.shows(25)
    (dy, ms), = ui.glides
    assert dy == again.offset and ms == O['GLIDE_MS']  # OLED's recall (GLIDE_MS) glides from the top


def test_a_reordered_list_restores_the_same_item(fw, ui):
    chosen(fw, ui, Page(ui, titles(40)), 25)
    again = Page(ui, titles(40)[::-1])
    paint(fw, again)
    assert again.selected == 39 - 25 and again.shows(39 - 25)


def test_the_second_line_tells_twin_titles_apart(fw, ui):
    names, artists = titles(40), ['A'] * 40
    names[5] = names[30] = 'Intro'
    artists[30] = 'B'
    chosen(fw, ui, Page(ui, names, subtitles=artists), 30)
    moved, moved_artists = titles(40), ['A'] * 40
    moved[2] = moved[29] = 'Intro'
    moved_artists[2] = 'B'  # the remembered artist's twin moved far; the other sits by the old place
    again = Page(ui, moved, subtitles=moved_artists)
    paint(fw, again)
    assert again.selected == 2


def test_twin_titles_without_a_second_line_take_the_nearer(fw, ui):
    names = titles(40)
    names[5] = names[30] = 'Intro'
    chosen(fw, ui, Page(ui, names), 30)
    moved = titles(40)
    moved[3] = moved[27] = 'Intro'
    again = Page(ui, moved)
    paint(fw, again)
    assert again.selected == 27


def test_a_list_with_no_row_of_that_name_keeps_the_place(fw, ui):
    chosen(fw, ui, Page(ui, titles(40)), 25)
    again = Page(ui, titles(40, start=100))
    paint(fw, again)
    assert again.selected == 25


def test_a_shorter_list_does_not_restore_past_its_end(fw, ui):
    chosen(fw, ui, Page(ui, titles(40)), 25)
    again = Page(ui, titles(10, start=100))
    paint(fw, again)
    assert again.selected == 0


def test_another_list_of_the_same_page_starts_at_its_top(fw, ui):
    chosen(fw, ui, Page(ui, titles(40)), 25)
    fw.var('fw_ctx_g_local_classinfo_save', C.c_ubyte * 912)[0x10] = 7  # another artist's songs
    again = Page(ui, titles(40))
    paint(fw, again)
    assert again.selected == 0 and again.offset == 0


@pytest.mark.parametrize('name, cls, kept', [
    ('localclass_page', O['CLASS_ALBUMS'], True),  # a root collection ignores the saved detail query
    ('localclass_page', 0xff10, False), ('album_page', O['CLASS_ALBUMS'], False),
])
def test_opening_an_artist_keeps_a_root_collections_row(fw, ui, name, cls, kept):
    fw.var('fw_ctx_g_class_type', C.c_int).value = cls
    chosen(fw, ui, Page(ui, titles(40), name=name), 25)
    fw.var('fw_ctx_g_local_classinfo_save', C.c_ubyte * 912)[0x10] = 7
    again = Page(ui, titles(40), name=name)
    paint(fw, again)
    assert again.selected == (25 if kept else 0)


def folder(fw, path):
    data = path.encode() + b'\0'
    C.memmove(C.addressof(fw.var('fw_ctx_g_folder_path', C.c_char)), data, len(data))


def test_each_folder_keeps_its_own_row(fw, ui):
    for path, row in (('/mnt/mmc/A', 5), ('/mnt/mmc/B', 9)):
        folder(fw, path)
        chosen(fw, ui, Page(ui, titles(20), name='folder_page'), row)
    for path, row in (('/mnt/mmc/A', 5), ('/mnt/mmc/B', 9), ('/mnt/mmc/C', 0)):
        folder(fw, path)
        again = Page(ui, titles(20), name='folder_page')
        paint(fw, again)
        assert again.selected == row


def test_a_folder_page_with_no_path_remembers_nothing(fw, ui):
    chosen(fw, ui, Page(ui, titles(20), name='folder_page'), 5)
    again = Page(ui, titles(20), name='folder_page')
    paint(fw, again)
    assert again.selected == 0


@pytest.mark.parametrize('name', ['search_dialog', 'wifi_page', 'coverflow_page'])  # Cover Flow: no album open
def test_a_page_without_a_content_key_never_imports_a_row(fw, ui, name):
    chosen(fw, ui, Page(ui, titles(20), name=name), 5)
    again = Page(ui, titles(20), name=name)
    paint(fw, again)
    assert again.selected == 0


def test_the_memory_holds_the_last_64_lists(fw, ui):
    for k in range(NAV['POS_MEM'] + 1):
        folder(fw, f'/mnt/mmc/{k}')
        chosen(fw, ui, Page(ui, titles(20), name='folder_page'), 1 + k % 7)
    recalled = []
    for k in (1, 0):  # the next oldest first: opening a forgotten one takes a slot
        folder(fw, f'/mnt/mmc/{k}')
        again = Page(ui, titles(20), name='folder_page')
        paint(fw, again)
        recalled.append(again.selected)
    assert recalled == [2, 0]  # the oldest went; the next oldest is still there


def test_a_list_left_by_return_opens_at_its_top_when_chosen_again(fw, ui):  # OLED's list.reopen.top
    theme(fw)
    folder(fw, '/mnt/mmc/A')
    chosen(fw, ui, Page(ui, titles(20), name='folder_page'), 6)
    folder(fw, '/mnt/mmc/A/B')
    chosen(fw, ui, Page(ui, titles(20), name='folder_page'), 4)
    key(fw, ui, KEY_RETURN)  # out of B, to A
    folder(fw, '/mnt/mmc/A')
    back = Page(ui, titles(20), name='folder_page')
    paint(fw, back)
    assert back.selected == 6  # the page Return lands on keeps its row
    folder(fw, '/mnt/mmc/A/B')
    again = Page(ui, titles(20), name='folder_page')
    paint(fw, again)
    assert again.selected == 0


def checked(ui, page, row):
    ui.widget('image', page.rows[row], (340, 0, 20, 20)).props[b'image'] = b'select'


def test_a_picker_opens_on_its_current_option(fw, ui):
    chosen(fw, ui, Page(ui, titles(8), name='playmode_page'), 4)
    again = Page(ui, titles(8), name='playmode_page')
    checked(ui, again, 2)
    paint(fw, again)
    assert again.selected == 2


def test_a_sliding_page_arrives_on_its_row_without_a_glide(fw, ui):
    chosen(fw, ui, Page(ui, titles(40)), 25)
    ui.glides.clear()
    again = Page(ui, titles(40))
    again.win.props[b'anim_hint'] = b'htranslate'
    paint(fw, again)
    assert again.selected == 25 and again.shows(25) and not ui.glides


def test_eq_s_list_lights_no_row_while_its_tabs_hold_the_wheel(fw, ui):
    """docs/internals.md#3eq: Advanced's tab bar takes the wheel above the list, whose row is -1 then; settling must
    leave it so, not light the first row in view beside the bar's segment (paint_selection, eq_tabs_hold)."""
    from test_peq_page import ADVANCED, HOME as EQ_HOME, Ui as EqUi
    page = Page(ui, titles(8), name='equalizer_page')
    eq = fw.var('peq_ui__ui', EqUi)
    eq.page, eq.view, eq.tab, eq.screen, eq.tabs = page.win.at, page.view.at, ADVANCED, EQ_HOME, 1
    page.view.props.update({SEL: -1, COUNT: 8})
    paint(fw, page)
    assert page.selected == -1
    eq.tabs = 0  # the wheel back on the list: ringnav settles a row as on any page
    paint(fw, page)
    assert page.selected == 0


def test_a_recall_glide_cut_short_is_asked_again_once(fw, ui):
    chosen(fw, ui, Page(ui, titles(40)), 25)
    ui.glides.clear()
    ui.glide_arrives = False
    again = Page(ui, titles(40))
    paint(fw, again)
    assert again.selected == 25 and len(ui.glides) == 2  # kept, and retried
    paint(fw, again)
    assert again.shows(again.selected)  # once only: then a row in view takes it


def test_a_recall_glide_that_arrives_on_its_retry_keeps_the_row(fw, ui):
    chosen(fw, ui, Page(ui, titles(40)), 25)
    again = Page(ui, titles(40))
    asked = []
    def glide(w, dx, dy, ms, *_):
        if asked:
            again.scroll(again.offset + C.c_int(dy).value)
        asked.append(dy)
    fw.mock('scroll_view_scroll_delta_to', glide)
    paint(fw, again)
    paint(fw, again)
    assert again.selected == 25 and again.shows(25) and len(asked) == 2


def test_a_swipe_keeps_the_row_while_it_moves_and_settles_on_the_middle_row(fw, ui):
    page = chosen(fw, ui, Page(ui, titles(40)), 2)
    page.scroll(10 * ROW_H)
    page.view.ptr(O['VIEW_ANIMATOR'], 0x1234)  # momentum
    paint(fw, page)
    assert page.selected == 2
    page.view.ptr(O['VIEW_ANIMATOR'], 0)
    paint(fw, page)
    middle = 10 + VIEW_H // ROW_H // 2  # rows 13 and 14 sit as near the middle: the earlier
    assert page.selected == middle - 1


def test_rows_taller_than_the_view_settle_on_one_partly_shown(fw, ui):
    page = chosen(fw, ui, Page(ui, titles(40), height=20), 0)
    page.scroll(5 * ROW_H + 10)
    paint(fw, page)
    assert page.selected == 5


def test_a_finger_down_does_not_recall(fw, ui):
    chosen(fw, ui, Page(ui, titles(40)), 25)
    ui.glides.clear()
    again = Page(ui, titles(40))
    again.scroll(ROW_H * 2 + 5)  # row 2 cut at the top
    ui.pressed = 1
    paint(fw, again)
    # as a list with nothing to recall: its first whole row, where the finger left it
    assert again.selected == 3 and again.offset == ROW_H * 2 + 5 and not ui.glides


# A dialog's buttons are its rows.
def dialog(ui, name='confirminfo_dialog', labels=('Cancel', 'OK')):
    win = ui.widget('dialog', ui.wm, (40, 100, 300, 120), name=name)
    buttons = [ui.widget('button', win, (20 + 140 * i, 70, 120, 40), click=True, text=t) for i, t in enumerate(labels)]
    ui.top = win
    return win, buttons


def test_a_confirm_dialogs_buttons_take_the_wheel_and_centre(fw, ui):
    win, buttons = dialog(ui)
    key(fw, ui, KEY_NEXT)
    assert win.props[SEL] == 1
    clicks = Clicks(fw)
    key(fw, ui, KEY_CENTER, ui.now + SLOW)
    ui.fire(ui.due('confirm_center')[0])
    assert clicks.seen == [(buttons[1].at, EVT_CLICK)]


def test_a_held_power_key_leaves_only_its_shutdown_prompt_the_wheel(fw, ui):
    fw.var('g_poweroff_state', C.c_ubyte).value = 1
    page = Page(ui, titles(10))
    key(fw, ui, KEY_NEXT)
    key(fw, ui, KEY_NEXT, ui.now + SLOW)
    assert page.selected == -1
    win, _ = dialog(ui)
    key(fw, ui, KEY_NEXT, ui.now + SLOW)
    assert win.props[SEL] == 1


@pytest.mark.parametrize('flag', ['g_power_longkey', 'g_ingore_bootkey_flag', 'guard'])
def test_the_release_that_ends_the_hold_leaves_the_shutdown_prompt_its_centre(fw, ui, flag):
    """V1.36's confirm keeps every Centre release from on_wm_keyup_fun, which cleared the hold's flags."""
    def flags():
        return (fw.var('g_power_longkey', C.c_ubyte).value, fw.var('g_ingore_bootkey_flag', C.c_ubyte).value,
                C.string_at(O['BOOT_KEY_GUARD'], 1)[0])

    def hold():
        if flag == 'guard':
            C.memset(O['BOOT_KEY_GUARD'], 1, 1)
        else:
            fw.var(flag, C.c_ubyte).value = 1

    fw.var('g_poweroff_state', C.c_ubyte).value = 1
    hold()
    Page(ui, titles(10))
    key(fw, ui, KEY_CENTER)
    assert any(flags())  # under any other window the release reaches stock, which clears them
    _, buttons = dialog(ui)
    clicks = Clicks(fw)
    key(fw, ui, KEY_CENTER, ui.now + SLOW)  # the hold's own release: no click
    assert flags() == (0, 0, 0) and not ui.due('confirm_center')
    key(fw, ui, KEY_NEXT, ui.now + SLOW)
    key(fw, ui, KEY_CENTER, ui.now + SLOW)
    ui.fire(ui.due('confirm_center')[0])
    assert clicks.seen == [(buttons[1].at, EVT_CLICK)]


@pytest.mark.parametrize('cable', [1, 0])
def test_usb_modes_scan_prompt_takes_the_wheel_cable_in_or_out(fw, ui, cable):
    fw.var('g_usblink_status', C.c_ubyte).value = 2
    fw.var('g_usbdet_value', C.c_ubyte).value = cable
    win, _ = dialog(ui)
    key(fw, ui, KEY_NEXT)
    assert win.props.get(SEL) == 1


# A virtual table: a pool of row widgets rebound to the rows in view.
class Table:
    """A table_client of n logical rows ROW_H high on page name, drawn by a pool of table_rows that its
    offset setter rebinds (ROW_INDEX each, -1 past the end). Full-bleed, as Page's: the window is the screen, the
    client under the status row."""
    def __init__(self, fw, ui, n, pool=10, name='allmusic_page'):
        self.ui, self.n, self.names = ui, n, titles(n)
        self.win = ui.widget('window', ui.wm, (0, 0, 375, 320), name=name)
        self.client = self.view = ui.widget('table_client', self.win, (0, BAR, 375, VIEW_H))
        self.client.int(O['ROW_HEIGHT'], ROW_H)
        self.client.int(O['TABLE_ROWS'], n)
        self.pool = []
        for _ in range(pool):
            row = ui.widget('table_row', self.client, (0, 0, 375, ROW_H), click=True)
            ui.widget('label', row, (10, 0, 300, 20))
            self.pool.append(row)
        self.bind(0)
        fw.mock('table_client_set_yoffset', lambda w, top, *_: self.bind(top))
        ui.top = self.win

    def bind(self, top):
        self.client.int(O['TABLE_TOP'], top)
        first = top // ROW_H
        for k, row in enumerate(self.pool):  # table_client's relayout: child k holds the k-th row in view
            i = first + k
            row.int(O['ROW_INDEX'], i if i < self.n else -1)
            struct.pack_into('<i', row.block, 4, ROW_H * i)
            row.children[0].text = self.names[i] if i < self.n else None

    @property
    def selected(self):
        return self.client.props.get(SEL, -1)

    def holder(self, i):
        (row,) = [r for r in self.pool if r.int(O['ROW_INDEX']) == i]
        return row


def test_the_wheel_walks_a_virtual_tables_logical_rows(fw, ui):
    table = Table(fw, ui, 100)
    seen = []
    for _ in range(30):
        key(fw, ui, KEY_NEXT, ui.now + SLOW)
        top = table.client.int(O['TABLE_TOP'])
        seen.append((table.selected, top))
    assert [s for s, _ in seen] == list(range(1, 31))
    assert all(0 <= ROW_H * s - top and ROW_H * s - top + ROW_H <= VIEW_H for s, top in seen)


def test_centre_on_a_virtual_table_clicks_whichever_widget_holds_the_row(fw, ui):
    table = Table(fw, ui, 100)
    for _ in range(12):
        key(fw, ui, KEY_NEXT, ui.now + SLOW)
    clicks = Clicks(fw)
    key(fw, ui, KEY_CENTER, ui.now + SLOW)
    before = table.holder(12)
    table.bind(table.client.int(O['TABLE_TOP']) + ROW_H)  # a rebind: another pool widget holds row 12
    assert table.holder(12) is not before
    ui.fire(ui.due('confirm_center')[0])
    assert clicks.seen == [(table.holder(12).at, EVT_CLICK)]


def test_a_virtual_table_recalls_its_logical_row(fw, ui):
    table = Table(fw, ui, 100)
    for _ in range(40):
        key(fw, ui, KEY_NEXT, ui.now + SLOW)
    assert table.selected == 40
    again = Table(fw, ui, 100)
    scrolled = []
    fw.mock('table_client_scroll_to', lambda w, top, *_: scrolled.append(top) or again.bind(top))
    paint(fw, again)
    assert again.selected == 40 and scrolled and 0 <= ROW_H * 40 - scrolled[-1] <= VIEW_H - ROW_H


@pytest.mark.parametrize('finger, top', [(-90, 0), (0, 0), (ROW_H * 30, ROW_H * 30), (ROW_H * 200, ROW_H * 100 - (282 - BAR))])
def test_a_drag_on_a_virtual_table_stops_at_its_ends(fw, ui, finger, top):
    """TABLE_DRAG_CALL: table_client's pointer move hands its unbounded offset to ringnav_table_drag, which keeps it
    from the first row's top to the end its release scrolls to (the rows less the client's height, cut at the set's list
    end, y 282 on the screen): nothing springs back."""
    table = Table(fw, ui, 100)
    paint(fw, table)
    drag = fw.fn('ringnav_table_drag', C.c_int, C.c_void_p, C.c_int)
    drag(table.client.at, finger)
    assert table.client.int(O['TABLE_TOP']) == top


def test_a_drag_on_a_tall_virtual_table_stops_on_the_list_end(fw, ui):
    """A table whose box runs on past the set's list end (Library > Songs: to the screen's foot) takes a drag to
    where the wheel ends, the last row's foot on y 282, not the box's foot (a drag to the end stopped near y 316, in
    the rounded corners)."""
    table = Table(fw, ui, 100)
    table.client.int(O['W_H'], 320 - BAR)  # the page's box runs to the screen's foot
    paint(fw, table)
    drag = fw.fn('ringnav_table_drag', C.c_int, C.c_void_p, C.c_int)
    drag(table.client.at, ROW_H * 200)
    top = table.client.int(O['TABLE_TOP'])
    assert BAR + ROW_H * 100 - top == 282


def test_a_fling_on_a_tall_virtual_table_clamps_to_the_list_end_not_the_boxs_foot(fw, ui):
    """TABLE_SCROLL_CALLS: stock's release scrolls to the fling's target with table_client_scroll_to, which clamps it
    to the rows less the client's height (the box's foot). ringnav_table_scroll runs it with the height cut at the
    set's list end (y 282), so the fling ends with the last row's foot there, and puts the height back."""
    table = Table(fw, ui, 100)
    table.client.int(O['W_H'], 320 - BAR)
    seen = []

    def stock(w, top, ms, *_):  # stock's clamp: the rows less the client's height now
        seen.append(min(top, ROW_H * 100 - table.client.int(O['W_H'])))
        return 0
    fw.mock('table_client_scroll_to', stock)
    fw.fn('ringnav_table_scroll', C.c_int, C.c_void_p, C.c_int, C.c_int)(table.client.at, ROW_H * 200, 300)
    assert BAR + ROW_H * 100 - seen[0] == 282 and table.client.int(O['W_H']) == 320 - BAR


def test_a_list_takes_no_overscroll_once_bound(fw, ui):
    """AWTK lets a drag run half a view past a scroll view's end and springs it back: a list's is 0."""
    page = Page(ui, titles(30))
    paint(fw, page)
    assert page.view.props[b'slide_limit_ratio'] == 0


# Home's carousel: one animator, retargeted tick by tick.
ANIM_X_TO, ANIM_DURATION, SLIDE_OFFSET, SLIDE_INDEX = O['ANIM_X_TO'], O['ANIM_DURATION'], O['SLIDE_OFFSET'], O['SLIDE_INDEX']


WA = host.defines('wheel_accel.h')


class Carousel:
    """Home's slide_menu of n cards on home_page, a card's width plus the spacer apart; its animator
    a block whose goal (ANIM_X_TO) and run (ANIM_DURATION) the C sets. The live offset is the test's."""
    def __init__(self, fw, ui, n=5, index=2, width=90, spacer=10, animates=True, binds=True, name='home_page'):
        self.fw, self.ui, self.stride = fw, ui, width + spacer
        self.win = ui.widget('window', ui.wm, (0, 30, 375, 290), name=name)
        self.menu = ui.widget('slide_menu', self.win, (0, 0, 375, 200))
        self.cards = [ui.widget('button', self.menu, (0, 0, width, 150), click=True, text=f'Card {i}') for i in range(n)]
        self.menu.int(SLIDE_INDEX, index)
        self.menu.int(O['SLIDE_SPACER'], spacer)
        self.blocks, self.handlers, self.started, self.destroyed = {}, [], [], []
        fw.mock('slide_menu_item_width', lambda *_: width)
        fw.mock('widget_animator_scroll_create', lambda *_: self.create() if animates else 0)
        fw.mock('widget_animator_on', lambda a, evt, fn, ctx, *_: (self.handlers.append((evt, fn, ctx)) or 1) if binds else 0)
        fw.mock('widget_animator_scroll_set_params', self.params)
        fw.mock('widget_animator_start', lambda a, *_: self.started.append((self.goal, self.duration)))
        fw.mock('widget_animator_destroy', lambda a, *_: self.destroyed.append(a))
        ui.top = self.win

    def create(self):
        block = C.create_string_buffer(0x100)
        self.blocks[C.addressof(block)] = block
        return C.addressof(block)

    def params(self, a, x0, y0, x1, y1, *_):
        struct.pack_into('<i', self.blocks[a], ANIM_X_TO, C.c_int(x1).value)

    @property
    def animator(self):
        return self.blocks.get(self.menu.ptr(O['SLIDE_ANIMATOR']))

    @property
    def goal(self):
        return struct.unpack_from('<i', self.animator, ANIM_X_TO)[0]

    @property
    def duration(self):
        return struct.unpack_from('<i', self.animator, ANIM_DURATION)[0]

    def live(self, offset):
        self.menu.int(SLIDE_OFFSET, offset)


@pytest.mark.parametrize('code, way', [(KEY_NEXT, -1), (KEY_PREV, 1)])
def test_a_tick_glides_the_carousel_one_card(fw, ui, code, way):
    home = Carousel(fw, ui)
    key(fw, ui, code)
    assert home.started == [(way * home.stride, home.duration)] and home.duration > 0


def test_a_tick_before_the_glide_lands_extends_it_and_hurries(fw, ui):
    home = Carousel(fw, ui)
    key(fw, ui, KEY_NEXT)
    first = home.duration
    home.live(-home.stride * 2 // 5)
    key(fw, ui, KEY_NEXT, ui.now + NAV['HOME_FAST_WINDOW_MS'])
    assert home.goal == -2 * home.stride and home.duration < first
    assert len(home.blocks) == 1  # the same animator, retargeted


def test_a_tick_after_the_fast_window_keeps_the_normal_pace(fw, ui):
    home = Carousel(fw, ui)
    key(fw, ui, KEY_NEXT)
    first = home.duration
    home.live(-home.stride)
    key(fw, ui, KEY_NEXT, ui.now + NAV['HOME_FAST_WINDOW_MS'] + 1)
    assert home.goal == -2 * home.stride and home.duration == first


def test_a_tick_back_mid_glide_heads_for_the_card_it_has_just_passed(fw, ui):
    home = Carousel(fw, ui)
    key(fw, ui, KEY_NEXT)
    key(fw, ui, KEY_NEXT, ui.now + 50)
    assert home.goal == -2 * home.stride
    home.live(-home.stride * 7 // 5)  # past the next card, short of the one after
    key(fw, ui, KEY_PREV, ui.now + 50)
    assert home.goal == -home.stride


@pytest.mark.parametrize('index, code, lands', [(2, KEY_NEXT, 3), (4, KEY_NEXT, 0), (0, KEY_PREV, 4)])
def test_with_no_animator_a_tick_lands_at_once_round_the_ring(fw, ui, index, code, lands):
    home = Carousel(fw, ui, index=index, animates=False)
    home.live(-17)
    key(fw, ui, code)
    assert fw.calls('slide_menu_set_value')[-1][1][:2] == (home.menu.at, lands)
    assert home.menu.int(SLIDE_OFFSET) == 0 and not home.started


def test_the_glides_end_hands_back_the_cards_focus(fw, ui):
    home = Carousel(fw, ui)
    key(fw, ui, KEY_NEXT)
    (evt, fn, ctx), = home.handlers
    assert evt == O['EVT_ANIM_END'] and ctx == home.menu.at
    home.menu.int(SLIDE_INDEX, 3)  # stock committed the index
    assert C.CFUNCTYPE(C.c_int, C.c_void_p, C.c_void_p)(fn)(ctx, 0x40) == 7  # RET_REMOVE: once
    assert fw.calls('slide_menu_on_scroll_done')[-1][1][:2] == (home.menu.at, 0x40)
    assert fw.calls('widget_set_focused')[-1][1][:2] == (home.cards[3].at, 1)


def test_an_immediate_move_drops_the_glide_still_running(fw, ui):
    home = Carousel(fw, ui)
    key(fw, ui, KEY_NEXT)
    a = home.menu.ptr(O['SLIDE_ANIMATOR'])
    glide = fw.fn('carousel_glide', C.c_int, C.c_void_p, C.c_int, C.c_uint, C.c_int)
    assert glide(home.menu.at, -home.stride, 0, 0) == 0
    assert home.destroyed == [a] and home.menu.ptr(O['SLIDE_ANIMATOR']) == 0
    assert fw.calls('slide_menu_set_value')[-1][1][:2] == (home.menu.at, 3)


def test_a_follow_glide_runs_as_long_as_coverflows_aim_says(fw, ui):
    home = Carousel(fw, ui)
    home.live(-30)
    follow = O['EASE_FOLLOW']
    assert fw.fn('carousel_glide', C.c_int, C.c_void_p, C.c_int, C.c_uint, C.c_int)(home.menu.at, -2 * home.stride, 90, follow) == 1
    aim = fw.fn('coverflow_follow_aim', C.c_int, C.c_int, C.c_int, C.c_int)(-2 * home.stride + 30, 90, -1)
    assert home.duration == aim
    assert fw.calls('widget_animator_scroll_create')[0][1][3] == 0  # AWTK gets no easing it lacks
    easing = struct.unpack_from('<Q', home.animator, O['ANIM_EASING'])[0]
    assert easing == C.cast(fw.lib.coverflow_follow, C.c_void_p).value


def test_the_carousels_momentum_is_the_lists_rows_never_a_letter(fw, ui):
    """The user, 2026-11-19: Cover Flow scrolls as the lists do, through every cover, never by letter. Timed ticks
    on a long carousel: one card a tick turned slowly, more as a sustained spin goes on (wheel_accel.c: 2 from
    WA_FIRST_MS, one more each WA_RAMP_MS, at most WA_MAX_ROWS), however long, and no letter shown; a pause past
    WA_RUN_MS starts over at one."""
    home = Carousel(fw, ui, n=400)
    goal, steps, t = 0, [], 1000
    for i in range(3):  # slow
        key(fw, ui, KEY_NEXT, t)
        steps.append((goal - home.goal) // home.stride)
        goal, t = home.goal, t + 300
    for i in range(90):  # a sustained spin, a tick each 30 ms for 2.7 s
        key(fw, ui, KEY_NEXT, t)
        steps.append((goal - home.goal) // home.stride)
        goal, t = home.goal, t + 30
    fast = steps[3:]
    assert steps[:3] == [1, 1, 1] and fast[0] == 1 and max(fast) == WA['WA_MAX_ROWS'] and fast[-1] == WA['WA_MAX_ROWS']
    assert all(b >= a for a, b in zip(fast, fast[1:]))  # rising while it goes on, never a letter's jump
    key(fw, ui, KEY_NEXT, t + WA['WA_RUN_MS'] + 100)  # after a pause: one again
    assert (goal - home.goal) // home.stride == 1


# Pull to search: a drag down from the top of Local Songs' list.
STOP = NAV['STOP']
EVT_DOWN, EVT_MOVE, EVT_UP = O['EVT_POINTER_DOWN'], O['EVT_POINTER_MOVE_BEFORE'], O['EVT_POINTER_UP_BEFORE']
PULL_CLAIM, PULL_SEARCH = O['PULL_CLAIM_PX'], O['PULL_SEARCH_PX']


class Pull:
    """A finger on page: ringnav_touch's down, then the moves and lift stock dispatches to the handler
    the down bound (pull_event), as they reach it. The prompt is a label the C makes on the page."""
    def __init__(self, fw, ui, page, fail_at=None):
        self.fw, self.ui, self.page, self.fail_at = fw, ui, page, fail_at
        self.bound, self.unbound, self.aborts = [], [], []
        fw.mock('widget_on', self.on)
        fw.mock('widget_off_by_func', lambda w, t, *_: self.unbound.append((w, t)))
        fw.mock('widget_to_local', self.to_local)
        fw.mock('label_create', lambda parent, x, y, w, h, *_: ui.widget('label', ui.by[parent], (x, y, w, h)).at)
        fw.mock('widget_set_name', lambda w, name, *_: ui.by[w].props.__setitem__(b'name', C.string_at(name)))
        fw.mock('widget_set_text_utf8', lambda w, s, *_: setattr(ui.by[w], 'text', C.string_at(s).decode()))
        fw.mock('widget_set_visible', lambda w, on, *_: w in ui.by and setattr(ui.by[w], 'visible', bool(on)))
        fw.mock('pointer_event_init', Clicks.init)
        fw.mock('widget_dispatch_event_to_target_recursive', lambda w, e, *_: self.aborts.append((w, C.c_int.from_address(e).value)))
        self.x = self.y = 0

    def on(self, w, kind, fn, ctx, *_):
        if self.fail_at is not None and len(self.bound) == self.fail_at:
            self.fail_at = None
            return 0
        self.bound.append((w, kind, fn, ctx))
        return len(self.bound)

    def to_local(self, w, xy, *_):
        """AWTK's: the point less the widget's screen origin, plus the scroll of a list on the way (widget_get_offset),
        so a scrolled list's press is in its content's coordinates."""
        x, y = self.ui.by[w].origin()
        point = (C.c_int * 2).from_address(xy)
        point[0] -= x
        point[1] -= y
        it = self.ui.by[w]
        while it is not None and it.kind != b'window_manager':
            if it.kind in (b'table_client', b'scroll_view'):
                point[1] += it.int(O['TABLE_TOP'] if it.kind == b'table_client' else O['SCROLL_Y'])
            it = it.parent

    def event(self, kind, x, y):
        e = C.create_string_buffer(0x40)
        struct.pack_into('<i', e, O['EVENT_TYPE'], kind)
        struct.pack_into('<2i', e, O['EVENT_X'], x, y)
        return e

    def down(self, x=100, y=100):
        self.x, self.y = x, y
        return self.fw.fn('ringnav_touch', C.c_int, C.c_void_p, C.c_void_p)(None, self.event(EVT_DOWN, x, y))

    def send(self, kind, dx=0, dy=0, to=None):
        """The handler for this event on widget `to` (the page unless given), as stock's dispatch runs
        it; 0 if none is bound."""
        to = to or self.page.win
        handlers = [(fn, ctx) for w, k, fn, ctx in self.bound if w == to.at and k == kind]
        if not handlers:
            return 0
        fn, ctx = handlers[0]
        self.last = self.event(kind, self.x + dx, self.y + dy)
        return C.CFUNCTYPE(C.c_int, C.c_void_p, C.c_void_p)(fn)(ctx, self.last)

    def move(self, dx, dy):
        return self.send(EVT_MOVE, dx, dy)

    def up(self, dx, dy):
        return self.send(EVT_UP, dx, dy)

    @property
    def prompt(self):
        """The prompt label over the list, as (shown, its text); None before the pull makes it."""
        found = self.ui.lookup(self.page.win, b'_pull_prompt')
        return (self.ui.by[found].visible, self.ui.by[found].text) if found else None

    @property
    def says(self):
        """The prompt's text while it is shown, else None."""
        prompt = self.prompt
        return prompt[1] if prompt and prompt[0] else None

    @property
    def hidden(self):
        return self.prompt is not None and not self.prompt[0]


def searches(fw):
    """Each Search a pull opened: library_search_open's strip page (search_strip.c, OLED's list.entry.ipod), whose
    page_open asks window_create first; the stub's 0 ends it there."""
    return fw.calls('window_create')


def test_a_pull_down_from_the_lists_top_searches_on_release(fw, ui):
    page = Page(ui, titles(20), name='localmusic_page')
    pull = Pull(fw, ui, page)
    pull.down()
    assert pull.move(0, PULL_CLAIM // 2) == 0 and pull.prompt is None  # the list's own, so far
    assert pull.move(2, PULL_CLAIM + 4) == STOP
    assert page.view.props[b'_pull_suppress'] == 1 and pull.aborts == [(page.win.at, O['EVT_POINTER_ABORT'])]
    pulling = pull.says
    assert pulling
    assert pull.move(0, PULL_SEARCH) == STOP
    ready = pull.says
    assert ready and ready != pulling  # far enough: it says so
    assert pull.move(0, PULL_SEARCH - 1) == STOP and pull.says == pulling  # back short of it
    assert pull.move(0, 0) == STOP and pull.hidden  # back where it began: still the pull's, nothing shown
    assert pull.move(0, PULL_SEARCH) == STOP and pull.says == ready
    assert pull.up(0, PULL_SEARCH) == STOP
    assert len(searches(fw)) == 1
    assert pull.hidden


def test_a_pull_let_go_short_does_not_search(fw, ui):
    page = Page(ui, titles(20), name='localmusic_page')
    pull = Pull(fw, ui, page)
    pull.down()
    pull.move(0, PULL_CLAIM + 4)
    assert pull.up(0, PULL_SEARCH - 1) == STOP  # claimed: the lift is the pull's, not a row's tap
    assert not searches(fw) and pull.hidden


@pytest.mark.parametrize('dx, dy', [(PULL_CLAIM + 4, PULL_CLAIM), (0, -PULL_CLAIM)])
def test_a_sideways_or_upward_drag_is_left_to_the_list(fw, ui, dx, dy):
    page = Page(ui, titles(20), name='localmusic_page')
    pull = Pull(fw, ui, page)
    pull.down()
    assert pull.move(dx, dy) == 0
    assert pull.move(0, PULL_SEARCH) == 0 and pull.up(0, PULL_SEARCH) == 0
    assert not searches(fw) and page.view.props.get(b'_pull_suppress') == 0


@pytest.mark.parametrize('y, scrolled', [(O['QUICK_EDGE_PX'] - 1, 0), (100, ROW_H), (BAR + VIEW_H + 5, 0)])
def test_a_touch_at_the_top_edge_below_the_list_or_on_a_scrolled_list_starts_no_pull(fw, ui, y, scrolled):
    page = Page(ui, titles(20), name='localmusic_page')
    page.scroll(scrolled)
    pull = Pull(fw, ui, page)
    pull.down(y=y)
    assert pull.move(0, PULL_SEARCH) == 0 and pull.up(0, PULL_SEARCH) == 0
    assert pull.prompt is None and not searches(fw)


@pytest.mark.parametrize('name, view, pulls', [
    ('allmusic_page', None, False), ('radio_page', None, True),
    ('home_page', 'scroll_view_localmusic', True), ('home_page', 'scroll_view_home', False),
])
def test_only_local_songs_internet_radio_and_homes_library_pull(fw, ui, name, view, pulls):
    page = Page(ui, titles(20), name=name)
    if view:
        page.view.props[b'name'] = view.encode()
    pull = Pull(fw, ui, page)
    pull.down()
    pull.move(0, PULL_SEARCH)
    assert (pull.up(0, PULL_SEARCH) == STOP) == pulls
    assert len(searches(fw)) == (pulls and name != 'radio_page')  # the radio's search is its own


ENDS = {'background': ('EVT_WINDOW_BACKGROUND', 'win'), 'closed': ('EVT_WINDOW_CLOSE', 'win'),
        'aborted': ('EVT_POINTER_ABORT', 'win'), 'page destroyed': ('EVT_DESTROY', 'win'),
        'list destroyed': ('EVT_DESTROY', 'view')}


@pytest.mark.parametrize('end', [*ENDS, 'covered', 'animating', 'dark'])
def test_the_pull_ends_when_its_page_stops_being_the_one_in_front(fw, ui, end):
    page = Page(ui, titles(20), name='localmusic_page')
    pull = Pull(fw, ui, page)
    pull.down()
    pull.move(0, PULL_CLAIM + 4)
    if end in ENDS:
        kind, on = ENDS[end]
        assert pull.send(O[kind], to=getattr(page, on)) == 0  # the event goes on to stock
    else:
        if end == 'covered':
            ui.top = Page(ui, titles(3), name='localmusic_page', top=False).win
        elif end == 'animating':
            ui.animating = 1
        else:
            fw.var('g_backlight_status', C.c_ubyte).value = 0
        assert pull.move(0, PULL_SEARCH) == 0
    assert pull.hidden
    ui.top, ui.animating = page.win, 0
    fw.var('g_backlight_status', C.c_ubyte).value = 1
    assert pull.move(0, PULL_SEARCH) == 0 and pull.up(0, PULL_SEARCH) == 0 and not searches(fw)


def test_a_failed_bind_takes_back_what_it_bound_and_the_next_touch_binds_afresh(fw, ui):
    page = Page(ui, titles(20), name='localmusic_page')
    pull = Pull(fw, ui, page, fail_at=3)
    pull.down()
    assert pull.bound and Counter((w, k) for w, k, *_ in pull.bound) == Counter(pull.unbound)  # all taken back
    assert pull.move(0, PULL_SEARCH) == 0 and pull.up(0, PULL_SEARCH) == 0 and not searches(fw)
    pull.down()
    live = Counter((w, k) for w, k, *_ in pull.bound) - Counter(pull.unbound)
    assert set(live.values()) == {1}  # bound afresh, none twice
    pull.move(0, PULL_SEARCH)
    assert pull.up(0, PULL_SEARCH) == STOP and len(searches(fw)) == 1


def test_a_page_binds_its_pull_once(fw, ui):
    page = Page(ui, titles(20), name='localmusic_page')
    pull = Pull(fw, ui, page)
    pull.down()
    first = len(pull.bound)
    pull.down()
    assert first and len(pull.bound) == first  # the second touch binds nothing more


# The Time Zone page's rows.
ZONES = host.tools_build.TIMEZONES
ORDER = host.tools_build.TIMEZONE_ORDER


class Zones:
    """Stock's time zone rows as stock builds them, row 0 first, then its caption, then the rest: each
    a list item whose last child is its button, holding an hscroll_label and a plain label. Each
    zone's offset now, in seconds, is offsets' (its label's standard one unless given); saved, the
    zone saved (g_time_zone)."""
    def __init__(self, fw, ui, offsets=None, saved=22):
        self.fw, self.ui, self.zone = fw, ui, ''
        self.offsets = {zone: 60 * host.tools_build.timezone_offset(label) for label, zone in ZONES}
        self.offsets.update(offsets or {})
        self.stock, self.focus, self.texts = [], [], {}
        fw.mock('toolsSetTimeZone', lambda name, *_: setattr(self, 'zone', C.string_at(name).decode()))
        fw.mock('fw_localtime_r', self.local)
        fw.mock('stock_timezone_row_trampoline', lambda item, i, *_: self.stock.append(i))
        fw.mock('hscroll_label_set_only_focus', lambda label, on, *_: self.focus.append((label, on)))
        fw.mock('widget_set_text_utf8', lambda w, s, *_: self.texts.__setitem__(w, C.string_at(s).decode()))
        # the value label list_value adds beside the key (named _setting_value)
        fw.mock('hscroll_label_create', lambda parent, x, y, w, h, *_: ui.widget('hscroll_label', ui.by[parent], (x, y, w, h)).at)
        fw.mock('widget_set_name', lambda w, name, *_: ui.by[w].props.__setitem__(b'name', C.string_at(name)) if w in ui.by else None)
        fw.var('g_time_zone', C.c_ubyte).value = saved
        self.saved = saved
        self.list = ui.widget('list_view')
        self.caption_label = ui.widget('hscroll_label')  # stock adds it after row 0
        self.rows = []

    def local(self, when, out, *_):
        C.c_int.from_address(out + 36).value = self.offsets[self.zone]  # tm_gmtoff, the Q2's struct tm
        return out

    def build(self, i):
        if i == 1:  # stock's caption, between rows 0 and 1
            self.caption_label = self.ui.widget('hscroll_label', self.ui.widget('view', self.list))
        item = self.ui.widget('list_item', self.list)
        button = self.ui.widget('button', item)
        scroll, plain = self.ui.widget('hscroll_label', button), self.ui.widget('label', button)
        self.fw.fn('ringnav_timezone_row', None, C.c_void_p, C.c_int)(item.at, i)
        self.rows.append((scroll, plain))
        value = next((w for w in button.children if w.kind == b'hscroll_label' and w is not scroll), None)
        return self.texts.get(scroll.at), self.texts.get(value.at) if value else None


def utc(offset):
    m = abs(offset) // 60
    return f'UTC{"-" if offset < 0 else "+"}{m // 60}{f":{m % 60:02d}" if m % 60 else ""}'


def test_each_zone_row_reads_its_city_and_offset_now_the_saved_one_first_then_west_to_east(fw, ui):
    zones = Zones(fw, ui, {'America/New_York': -4 * 3600, 'Europe/London': 3600})  # summer time
    shown = [zones.build(i) for i in range(len(ZONES) + 1)]
    names = [zone for _, zone in ZONES]
    cities = [label.split(' ', 1)[1] for label, _ in ZONES]
    rest = sorted((z for z in range(len(ZONES)) if z != zones.saved), key=lambda z: (zones.offsets[names[z]], cities[z]))
    rows = [zones.saved] + rest
    assert shown[0] == (None, None) and zones.stock[0] == 0  # stock's own first row
    assert zones.stock[1:] == [z + 1 for z in rows]  # stock names and clicks the zone by its index
    assert shown[1:] == [(cities[z], utc(zones.offsets[names[z]])) for z in rows]
    assert shown[1] == ('Shanghai', 'UTC+8')  # the saved zone, pinned
    assert ('New York', 'UTC-4') in shown and ('London', 'UTC+1') in shown and ('Kathmandu', 'UTC+5:45') in shown
    assert ('St. John\'s', 'UTC-3:30') in shown and ('Lord Howe', 'UTC+10:30') in shown
    chicago, mexico = shown.index(('Chicago', 'UTC-6')), shown.index(('Mexico City', 'UTC-6'))
    assert chicago < mexico  # ties by city


def test_the_saved_zones_value_stops_short_of_its_check(fw, ui):
    """The picker's check sits on the right column: the pinned row's value ends 24 px before the others'."""
    zones = Zones(fw, ui)
    fw.mock('widget_move_resize', lambda w, x, y, ww, h, *_: struct.pack_into('<4i', ui.by[w].block, 0, x, y, ww, h) if w in ui.by else None)
    for i in range(3):
        zones.build(i)
    values = [w for w in ui.by.values() if w.kind == b'hscroll_label' and w.props.get(b'name') == b'_setting_value']
    ends = [w.box[0] + w.box[2] for w in values]
    assert len(ends) == 2 and ends[0] == ends[1] - 24


def test_zone_rows_keep_their_ellipsis_until_focused(fw, ui):
    zones = Zones(fw, ui)
    for i in range(3):
        zones.build(i)
    assert [f for f in zones.focus if f[0] in [scroll.at for scroll, _ in zones.rows]] == \
        [(scroll.at, 1) for scroll, _ in zones.rows]
    assert not any(plain.at in zones.texts for _, plain in zones.rows)


def test_a_row_past_the_zones_is_stocks(fw, ui):
    zones = Zones(fw, ui)
    for i in range(len(ZONES) + 2):
        text = zones.build(i)
    assert zones.stock[-1] == len(ZONES) + 1 and text == (None, None)


# Music loads leave out spoken tracks.
SONGS = [  # songtable's (id, album, fileurl): album X's music, the spoken tracks tagged X too, album Y
    (1, 'X', '/mnt/mmc/music/x/01.flac'), (2, 'X', '/mnt/mmc/audiobooks/x/01.mp3'),
    (3, 'X', '/mnt/mmc/podcasts/x/ep1.mp3'), (4, 'X', '/mnt/mmc/music/x/book.m4b'),
    (5, 'X', '/mnt/mmc/music/x/02.mp3'), (6, 'Y', '/mnt/mmc/music/y/01.flac')]


def songs(sql):
    """The ids of SONGS sql selects, as the Q2's sqlite runs it."""
    db = sqlite3.connect(':memory:')
    db.execute('create table songtable(id, album, songer, fileurl, albumsonger, track, cd_num)')
    db.executemany('insert into songtable(id, album, fileurl) values (?, ?, ?)', SONGS)
    return sorted(row[0] for row in db.execute(sql))


class Loads:
    """ringnav_library_query, inside a music load (staged_music) or not; the SQL stock is asked to run."""
    def __init__(self, fw):
        self.fw, self.ran = fw, []
        fw.mock('stock_dbquery_trampoline', lambda db, sql, *_: self.ran.append(C.string_at(sql).decode()) and 0)
        self.query = fw.fn('ringnav_library_query', C.c_int, C.c_char_p, C.c_char_p, C.c_void_p, C.c_int)

    def music(self, sql):
        """sql as a music load runs it: its result, and the load's count staged_music hands back."""
        got = []

        @C.CFUNCTYPE(C.c_int, C.c_void_p)
        def load(arg):
            got.append(self.query(b'db', sql.encode(), None, 0))
            return 7

        count = C.c_int()
        self.fw.fn('staged_music', C.c_void_p, C.c_void_p, C.c_void_p, C.POINTER(C.c_int))(load, None, C.byref(count))
        return got[0], count.value


def test_music_detail_loads_leave_out_spoken_tracks(fw):
    loads, sql = Loads(fw), "select * from songtable where album='X'"
    assert loads.music(sql) == (0, 7)
    loads.query(b'db', sql.encode(), None, 0)
    in_load, outside = loads.ran
    assert songs(in_load) == [1, 5]  # the album's music, its audiobook, podcast and .m4b left out
    assert songs(outside) == [1, 2, 3, 4, 5]  # outside a music load the query is stock's own


def test_a_music_load_too_long_to_filter_runs_nothing(fw):
    loads, head = Loads(fw), "select * from songtable where album='"
    (size,) = map(int, re.findall(r'\bfiltered\[(\d+)\]', NAVIGATION_SOURCE))
    sql = head + 'x' * (size - 2 - len(head)) + "'"  # as long as library_query's buffer can hold: no room for the filter
    assert loads.music(sql)[0] == -1 and not loads.ran  # not run unfiltered
    assert loads.query(b'db', sql.encode(), None, 0) == 0 and loads.ran == [sql]  # outside a load it runs as it is


NO_MIN = int(re.findall(r'#define NO_MIN (\d+)', (host.ROOT/'patch/name_order.c').read_text())[0])
CMP = {name: address for name, (_, address) in host.tools_build.NAME_COMPARATORS.items()}


class Sorts:
    """toolsQueryDbTable over n song records as stock runs them: a fresh record per row on the staging deque, in
    input's order; cstl_sort as stock's algo_sort_if, by title with equal titles in reverse input order, so an order
    depends on the order the records came in, as an unstable sort's does. calls logs (trampoline, sort) and
    ('sort', comparator)."""
    def __init__(self, fw, n):
        self.fw, self.q, self.calls, self.keep = fw, Deques(fw), [], []
        self.staging = fw.var('fw_ctx_tools_pdeq_directory', C.c_void_p)
        self.staging.value = self.q.new()
        self.ids = list(range(1, n + 1))
        self.titles = {i: f'T{i % 37:02d}' for i in self.ids}  # repeats: ties for the sort to settle
        self.at, self.mode = {}, 0
        for name in ('stock_sorted_query_trampoline', 'stock_dbquery_trampoline'):
            fw.mock(name, lambda db, sql, row, sort, *_, name=name: self.run(name, sort))
        fw.mock('cstl_sort', self.sort)
        self.sorted = fw.fn('ringnav_sorted_query', C.c_int, C.c_char_p, C.c_char_p, C.c_void_p, C.c_int)
        self.named = fw.fn('ringnav_library_query', C.c_int, C.c_char_p, C.c_char_p, C.c_void_p, C.c_int)

    def order(self):
        return sorted(self.ids, key=lambda i: (self.titles[i], -self.ids.index(i)))

    def record(self, i):
        r = C.create_string_buffer(O['REC_SIZE'] + 8)
        title = C.create_string_buffer(self.titles[i].encode())
        struct.pack_into('<i', r, O['REC_ID'], i)
        struct.pack_into('<i', r, O['REC_TYPE'], 8)
        # mode 2 reads the path alone; here, with 8-byte pointers, it overlaps the title's field
        struct.pack_into('<Q', r, O['REC_PATH' if self.mode == 2 else 'REC_TITLE'], C.addressof(title))
        self.keep += [r, title]
        self.at[C.addressof(r)] = i
        return C.addressof(r)

    def run(self, name, sort):
        self.calls.append((name.split('_')[1], sort))
        self.q[self.staging.value][:] = [self.record(i) for i in self.ids]
        if name == 'stock_sorted_query_trampoline' or sort:
            self.sort(self.staging.value, CMP['NAME_CMP_PINYIN'], log=False)
        return len(self.ids)

    def sort(self, deque, cmp, *_, log=True):
        if log:
            self.calls.append(('sort', cmp))
        rows = self.q[deque]
        rows[:] = sorted(rows, key=lambda r: (self.titles[self.at[r]], -rows.index(r)))

    def listed(self):
        return [struct.unpack_from('<i', C.string_at(p, 4))[0] for p in self.q[self.staging.value]]

    def load(self, sql=b'select * from songtable', mode=0):
        self.calls.clear()
        self.mode = mode
        assert self.sorted(b'db', sql, None, mode) == len(self.ids)
        return self.calls


SORTED = [('dbquery', 0), ('sort', CMP['NAME_CMP_PINYIN'])]


def test_a_sorted_load_is_sorted_once_and_then_takes_its_kept_order(fw):
    """name_order.c: the rows come unsorted, the first load sorts them with stock's comparator, the next moves them
    into the kept order, ties as the sort left them; another query or mode sorts again."""
    s = Sorts(fw, NO_MIN + 88)
    assert s.load() == SORTED and s.listed() == s.order()
    assert s.load() == [('dbquery', 0)] and s.listed() == s.order()
    assert s.load(mode=1) == [('dbquery', 0), ('sort', CMP['FILE_NAME_CMP_PINYIN'])]  # another mode, another comparator
    assert s.load(mode=2) == [('dbquery', 0), ('sort', CMP['PATH_CMP'])]
    assert s.load(mode=3) == SORTED and s.listed() == s.order()  # any other mode: the name sort
    assert s.load(b'select * from songtable where 1') == SORTED  # another query
    assert s.load(mode=3) == [('dbquery', 0)] and s.listed() == s.order()  # four slots: the latest four kept
    assert s.load() == SORTED  # the least recently used went


def test_the_comparator_flag_picks_the_other_pair_and_keeps_apart(fw):
    s = Sorts(fw, NO_MIN)
    flag = C.c_ubyte.from_address(O['NAME_SORT_FLAG'])
    s.load()
    flag.value = 1
    try:
        assert s.load() == [('dbquery', 0), ('sort', CMP['NAME_CMP'])]
        assert s.load(mode=1) == [('dbquery', 0), ('sort', CMP['FILE_NAME_CMP'])]
        assert s.load() == [('dbquery', 0)]
    finally:
        flag.value = 0


@pytest.mark.parametrize('change', ['title', 'gone', 'repeat', 'moved'])
def test_a_kept_order_is_used_only_for_the_same_records_in_the_same_order(fw, change):
    """Another title or a record fewer: not the kept records; an id twice: no one place for it; the same records in
    another order: stock's sort could settle the ties otherwise. Each sorts again."""
    s = Sorts(fw, NO_MIN + 3)
    if change == 'repeat':  # in both loads: the same records, but no one place for that id
        s.ids[9] = s.ids[8]
    s.load()
    if change == 'title':
        s.titles[5] = 'T00'
    elif change == 'gone':
        s.ids.remove(9)
    elif change == 'moved':
        s.ids[0], s.ids[37] = s.ids[37], s.ids[0]  # equal titles: the tie goes the other way
    assert s.load() == SORTED and s.listed() == s.order()


def test_the_name_sort_of_tools_query_db_table_is_kept_too(fw):
    s = Sorts(fw, NO_MIN)
    s.calls.clear()
    assert s.named(b'db', b'select id,album from songtable', None, 1) == NO_MIN and s.calls == SORTED
    s.calls.clear()
    assert s.named(b'db', b'select id,album from songtable', None, 1) == NO_MIN and s.calls == [('dbquery', 0)]
    assert s.listed() == s.order()
    s.calls.clear()
    s.named(b'db', b'select id,album from songtable', None, 0)  # no sort asked: nothing to keep or apply
    assert s.calls == [('dbquery', 0)] and s.listed() == s.ids


def test_a_short_list_is_sorted_every_time_and_one_row_not_at_all(fw):
    s = Sorts(fw, NO_MIN - 1)
    assert s.load() == SORTED and s.load() == SORTED and s.listed() == s.order()
    s.ids[1:] = []
    assert s.load() == [('dbquery', 0)]


def test_another_threads_load_sorts_in_stock(fw):
    s = Sorts(fw, NO_MIN)
    fw.mock('fw_pthread_self', lambda *_: 7)  # not ui_thread
    assert s.load() == [('sorted', 0)] and s.load(mode=2) == [('sorted', 2)]
    assert s.named(b'db', b'select 1 from songtable', None, 1) == NO_MIN and s.calls[-1] == ('dbquery', 1)


def reboot(fw, library):
    """name_order.c's RAM as a boot leaves it: no slot, the card's heads not read."""
    kept = fw.var('name_order__no', C.c_ubyte * fw.sizes['name_order__no'])
    file = host.offsets(library.parent/'name_order.o', 'no_slot')['file']  # its last field: a slot is file + 8
    for i in range(4):
        fw.fn('free', None, C.c_void_p)(C.c_void_p.from_buffer(kept, i * (file + 8) + file).value)
    C.memset(kept, 0, len(kept))


def test_the_kept_order_outlives_a_reboot_on_the_card(fw, library):
    files = Files(fw)
    s = Sorts(fw, NO_MIN + 5)
    assert s.load() == SORTED
    (path, data), = files.files.items()
    assert path.startswith('/mnt/mmc/.q2pod/order') and len(data) == 32 + 4 * len(s.ids)
    reboot(fw, library)
    assert s.load() == [('dbquery', 0)] and s.listed() == s.order()
    s.ids[0], s.ids[37] = s.ids[37], s.ids[0]  # rows in another order: sorted, kept again in that slot
    reboot(fw, library)
    assert s.load() == SORTED and s.listed() == s.order() and list(files.files) == [path]


@pytest.mark.parametrize('damage', ['torn', 'longer', 'foreign', 'ids', 'version'])
def test_a_damaged_or_foreign_order_file_is_never_used(fw, library, damage):
    files = Files(fw)
    s = Sorts(fw, NO_MIN + 5)
    s.load()
    (path, data), = files.files.items()
    data = bytearray(data)
    if damage == 'torn':
        data = data[:-4]
    elif damage == 'longer':
        data += b'\0'
    elif damage == 'foreign':
        data[0] ^= 1  # not our magic
    elif damage == 'version':
        data[4] ^= 1  # another firmware's
    else:
        data[32:40] = data[36:40] + data[32:36]  # two ids swapped: still a permutation, not this order
    files.files[path] = bytes(data)
    reboot(fw, library)
    assert s.load() == SORTED and s.listed() == s.order()


LOAD_SHOW_MS = int(re.findall(r'#define LOAD_SHOW_MS (\d+)', (host.ROOT/'patch/ui_kit.h').read_text())[0])
LOAD_TICK_MS = int(re.findall(r'#define LOAD_TICK_MS (\d+)', (host.ROOT/'patch/list_marks.c').read_text())[0])


class Shown:
    """The shown framebuffer page (320 x 375, 32 bpp, the second of two pages shown), an off-screen canvas of
    LOAD_AREA for sync_begin's plate (its fills are stock's stubs: it holds what the framebuffer showed), and the
    threads pthread_create starts: the overlay's painter (fb_painter, by its address) on a real thread with a clock
    of its own, a tick a LOAD_TICK_MS sleep; any other held in others for the test to run."""
    def __init__(self, fw):
        self.fw, self.page = fw, 320 * 375 * 4
        self.fb = (C.c_ubyte * (2 * self.page))(*bytes(range(256)) * (2 * self.page // 256))
        self.before, self.others, self.ui_ticks = bytes(self.fb), [], []
        self.painter: threading.Thread | None = None
        self.on_ui_tick: Callable[[int], object] = lambda n: None  # the test's work at the UI thread's nth sleep
        self.first_drawn = None  # the painter's tick it first changed the page at
        self.ticks = 0
        self.off = (C.c_uint * (240 * 100))()
        canvas, lcd = block(fw, size=4096), block(fw, size=4096)
        C.c_void_p.from_address(canvas + O['CANVAS_LCD']).value = lcd
        C.c_void_p.from_address(lcd + O['LCD_OFFLINE_FB']).value = C.addressof(self.off)
        C.c_int.from_address(lcd + O['LCD_LINE_LENGTH']).value = 240 * 4
        fw.mock('canvas_offline_create', lambda *_: canvas)
        fw.mock('fw_open', lambda *_: 5)
        fw.mock('fw_ioctl', self.ioctl)
        fw.mock('fw_mmap', lambda *_: C.addressof(self.fb))
        fw.mock('fw_usleep', self.usleep)
        fw.mock('fw_pthread_create', self.create)
        fw.mock('fw_pthread_join', self.join)
        self.painter_fn = C.cast(getattr(fw.lib, ui_name(fw.lib, 'fb_painter')), C.c_void_p).value

    def ioctl(self, fd, request, out, *_):
        if request == 0x4600:  # var: 320 x 375, 32 bpp, the second page shown
            C.memmove(out, struct.pack('<7I', 320, 375, 320, 750, 0, 375, 32), 28)
        else:  # fix: smem_len at word 5, line_length at word 11
            fix = [0] * 17
            fix[5], fix[11] = 2 * self.page, 1280
            C.memmove(out, struct.pack('<17I', *fix), 68)
        return 0

    def usleep(self, us, *_):
        if threading.current_thread() is self.painter:
            self.ticks += 1
            if self.first_drawn is None and bytes(self.fb) != self.before:
                self.first_drawn = self.ticks
            time.sleep(.0005)
        else:
            self.ui_ticks.append(us)
            self.on_ui_tick(len(self.ui_ticks))
        return 0

    def create(self, out, attr, run, arg, *_):
        C.c_ulong.from_address(out).value = 90 + len(self.others)
        body = C.CFUNCTYPE(C.c_void_p, C.c_void_p)(run)
        if run == self.painter_fn:
            self.painter = threading.Thread(target=body, args=(None,))
            self.painter.start()
        else:
            self.others.append(body)
        return 0

    def join(self, *_):
        if self.painter:
            self.painter.join(5)
        return 0

    def drawn(self):
        """Wait (real time, 5 s at most) for the painter to have drawn; the page as it was then."""
        end = time.monotonic() + 5
        while self.first_drawn is None and time.monotonic() < end:
            time.sleep(.001)
        return bytes(self.fb)


def upright_box(changed, shown=1):
    """The bounding box (x0, y0, x1, y1), half open, of changed framebuffer bytes as the screen shows them: upright
    375 x 320, the portrait page turned counterclockwise (tools/q2remote.py framebuffer_png: x the page's row, y 319
    less its column)."""
    xs = {i // 1280 - shown * 375 for i in changed}
    ys = {319 - i % 1280 // 4 for i in changed}
    return min(xs), min(ys), max(xs) + 1, max(ys) + 1


def sort_under_overlay(fw):
    """A full sort on its own thread under the loading overlay: the sort run at the UI thread's tenth sleep, once the
    painter has drawn. (the sort, the framebuffer, the page as the painter drew it)."""
    s, f, seen = Sorts(fw, NO_MIN), Shown(fw), {}

    def tick(n):
        if n == 10:
            seen['during'] = f.drawn()
            f.others[0](None)  # the sort's thread: its work, then done
    f.on_ui_tick = tick
    assert s.load() == SORTED and s.listed() == s.order()
    return s, f, seen['during']


def test_a_full_sort_runs_on_its_own_thread_under_the_overlay(fw):
    """The sort on a thread of its own; the overlay drawn on the shown framebuffer page by a painter thread from
    LOAD_SHOW_MS of its own clock, and what it covered put back once the sort is done."""
    s, f, during = sort_under_overlay(fw)
    assert set(f.ui_ticks) == {LOAD_TICK_MS * 1000} and len(f.ui_ticks) == 10
    # drawn after the sleep that reached LOAD_SHOW_MS, seen at the next: nothing sooner, so no flash
    assert f.first_drawn == LOAD_SHOW_MS // LOAD_TICK_MS + 1
    changed = [i for i in range(2 * f.page) if during[i] != f.before[i]]
    assert changed and {i // 1280 for i in changed} <= set(range(375, 750))  # on the shown page
    assert bytes(f.fb) == f.before and fw.calls('fw_munmap') and f.painter and not f.painter.is_alive()


def test_the_sort_overlay_is_centred_on_the_whole_screen(fw):
    """The spinner's dots (the plate's fills are stock's, stubbed here) centre on the screen's centre, 375 x 320 with
    the status row, within a pixel either way: not the centre of the room under the status row."""
    s, f, during = sort_under_overlay(fw)
    x0, y0, x1, y1 = upright_box([i for i in range(2 * f.page) if during[i] != f.before[i]])
    assert x1 - x0 <= 64 and y1 - y0 <= 64
    assert abs((x0 + x1) / 2 - 375 / 2) <= 1 and abs((y0 + y1) / 2 - 320 / 2) <= 1


def test_a_sort_done_before_the_delay_shows_nothing(fw):
    s, f = Sorts(fw, NO_MIN), Shown(fw)
    f.on_ui_tick = lambda n: n == 1 and f.others[0](None)
    assert s.load() == SORTED
    assert f.first_drawn is None and bytes(f.fb) == f.before and f.painter and not f.painter.is_alive()


def test_the_overlays_spinner_is_centred_on_the_whole_screen(fw):
    """Painted over a page the payload builds (page_open's, full-bleed: the whole screen), once its wait has lasted
    LOAD_SHOW_MS, the box's centre is the screen's (187.5, 160) within a pixel, not the rows' under the status row."""
    boxes, now = [], [5000]
    fw.mock('time_now_ms', lambda *_: now[0])
    fw.mock('canvas_fill_rounded_rect', lambda canvas, r, *_: boxes.append(tuple((C.c_int * 4).from_address(r))) or 0)
    page = page_block(fw)
    fw.mock('window_create', lambda *_: page)
    fw.mock('widget_move_resize', lambda w, *box: struct.pack_into('<4i', (C.c_char * 16).from_address(w + O['W_X']), 0, *box[:4]))
    win = fw.fn('page_open', C.c_void_p, C.c_char_p, C.c_void_p, C.c_void_p)(b'radio_page', None, None)
    canvas, lcd = block(fw, size=4096), block(fw, size=4096)
    C.c_void_p.from_address(canvas + O['CANVAS_LCD']).value = lcd
    paint = fw.fn('load_spinner_paint', None, C.c_void_p, C.c_void_p)
    for _ in range(LOAD_SHOW_MS // 100 + 1):  # a frame each 100 ms, as the spinner's timer asks
        boxes.clear()
        paint(win, canvas)
        now[0] += 100
    x, y, w, h = boxes[0]
    wy = C.c_int.from_address(win + O['W_Y']).value
    assert wy == 0  # the window from the screen's top
    assert (w, h) == (64, 64) and abs(x + w / 2 - 375 / 2) <= 1 and abs(wy + y + h / 2 - 320 / 2) <= 1
    for dx, dy, dw, dh in boxes[1:]:  # the dots inside it
        assert x <= dx and dx + dw <= x + w and y <= dy and dy + dh <= y + h


WARM_IDLE_MS = int(re.findall(r'#define WARM_IDLE_MS (\d+)', (host.ROOT/'patch/name_order.c').read_text())[0])
NO_SONGS = 1
WARM_SQL = b'select * from songs'  # library_query leaves it as it is, an open's and the warm's alike


class Warm:
    """name_order.c's load ahead over Sorts: getAllMusic is Songs' query (library_warm's for NO_SONGS); open() is
    the Library's open of Songs; the thread a poll starts is held (threads) until the test runs it, any other runs
    as it starts; the clock is now."""
    def __init__(self, fw, st, n=NO_MIN + 5, extra=0):
        self.fw, self.s, self.threads, self.now, self.extra = fw, Sorts(fw, n), [], 100000, extra
        self.files = Files(fw, card=True)  # the card mounted: a load ahead reads its library
        self.polling = False
        fw.mock('getAllMusic', self.query)
        fw.mock('fw_deque_pop_back', lambda q, *_: self.s.q[q].pop() and 0)
        fw.mock('time_now_ms', lambda *_: self.now)
        fw.mock('fw_pthread_create', self.create)
        fw.var('g_backlight_status', C.c_ubyte).value = 0  # dark: no input to wait out
        st('last_input', C.c_uint).value = 0
        self.gen = fw.var('library_gen', C.c_uint)
        self.poll_fn = fw.fn('name_order_poll', None)
        self.list_is = fw.fn('name_order_list', None, C.c_uint)
        C.memset(C.addressof(fw.var('name_order__nw', C.c_ubyte)), 0, fw.sizes['name_order__nw'])

    def query(self, *_):
        n = self.s.named(b'db', WARM_SQL, None, 1)
        for i in range(self.extra):  # stock's rows after its sort, as getAllArtist's Unknown row
            self.s.q[self.s.staging.value].append(self.s.record(self.s.ids[i]))
        return n

    def create(self, out, attr, run, arg, *_):
        C.c_ulong.from_address(out).value = 99
        if self.polling:
            self.threads.append(run)
        else:
            C.CFUNCTYPE(C.c_void_p, C.c_void_p)(run)(None)
        return 0

    def poll(self):
        self.polling = True
        try:
            self.poll_fn()
        finally:
            self.polling = False

    def run(self):
        C.CFUNCTYPE(C.c_void_p, C.c_void_p)(self.threads.pop())(None)

    def open(self):
        self.s.calls.clear()
        self.list_is(NO_SONGS)
        self.query()
        self.list_is(0)
        return list(self.s.calls)

    def change(self):
        """The records change (another title: an import's or a scan's) and library_gen moves."""
        self.s.titles[5] = 'T00' if self.s.titles[5] != 'T00' else 'T01'
        self.gen.value += 1


def test_a_changed_library_sorts_a_kept_list_ahead_and_its_open_takes_that_order(fw, st):
    """name_order.c: after library_gen moves, a Library list with a kept order is loaded again in the UI's quiet;
    its records, no longer the kept ones, are sorted on a thread of their own with stock's comparator; the next
    open moves its rows into that order without a sort, the order a sort at the open would have given."""
    w = Warm(fw, st)
    assert w.open() == SORTED
    w.change()
    w.s.calls.clear()
    w.poll()
    assert w.s.calls == [('dbquery', 0)] and len(w.threads) == 1  # loaded on the UI thread, not sorted there
    w.run()
    assert w.s.calls[-1] == ('sort', CMP['NAME_CMP_PINYIN'])
    w.poll()  # the sort collected and kept
    assert w.open() == [('dbquery', 0)] and w.s.listed() == w.s.order()
    w.s.calls.clear()
    w.poll()
    assert not w.s.calls and not w.threads  # nothing more due


def test_a_load_ahead_of_unchanged_records_sorts_nothing(fw, st):
    w = Warm(fw, st)
    w.open()
    w.gen.value += 1  # an import that changed none of these records
    w.s.calls.clear()
    w.poll()
    assert w.s.calls == [('dbquery', 0)] and not w.threads
    assert w.open() == [('dbquery', 0)] and w.s.listed() == w.s.order()


def boot_round(w):
    """The polls of a boot's own round: Songs, Albums and Artists each loaded ahead once (Albums' and
    Artists' queries are stock's stand-ins here, which load nothing), each sort ahead run and kept."""
    for _ in range(3):
        w.poll()
        while w.threads:
            w.run()
            w.poll()


def test_after_a_boot_every_library_list_is_loaded_ahead_opened_or_not(fw, st):
    """A boot's first quiet loads Songs, Albums and Artists ahead whether or not they were opened (a firmware
    update leaves every kept order another firmware's), and the first open takes the order sorted ahead. Dark, a
    boot's first passes touch nothing: the round waits for the screen's first light."""
    w = Warm(fw, st)
    w.s.calls.clear()
    w.poll()
    assert not w.s.calls and not w.threads  # dark since the boot
    fw.var('g_backlight_status', C.c_ubyte).value = 1  # lit, its last input long ago
    w.poll()
    assert w.s.calls == [('dbquery', 0)] and len(w.threads) == 1
    w.run()
    w.poll()
    assert w.open() == [('dbquery', 0)] and w.s.listed() == w.s.order()


def test_a_list_never_opened_is_sorted_ahead_after_a_change(fw, st):
    w = Warm(fw, st)
    w.query()  # the same rows from no Library list's open: kept, the list never opened
    boot_round(w)
    w.change()
    w.s.calls.clear()
    w.poll()
    assert w.s.calls == [('dbquery', 0)] and len(w.threads) == 1
    w.run()
    w.poll()
    assert w.open() == [('dbquery', 0)] and w.s.listed() == w.s.order()


def test_nothing_is_loaded_ahead_without_the_card(fw, st):
    w = Warm(fw, st)
    w.files.files.pop('/proc/mounts')
    w.s.calls.clear()
    w.poll()
    assert not w.s.calls and not w.threads


def test_nothing_more_is_loaded_ahead_until_library_gen_moves(fw, st):
    w = Warm(fw, st)
    w.open()
    boot_round(w)
    w.s.calls.clear()
    w.poll()
    assert not w.s.calls and not w.threads


def test_a_lit_screen_waits_for_quiet_and_a_scan_for_its_end(fw, st):
    w = Warm(fw, st)
    w.open()
    w.change()
    fw.var('g_backlight_status', C.c_ubyte).value = 1
    st('last_input', C.c_uint).value = w.now - WARM_IDLE_MS + 1
    w.s.calls.clear()
    w.poll()
    assert not w.s.calls
    w.now += 1  # WARM_IDLE_MS since the last input
    claim(O['SCAN_THREAD'])
    saved = C.string_at(O['SCAN_DONE'], 8)
    try:
        C.c_int.from_address(O['SCAN_THREAD']).value, C.c_int.from_address(O['SCAN_DONE']).value = 1, 0
        w.poll()
        assert not w.s.calls  # a scan running: its end moves library_gen again
    finally:
        C.memmove(O['SCAN_DONE'], saved, 8)
        C.c_int.from_address(O['SCAN_THREAD']).value = 0
    w.poll()
    assert w.s.calls == [('dbquery', 0)] and len(w.threads) == 1


def test_rows_stock_adds_after_its_sort_are_left_out_of_the_sort_ahead(fw, st):
    """getAllArtist and its kin add an Unknown row after their query's sort: the warm sorts the records it hashed,
    and keeps them only while they are those."""
    w = Warm(fw, st, extra=2)
    w.open()
    w.change()
    w.poll()
    w.run()
    w.poll()
    w.s.calls.clear()
    w.list_is(NO_SONGS)
    w.s.named(b'db', WARM_SQL, None, 1)
    assert w.s.calls == [('dbquery', 0)] and w.s.listed() == w.s.order()


def test_an_open_that_meets_the_sort_ahead_waits_for_it_rather_than_sorting(fw, st):
    w = Warm(fw, st)
    w.open()
    w.change()
    w.poll()
    assert len(w.threads) == 1
    sleeps = []
    fw.mock('fw_usleep', lambda us, *_: sleeps.append(us) or (len(sleeps) == 3 and w.run()) or 0)
    assert w.open() == [('dbquery', 0), ('sort', CMP['NAME_CMP_PINYIN'])]  # the warm's sort, run while it waited
    assert len(sleeps) == 3 and w.s.listed() == w.s.order()
    w.s.calls.clear()
    w.poll()
    assert not w.threads and not w.s.calls


def test_a_changed_list_takes_its_own_slot_not_another_lists(fw, st):
    """Four slots: a list's new order replaces that list's old one, so three other kept loads stay kept."""
    w = Warm(fw, st)
    w.open()
    for sql in (b'select * from a', b'select * from b', b'select * from c'):
        assert w.s.named(b'db', sql, None, 1) == len(w.s.ids)
    was, w.s.titles[5] = w.s.titles[5], 'T00'
    assert w.open() == SORTED  # the records changed: Songs sorted again, in Songs' slot
    w.s.titles[5] = was  # the other three loads' records as they were kept
    for sql in (b'select * from a', b'select * from b', b'select * from c'):
        w.s.calls.clear()
        w.s.named(b'db', sql, None, 1)
        assert w.s.calls == [('dbquery', 0)], sql


def test_after_a_reboot_a_changed_list_is_sorted_ahead(fw, st, library):
    w = Warm(fw, st)
    w.open()
    reboot(fw, library)
    C.memset(C.addressof(fw.var('name_order__nw', C.c_ubyte)), 0, fw.sizes['name_order__nw'])
    w.change()
    w.poll()
    assert len(w.threads) == 1  # the card's head named Songs


def library_page(ui):
    """The Library page on top: its rows' view and stock's All Songs (1), Album (2) and Artist (3) rows, each a
    button holding the button the click goes to, named as stock names them."""
    win = ui.widget('window', ui.wm, (0, 0, 375, 320), name='localmusic_page')
    view = ui.widget('scroll_view', win, (0, 58, 375, 262), name='scroll_view_localmusic')
    rows = {}
    for i in (1, 2, 3, 4):
        row = ui.widget('button', view, (0, 58 + 32 * i, 375, 32), name=str(i))
        rows[i] = ui.widget('button', row, (0, 0, 375, 32), name=str(i), click=True)
    ui.top = win
    return win, rows


def click_event():
    e = C.create_string_buffer(0x40)
    struct.pack_into('<i', e, O['EVENT_TYPE'], EVT_CLICK)
    return e


def catalogue_job(fw, running):
    """catalogue.c's job as a first import runs it (nothing indexed), or none."""
    from test_catalogue import cat_head
    c = cat_head(fw)
    c.running, c.job_app, c.job_seq = running, 0, b'5'


@pytest.mark.parametrize('row', [1, 2, 3])
def test_songs_albums_and_artists_pressed_before_the_servers_rows_wait_then_open(fw, ui, row):
    """While a signed-in server's rows are on their way with none shown, a press on All Songs, Album or Artist
    opens nothing yet: the loading wait, captioned, holds the Library page, and once the rows are in the press is
    made again."""
    win, rows = library_page(ui)
    sent = []
    fw.mock('stock_dispatch_trampoline', lambda w, e, *_: sent.append(w) or 0)
    wait = fw.fn('library_row_wait', C.c_int, C.c_void_p, C.c_void_p)
    catalogue_job(fw, 1)
    assert wait(rows[row].at, click_event()) == 1 and fw.fn('load_waiting', C.c_int)()
    for t in list(ui.timers):
        ui.fire(t)
    assert not sent  # the rows still on their way
    catalogue_job(fw, 0)
    for t in list(ui.timers):
        ui.fire(t)
    assert sent == [rows[row].at] and not fw.fn('load_waiting', C.c_int)()


def test_a_press_waits_only_for_those_rows_and_only_while_the_rows_are_on_their_way(fw, ui):
    win, rows = library_page(ui)
    wait = fw.fn('library_row_wait', C.c_int, C.c_void_p, C.c_void_p)
    catalogue_job(fw, 1)
    assert wait(rows[4].at, click_event()) == 0  # Genre: not a list the server's rows fill alone
    catalogue_job(fw, 0)
    assert wait(rows[1].at, click_event()) == 0


def test_return_gives_the_wait_up_and_opens_nothing(fw, ui):
    win, rows = library_page(ui)
    sent = []
    fw.mock('stock_dispatch_trampoline', lambda w, e, *_: sent.append(w) or 0)
    catalogue_job(fw, 1)
    fw.fn('library_row_wait', C.c_int, C.c_void_p, C.c_void_p)(rows[1].at, click_event())
    e = C.create_string_buffer(0x40)
    struct.pack_into('<i', e, O['EVENT_TYPE'], O['EVT_KEY_UP'])
    struct.pack_into('<I', e, O['EVENT_KEY'], KEY_RETURN)
    assert fw.fn('load_over_input', C.c_int, C.c_void_p)(e) == 1
    catalogue_job(fw, 0)
    for t in list(ui.timers):
        ui.fire(t)
    assert not sent and not fw.fn('load_waiting', C.c_int)()


def test_no_framebuffer_no_spinner_and_no_thread_sorts_in_place(fw):
    s = Sorts(fw, NO_MIN)
    fw.mock('fw_open', lambda *_: -1)  # pthread_create's stub starts nothing: sorted in place
    assert s.load() == SORTED and not fw.calls('fw_usleep') and not fw.calls('fw_mmap')


# What is painted over a list.
@pytest.fixture(scope='module')
def scratch(library):
    """scratch_t's field offsets as this build lays it out: its pointers move them."""
    return host.offsets(library.parent/'navigation.o', 'scratch_t')


@pytest.fixture
def st(fw, scratch):
    base = C.addressof(fw.var('st', C.c_char))
    return lambda name, ctype=C.c_int: ctype.from_address(base + scratch[name])


LCD_FILL = 0x12345678


class Canvas:
    """A canvas with an LCD (its fill colour LCD_FILL) and a clip of the whole screen; what it is asked
    to fill (colour, x, y, w, h), draw (icons, text) and clip to, in order."""
    def __init__(self, fw, clip=(0, 0, 375, 320)):
        self.block, self.lcd = C.create_string_buffer(0x100), C.create_string_buffer(0x200)
        struct.pack_into('<Q', self.block, O['CANVAS_LCD'], C.addressof(self.lcd))
        struct.pack_into('<I', self.lcd, O['LCD_FILL_COLOR'], LCD_FILL)
        self.at, self.clip = C.addressof(self.block), clip
        self.fill = self.text_color = self.stroke = 0
        self.fills, self.icons, self.named, self.clips, self.texts, self.boxes, self.images = [], [], [], [], [], [], {}
        fw.mock('canvas_set_fill_color', lambda c, color, *_: setattr(self, 'fill', color & 0xffffffff))
        fw.mock('canvas_fill_rect', lambda c, *box: self.fills.append((self.fill, *(C.c_int(v).value for v in box[:4]))))
        fw.mock('canvas_get_clip_rect', self.get_clip)
        fw.mock('canvas_set_clip_rect', lambda c, r, *_: self.clips.append(tuple((C.c_int * 4).from_address(r))))
        fw.mock('canvas_draw_icon', self.icon)
        fw.mock('canvas_set_text_color', lambda c, color, *_: setattr(self, 'text_color', color & 0xffffffff))
        fw.mock('canvas_measure_text', lambda *_: 10.0)  # every character 10 px
        fw.mock('canvas_draw_text', lambda c, s, n, x, y, *_: self.texts.append((self.chars(s, n), C.c_int(x).value, C.c_int(y).value, self.text_color)))
        fw.mock('canvas_draw_text_in_rect', lambda c, s, n, r, *_: self.boxes.append((self.chars(s, n), tuple((C.c_int * 4).from_address(r)), self.text_color)))
        fw.mock('widget_load_image', self.load)
        self.rounds, self.declines, self.strokes, self.drawn = [], 0, [], []  # declines: no vgcanvas
        fw.mock('canvas_fill_rounded_rect', lambda c, r, _, color, radius, *__: self.rounded('fill', r, color, radius))
        fw.mock('canvas_stroke_rounded_rect', lambda c, r, _, color, radius, *__: self.rounded('stroke', r, color, radius))
        fw.mock('canvas_set_stroke_color', lambda c, color, *_: setattr(self, 'stroke', color & 0xffffffff))
        fw.mock('canvas_stroke_rect', lambda c, *box: self.strokes.append((self.stroke, *(C.c_int(v).value for v in box[:4]))))
        fw.mock('canvas_draw_image', lambda c, bitmap, src, dst, *_: self.drawn.append((self.images[bitmap], tuple((C.c_int * 4).from_address(dst)))))

    def icon(self, c, bitmap, x, y, *_):
        """An icon drawn: its bitmap, and in named the image it held then (a caller may load each into one
        bitmap on its stack)."""
        self.icons.append((bitmap, C.c_int(x).value, C.c_int(y).value))
        self.named.append((self.images.get(bitmap), C.c_int(x).value, C.c_int(y).value))

    def rounded(self, kind, r, color, radius):
        self.rounds.append((kind, tuple((C.c_int * 4).from_address(r)), C.c_uint.from_address(color).value, radius))
        return self.declines

    @staticmethod
    def chars(s, n):
        return ''.join(chr(c) for c in (C.c_uint * n).from_address(s))

    def get_clip(self, c, r, *_):
        (C.c_int * 4).from_address(r)[:] = list(self.clip)

    def load(self, w, name, bitmap, *_):
        """Each image 12 x 16, at the address of the bitmap it filled."""
        (C.c_uint * 2).from_address(bitmap)[:] = [12, 16]
        self.images[bitmap] = C.string_at(name)


PAGE_BLACK = 0xff000000  # RGBA(0): the page's black, which the corners are painted in


def painted(fills, size):
    """How much of each pixel of a size-px square art the fills cover in the page's black, 0 to 1, as
    alpha blending builds it up: a fill at alpha a leaves 1 - a of what was under it."""
    cover = {}
    for color, x, y, w, h in fills:
        assert color & 0xffffff == PAGE_BLACK & 0xffffff  # all in the page's black, at any alpha
        for px in range(x, x + w):
            for py in range(y, y + h):
                assert 0 <= px < size and 0 <= py < size  # on the art
                cover[px, py] = 1 - (1 - cover.get((px, py), 0)) * (1 - (color >> 24) / 255)
    return cover


def art(ui, size=166, box=None):
    return ui.widget('image', ui.widget('window', ui.wm, (0, 0, 375, 320)), box or (10, 20, size, size))


def test_now_playings_art_has_round_corners_in_the_pages_black(fw, ui, st):
    size = 166
    canvas, cover = Canvas(fw), art(ui, size)
    st('np_cover', C.c_void_p).value = cover.at
    fw.fn('navigation__paint_cover', None, C.c_void_p, C.c_void_p)(cover.at, canvas.at)
    r = OLED['np_art_radius']  # theme.xml's playing.art.radius, in px at any size
    assert r
    black = painted(canvas.fills, size)
    assert {y for _, y in black} == {*range(r), *range(size - r, size)}  # r rows a corner; the straight edges are the art's own
    cover = art_py.corner_cover(r)
    for y in range(size):
        for x in range(size):
            cx, cy = min(x, size - 1 - x), min(y, size - 1 - y)
            # each corner pixel black by the share of it the arc leaves, along the flat of the arc as much as the steep
            want = 1 - cover[cy * r + cx] / 255 if cx < r and cy < r else 0
            assert abs(black.get((x, y), 0) - want) <= 1 / 255, (x, y)
    assert canvas.fill == LCD_FILL


def test_the_arts_corners_are_the_themes_px_at_any_size(fw, ui, st):
    rows = []
    for size in (166, 83):
        canvas, cover = Canvas(fw), art(ui, size)
        st('np_cover', C.c_void_p).value = cover.at
        fw.fn('navigation__paint_cover', None, C.c_void_p, C.c_void_p)(cover.at, canvas.at)
        rows.append(len({y for _, _, y, _, _ in canvas.fills if y < size // 2}))
    assert rows == [OLED['np_art_radius']] * 2


def test_the_full_screen_arts_corners_follow_its_image(fw, ui, st):
    canvas = Canvas(fw)
    window = ui.widget('window', ui.wm, (0, 0, 375, 320))
    image = ui.widget('image', window, (40, 30, 166, 166), name='img_bigcover')
    st('bc', C.c_void_p * 3)[2] = window.at
    fw.fn('navigation__paint_cover', None, C.c_void_p, C.c_void_p)(window.at, canvas.at)
    assert canvas.fills and all(40 <= x and x + w <= 40 + 166 and 30 <= y < 30 + 166 for _, x, y, w, _ in canvas.fills)
    assert {x for _, x, _, w, _ in canvas.fills if w > 1} >= {40}  # from the image's left edge, not the window's


def test_only_the_art_gets_corners(fw, ui, st):
    canvas, cover = Canvas(fw), art(ui)
    st('np_cover', C.c_void_p).value = cover.at
    paint_cover = fw.fn('navigation__paint_cover', None, C.c_void_p, C.c_void_p)
    paint_cover(art(ui).at, canvas.at)  # another image
    paint_cover(cover.at, C.create_string_buffer(0x100))  # a canvas with no LCD
    assert not canvas.fills


# The fast-scroll letter.
def spun(fw, ui, names, name='allmusic_page'):
    """A list spun past one row a tick (the letter's run), and the canvas to paint it on."""
    page = Page(ui, names, name=name)
    paint(fw, page)
    key(fw, ui, KEY_NEXT, ui.now + SLOW)
    spin(fw, ui, KEY_NEXT, 6, 100)
    return page, Canvas(fw)


def letter(fw, page, canvas):
    fw.fn('navigation__paint_letter', None, C.c_void_p, C.c_void_p)(page.view.at, canvas.at)
    return [text for text, _, _ in canvas.boxes]


@pytest.mark.parametrize('title, name, shown', [
    ('beatles', 'allmusic_page', 'B'), ('The Beatles', 'allmusic_page', 'B'), ('An Elephant', 'allmusic_page', 'E'),
    ('A Tribe', 'allmusic_page', 'T'), ('A', 'allmusic_page', 'A'), ('The ', 'allmusic_page', 'T'),  # no name after it
    ('Theory', 'allmusic_page', 'T'), ('  zed', 'allmusic_page', 'Z'),
    ('The Beatles', 'folder_page', 'T'),  # only the Library sorts past "The": a folder sorts by its file name
    ('Éclair', 'allmusic_page', 'É'),
    ('', 'allmusic_page', None), ('   ', 'allmusic_page', None),
])
def test_the_letter_is_the_one_the_selected_row_sorts_under(fw, ui, title, name, shown):
    page, canvas = spun(fw, ui, titles(200), name)
    page.rows[page.selected].children[0].text = title
    assert letter(fw, page, canvas) == ([shown] if shown else [])


def test_the_letter_sits_in_the_middle_of_the_list_and_puts_the_canvas_back(fw, ui):
    page, canvas = spun(fw, ui, titles(200))
    letter(fw, page, canvas)
    (_, (x, y, w, h), ink), = canvas.boxes
    assert (x, y) == ((375 - w) // 2, (VIEW_H - h) // 2) and w == h
    (kind, box, fill, _), = canvas.rounds
    assert kind == 'fill' and box == (x, y, w, h)  # the letter centred on its square
    assert 0 < fill >> 24 < 255  # a translucent square: the list shows through
    assert all((ink >> s & 255) > (fill >> s & 255) for s in (0, 8, 16))  # light ink on the dark square
    assert canvas.fill == LCD_FILL and canvas.clips[-1] == canvas.clip


def test_a_menu_or_a_slow_turn_shows_no_letter(fw, ui):
    page, canvas = spun(fw, ui, titles(200), 'sysset_page')  # a fixed menu is not alphabetical
    assert letter(fw, page, canvas) == []
    slow = Page(ui, titles(200))
    paint(fw, slow)
    spin(fw, ui, KEY_NEXT, 6, SLOW)
    assert letter(fw, slow, canvas) == []


def test_the_letter_goes_with_its_timer_or_a_touch(fw, ui):
    page, canvas = spun(fw, ui, titles(200))
    ui.fire(ui.due('letter_expire')[0])
    assert letter(fw, page, canvas) == []
    page, canvas = spun(fw, ui, titles(200))
    fw.fn('ringnav_touch', C.c_int, C.c_void_p, C.c_void_p)(None, C.create_string_buffer(0x40))
    assert letter(fw, page, canvas) == []


def test_homes_library_pane_shows_no_letter(fw, ui):
    page, canvas = spun(fw, ui, titles(200), 'home_page')  # Library's categories beside Home's pane: not A to Z
    page.view.props[b'name'] = b'scroll_view_localmusic'
    assert letter(fw, page, canvas) == []


@pytest.mark.parametrize('big', [b'0', b'1'])  # Fast Navigation off or on: Cover Flow's own letters change, not this one
def test_coverflows_album_tracks_show_no_letter(fw, ui, big):
    Config(fw, BIGLIBRARY=big)
    assert fw.fn('ringnav_big_library', C.c_int)() == int(big)
    page = Page(ui, titles(200), name='coverflow_page')
    album = [Rec('Abbey Road', artist='The Beatles')]  # the open album: its key is the track list's scope
    deque_of(fw, album)
    flow = cf(fw)
    flow.page, flow.albums, flow.tracks, flow.album, flow.screen = page.win.at, 0x5000, 0x5100, 0, TRACKS
    paint(fw, page)
    key(fw, ui, KEY_NEXT, ui.now + SLOW)
    spin(fw, ui, KEY_NEXT, 6, 100)
    canvas = Canvas(fw)
    assert letter(fw, page, canvas) == []  # its tracks are in album order (disc and track), not A to Z
    flow.screen = COVERS
    assert letter(fw, page, canvas) == ['S']  # the same spin with no album open keeps its letter


@pytest.mark.parametrize('big', [b'0', b'1'])  # Fast Navigation off or on
@pytest.mark.parametrize('name, cls', [('albuminfo_page', 0xff10), ('albuminfo_page', 0xff11),
                                       ('playerjumpinfo_page', 0xff10)])
def test_an_albums_songs_in_the_library_show_no_letter(fw, ui, name, cls, big):
    Config(fw, BIGLIBRARY=big)
    assert fw.fn('ringnav_big_library', C.c_int)() == int(big)
    fw.var('fw_ctx_g_class_type', C.c_int).value = cls
    page, canvas = spun(fw, ui, titles(200), name)
    assert letter(fw, page, canvas) == []  # in disc and track order, not A to Z
    Page(ui, titles(200), name='album_page')  # Return: the page behind is the top while the songs slide out
    assert letter(fw, page, canvas) == []


@pytest.mark.parametrize('name, cls', [
    ('album_page', O['CLASS_ALBUMS']),  # the Albums list a Return brings back
    ('artistinfo_page', 0xff11),  # Go to Artist from an album, one of its albums opened, Return: stock reloads nothing
])
def test_a_name_ordered_library_list_keeps_its_letter(fw, ui, name, cls):
    fw.var('fw_ctx_g_class_type', C.c_int).value = cls
    C.c_int.from_buffer(fw.var('fw_ctx_g_local_classinfo_save', C.c_ubyte * 912)).value = cls
    page, canvas = spun(fw, ui, titles(200), name)
    assert letter(fw, page, canvas) == ['S']


@pytest.mark.parametrize('name, cls, sort, shown', [
    ('upnext_page', 0xf001, 0, False),  # View Queue while a shuffle is on: the play order
    ('chapters_page', 0xf001, 0, False),  # the file's
    ('allmusic_page', 0xf009, 0, False),  # Recently Added: newest first
    ('allmusic_page', 0xf008, 0, False),  # Frequent: most played first
    ('allmusic_page', 0xf007, 0, False),  # Recent: last played first
    ('allmusic_page', 0xf006, 0, True), ('allmusic_page', 0xf00a, 0, True),  # the classes either side
    ('allmusic_page', 0xff13, 1, False),  # a playlist's songs by Time added (its Sort's 1): its own order
    ('allmusic_page', 0xff13, 0, True),  # a playlist's songs sorted by name
    ('allmusic_page', 0xf001, 3, False), ('allmusic_page', 0xf001, 4, False),  # Songs by Time added, either way
    ('allmusic_page', 0xf001, 0, True), ('allmusic_page', 0xf001, 1, True),  # Songs A to Z, and by file name
    ('allmusic_page', 0xf001, 2, True), ('allmusic_page', 0xf001, 5, True),  # the Sorts either side of Time added
    ('allmusic_page', 0xf002, 3, True),  # Hi-Res is by name whatever Songs' Sort is
    ('folder_page', 0xf009, 3, True),  # Folder after Recently Added: the class and Sort are the song list's alone
])
def test_the_other_lists_in_an_order_of_their_own_show_no_letter(fw, ui, name, cls, sort, shown):
    fw.var('fw_ctx_g_class_type', C.c_int).value = cls
    # a playlist's Sort is its own flag (0 by name, 1 Time added); the others read Songs'
    fw.var('fw_ctx_g_playlistsort_flag' if cls == 0xff13 else 'fw_ctx_g_sort_flag', C.c_int).value = sort
    page, canvas = spun(fw, ui, titles(200), name)
    assert letter(fw, page, canvas) == (['S'] if shown else [])


@pytest.mark.parametrize('mode, shown', [(0, False), (1, True)])  # its songs as added; the Add Songs picker, A to Z
def test_edit_playlists_songs_show_no_letter_but_its_picker_does(fw, ui, mode, shown):
    ple = fw.var('playlists__ple', Ple)
    ple.page, ple.mode = 1, mode
    page, canvas = spun(fw, ui, titles(200), 'playlistedit_page')
    assert letter(fw, page, canvas) == (['S'] if shown else [])


# The letter is opt-in: a list takes it only when its page is one of the name-sorted Library lists (az_index.c
# name_order). Every other long list, whatever its rows say, is in an order of its own: Wi-Fi's networks (by signal),
# Bluetooth's devices, Settings, the queue, the play-count and battery lists, a card app's.
@pytest.mark.parametrize('name', ['wifi_page', 'bluetooth_page', 'sysset_page', 'playerqueue_page', 'mostplayed_page',
                                  'era_albums_page', 'radio_page', 'home_page'])
def test_only_a_name_sorted_library_list_shows_the_letter(fw, ui, name):
    page, canvas = spun(fw, ui, titles(200), name)
    assert letter(fw, page, canvas) == []


@pytest.mark.parametrize('cls', [0xff07, 0xff09])  # an artist's songs, a genre's: unsorted (getMusicBySonger, ByGenre)
def test_an_artists_or_genres_songs_show_no_letter(fw, ui, cls):
    fw.var('fw_ctx_g_class_type', C.c_int).value = cls
    page, canvas = spun(fw, ui, titles(200))
    assert letter(fw, page, canvas) == []


# The iPod `>` on rows that open another list.
def chevrons(fw, page, canvas):
    fw.fn('navigation__paint_chevrons', None, C.c_void_p, C.c_void_p)(page.view.at, canvas.at)
    return canvas.named


def test_a_drill_lists_rows_each_get_a_chevron_lined_up_on_their_middles(fw, ui):
    page, canvas = Page(ui, titles(12), name='playlist_page'), Canvas(fw)
    page.scroll(ROW_H)
    drawn = chevrons(fw, page, canvas)
    shown = [i for i in range(12) if 0 < page.screen_y(i) + ROW_H and page.screen_y(i) < VIEW_H]  # rows 1 to 8
    assert len(drawn) == len(shown) == 8  # the rows in sight: the clip draws no others
    assert {name for name, _, _ in drawn} == {f'{OLED["art_id"]}_chevron_d'.encode()}  # list_marks.c chevron_icon
    assert len({x for _, x, _ in drawn}) == 1 and drawn[0][1] > 375 // 2  # one column, at the right
    assert [y for _, _, y in drawn] == [page.screen_y(i) + ROW_H // 2 for i in shown]
    assert canvas.clips[-1] == canvas.clip


def test_rows_with_more_dots_or_tiles_get_no_chevron(fw, ui):
    page, canvas = Page(ui, titles(6), name='playlist_page'), Canvas(fw)
    ui.widget('image', page.rows[1], (330, 0, 30, 30), name='img_more1')
    struct.pack_into('<i', page.rows[2].block, O['W_W'], 375 // 2 - 1)  # a grid tile
    assert [y for _, _, y in chevrons(fw, page, canvas)] == [ROW_H * i + ROW_H // 2 for i in (0, 3, 4, 5)]


@pytest.mark.parametrize('name, navbar', [('allmusic_page', 0), ('playlist_page', 1)])
def test_no_chevrons_off_a_drill_list_or_in_multi_select(fw, ui, name, navbar):
    fw.var('g_navbar_status', C.c_ubyte).value = navbar
    page, canvas = Page(ui, titles(6), name=name), Canvas(fw)
    assert chevrons(fw, page, canvas) == []


@pytest.mark.parametrize('mode', [1, 2])  # Add to Playlist from a list, from Now Playing
def test_the_add_to_playlist_picker_s_rows_act_in_place_and_get_no_chevron(fw, ui, mode):
    page, canvas = Page(ui, titles(6), name='playlist_page'), Canvas(fw)
    page.win.props[b'_pl_mode'] = mode
    assert chevrons(fw, page, canvas) == []


def test_homes_rows_each_get_the_themes_chevron_the_selected_one_white(fw, ui, st):
    """OLED's home.chevron.visible: every row of Home's list, as on a drill list, on its own middle; the selected
    row's on the bar is the white one (list_marks.c chevron_icon)."""
    assert OLED['chevron_home']
    theme(fw)
    page, canvas = Page(ui, titles(6), name='home_page'), Canvas(fw)
    st('sel_w', C.c_void_p).value = page.view.at
    st('sel_row').value = 2
    drawn = chevrons(fw, page, canvas)
    art = OLED['art_id']
    assert [(name, y) for name, _, y in drawn] == [
        (f'{art}_chevron_{"w" if i == 2 else "d"}'.encode(), ROW_H * i + ROW_H // 2) for i in range(6)]


# The selected row's title marquee.
class Marquee:
    """Row row of page selected and painted, its title an hscroll_label of text_w px in a 300 px label."""
    def __init__(self, fw, ui, st, text_w, row=3, only_focus=1, parent_focus=0):
        self.page = Page(ui, titles(10))
        self.labels = []
        for r in self.page.rows:
            label = ui.widget('hscroll_label', r.children[0], (0, 0, 300, 20))
            label.int(O['HSCROLL_TEXT_W'], text_w)
            label.byte(O['HSCROLL_ONLY_FOCUS'], only_focus)
            label.byte(O['HSCROLL_ONLY_FOCUS'] + 1, parent_focus)  # only_parent_focus, the char after
            self.labels.append(label)
        st('sel_w', C.c_void_p).value = self.page.view.at
        st('sel_row').value = st('marq_id').value = row
        st('marq_row', C.c_void_p).value = self.page.rows[row].at
        self.label = self.labels[row]
        self.moved = fw.fn('navigation__marq_moved', C.c_int, C.c_void_p)


def test_the_selected_rows_long_title_scrolls(fw, ui, st):
    marquee = Marquee(fw, ui, st, 400)
    marquee.moved(None)
    assert [a[0] for _, a in fw.calls('hscroll_label_start')] == [marquee.label.at]
    assert marquee.label.byte(O['HSCROLL_LOOP']) == 1
    assert (marquee.label.at, 0) in [a[:2] for _, a in fw.calls('hscroll_label_set_only_focus')]


def test_a_long_title_stock_gave_its_parents_focus_flag_scrolls_too(fw, ui, st):
    """set_hscroll_label_attribute's labels (stock's Wi-Fi networks, its settings rows) wait for their parent's focus,
    which no row has under ringnav (only_parent_focus, 0x79): the selected row's long one scrolls as an only_focus
    one does, its flag off while it runs and back once the selection moves on (user, 2026-11-16: Wi-Fi names
    clipped)."""
    marquee = Marquee(fw, ui, st, 400, only_focus=0, parent_focus=1)
    marquee.moved(None)
    assert [a[0] for _, a in fw.calls('hscroll_label_start')] == [marquee.label.at]
    assert marquee.label.byte(O['HSCROLL_ONLY_FOCUS'] + 1) == 0
    st('sel_row').value = 4
    marquee.moved(None)
    assert marquee.label.byte(O['HSCROLL_ONLY_FOCUS'] + 1) == 1 and marquee.label.byte(O['HSCROLL_ONLY_FOCUS']) == 0


@pytest.mark.parametrize('text_w, only_focus', [(300 - 4, 1), (400, 0)])
def test_a_title_that_fits_or_scrolls_anyway_is_left_alone(fw, ui, st, text_w, only_focus):
    marquee = Marquee(fw, ui, st, text_w, only_focus=only_focus)
    marquee.moved(None)
    assert not fw.calls('hscroll_label_start')


def test_albums_artist_line_and_grid_titles_take_stock_s_attributes_then_a_focus_flag(fw):
    """tools/build.py ALBUM_LABELS: stock's helper first, then the flag V1.34's rows set outright, so the
    label waits for a focus its parent never has under ringnav."""
    label = C.create_string_buffer(0x100)
    fw.fn('ringnav_album_label', None, C.c_void_p)(label)
    here = C.addressof(label)
    assert [(n, a[:2] if n.endswith('focus') else a[:1]) for n, a in fw.calls() if 'hscroll' in n] == [
        ('set_hscroll_label_attribute', (here,)), ('hscroll_label_set_only_parent_focus', (here, 1))]


def test_the_marquee_stops_once_the_selection_moves_on(fw, ui, st):
    marquee = Marquee(fw, ui, st, 400)
    marquee.moved(None)
    st('sel_row').value = 4
    marquee.moved(None)
    assert [a[0] for _, a in fw.calls('hscroll_label_stop')] == [marquee.label.at]
    assert fw.calls('hscroll_label_set_only_focus')[-1][1][:2] == (marquee.label.at, 1)  # its ellipsis back
    assert len(fw.calls('hscroll_label_start')) == 1 and st('marq_row', C.c_void_p).value is None


def test_a_destroyed_marquee_label_is_not_stopped(fw, ui, st):
    marquee = Marquee(fw, ui, st, 400)
    marquee.moved(None)
    (w, kind, fn, ctx), = [a[:4] for _, a in fw.calls('widget_on')]
    assert (w, kind) == (marquee.label.at, O['EVT_DESTROY'])
    C.CFUNCTYPE(C.c_int, C.c_void_p, C.c_void_p)(fn)(ctx, None)
    marquee.moved(None)
    assert not fw.calls('hscroll_label_stop')


def test_a_pair_over_the_list_leaves_its_rows_no_marquee(fw, ui, st):
    """An artist's or album's pair holding the selection (artist_page.c pair_holds): the list paints no bar, and the
    marquee a fast spin armed on the row it passed through finds no row, so that title never scrolls lit."""
    marquee = Marquee(fw, ui, st, 400)
    marquee.moved(None)  # running on row 3
    stop = C.c_byte(1)  # the pair's first stop lit
    ar = fw.var('artist_page__ar', C.c_void_p * 6)
    ar[1], ar[5] = marquee.page.view.at, C.addressof(stop)  # its list, its stop (head, list, panels, ctx, stop)
    paint(fw, marquee.page)
    assert [a[0] for _, a in fw.calls('hscroll_label_stop')] == [marquee.label.at]
    assert st('marq_row', C.c_void_p).value is None
    marquee.moved(None)
    assert len(fw.calls('hscroll_label_start')) == 1  # the first run's alone


@pytest.mark.parametrize('speed, paced', [(0.0, True), (0.2, True), (0.02, False)])
def test_every_scrolling_label_holds_then_runs_at_most_the_pace(fw, speed, paced):
    label = C.create_string_buffer(0x100)
    struct.pack_into('<f', label, O['HSCROLL_SPEED'], speed)
    fw.fn('ringnav_hscroll_start', C.c_int, C.c_void_p)(label)
    assert struct.unpack_from('<i', label, O['HSCROLL_DELAY'])[0] == O['MARQUEE_HOLD_MS']
    pace = struct.unpack('<f', struct.pack('<I', O['HSCROLL_PACE']))[0]
    assert struct.unpack_from('<f', label, O['HSCROLL_SPEED'])[0] == pytest.approx(pace if paced else speed)
    assert fw.calls('stock_hscroll_start_trampoline')[0][1][0] == C.addressof(label)


@pytest.mark.parametrize('paused', [0, 1])
def test_a_scrolling_labels_tick_counts_the_time_since_the_last(fw, paused):
    """Stock's tick adds the timer's 16 ms period to the pass; ringnav_hscroll_tick adds the rest of the time since the
    last tick (now less start), so a tick a 45 ms loop pass late moves the text 45 ms' worth. A paused label's, none."""
    label, info = C.create_string_buffer(0x100), C.create_string_buffer(0x58)
    struct.pack_into('<I', label, O['HSCROLL_ELAPSED'], 320)
    struct.pack_into('<B', label, O['HSCROLL_PAUSED'], paused)
    C.c_void_p.from_buffer(info, O['TIMER_CTX']).value = C.addressof(label)
    struct.pack_into('<QQI', info, O['TIMER_NOW'], 0x1_0000_0010, 0xffff_ffe3, 16)  # 45 ms across the low word's wrap
    fw.fn('ringnav_hscroll_tick', C.c_int, C.c_void_p)(info)
    assert struct.unpack_from('<I', label, O['HSCROLL_ELAPSED'])[0] == 320 + (0 if paused else 45 - 16)
    assert fw.calls('stock_hscroll_tick_trampoline')[0][1][0] == C.addressof(info)


# The Display settings the list engine reads.


@pytest.mark.parametrize('light', [0, 1])
def test_unflip_finds_the_grey_light_mode_draws_as_v(fw, light):
    theme(fw, LIGHT=light)
    fw.fn('accent', C.c_int)()  # the settings read, as ipod_light() has done wherever flip runs
    unflip = fw.fn('unflip', C.c_uint, C.c_uint)
    flip_v = fw.fn('flip_v', C.c_uint, C.c_uint, C.c_uint, C.c_uint)
    flip = fw.fn('flip', C.c_uint, C.c_uint)
    paper, ink = flip(0xff000000) & 255, flip(0xffffffff) & 255  # where black and white go
    for v in range(256):
        u = unflip(v)
        if not light:
            assert u == v
        elif ink <= v <= paper:
            assert abs(flip_v(u, paper, ink) - v) <= 1 and 0 <= u <= 255
        else:
            assert u == (255 if v < ink else 0)  # past the ends: the nearest Light Mode can draw


# Pills and gradients.
class Rect(C.Structure):
    _fields_ = [(k, C.c_int) for k in 'xywh']


def gradient(fw, canvas, r, top, bottom, hi, radius):
    fw.fn('navigation__gradient', None, C.c_void_p, Rect, C.c_uint, C.c_uint, C.c_uint, C.c_int)(canvas.at, Rect(*r), top, bottom, hi, radius)
    rows = {}
    for color, x, y, w, h in canvas.fills:
        rows.setdefault(y, []).append((color, x, w))
    return rows


def rgba(c, a=0xff):
    return a << 24 | (c & 255) << 16 | c & 0xff00 | c >> 16


def test_a_square_gradient_runs_top_to_bottom_under_its_highlight(fw):
    canvas = Canvas(fw)
    rows = gradient(fw, canvas, (5, 10, 100, 9), 0x000000, 0x8080ff, 0xffffff, 0)
    assert sorted(rows) == list(range(10, 19)) and all(len(v) == 1 and v[0][1:] == (5, 100) for v in rows.values())
    colors = [rows[y][0][0] for y in range(10, 19)]
    assert colors[0] == rgba(0xffffff) and colors[-1] == rgba(0x8080ff)
    blues = [c >> 16 & 255 for c in colors[1:]]
    assert blues == sorted(blues) and blues[0] < 0xff  # from top's toward bottom's


def test_a_pill_gradient_rounds_both_ends_alike(fw):
    canvas = Canvas(fw)
    h = 20
    rows = gradient(fw, canvas, (0, 0, 120, h), 0x202020, 0x404040, 0x606060, h // 2)
    def inset(y):
        return min(x for _, x, w in rows[y] if w > 1)
    insets = [inset(y) for y in range(h)]
    assert insets == insets[::-1] and insets[0] > h // 4 and insets[h // 2 - 1] <= 1
    assert insets[:h // 2] == sorted(insets[:h // 2], reverse=True)
    for y in range(h):  # an end's part pixels sit just outside the band, both sides alike
        edge = sorted((x, color >> 24) for color, x, w in rows[y] if w == 1)
        left, right = edge[:len(edge) // 2], edge[len(edge) // 2:]
        assert [a for _, a in left] == [a for _, a in right][::-1] and all(0 < a < 255 for _, a in edge)
        assert all(x < inset(y) for x, _ in left) and all(x >= 120 - inset(y) for x, _ in right)


def pill(fw, ui, h, children, inset=0):
    row = ui.widget('list_item', None, (0, 0, 300, h))
    for box, kind, props, visible in children:
        c = ui.widget(kind, row, box, visible=visible)
        c.props.update(props)
    return fw.fn('navigation__pill_radius', C.c_int, C.c_void_p, C.c_int, C.c_int)(row.at, h, inset)


def test_a_pill_without_art_is_half_its_height(fw, ui):
    assert pill(fw, ui, 40, []) == 20


@pytest.mark.parametrize('box', [(4, 4, 32, 32), (10, 2, 36, 36), (2, 8, 30, 24)])
def test_a_pill_clears_a_leading_covers_corner(fw, ui, box):
    h = 40
    r = pill(fw, ui, h, [(box, 'image', {}, True)])
    x, y = box[0], min(box[1], h - box[1] - box[3])
    def inside(r):
        return (r - x) ** 2 + (r - y) ** 2 <= r * r
    assert r < h // 2 and inside(r) and (r + 1 > h // 2 or not inside(r + 1))


@pytest.mark.parametrize('kind, props, visible, x', [
    ('image', {b'draw_type': O['IMAGE_DRAW_CENTER']}, True, 4), ('image', {}, False, 4),
    ('label', {}, True, 4), ('image', {}, True, 25),
])
def test_icons_hidden_checks_text_and_art_past_the_end_leave_the_pill(fw, ui, kind, props, visible, x):
    assert pill(fw, ui, 40, [((x, 4, 32, 32), kind, props, visible)]) == 20


# Finding the pane the wheel drives.
@pytest.mark.parametrize('depth, filler, found', [(3, 0, True), (20, 0, False), (0, 50, True), (0, 600, False)])
def test_the_pane_search_is_bounded(fw, ui, depth, filler, found):
    win = ui.widget('window', ui.wm, (0, 30, 375, 290), name='allmusic_page')
    for _ in range(filler):  # widgets before the list that hold nothing to navigate
        ui.widget('label', win, (0, 0, 10, 10))
    parent = win
    for _ in range(depth):
        parent = ui.widget('view', parent, (0, 0, 375, VIEW_H))
    view = scroll_list(ui, parent, titles(20))
    ui.top = win
    key(fw, ui, KEY_NEXT)
    assert (view.props.get(SEL, -1) == 1) == found


@pytest.mark.parametrize('active, moved', [(1, 1), (0, 0), (-1, None)])
def test_the_wheel_drives_only_the_shown_tabs_list(fw, ui, active, moved):
    win = ui.widget('window', ui.wm, (0, 30, 375, 290), name='allmusic_page')
    pages = ui.widget('pages', win, (0, 0, 375, VIEW_H))
    pages.props[b'active'] = active
    tabs = [scroll_list(ui, pages, titles(20, 100 * k)) for k in range(2)]
    ui.top = win
    key(fw, ui, KEY_NEXT)
    assert [t.props.get(SEL, -1) == 1 for t in tabs] == [k == moved for k in range(2)]


def test_a_dialogs_hidden_tab_has_no_buttons_to_reach(fw, ui):
    win = ui.widget('dialog', ui.wm, (40, 100, 300, 120), name='confirminfo_dialog')
    pages = ui.widget('pages', win, (0, 0, 300, 120))
    pages.props[b'active'] = 0
    for k, n in enumerate((2, 3)):
        tab = ui.widget('view', pages, (0, 0, 300, 120))
        for i in range(n):
            ui.widget('button', tab, (20 + 90 * i, 70, 80, 40), click=True, text=f'{k}{i}')
    ui.top = win
    for _ in range(4):
        key(fw, ui, KEY_NEXT, ui.now + SLOW)
    assert win.props[SEL] == 1  # the shown tab's last button: the hidden tab's three are not rows


def test_a_row_whose_click_is_going_takes_its_inner_button_as_the_target(fw, ui):
    page = Page(ui, titles(10))
    row = page.rows[2]
    row.item[O['EMIT_PENDING_REMOVE']] = b'\1'
    button = ui.widget('button', row, (300, 0, 60, ROW_H), click=True)
    clicks = centre(fw, ui, page, 2)
    ui.fire(ui.due('confirm_center')[0])
    assert clicks.seen == [(button.at, EVT_CLICK)]


def two_panes(ui):
    win = ui.widget('window', ui.wm, (0, 30, 375, 290), name='allmusic_page')
    panes = [scroll_list(ui, win, titles(20, 100 * k), (190 * k, 0, 180, VIEW_H)) for k in range(2)]
    ui.top = win
    return win, panes


def test_of_two_panes_the_wheel_takes_the_first_then_the_one_holding_a_selection(fw, ui):
    win, (left, right) = two_panes(ui)
    key(fw, ui, KEY_NEXT)
    assert left.props[SEL] == 1 and SEL not in right.props
    del left.props[SEL]
    scope = b'_ringnav_scope'
    right.props.update({SEL: 4, COUNT: 20, scope: left.props[scope]})  # loaded, its row 4 chosen
    key(fw, ui, KEY_NEXT, ui.now + SLOW)
    assert right.props[SEL] == 5 and SEL not in left.props


def test_two_panes_both_selected_take_no_tick(fw, ui):
    win, (left, right) = two_panes(ui)
    left.props[SEL], right.props[SEL] = 3, 4
    key(fw, ui, KEY_NEXT)
    assert (left.props[SEL], right.props[SEL]) == (3, 4)


def test_a_tap_finds_its_pane_by_ancestry(fw, ui):
    win, (left, right) = two_panes(ui)
    under = fw.fn('navigation__surface_under', C.c_void_p, C.c_void_p, C.c_void_p, C.POINTER(C.c_void_p), C.c_int)
    other = C.c_void_p()
    label = right.children[3].children[0]
    assert under(win.at, label.at, C.byref(other), 0) == right.at and other.value == left.at
    assert under(win.at, win.at, C.byref(other), 0) is None  # the window itself: no pane
    stray = ui.widget('label', ui.widget('view', None, (0, 0, 1, 1)))
    assert under(win.at, stray.at, C.byref(other), 0) is None


def test_the_playlist_picker_keeps_its_own_row(fw, ui):
    chosen(fw, ui, Page(ui, titles(20), name='playlist_page'), 5)
    picker = Page(ui, titles(20), name='playlist_page')
    picker.win.props[b'_pl_mode'] = 1  # Add to Playlist
    paint(fw, picker)
    assert picker.selected == 0
    chosen(fw, ui, picker, 8)
    for mode, row in ((0, 5), (1, 8)):
        again = Page(ui, titles(20), name='playlist_page')
        again.win.props[b'_pl_mode'] = mode
        paint(fw, again)
        assert again.selected == row


@pytest.mark.parametrize('style, scrolls', [(b's_btn_forwardspeed', False), (b's_btn_other', True)])
def test_a_speed_buttons_row_is_its_visible_button(fw, ui, style, scrolls):
    win = ui.widget('window', ui.wm, (0, 30, 375, 290), name='playset_page')
    view = ui.widget('scroll_view', win, (0, 0, 375, 100))
    view.byte(O['VIEW_VERTICAL'], 1)
    view.int(O['VIEW_CONTENT_H'], 61 * 6)
    for i in range(6):  # 61 px tap targets whose buttons show 24 px
        holder = ui.widget('view', view, (0, 61 * i, 375, 61))
        holder.props[b'style'] = style
        ui.widget('image', holder, (0, 0, 375, 61), click=True)
    ui.top = win
    key(fw, ui, KEY_NEXT)
    assert view.props[SEL] == 1 and (view.int(SCROLL_Y) > 0) == scrolls


@pytest.mark.parametrize('holder, tall', [('list_item', True), ('view', False)])
def test_a_button_over_the_top_of_its_list_row_is_selected_as_the_whole_row(fw, ui, holder, tall):
    win = ui.widget('window', ui.wm, (0, 30, 375, 290), name='allmusic_page')
    view = ui.widget('scroll_view', win, (0, 0, 375, VIEW_H))
    view.byte(O['VIEW_VERTICAL'], 1)
    view.int(O['VIEW_CONTENT_H'], 40 * 6)
    for i in range(6):  # the Library's rows: a 30 px button over the top of a 40 px row; or in a plain view
        ui.widget('button', ui.widget(holder, view, (0, 40 * i, 375, 40)), (0, 0, 375, 30), click=True, text=f'Row {i}')
    ui.top = win
    key(fw, ui, KEY_NEXT)
    canvas = Canvas(fw)
    fw.fn('navigation__paint_selection', None, C.c_void_p, C.c_void_p)(view.at, canvas.at)
    rows = sorted({y for _, _, y, _, _ in canvas.fills})  # the accent's bar, a fill a pixel row (gradient)
    assert rows == list(range(40, 40 + (40 if tall else 30)))  # a row's whole pitch; a button alone only its own


def test_a_tick_stops_the_lists_own_glide(fw, ui):
    page = Page(ui, titles(40))
    paint(fw, page)
    page.view.ptr(O['VIEW_ANIMATOR'], 0x7777)
    key(fw, ui, KEY_NEXT, ui.now + SLOW)
    assert [a[0] for _, a in fw.calls('widget_animator_destroy')] == [0x7777] and page.view.ptr(O['VIEW_ANIMATOR']) == 0
    assert [a[0] for _, a in fw.calls('widget_animator_pause')] == [0x7777]


def test_a_virtual_table_keeps_its_row_while_it_glides_past_then_settles_in_view(fw, ui):
    table = Table(fw, ui, 100)
    for _ in range(40):
        key(fw, ui, KEY_NEXT, ui.now + SLOW)
    table.bind(0)  # flung back to the top: row 40 is in no pool widget
    table.client.ptr(O['TABLE_ANIMATOR'], 0x5555)
    paint(fw, table)
    assert table.selected == 40
    table.client.ptr(O['TABLE_ANIMATOR'], 0)
    paint(fw, table)
    assert 0 <= table.selected < VIEW_H // ROW_H


def test_a_row_added_before_the_confirm_cancels_the_click(fw, ui):
    page = Page(ui, titles(30))
    clicks = centre(fw, ui, page, 3)
    ui.widget('list_item', page.view, (0, ROW_H * 30, 375, ROW_H), click=True)
    ui.fire(ui.due('confirm_center')[0])
    assert not clicks.seen


def test_a_row_rebuilt_in_place_before_the_confirm_is_not_clicked(fw, ui):
    page = Page(ui, titles(30))
    clicks = centre(fw, ui, page, 3)
    old = page.view.children[3]
    new = ui.widget('list_item', None, old.box, click=True)
    ui.widget('label', new, (10, 0, 300, 20), text=old.children[0].text)  # the same text, another widget
    struct.pack_into('<Q', new.block, W_PARENT, page.view.at)
    page.view.children[3] = new
    ui.fire(ui.due('confirm_center')[0])
    assert not clicks.seen


@pytest.mark.parametrize('rows, parent', [(40, 'list_view'), (40, 'table_view'), (5, 'list_view')])
def test_a_tick_leaves_the_lists_own_scroll_bar_asleep(fw, ui, rows, parent):
    """docs/internals.md#song-rows-cost: the theme draws the wheel's indicator itself (list_marks.c); stock's mobile
    bar, painted transparent, woken (scroll_bar_scroll_to at its own value) runs a 300 ms wait and a 500 ms opacity
    fade as a widget animator, repainting its column every loop pass: ~45 frames a tick on Songs."""
    win = ui.widget('window', ui.wm, (0, 30, 375, 290), name='allmusic_page')
    holder = ui.widget(parent, win, (0, 0, 375, VIEW_H))
    scroll_list(ui, holder, titles(rows))
    bar = ui.widget('scroll_bar_m', holder, (370, 0, 5, VIEW_H))
    bar.int(O['BAR_VALUE'], 123)
    ui.top = win
    key(fw, ui, KEY_NEXT)
    called = {name for name, _ in fw.calls()}
    assert called and not {n for n in called if n.startswith(('scroll_bar_', 'widget_animator_'))}


def test_setting_keys_name_each_option_in_config(fw):
    key_of = fw.fn('option_key', C.c_char_p, C.c_void_p, C.c_int)
    T = fw.fn('theme', C.c_void_p)()
    own = list(host.tools_theme.theme_keys(host.tools_theme.builtins()))
    assert [key_of(T, s) for s in range(1, 7)] == [b'HOME', b'BATTERY', b'LIGHT', b'KEEPLIT', b'CLICKER', b'REDUCEMOTION']
    assert [key_of(T, 7 + k).decode() for k in range(len(own))] == own
    assert key_of(T, 0) == b'OLED_ACCENT'  # the theme's own accent key


@in_fork  # Now Playing's new window answers NULL for its open_anim_hint: a bad read kills the process
def test_a_window_takes_its_themes_motion_as_it_loads(fw, ui):
    theme(fw)
    oled, = host.tools_theme.builtins()
    fields = dict(host.tools_theme.motion_fields(oled))
    def hint(kind):
        """The theme's window motion for kind, as theme.xml gives it; None: the window keeps its own."""
        value = fields[f'motion[MOTION_{kind}].hint']
        return json.loads(value).encode() if value else None
    sliding = ui.widget('window', ui.wm, name='allmusic_page')
    sliding.props[b'anim_hint'] = b'htranslate'  # a page that opens with stock's slide
    still = ui.widget('window', ui.wm, name='allmusic_page')
    playing = ui.widget('window', ui.wm, name='playing_page')
    load = fw.fn('navigation__motion_load', None, C.c_void_p, C.c_int)
    for win, np in ((sliding, 0), (still, 0), (playing, 1)):
        load(win.at, np)
    assert sliding.props[b'anim_hint'] == (hint('PAGE') or b'htranslate')
    assert b'anim_hint' not in still.props  # a page that does not slide is not made to
    assert playing.props.get(b'close_anim_hint') == hint('PLAYING') and b'anim_hint' not in playing.props  # it opens at once


def test_a_set_with_a_list_end_keeps_the_selected_row_above_it(fw, ui, st):
    names = host.tools_theme.GEOM_VALS
    table = fw.var('theme_engine__geom_vals', C.c_short * len(names))
    end = table[names.index('list_end')]
    assert end > 0, 'the geometry set has no list end'
    page = Page(ui, titles(60), height=end + 3 * ROW_H)
    paint(fw, page)
    bottoms = []
    for _ in range(30):
        key(fw, ui, KEY_NEXT, ui.now + SLOW)
        bottoms.append(page.screen_y(page.selected) + ROW_H)
    assert max(bottoms) <= end and max(bottoms) > end - 2 * ROW_H  # it runs to the end, not past it


@pytest.mark.parametrize('grower', ['caption', 'slider'])
@pytest.mark.parametrize('name, rows', [('display_page', 7), ('power_page', 9), ('playset_page', 12)])
def test_a_last_row_grown_in_place_scrolls_its_foot_up_to_the_list_end(fw, ui, grower, name, rows):
    """A settings page scrolled to its last row, which then grows in place: Display's Reduce Motion with its caption
    under it (settings_pages.c cap_step) or a grabbed inline slider (inline_slider.c heights). The view scrolls by whole
    rows until the grown row's foot is on or above OLED's list end, y 282, 38 px clear of the screen's edge. Kept only
    inside the view's own height, which runs on to the screen's foot, it stood in the glass's bottom corners (the
    device's Display page, 2026-11-18: Reduce Motion's caption down to y 303); and the content padded past the rows'
    foot as list_fade pads it, since stock's relayout of the grown list takes that padding off and AWTK's offset stops
    at the content's foot."""
    theme(fw)
    bar, end, pitch = 58, 282, 32  # OLED's status row, list end and row pitch on the screen
    win = ui.widget('window', ui.wm, (0, 0, 375, 320), name=name)  # full-bleed, as every page's window
    view = ui.widget('scroll_view', win, (0, bar, 375, 320 - bar))  # from the status row's foot
    view.byte(O['VIEW_VERTICAL'], 1)
    items = [ui.widget('list_item', view, (0, pitch * i, 375, pitch)) for i in range(rows)]
    view.int(O['VIEW_CONTENT_H'], pitch * rows)
    view.int(SCROLL_Y, max(0, pitch * rows - (end - bar)))  # the wheel's end: the last row's foot on the list end
    ui.top = win
    last = items[-1]

    def relayout(w, *_):
        """Stock's layout of the list: the rows stacked by their heights, the content their foot, unpadded."""
        y = 0
        for item in items:
            item.int(4, y)
            y += item.box[3]
        view.int(O['VIEW_CONTENT_H'], y)

    def set_offset(w, x, y, *_):
        """AWTK's scroll_view_set_offset, clamped to the content (seen on the emulator, 2026-11-18: Reduce Motion's
        grown foot stayed on the screen's foot while the content ended there)."""
        top = max(0, min(y, view.int(O['VIEW_CONTENT_H']) - view.box[3]))
        view.int(SCROLL_X, x)
        view.int(SCROLL_Y, top)
    fw.mock('widget_layout', relayout)
    fw.mock('scroll_view_set_offset', set_offset)
    if grower == 'caption':  # settings_pages.c's cap: view, item[8], text[8], cut[8][3], lines[8], open, closing
        cap = C.addressof(fw.var('settings_pages__cap', C.c_char))
        C.c_void_p.from_address(cap).value = view.at
        C.c_void_p.from_address(cap + 8).value = last.at
        C.c_ubyte.from_address(cap + 160).value = 1  # one line, measured
        C.c_void_p.from_address(cap + 168).value = last.at  # opening, started long ago: grown whole
        fw.fn('settings_pages__cap_step', C.c_int, C.c_void_p)(None)
    else:  # inline_slider.c's is: view, item[8], sw[8], kind[8], open; its run never started: grown whole
        sl = C.addressof(fw.var('inline_slider__is', C.c_char))
        C.c_void_p.from_address(sl).value = view.at
        C.c_void_p.from_address(sl + 8).value = last.at
        C.c_void_p.from_address(sl + 144).value = last.at
        fw.fn('inline_slider__heights', C.c_int)()
    assert last.box[3] > pitch  # it grew
    foot = bar + last.box[1] + last.box[3] - view.int(SCROLL_Y)
    assert end - pitch < foot <= end  # by whole rows, as far as it overran, and no further than the wheel's end
    assert view.int(SCROLL_Y) % pitch == 0 or foot == end
    assert view.props.get(b'_list_top') == view.int(SCROLL_Y) + 1  # what list_fade puts back after a relayout


class Settings:
    """A settings page scrolled to its end under OLED, as the wheel and the paints leave it, with captioned row
    `cap` (settings_pages.c's table, its caption one line, measured): the status row's client, the rows 32 px
    apart, the content padded by the box's rows past the list end (list_fade). widget_layout is stock's: the rows
    stacked by their heights, the content their foot, unpadded, the scroll clamped to it, then ipod_list_layout's
    own tail (row_paint.c list_relaid)."""
    bar, end, pitch = 58, 282, 32

    def __init__(self, fw, ui, rows, cap, name='playset_page'):
        self.fw, self.ui, self.cap = fw, ui, cap
        theme(fw)
        win = ui.widget('window', ui.wm, (0, self.bar, 375, 320 - self.bar), name=name)
        self.view = ui.widget('scroll_view', win, (0, 0, 375, 320 - self.bar))
        self.view.byte(O['VIEW_VERTICAL'], 1)
        self.items = [ui.widget('list_item', self.view, (0, self.pitch * i, 375, self.pitch))
                      for i in range(rows)]
        for i, item in enumerate(self.items):  # stock's row: a list_item holding a tappable button of labels
            button = ui.widget('button', item, (0, 0, 375, self.pitch), click=True)
            ui.widget('label', button, (10, 0, 300, 20), text=f'Row {i}')
        ui.top = win
        pad = 320 - self.end
        self.view.int(O['VIEW_CONTENT_H'], self.pitch * rows + pad)
        self.view.int(SCROLL_Y, self.pitch * rows + pad - (320 - self.bar))  # the wheel's end
        self.view.props.update({b'_list_pad': pad, b'_list_padded': self.pitch * rows + pad,
                                b'_list_top': self.view.int(SCROLL_Y) + 1})
        fw.mock('widget_layout', self.relayout)
        fw.mock('scroll_view_set_offset', self.set_offset)
        cap_at = C.addressof(fw.var('settings_pages__cap', C.c_char))  # view, item[8], text[8], cut[8][3], lines[8], open
        C.c_void_p.from_address(cap_at).value = self.view.at
        C.c_void_p.from_address(cap_at + 8).value = self.items[cap].at
        C.c_ubyte.from_address(cap_at + 160).value = 1
        self.cap_at = cap_at
        self.paint()
        while self.view.props.get(SEL, -1) < rows - 1:
            key(fw, ui, KEY_NEXT, ui.now + SLOW)

    def relayout(self, w, *_):
        y = 0
        for item in self.items:
            item.int(4, y)
            y += item.box[3]
        self.view.int(O['VIEW_CONTENT_H'], y)
        self.view.int(SCROLL_Y, max(0, min(self.view.int(SCROLL_Y), y - self.view.box[3])))
        self.fw.fn('navigation__list_relaid', None, C.c_void_p)(self.view.at)

    def set_offset(self, w, x, y, *_):
        self.view.int(SCROLL_X, x)
        self.view.int(SCROLL_Y, max(0, min(y, self.view.int(O['VIEW_CONTENT_H']) - self.view.box[3])))

    def paint(self):
        """The list's paint with its fades (list_fade pads the content and notes the scroll it saw)."""
        canvas = Canvas(self.fw)
        paint(self.fw, self, canvas.at)
        self.fw.fn('navigation__list_fade', None, C.c_void_p, C.c_void_p)(self.view.at, canvas.at)

    def foot(self, i):
        """Row i's foot on the screen."""
        return self.bar + self.items[i].box[1] + self.items[i].box[3] - self.view.int(SCROLL_Y)

    def select(self, i):
        """The wheel to row i, the paint, and the captions' growth and shrink run out."""
        while self.view.props[SEL] != i:
            self.ui.now += 1000
            key(self.fw, self.ui, KEY_NEXT if i > self.view.props[SEL] else KEY_PREV, self.ui.now)
            self.paint()
        self.paint()
        for _ in range(2):  # the change's timer, the second tick after its time is up
            self.ui.now += 1000
            self.fw.fn('settings_pages__cap_step', C.c_int, C.c_void_p)(None)
            self.paint()


ROWS = 9


def test_the_last_row_after_a_captioned_row_ends_on_the_list_end(fw, ui):
    """Settings > Battery's end (seen on the emulator, 2026-11-18: Battery History's bar 21 px below y 282): the wheel
    from In-Vehicle Mode, whose caption is open, to the last row. The reveal scrolls for the grown list, one row
    further than the list will end once the caption closes; the relayout that shrinks it clamps the scroll to stock's
    height, and the padding put back could not take the scroll the paint saw, which lay past the new end, so the
    last row stood on the screen's foot or below y 282 instead of on it."""
    s = Settings(fw, ui, ROWS, ROWS - 2)
    assert s.foot(ROWS - 1) == s.end
    s.select(ROWS - 2)
    s.select(ROWS - 1)
    assert s.foot(ROWS - 1) == s.end


@pytest.mark.parametrize('cap', range(ROWS))
def test_a_captioned_row_grows_down_from_its_own_top(fw, ui, st, cap):
    """Each captioned row of a page scrolled to its end, the wheel on it from the row above and from below: its
    bar, grown, starts at its own top and clears the row above's foot, whatever scrolling the growth took (settings
    pages: Display's Enlarged Album Art over Cover Flow, on the device 2026-11-18)."""
    s = Settings(fw, ui, ROWS, cap)
    for step in (cap, min(cap + 1, ROWS - 1), cap):
        s.select(step)
        if step != cap:
            continue
        above = s.items[cap - 1].box[1] + s.items[cap - 1].box[3] if cap else 0
        top = st('sel_y').value - st('sel_h').value // 2 + s.view.int(SCROLL_Y)  # the bar's, in the view's rows
        assert s.items[cap].box[3] > s.pitch
        assert top == s.items[cap].box[1] and top >= above, (cap, top, above)
        assert st('sel_h').value == s.items[cap].box[3]


# Painted helpers: the panel, the shadow, the boxes.


def wide(text):
    return (C.c_uint * (len(text) + 1))(*map(ord, text), 0)


def test_without_vgcanvas_the_letters_box_is_rounded_a_row_at_a_time(fw, ui):
    """fill_box's own rows when AWTK declines: the same box, its corners antialiased (corner_cover.c)."""
    page, canvas = spun(fw, ui, titles(200))
    canvas.declines = 1
    letter(fw, page, canvas)
    (_, box, _), = canvas.boxes
    (_, rounded, color, _), = canvas.rounds  # asked for first, and declined
    x, y, w, h = box
    assert rounded == box and all(x <= fx and fx + fw_ <= x + w and y <= fy < y + h for _, fx, fy, fw_, _ in canvas.fills)
    assert (color, x, y + h // 2, w, 1) in canvas.fills  # a middle row whole, in the box's colour
    top = [(c, fx, fw_) for c, fx, fy, fw_, _ in canvas.fills if fy == y]
    assert min(fx for _, fx, _ in top) > x  # the corner's outermost pixel clear
    assert any(0 < c >> 24 < color >> 24 for c, _, _ in top)  # part pixels along the flat of the arc


@pytest.mark.parametrize('declines, box, rounded', [(0, (10, 10, 80, 40), True), (1, (10, 10, 80, 40), False), (0, (10, 10, 20, 20), False)])
def test_a_frame_is_two_rings_rounded_where_it_can_be(fw, declines, box, rounded):
    canvas = Canvas(fw)
    canvas.declines = declines
    fw.fn('navigation__frame_box', None, C.c_void_p, C.POINTER(Rect), C.c_uint, C.c_uint)(canvas.at, C.byref(Rect(*box)), 0xffffffff, 12)
    x, y, w, h = box
    inner = (x + 1, y + 1, w - 2, h - 2)
    if rounded:
        assert [(k, r, radius) for k, r, _, radius in canvas.rounds] == [('stroke', box, 12), ('stroke', inner, 11)] and not canvas.strokes
    else:
        assert canvas.strokes == [(0xffffffff, *box), (0xffffffff, *inner)]


@pytest.mark.parametrize('value', [0, 7, 10, 42, 100, 999])
def test_put_num_writes_the_digits(fw, value):
    out = (C.c_uint * 4)()
    n = fw.fn('put_num', C.c_uint, C.c_void_p, C.c_uint)(out, value)
    assert ''.join(map(chr, out[:n])) == str(value)


def test_a_clip_that_misses_the_list_paints_no_chevrons(fw, ui):
    page, canvas = Page(ui, titles(6), name='playlist_page'), Canvas(fw, clip=(0, 300, 375, 20))
    assert chevrons(fw, page, canvas) == []


def test_a_selected_row_without_a_scrolling_title_starts_nothing(fw, ui, st):
    page = Page(ui, titles(10))
    st('sel_w', C.c_void_p).value = page.view.at
    st('sel_row').value = st('marq_id').value = 3
    st('marq_row', C.c_void_p).value = page.rows[3].at
    fw.fn('navigation__marq_moved', C.c_int, C.c_void_p)(None)
    assert not fw.calls('hscroll_label_start')


# Centre's single and double press on a page that tells them apart.
SINGLE = C.CFUNCTYPE(C.c_int, C.c_void_p)


class Press:
    """center_press on a page's own timer and press time; the single press's runs, by time."""
    def __init__(self, fw, ui):
        self.ui, self.timer, self.at, self.singles = ui, C.c_uint(0), C.c_uint(0), []
        self.single = SINGLE(lambda info: self.singles.append(ui.now) or 0)
        self.press = fw.fn('center_press', C.c_int, C.POINTER(C.c_uint), C.POINTER(C.c_uint), SINGLE, C.c_int)

    def __call__(self, at, scrubbing=0):
        self.ui.now = at
        return self.press(C.byref(self.timer), C.byref(self.at), self.single, scrubbing)


DOUBLE = O['DOUBLE_CLICK_MS']


def test_a_first_press_waits_for_a_second(fw, ui):
    press = Press(fw, ui)
    assert press(5000) == 1 and press.at.value == 5000
    fn, _, ms = ui.timers[press.timer.value]
    assert ms == DOUBLE and fn == C.cast(press.single, C.c_void_p).value and not press.singles


def test_a_second_press_in_time_is_the_double(fw, ui):
    press = Press(fw, ui)
    press(5000)
    timer = press.timer.value
    assert press(5000 + DOUBLE - 1) == 2 and press.timer.value == 0 and timer not in ui.timers and not press.singles


def test_a_press_after_an_overdue_first_runs_the_single_and_is_stocks(fw, ui):
    press = Press(fw, ui)
    press(5000)
    assert press(5000 + DOUBLE) == 0 and press.singles == [5000 + DOUBLE] and press.timer.value == 0


def test_a_scrubs_presses_are_doubles_and_the_next_waits_for_nothing(fw, ui):
    press = Press(fw, ui)
    assert press(5000, scrubbing=1) == 2 and press.at.value == 5000 and press.timer.value == 0
    assert press(5000 + DOUBLE - 1) == 1 and press.timer.value == 0  # no single: the screen stays on
    assert press(5000 + 2 * DOUBLE) == 1 and press.timer.value  # later: a first press again


# Cover Flow's page steps like Home's carousel; any other page's slide_menu is left to stock.
def test_coverflow_s_page_glides_a_cover_a_tick(fw, ui):
    covers = Carousel(fw, ui, name='coverflow_page')
    key(fw, ui, KEY_NEXT)
    assert covers.started == [(-covers.stride, covers.duration)]


class CfPage(C.Structure):
    """coverflow.c's cf as far as the pool reads it: cf.slide, and cf.count at the end (coverflow.h)."""
    _fields_ = [*((k, C.c_void_p) for k in 'page body covers slide name artist title albums tracks stock'.split()),
                *((k, C.c_int) for k in 'sort sort_read year_old artists on_sort resort'.split()),
                ('jobs', C.c_void_p), ('thread', C.c_ulong), ('timer', C.c_uint), ('title_timer', C.c_uint),
                *((k, C.c_uint) for k in 'art_timer art_at'.split()), *((k, C.c_int) for k in 'art_told art_ring want'.split()),
                ('titled', C.c_int), ('offline', C.c_int), ('saved_album', C.c_uint), ('albums_gen', C.c_uint),
                *((k, C.c_int) for k in 'screen album running done total cancel scoped all_sort'.split()),
                *((k, C.c_void_p) for k in 'all_albums all_stock hearts'.split()), ('count', C.c_int)]


def pooled(fw, ui, albums, index):
    """Cover Flow's covers with depth: eight children (CF_POOL) standing for albums albums, at index."""
    covers = Carousel(fw, ui, n=8, index=index, name='coverflow_page')
    c = fw.var('cf', CfPage)
    c.slide, c.count = covers.menu.at, albums
    return covers


@pytest.mark.parametrize('index, code, lands', [(4000, KEY_NEXT, 4001), (5999, KEY_NEXT, 0), (0, KEY_PREV, 5999)])
def test_a_pooled_glide_ends_on_the_album_not_the_child(fw, ui, index, code, lands):
    """The glide's end commits SLIDE_INDEX less the offset round the albums, where stock's would wrap it by the
    children; the focus goes to the child standing for it."""
    covers = pooled(fw, ui, 6000, index)
    key(fw, ui, code)
    (evt, fn, ctx), = covers.handlers
    covers.live(covers.goal)  # arrived
    assert C.CFUNCTYPE(C.c_int, C.c_void_p, C.c_void_p)(fn)(ctx, 0x40) == 7
    assert fw.calls('slide_menu_set_value')[-1][1][:2] == (covers.menu.at, lands)
    assert not fw.calls('slide_menu_on_scroll_done')
    assert covers.menu.int(SLIDE_OFFSET) == 0 and covers.menu.ptr(O['SLIDE_ANIMATOR']) == 0


@pytest.mark.parametrize('index, code, lands', [(4000, KEY_NEXT, 4001), (5999, KEY_NEXT, 0), (0, KEY_PREV, 5999)])
def test_a_pooled_tick_with_no_animator_lands_round_the_albums(fw, ui, index, code, lands):
    covers = pooled(fw, ui, 6000, index)
    covers.fw.mock('widget_animator_scroll_create', lambda *_: 0)
    key(fw, ui, code)
    assert fw.calls('slide_menu_set_value')[-1][1][:2] == (covers.menu.at, lands)


def test_another_page_s_slide_menu_is_not_a_carousel(fw, ui):
    other = Carousel(fw, ui, name='games_page')
    key(fw, ui, KEY_NEXT)
    assert not other.started


def test_a_glide_that_cannot_hear_its_end_lands_at_once(fw, ui):
    home = Carousel(fw, ui, binds=False)
    key(fw, ui, KEY_NEXT)
    assert len(home.destroyed) == 1 and not home.started and home.menu.ptr(O['SLIDE_ANIMATOR']) == 0
    assert fw.calls('slide_menu_set_value')[-1][1][:2] == (home.menu.at, 3)


# OLED's Shuffle row (list.shuffle.row): Songs' action bar takes the wheel's selection above the first song.
@pytest.fixture(scope='module')
def st_at(library):
    return host.offsets(library.parent/'navigation.o', 'scratch_t')


def action_bar(ui, page, sort=True):
    view = ui.widget('view', page.win, (0, 0, 375, 32), name='view_navbar_allplay')
    for name in ('label', 'play') + (('sort',) if sort else ()):
        ui.widget('image' if name != 'label' else 'label', view, (0, 0, 40, 32), name=name)
    return view


@pytest.mark.parametrize('sort', [True, False])
def test_the_wheel_steps_from_the_first_song_onto_shuffle_then_sort_and_back(fw, ui, st_at, sort):
    theme(fw)
    base = C.addressof(fw.var('st', C.c_char))
    field = lambda name: C.c_void_p.from_address(base + st_at[name])
    page = Page(ui, titles(20))
    view = action_bar(ui, page, sort)
    field('action_view').value = view.at
    paint(fw, page)
    assert page.selected == 0
    stops = []
    for code in (KEY_PREV, KEY_PREV, KEY_PREV, KEY_NEXT, KEY_NEXT, KEY_NEXT):
        key(fw, ui, code, ui.now + SLOW)
        on = field('shuffle_on').value == view.at
        stops.append((C.c_int.from_address(base + st_at['shuffle_stop']).value if on else 0, page.selected))
    top = 2 if sort else 1  # nothing above the last stop
    assert stops == [(1, 0), (top, 0), (top, 0), (top - 1, 0) if sort else (0, 0), (0, 0) if sort else (0, 1),
                     (0, 1) if sort else (0, 2)]


def test_a_page_over_the_shuffle_row_leaves_it_selected_for_return(fw, ui, st_at):
    """Rule 15, Return lands on the row that was left: Centre on Shuffle opens Now Playing, whose window takes the
    status row (bar_title_sync's title_gone) and whose keys are its own; back on Songs the bar is on Shuffle again,
    and its destroy is what lets it go."""
    theme(fw)
    base = C.addressof(fw.var('st', C.c_char))
    field = lambda name: C.c_void_p.from_address(base + st_at[name])
    page = Page(ui, titles(20))
    view = action_bar(ui, page)
    field('action_view').value = view.at
    paint(fw, page)
    key(fw, ui, KEY_PREV, ui.now + SLOW)
    assert field('shuffle_on').value == view.at
    fw.fn('title_gone', C.c_int, C.c_void_p, C.c_void_p)(None, None)  # another window on top
    ui.top = Page(ui, titles(3), name='playing_page').win
    key(fw, ui, KEY_NEXT, ui.now + SLOW)  # a key on that page
    assert field('shuffle_on').value == view.at
    ui.top = page.win
    field('action_view').value = view.at  # bar_title_sync finds it again on Songs
    paint(fw, page)
    assert field('shuffle_on').value == view.at and C.c_int.from_address(base + st_at['shuffle_stop']).value == 1
    (fn, ctx), = [a[2:4] for _, a in fw.calls('widget_on') if a[0] == view.at and a[1] == O['EVT_DESTROY']]
    C.CFUNCTYPE(C.c_int, C.c_void_p, C.c_void_p)(fn)(ctx, None)
    assert field('shuffle_on').value is None


# An album's rows (song_rows.c song_row): the track number and the length are each small_text in the row's box, so
# they share one vertical place, which SONG_SMALL_DY sets so their baseline is the title's.
SONG_SMALL_DY = NAV['SONG_SMALL_DY']


def test_a_track_number_stands_where_its_length_does_on_the_titles_baseline(fw):
    canvas = Canvas(fw)
    small_text = fw.fn('song_rows__small_text', None, C.c_void_p, C.c_void_p, C.c_int, C.c_int, C.c_int, C.c_void_p)
    row = (C.c_int * 4)(0, 154, 375, 32)  # an OLED row: 32 px, its title centred in it
    for text, x, w in (('12', 0, 40), ('5:10', 300, 40)):
        small_text(canvas.at, (C.c_uint * (len(text) + 1))(*map(ord, text), 0), x, w, 1, row)
    (number, number_box, _), (length, length_box, _) = canvas.boxes
    assert (number, length) == ('12', '5:10')
    assert number_box[0] + number_box[2] - 1 == 40 and length_box[0] + length_box[2] - 1 == 340  # each right-aligned
    assert number_box[1::2] == length_box[1::2] == (154 + SONG_SMALL_DY, 32)  # one place: the row's, its baseline down
    assert SONG_SMALL_DY == 2  # 15 px Inter centred in the row, 2 px down, sits on the 20 px title's baseline


# A label at rest on its ellipsis (ringnav_ellipsis_text, for stock's widget_draw_text_in_rect): the cut before
# stock's "..." never ends on a space.
def stock_cut(text, width, widths):
    """What stock's canvas_draw_text_bidi_in_rect keeps of text before "..." in a box width px wide: each character
    measured alone, while dots + kept + the next is under the width."""
    dots, ink = 3 * widths('.'), 0
    for i, c in enumerate(text):
        if width <= dots + ink + widths(c):
            return text[:i]
        ink += widths(c)
    return text


def ellipsis(fw, text, width, ellipses=1):
    widths = lambda c: 4.0 if c in ' .' else 9.0  # a space and a dot narrow, the rest 9 px
    fw.mock('canvas_measure_text', lambda c, s, n, *_: sum(widths(chr(v)) for v in (C.c_uint * n).from_address(s)))
    drawn = []
    fw.mock('widget_draw_text_in_rect', lambda w, c, s, n, r, e, *_: drawn.append(((C.c_int * 4).from_address(r)[2], e)))
    s = (C.c_uint * len(text))(*map(ord, text))
    box = (C.c_int * 4)(10, 0, width, 32)
    fw.fn('ringnav_ellipsis_text', C.c_int, C.c_void_p, C.c_void_p, C.c_void_p, C.c_uint, C.c_void_p, C.c_int)(
        None, None, s, len(text), box, ellipses)
    (w, e), = drawn
    assert e == ellipses and box[2] == width  # the label's own box untouched
    return stock_cut(text, w, widths), w


@pytest.mark.parametrize('width, kept', [
    (90, 'Sex Type'),    # stock's cut, "Sex Type ", fell after a word: it ends on "Type"
    (92, 'Sex Type'),    # ... up to the px where "T" would fit
    (93, 'Sex Type T'),  # a cut inside a word stays as stock makes it
    (44, 'Sex'),         # "Sex "
    (40, 'Sex'),         # "Sex", no space to trim
    (20, ''),            # nothing but the dots fits: nothing to trim
])
def test_an_ellipsis_never_follows_a_space(fw, width, kept):
    text = 'Sex Type Thing (2017 Remaster)'
    got, w = ellipsis(fw, text, width)
    assert got == kept and not got.endswith(' ')
    if got == stock_cut(text, width, lambda c: 4.0 if c in ' .' else 9.0):
        assert w == width  # no space at the cut: the box goes on as it was


@pytest.mark.parametrize('text, ellipses', [('Short', 1), ('Sex Type Thing (2017 Remaster)', 0)])
def test_a_text_that_fits_or_has_no_ellipsis_keeps_its_box(fw, text, ellipses):
    assert ellipsis(fw, text, 120, ellipses)[1] == 120


def test_a_long_artist_is_cut_as_before_with_a_handful_of_measurements(fw):
    """docs/internals.md#song-rows-cost: a Songs row's artist cut short to its box kept losing a character and measuring
    again, once per character (and its font set each time), on every repaint of every row as the wheel scrolls. The cut
    is the same, the ellipsis in the last place that fits, found by halving: a few measurements, not one a character."""
    canvas = Canvas(fw)
    measured = []
    fw.mock('canvas_measure_text', lambda c, s, n, *_: measured.append(n) or 8.0 * n)  # every character 8 px
    small_text = fw.fn('song_rows__small_text', None, C.c_void_p, C.c_void_p, C.c_int, C.c_int, C.c_int, C.c_void_p)
    row = (C.c_int * 4)(0, 154, 375, 32)
    text = 'Avenged Sevenfold and the Many Guests of the Long Night'
    small_text(canvas.at, (C.c_uint * (len(text) + 1))(*map(ord, text), 0), 0, 80, 0, row)
    (drawn, box, _), = canvas.boxes
    n = len(text)  # the old loop's answer: from the whole text down, the last character the ellipsis, until it fits
    while n > 1 and 8 * n > 80:
        n -= 1
    assert drawn == text[:n - 1] + '\u2026' and box[2] == 8 * n + 1
    assert len(measured) <= 8, measured  # 55 characters: one a character was 46 measurements
