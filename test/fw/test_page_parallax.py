"""page_parallax.c: stock's htranslate window animator with the page under drifting 30% of the width and darkening as
the new page slides over it, and a pop 200 ms against a push's 220
(docs/oled-design.md#motion)."""
import ctypes as C
import struct

import pytest

from conftest import O
from test_nav_lists import ui  # noqa: F401  (ui: fixture)

W, H = 375, 320
WA_PREV_WIN, WA_OPEN, WA_PERCENT, WA_CANVAS, WA_TIME = 0x14, 0xac, 0xb4, 0xb8, 0xbc


class Animator:
    """A window_animator_t: the page under (prev_win), its canvas, open or close, percent and time; stock's
    htranslate functions recorded."""
    def __init__(self, fw, ui, open_=1):
        self.fw, self.block = fw, C.create_string_buffer(0x100)
        self.at = C.addressof(self.block)
        self.under = ui.widget('window', ui.wm, (0, 0, W, H))
        self.lcd, self.canvas = C.create_string_buffer(0x200), C.create_string_buffer(0x100)
        struct.pack_into('<Q', self.canvas, O['CANVAS_LCD'], C.addressof(self.lcd))
        struct.pack_into('<Q', self.block, WA_PREV_WIN, self.under.at)
        struct.pack_into('<Q', self.block, WA_CANVAS, C.addressof(self.canvas))
        self.block[WA_OPEN] = open_
        self.calls, self.fills = [], []
        for name in ('htranslate_update', 'htranslate_draw_prev', 'htranslate_draw_curr'):
            fw.mock(name, lambda wa, *_, n=name: self.calls.append((n, self.percent, self.time)) or 0)
        fw.mock('canvas_set_fill_color', lambda c, color, *_: struct.pack_into('<I', self.lcd, O['LCD_FILL_COLOR'], color & 0xffffffff))
        fw.mock('canvas_fill_rect', lambda c, x, y, w, h, *_: self.fills.append(
            (C.c_int(x).value, y, w, h, struct.unpack_from('<I', self.lcd, O['LCD_FILL_COLOR'])[0])))

    percent = property(lambda self: struct.unpack_from('<f', self.block, WA_PERCENT)[0],
                       lambda self, v: struct.pack_into('<f', self.block, WA_PERCENT, v))
    time = property(lambda self: struct.unpack_from('<f', self.block, WA_TIME)[0],
                    lambda self, v: struct.pack_into('<f', self.block, WA_TIME, v))

    def run(self, name):
        return self.fw.fn(name, C.c_int, C.c_void_p)(self.at)


def test_the_page_under_is_drawn_30_percent_of_stocks_way_and_dimmed_where_it_shows(fw, ui):
    a = Animator(fw, ui)
    a.percent = 0.5
    assert a.run('parallax_prev') == 0
    (name, percent, _), = a.calls
    assert name == 'htranslate_draw_prev' and percent == pytest.approx(0.15)  # stock's offset is w * percent
    assert a.percent == 0.5  # put back for the new page's draw
    (x, y, w, h, color), = a.fills
    assert (x, y, w, h) == (0, 0, W - round(W * 0.5), H) and color >> 24 == 32 and color & 0xffffff == 0


@pytest.mark.parametrize('percent, dim', [(0.0, 0), (1.0, 64)])
def test_the_dim_runs_from_none_to_a_quarter(fw, ui, percent, dim):
    a = Animator(fw, ui)
    a.percent = percent
    a.run('parallax_prev')
    assert [f[4] >> 24 for f in a.fills] == ([dim] if dim and percent < 1 else [])
    assert fw.fn('parallax_dim', C.c_uint, C.c_float)(percent) == dim


@pytest.mark.parametrize('open_, time, seen', [(1, 0.5, 0.5), (0, 0.5, 0.55), (0, 0.95, 1.0)])
def test_a_pop_runs_its_time_220_over_200_as_fast_and_a_push_its_own(fw, ui, open_, time, seen):
    a = Animator(fw, ui, open_)
    a.time = time
    a.run('parallax_update')
    assert a.calls[0][2] == pytest.approx(seen) and a.time == pytest.approx(seen)


def test_the_status_row_paints_over_both_pages_after_the_new_one(fw, ui):
    """Every page is the whole screen (docs/oled-design.md#full-bleed), so the snapshots cover the bar stock painted
    first in the frame: parallax_curr paints it again after stock's draw of the new page, this frame's page under and
    percent readable through parallax_slide for that paint alone."""
    a = Animator(fw, ui)
    a.percent = 0.25
    bar = ui.widget('system_bar', ui.wm, (0, 0, W, 58))
    fw.var('fw_ctx_system_bar', C.c_void_p).value = bar.at
    slide = fw.fn('parallax_slide', C.c_void_p, C.POINTER(C.c_float))
    seen = []

    def paint(w, c, *_):
        p = C.c_float(-1)
        seen.append((w, c, slide(C.byref(p)), p.value))
        return 0
    fw.mock('widget_paint', paint)
    assert a.run('parallax_curr') == 0
    assert [c[0] for c in a.calls] == ['htranslate_draw_curr']
    assert seen == [(bar.at, C.addressof(a.canvas), a.under.at, 0.25)]
    assert not slide(C.byref(C.c_float()))  # only while the bar paints
