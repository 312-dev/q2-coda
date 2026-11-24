"""A held Centre or finger on Up Next's rows (upnext.c un_menu_hold, centre_menu.c, docs/internals.md#centre-hold-menu):
every kind of row the queue can show gets the song's menu at CENTRE_MENU_MS, or, where no menu applies, nothing at
all, and never stock's Shut Down? at 1000 ms. The rows: the queue's coming songs, Playing Next's, a shuffle's
played songs above the playing one, a card app's stream session's songs not yet queued (pending rows: the owner's
Subsonic queue, almost all of it, 2026-11-24, "holding center button on items in queue ... just brings up shutdown
screen"), and Clear. Up Next is opened in test_nav_hold's Ui world, as Now Playing's queue button opens it
(navigation.c np_queue). A pending row's record (addon.c pending_record) is the Q2's 4-byte layout, which the
host's 8-byte P() cannot draw: its menu is proven on the emulator against the fake Subsonic server instead."""
import ctypes as C

import pytest

from conftest import O
from test_centre_menu import (EVT_POINTER_ABORT, EVENT_TYPE, HOLD_MS, POINTER_DOWN, STOP, Centre, finger,  # noqa: F401
                              to_local)
from test_nav_hold import Hold, Ui, field, recs, scratch, song, st, use_set  # noqa: F401
from test_nav_loop import stock_bytes  # noqa: F401

KEY_CENTER = O['KEY_CENTER']
SEL = '_ringnav_index'


@pytest.fixture
def ui(fw):
    world = Ui(fw)
    yield world
    assert not world.errors


@pytest.fixture
def hold(fw, ui, st, recs, mcl):
    use_set(st)
    return Hold(fw, ui, st, recs, mcl)


@pytest.fixture
def centre(fw, ui, st, stock_bytes):
    return Centre(fw, ui, st, stock_bytes)


class Sheet:
    """Up Next over a queue of songs, the third playing: its rows as up_next() builds them."""
    def __init__(self, fw, ui, hold, recs, n=6, shuffle=False, played=0, queued=False):
        self.fw, self.ui, self.hold = fw, ui, hold
        self.songs = [song(recs, i) for i in range(n)]
        hold.now_playing(self.songs, at=0, held=True)
        pool, self.cells = C.c_uint64.from_address(O['MCL_POOL']).value & 0xffffffff, []
        def push(q, v, *_):  # stock's shuffle pool holds ints: deque_at gives an element's address
            if q & 0xffffffff == pool:
                self.cells.append(C.c_int(v))
                v = C.addressof(self.cells[-1])
            return hold.deques.push(q, v)
        fw.mock('fw__deque_push_back', push)
        fw.mock('fw_deque_pop_back', lambda q, *_: hold.deques[q].pop() and 0)
        if shuffle:
            assert fw.fn('playback_set', C.c_int, C.c_int, C.c_int)(0, 1) == 1
        for _ in range(played):
            assert fw.fn('ringnav_next', C.c_int, C.c_int)(1) >= 0
        if queued:  # a song played next: Playing Next and its Clear head the sheet
            add = hold.deques.rows([song(recs, 9)])
            fw.fn('playback_queue_insert', C.c_uint, C.c_void_p, C.c_void_p, C.c_int)(hold.queue, add, 1)
        fw.fn('up_next', None)()
        self.page = hold.dialog
        assert ui.top() == self.page
        self.view = ui.lookup(self.page, 'scroll_view', 1)
        C.c_ubyte.from_address(self.view + O['VIEW_VERTICAL']).value = 1  # stock's sortselect list scrolls so

    def stops(self):
        """Each stop's words: a song's title, 'Clear' for the pill."""
        out = []
        for k in self.ui.kids(self.view):
            node = self.ui.nodes[k]
            if '_un_stop' in node.props:
                out.append(node.props.get('_un_title', 'Clear'))
            for c in self.ui.kids(k):
                if '_un_stop' in self.ui.nodes[c].props:
                    out.append('Clear')
        return out

    def select(self, stop):
        self.fw.fn('ringnav_select', None, C.c_void_p, C.c_int, C.c_int)(self.view, stop, len(self.stops()))

    def menu(self):
        """The menu over the sheet once its open ran: its rows, None when none opened."""
        self.ui.due('qm_open')
        if self.hold.dialog == self.page or self.hold.dialog not in self.ui.stack:
            return None
        return [r for r in self.ui.rows(self.hold.view()) if r is not None]  # a gap has no words


SONG_MENU = ['Play Next', 'Play Last', 'Add to On-The-Go', 'Add to Playlist…', 'Favorite', 'Go to Album',
             'Go to Artist', 'Remove from Queue']


def held(centre, sheet, stop):
    sheet.select(stop)
    centre.press()
    assert centre.wait()
    return sheet.menu()


def test_centre_held_on_a_coming_song_opens_its_menu(fw, ui, hold, recs, centre):
    s = Sheet(fw, ui, hold, recs)
    assert s.stops() == ['Song 1', 'Song 2', 'Song 3', 'Song 4', 'Song 5']
    assert held(centre, s, 1) == SONG_MENU and not centre.shutdown_pending()


def test_centre_held_on_a_shuffles_played_song_opens_its_menu(fw, ui, hold, recs, centre):
    """Shuffle on, two songs played: they stand above the playing one, and each gets the menu a coming song gets."""
    s = Sheet(fw, ui, hold, recs, shuffle=True, played=2)
    stops = s.stops()
    assert len(stops) == 5
    assert held(centre, s, 0) == SONG_MENU and not centre.shutdown_pending()
    assert centre.release() == STOP  # the release plays nothing


def test_a_finger_held_on_a_played_song_opens_its_menu(fw, ui, hold, recs):
    s = Sheet(fw, ui, hold, recs, shuffle=True, played=2)
    to_local(ui)
    aborts = []
    fw.mock('widget_dispatch_event_to_target_recursive',
            lambda w, e, *_: aborts.append(C.c_int.from_address(e + EVENT_TYPE).value) or 0)
    rows = [k for k in ui.kids(s.view) if '_un_stop' in ui.nodes[k].props]
    x, y, w = 100, 10, rows[0]
    while w in ui.nodes:
        dx, dy = ui.nodes[w].rect()[:2]
        x, y, w = x + dx, y + dy, ui.nodes[w].parent
    ui.pressed = 1
    finger(fw, POINTER_DOWN, x, y)
    ui.due('cm_touch_due')
    assert aborts == [EVT_POINTER_ABORT] and s.menu() == SONG_MENU


def test_remove_from_queue_on_a_played_song_takes_it_out_and_the_playing_one_plays_on(fw, ui, hold, recs, centre, mcl):
    s = Sheet(fw, ui, hold, recs, shuffle=True, played=2)
    playing = field(hold.deques[hold.queue][mcl.pos], O['REC_PATH'])
    n = len(hold.deques[hold.queue])
    held(centre, s, 0)
    hold.pick('Remove from Queue')
    assert len(hold.deques[hold.queue]) == n - 1 and field(hold.deques[hold.queue][mcl.pos], O['REC_PATH']) == playing


def test_centre_held_on_clear_opens_nothing_and_never_shut_down(fw, ui, hold, recs, centre, st):
    """Clear (Playing Next's pill) has no menu: the hold does nothing, its Shut Down? is let go before it comes, and
    the release clears nothing."""
    s = Sheet(fw, ui, hold, recs, queued=True)
    assert s.stops()[:2] == ['Clear', 'Song 9']
    s.select(0)
    centre.press()
    centre.wait()
    assert s.menu() is None and centre.shutdown_pending()  # nothing at 500 ms, Shut Down? not yet let go
    ui.due('cm_guard_due')
    assert not centre.shutdown_pending() and centre.release() == STOP


def test_centre_held_where_no_row_is_keeps_shut_down(fw, ui, hold, recs, centre):
    """Now Playing has no row: its hold is the screen's off at a release past 400 ms (now_playing.c np_centre), and
    Shut Down? at 1000 ms."""
    hold.now_playing([song(recs, i) for i in range(3)])
    centre.press()
    centre.wait()
    ui.due('cm_guard_due')
    assert centre.shutdown_pending()
