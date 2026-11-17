"""patch/tilt.c, tug to tilt: the selection bar's lean from the wheel's part of a tick (wheel_part), its spring back
once the finger lifts, the clip it widens to paint past its list's edges, its stop at its window's top, and its hold
against a list's end (tilt_end) while the finger turns on past it."""
import ctypes as C

import pytest

from conftest import O

W_Y, W_PARENT, CANVAS_Y = O['W_Y'], O['W_PARENT'], O['CANVAS_Y']
UNITS = 160  # the wheel's travel per tick, whatever Wheel Sensitivity sets: wheel_part is a share of it
LIFT, GROW, SPRING_MS, MARGIN = 3, 35, 220, 12  # tilt.c's TILT_LIFT, TILT_GROW, TILT_SPRING_MS, TILT_MARGIN


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
        self.clip_fn = fw.fn('tilt_clip', C.c_int, C.c_void_p, C.POINTER(C.c_int))

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
    gw, gh = 343 * GROW * e // 256000, 32 * GROW * e // 256000
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
    assert bar.paint() == (16, 40, 343, 32)


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
    assert not bar.clip_fn(C.addressof(bar.canvas), old) and not bar.set_clips  # at rest: untouched
    bar.turn(-.5)
    bar.paint(y=0)
    assert bar.clip_fn(C.addressof(bar.canvas), old) == 1 and list(old) == bar.clip
    box = bar.state.box  # in the window's coordinates, MARGIN round the bar
    top, foot = max(0, 58 + box.y), 58 + box.y + box.h
    assert bar.set_clips == [[0, min(60, top), 375, max(260, foot) - min(60, top)]]


def test_the_bar_never_rises_past_its_windows_top(fw):
    """The first row's bar leaning up grows down instead of being cut under the status row's window."""
    bar = Bar(fw, win_y=58, list_y=0)
    bar.paint(y=0)
    bar.turn(-1 + 1 / UNITS)
    x, y, w, h = bar.paint(y=0)
    assert y == 0 and h > 32


@pytest.mark.parametrize('share', (-1 + 1 / UNITS, -.5, .5, 1 - 1 / UNITS))
def test_a_row_raised_above_its_window_leans_from_where_it_rests(fw, share):
    """Home's Split list starts 18 px above its window (split_art_up), its first row's top in the status row's band,
    which the bar paints. That row's leaning bar moves by its lean (TILT_LIFT and the growth) from where it rests, not
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
