"""The USB pages (patch/usb_ui.c, docs/internals.md#usb-pages): a cable's probe and the chooser, Always Do This and the
remembered choice, the mode pages and their toasts, and in a --dev build USB Debugging and adbd's question (U5)."""
import base64
import ctypes as C
import hashlib

import pytest

from conftest import O, Files
from test_coverflow import Ui, themed

UDC = '/sys/class/udc/13500000.otg/state'
EVT_DESTROY, EVT_KEY_UP = O['EVT_DESTROY'], O['EVT_KEY_UP']
ROWS = ['Charge Only', 'Transfer Files', 'USB Audio']
DEV_ROWS = ROWS + ['USB Debugging']
KEY = 'QUFBQUIzTnphQzF5YzJF'  # adbd's public key line, base64 (its comment after a space)


class Usb:
    """usb_ui.c over test_coverflow's widgets: stock's USB globals, config.ini's [Q2POD] and [SYSSET] keys, the
    commands run, the UDC's state file, the clock, the toasts and adbd's socket."""
    def __init__(self, fw, ini=None, tile=2):
        themed(fw)
        self.fw, self.ui = fw, Ui(fw)
        # AWTK's widget_on refuses a null handler (emitter_on's return_value_if_fail), as Allow's page_row_detail gives
        fw.mock('widget_on', lambda w, evt, fn, ctx, *_: (self.ui[w].on.append((evt, fn, ctx)) or len(self.ui[w].on)) if fn else 0)
        self.ini, self.saved, self.commands, self.toasts, self.sent = dict(ini or {}), [], [], [], []
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
        fw.mock('fw_socket', lambda *_: 7)
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
        self.det.value = 0
        self.files.files.pop(UDC, None)
        self.status()
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


def test_always_do_this_turns_its_switch_and_saves_the_choice_and_the_tile(fw):
    """Always Do This toggles its switch; on, USB Audio saves the pick (USB_AUDIO, 3), the tile's mode (SYSSET
    USBMODE, Audio's 1) and the mode it was saved with. The next cable skips the chooser for stock's own path."""
    u, page = chooser(fw)
    u.ui.click(u.row(page, 'Always Do This'))
    assert u.switch(page, 'Always Do This') == 'switch_on'
    u.ui.click(u.row(page, 'Always Do This'))
    u.ui.click(u.row(page, 'Always Do This'))
    assert u.switch(page, 'Always Do This') == 'switch_on'
    u.ui.click(u.row(page, 'USB Audio'))
    assert u.saved == [('Q2POD', 'USBPICK', 3), ('SYSSET', 'USBMODE', 1), ('Q2POD', 'USBPICKMODE', 1)]
    assert (u.mode.value, u.link.value) == (1, 0)
    u.unplug()
    assert u.mode.value == 1  # saved: no cable's mode to put back
    commands = len(u.commands)
    u.plug()
    assert not u.page('usbchoose_page') and u.link.value == 0 and len(u.commands) == commands  # no probe


@pytest.mark.parametrize('tile, asks', [(2, False), (1, True)])
def test_a_remembered_charge_only_holds_until_the_tile_changes(fw, tile, asks):
    """Charge Only saved with the tile on Files (2): kept while the tile is, so the cable charges with no probe;
    once the tile changes the chooser asks again."""
    u = Usb(fw, ini={'USBPICK': 1, 'USBPICKMODE': 2}, tile=tile)
    u.plug()
    assert u.link.value == 3 and bool(u.page('usbchoose_page')) == asks
    assert bool(u.commands) == asks


def test_a_release_build_drops_a_remembered_usb_debugging(fw):
    u = Usb(fw, ini={'USBPICK': 4, 'USBPICKMODE': 2})
    u.plug()
    assert [t for t, _ in u.rows(u.page('usbchoose_page'))] == ROWS + ['Always Do This']


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
    title = fw.fn('usb_ui_title', C.c_char_p, C.c_char_p)
    assert (title(b'usbmode_page'), title(b'usbdebug_page'), title(b'home_page')) == (b'USB Audio', b'USB Debugging', None)


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


def test_a_remembered_usb_debugging_opens_its_page_once_a_computer_is_there(fw_dev):
    u = Usb(fw_dev, ini={'USBPICK': 4, 'USBPICKMODE': 2})
    u.plug(host=False)
    assert not u.page('usbdebug_page') and not u.commands
    u.files.files[UDC] = b'configured\n'
    u.status(100)
    assert u.page('usbdebug_page') and not u.page('usbchoose_page')


def adb_question(fw_dev):
    """adbd's question on its socket: Allow USB Debugging? asked on the confirmation card (feedback.c confirm, which
    opens stock's sortselect_dialog; this harness opens none, so the card's answer is called as feedback.c would)."""
    u = Usb(fw_dev)
    u.socket = True
    u.poll()  # connects
    u.inbox.append(b'PK' + KEY.encode() + b' user@host\0')
    u.poll()
    asked = [C.string_at(a[0]) for _, a in fw_dev.calls('navigator_to')]
    assert b'dialog/sortselect_dialog' in asked
    return u


def card_answer(fw_dev, row, always):
    fw_dev.fn('usb_ui__auth_card_answer', None, C.c_void_p, C.c_int, C.c_int)(None, row, always)


def test_adbd_s_question_asks_on_the_card_with_the_key_s_fingerprint(fw_dev):
    """docs/adb.md#authorization: "PK" and the key from adbd's socket ask Allow USB Debugging? on the card, its body the
    key's fingerprint (the MD5 of the decoded key's first eight bytes) and what allowing gives; with no card shown (one
    already up, as here) it answers NO, so the computer asks again."""
    u = adb_question(fw_dev)
    fingerprint = ':'.join(f'{b:02X}' for b in hashlib.md5(base64.b64decode(KEY)).digest()[:8])
    body = C.string_at(C.addressof(fw_dev.var('usb_ui__auth_body', C.c_char * 64))).decode()
    assert body == f'Key {fingerprint}\nIt gets full control of the Q2.' and u.sent == ['NO']


@pytest.mark.parametrize('row, always, answer, kept', [(0, 0, 'NO', None), (1, 0, 'OK', None), (1, 1, 'OK', KEY),
                                                       (0, 1, 'NO', None)])
def test_the_card_answers_adbd_and_always_allow_keeps_the_key(fw_dev, row, always, answer, kept):
    """Cancel (or Return, its back row) answers NO; Allow answers OK; Allow with Always Allow on first appends the key's
    line to /mnt/data/adb_keys, which adbd reads before it lets the computer in."""
    u = adb_question(fw_dev)
    u.sent.clear()
    card_answer(fw_dev, row, always)
    assert u.sent == [answer]
    assert u.files.files.get('/mnt/data/adb_keys') == (None if kept is None else f'{kept} user@host\n'.encode())


def test_adbd_going_away_answers_nothing_more(fw_dev):
    u = adb_question(fw_dev)
    u.sent.clear()
    fw_dev.mock('fw_recv', lambda *_: 0)  # the socket closed
    u.poll()
    assert not u.sent
