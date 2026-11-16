"""library_edit.c: the Library's row order, saved whole and read against the default order (a row a later firmware adds
goes in at its default place), and OLED's Edit Library: Centre held enters it, Centre lifts the selected row, the
wheel carries it, Centre drops it and saves, Return puts a carried row back, a finger held on a row drags it."""
import ctypes as C
import re
import struct

import pytest

from conftest import O, ROOT, claim
from test_nav_hold import use_set
from test_nav_library_pages import LIBRARY_STOCK, Config, Disk, page_rows, scratch, st, stock_page, ui  # noqa: F401  (fixtures)

SOURCE = (ROOT / 'patch' / 'library_edit.c').read_text()
LR = {k: int(v) for k, v in re.findall(r'^#define (LR_\w+) (\d+)\b', SOURCE, re.M)}
DEFAULT = re.findall(r'lr_default\[\] = "(\w+)"', SOURCE)[0]
FILE = '/mnt/data/ringnav-library-order'
KEY_CENTER, KEY_RETURN, KEY_NEXT, KEY_PREV = O['KEY_CENTER'], O['KEY_RETURN'], O['KEY_NEXT'], O['KEY_PREV']
EVT_MOVE, EVT_UP, EVT_DOWN = O['EVT_POINTER_MOVE_BEFORE'], O['EVT_POINTER_UP_BEFORE'], O['EVT_POINTER_DOWN']
STOP, PITCH = 11, 32
SEL = '_ringnav_index'
# the Library's rows under OLED (Search leads: its set has no Library icons), as the page shows them
SHOWN = ['All Songs', 'Playlist', 'Artist', 'Album', 'Search', 'Genre', 'Years', 'Ratings', 'Recently Added', 'Recent',
         'Most Played', 'Games', 'Update Local Music']


def resolve(fw, saved):
    out = C.create_string_buffer(LR['LR_MAX'])
    fw.fn('lr_resolve', C.c_int, C.c_char_p, C.c_void_p)(saved.encode(), out)
    return out.value.decode()


def merge(fw, full, shown):
    out = C.create_string_buffer(full.encode(), LR['LR_MAX'])
    fw.fn('lr_merge', None, C.c_void_p, C.c_char_p)(out, shown.encode())
    return out.value.decode()


def test_nothing_saved_is_the_default_order(fw):
    assert resolve(fw, '') == DEFAULT


def test_a_saved_order_is_kept_and_letters_it_does_not_know_or_repeats_are_dropped(fw):
    mine = DEFAULT[::-1]
    assert resolve(fw, mine) == mine
    assert resolve(fw, 'Z' + mine[:3] + mine[0] + '!' + mine[3:]) == mine


def test_a_row_the_saved_order_does_not_name_goes_in_after_the_row_before_it_by_default(fw):
    """A later firmware's new row: the user's order stays, the new row lands after its default predecessor."""
    mine = DEFAULT.replace('e', '')
    mine = mine.replace('g', '') + 'g'  # Genres moved to the end, Era (new) not yet known
    assert resolve(fw, mine) == mine + 'e'
    # the first row by default has nothing before it: it goes first
    assert resolve(fw, DEFAULT[1:][::-1]) == DEFAULT[0] + DEFAULT[1:][::-1]
    # two new rows in a row keep their default order
    assert resolve(fw, DEFAULT.replace('e', '').replace('r', '').replace('g', '') + 'g')[-3:] == 'ger'


def test_hidden_rows_keep_their_places_when_the_shown_ones_move(fw):
    full = 'abHcdK'
    assert merge(fw, full, 'dcba') == 'dcHbaK'
    assert merge(fw, full, 'abcd') == full


@pytest.fixture
def keys():
    """Stock's key latches the filter reads and writes, clear, and put back after."""
    bytes_ = [O['KEY_LOCKOUT'], O['RETURN_RELEASE_LATCH']]
    for a in bytes_:
        claim(a)
    saved = [C.string_at(a, 1) for a in bytes_]
    for a in bytes_:
        C.memset(a, 0, 1)
    yield
    for a, b in zip(bytes_, saved):
        C.memmove(a, b, 1)


class Library:
    """The Library page under OLED on top, laid out as AWTK's list does (each shown row PITCH under the last)."""
    def __init__(self, fw, ui, st, files=None):
        use_set(st, 'oled')
        fw.var('g_backlight_status', C.c_ubyte).value = 1
        self.fw, self.ui = fw, ui
        self.disk = Disk(fw, files)
        Config(fw)
        fw.mock('stock_localmusic_trampoline', lambda *_: 0)
        fw.mock('widget_layout', lambda w, *_: self.layout(w) or 0)
        fw.mock('widget_to_local', self.to_local)
        fw.mock('scroll_view_set_offset', lambda w, x, y, *_: struct.pack_into('<i', ui.blocks[w], O['SCROLL_Y'], y) or 0)
        self.aborts = []
        fw.mock('widget_dispatch_event_to_target_recursive', lambda w, e, *_: self.aborts.append(w) or 0)
        self.build()

    def build(self):
        ui = self.ui
        self.win, self.view = stock_page(ui, 'scroll_view_localmusic', *[(c, None, str(i)) for i, c in enumerate(LIBRARY_STOCK)])
        ui.name[self.win] = 'localmusic_page'
        struct.pack_into('<4i', ui.blocks[self.view], 0, 0, 58, 375, 262)
        self.fw.fn('ringnav_localmusic', C.c_int, C.c_void_p, C.c_void_p)(self.win, None)
        self.layout(self.view)

    def layout(self, view):
        y = 0
        for item in self.ui.kids[view]:
            struct.pack_into('<2i', self.ui.blocks[item], 4, y, 375)
            struct.pack_into('<i', self.ui.blocks[item], 12, PITCH)
            if item not in self.ui.hidden:
                y += PITCH
        struct.pack_into('<i', self.ui.blocks[view], O['VIEW_CONTENT_H'], y)

    def to_local(self, w, xy, *_):
        x, y = struct.unpack_from('<2i', C.string_at(xy, 8))
        scroll = struct.unpack_from('<i', self.ui.blocks[w], O['SCROLL_Y'])[0]  # AWTK counts the view's scroll
        struct.pack_into('<2i', (C.c_char * 8).from_address(xy), 0, x, y - 58 + scroll)
        return 0

    @property
    def shown(self):
        return [c for c, shown in page_rows(self.ui, self.view) if shown]

    def y(self, caption):
        item = next(i for i in self.ui.kids[self.view] if page_rows(self.ui, self.view)[self.ui.kids[self.view].index(i)][0] == caption)
        return struct.unpack_from('<i', self.ui.blocks[item], 4)[0]

    @property
    def editing(self):
        return self.fw.fn('lr_editing', C.c_int, C.c_void_p)(self.win)

    def key(self, code, down=False):
        if down:
            return self.fw.fn('lr_edit_keydown', None, C.c_uint)(code)
        return self.fw.fn('lr_edit_key', C.c_int, C.c_void_p, C.c_uint, C.c_int)(self.win, code, 0)

    def hold_centre(self):
        self.key(KEY_CENTER, down=True)
        (t, ms), = self.ui.pending('lr_hold_due')
        assert ms == LR['LR_HOLD_MS']
        self.ui.fire('lr_hold_due')
        assert self.key(KEY_CENTER)  # the hold's release is taken

    def select(self, i):
        self.ui.props[(self.view, SEL)] = i

    def saved(self):
        return self.disk.files.get(FILE, b'').rstrip(b'\0').decode()


def test_the_page_follows_a_saved_order(fw, ui, st):
    lib = Library(fw, ui, st)
    assert lib.shown == SHOWN
    mine = DEFAULT.replace('s', '') + 's'  # Songs last
    lib2 = Library(fw, ui, st, {FILE: mine.encode().ljust(LR['LR_MAX'], b'\0')})
    assert lib2.shown == SHOWN[1:] + ['All Songs']


def test_a_short_or_missing_file_is_the_default_order(fw, ui, st):
    lib = Library(fw, ui, st, {FILE: b'hS'})
    assert lib.shown == SHOWN


def test_centre_held_enters_edit_library_and_held_again_saves_and_leaves(fw, ui, st, keys):
    lib = Library(fw, ui, st)
    lib.key(KEY_CENTER, down=True)
    assert lib.key(KEY_CENTER) == 0 and not ui.pending('lr_hold_due')  # a short press is the row's click
    assert not lib.editing
    lib.hold_centre()
    assert lib.editing
    lib.hold_centre()
    assert not lib.editing and lib.saved() == DEFAULT


def test_centre_lifts_the_wheel_carries_and_centre_drops_and_saves(fw, ui, st, keys):
    lib = Library(fw, ui, st)
    lib.hold_centre()
    lib.select(1)  # Playlist
    assert lib.key(KEY_CENTER)
    assert lib.key(KEY_NEXT) and lib.key(KEY_NEXT)
    assert lib.shown[:4] == ['All Songs', 'Artist', 'Album', 'Playlist'] and ui.props[(lib.view, SEL)] == 3
    assert lib.saved() == ''  # not until it is dropped
    assert lib.key(KEY_CENTER)
    assert lib.saved().startswith('hsalp')  # the hidden Shuffle keeps its place
    assert lib.key(KEY_NEXT) == 0  # nothing carried: the wheel is the list's


def test_the_carried_row_stops_at_the_ends(fw, ui, st, keys):
    lib = Library(fw, ui, st)
    lib.hold_centre()
    lib.select(0)
    lib.key(KEY_CENTER)
    assert lib.key(KEY_PREV)  # taken, but nowhere to go
    assert lib.shown == SHOWN


def test_return_while_carrying_puts_the_row_back_and_stays_in_edit_library(fw, ui, st, keys):
    lib = Library(fw, ui, st)
    lib.hold_centre()
    lib.select(4)  # Search
    lib.key(KEY_CENTER)
    for _ in range(5):
        lib.key(KEY_PREV)
    assert lib.shown[0] == 'Search'
    assert lib.key(KEY_RETURN)
    assert lib.shown == SHOWN and lib.editing and ui.props[(lib.view, SEL)] == 4
    assert lib.saved() == ''
    assert lib.key(KEY_RETURN)  # not carrying: saves and leaves
    assert not lib.editing and lib.saved() == DEFAULT


def test_the_order_survives_a_reboot_and_hidden_rows_keep_their_places(fw, ui, st, keys):
    lib = Library(fw, ui, st)
    lib.hold_centre()
    lib.select(7)  # Ratings, just before the hidden My Fav
    lib.key(KEY_CENTER)
    lib.key(KEY_NEXT)
    lib.key(KEY_CENTER)
    saved = lib.saved()
    assert saved.index('f') == DEFAULT.index('f') and saved.index('q') == DEFAULT.index('q')
    again = Library(fw, ui, st, dict(lib.disk.files))  # the page built afresh from the file
    assert again.shown[7:9] == ['Recently Added', 'Ratings']


def test_a_finger_held_on_a_row_enters_edit_library_and_drags_it(fw, ui, st, keys):
    lib = Library(fw, ui, st)
    down = C.create_string_buffer(0x40)
    struct.pack_into('<i', down, O['EVENT_TYPE'], EVT_DOWN)
    struct.pack_into('<2i', down, O['EVENT_X'], 100, 58 + PITCH * 2 + 10)  # on Artist
    assert fw.fn('lr_edit_touch', C.c_int, C.c_void_p)(down) == 0  # the rows keep it until the hold
    ui.fire('lr_hold_due')
    assert lib.editing and lib.aborts == [lib.win]  # the press the rows had, let go
    event = C.create_string_buffer(0x40)
    handler = ui.handler(lib.win, EVT_MOVE)

    def send(kind, y):
        struct.pack_into('<i', event, O['EVENT_TYPE'], kind)
        struct.pack_into('<2i', event, O['EVENT_X'], 100, y)
        return handler(C.addressof(event))

    assert send(EVT_MOVE, 58 + PITCH * 4 + 10) == STOP  # two rows down: the list does not scroll
    assert lib.shown[:5] == ['All Songs', 'Playlist', 'Album', 'Search', 'Artist']
    assert lib.saved() == ''
    assert send(EVT_UP, 58 + PITCH * 4 + 10) == STOP
    assert lib.saved().startswith('hsplSa')  # the hidden Shuffle keeps its place


def test_a_tap_in_edit_library_selects_its_row_and_opens_nothing(fw, ui, st, keys):
    lib = Library(fw, ui, st)
    lib.hold_centre()
    item = ui.kids[lib.view][5]
    button = ui.kids[item][0]
    assert fw.fn('lr_edit_click', C.c_int, C.c_void_p)(button) == 1
    lib.key(KEY_RETURN)
    assert fw.fn('lr_edit_click', C.c_int, C.c_void_p)(button) == 0  # out of it, the row opens again


def test_rows_slide_aside_over_the_slide_time(fw, ui, st, keys):
    lib = Library(fw, ui, st)
    lib.hold_centre()
    lib.select(1)
    lib.key(KEY_CENTER)
    lib.key(KEY_NEXT)
    assert lib.y('Playlist') == 2 * PITCH  # the carried row goes at once
    assert lib.y('Artist') == 2 * PITCH  # Artist still where it showed
    (t, ms), = ui.pending('lr_slide_step')
    assert ms == LR['LR_STEP_MS']
    fw.mock('time_now_ms', lambda *_: 10 ** 6)
    ui.fire('lr_slide_step')
    assert lib.y('Artist') == PITCH and not ui.pending('lr_slide_step')


def test_a_row_new_to_the_saved_order_shows_at_its_default_place_on_the_page(fw, ui, st):
    """A saved order from before Era existed, Genres moved last: Era comes in after Genres, the rest as saved."""
    mine = DEFAULT.replace('e', '').replace('g', '') + 'g'
    lib = Library(fw, ui, st, {FILE: mine.encode().ljust(LR['LR_MAX'], b'\0')})
    assert lib.shown[-3:] == ['Update Local Music', 'Genre', 'Years']


# A saved order from before Books' row was retired: "B" was its letter, between Photos and Games.
BEFORE_BOOKS = 'hspalSgerfncmqiLPAvoBGUu'


def test_a_retired_rows_letter_in_a_saved_order_is_dropped(fw):
    assert 'B' not in DEFAULT
    assert resolve(fw, BEFORE_BOOKS) == DEFAULT
    mine = 'B' + BEFORE_BOOKS.replace('s', '').replace('B', '') + 's'  # Books first, Songs last
    assert resolve(fw, mine) == DEFAULT.replace('s', '') + 's'


def test_a_saved_order_naming_a_retired_row_loads_and_saves_without_it(fw, ui, st, keys):
    """The page shows the rest in the saved order, and the next save writes the order without the retired letter."""
    mine = 'B' + BEFORE_BOOKS.replace('s', '').replace('B', '') + 's'
    lib = Library(fw, ui, st, {FILE: mine.encode().ljust(LR['LR_MAX'], b'\0')})
    assert lib.shown == SHOWN[1:] + ['All Songs']
    lib.hold_centre()
    lib.hold_centre()
    assert lib.saved() == DEFAULT.replace('s', '') + 's'
