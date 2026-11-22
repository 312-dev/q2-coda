"""q2guard's wait (patch/q2bt.c bt_sleep, docs/internals.md#bluetooth-link-monitor) when hci0 goes away under a
stream: Bluetooth switched off while it streamed to a car, on 2026-11-20, left q2guard at 100% of a core until it was
restarted, as the stream it was sampling never ended and its next sample was always due. Built for this machine with
test/unit/q2bt_sleep_host.c, which plays the system calls and the clock."""
import ctypes as C
import subprocess

import pytest

from test_q2guard import ROOT


@pytest.fixture
def L(tmp_path):
    out = tmp_path / 'q2bt_sleep.so'
    subprocess.run(['clang', '-shared', '-fPIC', '-O1', '-DQ2GUARD_HOST', '-Wall', '-Werror', '-Wno-unused-parameter',
                    '-Wno-unused-function', '-include', ROOT / 'test/unit/glibc228_snprintf.h',
                    f'-DPEQ_ROOT="{tmp_path}"', ROOT / 'test/unit/q2bt_sleep_host.c', ROOT / 'patch/q2bt_core.c', ROOT / 'patch/q2dbus.c',
                    ROOT / 'patch/q2guard_core.c', ROOT / 'patch/q2log_core.c', ROOT / 'patch/card_store.c',
                    '-o', out], check=True)
    lib = C.CDLL(str(out))
    lib.start.argtypes = [C.c_char_p]
    lib.sleep_for.argtypes = [C.c_uint, C.POINTER(C.c_uint)]
    lib.sleep_for.restype = C.c_uint
    lib.bt_ram.restype = C.c_char_p
    lib.start(str(tmp_path).encode())
    return lib


def wait(L, ms=5000):
    calls = C.c_uint()
    took = L.sleep_for(ms, C.byref(calls))
    return took, calls.value


def stream(L):
    """Two of q2guard's ticks: the first reads the count, the second sees it move and starts the stream."""
    wait(L), wait(L)
    assert L.streaming()


def test_a_stream_is_sampled_once_a_second(L):
    stream(L)
    took, calls = wait(L)
    assert 5000 <= took <= 5010 and calls <= 6


def test_hci0_gone_under_a_stream_ends_it_and_the_wait_sleeps(L):
    """The stack stopped while it streamed: the count can no longer be read. The stream ends there (a STOP in the
    q2-bt log), and each wait after is a sleep, not a loop that asks hci0 again until its time is up."""
    stream(L)
    L.hci_gone(1)
    for _ in range(3):
        took, calls = wait(L)
        assert 5000 <= took <= 5010 and calls <= 6
        assert not L.streaming()
    assert b'STOP' in C.string_at(L.bt_ram(), L.bt_used())


def test_hci0_back_starts_a_new_stream(L):
    stream(L)
    L.hci_gone(1)
    wait(L)
    L.hci_gone(0)
    wait(L), wait(L)
    assert L.streaming()
