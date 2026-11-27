"""tools/build.py's helpers: synthetic images and UI files stand in for stock's."""
import ctypes
import gc
import hashlib
import importlib.util
import io
import pathlib
import re
import shutil
import struct
import subprocess
import zipfile

import pytest

import build
import ipod
import theme


def image(size=0x400000, base=0x400000, null=False):
    """A little MIPS ELF: one LOAD segment, at base from file offset 0x1000, and a final PT_NULL slot."""
    data = bytearray(0x1000 + size)
    struct.pack_into('<I', data, 28, 52)
    struct.pack_into('<HH', data, 42, 32, 2 if null else 1)
    struct.pack_into('<8I', data, 52, 1, 0x1000, base, base, size, size, 5, 0x1000)
    return data


NULL = (0,) * 8


def program(*rows, size=0x1000, phoff=52):
    """size bytes of ELF whose program headers, from phoff, are rows: (type, offset, vaddr, paddr, filesz, memsz, flags, align)."""
    data = bytearray(size)
    struct.pack_into('<I', data, 28, phoff)
    struct.pack_into('<HH', data, 42, 32, len(rows))
    for i, row in enumerate(rows):
        struct.pack_into('<8I', data, phoff + 32 * i, *row)
    return data


@pytest.mark.parametrize('number,tag', [(1, 'V001i'), (35, 'V00Zi'), (36, 'V010i'), (46655, 'VZZZi'), (46656, 'V001i'), (46657, 'V002i')])
def test_dev_tag(number, tag):
    assert build.dev_tag(number) == tag


def refuse_git(*args):
    raise subprocess.CalledProcessError(128, args)


def test_dev_build_counts_across_worktrees(tmp_path, monkeypatch):
    # Two worktrees of one repository share the counter, so the second's first --dev build has a new tag.
    (tmp_path/'repo.git').mkdir()
    calls = []
    monkeypatch.setattr(build, 'run', lambda *args: calls.append(args) or '../repo.git\n')  # relative, as a plain checkout's git answers
    tags = []
    for checkout in ('a', 'b', 'a'):
        (tmp_path/checkout).mkdir(exist_ok=True)
        (tmp_path/checkout/'.git').write_text('gitdir: elsewhere\n')  # a worktree's .git is a file
        monkeypatch.setattr(build, 'ROOT', tmp_path/checkout)
        tags.append(build.dev_tag(build.dev_build()))
    assert tags == ['V001i', 'V002i', 'V003i']
    assert calls[1] == ('git', '-C', tmp_path/'b', 'rev-parse', '--git-common-dir')
    assert (tmp_path/'repo.git/build-number').read_text() == '3\n'


def test_dev_build_after_an_empty_counter(tmp_path, monkeypatch):
    # A counter emptied by a power loss mid-write counts as 0, not a crash on int('').
    (tmp_path/'.build-number').write_text('')
    monkeypatch.setattr(build, 'ROOT', tmp_path)
    monkeypatch.setattr(build, 'run', refuse_git)  # a source archive: not a git checkout
    assert build.dev_build() == 1 and (tmp_path/'.build-number').read_text() == '1\n'


def test_dev_build_ignores_an_enclosing_repository(tmp_path, monkeypatch):
    # A source archive unpacked inside another repo has no .git of its own: git must not be asked.
    monkeypatch.setattr(build, 'ROOT', tmp_path)
    monkeypatch.setattr(build, 'run', lambda *args: pytest.fail('asked git'))
    assert build.dev_build() == 1 and not (tmp_path/'.git').exists()


STOCK_CONFIG = b'[PLAYSET]\nCOVERMODE=1\n[SYSSET]\nAUTOSCAN=1\nBTCODING=2\nTIMEZONE=22\nSTANDBYMODE=1\nWHEELMODE=1\nNAVHIDE=0'


def test_the_default_config_names_english_so_no_language_guide_opens():
    """A reset's config.ini: Enlarged Album Art, Standby and Wheel Function off, LANGUAGE=2 (English) first in SYSSET, whose absence is what
    sends config_init to stock's first-boot language guide, the zone UTC (DEFAULT_TIMEZONE) for stock's Shanghai, and
    Smart Awake on in an [IPOD] of its own, on its own line after stock's last, which has no newline."""
    out = build.default_config(STOCK_CONFIG)
    assert out == (b'[PLAYSET]\nCOVERMODE=0\n[SYSSET]\nLANGUAGE=2\nAUTOSCAN=1\nBTCODING=2\nTIMEZONE=43\nSTANDBYMODE=0\nWHEELMODE=0\n'
                   b'NAVHIDE=0\n[IPOD]\nKEEPLIT=1\n')
    assert build.default_config(STOCK_CONFIG + b'\n').endswith(b'NAVHIDE=0\n[IPOD]\nKEEPLIT=1\n')
    for bad in (STOCK_CONFIG.replace(b'AUTOSCAN', b'LANGUAGE=1\nAUTOSCAN'), STOCK_CONFIG + b'\n[SYSSET]\n',
                STOCK_CONFIG + b'\n[IPOD]\n', STOCK_CONFIG.replace(b'TIMEZONE=22', b'TIMEZONE=5')):
        with pytest.raises(ValueError, match='Unexpected default config.ini'):
            build.default_config(bad)


def test_the_default_config_picks_auto_for_bluetooth_quality():
    """Auto (BTCODING 2) is Quality's choice on a Q2 that never picked one: the reset's and the first flash's
    config.ini (docs/internals.md#bluetooth-page). Stock's says 2 too; another value from a later stock is
    refused rather than shipped, and stock reads a missing key as 0, LDAC Best, so the key must be there."""
    for bad in (STOCK_CONFIG.replace(b'BTCODING=2', b'BTCODING=0'), STOCK_CONFIG.replace(b'BTCODING=2\n', b'')):
        with pytest.raises(ValueError, match='Unexpected default config.ini'):
            build.default_config(bad)
    assert b'\nBTCODING=2\n' in build.default_config(STOCK_CONFIG)


def test_quality_s_adaptive_choice_is_called_auto():
    """Settings > Bluetooth > Quality's adaptive choice, and the Quality row's value when it is chosen."""
    assert next(n for k, _, n in build.LABELS if k == b'btcode_ldacauto') == b'Auto'


def test_jumps():
    assert build.j(0x450000) == 0x08114000 and build.jal(0x450000) == 0x0c114000
    assert build.j(0x7) == 0x08000001 and build.jal(0x7) == 0x0c000001


class Memory:
    def __init__(self):
        self.store = {}

    def mem_write(self, address, data):
        self.store[address] = data

    def mem_read(self, address, size):
        assert size == 4
        return self.store[address]


def test_need():
    assert build.need(0, 'unused') == 0 and build.need(b'', 'unused') == b''  # only None fails
    with pytest.raises(ValueError, match=r'^Missing x$'):
        build.need(None, 'Missing x')
    with pytest.raises(ValueError, match=r'^Unexpected time zone UTC$'):
        build.timezone_offset('UTC')


def test_word_and_read():
    u = Memory()
    build.word(u, 8, -1)
    assert u.store[8] == b'\xff\xff\xff\xff' and build.read(u, 8) == 0xffffffff
    build.word(u, 12, 0x1234567890)
    assert u.store[12] == struct.pack('<I', 0x34567890) and build.read(u, 12) == 0x34567890


def test_segments():
    """Each header's file offset and its eight words, unsigned: a kernel's addresses are past 2^31."""
    rows = [(1, 0x1000, 0x80000000, 0x80000000, 0x400, 0x800, 7, 0x1000), (4, 0x1100, 0x80000100, 0x80000100, 0x24, 0x24, 4, 4), NULL]
    assert build.segments(program(*rows, phoff=64)) == [(64, rows[0]), (96, rows[1]), (128, rows[2])]


def test_segments_wants_32_byte_headers():
    data = program(NULL)
    struct.pack_into('<H', data, 42, 56)  # ELF64's
    with pytest.raises(ValueError, match='^Unexpected ELF program header size$'):
        build.segments(data)


def two_loads():
    """A note whose offset disagrees with its LOAD's, a note where nothing is loaded, then two LOAD
    segments, the first with 0x800 bytes of bss."""
    return program((4, 0x300, 0x400100, 0x400100, 0x40, 0x40, 4, 4), (4, 0x340, 0x500000, 0x500000, 0x40, 0x40, 4, 4),
                   (1, 0, 0x400000, 0x400000, 0x800, 0x1000, 5, 0x1000), (1, 0x1800, 0x411800, 0x411800, 0x100, 0x200, 6, 0x1000),
                   NULL, size=0x1900)


def test_fileoff():
    data = two_loads()
    assert [build.fileoff(data, a) for a in (0x400000, 0x400100, 0x4007ff, 0x411800, 0x4118ff)] == [0, 0x100, 0x7ff, 0x1800, 0x18ff]


@pytest.mark.parametrize('address', [0x3fffff, 0x400800, 0x410fff, 0x411900, 0x500000])
def test_fileoff_refuses_what_the_file_does_not_hold(address):
    with pytest.raises(ValueError, match=f'^Unmapped address {address:x}$'):
        build.fileoff(two_loads(), address)


class Mapper:
    def __init__(self):
        self.calls = []

    def mem_map(self, address, size):
        self.calls.append(('map', address, size))

    def mem_write(self, address, data):
        self.calls.append(('write', address, bytes(data)))


def loads():
    """A PHDR first, a LOAD ending on a page, and a LOAD from mid-page with bss past its file bytes."""
    data = program((6, 52, 0x400034, 0x400034, 0x80, 0x80, 4, 4), (1, 0, 0x400000, 0x400000, 0x200, 0x1000, 5, 0x1000),
                   (1, 0x1123, 0x411123, 0x411123, 0x10, 0x2000, 6, 0x1000), NULL, size=0x1200)
    data[0x1123:0x1133] = bytes(range(1, 17))
    return bytes(data)


def test_load_elf():
    data, u = loads(), Mapper()
    build.load_elf(u, data, 0x10000)
    assert u.calls == [('map', 0x410000, 0x1000), ('write', 0x410000, data[:0x200]),
                       ('map', 0x421000, 0x3000), ('write', 0x421123, bytes(range(1, 17)))]


def test_load_elf_at_the_link_address_by_default():
    data, u = loads(), Mapper()
    build.load_elf(u, data)
    assert [c[:2] for c in u.calls] == [('map', 0x400000), ('write', 0x400000), ('map', 0x411000), ('write', 0x411123)]


def test_machine_at_the_link_address_by_default():
    raw = image(0x2000)
    raw[0x1000] = 0x5a
    u = build.machine(bytes(raw), 0x800000)
    assert sorted(u.mem_regions())[0] == (0x400000, 0x401fff, 7) and u.mem_read(0x400000, 1) == b'\x5a'


def appendable(size=0x10001):
    """A LOAD from offset 0 (its paddr 0, as some linkers leave it), a dynamic segment inside it, and the final PT_NULL slot."""
    return program((1, 0, 0x400000, 0, 0x800, 0x1000, 5, 0x1000), (2, 0x700, 0x400700, 0x400700, 0x80, 0x80, 6, 4), NULL, size=size)


def test_append_payload():
    data = appendable()
    before = bytes(data)
    build.append_payload(data, b'\x11' * 6, 0x401001, 0x30, 'x')
    assert struct.unpack_from('<8I', data, 52 + 64) == (1, 0x20000, 0x401001, 0x401001, 6, 0x30, 7, 0x10000)
    assert data[:52 + 64] == before[:52 + 64] and data[52 + 96:0x10001] == before[52 + 96:]
    assert data[0x10001:] == bytes(0x20000 - 0x10001) + b'\x11' * 6


def test_append_payload_on_a_64k_boundary_pads_nothing():
    data = appendable(0x20000)
    build.append_payload(data, b'\x22' * 4, 0x500000, 4, 'x')
    assert len(data) == 0x20004 and data[0x20000:] == b'\x22' * 4
    assert struct.unpack_from('<2I', data, 52 + 64) == (1, 0x20000)


@pytest.mark.parametrize('base', [0x400800, 0x401000])
def test_append_payload_refuses_an_overlap(base):
    with pytest.raises(ValueError, match='^x: payload mapping overlaps$'):
        build.append_payload(appendable(), b'', base, 0, 'x')


@pytest.mark.parametrize('rows', [[(1, 0, 0x400000, 0, 0x800, 0x1000, 5, 0x1000), NULL, (2, 0x700, 0x400700, 0x400700, 0x80, 0x80, 6, 4)],
                                  [(1, 0, 0x400000, 0, 0x800, 0x1000, 5, 0x1000), NULL, NULL],
                                  [(1, 0, 0x400000, 0, 0x800, 0x1000, 5, 0x1000)]])
def test_append_payload_wants_one_final_null_slot(rows):
    with pytest.raises(ValueError, match='^x: no final PT_NULL slot$'):
        build.append_payload(program(*rows), b'', 0x500000, 0, 'x')


def test_the_payload_has_1_5_mb_below_its_scratch_state_and_link_ld_agrees():
    """SCRATCH (0xc80000): code and constants up to 1,572,864 bytes; patch/link.ld places .scratch there too."""
    assert build.SCRATCH - build.BASE == 1_572_864 == 0x180000
    assert f'.scratch {build.SCRATCH:#x} (NOLOAD)' in (build.ROOT/'patch/link.ld').read_text()
    with pytest.raises(ValueError, match='^Payload overlaps its scratch page$'):
        build.check_layout(bytes(1_572_865), {'__scratch_start': build.SCRATCH, '__scratch_end': build.SCRATCH})


@pytest.mark.parametrize('size,refused', [(build.SCRATCH - build.BASE - 1, False), (build.SCRATCH - build.BASE, False),
                                          (build.SCRATCH - build.BASE + 1, True)])
def test_check_layout_payload_may_reach_its_scratch_page(size, refused):
    # The payload occupies [BASE, BASE+size): ending exactly at SCRATCH is accepted, one byte more refused
    ps = {'__scratch_start': build.SCRATCH, '__scratch_end': build.SCRATCH}
    if refused:
        with pytest.raises(ValueError, match='^Payload overlaps its scratch page$'):
            build.check_layout(bytes(size), ps)
    else:
        build.check_layout(bytes(size), ps)


@pytest.mark.parametrize('end,refused', [(build.SCRATCH + 0x20000, False), (build.SCRATCH + 0x20001, True)])
def test_check_layout_scratch_state_may_fill_128_kb(end, refused):
    # Scratch state of exactly 128 KB is accepted, one byte more refused
    assert build.SCRATCH_SIZE == 0x20000
    ps = {'__scratch_start': build.SCRATCH, '__scratch_end': end}
    if refused:
        with pytest.raises(ValueError, match='^Scratch state exceeds its 128 KB$'):
            build.check_layout(b'', ps)
    else:
        assert build.check_layout(b'', ps) == 0


def test_check_layout_reports_the_bytes_free(capsys):
    # The build prints both rooms' headroom and returns the scratch state's (manifest scratch_free_bytes)
    ps = {'__scratch_start': build.SCRATCH, '__scratch_end': build.SCRATCH + 0x20000 - 17_268}
    assert build.check_layout(bytes(1000), ps) == 17_268
    assert capsys.readouterr().out == ('payload room: code and constants 1,571,864 bytes free of 1,572,864; '
                                       'scratch state 17,268 bytes free of 131,072\n')


def test_check_layout_wants_the_scratch_state_at_its_page():
    # A link that put the scratch state anywhere but SCRATCH is refused
    with pytest.raises(ValueError, match='^Scratch state moved$'):
        build.check_layout(b'', {'__scratch_start': build.SCRATCH + 4, '__scratch_end': build.SCRATCH + 4})


@pytest.mark.parametrize('label,minutes', [('GMT+0 London', 0), ('GMT-3:30 St. John\'s', -210), ('GMT+12:45 Chatham', 765),
                                           ('GMT-12 Baker Island', -720), ('GMT+5:30 Kolkata', 330)])
def test_timezone_offset(label, minutes):
    assert build.timezone_offset(label) == minutes


def zones():
    demo = image()
    start = build.fileoff(demo, 0x77efb0)
    labels = [b'timedate_auto', *[f'stock{i}'.encode() for i in range(26)], b'timezone_gmt+12']
    demo[start:start + 28 * 64] = b''.join(l.ljust(64, b'\0') for l in labels)
    for address, old in ((0x4c1bd8, 0x00102980), (0x4c1ed8, 0x2402001c), (0x4c23f4, 0x2402001c), (0x504eec, 0x2c43001b)):
        struct.pack_into('<I', demo, build.fileoff(demo, address), old)
    return demo, start


def test_patch_timezones():
    demo, start = zones()
    build.patch_timezones(demo)
    table = demo[start:start + 28 * 64]
    rows = [table[i:i + 32].split(b'\0')[0] for i in range(0, 32 * (len(build.TIMEZONES) + 1), 32)]
    assert rows == [b'timedate_auto', *[z[0].encode() for z in build.TIMEZONES]]
    assert table[32 * (len(build.TIMEZONES) + 1):] == bytes(len(table) - 32 * (len(build.TIMEZONES) + 1))
    count = len(build.TIMEZONES) + 1
    assert [struct.unpack_from('<I', demo, build.fileoff(demo, a))[0] for a in (0x4c1bd8, 0x4c1ed8, 0x4c23f4, 0x504eec)] == [
        0x00102940, 0x24020000 | count, 0x24020000 | (count - 1), 0x2c430000 | (count - 1)]
    assert len(demo) == len(zones()[0])


@pytest.mark.parametrize('offset,value', [(0, b'x'), (27 * 64, b'x')])
def test_patch_timezones_wants_stock_labels(offset, value):
    demo, start = zones()
    demo[start + offset:start + offset + 64] = value.ljust(64, b'\0')
    with pytest.raises(ValueError, match='^Unexpected timezone labels$'):
        build.patch_timezones(demo)


def test_patch_timezones_table_must_fit(monkeypatch):
    demo, _ = zones()
    monkeypatch.setattr(build, 'TIMEZONES', build.TIMEZONES * 2)
    with pytest.raises(ValueError, match='^Timezone labels exceed stock table$'):
        build.patch_timezones(demo)


def test_patch_timezones_table_may_fill_up(monkeypatch):
    demo, start = zones()
    monkeypatch.setattr(build, 'TIMEZONES', build.TIMEZONES[:1] * 55)
    build.patch_timezones(demo)
    assert demo[start + 55 * 32:start + 55 * 32 + 8] == build.TIMEZONES[0][0].encode()[:8]
    demo, _ = zones()
    monkeypatch.setattr(build, 'TIMEZONES', build.TIMEZONES[:1] * 56)
    with pytest.raises(ValueError, match='^Timezone labels exceed stock table$'):
        build.patch_timezones(demo)


def test_patch_timezones_counts_are_fields_of_the_word(monkeypatch):
    demo, _ = zones()
    monkeypatch.setattr(build, 'TIMEZONES', build.TIMEZONES[:2])
    build.patch_timezones(demo)
    assert [struct.unpack_from('<I', demo, build.fileoff(demo, a))[0] for a in (0x4c1ed8, 0x4c23f4, 0x504eec)] == [0x24020003, 0x24020002, 0x2c430002]


def test_patch_timezones_label_must_fit(monkeypatch):
    demo, _ = zones()
    monkeypatch.setattr(build, 'TIMEZONES', (('x' * 32, 'UTC'),))
    with pytest.raises(ValueError, match='^Timezone labels exceed stock table$'):
        build.patch_timezones(demo)


def test_patch_timezones_longest_label_fits(monkeypatch):
    demo, start = zones()
    monkeypatch.setattr(build, 'TIMEZONES', (('x' * 31, 'UTC'),))
    build.patch_timezones(demo)
    assert demo[start + 32:start + 63] == b'x' * 31 and demo[start + 63] == 0


@pytest.mark.parametrize('address', [0x4c1bd8, 0x4c1ed8, 0x4c23f4, 0x504eec])
def test_patch_timezones_wants_stock_code(address):
    demo, _ = zones()
    struct.pack_into('<I', demo, build.fileoff(demo, address), 0)
    with pytest.raises(ValueError, match=f'^Unexpected timezone code at {address:x}$'):
        build.patch_timezones(demo)


def ui(*children, name='root', **props):
    return ['window', [0, 0, 375, 320], {'name': name, **props}, list(children)]


def label(text, **props):
    return ['label', [1, 2, 3, 4], {'text': text, **props}, []]


def texts(data):
    return [n[2].get('text') for n in ipod.walk(ipod.decode(data))][1:]


def test_english_ui_shared_table():
    data = ipod.encode(ui(label('文件夹'), label('plain'), label('取消'), ['view', [0, 0, 1, 1], {}, []]))
    assert texts(build.english_ui('home_page.bin', data)) == ['Folder', 'plain', 'Cancel', None]


def test_english_ui_per_asset_wins():
    data = ipod.encode(ui(label('专辑'), label('风格'), label('歌手')))
    assert texts(build.english_ui('dialog/search_dialog.bin', data)) == ['Albums', 'Genres', 'Artists']
    assert texts(build.english_ui('other.bin', data)) == ['Album', 'Genre', 'Artists']


def test_english_ui_key_test_page():
    assert texts(build.english_ui('testmode/test_key.bin', ipod.encode(ui(label('中'))))) == ['Center']


def test_english_ui_unknown_text_stops_the_build():
    with pytest.raises(KeyError):
        build.english_ui('home_page.bin', ipod.encode(ui(label('未知'))))


def test_english_ui_refuses_a_file_it_cannot_rewrite():
    data = bytearray(ipod.encode(ui()))
    data[4 + 31] = 1  # stray byte in the widget type's padding
    with pytest.raises(ValueError, match=r'^x: UI round trip differs$'):
        build.english_ui('x', bytes(data))


def test_a_face_keeps_its_sources_dates_and_table_order():
    from fontTools.ttLib import TTFont
    font_bytes, ranges = build.face_font(build.FACES['inter'])
    font, source = TTFont(io.BytesIO(font_bytes)), TTFont(build.ROOT/'assets/fonts/Inter-Regular.ttf')
    assert font['head'].created == source['head'].created and font['head'].modified == source['head'].modified
    assert [t for t in sorted(font.reader.tables, key=lambda t: font.reader.tables[t].offset)] == \
           [t for t in sorted(source.reader.tables, key=lambda t: source.reader.tables[t].offset) if t in font.reader.tables]


def test_a_face_refuses_its_reserved_name(tmp_path, monkeypatch):
    """A name record the rename leaves holding the family's first word (its Reserved Font Name) stops the build."""
    from fontTools.ttLib import TTFont
    font = TTFont(build.ROOT/'assets/fonts/Inter-Regular.ttf')
    assert not font['name'].getDebugName(16)  # the rename takes ID 1's family
    for rec in font['name'].names:
        if rec.nameID == 1:  # a family the other records don't spell: their Inter stays
            rec.string = 'Inter Display'
    (tmp_path/'assets/fonts').mkdir(parents=True)
    font.save(tmp_path/'assets/fonts/Inter-Regular.ttf')
    monkeypatch.setattr(build, 'ROOT', tmp_path)
    with pytest.raises(ValueError, match='^Inter font name 3 keeps Inter$'):
        build.face_font(build.FACES['inter'])


def test_a_face_must_keep_its_characters(monkeypatch):
    from fontTools import subset
    keep = subset.Subsetter.populate
    monkeypatch.setattr(subset.Subsetter, 'populate', lambda self, unicodes: keep(self, unicodes=unicodes[:-1]))
    with pytest.raises(ValueError, match='^Inter font subset lost characters$'):
        build.face_font(build.FACES['inter'])


@pytest.mark.parametrize('face', ['inter', 'inter-medium'])
def test_inter_faces(face):
    """Inter's weights, cut as the others and renamed in every record (a weight's family is its ID 16),
    their figures and colon tabular: 0-9 one advance, so "1:11" and "8:88" are as wide."""
    from fontTools.ttLib import TTFont
    f = build.FACES[face]
    font_bytes, ranges = build.face_font(f)
    font, source = TTFont(io.BytesIO(font_bytes)), TTFont(build.ROOT/'assets/fonts'/f.source)
    have = sorted(u for u in source.getBestCmap() if 0x20 <= u < 0x530 or 0x2000 <= u < 0x2200) + [build.PLAY_MARK]
    assert sorted(font.getBestCmap()) == have and 0x416 in have and 0x3a9 in have  # Cyrillic, Greek
    names = {rec.nameID: str(rec) for rec in font['name'].names}
    assert all('Inter' not in names[i] for i in (1, 3, 4, 6)) and names[6] in ('VampOLED-Regular', 'VampOLED-Medium')
    assert names[1] == ('Vamp OLED' if face == 'inter' else 'Vamp OLED Medium')
    assert names[0] == str(source['name'].getName(0, 3, 1, 0x409))
    assert re.fullmatch(rf'#define {f.ranges_macro} \{{0x20,0x7e,[0-9a-fx,]+\}}', ranges)
    cmap, hmtx = font.getBestCmap(), font['hmtx']
    digits = {hmtx[cmap[ord(c)]][0] for c in '0123456789'}
    assert len(digits) == 1 and digits != {source['hmtx'][source.getBestCmap()[ord('1')]][0]}  # not the proportional 1
    assert hmtx[cmap[ord(':')]][0] == source['hmtx']['colon.tf'][0]
    width = lambda s: sum(hmtx[cmap[ord(c)]][0] for c in s)
    assert width('1:11') == width('8:88') == width('3:47')


def test_tabular_figures_must_be_in_the_font():
    f = build.FACES['inter']
    with pytest.raises(ValueError, match='^Inter font without zero.tnum$'):
        build.subset_font(build.ROOT/'assets/fonts'/f.source, f.family, f.ranges_macro, tabular='.tnum')


def test_a_face_without_tabular_figures_keeps_its_own():
    """tabular is a face's choice: without it the figures stay the source's proportional ones."""
    from fontTools.ttLib import TTFont
    f = build.FACES['inter']
    font = TTFont(io.BytesIO(build.subset_font(build.ROOT/'assets/fonts'/f.source, f.family, f.ranges_macro)[0]))
    cmap, hmtx = font.getBestCmap(), font['hmtx']
    assert len({hmtx[cmap[ord(c)]][0] for c in '0123456789'}) > 1


def test_the_faces_header_lists_each_face_by_its_index():
    from theme import FONTS
    header = dict(line.split(' ', 2)[1:] for line in build.faces_header(RANGES))
    assert [header[f.c] for f in build.FACES.values()] == ['0', '1', '2'] and header['FONT_N'] == '3'
    assert list(FONTS) == list(build.FACES) and list(FONTS.values()) == [f.c for f in build.FACES.values()]
    assert header['FONT_NAMES'] == '{"default","inter","inter-medium"}'
    assert header['FONT_FALLBACK'] == '{0,FONT_DEFAULT,FONT_DEFAULT}'
    assert header['FONT_FALLBACK_PCT'] == '{100,98,98}'
    assert header['FONT_RANGES'] == '{0x20,0x7e,0x25b6,0x25b6,0x30,0x3a}'
    assert header['FONT_RANGE_AT'] == '{0,0,4,6}'  # stock's none: every character
    with pytest.raises(ValueError, match='^One ranges macro per subset face$'):
        build.faces_header(RANGES[:1])


def test_the_faces_header_holds_each_face_s_ascii_advances_its_line_and_em():
    """ui_kit.c ui_set's table (face_advances): per face, printable ASCII's advance widths in font units as its subset
    maps them (the digits on Inter's tabular figures), its line (hhea ascent - descent, what AWTK sizes it by) and its
    em; stock's uncut face all zeros, which ui_set measures instead."""
    from fontTools.ttLib import TTFont
    header = dict(line.split(' ', 2)[1:] for line in build.faces_header(RANGES))
    assert (header['FONT_ADV_FIRST'], header['FONT_ADV_LAST']) == ('0x20', '0x7e')
    rows = [[int(v) for v in row.split(',')] for row in header['FONT_ADV'][2:-2].split('},{')]
    assert len(rows) == 3 and all(len(r) == 0x7f - 0x20 for r in rows) and set(rows[0]) == {0}
    font = TTFont(build.ROOT/'assets/fonts/Inter-Medium.ttf')
    cmap, hmtx = font.getBestCmap(), font['hmtx']
    medium = rows[list(build.FACES).index('inter-medium')]
    assert medium[ord('W') - 0x20] == hmtx[cmap[ord('W')]][0] and medium[ord(' ') - 0x20] == hmtx[cmap[32]][0]
    assert len({medium[ord(c) - 0x20] for c in '0123456789'}) == 1  # tabular, as the subset draws them
    assert header['FONT_LINE'] == f'{{0,{2478},{2478}}}' and header['FONT_UPM'] == '{0,2048,2048}'


@pytest.mark.parametrize('face', ['inter', 'inter-medium'])
def test_both_faces_are_given_a_play_mark_on_the_middle_of_their_capitals(face):
    """View Queue's mark (navigation.c stock_row_style): neither source has U+25B6, and a title holding
    a character its face lacks leaves that face."""
    from fontTools.pens.recordingPen import RecordingPen
    from fontTools.ttLib import TTFont
    font_bytes, ranges = build.face_font(build.FACES[face])
    font = TTFont(io.BytesIO(font_bytes))
    assert ranges.endswith(',0x25b6,0x25b6}')
    name, cap = font.getBestCmap()[build.PLAY_MARK], font['OS/2'].sCapHeight
    pen = RecordingPen()
    font.getGlyphSet()[name].draw(pen)
    points = [args[0] for op, args in pen.value if op in ('moveTo', 'lineTo')]
    assert [op for op, _ in pen.value] == ['moveTo', 'lineTo', 'lineTo', 'closePath']  # a triangle
    xs, ys = sorted(x for x, _ in points), sorted(y for _, y in points)
    assert xs[0] == xs[1] and ys[1] == (ys[0] + ys[2]) // 2  # an upright edge on the left, its point level with the middle
    assert abs(ys[0] + ys[2] - cap) <= 1  # on the capitals' middle
    assert abs((ys[2] - ys[0]) / cap - .78) < .005 and abs((xs[2] - xs[0]) / cap - .70) < .005
    # at OLED's row title px (stb scales ascent - descent to the size), the upright edge is about a pixel in
    px = theme.builtins()[0]['font_px[ROLE_TITLE]']
    assert 1 <= xs[0] * px / (font['hhea'].ascent - font['hhea'].descent) < 1.1
    advance, bearing = font['hmtx'][name]
    assert bearing == xs[0] and advance == xs[2] + bearing  # as much room after it as before
    area = sum(x0 * y1 - x1 * y0 for (x0, y0), (x1, y1) in zip(points, points[1:] + points[:1]))
    if 'CFF ' in font:  # the charstring's own width is the advance; outer contours run counter-clockwise
        charstring = font['CFF '].cff.topDictIndex[0].CharStrings[name]
        charstring.draw(RecordingPen())
        assert charstring.width == advance and area > 0
    else:  # and clockwise in glyf
        assert area < 0
    assert font['maxp'].numGlyphs == len(font.getGlyphOrder()) and font.getGlyphOrder()[-1] == name


@pytest.mark.parametrize('face', [k for k, f in build.FACES.items() if f.source])
def test_the_faces_keep_every_other_glyph_as_drawn(face):
    """A subset changes no outline or metric of the original's (the licences' Modified Version); the
    mark is the one glyph added, at the end. A face with tabular figures draws TABULAR's characters
    with its own tabular glyphs."""
    from fontTools.pens.recordingPen import DecomposingRecordingPen
    from fontTools.ttLib import TTFont
    font, source = TTFont(io.BytesIO(build.face_font(build.FACES[face])[0])), TTFont(build.ROOT/'assets/fonts'/build.FACES[face].source)
    def drawn(font, name):  # components decomposed: the subset drops post's names, so a component's is uniXXXX
        glyphs = font.getGlyphSet()
        pen = DecomposingRecordingPen(glyphs)
        glyphs[name].draw(pen)
        return pen.value, font['hmtx'][name]
    cmap, was, tabular = font.getBestCmap(), source.getBestCmap(), build.FACES[face].tabular
    def original(u): return was[u] + (tabular if chr(u) in build.TABULAR else '')
    assert all(drawn(font, cmap[u]) == drawn(source, original(u)) for u in cmap if u != build.PLAY_MARK)
    assert font.getGlyphOrder()[-1] == cmap[build.PLAY_MARK]


HEART = [(50, -10), (1250, -10), (1250, 1490), (50, 1490)]
EMPTY_HEART = [(2, 84), (77, 159), (152, 84), (77, 9)]  # stock's ♡ box: x from 2, y up to 159
FONT_ORDER = ('post', 'name', 'cmap', 'OS/2', 'glyf', 'loca', 'hmtx', 'maxp', 'hhea', 'head')


def hearts_font(empty=EMPTY_HEART, composite=False, drop=()):
    """A TrueType font with the stock font's quirks: curly quotes on wide glyphs of their own, and a
    ♡ smaller than its ♥. drop leaves characters out of its Unicode cmaps. Its tables are in
    FONT_ORDER, neither sorted nor fontTools' own order."""
    from fontTools.fontBuilder import FontBuilder
    from fontTools.pens.ttGlyphPen import TTGlyphPen
    from fontTools.ttLib.sfnt import SFNTReader, SFNTWriter
    from fontTools.ttLib.tables._c_m_a_p import CmapSubtable

    def outline(points):
        pen = TTGlyphPen(None)
        pen.moveTo(points[0])
        for p in points[1:]:
            pen.lineTo(p)
        pen.closePath()
        return pen.glyph()

    metrics = {'.notdef': (1000, 100), 'quotesingle': (600, 100), 'quotedbl': (800, 100), 'quoteleft': (2048, 100),
               'quotedblleft': (2048, 100), 'heart': (1300, 50), 'heartwhite': (2048, 2)}
    fb = FontBuilder(2048, isTTF=True)
    fb.setupGlyphOrder(list(metrics))
    cmap = {0x22: 'quotedbl', 0x27: 'quotesingle', 0x2018: 'quoteleft', 0x2019: 'quoteleft', 0x201c: 'quotedblleft',
            0x201d: 'quotedblleft', 0x2661: 'heartwhite', 0x2665: 'heart'}
    fb.setupCharacterMap({c: g for c, g in cmap.items() if c not in drop})
    glyphs = {name: outline([(100, 1000), (300, 1000), (300, 1400), (100, 1400)]) for name in metrics}
    glyphs['heart'] = outline(HEART)
    glyphs['heartwhite'] = outline(empty)
    if composite:
        pen = TTGlyphPen(glyphs)
        pen.addComponent('heart', (1, 0, 0, 1, 0, 0))
        glyphs['heartwhite'] = pen.glyph()
    fb.setupGlyf(glyphs)
    fb.setupHorizontalMetrics(metrics)
    fb.setupHorizontalHeader(ascent=1854, descent=-434)
    fb.setupNameTable({'familyName': 'Hearts', 'styleName': 'Regular'})
    fb.setupOS2()
    fb.setupPost()
    mac = CmapSubtable.newSubtable(6)  # Mac Roman: its codes aren't Unicode (0xd4 is ‘)
    mac.platformID, mac.platEncID, mac.language = 1, 0, 0
    mac.cmap = {0x22: 'quotedbl', 0x27: 'quotesingle', 0xd4: 'quoteleft', 0xd5: 'quoteleft'}
    fb.font['cmap'].tables.append(mac)
    fb.font['head'].created = fb.font['head'].modified = 0xb0000000
    fb.font.recalcTimestamp = False
    built = io.BytesIO()
    fb.save(built)
    reader, out = SFNTReader(io.BytesIO(built.getvalue())), io.BytesIO()
    writer = SFNTWriter(out, len(reader.tables), reader.sfntVersion)
    for tag in FONT_ORDER:
        writer[tag] = reader[tag]
    writer.close()
    return out.getvalue()


def cmaps(data):
    from fontTools.ttLib import TTFont
    return {(t.platformID, t.platEncID): dict(t.cmap) for t in TTFont(io.BytesIO(data))['cmap'].tables}


def test_font_quotes_maps_curly_quotes_to_straight():
    src = hearts_font()
    before, after = cmaps(src), cmaps(build.font_quotes(src))
    unicode = [p for p in after if p != (1, 0)]
    assert unicode and sorted(after) == sorted(before)
    for p in unicode:
        assert after[p] == {**before[p], 0x2018: 'quotesingle', 0x2019: 'quotesingle', 0x201c: 'quotedbl', 0x201d: 'quotedbl'}
    assert after[(1, 0)] == before[(1, 0)]


def test_font_quotes_fits_the_empty_heart_to_the_full_one():
    from fontTools.ttLib import TTFont
    font = TTFont(io.BytesIO(build.font_quotes(hearts_font())))
    glyf, hmtx = font['glyf'], font['hmtx']
    # ♡ stretched onto ♥'s box: x 2..152 onto 50..1250 (8 times), y 9..159 onto -10..1490 (10 times)
    assert list(glyf['heartwhite'].coordinates) == [(50 + (x - 2) * 8, -10 + (y - 9) * 10) for x, y in EMPTY_HEART]
    empty = glyf['heartwhite']
    assert (empty.xMin, empty.yMin, empty.xMax, empty.yMax) == (50, -10, 1250, 1490)
    assert list(glyf['heart'].coordinates) == HEART
    assert hmtx['heart'] == hmtx['heartwhite'] == (1300 + 32, 50)  # ♥'s advance and a gap, the shared box's side bearing
    assert hmtx['quotesingle'] == (600, 100) and hmtx['quoteleft'] == (2048, 100)


def test_font_quotes_keeps_the_rest_of_the_font():
    from fontTools.ttLib import TTFont
    src = hearts_font()
    before, after = TTFont(io.BytesIO(src)), TTFont(io.BytesIO(build.font_quotes(src)))
    assert after['head'].modified == before['head'].modified == 0xb0000000  # reproducible
    assert after['head'].created == before['head'].created
    assert sorted(after.reader.tables, key=lambda t: after.reader.tables[t].offset) == list(FONT_ORDER)
    for tag in ('post', 'name', 'OS/2', 'maxp'):
        assert after.reader[tag] == before.reader[tag]


@pytest.mark.parametrize('font,message', [
    (dict(drop=(0x27,)), 'Font without straight quotes'), (dict(drop=(0x22,)), 'Font without straight quotes'),
    (dict(empty=[(x + 1, y) for x, y in EMPTY_HEART]), 'Unexpected ♡ glyph'),
    (dict(empty=[(x, y + 1) for x, y in EMPTY_HEART]), 'Unexpected ♡ glyph'),
    (dict(composite=True), 'Unexpected ♡ glyph')])
def test_font_quotes_wants_the_stock_glyphs(font, message):
    with pytest.raises(ValueError, match=f'^{message}$'):
        build.font_quotes(hearts_font(**font))


def strings_bin(pairs):
    """A strings/*.bin: u16 0, u16 pair count, a u32 file offset to each key and value, then the NUL-ended text."""
    texts = [t for pair in pairs for t in pair]
    offsets, at = [], 4 + 4 * len(texts)
    for t in texts:
        offsets.append(at)
        at += len(t) + 1
    return struct.pack(f'<HH{len(texts)}I', 0, len(pairs), *offsets) + b''.join(t + b'\0' for t in texts)


def test_relabel():
    # in an order of their own, so each change moves offsets past it that later ones have moved too;
    # each label between two others
    pairs = [(b'powerm_sleep', b'Sleep Timer')]
    for k, old, _ in reversed(build.LABELS):
        pairs += [(k, old), (k + b'_next', b'Kept')]
    new = {k: n for k, _, n in build.LABELS}
    out = build.relabel(strings_bin(pairs))
    assert isinstance(out, bytes) and out == strings_bin([(k, new.get(k, v)) for k, v in pairs])


@pytest.mark.parametrize('change', ['renamed', 'missing', 'twice'])
def test_relabel_wants_each_stock_label(change):
    pairs = [(b'powerm_sleep', b'Sleep Timer'), *[(k, old) for k, old, _ in build.LABELS]]
    key = build.LABELS[1][0]
    if change == 'renamed':
        pairs[2] = (key, build.LABELS[1][2])
    elif change == 'missing':
        del pairs[2]
    else:  # listed again with another value
        pairs.append((key, b'Other'))
    with pytest.raises(ValueError, match=f'^Unexpected {key.decode()} label$'):
        build.relabel(strings_bin(pairs))


def test_relabel_replaces_a_pair_stock_lists_twice():
    """Stock's en_US lists scan_now and scan_spec twice each, back to back: both copies take the new value."""
    k, old, new = next(label for label in build.LABELS if label[0] == b'scan_now')
    pairs = [(b'powerm_sleep', b'Sleep Timer'), *[(key, o) for key, o, _ in build.LABELS if key != k], (k, old), (k, old)]
    out = build.relabel(strings_bin(pairs))
    names = {key: n for key, _, n in build.LABELS}
    assert out == strings_bin([(key, names.get(key, v)) for key, v in pairs])


SOI = b'\xff\xd8'


def segment(marker, body):
    """A JPEG marker segment: its length counts itself and the body."""
    return bytes([0xff, marker]) + struct.pack('>H', len(body) + 2) + body


def frame(marker=0xc0, bits=8, height=375, width=320, components=3):
    """A start of frame: precision, lines, samples per line, then 3 bytes for each component, numbered
    from 1 as JFIF does."""
    return segment(marker, struct.pack('>BHHB', bits, height, width, components) +
                   b''.join(bytes((c, 0x11, 0)) for c in range(1, components + 1)))


JFIF = segment(0xe0, b'JFIF\0\1\1\0\0\1\0\1\0\0')


@pytest.mark.parametrize('head', [
    JFIF + segment(0xdb, bytes(65)),
    b'\xff\xff\xff' + JFIF[1:],  # fill bytes before a marker
    b'\xff\x01\xff\xd0\xff\xd7',  # TEM and RSTn stand alone, with no length
    frame(0xc4, height=1, width=1) + frame(0xc8, height=1, width=1) + frame(0xcc, height=1, width=1),  # DHT, JPG, DAC
    segment(0xe2, bytes(0x7ffe))])  # an ICC profile's APP2 may run to 32 KB
def test_jpeg_size(head):
    assert build.jpeg_size(SOI + head + frame() + segment(0xda, bytes(10)) + b'\xff\xd9') == (320, 375)


@pytest.mark.parametrize('width,height', [(1, 1), (65535, 40000)])
def test_jpeg_size_dimensions(width, height):
    assert build.jpeg_size(SOI + JFIF + frame(width=width, height=height)) == (width, height)


NOT_BASELINE_RGB = 'Logo must be an 8-bit, three-component baseline JPEG (not grayscale/CMYK)'


@pytest.mark.parametrize('data,message', [
    (b'', 'Logo must be a JPEG'), (b'\x89PNG\r\n\x1a\n', 'Logo must be a JPEG'),
    (SOI, 'No JPEG size marker'), (SOI + JFIF, 'No JPEG size marker'),
    (SOI + b'\xff\xd9' + frame(), 'No JPEG size marker'),  # EOI: the image ended
    (SOI + b'\xff\xda' + frame(), 'No JPEG size marker'),  # a scan before any frame
    (SOI + b'\0' + frame(), 'Malformed JPEG'),
    (SOI + JFIF + b'\xff\xff', 'Truncated JPEG marker'),
    (SOI + b'\xff\x00' + frame(), 'Malformed JPEG marker'),  # a stuffed zero, not a marker
    (SOI + SOI + frame(), 'Malformed JPEG marker'),
    (SOI + b'\xff\xe0\x00', 'Truncated JPEG segment'),
    (SOI + b'\xff\xe0\x00\x00', 'Invalid JPEG segment length'), (SOI + b'\xff\xe0\x00\x01' + frame(), 'Invalid JPEG segment length'),
    (SOI + JFIF[:-1], 'Invalid JPEG segment length'), (SOI + frame()[:-1], 'Invalid JPEG segment length'),
    (SOI + segment(0xc0, struct.pack('>BHHB', 8, 375, 320, 3) + bytes(6)), 'Invalid JPEG frame length'),
    (SOI + segment(0xc0, struct.pack('>BHHB', 8, 375, 320, 2) + bytes(9)), 'Invalid JPEG frame length'),
    (SOI + segment(0xc0, struct.pack('>BHH', 8, 375, 320)), 'Invalid JPEG frame length'),
    *((SOI + frame(m), NOT_BASELINE_RGB) for m in (0xc1, 0xc2, 0xc3, 0xc5, 0xc6, 0xc7, 0xc9, 0xca, 0xcb, 0xcd, 0xce, 0xcf)),
    (SOI + frame(bits=12), NOT_BASELINE_RGB), (SOI + frame(components=1), NOT_BASELINE_RGB),
    (SOI + frame(components=4), NOT_BASELINE_RGB), (SOI + frame(components=0), NOT_BASELINE_RGB),
    (SOI + frame(width=0), 'Invalid JPEG dimensions'), (SOI + frame(height=0), 'Invalid JPEG dimensions')])  # 0: DNL's
def test_jpeg_size_refuses(data, message):
    with pytest.raises(ValueError, match=f'^{re.escape(message)}$'):
        build.jpeg_size(data)


def test_source_sha256_hashes_every_input_by_path(tmp_path, monkeypatch):
    monkeypatch.setattr(build, 'ROOT', tmp_path)
    files = ['assets/icons/a.png', 'assets/b.jpg', 'patch/c.c', 'tools/d.py', 'tools/e.c', 'tools/f.h', 'tools/g.S',
             'docs/manual/build.py', 'docs/manual/guide.html', 'player/h.c', 'player/i.h', 'player/Makefile',
             'tools/adbd.patch', 'tools/uac1_module/Kbuild', 'tools/uac1_module/patches/p.patch', 'tools/stock_map.tsv']
    for name in files:
        (tmp_path/name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path/name).write_text(name)
    for name in ('tools/note.txt', 'docs/other.md', 'test/x.py', 'tools/sub/deep.py', 'player/build/host/q2player',
                 'tools/uac1_module/__pycache__/x.pyc'):
        (tmp_path/name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path/name).write_text(name)
    expected = hashlib.sha256()
    for name in sorted(files):
        expected.update(name.encode() + b'\0')
        expected.update(name.encode())
    assert build.source_sha256() == expected.hexdigest()


def test_compile_helper(tmp_path, monkeypatch):
    commands = []
    def run(*args):
        commands.append(args)
        if args[0] == 'ld.lld':
            (tmp_path/'helper').write_bytes(b'linked')
    monkeypatch.setattr(build, 'run', run)
    monkeypatch.setattr(build, 'FLAGS', ['-a', '-mno-abicalls', '-b', '-G0'])
    cat = {'lib/libc.so': b'libc', 'usr/lib/libz.so': b'libz'}.__getitem__
    assert build.compile_helper(tmp_path, cat, 'tool', 'helper', ['lib/libc.so', 'usr/lib/libz.so']) == b'linked'
    assert (tmp_path/'libc.so').read_bytes() == b'libc' and (tmp_path/'libz.so').read_bytes() == b'libz'
    objs = [tmp_path/'tool.o', tmp_path/'start.o']
    flags = ('clang', '-a', '-b', '-mnan=2008', '-mabs=2008', '-mabicalls', '-c')
    assert commands[:2] == [(*flags, build.ROOT/'patch/tool.c', '-o', objs[0]), (*flags, build.ROOT/'patch/start.c', '-o', objs[1])]
    assert commands[2] == ('ld.lld', '-m', 'elf32ltsmip', '-e', '__start', '--dynamic-linker', '/lib/ld-linux-mipsn8.so.1',
                           '--image-base=0x400000', '-z', 'noexecstack', '--gc-sections', '-s', '--hash-style=sysv', '--build-id=none',
                           *objs, f'-L{tmp_path}', '-l:libc.so', '-l:libz.so', '-o', tmp_path/'helper')


@pytest.mark.parametrize('sections', [None, False])
def test_compile_helper_sources_with_or_without_sections(tmp_path, monkeypatch, sections):
    """A list of sources: each compiled with -ffunction-sections and -fdata-sections, unless sections is False."""
    commands = []
    monkeypatch.setattr(build, 'run', lambda *args: commands.append(args) or (tmp_path/'helper').write_bytes(b'x'))
    monkeypatch.setattr(build, 'FLAGS', ['-a'])
    build.compile_helper(tmp_path, {}.__getitem__, ['one', 'two'], 'helper', [], sections=sections)
    split = ['-ffunction-sections', '-fdata-sections'] if sections is None else []
    assert commands[:3] == [('clang', '-a', '-mnan=2008', '-mabs=2008', '-mabicalls', *split, '-c',
                             build.ROOT/'patch'/f'{s}.c', '-o', tmp_path/f'{s}.o') for s in ('one', 'two', 'start')]


def test_q2_iap_is_built_from_one_list_of_sources():
    """tools/build.py (the image), tools/iap_probe/probe_build.py and test/iap_probe.py (which takes probe_build's)
    build q2-iap from the same sources, and each is a file."""
    spec = importlib.util.spec_from_file_location('iap_probe_build', build.ROOT/'tools/iap_probe/probe_build.py')
    assert spec and spec.loader
    probe_build = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(probe_build)
    assert probe_build.SOURCES == build.Q2IAP_SOURCES
    assert all((build.ROOT/'patch'/f'{s}.c').is_file() for s in build.Q2IAP_SOURCES)


def test_bt_silence_commands(monkeypatch):
    commands = []
    def run(*args):
        commands.append(args)
        if args[0] == 'llvm-objcopy':
            args[-1].write_bytes(b'code')
        return ''
    monkeypatch.setattr(build, 'run', run)
    monkeypatch.setattr(build, 'symbols', lambda path: {'path': path.name})
    assert build.bt_silence() == (b'code', {'path': 's.elf'})
    cc, ld, objcopy = commands
    obj, elf, binary = cc[-1], ld[-1], objcopy[-1]
    assert (obj.name, elf.name, binary.name) == ('s.o', 's.elf', 's.bin') and obj.parent == elf.parent == binary.parent
    assert cc == ('clang', '--target=mipsel-linux-gnu', '-march=mips32r2', '-mno-abicalls', '-fno-pic', '-DBUDGET=192000', '-DMTU_MAX=675', '-c',
                  build.ROOT/'tools/bt_silence.S', '-o', obj)
    assert ld == ('ld.lld', '-m', 'elf32ltsmip', '-Ttext=0x450400', '-e', 'timeout', obj, '-o', elf)
    assert objcopy == ('llvm-objcopy', '-O', 'binary', '-j', '.text', elf, binary)


@pytest.mark.skipif(not shutil.which('llvm-objcopy'), reason='no LLVM')
def test_bt_silence():
    code, symbols = build.bt_silence()
    assert len(code) % 4 == 0 and len(code) > 0
    for name in ('timeout', 'fill', 'read', 'sbc_bp', 'mtu_clamp', 'aac_info', 'aac_rate'):
        assert build.BT_SILENCE <= symbols[name] < build.BT_SILENCE + len(code)
    assert symbols['timeout'] == build.BT_SILENCE  # its entry
    # SBC's three bytes and AAC's four where q2guard writes them (patch/q2bt.h BT_SBC_POOL and BT_AAC_RATE), then
    # AAC's two stubs
    assert symbols['sbc_pool'] == build.BT_SBC_POOL and symbols['aac_ask'] == build.BT_AAC_RATE == build.BT_SBC_POOL + 4
    assert symbols['aac_info'] == build.BT_AAC_RATE + 4 < symbols['aac_rate'] < build.BT_SILENCE + len(code)


# AAC's quality (docs/internals.md#bluetooth-aac): stock bluealsa's words, read from V1.36's binary, and what the
# build makes them: main's forced aac_bitrate 220000 (lui $2,3; addiu $2,$2,0x5b60) to 240000 (lui $2,4; addiu
# $2,$2,-0x5680), and the encoder thread's VBR mode cap of 4 (addiu $17,$zero,4 past the table; addiu $2,$zero,4 for
# the 5 it turns down) to upstream's 5.
AAC_STOCK = {0x403f2c: 0x3c020003, 0x403f3c: 0x24425b60, 0x416f58: 0x24110004, 0x4171a0: 0x24020004}
AAC_NEW = {0x403f2c: 0x3c020004, 0x403f3c: 0x2442a980, 0x416f58: 0x24110005, 0x4171a0: 0x24020005}


def bluealsa():
    raw = image(0x4e000, 0x400000, null=True)
    raw[build.AAC_44K1] = 0xff
    for address, old in AAC_STOCK.items():
        struct.pack_into('<I', raw, build.fileoff(raw, address), old)
    for caller, _ in build.BT_PCM_CALLERS:
        struct.pack_into('<I', raw, build.fileoff(raw, caller - 8), 0x0c105174)
    for address, old, _, _ in build.BT_SILENCE_SITES:
        struct.pack_into('<I', raw, build.fileoff(raw, address), old)
    return bytes(raw)


@pytest.fixture
def silence(monkeypatch):
    symbols = {'timeout': build.BT_SILENCE, 'fill': build.BT_SILENCE + 8, 'read': build.BT_SILENCE + 16,
               'sbc_bp': build.BT_SILENCE + 20, 'mtu_clamp': build.BT_SILENCE + 24, 'sbc_pool': build.BT_SBC_POOL,
               'aac_ask': build.BT_AAC_RATE, 'aac_info': build.BT_SILENCE + 32, 'aac_rate': build.BT_SILENCE + 40}
    monkeypatch.setattr(build, 'bt_silence', lambda: (b'\x11' * 24, symbols))
    return symbols


def payload(out):
    _, header = build.segments(out)[-1]
    assert header[:3] == (1, header[1], build.BT_DROP_PAYLOAD)
    return bytes(out[header[1]:header[1] + header[4]]), header


def test_patch_bluealsa_drops_aac_44k1(silence):
    raw = bluealsa()
    out = build.patch_bluealsa(raw)
    assert out[build.AAC_44K1] == 0
    assert isinstance(out, bytes) and raw[build.AAC_44K1] == 0xff


def test_patch_bluealsa_payload(silence):
    out = build.patch_bluealsa(bluealsa())
    data, header = payload(out)
    assert header[3] == build.BT_DROP_PAYLOAD and header[6] == 7
    assert len(data) == build.BT_SILENCE - build.BT_DROP_PAYLOAD + 24 and data[-24:] == b'\x11' * 24
    words = struct.unpack(f'<{(len(data) - 24) // 4}I', data[:-24])
    assert words[:4] == (0x27bdffe0, 0xafbf001c, 0xafb00018, 0x00808025)
    reset = 21
    assert words[reset] == 0x8fa80054
    at = reset + 1
    for caller, offset in build.BT_PCM_CALLERS:
        size = 6 if offset is not None else 9
        assert words[at:at + 4] == (0x3c0a0000 | caller >> 16, 0x354a0000 | caller & 0xffff, 0x150a0000 | size + 1, 0)
        if offset is not None:
            assert words[at + 4] == 0x264b0000 | offset
        assert words[at + 4 + size - 2:at + 4 + size] == (0x081051e4, 0)
        at += 4 + size
    assert words[at:at + 2] == (0x081051e4, 0) and set(words[at + 2:]) <= {0}


def test_patch_bluealsa_hooks(silence):
    raw = bluealsa()
    out = build.patch_bluealsa(raw)
    assert struct.unpack_from('<2I', out, build.fileoff(raw, build.BT_DROP)) == (build.j(build.BT_DROP_PAYLOAD), 0)
    reset = build.BT_DROP_PAYLOAD + 21 * 4
    assert struct.unpack_from('<2I', out, build.fileoff(raw, build.BT_DROP_HANDLER)) == (build.j(reset), 0)
    expected = {0x41465c: build.j(silence['timeout']), 0x414684: build.j(silence['fill']), 0x414688: 0x00002825,
                0x414754: build.jal(silence['read']), 0x42d09c: 0x004147a0,
                0x406580: build.jal(silence['sbc_bp']),  # the SBC encoder's one sbc_encode
                0x40abf8: build.jal(silence['mtu_clamp']),  # where the A2DP acquire stores the write MTU
                0x417268: build.jal(silence['aac_info']),  # the AAC encoder's one aacEncInfo
                0x4175a4: build.jal(silence['aac_rate'])}  # and its loop's aacEncEncode
    for address, new in expected.items():
        assert struct.unpack_from('<I', out, build.fileoff(raw, address))[0] == new


def test_patch_bluealsa_refuses_a_moved_caller(silence):
    raw = bytearray(bluealsa())
    struct.pack_into('<I', raw, build.fileoff(raw, 0x418114 - 8), 0)
    with pytest.raises(ValueError, match='^Bluetooth PCM caller mismatch$'):
        build.patch_bluealsa(bytes(raw))


@pytest.mark.parametrize('site', range(9))
def test_patch_bluealsa_refuses_a_moved_site(silence, site):
    raw = bytearray(bluealsa())
    struct.pack_into('<I', raw, build.fileoff(raw, build.BT_SILENCE_SITES[site][0]), 0xdead)
    with pytest.raises(ValueError, match='^Bluetooth silence site mismatch$'):
        build.patch_bluealsa(bytes(raw))


def test_patch_bluealsa_raises_aac_quality(silence):
    """The forced peak 240000 (a frame at most 640 bytes, under the 663 one packet holds at the 675-byte MTU) and fdk-aac
    VBR mode 5 where the bitrate allows it, as upstream bluealsa 4.0.0."""
    raw = bluealsa()
    out = build.patch_bluealsa(raw)
    got = {a: struct.unpack_from('<I', out, build.fileoff(raw, a))[0] for a in AAC_STOCK}
    assert got == AAC_NEW
    hi, lo = got[0x403f2c] & 0xffff, got[0x403f3c] & 0xffff
    assert (hi << 16) + (lo - 0x10000 if lo & 0x8000 else lo) == 240000
    assert 240000 * 1024 // 48000 // 8 == 640 <= build.BT_MTU_MAX - 12 == 663
    assert {got[0x416f58] & 0xffff, got[0x4171a0] & 0xffff} == {5}


@pytest.mark.parametrize('site', sorted(AAC_STOCK))
def test_patch_bluealsa_refuses_a_moved_aac_site(silence, site):
    raw = bytearray(bluealsa())
    struct.pack_into('<I', raw, build.fileoff(raw, site), 0xdead)
    with pytest.raises(ValueError, match='^Bluetooth AAC site mismatch$'):
        build.patch_bluealsa(bytes(raw))


def test_the_launcher_turns_the_afterburner_on():
    """bluealsa2's line: fdk-aac's afterburner (--aac-afterburner, which Shanling's forced options leave alone)."""
    stock = b'bluealsa -c LDAC -c aptX-HD -c aptX -c AAC'
    line = stock.replace(*build.BT_KEEPALIVE).replace(*build.BT_GUEST_SOURCE)
    assert b' --aac-afterburner ' in line and b'--keep-alive=5' in line and line.endswith(b'-c AAC')


def test_the_launcher_starts_q2_bip_behind_bluealsa():
    """bluealsa2 with cover art's responder: in the background, on a line of its own, and bluealsa's line as it was,
    still the script's last and in the foreground, so the script ends when bluealsa does (and q2-bip with it)."""
    stock = b'bluealsa -c LDAC -c aptX-HD -c aptX -c AAC\n'
    before = stock.replace(*build.BT_KEEPALIVE).replace(*build.BT_GUEST_SOURCE)
    assert before.count(build.BT_COVER_START[0]) == 1
    assert before.replace(*build.BT_COVER_START) == b'q2-bip &\n' + before
    assert build.BIP == 'usr/bin/q2-bip' and build.BIP_SOURCES == ['q2bip', 'q2bip_core']


def test_patch_bluealsa_refuses_sbc_bytes_elsewhere(silence):
    silence['sbc_pool'] += 4
    with pytest.raises(ValueError, match='^SBC bitpool bytes not at BT_SBC_POOL$'):
        build.patch_bluealsa(bluealsa())


def test_patch_bluealsa_refuses_aac_bytes_elsewhere(silence):
    silence['aac_ask'] += 4
    with pytest.raises(ValueError, match='^AAC peak bytes not at BT_AAC_RATE$'):
        build.patch_bluealsa(bluealsa())


def test_patch_bluealsa_payload_may_fill_its_room(silence, monkeypatch):
    words = 21 + 1 + sum(4 + (6 if offset is not None else 9) for _, offset in build.BT_PCM_CALLERS) + 2
    monkeypatch.setattr(build, 'BT_SILENCE', build.BT_DROP_PAYLOAD + words * 4)
    assert build.patch_bluealsa(bluealsa())
    monkeypatch.setattr(build, 'BT_SILENCE', build.BT_DROP_PAYLOAD + words * 4 - 4)
    with pytest.raises(ValueError, match='^Bluetooth DROP payload too long$'):
        build.patch_bluealsa(bluealsa())


def test_patch_bluealsa_names_itself_in_a_mapping_error(silence):
    raw = bytearray(bluealsa())
    struct.pack_into('<H', raw, 44, 1)  # no final PT_NULL slot
    with pytest.raises(ValueError, match='^bluealsa: no final PT_NULL slot$'):
        build.patch_bluealsa(bytes(raw))


def test_patch_bluealsa_payload_must_fit(silence, monkeypatch):
    monkeypatch.setattr(build, 'BT_SILENCE', build.BT_DROP_PAYLOAD + 8)
    with pytest.raises(ValueError, match='^Bluetooth DROP payload too long$'):
        build.patch_bluealsa(bluealsa())


def elf(*headers, size=0x3000):
    """An ELF32 image of size bytes whose program headers, (type, offset, vaddr, filesz, memsz) each, start at 52."""
    data = bytearray(size)
    struct.pack_into('<I', data, 28, 52)
    struct.pack_into('<HH', data, 42, 32, len(headers))
    for i, (kind, offset, vaddr, filesz, memsz) in enumerate(headers):
        struct.pack_into('<8I', data, 52 + 32 * i, kind, offset, vaddr, vaddr, filesz, memsz, 5, 0x1000)
    return data


@pytest.mark.parametrize('load', [0, 0x100000])
def test_machine(load):
    from unicorn.mips_const import UC_MIPS_REG_A0
    b = elf((1, 0x1000, 0x400123, 0x10, 0x2000), (4, 0x1000, 0x800000, 0x10, 0x10), (1, 0x2000, 0x410000, 8, 0x100))
    b[0x1000:] = b'\xee' * (len(b) - 0x1000)  # bytes past a segment's filesz aren't its own
    struct.pack_into('<2I', b, 0x2000, 0x2402002a, 0xac820000)  # addiu v0, zero, 42; sw v0, 0(a0)
    u = build.machine(bytes(b), 0x2000000, load)
    # whole pages around each LOAD's memsz; nothing for the PT_NOTE
    assert sorted(r[:2] for r in u.mem_regions()) == [(load + 0x400000, load + 0x402fff), (load + 0x410000, load + 0x410fff),
                                                      (0x2000000, 0x200ffff)]
    assert all(r[2] == 7 for r in u.mem_regions())
    assert u.mem_read(load + 0x400000, 0x3000) == bytes(0x123) + b'\xee' * 0x10 + bytes(0x3000 - 0x133)
    assert u.mem_read(load + 0x410000, 0x1000)[8:] == bytes(0xff8)
    u.reg_write(UC_MIPS_REG_A0, 0x2000000)
    u.emu_start(load + 0x410000, load + 0x410008)
    assert u.mem_read(0x2000000, 4) == b'\x2a\0\0\0'  # little-endian, into the scratch


# The TagLib cover library's layout (test/m4b.py): the M4A test's beq at SITE to the MP4 extractor
# (0x1040004a, to SITE + 4 + 0x4a * 4), the next format's test after its delay slot, gp and strcasecmp's GOT slot.
SITE, MP4, NEXT, GP, STRCASECMP = 0x125ac, 0x126d8, 0x125b4, 0x327e0, 0x327e0 - 0x7e90


def taglib():
    lib = elf((1, 0x1000, 0x12000, 0x1000, 0x1000), (1, 0x2000, 0x2a000, 0x1000, 0x1000), (0, 0, 0, 0, 0))
    struct.pack_into('<I', lib, build.fileoff(lib, SITE), 0x1040004a)
    return bytes(lib)


@pytest.fixture
def tag_c(monkeypatch):
    lib = taglib()
    monkeypatch.setattr(build, 'TAG_C_SHA', hashlib.sha256(lib).hexdigest())  # the pin is stock's library
    return lib


def dispatch(lib, ext, load):
    """Where lib's cover dispatch goes for ext, entered at SITE with strcasecmp's answer for m4a in v0,
    loaded at load; and what else strcasecmp was asked to compare ext with."""
    from unicorn import UC_HOOK_CODE
    from unicorn.mips_const import (UC_MIPS_REG_A0, UC_MIPS_REG_A1, UC_MIPS_REG_GP, UC_MIPS_REG_PC, UC_MIPS_REG_RA,
                                    UC_MIPS_REG_S0, UC_MIPS_REG_SP, UC_MIPS_REG_V0)
    scratch = 0x2000000
    gc.collect()  # a hooked Unicorn is in a cycle, and holds 1 GB of address space until it's collected
    u = build.machine(lib, scratch, load)
    strcasecmp, name, sp = scratch, scratch + 0x100, scratch + 0x8000
    build.word(u, load + STRCASECMP, strcasecmp)  # as the loader fills the GOT
    build.word(u, sp + 16, load + GP)  # o32: the caller's gp, saved
    u.mem_write(name, ext.encode() + b'\0')
    for reg, value in ((UC_MIPS_REG_S0, name), (UC_MIPS_REG_GP, load + GP), (UC_MIPS_REG_SP, sp),
                       (UC_MIPS_REG_V0, int(ext.lower() != 'm4a'))):
        u.reg_write(reg, value)
    compared, reached = [], []

    def string(p):
        return bytes(u.mem_read(p, 16)).split(b'\0')[0]

    def hook(u, address, size, _):
        if address == strcasecmp:
            a, b = string(u.reg_read(UC_MIPS_REG_A0)), string(u.reg_read(UC_MIPS_REG_A1))
            compared.append(b)
            u.reg_write(UC_MIPS_REG_V0, int(a.lower() != b.lower()))
            u.reg_write(UC_MIPS_REG_PC, u.reg_read(UC_MIPS_REG_RA))
        elif address - load in (MP4, NEXT):
            reached.append(address - load)
            u.emu_stop()
    u.hook_add(UC_HOOK_CODE, hook)
    u.emu_start(load + SITE, 0, count=32)
    return reached, compared


@pytest.mark.parametrize('load', [0, 0x7700000])  # a shared library goes anywhere
@pytest.mark.parametrize('ext,goes,compared', [
    ('m4a', MP4, []), ('M4A', MP4, []), ('m4b', MP4, [b'm4b']), ('M4B', MP4, [b'm4b']), ('m4B', MP4, [b'm4b']),
    ('mp3', NEXT, [b'm4b']), ('m4bx', NEXT, [b'm4b']), ('m4', NEXT, [b'm4b'])])
def test_patch_m4b_cover_sends_m4b_to_the_mp4_extractor(tag_c, load, ext, goes, compared):
    assert dispatch(build.patch_m4b_cover(tag_c), ext, load) == ([goes], compared)


def test_cover_dispatch_without_the_patch():
    assert dispatch(taglib(), 'm4b', 0) == ([NEXT], []) and dispatch(taglib(), 'm4a', 0) == ([MP4], [])


def test_patch_m4b_cover_segment(tag_c):
    out = build.patch_m4b_cover(tag_c)
    at, header = build.segments(out)[-1]
    offset = header[1]
    assert header == (1, offset, 0x30000, 0x30000, 48, 48, 5, 0x10000)  # read and execute: nothing in it changes
    assert offset % 0x10000 == 0 and offset >= len(tag_c) and len(out) == offset + 48
    assert out[len(tag_c):offset] == bytes(offset - len(tag_c)) and isinstance(out, bytes)
    words = (0x104089b5, 0, 0x8f998170, 0x02002025, 0x2785d84c, 0x0320f809, 0, 0x104089ae, 0x8fbc0010, 0x10008963, 0)
    assert out[offset:] == struct.pack('<11I', *words) + b'm4b\0'
    # in the library itself, only the M4A branch and the PT_NULL slot change
    site = build.fileoff(tag_c, SITE)
    rest = bytearray(out[:len(tag_c)])
    assert rest[site:site + 4] != tag_c[site:site + 4]
    rest[site:site + 4], rest[at:at + 32] = tag_c[site:site + 4], tag_c[at:at + 32]
    assert rest == tag_c


def test_patch_m4b_cover_wants_the_pinned_library():
    with pytest.raises(ValueError, match='^Unexpected TagLib cover library$'):
        build.patch_m4b_cover(taglib())


def test_patch_m4b_cover_wants_the_m4a_branch(monkeypatch):
    lib = bytearray(taglib())
    struct.pack_into('<I', lib, build.fileoff(lib, SITE), 0x1040004b)
    monkeypatch.setattr(build, 'TAG_C_SHA', hashlib.sha256(lib).hexdigest())
    with pytest.raises(ValueError, match='^Unexpected TagLib M4A cover branch$'):
        build.patch_m4b_cover(bytes(lib))


def test_patch_m4b_cover_names_itself_in_a_mapping_error(monkeypatch):
    raw = bytearray(taglib())
    struct.pack_into('<H', raw, 44, 2)  # no final PT_NULL slot
    monkeypatch.setattr(build, 'TAG_C_SHA', build.sha(raw))
    with pytest.raises(ValueError, match='^TagLib cover: no final PT_NULL slot$'):
        build.patch_m4b_cover(bytes(raw))


RANGES = ['#define INTER_RANGES {0x20,0x7e,0x25b6,0x25b6}', '#define INTER_MEDIUM_RANGES {0x30,0x3a}']


def stock_h(dev=0, repo=build.REPO):
    from theme import builtins
    context = {name: f'0x{0x700000 + 16 * i:x}u' for i, name in enumerate(build.CONTEXT_DATA)}  # as demo's symbols
    return build.stock_header(context, RANGES, builtins(), dev, repo)


@pytest.mark.parametrize('dev,beta,repo,version,tag,flag', [(0, 0, build.REPO, f'{build.VERSION} Muse', build.VERSION, '0'),
                                                            (0, 2, build.REPO, f'{build.VERSION} Muse beta 2', build.VERSION, '0'),
                                                            (7, 2, 'someone/q2-fork', f'{build.VERSION} Muse dev 7', '', '1')])
def test_stock_header_versions(dev, beta, repo, version, tag, flag, monkeypatch):
    monkeypatch.setattr(build, 'BETA', beta)
    header = stock_h(dev, repo)
    lines = header.splitlines()
    assert header.endswith('\n') and all(line.startswith('#define ') for line in lines)
    macros = dict(line.split(' ', 2)[1:] for line in lines)
    assert len(macros) == len(lines) and [f'#define {name} {macros[name]}' for name in ('INTER_RANGES', 'INTER_MEDIUM_RANGES')] == RANGES
    assert macros['Q2POD_VERSION'] == f'"{version}"' and macros['Q2POD_DEV'] == flag
    assert macros['Q2POD_TAG'] == f'"{tag}"'  # a dev build has none, and takes any release
    assert macros['Q2POD_REPO'] == f'"{repo}"'
    assert macros['Q2POD_UA'] == f'"Q2Muse/{build.VERSION} (https://github.com/{build.REPO})"'  # the project's contact, not --update-repo's
    assert macros['bt_showcoding'] == '((const unsigned char *)0x700000u)'


@pytest.mark.skipif(not shutil.which('clang'), reason='no clang')
def test_stock_header_is_c(tmp_path, monkeypatch):
    """stock.h as the payload's C reads it: built into a host library whose data ctypes reads back."""
    monkeypatch.setattr(build, 'BETA', 0)
    import ipod
    import theme
    themes = theme.builtins()
    (tmp_path/'stock.h').write_text(stock_h())
    (tmp_path/'h.c').write_text(f'''#include "stock.h"
const int h_ints[] = {{RING_STEP, THEME_N, THEME_KEYS, Q2POD_DEV}};
const char *const h_strings[] = {{ROCKBOX_FLAG, STOCK_VERSION, Q2POD_VERSION, Q2POD_TAG, Q2POD_REPO, Q2POD_UA}};
const char *const h_zones[] = TIMEZONE_NAMES;
const char *const h_cities[] = TIMEZONE_CITIES;
const int h_order[] = TIMEZONE_ORDER;
const char h_icons[] = SETTINGS_ICON_NAMES;
const unsigned char *const h_context[] = {{{', '.join(build.CONTEXT_DATA)}}};
const unsigned short h_medium[] = INTER_MEDIUM_RANGES;
const char *const h_faces[FONT_N] = FONT_NAMES;
const unsigned char h_fallback[FONT_N] = FONT_FALLBACK, h_pct[FONT_N] = FONT_FALLBACK_PCT;
const unsigned short h_ranges[] = FONT_RANGES, h_at[FONT_N + 1] = FONT_RANGE_AT;
const int h_inter[] = {{FONT_INTER, FONT_INTER_MEDIUM}};
const unsigned long h_sizes[] = {{sizeof h_zones / sizeof *h_zones, sizeof h_cities / sizeof *h_cities,
    sizeof h_order / sizeof *h_order, sizeof h_icons, sizeof h_medium / sizeof *h_medium}};
''')
    subprocess.run(['clang', '-shared', '-fPIC', '-std=c11', '-Wall', '-Wextra', '-Werror', str(tmp_path/'h.c'), '-o', str(tmp_path/'h.so')],
                   check=True)
    lib = ctypes.CDLL(str(tmp_path/'h.so'))

    def array(kind, name, n):
        return list((kind * n).in_dll(lib, name))
    zones, cities, order, icons, medium = array(ctypes.c_ulong, 'h_sizes', 5)
    assert array(ctypes.c_int, 'h_ints', 4) == [build.RING_STEP, len(themes), len(theme.theme_keys(themes)), 0]
    assert [s.decode() for s in array(ctypes.c_char_p, 'h_strings', 6)] == [
        build.ROCKBOX_FLAG, 'V1.36', f'{build.VERSION} Muse', build.VERSION, build.REPO, f'Q2Muse/{build.VERSION} (https://github.com/{build.REPO})']
    assert [s.decode() for s in array(ctypes.c_char_p, 'h_zones', zones)] == [zone for _, zone in build.TIMEZONES]
    # each row's city, its label after the offset
    cities = [s.decode() for s in array(ctypes.c_char_p, 'h_cities', cities)]
    assert len(cities) == len(build.TIMEZONES) and "St. John's" in cities
    assert all(re.fullmatch(rf'GMT[+-]\d+(:\d\d)? {re.escape(city)}', label) for (label, _), city in zip(build.TIMEZONES, cities))
    # every saved index once, by city: the rows' tie order (navigation.c tz_now sorts by the offset now)
    order = array(ctypes.c_int, 'h_order', order)
    rows = [build.TIMEZONES[i][0].split(' ', 1)[1] for i in order]
    assert sorted(order) == list(range(len(build.TIMEZONES))) and rows == sorted(rows)
    # named_in's list: NUL-separated names, an empty one last
    icons = (ctypes.c_char * icons).in_dll(lib, 'h_icons').raw
    assert icons.endswith(b'\0\0') and all(n.endswith('.png') for n in ipod.SETTINGS_ICONS)
    assert sorted(icons[:-2].split(b'\0')) == sorted(n.removesuffix('.png').encode() for n in ipod.SETTINGS_ICONS)
    assert array(ctypes.c_void_p, 'h_context', len(build.CONTEXT_DATA)) == [0x700000 + 16 * i for i in range(len(build.CONTEXT_DATA))]
    assert array(ctypes.c_ushort, 'h_medium', medium) == [0x30, 0x3a]
    # the faces' table (navigation.c face_has, theme_font), by FONT_*
    n = len(build.FACES)
    assert [s.decode() for s in array(ctypes.c_char_p, 'h_faces', n)] == [f.name for f in build.FACES.values()]
    assert array(ctypes.c_ubyte, 'h_fallback', n) == [0, 0, 0] and array(ctypes.c_ubyte, 'h_pct', n) == [100, 98, 98]
    assert array(ctypes.c_ushort, 'h_at', n + 1) == [0, 0, 4, 6] and array(ctypes.c_int, 'h_inter', 2) == [1, 2]
    assert array(ctypes.c_ushort, 'h_ranges', 6) == [0x20, 0x7e, 0x25b6, 0x25b6, 0x30, 0x3a]


def test_build_refuses_a_zip_that_is_not_the_audited_one(tmp_path):
    zip_path = tmp_path/'stock.zip'
    zip_path.write_bytes(b'not the audited ZIP')
    with pytest.raises(ValueError, match='^Unsupported ZIP: SHA-256 differs from audited original$'):
        build.build(zip_path, tmp_path/'out', None)


@pytest.mark.parametrize('version,beta', [('1.7.10', 0), ('1.7', 0), ('1.7.6.0', 0), ('10.7.6', 0), ('17.6', 0), ('1.76', 0),
                                          ('1.x.6', 0), ('1-7-6', 0), ('1.8.0', 10), ('1.8.0', -1)])
def test_build_refuses_a_version_not_of_three_single_digits(tmp_path, monkeypatch, version, beta):
    # 17.6 and 1.76 pack to a 5-character tag too, so the tag's length alone let them through; refused before the ZIP is read
    monkeypatch.setattr(build, 'VERSION', version)
    monkeypatch.setattr(build, 'BETA', beta)
    with pytest.raises(ValueError, match='^VERSION must be three single digits'):
        build.build(tmp_path/'no such.zip', tmp_path/'out', None)
    assert not (tmp_path/'out').exists()


@pytest.mark.parametrize('version,beta,ok', [('1.8.0', 0, True), ('1.8.0', 2, True), ('0.0.1', 9, True), ('9.9.9', 0, True),
                                             ('17.6', 0, False), ('1.8.0', 10, False)])
def test_version_ok(version, beta, ok):
    assert build.version_ok(version, beta) == ok


# The rootfs's hardening (docs/building.md#what-the-rootfs-drops), on stand-in files shaped as stock's.
STOCK_SHADOW = b'root:$5$salt$hash:::::::\ndaemon:*:::::::\nnobody:*:::::::\n'


def test_lock_root_leaves_no_password_login():
    assert build.password_logins(STOCK_SHADOW) == ['root']
    locked = build.lock_root(STOCK_SHADOW)
    assert locked == b'root:*:::::::\ndaemon:*:::::::\nnobody:*:::::::\n' and build.password_logins(locked) == []


@pytest.mark.parametrize('field, logs_in', [(b'$5$s$h', True), (b'', True), (b'*', False), (b'!', False), (b'!$5$s$h', False)])
def test_password_logins_counts_an_empty_field_or_a_hash(field, logs_in):
    assert build.password_logins(b'daemon:*:::::::\nuser:' + field + b':::::::\n') == (['user'] if logs_in else [])


@pytest.mark.parametrize('shadow', [b'daemon:*:::::::\n', STOCK_SHADOW + b'root:x:::::::\n'])
def test_lock_root_wants_one_root(shadow):
    with pytest.raises(ValueError, match='Unexpected /etc/shadow'):
        build.lock_root(shadow)


def test_the_serial_getty_goes():
    inittab = (b'::sysinit:/etc/init.d/rcS\n\n# Put a getty on the serial port\n'
               b'console::respawn:/sbin/getty -L ttyS3 115200 vt100 # GENERIC_SERIAL\n\n# Stuff to do\n')
    assert inittab.replace(*build.SERIAL_GETTY) == b'::sysinit:/etc/init.d/rcS\n\n# Stuff to do\n'


def test_the_debugging_tools_are_dropped():
    removed = set(build.OBSOLETE)
    for tool in ('strace', 'sqlite3', 'hcidump', 'memtester', 'telnet', 'tftp'):
        assert f'usr/bin/{tool}' in removed, tool
    assert {'usr/sbin/nandtest', 'usr/sbin/inetd'} <= removed
    assert 'usr/lib/libsqlite3.so.0.8.6' not in removed  # the library demo uses stays


def test_bluetooth_discovery_times_out():
    main = b'# 0 = disable timer\n#DiscoverableTimeout = 0\nDiscoverableTimeout = 0\n'
    assert main.count(build.BT_DISCOVERABLE[0]) == 1
    assert main.replace(*build.BT_DISCOVERABLE) == b'# 0 = disable timer\n#DiscoverableTimeout = 0\nDiscoverableTimeout = 180\n'


def test_bluetooth_name_is_q2_muse():
    main = b"[General]\n# Defaults to 'BlueZ X.YZ'\nName = Shanling Q2\n"
    assert main.count(build.BT_NAME[0]) == 1
    assert main.replace(*build.BT_NAME) == b"[General]\n# Defaults to 'BlueZ X.YZ'\nName = Vamp\n"


def test_bluez_experimental_interfaces_are_on():
    main = b"# Enables D-Bus experimental interfaces\n# Possible values: true or false\n#Experimental = false\n"
    assert main.count(build.BT_EXPERIMENTAL[0]) == 1
    assert main.replace(*build.BT_EXPERIMENTAL).endswith(b"\nExperimental = true\n")


def test_own_bluez_builds_turn_the_gatt_server_off():
    main = b"#Cache = always\n\n[GATT]\n# GATT attribute cache.\n"
    assert main.count(build.BT_GATT_SERVER[0]) == 1
    edited = main.replace(*build.BT_GATT_SERVER)
    assert b'\n[GATT]\n' in edited and edited.count(b'\nServer = false\n') == 1
    assert edited.index(b'[GATT]') < edited.index(b'Server = false')  # in [GATT], where vamp.patch reads it


def test_network_time_asks_the_worldwide_pool_in_place():
    servers = b'time.apple.com\0\0cn.pool.ntp.org\0time.windows.com\0'
    assert len(build.NTP_POOL[0]) == len(build.NTP_POOL[1])
    assert servers.replace(*build.NTP_POOL) == b'time.apple.com\0\x000.pool.ntp.org\0\0time.windows.com\0'


# Stock's HTTPS (docs/internals.md#stock-https): the setopt sites patched, the bundle pinned, the plain-HTTP Tidal URLs.
def setopt_site(*words):
    """A 0x400000-based image holding words from 0x400100, and its address of the last one."""
    data = image(0x1000)
    for k, w in enumerate(words):
        struct.pack_into('<I', data, 0x1100 + 4 * k, w)
    return data, 0x400100 + 4 * (len(words) - 1)


@pytest.mark.parametrize('option', [0x40, 0x51])
def test_stock_tls_site_is_a_setopt_of_that_option(option):
    data, at = setopt_site(build.SETOPT_GOT, 0x00003025, 0x24050000 | option, 0x8fbc0010, 0x0320f809)
    assert build.stock_tls_site(data, at, option)
    assert not build.stock_tls_site(data, at, 0x51 if option == 0x40 else 0x40)  # another option
    data, at = setopt_site(0x8f99f600, 0x00003025, 0x24050000 | option, 0x8fbc0010, 0x0320f809)
    assert not build.stock_tls_site(data, at, option)  # another function's GOT slot
    data, at = setopt_site(build.SETOPT_GOT, 0x00003025, 0x24050000 | option, 0x8fbc0010, 0x0c000000)
    assert not build.stock_tls_site(data, at, option)  # not a jalr $25


def test_the_stock_tls_sites_are_ten_checks_turned_off():
    assert len(set(build.STOCK_TLS_CALLS)) == 10 and {o for _, o in build.STOCK_TLS_CALLS} == {0x40, 0x51}


def test_the_mozilla_bundle_is_the_pinned_one(monkeypatch, tmp_path):
    assert hashlib.sha256(build.mozilla_ca()).hexdigest() == build.STOCK_CA_SHA
    (tmp_path/'assets').mkdir()
    (tmp_path/'assets/mozilla-ca.pem').write_bytes(build.mozilla_ca() + b'\n')
    monkeypatch.setattr(build, 'ROOT', tmp_path)
    with pytest.raises(ValueError, match='Unexpected assets/mozilla-ca.pem'):
        build.mozilla_ca()


def test_tidal_https_takes_a_padding_nul():
    urls = b''.join(b'\0http://api.tidal.com' + p + b'\0\0\0' for p in build.TIDAL_HTTP)
    made = build.tidal_https(urls)
    assert len(made) == len(urls) and b'http://' not in made
    assert made == b''.join(b'\0https://api.tidal.com' + p + b'\0\0' for p in build.TIDAL_HTTP)
    with pytest.raises(ValueError, match='Unexpected Tidal URL'):
        build.tidal_https(urls.replace(build.TIDAL_HTTP[0] + b'\0\0', build.TIDAL_HTTP[0] + b'\0X'))  # no spare NUL after it


def test_adb_wrapper_parks_around_stocks_script_and_keeps_its_status():
    # run under sh by test/adb.py; here its lines
    assert build.adb_wrapper('uac_device_config.sh').decode().splitlines()[3:] == [
        '[ "$1" = start ] && /usr/bin/q2adb-gadget park', '/usr/bin/uac_device_config_stock.sh "$@"', 'r=$?',
        '[ "$1" = stop ] && /usr/bin/q2adb-gadget unpark', 'exit $r']


def test_adb_hook_starts_adbd_after_q2remote_and_its_gadget_first():
    old, new = build.ADB_HOOK
    assert build.REMOTE_HOOK[1].endswith(old) and new.startswith(old)
    line = new[len(old):].decode()
    assert line.index('q2adb-gadget setup') < line.index('/usr/bin/adbd') < line.index('q2adb-gadget bind')
    assert line.endswith(') &\n') and build.USB_SCRIPTS == ('storage_config.sh', 'uac_device_config.sh')


def test_adbd_binary_is_thirdpartys_stripped(tmp_path, monkeypatch):
    import thirdparty
    monkeypatch.setattr(thirdparty, 'build', lambda name: tmp_path/f'stage-{name}')
    def stripped(src, dst):
        assert src == tmp_path/'stage-adbd/usr/bin/adbd'
        dst.write_bytes(b'\x7fELF adbd')
        return dst
    monkeypatch.setattr(thirdparty, 'stripped', stripped)
    assert build.adbd_binary(tmp_path) == b'\x7fELF adbd'
    assert build.adbd_record(tmp_path) == dict(version=thirdparty.SOURCES['adbd'][0],
                                               sha256=hashlib.sha256(b'\x7fELF adbd').hexdigest(), bytes=9)


def test_the_own_bluetoothd_is_thirdpartys_stripped_and_built_for_stocks_abi(tmp_path, monkeypatch):
    import thirdparty
    monkeypatch.setattr(thirdparty, 'build', lambda name: tmp_path/f'stage-{name}')
    def stripped(src, dst):
        assert src == tmp_path/'stage-bluez/usr/bin/bluetoothd'
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(b'\x7fELF own')
        return dst
    monkeypatch.setattr(thirdparty, 'stripped', stripped)
    abi = {b'\x7fELF own': (0x70001405, 'hard'), b'\x7fELF stock': (0x70001405, 'hard'), b'\x7fELF soft': (0x70001005, 'soft')}
    class Elf:
        def __init__(self, path):
            self.flags, self.fp = abi[pathlib.Path(path).read_bytes()]
    monkeypatch.setattr(thirdparty, 'Elf', Elf)
    path, record = build.own_bluetoothd(tmp_path, b'\x7fELF stock')
    # a folder of its own: out/bluetoothd is stock's patched binary, the one demo starts
    assert path == tmp_path/'own-bluez/bluetoothd' and path.read_bytes() == b'\x7fELF own'
    assert record == dict(version=thirdparty.SOURCES['bluez'][0], path='usr/lib/vamp-bluez/bluetoothd',
                          sha256=hashlib.sha256(b'\x7fELF own').hexdigest(), bytes=8)
    assert build.OWN_BLUETOOTHD == 'usr/lib/vamp-bluez/bluetoothd' != build.BLUETOOTHD
    assert build.OWN_BLUETOOTHD.rsplit('/', 1)[1] == 'bluetoothd'  # the name demo's killall ends
    with pytest.raises(ValueError, match='not built for stock'):
        build.own_bluetoothd(tmp_path, b'\x7fELF soft')


def test_a_rootfs_over_72_mib_is_refused_and_one_at_it_fits():
    """The recovery writes mtd2 with no size check, so the build is the bound: 72 MiB, 24 of mtd2's 600 blocks spare."""
    assert build.ROOTFS_ROOM == 75_497_472 == 576 * 128 * 1024
    build.rootfs_fits(build.ROOTFS_ROOM)
    build.rootfs_fits(50_688_001)  # past stock's own image, which was the limit before
    with pytest.raises(ValueError, match=r'^Repacked rootfs is 75,497,473 bytes, 1 over the 75,497,472 mtd2 holds$'):
        build.rootfs_fits(build.ROOTFS_ROOM + 1)


LAUNCHER = ('#!/bin/sh\npid=/tmp/q2-demo\nif [ "$1" != --loop ]; then\n'
            '    dir=$(cd "$(dirname "$0")" && pwd)\n'
            '    cp -f "$dir/q2-demo" /tmp/q2-demo.bin && chmod 755 /tmp/q2-demo.bin || exit 1\n'
            '    exec /bin/sh /tmp/q2-demo.run --loop "$dir"\nfi\n')


def test_a_carried_apps_launcher_keeps_its_data_on_the_card_and_runs_the_image_copy():
    text = build.bundled_launcher('demo', LAUNCHER)
    assert 'dir=/mnt/mmc/.demo; mkdir -p "$dir" || exit 1\n' in text
    assert 'ln -sf /usr/lib/q2apps/demo/q2-demo /tmp/q2-demo.bin || exit 1\n' in text
    assert 'dirname' not in text and 'cp -f' not in text and text.endswith('--loop "$dir"\nfi\n')


@pytest.mark.parametrize('text', [LAUNCHER.replace('dirname', 'basename'), LAUNCHER.replace('cp -f', 'cp'),
                                  LAUNCHER + LAUNCHER])
def test_a_launcher_not_as_expected_is_refused(text):
    with pytest.raises(ValueError, match='Unexpected launcher in the demo app'):
        build.bundled_launcher('demo', text)


def app_zip(files):
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w') as z:
        for name, data in files.items():
            z.writestr(name, data)
    return out.getvalue()


def pinned(monkeypatch, tmp_path, data, version='q2-v1.2', builtin=False):
    (tmp_path/'demo.zip').write_bytes(data)
    monkeypatch.setitem(build.BUNDLED_APPS, 'demo', (version, 'demo.zip', len(data), hashlib.sha256(data).hexdigest(), builtin))


def test_a_carried_app_is_its_pinned_zips_files_with_a_version_and_the_new_launcher(monkeypatch, tmp_path):
    pinned(monkeypatch, tmp_path, app_zip({'.demo/q2app': 'name=Demo\n', '.demo/q2-demo': b'ELF', '.demo/run': LAUNCHER,
                                           '.demo/icon.png': b'png', 'README': 'outside its folder'}))
    files = build.bundled_app('demo', tmp_path)
    assert sorted(files) == ['bundle', 'icon.png', 'q2-demo', 'q2app', 'run', 'version']
    assert files['q2-demo'] == (b'ELF', True) and files['run'][1] and files['q2app'] == (b'name=Demo\n', False)
    assert files['version'] == (b'q2-v1.2\n', False) and b'/usr/lib/q2apps/demo/q2-demo' in files['run'][0]
    digest = build.BUNDLED_APPS['demo'][3]
    assert build.bundle_fields(files['bundle'][0]) == {'zip': 'demo.zip', 'zip_sha256': digest,
                                                       'q2-demo_sha256': hashlib.sha256(b'ELF').hexdigest(), 'build': '-'}
    assert not files['bundle'][1]


def stamped(monkeypatch, tmp_path, name, stamp):
    data = app_zip({'.demo/q2app': '', '.demo/q2-demo': b'ELF', '.demo/run': LAUNCHER, '.demo/version': stamp})
    (tmp_path/name).write_bytes(data)
    monkeypatch.setitem(build.BUNDLED_APPS, 'demo', ('q2-v1.0', name, len(data), hashlib.sha256(data).hexdigest(), True))


def test_a_zips_commit_stamp_is_carried_in_its_bundle_file(monkeypatch, tmp_path):
    stamped(monkeypatch, tmp_path, 'demo-v1.0-7ded418.zip', 'q2-v1.0+7ded418\n')
    files = build.bundled_app('demo', tmp_path)
    assert files['version'][0] == b'q2-v1.0\n'  # addon_version_cmp's tag, as before
    assert build.bundle_fields(files['bundle'][0])['build'] == 'q2-v1.0+7ded418'


@pytest.mark.parametrize('name, stamp, why', [
    ('demo-v1.0-7ded418.zip', 'q2-v1.0+32bd2cd', "says it is 'q2-v1.0\\+32bd2cd'"),  # renamed after the build
    ('demo-v1.0-7ded418-dirty.zip', 'q2-v1.0+7ded418-dirty', 'uncommitted changes'),
    ('demo.zip', 'q2-v1.0+7ded418', 'not q2-v1.0\\+ its name'),  # no commit in its name to check it by
])
def test_a_zip_whose_stamp_is_not_a_clean_commits_is_refused(monkeypatch, tmp_path, name, stamp, why):
    stamped(monkeypatch, tmp_path, name, stamp)
    with pytest.raises(ValueError, match=why):
        build.bundled_app('demo', tmp_path)


def test_a_builtin_app_carries_its_builtin_file_and_no_zip_brings_one(monkeypatch, tmp_path):
    files = {'.demo/q2app': '', '.demo/q2-demo': b'ELF', '.demo/run': LAUNCHER}
    pinned(monkeypatch, tmp_path, app_zip(files), builtin=True)
    assert build.bundled_app('demo', tmp_path)['builtin'] == (b'1\n', False)
    pinned(monkeypatch, tmp_path, app_zip(files))
    assert 'builtin' not in build.bundled_app('demo', tmp_path)
    for extra in ('builtin', 'bundle'):
        pinned(monkeypatch, tmp_path, app_zip({**files, f'.demo/{extra}': ''}))
        with pytest.raises(ValueError, match='Unexpected files in demo.zip'):
            build.bundled_app('demo', tmp_path)


def test_subsonic_is_the_firmwares_alone():
    """The user's, 2026-11-17: Subsonic is not the App Store's; the firmware runs and updates its own."""
    assert build.BUNDLED_APPS['subsonic'][4] is True


def test_a_carried_app_must_be_the_pinned_zip(monkeypatch, tmp_path):
    good = app_zip({'.demo/q2app': '', '.demo/q2-demo': b'ELF', '.demo/run': LAUNCHER})
    with pytest.raises(ValueError, match='Missing .*demo.zip'):
        monkeypatch.setitem(build.BUNDLED_APPS, 'demo', ('q2-v1', 'demo.zip', 1, '0' * 64, False))
        build.bundled_app('demo', tmp_path)
    pinned(monkeypatch, tmp_path, good)
    (tmp_path/'demo.zip').write_bytes(good[:-1] + b'x')  # same size, other bytes
    with pytest.raises(ValueError, match='is not the pinned demo q2-v1.2'):
        build.bundled_app('demo', tmp_path)


@pytest.mark.parametrize('files', [{'.demo/q2app': '', '.demo/run': LAUNCHER},  # no binary
                                   {'.demo/q2app': '', '.demo/q2-demo': b'', '.demo/run': LAUNCHER, '.demo/sub/x': ''}])
def test_a_carried_apps_zip_holds_its_app_and_nothing_nested(monkeypatch, tmp_path, files):
    pinned(monkeypatch, tmp_path, app_zip(files))
    with pytest.raises(ValueError, match='Unexpected files in demo.zip'):
        build.bundled_app('demo', tmp_path)


def test_every_carried_apps_paths_fit_addon_cs_slots():
    for app_id, (version, name, size, digest, builtin) in build.BUNDLED_APPS.items():
        assert len(f'/{build.BUNDLED_DIR}/{app_id}/q2-{app_id}') < 48 and len(f'/bin/sh /{build.BUNDLED_DIR}/{app_id}/run') < 48
        assert len(f'file:///{build.BUNDLED_DIR}/{app_id}/icon-40.png') < 56 and len(version) < 24
        assert re.fullmatch(r'[a-z0-9-]{1,15}', app_id) and size > 0 and re.fullmatch('[0-9a-f]{64}', digest)
        assert isinstance(builtin, bool)


def stock_opens(monkeypatch, calls=None, reads=None):
    """check_keyboard_opens over stock_window_opens' answer for V1.36's demo, as test/build.py finds it on the stock
    image, with calls and reads changed."""
    known = {**{a: None for a in build.WINDOW_OPENS}, 0x665374: 'edit_menu', 0x69fb5c: None, 0x69fbd0: 'kb_default_t9'}
    found = {**dict(build.KB_NAME_READS), 0x69f9d8: 'kb_default_t9'}
    monkeypatch.setattr(build, 'stock_window_opens', lambda demo: ({**known, **(calls or {})}, {**found, **(reads or {})}))
    return lambda: build.check_keyboard_opens(b'')


def test_keyboard_opens_pass_on_stock_s_calls(monkeypatch):
    assert set(stock_opens(monkeypatch)()) >= {a for a, _ in build.KB_OPEN_SITES}


@pytest.mark.parametrize('calls, reads, said', [
    ({0x500000: 'some_page'}, None, 'WINDOW_OPENS does not know'),  # a new call to window_open
    ({0x4f5390: 'kb_default_t9'}, None, 'opens the keyboard kb_default_t9'),  # a known one handed a keyboard's name
    ({0x69fc00: None}, None, 'input_method_default calls window_open'),  # a third open in the input method
    (None, {0x480000: 'kb_hex'}, 'code names a keyboard'),  # a keyboard's name read anywhere new
])
def test_keyboard_opens_stop_the_build_on_a_caller_not_redirected(monkeypatch, calls, reads, said):
    check = stock_opens(monkeypatch, calls, reads)
    with pytest.raises(ValueError, match=said):
        check()


# stock_window_opens over a small ELF of the shape stock's demo has: .text, .rodata, .got and the section names, the
# code position-independent as stock's (each address a GOT load, then an addiu), so every way a call or a name is found
# runs here, not only on the stock image (test/build.py).
def mips_elf(code, rodata, got, text_a=0x30000, ro_a=0x10000, got_a=0x20000):
    """An ELF32 image: 16 nops, code (words), 48 nops in .text; rodata (bytes); got (words). (image, text address)."""
    text = struct.pack(f'<{16 + len(code) + 48}I', *([0] * 16 + code + [0] * 48))
    gotb = struct.pack(f'<{len(got)}I', *got)
    names = b'\0.text\0.rodata\0.got\0.shstrtab\0'
    body = [text, rodata, gotb, names]
    offs, at = [], 0x34
    for b in body:
        offs.append(at)
        at += len(b)
    shoff = at
    heads = [(0, 0, 0, 0, 0, 0)] + [(names.index(n), 1, 0, a, o, len(b)) for n, a, o, b in zip(
        (b'.text', b'.rodata', b'.got', b'.shstrtab'), (text_a, ro_a, got_a, 0), offs, body)]
    header = bytearray(0x34)
    struct.pack_into('<I', header, 0x20, shoff)
    struct.pack_into('<HHH', header, 0x2e, 40, len(heads), len(heads) - 1)
    sections = b''.join(struct.pack('<IIIIII', *h) + bytes(16) for h in heads)
    return bytes(header) + b''.join(body) + sections, text_a


def lw(rt, slot, rs=28): return 0x23 << 26 | rs << 21 | rt << 16 | (-0x7ff0 + 4 * slot) & 0xffff
def addiu(rt, rs, imm): return 0x09 << 26 | rs << 21 | rt << 16 | imm & 0xffff
def orr(rd, rs, rt=0): return rs << 21 | rt << 16 | rd << 11 | 0x25
def lui(rt, imm): return 0x0f << 26 | rt << 16 | imm


JALR = 0x0320f809


def test_stock_window_opens_finds_each_call_and_the_name_it_passes_and_each_keyboard_name_read():
    rodata = b'\0kb_hex\0page_one\0' + b'x' * 70 + b'\0'
    ro_a, opener = 0x10000, 0x30800
    hexname, page, long = ro_a + 1, ro_a + 8, ro_a + 17
    got = [opener, ro_a, hexname, 0x99999999]  # slots 0 window_open, 1 .rodata, 2 kb_hex itself, 3 nowhere mapped
    base = 0x30000 + 16 * 4
    code: list = []

    def at(): return base + 4 * len(code)

    BAL, B = object(), object()  # stand-ins for a bal or a b to the opener, placed where they land

    def emit(*words):
        """The words at the end of the code, a stand-in turned into its branch there; the call's address."""
        for w in words:
            if w is BAL or w is B:
                w = (0x0411 if w is BAL else 0x1000) << 16 | (opener - at() - 4) >> 2 & 0xffff
            code.append(w)
        return at() - 8  # the call before the trailing delay slot
    jalr_page = emit(lw(4, 1), addiu(4, 4, page - ro_a), lw(25, 0), JALR, 0)  # a jalr of the GOT slot: page_one
    bal_hex = emit(lw(5, 1), orr(4, 5), addiu(4, 4, hexname - ro_a), BAL, 0)  # moved, then offset: kb_hex, by bal
    emit(lw(6, 2), lw(7, 1), addiu(7, 7, hexname - ro_a))  # kb_hex read: the slot itself, and slot plus addiu
    read_slot, read_addiu = at() - 12, at() - 4
    b_call = emit(lui(4, 1), B, 0)  # b, a tail call, $4 from a lui: unknown
    jalr_nowhere = emit(lw(4, 3), lw(25, 0), JALR, 0)  # an address in no section: unknown
    bal_long = emit(lw(4, 1), addiu(4, 4, long - ro_a), BAL, 0)  # a string too long for a name: unknown
    bal_or = emit(lw(4, 1), orr(4, 5, 6), BAL, 0)  # or of two registers: unknown
    bal_addiu = emit(addiu(4, 9, 8), BAL, 0)  # addiu from a register nothing set: unknown
    emit(lw(25, 0), lw(25, 1), JALR, 0)  # $25 loaded again before the jalr: not the opener's
    bal_after = emit(lw(25, 0), BAL, 0)  # a bal after the load: the bal is the call, counted once
    jalr_later = emit(lw(4, 1), addiu(4, 4, page - ro_a), lw(25, 0), 0, JALR, 0)  # the jalr a word on: still its
    emit(lw(25, 0))  # the last load, no jalr within its reach (the trailing nops): no call
    demo, _ = mips_elf(code, rodata, got)
    calls, reads = build.stock_window_opens(demo, (opener,))
    assert calls == {jalr_page: 'page_one', bal_hex: 'kb_hex', b_call: None, jalr_nowhere: None, bal_long: None,
                     bal_or: None, bal_addiu: None, bal_after: None, jalr_later: 'page_one'}
    assert reads == {read_slot: 'kb_hex', read_addiu: 'kb_hex'}  # bal_hex's addiu builds on $4, not on the load's $5


def test_the_font_memo_follows_only_the_code_its_function_names():
    """memo's key (memo_source): an edit elsewhere in tools/build.py keeps the fonts' cache, as a new hook or a new
    BUNDLED_APPS pin should; an edit of subset_font, of a helper it calls or of a constant either names does not."""
    text = pathlib.Path(build.__file__).read_text()
    key = build.memo_source(build.subset_font, text)
    assert 'def play_mark' in key and 'PLAY_MARK' in key and 'TABULAR' in key and 'BUNDLED_APPS' not in key
    assert build.memo_source(build.subset_font, text + '\n# a comment\nOTHER = 1\n') == key
    assert build.memo_source(build.subset_font, text.replace('PLAY_MARK = 0x25b6', 'PLAY_MARK = 0x25b7')) != key
    helper = text.index('def play_mark')
    assert build.memo_source(build.subset_font, text[:helper] + 'def play_mark(font):\n    pass\n\ndef was_play_mark' + text[helper + 13:]) != key


def test_every_hook_is_where_a_record_not_made_from_hooks_says():
    """HOOKS' 18 hooks on functions Shanling left unnamed were checked against PRIVATE_FUNCTIONS, itself made from
    HOOKS: keylock_rows moved to tidal_favourite_request's start built (2026-11-22). stock_map.tsv names them."""
    mapped = build.stock_map()
    private = [n for n in build.HOOKS if build.PRIVATE_FUNCTIONS.get(n) == build.HOOKS[n][0]]
    assert len(private) == 18 and all(mapped[n] == build.HOOKS[n][0] for n in private)
    stock = {n: a for n, (a, _, _) in build.HOOKS.items() if n not in private}  # as stock's symbol table gives them
    build.hook_address_check(build.HOOKS, stock, mapped)
    moved = {**build.HOOKS, 'keylock_rows': (mapped['tidal_favourite_request'], 'ringnav_keylock_rows', None)}
    with pytest.raises(ValueError, match=r'keylock_rows: HOOKS has 0x484514, but keylock_rows is at 0x4c9dd8 \(tools/stock_map.tsv\)'):
        build.hook_address_check(moved, stock, mapped)
    with pytest.raises(ValueError, match='in neither'):
        build.hook_address_check({'nowhere': (1, 'x', None)}, {}, mapped)


def test_own_bluealsa_files_are_thirdpartys_stripped_under_names_of_their_own(tmp_path, monkeypatch):
    import thirdparty
    monkeypatch.setattr(thirdparty, 'build', lambda name: tmp_path/f'stage-{name}')
    def stripped(src, dst):
        assert src.is_relative_to(tmp_path/'stage-bluealsa') and dst.parent == tmp_path/'own-bluealsa'
        dst.parent.mkdir(exist_ok=True)
        dst.write_bytes(b'\x7fELF ' + src.name.encode())
        return dst
    monkeypatch.setattr(thirdparty, 'stripped', stripped)
    files = build.own_bluealsa_files(tmp_path)
    assert files == {'usr/lib/vamp-bluealsa/bluealsad': (build.HELPER_LIKE, b'\x7fELF bluealsad'),
                     'usr/lib/vamp-bluealsa/bluealsa-aplay.own': (build.HELPER_LIKE, b'\x7fELF bluealsa-aplay'),
                     'usr/lib/vamp-bluealsa/libasound_module_pcm_bluealsa.own.so': (build.HELPER_LIKE, b'\x7fELF libasound_module_pcm_bluealsa.so'),
                     'usr/lib/vamp-bluealsa/libasound_module_ctl_bluealsa.own.so': (build.HELPER_LIKE, b'\x7fELF libasound_module_ctl_bluealsa.so')}
    # the build keeps each added file in its output folder by base name: none may be stock's bluealsa's or bluealsa-aplay's
    names = [p.rsplit('/', 1)[1] for p in files]
    assert len(set(names)) == 4 and not {'bluealsa', 'bluealsa-aplay', 'bluealsa2'} & set(names)
    assert build.own_bluealsa_record(files) == dict(
        version=thirdparty.SOURCES['bluealsa'][0],
        files={p: dict(sha256=hashlib.sha256(d).hexdigest(), bytes=len(d)) for p, (_, d) in files.items()})
