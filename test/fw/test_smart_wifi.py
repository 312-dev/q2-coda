"""Smart Wi-Fi, patch/smart_wifi.c (docs/internals.md#smart-wi-fi): what keeps the radio on past SMART_IDLE_MS.
stock's wifi_switch is a stand-in that toggles g_wififlag as stock's does; test/patch.py runs stock's own."""
import ctypes as C

import pytest

from conftest import O
from test_nav_display_library import scratch, st  # noqa: F401 (fixtures)
from test_nav_library_pages import Config


class Q2:
    def __init__(self, fw, st, **config):
        self.fw, self.ms, self.switched = fw, 1_000_000, []
        self.config = Config(fw, **config)
        fw.mock('time_now_ms', lambda *_: self.ms & 0xffffffff)
        self.wifi = fw.var('g_wififlag', C.c_ubyte)
        fw.mock('wifi_switch', self.switch)
        C.c_int.from_address(O['NET_TYPE']).value = 2  # initNetworkModule's Wi-Fi: stock's switch acts
        st('smart_wifi').value = 1
        self.wifi.value = 1  # the user's Wi-Fi on
        self.poll = fw.fn('smart_poll', None)
        self.off = fw.fn('smart_wifi_off', C.c_int)

    def switch(self, *_):
        self.wifi.value ^= 1
        self.switched.append(self.wifi.value)
        return 0

    def idle(self, ms):
        """Polls every SMART_POLL_MS for ms with nothing else going on."""
        for _ in range(ms // O['SMART_POLL_MS'] + 1):
            self.poll()
            self.ms += O['SMART_POLL_MS']


def test_nothing_needing_the_network_turns_the_radio_off(fw, st):
    q2 = Q2(fw, st, CARREMOTE=b'0')
    q2.idle(O['SMART_IDLE_MS'] - O['SMART_POLL_MS'])
    assert q2.switched == [] and not q2.off()
    q2.idle(2 * O['SMART_POLL_MS'])
    assert q2.switched == [0] and q2.off() == 1


@pytest.mark.parametrize('minutes', [6, 60])
def test_car_remote_on_keeps_the_radio_on(fw, st, minutes):
    """A phone connects over Wi-Fi when the car starts, to a daemon that is on whether a session is open or not."""
    q2 = Q2(fw, st, CARREMOTE=b'1')
    q2.idle(minutes * 60_000)
    assert q2.switched == [] and not q2.off() and q2.wifi.value == 1
