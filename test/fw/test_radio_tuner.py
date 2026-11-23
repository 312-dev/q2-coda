"""radio_tuner.c (docs/internals.md#radio-home): Home's Radio. The list from q2radiofetch read, the dial's keys
and what the receiver (q2video -t) is told for each: the needle's carrier, level, offset and fade, the stream started
only once the dial lands, kept warm when it leaves, stopped on pause; seeking past stations with no stream; the
stations list, the ZIP entry and the fetch they start; the StreamTitle as the tuner shows it."""
import ctypes as C
import re

import pytest

import host
from conftest import O, ROOT, Files

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
                ('mq_key', C.c_uint * 2), ('live', C.c_int), ('vol', C.c_int),
                *((k, C.c_uint) for k in 'vol_timer hold'.split()), ('held', C.c_int), ('tap_at', C.c_uint),
                ('tap_armed', C.c_int)]


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
    lit = fw.var('g_backlight_status', C.c_ubyte)
    lit.value = 1
    awake = []
    fw.mock('reset_poweroptions_timer', lambda *a: awake.append(a[:3]))
    tu.poll()
    assert raw.value == 1
    travel.value = -20  # clockwise: the travel falls
    tu.poll(300)
    assert rt.freq == 9996 and travel.value == 0
    assert (1, 1, 1) in awake  # no key event comes: the travel holds the screen on itself
    travel.value = -7  # a step and a little: the little is kept
    tu.poll(300)
    travel.value = -7
    tu.poll(300)
    assert rt.freq == 10000
    travel.value = 42  # anticlockwise, with the little kept 6 steps in 60 ms: fast
    tu.poll(60)
    assert rt.freq == 10000 - 6 * 6
    lit.value = 0  # the screen off: ticks again, so the wheel wakes it as stock's do
    tu.poll()
    assert raw.value == 0
    lit.value = 1
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


def hold_centre(tu):
    """Centre held on the tuner until its hold goes off, then let go."""
    tu.fw.fn('tuner_keydown', None, C.c_void_p, C.c_uint)(PAGE, KEY_CENTER)
    tu.fw.fn('radio_tuner__hold_due', C.c_int, C.c_void_p)(None)
    tu.key(KEY_CENTER)


def test_the_stations_list(tu):
    rt = tu.rt
    rt.screen, rt.freq = TUNER, 9306
    hold_centre(tu)
    assert rt.screen == LIST and rt.sel == 2  # on the station heard (0.04 MHz off it)
    tu.key(KEY_NEXT)
    assert rt.sel == 3
    tu.key(KEY_CENTER)
    assert rt.screen == TUNER and rt.freq == 10590
    hold_centre(tu)
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


def test_a_stream_the_receiver_missed_is_named_again(tu):
    """A receiver started again (tune, after the last one ended) binds its socket some time after demo's land has
    named the stream, so that u can be lost: seen in the emulator, its state idle with no url while the page had the
    station's feed. The state's url= not the feed's: u again, with the carrier; the receiver ignores a u it has."""
    rt = tu.rt
    rt.screen, rt.freq, rt.feed = TUNER, 9310, 2
    tu.files.files['/tmp/q2tuner.state'] = b'state=idle\ntitle=\ncodec=\nurl=\n'
    tu.poll()
    assert 'uhttps://xrt' in tu.sent and tu.params()[4] == 9310
    tu.files.files['/tmp/q2tuner.state'] = b'state=connecting\ntitle=\ncodec=\nurl=https://xrt\n'
    tu.poll()
    assert not [m for m in tu.sent if m.startswith('u')]
    rt.feed = -1  # no stream wanted: nothing to name
    tu.files.files['/tmp/q2tuner.state'] = b'state=idle\ntitle=\ncodec=\nurl=\n'
    tu.poll()
    assert not [m for m in tu.sent if m.startswith('u')]


SCRUB_MS = O['SCRUB_MS']
_hold = re.search(r'^#define LR_HOLD_MS (\d+)', (ROOT / 'patch' / 'navigation.h').read_text(), re.M)
assert _hold, 'navigation.h defines LR_HOLD_MS'
HOLD_MS = int(_hold[1])


class Centre:
    """The tuner's Centre (approved 2026-11-20, ~/q2-emu/design/radio-tuner/centre-volume/sheet.png): a tap shows
    Now Playing's volume band in the dial's place, the wheel the volume, gone SCRUB_MS after the last turn or press;
    a hold of HOLD_MS opens the stations list, stock's Shut Down? hold (st.centre_hold) taken off it."""
    def __init__(self, tu, fw):
        self.tu, self.fw = tu, fw
        self.volume, self.limit = fw.var('g_volume', C.c_ubyte), fw.var('g_maxvolume', C.c_ubyte)
        self.volume.value, self.limit.value = 40, 100
        self.set, self.saved, self.removed = [], [], []
        fw.mock('device_set_volume', lambda v, *_: self.set.append(v) or 0)
        fw.mock('write_int_config', lambda v, *_: self.saved.append(v) or 0)
        fw.mock('timer_remove', lambda t, *_: self.removed.append(t) or 1)
        self.down_fn = fw.fn('tuner_keydown', None, C.c_void_p, C.c_uint)
        tu.rt.screen, tu.rt.freq = TUNER, 9990

    def down(self):
        self.tu.timers.clear()
        self.down_fn(PAGE, KEY_CENTER)

    def timers(self):
        return [ms for _, ms in self.tu.timers]


@pytest.fixture
def ce(tu, fw):
    return Centre(tu, fw)


def test_a_tap_shows_the_volume_and_the_wheel_sets_it(tu, ce):
    ce.down()
    assert HOLD_MS in ce.timers() and tu.rt.vol == 0  # the hold's clock, nothing shown yet
    tu.now += 200
    assert tu.key(KEY_CENTER) == 1
    assert tu.rt.vol == 1 and tu.rt.screen == TUNER and tu.rt.hold == 0
    assert SCRUB_MS in [ms for _, ms in tu.timers]
    tu.key(KEY_NEXT)
    assert ce.volume.value == 41 and ce.set == [41] and ce.saved == [41]
    assert tu.rt.freq == 9990 and not [m for m in tu.sent if m.startswith('k')]  # no tuning, no click
    tu.key(KEY_PREV)
    tu.key(KEY_PREV)
    assert ce.volume.value == 39


def test_the_volume_goes_after_the_timeout_and_the_wheel_tunes_again(tu, ce):
    ce.down()
    tu.key(KEY_CENTER)
    tu.timers.clear()
    tu.key(KEY_NEXT)
    assert [ms for _, ms in tu.timers] == [SCRUB_MS]  # a turn starts the 3 s again
    tu.fw.fn('radio_tuner__vol_end', C.c_int, C.c_void_p)(None)
    assert tu.rt.vol == 0 and tu.rt.vol_timer == 0
    tu.now += 1000
    tu.key(KEY_NEXT)
    assert tu.rt.freq == 9992 and ce.volume.value == 41


def test_a_tap_while_it_shows_closes_it(tu, ce):
    ce.down()
    tu.key(KEY_CENTER)
    ce.down()
    tu.key(KEY_CENTER)
    assert tu.rt.vol == 0


def test_the_raw_wheel_waits_while_the_volume_shows(tu, ce, fw):
    fw.mock('window_manager_get_top_window', lambda *_: PAGE)
    fw.var('g_backlight_status', C.c_ubyte).value = 1
    raw = fw.var('wheel_raw', C.c_int)
    tu.poll()
    assert raw.value == 1
    ce.down()
    tu.key(KEY_CENTER)
    tu.poll()
    assert raw.value == 0  # ticks: the volume's steps at the wheel's speed, as Now Playing's
    tu.fw.fn('radio_tuner__vol_end', C.c_int, C.c_void_p)(None)
    tu.poll()
    assert raw.value == 1


def test_a_hold_opens_the_list(tu, ce):
    tu.rt.freq = 9310
    ce.down()
    fn = tu.fw.fn('radio_tuner__hold_due', C.c_int, C.c_void_p)
    assert fn(None) == 0
    assert tu.rt.screen == LIST and tu.rt.sel == 2 and tu.rt.held == 1 and tu.rt.vol == 0
    assert tu.key(KEY_CENTER) == 1  # its release: taken, no row picked
    assert tu.rt.screen == LIST and tu.rt.held == 0
    tu.key(KEY_CENTER)  # a later tap on the list picks the row, as before
    assert tu.rt.screen == TUNER and tu.rt.freq == 9310


@pytest.fixture(scope='module')
def scratch(library):
    return host.offsets(library.parent/'navigation.o', 'scratch_t')


def test_the_hold_takes_stocks_shut_down_off(tu, ce, fw, scratch):
    """Stock's own Centre hold (Shut Down? at 1000 ms, ringnav_hold_timer's st.centre_hold) is removed when the
    list's hold goes off, as Edit Library's is (accepted with the sheet: Shut Down? is not on Radio's tuner)."""
    centre_hold = C.c_uint.from_address(C.addressof(fw.var('st', C.c_char)) + scratch['centre_hold'])
    centre_hold.value = 77
    ce.down()
    tu.fw.fn('radio_tuner__hold_due', C.c_int, C.c_void_p)(None)
    assert ce.removed == [77] and centre_hold.value == 0


def test_no_hold_off_the_tuner_screen(tu, ce):
    tu.rt.screen = LIST
    ce.down()
    assert HOLD_MS not in ce.timers()


def test_a_double_press_turns_the_screen_off(tu, ce, fw):
    """As on Now Playing and every other page: a second tap within DOUBLE_CLICK_MS of the first, the wheel not turned
    between, undoes the first (the band gone) and turns the screen off (stock's Centre release replayed)."""
    off = []
    fw.mock('on_wm_keyup_fun', lambda ctx, e, *_: off.append(C.c_uint.from_address(e + O['EVENT_KEY']).value) or 0)
    fw.var('g_backlight_status', C.c_ubyte).value = 1
    ce.down()
    tu.key(KEY_CENTER)
    assert tu.rt.vol == 1
    tu.now += O['DOUBLE_CLICK_MS'] - 50
    ce.down()
    tu.key(KEY_CENTER)
    assert tu.rt.vol == 0 and off == [KEY_CENTER]
    tu.now += 1000  # slower, or with a turn between: two taps
    ce.down()
    tu.key(KEY_CENTER)
    tu.key(KEY_NEXT)
    tu.now += 100
    ce.down()
    tu.key(KEY_CENTER)
    assert tu.rt.vol == 0 and off == [KEY_CENTER]


# Radio as Now Playing (the user's, 2026-11-20: "radio should remember place when leaving", "it should be Now
# Playing when radio is playing until library is reselected", "going back on Radio to another menu should not stop
# it"): the page is a view of a receiver that outlives it.
FREQ_FILE = '/mnt/mmc/Radio/.local/freq'
NP_PAGE = 0x6160
NOW_LIBRARY, NOW_RADIO = 1, 2  # navigation.h's NOW_* order


class Owner:
    """The tuner on a list, its receiver running, and the Library's restarts recorded."""
    def __init__(self, tu, fw):
        self.tu, self.fw = tu, fw
        tu.rt.screen, tu.rt.freq, tu.rt.feed = TUNER, 9310, 2
        self.started = []
        fw.mock('player_save_start', lambda pause, *_: self.started.append(pause) or 0)
        fw.mock('fw_waitpid', lambda *_: 999)  # the receiver goes when asked
        libc = C.CDLL(None)
        libc.calloc.restype = C.c_void_p
        heap = libc.calloc(1, C.sizeof(Rt))  # on the heap, as tuner_open's: closed and tuner_stop free it
        C.memmove(heap, C.addressof(tu.rt), C.sizeof(Rt))
        tu.rt = Rt.from_address(heap)
        fw.var('radio_tuner__rt', C.c_void_p).value = heap
        self.close = fw.fn('radio_tuner__closed', C.c_int, C.c_void_p, C.c_void_p)
        self.state = fw.fn('tuner_state', C.c_int)
        self.rt_var = fw.var('radio_tuner__rt', C.c_void_p)

    def alive(self):
        return bool(self.rt_var.value)


@pytest.fixture
def ow(tu, fw):
    return Owner(tu, fw)


def test_back_leaves_radio_playing(tu, ow):
    """Back (Return on the tuner, left to stock, which closes the page): the station plays on, nothing stopped."""
    assert tu.key(KEY_RETURN) == 0
    tu.sent.clear()
    ow.close(None, None)
    assert ow.alive() and ow.state() == 1
    assert not [m for m in tu.sent if m in ('q', 'x')]  # the receiver neither told to quit nor its stream dropped
    assert not tu.rt.page and tu.rt.freq == 9310


def test_back_on_a_page_that_found_nothing_ends_it(tu, ow):
    tu.rt.screen, tu.rt.n, tu.rt.feed = ERROR, 0, -1
    ow.close(None, None)
    assert not ow.alive()


def test_the_last_station_is_written_where_the_dial_lands(tu, ow):
    """Landing on a station writes it to the card (FREQ_FILE), where a page opened with no receiver starts
    (test_now_source.py), and tuner_stop leaves it there."""
    tu.fire('land')
    assert tu.files.files.get(FREQ_FILE) == b'9310'
    ow.close(None, None)
    tu.fw.fn('tuner_stop', None)()
    assert not ow.alive() and tu.files.files.get(FREQ_FILE) == b'9310'


def test_radio_is_the_owner_until_the_library_is_chosen(tu, ow, fw):
    """library -> Radio -> left -> still Radio (headset and car keys its) -> the Library's Play on its Now Playing:
    Radio stops, its place kept, and the Library plays on from where it was held."""
    fw.mock('mclGetPlayStatus', lambda *_: 2)
    fw.fn('now_hold', None)()  # the Library playing when Radio opened
    ow.close(None, None)
    assert fw.fn('now_owner', C.c_int)() == NOW_RADIO and fw.fn('media_status', C.c_int)() == 2
    fw.fn('media_set_playing', None, C.c_int)(0)  # a headset's pause: Radio's
    assert tu.rt.playing == 0 and not ow.started
    fw.fn('media_set_playing', None, C.c_int)(1)
    assert tu.rt.playing == 1
    fw.mock('window_manager_get_top_window', lambda *_: NP_PAGE)
    name = C.create_string_buffer(b'playing_page')
    fw.mock('widget_get_prop_str', lambda w, k, d, *_: C.addressof(name) if w == NP_PAGE else d)
    tu.sent.clear()
    assert tu.key_fn(NP_PAGE, KEY_PLAY) == 1
    assert not ow.alive() and 'q' in tu.sent
    assert ow.started == [0]  # the held song, playing from its place
    assert fw.fn('now_owner', C.c_int)() == NOW_LIBRARY


def test_a_pick_in_the_library_ends_radio_and_drops_the_hold(tu, ow, fw):
    fw.mock('mclGetPlayStatus', lambda *_: 2)
    fw.fn('now_hold', None)()
    ow.close(None, None)
    fw.fn('tuner_yield', None)()  # playback.c ringnav_load, a user's pick
    assert not ow.alive() and not ow.started  # the pick plays, not the held song
    sec, total = C.c_int(), C.c_int()
    fw.mock('mclGetPlayStatus', lambda *_: 2)
    assert fw.fn('now_time', C.c_int, C.c_void_p, C.c_void_p)(C.byref(sec), C.byref(total)) == 2  # the player's


# The dial on the piezo (the user's, 2026-11-21: "spinning the wheel is audible with the piezo like turning a
# crunchy dial"). The tuner reads the touch wheel raw, so no key event, and so none of stock's Key Tone clicks
# (buzzeer_switch on a key's press), reaches the buzzer: the dial asks for one itself, a click per step it moves.
def piezo(fw, tu, mode='run', clicker=0):
    from test_nav_loop import Buzzer
    b = Buzzer(fw, mode)
    fw.mock('window_manager_get_top_window', lambda *_: PAGE)
    tu.rt.screen = TUNER
    return b


def turn(fw, tu, units, ms=300):
    fw.var('wheel_travel', C.c_int).value = units
    tu.poll(ms)


def test_each_step_the_dial_moves_clicks_the_piezo(tu, fw):
    b = piezo(fw, tu)
    tu.rt.freq = 9990
    tu.poll()
    turn(fw, tu, -20)  # three steps, slowly: three clicks, one a step
    assert tu.rt.freq == 9996 and b.clicks() == [1, 1, 1]


def test_a_detent_s_held_steps_do_not_click_but_landing_does(tu, fw):
    b = piezo(fw, tu)
    tu.rt.freq = 9300
    tu.poll()
    turn(fw, tu, -20)  # 93.02, 93.04, then 93.1 caught
    assert tu.rt.freq == 9310 and len(b.clicks()) == 3
    turn(fw, tu, -20)  # the three steps the detent holds: the dial does not move, nothing clicks
    assert tu.rt.freq == 9310 and len(b.clicks()) == 3


def test_a_fast_spin_clicks_as_fast_as_the_buzzer_frees(tu, fw):
    """Steps that come while the last click's worker still runs (tone_busy) are dropped, not queued: the buzzer's
    own length is the spacing, so a spin is a crisp train of clicks, never a smear."""
    b = piezo(fw, tu, 'hold')
    tu.rt.freq = 9990
    tu.poll()
    turn(fw, tu, -60, 60)  # nine steps in one pass
    assert len(fw.calls('fw_pthread_create')) == 1


def test_clicker_off_is_silent(tu, fw):
    b = piezo(fw, tu)
    fw.var('g_keytone_flag', C.c_ubyte).value = 0  # Clicker: Off
    tu.rt.freq = 9990
    tu.poll()
    turn(fw, tu, -20)
    assert b.clicks() == [] and tu.rt.freq == 9996


def test_clicker_headphones_leaves_the_buzzer_quiet_and_sends_the_player_nothing(tu, fw, library):
    """Headphones never sounds the buzzer; and nothing goes to q2player, which plays nothing under Radio (the
    receiver's own click, k, is the dial's in what Radio plays)."""
    b = piezo(fw, tu)
    fw.fn('clicker_text', C.c_char_p)()  # its config read once, which would set the field back
    st = host.offsets(library.parent/'navigation.o', 'scratch_t')
    C.c_int.from_address(C.addressof(fw.var('st', C.c_char)) + st['clicker']).value = 2  # CLICKER_HEADPHONES
    tu.rt.freq = 9990
    tu.poll()
    turn(fw, tu, -20)
    assert b.clicks() == [] and not fw.calls('fw_send') and tu.rt.freq == 9996
