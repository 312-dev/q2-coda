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

/* Set text (ui_set): each face's advances in its font units (tools/build.py face_advances), its
 * line, which AWTK sizes it by, and its em. */
static const unsigned short ui_adv[FONT_N][FONT_ADV_LAST - FONT_ADV_FIRST + 1] = FONT_ADV;
static const unsigned short ui_line_units[FONT_N] = FONT_LINE, ui_upm[FONT_N] = FONT_UPM;

/* s's n characters set in face at AWTK size px with track thousandths of an em between them: where
 * each starts, pen[k] in 1/64 px from the first's, and pen[n] the whole width. A character the
 * table lacks (or a face it has no row for) takes its width from the canvas, a whole pixel. */
static void ui_set_pens(void *canvas, const unsigned *s, unsigned n, const char *face, int px,
                        int track, int *pen) {
    int fi = !tk_strcmp(face, "inter-medium") ? FONT_INTER_MEDIUM
             : !tk_strcmp(face, "inter")      ? FONT_INTER
                                              : -1;
    if (fi >= 0 && !ui_line_units[fi]) fi = -1;
    int line = fi >= 0 ? ui_line_units[fi] : 121, upm = fi >= 0 ? ui_upm[fi] : 100, at = 0,
        gap = track * px * upm * 64 / (1000 * line);
    canvas_set_font(canvas, face, (unsigned)px);
    for (unsigned k = 0; k < n; ++k) {
        unsigned c = s[k], u = fi >= 0 && c >= FONT_ADV_FIRST && c <= FONT_ADV_LAST
                                   ? ui_adv[fi][c - FONT_ADV_FIRST]
                                   : 0;
        pen[k] = at;
        at += u ? (int)((u * (unsigned)px * 64 + (unsigned)line / 2) / (unsigned)line)
                : (int)(canvas_measure_text(canvas, s + k, 1) * 64 + 0.5f);
        if (k + 1 < n) at += gap;
    }
    pen[n] = at;
}

#define UI_SET_MAX 48 /* a set label's characters; past them it is cut */
int ui_set_w(void *canvas, const char *s, int medium, int px, int track) {
    unsigned buf[UI_SET_MAX + 1], n = ui_wide(s, buf, UI_SET_MAX + 1);
    if (!n) return 0;
    const char *face = theme_face(buf, (int)n, &px);
    if (medium && face && !tk_strcmp(face, "inter")) face = "inter-medium";
    int pen[UI_SET_MAX + 1];
    ui_set_pens(canvas, buf, n, face, px, track, pen);
    return (pen[n] + 32) >> 6;
}
int ui_set(void *canvas, const char *s, int medium, int px, unsigned rgb, int raw, int x, int y,
           int track) {
    unsigned buf[UI_SET_MAX + 1], n = ui_wide(s, buf, UI_SET_MAX + 1);
    if (!n) return 0;
    int fpx = px, pen[UI_SET_MAX + 1];
    const char *face = theme_face(buf, (int)n, &fpx);
    if (medium && face && !tk_strcmp(face, "inter")) face = "inter-medium";
    ui_set_pens(canvas, buf, n, face, fpx, track, pen);
    unsigned text = (unsigned)I(P(canvas, CANVAS_LCD), LCD_TEXT_COLOR),
             was = ui_ink_raw((unsigned)raw);
    int align_v = I(canvas, CANVAS_ALIGN_V), align_h = I(canvas, CANVAS_ALIGN_H);
    canvas_set_text_color(canvas, RGBA(rgb));
    I(canvas, CANVAS_ALIGN_V) = 1;
    I(canvas, CANVAS_ALIGN_H) = 2;
    for (unsigned k = 0; k < n; ++k) {
        if (buf[k] == ' ') continue;
        /* one glyph from its own pen, rounded: no glyph's error carries to the next */
        rect_t r = { x + ((pen[k] + 32) >> 6), y - fpx, 2 * fpx, 2 * fpx };
        canvas_draw_text_in_rect(canvas, buf + k, 1, &r);
    }
    I(canvas, CANVAS_ALIGN_V) = align_v;
    I(canvas, CANVAS_ALIGN_H) = align_h;
    ui_ink_raw(was);
    stock_canvas_set_text_color(canvas, text);
    return (pen[n] + 32) >> 6;
}

int ui_set_in(void *canvas, const char *s, int medium, int px, unsigned rgb, int x, int w, int y,
              int off, int track) {
    int ink = ui_set_w(canvas, s, medium, px, track), over = ink - w;
    if (over <= 0) return ui_set(canvas, s, medium, px, rgb, 0, x + (w - ink) / 2, y, track);
    off = off < 0 ? 0 : off > over ? over : off;
    int was[4], ox = I(canvas, CANVAS_X), oy = I(canvas, CANVAS_Y),
                col[4] = { ox + x, oy + y - px / 2, w, px };
    canvas_get_clip_rect(canvas, was);
    int l = col[0] > was[0] ? col[0] : was[0], t = col[1] > was[1] ? col[1] : was[1],
        r = col[0] + col[2] < was[0] + was[2] ? col[0] + col[2] : was[0] + was[2],
        b = col[1] + col[3] < was[1] + was[3] ? col[1] + col[3] : was[1] + was[3];
    if (r <= l || b <= t) return ink;
    int clip[4] = { l, t, r - l, b - t };
    canvas_set_clip_rect(canvas, clip);
    int faded = text_fade_under(canvas, x, x + w, y - px / 2, px, (off > 0) | (off < over) << 1);
    ui_set(canvas, s, medium, px, rgb, 0, x - off, y, track);
    if (faded) text_fade(canvas);
    canvas_set_clip_rect(canvas, was);
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

/* The empty states' buttons (docs/oled-design.md#buttons). */
int ui_text_w(void *canvas, const char *s, int medium, int px) {
    unsigned buf[96], n = ui_wide(s, buf, sizeof buf / sizeof *buf);
    if (!n) return 0;
    const char *face = theme_face(buf, (int)n, &px);
    if (medium && face && !tk_strcmp(face, "inter")) face = "inter-medium";
    return ui_measure_w(canvas, face, px, buf, n);
}
int ui_cta_w(void *canvas, const char *const *labels, int n) {
    int w = 0;
    for (int i = 0; i < n; ++i) {
        int t = ui_text_w(canvas, labels[i], 1, CTA_PX) + 2 * (n > 1 ? CTA_PAIR_PAD : CTA_PAD);
        if (t > w) w = t;
    }
    int least = n > 1 ? CTA_PAIR_MIN : CTA_MIN, most = n > 1 ? (CTA_BOX - CTA_GAP) / 2 : CTA_BOX;
    return w < least ? least : w > most ? most : w;
}
int ui_cta_x(void *canvas, const char *const *labels, int n, int i, int *w) {
    *w = ui_cta_w(canvas, labels, n);
    return 375 / 2 - (n * *w + (n - 1) * CTA_GAP) / 2 + i * (*w + CTA_GAP);
}
/* The buttons' focus ring in a, CTA_RING_H tall: its round ends tools/art.py's supersampled
 * images (cta_ring_end, Text Primary in the half's ink), the straight runs between them
 * CTA_RING_W whole rows of ink at its top and foot. Without the images (another set's art),
 * round_ring's row-at-a-time rings, a pixel apart. */
static void cta_ring(void *canvas, const rect_t *a, unsigned ink) {
    unsigned bitmap[64]; /* bitmap_t: width @0, height @4 */
    int end = CTA_RING_H / 2, light = ipod_light();
    for (int right = 0; right < 2; ++right) {
        char name[16];
        tk_snprintf(name, sizeof name, "ctaring_%c_%c", right ? 'r' : 'l', light ? 'l' : 'd');
        if (a->h != CTA_RING_H || widget_load_image(window_manager(), theme_art(name), bitmap)) {
            rect_t b = { a->x + 1, a->y + 1, a->w - 2, a->h - 2 };
            round_ring(canvas, a, a->h / 2, ink);
            round_ring(canvas, &b, b.h / 2, ink);
            return;
        }
        rect_t src = { 0, 0, (int)bitmap[0], (int)bitmap[1] },
               dst = { right ? a->x + a->w - end : a->x, a->y, src.w, src.h };
        canvas_draw_image(canvas, bitmap, &src, &dst);
    }
    canvas_set_fill_color(canvas, ink);
    canvas_fill_rect(canvas, a->x + end, a->y, a->w - 2 * end, CTA_RING_W);
    canvas_fill_rect(canvas, a->x + end, a->y + a->h - CTA_RING_W, a->w - 2 * end, CTA_RING_W);
}
/* One capsule at r: primary the accent, else the grey; ringed while ui_focus_shown when lit; down
 * a step darker. */
static void cta_capsule(void *canvas, rect_t *r, const char *label, int primary, int lit,
                        int down) {
    int light = ipod_light();
    unsigned fill = !primary
                        ? (light ? (down ? 0xd1d1d6 : 0xe5e5ea) : (down ? RGB_OFF : RGB_RAISED_2))
                    : down ? CTA_ACCENT_DOWN
                           : (scheme()->tones[0] & 0xffffffu);
    unsigned raw = ui_ink_raw(1); /* final in both halves */
    if (lit && ui_focus_shown()) {
        /* a CTA_RING_W px ring in Text Primary CTA_RING_OFF px out, concentric with the capsule */
        int o = CTA_RING_OFF + CTA_RING_W;
        rect_t a = { r->x - o, r->y - o, r->w + 2 * o, r->h + 2 * o };
        cta_ring(canvas, &a, RGBA(tok(TOK_PRIMARY)));
    }
    fill_box(canvas, r, RGBA(fill), CTA_H / 2);
    ui_ink_raw(raw);
    ui_text(canvas, label, 1, CTA_PX, primary ? 0xffffff : tok(TOK_PRIMARY), 1, r->x + r->w / 2,
            r->y + r->h / 2, r->w - 2 * CTA_INSET, 1);
}
void ui_cta_paint(void *canvas, const char *const *labels, int n, int y, int sel, int pressed) {
    for (int i = 0; i < n; ++i) {
        rect_t r = { 0, y, 0, CTA_H };
        r.x = ui_cta_x(canvas, labels, n, i, &r.w);
        cta_capsule(canvas, &r, labels[i], !i, i == sel, i == sel && pressed);
    }
}
int ui_button(void *canvas, int cx, int cy, const char *label, int primary, int focused,
              int pressed) {
    int w = ui_cta_w(canvas, &label, 1);
    rect_t r = { cx - w / 2, cy - CTA_H / 2, w, CTA_H };
    cta_capsule(canvas, &r, label, primary, focused, pressed);
    return w;
}

/* Motion (ui_kit.h): the one Reduce Motion flag, the curves, a run's progress and the shared
 * frame timer. */
int motion_reduced(void) {
    accent(); /* the [IPOD] keys, read once */
    return st.reduce_motion;
}
int motion_ms(int ms, int kind) { return kind == MO_FEEDBACK || !motion_reduced() ? ms : 0; }
int motion_fade_ms(int ms) { return motion_reduced() ? MO_FADE_MS : ms; }
const motion_t *motion_row(int kind) {
    /* a window kind fades: Page's too, whose slide is its windows' asset hint (tools/ipod.py
     * SLIDE) and so no row of its own; a swap and an in-place slide are immediate (ms 0); the
     * wheel's steps are the scroll itself and stay */
    static const motion_t fade = { "fade(duration=150)", 0, 0 }, still = { 0, 0, 0 };
    _Static_assert(MO_FADE_MS == 150, "fade's duration is MO_FADE_MS");
    const motion_t *m = &theme()->motion[kind];
    if (!motion_reduced() || kind >= MOTION_STEP) return m;
    if (kind == MOTION_INPLACE || kind == MOTION_SWAP) return &still;
    return m->hint || kind == MOTION_PAGE ? &fade : m;
}
int motion_ease(int t, int ease) {
    if (t <= 0) return 0;
    if (t >= 1000) return 1000;
    int u = 1000 - t;
    switch (ease) {
    case EASE_LINEAR:
        return t;
    case EASE_QUAD_IN:
        return t * t / 1000;
    case EASE_QUAD_OUT:
        return 1000 - u * u / 1000;
    case EASE_CUBIC_IN:
        return t * t / 1000 * t / 1000;
    default:
        return 1000 - u * u / 1000 * u / 1000; /* EASE_CUBIC_OUT */
    }
}
static struct {
    unsigned until, timer; /* the last running motion's end, time_now_ms; the shared timer */
} mo __attribute__((section(".scratch")));
/* The overlays ovm_bind tied to a widget, whose opacity each frame sets. */
#define OVM_BOUND 4
static struct {
    ovm_t *o;
    void *w;
} bound[OVM_BOUND] __attribute__((section(".scratch")));
static void ovm_apply(void) {
    for (int i = 0; i < OVM_BOUND; ++i)
        if (bound[i].w) widget_set_opacity(bound[i].w, (unsigned)ovm_opacity(bound[i].o));
}
static void (*mo_hooks[MO_HOOKS])(void) __attribute__((section(".scratch")));
void motion_hook(void (*fn)(void)) {
    for (int i = 0; i < MO_HOOKS; ++i)
        if (mo_hooks[i] == fn) return;
    for (int i = 0; i < MO_HOOKS; ++i)
        if (!mo_hooks[i]) {
            mo_hooks[i] = fn;
            return;
        }
}
static int mo_frame(const void *info) {
    (void)info;
    ovm_apply();
    void *top = window_manager_get_top_window(window_manager());
    if (top) widget_invalidate_force(top, (void *)0);
    for (int i = 0; i < MO_HOOKS; ++i)
        if (mo_hooks[i]) mo_hooks[i]();
    if ((int)(mo.until - (unsigned)time_now_ms()) > -MO_FRAME_MS) return 8; /* RET_REPEAT */
    mo.timer = 0;
    return 7; /* RET_REMOVE */
}
void motion_frames(unsigned ms) {
    unsigned end = (unsigned)time_now_ms() + ms;
    if (!mo.timer || (int)(end - mo.until) > 0) mo.until = end;
    if (!mo.timer) mo.timer = timer_add(mo_frame, (void *)0, MO_FRAME_MS);
}
void anim_start(anim_t *a, int ms, int ease, int kind) {
    a->at = (unsigned)time_now_ms() ?: 1;
    a->ms = (unsigned short)motion_ms(ms, kind);
    a->ease = (unsigned char)ease;
    if (a->ms) motion_frames(a->ms);
}
int anim_at(const anim_t *a) {
    unsigned gone = (unsigned)time_now_ms() - a->at;
    if (!a->at || !a->ms || gone >= a->ms) return 1000;
    return motion_ease((int)(gone * 1000 / a->ms), a->ease);
}
int anim_running(const anim_t *a) {
    return a->at && a->ms && (unsigned)time_now_ms() - a->at < a->ms;
}

/* An overlay's run: from its level at the start (ovm_level) to 1000 in, or 0 out. Under Reduce
 * Motion an enter takes MO_FADE_MS and an exit MO_OUT_FADE_MS, and nothing moves or scales. */
static void ovm_go(ovm_t *o, int ms, int ease, int out) {
    o->from = (short)ovm_level(o);
    o->out = (unsigned char)out;
    anim_start(&o->a, motion_reduced() ? out ? MO_OUT_FADE_MS : MO_FADE_MS : ms, ease, MO_FEEDBACK);
}
void ovm_enter(ovm_t *o, int ms, int ease) { ovm_go(o, ms, ease, 0); }
void ovm_exit(ovm_t *o, int ms, int ease) { ovm_go(o, ms, ease, 1); }
int ovm_level(const ovm_t *o) {
    if (!o->a.at) return 0;
    int p = anim_at(&o->a);
    return o->out ? o->from - o->from * p / 1000 : o->from + (1000 - o->from) * p / 1000;
}
int ovm_alpha(const ovm_t *o, int alpha) { return alpha * ovm_level(o) / 1000; }
int ovm_shift(const ovm_t *o, int px) {
    return motion_reduced() ? 0 : px * (1000 - ovm_level(o)) / 1000;
}
int ovm_scale(const ovm_t *o, int from) {
    return motion_reduced() ? 1000 : from + (1000 - from) * ovm_level(o) / 1000;
}
int ovm_running(const ovm_t *o) { return anim_running(&o->a); }
int ovm_shown(const ovm_t *o) { return o->a.at && (!o->out || anim_running(&o->a)); }
int ovm_opacity(const ovm_t *o) {
    return !o->out && o->a.at && motion_reduced() ? 255 : ovm_alpha(o, 255);
}
static int ovm_gone(void *ctx, void *event) {
    (void)event;
    for (int i = 0; i < OVM_BOUND; ++i)
        if (bound[i].w == ctx) bound[i].o = (ovm_t *)0, bound[i].w = (void *)0;
    return 0;
}
void ovm_bind(ovm_t *o, void *w) {
    int at = -1;
    for (int i = 0; i < OVM_BOUND; ++i)
        if (bound[i].o == o || (at < 0 && !bound[i].w)) at = i;
    if (at < 0) return; /* ponytail: four at once; a fifth shows at the opacity it has */
    bound[at].o = w ? o : (ovm_t *)0;
    bound[at].w = w;
    if (!w) return;
    widget_on(w, EVT_DESTROY, ovm_gone, w);
    widget_set_opacity(w, (unsigned)ovm_opacity(o));
}
