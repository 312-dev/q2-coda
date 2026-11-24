"""engine.c on its device path: the frame loop's steps, a frame's input, the changed rects, drawing into the screen's
buffer, sounds and saves; then the demo scene on Games' page."""
import ctypes as C
import math

import pytest

from conftest import O
from test_games import PAGE, Canvas, Card, Page, fnv1a, paint, texts

CENTER, PREV, NEXT, PLAY, RETURN, BACK_BTN, FWD_BTN = 218, 172, 173, 171, 170, 222, 223
E_CENTRE, E_PLAY, E_PREV, E_NEXT, E_TOUCH = 1, 2, 4, 8, 16
RAW, STILL = 1, 2
ENGINE = 5  # games.c's screen of the engine's first game
DEMO = ENGINE + 3  # the demo scene's: eng_games[] lists it last, after Bricks, Cabin Solitaire and the Evening Puzzle
REPEAT = 8
SHEET = 0x7300  # Page's first widget: the sheet


class Input(C.Structure):
    _fields_ = [*((k, C.c_int) for k in 'wheel ticks fast'.split()), *((k, C.c_uint) for k in 'held pressed released'.split()),
                ('tx', C.c_int), ('ty', C.c_int)]


class Surface(C.Structure):
    _fields_ = [('px', C.POINTER(C.c_uint)), *((k, C.c_int) for k in 'stride ox oy w h x0 y0 x1 y1'.split()), ('canvas', C.c_void_p)]


INIT, STEP = C.CFUNCTYPE(None, C.c_void_p, C.c_int), C.CFUNCTYPE(C.c_int, C.c_void_p, C.POINTER(Input))
DRAW, PAUSE = C.CFUNCTYPE(None, C.c_void_p, C.POINTER(Surface)), C.CFUNCTYPE(None, C.c_void_p)


class GameDef(C.Structure):
    _fields_ = [('name', C.c_char_p), ('size', C.c_uint), ('flags', C.c_uint), ('init', INIT), ('step', STEP), ('draw', DRAW),
                ('pause', PAUSE)]


class Eng(C.Structure):
    _fields_ = [*((k, C.c_void_p) for k in 'game page sheet held left'.split()), *((k, C.c_uint) for k in 'timer at acc'.split()),
                *((k, C.c_int) for k in 'running quit h first'.split()), *((k, C.c_uint) for k in 'keys pressed released'.split()),
                *((k, C.c_int) for k in 'ticks fast tx ty rects'.split()), ('rect', (C.c_int * 4) * 8), ('steps', C.c_uint),
                ('frames', C.c_uint)]


class Demo(C.Structure):
    _fields_ = [*((k, C.c_int) for k in 'x y vx vy pad h hits fast'.split()), ('magic', C.c_uint), ('best', C.c_uint),
                ('sum', C.c_uint), ('saved', C.c_uint)]


def api(fw):
    p, i, u = C.c_void_p, C.c_int, C.c_uint
    cb = C.CFUNCTYPE(None)
    fns = {'eng_open': (i, C.POINTER(GameDef), p, p, i, cb, cb), 'eng_close': (None,), 'eng_run': (None,), 'eng_hold': (None,),
           'eng_live': (i,), 'eng_key': (i, u, i), 'eng_touch': (i, p, p), 'eng_paint': (None, p), 'eng_count': (i,),
           'eng_dirty': (None, i, i, i, i), 'eng_inset': (i, i), 'eng_dirty_all': (None,), 'eng_quit': (None,), 'eng_awake': (None,), 'eng_sfx': (None, i),
           'eng_load': (i, C.c_char_p, p, u, u), 'eng_save': (i, C.c_char_p, p, u, u), 'eng_sin': (i, i), 'eng_cos': (i, i),
           'eng_atan2': (i, i, i), 'eng_sqrt': (u, C.c_ulonglong), 'engine__eng_frame': (i, p),
           'eng_fill': (None, C.POINTER(Surface), i, i, i, i, u),
           'eng_disc': (None, C.POINTER(Surface), i, i, i, u), 'eng_round': (None, C.POINTER(Surface), i, i, i, i, i, u),
           'eng_text': (i, C.POINTER(Surface), C.c_char_p, i, i, i, u, u, i), 'games_keydown': (i, p, u)}
    return {k.replace('engine__eng_', ''): fw.fn(k, v[0], *v[1:]) for k, v in fns.items()}


class Rig:
    """A game of Python callbacks open on the engine, over Page's clock and timers; its steps' inputs and the rects
    the sheet was told changed are kept."""
    def __init__(self, fw, flags=0, moving=1, files=None, size=16):
        self.fw, self.e, self.page = fw, api(fw), Page(fw, files)
        self.inputs, self.rects, self.events, self.moving, self.on_step = [], [], [], moving, None
        fw.mock('widget_invalidate_force', lambda w, r, *_: self.rects.append((w, tuple((C.c_int * 4).from_address(r)) if r else None)))
        self.awake = []
        fw.mock('reset_poweroptions_timer', lambda *a: self.awake.append(a[:3]))
        self.cb = (INIT(lambda s, h: self.events.append(('init', h))), STEP(self.step), DRAW(self.draw), PAUSE(lambda s: self.events.append('pause')))
        self.game = GameDef(b'Rig', size, flags, *self.cb)
        self.held, self.left = C.CFUNCTYPE(None)(lambda: self.events.append('held')), C.CFUNCTYPE(None)(lambda: self.events.append('left'))
        self.drawn = None
        assert self.e['eng_open'](self.game, PAGE, SHEET, 290, self.held, self.left) == 1

    def step(self, state, inp):
        i = inp.contents
        self.inputs.append((i.wheel, i.ticks, i.fast, i.held, i.pressed, i.released, i.tx, i.ty))
        if self.on_step:
            self.on_step()
        return self.moving

    def draw(self, state, s):
        s = s.contents
        self.drawn = (s.ox, s.oy, s.w, s.h, s.x0, s.y0, s.x1, s.y1)
        self.fw.fn('eng_fill', None, C.POINTER(Surface), *[C.c_int] * 4, C.c_uint)(s, 0, 0, 375, 290, 0x102030)

    @property
    def s(self):
        return Eng.from_address(self.fw.var('engine__en', C.c_void_p).value)

    def frame(self, ms):
        """The frame timer's pass ms after the last: its return and the steps it took."""
        self.page.now += ms
        before = len(self.inputs)
        return self.e['frame'](None), len(self.inputs) - before


def surface(w=40, h=30, clip=None, ox=0, oy=0, back=0xff000000):
    buf = (C.c_uint * (w * h))(*[back] * (w * h))
    x0, y0, x1, y1 = clip or (0, 0, w - ox, h - oy)
    return Surface(C.cast(buf, C.POINTER(C.c_uint)), w, ox, oy, w, h, x0, y0, x1, y1, None), buf


def lit(buf, w, back=0xff000000):
    return {(k % w, k // w): v for k, v in enumerate(buf) if v != back}


def test_a_pass_a_refresh_after_the_last_is_one_step_and_a_late_one_catches_up(fw):
    r = Rig(fw)
    assert r.events == [('init', 290)] and r.page.timers[-1][1] == 4 and r.e['eng_live']() and r.s.running
    assert [r.frame(ms) for ms in (16, 17, 18, 15)] == [(REPEAT, 1)] * 4 and r.s.acc == 0  # the ms clock's 16 and 17 are one
    assert [r.frame(ms)[1] for ms in (33, 34, 50, 200, 5000)] == [2, 2, 3, 4, 4] and r.s.steps == 19
    # passes that painted nothing come sooner: their time adds up to a step
    assert [r.frame(5)[1] for _ in range(7)] == [0, 0, 0, 1, 0, 0, 1] and r.s.acc == 100


def test_a_frames_input_goes_to_its_first_step_and_held_keys_stay(fw):
    r = Rig(fw, flags=RAW)
    assert fw.var('wheel_raw', C.c_int).value == 1
    r.frame(16)
    del r.inputs[:]
    assert r.e['eng_key'](CENTER, 1) == 1 and r.e['eng_key'](CENTER, 1) == 1  # a repeat is no second press
    assert r.e['eng_key'](NEXT, 0) == 1 and r.e['eng_key'](NEXT, 1) == 1  # raw: no ticks
    assert r.e['eng_key'](RETURN, 0) == 0 and r.e['eng_key'](999, 1) == 0
    fw.var('wheel_travel', C.c_int).value = -7  # the wheel's x falls clockwise
    assert r.frame(33) == (REPEAT, 2)
    assert r.inputs == [(7, 0, 0, E_CENTRE, E_CENTRE, 0, 0, 0), (0, 0, 0, E_CENTRE, 0, 0, 0, 0)]
    assert r.awake and fw.var('wheel_travel', C.c_int).value == 0
    r.awake.clear()
    r.e['eng_key'](CENTER, 0)
    r.e['eng_key'](FWD_BTN, 0)  # a release whose press went elsewhere is both
    r.e['eng_key'](BACK_BTN, 1)
    r.e['eng_key'](PLAY, 1)
    r.frame(16)
    assert r.inputs[-1] == (0, 0, 0, E_PREV | E_PLAY, E_NEXT | E_PREV | E_PLAY, E_CENTRE | E_NEXT, 0, 0)
    r.awake.clear()
    r.frame(16)
    assert r.inputs[-1][3:6] == (E_PREV | E_PLAY, 0, 0) and not r.awake  # nothing new: the screen may time out


@pytest.mark.parametrize('speed, steps', [(0, 1), (270, 1), (330, 2), (475, 10), (5000, 10)])
def test_a_tick_is_a_detent_and_as_many_fast_steps_as_the_volume_takes_at_that_speed(fw, speed, steps):
    r = Rig(fw)
    assert fw.var('wheel_raw', C.c_int).value == 0
    fw.var('wheel_speed', C.c_int).value = speed
    for key in (NEXT, NEXT, PREV, NEXT):
        r.e['eng_key'](key, 0)
    r.frame(16)
    assert r.inputs[-1][:3] == (0, 2, 2 * steps)


def test_a_still_game_runs_its_timer_only_after_input_and_while_something_moves(fw):
    r = Rig(fw, flags=STILL, moving=0)
    assert r.e['eng_live']()  # the first frame, to draw
    assert r.frame(4) == (0, 1) and not r.s.timer and not r.e['eng_live']()  # at once: a pass after the key
    timers = len(r.page.timers)
    r.e['eng_key'](NEXT, 0)
    assert r.e['eng_live']() and len(r.page.timers) == timers + 1
    r.e['eng_key'](NEXT, 0)
    assert len(r.page.timers) == timers + 1  # the one timer
    r.moving = 1
    assert r.frame(4) == (REPEAT, 1) and r.inputs[-1][1] == 2
    r.moving = 0
    assert r.frame(16) == (0, 1) and not r.e['eng_live']()


def test_changed_rects_reach_the_sheet_clamped_and_past_eight_join_the_last(fw):
    r = Rig(fw)
    def step():
        r.e['eng_dirty'](-5, -5, 20, 20)
        r.e['eng_dirty'](370, 280, 50, 50)
        r.e['eng_dirty'](400, 0, 5, 5)  # off the sheet
        r.e['eng_dirty'](10, 10, 0, 5)  # empty
    r.on_step = step
    r.frame(16)
    assert r.rects == [(SHEET, (0, 0, 15, 15)), (SHEET, (370, 280, 5, 10))] and r.s.frames == 1 and r.s.rects == 0
    r.rects.clear()
    r.on_step = lambda: [r.e['eng_dirty'](k * 10, k * 10, 5, 5) for k in range(10)] and None
    r.frame(16)
    assert len(r.rects) == 8 and r.rects[7][1] == (70, 70, 25, 25) and r.rects[0][1] == (0, 0, 5, 5)
    r.rects.clear()
    r.on_step = lambda: (r.e['eng_dirty'](1, 1, 2, 2), r.e['eng_dirty_all']()) and None
    r.frame(16)
    assert r.rects == [(SHEET, (0, 0, 375, 290))]
    r.rects.clear()
    r.on_step = None
    r.frame(16)
    assert not r.rects and r.s.frames == 3  # a step that changed nothing paints nothing


def test_the_screen_off_or_another_window_pauses_and_a_quit_leaves(fw):
    r = Rig(fw)
    fw.var('g_backlight_status', C.c_ubyte).value = 0
    assert r.frame(16) == (0, 0) and r.events[-1] == 'held' and not r.s.timer
    fw.var('g_backlight_status', C.c_ubyte).value = 1
    r.e['eng_hold']()
    assert r.events[-1] == 'pause' and not r.s.running and r.e['eng_key'](CENTER, 1) == 0
    r.e['eng_hold']()
    assert r.events.count('pause') == 1  # held already
    r.e['eng_run']()
    r.e['eng_run']()
    assert r.s.running and r.e['eng_live']()
    fw.mock('window_manager_get_top_window', lambda *_: PAGE + 8)
    assert r.frame(16) == (0, 0) and r.events[-1] == 'held'
    fw.mock('window_manager_get_top_window', lambda *_: PAGE)
    r.e['eng_hold'](), r.e['eng_run']()
    calls = []
    def step():
        calls.append(1)
        r.e['eng_quit']()
    r.on_step = step
    assert r.frame(50) == (0, 1) and r.events[-1] == 'left' and len(calls) == 1  # no step after the one that quit
    assert r.e['eng_open'](r.game, PAGE, SHEET, 290, r.held, r.left) == 0  # one game at a time
    r.e['eng_close']()
    assert not fw.var('engine__en', C.c_void_p).value and not r.e['eng_live']()
    r.e['eng_close'](), r.e['eng_run'](), r.e['eng_hold']()
    assert r.e['eng_key'](CENTER, 1) == 0 and r.e['eng_touch'](None, None) == 0
    r.e['eng_paint'](None)


def test_the_glass_hides_the_ends_of_the_rows_in_its_corners(fw):
    r = Rig(fw)  # a 290 px sheet starts at the screen's row 30
    inset = r.e['eng_inset']
    assert [inset(y) for y in (-30, 0, 30, 49, 50, 130, 209, 210, 260, 289)] == [80, 18, 3, 1, 0, 0, 0, 1, 19, 80]
    assert all(inset(y) >= inset(y + 1) for y in range(0, 50)) and all(inset(y) <= inset(y + 1) for y in range(210, 289))


def test_a_game_with_no_init_or_pause_opens_and_holds(fw):
    e, p = api(fw), Page(fw)
    game = GameDef(b'Bare', 8, 0, INIT(), STEP(lambda s, i: 1), DRAW(lambda s, f: None), PAUSE())
    nothing = C.CFUNCTYPE(None)(lambda: None)
    assert e['eng_open'](game, PAGE, SHEET, 290, nothing, nothing) == 1
    e['eng_hold']()
    e['eng_close']()


def event(kind, x, y):
    e = (C.c_int * 16)()
    e[O['EVENT_TYPE'] // 4], e[O['EVENT_X'] // 4], e[O['EVENT_Y'] // 4] = kind, x, y
    return e


def test_a_finger_is_a_key_with_its_place_on_the_sheet(fw):
    r = Rig(fw)
    def local(w, xy, *_):
        (C.c_int * 2).from_address(xy)[1] -= 30  # the sheet starts under the status row
    fw.mock('widget_to_local', local)
    down, move, up = O['EVT_POINTER_DOWN'], O['EVT_POINTER_MOVE_BEFORE'], O['EVT_POINTER_UP_BEFORE']
    r.e['eng_touch'](None, event(move, 5, 5))  # no press of the sheet's
    r.frame(16)
    assert r.inputs[-1][3:] == (0, 0, 0, 0, 0)
    r.e['eng_touch'](None, event(down, 100, 130))
    r.frame(16)
    assert r.inputs[-1][3:] == (E_TOUCH, E_TOUCH, 0, 100, 100)
    r.e['eng_touch'](None, event(move, 140, 90))
    r.frame(16)
    assert r.inputs[-1][3:] == (E_TOUCH, 0, 0, 140, 60)
    r.e['eng_touch'](None, event(up, 141, 91))
    r.frame(16)
    assert r.inputs[-1][3:] == (0, 0, E_TOUCH, 141, 61)


def test_a_sound_is_one_of_the_clickers_by_its_route(fw):
    r = Rig(fw)
    kinds = []
    fw.mock('buzzeer_switch', lambda *_: kinds.append(fw.var('clicker_kind', C.c_int).value))
    for k in range(3):
        r.e['eng_sfx'](k)
    assert kinds == [1, 2, 3] and fw.var('clicker_kind', C.c_int).value == 0
    r.e['eng_awake']()
    assert r.awake == [(1, 1, 1)]


def test_a_save_is_sealed_and_anything_else_reads_as_none(fw):
    r = Rig(fw)
    toasts = []
    fw.mock('navigator_to_with_context', lambda page, info, *_: toasts.append(C.string_at(info + 8)))  # toast's dialog
    buf = (C.c_uint * 4)(0, 7, 9, 0)
    assert r.e['eng_save'](b'rig', buf, 16, 0x31474952) == 1
    saved = r.page.card.files['/mnt/data/game-rig']
    assert saved == bytes(buf) and buf[0] == 0x31474952 and buf[3] == fnv1a(saved[:12]) and '/mnt/data/game-rig.tmp' not in r.page.card.files
    back = (C.c_uint * 4)()
    assert r.e['eng_load'](b'rig', back, 16, 0x31474952) == 1 and list(back) == list(buf)
    assert r.e['eng_load'](b'rig', back, 16, 0x31474953) == 0 and list(back) == [0] * 4  # another game's magic
    r.page.card.files['/mnt/data/game-rig'] = saved[:8] + b'\1' + saved[9:]
    assert r.e['eng_load'](b'rig', back, 16, 0x31474952) == 0 and list(back) == [0] * 4  # a flipped byte
    assert r.e['eng_load'](b'none', back, 16, 0x31474952) == 0
    r.page.card.fail = True
    assert r.e['eng_save'](b'rig', buf, 16, 0x31474952) == 0 and toasts == [b"Couldn't save"]


def test_fill_stays_in_the_rows_and_columns_being_painted(fw):
    e = api(fw)
    s, buf = surface(clip=(10, 5, 20, 15))
    e['eng_fill'](s, 0, 0, 40, 30, 0x123456)
    assert set(lit(buf, 40)) == {(x, y) for x in range(10, 20) for y in range(5, 15)} and buf[5 * 40 + 10] == 0xff123456
    e['eng_fill'](s, 30, 0, 5, 5, 0xffffff)
    assert len(lit(buf, 40)) == 100  # outside: nothing
    s, buf = surface(ox=3, oy=2)  # the sheet's origin on the screen
    e['eng_fill'](s, 0, 0, 1, 1, 0xff0000)
    assert lit(buf, 40) == {(3, 2): 0xffff0000}


def area(buf):
    return sum((v & 255) for v in buf) / 255


@pytest.mark.parametrize('r', [1.5, 3, 6, 9.25, 12])
def test_a_disc_covers_its_area_wherever_its_centre_falls_in_a_pixel(fw, r):
    e = api(fw)
    for cx, cy in ((20, 15), (20.5, 15.5), (20.25, 14.8125), (19.9375, 15.0625)):
        s, buf = surface()
        e['eng_disc'](s, int(cx * 65536), int(cy * 65536), int(r * 65536), 0xffffff)
        assert area(buf) == pytest.approx(math.pi * r * r, rel=.04, abs=1.5)
        for (x, y) in lit(buf, 40):
            assert math.hypot(x + .5 - cx, y + .5 - cy) < r + .75
    a, b = surface(), surface()
    e['eng_disc'](a[0], 20 << 16, 15 << 16, 6 << 16, 0xffffff), e['eng_disc'](b[0], (20 << 16) + 0x4000, 15 << 16, 6 << 16, 0xffffff)
    assert list(a[1]) != list(b[1])  # a quarter pixel on is another picture: the edge moves by parts


def test_a_disc_is_symmetric_and_draws_nothing_empty_or_outside(fw):
    e = api(fw)
    s, buf = surface()
    e['eng_disc'](s, 20 << 16, 15 << 16, 6 << 16, 0x2160d8)
    px = lit(buf, 40)
    assert all(px.get((39 - x, y)) == v and px.get((x, 29 - y)) == v for (x, y), v in px.items())
    assert buf[15 * 40 + 20] == 0xff2160d8 and (14, 15) in px and (13, 15) not in px
    s, buf = surface()
    e['eng_disc'](s, 20 << 16, 15 << 16, 0, 0xffffff), e['eng_disc'](s, 20 << 16, 15 << 16, -5 << 16, 0xffffff)
    e['eng_disc'](s, 200 << 16, 15 << 16, 6 << 16, 0xffffff)
    assert not lit(buf, 40)


@pytest.mark.parametrize('w, h, r', [(20, 10, 0), (20, 10, 3), (20, 10, 5), (20, 10, 50), (9, 9, 4)])
def test_a_rounded_box_has_square_sides_and_round_corners(fw, w, h, r):
    e = api(fw)
    s, buf = surface()
    e['eng_round'](s, 5, 5, w, h, r, 0xffffff)
    r = min(r, w // 2, h // 2)
    assert area(buf) == pytest.approx(w * h - (4 - math.pi) * r * r, rel=.03, abs=1.5)
    px = lit(buf, 40)
    assert all(5 <= x < 5 + w and 5 <= y < 5 + h for x, y in px)
    assert px[(5 + w // 2, 5)] == 0xffffffff and px[(5, 5 + h // 2)] == 0xffffffff
    assert all(px.get((2 * 5 + w - 1 - x, y)) == v for (x, y), v in px.items())
    s, buf = surface(clip=(0, 0, 4, 4))
    e['eng_round'](s, 5, 5, w, h, r, 0xffffff)
    assert not lit(buf, 40)


def test_tables_are_games(fw):
    e = api(fw)
    assert (e['eng_sin'](256), e['eng_cos'](0), e['eng_sin'](768), e['eng_atan2'](5, 5), e['eng_sqrt'](1 << 40)) == \
        (e['eng_cos'](0), e['eng_cos'](0), -e['eng_cos'](0), 128, 1 << 20)


def screen(canvas, w=375, h=320, fmt=None, clip=(0, 0, 375, 320), origin=(0, 30)):
    """Canvas with a screen behind it: stock's lcd_mem, its buffer, the canvas's origin and clip.

    Hold the returned buffer while the firmware paints: lcd_mem keeps only its address."""
    buf = (C.c_uint * (w * h))()
    for off, v in ((O['LCD_W'], w), (O['LCD_H'], h), (O['LCD_LINE_LENGTH'], 4 * w), (O['LCD_FORMAT'], O['LCD_BGRA8888'] if fmt is None else fmt)):
        C.c_int.from_buffer(canvas.lcd, off).value = v
    C.c_void_p.from_buffer(canvas.lcd, O['LCD_OFFLINE_FB']).value = C.addressof(buf)
    C.c_int.from_buffer(canvas.raw, O['CANVAS_X']).value, C.c_int.from_buffer(canvas.raw, O['CANVAS_Y']).value = origin
    canvas.fw.mock('canvas_get_clip_rect', lambda c, r, *_: C.memmove(r, (C.c_int * 4)(*clip), 16) and 0)
    return buf


def test_paint_hands_draw_the_screens_buffer_clipped_to_this_pass(fw):
    r = Rig(fw)
    canvas = Canvas(fw)
    r.e['eng_paint'](canvas.p)  # no buffer behind the canvas
    assert r.drawn is None
    buf = screen(canvas, clip=(100, 20, 50, 40))
    r.e['eng_paint'](canvas.p)
    assert r.drawn == (0, 30, 375, 290, 100, 0, 150, 30)
    px = lit(buf, 375, back=0)
    assert set(px) == {(x, y) for x in range(100, 150) for y in range(30, 60)} and set(px.values()) == {0xff102030}
    buf = screen(canvas, clip=(-10, -10, 1000, 1000))  # held: eng_paint fills it
    r.e['eng_paint'](canvas.p)
    assert r.drawn == (0, 30, 375, 290, 0, 0, 375, 290)
    r.drawn = None
    for other in (dict(fmt=5), dict(clip=(0, 0, 375, 30)), dict(clip=(400, 0, 10, 10))):
        buf = screen(canvas, **other)
        r.e['eng_paint'](canvas.p)
    C.c_int.from_buffer(canvas.lcd, O['LCD_LINE_LENGTH']).value = 4 * 376  # padded rows
    r.e['eng_paint'](canvas.p)
    assert r.drawn is None


# The demo scene, through Games' page.

class DemoPage(Page):
    e: dict
    rects: list
    d: Demo


def demo(fw, files=None):
    p = DemoPage(fw, files, room=290)  # the sheet's height these numbers were measured on
    p.e = api(fw)
    p.rects = []
    fw.mock('widget_invalidate_force', lambda w, r, *_: p.rects.append(tuple((C.c_int * 4).from_address(r)) if r else None))
    p.go(DEMO)
    p.d = Demo.from_address(fw.var('engine__en', C.c_void_p).value + C.sizeof(Eng) + (-C.sizeof(Eng)) % 8)
    return p


def tick(p, n=1, ms=16):
    for _ in range(n):
        p.now += ms
        p.e['frame'](None)


def test_the_demo_is_listed_only_where_a_dev_build_asks(fw):
    e, p = api(fw), Page(fw)
    fw.mock('toolsReadConfig', lambda path, sec, key, out, d, *_: C.memmove(out, b'1\0', 2) and 0)
    assert e['eng_count']() == 3 and p.s.engines == 3 and p.s.rows == 3  # test/fw builds as a release does: no demo


@pytest.mark.parametrize('asked,count', [(b'1', 4), (b'0', 3)])
def test_a_dev_build_lists_the_demo_last_when_asked(fw_dev, asked, count):
    fw_dev.mock('toolsReadConfig', lambda path, sec, key, out, d, *_: C.memmove(out, asked + b'\0', 2) and 0)
    assert api(fw_dev)['eng_count']() == count


def test_the_demo_ball_moves_a_step_a_frame_and_bounces(fw_dev):  # a release build links no demo
    fw = fw_dev
    p = demo(fw)
    d = p.d
    assert p.s.screen == DEMO and p.e['eng_live']() and fw.var('wheel_raw', C.c_int).value == 1
    assert (d.h, d.pad, d.hits, d.vx, d.vy) == (290, 187, 0, int(2.25 * 65536), int(2.5 * 65536))
    x, y = d.x, d.y
    p.rects.clear()
    tick(p)
    assert (d.x, d.y) == (x + d.vx, y + d.vy)
    assert p.rects == [(180, 63, 15, 15), (182, 65, 15, 15)]  # where it was, where it is
    seen = set()
    for _ in range(4000):
        tick(p)
        d.pad = max(52, min(323, (d.x >> 16)))  # the paddle under the ball: every fall is a bounce
        assert 26 * 65536 <= d.x <= 349 * 65536 and 36 * 65536 <= d.y <= (290 - 44 - 6) * 65536
        seen.add((d.vx > 0, d.vy > 0))
    assert seen == {(a, b) for a in (True, False) for b in (True, False)} and d.hits > 20 and d.best == d.hits
    assert abs(d.vx) <= 4 * 65536


def test_the_demo_paddle_follows_the_wheel_the_keys_and_a_finger(fw_dev):  # a release build links no demo
    fw = fw_dev
    p = demo(fw)
    d = p.d
    fw.var('wheel_travel', C.c_int).value = -10
    p.rects.clear()
    tick(p)
    assert d.pad == 207 and (155, 246, 64, 8) in p.rects and (175, 246, 64, 8) in p.rects
    fw.var('wheel_travel', C.c_int).value = 500
    tick(p)
    assert d.pad == 52  # the field's left end
    assert p.e['games_keydown'](PAGE, FWD_BTN) == 1
    tick(p, 3)
    assert d.pad == 64
    p.key(FWD_BTN)
    assert p.e['games_keydown'](PAGE, BACK_BTN) == 1
    tick(p)
    assert d.pad == 60
    p.key(BACK_BTN)
    fw.mock('widget_to_local', lambda *_: 0)
    p.e['eng_touch'](None, event(O['EVT_POINTER_DOWN'], 900, 100))
    tick(p)
    assert d.pad == 323  # the right end
    p.e['eng_touch'](None, event(O['EVT_POINTER_UP_BEFORE'], 900, 100))
    kinds = []
    fw.mock('buzzeer_switch', lambda *_: kinds.append(1))
    p.e['games_keydown'](PAGE, CENTER)
    tick(p)
    assert d.fast == 1 and kinds == [1]
    p.key(CENTER)


def test_the_demo_serves_again_after_a_miss_and_saves_a_best_at_a_pause(fw_dev):  # a release build links no demo
    fw = fw_dev
    p = demo(fw)
    d = p.d
    kinds = []
    fw.mock('buzzeer_switch', lambda *_: kinds.append(fw.var('clicker_kind', C.c_int).value))
    d.pad, d.hits = 52, 3
    d.x, d.y, d.vx, d.vy = 300 << 16, 280 << 16, 0, 5 << 16
    tick(p, 5)
    assert d.hits == 0 and d.y < 100 << 16 and kinds == [3]  # the alert
    d.x, d.y, d.vx, d.vy = 60 << 16, 239 << 16, 0, 2 << 16
    tick(p)
    assert d.hits == 1 and d.vy < 0 and d.vx > 0 and d.best == 1 and kinds == [3, 1] and (0, 0, 375, 28) in p.rects
    assert '/mnt/data/game-demo' not in p.card.files
    p.key(RETURN)  # the menu: a pause
    assert p.s.paused == 2 and not p.e['eng_live']() and fw.var('wheel_raw', C.c_int).value == 0
    saved = p.card.files['/mnt/data/game-demo']
    assert saved[:4] == b'DMO1' and int.from_bytes(saved[4:8], 'little') == 1
    del p.card.files['/mnt/data/game-demo']
    p.key(RETURN)  # Continue
    assert p.s.paused == 0 and p.e['eng_live']()
    p.key(RETURN)
    assert '/mnt/data/game-demo' not in p.card.files  # nothing new to save
    p.close()
    q = demo(fw, {'/mnt/data/game-demo': saved})
    assert q.d.best == 1 and q.d.saved == 1
    q.close()


def test_the_demo_pauses_under_games_popup_and_leaves_by_its_menu_or_its_own_quit(fw_dev):  # a release build links no demo
    fw = fw_dev
    p = demo(fw)
    canvas = Canvas(fw)
    buf = screen(canvas, origin=(0, 30))
    ops = paint(p, canvas)
    assert [t.lower() for t in texts(ops)[:2]] == ['bounces 0', 'best 0'] and buf[(30 + 246) * 375 + 187] == 0xffffffff
    assert buf[(30 + 100) * 375 + 5] == 0xff000000  # black outside the field
    assert p.e['games_keydown'](PAGE, RETURN) == 0  # Return's press is stock's
    fw.var('g_backlight_status', C.c_ubyte).value = 0
    p.rects.clear()
    tick(p)
    fw.var('g_backlight_status', C.c_ubyte).value = 1
    assert p.s.paused == 1 and p.rects == [None] and not p.e['eng_live']()
    assert 'paused' in [t.lower() for t in texts(paint(p, canvas))]
    assert p.e['games_keydown'](PAGE, CENTER) == 0  # paused: stock's click, and the release resumes
    p.rects.clear()
    p.key(FWD_BTN)
    assert p.s.paused == 1 and not p.rects  # a key the pause ignores paints nothing
    p.key(CENTER)
    assert p.s.paused == 0 and p.e['eng_live']() and p.rects == [None]
    p.key(RETURN, NEXT)
    assert p.s.paused == 2 and p.s.choice == 1 and [t.lower() for t in texts(paint(p, canvas))[-3:]] == ['paused', 'continue', 'quit']
    p.key(CENTER)
    p.g.show(None)
    assert p.s.screen == 0 and not fw.var('engine__en', C.c_void_p).value
    p.go(DEMO)
    p.e['games_keydown'](PAGE, PLAY)  # the demo's own way out
    tick(p)
    p.g.show(None)
    assert p.s.screen == 0 and not fw.var('engine__en', C.c_void_p).value
    assert p.e['games_keydown'](PAGE, CENTER) == 0 and p.e['games_keydown'](PAGE + 8, CENTER) == 0  # the chooser's is stock's
    p.go(DEMO)
    p.close()  # the page closing frees the game with the rest
    assert not fw.var('engine__en', C.c_void_p).value and not fw.var('games__g', C.c_void_p).value
    assert p.e['games_keydown'](PAGE, CENTER) == 0


def test_a_game_that_cannot_open_leaves_the_list(fw):
    r = Rig(fw)  # the engine is taken: eng_open fails as it does with no memory
    r.page.go(ENGINE)
    assert r.page.s.screen == 0 and r.s.game == C.addressof(r.game)
    r.e['eng_close']()
