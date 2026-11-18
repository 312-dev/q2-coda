"""Settings > Car Remote, patch/carremote_page.c (docs/internals.md#car-remote, docs/oled-design.md#car-remote): the
switch and its caption, Pair a Phone, the phones' rows with their link and when each was last seen, a phone's page
(Status, Paired, Last Connected, Rename, Forget Phone), the how-to page and the toast naming a paired phone. The
daemon's files are a dict (conftest.Files); AWTK is test_nav_library_pages' fake, the wall clock UTC."""
import calendar
import ctypes as C
import struct
import time

from conftest import O, STRIP_GO, mcl  # noqa: F401
from test_coda_rc_shim import ONLINE, PAIR, PAIR_END, PEERS, Q2, card_route, peer
from test_nav_library_pages import call, empty_state, signed, ui  # noqa: F401
from test_nav_hold import recs, scratch, st  # noqa: F401

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


def test_no_phones_is_the_empty_state_with_pair_a_phone_a_row_of_the_one_list(fw, ui, mcl, recs):
    """Pair a Phone was the empty state's own list beside the page's (emulator, 2026-11-16): each held a selection,
    so the wheel had no list to move (nav/lists.c surface_under gives none when two do) and the switch no bar. The
    capsule is the page's own row 1, selected as the page opens, painting its own ring, and Centre on it opens the
    how-to page."""
    Car(fw, ui, recs)
    page = open_page(fw, ui)
    assert empty_state(ui, page) == ('', 'No Paired Phones', 'Open Q2 Muse Remote on your phone to pair',  # compact
                                     None)  # its button is the page's row, not the empty state's
    assert len([w for w in ui.subtree(page) if ui.kind[w] == 'scroll_view_create']) == 1  # one list for the wheel
    view = lists(ui, page)[-1]
    switch, cta = stops(ui, page)
    assert ui.props[(view, '_ringnav_count')] == 2 and ui.props[(view, '_ringnav_index')] == 1
    assert ui.props.get((cta, '_rings_itself')) == 1  # no selection bar behind the capsule
    assert geometry(ui, cta)[3] == 38 + 2 * 5 and not [k for k in ui.kids[cta] if ui.kind[k] == 'image_create']  # no chevron
    ui.handler(cta, O['EVT_CLICK'])()  # Centre on the capsule
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
    card_route(fw, ui)
    q2.tick()
    assert ui.name[ui.top()] == 'sortselect_dialog'  # the pairing card (pair_prompt.c)
    assert C.string_at(C.addressof(C.c_char.in_dll(fw.lib, 'pair_prompt__pair_name'))) == b'Galaxy S24'
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


def test_the_wheel_moves_onto_pair_a_phone_without_scrolling_the_page(fw, ui, mcl, recs, st):
    """The user's device (p31 dev, 2026-11-16): with no phones, the wheel moving from the switch to Pair a Phone
    scrolled the list, the capsule to its top and the switch gone, where everything fits on the screen. The capsule's
    row, CR_CTA_OUT past it for the ring, ended at y 283 under OLED's set, a pixel past its list end (282), so
    nav/lists.c reveal scrolled it into view and fit_top took the list on to the row's top (reproduced on the
    emulator). The row reaches only as far as the ring, so its foot stays inside the list end and nothing scrolls."""
    from test_nav_hold import use_set
    oled = use_set(st)
    Car(fw, ui, recs)
    page = open_page(fw, ui)
    # the page's list at OLED's y (its switch at the status row's foot, 58), as on the Q2, and the page built again
    view = lists(ui, page)[-1]
    struct.pack_into('<i', ui.blocks[page], 4, oled.bar - (fw.fn('feedback_below', C.c_int, C.c_void_p)(view)
                                                           - geometry(ui, view)[3]))
    fw.fn('carremote_page__cr_page_show', C.c_int, C.c_void_p)(1)
    view = lists(ui, page)[-1]
    top = fw.fn('feedback_below', C.c_int, C.c_void_p)(view) - geometry(ui, view)[3]
    switch, gap, cta = ui.kids[view]
    assert top == oled.bar
    # stock's layout stacks the rows; the switch's caption grows it by what the gap gives up, so the foot holds
    foot = top + geometry(ui, switch)[3] + geometry(ui, gap)[3] + geometry(ui, cta)[3]
    assert foot <= oled.bar + oled.list_end
    assert geometry(ui, cta)[3] == 38 + 2 * 5  # the ring's 2 px, 3 px out, and no more


BLE = '/tmp/coda-rc.ble'
WIFI_STEPS = ['1', 'Open the Q2 Muse Remote app', '2', 'Pair a Q2 Muse > Wi-Fi address', '3',
              'Pick Q2 Muse, then tap Next', '4', 'Type the code shown here']


def howto_texts(fw, ui, howto):
    """What Pair a Phone paints, in order."""
    lcd, canvas = C.create_string_buffer(0x200), C.create_string_buffer(0x200)
    C.c_void_p.from_address(C.addressof(canvas) + O['CANVAS_LCD']).value = C.addressof(lcd)
    texts = []
    fw.mock('canvas_draw_text_in_rect', lambda c, s, n, *_: texts.append(''.join(map(chr, (C.c_uint * n).from_address(s)))) or 0)
    fw.mock('canvas_measure_text', lambda c, s, n, *_: 5.0 * n)
    fw.fn('carremote_paint', None, C.c_void_p, C.c_void_p)(howto, C.addressof(canvas))
    return texts


def open_howto(fw, ui, recs, bluetooth=0, advertising=False, wifi=True):
    q2 = Car(fw, ui, recs)
    fw.var('g_bluetoothflag', C.c_ubyte).value = bluetooth
    if advertising:
        q2.files[BLE] = b'1\n'
    if not wifi:
        fw.mock('fw_ioctl', lambda *_: -1)
    page = open_page(fw, ui)
    ui.handler(stops(ui, page)[1], O['EVT_CLICK'])()  # the empty state's Pair a Phone
    assert ui.name[ui.top()] == 'carhowto_page'
    return q2, ui.top()


def test_pair_a_phone_with_bluetooth_off_gives_the_wi_fi_steps_and_the_address(fw, ui, mcl, recs):
    _, howto = open_howto(fw, ui, recs)
    assert howto_texts(fw, ui, howto) == WIFI_STEPS + ['Q2 Muse', 'Address  192.168.1.30', 'Phone and Q2 Muse on one Wi-Fi',
                                                       'Waiting for a phone…']


def test_wi_fi_steps_pick_the_q2_by_its_bluetooth_name(fw, ui, mcl, recs):
    """Q2 Muse Remote's Wi-Fi tab lists the Q2 under its mDNS instance, the Bluetooth name (coda_rc_mdns.c)."""
    def read(path, section, key, out, default, *_):
        v = b'Car Q2' if C.string_at(key) == b'BTNAME' else C.string_at(default)
        C.memmove(out, v + b'\0', len(v) + 1)
    _, howto = open_howto(fw, ui, recs)
    fw.mock('toolsReadConfig', read)
    assert howto_texts(fw, ui, howto)[5] == 'Pick Car Q2, then tap Next'


def test_bluetooth_on_but_not_advertising_is_still_the_wi_fi_steps(fw, ui, mcl, recs):
    """A real Q2's bluetoothd without GattManager1 (2026-11-17): the app's Bluetooth tab could not find it."""
    _, howto = open_howto(fw, ui, recs, bluetooth=1)
    assert howto_texts(fw, ui, howto)[:8] == WIFI_STEPS


def test_advertising_gives_the_bluetooth_steps_and_the_address_besides(fw, ui, mcl, recs):
    _, howto = open_howto(fw, ui, recs, bluetooth=1, advertising=True)
    assert howto_texts(fw, ui, howto) == [
        '1', 'Open the Q2 Muse Remote app', '2', 'Pair a Q2 Muse > Bluetooth', '3', 'Pick Q2 Muse, then tap Next', '4',
        'Type the code shown here', 'Q2 Muse', 'Or Wi-Fi address  192.168.1.30', 'Waiting for a phone…']


def test_a_stale_advertising_file_with_bluetooth_off_is_not_believed(fw, ui, mcl, recs):
    _, howto = open_howto(fw, ui, recs, bluetooth=0, advertising=True)
    assert howto_texts(fw, ui, howto)[:8] == WIFI_STEPS


def test_with_no_link_it_says_to_join_wi_fi_first_and_waits_for_nothing(fw, ui, mcl, recs):
    _, howto = open_howto(fw, ui, recs, wifi=False)
    assert howto_texts(fw, ui, howto) == ['1', 'Open Settings > Wi-Fi', '2', 'Join the Wi-Fi your phone is on', '3',
                                          'Come back here to pair', 'Q2 Muse', 'Wi-Fi  not connected']


def test_pair_a_phone_follows_bluetooth_coming_on(fw, ui, mcl, recs):
    q2, howto = open_howto(fw, ui, recs, bluetooth=1)
    painted = []
    fw.mock('widget_invalidate_force', lambda w, *_: painted.append(w) or 0)
    q2.tick()
    assert howto not in painted  # nothing changed
    q2.files[BLE] = b'1\n'  # bluetoothd took the GATT application
    q2.tick()
    assert howto in painted
    assert howto_texts(fw, ui, howto)[3] == 'Pair a Q2 Muse > Bluetooth'


def test_renaming_a_phone_gets_the_strip_s_arrow(fw, ui, mcl, recs):
    """Car Remote's Rename Phone: the letter strip's return key is the arrow (the user's 22:14 ruling)."""
    from test_search_strip import Ss
    Car(fw, ui, recs, phones=paired('Pixel 10 Pro', A), online=A + b'\x00')
    click(ui, open_page(fw, ui), 3)
    click(ui, ui.top(), 3)  # Rename
    ss = fw.var('search_strip__ss', Ss)
    assert ss.page and ss.ret == STRIP_GO
