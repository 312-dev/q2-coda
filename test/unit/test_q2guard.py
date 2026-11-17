"""q2guard's core (patch/q2guard_core.c, docs/internals.md#overheat-guardian): the sensor's verdict, the warning and
the protective power-off, the CPU-load fallback with no sensor, the log line, the top three, and the log's ring on
the card with RAM while it is away. Built for this machine with test/unit/q2guard_host.c."""
import ctypes as C
import datetime
import os
import pathlib
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
NONE = -100000
EV_NONE, EV_SENSOR_DOWN, EV_SENSOR_OK, EV_WARN, EV_LOAD_WARN, EV_LOAD_CANCELLED, EV_OFF = range(7)
FILE_MAX, RAM = 262144, 65536


@pytest.fixture(scope='module')
def L(tmp_path_factory):
    out = tmp_path_factory.mktemp('q2guard') / 'q2guard.so'
    subprocess.run(['clang', '-shared', '-fPIC', '-O1', '-DQ2GUARD_HOST', '-Wall', '-Werror', '-Wno-unused-parameter',
                    ROOT / 'patch/q2guard_core.c', ROOT / 'test/unit/q2guard_host.c', '-o', out], check=True)
    lib = C.CDLL(str(out))
    for name in ('guard_new', 'log_new', 'table_new', 'log_ram'):
        getattr(lib, name).restype = C.c_void_p
    lib.log_ram.argtypes = lib.log_used.argtypes = lib.guard_load.argtypes = [C.c_void_p]
    lib.feed.argtypes = [C.c_void_p, C.c_uint, C.c_int, C.c_int, C.c_int, C.c_int, C.c_int, C.POINTER(C.c_int)]
    lib.heat_add.argtypes = [C.c_void_p, C.c_char_p]
    lib.heat_flush.argtypes = [C.c_void_p, C.c_char_p, C.c_long]
    lib.heat_card_away.argtypes = [C.c_void_p, C.c_long]
    lib.guard_state.argtypes = [C.c_void_p]
    lib.guard_state.restype = C.c_char_p
    return lib


class Guard:
    """A guard fed one sample per 5 s."""
    def __init__(self, L):
        self.L, self.g, self.now, self.out = L, L.guard_new(), 0, (C.c_int * 3)()
        self.events = []

    def step(self, temp=NONE, busy=-1, cancel=0, acked=0, saved=0, dt=5000):
        self.now += dt
        ev = self.L.feed(self.g, self.now, temp, busy, cancel, acked, saved, self.out)
        self.events.append(ev)
        return ev

    def actions(self):
        return dict(zip(('kill', 'flush', 'off'), self.out))

    def state(self):
        return self.L.guard_state(self.g).decode()


def real(g, n=2, base=300):
    """A sensor that has varied: trusted."""
    for i in range(n):
        g.step(base + i)


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


@pytest.mark.parametrize('bad', [NONE, 901, -201, 2550])
def test_three_bad_readings_in_a_row_put_the_sensor_down_two_do_not(L, bad):
    g = Guard(L)
    real(g)
    assert [g.step(bad), g.step(bad), g.step(310)] == [EV_NONE] * 3
    assert [g.step(bad), g.step(bad), g.step(bad)] == [EV_NONE, EV_NONE, EV_SENSOR_DOWN]


def test_an_out_of_range_sensor_never_arms_the_power_off(L):
    g = Guard(L)
    for _ in range(20):
        assert g.step(1000) in (EV_NONE, EV_SENSOR_DOWN)
    assert not g.actions()['off'] and g.state() == 'nosensor'


def test_a_sensor_that_comes_back_and_varies_is_trusted_again(L):
    g = Guard(L)
    real(g)
    for _ in range(3):
        g.step(NONE)
    assert g.step(300) == EV_NONE and g.step(302) == EV_SENSOR_OK


# ---- the temperature ----

def test_two_readings_over_the_power_off_do_nothing_three_power_off(L):
    g = Guard(L)
    real(g)
    assert [g.step(590), g.step(590), g.step(400)] == [EV_NONE] * 3
    assert [g.step(580), g.step(585)] == [EV_NONE] * 2
    assert g.step(600) == EV_OFF and g.actions()['flush'] == 1 and g.state() == 'poweroff-hot'


def test_the_warning_shows_once_per_episode_and_rearms_below_45(L):
    g = Guard(L)
    real(g)
    assert [g.step(510), g.step(510), g.step(510)] == [EV_NONE, EV_NONE, EV_WARN]
    assert [g.step(510) for _ in range(5)] == [EV_NONE] * 5
    g.step(460), g.step(455)                                  # still warm: no re-arm
    assert [g.step(500) for _ in range(3)] == [EV_NONE] * 3
    g.step(449)                                                # cooled
    assert [g.step(500) for _ in range(3)] == [EV_NONE, EV_NONE, EV_WARN]


def test_a_power_off_is_never_undone_by_cooling(L):
    g = Guard(L)
    real(g)
    for _ in range(3):
        g.step(600)
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
    real(g)
    for _ in range(3):
        g.step(600)
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
    assert line(L) == (f'{when} up=1234 t=41.5 raw=27 lv=87 mv=4105 st=Charging cab=1 udc=configured gad=adb scr=1 '
                       'br=80 wifi=1 bt=0 mhz=- cpu=23 top=hciplayer:12.3,demo:8.0,adbd:0.3 thr=27 np=63 g=ok\n')


def test_a_line_with_nothing_read_says_so_field_by_field(L):
    assert line(L, 1) == ('1970-01-01T00:00:00Z up=0 t=- raw=- lv=- mv=- st=- cab=- udc=- gad=- scr=- br=- wifi=- '
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

def flush(L, log, d, now=1000):
    return L.heat_flush(log, str(d).encode(), now)


def test_lines_are_held_until_a_flush_writes_them_in_one_go(L, tmp_path):
    log = L.log_new()
    d = tmp_path / '.q2muse/logs'
    for i in range(3):
        L.heat_add(log, f'line {i}\n'.encode())
    assert not d.exists()
    assert flush(L, log, d) == 0 and L.log_used(log) == 0
    assert (d / 'q2-heat.0').read_text() == 'line 0\nline 1\nline 2\n'


def test_the_ring_moves_0_to_1_and_never_splits_a_line(L, tmp_path):
    log = L.log_new()
    d = tmp_path / 'logs'
    one = ('x' * 199 + '\n')
    for _ in range(FILE_MAX // 200 - 1):
        L.heat_add(log, one.encode())
        if L.log_used(log) > RAM - 400:
            assert flush(L, log, d) == 0
    flush(L, log, d)
    first = (d / 'q2-heat.0').read_text()
    assert len(first) == (FILE_MAX // 200 - 1) * 200 and not (d / 'q2-heat.1').exists()
    for _ in range(3):
        L.heat_add(log, one.encode())
    assert flush(L, log, d) == 0
    assert (d / 'q2-heat.1').read_text() == first + one       # filled to the line that fits
    assert (d / 'q2-heat.0').read_text() == one * 2
    assert os.path.getsize(d / 'q2-heat.1') <= FILE_MAX


def test_card_away_then_back_flushes_in_order_after_a_gap_marker(L, tmp_path):
    """Unmounted (USB Transfer Files, pulled, read-only): the lines wait in RAM; when the card is back they are
    written in order after a GAP line saying since when and how many."""
    log = L.log_new()
    d = tmp_path / 'logs'
    L.heat_add(log, b'before\n')
    assert flush(L, log, d) == 0
    L.heat_card_away(log, 0)
    for i in range(3):
        L.heat_add(log, f'away {i}\n'.encode())
    assert flush(L, log, d, 600) == 0
    assert (d / 'q2-heat.0').read_text() == ('before\n1970-01-01T00:10:00Z GAP card away since 1970-01-01T00:00:00Z '
                                            'held=3 dropped=0\naway 0\naway 1\naway 2\n')


def test_a_card_that_refuses_the_write_keeps_the_lines(L, tmp_path):
    log = L.log_new()
    d = tmp_path / 'logs'
    d.mkdir()
    d.chmod(0o500)
    try:
        L.heat_add(log, b'kept\n')
        assert flush(L, log, d) == -1 and L.log_used(log) == 5
    finally:
        d.chmod(0o700)
    assert flush(L, log, d, 60) == 0
    assert (d / 'q2-heat.0').read_text().splitlines()[1:] == ['kept']


def test_ram_is_bounded_the_oldest_lines_go_and_are_counted(L, tmp_path):
    log = L.log_new()
    one = 'y' * 99 + '\n'
    for _ in range(RAM // 100 + 10):
        L.heat_add(log, one.encode())
    assert L.log_used(log) <= RAM
    assert flush(L, log, tmp_path / 'logs') == 0
    head = (tmp_path / 'logs/q2-heat.0').read_text().splitlines()[0]
    assert head.endswith('dropped=10')
