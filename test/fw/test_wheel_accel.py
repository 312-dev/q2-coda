"""wheel_accel.c (docs/internals.md#wheel-acceleration): the wheel's momentum from timed ticks, as an iPod classic's.
A slow turn stays a row a tick; a sustained spin gains rows a tick; a fast spin that goes on, on a long list, jumps a
letter a tick and drops back to rows once it slows; a pause, a reversal or another list starts over."""
import ctypes as C
import re

from conftest import ROOT

WA = {k: int(v) for k, v in re.findall(r'^#define (WA_\w+) \(?(-?\d+)', (ROOT / 'patch/wheel_accel.h').read_text(), re.M)}
LETTER = WA['WA_LETTER']


class Accel(C.Structure):
    _fields_ = [('key', C.c_void_p), ('last', C.c_uint), ('run', C.c_uint), ('dir', C.c_int)]


def spin(fw, a, gaps, length=1000, start=10000, key=1, dir=1):
    """A tick at start, then one after each gap (ms): each tick's step."""
    f = fw.fn('wheel_accel', C.c_int, C.c_void_p, C.c_void_p, C.c_int, C.c_uint, C.c_int)
    out, t = [], start
    for g in [0] + list(gaps):
        t += g
        out.append(f(C.byref(a), key, dir, t, length))
    return out


def test_a_slow_turn_stays_a_row_a_tick(fw):
    """Ticks further apart than a run allows: each one is a new turn, a row."""
    assert spin(fw, Accel(), [WA['WA_RUN_MS'] + 1] * 30) == [1] * 31


def test_a_sustained_spin_gains_rows_then_reaches_letter_steps(fw):
    """25 ms apart (40 a second): a row a tick for WA_FIRST_MS, then a row more each WA_RAMP_MS, then letters."""
    steps = spin(fw, Accel(), [25] * 60)
    first = WA['WA_FIRST_MS'] // 25
    assert steps[:first] == [1] * first
    rows = steps[first:WA['WA_LETTER_MS'] // 25]
    assert rows == sorted(rows) and rows[0] == 2 and max(rows) <= WA['WA_MAX_ROWS']
    assert steps[WA['WA_LETTER_MS'] // 25:] == [LETTER] * len(steps[WA['WA_LETTER_MS'] // 25:])


def test_slowing_drops_letters_back_to_rows_and_a_pause_resets(fw):
    a = Accel()
    assert spin(fw, a, [25] * 60)[-1] == LETTER
    assert spin(fw, a, [WA['WA_FAST_GAP_MS'] + 20], start=a.last)[-1] == WA['WA_MAX_ROWS']  # slower: rows again
    assert spin(fw, a, [WA['WA_RUN_MS'] + 1], start=a.last)[-1] == 1  # a pause: a new turn


def test_a_reversal_or_another_list_starts_over_and_a_short_list_never_jumps_letters(fw):
    a = Accel()
    spin(fw, a, [25] * 60)
    assert spin(fw, a, [], start=a.last + 25, dir=-1) == [1]
    spin(fw, a, [25] * 60)
    assert spin(fw, a, [], start=a.last + 25, key=2) == [1]
    assert LETTER not in spin(fw, Accel(), [25] * 80, length=WA['WA_LETTER_ROWS'] - 1)
