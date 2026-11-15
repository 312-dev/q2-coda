"""tools/art.py: the theme images it draws, from the shipped theme's values."""
import functools
import math
import pathlib
import re
import struct
import sys
import zlib

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).parents[2]/'tools'))
import art
import ipod
import theme


@functools.cache
def classic():
    """Classic's images without the pane's icons, which take most of the drawing time. Every art.py
    mutant runs these tests, so only the two that read the icons draw them, and the rest share one
    drawing a session (conftest's uncached clears it for each mutant). Read it, don't change it."""
    return art.images({**theme.builtins()[1], 'pane_icon_x': 0})


def pixels(data):
    w, h = struct.unpack('>II', data[16:24])
    idat = data.index(b'IDAT')
    raw = zlib.decompress(data[idat + 4:idat + 4 + struct.unpack('>I', data[idat - 4:idat])[0]])
    return w, h, lambda x, y: tuple(raw[y * (4 * w + 1) + 1 + 4 * x + i] for i in range(4))


# Stock's quick settings images, every drop_ name its demo sets (strings demo | grep '^drop_').
QS_LIT = {'wifiopen', 'btopen', 'keylockopen', 'highgain', 'lo', 'usbaudio', 'usbdac'}
QS_INKS = lambda n: ('', '_l') if n in QS_LIT else ('_l', '_d')  # on: white, or dark on a bright accent; off: the light and the dark half's
QS_NAMES = ('wifi', 'wifiopen', 'bt', 'btopen', 'keylock', 'keylockopen', 'lowgain', 'lowgaindisable', 'highgain', 'highgaindisable', 'lo', 'po', 'sa', 'usbstorage', 'usbaudio', 'usbdac', 'playset', 'sysset')


def test_classic_draws_its_images_and_the_others_none():
    themes = theme.builtins()
    assert not art.images(themes[0]) and not art.images(themes[2])
    # the bar's speakers come with a track stated in one half alone
    for half in ({'np_track': themes[1]['np_track']}, {'light': {'np_track': themes[1]['light']['np_track']}}):
        assert set(art.images({**themes[0], **half})) == {'minimal_vol_lo', 'minimal_vol_hi'}
    full = art.images(themes[1])
    assert {k: v for k, v in full.items() if '_icon_' not in k} == classic()  # what the other tests draw
    assert set(full) == {f'classic_light_{n}' for n in ('sort_bars', 'icon_note', 'icon_folder', 'icon_stack', 'icon_game', 'icon_wave', 'icon_gear', 'chevron', 'bolt', 'plug', 'note', 'play_l', 'play_d', 'pause_l', 'pause_d', 'vol_lo', 'vol_hi', *(f'spk_{v}_{h}' for v in ('quiet', 'loud') for h in 'ldw'), *(f'drop_{n}{k}' for n in QS_NAMES for k in QS_INKS(n)), 'drop_track', 'drop_knob', 'sun_small', 'sun_big', 'play_w', 'pause_w', 'orb_note', 'orb_usb', 'tray_108', 'tray_165', 'tray_164', 'cell_45', 'cell_60', 'cell_lit_45', 'cell_lit_60', 'tile', 'clock_chrome', 'clock_shadow', 'kb_del', 'kb_shift', 'kb_back', 'field_glass', 'field', 'band_shadow', 'panel_shadow', 'rate_shadow', 'tray_shadow', 'confirm_shadow', 'track_inset', 'lit_edge', 'band_top', 'band_foot', 'heart_on_l', 'heart_on_d', 'heart_off_l', 'heart_off_d', 'station')}


# The approved rendered rows, within 1 per channel: the fill down column 272 of the pause bars
# (the gradient's stops sampled at pixel centres), the edge down column 270.
REF_FILL = {4: 0x114c8f, 5: 0x1462a3, 6: 0x1870ae, 7: 0x1f83bf, 8: 0x299cd6, 9: 0x31b0e7, 10: 0x36bff2, 11: 0x38c7f6, 12: 0x35bbef,
              13: 0x2ca6e0, 14: 0x238fcd, 15: 0x1e7cba, 16: 0x196dab, 17: 0x1563a2, 18: 0x135999}
REF_EDGE = {5: 0x114d90, 6: 0x114e91, 7: 0x114f91, 8: 0x125092, 9: 0x125193, 10: 0x125293, 11: 0x125294, 12: 0x125395, 13: 0x125495,
              14: 0x125596, 15: 0x135697, 16: 0x135797, 17: 0x135898}


def test_the_play_state_stands_on_its_approved_rows():
    got = classic()
    w, h, at = pixels(got['classic_light_pause_l'])
    x0, y0 = art.STATE_BOX[:2]
    assert (w, h) == (ipod.STATE_W, 22)  # the bar's box and the art's are one number
    near = lambda p, rgb: p[3] == 255 and all(abs(p[i] - (rgb >> s & 255)) <= 1 for i, s in enumerate((16, 8, 0)))
    for y, rgb in REF_FILL.items():
        assert near(at(272 - x0, y - y0), rgb), y
    for y, rgb in REF_EDGE.items():
        assert near(at(270 - x0, y - y0), rgb), y
    # its extents: the bars' rows and columns, nothing past them; no halo, as an iPod's
    ink = {(x, y) for x in range(w) for y in range(h) if at(x, y)[3]}
    assert {y for _, y in ink} == set(range(4, 19)) and {x for x, _ in ink} == {*range(270 - x0, 276 - x0), *range(277 - x0, 283 - x0)}
    assert at(276 - x0, 11 - y0)[3] == 0  # between the bars: clear
    assert got['classic_light_play_l'] == got['classic_light_play_d']


def test_the_bolt_and_the_plug_fill_the_batterys_inside():
    t = theme.builtins()[1]
    got = classic()
    x0, y0 = art.BOLT_BOX[:2]
    g = theme.sets(t)['classic-light']
    for name in ('classic_light_bolt', 'classic_light_plug'):
        assert pixels(got[name])[:2] == tuple(art.BOLT_BOX[2:]) == (g.batt_w - 2, g.batt_h - 2) == (24, 11)  # the inside, BOLT_BOX's frame: x 290 to 313, rows 5 to 15
    at = pixels(got['classic_light_bolt'])[2]
    assert at(301 - x0, 10 - y0) == (0x3c, 0x4a, 0x34, 255)  # its colour, opaque
    assert t['bolt'] == 0x3c4a34 and t['bolt_halo_a'] == 102
    halo = at(304 - x0, 10 - y0)  # past the bolt's edge: white, no stronger than its alpha
    assert halo[:3] == (255, 255, 255) and 0 < halo[3] <= 102
    assert at(290 - x0, 10 - y0)[3] == 0 and at(313 - x0, 10 - y0)[3] == 0
    plug = pixels(got['classic_light_plug'])[2]
    assert plug(300 - x0, 10 - y0) == (0x3c, 0x4a, 0x34, 255) and plug(290 - x0, 11 - y0)[3] == 0


def test_the_volume_and_slider_page_images():
    got = classic()
    size = {'spk_quiet': (12, 14), 'spk_loud': (22, 14), 'sun_small': (16, 16), 'sun_big': (22, 22), 'play_w': (10, 11), 'pause_w': (10, 11), 'orb_note': (116, 116), 'orb_usb': (130, 130)}
    for name, wh in size.items():
        for n in ([f'{name}_{h}' for h in 'ldw'] if name.startswith('spk') else [name]):
            w, h, at = pixels(got[f'classic_light_{n}'])
            assert (w, h) == wh
            assert not all(at(x, y)[3] == 255 for x in (0, w - 1) for y in (0, h - 1))
    inks = {'l': (0x39, 0x3c, 0x39), 'd': (0xc6, 0xc3, 0xc6), 'w': (255, 255, 255)}
    for half, rgb in inks.items():
        for n in ('quiet', 'loud'):
            w, h, at = pixels(got[f'classic_light_spk_{n}_{half}'])
            assert at(5, 7) == (*rgb, 255) and {at(x, y)[:3] for x in range(w) for y in range(h) if at(x, y)[3]} == {rgb}  # the cone; no other colour
            assert any(at(x, y)[3] for x in range(11, w) for y in range(h)) == (n == 'loud')  # only loud has arcs
    for n in ('sun_small', 'sun_big', 'play_w', 'pause_w'):
        w, h, at = pixels(got[f'classic_light_{n}'])
        assert {at(x, y)[:3] for x in range(w) for y in range(h) if at(x, y)[3]} == {(255, 255, 255)}
    assert pixels(got['classic_light_sun_big'])[2](11, 11)[3] == 255 and pixels(got['classic_light_sun_small'])[2](8, 8)[3] == 255
    for n, side in (('sun_small', 12), ('sun_big', 22)):  # the ink's bounding box, a quarter's alpha or more
        w, h, at = pixels(got[f'classic_light_{n}'])
        inked = [(x, y) for x in range(w) for y in range(h) if at(x, y)[3] >= 64]
        assert max(p[0] for p in inked) - min(p[0] for p in inked) + 1 == side and max(p[1] for p in inked) - min(p[1] for p in inked) + 1 == side, n
    assert pixels(got['classic_light_play_w'])[2](3, 5)[3] == 255 and pixels(got['classic_light_pause_w'])[2](5, 5)[3] < 64


def test_the_confirm_shadow_falls_soft_and_black_under_the_tray():
    got = classic()['classic_light_confirm_shadow']
    w, h, at = pixels(got)
    assert (w, h) == (315, 68) and all(at(x, y)[:3] == (0, 0, 0) for x in (0, 157, 314) for y in (0, 34, 67))
    # the tray at 8, 6 in the image; its shadow two rows down, at most the alpha, gone by the image's edge
    assert at(157, 34)[3] == 0x77 and at(157, 67)[3] < 8 and at(0, 34)[3] < 8 and at(157, 0)[3] < 3
    assert at(157, 6)[3] < at(157, 8)[3] < at(157, 10)[3] and at(157, 60)[3] > at(157, 64)[3]
    assert len(got) < 8192


def test_the_round_icons_are_centred_opaque_circles_in_their_shadow():
    got = classic()
    for name, side, r in (('orb_note', 116, 49.5), ('orb_usb', 130, 49.5 * 1.14)):
        w, h, at = pixels(got[f'classic_light_{name}'])
        assert (w, h) == (side, side)
        assert at(w // 2, h // 2)[3] == 255
        assert [at(x, y)[3] for x in (0, w - 1) for y in (0, h - 1)] == [0, 0, 0, 0]
        assert at(w // 2, round(side / 2 - r + 3))[3] == 255 and at(w // 2, round(side / 2 + r - 3))[3] == 255  # the rim, inside its radius
        below = at(w // 2, round(side / 2 + r + 2))  # the shadow: black, translucent, a drop below the circle stronger than above
        above = at(w // 2, round(side / 2 - r - 3))
        assert below[:3] == (0, 0, 0) and above[3] < below[3] <= 0x66
    note, usb = pixels(got['classic_light_orb_note'])[2], pixels(got['classic_light_orb_usb'])[2]
    assert note(58 + 16, 58 - 10)[:3] == (0x16, 0x0b, 0x00)  # the note's right stem
    assert usb(65, 65)[:3] == (0x16, 0x0b, 0x00) and usb(65, 65 + 33)[:3] == (0x16, 0x0b, 0x00)  # the trident's stem, its foot
    assert note(58, 58 + 44)[:3] != (0x16, 0x0b, 0x00) and note(58, 58 + 44)[0] > 0xc0  # the circle's lower rows, in its colours


def test_the_soft_shadows_are_their_boxes_blurred():
    got = classic()
    tail = lambda d, sigma: .5 * math.erfc(d / (sigma * 2**.5))  # a straight edge's blur, d past it
    for name, (bw, bh, blur, alpha) in {'band_shadow': (322, 46, 5, 0x66), 'panel_shadow': (319, 48, 6, 0x33), 'rate_shadow': (304, 110, 6, 0x33), 'tray_shadow': (125, 44, 6, 0x77)}.items():
        w, h, at = pixels(got[f'classic_light_{name}'])
        pad = blur + 2
        assert (w, h) == (bw + 2 * pad, bh + 2 * pad)
        assert {at(x, y)[:3] for x in range(w) for y in range(h) if at(x, y)[3]} == {(0, 0, 0)}  # black in either half
        assert max(at(x, y)[3] for x in range(w) for y in (0, h - 1)) <= 1 and max(at(x, y)[3] for x in (0, w - 1) for y in range(h)) <= 1  # nothing cut at the image's edge
        for y in range(pad + bh - 2 + (name == 'tray_shadow'), h):  # under the box's foot (its y offset is 2) and any ring, down the middle
            assert abs(at(w // 2, y)[3] - alpha * tail(y + .5 - pad - bh, blur / 2)) <= 1, (name, y)
        assert at(w // 2, pad + bh // 2)[3] == 0 and at(w // 2, pad + bh - 3)[3] == 0  # none under the box, as CSS clips it
        assert at(w // 2, pad - 4)[3] < at(w // 2, h - pad + 1)[3] and at(pad - 1, h // 2)[3] == at(w - pad, h // 2)[3]  # the foot darker than the head; the sides alike
    # the tray's ring, over its shadow
    w, h, at = pixels(got['classic_light_tray_shadow'])
    under = 0x77 / 255 * tail(2.5, 3)
    assert abs(at(w // 2, 8 - 2 - 1)[3] - 255 * (1 - (1 - 0x66 / 255) * (1 - under))) <= 1


def test_the_switch_edges():
    got = classic()
    w, h, at = pixels(got['classic_light_track_inset'])
    assert (w, h) == (100, 27) and at(50, 13)[3] == 0
    top = 0xdd / 255 * (1 - .5 * math.erfc(1.5 / 2**.5))
    assert at(50, 0)[:3] == (0, 0, 0) and abs(at(50, 0)[3] - 255 * (1 - (1 - top) * (1 - 0x88 / 255))) <= 1  # the inset's blur over its 1 px ring
    assert at(50, 26) == (255, 255, 255, 0x29) and at(0, 26)[3] == 0  # the light row under it, rounded
    w, h, at = pixels(got['classic_light_lit_edge'])
    assert (w, h) == (52, 28) and at(26, 14)[3] == 0
    assert at(26, 0) == (0, 0, 0, 0x77) and at(26, 27) == (0, 0, 0, 0x77) and at(0, 14) == (0, 0, 0, 0x77)  # its ring
    assert at(26, 1) == (255, 255, 255, 0x73) and at(26, 26) == (0, 0, 0, 0x59)  # its light top row, dark last


def test_quick_settings_glyphs_are_flat_marks_on_clear():
    # navigation.c paints each button; its art is the glyph alone in the tile's ink: white on, #262626 / #e4e6e9 off
    got = classic()
    inks = {'': (255, 255, 255), '_l': (0x26, 0x26, 0x26), '_d': (0xe4, 0xe6, 0xe9)}
    for n in QS_NAMES:
        for k in QS_INKS(n):
            w, h, at = pixels(got[f'classic_light_drop_{n}{k}'])
            assert (w, h) == (60, 60)
            assert all(at(x, y)[3] == 0 for x in range(60) for y in (0, 59)) and all(at(x, y)[3] == 0 for x in (0, 59) for y in range(60)), n
            assert {at(x, y)[:3] for x in range(60) for y in range(60) if at(x, y)[3]} == {inks[k]}, n  # one ink, flat


def test_quick_settings_glyphs_dim_only_when_disabled():
    got = classic()
    mark = lambda n: got[f'classic_light_drop_{n}{QS_INKS(n)[0]}']
    alpha = lambda n: max(pixels(mark(n))[2](x, y)[3] for x in range(60) for y in range(60))
    assert {n for n in QS_NAMES if alpha(n) < 255} == {'lowgaindisable', 'highgaindisable'}
    assert alpha('highgaindisable') == round(255 * art.QS_DIM)
    assert all(mark(a) != mark(b) for a, b in (('lo', 'sa'), ('po', 'sa'), ('lowgain', 'highgaindisable')))
    # stock's three USB modes, three marks: storage off, as stock's grey disc
    assert len({got[f'classic_light_drop_{n}_l'] for n in ('usbstorage', 'usbaudio', 'usbdac')}) == 3 and 'usbstorage' not in QS_LIT  # one ink, three marks
    # the text keeps its counter: the O of PO is clear in its middle
    w, h, at = pixels(mark('po'))
    row = [at(x, 30)[3] for x in range(30, 45)]
    assert max(row) == 255 and min(row[row.index(max(row)):]) < 90


def test_quick_settings_lit_names_are_the_on_buttons():
    assert art.qs_lit() == ''.join(f'drop_{n}\\0' for n in art.QS_BUTTONS if n in QS_LIT) and set(art.QS_BUTTONS) == set(QS_NAMES)


def test_quick_settings_track_and_knob():
    got = classic()
    w, h, at = pixels(got['classic_light_drop_track'])
    assert (w, h) == (239, 12)
    assert at(120, 0)[3] > at(120, 5)[3] and at(120, 0)[:3] == (0, 0, 0)  # the inset darkens from the top
    assert at(120, 11) == (255, 255, 255, 0x22) and at(0, 0)[3] == 0
    w, h, at = pixels(got['classic_light_drop_knob'])
    assert (w, h) == (29, 29) and min(at(14, 3)[:3]) >= 0xfb and at(14, 14)[3] == 255 and at(0, 0)[3] < 16
    assert abs(at(14, 14)[0] - 0xd9) <= 1 and at(14, 26)[:3] == (0, 0, 0) and at(14, 25)[0] < 0xc0  # the ramp's middle; the shadow's row below the disc


def test_the_keyboard_glyphs_and_the_field():
    got = classic()
    for name, size in (('kb_del', (22, 18)), ('kb_shift', (22, 18)), ('kb_back', (13, 24)), ('field_glass', (16, 16)), ('field', (32, 31))):
        assert pixels(got[f'classic_light_{name}'])[:2] == size
    at = pixels(got['classic_light_kb_del'])[2]
    assert at(16, 2)[:3] == (255, 255, 255) and at(0, 0)[3] == 0 and at(14, 9)[2] < 200  # the body; the cross's slate
    assert pixels(got['classic_light_kb_shift'])[2](11, 9)[:3] == (255, 255, 255)
    at = pixels(got['classic_light_kb_back'])[2]
    assert at(1, 12)[3] > 128 and at(12, 12)[3] == 0
    at = pixels(got['classic_light_field_glass'])[2]
    assert at(6, 2)[:3] == (0x8a, 0x8f, 0x96) and at(6, 6)[3] == 0 and at(13, 13)[3] > 128
    at = pixels(got['classic_light_field'])[2]
    assert at(16, 20) == (255, 255, 255, 255) and at(16, 1)[0] < at(16, 8)[0] and at(0, 0)[3] == 0  # white, its top soft, its corner out
    assert at(16, 30)[:3] == (255, 255, 255) and 0x50 <= at(16, 30)[3] <= 0x60  # the row below


def test_set_time_and_set_date_images():
    got = classic()
    w = {'tray_108': (122, 64), 'tray_165': (179, 64), 'tray_164': (178, 64), 'cell_45': (53, 50), 'cell_lit_45': (53, 50), 'cell_60': (68, 50), 'cell_lit_60': (68, 50), 'tile': (130, 112), 'clock_chrome': (110, 110), 'clock_shadow': (86, 26)}
    for n, wh in w.items():
        assert pixels(got[f'classic_light_{n}'])[:2] == wh, n
    near = lambda p, rgb: p[3] == 255 and all(abs(p[i] - (rgb >> s & 255)) <= 1 for i, s in enumerate((16, 8, 0)))
    _, _, face = pixels(got['classic_light_clock_chrome'])
    assert face(55, 55) == (255, 255, 255, 255) and face(0, 0)[3] == 0  # the middle of the face; outside the ring
    assert any(face(x, y)[:3] != (255, 255, 255) and face(x, y)[3] for x in range(50, 60) for y in range(13, 26))  # the 12, from the font's outlines
    assert any(face(x, 55) == (255, 255, 255, 255) for x in range(14, 20)) and any(face(x, 55)[:3] != (255, 255, 255) for x in range(14, 20))  # the 9's ink and its open counter
    _, _, tile = pixels(got['classic_light_tile'])
    row = lambda stops, y, h: art.gradient(stops, 0, h)(y + .5)  # a row's stop: its middle
    over = lambda rgb, a: sum(round((rgb >> s & 255) + (255 - (rgb >> s & 255)) * a / 255) << s for s in (16, 8, 0))
    assert near(tile(65, 6), over(row(art.TILE_TOP, 0, 22), 0x55)) and near(tile(65, 27), row(art.TILE_TOP, 21, 22)) and near(tile(65, 28), row(art.TILE_PAGE, 22, 100))
    assert tile(65, 106)[:3] == (0, 0, 0) and tile(65, 106)[3] >= 0x66  # the row under it, over the soft shadow
    assert tile(0, 0)[3] == 0
    _, _, sh = pixels(got['classic_light_clock_shadow'])
    assert abs(sh(43, 13)[3] - 0x70) <= 4 and sh(0, 0)[3] == 0 and sh(0, 13)[3] < 10
    _, _, lit = pixels(got['classic_light_cell_lit_45'])
    assert near(lit(26, 4), over(row(art.CELL_LIT, 0, 42), 0x66)) and near(lit(26, 45), row(art.CELL_LIT, 41, 42))
    assert lit(26, 46)[:3] == (0, 0, 0) and lit(26, 46)[3] > 0x40  # the row below: black, soft
    _, _, off = pixels(got['classic_light_cell_45'])
    assert off(26, 46)[:3] == (255, 255, 255) and abs(off(26, 46)[3] - 0x24) <= 1 and off(26, 4)[3] == 255
    _, _, tray = pixels(got['classic_light_tray_108'])
    assert near(tray(60, 7), row(art.TRAY_STOPS, 0, 48)) and tray(60, 55)[0] > 80 and tray(60, 55)[3] > 0x26  # the light row under it, over the shadow
    assert tray(0, 0)[3] < 12


def test_the_note_is_white_and_translucent():
    w, h, at = pixels(classic()['classic_light_note'])
    assert (w, h) == (100, 115)
    assert at(0, 0) == (0, 0, 0, 0)  # outside the drawing
    assert at(36, 30)[:3] == (255, 255, 255) and at(36, 30)[3] > 200  # the gloss at the stem's top
    assert at(90, 60)[:3] == (255, 255, 255) and 60 < at(90, 60)[3] < 130  # the right stem: its fill alone


def test_the_chevron_is_a_white_rounded_stroke_on_its_box():
    w, h, at = pixels(classic()['classic_light_chevron'])
    assert (w, h) == (12, 16)
    assert at(8, 8) == (255, 255, 255, 255)  # the point's centreline
    assert at(0, 15)[3] == 0 and at(11, 0)[3] == 0  # outside the stroke
    assert 0 < at(1, 1)[3] < 255  # a rounded end's edge


def test_sort_is_three_white_bars():
    w, h, at = pixels(classic()['classic_light_sort_bars'])
    assert (w, h) == (16, 12)
    for y in range(h):
        ink = [x for x in range(w) if at(x, y) == (255, 255, 255, 255)]
        assert ink == list(range({0: 16, 1: 16, 5: 11, 6: 11, 10: 6, 11: 6}.get(y, 0))), y
        assert all(at(x, y)[3] == 0 for x in range(w) if x not in ink), y


def test_the_volume_ends_are_white_speakers_the_loud_one_with_waves():
    got = classic()
    (lw, lh, lo), (hw, hh, hi) = (pixels(got[f'classic_light_vol_{n}']) for n in ('lo', 'hi'))
    assert (lw, lh, hw, hh) == (8, 13, 18, 13)
    assert all(lo(x, y)[:3] == (255, 255, 255) for x in range(lw) for y in range(lh) if lo(x, y)[3])
    assert all(hi(x, y) == lo(x, y) for x in range(7) for y in range(lh))  # the same speaker
    waves = [x for x in range(9, hw) if any(hi(x, y)[3] for y in range(hh))]
    assert waves and max(waves) == hw - 1


def test_the_pane_icons_fit_their_box_and_keep_their_holes():
    got = art.images(theme.builtins()[1])
    for name, side in (('folder', 96), ('stack', 96), ('game', 96), ('wave', 96), ('gear', 96), ('note', 100)):
        w, h, at = pixels(got[f'classic_light_icon_{name}'])
        assert w == side
        ink = [(x, y) for x in range(w) for y in range(h) if at(x, y)[3]]
        xs, ys = [p[0] for p in ink], [p[1] for p in ink]
        if name != 'note':  # 92 px on its longer side, centred on (278, 124)
            assert max(max(xs) - min(xs), max(ys) - min(ys)) in range(91, 96), name
            assert abs((min(xs) + max(xs)) / 2 + 230 - 278) <= 1 and abs((min(ys) + max(ys)) / 2 + 76 - 124) <= 1, name
    x, y = 278 - 230, 124 - 76
    assert pixels(got['classic_light_icon_gear'])[2](x, y)[3] == 0  # the gear's hole, even-odd
    assert pixels(got['classic_light_icon_folder'])[2](x, y)[3] > 0
    assert pixels(got['classic_light_icon_folder'])[2](x, y)[:3] == (255, 255, 255)


def test_path_reads_arcs_and_further_subpaths():
    polys = art.path('M0 0H10V10H0ZM20 0H30V10H20Z')
    assert len(polys) == 2 and polys[1][0] == (20, 0)
    arc = art.path('M0 0A5 5 0 1 1 10 0Z')[0]
    assert min(y for _, y in arc) >= -5.01 and max(abs(x - 5) for x, _ in arc) <= 5.01 and len(arc) > 40
    assert art.coverage([[(0, 0), (2, 0), (2, 2), (0, 2)]], (0, 0, 3, 1)) == [1.0, 1.0, 0.0]
    # a span inside another adds nothing: the union, not a sum
    assert art.coverage([[(0, 0), (3, 0), (3, 1), (0, 1)], [(1, 0), (1.5, 0), (1.5, 1), (1, 1)]], (0, 0, 3, 1)) == [1.0, 1.0, 1.0]


@pytest.mark.parametrize('rx, ry, sweep', [(1, 1, 1), (1, 2, 1), (2, 1, 0)])
def test_path_arc_too_small_for_its_chord_grows_to_fit(rx, ry, sweep):
    # SVG's rule (F.6.6): radii that can't span the ends scale up, in proportion, until they just do: half an
    # ellipse about the chord's middle, on the side sweep says (1: the positive angles, up in SVG's y-down space)
    arc, = art.path(f'M0 0A{rx} {ry} 0 0 {sweep} 10 0')
    rx, ry = 5, 5 * ry / rx  # the chord is 10 along x
    assert math.dist(arc[-1], (10, 0)) < 1e-9
    assert all(abs(((x - 5) / rx)**2 + (y / ry)**2 - 1) < 1e-9 for x, y in arc)
    assert all(y * (1 if sweep else -1) < 1e-9 for _, y in arc) and abs(max(abs(y) for _, y in arc) - ry) < 1e-9


def test_gradient_holds_the_last_stops_colour_past_it():
    g = art.gradient(((0, 0x000000), (50, 0x2080ff)), 10, 20)  # as an SVG linearGradient's: its end pads
    assert g(10) == 0 and g(15) == g(17.5) == g(20) == g(30) == 0x2080ff


def test_gradient_holds_the_first_stops_colour_before_it():
    g = art.gradient(((50, 0x204060), (100, 0xffffff)), 0, 100)
    assert g(10) == g(50) == 0x204060


@pytest.mark.parametrize('poly', [[(1, 2), (5, 2), (9, 2)], [(1, 2), (9, 2), (5, 2)]])
def test_butt_stroke_joins_nothing_where_the_line_goes_straight_on_or_back(poly):
    # no corner there; doubled back, the two sides' normals cancel and a mitre has no direction
    parts = art.butt_stroke(poly, 2)
    assert len(parts) == 2  # the segments alone
    assert art.coverage(parts, (0, 0, 10, 4)) == art.coverage([[(1, 1), (9, 1), (9, 3), (1, 3)]], (0, 0, 10, 4))


def test_every_literal_corner_radius_is_in_the_table():
    source = ''.join(p.read_text() for p in sorted((pathlib.Path(__file__).parents[2] / 'patch').glob('*.c')))
    def literal(names):
        return {int(r) for r in re.findall(rf'(?:{names})\([^;]*?,\s*(\d+)(?:,[^;]*?)?\);', source)}
    radii = literal('paint_round_rows|round_row|row_below|inset_top|round_ring')
    assert radii and radii <= set(art.CORNERS)
    assert {r - 1 for r in literal('round_ring')} <= set(art.CORNERS)  # a ring reads its radius less one too
    lit = {int(r) for r in re.findall(r'cover_of\((\d+)\)', source)}
    assert lit and lit <= set(art.CORNERS)  # pale_lit's


def test_the_round_corners_cover_their_arcs():
    for r in art.CORNERS:
        cover = art.corner_cover(r)
        assert len(cover) == r * r and cover == bytes(cover[x * r + y] for y in range(r) for x in range(r))  # about its diagonal
        assert cover[0] == {2: 81, 3: 6}.get(r, 0) and cover[-1] == 255  # the small corners' outer pixels keep some cover
        for y, x, drawn in ((y, x, v) for y in range(r) for x, v in enumerate(art.rounded(r, r, 0, 0, 2 * r, 2 * r, r)[y * r:(y + 1) * r])):
            assert abs(cover[y * r + x] - drawn * 255) <= 16, (r, x, y)  # the images' own corners, their sampling apart
    assert all(f'[{r}] = ' in art.corner_table() for r in art.CORNERS)


def test_the_bands_rim_follows_its_corners():
    got = classic()
    w, h, top = pixels(got['classic_light_band_top'])
    assert (w, h) == (322, 9)
    assert top(100, 0) == (255, 255, 255, 0x59) and top(100, 1)[3] == 0  # inset 0 1px, white at 0x59, its own row
    assert 0 < top(1, 3)[3] <= 0x59 and top(0, 0)[3] == 0 and top(320, 3) == top(1, 3)  # on the arc, mirrored
    w, h, foot = pixels(got['classic_light_band_foot'])
    assert (w, h) == (322, 9) and foot(100, 8) == (255, 255, 255, 0x1f) and foot(100, 7)[3] == 0  # 0 1px under the band, white at 0x1f
    assert foot(0, 8)[3] == 0 and 0 < foot(1, 5)[3] <= 0x1f and foot(320, 5) == foot(1, 5)


def test_the_rating_hearts_are_the_panels_size_and_ink():
    got = classic()
    for half, ink in (('l', (0, 0, 0)), ('d', (255, 255, 255))):
        w, h, on = pixels(got[f'classic_light_heart_on_{half}'])
        assert (w, h) == (32, 30) and on(16, 16) == (*ink, 255)  # filled
        _, _, off = pixels(got[f'classic_light_heart_off_{half}'])
        assert off(16, 16)[3] == 0 and off(16, 26)[:3] == ink and off(16, 26)[3] > 200  # its outline: the stroke at the point
        assert on(0, 0)[3] == 0 == off(0, 0)[3]
        assert max(x for x in range(32) if any(off(x, y)[3] for y in range(30))) == 31  # the stroke reaches its box's width


def test_the_station_cover_fills_the_slanted_arts_square():
    w, h, at = pixels(classic()['classic_light_station'])
    a = art.inc('NP_IPOD_ART')
    assert (w, h) == (a, a) and at(0, 0)[3] == at(a - 1, a - 1)[3] == 255  # opaque: the slant's paper never shows
    assert at(a // 2, a - 1)[:3] != at(a // 2, 0)[:3]  # its gradient, top to bottom
    assert min(at(54, 87)[:3]) > 200  # the left wave's white, at its widest


def test_a_theme_drawing_anothers_art_shares_its_images():
    """A theme stating art="classic-light" draws Classic's images, by Classic's names and bytes, packaged once; a value
    that would draw them otherwise stops the build, as the payload loads one image per name."""
    light = {**theme.builtins()[1], 'pane_icon_x': 0}
    other = {**light, 'id': 'other'}  # Classic's values under another id, as a theme extending it resolves
    assert set(art.images({**other, 'art_id': 'other'})) == {n.replace('classic_light_', 'other_') for n in classic()} - {'other_note'}
    assert art.theme_images([light, other]) == classic()  # its art_id, Classic's: the same names and bytes
    with pytest.raises(ValueError, match="other: classic_light_bolt differs from classic-light's, whose art it draws by name"):
        art.theme_images([light, {**other, 'bolt': 0x123456}])


def test_oled_draws_its_glyphs_from_phosphor_in_each_halfs_ink():
    """OLED's own images: the caret in Chevron grey (each half's) and white for the selection, the play state in each
    half's ink, Now Playing's shuffle and repeat in Text Secondary (each half's) and Accent Blue when on, the Shuffle row's
    glyph (regular) in Accent Blue and white for the selection, Folder view's folder (regular) in Text Secondary (each
    half's) and white for the selection, a disabled row's caret in Text Tertiary (each half's), the pickers' check
    (bold) in Accent Blue and white for the selection, the Wi-Fi page's signal and lock (regular) in Text Primary
    (each half's), the shuffle confirmation's glyph (bold) in Accent Blue, Now Playing's heart and More (regular) in
    Text Primary (each half's), More pressed in Text Secondary and the full heart (fill) in Accent Blue, and the
    Ratings rows' filled and outlined hearts and Streaming's service marks in the folder's inks, all from the vendored
    Phosphor SVGs but TIDAL's mark, drawn in art.py, and the inset modal's symbols (regular, 19 px) in Text Secondary
    (each half's) and white for the selection and the picker's check in Accent Blue, the volume's two speakers (fill,
    16 px) in Text Secondary (each half's), and an artist page's Shuffle and Radio (regular, 16 px) in Accent Blue and
    white for the lit one."""
    t = theme.builtins()[3]
    got = art.images(t)
    modes = [f'mode_{n}_{k}' for n in ('shuffle', 'repeat') for k in ('d', 'l', 'on')] + ['mode_repeat1_on']
    wifi = [f'{n}_{k}' for n in ('wifi_1', 'wifi_2', 'wifi_3', 'lock') for k in ('d', 'l')]
    glyphs = {'np_fav': 0, 'np_unfav_d': 0xffffff, 'np_unfav_l': 0, 'np_more_d': 0xffffff, 'np_more_l': 0,
              'np_moredown_d': 0xababab, 'np_moredown_l': 0x5c5c5c}
    rates = [f'rate_{on}_{k}' for on in ('on', 'off') for k in ('d', 'l', 'w')]
    marks = [f'stream_{n}_{k}' for n in ('tidal', 'spotify', 'app', 'radio', 'airplay', 'config') for k in ('d', 'l', 'w')]
    menus = [f'menu_{n}_{k}' for n in art.MENU_ICONS for k in ('d', 'l', 'w')] + ['menu_check_a']
    speakers = [f'vol_{n}_{k}' for n in ('lo', 'hi') for k in ('d', 'l')]  # the volume's ends on Now Playing (fill, 16 px)
    pair = [f'pair_{n}_{k}' for n in ('shuffle', 'radio') for k in ('a', 'w')]  # artist_page.c's action pair
    keylock = [f'keylock_{k}' for k in ('d', 'l')]  # keylock.c: the status row's padlock and the card's
    card = [f'keylock_card_{k}' for k in ('d', 'l')]
    assert set(got) == {f'oled_{n}' for n in ('chevron_d', 'chevron_l', 'chevron_w', 'chevron_td', 'chevron_tl', 'play_d', 'play_l',
                                              'pause_d', 'pause_l', 'shuffle_a', 'shuffle_w', 'folder_d', 'folder_l', 'folder_w',
                                              'check_a', 'check_w', 'confirm_shuffle', *modes, *wifi, *glyphs, *rates, *marks, *menus, *speakers, *pair, *keylock, *card,
                                              'search_pop', 'search_pop_letters', 'search_pop_echoes')}
    assert {'funnel-simple', 'arrow-bend-down-right', 'queue', 'heart', 'list-plus', 'check', 'caret-up', 'caret-down'} <= set(art.MENU_ICONS)
    blue = t['schemes'][t['accents'].bit_length() - 1]['tones'][0]
    for name, px, rgb in (('chevron_d', art.CARET_PX, 0x999999), ('chevron_l', art.CARET_PX, 0x8a8a8a), ('chevron_w', art.CARET_PX, 0xffffff),
                          ('play_d', art.STATE_PX, 0xffffff), ('play_l', art.STATE_PX, 0),
                          ('shuffle_a', art.ROW_ICON_PX, blue), ('shuffle_w', art.ROW_ICON_PX, 0xffffff),
                          ('folder_d', art.ROW_ICON_PX, 0xababab), ('folder_l', art.ROW_ICON_PX, 0x5c5c5c),
                          ('folder_w', art.ROW_ICON_PX, 0xffffff), ('confirm_shuffle', art.CONFIRM_GLYPH_PX, blue),
                          ('chevron_td', art.CARET_PX, 0x858585), ('chevron_tl', art.CARET_PX, 0x6e6e6e),
                          ('check_a', art.CHECK_PX, blue), ('check_w', art.CHECK_PX, 0xffffff),
                          *((n, art.ROW_ICON_PX, 0 if n.endswith('_l') else 0xffffff) for n in wifi),
                          *((n, 22, {'d': 0xababab, 'l': 0x5c5c5c, 'on': blue}[n.rsplit('_', 1)[1]]) for n in modes),
                          *((n, art.ROW_ICON_PX, blue if n == 'np_fav' else rgb) for n, rgb in glyphs.items()),
                          *((n, art.RATE_PX, {'d': 0xababab, 'l': 0x5c5c5c, 'w': 0xffffff}[n[-1]]) for n in rates),
                          *((n, art.STREAM_PX, {'d': 0xababab, 'l': 0x5c5c5c, 'w': 0xffffff}[n[-1]]) for n in marks),
                          *((n, art.MENU_ICON_PX, {'d': 0xababab, 'l': 0x5c5c5c, 'w': 0xffffff, 'a': blue}[n.rsplit('_', 1)[1]]) for n in menus),
                          *((n, 16, {'d': 0xababab, 'l': 0x5c5c5c}[n[-1]]) for n in speakers),
                          *((n, art.PAIR_ICON_PX, blue if n.endswith('_a') else 0xffffff) for n in pair),
                          *((n, art.STATE_PX, 0 if n.endswith('_l') else 0xffffff) for n in keylock),
                          *((n, art.KEYLOCK_CARD_PX, 0 if n.endswith('_l') else 0xffffff) for n in card)):
        w, h, at = pixels(got[f'oled_{name}'])
        inked = {at(x, y)[:3] for y in range(h) for x in range(w) if at(x, y)[3]}  # straight alpha: one colour, any coverage
        assert (w, h) == (px, px) and inked == {tuple(rgb >> s & 255 for s in (16, 8, 0))}
        assert max(at(x, y)[3] for y in range(h) for x in range(w)) > 200


def test_phosphor_fills_nonzero_so_an_inner_contour_is_a_hole():
    """A ring as two contours wound opposite ways, as Phosphor's outlines are: its middle stays clear."""
    ring = art.path('M2 2H14V14H2ZM5 5V11H11V5Z')
    cov = art.coverage_nonzero(ring, (0, 0, 16, 16))
    assert cov[8 * 16 + 8] == 0 and cov[3 * 16 + 3] == 1 and cov[0] == 0
    assert art.coverage(ring, (0, 0, 16, 16))[8 * 16 + 8] == 1  # the union rule would fill it


def test_oleds_search_pop_is_one_bubble_and_two_letter_strips():
    """Search's key pop (search_strip.c): the bubble, 30 px wide to the key's foot 66 px down, in Accent Blue, a
    rounded head over a neck narrowing to the 18 px key, clear beside the key; the letters' and echoes' strips, a head's
    and a key's cell per strip character, white, the echo at 60 %; each glyph's ink centred on its cell's middle
    within a tenth of a pixel, read from the edge columns' coverage (H: two upright stems)."""
    t = theme.builtins()[3]
    got = art.images(t)
    blue = t['schemes'][t['accents'].bit_length() - 1]['tones'][0]
    w, h, at = pixels(got['oled_search_pop'])
    assert (w, h) == (30, 66) and at(15, 15) == (*blue.to_bytes(3, 'big'), 255) and at(15, 60) == (*blue.to_bytes(3, 'big'), 255)
    assert at(0, 0)[3] == 0 and at(0, 15)[3] == 255 and at(29, 15)[3] == 255  # the head's rounded corner; its sides
    assert at(3, 60)[3] == 0 and at(26, 60)[3] == 0 and at(6, 60)[3] == 255 and at(23, 60)[3] == 255  # the key, 6 px in
    n = len(art.POP_CHARS)
    for name, cell, ch, alpha in (('letters', 30, 30, 255), ('echoes', 18, 38, 153)):
        w, h, at = pixels(got[f'oled_search_pop_{name}'])
        assert (w, h) == (cell * n, ch)
        i = art.POP_CHARS.index('H')
        cols = [max(at(cell * i + x, y)[3] for y in range(h)) / alpha for x in range(cell)]
        assert max(cols) == pytest.approx(1, abs=0.01) and {at(cell * i + x, y)[:3] for x in range(cell) for y in range(h) if at(cell * i + x, y)[3]} == {(255, 255, 255)}
        lit = [x for x, c in enumerate(cols) if c > 0.01]
        left, right = lit[0] + 1 - cols[lit[0]], cell - (lit[-1] + cols[lit[-1]])
        assert left == pytest.approx(right, abs=0.1)


def test_oleds_rating_hearts_are_phosphors_filled_and_outlined_heart():
    """A filled heart inks its middle, the outlined one leaves it clear."""
    got = art.images(theme.builtins()[3])
    mid = art.RATE_PX // 2
    for name, middle in (('oled_rate_on_d', True), ('oled_rate_off_d', False)):
        w, h, at = pixels(got[name])
        assert bool(at(mid, mid)[3]) == middle


def test_oleds_tidal_mark_is_four_diamonds_three_across_one_under():
    """TIDAL's mark: the three top diamonds' centres and the lower one's are inked, the corners between them clear."""
    w, h, at = pixels(art.images(theme.builtins()[3])['oled_stream_tidal_d'])
    assert all(at(x, 6)[3] > 200 for x in (3, 10, 16)) and at(10, 13)[3] > 200
    assert at(3, 13)[3] == 0 and at(16, 13)[3] == 0 and at(10, 1)[3] == 0
