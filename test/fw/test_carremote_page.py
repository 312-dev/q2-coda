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
    # the status row holds the caption, as on the Q2 (bar_title_fits), so the switch is at its foot, 58
    bar_title = C.create_string_buffer(0x400)
    st('bar_title', C.c_void_p).value = C.addressof(bar_title)
    fw.mock('widget_get_canvas', lambda *_: 1)
    page = open_page(fw, ui)
    view = lists(ui, page)[-1]
    top = fw.fn('feedback_below', C.c_int, C.c_void_p)(view) - geometry(ui, view)[3]
    switch, gap, cta = ui.kids[view]
    assert top == oled.bar
    # stock's layout stacks the rows; the switch's caption grows it by what the gap gives up, so the foot holds
    foot = top + geometry(ui, switch)[3] + geometry(ui, gap)[3] + geometry(ui, cta)[3]
    assert foot <= oled.bar + oled.list_end
    assert geometry(ui, cta)[3] == 38 + 2 * 5  # the ring's 2 px, 3 px out, and no more


BLE = '/tmp/coda-rc.ble'
IROH, QR = '/tmp/coda-rc.iroh', '/tmp/coda-rc.qr'
ENDPOINT = '8aace45aa34b357c67fae6d1433685921c74f979a8be842802efc17807939913'
RELAY = b'relay https://use1-1.relay.n0.iroh.link./\n'
# What Q2 Muse Remote's one Pair a Q2 Muse screen asks of the phone, beside the QR code and over the code alone
QR_HOW = ['On your phone, open', 'Q2 Muse Remote', 'Tap Pair a Q2 Muse,', 'then scan this or', 'type the code']
CODE_HOW = ['On your phone, open Q2 Muse Remote,', 'tap Pair a Q2 Muse and type this code']
NEARBY = 'Bluetooth  Phones nearby can find it'
SAME_WIFI = ['Wi-Fi  192.168.1.30', 'Phone on the same Wi-Fi']
WAITING = 'Waiting for a phone…'


def howto_texts(fw, ui, howto):
    """What Pair a Phone paints, in order. None of it names the app's old tabs or a Next button: its Pair a Q2 Muse
    screen is the one flow, the Q2 Muses it finds by Bluetooth and on the Wi-Fi in one list."""
    lcd, canvas = C.create_string_buffer(0x200), C.create_string_buffer(0x200)
    C.c_void_p.from_address(C.addressof(canvas) + O['CANVAS_LCD']).value = C.addressof(lcd)
    texts = []
    fw.mock('canvas_draw_text_in_rect', lambda c, s, n, *_: texts.append(''.join(map(chr, (C.c_uint * n).from_address(s)))) or 0)
    fw.mock('canvas_measure_text', lambda c, s, n, *_: 5.0 * n)
    fw.fn('carremote_paint', None, C.c_void_p, C.c_void_p)(howto, C.addressof(canvas))
    assert not [t for t in texts if 'Wi-Fi address' in t or '> Bluetooth' in t or 'tap Next' in t or 'Scan QR' in t]
    return texts


def open_howto(fw, ui, recs, bluetooth=0, advertising=False, wifi=True, random=1042917, iroh=None):
    """Pair a Phone with Bluetooth on (bluetooth), the daemon advertising, Wi-Fi joined, /dev/urandom giving random
    (None: no randomness) and q2-iroh's --info file (iroh); the QR codes it encodes."""
    encoded = []
    fw.mock('QRcode_encodeString', lambda text, *_: encoded.append(C.string_at(text).decode()) and 0)
    q2 = Car(fw, ui, recs)
    fw.var('g_bluetoothflag', C.c_ubyte).value = bluetooth
    if advertising:
        q2.files[BLE] = b'1\n'
    if not wifi:
        fw.mock('fw_ioctl', lambda *_: -1)
    if random is not None:
        q2.files['/dev/urandom'] = struct.pack('<I', random)
    if iroh is not None:
        q2.files[IROH] = iroh
    page = open_page(fw, ui)
    ui.handler(stops(ui, page)[1], O['EVT_CLICK'])()  # the empty state's Pair a Phone
    assert ui.name[ui.top()] == 'carhowto_page'
    return q2, ui.top(), encoded


def open_qr(fw, ui, recs, random=1042917, relay=True, **kw):
    """Pair a Phone with q2-iroh running (its --info file), with a relay or not"""
    return open_howto(fw, ui, recs, random=random, iroh=f'id {ENDPOINT}\n'.encode() + (RELAY if relay else b''), **kw)


def test_on_wi_fi_without_q2_iroh_the_page_shows_the_code_to_type(fw, ui, mcl, recs):
    """The daemon's PairStart takes the page's code whichever way the phone came (coda_rc_core.c pair_start,
    docs/remote-protocol.md#pairing-by-qr-code), so the code shows with no QR code too."""
    q2, howto, encoded = open_howto(fw, ui, recs)
    assert q2.files[QR] == b'042917\n' and encoded == []
    assert howto_texts(fw, ui, howto) == ['042 917'] + CODE_HOW + SAME_WIFI + [WAITING]


def test_bluetooth_on_but_not_advertising_says_nothing_of_bluetooth(fw, ui, mcl, recs):
    """A real Q2's bluetoothd without GattManager1 (2026-11-17): a phone's scan could not find it."""
    _, howto, _ = open_howto(fw, ui, recs, bluetooth=1)
    assert NEARBY not in howto_texts(fw, ui, howto)


def test_advertising_adds_the_bluetooth_line(fw, ui, mcl, recs):
    _, howto, _ = open_howto(fw, ui, recs, bluetooth=1, advertising=True)
    assert howto_texts(fw, ui, howto) == ['042 917'] + CODE_HOW + [NEARBY] + SAME_WIFI + [WAITING]


def test_advertising_with_no_wi_fi_is_the_code_and_bluetooth_alone(fw, ui, mcl, recs):
    _, howto, _ = open_howto(fw, ui, recs, bluetooth=1, advertising=True, wifi=False)
    assert howto_texts(fw, ui, howto) == ['042 917'] + CODE_HOW + [NEARBY, WAITING]


def test_a_stale_advertising_file_with_bluetooth_off_is_not_believed(fw, ui, mcl, recs):
    _, howto, _ = open_howto(fw, ui, recs, bluetooth=0, advertising=True)
    assert NEARBY not in howto_texts(fw, ui, howto)


def test_with_no_link_it_says_to_turn_on_bluetooth_or_join_wi_fi_and_waits_for_nothing(fw, ui, mcl, recs):
    _, howto, _ = open_howto(fw, ui, recs, wifi=False)
    assert howto_texts(fw, ui, howto) == ['1', 'Turn on Bluetooth, or', '2', 'join a Wi-Fi network in',
                                          'Settings > Wi-Fi', '3', 'Come back here to pair', 'Q2 Muse',
                                          'Not reachable yet']


def test_pair_a_phone_follows_bluetooth_coming_on(fw, ui, mcl, recs):
    q2, howto, _ = open_howto(fw, ui, recs, bluetooth=1, wifi=False)
    painted = []
    fw.mock('widget_invalidate_force', lambda w, *_: painted.append(w) or 0)
    q2.tick()
    assert howto not in painted  # nothing changed
    q2.files[BLE] = b'1\n'  # the daemon advertises
    q2.tick()
    assert howto in painted
    assert howto_texts(fw, ui, howto) == ['042 917'] + CODE_HOW + [NEARBY, WAITING]


def test_with_q2_iroh_and_a_relay_the_qr_code_works_from_any_network(fw, ui, mcl, recs):
    """docs/remote-protocol.md#pairing-by-qr-code: the QR code carries the code, the endpoint id, the Wi-Fi address
    and the relay, in capitals (QR's alphanumeric mode); the code is in /tmp/coda-rc.qr for the daemon to take."""
    q2, howto, encoded = open_qr(fw, ui, recs)
    assert q2.files[QR] == b'042917\n'
    assert howto_texts(fw, ui, howto) == ['042 917'] + QR_HOW + ['Works from any', 'network', 'Wi-Fi 192.168.1.30',
                                                                 'Waiting…']
    assert encoded[-1] == f'Q2MUSE:1:042917:{ENDPOINT.upper()}:192.168.1.30:HTTPS://USE1-1.RELAY.N0.IROH.LINK./'


def test_before_q2_iroh_has_a_relay_the_qr_code_works_from_the_same_wi_fi_only(fw, ui, mcl, recs):
    """Without a relay iroh cannot cross networks: the QR code reaches the Q2 at its Wi-Fi address alone."""
    q2, howto, encoded = open_qr(fw, ui, recs, relay=False)
    texts = howto_texts(fw, ui, howto)
    assert texts == ['042 917'] + QR_HOW + ['Phone on the same', 'Wi-Fi as this Q2 Muse', 'Wi-Fi 192.168.1.30',
                                            'Waiting…']
    assert encoded[-1] == f'Q2MUSE:1:042917:{ENDPOINT.upper()}:192.168.1.30:'


def test_beside_the_qr_code_bluetooth_shows_only_while_advertising(fw, ui, mcl, recs):
    _, howto, _ = open_qr(fw, ui, recs, bluetooth=1, advertising=True)
    assert howto_texts(fw, ui, howto) == ['042 917'] + QR_HOW + ['Works from any', 'network', 'Or nearby,',
                                                                 'by Bluetooth', 'Wi-Fi 192.168.1.30', 'Waiting…']


def test_pair_a_phone_follows_the_relay_coming(fw, ui, mcl, recs):
    q2, howto, _ = open_qr(fw, ui, recs, relay=False)
    painted = []
    fw.mock('widget_invalidate_force', lambda w, *_: painted.append(w) or 0)
    q2.tick()
    assert howto not in painted
    q2.files[IROH] = f'id {ENDPOINT}\n'.encode() + RELAY
    q2.tick()
    assert howto in painted and 'Works from any' in howto_texts(fw, ui, howto)


def test_a_code_the_daemon_spent_is_replaced_and_closing_takes_it_away(fw, ui, mcl, recs):
    """The daemon removes /tmp/coda-rc.qr when a pairing on its code ended (a wrong guess among them): the page makes
    another at its next poll and paints it. Closed, the page leaves no code for the daemon to take."""
    q2, howto, encoded = open_qr(fw, ui, recs)
    del q2.files[QR]
    q2.files['/dev/urandom'] = struct.pack('<I', 7)
    q2.tick()
    assert q2.files[QR] == b'000007\n'
    assert howto_texts(fw, ui, howto)[0] == '000 007' and encoded[-1].startswith('Q2MUSE:1:000007:')
    fw.fn('window_close', None, C.c_void_p)(howto)
    assert QR not in q2.files


def test_a_code_spent_without_q2_iroh_is_replaced_too(fw, ui, mcl, recs):
    q2, howto, _ = open_howto(fw, ui, recs)
    painted = []
    fw.mock('widget_invalidate_force', lambda w, *_: painted.append(w) or 0)
    del q2.files[QR]
    q2.files['/dev/urandom'] = struct.pack('<I', 7)
    q2.tick()
    assert howto in painted and howto_texts(fw, ui, howto)[0] == '000 007'


def test_with_a_relay_and_no_wi_fi_address_it_is_still_a_qr_code(fw, ui, mcl, recs):
    """The emulator's case (no wlan0): reachable through the relay alone, the QR code has no address and the page
    names none."""
    _, howto, encoded = open_qr(fw, ui, recs, wifi=False)
    texts = howto_texts(fw, ui, howto)
    assert texts == ['042 917'] + QR_HOW + ['Works from any', 'network', 'Waiting…']
    assert encoded[-1] == f'Q2MUSE:1:042917:{ENDPOINT.upper()}::HTTPS://USE1-1.RELAY.N0.IROH.LINK./'


def test_with_q2_iroh_but_no_way_to_reach_it_the_page_keeps_its_steps(fw, ui, mcl, recs):
    _, howto, encoded = open_qr(fw, ui, recs, relay=False, wifi=False)  # not yet online
    assert howto_texts(fw, ui, howto)[1] == 'Turn on Bluetooth, or' and encoded == []


def test_without_randomness_there_is_no_code_and_nothing_to_scan(fw, ui, mcl, recs):
    """The daemon then makes its own code when the phone asks and shows it on the pairing card."""
    q2, howto, encoded = open_qr(fw, ui, recs, random=4294967295)  # rejected every time
    assert QR not in q2.files and encoded == []
    assert howto_texts(fw, ui, howto) == ['On your phone, open Q2 Muse Remote', 'and tap Pair a Q2 Muse. The code',
                                          'shows when your phone asks'] + SAME_WIFI + [WAITING]


def test_renaming_a_phone_gets_the_strip_s_arrow(fw, ui, mcl, recs):
    """Car Remote's Rename Phone: the letter strip's return key is the arrow (the user's 22:14 ruling)."""
    from test_search_strip import Ss
    Car(fw, ui, recs, phones=paired('Pixel 10 Pro', A), online=A + b'\x00')
    click(ui, open_page(fw, ui), 3)
    click(ui, ui.top(), 3)  # Rename
    ss = fw.var('search_strip__ss', Ss)
    assert ss.page and ss.ret == STRIP_GO
