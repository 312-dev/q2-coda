/* A set_switch theme's slider page (docs/oled-design.md Progress and sliders): stock's four slider
 * windows (Brightness, Max Volume, Channel Balance, Default Volume, whose window crossfade.c also
 * dresses as Crossfade, Wheel Sensitivity, Resume Songs and Night Shift) in one form. Under the
 * status row: the feature's switch as a selected row when the page has one
 * (Default Volume's, Crossfade's, Night Shift's img_bootswitch, its toolbar hidden); the value
 * centred in Inter Medium; a 6 px round track on the text and right columns, Track behind and the
 * accent's fill to a white knob; the range's ends in Text Tertiary under it. Stock's -/+ discs go
 * (the wheel and edit_key still click them) and its value label stays, unseen, so its writes keep
 * repainting where the value is drawn. Everything is laid out as the window loads, before stock's
 * init, and painted by slider_page_paint after the window's children. EQ's Simple tab draws its
 * rows and vertical sliders here too (eq_rows_paint, at the end). */
#include "navigation.h"

/* Screen rows (docs/oled-design.md): less the status row's foot in the view's rows, less the window's
 * screen row (0 on a full-bleed page) in the window's */
#define SP_VALUE_Y 150 /* the value's centre */
#define SP_TRACK_Y 186 /* the track's top */
#define SP_ENDS_Y 214  /* the range ends' centre */
#define SP_TRACK_H 6
#define SP_KNOB 14
#define SP_ROW 32            /* the switch row, from the status row's foot (bar_foot) */
#define SP_VALUE_PX TYPE(22) /* Inter Medium 22, AWTK's size (oled-design.md Type) */
#define SP_ENDS_PX TYPE(15)  /* 15 */
#define SP_LINE 32           /* the value's box, centred on its row */
#define SP_KNOB_EDGE 1 /* Light Mode's Track ring round a knob no other ring has */

/* A white knob centred on x, y of vg, ringed ring_px in ring (0xRRGGBB, the accent's on a focused
 * EQ band); with no ring in Light Mode, where white is the canvas, SP_KNOB_EDGE px of Track, as
 * iOS edges its white thumb. */
static void knob(void *vg, float x, float y, int ring_px, unsigned ring) {
    if (!ring_px && ipod_light()) ring_px = SP_KNOB_EDGE, ring = RGB_TRACK_L;
    if (ring_px) {
        vgcanvas_begin_path(vg);
        vgcanvas_arc(vg, x, y, SP_KNOB / 2.0f + (float)ring_px, 0, 6.2831853f, 0);
        vgcanvas_set_fill_color(vg, RGBA(ring));
        vgcanvas_fill(vg);
    }
    vgcanvas_begin_path(vg);
    vgcanvas_arc(vg, x, y, SP_KNOB / 2.0f, 0, 6.2831853f, 0);
    vgcanvas_set_fill_color(vg, RGBA(0xffffff));
    vgcanvas_fill(vg);
}

static struct {
    void *slider, *value, *nav, *sw;
    unsigned char kind; /* SLIDER_* */
} sp __attribute__((section(".scratch")));
/* the open slider page's window, which ringnav_paint compares before calling slider_page_paint */
void *slider_page_win __attribute__((section(".scratch")));

static int sp_gone(void *win, void *event) {
    (void)event;
    if (win == slider_page_win) slider_page_win = sp.slider = sp.value = sp.nav = sp.sw = (void *)0;
    return 0;
}

/* The row's tap: stock's or crossfade.c's switch handler, through the hidden switch. */
static int sp_row_hit(void *win, void *event) {
    (void)event;
    void *sw = widget_lookup(win, "img_bootswitch", 1);
    char click[0x30];
    if (!sw || !widget_get_visible(sw)) return 0;
    stock_dispatch_trampoline(sw, pointer_event_init(click, EVT_CLICK, sw, 0, 0));
    widget_invalidate_force(win, (void *)0); /* stock may have hidden the slider's view */
    return 0;
}

/* navigation.c slider_open, as stock's window win loads: s its slider, kind its SLIDER_*. */
void slider_page_open(void *win, void *s, unsigned kind) {
    static const char *const values[] = { "label_backlight", "label_vol", "label_balance" },
                             *const discs[] = { "img_dec", "img_add" },
                             *const states[] = { "normal", "pressed", "over", "disable",
                                                 "focused" };
    char key[STYLE_KEY];
    /* top: the view's rows count from the status row's foot; foot: that row in win */
    int top = geom_val(V_BAR), foot = bar_foot(win), view_y = kind == SLIDER_BOOT ? SP_ROW : 0;
    void *view = P(s, W_PARENT), *c;
    bright_bar.easing = 0; /* the page opens on its value */
    slider_page_win = win, sp.slider = s, sp.kind = (unsigned char)kind,
    sp.value = sp.sw = (void *)0;
    widget_on(win, EVT_DESTROY, sp_gone, win);
    widget_move_resize(view, 0, foot + view_y, 375, client_h() - view_y);
    /* the slider's box on the track, a knob's height either side for a finger; stock's own bar
     * drawn in nothing, as slider_page_paint draws it */
    widget_move_resize(s, geom_val(V_TEXT_X), SP_TRACK_Y + SP_TRACK_H / 2 - top - view_y - SP_KNOB,
                       375 - geom_val(V_RIGHT_X) - geom_val(V_TEXT_X), 2 * SP_KNOB);
    for (unsigned i = 0; i < sizeof states / sizeof *states; ++i) {
        widget_set_prop_int(s, style_key(key, states[i], "bg_color"), 0);
        widget_set_prop_int(s, style_key(key, states[i], "fg_color"), 0);
        widget_set_prop_int(s, style_key(key, states[i], "border_color"), 0);
    }
    for (unsigned i = 0; i < sizeof discs / sizeof *discs; ++i)
        if ((c = widget_lookup(win, discs[i], 1))) widget_set_visible(c, 0, 0);
    for (unsigned i = 0; !sp.value && i < sizeof values / sizeof *values; ++i)
        sp.value = widget_lookup(win, values[i], 1);
    if (sp.value) { /* where the value is drawn, so its writes repaint it; its own text clear */
        widget_move_resize(sp.value, 0, SP_VALUE_Y - top - view_y - SP_LINE / 2, 375, SP_LINE);
        text_ink(sp.value, 0, INK_LABEL);
    }
    if (kind != SLIDER_BOOT) return;
    /* the toolbar's title and switch read as a row (stock's init fills the toolbar after this) */
    if ((sp.nav = widget_lookup(win, "view_navbar", 1))) {
        widget_set_visible(sp.nav, 0, 0);
        widget_set_enable(sp.nav, 0);
    }
    c = image_create(win, 0, foot, 375, SP_ROW);
    widget_set_name(c, "switch_hit");
    widget_set_prop_str(c, "clickable", "true");
    widget_on(c, EVT_CLICK, sp_row_hit, win);
}

/* The switch row of page, when it is this page: its hidden switch's image change repaints
 * nothing (crossfade.c's switch). */
void slider_page_dirty(void *page) {
    if (!page || page != slider_page_win) return;
    rect_t row = { 0, bar_foot(page), 375, SP_ROW };
    widget_invalidate_force(page, &row);
}

/* A pill (a rounded rectangle whose radius is half its height) on vg. */
static void pill(void *vg, float x, float y, float w, float h) {
    float r = h / 2;
    vgcanvas_begin_path(vg);
    vgcanvas_arc(vg, x + r, y + r, r, 1.5707963f, 4.7123890f, 0);
    vgcanvas_line_to(vg, x + w - r, y);
    vgcanvas_arc(vg, x + w - r, y + r, r, -1.5707963f, 1.5707963f, 0);
    vgcanvas_close_path(vg);
}

/* s's UTF-8 as characters in out, n - 1 of them at most and a 0 */
static unsigned wide(unsigned *out, unsigned n, const char *s) { return ui_wide(s, out, n); }

/* ringnav_paint, after the window's children: the switch row, the value, the track and the range
 * ends, in the window's coordinates. */
void slider_page_paint(void *w, void *canvas) {
    if (!w || w != slider_page_win || !P(canvas, CANVAS_LCD)) return;
    static const char *const faces[] = FONT_NAMES;
    const scheme_t *S = scheme();
    void *lcd = P(canvas, CANVAS_LCD), *vg = canvas_get_vgcanvas(canvas);
    unsigned fill = (unsigned)I(lcd, LCD_FILL_COLOR), text = (unsigned)I(lcd, LCD_TEXT_COLOR),
             raw = st.raw_ink, buf[48];
    /* top: the window's screen row (screen rows less it are the window's); foot: the status row's */
    int top = I(w, W_Y), foot = bar_foot(w), x0 = geom_val(V_TEXT_X), x1 = 375 - geom_val(V_RIGHT_X), px;
    unsigned accent = S->tones[0] & 0xffffffu;
    void *sw = sp.kind == SLIDER_BOOT ? widget_lookup(w, "img_bootswitch", 1) : (void *)0;
    if (sw && widget_get_visible(sw)) { /* the selected row: its title is the toolbar's */
        void *title = sp.nav && widget_count_children(sp.nav) ? widget_get_child(sp.nav, 0) : 0;
        const unsigned *s = title ? widget_get_text(title) : (void *)0;
        st.raw_ink = 1; /* final: white on the accent in either half */
        sel_bar_flat(canvas, 0, foot, 375, SP_ROW);
        if (s && *s) {
            px = theme()->font_px[ROLE_TITLE];
            const char *face = theme_face(s, -1, &px);
            ui_line_w(canvas, s, wide_len(s), x0, x1 - x0 - 48, foot + SP_ROW / 2, 2,
                      face ? face : faces[0], px, RGBA(0xffffff));
        }
        if (night_page(w)) { /* Night Shift's three choices: a value, white on the bar */
            static const char *const modes[] = { "Off", "On", "Auto" };
            int on = night_on();
            px = theme()->font_px[ROLE_TITLE];
            ui_line_w(canvas, buf,
                      wide(buf, sizeof buf / sizeof *buf, modes[on < 0 || on > 2 ? 0 : on]), x0,
                      x1 - x0, foot + SP_ROW / 2, 3, faces[theme()->font[ROLE_TITLE]], px, RGBA(0xffffff));
        } else
            switch_mark(canvas, x1, foot + SP_ROW / 2,
                        !tk_strcmp(widget_get_prop_str(sw, "image", ""), "switch_on"), 1);
        st.raw_ink = raw;
    }
    /* Night Shift while Off: the value, the track and the knob in Text Tertiary, no accent, as a
     * disabled row (docs/oled-design.md Progress and sliders) */
    int idle = night_page(w) && !night_on();
    void *view = P(sp.slider, W_PARENT);
    if (!widget_get_visible(view)) { /* stock hides it while Default Volume's switch is off */
        stock_canvas_set_fill_color(canvas, fill);
        stock_canvas_set_text_color(canvas, text);
        return;
    }
    /* the value: the label's text after any "Name: " stock writes before it (Max Volume's) */
    const unsigned *v = sp.value ? widget_get_text(sp.value) : (void *)0;
    if (v) {
        const unsigned *p = v;
        while (*p && *p != ':') ++p;
        if (*p) v = p + 1 + (p[1] == ' ');
        ui_line_w(canvas, v, wide_len(v), 0, 375, SP_VALUE_Y - top, 1, faces[FONT_INTER_MEDIUM],
                  SP_VALUE_PX, RGBA(idle ? S->muted : 0xffffff));
    }
    /* the track, its fill to the knob's centre, and the knob, final in each half; Brightness's at
     * its eased level (levels.c bright_wheel), as Control Center's slider */
    int part = slider_part(sp.slider, x1 - x0 - SP_KNOB), y = SP_TRACK_Y - top;
    if (sp.kind == SLIDER_LIGHT) {
        int lo = widget_get_prop_int(sp.slider, "min", 0),
            hi = widget_get_prop_int(sp.slider, "max", 100),
            at = level_shown(&bright_bar, widget_get_prop_int(sp.slider, "value", 0)) - (lo << 8);
        part = hi > lo ? (x1 - x0 - SP_KNOB) * clamp_step(0, (hi - lo) << 8, at) / ((hi - lo) << 8)
                       : 0;
    }
    float kx = (float)(x0 + SP_KNOB / 2 + part);
    if (vg) {
        st.raw_ink = 1;
        vgcanvas_save(vg);
        vgcanvas_translate(vg, (float)I(canvas, CANVAS_X), (float)I(canvas, CANVAS_Y));
        pill(vg, (float)x0, (float)y, (float)(x1 - x0), SP_TRACK_H);
        vgcanvas_set_fill_color(vg, RGBA(ipod_light() ? RGB_TRACK_L : RGB_TRACK));
        vgcanvas_fill(vg);
        /* Channel Balance's fill runs from the centre, where neither side is turned down */
        float from = sp.kind == SLIDER_BALANCE ? (float)(x0 + x1) / 2 : (float)x0,
              a = from < kx ? from : kx, b = from < kx ? kx : from;
        if (b - a >= 1 && !idle) {
            pill(vg, a, (float)y, b - a, SP_TRACK_H);
            vgcanvas_set_fill_color(vg, RGBA(accent));
            vgcanvas_fill(vg);
        }
        if (idle) { /* a knob in Text Tertiary, which needs no Light Mode edge */
            vgcanvas_begin_path(vg);
            vgcanvas_arc(vg, kx, (float)y + SP_TRACK_H / 2.0f, SP_KNOB / 2.0f, 0, 6.2831853f, 0);
            vgcanvas_set_fill_color(vg, RGBA(S->muted));
            vgcanvas_fill(vg);
        } else
            knob(vg, kx, (float)y + SP_TRACK_H / 2.0f, 0, 0);
        vgcanvas_restore(vg);
        st.raw_ink = raw;
    }
    /* the ends: the slider's own range as the page writes its value */
    char lo[16], hi[16];
    int min = widget_get_prop_int(sp.slider, "min", 0),
        max = widget_get_prop_int(sp.slider, "max", 100);
    if (xfade_page(w))
        xfade_ends(lo, hi, sizeof lo);
    else if (sp.kind == SLIDER_BALANCE)
        tk_snprintf(lo, sizeof lo, "L"), tk_snprintf(hi, sizeof hi, "R");
    else if (sp.kind == SLIDER_LIGHT)
        tk_snprintf(lo, sizeof lo, "%d%%", min), tk_snprintf(hi, sizeof hi, "%d%%", max);
    else
        tk_snprintf(lo, sizeof lo, "%d", min), tk_snprintf(hi, sizeof hi, "%d", max);
    px = SP_ENDS_PX;
    const char *face = faces[theme()->font[ROLE_META]];
    ui_line_w(canvas, buf, wide(buf, sizeof buf / sizeof *buf, lo), x0, x1 - x0, SP_ENDS_Y - top, 2,
              face, px, RGBA(S->muted));
    ui_line_w(canvas, buf, wide(buf, sizeof buf / sizeof *buf, hi), x0, x1 - x0, SP_ENDS_Y - top, 3,
              face, px, RGBA(S->muted));
    stock_canvas_set_fill_color(canvas, fill);
    stock_canvas_set_text_color(canvas, text);
}

/* EQ's Simple tab under a set_switch theme (eq_rows, peq_ui.c e3_paint and tabs_paint), in the
 * page's coordinates: under the tabs, the switch (Enabled), Presets and Reset as rows, Presets
 * with the PEQ preset playing as its value, then Low, Mid and High as the standard's vertical
 * sliders (docs/oled-design.md Progress and sliders): a 4 px Track line, the fill from 0 dB to the
 * gain in the accent (Text Tertiary while 3EQ is off), a 14 px white knob ringed in the accent
 * while the wheel is on it (knob), and the band's name and gain under it in 15 px, Text Tertiary,
 * Text Primary on the focused band. */
#define EQ_KNOB_RING 2
#define EQ_LINE 4
int eq_rows(void) { return theme()->set_switch; }
int eq_list_end(void) { return geom_val(V_LIST_END); }

static void eq_ink_text(void *canvas, const char *s, int x, int w, int y, int align, int px,
                        unsigned rgb) {
    static const char *const faces[] = FONT_NAMES;
    unsigned buf[48], n = wide(buf, sizeof buf / sizeof *buf, s);
    int fpx = px;
    const char *face = theme_face(buf, (int)n, &fpx);
    ui_line_w(canvas, buf, n, x, w, y, align, face ? face : faces[0], fpx, RGBA(rgb));
}

static unsigned eq_primary(void) { return tok(TOK_PRIMARY); }

void eq_rows_paint(void *w, void *canvas, const eq_rows_t *e) {
    void *lcd = P(canvas, CANVAS_LCD), *vg = canvas_get_vgcanvas(canvas);
    if (!lcd) return;
    unsigned fill = (unsigned)I(lcd, LCD_FILL_COLOR), text = (unsigned)I(lcd, LCD_TEXT_COLOR),
             raw = st.raw_ink, accent = scheme()->tones[0] & 0xffffffu;
    int x0 = geom_val(V_TEXT_X), x1 = 375 - geom_val(V_RIGHT_X),
        row_px = theme()->font_px[ROLE_TITLE];
    st.raw_ink = 1; /* final colours, each half's */
    static const char *const rows[] = { "Enabled", "Presets", "Reset" };
    for (int i = 0; i < 3; ++i) {
        int y = EQ_ROWS_Y + i * EQ_ROW, cy = y + EQ_ROW / 2, sel = e->focus == 1 + i;
        if (sel) { sel_bar_flat(canvas, 0, y, 375, EQ_ROW); }
        eq_ink_text(canvas, rows[i], x0, x1 - x0 - 48, cy, 2, row_px,
                    sel ? 0xffffff : eq_primary());
        if (!i) switch_mark(canvas, x1, cy, e->on, sel);
        if (i == 1 && e->preset) /* the preset playing, as a settings row's value */
            eq_ink_text(canvas, e->preset, x0, x1 - 1 - VALUE_CHEVRON_GAP - x0, cy, 3, row_px,
                        sel            ? 0xffffff
                        : ipod_light() ? RGB_SECONDARY_L
                                       : RGB_SECONDARY);
        if (i == 1) chevron_icon(w, canvas, x1, cy, sel);
    }
    for (int i = 0; vg && i < 3; ++i) { /* the columns stock's touch reads (peq_ui.c e3_touch) */
        static const char *const names[] = { "Low", "Mid", "High" };
        int x = 62 + 125 * i, focus = e->focus == 4 + i;
        float zero = (float)(EQ_SLIDERS_TOP + EQ_SLIDERS_BOTTOM) / 2,
              y = zero - (float)(e->gain[i] * (EQ_SLIDERS_BOTTOM - EQ_SLIDERS_TOP) / (2 * e->max)),
              a = y < zero ? y : zero, b = y < zero ? zero : y;
        stock_canvas_set_fill_color(canvas, RGBA(ipod_light() ? RGB_TRACK_L : RGB_TRACK));
        canvas_fill_rect(canvas, x - EQ_LINE / 2, EQ_SLIDERS_TOP, EQ_LINE,
                         EQ_SLIDERS_BOTTOM - EQ_SLIDERS_TOP);
        if (b - a >= 1) {
            stock_canvas_set_fill_color(canvas, RGBA(e->on ? accent : scheme()->muted));
            canvas_fill_rect(canvas, x - EQ_LINE / 2, (int)a, EQ_LINE, (int)(b - a + 0.5f));
        }
        vgcanvas_save(vg);
        vgcanvas_translate(vg, (float)I(canvas, CANVAS_X), (float)I(canvas, CANVAS_Y));
        knob(vg, (float)x, y, focus ? EQ_KNOB_RING : 0, accent); /* the accent's ring on focus */
        vgcanvas_restore(vg);
        char gain[16];
        tk_snprintf(gain, sizeof gain, e->gain[i] ? "%+.1f dB" : "0 dB", e->gain[i]);
        unsigned ink = focus ? eq_primary() : scheme()->muted;
        eq_ink_text(canvas, names[i], x - 56, 112, EQ_NAME_Y, 1, SP_ENDS_PX, ink);
        eq_ink_text(canvas, gain, x - 56, 112, EQ_GAIN_Y, 1, SP_ENDS_PX,
                    focus && e->adjusting ? eq_primary() : scheme()->muted);
    }
    st.raw_ink = raw;
    stock_canvas_set_fill_color(canvas, fill);
    stock_canvas_set_text_color(canvas, text);
}

/* An iOS segmented control (docs/oled-design.md#tabs): a track rounded SEG_TRACK_R, w by h from
 * x, y (EQ's SEG_H high), cut into n equal segments, and the open one a segment SEG_IN inside it
 * rounded SEG_R, raised in SEG_RAISED (white in Light Mode), the accent only while focus (the wheel
 * is on the control), so the open segment reads apart from the wheel's place. The labels in Inter
 * Medium 17 px: the open one white on the accent and Text Primary on the raised segment, the rest
 * SEG_INK. EQ's Simple and Advanced and Search's All, Songs, Artists and Albums draw it. */
#define SEG_H 34
#define SEG_IN 2
#define SEG_TRACK_R 9
#define SEG_R 8
#define SEG_PX TYPE(17) /* Inter Medium 17, AWTK's size (oled-design.md Type) */
#define SEG_TRACK RGB_RAISED
#define SEG_RAISED RGB_OFF
#define SEG_INK RGB_SECONDARY
#define SEG_TRACK_LIGHT 0xe5e5ea /* iOS's light track and secondary label */
#define SEG_INK_LIGHT 0x8a8a8e
void segments_paint(void *canvas, const char *const *names, int n, int open, int focus, int x,
                    int y, int w, int h) {
    static const char *const faces[] = FONT_NAMES;
    void *lcd = P(canvas, CANVAS_LCD);
    if (!lcd || n < 1) return;
    unsigned fill = (unsigned)I(lcd, LCD_FILL_COLOR), text = (unsigned)I(lcd, LCD_TEXT_COLOR),
             raw = st.raw_ink;
    int light = ipod_light(), inner = w - 2 * SEG_IN;
    st.raw_ink = 1;
    rect_t track = { x, y, w, h };
    fill_box(canvas, &track, RGBA(light ? SEG_TRACK_LIGHT : SEG_TRACK), SEG_TRACK_R);
    for (int i = 0; i < n; ++i) {
        int sx = x + SEG_IN + i * inner / n, sw = (i + 1) * inner / n - i * inner / n,
            shown = i == open;
        if (shown) {
            rect_t seg = { sx, y + SEG_IN, sw, h - 2 * SEG_IN };
            fill_box(canvas, &seg,
                     RGBA(focus   ? scheme()->tones[0] & 0xffffffu
                          : light ? 0xffffff
                                  : SEG_RAISED),
                     SEG_R);
        }
        unsigned buf[16], len = wide(buf, sizeof buf / sizeof *buf, names[i]);
        ui_line_w(canvas, buf, len, sx, sw, y + h / 2, 1, faces[FONT_INTER_MEDIUM], SEG_PX,
                  RGBA(shown && focus ? 0xffffff
                       : shown        ? eq_primary()
                       : light        ? SEG_INK_LIGHT
                                      : SEG_INK));
    }
    st.raw_ink = raw;
    stock_canvas_set_fill_color(canvas, fill);
    stock_canvas_set_text_color(canvas, text);
}

/* ... and EQ's tabs, Simple and Advanced, across the bar from SEG_X, centred in EQ_TABS_H. */
#define SEG_X 20
void eq_tabs_paint(void *w, void *canvas, int simple, int focus) {
    (void)w;
    static const char *const names[] = { "Simple", "Advanced" };
    segments_paint(canvas, names, 2, !simple, focus, SEG_X, (EQ_TABS_H - SEG_H) / 2,
                   375 - 2 * SEG_X, SEG_H);
}
