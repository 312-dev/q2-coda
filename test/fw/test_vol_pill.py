"""patch/vol_pill.c: the status row's volume pill, in the clock's place, for a volume change made off Now Playing
(a headset's own keys or AVRCP volume, the Car Remote): shown, updated, faded back to the clock, never on Now
Playing, the mute, a linked headset's level on the Q2's scale, and the box it repaints."""
import ctypes as C

import pytest

from conftest import mcl  # noqa: F401 (fixtures)
from test_nav_hold import recs  # noqa: F401
from test_nav_library_pages import ui  # noqa: F401
from test_nav_painters import Canvas, scratch, st  # noqa: F401
from test_status_row import Row

# Row's bar: the battery's body at x 322, the level bars (playing) 6 + 10 px left of it, so the leftmost slot's
# edge is 306. The pill takes the clock's place at the row's left: from x 50 (VP_X, its speaker at the clock's text,
# STATUS_ROW_INSET 62, less VP_PAD), 199 wide (VP_W), so it ends short of 298, VP_CLEAR (8) before the slots.
BOX = (50, 23 - 15, 199, 30)
HOLD, FADE = 2000, 250
RAISED, TRACK, PRIMARY, SECONDARY = 0x1c1c1e, 0x333333, 0xffffff, 0xababab


def rgba(rgb):
    """theme.h RGBA: 0xRRGGBB as AWTK's colour word, opaque."""
    return 0xff000000 | (rgb & 255) << 16 | (rgb & 0xff00) | rgb >> 16


class Pill(Row):
    """Row (Home, playing) with the clock in hand, the labels' opacity recorded and the payload's vol_show and
    paint in reach."""
    def __init__(self, fw, st, page='home_page'):
        super().__init__(fw, st)
        self.now = 100000
        fw.mock('time_now_ms', lambda *_: self.now)
        self.opacity: dict = {}
        fw.mock('widget_set_opacity', lambda w, o, *_: self.opacity.__setitem__(self.ui.nodes[w].name, o))
        self.opened: list = []
        fw.mock('navigator_to', lambda path, *_: self.opened.append(C.string_at(path)) or 0)
        self.alphas: list = []
        fw.mock('canvas_set_global_alpha', lambda c, a, *_: self.alphas.append(a & 255))
        if page != 'home_page':
            self.ui.window(page)
        self.volume(62)

    def volume(self, v):
        """The volume moved to v by a headset (bt_volume_poll), shown with vol_show."""
        g = self.fw.var('g_volume', C.c_ubyte)
        was, g.value = g.value, v
        self.fw.fn('vol_show', None, C.c_int)(was)

    def up(self):
        return self.fw.fn('vol_pill_up', C.c_int)()

    def pill(self):
        """A paint of the row: (the pill's box and ink, or None; the track and its fill; the texts; the art)."""
        canvas = Canvas(self.fw)
        names: list = []
        load = canvas.load_image
        self.fw.mock('widget_load_image', lambda w, n, b, *_: names.append(C.string_at(n).decode()) or load(w, n, b))
        canvas.art = {f'oled_status_{n}_d': (14, 14) for n in ('vol', 'mute')}
        self.fw.fn('vol_pill_paint', None, C.c_void_p, C.c_void_p)(self.bar.addr, canvas.addr)
        pill = next(((b, ink) for b, ink, r in canvas.boxes if b == BOX), None)
        return pill, [(b, ink) for b, ink, _ in canvas.boxes if b != BOX], canvas.texts, names

    def fire(self):
        """The newest timer's callback, run as the timer would."""
        fn, _ = self.timers[-1]
        return C.CFUNCTYPE(C.c_int, C.c_void_p)(fn)(None)

    def frame(self):
        return self.fw.fn('vol_pill__vp_frame', C.c_int)()


@pytest.fixture
def pill(fw, st):
    return Pill(fw, st)


def test_a_headsets_change_off_now_playing_shows_the_pill_in_the_clocks_place(fw, pill):
    """A Raised pill 30 px tall on the row's centre, from the clock's place at the row's left, the speaker, a 4 px
    track in Track filled in Text Primary to 62 of 100, and "62" in Text Secondary ending 12 px in from its right end;
    the clock out (opacity 0), and stock's volume dialog not opened."""
    assert pill.up() and pill.opened == []
    box, rest, texts, names = pill.pill()
    assert box == (BOX, rgba(RAISED))
    x, y, w, h = BOX
    track_x, num = x + 12 + 14 + 8, x + w - 12
    track_w = num - 32 - 8 - track_x
    assert rest == [((track_x, 23 - 2, track_w, 4), rgba(TRACK)), ((track_x, 21, track_w * 62 // 100, 4), rgba(PRIMARY))]
    assert [(t, r[0] + r[2] - 1, c) for t, r, c in texts] == [('62', num, rgba(SECONDARY))]  # right-aligned at num
    assert names == ['oled_status_vol_d']
    assert pill.opacity == {'label_clock': 0}


def test_it_follows_each_change_and_repaints_only_its_box(fw, pill):
    """A change while it shows repaints the pill's box alone, never the page or the whole row (the lesson of
    perf/cc-volume-drag); the first one repaints the band where the clock stands too, as it goes out."""
    assert pill.dirty == [(0, BOX[1], 306, BOX[3])]  # the first: from the left to the slots, in the pill's rows
    pill.dirty.clear()
    for v in (63, 64, 70):
        pill.volume(v)
    assert pill.dirty == [BOX] * 3
    _, rest, texts, _ = pill.pill()
    assert texts[0][0] == '70' and rest[1][0][2] == rest[0][0][2] * 70 // 100


def test_it_holds_two_seconds_after_the_last_change_then_fades_back_to_the_clock(fw, pill):
    assert [ms for _, ms in pill.timers][-1] == HOLD
    pill.now += 1500
    pill.volume(50)  # the hold starts again from here
    pill.now += 500
    pill.fire()
    assert pill.up() and [ms for _, ms in pill.timers][-1] == 1500  # put off: 1500 ms still to hold
    pill.now += 1500
    pill.fire()
    alphas = []
    while pill.frame():
        alphas.append(pill.opacity['label_clock'])
        pill.now += 33
    assert alphas[0] == 0 and alphas == sorted(alphas) and len(alphas) >= FADE // 33 - 1  # the clock fades back in
    assert not pill.up() and pill.opacity == {'label_clock': 255}
    assert pill.pill()[0] is None  # gone


def test_reduce_motion_puts_the_clock_back_at_once(fw, st, pill):
    st('reduce_motion').value = 1
    pill.now += HOLD
    pill.fire()
    assert not pill.up() and pill.opacity == {'label_clock': 255}
    assert not pill.frame()


@pytest.mark.parametrize('slots, box', [
    ('lock', BOX),  # bars, Wi-Fi and the padlock, 57 px: room for all of it
    ('sync', (50, 8, 187, 30)),  # and a download, 77 px: narrower, ending VP_CLEAR short of the leftmost
    ('crowd', (37, 8, 141, 30)),  # every slot, 136 px: VP_MIN_W, ending there, so it leaves the clock's place
])
def test_it_narrows_from_its_right_end_as_the_slots_reach_left(fw, pill, slots, box):
    """The pill keeps the clock's place and ends VP_CLEAR short of the leftmost slot, VP_W at most; at VP_MIN_W (141)
    it moves left instead."""
    pill.wifi(3)
    fw.var('g_keylock_flag', C.c_ubyte).value = 1
    if slots != 'lock':
        pill.app(2, 500)
    if slots == 'crowd':
        pill.ap.a[0].reach = 1
        from test_status_row import bt_link
        bt_link(fw, status=3, level=70)
    pill.dirty.clear()
    pill.volume(40)  # up already: its box alone repaints
    assert pill.dirty == [box]


@pytest.mark.parametrize('page', ['playing_page', 'radionp_page', 'airplay_page'])
def test_on_now_playing_stocks_volume_band_opens_and_no_pill(fw, st, page):
    """Now Playing has its own volume (the band over its progress bar): the pill never shows there."""
    pill = Pill(fw, st, page)
    assert not pill.up() and pill.opened == [b'dialog/volume_dialog']
    assert pill.pill()[0] is None


def test_a_pill_up_when_now_playing_opens_goes_at_once(fw, pill):
    pill.ui.window('playing_page')
    assert pill.pill()[0] is None and not pill.up() and pill.opacity['label_clock'] == 255


def test_control_centers_slider_follows_and_no_pill(fw, st):
    pill = Pill(fw, st, 'statusbar_dialog')
    assert not pill.up() and pill.opened == []


def test_the_mute_is_the_muted_speaker_and_an_empty_track(fw, pill):
    pill.volume(0)
    box, rest, texts, names = pill.pill()
    assert box and names == ['oled_status_mute_d'] and len(rest) == 1 and rest[0][1] == rgba(TRACK)  # no fill
    assert texts[0][0] == '0'


def test_with_link_volume_it_shows_the_headsets_level_on_the_q2s_scale(fw, pill):
    """Link Volume on a headset that took its absolute volume: its own keys take it to 80 of AVRCP's 127, which the
    pill shows as the Q2's 0 to 100 reads it everywhere else (bt_vol_q2: 63), never as 80."""
    from test_nav_loop import VolPoll, reads_as
    vol = VolPoll(fw, volume=50)
    vol.headset.level = 80
    vol.poll()
    want = reads_as(80)
    assert want != 80 and fw.var('g_volume', C.c_ubyte).value == want
    _, rest, texts, _ = pill.pill()
    assert texts[0][0] == str(want) and rest[1][0][2] == rest[0][0][2] * want // 100


def test_the_car_remotes_volume_shows_the_pill(fw, ui, mcl, recs):
    """The Car Remote's `volume N` (coda_rc_shim.c), set from the car, shows on the Q2's lit screen as a headset's."""
    from test_coda_rc_shim import CMD, Q2
    q2 = Q2(fw, ui, recs)
    fw.var('g_backlight_status', C.c_ubyte).value = 1
    fw.var('g_maxvolume', C.c_ubyte).value = 100
    ui.name[ui.new('window_create', 0, 0, 0, 0, 0)] = 'home_page'
    q2.files[CMD] = b'5 volume 30\n'
    q2.tick()
    assert fw.var('g_volume', C.c_ubyte).value == 30 and fw.fn('vol_pill_up', C.c_int)()
