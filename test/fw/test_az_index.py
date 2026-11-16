"""az_index.c: the A-Z index on a long Library list in name order under OLED: hidden at rest, brought up by a
finger on the list's right edge (which jumps the list to the letter under it) or a fast spin of the wheel, and
gone a second after either ends."""
import ctypes as C
import re
import struct
from typing import Any

import pytest

from conftest import O, Deques, ROOT
from test_nav_lists import ROW_H, SLOW, STOP, Canvas, Pull, Table, key, ui  # noqa: F401  (ui: fixture)

# its own constants, and the three navigation.h shares with add_songs.c
SOURCE = (ROOT / 'patch' / 'az_index.c').read_text() + (ROOT / 'patch' / 'navigation.h').read_text()
AZ = {k: int(v) for k, v in re.findall(r'^#define (AZ_\w+) (\d+)\b', SOURCE, re.M)}
LETTERS = '#ABCDEFGHIJKLMNOPQRSTUVWXYZ'
EVT_MOVE, EVT_UP = O['EVT_POINTER_MOVE_BEFORE'], O['EVT_POINTER_UP_BEFORE']
KEEP = []  # the records' buffers, alive for the test


@pytest.fixture
def trim(fw):
    fw.mock('toolsTrimLeft', lambda s, *_: C.memmove(s, C.string_at(s).lstrip() + b'\0', len(C.string_at(s).lstrip()) + 1))


@pytest.mark.parametrize('name, letter', [
    ('Beatles', 'B'), ('beatles', 'B'), ('The Beatles', 'B'), ('An Elephant', 'E'), ('  zed', 'Z'),
    ('2Pac', '#'), ('(What)', '#'), ('Éclair', '#'), ('', '#'), ('Theory', 'T'),
])
def test_a_name_files_under_its_first_letter_past_its_article(fw, trim, name, letter):
    assert LETTERS[fw.fn('az_initial', C.c_int, C.c_char_p)(name.encode())] == letter


def test_the_finger_maps_evenly_over_all_27_letters_from_y_80_to_240(fw):
    at = fw.fn('az_letter_at', C.c_int, C.c_int)
    rows = [at(y) for y in range(AZ['AZ_TOP'], AZ['AZ_BOTTOM'])]
    assert rows == sorted(rows) and set(rows) == set(range(27))
    assert all(5 <= rows.count(k) <= 6 for k in range(27))  # 160 rows over 27 letters
    assert at(0) == 0 and at(AZ['AZ_TOP'] - 1) == 0 and at(AZ['AZ_BOTTOM']) == 26 and at(320) == 26


def test_27_letters_in_11_px_slots_fold_into_seven_with_dots_between(fw):
    out = (C.c_byte * 27)()
    n = fw.fn('az_column', C.c_int, C.c_void_p)(out)
    assert ''.join('.' if c < 0 else LETTERS[c] for c in out[:n]) == '#.D.I.M.Q.V.Z'  # the mock-up's, as iOS folds


@pytest.mark.parametrize('letter, row', [('A', 0), ('B', 3), ('C', 3), ('D', 3), ('E', 7), ('Z', 7), ('#', 0)])
def test_a_letter_with_no_rows_jumps_to_the_next_that_has_some_else_the_last_before(fw, letter, row):
    first = (C.c_int * 27)(*[-1] * 27)
    first[1], first[4], first[5] = 0, 3, 7  # A, D, E
    assert fw.fn('az_target', C.c_int, C.c_void_p, C.c_int)(first, LETTERS.index(letter)) == row


def songs(fw, names):
    """p_deque_showlist as Songs' records, each its title."""
    keep, recs = [], []
    for name in names:
        s, r = C.create_string_buffer(name.encode()), C.create_string_buffer(O['REC_SIZE'] + 16)
        struct.pack_into('<Q', r, O['REC_TITLE'], C.addressof(s))
        struct.pack_into('<i', r, O['REC_TYPE'], 8)
        keep += [s, r]
        recs.append(C.addressof(r))
    deques = Deques(fw)
    fw.var('fw_ctx_p_deque_showlist', C.c_uint).value = deques.new(recs)
    KEEP.extend(keep)


def library(n=78):
    """n titles, three a letter from A, A to Z in order."""
    return [f'{chr(65 + i // 3)} song {i}' for i in range(n)]


def defaults(fw):
    """config.ini with no keys: every setting its default."""
    def read(path, section, name, out, default, *_):
        C.memmove(out, C.string_at(default) + b'\0', len(C.string_at(default)) + 1)
    fw.mock('toolsReadConfig', read)


def touchable(fw, ui, n=78, name='allmusic_page', cls=0xf001, sort=0):
    defaults(fw)
    fw.mock('toolsTrimLeft', lambda *_: None)
    fw.var('fw_ctx_g_class_type', C.c_int).value = cls
    fw.var('fw_ctx_g_sort_flag', C.c_int).value = sort
    table = Table(fw, ui, n, name=name)
    songs(fw, library(n))
    return table, Pull(fw, ui, table)


def letter_y(letter):
    """Screen row of letter's middle on the strip."""
    k = LETTERS.index(letter)
    return round(AZ['AZ_TOP'] + (AZ['AZ_BOTTOM'] - AZ['AZ_TOP']) * (2 * k + 1) / 54)


def az(fw):
    return {k: fw.fn(f'az_{k}', C.c_int, C.c_void_p) for k in ('on', 'right', 'bar_in')}


def test_a_finger_on_the_right_edge_jumps_to_the_letter_and_holds_the_touch(fw, ui):
    table, pull = touchable(fw, ui)
    assert pull.down(370, letter_y('M')) == 0
    m = 12 * 3  # M's first row
    assert table.selected == m and table.client.int(O['TABLE_TOP']) == m * ROW_H
    assert pull.aborts == [(table.win.at, O['EVT_POINTER_ABORT'])]  # the rows it pressed let go
    f = az(fw)
    assert f['on'](table.client.at) and f['right'](table.client.at) == f['right'](0) + AZ['AZ_RIGHT_IN']
    assert f['bar_in'](table.client.at) == AZ['AZ_BAR_IN']
    pull.x = 370
    assert pull.send(EVT_MOVE, 0, letter_y('B') - pull.y) == STOP  # the list neither scrolls nor taps
    assert table.selected == 3
    assert pull.send(EVT_MOVE, 0, 320 - pull.y) == STOP  # past the end: Z, as far down as the list goes
    assert table.selected == 75 and table.client.int(O['TABLE_TOP']) == 78 * ROW_H - table.client.box[3]
    assert pull.send(EVT_UP, 0, 0) == STOP
    (fade,) = ui.due('az_due')
    assert ui.timers[fade][2] == AZ['AZ_SHOW_MS'] - AZ['AZ_FADE_MS']
    ui.now += AZ['AZ_SHOW_MS'] - AZ['AZ_FADE_MS']
    ui.fire(fade)
    assert f['on'](table.client.at)  # fading
    ui.now += AZ['AZ_FADE_MS']
    ui.invalidated.clear()
    ui.fire(ui.due('az_due')[0])
    assert not f['on'](table.client.at) and ui.invalidated == [table.win.at] and not ui.due('az_due')


def test_a_press_after_a_missed_lift_ends_the_hold(fw, ui):
    table, pull = touchable(fw, ui)
    pull.down(370, letter_y('M'))
    assert fw.fn('az_held', C.c_int, C.c_void_p)(None)  # held: moves are the strip's
    pull.down(100, 150)  # the lift went elsewhere; a press on the rows
    assert not fw.fn('az_held', C.c_int, C.c_void_p)(None)
    (fade,) = ui.due('az_due')
    assert ui.timers[fade][2] == AZ['AZ_SHOW_MS'] - AZ['AZ_FADE_MS']


@pytest.mark.parametrize('case', ['short', 'by time added', 'artists songs', 'mid list', 'settings'])
def test_no_index_on_a_short_or_unsorted_list_or_away_from_the_edge(fw, ui, case):
    cases: dict[str, dict[str, Any]] = {'short': dict(n=50), 'by time added': dict(sort=3),
                                        'artists songs': dict(cls=0xff07), 'settings': dict(name='sysset_page')}
    args = cases.get(case, {})
    table, pull = touchable(fw, ui, **args)
    pull.down(200 if case == 'mid list' else 370, letter_y('M'))
    assert table.selected == -1 and not pull.aborts and not az(fw)['on'](table.client.at)


def test_a_fast_spin_brings_the_strip_up_with_the_rows_letter_and_a_slow_one_does_not(fw, ui):
    table, _ = touchable(fw, ui)
    speed = fw.var('wheel_speed', C.c_int)
    speed.value = AZ['AZ_SPIN_DPS'] - 1
    for _ in range(5):
        key(fw, ui, O['KEY_NEXT'], ui.now + SLOW)
    assert not az(fw)['on'](table.client.at)
    speed.value = AZ['AZ_SPIN_DPS']
    key(fw, ui, O['KEY_NEXT'], ui.now + SLOW)
    assert az(fw)['on'](table.client.at)
    canvas = Canvas(fw)
    fw.fn('az_paint', None, C.c_void_p, C.c_void_p)(table.win.at, canvas.at)
    texts = [t for t, _, _ in canvas.boxes]
    assert texts[:13] == list('#•D•I•M•Q•V•Z') and texts[13:] == ['C', 'C']  # row 6: C, lit and in the bubble
    (_, (x, y, w, h), _), = [b for b in canvas.boxes[13:14]]
    assert x + w // 2 == 375 - AZ['AZ_X'] and y + h // 2 == letter_y('C')
    _, (_, top, _, h), _ = canvas.boxes[0]
    assert top + h // 2 == AZ['AZ_TOP'] + (AZ['AZ_BOTTOM'] - AZ['AZ_TOP'] + 13) // 26  # '#', the first of 13 slots
    struct.pack_into('<i', canvas.block, O['CANVAS_Y'], 30)  # a canvas whose origin is 30 rows down the screen
    canvas.boxes.clear()
    fw.fn('az_paint', None, C.c_void_p, C.c_void_p)(table.win.at, canvas.at)
    assert canvas.boxes[0][1][1] == top - 30  # drawn at the same place on screen
    speed.value = 0  # slowing: the letter keeps up, the fade is not put off
    (fade,) = ui.due('az_due')
    for _ in range(3):
        key(fw, ui, O['KEY_NEXT'], ui.now + SLOW)
    assert ui.due('az_due') == [fade]
    canvas = Canvas(fw)
    fw.fn('az_paint', None, C.c_void_p, C.c_void_p)(table.win.at, canvas.at)
    assert [t for t, _, _ in canvas.boxes][13:] == ['D']  # no bubble once slow


def test_the_hidden_strip_paints_nothing_and_hides_no_scroll_indicator(fw, ui):
    table, _ = touchable(fw, ui)
    canvas = Canvas(fw)
    fw.fn('az_paint', None, C.c_void_p, C.c_void_p)(table.win.at, canvas.at)
    assert not canvas.boxes and not canvas.fills and not canvas.rounds
    f = az(fw)
    assert not f['on'](table.client.at) and f['bar_in'](table.client.at) == 0
