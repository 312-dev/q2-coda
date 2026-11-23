"""np_stream.c: Now Playing's spinner and amount while a server song loads, centred in the times row
(docs/server-library.md#download-ahead-and-the-streaming-state, streaming-v3's variant D)."""
import ctypes as C
import struct

import pytest

from conftest import O, Deques
from test_addon import World, songs  # noqa: F401 (songs: a fixture)
from test_coverflow import scratch  # noqa: F401 (a fixture)
from test_nav_painters import theme

CANVAS_LCD = O['CANVAS_LCD']
SONG = b'/mnt/mmc/.subsonic/stream/acct/q/a/Song [s1].flac'
STREAM = b'/tmp/q2-subsonic.stream'
NP_WIN_Y = 58  # Now Playing's window, under OLED's status row


def status(state, have, total=20_000_000):
    return b'#Q2STREAM/1\tseq=1\n%s\t%d\t%d\t780000\t%s\n' % (state, have, total, SONG)


class Np:
    """Now Playing (OLED) with SONG the queue's song at MCL_POS, .stream and q2player.wait as given, the timers, the
    spinner's frames and the text drawn."""
    def __init__(self, fw, mcl, songs, scratch, stream=None, waits=True):
        self.fw = fw
        theme(fw)
        fw.var('g_backlight_status', C.c_ubyte).value = 1  # lit: ns_tick ends while dark
        self.world = World(fw, now=1000)
        if stream is not None:
            self.world.files[STREAM] = stream
            self.world.mtime[STREAM] = 1000
        if waits:
            self.world.files[b'/tmp/q2player.wait'] = SONG + b'\n'
        self.deques = deques = Deques(fw)
        self.q = q = deques.new()
        deques[q].append(songs(SONG))
        fw.var('fw_ctx_mcl_pdeqplaylist', C.c_uint).value = q
        mcl.pos = 0
        # the view as the device has it: at y 58 on the page (its paint's coordinates are its own)
        self.winbuf = C.create_string_buffer(0x400)
        struct.pack_into('<i', self.winbuf, O['W_Y'], NP_WIN_Y)
        self.win = C.addressof(self.winbuf)
        C.c_void_p.from_address(C.addressof(fw.var('st', C.c_char)) + scratch['np_win']).value = self.win
        fw.mock('window_manager_get_top_window', lambda *_: self.win)
        self.timers, self.loads, self.icons, self.texts = [], [], [], []
        fw.mock('timer_add', lambda fn, ctx, ms, *_: self.timers.append((fn, ms)) or len(self.timers))
        fw.mock('widget_load_image', lambda w, name, bitmap, *_: self.loads.append(C.string_at(name).decode()) or 0)
        fw.mock('canvas_draw_icon', lambda c, bitmap, x, y, *_: self.icons.append((C.c_int(x).value, C.c_int(y).value)))
        fw.mock('canvas_measure_text', lambda c, s, n, *_: float(n * 7))
        fw.mock('canvas_draw_text_in_rect', lambda c, s, n, box, *_: self.texts.append(
            (''.join(map(chr, (C.c_uint * n).from_address(s))), struct.unpack_from('<iiii', C.string_at(box, 16)))) or 0)
        self.lcd, self.canvas = C.create_string_buffer(0x200), C.create_string_buffer(0x100)
        struct.pack_into('<Q', self.canvas, CANVAS_LCD, C.addressof(self.lcd))

    def paint(self):
        self.fw.fn('np_stream_paint', None, C.c_void_p, C.c_void_p)(self.win, C.addressof(self.canvas))

    def tick(self):
        """The newest timer fires."""
        fn, _ = self.timers[-1]
        self.world.ms += 500
        C.CFUNCTYPE(C.c_int, C.c_void_p)(fn)(None)


def test_a_song_the_player_waits_for_shows_the_spinner_and_the_amount(fw, mcl, songs, scratch):
    np = Np(fw, mcl, songs, scratch, status(b'get', 7_040_000))
    np.paint()
    assert np.texts == [] and len(np.timers) == 1  # the first paint starts the check
    np.tick()
    np.paint()
    assert np.texts and np.texts[-1][0] == '7.0 of 20 MB'
    # the times row's centre, y 250 on the screen: the device, 2026-11-17, drew it 58 px low, half off the panel
    assert np.loads[-1] == 'oled_npspin_d_1' and np.icons[-1][1] + NP_WIN_Y == 250
    assert np.texts[-1][1][1] + np.texts[-1][1][3] // 2 + NP_WIN_Y in (249, 250)
    # spinner and amount centred together on the screen's middle
    x, _, w, _ = np.texts[-1][1]
    left = np.icons[-1][0] - 7
    assert abs((left + (x + w - 1)) / 2 - 375 / 2) <= 2
    assert np.timers[-1][1] == 83  # a spoke a frame while it loads


def test_nothing_while_the_song_plays_on(fw, mcl, songs, scratch):
    np = Np(fw, mcl, songs, scratch, status(b'get', 7_040_000), waits=False)
    np.paint()
    np.tick()
    np.paint()
    assert np.texts == [] and np.timers[-1][1] == 500


def test_nothing_for_a_song_whose_line_is_done(fw, mcl, songs, scratch):
    np = Np(fw, mcl, songs, scratch, status(b'done', 20_000_000))
    np.paint()
    np.tick()
    np.paint()
    assert np.texts == []


def test_it_goes_once_the_song_plays_again(fw, mcl, songs, scratch):
    np = Np(fw, mcl, songs, scratch, status(b'stall', 9_600_000))
    np.paint()
    np.tick()
    np.paint()
    assert np.texts[-1][0] == '9.6 of 20 MB'
    del np.world.files[b'/tmp/q2player.wait']
    for _ in range(6):
        np.tick()
    n = len(np.texts)
    np.paint()
    assert len(np.texts) == n and np.timers[-1][1] == 500


def test_a_small_total_shows_tenths_and_an_unknown_one_none(fw, mcl, songs, scratch):
    np = Np(fw, mcl, songs, scratch, status(b'get', 1_200_000, 4_400_000))
    np.paint()
    np.tick()
    np.paint()
    assert np.texts[-1][0] == '1.2 of 4.4 MB'
    np.world.files[STREAM] = status(b'get', 1_200_000, 0)
    for _ in range(6):
        np.tick()
    np.paint()
    assert np.texts[-1][0] == '1.2 MB'


W_X, W_Y, W_W, W_H, W_PARENT = O['W_X'], O['W_Y'], O['W_W'], O['W_H'], O['W_PARENT']


def art_page(np, scratch):
    """Now Playing's art (24, 0, 150 x 150) on its page, as tools/ipod.py np_grid lays them out."""
    page, art = C.create_string_buffer(0x200), C.create_string_buffer(0x200)
    for off, v in ((W_X, 24), (W_Y, 0), (W_W, 150), (W_H, 150)):
        struct.pack_into('<i', art, off, v)
    struct.pack_into('<Q', art, W_PARENT, C.addressof(page))
    np.keep = (page, art)
    C.c_void_p.from_address(C.addressof(np.fw.var('st', C.c_char)) + scratch['np_cover']).value = C.addressof(art)
    return C.addressof(page)


def badge(np, page):
    np.paint()
    np.tick()
    np.texts.clear(), np.loads.clear(), np.icons.clear()
    np.fw.fn('np_stream_paint', None, C.c_void_p, C.c_void_p)(page, C.addressof(np.canvas))
    return [t for t, _ in np.texts], np.loads, np.icons


def test_a_server_song_shows_its_format_alone_on_the_art_page(fw, mcl, songs, scratch):
    """The format badge is its text in a ring, no glyph (the user's, 2026-11-19: "remove cloud icon from FLAC"): the
    label NB_PAD px in from the badge's left, the ring that far past its end."""
    np = Np(fw, mcl, songs, scratch, status(b'done', 20_000_000), waits=False)
    page = art_page(np, scratch)
    texts, loads, icons = badge(np, page)
    assert texts == ['FLAC'] and loads == [] and icons == []
    x, _, _, _ = np.texts[0][1]
    assert x == 192 + 9  # the label 9 px in


def test_the_badge_is_the_same_while_the_server_cant_be_reached(fw, mcl, songs, scratch):
    """No crossed-out cloud either: a server out of reach is the status row's (offline glyph) and, for a song that
    can't go on, the times row's message."""
    np = Np(fw, mcl, songs, scratch, status(b'done', 20_000_000), waits=False)
    np.world.files[b'/tmp/q2-subsonic.state'] = b'server=down\n'
    page = art_page(np, scratch)
    texts, loads, _ = badge(np, page)
    assert texts == ['FLAC'] and loads == []


def test_a_card_file_has_no_badge(fw, mcl, songs, scratch):
    np = Np(fw, mcl, songs, scratch, waits=False)
    np.deques[np.q][:] = [songs(b'/mnt/mmc/Music/a.flac')]
    page = art_page(np, scratch)
    texts, loads, _ = badge(np, page)
    assert texts == [] and loads == []


def stuck(np, state):
    """np with the server out of reach as state says, painted once its NS_TRYING_MS ran out."""
    np.world.files[b'/tmp/q2-subsonic.state'] = state
    np.paint()
    np.tick()
    np.texts.clear(), np.loads.clear()
    np.paint()
    trying = [t for t, _ in np.texts], [n for n in np.loads if 'spin' in n]
    for _ in range(20):  # 10 s
        np.tick()
    np.texts.clear(), np.loads.clear()
    np.paint()
    return trying


def test_a_song_stuck_with_its_server_down_offers_retry_and_skip(fw, mcl, songs, scratch):
    np = Np(fw, mcl, songs, scratch, status(b'get', 7_040_000))
    error = fw.fn('np_stream_error', C.c_int)
    texts, spins = stuck(np, b'server=down\n')
    # while the app tries again: why, beside the spinner, and no Retry or Skip yet (the user's, 2026-11-19)
    assert texts == ["Can't reach the server \u00b7 trying again"] and spins
    assert error() == 1
    assert [t for t, _ in np.texts] == ["Can't reach the server"] and np.loads == []  # no spinner, no amount
    fw.fn('np_stream_cta_click', None, C.c_int)(100)  # left of the middle: Retry
    assert np.world.files[b'/tmp/q2-subsonic.retry'] == b'1\n'
    del np.world.files[b'/tmp/q2-subsonic.state']  # the server answers again
    for _ in range(6):
        np.tick()
    assert error() == 0


@pytest.mark.parametrize('state,why', [(b'reach=down 502\n', 'Server error 502'),
                                       (b'reach=down none\n', 'Server not answering'),
                                       (b'reach=down dns\n', 'Server not found')])
def test_a_stuck_song_says_why_in_the_words_of_the_apps_reach(fw, mcl, songs, scratch, state, why):
    np = Np(fw, mcl, songs, scratch, status(b'get', 7_040_000))
    texts, _ = stuck(np, state)
    assert texts == [why + ' \u00b7 trying again']
    assert [t for t, _ in np.texts] == [why]
    assert fw.fn('np_stream_error', C.c_int)() == 1


def test_a_song_that_plays_on_is_not_stuck_whatever_the_server(fw, mcl, songs, scratch):
    np = Np(fw, mcl, songs, scratch, status(b'done', 20_000_000), waits=False)
    np.world.files[b'/tmp/q2-subsonic.state'] = b'server=down\n'
    np.paint()
    np.tick()
    assert fw.fn('np_stream_error', C.c_int)() == 0


def test_dark_now_playing_s_stream_tick_stops_until_it_is_lit(fw, mcl, songs, scratch):
    """ns_tick, the one page timer with no backlight test: with Now Playing on top and the screen dark it read
    q2player.wait and the song's .stream every 500 ms. Dark, it ends (the next paint, lit, starts it again);
    the files it read in a dark minute are counted."""
    np = Np(fw, mcl, songs, scratch, stream=status(b'loading', 100))
    np.paint()
    assert np.timers
    opened = []
    files = np.world
    fw.mock('fw_fopen', lambda path, mode, *_: opened.append(C.string_at(path)) or files.fopen(path, mode))
    fw.var('g_backlight_status', C.c_ubyte).value = 0
    for _ in range(120):
        if not np.timers:
            break
        fn, _ = np.timers.pop()
        np.world.ms += 500
        C.CFUNCTYPE(C.c_int, C.c_void_p)(fn)(None)
    print(f'ns_tick, a dark minute: {len(opened)} file opens')
    assert len(opened) <= 2 and not np.timers
    fw.var('g_backlight_status', C.c_ubyte).value = 1
    np.paint()
    assert np.timers  # lit again: the next paint starts it
