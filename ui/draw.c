/* Drawing into the canvas: fills, rounded boxes with smoothed edges at sub-pixel places (leaning
 * ones too), soft shadows, dithered ramps and fades, glyph coverage. Every call clips to the rect
 * being drawn (clip_set), so a frame's cost is its changed part's.
 *
 * Edges: a shape's edge pixel is blended by the share of the pixel the shape covers, onto the
 * colour already there. Ramps: computed past 8 bits and brought back with blue noise under the
 * last bit (dither64), so a fade over 50 rows is grain, never bands
 * (docs/ui-rewrite.md#gradients). */
#include <math.h>

#include "px.h"
#include "q2ui.h"

const uint8_t dither64[64 * 64] = {
#include "bluenoise.inc"
};

static rect_t clip = { 0, 0, SCREEN_W, SCREEN_H };
static struct {
    int on, x0, x1, left, right;
} fade;

int rect_meet(rect_t a, rect_t b, rect_t *out) {
    int x0 = a.x > b.x ? a.x : b.x, y0 = a.y > b.y ? a.y : b.y;
    int x1 = a.x + a.w < b.x + b.w ? a.x + a.w : b.x + b.w;
    int y1 = a.y + a.h < b.y + b.h ? a.y + a.h : b.y + b.h;
    if (x1 <= x0 || y1 <= y0) return 0;
    if (out) *out = (rect_t){ x0, y0, x1 - x0, y1 - y0 };
    return 1;
}
rect_t rect_union(rect_t a, rect_t b) {
    if (a.w <= 0 || a.h <= 0) return b;
    if (b.w <= 0 || b.h <= 0) return a;
    int x0 = a.x < b.x ? a.x : b.x, y0 = a.y < b.y ? a.y : b.y;
    int x1 = a.x + a.w > b.x + b.w ? a.x + a.w : b.x + b.w;
    int y1 = a.y + a.h > b.y + b.h ? a.y + a.h : b.y + b.h;
    return (rect_t){ x0, y0, x1 - x0, y1 - y0 };
}
rect_t rect_of(rectf_t r, int pad) {
    int x0 = (int)floorf(r.x) - pad, y0 = (int)floorf(r.y) - pad;
    int x1 = (int)ceilf(r.x + r.w) + pad, y1 = (int)ceilf(r.y + r.h) + pad;
    return (rect_t){ x0, y0, x1 - x0, y1 - y0 };
}
void clip_set(rect_t r) {
    rect_t screen = { 0, 0, SCREEN_W, SCREEN_H };
    if (!rect_meet(r, screen, &clip)) clip = (rect_t){ 0, 0, 0, 0 };
}
rect_t clip_get(void) { return clip; }

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
            *p = px_mix(*p, rgb, a);
}

#define EYE PX_EYE

/* A float to 1/64 px, rounded. */
static inline int q6(float v) { return (int)(v * PX_ONE + (v < 0 ? -0.5f : 0.5f)); }

void fill_round(rectf_t r, float radius, float tilt, uint32_t rgb, float alpha, const gleam_t *g) {
    float hw = r.w / 2, hh = r.h / 2, cx = r.x + hw, cy = r.y + hh;
    if (hw <= 0 || hh <= 0 || alpha <= 0) return;
    if (radius > hw) radius = hw;
    if (radius > hh) radius = hh;
    float st = tilt ? sinf(tilt) : 0, ct = tilt ? cosf(tilt) : 1, grow = EYE / (EYE - hh * fabsf(st));
    rect_t c, box = rect_of((rectf_t){ cx - hw * grow, cy - hh * grow, 2 * hw * grow, 2 * hh * grow }, 1);
    if (!rect_meet(box, clip, &c)) return;
    int whole = (int)(clampf(alpha, 0, 1) * 256 + 0.5f), shine = g && g->strength > 0;
    int hwq = q6(hw), hhq = q6(hh), rq = q6(radius), end = c.x + c.w;
    for (int y = c.y; y < c.y + c.h; ++y) {
        /* the row of the box this scanline shows, and its size on screen: a lean turns the box
         * about its middle line, so each scanline is one row of it at one scale. The row's own
         * numbers are worked out once, in floats; its pixels are whole-number work. */
        float dy = (float)y + 0.5f - cy, v = st ? dy * EYE / (EYE * ct + dy * st) : dy;
        float s = st ? EYE / (EYE - v * st) : 1;
        int avq = q6(fabsf(v)), sq = (int)(s * 256 + 0.5f), inv = (int)(65536 / s);
        /* coverage, 0 to 256, from a distance outside the box in 1/64 px, scaled to the screen */
#define COVER(d) (128 - (((d) * sq) >> 6))
        int mid = COVER(avq - hhq);
        if (mid <= 0) continue;
        if (mid > 256) mid = 256;
        /* between these columns the row is clear of the corners and the ends: one coverage for
         * all of them */
        float inner = (hw - radius - 1) * s;
        int in0 = (int)ceilf(cx - inner), in1 = (int)floorf(cx + inner);
        if (in0 < c.x) in0 = c.x;
        if (in1 > end) in1 = end;
        if (in1 < in0) in0 = in1 = c.x;
        const uint8_t *noise = dither64 + (y & 63) * 64;
        uint32_t *row = canvas + y * SCREEN_W;
        unsigned amid = (unsigned)(mid * whole) >> 8;
        /* a column's place across the box, 1/64 px, is a straight line of the screen's */
        int u0 = q6(((float)c.x - cx) / s), x = c.x;
        for (int pass = 0; pass < 2; ++pass) {
            for (int stop = pass ? end : in0; x < stop; ++x) {
                int u = (int)(((int64_t)(2 * (x - c.x) + 1) * inv) >> 11) + u0;
                int a = COVER(px_box_dist(u < 0 ? -u : u, avq, hwq, hhq, rq));
                if (a <= 0) continue;
                a = (a > 256 ? 256 : a) * whole >> 8;
                if (rgb == RGB_GLEAM_ONLY) { /* the gleam alone, at the box's ends and corners */
                    float gu = ((float)u / PX_ONE - g->gx) / g->rx, gv = (v - g->gy) / g->ry, k = 1 - gu * gu - gv * gv;
                    if (shine && k > 0)
                        row[x] = px_mix16(row[x], 0xffffff, (unsigned)(g->strength * k * k * 65535) * (unsigned)a >> 8, noise[x & 63]);
                    continue;
                }
                row[x] = a >= 256 ? 0xff000000 | rgb : px_mix(row[x], rgb, (unsigned)a);
            }
            if (pass) break;
            x = in0;
            if (!shine) {
                if (amid >= 256)
                    for (; x < in1; ++x) row[x] = 0xff000000 | rgb;
                else
                    for (; x < in1; ++x) row[x] = px_mix(row[x], rgb, amid);
            } else {
                /* the gleam, in 1/16384: 1 - (gu^2 + gv^2), squared, of white */
                float gv = (v - g->gy) / g->ry;
                int gv2 = (int)(gv * gv * 16384), strength = (int)(g->strength * 65535);
                int gu = (int)((((float)x + 0.5f - cx) / s - g->gx) / g->rx * 16384);
                int step = (int)(16384 / (s * g->rx));
                for (; x < in1; ++x, gu += step) {
                    int k = 16384 - ((gu >> 2) * (gu >> 2) >> 10) - gv2;
                    uint32_t ink = rgb;
                    unsigned white = k > 0 && gu > -32768 && gu < 32768 ? (unsigned)((k * k >> 14) * (strength >> 2) >> 12) : 0;
                    if (rgb == RGB_GLEAM_ONLY) {
                        if (white) row[x] = px_mix16(row[x], 0xffffff, white * amid >> 8, noise[x & 63]);
                        continue;
                    }
                    if (white) ink = px_mix16(rgb, 0xffffff, white, noise[x & 63]) & 0xffffff;
                    row[x] = amid >= 256 ? 0xff000000 | ink : px_mix(row[x], ink, amid);
                }
            }
        }
#undef COVER
    }
}

void fill_disc(float cx, float cy, float radius, uint32_t rgb, float alpha) {
    fill_round((rectf_t){ cx - radius, cy - radius, 2 * radius, 2 * radius }, radius, 0, rgb, alpha, 0);
}

void stroke_line(float x0, float y0, float x1, float y1, float width, uint32_t rgb, float alpha) {
    float lx = x0 < x1 ? x0 : x1, ly = y0 < y1 ? y0 : y1, half = width / 2;
    rect_t c, box = rect_of((rectf_t){ lx - half, ly - half, fabsf(x1 - x0) + width, fabsf(y1 - y0) + width }, 1);
    if (!rect_meet(box, clip, &c)) return;
    int whole = (int)(clampf(alpha, 0, 1) * 256 + 0.5f);
    /* in 1/64 px from the stroke's start */
    int dx = q6(x1 - x0), dy = q6(y1 - y0), len2 = dx * dx + dy * dy, halfq = q6(half);
    for (int y = c.y; y < c.y + c.h; ++y) {
        uint32_t *p = canvas + y * SCREEN_W + c.x;
        int py = q6((float)y + 0.5f - y0);
        for (int x = c.x; x < c.x + c.w; ++x, ++p) {
            /* the pixel's distance from the stroke's middle line, its round ends included */
            int px = q6((float)c.x + 0.5f - x0) + (x - c.x) * PX_ONE;
            int dot = px * dx + py * dy, t = len2 <= 0 || dot <= 0 ? 0 : dot >= len2 ? 256 : (int)(((int64_t)dot << 8) / len2);
            int ex = px - (t * dx >> 8), ey = py - (t * dy >> 8);
            int a = 128 - (((int)px_isqrt((unsigned)(ex * ex + ey * ey)) - halfq) * 4);
            if (a <= 0) continue;
            a = (a > 256 ? 256 : a) * whole >> 8;
            *p = a >= 256 ? 0xff000000 | rgb : px_mix(*p, rgb, (unsigned)a);
        }
    }
}

void shadow_round(rectf_t r, float radius, float blur, uint32_t rgb, float alpha) {
    float hw = r.w / 2, hh = r.h / 2, cx = r.x + hw, cy = r.y + hh;
    rect_t c, box = rect_of(r, (int)ceilf(blur) + 1);
    if (blur <= 0 || alpha <= 0 || !rect_meet(box, clip, &c)) return;
    /* between these columns a row is clear of the corners: its distance from the box is the
     * row's own, one value for all of them */
    int in0 = (int)ceilf(cx - (hw - radius)), in1 = (int)floorf(cx + (hw - radius)), end = c.x + c.w;
    if (in0 < c.x) in0 = c.x;
    if (in1 > end) in1 = end;
    if (in1 < in0) in0 = in1 = c.x;
    int hwq = q6(hw), hhq = q6(hh), rq = q6(radius), blurq = q6(blur), top = (int)(alpha * 65535);
    /* the shadow's strength d (1/64 px) outside the box: all of it at the edge, none blur out,
     * falling as the square */
#define SHADE(d) (t = blurq - ((d) < 0 ? 0 : (d)), (unsigned)(((int64_t)top * t / blurq) * t / blurq))
    for (int y = c.y; y < c.y + c.h; ++y) {
        int avq = q6(fabsf((float)y + 0.5f - cy)), dmid = avq - hhq, t;
        const uint8_t *noise = dither64 + (y & 63) * 64;
        uint32_t *row = canvas + y * SCREEN_W;
        int x = c.x, x0q = q6((float)c.x + 0.5f - cx);
        for (int pass = 0; pass < 2; ++pass) {
            for (int stop = pass ? end : in0; x < stop; ++x) {
                int u = x0q + (x - c.x) * PX_ONE, d = px_box_dist(u < 0 ? -u : u, avq, hwq, hhq, rq);
                if (d >= blurq) continue;
                row[x] = px_mix16(row[x], rgb, SHADE(d), noise[x & 63]);
            }
            if (pass) break;
            if (dmid < blurq) {
                unsigned a = SHADE(dmid);
                for (; x < in1; ++x) row[x] = px_mix16(row[x], rgb, a, noise[x & 63]);
            }
            x = in1;
        }
    }
#undef SHADE
}

/* p scaled to keep/65536 of itself, noise under the last bit */
static inline uint32_t dim16(uint32_t p, unsigned keep, unsigned noise) {
    return px_mix16(0, p, keep, noise);
}
void shade_rows(int y0, int y1, int a0, int a1) {
    rect_t c, band = { 0, y0, SCREEN_W, y1 - y0 };
    if (!rect_meet(band, clip, &c)) return;
    int span = y1 - y0 - 1 > 0 ? y1 - y0 - 1 : 1;
    for (int y = c.y; y < c.y + c.h; ++y) {
        unsigned keep = (unsigned)((a0 * 257 * (span - (y - y0)) + a1 * 257 * (y - y0)) / span);
        const uint8_t *noise = dither64 + (y & 63) * 64;
        uint32_t *p = canvas + y * SCREEN_W + c.x;
        for (int x = c.x; x < c.x + c.w; ++x, ++p)
            if (*p & 0xffffff) *p = dim16(*p, keep, noise[x & 63]);
    }
}
void shade_cols(int x0, int x1, int a0, int a1) {
    rect_t c, band = { x0, 0, x1 - x0, SCREEN_H };
    if (!rect_meet(band, clip, &c)) return;
    int span = x1 - x0 - 1 > 0 ? x1 - x0 - 1 : 1;
    for (int y = c.y; y < c.y + c.h; ++y) {
        const uint8_t *noise = dither64 + (y & 63) * 64;
        uint32_t *p = canvas + y * SCREEN_W + c.x;
        for (int x = c.x; x < c.x + c.w; ++x, ++p) {
            unsigned keep = (unsigned)((a0 * 257 * (span - (x - x0)) + a1 * 257 * (x - x0)) / span);
            if (*p & 0xffffff) *p = dim16(*p, keep, noise[x & 63]);
        }
    }
}
void shade_all(rect_t r, unsigned keep) {
    rect_t c;
    if (keep >= 255 || !rect_meet(r, clip, &c)) return;
    for (int y = c.y; y < c.y + c.h; ++y)
        for (uint32_t *p = canvas + y * SCREEN_W + c.x, *end = p + c.w; p < end; ++p)
            *p = px_mix(0, *p, keep + (keep >> 7));
}

void fill_ramp(rect_t r, uint32_t top, uint32_t bottom) {
    rect_t c;
    if (!rect_meet(r, clip, &c)) return;
    int span = r.h - 1 > 0 ? r.h - 1 : 1;
    for (int y = c.y; y < c.y + c.h; ++y) {
        unsigned k = (unsigned)((y - r.y) * 65535 / span);
        const uint8_t *noise = dither64 + (y & 63) * 64;
        uint32_t *p = canvas + y * SCREEN_W + c.x;
        for (int x = c.x; x < c.x + c.w; ++x, ++p) *p = px_mix16(top, bottom, k, noise[x & 63]);
    }
}

void fade_set(int x0, int x1, int left, int right) {
    fade.on = 1, fade.x0 = x0, fade.x1 = x1, fade.left = left, fade.right = right;
}
void fade_clear(void) { fade.on = 0; }
/* how much (0-256) of a blit shows at column x under the fade window */
static inline unsigned fade_at(int x) {
    if (x < fade.x0 || x >= fade.x1) return 0;
    if (fade.left && x < fade.x0 + fade.left) return (unsigned)((x - fade.x0) * 256 / fade.left);
    if (fade.right && x >= fade.x1 - fade.right) return (unsigned)((fade.x1 - 1 - x) * 256 / fade.right);
    return 256;
}

void blit_a8(const uint8_t *bits, int w, int h, float fx, float fy, uint32_t rgb, unsigned alpha) {
    int x = (int)fx, y = (int)fy;
    if ((float)x > fx) --x;
    if ((float)y > fy) --y;
    /* the part of a pixel the bitmap sits right and down of the grid, in sixteenths */
    int sx = (int)((fx - (float)x) * 16 + 0.5f), sy = (int)((fy - (float)y) * 16 + 0.5f);
    if (sx == 16) sx = 0, ++x;
    if (sy == 16) sy = 0, ++y;
    rect_t c, box = { x, y, w + (sx > 0), h + (sy > 0) };
    if (!alpha || !rect_meet(box, clip, &c)) return;
    int plain = alpha >= 256 && !fade.on;
    if (plain && !sx && !sy) {
        /* on the grid, whole: most text, most frames */
        for (int j = c.y; j < c.y + c.h; ++j) {
            const uint8_t *s = bits + (j - y) * w + (c.x - x);
            uint32_t *p = canvas + j * SCREEN_W + c.x;
            for (int i = 0; i < c.w; ++i, ++p) {
                unsigned a = s[i];
                if (a == 255)
                    *p = 0xff000000 | rgb;
                else if (a)
                    *p = px_mix(*p, rgb, a + (a >> 7));
            }
        }
        return;
    }
    static const uint8_t none[256];
    if (plain && !sx && w <= (int)sizeof none) {
        /* between two rows of the grid, as a list gliding is: each pixel is its two rows' shares */
        for (int j = c.y; j < c.y + c.h; ++j) {
            int v = j - y;
            const uint8_t *row = (v < h ? bits + v * w : none) + (c.x - x);
            const uint8_t *up = (v > 0 ? bits + (v - 1) * w : none) + (c.x - x);
            uint32_t *p = canvas + j * SCREEN_W + c.x;
            for (int i = 0; i < c.w; ++i, ++p) {
                unsigned a = (row[i] * (unsigned)(16 - sy) + up[i] * (unsigned)sy + 8) >> 4;
                if (a >= 255)
                    *p = 0xff000000 | rgb;
                else if (a)
                    *p = px_mix(*p, rgb, a + (a >> 7));
            }
        }
        return;
    }
    for (int j = c.y; j < c.y + c.h; ++j) {
        int v = j - y;
        const uint8_t *row = v < h ? bits + v * w : 0, *up = v > 0 ? bits + (v - 1) * w : 0;
        uint32_t *p = canvas + j * SCREEN_W + c.x;
        for (int i = c.x; i < c.x + c.w; ++i, ++p) {
            int u = i - x;
            /* each pixel takes its share of the four the shifted bitmap lays over it */
            unsigned here = row && u < w ? row[u] : 0, left = row && u > 0 ? row[u - 1] : 0;
            unsigned above = up && u < w ? up[u] : 0, corner = up && u > 0 ? up[u - 1] : 0;
            unsigned a = (here * (unsigned)((16 - sx) * (16 - sy)) + left * (unsigned)(sx * (16 - sy)) +
                          above * (unsigned)((16 - sx) * sy) + corner * (unsigned)(sx * sy) + 128) >> 8;
            if (!a) continue;
            a += a >> 7;
            if (alpha < 256) a = a * alpha >> 8;
            if (fade.on) a = a * fade_at(i) >> 8;
            if (a >= 256)
                *p = 0xff000000 | rgb;
            else if (a)
                *p = px_mix(*p, rgb, a);
        }
    }
}
