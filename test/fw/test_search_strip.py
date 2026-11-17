"""search_strip.c: Search as an iPod classic's letter strip under the OLED theme (list.entry.ipod): the wheel
walks the strip, Centre types at the cursor, Previous and Next move it and Return erases, the results filter live
(songs, then artists, then albums), Next at the text's end moves to them and Return back, tabs above them pick a
group, and a held Return closes Search."""
import ctypes as C
import struct

import pytest

from conftest import O
from test_nav_library_pages import Config, Library, Songs, Ui, field
from test_nav_display_library import use_set
from test_nav_painters import scratch, st  # noqa: F401  (fixtures: the payload's state)

KEY_RETURN, KEY_CENTER, KEY_PREV, KEY_NEXT, KEY_PLAY, KEY_FWD_BTN, KEY_BACK_BTN = (
    O[k] for k in ('KEY_RETURN', 'KEY_CENTER', 'KEY_PREV', 'KEY_NEXT', 'KEY_PLAY', 'KEY_FWD_BTN', 'KEY_BACK_BTN'))
LIBRARY, SPACE, DELETE, ENTRIES = 0xf001, 36, 37, 38
ON_STRIP, ON_LIST, ON_TABS = 0, 1, 2
ALL, SONGS, ARTISTS, ALBUMS = 0, 1, 2, 3
GROUP, LIST_Y, TABS_X, TABS_W = 32, 32, 16, 343  # All's cap, the results' top under the tabs, the tabs' track
EVT_POINTER_DOWN, EVT_POINTER_UP_BEFORE, EVENT_X = O['EVT_POINTER_DOWN'], O['EVT_POINTER_UP_BEFORE'], O['EVENT_X']
STRIP_Y, LIT_X, STEP = 196, 228, 19  # the strip's top on the sheet, the lit letter's cell, a cell's width
EVT_POINTER_MOVE_BEFORE = O['EVT_POINTER_MOVE_BEFORE']
OLED = b'3'


class Ss(C.Structure):
    _fields_ = [*((k, C.c_void_p) for k in 'page sheet songs match kind'.split()),
                *((k, C.c_int) for k in 'nmatch letter focus sel top nsongs tab any cur by rename pick create'.split()),
                ('box', C.c_void_p), ('done', C.c_void_p), ('max', C.c_int), ('note', C.c_void_p), ('secret', C.c_int), ('set', C.c_int), ('typed_at', C.c_uint), ('show_timer', C.c_uint),
                ('shown', C.c_ubyte), ('play_held', C.c_ubyte),
                *((k, C.c_short) for k in 'down_x down_y pressed swiped'.split()), ('text', C.c_char * 65),
                ('pop_timer', C.c_uint), ('pop_step_at', C.c_uint), ('pop', C.c_ubyte),
                ('hold_at', C.c_uint), ('hold_timer', C.c_uint), ('held', C.c_ubyte)]


@pytest.fixture
def ui(fw):
    return Ui(fw)


@pytest.fixture
def song():
    return Songs()


class Strip:
    """The Search page opened over songs; key() sends one key as ringnav does."""
    im: C.Array[C.c_char]  # a text box's strip (boxed): its input method, edit, keyboard and pages
    edit: int
    kb: int
    keys: int

    def __init__(self, fw, ui, songs):
        self.fw, self.ui = fw, ui
        Config(fw)
        Library(fw, ui, songs)
        self.stock = []
        fw.mock('stock_search', lambda *a: self.stock.append(a) or 0)
        fw.fn('library_search_open', C.c_int, C.c_void_p, C.c_void_p)(None, None)
        self.ss = fw.var('search_strip__ss', Ss)
        self.take = fw.fn('search_strip_key', C.c_int, C.c_void_p, C.c_uint)

    def key(self, *keys):
        return [self.take(self.ui.top(), k) for k in keys]

    def type(self, text):
        """Each character walked to on the wheel and entered with Centre."""
        for ch in text:
            want = SPACE if ch == ' ' else ord(ch.upper()) - 65 if ch.isalpha() else 26 + int(ch)
            step = (want - self.ss.letter) % ENTRIES
            self.key(*[KEY_NEXT] * step, KEY_CENTER)

    def enter(self):
        """Next with the cursor at the text's end: the results."""
        return self.key(KEY_FWD_BTN)

    def touch(self, x0, y0, x1=None, y1=None, moves=()):
        """A press at x0, y0 on the sheet, moves through moves, and its release at x1, y1 (the press's place: a tap),
        in the sheet's coordinates."""
        self.feed(EVT_POINTER_DOWN, x0, y0, self.ss.sheet)
        for x, y in moves:
            self.feed(EVT_POINTER_MOVE_BEFORE, x, y, self.ss.page)
        self.feed(EVT_POINTER_UP_BEFORE, x0 if x1 is None else x1, y0 if y1 is None else y1, self.ss.page)

    def feed(self, evt, x, y, w):
        """One pointer event of type evt at x, y to w's handler."""
        event = C.create_string_buffer(0x40)
        struct.pack_into('<i', event, 0, evt)
        struct.pack_into('<ii', event, EVENT_X, x, y)
        self.ui.handler(w, evt)(C.addressof(event))

    def titles(self):
        """Each result as its row reads: an artist's or album's name with its kind, a song's title."""
        name = self.fw.fn('track_name', C.c_char_p, C.c_char_p, C.c_uint, C.c_void_p)
        out, match = C.create_string_buffer(256), C.cast(self.ss.match, C.POINTER(C.c_void_p))
        kind = C.cast(self.ss.kind, C.POINTER(C.c_ubyte))
        rows = []
        for i in range(self.ss.nmatch):
            k = kind[i]
            rows.append(name(out, 256, match[i]).decode() if k == 0 else
                        ('artist: ' if k == 1 else 'album: ') + field(match[i], 'artist' if k == 1 else 'album').decode())
        return rows


def library(song):
    return [song(b'/m/1.flac', b'Lovely Day', b'Bill Withers', b'Menagerie'),
            song(b'/m/2.flac', b'Love Will Tear Us Apart', b'Joy Division', b'Substance'),
            song(b'/m/3.flac', b'Atmosphere', b'Joy Division', b'Still'),
            song(b'/m/4.flac', b'Glove', b'Somebody', b'Love Songs')]


def test_search_opens_straight_on_the_strip_with_nothing_listed(fw, ui, song):
    s = Strip(fw, ui, library(song))
    assert ui.name[ui.top()] == 'searchstrip_page' and not s.stock
    assert s.ss.text == b'' and s.ss.letter == 0 and s.ss.focus == 0 and s.ss.nmatch == 0
    assert 'Search' in ui.text.values()  # the status row's title


def test_the_wheel_walks_letters_then_digits_space_and_delete_and_wraps(fw, ui, song):
    s = Strip(fw, ui, library(song))
    assert s.key(KEY_NEXT) == [1] and s.ss.letter == 1
    s.key(KEY_PREV, KEY_PREV)
    assert s.ss.letter == DELETE  # back past A to delete, the last entry
    s.key(KEY_PREV)
    assert s.ss.letter == SPACE
    s.key(*[KEY_PREV] * 10)
    assert s.ss.letter == 26  # 0, after Z


def test_results_list_the_songs_then_the_artists_then_the_albums(fw, ui, song):
    s = Strip(fw, ui, library(song))
    s.type('lo')
    assert s.ss.text == b'lo' and s.titles() == ['Lovely Day', 'Love Will Tear Us Apart', 'Glove', 'album: Love Songs']
    s.type('ve s')  # a song's album matches too
    assert s.ss.text == b'love s' and s.titles() == ['Glove', 'album: Love Songs']
    s.key(*[KEY_RETURN] * 6)
    s.type('o')
    assert s.titles() == ['Lovely Day', 'Love Will Tear Us Apart', 'Atmosphere', 'Glove', 'artist: Joy Division',
                          'artist: Somebody', 'album: Love Songs']


def test_previous_and_next_move_the_cursor_and_typing_and_return_act_at_it(fw, ui, song):
    s = Strip(fw, ui, library(song))
    s.type('joy')
    assert s.ss.cur == 3
    assert s.key(KEY_BACK_BTN, KEY_BACK_BTN) == [1, 1] and s.ss.cur == 1 and s.ss.text == b'joy'
    s.type('x')
    assert s.ss.text == b'jxoy' and s.ss.cur == 2 and s.titles() == []
    assert s.key(KEY_RETURN) == [1] and s.ss.text == b'joy' and s.ss.cur == 1  # the character before the cursor
    assert s.titles() == ['Love Will Tear Us Apart', 'Atmosphere', 'artist: Joy Division']
    s.key(KEY_RETURN)
    assert s.ss.text == b'oy' and s.ss.cur == 0
    assert s.key(KEY_RETURN, KEY_BACK_BTN) == [1, 1] and s.ss.text == b'oy' and s.ss.cur == 0  # at the start: nothing
    s.key(KEY_FWD_BTN)
    assert s.ss.cur == 1 and s.ss.focus == ON_STRIP
    s.key(KEY_FWD_BTN)
    assert s.ss.cur == 2 and s.ss.focus == ON_STRIP and ui.backs == 0


def test_a_space_is_never_first_nor_beside_another(fw, ui, song):
    s = Strip(fw, ui, library(song))
    s.type(' ')
    assert s.ss.text == b''
    s.type('a  ')
    assert s.ss.text == b'a '
    s.key(KEY_BACK_BTN)
    s.type(' ')  # between a and the space
    assert s.ss.text == b'a ' and s.ss.cur == 1


def test_the_strip_s_own_space_and_delete_entries(fw, ui, song):
    s = Strip(fw, ui, library(song))
    s.key(KEY_CENTER)  # a
    s.key(KEY_PREV, KEY_CENTER)  # delete
    assert s.ss.text == b''
    s.key(KEY_PREV, KEY_CENTER)  # a space first is not typed
    assert s.ss.text == b''


def test_return_on_no_text_only_clicks(fw, ui, song):
    s = Strip(fw, ui, library(song))
    assert s.key(KEY_RETURN) == [1] and ui.backs == 0 and s.ss.page


def test_next_at_the_text_s_end_moves_to_the_results_and_centre_plays_from_them(fw, ui, song):
    songs = library(song)
    s = Strip(fw, ui, songs)
    s.type('joy')
    s.enter()
    assert s.ss.focus == ON_LIST and s.ss.sel == 0 and ui.backs == 0
    s.key(KEY_NEXT, KEY_NEXT, KEY_NEXT)  # the bar stops at the last result
    assert s.ss.sel == 2
    s.key(KEY_PREV, KEY_CENTER)
    assert ui.played == [([songs[1], songs[2]], 1, LIBRARY)]  # the songs alone are the queue


def test_next_with_text_but_no_match_stays(fw, ui, song):
    s = Strip(fw, ui, library(song))
    s.type('zz')
    assert s.enter() == [1] and s.ss.focus == ON_STRIP and ui.backs == 0


def test_an_artist_is_listed_once_and_opens_its_page(fw, ui, song):
    songs = library(song)
    s = Strip(fw, ui, songs)
    s.type('division')
    assert s.titles() == ['Love Will Tear Us Apart', 'Atmosphere', 'artist: Joy Division']
    s.enter()
    s.key(KEY_NEXT, KEY_NEXT, KEY_CENTER)
    assert ui.went == ['localmusic/artistinfo_page'] and not ui.played


def test_an_album_opens_its_tracks(fw, ui, song):
    s = Strip(fw, ui, library(song))
    s.type('menag')
    assert s.titles() == ['Lovely Day', 'album: Menagerie']
    opened = []
    s.enter()
    s.key(KEY_NEXT)
    fw.mock('getMusicByAlbum', lambda *a: opened.append(a) or 0)  # Cover Flow reads the album's tracks
    s.key(KEY_CENTER)
    assert opened and not ui.played


def test_results_go_back_to_the_strip_by_wheel_or_previous(fw, ui, song):
    s = Strip(fw, ui, library(song))
    s.type('lo')
    s.enter()
    s.key(KEY_PREV)  # a turn up from the first result: the tabs
    assert s.ss.focus == ON_TABS
    s.key(KEY_PREV)  # and up again: the strip
    assert s.ss.focus == ON_STRIP and s.ss.text == b'lo'
    letter = s.ss.letter
    s.enter()
    s.key(KEY_NEXT, KEY_BACK_BTN)  # Previous on the results: the strip, the text and cursor as they were
    assert s.ss.focus == ON_STRIP and s.ss.text == b'lo' and s.ss.cur == 2 and s.ss.letter == letter


@pytest.mark.parametrize('on', ['results', 'tabs'])
def test_return_on_the_results_goes_back_to_the_strip_and_there_erases(fw, ui, song, on):
    s = Strip(fw, ui, library(song))
    s.type('lo')
    s.enter()
    s.key(KEY_PREV, KEY_NEXT)  # the tabs, and the wheel on to Songs
    if on == 'results':
        s.key(KEY_CENTER, KEY_NEXT)  # Centre: Songs' results
        assert s.ss.focus == ON_LIST and s.ss.sel == 1
    s.key(KEY_RETURN)
    assert s.ss.focus == ON_STRIP and ui.backs == 0 and s.ss.text == b'lo' and s.ss.tab == SONGS
    s.key(KEY_RETURN)
    assert s.ss.text == b'l' and s.ss.tab == SONGS and ui.backs == 0


@pytest.mark.parametrize('on', ['strip', 'results', 'tabs'])
def test_a_held_return_closes_search_wherever_the_wheel_is(fw, ui, song, on):
    s = Strip(fw, ui, library(song))
    fw.var('g_backlight_status', C.c_ubyte).value = 1
    s.type('lo')
    if on != 'strip':
        s.enter()
    if on == 'tabs':
        s.key(KEY_PREV)
    assert fw.fn('compact_now_playing', C.c_int)() == 0
    assert ui.backs == 1 and s.ss.text == b'lo' and not ui.went  # closed, not erased, no Now Playing


def test_the_list_scrolls_to_keep_the_bar_on_its_rows(fw, ui, song):
    s = Strip(fw, ui, [song(b'/m/%d.flac' % i, b'Song %d' % i) for i in range(9)])
    s.type('song')
    s.enter()
    s.key(*[KEY_NEXT] * 7)
    assert (s.ss.sel, s.ss.top) == (7, 3)  # five rows under the tabs
    s.key(*[KEY_PREV] * 5)
    assert (s.ss.sel, s.ss.top) == (2, 2)


def test_all_lists_32_songs_before_the_artists_and_songs_lists_every_one(fw, ui, song):
    """A common string could put hundreds of songs before the first artist: All stops at GROUP of them, so the
    artists are GROUP ticks down, and the Songs tab holds the rest."""
    songs = [song(b'/m/%d.flac' % i, b'Track %02d' % i, b'The Band', b'Record') for i in range(GROUP + 8)]
    s = Strip(fw, ui, songs)
    s.type('re')  # every song by its album, and the album itself
    assert s.titles() == ['Track %02d' % i for i in range(GROUP)] + ['album: Record']
    assert s.ss.nsongs == GROUP
    s.enter()
    s.key(KEY_PREV, KEY_NEXT)  # the tabs, and the wheel on to Songs
    assert s.ss.tab == SONGS and s.titles() == ['Track %02d' % i for i in range(GROUP + 8)]
    s.key(KEY_CENTER, *[KEY_NEXT] * (GROUP + 3), KEY_CENTER)  # into Songs' results, down past All's end
    assert ui.played == [(songs, GROUP + 3, LIBRARY)]


def test_the_wheel_moves_between_the_tabs_and_the_buttons_pick_and_the_tab_stays_while_typing(fw, ui, song):
    """On the tabs the wheel picks each as it reaches it (docs/oled-design.md#tabs): down past Albums, or Centre, into
    the results, up past All to the strip; Previous and Next pick the tab before and after, round."""
    s = Strip(fw, ui, library(song))
    s.type('o')
    assert s.ss.tab == ALL and s.titles()[:4] == ['Lovely Day', 'Love Will Tear Us Apart', 'Atmosphere', 'Glove']
    s.enter()
    s.key(KEY_PREV)
    assert s.ss.focus == ON_TABS
    s.key(KEY_NEXT)
    assert s.ss.tab == SONGS and s.ss.focus == ON_TABS and s.titles() == ['Lovely Day', 'Love Will Tear Us Apart', 'Atmosphere', 'Glove']
    s.key(KEY_FWD_BTN)
    assert s.ss.tab == ARTISTS and s.titles() == ['artist: Joy Division', 'artist: Somebody']  # A to Z, once each
    s.key(KEY_NEXT)
    assert s.ss.tab == ALBUMS and s.ss.focus == ON_TABS and s.titles() == ['album: Love Songs']
    s.key(KEY_NEXT)  # past the last tab: its results
    assert s.ss.tab == ALBUMS and s.ss.focus == ON_LIST and s.ss.sel == 0
    s.key(KEY_PREV, KEY_PREV, KEY_PREV, KEY_PREV)  # up the tabs to All
    assert s.ss.tab == ALL and s.ss.focus == ON_TABS
    s.key(KEY_BACK_BTN)  # Previous: the one before, round
    assert s.ss.tab == ALBUMS and s.ss.focus == ON_TABS and s.ss.text == b'o'
    s.key(KEY_CENTER)  # into the results
    assert s.ss.focus == ON_LIST and s.ss.sel == 0
    s.key(KEY_PREV, KEY_RETURN)  # the tabs, then back to the strip
    assert s.ss.focus == ON_STRIP and s.ss.tab == ALBUMS
    s.enter()
    s.key(KEY_PREV, KEY_PREV, KEY_PREV, KEY_PREV, KEY_PREV)  # the tabs, up past All: the strip
    assert s.ss.focus == ON_STRIP and s.ss.tab == ALL
    s.key(KEY_RETURN)  # an erase
    assert s.ss.text == b''
    s.type('s')
    assert s.ss.tab == ALL and 'album: Substance' in s.titles()


def test_a_tab_s_artists_are_every_one_once_a_to_z(fw, ui, song):
    names = [b'Band %02d' % i for i in range(GROUP + 8)]
    songs = [song(b'/m/%d.flac' % i, b'Song %d' % i, names[-1 - i % len(names)]) for i in range(2 * len(names))]
    s = Strip(fw, ui, songs)
    s.type('band')
    assert s.titles()[GROUP:GROUP + 2] == ['artist: Band 39', 'artist: Band 38']  # All: as their songs come
    assert len(s.titles()) == GROUP + GROUP
    s.enter()
    s.key(KEY_PREV, KEY_FWD_BTN, KEY_FWD_BTN)
    assert s.ss.tab == ARTISTS and s.titles() == ['artist: ' + n.decode() for n in names]


def test_next_onto_an_empty_tab_lands_on_the_tabs(fw, ui, song):
    s = Strip(fw, ui, library(song))
    s.type('lo')
    s.enter()
    s.key(KEY_PREV, KEY_FWD_BTN, KEY_FWD_BTN, KEY_RETURN)  # Artists, none holding "lo"; the strip
    assert s.ss.tab == ARTISTS and s.ss.nmatch == 0 and s.ss.focus == ON_STRIP
    s.enter()
    assert s.ss.focus == ON_TABS and ui.backs == 0
    s.key(KEY_NEXT)  # nothing below to go to
    assert s.ss.focus == ON_TABS


def test_a_tap_on_a_tab_picks_it(fw, ui, song):
    s = Strip(fw, ui, library(song))
    s.type('o')
    s.enter()
    s.touch(TABS_X + TABS_W * 3 // 4 + 20, 16)  # Albums, the fourth
    assert s.ss.tab == ALBUMS and s.ss.focus == ON_TABS and s.titles() == ['album: Love Songs']
    s.touch(TABS_X + 20, 16)
    assert s.ss.tab == ALL
    s.touch(100, LIST_Y + 32 + 10)  # the second result under the tabs
    assert s.ss.focus == ON_LIST and s.ss.sel == 1 and ui.played


def test_play_and_other_windows_pass(fw, ui, song):
    s = Strip(fw, ui, library(song))
    assert s.key(KEY_PLAY) == [0]
    assert s.take(0, KEY_NEXT) == 0 and s.ss.letter == 0


def test_closing_frees_and_forgets(fw, ui, song):
    s = Strip(fw, ui, library(song))
    s.type('lo')
    ui.close(ui.top())
    assert not s.ss.page and s.ss.text == b'' and s.ss.nmatch == 0


def test_a_tap_on_a_letter_types_it_at_the_cursor(fw, ui, song):
    s = Strip(fw, ui, library(song))
    s.touch(LIT_X + STEP + 5, STRIP_Y + 17)  # the cell after the lit one: B
    assert s.ss.text == b'b' and s.ss.letter == 1
    s.key(KEY_BACK_BTN)
    s.touch(LIT_X + 5, STRIP_Y + 17)  # the lit one, before the b
    assert s.ss.text == b'bb' and s.ss.cur == 1
    s.touch(LIT_X - 2 * STEP + 5, STRIP_Y + 17)  # two before B: delete, the character before the cursor
    assert s.ss.text == b'b' and s.ss.letter == DELETE and s.ss.cur == 0


def test_a_tap_on_a_faded_letter_at_either_end_types_it(fw, ui, song):
    s = Strip(fw, ui, library(song))
    s.touch(150, STRIP_Y + 17)  # the run's first cell, four before the lit A, round: 8
    assert s.ss.letter == (0 - 4) % ENTRIES and s.ss.text == b'8'
    s.key(*[KEY_NEXT] * 4)  # A again
    s.touch(330, STRIP_Y + 17)  # past the run's last cell, at the strip's end: E, four on
    assert s.ss.letter == 4 and s.ss.text == b'8e'


def test_a_drag_along_the_strip_moves_the_alphabet_as_it_goes_and_types_nothing(fw, ui, song):
    s = Strip(fw, ui, library(song))
    seen = []
    s.feed(EVT_POINTER_DOWN, 300, STRIP_Y + 17, s.ss.sheet)
    for x in (300 - STEP, 300 - 2 * STEP, 300 - 3 * STEP - 4):  # left: later letters come under the highlight
        s.feed(EVT_POINTER_MOVE_BEFORE, x, STRIP_Y + 17, s.ss.page)
        seen.append(s.ss.letter)
    s.feed(EVT_POINTER_UP_BEFORE, 300 - 3 * STEP - 4, STRIP_Y + 17, s.ss.page)
    assert seen == [1, 2, 3] and s.ss.letter == 3 and s.ss.text == b''
    s.touch(100, STRIP_Y + 17, 100 + 2 * STEP, STRIP_Y + 17)  # right, from the text's box: earlier letters
    assert s.ss.letter == 1 and s.ss.text == b''


def test_a_tap_on_a_result_opens_it_and_a_drag_scrolls(fw, ui, song):
    songs = [song(b'/m/%d.flac' % i, b'Song %d' % i) for i in range(9)]
    s = Strip(fw, ui, songs)
    s.type('song')
    s.touch(100, 150, 100, 150 - 2 * 32)  # up: two rows on
    assert s.ss.top == 2 and not ui.played
    s.touch(100, 32 + 10)  # the second row showing
    assert s.ss.focus == ON_LIST and s.ss.sel == 3 and ui.played == [(songs, 3, LIBRARY)]


def test_a_tap_below_the_results_does_nothing(fw, ui, song):
    s = Strip(fw, ui, library(song))
    s.type('joy')
    s.touch(100, 4 * 32 + 10)
    assert not ui.played and not ui.went and s.ss.focus == 0


def test_a_key_the_strip_takes_frees_stock_s_click_latch(fw, ui, song):
    """ringnav returns before stock's key-up, which is what clears the latch its key-down sets after a click: left
    set, every later press on the strip was silent (docs/internals.md#ipod-row-clicks)."""
    s = Strip(fw, ui, library(song))
    latch = C.c_byte.from_address(O['KEY_TONE_LATCH'])
    for key in (KEY_NEXT, KEY_CENTER, KEY_FWD_BTN, KEY_BACK_BTN, KEY_RETURN, KEY_RETURN):
        latch.value = 1
        assert s.key(key) == [1] and latch.value == 0
    latch.value = 1
    assert s.key(KEY_PLAY) == [0] and latch.value == 1  # stock's key-up clears it for a key that goes on


# The key pop: shown when the wheel lands on a letter, gone POP_MS after the last step; a spin (steps under FAST_MS
# apart) shows none until it stops, then one.
POP_MS, FAST_MS = 300, 125


class Popping(Strip):
    def __init__(self, fw, ui, songs):
        self.now = [100000]
        fw.mock('time_now_ms', lambda *_: self.now[0])
        super().__init__(fw, ui, songs)

    def step(self, ms, key=KEY_NEXT):
        self.now[0] += ms
        self.key(key)

    def timer(self):
        """The pop's timer: (its ms, it fires and returns what it returned)."""
        (_, ms), = self.ui.pending('search_strip__ss_pop_due')
        return ms

    def due(self):
        self.ui.fire('search_strip__ss_pop_due')


def test_a_slow_step_pops_at_once_and_the_pop_goes_after_the_wheel_stops(fw, ui, song):
    p = Popping(fw, ui, library(song))
    assert p.ss.pop == 0
    p.step(1000)
    assert p.ss.pop == 1 and p.timer() == POP_MS
    p.step(400)  # another slow step: still shown, its time counted again
    assert p.ss.pop == 1 and p.timer() == POP_MS
    p.due()
    assert p.ss.pop == 0 and not ui.pending('search_strip__ss_pop_due')


def test_a_spin_shows_no_pop_until_it_stops_then_one(fw, ui, song):
    p = Popping(fw, ui, library(song))
    p.step(1000)
    for _ in range(5):
        p.step(60)  # 16 letters a second
        assert p.ss.pop == 2 and p.timer() == FAST_MS
    p.due()  # FAST_MS without a step: the spin has stopped
    assert p.ss.pop == 1 and p.timer() == POP_MS
    p.due()
    assert p.ss.pop == 0


def test_only_the_wheel_pops_and_closing_stops_its_timer(fw, ui, song):
    p = Popping(fw, ui, library(song))
    p.key(KEY_CENTER, KEY_FWD_BTN)
    assert p.ss.pop == 0 and not ui.pending('search_strip__ss_pop_due')
    p.step(1000)
    ui.close(ui.top())
    assert not ui.pending('search_strip__ss_pop_due') and p.ss.pop == 0


def test_a_drag_along_the_strip_pops_as_the_wheel_does_and_a_tap_pops_its_letter(fw, ui, song):
    p = Popping(fw, ui, library(song))
    p.now[0] += 1000
    p.feed(EVT_POINTER_DOWN, 300, STRIP_Y + 17, p.ss.sheet)
    p.feed(EVT_POINTER_MOVE_BEFORE, 300 - STEP, STRIP_Y + 17, p.ss.page)  # a slow step: the pop at once
    assert p.ss.letter == 1 and p.ss.pop == 1 and p.timer() == POP_MS
    p.now[0] += 60
    p.feed(EVT_POINTER_MOVE_BEFORE, 300 - 2 * STEP, STRIP_Y + 17, p.ss.page)  # a quick one: a spin's
    assert p.ss.letter == 2 and p.ss.pop == 2
    p.feed(EVT_POINTER_UP_BEFORE, 300 - 2 * STEP, STRIP_Y + 17, p.ss.page)
    p.due()
    p.due()
    assert p.ss.pop == 0 and p.ss.text == b''
    p.now[0] += 1000
    p.touch(LIT_X + STEP + 5, STRIP_Y + 17)  # D, the cell after the lit C
    assert p.ss.letter == 3 and p.ss.pop == 1 and p.ss.text == b'd'


# What the sheet paints (search_strip_paint), on test_nav_painters' recording canvas.
def rgb(color):
    """A rounded box's colour as RGBA lays it in memory, as 0xRRGGBB."""
    return (color & 0xff) << 16 | color & 0xff00 | color >> 16 & 0xff


def painted(fw, s):
    from test_nav_painters import Canvas
    c = Canvas(fw)
    fw.fn('search_strip_paint', None, C.c_void_p, C.c_void_p)(s.ss.sheet, c.addr)
    return c


def test_the_strip_is_a_low_pill_with_the_text_s_box_and_its_cursor(fw, ui, song):
    """B2 (docs/ipod.md#search): the pill from x 40 to 335 and y 254 to 288 on the page (196 on the sheet) with
    17 px corners, the text's black box from x 46 to 142 inset 5 px, the lit cell's accent key at x 228, and the
    cursor a steady 2 px bar in the text's white after the characters before it."""
    s = Strip(fw, ui, library(song))
    s.type('ab')
    s.key(KEY_BACK_BTN)
    s.ss.pop = 0  # the wheel's pop over: the lit cell's own key shows
    c = painted(fw, s)
    boxes = [(b, r) for b, _, r in c.boxes]
    assert ((40, 196, 295, 34), 17) in boxes and ((46, 201, 96, 24), 12) in boxes and ((228, 201, 18, 24), 5) in boxes
    caret = [(r, ink) for r, ink in c.rects if r[2:] == (2, 18)]
    assert caret == [((56 + 1 + 10, 196 + 17 - 9, 2, 18), 0xffffffff)]  # one character (10 px here) before it
    letters = [t for t, r, _ in c.texts if len(t) == 1 and r[1] == 196 and r[2] == 19]
    assert letters == ['9', 'A', 'B', 'C', 'D', 'E', 'F']  # four cells each side of B: the space and delete drawn


@pytest.mark.parametrize('light', [0, 1])
def test_the_run_s_ends_fade_into_the_strip(fw, ui, song, st, light):
    """Each end of the letters' run fades from clear to exactly the pill's own fill (device bug, 2026-11-17: the fade
    was the pill's grey with red and blue swapped, #1E1C1C on #1C1C1E, a tinted band at each end), in either half."""
    s = Strip(fw, ui, library(song))
    st('light').value = light
    c = painted(fw, s)
    pill = [ink for b, ink, _ in c.boxes if b == (40, 196, 295, 34)]
    assert len(pill) == 1 and rgb(pill[0]) == (0xe3e3e1 if light else 0x1c1c1e)
    fade = {r[0]: ink >> 24 for r, ink in c.rects if r[2] == 1 and r[1] == 202 and ink & 0xffffff == pill[0] & 0xffffff}
    assert fade[148] == 255 and fade[326] == 255 and fade[148 + 21] < 20 and 148 + 22 not in fade
    assert not [r for r, ink in c.rects if r[2] == 1 and r[1] == 202 and ink & 0xffffff != pill[0] & 0xffffff]


def test_the_rows_fill_the_glass_above_the_strip_and_the_tabs_take_the_first_one(fw, ui, song):
    s = Strip(fw, ui, [song(b'/m/%d.flac' % i, b'Song %d' % i) for i in range(9)])
    s.type('song')
    rows = [r[1] for t, r, _ in painted(fw, s).texts if t.startswith('Song ')]
    assert rows == [32 * i for i in range(6)]  # six rows on the strip, no tabs
    s.enter()
    c = painted(fw, s)
    assert [r[1] for t, r, _ in c.texts if t.startswith('Song ')] == [32 * i for i in range(1, 6)]  # five under them
    assert [t for t, *_ in c.texts if t in ('All', 'Songs', 'Artists', 'Albums')] == ['All', 'Songs', 'Artists', 'Albums']
    track, open_tab = [(b, rgb(ink)) for b, ink, _ in c.boxes if b[1] in (1, 3)][:2]
    assert track == ((16, 1, 343, 30), 0x1c1c1e) and open_tab == ((18, 3, 84, 26), 0x3a3a3c)  # All, raised
    s.key(KEY_PREV)  # the wheel on the tabs: the open one in the accent
    assert [rgb(ink) for b, ink, _ in painted(fw, s).boxes if b[1] == 3] != [0x3a3a3c]


def test_a_server_song_that_cannot_play_is_greyed_with_the_offline_mark(fw, ui, song):
    """docs/server-library.md#grayed-rows: Local Only, a server row can't play; its row's text is #666666 (the bar's
    white at 70% when selected) and the cloud-slash mark ends at the right column, the text 12 px clear of it."""
    fw.mock('tk_str_start_with', lambda a, b, *_: C.string_at(a).startswith(C.string_at(b)))
    songs = [song(b'/mnt/mmc/a.flac', b'Song One'), song(b'/mnt/mmc/.subsonic/cat/al1/s1.flac', b'Song Two')]
    s = Strip(fw, ui, songs)
    s.type('song')
    c = painted(fw, s)
    ink = {t: colour for t, r, colour in c.texts}
    assert ink['Song One'] != 0xff666666 and ink['Song Two'] == 0xff666666
    mark = {(x, y) for (x, y), v in c.px.items() if x >= 320 and v & 0xffffff == 0x666666}
    assert mark and min(x for x, y in mark) >= 327 and max(x for x, y in mark) <= 346
    assert {y // 32 for x, y in mark} == {1}  # the second row's alone
    s.enter()
    s.key(KEY_NEXT)  # the bar on it
    c = painted(fw, s)
    assert {t: colour for t, r, colour in c.texts}['Song Two'] == 0xb3ffffff


def test_no_match_is_the_empty_state_centred_above_the_strip(fw, ui, song, st):
    """docs/oled-design.md#empty-and-offline: typed text that matches nothing paints the empty state, its outline
    glyph, "No Matches" and "Try fewer letters", centred between the status row (the page at screen y 58) and the
    strip (196 on the sheet): its 118 px stack from sheet y 39, no tiles."""
    s = Strip(fw, ui, library(song))
    use_set(st, 'oled')  # its bar ends at 58: nothing above the page, so the whole form, its glyph
    struct.pack_into('<i', (C.c_char * 8).from_address(s.ss.page), 4, 58)  # the page under the status row
    s.type('zz')
    c = painted(fw, s)
    top = (196 - 118) // 2
    assert not [b for b, _, r in c.boxes if r == 9]  # the tiles are gone
    words = [t for t, *_ in c.texts]
    assert 'No Matches' in words and 'Try fewer letters' in words and 'No results' not in words
    assert fw.fn('empty_top', C.c_int, C.c_int, C.c_int, C.c_int)(58, 58 + 196, 0) - 58 == top


@pytest.mark.parametrize('light', [0, 1])
def test_the_empty_state_s_title_reads_in_either_half(fw, ui, song, st, light):
    """The empty state's title is Text Primary: white on the black canvas, black on Light Mode's paper (emulator,
    2026-11-16: it was flipped twice in Light Mode, white on white, so only the glyph and hint showed)."""
    s = Strip(fw, ui, library(song))
    st('light').value = light
    s.type('zz')
    title = [colour for t, _, colour in painted(fw, s).texts if t == 'No Matches']
    assert title and rgb(title[0]) == (0x000000 if light else 0xffffff), [hex(c) for c in title]


class Renames:
    """sqlite3_exec as Rename runs it: the name lookup's count (taken, else 0), the rest logged; fail fails an
    update."""
    def __init__(self, fw, taken=0, fail=False):
        self.log, self.taken, self.fail = [], taken, fail
        fw.mock('sqlite3_exec', self.run)

    def run(self, db, sql, callback, ctx, *_):
        statement = C.string_at(sql)
        self.log.append(statement)
        if self.fail and statement.startswith(b'update'):
            return 1
        if statement.startswith(b'select count') and callback:
            value = (C.c_char_p * 1)(b'%d' % self.taken)
            C.CFUNCTYPE(C.c_int, C.c_void_p, C.c_int, C.c_void_p, C.c_void_p)(callback)(ctx, 1, C.addressof(value), None)
        return 0


def renaming(fw, ui, name):
    Config(fw)
    fw.fn('playlist_rename_open', None, C.c_int, C.c_char_p)(4, name)
    s = Strip.__new__(Strip)
    s.fw, s.ui, s.stock = fw, ui, []
    s.ss, s.take = fw.var('search_strip__ss', Ss), fw.fn('search_strip_key', C.c_int, C.c_void_p, C.c_uint)
    return s


def test_rename_opens_on_the_name_typing_a_word_s_first_letter_as_a_capital(fw, ui):
    """A playlist's Rename (its hold menu): the strip with its field off and the name over it, prefilled, the
    cursor at its end, a word's first letter a capital; Previous and Next move the cursor, and Next at the end
    saves (name and filename, as stock's), toasts and goes back (audit2 05b)."""
    sql = Renames(fw)
    s = renaming(fw, ui, b"Road")
    ss = s.ss
    assert ui.name[ui.top()] == 'searchstrip_page' and ss.text == b'Road' and ss.rename == 5 and ss.nmatch == 0
    assert ss.cur == 4 and 'Rename Playlist' in ui.text.values()
    s.type(' tu')  # a space, T, then u
    assert ss.text == b'Road Tu'
    s.key(KEY_BACK_BTN, KEY_FWD_BTN)  # the cursor back a character and on again: nothing saved
    assert ss.cur == 7 and ss.focus == 0 and not sql.log
    s.key(KEY_FWD_BTN)  # at the end: saved
    assert b"update playlist set name='Road Tu',filename='Road Tu' where id=4" in sql.log
    assert ui.toasts[-1] == 'Renamed' and ui.backs == 1


@pytest.mark.parametrize('taken, fail, said', [(1, False, 'A playlist has that name'), (0, True, 'Could not rename playlist')])
def test_a_rename_refused_says_why_and_stays(fw, ui, taken, fail, said):
    sql = Renames(fw, taken, fail)
    renaming(fw, ui, b"Rock 'n' Roll").key(KEY_FWD_BTN)
    assert any(b"filename='Rock ''n'' Roll'" in st for st in sql.log)  # quoted
    assert ui.toasts[-1] == said and ui.backs == 0


def test_an_empty_name_is_not_saved(fw, ui):
    sql = Renames(fw)
    renaming(fw, ui, b' ').key(KEY_FWD_BTN)
    assert ui.toasts[-1] == "Name can't be empty" and not sql.log


class Creates(Renames):
    """sqlite3_exec as ringnav_playlist_create runs it: the insert's changes() 1 unless fail."""
    def run(self, db, sql, callback, ctx, *_):
        statement = C.string_at(sql)
        if statement.startswith(b'insert'):
            self.log.append(statement)
            if self.fail:
                return 1
            value = (C.c_char_p * 1)(b'1')
            return C.CFUNCTYPE(C.c_int, C.c_void_p, C.c_int, C.c_void_p, C.c_void_p)(callback)(ctx, 1, C.addressof(value), None)
        return super().run(db, sql, callback, ctx)


@pytest.mark.parametrize('taken, fail, said, back', [(0, False, 'Playlist created', 1), (1, False, 'A playlist has that name', 0),
                                                     (0, True, 'Could not create playlist', 0)])
def test_new_playlist_names_it_on_the_strip_and_next_at_the_end_makes_it(fw, ui, taken, fail, said, back):
    """New Playlist (audit2 05b): the strip's field off, empty; Next before the name's end moves the cursor, at its
    end makes the playlist (ringnav_playlist_create), toasts and goes back; refused, it says why and stays."""
    Config(fw)
    sql = Creates(fw, taken, fail)
    fw.fn('playlist_new_open', None)()
    s = Strip.__new__(Strip)
    s.fw, s.ui, s.stock = fw, ui, []
    s.ss, s.take = fw.var('search_strip__ss', Ss), fw.fn('search_strip_key', C.c_int, C.c_void_p, C.c_uint)
    assert s.ss.create == 1 and s.ss.text == b'' and 'New Playlist' in ui.text.values()
    s.type('road trip')
    assert s.ss.text == b'Road Trip'
    s.key(KEY_BACK_BTN, KEY_FWD_BTN)
    assert not [q for q in sql.log if q.startswith(b'insert')]
    s.key(KEY_FWD_BTN)
    inserts = [q for q in sql.log if q.startswith(b'insert')]
    assert len(inserts) == (not taken) and all(b"'Road Trip'" in q for q in inserts)
    assert ui.toasts[-1] == said and ui.backs == back


def test_add_songs_search_lists_songs_by_title_or_artist_and_centre_adds_in_place(fw, ui, song):
    """Add Songs' Search row: songs alone (no artist or album rows), found by title or artist, never by album;
    Centre on one adds it to the playlist (stock's batch add, as Add to Playlist's) and the results stay."""
    songs = library(song)
    Config(fw)
    Library(fw, ui, songs)
    there, added = [], []
    fw.mock('checkPlayListExist', lambda r, dest, *_: int(r in there))
    fw.mock('_create_vector', lambda *_: 0x7e)  # Add Songs' own selection
    fw.mock('batch_add_file', lambda cls, kind, one, sel, dest, *_: added.append(dest) or there.append(songs[1]) or 0)
    fw.fn('add_songs_search', None)()
    s = Strip.__new__(Strip)
    s.fw, s.ui, s.stock = fw, ui, []
    s.ss, s.take = fw.var('search_strip__ss', Ss), fw.fn('search_strip_key', C.c_int, C.c_void_p, C.c_uint)
    assert s.ss.pick == 1
    s.type('songs')
    assert s.titles() == []  # Glove's album, Love Songs, isn't asked
    s.key(*[KEY_RETURN] * 5)  # erased
    s.type('joy')
    assert s.titles() == ['Love Will Tear Us Apart', 'Atmosphere']  # by artist, and no artist row
    s.key(KEY_FWD_BTN, KEY_CENTER)
    assert added == [0] and ui.toasts[-1].startswith('Added to') and s.ss.nmatch == 2
    assert ui.backs == 0 and not ui.played


# Return held on any letter-strip page (the user's, 2026-11-17, mock_hold_back.py): a press erases; held, a card shows
# from SHOW_MS and the page closes at HOLD_MS; let go before, and nothing is erased.
SHOW_MS, HOLD_MS = 200, 800


def r_h(ui):
    """The top window's height, which the scrim covers."""
    return C.c_int.from_address(ui.top() + O['W_H']).value


@pytest.mark.parametrize('held_ms, erased, closed, card', [(60, True, False, False), (400, False, False, True),
                                                           (HOLD_MS, False, True, False)])
def test_return_held_shows_its_card_and_leaves_and_a_press_erases(fw, ui, song, held_ms, erased, closed, card):
    s = Popping(fw, ui, library(song))
    s.type('ab')
    down = fw.fn('search_strip_down', None, C.c_void_p, C.c_uint)
    down(ui.top(), KEY_RETURN)
    down(ui.top(), KEY_RETURN)  # a repeat while held changes nothing
    assert len(ui.pending('search_strip__ss_hold_tick')) == 1
    for at in range(0, held_ms, 33):
        s.now[0] += min(33, held_ms - at)
        if s.now[0] - 100000 >= SHOW_MS and ui.pending('search_strip__ss_hold_tick'):
            ui.fire('search_strip__ss_hold_tick')
    assert bool(s.ss.held) == card
    if card:  # over the page, after stock painted it (ringnav_paint), the screen dimmed first
        from test_nav_painters import Canvas
        c = Canvas(fw)
        fw.fn('search_strip_overlay', None, C.c_void_p, C.c_void_p)(ui.top(), c.addr)
        assert [t for t, *_ in c.texts] == ['Keep holding to go back']
        assert [(r, ink) for r, ink in c.rects if r[2] == 375][:1] == [((0, 0, 375, r_h(ui)), 0x8c000000)]
    if not closed:
        s.key(KEY_RETURN)  # the release
    assert (s.ss.text == b'a') == erased and (s.ss.text == b'ab') == (not erased)
    assert ui.backs == int(closed) and not s.ss.held and not s.ss.hold_at


def test_a_names_page_writes_the_name_in_inter_medium(fw, ui):
    """Field off (New Playlist, Rename): the name in Inter Medium 22 (AWTK's 27), as the mock-up draws it."""
    Config(fw)
    fonts = []
    fw.mock('canvas_set_font', lambda c, face, px, *_: fonts.append((C.string_at(face), px & 0xffffffff)))
    s = renaming(fw, ui, b'Road')
    painted(fw, s)
    assert (b'inter-medium', 27) in fonts


# A stock text box (keyboard.c kb_strip_due) on the strip (the user's P1, mock_strip_password.py): its set chip, Play/Pause
# stepping abc, ABC, 123, #+=; Next at the text's end the box's own OK.
LOWER, UPPER, DIGITS, SYMBOLS = 0, 1, 2, 3


def boxed(fw, ui, text=b'', kind='password'):
    """A text box's edit, the input method's, opened on the strip."""
    Config(fw)
    edit = ui.new('edit', 0, 0, 0, 200, 40)
    ui.props[(edit, 'input_type')] = kind
    ui.text[edit] = text.decode()
    im = C.create_string_buffer(0x40)
    C.c_void_p.from_address(C.addressof(im) + O['IM_WIDGET']).value = edit
    fw.mock('input_method', lambda *_: C.addressof(im))
    s = Strip.__new__(Strip)
    kb = ui.new('window_create', 0, 0, 60, 375, 260)  # the input method's keyboard window, top
    keys = ui.new('pages', kb, 0, 40, 375, 221)
    s.fw, s.ui, s.stock, s.im, s.edit, s.kb, s.keys = fw, ui, [], im, edit, kb, keys
    assert fw.fn('search_strip_box', C.c_int, C.c_void_p, C.c_void_p, C.c_char_p)(kb, edit, b'Home Wi-Fi') == 1
    s.ss, s.take = fw.var('search_strip__ss', Ss), fw.fn('search_strip_key', C.c_int, C.c_void_p, C.c_uint)
    return s


def test_a_password_box_types_its_sets_and_next_at_the_end_is_its_ok(fw, ui):
    fw.mock('time_now_ms', lambda *_: 50000)  # the last character typed, a moment ago
    s = boxed(fw, ui)
    assert s.ss.secret == 1 and s.ss.set == LOWER and 'Home Wi-Fi' in ui.text.values()
    assert s.ss.page == s.kb and s.keys in ui.hidden  # on the keyboard's window, its keys hidden: no focus moves
    s.key(KEY_NEXT, KEY_CENTER)  # b
    s.key(KEY_PLAY)  # ABC
    s.key(KEY_CENTER)  # the set's first: A
    s.key(KEY_PLAY, KEY_PLAY, KEY_CENTER)  # #+=: !
    s.key(KEY_PLAY, KEY_NEXT, KEY_CENTER)  # round to abc: b
    assert s.ss.text == b'bA!b' and s.ss.set == LOWER
    texts = [t for t, *_ in painted(fw, s).texts]
    assert 'Password' in texts and 'abc' in texts and '•••b' in texts  # the last typed shows
    s.key(KEY_BACK_BTN, KEY_FWD_BTN)
    assert not fw.calls('input_method_dispatch_action')  # Next before the end moves the cursor
    s.key(KEY_FWD_BTN)
    assert ui.text[s.edit] == 'bA!b' and not ui.backs
    assert [a[0] for _, a in fw.calls('input_method_dispatch_action')] == [C.addressof(s.im)]
    assert [a[1] for _, a in fw.calls('input_method_request')] == [0]  # the OK left it open: closed, as Done does


def test_a_number_box_opens_on_digits_and_a_text_one_keeps_its_text(fw, ui):
    assert boxed(fw, ui, b'1000', 'text').ss.set == DIGITS


def test_return_held_on_a_box_closes_it_and_its_keyboard(fw, ui):
    s = boxed(fw, ui, b'abc', 'text')
    s.now = [100000]
    fw.mock('time_now_ms', lambda *_: s.now[0])
    fw.fn('search_strip_down', None, C.c_void_p, C.c_uint)(ui.top(), KEY_RETURN)
    s.now[0] += HOLD_MS
    ui.fire('search_strip__ss_hold_tick')
    assert not ui.backs and not fw.calls('input_method_dispatch_action')
    assert [a[1] for _, a in fw.calls('input_method_request')] == [0]  # the keyboard closed with it
