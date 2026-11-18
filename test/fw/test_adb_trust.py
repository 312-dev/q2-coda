"""patch/adb_trust.c (docs/adb.md#authorization): adbd's question about an unknown computer on the confirmation card's
wide form, what each answer sends adbd and keeps, the status pill after Allow, and Settings > General > Developer
Options > Trusted Computers."""
import base64
import ctypes as C
import hashlib
import struct

import pytest

from conftest import O, Files
from test_confirm import Card, rect, repeat
from test_nav_hold import Ui, ui  # noqa: F401 (fixtures)
from test_nav_painters import Canvas, scratch, st, theme  # noqa: F401 (fixtures)

KEY = base64.b64encode(b'an adb public key, as adb writes it').decode()
LINE = f'{KEY} grayson@MacBook-Pro'
MD5 = hashlib.md5(base64.b64decode(KEY)).digest()
KEYS, ADDED = '/mnt/data/adb_keys', '/mnt/data/adb_keys.added'
NOW = 1795003200  # 2026-11-18T12:00:00Z
KEY_RETURN = O['KEY_RETURN']


@pytest.fixture(scope='module')
def library(dev_library):
    """adb_trust.c is a --dev build's alone."""
    return dev_library


class Adb:
    """adbd's socket as usb_ui.c's auth_poll reaches it (connected, then a question), the files, the clock and
    MD5, over a card harness (test_confirm.Card)."""
    def __init__(self, fw, ui, files=None):
        theme(fw)
        self.fw, self.ui, self.sent, self.inbox = fw, ui, [], []
        self.files = Files(fw, dict(files or {}))
        self.card = Card(fw, ui, [5000])
        md5 = [hashlib.md5()]
        fw.mock('fw_MD5_Init', lambda *_: md5.__setitem__(0, hashlib.md5()) or 1)
        fw.mock('fw_MD5_Update', lambda ctx, data, n, *_: md5[0].update(C.string_at(data, n)) or 1)
        fw.mock('fw_MD5_Final', lambda out, *_: C.memmove(out, md5[0].digest(), 16) and 1)
        fw.mock('fw_time', lambda *_: NOW)
        fw.mock('fw_socket', lambda *_: 7)
        fw.mock('fw_connect', lambda *_: 0)
        fw.mock('fw_recv', self.recv)
        fw.mock('fw_send', lambda fd, data, n, *_: self.sent.append(C.string_at(data, n).decode()) or n)
        self.poll = fw.fn('usb_ui__auth_poll', None)
        self.poll()  # connects

    def recv(self, fd, out, size, *_):
        if not self.inbox:
            C.c_int.from_address(self.fw.fn('fw___errno_location', C.c_void_p)()).value = 11  # EAGAIN
            return -1
        data = self.inbox.pop(0)
        C.memmove(out, data, len(data))
        return len(data)

    def ask(self, line=LINE):
        self.inbox.append(b'PK' + line.encode() + b'\0')
        self.poll()

    def pill(self):
        """vol_pill.c's message: (glyph, words) while one is up."""
        raw = bytes(self.fw.var('vol_pill__vp', C.c_char * 90))
        glyph, msg = raw[18:42].split(b'\0')[0], raw[42:90].split(b'\0')[0]
        return glyph.decode(), msg.decode()


def items(card):
    return card.items()


def test_an_unknown_computer_asks_on_the_wide_card_allow_lit_and_always_on(fw, ui, st):
    """The card names the computer by its key's comment in the body, its key's short fingerprint under it, the switch
    "Always allow this computer" on, Deny and Allow side by side, Allow lit as it opens."""
    adb = Adb(fw, ui)
    adb.ask()
    card = adb.card
    switch, deny, allow = items(card)
    assert card.selected == 2 and adb.sent == []
    assert [rect(ui, i)[3] for i in (switch, deny, allow)] == [34, 20, 20]  # the answers stacked in the buttons' band
    canvas = card.paint()
    texts = [t[0] for t in canvas.texts]
    fp = f'Key {MD5[0]:02X}:{MD5[1]:02X}:{MD5[2]:02X}:{MD5[3]:02X} … {MD5[14]:02X}:{MD5[15]:02X}'
    assert texts == ['Allow this computer?', 'grayson@MacBook-Pro', ' wants developer access over USB.', fp]
    h = 16 + 24 + 6 + 19 + 2 + 17 + 10 + 34 + 10 + 40 + 16
    assert [b[0] for b in canvas.boxes if b[2] == 26] == [(18, 168 - h // 2, 339, h)]
    rows = Canvas(fw)
    fw.fn('confirm_rows_paint', None, C.c_void_p, C.c_void_p)(card.view, rows.addr)
    buttons = [b[0] for b in rows.boxes if b[2] == 20]
    assert [(b[2], b[3]) for b in buttons] == [(146, 40), (147, 40)] and buttons[1][0] - buttons[0][0] == 146 + 10
    assert 'On' not in texts and not any(t[0].endswith(' s') for t in rows.texts)  # no countdown on the wide form


@pytest.mark.parametrize('always', [1, 0])
def test_allow_lets_the_computer_in_and_always_keeps_its_key_with_the_day(fw, ui, st, always):
    """Allow answers OK, first adding the key's line to adb_keys and its day to adb_keys.added with the switch on;
    off, only this connection is let in and nothing is kept. Either way the status pill says the host was allowed."""
    adb = Adb(fw, ui)
    adb.ask()
    switch, deny, allow = items(adb.card)
    if not always:
        ui.click(switch)
    ui.click(allow)
    ui.due()
    assert adb.sent == ['OK']
    kept = adb.files.files.get(KEYS)
    assert kept == ((LINE + '\n').encode() if always else None)
    assert adb.files.files.get(ADDED) == (MD5[:8] + struct.pack('<I', NOW) if always else None)
    assert adb.pill() == ('adbpill', 'MacBook-Pro allowed')


def test_the_allowed_pill_shows_over_now_playing_too(fw, ui, st):
    """The volume pill stays off Now Playing (its own volume band), but "allowed" is no volume: it shows over the song,
    as the approved mock has it."""
    adb = Adb(fw, ui)
    ui.nodes[ui.wm].children.append(ui.window('playing_page'))  # page_in_use's top window
    adb.ask()
    ui.click(items(adb.card)[2])
    ui.due()
    canvas = Canvas(fw, char_w=5)
    fw.fn('vol_pill_paint', None, C.c_void_p, C.c_void_p)(None, canvas.addr)  # the status row's paint, Now Playing on top
    assert adb.sent == ['OK'] and adb.pill() == ('adbpill', 'MacBook-Pro allowed')
    assert 'MacBook-Pro allowed' in [t[0] for t in canvas.texts]


@pytest.mark.parametrize('how', ['deny', 'return', 'timeout'])
def test_deny_return_or_30_s_unanswered_leave_the_computer_out(fw, ui, st, how):
    """Nothing lets a computer in but a press on the Q2: Deny, Return or 30 s with no answer send NO and keep
    nothing."""
    adb = Adb(fw, ui)
    adb.ask()
    switch, deny, allow = items(adb.card)
    if how == 'deny':
        ui.click(deny)
    elif how == 'return':
        ui.key(adb.card.dialog, KEY_RETURN)
    else:
        adb.card.now[0] += 29_900
        assert repeat(ui, 'feedback__card_tick') == [8]
        adb.card.now[0] += 100
        assert repeat(ui, 'feedback__card_tick') == [0]
    ui.due()
    assert adb.sent == ['NO'] and KEYS not in adb.files.files and adb.pill() == ('', '')


def test_a_tap_on_either_half_of_the_buttons_band_answers_that_button(fw, ui, st):
    """Two buttons over the band take a tap, left Deny and right Allow, so no tap in it reaches the rows stacked
    under it (a tap low in the band would otherwise land on Allow's row)."""
    adb = Adb(fw, ui)
    adb.ask()
    card = adb.card
    buttons = [k for k in ui.kids(card.dialog) if ui.nodes[k].kind == 'button']
    left, right = sorted(buttons, key=lambda b: rect(ui, b)[0])
    lr, rr = rect(ui, left), rect(ui, right)
    assert lr[1] == rr[1] and lr[3] == rr[3] == 40 and lr[0] + lr[2] == rr[0]
    ui.click(left)
    ui.due()
    assert adb.sent == ['NO']


def test_another_card_up_answers_no_and_adbd_going_closes_the_card(fw, ui, st):
    adb = Adb(fw, ui)
    assert adb.card.ask('Restart?', ['Cancel', 'Restart'])
    adb.ask()
    assert adb.sent == ['NO']
    fw.fn('confirm_close', None)()
    adb.sent.clear()
    adb.ask()
    fw.mock('fw_recv', lambda *_: 0)  # adbd closed the socket
    adb.poll()
    ui.due()
    assert adb.sent == [] and not fw.fn('confirm_up', C.c_int)()


# Settings > General > Developer Options > Trusted Computers

def other(name):
    key = base64.b64encode(f'{name} key'.encode()).decode()
    return key, hashlib.md5(base64.b64decode(key)).digest()


def trusted(fw, ui):
    a, ma = other('thinkpad')
    b, mb = other('macbook')
    files = {KEYS: f'{a} GRAYTHINKPAD\n{b} grayson@MacBook-Pro\n'.encode(),
             ADDED: ma[:8] + struct.pack('<I', NOW - 5 * 86400) + mb[:8] + struct.pack('<I', NOW)}
    adb = Adb(fw, ui, files)
    fw.mock('fw_localtime_r', lambda t, out, *_: localtime(t, out))
    fw.fn('adb_trust__trust_open', C.c_int, C.c_void_p, C.c_void_p)(None, None)
    page = next(w for w, x in ui.nodes.items() if x.name == 'adbtrust_page')
    return adb, page, (a, ma), (b, mb)


def localtime(t, out):
    """glibc's localtime_r in UTC: struct tm's ints (sec, min, hour, mday, mon, year, wday, yday, isdst, gmtoff), as the Car Remote's tests."""
    import time
    tm = time.gmtime(C.c_long.from_address(t).value)
    vals = [tm.tm_sec, tm.tm_min, tm.tm_hour, tm.tm_mday, tm.tm_mon - 1, tm.tm_year - 1900, (tm.tm_wday + 1) % 7,
            tm.tm_yday - 1, 0, 0]
    C.memmove(out, struct.pack('<10i', *vals), 40)
    return out


def inside(ui, w, page):
    while w:
        if w == page:
            return True
        w = ui.nodes[w].parent
    return False


def labels(ui, page):
    rows = [w for w, x in ui.nodes.items() if x.kind == 'list_item' and inside(ui, w, page)]
    return [[ui.nodes[c].text for c in ui.kids(r) if ui.nodes[c].text] for r in rows]


def test_trusted_computers_lists_each_with_its_day_then_remove_all(fw, ui, st):
    adb, page, *_ = trusted(fw, ui)
    assert labels(ui, page) == [['GRAYTHINKPAD', 'Nov 13'], ['grayson@MacBook-Pro', 'Today'], [], ['Remove All'], []]


@pytest.mark.parametrize('which', [0, 1, 'all'])
def test_removing_asks_then_drops_the_key_and_its_day(fw, ui, st, which):
    """Centre on a computer asks "Remove ...?", Cancel first; Remove drops its line from adb_keys and its record
    from adb_keys.added, the other computer kept. Remove All takes both files."""
    adb, page, (a, ma), (b, mb) = trusted(fw, ui)
    rows = [w for w, x in ui.nodes.items() if x.kind == 'list_item' and inside(ui, w, page) and ui.nodes[w].on]
    ui.click(rows[2 if which == 'all' else which])
    assert adb.card.selected == 0  # Cancel
    ui.click(adb.card.items()[1])
    ui.due()
    if which == 'all':
        assert KEYS not in adb.files.files and ADDED not in adb.files.files
        return
    keep, kept_md5 = (b, mb) if which == 0 else (a, ma)
    assert adb.files.files[KEYS].decode().split('\n')[0].split(' ')[0] == keep
    assert adb.files.files[ADDED][:8] == kept_md5[:8] and len(adb.files.files[ADDED]) == 12
