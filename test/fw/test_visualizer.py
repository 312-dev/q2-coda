"""visualizer.c (docs/ipod.md#visualizer, docs/internals.md#visualizer-ipod): the analysis's bands and dynamics, the
tap's frames, and Now Playing's last page: its timer, its paint, Field's drawing and the track's name at rest.
test/peq.py checks vis_bands' sine levels in its own host build too."""
import ctypes as C
import math
import random
import struct

import pytest

from conftest import O

N, BANDS, W = 1024, 64, 343
DECAY, FLOOR_DB, FPS = 1.6, 60, 25
RET_REMOVE, RET_REPEAT = 7, 8
EVT_DESTROY, EVT_VALUE_CHANGED = O['EVT_DESTROY'], O['EVT_VALUE_CHANGED']
W_X, W_Y, W_W, W_PARENT = O['W_X'], O['W_Y'], O['W_W'], O['W_PARENT']
RING, TAP_SIZE = 65536, 24 + 65536 * 8


class Vz(C.Structure):
    _fields_ = [('re', C.c_float * N), ('im', C.c_float * N), ('pcm', (C.c_float * 2) * N), ('hann', C.c_float * N),
                ('level', C.c_float * BANDS), ('bin_rate', C.c_uint), ('w', C.c_int), ('h', C.c_int), ('bin', C.c_int * 65),
                *((k, C.c_void_p) for k in 'win slide page tap'.split()),
                *((k, C.c_uint) for k in 'timer last heard retry'.split()),
                *((k, C.c_int) for k in 'ox oy page_x'.split()), ('flow', C.c_float), ('glow', (C.c_void_p * 4) * 5),
                ('chrome', C.c_int), ('chrome_at', C.c_uint), ('lift', C.c_uint), ('in_bar', C.c_int), ('quiet', C.c_int)]


def vz(fw):
    return fw.var('visualizer__vz', Vz)


def analyze(fw, rate, dt=0.04):
    fw.fn('vis_analyze', None, C.c_uint, C.c_float)(rate, dt)


def sine(v, amp, k=12.0, phase=0.0, right=None):
    """pcm: a sine of amp, k cycles in the window, in both channels (right: its own amp)."""
    for i in range(N):
        s = math.sin(2 * math.pi * k * i / N + phase)
        v.pcm[i][0], v.pcm[i][1] = amp * s, (amp if right is None else right) * s


def silence(v):
    for i in range(N):
        v.pcm[i][0] = v.pcm[i][1] = 0


# The bands and the bars.

def test_levels_rise_six_tenths_of_the_way_a_tick(fw):
    v = vz(fw)
    sine(v, 1)
    analyze(fw, 44100)
    first = list(v.level)
    target = (C.c_float * BANDS)()
    fw.fn('vis_bands', None, C.c_void_p, C.c_uint, C.c_void_p)(C.addressof(v.pcm), 44100, target)
    assert max(target) > 0.99
    assert all(abs(a - 0.6 * t) < 1e-5 for a, t in zip(first, target))
    analyze(fw, 44100)
    assert all(abs(a - (f + 0.6 * (t - f))) < 1e-5 for a, f, t in zip(v.level, first, target))


def test_levels_fall_at_the_decay_rate_when_the_sound_stops(fw):
    v = vz(fw)
    for b in range(BANDS):
        v.level[b] = 0.9
    levels = []
    for tick in range(14):
        analyze(fw, 0, dt=0.05)
        levels.append(v.level[5])
    want = [max(0, 0.9 - DECAY * 0.05 * (t + 1)) for t in range(14)]
    assert all(abs(a - b) < 1e-5 for a, b in zip(levels, want)) and levels[-1] == 0


def test_a_quieter_frame_falls_no_lower_than_it(fw):
    v = vz(fw)
    sine(v, 0.1)
    target = (C.c_float * BANDS)()
    fw.fn('vis_bands', None, C.c_void_p, C.c_uint, C.c_void_p)(C.addressof(v.pcm), 44100, target)
    for b in range(BANDS):
        v.level[b] = 1
    analyze(fw, 44100, dt=1)
    assert all(abs(a - t) < 1e-6 for a, t in zip(v.level, target))  # 1.6 a second would go under it


def test_the_same_pitch_lands_in_the_same_band_at_any_rate(fw):
    v = vz(fw)
    out = []
    for rate in (44100, 48000, 44100):
        for i in range(N):
            v.pcm[i][0] = v.pcm[i][1] = math.sin(2 * math.pi * 1000 * i / rate)
        target = (C.c_float * BANDS)()
        fw.fn('vis_bands', None, C.c_void_p, C.c_uint, C.c_void_p)(C.addressof(v.pcm), rate, target)
        out.append(list(target).index(max(target)))
        assert v.bin_rate == rate
    band = 64 * math.log(1000 / 40) / math.log(400)  # 40 Hz to 16 kHz, log-spaced
    assert out[0] == out[1] == out[2] and abs(out[0] - band) <= 1


def test_a_sine_at_the_floor_reads_nothing(fw):
    v = vz(fw)
    sine(v, 10 ** (-(FLOOR_DB + 1) / 20))
    target = (C.c_float * BANDS)()
    fw.fn('vis_bands', None, C.c_void_p, C.c_uint, C.c_void_p)(C.addressof(v.pcm), 44100, target)
    assert max(target) == 0


def bands(fw, v, rate=44100):
    target = (C.c_float * BANDS)()
    fw.fn('vis_bands', None, C.c_void_p, C.c_uint, C.c_void_p)(C.addressof(v.pcm), rate, target)
    return list(target)


def spans(v):
    """Each band's bins as the last analysis laid them out at its rate (the test below holds them to the spec):
    from its first bin to the next band's first, at least its first, and none from half the transform up, which
    mirrors the lower half."""
    edge = list(v.bin)
    return [[k for k in range(edge[b], max(edge[b + 1], edge[b] + 1)) if k < N // 2] for b in range(BANDS)]


def heard(span, k, db):
    """Each band's level, its bins span, for a sine exactly k bins up at db dBFS, from the Hann window's spectrum:
    all of it in bin k, half its amplitude (6.02 dB under) in each neighbour and nothing that reaches the floor
    further out. A band is its loudest bin, 0 at FLOOR_DB under full scale and 1 at it."""
    out = []
    for bins in span:
        db_k = db if k in bins else db + 20 * math.log10(0.5) if {k - 1, k + 1} & set(bins) else None
        out.append(None if db_k is None else min(1, max(0, 1 + db_k / FLOOR_DB)))
    return out


@pytest.mark.parametrize('rate', [44100, 48000, 22050])
def test_the_bands_are_log_spaced_from_40_hz_to_16_khz(fw, rate):
    """Band b starts at 40 Hz times 400 to the b/64th and the last ends at 16 kHz: each edge is its frequency's
    bin, within one, and no band starts before the last."""
    v = vz(fw)
    bands(fw, v, rate)
    edge = list(v.bin)
    assert edge == sorted(edge)
    assert all(abs(e - 40 * 400 ** (b / BANDS) * N / rate) < 1 for b, e in enumerate(edge))


@pytest.mark.parametrize('k, db', [(1, -12), (39, 0), (42, -30), (360, -50), (100, 6)])
def test_a_band_reads_its_loudest_bin(fw, k, db):
    """Bin 1 is the first bin of the six lowest bands, narrower than a bin; 39 and 42 are the first and last of
    band 40 at 44.1 kHz; 360 is in the top band. Over full scale (an EQ's boost) reads full."""
    v = vz(fw)
    for i in range(N):
        v.pcm[i][0] = v.pcm[i][1] = 10 ** (db / 20) * math.sin(2 * math.pi * k * i / N)
    got = bands(fw, v)
    want = heard(spans(v), k, db)
    assert any(w == pytest.approx(min(1, 1 + db / FLOOR_DB), abs=1e-9) for w in want)
    for g, w in zip(got, want):
        assert g == pytest.approx(w, abs=2e-3) if w is not None else g < 0.02


def test_a_tone_between_two_bins_stays_in_its_bands(fw):
    """The Hann window's leakage: 1.4 dB down halfway between bins, and under the floor 10 bins out."""
    v = vz(fw)
    for i in range(N):
        v.pcm[i][0] = v.pcm[i][1] = math.sin(2 * math.pi * 100.5 * i / N)
    got = bands(fw, v)
    near = [b for b, bins in enumerate(spans(v)) if {100, 101} & set(bins)]
    far = [b for b, bins in enumerate(spans(v)) if bins and (max(bins) < 90 or min(bins) >= 111)]
    assert near and all(1 - 1.5 / FLOOR_DB < got[b] <= 1 for b in near)
    assert len(far) > BANDS - 12 and all(got[b] == 0 for b in far)


@pytest.mark.parametrize('side', [0, 1])
def test_one_channel_alone_reads_6_db_under_both(fw, side):
    """The bands read the mono mix, half of each channel."""
    v = vz(fw)
    for i in range(N):
        v.pcm[i][side], v.pcm[i][1 - side] = math.sin(2 * math.pi * 42 * i / N), 0
    assert bands(fw, v)[40] == pytest.approx(1 + 20 * math.log10(0.5) / FLOOR_DB, abs=2e-3)


def test_digital_silence_reads_nothing_after_a_loud_frame(fw):
    v = vz(fw)
    for i in range(N):
        v.pcm[i][0] = v.pcm[i][1] = 0.5  # DC: the last transform's bin 0 is large
    bands(fw, v)
    silence(v)
    assert max(bands(fw, v)) == 0


def test_bands_past_half_the_rate_read_nothing(fw):
    """At 22.05 kHz the top bands lie past 11 kHz: a sine's mirror image in the upper half of the transform is
    not a sound there."""
    v = vz(fw)
    for i in range(N):
        v.pcm[i][0] = v.pcm[i][1] = math.sin(2 * math.pi * 300 * i / N)
    got = bands(fw, v, 22050)
    edge = list(v.bin)
    above = [b for b in range(BANDS) if edge[b] >= N // 2]
    assert above and edge[BANDS] > N - 300 and all(got[b] == 0 for b in above)  # the image, bin N - 300, among them
    assert got[next(b for b, bins in enumerate(spans(v)) if 300 in bins)] > 0.99


def test_reset_puts_the_levels_at_rest(fw):
    v = vz(fw)
    sine(v, 1)
    analyze(fw, 44100)
    assert max(v.level) > 0
    fw.fn('vis_reset', None)()
    assert max(v.level) == 0


# The tap: VIS_N frames VIS_LATENCY_MS behind the last write.
class Tap:
    """/tmp/q2vis, mapped: the ring holds each frame's index (left) and its negative (right)."""
    def __init__(self, fw, seq=100000, rate=44100, age_ms=100, exists=True, now_ms=50000, clock=None):
        self.mem = C.create_string_buffer(TAP_SIZE)
        ring = (C.c_float * (2 * RING)).from_address(C.addressof(self.mem) + 24)
        for j in range(RING):
            ring[2 * j], ring[2 * j + 1] = j, -j
        self.set(seq, rate, now_ms * 1000000 - age_ms * 1000000)
        self.fixed, self.clock, self.exists, self.opened = now_ms * 1000000, clock, exists, []
        fw.mock('fw_open', self.open)
        fw.mock('fw_ftruncate', lambda fd, n, *_: 0 if n == TAP_SIZE else -1)
        fw.mock('fw_rename', lambda *_: 0)
        fw.mock('fw_mmap', lambda addr, n, prot, flags, fd, *_: C.addressof(self.mem) if n == TAP_SIZE else -1)
        fw.mock('fw_clock_gettime', lambda which, t, *_: struct.pack_into(
            '<qq', (C.c_char * 16).from_address(t), 0, self.now_ns // 10 ** 9, self.now_ns % 10 ** 9))
        fw.mock('mclGetPlayStatus', lambda *_: 2)

    @property
    def now_ns(self):
        """CLOCK_MONOTONIC: the page's clock when given, else fixed."""
        return self.clock() * 1000000 if self.clock else self.fixed

    def set(self, seq, rate, stamp):
        struct.pack_into('<IIq', self.mem, 0, seq & 0xffffffff, rate, stamp)

    @property
    def want(self):
        return struct.unpack_from('<I', self.mem, 16)[0]

    def open(self, path, flags, *_):
        path = C.string_at(path).decode()
        self.opened.append((path, flags))
        return 5 if self.exists or path.endswith('.tmp') else -1


def capture(fw):
    return fw.fn('visualizer__capture', C.c_uint)()


def f32(x):
    return struct.unpack('f', struct.pack('f', x))[0]


def lag(rate, age_ms):
    """Frames VIS_LATENCY_MS behind the last write, less its age: none ahead of it, at most half the ring."""
    behind = int(f32(rate * f32(f32(0.48) - f32(f32(age_ms * 1e6) * f32(1e-9)))))
    return min(max(behind, 0), RING // 2)


@pytest.mark.parametrize('seq, age_ms', [(100000, 100), (100000, 0), (1000, 300), (5, 480), (100000, 600)])
def test_the_frames_are_480_ms_behind_the_last_write_moved_on_since(fw, seq, age_ms):
    tap = Tap(fw, seq=seq, age_ms=age_ms)
    assert capture(fw) == 44100 and tap.want == 1
    first = (seq - lag(44100, age_ms) - N) % RING
    v = vz(fw)
    assert v.pcm[0][0] == pytest.approx(first, abs=1)
    assert [(v.pcm[i][0] - v.pcm[0][0]) % RING for i in range(N)] == list(range(N))  # in order, round the ring
    assert all(v.pcm[i][1] == -v.pcm[i][0] for i in range(N))  # the right channel from the same frames


def test_at_48_khz_the_frames_are_480_ms_behind_too(fw):
    Tap(fw, rate=48000, age_ms=0)
    assert capture(fw) == 48000
    assert vz(fw).pcm[0][0] == pytest.approx((100000 - 23040 - N) % RING, abs=1)  # 480 ms, well inside half the ring


def test_a_write_older_than_the_latency_gives_its_last_frames(fw):
    """600 ms old: the audio heard now was written before it, so the newest frames are the nearest."""
    Tap(fw, age_ms=600)
    assert capture(fw) == 44100 and vz(fw).pcm[N - 1][0] == (100000 - 1) % RING  # the ring holds each frame's index


@pytest.mark.parametrize('late, rate', [(0, 44100), (1, 0)])
def test_a_write_a_second_old_is_still_heard(fw, late, rate):
    tap = Tap(fw)
    tap.set(100000, 44100, tap.now_ns - 10 ** 9 - late)
    assert capture(fw) == rate


def test_a_capture_writes_its_frames_and_nothing_beside_them(fw):
    tap = Tap(fw)
    v = vz(fw)
    bands(fw, v)  # the window in place
    capture(fw)  # and the tap mapped
    before = C.string_at(C.addressof(v), C.sizeof(v))
    tap.set(100500, 44100, tap.now_ns - 100 * 10 ** 6)
    capture(fw)
    after, start, end = C.string_at(C.addressof(v), C.sizeof(v)), Vz.pcm.offset, Vz.pcm.offset + Vz.pcm.size
    assert after[:start] == before[:start] and after[end:] == before[end:] and after[start:end] != before[start:end]


def test_a_ring_ahead_of_half_its_length_is_held_there(fw):
    Tap(fw, rate=96000, age_ms=0)
    assert capture(fw) == 96000
    assert vz(fw).pcm[0][0] == (100000 - RING // 2 - N) % RING


@pytest.mark.parametrize('rate, age_ms', [(0, 100), (44100, 1001), (44100, -1)])
def test_nothing_written_lately_is_silence(fw, rate, age_ms):
    tap = Tap(fw, rate=rate, age_ms=age_ms)
    assert capture(fw) == 0 and tap.want == 1


def test_not_playing_is_silence_and_lets_the_writer_stop(fw):
    tap = Tap(fw)
    fw.mock('mclGetPlayStatus', lambda *_: 3)
    assert capture(fw) == 0 and tap.want == 0


def test_the_tap_is_made_whole_under_another_name_first(fw):
    tap = Tap(fw, exists=False)
    assert capture(fw) == 44100
    assert tap.opened == [('/tmp/q2vis', 2), ('/tmp/q2vis.tmp', 0x302)]
    assert [(C.string_at(a[0]), C.string_at(a[1])) for _, a in fw.calls('fw_rename')] == [(b'/tmp/q2vis.tmp', b'/tmp/q2vis')]
    assert capture(fw) == 44100
    assert len(tap.opened) == 2  # mapped once


def test_a_tap_already_there_is_mapped_as_it_is(fw):
    """hciplayer may have it mapped: a new file under its name would leave the writer on the old one."""
    tap = Tap(fw)
    assert capture(fw) == 44100
    assert tap.opened == [('/tmp/q2vis', 2)] and fw.calls('fw_ftruncate') == fw.calls('fw_rename') == []


@pytest.mark.parametrize('exists', [True, False])
def test_descriptor_0_is_a_file_like_any(fw, exists):
    """With demo's stdin closed, open can answer 0."""
    tap = Tap(fw, exists=exists)
    fw.mock('fw_open', lambda *a: 0 if tap.open(*a) == 5 else -1)
    assert capture(fw) == 44100 and len(tap.opened) == 1 + (not exists)
    assert [a[4] & 0xffffffff for _, a in fw.calls('fw_mmap')] == [0]


def test_the_tap_is_mapped_whole_shared_and_writable(fw):
    """demo bumps want in the writer's own pages: read and write, MAP_SHARED, from the start."""
    Tap(fw)
    capture(fw)
    (_, (addr, size, prot, flags, fd, offset)), = fw.calls('fw_mmap')
    assert (addr, size, prot & 0xffffffff, flags & 0xffffffff, fd & 0xffffffff, offset) == (0, TAP_SIZE, 3, 1, 5, 0)


@pytest.mark.parametrize('fail', ['open', 'ftruncate', 'rename', 'mmap'])
def test_no_tap_is_silence(fw, fail):
    tap = Tap(fw, exists=False)
    if fail == 'open':
        fw.mock('fw_open', lambda *_: -1)
    elif fail == 'ftruncate':
        fw.mock('fw_ftruncate', lambda *_: -1)
    elif fail == 'rename':
        fw.mock('fw_rename', lambda *_: -1)
    else:
        fw.mock('fw_mmap', lambda *_: -1)
    assert capture(fw) == 0 and tap.want == 0 and vz(fw).tap is None
    assert len(fw.calls('fw_close')) == (fail != 'open')


# The page.

class Now:
    """Now Playing's window: its slide_view (inside a view at box_x, 60), the page dots, the cover; showing on
    the visualizer page with the screen on unless told otherwise; the clock; the timers. Its widgets sit in its
    views, so only a recursive lookup finds them. page: whether stock makes the page's list_item."""
    def __init__(self, fw, width=375, lrc_y=0, now=100000, box_x=0, slide_x=0, page=True, dots=True, win_y=0,
                 slide_h=None):
        self.fw, self.now, self.blocks, self.timers = fw, now, [], []
        self.win = self.widget(0, win_y, 375, 320)
        self.box = self.widget(box_x, 60, 375, 200, self.win)
        self.slide = self.widget(slide_x, 0, width, 186 + lrc_y if slide_h is None else slide_h, self.box)  # as laid out
        self.cover, self.dots = self.widget(0, 0, 200, 200), self.widget(0, 0, 60, 10) if dots else 0
        self.lrc = self.widget(0, lrc_y, width, 100) if lrc_y else 0
        self.pages = 3 if page else 2  # art, lyrics and this
        self.value, self.animating, self.pressed, self.top, self.visible = self.pages - 1, 0, 0, self.win, {}
        fw.mock('widget_set_visible', lambda w, on, *_: self.visible.__setitem__(w, on))
        fw.mock('widget_get_visible', lambda w, *_: self.visible.get(w, 0))
        found = {'slide_view': self.slide, 'slide_indicator1': self.dots, 'img_cover': self.cover, 'view_lrc': self.lrc}
        fw.mock('widget_lookup', lambda w, name, deep, *_: found.get(C.string_at(name).decode(), 0) if deep else 0)
        fw.mock('list_item_create', lambda parent, x, y, w, h, *_: self.widget(x, y, w, h, parent) if page else 0)
        fw.mock('widget_count_children', lambda w, *_: self.pages if w == self.slide else 0)
        fw.mock('widget_get_prop_int', self.prop_int)
        fw.mock('window_manager_get_top_window', lambda *_: self.top)
        fw.mock('window_manager_is_animating', lambda *_: self.animating)
        fw.mock('window_manager_get_pointer_pressed', lambda *_: self.pressed)
        fw.mock('time_now_ms', lambda *_: self.now)
        fw.mock('timer_add', lambda fn, ctx, ms, *_: self.timers.append((fn, ms)) or len(self.timers))
        fw.var('g_backlight_status', C.c_ubyte).value = 1
        fw.var('g_equalizer_flag', C.c_ubyte).value = 1  # the filter is in already: peq_chain leaves it
        fw.fn('visualizer_attach', None, C.c_void_p)(self.win)
        self.v = vz(fw)

    def widget(self, x, y, w, h, parent=0):
        self.blocks.append(C.create_string_buffer(0x200))
        struct.pack_into('<4i', self.blocks[-1], 0, x, y, w, h)
        struct.pack_into('<Q', self.blocks[-1], W_PARENT, parent)
        return C.addressof(self.blocks[-1])

    def prop_int(self, w, key, default, *_):
        return self.value if w == self.slide and C.string_at(key) == b'value' else default

    def fire(self, ms):
        """The newest timer of that period, run as the next loop pass would."""
        fn = [f for f, p in self.timers if p == ms][-1]
        return C.CFUNCTYPE(C.c_int, C.c_void_p)(fn)(0)

    def handler(self, w, event):
        (fn, ctx), = [(fn, ctx) for _, (ww, evt, fn, ctx, *_) in self.fw.calls('widget_on') if ww == w and evt == event]
        return lambda: C.CFUNCTYPE(C.c_int, C.c_void_p, C.c_void_p)(fn)(ctx, 0)

    def invalidated(self):
        return len([1 for _, a in self.fw.calls('widget_invalidate_force') if a[0] == self.slide])


class Canvas:
    """The window's canvas: its origin, its lcd and clip; the text drawn on it (read as drawn) and its colour.
    drawn: each text's rect, colour, font (face, px) and the canvas origin it was drawn at."""
    def __init__(self, fw, x=0, y=0):
        self.raw, self.lcd = C.create_string_buffer(0x200), C.create_string_buffer(0x200)
        struct.pack_into('<ii', self.raw, 0, x, y)
        struct.pack_into('<Q', self.raw, 0x38, C.addressof(self.lcd))
        self.p, self.texts, self.color, self.clips = C.addressof(self.raw), [], 0, []
        self.drawn, self.font, self.measured = [], None, []  # measured: the font each measure was made in
        fw.mock('canvas_get_clip_rect', lambda c, r, *_: struct.pack_into('<4i', (C.c_char * 16).from_address(r), 0,
                                                                          0, 0, 375, 320))
        fw.mock('canvas_set_clip_rect', lambda c, r, *_: self.clips.append(tuple((C.c_int * 4).from_address(r))))
        fw.mock('canvas_set_text_color', lambda c, color, *_: setattr(self, 'color', color))
        fw.mock('canvas_set_font', lambda c, face, px, *_: setattr(self, 'font', (face and C.string_at(face), px)))
        fw.mock('canvas_draw_text_in_rect', self.draw)

    def draw(self, c, s, n, r, *_):
        text = ''.join(map(chr, (C.c_uint * n).from_address(s)))
        self.texts.append((text, self.color >> 24 & 255))
        self.drawn.append({'text': text, 'rect': tuple((C.c_int * 4).from_address(r)), 'color': self.color,
                           'font': self.font, 'origin': self.origin})

    @property
    def origin(self):
        return struct.unpack_from('<ii', self.raw, 0)


def paint(fw, w, canvas):
    fw.fn('visualizer_paint', None, C.c_void_p, C.c_void_p)(w, canvas.p)


def drew(fw):
    """Field's paint looked for its sprites: the page was drawn."""
    return bool(fw.calls('bitmap_create_ex'))


def test_the_page_joins_the_slide_view_and_its_dots(fw):
    now = Now(fw)
    v = now.v
    page = struct.unpack_from('<4i', (C.c_char * 16).from_address(v.page))
    assert page == (0, 0, 375, 186) and C.c_void_p.from_address(v.page + W_PARENT).value == now.slide
    assert (v.ox, v.oy, v.w) == (0, 60, 375)
    assert [(a[0], C.string_at(a[1]), a[2]) for _, a in fw.calls('widget_set_prop_int') if a[0] == now.dots] == \
        [(now.dots, b'max', 3)]
    assert [(a[0], C.string_at(a[1])) for _, a in fw.calls('widget_use_style')] == [(v.page, b's_listitem_black')]
    assert [a for _, a in fw.calls('widget_on') if a[0] == v.page] == []  # a tap does nothing


def test_the_slide_area_s_place_counts_from_the_window_wherever_it_sits(fw):
    """The canvas has the window's own place already (under a status bar, say)."""
    now = Now(fw, win_y=30)
    assert (now.v.ox, now.v.oy) == (0, 60)


def test_without_page_dots_the_page_still_joins(fw):
    assert Now(fw, dots=False).v.page and fw.calls('widget_set_prop_int') == []


@pytest.mark.parametrize('width, page', [(375, 375), (225, 225), (0, 375), (376, 375), (400, 375)])
def test_the_page_is_its_slide_s_width_up_to_the_screen_s(fw, width, page):
    """A slide_view not laid out yet, or wider than the screen, has the screen's width: the buffers'."""
    assert Now(fw, width=width).v.w == page


def test_classic_s_centred_slide_draws_over_itself(fw):
    """The 225-wide slide sits 75 px into its view: the drawing goes there with it."""
    now = Now(fw, width=225, slide_x=75)
    v = now.v
    assert (v.ox, v.oy) == (75, 60)
    paint(fw, v.page, Canvas(fw, x=75))  # the page first, at rest: at the slide area's own x
    canvas = Canvas(fw)
    paint(fw, now.win, canvas)
    assert v.page_x == 0 and canvas.clips[0] == (75, 60, 225, 186)


def test_classic_s_centred_page_starts_where_its_lyrics_do(fw):
    now = Now(fw, width=225, lrc_y=18)
    page = struct.unpack_from('<4i', (C.c_char * 16).from_address(now.v.page))
    assert page == (0, 18, 225, 186) and (now.v.w, now.v.oy) == (225, 78)


@pytest.mark.parametrize('slide_h, lrc_y, h', [(160, 0, 160), (178, 18, 160), (186, 0, 186), (250, 18, 186)])
def test_a_slide_that_ends_sooner_ends_the_page_with_it(fw, slide_h, lrc_y, h):
    """OLED's slide stops above its progress bar (tools/ipod.py np_grid): the page takes the slide's height below the
    lyrics' top where that is under the 186 px a slide has elsewhere, which stays the page's height at most."""
    now = Now(fw, lrc_y=lrc_y, slide_h=slide_h)
    page = struct.unpack_from('<4i', (C.c_char * 16).from_address(now.v.page))
    assert page == (0, lrc_y, 375, h) and now.v.h == h


def test_a_saved_style_is_never_read(fw):
    """IPOD/VIS, which an older firmware's config.ini may still hold, is never read: Field is the one style."""
    asked = []
    fw.mock('toolsReadConfig', lambda path, section, key, *_: asked.append(C.string_at(key)) or 0)
    Now(fw)
    assert b'VIS' not in asked


def test_no_slide_view_no_page(fw):
    fw.mock('widget_lookup', lambda *_: 0)
    fw.fn('visualizer_attach', None, C.c_void_p)(0x4000)
    assert vz(fw).page is None and fw.calls('list_item_create') == []


def test_painting_the_window_on_the_page_starts_its_timer_once(fw):
    now = Now(fw)
    canvas = Canvas(fw)
    paint(fw, now.win, canvas)
    paint(fw, now.win, canvas)
    assert [ms for _, ms in now.timers] == [1000 // FPS] and now.v.timer == 1


@pytest.mark.parametrize('change', ['value', 'top', 'screen'])
def test_no_timer_unless_the_page_shows(fw, change):
    now = Now(fw)
    if change == 'value':
        now.value = 1
    elif change == 'top':
        now.top = 0x9999
    else:
        fw.var('g_backlight_status', C.c_ubyte).value = 0
    paint(fw, now.win, Canvas(fw))
    assert [ms for _, ms in now.timers if ms == 1000 // FPS] == []
    if change == 'value':  # on another page nothing waits to paint this one either
        assert now.timers == []


def test_a_paint_under_another_window_draws_nothing_and_looks_again(fw):
    now = Now(fw)
    now.top = 0x9999  # the lock screen, closing over Now Playing
    paint(fw, now.win, Canvas(fw))
    assert not drew(fw) and [ms for _, ms in now.timers] == [250]


def test_a_look_under_another_window_looks_again(fw):
    now = Now(fw)
    now.animating = 1
    paint(fw, now.win, Canvas(fw))
    now.animating, now.top = 0, 0x9999  # the slide ended under an overlay
    count = now.invalidated()
    assert now.fire(250) == RET_REPEAT and now.invalidated() == count


def test_no_page_leaves_the_lyrics_page_alone(fw):
    """list_item_create failed: the slide_view keeps its two pages, the last of them the lyrics page."""
    now = Now(fw, page=False)
    paint(fw, now.win, Canvas(fw))
    assert now.v.page is None and now.timers == [] and not drew(fw)


def test_a_paint_while_the_window_slides_looks_again_250_ms_on(fw):
    now = Now(fw)
    now.animating = 1
    canvas = Canvas(fw)
    paint(fw, now.win, canvas)
    paint(fw, now.win, canvas)
    assert [ms for _, ms in now.timers].count(250) == 1 and not drew(fw)
    assert now.fire(250) == RET_REPEAT  # still sliding
    now.animating = 0
    count = now.invalidated()
    assert now.fire(250) == 0 and now.invalidated() == count + 1 and now.v.retry == 0


def test_a_look_after_the_page_went_does_nothing(fw):
    now = Now(fw)
    now.animating = 1
    paint(fw, now.win, Canvas(fw))
    now.value = 1
    count = now.invalidated()
    assert now.fire(250) == 0 and now.invalidated() == count


def test_a_page_mid_slide_paints_nothing(fw):
    now = Now(fw)
    canvas = Canvas(fw, x=-120)
    paint(fw, now.v.page, canvas)  # the page paints first: 120 px off its rest
    assert now.v.page_x == -120
    paint(fw, now.win, Canvas(fw))
    assert not drew(fw) and [ms for _, ms in now.timers].count(250) == 1


def test_the_drawing_is_clipped_to_the_slide_area_and_the_canvas_given_back(fw):
    now = Now(fw)
    canvas = Canvas(fw, x=0, y=0)
    paint(fw, now.win, canvas)
    assert canvas.clips == [(0, 60, 375, 186), (0, 0, 375, 320)] and canvas.origin == (0, 0)
    assert len(fw.calls('bitmap_create_ex')) == 1  # Field's paint, its sprites not made here


def test_a_clip_clear_of_the_slide_area_draws_nothing(fw):
    now = Now(fw)
    canvas = Canvas(fw)
    fw.mock('canvas_get_clip_rect', lambda c, r, *_: struct.pack_into('<4i', (C.c_char * 16).from_address(r), 0,
                                                                      0, 280, 375, 40))  # the bar under it only
    paint(fw, now.win, canvas)
    assert canvas.clips == [] and not drew(fw) and now.v.timer == 1


def test_no_lcd_no_paint(fw):
    now = Now(fw)
    canvas = Canvas(fw)
    struct.pack_into('<Q', canvas.raw, 0x38, 0)
    paint(fw, now.win, canvas)
    assert now.timers == [] and not drew(fw)


def test_paint_of_another_widget_does_nothing(fw):
    now = Now(fw)
    paint(fw, now.slide, Canvas(fw))
    paint(fw, 0, Canvas(fw))
    assert now.timers == []


def test_the_drawing_moves_to_the_slide_area_and_the_canvas_comes_back(fw):
    """The window under the status bar paints at its own place; the slide area is 12, 60 into it."""
    now = Now(fw, box_x=12, now=100001)
    canvas = Canvas(fw, x=3, y=30)
    origins = []
    fw.mock('bitmap_create_ex', lambda *_: origins.append(canvas.origin) or 0)  # Field's first step
    paint(fw, now.win, canvas)
    assert canvas.clips == [(15, 90, 360, 186), (0, 0, 375, 320)] and canvas.origin == (3, 30)
    assert origins == [(15, 90)]


def ticking(fw, playing=True, **page):
    now = Now(fw, **page)
    tap = Tap(fw, clock=lambda: now.now)
    fw.mock('time_now_ms', lambda *_: now.now)
    if not playing:
        fw.mock('mclGetPlayStatus', lambda *_: 3)
    paint(fw, now.win, Canvas(fw))
    return now, tap


def tick(now, step=40, sound=None):
    """The timer step ms on; with sound, a Tap, hciplayer wrote 10 ms before it."""
    now.now += step
    if sound:
        struct.pack_into('<q', sound.mem, 8, (now.now - 10) * 1000000)
    return now.fire(1000 // FPS)


def test_each_tick_while_playing_analyses_and_repaints(fw):
    now, tap = ticking(fw)
    struct.pack_into('<q', tap.mem, 8, now.now * 1000000)  # written 40 ms before the tick
    count = now.invalidated()
    assert tick(now) == RET_REPEAT
    assert now.v.heard == now.now and tap.want == 1 and now.invalidated() == count + 1
    assert fw.fn('visualizer_live', C.c_int)() == 1


def test_the_timer_ends_once_the_page_doesn_t_show(fw):
    now, _ = ticking(fw)
    now.value = 1
    assert tick(now) == RET_REMOVE and now.v.timer == 0 and fw.fn('visualizer_live', C.c_int)() == 0


def test_a_finger_held_on_the_page_holds_no_repaint(fw):
    """#303: under a resting finger the page drew only when the bar's second repainted it."""
    now, tap = ticking(fw)
    now.pressed = 1
    count = now.invalidated()
    tick(now, sound=tap)
    assert now.invalidated() == count + 1


def test_at_rest_one_repaint_puts_the_track_s_name_up_then_none(fw):
    now, _ = ticking(fw, playing=False)
    now.v.heard = 0
    counts, start = [], now.invalidated()
    for _ in range(4):
        assert tick(now) == RET_REPEAT  # the timer stays for the music's return
        counts.append(now.invalidated())
    assert now.v.quiet == 1 and counts == [start + 1] * 4
    assert fw.fn('visualizer_live', C.c_int)() == 0


def test_at_rest_a_finger_held_on_the_page_still_gets_the_track_s_name(fw):
    now, _ = ticking(fw, playing=False)
    now.v.heard = 0
    now.pressed, count = 1, now.invalidated()
    tick(now)
    assert now.v.quiet == 1 and now.invalidated() == count + 1


def test_the_page_is_live_or_at_rest_never_both(fw):
    """Not playing: 1.5 s after the last audio the repaints stop and the track's name comes up, at the same
    millisecond."""
    now, _ = ticking(fw, playing=False)
    v = now.v
    for since in (100, 1499, 1500, 1501, 1600, 1500, 1499):
        v.heard = now.now + 40 - since
        tick(now)
        assert (v.quiet, fw.fn('visualizer_live', C.c_int)()) == ((1, 0) if since > 1500 else (0, 1))


def test_playing_with_nothing_heard_is_not_at_rest(fw):
    """DSD, or a filter that writes nothing: playing, so the track's name stays down."""
    now, _ = ticking(fw)
    now.v.heard = 0
    for _ in range(3):
        tick(now)
    assert now.v.quiet == 0


def test_the_page_leaving_stops_the_repaints_at_once(fw):
    now, tap = ticking(fw)
    tick(now, sound=tap)
    assert fw.fn('visualizer_live', C.c_int)() == 1
    now.value = 1
    assert tick(now, sound=tap) == RET_REMOVE and fw.fn('visualizer_live', C.c_int)() == 0


def test_the_first_tick_puts_the_filter_in_for_the_tap(fw):
    now, _ = ticking(fw)
    fw.var('g_equalizer_flag', C.c_ubyte).value = 0  # the EQ is off: boot left the filter out
    tick(now)
    assert fw.calls('stock_eq_trampoline')


def test_a_tick_moves_by_the_time_since_the_last_at_most_a_tenth_of_a_second(fw):
    now, _ = ticking(fw)
    v = now.v
    v.heard = now.now  # the audio just stopped: the bars fall
    for b in range(BANDS):
        v.level[b] = 0.9
    falls = []
    for step in (40, 40, 300, 10):
        before = v.level[0]
        tick(now, step)
        falls.append(before - v.level[0])
    assert falls == pytest.approx([DECAY * 0.04, DECAY * 0.04, DECAY * 0.1, DECAY * 0.01], abs=1e-6)


def test_ticks_show_the_audio_from_its_first_frame_and_its_fall_after(fw):
    now, tap = ticking(fw)
    ring = (C.c_float * (2 * RING)).from_address(C.addressof(tap.mem) + 24)
    for j in range(RING):  # a tone in both channels: the Tap's own frames cancel in the mono mix
        ring[2 * j] = ring[2 * j + 1] = 0.5 * math.sin(2 * math.pi * j / 32)
    tick(now, sound=tap)  # the first with audio, after a long silence
    up = max(now.v.level)
    assert up > 0
    fw.mock('mclGetPlayStatus', lambda *_: 3)  # paused
    tick(now)
    assert 0 < max(now.v.level) < up


def test_a_slide_holds_the_repaints_while_playing_and_a_held_finger_does_not(fw):
    now, tap = ticking(fw)
    tick(now, sound=tap)
    now.pressed, count = 1, now.invalidated()
    assert tick(now, sound=tap) == RET_REPEAT and now.invalidated() == count + 1
    now.animating = 1
    count = now.invalidated()
    assert tick(now, sound=tap) == RET_REPEAT and now.invalidated() == count


def test_field_s_clock_runs_with_the_music_alone(fw):
    """flow, Field's turning, moves by the time between ticks while audio is heard, and stands while it isn't."""
    now, tap = ticking(fw)
    flows = [now.v.flow]
    for step in (40, 80):
        tick(now, step, sound=tap)
        flows.append(now.v.flow)
    assert flows[1] - flows[0] == pytest.approx(0.04) and flows[2] - flows[1] == pytest.approx(0.08)
    fw.mock('mclGetPlayStatus', lambda *_: 3)  # paused
    tick(now)
    assert now.v.flow == flows[2]


@pytest.mark.parametrize('since, kept', [(1000, True), (1500, True), (1501, False), (5000, False)])
def test_a_page_shown_again_keeps_its_history_while_the_audio_is_live(fw, since, kept):
    """A slide back onto the page within 1.5 s of the audio carries on; later it starts at rest."""
    now = Now(fw)
    v = now.v
    v.level[0], v.heard = 0.5, now.now - since
    now.handler(now.slide, EVT_VALUE_CHANGED)()
    assert v.level[0] == (0.5 if kept else 0)


def quiet_paint(fw, now, title, artist, width_px=7, quiet=1, full=False):
    """The page at rest with the track's title and artist labels (None: no such label), each letter width_px wide:
    the text drawn, or the canvas (full), whose measured list holds the font each measure was made in."""
    labels = {k: v for k, v in (('scrlabel_title', title), ('scrlabel_artist', artist)) if v is not None}
    wide = {k: (C.c_uint * (len(v) + 1))(*map(ord, v), 0) for k, v in labels.items()}
    widgets = {k: 0x6100 + i for i, k in enumerate(labels)}
    found = {'slide_view': now.slide}
    fw.mock('widget_lookup', lambda w, name, deep, *_: deep and widgets.get(C.string_at(name).decode(), found.get(C.string_at(name).decode(), 0)))
    fw.mock('widget_get_text', lambda w, *_: C.addressof(wide[{v: k for k, v in widgets.items()}[w]]) if w in widgets.values() else 0)
    canvas = Canvas(fw)
    fw.mock('canvas_measure_text', lambda c, s, n, *_: canvas.measured.append(canvas.font) or float(n * width_px))
    now.v.quiet = quiet
    paint(fw, now.win, canvas)
    return canvas if full else [t for t, _ in canvas.texts]


def test_at_rest_the_track_shows_as_title_and_artist(fw):
    now = Now(fw)
    assert quiet_paint(fw, now, 'Song', 'Band') == ['Song · Band']


def test_a_track_too_long_for_both_shows_its_title(fw):
    now = Now(fw)
    assert quiet_paint(fw, now, 'T' * 40, 'A' * 20) == ['T' * 40]


def test_a_title_too_long_alone_ends_in_an_ellipsis(fw):
    now = Now(fw)
    shown, = quiet_paint(fw, now, 'T' * 90, '')
    assert shown == 'T' * 48 + '…' and len(shown) * 7 <= W


def test_no_track_no_text(fw):
    now = Now(fw)
    assert quiet_paint(fw, now, '', '') == []


def test_playing_no_track_name(fw):
    now = Now(fw)
    assert quiet_paint(fw, now, 'Song', 'Band', quiet=0) == []


@pytest.mark.parametrize('title, artist, shown', [
    ('Song', '', 'Song'), ('', 'Band', 'Band'), ('Song', None, 'Song'), (None, 'Band', 'Band'),
    ('S' * 20, 'B' * 26, 'S' * 20 + ' · ' + 'B' * 26),  # exactly the page's width: whole
    ('T' * 49, 'A', 'T' * 49),  # the title alone exactly fits
    ('', 'A' * 60, 'A' * 48 + '…'), ('T' * 300, 'Band', 'T' * 48 + '…'), ('S', 'A' * 300, 'S'), ('', 'A' * 300, 'A' * 48 + '…')])
def test_the_track_s_name_fits_the_page(fw, title, artist, shown):
    """title · artist, the title alone if both don't fit, cut to fit with an ellipsis; no separator without
    both; no such label is no text."""
    assert quiet_paint(fw, Now(fw), title, artist) == [shown]


def test_a_text_too_wide_for_even_a_letter_is_an_ellipsis(fw):
    assert quiet_paint(fw, Now(fw), 'Song', 'Band', width_px=1000) == ['…']


def test_the_track_s_name_is_measured_in_the_font_it_is_drawn_in(fw):
    canvas = quiet_paint(fw, Now(fw), 'T' * 40, 'A' * 20, full=True)
    (drawn,) = canvas.drawn
    assert len(canvas.measured) > 1 and set(canvas.measured) == {drawn['font']}


def test_the_window_s_end_forgets_it(fw):
    now, _ = ticking(fw)
    now.handler(now.win, EVT_DESTROY)()
    v = now.v
    assert (v.win, v.slide, v.page, v.timer) == (None, None, None, 0)
    paint(fw, now.win, Canvas(fw))
    assert len([1 for _, ms in now.timers if ms == 1000 // FPS]) == 1


def test_a_slide_onto_the_page_starts_the_timer(fw):
    now = Now(fw)
    now.handler(now.slide, EVT_VALUE_CHANGED)()
    assert [ms for _, ms in now.timers] == [1000 // FPS]


def test_the_handlers_keep_themselves_and_pass_the_event_on(fw):
    """RET_OK: not RET_REMOVE, which drops the handler, nor RET_STOP."""
    now = Now(fw)
    assert now.handler(now.slide, EVT_VALUE_CHANGED)() == 0
    assert now.handler(now.win, EVT_DESTROY)() == 0


def test_the_last_window_s_end_leaves_the_next_one(fw):
    """Now Playing again before the last one's destroy event: that event is the old window's."""
    old = Now(fw)
    gone = old.handler(old.win, EVT_DESTROY)
    new = Now(fw)
    gone()
    assert (new.v.win, new.v.slide) == (new.win, new.slide) and new.v.page


def test_the_window_s_end_stops_a_pending_look(fw):
    now = Now(fw)
    now.animating = 1
    paint(fw, now.win, Canvas(fw))
    look = now.v.retry
    now.handler(now.win, EVT_DESTROY)()
    assert look and now.v.retry == 0 and look in [a[0] for _, a in fw.calls('timer_remove')]


def oled_now(fw):
    """Now under OLED (THEME 3, the theme with pages.own.painter="oled")."""
    def config(path, section, key, out, default, *_):
        value = b'3' if C.string_at(key) == b'THEME' else C.string_at(default)
        C.memmove(out, value + b'\0', len(value) + 1)
    fw.mock('toolsReadConfig', config)
    return Now(fw)


def test_field_draws_five_bands_of_glow_sprites_inside_the_screen(fw):
    """Each band's FIELD_DOTS sprites, 32 px, drawn whole at their size (no scaling), all round the page's centre;
    the sprites are made once, four strengths a band, and a sprite's corners are clear."""
    now = oled_now(fw)
    bufs, made, drawn = [], [], []

    def create(w, h, line, fmt, *_):
        bufs.append(C.create_string_buffer(w * h * 4))
        made.append((w, h, fmt))
        return len(bufs)
    fw.mock('bitmap_create_ex', create)
    fw.mock('bitmap_lock_buffer_for_write', lambda bm, *_: C.addressof(bufs[bm - 1]))
    fw.mock('bitmap_get_line_length', lambda bm, *_: 32 * 4)
    fw.mock('bitmap_unlock_buffer', lambda *_: 0)
    fw.mock('canvas_draw_image', lambda c, bm, src, dst, *_: drawn.append(
        (bm, tuple((C.c_int * 4).from_address(src)), tuple((C.c_int * 4).from_address(dst)))) or 0)
    for _ in range(2):
        paint(fw, now.win, Canvas(fw))
    assert made == [(32, 32, 1)] * 20 and len(drawn) == 2 * 5 * 40
    assert all(src == (0, 0, 32, 32) and dst[2:] == (32, 32) for _, src, dst in drawn)
    cx, cy = 375 / 2, 320 / 2  # the screen's: OLED's Field fills it (full_paint)
    assert all(abs(x + 16 - cx) <= 375 / 2 and abs(y + 16 - cy) <= 320 / 2 for _, _, (x, y, _, _) in drawn)
    corner = struct.unpack_from('<I', bufs[0], 0)[0]
    centre = struct.unpack_from('<I', bufs[0], (16 * 32 + 16) * 4)[0]
    assert corner >> 24 == 0 and centre >> 24 > 0x80


# OLED's Field over the whole screen: the page's other parts only while a thumb rests on the wheel.
class Full:
    """oled_now with the parts full_paint paints again by name, the system bar, the sprites and every widget_paint,
    canvas_fill_rect and global alpha as called."""
    PARTS = ('slider_play', 'label_playtime', 'label_ipod_remain', 'slide_indicator1')

    def __init__(self, fw):
        self.fw, self.now = fw, oled_now(fw)
        self.parts = {n: self.now.widget(0, 0, 10, 10, self.now.win) for n in self.PARTS}
        self.bar = self.now.widget(0, 0, 375, 58)
        fw.var('fw_ctx_system_bar', C.c_void_p).value = self.bar
        found = {'slide_view': self.now.slide, 'img_cover': self.now.cover, **self.parts}
        fw.mock('widget_lookup', lambda w, name, deep, *_: found.get(C.string_at(name).decode(), 0) if deep else 0)
        self.painted, self.alpha, self.fills, self.dots = [], [], [], []
        fw.mock('widget_paint', lambda w, c, *_: self.painted.append(w) or 0)
        fw.mock('canvas_set_global_alpha', lambda c, a, *_: self.alpha.append(a) or 0)
        fw.mock('canvas_fill_rect', lambda c, x, y, w, h, *_: self.fills.append((x, y, w, h)) or 0)
        fw.mock('canvas_draw_image', lambda c, bm, src, dst, *_: self.dots.append(tuple((C.c_int * 4).from_address(dst))) or 0)
        bufs = []
        fw.mock('bitmap_create_ex', lambda w, h, line, fmt, *_: bufs.append(C.create_string_buffer(w * h * 4)) or len(bufs))
        fw.mock('bitmap_lock_buffer_for_write', lambda bm, *_: C.addressof(bufs[bm - 1]))
        fw.mock('bitmap_get_line_length', lambda bm, *_: 32 * 4)
        fw.mock('bitmap_unlock_buffer', lambda *_: 0)
        self.bufs = bufs
        self.held = fw.var('wheel_held', C.c_int)

    def frame(self, at, held=None):
        """A paint at now + at ms, with the thumb resting or not."""
        if held is not None:
            self.held.value = held
        self.now.now = 100000 + at
        self.painted.clear(), self.alpha.clear(), self.fills.clear(), self.dots.clear()
        paint(self.fw, self.now.win, Canvas(self.fw, 0, 58))
        return self.now.v.chrome


def test_oled_s_field_fills_the_screen_round_its_centre(fw):
    """The window starts under the status row (y 58): Field draws from the screen's top, black first, its sprites round
    the screen's centre and reaching past the slide's foot."""
    full = Full(fw)
    full.frame(0, held=0)
    assert full.fills == [(0, 0, 375, 320)]
    ys = [y + 16 for _, y, _, _ in full.dots]
    xs = [x + 16 for x, _, _, _ in full.dots]
    assert len(full.dots) == 200 and max(ys) > 260 and min(ys) < 60 and max(xs) - min(xs) > 260
    assert abs(sum(ys) / len(ys) - 160) < 12 and abs(sum(xs) / len(xs) - 187.5) < 12


def test_oled_s_field_hides_the_rest_of_the_page_until_a_thumb_rests(fw):
    """No status row, bar, times, dots or song line with the thumb off the wheel; resting, they fade in over 150 ms
    and are painted again over Field at that alpha."""
    full = Full(fw)
    assert full.frame(0, held=0) == 0 and full.painted == [] and full.alpha == []
    assert 0 < full.frame(75, held=1) < 255 and full.alpha[0] == full.now.v.chrome
    assert full.frame(160) == 255
    assert full.painted == [full.parts[n] for n in Full.PARTS] and full.alpha == [255, 255]


def test_oled_s_page_stays_1_5_s_after_the_thumb_lifts_then_fades(fw):
    full = Full(fw)
    full.frame(0, held=1)
    full.frame(200)
    assert full.frame(250, held=0) == 255  # lifted at 200 (the last frame that saw it)
    assert full.frame(1650) == 255
    assert 0 < full.frame(1750) < 255
    assert full.frame(1900) == 0 and full.painted == []


def test_another_theme_draws_field_in_the_slide_area(fw):
    """Only OLED fills the screen: another theme's Field stays in the slide area, round its centre, with the page's
    parts as they are."""
    now, bufs, dots, fills = Now(fw), [], [], []
    fw.mock('bitmap_create_ex', lambda w, h, line, fmt, *_: bufs.append(C.create_string_buffer(w * h * 4)) or len(bufs))
    fw.mock('bitmap_lock_buffer_for_write', lambda bm, *_: C.addressof(bufs[bm - 1]))
    fw.mock('bitmap_get_line_length', lambda bm, *_: 32 * 4)
    fw.mock('bitmap_unlock_buffer', lambda *_: 0)
    fw.mock('canvas_draw_image', lambda c, bm, src, dst, *_: dots.append(tuple((C.c_int * 4).from_address(dst))) or 0)
    fw.mock('canvas_fill_rect', lambda c, x, y, w, h, *_: fills.append((x, y, w, h)) or 0)
    canvas = Canvas(fw)
    paint(fw, now.win, canvas)
    xs, ys = [x + 16 for x, _, _, _ in dots], [y + 16 for _, y, _, _ in dots]
    assert len(dots) == 200 and fills == [] and canvas.clips[0] == (0, 60, 375, 186)
    assert abs(sum(xs) / len(xs) - 187.5) < 12 and abs(sum(ys) / len(ys) - 93) < 12  # the slide area's own


@pytest.mark.parametrize('oled', [True, False])
def test_a_slide_off_oled_s_field_repaints_the_window_and_the_status_row_whole(fw, oled):
    """Field covered the whole screen; the slide repaints the slide area alone, so without this the rows around it
    (shuffle and repeat's among them) kept Field's last frame (seen in the emulator). Another theme's Field stays in
    the slide area, which the slide repaints."""
    full = Full(fw) if oled else None
    now = full.now if oled else Now(fw)
    if not oled:
        fw.var('fw_ctx_system_bar', C.c_void_p).value = now.widget(0, 0, 375, 58)
    bar = C.c_void_p.in_dll(fw.lib, 'fw_ctx_system_bar').value
    slid = now.handler(now.slide, EVT_VALUE_CHANGED)
    before = [a[0] for _, a in fw.calls('widget_invalidate_force')]
    now.value = 1  # the lyrics
    assert slid() == 0
    after = [a[0] for _, a in fw.calls('widget_invalidate_force')][len(before):]
    assert after == ([now.win, bar] if oled else [])
    now.value = now.pages - 1  # back onto the page: its own frames repaint it
    slid()
    assert [a[0] for _, a in fw.calls('widget_invalidate_force')][len(before) + len(after):] == []


def test_oled_s_field_runs_on_over_the_status_row_which_shows_only_with_the_rest(fw):
    """The status row is painted before Now Playing, outside its window: after the row's own paint, black and Field's
    part there, then the row again at the page's alpha (its own paint inside that one is stock's)."""
    full = Full(fw)
    bar = lambda: paint(fw, full.bar, Canvas(fw))
    full.frame(0, held=0)
    full.fills.clear(), full.dots.clear()
    bar()
    assert full.fills == [(0, 0, 375, 58)] and len(full.dots) == 200  # the clip keeps the bar's part
    assert full.painted == []
    full.frame(200, held=1)
    full.painted.clear(), full.alpha.clear()
    bar()
    assert full.painted == [full.bar] and full.alpha == [255, 255] and full.now.v.in_bar == 0


@pytest.mark.parametrize('name, drawn', [(b'volume_dialog', True), (b'msginfo_dialog', False)])
def test_oled_s_field_goes_on_under_the_wheel_s_volume(fw, name, drawn):
    """A turn on the page opens the volume's band over Now Playing (volume.c): Field goes on under it; any other
    window over the page pauses it, as before."""
    full = Full(fw)
    WM = 0x7000
    dialog = full.now.widget(0, 0, 375, 320)
    full.now.top = dialog
    label = C.create_string_buffer(name)
    fw.mock('window_manager', lambda *_: WM)
    fw.mock('widget_count_children', lambda w, *_: 2 if w == WM else full.now.pages if w == full.now.slide else 0)
    fw.mock('widget_get_child', lambda w, i, *_: (full.now.win, dialog)[i] if w == WM else 0)
    fw.mock('widget_get_prop_str', lambda w, key, default, *_: C.addressof(label) if w == dialog else default)
    full.frame(0, held=1)
    assert bool(full.dots) == drawn
