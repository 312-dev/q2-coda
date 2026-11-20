"""tools/bt_meta.c, the control channel's GetElementAttributes for stock's bluetoothd (docs/internals.md#track-metadata),
compiled as tools/build.py compiles it and run under Unicorn at a load address of its own, against a GOT and a
libbtctl record laid out as bluetoothd's: the attributes from g_track_meta, under its mutex, and AVRCP 1.6's cover art
attribute (docs/internals.md#bluetooth-cover-art) for a controller whose record says it fetches covers, and no other."""
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
OPEN, READ, CLOSE = DATA + 0x340, DATA + 0x360, DATA + 0x380  # libc's stand-ins, answered in hook
SESSION, TARGET = DATA + 0x2000, DATA + 0x2040  # bluetoothd's struct avrcp and its target (the controller's record)
FD = 9
# A controller's SupportedFeatures (AVRCP 1.6.2, table 8.2): categories 1 and 2 and browsing, and the same with
# its three cover art bits (7 GetImageProperties, 8 GetImage, 9 GetLinkedThumbnail)
CT_15, CT_16_COVER = 0x0043, 0x03c3


@pytest.fixture(scope='module')
def code():
    blob, sym = build.bt_meta()
    return blob, sym['bt_meta_get_element_attributes'] - build.BT_META


def run(code, request, meta=None, features=CT_15, cover=None, files=None):
    """The handler on pdu (an AVRCP header and params), meta (track, total, time, title, artist, album, genre) or none
    in g_track_meta, for a controller whose record has features (None: no record), with BT_COVER_FILE holding cover
    (None: no file): (its return, the pdu after, the calls to pthread as (function, mutex, $gp)). files takes the
    calls to libc: ('open', path, flags), ('read', fd, size), ('close', fd)."""
    blob, entry = code
    u = Uc(UC_ARCH_MIPS, UC_MODE_MIPS32 | UC_MODE_LITTLE_ENDIAN)
    u.mem_map(CODE, 0x10000)
    u.mem_map(DATA, 0x10000)
    u.mem_map(STACK - 0x10000, 0x10000)
    u.mem_write(CODE, blob)
    u.mem_write(STACK - 0x10000, b'5' * 0x10000)  # what a stack holds is not zeros: a short read must not pass as digits
    for stub in (LOCK, UNLOCK, RET, OPEN, READ, CLOSE):
        u.mem_write(stub, struct.pack('<2I', 0x03e00008, 0))  # jr $ra; nop
    got = {name: slot for name, (_, slot) in build.BT_META_GOT.items()}
    track_meta = DATA + 0x80  # libbtctl's g_track_meta, a pointer
    u.mem_write(GP + got['TRACK_META'], struct.pack('<I', track_meta))
    u.mem_write(GP + got['NOTIFY_MUTEX'], struct.pack('<I', MUTEX))
    u.mem_write(GP + got['LOCK'], struct.pack('<I', LOCK))
    u.mem_write(GP + got['UNLOCK'], struct.pack('<I', UNLOCK))
    for name, stub in (('OPEN', OPEN), ('READ', READ), ('CLOSE', CLOSE)):
        u.mem_write(GP + got[name], struct.pack('<I', stub))
    u.mem_write(SESSION + 0xc, struct.pack('<I', 0 if features is None else TARGET))
    u.mem_write(TARGET, struct.pack('<IHHi', 0, 0x0106, 0, features or 0))  # player, version, features
    files = [] if files is None else files
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
        if address in (OPEN, READ, CLOSE):
            assert uc.reg_read(UC_MIPS_REG_25) == address and uc.reg_read(UC_MIPS_REG_28) == GP
            a0, a1, a2 = (uc.reg_read(r) for r in (UC_MIPS_REG_4, UC_MIPS_REG_5, UC_MIPS_REG_6))
            if address == OPEN:
                files.append(('open', bytes(uc.mem_read(a0, 32)).split(b'\0')[0].decode(), a1))
                uc.reg_write(UC_MIPS_REG_2, 0xffffffff if cover is None else FD)
            elif address == READ:
                files.append(('read', a0, a2))
                uc.mem_write(a1, cover[:a2])
                uc.reg_write(UC_MIPS_REG_2, len(cover[:a2]))
            else:
                files.append(('close', a0))
                uc.reg_write(UC_MIPS_REG_2, 0)
    u.hook_add(UC_HOOK_CODE, hook)
    u.reg_write(UC_MIPS_REG_4, SESSION)
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


HANDLE = b'6136261\n/mnt/mmc/.coverflow/811c9dc5.jpg\n'
ALL = [(1, 106, 'Lovely Day'), (2, 106, 'Bill Withers'), (3, 106, 'Menagerie'), (4, 106, '3'), (5, 106, '12'),
       (6, 106, 'Soul'), (7, 106, '245000')]


def test_cover_handle_for_a_controller_that_fetches_covers(code):
    files = []
    ctype, pdu, calls = run(code, request(), SONG, CT_16_COVER, HANDLE, files)
    assert ctype == 0x0c and attrs(pdu) == (0, ALL + [(8, 106, '6136261')])
    assert files == [('open', build.BT_COVER_FILE, 0), ('read', FD, 7), ('close', FD)]  # read-only, seven bytes
    assert calls == [('lock', MUTEX, GP, True), ('unlock', MUTEX, GP, True)]
    _, pdu, _ = run(code, request(8, 1), SONG, CT_16_COVER, HANDLE)
    assert attrs(pdu)[1] == [(8, 106, '6136261'), (1, 106, 'Lovely Day')]
    # the attribute as AVRCP 1.6.2 6.6.1 lays it out: id 8, UTF-8 (106), seven digits
    assert pdu[7 + 1:7 + 16] == bytes.fromhex('00000008 006a 0007') + b'6136261'


@pytest.mark.parametrize('bit', [0x80, 0x100, 0x200])
def test_any_one_cover_bit_is_enough(code, bit):
    assert attrs(run(code, request(8), SONG, 0x43 | bit, HANDLE)[1])[1] == [(8, 106, '6136261')]


@pytest.mark.parametrize('features', [CT_15, 0, 0x7f, None])
def test_no_cover_attribute_for_a_controller_that_does_not_say_so(code, features):
    """Exactly what it was answered before cover art: all seven, the file never opened, and 8 no attribute."""
    files = []
    ctype, pdu, _ = run(code, request(), SONG, features, HANDLE, files)
    assert ctype == 0x0c and attrs(pdu) == (0, ALL) and files == []
    _, pdu, _ = run(code, request(8, 1), SONG, features, HANDLE, files)
    assert attrs(pdu)[1] == [(1, 106, 'Lovely Day')] and files == []
    assert run(code, request(8), SONG, features, HANDLE, files)[0] == 0x0a and files == []  # INVALID_PARAM


@pytest.mark.parametrize('cover', [None, b'', b'613626', b'61362x1\n', b'-136261\n'])
def test_a_song_with_no_cover_has_no_cover_attribute(code, cover):
    assert attrs(run(code, request(), SONG, CT_16_COVER, cover)[1]) == (0, ALL)
    ctype, pdu, _ = run(code, request(8), SONG, CT_16_COVER, cover)  # asked for alone: answered, with none
    assert ctype == 0x0c and attrs(pdu)[1] == []


def test_the_handle_follows_the_file(code):
    """A track change: demo's payload rewrites the file, and the next answer carries the new handle."""
    for cover, handle in ((HANDLE, '6136261'), (b'0000042\n/mnt/mmc/a/cover.jpg\n', '0000042')):
        assert attrs(run(code, request(8), SONG, CT_16_COVER, cover)[1])[1] == [(8, 106, handle)]


def test_cover_and_no_track(code):
    files = []
    assert attrs(run(code, request(), None, CT_16_COVER, HANDLE, files)[1])[1] == [(1, 106, ' ')]
    assert attrs(run(code, request(8, 1), None, CT_16_COVER, HANDLE)[1])[1] == [(8, 106, ''), (1, 106, '')]


def test_only_invalid_ids_is_rejected_with_cover(code):
    assert run(code, request(0, 9), SONG, CT_16_COVER, HANDLE)[0] == 0x0a


# ---- the AVRCP target's SDP record (bt_cover_sdp) ----

def sdp_lists(code, record=0x7000, version=0x7100):
    """bt_cover_sdp on record with the caller's AVCTP version data, bluetoothd's SDP functions stood in for here:
    (what sdp_set_add_access_protos was handed, as nested lists of ('uuid', n), ('u16', n) and the version; what
    was allocated and never freed)."""
    blob, _ = code
    entry = build.bt_meta()[1]['bt_cover_sdp'] - build.BT_META
    u = Uc(UC_ARCH_MIPS, UC_MODE_MIPS32 | UC_MODE_LITTLE_ENDIAN)
    u.mem_map(CODE, 0x10000)
    u.mem_map(DATA, 0x10000)
    u.mem_map(STACK - 0x10000, 0x10000)
    u.mem_write(CODE, blob)
    stubs = {}
    for i, (name, (_, slot)) in enumerate(build.BT_SDP_GOT.items()):
        stubs[DATA + 0x200 + i * 16] = name
        u.mem_write(DATA + 0x200 + i * 16, struct.pack('<2I', 0x03e00008, 0))
        u.mem_write(GP + slot, struct.pack('<I', DATA + 0x200 + i * 16))
    u.mem_write(RET, struct.pack('<2I', 0x03e00008, 0))
    objects: dict = {version: 'version'}
    live: set = set()
    set_on: list = []

    def new(value):
        at = 0x9000 + 16 * len(objects)
        objects[at] = value
        live.add(at)
        return at

    def hook(uc, address, size, _):
        name = stubs.get(address)
        if not name:
            return
        assert uc.reg_read(UC_MIPS_REG_25) == address and uc.reg_read(UC_MIPS_REG_28) == GP
        a0, a1 = uc.reg_read(UC_MIPS_REG_4), uc.reg_read(UC_MIPS_REG_5)
        v0 = 0
        if name == 'UUID16':
            objects[a0] = ('uuid', a1)  # filled in place, on the caller's stack
            v0 = a0
        elif name == 'DATA_ALLOC':
            assert a0 == 0x09  # SDP_UINT16
            v0 = new(('u16', struct.unpack('<H', uc.mem_read(a1, 2))[0]))
        elif name == 'LIST_APPEND':
            v0 = a0 or new([])
            objects[v0].append(a1)
        elif name == 'SET_ADD_ACCESS_PROTOS':
            set_on.append((a0, a1))
        else:  # LIST_FREE, with no function to free its data; FREE
            assert name == 'FREE' or a1 == 0
            assert a0 in live, 'freed twice, or never allocated'
            live.discard(a0)
        uc.reg_write(UC_MIPS_REG_2, v0)
    u.hook_add(UC_HOOK_CODE, hook, begin=DATA, end=DATA + 0x1000)
    u.reg_write(UC_MIPS_REG_4, record)
    u.reg_write(UC_MIPS_REG_5, version)
    u.reg_write(UC_MIPS_REG_28, GP)
    u.reg_write(UC_MIPS_REG_29, STACK - 0x100)
    u.reg_write(UC_MIPS_REG_31, RET)
    u.reg_write(UC_MIPS_REG_25, CODE + entry)
    u.emu_start(CODE + entry, RET, count=20000)

    def tree(at):
        v = objects[at]
        return [tree(e) for e in v] if isinstance(v, list) else v
    (rec, lists), = set_on
    assert rec == record
    return tree(lists), live


def test_sdp_record_lists_browsing_then_cover_art(code):
    lists, live = sdp_lists(code)
    assert lists == [[[('uuid', 0x0100), ('u16', 0x001b)], [('uuid', 0x0017), 'version']],  # L2CAP, AVCTP: browsing
                     [[('uuid', 0x0100), ('u16', build.BT_COVER_PSM)], [('uuid', 0x0008)]]]  # L2CAP, OBEX: cover art
    assert not live  # every list and both PSMs freed: the record keeps its own copies


def test_sdp_jump_reaches_the_code_from_gp_wherever_it_is_loaded(code):
    target = build.bt_meta()[1]['bt_cover_sdp']
    bias, jump = 0x2000000, 0x39000
    u = Uc(UC_ARCH_MIPS, UC_MODE_MIPS32 | UC_MODE_LITTLE_ENDIAN)
    u.mem_map(bias, 0x200000)
    u.mem_write(bias + jump, struct.pack('<5I', *build.bt_sdp_jump(target)))
    u.reg_write(UC_MIPS_REG_28, bias + build.BT_GP)
    u.reg_write(UC_MIPS_REG_31, 0x1234)
    u.emu_start(bias + jump, bias + target, count=10)
    assert u.reg_read(UC_MIPS_REG_25) == bias + target  # $t9 at the function, as its code expects
    assert u.reg_read(UC_MIPS_REG_31) == 0x1234  # and the caller's return address untouched


def test_the_psm_is_the_one_q2_bip_listens_on():
    import re
    psm, = re.findall(r'#define BIP_PSM (0x[0-9a-f]+)', (build.ROOT / 'patch/q2bip.h').read_text())
    assert int(psm, 16) == build.BT_COVER_PSM
    assert build.BT_COVER_PSM & 0x0101 == 0x0001  # a valid dynamic PSM: odd, its high byte even
    assert len(build.BT_COVER_FILE) == 15  # with its NUL, the four words bt_meta.c builds
