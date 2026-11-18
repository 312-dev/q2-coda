"""OTG host power (usb_otg.c, docs/internals.md#otg-host-power): run the real ringnav_otg_status over a model of
stock's check_otg_status and the Q2's USB pins, and replay the pin log of 2026-11-17's back-feed.

Stock's check_otg_status (0x4f3408, V1.36), as disassembled: id = toolsGpioGetValue("PD18"); host = id == 0; when
g_last_otgid differs from host it writes PC20 = host (bus power) and PE22 = host (charger off), then g_last_otgid =
host. Nothing else in demo writes PC20 high (into_poweroff, device_reboot and device_standby write it 0)."""
import ctypes as C
import pathlib
import re

import pytest

PULL_UP = '/usr/bin/cmd_gpio set_func PD18 input pull_up >/dev/null 2>&1'
PULL_FUNC = b'/tmp/q2-pd18.func'
PULL_CHECK = '/usr/bin/cmd_gpio get_func PD18 >/tmp/q2-pd18.func 2>&1'
HOST_DEV = b'/sys/bus/usb/devices/1-1/idVendor'
rmw = lambda addr, op: f'devmem {addr} 32 $(( $(devmem {addr}) {op} ))'
HOLD = rmw('0x1000003c', '| 0x20000000') + '; ' + rmw('0x10000040', '| 0x01800000')  # the PHY's ID pull-up, role device
FROM_PIN = rmw('0x10000040', '& ~0x01800000')  # the controller's role from the pin
PASS_MS = 1000  # on_wm_idle_status runs check_otg_status about once a second
# usb_otg.c's settle time, read from the source so the timings below follow it
_SOURCE = (pathlib.Path(__file__).parents[2]/'patch/usb_otg.c').read_text()


def define(name):
    m = re.search(rf'#define {name} (\d+)\b', _SOURCE)
    assert m, f'usb_otg.c: {name} is not a plain #define'
    return int(m[1])


SETTLE, BLANK, TRIAL = define('OTG_SETTLE_MS'), define('OTG_BLANK_MS'), define('OTG_TRIAL_MS')


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
        self.func = b'input pull_up\n'  # what cmd_gpio get_func PD18 answers into PULL_FUNC
        fw.mock('fw_system', lambda cmd, *_: self.commands.append(C.string_at(cmd).decode()))
        self.hostdev = False  # a device enumerated on the Q2's host port
        fw.mock('fw_fopen', self.fopen)
        fw.mock('fw_fread', self.fread)
        fw.mock('toolsGpioSetValue', self.write)

    def fopen(self, path, *_):
        name = C.string_at(path)
        return 9 if name == PULL_FUNC else 10 if name == HOST_DEV and self.hostdev else 0

    def write(self, name, value, *_):
        self.pins[C.string_at(name).decode()] = value

    def fread(self, buf, size, n, f, *_):
        data = self.func[:size * n]
        C.memmove(buf, data, len(data))
        return len(data) // size

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


ms = lambda a, b: int(round((b - a) * 1000))


def unplug(p, then_ms=1000):
    """A cable out of a computer: VBUS leaves the port."""
    p.cable = True
    p.run(3000)
    p.cable = False
    p.run(then_ms)


# The device's pin log of 2026-11-17 (q2-replug2-state.log, build p32b, one sample each 0.5 s): each unplug, when PD18
# read low, the first sample that read VBUS again, and when PD18 read high. Stock had turned PC20 on by that VBUS sample,
# and its own power masks a cable, so the log cannot say when the cable went back in; the replay puts it there, about a
# second after the ID went low.
LOGGED = [  # (unplugged, PD18 low, VBUS back, PD18 high) in seconds of uptime
    (607.90, 608.97, 610.02, 614.28),
    (622.25, 623.32, 623.85, 642.40),
    (642.94, 644.53, 645.06, 654.60),
    (655.13, 658.31, 658.84, 664.14),
    (665.20, 665.73, 666.26, 672.09),
]


@pytest.mark.parametrize('out,low,back,high', LOGGED)
def test_a_replug_into_a_computer_while_the_id_pin_reads_low_never_powers_the_bus(fw, out, low, back, high):
    p = Port(fw)
    unplug(p, ms(out, low))
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


# 22:43 (q2-ab-state.log, build 8fd0ddcd with PD18's pull-up, the PHY's ID pull-up and the controller held at device,
# 0.25 s samples, nothing attached): VBUS left at 431.82, PD18 read low at 443.93 (12.1 s on) and the 6 s settle powered
# the bus at 450.47; PD18 read high at 461.11, PC20 went off, and 7 s after that VBUS left PD18 read low again (468.54)
# and the bus came on again at 476.33: a loop into an empty port, and a laptop plugged in then sent no VBUS.
AB_OUT, AB_LOW, AB_HIGH = 431.82, 443.93, 461.11


def test_the_board_s_id_pulse_after_vbus_leaves_never_powers_the_bus_nor_loops(fw):
    p = Port(fw)
    unplug(p, ms(AB_OUT, AB_LOW))
    p.id = 0
    p.run(ms(AB_LOW, AB_HIGH))
    p.id = 1
    p.run(ms(AB_HIGH, 468.54))
    assert p.powered == []
    p.cable = True  # the laptop: VBUS from it, the Q2 a device
    p.run(5000)
    assert p.powered == [] and p.otgid.value == 0


def test_the_logged_pulse_under_the_settle_alone_powers_the_bus(fw):
    """Without the blank (vbus never seen leaving), the same 17 s low passes the 6 s settle: why the blank is needed."""
    p = Port(fw)
    p.id = 0
    p.run(ms(AB_LOW, AB_HIGH))
    assert p.powered != []


def test_an_otg_adapter_gets_bus_power_once_the_id_pin_has_settled_and_keeps_it_with_a_device(fw):
    """A DAC or a drive on an OTG adapter: ID grounded, no VBUS on the port. The bus power comes on after OTG_SETTLE_MS,
    the controller's role handed to the pin first, stays while a device is on the host port, and goes off in the pass the
    ID reads high, the controller held at device again."""
    p = Port(fw)
    p.run(3000)
    p.id = 0
    p.run(SETTLE)  # the wait starts at the first low pass: SETTLE - 1000 of it so far
    assert p.pins['PC20'] == 0 and p.otgid.value == 0
    p.run(PASS_MS)
    assert p.pins == {'PC20': 1, 'PE22': 1} and p.otgid.value == 1 and p.commands == [FROM_PIN]
    p.hostdev = True
    p.run(60000)
    assert p.pins['PC20'] == 1
    p.id = 1  # the adapter out
    p.hostdev = False
    p.run(PASS_MS)
    assert p.pins == {'PC20': 0, 'PE22': 0} and p.otgid.value == 0 and p.commands == [FROM_PIN, HOLD]


def test_bus_power_with_nothing_enumerated_goes_off_and_is_not_tried_again_until_a_cable(fw):
    p = Port(fw)
    p.id = 0
    p.run(SETTLE + PASS_MS)
    assert p.pins['PC20'] == 1
    p.run(TRIAL - PASS_MS)
    assert p.pins['PC20'] == 1
    p.run(PASS_MS)
    assert p.pins['PC20'] == 0 and p.otgid.value == 0 and p.records() == ['otg-on', 'otg-trial-off']
    p.run(60000)  # the ID still low (an empty adapter, or the board's pulse): no retry
    p.id = 1
    p.run(20000)
    p.id = 0
    p.run(30000)
    assert p.pins['PC20'] == 0
    p.id = 1
    p.cable = True  # a charger: VBUS seen with the bus power off clears it
    p.run(2000)
    p.cable = False
    p.run(BLANK + 1000)  # its removal blanks the ID for OTG_BLANK_MS
    p.id = 0
    p.run(SETTLE + PASS_MS)
    assert p.pins['PC20'] == 1


def test_an_otg_adapter_put_in_soon_after_an_unplug_waits_for_its_replug(fw):
    """ponytail: an adapter in within OTG_BLANK_MS of VBUS leaving is taken for the board's pulse until it is put in
    again."""
    p = Port(fw)
    unplug(p, 3000)
    p.id = 0
    p.hostdev = True
    p.run(60000)
    assert p.powered == []
    p.id = 1
    p.run(2000)
    p.id = 0
    p.run(SETTLE + PASS_MS)
    assert p.pins['PC20'] == 1


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
    """A charger or computer seen during the wait means the Q2 is not a host; once it is out, its removal blanks the ID
    and the low must start again after that."""
    p = Port(fw)
    p.id = 0
    p.run(SETTLE - 1000)
    p.cable = True
    p.run(1000)
    p.cable = False
    p.run(BLANK + SETTLE)
    assert p.powered == []
    p.id = 1
    p.run(1000)
    p.id = 0
    p.run(SETTLE + PASS_MS)
    assert p.pins['PC20'] == 1


def test_a_standby_between_passes_restarts_the_wait_and_clears_a_refused_trial(fw):
    p = Port(fw)
    p.id = 0
    p.run(SETTLE + TRIAL + PASS_MS)
    assert p.records() == ['otg-on', 'otg-trial-off']
    p.now += 60000  # suspended to RAM: no passes
    p.run(2000)  # the first pass after it starts again, the second starts the wait
    assert p.pins['PC20'] == 0
    p.run(SETTLE)
    assert p.pins['PC20'] == 1, (p.records(), p.commands, p.pins)


def test_a_wake_sets_both_pull_ups_and_the_device_role_again_and_records_the_pins_then_and_5_s_on(fw):
    """usb_record_wake, from system.c's ringnav_standby as toolsIntoStandby returns, and usb_ui_poll's later line."""
    p = Port(fw, cable=True)
    wake, poll = fw.fn('usb_record_wake', None), fw.fn('usb_record_poll', None)
    wake()
    assert p.commands == [PULL_UP, HOLD] and p.records() == ['wake']
    p.now += 4000
    poll()
    assert p.records() == ['wake']
    p.now += 1000
    poll()
    poll()
    assert p.records() == ['wake', 'wake+5s'] and p.commands == [PULL_UP, HOLD, PULL_CHECK]


@pytest.mark.parametrize('func,word,commands', [(b'input pull_up\n', 'boot', [HOLD, PULL_CHECK]),
                                                (b'input pull_hiz\n', 'boot-pull-off', [HOLD, PULL_CHECK, PULL_UP])])
def test_the_records_first_line_follows_stocks_pin_setup_with_the_id_pins_pull(fw, func, word, commands):
    """platform_init's hook: stock's run first (it sets PD18 input pull_up, tools/build.py ID_PULL_UP), then the PHY's
    ID pull-up and the device role, then the line with the pull cmd_gpio reports; a pull that is not the pull-up is set
    again and the line says it was off."""
    p = Port(fw)
    p.func = func
    order = []
    fw.mock('stock_platform_init_trampoline', lambda *_: order.append('stock') or 5)
    assert fw.fn('ringnav_platform_init', C.c_int)() == 5
    assert order == ['stock'] and p.records() == [word] and p.commands == commands


def test_the_record_has_each_change_of_bus_power(fw):
    p = Port(fw)
    p.id = 0
    p.hostdev = True
    p.run(SETTLE + PASS_MS)
    p.id = 1
    p.run(2000)
    assert p.records() == ['otg-on', 'otg-off']


# The pull-up's pulses with nothing attached (q2-pull-state.log, 21:0x) and the floating lows before it
# (q2-otg-state-fixbuild.log, 20:35), each after an unplug: all start within the blank.
FLOATING = [(ms(166.94, 168.00), ms(168.00, 176.56)), (ms(188.26, 190.93), ms(190.93, 196.30))]
PULLED = [(ms(1095.34, 1102.36), ms(1102.36, 1106.65))]


@pytest.mark.parametrize('after,low', FLOATING + PULLED)
def test_the_lows_after_an_unplug_never_power_the_bus(fw, after, low):
    p = Port(fw)
    unplug(p, after)
    p.id = 0
    p.run(low)
    p.id = 1
    p.run(2000)
    p.cable = True
    p.run(3000)
    assert p.powered == []
