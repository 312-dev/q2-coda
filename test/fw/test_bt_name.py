"""bt_name.c: the Q2's Bluetooth name. Vamp by default (main.conf's Name, BlueZ's Alias of ""), a chosen one in
config.ini's [Q2POD] BTNAME, set as BlueZ's adapter Alias over D-Bus at each Bluetooth on and at once after a
rename; Settings > Bluetooth's Name row shows it and opens the letter strip in a text box's form."""
import ctypes as C
import os
import pathlib
import subprocess

import pytest

from conftest import O, STRIP_SAVE
from test_nav_library_pages import Config, Ui
from test_search_strip import HOLD_MS, Strip

KEY_RETURN, KEY_PLAY = (O[k] for k in ('KEY_RETURN', 'KEY_PLAY'))
EVT_CLICK = O['EVT_CLICK']
LOWER, UPPER, DIGITS = 0, 1, 2


@pytest.fixture
def ui(fw):
    return Ui(fw)


class System:
    """system()'s commands, each answered 0."""
    def __init__(self, fw):
        self.commands = []
        fw.mock('fw_system', lambda cmd, *_: self.commands.append(C.string_at(cmd).decode()) or 0)


def name(fw):
    out = C.create_string_buffer(64)
    return fw.fn('bt_name', C.c_char_p, C.c_char_p, C.c_uint)(out, 64).decode()


@pytest.mark.parametrize('saved, shown', [(None, 'Vamp'), (b'', 'Vamp'), (b'Car Kit', 'Car Kit'),
                                          (b' x', 'Vamp'), (b'a' * 31, 'Vamp'), (b"it's", 'Vamp'),
                                          (b'a' * 30, 'a' * 30), (b'Q2', 'Q2')])
def test_the_name_is_the_saved_one_or_q2_muse(fw, saved, shown):
    Config(fw, **({} if saved is None else {'BTNAME': saved}))
    assert name(fw) == shown


def bt_page(fw, ui, **config):
    """Settings > Bluetooth with Bluetooth on: stock's five head rows, s1 its Bluetooth Name, as btmenu_rows
    builds them, then ringnav_btrows."""
    settings = Config(fw, **config)
    win = ui.new('window_create', 0, 0, 0, 0, 0)
    ui.name[win] = 'bluetooth_page'
    view = ui.new('scroll_view_create', win, 0, 26, 375, 294)
    ui.name[view] = 'scroll_view_bluetooth'

    def build(*_):
        for i in range(5):
            item = ui.new('list_item_create', view, 0, 0, 375, 78)
            ui.name[item] = f's{i}'
            ui.new('button_create', item, 20, 0, 335, 78)
        return 0
    fw.mock('stock_btrows_trampoline', build)
    fw.var('g_bluetoothflag', C.c_ubyte).value = 1
    fw.fn('ringnav_btrows', C.c_int, C.c_void_p, C.c_void_p)(win, None)
    return settings, view


def name_row(ui, view):
    row = ui.kids[view][1]
    labels = [ui.text.get(w) for w in ui.subtree(row) if ui.kind[w] == 'hscroll_label_create']
    return row, labels


def test_stock_s_bluetooth_name_row_becomes_name_with_the_name_as_its_value(fw, ui):
    _, view = bt_page(fw, ui, BTNAME=b'Car Kit')
    names = [ui.name.get(r, '') for r in ui.kids[view]]
    assert names[:2] == ['s0', ''] and 's1' not in names  # its place, stock's gone (bt_ui.c orders the rest)
    row, labels = name_row(ui, view)
    assert labels == ['Name', 'Car Kit']
    assert [a[2] for _, a in fw.calls('widget_resize') if a[0] == row] == [78]  # stock's row's height


def test_bluetooth_off_keeps_the_name_row_after_the_switch(fw, ui):
    Config(fw, BTNAME=b'Car Kit')
    win = ui.new('window_create', 0, 0, 0, 0, 0)
    view = ui.new('scroll_view_create', win, 0, 26, 375, 294)
    ui.name[view] = 'scroll_view_bluetooth'
    fw.mock('stock_btrows_trampoline', lambda *_: ui.new('list_item_create', view, 0, 0, 375, 78) and 0)
    fw.var('g_bluetoothflag', C.c_ubyte).value = 0
    fw.fn('ringnav_btrows', C.c_int, C.c_void_p, C.c_void_p)(win, None)
    row, labels = name_row(ui, view)
    assert labels == ['Name', 'Car Kit'] and [a[2] for _, a in fw.calls('widget_resize') if a[0] == row] == [78]


class Rename(Strip):
    """The Name row clicked: the strip on its own page."""
    def __init__(self, fw, ui, **config):
        self.fw, self.ui = fw, ui
        self.system = System(fw)
        self.config, self.view = bt_page(fw, ui, **config)
        row, _ = name_row(ui, self.view)
        ui.handler(ui.kids[row][0], EVT_CLICK)()
        self.attach(fw, ui)  # the strip's keys, typing and erasing (test_search_strip.Strip)



def test_the_name_row_opens_the_strip_as_a_text_box_on_the_name(fw, ui):
    r = Rename(fw, ui, BTNAME=b'Car Kit')
    assert ui.name[ui.top()] == 'searchstrip_page' and r.ss.text == b'Car Kit' and r.ss.cur == 7
    assert r.ss.box == r.ss.page and r.ss.set == LOWER and 'Bluetooth' in ui.text.values()


def test_play_saves_applies_and_shows_the_new_name(fw, ui):
    r = Rename(fw, ui)
    assert r.ss.text == b'Vamp'
    r.erase(7)
    r.type('My Q2')
    assert r.ss.text == b'My Q2'
    r.key(KEY_PLAY)
    assert r.config.values[b'BTNAME'] == b'My Q2' and ui.toasts[-1] == 'Renamed' and ui.backs == 1
    assert len(r.system.commands) == 1 and "'variant:string:My Q2'" in r.system.commands[0]
    assert name_row(ui, r.view)[1] == ['Name', 'My Q2']  # the row under the strip says it at once


def test_q2_muse_again_is_saved_as_none(fw, ui):
    r = Rename(fw, ui, BTNAME=b'Old')
    r.erase(3)
    r.type('Vamp')
    r.key(KEY_PLAY)
    assert r.config.values[b'BTNAME'] == b'' and "'variant:string:'" in r.system.commands[0] and ui.backs == 1


def test_edge_spaces_go_and_an_empty_name_is_refused(fw, ui):
    r = Rename(fw, ui)
    r.erase(7)
    r.type('  ')
    r.key(KEY_PLAY)
    assert ui.toasts[-1] == "Name can't be empty" and not r.config.written and not r.system.commands and not ui.backs
    r.type('a ')
    r.key(KEY_PLAY)
    assert r.config.values[b'BTNAME'] == b'a' and ui.backs == 1


def test_the_name_stops_at_30_characters(fw, ui):
    r = Rename(fw, ui)
    r.type('x' * 40)
    assert r.ss.text == b'Vamp' + b'x' * 26


def test_a_held_return_leaves_the_name_as_it_was(fw, ui):
    r = Rename(fw, ui, BTNAME=b'Car Kit')
    r.type('z')
    r.down(ui.top(), KEY_RETURN)
    r.now[0] += HOLD_MS
    ui.fire('search_strip__ss_hold_tick')
    assert ui.backs == 1 and not r.config.written and not r.system.commands


def test_bluetooth_on_sets_the_name(fw):
    """openBluetooth's registration (headset_status_register) also applies the saved name."""
    Config(fw, BTNAME=b'Car Kit')
    s = System(fw)
    fw.fn('headset_status_register', None)()
    assert len(s.commands) == 1 and "'variant:string:Car Kit'" in s.commands[0]
    assert [a[0] for _, a in fw.calls('btctl_device_status_callback')]  # stock's registration still made


@pytest.mark.parametrize('saved, alias', [(b'Car #1 $HOME', 'Car #1 $HOME'), (b'', ''), (b"x'y", '')])
def test_the_command_hands_dbus_send_the_alias_as_typed(fw, tmp_path, saved, alias):
    """The command under sh, a stand-in dbus-send recording its arguments: the Alias reaches it whole, $ and #
    untouched; a name that couldn't be saved is sent as none."""
    Config(fw, BTNAME=saved)
    s = System(fw)
    fw.fn('bt_name_apply', None)()
    bin_ = tmp_path / 'bin'
    bin_.mkdir()
    log = tmp_path / 'args'
    (bin_ / 'dbus-send').write_text(f'#!/bin/sh\nfor a; do printf "%s\\n" "$a"; done > {log}\n')
    (bin_ / 'usleep').write_text('#!/bin/sh\n')
    for f in bin_.iterdir():
        f.chmod(0o755)
    command = s.commands[0].replace(') >/dev/null 2>&1 &', ') >/dev/null 2>&1')  # run it to its end
    subprocess.run(['sh', '-c', command], check=True, env={**os.environ, 'PATH': f'{bin_}:{os.environ["PATH"]}'})
    args = pathlib.Path(log).read_text().splitlines()
    assert args == ['--system', '--print-reply', '--dest=org.bluez', '/org/bluez/hci0',
                    'org.freedesktop.DBus.Properties.Set', 'string:org.bluez.Adapter1', 'string:Alias',
                    f'variant:string:{alias}']


@pytest.mark.parametrize('saved, written', [(b'Car #1 $HOME', 'Car #1 $HOME'), (b'', 'Vamp'), (b"x'y", 'Vamp')])
def test_the_controller_is_given_the_name_itself(fw, tmp_path, saved, written):
    """The Alias set, the controller's own local name is written too (hciconfig hci0 name): on 2026-11-22 after a
    boot BlueZ's Name and Alias read Vamp while the RTL8733BS answered a name request with its ROM's RT_BT, the
    name the car showed, and an Alias of "" or one BlueZ already holds changes nothing in BlueZ."""
    Config(fw, BTNAME=saved)
    s = System(fw)
    fw.fn('bt_name_apply', None)()
    bin_ = tmp_path / 'bin'
    bin_.mkdir()
    log = tmp_path / 'hci'
    (bin_ / 'dbus-send').write_text('#!/bin/sh\n')
    (bin_ / 'hciconfig').write_text(f'#!/bin/sh\nfor a; do printf "%s\\n" "$a"; done >> {log}\n')
    (bin_ / 'usleep').write_text('#!/bin/sh\n')
    for f in bin_.iterdir():
        f.chmod(0o755)
    command = s.commands[0].replace(') >/dev/null 2>&1 &', ') >/dev/null 2>&1')  # run it to its end
    subprocess.run(['sh', '-c', command], check=True, env={**os.environ, 'PATH': f'{bin_}:{os.environ["PATH"]}'})
    assert pathlib.Path(log).read_text().splitlines() == ['hci0', 'name', written]


def test_the_name_s_return_key_is_a_check(fw, ui):
    """Bluetooth's Name saves a name: the strip's return key shows the check (search_strip.c ss_ret_kind)."""
    from test_search_strip import mark
    r = Rename(fw, ui)
    assert r.ss.ret == STRIP_SAVE and mark(fw, r) == 'save_w'
