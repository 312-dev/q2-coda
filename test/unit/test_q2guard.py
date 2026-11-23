"""q2guard's core (patch/q2guard_core.c, docs/internals.md#overheat-guardian): the sensor's verdict, the warning and
the protective power-off, the CPU-load fallback with no sensor, the log line, the top three, and the log's rotation
over card_store.c's RAM ring, which holds the lines while the card is away; and its PMIC reader (patch/q2guard_pmic.c):
the register checks, the sampling window's writes and the I2C error rules, over a recorded bus. Built for this machine
with test/unit/q2guard_host.c."""
import ctypes as C
import datetime
import os
import pathlib
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
NONE = -100000
EV_NONE, EV_SENSOR_DOWN, EV_SENSOR_OK, EV_WARN, EV_LOAD_WARN, EV_LOAD_CANCELLED, EV_OFF = range(7)
WARN, REARM, SHUT = 550, 500, 620  # q2guard.h, tenths of a degree of the PMIC's die
FILE_MAX, RAM = 262144, 65536


@pytest.fixture(scope='module')
def L(tmp_path_factory):
    out = tmp_path_factory.mktemp('q2guard') / 'q2guard.so'
    subprocess.run(['clang', '-shared', '-fPIC', '-O1', '-DQ2GUARD_HOST', '-Wall', '-Werror', '-Wno-unused-parameter',
                    f'-DPEQ_ROOT="{out.parent}"', ROOT / 'patch/q2guard_core.c', ROOT / 'patch/q2guard_pmic.c',
                    ROOT / 'patch/card_store.c',
                    ROOT / 'test/unit/q2guard_host.c', '-o', out], check=True)
    lib = C.CDLL(str(out))
    for name in ('guard_new', 'log_new', 'table_new', 'log_ram'):
        getattr(lib, name).restype = C.c_void_p
    lib.log_ram.argtypes = lib.log_used.argtypes = lib.guard_load.argtypes = [C.c_void_p]
    lib.feed.argtypes = [C.c_void_p, C.c_uint, C.c_int, C.c_int, C.c_int, C.c_int, C.c_int, C.c_int,
                         C.POINTER(C.c_int)]
    if hasattr(lib, 'feed_slept'):
        lib.feed_slept.argtypes = [C.c_void_p, C.c_uint, C.c_int, C.c_int, C.c_int, C.c_int, C.c_int, C.c_int,
                                   C.c_int, C.POINTER(C.c_int)]
    lib.log_new.argtypes = [C.c_char_p]
    lib.log_dropped.argtypes = [C.c_void_p]
    lib.heat_add.argtypes = [C.c_void_p, C.c_char_p]
    lib.heat_flush.argtypes = [C.c_void_p, C.c_char_p]
    lib.guard_state.argtypes = [C.c_void_p]
    lib.guard_state.restype = C.c_char_p
    lib.guard_why.argtypes = [C.c_void_p]
    lib.guard_why.restype = C.c_char_p
    lib.pmic_sample.argtypes = [C.POINTER(Bus), C.POINTER(Pmic), C.POINTER(C.c_int)]
    return lib


RD = C.CFUNCTYPE(C.c_int, C.c_void_p, C.c_int)
WR = C.CFUNCTYPE(C.c_int, C.c_void_p, C.c_int, C.c_int)
SLEEP = C.CFUNCTYPE(None, C.c_void_p, C.c_int)


class Bus(C.Structure):
    _fields_ = [('rd', RD), ('wr', WR), ('sleep_ms', SLEEP), ('ctx', C.c_void_p)]


class Pmic(C.Structure):
    _fields_ = [('state', C.c_int), ('errors', C.c_int), ('measured', C.c_int), ('first_ms', C.c_int),
                ('note', C.c_char * 96)]


class Guard:
    """A guard fed one sample per 5 s."""
    def __init__(self, L):
        self.L, self.g, self.now, self.out = L, L.guard_new(), 0, (C.c_int * 3)()
        self.events = []

    def step(self, temp=NONE, busy=-1, cancel=0, acked=0, saved=0, dt=5000, charge=0, slept=0):
        self.now += dt
        if hasattr(self.L, 'feed_slept'):
            ev = self.L.feed_slept(self.g, self.now, temp, busy, cancel, acked, saved, charge, slept, self.out)
        else:  # a build before guard_in.slept: the suspend unseen
            ev = self.L.feed(self.g, self.now, temp, busy, cancel, acked, saved, charge, self.out)
        self.events.append(ev)
        return ev

    def actions(self):
        return dict(zip(('kill', 'flush', 'off'), self.out))

    def state(self):
        return self.L.guard_state(self.g).decode()

    def why(self):
        return self.L.guard_why(self.g).decode()


def real(g, n=2, base=300):
    """A sensor that has varied: trusted."""
    for i in range(n):
        g.step(base + i)


def ramp(g, frm, to, step=25):
    """Readings from frm to to in steps a die can make (under GUARD_JUMP_DC)."""
    t = frm
    while t != to:
        t = min(t + step, to) if to > t else max(t - step, to)
        g.step(t)


# ---- the sensor ----

def test_a_varying_reading_in_range_makes_the_sensor_usable(L):
    g = Guard(L)
    assert g.step(300) == EV_NONE and g.state() == 'pending'
    assert g.step(301) == EV_SENSOR_OK and g.state() == 'ok'


def test_a_constant_sensor_never_arms_and_is_reported_once(L):
    """25.0 C for five minutes is a placeholder, not a thermistor: down, the card once, and hot readings after it
    (a constant can't be trusted to change) do nothing until it varies."""
    g = Guard(L)
    events = [g.step(250) for _ in range(60)]
    assert events[:59] == [EV_NONE] * 59 and events[59] == EV_SENSOR_DOWN and g.state() == 'nosensor'
    assert [g.step(250) for _ in range(10)] == [EV_NONE] * 10


@pytest.mark.parametrize('bad', [NONE, 1001, -1, 2550, 345])  # 345: over 3 C from the last good reading
def test_three_bad_readings_in_a_row_put_the_sensor_down_two_do_not(L, bad):
    g = Guard(L)
    real(g)
    assert [g.step(bad), g.step(bad), g.step(310)] == [EV_NONE] * 3
    assert [g.step(bad), g.step(bad), g.step(bad)] == [EV_NONE, EV_NONE, EV_SENSOR_DOWN]


def test_an_out_of_range_sensor_never_arms_the_power_off(L):
    g = Guard(L)
    for _ in range(20):
        assert g.step(1001) in (EV_NONE, EV_SENSOR_DOWN)
    assert not g.actions()['off'] and g.state() == 'nosensor'


def test_a_sensor_that_comes_back_and_varies_is_trusted_again(L):
    g = Guard(L)
    real(g)
    for _ in range(3):
        g.step(NONE)
    assert g.step(300) == EV_NONE and g.step(302) == EV_SENSOR_OK


# ---- the temperature ----

@pytest.mark.parametrize('t, near', [(0, 1), (1000, 999)])
def test_0_and_100_c_are_readings(L, t, near):
    g = Guard(L)
    assert [g.step(t), g.step(near)] == [EV_NONE, EV_SENSOR_OK]


@pytest.mark.parametrize('t', [-1, 1001])
def test_below_0_or_over_100_c_is_out_of_range(L, t):
    g = Guard(L)
    assert [g.step(t) for _ in range(3)] == [EV_NONE, EV_NONE, EV_SENSOR_DOWN] and g.why() == 'out of range'


def test_a_jump_of_3_c_is_a_misread_that_neither_counts_nor_breaks_a_run(L):
    """Two readings over the power-off, a misread far above, then a third over it: the power-off; the misread did
    not count as the third (it would have powered off a sample early) and did not reset the run."""
    g = Guard(L)
    real(g, base=590)
    ramp(g, 591, 615)
    g.step(SHUT + 5), g.step(SHUT + 5)
    assert g.state() != 'poweroff-hot'
    g.step(SHUT + 5 + 31)
    assert g.state() != 'poweroff-hot'
    assert g.step(SHUT + 5) == EV_OFF


def test_three_jumps_in_a_row_put_it_down_and_say_why(L):
    g = Guard(L)
    real(g)
    assert [g.step(340), g.step(260), g.step(340)] == [EV_NONE, EV_NONE, EV_SENSOR_DOWN]
    assert g.state() == 'nosensor' and g.why() == 'jumps'
    assert g.step(500) == EV_NONE and g.step(501) == EV_SENSOR_OK  # back: a fresh start, no old reference


CABLE, CHARGING = 1, 2  # q2guard.h GUARD_CHARGE_*: the charger's supply online, the battery charging

# The die's readings on a Q2 (q2-heat, 2026-11-20) at a cable plugged in (18:10:11, Discharging to Charging) and pulled
# out (18:12:45, Charging to Discharging): the charger is on the PMIC's die, so its turning on or off steps the reading
# 4.5 and 3.3 C in one sample, then it moves a few tenths a sample again.
PLUG = [(357, 0), (402, CABLE | CHARGING), (409, CABLE | CHARGING), (423, CABLE | CHARGING),
        (428, CABLE | CHARGING), (432, CABLE | CHARGING)]
PULL = [(443, CABLE | CHARGING), (444, CABLE | CHARGING), (444, CABLE | CHARGING), (411, 0), (401, 0), (396, 0),
        (389, 0)]


@pytest.mark.parametrize('seq', [PLUG, PULL], ids=['plug', 'pull'])
def test_the_step_at_a_charger_change_is_a_reading_not_a_sensor_down(L, seq):
    g = Guard(L)
    t0, c0 = seq[0]
    g.step(t0 - 2, charge=c0), g.step(t0 - 1, charge=c0)  # trusted, the charger as it was
    events = [g.step(t, charge=c) for t, c in seq]
    assert EV_SENSOR_DOWN not in events and g.state() == 'ok'


@pytest.mark.parametrize('seq', [PLUG, PULL], ids=['plug', 'pull'])
def test_the_same_step_with_no_charger_change_is_still_a_jump(L, seq):
    g = Guard(L)
    t0, c0 = seq[0]
    g.step(t0 - 2, charge=c0), g.step(t0 - 1, charge=c0)
    events = [g.step(t, charge=c0) for t, _ in seq]
    assert EV_SENSOR_DOWN in events and g.why() == 'jumps'


def test_a_standby_s_cooling_is_no_jump_and_raises_no_card(L):
    """The owner's Q2 on 2026-11-23: a Standby 20 s after a boot, the die at 40.1 C after the boot's load, 35.8 C at
    the first reading after the 75 s suspend, then 36.0, 36.2, 36.4 (its heat log). Each was held to 40.1 and was a
    jump, so the sensor went down ("No Temperature Sensor") 15 s after the wake and came back 15 s later. A suspend
    between two readings (in.slept) makes the first after it the reference: no event, the sensor trusted throughout.
    Red before guard_in.slept: EV_SENSOR_DOWN, why jumps."""
    g = Guard(L)
    for t in (392, 398, 401):
        g.step(t, charge=1)
    assert g.state() == 'ok'
    events = [g.step(358, charge=1, slept=1)] + [g.step(t, charge=1) for t in (360, 362, 364, 364, 365, 364)]
    assert EV_SENSOR_DOWN not in events and g.state() == 'ok', (events, g.why())


def test_a_standby_does_not_hide_a_sensor_that_died_in_it(L):
    """A sensor that reads nothing after the wake is down after three readings, as before: the suspend resets the
    reference, not the error count that follows."""
    g = Guard(L)
    real(g)
    events = [g.step(NONE, slept=1), g.step(NONE), g.step(NONE)]
    assert events == [EV_NONE, EV_NONE, EV_SENSOR_DOWN] and g.why() == 'none read'


def test_a_jump_with_no_standby_between_is_still_a_jump(L):
    """The same 4.3 C step with no suspend between the readings is a misread, three of them the sensor down."""
    g = Guard(L)
    for t in (392, 398, 401):
        g.step(t, charge=1)
    events = [g.step(t, charge=1) for t in (358, 360, 362)]
    assert events[-1] == EV_SENSOR_DOWN and g.why() == 'jumps'


def test_a_charger_change_waives_the_jump_for_two_readings_and_no_further(L):
    """The change and the reading after it may step (the die may be read just before the cable is); a step three
    readings on is a misread again, and three of them put the sensor down."""
    g = Guard(L)
    real(g, base=357)
    assert [g.step(358, charge=CABLE), g.step(402, charge=CABLE)] == [EV_NONE, EV_NONE]
    assert [g.step(t, charge=CABLE) for t in (450, 450, 450)] == [EV_NONE, EV_NONE, EV_SENSOR_DOWN]
    assert g.why() == 'jumps'


@pytest.mark.parametrize('bad', [900, 0, 2550])
def test_a_charger_change_does_not_let_a_wild_reading_through(L, bad):
    """The waiver is for a step the charger can make (6 C at most): a broken sensor's 90 C at a plug-in is still a
    misread, and goes on putting the sensor down."""
    g = Guard(L)
    real(g, base=357)
    assert [g.step(bad, charge=CABLE) for _ in range(3)] == [EV_NONE, EV_NONE, EV_SENSOR_DOWN]


def test_a_trusted_sensor_stuck_while_the_load_swings_goes_down(L):
    """A real die moves when the CPU goes from idle to flat out; one value for five minutes through that is a
    sensor that stopped converting."""
    g = Guard(L)
    real(g, base=380)
    events = [g.step(381, busy=10 if i % 2 else 90) for i in range(60)]
    assert EV_SENSOR_DOWN in events and g.why() == 'stuck while the load changed'
    assert events.index(EV_SENSOR_DOWN) == 58  # the 60th reading of 381 (the first was in real)


def test_a_trusted_sensor_steady_while_idle_stays_up(L):
    g = Guard(L)
    real(g, base=380)
    assert [g.step(381, busy=5 + i % 3) for i in range(120)] == [EV_NONE] * 120 and g.state() == 'ok'


def test_two_readings_over_the_power_off_do_nothing_three_power_off(L):
    g = Guard(L)
    real(g, base=600)
    for t in (SHUT + 10, SHUT + 10, SHUT - 10, SHUT, SHUT + 5):
        assert g.step(t) != EV_OFF
    assert g.step(SHUT + 20) == EV_OFF and g.actions()['flush'] == 1 and g.state() == 'poweroff-hot'


def test_the_warning_shows_once_per_episode_and_rearms_below_rearm(L):
    g = Guard(L)
    real(g, base=540)
    assert [g.step(WARN + 10), g.step(WARN + 10), g.step(WARN + 10)] == [EV_NONE, EV_NONE, EV_WARN]
    assert [g.step(WARN + 10) for _ in range(5)] == [EV_NONE] * 5
    for t in (WARN - 15, REARM + 10, REARM + 5, REARM + 30):  # still warm: no re-arm (in steps a die makes)
        g.step(t)
    assert [g.step(WARN) for _ in range(3)] == [EV_NONE] * 3
    g.step(REARM + 25), g.step(REARM - 1), g.step(REARM + 25)  # cooled
    assert [g.step(WARN) for _ in range(3)] == [EV_NONE, EV_NONE, EV_WARN]


def test_a_power_off_is_never_undone_by_cooling(L):
    g = begin(L)
    assert g.step(300, acked=1, dt=1000) == EV_NONE and g.state() == 'poweroff-hot'


def test_with_a_sensor_cpu_load_never_powers_off(L):
    g = Guard(L)
    real(g)
    for i in range(60):
        assert g.step(300 + i % 2, busy=100) in (EV_NONE,)
    assert not g.actions()['off']


# ---- the power-off ----

def begin(L):
    g = Guard(L)
    real(g, base=600)
    for _ in range(3):
        g.step(SHUT + 10)
    assert g.state() == 'poweroff-hot'
    return g


def test_demo_answering_leaves_the_power_off_to_it_until_the_30_s_deadline(L):
    g = begin(L)
    for _ in range(28):
        g.step(600, acked=1, saved=1, dt=1000)
        assert g.actions() == {'kill': 0, 'flush': 0, 'off': 0}
    g.step(600, acked=1, saved=1, dt=1000)                     # 29 s
    assert g.actions()['off'] == 0
    g.step(600, acked=1, saved=1, dt=1000)                     # 30 s
    assert g.actions()['off'] == 1


def test_demo_hung_q2guard_stops_the_player_at_5_s_and_powers_off_once_the_log_is_saved(L):
    g = begin(L)
    seen = []
    for t in range(1, 8):
        g.step(600, saved=t >= 6, dt=1000)
        seen.append(dict(g.actions()))
    assert [s['kill'] for s in seen] == [0, 0, 0, 0, 1, 0, 0]
    assert [s['off'] for s in seen] == [0, 0, 0, 0, 0, 1, 0]


def test_a_log_write_that_never_finishes_still_powers_off_at_30_s(L):
    g = begin(L)
    offs = []
    for _ in range(31):
        g.step(600, saved=0, dt=1000)
        offs.append(g.actions()['off'])
    assert offs.index(1) == 29 and sum(offs) == 1             # at 30 s, once


# ---- no sensor: the CPU load ----

def nosensor(L):
    g = Guard(L)
    for _ in range(3):
        g.step(NONE)
    assert g.state() == 'nosensor'
    return g


@pytest.mark.parametrize('busy, warns', [(84, False), (85, True)])
def test_two_minutes_at_85_percent_warns_84_does_not(L, busy, warns):
    g = nosensor(L)
    events = [g.step(busy=busy) for _ in range(30)]
    assert (EV_LOAD_WARN in events) == warns
    if warns:
        assert events.index(EV_LOAD_WARN) == 23                 # the window's 24th sample


def test_no_cancel_in_60_s_powers_off(L):
    g = nosensor(L)
    for _ in range(24):
        g.step(busy=90)
    assert g.state() == 'loadwarn'
    events = [g.step(busy=10) for _ in range(12)]
    assert events[:11] == [EV_NONE] * 11 and events[11] == EV_OFF and g.state() == 'poweroff-load'


def test_cancel_dismisses_and_rearms_only_after_a_calm_minute(L):
    g = nosensor(L)
    for _ in range(24):
        g.step(busy=90)
    assert g.step(busy=90, cancel=1) == EV_LOAD_CANCELLED
    assert [g.step(busy=95) for _ in range(30)] == [EV_NONE] * 30  # still busy: no new warning
    for _ in range(11):
        g.step(busy=40)
    g.step(busy=60)                                              # the calm minute starts again
    for _ in range(12):
        g.step(busy=40)
    assert L.guard_load(g.g) == 0
    events = [g.step(busy=90) for _ in range(24)]
    assert events[-1] == EV_LOAD_WARN


def test_a_sensor_that_comes_back_ends_the_load_countdown(L):
    g = nosensor(L)
    for _ in range(24):
        g.step(busy=90)
    g.step(300, busy=90)
    assert g.step(305, busy=90) == EV_LOAD_CANCELLED and g.state() == 'ok'


# ---- the line ----

def line(L, empty=0):
    buf = C.create_string_buffer(512)
    n = L.line(buf, 512, empty)
    return buf.raw[:n].decode()


def test_a_line_holds_every_field_in_order(L):
    when = datetime.datetime.fromtimestamp(1795442585, datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    assert line(L) == (f'{when} up=1234 t=41.5 die=41.5 adc=6884 raw=27 lv=87 mv=4105 st=Charging cab=1 udc=configured gad=adb scr=1 '
                       'br=80 wifi=1 bt=0 mhz=- cpu=23 top=hciplayer:12.3,demo:8.0,adbd:0.3 thr=27 np=63 g=ok\n')


def test_a_line_with_nothing_read_says_so_field_by_field(L):
    assert line(L, 1) == ('1970-01-01T00:00:00Z up=0 t=- die=- adc=- raw=- lv=- mv=- st=- cab=- udc=- gad=- scr=- br=- wifi=- '
                          'bt=- mhz=- cpu=- top=- thr=- np=- g=-\n')


# ---- the top three ----

def top(L, table, rows, span):
    n = len(rows)
    pids = (C.c_int * max(n, 1))(*[r[0] for r in rows])
    names = ((C.c_char * 16) * max(n, 1))(*[(C.c_char * 16)(*r[1].encode()) for r in rows])
    ticks = (C.c_uint * max(n, 1))(*[r[2] for r in rows])
    out = (C.c_ubyte * (20 * 3))()  # three struct proc_top: char[16], int
    L.top_three(C.c_void_p(table), n, pids, names, ticks, span, out)
    res = []
    for k in range(3):
        name = bytes(out[20 * k:20 * k + 16]).split(b'\0')[0].decode()
        pct = int.from_bytes(bytes(out[20 * k + 16:20 * k + 20]), 'little', signed=True)
        if name:
            res.append((name, pct))
    return res


def test_the_top_three_are_the_largest_deltas_since_the_last_pass(L):
    t = L.table_new()
    assert top(L, t, [(1, 'init', 100), (50, 'demo', 1000), (60, 'hciplayer', 500), (70, 'adbd', 10)], 500) == []
    rows = [(1, 'init', 101), (50, 'demo', 1100), (60, 'hciplayer', 750), (70, 'adbd', 15), (80, 'sh', 40)]
    assert top(L, t, rows, 500) == [('hciplayer', 500), ('demo', 200), ('sh', 80)]


# ---- the log ----

def new_log(L, d):
    """A log writing to dir d, with the card away (as the watching process sees it)."""
    L.card(0)
    return L.log_new(str(d).encode())


def flush(L, log, d):
    """What the writing child does: the card is up for it."""
    L.card(1)
    try:
        return L.heat_flush(log, str(d).encode())
    finally:
        L.card(0)


def test_lines_are_held_until_a_flush_writes_them_in_one_go(L, tmp_path):
    d = tmp_path / '.q2muse/logs'
    log = new_log(L, d)
    for i in range(3):
        L.heat_add(log, f'line {i}\n'.encode())
    assert not d.exists()
    assert flush(L, log, d) == 0 and L.log_used(log) == 0
    assert (d / 'q2-heat.0').read_text() == 'line 0\nline 1\nline 2\n'


def test_a_full_file_moves_0_to_1_at_the_next_flush_and_never_splits_a_line(L, tmp_path):
    d = tmp_path / 'logs'
    log = new_log(L, d)
    one = 'x' * 199 + '\n'
    for _ in range(FILE_MAX // 200 + 1):  # a flush at a time, as the ring holds a few hundred lines
        L.heat_add(log, one.encode())
        if L.log_used(log) > RAM - 400:
            assert flush(L, log, d) == 0
    assert flush(L, log, d) == 0 and not (d / 'q2-heat.1').exists()
    first = (d / 'q2-heat.0').read_text()
    assert len(first) == (FILE_MAX // 200 + 1) * 200 >= FILE_MAX
    L.heat_add(log, one.encode())
    assert flush(L, log, d) == 0
    assert (d / 'q2-heat.1').read_text() == first  # moved whole
    assert (d / 'q2-heat.0').read_text() == one


def test_the_heat_log_keeps_four_files(L, tmp_path):
    """q2-heat.0 to .3 of 256 kB: after a move 768 kB stay, about four hours of a line every 5 s (warm, busy or
    plugged in), as long as the Bluetooth log's six (docs/internals.md#overheat-guardian)."""
    d = tmp_path / 'logs'
    log = new_log(L, d)
    d.mkdir()
    for k in range(4):
        (d / f'q2-heat.{k}').write_text(str(k) * FILE_MAX)
    L.heat_add(log, b'new\n')
    assert flush(L, log, d) == 0
    assert [(d / f'q2-heat.{k}').read_text()[:1] for k in range(4)] == ['n', '0', '1', '2']


def test_card_away_then_back_flushes_in_order(L, tmp_path):
    """Unmounted (USB Transfer Files, pulled, read-only): the lines wait in RAM; when the card is back they are
    written in order, after what the card already had."""
    d = tmp_path / 'logs'
    log = new_log(L, d)
    L.heat_add(log, b'before\n')
    assert flush(L, log, d) == 0
    for i in range(3):
        L.heat_add(log, f'away {i}\n'.encode())
    assert flush_away(L, log, d) == -1 and L.log_used(log) == 21  # held, nothing lost
    assert (d / 'q2-heat.0').read_text() == 'before\n'
    assert flush(L, log, d) == 0
    assert (d / 'q2-heat.0').read_text() == 'before\naway 0\naway 1\naway 2\n'


def test_the_card_hold_is_live_only_while_its_process_is(L, tmp_path):
    """/tmp/mmc_hold (card_release.c): demo's pid while it lets the card go. A live pid holds; a pid gone (demo
    restarted), an empty file or none holds nothing, so a hold demo never removed cannot stop the log for good."""
    hold = tmp_path / 'mmc_hold'
    L.card_hold_live.argtypes = [C.c_char_p]
    assert L.card_hold_live(str(hold).encode()) == 0
    hold.write_text(f'{os.getpid()}\n')
    assert L.card_hold_live(str(hold).encode()) == 1
    child = gone_pid()
    hold.write_text(f'{child}\n')
    assert L.card_hold_live(str(hold).encode()) == 0
    hold.write_text('')
    assert L.card_hold_live(str(hold).encode()) == 0


def test_lines_logged_while_the_card_is_held_follow_in_order_once_it_is_back(L, tmp_path):
    """Standby's window: the hold up (q2guard.c card_ready false, so no writer is started and the watcher's ring only
    holds), the card unmounted, the suspend, the wake. The lines of that window, the wake's among them, are written
    after the card is back, in order, after what the card had, none lost."""
    d = tmp_path / 'logs'
    hold = tmp_path / 'mmc_hold'
    L.card_hold_live.argtypes = [C.c_char_p]
    log = new_log(L, d)
    L.heat_add(log, b'before\n')
    assert flush(L, log, d) == 0
    hold.write_text(f'{os.getpid()}\n')
    window = ['card: unmounted (1 mount) for standby', 'standby', 'wake', 'card: back, mounted once']
    for line in window:
        L.heat_add(log, f'{line}\n'.encode())
        if not L.card_hold_live(str(hold).encode()):  # q2guard.c flush_start's card_ready
            assert flush(L, log, d) == 0
    assert (d / 'q2-heat.0').read_text() == 'before\n', 'nothing written while held'
    hold.unlink()  # card_back
    assert not L.card_hold_live(str(hold).encode()) and flush(L, log, d) == 0
    assert (d / 'q2-heat.0').read_text().splitlines() == ['before', *window]


def flush_away(L, log, d):
    L.card(0)
    return L.heat_flush(log, str(d).encode())


def test_a_card_that_refuses_the_write_keeps_the_lines(L, tmp_path):
    d = tmp_path / 'logs'
    log = new_log(L, d)
    d.mkdir()
    d.chmod(0o500)
    try:
        L.heat_add(log, b'kept\n')
        assert flush(L, log, d) == -1 and L.log_used(log) == 5
    finally:
        d.chmod(0o700)
    assert flush(L, log, d) == 0
    assert (d / 'q2-heat.0').read_text() == 'kept\n'


def test_ram_is_bounded_the_oldest_lines_go_and_are_counted(L, tmp_path):
    d = tmp_path / 'logs'
    log = new_log(L, d)
    one = 'y' * 99 + '\n'
    for _ in range(RAM // 100 + 10):
        L.heat_add(log, one.encode())
    assert L.log_used(log) <= RAM and L.log_dropped(log) >= 10
    assert flush(L, log, d) == 0
    head = (d / 'q2-heat.0').read_text().splitlines()[0]
    assert head.startswith('== ') and head.endswith('lines lost while the card was away')


# ---- the PMIC's die temperature (q2guard_pmic.c) ----

STOCK = {0x30: 0x03, 0x34: 0x51, 0x36: 0xbf, 0x50: 0x10, 0x3c: 0x1b, 0x3d: 0x28}  # as read on a Q2, 2026-11-17
RAW = 0x1b28  # 6952: 38.1 C
PMIC_START, PMIC_ON, PMIC_OFF = range(3)


class FakeBus:
    """The AXP2101 as q2guard_pmic.c sees it: registers, every transaction recorded in order, and failures to
    order. 0x34's bits 5:0 and 0x3C/0x3D are read-only, as the datasheet has them."""
    def __init__(self, regs=None):
        self.regs, self.ops, self.fail = dict(STOCK if regs is None else regs), [], set()
        self.fail_writes = 0  # this many writes to 0x34 fail, then they succeed
        self.on_sleep = None
        self.script = []  # values 0x3C and 0x3D give, in turn, before the registers
        self.cb = (RD(self.rd), WR(self.wr), SLEEP(self.sleep))
        self.bus = Bus(*self.cb, None)

    def rd(self, _, reg):
        self.ops.append(('r', reg))
        if ('r', reg) in self.fail:
            return -1
        return self.script.pop(0) if self.script and reg in (0x3c, 0x3d) else self.regs.get(reg, 0)

    def wr(self, _, reg, v):
        self.ops.append(('w', reg, v))
        if ('w', reg) in self.fail:
            return -1
        if reg == 0x34 and self.fail_writes:
            self.fail_writes -= 1
            return -1
        self.regs[reg] = (v & 0xc0) | (self.regs[reg] & 0x3f) if reg == 0x34 else v
        return 0

    def sleep(self, _, ms):
        self.ops.append(('s', ms))
        if self.on_sleep:
            self.on_sleep(self)

    def writes(self):
        return [op[1:] for op in self.ops if op[0] == 'w']


def sample(L, bus, p):
    raw = C.c_int()
    t = L.pmic_sample(C.byref(bus.bus), C.byref(p), C.byref(raw))
    return t, raw.value


def started(measured=2):
    """A reader past its first window (measured), so a window is a single wait."""
    return Pmic(state=PMIC_START, measured=measured)


def test_the_conversion_is_22_c_at_7274_and_minus_20_counts_a_degree(L):
    assert [L.pmic_deci(7274), L.pmic_deci(7254), L.pmic_deci(RAW)] == [220, 230, 381]


def test_the_first_sample_sets_only_bit_4_then_reads_in_a_window_and_restores(L):
    bus, p = FakeBus(), started()
    assert sample(L, bus, p) == (381, RAW) and p.state == PMIC_ON
    assert bus.ops == [('r', 0x30), ('r', 0x34), ('r', 0x36), ('r', 0x50),   # the checks, all before any write
                       ('w', 0x30, 0x13), ('r', 0x30),                       # bit 4, read back
                       ('w', 0x34, 0x11), ('s', 1000),                       # mode 00 (bits 5:0 are read-only)
                       ('r', 0x3c), ('r', 0x3d), ('r', 0x3c),                # high, low, high again
                       ('w', 0x34, 0x51)]                                    # mode 01 back
    assert bus.regs[0x34] == 0x51 and bus.regs[0x30] == 0x13
    assert p.note.decode() == 'SENSOR pmic die temperature channel on (0x30 0x03 to 0x13)'


def test_a_later_sample_writes_only_the_window(L):
    bus, p = FakeBus(), started()
    sample(L, bus, p)
    bus.ops.clear()
    assert sample(L, bus, p) == (381, RAW)
    assert bus.writes() == [(0x34, 0x11), (0x34, 0x51)]


def test_a_mode_already_00_is_left_00(L):
    bus, p = FakeBus({**STOCK, 0x34: 0x11}), started()
    sample(L, bus, p)
    assert bus.writes()[-2:] == [(0x34, 0x11), (0x34, 0x11)]


@pytest.mark.parametrize('reg, v', [(0x30, 0x07), (0x30, 0x00), (0x34, 0x91), (0x34, 0xd1), (0x36, 0xff),
                                    (0x50, 0x00), (0x50, 0x14)])
def test_registers_not_as_stock_leaves_them_turn_it_off_with_no_write(L, reg, v):
    bus, p = FakeBus({**STOCK, reg: v}), started()
    assert sample(L, bus, p) == (NONE, -1) and p.state == PMIC_OFF
    assert bus.writes() == [] and f'0x{reg:02x} is 0x{v:02x}' in p.note.decode()
    bus.ops.clear()
    assert sample(L, bus, p)[0] == NONE and bus.ops == []  # off for good: the bus is not touched again


def test_a_register_changed_later_is_caught_before_the_next_window(L):
    bus, p = FakeBus(), started()
    sample(L, bus, p)
    bus.regs[0x50] = 0x00
    bus.ops.clear()
    assert sample(L, bus, p)[0] == NONE and p.state == PMIC_OFF and bus.writes() == []


def test_the_channel_found_off_again_is_turned_back_on(L):
    bus, p = FakeBus(), started()
    sample(L, bus, p)
    bus.regs[0x30] = 0x03
    p.note = b''
    assert sample(L, bus, p)[0] == 381 and bus.writes()[-3:] == [(0x30, 0x13), (0x34, 0x11), (0x34, 0x51)]
    assert 'channel on' in p.note.decode()


@pytest.mark.parametrize('fail', [('r', 0x3c), ('r', 0x3d), ('w', 0x34)])
def test_the_mode_is_restored_even_when_the_window_fails(L, fail):
    bus, p = FakeBus(), started()
    sample(L, bus, p)
    bus.ops.clear()
    if fail == ('w', 0x34):
        bus.fail_writes = 1  # the window's own write fails; the restore goes through
    else:
        bus.fail.add(fail)
    assert sample(L, bus, p) == (NONE, -1) and p.errors == 1 and p.state == PMIC_ON
    assert bus.ops[-1] == ('w', 0x34, 0x51) and bus.regs[0x34] == 0x51


def test_a_restore_is_tried_three_times_then_the_reader_turns_off(L):
    bus, p = FakeBus(), started()
    sample(L, bus, p)
    bus.fail_writes = 3  # the window's write and two restores fail, the third restore goes through
    bus.ops.clear()
    assert sample(L, bus, p)[0] == NONE and p.state == PMIC_ON and bus.regs[0x34] == 0x51
    assert bus.writes() == [(0x34, 0x11)] + [(0x34, 0x51)] * 3
    bus2, p2 = FakeBus(), started()
    sample(L, bus2, p2)
    bus2.ops.clear()
    bus2.fail.add(('w', 0x34))
    assert sample(L, bus2, p2)[0] == NONE and p2.state == PMIC_OFF
    assert bus2.writes() == [(0x34, 0x11)] + [(0x34, 0x51)] * 3
    assert 'not restored' in p2.note.decode()


def test_three_i2c_errors_in_a_row_turn_it_off_two_do_not(L):
    bus, p = FakeBus(), started()
    sample(L, bus, p)
    bus.fail.add(('r', 0x36))
    sample(L, bus, p), sample(L, bus, p)
    assert p.errors == 2 and p.state == PMIC_ON
    bus.fail.clear()
    assert sample(L, bus, p)[0] == 381 and p.errors == 0
    bus.fail.add(('r', 0x3d))
    for _ in range(3):
        sample(L, bus, p)
    assert p.state == PMIC_OFF and 'i2c errors in a row' in p.note.decode()
    assert bus.regs[0x34] == 0x51  # every window put the mode back


def test_a_torn_pair_is_rejected_without_counting_as_an_i2c_error(L):
    bus, p = FakeBus(), started()
    sample(L, bus, p)
    bus.script = [0x1b, 0x28, 0x1c]  # the high byte moved between the two reads
    assert sample(L, bus, p) == (NONE, -1) and p.errors == 0 and p.state == PMIC_ON


def test_no_conversion_yet_reads_none(L):
    bus, p = FakeBus({**STOCK, 0x3c: 0, 0x3d: 0}), started()
    assert sample(L, bus, p)[0] == NONE and p.state == PMIC_ON and p.errors == 0


def test_the_first_window_measures_when_the_first_conversion_lands(L):
    bus, p = FakeBus({**STOCK, 0x3c: 0, 0x3d: 0}), started(measured=0)
    polls = []

    def convert(b):
        polls.append(1)
        if len(polls) == 4:  # after the fourth 50 ms wait
            b.regs[0x3c], b.regs[0x3d] = 0x1b, 0x28
    bus.on_sleep = convert
    assert sample(L, bus, p) == (381, RAW)
    assert p.measured == 1 and p.first_ms == 200 and len(polls) == 20  # the window stays 1000 ms
    assert bus.writes()[-1] == (0x34, 0x51)


def gone_pid():
    """A pid no process has (the top of pid_max downwards), as a restarted demo's old one."""
    top = int(pathlib.Path('/proc/sys/kernel/pid_max').read_text())
    return next(p for p in range(top - 1, 1, -1) if not pathlib.Path(f'/proc/{p}').exists())
