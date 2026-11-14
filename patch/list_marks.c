/* A theme's own list marks and status glyphs (docs/oled-design.md): the chevron from its own art
 * on every row that opens a page (CHEV_ICON), the scroll indicator that shows while the wheel turns
 * (SB_WHEEL) and the slim battery (BATT_SLIM). Each draws in final colours, its own in each
 * half. */
#include "navigation.h"

/* CHEV_ICON: the image's centre this far left of the right column, as the design places the
 * caret's 15 px box (tools/art.py draws the caret-right glyph in it) */
#define CHEV_SHIFT 5

/* SB_WHEEL: shown while the wheel turns and for WHEEL_SB_MS after its last tick, fading over the
 * last WHEEL_SB_FADE_MS, one repaint a WHEEL_SB_STEP_MS. */
#define WHEEL_SB_MS 1000
#define WHEEL_SB_FADE_MS 200
#define WHEEL_SB_STEP_MS 40

static unsigned sb_at __attribute__((section(".scratch")));    /* the last tick, time_now_ms */
static void *sb_list __attribute__((section(".scratch")));     /* the list it moved */
static void *sb_owner __attribute__((section(".scratch")));    /* ... and its list view */
static unsigned sb_timer __attribute__((section(".scratch"))); /* the fade's next repaint */
static char art_name[48] __attribute__((section(".scratch")));  /* theme_art's answer */

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
    const char *name =
        theme_art(selected ? "chevron_w" : ipod_light() ? "chevron_l" : "chevron_d");
    if (!widget_load_image(w, name, bitmap))
        canvas_draw_icon(canvas, bitmap, right - CHEV_SHIFT, y);
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
    if (theme()->painter[PAINT_SCROLL] != SB_WHEEL) return;
    sb_at = time_now_ms();
    sb_list = list;
    sb_owner = P(list, W_PARENT); /* read now: only compared later, never followed */
    rearm(&sb_timer, sb_due, WHEEL_SB_MS - WHEEL_SB_FADE_MS);
}

/* The indicator's alpha on list now: 255 while the wheel turns, falling to 0 over the fade. */
unsigned wheel_sb_alpha(void *list) {
    unsigned gone = time_now_ms() - sb_at;
    /* the wheel moves a list's scroll view; its bar is the list view's */
    if ((list != sb_list && list != sb_owner) || gone >= WHEEL_SB_MS) return 0;
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
 * the charge a solid fill BATT_SLIM_IN px inside it with BATT_SLIM_FILL_R corners in Text
 * Primary (charging's green, low's red), and the nub, BATT_SLIM_NUB_W x BATT_SLIM_NUB_H, a pixel
 * past the body in BATT_SLIM_DIM. Final colours; the caller restores the fill. */
#define BATT_SLIM_R 4.0f
#define BATT_SLIM_IN 2.0f
#define BATT_SLIM_FILL_R 2.2f
#define BATT_SLIM_NUB_W 1.5f
#define BATT_SLIM_NUB_H 4.4f
#define BATT_SLIM_NUB_R 0.8f
#define BATT_SLIM_DIM 0x6e6e6e
#define BATT_SLIM_LOW 0xff453a /* under 20 %, the only red on any screen (docs/oled-design.md) */
#define BATT_SLIM_LOW_PCT 20
void battery_slim(void *canvas, int x, int y, int bw, int bh, unsigned key) {
    void *vg = canvas_get_vgcanvas(canvas);
    if (!vg) return;
    unsigned pct = key & 2 ? 100 : key >> 3, ink = ipod_light() ? 0x000000 : 0xffffff;
    unsigned level = key & 2 ? BATT_CHARGE_RGB : pct < BATT_SLIM_LOW_PCT ? BATT_SLIM_LOW : ink;
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
}
