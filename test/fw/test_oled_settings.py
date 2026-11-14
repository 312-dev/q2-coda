"""OLED's settings rows (docs/oled-design.md List row, Screens): settings.values.switch keeps an on/off row a
switch on a set_text theme, and the rows' text starts on the text column."""
from test_nav_display_library import (  # noqa: F401 (fixtures)
    CLASSIC, STOCK_BODY, THEMES, SettingsList, Ui, button_of, display, geometry, scratch, st, stand_in)

OLED = next(t['index'] for t in THEMES if t['id'] == 'oled')


def test_a_set_switch_themes_on_off_row_is_a_switch(fw):
    """Smart Awake (list_toggle) on OLED, a set_text theme that is also set_switch: the switch image, no On or Off."""
    ui = Ui(fw)
    b = button_of(display(fw, ui, THEME=b'%d' % OLED).view(), 'Smart Awake')
    assert b.find('_setting_value') is None and b.find('_setting_switch').props['image'] == b'switch_off'


def test_a_set_text_themes_on_off_row_still_reads_on_or_off(fw):
    ui = Ui(fw)
    b = button_of(display(fw, ui, THEME=b'%d' % CLASSIC).view(), 'Smart Awake')
    assert b.find('_setting_switch') is None and b.find('_setting_value').text == 'Off'


def test_oled_settings_text_starts_on_the_text_column(fw, st, geometry, stand_in):
    """A row without an icon: its label's box at text_x less hscroll_label's 2 px, so its text starts on text_x."""
    ui = Ui(fw)
    lst = SettingsList(fw, ui, st, geometry, stand_in, geom='GEOM_OLED', theme=b'%d' % OLED)
    item = lst.row(('hscroll_label', (10, 0, 260, STOCK_BODY), 'label'))
    lst()
    assert item.children[0].children[0].box[0] == lst.g['V_TEXT_X'] - 2

