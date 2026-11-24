"""centre_menu.c: Centre or a finger held on a song's row opens the song's hold menu (docs/internals.md#centre-hold-menu),
half way to stock's Shut Down? hold, through input.c's key and pointer hooks and the hold menu's own code
(hold_menu.c qm_centre_hold, qm_touch_hold): an album's tracks, Songs, Up Next's queue; rows that are no song, a
double press, a dark screen, a rim chord, a drag and Edit Library's hold keep what they did."""
import ctypes as C
import struct

import pytest

from conftest import O, claim, ui_name
from test_nav_hold import (Hold, Ui, recs, scratch, song, st, use_set)  # noqa: F401
from test_nav_loop import key_event, stock_bytes  # noqa: F401
from test_upnext import PAGE, Low, Page, Queue, low  # noqa: F401

KEY_CENTER, KEY_PLAY, KEY_RETURN = O['KEY_CENTER'], O['KEY_PLAY'], O['KEY_RETURN']
DOWN, UP = O['EVT_KEY_DOWN'], O['EVT_KEY_UP']
EVENT_TYPE, EVENT_X, EVENT_Y = O['EVENT_TYPE'], O['EVENT_X'], O['EVENT_Y']
EVT_POINTER_ABORT, POINTER_DOWN, POINTER_MOVE, POINTER_UP = O['EVT_POINTER_ABORT'], 0xff, 0x101, 0x103
STOP = 11
HOLD_MS, STOCK_MS = 500, 1000  # half of stock's Centre hold (0x6b3654 li $a2, 0x3e8)
SONG_ROWS = ['Play Next', 'Play Last', 'Add to On-The-Go', 'Add to Playlist…', 'Favorite', 'Go to Album', 'Go to Artist']


@pytest.fixture
def ui(fw):
    world = Ui(fw)
    yield world
    assert not world.errors


class Centre:
    """A Centre press as input.c sees it: chord_gate's press time (centre_at) and stock's hold timer (centre_hold, a
    1000 ms timer ringnav_hold_timer names) set, then ringnav_keydown; its release through ringnav, the key filter."""
    def __init__(self, fw, ui, st, stock_bytes):
        self.fw, self.ui, self.st = fw, ui, st
        for at in (O['KEY_LOCKOUT'], O['BOOT_KEY_GUARD'], O['RETURN_RELEASE_LATCH']):
            stock_bytes(at, 1)
        stock_bytes(O['SCAN_DONE'])
        fw.mock('stock_keydown_trampoline', lambda *_: 0)
        self.keydown = fw.fn('ringnav_keydown', C.c_int, C.c_void_p, C.c_void_p)
        self.ringnav = fw.fn('ringnav', C.c_int, C.c_void_p, C.c_void_p)
        self.stock = 0

    def press(self, at=0x5001):
        self.ui.press(KEY_CENTER)
        self.st('centre_at').value = at
        self.stock = self.ui.add_timer(0x4f0001, 0, STOCK_MS)  # stock's own hold, Shut Down?
        self.st('centre_hold').value = self.stock
        return self.keydown(None, key_event(KEY_CENTER, DOWN))

    def wait(self):
        """The 500 ms the hold waits, run; then the menu's deferred open."""
        ran = self.ui.due('cm_centre_due')
        self.ui.due('qm_open')
        return ran

    def release(self):
        self.st('centre_at').value = 0  # chord_gate's, on the release
        self.st('centre_hold').value = 0
        return self.ringnav(None, key_event(KEY_CENTER, UP))

    def shutdown_pending(self):
        return self.stock in self.ui.timers


@pytest.fixture
def centre(fw, ui, st, stock_bytes):
    use_set(st)
    return Centre(fw, ui, st, stock_bytes)


@pytest.fixture
def hold(fw, ui, st, recs, mcl):
    return Hold(fw, ui, st, recs, mcl)


def armed(ui):
    return [ms for fn, _, ms in ui.timers.values() if fn == ui.fn('cm_centre_due')]


def test_centre_held_on_an_albums_track_opens_its_menu_at_half_the_shutdown_hold(hold, centre, recs, ui, st):
    tracks = [song(recs, i) for i in range(3)]
    hold.songs_page('album_page', tracks, sel=1, cls=0xff10)  # an album's own tracks
    centre.press()
    assert armed(ui) == [HOLD_MS]
    centre.wait()
    assert hold.dialog in ui.stack and hold.title() == 'Song 1'
    assert ui.rows(hold.view()) == [r for r in SONG_ROWS if r != 'Go to Album']  # it is that album's list
    assert not centre.shutdown_pending()  # this press never reaches Shut Down?


def test_the_hold_s_release_plays_nothing_and_picks_nothing(hold, centre, recs, ui, st):
    hold.songs_page('allmusic_page', [song(recs, i) for i in range(3)], sel=2)
    centre.press()
    centre.wait()
    assert centre.release() == STOP
    assert not st('center_timer').value and hold.dialog in ui.stack  # no row's open pending, the menu stays


def test_songs_gets_the_same_menu_as_play_pause_s_hold(hold, centre, recs, ui):
    hold.songs_page('allmusic_page', [song(recs, i) for i in range(3)], sel=2)
    centre.press()
    centre.wait()
    assert ui.rows(hold.view()) == SONG_ROWS and hold.title() == 'Song 2'


@pytest.mark.parametrize('name, cls', [('localclass_page', 0xf003), ('localclass_page', 0xf004)])
def test_an_album_or_artist_row_keeps_stock_s_hold(hold, centre, recs, ui, st, name, cls):
    rows = [recs(album=a, artist='The Beatles', kind=4, id_=i + 1) for i, a in enumerate(('Help!', 'Revolver'))]
    hold.songs_page(name, rows, sel=1, cls=cls)
    centre.press()
    centre.wait()
    assert hold.dialog not in ui.stack and centre.shutdown_pending()  # Shut Down? still comes at 1000 ms
    centre.release()
    assert st('center_timer').value  # the release is a press: the row opens once DOUBLE_CLICK_MS is up


def test_a_released_press_opens_nothing(hold, centre, recs, ui):
    hold.songs_page('allmusic_page', [song(recs, i) for i in range(3)], sel=0)
    centre.press()
    centre.release()
    assert armed(ui) == [] and not centre.wait()


def test_a_rim_chord_that_ended_stock_s_hold_opens_nothing(hold, centre, recs, ui, st):
    hold.songs_page('allmusic_page', [song(recs, i) for i in range(3)], sel=0)
    centre.press()
    st('centre_hold').value = 0  # chord_gate: an edge button joined within CHORD_MS
    centre.wait()
    assert hold.dialog not in ui.stack


def test_the_second_press_of_a_double_press_arms_nothing(hold, centre, recs, ui, st):
    hold.songs_page('allmusic_page', [song(recs, i) for i in range(3)], sel=0)
    st('center_timer').value = ui.add_timer(0x4f0002, 0, 250)  # the first press's open, pending
    centre.press()
    assert armed(ui) == []


def test_a_dark_screen_arms_nothing(hold, centre, recs, ui, fw):
    hold.songs_page('allmusic_page', [song(recs, i) for i in range(3)], sel=0)
    fw.var('g_backlight_status', C.c_ubyte).value = 0  # asleep: the press only wakes (Buttons Lock too)
    centre.press()
    assert armed(ui) == []


def test_now_playing_keeps_its_own_centre(hold, centre, recs, ui):
    hold.now_playing([song(recs, i) for i in range(3)])
    centre.press()
    centre.wait()
    assert hold.dialog not in ui.stack and centre.shutdown_pending()


def test_play_pause_s_hold_on_a_song_still_latches_its_release(hold, recs, ui, st):
    """qm_hold's Play/Pause path is unchanged by Centre's: its release is latched by the key's press time."""
    hold.songs_page('allmusic_page', [song(recs, i) for i in range(3)], sel=2)
    assert hold.hold() == STOP and st('qm_press', C.c_ulonglong).value == 0x51


def test_the_library_page_keeps_edit_library_s_hold(fw, centre, ui, st):
    """Centre held on the Library (localmusic_page) is Edit Library's at LR_HOLD_MS (library_edit.c): the menu's
    500 ms finds no song there and leaves stock's hold and the editor's to come."""
    page = ui.window('localmusic_page')
    centre.press()
    centre.wait()
    assert ui.top() == page and centre.shutdown_pending()


# A finger held on a song's row.
def to_local(ui):
    """widget_to_local over the Ui's widgets: the point less each box's place from the widget up."""
    def local(w, xy, *_):
        x, y = struct.unpack_from('<2i', C.string_at(xy, 8))
        while w in ui.nodes:
            dx, dy = ui.nodes[w].rect()[:2]
            x, y, w = x - dx, y - dy, ui.nodes[w].parent
        struct.pack_into('<2i', (C.c_char * 8).from_address(xy), 0, x, y)
        return 0
    ui.fw.mock('widget_to_local', ui.guard(local))


def finger(fw, kind, x, y):
    """ringnav_input's raw pointer event: the panel's portrait axes (UI x is raw y, UI y is 319 less raw x)."""
    fw.fn('cm_touch', None, C.c_uint, C.c_int, C.c_int)(kind, 319 - y, x)


def test_a_finger_held_on_an_albums_track_selects_it_and_opens_its_menu(hold, recs, ui, fw, st):
    hold.songs_page('album_page', [song(recs, i) for i in range(3)], sel=0, cls=0xff10)
    to_local(ui)
    aborts = []
    fw.mock('widget_dispatch_event_to_target_recursive', lambda w, e, *_: aborts.append(C.c_int.from_address(e + EVENT_TYPE).value) or 0)
    ui.pressed = 1
    finger(fw, POINTER_DOWN, 100, 30 + 72 * 2 + 20)  # on the third row
    assert [ms for fn, _, ms in ui.timers.values() if fn == ui.fn('cm_touch_due')] == [HOLD_MS]
    ui.due('cm_touch_due')
    ui.due('qm_open')
    assert hold.dialog in ui.stack and hold.title() == 'Song 2'
    assert ui.prop(hold.list, '_ringnav_index', -1) == 2
    assert aborts == [EVT_POINTER_ABORT]  # the row's press let go: the lift clicks nothing


def test_a_drag_opens_nothing(hold, recs, ui, fw):
    hold.songs_page('album_page', [song(recs, i) for i in range(3)], sel=0, cls=0xff10)
    ui.pressed = 1
    finger(fw, POINTER_DOWN, 100, 120)
    finger(fw, POINTER_MOVE, 100, 132)  # a scroll's first 12 px
    assert not ui.due('cm_touch_due') and hold.dialog not in ui.stack


def test_a_tap_opens_nothing(hold, recs, ui, fw):
    hold.songs_page('album_page', [song(recs, i) for i in range(3)], sel=0, cls=0xff10)
    ui.pressed = 1
    finger(fw, POINTER_DOWN, 100, 120)
    finger(fw, POINTER_UP, 100, 120)
    assert not ui.due('cm_touch_due')


def test_a_finger_on_an_album_row_keeps_the_selection(hold, recs, ui, fw):
    albums = [recs(album=a, artist='The Beatles', kind=4, id_=i + 1) for i, a in enumerate(('Help!', 'Revolver'))]
    hold.songs_page('localclass_page', albums, sel=0, cls=0xf003)
    to_local(ui)
    ui.pressed = 1
    finger(fw, POINTER_DOWN, 100, 30 + 72 + 20)
    ui.due('cm_touch_due')
    ui.due('qm_open')
    assert hold.dialog not in ui.stack and ui.prop(hold.list, '_ringnav_index', -1) == 0


# Up Next's queue.
@pytest.fixture
def plain(fw, mcl, low):
    queue = Queue(fw, mcl, low, shuffle=False)
    fw.mock('window_manager_get_top_window', lambda *_: PAGE)
    return queue, Page(fw)


def keyrec(fw, key=KEY_CENTER):
    """Stock's input status with key held, for the hold's key record (hold_menu.c key_record, play_key)."""
    keys = C.create_string_buffer(O['INPUT_KEYS'] + 16 * O['INPUT_KEY_SIZE'])
    struct.pack_into('<I', keys, O['INPUT_KEYS'], key)
    fw.mock('window_manager_get_input_device_status', lambda *_: C.addressof(keys))
    return keys


def library_records(queue, recs):
    """The queue's rows as the Library's records once Up Next has listed them: the hold menu hashes a record's
    fields whole, which Low's 4-byte cells (test_upnext) don't give here."""
    rows = queue.deques[queue.handle]
    rows[:] = [song(recs, k) for k in range(len(rows))]


def test_centre_held_on_a_song_in_up_next_opens_its_menu(fw, plain, st, recs):
    queue, page = plain
    page.open()
    library_records(queue, recs)
    page.select(2)  # the third song after the playing one (queue index 2): index 5
    keys = keyrec(fw)
    assert fw.fn('qm_centre_hold', C.c_int, C.c_void_p)(PAGE) == 1
    assert st('qm_idx').value == 5 and [ms for _, ms in page.timers][-1] == 0  # its menu on the way (qm_open)
    del keys


def test_centre_held_on_playing_next_keeps_its_lift_and_a_finger_does_not(fw, plain, st, recs):
    """Centre held on a Playing Next song is its lift (un_keydown, UN_HOLD_MS); un_menu_hold gives the menu there
    only to a hold that is not Centre's (a finger's, qm_touch_hold)."""
    queue, page = plain
    queue.queue(5, 2)
    page.open()
    library_records(queue, recs)
    page.select(1)  # Playing Next's song
    keys = keyrec(fw)
    assert fw.fn('qm_centre_hold', C.c_int, C.c_void_p)(PAGE) == 0
    page.select(2)  # the queue's own next song
    assert fw.fn('qm_centre_hold', C.c_int, C.c_void_p)(PAGE) == 1
    st('qm_timer').value = 0
    keys = keyrec(fw, KEY_PLAY)
    page.select(1)
    assert fw.fn('un_menu_hold', C.c_int, C.c_void_p, C.c_int)(PAGE, 0) == 1
    del keys
