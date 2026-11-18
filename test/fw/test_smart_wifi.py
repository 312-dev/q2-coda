"""Smart Wi-Fi, patch/smart_wifi.c (docs/internals.md#smart-wi-fi): what keeps the radio on past SMART_IDLE_MS.
stock's wifi_switch is a stand-in that toggles g_wififlag as stock's does; test/patch.py runs stock's own."""
import ctypes as C

import pytest

from conftest import O, stock_start_with
from test_nav_display_library import scratch, st  # noqa: F401 (fixtures)
from test_nav_library_pages import Config


class Q2:
    def __init__(self, fw, st, **config):
        self.fw, self.ms, self.switched = fw, 1_000_000, []
        self.config = Config(fw, **config)
        fw.mock('time_now_ms', lambda *_: self.ms & 0xffffffff)
        self.wifi = fw.var('g_wififlag', C.c_ubyte)
        fw.mock('wifi_switch', self.switch)
        C.c_int.from_address(O['NET_TYPE']).value = 2  # initNetworkModule's Wi-Fi: stock's switch acts
        st('smart_wifi').value = 1
        self.wifi.value = 1  # the user's Wi-Fi on
        self.poll = fw.fn('smart_poll', None)
        self.off = fw.fn('smart_wifi_off', C.c_int)

    def switch(self, *_):
        self.wifi.value ^= 1
        self.switched.append(self.wifi.value)
        return 0

    def idle(self, ms):
        """Polls every SMART_POLL_MS for ms with nothing else going on."""
        for _ in range(ms // O['SMART_POLL_MS'] + 1):
            self.poll()
            self.ms += O['SMART_POLL_MS']


def test_nothing_needing_the_network_turns_the_radio_off(fw, st):
    q2 = Q2(fw, st, CARREMOTE=b'0')
    q2.idle(O['SMART_IDLE_MS'] - O['SMART_POLL_MS'])
    assert q2.switched == [] and not q2.off()
    q2.idle(2 * O['SMART_POLL_MS'])
    assert q2.switched == [0] and q2.off() == 1


@pytest.mark.parametrize('minutes', [6, 60])
def test_car_remote_on_keeps_the_radio_on(fw, st, minutes):
    """A phone connects over Wi-Fi when the car starts, to a daemon that is on whether a session is open or not."""
    q2 = Q2(fw, st, CARREMOTE=b'1')
    q2.idle(minutes * 60_000)
    assert q2.switched == [] and not q2.off() and q2.wifi.value == 1


HOLD, FREE = 'wpa_cli -i wlan0 scan_interval 3600 >/dev/null 2>&1', 'wpa_cli -i wlan0 scan_interval 5 >/dev/null 2>&1'


class Scans(Q2):
    """The scan hold (docs/internals.md#scan-hold): what smart_poll tells wpa_supplicant, through wpa_cli, about
    its rescan while nothing is joined. sock is its control socket's inode, None with no wpa_supplicant; pages the
    window stack's names; playing and out stock's player (2 playing; 1 Bluetooth)."""
    def __init__(self, fw, st, **config):
        super().__init__(fw, st, **config)
        st('smart_wifi').value = 0  # the hold runs whether Smart Wi-Fi's row is on or not
        self.sock, self.pages, self.sent, self.playing, self.out = 7, [], [], 2, 1
        self.names = {}
        fw.mock('fw___xstat', self.stat)
        fw.mock('fw_system', lambda cmd, *_: self.sent.append(C.string_at(cmd).decode()) or 0)
        fw.mock('mclGetPlayStatus', lambda *_: self.playing)
        fw.mock('mclGetOutputWay', lambda *_: self.out)
        fw.mock('window_manager', lambda *_: 0x9000)
        fw.mock('widget_count_children', lambda *_: len(self.pages))
        fw.mock('widget_get_child', lambda wm, i, *_: 0xa000 + i)
        fw.mock('widget_get_prop_str', self.prop)
        fw.mock('tk_str_start_with', stock_start_with)
        fw.mock('tk_strcmp', lambda a, b, *_: C.string_at(a) != C.string_at(b))

    def stat(self, version, path, info, *_):
        if self.sock is None or C.string_at(path) != b'/var/run/wpa_supplicant/wlan0':
            return -1
        (C.c_uint * 36).from_address(info)[4] = self.sock
        return 0

    def prop(self, w, key, default, *_):
        name = self.names.setdefault(w, C.create_string_buffer(self.pages[w - 0xa000].encode()))
        return C.addressof(name)

    def open(self, *pages):
        self.pages, self.names = list(pages), {}


def test_playing_to_a_headset_holds_the_rescan_until_it_stops(fw, st):
    q2 = Scans(fw, st, CARREMOTE=b'0')
    q2.open('home_page', 'playing_page')
    q2.idle(10_000)
    assert q2.sent == [HOLD]  # once: the same wpa_supplicant is not told again
    q2.playing = 3
    q2.idle(3_000)
    assert q2.sent == [HOLD, FREE]


@pytest.mark.parametrize('adapt', [b'0', b'1'])
@pytest.mark.parametrize('page', ['wifi_page', 'kbwifipass_dialog'])
def test_the_wi_fi_page_scans_as_stock_s_while_music_plays(fw, st, adapt, page):
    """The user's rule: on the Wi-Fi page scanning runs normally, music playing or not, Adapt to Signal on or not."""
    q2 = Scans(fw, st, CARREMOTE=b'0', BTSTEPDOWN=adapt)
    q2.open('home_page', 'systemset_page', page)
    q2.idle(10_000)
    assert q2.sent == [FREE]  # a demo started beside a held wpa_supplicant puts its 5 s back
    q2.open('home_page', 'playing_page')
    q2.idle(2_000)
    assert q2.sent == [FREE, HOLD]
    q2.open('home_page', 'systemset_page', page)
    q2.idle(2_000)
    assert q2.sent == [FREE, HOLD, FREE]


def test_a_new_wpa_supplicant_is_held_again(fw, st):
    q2 = Scans(fw, st, CARREMOTE=b'0')
    q2.idle(2_000)
    q2.sock = None  # Wi-Fi off: nothing to tell
    q2.idle(2_000)
    q2.sock = 8  # back on, a new wpa_supplicant at scan_interval 5
    q2.idle(2_000)
    assert q2.sent == [HOLD, HOLD]


@pytest.mark.parametrize('playing, out, carremote', [(3, 1, b'0'), (2, 0, b'0'), (2, 2, b'0'), (2, 1, b'1')])
def test_no_hold_unless_a_headset_plays_and_nothing_needs_the_network(fw, st, playing, out, carremote):
    """Paused, on the jack or USB, or with Car Remote on (its phone joins over Wi-Fi when the car starts)."""
    q2 = Scans(fw, st, CARREMOTE=carremote)
    q2.playing, q2.out = playing, out
    q2.idle(5_000)
    assert HOLD not in q2.sent


def test_a_failed_wpa_cli_is_retried_after_a_pause(fw, st):
    q2 = Scans(fw, st, CARREMOTE=b'0')
    fails = []
    fw.mock('fw_system', lambda cmd, *_: fails.append(C.string_at(cmd).decode()) or 1)
    q2.idle(9_000)
    assert fails == [HOLD]
    q2.idle(2_000)
    assert fails == [HOLD, HOLD]
