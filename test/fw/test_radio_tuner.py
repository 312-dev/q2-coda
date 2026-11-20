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
                *((k, C.c_int) for k in 'audio sock volume touch tx tf moved'.split()),
                ('place', C.c_char * 48), ('err', C.c_char * 64), ('title_text', C.c_char * 256), ('state', C.c_char * 12),
                ('zip_text', C.c_char * 8), ('st', Station * RT_MAX)]


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
    assert [s[c].freq for c in s] == [881, 923, 931, 1059]
    assert (s['WXRT'].name, s['WXRT'].city, s['WXRT'].db, round(s['WXRT'].km, 1)) == (b'93XRT', b'Chicago, IL', 80, 3.1)
    assert s['WXRT'].url == b'https://xrt'  # the first source's first stream
    assert s['WCFS-FM'].url == b'' and s['WCFS-FM'].name == b'WCFS-FM'  # no name, no stream: its call sign stands in
    assert rt.place == b'Near Chicago, IL'  # the nearest station's city (WCRX, 0.4 km)


def test_wheel_tunes_and_the_receiver_follows(tu):
    rt = tu.rt
    rt.screen, rt.freq = TUNER, 999
    assert tu.key(KEY_NEXT) == 1 and rt.freq == 1001
    assert tu.params() == (-40, 0, 0, 1, 0)  # an empty channel: hiss, no carrier
    assert all(ms != 350 for _, ms in tu.timers)  # nothing to land on
    rt.freq = 929
    tu.key(KEY_NEXT)
    assert rt.freq == 931 and [ms for _, ms in tu.timers][-1] == 350  # the stream starts once the dial lands
    assert tu.fire('land') == 0
    assert tu.sent[0] == 'uhttps://xrt' and rt.feed == 2
    assert tu.params()[0] == 80 and tu.params()[4] == 931
    tu.now += 1000  # landed
    tu.key(KEY_NEXT)  # 93.3: the same carrier, the needle gliding off it
    assert tu.params()[0] == 80 and tu.params()[4] == 931
    tu.now += 1000
    tu.fw.fn('radio_tuner__receive', None)()
    assert tu.params()[1] == 200  # 0.2 MHz off: the IF filter makes it the next channel
    tu.key(KEY_NEXT)  # 93.5: no neighbour strong enough, so the stream is left warm
    assert [ms for _, ms in tu.timers][-1] == 4000 and rt.feed == 2
    tu.fire('cool')
    assert 'x' in tu.sent and rt.feed == -1


def test_neighbour_strong_enough_splatters_in(tu):
    rt = tu.rt
    rt.screen, rt.freq = TUNER, 929
    tu.key(KEY_NEXT)  # 93.1 itself
    tu.key(KEY_NEXT)  # 93.3: an empty channel next to an 80 dB station
    assert [ms for _, ms in tu.timers][-1] == 350
    tu.fire('land')
    assert tu.sent[0] == 'uhttps://xrt'


def test_a_station_with_no_stream_sputters(tu):
    rt = tu.rt
    rt.screen, rt.freq = TUNER, 1057
    tu.key(KEY_NEXT)
    assert rt.freq == 1059 and tu.params() == (9, 0, 700, 1, -1059)


def test_seek_skips_stations_with_no_stream(tu):
    rt = tu.rt
    rt.screen, rt.freq = TUNER, 1000
    tu.key(KEY_FWD_BTN)
    assert rt.freq == 881  # past 105.9 (no stream) and round the end
    tu.key(KEY_BACK_BTN)
    assert rt.freq == 931  # round the bottom end: the highest that streams
    tu.key(KEY_BACK_BTN)
    assert rt.freq == 923


def test_pause_stops_the_stream_and_the_sound(tu):
    rt = tu.rt
    rt.screen, rt.freq, rt.feed = TUNER, 931, 2
    tu.key(KEY_PLAY)
    assert rt.playing == 0 and 'x' in tu.sent and rt.feed == -1 and tu.params()[3] == 0
    tu.key(KEY_PLAY)
    assert rt.playing == 1 and tu.params()[3] == 1 and [ms for _, ms in tu.timers][-1] == 350


def test_dial_ends(tu):
    rt = tu.rt
    rt.screen, rt.freq = TUNER, 1079
    tu.key(KEY_NEXT)
    assert rt.freq == 1079
    rt.freq = 879
    tu.key(KEY_PREV)
    assert rt.freq == 879


def test_return_on_the_tuner_is_left_to_stock(tu):
    tu.rt.screen = TUNER
    assert tu.key(KEY_RETURN) == 0
    assert tu.key_fn(0x9999, KEY_NEXT) == 0  # another window's keys are not the tuner's


def test_the_stations_list(tu):
    rt = tu.rt
    rt.screen, rt.freq = TUNER, 931
    tu.key(KEY_CENTER)
    assert rt.screen == LIST and rt.sel == 2  # on the station tuned
    tu.key(KEY_NEXT)
    assert rt.sel == 3
    tu.key(KEY_CENTER)
    assert rt.screen == TUNER and rt.freq == 1059
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
    rt.screen, rt.freq = LOADING, 1000
    fw.mock('fw_waitpid', lambda *_: 0)
    poll()
    assert rt.screen == TUNER and rt.freq == 881  # the first that streams
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
