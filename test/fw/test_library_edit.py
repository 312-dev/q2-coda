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
# ... and in Edit Library, with the rows hidden until the user shows them (the menu review's)
EDITING = ['Shuffle', *SHOWN[:10], 'Most Played', 'Hi-Res', 'Long Songs', 'Games', 'Update Local Music']


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
        use_set(st)
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
        # as the device lays it out: the page's window under the status row, the view at its top
        struct.pack_into('<4i', ui.blocks[self.win], 0, 0, 58, 375, 262)
        struct.pack_into('<4i', ui.blocks[self.view], 0, 0, 0, 375, 262)
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
    assert lib.shown == EDITING
    lib.select(2)  # Playlist
    assert lib.key(KEY_CENTER)
    assert lib.key(KEY_NEXT) and lib.key(KEY_NEXT)
    assert lib.shown[:5] == ['Shuffle', 'All Songs', 'Artist', 'Album', 'Playlist'] and ui.props[(lib.view, SEL)] == 4
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
    assert lib.shown == EDITING


def test_return_while_carrying_puts_the_row_back_and_stays_in_edit_library(fw, ui, st, keys):
    lib = Library(fw, ui, st)
    lib.hold_centre()
    lib.select(5)  # Search
    lib.key(KEY_CENTER)
    for _ in range(6):
        lib.key(KEY_PREV)
    assert lib.shown[0] == 'Search'
    assert lib.key(KEY_RETURN)
    assert lib.shown == EDITING and lib.editing and ui.props[(lib.view, SEL)] == 5
    assert lib.saved() == ''
    assert lib.key(KEY_RETURN)  # not carrying: saves and leaves
    assert not lib.editing and lib.saved() == DEFAULT
    assert lib.shown == SHOWN and ui.props[(lib.view, SEL)] == 4  # the hidden rows gone, Search kept


def test_the_order_survives_a_reboot_and_hidden_rows_keep_their_places(fw, ui, st, keys):
    lib = Library(fw, ui, st)
    lib.hold_centre()
    lib.select(8)  # Ratings, just before the hidden My Fav
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

    # Shuffle came in above it: Artist stays a row down from the finger, and goes as far as the finger does
    assert send(EVT_MOVE, 58 + PITCH * 2 + 10) == STOP  # not moved yet: it stays in its place
    assert lib.shown[:5] == ['Shuffle', 'All Songs', 'Playlist', 'Artist', 'Album']
    assert send(EVT_MOVE, 58 + PITCH * 4 + 10) == STOP  # two rows down: the list does not scroll
    assert lib.shown[:6] == ['Shuffle', 'All Songs', 'Playlist', 'Album', 'Search', 'Artist']
    assert lib.saved() == ''
    assert send(EVT_UP, 58 + PITCH * 4 + 10) == STOP
    assert lib.saved().startswith('hsplSa')


def test_the_editors_list_ends_a_row_higher_with_its_fade_over_the_hint(fw, ui, st, keys):
    """The rows end LR_ROOM above the set's list end while the editor is open, so the hint has the room under
    them a list has above the screen's foot, and the rows past that end fade into the ground by LR_FOOT."""
    lib = Library(fw, ui, st)
    room, foot = (fw.fn(f, C.c_int, C.c_void_p) for f in ('lr_edit_room', 'lr_edit_foot'))
    assert room(lib.view) == 0 and foot(lib.view) == 0
    lib.hold_centre()
    assert room(lib.view) == LR['LR_ROOM'] == PITCH and foot(lib.view) == LR['LR_FOOT']
    assert room(lib.win) == 0  # only the list being edited
    lib.key(KEY_RETURN)
    assert room(lib.view) == 0 and foot(lib.view) == 0


def test_the_rows_past_the_editors_end_fade_into_the_ground_by_its_foot(fw, ui, st):
    """home.c list_fades with an editor's foot: clear down to 16 px above the list end, as every list's fade, and
    the ground's own colour, opaque, from LR_FOOT on; without one, the screen's foot's (y 316)."""
    from test_nav_painters import Canvas
    use_set(st)
    fades = fw.fn('list_fades', None, C.c_void_p, *[C.c_int] * 8)
    end = 58 + use_set(st).list_end - LR['LR_ROOM']

    def alphas(foot):
        canvas = Canvas(fw)
        fades(canvas.addr, 58, 320, end, foot, 0, 1, 1, 0)
        return {r[1]: color >> 24 for r, color in canvas.rects}  # each screen row's ground, by its alpha

    mine, theirs = alphas(LR['LR_FOOT']), alphas(0)
    assert end - 16 < min(mine) <= min(theirs) <= end - 12  # from where every list's fade starts, a little steeper
    assert all(mine[y] == 255 for y in range(LR['LR_FOOT'], 320)) and mine[LR['LR_FOOT'] - 8] < 255
    assert theirs[LR['LR_FOOT']] < 255 and theirs[316] == 255


def test_a_dragged_row_stays_inside_the_list_and_the_list_scrolls_under_it(fw, ui, st, keys):
    """A finger dragged past the list's top or its end holds the row there, never over the status row or the
    hint, while the list scrolls on under it."""
    lib = Library(fw, ui, st)
    top = 58
    end = top + use_set(st).list_end - LR['LR_ROOM']  # the set's list end counts from the window's top
    down = C.create_string_buffer(0x40)
    struct.pack_into('<i', down, O['EVENT_TYPE'], EVT_DOWN)
    struct.pack_into('<2i', down, O['EVENT_X'], 370, top + PITCH * 2 + 10)  # Playlist's grip: taken at once
    lib.hold_centre()
    assert fw.fn('lr_edit_touch', C.c_int, C.c_void_p)(down) == 1
    handler, event = ui.handler(lib.win, EVT_MOVE), C.create_string_buffer(0x40)

    def drag(y):
        struct.pack_into('<i', event, O['EVENT_TYPE'], EVT_MOVE)
        struct.pack_into('<2i', event, O['EVENT_X'], 370, y)
        assert handler(C.addressof(event)) == STOP
        scroll = struct.unpack_from('<i', ui.blocks[lib.view], O['SCROLL_Y'])[0]
        return top + lib.y('Playlist') - scroll, scroll  # the row's top on the screen

    assert drag(319)[0] == end - PITCH  # past the end: on the list's last place
    ui.fire('lr_scroll_due')  # the finger held there: the list scrolls on, the row with it
    row, scroll = drag(319)
    assert scroll > 0 and row == end - PITCH
    assert drag(0)[0] == top  # past the top: on its first place, under the status row


def test_a_lifted_row_keeps_its_circle_words_and_grip_where_they_stood(fw, ui, st, keys):
    """Center's lift changes the row's look alone: its card is the selection bar's box (inset LR_CARD_X each
    side, LR_LIFT% of the row's height), and its circle, words and grip are drawn where the row's own were."""
    from test_nav_painters import Canvas
    lib = Library(fw, ui, st)
    lib.hold_centre()
    lib.select(3)  # Artist
    canvas = Canvas(fw)
    paint = fw.fn('lr_edit_paint', None, C.c_void_p, C.c_void_p)

    def grips(w):
        canvas.rects.clear()
        paint(w, canvas.addr)
        return [r for r, _ in canvas.rects if r[2:] == (18, 2)]  # list_marks.c grip_mark's bars

    resting = grips(lib.view)
    assert len(resting) == 3 * len(EDITING)
    artist = resting[9:12]
    assert grips(lib.win) == []  # nothing lifted: the page draws no grip
    lib.key(KEY_CENTER)  # lift Artist
    assert grips(lib.view) == resting[:9] + resting[12:]  # the others where they were, Artist's not on the list
    canvas.boxes.clear()
    canvas.texts.clear()
    assert grips(lib.win) == artist  # on its card, where it stood
    card = canvas.boxes[-1][0]  # lift_card_paint's last: the card, over its shadow and ring
    assert LR['LR_CARD_X'] == 16  # the selection bar's inset (docs/oled-design.md#selection-bar)
    middle = artist[1][1] + 1
    assert card == (LR['LR_CARD_X'], middle - PITCH * LR['LR_LIFT'] // 200, 375 - 2 * LR['LR_CARD_X'],
                    PITCH * LR['LR_LIFT'] // 100)
    label = ui.kids[ui.kids[ui.kids[lib.view][3]][0]][0]
    assert ui.kind[label] == 'hscroll_label_create'
    wide = C.create_unicode_buffer(ui.text[label])  # the label's words as AWTK holds them (wchar_t, 4 bytes here)
    fw.mock('widget_get_text', lambda w, *_: C.addressof(wide) if w == label else 0)
    canvas.texts.clear()
    paint(lib.win, canvas.addr)
    word, = [t for t in canvas.texts if t[0] == ui.text[label]]
    x, w = 0, label
    while w:  # the label's x on the screen
        x, w = x + struct.unpack_from('<i', ui.blocks[w], 0)[0], ui.parent(w)
    assert word[1][0] + 1 == x + LR['LR_LABEL_PAD']  # the words' left, as the label's (lr_text centres them)


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
    lib.select(2)
    lib.key(KEY_CENTER)
    lib.key(KEY_NEXT)
    assert lib.y('Playlist') == 3 * PITCH  # the carried row goes at once
    assert lib.y('Artist') == 3 * PITCH  # Artist still where it showed
    (t, ms), = ui.pending('lr_slide_step')
    assert ms == LR['LR_STEP_MS']
    fw.mock('time_now_ms', lambda *_: 10 ** 6)
    ui.fire('lr_slide_step')
    assert lib.y('Artist') == 2 * PITCH and not ui.pending('lr_slide_step')


def test_a_row_new_to_the_saved_order_shows_at_its_default_place_on_the_page(fw, ui, st):
    """A saved order from before Era existed, Genres moved last: Era comes in after Genres, the rest as saved."""
    mine = DEFAULT.replace('e', '').replace('g', '') + 'g'
    lib = Library(fw, ui, st, {FILE: mine.encode().ljust(LR['LR_MAX'], b'\0')})
    assert lib.shown[-3:] == ['Update Local Music', 'Genre', 'Years']


# Visibility: each row is Auto (no mark: its own rule), Shown ('+' before its letter) or Hidden ('-'), the user's
KEY_PLAY = O['KEY_PLAY']
HOME = ['playing', 'localmusic', 'stream', 'folder', 'coverflow', 'home_search', 'settings']
HOME_DEFAULT = re.findall(r'lr_home\[\] = "(\w+)"', SOURCE)[0]
HOME_FILE = '/mnt/data/ringnav-home-order'


def test_a_mark_before_a_letter_is_the_rows_and_the_order_reads_as_before(fw):
    out, vis = C.create_string_buffer(LR['LR_MAX']), C.create_string_buffer(32)
    read = fw.fn('lr_resolve_in', C.c_int, C.c_char_p, C.c_char_p, C.c_void_p, C.c_void_p)
    read(DEFAULT.encode(), ('+h-s' + DEFAULT[2:]).encode(), out, vis)
    assert out.value.decode() == DEFAULT
    assert vis.raw[:3] == b'+-\0'
    # a mark before a letter this firmware doesn't know goes with it; one alone is dropped
    read(DEFAULT.encode(), ('+Z-' + DEFAULT).encode(), out, vis)
    assert out.value.decode() == DEFAULT and vis.raw[:2] == b'-\0'
    assert resolve(fw, '+h-s' + DEFAULT[2:]) == DEFAULT  # the old reader's view: marks are unknown letters


def test_a_mark_is_the_users_and_no_mark_leaves_the_row_to_its_own_rule(fw):
    shows = fw.fn('lr_shows', C.c_int, C.c_int, C.c_int)
    shown, hidden, locked = 1, 2, 3
    assert shows(0, shown) and not shows(0, hidden)
    assert shows(ord('+'), hidden) and not shows(ord('-'), shown)
    assert shows(ord('-'), locked) and shows(0, locked)  # Settings always shows


def test_an_old_saved_order_with_no_marks_keeps_the_rows_hidden_by_default_hidden(fw, ui, st):
    mine = DEFAULT.replace('s', '') + 's'
    lib = Library(fw, ui, st, {FILE: mine.encode().ljust(LR['LR_MAX'], b'\0')})
    assert lib.shown == SHOWN[1:] + ['All Songs']


def test_play_pause_shows_a_hidden_row_and_hides_a_shown_one_and_the_choice_sticks(fw, ui, st, keys):
    """The four transitions: Auto hidden -> Shown, Auto shown -> Hidden, Shown -> Hidden, Hidden -> Shown."""
    lib = Library(fw, ui, st)
    lib.hold_centre()
    lib.select(EDITING.index('Shuffle'))
    assert lib.key(KEY_PLAY)  # Auto, hidden by the menu review: Shown
    assert lib.saved() == '+' + DEFAULT
    lib.select(EDITING.index('Genre'))
    assert lib.key(KEY_PLAY)  # Auto, shown: Hidden
    assert lib.saved() == DEFAULT.replace('h', '+h').replace('g', '-g')
    assert lib.shown == EDITING  # the editor lists both still, Genre dimmed
    lib.key(KEY_RETURN)
    assert lib.shown == ['Shuffle'] + [r for r in SHOWN if r != 'Genre']
    again = Library(fw, ui, st, dict(lib.disk.files))
    assert again.shown == lib.shown
    again.hold_centre()
    again.select(0)
    again.key(KEY_PLAY)  # Shown: Hidden
    again.select(EDITING.index('Genre'))
    again.key(KEY_PLAY)  # Hidden: Shown
    again.key(KEY_RETURN)
    assert again.shown == SHOWN and again.saved() == DEFAULT.replace('h', '-h').replace('g', '+g')


def test_play_pause_outside_the_editor_is_not_taken(fw, ui, st, keys):
    lib = Library(fw, ui, st)
    assert lib.key(KEY_PLAY) == 0 and lib.saved() == ''


def test_a_tap_on_a_rows_circle_shows_or_hides_it(fw, ui, st, keys):
    lib = Library(fw, ui, st)
    lib.hold_centre()
    item = ui.kids[lib.view][[c for c, _ in page_rows(ui, lib.view)].index('Hi-Res')]
    down = C.create_string_buffer(0x40)
    struct.pack_into('<i', down, O['EVENT_TYPE'], EVT_DOWN)
    y = 58 + EDITING.index('Hi-Res') * PITCH + 10
    for x, saved in ((LR['LR_VIS_ZONE'] + 40, ''), (20, DEFAULT.replace('i', '+i'))):  # past the circle: selects
        struct.pack_into('<2i', down, O['EVENT_X'], x, y)
        assert fw.fn('lr_edit_touch', C.c_int, C.c_void_p)(down) == 0
        assert fw.fn('lr_edit_click', C.c_int, C.c_void_p)(ui.kids[item][0]) == 1
        assert lib.saved() == saved
        assert ui.props[(lib.view, SEL)] == EDITING.index('Hi-Res')


class Home:
    """Home's main list's rows, tagged and shown by lr_home_apply; the card's root listed by toolsLoadDirectory. The
    clock moves CARD_RECHECK_MS with each Home and each change of the card, as home_folders looks again no sooner."""
    ms = 10 ** 6

    def __init__(self, fw, ui, files=None, folders=0):
        self.fw, self.ui, self.folders, self.mtime = fw, ui, folders, 1
        Home.ms += O['CARD_RECHECK_MS']
        fw.mock('time_now_ms', lambda *_: Home.ms)
        self.disk = Disk(fw, {'/tmp/mmc_add': b'', **{k: v.ljust(LR['LR_MAX'], b'\0') for k, v in (files or {}).items()}})
        fw.mock('fw___xstat', self.stat)
        fw.mock('toolsLoadDirectory', lambda path, *_: self.folders if C.string_at(path) == b'/mnt/mmc' else 0)
        _, self.view = stock_page(ui, 'scroll_view_home')
        for row in HOME:
            item = ui.new('view_create', self.view, 0, 0, 375, PITCH)
            ui.name[item] = 'btn_' + row
            ui.text[ui.new('hscroll_label_create', item, 24, 0, 300, PITCH)] = row
        fw.fn('lr_home_tag', None, C.c_void_p)(self.view)

    def stat(self, version, path, info, *_):
        struct.pack_into('<I', (C.c_char * 144).from_address(info), 64, self.mtime)  # st_mtime, word 16
        return 0

    def card(self, folders):
        """The card's root changes: folders now, or none."""
        self.folders, self.mtime = folders, self.mtime + 1
        Home.ms += O['CARD_RECHECK_MS']

    def apply(self, np=1):
        self.fw.fn('lr_home_apply', None, C.c_void_p, C.c_int)(self.view, np)
        return [self.ui.name[i][4:] for i in self.ui.kids[self.view] if i not in self.ui.hidden]


def test_home_folows_its_saved_order_and_now_playing_only_with_something_loaded(fw, ui):
    home = Home(fw, ui, {HOME_FILE: b'Sclpqsf'}, folders=3)
    assert home.apply() == ['settings', 'coverflow', 'localmusic', 'playing', 'home_search', 'stream', 'folder']
    assert home.apply(np=0) == ['settings', 'coverflow', 'localmusic', 'home_search', 'stream', 'folder']
    assert Home(fw, ui, folders=3).apply(np=0) == HOME[1:]


def test_folders_is_auto_hidden_until_the_card_has_folders_and_a_users_choice_sticks(fw, ui):
    """Auto follows the card both ways; Shown stays on an empty card, Hidden with folders."""
    home = Home(fw, ui)
    assert 'folder' not in home.apply()
    home.card(2)
    assert 'folder' in home.apply()
    home.card(0)
    assert 'folder' not in home.apply()
    shown = Home(fw, ui, {HOME_FILE: ('+f'.join(HOME_DEFAULT.split('f'))).encode()})
    assert 'folder' in shown.apply()  # Shown, no folders
    hidden = Home(fw, ui, {HOME_FILE: ('-f'.join(HOME_DEFAULT.split('f'))).encode()}, folders=5)
    assert 'folder' not in hidden.apply()  # Hidden, folders


def test_settings_on_home_cannot_be_hidden(fw, ui):
    home = Home(fw, ui, {HOME_FILE: ('-S'.join(HOME_DEFAULT.split('S')) + '').encode()}, folders=1)
    assert home.apply()[-1] == 'settings'
    other = Home(fw, ui, {HOME_FILE: b'-p-l-s-f-c-qS'}, folders=1)
    assert other.apply() == ['settings']


# A saved order from before Photos' and Books' rows were retired: "o" and "B" were their letters, before Games.
BEFORE_BOOKS = 'hspalSgerfncmqiLPAvoBGUu'


def test_a_retired_rows_letter_in_a_saved_order_is_dropped(fw):
    assert 'B' not in DEFAULT and 'o' not in DEFAULT
    assert resolve(fw, BEFORE_BOOKS.replace('o', '')) == DEFAULT
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
