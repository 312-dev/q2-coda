/* Page push and pop with parallax (docs/oled-design.md#motion), as UINavigationController's: the
 * new page slides in from the right while the page under drifts PX_UNDER_PCT of the width left and
 * dims to PX_DIM_ALPHA; a pop is the reverse, PAGE_POP_MS against the push's PAGE_SLIDE_MS. The
 * status row is the system bar's own window, painted over the slide in place, so it stays put.
 *
 * Stock's htranslate window animator (the hint every sliding page carries, tools/ipod.py SLIDE)
 * draws both pages from the snapshots it took at the start: the page under (prev_img) moved the
 * whole way with the new one, the new one (curr_img) after it. tools/build.py HTRANSLATE_SLOTS
 * points its vtable's update and draw_prev slots at these (HTRANSLATE_VT), and each runs stock's
 * own with what it changes:
 * - parallax_update: a close's time runs PAGE_SLIDE_MS / PAGE_POP_MS as fast, so the animator
 *   ends sooner (its end is the time's, read after this);
 * - parallax_prev: the page under drawn at PX_UNDER_PCT of stock's offset, then darkened where the
 *   new page does not yet cover it, in proportion to how far the new one is in;
 * - parallax_curr: the new page as stock draws it, then the system bar again over both. Every page
 *   is the whole screen (docs/oled-design.md#full-bleed), so the snapshots cover the bar stock
 *   painted first in the frame (window_animator_begin_frame); this paint, with this frame's
 *   percent readable (parallax_slide), keeps the status row on top.
 * Under Reduce Motion the hint is a fade (ui_kit.c motion_row), so none of this runs. Every window
 * that slides takes it: pages, Now Playing's close, Cover Flow.
 */
#include "navigation.h"

#define PX_UNDER_PCT 30 /* the page under travels this share of the width */
#define PX_DIM_ALPHA 64 /* ... and darkens to this, of 255, under a page fully in */
#define PAGE_POP_MS 200 /* a pop, against a push's PAGE_SLIDE_MS */
/* window_animator_t (stock's, AWTK's): duration @0, prev_win @0x14, curr_win @0x18, prev_img
 * @0x1c, curr_img @0x64, open @0xac, percent @0xb4 (float, eased), canvas @0xb8, time_percent
 * @0xbc (float, 0..1, the end at 1) */
#define WA_PREV_WIN 0x14
#define WA_OPEN 0xac
#define WA_PERCENT 0xb4
#define WA_CANVAS 0xb8
#define WA_TIME 0xbc

#define F(p, off) (*(float *)((char *)(p) + (off)))

/* The page under the slide and how far the new page is in, while parallax_curr paints the bar */
static struct {
    void *under;
    float percent;
    int painting;
} px __attribute__((section(".scratch")));

/* The page under the window sliding now, and in *percent how far the sliding one is in (0..1),
 * while parallax_curr paints the system bar over the frame it just drew; else 0. Stock paints
 * the bar first in each frame (window_animator_begin_frame), before update_percent, so a paint
 * there would see the frame before. */
void *parallax_slide(float *percent) {
    if (!px.painting) return (void *)0;
    *percent = px.percent;
    return px.under;
}

/* The page under's offset from its place, px left, with the new page percent (0..1) in. */
int parallax_under_x(int w, float percent) {
    return (int)(w * percent * PX_UNDER_PCT / 100 + 0.5f);
}

/* The page under's darkening (alpha of 255) with the new page percent (0..1) in. */
unsigned parallax_dim(float percent) { return (unsigned)(PX_DIM_ALPHA * percent + 0.5f); }

int parallax_update(void *wa) {
    if (!*((unsigned char *)wa + WA_OPEN)) {
        float t = F(wa, WA_TIME) * PAGE_SLIDE_MS / PAGE_POP_MS;
        F(wa, WA_TIME) = t > 1 ? 1 : t;
    }
    return htranslate_update(wa);
}

int parallax_prev(void *wa) {
    float percent = F(wa, WA_PERCENT);
    void *win = P(wa, WA_PREV_WIN), *c = P(wa, WA_CANVAS);
    F(wa, WA_PERCENT) = percent * PX_UNDER_PCT / 100;
    int r = htranslate_draw_prev(wa);
    F(wa, WA_PERCENT) = percent;
    void *lcd = c ? P(c, CANVAS_LCD) : (void *)0;
    int w = win ? I(win, W_W) : 0, open = w - (int)(w * percent + 0.5f);
    unsigned a = parallax_dim(percent);
    if (r || !lcd) return r;
    if (open > 0 && a) {
        unsigned fill = (unsigned)I(lcd, LCD_FILL_COLOR);
        stock_canvas_set_fill_color(c, a << 24); /* black in both halves, as iOS dims */
        canvas_fill_rect(c, 0, I(win, W_Y), open, I(win, W_H));
        stock_canvas_set_fill_color(c, fill);
    }
    return r;
}

int parallax_curr(void *wa) {
    int r = htranslate_draw_curr(wa);
    void *c = P(wa, WA_CANVAS), *bar = *(void *const *)system_bar;
    if (r || !c || !P(c, CANVAS_LCD) || !bar) return r;
    px.under = P(wa, WA_PREV_WIN), px.percent = F(wa, WA_PERCENT), px.painting = 1;
    widget_paint(bar, c);
    px.painting = 0;
    return r;
}
