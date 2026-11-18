"""OTG host power (usb_otg.c, docs/internals.md#otg-host-power): run the real ringnav_otg_status over a model of
stock's check_otg_status and the Q2's USB pins, and replay the pin log of 2026-11-17's back-feed.

Stock's check_otg_status (0x4f3408, V1.36), as disassembled: id = toolsGpioGetValue("PD18"); host = id == 0; when
g_last_otgid differs from host it writes PC20 = host (bus power) and PE22 = host (charger off), then g_last_otgid =
host. Nothing else in demo writes PC20 high (into_poweroff, device_reboot and device_standby write it 0)."""
import ctypes as C
import pathlib
import re

import pytest

PASS_MS = 1000  # on_wm_idle_status runs check_otg_status about once a second
# usb_otg.c's settle time, read from the source so the timings below follow it
_SETTLE = re.search(r'#define OTG_SETTLE_MS (\d+)\n', (pathlib.Path(__file__).parents[2]/'patch/usb_otg.c').read_text())
assert _SETTLE, 'usb_otg.c: OTG_SETTLE_MS is not a plain #define'
SETTLE = int(_SETTLE[1])


class Port:
    """The pins as the Q2 reads them. PD17 (cable sense, 0 = VBUS on the port) reads low for a computer's or a
    charger's VBUS and for the Q2's own bus power alike, as the log shows (vbus=1, PD17=0 from the pass PC20 went 1)."""
    def __init__(self, fw, id_pin=1, cable=False):
        self.fw, self.id, self.cable = fw, id_pin, cable
        self.pins = {'PC20': 0, 'PE22': 0}
        self.now = 100000
        self.backfeed = []  # times the Q2 powered the bus while a computer's VBUS was on it
        self.powered = []  # times the Q2 powered the bus at all
        self.otgid = fw.var('g_last_otgid', C.c_byte)
        self.otgid.value = 0
        self.guard = fw.fn('ringnav_otg_status', C.c_int)
        fw.mock('time_now_ms', lambda *_: self.now)
        fw.mock('toolsGpioGetValue', self.read)
        fw.mock('stock_otg_status_trampoline', self.stock)
        self.commands = []
        fw.mock('fw_system', lambda cmd, *_: self.commands.append(C.string_at(cmd).decode()))

    def records(self):
        """What usb_record.c wrote, by its event word: the third argument of its line's tk_snprintf."""
        return [C.string_at(a[5]).decode() for _, a in self.fw.calls('tk_snprintf')
                if C.string_at(a[2]).startswith(b'%u %u %s PD18=')]

    def read(self, name, *_):
        pin = C.string_at(name).decode()
        if pin == 'PD18':
            return self.id
        if pin == 'PD17':
            return 0 if self.cable or self.pins['PC20'] else 1
        return self.pins.get(pin, 1)  # PD19: an input with a pull-up

    def stock(self, *_):
        host = int(self.id == 0)
        if self.otgid.value != host:
            self.pins['PC20'] = self.pins['PE22'] = host
            self.otgid.value = host
        return 0

    def run(self, ms):
        """Status passes for ms, as on_wm_idle_status calls check_otg_status."""
        end = self.now + ms
        while self.now < end:
            self.now += PASS_MS
            self.guard()
            if self.pins['PC20']:
                self.powered.append(self.now)
                if self.cable:
                    self.backfeed.append(self.now)


# From the device's pin log of 2026-11-17 (q2-replug2-state.log, build p32b, one sample each 0.5 s): each unplug, when
# PD18 read low, the first sample that read VBUS again, and when PD18 read high. Stock had turned PC20 on by that VBUS
# sample, and its own power masks a cable, so the log cannot say when the cable went back in; the replay puts it there,
# about a second after the ID went low.
LOGGED = [  # (unplugged, PD18 low, VBUS back, PD18 high) in seconds of uptime
    (607.90, 608.97, 610.02, 614.28),
    (622.25, 623.32, 623.85, 642.40),
    (642.94, 644.53, 645.06, 654.60),
    (655.13, 658.31, 658.84, 664.14),
    (665.20, 665.73, 666.26, 672.09),
]


@pytest.mark.parametrize('unplug,low,back,high', LOGGED)
def test_a_replug_into_a_computer_while_the_id_pin_reads_low_never_powers_the_bus(fw, unplug, low, back, high):
    p = Port(fw, cable=True)
    p.run(5000)  # plugged in, a USB device
    ms = lambda a, b: int(round((b - a) * 1000))
    p.cable = False
    p.run(ms(unplug, low))
    p.id = 0  # the ID pin's low after an unplug, no cable in
    p.run(ms(low, back))
    p.cable = True  # back into the computer
    p.run(ms(back, high))
    p.id = 1
    p.run(3000)
    assert p.backfeed == [] and p.powered == [] and p.otgid.value == 0


def test_the_logged_replugs_back_feed_under_stocks_own_pass(fw):
    """The model is the log's: stock alone, the same sequence powers the bus into the computer."""
    p = Port(fw, cable=True)
    p.guard = lambda: p.stock()
    p.cable = False
    p.run(1000)
    p.id = 0
    p.run(1000)
    p.cable = True
    p.run(4000)
    assert p.backfeed and p.pins['PC20'] == 1


def test_an_otg_adapter_gets_bus_power_once_the_id_pin_has_settled(fw):
    """A DAC or a drive on an OTG adapter: ID grounded, no VBUS on the port. The bus power comes on after
    OTG_SETTLE_MS, stays on through PD17 reading the Q2's own power, and goes off in the pass the ID reads high."""
    p = Port(fw)
    p.run(3000)
    p.id = 0
    p.run(SETTLE)  # the wait starts at the first low pass: SETTLE - 1000 of it so far
    assert p.pins['PC20'] == 0 and p.otgid.value == 0
    p.run(PASS_MS)
    assert p.pins == {'PC20': 1, 'PE22': 1} and p.otgid.value == 1
    p.run(20000)
    assert p.pins['PC20'] == 1
    p.id = 1  # the adapter out
    p.run(PASS_MS)
    assert p.pins == {'PC20': 0, 'PE22': 0} and p.otgid.value == 0


def test_an_otg_adapter_in_at_boot_gets_bus_power(fw):
    p = Port(fw, id_pin=0)
    p.run(SETTLE + PASS_MS)
    assert p.pins['PC20'] == 1 and p.otgid.value == 1


def test_an_id_low_shorter_than_the_settle_time_never_powers_the_bus(fw):
    p = Port(fw)
    for _ in range(5):
        p.id = 0
        p.run(SETTLE)
        p.id = 1
        p.run(1000)
    assert p.powered == [] and p.otgid.value == 0


def test_vbus_on_the_port_restarts_the_wait(fw):
    """A charger or computer seen during the wait means the Q2 is not a host: the full settle time again once it is out."""
    p = Port(fw)
    p.id = 0
    p.run(SETTLE - 1000)
    p.cable = True
    p.run(1000)
    p.cable = False
    p.run(SETTLE)
    assert p.pins['PC20'] == 0
    p.run(PASS_MS)
    assert p.pins['PC20'] == 1


def test_a_standby_between_passes_restarts_the_wait(fw):
    p = Port(fw)
    p.id = 0
    p.run(SETTLE - 1000)
    p.now += 60000  # suspended to RAM: no passes
    p.run(2000)  # the first pass after it starts again, the second starts the wait
    assert p.pins['PC20'] == 0
    p.run(SETTLE)
    assert p.pins['PC20'] == 1
    assert p.records() == ['otg-on'] and p.commands == []


def test_a_wake_sets_the_pull_up_again_and_records_the_pins_then_and_5_s_on(fw):
    """usb_record_wake, from system.c's ringnav_standby as toolsIntoStandby returns, and usb_ui_poll's later line."""
    p = Port(fw, cable=True)
    wake, poll = fw.fn('usb_record_wake', None), fw.fn('usb_record_poll', None)
    wake()
    assert p.commands == ['/usr/bin/cmd_gpio set_func PD18 input pull_up >/dev/null 2>&1']
    assert p.records() == ['wake']
    p.now += 4000
    poll()
    assert p.records() == ['wake']
    p.now += 1000
    poll()
    poll()
    assert p.records() == ['wake', 'wake+5s']


def test_the_record_has_each_change_of_bus_power(fw):
    p = Port(fw)
    p.id = 0
    p.run(SETTLE + PASS_MS)
    p.id = 1
    p.run(2000)
    assert p.records() == ['otg-on', 'otg-off'] and p.commands == []


ms = lambda a, b: int(round((b - a) * 1000))


def unplugged_low(fw, low_ms):
    """A cable out of a computer, then the ID low for low_ms with nothing attached, then the cable back in."""
    p = Port(fw, cable=True)
    p.run(3000)
    p.cable = False
    p.run(1000)
    p.id = 0
    p.run(low_ms)
    p.id = 1
    p.run(2000)
    p.cable = True
    p.run(3000)
    assert p.backfeed == []
    return p


# The device's pin log of 2026-11-17 20:35 (q2-otg-state-fixbuild.log, build 8fd0ddcd, PD18 floating, no pull): after
# each unplug PD18 read low with no VBUS and nothing attached, and a 3 s settle let stock power the bus twice.
FLOATING = [ms(168.00, 176.56), ms(190.93, 196.30), ms(197.37, 202.16)]
# 21:0x (q2-pull-state.log, PD18's pull-up on, nothing attached): the line is still pulled low by the board, in pulses
# of 2 to 4 s some 6 to 7 s apart. The first ran 3.2 s and stock then powered the bus for a pass, so its end is taken
# where the ID read high again, 4.3 s on.
PULLED = [ms(1102.36, 1106.65), ms(1112.02, 1114.17), ms(1128.14, 1130.29)]


@pytest.mark.parametrize('low_ms', PULLED)
def test_with_the_pull_up_the_id_pulses_of_an_empty_port_never_power_the_bus(fw, low_ms):
    assert unplugged_low(fw, low_ms).powered == []


def test_a_floating_id_outlasts_any_settle_time_the_pull_up_is_what_ends_it(fw):
    """The settle alone: 4.8 and 5.4 s lows are held off, the 8.6 s one is not; the pull-up removes that kind of low."""
    powered = [unplugged_low(fw, low).powered != [] for low in FLOATING]
    assert powered == [True, False, False]
