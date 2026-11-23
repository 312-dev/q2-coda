"""The USB pages (patch/usb_ui.c, docs/internals.md#usb-pages): a cable's probe and the chooser, Always Do This and the
remembered choice, the mode pages and their toasts, and in a --dev build USB Debugging and adbd's question (U5)."""
import ctypes as C
import hashlib

import pytest

from conftest import O, Files
from test_coverflow import Ui, themed
from test_nav_painters import Canvas, scratch, st  # noqa: F401 (fixtures)

UDC = '/sys/class/udc/13500000.otg/state'
EVT_DESTROY, EVT_KEY_UP = O['EVT_DESTROY'], O['EVT_KEY_UP']
ROWS = ['Charge Only', 'Transfer Files', 'USB Audio']
DEV_ROWS = ROWS + ['USB Debugging']
CAR_START = '/usr/bin/q2car start >/dev/null 2>&1'  # usb_car.c usb_car_start, Automatic's iPhone
KEY = 'QUFBQUIzTnphQzF5YzJF'  # adbd's public key line, base64 (its comment after a space)


class Usb:
    """usb_ui.c over test_coverflow's widgets: stock's USB globals, config.ini's [Q2POD] and [SYSSET] keys, the
    commands run, the UDC's state file, the clock, the toasts and adbd's socket."""
    def __init__(self, fw, ini=None, tile=2):
        themed(fw)
        self.fw, self.ui = fw, Ui(fw)
        # AWTK's widget_on refuses a null handler (emitter_on's return_value_if_fail), as Allow's page_row_detail gives
        fw.mock('widget_on', lambda w, evt, fn, ctx, *_: (self.ui[w].on.append((evt, fn, ctx)) or len(self.ui[w].on)) if fn else 0)
        # Ask Every Time (5) unless a test names the setting: most of these are the probe's and the chooser's; the
        # setting's own default, Automatic (0), is test_usb_car.py's
        self.ini, self.saved, self.commands, self.toasts, self.sent = {'USBPICK': 5, **(ini or {})}, [], [], [], []
        self.now, self.socket, self.inbox = 1000, False, []
        self.files = Files(fw, {})
        self.det, self.link = fw.var('g_usbdet_value', C.c_ubyte), fw.var('g_usblink_status', C.c_ubyte)
        self.mode = fw.var('g_usbmode', C.c_ubyte)
        self.mode.value = tile
        fw.var('g_backlight_status', C.c_ubyte).value = 1
        self.status_fn, self.poll_fn = fw.fn('usb_ui_status', None), fw.fn('usb_ui_poll', None)

        def read(path, section, key, out, default, *_):  # themed's THEME, the keys given, else the default
            key = C.string_at(key).decode()
            value = str(self.ini[key]).encode() if key in self.ini else b'3' if key == 'THEME' else C.string_at(default)
            C.memmove(out, value + b'\0', len(value) + 1)
        fw.mock('toolsReadConfig', read)
        fw.mock('write_int_config', self.save)
        fw.mock('fw_system', lambda cmd, *_: self.commands.append(C.string_at(cmd).decode()) or 0)
        fw.mock('time_now_ms', lambda *_: self.now)
        fw.mock('navigator_to_with_context', lambda name, info, *_: self.toasts.append(C.string_at(info + 8).decode()) or 0)
        fw.mock('window_manager_close_window_force', lambda wm, w, *_: self.close(w))
        self.md5 = hashlib.md5()
        if 'fw_MD5_Init' not in fw.stubs:  # a release build's library: no ADB prompt, no socket
            return
        fw.mock('fw_MD5_Init', lambda *_: setattr(self, 'md5', hashlib.md5()) or 1)
        fw.mock('fw_MD5_Update', lambda ctx, data, n, *_: self.md5.update(C.string_at(data, n)) or 1)
        fw.mock('fw_MD5_Final', lambda out, *_: C.memmove(out, self.md5.digest(), 16) and 1)
        self.sockets = []  # (domain, type) of every socket() the payload made
        fw.mock('fw_socket', lambda domain, kind, *_: self.sockets.append((domain, kind)) or 7)
        fw.mock('fw_connect', lambda *_: 0 if self.socket else -1)
        fw.mock('fw_recv', self.recv)
        fw.mock('fw_send', lambda fd, data, n, *_: self.sent.append(C.string_at(data, n).decode()) or n)

    def save(self, value, section, key, *_):
        key, value = C.string_at(key).decode(), C.c_int(value).value
        self.saved.append((C.string_at(section).decode(), key, value))
        self.ini[key] = value
        return 0

    def recv(self, fd, out, size, *_):
        if not self.inbox:
            C.c_int.from_address(self.fw.fn('fw___errno_location', C.c_void_p)()).value = 11  # EAGAIN
            return -1
        data = self.inbox.pop(0)
        C.memmove(out, data, len(data))
        return len(data)

    def close(self, w):
        self.ui.click(w, EVT_DESTROY)
        self.ui.drop([w])
        if self.ui.top == w:
            self.ui.top = 0
        return 0

    def status(self, ms=0):
        self.now += ms
        self.status_fn()

    def plug(self, host=True):
        """A cable in; a computer that configures the gadget, or none."""
        self.det.value = 1
        if host:
            self.files.files[UDC] = b'configured\n'
        self.status()

    def unplug(self):
        """A cable out; stock's pass then lets go of the link (g_usblink_status 0) for the next one."""
        self.det.value = 0
        self.files.files.pop(UDC, None)
        self.status()
        self.link.value = 0
        self.poll()

    def poll(self):
        self.poll_fn()

    def page(self, name):
        return next((w for w, x in self.ui.w.items() if x.type == 'window' and x.name == name), 0)

    def rows(self, page):
        """A page's rows: their first label's text, and the row."""
        items = [w for w, x in self.ui.w.items() if x.type == 'list_item' and self.inside(w, page)]
        return [(self.ui[self.ui.of('hscroll_label', i)[0]].text, i) for i in items]

    def inside(self, w, page):
        while w:
            if w == page:
                return True
            w = self.ui[w].parent
        return False

    def row(self, page, text):
        return next(i for t, i in self.rows(page) if t == text)

    def switch(self, page, text):
        return self.ui[self.ui.lookup(self.row(page, text), '_setting_switch')].image


def chooser(fw, **kw):
    u = Usb(fw, **kw)
    u.plug()
    page = u.page('usbchoose_page')
    assert page
    return u, page


def test_a_cable_with_no_choice_probes_and_a_computer_gets_the_chooser(fw):
    """No remembered choice: stock is kept out (g_usblink_status 3) and its storage gadget starts with no card in it,
    a probe; the computer configuring it opens Use USB For on Transfer Files, a release build's three rows and Always
    Do This, off."""
    u = Usb(fw)
    u.plug(host=False)
    assert u.link.value == 3 and u.commands == ['storage_config.sh start /dev/null >/dev/null 2>&1']
    assert not u.page('usbchoose_page')
    u.files.files[UDC] = b'configured\n'
    u.status(500)
    page = u.page('usbchoose_page')
    assert [t for t, _ in u.rows(page)] == ROWS + ['Always Do This'] and u.switch(page, 'Always Do This') == 'switch_off'
    assert u.ui[u.ui.of('scroll_view')[0]].props['_ringnav_index'] == 1  # Transfer Files


def test_no_computer_within_the_probe_is_a_charger(fw):
    u = Usb(fw)
    u.plug(host=False)
    u.status(3000)
    assert len(u.commands) == 1  # USB_PROBE_MS not yet past
    u.status(1)
    assert u.commands[-1] == 'storage_config.sh stop >/dev/null 2>&1' and not u.page('usbchoose_page')
    u.files.files[UDC] = b'configured\n'
    u.status(100)
    assert not u.page('usbchoose_page')  # the probe is over: a later answer opens nothing


def test_transfer_files_for_this_cable_hands_back_to_stock_and_unplugging_restores_the_tile(fw):
    """A choice without Always Do This: the probe stops, g_usbmode is Files for this cable, stock's next pass may
    start its gadget (g_usblink_status 0); nothing is saved. Out, the tile's mode is back and Unplugged toasts."""
    u, page = chooser(fw, tile=1)
    u.ui.click(u.row(page, 'Transfer Files'))
    assert u.commands[-1] == 'storage_config.sh stop >/dev/null 2>&1'
    assert (u.mode.value, u.link.value, u.saved) == (2, 0, []) and not u.page('usbchoose_page')
    u.unplug()
    assert u.mode.value == 1 and u.toasts == ['Unplugged']


def test_return_on_the_chooser_is_charge_only(fw):
    u, page = chooser(fw)
    u.close(page)
    assert u.commands[-1] == 'storage_config.sh stop >/dev/null 2>&1' and u.link.value == 3  # stock stays out


def test_always_do_this_turns_its_switch_and_saves_the_choice_and_the_mode(fw):
    """Always Do This toggles its switch; on, USB Audio saves the pick (USB_AUDIO, 3) and stock's mode (SYSSET
    USBMODE, Audio's 1). The next cable skips the chooser for stock's own path."""
    u, page = chooser(fw)
    u.ui.click(u.row(page, 'Always Do This'))
    assert u.switch(page, 'Always Do This') == 'switch_on'
    u.ui.click(u.row(page, 'Always Do This'))
    u.ui.click(u.row(page, 'Always Do This'))
    assert u.switch(page, 'Always Do This') == 'switch_on'
    u.ui.click(u.row(page, 'USB Audio'))
    assert u.saved == [('Q2POD', 'USBPICK', 3), ('SYSSET', 'USBMODE', 1)]
    assert (u.mode.value, u.link.value) == (1, 0)
    u.unplug()
    assert u.mode.value == 1  # saved: no cable's mode to put back
    commands = len(u.commands)
    u.plug()
    assert not u.page('usbchoose_page') and u.link.value == 0 and len(u.commands) == commands  # no probe


@pytest.mark.parametrize('tile', [2, 1])
def test_a_remembered_charge_only_holds_whatever_the_mode(fw, tile):
    """Charge Only saved: the cable charges with no probe and no chooser, whatever g_usbmode is (Control Center has
    no USB tile to change it, so an older build's USBPICKMODE no longer drops the choice)."""
    u = Usb(fw, ini={'USBPICK': 1, 'USBPICKMODE': 2}, tile=tile)
    u.plug()
    assert u.link.value == 3 and not u.page('usbchoose_page') and not u.commands


@pytest.mark.parametrize('pick, tile, mode', [(2, 1, 2), (3, 2, 1), (2, 2, 2)])
def test_a_remembered_files_or_audio_sets_its_mode_for_the_cable(fw, pick, tile, mode):
    """Transfer Files (2) or USB Audio (3) saved: stock's own path in the saved choice's mode, whatever g_usbmode was;
    unplugged, g_usbmode is back."""
    u = Usb(fw, ini={'USBPICK': pick}, tile=tile)
    u.plug()
    assert (u.link.value, u.mode.value) == (0, mode) and not u.page('usbchoose_page') and not u.commands
    u.unplug()
    assert u.mode.value == tile


def test_a_release_build_drops_a_remembered_usb_debugging(fw):
    """A release build has no USB Debugging: a remembered one is Automatic, the iPhone first, never adb."""
    u = Usb(fw, ini={'USBPICK': 4, 'USBPICKMODE': 2})
    u.plug()
    assert u.commands == [CAR_START] and not u.page('usbchoose_page') and not u.page('usbdebug_page')


def test_the_mode_page_hides_stock_s_face_and_centre_is_its_stop(fw):
    """usbmode_page as it loads: stock's picture, line and DAC switches hidden, its back arrow unseen but there;
    Centre clicks that arrow, which leaves the mode. USB Audio reached without the chooser toasts USB Audio Ready."""
    u = Usb(fw, tile=1)
    win = u.ui.make(0, 'window', 0, 0, 375, 320, name='usbmode_page')
    parts = {n: u.ui.make(win, 'image', name=n) for n in ('img_usbmode', 'scrlabel_info', 'img_daccharge', 'img_dacset')}
    back = u.ui.make(win, 'image', name='img_return')
    clicks = []
    fw.mock('stock_dispatch_trampoline', lambda w, e, *_: clicks.append(w) or 0)
    fw.mock('pointer_event_init', lambda buf, *_: buf)
    fw.fn('usb_ui_mode_load', None, C.c_void_p)(win)
    assert not any(u.ui[w].visible for w in parts.values()) and u.ui[back].props['_usb_unseen'] == 1
    u.poll()
    assert u.toasts == ['USB Audio Ready']
    key = fw.fn('usb_ui_key', C.c_int, C.c_void_p, C.c_uint)
    assert key(win, O['KEY_RETURN']) == 0 and key(0, O['KEY_CENTER']) == 0
    assert key(win, O['KEY_CENTER']) == 1 and clicks == [back]
    assert u.saved == []  # nothing remembered: nothing to forget
    title = fw.fn('usb_ui_title', C.c_char_p, C.c_char_p)
    assert (title(b'usbmode_page'), title(b'usbdebug_page'), title(b'home_page')) == (b'USB Audio', b'USB Debugging', None)


def mode_page(u):
    win = u.ui.make(0, 'window', 0, 0, 375, 320, name='usbmode_page')
    u.ui.make(win, 'image', name='img_return')
    u.fw.mock('stock_dispatch_trampoline', lambda *_: 0)
    u.fw.mock('pointer_event_init', lambda buf, *_: buf)
    u.fw.fn('usb_ui_mode_load', None, C.c_void_p)(win)
    return win


@pytest.mark.parametrize('pick', [2, 3])
def test_stop_on_a_remembered_mode_forgets_it_and_the_next_cable_is_automatic(fw, pick):
    """Stop (Centre) on Transfer Files' or USB Audio's mode page puts the setting back to Automatic (USBPICK 0), so the
    next cable shows the iPhone first."""
    u = Usb(fw, ini={'USBPICK': pick})
    u.plug()
    win = mode_page(u)
    assert fw.fn('usb_ui_key', C.c_int, C.c_void_p, C.c_uint)(win, O['KEY_CENTER']) == 1
    assert u.saved == [('Q2POD', 'USBPICK', 0)]
    u.unplug()
    u.plug()
    assert u.commands[-1] == CAR_START and not u.page('usbchoose_page')


def test_centre_after_the_computer_ejected_leaves_but_keeps_the_choice(fw):
    """Once the computer ejected the card the page is OK to Disconnect, with no Stop pill: Centre still leaves, and
    the remembered Transfer Files stays."""
    u = Usb(fw, ini={'USBPICK': 2})
    u.plug()
    win = mode_page(u)
    fw.fn('usb_ui_ejected', None, C.c_void_p)(win)
    assert fw.fn('usb_ui_key', C.c_int, C.c_void_p, C.c_uint)(win, O['KEY_CENTER']) == 1
    assert u.saved == []


def stop_ring(u, page, st, touch_mode, light=0):
    """usb_ui_paint over a mode page: Stop's capsule (the house secondary button, 38 px) and whatever lies beside it
    in its rows' band, the focus ring's ends and runs."""
    st('touch_mode').value, st('light').value = touch_mode, light
    c = Canvas(u.fw, char_w=7)
    u.fw.fn('usb_ui_paint', None, C.c_void_p, C.c_void_p)(page, c.addr)
    stop, = [box for box, _, radius in c.boxes if radius == 19]
    x, y, w, h = stop
    ring = [r for r, _ in c.rects if r[1] < y or r[1] >= y + h] + [i for i in c.images if i[1] < y]
    return stop, ring


@pytest.mark.parametrize('light', [0, 1])
@pytest.mark.parametrize('touch_mode, ringed', [(0, True), (1, False)])
def test_the_mode_page_s_stop_is_a_button_ringed_under_the_focus_rule(fw, st, touch_mode, ringed, light):
    """The user's device (2026-11-17): the wheel on a mode page showed nothing on Stop. It is the house secondary
    button (docs/oled-design.md#buttons), ringed while the wheel was the last input, dark and Light Mode alike."""
    u = Usb(fw, tile=1)
    win = mode_page(u)
    stop, ring = stop_ring(u, win, st, touch_mode, light)
    assert stop[3] == 38 and bool(ring) == ringed


def test_the_wheel_on_a_mode_page_shows_stop_s_ring_and_still_reaches_stock(fw, st):
    """A turn after a tap clears touch mode (the one record, docs/oled-design.md#focus-ring) and repaints the page; it
    is not taken, so USB Audio's wheel still sets the volume."""
    u = Usb(fw, tile=1)
    win = mode_page(u)
    st('touch_mode').value = 1
    assert fw.fn('usb_ui_key', C.c_int, C.c_void_p, C.c_uint)(win, O['KEY_NEXT']) == 0
    assert st('touch_mode').value == 0


# --dev builds: USB Debugging and adbd's question.

def test_a_dev_build_offers_usb_debugging_whose_page_stops_the_adb_gadget(fw_dev):
    """Its fourth row opens USB Debugging's mode page over the chooser's place; Centre there is Stop, which parks the
    ADB gadget; Return only leaves."""
    u, page = chooser(fw_dev)
    assert [t for t, _ in u.rows(page)] == DEV_ROWS + ['Always Do This']
    u.ui.click(u.row(page, 'USB Debugging'))
    debug = u.page('usbdebug_page')
    assert debug and not u.page('usbchoose_page')
    event = C.create_string_buffer(0x40)
    C.c_int.from_address(C.addressof(event) + O['EVENT_KEY']).value = O['KEY_CENTER']
    assert u.ui.click(debug, EVT_KEY_UP, C.addressof(event))[-1] == 11  # STOP
    assert u.commands[-1] == '/usr/bin/q2adb-gadget park >/dev/null 2>&1' and not u.page('usbdebug_page')


def debug_key(u, page, key):
    event = C.create_string_buffer(0x40)
    C.c_int.from_address(C.addressof(event) + O['EVENT_KEY']).value = O[key]
    return u.ui.click(page, EVT_KEY_UP, C.addressof(event))


@pytest.mark.parametrize('tile', [2, 1])
def test_a_remembered_usb_debugging_opens_its_page_once_a_computer_is_there(fw_dev, tile):
    """Whatever g_usbmode is: an older build's USBPICKMODE, saved for a tile Control Center no longer shows, is
    ignored."""
    u = Usb(fw_dev, ini={'USBPICK': 4, 'USBPICKMODE': 2}, tile=tile)
    u.plug(host=False)
    # the ADB gadget back, in case Automatic's iPhone parked it on an earlier cable; no iPhone shown
    assert not u.page('usbdebug_page') and u.commands == ['/usr/bin/q2adb-gadget unpark >/dev/null 2>&1']
    u.files.files[UDC] = b'configured\n'
    u.status(100)
    assert u.page('usbdebug_page') and not u.page('usbchoose_page')


@pytest.mark.parametrize('touch_mode, ringed', [(0, True), (1, False)])
def test_usb_debugging_s_stop_is_ringed_under_the_focus_rule_and_the_wheel_shows_it(fw_dev, st, touch_mode, ringed):
    u, page = chooser(fw_dev)
    u.ui.click(u.row(page, 'USB Debugging'))
    debug = u.page('usbdebug_page')
    stop, ring = stop_ring(u, debug, st, touch_mode)
    assert stop[3] == 38 and bool(ring) == ringed
    st('touch_mode').value = 1
    assert fw_dev.fn('usb_ui_key', C.c_int, C.c_void_p, C.c_uint)(debug, O['KEY_PREV']) == 0
    assert st('touch_mode').value == 0


@pytest.mark.parametrize('key, forgets', [('KEY_CENTER', True), ('KEY_RETURN', False)])
def test_stop_on_a_remembered_usb_debugging_forgets_it_and_the_next_cable_is_automatic(fw_dev, key, forgets):
    """Stop (Centre) on USB Debugging's page parks the gadget and puts the setting back to Automatic, so the next
    cable shows the iPhone first; Return only leaves the page and the choice holds."""
    u = Usb(fw_dev, ini={'USBPICK': 4})
    u.plug()
    debug_key(u, u.page('usbdebug_page'), key)
    assert not u.page('usbdebug_page')
    assert u.saved == ([('Q2POD', 'USBPICK', 0)] if forgets else [])
    u.unplug()
    u.plug()
    assert (u.commands[-1] == CAR_START) == forgets and bool(u.page('usbdebug_page')) != forgets


def adb_question(fw_dev):
    """adbd's question on its socket: Allow this computer? asked on the confirmation card (adb_trust.c, feedback.c
    confirm, which opens stock's sortselect_dialog; this harness opens none)."""
    u = Usb(fw_dev)
    u.socket = True
    u.poll()  # connects
    u.inbox.append(b'PK' + KEY.encode() + b' user@host\0')
    u.poll()
    asked = [C.string_at(a[0]) for _, a in fw_dev.calls('navigator_to')]
    assert b'dialog/sortselect_dialog' in asked
    return u


def test_adbd_s_socket_is_a_mips_stream_socket(fw_dev):
    """MIPS Linux numbers SOCK_DGRAM 1 and SOCK_STREAM 2: AF_UNIX (1) with type 1 made a datagram socket that never
    reached adbd's listener, so no prompt ever appeared and every new computer stayed unauthorized."""
    u = Usb(fw_dev)
    u.poll()
    assert u.sockets == [(1, 2)]


def test_adbd_s_question_asks_on_the_card_and_no_card_shown_answers_no(fw_dev):
    """docs/adb.md#authorization: "PK" and the key from adbd's socket ask on adb_trust.c's card (its answers are
    test_adb_trust.py's); with no card shown (one already up, as here) it answers NO, so the computer asks again."""
    u = adb_question(fw_dev)
    assert u.sent == ['NO']


def test_adbd_going_away_answers_nothing_more(fw_dev):
    u = adb_question(fw_dev)
    u.sent.clear()
    fw_dev.mock('fw_recv', lambda *_: 0)  # the socket closed
    u.poll()
    assert not u.sent


# The cable's edges (patch/usb_event.c, docs/internals.md#usb-cable-events) and the probe's gadget at an unplug.

def edges(fw, *stamps):
    """usb_event.c's thread for one burst: the kernel's line events (struct gpioevent_data: the u64 timestamp in ns,
    the id) read from the line's file until it has none, the bounce slept off in between."""
    queue = [s for s in stamps]
    sleeps = []

    def read(fd, buf, n, *_):
        if not queue:
            return -1
        C.memmove(buf, C.c_uint64(queue.pop(0)).value.to_bytes(8, 'little') + b'\x01\0\0\0\0\0\0\0', 16)
        return 16
    fw.mock('fw_read', read)
    fw.mock('fw_usleep', lambda us, *_: sleeps.append(us) or 0)
    assert fw.fn('usb_event_wait', C.c_int, C.c_int)(5) == 1
    return sleeps


def test_a_burst_of_edges_is_one_cable_change_stamped_with_its_first_edge(fw):
    """The thread reads an edge, sleeps the contacts' bounce off (USB_SETTLE_MS), drains the rest of the burst and
    counts it once; the UI thread takes it once, with the first edge's time."""
    take = fw.fn('usb_event_take', C.c_int, C.POINTER(C.c_uint64))
    edge = C.c_uint64()
    assert not take(C.byref(edge))
    assert edges(fw, 7_000_000, 7_400_000, 9_000_000) == [20000]
    assert take(C.byref(edge)) == 1 and edge.value == 7_000_000
    assert not take(C.byref(edge))


def test_an_edge_runs_stock_s_cable_read_and_the_usb_check_in_the_same_pass(fw):
    """usb_ui_poll, every UI pass: an edge runs get_battery_capacity (which sets g_usbdet_value, as the status bar's
    one-second timer would) and check_usb_status's hook at once, so the probe starts in that pass with no
    usb_ui_status from stock; the unified log says how the cable was seen and how long after its edge."""
    u = Usb(fw)
    u.status()  # the first pass: no cable
    fw.mock('get_battery_capacity', lambda *_: setattr(u.det, 'value', 1) or 0)
    fw.mock('stock_usb_status_trampoline', lambda *_: 0)
    fw.mock('fw_clock_gettime', lambda clock, ts, *_: C.memmove(ts, bytes(C.c_long(1)) + bytes(C.c_long(9_000_000)), 2 * C.sizeof(C.c_long)) and 0)
    fw.mock('fw_time', lambda *_: 1794926102)  # 2026-11-17T14:35:02Z
    logged = []
    fw.mock('fw_write', lambda fd, data, n, *_: logged.append((fd, C.string_at(data, n).decode())) or n)
    edges(fw, 1_000_000_000)
    u.poll()
    assert u.link.value == 3 and u.commands == ['storage_config.sh start /dev/null >/dev/null 2>&1']
    # a unified log line (ulog.c) on stderr: the time, the uptime in ms, the level and the source
    # (usb_record.c's line of the edge, its pins, comes first, through the same ulog)
    assert logged[0][1].startswith('2026-11-17T14:35:02Z 1009 I usb in PD18=')
    assert logged[1:] == [(2, '2026-11-17T14:35:02Z 1009 I usb cable in, seen after its edge, 9 ms\n')]


def test_the_waiting_probe_reads_the_computer_s_answer_every_tenth_of_a_second(fw):
    """The chooser follows the computer's answer, not stock's next second: usb_ui_poll looks at the UDC's state every
    USB_PROBE_LOOK_MS while the probe waits."""
    u = Usb(fw)
    u.plug(host=False)
    u.files.files[UDC] = b'configured\n'
    u.now += 50
    u.poll()
    assert not u.page('usbchoose_page')  # under USB_PROBE_LOOK_MS since the last look
    u.now += 50
    u.poll()
    assert u.page('usbchoose_page')


@pytest.mark.parametrize('answered', [False, True])
def test_unplugging_stops_the_probe_s_gadget_whether_it_waited_or_its_chooser_was_up(fw, answered):
    """Seen on a Q2 (p31, 2026-11-16): a cable out with the chooser up and unanswered left the probe's storage gadget
    running, configfs mounted and the ADB gadget parked, so the next cable's probe could not start. The probe's
    gadget now goes with the cable, once."""
    u = Usb(fw)
    u.plug(host=answered)
    assert bool(u.page('usbchoose_page')) == answered
    u.unplug()
    assert u.commands == ['storage_config.sh start /dev/null >/dev/null 2>&1', 'storage_config.sh stop >/dev/null 2>&1']
    assert not u.page('usbchoose_page')
    u.link.value = 0  # stock's pass at the cable out (check_usb_status: "=====usb disconnect==")
    u.plug(host=False)
    assert u.commands[-1] == 'storage_config.sh start /dev/null >/dev/null 2>&1'


def test_a_choice_then_an_unplug_stops_the_probe_only_once(fw):
    u, page = chooser(fw)
    u.ui.click(u.row(page, 'Charge Only'))
    u.unplug()
    assert u.commands.count('storage_config.sh stop >/dev/null 2>&1') == 1


def test_a_dev_build_probes_with_the_adb_gadget_already_on_the_controller(fw_dev):
    """The ADB gadget bound when the cable comes is the probe: a computer configuring it gets the chooser with no
    storage gadget swapped in, so USB Debugging and Charge Only keep the one enumeration the plug made."""
    u = Usb(fw_dev)
    u.files.files['/sys/kernel/config/usb_gadget/q2adb/UDC'] = b'13500000.otg\n'
    page = (u.plug(), u.page('usbchoose_page'))[1]
    assert page and not u.commands
    u.ui.click(u.row(page, 'USB Debugging'))
    assert u.page('usbdebug_page') and not u.commands
    u.unplug()
    assert not u.commands


def test_a_dev_build_whose_adb_gadget_is_not_back_yet_probes_with_storage(fw_dev):
    u = Usb(fw_dev)
    u.files.files['/sys/kernel/config/usb_gadget/q2adb/UDC'] = b'\n'
    u.plug(host=False)
    assert u.commands == ['storage_config.sh start /dev/null >/dev/null 2>&1']


# The USB picker (usb_pick_open): Settings > General > USB and Control Center's USB row, the remembered choice.

PICK = ['Automatic', 'Transfer Files', 'USB Audio', 'Charge Only', 'Ask Every Time']
DEV_PICK = PICK + ['USB Debugging']
SORTSELECT = b'dialog/sortselect_dialog'  # the confirmation card's dialog (feedback.c confirm)


def picker(u):
    assert u.fw.fn('usb_pick_open', C.c_int, C.c_void_p, C.c_void_p)(None, None) == 0
    page = u.page('usbpick_page')
    assert page
    return page


def checked(u, page):
    """The rows holding stock's select image, which the theme paints as the picker's check (row_paint.c paint_radio)
    and the wheel opens on (contexts.inc CHOICE)."""
    return [t for t, row in u.rows(page) if any(u.ui[i].image == 'select' for i in u.ui.of('image', row))]


def value(fw):
    return fw.fn('usb_ui_value', C.c_char_p)().decode()


def asked(fw):
    return [C.string_at(a[0]) for _, a in fw.calls('navigator_to')].count(SORTSELECT)


@pytest.mark.parametrize('pick, row', [(0, 'Automatic'), (1, 'Charge Only'), (2, 'Transfer Files'),
                                       (3, 'USB Audio'), (5, 'Ask Every Time')])
def test_the_picker_lists_the_choices_and_checks_the_remembered_one(fw, pick, row):
    u = Usb(fw, ini={'USBPICK': pick})
    page = picker(u)
    assert [t for t, _ in u.rows(page)] == PICK and checked(u, page) == [row] and value(fw) == row


def test_a_dev_build_s_picker_offers_usb_debugging(fw_dev):
    u = Usb(fw_dev, ini={'USBPICK': 4})
    page = picker(u)
    assert [t for t, _ in u.rows(page)] == DEV_PICK and checked(u, page) == ['USB Debugging']


def test_a_release_build_reads_a_remembered_usb_debugging_as_automatic(fw):
    u = Usb(fw, ini={'USBPICK': 4})
    assert checked(u, picker(u)) == ['Automatic'] and value(fw) == 'Automatic'


@pytest.mark.parametrize('pick, lines', [
    (0, ['A car plays the Q2 through its speakers.', 'A computer opens the SD card.']),
    (5, ['Plug into a computer to choose.']),
    (4, ['A computer gets adb on every cable.', 'Stop on its page goes back to Automatic.'])])
def test_the_footer_says_what_the_checked_choice_does(fw_dev, st, pick, lines):
    """docs/oled-design.md#usb: the checked choice's note under the list, 13 px Text Tertiary on the text column, its
    lines 17 px apart."""
    u = Usb(fw_dev, ini={'USBPICK': pick})
    foot = u.ui.lookup(picker(u), '_usb_pick_foot')
    c = Canvas(fw_dev)
    fw_dev.fn('usb_ui_paint', None, C.c_void_p, C.c_void_p)(foot, c.addr)
    assert [t for t, _, _ in c.texts] == lines
    ys = [box[1] + box[3] / 2 for _, box, _ in c.texts]
    assert ys[0] == 19 and all(b - a == 17 for a, b in zip(ys, ys[1:]))



def test_a_swipe_back_takes_the_footer_along_with_the_page(fw_dev, st):
    """docs/internals.md#swipe-back: the swipe moves the picker's window (widget_move), and every canvas in it starts
    that far right. The footer's note is on the page's text column and rides with it; at its screen column it stood
    still over the page uncovered under it (seen on the device)."""
    u = Usb(fw_dev, ini={'USBPICK': 0})
    page = picker(u)
    foot = u.ui.lookup(page, '_usb_pick_foot')
    paint = fw_dev.fn('usb_ui_paint', None, C.c_void_p, C.c_void_p)
    rest = Canvas(fw_dev)
    paint(foot, rest.addr)
    off = 120
    C.c_void_p.from_address(page + O['W_PARENT']).value = u.ui.wm  # a window is the window manager's child
    u.ui.box(page, off, *u.ui.at(page)[1:])  # where swipe_back.c's place() puts it
    moved = Canvas(fw_dev, x=off)
    paint(foot, moved.addr)
    assert moved.texts and [box for _, box, _ in moved.texts] == [box for _, box, _ in rest.texts]

def test_a_pick_with_no_cable_only_saves_it_and_ask_every_time_undoes_a_saved_charge_only(fw):
    """The gap feat/usb-mode-row left: a saved Charge Only had no way back. With no cable in a pick is only saved
    (Transfer Files with stock's mode, as Always Do This saves it); the link, which stock's USB-mode exit prompt may
    still hold at 2, is left alone. Ask Every Time saves USBPICK 5, and the next cable gets the chooser."""
    u = Usb(fw, ini={'USBPICK': 1}, tile=1)
    u.link.value = 2
    page = picker(u)
    u.ui.click(u.row(page, 'Transfer Files'))
    assert u.saved == [('Q2POD', 'USBPICK', 2), ('SYSSET', 'USBMODE', 2)]
    assert checked(u, page) == ['Transfer Files'] and u.page('usbpick_page') and (u.link.value, u.commands) == (2, [])
    u.ui.click(u.row(page, 'Transfer Files'))  # the checked one again: nothing
    u.ui.click(u.row(page, 'Ask Every Time'))
    assert u.saved[2:] == [('Q2POD', 'USBPICK', 5)] and checked(u, page) == ['Ask Every Time']
    assert value(fw) == 'Ask Every Time' and not asked(fw)
    u.link.value = 0
    u.plug()
    assert u.page('usbchoose_page')


def test_a_pick_while_a_cable_charges_switches_it_now(fw):
    """A remembered Charge Only holds the cable (g_usblink_status 3); USB Audio picked then is saved and handed to
    stock at once, in its mode."""
    u = Usb(fw, ini={'USBPICK': 1}, tile=2)
    u.plug()
    assert u.link.value == 3
    u.ui.click(u.row(picker(u), 'USB Audio'))
    assert u.saved == [('Q2POD', 'USBPICK', 3), ('SYSSET', 'USBMODE', 1)] and (u.link.value, u.mode.value) == (0, 1)
    assert not asked(fw)


def test_leaving_usb_audio_for_charge_only_closes_stock_s_mode_at_once(fw):
    """Stock's mode is left by closing its window, whose EVT_WINDOW_CLOSE runs stock's usbmode_exit (the gadget
    stopped, g_usblink_status 3). Only Transfer Files asks first."""
    u = Usb(fw, ini={'USBPICK': 3})
    u.plug()
    mode_page(u)
    u.ui.click(u.row(picker(u), 'Charge Only'))
    assert not u.page('usbmode_page') and u.saved == [('Q2POD', 'USBPICK', 1)] and not asked(fw)


def files_mounted(fw, ejected=False):
    u = Usb(fw, ini={'USBPICK': 2})
    u.plug()
    win = mode_page(u)
    if ejected:
        fw.fn('usb_ui_ejected', None, C.c_void_p)(win)
    return u, picker(u)


def pick_answer(fw, row):
    fw.fn('usb_ui__usb_pick_answer', None, C.c_void_p, C.c_int, C.c_int)(None, row, 0)


@pytest.mark.parametrize('row', [0, 1])
def test_leaving_transfer_files_while_the_computer_has_the_card_asks_first(fw, row):
    """A copy in progress is not cut off unasked: USB Audio picked while Transfer Files' page is up and the card not
    ejected asks on the confirmation card, and nothing changes until it answers. Cancel (row 0) leaves it all;
    Switch (row 1) leaves stock's mode and hands the cable to stock again as USB Audio."""
    u, page = files_mounted(fw)
    u.ui.click(u.row(page, 'USB Audio'))
    assert asked(fw) == 1 and u.page('usbmode_page') and u.saved == [] and checked(u, page) == ['Transfer Files']
    pick_answer(fw, row)
    if not row:
        assert u.page('usbmode_page') and u.saved == [] and checked(u, page) == ['Transfer Files']
        return
    assert not u.page('usbmode_page') and u.saved == [('Q2POD', 'USBPICK', 3), ('SYSSET', 'USBMODE', 1)]
    assert (u.link.value, u.mode.value) == (0, 1) and checked(u, page) == ['USB Audio']


def test_once_ejected_transfer_files_is_left_without_asking(fw):
    u, page = files_mounted(fw, ejected=True)
    u.ui.click(u.row(page, 'Charge Only'))
    assert not asked(fw) and not u.page('usbmode_page') and u.saved == [('Q2POD', 'USBPICK', 1)]


def test_ask_every_time_while_transfer_files_is_up_only_saves_it(fw):
    u, page = files_mounted(fw)
    u.ui.click(u.row(page, 'Ask Every Time'))
    assert not asked(fw) and u.page('usbmode_page') and u.saved == [('Q2POD', 'USBPICK', 5)]


def test_a_dev_build_switches_debugging_off_and_on(fw_dev):
    """From USB Debugging's page, Charge Only closes it and parks the ADB gadget; USB Debugging again unparks it and
    opens the page."""
    u = Usb(fw_dev, ini={'USBPICK': 4})
    u.plug()
    assert u.page('usbdebug_page')
    page = picker(u)
    u.ui.click(u.row(page, 'Charge Only'))
    assert not u.page('usbdebug_page') and u.commands[-1] == '/usr/bin/q2adb-gadget park >/dev/null 2>&1'
    u.ui.click(u.row(page, 'USB Debugging'))
    assert u.page('usbdebug_page') and u.commands[-1] == '/usr/bin/q2adb-gadget unpark >/dev/null 2>&1'
    assert u.saved == [('Q2POD', 'USBPICK', 1), ('Q2POD', 'USBPICK', 4)]


def test_the_cable_is_what_stock_reads(fw):
    u = Usb(fw)
    cable = fw.fn('usb_ui_cable', C.c_int)
    assert cable() == 0
    u.det.value = 1
    assert cable() == 1


# A cable in starts one Bluetooth connect to the saved device (headset.c headset_bt_usb,
# docs/internals.md#bluetooth-on-usb-power). The car drive of 2026-11-20: the car ended the link at its switch-off
# (reason 3, which BlueZ does not retry), and at the next start Bluetooth waited for the car to page the Q2 itself,
# about 4.5 minutes after the car's USB enumerated it.

def usb_power(fw, ini=None, since_on=30000):
    """usb_ui.c and headset.c with test_bt_trace's Bluetooth: on, its stack up, nothing linked, the car saved and
    paired, Bluetooth on since_on ms before. autoLinkBluetooth stands in for stock's: its checkPairDev site
    (BT_AUTOLINK) is headset_bt_autolink, and a yes starts the link thread to the car."""
    from test_bt_trace import CAR, Rig
    u = Usb(fw, ini=ini)
    r = Rig(fw)
    r.set(init=0, conn=0)
    fw.mock('checkPairDev', lambda *_: 1)
    starts = []

    def auto(*_):
        if fw.fn('headset_bt_autolink', C.c_int, C.c_char_p)(CAR.encode()):
            starts.append(CAR)
            r.links = [(CAR, 1)]
            r.set(conn=1)
        return 1
    fw.mock('autoLinkBluetooth', auto)
    fw.fn('headset_status_register', None)()  # Bluetooth on
    r.now += since_on
    u.status()  # a pass with no cable: the next one in is a plug-in
    r.poll()
    r.said()
    return u, r, starts


def test_a_cable_in_starts_one_connect_to_the_saved_device_logged_as_usb(fw):
    from test_bt_trace import CAR
    u, r, starts = usb_power(fw)
    u.plug(host=False)
    assert starts == [CAR]
    r.poll()
    assert ('I', f'connect starts to {CAR} why=usb') in r.said()
    for _ in range(5):  # the cable stays in and the thread ends: nothing pages again
        r.now += 5000
        r.set(conn=0)
        u.status()
        u.poll()
    assert starts == [CAR]


@pytest.mark.parametrize('case', ['connected', 'bluetooth off', 'stack starting', 'link thread running',
                                  'no saved device', 'usb audio', 'otg bus power', 'just switched on'])
def test_a_cable_in_starts_no_connect(fw, case):
    from test_bt_trace import BUDS
    u, r, starts = usb_power(fw, ini={'USBPICK': 3} if case == 'usb audio' else None,
                             since_on=5000 if case == 'just switched on' else 30000)
    if case == 'connected':
        r.links = [(BUDS, 3)]
    elif case == 'bluetooth off':
        r.set(on=0)
    elif case == 'stack starting':
        r.set(init=101)
    elif case == 'link thread running':
        r.set(conn=1)
    elif case == 'no saved device':
        r.set(saved='')
    elif case == 'otg bus power':
        fw.var('g_last_otgid', C.c_byte).value = 1
    u.plug(host=False)
    assert starts == []


def own_bus_power(fw, u, r):
    """The Q2 a host for an accessory, through usb_otg.c's real ringnav_otg_status over stock's pass (PD18 low: bus
    power on, as test_usb_otg.py's model has it), then the accessory pulled (PD18 high: off)."""
    otgid = fw.var('g_last_otgid', C.c_byte)
    pins = {'PD18': 0, 'PD17': 1}
    fw.mock('toolsGpioGetValue', lambda name, *_: pins.get(C.string_at(name).decode(), 1))
    fw.mock('toolsGpioSetValue', lambda *_: 0)

    gpio = fw.fn('ringnav_gpio_value', C.c_int, C.c_char_p)
    fw.mock('stock_gpio_value_trampoline', lambda name, *_: pins.get(C.string_at(name).decode(), 1))

    def stock(*_):  # its PD18 read through toolsGpioGetValue's hook
        otgid.value = int(gpio(b'PD18') == 0)
        return 0
    fw.mock('stock_otg_status_trampoline', stock)
    u.files.files['/sys/bus/usb/devices/1-1/idVendor'] = b'3302\n'  # AB13X USB Audio, kept powered
    guard = fw.fn('ringnav_otg_status', C.c_int)
    for _ in range(8):
        r.now += 1000
        guard()
    assert otgid.value == 1
    pins['PD18'] = 1
    del u.files.files['/sys/bus/usb/devices/1-1/idVendor']  # pulled: off the host port too
    r.now += 1000
    guard()
    assert otgid.value == 0


@pytest.mark.parametrize('after_ms, connects', [(1500, False), (4000, True)])
def test_the_q2_s_own_bus_power_ending_is_no_cable_for_bluetooth(fw, after_ms, connects):
    """2026-11-23: earbuds on the Q2's own bus power pulled at 13404.0, and 1.6 s later stock's read took the dying VBUS
    for a cable (13405.6 "cable in", out again 0.2 s on) and Bluetooth connected for it (why=usb). A real cable after
    OTG_OWN_VBUS_MS still connects."""
    from test_bt_trace import CAR
    u, r, starts = usb_power(fw)
    own_bus_power(fw, u, r)
    r.now += after_ms
    u.plug(host=False)
    assert starts == ([CAR] if connects else [])
