"""patch/navigation.c's paint hooks under OLED, the one theme: the image hook (the accent, Light Mode, the mono
icons, the theme's own art and tiles, art corners), the style colour and gradient hooks, the background hook, the
bar title and Quick Settings' brightness, with volume.c's volume overlay, scan_page.c's scan page and USB mode's
eject."""
import collections
import ctypes as C
import re
import struct

import pytest

import host
from conftest import NAVIGATION_SOURCE, O, Deques, Externpic, Files, block, in_fork, libc
from test_nav_hold import recs  # noqa: F401  stSongInfo records P() reads whole

W_X, W_Y, W_W, W_H, W_PARENT = O['W_X'], O['W_Y'], O['W_W'], O['W_H'], O['W_PARENT']
CANVAS_LCD, LCD_FILL_COLOR = O['CANVAS_LCD'], O['LCD_FILL_COLOR']
EVT_DESTROY = O['EVT_DESTROY']
STOCK_RED = O['STOCK_RED']
RET_OK, RET_REMOVE, RET_REPEAT = 0, 7, 8
OFFSETS = (host.ROOT/'patch/offsets.inc').read_text()
STRINGS = dict(re.findall(r'^#define (\w+) +"([^"]*)"', OFFSETS, re.M))
ACCENTS = [[int(v, 16) for v in re.findall(r'0x[0-9a-f]+', row)] for row in re.findall(r'\{ (0x[^}]*)\}', OFFSETS)][:9]
LIGHT, RED = 2, 3  # a scheme's tones: the light tone, the red tone
OLED = '3'  # IPOD/THEME, which theme_boot keeps at OLED's
# OLED's theme.xml: its one scheme's five tones (Accent Blue), its light half's paper and ink (greys: one channel)
_OLED_XML = (host.ROOT/'assets/themes/oled/theme.xml').read_text()
_tones = re.search(r'<colorScheme name="oled-blue"[^>]* tones="([^"]*)"', _OLED_XML)
_paper, _ink = re.search(r'light\.paper="#(\w+)"', _OLED_XML), re.search(r'light\.ink="#(\w+)"', _OLED_XML)
assert _tones and _paper and _ink
BLUE = [int(t[1:], 16) for t in _tones[1].split()]
PAPER, INK = int(_paper[1], 16) & 255, int(_ink[1], 16) & 255
# bitmap_t's formats (u16 @0xe) and where each keeps r, g, b and a (docs/internals.md#accent)
AT = {1: (0, 1, 2, 3), 2: (3, 2, 1, 0), 3: (2, 1, 0, 3), 4: (1, 2, 3, 0)}
OPAQUE, PREMULTI = 1, O['BITMAP_PREMULTI']
# OLED's grid, which the build bakes into stock's rows
_set = re.search(r'<geometry ([^>]*)/>', _OLED_XML)
assert _set
GEOMETRY = {k: int(v) for k, v in re.findall(r'(\w+)="(\d+)"', _set[1])}
# its list's art corners (list.art.radius) and the selection's inset (list.selection.inset)
_art = re.search(r'<element name="art" [^>]*radius="(\d+)"', _OLED_XML)
_sel = re.search(r'<element name="selection" inset="(\d+)"', _OLED_XML)
assert _art and _sel
ART_RADIUS, SEL_INSET = int(_art[1]), int(_sel[1])


def rgba(c, a=0xff):
    """0xRRGGBB as a color_t, whose bytes are r, g, b, a."""
    return a << 24 | (c & 255) << 16 | c & 0xff00 | c >> 16


def channels(c):
    return c >> 16 & 255, c >> 8 & 255, c & 255


def luma(c):
    r, g, b = channels(c)
    return (r * 299 + g * 587 + b * 114) // 1000


class Config:
    """config.ini, its value or the default."""
    def __init__(self, fw, **values):
        self.values = {k.encode(): v.encode() for k, v in values.items()}
        fw.mock('toolsReadConfig', self.read)

    def read(self, path, section, key, out, default, *_):
        value = self.values.get(C.string_at(key), C.string_at(default))
        C.memmove(out, value + b'\0', len(value) + 1)


def strings(fw):
    """Stock's AWTK string tests, which the C leans on."""
    fw.mock('tk_str_start_with', lambda s, prefix, *_: C.string_at(s).startswith(C.string_at(prefix)))
    fw.mock('tk_str_end_with', lambda s, end, *_: C.string_at(s).endswith(C.string_at(end)))


def theme(fw, **values):
    """The settings the C reads at its first colour: OLED and the rest."""
    strings(fw)
    Config(fw, THEME=OLED, **values)


@pytest.fixture(scope='module')
def layout(library):
    """scratch_t's, scheme_t's and theme_t's field offsets as this build lays them out (their pointers move them), and
    volume.c's own state's, vd."""
    obj = library.parent/'navigation.o'
    return {'st': host.offsets(obj, 'scratch_t'), 'scheme': host.offsets(obj, 'scheme_t'),
            'theme': host.offsets(obj, 'theme_t'), 'vd': host.offsets(library.parent/'volume.o', 'vd')}


@pytest.fixture
def st(fw, layout):
    base = C.addressof(fw.var('st', C.c_char))
    return lambda name, ctype=C.c_int: ctype.from_address(base + layout['st'][name])


@pytest.fixture
def vd(fw, layout):
    """volume.c's own state by field: the dialog vol_paint last drew, its value and the poll's timer."""
    base = C.addressof(fw.var('volume__vd', C.c_char))
    return lambda name, ctype=C.c_int: ctype.from_address(base + layout['vd'][name])


@pytest.fixture
def scheme(fw, layout):
    """The live scheme's 0xRRGGBB fields by name, as the theme declares them for the half on show."""
    def read(name):
        return C.c_uint.from_address(fw.fn('scheme', C.c_void_p)() + layout['scheme'][name]).value
    return read


@pytest.fixture
def theme_t(fw, layout):
    """The live theme's fields by name, as tools/theme.py wrote them from its theme.xml: an array's index-th."""
    def read(name, ctype, index=0):
        return ctype.from_address(fw.fn('theme', C.c_void_p)() + layout['theme'][name] + index * C.sizeof(ctype)).value
    return read


# The image hook: theme images as they are decoded, before the image manager caches them.

class Bitmap:
    """A decoded bitmap_t: w @0, h @4, flags @0xc, format @0xe, and pixels of rows stride long, each padded with
    0x5a bytes the C must leave."""
    def __init__(self, fw, rows, fmt=1, pad=2, flags=0):
        self.h, self.w, self.at = len(rows), len(rows[0]), AT.get(fmt, AT[1])
        self.stride = 4 * (self.w + pad)
        self.header = block(fw, size=0x40)
        self.data = block(fw, size=self.stride * self.h)
        C.memmove(self.header, struct.pack('<iiiHH', self.w, self.h, 0, flags, fmt), 16)
        raw = bytearray(b'\x5a' * (self.stride * self.h))
        for y, row in enumerate(rows):
            for x, p in enumerate(row):
                for k in range(4):
                    raw[y * self.stride + 4 * x + self.at[k]] = p[k]
        C.memmove(self.data, bytes(raw), len(raw))

    def get(self, x, y):
        raw = C.string_at(self.data + y * self.stride + 4 * x, 4)
        return tuple(raw[self.at[k]] for k in range(4))

    def rows(self):
        raw = C.string_at(self.data, self.stride * self.h)
        return [[tuple(raw[y * self.stride + 4 * x + self.at[k]] for k in range(4)) for x in range(self.w)]
                for y in range(self.h)]

    def padding(self):
        return {C.string_at(self.data + y * self.stride + 4 * self.w, self.stride - 4 * self.w) for y in range(self.h)}

    @property
    def flags(self):
        return C.c_ushort.from_address(self.header + 0xc).value


class Bitmaps:
    """bitmap_lock_buffer_for_write and the rest over Bitmap's, by header."""
    def __init__(self, fw):
        self.known, self.locked = {}, []
        fw.mock('bitmap_lock_buffer_for_write', lambda bm, *_: self.locked.append(bm) or self.known[bm].data)
        fw.mock('bitmap_get_line_length', lambda bm, *_: self.known[bm].stride)

    def __call__(self, fw, rows, **kw):
        bm = Bitmap(fw, rows, **kw)
        self.known[bm.header] = bm
        return bm


@pytest.fixture
def bitmaps(fw):
    return Bitmaps(fw)


def fill(w, h, px):
    return [[px] * w for _ in range(h)]


def image_add(fw, name, bm, result=0):
    """The hook, stock's own add after it answering result: what reached stock."""
    seen = []
    fw.mock('stock_image_trampoline', lambda m, n, b, *_: seen.append((m, C.string_at(n) if n else None, b)) or result)
    got = fw.fn('ringnav_image_add', C.c_int, C.c_void_p, C.c_char_p, C.c_void_p)(0x5100, name, bm.header if bm else None)
    assert got == result and seen == [(0x5100, name, bm.header if bm else 0)]
    return seen


def px(c, a=255):
    return (*channels(c), a)


def saturation(p):
    return max(p[:3]) - min(p[:3])


RED_PX, WHITE, CLEAR = px(STOCK_RED), (255, 255, 255, 255), (0, 0, 0, 0)
PLAIN = b'loading_ring'  # a theme image no rule of the hook names: no icon, no control, not OLED's own art


@pytest.mark.parametrize('fmt', [1, 2, 3, 4])
def test_a_theme_image_s_red_takes_the_accent_s_red_tone_in_any_byte_order(fw, bitmaps, fmt):
    theme(fw)
    bm = bitmaps(fw, [[RED_PX, px(0x808080), px(0x123456)]], fmt=fmt)
    image_add(fw, PLAIN, bm)
    assert bm.rows() == [[px(BLUE[RED]), px(0x808080), px(0x123456)]]  # greys and other hues stay
    assert bm.padding() == {b'\x5a' * 8}


def test_an_unknown_format_or_no_bitmap_is_left_to_stock(fw, bitmaps):
    theme(fw)
    bm = bitmaps(fw, [[RED_PX]], fmt=5)
    image_add(fw, PLAIN, bm)
    image_add(fw, PLAIN, None, result=3)
    assert bitmaps.locked == [] and bm.get(0, 0) == RED_PX


@pytest.mark.parametrize('name', [b'file:///mnt/mmc/Music/a.jpg', b'/usr/share/x.png', b'http://host/a.png'])
def test_covers_by_path_keep_their_colours(fw, bitmaps, name):
    theme(fw, LIGHT='1')
    bm = bitmaps(fw, [[RED_PX, WHITE]])
    image_add(fw, name, bm)
    assert bm.rows() == [[RED_PX, WHITE]]


@pytest.mark.parametrize('name', [b'menu_left', b'menu_music'])
def test_stock_s_card_art_is_a_theme_image_like_any(fw, bitmaps, name):
    """Home's carousel is never shown, so its cards' art (menu_*) has no exception: it flips in Light Mode as any
    theme image does."""
    theme(fw, LIGHT='1')
    bm = bitmaps(fw, [[WHITE, CLEAR]])
    image_add(fw, name, bm)
    assert bm.rows() == [[(INK, INK, INK, 255), CLEAR]]


def test_the_confirm_discs_take_the_dark_surface(fw, bitmaps):
    theme(fw)
    bm = bitmaps(fw, [[RED_PX, WHITE]])
    image_add(fw, STRINGS['CONFIRM_IMAGE'].encode() + b'ok', bm)
    assert bm.rows() == [[px(O['CONFIRM_SURFACE']), WHITE]]


def test_an_active_quick_control_keeps_its_white_glyph_on_accent_blue(fw, bitmaps):
    """Accent Blue is no brighter than GLYPH_LIGHT_MAX, so the glyph stays white; drop_ images are mono: the disc
    is then its tone's grey."""
    theme(fw)
    assert luma(BLUE[RED]) <= O['GLYPH_LIGHT_MAX']
    bm = bitmaps(fw, [[RED_PX, WHITE]])
    image_add(fw, STRINGS['DROPDOWN_IMAGE'].encode() + b'wifi_on', bm)
    assert bm.rows() == [[(luma(BLUE[RED]),) * 3 + (255,), WHITE]]


def test_an_inactive_quick_control_keeps_its_white_glyph(fw, bitmaps):
    theme(fw)
    bm = bitmaps(fw, [[px(0x555555), WHITE]])
    image_add(fw, STRINGS['DROPDOWN_IMAGE'].encode() + b'wifi_off', bm)
    assert bm.rows() == [[px(0x555555), WHITE]]


@pytest.mark.parametrize('fmt', [1, 3])
def test_a_list_icon_s_disc_greys_and_keeps_its_glyph(fw, bitmaps, scheme, fmt):
    """icons.mono: the disc (its most saturated pixels) turns the scheme's chevron grey, the white glyph stays, and an
    edge halfway between blends halfway."""
    theme(fw)
    half = tuple((v + 255) // 2 for v in RED_PX)  # stock red halfway to white: half the disc's saturation
    bm = bitmaps(fw, [[RED_PX, WHITE, half, px(STOCK_RED, 0x40)]], fmt=fmt)
    image_add(fw, b'list_music', bm)
    grey = scheme('chevron') & 255
    edge = 255 - (255 - grey) * saturation(half) // saturation(RED_PX)
    assert bm.rows() == [[(grey,) * 3 + (255,), WHITE, (edge,) * 3 + (255,), (grey,) * 3 + (0x40,)]]


def test_an_already_grey_icon_is_left(fw, bitmaps):
    theme(fw)
    bm = bitmaps(fw, [[px(0x333333), WHITE]])
    image_add(fw, b'list_music', bm)
    assert bm.rows() == [[px(0x333333), WHITE]]


def test_baidu_s_square_is_cut_to_a_disc(fw, bitmaps):
    """Cut to one as wide as the square, its edge soft at the square's sides."""
    theme(fw)
    bm = bitmaps(fw, fill(12, 12, WHITE))
    image_add(fw, b'list_baidu', bm)
    alpha = [[p[3] for p in row] for row in bm.rows()]
    assert alpha[0][0] == alpha[0][11] == alpha[11][0] == alpha[11][11] == 0  # its corners gone
    assert alpha[5][5] == alpha[6][6] == 255 and 0 < alpha[6][0] < 255  # its middle kept, its edge soft
    assert alpha[6][6:] == sorted(alpha[6][6:], reverse=True)  # falling outwards
    assert all(row == row[::-1] for row in alpha) and alpha == [list(r) for r in zip(*alpha)]  # round
    assert all(p[:3] == (255, 255, 255) for row in bm.rows() for p in row)


def test_baidu_greys_inverted(fw, bitmaps, scheme):
    """Its white square becomes the grey disc and the blue logo white, as its neighbours' discs and glyphs."""
    theme(fw)
    rows = fill(12, 12, WHITE)
    rows[6][6] = (0x20, 0x60, 0xff, 255)
    bm = bitmaps(fw, rows)
    image_add(fw, b'list_baidu', bm)
    grey = scheme('chevron') & 255
    assert bm.get(6, 6) == WHITE and bm.get(3, 6) == (grey, grey, grey, 255) and bm.get(0, 0)[3] == 0


def test_the_navigation_icons_are_grey_after_the_accent(fw, bitmaps):
    theme(fw)
    bm = bitmaps(fw, [[RED_PX, px(0x2040c0)]])
    image_add(fw, b'navbar_back', bm)
    tone = luma(BLUE[RED])
    assert bm.rows() == [[(tone,) * 3 + (255,), (luma(0x2040c0),) * 3 + (255,)]]


@pytest.mark.parametrize('name', [b'play_defaultcover', b'playlist_default', b'tidal_shanling', b'tidal_user'])
def test_the_placeholders_and_tidal_s_own_keep_their_colour(fw, bitmaps, name):
    theme(fw)
    bm = bitmaps(fw, [[px(0x2040c0)]])
    image_add(fw, name, bm)
    assert bm.rows() == [[px(0x2040c0)]]


def test_the_codec_badges_take_the_muted_grey(fw, bitmaps, scheme):
    theme(fw)
    badge, delete = bitmaps(fw, [[RED_PX, CLEAR]]), bitmaps(fw, [[RED_PX, CLEAR]])
    image_add(fw, b'img_flac', badge)
    image_add(fw, b'img_delete', delete)
    assert badge.rows() == [[px(scheme('muted')), CLEAR]]
    assert delete.get(0, 0) == (luma(BLUE[RED]),) * 3 + (255,)  # an action, not a badge


def test_a_card_app_s_streaming_icon_greys(fw, bitmaps):
    """icons.addon_mono: as Streaming's own stream_ icons."""
    theme(fw)
    bm = bitmaps(fw, [[px(0x2040c0), (0x80, 0x40, 0x20, 0x80)]])
    image_add(fw, b'file:///mnt/mmc/.q2apps/radio/icon.png', bm)
    assert bm.rows() == [[(luma(0x2040c0),) * 3 + (255,), (luma(0x804020),) * 3 + (0x80,)]]


def test_a_card_app_s_other_files_keep_their_colours(fw, bitmaps):
    theme(fw)
    bm = bitmaps(fw, [[px(0x2040c0)]])
    image_add(fw, b'file:///mnt/mmc/.q2apps/radio/cover.png', bm)
    assert bm.rows() == [[px(0x2040c0)]]


# OLED's own images, stock's marks it draws itself, and its tiles.

@pytest.mark.parametrize('light', ['0', '1'])
def test_the_theme_s_own_images_are_final(fw, bitmaps, light):
    """pages.own.art_final: an image named by the theme's art prefix (tools/art.py's oled_) keeps its colours."""
    theme(fw, LIGHT=light)
    bm = bitmaps(fw, [[RED_PX, WHITE]])
    image_add(fw, b'oled_vol_lo_d', bm)
    assert bm.rows() == [[RED_PX, WHITE]]


@pytest.mark.parametrize('name', [b'list_into', b'select', b'unselect', b'switch_on', b'switch_off', b'gain_low',
                                  b'gain_high'])
def test_stock_s_chevron_radio_rings_and_switches_are_emptied(fw, bitmaps, name):
    """paint_into, paint_radio and switch_mark draw them instead: the images draw nothing, and are not opaque."""
    theme(fw)
    bm = bitmaps(fw, fill(3, 2, WHITE), flags=OPAQUE | PREMULTI)
    image_add(fw, name, bm)
    assert bm.rows() == fill(3, 2, CLEAR) and bm.flags == PREMULTI and bm.padding() == {b'\x5a' * 8}


@pytest.mark.parametrize('name', [b'list_folder', b'list_song'])
def test_the_grey_tiles_shrink_to_the_placeholder_size_in_their_middle(fw, bitmaps, theme_t, name):
    """The theme's art placeholder size (theme.xml's list art placeholder), square, as much room either side."""
    theme(fw)
    size = theme_t('art_placeholder', C.c_short)
    bm = bitmaps(fw, fill(48, 48, px(0x606060)), flags=OPAQUE)
    image_add(fw, name, bm)
    rows = bm.rows()
    inside = [(x, y) for y in range(48) for x in range(48) if rows[y][x][3]]
    xs, ys = {x for x, _ in inside}, {y for _, y in inside}
    assert 0 < size < 48 and len(inside) == size * size and max(xs) - min(xs) == max(ys) - min(ys) == size - 1
    assert abs(min(xs) - (47 - max(xs))) <= 1 and abs(min(ys) - (47 - max(ys))) <= 1  # centred
    assert {rows[y][x] for x, y in inside} == {px(0x606060)}
    assert bm.flags & OPAQUE == 0


def shrink(fw, bm, to, a=3):
    fw.fn('navigation__shrink_centred', None, C.c_void_p, C.c_int, C.c_int)(bm.header, to, a)


def test_a_shrunk_pixel_is_the_alpha_weighted_mean_of_the_block_it_covers(fw, bitmaps):
    """A 4 x 4 into 2 x 2 in its middle: each pixel from a 2 x 2 block; clear pixels add no colour."""
    rows = fill(4, 4, CLEAR)
    rows[0][0], rows[0][1] = (200, 0, 0, 255), (0, 0, 0, 0)  # block (0, 0): one red, one clear...
    rows[1][0], rows[1][1] = (100, 0, 0, 255), (0, 0, 0, 0)
    rows[0][2] = rows[0][3] = rows[1][2] = rows[1][3] = (10, 20, 30, 255)
    bm = bitmaps(fw, rows, fmt=1)
    shrink(fw, bm, 2)
    out = bm.rows()
    assert out[1][1] == (150, 0, 0, 127) and out[1][2] == (10, 20, 30, 255)
    assert out[2][1] == out[2][2] == CLEAR  # blocks of clear pixels
    assert all(out[y][x] == CLEAR for y in range(4) for x in range(4) if not (1 <= x <= 2 and 1 <= y <= 2))


def test_a_premultiplied_shrink_is_the_plain_mean(fw, bitmaps):
    rows = fill(4, 4, CLEAR)
    rows[0][0] = (200, 0, 0, 255)
    bm = bitmaps(fw, rows, flags=PREMULTI | OPAQUE)
    shrink(fw, bm, 2)
    assert bm.get(1, 1) == (50, 0, 0, 63) and bm.flags == PREMULTI


@pytest.mark.parametrize('w, h, to', [(65, 65, 38), (48, 48, 48), (48, 48, 0), (30, 48, 38)])
def test_shrink_leaves_what_it_can_t_shrink(fw, bitmaps, w, h, to):
    bm = bitmaps(fw, fill(w, h, WHITE), flags=OPAQUE)
    shrink(fw, bm, to)
    assert bitmaps.locked == [] and bm.flags == OPAQUE


# Rounded thumbnails, round discs.

SLDP = b'file:///mnt/mmc/.sldp/1f3a.jpg'


@pytest.mark.parametrize('name', [SLDP, b'file:///mnt/mmc/.downpic/fd0007.jpg'])
def test_stock_s_thumbnails_take_the_list_art_s_corners(fw, st, bitmaps, name):
    """list.art.radius (theme.xml) is on screen at the set's art size: at 166 px, that share of it."""
    theme(fw)
    bm = bitmaps(fw, fill(166, 166, WHITE), flags=OPAQUE)
    image_add(fw, name, bm)
    alpha = [[p[3] for p in row] for row in bm.rows()]
    r = (166 * ART_RADIUS + GEOMETRY['art'] // 2) // GEOMETRY['art']  # round_corners'
    for y, x in [(0, 0), (0, 165), (165, 0), (165, 165)]:
        assert alpha[y][x] == 0
    assert alpha[r][r] == 255 and alpha[0][r] == 255 and alpha[r][0] == 255  # past the arc
    assert alpha[83][83] == 255 and all(row == row[::-1] for row in alpha) and alpha == alpha[::-1]
    assert any(0 < a < 255 for a in alpha[0][:r])  # an antialiased step on the arc
    assert alpha[r - 2][0] < 255 == alpha[r - 1][0]  # the arc runs down the side to the radius, meeting it within a pixel
    assert all(alpha[y][:r] == sorted(alpha[y][:r]) for y in range(r))  # rising inwards along each row
    assert bm.flags & OPAQUE == 0 and {p[:3] for row in bm.rows() for p in row} == {(255, 255, 255)}


@pytest.mark.parametrize('name', [b'file:///mnt/mmc/Music/a.jpg', b'file:///mnt/mmc/.downpic/cover.jpg'])
def test_other_covers_are_not_rounded(fw, bitmaps, name):
    theme(fw)
    bm = bitmaps(fw, fill(20, 20, WHITE), flags=OPAQUE)
    image_add(fw, name, bm)
    assert bm.rows() == fill(20, 20, WHITE)


def disc(n, edge):
    """A placeholder disc: clear corners round an opaque middle whose top edge's middle is edge."""
    rows = fill(n, n, px(0x404040))
    for y in range(n):
        for x in range(n):
            if (2 * x + 1 - n) ** 2 + (2 * y + 1 - n) ** 2 > n * n:
                rows[y][x] = (0x40, 0x40, 0x40, 0)
    rows[0][n // 2] = edge
    return rows


def test_a_placeholder_disc_stays_round(fw, bitmaps):
    theme(fw)
    bm = bitmaps(fw, disc(10, px(0x707070)))
    before = bm.rows()
    image_add(fw, b'play_defaultcover', bm)
    assert bm.rows() == before


# Light Mode's flip of glyph images.

@pytest.mark.parametrize('premul', [0, PREMULTI])
def test_light_mode_flips_a_glyph_image(fw, bitmaps, premul):
    """White marks on clear turn the light half's ink, a half-covered pixel its own share of it."""
    theme(fw, LIGHT='1')
    edge = (0x80, 0x80, 0x80, 0x80) if premul else (255, 255, 255, 0x80)
    bm = bitmaps(fw, [[WHITE, edge, CLEAR]], flags=premul)
    image_add(fw, b'list_more', bm)
    flipped = (INK * 0x80 // 255,) * 3 if premul else (INK,) * 3
    assert bm.rows() == [[(INK, INK, INK, 255), (*flipped, 0x80), CLEAR]]


def test_light_mode_keeps_a_dark_disc_s_white_glyph(fw, bitmaps):
    """Mostly dark (a disc or plate): the white glyph on it stays white."""
    theme(fw, LIGHT='1')
    bm = bitmaps(fw, [[px(0x303030)] * 3 + [WHITE]])
    image_add(fw, b'list_disc', bm)
    assert bm.rows() == [[px(0x303030)] * 3 + [WHITE]]


def test_light_mode_keeps_a_coloured_image(fw, bitmaps):
    theme(fw, LIGHT='1')
    bm = bitmaps(fw, [[px(0x2080ff), WHITE]])
    image_add(fw, PLAIN, bm)
    assert bm.rows() == [[px(0x2080ff), WHITE]]


def test_light_mode_flips_a_quick_control_s_inactive_disc_whole(fw, bitmaps):
    theme(fw, LIGHT='1')
    rows = [[px(0x303030)] * 3 + [WHITE]]
    control, plate = bitmaps(fw, rows), bitmaps(fw, rows)
    image_add(fw, STRINGS['DROPDOWN_IMAGE'].encode() + b'bt_off', control)
    image_add(fw, b'list_disc', plate)
    assert control.get(3, 0) == (INK, INK, INK, 255) and control.get(0, 0)[0] > 0x30
    assert plate.rows() == rows


def test_light_mode_leaves_an_image_the_accent_mapped(fw, bitmaps):
    theme(fw, LIGHT='1')
    bm = bitmaps(fw, [[RED_PX, WHITE, WHITE, WHITE]])
    image_add(fw, PLAIN, bm)
    assert bm.rows()[0][1:] == [WHITE] * 3


# The colour hooks.

def style_color(fw, stock, name, fallback=0, inner=None):
    """The colour hook over stock's answer, a color_t: what the widget gets. inner, if set, is a property stock
    asks the hook for again from inside its own answer (a mutable style through its default one): then the
    outer answer and the inner one."""
    hook = fw.fn('ringnav_style_color', C.c_void_p, C.c_void_p, C.c_void_p, C.c_char_p, C.c_uint)
    nested = []

    def stock_color(out, style, n, fb, *_):
        C.c_uint.from_address(out).value = stock
        if inner and not nested:
            nested.append(1)
            again = C.c_uint(0)
            hook(C.addressof(again), style, inner, fb)
            nested.append(again.value)
    fw.mock('stock_color_trampoline', stock_color)
    out = C.c_uint(0)
    assert hook(C.addressof(out), 0x7000, name, fallback) == C.addressof(out)
    return (out.value, nested[1]) if inner else out.value


@pytest.mark.parametrize('name, tone', [(b'bg_color', LIGHT), (b'border_color', LIGHT), (b'fg_color', LIGHT),
                                        (b'text_color', RED), (b'highlight_text_color', RED)])
@pytest.mark.parametrize('alpha', [0xff, 0x40])
def test_stock_red_takes_the_light_tone_and_text_the_red_one(fw, name, tone, alpha):
    theme(fw)
    assert style_color(fw, rgba(STOCK_RED, alpha), name) == rgba(BLUE[tone], alpha)


@pytest.mark.parametrize('color', [rgba(0x808080), rgba(0x2050c0), rgba(0xffffff, 0x80)])
def test_other_colours_stay(fw, color):
    theme(fw)
    assert style_color(fw, color, b'bg_color') == style_color(fw, color, b'text_color') == color


def test_an_edit_s_selected_text_keeps_stock_s_colour(fw):
    theme(fw, LIGHT='1')
    assert style_color(fw, rgba(STOCK_RED), b'selected_text_color') == rgba(STOCK_RED)
    assert style_color(fw, rgba(0x101010), b'selected_text_color') == rgba(0x101010)  # not flipped to paper


@pytest.mark.parametrize('name, stock, light', [(b'text_color', 0xffffff, INK), (b'bg_color', 0x000000, PAPER)])
def test_only_the_outermost_ask_maps(fw, name, stock, light):
    """Light Mode's flip is not its own inverse: a style that asks again from inside its answer is mapped once."""
    theme(fw, LIGHT='1')
    once = style_color(fw, rgba(stock), name)
    outer, inner = style_color(fw, rgba(stock), name, inner=name)
    assert once == outer == rgba(light * 0x010101) and inner == rgba(stock)


# The style hooks read where the paint is: widgets as stock's calls see them.

class Node:
    def __init__(self, name, kind, parent):
        self.block = C.create_string_buffer(0x400)
        self.p = C.addressof(self.block)
        self.name, self.kind, self.parent = name, kind, parent
        self.props, self.children, self.text, self.visible = {}, [], None, 1


def wide(s, n=None):
    """A wide string from the C, n characters or to its NUL."""
    if not s:
        return None
    out = []
    while (n is None or len(out) < n) and (c := C.c_uint.from_address(s + 4 * len(out)).value):
        out.append(chr(c))
    return ''.join(out)


def rect(r):
    return tuple((C.c_int * 4).from_address(r)) if r else None


class World:
    """Stock's widgets: each a zeroed block holding its box and its parent, with a name, a type, properties, a
    text, its visibility and children, which stock's calls read and write as the C makes them. Timers, handlers
    and invalidations are kept."""
    def __init__(self, fw):
        self.fw, self.nodes, self.strs, self.wides = fw, {}, {}, []
        self.handlers, self.timers, self.invalid, self.removed = [], [], [], []
        self.wm = self.add(None, 'window_manager', 'window_manager', box=(0, 0, 375, 320))
        self.top = 0
        m = fw.mock
        m('window_manager', lambda *_: self.wm)
        m('window_manager_get_top_window', lambda *_: self.top)
        m('widget_lookup', self.lookup)
        m('widget_get_prop_str', self.get_str)
        m('widget_get_prop_int', self.get_int)
        m('widget_get_prop_bool', self.get_int)
        m('widget_set_prop_int', lambda w, k, v, *_: self.set(w, k, C.c_int(v).value))
        m('widget_set_prop_str', lambda w, k, v, *_: self.set(w, k, C.string_at(v).decode()))
        m('widget_get_type', lambda w, *_: self.cstr(self.nodes[w].kind) if w in self.nodes else 0)
        m('widget_count_children', lambda w, *_: len(self.nodes[w].children) if w in self.nodes else 0)
        m('widget_get_child', self.child)
        m('widget_get_text', self.get_text)
        m('widget_get_text_utf8', self.get_utf8)
        m('widget_set_text_utf8', lambda w, s, *_: self.set_text(w, C.string_at(s).decode()))
        m('widget_get_visible', lambda w, *_: self.nodes[w].visible if w in self.nodes else 0)
        m('widget_set_visible', lambda w, v, *_: self.node(w).__setattr__('visible', v))
        m('widget_move_resize', lambda w, x, y, ww, h, *_: self.box(w, x, y, ww, h))
        m('widget_resize', lambda w, ww, h, *_: self.box(w, self.at(w)[0], self.at(w)[1], ww, h))
        m('widget_set_name', lambda w, s, *_: self.node(w).__setattr__('name', C.string_at(s).decode()))
        m('widget_use_style', lambda w, s, *_: self.set(w, 'style', C.string_at(s).decode()))
        for kind in ('hscroll_label', 'label', 'image', 'button'):
            m(f'{kind}_create', lambda parent, x, y, ww, h, *_, kind=kind: self.add(parent, None, kind, (x, y, ww, h)))
        m('widget_on', lambda w, evt, fn, ctx, *_: self.handlers.append((w, evt, fn, ctx)) or len(self.handlers))
        m('timer_add', lambda fn, ctx, ms, *_: self.timers.append((fn, ctx, ms)) or len(self.timers))
        m('widget_add_timer', lambda w, fn, ms, *_: self.timers.append((fn, w, ms)) or len(self.timers))
        m('timer_remove', lambda t, *_: self.removed.append(t))
        m('widget_invalidate_force', lambda w, r, *_: self.invalid.append((w, rect(r))))
        m('widget_set_sensitive', lambda w, v, *_: self.set(w, 'sensitive', v & 0xff))
        m('widget_factory_create_widget', lambda f, kind, parent, x, y, ww, *_: self.add(parent, None, C.string_at(kind).decode(), (x, y, ww, 0)))

    def add(self, parent, name, kind='view', box=(0, 0, 375, 320), text=None, **props):
        node = Node(name, kind, parent)
        node.props.update(props)
        node.text = text
        self.nodes[node.p] = node
        struct.pack_into('<iiii', node.block, W_X, *box)
        if parent:
            struct.pack_into('<Q', node.block, W_PARENT, parent)
            self.nodes[parent].children.append(node.p)
        return node.p

    def window(self, name, kind='window', box=(0, 0, 375, 320), top=True, **props):
        win = self.add(self.wm, name, kind, box, **props)
        if top:
            self.top = win
        return win

    def node(self, w):
        return self.nodes.get(w) or Node(None, None, 0)

    def __getitem__(self, w):
        return self.nodes[w]

    def child(self, w, i, *_):
        kids = self.nodes[w].children if w in self.nodes else []
        return kids[i] if 0 <= i < len(kids) else 0

    def at(self, w):
        return struct.unpack_from('<iiii', self.nodes[w].block, W_X)

    def box(self, w, x, y, ww, h):
        if w in self.nodes:
            struct.pack_into('<iiii', self.nodes[w].block, W_X, *(C.c_int(v).value for v in (x, y, ww, h)))

    def cstr(self, s):
        if s not in self.strs:
            self.strs[s] = C.create_string_buffer(s.encode())
        return C.addressof(self.strs[s])

    def find(self, parent, name, deep=True):
        for child in self.nodes[parent].children:
            if self.nodes[child].name == name:
                return child
            if deep and (found := self.find(child, name)):
                return found
        return 0

    def lookup(self, parent, name, deep, *_):
        return self.find(parent, C.string_at(name).decode(), bool(deep)) if parent in self.nodes else 0

    def get_str(self, w, key, default, *_):
        key, node = C.string_at(key).decode(), self.nodes.get(w)
        value = None if node is None else node.name if key == 'name' else node.props.get(key)
        return self.cstr(value) if isinstance(value, str) else default

    def get_int(self, w, key, default, *_):
        value = self.nodes[w].props.get(C.string_at(key).decode()) if w in self.nodes else None
        return value if isinstance(value, int) else C.c_int(default).value

    def set(self, w, key, value):
        self.node(w).props[C.string_at(key).decode() if isinstance(key, int) else key] = value

    def get_text(self, w, *_):
        text = self.nodes[w].text if w in self.nodes else None
        if text is None:
            return 0
        self.wides.append((C.c_uint * (len(text) + 1))(*map(ord, text), 0))
        return C.addressof(self.wides[-1])

    def get_utf8(self, w, out, size, *_):
        if w not in self.nodes:
            return 1  # RET_BAD_PARAMS
        data = (self.nodes[w].text or '').encode()[:size - 1]
        C.memmove(out, data + b'\0', len(data) + 1)
        return 0

    def set_text(self, w, s):
        self.node(w).text = s

    def on(self, w, event):
        """w's handlers for event, each run with its context as stock would."""
        return [lambda f=fn, c=ctx: C.CFUNCTYPE(C.c_int, C.c_void_p, C.c_void_p)(f)(c, 0)
                for target, evt, fn, ctx in self.handlers if target == w and evt == event]

    def fire(self, timer):
        """Run timer (timer_add's number) as stock would: its answer."""
        fn, ctx, _ = self.timers[timer - 1]
        info = C.create_string_buffer(0x40)
        struct.pack_into('<Q', info, O['TIMER_CTX'], ctx or 0)
        return C.CFUNCTYPE(C.c_int, C.c_void_p)(fn)(C.addressof(info))


@pytest.fixture
def world(fw):
    return World(fw)


def test_the_theme_s_scrollbar_draws_its_own_thumb(fw, st, world):
    """list.scrollbar: paint_scrollbar draws the thumb and track, so stock's bar goes clear; its border stays."""
    theme(fw)
    bar = world.add(world.window('list_page'), 'bar', 'scroll_bar_m')
    st('paint_w', C.c_void_p).value = bar
    assert style_color(fw, rgba(0x808080), b'fg_color') == rgba(0x808080, 0)
    assert style_color(fw, rgba(0x404040), b'bg_color') == rgba(0x404040, 0)
    assert style_color(fw, rgba(0x404040), b'border_color') == rgba(0x404040)


@pytest.mark.parametrize('enabled, disabled', [((1, 1), 0), ((1, 0), 1), ((0, 1), 1)])
def test_a_playlist_row_s_delete_and_rename_grey_under_a_disabled_parent(fw, st, world, scheme, enabled, disabled):
    """Stock's #aaaaaa secondary grey, which the scheme's muted then takes, under a disabled parent; else white."""
    theme(fw)
    win = world.window('playlist_page', enable=enabled[0])
    label = world.add(world.add(win, 'view_bottom', enable=enabled[1]), 'label_del', 'label')
    st('paint_w', C.c_void_p).value = label
    grey = scheme('muted') if disabled else 0xffffff
    assert style_color(fw, rgba(0x123456, 0xc0), b'text_color') == rgba(grey, 0xc0)


def selection(world, st):
    """A list's selected row and a label on it, being painted: (surface, row, label)."""
    surface = world.add(world.window('list_page'), 'list', 'scroll_view')
    row = world.add(surface, 'row', 'list_item')
    label = world.add(row, 'title', 'label')
    st('sel_w', C.c_void_p).value = surface
    st('paint_w', C.c_void_p).value = label
    return surface, row, label


def test_the_selection_s_text_is_the_scheme_s(fw, st, world, scheme):
    theme(fw)
    _, row, _ = selection(world, st)
    st('sel_accent', C.c_void_p).value = row
    assert style_color(fw, rgba(0x808080, 0x80), b'text_color') == rgba(scheme('sel_text'), 0x80)
    st('sel_accent', C.c_void_p).value = 0
    assert style_color(fw, rgba(0x808080, 0x80), b'text_color') == rgba(0x808080, 0x80)


def test_stock_s_secondary_text_is_the_scheme_s_quieter_grey(fw, st, world, scheme):
    theme(fw)
    selection(world, st)
    assert style_color(fw, rgba(0xaaaaaa), b'text_color') == rgba(scheme('muted')) != rgba(0xaaaaaa)


def gradient_hook(fw, colors, name=b'bg_color', own=False):
    """A style whose vtable's get_gradient fills the caller's gradient_t with colors (its own cached one when
    own): the stops after the hook, the gradient it answered, and what must outlive the call. A gradient_t is
    0x4c bytes, 8 stops; the room past it is someone else's."""
    out = C.create_string_buffer(0x60)
    cached = C.create_string_buffer(0x60)
    target = cached if own else out
    getter = C.CFUNCTYPE(C.c_void_p, C.c_void_p, C.c_char_p, C.c_void_p)(
        lambda style, n, o: struct.pack_into(f'<I{2 * len(colors)}I', target, 8, len(colors),
                                             *(v for i, c in enumerate(colors) for v in (c, i))) or C.addressof(target))
    vtable = (C.c_void_p * 4)(0, 0, 0, C.cast(getter, C.c_void_p).value)
    style = C.c_void_p(C.addressof(vtable))
    got = fw.fn('ringnav_style_gradient', C.c_void_p, C.c_void_p, C.c_char_p, C.c_void_p)(
        C.addressof(style), name, C.addressof(out))
    stops = struct.unpack_from(f'<{2 * len(colors)}I', target, 12)
    return list(stops[::2]), got, (C.addressof(target), getter, vtable)


def test_a_gradient_s_stops_take_the_light_tone(fw):
    theme(fw)
    stops, got, keep = gradient_hook(fw, [rgba(STOCK_RED), rgba(0x808080), rgba(STOCK_RED, 0x40)])
    assert stops == [rgba(BLUE[LIGHT]), rgba(0x808080), rgba(BLUE[LIGHT], 0x40)]
    assert got == keep[0]


def test_a_gradient_maps_its_eight_stops_and_nothing_past_them(fw):
    theme(fw)
    stops, _, _ = gradient_hook(fw, [rgba(STOCK_RED)] * 10)  # a count past what gradient_t holds
    assert stops == [rgba(BLUE[LIGHT])] * 8 + [rgba(STOCK_RED)] * 2


def test_a_style_s_own_cached_gradient_is_left(fw):
    theme(fw)
    stops, got, keep = gradient_hook(fw, [rgba(STOCK_RED)], own=True)
    assert stops == [rgba(STOCK_RED)] and got == keep[0]


def test_no_style_has_no_gradient(fw):
    theme(fw)
    hook = fw.fn('ringnav_style_gradient', C.c_void_p, C.c_void_p, C.c_char_p, C.c_void_p)
    out = C.create_string_buffer(0x4c)
    assert hook(None, b'bg_color', C.addressof(out)) is None
    vtable = (C.c_void_p * 4)()
    style = C.c_void_p(C.addressof(vtable))  # a vtable with no get_gradient
    assert hook(C.addressof(style), b'bg_color', C.addressof(out)) is None


def test_a_gradient_asked_for_a_colour_is_left_to_the_colour_hook(fw):
    """style_get_color asks for the gradient first: the colour hook alone picks the tone, text's or not."""
    theme(fw)
    seen = []

    def stock_color(out, style, name, fb, *_):
        seen.append(gradient_hook(fw, [rgba(STOCK_RED)])[0])
        C.c_uint.from_address(out).value = rgba(STOCK_RED)
    fw.mock('stock_color_trampoline', stock_color)
    out = C.c_uint(0)
    fw.fn('ringnav_style_color', C.c_void_p, C.c_void_p, C.c_void_p, C.c_char_p, C.c_uint)(
        C.addressof(out), 0x7000, b'text_color', 0)
    assert seen == [[rgba(STOCK_RED)]] and out.value == rgba(BLUE[RED])


# The background hook: what it paints, under what it leaves to stock.

class Canvas:
    """A canvas and its lcd, whose fill colour stock's setter writes: the fills (x, y, w, h, color), the rounded
    boxes (rect, color, radius), the theme images drawn (name, dst rect; every image 20 x 10) and the text
    (text, rect, colour, (face, px))."""
    def __init__(self, fw, fill=0xff123456, lcd=True):
        self.lcd = C.create_string_buffer(0x200)
        self.raw = C.create_string_buffer(0x100)
        if lcd:
            struct.pack_into('<Q', self.raw, CANVAS_LCD, C.addressof(self.lcd))
        struct.pack_into('<I', self.lcd, LCD_FILL_COLOR, fill)
        self.p = C.addressof(self.raw)
        self.fills, self.rounded, self.arts, self.texts, self.loaded, self.names = [], [], [], [], {}, []
        self.text_color, self.font = 0, (None, 0)
        fw.mock('canvas_set_fill_color', lambda c, color, *_: struct.pack_into('<I', self.lcd, LCD_FILL_COLOR, color & 0xffffffff))
        fw.mock('canvas_fill_rect', lambda c, x, y, w, h, *_: self.fills.append(
            (*(C.c_int(v).value for v in (x, y, w, h)), self.fill)))
        fw.mock('canvas_fill_rounded_rect', lambda c, r, bg, color, radius, *_: self.rounded.append(
            (rect(r), C.c_uint.from_address(color).value, radius)))
        fw.mock('canvas_set_text_color', lambda c, color, *_: setattr(self, 'text_color', color & 0xffffffff))
        fw.mock('canvas_set_font', lambda c, face, px, *_: setattr(self, 'font', (C.string_at(face).decode() if face else None, px)))
        fw.mock('canvas_draw_text_in_rect', lambda c, s, n, r, *_: self.texts.append((wide(s, n), rect(r), self.text_color, self.font)))
        fw.mock('canvas_measure_text', lambda c, s, n, *_: 10.0 * n)  # 10 px a character
        fw.mock('widget_load_image', self.load)
        fw.mock('canvas_draw_image', lambda c, bm, src, dst, *_: self.arts.append((self.loaded.get(bm), rect(dst))))
        self.clip = (0, 0, 375, 480)  # the whole screen
        fw.mock('canvas_get_clip_rect', lambda c, r, *_: C.memmove(r, struct.pack('<4i', *self.clip), 16) and 0)

    @property
    def fill(self):
        return struct.unpack_from('<I', self.lcd, LCD_FILL_COLOR)[0]

    def load(self, w, name, bitmap, *_):
        self.loaded[bitmap] = C.string_at(name).decode()
        self.names.append(self.loaded[bitmap])
        C.memmove(bitmap, struct.pack('<ii', 20, 10), 8)
        return 0

    def art(self, name):
        return [dst for n, dst in self.arts if n == name]

    def rows(self, x=None, w=None):
        """The one-pixel-high fills, by row: {y: color}, those at x, w wide if given."""
        return {y: c for fx, y, fw_, h, c in self.fills if h == 1 and (x is None or fx == x) and (w is None or fw_ == w)}


def paint_bg(fw, w, canvas, stock=0):
    """The hook over stock's own background paint, which answers stock: the hook's answer."""
    fw.mock('stock_paint_bg_trampoline', lambda *_: stock)
    return fw.fn('ringnav_paint_bg', C.c_int, C.c_void_p, C.c_void_p)(w, canvas.p)


PH_GREY = O['PH_GREY']


def press_row(world, width=375, h=52, style='s_btn_listitem', state='pressed', tile=False):
    """A list row and the button over its top, pressed: (row, button)."""
    surface = world.add(world.window('list_page'), 'list', 'scroll_view')
    row = world.add(surface, 'row', 'list_item', box=(0, 0, width, h))
    button = world.add(row, 'btn', 'button', box=(0, 0, width // 3 if tile else width, h), style=style,
                       state_for_style=state)
    return row, button


def test_a_pressed_row_is_grey_across_the_whole_row(fw, world):
    """The whole row, square; the button's own box is skipped."""
    theme(fw)
    row, button = press_row(world)
    canvas = Canvas(fw)
    assert paint_bg(fw, row, canvas, stock=5) == 5  # the row's own (clear) background is still stock's
    _, _, w, h = world.at(row)
    assert canvas.rounded == [((0, 0, w, h), PH_GREY, 0)]
    assert paint_bg(fw, button, canvas, stock=5) == 0 and len(fw.calls('stock_paint_bg_trampoline')) == 1


@pytest.mark.parametrize('style', ['s_btn_forwardspeed', 's_listitem_black'])
def test_the_other_list_buttons_press_grey_too(fw, world, style):
    theme(fw)
    row, _ = press_row(world, style=style)
    canvas = Canvas(fw)
    paint_bg(fw, row, canvas)
    assert [c for _, c, _ in canvas.rounded] == [PH_GREY]


@pytest.mark.parametrize('style, state', [('s_btn_white', 'pressed'), ('s_btn_listitem', 'normal')])
def test_an_unpressed_or_other_button_is_stock_s(fw, world, style, state):
    theme(fw)
    row, button = press_row(world, style=style, state=state)
    canvas = Canvas(fw)
    paint_bg(fw, row, canvas)
    paint_bg(fw, button, canvas)
    assert canvas.rounded == [] and len(fw.calls('stock_paint_bg_trampoline')) == 2


def test_a_released_row_repaints_its_edges_once_after(fw, world):
    """The press invalidated the whole surface, the release only the button: the row's edges wait for a
    repaint of the top window, once."""
    theme(fw)
    row, button = press_row(world)
    canvas = Canvas(fw)
    paint_bg(fw, row, canvas)
    world.set(button, 'state_for_style', 'normal')
    paint_bg(fw, row, canvas)
    (fn, _, ms), = world.timers
    assert ms == 0 and world.fire(1) == RET_OK and world.invalid == [(world.top, None)]
    paint_bg(fw, row, canvas)
    assert len(world.timers) == 1  # once


def test_a_pressed_tile_is_grey_inside_its_card(fw, world):
    """Square, as the rows' grey."""
    theme(fw)
    row, tile = press_row(world, tile=True)
    canvas = Canvas(fw)
    paint_bg(fw, row, canvas)
    assert canvas.rounded == []  # the tile draws its own
    assert paint_bg(fw, tile, canvas, stock=5) == 0
    inset = O['TILE_INSET']
    assert canvas.rounded == [((inset, inset, 125 - 2 * inset, 52 - 2 * inset), PH_GREY, 0)]


def test_a_row_its_own_target_draws_its_grey_as_its_background(fw, world):
    """A page row (its own touch target, no button over it): its clear background is the grey's."""
    theme(fw)
    surface = world.add(world.window('list_page'), 'list', 'scroll_view')
    row = world.add(surface, 'row', 'list_item', box=(0, 0, 375, 52), style='s_listitem_black', state_for_style='pressed')
    world.add(row, 'title', 'label', box=(20, 0, 300, 52))
    canvas = Canvas(fw)
    assert paint_bg(fw, row, canvas, stock=5) == 0 and canvas.rounded == [((0, 0, 375, 52), PH_GREY, 0)]


def test_a_childless_page_is_no_row(fw, world):
    theme(fw)
    surface = world.add(world.window('playing_page'), 'list', 'scroll_view')
    page = world.add(surface, 'vis', 'list_item', box=(0, 0, 375, 52), style='s_listitem_black', state_for_style='pressed')
    canvas = Canvas(fw)
    paint_bg(fw, page, canvas)
    assert canvas.rounded == []


# Album grids' tiles.

def album_grid(world, window='album_page', w=110, h=160, kind='button'):
    tile = world.add(world.add(world.window(window), 'grid', 'scroll_view'), 'tile', kind, box=(0, 0, w, h))
    cover = world.add(tile, 'cover', 'image', box=(0, 0, 110, 110))
    overlay = world.add(cover, 'playing', 'image', box=(0, 0, 110, 110))
    badge = world.add(tile, 'badge', 'image', box=(86, 4, 20, 20))
    name = world.add(tile, 'name', 'hscroll_label', box=(0, 115, 110, 20))
    return tile, cover, overlay, badge, name


@pytest.mark.parametrize('window', ['album_page', 'artistinfo_page'])
def test_an_album_tile_insets_its_cover_and_name_inside_the_card(fw, world, window):
    theme(fw)
    fw.mock('image_set_draw_type', lambda w, t, *_: world.set(w, 'draw_type', t))
    tile, cover, overlay, badge, name = album_grid(world, window)
    paint_bg(fw, tile, Canvas(fw))
    pad = O['TILE_INSET'] + O['TILE_CLEARANCE']
    assert world.at(cover) == (pad, pad, 110 - 2 * pad, 110 - 2 * pad)  # the same margin on all four sides
    assert world.at(overlay) == (0, 0, 110 - 2 * pad, 110 - 2 * pad)  # nested: at the cover's own origin
    assert world.at(name) == (pad, 115 - O['TILE_INSET'], 110 - 2 * pad, 20)  # under the cover, as wide
    assert world.at(badge) == (86, 4, 20, 20)
    assert world[cover].props['draw_type'] == world[overlay].props['draw_type'] == O['IMAGE_DRAW_SCALE_DOWN']
    moves = len(fw.calls('widget_move_resize'))
    paint_bg(fw, tile, Canvas(fw))
    assert len(fw.calls('widget_move_resize')) == moves and world.at(name)[1] == 115 - O['TILE_INSET']  # once


@pytest.mark.parametrize('window, w, h, kind', [('home_page', 110, 160, 'button'), ('album_page', 110, 150, 'button'),
                                                 ('album_page', 100, 160, 'button'), ('album_page', 110, 160, 'view')])
def test_anything_else_keeps_its_layout(fw, world, window, w, h, kind):
    theme(fw)
    tile, cover, *_ = album_grid(world, window, w, h, kind)
    paint_bg(fw, tile, Canvas(fw))
    assert world.at(cover) == (0, 0, 110, 110) and fw.calls('widget_move_resize') == []


# A dialog's labels.

def test_another_dialog_s_labels_stay(fw, world):
    theme(fw)
    win = world.window('msginfo_dialog', 'dialog')
    prompt = world.add(win, 'prompt', 'label', box=(30, 60, 315, 40))
    paint_bg(fw, win, Canvas(fw))
    assert world.at(prompt) == (30, 60, 315, 40)


def test_a_toast_rises_and_fades_in_then_sinks_and_fades_out_over_the_page(fw, world):
    """docs/oled-design.md#overlay-motion: the pill comes up 10 px and fades in over 200 ms cubic out (the dialog's
    opacity, set by the shared frame timer: stock's widget_paint skips a widget under opacity 9, so the toast's own
    paint never could; its line moved with the pill), nothing on its first frame; once its dialog goes, the page under
    paints it sinking 4 px and fading over 150 ms cubic in, then nothing."""
    theme(fw)
    now = [10000]
    fw.mock('time_now_ms', lambda *_: now[0])
    page = world.window('settings_page')
    win = world.window('msginfo_dialog', 'dialog')
    line = world.add(win, 'line', 'label', box=(30, 60, 315, 40), text='Saved')
    opacity = lambda: [a[1] for _, a in fw.calls('widget_set_opacity') if a[0] == win][-1]  # noqa: E731
    frame_fn = C.cast(fw.lib.ui_kit__mo_frame, C.c_void_p).value

    def frame():
        """The shared frame timer's tick (ui_kit.c mo_frame), then the toast's paint only as stock would: at 9 up."""
        world.fire(max(i + 1 for i, (fn, _, _) in enumerate(world.timers) if fn == frame_fn))
        canvas = Canvas(fw)
        if opacity() >= 9:
            paint_bg(fw, win, canvas)
        return canvas

    paint_bg(fw, win, Canvas(fw))  # its first frame, at stock's 255
    assert not world[line].visible and opacity() == 0
    now[0] += 100  # 875 of the way in
    frame()
    assert world[line].visible and world.at(line)[1] == 236 + 1 and opacity() == 223
    now[0] += 100
    canvas = frame()
    assert world.at(line)[1] == 236 and opacity() == 255 and canvas.rounded[0][0][1] == 236
    for gone in world.on(win, O['EVT_DESTROY']):  # toast_gone and ovm_bind's ovm_gone
        gone()
    world.top = page
    leave = fw.fn('feedback_toast_leave', None, C.c_void_p, C.c_void_p)
    now[0] += 75  # half way out, cubic in: 1/8 gone
    canvas = Canvas(fw)
    leave(page, canvas.p)
    (box, ink, _), = canvas.rounded
    assert box[1] == 236 and ink >> 24 == 223 and [t[0] for t in canvas.texts] == ['Saved']
    now[0] += 50  # 125 of 150 ms: 577 gone, 2 px down
    canvas = Canvas(fw)
    leave(page, canvas.p)
    assert canvas.rounded[0][0][1] == 236 + 2 and canvas.rounded[0][1] >> 24 == 107
    now[0] += 25
    canvas = Canvas(fw)
    leave(page, canvas.p)
    assert canvas.rounded == [] and canvas.texts == []


# Quick Settings.

def test_named_in_a_nul_separated_list(fw):
    named = fw.fn('navigation__named_in', C.c_int, C.c_char_p, C.c_char_p)
    names = b'drop_wifi\0drop_bt\0drop_lo\0'
    assert [named(names, n) for n in (b'drop_wifi', b'drop_bt', b'drop_lo', b'drop', b'drop_btx', b'')] == [1, 1, 1, 0, 0, 0]
    assert named(b'', b'drop_wifi') == 0


SILVER = O['GLYPH_LIGHT_MAX']


@pytest.mark.parametrize('tone, bright', [(0xffffff, 1), (0x000000, 0), (SILVER * 0x010101, 0), ((SILVER + 1) * 0x010101, 1),
                                          *((a[RED], int(luma(a[RED]) > SILVER)) for a in ACCENTS)])
def test_a_bright_tone_is_one_over_graphite_s_silver_line(fw, tone, bright):
    """Perceived brightness over GLYPH_LIGHT_MAX: a grey's is its own level, so the line falls between two greys."""
    assert fw.fn('navigation__bright_tone', C.c_int, C.c_uint)(tone) == bright


def quick_window(world, **icons):
    win = world.window('statusbar_dialog', 'dialog')
    view = world.add(win, 'view_backlight', box=(0, 250, 375, 60))
    slider = world.add(view, 'slider_backlight', 'slider', box=(40, 10, 300, 40), min=0, max=100, value=50)
    return win, view, slider


# The bar's title (the system bar's label_title) names the top window.

def bar_title(fw, world, st, **config):
    theme(fw, **config)
    bar = world.window('system_bar', 'system_bar', box=(0, 0, 375, 26), top=False)
    title = world.add(bar, 'label_title', 'hscroll_label', box=(142, 0, 91, 26))
    st('bar_title', C.c_void_p).value = title
    return title


def sync(fw, win):
    fw.fn('bar_title_sync', None, C.c_void_p)(win)


def test_now_playing_leaves_the_bar_to_the_clock(fw, world, st):
    """The status row shows the clock over Now Playing (tools/ipod.py status_row): no title."""
    label = bar_title(fw, world, st)
    win = world.window('playing_page')
    world.add(world.add(win, 'view_navbar'), 'label_name', 'hscroll_label', text='Its own label')
    sync(fw, win)
    assert not world[label].text


@pytest.mark.parametrize('name, title', [('coverflow_page', 'Cover Flow'), ('equalizer_page', 'EQ'),
                                         ('spotify_page', 'Spotify'), ('radionp_page', 'Internet Radio'),
                                         ('usbmode_page', 'Transfer Files')])
def test_a_page_known_by_name_titles_the_bar(fw, world, st, name, title):
    label = bar_title(fw, world, st)
    win = world.window(name)
    world.add(world.add(win, 'view_navbar'), 'label_name', 'hscroll_label', text='Its own label')
    sync(fw, win)
    assert world[label].text == title
    (fn, _, ms), = world.timers
    assert ms == 0 and world.fire(1) == RET_OK and world.invalid == [(label, None)]  # after the paint


def navbar_page(world, text='Albums', kind='hscroll_label'):
    win = world.window('albums_page')
    nav = world.add(win, 'view_navbar')
    world.add(nav, 'img_back', 'image')
    return win, world.add(nav, 'label_title', kind, text=text)


def test_an_inset_modal_back_on_top_names_the_page_under_it_again(fw, world, st):
    """A playlist's More (a menu sheet) over Playlists, then Delete Playlist's confirm over it, which writes its
    question into the bar (feedback.c): after Cancel the modal is on top again, and the bar names Playlists, not the
    question, while it stays open."""
    label = bar_title(fw, world, st)
    win, _ = navbar_page(world, 'Playlists')
    sync(fw, win)
    assert world[label].text == 'Playlists'
    more = world.window('playlistsmore_page', 'dialog')
    fw.fn('modal_frame', None, C.c_void_p, C.c_void_p)(more, None)
    sync(fw, more)
    assert world[label].text == 'Playlists'
    confirm = world.window('confirminfo_dialog', 'dialog')
    sync(fw, confirm)
    world.set_text(label, 'Delete playlist \u201ca\u201d?')  # feedback_confirm's question
    for target, evt, fn, ctx in list(world.handlers):  # Cancel: the confirm goes (title_gone)
        if target == confirm and evt == EVT_DESTROY:
            C.CFUNCTYPE(C.c_int, C.c_void_p, C.c_void_p)(fn)(ctx, 0)
    world.nodes[world.wm].children.remove(confirm)
    world.top = more
    sync(fw, more)
    assert world[label].text == 'Playlists'


@pytest.mark.parametrize('kind', ['hscroll_label', 'label'])
def test_a_stock_page_s_navbar_title_titles_the_bar_and_is_followed(fw, world, st, kind):
    label = bar_title(fw, world, st)
    win, own = navbar_page(world, kind=kind)
    sync(fw, win)
    assert world[label].text == 'Albums'
    sync(fw, win)
    assert len(fw.calls('widget_set_text_utf8')) == 1 and len(world.timers) == 1  # unchanged: not written
    world.set_text(own, 'Album Artists')
    sync(fw, win)
    assert world[label].text == 'Album Artists' and len(world.timers) == 2


@pytest.mark.parametrize('text, title', [('Channel Balance', 'Channel Balance'), ('(null)', ''), ('', '')])
def test_a_title_too_long_or_stock_s_null_name(fw, world, st, text, title):
    label = bar_title(fw, world, st)
    world.set_text(label, 'Before')
    win, _ = navbar_page(world, text)
    sync(fw, win)
    assert world[label].text == title


class EmptyT(C.Structure):
    _fields_ = [('glyph', C.c_char_p), ('title', C.c_char_p), ('hint', C.c_char_p), ('row', C.c_char_p),
                ('foot', C.c_char_p), ('bar', C.c_int)]


def test_an_empty_state_retitles_the_bar_on_a_page_already_titled(fw, world, st):
    """Cover Flow over no albums (cf_empty.c): its page is titled first while its covers load (a status row's covers
    show no title, so ""), then the empty state comes; the bar must look again (empty_list), or it keeps "" and the
    status row shows only the clock (seen in the emulator, 2026-11-16) where the design names the page."""
    label = bar_title(fw, world, st)
    win = world.window('coverflow_page')
    body = world.add(win, 'view_body', box=(0, 0, 375, 290))
    from test_coverflow import COVERS, Cf
    cf = fw.var('cf', Cf)
    cf.screen, cf.scoped, cf.body = COVERS, 0, body
    sync(fw, win)
    assert not world[label].text  # no title: the clock
    e = EmptyT(b'cards', b'No Albums', b'Copy music to the SD card', None)
    fw.fn('empty_list', C.c_void_p, C.c_void_p, C.c_void_p, C.c_char_p, C.c_void_p, C.c_void_p)(
        win, body, b'Cover Flow', C.addressof(e), None)
    assert world[label].text == 'Cover Flow'


def shown_empty(world, win):
    """win's empty state (feedback.c empty_state) as shown: (glyph, title, hint), else None. Its view is insensitive:
    a row beside it keeps its taps."""
    art = world.find(win, '_empty_state')
    if not art or not world[art].visible:
        return None
    assert world[art].props['sensitive'] == 0
    return tuple(world[art].props[f'_es_{k}'] for k in ('glyph', 'title', 'hint'))


# The empty state's stack with its button (60 + 14 + 24 + 20 + 20 + 38) centred under the status row (y 40): the button's
# y.
EMPTY_ROW_Y = 40 + (280 - 176) // 2 + 118 + 20


def test_an_empty_library_s_songs_page_is_the_empty_state_over_scan_music_now(fw, world, st):
    """Songs over an empty library (stock's panel "view" on show): the panel's picture and line hide, its btn_upmusic
    becomes the empty state's button, Scan Music Now, under "No Songs" and how to fill the card: its band across the
    screen with no fill or words of its own, the capsule and its label painted under it (feedback_scan_bar). Songs
    back, the empty state goes and the Shuffle row returns."""
    theme(fw)
    deques = Deques(fw)
    showlist = fw.var('fw_ctx_p_deque_showlist', C.c_uint)
    win = world.window('allmusic_page')
    row = world.add(win, 'view_navbar_allplay', box=(0, 0, 375, 32))
    view = world.add(win, 'view', box=(0, 32, 375, 190))
    picture = world.add(view, 'image', 'image', box=(69, 0, 236, 96))
    line = world.add(view, 'label_scanfolder', 'label', box=(0, 96, 375, 20), text='Currently no songs')
    btn = world.add(view, 'btn_upmusic', 'button', box=(44, 136, 287, 40), text='Scan Music Now')
    level = deques.new()
    showlist.value = level
    songs = fw.fn('feedback_songs', None, C.c_void_p, C.c_void_p)
    songs(win, row)
    assert not world[row].visible and not world[picture].visible and not world[line].visible
    assert world[btn].visible and world.at(view)[1] == EMPTY_ROW_Y and tuple(world.at(btn)) == (0, 0, 375, 38)
    assert world[btn].text == ''
    assert all(world[btn].props[f'style:{s}:bg_color'] == 0 for s in ('normal', 'pressed', 'over', 'focused'))
    assert shown_empty(world, win) == ('music-notes', 'No Songs', 'Copy music to the SD card')
    assert world[world.find(win, '_empty_state')].props['_es_top'] == EMPTY_ROW_Y - 138  # the stack's top
    canvas = Canvas(fw)
    fw.fn("feedback_scan_bar", None, C.c_void_p, C.c_void_p)(btn, canvas.p)
    (box, _, radius), = canvas.rounded  # the capsule, centred, at least 140 px, its label on it
    assert radius == 19 and (box[1], box[3]) == (0, 38) and box[2] >= 140 and box[0] * 2 + box[2] in (374, 375)
    assert 'Scan Music Now' in [t[0] for t in canvas.texts]
    writes = len(fw.calls('widget_set_prop_str'))
    songs(win, row)
    assert len(fw.calls('widget_set_prop_str')) == writes  # unchanged: not written
    world[view].visible = 0
    deques[level].append(1)
    songs(win, row)
    assert world[row].visible and shown_empty(world, win) is None


@pytest.mark.parametrize('cls, words', [
    (0xf00a, ('heart-slash', 'No Favorites', 'Hold Play/Pause on a song to add it')),
    (0xf007, ('clock-counter-clockwise', 'Nothing Played Yet', 'Songs you play show up here')),
    (0xf008, ('chart-bar', 'No Plays Yet', 'Your most played songs show up here')),
    (0xf009, ('sparkle', 'No Songs Added Yet', 'New music on the card shows up here')),
    (0xf001, ('music-notes', 'No Songs', 'Copy music to the SD card'))])
def test_an_empty_song_list_shows_its_class_s_empty_state(fw, world, cls, words):
    theme(fw)
    deques = Deques(fw)
    fw.var('fw_ctx_p_deque_showlist', C.c_uint).value = deques.new()
    C.c_int.from_address(C.addressof(fw.var('fw_ctx_g_class_type', C.c_char))).value = cls
    win = world.window('allmusic_page')
    row = world.add(win, 'view_navbar_allplay', box=(0, 0, 375, 32))
    fw.fn('feedback_songs', None, C.c_void_p, C.c_void_p)(win, row)
    assert not world[row].visible and shown_empty(world, win) == words
    assert world[world.find(win, '_empty_state')].props['_es_top'] == 40 + (280 - 118) // 2  # no button: centred alone


def test_an_empty_library_with_no_card_in_says_so_and_drops_the_scan_row(fw, world):
    theme(fw)
    deques = Deques(fw)
    fw.var('fw_ctx_p_deque_showlist', C.c_uint).value = deques.new()
    fw.mock('fw_access', lambda path, *_: -1 if C.string_at(path) == b'/tmp/mmc_add' else 0)
    win = world.window('allmusic_page')
    row = world.add(win, 'view_navbar_allplay', box=(0, 0, 375, 32))
    view = world.add(win, 'view', box=(0, 32, 375, 190))
    world.add(view, 'btn_upmusic', 'button', box=(44, 136, 287, 40), text='Scan Music Now')
    fw.fn('feedback_songs', None, C.c_void_p, C.c_void_p)(win, row)
    assert not world[view].visible and shown_empty(world, win) == ('sd-card', 'No SD Card', 'Insert an SD card with music')


@pytest.mark.parametrize('layer, text, title', [(1, 'SD', 'Folders'), (2, 'SD', 'Folders'), (2, 'SD:/', 'Folders'),
                                                 (3, 'Music', 'Music'), (3, 'SD:/Music', 'Music'),
                                                 (4, 'SD:/Music/Aurora Field/', 'Aurora Field'), (3, 'SD:', 'Folders')])
def test_folder_s_title_is_folders_at_the_root_and_the_folder_s_own_name_deeper(fw, world, st, layer, text, title):
    """Stock's navbar names the storage ("SD") or a path ("SD:/Music"); the bar says Folders at the card's root and
    the folder's own name below it, never a path."""
    label = bar_title(fw, world, st)
    fw.var('g_folder_layer', C.c_ubyte).value = layer
    win = world.window('folder_page')
    world.add(world.add(win, 'view_navbar'), 'label_title', 'hscroll_label', text=text)
    sync(fw, win)
    assert world[label].text == title


def test_a_payload_page_s_caption_on_show_titles_the_bar(fw, world, st):
    label = bar_title(fw, world, st)
    win = world.window('games_page')
    world.add(win, 'sheet', 'view')
    hidden = world.add(win, 'caption', 'hscroll_label', box=(0, 0, 375, 48), text='Games')
    world[hidden].visible = 0
    world.add(win, 'note', 'hscroll_label', box=(0, 60, 375, 30), text='Not a caption')
    body = world.add(win, 'body', 'view')
    world.add(body, 'caption2', 'hscroll_label', box=(0, 0, 375, 48), text='Tetris')
    sync(fw, win)
    assert world[label].text == 'Tetris'


def test_a_hidden_caption_still_names_the_page(fw, world, st):
    label = bar_title(fw, world, st)
    win = world.window('games_page')
    first = world.add(win, '_page_title', 'hscroll_label', box=(0, 0, 375, 32), text='Games')  # page_title's
    second = world.add(win, 'other', 'hscroll_label', box=(0, 0, 375, 48), text='Second')
    world[first].visible = world[second].visible = 0
    sync(fw, win)
    assert world[label].text == 'Games'


def test_a_dialog_keeps_the_window_s_title(fw, world, st):
    label = bar_title(fw, world, st)
    win, _ = navbar_page(world)
    sync(fw, win)
    dialog = world.window('confirminfo_dialog', 'dialog')
    sync(fw, dialog)
    assert world[label].text == 'Albums'


def test_only_the_top_window_titles_the_bar(fw, world, st):
    label = bar_title(fw, world, st)
    win, _ = navbar_page(world)
    world.window('other_page')  # on top
    sync(fw, win)
    assert world[label].text is None and world.handlers == []


def test_no_title_label_no_title(fw, world, st):
    theme(fw)
    win, _ = navbar_page(world)
    sync(fw, win)
    assert world.handlers == [] and fw.calls('widget_set_text_utf8') == []


def test_the_window_going_drops_its_title_s_source(fw, world, st):
    label = bar_title(fw, world, st)
    win, own = navbar_page(world)
    sync(fw, win)
    assert st('title_win', C.c_void_p).value == win and st('title_lbl', C.c_void_p).value == own
    for gone in world.on(win, EVT_DESTROY):
        gone()
    assert st('title_win', C.c_void_p).value is None and st('title_lbl', C.c_void_p).value is None
    assert {a[0] for _, a in fw.calls('widget_off_by_func')} == {win, own}
    world.set_text(own, 'Songs')
    sync(fw, win)  # the same window again: found afresh
    assert world[label].text == 'Songs'


def test_home_s_own_list_has_no_title(fw, world, st):
    """The status row names none of Home's own lists, and shows the clock."""
    label = bar_title(fw, world, st)
    sync(fw, world.window('home_page'))
    assert not world[label].text
    win, _ = navbar_page(world)
    sync(fw, win)
    assert world[label].text == 'Albums'


def test_songs_action_bar_is_found_with_the_title(fw, world, st):
    bar_title(fw, world, st)
    win, _ = navbar_page(world)
    action = world.add(win, 'view_navbar_allplay')
    sync(fw, win)
    assert st('action_view', C.c_void_p).value == action


def action_view(world, n):
    view = world.add(world.window('songs_page'), 'view_navbar_allplay', box=(0, 0, 375, 40))
    made = [lambda: world.add(view, 'label', 'label', box=(40, 0, 200, 40), text='Play All'),
            lambda: world.add(view, 'play', 'image', box=(0, 0, 40, 40), style='s_img_playall'),
            lambda: world.add(view, 'sort', 'image', box=(335, 0, 40, 40), style='s_img_sort')]
    return view, [make() for make in made[:n]]


def nav_define(name):
    """A number navigation.c #defines (clang-format may break the line after its name)."""
    found = re.search(rf'^#define {name}[\s\\]*(\d+)', NAVIGATION_SOURCE, re.M)
    assert found, name
    return int(found[1])


def test_songs_action_bar_is_the_shuffle_row(fw, world, st):
    """list.shuffle (OLED's theme.xml): the set's action_row high; the label reads Shuffle SHUFFLE_LABEL_DX after
    the text column, where its glyph stands; Play All's image the row's left as its hit area, without its icon;
    Sort's bars centred in the SORT_PILL_W pill at the selection's inset from the right edge."""
    theme(fw)
    fw.mock('image_set_draw_type', lambda w, t, *_: world.set(w, 'draw_type', t))
    view, (label, play, sort) = action_view(world, 3)
    fw.fn('navigation__action_row', None, C.c_void_p)(view)
    h, inset = GEOMETRY['action_row'], SEL_INSET
    pill = nav_define('SORT_PILL_W')
    assert world.at(label) == (GEOMETRY['text_x'] + nav_define('SHUFFLE_LABEL_DX'), 0, 280, h)
    assert world[label].text == 'Shuffle'
    assert world.at(play) == (0, 0, 300, h) and world.at(sort) == (375 - inset - pill, 0, pill, h)
    assert world[play].props['style'] == world[sort].props['style'] == 'default'
    assert fw.calls('image_base_set_image') and world[sort].props['draw_type'] == O['IMAGE_DRAW_CENTER']
    moves = len(fw.calls('widget_move_resize'))
    fw.fn('navigation__action_row', None, C.c_void_p)(view)
    assert len(fw.calls('widget_move_resize')) == moves  # done: its first child's height says so


@pytest.mark.parametrize('n, moved', [(1, 0), (2, 2)])
def test_an_action_bar_without_sort_or_play(fw, world, st, n, moved):
    theme(fw)
    view, _ = action_view(world, n)
    fw.fn('navigation__action_row', None, C.c_void_p)(view)
    assert len(fw.calls('widget_move_resize')) == moved


def test_any_bar_title_fits_the_status_row(fw, world, st):
    """The status row's label ends a long title in an ellipsis, so any fits once the bar is found."""
    label = bar_title(fw, world, st)
    canvas = Canvas(fw)
    fw.mock('widget_get_canvas', lambda w, *_: canvas.p if w == label else 0)
    fits = fw.fn('bar_title_fits', C.c_int, C.c_void_p)
    page = world.window('x_page')
    short, long_ = world.add(page, 'a', 'label', text='Settings'), world.add(page, 'b', 'label', text='Playlists')
    assert fits(short) == 1 and fits(long_) == 1


def test_no_bar_title_fits_nothing(fw, world, st):
    theme(fw)
    page = world.window('x_page')
    assert fw.fn('bar_title_fits', C.c_int, C.c_void_p)(world.add(page, 'a', 'label', text='A')) == 0


def test_an_empty_page_keeps_its_message_and_the_bar_its_name(fw, world, st):
    label = bar_title(fw, world, st)
    win = world.window('songs_page')
    body = world.add(win, 'body', 'view')
    caption = world.add(body, 'caption', 'hscroll_label', box=(0, 0, 375, 48), text='No Songs')
    fw.fn('page_empty', None, C.c_void_p, C.c_char_p)(body, b'Songs')
    assert world[caption].text == 'Songs' and world[caption].visible == 0
    message, hint = world.find(body, '_empty'), world.find(body, '_empty_hint')  # OLED's note (feedback_empty)
    assert world[message].text == 'No Songs' and world[message].visible
    assert world[hint].text == '' and not world[hint].visible
    assert world[label].text == 'Songs'


# The background hook's own windows.

def system_bar(fw, world, **props):
    bar = world.window('system_bar', 'system_bar', box=(0, 0, 375, 26), top=False, **props)
    fw.var('fw_ctx_system_bar', C.c_void_p).value = bar
    return bar


@pytest.mark.parametrize('light', ['0', '1'])
def test_the_bar_lays_no_ground_of_its_own(fw, world, light):
    """Every page's window is full-bleed (docs/oled-design.md#full-bleed): its own ground, black or Light Mode's paper,
    runs under the bar, which fills nothing, and the canvas's fill colour is left as it was."""
    theme(fw, LIGHT=light)
    bar = system_bar(fw, world)
    canvas = Canvas(fw)
    paint_bg(fw, bar, canvas)
    assert canvas.fills == [] and canvas.fill == 0xff123456


def test_painting_the_bar_keeps_its_clock_and_title_current(fw, world, st):
    theme(fw)
    bar = system_bar(fw, world)
    title = world.add(bar, 'label_title', 'hscroll_label', box=(142, 0, 91, 26))
    st('bar_title', C.c_void_p).value = title
    win, _ = navbar_page(world)
    paint_bg(fw, bar, Canvas(fw))
    assert world[title].text == 'Albums'
    assert st('clock_timer', C.c_uint).value and len(world.timers) >= 2  # the clock's, at once, and the title's
    timers = len(world.timers)
    paint_bg(fw, win, Canvas(fw))  # the top window: the same, but the clock is armed
    assert len(world.timers) == timers


def test_home_keeps_its_title_while_music_plays_and_the_state_sits_by_the_battery(fw, world, st):
    """Home's own list is like any page for the play state: the bar's paint shows it by the battery, as over a
    page, and repaints the bar after it; the status row's title stays empty there."""
    theme(fw)
    bar = system_bar(fw, world)
    title = world.add(bar, 'label_title', 'hscroll_label', box=(142, 0, 91, 26))
    state = world.add(bar, 'img_state', 'image', box=(250, 0, 18, 26))
    world[state].visible = 0
    fw.mock('image_base_set_image', lambda w, s, *_: world.set(w, 'image', C.string_at(s).decode()))
    status = [2]  # playing
    fw.mock('mclGetPlayStatus', lambda *_: status[0])

    def shown():
        """After the bar's paint: the title, whether it shows, and the state's image when it shows."""
        paint_bg(fw, bar, Canvas(fw))
        return world[title].text, world[title].visible, world[state].visible and world[state].props.get('image')

    home = world.window('home_page')
    on_home = shown()
    assert not on_home[0] and on_home[1:] == (1, on_home[2]) and on_home[2]
    assert st('bar_timer', C.c_uint).value  # mid-paint: the bar again after this frame
    navbar_page(world)
    assert shown() == ('Albums', 1, on_home[2])  # over a page: the same image in the same img_state
    world.top = home
    status[0] = 0  # stopped
    assert shown()[1:] == (1, 0) and not shown()[0]


def inside(r, box):
    x, y, w, h = r
    bx, by, bw, bh = box
    return bx <= x and by <= y and x + w <= bx + bw and y + h <= by + bh


# The volume (stock's dialog/volume_dialog over another window).

def volume(fw, world, st, level=37, maximum=100, under='home_page', np=False):
    """The dialog over a window (Now Playing's with np): (dialog, slider_vol, label_vol)."""
    below = world.window(under, top=False)
    if np:
        slider = world.add(below, 'slider_play', 'slider', box=(66, 245, 243, 20), bar_size=4)
        times = world.add(below, 'label_playtime', 'label', box=(16, 270, 46, 16))
        st('np_win', C.c_void_p).value = below
        st('np_slider', C.c_void_p).value = slider
        st('np_elapsed', C.c_void_p).value = times
    dialog = world.window('volume_dialog', 'dialog')
    vol = world.add(dialog, 'slider_vol', 'slider', box=(0, 100, 375, 40), max=maximum, value=level)
    label = world.add(dialog, 'label_vol', 'label', box=(0, 150, 375, 40))
    return dialog, vol, label


def vol_paint(fw, dialog, canvas):
    fw.fn('vol_paint', None, C.c_void_p, C.c_void_p)(dialog, canvas.p)


@pytest.mark.parametrize('level, fill', [(37, 231 * 37 // 100), (100, 231), (1, 10), (0, None)])
def test_away_from_now_playing_the_volume_is_a_pill_and_its_number_on_a_panel(fw, world, st, level, fill):
    theme(fw)
    dialog, vol, label = volume(fw, world, st, level)
    canvas = Canvas(fw)
    vol_paint(fw, dialog, canvas)
    x, y, w, h = O['VOL_PANEL_X'], O['VOL_PANEL_Y'], 375 - 2 * O['VOL_PANEL_X'], O['VOL_PANEL_H']
    pad, bar, room = O['VOL_PANEL_PAD'], O['VOL_PANEL_BAR'], O['VOL_PANEL_TEXT']
    track = (x + pad, y + (h - bar) // 2, w - 2 * pad - room, bar)
    assert canvas.rounded[0] == ((x, y, w, h), O['LETTER_ALPHA'] << 24 | O['FILL_RGB'], O['LETTER_RADIUS'])
    assert canvas.rounded[1] == (track, rgba(O['VOL_PANEL_TRACK']), bar // 2)
    assert canvas.rounded[2:] == ([((track[0], track[1], fill, bar), rgba(0xffffff), bar // 2)] if fill else [])
    assert [(t, r) for t, r, *_ in canvas.texts] == [(str(level), (track[0] + track[2], y, room, h))]
    assert world.at(vol) == (x, y, w, h) and world[vol].visible == world[label].visible == 0  # stock's under it
    assert canvas.fill == 0xff123456


def test_the_volume_with_no_range_is_empty(fw, world, st):
    theme(fw)
    dialog, *_ = volume(fw, world, st, 5, maximum=0)
    canvas = Canvas(fw)
    vol_paint(fw, dialog, canvas)
    assert len(canvas.rounded) == 2 and canvas.texts[0][0] == '5'


def added(after, before):
    """after's fills less before's, each as many times as before has it."""
    extra = list(after.fills)
    for f in before.fills:
        extra.remove(f)
    return extra


def test_over_now_playing_the_volume_s_track_is_the_theme_s(fw, world, st, theme_t, layout):
    """playing.scrubber.track: the volume over the progress bar draws the bar's own track."""
    theme(fw)
    dialog, *_ = volume(fw, world, st, 37, under='playing_page', np=True)
    track = fw.fn('theme', C.c_void_p)() + layout['theme']['np_track_rgb']
    assert not libc.mprotect(track & ~0xfff, 0x2000, 3)  # this test's own load of the table, made writable
    C.c_uint.from_address(track).value = 0x262626
    canvas = Canvas(fw)
    vol_paint(fw, dialog, canvas)
    assert canvas.rounded[0][1] == rgba(0x262626)


def test_the_volume_polls_while_it_is_on_top(fw, world, st, vd):
    theme(fw)
    dialog, vol, _ = volume(fw, world, st, 37)
    vol_paint(fw, dialog, Canvas(fw))
    vol_paint(fw, dialog, Canvas(fw))
    (fn, _, ms), = world.timers
    assert ms == O['VOL_POLL_MS']  # one, however many paints
    assert world.fire(1) == RET_REPEAT and world.invalid == []
    world.set(vol, 'value', 38)
    assert world.fire(1) == RET_REPEAT and world.invalid == [(dialog, None)]  # a change repaints it whole
    world.window('home_page')
    assert world.fire(1) == RET_OK and vd('vol_dialog', C.c_void_p).value is None and vd('vol_timer', C.c_uint).value == 0


# The wheel's volume steps at its speed (vol_next) and the bar eases after them (vol_ease).
KEY_NEXT, KEY_PREV = O['KEY_NEXT'], O['KEY_PREV']


def volumes(fw, volume, maximum=100):
    fw.var('g_volume', C.c_ubyte).value, fw.var('g_maxvolume', C.c_ubyte).value = volume, maximum
    return fw.var('g_volume', C.c_ubyte)


@pytest.mark.parametrize('speed', [0, 100, 270, 300, 350, 400, 450, 470, 500, 540, 2000, 1 << 30])
def test_a_ticks_volume_steps_are_rockboxs_fourth_power_of_the_speed(fw, speed):
    """(speed / 270)^4 rounded (Rockbox's iPod WHEEL_ACCELERATION 3, WHEEL_ACCEL_START 270), 1 to VOL_ACCEL_MAX."""
    volumes(fw, 50)
    fw.var('wheel_speed', C.c_int).value = speed
    want = max(1, min(O['VOL_ACCEL_MAX'], round((speed / 270) ** 4)))
    step = fw.fn('vol_next', C.c_int, C.c_uint)
    assert (step(KEY_NEXT) - 50, 50 - step(KEY_PREV)) == (want, want)
    assert fw.var('g_volume', C.c_ubyte).value == 50  # nothing set: the caller does


@pytest.mark.parametrize('volume, maximum, key, want', [
    (98, 100, 'next', 100), (100, 100, 'next', 100), (55, 60, 'next', 60), (60, 60, 'next', 60),
    (70, 60, 'next', 70),  # over the limit (lowered since): up leaves it, as stock's dialog
    (70, 60, 'prev', 60), (3, 100, 'prev', 0), (0, 100, 'prev', 0),
])
def test_a_fast_ticks_volume_stays_within_its_limits(fw, volume, maximum, key, want):
    volumes(fw, volume, maximum)
    fw.var('wheel_speed', C.c_int).value = 540
    assert fw.fn('vol_next', C.c_int, C.c_uint)(KEY_NEXT if key == 'next' else KEY_PREV) == want


@pytest.mark.parametrize('volume, key, want', [(40, 'next', 50), (40, 'prev', 30), (95, 'next', 100), (4, 'prev', 0),
                                               (100, 'next', 100), (0, 'prev', 0)])
def test_the_volume_dialogs_tick_leaves_stock_one_step_to_make(fw, volume, key, want):
    """vol_keyup sets the volume a step short of vol_next's; stock's own step (mocked here as its +-1 within 0 and
    the limits) lands on it, with its save, slider and count."""
    g = volumes(fw, volume)
    fw.var('wheel_speed', C.c_int).value = 540  # 10 a tick
    fw.var('g_backlight_status', C.c_ubyte).value = 1
    k = KEY_NEXT if key == 'next' else KEY_PREV
    seen = []

    def stock(dialog, e, *_):
        seen.append(g.value)
        g.value = min(100, g.value + 1) if k == KEY_NEXT else max(0, g.value - 1)
        return 11
    fw.mock('stock_vol_keyup_trampoline', stock)
    e = C.create_string_buffer(0x40)
    struct.pack_into('<I', e, O['EVENT_KEY'], k)
    assert fw.fn('vol_keyup', C.c_int, C.c_void_p, C.c_void_p)(None, C.addressof(e)) == 11
    assert g.value == want and len(seen) == 1


def test_other_keys_over_the_volume_dialog_are_stocks(fw):
    g = volumes(fw, 40)
    fw.var('wheel_speed', C.c_int).value = 540
    fw.mock('stock_vol_keyup_trampoline', lambda *_: 0)
    e = C.create_string_buffer(0x40)
    struct.pack_into('<I', e, O['EVENT_KEY'], O['KEY_RETURN'])
    assert fw.fn('vol_keyup', C.c_int, C.c_void_p, C.c_void_p)(None, C.addressof(e)) == 0 and g.value == 40


def test_a_dark_tick_over_the_volume_dialog_moves_nothing(fw):
    """A dialog left open past the screen-off: its tick goes on (0) to on_wm_keyup_fun's hook, which drops it."""
    g = volumes(fw, 40)
    fw.var('wheel_speed', C.c_int).value = 540
    fw.var('g_backlight_status', C.c_ubyte).value = 0
    seen = []
    fw.mock('stock_vol_keyup_trampoline', lambda *_: seen.append(1) or 11)
    e = C.create_string_buffer(0x40)
    struct.pack_into('<I', e, O['EVENT_KEY'], KEY_NEXT)
    assert fw.fn('vol_keyup', C.c_int, C.c_void_p, C.c_void_p)(None, C.addressof(e)) == 0 and g.value == 40 and not seen


def eased_widths(fw, world, st, clock, ms, polls, frm=40, to=50, maximum=100):
    """The panel's bar over Home after a tick from frm to to: its width at each of polls, ms apart."""
    theme(fw)
    dialog, vol, _ = volume(fw, world, st, frm, maximum)
    vol_paint(fw, dialog, Canvas(fw))
    volumes(fw, frm)
    fw.var('wheel_speed', C.c_int).value = 540
    world.set(vol, 'value', fw.fn('vol_next', C.c_int, C.c_uint)(KEY_NEXT if to > frm else KEY_PREV))
    widths = []
    for _ in range(polls):
        canvas = Canvas(fw)
        vol_paint(fw, dialog, canvas)
        widths.append(canvas.rounded[2][0][2] if len(canvas.rounded) > 2 else 0)
        clock[0] += ms
        world.invalid.clear()
        assert world.fire(1) == RET_REPEAT
    return widths, canvas


def track(level, maximum=100):
    return (375 - 2 * O['VOL_PANEL_X'] - 2 * O['VOL_PANEL_PAD'] - O['VOL_PANEL_TEXT']) * level // maximum


@pytest.fixture
def clock(fw):
    now = [5000]
    fw.mock('time_now_ms', lambda *_: now[0])
    return now


def test_the_volume_bar_eases_to_a_ticks_level(fw, world, st, clock):
    widths, canvas = eased_widths(fw, world, st, clock, 16, 20)
    assert widths[0] == track(40)  # the tick's paint: the bar where it was
    assert widths == sorted(widths) and len(set(widths)) > 6  # moving, a frame at a time
    assert widths[-1] == track(50) and canvas.texts[0][0] == '50'  # landed; the number is the value at once
    tau, dt = O['VOL_EASE_MS'], 16  # each poll leaves tau / (tau + dt) of the way
    for n in (1, 3, 6):
        left = (tau / (tau + dt)) ** n
        assert abs(widths[n] - track(40) - (track(50) - track(40)) * (1 - left)) <= 1.5, (n, widths)


def test_the_volume_bar_eases_down_as_up(fw, world, st, clock):
    widths, _ = eased_widths(fw, world, st, clock, 16, 20, frm=60, to=50)
    assert widths[0] == track(60) and widths == sorted(widths, reverse=True) and widths[-1] == track(50)


def test_a_slow_pass_moves_the_bar_further(fw, world, st, clock):
    """The ease is by time: a pass that comes late finds the bar most of the way."""
    widths, _ = eased_widths(fw, world, st, clock, 200, 3)
    assert widths[0] == track(40) and widths[1] >= track(40) + (track(50) - track(40)) * 0.75


def test_the_volume_poll_repaints_while_the_bar_eases_and_then_stops(fw, world, st, clock):
    theme(fw)
    dialog, vol, _ = volume(fw, world, st, 40)
    vol_paint(fw, dialog, Canvas(fw))
    volumes(fw, 40)
    world.set(vol, 'value', fw.fn('vol_next', C.c_int, C.c_uint)(KEY_NEXT))  # a slow tick: 41
    repaints = 0
    for _ in range(40):
        vol_paint(fw, dialog, Canvas(fw))
        clock[0] += 16
        world.invalid.clear()
        world.fire(1)
        repaints += bool(world.invalid)
    assert 2 < repaints < 30 and not world.invalid  # it settled, and the poll went quiet


def test_a_new_volume_dialog_opens_on_its_value(fw, world, st, clock):
    """A dialog the bar was easing in when it closed: the next one shows its value, not the old ease."""
    theme(fw)
    dialog, vol, _ = volume(fw, world, st, 40)
    vol_paint(fw, dialog, Canvas(fw))
    volumes(fw, 40)
    fw.var('wheel_speed', C.c_int).value = 540
    world.set(vol, 'value', fw.fn('vol_next', C.c_int, C.c_uint)(KEY_NEXT))
    again = world.window('volume_dialog', 'dialog')
    world.add(again, 'slider_vol', 'slider', box=(0, 100, 375, 40), max=100, value=50)
    canvas = Canvas(fw)
    vol_paint(fw, again, canvas)
    assert canvas.rounded[2][0][2] == track(50)


def test_over_now_playing_its_bar_eases_too(fw, world, st, clock):
    """OLED's set: the white bar between the speakers, VOL_SPK_INSET in from each end of the progress bar."""
    theme(fw)
    dialog, vol, _ = volume(fw, world, st, 20, under='playing_page', np=True)
    vol_paint(fw, dialog, Canvas(fw))
    volumes(fw, 20)
    fw.var('wheel_speed', C.c_int).value = 540
    world.set(vol, 'value', fw.fn('vol_next', C.c_int, C.c_uint)(KEY_NEXT))
    w = 243 - 2 * O['VOL_SPK_INSET']

    def width():
        canvas = Canvas(fw)
        vol_paint(fw, dialog, canvas)
        _, (fill, _, _) = canvas.rounded  # the track, then the fill
        return fill[2]
    assert width() == w * 20 // 100  # the tick's paint: where it was
    clock[0] += 50
    world.fire(1)
    assert w * 20 // 100 < width() < w * 30 // 100
    for _ in range(3):  # each poll a second on leaves VOL_EASE_MS / (VOL_EASE_MS + 1000) of the way: it lands
        clock[0] += 1000
        world.fire(1)
    assert width() == w * 30 // 100


@pytest.mark.parametrize('name, below', [('home_page', 1), ('volume_dialog', 0)])
def test_no_volume_dialog_no_volume(fw, world, st, name, below):
    theme(fw)
    if below:
        world.window('below')
    dialog = world.window(name, 'dialog')
    world.add(dialog, 'slider_vol', 'slider', value=50)
    canvas = Canvas(fw)
    vol_paint(fw, dialog, canvas)
    assert canvas.rounded == [] and world.timers == []


def test_the_bar_s_paint_with_the_volume_on_top_draws_it(fw, world, st):
    """The dialog painted as the top window draws all that vol_paint draws over it."""
    theme(fw)
    system_bar(fw, world)
    dialog, *_ = volume(fw, world, st, 50)
    canvas = Canvas(fw)
    paint_bg(fw, dialog, canvas)
    alone = Canvas(fw)
    vol_paint(fw, dialog, alone)
    assert alone.rounded and alone.texts
    assert collections.Counter(alone.rounded) <= collections.Counter(canvas.rounded)
    assert collections.Counter(alone.texts) <= collections.Counter(canvas.texts)


def test_the_volume_panel_s_colour_is_defined(fw, world, st):
    """The panel away from Now Playing: its fill is the fast-scroll letter's, 85% of the dark surface."""
    theme(fw)
    dialog, *_ = volume(fw, world, st, 50)
    canvas = Canvas(fw)
    vol_paint(fw, dialog, canvas)
    assert canvas.rounded[0][1] == O['LETTER_ALPHA'] << 24 | O['FILL_RGB']


def ui_load(fw, st, name, root, parent=0x4000):
    """ringnav_ui_load over stock's loader, which builds root: its answer and the names stock was asked for."""
    st('theme_booted').value = 1  # the system bar's load booted the theme already
    asked = []
    fw.mock('stock_ui_load_trampoline', lambda n, p, *_: asked.append((C.string_at(n).decode(), p)) or root)
    got = fw.fn('ringnav_ui_load', C.c_void_p, C.c_char_p, C.c_void_p)(name, parent)
    assert asked and asked[0][1] == parent
    return got or 0, [n for n, _ in asked]


def named(world, win, name):
    return world.find(win, name)


# The library scan's page (scan_page.c): Update Local Music's dialog as the empty states' stack.

SCAN_WHOLE = O['SCAN_WHOLE']


@pytest.fixture
def whole(fw):
    """Stock's whole-library scan flag, at its fixed address: conftest's map outlives a load, so it goes back."""
    flag = C.c_ubyte.from_address(SCAN_WHOLE)
    saved = flag.value
    yield flag
    flag.value = saved


def scan_dialog(fw, world, st, text='Scanned 0', create=0):
    theme(fw)
    win = world.window('updatemusic_dialog', 'dialog')
    for name in ('img_animation', 'label_scanfolder'):
        world.add(win, name, 'image')
    world.add(win, 'label_msg', 'label', text=text)
    world.add(win, 'btn_no', 'button', box=(100, 250, 175, 40), text='Stop')
    threads = []
    fw.mock('fw_pthread_create', lambda t, attr, fn, arg, *_: threads.append(fn) or create)
    ui_load(fw, st, b'updatemusic_dialog', win)
    return win, threads


def count(fw, threads, songs):
    """Run the dialog's counting thread over a library of songs."""
    def exec_(db, sql, row, ctx, *_):
        if row and b'count(*)' in C.string_at(sql):
            values = (C.c_char_p * 1)(str(songs).encode())
            C.CFUNCTYPE(C.c_int, C.c_void_p, C.c_int, C.c_void_p, C.c_void_p)(row)(ctx, 1, C.addressof(values), 0)
        return 0
    fw.mock('sqlite3_exec', exec_)
    (thread,) = threads
    C.CFUNCTYPE(C.c_void_p, C.c_void_p)(thread)(None)


def test_the_scan_dialog_s_page_lives_with_its_window(fw, world, st):
    """Stock's widgets stay for its look-ups, hidden; one count runs, detached; the poll every 80 ms (the spinner's frame) follows the
    window, which takes both with it as it goes."""
    win, threads = scan_dialog(fw, world, st)
    for name in ('img_animation', 'label_msg', 'label_scanfolder'):
        assert world[named(world, win, name)].visible == 0
    assert len(threads) == 1 and fw.calls('fw_pthread_detach')
    (fn, _, ms), = world.timers
    assert ms == 80 and st('msg_win', C.c_void_p).value == win
    for gone in world.on(win, EVT_DESTROY):
        gone()
    assert st('msg_win', C.c_void_p).value is None and world.removed == [1]


def test_one_count_answers_two_dialogs(fw, world, st):
    _, threads = scan_dialog(fw, world, st)
    win2 = world.window('updatemusic_dialog', 'dialog')
    world.add(win2, 'label_msg', 'label', text='0')
    world.add(win2, 'btn_no', 'button')
    ui_load(fw, st, b'updatemusic_dialog', win2)
    assert len(threads) == 1  # still running


def test_a_count_that_can_t_start_lets_the_next_dialog_try(fw, world, st):
    _, threads = scan_dialog(fw, world, st, create=11)
    assert len(threads) == 1 and fw.calls('fw_pthread_detach') == []
    win2 = world.window('updatemusic_dialog', 'dialog')
    world.add(win2, 'label_msg', 'label', text='0')
    world.add(win2, 'btn_no', 'button')
    ui_load(fw, st, b'updatemusic_dialog', win2)
    assert len(threads) == 2  # tried again


def poll(world):
    return world.fire(1)


@pytest.fixture
def done(fw):
    """Stock's scan-over counter (wait_scan sets it), at its fixed address, and its stop flag (toolsGetUserStop's
    answer): 0 ran to the end, 1 Stop, 2 /mnt/data full. Both go back."""
    counter = C.c_int.from_address(O['SCAN_DONE'])
    saved, stop = counter.value, [0]
    fw.mock('toolsGetUserStop', lambda *_: stop[0])
    counter.value = 0
    yield counter, stop
    counter.value = saved


def scan_page(fw, win):
    """The scan page painted: its words (text, centre x, centre y, colour) and its art (name, x, y)."""
    canvas = Canvas(fw)
    paint_bg(fw, win, canvas)
    words = [(t, r[0] + r[2] // 2, r[1] + r[3] // 2, c) for t, r, c, _ in canvas.texts]
    return canvas, words, [(n, d[0], d[1]) for n, d in canvas.arts]


def said(words):
    return [w[0] for w in words]


SCAN_FIG_Y, SCAN_TITLE_CY, SCAN_LINE_CY, SCAN_BTN_CY = 92, 178, 200, 249  # 27 v4's stack, centred from y 40 to 320


def test_the_scan_counts_against_the_library_before_it(fw, world, st, whole, done):
    """Late: the ring's percent (its line's arc needs a vgcanvas, none here), "312 of 704 songs", Stop; the poll
    repaints the stack only when stock's text changes or the spinner turns."""
    whole.value = 1
    win, threads = scan_dialog(fw, world, st, 'Scanned 312')
    count(fw, threads, 704)
    assert poll(world) == RET_REPEAT
    (target, box), = world.invalid
    assert target == win and box == (0, 40, 375, 320 - 40)  # under the status row, to the screen's end
    _, words, _ = scan_page(fw, win)
    assert ('312 of 704 songs', 187, SCAN_TITLE_CY, 0xffffffff) in words
    assert '44%' in said(words) and 'Stop' in said(words)
    assert poll(world) == RET_REPEAT and len(world.invalid) == 1  # nothing new, no spinner: nothing repainted


def test_the_scan_dialog_is_the_empty_states_stack(fw, world, st, whole, done):
    """Early (27 v4): the status row the dialog covers, painted under its title, the spinner in the figure's box, "1,284 songs found", the
    folder's glyph before the name stock writes on a whole scan, and stock's button unseen over the painted Stop, which Centre and a tap press."""
    whole.value = 1
    win, threads = scan_dialog(fw, world, st, 'Scanned 1284')
    for name in ('img_animation', 'label_msg', 'label_scanfolder'):
        assert world[named(world, win, name)].visible == 0
    world[named(world, win, 'label_scanfolder')].text = 'SD:/Music/Brent Faiyaz/'
    folder, button = (named(world, win, n) for n in ('msg_folder', 'btn_no'))
    assert fw.fn('msg_title', C.c_char_p, C.c_char_p)(b'updatemusic_dialog') == b'Update Library'  # the status row's
    assert fw.fn('msg_title', C.c_char_p, C.c_char_p)(b'updatemusic_page') is None
    bar, painted = fw.var('fw_ctx_system_bar', C.c_void_p), []
    saved_bar, bar.value = bar.value, 0x5eed
    fw.mock('widget_paint', lambda w, c, *_: painted.append(w) or 0)
    assert world[button].props['style:normal:bg_color'] == 0 and world[button].props['style:normal:text_color'] == 0
    poll(world)
    canvas, words, arts = scan_page(fw, win)
    bar.value = saved_bar
    assert painted == [0x5eed]  # the status row the dialog covers, painted first, as Control Center's
    assert ('1,284 songs found', 187, SCAN_TITLE_CY, 0xffffffff) in words
    assert [a for a in arts if a[0].startswith('oled_swspin_d_')] and \
        [(x, y) for n, x, y in arts if n.startswith('oled_swspin_d_')] == [(187 - 24, SCAN_FIG_Y + 30 - 24)]
    assert world[folder].text == 'Brent Faiyaz' and world[folder].visible
    assert world[folder].props['style:normal:text_color'] & 0xffffffff == 0xffababab  # Text Secondary, opaque
    (fx, fy), = [(x, y) for n, x, y in arts if n == 'oled_scan_folder_d']
    assert fy == SCAN_LINE_CY - 7 and world.at(folder)[0] == fx + 15 + 6 - 2  # the glyph, 6 px, the name
    bx, by, bw, bh = world.at(button)
    assert (by, bh) == (SCAN_BTN_CY - 19, 38) and bx == (375 - bw) // 2  # over the painted Stop
    pill = [r for r, c, _ in canvas.rounded if r[1] == SCAN_BTN_CY - 19 and r[3] == 38]
    assert pill and pill[0][2] == bw and ('Stop', 187, SCAN_BTN_CY, 0xffffffff) in [(t, x, y, c) for t, x, y, c in words if t == 'Stop']


def test_the_spinner_turns_with_the_poll(fw, world, st, whole, done):
    win, _ = scan_dialog(fw, world, st, 'Scanned 3')
    frames = []
    for _ in range(3):
        assert poll(world) == RET_REPEAT
        frames += [n for n, *_ in scan_page(fw, win)[2] if n.startswith('oled_swspin')]
    assert frames == ['oled_swspin_d_1', 'oled_swspin_d_2', 'oled_swspin_d_3'] and len(world.invalid) == 3
    _, words, arts = scan_page(fw, win)  # stock named no folder: the line says what is scanned, no glyph
    assert ('Scanning the SD card', 187, SCAN_LINE_CY) in [w[:3] for w in words] and not [a for a in arts if 'folder' in a[0]]


@pytest.mark.parametrize('stop, whole_scan, text, total, figure, title, line, card', [
    (0, 1, 'Updated 738', 704, 'oled_scan_done_d', 'Library Updated', '738 songs, 34 new', 1),
    (0, 1, 'Updated 3800', 800, 'oled_scan_done_d', 'Library Updated', '3,800 songs, 3,000 new', 1),  # thousands, both
    (0, 1, 'Updated 700', 704, 'oled_scan_done_d', 'Library Updated', '700 songs', 1),  # fewer: no "new"
    (0, 0, 'Updated 738', 704, 'oled_scan_done_d', 'Library Updated', '738 songs', 1),  # a folder's scan: not measured
    (1, 1, 'Updated 120', 704, 'oled_scan_done_d', 'Scan Stopped', '120 songs found', 1),
    (2, 1, 'No storage', 704, 'oled_scan_card_d', 'Not Enough Space', "Some songs weren't added", 1),
    # the card pulled mid-scan (emulator, 2026-11-16): stock ends as a finish with the count it had and toasts
    # "SD Card Removed"; the library is not left as it was (3,800 songs became 3,000)
    (0, 1, 'Updated 3000', 3800, 'oled_scan_card_d', 'SD Card Removed', 'Put it back and scan again', 0)])
def test_the_scan_s_ending_says_how_it_ended(fw, world, st, whole, done, stop, whole_scan, text, total, figure, title, line,
                                            card):
    """Once wait_scan sets SCAN_DONE stock writes its ending into label_msg (scan_updatefinish, or msg_no_storage
    with the stop flag at 2): the check circle or the memory chip, the title, the line and Done in the accent. With the
    card gone (stock's /tmp/mmc_add) it says so instead."""
    fw.mock('fw_access', lambda path, *_: 0 if card or C.string_at(path) != b'/tmp/mmc_add' else -1)
    whole.value = whole_scan
    win, threads = scan_dialog(fw, world, st, 'Scanned 100')
    count(fw, threads, total)
    poll(world)
    done[0].value, done[1][0] = 1, stop
    world[named(world, win, 'label_msg')].text = text
    poll(world)
    canvas, words, arts = scan_page(fw, win)
    assert [(n, x, y) for n, x, y in arts if n.startswith('oled_scan_')] == [(figure, 187 - 30, SCAN_FIG_Y)]
    assert (title, 187, SCAN_TITLE_CY) in [w[:3] for w in words] and (line, 187, SCAN_LINE_CY) in [w[:3] for w in words]
    assert 'Done' in said(words) and not world[named(world, win, 'msg_folder')].visible
    assert [c for r, c, _ in canvas.rounded if r[1] == SCAN_BTN_CY - 19] == [rgba(0x2160d8)]  # the primary button


def test_an_ending_a_paint_read_first_still_repaints_the_whole_stack(fw, world, st, whole, done):
    """Stock repaints its hidden label's box as it writes the ending; that paint's msg_sync sees the new text first,
    and the next poll still repaints the stack, so the button turns from Stop to Done with the rest."""
    win, threads = scan_dialog(fw, world, st, 'Scanned 100')
    count(fw, threads, 704)
    poll(world)
    done[0].value = 1
    world[named(world, win, 'label_msg')].text = 'Updated 738'
    scan_page(fw, win)  # stock's own repaint, before the poll
    before = len(world.invalid)
    poll(world)
    assert len(world.invalid) == before + 1 and world.invalid[-1] == (win, (0, 40, 375, 280))


def test_a_scan_window_lower_down_keeps_its_stack_s_place_on_the_screen(fw, world, st, whole, done):
    """The stack is in screen rows: the window's own origin is read when it paints."""
    win, _ = scan_dialog(fw, world, st, 'Scanned 9')
    poll(world)
    full = scan_page(fw, win)[1]
    world.box(win, 0, 26, 375, 320 - 26)
    low = scan_page(fw, win)[1]
    assert [(t, y - 26) for t, _, y, _ in full] == [(t, y) for t, _, y, _ in low]


# USB mode's page: the computer's eject.

USB_LUN = '/sys/kernel/config/usb_gadget/storage_demo/functions/mass_storage.msg/lun.0/file'


CC_SMART, CC_NOW, CC_BRIGHT, CC_VOLUME, CC_USB = 4, 6, 7, 8, 9  # control_center.c's stops


def control_center(fw, world, focus=0, adjust=0, ring=True, light=False, **config):
    """Control Center painted (control_center.c cc_paint): Wi-Fi on, Bluetooth off, nothing playing, the brightness at
    half; the wheel on focus, a slider adjusting or not, its ring showing (a tick just now, the clock at 0) or the page
    just pulled down; Light Mode on or off; config.ini's other keys. The canvas."""
    theme(fw, **({'LIGHT': '1'} if light else {}), **config)
    fw.var('g_wififlag', C.c_ubyte).value, fw.var('g_bluetoothflag', C.c_ubyte).value = 1, 0
    win, _, _ = quick_window(world)
    world[win].props.update(_cc_focus=focus, _cc_adjust=adjust, **({'_cc_seen': 1} if ring else {}))
    canvas = Canvas(fw)
    paint_bg(fw, win, canvas)
    return canvas


def cc_words(canvas):
    """The words the page set glyph by glyph (ui_set), spaces dropped: a word ends where the next glyph is on another
    row or more than 16 px on (wider than a glyph and a space at 13 px), or back to the left."""
    words, last = [], None
    for ch, (x, y, _, _), *_ in canvas.texts:
        if last is None or y != last[1] or not 0 < x - last[0] <= 16:
            words.append('')
        words[-1] += ch
        last = (x, y)
    return words


def test_control_center_is_v4_s_tightened_layout(fw, world):
    """v4, A tightened (mock_cc4_tight.py): one 10 px gap everywhere. The toggles' block 158 x 210 from (20, 38), two by
    three, circles 66 px apart both ways (centres x 66 and 132, y 68, 134, 200); Now Playing 188, 38, 167 x 114;
    Brightness at y 162 and Volume at y 210, each 167 x 38, ending level with the block; the grab bar along the
    screen's foot (test below); the
    wheel's white ring on Wi-Fi. No tiles. Every name set, Smart Wi-Fi's whole."""
    canvas = control_center(fw, world)
    accent, block, off = rgba(0x2160d8), rgba(0x1c1c1e), rgba(0x3a3a3c)
    boxes = [(r, c) for r, c, _ in canvas.rounded]
    assert ((20, 38, 158, 210), block) in boxes and ((188, 38, 167, 114), block) in boxes
    circles = [r for r, c, radius in canvas.rounded if radius == 20 and r[2] == 40]
    assert circles == [(cx - 20, cy - 20, 40, 40) for cy in (68, 134, 200) for cx in (66, 132)]
    assert ((46, 48, 40, 40), accent) in boxes and ((112, 48, 40, 40), off) in boxes  # Wi-Fi on, Bluetooth off
    assert ((43, 45, 46, 46), rgba(0xffffff)) in boxes  # the wheel's ring round Wi-Fi
    assert ((188, 162, 167, 38), off) in boxes and ((188, 210, 167, 38), off) in boxes
    assert not [r for r, c, radius in canvas.rounded if radius == 12]  # v3's tiles' corners
    assert cc_words(canvas)[:6] == ['Wi-Fi', 'Bluetooth', 'AirPlay', 'Car', 'SmartWi-Fi', 'EQ']


@pytest.mark.parametrize('light', [False, True])
def test_the_grab_bar_stands_on_the_sheet_s_foot_as_the_page_sheets_stand_off_their_top(fw, world, light):
    """The grab bar is centred, 36 x 5, 6 px off the sheet's foot (the screen's, y 320), the gap menu_modal.c's sheets keep
    off their top (grab at 46 under a top of 40); not 10 px under the blocks, which end at y 248. It is the one thing
    that moved: the blocks stay where the layout test puts them."""
    grabs = [(r, c) for r, c, radius in control_center(fw, world, light=light).rounded if r[2:] == (36, 5)]
    assert [r for r, _ in grabs] == [(375 // 2 - 18, 309, 36, 5)] and 320 - (309 + 5) == 6


USB_ROW = (20, 258, 335, 38)  # under the cluster (its foot y 248, 10 px), its width, the sliders' 38 px


@pytest.mark.parametrize('light', [False, True])
@pytest.mark.parametrize('cable', [0, 1])
def test_a_cable_in_adds_the_usb_row_under_the_cluster(fw, world, cable, light):
    """design/usb-row mock_usb_row.py cc(): with a cable in, a full-width row in the block's colour, fully round as the
    sliders: the USB glyph centred 22 px in, "USB" from 40 px in, the remembered choice in Text Secondary ending 30 px
    from its end and the chevron centred 18 px from it. No cable, no row: the sheet as it shipped."""
    fw.var('g_usbdet_value', C.c_ubyte).value = cable
    canvas = control_center(fw, world, light=light, USBPICK='1')
    rows = [(r, c, radius) for r, c, radius in canvas.rounded if r == USB_ROW]
    assert rows == ([(USB_ROW, rgba(0xe3e3e1 if light else 0x1c1c1e), 19)] if cable else [])
    words = [(t, box, c) for t, box, c, _ in canvas.texts if box[1] <= 277 <= box[1] + box[3]]
    arts = [(n, dst) for n, dst in canvas.arts if 258 <= dst[1] <= 296]
    if not cable:
        assert not words and not arts
        return
    (usb, ubox, uink), (value, vbox, vink) = sorted(words, key=lambda w: w[1][0])
    assert (usb, ubox[0], value, vbox[0] + vbox[2] - 1) == ('USB', 60, 'Charge Only', 325)  # ui_text's box: ink + 1
    assert vink == rgba(0x545454 if light else 0xababab) and uink == rgba(0 if light else 0xffffff)
    tail = '_l' if light else ''
    assert [(n.split('_cc_')[1], dst[0]) for n, dst in sorted(arts, key=lambda a: a[1][0])] == [
        ('usb' + tail, 42 - 10), ('chev' + tail, 337 - 6)]


def test_the_wheel_on_the_usb_row_rings_it_as_the_mock(fw, world):
    """The ring the mock draws for the row: 2 px, 5 px out, so its outer box is 10 px bigger each way."""
    fw.var('g_usbdet_value', C.c_ubyte).value = 1
    canvas = control_center(fw, world, focus=CC_USB)
    assert [c for r, c, _ in canvas.rounded if r == (15, 253, 345, 48)] == [rgba(0xffffff)]


def test_a_slider_with_the_wheel_takes_the_accent_s_ring(fw, world):
    ring = (185, 159, 173, 44)  # Brightness's, 3 px out
    assert [c for r, c, _ in control_center(fw, world, focus=CC_BRIGHT, adjust=1).rounded if r == ring] == [rgba(0x2160d8)]
    assert [c for r, c, _ in control_center(fw, world, focus=CC_BRIGHT).rounded if r == ring] == [rgba(0xffffff)]


def test_pulled_down_the_page_shows_no_ring_and_the_now_playing_block_takes_one(fw, world):
    """No wheel input yet: no ring anywhere. On the Now Playing block the ring goes round the whole block."""
    boxes = lambda canvas: [r for r, c, _ in canvas.rounded if c == rgba(0xffffff)]
    assert boxes(control_center(fw, world, ring=False)) == []
    assert boxes(control_center(fw, world)) == [(43, 45, 46, 46)]  # Wi-Fi's
    assert boxes(control_center(fw, world, focus=CC_NOW)) == [(185, 35, 173, 120)]


SCREEN = 375 * 320
CC_BEHIND, CC_INK = 0xff1c1c1e, 0xffffffff


def inter_pens(text, px=16):
    """text's pens as Inter Medium sets it at AWTK size px: each glyph's start from the first's, exact, in px."""
    from fontTools.ttLib import TTFont
    font = TTFont(host.ROOT/'assets/fonts/Inter-Medium.ttf')
    cmap, hmtx, line = font.getBestCmap(), font['hmtx'], font['hhea'].ascent - font['hhea'].descent
    pens, at = [], 0.0
    for ch in text:
        pens.append(at)
        at += hmtx[cmap[ord(ch)]][0] * px / line
    return pens, at


def fb_canvas(fw):
    """A Canvas over a 375 x 320 BGRA buffer of the block's colour whose glyphs ink: each text stock draws inks its rect's
    first 6 columns over rows 4 above to 4 below its row, as far as the clip lets it. (canvas, the buffer)"""
    canvas = Canvas(fw)
    fb = (C.c_uint * SCREEN)(*([CC_BEHIND] * SCREEN))
    struct.pack_into('<Q', canvas.lcd, O['LCD_OFFLINE_FB'], C.addressof(fb))
    for k, v in (('LCD_W', 375), ('LCD_H', 320), ('LCD_LINE_LENGTH', 4 * 375), ('LCD_FORMAT', O['LCD_BGRA8888'])):
        struct.pack_into('<i', canvas.lcd, O[k], v)
    canvas.clip = (0, 0, 375, 320)
    fw.mock('canvas_set_clip_rect', lambda c, r, *_: setattr(canvas, 'clip', struct.unpack('<4i', C.string_at(r, 16))) or 0)

    def ink(c, s, n, r, *_):
        canvas.texts.append((wide(s, n), rect(r), canvas.text_color, canvas.font))
        x, y, w, h = rect(r)
        cx, cy, cw, ch = canvas.clip
        for yy in range(max(y + h // 2 - 4, cy), min(y + h // 2 + 4, cy + ch)):
            for xx in range(max(x, cx), min(x + 6, cx + cw)):
                fb[yy * 375 + xx] = CC_INK
        return 0
    fw.mock('canvas_draw_text_in_rect', ink)
    return canvas, fb


def set_in(fw, text, x, w, off=0, y=275):
    """ui_kit.c ui_set_in: text in Inter Medium 13 (AWTK 16) in the column x to x + w on row y, scrolled off px."""
    theme(fw)
    canvas, fb = fb_canvas(fw)
    fn = fw.fn('ui_set_in', C.c_int, C.c_void_p, C.c_char_p, C.c_int, C.c_int, C.c_uint, C.c_int, C.c_int, C.c_int,
               C.c_int, C.c_int)
    width = fn(canvas.p, text.encode(), 1, 16, 0xffffff, x, w, y, off, 0)
    return canvas, fb, width


def test_a_toggle_s_name_is_set_at_inter_s_own_advances(fw, world):
    """ui_set: each glyph of "Bluetooth" starts at its exact pen from Inter Medium's advances, rounded on its own (AWTK
    rounds every advance and carries the error), centred in its 76 px room on its circle (x 132); "Smart Wi-Fi" (75 px)
    fits its room centred on x 66, its space Inter's 3.5 px, not the 7 AWTK leaves. The names' scrollers hold each
    name's width (cc_marq's range)."""
    theme(fw)
    win, _, _ = quick_window(world)
    names = [world.add(win, f'cc_name{i}', 'hscroll_label', box=(0, 0, 80, 16)) for i in range(6)]
    world[win].props.update(_cc_focus=0, _cc_seen=1)
    canvas, _ = fb_canvas(fw)
    paint_bg(fw, win, canvas)
    pens, w = inter_pens('Bluetooth')
    blue = [r[0] for t, r, *_ in canvas.texts if r[1] == 68 + 29 - 16 and r[0] > 99]
    assert blue == [94 + (76 - round(w)) // 2 + int(p + 0.5) for p in pens]
    pens, w = inter_pens('Smart Wi-Fi')
    smart = [r[0] for t, r, *_ in canvas.texts if r[1] == 200 + 29 - 16 and r[0] < 104]
    assert smart == [28 + (76 - round(w)) // 2 + int(p + 0.5) for k, p in enumerate(pens) if k != 5]
    widths = [struct.unpack_from('<i', world[n].block, O['HSCROLL_TEXT_W'])[0] for n in names]
    assert widths == [round(inter_pens(t)[1]) for t in ('Wi-Fi', 'Bluetooth', 'AirPlay', 'Car', 'Smart Wi-Fi', 'EQ')]


def test_text_wider_than_its_column_fades_out_at_its_edge_inside_its_box(fw):
    """ui_set_in, "Smart Wi-Fi" (75 px) in a 68 px column from x 24 on row 275 (rows 267 to 283): it starts at the
    column's left, its text whole there; the right end's last column shows only what is behind it, the columns before
    it a blend (the text itself fading, no band painted); nothing is drawn outside the column."""
    _, fb, width = set_in(fw, 'Smart Wi-Fi', 24, 68)
    px = lambda x, y: fb[y * 375 + x]  # noqa: E731
    assert width == round(inter_pens('Smart Wi-Fi')[1])
    rows = range(255, 300)
    assert all(px(x, y) == CC_BEHIND for y in rows for x in (*range(14, 24), *range(92, 100)))
    assert all(px(91, y) == CC_BEHIND for y in rows)  # the end column: the text weighs 0 there
    assert CC_INK in [px(x, 275) for x in range(24, 30)]  # the start whole
    ramp = {px(x, 275) for x in range(80, 91)} - {CC_BEHIND, CC_INK}
    assert ramp and not [y for y in (*range(255, 267), *range(283, 300)) for x in range(24, 92) if px(x, y) != CC_BEHIND]


def test_scrolled_text_fades_at_both_ends(fw):
    """Scrolled off its start (the marquee's xoffset) the left end fades too; at the end of its range only the left."""
    _, fb, _ = set_in(fw, 'Smart Wi-Fi', 24, 68, off=3)
    assert all(fb[y * 375 + 24] == CC_BEHIND for y in range(267, 283)) and all(fb[y * 375 + 91] == CC_BEHIND for y in range(267, 283))
    _, fb, _ = set_in(fw, 'Smart Wi-Fi', 24, 68, off=7)  # 75 - 68: its end in view
    assert all(fb[y * 375 + 24] == CC_BEHIND for y in range(267, 283)) and CC_INK in [fb[275 * 375 + x] for x in range(84, 92)]


def test_text_that_fits_its_column_is_centred_and_unclipped(fw):
    canvas, fb, width = set_in(fw, 'EQ', 24, 68)
    assert canvas.clip == (0, 0, 375, 320) and [r[0] for _, r, *_ in canvas.texts][0] == 24 + (68 - width) // 2


@pytest.mark.parametrize('flag, image, online, glyph', [
    (0, 'bar_wifi0', 1, 'cc_wifi_slash'),  # off
    (0, 'bar_wifi3', 1, 'cc_wifi_3'),      # the status row's bars win: the two never disagree
    (1, 'bar_wifi0', 1, 'cc_wifi_0'),      # on, not joined
    (1, 'bar_wifi1', 1, 'cc_wifi_1'), (1, 'bar_wifi2', 1, 'cc_wifi_2'),
    (1, 'bar_wifi3', 1, 'cc_wifi_3'), (1, 'bar_wifi4', 1, 'cc_wifi_3'),
    (1, 'bar_wifi2', 0, 'cc_wifi_x'),      # joined, no link up
])
def test_the_wi_fi_circle_shows_the_link_as_the_status_row_reads_it(fw, world, st, flag, image, online, glyph):
    """control_center.c cc_wifi, from status_row.c's wifi_level (stock's bars) and wifi_offline: wifi-slash off,
    wifi-none on but not joined, wifi-low to wifi-high by the bars (3 and 4 full), wifi-x with no link up."""
    fw.mock('networkGetConnectStatus', lambda *_: online)
    bar = world.window('system_bar', 'system_bar', box=(0, 0, 375, 26), top=False)
    st('bar_wifi', C.c_void_p).value = world.add(bar, 'img_wifi', 'image', image=image)
    theme(fw)
    fw.var('g_wififlag', C.c_ubyte).value = flag
    win, _, _ = quick_window(world)
    world[win].props.update(_cc_focus=0)
    canvas = Canvas(fw)
    paint_bg(fw, win, canvas)
    assert [n for n in canvas.names if '_cc_wifi' in n] == [f'oled_{glyph}']


@pytest.mark.parametrize('light', [False, True])
def test_light_mode_inks_the_glyphs_on_light_ground_and_keeps_a_lit_circle_s_white(fw, world, light):
    """Light Mode (tools/art.py CC_LIGHT): an off circle's glyph, the transport and the sliders' sun and speaker take
    their _l art; a lit circle (Wi-Fi here) keeps its white glyph on Accent Blue."""
    names = [n for n in control_center(fw, world, light=light).names if '_cc_' in n]
    lit = [n for n in names if 'cc_wifi' in n]
    off = [n for n in names if n.split('_cc_')[1].split('_l')[0] in ('bluetooth', 'airplay', 'car', 'leaf', 'eq', 'sun',
                                                                     'speaker', 'back', 'play', 'pause', 'forward')]
    assert lit and not [n for n in lit if n.endswith('_l')]
    assert off and all(n.endswith('_l') == light for n in off)


def landed(fw, world, t, **keys):
    """Control Center painted t ms after it came to rest open: each rounded box with the canvas's y offset then."""
    now = [100000 + t]
    fw.mock('time_now_ms', lambda *_: now[0])
    theme(fw, **keys)
    win, _, _ = quick_window(world)
    world[win].props.update(_cc_landed=100001)
    canvas = Canvas(fw)
    drawn = []
    fw.mock('canvas_fill_rounded_rect', lambda c, r, bg, color, radius, *_: drawn.append(
        (rect(r), C.c_uint.from_address(color).value, struct.unpack_from('<i', canvas.raw, O['CANVAS_Y'])[0])))
    paint_bg(fw, win, canvas)
    assert struct.unpack_from('<i', canvas.raw, O['CANVAS_Y'])[0] == 0  # given back
    return drawn


@pytest.mark.parametrize('t, dy', [(0, 0), (50, 8), (140, 0), (190, -2), (279, 0), (280, 0), (5000, 0)])
def test_landing_open_the_blocks_overshoot_8_px_and_settle_by_280_ms(fw, world, t, dy):
    """The landing's spring (cc_springs): every block, circle and slider drawn dy lower (the canvas's offset)."""
    drawn = landed(fw, world, t)
    blocks = [d for d in drawn if d[0] == (20, 38, 158, 210)]
    assert blocks and {y for _, _, y in drawn} == {dy}


@pytest.mark.parametrize('t', [50, 190])
def test_under_reduce_motion_control_center_lands_still(fw, world, t):
    """A spring is MO_SPATIAL (ui_kit.h): with IPOD REDUCEMOTION on the blocks are at rest from the landing."""
    drawn = landed(fw, world, t, REDUCEMOTION='1')
    assert drawn and {y for _, _, y in drawn} == {0}


def test_the_sliders_fill_to_their_eased_levels(fw, world):
    """Control Center's sliders draw their bars' eased levels (levels.c), as Now Playing's volume bar does: the volume
    at 100 eased to 50, the brightness at 50 eased to 25."""
    fw.var('g_volume', C.c_ubyte).value = 100
    struct.pack_into('<iiI', fw.var('vol_bar', C.c_char * 12), 0, 50 << 8, 1, 0)
    struct.pack_into('<iiI', fw.var('bright_bar', C.c_char * 12), 0, 25 << 8, 1, 0)
    canvas = control_center(fw, world)
    fills = [r for r, c, _ in canvas.rounded if c == rgba(0xe5e5ea)]
    assert (188, 210, 167 * 128 // 256, 38) in fills and (188, 162, 167 * 64 // 256, 38) in fills


def usb_page(fw, world, st, mode=0):
    theme(fw)
    fw.var('g_usbmode', C.c_ubyte).value = mode
    win = world.window('usbmode_page')
    world.add(win, 'img_usbmode', 'image')
    world.add(win, 'scrlabel_info', 'hscroll_label', box=(20, 200, 335, 30), text='USB Connected')
    ui_load(fw, st, b'usbmode_page', win)
    return win


def test_usb_audio_mode_has_nothing_to_eject(fw, world, st):
    usb_page(fw, world, st, mode=1)
    assert world.timers == []


def test_the_computer_s_eject_says_ok_to_disconnect(fw, world, st):
    """Its LUN read open, then empty: the computer ejected the card, and the mode page (usb_ui.c usb_ui_ejected) draws
    OK to Disconnect again; the poll ends."""
    files = Files(fw, {USB_LUN: b'/dev/mmcblk0p1\n'})
    win = usb_page(fw, world, st)
    (_, ctx, ms), = world.timers
    assert ctx == win and ms == 500
    assert poll(world) == RET_REPEAT and st('usb_lun').value == 1  # the LUN open: storage in use
    files.files[USB_LUN] = b''
    assert poll(world) == RET_REMOVE and world.invalid[-1] == (win, None)


@pytest.mark.parametrize('files', [{}, {USB_LUN: b''}])
def test_no_lun_open_yet_is_no_eject(fw, world, st, files):
    """No file: the gadget isn't built; an empty one before it ever read open isn't an eject either."""
    Files(fw, dict(files))
    usb_page(fw, world, st)
    assert [poll(world) for _ in range(3)] == [RET_REPEAT] * 3 and st('usb_lun').value == 0


# Loading a window.

def test_a_loaded_window_is_stock_s_root(fw, world, st):
    theme(fw)
    win = world.window('albums_page')
    assert ui_load(fw, st, b'albums_page', win) == (win, ['albums_page'])
    assert ui_load(fw, st, b'albums_page', 0)[0] == 0


# The full-screen art (bigcover_page_init).

def test_the_full_screen_art_shows_its_name_then_fades_it(fw, world, st):
    """The name and its shade shown BC_MS, then faded together, BC_STEPS steps BC_STEP_MS apart, to nothing."""
    theme(fw)
    fw.mock('stock_bigcover_trampoline', lambda *_: 4)
    win = world.window('bigcover_page')
    title, shade = world.add(win, 'scrlabel_title', 'hscroll_label'), world.add(win, 'view_shade')
    assert fw.fn('ringnav_bigcover', C.c_int, C.c_void_p, C.c_void_p)(win, 0x55) == 4
    assert (st('bc', C.c_void_p * 3)[:]) == [title, shade, win]

    def shown():
        seen = {}
        for _, a in fw.calls('widget_set_opacity'):
            seen.setdefault(a[0], []).append(a[1])
        return seen
    assert shown() == {title: [255], shade: [255]}
    (_, _, ms), = world.timers
    assert ms == O['BC_MS'] and world.fire(1) == RET_OK  # the wait ends, the steps begin
    (_, _, step), = world.timers[1:]
    while world.fire(2) == RET_REPEAT:
        pass
    steps = shown()[title]
    assert step == O['BC_STEP_MS'] and shown()[shade] == steps and len(steps) == 1 + O['BC_STEPS']
    assert steps == sorted(steps, reverse=True) and len(set(steps)) == len(steps) and steps[-1] == 0
    for gone in world.on(win, EVT_DESTROY):
        gone()
    assert st('bc', C.c_void_p * 3)[:] == [None, None, None]


def test_closing_the_full_screen_art_drops_its_fade(fw, world, st):
    theme(fw)
    fw.mock('stock_bigcover_trampoline', lambda *_: 4)
    win = world.window('bigcover_page')
    world.add(win, 'scrlabel_title', 'hscroll_label'), world.add(win, 'view_shade')
    fw.fn('ringnav_bigcover', C.c_int, C.c_void_p, C.c_void_p)(win, 0x55)
    for gone in world.on(win, EVT_DESTROY):
        gone()
    assert world.removed == [1] and st('bc', C.c_void_p * 3)[:] == [None, None, None]


def test_no_full_screen_art_window_is_stock_s_answer(fw, world):
    theme(fw)
    fw.mock('stock_bigcover_trampoline', lambda *_: 3)
    assert fw.fn('ringnav_bigcover', C.c_int, C.c_void_p, C.c_void_p)(None, 0x55) == 3
    assert world.handlers == [] and world.timers == []


# The theme's boot, at the system bar's load (application_init's first window).

def boot(fw, world, st):
    bar = world.window('system_bar', 'system_bar', top=False)
    fw.mock('stock_ui_load_trampoline', lambda *_: bar)
    load = fw.fn('ringnav_ui_load', C.c_void_p, C.c_char_p, C.c_void_p)
    assert load(b'system_bar', 0) == bar
    return load


def test_the_system_bar_s_load_boots_the_theme(fw, world, st):
    """theme_boot (a retired theme's migration) runs at the first system bar load; the load goes on to stock's."""
    theme(fw)
    load = boot(fw, world, st)
    assert st('theme_booted').value == 1 and load(b'system_bar', 0)


# Boot may paint Home before the screen is usable: once the list is reachable, it is repainted.

def test_the_first_reachable_list_is_repainted_once(fw, world, st):
    theme(fw)
    bar = system_bar(fw, world)
    win = world.window('games_page')
    rows = world.add(win, 'list', 'scroll_view')
    C.c_ubyte.from_buffer(world[rows].block, O['VIEW_VERTICAL']).value = 1
    paint_bg(fw, bar, Canvas(fw))
    assert (rows, None) not in world.invalid  # the screen isn't usable yet
    fw.var('g_backlight_status', C.c_ubyte).value = 1
    paint_bg(fw, bar, Canvas(fw))
    paint_bg(fw, bar, Canvas(fw))
    assert world.invalid.count((rows, None)) == 1


@pytest.mark.parametrize('light, ink', [('0', 'd'), ('1', 'l')])
def test_over_oleds_now_playing_the_volume_is_its_bar_between_two_speakers(fw, world, st, theme_t, light, ink):
    """A status row's set (OLED's): the bar keeps its rows, inset VOL_SPK_INSET at each end, white on the track; a
    16 px speaker (Phosphor's speaker-simple-none, -high) centred VOL_SPK_X in from each end of the progress bar on
    its centre line; no number (the "A: iPod speakers" mock-up)."""
    theme(fw, LIGHT=light)  # OLED
    dialog, *_ = volume(fw, world, st, 37, under='playing_page', np=True)
    canvas = Canvas(fw)
    vol_paint(fw, dialog, canvas)
    inset, spk = O['VOL_SPK_INSET'], O['VOL_SPK_X']
    assert (inset, spk, O['VOL_SPK_PX']) == (24, 8, 16)  # tools/art.py draws them at VOL_SPK_PX too
    bar = (66 + inset, 245 + (20 - 4) // 2, 243 - 2 * inset, 4)  # volume()'s slider_play: x 66, w 243, bar 4
    assert [(r, radius) for r, _, radius in canvas.rounded] == [(bar, 2), ((bar[0], bar[1], bar[2] * 37 // 100, 4), 2)]
    if light == '0':  # the track, and the fill white (ui_ink flips both for Light Mode's paper)
        assert [c for _, c, _ in canvas.rounded] == [rgba(theme_t('np_track_rgb', C.c_uint)), 0xffffffff]
        assert (0, 245, 375, 270 + 16 - 245, 0xff000000) in canvas.fills  # the times stay papered over
    cy = bar[1] + 2
    half = O['VOL_SPK_PX'] // 2  # placed as VOL_SPK_PX squares (the mock's images are 20 x 10)
    assert canvas.art(f'oled_vol_lo_{ink}') == [(66 + spk - half, cy - half, 20, 10)]
    assert canvas.art(f'oled_vol_hi_{ink}') == [(66 + 243 - spk - half, cy - half, 20, 10)]
    assert canvas.texts == []


def test_an_empty_folder_level_says_so(fw, world):
    """Folder (folder_page) over a level with nothing in it, an empty card or a folder with no music: stock's page
    is its table alone, which would leave it blank. It shows the empty state, "No Folders", written once per change of
    the level's emptiness, and it goes when the level has rows. Another page gets nothing."""
    theme(fw)
    deques = Deques(fw)
    showlist = fw.var('fw_ctx_p_deque_showlist', C.c_uint)
    folder = fw.fn('feedback_folder', None, C.c_void_p)
    win = world.window('folder_page')
    world.add(win, 'tableview_folder', 'table_view')
    level = deques.new()
    showlist.value = level
    folder(win)
    assert shown_empty(world, win) == ('folder', 'No Folders', 'Copy music to the SD card')
    writes = len(fw.calls('widget_set_prop_str'))
    folder(win)
    assert len(fw.calls('widget_set_prop_str')) == writes  # unchanged: not written
    deques[level].append(1)
    folder(win)
    assert shown_empty(world, win) is None
    other = world.window('allmusic_page')
    deques[level].clear()
    folder(other)
    assert not world.find(other, '_empty_state')


CC_ART_AT = (200, 50, 52, 52)  # control_center.c: the Now Playing block's cover, CC_ART square, CC_ART_IN in


def cc_playing(fw, world, mcl, recs, path, cover_type=0, light=False):
    """Control Center over a queue playing one record at path, by artist B on album A; the player's cover thread has
    parsed it as cover_type (now_cover: 1 embedded, 2 folder image, 3 none; 0 not parsed). The canvas."""
    deques = Deques(fw)
    fw.var('fw_ctx_mcl_pdeqplaylist', C.c_uint).value = deques.new([recs(path=path, title='Song', artist='B', album='A')])
    mcl.pos = 0
    fw.var('fw_ctx_g_lastcover_url', C.c_char * 1024).value = path.encode() if cover_type else b''
    fw.var('g_playcover_type', C.c_ubyte).value = cover_type
    canvas = control_center(fw, world, light=light)
    canvas.keep = deques
    return canvas


def test_control_center_shows_the_cover_home_shows(fw, world, mcl, recs):
    """Control Center's Now Playing block takes the song's cover from the same lookup as Home's pane and the modal
    header (coverflow.c song_cover_url): the player's file once it has parsed the song (embedded art or a folder
    image, its cached bitmap dropped when the song changes, as the player writes each song's to one file), else Cover
    Flow's cache of the album; none is the block's grey square. Before, the block asked for the
    player's file as "file://file://..." and showed the grey square for every song (the device, 2026-11-16: 'Heart
    Shaped Box' blank while Home showed its cover)."""
    files = Files(fw)
    Externpic(fw).write()  # the player's file, stat'd (now_cover_written): one write, so every paint sees one key
    for kind in (1, 2):
        canvas = cc_playing(fw, world, mcl, recs, '/mnt/mmc/Music/a.flac', cover_type=kind)
        name = 'file:///tmp/coverpic.jpg' if kind == 1 else 'file:///tmp/externpic.jpg'
        assert canvas.art(name) == [CC_ART_AT], (kind, sorted(set(canvas.loaded.values())))
    unloads = lambda: fw.calls('image_manager_unload_bitmap_by_name')  # the name was cc_paint's own buffer
    before = len(unloads())
    paint_bg(fw, world.top, Canvas(fw))  # the same song and file: the cached bitmap stays
    assert len(unloads()) == before
    cc_playing(fw, world, mcl, recs, '/mnt/mmc/Music/b.flac', cover_type=1)  # the next song, the same file
    assert len(unloads()) == before + 1
    key = fw.fn('tags_key', C.c_uint, C.c_char_p, C.c_char_p)(b'B', b'A')
    cached = f'/mnt/mmc/.coverflow/{key:08x}.jpg'
    files.files[cached] = b'\xff\xd8jpeg'
    canvas = cc_playing(fw, world, mcl, recs, '/mnt/mmc/Music/a.flac', cover_type=3)  # the player found none
    assert canvas.art('file://' + cached) == [CC_ART_AT]
    del files.files[cached]
    canvas = cc_playing(fw, world, mcl, recs, '/mnt/mmc/Music/a.flac', cover_type=3)
    assert canvas.arts == [a for a in canvas.arts if a[1] != CC_ART_AT]
    assert (CC_ART_AT, rgba(0x3a3a3c)) in [(r, c) for r, c, _ in canvas.rounded]  # the grey square


@pytest.mark.parametrize('light', [False, True])
def test_control_center_s_cover_corners_are_the_block_s_colour_as_painted(fw, world, mcl, recs, light):
    """The cover's rounded corners are scrubbed in the block's colour as the screen shows it: Light Mode's block is
    the flipped one, so a dark corner would show on it (the emulator, 2026-11-16, once the cover showed)."""
    canvas = cc_playing(fw, world, mcl, recs, '/mnt/mmc/Music/a.flac', cover_type=1, light=light)
    flip = fw.fn('flip_if', C.c_uint, C.c_int, C.c_uint)
    block = [c for r, c, _ in canvas.rounded if r == (188, 38, 167, 114)]
    x, y = CC_ART_AT[:2]
    corner = [c for fx, fy, w, h, c in canvas.fills if (fx, fy, h) == (x, y, 1)]
    want = flip(int(light), 0x1c1c1e)
    assert corner and set(corner) == {rgba(want) & 0xffffff | 0xff000000} and block


def test_a_status_pages_stack_has_its_lines_a_bar_and_a_footer(fw, st):
    """docs/oled-design.md#sync: the hint's lines each on their own 20 px band, a 4 px bar 10 px under the title when
    it has one (Track, filled in the accent to its permille), and the footer centred on y 304 in 13 px Text Tertiary."""
    from test_nav_painters import Canvas
    canvas = Canvas(fw)
    fw.mock('canvas_get_vgcanvas', lambda *_: 1)
    w = C.create_string_buffer(0x200)
    e = EmptyT(b'', b'Syncing 4 of 12', b'Rockstar\nPost Malone', None, b'Keep the Q2 on Wi-Fi', 334)
    fw.fn('empty_paint', None, C.c_void_p, C.c_void_p, C.POINTER(EmptyT), C.c_int)(C.addressof(w), canvas.addr, C.byref(e), 100)
    texts = [(t[0], t[1][1] + t[1][3] // 2) for t in canvas.texts]
    assert [t for t, _ in texts] == ['Syncing 4 of 12', 'Rockstar', 'Post Malone', 'Keep the Q2 on Wi-Fi']
    title, l1, l2, foot = (y for _, y in texts)
    assert l1 - title > 24 + 2 * 10 and l2 - l1 == 20 and foot == 304
    fills = fw.calls('vgcanvas_fill')
    assert len(fills) == 2  # the track, then the fill to 333 permille
