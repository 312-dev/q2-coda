"""Standby (patch/system.c, docs/internals.md#standby-limit): on once on a Q2 set up while Q2 Muse shipped it off,
a later Off kept, and held while a host has the USB link, where a suspend left the link dead after the wake (a Q2 on a
laptop, 2026-11-22: the gadget still configured on the Q2, the computer never reached it again until the cable was
handled)."""
import ctypes as C

import pytest

from conftest import Files

UDC = '/sys/class/udc/13500000.otg/state'


class Config:
    """config.ini's keys as toolsReadConfig reads them and write_int_config saves them, and stock's two flags."""
    def __init__(self, fw, ini=None, standby=0, savepower=0):
        self.ini, self.saved = dict(ini or {}), []
        self.standby, self.savepower = fw.var('g_standbyflag', C.c_ubyte), fw.var('g_savepower_mode', C.c_ubyte)
        self.standby.value, self.savepower.value = standby, savepower
        fw.mock('toolsReadConfig', self.read)
        fw.mock('write_int_config', self.write)
        self.fw = fw

    def read(self, path, section, key, out, default, *_):
        v = self.ini.get(C.string_at(key).decode())
        C.memmove(out, (v if v is not None else C.string_at(default).decode()).encode() + b'\0', 8)
        return 0

    def write(self, value, section, key, *_):
        self.saved.append((C.string_at(section).decode(), C.string_at(key).decode(), value))
        self.ini[C.string_at(key).decode()] = str(value)
        return 0

    def run(self):
        self.fw.fn('standby_default', None)()


def test_a_q2_set_up_with_standby_off_gets_it_on_once(fw):
    c = Config(fw)
    c.run()
    assert c.standby.value == 1 and c.saved == [('SYSSET', 'STANDBYMODE', 1), ('Q2POD', 'STANDBYOWN', 1)]
    assert ('reset_poweroptions_timer', 1, 0, 0) in [(n, *a[:3]) for n, a in fw.calls('reset_poweroptions_timer')]


def test_standby_turned_off_after_that_stays_off(fw):
    c = Config(fw, {'STANDBYOWN': '1', 'STANDBYMODE': '0'})
    c.run()
    assert c.standby.value == 0 and c.saved == []


def test_auto_shutdown_chosen_is_kept_and_standby_left_off(fw):
    """Stock lets Auto-standby or Auto-shutdown be on, not both (Settings > Battery > Standby)."""
    c = Config(fw, savepower=1)
    c.run()
    assert c.standby.value == 0 and c.saved == [('Q2POD', 'STANDBYOWN', 1)]


def test_standby_already_on_is_only_marked(fw):
    c = Config(fw, standby=1)
    c.run()
    assert c.standby.value == 1 and c.saved == [('Q2POD', 'STANDBYOWN', 1)]


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
