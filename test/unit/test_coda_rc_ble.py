"""coda-rc's BLE registration (patch/coda_rc_ble.c) against a fake libdbus (coda_rc_ble_fake.c): with Bluetooth off it
logs once and waits for bluetoothd instead of asking every 30 s, registers 2 s after bluetoothd takes the bus, logs
a refusal that repeats once, and keeps /tmp/coda-rc.ble (crc_ble_advertising) only while both registrations stand
(docs/remote-protocol.md#bluetooth-le-primary)."""
import ctypes
import pathlib
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
PATCH = ROOT / 'patch'
UNKNOWN = b'org.freedesktop.DBus.Error.ServiceUnknown'
NO_METHOD = b'org.freedesktop.DBus.Error.UnknownMethod'


@pytest.fixture
def ble(tmp_path):
    out = tmp_path / 'ble.so'
    subprocess.run(['clang', '-shared', '-fPIC', '-O1', '-DCODA_HOST', '-Wall', '-Werror', '-Wno-unused-parameter',
                    '-I', str(PATCH), str(PATCH / 'coda_rc_ble.c'), str(ROOT / 'test/unit/coda_rc_ble_fake.c'),
                    '-o', str(out)], check=True)
    return Ble(ctypes.CDLL(str(out)))


class Ble:
    def __init__(self, lib):
        self.lib = lib
        self.error = ctypes.c_char_p.in_dll(lib, 'fake_error')
        self.now = ctypes.c_uint32.in_dll(lib, 'fake_now')
        self.now.value = 1_000_000
        lib.ble_retry_due.argtypes = [ctypes.c_uint32]
        lib.fake_owner.argtypes = [ctypes.c_char_p]

    def answer(self, error):
        self.keep = error  # the fake holds the pointer
        self.error.value = error

    @property
    def calls(self):
        return ctypes.c_int.in_dll(self.lib, 'fake_calls').value

    @property
    def advertising(self):
        return ctypes.c_int.in_dll(self.lib, 'fake_advertising').value

    @property
    def log(self):
        return ctypes.string_at(ctypes.addressof(ctypes.c_char.in_dll(self.lib, 'fake_log'))).decode().splitlines()

    def start(self):
        assert self.lib.ble_start() == 99
        self.lib.ble_handle()

    def run(self, seconds):
        """The daemon's loop for this long: ble_handle every pass, ble_retry_due once a second."""
        for _ in range(seconds):
            self.now.value += 1000
            self.lib.ble_retry_due(self.now.value)
            self.lib.ble_handle()


def test_bluetooth_off_is_logged_once_and_nothing_is_retried(ble):
    ble.answer(UNKNOWN)
    ble.start()
    assert ble.calls == 2  # the GATT application and the advertisement, once
    ble.run(600)  # ten minutes of Bluetooth off
    assert ble.calls == 2
    assert ble.log == ['ble: bluetoothd is not running']
    assert not ble.advertising


def test_bluetooth_on_registers_two_seconds_after_bluetoothd_arrives(ble):
    ble.answer(UNKNOWN)
    ble.start()
    ble.answer(None)
    ble.lib.fake_owner(b':1.44')
    ble.run(1)
    assert ble.calls == 2  # bluetoothd is given 2 s to put its objects up
    ble.run(2)
    assert ble.calls == 4 and ble.advertising
    assert ble.log == ['ble: bluetoothd is not running', 'ble: bluetoothd started; registering in 2 s', 'ble: registered']


def test_bluetooth_off_again_stops_advertising_and_waits(ble):
    ble.answer(None)
    ble.start()
    assert ble.advertising
    ble.answer(UNKNOWN)
    ble.lib.fake_owner(b'')
    assert not ble.advertising
    ble.run(600)
    assert ble.calls == 2
    assert ble.log == ['ble: registered', 'ble: bluetoothd left the bus; waiting for it']


def test_a_refusal_that_repeats_is_retried_every_30_s_and_logged_once(ble):
    """bluetoothd up but without GattManager1 on hci0 (a real Q2, 2026-11-17: UnknownMethod)."""
    ble.answer(NO_METHOD)
    ble.start()
    ble.run(300)
    assert ble.calls == 2 + 2 * 10
    assert ble.log == ['ble: registration refused: org.freedesktop.DBus.Error.UnknownMethod']
    assert not ble.advertising
