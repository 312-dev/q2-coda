"""centre_menu.c: Centre or a finger held on a song's row opens the song's hold menu (docs/internals.md#centre-hold-menu),
half way to stock's Shut Down? hold, through input.c's key and pointer hooks and the hold menu's own code
(hold_menu.c qm_centre_hold, qm_touch_hold): an album's tracks, Songs, Up Next's queue; rows that are no song, a
double press, a dark screen, a rim chord, a drag and Edit Library's hold keep what they did."""
import ctypes as C
import struct

import pytest

from conftest import O, claim, ui_name
from test_nav_hold import (Hold, Ui, cstr, directory, field, recs, scratch, song, st, use_set)  # noqa: F401
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


class Held(C.Structure):
    """hold_menu.c's qm_held: a payload page's song list held (qm_song_hold), its last row's words and what it runs."""
    _fields_ = [('page', C.c_void_p), ('list', C.c_void_p), ('extra', C.c_char_p), ('pick', C.c_void_p), ('play', C.c_void_p),
                ('move', C.c_void_p)]


def test_up_next_s_menu_ends_with_remove_from_queue_which_takes_that_song_out(fw, plain, st, recs):
    queue, page = plain
    page.open()
    library_records(queue, recs)
    rows = [C.string_at(r, O['REC_SIZE']) for r in queue.deques[queue.handle]]
    page.select(2)  # queue index 5
    keys = keyrec(fw)
    assert fw.fn('qm_centre_hold', C.c_int, C.c_void_p)(PAGE) == 1
    held = fw.var('navigation__qm_held', Held)
    assert held.extra == b'Remove from Queue' and st('qm_idx').value == 5
    C.CFUNCTYPE(None)(held.pick)()  # the row picked, once qm_run has checked the record (upnext.c un_remove)
    left = [C.string_at(r, O['REC_SIZE']) for r in queue.deques[queue.handle]]
    assert left == rows[:5] + rows[6:] and queue.mcl.pos == 2 and not page.toasts
    del keys


def test_centre_held_on_playing_next_opens_its_menu_before_its_lift(fw, plain, st, recs):
    """Centre held on a Playing Next song gets its menu at CENTRE_MENU_MS, as every queue row does, which cancels the lift un_keydown timed for UN_HOLD_MS; a finger gets it
    too."""
    queue, page = plain
    queue.queue(5, 2)
    page.open()
    library_records(queue, recs)
    page.select(1)  # Playing Next's song
    keys = keyrec(fw)
    page.key(KEY_CENTER, down=True)  # the lift timed
    assert fw.fn('qm_centre_hold', C.c_int, C.c_void_p)(PAGE) == 1
    st('qm_timer').value = 0
    page.select(2)  # the queue's own next song
    assert fw.fn('qm_centre_hold', C.c_int, C.c_void_p)(PAGE) == 1
    st('qm_timer').value = 0
    keys = keyrec(fw, KEY_PLAY)
    page.select(1)
    assert fw.fn('un_menu_hold', C.c_int, C.c_void_p, C.c_int)(PAGE, 0) == 1
    del keys


# A card app's own page (addon.c addon_row_hold, docs/internals.md#card-apps): a row whose hold op is "hold" (an app that
# sees "holdmenu" in the caps, q2-subsonic's song rows) gets the menu the app answers that op with.
def app_song(i, hold=b'hold'):
    return (b'action', b'play', b'Song %d' % i, b'Settle \xc2\xb7 4:1%d' % i, b'song:s%d top:ar1 %d album:al1' % (i, i), hold)


APP_MENU = [(b'action', b'next', b'Play Next', b'', b'song:s1 top:ar1 1 album:al1', b''),
            (b'action', b'last', b'Play Last', b'', b'song:s1 top:ar1 1 album:al1', b''),
            (b'action', b'toggle', b'Sync Offline', b'', b'song:s1', b'')]


class Head(C.Structure):
    _fields_ = [('title', C.c_char * 96), ('sub', C.c_char * 96), ('url', C.c_char * 160), ('bars', C.c_int)]


class Modal(C.Structure):
    """menu_modal.c's modals[]: an open sheet, its header kept."""
    _fields_ = [*((n, C.c_void_p) for n in ('win', 'list', 'view')), *((n, C.c_int) for n in ('top', 'high', 'ly', 'off', 'head_y')),
                ('head', Head)]


def sheet_head(fw):
    """The open sheet's header, its title and subtitle."""
    open_ = [m for m in fw.var('menu_modal__modals', Modal * 2) if m.win]
    return (open_[0].head.title, open_[0].head.sub) if open_ else None


class AppPage:
    """An app's Top Songs (LAY_TOP) open in the Ui world, the worker never run: answer() puts the reply where its thread
    would and the page's tick takes it, as test_addon.py's Page does."""
    def __init__(self, fw, ui, hold, rows):
        from test_addon import LAY_TOP, Ap, Pg, Req
        self.fw, self.ui, self.hold = fw, ui, hold
        ap = fw.var('addon__ap', Ap)
        ap.n, a = 1, ap.a[0]
        a.id, a.name, a.bin, a.pid = b'foo', b'Foo', b'/mnt/mmc/.foo/q2-foo', b'/tmp/q2-foo'
        a.state, a.browse = b'/tmp/q2-foo.state', b'/tmp/q2-foo.browse.sock'
        a.present = a.launched = a.queue_seen = 1
        hold.card.files.update({'/mnt/mmc/.foo/q2-foo': b'', '/tmp/q2-foo': b'1'})
        fw.mock('fw_pthread_create', lambda *_: 0)
        def screen(*_):  # a window is the screen's size, as stock makes it: the page's list lays out under its title
            w = ui.window('')
            ui.place(w, 0, 0, 375, 320)
            return w
        fw.mock('window_create', ui.guard(screen))
        self.pg, self.req = fw.var('addon__pg', Pg), fw.var('addon__req', Req)
        at = fw.fn('addon__page_at', C.c_int, C.c_int, C.c_char_p, C.c_char_p, C.c_char_p, C.c_int)
        assert at(0, b'top', b'\tartist:ar1', b'Top Songs', 0) == 1
        self.pg.lv[0].lay = LAY_TOP
        self.rows = rows
        self.answer(rows)
        self.page, self.view = self.pg.page, self.pg.view
        C.c_ubyte.from_address(self.view + O['VIEW_VERTICAL']).value = 1  # stock's scroll_view in a list_view scrolls so
        assert self.ui.top() == self.page and self.pg.lv[0].n == len(rows)

    def answer(self, rows=(), msg=b'', status=b'ok'):
        assert self.req.buf, 'no request in flight to answer'
        data = b'Q2SPOT/1\n%d\t%s\t%s\t\n' % (self.req.id, status, msg) + b''.join(b'\t'.join(r) + b'\n' for r in rows)
        C.memmove(self.req.buf, data + b'\0', len(data) + 1)
        self.req.n, self.req.fail, self.req.done = len(data), 0, 1
        self.fw.fn('addon__tick', C.c_int, C.c_void_p)(None)

    def request(self):
        return C.string_at(self.req.buf)[:self.req.len] if self.req.buf else b''

    def select(self, i):
        self.fw.fn('ringnav_select', None, C.c_void_p, C.c_int, C.c_int)(self.view, i, self.pg.lv[0].n)

    def menu(self):
        view = self.hold.view() if self.hold.dialog in self.ui.stack else 0
        return [(self.ui.label(k), self.ui.nodes[k].props.get('_mm_icon')) for k in self.ui.items(view)] if view else None


@pytest.fixture
def app(fw, ui, hold):
    return AppPage(fw, ui, hold, [app_song(i) for i in range(3)] + [(b'info', b'', b'3 songs', b'', b'', b'')])


def test_centre_held_on_an_apps_song_asks_the_app_for_its_menu_and_shows_it(app, centre, ui):
    app.select(1)
    centre.press()
    assert armed(ui) == [HOLD_MS]
    centre.wait()
    assert app.request().endswith(b'\thold\tsong:s1 top:ar1 1 album:al1\n')
    assert not centre.shutdown_pending()  # this press never reaches Shut Down?
    assert centre.release() == STOP  # nor plays the song
    app.answer(APP_MENU)
    assert app.menu() == [('Play Next', 'arrow-bend-down-right'), ('Play Last', 'queue'), ('Sync Offline', 'cloud-arrow-down')]
    assert sheet_head(app.fw) == (b'Song 1', b'Settle')  # the row's title, its subtitle's first part


def test_a_pick_from_an_apps_menu_sends_that_rows_op(app, centre, ui):
    app.select(1)
    centre.press()
    centre.wait()
    app.answer(APP_MENU)
    ui.click(ui.find(app.hold.view(), 'Play Last'))
    ui.due('menu_modal__pick_run')
    assert app.request().endswith(b'\tlast\tsong:s1 top:ar1 1 album:al1\n')


def test_an_apps_row_with_another_hold_op_keeps_stock_s_centre_hold(fw, ui, hold, centre):
    page = AppPage(fw, ui, hold, [app_song(i, b'toggle') for i in range(3)])
    page.select(1)
    sent = page.req.id
    centre.press()
    centre.wait()
    assert page.req.id == sent and centre.shutdown_pending()  # nothing asked; Shut Down? still at 1000 ms


def test_a_finger_held_on_an_apps_song_selects_it_and_its_lift_clicks_nothing(app, ui, fw):
    to_local(ui)
    aborts = []
    fw.mock('widget_dispatch_event_to_target_recursive', lambda w, e, *_: aborts.append(C.c_int.from_address(e + EVENT_TYPE).value) or 0)
    app.select(0)
    x, y, w = 100, 10, ui.items(app.view)[2]
    while w in ui.nodes:  # a point in the third row, on the screen
        dx, dy = ui.nodes[w].rect()[:2]
        x, y, w = x + dx, y + dy, ui.nodes[w].parent
    ui.pressed = 1
    finger(fw, POINTER_DOWN, x, y)
    ui.due('cm_touch_due')
    assert ui.prop(app.view, '_ringnav_index', -1) == 2
    assert app.request().endswith(b'\thold\tsong:s2 top:ar1 2 album:al1\n')
    assert aborts == [EVT_POINTER_ABORT]  # the row's press let go: the lift plays nothing


def test_play_next_and_play_last_from_an_apps_menu_land_where_the_hold_menus_do(fw, ui, hold, centre, recs):
    """The app fetches the song, names it in its .queue and answers; the firmware queues it at once as the Library's
    hold menu does (library_pages.c queue_add): Play Next after the song playing, Play Last at the end of what plays
    next, each toasted with its words."""
    songs = [song(recs, i) for i in range(3)]
    hold.now_playing(songs, at=0, held=True)
    pool, cells = C.c_uint64.from_address(O['MCL_POOL']).value & 0xffffffff, []
    def push(q, v, *_):  # stock's shuffle pool holds ints: deque_at gives an element's address
        if q & 0xffffffff == pool:
            cells.append(C.c_int(v))
            v = C.addressof(cells[-1])
        return hold.deques.push(q, v)
    fw.mock('fw__deque_push_back', push)
    app = AppPage(fw, ui, hold, [app_song(i) for i in range(3)])
    fetched = {}
    def listing(path, *_):
        folder = cstr(path)
        rows, q = [r for p, r in fetched.items() if p.rsplit('/', 1)[0] == folder], directory(fw)
        hold.deques.free(q)
        for r in rows:  # stock's listing: its own copy of each record
            hold.deques.push(q, r)
        return len(rows)
    fw.mock('toolsLoadDirectory', listing)
    queue_file = ''
    for i, (row, op, said) in enumerate((('Play Next', 'next', 'Playing Next'), ('Play Last', 'last', 'Playing Last'))):
        app.select(i)
        centre.press()
        centre.wait()
        centre.release()
        app.answer(APP_MENU)
        ui.click(ui.find(hold.view(), row))
        ui.due('menu_modal__pick_run')
        assert app.request().endswith(b'\t%s\tsong:s1 top:ar1 1 album:al1\n' % op.encode())
        path = f'/mnt/mmc/.foo/stream/a/song {i} [s{i}].flac'
        fetched[path] = recs(path=path, title=f'Fetched {i}', album='Settle', artist='Disclosure')
        queue_file += f'{7 + i} {op} {path}\n'
        hold.card.files['/tmp/q2-foo.queue'] = queue_file.encode()
        app.answer()  # its reply says nothing: the song is queued and toasted at once
        assert ui.toasts[-1] == said
        app.answer(app.rows)  # the level asked again after the action, as after any
    queued = [field(r, O['REC_PATH']) for r in hold.deques[hold.queue]]
    assert queued == ['/mnt/mmc/Music/0.flac', '/mnt/mmc/.foo/stream/a/song 0 [s0].flac',
                      '/mnt/mmc/.foo/stream/a/song 1 [s1].flac', '/mnt/mmc/Music/1.flac', '/mnt/mmc/Music/2.flac']
