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
#define box_dist px_box_dist

void fill_round(rectf_t r, float radius, float tilt, uint32_t rgb, float alpha, const gleam_t *g) {
    float hw = r.w / 2, hh = r.h / 2, cx = r.x + hw, cy = r.y + hh;
    if (hw <= 0 || hh <= 0 || alpha <= 0) return;
    if (radius > hw) radius = hw;
    if (radius > hh) radius = hh;
    float st = sinf(tilt), ct = cosf(tilt), grow = EYE / (EYE - hh * fabsf(st));
    rect_t c, box = rect_of((rectf_t){ cx - hw * grow, cy - hh * grow, 2 * hw * grow, 2 * hh * grow }, 1);
    if (!rect_meet(box, clip, &c)) return;
    unsigned whole = (unsigned)(clampf(alpha, 0, 1) * 256 + 0.5f);
    int shine = g && g->strength > 0;
    for (int y = c.y; y < c.y + c.h; ++y) {
        /* the row of the box this scanline shows, and its size on screen: a lean turns the box
         * about its middle line, so each scanline is one row of it at one scale */
        float dy = (float)y + 0.5f - cy, v = dy * EYE / (EYE * ct + dy * st);
        float s = EYE / (EYE - v * st), av = fabsf(v);
        float mid = clampf(0.5f - (av - hh) * s, 0, 1);
        if (mid <= 0) continue;
        /* between these columns the row is clear of the corners and the ends */
        float inner = (hw - radius - 1) * s;
        int in0 = (int)ceilf(cx - inner), in1 = (int)floorf(cx + inner);
        const uint8_t *noise = dither64 + (y & 63) * 64;
        uint32_t *p = canvas + y * SCREEN_W + c.x;
        float gv = shine ? (v - g->gy) / g->ry : 0;
        for (int x = c.x; x < c.x + c.w; ++x, ++p) {
            float u = ((float)x + 0.5f - cx) / s, cov = mid;
            if (x < in0 || x >= in1) {
                cov = clampf(0.5f - box_dist(fabsf(u), av, hw, hh, radius) * s, 0, 1);
                if (cov <= 0) continue;
            }
            uint32_t ink = rgb;
            if (shine) {
                float gu = (u - g->gx) / g->rx, k = 1 - (gu * gu + gv * gv);
                if (k > 0)
                    ink = px_mix16(rgb, 0xffffff, (unsigned)(g->strength * k * k * 65535),
                                   noise[x & 63]) & 0xffffff;
            }
            unsigned a = (unsigned)(cov * (float)whole + 0.5f);
            *p = a >= 256 ? 0xff000000 | ink : px_mix(*p, ink, a);
        }
    }
}

void fill_disc(float cx, float cy, float radius, uint32_t rgb, float alpha) {
    fill_round((rectf_t){ cx - radius, cy - radius, 2 * radius, 2 * radius }, radius, 0, rgb, alpha, 0);
}

void stroke_line(float x0, float y0, float x1, float y1, float width, uint32_t rgb, float alpha) {
    float half = width / 2, dx = x1 - x0, dy = y1 - y0, len2 = dx * dx + dy * dy;
    float lx = x0 < x1 ? x0 : x1, ly = y0 < y1 ? y0 : y1;
    rect_t c, box = rect_of((rectf_t){ lx - half, ly - half, fabsf(dx) + width, fabsf(dy) + width }, 1);
    if (!rect_meet(box, clip, &c)) return;
    unsigned whole = (unsigned)(clampf(alpha, 0, 1) * 256 + 0.5f);
    for (int y = c.y; y < c.y + c.h; ++y) {
        uint32_t *p = canvas + y * SCREEN_W + c.x;
        for (int x = c.x; x < c.x + c.w; ++x, ++p) {
            /* the pixel's distance from the stroke's middle line, its round ends included */
            float px = (float)x + 0.5f - x0, py = (float)y + 0.5f - y0;
            float t = len2 > 0 ? clampf((px * dx + py * dy) / len2, 0, 1) : 0;
            float ex = px - t * dx, ey = py - t * dy;
            float cov = clampf(0.5f - (sqrtf(ex * ex + ey * ey) - half), 0, 1);
            unsigned a = (unsigned)(cov * (float)whole + 0.5f);
            if (a) *p = a >= 256 ? 0xff000000 | rgb : px_mix(*p, rgb, a);
        }
    }
}

void shadow_round(rectf_t r, float radius, float blur, uint32_t rgb, float alpha) {
    float hw = r.w / 2, hh = r.h / 2, cx = r.x + hw, cy = r.y + hh;
    rect_t c, box = rect_of(r, (int)ceilf(blur) + 1);
    if (blur <= 0 || alpha <= 0 || !rect_meet(box, clip, &c)) return;
    for (int y = c.y; y < c.y + c.h; ++y) {
        float av = fabsf((float)y + 0.5f - cy);
        const uint8_t *noise = dither64 + (y & 63) * 64;
        uint32_t *p = canvas + y * SCREEN_W + c.x;
        for (int x = c.x; x < c.x + c.w; ++x, ++p) {
            float d = box_dist(fabsf((float)x + 0.5f - cx), av, hw, hh, radius);
            if (d < -1 || d >= blur) continue;
            float t = 1 - (d < 0 ? 0 : d) / blur;
            *p = px_mix16(*p, rgb, (unsigned)(alpha * t * t * 65535), noise[x & 63]);
        }
    }
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
    int x = (int)floorf(fx), y = (int)floorf(fy);
    /* the part of a pixel the bitmap sits right and down of the grid, in sixteenths */
    int sx = (int)((fx - (float)x) * 16 + 0.5f), sy = (int)((fy - (float)y) * 16 + 0.5f);
    if (sx == 16) sx = 0, ++x;
    if (sy == 16) sy = 0, ++y;
    rect_t c, box = { x, y, w + (sx > 0), h + (sy > 0) };
    if (!alpha || !rect_meet(box, clip, &c)) return;
    for (int j = c.y; j < c.y + c.h; ++j) {
        int v = j - y;
        const uint8_t *row = v < h ? bits + v * w : 0, *up = v > 0 ? bits + (v - 1) * w : 0;
        uint32_t *p = canvas + j * SCREEN_W + c.x;
        for (int i = c.x; i < c.x + c.w; ++i, ++p) {
            int u = i - x;
            unsigned a;
            if (!sx && !sy)
                a = row[u];
            else {
                /* each pixel takes its share of the four the shifted bitmap lays over it */
                unsigned here = row && u < w ? row[u] : 0, left = row && u > 0 ? row[u - 1] : 0;
                unsigned above = up && u < w ? up[u] : 0, corner = up && u > 0 ? up[u - 1] : 0;
                a = (here * (unsigned)((16 - sx) * (16 - sy)) + left * (unsigned)(sx * (16 - sy)) +
                     above * (unsigned)((16 - sx) * sy) + corner * (unsigned)(sx * sy) + 128) >> 8;
            }
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
