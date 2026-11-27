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


HIGHS = high_halves(8)  # this file's (conftest): four networks' names and flags


def strings(n):
    """Network n's name and flags at fixed addresses (stock's 32-bit scan record, a pointer a word, read 8 bytes at a
    time here): the name's high half is the flags' low, so P(rec, 0) and P(rec, 4) each read a whole address."""
    return HIGHS[2 * n] << 32, HIGHS[2 * n + 1] << 32 | HIGHS[2 * n]


@pytest.fixture(autouse=True)
def pages():
    mapped = []
    try:
        for n in range(4):
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
        for n, (ssid, flags, *dbm) in enumerate(networks):
            name, fl = strings(n)
            C.memmove(name, ssid.encode() + b'\0', len(ssid) + 1)
            C.memmove(fl, flags.encode() + b'\0', len(flags) + 1)
            # the signal in dBm at 0x14 (stock's wifimenu_rows_build reads it for wifi_sig1..4)
            rec = (C.c_uint * 6)(name & 0xffffffff, fl & 0xffffffff, fl >> 32, 0, 0, (dbm[0] if dbm else -60) & 0xffffffff)
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


def closed(p):
    """demo's Wi-Fi page left for Home, as it is while q2ui has the screen."""
    home = p.ui.new('window_create', 0, 0, 0, 0, 0)
    p.ui.name[home] = 'home_page'


def run_timers(p, fn_name, passes=1):
    """Each pass, the timers named fn_name as the loop runs them: those answering RET_REPEAT (8) stay."""
    fn = C.cast(getattr(p.fw.lib, fn_name), C.c_void_p).value
    for _ in range(passes):
        for t, (f, ctx, ms) in list(p.ui.timers.items()):
            if f == fn and C.CFUNCTYPE(C.c_int, C.c_void_p)(f)(ctx) != 8:
                del p.ui.timers[t]


def scanning(fw, p):
    """Stock's wifi_scan (it sets wifi_searchtime to 10) and its results' read, noted in p.calls."""
    def scan(*_):
        p.calls.append(('scan',))
        fw.var('wifi_searchtime', C.c_ubyte).value = 10
        return 0
    fw.mock('wifi_scan', scan)
    fw.mock('networkGetWifiResult', lambda d, *_: p.calls.append(('result', d)) or 0)


def test_q2uis_search_is_stocks_and_its_results_are_read_as_stocks_page_would(fw):
    """With demo's page closed its timer never reads a search's results: q2ui's "scan" starts stock's search and
    counts it down as that timer does (200 ms passes from 10), then reads the results into wifi_showlist."""
    p = Page(fw)
    closed(p)
    scanning(fw, p)
    assert fw.fn('wifi_flows_scan', C.c_int)() == 0
    run_timers(p, 'wifi_flows__scan_tick')
    assert p.calls == [('scan',)] and [ms for _, _, ms in p.ui.timers.values()] == [200]
    run_timers(p, 'wifi_flows__scan_tick', 9)
    assert p.calls == [('scan',)]
    run_timers(p, 'wifi_flows__scan_tick')
    deque = fw.var('fw_ctx_wifi_showlist', C.c_uint).value
    assert p.calls == [('scan',), ('result', deque)] and not p.ui.timers


def test_a_search_asked_for_while_wi_fi_starts_waits_for_it_and_none_is_made_with_wi_fi_off(fw):
    p = Page(fw)
    closed(p)
    scanning(fw, p)
    fw.var('wifi_initstatus', C.c_ubyte).value = 1
    assert fw.fn('wifi_flows_scan', C.c_int)() == 0
    run_timers(p, 'wifi_flows__scan_tick', 5)
    assert p.calls == []
    fw.var('wifi_initstatus', C.c_ubyte).value = 0
    run_timers(p, 'wifi_flows__scan_tick')
    assert p.calls == [('scan',)]
    fw.var('g_wififlag', C.c_ubyte).value = 0
    run_timers(p, 'wifi_flows__scan_tick')
    assert not p.ui.timers and p.calls == [('scan',)]
    assert fw.fn('wifi_flows_scan', C.c_int)() == 1 and not p.ui.timers


def networks(fw, room=2048):
    out = C.create_string_buffer(room)
    n = fw.fn('wifi_flows_networks', C.c_uint, C.c_void_p, C.c_uint)(out, room)
    return out.raw[:n].decode()


def test_q2ui_is_told_each_networks_level_lock_and_join_with_the_joined_one_first(fw):
    """A line a network, "LEVEL LOCK STATE NAME", in scan order but the one with a state first, as demo's page; the
    levels stock's signal steps; a backslash and a newline in a name escaped."""
    nets = (('Cafe', '[ESS]', -72), ('Home', '[WPA2-PSK-CCMP][ESS]', -50), ('A\\\\b\nc', '[WEP]', -78), ('Far', '[ESS]', -90))
    p = Page(fw, networks=nets)
    closed(p)
    assert networks(fw) == 'scan 0\n3 0 0 Cafe\n4 1 0 Home\n2 1 0 A\\\\\\\\b\\nc\n1 0 0 Far\n'
    fw.var('wifi_connectstatus', C.c_ubyte).value = 2
    fw.var('fw_ctx_wifi_name', C.c_char * 256).value = b'Home'
    fw.var('wifi_searchtime', C.c_ubyte).value = 4
    assert networks(fw) == 'scan 1\n4 1 2 Home\n3 0 0 Cafe\n2 1 0 A\\\\\\\\b\\nc\n1 0 0 Far\n'
    fw.var('wifi_connectstatus', C.c_ubyte).value = 1
    assert networks(fw).split('\n')[1] == '4 1 1 Home'
    assert networks(fw, 30) == 'scan 1\n4 1 1 Home\n3 0 0 Cafe\n'  # whole lines only
    fw.var('g_wififlag', C.c_ubyte).value = 0
    assert networks(fw) == 'scan 1\n'


def test_q2ui_is_told_a_failed_join_and_a_refused_password(fw):
    p = Page(fw)
    closed(p)
    fw.fn('wifi_rows_failed', None, C.c_char_p, C.c_int)(b'Home', 1)
    assert networks(fw).split('\n')[1] == '4 1 4 Home'
    fw.fn('wifi_rows_failed', None, C.c_char_p, C.c_int)(b'Cafe', 0)
    assert networks(fw).split('\n')[1:3] == ['4 0 3 Cafe', '4 1 0 Home']


def nexts(fw, p, answer):
    fw.mock('check_wifinextaction', lambda at, x, lo, hi, *_: p.calls.append(('next', at, x, lo, hi)) or answer)


def test_q2uis_join_by_name_is_stocks_rows_own_steps(fw):
    """"join NAME": the network found by its name in the scan, then from a timer stock's row's steps for it: a saved
    or open one joined at once (check_wifinextaction 3)."""
    p = Page(fw)
    closed(p)
    nexts(fw, p, 3)
    fw.var('wifi_searchtime', C.c_ubyte).value = 6
    assert fw.fn('wifi_flows_join', C.c_int, C.c_char_p)(b'Cafe') == 0
    assert p.calls == []  # from a timer, as stock's dialogs open
    run_timers(p, 'wifi_flows__join_now')
    assert p.calls == [('next', 1, 0, 276, 375), ('connect', 1)] and fw.var('wifi_searchtime', C.c_ubyte).value == 0


def test_a_join_that_needs_a_password_asks_with_stocks_entry_then_joins(fw):
    p = Page(fw)
    closed(p)
    nexts(fw, p, 4)
    fw.var('fw_ctx_prepare_linkpasswd', C.c_char * 128).value = b'old'
    assert fw.fn('wifi_flows_join', C.c_int, C.c_char_p)(b'Home') == 0
    run_timers(p, 'wifi_flows__join_now')
    assert [w for w, _ in p.went] == ['dialog/kbwifipass_dialog'] and p.calls[-1] == ('connect', 0)
    assert fw.var('fw_ctx_prepare_linkpasswd', C.c_char * 128).value == b''
    p.calls.clear()
    p.answer = 1  # Cancel
    fw.fn('wifi_flows_join', C.c_int, C.c_char_p)(b'Home')
    run_timers(p, 'wifi_flows__join_now')
    assert p.calls == [('next', 0, 0, 276, 375)]


def test_a_join_of_a_joined_network_does_nothing_and_one_not_in_the_scan_is_refused(fw):
    p = Page(fw)
    closed(p)
    nexts(fw, p, 2)
    fw.fn('wifi_flows_join', C.c_int, C.c_char_p)(b'Home')
    run_timers(p, 'wifi_flows__join_now')
    assert p.calls == [('next', 0, 0, 276, 375)] and p.went == []
    assert fw.fn('wifi_flows_join', C.c_int, C.c_char_p)(b'Elsewhere') == 1 and not p.ui.timers


def test_a_join_of_the_network_that_is_joining_ends_that_join_and_another_is_refused_meanwhile(fw):
    p = Page(fw)
    closed(p)
    nexts(fw, p, 3)
    p.joining('Home')
    assert fw.fn('wifi_flows_join', C.c_int, C.c_char_p)(b'Cafe') == 1
    assert fw.fn('wifi_flows_join', C.c_int, C.c_char_p)(b'Home') == 0
    assert p.calls == [('disconnect',)] and fw.var('wifi_connectstatus', C.c_ubyte).value == 0 and not p.ui.timers


def test_q2uis_lines_down_its_pipe_search_and_join_and_the_networks_are_told_in_their_own_file(fw):
    """q2ui_gate.c: "scan" and "join NAME" down q2ui's pipe reach the search and the join, the name's escapes undone;
    the networks go to /tmp/q2ui.wifi on the status's looks, written only when they change."""
    from conftest import Files
    from test_q2ui_gate import Piped
    files = Files(fw)
    long = 'Ca\\fe\nX' + '\\' * 25  # 32 bytes, 27 escaped: a line of 65 bytes, past a setting's 64 (the longest, 70)
    p = Page(fw, networks=(('Home', '[WPA2-PSK-CCMP][ESS]', -50), (long, '[ESS]', -72)))
    g = Piped(fw)
    nexts(fw, p, 3)
    g.poll()  # q2ui started
    g.sent = b'scan\njoin Ca\\\\fe\\nX' + b'\\\\' * 25 + b'\njoin Nowhere\n'
    g.poll()
    assert sorted(ms for _, _, ms in p.ui.timers.values()) == [50, 200]
    run_timers(p, 'wifi_flows__join_now')
    assert p.calls[-1] == ('connect', 1)
    assert files.files['/tmp/q2ui.wifi'].decode() == 'scan 1\n4 1 0 Home\n3 0 0 Ca\\\\fe\\nX' + '\\\\' * 25 + '\n'
    writes = []
    rename = files.rename
    fw.mock('fw_rename', lambda a, b, *_: writes.append(C.string_at(b).decode()) or rename(a, b))
    g.ms += 600
    g.poll()
    assert '/tmp/q2ui.wifi' not in writes  # nothing changed
    fw.var('g_wififlag', C.c_ubyte).value = 0
    g.ms += 600
    g.poll()
    assert writes.count('/tmp/q2ui.wifi') == 1 and files.files['/tmp/q2ui.wifi'] == b'scan 1\n'
