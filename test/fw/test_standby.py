"""Standby (patch/system.c, docs/internals.md#standby-limit): held while a host has the USB link, where a suspend left the link dead after the wake (a Q2 on a
laptop, 2026-11-22: the gadget still configured on the Q2, the computer never reached it again until the cable was
handled)."""
import ctypes as C

import pytest

from conftest import Files

UDC = '/sys/class/udc/13500000.otg/state'


# ---- held while a host has the USB link (standby_host_poll) ----

class Cable:
    def __init__(self, fw, cable=1, udc='configured\n'):
        self.fw, self.now = fw, 100000
        fw.var('g_usbdet_value', C.c_ubyte).value = cable
        self.files = Files(fw, {UDC: udc.encode()} if udc else {})
        fw.mock('time_now_ms', lambda *_: self.now)

    def holds(self, passes=5, step=300):
        """reset_poweroptions_timer(1, 0, 0) calls over passes UI passes step ms apart."""
        before = len(self.fw.calls('reset_poweroptions_timer'))
        for _ in range(passes):
            self.now += step
            self.fw.fn('system__standby_host_poll', None)()
        return [a[:3] for _, a in self.fw.calls('reset_poweroptions_timer')[before:]]


def test_a_computer_on_the_cable_holds_standby_once_a_second(fw):
    """Five passes 300 ms apart: 1.5 s, so the countdown starts again once (the first look) and once more."""
    assert Cable(fw).holds() == [(1, 0, 0), (1, 0, 0)]


@pytest.mark.parametrize('cable, udc', [(1, 'not attached\n'), (1, 'default\n'), (1, None), (0, 'configured\n')])
def test_a_charger_or_no_cable_lets_standby_count(fw, cable, udc):
    """A charger configures nothing; with no cable the controller is not read at all."""
    c = Cable(fw, cable, udc)
    assert c.holds() == []
    if not cable:
        assert not fw.calls('fw_fopen')
