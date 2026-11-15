"""search_strip.c: Search as an iPod classic's letter strip under the OLED theme (list.entry.ipod): the wheel
walks the strip, Centre types at the cursor, Previous and Next move it and Return erases, the results filter live
(songs, then artists, then albums), Next at the text's end moves to them and Return back, tabs above them pick a
group, and a held Return closes Search."""
import ctypes as C
import struct

import pytest

from conftest import O
from test_nav_library_pages import Config, Library, Songs, Ui, field

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
                *((k, C.c_int) for k in 'nmatch letter focus sel top nsongs tab any cur by'.split()),
                *((k, C.c_short) for k in 'down_x down_y pressed swiped'.split()), ('text', C.c_char * 49),
                ('pop_timer', C.c_uint), ('pop_step_at', C.c_uint), ('pop', C.c_ubyte)]


@pytest.fixture
def ui(fw):
    return Ui(fw)


@pytest.fixture
def song():
    return Songs()


class Strip:
    """The Search page opened under theme, over songs; key() sends one key as ringnav does."""
    def __init__(self, fw, ui, songs, theme=OLED):
        self.fw, self.ui = fw, ui
        Config(fw, **({} if theme is None else {'THEME': theme}))
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


def test_other_themes_keep_stock_s_search(fw, ui, song):
    s = Strip(fw, ui, library(song), theme=None)
    assert len(s.stock) == 1 and not ui.windows


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
    s.key(KEY_PREV, KEY_CENTER)  # the tabs: Songs
    if on == 'results':
        s.key(KEY_NEXT, KEY_NEXT)
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
    s.key(KEY_PREV, KEY_CENTER)  # the tabs: Songs
    assert s.ss.tab == SONGS and s.titles() == ['Track %02d' % i for i in range(GROUP + 8)]
    s.key(KEY_NEXT, *[KEY_NEXT] * (GROUP + 3), KEY_CENTER)  # down past All's end
    assert ui.played == [(songs, GROUP + 3, LIBRARY)]


def test_the_tabs_pick_a_group_by_centre_and_the_buttons_and_it_stays_while_typing(fw, ui, song):
    s = Strip(fw, ui, library(song))
    s.type('o')
    assert s.ss.tab == ALL and s.titles()[:4] == ['Lovely Day', 'Love Will Tear Us Apart', 'Atmosphere', 'Glove']
    s.enter()
    s.key(KEY_PREV)
    assert s.ss.focus == ON_TABS
    s.key(KEY_CENTER)
    assert s.ss.tab == SONGS and s.titles() == ['Lovely Day', 'Love Will Tear Us Apart', 'Atmosphere', 'Glove']
    s.key(KEY_FWD_BTN)
    assert s.ss.tab == ARTISTS and s.titles() == ['artist: Joy Division', 'artist: Somebody']  # A to Z, once each
    s.key(KEY_CENTER)
    assert s.ss.tab == ALBUMS and s.titles() == ['album: Love Songs']
    s.key(KEY_CENTER)
    assert s.ss.tab == ALL
    s.key(KEY_BACK_BTN)  # Previous: the one before, round
    assert s.ss.tab == ALBUMS and s.ss.focus == ON_TABS and s.ss.text == b'o'
    s.key(KEY_NEXT)  # down to the results
    assert s.ss.focus == ON_LIST and s.ss.sel == 0
    s.key(KEY_RETURN, KEY_RETURN)  # back to the strip, and an erase
    assert s.ss.focus == ON_STRIP and s.ss.text == b'' and s.ss.tab == ALBUMS
    s.type('s')
    assert s.ss.tab == ALBUMS and s.titles() == ['album: Love Songs', 'album: Still', 'album: Substance']


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


def test_the_run_s_ends_fade_into_the_strip(fw, ui, song):
    s = Strip(fw, ui, library(song))
    c = painted(fw, s)
    fade = {r[0]: ink >> 24 for r, ink in c.rects if r[2] == 1 and r[1] == 202 and ink & 0xffffff == 0x1c1c1e}
    assert fade[148] == 255 and fade[326] == 255 and fade[148 + 21] < 20 and 148 + 22 not in fade


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
    """docs/server-library.md#greyed-rows: Local Only, a server row can't play; its row's text is #666666 (the bar's
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
