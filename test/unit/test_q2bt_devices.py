"""q2guard's device lines (patch/q2bt_core.c bt_mgmt_*, docs/internals.md#bluetooth-devices): the kernel's Bluetooth
management events, as a socket on the control channel reads them, turned into unified-log lines that name the device.
The event bytes are built field by field as Linux 5.10's net/bluetooth/mgmt.c sends them (mgmt_device_connected,
mgmt_device_disconnected, mgmt_connect_failed, mgmt_auth_failed, device_unpaired, mgmt_new_link_key), struct mgmt_hdr
first. Built for this machine with test/unit/q2guard_host.c, snprintf as the Q2's glibc 2.28 runs it."""
import ctypes as C
import struct
import subprocess

import pytest

from test_q2guard import ROOT

NEW_LINK_KEY, CONNECTED, DISCONNECTED, CONNECT_FAILED, AUTH_FAILED, UNPAIRED = 0x09, 0x0B, 0x0C, 0x0D, 0x11, 0x16
DEVICE_FOUND, DISCOVERING = 0x12, 0x13
BUDS = bytes([0x6d, 0x96, 0xd3, 0x6e, 0x00, 0x04])  # 04:00:6E:D3:96:6D as bdaddr_t holds it, last byte first
CAR = bytes([0x01, 0x02, 0x03, 0x04, 0x05, 0x06])   # 06:05:04:03:02:01
BREDR, LE_PUBLIC = 0, 1


class Ev(C.Structure):
    _fields_ = [('op', C.c_int), ('addr', C.c_uint8 * 6), ('type', C.c_int), ('code', C.c_int),
                ('name', C.c_char * 64), ('rssi', C.c_int), ('out', C.c_int)]


class Found(C.Structure):
    _fields_ = [('addr', (C.c_uint8 * 6) * 32), ('n', C.c_int), ('capped', C.c_int)]


class Names(C.Structure):
    _fields_ = [('addr', (C.c_uint8 * 6) * 8), ('name', (C.c_char * 64) * 8), ('next', C.c_int)]


@pytest.fixture(scope='module')
def L(tmp_path_factory):
    out = tmp_path_factory.mktemp('q2bt_devices') / 'q2bt.so'
    # the Q2's glibc 2.28 snprintf, which empties its buffer first (test/unit/glibc228_snprintf.h)
    subprocess.run(['clang', '-shared', '-fPIC', '-O1', '-DQ2GUARD_HOST', '-Wall', '-Werror', '-Wno-unused-parameter',
                    '-include', ROOT / 'test/unit/glibc228_snprintf.h',
                    f'-DPEQ_ROOT="{out.parent}"', ROOT / 'patch/q2bt_core.c', ROOT / 'patch/q2guard_core.c',
                    ROOT / 'patch/card_store.c', ROOT / 'test/unit/q2guard_host.c', '-o', out], check=True)
    lib = C.CDLL(str(out))
    lib.bt_mgmt_parse.argtypes = [C.c_char_p, C.c_int, C.c_void_p]
    lib.bt_mgmt_line.argtypes = [C.c_void_p, C.c_char_p, C.c_char_p, C.c_int]
    lib.bt_mgmt_line.restype = C.c_char
    lib.bt_info_name.argtypes = [C.c_char_p, C.c_char_p, C.c_int]
    lib.bt_addr_text.argtypes = [C.c_char_p, C.c_char_p]
    lib.bt_names_get.argtypes = [C.c_void_p, C.c_char_p]
    lib.bt_names_get.restype = C.c_char_p
    lib.bt_names_put.argtypes = [C.c_void_p, C.c_char_p, C.c_char_p]
    lib.bt_found_reset.argtypes = [C.c_void_p]
    lib.bt_found_first.argtypes = [C.c_void_p, C.c_char_p]
    lib.bt_acl_out.argtypes = [C.c_char_p, C.c_int, C.c_char_p]
    return lib


def event(op, params, index=0):
    """struct mgmt_hdr (opcode, index, len, little-endian) and the parameters."""
    return struct.pack('<HHH', op, index, len(params)) + params


def addr(bdaddr, kind=BREDR):
    return bdaddr + bytes([kind])  # struct mgmt_addr_info


def eir(*fields):
    return b''.join(bytes([len(data) + 1, kind]) + data for kind, data in fields)


def connected(bdaddr, eir_data=b'', kind=BREDR):
    return event(CONNECTED, addr(bdaddr, kind) + struct.pack('<IH', 0, len(eir_data)) + eir_data)


def line(L, ev, name=None, out=None):
    """The event's line and level as q2bt.c's mgmt_drain writes them (out: who began a connection, as the drain
    fills it from the connection list); (None, None) when it is not one."""
    e = Ev()
    if not L.bt_mgmt_parse(ev, len(ev), C.byref(e)):
        return None, None
    if out is not None:
        e.out = out
    out = C.create_string_buffer(192)
    level = L.bt_mgmt_line(C.byref(e), (name or e.name or b'') or None, out, 192)
    return out.value.decode(), level.decode()


# ---- the user's reports of 2026-11-19 ----

def test_a_connect_names_the_device_from_its_eir(L):
    """The Pixel Buds at 14:21:33Z: connected, with the address and the name the kernel had read (EIR's complete
    name, then the class of device, as mgmt_device_connected appends them)."""
    ev = connected(BUDS, eir((0x09, "Grayson's Pixel Buds".encode()), (0x0d, bytes([0x04, 0x04, 0x24]))))
    assert line(L, ev) == ('connected 04:00:6E:D3:96:6D "Grayson\'s Pixel Buds"', 'I')


def test_a_connect_with_no_name_takes_the_store_s(L):
    """No EIR (a name not yet read): the caller's, from BlueZ's info file or the names seen; none at all, the address
    alone."""
    assert line(L, connected(CAR), b'BMW 12345') == ('connected 06:05:04:03:02:01 "BMW 12345"', 'I')
    assert line(L, connected(CAR)) == ('connected 06:05:04:03:02:01', 'I')


@pytest.mark.parametrize('reason, words, level', [
    (0, 'unspecified', 'W'),
    (1, 'timeout or link loss', 'W'),
    (2, 'this Q2 ended it', 'I'),
    (3, 'the device ended it', 'W'),
    (4, 'authentication failed', 'W'),
    (5, 'suspend', 'W'),
    (9, 'other', 'W'),
])
def test_a_drop_says_who_ended_it(L, reason, words, level):
    """mgmt.h's MGMT_DEV_DISCONN_*: the drop ~40 s after the Pixel Buds' refused starts, which nothing logged
    because no stream ran. Only the Q2's own disconnect (2) is not a warning."""
    ev = event(DISCONNECTED, addr(BUDS) + bytes([reason]))
    assert line(L, ev, b"Grayson's Pixel Buds") == (
        f'disconnected 04:00:6E:D3:96:6D "Grayson\'s Pixel Buds" reason={reason} {words}', level)


def test_a_failed_connect_and_authentication_warn(L):
    """Connects that took minutes: each failed attempt, with the kernel's mgmt status (mgmt_status() folds the HCI
    code into it: Page Timeout 0x04 becomes 0x04 CONNECT_FAILED, Connection Timeout 0x08 becomes 0x08 TIMEOUT)."""
    assert line(L, event(CONNECT_FAILED, addr(CAR) + b'\x04'), b'BMW 12345') == (
        'connect failed 06:05:04:03:02:01 "BMW 12345" status=0x04 no answer (page timeout)', 'W')
    assert line(L, event(CONNECT_FAILED, addr(CAR) + b'\x08')) == (
        'connect failed 06:05:04:03:02:01 status=0x08 timed out', 'W')
    assert line(L, event(AUTH_FAILED, addr(CAR) + b'\x05'), b'BMW 12345') == (
        'auth failed 06:05:04:03:02:01 "BMW 12345" status=0x05 authentication failed', 'W')
    assert line(L, event(CONNECT_FAILED, addr(CAR) + b'\x7f')) == (
        'connect failed 06:05:04:03:02:01 status=0x7f other', 'W')


def test_a_forget_and_a_pairing_are_noted(L):
    """The Forget that took two taps: Device Unpaired is when it completed. A pairing is New Link Key: the key is
    never written."""
    assert line(L, event(UNPAIRED, addr(CAR)), b'BMW 12345') == ('forgotten 06:05:04:03:02:01 "BMW 12345"', 'I')
    key = bytes(range(0xa0, 0xb0))
    ev = event(NEW_LINK_KEY, b'\x01' + addr(BUDS) + b'\x04' + key + b'\x00')  # store_hint, mgmt_link_key_info
    text, level = line(L, ev, b'Pixel Buds')
    assert (text, level) == ('paired 04:00:6E:D3:96:6D "Pixel Buds"', 'I')
    assert 'A0' not in text and 'a0' not in text


def test_an_le_address_is_marked(L):
    assert line(L, connected(BUDS, kind=LE_PUBLIC), b'Pixel Buds') == (
        'connected 04:00:6E:D3:96:6D "Pixel Buds" LE', 'I')


# ---- what is not one ----

def test_other_events_and_cut_reads_are_not_lines(L):
    """Discovery's Device Found and the rest go by. A read holds one event whole (a datagram), but one cut short (a
    buffer smaller than the event, or a header whose len overstates) or under its struct's size is no line."""
    assert line(L, event(0x0006, b'\x01')) == (None, None)  # New Settings
    ev = event(DISCONNECTED, addr(BUDS) + b'\x01')
    assert line(L, ev[:-1]) == (None, None)       # the reason cut off
    assert line(L, ev[:5]) == (None, None)        # the header cut off
    assert line(L, event(DISCONNECTED, addr(BUDS))) == (None, None)  # len under the struct
    whole = connected(BUDS, eir((0x09, b'Pixel Buds')))
    assert line(L, whole[:-3]) == (None, None)


def test_a_bad_eir_never_reads_past_the_event(L):
    """An EIR field whose length runs past eir_len is not read; eir_len past the event is held to it."""
    params = addr(BUDS) + struct.pack('<IH', 0, 40) + bytes([30, 0x09]) + b'Pixel'
    assert line(L, event(CONNECTED, params)) == ('connected 04:00:6E:D3:96:6D', 'I')
    long_name = b'N' * 100
    text, _ = line(L, connected(BUDS, eir((0x09, long_name))))
    assert text == 'connected 04:00:6E:D3:96:6D "' + 'N' * 63 + '"'  # cut to the struct's 64
    shortened = eir((0x08, b'Pixel'), (0x09, b'Pixel Buds'))
    assert line(L, connected(BUDS, shortened))[0] == 'connected 04:00:6E:D3:96:6D "Pixel Buds"'
    assert line(L, connected(BUDS, eir((0x08, b'Pixel'))))[0] == 'connected 04:00:6E:D3:96:6D "Pixel"'


# ---- the name's other sources ----

def test_the_info_file_s_name(L):
    info = (b'[General]\nName=Grayson\'s Pixel Buds\nAlias=Buds\nClass=0x240404\nTrusted=true\n\n'
            b'[LinkKey]\nKey=00112233445566778899AABBCCDDEEFF\nType=4\nPINLength=0\n')
    out = C.create_string_buffer(64)
    assert L.bt_info_name(info, out, 64) == 1 and out.value == b"Grayson's Pixel Buds"
    assert L.bt_info_name(info.replace(b'\n', b'\r\n'), out, 64) == 1 and out.value == b"Grayson's Pixel Buds"
    assert L.bt_info_name(b'[General]\nAlias=Buds\nShortName=x\n', out, 64) == 0 and out.value == b''
    assert L.bt_info_name(b'Name=' + b'x' * 100, out, 64) == 1 and len(out.value) == 63


def test_names_seen_outlive_the_store(L):
    """A Forget's info file is gone by the time Device Unpaired comes: the name seen at its connect is used. Eight
    are kept, the oldest replaced; a device seen again keeps its place."""
    c = Names()
    assert L.bt_names_get(C.byref(c), BUDS) is None
    L.bt_names_put(C.byref(c), BUDS, b'Pixel Buds')
    L.bt_names_put(C.byref(c), BUDS, b'Pixel Buds Pro')
    assert L.bt_names_get(C.byref(c), BUDS) == b'Pixel Buds Pro' and c.next == 1
    L.bt_names_put(C.byref(c), CAR, None)  # no name: nothing kept
    assert L.bt_names_get(C.byref(c), CAR) is None
    for i in range(8):
        L.bt_names_put(C.byref(c), bytes([i, 0, 0, 0, 0, 0x99]), f'dev {i}'.encode())
    assert L.bt_names_get(C.byref(c), BUDS) is None  # the oldest went
    assert L.bt_names_get(C.byref(c), bytes([7, 0, 0, 0, 0, 0x99])) == b'dev 7'


def test_a_cached_name_survives_its_own_lookup(L):
    """2026-11-19, V1JAi: `connect failed 60:E6:F0:A1:1F:09 ...` at 20:37:35Z had no name although the same address
    was "BMW 83828" at 20:35:46Z. q2bt.c's mgmt_drain puts back the name it got, and the second event's name came
    from the names seen, so it was put back from its own slot: glibc's snprintf empties its buffer before it reads
    "%s", which blanked the slot and the line's name with it. Each event of a device after its first is a lookup
    like this one."""
    get = L['bt_names_get']  # a handle of its own that keeps the pointer (c_char_p's restype copies the bytes)
    get.argtypes, get.restype = [C.c_void_p, C.c_char_p], C.c_void_p
    c = Names()
    L.bt_names_put(C.byref(c), CAR, b'BMW 83828')
    for _ in range(3):  # the drain's order: get, put back, then the line from what get returned
        got = C.cast(get(C.byref(c), CAR), C.c_char_p)
        L.bt_names_put(C.byref(c), CAR, got)
        assert got.value == b'BMW 83828'
        assert L.bt_names_get(C.byref(c), CAR) == b'BMW 83828'


def test_the_address_as_written(L):
    out = C.create_string_buffer(18)
    L.bt_addr_text(BUDS, out)
    assert out.value == b'04:00:6E:D3:96:6D'


# ---- who began a connection, and discovery (the pairing page's search) ----

class ConnInfo(C.Structure):
    _fields_ = [('handle', C.c_uint16), ('bdaddr', C.c_uint8 * 6), ('type', C.c_uint8), ('out', C.c_uint8),
                ('state', C.c_uint16), ('link_mode', C.c_uint32)]


def conns(*cs):
    return bytes((C.c_uint16 * 2)(0, len(cs))) + b''.join(bytes(c) for c in cs)


def test_a_connect_says_who_began_it(L):
    """mgmt's Device Connected has no such flag in 5.10 (its flags are always 0): the connection list's out byte,
    hci_conn->out, is 1 for a Create Connection the Q2 sent and 0 for a link the device asked for."""
    buds = ConnInfo(handle=1, type=1, out=0, state=1)
    buds.bdaddr[:] = list(BUDS)
    car = ConnInfo(handle=2, type=1, out=1, state=1)
    car.bdaddr[:] = list(CAR)
    le = ConnInfo(handle=0x40, type=0x80, out=1, state=1)
    le.bdaddr[:] = list(BUDS)
    assert L.bt_acl_out(conns(le, buds, car), 4, BUDS) == 0
    assert L.bt_acl_out(conns(le, buds, car), 4, CAR) == 1
    assert L.bt_acl_out(conns(le), 4, BUDS) == -1
    ev = connected(BUDS)
    assert line(L, ev, b'Pixel Buds', out=0) == ('connected 04:00:6E:D3:96:6D "Pixel Buds" by=device', 'I')
    assert line(L, ev, b'Pixel Buds', out=1) == ('connected 04:00:6E:D3:96:6D "Pixel Buds" by=q2', 'I')
    assert line(L, ev, b'Pixel Buds') == ('connected 04:00:6E:D3:96:6D "Pixel Buds"', 'I')  # not listed


def test_discovery_start_and_stop(L):
    """mgmt_discovering: type (hdev->discovery.type, a mask of address types) and 1 started or 0 stopped."""
    assert line(L, event(DISCOVERING, bytes([7, 1]))) == ('discovery started BR/EDR+LE', 'I')
    assert line(L, event(DISCOVERING, bytes([7, 0]))) == ('discovery stopped BR/EDR+LE', 'I')
    assert line(L, event(DISCOVERING, bytes([1, 1]))) == ('discovery started BR/EDR', 'I')
    assert line(L, event(DISCOVERING, bytes([6, 1]))) == ('discovery started LE', 'I')
    assert line(L, event(DISCOVERING, bytes([7]))) == (None, None)


def found(bdaddr, rssi, eir_data=b'', kind=BREDR):
    return event(DEVICE_FOUND, addr(bdaddr, kind) + struct.pack('<bIH', rssi, 0, len(eir_data)) + eir_data)


def test_a_found_device_with_its_name_and_signal(L):
    ev = found(BUDS, -52, eir((0x01, b'\x06'), (0x09, "Grayson's Pixel Buds".encode())))
    assert line(L, ev) == ('found 04:00:6E:D3:96:6D "Grayson\'s Pixel Buds" rssi=-52 BR/EDR', 'I')
    assert line(L, found(CAR, -90, kind=LE_PUBLIC)) == ('found 06:05:04:03:02:01 rssi=-90 LE', 'I')
    assert line(L, found(CAR, -90)[:-1]) == (None, None)


def test_only_each_device_s_first_answer_in_a_discovery(L):
    """Device Found comes for every inquiry result, several a second for one device: the first per address per
    discovery is logged, 32 addresses at most, and the first past that says the rest are not."""
    f = Found()
    L.bt_found_reset(C.byref(f))
    assert L.bt_found_first(C.byref(f), BUDS) == 1
    assert L.bt_found_first(C.byref(f), BUDS) == 0
    assert L.bt_found_first(C.byref(f), CAR) == 1
    L.bt_found_reset(C.byref(f))  # the next discovery
    assert L.bt_found_first(C.byref(f), BUDS) == 1
    for i in range(31):
        assert L.bt_found_first(C.byref(f), bytes([i, 1, 2, 3, 4, 5])) == 1
    assert L.bt_found_first(C.byref(f), CAR) == -1  # the 33rd: said once
    assert L.bt_found_first(C.byref(f), bytes([99, 1, 2, 3, 4, 5])) == 0
    assert L.bt_found_first(C.byref(f), BUDS) == 0
