"""Smart Wi-Fi, patch/smart_wifi.c (docs/internals.md#smart-wi-fi): what keeps the radio on past SMART_IDLE_MS.
stock's wifi_switch is a stand-in that toggles g_wififlag as stock's does; test/patch.py runs stock's own."""
import ctypes as C

import pytest

from conftest import O, Files, stock_start_with
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


def car_remote_on(fw, q2, link):
    """Car Remote turned on now (its daemon started: coda_rc_shim.c's use count starts), Wi-Fi's link as given."""
    fw.mock('networkGetConnectStatus', lambda *_: link)
    fw.fn('coda_rc_toggle', None)()


def test_car_remote_with_no_wi_fi_link_lets_the_radio_go(fw, st):
    """docs/internals.md#smart-wi-fi. The heat log of 2026-11-22: 485 idle samples on battery with Car Remote on and no
    network joined, the radio on and scanning (the driver's thread and wpa_supplicant among the busiest) for nothing:
    with no saved network in range the phone comes over Bluetooth. Car Remote holds no radio without a link."""
    q2 = Q2(fw, st, CARREMOTE=b'0')
    car_remote_on(fw, q2, link=0)
    q2.idle(O['SMART_IDLE_MS'] + 2 * O['SMART_POLL_MS'])
    assert q2.switched == [0] and q2.off() == 1


def test_car_remote_holds_a_linked_radio_ten_minutes_after_its_last_use(fw, st):
    """With a link, a phone may come over this Wi-Fi or through q2-iroh: the radio stays for 10 minutes after Car
    Remote's last use (here its start), then goes SMART_IDLE_MS later, as for anything else."""
    q2 = Q2(fw, st, CARREMOTE=b'0')
    car_remote_on(fw, q2, link=1)
    q2.idle(10 * 60_000)
    assert q2.switched == [] and not q2.off()
    q2.idle(O['SMART_IDLE_MS'] + 2 * O['SMART_POLL_MS'])
    assert q2.switched == [0] and q2.off() == 1


@pytest.mark.parametrize('page', ['radio_page', 'tuner_page'])
def test_a_page_that_streams_keeps_the_radio_on(fw, st, page):
    """Internet Radio's lists and Home's Radio (radio_tuner.c, whose stations stream while it is open): Smart Wi-Fi
    leaves the radio on however long the page stays up."""
    q2 = Scans(fw, st, CARREMOTE=b'0')
    st('smart_wifi').value = 1
    q2.open('home_page', page)
    q2.idle(3 * O['SMART_IDLE_MS'])
    assert q2.switched == [] and not q2.off() and q2.wifi.value == 1


HOLD, FREE = 'wpa_cli -i wlan0 scan_interval 3600 >/dev/null 2>&1', 'wpa_cli -i wlan0 scan_interval 5 >/dev/null 2>&1'


class Scans(Q2):
    """The scan hold (docs/internals.md#scan-hold): what smart_poll tells wpa_supplicant, through wpa_cli, about
    its rescan while nothing is joined. sock is its control socket's inode, None with no wpa_supplicant; pages the
    window stack's names; playing and out stock's player (2 playing; 1 Bluetooth)."""
    def __init__(self, fw, st, **config):
        super().__init__(fw, st, **config)
        st('smart_wifi').value = 0  # the hold runs whether Smart Wi-Fi's row is on or not
        self.sock: int | None = 7
        self.pages: list[str] = []
        self.sent: list[str] = []
        self.playing, self.out = 2, 1
        self.linked = 0  # networkGetConnectStatus: nothing joined
        self.lit = fw.var('g_backlight_status', C.c_ubyte)
        self.lit.value = 1  # the screen on: the backoff below waits for dark
        self.cable = fw.var('g_usbdet_value', C.c_ubyte)
        self.names: dict[int, C.Array[C.c_char]] = {}
        fw.mock('networkGetConnectStatus', lambda *_: self.linked)
        fw.mock('fw___xstat', self.stat)
        self.asked: list[str] = []  # the join watch's wpa_cli status, apart from the intervals
        self.files = Files(fw)  # wlan0's operstate and the join watch's wpa_cli output
        self.status = b''  # what wpa_cli status and scan_results | wc -l print
        fw.mock('fw_system', self.system)
        fw.mock('mclGetPlayStatus', lambda *_: self.playing)
        fw.mock('mclGetOutputWay', lambda *_: self.out)
        fw.mock('window_manager', lambda *_: 0x9000)
        fw.mock('widget_count_children', lambda *_: len(self.pages))
        fw.mock('widget_get_child', lambda wm, i, *_: 0xa000 + i)
        fw.mock('widget_get_prop_str', self.prop)
        fw.mock('tk_str_start_with', stock_start_with)
        fw.mock('tk_strcmp', lambda a, b, *_: C.string_at(a) != C.string_at(b))

    def system(self, cmd, *_):
        cmd = C.string_at(cmd).decode()
        if 'scan_interval' in cmd:
            self.sent.append(cmd)
        else:
            self.asked.append(cmd)
            self.files.files['/tmp/q2-wpa-status'] = self.status
        return 0

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


@pytest.mark.parametrize('playing, out, carremote', [(3, 1, 0), (2, 0, 0), (2, 2, 0), (2, 1, 1)])
def test_no_hold_unless_a_headset_plays_and_nothing_needs_the_network(fw, st, playing, out, carremote):
    """Paused, on the jack or USB, or with Car Remote wanting the network (linked and used lately)."""
    q2 = Scans(fw, st, CARREMOTE=b'0')
    if carremote:
        car_remote_on(fw, q2, link=1)
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


def interval(cmd: str) -> int:
    return int(cmd.split()[4])


def test_dark_and_unjoined_backs_the_rescan_off_to_two_minutes(fw, st):
    """2026-11-19, a Q2 in a pocket with only the car's network saved: wpa_supplicant scanned every 5 s for hours
    with the screen off (ps_info's IPS leave count, one scan each 7.5 to 10 s). Dark with nothing joined, each
    rescan waits twice the last, from 5 s up to two minutes."""
    q2 = Scans(fw, st, CARREMOTE=b'1')
    q2.playing = 3  # paused on the jack: no hold
    q2.idle(2_000)
    assert q2.sent == [FREE]
    q2.lit.value = 0
    q2.idle(10 * 60_000)
    assert [interval(c) for c in q2.sent] == [5, 10, 20, 40, 80, 120]


@pytest.mark.parametrize('why', ['lit', 'linked', 'joining', 'wifi_page', 'cable'])
def test_the_backoff_ends_at_once(fw, st, why):
    """The screen on, a link, a join under way, the Wi-Fi page (the user's rule: it scans as stock's) or a cable
    put 5 s back, which also brings the rescan already due forward; dark again, it starts from 5 s."""
    q2 = Scans(fw, st, CARREMOTE=b'0')
    q2.playing = 3
    q2.lit.value = 0
    q2.idle(60_000)
    assert interval(q2.sent[-1]) == 40
    if why == 'lit':
        q2.lit.value = 1
    elif why == 'linked':
        q2.linked = 1
    elif why == 'joining':
        fw.var('wifi_connectstatus', C.c_ubyte).value = 1
    elif why == 'wifi_page':
        q2.open('home_page', 'systemset_page', 'wifi_page')
    else:
        q2.cable.value = 1
    sent = len(q2.sent)
    q2.idle(10 * 60_000)
    assert [interval(c) for c in q2.sent[sent:]] == [5]


def test_a_headset_s_hold_wins_over_the_backoff(fw, st):
    q2 = Scans(fw, st, CARREMOTE=b'0')
    q2.lit.value = 0
    q2.idle(5 * 60_000)
    assert q2.sent == [HOLD]


def wifi_lines(fw):
    """demo's stderr as (level, message) for its wifi lines"""
    out = []
    fw.mock('fw_write', lambda fd, data, n, *_: (fd == 2 and (lambda f: f[3] == 'wifi' and out.append((f[2], f[4])))(
        C.string_at(data, n).decode().rstrip('\n').split(' ', 4))) or n)
    return out


@pytest.mark.parametrize('playing', [2, 3])
def test_a_boot_after_an_update_rescans_every_5_s_with_the_cable_in(fw, st, playing):
    """A restart by the updater: the screen dark and nobody touching it, the cable in, a headset's connect under
    way and the resumed stream on the Bluetooth output, played or paused. The cable needs the network, so neither
    the hold nor the dark backoff applies: wpa_supplicant rescans every 5 s."""
    q2 = Scans(fw, st, CARREMOTE=b'0')
    q2.lit.value, q2.cable.value, q2.playing, q2.out = 0, 1, playing, 1
    q2.idle(10 * 60_000)
    assert q2.sent == [FREE]


def test_a_wpa_supplicant_that_never_joins_says_why_once_a_minute(fw, st):
    """The join watch: unassociated 30 s after wpa_supplicant's socket appeared, then each minute, a line with its
    wpa_state, the networks its last scan saw, the interval it was given and why, and stock's switch flags; the
    association and the link (a lease) as they come, with the seconds since its start."""
    said = wifi_lines(fw)
    q2 = Scans(fw, st, CARREMOTE=b'0')
    q2.playing, q2.lit.value = 3, 0  # paused, dark, no cable: the backoff
    q2.status = (b'bssid=\nwpa_state=DISCONNECTED\naddress=00:11:22:33:44:55\n'
                 b'3\n')  # scan_results: its header and two networks
    q2.idle(29_000)
    assert said == [] and q2.asked == []
    q2.idle(2_000)
    assert said == [('W', 'not joined 30 s after wpa_supplicant started: wpa_state=DISCONNECTED, 2 networks in its '
                          'last scan, rescans every 20 s (dark backoff), switch 1 scan 0 connect 0')]
    assert '/tmp/q2-wpa-status' not in q2.files.files
    q2.idle(56_000)
    assert len(said) == 1
    q2.idle(2_000)
    assert len(said) == 2 and said[1][1].startswith('not joined 90 s after wpa_supplicant started')
    q2.files.files['/sys/class/net/wlan0/operstate'] = b'up\n'
    q2.idle(1_000)
    assert said[2:] == [('I', 'associated, 93 s after wpa_supplicant started')]
    q2.linked = 1
    q2.idle(1_000)
    assert said[3:] == [('I', 'link up (a lease), 95 s after wpa_supplicant started')]
    q2.idle(5 * 60_000)
    assert len(said) == 4 and len(q2.asked) == 2  # joined: nothing more asked

