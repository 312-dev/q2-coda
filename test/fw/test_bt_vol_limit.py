"""bt_vol_limit.c (docs/internals.md#headset-controls-and-volume): each paired Bluetooth device's Volume Limit, 0 to
100 on the Q2's scale, No Limit (100) by default. While that device is the Bluetooth output the volume stops at the
lower of it and Max Volume, from the wheel, Control Center, Playback's Volume page and the Car Remote; a wired output
or another device never sees it. Kept in /mnt/data/q2-btvollimit, read once, written whole and renamed; a corrupt or
missing file reads as no limit, and Forget This Device drops a device's line. The device's page has the row, which
opens the slider page (crossfade.c xfade_open 5)."""
import ctypes as C

import pytest

from conftest import O, Files
from test_bt_detail import Answers, Detail
from test_bt_ui import CAR, HEAD
from test_crossfade import PAGE, Page as Slider, opened

STORE, TMP = '/mnt/data/q2-btvollimit', '/mnt/data/q2-btvollimit.tmp'
NEXT, PREV = O['KEY_NEXT'], O['KEY_PREV']


class Link:
    """The output (mclGetOutputWay: 0 the jack, 1 Bluetooth) and stock's link list, its last item the device the
    output goes to, at status 3; the store's file in files; the clock."""
    def __init__(self, fw, mac=HEAD, way=1, files=None, volume=50, top=100):
        self.fw, self.mac, self.way, self.now = fw, mac, way, 1000
        self.files = Files(fw, files if files is not None else {})
        fw.mock('time_now_ms', lambda *_: self.now)
        fw.mock('mclGetOutputWay', lambda *_: self.way)
        fw.mock('getBtLinkSize', lambda *_: 1 if self.mac else 0)
        fw.mock('getBtLinkItem', self.item)
        fw.var('g_bluetoothflag', C.c_ubyte).value = 1
        self.volume, self.top = fw.var('g_volume', C.c_ubyte), fw.var('g_maxvolume', C.c_ubyte)
        self.volume.value, self.top.value = volume, top

    def item(self, i, status, mac, *_):
        C.c_int.from_address(status).value = 3
        C.memmove(mac, self.mac.encode() + b'\0', len(self.mac) + 1)
        return 0

    def cap(self):
        return self.fw.fn('vol_cap', C.c_int)()

    def limit(self, mac=None):
        return self.fw.fn('btlim_get', C.c_int, C.c_char_p)((mac or self.mac).encode())

    def set(self, v, mac=None):
        self.fw.fn('btlim_set', None, C.c_char_p, C.c_int)((mac or self.mac).encode(), v)

    def poll(self, after=300):
        self.now += after
        self.fw.fn('btlim_poll', None)()

    def played(self):
        return [a[0] for _, a in self.fw.calls('device_set_volume')]


def test_a_device_has_no_limit_by_default(fw):
    link = Link(fw, top=80)
    assert link.limit() == 100 and link.cap() == 80 and TMP not in link.files.files


def test_the_device_s_limit_holds_while_it_is_the_bluetooth_output(fw):
    link = Link(fw)
    link.set(40)
    assert link.cap() == 40
    link.way = 0  # the jack: the device's limit is not its
    assert link.cap() == 100
    link.way, link.mac = 1, CAR  # another device
    assert link.cap() == 100


@pytest.mark.parametrize('top, limit, cap', [(60, 40, 40), (30, 40, 30), (100, 0, 0), (100, 100, 100)])
def test_the_lower_of_max_volume_and_the_device_s_limit_wins(fw, top, limit, cap):
    link = Link(fw, top=top)
    link.set(limit)
    assert link.cap() == cap


def test_the_store_is_read_and_a_bad_line_passed_over(fw):
    files = {STORE: (f'{HEAD} 35\n'
                     f'{CAR} 7x\n'            # not a number
                     'AA:BB:CC:DD:EE 20\n'    # not an address
                     'AA:BB:CC:DD:EE:04 100\n'  # 100 is no limit
                     'aa:bb:cc:dd:ee:05 12').encode()}  # any case, no last newline
    link = Link(fw, files=files)
    assert link.limit() == 35 and link.limit(CAR) == 100 and link.limit('AA:BB:CC:DD:EE:04') == 100
    assert link.limit('AA:BB:CC:DD:EE:05') == 12


@pytest.mark.parametrize('data', [None, b'', b'\xff\x00garbage\n\n', b'AA:BB:CC:DD:EE:01 '])
def test_a_missing_or_corrupt_store_reads_as_no_limit(fw, data):
    link = Link(fw, files={} if data is None else {STORE: data}, top=90)
    assert link.limit() == 100 and link.cap() == 90


def test_the_slider_page_sets_the_device_s_limit_and_saves_it_whole(fw):
    link = Link(fw, mac=CAR, volume=50)  # the output is the car: the headset's limit does not touch it
    fw.fn('btlim_edit', None, C.c_char_p)(HEAD.encode())
    page = opened(fw, 5, {})
    assert page.text[page.title] == 'Volume Limit' and page.label() == 'No Limit'
    assert page.widgets['img_bootswitch'] in page.hidden
    fw.var('wheel_speed', C.c_int).value = 0  # a slow turn: 1 a tick
    assert page.key(PREV) == 1 and page.label() == '99' and link.limit(HEAD) == 99
    page.shows = 60
    page.fire('slider_bootvol', O['EVT_VALUE_CHANGING'])
    assert link.limit(HEAD) == 60 and link.files.files[STORE] == f'{HEAD} 99\n'.encode()  # a drag saves at its release
    page.fire('slider_bootvol', O['EVT_VALUE_CHANGED'])
    assert link.files.files[STORE] == f'{HEAD} 60\n'.encode() and TMP not in link.files.files
    assert link.volume.value == 50 and not link.played()
    page.shows = 100
    page.fire('slider_bootvol', O['EVT_VALUE_CHANGED'])
    assert page.label() == 'No Limit' and link.files.files[STORE] == b''  # no limit is not kept
    lo, hi = C.create_string_buffer(16), C.create_string_buffer(16)
    fw.fn('xfade_ends', None, C.c_char_p, C.c_char_p, C.c_uint)(lo, hi, 16)
    assert (lo.value, hi.value) == (b'0', b'No Limit')


def test_a_lower_limit_for_the_connected_device_lowers_the_volume_at_once(fw):
    link = Link(fw, volume=70)
    fw.fn('btlim_edit', None, C.c_char_p)(HEAD.encode())
    page = opened(fw, 5, {})
    page.shows = 45
    page.fire('slider_bootvol', O['EVT_VALUE_CHANGED'])
    assert link.volume.value == 45 and link.played() == [45]
    assert not [w for w in page.config.written if w[0] in ('VOLUME', 'BT_VOLUME')]  # not saved over every device's level


def test_a_device_connecting_above_its_limit_is_pulled_down_without_a_save(fw):
    """At boot with it connected, too: the first look finds it."""
    link = Link(fw, mac='', volume=80)
    link.set(30, HEAD)
    link.poll()
    assert link.volume.value == 80 and not link.played()
    link.mac = HEAD
    link.poll()
    assert link.volume.value == 30 and link.played() == [30] and not fw.calls('write_int_config')
    # leaving Bluetooth, its level is the one it was pulled down from, for the next device
    assert fw.fn('btlim_departing', C.c_int, C.c_int)(30) == 80


def test_another_device_starts_from_the_level_the_last_one_was_pulled_from(fw):
    link = Link(fw, volume=80)
    link.set(30)
    link.poll()
    assert link.volume.value == 30
    link.mac = CAR
    link.poll()
    assert link.volume.value == 80 and link.played() == [30, 80]


def test_a_volume_saved_after_the_pull_is_the_user_s(fw):
    link = Link(fw, volume=80)
    link.set(30)
    link.poll()
    fw.fn('btlim_saved', None)()  # the wheel moved it and saved it (ringnav_write_config)
    assert fw.fn('btlim_departing', C.c_int, C.c_int)(30) == 30


def test_the_output_becoming_bluetooth_holds_the_saved_level_to_the_limit(fw):
    link = Link(fw, way=0, volume=20)
    link.set(40)
    entering = fw.fn('btlim_entering', C.c_int, C.c_int)
    assert entering(70) == 40 and entering(25) == 25
    link.mac = CAR
    assert entering(70) == 70


def test_the_wheel_stops_at_the_limit(fw):
    link = Link(fw, volume=38)
    link.set(40)
    fw.var('wheel_speed', C.c_int).value = 0
    step = fw.fn('vol_next', C.c_int, C.c_uint)
    assert step(NEXT) == 39
    link.volume.value = 40
    assert step(NEXT) == 40 and step(PREV) == 39
    link.way = 0
    assert step(NEXT) == 41  # the jack: no device limit


@pytest.mark.parametrize('limit, start, lands', [(40, 40, 40), (40, 39, 40), (0, 0, 0)])
def test_the_volume_dialog_s_tick_lands_on_the_limit(fw, limit, start, lands):
    """Stock's own step (volume_dialog_on_keyup) clamps at Max Volume alone."""
    link = Link(fw, volume=start)
    link.set(limit)
    fw.var('wheel_speed', C.c_int).value = 0
    fw.var('g_backlight_status', C.c_ubyte).value = 1

    def stock(dialog, e, *_):
        link.volume.value = min(link.volume.value + 1, link.top.value)
        return 11
    fw.mock('stock_vol_keyup_trampoline', stock)
    event = C.create_string_buffer(0x40)
    C.c_uint.from_address(C.addressof(event) + O['EVENT_KEY']).value = NEXT
    assert fw.fn('vol_keyup', C.c_int, C.c_void_p, C.c_void_p)(None, C.addressof(event)) == 11
    assert link.volume.value == lands


def test_control_center_s_slider_stops_at_the_limit(fw):
    link = Link(fw, volume=10)
    link.set(55)
    fw.fn('control_center__cc_volume', None, C.c_int)(90)
    assert link.volume.value == 55
    link.way = 0
    fw.fn('control_center__cc_volume', None, C.c_int)(90)
    assert link.volume.value == 90


def test_playback_s_volume_page_stops_at_the_limit(fw):
    link = Link(fw, volume=10, top=60)
    link.set(35)
    page = opened(fw, 4, {})
    page.shows = 50
    page.fire('slider_bootvol', O['EVT_VALUE_CHANGED'])
    assert link.volume.value == 35


def test_forgetting_a_device_drops_its_limit(fw):
    d = Detail(fw)
    Answers(fw, d)
    files = d.files.files
    files[STORE] = f'{HEAD} 20\n{CAR} 50\n'.encode()
    d.click_row('d1')  # the car
    fw.fn('bt_detail__forget', None, C.c_void_p, C.c_int, C.c_int)(None, 1, 0)
    assert files[STORE] == f'{HEAD} 20\n'.encode()


def test_the_device_page_shows_its_limit_and_opens_the_slider(fw):
    d = Detail(fw)
    d.files.files[STORE] = f'{CAR} 45\n'.encode()
    d.click_row('d0')
    assert 'No Limit' in d.ui.shown(d.page)
    fw.fn('bt_detail__bd_closed', C.c_int, C.c_void_p, C.c_void_p)(d.page, None)
    d.click_row('d1')
    assert d.detail_rows()[1] == 'Volume Limit' and '45' in d.ui.shown(d.page)
    d.ui.click(d.page, 1)
    assert 'playset/bootvol_page' in d.ui.went and fw.fn('btlim_edit_get', C.c_int)() == 45  # the slider page
