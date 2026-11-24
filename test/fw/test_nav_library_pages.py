"""The payload's own Library pages, end to end on a fake AWTK: library_lists.c's Shuffle, Most Played, Long Songs, Era
and Tidal Downloads, ratings.c's Ratings and Now Playing's heart, navigation.c's Chapters and Streaming's
Configuration, scrobble_ui.c's Upload Scrobbles, the Library and Streaming pages (home.c) and the About page stock
builds and the payload rearranges, Folder view's hooks, the queue menu's tracks and the playlist editor's records
(playlists.c)."""
import ctypes as C
import re
import struct

import pytest

import host
from conftest import FNV_PRIME, O, Deques, Files, claim, favdir_card, fnv1a, high_halves, in_fork, libc, stock_prop_str, ui_name
from conftest import Rows as HeldRows  # Rows here is Folder view's list

from test_navigation import Sa  # test_navigation.py keeps the mirror
from test_tidal import HEARTS, urls_play

KEY_RETURN, KEY_CENTER, KEY_PREV, KEY_NEXT, KEY_PLAY = (O[k] for k in ('KEY_RETURN', 'KEY_CENTER', 'KEY_PREV', 'KEY_NEXT', 'KEY_PLAY'))
EVT_CLICK, EVT_DESTROY, EVT_KEY_UP, EVT_POINTER_DOWN, EVT_POINTER_ABORT = (
    O[k] for k in ('EVT_CLICK', 'EVT_DESTROY', 'EVT_KEY_UP', 'EVT_POINTER_DOWN', 'EVT_POINTER_ABORT'))
W_PARENT, W_EMITTER, EVENT_KEY = O['W_PARENT'], O['W_EMITTER'], O['EVENT_KEY']
REC_ID, REC_TYPE, REC_CUE_START = O['REC_ID'], O['REC_TYPE'], O['REC_CUE_START']
PLAYS_SLOTS, RATE_SLOTS, RESUME_SLOTS = O['PLAYS_SLOTS'], O['RATE_SLOTS'], O['RESUME_SLOTS']
LONG_SONG_S = O['LONG_SONG_S']
LIBRARY, FOLDER = 0xf001, 1
STOP, REPEAT = 11, 8  # a handler's RET_STOP; a timer's RET_REPEAT
PLAYS_FILE, RESUME_FILE = '/mnt/mmc/.q2pod/ringnav-plays', '/mnt/mmc/.q2pod/ringnav-resume'  # card_store.c
RATINGS, ALBUM_RATINGS, LENGTHS = '/mnt/data/ringnav-ratings', '/mnt/data/ringnav-album-ratings', '/mnt/data/ringnav-durations-v1'
FAV_CLICK = 0x53c968  # Now Playing's heart: stock's My Fav toggle
TIDAL_DIR, SPOT_DIR = '/mnt/mmc/.tidal', '/mnt/mmc/.spotify'
DOT = '·'
CLICK = C.CFUNCTYPE(C.c_int, C.c_void_p, C.c_void_p)
TICK = C.CFUNCTYPE(C.c_int, C.c_void_p)
TYPES = {'window_create': 'window', 'list_item_create': 'list_item', 'hscroll_label_create': 'hscroll_label',
         'image_create': 'image', 'button_create': 'button', 'scroll_view_create': 'scroll_view',
         'list_view_create': 'list_view', 'label_create': 'label'}


def signed(v):
    return C.c_int(v).value


class Ui:
    """AWTK as a tree of zeroed widgets: what the C builds, names, writes, shows and hooks, kept per widget, with
    the timers, the windows (the newest on top) and where navigation went: toasts, plays and backs."""
    def __init__(self, fw):
        self.fw, self.blocks, self.cstrings = fw, {}, {}
        self.kind, self.kids = {}, {0: []}
        self.text, self.name, self.image, self.props = {}, {}, {}, {}
        self.hidden, self.dull = set(), set()
        self.handlers, self.windows, self.timers, self.tick = [], [], {}, 0
        self.toasts, self.played, self.went, self.backs, self.offs = [], [], [], 0, []
        self.deques = Deques(fw)
        for kind in TYPES:
            fw.mock(kind, lambda parent, x, y, w, h, *_, k=kind: self.new(k, parent, x, y, w, h))
        fw.mock('widget_factory_create_widget', lambda f, kind, parent, x, y, w, *_: self.new(C.string_at(kind).decode(), parent, x, y, w, 0))
        fw.mock('widget_set_text_utf8', lambda w, s, *_: self.text.__setitem__(w, C.string_at(s).decode()))
        fw.mock('widget_get_text_utf8', self.get_text)
        fw.mock('widget_set_name', lambda w, s, *_: self.name.__setitem__(w, C.string_at(s).decode()))
        fw.mock('widget_get_prop_str', self.prop_str)
        fw.mock('widget_get_type', lambda w, *_: self.cstr(TYPES.get(self.kind.get(w, ''), self.kind.get(w, ''))))
        fw.mock('widget_set_prop_int', lambda w, k, v, *_: self.props.__setitem__((w, C.string_at(k).decode()), signed(v)))
        fw.mock('widget_get_prop_int', lambda w, k, d, *_: self.props.get((w, C.string_at(k).decode()), signed(d)))
        fw.mock('widget_set_prop_str', lambda w, k, v, *_: self.props.__setitem__((w, C.string_at(k).decode()), C.string_at(v).decode()))
        fw.mock('widget_set_visible', lambda w, v, *_: (self.hidden.discard if v & 0xff else self.hidden.add)(w))
        fw.mock('widget_get_visible', lambda w, *_: w not in self.hidden)
        fw.mock('widget_set_sensitive', lambda w, v, *_: (self.dull.discard if v & 0xff else self.dull.add)(w))
        fw.mock('widget_get_child', lambda w, i, *_: self.child(w, i & 0xffffffff))
        fw.mock('widget_count_children', lambda w, *_: len(self.kids.get(w, ())))
        fw.mock('widget_lookup', lambda w, name, deep, *_: self.lookup(w, C.string_at(name).decode(), deep & 0xff))
        fw.mock('widget_restack', lambda w, i, *_: self.restack(w, i & 0xffffffff))
        fw.mock('widget_destroy', lambda w, *_: self.destroy(w))
        fw.mock('widget_destroy_children', lambda w, *_: [self.destroy(k) for k in list(self.kids.get(w, ()))] and 0)
        fw.mock('image_base_set_image', lambda w, s, *_: self.image.__setitem__(w, C.string_at(s).decode()))
        fw.mock('widget_on', self.on)
        fw.mock('widget_off_by_func', lambda w, evt, fn, ctx, *_: self.offs.append((w, evt, fn, ctx)))
        fw.mock('timer_add', self.add_timer)
        fw.mock('timer_remove', lambda t, *_: self.timers.pop(t & 0xffffffff, None) and 0)
        fw.mock('window_manager_get_top_window', lambda *_: self.windows[-1] if self.windows else 0)
        fw.mock('navigator_to_with_context', self.navigate)
        fw.mock('navigator_to', lambda name, *_: self.went.append(C.string_at(name).decode()))
        fw.mock('navigator_back', lambda *_: setattr(self, 'backs', self.backs + 1))

    def cstr(self, value):
        """A NUL-terminated copy of value the C may keep: one buffer per distinct string."""
        if value not in self.cstrings:
            self.cstrings[value] = C.create_string_buffer(value.encode())
        return C.addressof(self.cstrings[value])

    def new(self, kind, parent, x, y, w, h):
        block = C.create_string_buffer(0x200)
        if kind == 'window_create' and not w:
            w, h = 375, 320  # stock's pages fill the screen
        struct.pack_into('<4i', block, 0, *(signed(v) for v in (x, y, w, h)))
        struct.pack_into('<Q', block, W_PARENT, parent)
        at = C.addressof(block)
        self.blocks[at], self.kind[at], self.kids[at] = block, kind, []
        self.kids.setdefault(parent, []).append(at)
        if kind == 'window_create':
            self.windows.append(at)
        return at

    def parent(self, w):
        return C.c_void_p.from_address(w + W_PARENT).value or 0

    def child(self, w, i):
        kids = self.kids.get(w, [])
        return kids[i] if i < len(kids) else 0

    def get_text(self, w, out, n, *_):
        s = self.text.get(w, '').encode()[:n - 1]
        C.memmove(out, s + b'\0', len(s) + 1)
        return 0

    def prop_str(self, w, key, default, *_):
        if w not in self.kind:
            return default  # stock: widget_get_prop fails on a NULL widget
        key = C.string_at(key).decode()
        value = {'name': self.name, 'image': self.image}.get(key, {}).get(w)
        if value is None:
            value = self.props.get((w, key))
        kind = TYPES.get(self.kind[w], self.kind[w]).encode()
        value = stock_prop_str(kind, key.encode(), value.encode() if isinstance(value, str) else value, default)
        return 0 if value is None else self.cstr(value.decode()) if isinstance(value, bytes) else value

    def lookup(self, w, name, deep):
        for k in self.kids.get(w, ()):
            if self.name.get(k) == name:
                return k
            found = deep and self.lookup(k, name, deep)
            if found:
                return found
        return 0

    def restack(self, w, i):
        kids = self.kids[self.parent(w)]
        kids.remove(w)
        kids.insert(min(i, len(kids)), w)

    def destroy(self, w):
        """w and what it holds go, each one's EVT_DESTROY handlers run first, as AWTK's."""
        for k in list(self.kids.get(w, ())):
            self.destroy(k)
        for h in [h for h in self.handlers if h[0] == w and h[1] == EVT_DESTROY]:
            CLICK(h[2])(h[3], 0)
        self.handlers = [h for h in self.handlers if h[0] != w]
        if w in self.kids.get(self.parent(w), ()):
            self.kids[self.parent(w)].remove(w)
        if w in self.windows:
            self.windows.remove(w)
        self.kids.pop(w, None)
        return 0

    def on(self, w, evt, fn, ctx, *_):
        self.handlers.append((w, evt & 0xffffffff, fn, ctx))
        return len(self.handlers)

    def add_timer(self, fn, ctx, ms, *_):
        self.tick += 1
        self.timers[self.tick] = (fn, ctx, ms & 0xffffffff)
        return self.tick

    def navigate(self, name, info, *_):
        name = C.string_at(name).decode()
        if name == 'dialog/msginfo_dialog':
            self.toasts.append(C.string_at(info + 8).decode())
        elif name == 'playing_page':
            dq, idx, cls = struct.unpack('<Qii', C.string_at(info, 16))
            self.played.append((list(self.deques[dq]), idx, cls))  # playing_page copies the queue at once
        else:
            self.went.append(name)

    # What a test reads and does.
    def fn(self, name):
        return C.cast(getattr(self.fw.lib, ui_name(self.fw.lib, name)), C.c_void_p).value

    def pending(self, name):
        """The live timers running name, of navigation.c or the unit it moved to (ui_name finds it): [(id, ms)]."""
        return [(t, ms) for t, (fn, _, ms) in self.timers.items() if fn == self.fn(name)]

    def fire(self, name):
        """The newest timer running name, as the next loop pass runs it: gone unless it repeats."""
        t, _ = self.pending(name)[-1]
        fn, ctx, _ = self.timers[t]
        result = TICK(fn)(ctx)
        if result != REPEAT and self.timers.get(t, (None,))[0] == fn:
            del self.timers[t]
        return result

    def handler(self, w, evt):
        fns = [(fn, ctx) for ww, e, fn, ctx in self.handlers if ww == w and e == evt]
        fn, ctx = fns[-1]
        return lambda event=0: CLICK(fn)(ctx, event)

    def subtree(self, w):
        for k in self.kids.get(w, ()):
            yield k
            yield from self.subtree(k)

    def view(self, page):
        """The newest list in page: its scroll view."""
        return [w for w in self.subtree(page) if self.kind[w] == 'scroll_view_create'][-1]

    def rows(self, page):
        """The newest list's rows as (caption, detail or None)."""
        out = []
        for item in self.kids[self.view(page)]:
            labels = [self.text.get(k) for k in self.kids[item] if self.kind[k] == 'hscroll_label_create'] + [None, None]
            out.append((labels[0], labels[1]))
        return out

    def captions(self, page):
        return [c for c, _ in self.rows(page)]

    def click(self, page, row):
        """Row row of page's newest list, clicked."""
        return self.handler(self.kids[self.view(page)][row], EVT_CLICK)()

    def shown(self, page):
        """The texts of page's visible labels: its title or message among them."""
        def visible(w):
            while w and w != page:
                if w in self.hidden:
                    return False
                w = self.parent(w)
            return True
        return [self.text[w] for w in self.subtree(page) if w in self.text and visible(w)]

    def key(self, page, key):
        event = C.create_string_buffer(0x40)
        struct.pack_into('<i', event, EVENT_KEY, key)
        return self.handler(page, EVT_KEY_UP)(C.addressof(event))

    def close(self, page):
        self.destroy(page)

    def top(self):
        return self.windows[-1]


@pytest.fixture
def ui(fw):
    return Ui(fw)


def empty_state(ui, page):
    """The empty state shown in page (feedback.c empty_state): (glyph, title, hint, its button's label or None), else
    None. Its view is insensitive, so the rows beside it keep their taps; the button's row is blank, its label the
    painter's."""
    art = ui.lookup(page, '_empty_state', 1)
    if not art or art in ui.hidden:
        return None
    assert art in ui.dull
    row = ui.lookup(page, '_empty_row', 1)
    caption = None
    if row and row not in ui.hidden:
        (item,) = ui.kids[ui.kids[row][0]]
        assert next(ui.text[k] for k in ui.subtree(item) if ui.kind[k] == 'hscroll_label_create') == ''
        caption = ui.props[(art, '_es_row')]
    return tuple(ui.props[(art, f'_es_{k}')] for k in ('glyph', 'title', 'hint')) + (caption,)


class Config:
    """config.ini: a key's value, or the default; a missing key with an empty default leaves out alone. A write
    is read back, and kept in written."""
    def __init__(self, fw, **values):
        self.values, self.written = {k.encode(): v for k, v in values.items()}, []
        fw.mock('toolsReadConfig', self.read)
        fw.mock('toolsWriteConfig', self.write)

    def read(self, path, section, key, out, default, *_):
        value = self.values.get(C.string_at(key), C.string_at(default))
        if value or C.string_at(key) in self.values:
            C.memmove(out, value + b'\0', len(value) + 1)

    def write(self, path, section, key, value, *_):
        self.values[C.string_at(key)] = C.string_at(value)
        self.written.append(C.string_at(value))
        return 0


@pytest.fixture(scope='module')
def scratch(library):
    """scratch_t's field offsets as this build lays it out: its pointers move them."""
    return host.offsets(library.parent/'navigation.o', 'scratch_t')


@pytest.fixture
def st(fw, scratch):
    """navigation.c's state by field: st('rt_page', C.c_void_p).value."""
    base = C.addressof(fw.var('st', C.c_char))
    return lambda name, ctype=C.c_int: ctype.from_address(base + scratch[name])


@pytest.fixture(scope='module')
def lists_fields(library):
    """library_lists.c's own state, ll: its field offsets as this build lays it out."""
    return host.offsets(library.parent/'library_lists.o', 'll')


@pytest.fixture
def lists(fw, lists_fields):
    base = C.addressof(fw.var('library_lists__ll', C.c_char))
    return lambda name, ctype=C.c_int: ctype.from_address(base + lists_fields[name])


# Song records. The Q2's hold 4-byte pointers 4 bytes apart (REC_NAME, REC_PATH, REC_TITLE, REC_ALBUM, REC_ARTIST),
# and P() reads 8 here: a field's pointer is its own word with the next field's word as its high half. So each
# record's strings sit where that chain says: string k at (word k+1) << 32 | (word k), a page mapped for it. The
# words are this file's high halves (conftest.high_halves), the multiples of 0x40 among them.
WORDS = [w for w in high_halves(0x1000) if w % 0x40 == 0]  # a string's room: 63 bytes and its NUL
NAME_AT = O['REC_NAME']
FIELDS = ('name', 'path', 'title', 'album', 'artist')
MAPPED = set()


def place(address, data):
    page = address & ~0xfff
    if page not in MAPPED:
        assert libc.mmap(page, 0x1000, 3, 0x100022, -1, 0) == page, f'{page:#x} is taken'  # MAP_FIXED_NOREPLACE
        MAPPED.add(page)
    assert len(data) < 0x40
    C.memmove(address, data + b'\0', len(data) + 1)


class Songs:
    """stSongInfo records: a song's file name, path, title, album and artist, its id, kind and CUE range."""
    def __init__(self):
        self.keep, self.pairs, self.start = [], set(), 0

    def chain(self):
        n = len(WORDS)
        for stride in range(1, n):
            for _ in range(n):
                i, self.start = self.start, (self.start + 1) % n
                words = [WORDS[(i + k * stride) % n] for k in range(len(FIELDS) + 1)]
                pairs = set(zip(words[1:], words))
                if len(pairs) == len(FIELDS) and not pairs & self.pairs:
                    self.pairs |= pairs
                    return words
        raise AssertionError('no room for another record')

    def __call__(self, path=b'', title=b'', artist=b'', album=b'', name=None, id_=0, kind=8, cue=0, end=0, track=0, disc=0):
        name = path.rsplit(b'/', 1)[-1] if name is None else name
        words = self.chain()
        r = C.create_string_buffer(0x60)
        for k, value in enumerate((name, path, title, album, artist)):
            place(words[k + 1] << 32 | words[k], value)
        struct.pack_into('<6I', r, NAME_AT, *words)
        struct.pack_into('<i', r, REC_ID, id_)
        struct.pack_into('<i', r, REC_TYPE, kind)
        struct.pack_into('<ii', r, REC_CUE_START, cue, end)
        struct.pack_into('<i', r, O['REC_TRACK'], track)
        struct.pack_into('<i', r, O['REC_DISC'], disc)
        self.keep.append(r)
        return C.addressof(r)


@pytest.fixture
def song():
    return Songs()


def field(r, name):
    return C.string_at(C.c_void_p.from_address(r + NAME_AT + 4 * FIELDS.index(name)).value or 0)


def test_a_record_reads_back_whole(fw, song):
    """The layout the rest stands on: the C's own track_name reads the title, else the file name less its
    extension, through the same 8-byte reads."""
    tagged, untagged = song(b'/m/A/01 One.flac', b'One', b'Band', b'A'), song(b'/m/A/02 Two.flac')
    assert [field(tagged, f) for f in FIELDS] == [b'01 One.flac', b'/m/A/01 One.flac', b'One', b'A', b'Band']
    name = fw.fn('track_name', C.c_char_p, C.c_char_p, C.c_uint, C.c_void_p)
    out = C.create_string_buffer(64)
    assert name(out, 64, tagged) == b'One' and name(out, 64, untagged) == b'02 Two'


def listen_key(path, cue=0):
    """A play count's key (docs/internals.md#most-played): the path's FNV-1a with its separator, a CUE track's
    start mixed in; never 0."""
    key = fnv1a(path) * FNV_PRIME & 0xffffffff
    if cue:
        key = fnv1a(struct.pack('<i', cue), key)
    return key or 1


def file_key(path):
    return fnv1a(path) * FNV_PRIME & 0xffffffff


class Library:
    """Stock's library queries over a list of records: getAllMusic fills staged's deque with every song."""
    def __init__(self, fw, ui, songs=()):
        self.fw, self.deques, self.songs, self.asked = fw, ui.deques, list(songs), []
        fw.mock('getAllMusic', self.all)

    def all(self, sort, *_):
        self.asked.append(sort)
        self.deques[self.directory()].extend(self.songs)
        return len(self.songs)

    def directory(self):
        return self.fw.var('fw_ctx_tools_pdeq_directory', C.c_uint).value


def call(fw, name, ctx=0):
    """A row's click handler, in navigation.c or the unit it moved to (ui_name finds it), as the row's press
    calls it."""
    return fw.fn(ui_name(fw.lib, name), C.c_int, C.c_void_p, C.c_void_p)(ctx, None)


# The Library's Shuffle row.
def test_shuffle_opens_one_page_of_three_picks(fw, ui, st):
    boot(fw, st)
    call(fw, 'shuffle_menu')
    page = ui.top()
    assert ui.name[page] == 'shuffle_page' and ui.captions(page) == ['Shuffle Songs', 'Shuffle Albums', 'Shuffle Folders']
    assert 'Albums and folders take a moment to shuffle' in ui.shown(page)
    # the note at the page's foot, in the full-bleed window's rows (the screen's), 4 px clear of its end
    note = next(w for w in ui.subtree(page) if ui.text.get(w) == 'Albums and folders take a moment to shuffle')
    y, h = (C.c_int.from_address(note + k).value for k in (4, 12))
    assert (y, h) == (320 - 28, 24) and y + h <= 320 - 4
    call(fw, 'shuffle_menu')  # pressed again while it is open: the same page
    assert ui.windows == [page]


def test_shuffle_page_opens_again_once_closed(fw, ui):
    call(fw, 'shuffle_menu')
    ui.close(ui.top())
    call(fw, 'shuffle_menu')
    assert len(ui.windows) == 1


def test_shuffle_songs_with_no_library_says_to_update_it(fw, ui, mcl):
    Library(fw, ui)
    call(fw, 'shuffle_menu')
    ui.click(ui.top(), 0)
    assert ui.toasts == ['Update Local Music first'] and not ui.played


def test_shuffle_songs_plays_the_library_from_a_random_song(fw, ui, mcl, song):
    songs = [song(b'/m/%d.flac' % i) for i in range(4)]
    library = Library(fw, ui, songs)
    call(fw, 'shuffle_menu')
    ui.click(ui.top(), 0)
    (queue, at, cls), = ui.played
    assert queue == songs and 0 <= at < 4 and cls == LIBRARY
    assert library.asked == [3]  # unsorted: Shuffle has no use for the title sort
    assert ui.windows  # the page stays, as Most Played's rows leave it


def test_shuffle_songs_in_steps_reads_then_plays_and_a_cancel_between_changes_nothing(fw, ui, mcl, song):
    """The three calls a loading indicator drives (library_lists.c): the first poll reads the library and plays
    nothing, the second plays and says it is done; cancelled after the read, nothing plays and a poll is done."""
    songs = [song(b'/m/%d.flac' % i) for i in range(4)]
    library = Library(fw, ui, songs)
    start, cancel = fw.fn('shuffle_songs_start', None), fw.fn('shuffle_songs_cancel', None)
    poll = fw.fn('shuffle_songs_poll', C.c_int)
    assert poll() == 1 and not library.asked, 'nothing asked for: done, and nothing read'
    start()
    assert not library.asked and not ui.played
    assert poll() == 0 and library.asked == [3] and not ui.played
    cancel()
    assert poll() == 1 and library.asked == [3] and not ui.played and not ui.toasts
    start()
    assert poll() == 0 and not ui.played
    assert poll() == 1
    (queue, at, cls), = ui.played
    assert queue == songs and 0 <= at < 4 and cls == LIBRARY and library.asked == [3, 3]
    assert poll() == 1 and len(ui.played) == 1, 'done stays done'


@pytest.mark.parametrize('row, folders', [(1, 0), (2, 1)])
def test_shuffle_albums_and_folders_with_no_library(fw, ui, mcl, row, folders):
    Library(fw, ui)
    call(fw, 'shuffle_menu')
    ui.click(ui.top(), row)
    assert ui.toasts == ['Update Local Music first'] and not ui.played


@pytest.mark.parametrize('row, folders', [(1, 0), (2, 1)])
def test_shuffle_albums_and_folders_play_the_whole_library_grouped(fw, ui, mcl, song, row, folders):
    songs = [song(b'/m/%c/%d.flac' % (65 + i % 2, i), album=b'%c' % (65 + i % 2)) for i in range(4)]
    library = Library(fw, ui, songs)
    queue = library.deques.new()
    fw.var('fw_ctx_mcl_pdeqplaylist', C.c_uint).value = queue
    fw.mock('mclLoadPlayList', lambda dq, *_: library.deques[queue].__setitem__(slice(None), library.deques[dq]))
    call(fw, 'shuffle_menu')
    ui.click(ui.top(), row)
    (played, at, cls), = ui.played
    assert played == songs and cls == LIBRARY and not ui.toasts
    assert fw.fn('playback_option', C.c_int, C.c_int)(2) == folders  # grouped by album or by folder


def test_shuffle_albums_over_the_queue_s_limit_says_so(fw, ui, mcl):
    library = Library(fw, ui, [0x100] * 65537)  # playback.c's QUEUE_LIMIT is 65536 songs
    queue = ui.deques.new()
    fw.var('fw_ctx_mcl_pdeqplaylist', C.c_uint).value = queue
    fw.mock('mclLoadPlayList', lambda dq, *_: ui.deques[queue].__setitem__(slice(None), ui.deques[dq]))
    call(fw, 'shuffle_menu')
    ui.click(ui.top(), 1)
    assert ui.toasts == ['Too many songs to shuffle'] and not ui.played and library.asked == [3]


@pytest.mark.parametrize('key, back, result', [(KEY_RETURN, 1, STOP), (KEY_CENTER, 0, 0), (KEY_PLAY, 0, 0)])
def test_a_page_s_return_goes_back(fw, ui, key, back, result):
    call(fw, 'shuffle_menu')
    assert ui.key(ui.top(), key) == result and ui.backs == back


# Most Played.
def plays(*counts):
    """ringnav-plays: (path, count) slots, most recently counted first."""
    data = b''.join(struct.pack('<2I', listen_key(p), n) for p, n in counts)
    return data + b'\0' * (PLAYS_SLOTS * 8 - len(data))


def test_most_played_lists_the_counted_songs_most_played_first(fw, ui, song):
    a, b, c, d = (song(b'/m/%c.flac' % x, b'Song %c' % x, artist) for x, artist in zip(b'abcd', (b'X', b'', b'Y', b'Z')))
    Files(fw, {PLAYS_FILE: plays((b'/m/b.flac', 1), (b'/m/c.flac', 5), (b'/m/a.flac', 1))}, card=True)  # card_store.c
    Library(fw, ui, [a, b, c, d])
    call(fw, 'most_played')
    page = ui.top()
    assert ui.name[page] == 'mostplayed_page'
    # equal counts: the more recently counted first (the table's order)
    assert ui.rows(page) == [('Song c', f'Y {DOT} 5 plays'), ('Song b', '1 play'), ('Song a', f'X {DOT} 1 play')]
    ui.click(page, 1)
    assert ui.played == [([c, b, a], 1, LIBRARY)]


def test_most_played_with_no_plays_says_so(fw, ui, song):
    Files(fw)
    Library(fw, ui, [song(b'/m/a.flac')])
    call(fw, 'most_played')
    assert empty_state(ui, ui.top()) == ('chart-bar', 'No Plays Yet', 'Your most played songs show up here', None)
    assert ui.rows(ui.top()) == [] and 'Most Played' not in ui.shown(ui.top())  # the name is the status row's


def test_most_played_ranks_its_kept_songs_again_without_the_library(fw, ui, song, st):
    a, b = song(b'/m/a.flac', b'A'), song(b'/m/b.flac', b'B')
    Files(fw, {PLAYS_FILE: plays((b'/m/a.flac', 2), (b'/m/b.flac', 1))}, card=True)  # card_store.c
    library = Library(fw, ui, [a, b])
    call(fw, 'most_played')
    ui.close(ui.top())
    st('plays', C.c_uint * 4)[:] = [listen_key(b'/m/b.flac'), 3, listen_key(b'/m/a.flac'), 2]  # b heard twice since
    call(fw, 'most_played')
    assert ui.captions(ui.top()) == ['B', 'A'] and library.asked == [3]


def test_most_played_reads_the_library_again_after_it_changed(fw, ui, song):
    a, b = song(b'/m/a.flac', b'A'), song(b'/m/b.flac', b'B')
    Files(fw, {PLAYS_FILE: plays((b'/m/a.flac', 2), (b'/m/b.flac', 1))}, card=True)  # card_store.c
    library = Library(fw, ui, [a])
    call(fw, 'most_played')
    ui.close(ui.top())
    library.songs.append(b)
    fw.var('library_gen', C.c_uint).value += 1  # a scan or an update since
    call(fw, 'most_played')
    assert ui.captions(ui.top()) == ['A', 'B'] and library.asked == [3, 3]


def test_most_played_opens_once(fw, ui, song):
    Files(fw)
    Library(fw, ui)
    call(fw, 'most_played')
    call(fw, 'most_played')
    assert len(ui.windows) == 1


def test_most_played_finds_a_kept_song(fw, ui, song):
    a, b = song(b'/m/a.flac'), song(b'/m/b.flac')
    Files(fw, {PLAYS_FILE: plays((b'/m/b.flac', 4), (b'/m/a.flac', 1))}, card=True)  # card_store.c
    Library(fw, ui, [a, b])
    find = fw.fn('library_lists__mp_find', C.c_int, C.c_uint)
    assert find(listen_key(b'/m/a.flac')) == -1  # nothing kept before the page opens
    call(fw, 'most_played')
    assert [find(listen_key(p)) for p in (b'/m/b.flac', b'/m/a.flac', b'/m/c.flac')] == [0, 1, -1]


def test_album_plays_sums_each_album_s_listens(fw, ui, song):
    a1, a2, b1, other = (song(b'/m/%s.flac' % n) for n in (b'a1', b'a2', b'b1', b'x'))
    Files(fw, {PLAYS_FILE: plays((b'/m/a1.flac', 3), (b'/m/b1.flac', 2), (b'/m/a2.flac', 4))}, card=True)  # card_store.c
    Library(fw, ui, [a1, a2, b1, other])
    album = {a1: 0, a2: 0, b1: 1, other: -1}
    of = C.CFUNCTYPE(C.c_int, C.c_void_p)(lambda r: album[r])
    sums = (C.c_uint * 2)()
    fw.fn('album_plays', None, C.c_void_p, C.c_void_p)(C.cast(of, C.c_void_p), sums)
    assert list(sums) == [7, 2]


# Ratings' lists.
def ratings(*kept):
    """ringnav-ratings: (key, stars) slots, the rest free."""
    data = b''.join(struct.pack('<I', key & ~7 | n) for key, n in kept)
    return data + b'\0' * (RATE_SLOTS * 4 - len(data))


def test_ratings_menu_lists_its_five_lists(fw, ui):
    call(fw, 'ratings_menu')
    page = ui.top()
    assert ui.name[page] == 'ratings_page' and ui.captions(page) == ['Top Rated', 'Loved', 'Albums', 'All Rated', 'Disliked']
    assert [ui.image[w] for w in ui.subtree(ui.view(page)) if w in ui.image] == ['list_into'] * 5  # each opens a list
    call(fw, 'ratings_menu')
    assert ui.windows == [page]


class Rated:
    """A library of six songs: a My Fav with no stars, one each of 5, 4, 3 and 1 kept stars, one unrated."""
    def __init__(self, fw, ui, song):
        self.fav, self.five, self.four, self.three, self.one, self.none = (
            song(b'/m/%s.flac' % n, n.title(), artist) for n, artist in
            ((b'fav', b'F'), (b'five', b'V'), (b'four', b''), (b'three', b'T'), (b'one', b'O'), (b'none', b'N')))
        self.library = Library(fw, ui, [self.one, self.fav, self.three, self.none, self.four, self.five])
        Files(fw, {RATINGS: ratings((listen_key(b'/m/five.flac'), 5), (listen_key(b'/m/four.flac'), 4),
                                    (listen_key(b'/m/three.flac'), 3), (listen_key(b'/m/one.flac'), 1))})
        stock_fav = song(b'/m/fav.flac')  # stock's own myfav row of the same file
        fw.mock('getAllFav', lambda *_: self.library.deques[self.library.directory()].append(stock_fav) or 1)


# Each row's second line its artist after a dot (a space for none); its stars are glyphs drawn before the words
# (RATE_STARS, test_oled_draws_a_ratings_rows_stars_as_glyphs). A like is no rating: the My Fav song is in Loved
# alone, with no stars.
@pytest.mark.parametrize('row, title, want', [
    (0, 'Top Rated', [('Five', f'{DOT} V', 5), ('Four', ' ', 4)]),
    (1, 'Loved', [('Fav', f'{DOT} F', None)]),
    (3, 'All Rated', [('Five', f'{DOT} V', 5), ('Four', ' ', 4), ('Three', f'{DOT} T', 3), ('One', f'{DOT} O', 1)]),
    (4, 'Disliked', [('One', f'{DOT} O', 1)]),
])
def test_a_ratings_list_is_its_stars_most_first(fw, ui, song, row, title, want):
    Rated(fw, ui, song)
    call(fw, 'ratings_menu')
    ui.click(ui.top(), row)
    page = ui.top()
    assert ui.name[page] == 'rated_page' and title in ui.shown(page) and ui.rows(page) == [w[:2] for w in want]
    assert [ui.props.get((item, '_stars')) for item in ui.kids[ui.view(page)]] == [w[2] for w in want]


def test_loved_is_the_liked_songs_with_their_own_stars_most_first(fw, ui, song):
    a, b, c = (song(b'/m/%c.flac' % x, b'%c' % x) for x in b'abc')
    library = Library(fw, ui, [a, b, c])
    Files(fw, {RATINGS: ratings((listen_key(b'/m/b.flac'), 2), (listen_key(b'/m/c.flac'), 4))})
    fw.mock('getAllFav', lambda *_: library.deques[library.directory()].extend([song(b'/m/a.flac'), song(b'/m/b.flac')]) or 2)
    call(fw, 'rated_open', 1)
    page = ui.top()
    assert ui.captions(page) == ['b', 'a']  # c, rated but not liked, is not Loved
    assert [ui.props.get((item, '_stars')) for item in ui.kids[ui.view(page)]] == [2, None]


def test_oled_draws_a_ratings_rows_stars_as_glyphs(fw, ui, song, st):
    """OLED's second line (docs/oled-design.md): Phosphor's filled stars for the song's, outlined for the rest, in
    Text Primary or white on the selection, the words after them."""
    from test_nav_hold import use_set
    use_set(st)
    Rated(fw, ui, song)
    fw.mock('widget_move_resize', lambda w, x, y, ww, h, *_: struct.pack_into('<4i', ui.blocks[w], 0, *map(signed, (x, y, ww, h))))
    call(fw, 'rated_open', 3)
    page = ui.top()
    assert ui.rows(page) == [('Five', f'{DOT} V'), ('Four', ' '), ('Three', f'{DOT} T'), ('One', f'{DOT} O')]
    drawn: list = []  # image names, each followed by where it was drawn
    canvas = C.create_string_buffer(0x100)
    fw.mock('widget_load_image', lambda w, name, bitmap, *_: drawn.append(C.string_at(name).decode()) or 0)
    fw.mock('canvas_draw_image', lambda canvas, bitmap, src, dst, *_: drawn.append(struct.unpack_from('<2i', C.string_at(dst, 8))) or 0)
    three = ui.kids[ui.view(page)][2]
    paint = fw.fn('rate_stars_paint', None, C.c_void_p, C.c_void_p)
    paint(three, C.addressof(canvas))
    names, at = drawn[0::2], drawn[1::2]
    assert names == ['oled_rate_on_d'] * 3 + ['oled_rate_off_d'] * 2
    words = struct.unpack_from('<i', ui.blocks[ui.kids[three][1]], 0)[0]
    assert [x for x, _ in at] == [words - 84 + 16 * i for i in range(5)] and at[0][0] >= 0
    drawn.clear()
    st('sel_accent', C.c_void_p).value = st('paint_w', C.c_void_p).value = three
    paint(three, C.addressof(canvas))
    assert drawn[0::2] == ['oled_rate_on_w'] * 3 + ['oled_rate_off_w'] * 2


def test_a_ratings_list_plays_from_its_row(fw, ui, song):
    rated = Rated(fw, ui, song)
    call(fw, 'rated_open', 3)
    ui.click(ui.top(), 2)
    assert ui.played == [([rated.five, rated.four, rated.three, rated.one], 2, LIBRARY)]


def test_a_ratings_list_in_library_order_among_equal_hearts(fw, ui, song):
    a, b, c = (song(b'/m/%c.flac' % x, b'%c' % x) for x in b'abc')
    Library(fw, ui, [c, a, b])
    Files(fw, {RATINGS: ratings(*((listen_key(b'/m/%c.flac' % x), 2) for x in b'abc'))})
    fw.mock('getAllFav', lambda *_: 0)
    call(fw, 'rated_open', 3)
    assert ui.captions(ui.top()) == ['c', 'a', 'b']


def test_an_empty_ratings_list_says_how_to_rate(fw, ui, song):
    Library(fw, ui, [song(b'/m/a.flac', b'A')])
    Files(fw)
    call(fw, 'rated_open', 0)
    assert empty_state(ui, ui.top()) == ('star', 'No Rated Songs Yet', 'Swipe to the stars on Now Playing', None)
    assert ui.rows(ui.top()) == []  # no fake row, nothing to play


def test_an_empty_loved_says_how_to_love_a_song(fw, ui, song):
    Library(fw, ui, [song(b'/m/a.flac', b'A')])
    Files(fw, {RATINGS: ratings((listen_key(b'/m/a.flac'), 5))})  # five stars are no like
    fw.mock('getAllFav', lambda *_: 0)
    call(fw, 'rated_open', 1)
    assert empty_state(ui, ui.top()) == ('heart-slash', 'No Loved Songs Yet', 'Tap the heart on Now Playing', None)


def test_a_ratings_list_reads_every_song(fw, ui, song):
    library = Library(fw, ui, [song(b'/m/a.flac')])
    Files(fw)
    call(fw, 'rated_open', 0)
    assert library.asked == [0]  # getAllMusic(0): stock's sort, spoken tracks too


def test_a_ratings_list_closes_with_its_page(fw, ui, song, st):
    Rated(fw, ui, song)
    call(fw, 'rated_open', 0)
    assert st('rt_list', C.c_void_p).value
    ui.close(ui.top())
    assert not st('rt_page', C.c_void_p).value and not st('rt_list', C.c_void_p).value
    call(fw, 'rated_open', 1)  # and opens again
    assert 'Loved' in ui.shown(ui.top())


# rate_all's stars and likes, case by case and against a model of the rule.
LIKED = 0x80  # ratings.c RATE_LIKED


def rate_all(fw, ui, records, favs=(), slots=()):
    """rate_all's stars for records, LIKED added for those in My Fav, with favs (records) in My Fav and slots (raw
    words) in the kept table. Once a test: the table is read on first use."""
    fw.mock('getAllFav', lambda *_: ui.deques[fw.var('fw_ctx_tools_pdeq_directory', C.c_uint).value].extend(favs) or len(favs))
    Files(fw, {RATINGS: b''.join(struct.pack('<I', s) for s in slots) + b'\0' * (RATE_SLOTS - len(slots)) * 4})
    h = (C.c_ubyte * (len(records) + 1))()
    fw.fn('ratings__rate_all', None, C.c_void_p, C.c_void_p)(ui.deques.new(records), h)
    return list(h[:len(records)])


def stars_model(keys, favs, slots):
    """The rule: a song is liked when a favourite's key matches it above the low three bits; its stars are the last
    kept slot in slot order that matches and holds 1 to 5, others passed over."""
    out = []
    for key in keys:
        liked = any((f ^ key) >> 3 == 0 for f in favs)
        stars = 0
        for s in slots:
            if s and (s ^ key) < 8 and 1 <= s & 7 <= 5:
                stars = s & 7
        out.append(stars | (LIKED if liked else 0))
    return out


def test_a_favourite_keeps_its_own_stars(fw, ui, song):
    a, b = song(b'/m/a.flac'), song(b'/m/b.flac')
    favs = [song(b'/m/a.flac'), song(b'/m/b.flac')]
    assert rate_all(fw, ui, [a, b], favs=favs, slots=[listen_key(b'/m/a.flac') & ~7 | 2]) == [LIKED | 2, LIKED]


def test_a_kept_song_is_its_stars(fw, ui, song):
    a, b = song(b'/m/a.flac'), song(b'/m/b.flac')
    assert rate_all(fw, ui, [a, b], slots=[0, listen_key(b'/m/b.flac') & ~7 | 3]) == [0, 3]


def test_five_stars_are_kept_and_no_like(fw, ui, song):
    assert rate_all(fw, ui, [song(b'/m/a.flac')], slots=[listen_key(b'/m/a.flac') & ~7 | 5]) == [5]


def test_keys_alike_below_bit_3_keep_their_own_stars(fw, ui, song):
    paths = [b'/m/%d.flac' % i for i in range(64)]
    p, q = next((p, q) for p in paths for q in paths if p < q and listen_key(p) & 7 == listen_key(q) & 7)
    assert listen_key(p) >> 3 != listen_key(q) >> 3
    assert rate_all(fw, ui, [song(p), song(q)], slots=[listen_key(p) & ~7 | 1, listen_key(q) & ~7 | 4]) == [1, 4]


@pytest.mark.parametrize('slots, want', [((2, None, 4), 4), ((4, 2), 2), ((2, 5), 5), ((5, 2), 2)])  # None: free
def test_two_slots_of_one_key_read_the_later(fw, ui, song, slots, want):
    k = listen_key(b'/m/a.flac') & ~7
    assert rate_all(fw, ui, [song(b'/m/a.flac')], slots=[0 if n is None else k | n for n in slots]) == [want]


@pytest.mark.parametrize('bad', [0, 6, 7])
@pytest.mark.parametrize('before', [(), (2,)])
def test_a_malformed_slot_is_passed_over(fw, ui, song, bad, before):
    k = listen_key(b'/m/a.flac') & ~7
    assert rate_all(fw, ui, [song(b'/m/a.flac')], slots=[k | n for n in (*before, bad)]) == [2 if before else 0]


def test_a_large_library_reads_as_the_model(fw, ui, song):
    import random
    rng = random.Random(13)
    paths = [b'/m/%04d.flac' % i for i in range(300)]
    keys = [listen_key(p) for p in paths]
    gone = [listen_key(b'/x/%d.flac' % i) for i in range(20)]  # rated, no longer in the library
    favs = rng.sample(paths, 40) + [b'/x/0.flac']
    slots = [0] * 600
    for key in rng.sample(keys, 120) + rng.sample(keys, 30) + gone:  # some songs twice: a damaged table
        slots[rng.randrange(len(slots))] = key & ~7 | rng.choice([0, 1, 2, 3, 4, 5, 5, 6, 7])
    want = stars_model(keys, [listen_key(p) for p in favs], slots)
    assert {0, 5, LIKED} < set(want) and len(set(want)) >= 8  # the cases are all there
    assert rate_all(fw, ui, [song(p) for p in paths], favs=[song(p) for p in favs], slots=slots) == want


def test_no_rated_albums_says_so(fw, ui, song):
    Files(fw)
    fw.mock('getAllAlbum', lambda *_: 0)
    call(fw, 'ratings_menu')
    ui.click(ui.top(), 2)
    assert ui.toasts == ['No rated albums yet']


# Long Songs.
class Probe:
    """Stock's TagLib, one file's seconds each (None: TagLib can't open it), and the worker's thread, run when
    the test says."""
    def __init__(self, fw, seconds):
        self.fw, self.seconds, self.opened, self.threads = fw, dict(seconds), [], []
        self.keep, self.files, self.stamps = [], {}, {}
        fw.mock('ringnav_statx', self.stat)
        self.length = C.CFUNCTYPE(C.c_int, C.c_void_p)(lambda p: self.seconds[self.files[p]])
        fw.mock('taglib_file_new', self.open)
        fw.mock('taglib_file_audioproperties', lambda f, *_: f)
        fw.mock('fw_pthread_create', lambda t, attr, fn, arg, *_: self.threads.append(fn) or 0)

    def stat(self, dirfd, path, flags, mask, out, *_):
        """statx: a regular file of stamps[path] (size, mtime, nanoseconds), or none for None."""
        path = C.string_at(path)
        stamp = self.stamps.get(path, (1000, 2000, 0))
        if stamp is None:
            return 2  # ENOENT, as the raw syscall returns it
        sx = (C.c_uint * 64).from_address(out)
        sx[7] = 0o100644  # stx_mode, byte 28
        sx[10], sx[28], sx[30] = stamp  # stx_size, stx_mtime's seconds and nanoseconds
        return 0

    def open(self, path, kind, *_):
        path = C.string_at(path)
        self.opened.append(path)
        if self.seconds.get(path) is None:
            return 0
        vtable = (C.c_void_p * 4)(0, 0, 0, C.cast(self.length, C.c_void_p).value)
        props = C.c_void_p(C.addressof(vtable))
        self.keep += [vtable, props]
        self.files[C.addressof(props)] = path
        return C.addressof(props)

    def run(self):
        C.CFUNCTYPE(C.c_void_p, C.c_void_p)(self.threads[-1])(None)


def path_key(path):
    """durations.c's key: the path's FNV-1a 64."""
    h = 0xcbf29ce484222325
    for c in path:
        h = (h ^ c) * 0x100000001b3 & (1 << 64) - 1
    return h


def lengths(*pairs, stamp=(1000, 2000, 0)):
    """The duration cache: its magic, then (key, size, mtime, ns, seconds) by key, at the Probe's default stamp."""
    return b'Q2DUR2\0\0' + b''.join(struct.pack('<Q3Ii', k, *stamp, s) for k, s in sorted((path_key(p), s) for p, s in pairs))


@pytest.fixture
def clock_text(fw):
    """toolsTimeItoa's m:ss, whatever stock's exact format."""
    fw.mock('toolsTimeItoa', lambda out, sec, *_: C.memmove(out, b'%d:%02d\0' % (sec // 60, sec % 60), 8) and 0)


def test_long_songs_lists_those_over_ten_minutes_longest_first(fw, ui, song, clock_text):
    a, b, c = song(b'/m/a.flac', b'A', b'X'), song(b'/m/b.flac', b'B'), song(b'/m/c.flac', b'C', b'Z')
    Library(fw, ui, [a, b, c])
    Files(fw, {LENGTHS: lengths((b'/m/a.flac', 700), (b'/m/b.flac', 1200), (b'/m/c.flac', LONG_SONG_S))})
    probe = Probe(fw, {})
    call(fw, 'long_songs')
    page = ui.top()
    assert ui.name[page] == 'longsongs_page' and ui.rows(page) == [('B', '20:00'), ('A', f'X {DOT} 11:40')]
    assert not probe.threads  # every length known
    ui.click(page, 1)
    assert ui.played == [([b, a], 1, LIBRARY)]


def test_long_songs_with_none_says_so(fw, ui, song):
    Library(fw, ui, [song(b'/m/a.flac', b'A')])
    Files(fw, {LENGTHS: lengths((b'/m/a.flac', 60))})
    call(fw, 'long_songs')
    # the empty state (audit2 K1), no fake row
    assert empty_state(ui, ui.top()) == ('timer', 'No Long Songs', 'None over 10 minutes', None)
    assert ui.rows(ui.top()) == []
    call(fw, 'long_songs')  # pressed again while it is open
    assert len(ui.windows) == 1


def test_long_songs_measures_the_unknown_first_then_lists_them(fw, ui, song, clock_text):
    a, b, c = song(b'/m/a.flac', b'A'), song(b'/m/b.flac', b'B'), song(b'/m/c.flac', b'C')
    Library(fw, ui, [a, b, c])
    files = Files(fw, {LENGTHS: lengths((b'/m/a.flac', 900))})
    probe = Probe(fw, {b'/m/b.flac': 3600, b'/m/c.flac': None})
    call(fw, 'long_songs')
    page = ui.top()
    assert ui.rows(page) == [('Cancel', None)] and 'Measuring songs…' in ui.shown(page)
    assert [ms for _, ms in ui.pending('ls_poll')] == [250]
    assert ui.fire('ls_poll') == REPEAT and 'Measuring songs… 0/3' in ui.shown(page)
    probe.run()
    assert sorted(probe.opened) == [b'/m/b.flac', b'/m/c.flac']  # only the two never measured
    assert ui.fire('ls_poll') == 0 and not ui.pending('ls_poll')
    assert ui.rows(page) == [('B', '60:00'), ('A', '15:00')]
    # what was measured is kept, sorted, a file TagLib can't read as 0 so it isn't measured again
    assert files.files[LENGTHS] == lengths((b'/m/a.flac', 900), (b'/m/b.flac', 3600), (b'/m/c.flac', 0))


def test_long_songs_cancel_lists_what_is_known(fw, ui, song, clock_text):
    a, b = song(b'/m/a.flac', b'A'), song(b'/m/b.flac', b'B')
    Library(fw, ui, [a, b])
    files = Files(fw, {LENGTHS: lengths((b'/m/a.flac', 900))})
    probe = Probe(fw, {b'/m/b.flac': 3600})
    call(fw, 'long_songs')
    ui.click(ui.top(), 0)  # Cancel, before the worker measured anything
    probe.run()  # cancellation is cooperative; join only after the worker exits
    assert ui.fire('ls_poll') == 0
    assert len(fw.calls('fw_pthread_join')) == 1  # the worker stopped and joined
    assert ui.rows(ui.top()) == [('A', '15:00')] and files.files[LENGTHS] == lengths((b'/m/a.flac', 900))


def test_long_songs_closed_while_measuring_keeps_what_was_measured(fw, ui, song, clock_text):
    a, b = song(b'/m/a.flac', b'A'), song(b'/m/b.flac', b'B')
    Library(fw, ui, [a, b])
    files = Files(fw)
    probe = Probe(fw, {b'/m/a.flac': 700, b'/m/b.flac': 800})
    call(fw, 'long_songs')
    probe.run()
    ui.close(ui.top())
    assert not ui.pending('ls_poll') and files.files[LENGTHS] == lengths((b'/m/a.flac', 700), (b'/m/b.flac', 800))
    measured = len(probe.opened)
    call(fw, 'long_songs')  # opened again: nothing left to measure
    assert len(probe.opened) == measured and len(probe.threads) == 1 and ui.captions(ui.top()) == ['B', 'A']


def test_long_songs_closed_with_the_switch_off_stops_its_pass(fw, ui, song, clock_text):
    Library(fw, ui, [song(b'/m/a.flac', b'A'), song(b'/m/b.flac', b'B'), song(b'/m/c.flac', b'C')])
    files = Files(fw, {LENGTHS: lengths((b'/m/a.flac', 700))})
    seconds = {b'/m/b.flac': 800, b'/m/c.flac': 900}
    probe = Probe(fw, seconds)
    call(fw, 'long_songs')
    opened = probe.open
    fw.mock('taglib_file_new', lambda *a: ui.close(ui.top()) or opened(*a))  # closed during the first file
    probe.run()
    first, = probe.opened  # it stops at the next check
    assert fw.fn('durations_busy', C.c_int, C.c_void_p, C.c_void_p)(None, None) == 0
    assert files.files[LENGTHS] == lengths((b'/m/a.flac', 700), (first, seconds[first]))  # what was measured is kept


def test_long_songs_closed_with_the_switch_on_lets_its_pass_finish(fw, ui, song, clock_text):
    Library(fw, ui, [song(b'/m/a.flac', b'A')])
    Files(fw)
    Config(fw, SCANDURATIONS=b'1')
    fw.fn('pod_settings', None)()
    probe = Probe(fw, {b'/m/a.flac': 700})
    call(fw, 'long_songs')
    ui.close(ui.top())
    probe.run()
    assert probe.opened == [b'/m/a.flac']


def test_long_songs_after_a_restart_walks_nothing_when_every_song_has_a_length(fw, ui, song, clock_text):
    """A fresh durations state (RAM, gone at power-off) over a full cache: listed at once."""
    Library(fw, ui, [song(b'/m/a.flac', b'A')])
    Files(fw, {LENGTHS: lengths((b'/m/a.flac', 700))})
    probe = Probe(fw, {})
    call(fw, 'long_songs')
    assert not probe.threads and not fw.calls('ringnav_statx') and ui.rows(ui.top()) == [('A', '11:40')]


def test_a_lengths_file_that_cannot_be_read_is_not_written_over(fw, ui, song, clock_text):
    Library(fw, ui, [song(b'/m/a.flac', b'A')])
    old = lengths((b'/m/x.flac', 700), (b'/m/y.flac', 800))
    files = Files(fw, {LENGTHS: old})
    read = files.fread
    fw.mock('fw_fread', lambda out, size, count, f, *_: 0 if files.open[f][0] == LENGTHS else read(out, size, count, f))
    probe = Probe(fw, {b'/m/a.flac': 900})
    call(fw, 'long_songs')
    probe.run()
    ui.fire('ls_poll')
    assert ui.captions(ui.top()) == ['A'] and files.files[LENGTHS] == old  # this visit's, kept in memory alone


def test_a_cue_track_s_length_is_its_own(fw, ui, song, clock_text):
    whole = song(b'/m/book.flac', b'Whole', cue=100, end=800)  # both ends: no measuring
    last = song(b'/m/book.flac', b'Last', cue=800)  # to the file's end
    tail = song(b'/m/side.flac', b'Tail', cue=1000)
    Library(fw, ui, [whole, last, tail])
    files = Files(fw)
    probe = Probe(fw, {b'/m/book.flac': 1500, b'/m/side.flac': 900})
    call(fw, 'long_songs')
    probe.run()
    ui.fire('ls_poll')
    assert ui.rows(ui.top()) == [('Whole', '11:40'), ('Last', '11:40')] or \
        ui.rows(ui.top()) == [('Last', '11:40'), ('Whole', '11:40')]  # 700 s each; Tail starts past its end
    assert sorted(probe.opened) == [b'/m/book.flac', b'/m/side.flac']  # for Last and Tail: Whole has both ends
    assert files.files[LENGTHS] == lengths((b'/m/book.flac', 1500), (b'/m/side.flac', 900))


def test_a_file_two_cue_tracks_run_to_the_end_of_is_kept_once(fw, ui, song, clock_text):
    """Two CUE tracks to one unmeasured file's end each want it measured; the lengths file keeps it once, and
    both tracks' lengths come from it."""
    early, late = song(b'/m/book.flac', b'Early', cue=300), song(b'/m/book.flac', b'Late', cue=800)
    Library(fw, ui, [late, early])
    files = Files(fw)
    probe = Probe(fw, {b'/m/book.flac': 1500})
    call(fw, 'long_songs')
    assert ui.fire('ls_poll') == REPEAT and 'Measuring songs… 0/2' in ui.shown(ui.top())  # one job per track
    probe.run()
    ui.fire('ls_poll')
    assert ui.rows(ui.top()) == [('Early', '20:00'), ('Late', '11:40')]  # 1200 s and 700 s, longest first
    assert files.files[LENGTHS] == lengths((b'/m/book.flac', 1500))  # one exact-path entry


# Upload Scrobbles.
class Up(C.Structure):
    _fields_ = [('lock', C.c_uint * 16), ('thread', C.c_ulong), ('running', C.c_int), ('done', C.c_int), ('ok', C.c_int),
                ('sent', C.c_int), ('cfg', (C.c_char * 256) * 6)]


class Scrobbler:
    """.scrobble.ini (a ListenBrainz token, or none, and [SCROBBLE] AUTO), Wi-Fi, and the upload's thread, which
    only starts: the test ends it."""
    def __init__(self, fw, token=b'abc', wifi=1, auto=b''):
        self.up, self.sa, self.threads = fw.var('scrobble__up', Up), fw.var('scrobble_ui__sa', Sa), []
        self.values = values = {b'TOKEN': token, b'AUTO': auto}
        def read(path, section, key, out, default, *_):
            value = values.get(C.string_at(key), b'')
            C.memmove(out, value + b'\0', len(value) + 1)
        fw.mock('toolsReadConfig', read)
        fw.mock('get_wifisignal', lambda *_: wifi)
        fw.mock('fw_pthread_create', lambda t, attr, fn, arg, *_: self.threads.append(fn) or 0)

    def ends(self, ok, sent):
        self.up.done, self.up.ok, self.up.sent = 1, ok, sent


def test_upload_needs_wifi(fw, ui):
    scrobbler = Scrobbler(fw, wifi=0)
    call(fw, 'upload_scrobbles')
    assert ui.toasts == ['Connect to Wi-Fi first'] and not scrobbler.threads and not ui.timers


def test_upload_without_an_account_fails(fw, ui):
    Scrobbler(fw, token=b'')
    call(fw, 'upload_scrobbles')
    assert ui.toasts == ['Upload failed'] and not ui.timers


@pytest.mark.parametrize('ok, sent, toast, pending', [(1, 3, 'Scrobbles sent: 3', 0), (1, 0, 'Nothing to upload', 0),
                                                      (0, 2, 'Upload failed, 2 sent', 5)])
def test_upload_reports_how_it_went(fw, ui, ok, sent, toast, pending):
    scrobbler = Scrobbler(fw)
    scrobbler.sa.pending = 5
    call(fw, 'upload_scrobbles')
    assert ui.toasts == ['Uploading scrobbles'] and len(scrobbler.threads) == 1
    assert [ms for _, ms in ui.pending('upload_wait')] == [500]
    assert ui.fire('upload_wait') == REPEAT and ui.toasts == ['Uploading scrobbles']  # still going
    scrobbler.ends(ok, sent)
    assert ui.fire('upload_wait') == 0 and ui.toasts[1:] == [toast]
    assert scrobbler.sa.pending == pending  # listens sent no longer count toward the automatic upload


def test_upload_while_one_runs(fw, ui):
    scrobbler = Scrobbler(fw)
    call(fw, 'upload_scrobbles')
    call(fw, 'upload_scrobbles')
    assert ui.toasts == ['Uploading scrobbles', 'Already uploading'] and len(ui.pending('upload_wait')) == 1
    assert len(scrobbler.threads) == 1


def test_upload_takes_over_the_automatic_one(fw, ui):
    scrobbler = Scrobbler(fw)
    scrobbler.up.running, scrobbler.sa.running = 1, 1
    call(fw, 'upload_scrobbles')
    assert ui.toasts == ['Uploading scrobbles'] and ui.pending('upload_wait') and not scrobbler.sa.running
    scrobbler.ends(1, 4)
    ui.fire('upload_wait')
    assert ui.toasts[-1] == 'Scrobbles sent: 4'


@pytest.mark.parametrize('ok, pending', [(1, 0), (0, 3)])
def test_the_automatic_upload_ends(fw, ok, pending):
    scrobbler = Scrobbler(fw)
    now = 10 ** 9
    fw.mock('time_now_ms', lambda *_: now)
    scrobbler.up.running, scrobbler.sa.running, scrobbler.sa.pending = 1, 1, 3
    auto = fw.fn('scrobble_auto', None)
    auto()
    assert scrobbler.sa.running == 1  # still uploading
    scrobbler.ends(ok, 3)
    auto()
    assert scrobbler.sa.running == 0 and scrobbler.sa.pending == pending  # sent listens no longer wait


@pytest.mark.parametrize('why', ['failed', 'off'])
def test_the_automatic_upload_tries_again_30_minutes_on(fw, why):
    """A failure tries again SA_RETRY_MIN (30) minutes on (docs/internals.md#scrobbling), and a due upload that
    found AUTO off reads the card again as late (scrobble_auto's comment); not a millisecond sooner."""
    scrobbler = Scrobbler(fw, auto=b'' if why == 'off' else b'1')
    clock = [10 ** 9]
    fw.mock('time_now_ms', lambda *_: clock[0])
    auto = fw.fn('scrobble_auto', None)
    scrobbler.sa.pending, scrobbler.sa.first_at = 3, clock[0] - 180 * 60000  # three listens, long due
    auto()
    if why == 'failed':
        assert len(scrobbler.threads) == 1
        clock[0] += 5 * 60000  # five minutes on, it fails
        scrobbler.ends(0, 0)
        auto()
    else:
        assert not scrobbler.threads
        scrobbler.values[b'AUTO'] = b'1'  # turned on since
    assert scrobbler.sa.running == 0 and scrobbler.sa.pending == 3
    since, tried = clock[0], len(scrobbler.threads)
    clock[0] = since + 30 * 60000 - 1
    auto()
    assert len(scrobbler.threads) == tried
    clock[0] = since + 30 * 60000
    auto()
    assert len(scrobbler.threads) == tried + 1 and scrobbler.sa.running == 1


def test_the_automatic_upload_starts_on_its_own(fw, ui):
    scrobbler = Scrobbler(fw, auto=b'1')
    now = 10 ** 9
    fw.mock('time_now_ms', lambda *_: now)
    scrobbler.sa.pending, scrobbler.sa.first_at = 1, now - 180 * 60000
    fw.fn('scrobble_auto', None)()
    assert scrobbler.sa.running == 1 and len(scrobbler.threads) == 1 and not ui.toasts  # silently


# Now Playing's heart and the rating picker.
class Heart:
    """Now Playing's window with stock's heart (img_fav, its click stock's My Fav toggle) and a playing library
    track, rated (kept stars) or not; rate_bind as playing_page_init runs it."""
    CTX = 0x5eed
    log: object = None  # a caller's stock_stub argument log, kept alive with the heart (test_catalogue.py)

    def __init__(self, fw, ui, song, mcl, kept=0, fav=False, bind=True, handler=FAV_CLICK, removing=0, table=None,
                 path=b'/m/now.flac', image=None):
        self.fw, self.ui = fw, ui
        self.track = song(path, b'Now')
        queue = ui.deques.new([self.track])
        fw.var('fw_ctx_mcl_pdeqplaylist', C.c_uint).value = queue
        mcl.type, mcl.pos = LIBRARY, 0
        if table is None and kept:
            table = ratings((listen_key(b'/m/now.flac'), kept))
        self.files = Files(fw, {} if table is None else {RATINGS: table})
        self.fav = fav
        fw.mock('checkFavExist', lambda *_: self.fav)
        self.pressed = 1
        fw.mock('window_manager_get_pointer_pressed', lambda *_: self.pressed)
        self.win = ui.new('window_create', 0, 0, 0, 0, 0)
        self.heart = ui.new('image_create', self.win, 300, 200, 50, 40)
        ui.name[self.heart] = 'img_fav'
        ui.image[self.heart] = image or ('play_fav' if fav else 'play_unfav')
        self.emitter, self.item = C.create_string_buffer(0x40), C.create_string_buffer(0x40)
        struct.pack_into('<Q', self.emitter, 0, C.addressof(self.item))
        struct.pack_into('<Q', self.item, 0, self.CTX)  # emitter_on_with_tag's item: {ctx, id, type, handler...}
        struct.pack_into('<i', self.item, O['EMIT_TYPE'], EVT_CLICK)
        struct.pack_into('<Q', self.item, 12, handler)
        struct.pack_into('<B', self.item, O['EMIT_PENDING_REMOVE'], removing)
        C.c_void_p.from_address(self.heart + W_EMITTER).value = C.addressof(self.emitter)
        if bind:
            fw.fn('rate_bind', None, C.c_void_p)(self.win)

    def stars(self):
        """The playing track's rating as the rating page shows it (ratings.c rate_now): 0 to 5, -1 for none."""
        return self.fw.fn('rate_now', C.c_int)()

    def rate(self, n):
        """n stars kept on the rating page (np_rate.c nr_keep): 1 when kept."""
        return self.fw.fn('rate_now_set', C.c_int, C.c_int)(n)

    def kept(self):
        return self.fw.fn('rate_kept', C.c_int, C.c_int, C.c_uint)(0, listen_key(b'/m/now.flac'))

    def copy_due(self):
        return self.fw.var('ratings__rc_due', C.c_int).value


def test_the_heart_s_click_is_swapped_and_it_has_no_hold(fw, ui, song, mcl):
    """A press held on the heart is a tap: nothing but the click is bound, no hold timer."""
    heart = Heart(fw, ui, song, mcl)
    assert ui.offs == [(heart.heart, EVT_CLICK, FAV_CLICK, Heart.CTX)]  # stock's own click comes off
    on = {(e, fn, ctx) for w, e, fn, ctx in ui.handlers if w == heart.heart}
    assert on == {(EVT_CLICK, ui.fn('rate_fav_click'), Heart.CTX)}
    assert [ms for _, ms in ui.pending('rate_tick')] == [500]


@pytest.mark.parametrize('handler, removing', [(0x52b000, 0), (FAV_CLICK, 1)])
def test_a_heart_without_stock_s_click_is_left_alone(fw, ui, song, mcl, handler, removing):
    heart = Heart(fw, ui, song, mcl, handler=handler, removing=removing)
    assert not ui.offs and not [h for h in ui.handlers if h[0] == heart.heart] and not ui.timers
    assert ui.kids[heart.win] == [heart.heart]  # no badge


@pytest.mark.parametrize('kept, fav, stars', [(3, False, 3), (0, False, 0), (3, True, 3), (0, True, 0), (5, False, 5),
                                              (5, True, 5)])
@pytest.mark.parametrize('image', ['play_unfav', 'oled_np_unfav_d', 'oled_np_fav_d'])
def test_the_heart_has_no_badge_and_the_stars_are_the_kept_rating_alone(fw, ui, song, mcl, kept, fav, stars, image):
    """The heart is the like alone: nothing is added beside it, whatever its image, and nothing is written on it as
    the tick runs. The rating page's stars are the kept ones, whether the song is liked or not (np_rate.c)."""
    heart = Heart(fw, ui, song, mcl, kept=kept, fav=fav, image=image)
    fw.var('g_backlight_status', C.c_ubyte).value = 1
    assert ui.fire('rate_tick') == REPEAT
    assert ui.kids[heart.win] == [heart.heart] and not fw.calls('widget_set_text_utf8')
    assert heart.stars() == stars


def test_a_streamed_track_has_no_stars(fw, ui, song, mcl):
    heart = Heart(fw, ui, song, mcl, kept=3, bind=False)
    mcl.type = 0x2000  # a stream's queue
    fw.fn('rate_bind', None, C.c_void_p)(heart.win)
    assert heart.stars() == -1 and ui.kids[heart.win] == [heart.heart]


@pytest.mark.parametrize('kept', [5, 4, 1, 0])
def test_the_stars_set_are_kept(fw, ui, song, mcl, kept):
    heart = Heart(fw, ui, song, mcl, kept=3)
    assert heart.rate(kept) == 1
    assert heart.kept() == kept and heart.copy_due() and heart.stars() == kept


def test_the_tick_goes_with_its_window(fw, ui, song, mcl, st):
    heart = Heart(fw, ui, song, mcl, kept=1)
    ui.close(heart.win)
    assert not st('rate_win', C.c_void_p).value and not ui.pending('rate_tick')


def test_another_window_s_end_leaves_the_heart(fw, ui, song, mcl, st):
    heart = Heart(fw, ui, song, mcl, kept=1)
    assert fw.fn('ratings__rate_gone', C.c_int, C.c_void_p, C.c_void_p)(0x1234, None) == 0
    assert st('rate_win', C.c_void_p).value == heart.win and ui.pending('rate_tick')


def stock_stub(address, result, log):
    """x86-64 at a fixed stock address: its first three arguments logged, result returned. Only ever in a fork.
    The code holds log's address, not log: the caller keeps log alive while the stub can run, or the stub
    writes into whatever Python object took its memory."""
    claim(address)
    code = (b'\x48\xb8' + struct.pack('<Q', C.addressof(log)) + bytes.fromhex('488938 48897008 48895010') +
            b'\xb8' + struct.pack('<I', result) + b'\xc3')
    C.memmove(address, code, len(code))


@in_fork
@pytest.mark.parametrize('fav', [False, True])
@pytest.mark.parametrize('n', [5, 4, 0])
def test_stars_leave_the_like_as_it_is(fw, ui, song, mcl, fav, n):
    """Five stars are no like and fewer take none away: the heart's toggle never runs, My Fav is not written."""
    heart = Heart(fw, ui, song, mcl, kept=3, fav=fav)
    log = (C.c_long * 3)()
    stock_stub(FAV_CLICK, 0, log)
    assert heart.rate(n) == 1
    assert not log[0] and not fw.calls('batch_add_file') and not fw.calls('deleteMusicFromFav')
    assert heart.kept() == n and ui.image[heart.heart] == ('play_fav' if fav else 'play_unfav')


@in_fork
def test_stars_on_an_app_synced_song_tell_its_app_nothing(fw, ui, song, mcl):
    """The card app's fav.log is the like's (test_the_heart_on_a_song_an_app_synced_is_stock_s_and_told_to_the_app)."""
    path = b'/mnt/mmc/Jellyfin/A/now.flac'
    heart = Heart(fw, ui, song, mcl, path=path)
    log = (C.c_long * 3)()
    stock_stub(FAV_CLICK, 0, log)
    favdir_card(fw, heart.files.files)
    assert heart.rate(5) == 1 and not log[0]
    assert '/mnt/mmc/.jf/fav.log' not in heart.files.files


@in_fork
def test_a_tap_is_stock_s_toggle_and_keeps_the_stars(fw, ui, song, mcl):
    heart = Heart(fw, ui, song, mcl, kept=3)
    log = (C.c_long * 3)()
    stock_stub(FAV_CLICK, 0, log)
    event = C.create_string_buffer(0x40)
    ui.handler(heart.heart, EVT_CLICK)(C.addressof(event))
    assert list(log[:2]) == [Heart.CTX, C.addressof(event)]
    assert heart.kept() == 3 and heart.copy_due() and heart.stars() == 3


@in_fork
def test_the_heart_on_a_card_app_s_streamed_song_is_the_app_s(fw, ui, song, mcl):
    """A song of an app's URL queue whose line named an item (tidal.c app_fav): the press goes to the app's
    fav.log and the heart flips at once in stock's Tidal images, stock's click (Tidal's API) never run."""
    heart = Heart(fw, ui, song, mcl)
    log = (C.c_long * 3)()
    stock_stub(FAV_CLICK, 0, log)
    assert urls_play(fw, heart.files, HEARTS) == 1
    tidal_id = C.create_string_buffer(b'app:1:0')  # its line said a favourite
    C.c_void_p.from_address(heart.track + O['REC_TIDAL_ID']).value = C.addressof(tidal_id)
    mcl.type = 0xf1
    assert ui.handler(heart.heart, EVT_CLICK)() == 0 and ui.image[heart.heart] == 'tidal_fav_rm'
    assert ui.handler(heart.heart, EVT_CLICK)() == 0 and ui.image[heart.heart] == 'tidal_fav_add'
    assert heart.files.files['/mnt/mmc/.a/fav.log'] == b'\nitem\t0\tid-a\n\nitem\t1\tid-a\n'
    assert not log[0] and not ui.toasts


@in_fork
def test_an_app_s_streamed_song_without_an_item_has_no_heart(fw, ui, song, mcl):
    """Its line named no item: the press says so and never reaches stock's click, which would call Tidal."""
    heart = Heart(fw, ui, song, mcl)
    log = (C.c_long * 3)()
    stock_stub(FAV_CLICK, 0, log)
    urls_play(fw, heart.files, HEARTS)
    tidal_id = C.create_string_buffer(b'app:1:2')
    C.c_void_p.from_address(heart.track + O['REC_TIDAL_ID']).value = C.addressof(tidal_id)
    mcl.type = 0xf1
    assert ui.handler(heart.heart, EVT_CLICK)() == 0
    assert not log[0] and ui.toasts == ['This app has no favorites'] and ui.image[heart.heart] == 'play_unfav'
    assert '/mnt/mmc/.a/fav.log' not in heart.files.files


@in_fork
def test_a_streamed_song_s_press_the_card_wont_take_says_so(fw, ui, song, mcl):
    heart = Heart(fw, ui, song, mcl)
    urls_play(fw, heart.files, HEARTS)
    tidal_id = C.create_string_buffer(b'app:1:1')
    C.c_void_p.from_address(heart.track + O['REC_TIDAL_ID']).value = C.addressof(tidal_id)
    mcl.type = 0xf1
    fw.mock('fw_fopen', lambda *_: 0)
    assert ui.handler(heart.heart, EVT_CLICK)() == 0
    assert ui.image[heart.heart] == 'play_unfav' and ui.toasts == ['Favorite not saved']


@in_fork
@pytest.mark.parametrize('fav, told', [(True, b'1'), (False, b'0')])
def test_the_heart_on_a_song_an_app_synced_is_stock_s_and_told_to_the_app(fw, ui, song, mcl, fav, told):
    """A song in a card app's favdir (q2-jellyfin's Jellyfin): stock's My Fav toggle as ever, then the
    favourite as it now is goes to the app's fav.log."""
    path = b'/mnt/mmc/Jellyfin/A/now.flac'
    heart = Heart(fw, ui, song, mcl, path=path)
    log = (C.c_long * 3)()
    stock_stub(FAV_CLICK, 0, log)
    favdir_card(fw, heart.files.files)
    heart.fav = fav  # stock's toggle has made it so
    assert ui.handler(heart.heart, EVT_CLICK)() == 0 and log[0] == Heart.CTX
    assert heart.files.files['/mnt/mmc/.jf/fav.log'] == b'\npath\t' + told + b'\t' + path + b'\n'


def test_a_full_ratings_table_says_so(fw, ui, song, mcl):
    heart = Heart(fw, ui, song, mcl, table=b''.join(struct.pack('<I', (i + 1) << 3 | 1) for i in range(RATE_SLOTS)))
    assert heart.rate(1) == 0
    assert ui.toasts == ['Ratings are full'] and heart.kept() == 0


def test_a_rating_that_cannot_be_written_says_so(fw, ui, song, mcl):
    heart = Heart(fw, ui, song, mcl, kept=2)
    heart.files.fail_rename = True
    assert heart.rate(3) == 0
    assert ui.toasts == ['Rating not saved'] and heart.kept() == 2 and heart.stars() == 2


# Chapters.
def ffmetadata(*chapters):
    """ffmpeg's -f ffmetadata output: (start seconds, title) chapters, an empty title untitled."""
    rows = b''.join(b'[CHAPTER]\nTIMEBASE=1/1000\nSTART=%d\nEND=%d\n%s' % (s * 1000, s * 1000 + 1, b'title=%s\n' % t if t else b'')
                    for s, t in chapters)
    return b';FFMETADATA1\ntitle=The Book\n' + rows


class Book:
    """ffmpeg behind chapters.c's pipe2 and fork: its output in one read, then its exit; the playing track,
    the player's place in it, and its seeks."""
    def __init__(self, fw, ui, song, mcl, path=b'/mnt/mmc/Audiobooks/book.m4b', output=b'', status=0, cue=0, end=0,
                 files=None):
        self.fw, self.ui, self.output, self.status, self.chunks = fw, ui, output, status, [output]
        self.killed, self.waits, self.closed = [], [], []
        self.errno = C.c_int(0)
        def pipe2(p, flags, *_):
            C.cast(p, C.POINTER(C.c_int))[0], C.cast(p, C.POINTER(C.c_int))[1] = 5, 6
        def read(fd, buf, n, *_):
            if not self.chunks:
                return 0
            chunk = self.chunks.pop(0)
            C.memmove(buf, chunk, len(chunk))
            return len(chunk)
        def waitpid(pid, status, options, *_):
            self.waits.append(options)
            C.cast(status, C.POINTER(C.c_int))[0] = self.status
            return 123
        fw.mock('chapter_pipe2', pipe2)
        fw.mock('fw_fork', lambda *_: 123)
        fw.mock('fw_read', read)
        fw.mock('fw_waitpid', waitpid)
        fw.mock('fw_close', lambda fd, *_: self.closed.append(fd))
        fw.mock('chapter_kill', lambda pid, sig, *_: self.killed.append((pid, sig)))
        fw.mock('fw___errno_location', lambda *_: C.addressof(self.errno))
        files = files or {path: b'\0' * 64}  # no moov: no index to fit
        self.files = Files(fw, {k.decode() if isinstance(k, bytes) else k: v for k, v in files.items()})
        self.track = song(path, b'Book', cue=cue, end=end)
        fw.var('fw_ctx_mcl_pdeqplaylist', C.c_uint).value = ui.deques.new([self.track])
        mcl.type, mcl.pos = LIBRARY, 0
        self.at, self.state = 0, 2
        fw.mock('player_playtime_and_length', lambda sec, total, *_: C.c_int.from_address(sec).__setattr__('value', self.at))
        fw.mock('mclGetPlayStatus', lambda *_: self.state)

    def open(self):
        self.fw.fn('navigation__chapters_open', None, C.c_void_p)(self.track)
        return self.ui.top() if self.ui.windows else None

    def selected(self, page):
        return self.ui.props.get((self.ui.view(page), '_ringnav_index'))


def test_chapters_load_then_list_with_their_times(fw, ui, song, mcl):
    book = Book(fw, ui, song, mcl, output=ffmetadata((0, b'Opening'), (754, b''), (3725, b'Caf\xc3\xa9')))
    book.at = 800
    page = book.open()
    assert ui.name[page] == 'chapters_page' and 'Loading chapters…' in ui.shown(page)
    assert [ms for _, ms in ui.pending('chapter_poll')] == [50]
    assert ui.fire('chapter_poll') == 0 and not ui.pending('chapter_poll')
    assert ui.rows(page) == [('Opening', '0:00:00'), ('Chapter 2', '0:12:34'), ('Café', '1:02:05')]
    assert book.selected(page) == 1  # the chapter playing


@pytest.mark.parametrize('at, playing', [(0, 0), (753, 0), (754, 1), (3724, 1), (3725, 2), (9999, 2)])
def test_chapters_open_on_the_one_playing(fw, ui, song, mcl, at, playing):
    book = Book(fw, ui, song, mcl, output=ffmetadata((0, b'One'), (754, b'Two'), (3725, b'Three')))
    book.at = at
    page = book.open()
    ui.fire('chapter_poll')
    assert book.selected(page) == playing


def test_chapters_poll_until_ffmpeg_ends(fw, ui, song, mcl):
    book = Book(fw, ui, song, mcl, output=ffmetadata((0, b'One')))
    book.chunks = []  # nothing written yet, and the pipe still open
    fw.mock('fw_read', lambda *_: (book.errno.__setattr__('value', 11), -1)[1])  # EAGAIN
    fw.mock('fw_waitpid', lambda *_: 0)  # still running
    page = book.open()
    assert ui.fire('chapter_poll') == REPEAT and 'Loading chapters…' in ui.shown(page)


@pytest.mark.parametrize('output, status, hint', [(b';FFMETADATA1\ntitle=x\n', 0, 'This file has no chapter marks'),
                                                  (b'', 256, "This file's chapters couldn't be read")])
def test_chapters_none_or_unreadable_say_so(fw, ui, song, mcl, output, status, hint):
    book = Book(fw, ui, song, mcl, output=output, status=status)
    page = book.open()
    ui.fire('chapter_poll')
    assert empty_state(ui, page) == ('list-numbers', 'No Chapters', hint, None) and ui.rows(page) == []


def test_a_cue_track_s_chapters_are_on_its_own_clock(fw, ui, song, mcl):
    chapters = [(0, b'Intro'), (300, b'A'), (900, b''), (1500, b'C'), (1800, b'D'), (2400, b'E')]
    book = Book(fw, ui, song, mcl, output=ffmetadata(*chapters), cue=600, end=1800)
    page = book.open()
    ui.fire('chapter_poll')
    # the one playing at its start shows at 0:00, earlier ones go, and so do those from its end on
    assert ui.rows(page) == [('A', '0:00:00'), ('Chapter 3', '0:05:00'), ('C', '0:15:00')]


def test_a_spoken_mp3_opens_chapters(fw, ui, song, mcl):
    fw.mock('tk_str_start_with', lambda s, prefix, *_: C.string_at(s).startswith(C.string_at(prefix)))
    page = Book(fw, ui, song, mcl, path=b'/mnt/mmc/Podcasts/show.mp3', output=ffmetadata((0, b'One'))).open()
    ui.fire('chapter_poll')
    assert ui.rows(page) == [('One', '0:00:00')]


def test_a_song_has_no_chapters(fw, ui, song, mcl):
    fw.mock('tk_str_start_with', lambda s, prefix, *_: C.string_at(s).startswith(C.string_at(prefix)))
    assert Book(fw, ui, song, mcl, path=b'/mnt/mmc/Music/song.mp3').open() is None and not fw.calls('fw_fork')


def box(kind, payload):
    return struct.pack('>I', 8 + len(payload)) + kind + payload


def test_a_book_too_long_for_ram_says_so(fw, ui, song, mcl):
    samples = 2 ** 20  # 28 MB of index against 20 MB free
    stsz = box(b'stsz', struct.pack('>3I', 0, 0, samples))
    mp4 = box(b'moov', box(b'trak', box(b'mdia', box(b'minf', box(b'stbl', stsz)))))
    path = b'/mnt/mmc/Audiobooks/long.m4b'
    book = Book(fw, ui, song, mcl, path=path, files={path: mp4, '/proc/meminfo': b'MemAvailable:   20000 kB\n'})
    assert book.open() is None and ui.toasts == ['Too long to read chapters on the Q2'] and not fw.calls('fw_fork')


def test_chapters_open_once(fw, ui, song, mcl):
    book = Book(fw, ui, song, mcl, output=ffmetadata((0, b'One')))
    book.open()
    book.open()
    assert len(ui.windows) == 1 and len(fw.calls('fw_fork')) == 1


def test_chapters_without_a_page_reap_ffmpeg(fw, ui, song, mcl, st):
    book = Book(fw, ui, song, mcl, output=ffmetadata((0, b'One')))
    fw.mock('window_create', lambda *_: 0)
    assert book.open() is None
    assert book.killed == [(123, 9)] and book.waits == [0]  # killed, then waited for: no zombie
    assert not st('ch', C.c_void_p).value and not ui.pending('chapter_poll')


def test_chapters_without_a_poll_close_their_page_and_reap_ffmpeg(fw, ui, song, mcl, st):
    book = Book(fw, ui, song, mcl, output=ffmetadata((0, b'One')))
    fw.mock('timer_add', lambda *_: 0)  # no timer to be had
    book.open()
    assert ui.backs == 1 and book.killed == [(123, 9)] and book.waits == [0] and not st('ch', C.c_void_p).value


def test_chapters_closed_while_loading_end_ffmpeg_and_free_on_the_next_poll(fw, ui, song, mcl, st):
    book = Book(fw, ui, song, mcl, output=ffmetadata((0, b'One')))
    ui.close(book.open())
    assert book.killed == [(123, 9)] and st('ch', C.c_void_p).value  # the poll still owns it
    assert ui.fire('chapter_poll') == 0 and not st('ch', C.c_void_p).value
    book.open()
    assert len(ui.windows) == 1  # and it opens again


def test_chapters_closed_once_loaded_free_at_once(fw, ui, song, mcl, st):
    book = Book(fw, ui, song, mcl, output=ffmetadata((0, b'One')))
    page = book.open()
    ui.fire('chapter_poll')
    ui.close(page)
    assert not book.killed and not st('ch', C.c_void_p).value


@pytest.mark.parametrize('state', [2, 3])
def test_a_chapter_row_seeks_there_and_goes_back(fw, ui, song, mcl, st, state):
    book = Book(fw, ui, song, mcl, output=ffmetadata((0, b'One'), (95, b'Two')))
    book.state = state
    page = book.open()
    ui.fire('chapter_poll')
    ui.click(page, 1)
    assert ui.backs == 1 and st('scrub_to').value == 95
    if state == 2:  # playing: the player's own seek
        assert [c[1][0] for c in fw.calls('player_seek_time')] == [95]
    else:  # paused: the file's second, waiting for Play
        assert [c[1][0] for c in fw.calls('mclSetSeek')] == [95]


def test_a_cue_track_s_chapter_seeks_in_the_file(fw, ui, song, mcl):
    book = Book(fw, ui, song, mcl, output=ffmetadata((0, b'A'), (700, b'B')), cue=600)
    book.state = 3
    page = book.open()
    ui.fire('chapter_poll')
    ui.click(page, 1)  # B, 100 s into the track
    assert [c[1][0] for c in fw.calls('mclSetSeek')] == [700]


@pytest.mark.parametrize('change', ['stopped', 'track'])
def test_a_chapter_row_only_goes_back_once_the_book_is_not_playing(fw, ui, song, mcl, change):
    book = Book(fw, ui, song, mcl, output=ffmetadata((0, b'One'), (95, b'Two')))
    page = book.open()
    ui.fire('chapter_poll')
    if change == 'stopped':
        book.state = 1
    else:
        mcl.pos = 1  # another track on
        ui.deques[fw.var('fw_ctx_mcl_pdeqplaylist', C.c_uint).value].append(song(b'/m/next.flac'))
    ui.click(page, 1)
    assert ui.backs == 1 and not fw.calls('player_seek_time') and not fw.calls('mclSetSeek')


# Tidal Downloads, and a card app's local songs.
class Card(Files):
    """The card: files with their mtimes, its listings as stock's toolsLoadDirectory gives them (audio files, and
    a folder in Tidal's), and access. Its records come from the test's own song, so none shares another's room."""
    def __init__(self, fw, ui, song, songs):
        super().__init__(fw, {p.decode(): b'x' for p in songs})
        self.fw, self.ui, self.mtime = fw, ui, dict(songs)
        self.records = {p: song(p) for p in songs if p.endswith((b'.flac', b'.m4a'))}
        self.folder = song(b'/mnt/mmc/.tidal/old', kind=4)
        fw.mock('toolsLoadDirectory', self.listing)
        fw.mock('fw___xstat', self.stat)
        fw.mock('fw_access', lambda p, *_: 0 if C.string_at(p).decode() in self.files else -1)
        self.ents, self.dirs = [], {}
        fw.mock('fw_opendir', self.opendir)
        fw.mock('fw_readdir', self.readdir)
        fw.mock('fw_closedir', lambda d, *_: self.dirs.pop(d) and 0)

    def listing(self, path, *_):
        path = C.string_at(path)
        rows = [r for p, r in self.records.items() if (p.rsplit(b'/', 1)[0] or b'/') == path and p.decode() in self.files]
        rows += [self.folder] if path == TIDAL_DIR.encode() else []
        self.ui.deques[self.fw.var('fw_ctx_tools_pdeq_directory', C.c_uint).value].extend(rows)
        return len(rows)

    def stat(self, version, path, info, *_):
        path = C.string_at(path)
        if path not in self.mtime or path.decode() not in self.files:
            return -1
        C.c_uint.from_address(info + 64).value = self.mtime[path]
        return 0

    def opendir(self, path, *_):
        path = C.string_at(path).decode()
        names = [p[len(path) + 1:] for p in self.files if p.startswith(path + '/')]
        self.dirs[0x3100 + len(self.ents)] = names
        return 0x3100 + len(self.ents)

    def readdir(self, d, *_):
        if not self.dirs[d]:
            return 0
        ent = C.create_string_buffer(268)
        name = self.dirs[d].pop(0).encode()
        C.memmove(C.addressof(ent) + 11, name, len(name))
        self.ents.append(ent)
        return C.addressof(ent)

    def paths(self, queue):
        return [field(r, 'path') for r in queue]


TIDAL_SONGS = {b'/mnt/mmc/.tidal/111-LOSSLESS Old One.flac': 100, b'/mnt/mmc/.tidal/222-HI_RES New One.flac': 300,
               b'/mnt/mmc/.tidal/333-HIGH Middle.m4a': 200}


def test_tidal_downloads_lists_newest_first_then_clear(fw, ui, song):
    card = Card(fw, ui, song, TIDAL_SONGS)
    call(fw, 'tidal_downloads')
    page = ui.top()
    assert ui.name[page] == 'tidal_downloads'
    assert ui.captions(page) == ['New One', 'Middle', 'Old One', 'Clear All Downloads…']  # no folder
    ui.click(page, 1)
    (queue, at, cls), = ui.played
    assert card.paths(queue) == sorted(TIDAL_SONGS, key=lambda p: -TIDAL_SONGS[p]) and at == 1 and cls == LIBRARY


def test_a_download_gone_from_the_card_does_not_play(fw, ui, song):
    card = Card(fw, ui, song, TIDAL_SONGS)
    call(fw, 'tidal_downloads')
    del card.files['/mnt/mmc/.tidal/333-HIGH Middle.m4a']
    ui.click(ui.top(), 1)
    assert not ui.played


def test_no_downloads_says_so(fw, ui, song):
    Card(fw, ui, song, {})
    call(fw, 'tidal_downloads')
    assert empty_state(ui, ui.top()) == ('download-simple', 'No Downloads Yet', 'Synced music shows up here', None)
    assert ui.rows(ui.top()) == []  # no Clear All Downloads over nothing


def td_card(fw, ui):
    """Clear All Downloads' confirmation card (feedback.c confirm, on stock's sortselect_dialog): asked, and its body."""
    return 'dialog/sortselect_dialog' in ui.went, C.string_at(C.addressof(fw.var('library_lists__td_body', C.c_char * 48))).decode()


def td_answer(fw, row):
    fw.fn('library_lists__td_answer', None, C.c_void_p, C.c_int, C.c_int)(None, row, 0)


def test_clear_all_asks_on_the_card_and_cancel_keeps_them(fw, ui, song):
    card = Card(fw, ui, song, TIDAL_SONGS)
    call(fw, 'tidal_downloads')
    page = ui.top()
    ui.click(page, 3)
    assert td_card(fw, ui) == (True, '3 tracks. Settings are kept.')
    td_answer(fw, 0)  # Cancel
    ui.fire('td_show')
    assert ui.captions(page)[-1] == 'Clear All Downloads…' and len(card.files) == 3
    assert ui.props[(ui.view(page), '_ringnav_index')] == 3  # back on Clear


def test_return_on_the_list_leaves(fw, ui, song):
    Card(fw, ui, song, TIDAL_SONGS)
    call(fw, 'tidal_downloads')
    page = ui.top()
    assert ui.key(page, KEY_RETURN) == STOP and ui.backs == 1


def test_remove_clears_every_download(fw, ui, song):
    card = Card(fw, ui, song, {**TIDAL_SONGS, b'/mnt/mmc/.tidal/.cache-size': 1})
    call(fw, 'tidal_downloads')
    page = ui.top()
    ui.click(page, 3)
    td_answer(fw, 1)  # Remove
    ui.fire('td_show')
    assert list(card.files) == ['/mnt/mmc/.tidal/.cache-size']  # Settings are kept
    assert empty_state(ui, page)[1] == 'No Downloads Yet'


def test_one_download_is_a_track(fw, ui, song):
    Card(fw, ui, song, {b'/mnt/mmc/.tidal/1-HIGH A.flac': 5})
    call(fw, 'tidal_downloads')
    ui.click(ui.top(), 1)
    assert td_card(fw, ui)[1] == '1 track. Settings are kept.'


def test_an_answer_after_the_page_closed_rebuilds_nothing(fw, ui, lists, song):
    Card(fw, ui, song, TIDAL_SONGS)
    call(fw, 'tidal_downloads')
    page = ui.top()
    ui.click(page, 3)
    ui.close(page)
    td_answer(fw, 0)
    assert not ui.pending('td_show') and not lists('td_list', C.c_void_p).value
    call(fw, 'tidal_downloads')
    assert ui.captions(ui.top())[-1] == 'Clear All Downloads…'


APP = b'/mnt/mmc/Deezer/Artist/Album'
APP_SONGS = {APP + b'/02 - Second [22].flac': 300, APP + b'/01 - First [11].flac': 100, APP + b'/03 - Third.flac': 200}


def test_a_card_app_s_album_lists_in_track_order(fw, ui, song):
    card = Card(fw, ui, song, APP_SONGS)
    assert fw.fn('songs_open', C.c_int, C.c_char_p, C.c_char_p, C.c_int)(APP + b'/', b'Album', 1) == 0
    page = ui.top()
    assert ui.name[page] == 'app_songs' and ui.captions(page) == ['01 - First', '02 - Second', '03 - Third']
    ui.click(page, 2)
    assert card.paths(ui.played[0][0]) == sorted(APP_SONGS) and ui.played[0][1:] == (2, LIBRARY)


def test_a_card_app_s_folder_lists_newest_first(fw, ui, song):
    Card(fw, ui, song, APP_SONGS)
    fw.fn('songs_open', C.c_int, C.c_char_p, C.c_char_p, C.c_int)(APP + b'/', b'Downloads', 0)
    assert ui.captions(ui.top()) == ['02 - Second', '03 - Third', '01 - First']


def test_a_card_app_s_song_plays_its_folder_from_it(fw, ui, song):
    card = Card(fw, ui, song, APP_SONGS)
    fw.fn('songs_open', C.c_int, C.c_char_p, C.c_char_p, C.c_int)(APP + b'/03 - Third.flac', b'', 1)
    (queue, at, cls), = ui.played
    assert card.paths(queue) == sorted(APP_SONGS) and at == 2 and cls == LIBRARY and not ui.windows


def test_a_card_app_s_song_no_longer_there_says_so(fw, ui, song):
    Card(fw, ui, song, APP_SONGS)
    fw.fn('songs_open', C.c_int, C.c_char_p, C.c_char_p, C.c_int)(APP + b'/04 - Gone.flac', b'', 1)
    assert ui.toasts == ["It isn't on the card any more"] and not ui.played


@pytest.mark.parametrize('path', [b'', b'song.flac', b'/song.flac', b'/m/' + b'x' * 300])
def test_a_card_app_s_odd_path_opens_nothing(fw, ui, song, path):
    Card(fw, ui, song, {})
    assert fw.fn('songs_open', C.c_int, C.c_char_p, C.c_char_p, C.c_int)(path, b'', 1) == 0
    assert not ui.windows and not ui.played and not ui.toasts


def test_the_card_root_lists_as_a_folder(fw, ui, song):
    Card(fw, ui, song, {b'/a.flac': 1})
    fw.fn('songs_open', C.c_int, C.c_char_p, C.c_char_p, C.c_int)(b'/', b'Card', 0)
    assert ui.captions(ui.top()) == ['a']


def test_downloads_open_once(fw, ui, song):
    Card(fw, ui, song, TIDAL_SONGS)
    call(fw, 'tidal_downloads')
    fw.fn('songs_open', C.c_int, C.c_char_p, C.c_char_p, C.c_int)(APP + b'/', b'Album', 1)
    assert len(ui.windows) == 1


# The card as a tree of folders, for the pages that look at it.
class Disk(Files):
    """Files by path (a folder is what holds one, or one of folders); opendir lists a folder's names, a folder's
    with d_type 4, and access finds either."""
    def __init__(self, fw, files=None, folders=()):
        super().__init__(fw, dict(files or {}))
        self.folders, self.ents, self.dirs = set(folders), [], {}
        fw.mock('fw_opendir', self.opendir)
        fw.mock('fw_readdir', self.readdir)
        fw.mock('fw_closedir', lambda d, *_: self.dirs.pop(d) and 0)
        fw.mock('fw_access', lambda p, *_: 0 if self.exists(C.string_at(p).decode()) else -1)

    def folder(self, path):
        return path in self.folders or any(p.startswith(path + '/') for p in [*self.files, *self.folders])

    def exists(self, path):
        return path in self.files or self.folder(path)

    def opendir(self, path, *_):
        path = C.string_at(path).decode()
        if not self.folder(path):
            return 0
        names = sorted({p[len(path) + 1:].split('/')[0] for p in [*self.files, *self.folders] if p.startswith(path + '/')})
        handle = 0x3100 + len(self.ents) + len(self.dirs)
        self.dirs[handle] = [(n, 4 if self.folder(f'{path}/{n}') else 8) for n in names]
        return handle

    def readdir(self, d, *_):
        if not self.dirs[d]:
            return 0
        name, kind = self.dirs[d].pop(0)
        ent = C.create_string_buffer(268)
        struct.pack_into('<B', ent, 10, kind)
        C.memmove(C.addressof(ent) + 11, name.encode(), len(name))
        self.ents.append(ent)
        return C.addressof(ent)


# Streaming's Configuration (two levels, 25 v3 B: docs/oled-design.md#streaming-configuration).
def configuration(fw, ui, files=None, hide=None):
    disk = Disk(fw, files)
    config = Config(fw, **({} if hide is None else {'STREAMHIDE': hide}))
    call(fw, 'stream_config')
    return ui.top(), disk, config, config.written


def provider(fw, ui, page, sp):
    """TIDAL's (sp 0) or Spotify's page, opened from its row on Configuration."""
    ui.click(page, 3 + sp)
    return ui.top()


def switches(ui, page, first=0):
    """The switch rows' captions and switches, from row first."""
    out = []
    for item in ui.kids[ui.view(page)][first:]:
        label, switch = ui.kids[item][0], ui.lookup(item, '_setting_switch', 0)
        if switch:
            out.append((ui.text[label], ui.image[switch] == 'switch_on'))
    return out


def test_configuration_is_stop_the_providers_then_the_lone_switches(fw, ui):
    page, *_ = configuration(fw, ui, hide=b'RADIO,SPOTIFY')
    assert ui.name[page] == 'streamconf_page'
    # the headers are whole rows, so the list scrolls in whole rows (audit2 E1)
    assert ui.rows(page)[:6] == [('Stop Streaming Services', None), ('Download Ahead', '2 Songs'), ('Services', None),
                                 ('TIDAL', 'Shown'), ('Spotify', 'Hidden'), ('Show in Streaming', None)]
    rows = ui.kids[ui.view(page)]
    assert all(any(ui.image.get(k) == 'list_into' for k in ui.kids[rows[r]]) for r in (3, 4))  # each opens a page
    assert switches(ui, page, 6) == [('Internet Radio', False), ('AirPlay', True)]  # Tidal Downloads is TIDAL's
    call(fw, 'stream_config')
    assert ui.windows == [page]


@pytest.mark.parametrize('sp, rows', [
    (0, [('Show in Streaming', None), ('Show Downloads', None), ('Cache', '2 GB')]),
    (1, [('Show in Streaming', None), ('Cache', '1 GB'), ('Erase Spotify Data', None)])])
def test_a_provider_row_opens_its_page(fw, ui, sp, rows):
    page, *_ = configuration(fw, ui, {f'{SPOT_DIR}/.cache-size': b'1024M'}, hide=b'DOWNLOADS')
    sub = provider(fw, ui, page, sp)
    assert ui.name[sub] == 'streamsvc_page' and ui.rows(sub) == rows
    assert switches(ui, sub) == [('Show in Streaming', True)] + ([('Show Downloads', False)] if not sp else [])


@pytest.mark.parametrize('saved, shown', [(b'512M', '512 MB'), (b'8192M', '8 GB'), (b'3000M', '2 GB'), (b'', '2 GB')])
def test_a_cache_size_reads_back(fw, ui, saved, shown):
    page, *_ = configuration(fw, ui, {f'{TIDAL_DIR}/.cache-size': saved})
    assert ui.rows(provider(fw, ui, page, 0))[2] == ('Cache', shown)


@pytest.mark.parametrize('sp, folder, saved, after, shown', [
    (0, TIDAL_DIR, None, b'4096M', '4 GB'), (1, SPOT_DIR, b'4096M', b'8192M', '8 GB'), (0, TIDAL_DIR, b'8192M', b'512M', '512 MB')])
def test_a_cache_row_steps_to_the_next_size_and_keeps_it(fw, ui, sp, folder, saved, after, shown):
    page, disk, *_ = configuration(fw, ui, {} if saved is None else {f'{folder}/.cache-size': saved})
    sub = provider(fw, ui, page, sp)
    row = 2 - sp
    ui.click(sub, row)
    assert disk.files[f'{folder}/.cache-size'] == after  # librespot's --cache-size-limit form
    assert ui.rows(sub)[row] == ('Cache', shown) and [C.string_at(c[1][0]).decode() for c in fw.calls('fw_mkdir')] == [folder]


def test_a_cache_size_that_cannot_be_kept_says_so(fw, ui):
    page, disk, *_ = configuration(fw, ui)
    sub = provider(fw, ui, page, 0)
    disk.fail_rename = True
    ui.click(sub, 2)
    assert ui.toasts == ['Could not save setting'] and ui.rows(sub)[2] == ('Cache', '2 GB')


def erasing(fw):
    """netupdate.c's erase: its thread only starts."""
    fw.mock('fw_pthread_create', lambda *_: 0)


def erase_answer(fw, row):
    """Erase Spotify Data's confirmation card answered (stream_config.c sc_erase_answer), as feedback.c runs it."""
    fw.fn('stream_config__sc_erase_answer', None, C.c_void_p, C.c_int, C.c_int)(None, row, 0)


def test_erase_asks_on_the_card_first(fw, ui):
    erasing(fw)
    page, *_ = configuration(fw, ui)
    sub = provider(fw, ui, page, 1)
    ui.click(sub, 2)
    assert 'dialog/sortselect_dialog' in ui.went and not fw.calls('fw_system')  # the card, nothing erased
    erase_answer(fw, 0)  # Cancel
    assert ui.captions(sub)[2] == 'Erase Spotify Data' and not fw.calls('fw_system')
    erase_answer(fw, 1)  # Erase
    assert ui.captions(sub)[2] == 'Erasing…' and fw.calls('fw_system')  # librespot stopped first


def test_while_erasing_spotify_s_cache_and_erase_wait(fw, ui):
    erasing(fw)
    page, disk, *_ = configuration(fw, ui)
    sub = provider(fw, ui, page, 1)
    erase_answer(fw, 1)
    ui.click(sub, 1)
    assert ui.toasts == ['Wait for the one in progress'] and f'{SPOT_DIR}/.cache-size' not in disk.files
    asked = ui.went.count('dialog/sortselect_dialog')
    ui.click(sub, 2)  # no card while one runs: it says to wait
    assert ui.toasts[-1] == 'Wait for the one in progress' and ui.captions(sub)[2] == 'Erasing…'
    assert ui.went.count('dialog/sortselect_dialog') == asked


def test_an_erase_s_end_shows_on_the_page(fw, ui):
    page, disk, *_ = configuration(fw, ui, {f'{SPOT_DIR}/.cache-size': b'1024M'})
    sub = provider(fw, ui, page, 1)
    del disk.files[f'{SPOT_DIR}/.cache-size']  # netupdate.c's erase took it, and calls back
    fw.fn('stream_erased', None)()
    assert ui.rows(sub)[1:3] == [('Cache', '2 GB'), ('Erase Spotify Data', None)]


def test_an_erase_s_end_with_the_pages_gone_writes_nothing(fw, ui):
    page, *_ = configuration(fw, ui)
    ui.close(provider(fw, ui, page, 1))
    ui.close(page)
    writes = len(fw.calls('widget_set_text_utf8'))
    fw.fn('stream_erased', None)()
    assert len(fw.calls('widget_set_text_utf8')) == writes


@pytest.mark.parametrize('kind, status, stops', [(0x2000, 2, 1), (0x2000, 1, 0), (LIBRARY, 2, 0), (FOLDER, 2, 0)])
def test_stop_streaming_ends_streams_and_leaves_local_music(fw, ui, mcl, kind, status, stops):
    page, *_ = configuration(fw, ui)
    mcl.type = kind
    fw.mock('mclGetPlayStatus', lambda *_: status)
    ui.click(page, 0)
    assert ui.toasts == ['Streaming stopped'] and len(fw.calls('player_stop')) == stops
    assert fw.calls('fw_system')  # librespot's loop ended whatever played


@pytest.mark.parametrize('saved, shown', [(None, '2 Songs'), (b'0', 'Off'), (b'1', '1 Song'), (b'3', '3 Songs'), (b'7', '2 Songs')])
def test_download_ahead_reads_back(fw, ui, saved, shown):
    Disk(fw)
    Config(fw, **({} if saved is None else {'AHEAD': saved}))
    call(fw, 'stream_config')
    assert ui.rows(ui.top())[1] == ('Download Ahead', shown)


def test_download_ahead_steps_off_one_two_three_and_keeps_it(fw, ui):
    page, *_ = configuration(fw, ui)
    kept = []
    fw.mock('write_int_config', lambda v, section, key, *_: kept.append((C.string_at(section), C.string_at(key), v)))
    for shown in ('3 Songs', 'Off', '1 Song', '2 Songs'):
        ui.click(page, 1)
        assert ui.rows(page)[1] == ('Download Ahead', shown)
    assert kept == [(b'Q2POD', b'AHEAD', n) for n in (3, 0, 1, 2)]


def test_a_service_row_hides_it(fw, ui):
    page, _, config, written = configuration(fw, ui, hide=b'TIDAL')
    ui.click(page, 6)
    assert written == [b'TIDAL,RADIO'] and switches(ui, page, 6)[0] == ('Internet Radio', False)
    sub = provider(fw, ui, page, 0)
    ui.click(sub, 0)  # TIDAL's Show in Streaming: shown again, and its row on Configuration says so
    assert written[-1] == b'RADIO' and switches(ui, sub)[0] == ('Show in Streaming', True)
    assert ui.rows(page)[3] == ('TIDAL', 'Shown')


# Stock's pages the payload rearranges: Streaming, the Library and About.
def stock_row(ui, view, caption, image=None, name=None):
    """A stock list row: a list_item holding a button with its icon and its label."""
    item = ui.new('list_item_create', view, 0, 0, 375, 70)
    button = ui.new('button_create', item, 20, 0, 335, 70)
    if image:
        ui.image[ui.new('image_create', button, 10, 0, 52, 70)] = image
    ui.text[ui.new('hscroll_label_create', button, 72, 0, 260, 70)] = caption
    if name:
        ui.name[button] = name
    return item


def row_text(ui, item):
    return next((ui.text[w] for w in ui.subtree(item) if w in ui.text), None)


def stock_page(ui, view_name, *rows):
    win = ui.new('window_create', 0, 0, 0, 0, 0)
    view = ui.new('scroll_view_create', win, 0, 0, 375, 320)
    ui.name[view] = view_name
    for row in rows:
        stock_row(ui, view, *row)
    return win, view


def page_rows(ui, view):
    """The view's rows in order: (caption, shown)."""
    return [(row_text(ui, item), item not in ui.hidden) for item in ui.kids[view]]


STREAM_STOCK = [('TIDAL', 'list_tidal'), ('Baidu Netdisk', 'list_baidu'), ('Qobuz', 'list_qobuz')]


def streaming(fw, ui, hide=None, files=None):
    Disk(fw, files)
    Config(fw, **({} if hide is None else {'STREAMHIDE': hide}))
    fw.mock('stock_stream_trampoline', lambda *_: 7)
    win, view = stock_page(ui, 'scroll_view_streamsset', *STREAM_STOCK)
    assert fw.fn('ringnav_stream', C.c_int, C.c_void_p, C.c_void_p)(win, None) == 7  # stock's own result
    return win, view


def deezer(name=b'Deezer'):
    return {'/mnt/mmc/.deez/q2app': b'name=%s\n' % name, '/mnt/mmc/.deez/q2-deez': b'ELF'}


def test_streaming_drops_baidu_and_adds_the_payload_s_services(fw, ui):
    win, view = streaming(fw, ui)
    assert page_rows(ui, view) == [(c, True) for c in ('TIDAL', 'Qobuz', 'Spotify', 'Tidal Downloads', 'Internet Radio',
                                                       'AirPlay', 'Configuration')]


def test_oled_marks_each_service_with_its_own_glyph(fw, ui, st):
    """OLED (docs/oled-design.md): a service's logo on its disc gives way to the theme's 20 px mark centred on x 34,
    in Text Secondary or white on the selection, the words from x 52; a row without a known logo keeps its own."""
    from test_nav_hold import use_set
    use_set(st)
    fw.mock('widget_move_resize', lambda w, x, y, ww, h, *_: struct.pack_into('<4i', ui.blocks[w], 0, *map(signed, (x, y, ww, h))))
    win, view = streaming(fw, ui)
    drawn: list = []  # image names, each followed by where it was drawn
    canvas = C.create_string_buffer(0x100)
    fw.mock('widget_load_image', lambda w, name, bitmap, *_: drawn.append(C.string_at(name).decode()) or 0)
    fw.mock('canvas_draw_image', lambda canvas, bitmap, src, dst, *_: drawn.append(struct.unpack_from('<2i', C.string_at(dst, 8))) or 0)
    paint = fw.fn('stream_mark_paint', None, C.c_void_p, C.c_void_p)
    for item in ui.kids[view]:
        struct.pack_into('<i', ui.blocks[item], 12, 32)  # the list's layout: stream_row's pitch, the payload's rows too
        paint(item, C.addressof(canvas))
    names = drawn[0::2]
    assert names == [f'oled_stream_{n}_d' for n in ('tidal', 'spotify', 'tidal', 'radio', 'airplay', 'config')]  # not Qobuz
    assert set(drawn[1::2]) == {(24, 6)}  # x 24 on the 375 px row, centred in the 32 px row
    logos = [w for item in ui.kids[view] for w in ui.subtree(item) if ui.image.get(w, '').startswith(('list_tidal', 'stream_'))]
    assert logos and all(w in ui.hidden for w in logos)
    tidal = ui.kids[view][0]
    label, = (w for w in ui.subtree(tidal) if ui.kind[w] == 'hscroll_label_create')
    assert struct.unpack_from('<i', ui.blocks[label], 0)[0] == 52 - 20  # in its button at x 20
    drawn.clear()
    st('sel_accent', C.c_void_p).value = st('paint_w', C.c_void_p).value = tidal
    paint(tidal, C.addressof(canvas))
    assert drawn[0] == 'oled_stream_tidal_w'
    drawn.clear()
    st('sel_accent', C.c_void_p).value = ui.kids[tidal][0]  # a settings row's selection is its button
    paint(tidal, C.addressof(canvas))
    assert drawn[0] == 'oled_stream_tidal_w'


@pytest.mark.parametrize('subsonic_icon', [True, False])
def test_oled_marks_a_card_app_by_its_id_its_own_icon_or_app_window(fw, ui, st, subsonic_icon):
    """OLED's settings layout hides every leading image on a Streaming row, a card app's own icon too, so the marks
    stand for all of them: q2-subsonic (id subsonic) takes its own mark, icon files or none; another app with its own
    icon has it drawn in the mark's 20 px box centred on x 34; one without takes app-window."""
    from test_coverflow import png
    from test_nav_hold import use_set
    use_set(st)
    fw.mock('widget_move_resize', lambda w, x, y, ww, h, *_: struct.pack_into('<4i', ui.blocks[w], 0, *map(signed, (x, y, ww, h))))
    fw.mock('tk_str_start_with', lambda s, prefix, *_: bool(s) and C.string_at(s).startswith(C.string_at(prefix)))
    files = {**deezer(), '/mnt/mmc/.deez/icon-40.png': png(40, 40), '/mnt/mmc/.soul/q2app': b'name=Soulseek\n',
             '/mnt/mmc/.soul/q2-soul': b'ELF', '/mnt/mmc/.subsonic/q2app': b'name=Subsonic\n',
             '/mnt/mmc/.subsonic/q2-subsonic': b'ELF'}
    if subsonic_icon:
        files['/mnt/mmc/.subsonic/icon-40.png'] = png(40, 40)
    win, view = streaming(fw, ui, files=files)
    drawn: list = []  # image names, each followed by its box on screen
    canvas = C.create_string_buffer(0x100)
    fw.mock('widget_load_image', lambda w, name, bitmap, *_: drawn.append(C.string_at(name).decode())
            or C.memmove(bitmap, struct.pack('<2I', 40, 40), 8) and 0)
    fw.mock('canvas_draw_image', lambda canvas, bitmap, src, dst, *_: drawn.append(struct.unpack_from('<4i', C.string_at(dst, 16))) or 0)
    paint = fw.fn('stream_mark_paint', None, C.c_void_p, C.c_void_p)
    rows = dict(zip([c for c, _ in page_rows(ui, view)], ui.kids[view]))
    got = {}
    for name in ('Deezer', 'Soulseek', 'Subsonic'):
        struct.pack_into('<i', ui.blocks[rows[name]], 12, 32)  # stream_row's pitch
        drawn.clear()
        paint(rows[name], C.addressof(canvas))
        got[name] = list(drawn)
    assert got == {'Deezer': ['file:///mnt/mmc/.deez/icon-40.png', (24, 6, 20, 20)],
                   'Soulseek': ['oled_stream_app_d', (24, 6, 40, 40)], 'Subsonic': ['oled_stream_subsonic_d', (24, 6, 40, 40)]}
    icons = [w for item in ui.kids[view] for w in ui.subtree(item) if ui.image.get(w, '').startswith('file://')]
    assert len(icons) == 1 + subsonic_icon and all(w in ui.hidden for w in icons)


def test_streaming_lists_the_card_s_apps_after_spotify(fw, ui):
    win, view = streaming(fw, ui, files=deezer())
    assert [c for c, _ in page_rows(ui, view)][2:5] == ['Spotify', 'Deezer', 'Tidal Downloads']


def test_streaming_hides_what_configuration_hid(fw, ui):
    win, view = streaming(fw, ui, hide=b'TIDAL,deez,AIRPLAY', files=deezer())
    assert [c for c, shown in page_rows(ui, view) if not shown] == ['TIDAL', 'Deezer', 'AirPlay']


def test_streaming_s_airplay_opens_its_switch(fw, ui):
    win, view = streaming(fw, ui)
    item = ui.kids[view][-2]
    button, = ui.kids[item]
    ui.handler(button, EVT_CLICK)()
    assert ui.went == ['systemset/netservice_page']


def test_a_service_hidden_on_configuration_goes_from_streaming_at_once(fw, ui):
    win, view = streaming(fw, ui, hide=b'')
    button, = ui.kids[ui.kids[view][-1]]
    ui.handler(button, EVT_CLICK)()  # Configuration
    config = ui.top()
    assert ui.name[config] == 'streamconf_page'
    ui.click(config, 6)  # Internet Radio's switch
    assert dict(page_rows(ui, view))['Internet Radio'] is False
    ui.click(config, 6)
    assert dict(page_rows(ui, view))['Internet Radio'] is True
    ui.click(config, 3)  # stock's own TIDAL row too, from TIDAL's page
    ui.click(ui.top(), 0)
    assert dict(page_rows(ui, view))['TIDAL'] is False


def test_streaming_without_its_list_is_stock_s(fw, ui):
    Disk(fw)
    win = ui.new('window_create', 0, 0, 0, 0, 0)
    fw.fn('ringnav_stream', C.c_int, C.c_void_p, C.c_void_p)(win, None)
    assert ui.kids[win] == [] and not fw.calls('fw_opendir')



LIBRARY_STOCK = ['Update Local Music', 'All Songs', 'Album', 'Artist', 'Genre', 'Hi-Res', 'My Fav', 'Frequent', 'Recent',
                 'Recently Added', 'Playlist']


def library(fw, ui, folders=(), token=b'', extra=0):
    Disk(fw, folders=folders)
    Config(fw, TOKEN=token)
    fw.mock('stock_localmusic_trampoline', lambda *_: 9)
    win, view = stock_page(ui, 'scroll_view_localmusic', *[(c, None, str(i)) for i, c in enumerate(LIBRARY_STOCK)])
    for i in range(extra):
        stock_row(ui, view, 'more')
    assert fw.fn('ringnav_localmusic', C.c_int, C.c_void_p, C.c_void_p)(win, None) == 9
    return win, view


def boot(fw, st):
    """OLED's settings, on the geometry set the build bakes."""
    Config(fw)


def test_the_library_s_rows_in_their_order(fw, ui, st):
    boot(fw, st)
    win, view = library(fw, ui)
    # the menu review's order (2026-11-15, home.c ringnav_localmusic), with no Search or Games since 2026-11-19;
    # My Fav and Frequent hidden, kept in the page for stock's index: Ratings and Most Played list theirs; Shuffle,
    # Hi-Res and Long Songs hidden too, in their places; Update Local Music kept hidden, Storage's now
    hidden = ('Shuffle', 'My Fav', 'Frequent', 'Hi-Res', 'Long Songs', 'Update Local Music')
    assert page_rows(ui, view) == [(c, c not in hidden) for c in ['Shuffle', 'All Songs', 'Playlist', 'Artist', 'Album',
        'Genre', 'Years', 'Ratings', 'My Fav', 'Recently Added', 'Recent', 'Most Played', 'Frequent', 'Hi-Res',
        'Long Songs', 'Update Local Music']]


def test_the_library_adds_the_card_s_media_folders_and_upload(fw, ui):
    win, view = library(fw, ui, folders=['/mnt/mmc/podcasts', '/mnt/mmc/VIDEOS', '/mnt/mmc/Music'], token=b'x')
    rows = [c for c, _ in page_rows(ui, view)]
    assert rows[rows.index('Long Songs'):] == ['Long Songs', 'Podcasts', 'Videos', 'Upload Scrobbles',
                                               'Update Local Music']


def test_the_library_s_added_rows_have_no_name_for_stock_s_click(fw, ui):
    win, view = library(fw, ui)
    stock = {ui.kids[item][0] for item in ui.kids[view] if row_text(ui, item) in LIBRARY_STOCK}
    added = [ui.kids[item][0] for item in ui.kids[view] if row_text(ui, item) not in LIBRARY_STOCK]
    assert all(b in ui.name for b in stock) and not any(b in ui.name for b in added)
    clicks = {row_text(ui, item): fn for item in ui.kids[view] for w, e, fn, _ in ui.handlers
              if w == ui.kids[item][0] and e == EVT_CLICK}
    assert clicks['Shuffle'] == ui.fn('shuffle_menu') and clicks['Most Played'] == ui.fn('most_played')
    assert clicks['Ratings'] == ui.fn('ratings_menu') and clicks['Long Songs'] == ui.fn('long_songs')
    assert clicks['Years'] == ui.fn('era_menu')


def test_a_library_that_is_not_stock_s_is_left(fw, ui):
    win, view = library(fw, ui, extra=1)
    assert [c for c, _ in page_rows(ui, view)] == LIBRARY_STOCK + ['more']


def test_the_library_has_no_search_or_games_row_and_drops_its_icons(fw, ui, st):
    """2026-11-19: each list page has its own search bar (search_bar.c) and Home has Games and its one Search."""
    boot(fw, st)
    win, view = library(fw, ui)
    names = [c for c, _ in page_rows(ui, view)]
    assert 'Search' not in names and 'Games' not in names and ('Shuffle', False) in page_rows(ui, view)
    images = [w for item in ui.kids[view] for w in ui.subtree(item) if ui.kind[w] == 'image_create']
    icons = [w for w in images if ui.image[w] != 'list_into']
    assert icons and all(w in ui.hidden for w in icons)
    assert not [w for w in images if ui.image[w] == 'list_into' and w in ui.hidden]  # the chevrons stay


@in_fork
def test_home_s_library_list_is_the_library_s_rows(fw, ui, st):
    log = (C.c_long * 3)()
    stock_stub(O['LOCALMUSIC_BG'], 0, log)
    boot(fw, st)
    _, page = library(fw, ui)  # the Library page's own rows
    win, view = stock_page(ui, 'scroll_view_localmusic', *[(c, None, str(i)) for i, c in enumerate(LIBRARY_STOCK)])
    fw.fn('library_rows', None, C.c_void_p)(win)
    rows = [c for c, _ in page_rows(ui, view)]
    assert page_rows(ui, view) == page_rows(ui, page) and rows[:3] == ['Shuffle', 'All Songs', 'Playlist']
    assert log[0] == win  # its play gif's timer stopped, as the page's background does
    assert [c[1][:2] for c in fw.calls('widget_off_by_ctx')] == [(win, win)]


@in_fork
def test_home_s_streaming_list_is_streaming_s_rows(fw, ui):
    log = (C.c_long * 3)()
    stock_stub(O['STREAM_BG'], 0, log)
    Disk(fw)
    Config(fw)
    win, view = stock_page(ui, 'scroll_view_streamsset', *STREAM_STOCK)
    fw.fn('stream_rows', None, C.c_void_p)(win)
    assert [c for c, _ in page_rows(ui, view)][-1] == 'Configuration' and log[0] == win
    assert [c[1][:2] for c in fw.calls('widget_off_by_ctx')] == [(win, win)]


ABOUT = [('Model', 'Q2'), ('FW. Version', 'V100I'), ('Serial', '1'), ('Storage', '-'), ('Remaining', '-'),
         ('Legal', ''), ('Reset', '')]


def about(ui, rows):
    """Stock's About page: its rows, then its thanks, a 100 px item of text."""
    win = ui.new('window_create', 0, 0, 0, 0, 0)
    view = ui.new('scroll_view_create', win, 0, 0, 375, 320)
    ui.name[view] = 'scroll_view_about'
    for title, value in rows:
        button = ui.new('button_create', ui.new('list_item_create', view, 0, 0, 375, 70), 20, 0, 335, 70)
        for x, text in ((10, title), (176, value)):
            ui.text[ui.new('hscroll_label_create', button, x, 0, 149, 70)] = text
    ui.text[ui.new('hscroll_label_create', ui.new('list_item_create', view, 0, 0, 375, 100), 20, 10, 335, 100)] = 'about_more'
    return win, view


def about_rows(ui, view):
    """Each row's (title, value); the thanks, where it still stands, as its key alone."""
    firsts = [ui.kids[item][0] for item in ui.kids[view]]
    return [tuple(ui.text[label] for label in ui.kids.get(first, ())) or (ui.text[first],) for first in firsts]


def test_about_shows_stock_s_version_and_the_payload_s_after_it_without_stock_s_thanks(fw, ui):
    win, view = about(ui, ABOUT)
    fw.mock('stock_about_trampoline', lambda *_: 5)
    assert fw.fn('ringnav_about', C.c_int, C.c_void_p, C.c_void_p)(win, None) == 5
    version, beta = host.tools_build.VERSION, host.tools_build.BETA
    name = host.tools_build.PRODUCT_NAME
    assert about_rows(ui, view) == [ABOUT[0], ('FW. Version', host.tools_build.STOCK_VERSION), (name, version + (f' beta {beta}' if beta else '')),
                                    *ABOUT[2:]]


@pytest.mark.parametrize('value, shown', [('779.72G', '779.72 GB'), ('512.5M', '512.5 MB'), ('0.0.0.0', 'Not connected'),
                                          ('192.168.1.4', '192.168.1.4'), ('Shanling Q2', 'Shanling Q2')])
def test_about_s_values_take_the_approved_words(fw, ui, value, shown):
    """audit-2 #31: sizes with their unit, and no network's IP address as words."""
    win, view = about(ui, [*ABOUT[:2], ('Capacity', value), *ABOUT[3:]])
    fw.mock('stock_about_trampoline', lambda *_: 5)
    fw.fn('ringnav_about', C.c_int, C.c_void_p, C.c_void_p)(win, None)
    assert about_rows(ui, view)[3] == ('Capacity', shown)  # after the product's row


def test_an_about_page_that_is_not_stock_s_is_left(fw, ui):
    win, view = about(ui, ABOUT[:6])
    fw.fn('ringnav_about', C.c_int, C.c_void_p, C.c_void_p)(win, None)
    assert about_rows(ui, view) == [*ABOUT[:6], ('about_more',)]


def test_usb_mode_ends_the_card_s_apps_before_stock_unmounts_it(fw):
    disk = Disk(fw, {**deezer(), '/tmp/q2-deez': b'1'})  # its pid file: running
    fw.fn('addon_scan', C.c_int)()
    order = []
    fw.mock('fw_system', lambda cmd, *_: order.append('stop'))
    fw.mock('sleep_ms', lambda *_: (order.append('wait'), disk.files.pop('/tmp/q2-deez', None)) and 0)
    fw.mock('stock_usbmode_trampoline', lambda *_: order.append('stock') or 3)
    assert fw.fn('ringnav_usbmode', C.c_int, C.c_void_p, C.c_void_p, C.c_void_p, C.c_void_p)(None, None, None, None) == 3
    assert order == ['stop', 'wait', 'stock']


def test_a_track_too_long_for_ram_does_not_start(fw, ui, song, mcl):
    stsz = box(b'stsz', struct.pack('>3I', 0, 0, 2 ** 20))
    mp4 = box(b'moov', box(b'trak', box(b'mdia', box(b'minf', box(b'stbl', stsz)))))
    Files(fw, {'/m/long.m4b': mp4, '/proc/meminfo': b'MemAvailable:   20000 kB\n'})
    fw.var('fw_ctx_mcl_pdeqplaylist', C.c_uint).value = ui.deques.new([song(b'/m/long.m4b')])
    assert fw.fn('ringnav_start_player', C.c_int)() == -1 and not fw.calls('stock_start_player_trampoline')


def test_a_local_track_starts_once_the_streams_let_go(fw, ui, song, mcl):
    """mclStartPlayer's hook: a playing librespot is told to pause (and says it has) and a radio station's
    q2video to quit, both before stock opens the DAC's PCM, whose result is the hook's."""
    files = Files(fw, {'/tmp/q2-librespot.state': b'state=paused\n', '/tmp/q2radio.state': b'state=playing\n'})
    fw.var('fw_ctx_mcl_pdeqplaylist', C.c_uint).value = ui.deques.new([song(b'/m/a.flac')])
    sp = fw.var('spotify__sp', C.c_int * 8)  # launched, sock, playing, paused, local, active
    sp[2], sp[5] = 1, 1
    rd = fw.var('radio__rd', C.c_int * 3)  # pid, sock, active
    rd[0], rd[2] = 4242, 1
    clock = [0]
    fw.mock('time_now_ms', lambda *_: clock[0])
    fw.mock('sleep_ms', lambda ms, *_: clock.__setitem__(0, clock[0] + ms))
    fw.mock('fw_socket', lambda *_: 7)
    order: list[object] = []  # each datagram as (socket path, bytes), then stock's start
    fw.mock('fw_sendto', lambda s, data, n, flags, to, *_: order.append((C.string_at(to + 2), C.string_at(data, n))) or n)
    fw.mock('fw_waitpid', lambda pid, *_: pid)  # q2video quits when told
    fw.mock('stock_start_player_trampoline', lambda *_: order.append('start') or 4)
    assert fw.fn('ringnav_start_player', C.c_int)() == 4
    assert order == [(b'/tmp/q2-librespot.sock', b's'), (b'/tmp/q2radio.sock', b'q'), 'start']
    assert (sp[2], sp[5], rd[0], rd[2]) == (0, 0, 0, 0) and '/tmp/q2radio.state' not in files.files


# Era.
class EraDb:
    """The library database as sqlite3_open and _exec see it: Era's decade counts and a decade's albums, the
    statements asked, and an open that fails when told."""
    def __init__(self, fw, decades=(), albums=(), opens=True):
        self.decades, self.albums, self.opens, self.asked = decades, albums, opens, []
        self.keep = []
        def open_(path, db, *_):
            if not self.opens:
                return 14  # SQLITE_CANTOPEN
            C.c_void_p.from_address(db).value = 0xdb
            return 0
        fw.mock('sqlite3_open', open_)
        fw.mock('sqlite3_exec', self.run)

    def run(self, db, sql, callback, ctx, *_):
        sql = C.string_at(sql).decode()
        self.asked.append(sql)
        if not callback:
            return 0
        rows = self.decades if 'count(*)' in sql else self.albums
        call = C.CFUNCTYPE(C.c_int, C.c_void_p, C.c_int, C.POINTER(C.c_char_p), C.c_void_p)(callback)
        for values in rows:
            cells = (C.c_char_p * len(values))(*values)
            self.keep.append(cells)
            if call(ctx, len(values), cells, None):
                return 4  # SQLITE_ABORT
        return 0


def test_era_lists_each_decade_with_its_albums(fw, ui):
    db = EraDb(fw, decades=[(b'1970', b'1'), (b'1990', b'3'), (b'2000', b'12')])
    call(fw, 'era_menu')
    page = ui.top()
    assert ui.name[page] == 'era_page' and 'Years' in ui.shown(page)
    assert ui.rows(page) == [('70s', '1 album'), ('90s', '3 albums'), ('2000s', '12 albums')]
    assert db.asked[-1].startswith('select year/10*10,count(*)')
    call(fw, 'era_menu')
    assert ui.windows == [page]


def test_era_with_no_database_says_so(fw, ui):
    EraDb(fw, decades=[], opens=False)
    call(fw, 'era_menu')
    assert ui.toasts == ['Years unavailable'] and not ui.windows


def test_era_with_no_dated_album_opens_its_empty_state(fw, ui):
    EraDb(fw, decades=[], opens=True)
    call(fw, 'era_menu')
    (page,) = ui.windows
    assert ui.name[page] == 'era_page' and not ui.toasts and ui.rows(page) == []
    assert empty_state(ui, page) == ('calendar-blank', 'No Dated Albums Yet', 'Albums need a year tag', None)


ERA_ALBUMS = [(b'Blue', b'Band', b'1991', b'/m/blue/1.flac'), (b'Grey', b'', b'1995', b'/m/grey/1.flac'),
              (b'Red', b'Band', b'1999', b'/m/red/1.flac')]


def era_decade(fw, ui, albums=ERA_ALBUMS):
    db = EraDb(fw, decades=[(b'1970', b'1'), (b'1990', b'%d' % len(albums))], albums=albums)
    call(fw, 'era_menu')
    ui.click(ui.top(), 1)
    return db, ui.top()


def test_a_decade_lists_its_albums_by_year(fw, ui):
    db, page = era_decade(fw, ui)
    assert ui.name[page] == 'era_albums_page' and '90s' in ui.shown(page)
    assert ui.rows(page) == [('Play All', '3 albums'), ('Shuffle', 'Every song, shuffled'), ('Blue', 'Band • 1991'),
                             ('Grey', '1995'), ('Red', 'Band • 1999')]
    assert 'having release_year between 1990 and 1999' in db.asked[-1]
    # only albums with a track dated in the decade are grouped: an album's year is its latest track's
    assert "between 1990 and 1999) group by album" in db.asked[-1]


def big_decade(fw, ui, n):
    """A decade of n albums opened: (the page, its album rows' titles as made so far)."""
    albums = [(b'Album %04d' % i, b'Band', b'2011', b'/m/%d/1.flac' % i) for i in range(n)]
    EraDb(fw, decades=[(b'2010', b'%d' % n)], albums=albums)
    call(fw, 'era_menu')
    ui.click(ui.top(), 0)
    page = ui.top()
    return page, [r[0] for r in ui.rows(page)[2:]]


# The owner's 2010s held 2,664 albums: every row's widgets in one pass held the screen about 1.3 s on the Q2 (2026-11-24).
# The page opens with a screenful made, and the rest follow a chunk a pass (era_rows_due), in order.
def test_a_large_decade_opens_with_a_screenful_of_rows_and_makes_the_rest_a_chunk_a_pass(fw, ui):
    page, made = big_decade(fw, ui, 1000)
    assert ui.rows(page)[0] == ('Play All', '1000 albums') and 1 <= len(made) <= 32  # not 1,000 in the open's pass
    passes = 0
    while ui.pending('era_rows_due'):
        before = len(ui.rows(page))
        ui.fire('era_rows_due')
        assert len(ui.rows(page)) - before <= 128  # each pass bounded
        passes += 1
    made = [r[0] for r in ui.rows(page)[2:]]
    assert made == ['Album %04d' % i for i in range(1000)] and 5 <= passes <= 70


class Steps:
    """The step ui_watch.c names for the UI pass an action runs in: one late pass around each action."""
    def __init__(self, fw):
        self.fw, self.ms, self.lines = fw, 1000, []
        fw.mock('time_now_ms', lambda *_: self.ms)
        fw.var('g_backlight_status', C.c_ubyte).value = 1
        fw.mock('fw_write', lambda fd, data, n, *_: fd == 2 and self.lines.append(C.string_at(data, n).decode()) or n)
        fw.fn('ui_watch_woke', None)()

    def of(self, action):
        action()
        self.ms += 5000  # late, and past the one-line-a-second limit
        self.fw.fn('ui_watch_enter', None)()
        self.fw.fn('ui_watch_polled', None)()
        self.fw.fn('ui_watch_woke', None)()
        found = re.search(r'step ([^,]*),', self.lines[-1])
        assert found
        return found[1]


def test_years_names_each_step_of_a_decade(fw, ui, song, mcl):
    steps = Steps(fw)
    fw.mock('fw_access', lambda *_: 0)
    Library(fw, ui, [song(b'/m/1.flac', id_=1)])
    Albums(fw, ui, song)
    albums = [(b'Blue', b'Band', b'1991', b'/m/blue/1.flac')] * 40
    db = EraDb(fw, decades=[(b'1990', b'40')], albums=albums)
    assert steps.of(lambda: call(fw, 'era_menu')) == 'years list'
    assert steps.of(lambda: ui.click(ui.top(), 0)) == 'decade albums'
    page = ui.top()
    assert steps.of(lambda: ui.fire('era_rows_due')) == 'decade rows'
    db.albums = [(b'1',)]
    assert steps.of(lambda: ui.click(page, 0)) == 'decade play all'
    assert steps.of(lambda: ui.click(page, 1)) == 'decade shuffle'
    db.albums = albums
    assert steps.of(lambda: ui.click(page, 2)) == 'decade album'


def test_a_decade_closed_while_its_rows_are_made_stops_making_them(fw, ui):
    page, _ = big_decade(fw, ui, 500)
    assert ui.pending('era_rows_due')
    ui.close(page)
    assert not ui.pending('era_rows_due')


# the search bar's search reads the page's rows: they are all made first (search_bar.c)
def test_a_decade_s_rows_are_all_made_before_its_search_reads_them(fw, ui):
    page, _ = big_decade(fw, ui, 300)
    fw.fn('era_rows_finish', None, C.c_void_p)(page)
    assert len(ui.rows(page)) == 302 and not ui.pending('era_rows_due')
    fw.fn('era_rows_finish', None, C.c_void_p)(page)  # done: nothing more
    assert len(ui.rows(page)) == 302


def test_a_decade_s_albums_stop_at_its_count(fw, ui):
    EraDb(fw, decades=[(b'1990', b'1')], albums=ERA_ALBUMS)  # the albums query found more since
    call(fw, 'era_menu')
    ui.click(ui.top(), 0)
    assert ui.rows(ui.top())[0] == ('Play All', '1 album') and len(ui.rows(ui.top())) == 3


def test_a_decade_closes_and_opens_again(fw, ui):
    db, page = era_decade(fw, ui)
    era = ui.windows[0]
    ui.click(era, 0)  # another decade while one is open: nothing
    assert ui.windows == [era, page]
    ui.close(page)
    ui.click(ui.top(), 1)
    assert len(ui.rows(ui.top())) == 5


# a decade whose albums query fails says 'Years unavailable', as the decade list does, rather than opening an empty page
def test_a_failing_decade_query_says_era_unavailable(fw, ui):
    db = EraDb(fw, decades=[(b'1990', b'3')], albums=ERA_ALBUMS)
    call(fw, 'era_menu')
    era, run = ui.top(), db.run
    fw.mock('sqlite3_exec', lambda d, sql, *a: 1 if b'having release_year' in C.string_at(sql) else run(d, sql, *a))
    ui.click(era, 0)
    assert ui.toasts == ['Years unavailable'] and ui.windows == [era]
    fw.mock('sqlite3_exec', run)  # the next try opens it
    ui.click(era, 0)
    assert len(ui.rows(ui.top())) == 5


# a failing query behind Play All, Shuffle or an album says 'Years unavailable' too, not that the card or album is gone
@pytest.mark.parametrize('row', [0, 1, 2])
def test_a_failing_song_query_says_era_unavailable(fw, ui, row):
    db, page = era_decade(fw, ui)
    run = db.run
    fw.mock('sqlite3_exec', lambda d, sql, *a: 1 if b'select s.id from songtable s' in C.string_at(sql) else run(d, sql, *a))
    fw.mock('getMusicByAlbum', lambda *_: -1)  # stock's count when its query fails (toolsQueryDbTable: no database, or sqlite3_exec fails)
    ui.click(page, row)
    assert ui.toasts == ['Years unavailable'] and not ui.played


# Play All's or Shuffle's key query worked but loading the library failed: still 'Years unavailable', not 'Storage
# unavailable'
@pytest.mark.parametrize('row', [0, 1])
def test_play_all_or_shuffle_with_the_library_failing_says_era_unavailable(fw, ui, mcl, row):
    fw.mock('getAllMusic', lambda *_: -1)
    db, page = era_decade(fw, ui)
    db.albums = [(b'9',)]
    ui.click(page, row)
    assert ui.toasts == ['Years unavailable'] and not ui.played


# an album row whose query returned rows and then failed does not open
def test_an_album_row_with_a_partial_failing_query_says_era_unavailable(fw, ui, song):
    albums = Albums(fw, ui, song)
    db, page = era_decade(fw, ui)
    load = albums.load
    fw.mock('getMusicByAlbum', lambda name, *_: [load(name), -1][1])
    ui.click(page, 2)
    assert ui.toasts == ['Years unavailable'] and not ui.played


class Albums:
    """getMusicByAlbum: every song of that album name, in no order, and the card's access to them."""
    def __init__(self, fw, ui, song, gone=()):
        self.songs = {  # another album of the same name, in another folder
            b'Blue': [song(b'/m/blue/x.flac', b'B2', album=b'Blue', track=2), song(b'/m/blue/1.flac', b'B1', album=b'Blue', track=1)],
            b'Grey': [song(b'/m/grey/1.flac', b'G1', album=b'Grey')],
            b'Red': [song(b'/m/red/1.flac', b'R1', album=b'Red'), song(b'/m/other/red.flac', b'Other Red', album=b'Red')],
        }
        self.load = lambda name: ui.deques[fw.var('fw_ctx_tools_pdeq_directory', C.c_uint).value] \
            .extend(self.songs.get(C.string_at(name), [])) or 0
        fw.mock('getMusicByAlbum', lambda name, *_: self.load(name))
        fw.mock('fw_access', lambda p, *_: -1 if C.string_at(p) in gone else 0)


def play_all(fw, ui, songs, keys, albums=ERA_ALBUMS):
    """Play All on a decade whose key query gives keys (song ids, in its order) over the library songs: the queue
    played, or None."""
    library = Library(fw, ui, songs)
    db, page = era_decade(fw, ui, albums=albums)
    db.albums = [(b'%d' % k,) for k in keys]
    ui.click(page, 0)
    return db, library, ui.played[0] if ui.played else None


# one query in the page's order (each album's tracks by disc, track, path), then the library filtered by it
def test_play_all_plays_the_decade_s_songs_in_the_query_s_order(fw, ui, song):
    fw.mock('fw_access', lambda *_: 0)
    songs = [song(b'/m/%d.flac' % i, b'T%d' % i, id_=i) for i in (5, 9, 1, 7)]  # the library's order
    db, library, (queue, at, cls) = play_all(fw, ui, songs, [9, 1, 5])
    assert [field(r, 'title') for r in queue] == [b'T9', b'T1', b'T5'] and at == 0 and cls == LIBRARY
    assert 'order by g.y,g.a COLLATE NOCASE,g.b COLLATE NOCASE,g.d,ifnull(s.cd_num,0),ifnull(s.track,0)' in db.asked[-1]
    assert library.asked == [3]


# a song the query gave twice keeps its first place
def test_play_all_keeps_a_song_once(fw, ui, song):
    fw.mock('fw_access', lambda *_: 0)
    songs = [song(b'/m/%d.flac' % i, b'T%d' % i, id_=i) for i in (1, 2)]
    _, _, (queue, _, _) = play_all(fw, ui, songs, [2, 1, 2])
    assert [field(r, 'title') for r in queue] == [b'T2', b'T1']


# The cost: Play All on the owner's 2010s (2,664 albums) made two stock queries an album, 1.2 s in the emulator and
# about 4.5 s on the Q2 (2026-11-24). Now one SQLite query and one library load, whatever the album count.
def test_play_all_s_queries_do_not_grow_with_the_decade_s_albums(fw, ui, song):
    fw.mock('fw_access', lambda *_: 0)
    by_album = []
    fw.mock('getMusicByAlbum', lambda *a: by_album.append(a) and 0)
    albums = [(b'Album %04d' % i, b'Band', b'1995', b'/m/%d/1.flac' % i) for i in range(300)]
    songs = [song(b'/m/%d/1.flac' % i, b'T%d' % i, id_=i + 1) for i in range(300)]
    db, library, (queue, _, _) = play_all(fw, ui, songs, range(1, 301), albums=albums)
    assert len(queue) == 300 and not by_album and library.asked == [3]
    assert sum('select s.id from songtable s' in q for q in db.asked) == 1


def test_play_all_with_the_card_away_says_so(fw, ui, song):
    fw.mock('fw_access', lambda p, *_: -1 if C.string_at(p) == b'/m/1.flac' else 0)
    assert play_all(fw, ui, [song(b'/m/1.flac', id_=1), song(b'/m/2.flac', id_=2)], [1, 2])[2] is None
    assert ui.toasts == ['Storage unavailable']


# a server row (catalogue.c) has no file: Play All goes on to play_folder, whose load hands it to the app or says
# why it can't, as a server track pressed in Cover Flow does; a card file that is gone still says the card is away
def test_play_all_from_a_server_song_plays_rather_than_saying_storage_unavailable(fw, ui, song):
    server = b'/mnt/mmc/.subsonic/cat/al1/s%d.flac'
    fw.mock('tk_str_start_with', lambda s, p, *_: C.string_at(s).startswith(C.string_at(p)))  # stock's, for catalogue_path
    fw.mock('fw_access', lambda *_: -1)
    songs = [song(server % 1, b'B1', id_=1), song(server % 2, b'B2', id_=2)]
    _, _, (queue, _, _) = play_all(fw, ui, songs, [1, 2])
    assert [field(r, 'title') for r in queue] == [b'B1', b'B2'] and not ui.toasts


def test_shuffle_plays_the_decade_s_songs_shuffled(fw, ui, song, mcl):
    songs = [song(b'/m/%d.flac' % i, id_=i) for i in (5, 9, 1)]
    library = Library(fw, ui, songs)
    fw.mock('fw_access', lambda *_: 0)
    db, page = era_decade(fw, ui)
    db.albums = [(b'9',), (b'1',)]  # the decade's song ids
    ui.click(page, 1)
    (queue, at, cls), = ui.played
    assert queue == songs[1:] and cls == LIBRARY and library.asked == [3]


def test_an_album_row_that_is_gone_says_so(fw, ui, song):
    Albums(fw, ui, song)
    db, page = era_decade(fw, ui, albums=[(b'Blue', b'Band', b'1991', b'/m/blue/9.flac')])  # no such file now
    ui.click(page, 2)
    assert ui.toasts == ['Album not found']


def test_library_count_is_the_query_s_number(fw):
    db = EraDb(fw, decades=[(b'42',)])
    count = fw.fn('library_count', C.c_int, C.c_char_p)
    assert count(b'select count(*) from songtable') == 42
    db.opens = False
    assert count(b'select count(*) from songtable') == -1


# Podcasts, Audiobooks and Videos: Folder view at the card's folder.
def test_a_media_row_opens_folder_view_at_its_folder(fw, ui, st):
    Disk(fw, folders=['/mnt/mmc/PODCASTS', '/mnt/mmc/Music'])
    call(fw, 'media_click', 1)
    assert ui.went == ['folder_page'] and st('media_root', C.c_char * 32).value == b'/mnt/mmc/PODCASTS'


@pytest.mark.parametrize('kind, name', [(1, 'Podcasts'), (3, 'Videos')])
def test_a_media_row_whose_folder_went_says_so(fw, ui, kind, name):
    Disk(fw, {'/mnt/mmc/Podcasts': b'a file, not a folder'}, folders=['/mnt/mmc/Music'])
    call(fw, 'media_click', kind)
    assert ui.toasts == [f'No {name} folder on the card'] and not ui.went


class Folder:
    """Folder view: stock's page init, back and click as stubs that move g_folder_layer as stock's do, and the
    folder it shows."""
    def __init__(self, fw, ui):
        self.fw, self.ui = fw, ui
        self.layer, self.otg = fw.var('g_folder_layer', C.c_ubyte), fw.var('g_otg_status', C.c_ubyte)
        self.path = fw.var('fw_ctx_g_folder_path', C.c_char * 1024)
        fw.mock('stock_folder_trampoline', lambda *_: 6)
        fw.mock('stock_folder_back_trampoline', lambda *_: setattr(self.layer, 'value', self.layer.value - 1) or 0)
        self.win = ui.new('window_create', 0, 0, 0, 0, 0)

    def open(self):
        return self.fw.fn('ringnav_folder', C.c_int, C.c_void_p, C.c_void_p)(self.win, None)

    def back(self):
        return self.fw.fn('ringnav_folder_back', C.c_int, C.c_void_p, C.c_void_p)(None, None)

    def at(self):
        """Where stock was sent, or None: the folder and its layer."""
        if not self.fw.calls('folder_reset_variable'):
            return None
        names = [c[0] for c in self.fw.calls() if c[0].startswith('folder_')]
        assert names[-4:] == ['folder_reset_variable', 'folder_reload_data', 'folder_reinit_navbarname', 'folder_refresh']
        return self.path.value.decode(), self.layer.value


def test_a_media_folder_is_folder_view_s_root(fw, ui, st):
    Disk(fw, {'/tmp/mmc_add': b''}, folders=['/mnt/mmc/Audiobooks'])
    folder = Folder(fw, ui)
    call(fw, 'media_click', 2)
    assert folder.open() == 6 and folder.at() == ('/mnt/mmc/Audiobooks', 3)
    assert [c[1][0] for c in fw.calls('folder_refresh')] == [folder.win]
    folder.back()  # Back at it leaves the page, as stock's from its first layer
    assert folder.layer.value == 0 and not st('media_root', C.c_char * 32).value


@pytest.mark.parametrize('card, otg, at', [(True, 0, ('/mnt/mmc', 2)), (True, 1, None), (False, 0, None)])
def test_folder_view_opens_on_the_card_when_it_is_the_only_storage(fw, ui, card, otg, at):
    Disk(fw, {'/tmp/mmc_add': b''} if card else {})
    folder = Folder(fw, ui)
    folder.otg.value = otg
    folder.open()
    assert folder.at() == at


def test_folder_root_opens_folder_view_below_the_card(fw, ui):
    Disk(fw, {'/tmp/mmc_add': b''})
    Config(fw, FOLDERROOT=b'Music')
    fw.mock('fw___xstat', lambda v, path, info, *_: C.c_uint.from_address(info + 20).__setattr__('value', 0o040755) or 0)
    folder = Folder(fw, ui)
    folder.open()
    assert folder.at() == ('/mnt/mmc/Music', 3)


def test_go_to_folder_opens_folder_view_there_once(fw, ui, st):
    Disk(fw, {'/tmp/mmc_add': b''})
    folder = Folder(fw, ui)
    st('go_folder', C.c_char * 1024).value, st('go_layer').value = b'/mnt/mmc/A/B', 4
    folder.open()
    assert folder.at() == ('/mnt/mmc/A/B', 4) and not st('go_layer').value
    folder.open()
    assert folder.at() == ('/mnt/mmc', 2)


def test_back_below_the_root_is_stock_s(fw, ui, st):
    Disk(fw, {'/tmp/mmc_add': b''}, folders=['/mnt/mmc/Podcasts'])
    folder = Folder(fw, ui)
    call(fw, 'media_click', 1)
    folder.open()
    folder.path.value, folder.layer.value = b'/mnt/mmc/Podcasts/Show', 4
    folder.back()
    assert folder.layer.value == 3 and st('media_root', C.c_char * 32).value == b'/mnt/mmc/Podcasts'


@pytest.mark.parametrize('otg, roots', [(0, 1), (1, 2)])
def test_the_storage_list_drops_otg_without_a_drive(fw, ui, otg, roots):
    folder = Folder(fw, ui)
    folder.otg.value = otg
    showlist = ui.deques.new([0x10, 0x20])
    fw.var('fw_ctx_p_deque_showlist', C.c_uint).value = showlist
    fw.mock('fw_deque_pop_back', lambda q, *_: ui.deques[q].pop() and 0)
    fw.fn('ringnav_folder_top', None)()
    assert len(ui.deques[showlist]) == roots


class Rows:
    """Folder view's list (p_deque_showlist) and stock's row lookup: a click's target is row i."""
    def __init__(self, fw, ui, records):
        fw.var('fw_ctx_p_deque_showlist', C.c_uint).value = ui.deques.new(records)
        self.row, self.event = C.create_string_buffer(0x100), C.create_string_buffer(0x40)
        fw.mock('table_row_of', lambda target, *_: C.addressof(self.row))
        fw.mock('stock_folder_click_trampoline', lambda *_: 2)
        self.fw = fw

    def click(self, i):
        struct.pack_into('<i', self.row, O['ROW_INDEX'], i)
        return self.fw.fn('ringnav_folder_click', C.c_int, C.c_void_p, C.c_void_p)(None, C.addressof(self.event))


def test_a_video_in_folder_view_plays_as_a_video(fw, ui, song):
    Files(fw)
    rows = Rows(fw, ui, [song(b'/mnt/mmc/Videos/a.mp4'), song(b'/mnt/mmc/Videos/b.flac')])
    fw.mock('fw_fork', lambda *_: 4242)
    assert rows.click(0) == 0 and fw.calls('fw_fork') and not fw.calls('stock_folder_click_trampoline')
    assert rows.click(1) == 2 and len(fw.calls('stock_folder_click_trampoline')) == 1


def test_select_mode_leaves_a_video_to_stock(fw, ui, song):
    rows = Rows(fw, ui, [song(b'/mnt/mmc/Videos/a.mp4')])
    fw.var('g_navbar_status', C.c_ubyte).value = 1
    assert rows.click(0) == 2 and not fw.calls('fw_fork')


# A spoken CUE track's saved place.
def spots(*places, slots=RESUME_SLOTS):
    data = b''.join(struct.pack('<Ii', key, sec) for key, sec in places)
    return data + b'\0' * (slots * 8 - len(data))


def book_place(fw, song, files, path=b'/mnt/mmc/Audiobooks/b.flac'):
    Files(fw, files, card=True)  # places are on it (card_store.c)
    fw.mock('tk_str_start_with', lambda s, prefix, *_: C.string_at(s).startswith(C.string_at(prefix)))
    deques = Deques(fw)
    tracks = [song(path, b'A', cue=0, end=600), song(path, b'B', cue=600, end=1200), song(path, b'C', cue=1200)]
    other = song(b'/mnt/mmc/Audiobooks/other.flac', cue=1000, end=2000)
    folder = song(b'/mnt/mmc/Audiobooks/x', kind=4)
    listing = deques.new([folder, tracks[0], other, tracks[1], tracks[2]])
    at, off = C.c_int(-1), C.c_int(-1)
    place = fw.fn('navigation__book_place', C.c_void_p, C.c_void_p, C.c_void_p, C.POINTER(C.c_int), C.POINTER(C.c_int))
    best = place(listing, tracks[1], C.byref(at), C.byref(off))
    return (field(best, 'title') if best else None), at.value, off.value


def test_a_book_resumes_in_the_chapter_holding_its_place(fw, song):
    key = file_key(b'/mnt/mmc/Audiobooks/b.flac')
    assert book_place(fw, song, {RESUME_FILE: spots((0x77, 5), (key, 1500))}) == (b'C', 3, 300)  # 3: the folder row isn't a song


def test_a_book_s_place_from_an_earlier_release_s_file(fw, song):
    key = file_key(b'/mnt/mmc/Audiobooks/b.flac')
    assert book_place(fw, song, {RESUME_FILE: spots((key, 700), slots=32)}) == (b'B', 2, 100)


def test_a_book_with_no_place_has_none(fw, song):
    assert book_place(fw, song, {})[0] is None


def test_a_song_s_cue_track_is_not_a_book(fw, song):
    key = file_key(b'/mnt/mmc/Music/b.flac')
    assert book_place(fw, song, {RESUME_FILE: spots((key, 700))}, path=b'/mnt/mmc/Music/b.flac')[0] is None


# Shuffle Songs over the song holding its place.
def holding(fw, ui, mcl, st, playing):
    """playing on, a long track 600 s into its 3600: its place is held (resume_held)."""
    fw.var('fw_ctx_mcl_pdeqplaylist', C.c_uint).value = ui.deques.new([playing])
    mcl.type, mcl.pos = LIBRARY, 0
    fw.mock('mclGetPlayStatus', lambda *_: 2)
    fw.mock('player_playtime_and_length', lambda sec, total, *_: C.c_int.from_address(sec).__setattr__('value', 600))
    st('rs_long').value, st('rs_total').value = 1, 3600


def test_shuffle_songs_of_the_one_song_holding_its_place_is_now_playing(fw, ui, song, mcl, st):
    playing = song(b'/m/book.flac')
    holding(fw, ui, mcl, st, playing)
    Library(fw, ui, [song(b'/m/book.flac')])
    call(fw, 'shuffle_songs')
    assert not ui.played and [C.string_at(c[1][0]) for c in fw.calls('navigator_switch_to_with_context')] == [b'playing_page']


def test_shuffle_songs_starts_anywhere_but_the_song_holding_its_place(fw, ui, song, mcl, st):
    holding(fw, ui, mcl, st, song(b'/m/book.flac'))
    for _ in range(8):  # rand_below's picks: never the held one
        Library(fw, ui, [song(b'/m/book.flac'), song(b'/m/other.flac')])
        ui.played.clear()
        call(fw, 'shuffle_songs')
        assert ui.played[0][1] == 1


# The queue menu's tracks for a row.
QM_SONG_, QM_FOLDER_, QM_COVERALBUM_, QM_PLAYLIST_ = 1, 4, 6, 8


def collect(fw, ui, st, kind, r, cls=0):
    st('qm_kind').value, st('qm_cls', C.c_uint).value = kind, cls
    return [field(t, 'path') for t in ui.deques[fw.fn('qm_collect', C.c_long, C.c_void_p)(r)]]


def test_a_song_row_s_tracks_are_the_song_but_a_video(fw, ui, song, st):
    assert collect(fw, ui, st, QM_SONG_, song(b'/m/a.flac')) == [b'/m/a.flac']
    assert collect(fw, ui, st, QM_SONG_, song(b'/m/clip.mp4')) == []


def test_a_folder_row_s_tracks_take_in_its_subfolders(fw, ui, song, st):
    tree = {b'/mnt/mmc/Music/Album': [song(b'/mnt/mmc/Music/Album/1.flac'), song(name=b'CD2', kind=4),
                                     song(name=b'.hidden', kind=4), song(b'/mnt/mmc/Music/Album/v.mp4')],
            b'/mnt/mmc/Music/Album/CD2': [song(b'/mnt/mmc/Music/Album/CD2/2.flac')],
            b'/mnt/mmc/Music/Album/.hidden': [song(b'/mnt/mmc/Music/Album/.hidden/3.flac')]}
    fw.mock('toolsLoadDirectory', lambda path, *_: ui.deques[fw.var('fw_ctx_tools_pdeq_directory', C.c_uint).value]
            .extend(tree.get(C.string_at(path), [])) or 0)
    fw.var('fw_ctx_g_folder_path', C.c_char * 1024).value = b'/mnt/mmc/Music'
    assert collect(fw, ui, st, QM_FOLDER_, song(name=b'Album', kind=4)) == [b'/mnt/mmc/Music/Album/1.flac',
                                                                              b'/mnt/mmc/Music/Album/CD2/2.flac']


def test_a_playlist_row_s_tracks_leave_out_spoken_ones(fw, ui, song, st):
    rows = [song(b'/mnt/mmc/Music/a.flac'), song(b'/mnt/mmc/Podcasts/ep.mp3'), song(b'/mnt/mmc/Music/x', kind=4),
            song(b'/mnt/mmc/Music/b.flac')]
    asked = []
    fw.mock('getMusicByPlayList', lambda n, *_: asked.append(n) or ui.deques[fw.var('fw_ctx_tools_pdeq_directory', C.c_uint).value]
            .extend(rows) or 0)
    fw.mock('tk_str_start_with', lambda s, prefix, *_: C.string_at(s).startswith(C.string_at(prefix)))
    playlist = song(kind=4, disc=7)  # a playlist row: its cd_num
    assert collect(fw, ui, st, QM_PLAYLIST_, playlist) == [b'/mnt/mmc/Music/a.flac', b'/mnt/mmc/Music/b.flac'] and asked == [7]


def album_songs(fw, ui, song, st):
    """A row whose tracks are two songs in a card app's favdir (a playlist row's, the simplest to give): its files."""
    rows = [song(b'/mnt/mmc/Jellyfin/A/1.flac'), song(b'/mnt/mmc/Jellyfin/A/2.flac')]
    fw.mock('getMusicByPlayList', lambda n, *_: ui.deques[fw.var('fw_ctx_tools_pdeq_directory', C.c_uint).value]
            .extend(rows) or 0)
    files = Files(fw)
    favdir_card(fw, files.files)
    st('qm_kind').value = QM_PLAYLIST_
    return files


def test_an_album_s_favourite_is_told_for_each_of_its_songs(fw, ui, song, st):
    files = album_songs(fw, ui, song, st)
    fw.fn('rate_album_favs', None, C.c_void_p, C.c_int)(song(kind=4, disc=7), 1)
    assert files.files['/mnt/mmc/.jf/fav.log'] == b''.join(b'\npath\t1\t/mnt/mmc/Jellyfin/A/%d.flac\n' % i
                                                         for i in (1, 2))


def test_an_album_s_unfavourite_is_told_only_for_songs_that_were_favourites(fw, ui, song, st):
    """A song liked on the app's server and never hearted here isn't the Q2's to unlike."""
    files = album_songs(fw, ui, song, st)
    fw.mock('checkFavExist', lambda r, *_: field(r, 'path').endswith(b'/2.flac'))
    fw.fn('rate_album_favs', None, C.c_void_p, C.c_int)(song(kind=4, disc=7), 0)
    assert files.files['/mnt/mmc/.jf/fav.log'] == b'\npath\t0\t/mnt/mmc/Jellyfin/A/2.flac\n'
    assert len(fw.calls('deleteMusicFromFav')) == 2  # My Fav is cleared of both as before


def test_an_album_s_favourite_stops_telling_at_the_first_refusal(fw, ui, song, st):
    files = album_songs(fw, ui, song, st)
    tried = []
    fw.mock('fw_fopen', lambda path, mode, *_: tried.append(C.string_at(path)) or 0 if C.string_at(path).endswith(b'fav.log')
            else files.fopen(path, mode))
    fw.fn('rate_album_favs', None, C.c_void_p, C.c_int)(song(kind=4, disc=7), 1)
    assert tried == [b'/mnt/mmc/.jf/fav.log'] and ui.toasts == ['Favorite not sent to the app']


def test_a_cover_s_tracks_are_its_album_in_order(fw, ui, song, st):
    albums = Albums(fw, ui, song)
    assert collect(fw, ui, st, QM_COVERALBUM_, albums.songs[b'Blue'][0]) == [b'/m/blue/1.flac', b'/m/blue/x.flac']


@pytest.mark.parametrize('mode, forced', [(2, 2), (0, 0)])
def test_play_next_in_stock_shuffle_forces_the_track_next(fw, ui, song, st, mcl, mode, forced):
    a, b, c = song(b'/m/a.flac'), song(b'/m/b.flac'), song(b'/m/c.flac')
    held = HeldRows(fw)  # the queue holds its own copies, as libcstl's: Play Next turns them into place by their bytes
    queue = held.rows([a, b])
    fw.var('fw_ctx_mcl_pdeqplaylist', C.c_uint).value = queue
    C.c_uint64.from_address(O['MCL_POOL']).value = held.new()
    mcl.type, mcl.pos, mcl.mode, mcl.lastpos = LIBRARY, 0, mode, -1
    st('qm_kind').value = QM_SONG_
    assert fw.fn('navigation__qm_apply', C.c_int, C.c_void_p, C.c_int)(held.new([c]), 1) == 1
    assert [field(r, 'path') for r in held[queue]] == [field(r, 'path') for r in (a, c, b)]
    assert st('qm_forced', C.c_uint).value == forced
    assert st('qm_forced_hash', C.c_uint).value == fw.fn('navigation__rec_hash', C.c_uint, C.c_void_p)(c)


# Rated albums.
def rated_albums(fw, ui, song, hearts=0):
    """The Library's albums A and B (stock's list, its album split and its music albums all the two), A rated."""
    files = Files(fw)
    rows = [song(b'/m/a/1.flac', album=b'A', id_=1), song(b'/m/b/1.flac', album=b'B', id_=2)]
    put = lambda *_: ui.deques[fw.var('fw_ctx_tools_pdeq_directory', C.c_uint).value].extend(rows) or 2  # noqa: E731
    fw.mock('getAllAlbum', put)
    fw.mock('toolsQueryDbTable', put)
    if hearts:
        album_id = fw.fn('album_id', C.c_uint, C.c_void_p)(rows[0])
        assert fw.fn('rate_keep', C.c_int, C.c_int, C.c_uint, C.c_int)(1, album_id, hearts) == 1
    call(fw, 'rated_albums')
    return files



def test_a_cold_cover_flow_open_leaves_home_live_with_its_spinner_through_a_long_query(fw, ui, song):
    """Device report (p32, 2026-11-17): Home's Cover Flow row stopped its spinner while a cold open queried the
    library; and (2026-11-19) the whole screen held still until the albums were in, Return included. getAllAlbum
    takes 1.2 s here, on a thread of its own: the press returns once the open is 75 ms old, the spinner up on Home's
    row and Home's paint drawing it; each later pass of the main loop collects a step that ended and starts the
    next, and the page opens after the last."""
    Files(fw)
    clock, thread, paints = [1000], [], []
    fw.mock('time_now_ms', lambda *_: clock[0])
    fw.var('g_backlight_status', C.c_ubyte).value = 1
    home = ui.new('window_create', 0, 0, 0, 0, 0)
    ui.name[home] = 'home_page'
    rows = [song(b'/m/a/1.flac', album=b'A', id_=1), song(b'/m/b/1.flac', album=b'B', id_=2)]
    put = lambda *_: ui.deques[fw.var('fw_ctx_tools_pdeq_directory', C.c_uint).value].extend(rows) or 2  # noqa: E731
    fw.mock('getAllAlbum', put)
    fw.mock('toolsQueryDbTable', put)

    def create(out, attr, run, arg, *_):  # a step's thread, run when the test says it has ended
        C.c_ulong.from_address(out).value = 99
        thread.append([run, arg])
        return 0

    def usleep(us, *_):
        clock[0] += us // 1000
        return 0
    fw.mock('fw_pthread_create', create)
    fw.mock('fw_usleep', usleep)
    fw.mock('widget_load_image', lambda *_: 1)
    fw.mock('widget_invalidate_force', lambda w, *_: fw.fn('coverflow_spinner', C.c_int)() and paints.append(clock[0]) or 0)
    fw.fn('coverflow__coverflow_open', C.c_int, C.c_void_p, C.c_void_p)(None, None)
    # the press is back: getAllAlbum still runs, the open 75 ms old, Home with its row's spinner
    assert len(thread) == 1 and thread[0][0] and clock[0] - 1000 >= 75 and clock[0] - 1000 < 1200
    assert ui.name[ui.top()] == 'home_page' and fw.fn('coverflow_spinner', C.c_int)() and paints
    fw.fn('spinner_icon', None, C.c_void_p, C.c_void_p, C.c_int, C.c_int)(home, None, 355, 100)  # Home's paint draws it
    clock[0] += 1200
    for _ in range(10):  # each step ends, then a pass of the main loop collects it
        for t in thread:
            if t[0]:
                run, t[0] = t[0], None
                C.CFUNCTYPE(C.c_void_p, C.c_void_p)(run)(t[1])
        for t, (fn, ctx, _ms) in list(ui.timers.items()):
            if fn == C.cast(fw.lib.coverflow__open_step, C.c_void_p).value:
                del ui.timers[t]
                TICK(fn)(ctx)
    assert ui.name[ui.top()] == 'coverflow_page' and not fw.fn('coverflow_spinner', C.c_int)()


def test_a_library_query_on_the_ui_thread_gives_a_running_cover_flow_step_up_first(fw, ui, song):
    """A library query of the payload's own on the UI thread (staged_into) while a Cover Flow open's step runs on its
    thread would meet the step's use of stock's staging pointer: the open is given up first, its step told to stop
    (coverflow_query_stop answers on its thread alone) and joined, and then the query runs."""
    Files(fw)
    clock, thread = [1000], []
    fw.mock('time_now_ms', lambda *_: clock[0])
    home = ui.new('window_create', 0, 0, 0, 0, 0)
    ui.name[home] = 'home_page'
    fw.mock('fw_pthread_create', lambda out, attr, run, arg, *_: C.c_ulong.from_address(out).__setattr__('value', 99)
            or thread.append(run) or 0)
    fw.mock('fw_usleep', lambda us, *_: clock.__setitem__(0, clock[0] + us // 1000) or 0)
    on = [0]  # the thread asking: 0 the UI's, else the step's
    fw.mock('fw_pthread_self', lambda *_: on[0])
    fw.fn('coverflow__coverflow_open', C.c_int, C.c_void_p, C.c_void_p)(None, None)
    stop = fw.fn('coverflow_query_stop', C.c_int, C.c_void_p)
    assert thread and fw.fn('coverflow_spinner', C.c_int)() and not stop(None)
    fw.mock('getAllMusic', lambda *_: 0)
    joins = len(fw.calls('fw_pthread_join'))
    fw.fn('staged', C.c_void_p, C.c_void_p, C.c_void_p, C.c_void_p)(fw.lib.getAllMusic, None, C.byref(C.c_int()))
    assert len(fw.calls('fw_pthread_join')) == joins + 1 and not fw.fn('coverflow_spinner', C.c_int)()
    assert not ui.timers or all(fn != C.cast(fw.lib.coverflow__open_step, C.c_void_p).value
                                for fn, _c, _m in ui.timers.values())


class CfOpen:
    """Home's Cover Flow pressed (coverflow_open) over albums A and B: each step's thread runs when the test says
    (steps), the clock is the test's, and the unified log's cf lines are kept (lines)."""
    def __init__(self, fw, ui, song):
        self.fw, self.ui, self.clock, self.thread, self.lines = fw, ui, [1000], [], []
        Files(fw)
        fw.mock('time_now_ms', lambda *_: self.clock[0])
        fw.var('g_backlight_status', C.c_ubyte).value = 1
        home = ui.new('window_create', 0, 0, 0, 0, 0)
        ui.name[home] = 'home_page'
        rows = [song(b'/m/a/1.flac', album=b'A', id_=1), song(b'/m/b/1.flac', album=b'B', id_=2)]
        put = lambda *_: ui.deques[fw.var('fw_ctx_tools_pdeq_directory', C.c_uint).value].extend(rows) or 2  # noqa: E731
        fw.mock('getAllAlbum', put)
        fw.mock('toolsQueryDbTable', put)
        fw.mock('fw_pthread_create', self.create)
        fw.mock('fw_usleep', lambda us, *_: self.clock.__setitem__(0, self.clock[0] + us // 1000) or 0)
        fw.mock('widget_load_image', lambda *_: 1)
        fw.mock('fw_write', self.write)
        fw.fn('coverflow__coverflow_open', C.c_int, C.c_void_p, C.c_void_p)(None, None)

    def create(self, out, attr, run, arg, *_):
        C.c_ulong.from_address(out).value = 99
        self.thread.append([run, arg])
        return 0

    def write(self, fd, data, n, *_):
        line = C.string_at(data, n).decode().rstrip('\n').split(' ', 4)
        if fd == 2 and line[3] == 'cf':
            self.lines.append(line[2] + ' ' + line[4])
        return n

    def steps(self, n=10, until=None):
        """Each running step ends and a pass of the main loop collects it, n times; a step until (its k) is left
        running, as is the art build's worker."""
        worker = C.cast(self.fw.lib.coverflow__worker, C.c_void_p).value
        for _ in range(n):
            for t in self.thread:
                if t[0] and t[0] != worker and t[1] != until:
                    run, t[0] = t[0], None
                    C.CFUNCTYPE(C.c_void_p, C.c_void_p)(run)(t[1])
            for t, (fn, ctx, _ms) in list(self.ui.timers.items()):
                if fn == C.cast(self.fw.lib.coverflow__open_step, C.c_void_p).value:
                    del self.ui.timers[t]
                    TICK(fn)(ctx)

    def watch(self, ms):
        """ms later, the ticks that wait (open_step's, and the art build's look, art_poll) run as the main loop
        would: the cf lines they added."""
        self.clock[0] += ms
        before = len(self.lines)
        ticks = {C.cast(self.fw.lib.coverflow__open_step, C.c_void_p).value,
                 C.cast(self.fw.lib.coverflow__art_poll, C.c_void_p).value}
        for t, (fn, ctx, _ms) in list(self.ui.timers.items()):
            if fn in ticks:
                del self.ui.timers[t]
                TICK(fn)(ctx)
        return self.lines[before:]


# Cover Flow's open in the unified log (docs/internals.md#cover-flow-log): "Cover Flow never loaded" (the owner,
# 2026-11-24) left no line to say why. An open says what it opens, its covers when they show and how long after.
def test_a_cover_flow_open_logs_its_albums_and_when_its_covers_show(fw, ui, song):
    cf = CfOpen(fw, ui, song)
    assert cf.lines == ['I cover flow open: the albums to query']
    cf.clock[0] += 1200
    cf.steps()
    assert ui.name[ui.top()] == 'coverflow_page'
    assert cf.lines[-1].startswith('I cover flow shown: ') and ', 2 albums, ' in cf.lines[-1]
    assert cf.watch(60000) == []  # nothing left to watch


# Not shown 5 s after the open: a W line naming the phase it waits in, then one a minute while it still waits
def test_a_cover_flow_open_still_waiting_says_where_after_5_s_then_each_minute(fw, ui, song):
    cf = CfOpen(fw, ui, song)
    assert cf.watch(4000) == []
    assert cf.watch(1000) == ["W cover flow not shown 5 s after its open: the albums' query (step 1 of 3)"]
    assert cf.watch(16) == [] and cf.watch(59000) == []  # never a line a tick
    assert cf.watch(1000) == ["W cover flow not shown 65 s after its open: the albums' query (step 1 of 3)"]


# the cover lookup (OPEN_ART, its own thread) says how far it is, and how many albums have no cover file to make art from
def test_a_cover_flow_open_in_its_cover_lookup_says_how_far(fw, ui, song):
    cf = CfOpen(fw, ui, song)
    cf.steps(until=4)
    assert cf.watch(5000) == ['W cover flow not shown 5 s after its open: the cover lookup, 0 of 2 albums looked at, '
                              '0 without a cover file']


# the covers up at once with their art to build behind them (on a thread), and the page closed before the build
# ends: the line says how far it got
def test_a_cover_flow_page_closed_while_its_art_builds_says_so(fw, ui, song):
    def statfs(path, buf, *_):  # the card has room for the art
        words = C.cast(buf, C.POINTER(C.c_uint))
        words[1], words[7] = 4096, 1 << 20
        return 0
    fw.mock('fw_statfs', statfs)
    fw.mock('fw_access', lambda p, *_: -1 if b'/.coverflow/' in C.string_at(p) else 0)  # no album's art made yet
    cf = CfOpen(fw, ui, song)
    cf.steps()
    page = ui.top()
    assert ui.name[page] == 'coverflow_page'
    assert cf.lines[-1].startswith('I cover flow shown: covers, 2 albums, ') and ' ms after its open, 2 covers to build' in cf.lines[-1]
    assert cf.watch(5000) == []  # shown: nothing waits
    cf.clock[0] += 1500
    ui.close(page)
    assert cf.lines[-1] == 'I cover flow art stopped: 0 of 2 covers, 6500 ms'
    assert cf.watch(60000) == []


# Return while it waits: the open given up, and the line says where it was
def test_a_cover_flow_open_given_up_says_where_it_was(fw, ui, song):
    cf = CfOpen(fw, ui, song)
    key = (C.c_uint * 16)()
    key[O['EVENT_TYPE'] // 4], key[O['EVENT_KEY'] // 4] = O['EVT_KEY_UP'], O['KEY_RETURN']
    assert fw.fn('coverflow_wait_input', C.c_int, C.c_void_p)(key) == 1
    assert cf.lines[-1].startswith('I cover flow open given up after ') and \
        cf.lines[-1].endswith("in the albums' query (step 1 of 3)")
    assert cf.watch(60000) == []


def test_albums_none_of_them_rated_says_so(fw, ui, song):
    rated_albums(fw, ui, song)
    assert ui.toasts == ['No rated albums yet'] and not ui.windows


def test_a_rated_album_opens_cover_flow_on_it_alone(fw, ui, song):
    rated_albums(fw, ui, song, hearts=4)
    assert not ui.toasts and ui.name[ui.top()] == 'coverflow_page'
    cf = fw.var('cf', C.c_void_p * 10)  # page body covers slide name artist title albums tracks stock
    assert [field(r, 'album') for r in ui.deques[cf[7]]] == [b'A']  # the covers shown: B has no hearts


def test_a_card_app_s_album_songs_are_in_track_order(fw, ui, song):
    card = Card(fw, ui, song, APP_SONGS)
    songs = fw.fn('app_album_songs', C.c_long, C.c_char_p)(APP)
    assert card.paths(ui.deques[songs]) == sorted(APP_SONGS)


# The playlist editor's records.
class Ple(C.Structure):
    _fields_ = [*((k, C.c_void_p) for k in 'page view songs picker edit match'.split()),
                *((k, C.c_int) for k in 'dest mode selected pending nmatch'.split()),
                ('key', C.c_uint), ('timer', C.c_uint), ('name', C.c_char * 128), ('query', C.c_char * 128)]


@pytest.mark.parametrize('query, titles', [(b'moon', [b'Blue Moon']), (b'ELVIS', [b'Blue Moon', b'02 Untitled']),
                                           (b'hits', [b'Blue Moon']), (b'untitled', [b'02 Untitled']), (b'zz', []),
                                           (b'', [b'Blue Moon', b'02 Untitled', b'Other'])])
def test_add_songs_search_matches_title_artist_or_album(fw, ui, song, query, titles):
    rows = [song(b'/m/1.flac', b'Blue Moon', b'Elvis', b'Hits'), song(b'/m/02 Untitled.flac', b'', b'elvis'),
            song(b'/m/3.flac', b'Other', b'Someone', b'Else')]
    ple = fw.var('playlists__ple', Ple)
    ple.picker, ple.query = ui.deques.new(rows), query
    fw.fn('playlists__ple_filter', None)()
    name = fw.fn('track_name', C.c_char_p, C.c_char_p, C.c_uint, C.c_void_p)
    out = C.create_string_buffer(64)
    matches = C.cast(ple.match, C.POINTER(C.c_void_p))
    assert [name(out, 64, matches[i]) for i in range(ple.nmatch)] == titles


def test_add_songs_search_with_no_picker_matches_nothing(fw, ui):
    ple = fw.var('playlists__ple', Ple)
    fw.fn('playlists__ple_filter', None)()
    assert ple.nmatch == 0 and not ple.match


@pytest.mark.parametrize('selected, size, kept', [(1, 3, 1), (5, 3, 2), (2, 0, 0)])
def test_a_playlist_read_again_keeps_the_selection_in_it(fw, ui, song, selected, size, kept):
    rows = [song(b'/m/%d.flac' % i) for i in range(size)]
    asked = []
    # the stored order, whatever stock's Sort shows the playlist in
    fw.mock('sort_playlist_addtime', lambda n, *_: asked.append(n) or ui.deques[fw.var('fw_ctx_tools_pdeq_directory', C.c_uint).value]
            .extend(rows) or 0)
    ple = fw.var('playlists__ple', Ple)
    old = ui.deques.new([1])
    ple.songs, ple.dest, ple.selected = old, 4, selected
    fw.fn('playlists__ple_reload', None)()
    assert ui.deques[ple.songs] == rows and ple.selected == kept and asked == [4]
    assert old not in ui.deques.lists  # the old list went


def test_a_picker_row_s_check_sits_at_its_end(fw, ui):
    item = ui.new('list_item_create', 0, 0, 0, 375, 48)
    label = ui.new('hscroll_label_create', item, 20, 0, 335, 48)  # the caption across the row
    tick = fw.fn('playlists__ple_tick', C.c_void_p, C.c_void_p)(item)
    assert ui.image[tick] == 'select' and ui.kids[item] == [label, tick]
    tx, ty, tw, th = struct.unpack_from('<4i', ui.blocks[tick])
    assert tx + tw <= 375 and (ty, th) == (0, 48)  # in the row, its height
    (_, (w, x, y, width, h, _)), = fw.calls('widget_move_resize')
    assert w == label and (x, y, h) == (20, 0, 48) and 0 < width and x + width <= tx  # the caption stops short of it
    assert fw.fn('playlists__ple_tick', C.c_void_p, C.c_void_p)(None) is None


def test_the_editor_s_close_frees_all_but_the_held_key(fw, ui):
    ple = fw.var('playlists__ple', Ple)
    songs, picker = ui.deques.new([1]), ui.deques.new([2])
    ple.songs, ple.picker, ple.timer, ple.key, ple.dest = songs, picker, 33, KEY_CENTER, 5
    fw.fn('playlists__ple_closed', C.c_int, C.c_void_p, C.c_void_p)(None, None)
    assert [c[1][0] for c in fw.calls('timer_remove')] == [33]
    assert not ui.deques.lists and (ple.songs, ple.picker, ple.dest, ple.key) == (None, None, 0, KEY_CENTER)


class Playlists:
    """playlist's rows by id: (cd_num, fileurl, time, time_end), and SQLite as ple_mutate speaks to it: its
    statements, a failure where asked, and the rows' state after a commit (a rollback puts them back)."""
    def __init__(self, fw, rows, fail=(), opens=True):
        self.rows, self.fail, self.opens, self.log, self.keep = dict(rows), fail, opens, [], []
        self.saved = dict(rows)
        fw.mock('sqlite3_open', lambda path, db, *_: 14 if not self.opens else C.c_void_p.from_address(db).__setattr__('value', 0xdb) or 0)
        fw.mock('sqlite3_exec', self.run)
        fw.mock('sqlite3_close', lambda *_: self.log.append('close') and 0)

    def rows_to(self, callback, ctx, rows):
        call = C.CFUNCTYPE(C.c_int, C.c_void_p, C.c_int, C.POINTER(C.c_char_p), C.c_void_p)(callback)
        for values in rows:
            cells = (C.c_char_p * len(values))(*values)
            self.keep.append(cells)
            if call(ctx, len(values), cells, None):
                return 4  # SQLITE_ABORT
        return 0

    def run(self, db, sql, callback, ctx, *_):
        sql = C.string_at(sql).decode()
        self.log.append(sql)
        if any(f in sql for f in self.fail):
            return 1
        if sql == 'begin immediate':
            self.saved = dict(self.rows)
            return 0
        if sql == 'rollback':
            self.rows = dict(self.saved)
            return 0
        if sql == 'commit':
            return 0
        if sql.startswith('select id,fileurl'):
            dest, a, b = map(int, re.findall(r'-?\d+', sql)[-3:])
            found = [(b'%d' % i, r[1], b'%d' % r[2], b'%d' % r[3]) for i, r in sorted(self.rows.items())
                     if r[0] == dest and i in (a, b)]
            return self.rows_to(callback, ctx, found)
        if sql.startswith('select case'):
            top = max(self.rows, default=100)
            return self.rows_to(callback, ctx, [(None if top >= 2 ** 31 - 1 else b'%d' % (max(100, top) + 1),)])
        status = 0
        for statement in sql.split(';'):
            if m := re.fullmatch(r'update playlist set id=(-?\d+) where cd_num=(\d+) and id=(-?\d+)', statement):
                to, dest, at = map(int, m.groups())
                changed = at in self.rows and self.rows[at][0] == dest
                if changed:
                    self.rows[to] = self.rows.pop(at)
            elif m := re.fullmatch(r'delete from playlist where cd_num=(\d+) and id=(-?\d+)', statement):
                dest, at = map(int, m.groups())
                changed = at in self.rows and self.rows[at][0] == dest
                if changed:
                    del self.rows[at]
            elif statement == 'select changes()':
                status = status or self.rows_to(callback, ctx, [(b'%d' % changed,)])
        return status


def entry(song, id_, path, cue=0, end=0):
    return song(path, id_=id_, cue=cue, end=end)


def mutate(fw, dest, a, b=None):
    return fw.fn('playlists__ple_mutate', C.c_int, C.c_int, C.c_void_p, C.c_void_p)(dest, a, b)


def test_moving_a_playlist_song_swaps_two_rows(fw, song):
    db = Playlists(fw, {201: (3, b'/m/a.flac', 0, 0), 202: (3, b'/m/b.flac', 0, 0), 300: (4, b'/m/c.flac', 0, 0)})
    assert mutate(fw, 3, entry(song, 201, b'/m/a.flac'), entry(song, 202, b'/m/b.flac')) == 1
    assert db.rows == {201: (3, b'/m/b.flac', 0, 0), 202: (3, b'/m/a.flac', 0, 0), 300: (4, b'/m/c.flac', 0, 0)}
    assert db.log[:2] == ['PRAGMA busy_timeout=500', 'begin immediate'] and db.log[-2:] == ['commit', 'close']


def test_removing_a_playlist_song_deletes_its_row(fw, song):
    db = Playlists(fw, {201: (3, b'/m/a.flac', 10, 20), 202: (3, b'/m/b.flac', 0, 0)})
    assert mutate(fw, 3, entry(song, 201, b'/m/a.flac', 10, 20)) == 1
    assert db.rows == {202: (3, b'/m/b.flac', 0, 0)} and db.log[-2] == 'commit'


@pytest.mark.parametrize('rows', [
    {201: (3, b'/m/other.flac', 0, 0), 202: (3, b'/m/b.flac', 0, 0)},  # another song there since
    {201: (3, b'/m/a.flac', 5, 0), 202: (3, b'/m/b.flac', 0, 0)},  # another CUE track of the file
    {201: (4, b'/m/a.flac', 0, 0), 202: (3, b'/m/b.flac', 0, 0)},  # in another playlist
    {202: (3, b'/m/b.flac', 0, 0)},  # gone
])
def test_a_playlist_changed_since_it_was_read_is_left_as_it_is(fw, song, rows):
    db = Playlists(fw, rows)
    assert mutate(fw, 3, entry(song, 201, b'/m/a.flac'), entry(song, 202, b'/m/b.flac')) == 0
    assert db.rows == rows and db.log[-2:] == ['rollback', 'close']


def test_no_id_left_above_the_top_one_leaves_the_playlist(fw, song):
    rows = {201: (3, b'/m/a.flac', 0, 0), 202: (3, b'/m/b.flac', 0, 0), 2 ** 31 - 1: (9, b'/m/z.flac', 0, 0)}
    db = Playlists(fw, rows)
    assert mutate(fw, 3, entry(song, 201, b'/m/a.flac'), entry(song, 202, b'/m/b.flac')) == 0
    assert db.rows == rows and 'rollback' in db.log


@pytest.mark.parametrize('fail, rolled', [(('begin',), False), (('update',), True), (('commit',), True)])
def test_a_database_that_refuses_leaves_the_playlist(fw, song, fail, rolled):
    rows = {201: (3, b'/m/a.flac', 0, 0), 202: (3, b'/m/b.flac', 0, 0)}
    db = Playlists(fw, rows, fail=fail)
    assert mutate(fw, 3, entry(song, 201, b'/m/a.flac'), entry(song, 202, b'/m/b.flac')) == 0
    assert db.rows == rows and ('rollback' in db.log) == rolled and db.log[-1] == 'close'


def test_a_database_that_will_not_open_is_left(fw, song):
    db = Playlists(fw, {}, opens=False)
    assert mutate(fw, 3, entry(song, 201, b'/m/a.flac')) == 0 and db.log == []


# The empty state's own page (feedback.c empty_open): a streaming service with no link.
def test_a_streaming_service_with_no_link_is_the_empty_state_and_its_row_opens_wi_fi(fw, ui):
    assert call(fw, 'feedback_offline') == 1
    (page,) = ui.windows
    assert ui.name[page] == 'empty_page' and 'Streaming' not in ui.shown(page)  # the status row names it
    assert empty_state(ui, page) == ('cloud-slash', 'Not Connected', 'Turn on Wi-Fi to stream', 'Turn on Wi-Fi')
    # the stack with its button (176 px) centred under the status row: the button 138 px under the stack's top
    top = 40 + (280 - 176) // 2
    assert ui.props[(ui.lookup(page, '_empty_state', 1), '_es_top')] == top
    row = ui.lookup(page, '_empty_row', 1)
    assert struct.unpack_from('<4i', ui.blocks[row], 0)[1] == top + 138 and ui.kind[row] == 'list_view_create'
    assert ui.props[(ui.kids[row][0], '_ringnav_index')] == 0  # its one row already selected
    assert call(fw, 'feedback_offline') == 1 and len(ui.windows) == 1  # pressed again: the one page
    ui.handler(ui.kids[ui.kids[row][0]][0], EVT_CLICK)()  # the row: list view, scroll view, item
    assert ui.went == ['systemset/wifi_page']
    ui.close(page)
    assert call(fw, 'feedback_offline') == 1 and len(ui.windows) == 1  # closed: it opens again


# Chapters under the loading overlay (docs/oled-design.md#loading): the page opens once ffmpeg's chapters are read, the
# spinner over the window the menu was held on meanwhile (list_marks.c load_over), Return or a tap off it giving up.
class Lo(C.Structure):
    _fields_ = [('win', C.c_void_p), ('cancel', C.c_void_p)]


def gate(fw, type, a, b=0):
    """An event as ringnav_input sees it: a key (a its code), or a touch at the portrait panel's a, b."""
    e = C.create_string_buffer(0x40)
    struct.pack_into('<i', e, O['EVENT_TYPE'], type)
    struct.pack_into('<ii', e, O['EVENT_X'], a, b)  # EVENT_KEY is EVENT_X
    return fw.fn('load_over_input', C.c_int, C.c_void_p)(e)


def held_chapters(fw, ui, song, mcl, sheet=False):
    """Chapters chosen from the hold menu on a window: (the book, that window, the overlay's state)."""
    book = Book(fw, ui, song, mcl, output=ffmetadata((0, b'One'), (754, b'Two')))
    home = ui.new('window_create', 0, 0, 0, 0, 0)
    if sheet:
        ui.new('dialog', 0, 0, 0, 375, 320)  # the menu's sheet, still falling away
    book.open()
    return book, home, fw.var('list_marks__lo', Lo)


@pytest.mark.parametrize('sheet', [False, True])
def test_chapters_load_under_the_overlay_on_the_window_held(fw, ui, song, mcl, sheet):
    book, home, lo = held_chapters(fw, ui, song, mcl, sheet)
    assert ui.windows == [home] and lo.win == home  # no Loading chapters… page
    assert [ms for _, ms in ui.pending('chapter_poll')] == [50]


def test_chapters_open_once_read(fw, ui, song, mcl):
    book, home, lo = held_chapters(fw, ui, song, mcl)
    assert ui.fire('chapter_poll') == 0 and lo.win is None and len(ui.windows) == 2
    page = ui.top()
    assert ui.name[page] == 'chapters_page' and ui.rows(page) == [('One', '0:00:00'), ('Two', '0:12:34')]
    assert not ui.pending('chapter_poll')  # opened once


def test_chapters_still_reading_keep_the_overlay(fw, ui, song, mcl):
    book, home, lo = held_chapters(fw, ui, song, mcl)
    fw.mock('fw_read', lambda *_: (book.errno.__setattr__('value', 11), -1)[1])  # EAGAIN
    fw.mock('fw_waitpid', lambda *_: 0)  # still running
    assert ui.fire('chapter_poll') == REPEAT and ui.windows == [home] and lo.win == home


def test_return_gives_chapters_up_and_nothing_opens_late(fw, ui, song, mcl, st):
    book, home, lo = held_chapters(fw, ui, song, mcl)
    assert gate(fw, O['EVT_KEY_DOWN'], O['KEY_RETURN']) == 1 and lo.win == home
    assert gate(fw, O['EVT_KEY_UP'], O['KEY_RETURN']) == 1 and lo.win is None
    assert book.killed == [(123, 9)] and st('ch', C.c_void_p).value  # the poll reaps and frees it
    assert ui.fire('chapter_poll') == 0 and ui.windows == [home] and not st('ch', C.c_void_p).value
    book.open()
    assert lo.win == home  # and they can be asked for again


@pytest.mark.parametrize('type', ['EVT_KEY_DOWN', 'EVT_KEY_LONG', 'EVT_KEY_UP'])
def test_centre_while_chapters_load_does_nothing(fw, ui, song, mcl, type):
    book, home, lo = held_chapters(fw, ui, song, mcl)
    assert gate(fw, O[type], 218) == 1 and lo.win == home and not book.killed
    book.open()
    assert len(fw.calls('fw_fork')) == 1  # asked once


def test_a_tap_off_the_spinner_gives_chapters_up(fw, ui, song, mcl):
    book, home, lo = held_chapters(fw, ui, song, mcl)
    assert gate(fw, 0xff, 319 - 160, 187) == 1 and gate(fw, 0x103, 319 - 160, 187) == 1 and lo.win == home
    assert gate(fw, 0xff, 319 - 100, 40) == 1 and gate(fw, 0x103, 319 - 100, 40) == 1 and lo.win is None
    assert book.killed == [(123, 9)]
    ui.fire('chapter_poll')
    assert ui.windows == [home]


def test_the_window_closing_gives_chapters_up(fw, ui, song, mcl):
    book, home, lo = held_chapters(fw, ui, song, mcl)
    ui.close(home)
    assert lo.win is None and book.killed == [(123, 9)]
