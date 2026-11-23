"""wifi_rows.c's network list (docs/oled-design.md Screens: Wi-Fi): one "Networks" header; the network joining, joined
or whose join failed is the first row under it, one line high like every other, its state a mark on the right and
a caption under the bar while selected; the rest in scan order. The selection stays on the network it was on through
a rebuild, and follows a pressed network to the top. Stock's rows are made here as wifimenu_rows_build makes them,
laid out by ipod_list_layout around a stand-in for stock's list layouter."""
import ctypes as C

import pytest

from conftest import O, Deques, high_halves, libc
from test_nav_display_library import (  # noqa: F401 (fixtures)
    OLED, STOCK_BODY, SettingsList, Ui, geometry, scratch, st, stand_in)

WPA, OPEN = '[WPA2-PSK-CCMP][ESS]', '[ESS]'
SCAN = [('Adams Home', WPA), ('NETGEAR47-5G', WPA), ('Cafe Lumen Guest', OPEN),
        ('Lakeview Residents WiFi 5G Ext', WPA), ('Pixel 10 Pro', WPA)]
HIGHS = high_halves(2 * 8)  # this file's (conftest): eight networks' names and flags (an SSID is 32 bytes at most)
SEL = '_ringnav_index'
CONNECTING = 'Connecting…'


def strings(n):
    """Network n's name and flags at fixed addresses, as test_wifi_flows.py lays a 32-bit scan record out."""
    return HIGHS[2 * n] << 32, HIGHS[2 * n + 1] << 32 | HIGHS[2 * n]


@pytest.fixture(autouse=True)
def pages():
    mapped = []
    try:
        for n in range(8):
            for at in strings(n):
                page = at & ~0xfff
                got = libc.mmap(page, 0x2000, 3, 0x100022, -1, 0)
                if got not in (None, C.c_void_p(-1).value):
                    mapped.append(got)
        yield
    finally:
        for page in mapped:
            libc.munmap(C.c_void_p(page), C.c_size_t(0x2000))


class Wifi:
    """Settings > Wi-Fi with Wi-Fi on: stock's rows for the scan (switch 0, Search Again 1, the header, a row per
    record named by its index, Add Network), stock's state lines from `lines` ({ssid: 'msg_connecting' or
    'msg_connected'}), rebuilt by ringnav_wifi_rows as stock's timer does and laid out."""
    def __init__(self, fw, st, geometry, stand_in, scan=SCAN):
        self.fw, self.ui = fw, Ui(fw)
        self.lst = SettingsList(fw, self.ui, st, geometry, stand_in, theme=b'%d' % OLED, window='wifi_page',
                                view='scroll_view_wifi')
        self.lst.list.name = 'list_view_wifi'
        self.view, self.win = self.lst.view, self.lst.win
        self.deques = Deques(fw)
        self.now, self.lines, self.timers = 10_000, {}, []
        fw.mock('time_now_ms', lambda *_: self.now)
        fw.mock('timer_add', lambda fn, ctx, ms, *_: self.timers.append((fn, ms)) or len(self.timers))
        fw.mock('widget_get_prop_bool', lambda w, k, d, *_: self.ui.at[w].props.get(C.string_at(k).decode(), d & 0xff)
                if w in self.ui.at else d & 0xff)
        fw.mock('widget_set_enable', lambda w, on, *_: self.ui.at[w].props.__setitem__('enable', on & 0xff))
        fw.mock('stock_wifi_rows_trampoline', lambda *_: self.build() or 0)
        fw.var('g_wififlag', C.c_ubyte).value = 1
        fw.var('wifi_searchtime', C.c_ubyte).value = 0
        self.records = []
        self.scan(scan)
        self.rebuild()

    def scan(self, nets):
        self.nets = list(nets)
        self.records = []
        for n, (ssid, flags) in enumerate(self.nets):
            name, fl = strings(n)
            C.memmove(name, ssid.encode() + b'\0', len(ssid) + 1)
            C.memmove(fl, flags.encode() + b'\0', len(flags) + 1)
            self.records.append((C.c_uint * 3)(name & 0xffffffff, fl & 0xffffffff, fl >> 32))
        self.fw.var('fw_ctx_wifi_showlist', C.c_uint).value = self.deques.new(C.addressof(r) for r in self.records)

    def build(self):
        lst, view = self.lst, self.view
        view.children.clear()
        sw = lst.row(('hscroll_label', (10, 0, 260, STOCK_BODY), 'label'), ('image', (276, 0, 50, STOCK_BODY), '', b'switch_on'))
        sw.children[0].name = '0'
        again = lst.row(('hscroll_label', (10, 0, 260, STOCK_BODY), 'label'),
                        ('image', (276, 0, 50, STOCK_BODY), 'wifi_search_icon', b'bt_refresh'))
        again.children[0].name = '1'
        head = self.ui.add('list_item', view, (0, 0, 375, 40))
        self.ui.add('label', head, (0, 8, 375, 24)).text = 'Select Network'
        for n, (ssid, _) in enumerate(self.nets):
            line = self.lines.get(ssid)
            kids = [('image', (10, 9, O['SET_STOCK_ICON'], 52), '', b'wifi_sig4'), ('hscroll_label', (72, 0, 200, STOCK_BODY), 'label')]
            if line:
                kids.append(('label', (72, 40, 200, 20), 'state'))
            if line == 'msg_connected':
                kids.append(('image', (276, 0, 50, STOCK_BODY), 'forget', b'navbar_delete'))
            item = lst.row(*kids)
            b = item.children[0]
            b.name = str(n)
            b.children[1].text = ssid
            if line:
                b.children[2].props['tr_text'] = line.encode()
        lst.row(('hscroll_label', (10, 0, 260, STOCK_BODY), 'label')).children[0].name = 'add'

    def rebuild(self):
        """Stock's timer rebuilds the list (ringnav_wifi_rows), then the page lays it out."""
        self.fw.fn('ringnav_wifi_rows', C.c_int, C.c_void_p)(self.win.addr)
        self.lst()

    def state(self, ssid, status):
        """Stock's globals for a join: wifi_name and wifi_connectstatus (1 joining, 2 joined, 0 none), and the line
        its rows give that network."""
        self.fw.var('fw_ctx_wifi_name', C.c_char * 256).value = ssid.encode()
        self.fw.var('wifi_connectstatus', C.c_ubyte).value = status
        self.lines = {ssid: {1: 'msg_connecting', 2: 'msg_connected'}[status]} if status else {}

    def items(self):
        return [i for i in self.view.children if i.children and i.children[0].kind == 'button']

    def networks(self):
        return [i for i in self.items() if i.children[0].name.isdigit() and any(
            c.props.get('image') in (b'wifi_sig4', '') and c.kind == 'image' for c in i.children[0].children)
            and i.children[0].children[1].text]

    def names(self):
        return [i.children[0].children[1].text for i in self.networks()]

    def row(self, ssid):
        return next(i for i in self.networks() if i.children[0].children[1].text == ssid)

    def sel(self):
        return self.view.props.get(SEL, -1)

    def select(self, ssid):
        """The selection on ssid's row: its id among the list's tap targets (navigation.c load_rows)."""
        self.view.props[SEL] = self.items().index(self.row(ssid))

    def selected(self):
        it = self.items()
        s = self.sel()
        return it[s].children[0].children[1].text if 0 <= s < len(it) and it[s] in self.networks() else None

    def captions(self):
        table = self.fw.var('settings_pages__cap', C.c_void_p * 17)
        if table[0] != self.view.addr:
            return {}
        rows = {i.addr: i.children[0].children[1].text for i in self.networks()}
        return {rows.get(table[1 + k]): C.wstring_at(table[9 + k]) for k in range(8) if table[1 + k]}


def scan_names():
    return [s for s, _ in SCAN]


def test_a_joining_network_is_one_line_high_first_under_networks_with_its_state_as_a_caption(fw, st, geometry, stand_in):
    """The owner's report: "Connecting..." sat 19 px under its own name and 8 px over the next row's bar, and read as
    that row's. Now the joining network is the first row under Networks, one settings row high as every other, stock's
    line hidden, and "Connecting..." a caption under the bar while it is selected."""
    w = Wifi(fw, st, geometry, stand_in)
    w.state('Lakeview Residents WiFi 5G Ext', 1)
    w.rebuild()
    assert w.names() == ['Lakeview Residents WiFi 5G Ext', 'Adams Home', 'NETGEAR47-5G', 'Cafe Lumen Guest', 'Pixel 10 Pro']
    row = w.row('Lakeview Residents WiFi 5G Ext')
    _, name, line = row.children[0].children
    assert not line.visible and row.box[3] == w.lst.g['V_SET_ROW'] and name.box[1] == 0 and name.box[3] == row.box[3]
    assert w.captions() == {'Lakeview Residents WiFi 5G Ext': CONNECTING}
    head = w.view.children.index(next(i for i in w.view.children if i.find('_wifi_section')))
    assert w.view.children[head + 1] is row  # directly under the header: no gap, no other header


def test_a_joined_network_is_first_with_connected_as_its_caption(fw, st, geometry, stand_in):
    w = Wifi(fw, st, geometry, stand_in)
    w.state('Cafe Lumen Guest', 2)
    w.rebuild()
    assert w.names()[0] == 'Cafe Lumen Guest' and w.names()[1:] == [s for s in scan_names() if s != 'Cafe Lumen Guest']
    assert w.captions() == {'Cafe Lumen Guest': 'Connected'} and w.row('Cafe Lumen Guest').box[3] == w.lst.g['V_SET_ROW']


def test_nothing_joined_is_scan_order(fw, st, geometry, stand_in):
    w = Wifi(fw, st, geometry, stand_in)
    assert w.names() == scan_names() and w.captions() == {}


def test_a_pressed_network_moves_first_and_the_selection_goes_with_it(fw, st, geometry, stand_in):
    """Adams Home joined and first; Lakeview, fourth, selected and pressed: it is first, selected, joining. The bar is
    never left on the network that took its place, so a second Centre cannot join another one."""
    w = Wifi(fw, st, geometry, stand_in)
    w.state('Adams Home', 2)
    w.rebuild()
    w.select('Lakeview Residents WiFi 5G Ext')

    def stock_press(button, event, *_):  # stock's row click: the join starts and the list is rebuilt
        w.state('Lakeview Residents WiFi 5G Ext', 1)
        fw.fn('ringnav_wifi_rows', C.c_int, C.c_void_p)(w.win.addr)
        return 0
    fw.mock('stock_wifi_network_row_trampoline', stock_press)
    b = w.row('Lakeview Residents WiFi 5G Ext').children[0]
    fw.fn('ringnav_wifi_network_row', C.c_int, C.c_void_p, C.c_void_p)(b.addr, None)
    w.lst()
    assert w.names()[0] == 'Lakeview Residents WiFi 5G Ext' and w.selected() == 'Lakeview Residents WiFi 5G Ext'
    # the network joined before goes back to its scan place: first of the rest
    assert w.names()[1:] == ['Adams Home', 'NETGEAR47-5G', 'Cafe Lumen Guest', 'Pixel 10 Pro']
    w.state('Lakeview Residents WiFi 5G Ext', 2)
    w.rebuild()
    assert w.selected() == 'Lakeview Residents WiFi 5G Ext' and w.captions() == {
        'Lakeview Residents WiFi 5G Ext': 'Connected'}


def test_a_rescan_keeps_the_active_row_first_and_the_selection_on_its_network(fw, st, geometry, stand_in):
    w = Wifi(fw, st, geometry, stand_in)
    w.state('Lakeview Residents WiFi 5G Ext', 1)
    w.rebuild()
    w.select('NETGEAR47-5G')
    w.scan([('Pixel 10 Pro', WPA), ('NETGEAR47-5G', WPA), ('Adams Home', WPA), ('Lakeview Residents WiFi 5G Ext', WPA)])
    w.rebuild()
    assert w.names() == ['Lakeview Residents WiFi 5G Ext', 'Pixel 10 Pro', 'NETGEAR47-5G', 'Adams Home']
    assert w.selected() == 'NETGEAR47-5G'


def test_stock_dropping_the_joining_state_for_a_check_does_not_move_the_row(fw, st, geometry, stand_in):
    """Stock's 500 ms check can clear wifi_connectstatus before it is set again: the row stays first through a
    rebuild with no state, and goes back to scan order only once none has shown for WIFI_SETTLE_MS (2 s), when the
    timer that waited rebuilds the list."""
    w = Wifi(fw, st, geometry, stand_in)
    w.state('Lakeview Residents WiFi 5G Ext', 1)
    w.rebuild()
    w.state('Lakeview Residents WiFi 5G Ext', 0)
    w.now += 500
    w.rebuild()
    assert w.names()[0] == 'Lakeview Residents WiFi 5G Ext'
    w.state('Lakeview Residents WiFi 5G Ext', 1)
    w.now += 500
    w.rebuild()
    assert w.names()[0] == 'Lakeview Residents WiFi 5G Ext'
    w.state('Lakeview Residents WiFi 5G Ext', 0)
    w.now += 500
    w.rebuild()
    assert fw.var('g_wifisetting_flag', C.c_ubyte).value == 0
    fn, ms = w.timers[-1]
    assert ms == 1500  # the rest of the 2 s since stock last showed it
    w.now += 1500
    C.CFUNCTYPE(C.c_int, C.c_void_p)(fn)(None)
    assert fw.var('g_wifisetting_flag', C.c_ubyte).value == 1  # the page's timer rebuilds
    w.rebuild()
    assert w.names() == scan_names() and w.captions() == {}


def fail(fw, w, ssid, state=b'4WAY_HANDSHAKE'):
    """A join of ssid that wpa_supplicant refused (its handshake reached, never COMPLETED), as stock's check ends it."""
    status = fw.var('fw_ctx_g_wifiStatus', C.c_char * 248)
    C.memmove(C.addressof(status) + 0xd8, state + b'\0', len(state) + 1)
    w.state(ssid, 1)
    fw.mock('stock_wifi_checkstatus_trampoline', lambda *_: 0)
    fw.fn('ringnav_wifi_checkstatus', C.c_int)()
    w.state(ssid, 0)
    fw.mock('stock_wifi_checkstatus_trampoline', lambda *_: 2)
    fw.fn('ringnav_wifi_checkstatus', C.c_int)()


def test_a_failed_join_stays_first_with_why_until_the_next_scan(fw, st, geometry, stand_in):
    w = Wifi(fw, st, geometry, stand_in)
    fail(fw, w, 'NETGEAR47-5G')
    w.now += 10_000
    w.rebuild()
    assert w.names()[0] == 'NETGEAR47-5G' and w.captions() == {'NETGEAR47-5G': 'Wrong password'}
    # Search Again: stock's click starts the scan (wifi_searchtime) and rebuilds only once it ends
    fw.mock('stock_wifi_switch_row_trampoline', lambda *_: setattr(fw.var('wifi_searchtime', C.c_ubyte), 'value', 10) or 0)
    again = next(i for i in w.items() if i.children[0].name == '1').children[0]
    fw.fn('ringnav_wifi_switch_row', C.c_int, C.c_void_p, C.c_void_p)(again.addr, None)
    fw.var('wifi_searchtime', C.c_ubyte).value = 0
    w.rebuild()
    assert w.names() == scan_names() and w.captions() == {}


def test_a_failed_join_gives_way_to_the_next_join(fw, st, geometry, stand_in):
    w = Wifi(fw, st, geometry, stand_in)
    fail(fw, w, 'NETGEAR47-5G', b'SCANNING')  # never reached the network
    w.rebuild()
    assert w.captions() == {'NETGEAR47-5G': "Couldn't connect"}
    w.state('Adams Home', 1)
    w.rebuild()
    assert w.names() == scan_names() and w.captions() == {'Adams Home': CONNECTING}


def marks(fw, w, ssid, selected=0):
    """The glyphs wifi_mark draws on ssid's row, right to left (theme_art's names, the theme's prefix dropped)."""
    loads = []
    fw.mock('widget_load_image', lambda wd, name, bm, *_: loads.append(C.string_at(name).decode()) or 0)
    sig = next(c for c in w.row(ssid).children[0].children if c.kind == 'image' and c.visible)
    fw.fn('wifi_mark', None, C.c_void_p, C.c_void_p, C.c_int, C.c_int)(sig.addr, None, 4, selected)
    return [n.split('_', 1)[1] if n.startswith('oled_') else n for n in loads]


def test_the_marks_say_joining_joined_and_failed(fw, st, geometry, stand_in):
    w = Wifi(fw, st, geometry, stand_in)
    w.state('Adams Home', 1)
    w.rebuild()
    assert marks(fw, w, 'Adams Home')[:2] == ['wifi_3_d', 'lock_d'] and marks(fw, w, 'Adams Home')[2].startswith('spin_')
    assert marks(fw, w, 'NETGEAR47-5G') == ['wifi_3_d', 'lock_d']
    w.state('Adams Home', 2)
    w.rebuild()
    assert marks(fw, w, 'Adams Home') == ['trash_d', 'wifi_3_d', 'lock_d', 'check_a']
    fail(fw, w, 'Cafe Lumen Guest', b'SCANNING')
    w.rebuild()
    assert marks(fw, w, 'Cafe Lumen Guest') == ['wifi_3_d', 'wifi_warn_d']  # an open network: no lock
    assert marks(fw, w, 'Cafe Lumen Guest', 1) == ['wifi_3_d', 'wifi_warn_w']


def test_a_long_name_ends_before_the_marks(fw, st, geometry, stand_in):
    w = Wifi(fw, st, geometry, stand_in)
    w.state('Lakeview Residents WiFi 5G Ext', 1)
    w.rebuild()
    sig, name, _ = w.row('Lakeview Residents WiFi 5G Ext').children[0].children
    assert name.box[0] + name.box[2] <= sig.box[0] - 8


def test_the_joined_networks_trash_ends_on_the_right_column_with_its_glyphs_left_of_it(fw, st, geometry, stand_in):
    """Stock's Forget image (navbar_delete) on the joined row keeps its tap but no art, centred on the right column's
    last 20 px slot; the signal's box takes that slot too, for wifi_mark to draw the trash there and the signal, lock
    and check a slot left, so the trash lines up with every other row's signal, selected or not. A second layout
    finds it by its mark, not its emptied image."""
    w = Wifi(fw, st, geometry, stand_in)
    w.state('Adams Home', 2)
    w.rebuild()
    sig, name, _, trash = w.row('Adams Home').children[0].children
    other = w.row('NETGEAR47-5G').children[0].children[0]
    right = 375 - 28
    assert sig.box[0] + sig.box[2] == right and sig.box[2] == 4 * 20 + 3 * 8
    assert other.box[0] + other.box[2] == right and other.box[2] == 3 * 20 + 2 * 8
    assert trash.box[0] + trash.box[2] // 2 == right - 10 and trash.box[2] == 50 and trash.visible
    assert trash.props['image'] in (b'', '') and name.box[0] + name.box[2] <= sig.box[0]
    w.lst()
    assert trash.box[0] + trash.box[2] // 2 == right - 10


def test_a_network_stock_still_shows_joined_but_not_first_shows_no_trash(fw, st, geometry, stand_in):
    """Adams Home joined, then a join of NETGEAR47-5G: stock can show both lines for a moment. Only the first row is
    the joined one's: Adams Home, back in scan order, has no trash or check, and its Forget tap is hidden."""
    w = Wifi(fw, st, geometry, stand_in)
    w.state('NETGEAR47-5G', 1)
    w.lines['Adams Home'] = 'msg_connected'
    w.rebuild()
    assert w.names()[0] == 'NETGEAR47-5G' and w.captions() == {'NETGEAR47-5G': CONNECTING}
    kids = w.row('Adams Home').children[0].children
    assert not kids[2].visible and not kids[3].visible and w.row('Adams Home').box[3] == w.lst.g['V_SET_ROW']
    assert marks(fw, w, 'Adams Home') == ['wifi_3_d', 'lock_d']


def test_a_rebuilt_list_drops_the_old_rows_captions(fw, st, geometry, stand_in):
    """Stock rebuilds the list on every scan and link change, its items destroyed: a caption entry for a gone item
    would be written to by the caption's growth, and fill the table."""
    w = Wifi(fw, st, geometry, stand_in)
    w.state('Adams Home', 2)
    w.rebuild()
    old = w.row('Adams Home').addr
    w.rebuild()
    table = fw.var('settings_pages__cap', C.c_void_p * 17)
    items = [table[1 + k] for k in range(8) if table[1 + k]]
    assert items == [w.row('Adams Home').addr] and old not in items


def test_a_row_made_where_a_captioned_row_was_has_no_caption(fw, st, geometry, stand_in):
    """Stock's rebuild frees its items and makes new ones, which can take a freed item's address: the emulator showed
    a failed network's "Wrong password" under Search Again after a rebuild (2026-11-22), the caption table's entry
    for the old item read as the new one's. Every row without a state drops any caption its item holds."""
    w = Wifi(fw, st, geometry, stand_in)
    again = next(i for i in w.items() if i.children[0].name == '1')
    fw.fn('caption_row', None, C.c_void_p, C.c_char_p)(again.children[0].children[0].addr, b'Wrong password')
    w.lst()
    table = fw.var('settings_pages__cap', C.c_void_p * 17)
    assert again.addr not in [table[1 + k] for k in range(8)]
