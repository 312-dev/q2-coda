"""patch/tilt.c, tug to tilt: the selection bar's lean from the wheel's part of a tick (wheel_part), its spring back
once the finger lifts, the clip it widens to paint past its list's edges, its stop at its window's top, and its hold
against a list's end (tilt_end) while the finger turns on past it. And the press (docs/internals.md#row-press): the bar
raised at rest, sunk by Centre's press with its blue brightened, sprung back with one overshoot on the release, and
under Reduce Motion the blue alone."""
import ctypes as C

import pytest

from conftest import O

W_Y, W_PARENT, CANVAS_Y = O['W_Y'], O['W_PARENT'], O['CANVAS_Y']
UNITS = 160  # the wheel's travel per tick, whatever Wheel Sensitivity sets: wheel_part is a share of it
LIFT, GROW, SPRING_MS, MARGIN = 3, 35, 220, 12  # tilt.c's TILT_LIFT, TILT_GROW, TILT_SPRING_MS, TILT_MARGIN
FOCUS = 20  # tilt.c's PRESS_FOCUS less 1000: the bar's rest lift, thousandths
REST = (16 - 343 * FOCUS // 1000 // 2, 40, 343 + 343 * FOCUS // 1000, 32)  # a 343 x 32 bar at 16, 40, lifted


class Rect(C.Structure):
    _fields_ = [('x', C.c_int), ('y', C.c_int), ('w', C.c_int), ('h', C.c_int)]


class Tilt(C.Structure):
    """tilt.c's state."""
    _fields_ = [('w', C.c_void_p), ('win', C.c_void_p), ('box', Rect), ('p', C.c_int), ('from_', C.c_int),
                ('at', C.c_uint), ('end_w', C.c_void_p), ('end', C.c_int)]


def ease(a):
    return 256 - (256 - a) * (256 - a) // 256


class Bar:
    """A list in a window at y win_y under the window manager, its bar painted on a canvas at the list's place;
    the wheel's carry and finger, the clock and the canvas's clip as tilt.c reads them."""
    def __init__(self, fw, win_y=58, list_y=0):
        self.fw = fw
        self.wm, self.win, self.list = (C.create_string_buffer(0x100) for _ in range(3))
        C.c_int.from_buffer(self.win, W_Y).value = win_y
        C.c_int.from_buffer(self.list, W_Y).value = list_y
        C.c_void_p.from_buffer(self.win, W_PARENT).value = C.addressof(self.wm)
        C.c_void_p.from_buffer(self.list, W_PARENT).value = C.addressof(self.win)
        self.canvas = C.create_string_buffer(0x100)
        C.c_int.from_buffer(self.canvas, CANVAS_Y).value = win_y + list_y  # the list's origin on screen
        self.now, self.clip, self.set_clips = 1000, [0, 60, 375, 200], []
        fw.mock('window_manager', lambda *_: C.addressof(self.wm))
        fw.mock('window_manager_get_top_window', lambda *_: C.addressof(self.win))
        fw.mock('time_now_ms', lambda *_: self.now)
        fw.mock('canvas_get_clip_rect', lambda canvas, out, *_: C.memmove(out, (C.c_int * 4)(*self.clip), 16) and 0)
        fw.mock('canvas_set_clip_rect', lambda canvas, r, *_: self.set_clips.append(list((C.c_int * 4).from_address(r))))
        self.acc, self.units = fw.var('playback__wh_acc', C.c_int), fw.var('playback__wh_units', C.c_int)
        self.held = fw.var('wheel_held', C.c_int)
        self.units.value = UNITS
        self.state = fw.var('tilt__tilt', Tilt)
        self.bar_fn = fw.fn('tilt_bar', None, C.c_void_p, C.c_void_p, C.POINTER(Rect), C.c_uint, C.c_int)
        self.poll_fn = fw.fn('tilt_poll', None)
        self.end_fn = fw.fn('tilt_end', None, C.c_void_p, C.c_int)
        self.clip_fn = fw.fn('tilt_clip', C.c_int, C.c_void_p, C.POINTER(C.c_int), C.POINTER(C.c_int))

    def paint(self, x=16, y=40, w=343, h=32):
        """tilt_bar on a bar at x, y (the list's coordinates): the box it leaves."""
        r = Rect(x, y, w, h)
        self.bar_fn(C.addressof(self.list), C.addressof(self.canvas), C.byref(r), 0x2060e0, 8)
        return (r.x, r.y, r.w, r.h)

    def turn(self, share):
        """A finger on the wheel with share of a tick turned (+ toward Next), and a UI pass."""
        self.held.value = 1
        self.acc.value = -round(share * UNITS)  # x falls toward Next: the carry is negative that way
        self.poll()

    def lift(self):
        self.held.value = 0
        self.acc.value = 0
        return self.poll()

    def poll(self, after=0):
        self.now += after
        self.poll_fn()
        return self.state.p


def test_a_tug_leans_the_bar_toward_the_next_row_by_its_eased_share(fw):
    bar = Bar(fw)
    rest = bar.paint()
    bar.turn(.5)
    assert bar.state.p == 128
    e = ease(128)
    grow = FOCUS + GROW * e // 256  # the lean grows the lifted bar
    gw, gh = 343 * grow // 1000, 32 * grow // 1000
    assert rest == REST
    assert bar.paint() == (16 - gw // 2, 40 + LIFT * e // 256 - gh // 2, 343 + gw, 32 + gh) != rest
    assert fw.calls('widget_invalidate_force')  # its box painted again as the lean changed


def test_a_tug_back_leans_the_other_way(fw):
    bar = Bar(fw)
    bar.paint()
    bar.turn(-1 + 1 / UNITS)  # all but a unit of a tick toward Previous
    assert bar.state.p < -250
    assert bar.paint()[1] < 40  # up, toward the row above


def test_a_lift_springs_the_bar_back_to_rest(fw):
    bar = Bar(fw)
    bar.paint()
    bar.turn(1 - 1 / UNITS)
    full = bar.state.p
    assert bar.lift() == full  # the spring starts from the lean shown
    assert 0 < bar.poll(SPRING_MS // 2) < full // 4  # cubic: most of the way back by half its time
    assert bar.poll(SPRING_MS // 2) == 0
    assert bar.paint() == REST


def test_a_tick_at_a_lists_end_holds_the_lean_full_while_the_finger_turns_on(fw):
    """At an end the tick moves nothing and leaves only the carry: the lean holds full rather than drop to that and
    build again."""
    bar = Bar(fw)
    bar.paint()
    bar.turn(.9)
    bar.end_fn(C.addressof(bar.list), 1)  # the tick, at the last row
    for share in (.05, 0, .5, .95, .1):
        bar.turn(share)
        assert bar.state.p == 256, share
    bar.turn(-.1)  # turned back: off the end, the lean the wheel's again
    assert bar.state.p == -25
    bar.turn(.3)
    assert bar.state.p == 76  # and the end forgotten


def test_the_end_holds_only_its_own_list_and_finger(fw):
    bar = Bar(fw)
    bar.paint()
    other = C.create_string_buffer(0x100)
    bar.end_fn(C.addressof(other), 1)  # another list's end
    bar.turn(.25)
    assert bar.state.p == 64
    bar.end_fn(C.addressof(bar.list), 1)
    bar.turn(.25)
    assert bar.state.p == 256
    bar.lift()  # a lift ends the hold: the bar springs back
    assert bar.poll(SPRING_MS) == 0
    bar.turn(.25)
    assert bar.state.p == 64


@pytest.mark.parametrize('dir', (1, -1))
def test_the_held_end_leans_toward_its_end(fw, dir):
    bar = Bar(fw)
    bar.paint()
    bar.end_fn(C.addressof(bar.list), dir)
    bar.turn(dir * .01)
    assert bar.state.p == dir * 256


def test_the_clip_widens_to_the_lean_and_only_while_it_leans(fw):
    bar = Bar(fw, win_y=58)
    bar.paint(y=0)  # the first row
    old = (C.c_int * 4)()
    screen = (C.c_int * 4)(0, 0, 375, 320)  # this pass's clip before the list's own
    assert not bar.clip_fn(C.addressof(bar.canvas), old, screen) and not bar.set_clips  # at rest: untouched
    bar.turn(-.5)
    bar.paint(y=0)
    assert bar.clip_fn(C.addressof(bar.canvas), old, screen) == 1 and list(old) == bar.clip
    box = bar.state.box  # in the window's coordinates, MARGIN round the bar
    top, foot = max(0, 58 + box.y), 58 + box.y + box.h
    assert bar.set_clips == [[0, min(60, top), 375, max(260, foot) - min(60, top)]]


@pytest.mark.parametrize('frame', ([0, 200, 375, 40], [0, 100, 375, 4], [0, 50, 375, 20]))
def test_the_widened_clip_stays_inside_this_pass_s_clip(fw, frame):
    """The lean's clip is widened past the list's edges, never past the clip this paint pass had before the list's
    own (frame: its dirty area). A small repaint over the list while the bar leans (the art pane's drift, a clock
    tick) paints only there; widened past it the bar covered the lit row's words, which that pass never paints
    again, so they vanished on every other frame (user, 2026-11-17: the text inside the selected row flickers while
    the wheel is dragged short of a tick)."""
    bar = Bar(fw, win_y=58)
    bar.clip = [0, max(frame[1], 60), 375, 4]  # the list's clip: the pass's, cut to the list
    bar.paint(y=40)
    bar.turn(.5)
    bar.paint(y=40)
    old = (C.c_int * 4)()
    assert bar.clip_fn(C.addressof(bar.canvas), old, (C.c_int * 4)(*frame)) == 1
    x, y, w, h = bar.set_clips[-1]
    assert frame[1] <= y and y + h <= frame[1] + frame[3]


def test_the_bar_never_rises_past_its_windows_top(fw):
    """The first row's bar leaning up grows down instead of being cut at its window's top."""
    bar = Bar(fw, win_y=58, list_y=0)
    bar.paint(y=0)
    bar.turn(-1 + 1 / UNITS)
    x, y, w, h = bar.paint(y=0)
    assert y == 0 and h > 32


@pytest.mark.parametrize('share', (-1 + 1 / UNITS, -.5, .5, 1 - 1 / UNITS))
def test_a_row_raised_above_its_window_leans_from_where_it_rests(fw, share):
    """Home's Split list starts 18 px above its window (split_art_up), its first row's top at the status row's glyphs,
    where the bar paints. That row's leaning bar moves by its lean (TILT_LIFT and the growth) from where it rests, not
    down to the window's top: that put it up to 18 px below its words on every tug, a blue slice lagging off the row
    (user, 2026-11-16, on the Q2: Home's selected text flickering, "a lagging blue slice"). All it paints, its
    shadow too, stays inside the box tilt_poll paints again."""
    bar = Bar(fw, win_y=58, list_y=-18)
    bar.paint(y=0)
    bar.turn(share)
    x, y, w, h = bar.paint(y=0)
    assert -2 <= y <= LIFT and h <= 32 + 2  # its own row's place, give or take the lean
    box = bar.state.box  # the window's coordinates; the bar is the list's, 18 px higher
    shadow_foot = y - 18 + h + 3 * 2 + 3  # TILT_SHADOW rings, each 2 px lower and 1 px taller
    assert box.y <= y - 18 and shadow_foot <= box.y + box.h


@pytest.mark.parametrize('share', (-1 + 1 / UNITS, 1 - 1 / UNITS))
def test_a_full_lean_s_shadow_stays_inside_the_box_painted_again(fw, share):
    """An ordinary row's bar at a full lean: lifted TILT_LIFT px, grown, its outermost shadow ring 2 px lower a ring
    and 1 px taller, ends 45 px below the row's top, a px past MARGIN round the row; the box tilt_poll paints again
    reaches it, else that ring's last line stays on the row below once the bar springs back."""
    bar = Bar(fw, win_y=58)
    bar.paint(y=64)
    bar.turn(share)
    x, y, w, h = bar.paint(y=64)
    box = bar.state.box
    shadow = (x - 3, y + 3 * 2 * ease(255) // 256, w + 6, h + 3)  # tilt.c's outermost ring at this lean
    assert box.x <= shadow[0] and shadow[0] + shadow[2] <= box.x + box.w
    assert box.y <= min(y, shadow[1]) and shadow[1] + shadow[3] <= box.y + box.h


def test_under_reduce_motion_the_bar_steps_without_leaning(fw):
    """A lean is MO_SPATIAL (ui_kit.h): with IPOD REDUCEMOTION on, a tug leaves the bar where it is."""
    from test_nav_painters import theme
    theme(fw, REDUCEMOTION=b'1')
    bar = Bar(fw)
    rest = bar.paint()
    bar.turn(.5)
    assert bar.state.p == 0 and bar.paint() == rest


DOWN, DOWN_MS, SPRING, FADE_MS, RM_HOLD_MS = 965, 90, 280, 120, 80  # tilt.c's PRESS_DOWN and its timings, ms
BLUE = 0xb8141c  # the OLED accent, Arterial (theme.xml oled-arterial), named as the tests were written
PRESSED = sum((((BLUE >> s & 255) * 880 + 255 * 120) // 1000) << s for s in (0, 8, 16))  # tilt.c PRESS_LIGHT_STEP: 120/1000 to white


def cubic_out(t):
    return 1000 - (1000 - t) ** 3 // 1000000


def sized(scale):
    """A 343 x 32 bar at 16, 40 at scale thousandths, as tilt_bar sizes it (C's division: toward 0)."""
    gw, gh = int(343 * (scale - 1000) / 1000), int(32 * (scale - 1000) / 1000)
    return (16 - int(gw / 2), 40 - int(gh / 2), 343 + gw, 32 + gh)


def mixed(a, b, j):
    """theme_engine.c's mix: a to b by j thousandths, per channel."""
    return sum(((a >> s & 255) * (1000 - j) + (b >> s & 255) * j) // 1000 << s for s in (0, 8, 16))


class Press(Bar):
    def __init__(self, fw, **kw):
        super().__init__(fw, **kw)
        self.press_fn = fw.fn('tilt_press', None, C.c_int)
        self.tone_fn = fw.fn('tilt_tone', C.c_uint, C.c_uint)

    def press(self, down=1, after=0):
        self.now += after
        self.press_fn(down)
        self.poll()

    def tone(self):
        return self.tone_fn(BLUE)


def test_centre_down_sinks_the_bar_over_90_ms_cubic_out_and_brightens_its_blue(fw):
    bar = Press(fw)
    assert bar.paint() == REST and bar.tone() == BLUE
    bar.press()
    assert bar.paint() == REST  # its first frame
    half = cubic_out(500)
    bar.poll(DOWN_MS // 2)
    assert bar.paint() == sized(1020 - 55 * half // 1000) and bar.tone() == mixed(BLUE, PRESSED, half)
    bar.poll(DOWN_MS // 2)
    assert bar.paint() == sized(DOWN) == (22, 40, 331, 31) and bar.tone() == PRESSED
    bar.poll(500)  # held: it stays down
    assert bar.paint() == sized(DOWN) and bar.tone() == PRESSED


def test_the_release_springs_back_past_rest_once_and_settles_in_280_ms(fw):
    bar = Press(fw)
    bar.paint()
    bar.press(after=0)
    bar.poll(200)
    bar.press(0)
    widths = []
    for _ in range(SPRING // 4):
        widths.append(bar.paint()[2])
        bar.poll(4)
    rest = REST[2]
    assert widths[0] == 331 and max(widths) == rest + 1  # the overshoot, about +3 thousandths
    over = [w > rest for w in widths]
    runs = sum(1 for a, b in zip([False] + over, over) if b and not a)
    assert runs == 1  # one overshoot, no second
    assert widths.index(max(widths)) * 4 < SPRING // 2 + 40  # it peaks early, as the page starts to open
    assert bar.paint() == REST


def test_the_blue_fades_back_over_120_ms_after_the_release(fw):
    bar = Press(fw)
    bar.paint()
    bar.press()
    bar.poll(DOWN_MS)
    bar.press(0)
    assert bar.tone() == PRESSED
    bar.poll(FADE_MS // 2)
    assert bar.tone() == mixed(BLUE, PRESSED, 500)
    bar.poll(FADE_MS // 2)
    assert bar.tone() == BLUE


def test_a_release_mid_press_springs_from_where_the_bar_got_to(fw):
    bar = Press(fw)
    bar.paint()
    bar.press()
    bar.poll(DOWN_MS // 3)
    got = bar.paint()
    bar.press(0)
    assert bar.paint() == got and REST[2] > got[2] > 331


def test_a_press_wins_over_the_lean(fw):
    bar = Press(fw)
    bar.paint()
    bar.turn(.5)
    assert bar.state.p == 128
    bar.press()
    assert bar.state.p == 0
    bar.turn(.7)  # the finger still turning: no lean while the press runs
    assert bar.state.p == 0 and bar.paint() == REST


def test_the_press_repaints_from_the_ui_loop_not_from_its_paint(fw):
    """tilt_bar paints only; tilt_poll, each UI loop pass, asks for the bar's box again while the press changes
    (a paint that invalidated would schedule its own next paint: the loop ui_kit.h's timer exists to prevent)."""
    bar = Press(fw)
    bar.paint()
    before = len(fw.calls('widget_invalidate_force'))
    bar.paint()
    bar.paint()
    assert len(fw.calls('widget_invalidate_force')) == before
    bar.press_fn(1)
    bar.poll(10)
    asks = fw.calls('widget_invalidate_force')[before:]
    assert asks and all(a[0] == C.addressof(bar.win) and a[1] for _, a in asks)  # the window, with a box
    bar.paint()
    n = len(fw.calls('widget_invalidate_force'))
    bar.poll(0)  # nothing new to show since that paint: no ask
    assert len(fw.calls('widget_invalidate_force')) == n
    assert fw.calls('timer_add')  # the shared Motion timer runs the frames


def test_a_repeat_of_the_press_changes_nothing(fw):
    bar = Press(fw)
    bar.paint()
    bar.press()
    bar.poll(DOWN_MS)
    bar.press()  # Centre's repeat while held
    assert bar.paint() == sized(DOWN)


def test_no_press_on_a_bar_whose_window_is_not_on_top(fw):
    bar = Press(fw)
    bar.paint()
    other = C.create_string_buffer(0x100)
    fw.mock('window_manager_get_top_window', lambda *_: C.addressof(other))
    bar.press()
    bar.now += DOWN_MS
    assert bar.paint() == REST and bar.tone() == BLUE


def test_a_press_whose_window_leaves_the_top_ends_there(fw):
    """A hold that opens a page: AWTK drops its release; the list is not found pressed on the way back."""
    bar = Press(fw)
    bar.paint()
    bar.press()
    bar.poll(DOWN_MS)
    other = C.create_string_buffer(0x100)
    fw.mock('window_manager_get_top_window', lambda *_: C.addressof(other))
    bar.poll(500)
    fw.mock('window_manager_get_top_window', lambda *_: C.addressof(bar.win))
    assert bar.paint() == REST and bar.tone() == BLUE


def test_under_reduce_motion_a_press_only_brightens_the_blue(fw):
    """Reduce Motion: no rest lift, no scale, no shadow; the blue steps up for the press and down 80 ms after the
    release."""
    from test_nav_painters import theme
    theme(fw, REDUCEMOTION=b'1')
    bar = Press(fw)
    flat = (16, 40, 343, 32)
    rings = len(fw.calls('canvas_fill_rounded_rect'))
    assert bar.paint() == flat and len(fw.calls('canvas_fill_rounded_rect')) == rings  # no shadow
    bar.press()
    assert bar.paint() == flat and bar.tone() == PRESSED  # at once
    bar.poll(DOWN_MS)
    assert bar.paint() == flat and len(fw.calls('canvas_fill_rounded_rect')) == rings
    bar.press(0)
    bar.poll(RM_HOLD_MS - 1)
    assert bar.paint() == flat and bar.tone() == PRESSED
    bar.poll(1)
    assert bar.tone() == BLUE


def test_the_rest_lift_casts_a_shadow_that_the_press_pulls_in(fw):
    bar = Press(fw)
    alphas = []
    fw.mock('canvas_fill_rounded_rect',  # the colour is read in the call: it lives on fill_box's stack
            lambda canvas, r, _, color, *__: alphas.append(C.c_uint.from_address(color).value >> 24))

    def rings():
        alphas.clear()
        bar.paint()
        return list(alphas)
    rest = rings()
    assert len(rest) == 3 and all(rest)  # TILT_SHADOW's rings
    bar.press()
    bar.poll(DOWN_MS)
    down = rings()
    assert len(down) == 3 and all(d < r for d, r in zip(down, rest))  # fainter: pulled in to a quarter


def test_a_bar_in_no_window_paints_at_rest(fw):
    """A list not under the window manager (a page being built or torn down) has no window: the rest lift sizes its
    bar without the window's top to keep it under."""
    bar = Press(fw)
    C.c_void_p.from_buffer(bar.list, W_PARENT).value = None
    assert bar.paint() == REST


def test_everything_the_press_draws_stays_inside_the_box_it_repaints(fw):
    """tilt.box (the bar's box and TILT_MARGIN round it) is what tilt_poll invalidates: the bar and its shadow rings,
    at rest, at the bottom of a press, through the spring's overshoot and under a full lean, never paint past it, or
    a move would leave a stale slice of them behind."""
    bar = Press(fw)  # the list at its window's origin: the list's coordinates are the box's
    rings = []
    fw.mock('canvas_fill_rounded_rect', lambda canvas, r, *_: rings.append(tuple((C.c_int * 4).from_address(r))))

    def inside():
        rings.clear()
        drawn = [bar.paint()] + rings
        b = bar.state.box
        return all(b.x <= x and b.y <= y and x + w <= b.x + b.w and y + h <= b.y + b.h for x, y, w, h in drawn), drawn
    assert inside()[0]  # at rest, raised with its shadow
    bar.press()
    bar.poll(DOWN_MS)
    assert inside()[0]  # pressed
    bar.press(0)
    for _ in range(SPRING // 10):  # the spring, its overshoot included
        ok, drawn = inside()
        assert ok, drawn
        bar.poll(10)
    bar.poll(SPRING)
    bar.turn(.99)  # a full lean: the growth, the lift and the deepest shadow
    ok, drawn = inside()
    assert ok, drawn
