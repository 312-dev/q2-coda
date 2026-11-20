"""radio_tuner.c (docs/internals.md#radio-home): Home's Radio. The list from q2radiofetch read, the dial's keys
and what the receiver (q2video -t) is told for each: the needle's carrier, level, offset and fade, the stream started
only once the dial lands, kept warm when it leaves, stopped on pause; seeking past stations with no stream; the
stations list, the ZIP entry and the fetch they start; the StreamTitle as the tuner shows it."""
import ctypes as C

import pytest

from conftest import O, Files

KEY_NEXT, KEY_PREV, KEY_CENTER, KEY_RETURN, KEY_PLAY, KEY_FWD_BTN, KEY_BACK_BTN = (
    O[k] for k in ('KEY_NEXT', 'KEY_PREV', 'KEY_CENTER', 'KEY_RETURN', 'KEY_PLAY', 'KEY_FWD_BTN', 'KEY_BACK_BTN'))
LOADING, TUNER, LIST, ZIP, ERROR = range(5)
LIST_FILE, ZIP_FILE, SOCK = '/tmp/q2tuner.list', '/mnt/mmc/Radio/.local/zip', '/tmp/q2tuner.sock'
PAGE = 0x5150
RT_MAX = 64


class Station(C.Structure):
    _fields_ = [('freq', C.c_short), ('db', C.c_ubyte), ('km', C.c_float), ('call', C.c_char * 8),
                ('name', C.c_char * 48), ('city', C.c_char * 40), ('url', C.c_char * 300)]


class Rt(C.Structure):
    _fields_ = [('page', C.c_void_p), ('sheet', C.c_void_p), ('title', C.c_void_p),
                *((k, C.c_int) for k in 'screen n freq playing y0 sel zi feed'.split()), ('from_', C.c_float),
                *((k, C.c_uint) for k in 'glide_at fetch_at anim land warm polled shown_at leave ended'.split()),
                ('scroll', C.c_float), ('target', C.c_float), ('zip', C.c_int * 5),
                *((k, C.c_int) for k in 'audio sock volume touch tx tf moved no_wifi'.split()),
                ('place', C.c_char * 48), ('err', C.c_char * 64), ('title_text', C.c_char * 256), ('state', C.c_char * 12),
                ('zip_text', C.c_char * 8), ('st', Station * RT_MAX),
                *((k, C.c_int) for k in 'det_f det_left det_d'.split()), ('tick_at', C.c_uint),
                *((k, C.c_int) for k in 'travel shown raw'.split()),
                *((k, C.c_uint) for k in 'words_at locked_at marq'.split()), ('mq_at', C.c_uint * 2),
                ('mq_key', C.c_uint * 2)]


LIST_TEXT = ('#Q2RADIO 2 1795160000 41.8708 -87.6242 complete ZIP 60605\n'
        'WCRX\tFM\t88.1\t0.4\t80\tWCRX\tChicago, IL\tiheart=https://wcrx/live\tiheart=http://wcrx/live\n'
        'WPWX\tFM\t92.3\t27.9\t56\tPower 92\tHammond, IN\tiheart=https://power92\n'
        'WXRT\tFM\t93.1\t3.1\t80\t93XRT\tChicago, IL\tiheart=https://xrt\tradiobrowser=http://xrt2\n'
        'WCFS-FM\tFM\t105.9\t1.3\t80\t\tElmwood Park, IL\n'
        'WBBM\tAM\t780\t27.7\t80\tNewsradio\tChicago, IL\tiheart=https://wbbm\n')


class Tuner:
    """The page's state on a heap of the test's, the clock, the receiver's datagrams, timers and the fetch's fork."""
    def __init__(self, fw):
        self.fw, self.now, self.sent, self.timers, self.forks = fw, 100000, [], [], []
        self.rt = Rt()
        fw.var('radio_tuner__rt', C.c_void_p).value = C.addressof(self.rt)
        self.rt.page, self.rt.sheet, self.rt.playing, self.rt.feed, self.rt.audio = PAGE, 0x5151, 1, -1, 999
        fw.mock('time_now_ms', lambda *_: self.now)
        fw.mock('fw_sendto', self.sendto)
        fw.mock('fw_socket', lambda *_: 7)
        fw.mock('timer_add', self.timer_add)
        fw.mock('fw_fork', lambda *_: self.forks.append(1) or 4242)
        self.files = Files(fw, {LIST_FILE: LIST_TEXT.encode()})
        fw.mock('fw_access', lambda p, *_: 0 if C.string_at(p).decode() in self.files.files else -1)
        self.key_fn = fw.fn('tuner_key', C.c_int, C.c_void_p, C.c_uint)
        fw.fn('radio_tuner__list_read', C.c_int)()
        self.signal = 3  # stock's network monitor's connect status (0: no link)
        fw.mock('networkGetConnectStatus', lambda *_: self.signal)
        fw.mock('fw_waitpid', lambda *_: 0)  # the receiver and any fetch still running
        self.poll_fn = fw.fn('tuner_poll', None)

    def poll(self, ms=600):
        """tuner_poll after ms, the clock moved on."""
        self.now += ms
        self.sent.clear()
        self.poll_fn()

    def sendto(self, s, data, n, flags, to, *_):
        self.sent.append(C.string_at(data, n).decode())
        return n

    def timer_add(self, fn, ctx, ms, *_):
        self.timers.append((fn, ms))
        return len(self.timers)

    def key(self, k):
        self.sent.clear()
        return self.key_fn(PAGE, k)

    def params(self):
        """The last p datagram as (level, offset kHz, fade thousandths, gain, station)."""
        p = [m for m in self.sent if m.startswith('p')]
        return tuple(int(v) for v in p[-1][1:].split()) if p else None

    def fire(self, name):
        """Run the newest timer the page armed for radio_tuner.c's `name` (land, cool)."""
        fn = self.fw.fn(f'radio_tuner__{name}', C.c_int, C.c_void_p)
        self.sent.clear()
        return fn(None)

    def calls(self):
        return {self.rt.st[i].call.decode(): self.rt.st[i] for i in range(self.rt.n)}


@pytest.fixture
def tu(fw):
    t = Tuner(fw)
    yield t
    fw.var('radio_tuner__rt', C.c_void_p).value = None


def test_the_list_read(tu):
    rt = tu.rt
    assert rt.n == 4  # FM only: WBBM's 780 AM has no place on the dial
    s = tu.calls()
    assert [s[c].freq for c in s] == [8810, 9230, 9310, 10590]
    assert (s['WXRT'].name, s['WXRT'].city, s['WXRT'].db, round(s['WXRT'].km, 1)) == (b'93XRT', b'Chicago, IL', 80, 3.1)
    assert s['WXRT'].url == b'https://xrt'  # the first source's first stream
    assert s['WCFS-FM'].url == b'' and s['WCFS-FM'].name == b'WCFS-FM'  # no name, no stream: its call sign stands in
    assert rt.place == b'Near Chicago, IL'  # the nearest station's city (WCRX, 0.4 km)


def steps(tu, k, n, gap=100):
    """n wheel ticks gap ms apart; the clicks the receiver was told to make."""
    clicks = []
    for _ in range(n):
        tu.now += gap
        tu.key(k)
        clicks += [m for m in tu.sent if m.startswith('k')]
    return clicks


def test_fine_tuning_in_0_02_mhz_steps_and_0_06_when_fast(tu):
    """The approved tuner (~/q2-emu/design/radio-tuner/index.html fineTurn): a wheel step moves 0.02 MHz, 0.06 when
    the steps come under 35 ms apart, and every step clicks."""
    rt = tu.rt
    rt.screen, rt.freq = TUNER, 9990
    assert steps(tu, KEY_NEXT, 1) == ['k10'] and rt.freq == 9992
    assert tu.params() == (-40, 0, 0, 1, 0)  # an empty channel: hiss, no carrier
    assert all(ms != 350 for _, ms in tu.timers)  # nothing to land on
    steps(tu, KEY_NEXT, 2, gap=20)
    assert rt.freq == 9992 + 2 * 6
    steps(tu, KEY_PREV, 1)
    assert rt.freq == 10002


def test_a_station_is_a_detent(tu):
    """CAPTURE 0.04 MHz: a step that would reach within it of a station, or pass it, lands on it with the louder
    click; the next HOLD (3) steps the same way only click in place, then the dial moves on."""
    rt = tu.rt
    rt.screen, rt.freq = TUNER, 9300
    assert steps(tu, KEY_NEXT, 3) == ['k10', 'k10', 'k14'] and rt.freq == 9310  # 93.02, 93.04, then 93.06 is caught
    assert [ms for _, ms in tu.timers][-1] == 350  # the stream starts once the dial lands
    assert steps(tu, KEY_NEXT, 3) == ['k6'] * 3 and rt.freq == 9310
    assert steps(tu, KEY_NEXT, 1) == ['k10'] and rt.freq == 9312
    rt.freq = 9302
    steps(tu, KEY_NEXT, 1, gap=10)  # fast: 93.08 is within reach, so 93.1
    assert rt.freq == 9310
    rt.freq, rt.det_f = 9303, 0
    tu.now += 1000
    tu.key(KEY_NEXT)  # 93.05: 0.05 short of it, not caught
    assert rt.freq == 9305
    tu.now += 10
    tu.key(KEY_NEXT)  # 0.06 from 93.05 passes 93.1: it lands on it instead
    assert rt.freq == 9310


def test_sitting_on_a_station_holds_too(tu):
    rt = tu.rt
    rt.screen, rt.freq = TUNER, 9000
    tu.key(KEY_FWD_BTN)  # seek lands on 92.3
    assert rt.freq == 9230
    assert steps(tu, KEY_PREV, 4) == ['k6', 'k6', 'k6', 'k10'] and rt.freq == 9228


def test_a_station_is_heard_within_0_1_mhz(tu):
    """at(): the nearest station within 0.1 MHz is the one on the dial, off centre as a real set hears it."""
    rt = tu.rt
    rt.screen, rt.freq = TUNER, 9302
    tune = tu.fw.fn('radio_tuner__tune', None)
    tune()
    assert 350 in [ms for _, ms in tu.timers]  # 93.1, 0.08 MHz off, is the station here: its stream starts
    assert tu.fire('land') == 0 and tu.sent[0] == 'uhttps://xrt' and rt.feed == 2
    tu.now += 1000
    tu.fw.fn('radio_tuner__receive', None)()
    assert tu.params()[0] == 80 and tu.params()[1] == -80 and tu.params()[4] == 9310  # 0.08 MHz below it
    rt.freq = 9330  # an empty channel next to an 80 dB station: the same stream, splattering in
    tune()
    assert [ms for _, ms in tu.timers][-1] != 4000 and rt.feed == 2
    rt.freq = 9360  # nothing near enough: the stream is left warm
    tune()
    assert [ms for _, ms in tu.timers][-1] == 4000 and rt.feed == 2
    tu.fire('cool')
    assert 'x' in tu.sent and rt.feed == -1


def test_the_needle_glides_with_a_90_ms_time_constant(tu):
    rt = tu.rt
    rt.screen, rt.freq = TUNER, 9990
    tu.key(KEY_NEXT)
    rt.freq, rt.from_, rt.glide_at = 10090, 9990.0, tu.now
    tu.now += 90
    assert tu.fw.fn('radio_tuner__needle', C.c_float)() == pytest.approx(10090 - 100 * 0.3679, abs=0.5)
    tu.now += 2000
    assert tu.fw.fn('radio_tuner__needle', C.c_float)() == 10090 and rt.glide_at == 0


def test_the_wheel_turns_the_dial_by_its_travel(tu, fw):
    """12 degrees of the touch wheel a step, as the mockup's drag: 200 units a turn, so 20 units are 3 steps. The
    tuner reads the wheel raw while it is the top window's tuner screen, and gives it back otherwise."""
    rt = tu.rt
    rt.screen, rt.freq = TUNER, 9990
    top = [PAGE]
    fw.mock('window_manager_get_top_window', lambda *_: top[0])
    raw, travel = fw.var('wheel_raw', C.c_int), fw.var('wheel_travel', C.c_int)
    tu.poll()
    assert raw.value == 1
    travel.value = -20  # clockwise: the travel falls
    tu.poll(300)
    assert rt.freq == 9996 and travel.value == 0
    travel.value = -7  # a step and a little: the little is kept
    tu.poll(300)
    travel.value = -7
    tu.poll(300)
    assert rt.freq == 10000
    travel.value = 42  # anticlockwise, with the little kept 6 steps in 60 ms: fast
    tu.poll(60)
    assert rt.freq == 10000 - 6 * 6
    rt.screen = LIST
    tu.poll()
    assert raw.value == 0
    rt.screen, top[0] = TUNER, 0x9999
    tu.poll()
    assert raw.value == 0


def test_a_station_with_no_stream_sputters(tu):
    rt = tu.rt
    rt.screen, rt.freq = TUNER, 10586
    tu.key(KEY_NEXT)
    assert rt.freq == 10590 and tu.params() == (9, 0, 700, 1, -10590)


def test_seek_skips_stations_with_no_stream(tu):
    rt = tu.rt
    rt.screen, rt.freq = TUNER, 10000
    tu.key(KEY_FWD_BTN)
    assert rt.freq == 8810  # past 105.9 (no stream) and round the end
    tu.key(KEY_BACK_BTN)
    assert rt.freq == 9310  # round the bottom end: the highest that streams
    tu.key(KEY_BACK_BTN)
    assert rt.freq == 9230


def test_pause_stops_the_stream_and_the_sound(tu):
    rt = tu.rt
    rt.screen, rt.freq, rt.feed = TUNER, 9310, 2
    tu.key(KEY_PLAY)
    assert rt.playing == 0 and 'x' in tu.sent and rt.feed == -1 and tu.params()[3] == 0
    tu.key(KEY_PLAY)
    assert rt.playing == 1 and tu.params()[3] == 1 and [ms for _, ms in tu.timers][-1] == 350


def test_dial_ends(tu):
    rt = tu.rt
    rt.screen, rt.freq = TUNER, 10790
    tu.key(KEY_NEXT)
    assert rt.freq == 10790
    rt.freq = 8790
    tu.key(KEY_PREV)
    assert rt.freq == 8790


def test_the_words_wait_for_the_station(tu):
    """The reveal: a station's name and details show only once its stream is sounding and its title has come, or
    4 s of sound with none; never "No station". Once up they stay while the dial rests on it, paused included."""
    rt = tu.rt
    rt.screen, rt.freq, rt.feed = TUNER, 9310, 2
    state = '/tmp/q2tuner.state'
    tu.files.files[state] = b'state=connecting\ntitle=\n'
    tu.poll()
    assert rt.shown == 0
    tu.files.files[state] = b'state=playing\ntitle=\n'
    tu.poll()
    assert rt.shown == 0 and rt.locked_at == tu.now
    tu.poll(3000)
    assert rt.shown == 0
    tu.poll(1100)
    assert rt.shown == 3 and rt.words_at == tu.now  # WXRT's index + 1, faded in from now
    tu.key(KEY_PLAY)
    tu.poll()
    assert rt.shown == 3  # paused: the words stay, the song line says Paused
    tu.key(KEY_PLAY)
    rt.freq = 9230  # the dial left it
    tu.poll()
    assert rt.shown == 0


def test_a_title_reveals_at_once(tu):
    rt = tu.rt
    rt.screen, rt.freq, rt.feed = TUNER, 9310, 2
    tu.files.files['/tmp/q2tuner.state'] = b'state=playing\ntitle=Wilco - Jesus, Etc.\n'
    tu.poll()
    assert rt.shown == 3


def test_return_on_the_tuner_is_left_to_stock(tu):
    tu.rt.screen = TUNER
    assert tu.key(KEY_RETURN) == 0
    assert tu.key_fn(0x9999, KEY_NEXT) == 0  # another window's keys are not the tuner's


def test_the_stations_list(tu):
    rt = tu.rt
    rt.screen, rt.freq = TUNER, 9306
    tu.key(KEY_CENTER)
    assert rt.screen == LIST and rt.sel == 2  # on the station heard (0.04 MHz off it)
    tu.key(KEY_NEXT)
    assert rt.sel == 3
    tu.key(KEY_CENTER)
    assert rt.screen == TUNER and rt.freq == 10590
    tu.key(KEY_CENTER)
    for _ in range(10):
        tu.key(KEY_NEXT)
    assert rt.sel == rt.n + 1  # the last row, Enter ZIP Code, and no further
    tu.key(KEY_CENTER)
    assert rt.screen == ZIP and list(rt.zip) == [6, 0, 6, 0, 1] and rt.zi == 0
    tu.key(KEY_RETURN)
    assert rt.screen == LIST
    tu.key(KEY_RETURN)
    assert rt.screen == TUNER


def test_use_my_location_fetches_again(tu, fw):
    rt = tu.rt
    rt.screen, rt.sel = LIST, rt.n
    tu.files.files[ZIP_FILE] = b'60605'
    tu.key(KEY_CENTER)
    assert rt.screen == LOADING and tu.forks == [1]
    assert ZIP_FILE not in tu.files.files and LIST_FILE not in tu.files.files
    assert tu.params()[3] == 0  # silent while it looks


def test_zip_entry(tu):
    rt = tu.rt
    rt.screen, rt.zi = ZIP, 0
    for i, d in enumerate([6, 0, 6, 0, 1]):
        rt.zip[i] = d
    tu.key(KEY_PREV)
    assert rt.zip[0] == 5
    tu.key(KEY_NEXT)
    for d in range(4):
        tu.key(KEY_CENTER)
    assert rt.zi == 4
    tu.key(KEY_NEXT)
    tu.key(KEY_NEXT)
    assert list(rt.zip) == [6, 0, 6, 0, 3]
    tu.key(KEY_RETURN)
    assert rt.zi == 3
    tu.key(KEY_CENTER)
    tu.key(KEY_CENTER)
    assert rt.screen == LOADING and rt.zip_text == b'60603' and tu.forks == [1]


def test_the_fetch_ends(tu, fw):
    rt = tu.rt
    poll = fw.fn('tuner_poll', None)
    fw.var('radio_tuner__rt_fetch_pid', C.c_int).value = 4242
    rt.screen, rt.freq = LOADING, 10000
    fw.mock('fw_waitpid', lambda *_: 0)
    poll()
    assert rt.screen == TUNER and rt.freq == 8810  # the first that streams
    fw.mock('fw_waitpid', lambda *_: 4242)
    del tu.files.files[LIST_FILE]
    rt.screen = LOADING
    poll()
    assert rt.screen == ERROR and rt.err == b'No internet connection'
    assert fw.var('radio_tuner__rt_fetch_pid', C.c_int).value == 0


@pytest.mark.parametrize('raw, shown', [
    ('Wilco - Jesus, Etc.', 'Wilco - Jesus, Etc.'),
    ('THE ROLLING STONES - PAINT IT BLACK', 'The Rolling Stones - Paint It Black'),
    ('AC/DC - BACK IN BLACK', 'AC/DC - Back in Black'),
    ('title="R.I.P.",artist="MOTIONLESS IN WHITE",url="song_spot=\\"M\\""', 'Motionless in White - R.I.P.'),
    ('text="Spot Block" song_spot="T"', 'Spot Block'),
    ('', ''),
])
def test_titles(fw, raw, shown):
    out = C.create_string_buffer(256)
    fw.fn('tuner_title', None, C.c_char_p, C.c_char_p, C.c_uint)(raw.encode(), out, 256)
    assert out.value.decode() == shown


def test_no_wi_fi_stands_until_the_link_is_back_and_then_the_station_is_started_again(tu, fw):
    """Grayson, 2026-11-20: "shows a toast if wifi is lost that's persistent". Lost: "No Wi-Fi" stays up (no_wifi)
    while Radio is open; back: it goes, and the receiver is told at once (r) to start a stream that isn't sounding."""
    rt = tu.rt
    rt.screen, rt.freq, rt.feed = TUNER, 9310, 2
    tu.poll()
    assert rt.no_wifi == 0 and 'r' not in tu.sent
    tu.signal = 0
    for _ in range(20):  # 12 s without a link: it stays
        tu.poll()
        assert rt.no_wifi == 1
    tu.signal = 2
    tu.poll()
    assert rt.no_wifi == 0 and tu.sent.count('r') == 1
    tu.poll()
    assert 'r' not in tu.sent  # once



def test_a_stall_with_the_link_up_is_static_and_no_toast(tu):
    """A stream that stalls while Wi-Fi stays up: the receiver's own static (q2video -t), and no "No Wi-Fi"."""
    tu.rt.screen, tu.rt.feed = TUNER, 2
    tu.files.files['/tmp/q2tuner.state'] = b'state=stalled\ntitle=\ncodec=AAC\nurl=https://xrt\n'
    tu.poll()
    assert tu.rt.state == b'stalled' and tu.rt.no_wifi == 0 and 'r' not in tu.sent
