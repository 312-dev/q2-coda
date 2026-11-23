"""bt_ui.c: Settings > Bluetooth, devices before settings. The switch, Name, My Devices and Other Devices with their
rows, then the Audio group (Guest Play, Quality, Headset Volume, Resume on Connect, Link Volume); stock's Scan now row gone, a scan
started by the page itself while it is open; the connected device a two-line row ("Connected · LDAC · 72%")."""
import ctypes as C
import os
import re
import subprocess

import pytest

from conftest import Files
from test_nav_library_pages import Config, Ui

EVT_CLICK = 0x10c
CLASS_FILE = '/tmp/q2-btclass'
BATT_FILE = '/tmp/q2-btbatt'
SEEN_FILE = '/tmp/q2-btseen'
HEAD = 'AA:BB:CC:DD:EE:01'
CAR = 'AA:BB:CC:DD:EE:02'
SPEAKER = 'AA:BB:CC:DD:EE:03'


class Page:
    """Settings > Bluetooth as stock's btmenu_rows builds it: five head rows s0-s4 (Bluetooth on) or the switch,
    then My Devices' header and the paired devices, Other Devices' header and the found ones, each device row its
    button named by its index in pdeq_btshowlist, holding stock's icon, name and state labels; then ringnav_btrows."""
    def __init__(self, fw, devices=(), on=1, files=None, answer=True, **config):
        self.fw, self.ui, self.devices = fw, Ui(fw), list(devices)
        self.config = Config(fw, **config)
        self.files = Files(fw, dict(files or {}))
        self.commands, self.now, self.links, self.answer = [], 1000, [], answer
        fw.mock('fw_system', self.system)
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

    def system(self, cmd, *_):
        """A command run; with answer, BlueZ's answer to the page's ask of which found devices the scan heard
        (SEEN_FILE), at once: every device listed as found was."""
        command = C.string_at(cmd).decode()
        self.commands.append(command)
        if self.answer and SEEN_FILE in command:
            seen = ''.join(f'{mac} seen\n' for mac, _, state in self.devices if state == 'found')
            ask = re.search(r"echo '(#\d+)'", command)
            assert ask
            self.files.files[SEEN_FILE] = (ask[1] + '\n' + seen).encode()
        return 0

    def link_item(self, i, status, mac, *_):
        m, s = self.links[i]
        C.c_int.from_address(status).value = s
        C.memmove(mac, m.encode() + b'\0', len(m) + 1)
        return 0

    def build(self, *_):
        """btmenu_rows: the list's rows made again whole."""
        ui, view = self.ui, self.view
        for k in list(ui.kids[view]):
            ui.destroy(k)
        for i in range(5 if self.fw.var('g_bluetoothflag', C.c_ubyte).value else 1):
            item = ui.new('list_item_create', view, 0, 0, 375, 78)
            ui.name[item] = f's{i}'
            b = ui.new('button_create', item, 20, 0, 335, 78)
            ui.name[b] = str(i)
            if i == 2:  # Quality over its choice, a second line (btmenu_setting_row_build)
                ui.text[ui.new('hscroll_label_create', b, 10, 0, 266, 35)] = 'Quality'
                ui.text[ui.new('hscroll_label_create', b, 10, 35, 266, 35)] = 'Auto'
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
    """Other Devices fills while a headset streams, as stock's page does: only a connect that starts stops a scan
    (the page reads the scan's results meanwhile, a_device_found_mid_scan below)."""
    s = Stops(fw, DEVICES)
    s.p.poll()
    fw.var('bt_searchtime', C.c_ubyte).value = 40
    s.p.poll()
    s.p.poll()
    assert 'stop' not in s.calls


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
    assert labels == [('Quality', None), ('Auto', '_setting_value')]  # the title, its value (list_value), one line


def test_quality_s_choices_show_their_bit_rates(fw):
    ui = Ui(fw)
    Config(fw)
    win = ui.new('window_create', 0, 0, 0, 0, 0)
    ui.name[win] = 'btquality_page'
    lv = ui.new('list_view_create', win, 0, 26, 375, 294)
    view = ui.new('scroll_view_create', lv, 0, 0, 375, 294)
    names = ['LDAC Best', 'LDAC Standard', 'Auto', 'aptX HD', 'aptX', 'AAC', 'SBC HQ', 'SBC']
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
    for i, n in enumerate(['LDAC Best', 'LDAC Standard', 'Auto']):
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


STEP_TITLE = 'Adapt to Signal'
STEP_CAPTION = 'On Auto or SBC, lowers quality to keep playing when the signal is weak'


def quality_page(fw, **config):
    """Quality's picker as stock builds it, its eight choices, laid out twice (each step of a caption's growth lays
    the list out again): pre, then post, as ipod_list_layout runs them."""
    ui = Ui(fw)
    cfg = Config(fw, **config)
    win = ui.new('window_create', 0, 0, 0, 0, 0)
    ui.name[win] = 'btquality_page'
    lv = ui.new('list_view_create', win, 0, 26, 375, 294)
    view = ui.new('scroll_view_create', lv, 0, 0, 375, 294)
    for i, n in enumerate(['LDAC Best', 'LDAC Standard', 'Auto', 'aptX HD', 'aptX', 'AAC', 'SBC HQ', 'SBC']):
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
    assert len(rows) == 1 and rows[0] == ui.kids[view][0]  # one, first, above the choices
    b = ui.kids[rows[0]][0]
    switch = [w for w in ui.subtree(b) if ui.name.get(w) == '_setting_switch']
    assert len(switch) == 1
    assert not [w for w in ui.subtree(b) if ui.name.get(w) == '_bt_kbps']  # no choice's bit rate
    return b, ui.image[switch[0]] == 'switch_on'


def test_quality_opens_with_the_adapt_to_signal_switch_off_by_default(fw):
    """Adapt to Signal (docs/internals.md#bluetooth-link-monitor): q2guard's step-down from aptX HD under Auto
    and SBC's bitpool, off unless [Q2POD] BTSTEPDOWN says 1, so a Q2 that never saved the key (the switch's
    default was on before) is off too; one row however often the picker lays out, its caption saying what it
    does."""
    ui, _, view = quality_page(fw)
    assert step_row(ui, view)[1] is False
    table = fw.var('settings_pages__cap', C.c_void_p * 17)  # view, item[CAP_MAX], text[CAP_MAX]
    items = [table[1 + k] for k in range(8)]
    assert items.count(ui.kids[view][0]) == 1
    assert C.wstring_at(table[9 + items.index(ui.kids[view][0])]) == STEP_CAPTION
    ui, _, view = quality_page(fw, BTSTEPDOWN=b'1')    # turned on by hand: kept on
    assert step_row(ui, view)[1] is True
    ui, _, view = quality_page(fw, BTSTEPDOWN=b'0')
    assert step_row(ui, view)[1] is False


def test_quality_is_one_flat_list_switch_then_codec_header_then_auto_first(fw):
    """docs/oled-design.md#bluetooth Quality: Adapt to Signal, a CODEC header the wheel passes, then Auto and the
    fixed choices (stock's rows, named by their index in g_btcoding, so a pick still saves stock's number); the
    order holds however often the picker lays out."""
    ui, _, view = quality_page(fw)
    rows = ui.kids[view]
    head = ui.lookup(rows[1], '_bt_head', 0)
    assert head and ui.text[head] == 'CODEC' and ui.props.get((rows[1], '_bt_head')) == 1  # a heading: passed
    names = [ui.name.get(ui.kids[r][0]) if ui.kids[r] else None for r in rows[2:]]
    assert names == ['2', '0', '1', '3', '4', '5', '6', '7']
    assert len(rows) == 10


def test_quality_s_rates_end_on_the_switch_s_edge_and_before_the_check_on_the_checked_row(fw):
    """A rate is a Settings row's value: flush with the right column, where the switch above ends; the checked
    row's sits just before its check (12 px and the gap), and moves when a pick moves the check (no layout)."""
    fw.mock('widget_move_resize', lambda w, *box: [setattr(C.c_int.from_address(w + 4 * i), 'value', v)
                                                   for i, v in enumerate(box[:4])] and 0)
    fw.var('g_btcoding', C.c_ubyte).value = 3  # aptX HD
    ui, _, view = quality_page(fw)

    def ends():
        out = {}
        for item in ui.kids[view]:
            b = ui.kids[item][0] if ui.kids[item] else 0
            l = ui.lookup(b, '_bt_kbps', 0) if b else 0
            if l:
                x, _, w, _ = geometry(l)
                out[ui.name[b]] = geometry(b)[0] + x + w
        return out
    b, _ = step_row(ui, view)
    sw = next(w for w in ui.subtree(b) if ui.name.get(w) == '_setting_switch')
    edge = geometry(b)[0] + geometry(sw)[0] + geometry(sw)[2]
    assert ends() == {'0': edge, '1': edge, '3': edge - 24, '4': edge}
    fw.var('g_btcoding', C.c_ubyte).value = 0  # LDAC Best picked: stock moves the check, nothing lays out
    fw.fn('bt_ui_poll', None)()
    assert ends() == {'0': edge - 24, '1': edge, '3': edge, '4': edge}


def test_the_adapt_to_signal_switch_toggles_and_saves(fw):
    ui, cfg, view = quality_page(fw)
    b, _ = step_row(ui, view)
    ui.handler(b, EVT_CLICK)()
    assert cfg.values[b'BTSTEPDOWN'] == b'1' and step_row(ui, view)[1] is True
    ui.handler(b, EVT_CLICK)()
    assert cfg.values[b'BTSTEPDOWN'] == b'0' and step_row(ui, view)[1] is False


@pytest.mark.parametrize('codec, status, caption', [
    (13, 3, 'Best this headset supports: aptX HD, 576 kbps'),  # a B&W Px7 on Auto: no LDAC, aptX HD at 48 kHz
    (2, 3, 'Best this headset supports: AAC'),                 # no fixed rate to name
    (4, 3, 'Steps with the signal, 330 to 990 kbps'),      # LDAC: Auto is what it says
    (13, 2, 'Steps with the signal, 330 to 990 kbps'),     # not connected: the setting, not a headset
    (0, 3, 'Steps with the signal, 330 to 990 kbps'),      # codec not known yet
])
def test_quality_s_auto_caption_names_what_the_connected_headset_streams(fw, codec, status, caption):
    """Auto offers aptX HD, aptX, AAC and SBC beside LDAC; a headset without LDAC gets one of those at its own
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
    for i, n in enumerate(['LDAC Best', 'LDAC Standard', 'Auto']):
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


def test_a_tap_on_a_paired_row_opens_its_page(fw):
    t = Tap(fw, state='paired', linking=0, status=3, target=HEAD)
    t.tap()
    assert t.updates == [] and t.stock == []  # Connect is the page's first row (test_bt_detail.py)
    assert any(t.ui.name.get(w) == 'btdevice_page' for w in t.ui.windows)


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
    (2, 3, 'LDAC aptX-HD aptX AAC SBC'),   # Auto on a headset without LDAC: the best it has, as bluetoothd picks
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


# The connected device's codecs (docs/internals.md#bluetooth-page, Quality): learned from bluealsa's GetCodecs once
# the link streams, the Quality pick taken as the head of an ordered list ending in SBC, a codec the device lacks
# refused before the link is touched and marked on the picker.
CODECS_FILE = '/tmp/q2bt.codecs'
BMW = ['SBC', 'AAC']                          # a car: SBC and AAC only
PX7 = ['aptX-HD', 'aptX', 'AAC', 'SBC']        # Bowers & Wilkins Px7: no LDAC
CODEC = {'SBC': 1, 'AAC': 2, 'aptX': 3, 'LDAC': 4, 'aptX-HD': 13}


class Bluealsa:
    """bluealsa and its D-Bus as sh sees them: dbus-send answering GetPCMs (the device's HFP and A2DP source PCMs)
    and GetCodecs (the device's sink endpoints, as dbus-send --print-reply prints a{sa{sv}}), and q2bt-codec, which
    refuses a codec the device lacks as SelectCodec does ("SEP codec not available") and records every call."""
    def __init__(self, tmp_path, mac, codecs):
        self.bin, self.calls, self.mac = tmp_path / 'bin', tmp_path / 'calls', mac
        self.bin.mkdir()
        dev = 'dev_' + mac.replace(':', '_')
        self.pcm = f'/org/bluealsa/hci0/{dev}/a2dpsrc/sink'
        entries = ''.join(
            f'      dict entry(\n         string "{c}"\n         array [\n            dict entry(\n'
            '               string "Capabilities"\n               variant                   array of bytes [\n'
            '                     ff ff 02 35\n                  ]\n            )\n         ]\n      )\n' for c in codecs)
        (self.bin / 'dbus-send').write_text(
            '#!/bin/sh\ncase "$*" in\n'
            '*GetPCMs*) cat <<X\nmethod return time=1 sender=:1.5 -> destination=:1.9 serial=4 reply_serial=2\n'
            f'   array [\n      dict entry(\n         object path "/org/bluealsa/hci0/{dev}/hfpag/source"\n      )\n'
            f'      dict entry(\n         object path "{self.pcm}"\n      )\n   ]\nX\n;;\n'
            f'*{self.pcm}*GetCodecs*) cat <<X\nmethod return time=1 sender=:1.5 -> destination=:1.9 serial=5 '
            f'reply_serial=2\n   array [\n{entries}   ]\nX\n;;\n*) exit 1;;\nesac\n')
        (self.bin / 'q2bt-codec').write_text(
            f'#!/bin/sh\necho "$1 $2" >>{self.calls}\ncase " {" ".join(codecs)} " in *" $2 "*) exit 0;; esac\n'
            'echo "org.freedesktop.DBus.Error.Failed SEP codec not available"; exit 1\n')
        for f in self.bin.iterdir():
            f.chmod(0o755)

    def sh(self, command, swap):
        """command as sh runs it in the background, its /tmp file at swap's local path; returns that file's text."""
        (path, local), = swap.items()
        command = command.replace(path, str(local)).rstrip().removesuffix('&')
        subprocess.run(['sh', '-c', command], check=False,
                       env={**os.environ, 'PATH': f'{self.bin}:{os.environ["PATH"]}'})
        return local.read_text() if local.exists() else None

    def selected(self):
        return [line.split()[1] for line in self.calls.read_text().splitlines()] if self.calls.exists() else []


class Connected(Page):
    """A device connected and streaming `codec` (bluealsa's name), its codecs learned as the payload asks for them:
    the fetch it starts run under sh against `bluealsa`, its answer put where the payload reads it."""
    def __init__(self, fw, tmp_path, mac, name, sink, codec):
        super().__init__(fw, [(mac, name, 'connected')])
        self.bluealsa = Bluealsa(tmp_path, mac, sink)
        self.tmp = tmp_path
        self.links = [(mac, 3)]
        fw.var('bt_linkstatus', C.c_ubyte).value = 1
        self.stream(codec)
        self.commands.clear()
        self.poll()
        fetches = [c for c in self.commands if CODECS_FILE in c]
        assert len(fetches) == 1 and fetches[0].rstrip().endswith('&')
        answer = self.bluealsa.sh(fetches[0], {CODECS_FILE: tmp_path / 'codecs'})
        assert answer is not None
        self.files.files[CODECS_FILE] = answer.encode()
        self.poll()

    def stream(self, codec):
        self.fw.var('fw_ctx_bt_showcoding', C.c_int).value = CODEC[codec]
        self.fw.var('fw_ctx_a2dp_eventstate', C.c_int).value = CODEC[codec]

    def picker(self):
        """Quality's picker on this page's ui, laid out twice as ipod_list_layout does."""
        ui = self.ui
        win = ui.new('window_create', 0, 0, 0, 0, 0)
        ui.name[win] = 'btquality_page'
        lv = ui.new('list_view_create', win, 0, 26, 375, 294)
        self.qview = ui.new('scroll_view_create', lv, 0, 0, 375, 294)
        for i, n in enumerate(['LDAC Best', 'LDAC Standard', 'Auto', 'aptX HD', 'aptX', 'AAC', 'SBC HQ', 'SBC']):
            item = ui.new('list_item_create', self.qview, 0, 32 * i, 375, 32)
            b = ui.new('button_create', item, 0, 0, 375, 32)
            ui.name[b] = str(i)
            ui.text[ui.new('hscroll_label_create', b, 22, 0, 266, 32)] = n
        for _ in range(2):
            self.fw.fn('bt_rows_pre', None, C.c_void_p)(self.qview)
            self.fw.fn('bt_rows_post', None, C.c_void_p)(self.qview)

    def choice(self, k):
        return next(r for r in self.ui.kids[self.qview] if self.ui.kids[r] and self.ui.name.get(self.ui.kids[r][0]) == str(k))

    def lacking(self):
        """The choices marked as the device lacking them, by g_btcoding's index."""
        return sorted(k for k in range(8) if self.ui.props.get((self.choice(k), '_bt_lack')) == 1)

    def caption(self, k):
        table = self.fw.var('settings_pages__cap', C.c_void_p * 17)  # view, item[CAP_MAX], text[CAP_MAX]
        items = [table[1 + i] for i in range(8)]
        row = self.choice(k)
        return C.wstring_at(table[9 + items.index(row)]) if row in items else None

    def pick(self, row):
        """Stock's Quality click on row: bt_quality_linked's answer and the codec bluealsa ends on, if it switched."""
        self.commands.clear()
        r = self.fw.fn('bt_quality_linked', C.c_int, C.c_int)(row)
        switches = [c for c in self.commands if PICK_FILE in c]
        assert len(switches) <= 1
        took = self.bluealsa.sh(switches[0], {PICK_FILE: self.tmp / 'pick'}) if switches else None
        return r, took.splitlines()[-1] if took else None


def test_the_device_s_codecs_are_asked_of_bluealsa_once_it_streams(fw, tmp_path):
    """GetCodecs on the device's own A2DP source PCM (not its HFP one), written as the MAC then each string a line."""
    p = Connected(fw, tmp_path, CAR, 'BMW 6R81', BMW, 'AAC')
    lines = (tmp_path / 'codecs').read_text().splitlines()
    assert lines[0] == CAR and [n for n in lines if n in CODEC] == ['SBC', 'AAC']  # the inner keys are read past
    p.commands.clear()
    for _ in range(30):
        p.poll(3000)
    assert not [c for c in p.commands if CODECS_FILE in c]  # answered: not asked again


def test_an_unanswered_ask_is_made_again_a_few_times(fw):
    p = Page(fw, [(CAR, 'BMW 6R81', 'connected')])
    p.links = [(CAR, 3)]
    fw.var('fw_ctx_bt_showcoding', C.c_int).value = 0
    p.poll()
    assert not [c for c in p.commands if CODECS_FILE in c]  # not streaming yet: no PCM to ask
    fw.var('fw_ctx_bt_showcoding', C.c_int).value = 2
    for _ in range(100):
        p.poll()
    asks = [c for c in p.commands if CODECS_FILE in c]
    assert len(asks) == 10 and f'dev_{CAR.replace(":", "_")}/a2dpsrc' in asks[0]


def test_a_car_without_aptx_hd_keeps_aac_when_aptx_hd_is_picked(fw, tmp_path):
    """The user's BMW: SBC and AAC. aptX HD picked while AAC streams: refused before the link is touched (no
    SelectCodec at all), AAC kept, stock told nothing is linked so its tail saves the pick with no reconnect."""
    p = Connected(fw, tmp_path, CAR, 'BMW 6R81', BMW, 'AAC')
    assert p.pick(3) == (0, None)
    assert p.bluealsa.selected() == []


@pytest.mark.parametrize('row', [3, 2, 0, 4])  # aptX HD, Auto, LDAC Best, aptX
def test_a_car_on_sbc_gets_aac_for_a_pick_it_lacks_or_auto(fw, tmp_path, row):
    """The same car stepped down to SBC: aptX HD, LDAC, aptX or Auto walk the ordered list to the first codec it
    takes, AAC; the codecs it lacks are never asked of bluealsa."""
    p = Connected(fw, tmp_path, CAR, 'BMW 6R81', BMW, 'SBC')
    assert p.pick(row) == (0, 'AAC')
    assert p.bluealsa.selected() == ['AAC']


def test_auto_on_a_car_streaming_aac_changes_nothing(fw, tmp_path):
    p = Connected(fw, tmp_path, CAR, 'BMW 6R81', BMW, 'AAC')
    assert p.pick(2) == (0, None)


def test_auto_on_a_px7_takes_aptx_hd(fw, tmp_path):
    """A Px7 has no LDAC: Auto's list starts at LDAC, which it lacks, so aptX HD, asked first and taken."""
    p = Connected(fw, tmp_path, HEAD, 'Px7', PX7, 'SBC')
    assert p.pick(2) == (0, 'aptX-HD')
    assert p.bluealsa.selected() == ['aptX-HD']


def test_a_pick_with_the_device_s_codecs_unknown_walks_the_whole_list(fw, tmp_path):
    """No answer yet: bluealsa's own refusal does the walk (each codec it lacks refused before the stream is
    touched), as before the codecs were learned."""
    p = Page(fw, [(CAR, 'BMW 6R81', 'connected')])
    bluealsa = Bluealsa(tmp_path, CAR, BMW)
    p.links = [(CAR, 3)]
    fw.var('bt_linkstatus', C.c_ubyte).value = 1
    fw.var('fw_ctx_bt_showcoding', C.c_int).value = 1
    p.commands.clear()
    assert fw.fn('bt_quality_linked', C.c_int, C.c_int)(3) == 0
    switch, = [c for c in p.commands if PICK_FILE in c]
    assert 'for c in aptX-HD LDAC aptX AAC SBC; do' in switch
    assert bluealsa.sh(switch, {PICK_FILE: tmp_path / 'pick'}).splitlines()[-1] == 'AAC'
    assert bluealsa.selected() == ['aptX-HD', 'LDAC', 'aptX', 'AAC']


def test_the_picker_marks_and_captions_what_the_car_lacks(fw, tmp_path):
    """LDAC Best and Standard, aptX HD and aptX marked; Auto, AAC and both SBCs not. Each marked row says why under
    it while selected, with the codec streaming now; the picked aptX HD row (checked, kept for the next device)
    says what is used instead."""
    p = Connected(fw, tmp_path, CAR, 'BMW 6R81', BMW, 'AAC')
    fw.var('g_btcoding', C.c_ubyte).value = 3
    p.picker()
    assert p.lacking() == [0, 1, 3, 4]
    assert p.caption(3) == "BMW 6R81 doesn't support aptX HD: using AAC"
    assert p.caption(0) == "BMW 6R81 doesn't support LDAC: using AAC"
    assert p.caption(5) is None and p.caption(7) is None
    assert p.caption(2) == 'Best this headset supports: AAC'  # Auto's own


def test_the_marks_follow_the_codec_streaming_and_go_with_the_device(fw, tmp_path):
    p = Connected(fw, tmp_path, CAR, 'BMW 6R81', BMW, 'AAC')
    p.picker()
    p.stream('SBC')  # stepped down: the caption says so at the next poll, no layout
    p.poll()
    assert p.caption(3) == "BMW 6R81 doesn't support aptX HD: using SBC"
    p.links = []  # gone: nothing is known to be lacking
    p.poll()
    assert p.lacking() == [] and p.caption(3) is None


def test_a_px7_marks_only_ldac(fw, tmp_path):
    p = Connected(fw, tmp_path, HEAD, 'Px7', PX7, 'aptX-HD')
    p.picker()
    assert p.lacking() == [0, 1]
    assert p.caption(1) == "Px7 doesn't support LDAC: using aptX HD"


def test_a_marked_row_s_text_is_grayed_as_the_library_s(fw, tmp_path):
    """docs/server-library.md#grayed-rows: #666666 off the selection; the other rows' text is left as the theme
    gives it."""
    p = Connected(fw, tmp_path, CAR, 'BMW 6R81', BMW, 'AAC')
    p.picker()
    dim = fw.fn('bt_quality_dim', C.c_int, C.c_void_p, C.c_void_p)

    def ink(k):
        title = next(w for w in p.ui.subtree(p.choice(k)) if p.ui.kind[w] == 'hscroll_label_create')
        color = C.c_uint(0xffffffff)
        return dim(title, C.byref(color)), color.value
    assert ink(3) == (1, 0xff666666)
    assert ink(5) == (0, 0xffffffff)
    assert ink(2) == (0, 0xffffffff)


# ---- the row follows the kernel's link (docs/internals.md#bluetooth-page) ----

def acl_list(fw, *macs):
    """socket and HCIGETCONNLIST as the kernel answers them: each mac a connected ACL link (type 1, state 1)."""
    fw.mock('fw_socket', lambda *_: 7)
    def ioctl(fd, request, req, *_):
        if request & 0xffffffff != 0x400448d4:
            return -1
        C.c_uint16.from_address(req + 2).value = len(macs)
        for i, mac in enumerate(macs):
            c = req + 4 + 16 * i
            C.memmove(c + 2, bytes(reversed(bytes.fromhex(mac.replace(':', '')))), 6)
            C.c_uint8.from_address(c + 8).value = 1
            C.c_uint16.from_address(c + 10).value = 1
        return 0
    fw.mock('fw_ioctl', ioctl)


def state_text(p, row):
    return next((p.ui.props.get((w, 'tr_text')) for w in p.ui.subtree(row)
                 if (w, 'tr_text') in p.ui.props), None)


def test_a_connecting_row_whose_link_is_up_shows_connected(fw):
    """2026-11-19 (V1JBi): the kernel connected the Pixel Buds at 20:40:36Z, an AAC stream ran from 20:40:42Z, yet
    the row said Connecting for minutes: stock's last link item never reached 3 while its link thread ran. With the
    kernel holding the link, the row and the device's page say Connected."""
    p = Page(fw, [(HEAD, 'Pixel Buds', 'connecting'), (CAR, 'BMW 83828', 'paired')])
    fw.var('fw_ctx_bt_connectstatus', C.c_int).value = 1
    p.links = [(HEAD, 5)]
    acl_list(fw)  # not linked yet: Connecting, as stock says
    p.rebuild()
    p.layout()
    assert state_text(p, p.row('d0')) == 'msg_connecting'
    assert fw.fn('bt_dev_state', C.c_int, C.c_int)(0) == 2  # BT_ROW_CONNECTING
    acl_list(fw, HEAD)
    p.now += 600
    p.rebuild()
    p.layout()
    row = p.row('d0')
    assert state_text(p, row) == 'msg_connected' and geometry(row)[3] == 52
    assert p.ui.lookup(row, '_bt_dot', 1) and not p.ui.lookup(row, '_bt_spin', 1)
    assert fw.fn('bt_dev_state', C.c_int, C.c_int)(0) == 3  # BT_ROW_CONNECTED
    assert state_text(p, p.row('d1')) == 'msg_paired'  # another device's row is stock's


def test_the_link_coming_up_rebuilds_the_page(fw):
    """Stock rebuilds its rows only when its own state moves, and here it does not: the page's poll asks for the
    rebuild (g_btsetting_flag) when the kernel's link to the last item comes up or goes."""
    p = Page(fw, [(HEAD, 'Pixel Buds', 'connecting')])
    fw.var('fw_ctx_bt_connectstatus', C.c_int).value = 1
    p.links = [(HEAD, 5)]
    acl_list(fw)
    p.poll()
    flag = fw.var('g_btsetting_flag', C.c_ubyte)
    flag.value = 0
    acl_list(fw, HEAD)
    p.poll()
    assert flag.value == 1


def test_a_listed_acl_is_only_a_connected_one(fw):
    """HCIGETCONNLIST's hci_conn_info: an LE link (type 0x80) or one still connecting (state 5, BT_CONNECT) to the
    address is not a connected ACL link."""
    def req(kind, state):
        b = bytearray(4 + 16)
        b[2] = 1
        b[4 + 2:4 + 8] = bytes(reversed(bytes.fromhex(HEAD.replace(':', ''))))
        b[4 + 8], b[4 + 10] = kind, state
        return bytes(b)
    listed = fw.fn('bt_acl_listed', C.c_int, C.c_char_p, C.c_int, C.c_char_p)
    assert listed(req(1, 1), 8, HEAD.encode()) == 1
    assert listed(req(1, 1), 8, HEAD.lower().encode()) == 1
    assert listed(req(0x80, 1), 8, HEAD.encode()) == 0
    assert listed(req(1, 5), 8, HEAD.encode()) == 0
    assert listed(req(1, 1), 8, CAR.encode()) == 0


def test_a_row_stock_labels_connecting_shows_connected_once_its_link_is_listed(fw):
    """V1JCi, 2026-11-19 22:14 (test A): a tap on the found Pixel Buds; the link list went 1, 3, 5, then 3 at
    22:14:49Z with the kernel's link up, while stock's link thread ran on to 22:15:02Z. Stock labels a row
    msg_connecting while its link thread runs, whatever the item's status, so the row moved to My Devices said
    Connecting and stayed so. Status 3 is connected (bt_dev_state), and the label follows it at each rebuild."""
    p = Page(fw, [(HEAD, 'Pixel Buds', 'found')])
    fw.var('fw_ctx_bt_connectstatus', C.c_int).value = 1
    p.links = [(HEAD, 1)]
    acl_list(fw)
    p.rebuild()
    p.layout()
    p.links = [(HEAD, 5)]
    acl_list(fw, HEAD)
    p.devices[0] = (HEAD, 'Pixel Buds', 'connecting')  # stock moved it to My Devices, labelled while connecting
    p.now += 600
    p.rebuild()
    p.layout()
    assert state_text(p, p.row('d0')) == 'msg_connected'
    p.links = [(HEAD, 3)]  # stock's item connected; its thread still running, its label still connecting
    p.now += 600
    p.rebuild()
    p.layout()
    row = p.row('d0')
    assert state_text(p, row) == 'msg_connected' and geometry(row)[3] == 52
    assert fw.fn('bt_dev_state', C.c_int, C.c_int)(0) == 3


def test_a_label_stock_left_behind_asks_for_a_rebuild(fw):
    """A row built while stock's thread ran keeps its label until stock rebuilds, which it does only when its own
    state moves: the page's poll asks for one (g_btsetting_flag) while any device row's label says other than
    bt_dev_state, and stops once they agree."""
    p = Page(fw, [(HEAD, 'Pixel Buds', 'connecting')])
    fw.var('fw_ctx_bt_connectstatus', C.c_int).value = 1
    p.links = [(HEAD, 5)]
    acl_list(fw)
    p.rebuild()
    p.layout()
    p.poll()
    flag = fw.var('g_btsetting_flag', C.c_ubyte)
    flag.value = 0
    p.poll()
    assert flag.value == 0  # label and state agree: Connecting
    p.links = [(HEAD, 3)]  # no rebuild from stock
    p.poll()
    assert flag.value == 1
    flag.value = 0
    p.rebuild()
    p.layout()
    p.poll()
    assert flag.value == 0


def test_a_device_found_mid_scan_is_listed_within_a_second(fw):
    """V1JCi, 2026-11-19: the radio heard the Pixel Buds at 22:14:32.2Z (q2guard's found) and the page listed them
    at 22:14:37.2Z (page shows found): stock's page timer reads the search results (getSearchResult) only as a
    scan's count ends, so a device found early in a 10 s scan waited for its end. While the page scans, its poll
    reads them each second and rebuilds the list when they changed (stock's btmenu_rows, at once)."""
    s = Scan(fw)
    builds = []
    fw.mock('stock_btrows_trampoline', lambda *a: builds.append(s.p.now) or s.p.build())
    reads = []
    found = C.create_string_buffer(0x124)
    C.memmove(C.addressof(found) + 4, HEAD.encode() + b'\0', len(HEAD) + 1)
    listed = s.p.ui.deques[fw.var('fw_ctx_pdeq_btshowlist', C.c_uint).value]
    fw.mock('getSearchResult', lambda *_: reads.append(s.p.now) or 0)
    s.p.poll()  # the scan starts
    assert s.started == 1
    s.p.poll()
    s.p.poll()
    assert len(reads) == 1 and not builds  # read a second on; nothing new
    listed.append(C.addressof(found))  # what getSearchResult would add: the Buds heard
    s.p.poll()
    s.p.poll()
    assert len(reads) == 2 and len(builds) == 1
    fw.var('bt_searchtime', C.c_ubyte).value = 1  # stock's timer reads them itself at the count's end
    s.p.poll()
    s.p.poll()
    assert len(reads) == 2 and len(builds) == 1


def test_a_reconnect_with_no_link_thread_is_stock_s_connected_as_it_was(fw):
    """Test B the same evening (V1JCi): the lid closed (22:17:37Z, reason 3), the Buds reconnected by themselves at
    22:18:08Z (by=device), the item went straight to 3 with no status 5 and no connect started: no link thread ran,
    so stock labelled the row msg_connected itself and it said Connected. Test A above differs only in the thread
    running through 1, 3, 5, 3: here nothing is changed and no rebuild is asked for."""
    p = Page(fw, [(HEAD, 'Pixel Buds', 'connected')])
    fw.var('fw_ctx_bt_connectstatus', C.c_int).value = 0
    p.links = [(HEAD, 3)]
    acl_list(fw, HEAD)
    p.rebuild()
    p.layout()
    row = p.row('d0')
    assert state_text(p, row) == 'msg_connected' and geometry(row)[3] == 52
    assert not [a for _, a in fw.calls('widget_set_prop_str') if C.string_at(a[1]) == b'tr_text']
    p.poll()
    flag = fw.var('g_btsetting_flag', C.c_ubyte)
    flag.value = 0
    p.poll()
    assert flag.value == 0


def test_a_device_found_mid_scan_never_paints_on_another_s_row(fw):
    """V1JDi, 2026-11-19: on the pairing page during a search, a Bluetooth glyph "blended with a headphone one as the
    list shifted". A row knows its device by its place in pdeq_btshowlist (its button's name, which stock's click
    reads too), and the mid-scan read (getSearchResult) refills that list: until the list was rebuilt, on stock's
    200 ms page timer, the row titled JBL Flip 6 painted the place's new device, the Pixel Buds' headphones, and a
    tap there went to the Buds. Stock's own timer reads and rebuilds in one call (0x4c4848); so does the page now."""
    s = Scan(fw, [(CAR, 'BMW 83828', 'paired'), (SPEAKER, 'JBL Flip 6', 'found')])
    p = s.p
    p.files.files[CLASS_FILE] = f'{CAR} 2098208\n{HEAD} 2360324\n{SPEAKER} 256\n'.encode()
    p.rebuild()
    drawn = []
    fw.mock('widget_load_image', lambda w, name, *_: drawn.append(C.string_at(name).decode()) or 1)
    listed = p.ui.deques[fw.var('fw_ctx_pdeq_btshowlist', C.c_uint).value]
    buds = C.create_string_buffer(0x124)
    C.memmove(C.addressof(buds) + 4, HEAD.encode() + b'\0', len(HEAD) + 1)
    C.memmove(C.addressof(buds) + 0x24, b'Pixel Buds\0', 11)

    def found(*_):  # the Buds heard: stock's list refilled with them before the JBL
        if len(listed) == 2:
            listed.insert(1, C.addressof(buds))
            p.devices.insert(1, (HEAD, 'Pixel Buds', 'found'))
        return 0
    fw.mock('getSearchResult', found)

    def rows():
        """Each device row as painted now: its title and the kind its glyph draws."""
        out = []
        for r in p.rows():
            glyph = p.ui.lookup(r, '_bt_glyph', 1)
            if not glyph:
                continue
            drawn.clear()
            fw.fn('bt_ui_paint', None, C.c_void_p, C.c_void_p)(glyph, None)
            title = next(p.ui.text[w] for w in p.ui.subtree(r) if p.ui.kind.get(w) == 'hscroll_label_create')
            out.append((title, next(n.split('btdev_')[1].split('_')[0] for n in drawn if 'btdev_' in n)))
        return out
    p.layout()
    assert rows() == [('BMW 83828', 'car'), ('JBL Flip 6', 'bt')]
    for _ in range(4):  # the scan starts, and its results are read a second on; each frame laid out, then painted
        p.poll()
        p.layout()
        assert all(kind == {'BMW 83828': 'car', 'Pixel Buds': 'headphones', 'JBL Flip 6': 'bt'}[t] for t, kind in rows()), rows()
    assert [t for t, _ in rows()] == ['BMW 83828', 'Pixel Buds', 'JBL Flip 6']


ARCTIS = '20:AF:1B:18:FD:03'
BMW_CAR = '60:E6:F0:A1:1F:09'
ADAPTER = 'A4:E8:8D:7B:40:31'
ARCTIS_INFO = ('[General]\nName=Arctis Nova Pro Wireless\nClass=0x240418\nSupportedTechnologies=BR/EDR;\nTrusted=true\n'
               'Blocked=false\n\n[DeviceID]\nSource=2\nVendor=4152\nProduct=4733\nVersion=1\n')
BMW_INFO = '[General]\nName=BMW 83828\nTrusted=true\n\n[LinkKey]\nKey=00112233445566778899AABBCCDDEEFF\nType=4\nPINLength=0\n'


class BlueZ:
    """BlueZ under sh for the page's ask (SEEN_FILE): a dbus-send answering Device1's Properties.Get from an object
    set (a device's property missing is BlueZ's error, as RSSI is for a device no discovery heard) and logging
    Adapter1.RemoveDevice, and BlueZ's store (ADAPTER/ADDRESS/info) in a directory for /mnt/data/bluetooth."""
    def __init__(self, tmp_path, devices):
        self.tmp = tmp_path
        self.bin, self.objs, self.store = tmp_path / 'bin', tmp_path / 'objs', tmp_path / 'store'
        for d in (self.bin, self.objs, self.store):
            d.mkdir()
        for mac, (props, info) in devices.items():
            obj = self.objs / ('dev_' + mac.replace(':', '_'))
            obj.mkdir()
            for k, v in props.items():
                (obj / k).write_text(v)
            if info is not None:
                (self.store / ADAPTER / mac).mkdir(parents=True)
                (self.store / ADAPTER / mac / 'info').write_text(info)
        (self.bin / 'dbus-send').write_text(
            '#!/bin/sh\n'
            f'[ "$5" = org.bluez.Adapter1.RemoveDevice ] && [ "$4" = /org/bluez/hci0 ] && echo "${{6#objpath:}}" >> {tmp_path}/removed && exit 0\n'
            '[ "$5" = org.freedesktop.DBus.Properties.Get ] && [ "$6" = string:org.bluez.Device1 ] || exit 1\n'
            f'v={self.objs}/${{4##*/}}/${{7#string:}}\n'
            '[ -f "$v" ] || { echo "Error org.freedesktop.DBus.Error.InvalidArgs: No such property" >&2; exit 1; }\n'
            'echo "method return time=1 sender=:1.2"; echo "   variant       $(cat "$v")"\n')
        (self.bin / 'dbus-send').chmod(0o755)

    def run(self, command):
        """The command, as the page's shell runs it, in the foreground: what it wrote."""
        out = self.tmp / 'seen'
        command = (command.replace(SEEN_FILE, str(out)).replace('/mnt/data/bluetooth', str(self.store))
                   .replace(') >/dev/null 2>&1 &', ') >/dev/null 2>&1'))
        subprocess.run(['sh', '-c', command], check=True, env={**os.environ, 'PATH': f'{self.bin}:{os.environ["PATH"]}'})
        return out.read_bytes()

    def removed(self):
        f = self.tmp / 'removed'
        return f.read_text().split() if f.exists() else []


def other_devices(p):
    names = p.names()
    return names[names.index('Other Devices') + 1:names.index('Audio')]


def test_other_devices_lists_what_the_scan_heard_and_a_stale_record_is_removed(fw, tmp_path):
    """2026-11-20 (V1N6i): the Arctis Nova Pro Wireless stayed in Other Devices 20 miles from it. BlueZ's store held
    a record for it, Trusted with no [LinkKey] (a pairing that never finished), which BlueZ loads at every start;
    libbtctl's btctl_get_device_list lists every Device1 BlueZ has, a paired flag from Paired, so it was listed as
    found whatever the radio heard. A found device is now listed once BlueZ has an RSSI for it (a discovery heard
    it), and a stored record no key was ever saved in is removed through BlueZ."""
    devices = [(BMW_CAR, 'BMW 83828', 'paired'), (ARCTIS, 'Arctis Nova Pro Wireless', 'found'),
               (SPEAKER, 'JBL Flip 6', 'found')]
    s = Scan.__new__(Scan)
    s.fw, s.started = fw, 0
    s.p = p = Page(fw, devices, answer=False)
    fw.mock('btStartSearch', s.start)
    p.ui.windows.append(p.win)
    bluez = BlueZ(tmp_path, {
        BMW_CAR: ({'Paired': 'boolean true', 'Trusted': 'boolean true', 'Connected': 'boolean false'}, BMW_INFO),
        ARCTIS: ({'Paired': 'boolean false', 'Trusted': 'boolean true', 'Connected': 'boolean false',
                  'Class': 'uint32 2360344'}, ARCTIS_INFO),
        SPEAKER: ({'Paired': 'boolean false', 'Connected': 'boolean false', 'RSSI': 'int16 -52'}, None)})
    assert other_devices(p) == []  # nothing yet: BlueZ has not said what the scan heard
    asks = [c for c in p.commands if SEEN_FILE in c]
    assert len(asks) == 1 and BMW_CAR not in asks[0]  # only the found ones are asked about
    p.files.files[SEEN_FILE] = bluez.run(asks[0])
    p.poll()
    assert other_devices(p) == ['d2']
    assert 'd0' in p.names()
    assert bluez.removed() == ['/org/bluez/hci0/dev_20_AF_1B_18_FD_03']


def test_a_stale_record_is_kept_while_a_key_a_link_or_a_connect_says_it_is_real(fw, tmp_path):
    """Nothing with a key in its record, connected, or while a connect runs is removed; each is still not listed
    until a scan hears it."""
    keyed, linked = 'AA:BB:CC:DD:EE:04', 'AA:BB:CC:DD:EE:05'
    devices = [(ARCTIS, 'Arctis Nova Pro Wireless', 'found'), (keyed, 'Keyed', 'found'), (linked, 'Linked', 'found')]
    bluez = BlueZ(tmp_path, {
        ARCTIS: ({'Paired': 'boolean false', 'Connected': 'boolean false'}, ARCTIS_INFO),
        keyed: ({'Paired': 'boolean false', 'Connected': 'boolean false'}, BMW_INFO.replace('BMW 83828', 'Keyed')),
        linked: ({'Paired': 'boolean false', 'Connected': 'boolean true'}, ARCTIS_INFO.replace('Arctis', 'Linked'))})
    fw.var('fw_ctx_bt_connectstatus', C.c_int).value = 1  # a connect running: nothing is removed
    p = Page(fw, devices, answer=False)
    assert bluez.run(next(c for c in p.commands if SEEN_FILE in c)).split(b'\n')[1:] == [b'']
    assert bluez.removed() == [] and other_devices(p) == []
    fw.var('fw_ctx_bt_connectstatus', C.c_int).value = 0
    p.commands.clear()
    p.rebuild()
    p.files.files[SEEN_FILE] = bluez.run(next(c for c in p.commands if SEEN_FILE in c))
    assert bluez.removed() == ['/org/bluez/hci0/dev_20_AF_1B_18_FD_03']
    p.rebuild()
    assert other_devices(p) == []


def test_a_device_the_scan_heard_stays_listed_this_open(fw):
    """Once heard, a found device stays on the list while the page is open: BlueZ drops its RSSI as each discovery
    ends, and with a device connected the page does not scan again."""
    p = Page(fw, [(CAR, 'BMW 6R81', 'paired'), (SPEAKER, 'JBL Flip 6', 'found')])
    assert other_devices(p) == ['d1']
    asked = len([c for c in p.commands if SEEN_FILE in c])
    p.answer = False
    p.files.files[SEEN_FILE] = b'#999\n'
    p.rebuild()
    assert other_devices(p) == ['d1'] and len([c for c in p.commands if SEEN_FILE in c]) == asked
