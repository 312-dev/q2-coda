"""tools/game_art.py: Sudoku's masks, packed for eng_glyph. The design's fonts are not in the repository, so the
drawing is tested on Inter (assets/fonts) for all four faces; with Q2_GAME_FONTS naming the folder that holds them,
the committed patch/game_sudoku_art.inc is checked against them."""
import functools
import hashlib
import os
import pathlib
import random
import re
import shutil
import sys

import pytest

ROOT = pathlib.Path(__file__).parents[2]
sys.path.insert(0, str(ROOT/'tools'))
import game_art
from fontTools.ttLib import TTFont

INTER = ROOT/'assets/fonts/Inter-Medium.ttf'


@functools.cache
def inter():
    return TTFont(INTER)


@functools.cache
def inter_sets():
    f = inter()
    return game_art.sets({'hand': f, 'serif': f, 'display': f, 'type': f})


@pytest.mark.parametrize('seed', range(6))
def test_pack_then_unpack_gives_the_mask_back_to_a_fifteenth(seed):
    rnd = random.Random(seed)
    w, h = rnd.randint(1, 40), rnd.randint(1, 30)
    # runs of clear and solid, long and short, with edges between
    alpha = [rnd.choice([0] * 6 + [255] * 6 + [rnd.randrange(256)]) for _ in range(w * h)]
    data = game_art.pack(w, h, bytes(alpha))
    assert data[:2] == bytes((w, h))
    assert game_art.unpack(data) == (w, h, [round(a * 15 / 255) for a in alpha])


def test_a_run_is_at_most_sixteen_and_an_edge_one_nibble():
    data = game_art.pack(40, 1, bytes([0] * 40))
    assert data[2:] == bytes((0x0f, 0x0f, 0x07))  # 16, 16 and 8 clear
    assert game_art.pack(3, 1, bytes((136, 255, 0)))[2:] == bytes((0x8f, 0x00, 0x00))  # an 8, a run of 1 solid, 1 clear
    assert game_art.pack(1, 1, bytes((119,)))[2:] == bytes((0x70,))  # a lone edge, padded
    with pytest.raises(AssertionError, match='packed whole'):
        game_art.unpack(bytes((2, 1, 0x0f)))


def test_a_glyph_is_its_ink_placed_from_the_pen_on_the_baseline():
    x, y, adv, data = game_art.glyph(inter(), 'H', 20)
    w, h, a = game_art.unpack(data)
    assert (x, y, adv, w, h) == (1, -15, 15, 13, 15)  # Inter's H at 20 px: a pixel's bearing, 15 tall on the baseline
    assert a[0] < 15 and a[13 * 7 + 3] == 15  # the stem's left edge is partly covered, its middle solid
    thick = game_art.unpack(game_art.glyph(inter(), 'H', 20, None, 1.5)[3])
    assert thick[0] > w and sum(thick[2]) > sum(a)  # thickened by a pen's stroke round it


def test_turn_turns_anticlockwise_on_the_screen():
    (pt,), = game_art.turn([[(10, 0)]], 90, 0, 0)
    assert pt == pytest.approx((0, -10))


def test_a_picture_is_placed_at_its_own_top_left():
    x, y, adv, data = game_art.picture([], [[(0, 0), (10, 0)]], 2)
    w, h, a = game_art.unpack(data)
    assert (x, y, adv) == (0, 0, 0) and (w, h) == (12, 2) and a[1] == 15


def test_the_sets_are_in_the_order_and_sizes_game_sudoku_reads():
    groups, frame = inter_sets()
    names = [n for n, _ in groups]
    assert names == ['A_PRINT', 'A_PEN', 'A_DIAL', 'A_HAND', 'A_TALLY', 'A_CAPS', 'A_NAME', 'A_TITLE', 'A_HEAD', 'A_STAMP']
    size = {n: len(g) for n, g in groups}
    assert size == {'A_PRINT': 9, 'A_PEN': 11, 'A_DIAL': 9, 'A_HAND': len(game_art.HAND), 'A_TALLY': 9,
                    'A_CAPS': len(game_art.CAPS), 'A_NAME': 3, 'A_TITLE': 1, 'A_HEAD': 1, 'A_STAMP': 1}
    head = game_art.unpack(dict(groups)['A_HEAD'][0][3])
    assert head[1] > 5 * head[0]  # set sideways
    assert len(frame) == 16 and frame[2] > frame[0] and frame[1] > frame[3]  # its top edge rises to the right


def test_source_lists_every_mask_with_its_offset():
    groups, frame = inter_sets()
    f = inter()
    text = game_art.source({'hand': f, 'serif': f, 'display': f, 'type': f})
    found = re.search(r'art_at\[A_COUNT\] = \{ ([^}]*) \}', text)
    assert found
    at = [int(v) for v in found.group(1).split(', ')]
    data = [int(v) for v in re.findall(r'\d+', text[text.index('art['):].split('= {', 1)[1])]
    assert len(at) == sum(len(g) for _, g in groups) and at[0] == 0
    first = groups[0][1][0]
    assert bytes(data[:3 + len(first[3])]) == bytes((first[0] & 255, first[1] & 255, first[2])) + first[3]
    assert f'#define A_HAND_SET "{game_art.HAND}"' in text and 'A_STAMP = ' in text
    assert re.search(r'art_frame\[16\] = \{ ' + ', '.join(map(str, frame)) + ' \\}', text)


def test_load_reads_the_fonts_it_was_drawn_from_and_no_other(tmp_path, monkeypatch):
    digest = hashlib.sha256(INTER.read_bytes()).hexdigest()
    monkeypatch.setattr(game_art, 'FONTS', {'hand': ('a.ttf', digest), 'serif': ('b.ttf', digest)})
    for n in 'ab':
        shutil.copy(INTER, tmp_path/f'{n}.ttf')
    assert set(game_art.load(tmp_path)) == {'hand', 'serif'}
    (tmp_path/'b.ttf').write_bytes(INTER.read_bytes()[:-1] + b'x')
    with pytest.raises(AssertionError, match='b.ttf is not the font'):
        game_art.load(tmp_path)


@pytest.mark.skipif(not os.environ.get('Q2_GAME_FONTS'), reason='Q2_GAME_FONTS: the folder of the design fonts')
def test_the_committed_masks_are_the_fonts_drawing():
    assert (ROOT/'patch/game_sudoku_art.inc').read_text() == game_art.source(game_art.load(os.environ['Q2_GAME_FONTS']))
