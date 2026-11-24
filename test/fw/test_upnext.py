"""upnext.c on its device path: Up Next's rows over a queue as playback.c orders it (a shuffle's, or the queue's
own outside advanced play), Playing Next under its heading and Clear, a row's tap, the bump (a Play/Pause hold or a
tap on the arrow), lifting a queued row with Centre held, and the rows following the queue as it plays."""
import ctypes as C
import mmap
import pathlib
import re
import struct

import pytest

import host
from conftest import O, Deques, libc, stock_prop_str
from test_nav_hold import recs  # noqa: F401

# navigation.h's: a handler's RET_STOP, and the travel a tap stays within (AWTK's drag threshold)
NAV = dict(re.findall(r'^#define (STOP|TAP_PX) (\d+)', (pathlib.Path(__file__).resolve().parents[2]/'patch/navigation.h').read_text(), re.M))
STOP, TAP_PX = int(NAV['STOP']), int(NAV['TAP_PX'])
# Up Next's sheet, stock's sortselect dialog as a zeroed widget (no parent): OLED's title and toast checks read the
# window on top
_PAGE = C.create_string_buffer(0x400)
PAGE = C.addressof(_PAGE)
EVT_CLICK, EVT_DESTROY, SEL, COUNT = O['EVT_CLICK'], O['EVT_DESTROY'], b'_ringnav_index', b'_ringnav_count'
KEY_CENTER, KEY_NEXT, KEY_PREV, KEY_RETURN = O['KEY_CENTER'], O['KEY_NEXT'], O['KEY_PREV'], O['KEY_RETURN']
MAP_32BIT = 0x40
NAMES = [b'%02d Song.flac' % k for k in range(8)]


class Low:
    """Memory under 4 GB for the queue's records, laid out as the Q2's: a pointer there is a 4-byte cell, and P()
    here reads 8, so a field reads whole only with an empty cell after it. A record keeps its file name (REC_NAME;
    REC_PATH's cell after it stays empty, so track_name shows the name, as for an untagged file) and its artist."""
    def __init__(self, size=1 << 16):
        self.base = libc.mmap(None, size, 3, mmap.MAP_PRIVATE | mmap.MAP_ANONYMOUS | MAP_32BIT, -1, 0)
        assert self.base and self.base + size < 1 << 32
        self.at, self.size = self.base, size

    def take(self, data):
        p = self.at
        C.memmove(p, data, len(data))
        self.at += len(data) + 15 & ~15
        assert self.at <= self.base + self.size
        return p

    def record(self, name, artist=None, cue=0):
        rec = bytearray(0x60)
        struct.pack_into('<I', rec, O['REC_NAME'], self.take(name + b'\0'))
        if artist is not None:
            struct.pack_into('<I', rec, O['REC_ARTIST'], self.take(artist + b'\0'))
        struct.pack_into('<ii', rec, O['REC_TYPE'], 8, 0)
        struct.pack_into('<ii', rec, O['REC_CUE_START'], cue, cue + 60)  # a queue's identity is in these too
        return self.take(bytes(rec))

    def close(self):
        libc.munmap.argtypes = [C.c_void_p, C.c_size_t]
        libc.munmap(self.base, self.size)


@pytest.fixture
def low():
    memory = Low()
    yield memory
    memory.close()


class Queue:
    """The playing queue in stock's deque, its player's globals, and Advanced Play's Shuffle Songs over it
    (playback.c). The odd tracks' artists go in after the order is drawn: its album grouping reads REC_ALBUM's cell
    with the artist's as its high half here."""
    def __init__(self, fw, mcl, low, names=NAMES, pos=2, seed=0x2545f491, shuffle=True):
        self.fw, self.mcl, self.low, self.deques = fw, mcl, low, Deques(fw)
        fw.mock('fw_deque_pop_back', lambda q, *_: self.deques[q].pop() and 0)
        self.records = [low.record(n, cue=k) for k, n in enumerate(names)]
        self.handle = self.deques.new(self.records)
        fw.var('fw_ctx_mcl_pdeqplaylist', C.c_uint).value = self.handle  # a 32-bit pointer's cell, as on the Q2
        # stock's shuffle pool: a deque of ints, deque_at a pointer to one, as libcstl's
        self.pool, self.ints = self.deques.new(), []
        C.c_uint64.from_address(O['MCL_POOL']).value = self.pool
        push = lambda q, v, *_: self.deques[q].append(self.cell(v) if q == self.pool else v)
        fw.mock('fw__deque_push_back', push)
        mcl.pos = pos
        fw.var('playback__rng', C.c_uint).value = seed
        if shuffle:
            assert fw.fn('playback_set', C.c_int, C.c_int, C.c_int)(0, 1) == 1
        for k in range(1, len(names), 2):
            C.c_uint.from_address(self.records[k] + O['REC_ARTIST']).value = low.take(b'Artist %d\0' % k)

    def cell(self, v):
        self.ints.append(C.c_int(v))
        return C.addressof(self.ints[-1])

    def name(self, at):
        return C.string_at(C.c_uint.from_address(self.deques[self.handle][at] + O['REC_NAME']).value).decode()

    def names(self):
        return [self.name(i) for i in range(len(self.deques[self.handle]))]

    def queue(self, k, next_):
        """NAMES[k] queued again with Play Next (1) or Play Last (2), as the hold menu queues a song."""
        add = self.deques.new([self.low.record(NAMES[k], cue=100 + k)])
        return self.fw.fn('playback_queue_insert', C.c_uint, C.c_void_p, C.c_void_p, C.c_int)(self.handle, add, next_)

    def played(self, n):
        """The tracks the next n natural changes play, by name."""
        out = []
        for _ in range(n):
            assert self.fw.fn('ringnav_next', C.c_int, C.c_int)(1) >= 0
            out.append(self.name(self.mcl.pos))
        return out


class Page:
    """Up Next's sheet on stubs (menu_modal.c modal_sheet: stock's sortselect dialog, its list_view and scroll_view):
    widgets as zeroed blocks that keep their box, parent and children, their names and props, the handlers, the timers
    and the toasts."""
    def __init__(self, fw):
        self.fw, self.children, self.blocks, self.timers, self.toasts = fw, {}, [], [], []
        self.props, self.handlers, self.kinds, self.names, self.bound, self.strs = {}, {}, {}, {}, {}, []
        self.opened, self.closed = 0, []
        self._np = C.create_string_buffer(0x400)  # Now Playing, under the sheet until it opens
        self.top = C.addressof(self._np)
        fw.mock('window_manager_get_top_window', lambda *_: self.top)
        fw.mock('navigator_to', lambda name, *_: self.dialog(C.string_at(name)))
        fw.mock('widget_lookup', lambda w, name, deep, *_: self.lookup(w, C.string_at(name)))
        fw.mock('widget_set_name', lambda w, name, *_: self.names.__setitem__(w, C.string_at(name)) or 0)
        fw.mock('widget_get_prop_str', self.prop_str)
        fw.mock('widget_get_type', lambda w, *_: self.cstr(self.kinds.get(w, 'dialog').replace('_create', '').encode()))
        fw.mock('widget_get_visible', lambda w, *_: 1)
        fw.mock('widget_move_resize', self.place)
        fw.mock('window_close', self.close_window)
        for kind in ('scroll_view_create', 'list_view_create', 'list_item_create', 'label_create', 'image_create',
                     'hscroll_label_create'):
            fw.mock(kind, lambda parent, x=0, y=0, w=0, h=0, *_, kind=kind: self.widget(parent, kind, x, y, w, h))
        fw.mock('widget_count_children', lambda w, *_: len(self.children.get(w, [])))
        fw.mock('widget_get_child', lambda w, i, *_: (self.children.get(w, []) + [0] * (i + 1))[i])
        fw.mock('widget_restack', self.restack)
        fw.mock('widget_destroy_children', lambda w, *_: self.children.pop(w, None) and 0)
        fw.mock('widget_get_prop_int', self.prop)
        fw.mock('widget_set_prop_int', lambda w, key, v, *_: self.props.__setitem__((w, C.string_at(key)), C.c_int(v).value))
        fw.mock('widget_set_prop_str', lambda w, key, v, *_: self.props.__setitem__((w, C.string_at(key)), C.string_at(v).decode()))
        fw.mock('widget_on', self.on)
        fw.mock('timer_add', lambda fn, ctx, ms, *_: self.timers.append((fn, ms)) or len(self.timers))
        fw.mock('navigator_to_with_context', lambda page, info, *_: self.toasts.append(C.string_at(info + 8).decode()))

    def dialog(self, name):
        """navigator_to's sortselect dialog: Up Next's window on top, its list_view and scroll_view new."""
        if name == b'dialog/sortselect_dialog':
            self.opened += 1
            self.top = PAGE
            self.names[PAGE] = b'sortselect_dialog'
            self.children[PAGE] = []
            lv = self.widget(PAGE, 'list_view_create', 0, 44, 375, 276)
            self.names[lv] = b'list_view'
            self.names[self.widget(lv, 'scroll_view_create', 0, 0, 375, 276)] = b'scroll_view'
        return 0

    def lookup(self, w, name):
        for k in self.children.get(w, []):
            if self.names.get(k) == name:
                return k
            if found := self.lookup(k, name):
                return found
        return 0

    def cstr(self, b):
        self.strs.append(C.create_string_buffer(b))
        return C.addressof(self.strs[-1])

    def prop_str(self, w, key, default, *_):
        if not w:
            return default  # stock: widget_get_prop fails on a NULL widget
        name = C.string_at(key)
        v = self.names.get(w) if name == b'name' else self.props.get((w, name))
        kind = self.kinds.get(w, 'dialog').replace('_create', '').encode()
        v = stock_prop_str(kind, name, v.encode() if isinstance(v, str) else v, default)
        return 0 if v is None else self.cstr(v) if isinstance(v, bytes) else v

    def place(self, w, x, y, ww, h, *_):
        for off, v in zip(('W_X', 'W_Y', 'W_W', 'W_H'), (x, y, ww, h)):
            C.c_int.from_address(w + O[off]).value = v
        return 0

    def box(self, w):
        return tuple(C.c_int.from_address(w + O[k]).value for k in ('W_X', 'W_Y', 'W_W', 'W_H'))

    def on(self, w, event, fn, ctx, *_):
        """Each handler kept; the first of a kind is the one handlers[] answers (a row's click, the sheet's Return)."""
        self.bound.setdefault((w, event), []).append((fn, ctx))
        self.handlers.setdefault((w, event), (fn, ctx))
        return 1

    def close_window(self, w, *_):
        """window_close: its destroy handlers, each it was given (upnext.c's own, menu_modal.c's)."""
        self.closed.append(w)
        if w == PAGE:
            self.top = C.addressof(self._np)
        for fn, ctx in self.bound.pop((w, EVT_DESTROY), []):
            C.CFUNCTYPE(C.c_int, C.c_void_p, C.c_void_p)(fn)(ctx, None)
        self.handlers.pop((w, EVT_DESTROY), None)
        return 0

    def widget(self, parent, kind, x, y, w, h):
        self.blocks.append(C.create_string_buffer(0x400))
        b = C.addressof(self.blocks[-1])
        struct.pack_into('<iiii', self.blocks[-1], O['W_X'], x, y, w, h)
        struct.pack_into('<Q', self.blocks[-1], O['W_PARENT'], parent)
        self.children.setdefault(parent, []).append(b)
        self.kinds[b] = kind
        return b

    def restack(self, w, i, *_):
        kids = self.children[C.c_uint64.from_address(w + O['W_PARENT']).value]
        kids.remove(w)
        kids.insert(i, w)
        return 0

    def prop(self, w, key, default, *_):
        name = C.string_at(key)
        return 290 if name == b'h' else self.props.get((w, name), C.c_int(default).value)

    def open(self):
        self.fw.fn('up_next', None)()

    def close(self):
        """The sheet closed: its destroy handlers, upnext.c's own close and the sheet's."""
        self.close_window(PAGE)

    @property
    def view(self):
        return [w for w, k in self.kinds.items() if k == 'scroll_view_create'][-1]

    def kids(self):
        return self.children.get(self.view, [])

    @property
    def stops(self):
        """The rows the wheel stops on, in order: 'Clear', then each song's title."""
        out = []
        for w in self.kids():
            for x in [w] + self.children.get(w, []):
                if (x, EVT_CLICK) in self.handlers:
                    out.append(self.props.get((x, b'_un_title'), 'Clear'))
        return out

    @property
    def captions(self):
        return [s for s in self.stops if s != 'Clear']

    @property
    def queued(self):
        """Playing Next's songs: the rows between its heading and "From"."""
        kids = self.kids()
        if not any(self.kinds[w] == 'label_create' and self.children.get(w) for w in kids):
            return []
        end = [i for i, w in enumerate(kids) if self.props.get((w, b'_un_from'))]
        return [self.props[(w, b'_un_title')] for w in kids[2:end[0] if end else len(kids)]]  # past the header

    def playing(self):
        return self.props[(self.kids()[0], b'_un_title')]

    def selected(self):
        """The selected stop and the stops' count, as ringnav_select left them."""
        return self.props.get((self.view, SEL)), self.props.get((self.view, COUNT))

    def select(self, stop):
        self.props[(self.view, SEL)] = stop

    def tap(self, stop, x=None):
        """A stop pressed: Centre's click (no event), or a finger's at x."""
        rows = [x for w in self.kids() for x in [w] + self.children.get(w, []) if (x, EVT_CLICK) in self.handlers]
        fn, ctx = self.handlers[rows[stop], EVT_CLICK]
        event = None
        if x is not None:
            self.ev = C.create_string_buffer(0x40)
            struct.pack_into('<i', self.ev, O['EVENT_TYPE'], EVT_CLICK)
            struct.pack_into('<ii', self.ev, O['EVENT_X'], x, 100)
            event = C.addressof(self.ev)
        return C.CFUNCTYPE(C.c_int, C.c_void_p, C.c_void_p)(fn)(ctx, event)

    def run_last(self):
        """The timer last added, run from the next loop pass."""
        fn, _ = self.timers[-1]
        return C.CFUNCTYPE(C.c_int, C.c_void_p)(fn)(None)

    def run(self, ms):
        """The newest timer added for ms, run."""
        fn, _ = [t for t in self.timers if t[1] == ms][-1]
        return C.CFUNCTYPE(C.c_int, C.c_void_p)(fn)(None)

    def un_hold(self, top=PAGE):
        """navigation.c's ringnav_keylong handing a Play/Pause hold to Up Next, top the page on top: 1 when taken."""
        return self.fw.fn('un_hold', C.c_int, C.c_void_p)(top)

    def hold(self, stop):
        """Play/Pause held on a stop, and what it defers, run from the next loop pass."""
        self.select(stop)
        assert self.un_hold() == 1
        return self.run_last()

    def key(self, key, down=False):
        if down:
            return self.fw.fn('un_keydown', None, C.c_uint)(key)
        return self.fw.fn('un_key', C.c_int, C.c_void_p, C.c_uint, C.c_int)(PAGE, key, 0)


@pytest.fixture(scope='module')
def scratch(library):
    """scratch_t's field offsets as this build lays it out."""
    return host.offsets(library.parent/'navigation.o', 'scratch_t')


@pytest.fixture
def st(fw, scratch):
    base = C.addressof(fw.var('st', C.c_char))
    return lambda name, ctype=C.c_int: ctype.from_address(base + scratch[name])


@pytest.fixture
def up(fw, mcl, low):
    queue = Queue(fw, mcl, low)
    fw.mock('window_manager_get_top_window', lambda *_: PAGE)
    return queue, Page(fw)


@pytest.fixture
def plain(fw, mcl, low):
    """List play, no advanced play: the queue's own order, the third track playing."""
    queue = Queue(fw, mcl, low, shuffle=False)
    fw.mock('window_manager_get_top_window', lambda *_: PAGE)
    return queue, Page(fw)


def test_up_next_lists_the_playing_song_then_what_plays_in_order(fw, up):
    queue, page = up
    page.open()
    assert page.playing() == queue.name(queue.mcl.pos) and page.queued == []
    assert len(page.captions) == len(NAMES) - 1 and page.captions == queue.played(len(NAMES) - 1)
    assert page.selected() == (0, len(NAMES) - 1)  # the first song, no Clear


def test_up_next_stops_at_500_rows_of_a_longer_queue(fw, mcl):
    low = Low(1 << 18)
    fw.mock('window_manager_get_top_window', lambda *_: PAGE)
    queue, page = Queue(fw, mcl, low, names=[b'%03d Song.flac' % k for k in range(600)]), Page(fw)
    page.open()
    assert len(page.captions) == 500 and page.captions == queue.played(500)  # the next 500, in play order
    low.close()


def test_up_next_opens_in_the_menu_sheet_its_rows_at_their_own_heights(fw, plain, st):
    """Up Next is the menu sheet (menu_modal.c modal_sheet, docs/oled-design.md#up-next): stock's sortselect dialog
    named as its page was, the list from y 50 to the screen's foot, headed by the sheet's 72 px header space (the
    playing song, not a stop), then Playing Next's 24 px heading, its 46 px rows, "From ..." at 26 px and the rest.
    Until its first whole paint starts the rise the list waits the sheet's height (280 px) lower."""
    st('settings_read').value = 1
    queue, page = plain
    queue.queue(7, 1)
    page.open()
    assert page.names[PAGE] == b'upnext_page' and page.props[(PAGE, b'_mm_own')] == 1
    lv = C.c_uint64.from_address(page.view + O['W_PARENT']).value
    assert page.box(lv)[1:] == (50 + 280, 375, 270)  # its place 50 to the foot, below the screen until it rises
    kids = page.kids()
    assert page.names[kids[0]] == b'_mm_head' and page.playing() == queue.name(queue.mcl.pos)
    assert [page.box(w)[3] for w in kids[:5]] == [72, 24, 46, 26, 46]
    assert [page.box(w)[1] for w in kids[:3]] == [0, 72, 96]
    assert 'Clear' == page.stops[0] and queue.name(queue.mcl.pos) not in page.stops  # the header is no stop
    clear = page.children[kids[1]][0]
    for w in [clear] + kids[1:]:  # no paint of their own: the sheet shows round Clear's pill and under each row
        assert page.props.get((w, b'style:normal:bg_color')) == 0, w
    assert page.props.get((clear, b'style:normal:border_color')) == 0  # nor the row style's frame round the pill


def page_block(page, w):
    return next(b for b in page.blocks if C.addressof(b) == w)


@pytest.mark.parametrize('mode, n', [(0, 5), (3, 8), (1, 0)])  # List, Repeat All, Repeat One
def test_outside_advanced_play_up_next_is_the_queues_order(fw, plain, mode, n):
    queue, page = plain
    queue.mcl.mode = mode
    page.open()
    assert page.captions == (queue.names()[3:] + queue.names()[:3])[:n]


def test_a_tapped_row_plays_its_own_track_and_the_page_closes(fw, up):
    queue, page = up
    page.open()
    caption = page.captions[3]
    assert page.tap(3) == 0
    (at, *_), = [a for _, a in fw.calls('player_start_queue')]
    assert queue.name(at) == caption and fw.calls('navigator_back')


def test_a_row_starts_nothing_while_headphones_are_required_and_absent(fw, up):
    """Its check (it toasts) comes before the start, and Up Next stays with its selection, as any list a refused start
    is tapped on (output_rule.c)."""
    _, page = up
    page.open()
    fw.mock('check_forbid_play_no_headset', lambda *_: 1)
    page.tap(1)
    assert not fw.calls('player_start_queue') and not fw.calls('navigator_back')


@pytest.mark.parametrize('next_, order', [(1, ['00', '07']), (2, ['07', '00'])])
def test_play_next_and_play_last_show_under_playing_next_with_clear_first(fw, plain, next_, order):
    queue, page = plain
    queue.queue(7, 1)  # Play Next 07
    queue.queue(0, next_)  # 00, by Play Next (ahead of 07) or Play Last (after it)
    page.open()
    assert [q[:2] for q in page.queued] == order and page.stops[0] == 'Clear'
    assert page.captions[2:] == queue.names()[5:]  # the rest of the queue after them
    assert page.selected() == (1, len(page.stops))  # the first song, not Clear
    assert [n[:2] for n in queue.names()[3:5]] == order  # right after the playing one: they play next, in turn


def test_clear_takes_playing_next_out_of_the_queue(fw, plain):
    queue, page = plain
    before = queue.names()
    queue.queue(7, 1)
    queue.queue(0, 2)
    page.open()
    page.tap(0)  # Clear
    assert queue.names() == before and page.queued == [] and page.captions == before[3:]
    assert page.toasts == [] and queue.mcl.pos == 2 and page.selected() == (0, len(before) - 3)


def test_clear_under_advanced_play_follows_the_queue(fw, up):
    queue, page = up
    for r in queue.records:  # no artists: the insert groups the queue again, reading REC_ALBUM's cell whole
        C.c_uint.from_address(r + O['REC_ARTIST']).value = 0
    page.open()
    before = page.captions
    page.close()
    queue.queue(1, 1)
    page.open()
    assert len(page.queued) == 1
    page.tap(0)
    # the copy gone, every other song still to come (follow_queue draws the order again, as an insert does)
    assert page.queued == [] and sorted(page.captions) == sorted(before) and len(queue.names()) == len(NAMES)


def test_a_hold_bumps_a_queued_row_to_the_top_with_its_edge_lit(fw, plain):
    queue, page = plain
    for at in (5, 6, 7):
        queue.queue(at, 2)  # Play Last: 05, 06, 07 in turn
    page.open()
    assert [q[:2] for q in page.queued] == ['05', '06', '07']
    page.hold(3)  # 07, the third queued (Clear is stop 0)
    assert [q[:2] for q in page.queued] == ['07', '05', '06'] and page.selected()[0] == 3  # the selection stays
    assert [ms for _, ms in page.timers].count(700) == 1 and page.toasts == []  # the edge's time (UN_FRESH_MS)
    assert [n[:2] for n in queue.names()[3:6]] == ['07', '05', '06']  # the rows moved: so they play


def test_a_hold_on_the_first_queued_or_a_coming_row_moves_nothing_but_is_taken(fw, plain):
    queue, page = plain
    queue.queue(7, 2)
    queue.queue(6, 2)
    page.open()
    before = page.stops
    page.hold(1)  # the first queued: already next
    page.hold(4)  # a row of the rest of the queue
    assert page.stops == before and page.un_hold(0x7f00) == 0 and page.un_hold(None) == 0


def test_a_tap_on_a_queued_rows_arrow_bumps_it(fw, plain, st):
    queue, page = plain
    queue.queue(7, 2)
    queue.queue(6, 2)
    page.open()
    st('touch_mode').value = 1
    page.tap(2, x=360)
    assert [q[:2] for q in page.queued] == ['06', '07'] and not fw.calls('player_start_queue')


def lift(page):
    """Centre held on the selected row: its hold's timer run, the release taken."""
    page.key(KEY_CENTER, down=True)
    page.run(550)
    assert page.key(KEY_CENTER) == 1


def test_centre_held_lifts_a_queued_row_the_wheel_carries_it_and_centre_puts_it_down(fw, plain):
    queue, page = plain
    for at in (5, 6, 7):
        queue.queue(at, 2)
    page.open()
    page.select(1)  # 05
    lift(page)
    assert page.key(KEY_NEXT) == 1 and page.key(KEY_NEXT) == 1 and page.key(KEY_NEXT) == 1  # stops at the last
    assert page.selected()[0] == 3
    assert page.key(KEY_CENTER) == 1
    assert [q[:2] for q in page.queued] == ['06', '07', '05'] and page.selected()[0] == 3
    assert [n[:2] for n in queue.names()[3:6]] == ['06', '07', '05']


def test_return_puts_a_lifted_row_back(fw, plain):
    queue, page = plain
    for at in (5, 6):
        queue.queue(at, 2)
    page.open()
    page.select(1)
    lift(page)
    page.key(KEY_NEXT)
    assert page.key(KEY_RETURN) == 1
    assert [q[:2] for q in page.queued] == ['05', '06'] and page.selected()[0] == 1


def test_centre_held_on_a_coming_row_lifts_nothing(fw, plain):
    queue, page = plain
    queue.queue(7, 2)
    page.open()
    page.select(2)  # the rest of the queue
    timers = len(page.timers)
    page.key(KEY_CENTER, down=True)
    assert len(page.timers) == timers and page.key(KEY_CENTER) == 0 and page.key(KEY_NEXT) == 0


def test_the_rows_follow_the_queue_as_it_plays(fw, plain):
    queue, page = plain
    for r in queue.records:  # no artists: the header reads the playing song's album, whose cell an artist fills here
        C.c_uint.from_address(r + O['REC_ARTIST']).value = 0
    page.open()
    before = page.captions
    assert page.run(1000) == 8  # nothing moved: the same rows
    queue.mcl.pos += 1  # the next song plays
    page.run(1000)
    assert page.captions == before[1:] and page.playing() == before[0]


def test_up_next_opens_one_page_at_a_time_and_again_once_closed(fw, up):
    _, page = up
    page.open()
    page.open()
    assert page.opened == 1
    page.close()
    page.open()
    assert page.opened == 2 and len(page.captions) == len(NAMES) - 1


def test_return_lets_the_sheet_go_and_up_next_opens_again(fw, up):
    """Return on the sheet (menu_modal.c modal_close: before its first whole paint, at once) closes it and Up Next with
    it. Any other key's release is not the sheet's."""
    _, page = up
    page.open()
    fn, ctx = page.handlers[PAGE, O['EVT_KEY_UP']]
    ev = C.create_string_buffer(0x40)
    struct.pack_into('<i', ev, O['EVENT_TYPE'], O['EVT_KEY_UP'])
    struct.pack_into('<i', ev, O['EVENT_KEY'], KEY_NEXT)
    assert C.CFUNCTYPE(C.c_int, C.c_void_p, C.c_void_p)(fn)(ctx, C.addressof(ev)) == 0 and page.closed == []
    struct.pack_into('<i', ev, O['EVENT_KEY'], KEY_RETURN)
    C.CFUNCTYPE(C.c_int, C.c_void_p, C.c_void_p)(fn)(ctx, C.addressof(ev))
    assert page.closed == [PAGE]
    page.open()
    assert page.opened == 2 and page.captions


def test_the_header_s_level_bars_ride_the_shared_loop_and_repaint_only_their_box(fw, plain):
    """The user's (08:35): smoother bars. Up Next's header bars move on ui_kit's one 33 ms loop (lvl_h16's continuous
    heights), each frame repainting the 12 x 13 box they were last painted in, not the header; paused, they hold and
    the loop drops them."""
    from test_nav_painters import Canvas
    _, page = plain
    now, status, dirty = [5000], [2], []
    fw.mock('time_now_ms', lambda *_: now[0])
    fw.mock('mclGetPlayStatus', lambda *_: status[0])
    fw.var('g_backlight_status', C.c_ubyte).value = 1
    loops = C.c_char.in_dll(fw.lib, 'ui_kit__loops')
    C.memset(C.addressof(loops), 0, fw.sizes['ui_kit__loops'])
    page.open()
    assert [ms for _, ms in page.timers].count(33) == 1  # the shared loop; no 125 ms timer of its own
    def paint():
        canvas = Canvas(fw, x=0, y=60)  # the header, painted 60 px down the screen
        fw.fn('un_bars', None, C.c_void_p, C.c_int, C.c_int)(canvas.addr, 200, 28)
        return canvas.boxes
    first = paint()
    fw.mock('widget_invalidate_force', lambda w, r, *_: dirty.append((w, struct.unpack('<4i', C.string_at(r, 16)) if r else None)))
    now[0] += 33
    assert page.run(33) == 8 and dirty == [(PAGE, (200, 60 + 28 - 13, 12, 13))]
    moved = paint()
    assert moved != first
    status[0] = 3
    dirty.clear()
    assert page.run(33) == 7 and dirty == []  # paused: RET_REMOVE, nothing repainted
    held = paint()
    now[0] += 500
    assert paint() == held


def finger(page, at, to, scrolled=0, aborts=None):
    """A finger down at at, moving to to a sample every 4 px as the panel reports it, and up there, on the sheet (its
    window's pointer handlers, before its children's), the rows scrolled down by scrolled; then what that asked of the
    next pass: 1 when the sheet was closed. As stock's scroll_view does on the emulator, a move the window's handlers
    let through once the finger is TAP_PX from where it came down is the rows' drag, and aborts the press through the
    window (EVT_POINTER_ABORT); aborts, when given, collects the aborts the sheet sent its own children."""
    C.c_int.from_address(page.view + O['SCROLL_Y']).value = scrolled
    timers = len(page.timers)
    sent = []
    page.fw.mock('pointer_event_init', lambda out, kind, *_: struct.pack_into('<i', (C.c_char * 4).from_address(out), 0,
                                                                               kind) or out)
    page.fw.mock('widget_dispatch_event_to_target_recursive',
                 lambda w, e, *_: sent.append((w, C.c_int.from_address(e).value)) or 0)

    def send(kind, x, y):
        ev = C.create_string_buffer(0x40)
        struct.pack_into('<i', ev, O['EVENT_TYPE'], kind)
        struct.pack_into('<ii', ev, O['EVENT_X'], x, y)
        return [C.CFUNCTYPE(C.c_int, C.c_void_p, C.c_void_p)(fn)(ctx, C.addressof(ev))
                for fn, ctx in page.bound.get((PAGE, kind), [])]

    assert send(O['EVT_POINTER_DOWN'], *at) == [0] * len(page.bound.get((PAGE, O['EVT_POINTER_DOWN']), []))
    n = max(abs(to[0] - at[0]), abs(to[1] - at[1])) // 4
    rows_drag = False
    for i in range(1, n + 1):
        x, y = at[0] + (to[0] - at[0]) * i // n, at[1] + (to[1] - at[1]) * i // n
        if STOP in send(O['EVT_POINTER_MOVE_BEFORE'], x, y):
            continue
        if not rows_drag and max(abs(x - at[0]), abs(y - at[1])) >= TAP_PX:
            rows_drag = True  # the rows' scroll takes the drag: the window hears the press aborted
            send(O['EVT_POINTER_ABORT'], x, y)
    send(O['EVT_POINTER_UP_BEFORE'], *to)
    if aborts is not None:
        aborts.extend(sent)
    for fn, _ in page.timers[timers:]:
        info = C.create_string_buffer(0x40)
        C.c_void_p.from_address(C.addressof(info) + O['TIMER_CTX']).value = PAGE
        C.CFUNCTYPE(C.c_int, C.c_void_p)(fn)(C.addressof(info))
    return int(PAGE in page.closed)


@pytest.mark.parametrize('at, to', [((180, 20), (184, 24)), ((30, 5), (30, 5)), ((360, 38), (352, 32))],
                         ids=['scrim', 'status-row', 'scrim-wobble'])
def test_a_tap_on_the_dimmed_page_above_the_sheet_closes_it(fw, up, at, to):
    """The user's, 2026-11-18 00:06: only Return closed Up Next. A tap above the sheet's top (y 40), on the page
    receding behind it, lets it fall away as Return does (menu_modal.c modal_close)."""
    _, page = up
    page.open()
    assert finger(page, at, to) == 1


def test_a_drag_down_on_the_sheet_closes_it(fw, up):
    """A drag down of 48 px or more, more down than across, that came down on the grab bar's strip or on the rows
    while they are at their top, lets the sheet fall away; a drag on rows scrolled down scrolls them."""
    _, page = up
    page.open()
    aborts: list = []
    assert finger(page, (187, 46), (190, 94), aborts=aborts) == 1  # the grab bar, 48 px down
    assert aborts == [(PAGE, O['EVT_POINTER_ABORT'])]  # claimed: the sheet's pressed widgets let go


@pytest.mark.parametrize('at, to', [((140, 46), (220, 185)), ((130, 46), (230, 189)), ((120, 150), (210, 280))],
                         ids=['grab-30deg', 'grab-35deg', 'rows-35deg'])
def test_a_thumb_s_slanted_drag_down_closes_the_sheet(fw, up, at, to):
    """The user's, 2026-11-21, on the device: a swipe down still left Up Next up. A thumb holding the Q2 swipes down
    on a slant, 30 to 35 degrees off the vertical; driven so on the emulator with the panel's own events, the sheet
    claimed the drag (more down than across) and followed it, then rose back at the lift, which also asked for less
    than half as far across as down. Once claimed, the drop alone decides, as the sheet follows only the drop."""
    _, page = up
    page.open()
    assert finger(page, at, to) == 1


@pytest.mark.parametrize('at, to, scrolled', [
    ((187, 120), (187, 160), 0),   # 40 px: short of it
    ((187, 120), (260, 180), 0),   # as much across as down
    ((187, 180), (187, 120), 0),   # up: the rows scroll
    ((187, 120), (187, 220), 92),  # the rows scrolled down: they scroll back
    ((180, 20), (180, 80), 0),     # from above the sheet: no tap, no drag on it
    ((187, 200), (187, 205), 0),   # a tap on a row: the row's
], ids=['short', 'sideways', 'up', 'scrolled', 'from-scrim', 'row-tap'])
def test_what_does_not_close_the_sheet(fw, up, at, to, scrolled):
    _, page = up
    page.open()
    assert finger(page, at, to, scrolled) == 0


def test_rows_at_their_top_drag_the_sheet_down(fw, up):
    """The user's, 2026-11-18 05:08, on the device: a pull down from the grab bar left the sheet up. A finger that
    lands on the rows (from y 50, a few px under the bar), at their top, is the rows' scroll's once it has moved
    TAP_PX, and stock's scroll_view aborts the press through the window: the sheet claims the drag in the move
    before that, so the rows never take it."""
    _, page = up
    page.open()
    assert finger(page, (187, 58), (187, 160)) == 1
    page.open()
    assert finger(page, (187, 150), (187, 260)) == 1


def test_a_row_of_the_playing_songs_album_takes_the_cover_now_playing_shows(fw, mcl, recs):
    """The user's, 2026-11-17: Up Next's rows of "Idle Moments" were grey: their album had no cover saved by
    Subsonic and none in Cover Flow's cache, though Now Playing showed the song's own art. A row of the playing
    song's album takes the player's cover of that song; a row of another album never does."""
    fw.mock('window_manager_get_top_window', lambda *_: PAGE)
    page = Page(fw)
    fw.var('fw_ctx_mcl_pdeqplaylist', C.c_uint).value = Deques(fw).new([
        recs(path='/mnt/mmc/.subsonic/stream/a/original/0/Jean De Fleur [s1].flac', title='Jean De Fleur',
             artist='Grant Green', album='Idle Moments'),
        recs(path='/mnt/mmc/.subsonic/stream/a/original/0/Idle Moments [s2].flac', title='Idle Moments',
             artist='Grant Green', album='Idle Moments'),
        recs(path='/mnt/mmc/m/Speak No Evil.flac', title='Speak No Evil', artist='Wayne Shorter',
             album='Speak No Evil'),
    ])
    mcl.pos = 0
    fw.var('fw_ctx_g_lastcover_url', C.c_char * 1024).value = b'/mnt/mmc/.subsonic/stream/a/original/0/Jean De Fleur [s1].flac'
    fw.var('g_playcover_type', C.c_ubyte).value = 1  # embedded art, the player's /tmp/coverpic.jpg
    covers: list[str] = []
    fw.mock('image_base_set_image', lambda w, name, *_: covers.append(C.string_at(name).decode()) or 0)
    page.open()
    assert len(covers) == 1 and covers[0].endswith('/tmp/coverpic.jpg')  # Idle Moments' row; not Speak No Evil's
    starts: list[str] = []
    def create(t, attr, fn, job, *_):  # started: the fetch runs (busy) until the test ends
        starts.append(C.string_at(job + 8).decode())
        C.c_ulong.from_address(t).value = 1
        return 0
    fw.mock('fw_pthread_create', create)
    page.close()
    page.open()
    assert starts == ['/mnt/mmc/m/Speak No Evil.flac']  # Speak No Evil's cover fetched from its file, once
    fw.var('album_cover__ac', C.c_uint).value = 0  # the fetch over (ac.busy), for the tests after


# Remove from Queue (the song menu's row on a queued song, upnext.c un_remove, playback.c playback_queue_remove).
def remove(fw, at):
    return fw.fn('playback_queue_remove', C.c_int, C.c_uint)(at)


def test_remove_from_queue_takes_a_coming_song_out_and_the_playing_one_plays_on(fw, plain):
    queue, page = plain
    before = queue.names()
    assert remove(fw, 5) == 1
    assert queue.names() == before[:5] + before[6:] and queue.mcl.pos == 2 and queue.name(2) == before[2]
    page.open()
    assert before[5] not in page.captions and page.captions == before[3:5] + before[6:]


def test_removing_a_playing_next_song_shortens_playing_next(fw, plain):
    queue, page = plain
    queue.queue(7, 1)  # Play Next 07: at 3
    queue.queue(0, 2)  # Play Last 00: at 4
    assert remove(fw, 3) == 1
    page.open()
    assert [q[:2] for q in page.queued] == ['00'] and queue.mcl.pos == 2


def test_removing_a_song_before_the_playing_one_keeps_it_playing(fw, plain):
    queue, page = plain
    playing = queue.name(2)
    assert remove(fw, 0) == 1 and queue.mcl.pos == 1 and queue.name(1) == playing


def test_remove_under_advanced_play_keeps_every_other_song_to_come(fw, up):
    """Shuffle Songs' order is drawn again over the rows left (follow_queue): which song comes when is the order's,
    so only the set is checked, not the order."""
    queue, page = up
    for r in queue.records:  # no artists: the regrouping reads REC_ALBUM's cell whole
        C.c_uint.from_address(r + O['REC_ARTIST']).value = 0
    page.open()
    before = page.captions
    page.close()
    playing = queue.name(queue.mcl.pos)
    gone = next(i for i in range(len(NAMES)) if i != queue.mcl.pos)
    name = queue.name(gone)
    assert remove(fw, gone) == 1
    assert queue.name(queue.mcl.pos) == playing and name not in queue.names() and len(queue.names()) == len(NAMES) - 1
    page.open()
    assert sorted(page.captions) == sorted(c for c in before if c != name)


def test_the_playing_song_is_never_removed(fw, plain):
    queue, page = plain
    before = queue.names()
    assert remove(fw, 2) == 0 and queue.names() == before
