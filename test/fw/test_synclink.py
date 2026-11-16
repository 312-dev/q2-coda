"""SyncLink off by default (patch/synclink.c, docs/internals.md#synclink): config_init's hook turns a SyncLink left on
by older firmware off once; its page is no longer reached from Settings."""
import ctypes as C

import pytest
from test_usb_charge import Charging


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
