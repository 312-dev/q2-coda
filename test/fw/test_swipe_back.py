"""swipe_back.c: a drag right from the screen's left edge takes the page with it and, let go far enough or
flicked, goes on off the right edge and goes back as Return does; let go short, it springs back. A drag that
starts mid-screen, or goes up or down first, is left to the page."""
import ctypes as C
import re
import struct

import pytest

from conftest import O, ROOT
from test_coverflow import themed
from test_nav_lists import STOP, Page, Pull, scratch, st, titles, ui  # noqa: F401  (scratch, st, ui: fixtures)

SB = {k: int(v, 0) for k, v in re.findall(r'^#define (SB_\w+) (0x[0-9a-f]+|\d+)\b', (ROOT / 'patch' / 'swipe_back.c').read_text(), re.M)}
EVT_MOVE, EVT_UP = O['EVT_POINTER_MOVE_BEFORE'], O['EVT_POINTER_UP_BEFORE']
KEY_RETURN, EVT_KEY_DOWN, EVT_KEY_UP = O['KEY_RETURN'], O['EVT_KEY_DOWN'], O['EVT_KEY_UP']
W = 375


class Swipe:
    """A page over another (the list it was opened from), a finger on it (test_nav_lists' Pull), the layer's moves
    and the keys that reach stock's input."""
    def __init__(self, fw, ui, under=True, kind='window', name='albums_page'):
        self.fw, self.ui = fw, ui
        self.t = themed(fw)
        if under:
            self.under = Page(ui, titles(5), name='home_page')
        self.page = Page(ui, titles(20), name=name)
        self.page.win.kind = kind.encode()
        self.page.win.props[b'close_anim_hint'] = b'htranslate(duration=120)'
        self.pull = Pull(fw, ui, self.page)
        self.keys = []
        fw.mock('widget_move', self.move)
        fw.mock('stock_input_trampoline', self.input)

    def move(self, w, x, y, *_):
        self.ui.by[w].int(0, x)

    def input(self, wm, e, *_):
        self.keys.append((C.c_int.from_address(e).value, C.c_int.from_address(e + O['EVENT_KEY']).value,
                          self.page.win.props.get(b'close_anim_hint')))
        return 0

    @property
    def x(self):
        return self.page.win.box[0]

    def poll(self, ms):
        """The UI loop's passes over ms."""
        for _ in range(ms // 8 + 1):
            self.ui.now += 8
            self.fw.fn('swipe_back_poll', None)()

    def drag(self, *xs, gap=16):
        """Moves to each x, gap ms apart; the last move's answer."""
        result = None
        touch = self.fw.fn('ringnav_touch', C.c_int, C.c_void_p, C.c_void_p)
        for x in xs:
            self.ui.now += gap
            touch(None, self.pull.event(EVT_MOVE, x, self.pull.y))  # stock forwards each move to the press's hook too
            result = self.pull.move(x - self.pull.x, 0)
        return result

    def lift(self, x):
        self.ui.now += 16
        return self.pull.up(x - self.pull.x, 0)


def test_the_ease_runs_from_rest_to_the_goal_slowing(fw):
    ease = fw.fn('sb_ease', C.c_int, C.c_int)
    steps = [ease(k) for k in range(0, 257, 32)]
    assert steps[0] == 0 and steps[-1] == 256 and steps == sorted(steps)
    gains = [b - a for a, b in zip(steps, steps[1:])]
    assert gains == sorted(gains, reverse=True)  # fast first, then slower


@pytest.mark.parametrize('off, v, back', [
    (W * SB['SB_DONE_PCT'] // 100 + 1, 0, 1), (W * SB['SB_DONE_PCT'] // 100 - 1, 0, 0),
    (20, SB['SB_FLICK'], 1), (20, SB['SB_FLICK'] - 1, 0), (W - 1, -SB['SB_FLICK'] - 1, 0),
])
def test_let_go_past_40_percent_or_flicked_it_goes_back(fw, off, v, back):
    assert fw.fn('sb_done', C.c_int, C.c_int, C.c_int, C.c_int)(off, W, v) == back


def test_an_edge_swipe_takes_the_page_and_goes_back_as_return(fw, ui):
    s = Swipe(fw, ui)
    s.pull.down(SB['SB_EDGE_PX'] - 1, 150)
    assert s.drag(s.pull.x + SB['SB_CLAIM_PX'] // 2) == 0 and not s.pull.aborts  # the page's, so far
    assert s.drag(s.pull.x + SB['SB_CLAIM_PX'] + 2) == STOP
    assert s.pull.aborts == [(s.page.win.at, O['EVT_POINTER_ABORT'])]  # the rows it pressed let go
    assert s.drag(100, 200) == STOP and s.x == 200 - s.pull.x  # the page follows the finger
    assert s.lift(200) == STOP and not s.keys
    s.poll(SB['SB_GLIDE_MS'] // 2)
    assert 200 - s.pull.x < s.x < W and not s.keys  # on its way out
    s.poll(SB['SB_GLIDE_MS'])
    # Return, its press and release, with the page's own close slide off; then the hint and place are back
    assert s.keys == [(EVT_KEY_DOWN, KEY_RETURN, b''), (EVT_KEY_UP, KEY_RETURN, b'')]
    assert s.page.win.props[b'close_anim_hint'] == b'htranslate(duration=120)' and s.x == 0


def test_a_short_slow_swipe_springs_back(fw, ui):
    s = Swipe(fw, ui)
    s.pull.down(5, 150)
    s.drag(30, 60, 90)
    assert s.x == 85
    s.ui.now += 400  # held still: no speed at the lift
    assert s.lift(90) == STOP
    s.poll(SB['SB_GLIDE_MS'] // 2)
    assert 0 < s.x < 85
    s.poll(SB['SB_GLIDE_MS'])
    assert s.x == 0 and not s.keys


def test_a_short_flick_goes_back(fw, ui):
    s = Swipe(fw, ui)
    s.pull.down(5, 150)
    s.drag(30, 60, gap=20)  # 30 px in 20 ms: 1500 px a second
    s.lift(60)
    s.poll(2 * SB['SB_GLIDE_MS'])
    assert [k[:2] for k in s.keys] == [(EVT_KEY_DOWN, KEY_RETURN), (EVT_KEY_UP, KEY_RETURN)]


@pytest.mark.parametrize('dx, dy', [(2, SB['SB_CLAIM_PX']), (8, -12), (-SB['SB_CLAIM_PX'], 0)])
def test_a_drag_up_down_or_left_from_the_edge_is_the_pages(fw, ui, dx, dy):
    s = Swipe(fw, ui)
    s.pull.down(5, 150)
    assert s.pull.move(dx, dy) == 0
    assert s.pull.move(200, dy) == 0 and s.pull.up(200, dy) == 0  # the list scrolls; nothing is taken after
    s.poll(400)
    assert not s.pull.aborts and s.x == 0 and not s.keys


@pytest.mark.parametrize('case', ['mid screen', 'home', 'dialog', 'animating'])
def test_no_swipe_mid_screen_on_homes_list_over_a_dialog_or_while_a_window_slides(fw, ui, case):
    """Mid-screen a sideways drag stays the page's own (Now Playing's pages, Cover Flow, a slider)."""
    s = Swipe(fw, ui, under=case != 'home', kind='dialog' if case == 'dialog' else 'window')
    ui.animating = case == 'animating'
    s.pull.down(SB['SB_EDGE_PX'] if case == 'mid screen' else 5, 150)
    assert s.drag(100, 200) in (0, None) and s.lift(200) in (0, None)
    s.poll(400)
    assert not s.pull.aborts and s.x == 0 and not s.keys


def test_an_abort_mid_swipe_springs_back(fw, ui):
    s = Swipe(fw, ui)
    s.pull.down(5, 150)
    s.drag(60, 120)
    assert s.pull.send(O['EVT_POINTER_ABORT']) == 0
    s.poll(2 * SB['SB_GLIDE_MS'])
    assert s.x == 0 and not s.keys


class Under:
    """The page under a swipe: stock's offline canvas and bitmap calls recorded, the swiped window's paints."""
    def __init__(self, fw):
        self.fw, self.painted, self.made, self.gone, self.images, self.fills = fw, [], [], [], [], []
        self.lcd, self.canvas = C.create_string_buffer(0x200), C.create_string_buffer(0x100)
        self.fb = (C.c_uint * (W * 320))()
        struct.pack_into('<Q', self.canvas, O['CANVAS_LCD'], C.addressof(self.lcd))
        struct.pack_into('<Q', self.lcd, O['LCD_OFFLINE_FB'], C.addressof(self.fb))
        for k, v in (('LCD_W', W), ('LCD_H', 320), ('LCD_LINE_LENGTH', 4 * W), ('LCD_FORMAT', O['LCD_BGRA8888'])):
            struct.pack_into('<i', self.lcd, O[k], v)
        self.bitmap, self.px = C.create_string_buffer(0x40), (C.c_uint * (W * 320))()
        fw.mock('canvas_offline_create', lambda *_: C.addressof(self.canvas))
        fw.mock('widget_paint', lambda w, c, *_: self.painted.append(w))
        fw.mock('bitmap_create_ex', lambda *a: self.made.append(a[:4]) or C.addressof(self.bitmap))
        fw.mock('bitmap_lock_buffer_for_write', lambda *_: C.addressof(self.px))
        fw.mock('bitmap_destroy', lambda b, *_: self.gone.append(b))
        fw.mock('canvas_draw_image', lambda c, b, src, dst, *_: self.images.append(
            (struct.unpack('<4i', C.string_at(src, 16)), struct.unpack('<4i', C.string_at(dst, 16)))))
        fw.mock('canvas_fill_rect', lambda c, x, y, w, h, *_: self.fills.append((C.c_int(x).value, C.c_int(y).value, w, h)))

    def paint(self, w):
        self.images.clear(), self.fills.clear()
        self.fw.fn('swipe_back_paint', None, C.c_void_p, C.c_void_p)(w, C.addressof(self.canvas))


@pytest.mark.parametrize('reduced', [0, 1])
def test_the_page_under_shows_left_of_the_page_drifting_in_and_lightening(fw, ui, reduced):
    """docs/oled-design.md#gestures: at the claim the window under is painted once, as stock's animator snapshots it;
    the top window's background then draws it in the strip it uncovers, 30% of the width short of its place and
    darkened at the start, both to nothing as the page leaves. Reduce Motion keeps it still and undimmed."""
    from test_ui_kit import reduce_motion
    s = Swipe(fw, ui)
    reduce_motion(fw, reduced)
    under = Under(fw)
    s.pull.down(5, 150)
    s.drag(30)
    assert under.painted == [s.under.win.at] and under.made == [(W, 320, 4 * W, O['LCD_BGRA8888'])]
    s.drag(30 + 100)
    off = s.x
    s.page.win.int(4, 40)  # a page below the status row: its canvas starts 40 px down the screen
    under.paint(s.page.win.at)
    (src, dst), = under.images
    shift = 0 if reduced else round(W * (1 - off / W) * 0.3)
    assert dst == (-off, -40, off, 320) and src == (shift, 0, off, 320)
    assert under.fills == ([] if reduced else [(-off, -40, off, 320)])
    under.paint(s.under.win.at)  # any other window: nothing
    assert under.images == []
    s.lift(30 + 100)
    s.ui.now += 400
    s.poll(2 * SB['SB_GLIDE_MS'])
    assert under.gone == [C.addressof(under.bitmap)]  # the snapshot let go once the swipe ends


def test_an_edge_swipe_keeps_the_rows_bar_and_a_touch_on_the_page_hides_it(fw, ui, st):
    """Seen on the emulator 2026-11-21 with the panel's own events (also on the base): the selected row's accent bar
    went as the finger came down at the edge, stayed gone while the page slid and after it sprang back. A touch hides
    the bar (st.touch_mode, docs/internals.md#touch-mode) because it is a touch on the page's rows; an edge press
    that becomes a swipe back is none, so the bar rides with the page. The same press dragged up or down, or let go
    where it came down, is the page's after all, and hides it."""
    touch = st('touch_mode')
    s = Swipe(fw, ui)
    touch.value = 0
    s.pull.down(5, 150)
    assert touch.value == 0  # a candidate: not yet a touch on the rows
    s.drag(30, 90)
    assert touch.value == 0 and s.x == 85  # claimed, the page follows; the bar with it
    s.ui.now += 400
    s.lift(90)
    s.poll(2 * SB['SB_GLIDE_MS'])
    assert touch.value == 0 and s.x == 0  # sprung back, the row still lit

    for dx, dy in ((2, SB['SB_CLAIM_PX']), (0, 0)):  # dragged down; a tap at the edge
        touch.value = 0
        s.pull.down(5, 150)
        if dy:
            s.pull.move(dx, dy)
        else:
            s.pull.up(0, 0)
        assert touch.value == 1, (dx, dy)
        s.pull.up(dx, dy)

    touch.value = 0
    s.pull.down(SB['SB_EDGE_PX'] + 40, 150)  # mid screen: a touch on the rows at once, as before
    assert touch.value == 1
