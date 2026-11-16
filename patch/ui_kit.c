/* The design's shared components (docs/oled-design.md#what-the-engine-needs): its colour tokens by
 * half, and the primitives every painter draws with. The tokens' values are tools/theme.py's
 * TOKENS, written to tokens.h, which tools/art.py inks its glyphs from too. */
#include "navigation.h"

static const unsigned ui_tokens[TOK_N][2] = UI_TOKENS;

unsigned tok(int t) { return ui_tokens[t][ipod_light() ? 1 : 0]; }

unsigned ui_fade_alpha(unsigned at) {
    unsigned gone = (unsigned)time_now_ms() - at;
    return gone >= UI_SHOW_MS                ? 0
           : gone <= UI_SHOW_MS - UI_FADE_MS ? 255
                                             : 255u * (UI_SHOW_MS - gone) / UI_FADE_MS;
}

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
    unsigned buf[64], n = ui_wide(s, buf, 64);
    ui_line_w(canvas, buf, n, x, w, y, align, face, px, RGBA(rgb));
}
/* s's width, UTF-8, in face at px. */
int ui_measure(void *canvas, const char *s, const char *face, int px) {
    unsigned buf[64], n = ui_wide(s, buf, 64);
    canvas_set_font(canvas, face, (unsigned)px);
    return (int)(canvas_measure_text(canvas, buf, n) + 0.5f);
}

/* s's UTF-8 as characters in out (at most n - 1, then 0); a malformed byte stands for itself. */
unsigned ui_wide(const char *s, unsigned *out, unsigned n) {
    unsigned k = 0;
    const unsigned char *p = (const unsigned char *)s;
    while (p && *p && k + 1 < n) {
        unsigned c = *p++, more = c >= 0xf0 ? 3 : c >= 0xe0 ? 2 : c >= 0xc0 ? 1 : 0;
        if (more) c &= 0x3f >> more;
        for (; more && (*p & 0xc0) == 0x80; --more) c = c << 6 | (*p++ & 0x3f);
        out[k++] = c;
    }
    out[k] = 0;
    return k;
}

int ui_measure_w(void *canvas, const char *face, int px, const unsigned *s, unsigned n) {
    canvas_set_font(canvas, face, (unsigned)px);
    return (int)(canvas_measure_text(canvas, s, n) + 0.5f);
}

/* s in Inter (medium: Inter Medium) at AWTK size px, in rgb (0xRRGGBB; final when raw), its row
 * centred on y, from x (align 2), ending at x (align 3) or centred on x (align 1), at most w wide:
 * an ellipsis ends a longer one. A character Inter lacks takes the theme's fallback face. Returns
 * its width. */
int ui_text(void *canvas, const char *s, int medium, int px, unsigned rgb, int raw, int x, int y,
            int w, int align) {
    unsigned buf[96], n = ui_wide(s, buf, sizeof buf / sizeof *buf);
    if (!n || w <= 0) return 0;
    int fpx = px;
    const char *face = theme_face(buf, (int)n, &fpx);
    if (medium && face && !tk_strcmp(face, "inter")) face = "inter-medium";
    int ink = ui_measure_w(canvas, face, fpx, buf, n);
    while (n > 1 && ink > w)
        buf[--n - 1] = 0x2026, buf[n] = 0, ink = ui_measure_w(canvas, face, fpx, buf, n);
    rect_t r = { align == 3 ? x - ink : align == 1 ? x - ink / 2 : x, y - fpx, ink + 1, 2 * fpx };
    unsigned text = (unsigned)I(P(canvas, CANVAS_LCD), LCD_TEXT_COLOR),
             was = ui_ink_raw((unsigned)raw);
    int align_v = I(canvas, CANVAS_ALIGN_V), align_h = I(canvas, CANVAS_ALIGN_H);
    canvas_set_text_color(canvas, RGBA(rgb));
    I(canvas, CANVAS_ALIGN_V) = 1;
    I(canvas, CANVAS_ALIGN_H) = 2;
    canvas_draw_text_in_rect(canvas, buf, n, &r);
    I(canvas, CANVAS_ALIGN_V) = align_v;
    I(canvas, CANVAS_ALIGN_H) = align_h;
    ui_ink_raw(was);
    stock_canvas_set_text_color(canvas, text);
    return ink;
}

int kv_fit(int span, int label_w, int value_w, int *box) {
    int keep = value_w < KV_VALUE_MIN ? value_w : KV_VALUE_MIN; /* the value's least */
    if (label_w > span - KV_GAP - keep) label_w = span - KV_GAP - keep;
    if (label_w < 0) label_w = 0;
    *box = span - label_w - KV_GAP;
    return label_w;
}

int ui_focus_shown(void) { return !st.touch_mode; }

int ui_button(void *canvas, int cx, int cy, const char *label, int primary, int focused) {
    unsigned raw = ui_ink_raw(1);
    int w = ui_measure(canvas, label, "inter-medium", UI_BTN_PX) + 2 * UI_BTN_PAD;
    rect_t r = { cx - w / 2, cy - UI_BTN_H / 2, w, UI_BTN_H };
    if (focused) { /* the ring: Text Primary, UI_BTN_GAP of the page between it and the button */
        int out = UI_BTN_GAP + UI_BTN_RING;
        rect_t ring = { r.x - out, r.y - out, r.w + 2 * out, r.h + 2 * out },
               gap = { r.x - UI_BTN_GAP, r.y - UI_BTN_GAP, r.w + 2 * UI_BTN_GAP,
                       r.h + 2 * UI_BTN_GAP };
        fill_box(canvas, &ring, RGBA(tok(TOK_PRIMARY)), (unsigned)ring.h / 2);
        fill_box(canvas, &gap, RGBA(tok(TOK_CANVAS)), (unsigned)gap.h / 2);
    }
    fill_box(canvas, &r, RGBA(primary ? scheme()->tones[0] & 0xffffffu : tok(TOK_RAISED_2)),
             UI_BTN_H / 2);
    ui_line(canvas, label, r.x, r.w, cy, 1, "inter-medium", UI_BTN_PX,
            primary ? tok(TOK_ON_ACCENT) : tok(TOK_PRIMARY));
    ui_ink_raw(raw);
    return w;
}
