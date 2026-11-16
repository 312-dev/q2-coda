#!/usr/bin/env python3
"""The built bluetoothd's control-channel GetElementAttributes (docs/internals.md#track-metadata): stock's
binary with one handler-table word changed and tools/bt_meta.c appended. Under Unicorn the patched binary is
loaded at an address of its own (it is position independent), the loader's R_MIPS_REL32 applied to the table
word as ld.so applies it, and GetElementAttributes called through that word with bluetoothd's $gp and a
libbtctl record in its GOT: the song comes back; the stock binary's handler, given the same request, rejects it
(no registered player).

Usage: python3 test/bluetoothd.py BUILD_DIRECTORY (needs unsquashfs, readelf and Unicorn).
"""
import pathlib, struct, subprocess, sys
from unicorn.mips_const import *
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'tools'))
from build import (BLUETOOTHD, BLUETOOTHD_SHA, BT_META, BT_META_GOT, BT_META_SLOT, fileoff, load_elf, segments, sha,
                   word)
from unicorn import UC_ARCH_MIPS, UC_MODE_LITTLE_ENDIAN, UC_MODE_MIPS32, Uc

BIAS = 0x20000000  # a load address other than the link's
GP = 0x13e420      # bluetoothd's canonical $gp (readelf -A)
DATA = 0x30000000
META, STRINGS, MUTEX, PDU, STACK = DATA + 0x100, DATA + 0x200, DATA + 0x400, DATA + 0x1000, DATA + 0x8000
LOCK, UNLOCK, RET, NOTHING = DATA + 0x600, DATA + 0x700, DATA + 0x800, DATA + 0x900


def readelf(option, raw):
    import tempfile
    with tempfile.NamedTemporaryFile() as f:
        f.write(raw)
        f.flush()
        return subprocess.check_output(['readelf', option, f.name]).decode()


def element_attributes(raw, handler, song):
    """handler (a link-time address in raw) run on a request for all attributes, song in g_track_meta."""
    u = Uc(UC_ARCH_MIPS, UC_MODE_MIPS32 | UC_MODE_LITTLE_ENDIAN)
    load_elf(u, raw, BIAS)
    u.mem_map(DATA, 0x10000)
    for stub in (LOCK, UNLOCK, RET):
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
    for k, v in (('TRACK_META', track_meta), ('NOTIFY_MUTEX', MUTEX), ('LOCK', LOCK), ('UNLOCK', UNLOCK)):
        word(u, gp + slots[k], v)
    at, ptrs = STRINGS, []
    for s in song[3:]:
        u.mem_write(at, s.encode() + b'\0')
        ptrs.append(at)
        at += len(s) + 1
    u.mem_write(META, struct.pack('<3i4I', *song[:3], *ptrs))
    word(u, track_meta, META)
    params = struct.pack('>QB', 0, 0)
    u.mem_write(PDU, b'\x00\x19\x58\x20\x00' + struct.pack('>H', len(params)) + params)
    u.reg_write(UC_MIPS_REG_A0, DATA + 0x40)  # a session: stock's handler reads its target (null: no player)
    u.reg_write(UC_MIPS_REG_A1, PDU)
    u.reg_write(UC_MIPS_REG_A2, 3)
    u.reg_write(UC_MIPS_REG_GP, gp)
    u.reg_write(UC_MIPS_REG_SP, STACK)
    u.reg_write(UC_MIPS_REG_RA, RET)
    u.reg_write(UC_MIPS_REG_T9, handler + BIAS)
    u.emu_start(handler + BIAS, RET, count=200000)
    assert u.reg_read(UC_MIPS_REG_PC) == RET, 'the handler did not return'
    pdu = bytes(u.mem_read(PDU, 0x200))
    n = struct.unpack_from('>H', pdu, 5)[0]
    out, pos = [], 1
    for _ in range(pdu[7] if n else 0):
        i, cs, ln = struct.unpack_from('>IHH', pdu, 7 + pos)
        out.append((i, pdu[7 + pos + 8:7 + pos + 8 + ln].decode()))
        pos += 8 + ln
    return u.reg_read(UC_MIPS_REG_V0) & 0xff, out


def check(directory):
    directory = pathlib.Path(directory)
    def extract(name):
        return subprocess.check_output(['unsquashfs', '-cat', str(directory / name), BLUETOOTHD])
    stock, patched = extract('stock.squashfs'), extract('rootfs.squashfs')
    assert sha(stock) == BLUETOOTHD_SHA
    site, pdu_word, handler = BT_META_SLOT
    # Only the table word and the program header that maps the appended code differ.
    null_header = segments(stock)[-1][0]
    changed = [i for i in range(len(stock)) if stock[i] != patched[i]]
    allowed = set(range(fileoff(stock, site + 4), fileoff(stock, site + 8))) | set(range(null_header, null_header + 32))
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


if __name__ == '__main__':
    check(sys.argv[1])
