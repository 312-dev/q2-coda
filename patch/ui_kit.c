/* The design's shared components (docs/oled-design.md#what-the-engine-needs): its colour tokens by
 * half, and the primitives every painter draws with. The tokens' values are tools/theme.py's
 * TOKENS, written to tokens.h, which tools/art.py inks its glyphs from too. */
#include "navigation.h"

static const unsigned ui_tokens[TOK_N][2] = UI_TOKENS;

unsigned tok(int t) { return ui_tokens[t][ipod_light() ? 1 : 0]; }

#define UI_LINE 32 /* a line's box, centred on its row */
/* s, n characters, centred on row y in the columns x to x + w, aligned (1 centre, 2 left, 3 right)
 * in face at px, in color (0xAARRGGBB: ui_ink's unless raw). */
void ui_line_w(void *canvas, const unsigned *s, unsigned n, int x, int w, int y, int align,
               const char *face, int px, unsigned color) {
    rect_t r = { x, y - UI_LINE / 2, w, UI_LINE };
    int align_v = I(canvas, CANVAS_ALIGN_V), align_h = I(canvas, CANVAS_ALIGN_H);
    canvas_set_font(canvas, face, (unsigned)px);
    canvas_set_text_color(canvas, color);
    I(canvas, CANVAS_ALIGN_V) = 1;
    I(canvas, CANVAS_ALIGN_H) = align;
    canvas_draw_text_in_rect(canvas, s, n, &r);
    I(canvas, CANVAS_ALIGN_V) = align_v;
    I(canvas, CANVAS_ALIGN_H) = align_h;
}
/* s, UTF-8, as ui_line_w in face at px, in rgb (0xRRGGBB, through ui_ink unless raw). */
void ui_line(void *canvas, const char *s, int x, int w, int y, int align, const char *face, int px,
             unsigned rgb) {
    unsigned buf[64], n = ar_wide(s, buf, 64);
    ui_line_w(canvas, buf, n, x, w, y, align, face, px, RGBA(rgb));
}
/* s's width, UTF-8, in face at px. */
int ui_measure(void *canvas, const char *s, const char *face, int px) {
    unsigned buf[64], n = ar_wide(s, buf, 64);
    canvas_set_font(canvas, face, (unsigned)px);
    return (int)(canvas_measure_text(canvas, buf, n) + 0.5f);
}
