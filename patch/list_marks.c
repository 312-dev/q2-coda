/* A theme's own list marks and status glyphs (docs/oled-design.md): the chevron from its own art
 * on every row that opens a page (chevron_icon, spinner_icon), the scroll indicator that shows
 * while the wheel turns (wheel_sb_alpha), the slim battery (battery_slim), a switch (switch_mark)
 * and a scrolling label's faded ends (marquee_fade). Each draws in final colours, its own in each
 * half. */
#include "navigation.h"

/* CHEV_ICON: the image's centre this far left of the right column, as the design places the
 * caret's 15 px box (tools/art.py draws the caret-right glyph in it) */
#define CHEV_SHIFT 5
#define SPIN_BOX 16 /* the spinner's 15 px frame (tools/art.py spinner), whole */

/* SB_WHEEL: shown while the wheel turns and for WHEEL_SB_MS after its last tick, fading over the
 * last WHEEL_SB_FADE_MS, one repaint a WHEEL_SB_STEP_MS. */
#define WHEEL_SB_MS 1000
#define WHEEL_SB_FADE_MS 200
#define WHEEL_SB_STEP_MS 40

static unsigned sb_at __attribute__((section(".scratch")));    /* the last tick, time_now_ms */
static void *sb_list __attribute__((section(".scratch")));     /* the list it moved */
static void *sb_owner __attribute__((section(".scratch")));    /* ... and its list view */
static unsigned sb_timer __attribute__((section(".scratch"))); /* the fade's next repaint */
static char art_name[48] __attribute__((section(".scratch"))); /* theme_art's answer */

/* The active theme's own image name: its art prefix (tools/art.py) then name. The answer lasts
 * until the next call. */
const char *theme_art(const char *name) {
    tk_snprintf(art_name, sizeof art_name, "%s%s", theme()->art, name);
    return art_name;
}

/* The `>` of a row whose centre row is y, its glyph on the set's right column, whose screen x the
 * canvas reaches at right (canvas columns): white on the selection, else the half's grey. */
void chevron_icon(void *w, void *canvas, int right, int y, int selected) {
    unsigned bitmap[64]; /* bitmap_t */
    const char *name = theme_art(selected ? "chevron_w" : ipod_light() ? "chevron_l" : "chevron_d");
    if (!widget_load_image(w, name, bitmap))
        canvas_draw_icon(canvas, bitmap, right - CHEV_SHIFT, y);
}

/* ... or, on the selected row while Cover Flow opens, the spinner's frame (1-8, coverflow.c
 * coverflow_spinner) in its place, white (tools/art.py spinner). */
static struct {
    void *w;  /* the list it was last painted on, 0 once it is done */
    int x, y; /* its centre there */
} spin __attribute__((section(".scratch")));
void spinner_icon(void *w, void *canvas, int right, int y, int frame) {
    unsigned bitmap[64]; /* bitmap_t */
    char name[8];
    tk_snprintf(name, sizeof name, "spin_%d", frame - 1);
    spin.w = w, spin.x = right - CHEV_SHIFT, spin.y = y;
    if (!widget_load_image(w, theme_art(name), bitmap))
        canvas_draw_icon(canvas, bitmap, spin.x, spin.y);
}
/* The spinner's next frame: its box on its list repainted, or win's whole until it has painted. */
void spinner_dirty(void *win) {
    rect_t r = { spin.x - SPIN_BOX / 2, spin.y - SPIN_BOX / 2, SPIN_BOX, SPIN_BOX };
    widget_invalidate_force(spin.w ? spin.w : win, spin.w ? &r : (void *)0);
}
void spinner_done(void) { spin.w = 0; }

/* ... and a disabled row's (stock's list_intodown, docs/oled-design.md Chevrons and disabled rows),
 * in the half's Text Tertiary, selected or not: the wheel never selects such a row. */
void chevron_muted(void *w, void *canvas, int right, int y) {
    unsigned bitmap[64]; /* bitmap_t */
    if (!widget_load_image(w, theme_art(ipod_light() ? "chevron_tl" : "chevron_td"), bitmap))
        canvas_draw_icon(canvas, bitmap, right - CHEV_SHIFT, y);
}

/* A scrolling text's ends (list.marquee.fade, docs/oled-design.md#motion): over an hscroll_label
 * whose text is wider than its column, once stock has painted it, MARQUEE_FADE_PX columns at the
 * right end while the text goes on past it, and at the left while it is scrolled off its start,
 * each from clear at the inside to the colour behind it at the end: the selection's on the
 * selected row's marquee (marq_label), else the page's paper. A label at rest on stock's ellipsis
 * (stopped, ellipses set) keeps it, so only a scrolling text pays: a fill a column at each end it
 * fades. The column is the one stock clips the text to: inside the label's margin_left and
 * margin_right (2 px unless its style says, as a folder row's says past its icon). */
#define MARQUEE_FADE_PX 12
void marquee_fade(void *w, void *canvas) {
    void *lcd = w && canvas ? P(canvas, CANVAS_LCD) : (void *)0;
    if (!lcd || tk_strcmp(widget_get_type(w), "hscroll_label")) return;
    int loop = np_marq_owns(w); /* Now Playing's lines: their copy follows, so the right fades on */
    if (loop) np_marq_kick(w);
    void *style = P(w, W_ASTYLE);
    int x0 = style_get_int(style, "margin_left", 2),
        x1 = I(w, W_W) - style_get_int(style, "margin_right", 2), h = I(w, W_H),
        off = I(w, HSCROLL_XOFFSET), over = I(w, HSCROLL_TEXT_W) - (x1 - x0);
    if (over <= 0 || (!loop && B(w, HSCROLL_ELLIPSES) && !I(w, HSCROLL_TIMER))) return;
    unsigned saved = (unsigned)I(lcd, LCD_FILL_COLOR),
             behind = (w == st.marq_label ? RGBA(scheme()->tones[0]) : ui_ink(RGBA(0))) & 0xffffffu;
    int left = off > 0, right = loop || off < over;
    for (int i = 0; i < MARQUEE_FADE_PX && (left || right); ++i) {
        unsigned a = 255u * (unsigned)(MARQUEE_FADE_PX - i) / MARQUEE_FADE_PX;
        stock_canvas_set_fill_color(canvas, behind | a << 24);
        if (left) canvas_fill_rect(canvas, x0 + i, 0, 1, h);
        if (right) canvas_fill_rect(canvas, x1 - 1 - i, 0, 1, h);
    }
    stock_canvas_set_fill_color(canvas, saved);
}

/* The indicator's column on the top window, repainted as it fades. */
static int sb_due(const void *info) {
    (void)info;
    sb_timer = 0;
    unsigned gone = time_now_ms() - sb_at;
    const theme_t *T = theme();
    void *top = window_manager_get_top_window(window_manager());
    if (top) {
        rect_t r = { 375 - T->sb_right - T->sb_w - 1, 0, T->sb_w + 2, I(top, W_H) };
        widget_invalidate_force(top, &r);
    }
    if (gone < WHEEL_SB_MS) sb_timer = timer_add(sb_due, (void *)0, WHEEL_SB_STEP_MS);
    return 0;
}

/* A wheel tick moved list: the indicator shows on it, and its fade starts over. */
void wheel_sb_arm(void *list) {
    sb_at = time_now_ms();
    sb_list = list;
    sb_owner = P(list, W_PARENT); /* read now: only compared later, never followed */
    rearm(&sb_timer, sb_due, WHEEL_SB_MS - WHEEL_SB_FADE_MS);
}

/* The indicator's alpha on list now: 255 while the wheel turns, falling to 0 over the fade. */
unsigned wheel_sb_alpha(void *list) {
    unsigned gone = time_now_ms() - sb_at;
    /* the wheel moves a list's scroll view; its bar is the list view's */
    /* the A-Z index takes the edge while it shows (az_index.c) */
    if ((list != sb_list && list != sb_owner) || gone >= WHEEL_SB_MS || az_on(list)) return 0;
    if (gone <= WHEEL_SB_MS - WHEEL_SB_FADE_MS) return 255;
    return 255u * (WHEEL_SB_MS - gone) / WHEEL_SB_FADE_MS;
}

/* A rounded rectangle's path on vg. */
static void round_path(void *vg, float x, float y, float w, float h, float r) {
    vgcanvas_begin_path(vg);
    vgcanvas_move_to(vg, x + r, y);
    vgcanvas_line_to(vg, x + w - r, y);
    vgcanvas_arc(vg, x + w - r, y + r, r, -1.5707963f, 0, 0);
    vgcanvas_line_to(vg, x + w, y + h - r);
    vgcanvas_arc(vg, x + w - r, y + h - r, r, 0, 1.5707963f, 0);
    vgcanvas_line_to(vg, x + r, y + h);
    vgcanvas_arc(vg, x + r, y + h - r, r, 1.5707963f, 3.1415927f, 0);
    vgcanvas_line_to(vg, x, y + r);
    vgcanvas_arc(vg, x + r, y + r, r, 3.1415927f, 4.7123890f, 0);
    vgcanvas_close_path(vg);
}

/* BATT_SLIM: the body bw x bh at x, y, BATT_SLIM_R corners and a 1 px outline in BATT_SLIM_DIM,
 * the charge a solid fill BATT_SLIM_IN px inside it with BATT_SLIM_FILL_R corners to the level, in
 * Text Primary (red under BATT_LOW_PCT; green on a cable that charges it or holds it full, or
 * at Charge Limit; orange on one too weak), and the nub, BATT_SLIM_NUB_W x BATT_SLIM_NUB_H, a pixel
 * past the body in BATT_SLIM_DIM. Charging, Phosphor's lightning (fill, 7 px, Text Primary) sits on
 * the body's centre with a 1 px Canvas gap cut round it where it crosses the fill; too weak, its
 * warning in the same place (docs/oled-design.md#status-row, tools/art.py BATT_GLYPHS: the gap is
 * the image's own, BATT_GLYPH_BOX px with the glyph's centre BATT_GLYPH_X, BATT_GLYPH_Y in from its
 * corner). Final colours; the caller restores the fill. */
#define BATT_SLIM_R 4.0f
#define BATT_SLIM_IN 2.0f
#define BATT_SLIM_FILL_R 2.2f
#define BATT_SLIM_NUB_W 1.5f
#define BATT_SLIM_NUB_H 4.4f
#define BATT_SLIM_NUB_R 0.8f
#define BATT_SLIM_DIM 0x6e6e6e
#define BATT_SLIM_LOW 0xff453a /* under 20 %, the only red on any screen (docs/oled-design.md) */
#define BATT_GLYPH_X 4.5f /* tools/art.py BATT_GLYPH_BOX (9 x 10) and the glyph's centre in it */
#define BATT_GLYPH_Y 5.0f
/* The charge fill's share of the body, in percent, for battery_now's key: the level, on the cable
 * as off it. */
static unsigned battery_fill(unsigned key) { return BATT_KEY_LEVEL(key); }
/* The fill's colour for key, ink being Text Primary's. */
static unsigned battery_tone(unsigned key, unsigned ink) {
    return key & BATT_KEY_WEAK                              ? BATT_WEAK_RGB
           : key & (BATT_KEY_CHARGING | BATT_KEY_PLUGGED)   ? BATT_CHARGE_RGB
           : BATT_KEY_LEVEL(key) < BATT_LOW_PCT        ? BATT_SLIM_LOW
                                                            : ink;
}
/* The glyph inside the body for key, a theme image's name, or 0 for none. */
static const char *battery_glyph(unsigned key) {
    int light = ipod_light();
    if (key & BATT_KEY_WEAK) return theme_art(light ? "battwarn_l" : "battwarn_d");
    if (key & BATT_KEY_CHARGING) return theme_art(light ? "battbolt_l" : "battbolt_d");
    return (const char *)0;
}
void battery_slim(void *w, void *canvas, int x, int y, int bw, int bh, unsigned key) {
    void *vg = canvas_get_vgcanvas(canvas);
    if (!vg) return;
    unsigned pct = battery_fill(key), level = battery_tone(key, ipod_light() ? 0x000000 : 0xffffff);
    float fx = (float)x + BATT_SLIM_IN, fy = (float)y + BATT_SLIM_IN,
          fw = ((float)bw - 2 * BATT_SLIM_IN) * (float)pct / 100, fh = (float)bh - 2 * BATT_SLIM_IN;
    unsigned raw = st.raw_ink;
    st.raw_ink = 1; /* final: Light Mode's flip leaves them */
    vgcanvas_save(vg);
    vgcanvas_translate(vg, (float)I(canvas, CANVAS_X), (float)I(canvas, CANVAS_Y));
    round_path(vg, (float)x + 0.5f, (float)y + 0.5f, (float)bw - 1, (float)bh - 1, BATT_SLIM_R);
    vgcanvas_set_line_width(vg, 1);
    vgcanvas_set_stroke_color(vg, RGBA(BATT_SLIM_DIM));
    vgcanvas_stroke(vg);
    if (fw > 0) {
        round_path(vg, fx, fy, fw, fh, fw < 2 * BATT_SLIM_FILL_R ? fw / 2 : BATT_SLIM_FILL_R);
        vgcanvas_set_fill_color(vg, RGBA(level));
        vgcanvas_fill(vg);
    }
    round_path(vg, (float)(x + bw + 1), (float)y + ((float)bh - BATT_SLIM_NUB_H) / 2,
               BATT_SLIM_NUB_W, BATT_SLIM_NUB_H, BATT_SLIM_NUB_R);
    vgcanvas_set_fill_color(vg, RGBA(BATT_SLIM_DIM));
    vgcanvas_fill(vg);
    vgcanvas_restore(vg);
    st.raw_ink = raw;
    const char *glyph = battery_glyph(key);
    if (glyph && w) /* whole pixels: the image holds the glyph's half-pixel place */
        paint_art(w, canvas, glyph, x + (int)((float)bw / 2 - BATT_GLYPH_X + 0.5f),
                  y + (int)((float)bh / 2 - BATT_GLYPH_Y + 0.5f));
}

/* settings.values.switch: an on/off setting's switch (docs/oled-design.md List row), SWITCH_W x
 * SWITCH_H, fully round, ending on screen column right (canvas columns) and centred on row y, its
 * SWITCH_KNOB px knob SWITCH_KNOB_IN px in. Off the selection: the accent's track when on, Track
 * (each half's) when off, a white knob. On the selection, whose bar is the accent: a white track
 * with an accent knob when on, a SWITCH_RING px white outline round a white knob when off. Final
 * colours. */
#define SWITCH_W 34.0f
#define SWITCH_H 20.0f
#define SWITCH_KNOB 16.0f
#define SWITCH_KNOB_IN 2.0f
#define SWITCH_RING 1.5f
#define SWITCH_TRACK_DARK 0x333333
#define SWITCH_TRACK_LIGHT 0xd9d9d9
void switch_mark(void *canvas, int right, int y, int on, int selected) {
    void *vg = canvas ? canvas_get_vgcanvas(canvas) : (void *)0;
    if (!vg) return;
    unsigned accent = scheme()->tones[0] & 0xffffffu,
             track = selected       ? 0xffffff
                     : on           ? accent
                     : ipod_light() ? SWITCH_TRACK_LIGHT
                                    : SWITCH_TRACK_DARK,
             knob = selected && on ? accent : 0xffffff;
    float x = (float)right - SWITCH_W, top = (float)y - SWITCH_H / 2,
          kx = on ? x + SWITCH_W - SWITCH_KNOB_IN - SWITCH_KNOB : x + SWITCH_KNOB_IN;
    unsigned raw = st.raw_ink;
    st.raw_ink = 1; /* final: Light Mode's flip leaves them */
    vgcanvas_save(vg);
    vgcanvas_translate(vg, (float)I(canvas, CANVAS_X), (float)I(canvas, CANVAS_Y));
    if (selected && !on) { /* the outline's centre line SWITCH_RING / 2 inside the track's edge */
        round_path(vg, x + SWITCH_RING / 2, top + SWITCH_RING / 2, SWITCH_W - SWITCH_RING,
                   SWITCH_H - SWITCH_RING, (SWITCH_H - SWITCH_RING) / 2);
        vgcanvas_set_line_width(vg, SWITCH_RING);
        vgcanvas_set_stroke_color(vg, RGBA(track));
        vgcanvas_stroke(vg);
    } else {
        round_path(vg, x, top, SWITCH_W, SWITCH_H, SWITCH_H / 2);
        vgcanvas_set_fill_color(vg, RGBA(track));
        vgcanvas_fill(vg);
    }
    round_path(vg, kx, top + SWITCH_KNOB_IN, SWITCH_KNOB, SWITCH_KNOB, SWITCH_KNOB / 2);
    vgcanvas_set_fill_color(vg, RGBA(knob));
    vgcanvas_fill(vg);
    vgcanvas_restore(vg);
    st.raw_ink = raw;
}
