"""patch/ui_kit.c: the design's shared components, starting with its colour tokens in each half."""
import ctypes as C
import json

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
    assert bool(c.frames) == ringed and len(c.boxes) == 1  # the capsule either way


@pytest.mark.parametrize('light', [0, 1])
def test_the_focus_ring_is_two_antialiased_pills_concentric_with_the_capsule(fw, st, light):
    """The user's device (2026-11-16, Car Remote's Pair a Phone at 3x): the ring was aliased, its round ends dotted
    and a flat segment over its top that did not follow the pill. It was drawn a row at a time from tools/art.py's
    corner coverage, which has no table for its radii (24, 23), so it fell back to hard one-pixel steps. It is AWTK's
    antialiased rounded rect, as the capsule's fill is: two one-pixel strokes, each a whole pill (radius half its
    height), CTA_RING_OFF (3) px out and one in from it, in Text Primary, and nothing else drawn outside the capsule."""
    from test_nav_painters import Canvas, theme
    theme(fw)
    st('light').value = light
    st('touch_mode').value = 0
    c = Canvas(fw, char_w=7)
    arr = (C.c_char_p * 1)(b'Pair a Phone')
    fw.fn('ui_cta_paint', None, C.c_void_p, C.c_void_p, C.c_int, C.c_int, C.c_int, C.c_int)(
        c.addr, C.addressof(arr), 1, 100, 0, 0)
    (capsule, _, radius), = c.boxes
    x, y, w, h = capsule
    assert (y, h, radius) == (100, 38, 19)
    ink = 0xff000000 | host.tools_theme.token('primary', bool(light))
    assert c.frames == [((x - 5, y - 5, w + 10, h + 10), ink, 24), ((x - 4, y - 4, w + 8, h + 8), ink, 23)]
    assert not [r for (r, _) in c.rects if r[1] < y or r[1] >= y + h]  # no row or segment of its own


@pytest.mark.parametrize('light', [0, 1])
@pytest.mark.parametrize('primary, pressed, fill', [(1, 0, None), (1, 1, 0x1a4dad), (0, 0, 'grey'), (0, 1, 'down')])
def test_one_button_is_the_empty_states_capsule_in_either_kind(fw, st, light, primary, pressed, fill):
    """ui_kit.c ui_button, Software Update's and the library scan's (docs/oled-design.md#buttons): the empty states'
    capsule, 38 px tall, its label Inter Medium 17 with 24 px each side and at least 140 px, centred on (cx, cy);
    primary the accent with white words, secondary the grey with Text Primary words, and pressed a step darker."""
    from test_nav_painters import OLED as OLED_THEME, THEMES, Canvas, rgba, theme
    theme(fw)
    st('light').value = light
    st('touch_mode').value = 1
    c = Canvas(fw, char_w=7)
    w = fw.fn('ui_button', C.c_int, C.c_void_p, C.c_int, C.c_int, C.c_char_p, C.c_int, C.c_int, C.c_int)(
        c.addr, 187, 100, b'Stop', primary, 0, pressed)
    assert w == 140  # 'Stop' is 28 px + 48: the least width
    t = THEMES[OLED_THEME]
    accent = t['schemes'][t['accents'].bit_length() - 1]['tones'][0]
    grey = {('grey', 0): 0x2c2c2e, ('grey', 1): 0xe5e5ea, ('down', 0): 0x3a3a3c, ('down', 1): 0xd1d1d6}
    want = accent if fill is None else grey.get((fill, light), fill)
    (box, ink, radius), = c.boxes
    assert box == (187 - 70, 100 - 19, 140, 38) and ink == rgba(want) and radius == 19
    (words, _, color), = c.texts
    assert words == 'Stop' and color & 0xffffff == (0xffffff if primary else host.tools_theme.token('primary', bool(light)))


# ---- motion (ui_kit.h, docs/oled-design.md#motion) ----
MO_SPATIAL, MO_DECOR, MO_FEEDBACK = range(3)
EASE_LINEAR, EASE_QUAD_IN, EASE_QUAD_OUT, EASE_CUBIC_IN, EASE_CUBIC_OUT = 0, 1, 2, 4, 5


def reduce_motion(fw, on):
    """config.ini's IPOD REDUCEMOTION, read with the theme's keys as Settings > Display's switch writes it."""
    from test_nav_painters import theme
    return theme(fw, **({'REDUCEMOTION': b'1'} if on else {}))


@pytest.mark.parametrize('on', [0, 1])
def test_reduce_motion_is_ipod_reducemotion_and_off_unless_chosen(fw, on):
    reduce_motion(fw, on)
    assert fw.fn('motion_reduced', C.c_int)() == on


@pytest.mark.parametrize('on', [0, 1])
def test_each_kind_of_motion_runs_or_stops_as_reduce_motion_says(fw, on):
    """MO_SPATIAL (slides, springs, leans) and MO_DECOR (loops that say nothing new) stop under Reduce Motion; MO_FEEDBACK
    (a finger followed, a value, progress) always runs; a spatial motion's fade form is MO_FADE_MS."""
    reduce_motion(fw, on)
    ms = fw.fn('motion_ms', C.c_int, C.c_int, C.c_int)
    assert [ms(220, k) for k in (MO_SPATIAL, MO_DECOR, MO_FEEDBACK)] == ([0, 0, 220] if on else [220, 220, 220])
    assert fw.fn('motion_fade_ms', C.c_int, C.c_int)(220) == (150 if on else 220)


class Motion(C.Structure):
    _fields_ = [('hint', C.c_char_p), ('ms', C.c_ushort), ('easing', C.c_ubyte)]


@pytest.mark.parametrize('on', [0, 1])
def test_the_themes_motion_rows_take_their_reduced_form_under_the_flag(fw, on):
    """motion_row: off, the theme's own rows; on, a window's slide (Page's, whose slide is its windows' asset hint) is
    a 150 ms fade, a swap and an in-place slide are immediate, and the wheel's steps are the scroll itself, kept."""
    reduce_motion(fw, on)
    row = fw.fn('motion_row', C.POINTER(Motion), C.c_int)
    oled, = host.tools_theme.builtins()
    fields = dict(host.tools_theme.motion_fields(oled))
    kinds = ['PAGE', 'PLAYING', 'COVERFLOW', 'INPLACE', 'SWAP', 'STEP', 'STEP_FAST', 'COVERFLOW_STEP',
             'COVERFLOW_STEP_FAST']
    got = [(row(k).contents.hint, row(k).contents.ms) for k in range(len(kinds))]
    def own(k):
        h = fields.get(f'motion[MOTION_{k}].hint')
        return (json.loads(h).encode() if h else None, fields.get(f'motion[MOTION_{k}].ms', 0))
    want = [own(k) for k in kinds]
    if on:
        want[0] = (b'fade(duration=150)', 0)
        want[1:3] = [(b'fade(duration=150)', 0) if h else (h, m) for h, m in want[1:3]]
        want[3:5] = [(None, 0), (None, 0)]
    assert got == want and want[5][1]  # the steps glide in both


@pytest.mark.parametrize('ease, t, at', [(EASE_LINEAR, 500, 500), (EASE_QUAD_IN, 500, 250), (EASE_QUAD_OUT, 500, 750),
                                         (EASE_CUBIC_IN, 500, 125), (EASE_CUBIC_OUT, 500, 875),
                                         (EASE_CUBIC_OUT, -5, 0), (EASE_CUBIC_OUT, 1200, 1000)])
def test_the_curves_are_awtks_easings_in_thousandths(fw, ease, t, at):
    assert fw.fn('motion_ease', C.c_int, C.c_int, C.c_int)(t, ease) == at


class Anim(C.Structure):
    _fields_ = [('at', C.c_uint), ('ms', C.c_ushort), ('ease', C.c_ubyte)]


class Ovm(C.Structure):
    _fields_ = [('a', Anim), ('from_', C.c_short), ('out', C.c_ubyte)]


def clock(fw):
    now = [5000]
    fw.mock('time_now_ms', lambda *_: now[0])
    timers = []
    fw.mock('timer_add', lambda fn, ctx, ms, *_: timers.append(ms) or len(timers))
    return now, timers


@pytest.mark.parametrize('on', [0, 1])
def test_a_run_eases_to_its_end_on_one_shared_timer_that_stops_after_the_last(fw, on):
    """anim_start: one 16 ms timer for every run at once, repainting the top window, gone 16 ms after the last run
    ends; under Reduce Motion a spatial run is at its end at once and starts no timer."""
    reduce_motion(fw, on)
    now, timers = clock(fw)
    invalidated = []
    fw.mock('window_manager_get_top_window', lambda *_: 0x1234)
    fw.mock('widget_invalidate_force', lambda w, r, *_: invalidated.append(w) or 0)
    a, b = Anim(), Anim()
    start = fw.fn('anim_start', None, C.c_void_p, C.c_int, C.c_int, C.c_int)
    at = fw.fn('anim_at', C.c_int, C.c_void_p)
    running = fw.fn('anim_running', C.c_int, C.c_void_p)
    start(C.addressof(a), 200, EASE_CUBIC_OUT, MO_SPATIAL)
    now[0] += 100
    start(C.addressof(b), 200, EASE_LINEAR, MO_SPATIAL)
    if on:
        assert timers == [] and at(C.addressof(a)) == 1000 and not running(C.addressof(b))
        return
    assert timers == [16]  # one timer for both
    assert at(C.addressof(a)) == 875 and at(C.addressof(b)) == 0 and running(C.addressof(a))
    frame = fw.fn('ui_kit__mo_frame', C.c_int, C.c_void_p)
    now[0] += 200  # a done; b at its end
    assert at(C.addressof(a)) == 1000 and not running(C.addressof(a)) and frame(None) == 8  # RET_REPEAT
    now[0] += 16
    assert frame(None) == 7 and invalidated == [0x1234, 0x1234]  # RET_REMOVE: nothing runs
    start(C.addressof(a), 200, EASE_CUBIC_OUT, MO_FEEDBACK)
    assert timers == [16, 16]  # the next run starts it again


@pytest.mark.parametrize('on', [0, 1])
def test_a_switch_flipped_under_reduce_motion_is_there_at_once(fw, on):
    reduce_motion(fw, on)
    now, timers = clock(fw)
    knob = fw.fn('list_marks__sw_knob', C.c_int, C.c_int, C.c_int, C.c_int)
    assert knob(355, 100, 0) == 0
    assert knob(355, 100, 1) == (1000 if on else 0) and timers == ([] if on else [16])


@pytest.mark.parametrize('on', [0, 1])
def test_an_overlay_rises_and_fades_in_then_leaves_from_where_it_is(fw, on):
    """ovm_*: the toast's 200 ms cubic-out rise of 10 px, then an exit begun mid-way going down from that level; under
    Reduce Motion it fades only, over MO_FADE_MS in and MO_OUT_FADE_MS out, nothing moved or scaled."""
    reduce_motion(fw, on)
    now, _ = clock(fw)
    o = Ovm()
    p = C.addressof(o)
    level = fw.fn('ovm_level', C.c_int, C.c_void_p)
    shift = fw.fn('ovm_shift', C.c_int, C.c_void_p, C.c_int)
    scale = fw.fn('ovm_scale', C.c_int, C.c_void_p, C.c_int)
    alpha = fw.fn('ovm_alpha', C.c_int, C.c_void_p, C.c_int)
    shown = fw.fn('ovm_shown', C.c_int, C.c_void_p)
    assert level(p) == 0 and not shown(p)
    fw.fn('ovm_enter', None, C.c_void_p, C.c_int, C.c_int)(p, 200, EASE_CUBIC_OUT)
    assert level(p) == 0 and shift(p, 10) == (0 if on else 10) and scale(p, 1060) == (1000 if on else 1060)
    now[0] += 75 if on else 100
    assert level(p) == 875 and alpha(p, 128) == 112 and shift(p, 10) == (0 if on else 1)
    fw.fn('ovm_exit', None, C.c_void_p, C.c_int, C.c_int)(p, 150, EASE_LINEAR)
    assert o.a.ms == (120 if on else 150) and level(p) == 875 and shown(p)
    now[0] += o.a.ms // 2
    assert level(p) == 438
    now[0] += o.a.ms
    assert level(p) == 0 and not shown(p)
