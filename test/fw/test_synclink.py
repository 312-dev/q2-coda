"""SyncLink off by default (patch/synclink.c, docs/internals.md#synclink): config_init's hook turns a SyncLink left on
by older firmware off once, and the Wireless page's SyncLink row has a caption saying what it allows."""
import ctypes as C

import pytest
from test_nav_display_library import (  # noqa: F401 (fixtures)
    STOCK_BODY, THEMES, SettingsList, Ui, geometry, scratch, st, stand_in)
from test_usb_charge import Charging

OLED = next(t['index'] for t in THEMES if t['id'] == 'oled')
HINT = 'Lets nearby phones control this Q2 without a password'


class Boot(Charging):
    """config_init's hook with stock's own reading of SYSSET SYNCLINK (0 Off, 1 Bluetooth, 2 Wi-Fi) into
    g_synclinkflag; own: Q2POD SYNCLINKOWN, None while the key is missing."""
    def __init__(self, fw, synclink, own=None):
        super().__init__(fw)
        self.ini['SYNCLINK'] = str(synclink)
        del self.ini['SYNCLINKOWN']
        if own is not None:
            self.ini['SYNCLINKOWN'] = str(own)
        self.synclink = fw.var('g_synclinkflag', C.c_ubyte)

    def load(self, *_):
        self.synclink.value = int(self.ini['SYNCLINK'])
        return super().load()


@pytest.mark.parametrize('on', [1, 2], ids=['bluetooth', 'wifi'])
def test_synclink_left_on_by_older_firmware_is_off_before_anything_starts_it(fw, on):
    """Each of stock's starts (checkBluetoothStatus and addMessage for Bluetooth, synclink_mainscheduling for Wi-Fi,
    after platform_init's config_init) runs only on g_synclinkflag 1 or 2: 0 when config_init returns is all off."""
    q = Boot(fw, on)
    assert q.config() == 77
    assert q.synclink.value == 0
    assert q.saved == [('SYSSET', 'SYNCLINK', 0), ('Q2POD', 'SYNCLINKOWN', 1)]


def test_synclink_never_turned_on_only_gets_the_mark(fw):
    q = Boot(fw, 0)
    q.config()
    assert q.synclink.value == 0 and q.saved == [('Q2POD', 'SYNCLINKOWN', 1)]


@pytest.mark.parametrize('on', [1, 2], ids=['bluetooth', 'wifi'])
def test_synclink_turned_on_on_this_firmware_stays_on(fw, on):
    """Once marked, SYNCLINK is the user's: a choice made on its page since is kept, every boot."""
    q = Boot(fw, on, own=1)
    q.config()
    q.config()
    assert q.synclink.value == on and q.saved == []


def wireless(fw, st, geometry, stand_in):
    """Stock's Wireless list as systemset_wifiset_page_init builds it: Wi-Fi, Bluetooth and SyncLink rows."""
    ui = Ui(fw)
    lst = SettingsList(fw, ui, st, geometry, stand_in, geom='GEOM_OLED', theme=b'%d' % OLED, window='wifiset_page',
                       view='scroll_view_wifiset')
    lst.list.name = 'list_view_wifiset'
    rows = [lst.row(('hscroll_label', (72, 0, 260, STOCK_BODY), 'label')) for _ in range(3)]
    return lst, rows


def test_the_synclink_row_has_a_caption_under_it(fw, st, geometry, stand_in):
    lst, rows = wireless(fw, st, geometry, stand_in)
    lst()
    items = lst.view.children
    assert items[:3] == rows and len(items) == 4
    hint = items[3]
    (label,) = hint.children
    assert label.text == HINT and label.kind == 'label'
    # wrapped at words on the rows' text column, in Text Tertiary, and the wheel passes it
    assert label.props['line_wrap'] == 1 and label.props['word_wrap'] == 1
    assert label.box[0] == rows[2].children[0].children[0].box[0] + 2  # hscroll_label's text starts 2 px in
    assert label.props['_final'] & 0xffffff == 0x858585
    assert hint.props['style'] == 's_listitem_black'
    assert any(w == hint.addr and not on for w, on in (c[1][:2] for c in fw.calls('widget_set_enable')))


def test_the_caption_is_made_once(fw, st, geometry, stand_in):
    lst, rows = wireless(fw, st, geometry, stand_in)
    lst()
    lst()
    assert len(lst.view.children) == 4


def test_other_settings_lists_get_no_caption(fw, st, geometry, stand_in):
    lst, rows = wireless(fw, st, geometry, stand_in)
    lst.list.name = 'list_view_netservice'
    lst()
    assert lst.view.children == rows
