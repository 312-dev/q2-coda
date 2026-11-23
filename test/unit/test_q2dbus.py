"""q2guard's A2DP transport watcher (patch/q2dbus.c, docs/internals.md#bluetooth-event-log): BlueZ's MediaTransport1
signals parsed from the bytes a little-endian bus sends (built here by a marshaller of this file's own), each made,
started, suspended and closed transport's line, what is not one passed over. The socket side runs against a real
dbus-daemon in test/q2dbus_bus.py, as test/unit may start no program but the compilers."""
import ctypes as C
import struct
import subprocess

import pytest

from test_q2guard import ROOT

BUDS = '04:00:6E:D3:96:6D'
TRANSPORT = '/org/bluez/hci0/dev_04_00_6E_D3_96_6D/sep1/fd0'
BUF = 8192


class Tr(C.Structure):
    _fields_ = [('kind', C.c_int), ('addr', C.c_char * 18), ('bdaddr', C.c_ubyte * 6), ('tail', C.c_char * 32),
                ('state', C.c_char * 16)]


class Bus(C.Structure):
    _fields_ = [('fd', C.c_int), ('tried', C.c_int), ('warned', C.c_int), ('failed_at', C.c_uint), ('len', C.c_int),
                ('skip', C.c_int), ('buf', C.c_ubyte * BUF)]


NONE, STATE, ADDED, REMOVED = range(4)


@pytest.fixture(scope='module')
def L(tmp_path_factory):
    out = tmp_path_factory.mktemp('q2dbus') / 'q2dbus.so'
    subprocess.run(['clang', '-shared', '-fPIC', '-O1', '-DQ2GUARD_HOST', '-Wall', '-Werror', '-Wno-unused-parameter',
                    f'-DPEQ_ROOT="{out.parent}"', ROOT / 'patch/q2dbus.c', '-o', out], check=True)
    lib = C.CDLL(str(out))
    lib.dbus_transport.argtypes = [C.c_char_p, C.c_int, C.POINTER(Tr)]
    lib.dbus_size.argtypes = [C.c_char_p, C.c_int]
    lib.dbus_tr_line.argtypes = [C.POINTER(Tr), C.c_char_p, C.c_char_p, C.c_int]
    lib.dbus_tr_line.restype = C.c_char
    lib.dbus_call.argtypes = [C.c_char_p, C.c_int, C.c_uint, C.c_char_p, C.c_char_p]
    lib.dbus_auth.argtypes = [C.c_char_p, C.c_int, C.c_uint]
    lib.dbus_init.argtypes = [C.POINTER(Bus)]
    lib.dbus_ok.argtypes = [C.POINTER(Bus), C.c_char_p, C.c_uint]
    lib.dbus_next.argtypes = [C.POINTER(Bus), C.POINTER(Tr), C.c_uint]
    return lib


# ---- a marshaller, little-endian, for the types these signals use ----

class W:
    def __init__(self):
        self.b = bytearray()

    def pad(self, a):
        self.b += bytes(-len(self.b) % a)

    def value(self, sig, v):
        """One value of the complete type sig."""
        c = sig[0]
        if c == 's' or c == 'o':
            self.pad(4)
            e = v.encode()
            self.b += struct.pack('<I', len(e)) + e + b'\0'
        elif c == 'g':
            self.b += bytes([len(v)]) + v.encode() + b'\0'
        elif c == 'y':
            self.b += bytes([v])
        elif c == 'q':
            self.pad(2)
            self.b += struct.pack('<H', v)
        elif c in 'ub':
            self.pad(4)
            self.b += struct.pack('<I', v)
        elif c == 'v':
            vs, val = v
            self.value('g', vs)
            self.value(vs, val)
        elif c == 'a':
            self.pad(4)
            at = len(self.b)
            self.b += bytes(4)
            inner = sig[1:]
            self.pad(8 if inner[0] in '({' else 4 if inner[0] in 'sobua' else 1)
            start = len(self.b)
            if inner.startswith('{'):
                k, vt = inner[1], inner[2:-1]
                for key, val in v.items():
                    self.pad(8)
                    self.value(k, key)
                    self.value(vt, val)
            else:
                for item in v:
                    self.value(inner, item)
            struct.pack_into('<I', self.b, at, len(self.b) - start)
        else:
            raise ValueError(sig)


def signal(path, iface, member, sig, *args, sender=':1.7'):
    body = W()
    k, types = 0, []
    while k < len(sig):  # split sig into complete types
        e = k
        depth = 0
        while True:
            ch = sig[e]
            depth += ch in '({'
            depth -= ch in ')}'
            e += 1
            if ch != 'a' and depth == 0:
                break
        types.append(sig[k:e])
        k = e
    for t, a in zip(types, args):
        body.value(t, a)
    f = W()
    f.b += b'l' + bytes([4, 0, 1]) + struct.pack('<III', len(body.b), 9, 0)
    for code, t, v in ((1, 'o', path), (2, 's', iface), (3, 's', member), (8, 'g', sig), (7, 's', sender)):
        f.pad(8)
        f.b += bytes([code])
        f.value('g', t)
        f.value(t, v)
    struct.pack_into('<I', f.b, 12, len(f.b) - 16)
    f.pad(8)
    return bytes(f.b + body.b)


def props(path, iface, changed, invalidated=()):
    return signal(path, 'org.freedesktop.DBus.Properties', 'PropertiesChanged', 'sa{sv}as', iface, changed,
                  list(invalidated))


def parse(L, m):
    t = Tr()
    return L.dbus_transport(m, len(m), C.byref(t)), t


def line(L, t, name=None):
    out = C.create_string_buffer(192)
    level = L.dbus_tr_line(C.byref(t), name.encode() if name else None, out, 192)
    return level.decode(), out.value.decode()


@pytest.mark.parametrize('state, words', [
    ('active', 'active: AVDTP started'), ('idle', 'idle: AVDTP suspended or not started'),
    ('pending', 'pending: the device asked to start, not yet acquired'),
])
def test_a_transport_s_state_is_a_line_with_the_device(L, state, words):
    m = props(TRANSPORT, 'org.bluez.MediaTransport1', {'Volume': ('q', 64), 'State': ('s', state),
                                                       'Delay': ('q', 150)})
    assert L.dbus_size(m, len(m)) == len(m)
    got, t = parse(L, m)
    assert got == 1 and t.kind == STATE and t.addr.decode() == BUDS and t.state.decode() == state
    assert bytes(t.bdaddr) == bytes(reversed(bytes.fromhex(BUDS.replace(':', ''))))
    assert line(L, t, "Grayson's Pixel Buds") == ('I', f'transport {BUDS} "Grayson\'s Pixel Buds" {words} /sep1/fd0')
    assert line(L, t) == ('I', f'transport {BUDS} {words} /sep1/fd0')


def test_a_transport_made_and_removed(L):
    m = signal('/', 'org.freedesktop.DBus.ObjectManager', 'InterfacesAdded', 'oa{sa{sv}}', TRANSPORT,
               {'org.freedesktop.DBus.Properties': {}, 'org.bluez.MediaTransport1': {
                   'Device': ('o', '/org/bluez/hci0/dev_04_00_6E_D3_96_6D'), 'Codec': ('y', 2),
                   'State': ('s', 'idle'), 'Volume': ('q', 127)}})
    got, t = parse(L, m)
    assert got == 1 and t.kind == ADDED
    assert line(L, t)[1] == f'transport {BUDS} opened: A2DP stream configured /sep1/fd0'
    m = signal('/', 'org.freedesktop.DBus.ObjectManager', 'InterfacesRemoved', 'oas', TRANSPORT,
               ['org.freedesktop.DBus.Properties', 'org.bluez.MediaTransport1'])
    got, t = parse(L, m)
    assert got == 1 and t.kind == REMOVED and line(L, t)[1] == f'transport {BUDS} closed /sep1/fd0'


@pytest.mark.parametrize('m', [
    # a device's own properties (the RSSI of a discovery): not a transport's
    props('/org/bluez/hci0/dev_04_00_6E_D3_96_6D', 'org.bluez.Device1', {'RSSI': ('q', 60)}),
    # a transport's volume alone: no State in it
    props(TRANSPORT, 'org.bluez.MediaTransport1', {'Volume': ('q', 64)}),
    # a device object added during a discovery
    signal('/', 'org.freedesktop.DBus.ObjectManager', 'InterfacesAdded', 'oa{sa{sv}}',
           '/org/bluez/hci0/dev_04_00_6E_D3_96_6D', {'org.bluez.Device1': {'Name': ('s', 'Buds')}}),
    # bluealsa's own PCMs, outside /org/bluez
    signal('/', 'org.freedesktop.DBus.ObjectManager', 'InterfacesAdded', 'oa{sa{sv}}',
           '/org/bluealsa/hci0/dev_04_00_6E_D3_96_6D/a2dpsrc/sink', {'org.bluealsa.PCM1': {}}),
])
def test_what_is_not_a_transport_event_is_passed_over(L, m):
    assert parse(L, m)[0] == 0


def test_a_message_cut_short_or_lying_about_its_lengths_parses_to_nothing(L):
    m = props(TRANSPORT, 'org.bluez.MediaTransport1', {'State': ('s', 'active')})
    for n in range(16, len(m)):
        assert parse(L, m[:n])[0] == 0, n
    bad = bytearray(m)
    struct.pack_into('<I', bad, 4, 1 << 30)  # a body longer than anything
    assert L.dbus_size(bytes(bad), len(bad)) == -1
    assert L.dbus_size(b'x' * 16, 16) == -1 and L.dbus_size(m[:15], 15) == 0
    # the State's string length pointing past the end
    k = m.index(b'active') - 4
    bad = bytearray(m)
    struct.pack_into('<I', bad, k, 200)
    assert parse(L, bytes(bad))[0] == 0


def test_the_calls_and_the_auth_line_are_what_the_bus_expects(L):
    out = C.create_string_buffer(64)
    n = L.dbus_auth(out, 64, 0)
    assert out.raw[:n] == b'\0AUTH EXTERNAL 30\r\n'
    n = L.dbus_auth(out, 64, 1000)
    assert out.raw[:n] == b'\0AUTH EXTERNAL 31303030\r\n'
    buf = C.create_string_buffer(512)
    n = L.dbus_call(buf, 512, 2, b'AddMatch', b"type='signal'")
    m = buf.raw[:n]
    assert m[:4] == b'l\x01\x00\x01' and L.dbus_size(m, n) == n and struct.unpack_from('<I', m, 8)[0] == 2
    assert m.endswith(struct.pack('<I', 13) + b"type='signal'\0") and b'AddMatch' in m and b'\x08\x01g\x00\x01s\x00' in m
    assert L.dbus_call(buf, 40, 2, b'AddMatch', b"type='signal'") == 0  # does not fit
