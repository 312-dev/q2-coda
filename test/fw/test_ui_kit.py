"""patch/ui_kit.c: the design's shared components, starting with its colour tokens in each half."""
import ctypes as C

import pytest

import host
from test_nav_painters import scratch, st  # noqa: F401 (fixtures)

OLED = next(t['index'] for t in host.tools_theme.builtins() if t['id'] == 'oled')


@pytest.mark.parametrize('light', [0, 1])
def test_a_token_is_tools_theme_pys_value_in_the_half_light_mode_picks(fw, st, light):
    def config(path, section, key, out, default, *_):
        value = b'%d' % OLED if C.string_at(key) == b'THEME' else C.string_at(default)
        C.memmove(out, value + b'\0', len(value) + 1)
    fw.mock('toolsReadConfig', config)
    fw.fn('ipod_light', C.c_int)()  # reads the settings, OLED's theme among them
    st('light').value = light
    assert fw.fn('ipod_light', C.c_int)() == light
    tok = fw.fn('tok', C.c_uint, C.c_int)
    assert [tok(i) for i in range(len(host.tools_theme.TOKENS))] == [host.tools_theme.token(n, bool(light))
                                                                     for n in host.tools_theme.TOKENS]


def test_the_flat_selection_bar_is_the_themes_inset_and_radius_in_the_accent(fw, st):
    """list_marks.c sel_bar_flat, every page that paints its own rows (Sleep Timer's switch row, Scan Music Now, the
    slider page's switch row, the EQ's rows, Add Songs): sel_inset in from each side, sel_radius ends, the accent's top
    tone, the canvas's fill put back."""
    from test_nav_painters import OLED as OLED_THEME, THEMES, Canvas, rgba, theme
    theme(fw)
    t = THEMES[OLED_THEME]
    canvas = Canvas(fw)
    fw.fn('sel_bar_flat', None, C.c_void_p, C.c_int, C.c_int, C.c_int, C.c_int)(canvas.addr, 0, 40, 375 - 8, 32)
    (box, ink, radius), = canvas.boxes
    accent = t['schemes'][t['accents'].bit_length() - 1]['tones'][0]
    assert box == (t['sel_inset'], 40, 375 - 8 - 2 * t['sel_inset'], 32) and radius == t['sel_radius']
    assert ink == rgba(accent) and canvas.restored()


@pytest.mark.parametrize('gone, alpha', [(0, 255), (800, 255), (900, 127), (999, 1), (1000, 0), (5000, 0)])
def test_show_then_fade_holds_then_fades_over_its_last_200_ms(fw, gone, alpha):
    """ui_kit.c ui_fade_alpha, the scroll indicator's and the A-Z index's: whole for 800 ms, then down to 0 at 1000."""
    fw.mock('time_now_ms', lambda *_: 10000 + gone)
    assert fw.fn('ui_fade_alpha', C.c_uint, C.c_uint)(10000) == alpha


@pytest.mark.parametrize('lit', [1, 0])
def test_a_later_toast_waits_for_the_loop_pass_and_the_last_wins(fw, lit):
    """feedback.c toast_later (USB's and charging's): nothing shows when asked, the next toast_later_poll shows the last
    one asked for, once, and a dark screen drops it."""
    toasts = []
    fw.mock('navigator_to_with_context', lambda page, info, *_: toasts.append(C.string_at(info + 8).decode()) or 0)
    fw.var('g_backlight_status', C.c_ubyte).value = lit
    later = fw.fn('toast_later', None, C.c_char_p, C.c_char_p)
    later(b'usbtoast_audio', b'USB Audio Ready')
    later(b'usbtoast_unplugged', b'Unplugged')
    assert toasts == []
    poll = fw.fn('toast_later_poll', None)
    poll()
    poll()
    assert toasts == (['Unplugged'] if lit else [])
