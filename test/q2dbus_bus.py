#!/usr/bin/env python3
"""q2guard's A2DP transport watcher's socket side (patch/q2dbus.c, docs/internals.md#bluetooth-event-log) against a
dbus-daemon of its own: the authentication, Hello and the three match rules a Q2's bus takes, the signals gdbus sends
as bluetoothd would (a transport made, pending, active, removed) each read back as its line and a device's own
properties never sent, and a bus that goes let go and tried again a minute on. Needs dbus-daemon and gdbus; without
them it prints skip and passes. The parser alone is test/unit/test_q2dbus.py.

    python3 test/q2dbus_bus.py
"""
import ctypes as C
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'test/unit'))
from test_q2dbus import BUDS, TRANSPORT, Bus, Tr, line  # noqa: E402

CONFIG = """<!DOCTYPE busconfig PUBLIC "-//freedesktop//DTD D-Bus Bus Configuration 1.0//EN"
 "http://www.freedesktop.org/standards/dbus/1.0/busconfig.dtd">
<busconfig><type>custom</type><listen>unix:path={path}</listen><auth>EXTERNAL</auth>
<policy context="default"><allow user="*"/><allow own="*"/><allow send_destination="*"/>
<allow receive_sender="*"/><allow send_type="signal"/><allow receive_type="signal"/></policy></busconfig>"""


def library(tmp):
    out = tmp / 'q2dbus.so'
    subprocess.run(['clang', '-shared', '-fPIC', '-O1', '-DQ2GUARD_HOST', '-Wall', '-Werror', '-Wno-unused-parameter',
                    f'-DPEQ_ROOT="{tmp}"', ROOT / 'patch/q2dbus.c', '-o', out], check=True)
    lib = C.CDLL(str(out))
    lib.dbus_tr_line.argtypes = [C.POINTER(Tr), C.c_char_p, C.c_char_p, C.c_int]
    lib.dbus_tr_line.restype = C.c_char
    lib.dbus_init.argtypes = [C.POINTER(Bus)]
    lib.dbus_ok.argtypes = [C.POINTER(Bus), C.c_char_p, C.c_uint]
    lib.dbus_next.argtypes = [C.POINTER(Bus), C.POINTER(Tr), C.c_uint]
    return lib


def daemon(tmp):
    path = tmp / 'bus'
    (tmp / 'bus.conf').write_text(CONFIG.format(path=path))
    p = subprocess.Popen(['dbus-daemon', '--nofork', f'--config-file={tmp / "bus.conf"}'])
    for _ in range(250):
        if path.exists():
            return path, p
        time.sleep(0.02)
    p.terminate()
    raise SystemExit('dbus-daemon made no socket in 5 s')


def emit(path, obj, sig, *args):
    """A signal from a client of the bus (--session, which says Hello: --address would not, and the bus sends a
    connection's messages on only once it has)."""
    env = {**os.environ, 'DBUS_SESSION_BUS_ADDRESS': f'unix:path={path}'}
    subprocess.run(['gdbus', 'emit', '--session', '--object-path', obj, '--signal', sig, *args],
                   check=True, capture_output=True, env=env)


def drain(L, d, want, timeout=3.0):
    got, t, end = [], Tr(), time.time() + timeout
    while len(got) < want and time.time() < end:
        while L.dbus_next(C.byref(d), C.byref(t), 0):
            got.append(line(L, t)[1])
        time.sleep(0.02)
    return got


def hears_bluetoothd_and_only_it(L, path):
    d = Bus()
    L.dbus_init(C.byref(d))
    assert L.dbus_ok(C.byref(d), str(path).encode(), 1000) == 1 and d.fd >= 0, 'did not connect'
    time.sleep(0.2)  # its match rules in before the first signal
    emit(path, '/', 'org.freedesktop.DBus.ObjectManager.InterfacesAdded', f"objectpath '{TRANSPORT}'",
         "{'org.bluez.MediaTransport1': {'State': <'idle'>, 'Codec': <byte 2>}}")
    emit(path, TRANSPORT, 'org.freedesktop.DBus.Properties.PropertiesChanged', "'org.bluez.MediaTransport1'",
         "{'State': <'pending'>}", '@as []')
    emit(path, '/org/bluez/hci0/dev_04_00_6E_D3_96_6D', 'org.freedesktop.DBus.Properties.PropertiesChanged',
         "'org.bluez.Device1'", "{'Connected': <true>}", '@as []')  # no rule matches it
    emit(path, TRANSPORT, 'org.freedesktop.DBus.Properties.PropertiesChanged', "'org.bluez.MediaTransport1'",
         "{'State': <'active'>, 'Volume': <uint16 90>}", '@as []')
    emit(path, '/', 'org.freedesktop.DBus.ObjectManager.InterfacesRemoved', f"objectpath '{TRANSPORT}'", "['org.bluez.MediaTransport1']")
    got = drain(L, d, 4)
    want = [f'transport {BUDS} opened: A2DP stream configured /sep1/fd0',
            f'transport {BUDS} pending: the device asked to start, not yet acquired /sep1/fd0',
            f'transport {BUDS} active: AVDTP started /sep1/fd0',
            f'transport {BUDS} closed /sep1/fd0']
    assert got == want, got
    assert not drain(L, d, 1, timeout=0.3), 'more than the four'


def lets_a_bus_that_goes_go(L, path, p):
    d = Bus()
    L.dbus_init(C.byref(d))
    assert L.dbus_ok(C.byref(d), b'/nonexistent/bus', 1000) == 0
    assert L.dbus_ok(C.byref(d), str(path).encode(), 30000) == 0, 'tried again within the minute'
    assert L.dbus_ok(C.byref(d), str(path).encode(), 61000) == 1, 'not tried again after the minute'
    p.terminate()
    p.wait(5)
    t, end = Tr(), time.time() + 3
    while d.fd >= 0 and time.time() < end:
        L.dbus_next(C.byref(d), C.byref(t), 70000)
        time.sleep(0.02)
    assert d.fd == -1 and d.failed_at == 70000, (d.fd, d.failed_at)


def main():
    if not shutil.which('dbus-daemon') or not shutil.which('gdbus'):
        print('skip: no dbus-daemon or gdbus on this machine')
        return 0
    with tempfile.TemporaryDirectory() as t:
        tmp = pathlib.Path(t)
        L = library(tmp)
        path, p = daemon(tmp)
        try:
            hears_bluetoothd_and_only_it(L, path)
            print('ok: the four transport signals heard, a device signal not')
            lets_a_bus_that_goes_go(L, path, p)
            print('ok: a bus that went let go, tried again a minute on')
        finally:
            if p.poll() is None:
                p.terminate()
                p.wait(5)
    return 0


if __name__ == '__main__':
    sys.exit(main())
