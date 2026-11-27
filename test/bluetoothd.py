#!/usr/bin/env python3
"""The built bluetoothd's control-channel GetElementAttributes and GetPlayStatus (docs/internals.md#track-metadata):
stock's binary with two handler-table words changed and tools/bt_meta.c appended. Under Unicorn the patched binary is
loaded at an address of its own (it is position independent), the loader's R_MIPS_REL32 applied to the table
word as ld.so applies it, and GetElementAttributes called through that word with bluetoothd's $gp and a
libbtctl record in its GOT: the song comes back; the stock binary's handler, given the same request, rejects it
(no registered player). GetPlayStatus likewise, through its own table word: the length and the position demo keeps
in BT_PLAY_FILE, moved on while playing, where stock's answers length 0xFFFFFFFF at position 0 for every song.

And the AVRCP target's SDP record (docs/internals.md#bluetooth-cover-art): the three changed words are stock's
with the new values, and the record builder's call, taken as its bal takes it through the jump laid over stock's
old handler, runs tools/bt_meta.c's bt_cover_sdp against bluetoothd's own SDP functions (malloc and memcpy answered
here); the AdditionalProtocolDescriptorLists it leaves on a record, serialised as SDP sends it, is AVRCP 1.6's:
browsing, then cover art on q2-bip's PSM. The stock binary's helper, called the same way, leaves browsing alone.

Usage: python3 test/bluetoothd.py BUILD_DIRECTORY (needs unsquashfs, readelf and Unicorn).
"""
import json, pathlib, struct, subprocess, sys
from unicorn.mips_const import *
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'tools'))
from build import stock_program  # noqa: E402
from build import (BLUETOOTHD, BLUETOOTHD_SHA, BT_COVER_PSM, BT_META, BT_META_GOT, BT_META_SLOT, BT_PLAY_FILE, BT_PLAY_SLOT,
                   BT_SDP_GOT, BT_SDP_JUMP,
                   BT_SDP_WORDS, fileoff, load_elf, segments, sha, word)
from unicorn import UC_ARCH_MIPS, UC_HOOK_CODE, UC_MODE_LITTLE_ENDIAN, UC_MODE_MIPS32, Uc

BIAS = 0x20000000  # a load address other than the link's
GP = 0x13e420      # bluetoothd's canonical $gp (readelf -A)
DATA = 0x30000000
META, STRINGS, MUTEX, PDU, STACK = DATA + 0x100, DATA + 0x200, DATA + 0x400, DATA + 0x1000, DATA + 0x8000
LOCK, UNLOCK, RET, NOTHING = DATA + 0x600, DATA + 0x700, DATA + 0x800, DATA + 0x900
OPEN, READ, CLOSE, CLOCK, PLAY_STATUS = DATA + 0xa00, DATA + 0xa20, DATA + 0xa40, DATA + 0xa60, DATA + 0xa80


def readelf(option, raw):
    import tempfile
    with tempfile.NamedTemporaryFile() as f:
        f.write(raw)
        f.flush()
        return subprocess.check_output(['readelf', option, f.name]).decode()


def run_handler(raw, handler, request, song, status=0, play=None, now_ms=0):
    """handler (a link-time address in raw) run on request (an AVRCP header and params), song in g_track_meta, status
    in g_play_status, play in BT_PLAY_FILE (None: no file) and now_ms on the monotonic clock: (its return, the pdu)."""
    u = Uc(UC_ARCH_MIPS, UC_MODE_MIPS32 | UC_MODE_LITTLE_ENDIAN)
    load_elf(u, raw, BIAS)
    u.mem_map(DATA, 0x10000)
    for stub in (LOCK, UNLOCK, RET, OPEN, READ, CLOSE, CLOCK):
        u.mem_write(stub, struct.pack('<2I', 0x03e00008, 0))
    gp = GP + BIAS
    # as ld.so leaves the GOT: local entries moved by the load address, imported functions resolved (here to a
    # stand-in that returns 0: a stock handler's calls into glib find nothing)
    word(u, NOTHING, 0x03e00008), word(u, NOTHING + 4, 0x00001025)  # jr $ra; move $v0, $zero
    got = readelf('-AW', raw)
    local = True
    for line in got.splitlines():
        if 'Global entries' in line:
            local = False
        f = line.split()
        if len(f) >= 3 and f[1].endswith('(gp)') and f[0] != 'Address':
            slot = gp + int(f[1][:-4])
            value = int(f[2], 16)
            if local and value:
                word(u, slot, value + BIAS)
            elif not local and 'FUNC' in f and 'UND' in f:
                word(u, slot, NOTHING)
    slots = {k: o for k, (_, o) in BT_META_GOT.items()}
    track_meta = DATA + 0x80
    for k, v in (('TRACK_META', track_meta), ('NOTIFY_MUTEX', MUTEX), ('LOCK', LOCK), ('UNLOCK', UNLOCK),
                 ('PLAY_STATUS', PLAY_STATUS), ('OPEN', OPEN), ('READ', READ), ('CLOSE', CLOSE), ('CLOCK_GETTIME', CLOCK)):
        word(u, gp + slots[k], v)
    word(u, PLAY_STATUS, status)

    def libc(uc, address, size, _):
        a0, a1, a2 = uc.reg_read(UC_MIPS_REG_A0), uc.reg_read(UC_MIPS_REG_A1), uc.reg_read(UC_MIPS_REG_A2)
        if address == CLOCK:
            uc.mem_write(a1, struct.pack('<2i', now_ms // 1000, now_ms % 1000 * 1000000))
            uc.reg_write(UC_MIPS_REG_V0, 0)
        elif address == OPEN:
            path = bytes(uc.mem_read(a0, 32)).split(b'\0')[0].decode()
            uc.reg_write(UC_MIPS_REG_V0, 9 if path == BT_PLAY_FILE and play is not None else 0xffffffff)
        elif address == READ:
            data = play.encode()[:a2]
            uc.mem_write(a1, data)
            uc.reg_write(UC_MIPS_REG_V0, len(data))
        elif address == CLOSE:
            uc.reg_write(UC_MIPS_REG_V0, 0)
    for stub in (OPEN, READ, CLOSE, CLOCK):
        u.hook_add(UC_HOOK_CODE, libc, begin=stub, end=stub)
    at, ptrs = STRINGS, []
    for s in song[3:]:
        u.mem_write(at, s.encode() + b'\0')
        ptrs.append(at)
        at += len(s) + 1
    u.mem_write(META, struct.pack('<3i4I', *song[:3], *ptrs))
    word(u, track_meta, META)
    u.mem_write(PDU, request)
    u.reg_write(UC_MIPS_REG_A0, DATA + 0x40)  # a session: stock's handler reads its target (null: no player)
    u.reg_write(UC_MIPS_REG_A1, PDU)
    u.reg_write(UC_MIPS_REG_A2, 3)
    u.reg_write(UC_MIPS_REG_GP, gp)
    u.reg_write(UC_MIPS_REG_SP, STACK)
    u.reg_write(UC_MIPS_REG_RA, RET)
    u.reg_write(UC_MIPS_REG_T9, handler + BIAS)
    u.emu_start(handler + BIAS, RET, count=200000)
    assert u.reg_read(UC_MIPS_REG_PC) == RET, 'the handler did not return'
    return u.reg_read(UC_MIPS_REG_V0) & 0xff, bytes(u.mem_read(PDU, 0x200))


def element_attributes(raw, handler, song):
    """handler run on a request for all attributes, song in g_track_meta: (its return, [(id, text)])."""
    params = struct.pack('>QB', 0, 0)
    ctype, pdu = run_handler(raw, handler, b'\x00\x19\x58\x20\x00' + struct.pack('>H', len(params)) + params, song)
    n = struct.unpack_from('>H', pdu, 5)[0]
    out, pos = [], 1
    for _ in range(pdu[7] if n else 0):
        i, cs, ln = struct.unpack_from('>IHH', pdu, 7 + pos)
        out.append((i, pdu[7 + pos + 8:7 + pos + 8 + ln].decode()))
        pos += 8 + ln
    return ctype, out


def play_status(raw, handler, song, status, play, now_ms):
    """handler run on GetPlayStatus: (its return, (length, position, status))."""
    ctype, pdu = run_handler(raw, handler, b'\x00\x19\x58\x30\x00\x00\x00', song, status, play, now_ms)
    return ctype, struct.unpack_from('>IIB', pdu, 7)


# AdditionalProtocolDescriptorLists (attribute 0x000d) as SDP carries it. AVRCP 1.6.2, 8 (the target's record):
# a sequence of protocol descriptor lists, browsing's {L2CAP (0x0100), PSM 0x001b}, {AVCTP (0x0017), its version}
# and cover art's {L2CAP, the PSM}, {OBEX (0x0008)}. 0x35 n: a sequence of n bytes; 0x19: a 16-bit UUID; 0x09: a
# 16-bit unsigned integer.
BROWSING = bytes.fromhex('35 10  35 06 19 0100 09 001b  35 06 19 0017 09 0104')
COVER_ART = bytes.fromhex('35 0d  35 06 19 0100 09') + struct.pack('>H', BT_COVER_PSM) + bytes.fromhex('35 03 19 0008')
HEAP = 0x31000000
SDP_UINT16, SDP_UUID16, SDP_SEQ8 = 0x09, 0x19, 0x35


def sdp_data(u, at):
    """A BlueZ sdp_data_t at at, as SDP's bytes: dtd at +0, the value at +8 (a uuid_t's 16 bits at +12, a
    sequence's first element), the next element at +32."""
    dtd = u.mem_read(at, 1)[0]
    if dtd == SDP_UINT16:
        return bytes([dtd]) + struct.pack('>H', struct.unpack('<H', u.mem_read(at + 8, 2))[0])
    if dtd == SDP_UUID16:
        return bytes([dtd]) + struct.pack('>H', struct.unpack('<H', u.mem_read(at + 12, 2))[0])
    assert dtd == SDP_SEQ8, hex(dtd)
    body, e = b'', struct.unpack('<I', u.mem_read(at + 8, 4))[0]
    while e:
        body += sdp_data(u, e)
        e = struct.unpack('<I', u.mem_read(e + 32, 4))[0]
    return bytes([dtd, len(body)]) + body


def additional_protocols(raw, entry):
    """entry (a link-time address: stock's helper, or the jump over the old handler) called as the record
    builder's bal calls it, on an empty record and an AVCTP version of 1.4: the record's attributes as
    {id: SDP bytes}, and how many allocations were not freed."""
    u = Uc(UC_ARCH_MIPS, UC_MODE_MIPS32 | UC_MODE_LITTLE_ENDIAN)
    load_elf(u, raw, BIAS)
    u.mem_map(DATA, 0x10000)
    u.mem_map(HEAP, 0x10000)
    gp = GP + BIAS
    stubs, local = {}, True
    for line in readelf('-AW', raw).splitlines():
        if 'Global entries' in line:
            local = False
        f = line.split()
        if len(f) >= 3 and f[1].endswith('(gp)') and f[0] != 'Address':
            slot, value = gp + int(f[1][:-4]), int(f[2], 16)
            if local and value:
                word(u, slot, value + BIAS)
            elif not local and 'FUNC' in f and 'UND' in f:  # an import: a stand-in of its own, answered in hook
                stub = DATA + 0x2000 + 8 * len(stubs)
                stubs[stub] = f[-1]
                word(u, stub, 0x03e00008), word(u, stub + 4, 0)
                word(u, slot, stub)
    word(u, RET, 0x03e00008), word(u, RET + 4, 0)
    state = {'at': HEAP + 0x100, 'live': set()}

    def hook(uc, address, size, _):
        name = stubs.get(address)
        if not name:
            return
        a = [uc.reg_read(r) for r in (UC_MIPS_REG_A0, UC_MIPS_REG_A1, UC_MIPS_REG_A2)]
        if name in ('malloc', 'calloc'):
            n = a[0] if name == 'malloc' else a[0] * a[1]
            v0, state['at'] = state['at'], state['at'] + (n + 15 & ~15)
            state['live'].add(v0)
        elif name == 'free':
            assert not a[0] or a[0] in state['live'], 'freed twice, or never allocated'
            state['live'].discard(a[0])
            v0 = 0
        elif name == 'memcpy':
            uc.mem_write(a[0], bytes(uc.mem_read(a[1], a[2])))
            v0 = a[0]
        elif name == 'memset':
            uc.mem_write(a[0], bytes([a[1] & 0xff]) * a[2])
            v0 = a[0]
        elif name == 'memcmp':
            x, y = bytes(uc.mem_read(a[0], a[2])), bytes(uc.mem_read(a[1], a[2]))
            v0 = (x > y) - (x < y) & 0xffffffff
        else:
            raise AssertionError(f'{name} called')
        uc.reg_write(UC_MIPS_REG_V0, v0)
    u.hook_add(UC_HOOK_CODE, hook, begin=DATA, end=DATA + 0x10000)
    record, version, psm = HEAP, HEAP + 0x40, HEAP + 0x80
    # the version as the builder made it: sdp_data_alloc(SDP_UINT16, &0x0104), run here through the GOT's own slot
    sdp_alloc = struct.unpack('<I', u.mem_read(gp + BT_SDP_GOT['DATA_ALLOC'][1], 4))[0]
    u.mem_write(psm, struct.pack('<H', 0x0104))

    def call(fn, a0, a1):
        u.reg_write(UC_MIPS_REG_A0, a0), u.reg_write(UC_MIPS_REG_A1, a1)
        u.reg_write(UC_MIPS_REG_GP, gp), u.reg_write(UC_MIPS_REG_SP, STACK), u.reg_write(UC_MIPS_REG_RA, RET)
        u.reg_write(UC_MIPS_REG_T9, fn)
        u.emu_start(fn, RET, count=400000)
        assert u.reg_read(UC_MIPS_REG_PC) == RET, 'it did not return'
        return u.reg_read(UC_MIPS_REG_V0)
    version = call(sdp_alloc, SDP_UINT16, psm)
    before = set(state['live'])
    call(entry + BIAS, record, version)
    attrs, node = {}, struct.unpack('<I', u.mem_read(record + 8, 4))[0]  # sdp_record_t's attrlist: {next, data}
    while node:
        nxt, data = struct.unpack('<2I', u.mem_read(node, 8))
        attrs[struct.unpack('<H', u.mem_read(data + 2, 2))[0]] = sdp_data(u, data)
        node = nxt
    kept = set()  # what the record itself holds: its list nodes and their data, to the leaves
    def keep(at):
        kept.add(at)
        if u.mem_read(at, 1)[0] == SDP_SEQ8:
            e = struct.unpack('<I', u.mem_read(at + 8, 4))[0]
            while e:
                keep(e)
                e = struct.unpack('<I', u.mem_read(e + 32, 4))[0]
    node = struct.unpack('<I', u.mem_read(record + 8, 4))[0]
    while node:
        kept.add(node)
        keep(struct.unpack('<I', u.mem_read(node + 4, 4))[0])
        node = struct.unpack('<I', u.mem_read(node, 4))[0]
    node = struct.unpack('<I', u.mem_read(record + 4, 4))[0]  # and its pattern: each UUID it names, copied
    while node:
        kept.add(node)
        node, uuid = struct.unpack('<2I', u.mem_read(node, 8))
        kept.add(uuid)
    return attrs, len(state['live'] - before - kept)


def check_sdp(stock, patched):
    for site, old, new in BT_SDP_WORDS:
        assert struct.unpack_from('<2I', stock + patched, fileoff(stock, site))[0] == old
        assert struct.unpack_from('<I', patched, fileoff(stock, site))[0] == new
    version, features, call = (new for _, _, new in BT_SDP_WORDS)
    assert version == 0x24020106  # addiu $v0, $zero, 0x0106: AVRCP 1.6
    assert features & 0xffff == 0x0140 and features >> 16 == 0x3442  # ori $v0, $v0: browsing (bit 6), cover art (bit 8)
    site = BT_SDP_WORDS[2][0]
    assert call >> 16 == 0x0411 and site + 4 + ((call & 0xffff) - 0x10000) * 4 == BT_SDP_JUMP  # bal to the jump
    old = BT_SDP_WORDS[2][1]
    helper = site + 4 + ((old & 0xffff) - 0x10000) * 4
    attrs, leaked = additional_protocols(stock, helper)
    assert attrs == {0x000d: bytes([0x35, len(BROWSING)]) + BROWSING} and not leaked, (attrs, leaked)
    print("stock: the record's additional protocols are browsing alone")
    attrs, leaked = additional_protocols(patched, BT_SDP_JUMP)
    assert attrs == {0x000d: bytes([0x35, len(BROWSING + COVER_ART)]) + BROWSING + COVER_ART}, attrs
    assert not leaked, leaked
    print('patched: browsing, then cover art (L2CAP PSM %#x, OBEX):' % BT_COVER_PSM, attrs[0x000d].hex())


def check(directory):
    directory = pathlib.Path(directory)
    manifest = json.loads((directory / 'manifest.json').read_text())
    def extract(name, path=BLUETOOTHD):
        return subprocess.check_output(['unsquashfs', '-cat', str(directory / name), path])
    stock, patched = extract('stock.squashfs'), extract('rootfs.squashfs', stock_program(BLUETOOTHD, manifest))
    assert sha(stock) == BLUETOOTHD_SHA
    site, pdu_word, handler = BT_META_SLOT
    # Only the table word, the program header that maps the appended code and the SDP record's sites differ.
    null_header = segments(stock)[-1][0]
    changed = [i for i in range(len(stock)) if stock[i] != patched[i]]
    play_site, play_word, play_handler = BT_PLAY_SLOT
    allowed = set(range(fileoff(stock, site + 4), fileoff(stock, site + 8))) | set(range(null_header, null_header + 32))
    allowed |= set(range(fileoff(stock, play_site + 4), fileoff(stock, play_site + 8)))
    for at, n in [(w[0], 4) for w in BT_SDP_WORDS] + [(BT_SDP_JUMP, 20)]:  # the record's words and the jump
        allowed |= set(range(fileoff(stock, at), fileoff(stock, at) + n))
    assert changed and set(changed) <= allowed, [hex(c) for c in changed if c not in allowed][:8]
    t, o, v, _, f, m, flags, _ = segments(patched)[-1][1]
    assert (t, v, flags) == (1, BT_META, 5) and patched[o:o + f] and len(patched) == o + f
    entry = struct.unpack_from('<I', patched, fileoff(patched, site + 4))[0]
    assert entry == BT_META
    relocs = readelf('-rW', patched)
    assert any(l.split()[:1] == [f'{site + 4:08x}'] and 'R_MIPS_REL32' in l for l in relocs.splitlines()), \
        'the table word has no relocation: the loader would not move it'
    song = (3, 12, 245000, 'Lovely Day', 'Bill Withers', 'Menagerie', 'Soul')
    ctype, attrs = element_attributes(patched, entry, song)
    assert ctype == 0x0c and attrs == [(1, 'Lovely Day'), (2, 'Bill Withers'), (3, 'Menagerie'), (4, '3'), (5, '12'),
                                       (6, 'Soul'), (7, '245000')], (ctype, attrs)
    print('patched: GetElementAttributes answers the song from g_track_meta:', attrs[0][1])
    ctype, _ = element_attributes(stock, handler, song)
    assert ctype == 0x0a, ctype  # REJECTED: upstream's handler finds no registered player
    print('stock: the same request is rejected (no registered player)')
    # GetPlayStatus: the position demo keeps in BT_PLAY_FILE, moved on by the time since its stamp while playing
    play_entry = struct.unpack_from('<I', patched, fileoff(patched, play_site + 4))[0]
    assert any(l.split()[:1] == [f'{play_site + 4:08x}'] and 'R_MIPS_REL32' in l for l in relocs.splitlines())
    got = play_status(patched, play_entry, song, 1, '61000 245000 5000\n', 7500)
    assert got == (0x0c, (245000, 63500, 1)), got
    print('patched: GetPlayStatus answers the length and the moving position:', got[1])
    got = play_status(stock, play_handler, song, 1, '61000 245000 5000\n', 7500)
    assert got == (0x0c, (0xffffffff, 0, 1)), got  # no registered player: no length, always at 0
    print('stock: GetPlayStatus answers length 0xFFFFFFFF at position 0, whatever plays')
    check_sdp(stock, patched)


if __name__ == '__main__':
    check(sys.argv[1])
