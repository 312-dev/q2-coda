"""The smaller patch files on their device path: Night Shift, Videos' places, lyrics' text, q2video's samples, Crossfade's page, the EQ page's
files and rules, and Up Next."""
import ctypes as C
import math
import random
import re
import struct
import subprocess

import pytest
from conftest import ROOT, block

CENTER, PREV, NEXT, PLAY, RETURN, BACK_BTN, FWD_BTN = 218, 172, 173, 171, 170, 222, 223
FNV = 2166136261
r = C.byref

libc = C.CDLL(None)
libc.malloc.restype, libc.malloc.argtypes = C.c_void_p, [C.c_size_t]
libc.calloc.restype, libc.calloc.argtypes = C.c_void_p, [C.c_size_t, C.c_size_t]
libc.free.argtypes = [C.c_void_p]


def exact(ctype, n):
    """n zeroed ctypes in a calloc of exactly their size, so the sanitizer sees an overrun."""
    return (ctype * n).from_address(libc.calloc(n, C.sizeof(ctype)))


class Exact:
    def __init__(self, data):
        self.p = libc.malloc(len(data) or 1)
        C.memmove(self.p, bytes(data), len(data))

    def __enter__(self):
        return self.p

    def __exit__(self, *_):
        libc.free(self.p)


def config(fw, values=None):
    """config.ini: toolsReadConfig(path, section, key, out, default), and write_int_config's calls."""
    values = dict(values or {})

    def read(path, section, key, out, default, *_):
        v = values.get(C.string_at(key).decode(), C.string_at(default).decode())
        C.memmove(out, str(v).encode() + b'\0', len(str(v)) + 1)
    fw.mock('toolsReadConfig', read)
    written = []
    fw.mock('write_int_config', lambda v, s, k, *_: written.append((C.string_at(k).decode(), C.c_int(v).value)))
    return written


class Card:
    """fopen and friends on a dict."""
    def __init__(self, fw, files=None):
        self.files, self.open, self.unlinked, self.bad_rename, self.bad_close = dict(files or {}), {}, [], False, False
        for name in 'fopen fread fwrite fseek ftell rename unlink'.split():
            fw.mock('fw_' + name, getattr(self, name))
        fw.mock('fw_fclose', lambda *_: self.bad_close)
        fw.mock('fw_fflush', lambda *_: 0)
        fw.mock('fw_fsync', lambda *_: 0)
        fw.mock('fw_access', lambda path, *_: C.string_at(path).decode('latin1') not in self.files)

    def fopen(self, path, mode, *_):
        name, write = C.string_at(path).decode('latin1'), C.string_at(mode)[:1] == b'w'
        if not write and name not in self.files:
            return 0
        handle = 0x1000 + len(self.open)
        self.open[handle] = [name, 0]
        if write:
            self.files[name] = b''
        return handle

    def fread(self, buf, size, n, f, *_):
        name, at = self.open[f]
        data = self.files[name][at:at + size * n]
        C.memmove(buf, data, len(data))
        self.open[f][1] = at + len(data)
        return len(data) // size

    def fwrite(self, buf, size, n, f, *_):
        self.files[self.open[f][0]] += C.string_at(buf, size * n)
        return n

    def fseek(self, f, off, whence, *_):
        base = {0: 0, 1: self.open[f][1], 2: len(self.files[self.open[f][0]])}[whence]
        if base + off < 0:
            return -1
        self.open[f][1] = base + off
        return 0

    def ftell(self, f, *_):
        return self.open[f][1]

    def rename(self, a, b, *_):
        if self.bad_rename:
            return -1
        self.files[C.string_at(b).decode('latin1')] = self.files.pop(C.string_at(a).decode('latin1'))
        return 0

    def unlink(self, path, *_):
        self.unlinked.append(C.string_at(path).decode())
        self.files.pop(self.unlinked[-1], None)
        return 0


# Night Shift

class Ns(C.Structure):
    _fields_ = [*((k, C.c_int) for k in 'read on kelvin built dark saved_on saved_kelvin'.split()),
                ('lcd', C.c_void_p), ('end', C.c_void_p), ('g', C.c_ubyte * 256), ('b', C.c_ubyte * 256)]


class Lcd:
    """The screen's lcd as far as nightshift.c reads it, its frame buffer w x h in an exact allocation."""
    def __init__(self, w=4, h=3, line=None, rect=None):
        self.raw = C.create_string_buffer(0x200)
        self.w, self.h = w, h
        self.fb = exact(C.c_uint, w * h)
        struct.pack_into('<Q', self.raw, 0x110, C.addressof(self.fb))
        self.ints(0xb0, w, h)
        self.ints(0x124, w * 4 if line is None else line, 3)  # LCD_FORMAT: BGRA8888
        self.dirty(*(rect or (0, 0, w, h)))
        self.p = C.addressof(self.raw)

    def ints(self, off, *values):
        struct.pack_into(f'<{len(values)}i', self.raw, off, *values)

    def dirty(self, *rect):
        self.ints(0xf8, *rect)

    def pixels(self, value):
        self.fb[:] = [value] * (self.w * self.h)

    @property
    def end_frame(self):
        return struct.unpack_from('<Q', self.raw, 0x7c)[0]

    @end_frame.setter
    def end_frame(self, value):
        struct.pack_into('<Q', self.raw, 0x7c, value)


def ns(fw):
    return fw.var('nightshift__ns', Ns)


def warm(fw, lcd, kelvin=3400):
    s = ns(fw)
    s.kelvin = kelvin
    fw.fn('nightshift__warm', None, C.c_void_p)(lcd.p)
    return list(lcd.fb)


def helland(kelvin):
    t = kelvin / 100
    return 99.4708025861 * math.log(t) - 161.1195681661, 138.5177312231 * math.log(t - 10) - 305.0447927307


def test_each_temperature_is_helland_s_black_body_fit(fw):
    lcd = Lcd(1, 1)
    for k in range(2000, 5001, 100):
        lcd.pixels(0xffffffff)
        c = warm(fw, lcd, k)[0]
        green, blue = helland(k)
        assert abs((c >> 8 & 255) - green) <= 1, k
        assert abs((c & 255) - blue) <= 1, k
        assert c >> 16 == 0xf7ff, k  # red stays


def test_cooler_keeps_more_of_green_and_blue(fw):
    lcd = Lcd(1, 1)
    seen = []
    for k in range(2000, 5001, 100):
        lcd.pixels(0xffffffff)
        seen.append(warm(fw, lcd, k)[0])
    assert all((a >> 8 & 255) < (b >> 8 & 255) and (a & 255) < (b & 255) for a, b in zip(seen, seen[1:]))


def test_each_channel_scales_rounded_and_red_is_kept(fw):
    lcd = Lcd(4, 1)
    lcd.fb[:] = [0xff102030, 0xff000000, 0x00808080, 0x7fffffff]
    out = warm(fw, lcd, 2000)
    gs, bs = 137, 14  # NS_WARM's
    for before, after in zip([0xff102030, 0xff000000, 0x00808080, 0x7fffffff], out):
        assert after >> 24 == 0xf7 and after >> 16 & 255 == before >> 16 & 255
        assert after >> 8 & 255 == ((before >> 8 & 255) * gs + 127) // 255
        assert after & 255 == ((before & 255) * bs + 127) // 255


def test_a_warmed_pixel_is_not_warmed_twice_but_one_drawn_again_is(fw):
    lcd = Lcd(2, 1)
    lcd.pixels(0xffffffff)
    once = warm(fw, lcd)
    assert warm(fw, lcd) == once
    lcd.fb[0] = 0xffffffff
    assert warm(fw, lcd) == once


def test_only_the_frame_s_rect_is_touched(fw):
    lcd = Lcd(4, 3, rect=(1, 1, 2, 1))
    lcd.pixels(0xffffffff)
    out = warm(fw, lcd)
    changed = [i for i in range(12) if out[i] != 0xffffffff]
    assert changed == [5, 6]


@pytest.mark.parametrize('rect, want', (
    ((-2, -1, 3, 2), [0]), ((3, 2, 9, 9), [11]), ((0, 0, 99, 99), list(range(12))), ((3, 2, 1, 1), [11]),
    ((4, 0, 1, 1), []), ((0, 3, 1, 1), []), ((0, 0, 0, 3), []), ((0, 0, 3, 0), []), ((-5, 0, 3, 3), []), ((2, 1, -1, 1), [])))
def test_a_rect_past_the_screen_is_clipped(fw, rect, want):
    lcd = Lcd(4, 3, rect=rect)
    lcd.pixels(0xffffffff)
    out = warm(fw, lcd)
    assert [i for i in range(12) if out[i] != 0xffffffff] == want


def test_another_layout_is_left_as_drawn(fw):
    lcd = Lcd(4, 3, line=4 * 4 + 4)
    lcd.pixels(0xffffffff)
    assert warm(fw, lcd) == [0xffffffff] * 12
    assert ns(fw).built == 0


def test_no_frame_buffer_is_nothing(fw):
    lcd = Lcd(1, 1)
    struct.pack_into('<Q', lcd.raw, 0x110, 0)
    warm(fw, lcd)
    assert ns(fw).built == 0


def test_the_scales_are_built_once_a_temperature(fw):
    lcd = Lcd(1, 1)
    s = ns(fw)
    warm(fw, lcd, 3000)
    assert s.built == 3000
    s.g[255] = 1  # a table it won't build again
    lcd.pixels(0xffffffff)
    assert warm(fw, lcd, 3000)[0] >> 8 & 255 == 1
    lcd.pixels(0xffffffff)
    assert warm(fw, lcd, 3100)[0] >> 8 & 255 != 1


def test_night_end_warms_then_runs_stock_s(fw):
    lcd = Lcd(1, 1)
    lcd.pixels(0xffffffff)
    seen = []
    stock = C.CFUNCTYPE(C.c_int, C.c_void_p)(lambda p: (seen.append((p, lcd.fb[0] >> 24)), 42)[1])
    s = ns(fw)
    s.end, s.kelvin = C.cast(stock, C.c_void_p).value, 3000
    assert fw.fn('nightshift__night_end', C.c_int, C.c_void_p)(lcd.p) == 42
    assert seen == [(lcd.p, 0xf7)]


@pytest.mark.parametrize('on, dark, want', ((0, 0, 0), (0, 1, 0), (1, 0, 1), (1, 1, 1), (2, 0, 0), (2, 1, 1)))
def test_lit_is_on_or_auto_in_its_hours(fw, on, dark, want):
    s = ns(fw)
    s.on, s.dark = on, dark
    assert fw.fn('nightshift__lit', C.c_int)() == want


def canvas(lcd):
    raw = C.create_string_buffer(0x40)
    struct.pack_into('<Q', raw, 0x38, lcd.p if lcd else 0)
    return raw


def test_night_paint_takes_the_screen_s_end_frame_once_on(fw):
    paint = fw.fn('night_paint', None, C.c_void_p)
    lcd, stock = Lcd(), 0x123456
    lcd.end_frame = stock
    s = ns(fw)
    paint(canvas(lcd))
    assert lcd.end_frame == stock and s.lcd is None  # off: a flag test
    s.on = 1
    paint(canvas(lcd))
    assert s.lcd == lcd.p and s.end == stock and lcd.end_frame == C.cast(fw.lib.nightshift__night_end, C.c_void_p).value
    hooked = lcd.end_frame
    paint(canvas(lcd))
    assert s.end == stock and lcd.end_frame == hooked  # never its own hook as stock's


@pytest.mark.parametrize('field', ('mode', 'format', 'end', 'lcd'))
def test_night_paint_leaves_what_is_not_the_screen(fw, field):
    lcd = Lcd()
    lcd.end_frame = 0x123456
    if field == 'mode':
        lcd.ints(0xcc, 1)  # a window animation's
    if field == 'format':
        lcd.ints(0x128, 2)
    if field == 'end':
        lcd.end_frame = 0
    s = ns(fw)
    s.on = 1
    fw.fn('night_paint', None, C.c_void_p)(canvas(None if field == 'lcd' else lcd))
    assert s.lcd is None


def test_night_paint_with_no_canvas(fw):
    s = ns(fw)
    s.on = 1
    fw.fn('night_paint', None, C.c_void_p)(None)
    assert s.lcd is None


def night(fw, values=None):
    written = config(fw, values)
    fw.mock('window_manager', lambda *_: 0x9000)
    on, kelvin = fw.fn('night_on', C.c_int), fw.fn('night_kelvin', C.c_int)
    return on, kelvin, fw.fn('night_set', None, C.c_int, C.c_int), written


def test_off_gives_stock_s_end_frame_back(fw):
    _, _, night_set, _ = night(fw, {'NIGHTSHIFT': 1})
    lcd = Lcd()
    lcd.end_frame = 0x123456
    night_set(1, 3000)
    fw.fn('night_paint', None, C.c_void_p)(canvas(lcd))
    assert lcd.end_frame != 0x123456
    night_set(0, 3000)
    assert lcd.end_frame == 0x123456 and ns(fw).lcd is None
    assert fw.calls('widget_invalidate_force')[-1][1][0] == 0x9000


def test_auto_outside_its_hours_is_off_too(fw):
    _, _, night_set, _ = night(fw)
    lcd = Lcd()
    lcd.end_frame = 0x123456
    night_set(1, 3000)
    fw.fn('night_paint', None, C.c_void_p)(canvas(lcd))
    night_set(2, 3000)
    assert lcd.end_frame == 0x123456


@pytest.mark.parametrize('stored, want', (('3000', 3000), ('2000', 2000), ('5000', 5000), ('1900', 3400), ('5100', 3400), ('3050', 3400),
                                          ('x', 3400), ('', 3400), ('0', 3400)))
def test_a_saved_temperature_must_be_a_step_in_range(fw, stored, want):
    _, kelvin, _, _ = night(fw, {'NIGHTK': stored})
    assert kelvin() == want


@pytest.mark.parametrize('stored, want', (('0', 0), ('1', 1), ('2', 2), ('3', 0), ('11', 0), ('', 0)))
def test_a_saved_mode_is_off_on_or_auto(fw, stored, want):
    on, _, _, _ = night(fw, {'NIGHTSHIFT': stored})
    assert on() == want


def test_the_settings_are_read_once(fw):
    on, kelvin, _, _ = night(fw, {'NIGHTSHIFT': 1})
    on(), kelvin(), on()
    assert len(fw.calls('toolsReadConfig')) == 2


def test_the_clock_crossing_auto_s_hours_shows_at_once(fw):
    on, _, _, _ = night(fw, {'NIGHTSHIFT': 2})
    on()
    clock = fw.fn('night_clock', None, C.c_int)
    s = ns(fw)
    s.dark = 0
    before = len(fw.calls('widget_invalidate_force'))
    clock(0)
    assert len(fw.calls('widget_invalidate_force')) == before
    clock(1)
    assert s.dark == 1 and len(fw.calls('widget_invalidate_force')) == before + 1


def test_the_clock_leaves_a_fixed_mode_alone(fw):
    on, _, _, _ = night(fw, {'NIGHTSHIFT': 1})
    on()
    ns(fw).dark = 0
    fw.fn('night_clock', None, C.c_int)(1)
    assert not fw.calls('widget_invalidate_force')


def test_the_save_writes_only_what_changed(fw):
    _, _, night_set, written = night(fw, {'NIGHTSHIFT': 1, 'NIGHTK': '3000'})
    save = fw.fn('night_save', None)
    save()
    assert written == []
    night_set(2, 3000)
    save()
    assert written == [('NIGHTSHIFT', 2)]
    night_set(2, 2500)
    save()
    assert written == [('NIGHTSHIFT', 2), ('NIGHTK', 2500)]
    save()
    assert len(written) == 2


# Videos: extensions

def video_ext(fw):
    return fw.fn('video_ext', C.c_int, C.c_void_p)


VIDEOS = ('mp4', 'm4v', 'mkv', 'avi', 'mov', 'mpg')


def test_video_ext_by_the_extension_case_aside(fw):
    ext = video_ext(fw)
    for e in VIDEOS:
        assert ext(b'.' + e.encode()) == 1 and ext(b'.' + e.upper().encode()) == 1
    for e in (b'.', b'.t', b'.txt', b'.epub', b'.mp', b'.mp44', b'.mpeg', b'.flac', b'.mp4 '):
        assert ext(e) == 0, e
    assert ext(None) == 0


def test_video_ext_hostile_sweep(fw):
    ext = video_ext(fw)
    rng = random.Random(1)
    for _ in range(2000):
        name = bytes(rng.choices(b'.txTmp4EPUBkvaiogns\xff', k=rng.randrange(0, 10))).replace(b'\0', b'x')
        dot = name.rfind(b'.')
        if dot >= 0:
            tail = name[dot + 1:].lower()
            with Exact(name[dot:] + b'\0') as p:
                assert ext(p) == (tail.decode('latin1') in VIDEOS), name


# Lyrics' text

def test_utf8_char_is_utf8_else_latin1(fw):
    f = fw.fn('utf8_char', C.c_uint, C.c_void_p, C.c_uint, C.c_void_p)

    def chars(data):
        i, out = C.c_uint(0), []
        with Exact(data) as p:
            while i.value < len(data):
                out.append(f(p, len(data), r(i)))
        return out
    assert chars('aé€😀'.encode()) == [ord(c) for c in 'aé€😀']
    assert chars(b'\xe9a') == [0xe9, 0x61]  # Latin-1
    assert chars(b'\xe2\x82') == [0xe2, 0x82]  # cut by the end
    assert chars(b'\xf0\x9f\x98') == [0xf0, 0x9f, 0x98]
    assert chars(b'\xff\xf8') == [0xff, 0xf8]


def test_utf8_char_hostile_sweep(fw):
    rng = random.Random(8)
    f = fw.fn('utf8_char', C.c_uint, C.c_void_p, C.c_uint, C.c_void_p)
    for _ in range(2000):
        data = rng.randbytes(rng.randrange(1, 16))
        i = C.c_uint(0)
        with Exact(data) as p:
            while i.value < len(data):
                before = i.value
                assert f(p, len(data), r(i)) <= 0x1fffff and before < i.value <= len(data)


# Videos: the places

class Mark(C.Structure):
    _fields_ = [('key', C.c_uint), ('pos', C.c_uint)]


MARKS_FILE = '/mnt/data/ringnav-books'


def marks(fw):
    return fw.var('videos__marks', Mark * 64)


def test_a_mark_goes_first_and_the_oldest_falls_off(fw):
    Card(fw)
    mark = fw.fn('videos__mark', None, C.c_uint, C.c_uint)
    m = marks(fw)
    for key in range(1, 66):
        mark(key, key * 10)
    assert (m[0].key, m[63].key) == (65, 2)  # 64 kept, the first gone


def test_a_video_played_again_moves_to_the_front_and_keeps_the_rest(fw):
    Card(fw)
    mark = fw.fn('videos__mark', None, C.c_uint, C.c_uint)
    m = marks(fw)
    for key in (1, 2, 3):
        mark(key, 0)
    mark(1, 99)
    assert [(m[k].key, m[k].pos) for k in range(4)] == [(1, 99), (3, 0), (2, 0), (0, 0)]


def test_a_mark_is_written_whole(fw):
    card = Card(fw)
    fw.fn('videos__mark', None, C.c_uint, C.c_uint)(7, 8)
    assert len(card.files[MARKS_FILE]) == 512 and card.files[MARKS_FILE][:8] == struct.pack('<II', 7, 8)


def test_a_mark_keeps_the_places_already_saved(fw):
    """The file is read before the mark goes in: the places saved stay, books' as well as videos'."""
    card = Card(fw, {MARKS_FILE: struct.pack('<IIII', 5, 50, 6, 60) + bytes(496)})
    fw.fn('videos__mark', None, C.c_uint, C.c_uint)(7, 8)
    assert card.files[MARKS_FILE][:24] == struct.pack('<IIIIII', 7, 8, 5, 50, 6, 60)


class Vid(C.Structure):
    _fields_ = [*((k, C.c_int) for k in 'paused volume gain pid sock seek'.split()),
                *((k, C.c_uint) for k in 'polled control_at seek_at key at paused_at'.split())]


def video(fw):
    return fw.var('videos__vid', Vid)


def test_video_click_only_plays_a_video_file(fw):
    Card(fw)
    play = fw.fn('video_click', C.c_int, C.c_char_p)
    fw.mock('fw_fork', lambda *_: 77)
    fw.mock('fw_socket', lambda *_: 9)
    assert not play(None) and not play(b'noslash.mp4') and not play(b'/m/a.mp3') and not play(b'/m/.hidden.mp4')
    assert video(fw).pid == 0
    assert play(b'/m/a.mp4') == 1 and video(fw).pid == 77 and video(fw).sock == 9


def test_a_video_starts_from_its_mark(fw):
    g = fw.fn('fnv', C.c_uint, C.c_uint, C.c_char_p)
    key = g(FNV, b'/m/a.mp4')
    card = Card(fw, {MARKS_FILE: struct.pack('<II', key, 321) + bytes(504)})
    fw.mock('fw_fork', lambda *_: 77)
    assert fw.fn('video_click', C.c_int, C.c_char_p)(b'/m/a.mp4') == 1
    assert card.files['/tmp/q2video.place'] == struct.pack('<I', 321) and video(fw).key == key


def test_a_second_video_waits_for_the_first(fw):
    Card(fw)
    fw.mock('fw_fork', lambda *_: 77)
    video(fw).pid = 5
    fw.fn('video_click', C.c_int, C.c_char_p)(b'/m/a.mp4')
    assert video(fw).pid == 5 and not fw.calls('fw_fork')


def stopped(fw, place):
    card = Card(fw, {'/tmp/q2video.place': place} if place is not None else {})
    fw.mock('fw_waitpid', lambda *_: 1234)
    v = video(fw)
    v.pid, v.key = 1234, 66
    fw.fn('video_poll', None)()
    return v, card


def test_a_video_s_end_keeps_its_place(fw):
    v, card = stopped(fw, struct.pack('<I', 500))
    assert v.pid == 0
    assert card.files[MARKS_FILE][:8] == struct.pack('<II', 66, 500)


def test_a_video_never_resumed_takes_no_slot(fw):
    _, card = stopped(fw, struct.pack('<I', 0))
    assert MARKS_FILE not in card.files


def test_a_resumed_video_stopped_near_its_start_forgets_its_place(fw):
    card = Card(fw, {'/tmp/q2video.place': struct.pack('<I', 0)})
    fw.mock('fw_waitpid', lambda *_: 1234)
    v = video(fw)
    v.pid, v.key, v.at = 1234, 66, 300
    fw.fn('video_poll', None)()
    assert card.files[MARKS_FILE][:8] == struct.pack('<II', 66, 0)


def test_a_paused_video_quits_after_30_s(fw):
    Card(fw)
    v = video(fw)
    v.pid, v.paused = 1, 1
    now = [100000]
    fw.mock('time_now_ms', lambda *_: now[0])
    poll = fw.fn('video_poll', None)
    v.control_at = now[0]
    poll()
    assert v.paused_at == 100000 and not fw.calls('fw_sendto')
    now[0] += 29599
    poll()
    assert not fw.calls('fw_sendto')
    now[0] += 401  # exactly 30 s since the pause began, a poll due
    poll()
    assert fw.calls('fw_sendto')[0][1][1:3][1] == 1 and C.string_at(fw.calls('fw_sendto')[0][1][1], 1) == b'q'


def test_a_key_restarts_the_pause_s_30_s(fw):
    v = video(fw)
    v.paused_at = 5
    fw.fn('video_key', None, C.c_uint)(0)
    assert v.paused_at == 0


@pytest.mark.parametrize('key, seek, volume, maxvol, want', (
    (NEXT, 0, 50, 100, 51), (PREV, 0, 50, 100, 49), (NEXT, 0, 100, 100, 100), (NEXT, 0, 100, 120, 100), (NEXT, 0, 40, 40, 40),
    (PREV, 0, 0, 100, 0), (PREV, 0, 60, 40, 59), (NEXT, 1, 50, 100, 50), (PREV, 1, 50, 100, 50)))
def test_the_wheel_is_the_volume_up_to_its_limits_unless_seeking(fw, key, seek, volume, maxvol, want):
    written = config(fw)
    video(fw).seek = seek
    fw.var('g_volume', C.c_ubyte).value = volume
    fw.var('g_maxvolume', C.c_ubyte).value = maxvol
    fw.fn('video_key', None, C.c_uint)(key)
    assert fw.var('g_volume', C.c_ubyte).value == want
    assert written == ([('VOLUME', want)] if want != volume else [])


@pytest.mark.parametrize('stored, want', ((0, 1), (1, 0)))
def test_centre_toggles_seeking(fw, stored, want):
    video(fw).seek = stored
    fw.fn('video_key', None, C.c_uint)(CENTER)
    assert video(fw).seek == want


def test_play_flips_the_pause_at_once(fw):
    fw.mock('time_now_ms', lambda *_: 5000)
    fw.fn('video_key', None, C.c_uint)(PLAY)
    assert video(fw).paused == 1 and video(fw).control_at == 5000
    fw.fn('video_key', None, C.c_uint)(PLAY)
    assert video(fw).paused == 0


# Crossfade's page

class Xf(C.Structure):
    _fields_ = [*((k, C.c_int) for k in 'read on done'.split()), ('key', C.c_int * 8),
                *((k, C.c_int) for k in 'stops counted ctx sliding unsaved setting rs_read rs_min mono_read mono swap_read swap'.split()),
                *((k, C.c_void_p) for k in 'flag mono_flag swap_flag press_file swap_label mono_label page sw view slider dec add label'.split()),
                ('press', C.c_uint), ('press_at', C.c_uint), ('xfade_label', C.c_void_p), ('shown', C.c_int)]


def xf(fw):
    return fw.var('crossfade__xf', Xf)


@pytest.mark.parametrize('ctx, want', ((0, 0), (1, 0), (2, 0), (3, 1), (4, 0)))
def test_only_night_shifts_page_has_the_switch(fw, ctx, want):
    xf(fw).ctx = ctx
    assert fw.fn('crossfade__switched', C.c_int)() == want


# Smart Crossfade's one switch (crossfade.c settings): Q2POD XFADE, which the length picker before it saved too, so a
# config with Crossfade on at any length reads On; XFADESEC is not read.
@pytest.mark.parametrize('stored, on', (({'XFADE': '1', 'XFADESEC': '7'}, 1), ({'XFADE': '1', 'XFADESEC': '15'}, 1),
                                        ({'XFADE': '0', 'XFADESEC': '7'}, 0), ({'XFADESEC': '7'}, 0), ({}, 0), ({'XFADE': '1'}, 1)))
def test_smart_crossfade_reads_the_crossfade_switch_saved_before_it(fw, stored, on):
    config(fw, stored)
    fw.fn('crossfade__settings', None)()
    assert xf(fw).on == on


@pytest.mark.parametrize('stored, want', (('0', 0), ('20', 20), ('25', 25), ('120', 120), ('19', 30), ('15', 30), ('125', 30), ('33', 30), ('', 30), ('00', 30), ('x', 30)))
def test_resume_minutes_saved(fw, stored, want):
    config(fw, {'RESUMEMIN': stored})
    assert fw.fn('resume_min', C.c_int)() == want


def test_resume_minutes_are_read_once(fw):
    config(fw, {'RESUMEMIN': '60'})
    resume = fw.fn('resume_min', C.c_int)
    resume(), resume()
    assert len(fw.calls('toolsReadConfig')) == 1


def test_a_config_line_of_255_bytes_fits_every_reader(fw):
    """toolsReadConfig takes no size and copies a whole line, up to 255 bytes (its fgets): each reader's
    buffer holds one (a shorter one fails under ASan here)."""
    def line(digits): return digits + ' ' * (255 - len(digits))
    config(fw, {'RESUMEMIN': line('45')})
    assert fw.fn('resume_min', C.c_int)() == 45
    _, kelvin, _, _ = night(fw, {'NIGHTK': line('3000')})
    assert kelvin() == 3000


def page(fw, ctx, values=None):
    written = config(fw, values)
    fw.mock('window_manager', lambda *_: 0x9000)
    fw.fn('crossfade__settings', None)()
    x = xf(fw)
    x.ctx = ctx
    return x, written, fw.fn('crossfade__set_value', None, C.c_int, C.c_int)


def test_a_value_it_already_has_saves_nothing(fw):
    x, written, set_value = page(fw, 2, {'RESUMEMIN': '30'})
    set_value(30, 0)
    assert written == []


def test_a_drag_saves_at_its_release(fw):
    x, written, set_value = page(fw, 2, {'RESUMEMIN': '30'})
    set_value(40, 1)
    set_value(45, 1)
    assert x.rs_min == 45 and written == [] and x.unsaved == 1
    set_value(45, 0)  # the release, on a value it has
    assert written == [('RESUMEMIN', 45)] and x.unsaved == 0


def test_a_drag_never_released_saves_as_the_page_closes(fw):
    x, written, set_value = page(fw, 2, {'RESUMEMIN': '30'})
    x.page = 0x7000
    x.sw = x.slider = x.label = 0x7100
    set_value(40, 1)
    assert written == []
    assert fw.fn('crossfade__gone', C.c_int, C.c_void_p, C.c_void_p)(0x7000, None) == 0
    assert written == [('RESUMEMIN', 40)] and x.page is None and x.ctx == 0


def test_another_page_s_close_changes_nothing(fw):
    x, _, _ = page(fw, 2)
    x.page = 0x7000
    fw.fn('crossfade__gone', C.c_int, C.c_void_p, C.c_void_p)(0x7001, None)
    assert x.page == 0x7000 and x.ctx == 2


@pytest.mark.parametrize('n, want', ((0, 0), (15, 0), (19, 0), (20, 20), (22, 20), (23, 25), (118, 120), (120, 120), (125, 120), (999, 120)))
def test_the_resume_slider_runs_off_then_fives(fw, n, want):
    x, written, set_value = page(fw, 2, {'RESUMEMIN': '60'})
    set_value(n, 0)
    assert x.rs_min == want and written == [('RESUMEMIN', want)]


def test_the_resume_slider_on_its_value_is_nothing(fw):
    _, written, set_value = page(fw, 2, {'RESUMEMIN': '60'})
    set_value(60, 0)
    assert written == []


@pytest.mark.parametrize('ctx, stored, want', ((2, {'RESUMEMIN': '0'}, 15), (2, {'RESUMEMIN': '45'}, 45),
                                               (3, {'NIGHTK': '5000'}, 0), (3, {'NIGHTK': '2000'}, 30), (1, {'WHEELSENSITIVITY': '120'}, 120)))
def test_the_slider_s_value_for_each_page(fw, ctx, stored, want):
    page(fw, ctx, stored)
    assert fw.fn('crossfade__value', C.c_int)() == want


def night_page(fw, on=0, kelvin='3400'):
    x, written, set_value = page(fw, 3, {'NIGHTSHIFT': on, 'NIGHTK': kelvin})
    return x, written, set_value, fw.fn('night_on', C.c_int), fw.fn('night_kelvin', C.c_int)


def test_night_shift_a_move_turns_it_on_and_auto_stays(fw):
    _, written, set_value, on, kelvin = night_page(fw, 0)
    set_value(10, 0)
    assert (on(), kelvin()) == (1, 4000)
    assert written == [('NIGHTSHIFT', 1), ('NIGHTK', 4000)]


def test_night_shift_keeps_auto_through_a_move(fw):
    _, _, set_value, on, kelvin = night_page(fw, 2)
    set_value(5, 0)
    assert (on(), kelvin()) == (2, 4500)


def test_night_shift_the_slider_s_ends(fw):
    _, _, set_value, _, kelvin = night_page(fw, 1)
    set_value(-5, 0)
    assert kelvin() == 5000
    set_value(99, 0)
    assert kelvin() == 2000


def test_the_switch_cycles_night_shift_off_on_auto(fw):
    _, written, _, on, _ = night_page(fw, 0)
    toggle = fw.fn('crossfade__toggle', C.c_int, C.c_void_p, C.c_void_p)
    seen = []
    for _ in range(3):
        toggle(None, None)
        seen.append(on())
    assert seen == [1, 2, 0] and [v for k, v in written] == [1, 2, 0]


def test_a_page_with_no_switch_ignores_it(fw):
    x, written, _ = page(fw, 2)
    fw.fn('crossfade__toggle', C.c_int, C.c_void_p, C.c_void_p)(None, None)
    assert not written


@pytest.mark.parametrize('ctx, stored, sign, want', ((1, {'WHEELSENSITIVITY': '50'}, 1, 60), (1, {'WHEELSENSITIVITY': '50'}, -1, 40),
                                                     (2, {'RESUMEMIN': '30'}, 1, 35), (2, {'RESUMEMIN': '20'}, -1, 0)))
def test_plus_and_minus_step_by_the_page_s_tick(fw, ctx, stored, sign, want):
    x, _, _ = page(fw, ctx, stored)
    fw.fn('crossfade__step', C.c_int, C.c_void_p, C.c_void_p)(sign, None)
    assert (fw.fn('wheel_pct', C.c_int)() if ctx == 1 else x.rs_min) == want


def keyed(fw, ctx, stored):
    x, written, _ = page(fw, ctx, stored)
    x.page = 0x7000
    x.slider = x.sw = x.label = 0x7100
    return x, written, fw.fn('xfade_key', C.c_int, C.c_void_p, C.c_uint)


def test_the_wheel_steps_the_value_and_other_keys_are_left(fw):
    x, _, key = keyed(fw, 2, {'RESUMEMIN': '30'})
    assert key(0x7000, NEXT) == 1 and x.rs_min == 35
    assert key(0x7000, PREV) == 1 and x.rs_min == 30
    assert key(0x7000, PLAY) == 0
    assert key(0x7001, NEXT) == 0 and x.rs_min == 30


def test_centre_on_a_page_with_no_switch_goes_to_stock(fw):
    _, _, key = keyed(fw, 2, {})
    assert key(0x7000, CENTER) == 0


def int_files(fw, fd=4, fail_open=False, fail_write=False, fail_map=False, fail_rename=False):
    flags = {}
    cells = {}
    fw.mock('fw_open', lambda *_: -1 if fail_open else fd)
    fw.mock('fw_write', lambda fd, buf, n, *_: -1 if fail_write else n)  # the int's zero, written whole

    def mapped(*_):
        cells[len(cells)] = C.c_int(0x7f)
        return -1 if fail_map else C.addressof(cells[len(cells) - 1])
    fw.mock('fw_mmap', mapped)
    fw.mock('fw_rename', lambda *_: -1 if fail_rename else 0)
    fw.mock('fw_munmap', lambda *_: flags.__setitem__('unmapped', 1))
    fw.mock('fw_close', lambda *_: flags.__setitem__('closed', 1))
    return flags, cells


def shared(fw):
    return fw.fn('crossfade__shared_int', C.c_void_p, C.c_char_p, C.c_char_p)(b'/tmp/a.tmp', b'/tmp/a')


def test_a_shared_int_is_made_whole_then_renamed(fw):
    flags, cells = int_files(fw)
    assert shared(fw) == C.addressof(cells[0]) and flags == {'closed': 1}


@pytest.mark.parametrize('how', ('fail_open', 'fail_write', 'fail_map', 'fail_rename'))
def test_a_shared_int_that_can_t_be_made_is_tried_again_later(fw, how):
    flags, _ = int_files(fw, **{how: True})
    assert shared(fw) is None
    assert flags.get('closed') == (None if how == 'fail_open' else 1)
    assert flags.get('unmapped') == (1 if how == 'fail_rename' else None)


def test_mono_off_with_no_file_makes_none(fw):
    config(fw)
    fw.fn('mono_sync', None)()
    assert not fw.calls('fw_open')


def test_mono_on_writes_the_flag_the_player_maps(fw):
    config(fw, {'MONO': 1})
    fw.var('g_equalizer_flag', C.c_ubyte).value = 1
    _, cells = int_files(fw)
    fw.fn('mono_sync', None)()
    assert cells[0].value == 1
    x = xf(fw)
    x.mono = 0
    fw.fn('mono_sync', None)()
    assert cells[0].value == 0 and len(cells) == 1  # one file, kept


def test_swap_has_its_own_flag(fw):
    config(fw, {'SWAPLR': 1})
    fw.var('g_equalizer_flag', C.c_ubyte).value = 1
    _, cells = int_files(fw)
    fw.fn('mono_sync', None)()
    assert cells[0].value == 1 and len(cells) == 1


def test_a_mono_click_saves_and_tells_the_player(fw):
    written = config(fw)
    fw.var('g_equalizer_flag', C.c_ubyte).value = 1
    _, cells = int_files(fw)
    label, button = C.create_string_buffer(0x60), C.create_string_buffer(0x60)
    struct.pack_into('<Q', label, 0x48, C.addressof(button))
    fw.mock('widget_lookup', lambda *_: 0x7300)  # its switch, made
    fw.fn('mono_row', None, C.c_void_p)(label)
    written.clear()
    click = fw.fn('mono_click', C.c_int, C.c_void_p, C.c_void_p)
    click(None, None)
    assert written == [('MONO', 1)] and cells[0].value == 1
    click(None, None)
    assert written == [('MONO', 1), ('MONO', 0)] and cells[0].value == 0


# The EQ page: its files and rules

BANDS = 30


class Band(C.Structure):
    _fields_ = [('enabled', C.c_int), ('type', C.c_int), ('frequency', C.c_double), ('gain', C.c_double), ('q', C.c_double)]


class Preset(C.Structure):
    _fields_ = [('count', C.c_int), ('bypass', C.c_int), ('preamp', C.c_double), ('bands', Band * BANDS),
                ('balance', C.c_double), ('automatic', C.c_int), ('spare', C.c_int)]


class EqState(C.Structure):
    _fields_ = [('mode', C.c_int), ('hold', C.c_int), ('peq', Preset), ('eq3', Preset)]


class Ui(C.Structure):
    _fields_ = [*((k, C.c_void_p) for k in 'page view edit graph bar'.split()), ('timer', C.c_uint), ('keyboard_timer', C.c_uint),
                *((k, C.c_int) for k in 'screen band step count previous rendered dirty pick adjust tries capacity file_page tab tabs from_simple'.split()),
                ('kept', (C.c_int * 2) * 2), ('typed', C.c_double), ('draft', Preset), ('candidate', Preset), ('named', Preset), ('names', C.c_void_p),
                ('destination', C.c_char * 600), ('status', C.c_char * 160), ('name', C.c_char * 256), ('applied', C.c_char * 256),
                ('curve', (C.c_float * 343) * 2)]


def ui(fw):
    return fw.var('peq_ui__ui', Ui)


def peak_preset(gain=6.0, frequency=1000.0):
    p = Preset(1, 0, 0.0)
    p.bands[0] = Band(1, 0, frequency, gain, 1.0)
    return p


@pytest.mark.parametrize('automatic, want', ((0, 1), (1, 1), (2, 0)))
def test_on_auto_unless_the_preamp_was_picked(fw, automatic, want):
    ui(fw).draft.automatic = automatic
    assert fw.fn('peq_ui__on_auto', C.c_int)() == want


def test_a_draft_on_auto_saves_its_cut_for_every_rate(fw):
    u = ui(fw)
    u.draft = peak_preset(6)
    fw.fn('peq_ui__follow_auto', None)()
    assert u.draft.automatic == 1 and u.draft.preamp == pytest.approx(-6, abs=1e-6)


def test_a_picked_preamp_is_left(fw):
    u = ui(fw)
    u.draft = peak_preset(6)
    u.draft.automatic, u.draft.preamp = 2, -2.5
    fw.fn('peq_ui__follow_auto', None)()
    assert u.draft.automatic == 2 and u.draft.preamp == -2.5


def test_auto_is_kept_until_a_band_changes(fw):
    u = ui(fw)
    headroom = fw.fn('peq_ui__headroom', C.c_double, C.c_void_p)
    u.draft = peak_preset(6)
    assert headroom(r(u.draft)) == pytest.approx(-6, abs=1e-6)
    u.draft.preamp, u.draft.balance, u.draft.bypass, u.draft.automatic = -3, 2, 1, 2  # none of these move it
    assert headroom(r(u.draft)) == pytest.approx(-6, abs=1e-6)
    u.draft.bands[0].gain = 3
    assert headroom(r(u.draft)) == pytest.approx(-3, abs=1e-6)


def test_auto_is_the_loudest_cut_of_every_standard_rate(fw):
    ui(fw)
    headroom = fw.fn('peq_ui__headroom', C.c_double, C.c_void_p)
    auto = fw.fn('peq_auto', C.c_double, C.c_void_p, C.c_int)
    p = peak_preset(12, 15000)  # skipped below 30 kHz, and its response differs by rate above
    rates = (8000, 11025, 12000, 16000, 22050, 24000, 32000, 44100, 48000, 64000, 88200, 96000, 176400, 192000, 352800, 384000)
    cuts = [auto(r(p), rate) for rate in rates]
    assert min(cuts) < cuts[rates.index(48000)] and headroom(r(p)) == min(cuts)


def test_3eq_s_headroom_covers_every_standard_rate(fw):
    ui(fw)
    headroom = fw.fn('peq_ui__headroom', C.c_double, C.c_void_p)
    p = peak_preset(6)
    assert headroom(r(p)) == pytest.approx(-6, abs=1e-6)
    p = peak_preset(-6)
    assert headroom(r(p)) == 0


def test_3eq_s_headroom_of_a_bad_preset_is_its_preamp(fw):
    ui(fw)
    p = peak_preset(6)
    p.count, p.preamp = 99, -4
    assert fw.fn('peq_ui__headroom', C.c_double, C.c_void_p)(r(p)) == -4


def test_the_graph_s_axes(fw):
    y, x = fw.fn('peq_ui__graph_y', C.c_float, C.c_double), fw.fn('peq_ui__graph_x', C.c_float, C.c_double)
    assert y(0) == 42  # the plot's middle: rows 6 to 78
    assert y(15) == 6 and y(-15) == 78
    assert y(99) == 6 and y(-99) == 78
    assert x(20) == 16 and x(20000) == pytest.approx(16 + 342)
    assert x(math.sqrt(20 * 20000)) == pytest.approx(16 + 171)


def test_the_curve_is_the_draft_s_response(fw):
    u = ui(fw)
    u.draft = peak_preset(6)
    fw.fn('peq_ui__curve', None)()
    left = list(u.curve[0])
    assert min(left) == pytest.approx(42 - 6 * 72 / 30, abs=.02)  # 6 dB up at 1 kHz
    assert left[0] == pytest.approx(42, abs=.05) and left == list(u.curve[1])


def test_a_bad_draft_draws_flat(fw):
    u = ui(fw)
    u.draft = peak_preset(6)
    u.draft.count = 99
    fw.fn('peq_ui__curve', None)()
    assert all(v == 42 for v in u.curve[0])


def files(fw, names, types=None):
    class Dirent(C.Structure):
        _fields_ = [('ino', C.c_uint), ('off', C.c_uint), ('reclen', C.c_ushort), ('d_type', C.c_ubyte), ('d_name', C.c_char * 256)]
    entries = [Dirent(0, 0, 0, types.get(n, 8) if types else 8, n) for n in names]
    it = iter(entries)
    fw.mock('fw_opendir', lambda *_: 0x3000)
    fw.mock('fw_readdir', lambda *_: next((C.addressof(e) for e in it), 0))
    return fw.fn('peq_ui__list_files', None, C.c_char_p, C.c_char_p)


def listed(fw):
    u = ui(fw)
    return [C.string_at(u.names + 256 * k) for k in range(u.count)]


def test_list_files_by_extension_sorted(fw):
    files(fw, [b'b.peq', b'a.peq', b'x.txt', b'.peq', b'c.PEQ', b'peq'])(b'/d', b'.peq')
    assert listed(fw) == [b'a.peq', b'b.peq', b'c.PEQ']  # the extension in any case, as FAT keeps it
    assert ui(fw).capacity == 128


def test_list_files_only_files(fw):
    files(fw, [b'a.peq', b'b.peq', b'c.peq'], {b'b.peq': 4, b'c.peq': 0})(b'/d', b'.peq')
    assert listed(fw) == [b'a.peq', b'c.peq']


def test_list_files_past_a_page_grows_and_keeps_what_it_has(fw):
    names = [b'%04d.peq' % k for k in range(300)]
    files(fw, names[::-1])(b'/d', b'.peq')
    assert listed(fw) == names and ui(fw).capacity == 512


def test_list_files_a_name_of_255_is_taken_and_a_longer_can_t_be(fw):
    fine = b'x' * 251 + b'.peq'
    assert len(fine) == 255
    files(fw, [fine])(b'/d', b'.peq')
    assert listed(fw) == [fine]


def test_list_files_says_why_it_is_empty(fw):
    files(fw, [])(b'/d', b'.peq')
    assert ui(fw).status == b'No presets found'
    fw.mock('fw_opendir', lambda *_: 0)
    fw.fn('peq_ui__list_files', None, C.c_char_p, C.c_char_p)(b'/d', b'.peq')
    assert ui(fw).status == b'Folder missing or unavailable' and ui(fw).count == 0


FOLDER = '/mnt/mmc/EQ'  # the presets, as text (peq_export)


def apo(fw, p):
    out = C.create_string_buffer(8192)
    n = fw.fn('peq_export', C.c_uint, C.c_void_p, C.c_void_p, C.c_uint)(r(p), out, 8192)
    assert n
    return out.raw[:n]


def typed(fw, text):
    """The rename edit holding text: its UTF-8 copy and the wide text rename_preset checks for any."""
    wide = (C.c_uint * (len(text) + 1))(*text)
    fw.keep.append(wide)
    fw.mock('widget_get_text', lambda *_: C.addressof(wide))
    fw.mock('widget_get_text_utf8', lambda w, out, n, *_: (C.memmove(out, text + b'\0', min(len(text) + 1, n)), 0)[1])


def renamer(fw, text, old='Rock', others=()):
    u = ui(fw)
    u.edit, u.screen = 0x7000, NAMING
    u.destination = f'{FOLDER}/{old}.txt'.encode()
    u.name, u.applied = old.encode(), b'Other'
    card = Card(fw, {f'{FOLDER}/{old}.txt': b'p', **{f'{FOLDER}/{n}.txt': b'q' for n in others}})
    typed(fw, text)
    fw.fn('peq_ui__rename_preset', None, C.c_char_p)(None)  # None: the edit's text
    return u, card


def test_a_rename_moves_the_file_and_the_names_that_follow_it(fw):
    u, card = renamer(fw, b'Jazz')
    assert f'{FOLDER}/Jazz.txt' in card.files and f'{FOLDER}/Rock.txt' not in card.files
    assert u.name == b'Jazz' and u.applied == b'Other' and u.status == b'Renamed to Jazz' and u.screen == 2


def test_a_rename_trims_what_fat_drops(fw):
    _, card = renamer(fw, b'  Jazz. . ')
    assert f'{FOLDER}/Jazz.txt' in card.files


def test_an_empty_edit_goes_back_to_the_list(fw):
    u, card = renamer(fw, b'')  # Return's cancel leaves it so
    assert u.screen == RENAMES and u.status == b'' and list(card.files) == [f'{FOLDER}/Rock.txt']


@pytest.mark.parametrize('text', (b'   ', b'...', b'.hidden', b'a/b', b'a\\b', b'a:b', b'a*b', b'a?b', b'a"b', b'a<b', b'a>b', b'a|b'))
def test_a_name_fat_can_t_hold_keeps_the_preset(fw, text):
    u, card = renamer(fw, text)
    assert u.status == b'Name not allowed; preset kept' and list(card.files) == [f'{FOLDER}/Rock.txt']


def test_a_name_taken_keeps_the_preset(fw):
    u, card = renamer(fw, b'Jazz', others=('Jazz',))
    assert u.status == b'Name taken; preset kept' and card.files[f'{FOLDER}/Rock.txt'] == b'p'


def test_a_case_change_is_a_rename_though_fat_finds_the_name(fw):
    u = ui(fw)
    u.edit, u.destination, u.name = 0x7000, f'{FOLDER}/Rock.txt'.encode(), b'Rock'
    typed(fw, b'ROCK')
    fw.mock('fw_access', lambda *_: 0)  # FAT: ROCK.txt "exists"
    fw.fn('peq_ui__rename_preset', None, C.c_char_p)(None)  # None: the edit's text
    assert u.status == b'Renamed to ROCK' and u.name == b'ROCK'


def test_the_same_name_does_nothing(fw):
    u, card = renamer(fw, b'Rock')
    assert u.status == b'' and card.files == {f'{FOLDER}/Rock.txt': b'p'}


def test_a_failed_rename_says_so(fw):
    u = ui(fw)
    u.edit, u.destination = 0x7000, f'{FOLDER}/Rock.txt'.encode()
    card = Card(fw, {f'{FOLDER}/Rock.txt': b'p'})
    card.bad_rename = True
    typed(fw, b'Jazz')
    fw.fn('peq_ui__rename_preset', None, C.c_char_p)(None)  # None: the edit's text
    assert u.status == b'Rename failed; preset kept'


def test_rename_hostile_sweep(fw):
    rng = random.Random(6)
    for _ in range(500):
        text = bytes(rng.choices(b'ab .\\/:*?"<>| \xc3\xa9', k=rng.randrange(0, 40)))
        u, card = renamer(fw, text)
        names = [n for n in card.files]
        assert len(names) == 1 and not set(names[0][len(FOLDER) + 1:]) & set('/\\:*?"<>|')


def test_load_draft_keeps_the_switch_and_follows_auto(fw):
    u = ui(fw)
    u.draft.bypass, u.screen = 1, 2
    p = peak_preset(6)
    p.automatic = 1
    fw.fn('peq_ui__load_draft', None, C.c_void_p, C.c_char_p, C.c_int)(r(p), b'Rock.txt', 4)
    assert u.draft.bypass == 1 and u.draft.preamp == pytest.approx(-6, abs=1e-6)
    assert u.name == b'Rock' and u.screen == 0 and u.dirty == 1 and b'Apply' in u.status


def test_a_preamp_pick_sticks_to_the_saved_preset(fw):
    u = ui(fw)
    p = peak_preset(6)
    p.automatic = 2
    card = Card(fw, {f'{FOLDER}/Rock.txt': apo(fw, p)})
    u.name, u.draft.automatic, u.draft.preamp = b'Rock', 2, -9
    fw.fn('peq_ui__remember_preamp', None)()
    saved = Preset()
    assert fw.fn('peq_import_file', C.c_int, C.c_char_p, C.c_void_p, C.c_void_p)(f'{FOLDER}/Rock.txt'.encode(), r(saved), None)
    assert saved.preamp == -9 and saved.automatic == 2 and saved.bands[0].gain == 6


def test_a_preamp_pick_leaves_a_file_with_lines_left_out_as_it_is(fw):
    u = ui(fw)
    text = b'Device: all\nPreamp: -3 dB\nFilter: ON PK Fc 1000 Hz Gain 6 dB Q 1\n'
    card = Card(fw, {f'{FOLDER}/Rock.txt': text})
    u.name, u.draft.automatic, u.draft.preamp = b'Rock', 1, -6
    fw.fn('peq_ui__remember_preamp', None)()
    assert card.files == {f'{FOLDER}/Rock.txt': text}


def test_a_preamp_pick_of_a_stock_preset_writes_nothing(fw):
    u = ui(fw)
    card = Card(fw)
    u.name = b'Jazz'
    fw.fn('peq_ui__remember_preamp', None)()
    assert not card.files


ACTIVE = '/mnt/data/peq-active'


def eq_state(fw, mode=2, hold=0):
    s = EqState(mode, hold)
    fw.fn('peq_default', None, C.c_void_p)(r(s.peq))
    fw.fn('eq3_bands', None, C.c_void_p, C.c_void_p)(r(s.eq3), (C.c_double * 3)(1, 2, 3))
    return s


def saved_state(fw, mode=2, hold=0):
    return b'Q2PEQ03\0' + bytes(eq_state(fw, mode, hold))


def test_boot_clears_the_comparison_a_restart_ended(fw):
    card = Card(fw, {ACTIVE: saved_state(fw, 0, 1)})
    fw.fn('peq_boot', None)()
    assert EqState.from_buffer_copy(card.files[ACTIVE], 8).hold == 0
    assert not fw.calls('stock_eq_trampoline')  # nothing to reach yet


def test_boot_tells_the_filter_if_it_is_in(fw):
    Card(fw, {ACTIVE: saved_state(fw, 0, 1)})
    fw.var('peq_chained', C.c_int).value = 1
    fw.fn('peq_boot', None)()
    assert fw.calls('stock_eq_trampoline')


def test_boot_with_no_hold_writes_nothing(fw):
    s = eq_state(fw, 2, 0)
    s.peq.automatic = 1  # Auto, its cut (0 dB, flat) already right; never picked (0) is saved as Auto
    data = b'Q2PEQ03\0' + bytes(s)
    card = Card(fw, {ACTIVE: data})
    fw.fn('peq_boot', None)()
    assert card.files[ACTIVE] == data and not fw.calls('fw_fwrite')


def test_peq_stock_eq_chains_the_filter_then_shows_the_real_state(fw):
    Card(fw, {ACTIVE: saved_state(fw, 0)})
    flag = fw.var('g_equalizer_flag', C.c_ubyte)
    seen = []
    fw.mock('stock_eq_trampoline', lambda mode, *_: (seen.append((mode, flag.value)), 5)[1])
    assert fw.fn('peq_stock_eq', C.c_int, C.c_int)(0) == 5
    assert seen == [(1, 1)]  # always instantiated, whatever the mode
    assert flag.value == 0 and fw.var('peq_chained', C.c_int).value == 1


def test_peq_stock_eq_with_an_eq_on_lights_the_icon(fw):
    Card(fw, {ACTIVE: saved_state(fw, 1)})
    fw.fn('peq_stock_eq', C.c_int, C.c_int)(0)
    assert fw.var('g_equalizer_flag', C.c_ubyte).value == 1


def test_attach_turns_a_saved_eq_off_for_the_stock_flag_s_sake(fw):
    card = Card(fw, {ACTIVE: saved_state(fw, 2, 1)})
    fw.fn('peq_attach', None)()
    s = EqState.from_buffer_copy(card.files[ACTIVE], 8)
    assert (s.mode, s.hold) == (0, 0) and fw.calls('stock_eq_trampoline')


def test_attach_with_the_eq_off_saves_nothing(fw):
    data = saved_state(fw, 0)
    card = Card(fw, {ACTIVE: data})
    fw.fn('peq_attach', None)()
    assert card.files[ACTIVE] == data and fw.calls('stock_eq_trampoline')


def test_commit_writes_the_stock_switch_only_when_on_or_off_changes(fw):
    written = config(fw)
    Card(fw, {ACTIVE: saved_state(fw, 0)})
    commit = fw.fn('peq_ui__eq_commit', C.c_int, C.c_void_p)
    flag = fw.var('g_equalizer_flag', C.c_ubyte)
    flag.value = 0
    s = eq_state(fw, 2)
    assert commit(r(s)) == 1 and written == [('EQFLAG', 1)]
    flag.value = 1
    assert commit(r(s)) == 1 and written == [('EQFLAG', 1)]


def test_commit_of_an_unsaveable_state_changes_nothing(fw):
    written = config(fw)
    Card(fw)
    s = eq_state(fw, 9)
    assert fw.fn('peq_ui__eq_commit', C.c_int, C.c_void_p)(r(s)) == 0
    assert not written and not fw.calls('stock_eq_trampoline')


def test_apply_makes_the_draft_play_under_its_name(fw):
    config(fw)
    card = Card(fw, {ACTIVE: saved_state(fw, 1)})
    u = ui(fw)
    u.draft = peak_preset(6)
    u.name, u.dirty = b'Rock', 1
    assert fw.fn('peq_ui__apply', C.c_int, C.c_int)(0) == 1
    s = EqState.from_buffer_copy(card.files[ACTIVE], 8)
    assert s.mode == 2 and s.peq.bands[0].gain == 6 and s.eq3.bands[2].gain == 3  # 3EQ kept for later
    assert u.dirty == 0 and u.applied == b'Rock' and bytes(u.named) == bytes(u.draft)


def test_apply_bypassed_keeps_the_mode(fw):
    config(fw)
    card = Card(fw, {ACTIVE: saved_state(fw, 1)})
    u = ui(fw)
    u.draft = peak_preset(6)
    u.draft.bypass = 1
    fw.fn('peq_ui__apply', C.c_int, C.c_int)(0)
    s = EqState.from_buffer_copy(card.files[ACTIVE], 8)
    assert s.mode == 1 and s.peq.bypass == 1
    fw.fn('peq_ui__apply', C.c_int, C.c_int)(1)  # on: a preset picked from Simple
    s = EqState.from_buffer_copy(card.files[ACTIVE], 8)
    assert s.mode == 2 and not s.peq.bypass and u.draft.bypass == 0


def test_a_preamp_pick_sticks_to_a_long_named_preset(fw):
    u = ui(fw)
    name = 'Beyerdynamic DT 1990 Pro (250 ohm) (Balanced pads) ParametricEQ v2'
    assert len(name) > 63
    p = peak_preset(6)
    p.automatic = 2
    path = f'{FOLDER}/{name}.txt'
    card = Card(fw, {path: apo(fw, p)})
    fw.fn('peq_ui__load_draft', None, C.c_void_p, C.c_char_p, C.c_int)(r(p), (name + '.txt').encode(), len(name))
    u.draft.automatic, u.draft.preamp = 2, -9
    fw.fn('peq_ui__remember_preamp', None)()
    assert b'\nPreamp: -9 dB\n' in card.files[path]


HOME, BAND, PRESETS, SAVES, CONFIRM, DELETES, PICK, ADJUST, STOCKS, RENAMES, NAMING, TYPES = range(12)
ACT_BACK, ACT_APPLY, ACT_RESET, ACT_BYPASS, ACT_PER_OUTPUT, ACT_MENU, ACT_SAVE, ACT_ENABLE, ACT_TYPE, ACT_RAISE, ACT_LOWER, \
    ACT_TYPED, ACT_CHANNEL, ACT_STEP, ACT_YES, ACT_CANCEL, ACT_DELETE, ACT_BALANCE, ACT_PREAMP, ACT_GAIN, ACT_FREQUENCY, ACT_QUALITY, \
    ACT_KEYBOARD, ACT_STOCK, ACT_FILE_PREV, ACT_FILE_NEXT, ACT_RENAME, ACT_NAMED = range(1000, 1028)


def toasts(fw):
    """What action's toasts say, in order: its status, over the page (toast: stock's msginfo_dialog, the text 8 bytes
    into its info)."""
    said: list[bytes] = []
    fw.mock('navigator_to_with_context', lambda page, info, *_:
            said.append(C.string_at(info + 8)) if C.string_at(page) == b'dialog/msginfo_dialog' else None)
    return said


def act(fw, *ids):
    action = fw.fn('peq_ui__action', C.c_int, C.c_void_p, C.c_void_p)
    for id_ in ids:
        action(id_, None)


def band_page(fw, automatic=1):
    u = ui(fw)
    u.draft = peak_preset(6)
    u.draft.automatic, u.screen, u.band = automatic, BAND, 0
    return u, u.draft.bands[0]


def test_a_band_s_type_is_picked_and_its_channel_cycles(fw):
    u, b = band_page(fw)
    seen = []
    for i in (7, 6, 1, 0):  # all pass, notch, low shelf, peaking
        act(fw, ACT_TYPE)
        assert u.screen == TYPES
        act(fw, i)
        seen.append(b.type)
        assert u.screen == BAND
    assert seen == [7, 6, 1, 0]
    act(fw, ACT_TYPE, ACT_BACK)
    assert u.screen == BAND and b.type == 0
    seen = []
    for _ in range(3):
        act(fw, ACT_CHANNEL)
        seen.append(b.enabled)
    assert seen == [2, 3, 1]  # both, left, right


def test_a_band_that_is_off_has_no_channel(fw):
    u, b = band_page(fw)
    b.enabled = 0
    act(fw, ACT_CHANNEL)
    assert b.enabled == 0
    act(fw, ACT_ENABLE)
    assert b.enabled == 1


def test_raise_and_lower_step_a_frequency_within_its_range(fw):
    u, b = band_page(fw)
    u.adjust, b.frequency = ACT_FREQUENCY, 1000
    act(fw, ACT_RAISE)
    assert b.frequency == 1001
    act(fw, ACT_STEP, ACT_STEP)  # 1, 10, then 100
    act(fw, ACT_LOWER)
    assert b.frequency == 901
    b.frequency = 25
    act(fw, ACT_LOWER)
    assert b.frequency == 20
    b.frequency = 19950
    act(fw, ACT_RAISE)
    assert b.frequency == 20000
    act(fw, ACT_STEP, ACT_STEP, ACT_STEP)  # wraps to 1 by way of 1000
    assert u.step == 1


def test_q_steps_a_twentieth_within_its_range(fw):
    u, b = band_page(fw)
    u.adjust, b.q = ACT_QUALITY, 1
    act(fw, ACT_RAISE)
    assert b.q == pytest.approx(1.05)
    b.q = .12
    act(fw, ACT_LOWER)
    assert b.q == .1
    b.q = 9.99
    act(fw, ACT_RAISE)
    assert b.q == 10
    b.type, b.q = 6, 29.99  # a notch's goes to 30
    act(fw, ACT_RAISE, ACT_RAISE)
    assert b.q == 30
    act(fw, ACT_TYPE, 0)  # and comes down to a peak's 10
    assert (b.type, b.q) == (0, 10)


@pytest.mark.parametrize('typed, want', ((500, 500), (5, 20), (99999, 20000)))
def test_a_typed_frequency_is_clamped(fw, typed, want):
    u, b = band_page(fw)
    u.adjust, u.typed = ACT_FREQUENCY, typed
    act(fw, ACT_TYPED)
    assert b.frequency == want


def test_a_band_edit_keeps_the_preamp_on_auto_only_if_it_was(fw):
    u, b = band_page(fw, automatic=1)
    u.adjust = ACT_FREQUENCY
    act(fw, ACT_RAISE)
    assert u.draft.preamp == pytest.approx(-6, abs=1e-6) and u.dirty == 1
    u, b = band_page(fw, automatic=2)
    u.draft.preamp = -1
    act(fw, ACT_RAISE)
    assert u.draft.preamp == -1


@pytest.mark.parametrize('pick, row, field, want', ((ACT_BALANCE, 0, 'balance', -12), (ACT_BALANCE, 24, 'balance', 0), (ACT_BALANCE, 48, 'balance', 12),
                                                    (ACT_PREAMP, 1, 'preamp', 12), (ACT_PREAMP, 73, 'preamp', -24)))
def test_a_picker_s_rows_are_half_decibels(fw, pick, row, field, want):
    u = ui(fw)
    u.draft = peak_preset(6)
    u.screen, u.pick = PICK, pick
    act(fw, row)
    assert getattr(u.draft, field) == want and u.screen == HOME and u.dirty == 1


def test_the_gain_picker_reaches_24_either_way_and_turns_the_band_on(fw):
    for row, want in ((0, 24), (48, 0), (96, -24)):
        u, b = band_page(fw)
        b.enabled = 0
        u.screen, u.pick = PICK, ACT_GAIN
        act(fw, row)
        assert b.gain == want and b.enabled == 1 and u.screen == BAND


def test_a_row_past_a_picker_s_last_does_nothing(fw):
    u = ui(fw)
    u.screen, u.pick, u.draft.preamp = PICK, ACT_PREAMP, -1
    act(fw, 74)
    assert u.draft.preamp == -1 and u.screen == PICK


def test_preamp_s_first_row_is_auto_and_any_other_a_pick(fw):
    u = ui(fw)
    u.draft = peak_preset(6)
    u.screen, u.pick = PICK, ACT_PREAMP
    act(fw, 3)
    assert u.draft.automatic == 2 and u.draft.preamp == 11
    u.screen, u.pick = PICK, ACT_PREAMP
    act(fw, 0)
    assert u.draft.automatic == 1 and u.draft.preamp == pytest.approx(-6, abs=1e-6)


def test_back_steps_up_one_screen(fw):
    u = ui(fw)
    for screen, pick, want in ((BAND, 0, HOME), (PRESETS, 0, HOME), (SAVES, 0, PRESETS), (STOCKS, 0, PRESETS), (ADJUST, 0, BAND),
                               (PICK, ACT_GAIN, BAND), (PICK, ACT_PREAMP, HOME), (PICK, ACT_BALANCE, HOME)):
        u.screen, u.pick = screen, pick
        act(fw, ACT_BACK)
        assert u.screen == want, screen
    assert not fw.calls('navigator_back')
    u.screen = HOME
    act(fw, ACT_BACK)
    assert fw.calls('navigator_back')


def test_reset_is_the_default_draft_keeping_the_switch(fw):
    u = ui(fw)
    u.draft = peak_preset(6)
    u.draft.bypass, u.name = 0, b'Rock'
    act(fw, ACT_RESET)
    assert u.draft.count == 10 and u.draft.bands[0].gain == 0 and u.draft.bypass == 0
    assert u.name == b'' and u.dirty == 1 and u.draft.preamp == 0 and u.draft.automatic == 1


def test_a_stock_preset_loads_into_the_editor_and_plays_nothing_yet(fw):
    card = Card(fw)
    u = ui(fw)
    u.screen = STOCKS
    act(fw, 1)  # Rock
    gains = [u.draft.bands[k].gain for k in range(10)]
    assert gains == [-2, 0, 2, 4, -2, -2, 0, 0, 4, 4]
    assert [u.draft.bands[k].enabled for k in range(10)] == [1, 0, 1, 1, 1, 1, 0, 0, 1, 1]
    assert u.name == b'Rock' and u.screen == HOME and u.draft.preamp < 0 and not card.files


def test_a_row_past_the_stock_presets_does_nothing(fw):
    u = ui(fw)
    u.screen = STOCKS
    act(fw, 8)
    assert u.screen == STOCKS


def test_saving_takes_a_slot_and_asks_before_replacing_it(fw):
    card = Card(fw)
    said = toasts(fw)
    u = ui(fw)
    u.draft = peak_preset(6)
    u.screen = SAVES
    act(fw, 0)
    path = f'{FOLDER}/Manual 01.txt'
    assert card.files[path].startswith(b'# Equalizer APO preset') and len(said) == 1 and b'saved' in said[0]
    first = card.files[path]
    u.draft.bands[0].gain = 3
    act(fw, 0)
    assert u.screen == CONFIRM and u.previous == SAVES and card.files[path] == first
    act(fw, ACT_CANCEL)
    assert u.screen == SAVES and card.files[path] == first
    act(fw, 0, ACT_YES)
    assert u.screen == SAVES and card.files[path] != first


def test_a_preset_on_the_card_loads_or_says_which_line_failed(fw):
    files(fw, [b'good.txt', b'bad.txt'])(b'/mnt/mmc/EQ', b'.txt')
    card = Card(fw, {'/mnt/mmc/EQ/good.txt': b'Preamp: -3 dB\n', '/mnt/mmc/EQ/bad.txt': b'\nnonsense\n'})
    said = toasts(fw)
    u = ui(fw)
    u.screen = PRESETS
    act(fw, 0)  # sorted: bad.txt
    assert said == [b'Line 1: no Equalizer APO lines'] and u.screen == PRESETS
    act(fw, 1)
    assert u.screen == HOME and u.name == b'good' and u.draft.preamp == -3 and len(card.files) == 2  # nothing written


def test_deleting_asks_first_and_leaves_what_plays(fw):
    files(fw, [b'a.txt'])(b'/d', b'.txt')
    card = Card(fw, {f'{FOLDER}/a.txt': b'p', ACTIVE: b'x'})
    said = toasts(fw)
    u = ui(fw)
    u.screen = DELETES
    act(fw, 0)
    assert u.screen == CONFIRM and f'{FOLDER}/a.txt' in card.files
    act(fw, ACT_YES)
    assert f'{FOLDER}/a.txt' not in card.files and card.files[ACTIVE] == b'x' and said == [b'Deleted a.txt; active EQ unchanged']


def test_a_saved_preset_it_can_t_read_changes_nothing(fw):
    files(fw, [b'a.txt'])(b'/d', b'.txt')
    Card(fw, {f'{FOLDER}/a.txt': b'junk'})
    said = toasts(fw)
    u = ui(fw)
    u.draft = peak_preset(6)
    u.screen = PRESETS
    act(fw, 0)
    assert u.draft.bands[0].gain == 6 and said == [b'Line 1: no Equalizer APO lines']


def test_a_file_row_counts_through_its_page(fw):
    names = [b'%03d.txt' % k for k in range(200)]
    files(fw, names)(b'/d', b'.txt')
    Card(fw, {f'{FOLDER}/130.txt': b'junk'})
    u = ui(fw)
    u.screen, u.file_page = RENAMES, 1
    act(fw, 2)  # 128 a page
    assert u.screen == NAMING and u.destination == f'{FOLDER}/130.txt'.encode()
    assert u.file_page == 0  # a new screen starts on its first page


def test_the_switch_turns_peq_on_over_3eq_and_off(fw):
    config(fw)
    card = Card(fw, {ACTIVE: saved_state(fw, 1)})
    u = ui(fw)
    u.draft.bypass = 1
    act(fw, ACT_BYPASS)
    assert EqState.from_buffer_copy(card.files[ACTIVE], 8).mode == 2 and u.draft.bypass == 0
    act(fw, ACT_BYPASS)
    assert EqState.from_buffer_copy(card.files[ACTIVE], 8).mode == 0 and u.draft.bypass == 1


def test_a_failed_switch_says_so_and_stays(fw):
    config(fw)
    card = Card(fw, {ACTIVE: saved_state(fw, 0)})
    said = toasts(fw)
    u = ui(fw)
    u.draft.bypass = 1
    fw.mock('fw_fopen', lambda path, mode, *_: card.fopen(path, mode) if C.string_at(mode)[:1] == b'r' else 0)
    act(fw, ACT_BYPASS)
    assert u.draft.bypass == 1 and said == [b'Switch failed; EQ unchanged']


def test_a_display_only_row_does_nothing(fw):
    u = ui(fw)
    u.screen, u.status = BAND, b'kept'
    act(fw, -1)
    assert u.screen == BAND and u.status == b'kept'


class E3(C.Structure):
    _fields_ = [('view', C.c_void_p), ('save', C.c_uint), ('press', C.c_uint), ('press_at', C.c_uint),
                *((k, C.c_int) for k in 'focus adjusting dragging dirty on h'.split()), ('gain', C.c_double * 3), ('status', C.c_char_p), ('caption', C.c_int)]


E3_TABS, E3_SWITCH, E3_PRESETS, E3_RESET, E3_LOW, E3_MID, E3_HIGH = range(7)
# OLED's Simple tab (a set_switch theme's, peq.h): the switch, Presets and Reset as rows under the tabs, then the
# sliders' travel, +6 dB at its top to -6 at its foot
EQ = {k: int(v) for k, v in re.findall(r'^#define (EQ_TABS_H|EQ_ROW|EQ_SLIDERS_TOP|EQ_SLIDERS_BOTTOM) (\d+)\b',
                                        (ROOT/'patch/peq.h').read_text(), re.M)}
ROWS_Y, ROW, TOP, FOOT = EQ['EQ_TABS_H'], EQ['EQ_ROW'], EQ['EQ_SLIDERS_TOP'], EQ['EQ_SLIDERS_BOTTOM']
ZERO = (TOP + FOOT) // 2
assert ROWS_Y + 3 * ROW < TOP - 4  # a finger just above the travel is past the rows


def simple(fw, state=None):
    u = ui(fw)
    config(fw)
    card = Card(fw, {ACTIVE: state or saved_state(fw, 1)})
    e = fw.var('peq_ui__e3', E3)
    e.h, e.view, u.page, u.draft.bypass = 290, 0x7000, 0x7100, 1
    fw.mock('timer_add', lambda *_: 5)
    return u, e, card


def saved(card):
    return EqState.from_buffer_copy(card.files[ACTIVE], 8)


def test_a_gain_stays_in_6_db_and_arms_one_save(fw):
    _, e, _ = simple(fw)
    set_gain = fw.fn('peq_ui__e3_set', None, C.c_int, C.c_double)
    set_gain(0, 9)
    assert e.gain[0] == 6 and e.dirty == 1 and e.save == 5
    set_gain(1, -9)
    assert e.gain[1] == -6 and len(fw.calls('timer_add')) == 1


def test_the_same_gain_is_no_edit(fw):
    _, e, _ = simple(fw)
    fw.fn('peq_ui__e3_set', None, C.c_int, C.c_double)(0, 0)
    assert e.dirty == 0 and not fw.calls('timer_add')


@pytest.mark.parametrize('gain, y', ((0, ZERO), (6, TOP), (-6, FOOT), (3, ZERO - (FOOT - TOP) // 4)))
def test_a_slider_s_knob_follows_its_gain(fw, gain, y):
    fw.var('peq_ui__e3', E3).h = 290
    assert fw.fn('peq_ui__e3_y', C.c_int, C.c_double)(gain) == y


def test_a_flush_saves_the_gains_with_their_headroom_and_tells_a_playing_3eq(fw):
    _, e, card = simple(fw, saved_state(fw, 1))
    e.gain[0], e.gain[1], e.gain[2], e.dirty = 6, 0, 0, 1
    assert fw.fn('peq_ui__e3_flush', C.c_int)() == 1
    s = saved(card)
    assert s.eq3.bands[0].gain == 6 and s.eq3.preamp == pytest.approx(-6, abs=.01)
    assert e.dirty == 0 and fw.calls('stock_eq_trampoline')


def test_a_flush_while_another_eq_plays_leaves_the_player_alone(fw):
    _, e, card = simple(fw, saved_state(fw, 2))
    e.gain[0], e.dirty = 3, 1
    fw.fn('peq_ui__e3_flush', C.c_int)()
    assert saved(card).eq3.bands[0].gain == 3 and not fw.calls('stock_eq_trampoline')


def test_a_flush_with_nothing_to_save_writes_nothing(fw):
    _, _, card = simple(fw)
    before = card.files[ACTIVE]
    assert fw.fn('peq_ui__e3_flush', C.c_int)() == 1 and card.files[ACTIVE] == before


def test_a_failed_flush_stays_dirty_and_says_so(fw):
    _, e, card = simple(fw)
    e.gain[0], e.dirty = 3, 1
    fw.mock('fw_fopen', lambda path, mode, *_: card.fopen(path, mode) if C.string_at(mode)[:1] == b'r' else 0)
    assert fw.fn('peq_ui__e3_flush', C.c_int)() == 0
    assert e.dirty == 1 and e.status == b'Save failed; EQ unchanged'


def test_turning_3eq_off_holds_its_preamp_for_the_comparison(fw):
    u, e, card = simple(fw, saved_state(fw, 1))
    e.on = 1
    fw.fn('peq_ui__e3_switch', None, C.c_int)(0)
    s = saved(card)
    assert (s.mode, s.hold, e.on) == (0, 1, 0)


def test_turning_a_playing_peq_off_holds_nothing(fw):
    u, e, card = simple(fw, saved_state(fw, 2))
    u.draft.bypass = 0
    fw.fn('peq_ui__e3_switch', None, C.c_int)(0)
    assert (saved(card).mode, saved(card).hold) == (0, 0) and u.draft.bypass == 1


def test_a_slider_taken_over_a_playing_peq_turns_3eq_on_first(fw):
    u, e, card = simple(fw, saved_state(fw, 2))
    u.draft.bypass = 0
    fw.fn('peq_ui__e3_take', None)()
    assert saved(card).mode == 1 and e.adjusting == 1 and e.on == 1


def test_a_slider_stays_put_over_a_peq_the_switch_could_not_stop(fw):
    u, e, card = simple(fw, saved_state(fw, 2))
    u.draft.bypass = 0
    fw.mock('fw_fopen', lambda path, mode, *_: card.fopen(path, mode) if C.string_at(mode)[:1] == b'r' else 0)
    fw.fn('peq_ui__e3_take', None)()
    assert e.adjusting == 0 and e.status == b'Switch failed; EQ unchanged'


def test_reset_is_flat_saved_at_once(fw):
    _, e, card = simple(fw, saved_state(fw, 1))
    e.gain[0], e.gain[1], e.gain[2], e.focus = 1, 2, 3, E3_RESET
    fw.fn('peq_ui__e3_activate', None)()
    assert list(e.gain) == [0, 0, 0] and [saved(card).eq3.bands[k].gain for k in range(3)] == [0, 0, 0]


def test_the_wheel_walks_the_six_and_stops_at_each_end(fw):
    u, e, _ = simple(fw)
    key = fw.fn('eq_key', C.c_int, C.c_void_p, C.c_uint)
    e.focus = E3_LOW
    seen = []
    for k in (NEXT, NEXT, NEXT, PREV):
        assert key(0x7100, k) == 1
        seen.append(e.focus)
    assert seen == [E3_MID, E3_HIGH, E3_HIGH, E3_MID]
    e.focus = E3_TABS
    key(0x7100, PREV)
    assert e.focus == E3_TABS


def test_the_wheel_turns_a_taken_slider_half_a_db_a_tick(fw):
    _, e, _ = simple(fw)
    key = fw.fn('eq_key', C.c_int, C.c_void_p, C.c_uint)
    e.focus, e.adjusting = E3_MID, 1
    key(0x7100, NEXT), key(0x7100, NEXT), key(0x7100, PREV)
    assert e.gain[1] == .5 and e.focus == E3_MID


def test_other_pages_and_keys_are_not_simple_s(fw):
    key = fw.fn('eq_key', C.c_int, C.c_void_p, C.c_uint)
    assert key(0x7100, NEXT) == 0  # no page
    simple(fw)
    assert key(0x7101, NEXT) == 0 and key(0x7100, PLAY) == 0


def pointer(kind, x, y):
    raw = C.create_string_buffer(0x40)
    struct.pack_into('<i', raw, 0, kind)
    struct.pack_into('<ii', raw, 0x18, x, y)
    return raw


@pytest.mark.parametrize('x, y, band, gain', ((50, ZERO, 0, 0), (200, TOP, 1, 6), (300, FOOT, 2, -6), (50, TOP - 4, 0, 6), (50, 400, 0, -6),
                                              (50, ZERO - 1, 0, .5), (50, ZERO + 1, 0, -.5)))
def test_a_slider_follows_the_finger_by_half_decibels(fw, x, y, band, gain):
    _, e, _ = simple(fw)
    if gain == 0:
        e.gain[band] = 1
    assert fw.fn('peq_ui__e3_touch', C.c_int, C.c_void_p, C.c_void_p)(None, pointer(0x100, x, y)) == 0
    assert e.focus == E3_LOW + band and e.gain[band] == gain and e.dragging == 1


def test_a_lift_ends_the_drag_and_saves(fw):
    _, e, card = simple(fw, saved_state(fw, 1))
    touch = fw.fn('peq_ui__e3_touch', C.c_int, C.c_void_p, C.c_void_p)
    touch(None, pointer(0x100, 50, TOP))
    touch(None, pointer(0x104, 50, TOP))
    assert (e.dragging, e.adjusting, e.dirty) == (0, 0, 0) and saved(card).eq3.bands[0].gain == 6


def test_a_touch_on_the_tabs_is_not_simple_s(fw):
    _, e, _ = simple(fw)
    e.focus = E3_LOW
    fw.fn('peq_ui__e3_touch', C.c_int, C.c_void_p, C.c_void_p)(None, pointer(0x100, 50, ROWS_Y - 1))
    assert not e.dragging and e.focus == E3_LOW


@pytest.mark.parametrize('y, focus', ((ROWS_Y, E3_SWITCH), (ROWS_Y + ROW - 1, E3_SWITCH), (ROWS_Y + ROW, E3_PRESETS),
                                      (ROWS_Y + 2 * ROW - 1, E3_PRESETS), (ROWS_Y + 2 * ROW, E3_RESET), (ROWS_Y + 3 * ROW - 1, E3_RESET)))
def test_a_touch_on_the_rows_takes_its_row(fw, y, focus):
    _, e, _ = simple(fw)
    fw.fn('peq_ui__e3_touch', C.c_int, C.c_void_p, C.c_void_p)(None, pointer(0x100, 200, y))
    assert e.focus == focus


def test_a_folder_s_delete_removes_that_folder_alone_whatever_its_name(fw, tmp_path):
    """ringnav_folder_rm as remove_file calls it, its command run by /bin/sh: $, backticks and ' stay the name's."""
    rm = fw.fn('ringnav_folder_rm', C.c_int, C.c_void_p, C.c_uint, C.c_char_p, C.c_char_p)
    for name in ('$NOT', "Guns N' Roses", 'x$(touch p)`touch q`'):
        (tmp_path/'Music'/name/'CD1').mkdir(parents=True)
        (tmp_path/'Music'/'kept').mkdir(exist_ok=True)
        cmd = block(fw, size=0x61b)
        rm(cmd, 0x61b, b'rm -rf "%s"', str(tmp_path/'Music'/name).encode())
        subprocess.run(['sh', '-c', C.string_at(cmd).decode()], cwd=tmp_path, check=True)
        assert sorted(p.name for p in tmp_path.rglob('*')) == ['Music', 'kept']
    cmd = block(fw, size=25)  # exactly the command and its NUL
    assert rm(cmd, 25, b'', b"/mnt/mmc/a'b") == 24 and C.string_at(cmd) == b"rm -rf '/mnt/mmc/a'\\''b'"
    assert rm(cmd, 24, b'', b"/mnt/mmc/a'b") == 1 and C.string_at(cmd) == b':'  # a byte short: removes nothing


def test_a_video_holds_the_library_paused_where_it_was_and_gives_it_back(fw):
    """A video takes the output as Radio does (now_source.c now_hold): the Library's song playing at 61 s is stopped
    with its place kept, not forgotten by stock's player_stop, and reads paused at 61 s while the video plays; the
    video ended, the song is loaded again paused at 61 s (now_release), as when Radio's page shuts with no station."""
    from test_radio import Library
    lib = Library(fw)
    Card(fw)
    fw.mock('fw_fork', lambda *_: 77)
    fw.mock('fw_socket', lambda *_: 9)
    assert fw.fn('video_click', C.c_int, C.c_char_p)(b'/m/a.mp4') == 1
    assert lib.calls[0] == 'save_stop', lib.calls
    assert lib.now() == (3, 61)
    fw.mock('fw_waitpid', lambda *_: 77)
    fw.fn('video_poll', None)()
    assert video(fw).pid == 0 and lib.calls[-1] == 'save_start 1' and lib.now() == (3, 61)


def test_a_video_over_a_stopped_library_leaves_it_stopped(fw):
    from test_radio import Library
    lib = Library(fw, status=1, sec=-1)
    Card(fw)
    fw.mock('fw_fork', lambda *_: 77)
    fw.mock('fw_socket', lambda *_: 9)
    fw.fn('video_click', C.c_int, C.c_char_p)(b'/m/a.mp4')
    fw.mock('fw_waitpid', lambda *_: 77)
    fw.fn('video_poll', None)()
    assert 'save_start 1' not in lib.calls and lib.now() == (1, -1)
