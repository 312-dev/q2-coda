"""bt_detail.c: a Bluetooth device's page. Centre on a saved device's row opens it, connected or not (a found row
pairs, as stock), Play/Pause held on one too: the path ("Q2 → WH-1000XM5"), its codec line, then Battery, Disconnect
(Connect), Rename and Forget This Device; Forget asks on the confirmation card, then does what stock's Unpair does and
clears the saved link (BTLINKMAC) when it names the device."""
import ctypes as C

import pytest

from conftest import claim
from test_bt_ui import CAR, DEVICES, EVT_CLICK, HEAD, Page

LINK_MAC = 0xa4c0d8  # BTLINKMAC: the device stock connects again at boot and Bluetooth on

BATT = '/tmp/q2-btbatt'


class Detail(Page):
    def __init__(self, fw, devices=DEVICES, link=(HEAD, 3), level=72, rate=96000, codec=4, coding=0, **kw):
        files = {BATT: f'{HEAD} {level if level is not None else ""} {rate or ""} \n'.encode()}
        super().__init__(fw, devices, files=files, **kw)
        self.clicks = []
        fw.mock('btmenu_item_on_click', lambda b, ev, *_: self.clicks.append((self.ui.name[b], C.c_int.from_address(ev + 0x18).value)) or 0)
        fw.var('fw_ctx_bt_showcoding', C.c_int).value = codec
        fw.var('g_btcoding', C.c_ubyte).value = coding
        self.links = [link] if link else []
        fw.fn('bt_headset_battery', C.c_int)()
        self.now += 6000
        self.rebuild()
        self.layout()

    def click_row(self, name):
        b = self.ui.kids[self.row(name)][0]
        return self.ui.handler(b, EVT_CLICK)()

    @property
    def page(self):
        pages = [w for w in self.ui.windows if self.ui.name.get(w) == 'btdevice_page']
        return pages[-1] if pages else None

    def detail_rows(self):
        return self.ui.captions(self.page)


def test_centre_on_the_connected_row_opens_its_page(fw):
    d = Detail(fw)
    d.click_row('d0')
    assert d.page and not d.clicks
    assert d.detail_rows() == ['Battery', 'Disconnect', 'Rename', 'Forget This Device']
    shown = d.ui.shown(d.page)
    assert 'Q2 → WH-1000XM5' in shown and 'LDAC · 96 kHz · 990 kbps' in shown and '72%' in shown
    assert fw.fn('bt_detail_title', C.c_char_p, C.c_char_p)(b'btdevice_page') == b'WH-1000XM5'


def test_centre_on_a_saved_row_that_is_not_connected_opens_its_page(fw):
    d = Detail(fw)
    d.click_row('d1')
    assert d.page and not d.clicks  # no connect: Connect is the page's first row
    assert d.detail_rows() == ['Connect', 'Rename', 'Forget This Device']
    shown = d.ui.shown(d.page)
    assert 'Q2 → BMW 6R81' in shown and 'Not Connected' in shown
    assert fw.fn('bt_detail_title', C.c_char_p, C.c_char_p)(b'btdevice_page') == b'BMW 6R81'


def test_centre_on_a_found_row_pairs_as_stock(fw):
    d = Detail(fw)
    d.click_row('d2')
    assert not d.page and d.clicks == [('2', 0)]


def test_connect_is_stock_s_click_on_the_device(fw):
    d = Detail(fw)
    d.click_row('d1')
    d.ui.click(d.page, 0)
    assert d.clicks == [('1', 0)]


def test_forget_this_device_is_red(fw):
    d = Detail(fw)
    for row in ('d0', 'd1'):
        d.click_row(row)
        items = d.ui.kids[d.ui.view(d.page)]
        inks = [d.ui.props.get((d.ui.kids[item][0], '_final'), 0) & 0xffffffff for item in items]
        assert inks[-1] == 0xff000000 | 0xff453a and not any(inks[:-1])
        fw.fn('bt_detail__bd_closed', C.c_int, C.c_void_p, C.c_void_p)(d.page, None)


def test_play_pause_held_on_a_paired_row_opens_its_page_with_connect(fw):
    d = Detail(fw)
    d.ui.props[(d.view, '_ringnav_index')] = d.rows().index(d.row('d1'))
    assert fw.fn('bt_ui_hold', C.c_int, C.c_void_p)(d.win) == 1
    assert d.detail_rows() == ['Connect', 'Rename', 'Forget This Device'] and 'Not Connected' in d.ui.shown(d.page)


def test_held_on_a_found_row_or_a_header_is_not_taken(fw):
    d = Detail(fw)
    for row in (d.row('d2'), d.rows()[2]):
        d.ui.props[(d.view, '_ringnav_index')] = d.rows().index(row)
        assert fw.fn('bt_ui_hold', C.c_int, C.c_void_p)(d.win) == 0
    assert not d.page


def test_disconnect_is_stock_s_click_on_the_device(fw):
    d = Detail(fw)
    d.click_row('d0')
    d.ui.click(d.page, 1)
    assert d.clicks == [('0', 0)]


@pytest.mark.parametrize('codec, coding, rate, line', [
    (4, 0, 44100, 'LDAC · 44.1 kHz · 909 kbps'), (4, 1, 48000, 'LDAC · 48 kHz · 660 kbps'),
    (4, 2, 96000, 'LDAC · 96 kHz'), (13, 0, 48000, 'aptX HD · 48 kHz · 576 kbps'), (3, 0, 44100, 'aptX · 44.1 kHz · 352 kbps'),
    (2, 0, 48000, 'AAC · 48 kHz'), (1, 0, 0, 'SBC')])
def test_the_codec_line_says_only_what_is_known(fw, codec, coding, rate, line):
    d = Detail(fw, codec=codec, coding=coding, rate=rate, level=None)
    d.click_row('d0')
    assert line in d.ui.shown(d.page) and d.detail_rows()[0] == 'Disconnect'  # no battery reported: no row


class Answers:
    def __init__(self, fw, d, pair=1, out=(2, 0), save=2):
        self.calls = []
        fw.mock('handleBluetoothPair', lambda i, *_: self.calls.append(('pair', i)) or pair)
        fw.mock('check_outputdevice', lambda was, *_: out[1])
        fw.mock('mclGetSavePlayStatus', lambda *_: save)
        fw.mock('config_outputchannel', lambda a, b, *_: self.calls.append(('output', a, b)) or 0)
        fw.mock('getSearchResult', lambda *_: self.calls.append(('read',)) or 1)
        fw.mock('player_save_start', lambda p, *_: self.calls.append(('start', p)) or 0)
        fw.mock('window_close', lambda w, *_: self.calls.append(('close', w)) or 0)
        fw.var('g_headset_output', C.c_ubyte).value = out[0]


def test_forget_asks_on_the_card_then_unpairs_as_stock(fw):
    d = Detail(fw)
    a = Answers(fw, d)
    d.click_row('d0')
    d.ui.click(d.page, 3)
    assert 'dialog/sortselect_dialog' in d.ui.went  # the confirmation card
    forget = fw.fn('bt_detail__forget', None, C.c_void_p, C.c_int, C.c_int)
    forget(None, 0, 0)  # Cancel
    assert a.calls == []
    forget(None, 1, 0)
    assert a.calls == [('pair', 0), ('read',), ('start', 1), ('close', d.page)]
    assert d.ui.toasts == ['Device Forgotten'] and fw.var('g_btsetting_flag', C.c_ubyte).value == 1


@pytest.mark.parametrize('save, output', [(2, [('output', 0, 3)]), (1, [])])
def test_forgetting_the_output_puts_the_sound_back_on_a_jack(fw, save, output):
    d = Detail(fw)
    a = Answers(fw, d, pair=2, out=(2, 0), save=save)
    d.click_row('d0')
    fw.fn('bt_detail__forget', None, C.c_void_p, C.c_int, C.c_int)(None, 1, 0)
    assert [c for c in a.calls if c[0] == 'output'] == output and fw.var('g_headset_output', C.c_ubyte).value == 0


def test_forget_removes_a_saved_device_that_is_not_connected(fw):
    d = Detail(fw)
    a = Answers(fw, d)
    d.click_row('d1')
    d.ui.click(d.page, 2)
    assert 'dialog/sortselect_dialog' in d.ui.went
    fw.fn('bt_detail__forget', None, C.c_void_p, C.c_int, C.c_int)(None, 1, 0)
    assert a.calls == [('pair', 1), ('read',), ('start', 1), ('close', d.page)]
    assert d.ui.toasts == ['Device Forgotten']


@pytest.fixture
def link_mac():
    """BTLINKMAC's 32 bytes at stock's address, set by the test and put back after."""
    claim(LINK_MAC)
    saved = C.string_at(LINK_MAC, 32)
    yield lambda mac: C.memmove(LINK_MAC, mac.encode().ljust(32, b'\0'), 32)
    C.memmove(LINK_MAC, saved, 32)


@pytest.mark.parametrize('saved, cleared', [(CAR, True), (CAR.lower(), True), (HEAD, False), ('', False)])
def test_forget_clears_the_saved_link_when_it_names_the_device(fw, link_mac, saved, cleared):
    """So neither boot nor Bluetooth on (autoLinkBluetooth) tries to connect a device that is gone."""
    d = Detail(fw)
    Answers(fw, d)
    link_mac(saved)
    d.click_row('d1')
    fw.fn('bt_detail__forget', None, C.c_void_p, C.c_int, C.c_int)(None, 1, 0)
    assert C.string_at(LINK_MAC) == (b'' if cleared else saved.encode())
    assert d.config.values.get(b'BTLINKMAC') == (b'' if cleared else None)


def test_a_failed_unpair_keeps_the_saved_link(fw, link_mac):
    d = Detail(fw)
    Answers(fw, d, pair=0)
    link_mac(CAR)
    d.click_row('d1')
    fw.fn('bt_detail__forget', None, C.c_void_p, C.c_int, C.c_int)(None, 1, 0)
    assert C.string_at(LINK_MAC) == CAR.encode() and b'BTLINKMAC' not in d.config.values


def test_a_failed_unpair_does_nothing_more(fw):
    d = Detail(fw)
    a = Answers(fw, d, pair=0)
    d.click_row('d0')
    fw.fn('bt_detail__forget', None, C.c_void_p, C.c_int, C.c_int)(None, 1, 0)
    assert a.calls == [('pair', 0)] and not d.ui.toasts


def test_rename_sets_blue_z_s_alias_and_the_list_s_name(fw):
    d = Detail(fw)
    d.click_row('d0')
    d.ui.click(d.page, 2)
    ss = d.ui.windows[-1]
    assert d.ui.name.get(ss) == 'searchstrip_page'
    done = fw.fn('bt_detail__renamed', C.c_char_p, C.c_char_p)
    assert done(b'   ') == b"Name can't be empty" and done(b"it's") == b"Can't use that name"
    assert done(b' My Cans ') is None
    alias = [c for c in d.commands if 'Properties.Set string:org.bluez.Device1 string:Alias' in c]
    assert len(alias) == 1 and '/org/bluez/hci0/dev_AA_BB_CC_DD_EE_01 ' in alias[0] and "'variant:string:My Cans'" in alias[0]
    assert fw.fn('bt_dev_name', C.c_char_p, C.c_int)(0) == b'My Cans' and 'Renamed' in d.ui.toasts
    assert 'Q2 → My Cans' in d.ui.shown(d.page)


def test_the_page_follows_the_link(fw):
    d = Detail(fw)
    d.click_row('d0')
    d.links = []
    d.poll()
    assert d.detail_rows() == ['Connect', 'Rename', 'Forget This Device']
    d.links = [(HEAD, 3)]
    d.poll()
    assert d.detail_rows()[1] == 'Disconnect'
    assert CAR  # the car's row stays as it was


def test_rename_s_return_key_is_a_check(fw):
    """Renaming a device saves a name: the strip's return key shows the check (search_strip.c ss_ret_kind)."""
    from conftest import STRIP_SAVE
    from test_search_strip import Ss
    d = Detail(fw)
    d.click_row('d0')
    d.ui.click(d.page, 2)
    assert fw.var('search_strip__ss', Ss).ret == STRIP_SAVE


def bt_lines(fw):
    """demo's stderr as (level, message) for its bt lines, as test_bt_trace.py reads it"""
    out = []
    fw.mock('fw_write', lambda fd, data, n, *_: (fd == 2 and out.append(
        tuple(C.string_at(data, n).decode().rstrip('\n').split(' ', 4)[i] for i in (2, 3, 4)))) or n)
    return out


@pytest.mark.parametrize('pair, said', [(1, ('I', f'forget {CAR}: unpaired')),
                                        (2, ('I', f'forget {CAR}: disconnected and unpaired')),
                                        (0, ('W', f'forget {CAR}: stock\'s unpair refused it (0), nothing done'))])
def test_each_forget_says_how_it_went(fw, pair, said):
    """On 2026-11-19 a Forget took two taps and the log could not say why: each answer of the card is a bt line
    now, with what stock's unpair returned, and the card's Cancel too."""
    d = Detail(fw)
    Answers(fw, d, pair=pair)
    d.click_row('d1')
    lines = bt_lines(fw)
    forget = fw.fn('bt_detail__forget', None, C.c_void_p, C.c_int, C.c_int)
    forget(None, 0, 0)
    forget(None, 1, 0)
    assert [(lv, msg) for lv, src, msg in lines if src == 'bt'] == [('I', f'forget {CAR}: Cancel chosen'),
                                                                    (said[0], said[1])]


def test_a_forget_for_a_device_gone_from_the_list_says_so(fw):
    d = Detail(fw)
    Answers(fw, d)
    d.click_row('d1')
    lines = bt_lines(fw)
    fw.var('fw_ctx_pdeq_btshowlist', C.c_uint).value = d.ui.deques.new([])  # stock read its list again since
    fw.fn('bt_detail__forget', None, C.c_void_p, C.c_int, C.c_int)(None, 1, 0)
    assert ('W', f'forget {CAR}: not in the device list now, nothing done') in [(lv, m) for lv, s, m in lines]
