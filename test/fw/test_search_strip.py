"""search_strip.c: Search as an iPod classic's letter strip under the OLED theme (list.entry.ipod), and the names
and text boxes that type on it (the user's mock-up keyboard-inscreen/strip.html): the wheel walks one loop, the set
switcher, the set's characters and a space, the return key; one turn stops at the return key and a new one goes on
round; Centre types, cycles the set or submits; Return jumps to the return key and held goes back; Previous and Next
move the cursor and Previous held erases, faster as it goes on; Play/Pause submits. Search's results filter live
(songs, then artists, then albums), and tabs above them pick a group."""
import ctypes as C
import struct

import pytest

from conftest import O
from test_nav_library_pages import Config, Library, Songs, Ui, field
from test_nav_display_library import use_set
from test_nav_painters import scratch, st  # noqa: F401  (fixtures: the payload's state)

KEY_RETURN, KEY_CENTER, KEY_PREV, KEY_NEXT, KEY_PLAY, KEY_FWD_BTN, KEY_BACK_BTN = (
    O[k] for k in ('KEY_RETURN', 'KEY_CENTER', 'KEY_PREV', 'KEY_NEXT', 'KEY_PLAY', 'KEY_FWD_BTN', 'KEY_BACK_BTN'))
LIBRARY = 0xf001
ON_STRIP, ON_LIST, ON_TABS = 0, 1, 2
ALL, SONGS, ARTISTS, ALBUMS = 0, 1, 2, 3
LOWER, UPPER, DIGITS, SYMBOLS = 0, 1, 2, 3
SETS = ['abcdefghijklmnopqrstuvwxyz', 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', '0123456789', '!@#$%&*-_.?/+=:;,\'"()[]{}<>^~`\\|']
GROUP, QUERY_H, LIST_Y, TABS_X, TABS_W = 32, 40, 72, 16, 343  # All's cap, the results' top, under the tabs, the tabs
EVT_POINTER_DOWN, EVT_POINTER_UP_BEFORE, EVENT_X = O['EVT_POINTER_DOWN'], O['EVT_POINTER_UP_BEFORE'], O['EVENT_X']
STRIP_Y, STEP, RUN_X, VISIBLE, RET_L = 196, 19, 106, 10, 301  # the strip's top on the sheet, a cell, the run's first
EVT_POINTER_MOVE_BEFORE = O['EVT_POINTER_MOVE_BEFORE']
GESTURE_MS = 250  # wheel ticks closer than this are one turn
OLED = b'3'
DONE = C.CFUNCTYPE(C.c_char_p, C.c_char_p)  # search_strip_text's done: 0 to close, else why not


class Ss(C.Structure):
    _fields_ = [*((k, C.c_void_p) for k in 'page sheet songs match kind'.split()),
                *((k, C.c_int) for k in 'nmatch stop off focus sel top nsongs tab any cur by rename pick create'.split()),
                ('box', C.c_void_p), ('done', C.c_void_p), ('max', C.c_int), ('note', C.c_void_p), ('secret', C.c_int),
                ('set', C.c_int), ('typed_at', C.c_uint), ('turn_at', C.c_uint), ('halted', C.c_ubyte),
                *((k, C.c_short) for k in 'down_x down_y pressed swiped'.split()), ('text', C.c_char * 65),
                ('pop_timer', C.c_uint), ('pop_step_at', C.c_uint), ('pop', C.c_ubyte),
                ('hold_at', C.c_uint), ('hold_timer', C.c_uint), ('held', C.c_ubyte),
                ('erase_timer', C.c_uint), ('erase_gap', C.c_uint), ('prev_down', C.c_ubyte), ('erased', C.c_ubyte),
                ('peek_timer', C.c_uint), ('under', C.c_void_p), ('label', C.c_void_p),
                *((k, C.c_short) for k in 'form row_y row_h scroll fx fy fw fh'.split()),
                ('blink_at', C.c_uint), ('blink_timer', C.c_uint), ('covered', C.c_void_p), ('covered_win', C.c_void_p),
                ('edge', C.c_short * 65), ('nedge', C.c_short), ('fface', C.c_void_p), ('fpx', C.c_int),
                ('fch', C.c_int), ('grab', C.c_ubyte), ('loupe', C.c_ubyte), ('nudge', C.c_byte), ('grab_x', C.c_short),
                ('loupe_at', C.c_uint), ('loupe_timer', C.c_uint), ('nudge_timer', C.c_uint)]


@pytest.fixture
def ui(fw):
    return Ui(fw)


@pytest.fixture
def song():
    return Songs()


class Strip:
    """A strip's page; key() sends keys as ringnav does, each a new turn of the wheel (ms apart) unless told."""
    im: C.Array[C.c_char]  # a text box's strip (boxed): its input method, edit, keyboard and pages
    edit: int
    kb: int
    keys: int
    dialog: int  # ... the edit's window, and the row it sits in
    holder: int

    def __init__(self, fw, ui, songs):
        self.attach(fw, ui)
        Config(fw)
        Library(fw, ui, songs)
        self.stock = []
        fw.mock('stock_search', lambda *a: self.stock.append(a) or 0)
        fw.fn('library_search_open', C.c_int, C.c_void_p, C.c_void_p)(None, None)

    def attach(self, fw, ui):
        """The clock (time_now_ms), the strip's state and its key entry."""
        self.fw, self.ui, self.stock = fw, ui, []
        self.now = [100000]
        fw.mock('time_now_ms', lambda *_: self.now[0])
        self.ss = fw.var('search_strip__ss', Ss)
        self.take = fw.fn('search_strip_key', C.c_int, C.c_void_p, C.c_uint)
        self.down = fw.fn('search_strip_down', None, C.c_void_p, C.c_uint)

    def key(self, *keys, ms=400):
        out = []
        for k in keys:
            self.now[0] += ms
            out.append(self.take(self.ui.top(), k))
        return out

    def stops(self):
        return len(SETS[self.ss.set]) + 3

    def go(self, stop):
        """The highlight walked on to stop, a turn a tick (never stopped at the return key)."""
        self.key(*[KEY_NEXT] * ((stop - self.ss.stop) % self.stops()))

    def type(self, text):
        """Each character: its set picked on the switcher, walked to on the wheel and entered with Centre."""
        for ch in text:
            if ch != ' ' and ch not in SETS[self.ss.set]:
                self.go(0)
                while ch not in SETS[self.ss.set]:
                    self.key(KEY_CENTER)
            self.go(1 + (len(SETS[self.ss.set]) if ch == ' ' else SETS[self.ss.set].index(ch)))
            self.key(KEY_CENTER)

    def erase(self, n):
        """Previous held for n erases, then let go."""
        self.down(self.ui.top(), KEY_BACK_BTN)
        for _ in range(n):
            self.ui.fire('search_strip__ss_erase_due')
        return self.key(KEY_BACK_BTN)

    def enter(self):
        """Play/Pause: submitted (Search's results)."""
        return self.key(KEY_PLAY)

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


def attached(fw, ui):
    """A Strip over a page another call opened."""
    s = Strip.__new__(Strip)
    s.attach(fw, ui)
    return s


def library(song):
    return [song(b'/m/1.flac', b'Lovely Day', b'Bill Withers', b'Menagerie'),
            song(b'/m/2.flac', b'Love Will Tear Us Apart', b'Joy Division', b'Substance'),
            song(b'/m/3.flac', b'Atmosphere', b'Joy Division', b'Still'),
            song(b'/m/4.flac', b'Glove', b'Somebody', b'Love Songs')]


def test_search_opens_straight_on_the_strip_at_its_first_capital_with_nothing_listed(fw, ui, song):
    s = Strip(fw, ui, library(song))
    assert ui.name[ui.top()] == 'searchstrip_page' and not s.stock
    assert s.ss.text == b'' and s.ss.set == UPPER and s.ss.stop == 1 and s.ss.off == 0 and s.ss.focus == 0
    assert s.ss.nmatch == 0 and 'Search' in ui.text.values()  # the status row's title


def test_the_loop_is_the_switcher_the_set_a_space_and_the_return_key(fw, ui, song):
    s = Strip(fw, ui, library(song))
    assert s.key(KEY_PREV) == [1] and s.ss.stop == 0  # the switcher, before A
    s.key(KEY_PREV)
    assert s.ss.stop == 28 and s.ss.halted  # back round to the return key, the last: 26 capitals and the space
    s.key(KEY_PREV)
    assert s.ss.stop == 27  # the space
    s.key(KEY_NEXT, KEY_NEXT)
    assert s.ss.stop == 0  # each a turn of its own: on past the return key


def test_one_turn_stops_at_the_return_key_and_a_new_one_loops_to_the_switcher(fw, ui, song):
    s = Strip(fw, ui, library(song))
    s.go(25)  # Y
    s.key(*[KEY_NEXT] * 6, ms=GESTURE_MS - 10)  # one turn: Z, the space, the return key, and stopped there
    assert s.ss.stop == 28 and s.ss.halted
    s.key(KEY_PREV, ms=GESTURE_MS - 10)  # the same turn back goes nowhere either
    assert s.ss.stop == 28
    s.key(KEY_NEXT, ms=GESTURE_MS)  # a new turn: round to the switcher
    assert s.ss.stop == 0 and not s.ss.halted
    s.key(KEY_NEXT, KEY_NEXT, ms=10)
    assert s.ss.stop == 2


def test_the_run_starts_at_its_first_character_and_scrolls_only_a_cell_from_an_edge(fw, ui, song):
    s = Strip(fw, ui, library(song))
    s.go(9)  # I, the ninth of ten cells shown: still in from the edge
    assert s.ss.off == 0
    s.go(10)  # J: the run moves on a cell, keeping a cell past the highlight in view
    assert s.ss.off == 1
    s.go(27)  # the space, the run's last: its end in view, no further
    assert s.ss.off == 27 - VISIBLE
    s.go(2)  # round past the return key and the switcher to B: the run back at its start
    assert s.ss.off == 0
    s.go(20)
    s.go(0)
    s.key(KEY_CENTER)
    assert s.ss.off == 0 and s.ss.set == DIGITS


def test_centre_on_the_switcher_cycles_the_sets_and_stays_on_it(fw, ui, song):
    s = Strip(fw, ui, library(song))
    s.go(0)
    seen = []
    for _ in range(4):
        s.key(KEY_CENTER)
        seen.append(s.ss.set)
    assert seen == [DIGITS, SYMBOLS, LOWER, UPPER] and s.ss.stop == 0 and s.ss.text == b''
    s.type('a1"\\ |Z')
    assert s.ss.text == b'a1"\\ |Z'


def test_the_symbols_are_every_printable_one_once_across_the_sets(fw, ui, song):
    """#+= holds the 32 symbols: with the letters, digits and the space, every printable ASCII character."""
    chars = ''.join(SETS)
    assert len(SETS[SYMBOLS]) == 32 and sorted(chars + ' ') == [chr(c) for c in range(32, 127)]
    s = Strip(fw, ui, library(song))
    s.go(0)
    s.key(KEY_CENTER, KEY_CENTER)
    s.go(32)
    s.key(KEY_CENTER)
    assert s.ss.text == b'|'


def test_results_list_the_songs_then_the_artists_then_the_albums(fw, ui, song):
    s = Strip(fw, ui, library(song))
    s.type('lo')
    assert s.ss.text == b'lo' and s.titles() == ['Lovely Day', 'Love Will Tear Us Apart', 'Glove', 'album: Love Songs']
    s.type('ve s')  # a song's album matches too
    assert s.ss.text == b'love s' and s.titles() == ['Glove', 'album: Love Songs']
    s.erase(6)
    s.type('o')
    assert s.titles() == ['Lovely Day', 'Love Will Tear Us Apart', 'Atmosphere', 'Glove', 'artist: Joy Division',
                          'artist: Somebody', 'album: Love Songs']


def test_previous_and_next_move_the_cursor_and_typing_acts_at_it(fw, ui, song):
    s = Strip(fw, ui, library(song))
    s.type('joy')
    assert s.ss.cur == 3
    assert s.key(KEY_BACK_BTN, KEY_BACK_BTN) == [1, 1] and s.ss.cur == 1 and s.ss.text == b'joy'
    s.type('x')
    assert s.ss.text == b'jxoy' and s.ss.cur == 2 and s.titles() == []
    s.erase(1)  # the character before the cursor
    assert s.ss.text == b'joy' and s.ss.cur == 1 and s.titles() == ['Love Will Tear Us Apart', 'Atmosphere', 'artist: Joy Division']
    s.key(KEY_BACK_BTN, KEY_BACK_BTN)
    assert s.ss.cur == 0
    assert s.erase(1) == [1] and s.ss.text == b'joy'  # at the start: nothing
    s.key(KEY_FWD_BTN, KEY_FWD_BTN, KEY_FWD_BTN, KEY_FWD_BTN)
    assert s.ss.cur == 3 and s.ss.focus == ON_STRIP and ui.backs == 0  # Next stops at the end


def test_a_space_is_never_first_nor_beside_another(fw, ui, song):
    s = Strip(fw, ui, library(song))
    s.type(' ')
    assert s.ss.text == b''
    s.type('a  ')
    assert s.ss.text == b'a '
    s.key(KEY_BACK_BTN)
    s.type(' ')  # between a and the space
    assert s.ss.text == b'a ' and s.ss.cur == 1


def test_previous_held_erases_sooner_each_time_down_to_110_ms(fw, ui, song):
    """The first erase 450 ms into the hold, the next 500 ms after it, each after that 15% sooner, never under
    110 ms; letting go moves no cursor."""
    s = Strip(fw, ui, library(song))
    s.type('abcdefghijklmn')
    s.down(ui.top(), KEY_BACK_BTN)
    s.down(ui.top(), KEY_BACK_BTN)  # a repeat while held changes nothing
    gaps = []
    for _ in range(12):
        (_, ms), = ui.pending('search_strip__ss_erase_due')
        gaps.append(ms)
        ui.fire('search_strip__ss_erase_due')
    assert gaps == [450, 500, 425, 361, 306, 260, 221, 187, 158, 134, 113, 110]
    assert s.ss.text == b'ab' and s.ss.cur == 2
    assert s.key(KEY_BACK_BTN) == [1] and s.ss.cur == 2 and not ui.pending('search_strip__ss_erase_due')
    s.down(ui.top(), KEY_BACK_BTN)
    assert s.key(KEY_BACK_BTN) == [1] and s.ss.text == b'ab' and s.ss.cur == 1  # a press: the cursor
    assert not ui.pending('search_strip__ss_erase_due')


def test_previous_and_next_held_are_the_strip_s_not_a_scan_of_the_song(fw, ui, song):
    s = Strip(fw, ui, library(song))
    owns = fw.fn('search_strip_owns', C.c_int, C.c_void_p)
    assert owns(ui.top()) == 1 and owns(0) == 0
    ui.close(ui.top())
    assert owns(0) == 0


def test_return_jumps_to_the_return_key_and_centre_there_submits(fw, ui, song):
    s = Strip(fw, ui, library(song))
    s.type('joy')
    assert s.key(KEY_RETURN) == [1] and s.ss.stop == 28 and s.ss.halted and s.ss.text == b'joy'
    s.key(KEY_CENTER)
    assert s.ss.focus == ON_LIST and s.ss.sel == 0 and ui.backs == 0


def test_play_submits_from_the_strip_and_is_the_player_s_on_the_results(fw, ui, song):
    s = Strip(fw, ui, library(song))
    s.type('joy')
    assert s.key(KEY_PLAY) == [1] and s.ss.focus == ON_LIST
    assert s.key(KEY_PLAY) == [0] and s.take(0, KEY_NEXT) == 0


def test_submitting_moves_to_the_results_and_centre_plays_from_them(fw, ui, song):
    songs = library(song)
    s = Strip(fw, ui, songs)
    s.type('joy')
    s.enter()
    assert s.ss.focus == ON_LIST and s.ss.sel == 0 and ui.backs == 0
    s.key(KEY_NEXT, KEY_NEXT, KEY_NEXT)  # the bar stops at the last result
    assert s.ss.sel == 2
    s.key(KEY_PREV, KEY_CENTER)
    assert ui.played == [([songs[1], songs[2]], 1, LIBRARY)]  # the songs alone are the queue


def test_a_submit_with_no_match_stays(fw, ui, song):
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
    stop = s.ss.stop
    s.enter()
    s.key(KEY_NEXT, KEY_BACK_BTN)  # Previous on the results: the strip, the text and cursor as they were
    assert s.ss.focus == ON_STRIP and s.ss.text == b'lo' and s.ss.cur == 2 and s.ss.stop == stop


@pytest.mark.parametrize('on', ['results', 'tabs'])
def test_return_on_the_results_goes_back_to_the_strip_and_there_jumps_to_the_return_key(fw, ui, song, on):
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
    assert s.ss.text == b'lo' and s.ss.stop == s.stops() - 1 and ui.backs == 0


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
    assert (s.ss.sel, s.ss.top) == (7, 5)  # three rows under the tabs
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
    s.erase(1)
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


def test_submitting_onto_an_empty_tab_lands_on_the_tabs(fw, ui, song):
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
    s.touch(TABS_X + TABS_W * 3 // 4 + 20, QUERY_H + 16)  # Albums, the fourth
    assert s.ss.tab == ALBUMS and s.ss.focus == ON_TABS and s.titles() == ['album: Love Songs']
    s.touch(TABS_X + 20, QUERY_H + 16)
    assert s.ss.tab == ALL
    s.touch(100, LIST_Y + 32 + 10)  # the second result under the tabs
    assert s.ss.focus == ON_LIST and s.ss.sel == 1 and ui.played


def test_closing_frees_and_forgets(fw, ui, song):
    s = Strip(fw, ui, library(song))
    s.type('lo')
    ui.close(ui.top())
    assert not s.ss.page and s.ss.text == b'' and s.ss.nmatch == 0


def test_a_tap_on_a_result_opens_it_and_a_drag_scrolls(fw, ui, song):
    songs = [song(b'/m/%d.flac' % i, b'Song %d' % i) for i in range(9)]
    s = Strip(fw, ui, songs)
    s.type('song')
    s.touch(100, 150, 100, 150 - 2 * 32)  # up: two rows on
    assert s.ss.top == 2 and not ui.played
    s.touch(100, QUERY_H + 32 + 10)  # the second row showing
    assert s.ss.focus == ON_LIST and s.ss.sel == 3 and ui.played == [(songs, 3, LIBRARY)]


def test_a_tap_below_the_results_does_nothing(fw, ui, song):
    s = Strip(fw, ui, library(song))
    s.type('joy')
    s.touch(100, 4 * 32 + 10)
    assert not ui.played and not ui.went and s.ss.focus == 0


def test_keys_the_strip_takes_free_stock_s_click_latch(fw, ui, song):
    """ringnav returns before stock's key-up, which is what clears the latch its key-down sets after a click: left
    set, every later press on the strip was silent (docs/internals.md#ipod-row-clicks)."""
    s = Strip(fw, ui, library(song))
    latch = C.c_byte.from_address(O['KEY_TONE_LATCH'])
    for key in (KEY_NEXT, KEY_CENTER, KEY_FWD_BTN, KEY_BACK_BTN, KEY_RETURN, KEY_PREV):
        latch.value = 1
        assert s.key(key) == [1] and latch.value == 0
    s.key(KEY_FWD_BTN)
    s.erase(1)  # the B typed above
    s.type('lo')
    s.enter()
    latch.value = 1
    assert s.key(KEY_PLAY) == [0] and latch.value == 1  # stock's key-up clears it for a key that goes on


# The key pop: shown when the wheel lands on a character, gone POP_MS after the last step; a spin (steps under FAST_MS
# apart) shows none until it stops, then one.
POP_MS, FAST_MS = 300, 125


class Popping(Strip):
    def step(self, ms, key=KEY_NEXT):
        self.key(key, ms=ms)

    def timer(self):
        """The pop's timer's ms."""
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
        p.step(60)  # 16 characters a second
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


def test_a_drag_along_the_strip_moves_the_highlight_pops_as_the_wheel_does_and_types_nothing(fw, ui, song):
    p = Popping(fw, ui, library(song))
    p.now[0] += 1000
    p.feed(EVT_POINTER_DOWN, 300, STRIP_Y + 17, p.ss.sheet)
    p.feed(EVT_POINTER_MOVE_BEFORE, 300 - STEP, STRIP_Y + 17, p.ss.page)  # left: on to B, a slow step: the pop
    assert p.ss.stop == 2 and p.ss.pop == 1 and p.timer() == POP_MS
    p.now[0] += 60
    p.feed(EVT_POINTER_MOVE_BEFORE, 300 - 2 * STEP, STRIP_Y + 17, p.ss.page)  # a quick one: a spin's
    assert p.ss.stop == 3 and p.ss.pop == 2
    p.feed(EVT_POINTER_UP_BEFORE, 300 - 2 * STEP, STRIP_Y + 17, p.ss.page)
    p.due()
    p.due()
    assert p.ss.pop == 0 and p.ss.text == b''
    p.touch(200, STRIP_Y + 17, 200 + 2 * STEP, STRIP_Y + 17)  # right: back two
    assert p.ss.stop == 1 and p.ss.text == b''


def test_a_drag_is_one_turn_and_stops_at_the_return_key(fw, ui, song):
    s = Strip(fw, ui, library(song))
    s.go(26)  # Z
    s.touch(300, STRIP_Y + 17, 300 - 5 * STEP, STRIP_Y + 17, moves=[(300 - k * STEP, STRIP_Y + 17) for k in range(1, 6)])
    assert s.ss.stop == 28 and s.ss.text == b''


def test_a_tap_types_a_character_cycles_the_set_on_the_switcher_and_submits_on_the_return_key(fw, ui, song):
    p = Popping(fw, ui, library(song))
    p.touch(RUN_X + 3 * STEP + 5, STRIP_Y + 17)  # the fourth cell: D
    assert p.ss.text == b'D' and p.ss.stop == 4 and p.ss.pop == 1
    p.touch(60, STRIP_Y + 17)  # the switcher
    assert p.ss.set == DIGITS and p.ss.stop == 0 and p.ss.text == b'D'
    p.touch(RUN_X + 5, STRIP_Y + 17)
    assert p.ss.text == b'D0'
    p.touch(RET_L + 17, STRIP_Y + 17)  # the return key: no match, so it stays
    assert p.ss.stop == p.stops() - 1 and p.ss.focus == ON_STRIP
    p.erase(2)
    p.type('lo')
    p.touch(RET_L + 17, STRIP_Y + 17)
    assert p.ss.focus == ON_LIST


# What the sheet paints (search_strip_paint), on test_nav_painters' recording canvas.
def rgb(color):
    """A rounded box's colour as RGBA lays it in memory, as 0xRRGGBB."""
    return (color & 0xff) << 16 | color & 0xff00 | color >> 16 & 0xff


def painted(fw, s):
    from test_nav_painters import Canvas
    c = Canvas(fw)
    fw.fn('search_strip_paint', None, C.c_void_p, C.c_void_p)(s.ss.sheet, c.addr)
    return c


def test_the_strip_docks_the_switcher_left_and_the_return_key_right_the_run_from_its_first_character(fw, ui, song):
    """The pill from x 40 to 335 and y 254 to 288 on the page (196 on the sheet), 17 px corners; the switcher a
    #2C2C2E pill to x 96 naming the set; the return key an accent circle as tall as the strip; the run's cells from x
    106, the lit one's accent key 18 px wide inset 5 px, A to J shown, in Text Primary."""
    s = Strip(fw, ui, library(song))
    c = painted(fw, s)
    boxes = {(b, r): rgb(ink) for b, ink, r in c.boxes}
    assert boxes[((40, 196, 295, 34), 17)] == 0x1c1c1e
    assert boxes[((40, 196, 56, 34), 17)] == 0x2c2c2e
    accent = boxes[((301, 196, 34, 34), 17)]
    assert boxes[((106, 201, 18, 24), 5)] == accent != 0xffffff
    letters = [t for t, r, _ in c.texts if len(t) == 1 and r[1] == 196 and r[2] == 19]
    assert letters[0] == 'A' and sorted(set(letters)) == list('ABCDEFGHIJ')
    assert 'ABC' in [t for t, *_ in c.texts]
    s.key(KEY_PREV)  # the switcher: in the accent
    assert {(b, r): rgb(ink) for b, ink, r in painted(fw, s).boxes}[((40, 196, 56, 34), 17)] == accent
    s.key(KEY_RETURN)  # the return key: a white circle (its mark in the accent)
    boxes = {(b, r): rgb(ink) for b, ink, r in painted(fw, s).boxes}
    assert boxes[((301, 196, 34, 34), 17)] == 0xffffff and boxes[((40, 196, 56, 34), 17)] == 0x2c2c2e


@pytest.mark.parametrize('light', [0, 1])
def test_the_lit_return_key_is_a_white_circle_in_either_half(fw, ui, song, st, light):
    s = Strip(fw, ui, library(song))
    st('light').value = light
    s.key(KEY_RETURN)
    assert {(b, r): rgb(ink) for b, ink, r in painted(fw, s).boxes}[((301, 196, 34, 34), 17)] == 0xffffff


def test_the_run_s_letters_fade_only_where_more_lies_past_an_edge_and_nothing_is_painted_over_them(fw, ui, song):
    s = Strip(fw, ui, library(song))
    c = painted(fw, s)
    alpha = {}
    for t, r, colour in c.texts:
        if len(t) == 1 and r[1] == 196:
            alpha.setdefault(t, []).append(colour >> 24)
    assert alpha['A'] == [0xff] and min(alpha['J']) < 0x80 and len(alpha['J']) > 1  # more past the right edge alone
    assert not [r for r, _ in c.rects if 196 <= r[1] < 230 and r[2] == 1]  # no column painted over the run
    s.go(27)  # the run's end: more past the left edge alone
    alpha = {}
    for t, r, colour in painted(fw, s).texts:
        if len(t) == 1 and r[1] == 196:
            alpha.setdefault(t, []).append(colour >> 24)
    assert min(alpha['R']) < 0x80 and alpha['Z'] == [0xff]


def test_search_s_query_reads_on_its_first_line_with_its_cursor(fw, ui, song):
    s = Strip(fw, ui, library(song))
    c = painted(fw, s)
    assert [r[0] for t, r, _ in c.texts if t == 'Search your music'] == [48]
    assert ((48, 3, 2, 25), 0xffffffff) in c.rects and ((24, 35, 327, 1), c.rects[-1][1]) in c.rects
    s.type('LO')
    c = painted(fw, s)
    assert [r[0] for t, r, _ in c.texts if t == 'LO'] == [48] and ((48 + 20 + 1, 3, 2, 25), 0xffffffff) in c.rects
    s.enter()  # the results have the wheel: no cursor
    assert not [r for r, _ in painted(fw, s).rects if r[2:] == (2, 25)]


def test_the_rows_fill_the_glass_between_the_query_and_the_strip_and_the_tabs_take_the_first_one(fw, ui, song):
    s = Strip(fw, ui, [song(b'/m/%d.flac' % i, b'Song %d' % i) for i in range(9)])
    s.type('song')
    rows = [r[1] for t, r, _ in painted(fw, s).texts if t.startswith('Song ')]
    assert rows == [40 + 32 * i for i in range(4)]  # four rows, no tabs
    s.enter()
    c = painted(fw, s)
    assert [r[1] for t, r, _ in c.texts if t.startswith('Song ')] == [72 + 32 * i for i in range(3)]  # three under them
    assert [t for t, *_ in c.texts if t in ('All', 'Songs', 'Artists', 'Albums')] == ['All', 'Songs', 'Artists', 'Albums']
    track, open_tab = [(b, rgb(ink)) for b, ink, _ in c.boxes if b[1] in (41, 43)][:2]
    assert track == ((16, 41, 343, 30), 0x1c1c1e) and open_tab == ((18, 43, 84, 26), 0x3a3a3c)  # All, raised
    s.key(KEY_PREV)  # the wheel on the tabs: the open one in the accent
    assert [rgb(ink) for b, ink, _ in painted(fw, s).boxes if b[1] == 43] != [0x3a3a3c]


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
    assert {(y - QUERY_H) // 32 for x, y in mark} == {1}  # the second row's alone
    s.enter()
    s.key(KEY_NEXT)  # the bar on it
    c = painted(fw, s)
    assert {t: colour for t, r, colour in c.texts}['Song Two'] == 0xb3ffffff


def test_no_match_is_the_empty_state_centred_above_the_strip(fw, ui, song, st):
    """docs/oled-design.md#empty-and-offline: typed text that matches nothing paints the empty state, its outline
    glyph, "No Matches" and "Try fewer letters", centred between the query (the page at screen y 58, its rule
    on sheet y 35) and the strip (196 on the sheet), no tiles."""
    s = Strip(fw, ui, library(song))
    use_set(st)  # its bar ends at 58: nothing above the page, so the whole form, its glyph
    struct.pack_into('<i', (C.c_char * 8).from_address(s.ss.page), 4, 58)  # the page under the status row
    s.type('zz')
    c = painted(fw, s)
    assert not [b for b, _, r in c.boxes if r == 9]  # the tiles are gone
    words = [t for t, *_ in c.texts]
    assert 'No Matches' in words and 'Try fewer letters' in words and 'No results' not in words
    assert all(QUERY_H <= r[1] and r[1] + r[3] <= 196 for t, r, _ in c.texts if t in ('No Matches', 'Try fewer letters'))


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
    return attached(fw, ui)


def test_rename_opens_on_the_name_and_play_saves_it(fw, ui):
    """A playlist's Rename (its hold menu): the strip and the name over it, prefilled, the cursor at its end;
    Previous and Next move the cursor, and Play/Pause saves (name and filename, as stock's), toasts and goes back
    (audit2 05b)."""
    sql = Renames(fw)
    s = renaming(fw, ui, b"Road")
    ss = s.ss
    assert ui.name[ui.top()] == 'searchstrip_page' and ss.text == b'Road' and ss.rename == 5 and ss.nmatch == 0
    assert ss.cur == 4 and ss.set == LOWER and ss.stop == 1 and 'Rename Playlist' in ui.text.values()
    s.type(' Tu')
    assert ss.text == b'Road Tu'
    s.key(KEY_BACK_BTN, KEY_FWD_BTN, KEY_FWD_BTN)  # the cursor back a character and on again: nothing saved
    assert ss.cur == 7 and ss.focus == 0 and not sql.log
    s.key(KEY_PLAY)
    assert b"update playlist set name='Road Tu',filename='Road Tu' where id=4" in sql.log
    assert ui.toasts[-1] == 'Renamed' and ui.backs == 1


@pytest.mark.parametrize('taken, fail, said', [(1, False, 'A playlist has that name'), (0, True, 'Could not rename playlist')])
def test_a_rename_refused_says_why_and_stays(fw, ui, taken, fail, said):
    sql = Renames(fw, taken, fail)
    renaming(fw, ui, b"Rock 'n' Roll").key(KEY_PLAY)
    assert any(b"filename='Rock ''n'' Roll'" in st for st in sql.log)  # quoted
    assert ui.toasts[-1] == said and ui.backs == 0


def test_an_empty_name_is_not_saved(fw, ui):
    sql = Renames(fw)
    renaming(fw, ui, b' ').key(KEY_PLAY)
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
def test_new_playlist_names_it_on_the_strip_and_the_return_key_makes_it(fw, ui, taken, fail, said, back):
    """New Playlist (audit2 05b): the strip, empty; Next at the name's end goes nowhere, the return key makes the
    playlist (ringnav_playlist_create), toasts and goes back; refused, it says why and stays."""
    Config(fw)
    sql = Creates(fw, taken, fail)
    fw.fn('playlist_new_open', None)()
    s = attached(fw, ui)
    assert s.ss.create == 1 and s.ss.text == b'' and 'New Playlist' in ui.text.values()
    s.type('Road Trip')
    assert s.ss.text == b'Road Trip'
    s.key(KEY_FWD_BTN)
    assert not [q for q in sql.log if q.startswith(b'insert')]
    s.key(KEY_RETURN, KEY_CENTER)
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
    s = Strip.__new__(Strip)
    s.attach(fw, ui)
    fw.fn('add_songs_search', None)()
    assert s.ss.pick == 1
    s.type('songs')
    assert s.titles() == []  # Glove's album, Love Songs, isn't asked
    s.erase(5)
    s.type('joy')
    assert s.titles() == ['Love Will Tear Us Apart', 'Atmosphere']  # by artist, and no artist row
    s.key(KEY_PLAY, KEY_CENTER)
    assert added == [0] and ui.toasts[-1].startswith('Added to') and s.ss.nmatch == 2
    assert ui.backs == 0 and not ui.played


# Return held on any letter-strip page: a card shows from SHOW_MS and the page closes at HOLD_MS; let go before, and
# the highlight goes to the return key.
SHOW_MS, HOLD_MS = 200, 600


def r_h(ui):
    """The top window's height, which the scrim covers."""
    return C.c_int.from_address(ui.top() + O['W_H']).value


@pytest.mark.parametrize('held_ms, closed, card', [(60, False, False), (400, False, True), (HOLD_MS, True, False)])
def test_return_held_shows_its_card_and_leaves_and_a_press_jumps_to_the_return_key(fw, ui, song, held_ms, closed, card):
    s = Strip(fw, ui, library(song))
    fw.var('g_backlight_status', C.c_ubyte).value = 1
    s.type('AB')
    latch = C.c_ubyte.from_address(O['RETURN_RELEASE_LATCH'])
    latch.value = 0
    start = s.now[0]
    s.down(ui.top(), KEY_RETURN)
    s.down(ui.top(), KEY_RETURN)  # a repeat while held changes nothing
    assert len(ui.pending('search_strip__ss_hold_tick')) == 1
    for at in range(0, held_ms, 33):
        s.now[0] += min(33, held_ms - at)
        if s.now[0] - start >= SHOW_MS and ui.pending('search_strip__ss_hold_tick'):
            ui.fire('search_strip__ss_hold_tick')
    assert bool(s.ss.held) == card
    if card:  # over the page, after stock painted it (ringnav_paint), the screen dimmed first
        from test_nav_painters import Canvas
        c = Canvas(fw)
        fw.fn('search_strip_overlay', None, C.c_void_p, C.c_void_p)(ui.top(), c.addr)
        assert [t for t, *_ in c.texts] == ['Keep holding to go back']
        assert [(r, ink) for r, ink in c.rects if r[2] == 375][:1] == [((0, 0, 375, r_h(ui)), 0x8c000000)]
    if not closed:
        s.key(KEY_RETURN, ms=0)  # the release: the highlight on the return key, nothing erased
        assert s.ss.stop == s.stops() - 1 and s.ss.text == b'AB' and ui.backs == 0
        assert not s.ss.held and not s.ss.hold_at
        return
    assert ui.backs == 1 and latch.value == 1  # closed; its release is stock's filter's to swallow
    ui.close(ui.top())
    s.now[0] += 100  # stock's long press, at 700 ms: the hold's, not the page under it
    assert fw.fn('compact_now_playing', C.c_int)() == 0 and ui.backs == 1 and not ui.went


def test_a_names_page_writes_the_name_in_inter_medium(fw, ui):
    """A name (New Playlist, Rename): in Inter Medium 22 (AWTK's 27), as the mock-up draws it."""
    Config(fw)
    fonts = []
    fw.mock('canvas_set_font', lambda c, face, px, *_: fonts.append((C.string_at(face), px & 0xffffffff)))
    s = renaming(fw, ui, b'Road')
    painted(fw, s)
    assert (b'inter-medium', 27) in fonts


# A stock text box (keyboard.c kb_strip_due) on the strip: the sets on its switcher; Play/Pause its own OK.
def boxed(fw, ui, text=b'', name='edit_passwd', window='kbwifipass_dialog', wm=0, **props):
    """A text box's edit, the input method's, in its window, opened on the strip. By default a Wi-Fi network's password
    (kbwifipass_dialog's edit_passwd); as stock's every box, its input_type reads 0 (text) whatever its page says. props
    are the edit's own (a card app's _secret). wm: the window manager's address, the windows' parent."""
    Config(fw)
    dialog = ui.new('window_create', wm, 0, 0, 375, 320)
    ui.name[dialog] = window
    holder = dialog
    if window == 'editwifi_dialog':  # stock's: the edit sits in a row of its own, at the dialog's top
        holder = ui.new('view', dialog, 65, 5, 310, 50)
        ui.name[holder] = 'list_item_edit'
    edit = ui.new('edit', holder, 0, 0, 200, 40)
    ui.name[edit] = name
    ui.props[(edit, 'input_type')] = 0
    for k, v in props.items():
        ui.props[(edit, k)] = v
    ui.text[edit] = text.decode()
    im = C.create_string_buffer(0x40)
    C.c_void_p.from_address(C.addressof(im) + O['IM_WIDGET']).value = edit
    fw.mock('input_method', lambda *_: C.addressof(im))
    kb = ui.new('window_create', wm, 0, 60, 375, 260)  # the input method's keyboard window, top
    keys = ui.new('pages', kb, 0, 40, 375, 221)
    assert fw.fn('search_strip_box', C.c_int, C.c_void_p, C.c_void_p, C.c_char_p)(kb, edit, b'Home Wi-Fi') == 1
    s = attached(fw, ui)
    s.im, s.edit, s.kb, s.keys, s.dialog, s.holder = im, edit, kb, keys, dialog, holder
    return s


def test_a_password_box_types_its_sets_and_play_is_its_ok(fw, ui):
    s = boxed(fw, ui)
    assert s.ss.secret and s.ss.set == LOWER and s.ss.stop == 1 and 'Home Wi-Fi' in ui.text.values()
    assert s.ss.page == s.kb and s.keys in ui.hidden  # on the keyboard's window, its keys hidden: no focus moves
    s.type('bA!b')
    assert s.ss.text == b'bA!b' and s.ss.set == LOWER
    texts = [t for t, *_ in painted(fw, s).texts]
    assert 'Password' in texts and 'abc' in texts and '•••b' in texts  # the last typed shows
    assert not [t for t in texts if t.startswith('Press ')]  # no keyboard hints (the user's, on the mock-up)
    s.now[0] += 1000
    assert '••••' in [t for t, *_ in painted(fw, s).texts]  # a second on: dots alone
    s.key(KEY_BACK_BTN, KEY_FWD_BTN, KEY_FWD_BTN)
    assert not fw.calls('input_method_dispatch_action')  # Next moves the cursor, never the OK
    s.key(KEY_PLAY)
    assert ui.text[s.edit] == 'bA!b' and not ui.backs
    assert [a[0] for _, a in fw.calls('input_method_dispatch_action')] == [C.addressof(s.im)]
    assert [a[1] for _, a in fw.calls('input_method_request')] == [0]  # the OK left it open: closed, as Done does


def test_the_last_character_typed_turns_to_a_dot_after_a_second_unprompted(fw, ui):
    """The peek ends on its own: a timer a second after the character repaints, with no key pressed."""
    s = boxed(fw, ui)
    s.type('ab')
    (t, ms), = ui.pending('search_strip__ss_peek_due')
    assert ms == 1000
    s.now[0] += 1000
    ui.fire('search_strip__ss_peek_due')
    assert [a[0] for _, a in fw.calls('widget_invalidate_force')][-1] == s.ss.sheet
    assert '••' in [t for t, *_ in painted(fw, s).texts]


def test_the_peek_is_the_character_just_typed_alone(fw, ui):
    """Previous within the second moves the cursor: the character now before it stays a dot."""
    s = boxed(fw, ui)
    s.type('ab')
    s.key(KEY_BACK_BTN)
    assert s.ss.cur == 1 and '••' in [t for t, *_ in painted(fw, s).texts]


@pytest.mark.parametrize('password', [1, 0])
def test_add_network_s_dialog_is_a_password_only_when_stock_opened_it_for_one(fw, ui, password):
    """Add Network's rows both open editwifi_dialog, one edit named edit; its init keeps which in EDITWIFI_PASSWORD
    (1 the password row's, 0 the network name's): dots for the one, the name in plain for the other."""
    mode = C.c_int.from_address(O['EDITWIFI_PASSWORD'])
    mode.value = password
    s = boxed(fw, ui, b'Home', 'edit', 'editwifi_dialog')
    s.type('x')
    s.now[0] += 1000
    texts = [t for t, *_ in painted(fw, s).texts]
    mode.value = 0  # stock's memory outlives the test
    if password:
        assert s.ss.secret and '•••••' in texts and 'Password' in texts
    else:
        assert not s.ss.secret and 'Homex' in texts and 'Network Name' in texts and 'Password' not in texts


def test_add_network_s_own_text_box_is_hidden_while_the_strip_types_for_it(fw, ui):
    """editwifi_dialog's edit sits in the status row; the strip's field shows the text, so stock's box is covered, and is
    shown again when the keyboard goes with the dialog still up (an OK that leaves it open)."""
    fw.mock('window_manager', lambda *_: 1)  # a real one, whose children the C looks through for a live window
    s = boxed(fw, ui, b'Home', 'edit', 'editwifi_dialog', wm=1)
    assert s.holder in ui.hidden and s.edit not in ui.hidden
    ui.destroy(s.kb)
    assert s.holder not in ui.hidden


def test_a_box_s_row_is_left_alone_when_its_dialog_went_first(fw, ui):
    """An OK that closes the dialog frees the row before the keyboard goes: nothing is shown through a dead widget."""
    fw.mock('window_manager', lambda *_: 1)
    s = boxed(fw, ui, b'Home', 'edit', 'editwifi_dialog', wm=1)
    ui.destroy(s.dialog)
    ui.destroy(s.kb)
    assert not [c for c in fw.calls('widget_set_visible') if c[1][0] == s.holder and c[1][1] & 0xff]


def test_return_held_on_add_network_s_box_closes_its_dialog_too(fw, ui):
    """The dialog is only that box (stock closes it on its next Return): left up it shows the box alone, shifted to
    the top of the screen, so the hold quits it (a modal dialog), a loop pass after the keyboard's close."""
    closed: list = []
    fw.mock('dialog_quit', lambda w, *_: closed.append(w) or ui.destroy(w))
    fw.mock('window_manager', lambda *_: 1)
    s = boxed(fw, ui, b'abc', 'edit', 'editwifi_dialog', wm=1)
    s.down(ui.top(), KEY_RETURN)
    s.now[0] += HOLD_MS
    ui.fire('search_strip__ss_hold_tick')
    assert [a[1] for _, a in fw.calls('input_method_request')] == [0]
    assert closed == []  # not inside the keyboard's own close
    ui.fire('search_strip__ss_quit_due')
    assert closed == [s.dialog]


def test_a_card_app_s_password_box_shows_dots_and_is_done_not_joined(fw, ui):
    """A box the payload marks _secret (addon.c, a card app's password field): dots, under its row's label."""
    s = boxed(fw, ui, b'', '', 'addon_page', _secret=1, _strip_label='Password')
    s.type('pw')
    s.now[0] += 1000
    texts = [t for t, *_ in painted(fw, s).texts]
    assert '••' in texts and 'Password' in texts


def test_a_password_text_of_the_payload_s_own_shows_dots(fw, ui):
    """search_strip_secret, then search_strip_text (the Guest Play hotspot's password): dots, the last character
    for a second, Play/Pause saves; the next text is plain again."""
    Config(fw)
    taken = []
    done = DONE(lambda t: taken.append(t) or None)
    fw.fn('search_strip_secret', None)()
    fw.fn('search_strip_text', None, C.c_char_p, C.c_char_p, C.c_int, DONE)(b'Hotspot', b'otter-42', 63, done)
    s = attached(fw, ui)
    texts = [t for t, *_ in painted(fw, s).texts]
    assert s.ss.secret and '••••••••' in texts and not any('otter' in t for t in texts)
    assert 'Password' in texts
    s.type('x')
    assert '••••••••x' in [t for t, *_ in painted(fw, s).texts]
    s.key(KEY_PLAY)
    assert taken == [b'otter-42x']
    ui.close(ui.top())
    fw.fn('search_strip_text', None, C.c_char_p, C.c_char_p, C.c_int, DONE)(b'Name', b'Q2', 31, done)
    assert attached(fw, ui).ss.secret == 0


def test_a_number_box_opens_on_digits_and_a_text_one_keeps_its_text(fw, ui):
    assert boxed(fw, ui, b'1000', 'edit').ss.set == DIGITS


def test_return_held_on_a_box_closes_it_and_its_keyboard(fw, ui):
    s = boxed(fw, ui, b'abc', 'edit')
    s.down(ui.top(), KEY_RETURN)
    s.now[0] += HOLD_MS
    ui.fire('search_strip__ss_hold_tick')
    assert not ui.backs and not fw.calls('input_method_dispatch_action')
    assert [a[1] for _, a in fw.calls('input_method_request')] == [0]  # the keyboard closed with it


# The text where it lives (docs/oled-design.md#text-entry, the mock-up's value()): its field, the cursor kept 10 px in
# while the text overflows, fades only at an edge that cuts the text, and in the text's own alpha.
KEEP, EDGE, BLINK_MS = 10, 16, 530
QUERY_X0, QUERY_X1 = 48, 351  # Search's query field on the sheet: a character is 10 px on the test canvas


def clipped(fw, s):
    """The sheet painted, each text with the clip it was drawn under and its alpha."""
    from test_nav_painters import Canvas
    c = Canvas(fw)
    rows: list = []
    setattr(c, 'clipped', rows)  # read back as c.clipped

    def draw(cv, t, n, r, *_):
        c.draw_text(cv, t, n, r)
        rows.append((c.texts[-1][0], tuple(c.clip), c.texts[-1][2] >> 24, c.texts[-1][1]))
        return 0
    fw.mock('canvas_draw_text_in_rect', draw)
    fw.fn('search_strip_paint', None, C.c_void_p, C.c_void_p)(s.ss.sheet, c.addr)
    return c


def cursor(c, h=25):
    """The cursor's x: the one 2 px column the field painted (none while the blink hides it)."""
    xs = [r[0] for r, _ in c.rects if r[2:] == (2, h)]
    return xs[0] if xs else None


def model(n, cur, scroll, x0=QUERY_X0, x1=QUERY_X1, cw=10):
    """strip.html's value(): the text's left and the scroll kept, for a text of n characters."""
    room, full, cx = x1 - x0, n * cw + 3, cur * cw
    if full <= room:
        return x0, 0
    scroll = min(max(scroll, cx - room + KEEP), max(0, cx - KEEP))
    scroll = max(0, min(scroll, full - room))
    return x0 - scroll, scroll


def test_an_overflowing_text_scrolls_as_little_as_keeps_the_cursor_10_px_in(fw, ui, song):
    """Previous from the end of a text wider than its field walks the cursor left in place until it is 10 px from
    the field's left, then the text scrolls under it; Next walks it back. Each step lands where the mock-up's
    script puts it."""
    s = Strip(fw, ui, library(song))
    text = b'abcdefghij' * 4
    s.ss.text, s.ss.cur = text, len(text)
    scroll, seen = 0, []
    for key in [None] + [KEY_BACK_BTN] * 40 + [KEY_FWD_BTN] * 40:
        if key:
            s.key(key)
        left, scroll = model(len(text), s.ss.cur, scroll)
        x = cursor(clipped(fw, s))
        assert x == left + (s.ss.cur * 10 + 1 if s.ss.cur else 0)
        assert s.ss.scroll == scroll
        if 0 < scroll < len(text) * 10 + 3 - (QUERY_X1 - QUERY_X0):  # text hidden past both edges
            assert QUERY_X0 + KEEP <= x - 1 <= QUERY_X1 - KEEP  # the column after the cursor's character
        seen.append(scroll)
    assert 0 in seen and max(seen) == 100 and len(set(seen)) > 10  # it scrolled all the way, a step at a time


def fades(c, text):
    """The slices of text drawn below full alpha: (clip's left, its right, alpha)."""
    return [(clip[0], clip[0] + clip[2], a) for t, clip, a, _ in c.clipped if t == text and a < 0xff]


def test_the_text_fades_only_at_an_edge_that_cuts_it_and_in_its_own_alpha(fw, ui, song):
    s = Strip(fw, ui, library(song))
    s.ss.text, s.ss.cur = b'LOVE', 4  # fits: drawn once, whole
    c = clipped(fw, s)
    assert [a for t, _, a, _ in c.clipped if t == 'LOVE'] == [0xff] and not fades(c, 'LOVE')
    text = 'abcdefghij' * 4
    s.ss.text, s.ss.cur = text.encode(), 40  # the end in view: cut at the left alone
    c = clipped(fw, s)
    cut = fades(c, text)
    assert cut and all(QUERY_X0 <= l and r <= QUERY_X0 + EDGE for l, r, _ in cut)
    assert [a for _, _, a in cut] == sorted(a for _, _, a in cut)  # fading out toward the edge
    assert cut[0][2] < 0x20 and cut[-1][2] > 0xd0
    full = [clip for t, clip, a, _ in c.clipped if t == text and a == 0xff]
    assert full == [(QUERY_X0 + EDGE, full[0][1], QUERY_X1 - QUERY_X0 - EDGE, full[0][3])]  # the rest to the edge
    # nothing laid over the field: the one fill in its band is the cursor's
    assert [r for r, _ in c.rects if r[1] < 32 and r[1] + r[3] > 0 and r[2] != 2] == []
    s.ss.cur, s.ss.scroll = 0, 0  # the start in view: cut at the right alone
    cut = fades(clipped(fw, s), text)
    assert cut and all(QUERY_X1 - EDGE <= l and r <= QUERY_X1 for l, r, _ in cut)
    s.ss.cur, s.ss.scroll = 20, 50  # the middle, scrolled: cut at both
    c = clipped(fw, s)
    cut = fades(c, text)
    assert {l < QUERY_X0 + EDGE for l, _, _ in cut} == {True, False}
    assert all(t != text or QUERY_X0 <= clip[0] and clip[0] + clip[2] <= QUERY_X1 for t, clip, *_ in c.clipped)


def test_a_password_s_dots_scroll_and_fade_as_any_text_and_show_its_last_character_a_second(fw, ui):
    """A masked field's text is its dots, the character just typed among them for SS_PEEK_MS, in the same field."""
    s = boxed(fw, ui)
    s.ss.text, s.ss.cur = b'x' * 30, 30
    s.type('q')
    c = clipped(fw, s)
    shown = [t for t, *_ in c.clipped if t.startswith('•')]
    assert shown and shown[0] == '•' * 30 + 'q' and fades(c, shown[0])  # cut at the left, the peek at the end
    s.now[0] += 1000
    assert [t for t, *_ in clipped(fw, s).clipped if t.startswith('•')][0] == '•' * 31


def test_the_cursor_blinks_on_its_own_and_starts_lit_after_each_key(fw, ui, song):
    s = Strip(fw, ui, library(song))
    s.type('L')
    assert cursor(clipped(fw, s)) is not None
    (_, ms), = ui.pending('search_strip__ss_blink_due')
    assert ms == BLINK_MS
    s.now[0] += BLINK_MS
    ui.fire('search_strip__ss_blink_due')  # repaints the field alone, and goes on
    (w, rect), = [a[:2] for _, a in fw.calls('widget_invalidate_force')][-1:]
    assert w == s.ss.sheet and rect
    assert cursor(clipped(fw, s)) is None and ui.pending('search_strip__ss_blink_due')
    s.key(KEY_FWD_BTN)  # a key: lit again
    assert cursor(clipped(fw, s)) is not None
    s.enter()  # the results have the wheel: no cursor, and the blink stops
    ui.fire('search_strip__ss_blink_due')
    assert not ui.pending('search_strip__ss_blink_due')


def test_under_reduce_motion_the_cursor_is_steady(fw, ui, song):
    from test_ui_kit import reduce_motion
    reduce_motion(fw, 1)  # read once, before the strip opens
    s = Strip(fw, ui, library(song))
    s.type('L')
    assert not ui.pending('search_strip__ss_blink_due')
    s.now[0] += BLINK_MS
    assert cursor(clipped(fw, s)) is not None


def under_page(fw, ui, st, row_y=58 + 32, row_h=32):
    """The page a text is typed for: a list whose selected row is at screen y row_y (navigation's selection)."""
    Config(fw)
    page = ui.new('window_create', 0, 0, 0, 375, 320)
    rows = ui.new('view', page, 0, 58, 375, 262)
    st('sel_w', C.c_void_p).value = rows
    st('sel_y').value, st('sel_h').value = row_y - 58 + row_h // 2, row_h
    return page


def text_on_strip(fw, ui, text, label=None, note=None, secret=False):
    done = DONE(lambda t: None)
    ui.done = done  # kept alive
    if note:
        fw.fn('search_strip_note', None, C.c_char_p)(note)
    if label:
        fw.fn('search_strip_label', None, C.c_char_p)(label)
    if secret:
        fw.fn('search_strip_secret', None)()
    fw.fn('search_strip_text', None, C.c_char_p, C.c_char_p, C.c_int, DONE)(b'Bluetooth', text, 30, done)
    return attached(fw, ui)


def sheet_y(ui, s):
    """The sheet's top on the screen."""
    y, w = 0, s.ss.sheet
    while w:
        y += struct.unpack_from('<i', ui.blocks[w], 4)[0]
        w = ui.parent(w)
    return y


def test_a_named_row_s_text_is_a_cell_in_the_row_the_wheel_was_on_with_its_note_under_it(fw, ui, st):
    """Bluetooth's Name: the row's own place on the page under, an accent-lined box, the label at its left in Text
    Secondary, the name right-aligned with its cursor at its end, and who sees it 24 px under the row."""
    under_page(fw, ui, st)
    s = text_on_strip(fw, ui, b'Q2 Muse', b'Name', b'Phones and cars see this name')
    y = 58 + 32 - sheet_y(ui, s)
    assert (s.ss.form, s.ss.row_y, s.ss.row_h) == (2, y, 32)
    c = clipped(fw, s)
    boxes = [b for b, _, r in c.boxes if r in (8, 6)]
    assert boxes[:2] == [(16, y, 343, 32), (18, y + 2, 339, 28)]  # the accent line, then the box in it
    texts = {t: r for t, r, _ in c.texts}
    assert texts['Name'][0] == 24
    assert texts['Q2 Muse'][0] == 347 - 73  # right-aligned: 70 px of text and the cursor's 3
    assert cursor(c) == 347 - 73 + 70 + 1  # after the text's last character
    assert texts['Phones and cars see this name'][1] + 16 == y + 32 + 24  # centred 24 px under the row
    assert not [t for t in texts if t.startswith('Press ')]


def test_a_row_without_a_label_takes_its_whole_width_from_the_left(fw, ui, st):
    under_page(fw, ui, st, row_y=58 + 64)
    s = text_on_strip(fw, ui, b'Morning Ru')
    assert s.ss.form == 1
    assert {t: r for t, r, _ in clipped(fw, s).texts}['Morning Ru'][0] == 24


@pytest.mark.parametrize('where', ['gone', 'low'])
def test_a_row_not_in_sight_or_too_low_types_in_a_field_under_its_label(fw, ui, st, where):
    """With the selection not on the page under (the page's own row gone), or a row the strip would cover, the text
    goes in a field of its own: the label over a raised pill, the text in Inter Medium 22 inside it."""
    under_page(fw, ui, st, row_y=58 + 190)
    if where == 'gone':
        st('sel_w', C.c_void_p).value = ui.new('view', 0, 0, 0, 10, 10)
    s = text_on_strip(fw, ui, b'Gray', b'Name')
    assert s.ss.form == 3
    c = clipped(fw, s)
    assert ((40, 48, 295, 44), 12) in [(b, r) for b, _, r in c.boxes]
    texts = {t: r for t, r, _ in c.texts}
    assert texts['Name'][0] == 40 and texts['Gray'][0] == 56


def test_a_password_with_no_label_is_a_field_under_password(fw, ui, st):
    under_page(fw, ui, st)
    s = text_on_strip(fw, ui, b'otter-42', secret=True)
    assert s.ss.form == 3 and 'Password' in [t for t, *_ in clipped(fw, s).texts]


def test_a_search_box_types_on_the_query_line_its_tips_the_placeholder(fw, ui):
    """A box no page names (a search box: Internet Radio's, a card app's Search): the query line, as Search's."""
    s = boxed(fw, ui, b'', 'edit', 'radio_page', tips='Search stations')
    assert s.ss.form == 0
    c = clipped(fw, s)
    assert [r[0] for t, r, _ in c.texts if t == 'Search stations'] == [QUERY_X0]
    assert cursor(c) == QUERY_X0


def test_a_box_its_page_names_is_a_cell_in_its_row(fw, ui, st):
    """An EQ preset's name: the edit's _strip_label names the row the selection is on; the row's box is painted over
    the page as it was, its edit hidden for the picture and shown again."""
    page = under_page(fw, ui, st)
    snaps = []
    fw.mock('canvas_offline_create', lambda *a: snaps.append(set(ui.hidden)) and 0)
    Config(fw)
    edit = ui.new('edit', page, 12, 2, 351, 26)
    ui.name[edit] = 'edit'
    ui.props[(edit, '_strip_label')] = 'Name'
    ui.text[edit] = 'My Car EQ'
    im = C.create_string_buffer(0x40)
    C.c_void_p.from_address(C.addressof(im) + O['IM_WIDGET']).value = edit
    fw.mock('input_method', lambda *_: C.addressof(im))
    kb = ui.new('window_create', 0, 0, 60, 375, 260)
    assert fw.fn('search_strip_box', C.c_int, C.c_void_p, C.c_void_p, C.c_char_p)(kb, edit, b'Save Preset') == 1
    s = attached(fw, ui)
    assert s.ss.form == 2 and snaps == [{edit} | (ui.hidden - {edit})] and edit not in ui.hidden
    texts = {t: r for t, r, _ in clipped(fw, s).texts}
    assert texts['Name'][0] == 24 and 'My Car EQ' in texts


# Touch in the text (the mock-up's caretAt() and loupe(), docs/oled-design.md#text-entry): a tap puts the cursor at
# the nearest character boundary; a hold or a drag shows a 1.6x loupe over the cursor, the cursor following the
# finger; a finger at an edge that cuts the text nudges it a character at a time.
LOUPE_W, LOUPE_H, LOUPE_R, LOUPE_HOLD_MS, LOUPE_IN_MS, NUDGE_MS = 104, 46, 14, 250, 100, 150


def press(s, x, y):
    s.feed(EVT_POINTER_DOWN, x, y, s.ss.sheet)


def drag(s, x, y):
    s.feed(EVT_POINTER_MOVE_BEFORE, x, y, s.ss.page)


def lift(s, x, y):
    s.feed(EVT_POINTER_UP_BEFORE, x, y, s.ss.page)


def win_y(ui):
    return struct.unpack_from('<i', ui.blocks[ui.top()], 4)[0]


def loupe(fw, ui):
    """The overlay over the top window (ringnav_paint's search_strip_overlay): the loupe's box, its texts with the
    clip each was drawn under, the faces' sizes set, and its cursor's column."""
    from test_nav_painters import Canvas
    c = Canvas(fw)
    fonts: list = []
    rows: list = []
    setattr(c, 'fonts', fonts)
    setattr(c, 'clipped', rows)
    fw.mock('canvas_set_font', lambda cv, face, px, *_: fonts.append(px & 0xffff) or 0)

    def draw(cv, t, n, r, *_):
        c.draw_text(cv, t, n, r)
        rows.append((c.texts[-1][0], tuple(c.clip), c.texts[-1][2] >> 24, c.texts[-1][1]))
        return 0
    fw.mock('canvas_draw_text_in_rect', draw)
    fw.fn('search_strip_overlay', None, C.c_void_p, C.c_void_p)(ui.top(), c.addr)
    return c


def loupe_box(c):
    """The loupe's outline box (its 1 px line, then the lens 1 px in)."""
    boxes = [b for b, _, r in c.boxes if r == LOUPE_R]
    return boxes[0] if boxes else None


def test_a_tap_in_the_query_puts_the_cursor_at_the_nearest_boundary(fw, ui, song):
    s = Strip(fw, ui, library(song))
    s.ss.text, s.ss.cur = b'LOVE WILL', 9
    clipped(fw, s)  # painted: where each character lies
    for x, cur in [(QUERY_X0 + 23, 2), (QUERY_X0 + 27, 3), (QUERY_X0 + 300, 9), (QUERY_X0 - 6, 0), (QUERY_X0 + 4, 0)]:
        s.touch(x, 16)
        assert s.ss.cur == cur, x
        clipped(fw, s)
    assert s.ss.text == b'LOVE WILL' and s.ss.stop == 1  # nothing typed, the strip's highlight where it was
    assert not ui.pending('search_strip__ss_loupe_due') and not s.ss.loupe  # a tap: no loupe
    s.ss.text, s.ss.cur = b'LO', 2
    s.type('V')  # LOV, matched
    s.enter()  # the results have the wheel: a tap in the text gives it back to the strip, the cursor there
    assert s.ss.focus == ON_LIST
    clipped(fw, s)
    s.touch(QUERY_X0 + 21, 16)
    assert s.ss.focus == ON_STRIP and s.ss.cur == 2
    assert cursor(clipped(fw, s)) == QUERY_X0 + 21  # lit at once, after the second character


def test_a_tap_below_the_text_is_not_in_it(fw, ui, song):
    s = Strip(fw, ui, library(song))
    s.ss.text, s.ss.cur = b'LOVE', 4
    clipped(fw, s)
    s.touch(QUERY_X0 + 15, 16 + 23)  # under the field's band
    assert s.ss.cur == 4


def test_a_name_s_multibyte_characters_are_whole_to_the_cursor(fw, ui, st):
    """A name typed elsewhere may hold any character (Rename's prefilled "Café Tacvba"): the cursor stands after
    whole characters, painted after as many as it follows, a tap lands on a character's boundary, and Previous, Next
    and an erase step over a character, never into one."""
    under_page(fw, ui, st)
    s = renaming(fw, ui, 'Café Tacvba'.encode())
    assert s.ss.cur == 12  # bytes: é is two
    c = clipped(fw, s)
    left = {t: r for t, r, _ in c.texts}['Café Tacvba'][0]
    assert cursor(c) == left + 11 * 10 + 1  # after its 11 characters
    s.touch(left + 39, s.ss.row_y + s.ss.row_h // 2)  # nearest the boundary after é
    assert s.ss.cur == 5
    s.touch(left + 31, s.ss.row_y + s.ss.row_h // 2)  # ... before it
    assert s.ss.cur == 3
    s.key(KEY_FWD_BTN)
    assert s.ss.cur == 5
    s.key(KEY_BACK_BTN)
    assert s.ss.cur == 3
    s.key(KEY_FWD_BTN)
    s.erase(1)
    assert s.ss.text == b'Caf Tacvba' and s.ss.cur == 3


def test_a_tap_among_a_password_s_dots_is_a_character_each(fw, ui):
    s = boxed(fw, ui, b'otter-42')
    c = clipped(fw, s)
    left = [r for t, r, _ in c.texts if t.startswith('•')][0][0]
    s.touch(left + 31, s.ss.fy + 16)
    assert s.ss.cur == 3


@pytest.mark.parametrize('form', ['query', 'row', 'cell', 'field'])
def test_a_hold_shows_the_loupe_over_the_cursor_at_1_6x_in_every_form(fw, ui, st, song, form):
    """A finger held in the text: after 250 ms a lens 104 by 46 px with 14 px corners, its foot the text's size and
    10 px over the line, centred on the cursor, showing the text there at 1.6 times its size with a steady cursor in
    its middle; it goes with the finger."""
    if form == 'query':
        s = Strip(fw, ui, library(song))
        s.ss.text, s.ss.cur = b'LOVE WILL TEAR', 14
        px, design = 24, 20
    else:
        under_page(fw, ui, st, row_y=58 + (190 if form == 'field' else 32))
        s = text_on_strip(fw, ui, b'LOVE WILL TEAR', b'Name' if form == 'cell' else None)
        px, design = (27, 22) if form == 'field' else (24, 20)
    assert s.ss.form == ['query', 'row', 'cell', 'field'].index(form)
    c = clipped(fw, s)
    left = {t: r for t, r, _ in c.texts}['LOVE WILL TEAR'][0]
    cy = s.ss.fy + 16
    press(s, left + 50, cy)
    assert s.ss.cur == 5 and not s.ss.loupe and loupe_box(loupe(fw, ui)) is None
    (_, ms), = ui.pending('search_strip__ss_loupe_due')
    assert ms == LOUPE_HOLD_MS
    s.now[0] += LOUPE_HOLD_MS
    ui.fire('search_strip__ss_loupe_due')
    s.now[0] += LOUPE_IN_MS
    while ui.pending('search_strip__ss_loupe_due'):
        ui.fire('search_strip__ss_loupe_due')
    clipped(fw, s)
    c = loupe(fw, ui)
    sy, wy = sheet_y(ui, s), win_y(ui)
    centre = left + 50
    top = max(4, sy + cy - design - LOUPE_H - 10)
    assert loupe_box(c) == (centre - LOUPE_W // 2, top - wy, LOUPE_W, LOUPE_H)
    assert round(px * 1.6) in c.fonts
    mid = top - wy + LOUPE_H // 2
    h = 40 if px == 24 else 42  # the cursor's 25 or 26 px at 1.6x
    assert [r for r, _ in c.rects if r[2] == 3] == [(centre - 1, mid - h // 2, 3, h)]
    seen = [(clip, r) for t, clip, a, r in c.clipped if t == 'LOVE WILL TEAR']
    assert seen and all(centre - LOUPE_W // 2 + 4 <= clip[0] and clip[0] + clip[2] <= centre + LOUPE_W // 2 - 4
                        and top - wy < clip[1] and clip[1] + clip[3] < top - wy + LOUPE_H for clip, _ in seen)
    assert {r[0] for _, r in seen} == {centre - 5 * 10}  # the text's left: five characters before the cursor
    drag(s, left + 81, cy)  # the finger moves: the cursor and the lens with it
    assert s.ss.cur == 8
    clipped(fw, s)
    assert loupe_box(loupe(fw, ui))[0] == left + 80 - LOUPE_W // 2
    lift(s, left + 81, cy)
    assert s.ss.cur == 8 and not s.ss.loupe and not ui.pending('search_strip__ss_loupe_due')
    assert loupe_box(loupe(fw, ui)) is None


def test_the_loupe_keeps_4_px_inside_the_screen(fw, ui, song):
    s = Strip(fw, ui, library(song))
    s.ss.text, s.ss.cur = b'ab', 0
    clipped(fw, s)
    press(s, QUERY_X0, 16)
    drag(s, QUERY_X0 - 9, 16)  # a drag shows it at once
    assert s.ss.loupe
    s.now[0] += LOUPE_IN_MS
    while ui.pending('search_strip__ss_loupe_due'):
        ui.fire('search_strip__ss_loupe_due')
    box = loupe_box(loupe(fw, ui))
    assert box[0] == 4 and box[1] + win_y(ui) == 4  # the cursor at x 48: the lens at the screen's left, not past it
    lift(s, QUERY_X0 - 9, 16)
    s.ss.text, s.ss.cur = b'abcdefghij' * 4, 40
    clipped(fw, s)
    x = QUERY_X1 - KEEP - 1
    press(s, x, 16)
    drag(s, x - 9, 16)
    s.now[0] += LOUPE_IN_MS
    while ui.pending('search_strip__ss_loupe_due'):
        ui.fire('search_strip__ss_loupe_due')
    clipped(fw, s)
    box = loupe_box(loupe(fw, ui))
    assert box[0] + LOUPE_W == 375 - 4  # at the right


def test_the_loupe_grows_in_and_under_reduce_motion_shows_at_once(fw, ui, song):
    s = Strip(fw, ui, library(song))
    s.ss.text, s.ss.cur = b'LOVE WILL', 9
    clipped(fw, s)
    press(s, QUERY_X0 + 40, 16)
    s.now[0] += LOUPE_HOLD_MS
    ui.fire('search_strip__ss_loupe_due')
    small = loupe_box(loupe(fw, ui))
    assert small[2] < LOUPE_W and small[3] < LOUPE_H
    (_, ms), = ui.pending('search_strip__ss_loupe_due')  # its frames
    assert ms == 33
    s.now[0] += LOUPE_IN_MS
    ui.fire('search_strip__ss_loupe_due')
    assert loupe_box(loupe(fw, ui))[2:] == (LOUPE_W, LOUPE_H) and not ui.pending('search_strip__ss_loupe_due')
    lift(s, QUERY_X0 + 40, 16)


def test_under_reduce_motion_the_loupe_shows_whole_at_once(fw, ui, song):
    from test_ui_kit import reduce_motion
    reduce_motion(fw, 1)
    s = Strip(fw, ui, library(song))
    s.ss.text, s.ss.cur = b'LOVE WILL', 9
    clipped(fw, s)
    press(s, QUERY_X0 + 40, 16)
    s.now[0] += LOUPE_HOLD_MS
    ui.fire('search_strip__ss_loupe_due')
    assert loupe_box(loupe(fw, ui))[2:] == (LOUPE_W, LOUPE_H) and not ui.pending('search_strip__ss_loupe_due')


@pytest.mark.parametrize('side', ['left', 'right'])
def test_a_finger_at_an_edge_that_cuts_the_text_nudges_it_a_character_each_150_ms(fw, ui, song, side):
    """Held at the field's edge, where the text is cut, the cursor steps a character toward it at once and then every
    150 ms, the text scrolling under it, until that end of the text is in view; then the cursor follows the finger
    again. The release leaves it there."""
    s = Strip(fw, ui, library(song))
    text = b'abcdefghij' * 4
    s.ss.text, s.ss.cur = text, (40 if side == 'left' else 0)
    clipped(fw, s)
    mid = (QUERY_X0 + QUERY_X1) // 2
    press(s, mid, 16)
    start = s.ss.cur
    edge = QUERY_X0 + 3 if side == 'left' else QUERY_X1 - 3
    step = -1 if side == 'left' else 1
    drag(s, edge, 16)  # the cursor to the finger, and a step past it at once
    assert s.ss.loupe and (s.ss.cur < start if side == 'left' else s.ss.cur > start)
    steps = 1
    while ui.pending('search_strip__ss_nudge_due'):
        (_, ms), = ui.pending('search_strip__ss_nudge_due')
        assert ms == NUDGE_MS
        clipped(fw, s)  # each step painted, the text scrolled under the cursor
        before = s.ss.cur
        s.now[0] += NUDGE_MS
        ui.fire('search_strip__ss_nudge_due')
        if ui.pending('search_strip__ss_nudge_due'):
            assert s.ss.cur == before + step
            steps += 1
        assert steps < 60
    assert s.ss.scroll == (0 if side == 'left' else 100)  # that end of the text in view
    c = clipped(fw, s)
    assert abs(cursor(c) - edge) <= 6  # the cursor nearest the finger
    lift(s, edge, 16)
    after = s.ss.cur
    assert not s.ss.loupe and not ui.pending('search_strip__ss_nudge_due') and s.ss.cur == after
    assert s.ss.text == text


def test_a_finger_at_an_edge_of_a_text_that_fits_follows_with_no_nudge(fw, ui, song):
    s = Strip(fw, ui, library(song))
    s.ss.text, s.ss.cur = b'LOVE', 4
    clipped(fw, s)
    press(s, QUERY_X0 + 20, 16)
    drag(s, QUERY_X0 + 2, 16)
    assert s.ss.cur == 0 and not ui.pending('search_strip__ss_nudge_due')
    lift(s, QUERY_X0 + 2, 16)


def test_closing_the_page_mid_drag_stops_the_loupe_and_nudge(fw, ui, song):
    s = Strip(fw, ui, library(song))
    s.ss.text, s.ss.cur = b'abcdefghij' * 4, 40
    clipped(fw, s)
    press(s, 200, 16)
    drag(s, QUERY_X0 + 3, 16)
    assert ui.pending('search_strip__ss_nudge_due') or ui.pending('search_strip__ss_loupe_due')
    ui.close(ui.top())
    assert not ui.pending('search_strip__ss_nudge_due') and not ui.pending('search_strip__ss_loupe_due')
