"""tools/bt_meta.c, the control channel's GetElementAttributes for stock's bluetoothd (docs/internals.md#track-metadata),
compiled as tools/build.py compiles it and run under Unicorn at a load address of its own, against a GOT and a
libbtctl record laid out as bluetoothd's: the attributes from g_track_meta, under its mutex."""
import struct

import pytest
from unicorn import UC_ARCH_MIPS, UC_HOOK_CODE, UC_MODE_LITTLE_ENDIAN, UC_MODE_MIPS32, Uc
from unicorn.mips_const import UC_MIPS_REG_4, UC_MIPS_REG_5, UC_MIPS_REG_6, UC_MIPS_REG_2, UC_MIPS_REG_25, UC_MIPS_REG_28, UC_MIPS_REG_29, UC_MIPS_REG_31

import build

CODE, DATA, STACK = 0x2150000, 0x3000000, 0x4000000  # a load address other than the link's: position independence
GP = DATA + 0x8000
RET = DATA + 0x100  # where the handler returns to
LOCK, UNLOCK = DATA + 0x200, DATA + 0x300  # pthread's stand-ins: jr $ra
MUTEX, META, STRINGS = DATA + 0x400, DATA + 0x500, DATA + 0x600
PDU = DATA + 0x1000


@pytest.fixture(scope='module')
def code():
    blob, sym = build.bt_meta()
    return blob, sym['bt_meta_get_element_attributes'] - build.BT_META


def run(code, request, meta=None):
    """The handler on pdu (an AVRCP header and params), meta (track, total, time, title, artist, album, genre) or none
    in g_track_meta: (its return, the pdu after, the calls to pthread as (function, mutex, $gp))."""
    blob, entry = code
    u = Uc(UC_ARCH_MIPS, UC_MODE_MIPS32 | UC_MODE_LITTLE_ENDIAN)
    u.mem_map(CODE, 0x10000)
    u.mem_map(DATA, 0x10000)
    u.mem_map(STACK - 0x10000, 0x10000)
    u.mem_write(CODE, blob)
    for stub in (LOCK, UNLOCK, RET):
        u.mem_write(stub, struct.pack('<2I', 0x03e00008, 0))  # jr $ra; nop
    got = {name: slot for name, (_, slot) in build.BT_META_GOT.items()}
    track_meta = DATA + 0x80  # libbtctl's g_track_meta, a pointer
    u.mem_write(GP + got['TRACK_META'], struct.pack('<I', track_meta))
    u.mem_write(GP + got['NOTIFY_MUTEX'], struct.pack('<I', MUTEX))
    u.mem_write(GP + got['LOCK'], struct.pack('<I', LOCK))
    u.mem_write(GP + got['UNLOCK'], struct.pack('<I', UNLOCK))
    record = 0
    if meta:
        at, ptrs = STRINGS, []
        for s in meta[3:]:
            if s is None:
                ptrs.append(0)
                continue
            u.mem_write(at, s.encode() + b'\0')
            ptrs.append(at)
            at += len(s.encode()) + 1
        u.mem_write(META, struct.pack('<3i4I', *meta[:3], *ptrs))
        record = META
    u.mem_write(track_meta, struct.pack('<I', record))
    u.mem_write(PDU, request)
    calls = []

    def hook(uc, address, size, _):
        if address in (LOCK, UNLOCK):
            calls.append(({LOCK: 'lock', UNLOCK: 'unlock'}[address], uc.reg_read(UC_MIPS_REG_4), uc.reg_read(UC_MIPS_REG_28),
                          uc.reg_read(UC_MIPS_REG_25) == address))
    u.hook_add(UC_HOOK_CODE, hook)
    u.reg_write(UC_MIPS_REG_4, 0x1234)  # the session, unused
    u.reg_write(UC_MIPS_REG_5, PDU)
    u.reg_write(UC_MIPS_REG_6, 7)
    u.reg_write(UC_MIPS_REG_28, GP)
    u.reg_write(UC_MIPS_REG_29, STACK - 0x100)
    u.reg_write(UC_MIPS_REG_31, RET)
    u.reg_write(UC_MIPS_REG_25, CODE + entry)
    u.emu_start(CODE + entry, RET, count=20000)
    return u.reg_read(UC_MIPS_REG_2) & 0xff, bytes(u.mem_read(PDU, 0x220)), calls


def request(*ids, identifier=0):
    params = struct.pack('>QB', identifier, len(ids)) + b''.join(struct.pack('>I', i) for i in ids)
    return b'\x00\x19\x58\x20\x00' + struct.pack('>H', len(params)) + params


def attrs(pdu):
    """The answer: its packet type and (id, charset, text) for each attribute."""
    n = struct.unpack_from('>H', pdu, 5)[0]
    params = pdu[7:7 + n]
    out, pos = [], 1
    for _ in range(params[0]):
        i, cs, ln = struct.unpack_from('>IHH', params, pos)
        out.append((i, cs, params[pos + 8:pos + 8 + ln].decode()))
        pos += 8 + ln
    assert pos == n
    return pdu[4], out


SONG = (3, 12, 245000, 'Lovely Day', 'Bill Withers', 'Menagerie', 'Soul')


def test_all_attributes_from_g_track_meta_under_its_mutex(code):
    ctype, pdu, calls = run(code, request(), SONG)
    assert ctype == 0x0c  # STABLE
    assert attrs(pdu) == (0, [(1, 106, 'Lovely Day'), (2, 106, 'Bill Withers'), (3, 106, 'Menagerie'), (4, 106, '3'),
                              (5, 106, '12'), (6, 106, 'Soul'), (7, 106, '245000')])
    assert calls == [('lock', MUTEX, GP, True), ('unlock', MUTEX, GP, True)]  # $t9 at the function, bluetoothd's $gp


def test_the_ones_asked_for_in_order_once_each(code):
    _, pdu, _ = run(code, request(7, 1, 1, 9, 0, 2), SONG)
    assert attrs(pdu)[1] == [(7, 106, '245000'), (1, 106, 'Lovely Day'), (2, 106, 'Bill Withers')]


def test_a_missing_text_or_negative_number_is_left_out(code):
    _, pdu, _ = run(code, request(), (-1, -1, 0, 'Title', None, None, None))
    assert attrs(pdu)[1] == [(1, 106, 'Title'), (7, 106, '0')]


def test_no_track_is_a_one_space_title_or_the_asked_ones_empty(code):
    _, pdu, _ = run(code, request())
    assert attrs(pdu)[1] == [(1, 106, ' ')]
    _, pdu, _ = run(code, request(1, 2))
    assert attrs(pdu)[1] == [(1, 106, ''), (2, 106, '')]


def test_utf8_passes_through_and_one_packet_is_cut_at_a_character(code):
    _, pdu, _ = run(code, request(), (1, 1, 1, 'é' * 300, 'Artist', 'Album', 'Genre'))  # a 600-byte title
    n = struct.unpack_from('>H', pdu, 5)[0]
    _, got = attrs(pdu)
    assert got == [(1, 106, 'é' * ((build.BT_META_ROOM - 9) // 2))] and n <= build.BT_META_ROOM


@pytest.mark.parametrize('bad', [b'\x00\x19\x58\x20\x00\x00\x08' + bytes(8),  # under 9 bytes
                                 request(1, 2)[:6] + b'\x0d' + request(1, 2)[7:-4]])  # fewer ids than its count says
def test_a_short_request_is_rejected(code, bad):
    ctype, pdu, calls = run(code, bad, SONG)
    assert ctype == 0x0a and pdu[5:8] == b'\x00\x01\x01' and not calls


def test_only_invalid_ids_is_rejected(code):
    assert run(code, request(0, 8), SONG)[0] == 0x0a
