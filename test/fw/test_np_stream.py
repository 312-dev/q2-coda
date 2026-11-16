"""np_stream.c: Now Playing's spinner and amount while a server song loads, centred in the times row
(docs/server-library.md#download-ahead-and-the-streaming-state, streaming-v3's variant D)."""
import ctypes as C
import struct

from conftest import O, Deques
from test_addon import World, songs  # noqa: F401 (songs: a fixture)
from test_coverflow import scratch  # noqa: F401 (a fixture)
from test_nav_painters import theme

CANVAS_LCD = O['CANVAS_LCD']
SONG = b'/mnt/mmc/.subsonic/stream/acct/q/a/Song [s1].flac'
STREAM = b'/tmp/q2-subsonic.stream'


def status(state, have, total=20_000_000):
    return b'#Q2STREAM/1\tseq=1\n%s\t%d\t%d\t780000\t%s\n' % (state, have, total, SONG)


class Np:
    """Now Playing (OLED) with SONG the queue's song at MCL_POS, .stream and q2player.wait as given, the timers, the
    spinner's frames and the text drawn."""
    def __init__(self, fw, mcl, songs, scratch, stream=None, waits=True):
        self.fw = fw
        theme(fw)
        C.c_int.from_address(C.addressof(fw.var('st', C.c_char)) + scratch['geom']).value = \
            fw.fn('theme_geom', C.c_int, C.c_void_p)(fw.fn('theme', C.c_void_p)())  # OLED's set: its status row
        self.world = World(fw, now=1000)
        if stream is not None:
            self.world.files[STREAM] = stream
            self.world.mtime[STREAM] = 1000
        if waits:
            self.world.files[b'/tmp/q2player.wait'] = SONG + b'\n'
        deques = Deques(fw)
        q = deques.new()
        deques[q].append(songs(SONG))
        fw.var('fw_ctx_mcl_pdeqplaylist', C.c_uint).value = q
        mcl.pos = 0
        self.win = 0x5000
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
    assert np.loads[-1] == 'oled_npspin_d_1' and np.icons[-1][1] == 250
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
