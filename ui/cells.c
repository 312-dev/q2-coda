/* The cells' mask and ink (cells.h). No float, no allocation; divisions are two a cell a frame.
 *
 * A cell is an ellipse (a disc seen at its tumble's angle, turned by its spin) with a soft edge, a
 * dark rim and a lighter dimple. Its profile is a function of r2, the squared radius in the
 * ellipse's own measure, and r2 is a quadratic of the pixel's place, so along a row it is two adds
 * a pixel (forward differences). The edge's fall is read from a table for the cell's edge sharpness
 * (edge_tab: one row every 3% of sharpness, indexed by r2 across the band where the edge falls);
 * the dimple is a line in r2 under a cap.
 *
 * The reference renderer takes nine samples a pixel. cells_step takes three, on the line through
 * the pixel's middle along which r2 changes fastest, a third of a pixel apart: the same mean and
 * the same spread of r2 as the nine have, at a third of the table reads. cells_step_ss9 takes the
 * nine.
 */
#include <string.h>

#include "cells.h"
#include "cells_data.inc"

#define NIQ (CELLS_NI << 16)

static inline int32_t mul16(int32_t a, int32_t b) { return (int32_t)(((int64_t)a * b) >> 16); }
/* sine of an angle given as a share of a turn in 32 bits, Q15 */
static inline int32_t sin_q15(uint32_t ang) {
    unsigned i = ang >> 22;
    int f = (int)((ang >> 6) & 0xffff), a = sin_tab[i], b = sin_tab[i + 1];
    return a + (((b - a) * f) >> 16);
}
/* a tumble table at |cos| c (Q15) */
static inline int32_t by_cos(const int32_t *t, unsigned c) {
    unsigned i = c >> 7;
    int f = (int)(c & 127);
    return i >= 256 ? t[256] : t[i] + (((t[i + 1] - t[i]) * f) >> 7);
}

/* The loop's clock: the half-turn angle (a share of a turn, 32 bits: half a turn a loop), and the
 * distance the flow has run as a share of the loop (Q32), from the beat table. */
static void clock_of(uint64_t t_us, uint32_t *base, uint32_t *run) {
    uint32_t tau = (uint32_t)(t_us % CELLS_LOOP_US);
    *base = (uint32_t)(((uint64_t)tau * 375299969u) >> 20); /* 2^51 / 6e6 */
    uint32_t u = tau * CELLS_BEATS, whole = u / CELLS_LOOP_US, rem = u % CELLS_LOOP_US;
    uint32_t p = (uint32_t)(((uint64_t)rem * 366504u) >>
                            16); /* 512 * 65536 / 6e6: the table's place, 16.16 */
    uint32_t i = p >> 16, f = p & 0xffff;
    *run = whole * CELLS_BEAT_Q32 + beat_tab[i] +
           (uint32_t)(((uint64_t)(beat_tab[i + 1] - beat_tab[i]) * f) >> 16);
}

/* A cell at one time: where it is, and its profile's numbers. */
typedef struct {
    int32_t x, y;   /* the first copy's middle, px 16.16 */
    int32_t hx, hy; /* half its box, px 16.16: no pixel past it is touched */
    int32_t A, B,
        C; /* the table's place = A u^2 + B u w + C w^2 - jin, u and w px from the middle; 16.16 */
    int32_t m; /* the nine samples' mean lies this far up the table from the middle's place */
    int32_t jin,
        jkt;     /* r2 = 0 is jin below the table's start; the dimple's cap is jkt above r2 = 0 */
    int32_t c0;  /* the dimple's floor, Q16 */
    uint32_t ck; /* its slope: floor + ((place above r2 = 0) * ck >> 32) */
    uint32_t op; /* alpha (0 to 256) = sum of edge * dimple over the samples, times this, >> 32 */
    const uint8_t *edge; /* the edge, 0 to 255: [0] inside the band, [1..NI] its fall across it, [NI
                            + 1] gone */
    uint32_t rcap;       /* the dimple at and past its cap: the rim's full darkness */
} shape_t;

static void shape_of(int i, uint32_t base, uint32_t run, int h, int samples, shape_t *s) {
    uint32_t xf = cell_x0[i] + run;
    s->x = (int32_t)(((uint64_t)cell_period[i] * xf) >> 32) - cell_a[i] - (2 << 16);
    s->y = -(3 << 16) + (int32_t)(cell_y0[i] * (uint32_t)(h + 6)) +
           (int32_t)(((int64_t)cell_wob_a[i] *
                      sin_q15((uint32_t)cell_wob_n[i] * base + cell_wob_p[i])) >>
                     15);
    int32_t ctum = sin_q15((uint32_t)cell_tum_n[i] * base + cell_tum_p[i] + 0x40000000u);
    unsigned c = (unsigned)(ctum < 0 ? -ctum : ctum);
    int32_t asp = 19661 + (int32_t)((45875u * c) >> 15); /* 0.30 + 0.70 |cos| */
    int32_t dm = by_cos(tum_dm, c);
    int k = (cell_lnk[i] + by_cos(tum_lnasp, c) + 128) >> 8;
    k = k < 0 ? 0 : k >= CELLS_NK ? CELLS_NK - 1 : k;
    uint32_t th = cell_rot0[i] + (uint32_t)(int32_t)cell_rot_m[i] * base;
    int32_t ct = sin_q15(th + 0x40000000u), st = sin_q15(th);
    int32_t ct2 = (ct * ct) >> 15, st2 = (st * st) >> 15, cs = (ct * st) >> 15;
    int32_t ia2 = cell_ia2[i],
            ib2 = mul16(ia2, by_cos(tum_iasp2, c)); /* 1 / a^2 and 1 / b^2, Q24 */
    int32_t sc = edge_scale[k];
    int32_t a24 = (int32_t)(((int64_t)ct2 * ia2 + (int64_t)st2 * ib2) >> 15);
    int32_t c24 = (int32_t)(((int64_t)st2 * ia2 + (int64_t)ct2 * ib2) >> 15);
    int32_t b24 = (int32_t)(((int64_t)2 * cs * (ia2 - ib2)) >> 15);
    s->A = (int32_t)(((int64_t)a24 * sc) >> 24);
    s->B = (int32_t)(((int64_t)b24 * sc) >> 24);
    s->C = (int32_t)(((int64_t)c24 * sc) >> 24);
    s->m = (int32_t)(((int64_t)(s->A + s->C) * 4855) >> 16); /* 2/27 of A + C */
    s->jin = edge_jin[k], s->jkt = edge_jkt[k];
    s->c0 = 65536 - dm;
    s->ck = (uint32_t)(((uint64_t)(uint32_t)dm * edge_invkt[k]) >> 8);
    s->op = samples == 3 ? cell_op3[i] : cell_op9[i];
    s->edge = edge_tab + k * (CELLS_NI + 2);
    s->rcap = (uint32_t)s->c0 + (uint32_t)(((uint64_t)(uint32_t)s->jkt * s->ck) >> 32);
    /* its box: the turned ellipse's reach each way, out to where the edge is gone, and half a pixel
     * for the samples' own spread. One Newton step down from the sum of its axes' reaches, which
     * never comes out under the root. */
    int32_t ao = mul16(cell_a[i], edge_rho_o[k]), bo = mul16(ao, asp);
    int32_t ao2 = mul16(ao, ao), bo2 = mul16(bo, bo), act = ct < 0 ? -ct : ct,
            ast = st < 0 ? -st : st;
    uint32_t x0 = (uint32_t)(((int64_t)ao * act + (int64_t)bo * ast) >> 23) + 1;
    uint32_t y0 = (uint32_t)(((int64_t)ao * ast + (int64_t)bo * act) >> 23) + 1; /* px, Q8 */
    uint32_t sx = (uint32_t)(((int64_t)ao2 * ct2 + (int64_t)bo2 * st2) >> 15);
    uint32_t sy = (uint32_t)(((int64_t)ao2 * st2 + (int64_t)bo2 * ct2) >> 15); /* px^2, Q16 */
    s->hx = (int32_t)((((x0 + sx / x0) >> 1) + 2) << 8) + 32768;
    s->hy = (int32_t)((((y0 + sy / y0) >> 1) + 2) << 8) + 32768;
}

/* The edge's row for a place on the table (16.16): 0 inside the band, NI + 1 past the cell. No
 * branch. */
static inline int at(int32_t j) {
    int i = (j >> 16) + 1;
    i = j < 0 ? 0 : i;
    return i > CELLS_NI + 1 ? CELLS_NI + 1 : i;
}
/* edge * dimple at a place on the table */
static inline uint32_t tap(const shape_t *s, int32_t j) {
    int32_t ja = j + s->jin;
    ja = ja < 0 ? 0 : ja;
    ja = ja > s->jkt ? s->jkt : ja;
    return s->edge[at(j)] * ((uint32_t)s->c0 + (uint32_t)(((uint64_t)(uint32_t)ja * s->ck) >> 32));
}

/* A cell's alpha (0 to 256) from its samples' sum, laid over what the mask holds. */
static inline void under(uint8_t *keep, uint32_t sum, uint32_t op) {
    uint32_t al = ((sum >> 10) * op + (1u << 21)) >> 22;
    if (al) *keep = (uint8_t)((*keep * (256 - al) + 128) >> 8);
}

/* The copies of cell i that reach the box, each one's rows handed to `row`. */
#define EACH_ROW(c, s, i)                                                                          \
    for (int32_t x = (s).x, xend = ((c)->w << 16) + cell_a[i] + (2 << 16); x < xend;               \
         x += (int32_t)cell_period[i])                                                             \
        for (int ix0 = (x - (s).hx + 32767) >> 16, ix1 = ((x + (s).hx - 32768) >> 16) + 1,         \
                 iy = ((s).y - (s).hy + 32767) >> 16, iy1 = (((s).y + (s).hy - 32768) >> 16) + 1,  \
                 once = 1;                                                                         \
             once; once = 0)                                                                       \
            if ((ix0 = ix0 < 0 ? 0 : ix0) < (ix1 = ix1 > (c)->w ? (c)->w : ix1))                   \
                for (iy = iy < 0 ? 0 : iy, iy1 = iy1 > (c)->h ? (c)->h : iy1; iy < iy1; ++iy)

static inline uint32_t blocks(int ix0,
                              int ix1) { /* the bits of the blocks columns ix0 to ix1 - 1 lie in */
    int b0 = ix0 >> CELLS_BLOCK, b1 = (ix1 - 1) >> CELLS_BLOCK;
    return (b1 >= 31 ? 0xffffffffu : (2u << b1) - 1) & ~((1u << b0) - 1);
}

void cells_init(cells_t *c, int w, int h) {
    c->w = w > CELLS_MAX_W ? CELLS_MAX_W : w, c->h = h > CELLS_MAX_H ? CELLS_MAX_H : h;
    c->stride = (c->w + 15) & ~15;
    memset(c->keep, 255, (size_t)(c->stride * c->h));
    memset(c->dirty, 0, sizeof c->dirty);
}

/* r2's slope a third of a pixel long, near enough (the larger part and 3/8 of the smaller): how far
 * up and down the table the three samples lie from the middle one */
#define SPREAD(gx, gy, g)                                                                          \
    int32_t ax_ = (gx) < 0 ? -(gx) : (gx), ay_ = (gy) < 0 ? -(gy) : (gy);                          \
    int32_t g = ax_ > ay_ ? ax_ + (ay_ >> 2) + (ay_ >> 3) : ay_ + (ax_ >> 2) + (ax_ >> 3)

void cells_step(cells_t *c, uint64_t t_us) {
    uint32_t base, run;
    clock_of(t_us, &base, &run);
    memset(c->keep, 255, (size_t)(c->stride * c->h));
    memset(c->dirty, 0, sizeof c->dirty[0] * (size_t)c->h);
    for (int i = 0; i < CELLS_N; ++i) {
        shape_t s;
        shape_of(i, base, run, c->h, 3, &s);
        /* the loops' own copies: the mask's bytes may alias anything, a local cannot */
        const int32_t A2 = 2 * s.A, gxs = 2 * s.A / 3, gys = s.B / 3, jin = s.jin, jkt = s.jkt;
        const uint32_t c0 = (uint32_t)s.c0, ck = s.ck, rcap = s.rcap, op = s.op;
        const uint8_t *const edge = s.edge;
        const int sharp = jin >= jkt;
        /* what a pixel keeps (of 256) with all three samples in the rim, inside the band */
        const uint32_t rim =
            255 * (3 * c0 + (uint32_t)(((uint64_t)(3 * (uint32_t)jkt) * ck) >> 32));
        const uint32_t whole = 256 - (((rim >> 10) * op + (1u << 21)) >> 22);
        EACH_ROW(c, s, i) {
            int32_t u = (ix0 << 16) + 32768 - x, w = (iy << 16) + 32768 - s.y;
            int32_t au = mul16(s.A, u), bw = mul16(s.B, w);
            int32_t j = mul16(au, u) + mul16(bw, u) + mul16(mul16(s.C, w), w) - jin + s.m;
            int32_t dj = 2 * au + s.A + bw;
            int32_t gx = (2 * au + bw) / 3, gy = (mul16(s.B, u) + 2 * mul16(s.C, w)) / 3;
            uint8_t *restrict keep = c->keep + iy * c->stride;
            c->dirty[iy] |= blocks(ix0, ix1);
            if (sharp) {
                /* a sharp cell (most of the big ones): the band where its edge falls lies wholly
                 * past the dimple's cap. A sample in the band is the table's value at the rim's
                 * darkness; one inside it is the dimple's line, or the rim; so three samples cost
                 * one multiply. */
                for (int ix = ix0; ix < ix1; ++ix, j += dj, dj += A2, gx += gxs, gy += gys) {
                    SPREAD(gx, gy, g);
                    int32_t t0 = j - g, t2 = j + g, a0 = t0 + jin;
                    uint32_t sum;
                    if (t0 >= NIQ) continue;
                    if (t2 < 0) {        /* all three inside the band: no table */
                        if (a0 >= jkt) { /* and in the rim */
                            keep[ix] = (uint8_t)((keep[ix] * whole + 128) >> 8);
                            continue;
                        }
                        int32_t a1 = j + jin,
                                a2 = t2 + jin; /* never under 0: the middle is on the cell */
                        a0 = a0 < 0 ? 0 : a0, a1 = a1 > jkt ? jkt : a1, a2 = a2 > jkt ? jkt : a2;
                        sum = 255 * (3 * c0 +
                                     (uint32_t)(((uint64_t)(uint32_t)(a0 + a1 + a2) * ck) >> 32));
                    } else {
                        uint32_t es = (uint32_t)edge[at(t0)] + edge[at(j)] + edge[at(t2)];
                        int32_t m0 = t0 >> 31, m1 = j >> 31; /* all ones: inside the band */
                        int32_t a1 = j + jin;
                        a0 = a0 < 0 ? 0 : a0;
                        a0 = a0 > jkt ? jkt : a0, a1 = a1 > jkt ? jkt : a1;
                        uint32_t in = (uint32_t) - (m0 + m1);
                        uint32_t rs =
                            in * c0 +
                            (uint32_t)(((uint64_t)(uint32_t)((a0 & m0) + (a1 & m1)) * ck) >> 32);
                        sum = (es - 255 * in) * rcap + 255 * rs;
                        if (!sum) continue;
                    }
                    uint32_t al = ((sum >> 10) * op + (1u << 21)) >> 22;
                    keep[ix] = (uint8_t)((keep[ix] * (256 - al) + 128) >> 8);
                }
                continue;
            }
            /* a soft cell (small, far, or edge on): the edge's band and the dimple overlap, so each
             * sample is the table's value times the dimple's there */
#define TAP(t, out)                                                                                \
    do {                                                                                           \
        int32_t ja_ = (t) + jin;                                                                   \
        ja_ = ja_ < 0 ? 0 : ja_;                                                                   \
        ja_ = ja_ > jkt ? jkt : ja_;                                                               \
        out += edge[at(t)] * (c0 + (uint32_t)(((uint64_t)(uint32_t)ja_ * ck) >> 32));              \
    } while (0)
            for (int ix = ix0; ix < ix1; ++ix, j += dj, dj += A2, gx += gxs, gy += gys) {
                SPREAD(gx, gy, g);
                if (j - g >= NIQ) continue;
                uint32_t sum = 0;
                TAP(j - g, sum);
                TAP(j, sum);
                TAP(j + g, sum);
                uint32_t al = ((sum >> 10) * op + (1u << 21)) >> 22;
                keep[ix] = (uint8_t)((keep[ix] * (256 - al) + 128) >> 8);
            }
#undef TAP
        }
    }
}

void cells_step_ss9(cells_t *c, uint64_t t_us) {
    uint32_t base, run, acc[16];
    clock_of(t_us, &base, &run);
    memset(c->keep, 255, (size_t)(c->stride * c->h));
    memset(c->dirty, 0, sizeof c->dirty[0] * (size_t)c->h);
    for (int i = 0; i < CELLS_N; ++i) {
        shape_t s;
        shape_of(i, base, run, c->h, 9, &s);
        EACH_ROW(c, s, i) {
            int n = ix1 - ix0;
            if (n > 16) n = 16, ix1 = ix0 + 16;
            memset(acc, 0, sizeof acc[0] * (size_t)n);
            for (int sj = 0; sj < 3; ++sj) {
                int32_t u = (ix0 << 16) + 10923 - x, w = (iy << 16) + 10923 + sj * 21845 - s.y;
                int32_t au = mul16(s.A, u), bw = mul16(s.B, w);
                int32_t j = mul16(au, u) + mul16(bw, u) + mul16(mul16(s.C, w), w) - s.jin;
                int32_t dj = (2 * au + bw) / 3 + s.A / 9, ddj = 2 * s.A / 9;
                for (int k = 0; k < 3 * n; ++k, j += dj, dj += ddj) acc[k / 3] += tap(&s, j);
            }
            uint8_t *keep = c->keep + iy * c->stride + ix0;
            c->dirty[iy] |= blocks(ix0, ix1);
            for (int k = 0; k < n; ++k)
                if (acc[k]) under(keep + k, acc[k], s.op);
        }
    }
}

void cells_ink_init(cells_ink_t *k, uint32_t platter, uint32_t venous, unsigned strength_q16,
                    unsigned catch_q16, unsigned bits_rb, unsigned bits_g) {
    k->flat = 0xff000000 | platter;
    k->gain = (uint32_t)(((uint64_t)strength_q16 << 12) / 255);
    k->gain_catch = (uint32_t)(((uint64_t)k->gain * catch_q16) >> 19);
    k->white = 255u << 16;
    k->bits_rb = bits_rb, k->bits_g = bits_g;
    for (int i = 0; i < 3; ++i) {
        k->p[i] = (int32_t)(platter >> (16 - 8 * i) & 0xff);
        k->dp[i] = k->p[i] - (int32_t)(venous >> (16 - 8 * i) & 0xff);
    }
    for (unsigned keep = 0; keep < 256; ++keep) {
        cells_sums(k, keep, 0, k->ramp[keep]);
        k->ramp[keep][3] = 0;
    }
}

void cells_shade_row_plain(const cells_ink_t *k, uint32_t *dst, const uint8_t *keep,
                           const uint16_t *white, const uint8_t *noise, int sx, int n) {
    for (int x = 0; x < n; ++x) {
        uint32_t v[3];
        unsigned nz = noise[(sx + x) & 63];
        cells_sums(k, keep[x], white ? white[x] : 0, v);
        dst[x] = 0xff000000 | cells_level(v[0], nz, k->bits_rb) << 16 |
                 cells_level(v[1], nz, k->bits_g) << 8 | cells_level(v[2], nz, k->bits_rb);
    }
}

void cells_shade_row(const cells_ink_t *k, uint32_t *dst, const uint8_t *keep, uint32_t dirty,
                     const uint16_t *white, int g0, int g1, const uint8_t *noise, int sx, int n) {
    if (!white) g0 = g1 = 0;
    for (int x = 0, end; x < n; x = end) {
        end = (x | ((1 << CELLS_BLOCK) - 1)) + 1;
        if (end > n) end = n;
        int cell = dirty >> (x >> CELLS_BLOCK) & 1, sheen = x < g1 && end > g0;
        if (!cell && !sheen) {
            for (int i = x; i < end; ++i) dst[i] = k->flat;
        } else if (!cell) {
            for (int i = x; i < end; ++i)
                dst[i] = white[i] ? cells_px(k, 255, white[i], noise[(sx + i) & 63]) : k->flat;
        } else if (!sheen) {
            for (int i = x; i < end; ++i)
                dst[i] = keep[i] == 255 ? k->flat : cells_px(k, keep[i], 0, noise[(sx + i) & 63]);
        } else {
            for (int i = x; i < end; ++i)
                dst[i] = keep[i] == 255 && !white[i]
                             ? k->flat
                             : cells_px(k, keep[i], white[i], noise[(sx + i) & 63]);
        }
    }
}

uint32_t cells_hold_row(const cells_ink_t *k, cells_held_t *row, uint32_t *clean,
                        const uint16_t *white, const uint8_t *noise, int sx, int n) {
    uint32_t sheen = 0;
    for (int x = 0; x < n; ++x) {
        uint32_t wh = white ? white[x] : 0, v[3];
        cells_sums(k, 255, wh, v);
        for (int i = 0; i < 3; ++i) {
            /* 8.8 with the pixel's noise, one level's size, already under it: >> 10 is then the
             * level */
            uint32_t b = (v[i] >> 8) + ((uint32_t)noise[(sx + x) & 63] << 2);
            row[x].b[i] = (uint16_t)(b > 0xffff ? 0xffff : b);
        }
        /* how far a whole cell would pull this pixel toward venous, Q25: less under the sheen's
         * white */
        uint32_t pull =
            (uint32_t)(((uint64_t)(k->gain + ((k->gain_catch * wh) >> 13)) * (65536 - wh)) >> 19);
        row[x].pull = (uint16_t)(pull > 0xffff ? 0xffff : pull);
        /* with no cell on it: the sheen dithered, or the platter flat */
        clean[x] = wh ? 0xff000000 | (uint32_t)(row[x].b[0] >> 10) << 18 |
                            (uint32_t)(row[x].b[1] >> 10) << 10 | (uint32_t)(row[x].b[2] >> 10) << 2
                      : k->flat;
        if (wh) sheen |= 1u << (x >> CELLS_BLOCK);
    }
    return sheen;
}

void cells_draw_row(const cells_ink_t *k, uint32_t *restrict dst, const cells_held_t *restrict row,
                    const uint32_t *restrict clean, const uint8_t *restrict keep, uint32_t dirty,
                    int n) {
    const int32_t d0 = k->dp[0], d1 = k->dp[1], d2 = k->dp[2];
    for (int x = 0, end; x < n; x = end) {
        end = (x | ((1 << CELLS_BLOCK) - 1)) + 1;
        if (end > n) end = n;
        if (!(dirty >> ((unsigned)x >> CELLS_BLOCK) & 1)) {
            memcpy(dst + x, clean + x, sizeof *dst * (size_t)(end - x));
            continue;
        }
        for (int i = x; i < end; ++i) {
            unsigned kp = keep[i];
            if (kp == 255) {
                dst[i] = clean[i];
                continue;
            }
            int32_t a = (int32_t)((255 - kp) * row[i].pull); /* Q25 */
            uint32_t r = (uint32_t)(row[i].b[0] - ((d0 * a) >> 17)) >> 10,
                     g = (uint32_t)(row[i].b[1] - ((d1 * a) >> 17)) >> 10,
                     b = (uint32_t)(row[i].b[2] - ((d2 * a) >> 17)) >> 10;
            dst[i] = 0xff000000 | r << 18 | g << 10 | b << 2;
        }
    }
}
