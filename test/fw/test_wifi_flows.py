"""wifi_flows.c: Settings > Wi-Fi past stock's flows. The switch and a joining network's own row end a join that is
still trying (stock ignores both until it gives up); Search Again shows "Searching..." at once; a failed join of a
network that asks for a password asks for it again with why; Play/Pause held on a saved network offers Forget; and
Reset Network Settings sits at the page's foot."""
import ctypes as C

import pytest

from conftest import high_halves, libc
from test_nav_library_pages import Ui

EVT_CLICK = 0x10c
STATE_AT = 0xd8  # g_wifiStatus's wpa_state


HIGHS = high_halves(4)  # this file's (conftest)


def strings(n):
    """Network n's name and flags at fixed addresses (stock's 32-bit scan record, a pointer a word, read 8 bytes at a
    time here): the name's high half is the flags' low, so P(rec, 0) and P(rec, 4) each read a whole address."""
    return HIGHS[2 * n] << 32, HIGHS[2 * n + 1] << 32 | HIGHS[2 * n]


@pytest.fixture(autouse=True)
def pages():
    mapped = []
    try:
        for n in range(2):
            for at in strings(n):
                page = at & ~0xfff
                got = libc.mmap(page, 0x2000, 3, 0x100022, -1, 0)  # read/write, MAP_FIXED_NOREPLACE
                if got not in (None, C.c_void_p(-1).value):
                    mapped.append(got)
                assert got == page
        yield
    finally:
        for page in mapped:
            assert libc.munmap(C.c_void_p(page), C.c_size_t(0x2000)) == 0


class Page:
    """The Wi-Fi page with stock's rows as wifimenu_rows_build makes them: the switch (button 0), Search Again (1),
    the header and the busy line, one row per scan record (its button named by its index, a signal image in it)."""
    def __init__(self, fw, networks=(('Home', '[WPA2-PSK-CCMP][ESS]'), ('Cafe', '[ESS]')), saved=('Home',)):
        self.fw, self.ui, self.saved = fw, Ui(fw), set(saved)
        self.records = []
        for n, (ssid, flags) in enumerate(networks):
            name, fl = strings(n)
            C.memmove(name, ssid.encode() + b'\0', len(ssid) + 1)
            C.memmove(fl, flags.encode() + b'\0', len(flags) + 1)
            rec = (C.c_uint * 3)(name & 0xffffffff, fl & 0xffffffff, fl >> 32)
            self.records.append(rec)
        fw.var('fw_ctx_wifi_showlist', C.c_uint).value = self.ui.deques.new(C.addressof(r) for r in self.records)
        fw.var('g_wififlag', C.c_ubyte).value = 1
        self.win = self.ui.new('window_create', 0, 0, 0, 0, 0)
        self.ui.name[self.win] = 'wifi_page'
        self.view = self.ui.new('scroll_view_create', self.win, 0, 26, 375, 294)
        self.ui.name[self.view] = 'scroll_view_wifi'
        self.calls, self.went, self.answer = [], [], 0
        fw.mock('stock_wifi_rows_trampoline', self.build)
        fw.mock('stock_wifi_switch_row_trampoline', lambda b, e, *_: self.calls.append(('switch_row', self.ui.name[b])) or 0)
        fw.mock('stock_wifi_network_row_trampoline', lambda b, e, *_: self.calls.append(('network_row', self.ui.name[b])) or 0)
        fw.mock('networkDisconnect', lambda *_: self.calls.append(('disconnect',)) or 0)
        self.fit = 1
        fw.mock('networkCheckSsidPassword', lambda f, pw, *_: self.fit)
        fw.mock('networkCheckConnectedSsid', lambda s, *_: C.string_at(s).decode() in self.saved)
        fw.mock('wifi_action_deleterecord', lambda i, *_: self.calls.append(('forget', i)) or 0)
        fw.mock('wifi_action_connect', lambda i, *_: self.calls.append(('connect', i)) or 0)
        fw.mock('wifi_switch', lambda a, *_: self.calls.append(('wifi_switch', a)) or 0)
        fw.mock('networkClearSsid', lambda *_: self.calls.append(('clear',)) or 0)
        fw.mock('networkGetSsidList', lambda p, *_: self.calls.append(('read', C.string_at(p).decode())) or 0)
        fw.mock('fw_system', lambda cmd, *_: self.calls.append(('system', C.string_at(cmd).decode())) or 0)
        fw.mock('navigator_to', self.navigate)
        fw.mock('widget_get_prop_bool', lambda w, k, d, *_: self.ui.props.get((w, C.string_at(k).decode()), d & 0xff))
        self.tips = None
        fw.fn('ringnav_wifi_rows', C.c_int, C.c_void_p)(self.win)

    def navigate(self, name, *_):
        """navigator_to: the password entry runs its loop, in which wifi_flows' timer writes its box's tips."""
        name = C.string_at(name).decode()
        self.went.append((name, self.fw.var('fw_ctx_prepare_linkssid', C.c_char * 256).value.decode()))
        if name == 'dialog/kbwifipass_dialog':
            ui = self.ui
            dialog = ui.new('window_create', 0, 0, 0, 0, 0)
            ui.name[dialog] = 'kbwifipass_dialog'
            edit = ui.new('edit', ui.new('list_item', dialog, 0, 0, 375, 60), 60, 8, 250, 44)
            ui.name[edit] = 'edit_passwd'
            ui.windows.append(ui.new('window_create', 0, 0, 0, 0, 0))  # the keyboard's window, over it
            tip = C.cast(getattr(self.fw.lib, 'wifi_flows__reason_tip'), C.c_void_p).value
            for t, (fn, ctx, ms) in list(ui.timers.items()):
                if fn == tip:
                    del ui.timers[t]
                    C.CFUNCTYPE(C.c_int, C.c_void_p)(fn)(ctx)
            self.tips = ui.props.get((edit, 'tips'))
            for w in ui.windows[-2:]:
                ui.destroy(w)
                if w in ui.windows:
                    ui.windows.remove(w)
        return self.answer

    def build(self, *_):
        ui = self.ui
        for kid in list(ui.kids[self.view]):
            ui.destroy(kid)
        for i in range(2):
            item = ui.new('list_item_create', self.view, 0, 0, 375, 70)
            ui.name[ui.new('button_create', item, 20, 0, 335, 70)] = str(i)
        head = ui.new('list_item_create', self.view, 0, 0, 375, 44)
        ui.name[ui.new('label_create', head, 0, 0, 200, 32)] = '_wifi_busy'
        for i in range(len(self.records)):
            item = ui.new('list_item_create', self.view, 0, 0, 375, 70)
            b = ui.new('button_create', item, 20, 0, 335, 70)
            ui.name[b] = str(i)
            ui.image[ui.new('image_create', b, 0, 0, 50, 70)] = 'wifi_sig3'
            ui.props[(ui.kids[b][0], 'image')] = 'wifi_sig3'
        return 0

    def ask(self):
        """The timer a failed join left, if any, run as the next loop pass runs it."""
        ask = C.cast(getattr(self.fw.lib, 'wifi_flows__ask_again'), C.c_void_p).value
        for t, (fn, ctx, ms) in list(self.ui.timers.items()):
            if fn == ask:
                del self.ui.timers[t]
                C.CFUNCTYPE(C.c_int, C.c_void_p)(fn)(ctx)

    def row(self, i):
        """The button of the view's i-th row."""
        return self.ui.kids[self.ui.kids[self.view][i]][0]

    def joining(self, ssid, state=b'SCANNING'):
        self.fw.var('wifi_connectstatus', C.c_ubyte).value = 1
        self.fw.var('fw_ctx_wifi_name', C.c_char * 256).value = ssid.encode()
        self.state(state)

    def state(self, state):
        status = self.fw.var('fw_ctx_g_wifiStatus', C.c_char * 248)
        C.memmove(C.addressof(status) + STATE_AT, state + b'\0', len(state) + 1)

    def click(self, b, fn):
        return self.fw.fn(fn, C.c_int, C.c_void_p, C.c_void_p)(b, 0)


def test_the_switch_ends_a_join_that_is_trying_and_turns_wi_fi_off(fw):
    """Stock's switch click returns at once while wifi_connectstatus is 1; the join ends first, as stock's own
    timeout ends it (networkDisconnect, the name cleared, status 0), so stock's click turns Wi-Fi off."""
    p = Page(fw)
    p.joining('Home')
    p.click(p.row(0), 'ringnav_wifi_switch_row')
    assert p.calls == [('disconnect',), ('switch_row', '0')]
    assert fw.var('wifi_connectstatus', C.c_ubyte).value == 0 and fw.var('fw_ctx_wifi_name', C.c_char * 256).value == b''
    assert not p.ui.toasts


def test_the_switch_with_no_join_is_stock_s(fw):
    p = Page(fw)
    p.click(p.row(0), 'ringnav_wifi_switch_row')
    assert p.calls == [('switch_row', '0')]


def test_the_joining_network_s_row_ends_the_join_and_shows_it_idle(fw):
    """The row of the network a join is trying ends it, no toast, and the list is rebuilt (the row loses its
    Connecting line); stock would ignore the tap."""
    p = Page(fw)
    p.joining('Home')
    p.click(p.row(3), 'ringnav_wifi_network_row')
    assert p.calls == [('disconnect',)] and fw.var('wifi_connectstatus', C.c_ubyte).value == 0
    assert not p.ui.toasts and p.ui.text[p.ui.kids[p.row(5)][0]] == 'Reset Network Settings'


def test_another_network_s_row_during_a_join_is_stock_s(fw):
    p = Page(fw)
    p.joining('Home')
    p.click(p.row(4), 'ringnav_wifi_network_row')
    assert p.calls == [('network_row', '1')] and fw.var('wifi_connectstatus', C.c_ubyte).value == 1


def test_search_again_shows_searching_at_once(fw):
    """Stock's wifi_scan sets wifi_searchtime but rebuilds nothing until the search ends: the line is written now."""
    p = Page(fw)
    fw.mock('stock_wifi_switch_row_trampoline', lambda b, e, *_: setattr(fw.var('wifi_searchtime', C.c_ubyte), 'value', 10) or 0)
    p.click(p.row(1), 'ringnav_wifi_switch_row')
    busy = p.ui.lookup(p.win, '_wifi_busy', 1)
    assert p.ui.text[busy] == 'Searching...'


def test_search_again_during_a_join_says_why_it_waits(fw):
    p = Page(fw)
    p.joining('Home')
    p.click(p.row(1), 'ringnav_wifi_switch_row')
    assert p.calls == [] and p.ui.toasts == ['Connecting to Wi-Fi…']


def fail(fw, p, states):
    """network_checkstatus polls while the join tries, through states, then times out (stock's 2)."""
    check = fw.fn('ringnav_wifi_checkstatus', C.c_int)
    result = []
    fw.mock('stock_wifi_checkstatus_trampoline', lambda *_: result.append(0) or 0)
    for s in states:
        p.state(s)
        check()

    def timed_out(*_):
        fw.var('wifi_connectstatus', C.c_ubyte).value = 0
        fw.var('fw_ctx_wifi_name', C.c_char * 256).value = b''
        return 2
    fw.mock('stock_wifi_checkstatus_trampoline', timed_out)
    return check()


def test_a_join_refused_its_key_asks_for_the_password_again(fw):
    """Through the handshake and never COMPLETED: Wrong password, over stock's password entry for that network; no
    stock toast (1, not 2); the password entered replaces the saved one and the join starts again."""
    p = Page(fw)
    p.joining('Home')
    assert fail(fw, p, [b'SCANNING', b'ASSOCIATING', b'4WAY_HANDSHAKE', b'DISCONNECTED']) == 1
    p.ask()
    assert p.went == [('dialog/kbwifipass_dialog', 'Home')]
    assert p.tips == 'Wrong password'
    assert p.calls == [('forget', 0), ('connect', 0)] and fw.var('g_wifisetting_flag', C.c_ubyte).value == 1


def test_a_join_that_never_reached_the_network_says_couldn_t_connect(fw):
    p = Page(fw)
    p.joining('Home')
    assert fail(fw, p, [b'SCANNING', b'SCANNING']) == 1
    p.answer = 1  # Cancel: the saved password stays
    p.ask()
    assert p.went == [('dialog/kbwifipass_dialog', 'Home')] and p.calls == []
    assert p.tips == "Couldn't connect"


def test_an_ok_with_no_fit_password_keeps_the_saved_one(fw):
    """Return can quit the entry with OK and an empty box: wifi_action_connect would refuse it, so nothing is
    forgotten or joined."""
    p = Page(fw)
    p.joining('Home')
    fail(fw, p, [b'SCANNING'])
    p.fit = 0
    p.ask()
    assert p.went == [('dialog/kbwifipass_dialog', 'Home')] and p.calls == []


def test_an_open_network_s_failure_stays_stock_s_toast(fw):
    p = Page(fw)
    p.joining('Cafe')
    assert fail(fw, p, [b'SCANNING']) == 2
    p.ask()
    assert p.went == []


def test_a_failure_off_the_wi_fi_page_stays_stock_s_toast(fw):
    p = Page(fw)
    p.ui.windows.append(p.ui.new('window_create', 0, 0, 0, 0, 0))
    p.joining('Home')
    assert fail(fw, p, [b'4WAY_HANDSHAKE']) == 2


def test_play_pause_held_on_a_saved_network_asks_to_forget_it(fw):
    p = Page(fw)
    p.ui.props[(p.view, '_ringnav_index')] = 2  # the tap targets': switch, Search Again, Home
    assert fw.fn('wifi_flows_hold', C.c_int, C.c_void_p)(p.win) == 1
    assert p.went[-1][0] == 'dialog/sortselect_dialog'  # the confirmation card
    answer = fw.fn('wifi_flows__forget_answer', None, C.c_void_p, C.c_int, C.c_int)
    answer(None, 0, 0)
    assert p.calls == []
    answer(None, 1, 0)
    assert p.calls == [('forget', 0)] and p.ui.toasts == ['Network Forgotten']


def test_held_on_an_unsaved_network_or_another_row_is_not_taken(fw):
    p = Page(fw)
    for i in (0, 1, 3):
        p.ui.props[(p.view, '_ringnav_index')] = i
        assert fw.fn('wifi_flows_hold', C.c_int, C.c_void_p)(p.win) == 0


def test_reset_network_settings_puts_back_the_shipped_file_and_restarts_wi_fi(fw):
    p = Page(fw)
    reset = p.row(5)
    assert p.ui.text[p.ui.kids[reset][0]] == 'Reset Network Settings'
    p.ui.handler(reset, EVT_CLICK)()
    assert p.went[-1][0] == 'dialog/sortselect_dialog'
    fw.fn('wifi_flows__reset_answer', None, C.c_void_p, C.c_int, C.c_int)(None, 1, 0)
    assert p.calls == [('wifi_switch', 1),
                       ('system', 'cp -f /release/assets/default/raw/wpa_supplicant.conf /mnt/data/wpa_supplicant.conf'),
                       ('clear',), ('read', '/mnt/data/wpa_supplicant.conf'), ('wifi_switch', 1)]
    assert fw.var('wifi_searchtime', C.c_ubyte).value == 50 and p.ui.toasts == ['Network Settings Reset']
