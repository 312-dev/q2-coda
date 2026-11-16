"""Settings > Car Remote, patch/carremote_page.c (docs/internals.md#car-remote, docs/oled-design.md#car-remote): the
switch and its caption, Pair a Phone, the phones' rows with their link and when each was last seen, a phone's page
(Status, Paired, Last Connected, Rename, Forget Phone), the how-to page and the toast naming a paired phone. The
daemon's files are a dict (conftest.Files); AWTK is test_nav_library_pages' fake, the wall clock UTC."""
import calendar
import ctypes as C
import struct
import time

from conftest import O, mcl  # noqa: F401
from test_coda_rc_shim import ONLINE, PAIR, PAIR_END, PEERS, Q2, peer
from test_nav_library_pages import call, empty_state, signed, ui  # noqa: F401
from test_nav_hold import recs  # noqa: F401

SEEN = '/mnt/data/coda-rc/seen'
NOW = calendar.timegm((2026, 11, 16, 8, 24, 0))  # a Monday
FRIDAY = calendar.timegm((2026, 11, 13, 19, 0, 0))
PAIRED = calendar.timegm((2026, 11, 12, 9, 0, 0))
A, B = b'\x01' * 8, b'\x02' * 8


def paired(name, id_, created=PAIRED):
    return peer(name, id_)[:72] + struct.pack('<I', created)


class Car(Q2):
    """Q2 with the wall clock at NOW (UTC as local time), the Wi-Fi address 192.168.1.30 and Settings > Car Remote."""
    def __init__(self, fw, ui, recs, on=1, phones=b'', online=b'', seen=b''):
        super().__init__(fw, ui, recs, on=on)
        self.wall = NOW
        fw.mock('fw_time', lambda *_: self.wall)
        fw.mock('fw_localtime_r', self.local)
        fw.mock('fw_socket', lambda *_: 7)
        fw.mock('fw_ioctl', self.ifaddr)
        fw.mock('fw_close', lambda *_: 0)
        fw.mock('widget_move_resize', lambda w, x, y, ww, h, *_: struct.pack_into('<4i', ui.blocks[w], 0, *map(signed, (x, y, ww, h))))
        fw.mock('widget_resize', lambda w, ww, h, *_: struct.pack_into('<2i', ui.blocks[w], 8, *map(signed, (ww, h))))
        if phones:
            self.files[PEERS] = phones
        if online:
            self.files[ONLINE] = online
        if seen:
            self.files[SEEN] = seen

    @staticmethod
    def local(at, out, *_):
        tm = time.gmtime(C.c_long.from_address(at).value)
        fields = [tm.tm_sec, tm.tm_min, tm.tm_hour, tm.tm_mday, tm.tm_mon - 1, tm.tm_year - 1900, (tm.tm_wday + 1) % 7,
                  tm.tm_yday - 1, 0, 0]  # tm_wday counts from Sunday; tm_gmtoff (index 9 on the Q2) 0
        for k, v in enumerate(fields):
            C.c_int.from_address(out + 4 * k).value = v
        return out

    @staticmethod
    def ifaddr(fd, request, ifr, *_):
        assert request & 0xffffffff == 0x8915 and C.string_at(ifr) == b'wlan0'
        C.memmove(ifr + 16, bytes([2, 0, 0, 0, 192, 168, 1, 30]), 8)
        return 0


def lists(ui, page):
    """page's lists (their scroll views), the empty state's button left out."""
    return [w for w in ui.subtree(page) if ui.kind[w] == 'scroll_view_create'
            and ui.name.get(ui.parent(w)) != '_empty_row']


def rows(ui, page):
    """The newest list's rows as (caption, detail or None); a gap (no label, no click) left out."""
    out = []
    for item in ui.kids[lists(ui, page)[-1]]:
        labels = [ui.text.get(k) for k in ui.kids[item] if ui.kind[k] == 'hscroll_label_create']
        if labels:
            out.append((labels[0], (labels + [None])[1]))
    return out


def gaps(ui, page):
    """The heights of the newest list's gap rows, and where each stands among the children."""
    return [(i, geometry(ui, w)[3]) for i, w in enumerate(ui.kids[lists(ui, page)[-1]])
            if not any(ui.kind[k] == 'hscroll_label_create' for k in ui.kids[w])]


def geometry(ui, w):
    return struct.unpack_from('<4i', ui.blocks[w], 0)


def click(ui, page, row):
    return ui.handler(ui.kids[lists(ui, page)[-1]][row], O['EVT_CLICK'])()


def stops(ui, page):
    """The newest list's rows the wheel stops on: those with a click (navigation.c collect), the gap passed."""
    return [w for w in ui.kids[lists(ui, page)[-1]] if any(h[0] == w and h[1] == O['EVT_CLICK'] for h in ui.handlers)]


def open_page(fw, ui):
    call(fw, 'carremote_open')
    return ui.top()


def test_a_phone_row_keeps_its_two_lines(fw, ui, mcl, recs):
    """The user's screenshot (2026-11-16 13:30): the phone row showed no Connected / Not connected. The list's
    item_height forced every row to the one-line 32 px, cutting the 52 px phone row's second line (y 31 to 51)
    and laying the next phone over it (reproduced on the emulator before the fix)."""
    Car(fw, ui, recs, phones=paired('Pixel 10 Pro', A) + paired('Galaxy S24', B))
    page = open_page(fw, ui)
    view = lists(ui, page)[-1]
    assert ui.props.get((ui.parent(view), 'item_height'), 0) == 0  # each row its own height
    items = ui.kids[view]
    assert [geometry(ui, w)[3] for w in items[-2:]] == [52, 52]  # stock stacks them at these heights
    for item in items[-2:]:
        detail = [k for k in ui.kids[item] if ui.kind[k] == 'hscroll_label_create'][1]
        _, y, _, h = geometry(ui, detail)
        assert y + h <= 52  # the second line inside its row
    assert gaps(ui, page) == [(1, 10)]  # the gap under the switch is a row with no click: the wheel passes it


def test_off_is_the_switch_alone(fw, ui, mcl, recs):
    Car(fw, ui, recs, on=0, phones=paired('Pixel 10 Pro', A))
    page = open_page(fw, ui)
    assert rows(ui, page) == [('Car Remote', None)] and empty_state(ui, page) is None


def test_no_phones_is_the_empty_state_with_pair_a_phone(fw, ui, mcl, recs):
    Car(fw, ui, recs)
    page = open_page(fw, ui)
    assert rows(ui, page) == [('Car Remote', None)]
    assert empty_state(ui, page) == ('', 'No Paired Phones', 'Open Q2 Muse Remote on your phone to pair',  # compact
                                     'Pair a Phone')
    button = ui.lookup(page, '_empty_row', 1)
    ui.handler(ui.kids[ui.kids[button][0]][0], O['EVT_CLICK'])()  # Centre on the capsule
    assert ui.name[ui.top()] == 'carhowto_page'


def test_phones_show_their_link_and_when_last_seen(fw, ui, mcl, recs):
    Car(fw, ui, recs, phones=paired('Pixel 10 Pro', A) + paired('Galaxy S24', B) + paired('Car tablet', b'\x03' * 8),
        online=A + b'\x00', seen=B + struct.pack('<I', FRIDAY))
    page = open_page(fw, ui)
    assert rows(ui, page) == [('Car Remote', None), ('Pair a Phone', None),
                              ('Pixel 10 Pro', 'Connected over Wi-Fi'),
                              ('Galaxy S24', 'Not connected \xb7 last seen Friday'),
                              ('Car tablet', 'Not connected')]  # never seen since this firmware
    assert empty_state(ui, page) is None


def test_a_phone_over_bluetooth_and_one_seen_yesterday(fw, ui, mcl, recs):
    yesterday = NOW - 86400
    Car(fw, ui, recs, phones=paired('Pixel 10 Pro', A) + paired('Galaxy S24', B), online=A + b'\x01',
        seen=B + struct.pack('<I', yesterday))
    page = open_page(fw, ui)
    assert [d for _, d in rows(ui, page)[2:]] == ['Connected over Bluetooth', 'Not connected \xb7 last seen yesterday']


def test_pair_a_phone_says_how_and_closes_when_one_pairs(fw, ui, mcl, recs):
    q2 = Car(fw, ui, recs, phones=paired('Pixel 10 Pro', A))
    page = open_page(fw, ui)
    click(ui, page, 2)  # Pair a Phone, under the gap
    howto = ui.top()
    assert ui.name[howto] == 'carhowto_page'
    q2.files[PAIR] = b'852219\nGalaxy S24\n'
    q2.tick()
    assert ui.name[ui.top()] == 'carpair_page'
    assert {'Pair with "Galaxy S24"?', '852 219', 'Enter this code in Q2 Muse Remote'} <= set(ui.shown(ui.top()))
    del q2.files[PAIR]
    q2.files[PAIR_END] = b'1\n'
    q2.tick()
    ui.fire('cr_toast')
    assert ui.toasts == ['Galaxy S24 paired'] and howto not in ui.windows


def test_a_phones_page_and_rename(fw, ui, mcl, recs):
    q2 = Car(fw, ui, recs, phones=paired('Pixel 10 Pro', A) + paired('Galaxy S24', B), online=A + b'\x00')
    page = open_page(fw, ui)
    click(ui, page, 3)  # Pixel 10 Pro: the switch, the gap, Pair a Phone, then the phones
    phone = ui.top()
    assert ui.name[phone] == 'carphone_page'
    items = [w for w in ui.kids[lists(ui, phone)[-1]] if ui.kids[w]]
    values = [(ui.text[[k for k in ui.kids[w] if ui.kind[k] == 'hscroll_label_create'][0]],
               ui.props.get((w, '_cr_value'))) for w in items]
    assert values == [('Status', 'Connected \xb7 Wi-Fi'), ('Paired', 'Nov 12'), ('Last Connected', 'Now'),
                      ('Rename', 'Pixel 10 Pro'), ('Forget Phone', None)]
    assert ui.props[(lists(ui, phone)[-1], '_ringnav_index')] == 3  # opens on Rename (child 3)
    assert gaps(ui, phone) == [(4, 12)]  # Forget Phone after a 12 px gap
    done = fw.fn('carremote_page__cr_rename_done', C.c_char_p, C.c_char_p)
    assert done(b'   ') == b"Name can't be empty"
    assert done(b' Work phone ') is None
    assert q2.files[PEERS] == paired('Work phone', A) + paired('Galaxy S24', B)


def test_each_page_counts_the_rows_the_wheel_stops_on(fw, ui, mcl, recs):
    """The phone page opened on Status, not Rename (emulator, 2026-11-16): its count took the gap in (6 for 5
    rows), so ringnav's next load found the count changed and dropped the selection (nav/lists.c load)."""
    Car(fw, ui, recs, phones=paired('Pixel 10 Pro', A) + paired('Galaxy S24', B), online=A + b'\x00')
    page = open_page(fw, ui)
    assert ui.props[(lists(ui, page)[-1], '_ringnav_count')] == len(stops(ui, page)) == 4  # switch, Pair, 2 phones
    click(ui, page, 3)
    phone = ui.top()
    view = lists(ui, phone)[-1]
    assert ui.props[(view, '_ringnav_count')] == len(stops(ui, phone)) == 5
    assert stops(ui, phone)[ui.props[(view, '_ringnav_index')]] == ui.kids[view][3]  # Rename
    ui.props[(view, '_ringnav_index')] = 4  # Forget Phone, then a rebuild (a rename, a connection) keeps it
    fw.fn('carremote_page__cr_phone_show', C.c_int, C.c_void_p)(1)
    view = lists(ui, phone)[-1]
    assert stops(ui, phone)[ui.props[(view, '_ringnav_index')]] == ui.kids[view][5]


def test_last_connected_and_paired_on_another_year(fw, ui, mcl, recs):
    old = calendar.timegm((2025, 3, 2, 9, 0, 0))
    Car(fw, ui, recs, phones=paired('Galaxy S24', B, created=old), seen=B + struct.pack('<I', FRIDAY - 7 * 86400))
    page = open_page(fw, ui)
    click(ui, page, 3)
    items = ui.kids[lists(ui, ui.top())[-1]]
    assert [ui.props.get((w, '_cr_value')) for w in items[:3]] == ['Not connected', 'Mar 2, 2025', 'Nov 6']


def test_forget_asks_on_the_card_then_drops_the_phone(fw, ui, mcl, recs):
    q2 = Car(fw, ui, recs, phones=paired('Pixel 10 Pro', A) + paired('Galaxy S24', B))
    page = open_page(fw, ui)
    click(ui, page, 4)  # Galaxy S24
    phone = ui.top()
    click(ui, phone, 5)  # Forget Phone, under its gap
    assert ui.went[-1] == 'dialog/sortselect_dialog'  # feedback.c confirm, Cancel first
    answer = fw.fn('carremote_page__cr_forget_answer', None, C.c_void_p, C.c_int, C.c_int)
    answer(0, 0, 0)
    assert q2.files[PEERS] == paired('Pixel 10 Pro', A) + paired('Galaxy S24', B) and phone in ui.windows
    answer(0, 1, 0)
    assert q2.files[PEERS] == paired('Pixel 10 Pro', A) and phone not in ui.windows
    q2.tick()
    ui.fire('cr_page_show')
    assert [c for c, _ in rows(ui, page)[2:]] == ['Pixel 10 Pro']


def test_the_page_follows_pairing_and_connections_live(fw, ui, mcl, recs):
    """Pairing on an open Settings > Car Remote (2026-11-16): the page kept "No Paired Phones" until left."""
    q2 = Car(fw, ui, recs)
    page = open_page(fw, ui)
    assert empty_state(ui, page)[1] == 'No Paired Phones'
    q2.files[PEERS] = paired('Pixel 10 Pro', A)
    q2.files[ONLINE] = A + b'\x00'
    q2.tick()
    ui.fire('cr_page_show')
    assert rows(ui, page)[2:] == [('Pixel 10 Pro', 'Connected over Wi-Fi')] and empty_state(ui, page) is None
    q2.files[ONLINE] = b''
    q2.files[SEEN] = A + struct.pack('<I', NOW - 60)
    q2.tick()
    ui.fire('cr_page_show')
    assert rows(ui, page)[2:] == [('Pixel 10 Pro', 'Not connected \xb7 last seen today')]
    q2.tick()
    assert not ui.pending('cr_page_show')  # nothing changed: no rebuild


def test_the_switch_turns_it_on_and_off(fw, ui, mcl, recs):
    q2 = Car(fw, ui, recs, on=0)
    page = open_page(fw, ui)
    click(ui, page, 0)
    assert [a[:1] for _, a in fw.calls('write_int_config')][-1] == (1,) and q2.system[-1].startswith('( /usr/bin/coda-rc')
    ui.fire('cr_page_show')
    assert empty_state(ui, page)[1] == 'No Paired Phones'
    click(ui, page, 0)
    assert q2.system[-1].startswith('kill $(cat /tmp/coda-rc.pid')
    ui.fire('cr_page_show')
    assert empty_state(ui, page) is None
