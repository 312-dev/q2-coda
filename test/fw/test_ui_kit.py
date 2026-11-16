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


@pytest.mark.parametrize('touch_mode, shown', [(1, 0), (0, 1)])
def test_the_focus_ring_shows_after_the_wheel_and_hides_after_a_tap(fw, st, touch_mode, shown):
    """docs/oled-design.md#focus-ring: one record of the last input, navigation.c's st.touch_mode (a touch sets it,
    the wheel clears it), so a screen reached by a tap opens with no ring and one reached by the wheel and Centre
    opens with it."""
    st('touch_mode').value = touch_mode
    assert fw.fn('ui_focus_shown', C.c_int)() == shown


def test_a_switch_slides_its_knob_over_200_ms_easing_out_and_a_flip_mid_slide_turns_back_from_there(fw, st):
    """list_marks.c switch_mark (docs/oled-design.md List row, the user's 40x24 animated switch, 2026-11-16): a switch
    first seen at a place stands still; seen there again with the other value its knob slides over 200 ms, easing
    out (1 - (1 - t)^3); a flip mid-slide starts from where the knob is. 0 is off, 1000 on."""
    now = [5000]
    fw.mock('time_now_ms', lambda *_: now[0])
    timers = []
    fw.mock('timer_add', lambda fn, ctx, ms, *_: timers.append(ms) or len(timers))
    knob = fw.fn('list_marks__sw_knob', C.c_int, C.c_int, C.c_int, C.c_int)
    assert knob(355, 100, 0) == 0 and knob(355, 140, 1) == 1000 and not timers  # first seen: at rest
    assert knob(355, 100, 1) == 0 and timers == [16]  # flipped: the slide starts, repainting every 16 ms
    now[0] += 100
    assert knob(355, 100, 1) == 875  # half the time, 1 - 0.5^3 of the way
    now[0] += 50  # 150 ms: 985 (984.4, in whole thousandths); flipped back from there
    assert knob(355, 100, 0) == 985
    now[0] += 200
    assert knob(355, 100, 0) == 0 and knob(355, 140, 1) == 1000  # done; the other switch never moved


@pytest.mark.parametrize('labels, widths', [(['Go'], [140]), (['Turn on Wi-Fi now'], [17 * 7 + 48]), (['W' * 60], [327]),
                                            (['Turn on Wi-Fi', 'Try Again'], [131, 131]),
                                            (['W' * 30, 'Go'], [158, 158])])
def test_the_buttons_are_one_capsule_sized_to_its_label_or_an_equal_pair_centred_in_the_box(fw, st, labels, widths):
    """docs/oled-design.md#buttons: one sized to its label with 24 px each side, at least 140, at most the 327 px box;
    a pair equal, sized to the longer with 20 px each side, at least 120, 10 px apart, the two within the box."""
    from test_nav_painters import Canvas
    c = Canvas(fw, char_w=7)
    words = [C.c_char_p(s.encode()) for s in labels]
    arr = (C.c_char_p * len(words))(*words)
    w = C.c_int()
    xs = [fw.fn('ui_cta_x', C.c_int, C.c_void_p, C.c_void_p, C.c_int, C.c_int, C.c_void_p)(
        c.addr, C.addressof(arr), len(labels), i, C.addressof(w)) for i in range(len(labels))]
    assert [w.value] * len(labels) == widths
    total = sum(widths) + 10 * (len(widths) - 1)
    assert xs[0] == 187 - total // 2 and (len(xs) == 1 or xs[1] == xs[0] + widths[0] + 10)


@pytest.mark.parametrize('touch_mode, ringed', [(0, True), (1, False)])
def test_a_selected_button_is_ringed_only_under_the_focus_rule(fw, st, touch_mode, ringed):
    """The ring (2 px, Text Primary, 3 px out) shows on the selected button while the wheel was the last input; after
    a tap the button is drawn alone, and Centre still presses it."""
    from test_nav_painters import Canvas
    st('touch_mode').value = touch_mode
    c = Canvas(fw, char_w=7)
    arr = (C.c_char_p * 1)(b'Scan Music Now')
    fw.fn('ui_cta_paint', None, C.c_void_p, C.c_void_p, C.c_int, C.c_int, C.c_int, C.c_int)(
        c.addr, C.addressof(arr), 1, 100, 0, 0)
    beside = [r for r in c.rects if r[1] < 100 or r[1] >= 138]  # the ring's rows, above and below the capsule
    assert bool(beside) == ringed
