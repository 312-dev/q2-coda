"""bt_ui.c: Settings > Bluetooth, devices before settings. The switch, Name, My Devices and Other Devices with their
rows, then the Audio group (Guest Play, Quality, Headset Volume, Resume on Connect, Link Volume); stock's Scan now row gone, a scan
started by the page itself while it is open; the connected device a two-line row ("Connected · LDAC · 72%")."""
import ctypes as C
import os
import subprocess

import pytest

from conftest import Files
from test_nav_library_pages import Config, Ui

EVT_CLICK = 0x10c
CLASS_FILE = '/tmp/q2-btclass'
BATT_FILE = '/tmp/q2-btbatt'
HEAD = 'AA:BB:CC:DD:EE:01'
CAR = 'AA:BB:CC:DD:EE:02'
SPEAKER = 'AA:BB:CC:DD:EE:03'


class Page:
    """Settings > Bluetooth as stock's btmenu_rows builds it: five head rows s0-s4 (Bluetooth on) or the switch,
    then My Devices' header and the paired devices, Other Devices' header and the found ones, each device row its
    button named by its index in pdeq_btshowlist, holding stock's icon, name and state labels; then ringnav_btrows."""
    def __init__(self, fw, devices=(), on=1, files=None, **config):
        self.fw, self.ui, self.devices = fw, Ui(fw), list(devices)
        self.config = Config(fw, **config)
        self.files = Files(fw, dict(files or {}))
        self.commands, self.now, self.links = [], 1000, []
        fw.mock('fw_system', lambda cmd, *_: self.commands.append(C.string_at(cmd).decode()) or 0)
        fw.mock('time_now_ms', lambda *_: self.now)
        fw.mock('getBtLinkSize', lambda *_: len(self.links))
        fw.mock('getBtLinkItem', self.link_item)
        self.records = [C.create_string_buffer(0x124) for _ in self.devices]
        for record, (mac, name, _) in zip(self.records, self.devices):
            C.memmove(C.addressof(record) + 4, mac.encode() + b'\0', len(mac) + 1)
            C.memmove(C.addressof(record) + 0x24, name.encode() + b'\0', len(name) + 1)
        fw.var('fw_ctx_pdeq_btshowlist', C.c_uint).value = self.ui.deques.new(C.addressof(r) for r in self.records)
        fw.var('g_bluetoothflag', C.c_ubyte).value = on
        self.win = self.ui.new('window_create', 0, 0, 0, 0, 0)
        self.ui.name[self.win] = 'bluetooth_page'
        self.view = self.ui.new('scroll_view_create', self.win, 0, 26, 375, 294)
        self.ui.name[self.view] = 'scroll_view_bluetooth'
        fw.mock('stock_btrows_trampoline', self.build)
        self.rebuild()

    def link_item(self, i, status, mac, *_):
        m, s = self.links[i]
        C.c_int.from_address(status).value = s
        C.memmove(mac, m.encode() + b'\0', len(m) + 1)
        return 0

    def build(self, *_):
        ui, view = self.ui, self.view
        for i in range(5 if self.fw.var('g_bluetoothflag', C.c_ubyte).value else 1):
            item = ui.new('list_item_create', view, 0, 0, 375, 78)
            ui.name[item] = f's{i}'
            b = ui.new('button_create', item, 20, 0, 335, 78)
            ui.name[b] = str(i)
            if i == 2:  # Quality over its choice, a second line (btmenu_setting_row_build)
                ui.text[ui.new('hscroll_label_create', b, 10, 0, 266, 35)] = 'Quality'
                ui.text[ui.new('hscroll_label_create', b, 10, 35, 266, 35)] = 'LDAC Auto'
        if not self.fw.var('g_bluetoothflag', C.c_ubyte).value:
            return 0
        paired = [i for i, d in enumerate(self.devices) if d[2] != 'found']
        found = [i for i, d in enumerate(self.devices) if d[2] == 'found']
        for key, rows in (('bt_paireddev', paired), ('bt_compatiabledev', found)):
            if not rows:
                continue
            item = ui.new('list_item_create', view, 0, 0, 375, 60)
            label = ui.new('label_create', item, 0, 18, 375, 24)
            ui.props[(label, 'tr_text')] = key
            for i in rows:
                item = ui.new('list_item_create', view, 0, 0, 375, 78)
                ui.name[item] = f'd{i}'
                b = ui.new('button_create', item, 20, 0, 335, 78)
                ui.name[b] = str(i)
                ui.on(b, EVT_CLICK, 0x4c43a4, b)
                ui.image[ui.new('image_create', b, 0, 0, 50, 70)] = 'bt_lefticon'
                ui.text[ui.new('hscroll_label_create', b, 52, 12, 266, 32)] = self.devices[i][1]
                state = self.devices[i][2]
                if state != 'found':
                    ui.props[(ui.new('hscroll_label_create', b, 52, 44, 266, 26), 'tr_text')] = f'msg_{state}'
                    ui.image[ui.new('image_create', b, 276, 0, 50, 70)] = 'navbar_delete'
        return 0

    def rebuild(self):
        for k in list(self.ui.kids[self.view]):
            self.ui.destroy(k)
        self.fw.fn('ringnav_btrows', C.c_int, C.c_void_p, C.c_void_p)(self.win, None)

    def layout(self):
        self.fw.fn('bt_rows_pre', None, C.c_void_p)(self.view)
        self.fw.fn('bt_rows_post', None, C.c_void_p)(self.view)

    def rows(self):
        return self.ui.kids[self.view]

    def names(self):
        """Each row: stock's name, a header's text, or a payload row's title."""
        out = []
        for r in self.rows():
            head = self.ui.lookup(r, '_bt_head', 0)
            if head:
                out.append(self.ui.text[head])
            elif self.ui.name.get(r):
                out.append(self.ui.name[r])
            else:
                out.append(next((self.ui.text[w] for w in self.ui.subtree(r) if w in self.ui.text), None))
        return out

    def row(self, name):
        return next(r for r in self.rows() if self.ui.name.get(r) == name)

    def box(self, w):
        return C.c_int * 4

    def poll(self, ms=500):
        self.now += ms
        self.fw.fn('bt_ui_poll', None)()


def geometry(w):
    return tuple(C.c_int.from_address(w + 4 * i).value for i in range(4))


DEVICES = [(HEAD, 'WH-1000XM5', 'connected'), (CAR, 'BMW 6R81', 'paired'), (SPEAKER, 'JBL Flip 6', 'found')]


def test_devices_come_before_the_audio_group_and_scan_now_goes(fw):
    p = Page(fw, DEVICES)
    assert p.names() == ['s0', 'Name', 'My Devices', 'd0', 'd1', 'Other Devices', 'd2', 'Audio', 'Guest Play', 's2', 's3',
                         'Resume on Connect', 'Link Volume']
    assert 's4' not in p.names()  # Scan now


def test_other_devices_shows_with_nothing_found_yet_and_carries_the_spinner(fw):
    p = Page(fw, DEVICES[:2])
    names = p.names()
    assert names[names.index('Other Devices') + 1] == 'Audio'
    head = p.rows()[names.index('Other Devices')]
    assert p.ui.lookup(head, '_bt_headspin', 0)
    assert p.ui.lookup(p.rows()[names.index('My Devices')], '_bt_headspin', 0) == 0


def test_no_devices_still_has_both_lists_headers(fw):
    p = Page(fw)
    assert p.names() == ['s0', 'Name', 'Other Devices', 'Audio', 'Guest Play', 's2', 's3', 'Resume on Connect', 'Link Volume']


def test_bluetooth_off_keeps_name_and_says_what_on_gives(fw):
    p = Page(fw, DEVICES, on=0)
    assert p.names() == ['s0', 'Name', 'Turn on to play to headphones, a car or a speaker.']


def test_headers_are_whole_rows_the_wheel_passes(fw):
    p = Page(fw, DEVICES)
    p.layout()
    for name in ('My Devices', 'Other Devices', 'Audio'):
        r = p.rows()[p.names().index(name)]
        assert geometry(r)[3] == 32 and p.ui.props[(r, '_bt_head')] == 1
    assert [a[1] & 0xff for _, a in fw.calls('widget_set_enable') if a[0] in p.rows()].count(0) == 3


def test_the_connected_row_is_two_lines_with_codec_and_battery(fw):
    p = Page(fw, DEVICES, files={BATT_FILE: f'{HEAD} 72\n'.encode()})
    fw.var('fw_ctx_bt_showcoding', C.c_int).value = 4  # LDAC
    p.links = [(HEAD, 3)]
    fw.fn('bt_headset_battery', C.c_int)()  # its loop starts; the file answers on the next read
    p.now += 6000
    p.layout()
    row = p.row('d0')
    texts = [p.ui.text.get(w) for w in p.ui.subtree(row) if p.ui.kind[w] == 'hscroll_label_create']
    assert geometry(row)[3] == 52 and texts == ['WH-1000XM5', 'Connected · LDAC · 72%']
    assert p.ui.lookup(row, '_bt_dot', 1) and p.ui.lookup(row, '_bt_chevron', 1)
    assert 'navbar_delete' not in [p.ui.image.get(w) for w in p.ui.subtree(row) if w not in p.ui.hidden]


@pytest.mark.parametrize('codec, level, line', [(0, None, 'Connected'), (1, None, 'Connected · SBC'),
                                                (13, 9, 'Connected · aptX HD · 9%')])
def test_the_second_line_says_what_is_known(fw, codec, level, line):
    files = {BATT_FILE: f'{HEAD} {level}\n'.encode()} if level is not None else {}
    p = Page(fw, DEVICES, files=files)
    fw.var('fw_ctx_bt_showcoding', C.c_int).value = codec
    p.links = [(HEAD, 3)]
    fw.fn('bt_headset_battery', C.c_int)()
    p.now += 6000
    p.layout()
    texts = [p.ui.text.get(w) for w in p.ui.subtree(p.row('d0')) if p.ui.kind[w] == 'hscroll_label_create']
    assert texts[1] == line


def test_a_paired_row_is_one_line_saying_not_connected(fw):
    p = Page(fw, [(CAR, 'BMW 6R81', 'paired'), (SPEAKER, 'JBL', 'connecting')])
    p.layout()
    car, speaker = p.row('d0'), p.row('d1')
    assert geometry(car)[3] == geometry(speaker)[3] != 52
    assert p.ui.lookup(car, '_bt_chevron', 1) and not p.ui.lookup(car, '_bt_spin', 1)
    assert p.ui.lookup(speaker, '_bt_spin', 1) and not p.ui.lookup(speaker, '_bt_chevron', 1)


@pytest.mark.parametrize('cls, kind', [(0x240404, 'headphones'), (0x200418, 'headphones'), (0x200420, 'car'),
                                       (0x240414, 'speaker'), (0x5a020c, 'phone'), (0x000100, 'bt'), (0, 'bt')])
def test_a_device_s_kind_is_its_class(fw, cls, kind):
    assert fw.fn('bt_kind', C.c_char_p, C.c_uint)(cls).decode() == kind


def test_classes_are_fetched_once_for_new_devices_and_read_back(fw):
    p = Page(fw, DEVICES)
    fetches = [c for c in p.commands if CLASS_FILE in c]
    assert len(fetches) == 1 and all(f'dev_{m.replace(":", "_")}' in fetches[0] for m in (HEAD, CAR, SPEAKER))
    p.rebuild()
    assert len([c for c in p.commands if CLASS_FILE in c]) == 1  # asked already
    p.files.files[CLASS_FILE] = f'{HEAD} 2360324\n{CAR} 2098208\n'.encode()
    p.rebuild()
    cls = fw.fn('bt_class', C.c_uint, C.c_char_p)
    assert (cls(HEAD.encode()), cls(CAR.encode()), cls(SPEAKER.encode())) == (2360324, 2098208, 0)


def test_the_class_fetch_under_sh(fw, tmp_path):
    p = Page(fw, DEVICES[:2])
    bin_ = tmp_path / 'bin'
    bin_.mkdir()
    (bin_ / 'dbus-send').write_text(
        '#!/bin/sh\ncase "$4" in\n'
        '  */dev_AA_BB_CC_DD_EE_01) echo "   variant       uint32 2360324";;\n'
        '  *) exit 1;;\nesac\n')
    (bin_ / 'dbus-send').chmod(0o755)
    out = tmp_path / 'class'
    out.write_text(f'{SPEAKER} 7\n')
    command = next(c for c in p.commands if CLASS_FILE in c).replace(CLASS_FILE, str(out))
    subprocess.run(['sh', '-c', command.replace(') >/dev/null 2>&1 &', ') >/dev/null 2>&1')], check=True,
                   env={**os.environ, 'PATH': f'{bin_}:{os.environ["PATH"]}'})
    assert out.read_text() == f'{SPEAKER} 7\n{HEAD} 2360324\n'


def test_a_mac_a_shell_could_misread_is_never_asked_for(fw):
    p = Page(fw, [("AA:BB:CC:DD:EE:0F';x", 'X', 'paired')])
    assert not [c for c in p.commands if CLASS_FILE in c]


class Scan:
    def __init__(self, fw, devices=DEVICES[1:2]):
        self.p = Page(fw, devices)
        self.started = 0
        fw.mock('btStartSearch', self.start)
        self.fw = fw
        self.p.ui.windows.append(self.p.win) if self.p.win not in self.p.ui.windows else None

    def start(self, *_):
        self.started += 1
        self.fw.var('bt_searchtime', C.c_ubyte).value = 50
        return 0


def test_the_page_scans_as_it_opens_and_again_while_nothing_is_connected(fw):
    s = Scan(fw)
    s.p.poll()
    assert s.started == 1 and fw.var('g_btsetting_flag', C.c_ubyte).value == 1
    s.p.poll()
    assert s.started == 1  # one runs
    fw.var('bt_searchtime', C.c_ubyte).value = 0
    s.p.poll()
    assert s.started == 2


def test_with_a_device_connected_only_the_opening_scan_runs(fw):
    s = Scan(fw, DEVICES)
    s.p.poll()
    fw.var('bt_searchtime', C.c_ubyte).value = 0
    s.p.poll()
    s.p.poll()
    assert s.started == 1


@pytest.mark.parametrize('blocker', ['covered', 'off', 'linking', 'starting'])
def test_no_scan_while_covered_off_linking_or_starting(fw, blocker):
    s = Scan(fw)
    if blocker == 'covered':
        s.p.ui.new('window_create', 0, 0, 0, 0, 0)
    elif blocker == 'off':
        fw.var('g_bluetoothflag', C.c_ubyte).value = 0
    elif blocker == 'linking':
        fw.var('fw_ctx_bt_connectstatus', C.c_int).value = 1
    else:
        fw.var('fw_ctx_bt_initstatus', C.c_int).value = -102
    s.p.poll()
    assert s.started == 0


def test_scans_rebuilding_the_list_leave_one_of_each_header(fw):
    """Each scan result rebuilds the list whole (stock's btmenu_rows empties it first): however many cycles, one
    Other Devices header with the one spinner, one Audio header. The doubled header seen on V18Ri was a frame drawn at
    a clamped scroll (test_nav_painters.py, row_paint.c list_relaid), not a row left behind."""
    s = Scan(fw, DEVICES)
    for _ in range(4):
        s.p.poll()
        fw.var('bt_searchtime', C.c_ubyte).value = 0
        s.p.rebuild()
        s.p.layout()
        names = s.p.names()
        assert names.count('Other Devices') == 1 and names.count('Audio') == 1
        assert sum(bool(s.p.ui.lookup(r, '_bt_headspin', 0)) for r in s.p.rows()) == 1


class Stops(Scan):
    """Scan, with stock's btStopSearch and getSearchResult recorded: the stop zeroes bt_searchtime as stock's does."""
    def __init__(self, fw, devices=DEVICES[1:2]):
        super().__init__(fw, devices)
        self.calls = []
        fw.mock('btStopSearch', self.stop)
        fw.mock('getSearchResult', lambda *_: self.calls.append('results') or 0)

    def stop(self, *_):
        self.calls.append('stop')
        self.fw.var('bt_searchtime', C.c_ubyte).value = 0
        return 0


@pytest.mark.parametrize('how', ['link thread', 'row'])
def test_a_connect_starting_while_the_page_scans_stops_the_scan(fw, how):
    """A link the row's click did not start (the headset reconnecting, headset.c's or the boot's connect): stock's
    click stops the scan first (btmenu_item_on_click, 0x4c4430), these did not, and an inquiry beside a page and
    then a stream takes their air time for the rest of its 10 s."""
    s = Stops(fw, DEVICES[1:2] if how == 'link thread' else [(CAR, 'BMW 6R81', 'paired')])
    s.p.poll()
    assert s.started == 1 and s.calls == []
    fw.var('bt_searchtime', C.c_ubyte).value = 40
    if how == 'link thread':
        fw.var('fw_ctx_bt_connectstatus', C.c_int).value = 1
    else:
        s.p.devices[0] = (CAR, 'BMW 6R81', 'connected')
        s.p.rebuild()
    s.p.poll()
    assert s.calls == ['stop', 'results'] and fw.var('g_btsetting_flag', C.c_ubyte).value == 1


def test_the_opening_scan_runs_on_beside_a_device_already_connected(fw):
    """Other Devices fills while a headset streams, as stock's page does: only a connect that starts stops a scan."""
    s = Stops(fw, DEVICES)
    s.p.poll()
    fw.var('bt_searchtime', C.c_ubyte).value = 40
    s.p.poll()
    s.p.poll()
    assert s.calls == []


def test_the_page_gone_stops_its_scans(fw):
    s = Scan(fw)
    s.p.ui.destroy(s.p.win)
    s.p.poll()
    assert s.started == 0


class Links(Page):
    """The page's machinery with stock's link list and the battery loop's file ("MAC PCT RATE CLASS")."""
    def __init__(self, fw, devices=DEVICES, resume=b'0'):
        super().__init__(fw, devices, RESUME=resume)
        self.alias = ''

    def link(self, mac=None, level=None, cls=None):
        self.links = [(mac, 3)] if mac else []
        lv = '' if level is None else level
        self.files.files[BATT_FILE] = f'{mac} {lv} 48000 {cls or ""} {self.alias}\n'.encode() if mac else b''
        self.now += 5000  # the loop's file read again
        self.poll()


HEADPHONES, CAR_CLASS = 0x240404, 0x200420


def test_a_device_connecting_and_dropping_toasts_its_name(fw):
    p = Links(fw)
    p.link(HEAD, 80, HEADPHONES)
    p.link(HEAD, 80, HEADPHONES)
    p.link()
    assert p.ui.toasts == ['WH-1000XM5 connected', 'WH-1000XM5 disconnected']


@pytest.mark.parametrize('resume, said', [(b'1', 'BMW 6R81 connected · resuming'), (b'0', 'BMW 6R81 connected')])
def test_a_car_says_it_resumes_when_resume_on_connect_is_on(fw, resume, said):
    p = Links(fw, resume=resume)
    p.link(CAR, None, CAR_CLASS)
    assert p.ui.toasts == [said]


def test_the_connect_toast_waits_a_moment_for_the_class(fw):
    p = Links(fw)
    p.links = [(CAR, 3)]
    p.poll()
    assert p.ui.toasts == []
    p.poll(3000)
    assert p.ui.toasts == []
    p.poll(3000)
    assert p.ui.toasts == ['BMW 6R81 connected']


def test_a_drop_the_user_asked_for_keeps_stock_s_toast_alone(fw):
    p = Links(fw)
    p.link(HEAD, 80, HEADPHONES)
    toast = p.ui.new('window_create', 0, 0, 0, 0, 0)
    p.ui.name[toast] = 'msginfo_dialog'
    p.link()
    assert p.ui.toasts == ['WH-1000XM5 connected']


def test_the_headset_s_battery_toasts_at_20_and_at_10_once_each(fw):
    p = Links(fw)
    for level in (25, 20, 15, 10, 5, 3):
        p.link(HEAD, level, HEADPHONES)
    assert p.ui.toasts == ['WH-1000XM5 connected', 'Headphones low · 20%', 'Headphones low · 10%']
    p.link()
    p.link(HEAD, 9, HEADPHONES)
    p.poll()  # the connect toast first, the battery's at the next look
    assert p.ui.toasts[-2:] == ['WH-1000XM5 connected', 'Headphones low · 9%']  # a new connection: its own


class Stopped:
    """playback.c's ringnav_reboot, as stock's idle loop calls it with the radio dead (bt_initstatus -102)."""
    def __init__(self, fw, status=-102, emulator=False):
        self.fw, self.ui = fw, Ui(fw)
        # the emulator's marker: libq2emu.so, bound into its guest at /lib and on no Q2
        fw.mock('fw_access', lambda path, *_: 0 if emulator and C.string_at(path) == b'/lib/libq2emu.so' else -1)
        Config(fw)
        self.reboots, self.closed = [], []
        fw.var('fw_ctx_bt_initstatus', C.c_int).value = status
        fw.mock('stock_reboot_trampoline', lambda *_: self.reboots.append(1) or 0)
        fw.mock('window_close', lambda w, *_: self.closed.append(w) or self.ui.destroy(w))
        fw.mock('fw_system', lambda *_: 0)
        self.reboot = fw.fn('ringnav_reboot', C.c_int)

    def page(self):
        return next((w for w in self.ui.windows if self.ui.name.get(w) == 'btstopped_page'), None)

    def key(self, key):
        return self.ui.key(self.page(), key)


def test_a_dead_radio_asks_before_it_restarts_then_counts_down(fw):
    s = Stopped(fw)
    assert fw.fn('bt_stopped_toast', C.c_int, C.c_char_p, C.c_void_p)(b'dialog/msginfo_dialog', None) == 0
    assert not s.ui.toasts  # stock's "Bluetooth abnormal": the page says it
    assert s.reboot() == 0 and s.page() and not s.reboots
    assert s.reboot() == 0 and not s.reboots  # stock asks again: the page is up
    assert fw.fn('bt_stopped_title', C.c_char_p, C.c_char_p)(b'btstopped_page') == b'Bluetooth'
    for _ in range(9):
        assert s.ui.fire('bs_tick') == 8 and not s.reboots
    s.ui.fire('bs_tick')
    assert s.reboots == [1]


def test_a_real_q2_with_a_dead_radio_shows_the_page(fw):
    s = Stopped(fw, emulator=False)  # no libq2emu.so: a Q2, whatever else is missing
    assert s.reboot() == 0 and s.page() and not s.reboots
    assert [a for _, a in fw.calls('fw_access') if C.string_at(a[0]) == b'/lib/libq2emu.so']  # it looked


def test_the_emulator_has_no_radio_and_no_bluetooth_stopped_page(fw):
    s = Stopped(fw, emulator=True)
    for _ in range(3):  # stock's loop asks each pass
        assert s.reboot() == 0
    assert not s.page() and not s.reboots
    assert not s.ui.timers  # no countdown to a restart either


@pytest.mark.parametrize('key', [218, 170])  # Centre, Return
def test_not_now_keeps_the_q2_running_without_bluetooth(fw, key):
    s = Stopped(fw)
    s.reboot()
    s.key(key)
    assert not s.page() and not s.reboots
    assert s.reboot() == 0 and not s.page() and not s.reboots  # stock's later calls do nothing


def test_any_other_reboot_is_at_once(fw):
    s = Stopped(fw, status=0)
    assert s.reboot() == 0 and s.reboots == [1] and not s.page()


def test_quality_is_a_key_value_row_naming_the_choice(fw):
    p = Page(fw, DEVICES)
    b = p.ui.kids[p.row('s2')][0]
    labels = [(p.ui.text.get(w), p.ui.name.get(w)) for w in p.ui.subtree(b) if p.ui.kind[w] == 'hscroll_label_create']
    assert labels == [('Quality', None), ('LDAC Auto', '_setting_value')]  # the title, its value (list_value), one line


def test_quality_s_choices_show_their_bit_rates(fw):
    ui = Ui(fw)
    Config(fw)
    win = ui.new('window_create', 0, 0, 0, 0, 0)
    ui.name[win] = 'btquality_page'
    lv = ui.new('list_view_create', win, 0, 26, 375, 294)
    view = ui.new('scroll_view_create', lv, 0, 0, 375, 294)
    names = ['LDAC Best', 'LDAC Standard', 'LDAC Auto', 'aptX HD', 'aptX', 'AAC', 'SBC HQ', 'SBC']
    for i, n in enumerate(names):
        item = ui.new('list_item_create', view, 0, 32 * i, 375, 32)
        b = ui.new('button_create', item, 0, 0, 375, 32)
        ui.name[b] = str(i)
        ui.text[ui.new('hscroll_label_create', b, 22, 0, 266, 32)] = n
    assert fw.fn('bt_list', C.c_int, C.c_void_p)(lv) == 1
    fw.fn('bt_rows_post', None, C.c_void_p)(view)
    fw.fn('bt_rows_post', None, C.c_void_p)(view)  # a second layout adds none
    rates = []
    for item in ui.kids[view]:
        found = [ui.text[w] for w in ui.subtree(item) if ui.name.get(w) == '_bt_kbps']
        rates.append(found[0] if found else None)
    assert rates == ['990 kbps', '660 kbps', None, '576 kbps', '384 kbps', None, None, None]


def test_quality_s_auto_caption_is_one_entry_however_often_the_picker_lays_out(fw):
    """Each layout of the picker (each step of the caption's own growth lays the list out again) must not add Auto's
    row to the caption table again: settings_pages.c cap_step sets each entry's row height, and a later, unmeasured
    copy of the row would hold it at its title's height, the caption never drawn."""
    ui = Ui(fw)
    Config(fw)
    win = ui.new('window_create', 0, 0, 0, 0, 0)
    ui.name[win] = 'btquality_page'
    lv = ui.new('list_view_create', win, 0, 26, 375, 294)
    view = ui.new('scroll_view_create', lv, 0, 0, 375, 294)
    for i, n in enumerate(['LDAC Best', 'LDAC Standard', 'LDAC Auto']):
        item = ui.new('list_item_create', view, 0, 32 * i, 375, 32)
        b = ui.new('button_create', item, 0, 0, 375, 32)
        ui.name[b] = str(i)
        ui.text[ui.new('hscroll_label_create', b, 22, 0, 266, 32)] = n
    for _ in range(3):
        fw.fn('bt_rows_post', None, C.c_void_p)(view)
    table = fw.var('settings_pages__cap', C.c_void_p * 17)  # view, item[CAP_MAX], text[CAP_MAX]
    auto = ui.kids[view][2]
    assert table[0] == view and [table[1 + k] for k in range(8)].count(auto) == 1
    assert C.wstring_at(table[9]) == 'Steps with the signal, 330 to 990 kbps'


STEP_TITLE = 'Lower quality when the signal is weak'


def quality_page(fw, **config):
    """Quality's picker as stock builds it, its eight choices, laid out twice (each step of a caption's growth lays
    the list out again): pre, then post, as ipod_list_layout runs them."""
    ui = Ui(fw)
    cfg = Config(fw, **config)
    win = ui.new('window_create', 0, 0, 0, 0, 0)
    ui.name[win] = 'btquality_page'
    lv = ui.new('list_view_create', win, 0, 26, 375, 294)
    view = ui.new('scroll_view_create', lv, 0, 0, 375, 294)
    for i, n in enumerate(['LDAC Best', 'LDAC Standard', 'LDAC Auto', 'aptX HD', 'aptX', 'AAC', 'SBC HQ', 'SBC']):
        item = ui.new('list_item_create', view, 0, 32 * i, 375, 32)
        b = ui.new('button_create', item, 0, 0, 375, 32)
        ui.name[b] = str(i)
        ui.text[ui.new('hscroll_label_create', b, 22, 0, 266, 32)] = n
    for _ in range(2):
        fw.fn('bt_rows_pre', None, C.c_void_p)(view)
        fw.fn('bt_rows_post', None, C.c_void_p)(view)
    return ui, cfg, view


def step_row(ui, view):
    """The step-down's row: its button, its title and whether its switch is on."""
    rows = [item for item in ui.kids[view] if any(ui.text.get(w) == STEP_TITLE for w in ui.subtree(item))]
    assert len(rows) == 1 and rows[0] == ui.kids[view][-1]  # one, after the eight choices
    b = ui.kids[rows[0]][0]
    switch = [w for w in ui.subtree(b) if ui.name.get(w) == '_setting_switch']
    assert len(switch) == 1
    assert not [w for w in ui.subtree(b) if ui.name.get(w) == '_bt_kbps']  # no choice's bit rate
    return b, ui.image[switch[0]] == 'switch_on'


def test_quality_ends_with_the_step_down_switch_on_by_default(fw):
    """Lower quality when the signal is weak (docs/internals.md#bluetooth-link-monitor): q2guard's step-down from
    aptX HD under LDAC Auto, on unless [Q2POD] BTSTEPDOWN says 0; one row however often the picker lays out."""
    ui, _, view = quality_page(fw)
    assert step_row(ui, view)[1] is True
    ui, _, view = quality_page(fw, BTSTEPDOWN=b'0')
    assert step_row(ui, view)[1] is False


def test_the_step_down_switch_toggles_and_saves(fw):
    ui, cfg, view = quality_page(fw)
    b, _ = step_row(ui, view)
    ui.handler(b, EVT_CLICK)()
    assert cfg.values[b'BTSTEPDOWN'] == b'0' and step_row(ui, view)[1] is False
    ui.handler(b, EVT_CLICK)()
    assert cfg.values[b'BTSTEPDOWN'] == b'1' and step_row(ui, view)[1] is True


@pytest.mark.parametrize('codec, status, caption', [
    (13, 3, 'Streams aptX HD to this headset, 576 kbps'),  # a B&W Px7 on LDAC Auto: no LDAC, aptX HD at 48 kHz
    (2, 3, 'Streams AAC to this headset'),                 # no fixed rate to name
    (4, 3, 'Steps with the signal, 330 to 990 kbps'),      # LDAC: Auto is what it says
    (13, 2, 'Steps with the signal, 330 to 990 kbps'),     # not connected: the setting, not a headset
    (0, 3, 'Steps with the signal, 330 to 990 kbps'),      # codec not known yet
])
def test_quality_s_auto_caption_names_what_the_connected_headset_streams(fw, codec, status, caption):
    """LDAC Auto offers aptX HD, aptX, AAC and SBC beside LDAC; a headset without LDAC gets one of those at its own
    rate, so the caption names it instead of LDAC's range (docs/internals.md#bluetooth-page)."""
    p = Page(fw, DEVICES, files={BATT_FILE: f'{HEAD} 72 48000\n'.encode()})
    fw.var('fw_ctx_bt_showcoding', C.c_int).value = codec
    p.links = [(HEAD, status)]
    fw.fn('bt_headset_battery', C.c_int)()
    p.now += 6000
    ui = p.ui
    win = ui.new('window_create', 0, 0, 0, 0, 0)
    ui.name[win] = 'btquality_page'
    lv = ui.new('list_view_create', win, 0, 26, 375, 294)
    view = ui.new('scroll_view_create', lv, 0, 0, 375, 294)
    for i, n in enumerate(['LDAC Best', 'LDAC Standard', 'LDAC Auto']):
        item = ui.new('list_item_create', view, 0, 32 * i, 375, 32)
        b = ui.new('button_create', item, 0, 0, 375, 32)
        ui.name[b] = str(i)
        ui.text[ui.new('hscroll_label_create', b, 22, 0, 266, 32)] = n
    fw.fn('bt_rows_post', None, C.c_void_p)(view)
    table = fw.var('settings_pages__cap', C.c_void_p * 17)
    assert table[0] == view and table[1] == ui.kids[view][2]
    assert C.wstring_at(table[9]) == caption


def test_a_device_the_list_has_not_read_is_named_by_blue_z(fw):
    """Away from the Bluetooth page stock's list may be empty: the battery loop's Alias names it, and its drop."""
    p = Links(fw, devices=())
    p.alias = 'Pixel Buds'
    p.link(HEAD, 80, HEADPHONES)
    p.link()
    assert p.ui.toasts == ['Pixel Buds connected', 'Pixel Buds disconnected']


# A tap or Centre on a row that is connecting cancels that connect (docs/internals.md#bluetooth-page).
class Tap(Page):
    """The page with stock's link list: the row's device the link thread's target, at status (5 while its Connect
    runs), and stock's click and updateBtLinkStatus recorded."""
    def __init__(self, fw, state='connecting', status=5, linking=1, target=CAR):
        devices = [(HEAD, 'WH-1000XM5', 'paired'), (CAR, 'BMW 6R81', state)]
        super().__init__(fw, devices)
        self.links = [(target, status)]
        fw.var('fw_ctx_bt_connectstatus', C.c_int).value = linking
        self.stock, self.updates = [], []
        fw.mock('btmenu_item_on_click', lambda b, e, *_: self.stock.append(b) or 0)
        fw.mock('updateBtLinkStatus', lambda st, mac, *_: self.updates.append((st, C.string_at(mac).decode())) or 0)

    def tap(self, name='d1'):
        b = self.ui.kids[self.row(name)][0]
        return self.ui.handler(b, EVT_CLICK)()


def test_a_tap_on_a_connecting_row_cancels_its_connect(fw):
    t = Tap(fw)
    assert t.tap() == 11  # STOP
    assert t.updates == [(4, CAR)] and t.stock == []  # the thread's wait ends as a failure: stock's quiet path


@pytest.mark.parametrize('why, kw', [('before its connect runs', dict(status=1)), ('no link thread', dict(linking=0)),
                                     ("another device's connect", dict(target=HEAD))])
def test_a_connecting_row_whose_connect_is_not_running_goes_to_stock(fw, why, kw):
    t = Tap(fw, **kw)
    t.tap()
    assert t.updates == [] and len(t.stock) == 1  # stock's click, as before: it ignores it while a thread runs


def test_a_tap_on_a_paired_row_still_connects(fw):
    t = Tap(fw, state='paired', linking=0, status=3, target=HEAD)
    t.tap()
    assert t.updates == [] and len(t.stock) == 1


class Spinning(Page):
    """A device connecting, its row's spinner painted as AWTK would: each loop pass the poll, then every timer due,
    then the marks whose boxes were asked to repaint (the mark, or a widget holding it, invalidated)."""
    def __init__(self, fw, devices, scanning=0):
        super().__init__(fw, devices)
        self.ui.windows.append(self.win) if self.win not in self.ui.windows else None
        fw.var('fw_ctx_bt_connectstatus', C.c_int).value = 1  # stock's link thread runs
        fw.var('bt_searchtime', C.c_ubyte).value = 50 if scanning else 0
        fw.mock('btStartSearch', lambda *_: 0)
        self.drawn, self.dirty, self.due = [], [], {}
        fw.mock('widget_load_image', lambda w, name, *_: self.drawn.append((w, C.string_at(name).decode())) or 1)
        fw.mock('widget_invalidate_force', lambda w, *_: self.dirty.append(w) or 0)
        self.layout()

    def mark(self, row, name):
        return self.ui.lookup(self.row(row), name, 1)

    def paint(self, mark):
        """The frame mark shows now: the spinner art's name it loads."""
        self.drawn.clear()
        self.fw.fn('bt_ui_paint', None, C.c_void_p, C.c_void_p)(mark, None)
        return [n for w, n in self.drawn if 'spin' in n]

    def frames(self, mark, ms=1000, step=16):
        """What the screen shows over ms of loop passes every step ms: each frame painted as its box repaints."""
        shown = [self.paint(mark)]
        for _ in range(ms // step):
            self.dirty.clear()
            self.now += step
            self.fw.fn('bt_ui_poll', None)()
            for t, (fn, ctx, every) in list(self.ui.timers.items()):
                at = self.due.setdefault(t, self.now - step)
                if self.now - at >= every:
                    self.due[t] = self.now
                    if TICK(fn)(ctx) != 8:
                        self.ui.timers.pop(t, None)
            if any(w == mark or mark in set(self.ui.subtree(w)) for w in self.dirty):
                shown.append(self.paint(mark))
        return shown


TICK = C.CFUNCTYPE(C.c_int, C.c_void_p)


@pytest.mark.parametrize('scanning', [0, 1])
def test_a_connecting_row_s_spinner_turns_the_whole_connect(fw, scanning):
    """Device report (p32, 2026-11-17): the row's spinner froze while a headset connected. Stock connects on a link
    thread of its own, so nothing holds the UI thread: the spinner turns a spoke every 100 ms whether a scan runs or
    not (the page starts none while a device connects), its box repainted for each."""
    s = Spinning(fw, [(CAR, 'BMW 6R81', 'paired'), (SPEAKER, 'JBL Flip 6', 'connecting')], scanning)
    mark = s.mark('d1', '_bt_spin')
    assert mark
    shown = s.frames(mark)
    turns = [f for f in shown if f]
    assert len({f[0] for f in turns}) == 8  # every spoke
    assert len([1 for a, b in zip(turns, turns[1:]) if a != b]) >= 9  # a second: ten steps


PICK_FILE = '/tmp/q2bt.pick'


def quality_pick(fw, row, codec, linked=1):
    """Stock's Quality click for choice `row` with stock's codec number `codec` streaming: bt_quality_linked's answer
    (stock reconnects on anything but 0) and the switch it started, if any."""
    p = Page(fw, DEVICES)
    fw.var('bt_linkstatus', C.c_ubyte).value = linked
    fw.var('fw_ctx_bt_showcoding', C.c_int).value = codec
    p.commands.clear()
    r = fw.fn('bt_quality_linked', C.c_int, C.c_int)(row)
    return r, [c for c in p.commands if PICK_FILE in c]


@pytest.mark.parametrize('row, codec, order', [
    (4, 13, 'aptX LDAC aptX-HD AAC SBC'),  # aptX HD streaming, aptX picked
    (7, 13, 'SBC'),                        # SBC every headset has
    (6, 3, 'SBC'),
    (3, 1, 'aptX-HD LDAC aptX AAC SBC'),   # back up from SBC
    (5, 4, 'AAC LDAC aptX-HD aptX SBC'),
    (2, 3, 'LDAC aptX-HD aptX AAC SBC'),   # LDAC Auto on a headset without LDAC: the best it has, as bluetoothd picks
])
def test_a_quality_pick_on_a_live_link_switches_the_codec_in_place(fw, row, codec, order):
    """Stock asks to reconnect and drops the headset (btDisconnect, then connectBtDevice: a new ACL handle at each of
    the commute's three picks). A pick that changes the codec asks bluealsa for it on the link as it is, the choice's
    codec first and then bluetoothd's own order for a headset without it, and tells stock nothing is linked, so its
    tail saves the choice and sets the priority for the next connect with no dialog and no disconnect."""
    r, switches = quality_pick(fw, row, codec)
    assert r == 0 and len(switches) == 1
    assert f'for c in {order}; do' in switches[0] and 'q2bt-codec "$p" $c' in switches[0]
    assert switches[0].rstrip().endswith('&')


@pytest.mark.parametrize('row, codec', [(3, 13), (4, 3), (5, 2)])
def test_a_pick_of_the_codec_already_streaming_changes_nothing(fw, row, codec):
    """aptX HD, aptX and AAC have one rate each: the headset already has what was picked, so neither a switch nor
    stock's reconnect."""
    assert quality_pick(fw, row, codec) == (0, [])


@pytest.mark.parametrize('row, codec', [(0, 4), (1, 4), (2, 4), (6, 1), (7, 1)])
def test_a_pick_within_ldac_or_sbc_is_still_stock_s_reconnect(fw, row, codec):
    """LDAC's three choices and SBC's two differ in a rate bluealsa reads when the encoder starts, which only a new
    transport brings: stock's dialog and reconnect stay."""
    r, switches = quality_pick(fw, row, codec)
    assert r != 0 and not switches


def test_a_pick_with_nothing_linked_is_stock_s(fw):
    assert quality_pick(fw, 4, 13, linked=0) == (0, [])
    assert quality_pick(fw, 4, 0) == (0, [])  # linked, no codec yet: stock's own second test


def test_the_switch_under_sh_takes_the_first_codec_the_headset_has(fw, tmp_path):
    """The command as sh runs it: bluealsa's A2DP source PCM from GetPCMs, then q2bt-codec for each codec in order
    until one is taken. A Px7 has no LDAC: bluealsa refuses it at once and aptX HD is next."""
    _, switches = quality_pick(fw, 2, 3)
    bin_ = tmp_path / 'bin'
    bin_.mkdir()
    (bin_ / 'dbus-send').write_text(
        '#!/bin/sh\ncat <<E\n   array [\n      dict entry(\n'
        '         object path "/org/bluealsa/hci0/dev_EC_66_D1_CF_59_3F/hfpag/source"\n      )\n      dict entry(\n'
        '         object path "/org/bluealsa/hci0/dev_EC_66_D1_CF_59_3F/a2dpsrc/sink"\n      )\n   ]\nE\n')
    calls = tmp_path / 'calls'
    (bin_ / 'q2bt-codec').write_text(
        f'#!/bin/sh\necho "$1 $2" >>{calls}\n[ "$2" = LDAC ] && {{ echo "org.freedesktop.DBus.Error.Failed SEP codec '
        'not available"; exit 1; }\nexit 0\n')
    for f in bin_.iterdir():
        f.chmod(0o755)
    out = tmp_path / 'pick'
    command = switches[0].replace(PICK_FILE, str(out)).rstrip().removesuffix('&')
    subprocess.run(['sh', '-c', command], check=True, env={**os.environ, 'PATH': f'{bin_}:{os.environ["PATH"]}'})
    pcm = '/org/bluealsa/hci0/dev_EC_66_D1_CF_59_3F/a2dpsrc/sink'
    assert calls.read_text() == f'{pcm} LDAC\n{pcm} aptX-HD\n'
    assert out.read_text().splitlines()[-1] == 'aptX-HD'


def test_the_switch_under_sh_does_nothing_without_an_a2dp_source_pcm(fw, tmp_path):
    _, switches = quality_pick(fw, 7, 13)
    bin_ = tmp_path / 'bin'
    bin_.mkdir()
    (bin_ / 'dbus-send').write_text('#!/bin/sh\necho "   array [\n   ]"\n')
    calls = tmp_path / 'calls'
    (bin_ / 'q2bt-codec').write_text(f'#!/bin/sh\necho "$@" >>{calls}\n')
    for f in bin_.iterdir():
        f.chmod(0o755)
    out = tmp_path / 'pick'
    command = switches[0].replace(PICK_FILE, str(out)).rstrip().removesuffix('&')
    subprocess.run(['sh', '-c', command], check=False, env={**os.environ, 'PATH': f'{bin_}:{os.environ["PATH"]}'})
    assert not calls.exists() and out.read_text() == ''


@pytest.mark.parametrize('event, shown, direction, after', [
    (3, 13, 0, 3),    # stepped or picked down to aptX on the live link
    (1, 3, 0, 1),
    (13, 1, 0, 13),
    (3, 13, 1, 13),   # a link being set up: stock's checkBluetoothStatus takes the event itself
    (0x65, 13, 0, 13),  # bluealsa's refused configuration, stock's reconnect: not a codec
    (3, 0, 0, 0),     # no codec shown: nothing linked
])
def test_the_codec_shown_follows_a_switch_on_a_live_link(fw, event, shown, direction, after):
    """bluealsa names the new codec in its status event (a2dp_eventstate) at each SetConfiguration; stock's
    checkBluetoothStatus reads it only while it sets a link up (bt_a2dp_direction), so after a switch in place the
    badge and the connected row kept the codec the link began on."""
    p = Page(fw, DEVICES)
    fw.var('bt_linkstatus', C.c_ubyte).value = 1
    fw.var('fw_ctx_bt_showcoding', C.c_int).value = shown
    fw.var('fw_ctx_a2dp_eventstate', C.c_int).value = event
    fw.var('fw_ctx_bt_a2dp_direction', C.c_int).value = direction
    p.poll()
    assert fw.var('fw_ctx_bt_showcoding', C.c_int).value == after
    assert fw.var('fw_ctx_a2dp_eventstate', C.c_int).value == event  # left for stock
