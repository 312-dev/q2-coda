"""coverflow.c on its device path: Home's pan and cubic filter, the shelf's geometry, touch, the album order and the art cache."""
import ctypes as C
import math
import pathlib
import random
import re
import sqlite3
import struct
import unicodedata
from types import SimpleNamespace

import pytest

import host
from conftest import O, Deques, Externpic, Files, ask_cover, high_halves, in_fork, stock_prop_str

ROOT = pathlib.Path(__file__).resolve().parents[2]
CX, TOP, ART, ONE, MISS = 187, 13, 160, 65536, 99
VIEW_W, VIEW_H = 375, 220
HOME_PW = 188  # coverflow.c HOME_PW: the widest pane, home.ring's and home.held's rows
# Split's art pane as home_render fills it under the geometry set the build bakes (coverflow.c pane_w, pane_h), and a
# cover zoomed 110% to cover it (slide_load)
_G = host.tools_theme.built_geometry(host.tools_theme.builtins())
PANE_W, PANE_H = 375 - _G.split_art, 320 - _G.bar + _G.split_art_up
TEX_H = PANE_H * 11 // 10
LOADING, COVERS, TRACKS = range(3)
RATED_ALBUMS, PLAYLISTS = 1, 3  # coverflow.h's cf.scoped
CF_REACH, CF_WIDE_REACH = 3, 7  # the ring's slots either side of the centre: fx_ring's, and its callers' window
SORT_ALBUM, SORT_ARTIST, SORT_ADDED, SORT_PLAYED, SORT_RANDOM, SORT_RATING, SORT_YEAR = range(7)
FNV = 2166136261
r = C.byref


class Cf(C.Structure):
    _fields_ = [*((k, C.c_void_p) for k in 'page body covers slide name artist title albums tracks stock'.split()),
                *((k, C.c_int) for k in 'sort sort_read year_old artists on_sort resort'.split()),
                ('jobs', C.c_void_p), ('thread', C.c_ulong), ('timer', C.c_uint), ('title_timer', C.c_uint),
                ('art_timer', C.c_uint), ('art_at', C.c_uint), ('art_told', C.c_int), ('art_ring', C.c_int), ('want', C.c_int),
                ('titled', C.c_int), ('offline', C.c_int),
                ('saved_album', C.c_uint), ('albums_gen', C.c_uint),
                *((k, C.c_int) for k in 'screen album running done total cancel scoped all_sort'.split()),
                *((k, C.c_void_p) for k in 'all_albums all_stock hearts'.split()), ('count', C.c_int)]


class Touch(C.Structure):
    _fields_ = [*((k, C.c_int) for k in 'down drag caught x y from_ n'.split()), ('sx', C.c_int * 8), ('st', C.c_uint * 8)]


class Slide(C.Structure):
    _fields_ = [('tex', C.c_void_p), *((k, C.c_int) for k in 'tw th u0 v0 rx ry lib'.split()), ('kb', (C.c_int * 3) * 2),
                ('ms', C.c_uint)]


class Home(C.Structure):
    _fields_ = [*((k, C.c_void_p) for k in 'win art list sets lib stream'.split()),
                *((k, C.c_uint) for k in 'key repaint drift drift_last tick'.split()), ('sl', Slide * 2),
                ('frame', C.c_void_p), *((k, C.c_uint) for k in 'fade drawn'.split()), ('show', C.c_int), ('seed', C.c_uint),
                ('row', C.c_void_p), ('kind', C.c_int), ('pane_timer', C.c_uint),
                ('ring', (C.c_uint * HOME_PW) * 4), ('clip', C.c_int * 4), ('clipped', C.c_int),
                ('ink_bar', C.c_void_p), ('ground', C.c_uint), ('held', C.c_ubyte * HOME_PW),
                ('np', C.c_int), ('folders', C.c_int), ('np_stop', C.c_uint), ('np_timer', C.c_uint), ('rtick', C.c_uint)]


class Row(C.Structure):
    _fields_ = [('a', C.c_void_p), ('j', C.c_uint)]


class Pose(C.Structure):
    _fields_ = [(k, C.c_int) for k in 'xc zc c s bright eye'.split()]


class Job(C.Structure):
    _fields_ = [('track', C.c_char_p), ('key', C.c_uint), ('at', C.c_int), ('shared', C.c_ubyte), ('made', C.c_ubyte)]


class Dirent(C.Structure):
    _fields_ = [('ino', C.c_uint), ('off', C.c_uint), ('reclen', C.c_ushort), ('d_type', C.c_ubyte), ('d_name', C.c_char * 256)]


def api(fw):
    ptr, i, u = C.c_void_p, C.c_int, C.c_uint
    fns = {'coverflow__bspline_w': (None, u, ptr), 'home_sharpen': (None, ptr, i, i), 'coverflow__home_fit': (None, u, u, i, ptr, ptr),
           'coverflow__home_render': (None, ptr, i, ptr, i, i, i, u), 'coverflow__drift_at': (i, i, u, u),
           'coverflow__slide_at': (None, ptr, ptr, ptr, ptr), 'coverflow__velocity': (i, u), 'coverflow__sample': (None, i, u),
           'coverflow__round_stride': (i, i, i), 'coverflow__floor_div': (i, i, i), 'covers_live': (i,),
           'coverflow_hit': (i, i, i, i), 'coverflow_render': (None, ptr, i, i, ptr), 'pose': (None, i, ptr),
           'project': (i, ptr, i), 'unproject': (i, ptr, i, ptr, ptr), 'coverflow__slot': (i, i, i, i),
           'coverflow__lower': (u, ptr, u, ptr, ptr), 'coverflow__album_in': (i, ptr, u, ptr), 'coverflow__album_index': (i, ptr),
           'coverflow__name_cmp': (i, ptr, ptr), 'coverflow__tie': (i, i, ptr, ptr), 'coverflow__by_name': (i, ptr, ptr),
           'coverflow__by_album': (i, ptr, ptr), 'album_cmp': (i, ptr, ptr), 'album_id': (u, ptr), 'album_dir': (u, C.c_char_p, i),
           'folder_cmp': (i, ptr, ptr), 'coverflow__cover_artist': (C.c_char_p, ptr), 'coverflow__initial': (u, i),
           'coverflow_jump': (i, ptr, i, i, ptr), 'coverflow__bound': (i, i, i, i), 'coverflow__unbound': (i, i, i),
           'coverflow__release': (None, i), 'coverflow__touch': (i, ptr, ptr), 'stride_of': (i, ptr),
           'coverflow__stamp': (u, ptr), 'coverflow__lift': (None, i, u), 'coverflow__sort_saved': (i,),
           'coverflow_sort_name': (C.c_char_p,), 'coverflow_sort_set': (None, i),
           'coverflow_sort_now': (i,), 'coverflow_sort_count': (i,), 'coverflow_sort_at': (C.c_char_p, i), 'coverflow__sort_next': (i, i),
           'blob_io': (i, C.c_char_p, C.c_char_p, ptr, u, i), 'file_size': (C.c_long, C.c_char_p),
           'coverflow__ff_run': (i, C.c_char_p, C.c_char_p, i, i, ptr), 'coverflow__build_art': (None, ptr),
           'coverflow__worker': (ptr, ptr), 'art_strike': (None, C.c_char_p), 'coverflow_clean_end': (None,),
           'thumb': (i, C.c_char_p, C.c_char_p, i, i),
           'coverflow__grid_cover': (i, C.c_char_p, C.c_char_p, i, i, i), 'coverflow_album_cover': (i, C.c_char_p, C.c_char_p, i, i),
           'coverflow_grid_cover': (i, C.c_char_p, C.c_char_p, i, i), 'coverflow_folder_cover': (i, C.c_char_p, C.c_char_p, i, i),
           'coverflow_player_cover': (i, C.c_char_p, C.c_char_p, i, i), 'coverflow__marked_empty': (i, C.c_char_p),
           'now_cover': (C.c_ubyte, ptr, ptr), 'hash_bytes': (u, u, ptr, u), 'fnv': (u, u, C.c_char_p)}
    fw.fn('cf_restyle', None)()  # the theme's Cover Flow values, as the page's open copies them
    return SimpleNamespace(**{k.replace('coverflow__', ''): fw.fn(k, v[0], *v[1:]) for k, v in fns.items()})


libc = C.CDLL(None)
libc.malloc.restype, libc.malloc.argtypes = C.c_void_p, [C.c_size_t]
libc.calloc.restype, libc.calloc.argtypes = C.c_void_p, [C.c_size_t, C.c_size_t]
libc.free.argtypes = [C.c_void_p]
libc.qsort.argtypes = [C.c_void_p, C.c_size_t, C.c_size_t, C.c_void_p]
libc.mmap.restype, libc.mmap.argtypes = C.c_void_p, [C.c_void_p, C.c_size_t, C.c_int, C.c_int, C.c_int, C.c_long]


def exact(ctype, n):
    """n zeroed ctypes in a calloc of exactly their size, so the sanitizer sees an overrun; free with libc.free."""
    return (ctype * n).from_address(libc.calloc(n, C.sizeof(ctype)))


class Exact:
    def __init__(self, data):
        self.p = libc.malloc(len(data) or 1)
        C.memmove(self.p, bytes(data), len(data))

    def __enter__(self):
        return self.p

    def __exit__(self, *_):
        libc.free(self.p)


class Split:
    """Strings for a record holding both album and artist: an 8-byte pointer at 0x14 runs into 0x18, so the album goes
    under 4 GB (its high half, the artist's low half, 0) and each artist on a 4 GB boundary (its low half 0, its high
    half one of this file's: conftest.high_halves). Both are in the sanitizer's application memory; mapped once a
    process, never freed."""
    low = at = 0
    artists = {}
    highs = high_halves(0x40)

    @classmethod
    def album(cls, s):
        if not cls.low:
            cls.low = libc.mmap(0, 1 << 20, 3, 0x62, -1, 0)  # MAP_PRIVATE | MAP_ANONYMOUS | MAP_32BIT
            assert cls.low and cls.low < 1 << 32
            cls.at = cls.low
        p, cls.at = cls.at, cls.at + len(s) + 1
        assert cls.at < cls.low + (1 << 20)
        C.memmove(p, s + b'\0', len(s) + 1)
        return p

    @classmethod
    def artist(cls, s):
        if s not in cls.artists:
            p = cls.highs[len(cls.artists)] << 32
            assert libc.mmap(p, 0x1000, 3, 0x100022, -1, 0) == p  # MAP_FIXED_NOREPLACE
            C.memmove(p, s + b'\0', len(s) + 1)
            cls.artists[s] = p
        return cls.artists[s]


class Rec:
    """A stSongInfo as far as a 64-bit host can hold one: an 8-byte pointer at 0x14 overruns 0x18, so album and artist
    share a record only through Split's addresses."""
    def __init__(self, album=None, path=None, artist=None, album_artist=None, id=0):
        self.raw, self.keep = C.create_string_buffer(0x60), []
        struct.pack_into('<i', self.raw, 0, id)
        both = album is not None and artist is not None
        for off, s in ((0x0c, path), (0x14, album), (0x18, artist), (0x24, album_artist)):
            if s is None:
                continue
            s = s if isinstance(s, bytes) else s.encode()
            if both and off in (0x14, 0x18):
                p = Split.album(s) if off == 0x14 else Split.artist(s)
                struct.pack_into('<I' if off == 0x14 else '<Q', self.raw, off, p)  # the album's high half is the artist's
            else:
                self.keep.append(C.create_string_buffer(s))
                struct.pack_into('<Q', self.raw, off, C.addressof(self.keep[-1]))
        self.p = C.addressof(self.raw)


# Home's cubic filter

def test_bspline_weights_sum_to_256_and_are_never_negative(fw):
    g = api(fw)
    for t in range(256):
        w = (C.c_uint * 4)()
        g.bspline_w(t, w)
        assert sum(w) == 256, t
        assert all(x <= 256 for x in w), t  # unsigned: a negative would wrap past this


def test_bspline_weights_are_the_cubic_b_spline(fw):
    g = api(fw)
    for t in range(256):
        f = t / 256
        want = ((1 - f) ** 3 / 6, (3 * f**3 - 6 * f**2 + 4) / 6, (-3 * f**3 + 3 * f**2 + 3 * f + 1) / 6, f**3 / 6)
        w = (C.c_uint * 4)()
        g.bspline_w(t, w)
        for k in range(4):
            assert abs(w[k] - 256 * want[k]) <= 1.5, (t, k)


def test_bspline_at_phase_0_is_a_sixth_four_sixths_a_sixth(fw):
    w = (C.c_uint * 4)()
    api(fw).bspline_w(0, w)
    assert list(w) == [43, 171, 42, 0]


def test_bspline_mirrors_about_half_a_texel(fw):
    g = api(fw)
    for t in range(1, 256):
        a, b = (C.c_uint * 4)(), (C.c_uint * 4)()
        g.bspline_w(t, a)
        g.bspline_w(256 - t, b)
        assert all(abs(a[k] - b[3 - k]) <= 1 for k in range(4)), t


def sharpened(values):
    out = []
    for i, c in enumerate(values):
        prev, nxt = values[max(i - 1, 0)], values[min(i + 1, len(values) - 1)]
        out.append(0xff000000)
        for s in (0, 8, 16):
            v = (6 * (c >> s & 255) - (prev >> s & 255) - (nxt >> s & 255) + 2) >> 2
            out[-1] |= min(255, max(0, v)) << s
    return out


def test_sharpen_is_the_kernel_with_its_ends_their_own_neighbours(fw):
    g, rng = api(fw), random.Random(1)
    for n in (1, 2, 3, 8, 64):
        t = exact(C.c_uint, n)
        t[:] = [rng.getrandbits(32) for _ in range(n)]
        want = sharpened(list(t))
        g.home_sharpen(t, n, 1)
        assert list(t) == want
        libc.free(C.addressof(t))


def test_sharpen_leaves_a_flat_area_alone_and_clamps_an_edge(fw):
    g = api(fw)
    t = (C.c_uint * 4)(0x804020, 0x804020, 0x804020, 0x804020)
    g.home_sharpen(t, 4, 1)
    assert list(t) == [0xff804020] * 4
    t = (C.c_uint * 4)(0, 0, 0xffffff, 0xffffff)
    g.home_sharpen(t, 4, 1)
    assert [x & 255 for x in t] == [0, 0, 255, 255]  # would ring below 0 and past 255


def test_sharpen_walks_a_column_by_its_step(fw):
    g, rng = api(fw), random.Random(2)
    step, n = 5, 7
    data = [rng.getrandbits(32) for _ in range(step * n)]
    t = (C.c_uint * len(data))(*data)
    g.home_sharpen(t, n, step)
    assert list(t[::step]) == sharpened(data[::step])
    for i, v in enumerate(t):
        assert i % step == 0 or v == data[i]


def slide_tex(fw, tw, th, value):
    """home.sl[0].tex at tw x th inside its border, each texel value(memory column, memory row), in an exact allocation."""
    home = fw.var('coverflow__home', Home)
    ts = tw + 3
    tex = exact(C.c_uint, ts * (th + 3))
    for y in range(th + 3):
        for x in range(ts):
            tex[y * ts + x] = value(x, y)
    home.sl[0].tex, home.sl[0].tw, home.sl[0].th = C.addressof(tex), tw, th
    return tex


def render(fw, d, pitch, u, v, k=65536, blend=0, slide=0):
    """home_render of home.sl[slide] at u, v and scale k into d."""
    home = fw.var('coverflow__home', Home)
    api(fw).home_render(d, pitch, C.addressof(home.sl[slide]), u, v, k, blend)


def vigged(fw, c, y):
    """c as the vignette leaves it on the frame's row y (coverflow.c vig_k, vig)."""
    k = fw.fn('coverflow__vig_k', C.c_uint, C.c_int)(y)
    return c if k >= 256 else fw.fn('coverflow__vig', C.c_uint, C.c_uint, C.c_uint)(c, k)


def test_the_vignette_darkens_the_pane_s_top_50_and_bottom_40_screen_rows(fw):
    """V4: 0.25 + 0.75 t^1.6 of a row's brightness, t its distance from the edge over 50 px at the top and 40 at the
    bottom, in 256ths; the rows between untouched; alpha kept, each channel scaled."""
    k = fw.fn('coverflow__vig_k', C.c_uint, C.c_int)
    up = 320 - fw.fn('coverflow__pane_h', C.c_int)()  # the frame's row 0 on the screen
    def screen(y):
        return k(y - up)
    assert [screen(y) for y in (0, 25, 49, 50, 160, 279, 280, 300, 319)] == [64, 127, 250, 256, 256, 256, 248, 122, 64]
    for y in range(50):
        assert screen(y) == round(256 * (0.25 + 0.75 * (y / 50) ** 1.6))
    for d in range(40):
        assert screen(319 - d) == round(256 * (0.25 + 0.75 * (d / 40) ** 1.6))
    vig = fw.fn('coverflow__vig', C.c_uint, C.c_uint, C.c_uint)
    assert vig(0x80ff8040, 64) == 0x803f2010 and vig(0xffffffff, 256) == 0xffffffff


def test_a_flat_cover_renders_flat_at_any_phase(fw):
    g = api(fw)
    tex = slide_tex(fw, 206, TEX_H, lambda x, y: 0xff123456)
    d = exact(C.c_uint, PANE_W * PANE_H)
    for u, v in ((0, 0), (128, 128), (257, 3), (1111, 2222), (18 * 256, 29 * 256)):
        render(fw, d, PANE_W, u, v)
        assert all(d[i] == vigged(fw, 0xff123456, i // PANE_W) for i in range(0, len(d), 7)), (u, v)
    libc.free(C.addressof(d))
    libc.free(C.addressof(tex))


def test_a_ramp_comes_out_at_the_sub_pixel_position(fw):
    """A cubic B-spline reproduces straight lines: the output of a ramp reads the position sampled."""
    g = api(fw)
    tex = slide_tex(fw, 206, PANE_H + 90, lambda x, y: 0xff400000 | x | min(y, 255) << 8)
    d = exact(C.c_uint, PANE_W * PANE_H)
    for u, v in ((0, 0), (64, 0), (128, 128), (1000, 192), (16 * 256 + 37, 0)):
        render(fw, d, PANE_W, u, v)
        for py, px in ((60, 0), (100, 50), (150, PANE_W - 1), (163, 100)):  # rows clear of the vignette
            c = d[py * PANE_W + px]
            assert abs((c & 255) - (u / 256 + px + 1)) <= 1, (u, v, px)
            assert abs((c >> 8 & 255) - (v / 256 + py + 1)) <= 1, (u, v, py)
            assert c >> 16 == 0xff40
    libc.free(C.addressof(d))
    libc.free(C.addressof(tex))


def test_render_at_the_pan_s_far_corner_stays_inside_the_border(fw):
    g = api(fw)
    tw, th = 206, TEX_H
    tex = slide_tex(fw, tw, th, lambda x, y: 0xff000000)
    d = exact(C.c_uint, PANE_W * PANE_H)
    render(fw, d, PANE_W, (tw - PANE_W) * 256, (th - PANE_H) * 256)  # an overrun is the sanitizer's
    assert d[-1] == 0xff000000
    libc.free(C.addressof(d))
    libc.free(C.addressof(tex))


def test_render_honours_the_bitmap_s_pitch(fw):
    g = api(fw)
    tex = slide_tex(fw, 206, TEX_H, lambda x, y: 0xffffffff)
    pitch = PANE_W + 12
    d = exact(C.c_uint, pitch * PANE_H)
    render(fw, d, pitch, 300, 300)
    assert all(d[y * pitch + PANE_W - 1] == vigged(fw, 0xffffffff, y) and d[y * pitch + PANE_W] == 0
               for y in range(0, PANE_H, 7))
    libc.free(C.addressof(d))
    libc.free(C.addressof(tex))


# Home's pan

def fit(fw, w, h):
    a, b = C.c_int(), C.c_int()
    api(fw).home_fit(w, h, PANE_H, r(a), r(b))  # the panel's height, as the caller passes it
    return a.value, b.value


@pytest.mark.parametrize('w, h', ((0, 0), (0, 100), (100, 0), (8193, 100), (100, 8193), (2**31, 2**31)))
def test_home_fit_unknown_sizes_fill_the_panel(fw, w, h):
    assert fit(fw, w, h) == (PANE_W, PANE_H)


def test_home_fit_just_covers_the_panel_at_its_proportions(fw):
    rng = random.Random(3)
    for _ in range(500):
        w, h = rng.randint(1, 8192), rng.randint(1, 8192)
        fw_, fh = fit(fw, w, h)
        assert fw_ >= PANE_W and fh >= PANE_H
        assert fw_ == PANE_W or fh == PANE_H  # one side exact: just covering
        if fw_ == PANE_W:
            assert fh == math.ceil(PANE_W * h / w)
        else:
            assert fw_ == math.ceil(PANE_H * w / h)


@pytest.mark.parametrize('w, h, want', (  # a pane taller than wide (OLED's 179 x 320)
    (PANE_W, PANE_H, (PANE_W, PANE_H)), (2 * PANE_W, 2 * PANE_H, (PANE_W, PANE_H)), (500, 500, (PANE_H, PANE_H)),
    (1000, 100, (10 * PANE_H, PANE_H)), (100, 1000, (PANE_W, 10 * PANE_W)), (8192, 8192, (PANE_H, PANE_H)),
    (8192, 1, (8192 * PANE_H, PANE_H)), (1, 8192, (PANE_W, 8192 * PANE_W))))
def test_home_fit_exact_cases(fw, w, h, want):
    assert fit(fw, w, h) == want


def eased(range_, period, ms):
    """drift_at's smoothstep ping-pong, in 1/256 px."""
    t = ms % (2 * period)
    x = ((t if t < period else 2 * period - t) << 16) // period
    return range_ * 256 * ((x * x * (3 * 65536 - 2 * x)) >> 24) >> 24


@pytest.mark.parametrize('period', (20000, 26000))
def test_drift_is_an_eased_ping_pong_of_its_period(fw, period):
    g = api(fw)
    at = lambda ms: g.drift_at(30, period, ms)
    assert at(0) == 0
    assert at(period) == 30 * 256  # the far end
    assert abs(at(period // 2) - 15 * 256) <= 1  # halfway at half the time
    assert at(2 * period) == 0  # a whole cycle later
    assert at(2 * period + 123) == at(123)
    for ms in range(0, period, 97):
        assert at(ms) == at(2 * period - ms) == eased(30, period, ms), ms
        assert at(ms) <= at(ms + 16)  # rising to the far end, never a step back
    # eased: a 200 ms tick moves under a tenth of the middle's step next to each end, where it turns
    tick = lambda ms: at(ms + 200) - at(ms)
    middle = tick(period // 2 - 100)
    assert tick(0) * 10 < middle and tick(period - 200) * 10 < middle
    assert middle <= 30 * 256 * 200 * 3 // (2 * period) + 2  # at most 1.5 times the average speed


def slide_at(fw, i):
    """home.sl[i]'s window now: u, v in 1/256 texel and its scale in 1/65536."""
    u, v, k = C.c_int(), C.c_int(), C.c_int()
    api(fw).slide_at(C.addressof(fw.var('coverflow__home', Home).sl[i]), r(u), r(v), r(k))
    return u.value, v.value, k.value


def test_drift_range_zero_never_moves(fw):
    g = api(fw)
    assert g.drift_at(0, 20000, 7777) == 0


def test_drift_holds_its_largest_range_in_32_bits(fw):
    g = api(fw)
    assert g.drift_at(99, 26000, 26000) == 99 * 256


def test_the_pan_starts_at_the_old_crop_and_moves_each_axis_by_its_own_period(fw):
    s = fw.var('coverflow__home', Home).sl[0]
    s.u0, s.v0, s.rx, s.ry = 10, 20, 18, 29
    assert slide_at(fw, 0) == (10 * 256, 20 * 256, 65536)  # a texel a pixel
    s.ms = 20000
    assert slide_at(fw, 0) == ((10 + 18) * 256, 20 * 256 + eased(29, 26000, 20000), 65536)  # X's far end


# The shelf's geometry

def pose(g, t):
    p = Pose()
    g.pose(t, r(p))
    return p


def test_the_angle_tables_are_cosine_and_sine_to_60_degrees(fw):
    cos, sin = fw.var('coverflow__cf_cos', C.c_int * 17), fw.var('coverflow__cf_sin', C.c_int * 17)
    for k in range(17):
        a = math.radians(60 * k / 16)
        assert abs(cos[k] - 65536 * math.cos(a)) <= 1 and abs(sin[k] - 65536 * math.sin(a)) <= 1, k


def test_a_side_cover_s_pose_by_its_distance(fw):
    g = api(fw)
    p = pose(g, 0)
    assert (p.xc, p.zc, p.c, p.s, p.bright) == (0, 0, 65536, 0, 256)
    p = pose(g, ONE)
    assert (p.xc, p.zc, p.c, p.s, p.bright) == (16 * 128, 16 * 77, 0, 65536, 192)  # #329's shelf: edge-on
    p = pose(g, 2 * ONE)
    assert (p.xc, p.zc, p.bright) == (16 * (128 + 35), 16 * 77, 128)
    assert pose(g, 3 * ONE).bright == 128  # the third out stays (CF_THIRD)
    # and every one past it, drawn whole for as long as any of it shows (a thumb theme's wide ring)
    assert pose(g, 13 * ONE // 4).bright == pose(g, 7 * ONE // 2).bright == pose(g, 15 * ONE // 2).bright == 128


def test_a_cover_s_pose_is_continuous_and_keeps_turning_in_step(fw):
    g = api(fw)
    last = pose(g, 0)
    for t in range(256, 3 * ONE, 257):
        p = pose(g, t)
        assert 0 <= p.xc - last.xc <= 16 and 0 <= p.zc - last.zc <= 16 and last.bright - 2 <= p.bright <= last.bright, t
        assert abs(math.hypot(p.c, p.s) - 65536) < 90  # the 90 degree table's chords: 5.6 degrees a step
        last = p


def test_project_and_unproject_are_inverse(fw):
    g = api(fw)
    for t in (0, 1000, ONE // 2, ONE, 3 * ONE // 2):
        p = pose(g, t)
        for u in range(-1260, 1261, 40):
            dx = g.project(r(p), u)
            got, h = C.c_int(), C.c_int()
            assert g.unproject(r(p), dx, r(got), r(h)), (t, u)
            assert abs(got.value - u) <= 16, (t, u)
            assert h.value > 0


def test_unproject_misses_past_the_cover_s_edge(fw):
    g = api(fw)
    p = pose(g, 0)
    u, h = C.c_int(), C.c_int()
    assert g.unproject(r(p), 1279, r(u), r(h)) and u.value == 1279 and h.value == ART * 256
    assert not g.unproject(r(p), 1280, r(u), r(h))
    assert g.unproject(r(p), -1280, r(u), r(h))
    assert not g.unproject(r(p), -1281, r(u), r(h))


def test_the_ring_slot_nearest_first_and_the_side_being_left_farther(fw):
    g = api(fw)
    assert g.slot(0, 0, 0) == 3
    assert (g.slot(100, 1, 0), g.slot(100, 1, 1)) == (4, 2)  # moving right: the right cover is nearer
    assert (g.slot(-100, 1, 0), g.slot(-100, 1, 1)) == (2, 4)


class Ring:
    """The seven covers round the centre, ring[j] the cover j - 3, in a thumb theme's wide ring of
    fifteen (its shelf draws seven a side), the ends left out: .at is what coverflow_render takes."""
    def __init__(self, *covers):
        self.wide = (C.c_void_p * 15)(*([None] * 4 + list(covers)))
        self.at = C.c_void_p(C.addressof(self.wide) + 4 * C.sizeof(C.c_void_p))

    def __setitem__(self, j, cover):
        self.wide[j + 4] = cover


def corner_inset(fw):
    """coverflow_render fills cf_inset once."""
    g = api(fw)
    d = exact(C.c_uint, VIEW_W * VIEW_H)
    ring = Ring()  # kept alive: coverflow_render reads its array
    g.coverflow_render(d, VIEW_W, 0, ring.at)
    libc.free(C.addressof(d))
    return g


def test_hit_the_centre_cover_s_own_box(fw):
    g = api(fw)
    assert g.coverflow_hit(0, CX, 93) == 0
    assert g.coverflow_hit(0, CX - 80, 93) == 0 and g.coverflow_hit(0, CX + 79, 93) == 0
    assert g.coverflow_hit(0, CX, TOP) == 0 and g.coverflow_hit(0, CX, TOP + ART - 1) == 0
    assert g.coverflow_hit(0, CX, TOP - 1) == MISS
    assert g.coverflow_hit(0, CX, TOP + ART) == MISS  # the reflection is no target


def test_hit_a_rounded_corner_is_left_out(fw):
    g = corner_inset(fw)
    assert g.coverflow_hit(0, CX - 80, TOP) == MISS and g.coverflow_hit(0, CX + 79, TOP + ART - 1) == MISS
    assert g.coverflow_hit(0, CX - 70, TOP) == 0  # past the 11 px corner
    assert g.coverflow_hit(0, CX - 80, TOP + 20) == 0


def test_hit_across_the_shelf_at_rest(fw):
    g = api(fw)
    got = [g.coverflow_hit(0, x, 93) for x in range(VIEW_W)]
    hits = [h for h in got if h != MISS]
    assert sorted(set(hits)) == [-3, -2, -1, 0, 1, 2, 3]  # the third out stays, at CF_BRIGHT2
    assert hits == sorted(hits)
    assert got[0] == -3 and got[-1] == 3  # the third out runs off the frame
    assert got[CX - 81] == -1 and got[CX + 80] == 1


def test_hit_mirrors_about_the_centre_line(fw):
    g = api(fw)
    for x in range(0, 2 * CX, 3):
        for y in (40, 93, 150):
            a, b = g.coverflow_hit(0, x, y), g.coverflow_hit(0, 2 * CX - 1 - x, y)
            assert a == (b if b == MISS else -b), (x, y)


def texture(j):
    """A flat cover whose green over red says which: brightness scales both."""
    t = exact(C.c_uint, ART * ART)
    t[:] = [0xff0000f0 | (30 + 30 * j) << 8] * (ART * ART)
    return t


def bgra(p):
    """A texel (RGBA8888, red low) as the frame holds it: the screen's BGRA8888, red at bit 16."""
    return p & 0xff00ff00 | (p & 255) << 16 | p >> 16 & 255


def test_hit_names_the_cover_drawn_there(fw):
    g = api(fw)
    ring = [texture(j) for j in range(7)]
    d = exact(C.c_uint, VIEW_W * VIEW_H)
    wide = Ring(*[C.addressof(t) for t in ring])
    pointers = wide.at
    checked = 0
    for frac in (0, 8000, 16000, 32767, -8000, -32768):
        g.coverflow_render(d, VIEW_W, frac, pointers)

        def cover(x, y):
            c = d[y * VIEW_W + x]
            red, green = c >> 16 & 255, c >> 8 & 255  # BGRA8888
            if red < 8:
                return None
            return round(((green / red * 240) - 30) / 30)
        for y in (26, 45, 70, 90):  # above the horizon: no reflection reaches here
            for x in range(2, VIEW_W - 2):
                if len({cover(x + k, y) for k in (-2, -1, 0, 1, 2)} | {cover(x, y - 1), cover(x, y + 1)}) > 1:
                    continue  # an edge's blend
                j = cover(x, y)
                got = g.coverflow_hit(frac, x, y)
                if j is None:  # nothing drawn: no cover, or one past the seven this ring holds
                    assert got == MISS or abs(got) > 3, (frac, x, y)
                elif abs((j - 3) * ONE - frac) < 13 * ONE // 4:  # at least a quarter bright
                    assert got == j - 3, (frac, x, y)
                    checked += 1
    assert checked > 2000
    for t in (*ring, d):
        libc.free(C.addressof(t))


def test_the_centre_cover_comes_out_texel_for_texel(fw):
    g = api(fw)
    tex = exact(C.c_uint, ART * ART)
    tex[:] = [0xff000000 | x | y << 8 | 0x55 << 16 for y in range(ART) for x in range(ART)]
    d = exact(C.c_uint, VIEW_W * VIEW_H)
    ring = Ring()
    ring[3] = C.addressof(tex)
    g.coverflow_render(d, VIEW_W, 0, ring.at)
    for y in range(12, ART - 12):
        for x in range(ART):
            assert d[(TOP + y) * VIEW_W + CX - 80 + x] == bgra(tex[y * ART + x]), (x, y)
    for t in (tex, d):
        libc.free(C.addressof(t))


@pytest.mark.parametrize('top', (None, 90))
def test_the_reflection_mirrors_the_cover_and_fades_out_in_46_px(fw, style, top):
    """OLED's reflection starts at 0 of the cover's brightness (reflect_top 0), so it is the page's black and none
    shows; a theme starting it at 90 of 256 mirrors the cover, fading out over its 46 px."""
    g = api(fw)
    reflect_top = C.c_short.from_address(C.addressof(fw.var('cfs', C.c_char)) + style['reflect_top'])
    assert reflect_top.value == 0  # OLED's
    if top is not None:
        reflect_top.value = top
    top = reflect_top.value
    tex = exact(C.c_uint, ART * ART)
    tex[:] = [0xff000000 | (y + 60) for y in range(ART) for x in range(ART)]
    d = exact(C.c_uint, VIEW_W * VIEW_H)
    ring = Ring()
    ring[3] = C.addressof(tex)
    g.coverflow_render(d, VIEW_W, 0, ring.at)
    bottom = TOP + ART
    for k in range(46):
        red = d[(bottom + k) * VIEW_W + CX] >> 16 & 255  # BGRA8888
        mirrored = ART - 1 - k + 60
        assert abs(red - mirrored * top / 256 * (1 - (k + .5) / 46)) <= 2, k
    assert all(d[y * VIEW_W + CX] == 0xff000000 for y in range(bottom + 46, VIEW_H))
    for t in (tex, d):
        libc.free(C.addressof(t))


def test_every_pixel_of_a_frame_is_opaque_and_stays_in_the_frame(fw):
    g = api(fw)
    ring = [texture(j) for j in range(7)]
    d = exact(C.c_uint, VIEW_W * VIEW_H)
    wide = Ring(*[C.addressof(t) for t in ring])
    pointers = wide.at
    for frac in range(-32768, 32768, 7001):
        g.coverflow_render(d, VIEW_W, frac, pointers)
        assert all(x >> 24 == 255 for x in d)
    for t in (*ring, d):
        libc.free(C.addressof(t))


def test_covers_mirror_when_the_frame_does(fw):
    g = api(fw)
    tex = exact(C.c_uint, ART * ART)
    tex[:] = [0xff000000 | (x + 40) | (y + 40) << 8 for y in range(ART) for x in range(ART)]
    flip = exact(C.c_uint, ART * ART)
    flip[:] = [0xff000000 | (ART - 1 - x + 40) | (y + 40) << 8 for y in range(ART) for x in range(ART)]
    a, b = exact(C.c_uint, VIEW_W * VIEW_H), exact(C.c_uint, VIEW_W * VIEW_H)
    ring = Ring()
    ring[2] = C.addressof(tex)
    g.coverflow_render(a, VIEW_W, 0, ring.at)
    ring[2], ring[4] = None, C.addressof(flip)
    g.coverflow_render(b, VIEW_W, 0, ring.at)
    for y in range(0, VIEW_H, 11):
        for x in range(0, 2 * CX):
            assert a[y * VIEW_W + x] == b[y * VIEW_W + 2 * CX - 1 - x], (x, y)
    for t in (tex, flip, a, b):
        libc.free(C.addressof(t))


# A decoded image into a texture

def bitmap(fw, w, h, pixels, format=1, stride=None, loads=True):
    """widget_load_image: a bitmap_t of w, h and format, pixels the bytes its lock gives."""
    def load(owner, url, b, *_):
        if not loads:
            return 1
        C.c_uint.from_address(b).value, C.c_uint.from_address(b + 4).value = w, h
        C.c_ushort.from_address(b + 14).value = format
        return 0
    fw.mock('widget_load_image', load)
    fw.mock('bitmap_lock_buffer_for_read', lambda *_: C.addressof(pixels))
    fw.mock('bitmap_get_line_length', lambda *_: stride or 4 * w)


def rgba(w, h, colour):
    data = bytes(b for y in range(h) for x in range(w) for b in colour(x, y))
    return (C.c_ubyte * len(data)).from_buffer_copy(data)


def decoded(fw, unload=1):
    out = exact(C.c_uint, ART * ART)
    ok = fw.fn('cf_decode', C.c_int, C.c_char_p, C.c_void_p, C.c_int)(b'x', out, unload)
    return ok, out


def test_decode_a_square_cover_is_copied(fw):
    pixels = rgba(ART, ART, lambda x, y: (x, y, 7, 255))
    bitmap(fw, ART, ART, pixels)
    ok, out = decoded(fw)
    assert ok == 1 and all(out[y * ART + x] == 0xff070000 | y << 8 | x for y in range(ART) for x in range(ART))
    assert fw.calls('widget_unload_image')


def test_decode_a_wide_cover_sits_between_black_bars(fw):
    pixels = rgba(320, 160, lambda x, y: (200, 0, 0, 255))
    bitmap(fw, 320, 160, pixels)
    _, out = decoded(fw)
    column = [out[y * ART + 80] for y in range(ART)]
    assert column == [0xff000000] * 40 + [0xff0000c8] * 80 + [0xff000000] * 40


def test_decode_a_tall_cover_too(fw):
    pixels = rgba(80, 160, lambda x, y: (0, 200, 0, 255))
    bitmap(fw, 80, 160, pixels)
    _, out = decoded(fw)
    assert list(out[80 * ART:81 * ART]) == [0xff000000] * 40 + [0xff00c800] * 80 + [0xff000000] * 40


def test_decode_scales_by_the_nearest_texel_from_a_larger_one(fw):
    pixels = rgba(320, 320, lambda x, y: (x // 2, y // 2, 0, 255))
    bitmap(fw, 320, 320, pixels)
    _, out = decoded(fw)
    assert all(out[y * ART + x] == 0xff000000 | y << 8 | x for y in (0, 80, 159) for x in (0, 80, 159))


def test_decode_blends_a_transparent_pixel_over_the_black_page(fw):
    pixels = rgba(ART, ART, lambda x, y: (200, 100, 50, 128))
    bitmap(fw, ART, ART, pixels)
    _, out = decoded(fw)
    assert out[0] == 0xff000000 | 200 * 128 // 255 | (100 * 128 // 255) << 8 | (50 * 128 // 255) << 16


@pytest.mark.parametrize('format, order', ((1, (0, 1, 2, 3)), (2, (3, 2, 1, 0)), (3, (2, 1, 0, 3)), (4, (1, 2, 3, 0))))
def test_decode_reads_each_32_bit_format(fw, format, order):
    r_, g_, b_ = 10, 20, 30
    colour = [0] * 4
    colour[order[0]], colour[order[1]], colour[order[2]], colour[order[3]] = r_, g_, b_, 255
    pixels = rgba(ART, ART, lambda x, y: colour)
    bitmap(fw, ART, ART, pixels, format)
    assert decoded(fw)[1][0] == 0xff000000 | r_ | g_ << 8 | b_ << 16


def test_decode_honours_the_stride(fw):
    data = bytearray((ART * 4 + 8) * ART)
    for y in range(ART):
        data[y * (ART * 4 + 8):y * (ART * 4 + 8) + ART * 4] = bytes((1, y, 3, 255)) * ART
    pixels = (C.c_ubyte * len(data)).from_buffer(data)
    bitmap(fw, ART, ART, pixels, stride=ART * 4 + 8)
    assert decoded(fw)[1][159 * ART] == 0xff030001 | 159 << 8


@pytest.mark.parametrize('w, h, format', ((0, 160, 1), (160, 0, 1), (4097, 160, 1), (160, 4097, 1), (160, 160, 0), (160, 160, 5)))
def test_decode_refuses_what_it_can_t_read(fw, w, h, format):
    pixels = (C.c_ubyte * 4)()
    bitmap(fw, w, h, pixels, format)
    assert decoded(fw)[0] == 0 and fw.calls('widget_unload_image')


def test_decode_of_no_image_or_a_kept_one(fw):
    pixels = rgba(ART, ART, lambda x, y: (1, 2, 3, 255))
    bitmap(fw, ART, ART, pixels, loads=False)
    assert decoded(fw)[0] == 0
    bitmap(fw, ART, ART, pixels)
    assert decoded(fw, unload=0)[0] == 1 and not fw.calls('widget_unload_image')  # a theme image: the manager keeps it


def tex_rig(fw, w, h, pixels, **kw):
    bitmap(fw, w, h, pixels, **kw)
    frame = C.create_string_buffer(0x40)
    fw.mock('bitmap_create_ex', lambda *_: C.addressof(frame))
    home = fw.var('coverflow__home', Home)
    load = fw.fn('coverflow__slide_load', C.c_int, C.c_void_p, C.c_char_p, C.c_int, C.c_uint)
    return lambda url, lib=0, ms=0: load(C.addressof(home.sl[0]), url, lib, ms), home, frame


def test_slide_load_zooms_a_cover_by_a_tenth_with_its_pan_s_margin(fw):
    pixels = rgba(300, 300, lambda x, y: (10, 20, 30, 255))
    tex, home, frame = tex_rig(fw, 300, 300, pixels)
    assert tex(b'x') == 1
    s = home.sl[0]
    # the fit to OLED's PANE_W x PANE_H pane, PANE_H square, plus 10%; the pan's ends keep split_inset's 2% of it
    # (7 texels) clear of each edge (drift_axis)
    fit, t, ins = PANE_H, PANE_H * 11 // 10, PANE_H * 11 // 10 * 2 // 100
    assert (s.tw, s.th, s.lib) == (t, t, 0)
    assert (s.u0, s.rx) == ((fit - PANE_W) // 2, t - fit)  # the old crop, the whole zoom's room
    assert (s.v0, s.ry) == (ins, t - PANE_H - ins - ins)  # no crop: the inset at each end
    assert struct.unpack_from('<H', frame, 0xc)[0] & 1  # opaque


def split_inset_set(fw, library, inset):
    """The geometry set's values as the C reads them, its split_inset inset (OLED's 2), and its client."""
    names = host.tools_theme.GEOM_VALS
    row = dict(zip(names, (C.c_short * len(names)).in_dll(fw.lib, 'theme_engine__geom_vals')))
    assert row['split_inset'] == inset
    return row, 320 - row['bar']


def test_split_raises_home_s_list_to_y_40_where_the_art_runs_under_the_status_row(fw, library):
    """Under OLED's set (split_art_up) Split's main menu starts at y 40, its top rows under the status row's overlay
    (Home's window is the whole screen, docs/oled-design.md#full-bleed); Full's and the other lists stay at the theme's
    home.rows.top under the row's foot."""
    ui = Ui(fw)
    lst = ui.make(0, 'list_view', 0, 0, 375, 224)
    fw.var('coverflow__home', Home).list = lst  # the main menu; its Settings and other lists are full pages here
    width = fw.fn('coverflow__home_width', None, C.c_void_p, C.c_int, C.c_int, C.c_int, C.c_int, C.c_int)
    row, client = split_inset_set(fw, library, 2)  # OLED's
    width(lst, 196, 196, 24, 44, 0)
    assert rect(lst)[:2] == (0, 40) and 320 - client == 58
    fw.fn('ipod_home', C.c_int)()  # the settings read once
    home_mode = C.c_int.from_address(C.addressof(fw.var('st', C.c_char)) + host.offsets(library.parent/'navigation.o', 'scratch_t')['home'])
    home_mode.value = 1  # Full
    width(lst, 375, 375, 24, 44, 0)
    full_top = rect(lst)[1]
    home_mode.value = 0
    assert full_top >= 58  # the theme's home.rows.top, under the status row's foot
    other = ui.make(0, 'list_view', 0, 0, 375, 64)
    width(other, 375, 375, 24, 44, 0)
    assert rect(other)[1] == full_top


def full_bleed_home(fw, library):
    """Home in Split under OLED's set as FULL_BLEED lays it out: its window the whole screen, the main menu from y 40
    and the art pane from the top, under the status row's overlay (docs/oled-design.md#full-bleed)."""
    ui = Ui(fw)
    split_inset_set(fw, library, 2)
    home = fw.var('coverflow__home', Home)
    win = ui.make(ui.wm, 'window', 0, 0, 375, 320)
    home.win, home.list = win, ui.make(win, 'list_view', 0, 40, 196, 280)
    ui.make(home.list, 'scroll_view', 0, 0, 196, 280)
    home.art = ui.make(win, 'image', 196, 0, 179, 320)
    bar = ui.make(ui.wm, 'system_bar', 0, 0, 375, 58)
    fw.var('fw_ctx_system_bar', C.c_void_p).value = bar
    return ui, home, win, bar


@pytest.mark.parametrize('toast', (False, True))
def test_a_toast_over_home_leaves_the_rows_under_the_status_row_home_s(fw, library, toast):
    """OLED's toast (msginfo_dialog) over Home in Split: the bar lays no flat ground over the rows under it, which
    Home's own paint holds, as with nothing over Home (user, 2026-11-16: "Unplugged" and toasts like it ran a black bar
    across the top, the first row cut, as the bar painted for the toast as if it were the page). A payload page is
    full-bleed (coverflow.c page_open) and gets none either: its own ground runs under the bar."""
    ui, home, win, bar = full_bleed_home(fw, library)
    ui.top = ui.make(ui.wm, 'dialog', 0, 0, 375, 320, name='msginfo_dialog') if toast else win
    fw.mock('canvas_get_clip_rect', lambda c, r, *_: (C.c_int * 4).from_address(r).__setitem__(slice(0, 4), [0, 0, 375, 320]))
    fw.mock('canvas_set_clip_rect', lambda *_: None)
    paint_bg = fw.fn('ringnav_paint_bg', C.c_int, C.c_void_p, C.c_void_p)
    canvas = Canvas(fw)
    paint_bg(bar, canvas.p)
    assert not [r for r in canvas.rects if r[1] < 196 and r[2] < 58]  # nothing over the list's columns in the band
    ui.top = ui.make(ui.wm, 'window', 0, 0, 375, 320, name='queue_page')
    canvas.rects.clear()
    paint_bg(bar, canvas.p)
    assert not [r for r in canvas.rects if r[2] < 58]


def test_a_slide_over_home_leaves_its_band_to_home_s_snapshot(fw, library):
    """A payload page sliding over Home in Split (a push, or a Return back to Home): stock paints the bar before each
    frame's percent, so page_parallax.c's draw of the new page paints it again. Home's band, its rows under the status
    row, is in Home's own snapshot, drifted and darkened with it, and the sliding page's ground is its own, full-bleed,
    so the bar lays no flat ground; the title shows only over the sliding page, faded as far as it is out (user,
    2026-11-17: Control Center > hold Bluetooth > Return, or Library > Return, ran a flat band across the top for the
    whole slide, Home's raised top row cut, the closing page's title over it)."""
    ui, home, win, bar = full_bleed_home(fw, library)
    title = ui.make(bar, 'hscroll_label', 64, 0, 120, 58, name='label_title')
    ui.top = ui.make(ui.wm, 'window', 0, 0, 375, 320, name='queue_page')  # the sliding page, full-bleed
    ui.animating = 1
    canvas = Canvas(fw)
    clips, painted, alphas = [], [], []
    fw.mock('canvas_get_clip_rect', lambda c, r, *_: (C.c_int * 4).from_address(r).__setitem__(slice(0, 4), [0, 0, 375, 320]))
    fw.mock('canvas_set_clip_rect', lambda c, r, *_: clips.append(tuple((C.c_int * 4).from_address(r))))
    fw.mock('canvas_set_global_alpha', lambda c, a, *_: alphas.append(a))
    paint_bg = fw.fn('ringnav_paint_bg', C.c_int, C.c_void_p, C.c_void_p)
    paint_border = fw.fn('ringnav_paint', C.c_int, C.c_void_p, C.c_void_p)

    def paint(w, c, *_):  # stock's widget_paint of the bar: its background, then the title's
        painted.append(w)
        if w == bar:
            paint_bg(bar, c)
            paint_bg(title, c)
            paint_border(title, c)
    fw.mock('widget_paint', paint)
    # stock's own paint of the bar, before the frame's percent: no ground of the bar's own
    paint_bg(bar, canvas.p)
    assert not [r for r in canvas.rects if r[2] < 58]
    wa = C.create_string_buffer(0x100)
    struct.pack_into('<QQ', wa, 0x14, win, ui.top)  # prev_win, curr_win
    struct.pack_into('<Q', wa, 0xb8, canvas.p)
    struct.pack_into('<f', wa, 0xb4, 0.5)  # the new page half in
    fw.mock('htranslate_draw_prev', lambda *_: 0)
    fw.mock('htranslate_draw_curr', lambda *_: 0)
    clips.clear(), canvas.rects.clear()
    assert fw.fn('parallax_prev', C.c_int, C.c_void_p)(C.addressof(wa)) == 0
    assert fw.fn('parallax_curr', C.c_int, C.c_void_p)(C.addressof(wa)) == 0  # the bar over both pages
    open_ = 375 - round(375 * 0.5)  # the page covers from 187
    assert painted[:1] == [bar] and home.list not in painted  # the band is Home's snapshot's, not the bar's
    assert (0x20000000, 0, 0, open_, 320) in canvas.rects  # Home's snapshot darkened, its band with it
    assert not [r for r in canvas.rects if r[0] != 0x20000000 and r[2] < 58]  # no flat ground: each page's own
    assert (open_, 0, 375 - open_, 320) in clips and alphas == [128, 255]  # the title over the page alone, faded
    ui.animating = 0


def test_a_split_inset_keeps_the_pan_off_the_cover_s_edges(fw, library):
    row, client = split_inset_set(fw, library, 2)  # OLED's
    pw, ph = 375 - row['split_art'], client + row['split_art_up']
    pixels = rgba(300, 300, lambda x, y: (10, 20, 30, 255))
    tex, home, _ = tex_rig(fw, 300, 300, pixels)
    assert tex(b'x') == 1
    t = ph * 110 // 100  # a square cover fits the pane's height
    ins = t * 2 // 100
    s = home.sl[0]
    assert s.tw == t
    assert (s.v0, s.v0 + s.ry) == (ins, t - ph - ins)  # inside the inset at both ends
    assert (s.u0, s.u0 + s.rx) == ((ph - pw) // 2, (ph - pw) // 2 + t - ph)  # the zoom's margin, far from both edges
    assert s.rx > 0 and s.ry > 0  # it pans both ways


def test_slide_load_a_flat_cover_stays_flat_out_to_its_border(fw):
    pixels = rgba(300, 300, lambda x, y: (10, 20, 30, 255))
    tex, home, _ = tex_rig(fw, 300, 300, pixels)
    tex(b'x')
    ts = home.sl[0].tw + 3
    data = (C.c_uint * (ts * (319 + 3))).from_address(home.sl[0].tex)
    assert all(v == 0xff1e140a for v in data)


def test_slide_load_blends_over_the_black_page(fw):
    pixels = rgba(300, 300, lambda x, y: (200, 200, 200, 128))
    tex, home, _ = tex_rig(fw, 300, 300, pixels)
    tex(b'x')
    want = 200 * 128 * 255 // (255 * 255)
    assert (C.c_uint * 8).from_address(home.sl[0].tex)[5] & 255 == want


def test_slide_load_a_load_that_fails_says_so(fw):
    pixels = (C.c_ubyte * 4)()
    tex, _, _ = tex_rig(fw, 300, 300, pixels, loads=False)
    assert tex(b'x') == -1


@pytest.mark.parametrize('w, h, format', ((8192, 1, 1), (1, 8192, 1), (300, 300, 5), (300, 300, 0), (8193, 300, 1), (0, 300, 1)))
def test_slide_load_a_cover_it_won_t_pan_stays_still(fw, w, h, format):
    pixels = (C.c_ubyte * 4)()
    tex, home, _ = tex_rig(fw, w, h, pixels, format=format)
    assert tex(b'x') == 0 and home.sl[0].tw == 0 and not home.sl[0].tex and fw.calls('widget_unload_image')


# The status row over Split's art: the clock and every glyph in one ink, white or black by WCAG contrast over the mean
# of the art under them all

GAP = 4  # INK_GAP: empty columns that part two glyphs
BLACK, WHITE = 0xff000000, 0xffffffff


def lum(r, g, b):
    lin = [c / 255 / 12.92 if c / 255 <= 0.04045 else ((c / 255 + 0.055) / 1.055) ** 2.4 for c in (r, g, b)]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def wcag_white(r, g, b):
    """Whether white has at least black's WCAG contrast over the sRGB pixel (Scrolly's lampLineInk rule)."""
    L = lum(r, g, b)
    return 1.05 / (L + 0.05) >= (L + 0.05) / 0.05, abs((L + 0.05) ** 2 - 0.0525)


def ink(fw, screen, art, ground, held=None, h=1, cut=0):
    """home_ink over h rows: screen (BGRA words, the bar's glyphs on ground) and art (RGBA words), row-major."""
    n = len(screen)
    w = n // h
    fb, tex = (C.c_uint * n)(*screen), (C.c_uint * n)(*art)
    hold = (C.c_ubyte * w)(*(held or [0] * w))
    fw.fn('home_ink', None, C.c_void_p, C.c_int, C.c_void_p, C.c_int, C.c_int, C.c_int, C.c_uint, C.c_void_p, C.c_int)(
        fb, w, tex, w, w, h, ground, hold, cut)
    return list(fb), list(hold)


def art_px(r, g, b):
    return 0xff000000 | b << 16 | g << 8 | r


def screen_px(r, g, b):
    return 0xff000000 | r << 16 | g << 8 | b


def apart(cells, ground):
    """One-pixel glyphs, GAP columns of ground between them: the screen row and each glyph's column."""
    row, at = [], []
    for c in cells:
        at.append(len(row))
        row += [c] + [ground] * GAP
    return row, at


@pytest.mark.parametrize('ground, glyph', ((BLACK, WHITE), (WHITE, BLACK)))  # dark, Light Mode
def test_home_ink_shows_the_art_where_no_glyph_is_and_inks_a_glyph_by_contrast(fw, ground, glyph):
    screen = [glyph, ground]
    for tone, want in (((250, 250, 250), (0, 0, 0)), ((20, 24, 30), (255, 255, 255)), ((120, 190, 220), (0, 0, 0))):
        got, _ = ink(fw, screen, [art_px(*tone), art_px(120, 190, 220)], ground)  # white: black; dark: white; light blue: black
        assert got == [screen_px(*want), screen_px(120, 190, 220)]  # the art, in the screen's order, where no glyph is


def test_home_ink_gives_the_whole_row_one_ink(fw):
    """iOS's one style for the bar (the device's dark bars beside a light clock, 2026-11-16): a clock over dark art,
    the bars over light grey art and a battery over dark grey art come out in one ink, the one for their mean, and
    the row's ink is held in every column. Inked a glyph at a time, the light grey art's glyph would have been
    black."""
    dark, light, mid = art_px(10, 10, 10), art_px(150, 150, 150), art_px(40, 40, 40)
    screen, at = apart([WHITE] * 3, BLACK)
    art = [mid] * len(screen)
    art[at[0]], art[at[1]] = dark, light
    got, held = ink(fw, screen, art, BLACK)
    assert [got[i] for i in at] == [WHITE] * 3 and held == [1] * len(screen)
    assert ink(fw, [WHITE], [light], BLACK)[0] == [BLACK]  # alone, the light grey art's glyph is black
    art[at[0]] = art[at[2]] = light  # all light grey: all black
    got, held = ink(fw, screen, art, BLACK)
    assert [got[i] for i in at] == [BLACK] * 3 and held == [2] * len(screen)


def test_home_ink_blends_an_edge_by_its_coverage(fw):
    half = screen_px(128, 128, 128)  # a glyph's edge, half on the black ground
    got, _ = ink(fw, [half], [art_px(255, 255, 255)], BLACK)
    assert abs((got[0] & 255) - 127) <= 1  # white art: half black
    got, _ = ink(fw, [half], [art_px(0, 0, 0)], BLACK)
    assert abs((got[0] & 255) - 128) <= 1  # black art: half white
    dim = screen_px(0x6e, 0x6e, 0x6e)  # the slim battery's outline: its tone is its share
    assert abs((ink(fw, [dim], [art_px(255, 255, 255)], BLACK)[0][0] & 255) - (255 - 0x6e)) <= 1


def test_home_ink_picks_as_wcag_does(fw):
    rnd = random.Random(7)
    pixels = [(v, v, v) for v in range(256)] + [tuple(rnd.randrange(256) for _ in range(3)) for _ in range(1500)]
    for p in pixels:
        got, held = ink(fw, [WHITE], [art_px(*p)], BLACK)
        white, margin = wcag_white(*p)
        if margin > 1e-5:  # a tie within the table's rounding either way
            assert got == [WHITE if white else BLACK] and held == [1 if white else 2], p


def test_home_ink_gives_a_glyph_on_speckled_art_one_ink(fw):
    """Lettering under the battery: art alternating light and dark under one solid glyph. Each pixel picked on its
    own would speckle; the glyph takes the ink for its mean, white here (more dark than light)."""
    w, h = 12, 6
    dark, light = (10, 10, 10), (240, 240, 240)
    art = [art_px(*(light if (x * 7 + y * 3) % 8 == 0 else dark)) for y in range(h) for x in range(w)]
    got, held = ink(fw, [WHITE] * (w * h), art, BLACK, h=h)
    assert set(got) == {WHITE} and set(held) == {1}
    art = [art_px(*(dark if (x * 7 + y * 3) % 8 == 0 else light)) for y in range(h) for x in range(w)]
    got, _ = ink(fw, [WHITE] * (w * h), art, BLACK, h=h)
    assert set(got) == {BLACK}


def test_home_ink_weighs_the_art_by_the_glyph_s_coverage(fw):
    """A glyph whose solid part sits on dark art and whose faint edge sits on white: white, though most of its box
    is light."""
    faint = screen_px(8, 8, 8)
    screen = [WHITE, faint, faint, faint, faint]
    art = [art_px(0, 0, 0)] + [art_px(255, 255, 255)] * 4
    got, _ = ink(fw, screen, art, BLACK)
    assert got[0] == WHITE


def test_home_ink_finds_a_status_colour_in_its_own_glyph(fw):
    """GAP empty columns part two glyphs, each with its own status colour: the charging green keeps its hue and
    the grey glyph beside it takes the row's ink."""
    green = screen_px(0x34, 0xc7, 0x59)
    light = art_px(255, 255, 255)
    got, _ = ink(fw, [WHITE] + [BLACK] * GAP + [green], [light] * (GAP + 2), BLACK)
    assert (got[0], got[-1]) == (BLACK, green)


def grey(L):
    """The sRGB grey whose relative luminance is nearest L."""
    return min(range(256), key=lambda v: abs(lum(v, v, v) - L))


@pytest.mark.parametrize('L, was, want', (
    (0.19, 0, BLACK), (0.19, 1, WHITE), (0.215, 1, BLACK),    # past the bound; held white within 0.03; past it
    (0.17, 0, WHITE), (0.17, 2, BLACK), (0.145, 2, WHITE)))   # and the other way
def test_home_ink_keeps_the_row_s_ink_near_the_bound(fw, L, was, want):
    v = grey(L)
    got, held = ink(fw, [WHITE, BLACK, WHITE], [art_px(v, v, v)] * 3, BLACK, held=[0, 0, was])
    assert (got[0], got[2]) == (want, want) and held == [1 if want == WHITE else 2] * 3


@pytest.mark.parametrize('cut', (1, 2, 4, 7))
def test_home_ink_keeps_the_row_s_ink_where_its_clip_cuts_it(fw, cut):
    """Part of the row repainted (the play state's frames, a battery's level) on light art the whole row was white
    over: INK_CUT_LEFT 1, RIGHT 2 or ROWS 4 keep the held white; uncut, the row is picked again. Nothing held, a cut
    part picks its own."""
    light = art_px(240, 240, 240)
    screen = [WHITE] + [BLACK] * GAP + [WHITE]
    got, held = ink(fw, screen, [light] * len(screen), BLACK, held=[1] * len(screen), cut=cut)
    assert (got[0], got[-1]) == (WHITE, WHITE) and held == [1] * len(screen)
    got, held = ink(fw, screen, [light] * len(screen), BLACK, held=[1] * len(screen))
    assert (got[0], got[-1]) == (BLACK, BLACK) and held == [2] * len(screen)
    got, _ = ink(fw, screen, [light] * len(screen), BLACK, cut=cut)
    assert (got[0], got[-1]) == (BLACK, BLACK)


@pytest.mark.parametrize('ground', (BLACK, WHITE))
def test_home_ink_leaves_a_battery_s_status_colour_its_own(fw, ground):
    """Charging green over the art, at full and half cover; the dim outline beside it still inked
    (INK_KEEP_STATUS_COLOURS 1: with 0 the green is inked as the rest)."""
    green = (0x34, 0xc7, 0x59)
    gr = ground & 255
    half = screen_px(*((c + gr) // 2 for c in green))
    dim = screen_px(*([0x6e if ground == BLACK else 0x91] * 3))
    art = art_px(255, 255, 255)
    got, _ = ink(fw, [dim, screen_px(*green), half], [art] * 3, ground)
    assert got[1] == screen_px(*green)
    mid = [(got[2] >> s & 255) for s in (16, 8, 0)]
    assert all(abs(m - (g + 255) // 2) <= 3 for m, g in zip(mid, green))  # half green over the white art
    assert got[0] & 255 < 0xc0  # the outline: black ink over white art, at its share


# Touch: a fling's speed

def touches(fw, *samples):
    g = api(fw)
    for x, t in samples:
        g.sample(x, t)
    return g


def test_velocity_with_no_samples_or_one(fw):
    g = api(fw)
    assert g.velocity(0) == 0
    g.sample(10, 100)
    assert g.velocity(100) == 0  # no time passed


def test_velocity_is_px_a_second(fw):
    assert touches(fw, (0, 0), (10, 10), (20, 20)).velocity(20) == 1000


def test_velocity_is_signed(fw):
    assert touches(fw, (100, 0), (80, 10)).velocity(10) == -2000


def test_velocity_resting_60_ms_is_none(fw):
    g = touches(fw, (0, 0), (10, 10))
    assert g.velocity(70) == 1000
    assert g.velocity(71) == 0


def test_velocity_looks_back_100_ms_only(fw):
    g = touches(fw, (0, 0), (500, 10), (510, 110), (520, 120))
    assert g.velocity(120) == 1000
    g = touches(fw, (900, 0), (950, 20), (1050, 120))
    assert g.velocity(120) == 1000  # exactly 100 ms back is in: 100 px in 100 ms


@pytest.mark.parametrize('start, end, want', ((0, 100, 6000), (100, 0, -6000), (0, 60, 6000)))
def test_velocity_clamps_a_burst_of_queued_moves(fw, start, end, want):
    assert touches(fw, (start, 0), (end, 10)).velocity(10) == want


def test_velocity_the_ring_keeps_the_last_8(fw):
    g = api(fw)
    for k in range(30):
        g.sample(5 * k + (9000 if k < 22 else 0), 10 * k)  # the first 22, far off, have left the ring
    assert g.velocity(290) == 500
    assert fw.var('coverflow__tc', Touch).n == 30


def test_velocity_through_the_clock_s_wrap(fw):
    base = 2**32 - 15
    assert touches(fw, (0, base), (10, base + 10), (20, (base + 20) % 2**32)).velocity((base + 20) % 2**32) == 1000


def test_sample_fills_the_ring_in_order(fw):
    g = api(fw)
    tc = fw.var('coverflow__tc', Touch)
    for k in range(9):
        g.sample(k, 100 + k)
    assert list(tc.sx) == [8, 1, 2, 3, 4, 5, 6, 7] and tc.st[0] == 108 and tc.n == 9


@pytest.mark.parametrize('a, b, q', ((7, 2, 3), (-7, 2, -4), (6, 2, 3), (-6, 2, -3), (0, 5, 0), (-1, 5, -1), (1, 5, 0)))
def test_floor_div_rounds_down_past_zero(fw, a, b, q):
    assert api(fw).floor_div(a, b) == q


@pytest.mark.parametrize('off, want', ((0, 0), (54, 0), (55, 110), (56, 110), (-54, 0), (-55, 0), (-56, -110), (165, 220), (-165, -110), (110, 110)))
def test_round_stride_to_the_nearest_whole_cover(fw, off, want):
    assert api(fw).round_stride(off, 110) == want


def cf(fw):
    return fw.var('cf', Cf)


@pytest.mark.parametrize('slide, screen, timer, live', ((1, COVERS, 0, 1), (0, COVERS, 0, 0), (1, TRACKS, 0, 0), (1, LOADING, 0, 0), (1, COVERS, 5, 0)))
def test_covers_live_needs_the_covers_with_no_change_pending(fw, slide, screen, timer, live):
    c = cf(fw)
    c.slide, c.screen, c.timer = slide, screen, timer
    assert api(fw).covers_live() == live


class Shelf:
    """The playlists' slide_menu, which stops at its ends: its index, offset and animator in plain memory, n covers,
    stride 110."""
    def __init__(self, fw, n=4, index=1, offset=0, scoped=PLAYLISTS):
        self.fw, self.g, self.raw, self.anim = fw, api(fw), C.create_string_buffer(0x200), C.create_string_buffer(0x100)
        self.p = C.addressof(self.raw)
        self.set(0x78, index)
        self.set(0x84, 10)  # the spacer
        self.set(0x94, offset)
        fw.mock('widget_count_children', lambda *_: n)
        fw.mock('slide_menu_item_width', lambda *_: 100)
        fw.mock('widget_animator_scroll_create', lambda *_: C.addressof(self.anim))
        fw.mock('widget_animator_on', lambda *_: 1)
        self.goals = []
        fw.mock('widget_animator_scroll_set_params', lambda a, x0, y0, x1, y1, _: self.goals.append(C.c_int(x1).value))
        c = cf(fw)
        c.slide, c.screen, c.scoped, c.count = self.p, COVERS, scoped, n

    def set(self, off, v):
        struct.pack_into('<i', self.raw, off, v)

    def get(self, off):
        return struct.unpack_from('<i', self.raw, off)[0]

    @property
    def ms(self):
        return struct.unpack_from('<i', self.anim, 0x20)[0]


def test_stride_is_an_item_and_its_spacer(fw):
    s = Shelf(fw)
    assert s.g.stride_of(s.p) == 110


def test_the_library_s_covers_are_unbounded(fw):
    s = Shelf(fw, scoped=0)
    assert s.g.bound(9999, 110, 1) == 9999 and s.g.unbound(-9999, 110) == -9999


def test_bound_stops_at_the_ends_without_rubber(fw):
    s = Shelf(fw, n=4, index=1)
    hi, lo = 110, -220  # one album before this one, two after
    assert [s.g.bound(x, 110, 0) for x in (hi, hi + 1, 500, lo, lo - 1, -500, 0)] == [hi, hi, hi, lo, lo, lo, 0]


def test_the_rubber_band_is_ios_s_and_never_reaches_half_a_cover(fw):
    s = Shelf(fw, n=4, index=1)
    d = 2 * 110 // 5
    assert s.g.bound(50, 110, 1) == 50  # inside: nothing
    last = 110
    for x in range(1, 2000, 7):
        got = s.g.bound(110 + x, 110, 1)
        want = x * .55 / (1 + x * .55 / d)
        assert abs(got - 110 - want) <= 1, x
        assert last <= got < 110 + 55, x  # it gives ever less, and the centre stays clear of the wrap
        last = got
    assert s.g.bound(-220 - 100, 110, 1) == -220 - (s.g.bound(110 + 100, 110, 1) - 110)  # the other end alike


def test_unbound_undoes_the_rubber_band(fw):
    s = Shelf(fw, n=4, index=1)
    for x in range(1, 300, 3):
        shown = s.g.bound(110 + x, 110, 1)  # both truncate, so the finger lies between two shown pixels' inverses
        if shown > 110 and shown + 1 - 110 < 44:
            assert s.g.unbound(shown, 110) <= 110 + x <= s.g.unbound(shown + 1, 110) + 1, x


def test_unbound_inside_or_past_the_band_s_limit_is_itself(fw):
    s = Shelf(fw, n=4, index=1)
    assert s.g.unbound(50, 110) == 50
    assert s.g.unbound(110 + 44, 110) == 110 + 44  # a is d: the formula would divide by nothing


@pytest.mark.parametrize('offset, want', ((40, 0), (60, 110), (-40, 0), (-60, -110), (0, 0)))
def test_a_release_with_no_speed_settles_on_the_nearest_cover(fw, offset, want):
    s = Shelf(fw, scoped=0, offset=offset)
    s.g.release(0)
    if offset:
        assert s.goals == [want] and s.ms == 280


def test_a_release_at_rest_does_nothing(fw):
    s = Shelf(fw, scoped=0)
    s.g.release(0)
    assert not s.goals and not fw.calls('widget_animator_scroll_create')


def test_a_flick_carries_400_ms_and_lands_on_a_whole_cover(fw):
    s = Shelf(fw, scoped=0, offset=0)
    s.g.release(900)  # 1100 px/s of offset: 440 px on
    assert s.goals == [440]
    assert s.ms == 3000 * 440 // 1100  # cubic out starts at three times the mean


def test_a_flick_never_goes_back_against_itself(fw):
    s = Shelf(fw, scoped=0, offset=50)
    s.g.release(250)
    assert s.goals[-1] >= 110


def test_a_backward_flick_never_goes_forward(fw):
    s = Shelf(fw, scoped=0, offset=-50)
    s.g.release(-250)
    assert s.goals[-1] <= -110


@pytest.mark.parametrize('offset, v, ms', ((0, 123, 1400), (100, 131, 280), (0, 900, 1200)))
def test_a_glide_is_three_times_its_mean_time_within_its_limits(fw, offset, v, ms):
    s = Shelf(fw, scoped=0, offset=offset)
    s.g.release(v)
    assert s.ms == ms


def test_a_flick_past_an_artist_s_last_album_stops_there(fw):
    s = Shelf(fw, n=4, index=1, offset=100)
    s.g.release(3000)
    assert s.goals == [110]


def event(kind, x=0, y=0, t=1):
    raw = C.create_string_buffer(0x40)
    struct.pack_into('<i', raw, 0, kind)
    struct.pack_into('<I', raw, 8, t)
    struct.pack_into('<ii', raw, 0x18, x, y)
    return raw


DOWN, MOVE, UP, ABORT = 0x100, 0x102, 0x104, 0x107


def test_a_touch_stamps_its_own_time_or_the_clock_s(fw):
    g = api(fw)
    fw.mock('time_now_ms', lambda *_: 777)
    assert g.stamp(event(UP, t=55)) == 55 and g.stamp(event(UP, t=0)) == 777


def test_a_drag_moves_the_covers_after_the_slop(fw):
    s = Shelf(fw, scoped=0, offset=0)
    touch = s.g.touch
    touch(None, event(DOWN, 100, 50))
    tc = fw.var('coverflow__tc', Touch)
    assert tc.down and not tc.drag
    touch(None, event(MOVE, 107, 50))
    assert not tc.drag and s.get(0x94) == 0  # inside the slop: still a tap
    touch(None, event(MOVE, 110, 50))
    assert tc.drag and tc.x == 110 and s.get(0x94) == 0  # no jump by the slop
    touch(None, event(MOVE, 200, 50))
    assert s.get(0x94) == 110  # 90 px of finger is one album


def test_a_mostly_vertical_touch_is_stock_s(fw):
    s = Shelf(fw, scoped=0)
    s.g.touch(None, event(DOWN, 100, 50))
    s.g.touch(None, event(MOVE, 102, 70))
    assert not fw.var('coverflow__tc', Touch).down


def test_a_touch_on_a_changing_screen_is_not_taken(fw):
    s = Shelf(fw, scoped=0)
    cf(fw).timer = 3
    s.g.touch(None, event(DOWN, 100, 50))
    assert not fw.var('coverflow__tc', Touch).down


def test_a_touch_catches_a_moving_shelf_and_settles_it(fw):
    s = Shelf(fw, scoped=0, offset=70)
    struct.pack_into('<Q', s.raw, 0xc8, C.addressof(s.anim))
    s.g.touch(None, event(DOWN, 100, 50))
    tc = fw.var('coverflow__tc', Touch)
    assert tc.caught and fw.calls('widget_animator_pause')
    s.g.touch(None, event(UP, 100, 50, t=5))
    assert s.goals == [110]


def test_an_aborted_drag_settles_without_a_throw(fw):
    s = Shelf(fw, scoped=0)
    s.g.touch(None, event(DOWN, 100, 50, t=1))
    for k in range(1, 5):
        s.g.touch(None, event(MOVE, 100 + 10 * k, 50, t=10 * k))
    s.g.touch(None, event(ABORT, 140, 50, t=41))
    assert s.goals == [s.g.round_stride(s.get(0x94), 110)] and s.ms == 280


def test_the_lost_release_ends_at_the_next_key(fw):
    s = Shelf(fw, scoped=0, offset=70)
    s.g.touch(None, event(DOWN, 100, 50))
    s.g.lift(1, 0)
    assert not fw.var('coverflow__tc', Touch).down


# Albums apart

def test_album_cmp_the_name_case_aside(fw):
    g = api(fw)
    assert g.album_cmp(Rec('Abbey Road', '/a/x').p, Rec('ABBEY ROAD', '/a/y').p) == 0
    assert g.album_cmp(Rec('Abbey', '/a/x').p, Rec('Help', '/a/x').p) < 0


def test_album_cmp_tagged_names_come_first(fw):
    g = api(fw)
    assert g.album_cmp(Rec('Abbey', '/a/x').p, Rec('', '/a/x').p) < 0
    assert g.album_cmp(Rec(None, '/a/x').p, Rec('Abbey', '/a/x').p) > 0


def test_album_cmp_a_compilation_stays_one_album_across_folders(fw):
    g = api(fw)
    a, b = Rec('Hits', '/m/1/a.mp3', album_artist='Various'), Rec('Hits', '/m/2/b.mp3', album_artist='various')
    other = Rec('Hits', '/m/1/a.mp3', album_artist='Other')
    assert g.album_cmp(a.p, b.p) == 0 and g.album_cmp(a.p, other.p) != 0


def test_album_cmp_an_album_artist_on_one_side_tells_them_apart(fw):
    g = api(fw)
    a, b = Rec('Hits', '/m/a.mp3', album_artist='X'), Rec('Hits', '/m/a.mp3')
    assert g.album_cmp(a.p, b.p) < 0 and g.album_cmp(b.p, a.p) > 0


def test_album_cmp_otherwise_the_folder_disc_folders_as_one(fw):
    g = api(fw)
    assert g.album_cmp(Rec('A', '/m/A/CD1/1.flac').p, Rec('A', '/m/A/CD2/1.flac').p) == 0
    assert g.album_cmp(Rec('A', '/m/A/Disc 1/1.flac').p, Rec('A', '/m/A/disk_02/1.flac').p) == 0
    assert g.album_cmp(Rec('A', '/m/A/1.flac').p, Rec('A', '/m/B/1.flac').p) != 0
    assert g.album_cmp(Rec('A', '/m/a/1.flac').p, Rec('A', '/m/A/1.flac').p) != 0  # folders keep their case


def test_folder_cmp_keeps_disc_folders_apart(fw):
    g = api(fw)
    assert g.folder_cmp(Rec('A', '/m/A/CD1/1.flac').p, Rec('A', '/m/A/CD2/1.flac').p) != 0
    assert g.folder_cmp(Rec('A', '/m/A/1.flac').p, Rec('B', '/m/A/2.flac').p) == 0


def test_a_path_with_no_folder_or_none_at_all_compares(fw):
    g = api(fw)
    assert g.album_cmp(Rec('A').p, Rec('A', 'x.mp3').p) == 0
    assert g.album_cmp(Rec('A').p, Rec('A', '/m/x.mp3').p) != 0


@pytest.mark.parametrize('path, disc, want', (
    ('/m/A/1.flac', 0, '/m/A'), ('/m/A/CD1/1.flac', 1, '/m/A'), ('/m/A/CD1/1.flac', 0, '/m/A/CD1'), ('/m/A/cd12/1.flac', 1, '/m/A'),
    ('/m/A/CD 2/1.flac', 1, '/m/A'), ('/m/A/CD-2/1.flac', 1, '/m/A'), ('/m/A/Disc 2/1.flac', 1, '/m/A'), ('/m/A/DISK_03/1.flac', 1, '/m/A'),
    ('/m/A/disc3/1.flac', 1, '/m/A'), ('/m/A/Track1/1.flac', 1, '/m/A/Track1'), ('/m/ABCD1/1.flac', 1, '/m/ABCD1'),
    ('/m/2001/1.flac', 1, '/m/2001'), ('/CD1/1.flac', 1, ''), ('x.flac', 1, ''), ('/x.flac', 1, ''), ('CD1/x.flac', 1, 'CD1'), ('', 1, ''),
    ('/m/Disc/1.flac', 1, '/m/Disc'), ('/m/dis1/1.flac', 1, '/m/dis1')))
def test_album_dir(fw, path, disc, want):
    assert api(fw).album_dir(path.encode(), disc) == len(want)


def test_album_dir_hostile_sweep(fw):
    rng = random.Random(4)
    album_dir = fw.fn('album_dir', C.c_uint, C.c_void_p, C.c_int)
    for _ in range(2000):
        s = bytes(rng.choices(b'/CDcdDISKik 12_-a.', k=rng.randrange(0, 24)))
        with Exact(s + b'\0') as p:
            assert album_dir(p, 1) <= album_dir(p, 0) <= len(s), s


def records(rng):
    names = (None, '', 'Abbey', 'ABBEY', 'Help', 'help!', 'Zoo')
    artists = (None, '', 'X', 'x', 'Y')
    dirs = ('/m/A/', '/m/a/', '/m/A/CD1/', '/m/A/CD2/', '/m/B/', 'top', '')
    return [Rec(rng.choice(names), rng.choice(dirs) + 't.mp3', album_artist=rng.choice(artists), id=k + 1) for k in range(40)]


def sign(x):
    return (x > 0) - (x < 0)


def test_album_cmp_is_an_order_qsort_can_trust(fw):
    g, rng = api(fw), random.Random(5)
    v = records(rng)
    table = [[sign(g.album_cmp(a.p, b.p)) for b in v] for a in v]
    n = len(v)
    for i in range(n):
        assert table[i][i] == 0
        for j in range(n):
            assert table[i][j] == -table[j][i]
            for k in range(n):
                if table[i][j] <= 0 and table[j][k] <= 0:
                    assert table[i][k] <= 0, (i, j, k)


def test_album_id_is_the_same_exactly_when_album_cmp_says_so(fw):
    g, rng = api(fw), random.Random(6)
    v = records(rng)
    for a in v:
        for b in v:
            assert (g.album_id(a.p) == g.album_id(b.p)) == (g.album_cmp(a.p, b.p) == 0)


def test_album_id_the_unknown_card_is_one_album_and_never_0(fw):
    g = api(fw)
    assert g.album_id(Rec('x', '/a/1', id=-1).p) == g.album_id(Rec('y', '/b/2', id=-1).p) != 0


def test_fnv_separates_its_fields(fw):
    g = api(fw)
    assert g.fnv(g.fnv(FNV, b'ab'), b'c') != g.fnv(g.fnv(FNV, b'a'), b'bc')
    assert g.fnv(FNV, None) == FNV * 16777619 % 2**32


def rows(*recs, order=None):
    v = (Row * max(1, len(recs)))()
    for i, rec in enumerate(recs):
        v[i].a, v[i].j = rec.p, i if order is None else order[i]
    return v


def test_name_cmp_case_aside_and_no_name_is_empty(fw):
    g = api(fw)
    assert g.name_cmp(Rec('abc').p, Rec('ABC').p) == 0
    assert g.name_cmp(Rec('abc').p, Rec('abd').p) < 0
    assert g.name_cmp(Rec(None).p, Rec('').p) == 0


def test_a_tie_goes_by_the_list_s_order(fw):
    g = api(fw)
    a, b = Row(None, 3), Row(None, 7)
    assert g.tie(0, r(a), r(b)) < 0 and g.tie(0, r(b), r(a)) > 0 and g.tie(0, r(a), r(a)) == 0
    assert g.tie(5, r(a), r(b)) == 5 and g.tie(-5, r(b), r(a)) == -5


def test_by_name_and_by_album_sort_with_ties_in_order(fw):
    g = api(fw)
    recs = [Rec('b', '/1/x'), Rec('A', '/2/x'), Rec('a', '/1/x'), Rec('B', '/1/x')]
    for fn, want in ((fw.lib.coverflow__by_name, [1, 2, 0, 3]), (fw.lib.coverflow__by_album, [2, 1, 0, 3])):
        v = rows(*recs)
        libc.qsort(v, 4, C.sizeof(Row), C.cast(fn, C.c_void_p))
        assert [row.j for row in v] == want


CMP = C.CFUNCTYPE(C.c_int, C.c_void_p, C.c_void_p)


@pytest.mark.parametrize('values', ([], [5], [1, 3, 5], [1, 1, 1], [1, 2, 2, 2, 3], list(range(100))))
def test_lower_is_the_first_not_before(fw, values):
    g = api(fw)
    v = (Row * max(1, len(values)))()
    for i, x in enumerate(values):
        v[i].a = x + 1000
    cmp = CMP(lambda a, b: a - b)
    for probe in range(-1, 102):
        want = next((i for i, x in enumerate(values) if x >= probe), len(values))
        assert g.lower(v, len(values), probe + 1000, cmp) == want, probe


def sorted_rows(fw, recs):
    v = rows(*recs)
    libc.qsort(v, len(recs), C.sizeof(Row), C.cast(fw.lib.coverflow__by_album, C.c_void_p))
    return v


def test_album_in_finds_a_row_s_album_or_says_none(fw):
    g = api(fw)
    recs = [Rec('Zoo', '/z/1'), Rec('Abbey', '/a/1'), Rec('Help', '/h/1')]
    v = sorted_rows(fw, recs)
    for j, rec in enumerate(recs):
        assert g.album_in(v, 3, rec.p) == j
    assert g.album_in(v, 3, Rec('abbey', '/a/2').p) == 1  # the same album by album_cmp
    assert g.album_in(v, 3, Rec('Abbey', '/b/1').p) == -1
    assert g.album_in(v, 3, Rec('Zzz', '/z/1').p) == -1
    assert g.album_in(v, 0, recs[0].p) == -1


def test_album_in_two_rows_of_one_album_gives_the_first(fw):
    g = api(fw)
    recs = [Rec('B', '/b/1'), Rec('A', '/a/1'), Rec('a', '/a/2')]
    assert g.album_in(sorted_rows(fw, recs), 3, recs[2].p) == 1


def test_album_index_reads_the_stock_rows(fw):
    g = api(fw)
    recs = [Rec('Zoo', '/z/1'), Rec('Abbey', '/a/1')]
    v = sorted_rows(fw, recs)
    fw.var('coverflow__stock_rows', C.c_void_p).value = C.addressof(v)
    fw.var('coverflow__stock_n', C.c_uint).value = 2
    assert g.album_index(recs[0].p) == 0 and g.album_index(recs[1].p) == 1 and g.album_index(Rec('No', '/z/1').p) == -1


@pytest.mark.parametrize('kind, aa, want', ((1, 'Various', b'Various'), (1, '', b'Song Artist'), (1, None, b'Song Artist'), (0, 'Various', b'Song Artist')))
def test_cover_artist_is_the_album_artist_only_when_artists_says_so(fw, kind, aa, want):
    fw.var('fw_ctx_artist_type', C.c_int).value = kind
    assert api(fw).cover_artist(Rec(artist='Song Artist', album_artist=aa).p) == want


# Letters and jumps

class Albums:
    def __init__(self, fw, names=None, artists=None, unknown=(), scoped=0, sort=SORT_ALBUM):
        self.recs = [Rec(artist=n) if artists else Rec(n, id=-1 if i in unknown else 0) for i, n in enumerate(names or artists)]
        fw.mock('fw_deque_at', lambda d, i, *_: self.recs[i].p)
        fw.mock('fw_deque_size', lambda *_: len(self.recs))
        c = cf(fw)
        c.albums, c.slide, c.screen, c.scoped, c.sort = 0x5000, 0x6000, COVERS, scoped, sort
        self.g = api(fw)

    def jump(self, start, step):
        letter = C.c_uint(0xdead)
        return self.g.coverflow_jump(0x6000, start, step, r(letter)), letter.value


@pytest.mark.parametrize('name, want', (
    ('Abbey', 'A'), ('abbey', 'A'), ('zoo', 'Z'), ('1984', '#'), ('', '#'), ('!!!', '#'), (' Spaced', '#'), ('The Wall', 'W'), ('A Night', 'N'),
    ('An Awesome', 'A'), ('the', 'T'), ('Thee', 'T'), ('Étude', 'É'), ('étude', 'é'), ('日本', '日'), ('Ñ', 'Ñ'), ('😀 Smile', '😀'),
    ('¿Qué?', '#'), ('«Live»', '#'), ('１２', '#'), ('‘Tis', '#'), ('　Wide', '#')))
def test_initial_of_an_album(fw, name, want):
    a = Albums(fw, [name])
    assert a.g.initial(0) == ord(want)


def test_initial_the_unknown_row_is_hash(fw):
    assert Albums(fw, ['Abbey'], unknown=(0,)).g.initial(0) == ord('#')


def test_initial_goes_by_the_artist_in_artist_order(fw):
    a = Albums(fw, artists=['The Beatles'], sort=SORT_ARTIST)
    assert a.g.initial(0) == ord('B')


@pytest.mark.parametrize('raw', (b'\xc3', b'\xc3A', b'\xe2\x82', b'\xe2\x82A', b'\xf0\x9f\x98', b'\xf0\x9f\x98A'))
def test_initial_a_cut_sequence_is_hash(fw, raw):
    assert Albums(fw, [raw]).g.initial(0) == ord('#')


def test_initial_any_bytes_stay_in_bounds(fw):
    rng = random.Random(7)
    a = Albums(fw, ['x'])
    for _ in range(2000):
        a.recs = [Rec(rng.randbytes(rng.randrange(0, 12)).replace(b'\0', b'A'))]
        assert 0 <= a.g.initial(0) <= 0x1fffff


def test_the_misc_table_is_sorted_and_is_numbers_punctuation_and_separators():
    source = (ROOT/'patch/coverflow.c').read_text()
    def ranges(name, plane):
        body = source[source.index(f'{name}[][2] = {{'):]
        body = body[:body.index('};')]
        return [(int(a, 16) + plane, int(b, 16) + plane) for a, b in re.findall(r'\{ (0x[0-9a-f]+), (0x[0-9a-f]+) \}', body)]
    table = ranges('initial_misc', 0) + ranges('initial_misc1', 0x10000)  # the BMP's, then plane 1's
    assert all(b < 0x10000 for a, b in ranges('initial_misc', 0)) and all(b < 0x10000 for a, b in ranges('initial_misc1', 0))
    assert all(a <= b for a, b in table) and all(x[1] < y[0] for x, y in zip(table, table[1:]))
    listed = {c for a, b in table for c in range(a, b + 1)}
    for c in listed:
        assert unicodedata.category(chr(c))[0] in 'NPZCS', hex(c)  # Cn: newer than this python's Unicode
    if tuple(map(int, unicodedata.unidata_version.split('.'))) <= (16, 0, 0):  # the table's version
        missing = [c for c in range(0xa0, 0x20000) if unicodedata.category(chr(c))[0] in 'NPZ' and c not in listed]
        assert not missing, [hex(c) for c in missing[:20]]


@pytest.mark.parametrize('text, want', (('\u00bfQu\u00e9', ord('#')), ('\U0001d7ce', ord('#')), ('\U00010140', ord('#')),
                                        ('\U0001f600', 0x1f600), ('\u00c9t\u00e9', 0xc9), ('\U00020000', 0x20000)))
def test_initial_of_a_number_or_mark_in_either_plane_is_hash(fw, text, want):
    """initial_misc holds the BMP's ranges and initial_misc1 plane 1's, each as 16 bits: a digit or mark of
    either is '#', anything else its own code point."""
    assert Albums(fw, [text.encode()]).g.initial(0) == want


FIVE = ['Abba', 'Air', 'Beck', 'Blur', 'Cream']


@pytest.mark.parametrize('start, want', ((0, (2, ord('B'))), (1, (1, ord('B'))), (2, (2, ord('C'))), (3, (1, ord('C'))), (4, (1, 0))))
def test_jump_forward_lands_on_the_next_letter(fw, start, want):
    assert Albums(fw, FIVE).jump(start, 1) == want


@pytest.mark.parametrize('start, want', ((4, (2, ord('B'))), (3, (3, ord('A'))), (2, (2, ord('A')))))
def test_jump_back_lands_on_the_group_s_start(fw, start, want):
    assert Albums(fw, FIVE).jump(start, -1) == want


@pytest.mark.parametrize('start', (0, 1))
def test_jump_back_from_the_first_letter_wraps_to_the_last_album(fw, start):
    assert Albums(fw, FIVE).jump(start, -1) == (start + 1, 0)


def test_jump_one_album_only(fw):
    assert Albums(fw, ['Abba']).jump(0, 1) == (1, 0)
    assert Albums(fw, ['Abba']).jump(0, -1) == (1, 0)


def test_jump_the_utility_cards_and_other_orders_step_one_by_one(fw):
    a = Albums(fw, FIVE)
    assert a.jump(-1, 1) == (1, 0) and a.jump(5, 1) == (1, 0)
    c = cf(fw)
    c.sort = SORT_ARTIST + 1
    assert a.jump(0, 1) == (1, 0)
    c.sort, c.scoped = SORT_ALBUM, 1
    assert a.jump(0, 1) == (1, 0)
    c.scoped, c.screen = 0, TRACKS
    assert a.jump(0, 1) == (1, 0)
    c.screen = COVERS
    letter = C.c_uint(0xdead)
    assert a.g.coverflow_jump(0x6001, 0, 1, r(letter)) == 1 and letter.value == 0


# The files

class Card:
    """fopen and friends on a dict; a read past a file's end is short, as stdio's."""
    def __init__(self, fw, files=None):
        self.files, self.open, self.seen = dict(files or {}), {}, []
        self.fail_rename = self.fail_sync = False
        for name in 'fopen fread fwrite fseek ftell rename unlink'.split():
            fw.mock('fw_' + name, getattr(self, name))
        fw.mock('fw_fclose', lambda *_: 0)
        fw.mock('fw_fflush', lambda *_: 0)
        fw.mock('fw_fsync', lambda *_: self.fail_sync)
        fw.mock('fw_access', lambda path, *_: C.string_at(path).decode() not in self.files)

    def fopen(self, path, mode, *_):
        name, kind = C.string_at(path).decode(), C.string_at(mode)[:1]
        if kind == b'r' and name not in self.files:
            return 0
        handle = 0x1000 + len(self.open)
        self.open[handle] = [name, 0]
        if kind == b'w':
            self.files[name] = b''
        elif kind == b'a':
            self.files.setdefault(name, b'')
        return handle

    def fread(self, buf, size, n, f, *_):
        name, at = self.open[f]
        data = self.files[name][at:at + size * n]
        C.memmove(buf, data, len(data))
        self.open[f][1] += len(data)
        return len(data) // size

    def fwrite(self, buf, size, n, f, *_):
        self.files[self.open[f][0]] += C.string_at(buf, size * n)
        return n

    def fseek(self, f, off, whence, *_):
        self.open[f][1] = off if whence == 0 else len(self.files[self.open[f][0]]) + off
        return 0

    def ftell(self, f, *_):
        return self.open[f][1]

    def rename(self, a, b, *_):
        if self.fail_rename:
            return -1
        self.files[C.string_at(b).decode()] = self.files.pop(C.string_at(a).decode())
        return 0

    def unlink(self, path, *_):
        self.files.pop(C.string_at(path).decode(), None)
        return 0


def test_file_size(fw):
    g = api(fw)
    Card(fw, {'/a': b'12345', '/e': b''})
    assert g.file_size(b'/a') == 5 and g.file_size(b'/e') == 0 and g.file_size(b'/none') == -1


def test_blob_io_a_write_goes_through_its_temporary(fw):
    g = api(fw)
    card = Card(fw)
    buf = (C.c_ubyte * 4)(1, 2, 3, 4)
    assert g.blob_io(b'/a', b'/a.tmp', buf, 4, 1) == 1
    assert card.files == {'/a': bytes(buf)}
    out = (C.c_ubyte * 4)()
    assert g.blob_io(b'/a', b'/a.tmp', out, 4, 0) == 1 and bytes(out) == bytes(buf)


def test_blob_io_a_short_read_zeroes_the_buffer(fw):
    g = api(fw)
    Card(fw, {'/a': b'123'})
    out = (C.c_ubyte * 4)(9, 9, 9, 9)
    assert g.blob_io(b'/a', b'/a.tmp', out, 4, 0) == 0 and bytes(out) == bytes(4)


def test_blob_io_no_file_leaves_the_buffer(fw):
    g = api(fw)
    Card(fw)
    out = (C.c_ubyte * 4)(9, 9, 9, 9)
    assert g.blob_io(b'/a', b'/a.tmp', out, 4, 0) == 0 and bytes(out) == b'\x09' * 4


def test_blob_io_a_failed_sync_or_rename_is_a_failed_write(fw):
    g = api(fw)
    card = Card(fw, {'/a': b'old!'})
    buf = (C.c_ubyte * 4)(1, 2, 3, 4)
    card.fail_sync = True
    assert g.blob_io(b'/a', b'/a.tmp', buf, 4, 1) == 0 and card.files['/a'] == b'old!'
    card.fail_sync, card.fail_rename = False, True
    assert g.blob_io(b'/a', b'/a.tmp', buf, 4, 1) == 0 and card.files['/a'] == b'old!'


def statuses(fw, status):
    fw.mock('fw_fork', lambda *_: 1234)

    def wait(pid, out, *_):
        C.c_int.from_address(out).value = status
        return pid
    fw.mock('fw_waitpid', wait)


@pytest.mark.parametrize('status, exited', ((0, 1), (1 << 8, 1), (127 << 8, 0), (9, 0), (0x80 | 9, 0)))
def test_ff_run_a_run_that_reached_ffmpeg_s_own_exit(fw, status, exited):
    g = api(fw)
    Card(fw, {'/d.ff': b'jpg'})
    statuses(fw, status)
    got = C.c_int(-1)
    ok = g.ff_run(b'/s', b'/d', 160, 160, r(got))
    assert got.value == exited and ok == (status == 0)
    assert fw.var('ff_kills', C.c_uint).value == (0 if exited else 1)


def test_ff_run_success_renames_the_picture_into_place(fw):
    g = api(fw)
    card = Card(fw, {'/d.ff': b'jpg'})
    statuses(fw, 0)
    assert g.ff_run(b'/s', b'/d', 160, 160, r(C.c_int())) == 1 and card.files == {'/d': b'jpg'}


def test_ff_run_an_empty_or_failed_output_leaves_the_destination_alone(fw):
    g = api(fw)
    card = Card(fw, {'/d.ff': b'', '/d': b'old'})
    statuses(fw, 0)
    assert g.ff_run(b'/s', b'/d', 160, 160, r(C.c_int())) == 0 and card.files == {'/d': b'old'}
    card.files['/d.ff'] = b'partial'
    statuses(fw, 9)
    assert g.ff_run(b'/s', b'/d', 160, 160, r(C.c_int())) == 0 and card.files == {'/d': b'old'}


def test_ff_run_a_refused_fork_counts_as_killed(fw):
    g = api(fw)
    Card(fw)
    fw.mock('fw_fork', lambda *_: -1)
    got = C.c_int(-1)
    assert g.ff_run(b'/s', b'/d', 160, 160, r(got)) == 0 and got.value == 0
    assert fw.var('ff_kills', C.c_uint).value == 1


def test_ff_run_a_signal_mid_wait_waits_again(fw):
    g = api(fw)
    Card(fw, {'/d.ff': b'jpg'})
    fw.mock('fw_fork', lambda *_: 1234)
    errno = C.c_int(4)
    fw.mock('fw___errno_location', lambda *_: C.addressof(errno))
    tries = []

    def wait(pid, out, *_):
        tries.append(pid)
        if len(tries) == 1:
            return -1
        C.c_int.from_address(out).value = 0
        return pid
    fw.mock('fw_waitpid', wait)
    assert g.ff_run(b'/s', b'/d', 160, 160, r(C.c_int())) == 1 and len(tries) == 2


def art(key, suffix=''):
    return f'/mnt/mmc/.coverflow/{key:08x}.jpg{suffix}'


def job(track, key=0xabc, shared=0):
    return Job(track, key, 0, shared)


def builder(fw, card, cover=False, thumb=False, ff=None):
    """The art calls: stock's cover read, stock's thumbnailer, and ffmpeg's exit status (None: a refused fork)."""
    fw.mock('toolsGetAlbumCover', lambda t, dst, *_: (card.files.update({C.string_at(dst).decode(): b'art'}), 1)[1] if cover else 0)
    fw.mock('toolsThumbSpecCover', lambda src, dst, *_: (card.files.update({C.string_at(dst).decode(): b'art'}), 1)[1] if thumb else 0)
    if ff is None:
        fw.mock('fw_fork', lambda *_: -1)
    else:
        statuses(fw, ff)


def test_build_art_embedded_art_is_the_cache_file(fw):
    g = api(fw)
    card = Card(fw, {'/m/a.mp3': b''})
    builder(fw, card, cover=True)
    j = job(b'/m/a.mp3')
    g.build_art(r(j))
    assert card.files[art(0xabc)] == b'art' and art(0xabc, '.tmp') not in card.files


def test_build_art_the_image_beside_the_track_is_next(fw):
    g = api(fw)
    card = Card(fw, {'/m/a.mp3': b'', '/m/folder.png': b'x'})
    builder(fw, card, thumb=True)
    j = job(b'/m/a.mp3')
    g.build_art(r(j))
    assert card.files[art(0xabc)] == b'art'


def test_build_art_a_folder_too_long_for_its_cover_s_path_lends_no_file(fw):
    """Cut at the buffer, the cover's path is a prefix: whatever file that names is not the album's art."""
    g = api(fw)
    folder = '/m/' + 'x' * 1011  # with "/cover.jpg" one byte over the 1024-byte path
    card = Card(fw, {folder + '/a.mp3': b'', folder + '/cover.jp': b'x'})
    builder(fw, card, thumb=True, ff=0)
    g.build_art(r(job((folder + '/a.mp3').encode())))
    assert card.files[art(0xabc)] == b''  # nothing of its own: the marker


def test_build_art_a_shared_folder_s_cover_is_not_this_album_s(fw):
    g = api(fw)
    card = Card(fw, {'/m/a.mp3': b'', '/m/cover.jpg': b'x'})
    builder(fw, card, thumb=True, ff=0)
    j = job(b'/m/a.mp3', shared=1)
    g.build_art(r(j))
    assert card.files[art(0xabc)] == b''  # none of its own: the "no art" marker


def test_build_art_no_art_leaves_an_empty_marker(fw):
    g = api(fw)
    card = Card(fw, {'/m/a.mp3': b''})
    builder(fw, card, ff=0)
    j = job(b'/m/a.mp3')
    g.build_art(r(j))
    assert card.files[art(0xabc)] == b'' and art(0xabc, '.tmp') not in card.files


def test_build_art_a_killed_ffmpeg_leaves_no_marker_so_the_next_open_retries(fw):
    g = api(fw)
    card = Card(fw, {'/m/a.mp3': b''})
    builder(fw, card, ff=9)
    j = job(b'/m/a.mp3')
    g.build_art(r(j))
    assert art(0xabc) not in card.files


def test_build_art_with_the_storage_gone_touches_nothing(fw):
    g = api(fw)
    card = Card(fw)
    builder(fw, card, cover=True)
    j = job(b'/m/a.mp3')
    g.build_art(r(j))
    assert not card.files and not fw.calls('toolsGetAlbumCover')


def test_build_art_holds_both_stock_locks_round_stock_s_call(fw):
    g = api(fw)
    card = Card(fw, {'/m/a.mp3': b''})
    builder(fw, card, cover=True)
    order = []
    fw.mock('fw_pthread_mutex_lock', lambda m, *_: order.append(('lock', m)))
    fw.mock('fw_pthread_mutex_unlock', lambda m, *_: order.append(('unlock', m)))
    j = job(b'/m/a.mp3')
    g.build_art(r(j))
    assert [k for k, _ in order] == ['lock', 'lock', 'unlock', 'unlock']
    assert order[0][1] == order[3][1] and order[1][1] == order[2][1] and order[0][1] != order[1][1]


BUILDING = '/mnt/mmc/.coverflow/building'
CRASH_LOG = '/mnt/mmc/q2-crash.log'


def test_the_worker_notes_each_album_before_its_build_and_drops_the_note_after_the_last(fw):
    g = api(fw)
    card = Card(fw, {'/m/a.mp3': b'', '/m/b.mp3': b''})
    builder(fw, card, ff=0)
    noted = []
    fw.mock('toolsGetAlbumCover', lambda *_: noted.append(card.files.get(BUILDING)) or 0)
    jobs = (Job * 2)(Job(b'/m/a.mp3', 0xa, 0), Job(b'/m/b.mp3', 0xb, 1))
    c = cf(fw)
    c.jobs, c.total, c.done, c.cancel, c.art_ring, c.want = C.addressof(jobs), 2, 0, 0, 2, 0
    assert g.worker(None) is None and c.done == 2
    assert noted == [struct.pack('<I', 0xa) + b'/m/a.mp3', struct.pack('<I', 0xb) + b'/m/b.mp3']
    assert set(card.files) == {'/m/a.mp3', '/m/b.mp3', art(0xa), art(0xb)}  # no note, no temporary


def test_strike_marks_the_album_the_last_build_never_finished_and_drops_the_note(fw):
    g = api(fw)
    card = Card(fw, {BUILDING: struct.pack('<I', 0xabc) + b'/m/a.flac', CRASH_LOG: b'== date\n'})
    g.art_strike(BUILDING.encode())
    assert card.files == {art(0xabc): b'', CRASH_LOG: b'== date\n== cover flow skipped\n/m/a.flac\n'}


def test_strike_of_a_note_torn_before_its_key_marks_nothing(fw):
    g = api(fw)
    card = Card(fw, {BUILDING: b'\xbc\x0a'})
    g.art_strike(BUILDING.encode())
    assert card.files == {}


def test_strike_leaves_a_cover_that_was_made_and_no_note_marks_nothing(fw):
    g = api(fw)
    card = Card(fw, {BUILDING: struct.pack('<I', 0xabc), art(0xabc): b'art'})
    g.art_strike(BUILDING.encode())
    assert card.files == {art(0xabc): b'art'}
    g.art_strike(BUILDING.encode())
    assert card.files == {art(0xabc): b'art'}


def test_a_clean_end_drops_the_note(fw):
    g = api(fw)
    card = Card(fw, {BUILDING: struct.pack('<I', 0xabc), BUILDING.replace('building', 'pl-building'): struct.pack('<I', 0xdef)})
    g.coverflow_clean_end()
    assert card.files == {}


@pytest.mark.parametrize('during', ['cover', 'note'])
def test_a_clean_end_mid_build_stops_the_worker_before_the_next_album_s_note(fw, during):
    """Power-off while album a builds (during its cover read, or as its note is synced): b is never begun, so no
    note of b's outlives demo to strike it at the next boot, and a's note goes with the worker."""
    g = api(fw)
    card = Card(fw, {'/m/a.mp3': b'', '/m/b.mp3': b''})
    builder(fw, card, ff=0)
    if during == 'cover':
        fw.mock('toolsGetAlbumCover', lambda *_: g.coverflow_clean_end() or 0)
    else:
        fw.mock('fw_fsync', lambda *_: g.coverflow_clean_end() or 0)
    jobs = (Job * 2)(Job(b'/m/a.mp3', 0xa, 0), Job(b'/m/b.mp3', 0xb, 1))
    c = cf(fw)
    c.jobs, c.total, c.done, c.cancel, c.art_ring, c.want = C.addressof(jobs), 2, 0, 0, 2, 0
    assert g.worker(None) is None and c.done == (during == 'cover')
    assert set(card.files) == {'/m/a.mp3', '/m/b.mp3'} | ({art(0xa)} if during == 'cover' else set())


def png(w, h):
    """A PNG's signature and IHDR: all of the file thumb reads."""
    return b'\x89PNG\r\n\x1a\n' + struct.pack('>I4sIIBBBBB', 13, b'IHDR', w, h, 8, 6, 0, 0, 0) + bytes(4)


def segment(marker, body):
    return struct.pack('>BBH', 0xff, marker, len(body) + 2) + body


APP0, EXIF = segment(0xe0, b'JFIF\0' + bytes(9)), segment(0xe1, b'Exif\0\0' + bytes(2000))
DQT, DHT, DAC = segment(0xdb, bytes(65)), segment(0xc4, bytes(28)), segment(0xcc, bytes(2))


def jpeg(sof, w, h, *before):
    """A JPEG to its scan: SOI, the segments before, the frame header (8 bits, 3 components), SOS."""
    frame = segment(sof, struct.pack('>BHHB', 8, h, w, 3) + bytes(9))
    return b'\xff\xd8' + b''.join(before) + frame + segment(0xda, bytes(10)) + bytes(64)


@pytest.mark.parametrize('head, big', (
    (png(4000, 4000), 1), (png(1025, 16), 1), (png(16, 1025), 1), (png(70000, 1), 1), (png(1, 70000), 1),
    (png(1024, 1024), 0), (png(4000, 4000)[:20], 0),
    (jpeg(0xc2, 3000, 3000, APP0, EXIF, DQT, DHT, DAC), 1), (jpeg(0xc2, 1025, 16), 1), (jpeg(0xc2, 16, 1025), 1),
    (jpeg(0xc6, 2000, 2000), 0), (jpeg(0xca, 2000, 2000), 0), (jpeg(0xce, 2000, 2000), 0),
    (png(4000, 4000).replace(b'IHDR', b'CgBI'), 0),
    (jpeg(0xc2, 1024, 1024, APP0), 0), (jpeg(0xc0, 4000, 4000, APP0, EXIF), 0), (jpeg(0xc1, 4000, 4000), 0),
    (jpeg(0xc2, 3000, 3000, *[APP0] * 63), 1), (jpeg(0xc2, 3000, 3000, *[APP0] * 64), 1),
    (jpeg(0xc2, 3000, 3000, APP0, b'\xff'), 1), (jpeg(0xc0, 4000, 4000, b'\xff\xff', APP0, b'\xff'), 0),
    (b'\xff\xd8' + bytes(64), 1),
    (b'RIFF\0\0\0\0WEBPVP8 ' + bytes(16), 0)))
def test_thumb_sends_a_picture_stock_would_decode_whole_to_ffmpeg(fw, head, big):
    """Stock's PNG reader expands the whole image before its 1024 px test, and libjpeg holds a progressive JPEG's
    full-size coefficients at any scale, both in demo, the OOM killer's pick (#216): those go to ffmpeg alone, whose
    child the kernel kills first. A baseline JPEG decodes scaled, so it stays with stock, as does what thumb can't
    size (a cut-short PNG header, another type) or ffmpeg can't decode (an arithmetic or hierarchical progressive JPEG).
    Fill bytes before a marker are stepped over, and a JPEG whose frame header isn't found in its first 64 segments
    goes to ffmpeg, as it may be a big progressive one (GHSA-w54x-mvfw-c7v3)."""
    g = api(fw)
    card = Card(fw, {'/p/a': head, '/d.ff': b'jpg'})
    builder(fw, card, thumb=True, ff=0)
    assert g.thumb(b'/p/a', b'/d', 160, 160) == 1 and card.files['/d'] == (b'jpg' if big else b'art')
    assert len(fw.calls('toolsThumbSpecCover')) == 1 - big and len(fw.calls('fw_fork')) == big


def ogg_rig(fw, cover=0, status=1 << 8, ogg=b'/m/a.ogg'):
    card = Card(fw, {ogg.decode(): b''})
    fw.mock('toolsGetAlbumCover', lambda *_: cover)
    statuses(fw, status)
    return card


def test_grid_cover_stock_s_hit_needs_no_ffmpeg(fw):
    g = api(fw)
    ogg_rig(fw, cover=1)
    assert g.coverflow_grid_cover(b'/m/a.ogg', b'/d', 1, 1) == 1 and not fw.calls('fw_fork')


@pytest.mark.parametrize('track', (b'/m/a.mp3', b'/m/a', b'/m/a.ogg.bak'))
def test_grid_cover_only_ogg_goes_to_ffmpeg(fw, track):
    g = api(fw)
    ogg_rig(fw)
    assert g.coverflow_grid_cover(track, b'/d', 1, 1) == 0 and not fw.calls('fw_fork')


@pytest.mark.parametrize('track', (b'/m/a.OGG', b'/m/a.opus', b'/m/a.Opus'))
def test_grid_cover_an_ogg_by_any_case_does(fw, track):
    g = api(fw)
    ogg_rig(fw, ogg=track)
    g.coverflow_grid_cover(track, b'/d', 1, 1)
    assert fw.calls('fw_fork')


def test_grid_cover_a_miss_is_remembered(fw):
    g = api(fw)
    ogg_rig(fw)
    assert g.coverflow_grid_cover(b'/m/a.ogg', b'/d', 1, 1) == 0
    assert g.coverflow_grid_cover(b'/m/a.ogg', b'/d', 1, 1) == 0
    assert len(fw.calls('fw_fork')) == 1


def test_grid_cover_a_killed_ffmpeg_or_a_gone_card_is_not_a_miss(fw):
    g = api(fw)
    card = ogg_rig(fw, status=9)
    g.coverflow_grid_cover(b'/m/a.ogg', b'/d', 1, 1)
    g.coverflow_grid_cover(b'/m/a.ogg', b'/d', 1, 1)
    assert len(fw.calls('fw_fork')) == 2
    statuses(fw, 1 << 8)
    del card.files['/m/a.ogg']
    g.coverflow_grid_cover(b'/m/a.ogg', b'/d', 1, 1)
    g.coverflow_grid_cover(b'/m/a.ogg', b'/d', 1, 1)
    assert len(fw.calls('fw_fork')) == 4


def test_grid_cover_the_lock_is_dropped_for_ffmpeg(fw):
    g = api(fw)
    ogg_rig(fw)
    order = []
    fw.mock('fw_pthread_mutex_unlock', lambda *_: order.append('unlock'))
    fw.mock('fw_pthread_mutex_lock', lambda *_: order.append('lock'))
    fw.mock('fw_fork', lambda *_: (order.append('fork'), -1)[1])
    g.coverflow_grid_cover(b'/m/a.ogg', b'/d', 1, 1)
    assert order == ['unlock', 'fork', 'lock']


def test_grid_cover_the_ring_of_misses_overwrites_the_oldest(fw):
    g = api(fw)
    tracks = [b'/m/%d.ogg' % k for k in range(1025)]
    ogg_rig(fw)
    Card(fw, {t.decode(): b'' for t in tracks})
    runs = []

    def wait(pid, out, *_):
        runs.append(pid)
        C.c_int.from_address(out).value = 1 << 8
        return pid
    fw.mock('fw_waitpid', wait)
    for t in tracks:
        g.coverflow_grid_cover(t, b'/d', 1, 1)
    assert len(runs) == 1025
    g.coverflow_grid_cover(tracks[1024], b'/d', 1, 1)
    assert len(runs) == 1025  # the last, remembered
    g.coverflow_grid_cover(tracks[0], b'/d', 1, 1)
    assert len(runs) == 1026  # the first, overwritten


def test_album_cover_tries_the_folder_image_before_a_process(fw):
    g = api(fw)
    ogg_rig(fw)
    fw.mock('toolsGetExternCover', lambda *_: 1)
    assert g.coverflow_album_cover(b'/m/a.ogg', b'/d', 1, 1) == 1 and not fw.calls('fw_fork')


class Listing:
    def __init__(self, fw, names, d_type=8):
        self.entries = [Dirent(0, 0, 0, d_type, n) for n in names]
        self.at, self.opens = 0, []

        def opendir(path, *_):
            self.opens.append(C.string_at(path))
            self.at = 0
            return 0x2000
        fw.mock('fw_opendir', opendir)
        fw.mock('fw_readdir', self.read)

    def read(self, *_):
        self.at += 1
        return C.addressof(self.entries[self.at - 1]) if self.at <= len(self.entries) else 0


def folder_rig(fw, names, art_for=()):
    Card(fw)
    tried = []

    def cover(t, *_):
        tried.append(C.string_at(t))
        return C.string_at(t) in art_for
    fw.mock('toolsGetAlbumCover', cover)
    return Listing(fw, names), tried


def test_folder_cover_tries_the_first_song_by_name_first(fw):
    g = api(fw)
    _, tried = folder_rig(fw, [b'03.mp3', b'01.mp3', b'02.mp3'], art_for=(b'/m/01.mp3',))
    assert g.coverflow_folder_cover(b'/m/03.mp3', b'/d', 1, 1) == 1
    assert tried == [b'/m/01.mp3']


def test_folder_cover_skips_dot_files_folders_and_other_kinds(fw):
    g = api(fw)
    _, tried = folder_rig(fw, [b'._01.mp3', b'00.flac', b'02.mp3'], art_for=(b'/m/02.mp3',))
    assert g.coverflow_folder_cover(b'/m/05.mp3', b'/d', 1, 1) == 1 and tried == [b'/m/02.mp3']


def test_folder_cover_the_first_song_with_no_art_falls_to_stock_s_walk_and_stays_bare(fw):
    g = api(fw)
    listing, tried = folder_rig(fw, [b'01.mp3', b'02.mp3'], art_for=(b'/m/03.mp3',))
    assert g.coverflow_folder_cover(b'/m/02.mp3', b'/d', 1, 1) == 0
    assert tried == [b'/m/01.mp3', b'/m/02.mp3']
    tried.clear()
    assert g.coverflow_folder_cover(b'/m/03.mp3', b'/d', 1, 1) == 1
    assert tried == [b'/m/03.mp3'] and len(listing.opens) == 1  # the same walk: no second listing


def test_folder_cover_the_first_song_itself_is_not_tried_twice(fw):
    g = api(fw)
    _, tried = folder_rig(fw, [b'01.mp3'])
    assert g.coverflow_folder_cover(b'/m/01.mp3', b'/d', 1, 1) == 0 and tried == [b'/m/01.mp3']


def test_folder_cover_a_hit_ends_the_walk_and_the_next_lists_again(fw):
    g = api(fw)
    listing, _ = folder_rig(fw, [b'01.mp3'], art_for=(b'/m/01.mp3',))
    g.coverflow_folder_cover(b'/m/02.mp3', b'/d', 1, 1)
    g.coverflow_folder_cover(b'/m/03.mp3', b'/d', 1, 1)
    assert len(listing.opens) == 2


def test_folder_cover_another_folder_lists_again(fw):
    g = api(fw)
    listing, _ = folder_rig(fw, [b'01.mp3'])
    g.coverflow_folder_cover(b'/m/a/02.mp3', b'/d', 1, 1)
    g.coverflow_folder_cover(b'/m/ab/02.mp3', b'/d', 1, 1)
    assert listing.opens == [b'/m/a', b'/m/ab']


@pytest.mark.parametrize('track', (b'noslash.mp3', b'/m/noext', b'/m.d/noext', b'/' + b'x' * 600 + b'/a.mp3'))
def test_folder_cover_what_it_can_t_list_is_stock_s_song_alone(fw, track):
    g = api(fw)
    listing, tried = folder_rig(fw, [b'01.mp3'])
    g.coverflow_folder_cover(track, b'/d', 1, 1)
    assert tried == [track] and not listing.opens


def player_rig(fw, track=b'/m/a.mp3', id3_path=b'/m/a.mp3', artist=b'Art', album=b'Alb'):
    buf = fw.var('fw_ctx_g_play_id3_info', C.c_char * 2716)
    C.memmove(C.addressof(buf), id3_path + b'\0', len(id3_path) + 1)
    C.memmove(C.addressof(buf) + 0x504, artist + b'\0', len(artist) + 1)
    C.memmove(C.addressof(buf) + 0x404, album + b'\0', len(album) + 1)


def tags_key(g, artist, album):
    return g.fnv(g.fnv(FNV, artist), album)


def test_marked_empty_is_the_cache_s_empty_file_of_the_parsed_track(fw):
    g = api(fw)
    player_rig(fw)
    card = Card(fw, {art(tags_key(g, b'Art', b'Alb')): b''})
    assert g.marked_empty(b'/m/a.mp3')
    card.files[art(tags_key(g, b'Art', b'Alb'))] = b'jpeg'
    assert not g.marked_empty(b'/m/a.mp3')
    del card.files[art(tags_key(g, b'Art', b'Alb'))]
    assert not g.marked_empty(b'/m/a.mp3')


def test_marked_empty_another_track_says_nothing(fw):
    g = api(fw)
    player_rig(fw, id3_path=b'/m/other.mp3')
    Card(fw, {art(tags_key(g, b'Art', b'Alb')): b''})
    assert not g.marked_empty(b'/m/a.mp3')


def test_player_cover_stock_s_external_first_then_ffmpeg_unless_marked_empty(fw):
    g = api(fw)
    player_rig(fw)
    card = Card(fw, {})
    fw.mock('fw_fork', lambda *_: -1)
    fw.mock('toolsGetExternCover', lambda *_: 1)
    assert g.coverflow_player_cover(b'/m/a.mp3', b'/d', 1, 1) == 1 and not fw.calls('fw_fork')
    fw.mock('toolsGetExternCover', lambda *_: 0)
    assert g.coverflow_player_cover(b'/m/a.mp3', b'/d', 1, 1) == 0 and len(fw.calls('fw_fork')) == 1
    card.files[art(tags_key(g, b'Art', b'Alb'))] = b''
    assert g.coverflow_player_cover(b'/m/a.mp3', b'/d', 1, 1) == 0 and len(fw.calls('fw_fork')) == 1  # no process


def test_now_cover_needs_the_player_to_have_parsed_this_track(fw):
    g = api(fw)
    url = fw.var('fw_ctx_g_lastcover_url', C.c_char * 1024)
    C.memmove(C.addressof(url), b'/m/a.mp3\0', 9)
    fw.var('g_playcover_type', C.c_ubyte).value = 2
    out = C.c_char_p()
    rec = Rec('A', '/m/a.mp3')
    assert g.now_cover(rec.p, r(out)) == 2 and (out.value or b'').endswith(b'/tmp/externpic.jpg')
    assert g.now_cover(Rec('A', '/m/b.mp3').p, r(out)) == 0 and out.value is None
    assert g.now_cover(None, r(out)) == 0


@pytest.mark.parametrize('kind, name', ((1, b'coverpic.jpg'), (2, b'externpic.jpg'), (4, b'externpic.jpg'), (3, None), (0, None), (5, None), (9, None)))
def test_now_cover_each_type_s_file(fw, kind, name):
    g = api(fw)
    url = fw.var('fw_ctx_g_lastcover_url', C.c_char * 1024)
    C.memmove(C.addressof(url), b'/m/a.mp3\0', 9)
    fw.var('g_playcover_type', C.c_ubyte).value = kind
    out = C.c_char_p()
    assert g.now_cover(Rec('A', '/m/a.mp3').p, r(out)) == kind
    assert (out.value.rsplit(b'/')[-1] if out.value else None) == name


SORT_FILE = '/mnt/data/ringnav-coversort'


def int_bytes(v):
    return v.to_bytes(4, 'little', signed=True)


@pytest.mark.parametrize('sort, name', ((0, b'Sort: Album'), (1, b'Sort: Artist'), (2, b'Sort: Recently Added'), (3, b'Sort: Most Played'),
                                         (4, b'Sort: Random'), (6, b'Sort: Year')))
def test_the_library_s_sort_reads_back(fw, sort, name):
    g = api(fw)
    Card(fw, {SORT_FILE: int_bytes(sort)})
    assert g.sort_saved() == sort and g.coverflow_sort_name() == name


@pytest.mark.parametrize('data', (None, b'', b'\x01', int_bytes(5), int_bytes(7), int_bytes(-1), int_bytes(2**31 - 1)))
def test_a_bad_sort_file_is_album(fw, data):
    g = api(fw)
    Card(fw, {} if data is None else {SORT_FILE: data})
    assert g.sort_saved() == SORT_ALBUM and g.coverflow_sort_name() == b'Sort: Album'


def test_sort_file_hostile_sweep(fw):
    g, rng = api(fw), random.Random(8)
    card = Card(fw)
    for _ in range(2000):
        data = rng.randbytes(rng.choice((0, 1, 3, 4, 4, 4, 5)))
        card.files = {SORT_FILE: data}
        card.open = {}
        want = int.from_bytes(data[:4], 'little') if len(data) >= 4 else 0
        assert g.sort_saved() == (want if want in (0, 1, 2, 3, 4, 6) else 0)


def test_sort_picker_offers_the_library_s_six_orders_and_saves_a_pick(fw):
    g = api(fw)
    card = Card(fw, {SORT_FILE: int_bytes(SORT_YEAR)})
    assert g.coverflow_sort_count() == 6 and g.coverflow_sort_now() == 2
    assert [g.coverflow_sort_at(k) for k in range(6)] == [b'Album', b'Artist', b'Year', b'Recently Added',
                                                          b'Most Played', b'Random']
    seen = []
    for k in range(6):
        g.coverflow_sort_set(k)
        seen.append(int.from_bytes(card.files[SORT_FILE], 'little'))
        assert g.coverflow_sort_now() == k
    assert seen == [SORT_ALBUM, SORT_ARTIST, SORT_YEAR, SORT_ADDED, SORT_PLAYED, SORT_RANDOM]
    assert cf(fw).resort == 1


def test_sort_next_in_the_page_s_own_orders(fw):
    g = api(fw)
    c = cf(fw)
    c.sort = SORT_YEAR
    assert g.sort_next(1) == SORT_ADDED and g.sort_next(0) == SORT_YEAR
    c.sort = SORT_RATING  # not one of the library's
    assert g.sort_next(1) == SORT_ALBUM
    c.scoped = RATED_ALBUMS
    assert g.sort_next(0) == SORT_RATING and g.sort_next(1) == SORT_ALBUM
    c.sort = SORT_PLAYED
    assert g.sort_next(1) == SORT_RATING  # the rated orders wrap
    c.sort = SORT_RANDOM  # not a rated order
    assert g.sort_next(1) == SORT_RATING


def test_the_library_generation_moves_before_and_after_a_scan(fw):
    gen = fw.var('library_gen', C.c_uint)
    during = []
    fw.mock('stock_scan_all_trampoline', lambda *_: during.append(gen.value))
    fw.fn('coverflow_scan_all', C.c_int, C.c_void_p, C.c_void_p)(None, None)
    assert during == [1] and gen.value == 2


# Stock's songtable, as demo's CREATE TABLE text has it: VARCHAR columns, so text affinity, which LIKE
# needs to use a NOCASE index.
SONGTABLE = ('create table songtable(id INTEGER PRIMARY KEY AUTOINCREMENT, filename VARCHAR(512), fileurl VARCHAR(1024), '
             'album VARCHAR(512), songer VARCHAR(256), style VARCHAR(128), name VARCHAR(512), composer VARCHAR(256), '
             'albumsonger VARCHAR(256), year INTEGER, track INTEGER, cd_num INTEGER, time INTEGER, time_end INTEGER, '
             'time_create INTEGER, Initial INTEGER, bitdepth INTEGER, samprate INTEGER)')


@pytest.mark.parametrize('scan', ['scan_all', 'scan_folder'])
def test_a_scan_ends_by_indexing_what_stock_looks_up_alone(fw, scan):
    """An album's songs, Recently Added and a file's row read the whole table without an index. Update
    Local Music drops the table, so the indexes come after stock's own writes."""
    made, during = [], []
    fw.mock('toolsUpdateDbTable', lambda db, sql, *_: made.append((C.string_at(db), C.string_at(sql).decode())) or 1)
    fw.mock(f'stock_{scan}_trampoline', lambda *_: during.append(len(made)))
    fw.fn(f'coverflow_{scan}', C.c_int, C.c_void_p, C.c_void_p)(None, None)
    assert during == [0] and [db for db, _ in made] == [b'/mnt/data/database.db']
    sent = []
    fw.mock('stock_dbquery_trampoline', lambda db, sql, *_: sent.append(C.string_at(sql).decode()))
    query = fw.fn('ringnav_library_query', C.c_int, C.c_char_p, C.c_char_p, C.c_void_p, C.c_int)
    music = fw.var('navigation__library_music', C.c_int)
    for staged in (0, 1):  # staged_music's subquery too
        music.value = staged
        for sql in (b"select * from songtable where album like 'Abbey Road'",  # getMusicByAlbum
                    b"select * from songtable where album like 'Abbey Road' and albumsonger like 'The Beatles'",
                    b'select * from songtable ORDER BY time_create desc limit 100;'):  # getAllRecentAddRecord
            query(b'/mnt/data/database.db', sql, None, 0)
    assert len(sent) == 6
    row = "filename='a.flac' and fileurl='/mnt/mmc/a.flac' and time=0 and time_end=0"
    sent += [f'select * from songtable where {row}', f'delete from songtable where {row}',  # toolsCheckItemExist, toolsDeleteDbTable
             f"update songtable set album='b' where {row}"]  # ringnav_scan_refresh
    bare, indexed = sqlite3.connect(':memory:'), sqlite3.connect(':memory:')
    for db in bare, indexed:
        db.execute(SONGTABLE)
    indexed.executescript(made[0][1])
    plan = lambda db, sql: ' '.join(row[-1] for row in db.execute('explain query plan ' + sql))  # noqa: E731
    for sql in sent:
        assert 'INDEX' not in plan(bare, sql) and 'USING INDEX' in plan(indexed, sql), sql
    # and the same rows in the same order: songs of one second (a CUE image's tracks) stay in rowid order
    songs = [(i, f'{i}.flac', f'/mnt/mmc/{i}.flac', ('Abbey Road', 'abbey road')[i % 2], 'The Beatles', i // 3)
             for i in range(1, 10)]
    for db in bare, indexed:
        db.executemany('insert into songtable(id, filename, fileurl, album, songer, time_create) values (?, ?, ?, ?, ?, ?)', songs)
    for sql in sent[:6]:
        assert bare.execute(sql).fetchall() == indexed.execute(sql).fetchall(), sql
    # oldest first would walk the index backwards and reverse one second's songs: the hook adds id, stock's order
    asc, sent[:] = 'select * from songtable ORDER BY time_create asc;', []
    for staged in (0, 1):
        music.value = staged
        query(b'/mnt/data/database.db', asc.encode(), None, 0)
    assert len(sent) == 2
    for sql in sent:
        assert sql.endswith(' ORDER BY time_create asc, id') and indexed.execute(sql).fetchall() == bare.execute(asc).fetchall(), sql


@pytest.mark.parametrize('result, mask, btime, want', ((0, 0x800, 1700000000, 1700000000), (0, 0xfff, 1234, 1234),
                                                       (-1, 0x800, 1700000000, 99), (0, 0x7ff, 1700000000, 99), (0, 0x800, 0, 99)))
def test_a_song_is_dated_by_its_creation_stamp_where_the_card_keeps_one(fw, result, mask, btime, want):
    """statx's stx_btime (word 20) when its mask says it holds one, else stock's own date (#170, #188)."""
    asked = []

    def statx(dirfd, path, flags, m, buf, *_):
        asked.append((C.c_int(dirfd).value, C.string_at(path), flags, m))
        sx = (C.c_uint * 64).from_address(buf)
        sx[0], sx[20] = mask, btime
        return result
    fw.mock('ringnav_statx', statx)
    fw.mock('stock_file_created_trampoline', lambda path, *_: 99)
    assert fw.fn('ringnav_file_created', C.c_int, C.c_char_p)(b'/mnt/mmc/a.flac') == want
    assert asked == [(-100, b'/mnt/mmc/a.flac', 0, 0x800)]  # AT_FDCWD, STATX_BTIME


def xiph_h(name):
    """A size patch/xiph.h defines: a number, or a number shifted."""
    m = re.search(rf'^#define {name} \(?(\d+)(?: << (\d+))?\)?', (ROOT/'patch/xiph.h').read_text(), re.M)
    assert m, name
    return int(m[1]) << int(m[2] or 0)


OGG_TAGS_MAX = xiph_h('OGG_TAGS_MAX')


def ogg(size):
    """An Ogg Vorbis file's first two packets, the comment header (packet 1, what OGG_TAGS_MAX measures) size bytes
    long with its '\\x03vorbis' start (no CRC: xiph.c reads none)."""
    pages, seq = [], 0
    for packet in (b'\x01vorbis' + bytes(23), b'\x03vorbis' + bytes(size - 7)):
        lacing = [255] * (len(packet) // 255) + [len(packet) % 255]
        for at in range(0, len(lacing), 255):
            part = lacing[at:at + 255]
            pages.append(b'OggS\0' + bytes([at > 0]) + bytes(8) + struct.pack('<III', 1, seq, 0) + bytes([len(part)]) +
                         bytes(part) + packet[at * 255:at * 255 + sum(part)])
            seq += 1
    return b''.join(pages)


@pytest.mark.parametrize('track, size, stock', ((b'/m/a.ogg', OGG_TAGS_MAX + 1, False), (b'/m/a.OGG', OGG_TAGS_MAX + 1, False),
                                                (b'/m/a.ogg', OGG_TAGS_MAX, True), (b'/m/a.ogg', 1000, True),
                                                (b'/m/a.opus', OGG_TAGS_MAX + 1, True), (b'/m/a', OGG_TAGS_MAX + 1, True)))
def test_stock_reads_no_picture_out_of_an_ogg_comment_header_over_the_cap(fw, track, size, stock):
    """TagLib's whole parse of one over OGG_TAGS_MAX would get demo killed, a byte over it as surely as more; one at
    the cap is still stock's. Stock reads no .opus picture at all."""
    files = Files(fw, {track.decode(): ogg(size)})
    fw.mock('stock_album_cover_trampoline', lambda *_: 1)
    got = fw.fn('ringnav_album_cover', C.c_int, C.c_char_p, C.c_char_p, C.c_int, C.c_int)(track, b'/tmp/.tmp_picture', 160, 150)
    assert got == stock and not files.open
    calls = [(C.string_at(t), C.string_at(d), w, h) for t, d, w, h, *_ in (args for _, args in fw.calls('stock_album_cover_trampoline'))]
    assert calls == ([(track, b'/tmp/.tmp_picture', 160, 150)] if stock else [])


@pytest.mark.parametrize('writer, stock, walks', (('coverflow_scan_folder', 'stock_scan_folder_trampoline', True),
                                                  ('coverflow_delete_song', 'stock_delete_song_trampoline', False)))
def test_each_songtable_writer_moves_the_generation_round_stock_s_write(fw, writer, stock, walks):
    """A list queried while one runs (an odd generation) is never kept; a scan first walks the card for folder markers."""
    gen = fw.var('library_gen', C.c_uint)
    during = []
    fw.mock(stock, lambda a, b, *_: during.append((gen.value, a, b)) or 7)
    assert fw.fn(writer, C.c_int, C.c_void_p, C.c_void_p)(0x11, 0x22) == 7  # stock's answer, passed on
    assert during == [(1, 0x11, 0x22)] and gen.value == 2
    names = [name for name, _ in fw.calls()]
    assert ('fw_fopen' in names[:names.index(stock)]) == walks  # the marker walk writes its rules first


# What navigation.c asks of the open page

def py_key(artist, album):
    """album_key: FNV-1a of the artist, then of the album, each closed by a multiply (fnv's separator)."""
    h = FNV
    for s in (artist, album):
        for b in s:
            h = (h ^ b) * 16777619 % 2**32
        h = h * 16777619 % 2**32
    return h


FX_POOL = int(host.tools_build.need(re.search(r'^#define FX_POOL (\d+)', (ROOT/'patch/coverflow.h').read_text(), re.M), 'No FX_POOL')[1])
LD_SLOTS = int(host.tools_build.need(re.search(r'^#define LD_SLOTS (\d+)', (ROOT/'patch/coverflow.h').read_text(), re.M), 'No LD_SLOTS')[1])


class Ld(C.Structure):
    _fields_ = [('key', C.c_uint), *((k, C.c_int) for k in 'album rank state ok'.split())]


class Fx(C.Structure):
    _fields_ = [('frame', C.c_void_p), ('tex', C.c_void_p), ('album', C.c_int * FX_POOL), ('art', C.c_void_p * FX_POOL),
                ('half', C.c_ubyte * FX_POOL),
                *((k, C.c_int) for k in 'c frac drawn light lead arch todo mode letters_at next prev halves'.split()),
                ('half_at', C.c_uint), ('half_ms', C.c_int), ('full_ms', C.c_int),
                ('ld', Ld * LD_SLOTS), ('stage', C.c_void_p), ('gate', C.c_uint * 8), ('loader', C.c_ulong),
                ('ld_on', C.c_int), ('ld_stop', C.c_int), ('paint_at', C.c_uint), ('more', C.c_void_p), ('no_more', C.c_int)]


def deque_of(fw, recs):
    fw.mock('fw_deque_size', lambda *_: len(recs))
    fw.mock('fw_deque_at', lambda d, i, *_: recs[i].p if i < len(recs) else 0)


def test_the_bar_names_an_album_s_tracks_under_song_rows_and_no_covers(fw):
    """OLED's one-line song rows take the album's name from the tracks' hidden caption (0: read it); over the covers
    the status row has no title, so it shows the clock as Home's does."""
    themed(fw)
    recs = [Rec(artist='Bob Dylan', path='/a')]
    deque_of(fw, recs)
    title = fw.fn('coverflow_bar_title', C.c_char_p)
    c = cf(fw)
    c.albums, c.scoped, c.screen = 0x5000, RATED_ALBUMS, TRACKS
    assert title() is None
    c.screen = COVERS
    assert title() == b''
    c.scoped = 0
    assert title() == b''


def test_the_live_album_is_the_centre_card_while_the_covers_show(fw):
    s = Shelf(fw, n=3, index=1, scoped=RATED_ALBUMS)  # the rated albums: two, then their Sort card
    recs = [Rec('A'), Rec('B')]
    deque_of(fw, recs)
    c = cf(fw)
    c.page, c.albums = 0x7000, 0x5000
    album = fw.fn('coverflow_album', C.c_void_p, C.c_void_p)
    assert album(0x7000) == recs[1].p
    assert album(0x7001) is None  # another page's
    s.set(0x78, 2)
    assert album(0x7000) is None  # the Sort card is no album
    s.set(0x78, 0)
    assert album(0x7000) == recs[0].p
    c.timer = 9
    assert album(0x7000) is None  # a screen change is pending
    c.timer, c.screen = 0, TRACKS
    assert album(0x7000) is None
    c.screen, c.albums = COVERS, 0
    assert album(0x7000) is None


def test_the_track_list_and_its_scope_are_the_open_album_s(fw):
    recs = [Rec('Abbey', artist='Beatles'), Rec('Help', artist='Beatles')]
    deque_of(fw, recs)
    c = cf(fw)
    c.page, c.albums, c.tracks, c.album, c.screen = 0x7000, 0x5000, 0x5100, 1, TRACKS
    tracks, scope = fw.fn('coverflow_tracks', C.c_void_p, C.c_void_p), fw.fn('coverflow_scope', C.c_uint, C.c_void_p)
    assert tracks(0x7000) == 0x5100 and scope(0x7000) == py_key(b'Beatles', b'Help')
    c.album = 0
    assert scope(0x7000) == py_key(b'Beatles', b'Abbey')  # each album remembers its own track
    assert tracks(0x7001) is None and scope(0x7001) == 0
    c.screen = COVERS
    assert tracks(0x7000) is None and scope(0x7000) == 0


def test_which_slide_menu_is_the_covers_and_which_stops_at_its_ends(fw):
    c = cf(fw)
    slide, bounded = fw.fn('coverflow_slide', C.c_int, C.c_void_p), fw.fn('coverflow_bounded', C.c_int, C.c_void_p)
    assert not slide(None) and not bounded(None)
    c.slide = 0x6000
    assert slide(0x6000) and not slide(0x6001) and not bounded(0x6000)  # the library's wrap round
    c.scoped = PLAYLISTS
    assert bounded(0x6000) and not bounded(0x6001)
    c.scoped = RATED_ALBUMS  # the rated albums wrap like the library's
    assert not bounded(0x6000)


def test_folder_cover_a_first_song_too_long_to_name_is_stock_s_song_alone(fw):
    g = api(fw)
    folder = b'/' + b'd' * 300
    first = b'a' * 246 + b'.mp3'  # fits the listing's 256, not the folder's 512 with it
    listing, tried = folder_rig(fw, [first])
    assert g.coverflow_folder_cover(folder + b'/z.mp3', b'/d', 1, 1) == 0
    assert listing.opens == [folder] and tried == [folder + b'/z.mp3']


class Query:
    """toolsQueryDbTable over rows (id, album, artist, track, album artist), each through the C's own callback as stock's
    sqlite3_exec runs it; album_row, stock's row maker, puts a record of the first four columns on the staging deque."""
    ROW = C.CFUNCTYPE(C.c_int, C.c_void_p, C.c_int, C.c_void_p, C.c_void_p)

    def __init__(self, fw, q, rows):
        self.made, self.sql = [], []
        staging = fw.var('fw_ctx_tools_pdeq_directory', C.c_void_p)

        def query(db, sql, cb, sort, *_):
            assert C.string_at(db) == b'/mnt/data/database.db' and not sort
            self.sql.append(C.string_at(sql).decode())
            for row in rows:
                v = (C.c_char_p * 5)(*[None if x is None else str(x).encode() for x in row])
                assert Query.ROW(cb)(0, 5, C.addressof(v), 0) == 0
            return len(rows)

        def album_row(a, n, v, c, *_):
            assert n == 4  # the album artist is the callback's own
            cols = (C.c_char_p * 4).from_address(v)
            self.made.append(Rec(cols[1], cols[3], id=int(cols[0] or 0)))
            q[staging.value].append(self.made[-1].p)
        fw.mock('toolsQueryDbTable', query)
        fw.mock('album_row', album_row)


def field(p, off):
    s = C.c_void_p.from_address(p + off).value
    return None if s is None else C.string_at(s).decode()


def test_split_makes_a_name_s_row_one_card_per_album_in_its_place(fw):
    """getAllAlbum groups by name alone; ALBUM_SQL's rows, the fifth column the album artist, tell its albums apart."""
    q = Deques(fw)
    hits, abbey, lost, unknown = Rec('Hits', '/m/H1/1.flac', id=1), Rec('Abbey', '/m/A/1.flac', id=3), \
        Rec('Lost', '/m/L/1.flac', id=9), Rec('Unknown Album', id=-1)
    listed = q.new([hits.p, abbey.p, lost.p, unknown.p])
    staging = fw.var('fw_ctx_tools_pdeq_directory', C.c_void_p)
    staging.value = 0x4321
    query = Query(fw, q, [(1, 'Hits', 'A', '/m/H1/1.flac', 'Various'), (2, 'hits', 'A', '/m/H2/1.flac', 'Other'),
                          (4, 'Hits', 'A', '/m/H3/1.flac', 'various'), (3, 'Abbey', 'B', '/m/A/1.flac', ''),
                          (5, 'Zed', 'Z', '/m/Z/1.flac', None)])
    assert fw.fn('coverflow_split', C.c_int, C.c_void_p)(listed) == 5
    got = [(field(p, 0x14), field(p, 0x24)) for p in q[listed]]
    assert got == [('Hits', 'Various'), ('hits', 'Other'), ('Abbey', ''), ('Lost', None), ('Unknown Album', None)]
    assert q[listed][2] == query.made[3].p and q[listed][3] == lost.p  # the query's row; a name it missed stays
    assert query.sql[0].startswith('select id,album,songer,') and ' order by ' not in query.sql[0]
    assert staging.value == 0x4321  # the staging pointer leaves no trace
    assert len(q.lists) == 1  # the copy, the query's rows and the split all gone


def test_album_only_keeps_the_album_s_tracks_in_order_and_where_the_track_went(fw):
    q = Deques(fw)
    recs = [Rec('A', '/m/A/1.flac'), Rec('B', '/m/B/1.flac'), Rec('a', '/m/A/2.flac'), Rec('A', '/m/X/1.flac'),
            Rec('A', '/m/A/3.flac')]
    listed = q.new([r.p for r in recs])
    only = fw.fn('album_only', C.c_int, C.c_void_p, C.c_void_p, C.c_int)
    assert only(listed, recs[2].p, 2) == 1
    assert q[listed] == [recs[0].p, recs[2].p, recs[4].p]  # a same-named album in another folder is another album
    other = q.new([recs[1].p, recs[3].p])
    assert only(other, recs[0].p, 1) == 1 and q[other] == [recs[1].p, recs[3].p]  # none of it: untouched
    assert len(q.lists) == 2


def test_album_only_keeps_the_unknown_album_card_s_songs_and_the_resumed_one(fw):
    """Stock's resume finds every untagged song (getMusicByAlbum(NULL), without MUSIC_WHERE): the card's are kept,
    spoken and scoped-out ones are not, unless one is what was playing."""
    Files(fw, {'/mnt/data/q2-library-scope': b'0/mnt/mmc/Hidden\n'})
    fw.mock('tk_str_start_with', lambda s, prefix, *_: C.string_at(s).startswith(C.string_at(prefix)))
    q = Deques(fw)
    recs = [Rec(path='/mnt/mmc/Loose A/x.flac'), Rec(path='/mnt/mmc/Podcasts/Show/1.mp3'),
            Rec(path='/mnt/mmc/Hidden/h.flac'), Rec(path='/mnt/mmc/Loose B/y.flac'), Rec(path='/mnt/mmc/Loose/b.m4b'),
            Rec(path='/mnt/mmc/audiobooks/Book/1.mp3'), Rec('A', '/mnt/mmc/A/1.flac')]
    only = fw.fn('album_only', C.c_int, C.c_void_p, C.c_void_p, C.c_int)
    listed = q.new([r.p for r in recs])
    assert only(listed, recs[3].p, 3) == 1 and q[listed] == [recs[0].p, recs[3].p]
    for i, kept in ((1, [0, 1, 3]), (2, [0, 2, 3]), (4, [0, 3, 4])):  # what was playing stays, whatever the card shows
        listed = q.new([r.p for r in recs])
        assert only(listed, recs[i].p, i) == kept.index(i) and q[listed] == [recs[k].p for k in kept]


@pytest.fixture(scope='module')
def style(library):
    """cf_style_t's field offsets in this build."""
    return host.offsets(library.parent/'coverflow.o', 'cf_style_t')


def test_a_far_cover_s_pose_holds_for_a_wide_spacing(fw, style):
    """xs and zs as far as the theme's range takes them: a cover 22 out (entry_window's) stays exact, no wrap."""
    g = api(fw)
    cfs = C.addressof(fw.var('cfs', C.c_char))
    x1, z1 = (C.c_short.from_address(cfs + style[f]).value for f in ('x1', 'z1'))
    for f in ('xs', 'zs'):
        C.c_short.from_address(cfs + style[f]).value = 799
    p = pose(g, 22 * ONE)
    assert (p.xc, p.zc) == (16 * (x1 + 21 * 799), 16 * (z1 + 21 * 799))


def test_without_a_third_cover_the_second_fades_out_over_the_next_step(fw, style):
    g = api(fw)
    third = C.c_short.from_address(C.addressof(fw.var('cfs', C.c_char)) + style['third'])
    second = pose(g, 2 * ONE).bright
    assert third.value and pose(g, 3 * ONE).bright == second  # the theme's: the third stays
    third.value = 0
    assert pose(g, 2 * ONE).bright == second
    assert pose(g, 5 * ONE // 2).bright == second // 2
    assert pose(g, 3 * ONE).bright == 0 and pose(g, 4 * ONE).bright == 0
    last = second
    for t in range(2 * ONE, 3 * ONE, 999):
        b = pose(g, t).bright
        assert b <= last
        last = b


@pytest.mark.parametrize('scoped', (PLAYLISTS, 0))
def test_a_bounded_shelf_shows_no_cover_past_its_ends(fw, scoped):
    t = themed(fw)
    frame = C.create_string_buffer(0x40)
    fw.mock('bitmap_create_ex', lambda *_: C.addressof(frame))
    fw.mock('widget_load_image', lambda *_: 1)  # nothing decodes: the placeholder
    n = 3
    deque_of(fw, [Rec() for _ in range(n)])
    c = cf(fw)
    c.albums, c.slide, c.scoped = 0x5000, 0x6000, scoped
    fw.fn('cf_restyle', None)()
    assert fw.fn('coverflow__fx_open', C.c_int)() == 1
    wide = (C.c_void_p * (2 * CF_WIDE_REACH + 1))()  # the callers' window, ring[0] CF_REACH slots left of its centre
    ring = C.addressof(wide) + (CF_WIDE_REACH - CF_REACH) * C.sizeof(C.c_void_p)
    fw.fn('coverflow__fx_ring', None, C.c_int, C.c_int, C.c_void_p)(0, n, ring)
    reach = CF_WIDE_REACH if t['cf.thumb'] else CF_REACH  # a thumb theme's row reaches further
    js = range(-CF_WIDE_REACH, CF_WIDE_REACH + 1)  # each slot's place from the centre album, the first
    # the playlists' shelf: its three covers alone; the library's small shelf repeats round its wrap
    assert [int(bool(p)) for p in wide] == [int(abs(j) <= reach and (not scoped or 0 <= j < n)) for j in js]
    fw.fn('coverflow__fx_close', None)()


def test_a_tap_in_the_flat_fallback_opens_the_centre_cover_alone(fw):
    s = Shelf(fw, n=4, index=1, scoped=0)
    deque_of(fw, [Rec(), Rec(), Rec()])
    c = cf(fw)
    c.albums = 0x5000
    timers = []
    fw.mock('timer_add', lambda fn, ctx, ms, *_: timers.append((fn, ms)) or 5)

    def tap(x, y):
        c.timer = 0
        timers.clear()
        s.g.touch(None, event(DOWN, x, y, t=1))
        s.g.touch(None, event(UP, x, y, t=2))
        return timers
    opens = [(C.cast(fw.lib.coverflow__to_tracks, C.c_void_p).value, 0)]
    assert tap(CX, 50) == opens and c.album == 1 and c.timer == 5
    assert tap(CX - 79, 0) == opens and tap(CX + 79, ART - 1) == opens
    assert not tap(CX + 80, 50) and not tap(CX, ART) and not tap(CX - 120, 50)  # no side cover to aim at
    s.set(0x94, 20)  # moving: the centre cover settles rather than opens
    assert not tap(CX, 50) and s.goals == [0]


# Stock's widgets, for the pages and Home

THEMES = host.tools_theme.builtins()
OLED = THEMES[0]['index']  # the one theme, stored as THEME 3 (navigation.c theme_boot)
EVT_CLICK, EVT_WINDOW_FOREGROUND = O['EVT_CLICK'], O['EVT_WINDOW_FOREGROUND']
PANE_NOW, PANE_LIBRARY = range(2)  # what Split's pane shows: coverflow.c's


def themed(fw, **keys):
    """config.ini's [IPOD] keys as navigation.c reads them at its first theme(): THEME as OLED stores it, and any
    others."""
    values = {b'THEME': b'3', **{k.encode(): v.encode() for k, v in keys.items()}}

    def read(path, section, key, out, default, *_):
        value = values.get(C.string_at(key), C.string_at(default))
        C.memmove(out, value + b'\0', len(value) + 1)
    fw.mock('toolsReadConfig', read)
    fw.mock('fw___xstat', lambda *_: -1)  # no Rockbox on the card
    fw.mock('tk_str_start_with', lambda s, prefix, *_: C.string_at(s).startswith(C.string_at(prefix)))
    return THEMES[0]


def address(fw, name):
    return C.cast(getattr(fw.lib, name), C.c_void_p).value


def rect(p):
    return tuple((C.c_int * 4).from_address(p))


class Widget:
    def __init__(self, kind, parent, name=''):
        self.type, self.parent, self.name, self.kids = kind, parent, name, []
        self.text, self.image, self.style, self.visible, self.sensitive = None, '', '', 1, 1
        self.props, self.on = {}, []


class Ui:
    """Stock's widgets: each a zeroed block of the C's own heap (its box at W_X to W_H, its parent at W_PARENT, a
    slide_menu's fields), its tree, name, text, image, properties and handlers here; the timers, the window manager's
    windows and top one, what was invalidated and the animators made."""
    HANDLER = C.CFUNCTYPE(C.c_int, C.c_void_p, C.c_void_p)
    TIMER = C.CFUNCTYPE(C.c_int, C.c_void_p)

    def __init__(self, fw):
        self.fw, self.w, self.cstrs, self.keep, self.timers, self.dirty, self.animators = fw, {}, {}, [], {}, [], []
        self.calloc = fw.fn('calloc', C.c_void_p, C.c_size_t, C.c_size_t)
        self.top = self.animating = self.ids = 0
        self.animate = True
        self.wm = self.make(0, 'window_manager')
        for name, kind in (('window_create', 'window'), ('image_create', 'image'), ('list_view_create', 'list_view'),
                           ('scroll_view_create', 'scroll_view'), ('list_item_create', 'list_item'),
                           ('hscroll_label_create', 'hscroll_label')):
            fw.mock(name, lambda parent, x, y, w, h, *_, k=kind: self.make(parent, k, x, y, w, h))
        fw.mock('widget_factory', lambda *_: 1)
        fw.mock('widget_factory_create_widget', lambda f, kind, parent, x, y, w, *_: self.make(parent, self.str(kind), x, y, w))
        for name, prop in (('widget_set_name', 'name'), ('widget_use_style', 'style'), ('image_base_set_image', 'image')):
            fw.mock(name, lambda w, s, *_, p=prop: setattr(self[w], p, self.str(s)))
        fw.mock('widget_set_text_utf8', lambda w, s, *_: setattr(self[w], 'text', self.str(s) if s else None))
        fw.mock('widget_get_text', lambda w, *_: self.wide(self[w].text))
        fw.mock('widget_get_text_utf8', self.text_utf8)
        fw.mock('widget_set_visible', lambda w, v, *_: setattr(self[w], 'visible', v))
        fw.mock('widget_get_visible', lambda w, *_: self[w].visible)
        fw.mock('widget_set_sensitive', lambda w, v, *_: setattr(self[w], 'sensitive', v))
        fw.mock('widget_on', lambda w, evt, fn, ctx, *_: self[w].on.append((evt, fn, ctx)) or len(self[w].on))
        fw.mock('widget_destroy_children', lambda w, *_: self.drop(self[w].kids[:]))
        fw.mock('widget_destroy', lambda w, *_: self.drop([w]))
        fw.mock('widget_count_children', lambda w, *_: len(self[w].kids))
        fw.mock('widget_get_child', lambda w, i, *_: self[w].kids[i] if i < len(self[w].kids) else 0)
        fw.mock('widget_move_resize', lambda w, x, y, ww, h, *_: self.box(w, x, y, ww, h))
        fw.mock('widget_get_prop_str', self.prop_str)
        fw.mock('widget_set_prop_str', lambda w, k, v, *_: self[w].props.__setitem__(self.str(k), self.str(v)))
        fw.mock('widget_set_prop_int', lambda w, k, v, *_: self[w].props.__setitem__(self.str(k), C.c_int(v).value))
        fw.mock('widget_get_prop_int', lambda w, k, d, *_: self[w].props.get(self.str(k), C.c_int(d).value))
        fw.mock('widget_lookup', lambda w, name, *_: self.lookup(w, self.str(name)))
        fw.mock('widget_get_type', lambda w, *_: self.cstr(self[w].type))
        fw.mock('widget_restack', lambda w, i, *_: self.restack(w, i))
        fw.mock('widget_invalidate_force', lambda w, r, *_: self.dirty.append((w, rect(r) if r else None)))
        fw.mock('timer_add', self.add_timer)
        fw.mock('timer_remove', lambda i, *_: self.timers.pop(i, None) and 0)
        fw.mock('window_manager', lambda *_: self.wm)
        fw.mock('window_manager_get_top_window', lambda *_: self.top)
        fw.mock('window_manager_is_animating', lambda *_: self.animating)
        fw.mock('widget_animator_prop_create', self.animator)

    def __getitem__(self, w):
        return self.w[w] if w in self.w else self.w.setdefault(0, Widget('none', 0))

    @staticmethod
    def str(s):
        return C.string_at(s).decode() if s else ''

    def cstr(self, s):
        if s not in self.cstrs:
            self.cstrs[s] = C.create_string_buffer(s.encode())
        return C.addressof(self.cstrs[s])

    def wide(self, text):
        if text is None:
            return 0
        self.keep.append((C.c_uint * (len(text) + 1))(*map(ord, text)))
        return C.addressof(self.keep[-1])

    def text_utf8(self, w, out, size, *_):
        data = (self[w].text or '').encode()[:size - 1]
        C.memmove(out, data + b'\0', len(data) + 1)
        return 0

    def make(self, parent, kind, x=0, y=0, w=0, h=0, name=''):
        p = self.calloc(1, 0x200)
        self.w[p] = Widget(kind, parent, name)
        self.box(p, x, y, w, h)
        C.c_void_p.from_address(p + 0x48).value = parent or None  # W_PARENT
        if parent:
            self[parent].kids.append(p)
        return p

    def box(self, w, *xywh):
        if w in self.w:
            (C.c_int * 4).from_address(w)[:] = [C.c_int(v).value for v in xywh]

    def at(self, w):
        return rect(w)

    def drop(self, ws):
        for w in ws:
            if self[w].parent in self.w and w in self[self[w].parent].kids:
                self[self[w].parent].kids.remove(w)
            self.w.pop(w, None)

    def restack(self, w, i):
        kids = self[self[w].parent].kids
        kids.remove(w)
        kids.insert(i, w)

    def lookup(self, w, name):
        for k in self[w].kids if w else ():
            if self[k].name == name:
                return k
            found = self.lookup(k, name)
            if found:
                return found
        return 0

    def prop_str(self, w, k, d, *_):
        if not w or w not in self.w:
            return d  # stock: widget_get_prop fails on a NULL widget
        k, widget = self.str(k), self[w]
        # '' is a name, style or image never set
        value = (getattr(widget, k) or None) if k in ('name', 'style', 'image') else widget.props.get(k)
        value = stock_prop_str(widget.type.encode(), k.encode(), value.encode() if isinstance(value, str) else value, d)
        return 0 if value is None else self.cstr(value.decode()) if isinstance(value, bytes) else value

    def add_timer(self, fn, ctx, ms, *_):
        self.ids += 1
        self.timers[self.ids] = (fn, ctx, ms)
        return self.ids

    def timer(self, fn):
        """The timer running fn: (its id, ms), or None."""
        return next(((i, ms) for i, (f, _, ms) in self.timers.items() if f == fn), None)

    def fire(self, i):
        fn, ctx, ms = self.timers.pop(i)
        r = Ui.TIMER(fn)(ctx)
        if r == 8:  # RET_REPEAT
            self.timers[i] = (fn, ctx, ms)
        return r

    def animator(self, w, ms, delay, easing, prop, *_):
        if not self.animate:
            return 0
        self.animators.append((w, ms, easing, self.str(prop)))
        return self.calloc(1, 0x80)

    def handlers(self, w, evt=EVT_CLICK):
        return [(fn, ctx) for e, fn, ctx in self[w].on if e == evt]

    def click(self, w, evt=EVT_CLICK, event=0):
        return [Ui.HANDLER(fn)(ctx, event) for fn, ctx in self.handlers(w, evt)]

    def named(self, name):
        return next(w for w, x in self.w.items() if x.name == name)

    def of(self, kind, parent=None):
        return [w for w, x in self.w.items() if x.type == kind and (parent is None or x.parent == parent)]


class Homes:
    """A Home window as OLED's asset lays it out: the art, the main list's rows, the Settings list (hidden, two rows: a
    button with its label and icon), and stock's carousel (its slide_menu and arrows, hidden, as the asset keeps them).
    OLED's set has no home_lib, so no Library
    or Streaming list (tools/ipod.py ipod_home)."""
    ROWS = ('img_playing', 'img_library', 'img_cover_flow', 'img_folder', 'img_rockbox', 'img_shuffle_songs', 'img_games',
            'img_home_search', 'img_settings')

    def __init__(self, ui, carousel=False, rockbox=False):
        self.ui = ui
        self.win = ui.make(0, 'window', 0, 30, 375, 290)
        self.art = ui.make(self.win, 'image', 0, 0, 375, 290, name='img_homeart')
        self.main = ui.make(self.win, 'list_view', 0, 0, 375, 290, name='list_view_home')
        self.view = ui.make(self.main, 'scroll_view', 0, 0, 375, 290)
        self.rows = {name: ui.make(self.view, 'image', 0, 70 * i, 375, 70, name=name) for i, name in enumerate(self.ROWS)}
        if rockbox:
            ui[self.rows['img_rockbox']].name = 'btn_rockbox'
            ui.make(self.rows['img_rockbox'], 'label', name='label_rockbox')
        self.lists = {}
        for k in ('homeset',):
            self.lists[k] = ui.make(self.win, 'list_view', 0, 0, 375, 290, name='list_view_' + k)
            ui[self.lists[k]].visible = 0
            view = ui.make(self.lists[k], 'scroll_view', 0, 0, 375, 290)
            for i in range(2):
                row = ui.make(view, 'view', 0, 60 * i, 375, 60)
                b = ui.make(row, 'button', 10, 60 * i, 355, 60)
                ui.make(b, 'hscroll_label', 80, 10, 200, 40)
                ui[ui.make(b, 'image', 10, 10, 50, 50)].image = ('stream_radio', 'file:///mnt/mmc/apps/x/icon.png')[i]
        self.set_stream = ui.make(view, 'image', 0, 120, 375, 60, name='img_set_stream')  # Settings' Streaming
        self.sets, self.lib, self.stream = self.lists['homeset'], None, None
        self.carousel = []
        if carousel:
            self.carousel = [ui.make(self.win, 'slide_menu', name='slide_menu'), ui.make(self.win, 'image', name='img_left'),
                             ui.make(self.win, 'image', name='img_right')]
            for name in ('img_cover_flow', 'img_rockbox', 'label_rockbox'):
                ui.make(self.carousel[0], 'image', name=name)
            for w in self.carousel:  # tools/ipod.py ipod_home: hidden, taking no taps
                ui[w].visible = ui[w].sensitive = 0
        ui.top = self.win

    def open(self, fw, result=0):
        fw.mock('stock_home_trampoline', lambda *_: result)
        return fw.fn('coverflow_home', C.c_int, C.c_void_p, C.c_void_p)(self.win, 0)


def home_open(fw, carousel=False, rockbox=False, **keys):
    t = themed(fw, **keys)
    ui = Ui(fw)
    h = Homes(ui, carousel, rockbox)
    h.open(fw)
    return t, ui, h, fw.var('coverflow__home', Home)


LIST_MARKS = {k: int(v) for k, v in re.findall(r'^#define (CHEV_SHIFT|SPIN_BOX) (\d+)', host.ROOT.joinpath(
    'patch/list_marks.c').read_text(), re.M)}


@pytest.mark.parametrize('mode', ('0', '1'))  # Split, Full
def test_home_s_labels_end_clear_of_the_chevron(fw, library, mode):
    """coverflow_home_layout under OLED's set (chevron_home): every row of Home's lists draws the chevron, or Cover
    Flow's spinner in its place, centred CHEV_SHIFT left of the right column in a SPIN_BOX box (list_marks.c), so a
    label's text, which ends HSCROLL_MARGIN inside its box, ends by that box's left edge, as long as it runs."""
    t = themed(fw, HOME=mode)
    assert t['chevron_home']
    row, _ = split_inset_set(fw, library, 2)  # OLED's set
    ui = Ui(fw)
    h = Homes(ui)
    labels = [ui.make(r, 'hscroll_label', 24, 0, 300, 70) for r in h.rows.values()]
    h.open(fw)
    sets = [ui.of('hscroll_label', b)[0] for r in ui[ui[h.sets].kids[0]].kids for b in ui[r].kids]
    for label, width in [(w, ui.at(h.main)[2]) for w in labels] + [(w, 375) for w in sets]:
        x, _, w, _ = ui.at(label)
        chevron = width - row['right_x'] - LIST_MARKS['CHEV_SHIFT'] - LIST_MARKS['SPIN_BOX'] // 2
        assert x + w - 2 <= chevron, (mode, x, w, width)  # navigation.h HSCROLL_MARGIN


def test_home_binds_its_lists_and_never_the_carousel(fw):
    themed(fw)
    ui = Ui(fw)
    h = Homes(ui, carousel=True)
    assert h.open(fw, 5) == 5  # stock's answer
    home = fw.var('coverflow__home', Home)
    assert (home.win, home.art, home.list, home.sets, home.lib, home.stream) == (h.win, h.art, h.main, h.sets, h.lib, h.stream)
    assert not any(ui[w].sensitive for w in (h.art, h.sets))  # taps find the shown list's rows
    want = {'img_cover_flow': ('coverflow__coverflow_open', 0), 'img_settings': ('coverflow__home_settings', 0),
            'img_home_search': ('library_search_open', 0), 'img_games': ('games_open', 0)}  # Games: Home's, 2026-11-19
    for row, (fn, ctx) in want.items():
        assert ui.handlers(h.rows[row]) == [(address(fw, fn), ctx)], row
    assert not ui.handlers(h.rows['img_library'])  # stock's, by name
    # Streaming is Settings' row, after Playback; with no home_lib (OLED's set) it opens stock's Streaming page
    assert ui.handlers(h.set_stream) == [(address(fw, 'on_home_stream_onclick'), h.win)]
    assert not any(ui.handlers(ui.lookup(h.carousel[0], n)) for n in ('img_cover_flow', 'img_rockbox'))
    assert not any(ui[w].visible for w in h.carousel)


def test_a_page_list_runs_on_to_the_screen_s_foot(fw, library):
    """page_list under OLED's grid (list end 224 under a 58 px bar): twelve 32 px rows run on to the
    screen's foot, the rows past y 282 fading there (navigation.c list_fade), as Home's do."""
    themed(fw)
    split_inset_set(fw, library, 2)  # OLED's set
    ui = Ui(fw)
    fw.mock('widget_resize', lambda w, ww, h, *_: ui.box(w, *ui.at(w)[:2], ww, h))
    client = fw.fn('client_h', C.c_int)()
    page = ui.make(ui.wm, 'window', 0, 320 - client, 375, client)
    body = ui.make(page, 'view', 0, 0, 375, client)
    view = fw.fn('page_list', C.c_void_p, C.c_void_p, C.c_void_p, C.c_void_p, C.c_char_p, C.c_int, C.c_int)(
        page, body, None, b'Twelve', 12, 48)
    lv = ui[view].parent
    top, h = 320 - client + ui.at(lv)[1], ui.at(lv)[3]
    row = fw.fn('page_h', C.c_int, C.c_int)(48)
    assert top + h == 320 and 12 * row > h, (top, h, row)
    assert ui.at(view)[3] == h


def test_settings_slides_in_over_the_list_and_return_slides_the_list_back(fw):
    """Under home.lists.full Settings, laid out a page's width, slides in from the right by that width over the
    theme's swap motion, the art hidden; Return slides the main list back in from the left by its own width."""
    t, ui, h, _ = home_open(fw)
    title, back = fw.fn('coverflow_home_title', C.c_char_p), fw.fn('coverflow_home_back', C.c_int, C.c_void_p)
    assert title() == b'Q2 Muse' and not back(h.win)
    ui.box(h.sets, 50, 0, 375, 290)  # a list still sliding goes to its rest first
    ui.click(h.rows['img_settings'])
    assert not ui[h.main].visible and not ui[h.main].sensitive and ui[h.sets].visible and ui[h.sets].sensitive
    assert ui.at(h.sets)[0] == 375 and ui.animators == [(h.sets, t['motion.swap.ms'], t['motion.swap.easing'], 'x')]
    assert not ui[h.art].visible and title() == b'Settings'
    ui.click(h.rows['img_settings'])  # open already: nothing moves
    assert len(ui.animators) == 1
    assert not back(h.art) and back(h.win) == 1
    assert ui[h.main].visible and ui[h.main].sensitive and not ui[h.sets].visible and not ui[h.sets].sensitive
    assert ui.at(h.main)[0] == -ui.at(h.main)[2] and ui.animators[-1][0] == h.main  # in from the left
    assert ui[h.art].visible
    assert title() == b'Q2 Muse' and not back(h.win)


def test_under_home_lists_full_settings_is_a_page_and_return_brings_the_art_back(fw):
    """OLED's home.lists.full: the list a row opens takes the screen's width with the art hidden, as a page; Return
    shows the main list beside the art again."""
    t, ui, h, home = home_open(fw, HOME='0')
    assert t['home_lists_full'] and ui[h.art].visible
    assert ui.at(h.sets)[2] == 375  # laid out a page's width while hidden
    ui.click(h.rows['img_settings'])
    assert ui[h.sets].visible and not ui[h.art].visible
    assert ui.at(h.sets)[0] in (0, 375)  # in from the right by its own width, or in place with no slide
    assert fw.fn('coverflow_home_back', C.c_int, C.c_void_p)(h.win) == 1
    assert ui[h.main].visible and ui[h.art].visible


def test_a_list_return_leaves_on_home_opens_at_its_top_again(fw):
    """list.reopen.top on Home's own lists, which stay alive hidden: Return drops the row the list was left on and
    scrolls it to its top (navigation.c forget_rows), so choosing it again opens there."""
    _, ui, h, _ = home_open(fw)
    ui.click(h.rows['img_settings'])
    view = ui[h.sets].kids[0]
    ui[view].props['_ringnav_index'] = 1  # the wheel left it on its second row
    assert fw.fn('coverflow_home_back', C.c_int, C.c_void_p)(h.win) == 1
    assert ui[view].props['_ringnav_index'] == -1


def test_a_swap_with_no_animator_shows_the_list_in_place(fw):
    _, ui, h, _ = home_open(fw)
    ui.animate = False
    ui.click(h.rows['img_settings'])
    assert ui[h.sets].visible and ui.at(h.sets)[0] == 0


def test_home_owns_its_lists_rows(fw):
    themed(fw)
    ui = Ui(fw)
    h = Homes(ui)
    owns = fw.fn('coverflow_home_owns', C.c_int, C.c_void_p)
    assert not owns(h.rows['img_library'])  # before Home's bind
    h.open(fw)
    for k, lst in h.lists.items():
        assert owns(ui[ui[lst].kids[0]].kids[0]), k
    assert owns(h.rows['img_library']) and owns(h.main) and not owns(h.art) and not owns(h.win) and not owns(None)


@pytest.mark.parametrize('home', ('0', '1'))
def test_home_s_list_has_no_shortcut_row(fw, home):
    # Home's list has no Shortcut row, and a stored IPOD SHORTCUT is ignored: a row the asset had is left as it was
    themed(fw, SHORTCUT='0', HOME=home)
    ui = Ui(fw)
    h = Homes(ui, rockbox=True)
    rockbox = h.rows['img_rockbox']
    before = list(ui[h.view].kids)
    h.open(fw)
    assert [r for r in ui[h.view].kids if r != rockbox] == [r for r in before if r != rockbox]


def np_home(fw, status=1, home='0'):
    """Home with its Now Playing row named as the asset names it, play's state status[0] and the clock now[0]."""
    themed(fw, HOME=home)
    ui = Ui(fw)
    h = Homes(ui, rockbox=True)
    playing = h.rows['img_playing']
    ui[playing].name = 'btn_playing'
    state, now = [status], [1000]
    fw.mock('mclGetPlayStatus', lambda *_: state[0])
    fw.mock('time_now_ms', lambda *_: now[0])
    h.open(fw)
    return ui, h, playing, state, now


def shown_rows(ui, h):
    return [ui[r].name for r in ui[h.view].kids if ui[r].visible]


@pytest.mark.parametrize('home', ('0', '1'))
@pytest.mark.parametrize('status, shown', ((1, False), (0, False), (2, True), (3, True)))
def test_now_playing_s_row_shows_only_while_something_is_loaded(fw, home, status, shown):
    """An iPod's main menu: Now Playing only while a song plays or is paused, in Split and Full; hidden, it is
    restacked last and takes no taps."""
    ui, h, playing, _, _ = np_home(fw, status, home)
    rows = ui[h.view].kids
    assert ui[playing].visible == shown and ui[playing].sensitive == shown
    assert rows.index(playing) == (0 if shown else len(rows) - 1)
    assert rows.index(h.rows['img_rockbox']) == rows.index(h.rows['img_folder']) + 1
    assert fw.var('coverflow__home', Home).np == shown


def test_now_playing_s_row_comes_as_play_starts_keeping_the_selected_row(fw):
    ui, h, playing, state, _ = np_home(fw)
    view = h.view
    ui[view].props.update({'_ringnav_index': 2, '_ringnav_count': 8})  # Folder
    np_sync = fw.fn('coverflow_home_np', None)
    np_sync()
    assert not ui[playing].visible  # still nothing loaded: no layout
    state[0] = 2
    np_sync()
    assert ui[playing].visible and ui[view].kids.index(playing) == 0
    assert ui[view].props['_ringnav_index'] == 3 and ui[view].props['_ringnav_count'] == 9  # still Folder, no reset


@pytest.mark.parametrize('sel, after', ((0, 0), (3, 2), (-1, -1)))
def test_now_playing_s_row_goes_once_play_has_stopped_a_while(fw, sel, after):
    """Stopped: the row stays NP_GONE_MS, as for the moment between two tracks, and a timer takes it then, as nothing may
    paint; on Now Playing the selection lands on Library, else on the same row."""
    ui, h, playing, state, now = np_home(fw, status=2)
    view = h.view
    ui[view].props.update({'_ringnav_index': sel, '_ringnav_count': 9})
    np_sync = fw.fn('coverflow_home_np', None)
    state[0] = 1
    np_sync()
    assert ui[playing].visible
    due = ui.timer(address(fw, 'coverflow__np_due'))
    assert due and due[1] in (3000, 3001)  # from the stop, seen at now | 1
    now[0] += 2999
    np_sync()
    assert ui[playing].visible
    state[0] = 2  # the next track starts: kept
    now[0] += 5000
    np_sync()
    assert ui[playing].visible
    state[0] = 1
    np_sync()  # stopped again, from now
    now[0] += 3000
    ui.fire(ui.timer(address(fw, 'coverflow__np_due'))[0])
    assert not ui[playing].visible and ui[view].kids[-1] == playing
    assert ui[view].props['_ringnav_index'] == after and ui[view].props['_ringnav_count'] == 8
    assert shown_rows(ui, h)[0] == 'img_library'


@pytest.mark.parametrize('sel, y, want', ((6, 0, 70), (2, 0, 0), (7, 70, 140)))
def test_now_playing_s_row_coming_keeps_the_selected_row_in_sight(fw, sel, y, want):
    """The row comes above the list: scrolled, the same rows stay in view; at the top, the view moves a row only when
    the selected one would pass the seven the list rests on."""
    ui, h, playing, state, _ = np_home(fw)
    ui[h.view].props.update({'_ringnav_index': sel, '_ringnav_count': 8})
    C.c_int.from_address(h.view + O['SCROLL_Y']).value = y
    state[0] = 3
    before = len(fw.calls('scroll_view_set_offset'))
    fw.fn('coverflow_home_np', None)()
    offsets = [a[2] for _, a in fw.calls('scroll_view_set_offset')][before:]
    assert offsets and offsets[0] == want


@pytest.mark.parametrize('mode', ('1', '2', '7'))
def test_full_hides_the_art_and_a_saved_carousel_is_split(fw, mode):
    """Full hides the art; HOME=2, and any other value but 0 and 1, lays Home out as Split, the carousel left
    hidden."""
    themed(fw, HOME=mode)
    ui = Ui(fw)
    h = Homes(ui, carousel=True)
    h.open(fw)
    assert ui[h.art].visible == (mode != '1') and ui[h.main].visible and not ui[h.sets].visible
    assert not any(ui[w].visible or ui[w].sensitive for w in h.carousel)


# Split's pane: what the highlighted row shows

def home_row(fw, surface, row, id=0, painting=0):
    fw.fn('coverflow_home_row', None, C.c_void_p, C.c_void_p, C.c_int, C.c_int)(surface, row, id, painting)
    return fw.var('coverflow__home', Home).kind


def pane_rect(fw, h):
    """The pane's box in Home's window, the whole screen (docs/oled-design.md#full-bleed): split_art_up rows above
    the status row's foot to the screen's bottom."""
    names = host.tools_theme.GEOM_VALS
    row = dict(zip(names, (C.c_short * len(names)).in_dll(fw.lib, 'theme_engine__geom_vals')))
    x, ph = fw.fn('coverflow__pane_x', C.c_int)(), fw.fn('client_h', C.c_int)() + row['split_art_up']
    return h.win, (x, 320 - ph, 375 - x, ph)


@pytest.mark.parametrize('name', ('img_library', 'img_cover_flow', 'img_folder', 'img_games', 'img_settings',
                                  'img_shuffle_songs', 'img_home_search', 'img_unknown'))
def test_the_pane_follows_the_highlighted_row(fw, name):
    """Off Now Playing's row (the pane's first kind) any row shows the library's covers."""
    _, ui, h, home = home_open(fw)
    assert home.kind == PANE_NOW
    row = ui.make(h.view, 'image', name=name)
    ui.dirty.clear()
    assert home_row(fw, h.view, row) == PANE_LIBRARY
    assert ui.dirty == [pane_rect(fw, h)]  # on a wheel move the pane joins the row's frame
    ui.dirty.clear()
    home_row(fw, h.view, row)
    assert not ui.dirty  # the same row: nothing compared or drawn again


def test_a_paint_s_row_change_shows_in_the_next_frame(fw):
    _, ui, h, home = home_open(fw)
    ui.dirty.clear()
    assert home_row(fw, h.view, h.rows['img_cover_flow'], painting=1) == PANE_LIBRARY
    assert not ui.dirty  # a paint can't invalidate: AWTK drops it with the frame
    i, ms = ui.timer(address(fw, 'coverflow__pane_after'))
    assert ms == 0 and home.pane_timer == i
    ui.fire(i)
    assert ui.dirty == [pane_rect(fw, h)] and not home.pane_timer


@pytest.mark.parametrize('keys', ({'HOME': '1'}, {'ART': ''}))
def test_without_the_pane_rows_change_nothing(fw, keys):
    """Full or no art: no pane, so a row it would follow leaves it as it was."""
    _, ui, h, home = home_open(fw, **{k: v for k, v in keys.items() if k != 'ART'})
    if 'ART' in keys:
        home.art = 0
    home.kind = PANE_LIBRARY
    kind = home_row(fw, h.view, h.rows['img_playing'])
    assert (kind, home.row) == (PANE_LIBRARY, None)


# The pan's timer

def drifting(fw, light=1):
    home = fw.var('coverflow__home', Home)
    s = home.sl[0]
    s.tw, s.th, s.rx, s.ry, s.u0, s.v0 = 300, 300, 18, 29, 10, 0
    home.drawn = fw.fn('coverflow__frame_stamp', C.c_uint)()
    fw.var('g_backlight_status', C.c_ubyte).value = light
    return home


def test_the_pan_steps_while_it_shows_capped_after_a_stall(fw):
    _, ui, h, _ = home_open(fw)
    home = drifting(fw)
    now = [1000]
    fw.mock('time_now_ms', lambda *_: now[0])
    drift = fw.fn('coverflow__home_drift', C.c_int, C.c_void_p)
    tick, cap = O['DRIFT_TICK_MS'], O['DRIFT_MAX_STEP_MS']
    home.drift, home.drift_last, home.tick, home.sl[0].ms = 3, 1000 - tick, tick, 0
    ui.dirty.clear()
    assert drift(None) == 8 and home.sl[0].ms == tick and home.drift_last == 1000  # a tick on time keeps its time
    assert ui.dirty == [pane_rect(fw, h)]  # the pan moved: the panel repaints
    now[0] = 61000
    assert drift(None) == 8 and home.sl[0].ms == tick + cap  # a stalled loop never jumps
    home.sl[0].ms = 2 * O['DRIFT_X_MS'] * O['DRIFT_Y_MS'] - 1
    now[0] += tick
    drift(None)
    assert home.sl[0].ms == tick - 1  # whole cycles of both axes: no wrap mid-cycle


def test_a_pan_that_has_not_moved_repaints_nothing(fw):
    _, ui, h, _ = home_open(fw)
    home = drifting(fw)
    fw.mock('time_now_ms', lambda *_: 1100)
    home.drift_last = 1100  # no time shown since the last step: the pan is where the frame was drawn
    home.tick = O['DRIFT_TICK_MS']
    ui.dirty.clear()
    assert fw.fn('coverflow__home_drift', C.c_int, C.c_void_p)(None) == 8 and not ui.dirty


@pytest.mark.parametrize('change', ('dark', 'locked', 'covered', 'sliding', 'still', 'margin'))
def test_the_pan_stops_once_it_no_longer_shows(fw, change):
    _, ui, h, _ = home_open(fw)
    home = drifting(fw)
    if change == 'dark':
        fw.var('g_backlight_status', C.c_ubyte).value = 0
    elif change == 'locked':
        fw.var('g_lockscreen_pageflag', C.c_ubyte).value = 1
    elif change == 'covered':
        ui.top = ui.make(0, 'window')
    elif change == 'sliding':
        ui.animating = 1
    elif change == 'still':
        home.sl[0].tw = 0
    else:  # no margin left to pan across (a split_inset as wide as the zoom's)
        home.sl[0].rx = home.sl[0].ry = 0
    home.drift = 4
    ui.dirty.clear()
    assert fw.fn('coverflow__home_drift', C.c_int, C.c_void_p)(None) == 0 and not home.drift and not ui.dirty


def test_a_home_paint_arms_the_pan_once_while_it_shows(fw, mcl):
    _, ui, h, _ = home_open(fw)
    home = drifting(fw)
    fw.mock('time_now_ms', lambda *_: 4321)
    fw.fn('coverflow_home_art', None, C.c_void_p)(h.win)
    i, ms = ui.timer(address(fw, 'coverflow__home_drift'))
    assert ms == O['DRIFT_TICK_MS'] and home.drift == i and home.drift_last == 4321
    home.sl[0].tw = 300
    fw.fn('coverflow_home_art', None, C.c_void_p)(h.win)
    assert len([t for t in ui.timers.values() if t[0] == address(fw, 'coverflow__home_drift')]) == 1


# The slideshow (docs/ipod.md#home): library covers in turn, each zooming and drifting once, cross-faded

LIFE = O['SLIDE_HOLD_MS'] + 2 * O['SLIDE_FADE_MS']


def library_slide(s, ends=((1000, 2000, 65536), (3000, 1000, O['KB_NEAR']))):
    s.lib, s.kb[0][:], s.kb[1][:] = 1, ends[0], ends[1]


def test_a_library_slide_zooms_and_drifts_once_at_an_even_pace_over_its_life(fw):
    s = fw.var('coverflow__home', Home).sl[0]
    library_slide(s)
    assert slide_at(fw, 0) == (1000, 2000, 65536)
    s.ms = LIFE // 2
    assert slide_at(fw, 0) == (2000, 1500, (65536 + O['KB_NEAR']) // 2)
    for ms in (LIFE, LIFE + 1, 10 * LIFE):  # then still, at the far end
        s.ms = ms
        assert slide_at(fw, 0) == (3000, 1000, O['KB_NEAR'])
    s.ms = 0
    last = slide_at(fw, 0)
    for ms in range(0, LIFE, 100):  # one way, never back
        s.ms = ms
        now = slide_at(fw, 0)
        assert now[0] >= last[0] and now[1] <= last[1] and now[2] <= last[2]
        last = now


def test_a_library_slides_ends_stay_inside_the_inset_one_zoomed_in_one_not(fw, library):
    row, client = split_inset_set(fw, library, 2)  # OLED's
    pw, ph = 375 - row['split_art'], client + row['split_art_up']
    for w, h in ((300, 300), (400, 300), (300, 600)):
        tex, home, _ = tex_rig(fw, w, h, rgba(w, h, lambda x, y: (10, 20, 30, 255)))
        for seed in range(1, 40):
            home.seed = seed * 7919
            assert tex(b'x', 1) == 1
            s = home.sl[0]
            assert s.lib == 1 and sorted((s.kb[0][2], s.kb[1][2])) == [O['KB_NEAR'], 65536]
            for e in range(2):
                u, v, k = s.kb[e]
                ww, hh = pw * (k >> 8), ph * (k >> 8)
                assert s.tw * 2 // 100 * 256 <= u <= s.tw * 256 - s.tw * 2 // 100 * 256 - ww, (w, h, seed, e)
                assert s.th * 2 // 100 * 256 <= v <= s.th * 256 - s.th * 2 // 100 * 256 - hh, (w, h, seed, e)
            centre = [(s.kb[e][0] + pw * (s.kb[e][2] >> 8) // 2, s.kb[e][1] + ph * (s.kb[e][2] >> 8) // 2) for e in range(2)]
            assert all(abs(a - b) <= O['KB_DRIFT'] * 256 + 256 for a, b in zip(*centre))  # a slow drift, clamps aside


def test_a_zoomed_window_samples_the_cover_at_its_scale(fw):
    """A ramp through the zoom: each pixel reads the texel at u + x k, v + y k."""
    tex = slide_tex(fw, 206, PANE_H + 90, lambda x, y: 0xff400000 | x | min(y, 255) << 8)
    d = exact(C.c_uint, PANE_W * PANE_H)
    k = O['KB_NEAR']
    for u, v in ((0, 0), (3 * 256 + 77, 5 * 256 + 3)):
        render(fw, d, PANE_W, u, v, k)
        for py, px in ((60, 0), (100, 50), (150, PANE_W - 1), (200, 100)):  # rows clear of the vignette
            c = d[py * PANE_W + px]
            assert abs((c & 255) - (u / 256 + px * k / 65536 + 1)) <= 1, (u, px)
            assert abs((c >> 8 & 255) - (v / 256 + py * k / 65536 + 1)) <= 1, (v, py)
    libc.free(C.addressof(d))
    libc.free(C.addressof(tex))


def test_a_zoomed_out_window_at_the_far_corner_stays_inside_the_border(fw):
    tw, th = 206, TEX_H
    tex = slide_tex(fw, tw, th, lambda x, y: 0xff000000)
    d = exact(C.c_uint, PANE_W * PANE_H)
    k = 65536 * 109 // 100  # wider than any slide's window
    render(fw, d, PANE_W, tw * 256 - PANE_W * k // 256, th * 256 - PANE_H * k // 256, k)  # an overrun is the sanitizer's
    assert d[-1] == 0xff000000
    libc.free(C.addressof(d))
    libc.free(C.addressof(tex))


def test_the_incoming_slide_is_laid_over_the_frame_by_its_share(fw):
    home = fw.var('coverflow__home', Home)
    first = slide_tex(fw, 206, TEX_H, lambda x, y: 0xff000000)
    second = exact(C.c_uint, 209 * (TEX_H + 3))
    for i in range(209 * (TEX_H + 3)):
        second[i] = 0xffc8641e
    home.sl[1].tex, home.sl[1].tw, home.sl[1].th = C.addressof(second), 206, TEX_H
    d = exact(C.c_uint, PANE_W * PANE_H)
    render(fw, d, PANE_W, 0, 0)
    render(fw, d, PANE_W, 0, 0, blend=64, slide=1)
    middle = d[60 * PANE_W:(PANE_H - 50) * PANE_W]  # rows clear of the vignette
    assert set(middle) == {0xff000000 | (0xc8 * 64 + 128) >> 8 << 16 | (0x64 * 64 + 128) >> 8 << 8 | (0x1e * 64 + 128) >> 8}
    for b in (first, second, d):
        libc.free(C.addressof(b))


def fading(fw, ui, h):
    """The playing cover showing and a library cover fading in over it, the pan's timer at its own period."""
    home = drifting(fw)
    s = home.sl[1]
    s.tw, s.th = 300, 300
    library_slide(s)
    home.fade, home.tick, home.drift_last = 1, O['DRIFT_TICK_MS'], 1000
    return home


def test_a_fade_steps_both_slides_on_the_faster_tick_then_the_new_one_shows(fw):
    _, ui, h, _ = home_open(fw)
    home = fading(fw, ui, h)
    now = [1000]
    fw.mock('time_now_ms', lambda *_: now[0])
    drift = fw.fn('coverflow__home_drift', C.c_int, C.c_void_p)
    home.sl[0].tex, home.sl[1].tex = libc.calloc(4, 1), libc.calloc(4, 1)
    second = home.sl[1].tex
    now[0] += 100
    assert drift(None) == 0 and home.tick == O['SLIDE_TICK_MS']  # a new timer takes over at the fade's period
    i, ms = ui.timer(address(fw, 'coverflow__home_drift'))
    assert (i, ms) == (home.drift, O['SLIDE_TICK_MS'])
    assert (home.fade, home.sl[0].ms, home.sl[1].ms) == (101, 100, 100)
    k = fw.fn('coverflow__fade_k', C.c_uint)
    assert k() == 101 * 256 // O['SLIDE_FADE_MS']
    ui.dirty.clear()
    now[0] += 100
    assert drift(None) == 8 and ui.dirty == [pane_rect(fw, h)]  # each step repaints: the fade moved
    while home.fade:  # tick by tick, as a step is capped
        now[0] += O['SLIDE_TICK_MS']
        drift(None)
    assert now[0] - 1000 == 100 * -(-O['SLIDE_FADE_MS'] // 100)
    assert home.fade == 0 and home.sl[0].tex == second and home.sl[0].lib == 1 and not home.sl[1].tex and not home.sl[1].tw
    assert home.tick == O['DRIFT_TICK_MS']  # back to the pan's period, the old cover's texture freed (the sanitizer's)
    libc.free(second)


def test_a_playing_cover_that_comes_mid_fade_takes_the_incoming_ones_place(fw):
    """A track change fades in the album's Coverflow thumbnail until the player's own cover is parsed: that one, or a
    quick skip's next, replaces the incoming cover where the fade has got to, with its phase, so nothing jumps."""
    tex, home, _ = tex_rig(fw, 300, 300, rgba(300, 300, lambda x, y: (10, 20, 30, 255)))
    show = fw.fn('coverflow__slide_show', C.c_int, C.c_char_p, C.c_int, C.c_int)
    assert show(b'a', 0, 0) == 1 and home.sl[0].tw and not home.fade  # nothing showed: at once
    showing = home.sl[0].tex
    home.sl[0].ms = 700
    assert show(b'b', 0, 0) == 1 and home.fade == 1 and home.sl[1].ms == 700  # over it, at its phase
    home.fade, home.sl[1].ms = 600, 1300
    assert show(b'c', 0, 0) == 1 and home.fade == 600 and home.sl[0].tex == showing and home.sl[1].ms == 1300
    assert show(b'd', 1, 0) == 1 and home.fade == 1 and home.sl[0].tex != showing  # a library cover ends it first
    bitmap(fw, 300, 300, rgba(300, 300, lambda x, y: (1, 2, 3, 255)), loads=False)
    home.fade, home.sl[1].lib = 500, 0
    assert show(b'e', 0, 0) == -1 and home.fade == 0 and not home.sl[1].tw  # none to fade in: the one showing stays


def test_play_starting_mid_library_fade_fades_from_the_frame_as_it_stands(fw):
    """Play starts while a library cover fades in: the playing cover fades in over the frame as it was, the two blended
    covers, with no jump at the switch, and once in it shows alone."""
    covers = {u: (300, 300, rgba(300, 300, lambda x, y, c=c: c)) for u, c in
              (('a', (200, 0, 0, 255)), ('d', (0, 0, 200, 255)), ('e', (0, 200, 0, 255)))}
    b = Bitmaps(fw, covers)
    home = fw.var('coverflow__home', Home)
    show = fw.fn('coverflow__slide_show', C.c_int, C.c_char_p, C.c_int, C.c_int)
    frame = fw.fn('coverflow__home_frame', C.c_int)
    assert show(b'a', 0, 0) == 1 and show(b'd', 1, 0) == 1 and home.fade == 1 and home.sl[1].lib == 1
    home.fade = 600  # the library cover 600 ms into its fade
    assert frame()
    before = pixels_of(b, home.frame)
    assert show(b'e', 0, 0) == 1 and home.fade == 1 and home.sl[1].lib == 0  # play: the playing cover fades in
    assert frame()
    after = pixels_of(b, home.frame)
    for y in range(len(before)):
        for x in range(PANE_W):
            p, q = before[y][x], after[y][x]
            assert all(abs((p >> s & 255) - (q >> s & 255)) <= 1 for s in (0, 8, 16)), (x, y, hex(p), hex(q))
    home.fade = O['SLIDE_FADE_MS'] - 1
    assert frame()
    p = pixels_of(b, home.frame)[100][10]  # a row clear of the vignette
    assert p & 255 <= 1 and 198 <= p >> 8 & 255 <= 200 and p >> 16 & 255 <= 1  # all but a 256th the green


def test_a_fade_keeps_the_pan_live_with_a_still_cover_under_it(fw):
    _, ui, h, _ = home_open(fw)
    home = fading(fw, ui, h)
    home.sl[0].rx = home.sl[0].ry = 0
    assert fw.fn('coverflow__drift_live', C.c_int)() == 1
    home.fade = 0
    assert fw.fn('coverflow__drift_live', C.c_int)() == 0


def source(fw):
    return fw.fn('coverflow__art_source', C.c_int)()


SHOW_NOW, SHOW_LIB = 0, 1


@pytest.mark.parametrize('name, kind', (('img_playing', PANE_NOW), ('img_library', PANE_LIBRARY), ('img_cover_flow', PANE_LIBRARY),
                                        ('img_shuffle_songs', PANE_LIBRARY), ('img_home_search', PANE_LIBRARY),
                                        ('img_settings', PANE_LIBRARY), ('img_rockbox', PANE_LIBRARY)))
def test_under_oled_the_pane_follows_the_row_between_the_playing_cover_and_the_show(fw, name, kind):
    """OLED, an iPod classic's menu: Now Playing's row the playing cover, playing or paused however long; every other
    row the library's covers."""
    _, ui, h, home = home_open(fw, HOME='0')
    fw.mock('mclGetPlayStatus', lambda *_: 3)
    now = [1000]
    fw.mock('time_now_ms', lambda *_: now[0])
    ui.dirty.clear()
    assert home_row(fw, h.view, h.rows[name]) == kind
    assert source(fw) == (SHOW_NOW if kind == PANE_NOW else SHOW_LIB)
    now[0] += 600000  # ten minutes paused
    assert source(fw) == (SHOW_NOW if kind == PANE_NOW else SHOW_LIB)


def test_under_oled_the_wheel_off_now_playing_mid_fade_fades_the_show_in_over_the_frame(fw):
    """The wheel leaves Now Playing while its cover fades in: the next library cover fades in over the frame as it
    stands, with no jump, rather than once the playing cover's fade ends."""
    covers = {u: (300, 300, rgba(300, 300, lambda x, y, c=c: c)) for u, c in
              (('a', (200, 0, 0, 255)), ('d', (0, 0, 200, 255)), ('e', (0, 200, 0, 255)))}
    b = Bitmaps(fw, covers)
    home = fw.var('coverflow__home', Home)
    show = fw.fn('coverflow__slide_show', C.c_int, C.c_char_p, C.c_int, C.c_int)
    frame = fw.fn('coverflow__home_frame', C.c_int)
    assert show(b'd', 1, 0) == 1 and show(b'e', 0, 0) == 1 and home.fade == 1 and home.sl[1].lib == 0
    home.fade = 600  # the playing cover 600 ms into its fade
    assert frame()
    before = pixels_of(b, home.frame)
    assert show(b'a', 1, 0) == 1 and home.fade == 1 and home.sl[1].lib == 1 and home.sl[0].lib == 2  # SLIDE_STILL
    assert frame()
    after = pixels_of(b, home.frame)
    for y in range(len(before)):
        for x in range(PANE_W):
            p, q = before[y][x], after[y][x]
            assert all(abs((p >> s & 255) - (q >> s & 255)) <= 1 for s in (0, 8, 16)), (x, y, hex(p), hex(q))


def test_the_show_goes_on_over_a_track_change(fw, mcl):
    _, ui, h, _ = home_open(fw, HOME='0')
    home = drifting(fw)
    library_slide(home.sl[0])
    fw.mock('mclGetPlayStatus', lambda *_: 1)
    fw.mock('time_now_ms', lambda *_: 100000)
    home.show, home.key, home.kind = SHOW_LIB, 12345, PANE_LIBRARY
    fw.fn('coverflow_home_art', None, C.c_void_p)(h.win)
    assert home.key != 12345 and not fw.calls('widget_load_image') and home.sl[0].lib == 1


# A card app's URL queue: its songs have no path and a cover by URL leaves g_lastcover_url "", so a paint between the
# song change and its cover's request shows the last song's file, still in place, under the new album. A quick skip
# asks for the next cover while the last one downloads: that lands first, then the next one with no paint between
# its type 0 and 2. Home keys the file written, so each download is shown.
@in_fork
def test_home_shows_each_cover_a_url_queue_downloads(fw, mcl):
    _, ui, h, _ = home_open(fw)
    fw.var('g_backlight_status', C.c_ubyte).value = 1
    recs = [Rec(path='') for _ in range(2)]
    fw.var('fw_ctx_mcl_pdeqplaylist', C.c_uint).value = Deques(fw).new([rec.p for rec in recs])
    fw.var('fw_ctx_g_lastcover_url', C.c_char * 1024).value = b''
    kind, pic = fw.var('g_playcover_type', C.c_ubyte), Externpic(fw)
    def downloaded(size=20000):
        pic.write(size)
        kind.value = 2
    def paint():
        fw.fn('coverflow_home_art', None, C.c_void_p)(h.win)
        return len(fw.calls('image_manager_unload_bitmap_by_name'))
    mcl.pos = 0
    player_rig(fw, id3_path=b'', album=b'One')
    ask_cover(fw)
    downloaded()
    assert paint() == 1
    mcl.pos = 1
    player_rig(fw, id3_path=b'', album=b'Two')
    assert paint() == 2  # the last song's file, under the new album
    ask_cover(fw)
    downloaded()  # the first song's cover again, still downloading at the skip
    assert paint() == 3
    kind.value = 0  # the thread takes the second song's request: no paint sees it
    downloaded(31000)
    assert paint() == 4
    assert paint() == 4


def test_a_dark_screen_arms_no_pan(fw, mcl):
    _, ui, h, _ = home_open(fw)
    drifting(fw, light=0)
    fw.fn('coverflow_home_art', None, C.c_void_p)(h.win)
    assert not ui.timer(address(fw, 'coverflow__home_drift'))


def test_the_placeholder_runs_up_under_the_status_row_as_a_cover_does(fw, mcl, library):
    """Split's pane with no cover to show (nothing queued, or a song with no art) shows the placeholder from the
    screen's top edge, under the status row, as a cover does (user, 2026-11-16: it started under the status row,
    leaving a black band): it is loaded as the pane's slide, so the bar paints its top rows and inks its glyphs."""
    _, ui, h, _ = home_open(fw)
    row, _ = split_inset_set(fw, library, 2)  # OLED's set, whose art runs up split_art_up rows
    assert row['split_art_up']
    fw.var('g_backlight_status', C.c_ubyte).value = 1
    Bitmaps(fw, {'default_album_home': (290, 290, rgba(290, 290, lambda x, y: (58, 58, 58, 255)))})
    home = fw.var('coverflow__home', Home)
    fw.fn('coverflow_home_art', None, C.c_void_p)(h.win)
    assert home.sl[0].tw and fw.fn('coverflow_home_split', C.c_int, C.c_void_p)(h.win) == 2


# Stock's bitmaps and canvas

class Bitmaps:
    """widget_load_image by url: a bitmap_t (w @0, h @4, format @0xe) whose lock reads its pixels; bitmap_create_ex a
    blank one the C writes into."""
    def __init__(self, fw, images=None, create=True):
        self.images, self.made, self.loads, self.unloads, self.data, self.reads = images or {}, [], [], [], {}, 0
        self.keep = []
        fw.mock('widget_load_image', self.load)
        fw.mock('widget_unload_image', lambda owner, b, *_: self.unloads.append(b))
        fw.mock('bitmap_lock_buffer_for_read', self.read)
        fw.mock('bitmap_lock_buffer_for_write', lambda b, *_: C.addressof(self.data[b][0]))
        fw.mock('bitmap_get_line_length', lambda b, *_: self.data[b][1])
        fw.mock('bitmap_create_ex', self.create if create else lambda *_: 0)

    def read(self, b, *_):
        self.reads += 1
        return C.addressof(self.data[b][0])

    def load(self, owner, url, b, *_):
        url = C.string_at(url).decode()
        self.loads.append(url)
        if url not in self.images:
            return 1
        self.put(b, *self.images[url])
        return 0

    def put(self, b, w, h, pixels, fmt=1):
        (C.c_uint * 2).from_address(b)[:] = [w, h]
        C.c_ushort.from_address(b + 14).value = fmt
        self.data[b] = (pixels, 4 * w)

    def create(self, w, h, line, fmt, *_):
        block, pixels = (C.c_uint * 16)(w, h, line), (C.c_ubyte * (h * line))()
        C.c_ushort.from_address(C.addressof(block) + 14).value = fmt
        self.made.append((block, pixels))
        self.data[C.addressof(block)] = (pixels, line)
        return C.addressof(block)


def pixels_of(bitmaps, b):
    pixels, line = bitmaps.data[b]
    return [list((C.c_uint * (line // 4)).from_buffer(pixels, y * line)) for y in range(len(pixels) // line)]


class Canvas:
    """A canvas whose LCD holds a fill colour: the images, texts (in the last font's px), fonts and filled rects drawn."""
    def __init__(self, fw):
        self.lcd, self.raw = (C.c_char * 0x200)(), (C.c_char * 0x100)()
        C.c_uint.from_buffer(self.lcd, O['LCD_FILL_COLOR']).value = 0x12345678
        C.c_void_p.from_buffer(self.raw, O['CANVAS_LCD']).value = C.addressof(self.lcd)
        self.p = C.addressof(self.raw)
        self.images, self.texts, self.fonts, self.fills, self.rects = [], [], [('', 0)], [], []
        fw.mock('canvas_draw_image', lambda c, b, s, d, *_: self.images.append((b, rect(s), rect(d))))
        fw.mock('canvas_draw_text_in_rect', lambda c, s, n, r, *_: self.texts.append(
            (''.join(map(chr, (C.c_uint * n).from_address(s))), rect(r), self.fonts[-1][1])))
        fw.mock('canvas_set_font', lambda c, face, px, *_: self.fonts.append((C.string_at(face).decode() if face else '', px)))
        fw.mock('canvas_measure_text', lambda c, s, n, *_: n * self.fonts[-1][1] * .75)
        fw.mock('canvas_set_fill_color', lambda c, colour, *_: self.fills.append(colour & 0xffffffff))
        fw.mock('canvas_fill_rect', lambda c, x, y, w, h, *_: self.rects.append((self.fills[-1], *(C.c_int(v).value for v in (x, y, w, h)))))


# The pages' pieces, shared with navigation.c and the other pages

@pytest.mark.parametrize('current, failed, s, differs', [
    (b'Loading', 0, b'Loading', 0), (b'Loading', 0, b'Loading\xe2\x80\xa6', 1), (b'', 0, b'', 0),
    (b'x', 1, b'x', 1),  # stock wrote nothing: it didn't fit, so it differs
])
def test_text_differs(fw, current, failed, s, differs):
    def get(w, out, n, *_):
        C.memmove(out, current + b'\0', len(current) + 1)
        return failed
    fw.mock('widget_get_text_utf8', get)
    assert fw.fn('text_differs', C.c_int, C.c_void_p, C.c_char_p)(0x40, s) == differs


@pytest.fixture(scope='module')
def scratch(library):
    """scratch_t's field offsets in this build: navigation.c's st."""
    return host.offsets(library.parent/'navigation.o', 'scratch_t')


def bar_title(fw, ui, scratch, width):
    """The bar's title, width px: a caption of n characters measures 10 n px."""
    bar = ui.make(0, 'hscroll_label', 0, 0, width, 30)
    C.c_void_p.from_address(C.addressof(fw.var('st', C.c_char)) + scratch['bar_title']).value = bar
    fw.mock('widget_get_canvas', lambda *_: 0x1234)
    fw.mock('canvas_measure_text', lambda c, s, n, *_: float(n * 10))


def page_title(fw, body, caption, n):
    return fw.fn('page_title', C.c_void_p, C.c_void_p, C.c_char_p, C.c_int)(body, caption, n)


@pytest.mark.parametrize('rows, hidden', ((3, True), (0, False)))
def test_a_status_row_ends_a_long_caption_in_an_ellipsis_and_keeps_the_rows_where_they_are(fw, scratch, rows, hidden):
    """OLED's status row holds a caption too wide for its box too (its label ends it in an ellipsis), so a page's rows
    start under the status row whatever the caption's length; a page with no rows still says it as its first line."""
    t = themed(fw)
    ui = Ui(fw)
    bar_title(fw, ui, scratch, 40)  # 'Albums' measures 60 px: too wide
    title = page_title(fw, ui.make(0, 'view'), b'Albums', rows)
    assert ui[title].name == '_page_title' and ui[title].text == 'Albums'
    assert ui[title].visible == (not hidden)
    if hidden:
        assert ui.at(title)[3] == t['page_title_h'] == 0  # the rows start at the page's top


def page_list_or(fw, page, body, name, none, n, item_h=48):
    return fw.fn('page_list_or', C.c_void_p, C.c_void_p, C.c_void_p, C.c_char_p, C.c_char_p, C.c_int, C.c_int)(
        page, body, name, none, n, item_h)


def test_an_empty_page_says_so_and_a_bar_still_reads_its_name(fw):
    themed(fw)
    ui = Ui(fw)
    page = ui.make(0, 'window', 0, 0, 375, 320)  # full-bleed
    body = ui.make(page, 'view', 0, 58, 375, 262)  # Cover Flow's: from the status row's foot
    page_list_or(fw, page, body, b'Most Played', b'No plays yet', 0)
    labels = ui.of('hscroll_label', body)
    assert [ui[w].text for w in labels if ui[w].visible] == ['No plays yet']
    read = [ui[w].text for w in labels if ui[w].name == '_page_title' and not ui[w].visible]
    assert read == ['Most Played']
    assert ui.at(ui.of('list_view', body)[0])[3] == 0  # no rows


@pytest.mark.parametrize('n, rows', ((3, 3), (30, None)))
def test_a_page_list_s_rows_run_from_its_title_to_the_page_s_bottom_or_its_last_row(fw, n, rows):
    """With no bar to hold it (none here), the caption is the page's first line, a row high, and the rows run from
    under it."""
    t = themed(fw)
    ui = Ui(fw)
    page = ui.make(0, 'window', 0, 0, 375, 320)  # full-bleed
    body = ui.make(page, 'view', 0, 58, 375, 262)  # Cover Flow's: from the status row's foot
    view = page_list_or(fw, page, body, b'Albums', b'None', n)
    lv, row = ui[view].parent, t['page_row']
    assert t['page_title_h'] == 0  # OLED's: the title is the bar's, when it fits
    room = fw.fn('client_h', C.c_int)() - row
    assert ui.at(lv) == (0, row, 375, rows * row if rows else room)
    assert ui[lv].props['item_height'] == row and ui[view].props['virtual_h'] == n * row and ui[view].props['yslidable'] == 1
    assert [ui[w].text for w in ui.of('hscroll_label', body)] == ['Albums'] and ui[body].visible


def page_row_detail(fw, view, index, caption, detail, click=0x1234, into=False):
    name = 'page_row_detail_into' if into else 'page_row_detail'
    return fw.fn(name, C.c_void_p, C.c_void_p, C.c_int, C.c_char_p, C.c_char_p, C.c_void_p)(view, index, caption, detail, click)


def test_a_row_with_a_detail_puts_its_caption_over_it_in_the_page_s_text_column(fw):
    t = themed(fw)
    ui = Ui(fw)
    item = page_row_detail(fw, ui.make(0, 'scroll_view'), 2, b'Song', b'3 plays')
    h = t['page_detail']
    assert ui.at(item) == (0, 2 * h, 375, h) and ui.handlers(item) == [(0x1234, 2)]  # the third of the taller rows
    caption, detail = ui.of('hscroll_label', item)
    assert (ui[caption].text, ui[detail].text) == ('Song', '3 plays')
    (cx, cy, cw, ch), (dx, dy, dw, dh) = ui.at(caption), ui.at(detail)
    x = fw.fn('page_x', C.c_int)()
    assert cx == dx == x and cw == dw == 375 - 2 * x  # page_x's text column, its margins even
    assert 2 * cy + ch < 2 * dy + dh and cy + ch <= h and dy + dh <= h  # the caption over the detail, both ending in the row
    grey = ui[detail].props['style:normal:text_color'] & 0xffffffff
    r, g, b, a = (grey >> s & 255 for s in (0, 8, 16, 24))
    assert a == 255 and r == g == b < 255  # stock's second line: an opaque grey, dimmer than the caption's white


def test_an_empty_detail_keeps_the_caption_where_it_stands_over_one(fw):
    themed(fw)
    ui = Ui(fw)
    item = page_row_detail(fw, ui.make(0, 'scroll_view'), 0, b'Song', b'')
    over = ui.of('hscroll_label', page_row_detail(fw, ui.make(0, 'scroll_view'), 0, b'Song', b'3 plays'))[0]
    caption, detail = ui.of('hscroll_label', item)
    assert ui.at(caption) == ui.at(over)  # the same text column, where it stands over a detail
    assert ui[detail].text == ''  # kept for whoever writes it


CHEVRON_GLYPH = 322  # row_into: stock's list_into glyph spans x 322 to 332 on a full-width row, as on the Library's


def test_a_row_into_another_page_ends_its_labels_before_the_chevron(fw):
    t = themed(fw)
    ui = Ui(fw)
    view = ui.make(0, 'scroll_view')
    item = page_row_detail(fw, view, 1, b'Albums', b'', into=True)
    h, x = t['page_detail'], fw.fn('page_x', C.c_int)()
    caption, detail = ui.of('hscroll_label', item)
    (cx, cy, cw, ch), (dx, _, dw, _) = ui.at(caption), ui.at(detail)
    assert cx == dx == x and 2 * cy + ch == h  # centred in the row, beside the chevron
    assert cw == dw > 0 and x + cw <= CHEVRON_GLYPH  # both labels end before its glyph
    chevron, = ui.of('image', item)
    vx, vy, vw, vh = ui.at(chevron)
    assert vx <= CHEVRON_GLYPH and CHEVRON_GLYPH + 10 <= vx + vw <= 375 and (vy, vh) == (0, h)  # the glyph in it, a row high
    assert ui[chevron].image == 'list_into' and ui[chevron].visible
    fw.fn('page_row_into', None, C.c_void_p, C.c_int, C.c_char_p, C.c_void_p)(view, 2, b'Songs', 0)
    row = ui[view].kids[-1]
    lx, ly, lw, lh = ui.at(ui.of('hscroll_label', row)[0])
    assert ui.at(row)[3] == t['page_row'] and (lx, ly, lh) == (x, 0, t['page_row']) and 0 < lw <= CHEVRON_GLYPH - x


def test_a_level_slides_in_from_the_side_it_comes_from_where_the_theme_moves_levels(fw):
    t = themed(fw)
    ui = Ui(fw)
    lv = ui.make(0, 'list_view', 30, 48, 375, 200)
    view = ui.make(lv, 'scroll_view')
    push = fw.fn('page_push', C.c_int, C.c_void_p, C.c_int)
    ms = t['motion.inplace.ms']
    if not ms:
        assert push(view, 1) == 0 and ui.at(lv)[0] == 30 and not ui.animators
        return
    assert push(view, 1) == 1 and ui.at(lv)[0] == 375  # from its rest at 0, a width to the right
    assert ui.animators == [(lv, ms, t['motion.inplace.easing'], 'x')]
    assert push(view, -1) == 1 and ui.at(lv)[0] == -375
    assert push(view, 0) == 0 and push(None, 1) == 0 and len(ui.animators) == 2
    ui.animate = False
    assert push(view, 1) == 0 and ui.at(lv)[0] == 0  # no animator: the list at x 0


# The covers' captions

class WheelAccel(C.Structure):  # patch/wheel_accel.h wheel_accel_t
    _fields_ = [('key', C.c_void_p), ('last', C.c_uint), ('run', C.c_uint), ('dir', C.c_int)]


class Th(C.Structure):
    _fields_ = [*((k, C.c_float) for k in 'pos vel goal lead lead_v rate tau ticks seen seen_lead'.split()),
                *((k, C.c_uint) for k in 'at tick_at edge_at'.split()),
                *((k, C.c_int) for k in 'raw on finger held dir dead early clicked bounced jump tick_acc rebased lean fold'.split()),
                ('fold_at', C.c_uint), *((k, C.c_float) for k in 'enter fold_off fold_vel hold_lead hold_run hold_xrun hold_xs'.split()),
                ('hold_k', C.c_int), ('arch', C.c_float), ('seen_arch', C.c_float), ('wa', WheelAccel)]


def captions(fw, scoped=0, albums=(('Abbey', 'Beatles'), ('Help', 'Beatles'))):
    t = themed(fw)
    ui = Ui(fw)
    recs = [Rec(n, artist=a) for n, a in albums]
    deque_of(fw, recs)
    c = cf(fw)
    c.albums, c.scoped, c.screen = 0x5000, scoped, COVERS
    c.slide = ui.make(0, 'slide_menu')
    c.count = len(recs) + (scoped != 0)
    for _ in range(c.count):
        ui.make(c.slide, 'image')
    c.name, c.artist = ui.make(0, 'hscroll_label'), ui.make(0, 'hscroll_label')
    return t, ui, c, fw.fn('titles', None, C.c_uint)


def test_the_captions_name_the_centre_album_and_its_artist(fw):
    _, ui, c, titles = captions(fw)
    titles(1)
    assert (ui[c.name].text, ui[c.artist].text) == ('Help', 'Beatles') and c.titled == 2
    assert ui[c.name].props['xoffset'] == 0 and ui[c.artist].props['xoffset'] == 0
    ui[c.name].text = 'stale'
    titles(1)
    assert ui[c.name].text == 'stale'  # once each time it changes
    assert 'style:normal:font_name' not in ui[c.name].props  # the captions keep the style's face


@pytest.mark.parametrize('scoped, card', ((RATED_ALBUMS, 'Sort: Rating'),))
def test_the_card_after_the_albums_is_named_for_what_it_does(fw, scoped, card):
    _, ui, c, titles = captions(fw, scoped)
    c.sort = SORT_RATING
    titles(2)
    assert (ui[c.name].text, ui[c.artist].text) == (card, '')


@pytest.mark.parametrize('state', ('covers', 'thumb', 'tracks', 'gone'))
def test_the_captions_catch_up_with_the_frame_after_the_paint(fw, state):
    _, ui, c, titles = captions(fw)
    fw.var('fx', Fx).c = 1
    c.title_timer = 7
    if state == 'thumb':
        fw.var('th', Th).on = 1
    elif state == 'tracks':
        c.screen = TRACKS
    elif state == 'gone':
        c.slide = 0
    assert fw.fn('coverflow__retitle', C.c_int, C.c_void_p)(None) == 0 and not c.title_timer
    assert ui[c.name].text == ('Help' if state == 'covers' else None)


# Home's overflow (home.c): nine rows resting on seven, OLED's fades

def overflow_rig(fw, rows=9, top=40, hidden=()):
    """Home's main list as coverflow_home_layout leaves it before home.c sizes it: the rest's box, rows 32 px."""
    themed(fw)
    ui = Ui(fw)
    home = fw.var('coverflow__home', Home)
    home.win = ui.make(ui.wm, 'window', 0, 0, 375, 320)  # the whole screen (docs/oled-design.md#full-bleed)
    home.list = ui.make(home.win, 'list_view', 0, top, 204, 224)
    view = ui.make(home.list, 'scroll_view', 0, 0, 204, 224)
    for i in range(rows):
        row = ui.make(view, 'view', 0, 32 * i, 204, 32)
        ui[row].visible = i not in hidden
    fw.mock('scroll_view_set_offset', lambda w, x, y, *_: offset(w, y))
    return ui, home, view


def content(w):
    return C.c_int.from_address(w + O['VIEW_CONTENT_H']).value


def offset(w, y=None):
    if y is not None:
        C.c_int.from_address(w + O['SCROLL_Y']).value = y
    return C.c_int.from_address(w + O['SCROLL_Y']).value


def test_home_rests_on_seven_rows_and_scrolls_the_rest(fw):
    """home_overflow: the box the rest's rows, or under OLED's pages the screen's bottom; the content as a drag
    clamps it, so it stops where the wheel does (the shown rows' foot less the rest); a kept scroll clamped."""
    box = 280  # OLED's pages: to the screen's bottom, 320 less the window's 58 px top and the list's 18 px rise
    ui, home, view = overflow_rig(fw)
    rest = fw.fn('home_rest', C.c_int, C.c_void_p, C.c_void_p)
    foot = C.c_int()
    assert rest(view, C.byref(foot)) == 7 * 32 and foot.value == 9 * 32
    assert rest(ui.make(0, 'scroll_view'), None) == 0  # another list
    offset(view, 100)
    fw.fn('home_overflow', None, C.c_void_p, C.c_int)(home.list, 320)
    assert rect(home.list)[3] == rect(view)[3] == box  # both: a resize lays out neither
    assert content(view) - box == 2 * 32 and offset(view) == 2 * 32


def test_home_scrolled_to_its_end_keeps_its_scroll_through_stock_s_relayout(fw):
    """Settings selected at Home's end, opened, then Return: Home's relayout puts stock's content back (the rows'
    foot) and AWTK clamps the scroll to it, which sat the last row on the screen's foot with the padding gone. The
    paint's home_pad sets the content again and puts back the scroll it last saw; a scroll of the user's own, with
    no relayout, stays."""
    ui, home, view = overflow_rig(fw)
    pad = fw.fn('home_pad', None, C.c_void_p)
    fw.fn('home_overflow', None, C.c_void_p, C.c_int)(home.list, 320)
    offset(view, 2 * 32)  # the wheel at Settings: as far as the rest goes
    pad(view)
    C.c_int.from_address(view + O['VIEW_CONTENT_H']).value = 9 * 32  # Return: stock's relayout
    offset(view, 9 * 32 - 280)  # and its clamp
    pad(view)
    assert content(view) - 280 == 2 * 32 and offset(view) == 2 * 32
    offset(view, 32)  # a drag up
    pad(view)
    assert offset(view) == 32
    offset(view, 2 * 32)
    pad(view)
    C.c_int.from_address(view + O['W_H']).value = 320  # the paint's other form of the view (Split's band)
    offset(view, 2 * 32 - 40)
    pad(view)
    C.c_int.from_address(view + O['VIEW_CONTENT_H']).value = 9 * 32
    offset(view, 9 * 32 - 320)
    C.c_int.from_address(view + O['W_H']).value = 280  # and back: the relayout clamped it under its 280
    offset(view, 9 * 32 - 280)
    pad(view)
    assert offset(view) == 2 * 32  # its foot where it was: the padding kept


def test_a_hidden_row_restacked_last_is_not_scrolled_to(fw):
    ui, home, view = overflow_rig(fw, hidden=(8,))
    foot = C.c_int()
    fw.fn('home_rest', C.c_int, C.c_void_p, C.c_void_p)(view, C.byref(foot))
    assert foot.value == 8 * 32
    fw.fn('home_pad', None, C.c_void_p)(view)
    assert content(view) == 8 * 32  # the box is the rest's: the content is the shown rows'


def fades(fw, scroll, sel=None, top=40, split=1, list_y=40, cols=196, hidden=()):
    """home_fades over the rig's list, on a canvas at the screen's origin: {screen row: [(x, w, alpha)]}. The theme
    is read once a test."""
    ui, home, view = overflow_rig(fw, top=top, hidden=hidden)
    fw.fn('home_overflow', None, C.c_void_p, C.c_int)(home.list, 320)
    offset(view, scroll)
    if sel is not None:
        ui[view].props['_ringnav_index'] = sel
    canvas = Canvas(fw)
    fw.mock('canvas_get_clip_rect', lambda c, r, *_: (C.c_int * 4).from_address(r).__setitem__(slice(0, 4), [0, 0, 375, 320]))
    fw.fn('home_fades', None, C.c_void_p, C.c_void_p, C.c_int, C.c_int, C.c_int, C.c_int)(canvas.p, home.list, list_y, cols, split, 0)
    rows = {}
    for colour, x, y, w, h in canvas.rects:
        assert h == 1 and colour & 0xffffff == 0  # one row of the black ground
        rows.setdefault(y, []).append((x, w, colour >> 24))
    return rows


def test_full_fades_straight_across_from_the_rest_s_foot_and_under_the_top_once_scrolled(fw):
    rows = fades(fw, 0, top=58, split=0, list_y=58, cols=375)
    assert 58 + 224 - 16 < min(rows) < 58 + 224 - 12 and all(r == [(0, 375, r[0][2])] for r in rows.values())
    alphas = [rows[y][0][2] for y in sorted(rows)]
    assert alphas == sorted(alphas) and alphas[-1] == 255 and max(rows) == 319  # into the ground by y 316
    rows = fades(fw, 32, top=58, split=0, list_y=58, cols=375, sel=8)
    # the top's 14 rows, the last of a fade that runs on above them to y 8 (list_reach_paint paints those rows)
    assert min(rows) == 58 and rows[58][0][2] > rows[64][0][2] > 0 and 72 not in rows
    rows = fades(fw, 64, top=58, split=0, list_y=58, cols=375, sel=8)
    assert max(rows) < 72  # scrolled to its end: nothing below to fade


def test_split_fades_follow_the_glass_and_never_the_selected_row(fw):
    rows = fades(fw, 0)
    assert min(rows) > 40 and not [y for y in rows if y < 40 + 224 and rows[y][-1][0] >= 80]  # the straight part from y 264
    assert rows[230][0] == (0, 8, rows[230][0][2]) and rows[230][0][2] > 0  # the corner's, higher up
    assert all(x + w <= 196 for r in rows.values() for x, w, _ in r)  # left of the art
    rows = fades(fw, 32, sel=6)  # row 6 on the screen from 40 + 192 - 32
    assert not [y for y in rows if 200 <= y < 232] and rows[0][-1] == (80, 116, 255)  # the top: the ground at the edge


def test_split_s_scrolled_rows_are_the_ground_under_the_clock(fw):
    """The clock stands over Split's list at the status row's left (navigation.c clock_place), its digits on rows 17 to
    27: a row scrolled up under it is faded into the ground by the row below them, not only at the glass's edge."""
    rows = fades(fw, 32, sel=6)
    for y in range(17, 29):  # the clock's rows and the one under them, in its columns (62 on); the old fade let
        # 45% to 67% of the row through there
        alphas = [a for x, w, a in rows[y] if x < 120 and x + w > 62]
        assert alphas and min(alphas) == 255, y
    assert all(a < 255 for x, w, a in rows[40] if x >= 62)  # the list's own top: still showing


def test_only_homes_main_list_can_be_edited_never_its_settings_list(fw):
    """Settings keeps a fixed order (user ruling, 2026-11-16): Edit Home takes Home's main list alone, not the
    Settings list a Home row opens."""
    _, ui, h, _ = home_open(fw, HOME='1')
    edit_view = fw.fn('coverflow_home_edit_view', C.c_void_p, C.c_void_p)
    assert edit_view(h.win) == h.view
    assert not edit_view(h.main) and not edit_view(None)  # only with Home on top
    ui.click(h.rows['img_settings'])
    assert ui[h.sets].visible and not edit_view(h.win)


# A page the payload builds (page_open), full-bleed as stock's (docs/oled-design.md#full-bleed).
def code_page(fw, ui, name=b'mostplayed_page'):
    """page_open's page over window_create's whole-screen window, on top."""
    fw.mock('window_create', lambda *_: ui.make(ui.wm, 'window', 0, 0, 375, 320))
    ui.top = fw.fn('page_open', C.c_void_p, C.c_char_p, C.c_void_p, C.c_void_p)(name, None, None)
    return ui.top


def test_a_code_built_page_covers_the_screen_and_its_rows_rest_under_the_status_row(fw):
    """page_open's window stays the whole 375x320 screen, its ground under the status row too, and page_list lays its
    rows out from the row's foot (bar_foot), where they rested when the window started under the row."""
    t = themed(fw)
    ui = Ui(fw)
    page = code_page(fw, ui)
    assert ui.at(page) == (0, 0, 375, 320)
    view = fw.fn('page_list', C.c_void_p, C.c_void_p, C.c_void_p, C.c_void_p, C.c_char_p, C.c_int, C.c_int)(
        page, page, None, b'Most Played', 30, 48)
    lv, title, bar = ui[view].parent, ui[page].kids[0], fw.fn('bar_foot', C.c_int, C.c_void_p)(None)
    top = bar + ui.at(title)[3]  # under the caption, a row of the page where the bar can't hold it
    assert bar == 58 and ui.at(title)[1] == bar and ui.at(lv) == (0, top, 375, 320 - top)  # to the screen's foot
    assert fw.fn('page_room', C.c_int, C.c_void_p)(page) == fw.fn('client_h', C.c_int)() == 320 - bar


def test_the_bar_paints_no_artist_header_over_a_code_built_page(fw, library):
    """A card app's artist page (addon.c, on page_open's page): the monogram's top, above the status row's foot, is
    the page's own paint on its full-bleed window; the bar paints nothing of it again."""
    ui, home, win, bar = full_bleed_home(fw, library)
    page = code_page(fw, ui, b'addon_page')
    fw.fn('hero_open', C.c_void_p, C.c_void_p, C.c_char_p, C.c_char_p, C.c_char_p)(page, b'Artist', b'', b'')
    fw.mock('canvas_get_clip_rect', lambda c, r, *_: (C.c_int * 4).from_address(r).__setitem__(slice(0, 4), [0, 0, 375, 320]))
    fw.mock('canvas_set_clip_rect', lambda *_: None)
    canvas = Canvas(fw)
    mark = {name: len(fw.calls(name)) for name in ('canvas_fill_rounded_rect', 'canvas_draw_image')}
    fw.fn('ringnav_paint_bg', C.c_int, C.c_void_p, C.c_void_p)(bar, canvas.p)
    assert not [r for r in canvas.rects if r[2] < 58]
    assert {name: len(fw.calls(name)) for name in mark} == mark


SHOW_RADIO = 2


def test_while_radio_owns_now_playing_the_pane_is_radio_s_and_ticks_only_while_it_moves(fw, mcl):
    """Approved 2026-11-20 (design/radio-tuner/radio-now-playing/pane-v6, K1): while Radio is the owner, playing or
    paused, Split's pane is Radio's (radio_pane.c) in place of the covers: no cover loaded, no pan, its own frame
    tick (radio_tick) only while the pane moves and shows (rings while Radio plays, not paused); the Library chosen
    again, the covers come back and the tick stops."""
    from test_radio_tuner import Rt
    _, ui, h, _ = home_open(fw, HOME='0')
    home = drifting(fw)
    fw.mock('window_manager_get_top_window', lambda *_: h.win)
    rt = Rt()
    rt.playing, rt.audio, rt.feed, rt.freq = 1, 999, -1, 9310
    rt_var = fw.var('radio_tuner__rt', C.c_void_p)
    rt_var.value = C.addressof(rt)
    tick = address(fw, 'coverflow__radio_tick')
    try:
        loads = len(fw.calls('widget_load_image'))
        fw.fn('coverflow_home_art', None, C.c_void_p)(h.win)
        assert home.show == SHOW_RADIO and home.sl[0].tw == 0 and len(fw.calls('widget_load_image')) == loads
        assert not ui.timer(address(fw, 'coverflow__home_drift')) and ui.timer(tick)
        rt.playing = 0  # paused: the rings stand, nothing ticks once the way in is over
        fw.var('coverflow__home', Home).show = SHOW_RADIO
        assert fw.fn('coverflow__radio_tick', C.c_int, C.c_void_p)(None) in (0, 8)
        fw.fn('rp_free', None)()  # no way in pending: paused, still
        assert fw.fn('coverflow__radio_tick', C.c_int, C.c_void_p)(None) == 0
        assert fw.var('coverflow__home', Home).show == SHOW_RADIO
    finally:
        rt_var.value = None
    fw.fn('coverflow_home_art', None, C.c_void_p)(h.win)
    assert home.show in (SHOW_NOW, SHOW_LIB) and len(fw.calls('widget_load_image')) > loads  # the covers again


class Folders:
    """The card's folders as the art look reads them: __xstat gives each a time (o32: st_mtime word 16, its
    nanoseconds 17), opendir and readdir list its names (host.py's struct dirent: d_name at 11), access counts each
    file asked for. reads counts the folders listed."""
    def __init__(self, fw, names):
        self.names, self.time, self.reads, self.asked, self.keep, self.left = names, 1, 0, [], [], []
        fw.mock('fw___xstat', self.stat)
        fw.mock('fw_opendir', self.opendir)
        fw.mock('fw_readdir', self.readdir)
        fw.mock('fw_closedir', lambda *_: 0)
        fw.mock('fw_access', lambda path, *_: self.asked.append(C.string_at(path)) or -1)
        fw.mock('tk_str_start_with', lambda s, p, *_: C.string_at(s).startswith(C.string_at(p)))  # libc's, as stock's

    def stat(self, version, path, info, *_):
        if C.string_at(path).decode() not in self.names:
            return -1
        words = (C.c_uint * 36).from_address(info)
        words[16], words[17] = 1800000000, self.time
        return 0

    def opendir(self, path, *_):
        self.left = list(self.names.get(C.string_at(path).decode(), ()))
        self.reads += 1
        return 0x77

    def readdir(self, d, *_):
        if not self.left:
            return 0
        name = self.left.pop(0).encode()
        ent = C.create_string_buffer(268)
        C.memmove(C.addressof(ent) + 11, name, len(name))
        self.keep.append(ent)
        return C.addressof(ent)


# The owner's Q2 (2026-11-24): a file looked up in a 5,000-name exFAT folder took about 12 ms, and the look asked the
# card for two an album. The look reads the art cache and the app's covers folder once each and answers every album
# from their names: cached (0), a cover to build from (1), nothing to build from (2); a folder is read again only once
# its time moves.
def test_the_art_look_reads_each_folder_once_and_answers_every_album_from_its_names(fw):
    key = fw.fn('album_key', C.c_uint, C.c_void_p)
    state = fw.fn('coverflow__art_state', C.c_int, C.c_void_p, C.c_char_p, C.c_uint)
    covers = '/mnt/mmc/.subsonic/covers'
    made, cover, none = (Rec(a, f'/mnt/mmc/.subsonic/cat/{a}/s1.flac', 'Art') for a in ('alb1', 'alb2', 'alb3'))
    card = Folders(fw, {'/mnt/mmc/.coverflow': [f'{key(made.p):08x}.jpg', 'building', f'{key(cover.p):08x}.jpg.tmp'],
                        covers: ['alb1.jpg', 'alb2.jpg', 'alb9.jpg.tmp']})
    path = C.create_string_buffer(512)
    assert [state(r.p, path, 512) for r in (made, cover, none)] == [0, 1, 2]
    assert card.reads == 2 and card.asked == []
    assert [state(r.p, path, 512) for r in (made, cover, none)] == [0, 1, 2] and card.reads == 2
    card.names[covers].append('alb3.jpg')  # the app fetched it: the folder's time moves
    card.time += 1
    assert state(none.p, path, 512) == 1 and card.reads == 4 and card.asked == []


# A folder that can't be read (no such folder, or no memory for its names) leaves each album to the card's answer.
def test_the_art_look_asks_the_card_when_a_folder_can_t_be_read(fw):
    state = fw.fn('coverflow__art_state', C.c_int, C.c_void_p, C.c_char_p, C.c_uint)
    card = Folders(fw, {})
    r = Rec('alb1', '/mnt/mmc/.subsonic/cat/alb1/s1.flac', 'Art')
    assert state(r.p, C.create_string_buffer(512), 512) == 2
    assert card.reads == 0 and len(card.asked) == 2
