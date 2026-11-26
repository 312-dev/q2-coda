/* Drawing into the canvas: fills, a rounded box with a smoothed edge, a glyph's coverage. Every
 * call clips to the rect being drawn (clip_set), so a frame's cost is its changed part's. */
#include "q2ui.h"

static rect_t clip = { 0, 0, SCREEN_W, SCREEN_H };

int rect_meet(rect_t a, rect_t b, rect_t *out) {
    int x0 = a.x > b.x ? a.x : b.x, y0 = a.y > b.y ? a.y : b.y;
    int x1 = a.x + a.w < b.x + b.w ? a.x + a.w : b.x + b.w;
    int y1 = a.y + a.h < b.y + b.h ? a.y + a.h : b.y + b.h;
    if (x1 <= x0 || y1 <= y0) return 0;
    if (out) *out = (rect_t){ x0, y0, x1 - x0, y1 - y0 };
    return 1;
}
void clip_set(rect_t r) {
    rect_t screen = { 0, 0, SCREEN_W, SCREEN_H };
    if (!rect_meet(r, screen, &clip)) clip = (rect_t){ 0, 0, 0, 0 };
}
rect_t clip_get(void) { return clip; }

static inline uint32_t mix(uint32_t dst, uint32_t rgb, unsigned a) { /* a 0-256 */
    uint32_t rb = ((rgb & 0xff00ff) * a + (dst & 0xff00ff) * (256 - a)) >> 8 & 0xff00ff;
    uint32_t g = ((rgb & 0xff00) * a + (dst & 0xff00) * (256 - a)) >> 8 & 0xff00;
    return 0xff000000 | rb | g;
}

void fill(rect_t r, uint32_t rgb) {
    rect_t c;
    if (!rect_meet(r, clip, &c)) return;
    rgb |= 0xff000000;
    for (int y = c.y; y < c.y + c.h; ++y)
        for (uint32_t *p = canvas + y * SCREEN_W + c.x, *end = p + c.w; p < end; ++p) *p = rgb;
}

void fill_alpha(rect_t r, uint32_t rgb, unsigned alpha) {
    rect_t c;
    if (!alpha || !rect_meet(r, clip, &c)) return;
    unsigned a = alpha + (alpha >> 7);
    for (int y = c.y; y < c.y + c.h; ++y)
        for (uint32_t *p = canvas + y * SCREEN_W + c.x, *end = p + c.w; p < end; ++p)
            *p = mix(*p, rgb, a);
}

void shade_rows(int y0, int y1, int a0, int a1) {
    rect_t c, band = { 0, y0, SCREEN_W, y1 - y0 };
    if (!rect_meet(band, clip, &c)) return;
    for (int y = c.y; y < c.y + c.h; ++y) {
        unsigned a = (unsigned)(a0 + (a1 - a0) * (y - y0) / (y1 - y0 - 1 > 0 ? y1 - y0 - 1 : 1));
        a += a >> 7;
        for (uint32_t *p = canvas + y * SCREEN_W + c.x, *end = p + c.w; p < end; ++p)
            *p = mix(0, *p, a);
    }
}

static unsigned root(unsigned n) {
    unsigned r = 0, bit = 1u << 30;
    while (bit > n) bit >>= 2;
    for (; bit; bit >>= 2)
        if (n >= r + bit)
            n -= r + bit, r = (r >> 1) + bit;
        else
            r >>= 1;
    return r;
}
/* How much of a pixel a disc of radius r covers, 0-256, its centre dx, dy away, all in 1/16 px:
 * the edge is a pixel wide, by the distance alone (the game engine's, patch/engine.c cover). */
static unsigned cover(int dx, int dy, int r) {
    unsigned d2 = (unsigned)(dx * dx + dy * dy);
    int in = r - 8, out = r + 8;
    if (in > 0 && d2 <= (unsigned)(in * in)) return 256;
    if (d2 >= (unsigned)(out * out)) return 0;
    return (unsigned)(out - (int)root(d2)) * 16;
}

void fill_round(rect_t r, int radius, uint32_t rgb) {
    rect_t c;
    if (2 * radius > r.w) radius = r.w / 2;
    if (2 * radius > r.h) radius = r.h / 2;
    if (!rect_meet(r, clip, &c)) return;
    for (int y = c.y; y < c.y + c.h; ++y) {
        int v = y - r.y, cy = v < radius ? radius : v >= r.h - radius ? r.h - radius : -1;
        uint32_t *p = canvas + y * SCREEN_W + c.x;
        if (cy < 0) { /* a row between the corners: solid */
            for (uint32_t *end = p + c.w; p < end; ++p) *p = 0xff000000 | rgb;
            continue;
        }
        for (int x = c.x; x < c.x + c.w; ++x, ++p) {
            int u = x - r.x, cx = u < radius ? radius : u >= r.w - radius ? r.w - radius : -1;
            unsigned a =
                cx < 0 ? 256 : cover(u * 16 + 8 - cx * 16, v * 16 + 8 - cy * 16, radius * 16);
            if (a) *p = a == 256 ? 0xff000000 | rgb : mix(*p, rgb, a);
        }
    }
}

void blit_a8(const uint8_t *bits, int w, int h, int x, int y, uint32_t rgb) {
    rect_t c, box = { x, y, w, h };
    if (!rect_meet(box, clip, &c)) return;
    for (int j = c.y; j < c.y + c.h; ++j) {
        const uint8_t *s = bits + (j - y) * w + (c.x - x);
        uint32_t *p = canvas + j * SCREEN_W + c.x;
        for (int i = 0; i < c.w; ++i, ++p) {
            unsigned a = s[i];
            if (a == 255)
                *p = 0xff000000 | rgb;
            else if (a)
                *p = mix(*p, rgb, a + (a >> 7));
        }
    }
}
