"""search_strip.c: Search as an iPod classic's letter strip under the OLED theme (list.entry.ipod): the wheel
walks the strip, Centre types, Next and Previous type a space and delete, the songs filter live, and Return moves
to the results or closes."""
import ctypes as C
import struct

import pytest

from conftest import O
from test_nav_library_pages import Config, Library, Songs, Ui, field

KEY_RETURN, KEY_CENTER, KEY_PREV, KEY_NEXT, KEY_PLAY, KEY_FWD_BTN, KEY_BACK_BTN = (
    O[k] for k in ('KEY_RETURN', 'KEY_CENTER', 'KEY_PREV', 'KEY_NEXT', 'KEY_PLAY', 'KEY_FWD_BTN', 'KEY_BACK_BTN'))
LIBRARY, SPACE, DELETE, ENTRIES = 0xf001, 36, 37, 38
EVT_POINTER_DOWN, EVT_POINTER_UP_BEFORE, EVENT_X = O['EVT_POINTER_DOWN'], O['EVT_POINTER_UP_BEFORE'], O['EVENT_X']
STRIP_Y, CELL_X, STEP = 158, 108, 17  # the strip's top on the page, its first letter cell, a cell's width
OLED = b'3'


class Ss(C.Structure):
    _fields_ = [*((k, C.c_void_p) for k in 'page sheet songs match kind'.split()),
                *((k, C.c_int) for k in 'nmatch letter focus sel top songs_at'.split()),
                *((k, C.c_short) for k in 'down_x down_y pressed'.split()), ('text', C.c_char * 49)]


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

    def touch(self, x0, y0, x1=None, y1=None):
        """A press at x0, y0 on the sheet and its release at x1, y1 (the press's place: a tap), in the page's
        coordinates."""
        for evt, x, y, w in ((EVT_POINTER_DOWN, x0, y0, self.ss.sheet),
                             (EVT_POINTER_UP_BEFORE, x0 if x1 is None else x1, y0 if y1 is None else y1, self.ss.page)):
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


def test_results_filter_as_each_letter_goes_in(fw, ui, song):
    s = Strip(fw, ui, library(song))
    s.type('lo')
    assert s.ss.text == b'lo' and s.titles() == ['album: Love Songs', 'Lovely Day', 'Love Will Tear Us Apart', 'Glove']
    s.type('ve s')  # a song's album matches too
    assert s.ss.text == b'love s' and s.titles() == ['album: Love Songs', 'Glove']


def test_previous_deletes_and_next_types_a_space(fw, ui, song):
    s = Strip(fw, ui, library(song))
    s.type('joyx')
    assert s.titles() == []
    s.key(KEY_BACK_BTN)
    assert s.ss.text == b'joy' and s.titles() == ['artist: Joy Division', 'Love Will Tear Us Apart', 'Atmosphere']
    s.key(KEY_FWD_BTN, KEY_FWD_BTN)  # one space, never two in a row
    assert s.ss.text == b'joy '


def test_the_strip_s_own_space_and_delete_entries(fw, ui, song):
    s = Strip(fw, ui, library(song))
    s.key(KEY_CENTER)  # a
    s.key(KEY_PREV, KEY_CENTER)  # delete
    assert s.ss.text == b''
    s.key(KEY_PREV, KEY_CENTER)  # a space first is not typed
    assert s.ss.text == b''


def test_return_with_no_text_closes(fw, ui, song):
    s = Strip(fw, ui, library(song))
    assert s.key(KEY_RETURN) == [1] and ui.backs == 1


def test_return_with_results_moves_to_the_list_and_centre_plays_from_it(fw, ui, song):
    songs = library(song)
    s = Strip(fw, ui, songs)
    s.type('joy')
    s.key(KEY_RETURN)
    assert s.ss.focus == 1 and s.ss.sel == 0 and ui.backs == 0
    s.key(KEY_NEXT, KEY_NEXT, KEY_NEXT)  # the bar stops at the last result
    assert s.ss.sel == 2
    s.key(KEY_CENTER)
    assert ui.played == [([songs[1], songs[2]], 1, LIBRARY)]  # the songs alone are the queue


def test_an_artist_is_listed_once_and_opens_its_page(fw, ui, song):
    songs = library(song)
    s = Strip(fw, ui, songs)
    s.type('division')
    assert s.titles() == ['artist: Joy Division', 'Love Will Tear Us Apart', 'Atmosphere']
    s.key(KEY_RETURN, KEY_CENTER)
    assert ui.went == ['localmusic/artistinfo_page'] and not ui.played


def test_an_album_opens_its_tracks(fw, ui, song):
    s = Strip(fw, ui, library(song))
    s.type('menag')
    assert s.titles() == ['album: Menagerie', 'Lovely Day']
    opened = []
    s.key(KEY_RETURN)
    fw.mock('getMusicByAlbum', lambda *a: opened.append(a) or 0)  # Cover Flow reads the album's tracks
    s.key(KEY_CENTER)
    assert opened and not ui.played


def test_results_go_back_to_the_strip_by_wheel_or_button(fw, ui, song):
    s = Strip(fw, ui, library(song))
    s.type('lo')
    s.key(KEY_RETURN, KEY_PREV)  # a turn up from the first result
    assert s.ss.focus == 0 and s.ss.text == b'lo'
    letter = s.ss.letter
    s.key(KEY_RETURN, KEY_NEXT, KEY_BACK_BTN)  # Previous deletes, on the strip
    assert s.ss.focus == 0 and s.ss.text == b'l' and s.ss.letter == letter


def test_return_on_the_results_closes(fw, ui, song):
    s = Strip(fw, ui, library(song))
    s.type('lo')
    s.key(KEY_RETURN, KEY_RETURN)
    assert ui.backs == 1


def test_return_with_text_but_no_match_closes(fw, ui, song):
    s = Strip(fw, ui, library(song))
    s.type('zz')
    s.key(KEY_RETURN)
    assert s.ss.focus == 0 and ui.backs == 1


def test_the_list_scrolls_to_keep_the_bar_on_its_four_rows(fw, ui, song):
    s = Strip(fw, ui, [song(b'/m/%d.flac' % i, b'Song %d' % i) for i in range(7)])
    s.type('song')
    s.key(KEY_RETURN, *[KEY_NEXT] * 5)
    assert (s.ss.sel, s.ss.top) == (5, 2)
    s.key(*[KEY_PREV] * 3)
    assert (s.ss.sel, s.ss.top) == (2, 2)


def test_play_and_other_windows_pass(fw, ui, song):
    s = Strip(fw, ui, library(song))
    assert s.key(KEY_PLAY) == [0]
    assert s.take(0, KEY_NEXT) == 0 and s.ss.letter == 0


def test_closing_frees_and_forgets(fw, ui, song):
    s = Strip(fw, ui, library(song))
    s.type('lo')
    ui.close(ui.top())
    assert not s.ss.page and s.ss.text == b'' and s.ss.nmatch == 0


def test_a_tap_on_a_letter_types_it(fw, ui, song):
    s = Strip(fw, ui, library(song))
    s.touch(CELL_X + 4 * STEP + 5, STRIP_Y + 19)  # the cell after the highlight's: B
    assert s.ss.text == b'b' and s.ss.letter == 1
    s.touch(CELL_X + STEP + 5, STRIP_Y + 19)  # two before the highlight, now on B: delete
    assert s.ss.text == b'' and s.ss.letter == DELETE


def test_a_drag_along_the_strip_moves_the_alphabet(fw, ui, song):
    s = Strip(fw, ui, library(song))
    s.touch(300, STRIP_Y + 19, 300 - 3 * STEP, STRIP_Y + 19)  # left: later letters come under the highlight
    assert s.ss.letter == 3 and s.ss.text == b''


def test_a_tap_on_a_result_opens_it_and_a_drag_scrolls(fw, ui, song):
    songs = [song(b'/m/%d.flac' % i, b'Song %d' % i) for i in range(7)]
    s = Strip(fw, ui, songs)
    s.type('song')
    s.touch(100, 140, 100, 140 - 2 * 32)  # up: two rows on
    assert s.ss.top == 2 and not ui.played
    s.touch(100, 32 + 10)  # the second row showing
    assert s.ss.focus == 1 and s.ss.sel == 3 and ui.played == [(songs, 3, LIBRARY)]


def test_a_tap_below_the_results_does_nothing(fw, ui, song):
    s = Strip(fw, ui, library(song))
    s.type('joy')
    s.touch(100, 4 * 32 + 10)
    assert not ui.played and not ui.went and s.ss.focus == 0
