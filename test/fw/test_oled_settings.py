"""OLED's settings rows (docs/oled-design.md List row, Screens): settings.values.switch keeps an on/off row a
switch on a set_text theme, the rows' text starts on the text column, and the Wi-Fi page's list is restyled
after stock builds it (patch/wifi_rows.c)."""
import ctypes as C

from conftest import O
from test_nav_display_library import (  # noqa: F401 (fixtures)
    OLED, STOCK_BODY, SettingsList, Ui, button_of, display, geometry, scratch, st, stand_in)


def test_a_set_switch_themes_on_off_row_is_a_switch(fw):
    """Smart Awake (list_toggle) on OLED, a set_text theme that is also set_switch: the switch image (on, its default),
    no On or Off."""
    ui = Ui(fw)
    b = button_of(display(fw, ui, THEME=b'%d' % OLED).view(), 'Smart Awake')
    assert b.find('_setting_value') is None and b.find('_setting_switch').props['image'] == b'switch_on'


def test_oled_settings_text_starts_on_the_text_column(fw, st, geometry, stand_in):
    """A row without an icon: its label's box at text_x less hscroll_label's 2 px, so its text starts on text_x."""
    ui = Ui(fw)
    lst = SettingsList(fw, ui, st, geometry, stand_in, theme=b'%d' % OLED)
    item = lst.row(('hscroll_label', (10, 0, 260, STOCK_BODY), 'label'))
    lst()
    assert item.children[0].children[0].box[0] == lst.g['V_TEXT_X'] - 2


def wifi_list(fw, st, geometry, stand_in, searching):
    """Stock's Wi-Fi list as wifimenu_rows_build leaves it with Wi-Fi on: the switch row, the Scanning row, the
    centred header, one network (its signal image leading) and Add Network."""
    ui = Ui(fw)
    lst = SettingsList(fw, ui, st, geometry, stand_in, theme=b'%d' % OLED, window='wifi_page',
                       view='scroll_view_wifi')
    lst.list.name = 'list_view_wifi'
    fw.var('wifi_searchtime', C.c_ubyte).value = searching
    rows = {'switch': lst.row(('hscroll_label', (10, 0, 260, STOCK_BODY), 'label'),
                              ('image', (276, 0, 50, STOCK_BODY), '', b'switch_on')),
            'scan': lst.row(('hscroll_label', (10, 0, 260, STOCK_BODY), 'label'),
                            ('image', (276, 0, 50, STOCK_BODY), 'wifi_search_icon', b'bt_refresh'))}
    head = ui.add('list_item', lst.view, (0, 0, 375, 40))
    ui.add('label', head, (0, 8, 375, 24)).text = 'Select Network'
    rows['head'] = head
    rows['net'] = lst.row(('image', (10, 9, O['SET_STOCK_ICON'], 52), '', b'wifi_sig4'),
                          ('hscroll_label', (72, 0, 260, STOCK_BODY), 'label'))
    rows['add'] = lst.row(('hscroll_label', (10, 0, 260, STOCK_BODY), 'label'))
    return lst, rows


def test_the_wifi_page_has_a_networks_section_and_search_again_for_scanning(fw, st, geometry, stand_in):
    lst, rows = wifi_list(fw, st, geometry, stand_in, searching=1)
    lst()
    assert rows['scan'].visible and rows['switch'].visible and rows['add'].visible
    # stock's Scanning row, whose tap scans: a plain row reading Search Again, its spinner hidden
    label, icon = rows['scan'].children[0].children
    assert label.text == 'Search Again' and not icon.visible
    head = rows['head']
    stock, section, busy = head.children
    # stock's centred header hidden; the section's label on the text column after the 12 px gap, the busy line's
    # text ending on the right column, both on the label's 32 px line
    assert not stock.visible and head.box[3] == 12 + 32
    assert (section.text, section.box[:2]) == ('Networks', (lst.g['V_TEXT_X'], 12))
    assert busy.text == 'Searching...' and busy.box[0] + busy.box[2] == 375 - 28 and busy.box[1] == 12


def test_the_wifi_pages_busy_line_is_empty_once_the_scan_ends(fw, st, geometry, stand_in):
    lst, rows = wifi_list(fw, st, geometry, stand_in, searching=0)
    lst()
    assert rows['head'].children[2].text == ''


def test_a_networks_signal_moves_to_the_right_column(fw, st, geometry, stand_in):
    """set_child hides a leading image; wifi_rows_post shows the signal again across the glyphs' columns, ending on
    the right column, and the name ends before them."""
    lst, rows = wifi_list(fw, st, geometry, stand_in, searching=0)
    lst()
    sig, name = rows['net'].children[0].children
    assert sig.visible and sig.box[0] + sig.box[2] == 375 - 28 and sig.box[3] == lst.g['V_SET_ROW']
    assert name.box[0] + name.box[2] <= sig.box[0]


def test_a_networks_long_name_waits_for_the_selection_on_every_row(fw, st, geometry, stand_in):
    """Stock's rows' labels carry neither focus flag under the wheel's mode, so a long name scrolled once on every row as
    the page opened and never again, not even selected (user, 2026-11-16, on hardware and in the emulator). set_child
    (navigation.c) makes every settings row's title only_focus, each network's name and the connected row's beside
    its trash too: at rest on its ellipsis, the house marquee (marq_moved) scrolls it on the selected row."""
    lst, rows = wifi_list(fw, st, geometry, stand_in, searching=0)
    conn = lst.row(('image', (10, 9, O['SET_STOCK_ICON'], 52), '', b'wifi_sig4'),
                   ('hscroll_label', (72, 0, 200, STOCK_BODY), 'label'),
                   ('label', (72, 40, 200, 20), 'state'), ('image', (276, 0, 50, STOCK_BODY), 'forget', b'navbar_delete'))
    conn.children[0].children[2].props['tr_text'] = b'msg_connected'
    lst()
    names = [rows['net'].children[0].children[1], conn.children[0].children[1]]
    focus = {a[0] for _, a in fw.calls('hscroll_label_set_only_focus') if a[1] == 1}
    assert all(n.addr in focus for n in names)


def test_the_connected_networks_trash_ends_on_the_right_column_with_its_glyphs_left_of_it(fw, st, geometry, stand_in):
    """Stock's Forget image (navbar_delete) on the connected row keeps its tap but no art, centred on the right
    column's last 20 px slot; the signal's box takes that slot too, for wifi_mark to draw the trash there and the
    signal, lock and check a slot left, so the trash lines up with every other row's signal, selected or not."""
    lst, rows = wifi_list(fw, st, geometry, stand_in, searching=0)
    conn = lst.row(('image', (10, 9, O['SET_STOCK_ICON'], 52), '', b'wifi_sig4'),
                   ('hscroll_label', (72, 0, 200, STOCK_BODY), 'label'),
                   ('label', (72, 40, 200, 20), 'state'), ('image', (276, 0, 50, STOCK_BODY), 'forget', b'navbar_delete'))
    conn.children[0].children[2].props['tr_text'] = b'msg_connected'
    lst()
    sig, name, _, trash = conn.children[0].children
    other = rows['net'].children[0].children[0]
    right = 375 - 28
    assert sig.box[0] + sig.box[2] == right and sig.box[2] == 4 * 20 + 3 * 8  # the trash's slot, then three
    assert other.box[0] + other.box[2] == right and other.box[2] == 3 * 20 + 2 * 8
    assert trash.box[0] + trash.box[2] // 2 == right - 10 and trash.box[2] == 50 and trash.visible  # its tap, centred
    assert trash.props['image'] in (b'', '') and name.box[0] + name.box[2] <= sig.box[0]
    lst()  # a second layout finds it by its mark, not its emptied image
    assert trash.box[0] + trash.box[2] // 2 == right - 10


def status_row(lst, state, kind='label'):
    """A network row with stock's second line, the label stock puts under the name (its tr_text the state)."""
    return lst.row(('image', (10, 9, O['SET_STOCK_ICON'], 52), '', b'wifi_sig4'),
                   ('hscroll_label', (72, 0, 200, STOCK_BODY), 'label'),
                   (kind, (72, 40, 200, 20), 'state'), ('image', (276, 0, 50, STOCK_BODY), 'forget', b'navbar_delete'))


def captions(fw, view):
    """The caption table's rows (settings_pages.c cap: the view, then CAP_MAX items, then their texts)."""
    table = fw.var('settings_pages__cap', C.c_void_p * 17)
    return {table[1 + k]: C.wstring_at(table[9 + k]) for k in range(8) if table[1 + k]} if table[0] == view.addr else {}


def test_the_connected_network_is_one_line_and_says_connected_as_a_selection_caption(fw, st, geometry, stand_in):
    """The check already says Connected, so the status line is not drawn at rest (it read as another network's
    name): the row is one settings row high, as every other, with the word as a caption that grows under the bar
    while the row is selected, as Reduce Motion's does (settings_pages.c caption_row)."""
    lst, rows = wifi_list(fw, st, geometry, stand_in, searching=0)
    conn = status_row(lst, 'msg_connected', 'hscroll_label')  # stock's line, as the row's second hscroll_label
    conn.children[0].children[2].props['tr_text'] = b'msg_connected'
    lst()
    _, name, line, _ = conn.children[0].children
    assert not line.visible and conn.box[3] == lst.g['V_SET_ROW']  # no band for it: the row does not jump
    assert name.box[1] == 0 and name.box[3] == conn.box[3]  # the name alone, centred in the row
    assert captions(fw, lst.view) == {conn.addr: 'Connected'}
    assert rows['net'].box[3] == lst.g['V_SET_ROW']


def test_a_connecting_network_keeps_its_status_as_a_caption_sized_second_line(fw, st, geometry, stand_in):
    """No check or other mark shows Connecting, so its line stays, as a 13 px Text Tertiary caption under the name
    (docs/oled-design.md Type and Colors: second lines and captions), not a second row label."""
    lst, rows = wifi_list(fw, st, geometry, stand_in, searching=0)
    conn = status_row(lst, 'msg_connecting', 'hscroll_label')
    conn.children[0].children[2].props['tr_text'] = b'msg_connecting'
    lst()
    line = conn.children[0].children[2]
    assert line.visible and conn.box[3] == 2 * lst.g['V_SET_ROW'] and captions(fw, lst.view) == {}
    assert line.props['style:normal:font_size'] == (13 * 121 + 50) // 100  # TYPE(13), AWTK's size for Inter 13
    assert line.props['_final'] & 0xffffffff == 0xff858585


def test_a_rebuilt_wifi_list_drops_the_old_rows_captions(fw, st, geometry, stand_in):
    """Stock rebuilds the list on every scan and link change, its items destroyed: a caption entry for a gone
    item would be written to by the caption's growth, and fill the table."""
    lst, rows = wifi_list(fw, st, geometry, stand_in, searching=0)
    old = status_row(lst, 'msg_connected')
    old.children[0].children[2].props['tr_text'] = b'msg_connected'
    lst()
    assert list(captions(fw, lst.view)) == [old.addr]
    lst.view.children.remove(old)
    new = status_row(lst, 'msg_connected')
    new.children[0].children[2].props['tr_text'] = b'msg_connected'
    lst()
    assert list(captions(fw, lst.view)) == [new.addr]
