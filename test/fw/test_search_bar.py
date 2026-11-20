"""search_bar.c and search_strip.c's search_strip_scoped (docs/oled-design.md#search-bar): a music list page's search
bar one wheel step above its first row, out of sight until a turn up from that row brings it in, lit; Centre opens the
letter strip over the page's own rows, which it filters as the text is typed. Return shuts the strip and leaves the
text lit with the query and the results kept; the go key goes to the first result, or with none to the text."""
import ctypes as C
import struct

from conftest import O
from test_nav_library_pages import Config, Ui
from test_nav_painters import scratch, st  # noqa: F401  (fixtures: the payload's state)
from test_search_strip import attached

import pytest

KEY_CENTER, KEY_RETURN, KEY_NEXT, KEY_PREV = O['KEY_CENTER'], O['KEY_RETURN'], O['KEY_NEXT'], O['KEY_PREV']
ON_STRIP, ON_LIST, ON_FIELD = 0, 1, 3
BAND = 40  # search_bar.c SB_BAND


@pytest.fixture
def ui(fw):
    return Ui(fw)


def page(fw, ui, name='playlistsongs_page', words=('Rockstar', 'Road Trippin\'', 'Human', 'Rock and Roll')):
    """A page of rows as a payload list builds them: its window, a list_view, its scroll view, each row a list_item
    with a label of its words; the window on top."""
    Config(fw)
    fw.mock('widget_move', lambda w, x, y, *_: struct.pack_into('<2i', ui.blocks[w], 0, C.c_int(x).value,
                                                                C.c_int(y).value))
    win = ui.new('window_create', 0, 0, 0, 375, 320)
    ui.name[win] = name
    lst = ui.new('list_view_create', win, 0, 58, 375, 262)
    view = ui.new('scroll_view_create', lst, 0, 0, 375, 262)
    for i, w in enumerate(words):
        row = ui.new('list_item_create', view, 0, 32 * i, 375, 32)
        ui.text[ui.new('label_create', row, 24, 0, 300, 32)] = w
    return win, lst, view


def y_of(ui, w):
    return struct.unpack_from('<2i', ui.blocks[w], 0)[1]


def test_a_turn_up_from_the_first_row_brings_the_bar_in_and_down_puts_the_list_back(fw, ui, st):
    win, lst, view = page(fw, ui)
    up = fw.fn('search_bar_up', C.c_int, C.c_void_p, C.c_void_p)
    key = fw.fn('search_bar_key', C.c_int, C.c_void_p, C.c_uint)
    assert key(win, KEY_NEXT) == 0  # out of sight: the list's keys are its own
    assert up(win, view) == 1
    assert y_of(ui, lst) == 58 + BAND and st('touch_mode').value == 1  # the rows a band down, their selection hidden
    assert key(win, KEY_PREV) == 1 and y_of(ui, lst) == 58 + BAND  # nothing above it
    assert key(win, KEY_NEXT) == 1 and y_of(ui, lst) == 58 and st('touch_mode').value == 0
    assert key(win, KEY_NEXT) == 0  # the first row's again


def test_no_bar_on_a_page_that_is_no_music_list(fw, ui, st):
    win, lst, view = page(fw, ui, name='wifi_page')
    assert fw.fn('search_bar_up', C.c_int, C.c_void_p, C.c_void_p)(win, view) == 0 and y_of(ui, lst) == 58


def scoped(fw, ui, title=b'Road Trip'):
    win, lst, view = page(fw, ui)
    assert fw.fn('search_strip_scoped', C.c_int, C.c_void_p, C.c_void_p, C.c_char_p)(win, view, title) == 1
    s = attached(fw, ui)
    return s, win, view


def words(fw, s):
    match = C.cast(s.ss.match, C.POINTER(C.c_void_p))
    out = []
    for i in range(s.ss.nmatch):
        buf = C.create_string_buffer(64)
        fw.fn('search_strip__sc_words', C.c_char_p, C.c_void_p, C.c_char_p, C.c_uint)(match[i], buf, 64)
        out.append(buf.value.decode())
    return out


def test_centre_s_search_filters_the_page_s_own_rows_as_it_is_typed(fw, ui, st):
    """A playlist's songs (mock D): the strip over its rows, its words "Search Road Trip", the rows holding the text
    in the page's order, nothing else (no tabs, no artists or albums)."""
    s, win, view = scoped(fw, ui)
    assert C.string_at(s.ss.label) == b'Search Road Trip'
    s.type('ro')
    assert words(fw, s) == ['Rockstar', "Road Trippin'", 'Rock and Roll']
    s.type('c')
    assert words(fw, s) == ['Rockstar', 'Rock and Roll']


def test_return_shuts_the_strip_and_keeps_the_query_and_results_lit_on_the_bar(fw, ui, st):
    """The user's, 2026-11-19: Return closes the strip and puts the selection on the bar, the query and the filtered
    list kept; a turn down then goes into the results, and Return from the bar leaves the search."""
    s, win, view = scoped(fw, ui)
    s.type('ro')
    s.key(KEY_RETURN)
    assert s.ss.focus == ON_FIELD and s.ss.text == b'ro' and s.ss.nmatch == 3
    s.key(KEY_NEXT)
    assert s.ss.focus == ON_LIST and s.ss.sel == 0


@pytest.mark.parametrize('typed, focus', [('ro', ON_LIST), ('zz', ON_FIELD)])
def test_the_go_key_lights_the_first_result_or_with_none_the_bar(fw, ui, st, typed, focus):
    """The go key (the accent's disc) submits: the strip closes and the first result is lit; with no result the bar
    is, the empty state's No Matches under it."""
    s, win, view = scoped(fw, ui)
    s.type(typed)
    s.go(s.stops() - 1)
    s.key(KEY_CENTER)
    assert s.ss.focus == focus and s.ss.sel == 0


def test_centre_on_a_result_closes_the_search_and_opens_its_row_on_the_page(fw, ui, st):
    """A result opens its own row: the strip's page goes, and once the page is on top again its selection is on the
    row and Centre goes in, so the row does what it does there."""
    closed, keys = [], []
    fw.mock('window_close', lambda w, *_: closed.append(w) or 0)
    fw.mock('stock_input_trampoline', lambda wm, e, *_: keys.append(C.c_int.from_address(e).value) or 0)
    s, win, view = scoped(fw, ui)
    s.type('roc')
    s.go(s.stops() - 1)
    s.key(KEY_CENTER)  # to the results
    s.key(KEY_NEXT)  # Rock and Roll, the page's fourth row
    strip = s.ss.page
    s.key(KEY_CENTER)
    assert closed == [strip]
    ui.destroy(strip)
    ui.fire('search_strip__ss_scope_due')
    assert ui.props.get((view, '_ringnav_index')) == 3
