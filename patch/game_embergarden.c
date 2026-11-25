/* Ember Garden (docs/games-engine.md#ember-garden): a far volcano's ash cloud rains embers on a
 * kitchen garden; a brass sprinkler on the lawn puts them out with spurts of water. Embers fall on
 * slow sine paths in three sizes, each a flame of four frames; a cinder hit cracks into two small
 * embers; an ember that reaches the ground sets the nearest growing bed on its side alight, and the
 * fourth bed lost on one side ends the game. Waves start sparse and slow and grow denser and
 * faster, the sky holds only so many embers a wave, nothing cracks before wave G_CRACK, the light
 * turns from dawn to night across the waves, and every tenth is a lull of slow cinders worth
 * double. The sky is alive under it all: clouds drift, the ash billows, the crater glows and the
 * lava runs (sky_turn), one of them moving a step.
 *
 * The scene is the art file's (the design's sky-defense/src/export_retune.py), read once when the
 * game opens: a byte a pixel into a palette a band of rows, four colours an entry (dawn, day,
 * dusk, night), so a time of day is a palette and the light's turn is two palettes mixed, painted
 * a strip of rows a frame. Each step marks the boxes that changed and joins them into at most
 * ENG_RECTS; each is painted as the scene restored and what lies over it drawn again. */
#include "engine.h"
#include "game_embergarden_art.h"
#include "peq.h"
#include "stock.h" /* Q2POD_DEV */

#define G_ART "/usr/share/q2games/embergarden.bin"
#define G_MAGIC 0x31474d45 /* "EMG1" */
#define G_EMBERS 12        /* alive at once: the brief's 24 cut for the frame's cost */
#define G_SHOTS 3          /* spurts in flight */
#define G_PUFFS 3          /* steam puffs */
#define G_CINDERS 6        /* cinders alive at once: more are spawned medium (the frame's cost) */
#define G_LICK 6           /* steps a frame of an ember's flame shows */
#define G_BOXES 64         /* a step's changed boxes before they are joined */
#define G_PX 187           /* the sprinkler's pivot, (187.5, 270) */
#define G_PY 270
#define G_GROUND 258      /* an ember reaching this row has landed */
#define G_BED_Y 252       /* a bed's top */
#define G_AIM_LO (-17000) /* the aim, hundredths of a degree, -90 straight up */
#define G_AIM_HI (-1000)
#define G_WHEEL 120   /* hundredths of a degree a wheel position */
#define G_KEY 200     /* a step while Previous or Next is held */
#define G_REPEAT 12   /* steps between spurts while Center is held */
#define G_SPEED FX(6) /* a spurt's lead drop, a step */
#define G_HIT 72      /* its radius, 1/16 px (4.5) */
#define G_SWAY 18     /* an ember's widest sway, px each way, and its period in steps */
#define G_PERIOD 180
#define G_BURN 90      /* steps a bed burns before it is charred */
#define G_FLAME 8      /* steps a flame frame shows, each bed's a step apart from the next's */
#define G_BUDGET 14000 /* pixels of boxes a step may mark with its extras (extras) */
#define G_CATCH 8      /* steps from a landing to the bed alight */
#define G_HOLD 45      /* the wave banner's steps whole, then G_FADE fading */
#define G_FADE 15
#define G_STOP 3    /* hit-stop on a cinder's split */
#define G_CRACK 4   /* the first wave with cinders: nothing cracks before it */
#define G_CALM 90   /* steps from a crack to the next ember at least */
#define G_BREATH 40 /* and from a full sky's first gap */
#define G_STRIP 8   /* rows a step while the light turns */
#define G_TURN 32   /* passes of the strips from one light to the next */
#define G_DROP 14   /* steps the result card takes to drop in */
#define G_FLASH 120 /* steps between two strikes of lightning at least */
#define G_STRIKE 4  /* lightning strikes only over this many embers or fewer (the frame's cost) */
#define H_BANDS (320 / EG_BAND)

enum { START, PLAY, LOST };
enum { GROWING, CAUGHT, BURNING, CHARRED };
enum { DAWN, DAY, DUSK, NIGHT };

struct ember {
    fx bx, y, vx, vy; /* its path's middle, its height, a split's kick and its fall a step */
    int phase, amp, size, alive;
    int f; /* its flame's frame, 0 to 3 */
};
struct shot {
    fx x, y, ux, uy; /* the lead drop and its heading */
    int run, alive;  /* px travelled */
};
struct puff {
    int x, y, f, t, size, pts, alive; /* its frame, 0 to 4, and the steps it has shown */
};

/* The sky's moving things (sky_turn): each a mask of the art's, drawn in one colour. */
enum { DRIFT, BOB, RISE, PULSE, MARCH };
enum { C_CLOUD, C_PLUME, C_GLOW, C_LAVA };
#define VX 214 /* the crater */
#define VY 143
static const struct sky {
    unsigned char kind, part, colour, p; /* p: a cloud's turns a pixel, else its phase */
    short x, y;
} SKY[] = {
    { DRIFT, EG_CLOUD0, C_CLOUD, 1, 40, 150 },  { BOB, EG_BILLOW, C_PLUME, 0, 52, 28 },
    { MARCH, EG_LAVA0, C_LAVA, 0, VX, VY },     { RISE, EG_BILLOW, C_PLUME, 0, 0, 0 },
    { DRIFT, EG_CLOUD1, C_CLOUD, 2, 118, 168 }, { BOB, EG_BILLOW, C_PLUME, 77, 122, 30 },
    { MARCH, EG_LAVA1, C_LAVA, 1, VX, VY },     { BOB, EG_BILLOW, C_PLUME, 154, 196, 30 },
    { DRIFT, EG_CLOUD2, C_CLOUD, 1, 300, 142 }, { PULSE, EG_VGLOW, C_GLOW, 0, VX, VY },
    { MARCH, EG_LAVA2, C_LAVA, 2, VX, VY },     { RISE, EG_BILLOW, C_PLUME, 128, 0, 0 },
    { DRIFT, EG_CLOUD3, C_CLOUD, 2, 350, 170 }, { BOB, EG_BILLOW, C_PLUME, 231, 268, 28 },
    { BOB, EG_BILLOW, C_PLUME, 52, 338, 28 },
};
#define SKY_N (int)(sizeof SKY / sizeof *SKY)

struct eg {
    unsigned art[EG_ART_BYTES / 4];           /* the art file, whole */
    unsigned pal[H_BANDS][EG_COLOURS];        /* the palette now, 0xffRRGGBB */
    unsigned char grow[8][EG_CH_H * EG_CH_W]; /* each bed's box of the scene as it opened */
    int ok;                                   /* the art was read */
    struct ember em[G_EMBERS];
    struct shot sh[G_SHOTS];
    struct puff pf[G_PUFFS];
    struct {
        short x, y, a; /* where each of SKY's is drawn, and how strongly (of 256) */
    } sky[SKY_N];
    int mode, aim, recoil, spray, repeat, stop, h;
    int tick; /* the steps since the game opened: every moving picture's clock */
    int bed[8], bed_t[8], land_x, land_t, last[2], blink;
    unsigned char flame[8]; /* each burning bed's flame frame */
    int wave, lull, left, spawn, gap, banner, cleared;
    int light, from, k, strip,
        dim; /* the time of day, the one before it, the turn's pass, its strip */
    int bolt, bolt_x, bolt_seed, bolt_wait; /* lightning: its step, place and shape */
    int card;                               /* the result card's place */
    int seq, runs, wait;                    /* a run of one sound */
    unsigned score, seed;
    int boxes, box[G_BOXES][4];
    struct {
        unsigned magic, best, wave, sum;
    } save;
    unsigned saved;
};

/* ---- the art file ---- */

typedef unsigned quad __attribute__((may_alias)); /* four of the scene's bytes read at once */

struct part { /* a row of the file's table (export.py's Pack) */
    unsigned at;
    unsigned short w, h;
    short ax, ay; /* the anchor: a sprite's centre, a glyph's pen at its middle row */
    unsigned char kind, pad;
    unsigned short adv; /* a glyph's advance */
};
static const struct part *part(const struct eg *g, int id) {
    return (const struct part *)((const char *)g->art + 16) + id;
}
static const void *data(const struct eg *g, int id) {
    return (const char *)g->art + part(g, id)->at;
}
static unsigned char *scene_idx(struct eg *g) {
    return (unsigned char *)g->art + part(g, EG_SCENE)->at;
}

/* The art file, read whole into the state; anything amiss (missing, short, another layout) and the
 * game says so and leaves. */
static int load(struct eg *g) {
    if (!blob_io(G_ART, G_ART, g->art, EG_ART_BYTES, 0) || g->art[0] != EG_ART_MAGIC ||
        g->art[1] != EG_ART_BYTES || g->art[2] != EG_PARTS || g->art[3] != EG_BAND)
        return 0;
    for (int k = 0; k < EG_PARTS; ++k) {
        const struct part *p = part(g, k);
        unsigned bytes = p->kind == 1      ? 4u * p->w * p->h + 2u * p->h
                         : p->kind == 2    ? (p->w + 1u) / 2 * p->h
                         : k == EG_SCENE   ? ENG_W * 320u
                         : k == EG_PALETTE ? 12u * H_BANDS * EG_COLOURS
                                           : 8u * EG_CH_W * EG_CH_H;
        if (p->at < 16 + 16u * EG_PARTS || p->at > EG_ART_BYTES || bytes > EG_ART_BYTES - p->at)
            return 0;
    }
    return part(g, EG_SCENE)->w == ENG_W && part(g, EG_SCENE)->h == 320;
}

/* ---- colours ---- */

static const unsigned char AMB[4][3] = { /* the light on lit things, 256 at one: sky.py TOD amb */
                                         { 251, 220, 225 },
                                         { 255, 255, 255 },
                                         { 246, 179, 159 },
                                         { 102, 120, 184 }
};
#define WATER 0x38e8ff
#define WATER_KEY 0x0a2e78
#define EMBER 0xff7a1a
#define TINT 0x0a0618 /* the result card's dim: 55 percent of the way from it */

static unsigned mixc(unsigned a, unsigned b, int k, int n) {
    unsigned out = 0;
    for (int sh = 0; sh < 24; sh += 8) {
        int x = (int)(a >> sh & 255), y = (int)(b >> sh & 255);
        out |= (unsigned)(x + (y - x) * k / n) << sh;
    }
    return out;
}
static unsigned dimmed(unsigned c) { return mixc(TINT, c, 141, 256); }

/* rgb as the time of day lights it (sky.py L): each channel times the ambient, the turn's mix. */
static unsigned lit(const struct eg *g, unsigned rgb) {
    unsigned out = 0;
    for (int c = 0; c < 3; ++c) {
        int a = AMB[g->from][c] + (AMB[g->light][c] - AMB[g->from][c]) * g->k / G_TURN;
        out |= (unsigned)((int)(rgb >> (16 - 8 * c) & 255) * a >> 8) << (16 - 8 * c);
    }
    return g->dim ? dimmed(out) : out;
}

/* The palette now: each entry the turn's mix of its two times of day, dimmed under the card. */
static void palette(struct eg *g) {
    if (!g->ok) return;
    const unsigned char *src = data(g, EG_PALETTE), *a = src + g->from * 3 * H_BANDS * EG_COLOURS,
                        *b = src + g->light * 3 * H_BANDS * EG_COLOURS;
    for (int n = 0; n < H_BANDS * EG_COLOURS; ++n, a += 3, b += 3) {
        unsigned c = 0;
        for (int ch = 0; ch < 3; ++ch)
            c = c << 8 | (unsigned)(a[ch] + (b[ch] - a[ch]) * g->k / G_TURN);
        g->pal[0][n] = 0xff000000 | (g->dim ? dimmed(c) : c);
    }
}

/* ---- drawing ---- */

static inline unsigned *at(const struct eng_surface *s, int x, int y) {
    return s->px + (y + s->oy) * s->stride + x + s->ox;
}
static inline __attribute__((always_inline)) unsigned blend(unsigned dst, unsigned rgb,
                                                            unsigned a) { /* a of 256 */
    unsigned rb = ((rgb & 0xff00ff) * a + (dst & 0xff00ff) * (256 - a)) >> 8 & 0xff00ff;
    unsigned gg = ((rgb & 0xff00) * a + (dst & 0xff00) * (256 - a)) >> 8 & 0xff00;
    return 0xff000000 | rb | gg;
}

static unsigned isqrt(unsigned n) {
    unsigned r = 0, bit = 1u << 30;
    while (bit > n) bit >>= 2;
    for (; bit; bit >>= 2)
        if (n >= r + bit)
            n -= r + bit, r = (r >> 1) + bit;
        else
            r >>= 1;
    return r;
}

/* A disc, its centre and radius in 1/16 px, its edge smoothed over a pixel (linear in the squared
 * distance across it), alpha of 256. */
static void disc(struct eng_surface *s, int x16, int y16, int r16, unsigned rgb, int alpha) {
    int x = (x16 - r16 - 8) >> 4, y = (y16 - r16 - 8) >> 4, d = ((r16 + 8) >> 3) + 2, c[4];
    if (r16 <= 0 || alpha <= 0 || !eng_clip(s, x, y, d, d, c)) return;
    int in = r16 > 8 ? r16 - 8 : 0, out = r16 + 8, den = out * out - in * in;
    for (int j = c[1]; j < c[3]; ++j) {
        int dy = j * 16 + 8 - y16;
        unsigned *p = at(s, c[0], j);
        for (int i = c[0]; i < c[2]; ++i, ++p) {
            int dx = i * 16 + 8 - x16, d2 = dx * dx + dy * dy;
            unsigned a;
            if (d2 >= out * out) continue;
            /* the edge, a pixel wide, by the squared distance: no root a pixel */
            a = d2 <= in * in ? 256 : (unsigned)((out * out - d2) << 8) / (unsigned)den;
            a = a * (unsigned)alpha >> 8;
            *p = a >= 256 ? 0xff000000 | rgb : blend(*p, rgb, a);
        }
    }
}

static int floor_div(int a, int b) { return a / b - (a % b != 0 && (a < 0) != (b < 0)); }
/* Narrows [*a, *b) to the n where lo < f + k n < hi. */
static void within(int f, int k, int lo, int hi, int *a, int *b) {
    if (k < 0) {
        int t = -lo;
        lo = -hi, hi = t, f = -f, k = -k;
    }
    if (!k) {
        if (f <= lo || f >= hi) *b = *a;
        return;
    }
    int n0 = floor_div(lo - f, k) + 1, n1 = -floor_div(f - hi, k);
    *a = n0 > *a ? n0 : *a, *b = n1 < *b ? n1 : *b;
}

/* A straight stroke with square ends (Pillow's line), from x, y (1/16 px) along the unit ux, uy
 * (16.16) for len, wide w (both 1/16 px); its edges smoothed, alpha of 256. */
static void bar(struct eng_surface *s, int x16, int y16, fx ux, fx uy, int len, int w, unsigned rgb,
                int alpha) {
    int ex = x16 + (ux * len >> 16),
        ey = y16 + (uy * len >> 16); /* all in int: |dx| * 65536 < 2^31 */
    int lo_x = (x16 < ex ? x16 : ex) - w, hi_x = (x16 > ex ? x16 : ex) + w;
    int lo_y = (y16 < ey ? y16 : ey) - w, hi_y = (y16 > ey ? y16 : ey) + w, c[4];
    if (!eng_clip(s, lo_x >> 4, lo_y >> 4, (hi_x >> 4) - (lo_x >> 4) + 2,
                  (hi_y >> 4) - (lo_y >> 4) + 2, c))
        return;
    int half = w / 2;
    for (int j = c[1]; j < c[3]; ++j) {
        int dy = j * 16 + 8 - y16, dx = c[0] * 16 + 8 - x16;
        int along = dx * ux + dy * uy,
            across = dy * ux - dx * uy; /* 16.16 of 1/16 px, a pixel apart */
        int n0 = 0, n1 = c[2] - c[0];   /* the columns it can touch, from the two edges' lines */
        within(across, -16 * uy, -(half + 8) << 16, (half + 8) << 16, &n0, &n1);
        within(along, 16 * ux, -(8 << 16), (len + 8) << 16, &n0, &n1);
        along += n0 * 16 * ux, across -= n0 * 16 * uy;
        unsigned *p = at(s, c[0] + n0, j);
        for (int i = c[0] + n0; i < c[0] + n1; ++i, ++p, along += 16 * ux, across -= 16 * uy) {
            int t = along >> 16, d = across >> 16;
            int cw = half - (d < 0 ? -d : d) + 8, ct = t + 8, ce = len - t + 8;
            if (cw <= 0 || ct <= 0 || ce <= 0) continue;
            cw = cw > 16 ? 16 : cw, ct = ct > 16 ? 16 : ct, ce = ce > 16 ? 16 : ce;
            unsigned a = (unsigned)(cw * ct * ce) * (unsigned)alpha >> 12;
            *p = a >= 256 ? 0xff000000 | rgb : blend(*p, rgb, a);
        }
    }
}

/* A premultiplied sprite with its anchor at x, y, each row only between its inked columns; lean
 * shifts each row above the anchor that many 256ths of a pixel a row, the way a tail trails a
 * moving ember. */
static void sprite(const struct eg *g, struct eng_surface *s, int id, int x, int y, int lean) {
    const struct part *pt = part(g, id);
    const unsigned *src = data(g, id);
    const unsigned char *span = (const unsigned char *)(src + pt->w * pt->h);
    int top = y - pt->ay, j0 = s->y0 - top, j1 = s->y1 - top;
    j0 = j0 < 0 ? 0 : j0, j1 = j1 > pt->h ? pt->h : j1;
    for (int j = j0; j < j1; ++j) {
        int left = x - pt->ax + (j < pt->ay ? lean * (pt->ay - j) / 256 : 0);
        int i0 = s->x0 - left, i1 = s->x1 - left;
        i0 = i0 < span[2 * j] ? span[2 * j] : i0, i1 = i1 > span[2 * j + 1] ? span[2 * j + 1] : i1;
        const unsigned *q = src + j * pt->w + i0;
        unsigned *p = at(s, left + i0, top + j);
        for (int i = i0; i < i1; ++i, ++p) {
            unsigned v = *q++, a = v >> 24;
            if (!a) continue;
            if (a == 255) {
                *p = v;
                continue;
            }
            unsigned d = *p, k = 256 - a;
            *p = 0xff000000 | ((v & 0xffffff) + (((d & 0xff00ff) * k >> 8) & 0xff00ff) +
                               (((d & 0xff00) * k >> 8) & 0xff00));
        }
    }
}

/* A mask of 4 bits a pixel in rgb, its pen at x on its middle row y, alpha of 256. */
static void glyph(const struct eg *g, struct eng_surface *s, int id, int x, int y, unsigned rgb,
                  int alpha) {
    const struct part *pt = part(g, id);
    const unsigned char *src = data(g, id);
    int left = x - pt->ax, top = y - pt->ay, stride = (pt->w + 1) / 2, c[4];
    unsigned lut[16]; /* each of the mask's 16 levels at this alpha, of 256 */
    if (alpha <= 0 || !eng_clip(s, left, top, pt->w, pt->h, c)) return;
    for (int n = 0; n < 16; ++n) lut[n] = (unsigned)(n * 17 * alpha) >> 8, lut[n] += lut[n] >> 7;
#define PUT(p, a) (*(p) = (a) >= 256 ? 0xff000000 | rgb : blend(*(p), rgb, (a)))
    for (int j = c[1]; j < c[3]; ++j) {
        const unsigned char *q = src + (j - top) * stride;
        unsigned *p = at(s, c[0], j), a;
        int u = c[0] - left, end = c[2] - left;
        if (u & 1 && (a = lut[q[u >> 1] & 15])) PUT(p, a);
        p += u & 1, u += u & 1;
        for (; u + 1 < end; u += 2, p += 2) { /* two pixels a byte: an empty one skipped whole */
            unsigned b = q[u >> 1];
            if (!b) continue;
            if ((a = lut[b >> 4])) PUT(p, a);
            if ((a = lut[b & 15])) PUT(p + 1, a);
        }
        if (u < end && (a = lut[q[u >> 1] >> 4])) PUT(p, a);
    }
#undef PUT
}

/* A word's mask: from x (0), its ink centred on x (1) or ending at x (2); line: an outlined word,
 * its outline (the next part) drawn first in that colour. */
static void word(const struct eg *g, struct eng_surface *s, int id, int x, int y, int align,
                 unsigned rgb, int line, unsigned line_rgb, int alpha) {
    const struct part *pt = part(g, id);
    if (align) x = x - (align == 1 ? pt->w / 2 : pt->w) + pt->ax;
    if (line) glyph(g, s, id + 1, x, y, line_rgb, alpha);
    glyph(g, s, id, x, y, rgb, alpha);
}

/* v in a set of digit glyphs (first: its 0; step: 2 for an outlined set), with commas where the
 * set has one, aligned as word does by the advances. */
static void numeral(const struct eg *g, struct eng_surface *s, int first, int step, int comma,
                    unsigned v, int x, int y, int align, unsigned rgb, unsigned line_rgb,
                    int alpha) {
    int ids[16], n = 0, w = 0;
    do {
        if (comma && n % 4 == 3) ids[n++] = first + 10 * step;
        ids[n++] = first + (int)(v % 10) * step;
    } while ((v /= 10) && n < 14);
    for (int k = 0; k < n; ++k) w += part(g, ids[k])->adv;
    x -= align == 1 ? w / 2 : align == 2 ? w : 0;
    while (n--) {
        if (step == 2) glyph(g, s, ids[n] + 1, x, y, line_rgb, alpha);
        glyph(g, s, ids[n], x, y, rgb, alpha);
        x += part(g, ids[n])->adv;
    }
}

/* Columns x0 to x1 of a row of the scene: a palette entry a byte. */
static void row_of(const unsigned *pal, const unsigned char *q, unsigned *p, int n) {
    const unsigned char *end = q + n;
    while (q < end && (unsigned)q & 3) *p++ = pal[*q++];
    for (; q + 8 <= end; q += 8, p += 8) { /* eight indexes in two loads */
        unsigned w = *(const quad *)q, v = *(const quad *)(q + 4);
        p[0] = pal[w & 255], p[1] = pal[w >> 8 & 255], p[2] = pal[w >> 16 & 255],
        p[3] = pal[w >> 24];
        p[4] = pal[v & 255], p[5] = pal[v >> 8 & 255], p[6] = pal[v >> 16 & 255],
        p[7] = pal[v >> 24];
    }
    while (q < end) *p++ = pal[*q++];
}

/* The scene restored under the rect being painted, but where the card's body (rows card0 to card1,
 * columns CARD_X to CARD_X + 150) is about to cover it. */
#define CARD_X 112
static void scene(const struct eg *g, struct eng_surface *s, int card0, int card1) {
    const unsigned char *idx = data(g, EG_SCENE);
    for (int y = s->y0; y < s->y1; ++y) {
        const unsigned *pal = g->pal[y / EG_BAND];
        const unsigned char *q = idx + y * ENG_W;
        if (y < card0 || y >= card1) {
            row_of(pal, q + s->x0, at(s, s->x0, y), s->x1 - s->x0);
            continue;
        }
        if (s->x0 < CARD_X)
            row_of(pal, q + s->x0, at(s, s->x0, y), (s->x1 < CARD_X ? s->x1 : CARD_X) - s->x0);
        int x = s->x0 > CARD_X + 150 ? s->x0 : CARD_X + 150;
        if (x < s->x1) row_of(pal, q + x, at(s, x, y), s->x1 - x);
    }
}

/* ---- the things in the scene ---- */

static const int BED_X[8] = { 44, 79, 114, 149, 226, 261, 296, 331 };
static const int CH_X[8] = EG_CH_X;
static const int R16[3] = { 48, 74, 112 }; /* an ember's radius by size, 1/16 px: 3, 4.6, 7 */
static const short CARD_Y[G_DROP + 1] = { -150, -79, -20, 28,  66,  95,  116, 131,
                                          140,  145, 146, 145, 143, 141, 140 }; /* back-out */

static int angle(int aim) { return (aim + 36000) * 1024 / 36000 & 1023; } /* 1024 a turn */

static int ember_x(const struct ember *e) {
    return FX_INT(e->bx + e->amp * eng_sin(e->phase * 1024 / G_PERIOD));
}
/* How far its tail leans, 256ths of a pixel a row above it: the way it moves across (sky.py ember's
 * sway, 0.6 at the sine's steepest), over its radius. */
static int ember_lean(const struct ember *e) {
    int sway =
        (e->amp * eng_cos(e->phase * 1024 / G_PERIOD) / G_SWAY * 154 >> 16) + (e->vx * 96 >> 16);
    return -sway * 83 * 16 / (100 * R16[e->size]);
}
static int ember_part(const struct ember *e) { return EG_FLAME0_0 + 4 * e->size + e->f; }
static int shots(const struct eg *g) {
    int n = 0;
    for (int k = 0; k < G_SHOTS; ++k) n += g->sh[k].alive;
    return n;
}

/* The bolt of lightning seed draws: eight points down the ash column and a branch of four. */
static void bolt_shape(int seed, int x0, int *pts, int *br) {
    unsigned s = (unsigned)seed | 1;
    int x = x0 * 16, y = 46 * 16;
    for (int k = 0; k < 8; ++k) {
        pts[2 * k] = x, pts[2 * k + 1] = y;
        s ^= s << 13, s ^= s >> 17, s ^= s << 5;
        x += (int)(s % 272) - 112, y += 112 + (int)(s >> 8) % 80;
    }
    br[0] = pts[6], br[1] = pts[7];
    for (int k = 1; k < 4; ++k) {
        s ^= s << 13, s ^= s >> 17, s ^= s << 5;
        br[2 * k] = pts[6] - 96 - (k - 1) * 80 + (int)(s % 64) - 32,
               br[2 * k + 1] = pts[7] + 80 + (k - 1) * 96;
    }
}
static void bolt_line(struct eng_surface *s, const int *p, int n, int w, unsigned rgb, int alpha) {
    for (int k = 0; k + 1 < n; ++k) {
        int x0 = p[2 * k] >> 4, y0 = p[2 * k + 1] >> 4, x1 = p[2 * k + 2] >> 4,
            y1 = p[2 * k + 3] >> 4;
        if ((x0 < x1 ? x1 : x0) + 4 < s->x0 || (x0 < x1 ? x0 : x1) - 4 >= s->x1 || y1 + 4 < s->y0 ||
            y0 - 4 >= s->y1)
            continue; /* this segment is not in the rect being painted */
        int dx = p[2 * k + 2] - p[2 * k], dy = p[2 * k + 3] - p[2 * k + 1];
        int len = (int)isqrt((unsigned)(dx * dx + dy * dy));
        if (len)
            bar(s, p[2 * k], p[2 * k + 1], dx * 65536 / len, dy * 65536 / len, len + 8, w, rgb,
                alpha);
    }
}

static void draw_bolt(const struct eg *g, struct eng_surface *s) {
    int pts[16], br[8], f = g->bolt - 1, flash = f < 2 ? 256 : 141;
    if (f >= 4 || s->y0 >= 150) return; /* gone, or below the column */
    bolt_shape(g->bolt_seed, g->bolt_x, pts, br);
    bolt_line(s, pts, 8, 54, 0xb8a0ff,
              120 * flash >> 8); /* the sheath on the bolt, not its branch */
    bolt_line(s, pts, 8, 21, 0xffffff, flash + 51 > 256 ? 256 : flash + 51);
    bolt_line(s, br, 4, 14, 0xf0e8ff, flash);
}

/* The sky's moving things over the scene: the clouds in the day's light, the billows in the plume's
 * colour as the scene has it at EG_PLUME_X, EG_PLUME_Y, the crater's glow and the lava's dashes.
 * Like the scene, none of it is painted where the card's body (rows card0 to card1) will cover it:
 * the rect is painted as its rows above the card, those below, and the columns to each side. */
static void draw_sky(const struct eg *g, struct eng_surface *s, int card0, int card1) {
    unsigned col[4] = { lit(g, 0xffffff),
                        g->pal[EG_PLUME_Y / EG_BAND][((const unsigned char *)data(
                            g, EG_SCENE))[EG_PLUME_Y * ENG_W + EG_PLUME_X]] &
                            0xffffff,
                        g->dim ? dimmed(EMBER) : EMBER, g->dim ? dimmed(0xffe27a) : 0xffe27a };
    struct eng_surface t = *s;
    int y0 = s->y0 > card0 ? s->y0 : card0, y1 = s->y1 < card1 ? s->y1 : card1;
    int cut[4][4] = { { s->x0, s->y0, s->x1, s->y1 < card0 ? s->y1 : card0 },
                      { s->x0, s->y0 > card1 ? s->y0 : card1, s->x1, s->y1 },
                      { s->x0, y0, s->x1 < CARD_X ? s->x1 : CARD_X, y1 },
                      { s->x0 > CARD_X + 150 ? s->x0 : CARD_X + 150, y0, s->x1, y1 } };
    for (int n = 0; n < 4; ++n) {
        t.x0 = cut[n][0], t.y0 = cut[n][1], t.x1 = cut[n][2], t.y1 = cut[n][3];
        if (t.x0 >= t.x1 || t.y0 >= t.y1 || t.y0 >= 205)
            continue; /* all of it is above the hedge */
        for (int i = 0; i < SKY_N; ++i)
            glyph(g, &t, SKY[i].part, g->sky[i].x, g->sky[i].y, col[SKY[i].colour], g->sky[i].a);
    }
}

/* The sprinkler's head, turned to the aim and kicked back after a spurt, and its collar's drops:
 * sky.py sprinkler, the tripod and riser being the scene's. */
static void draw_sprinkler(const struct eg *g, struct eng_surface *s) {
    int a = angle(g->aim), rc = g->recoil;
    fx ux = eng_cos(a), uy = eng_sin(a);
    int kick = 56 * rc * rc * rc / 512, bx = 3000 - (ux * kick >> 16),
        by = 4320 - (uy * kick >> 16);
#define ALONG(d, n) (b##d + (u##d * (n) >> 16))
    unsigned edge = lit(g, 0x7a5414);
    bar(s, ALONG(x, -80), ALONG(y, -80), ux, uy, 400, 144, edge, 256);
    bar(s, ALONG(x, -80), ALONG(y, -80), ux, uy, 400, 102, lit(g, 0xd9a632), 256);
    bar(s, ALONG(x, 32) - (uy * 26 >> 16), ALONG(y, 32) + (ux * 26 >> 16), ux, uy, 240, 21,
        lit(g, 0xffe08a), 256);
    bar(s, ALONG(x, 272), ALONG(y, 272), ux, uy, 80, 166, edge, 256);
    bar(s, ALONG(x, 282), ALONG(y, 282), ux, uy, 61, 125, lit(g, 0xf2c452), 256);
    /* the impact arm: a sprung flap that flips out on a spurt */
    int sw = 128 + rc * 230 / 8;                           /* 0.5 to 1.4, of 256 */
    fx wx = ux - (uy * sw >> 8), wy = uy + (ux * sw >> 8); /* along the aim, then out */
    int mag = (int)isqrt((unsigned)((wx >> 4) * (wx >> 4) + (wy >> 4) * (wy >> 4)));
    int ax = ALONG(x, 144), ay = ALONG(y, 144);
    if (mag) {
        bar(s, ax, ay, wx * 4096 / mag, wy * 4096 / mag, 144 * mag / 4096, 35, lit(g, 0x8a8e96),
            256);
        disc(s, ax + (wx * 144 >> 16), ay + (wy * 144 >> 16), 42, lit(g, 0xc9ced8), 256);
    }
#undef ALONG
    disc(s, bx, by, 122, edge, 256);
    disc(s, bx, by, 98, lit(g, 0xe0b040), 256);
    disc(s, bx - 29, by - 29, 34, lit(g, 0xffefa8), 256);
    disc(s, bx, by, 26, edge, 256);
    if (g->mode != LOST)
        for (int k = 0, left = G_SHOTS - shots(g); k < 3; ++k)
            disc(s, 2920 + 80 * k, 4520, 27, k < left ? WATER : lit(g, 0x2a3a4a), 256);
}

/* The aim: a dotted arc of the sweep and a dotted sight line along it. */
static void draw_aim(const struct eg *g, struct eng_surface *s) {
    if (s->y0 > G_PY || s->y1 < G_PY - 80) return;
    for (int k = 0; k < 17; ++k) {
        int a = (190 + 10 * k) * 1024 / 360;
        disc(s, 3000 + (eng_cos(a) * 31 >> 12), 4320 + (eng_sin(a) * 31 >> 12), 13, 0xffffff, 110);
    }
    int a = angle(g->aim);
    for (int k = 0; k < 5; ++k)
        disc(s, 3000 + (eng_cos(a) * (40 + 9 * k) >> 12), 4320 + (eng_sin(a) * (40 + 9 * k) >> 12),
             18 - 2 * k, 0xffffff, 190 - 30 * k);
}

/* A spurt: its lead drop and a trail that sags behind it (sky.py burst), each drop on a navy
 * keyline; the trail only as far as the spurt has come. */
static const short TRAIL[4][2] = {
    { 120, 48 }, { 224, 40 }, { 320, 32 }, { 408, 24 }
}; /* 1/16 px */
static void draw_shot(struct eng_surface *s, const struct shot *h) {
    int x = h->x >> 12, y = h->y >> 12, ux = h->ux >> 12, uy = h->uy >> 12, n = 0, tx[4], ty[4];
    for (; n < 4 && TRAIL[n][0] <= h->run * 16; ++n) {
        int d = TRAIL[n][0];
        tx[n] = x - ux * d / 16, ty[n] = y - uy * d / 16 + 11 * d * d / 16000;
    }
    for (int k = n; k--;) disc(s, tx[k], ty[k], TRAIL[k][1] + 16, WATER_KEY, 256);
    disc(s, x, y, 86, WATER_KEY, 256);
    for (int k = n; k--;) disc(s, tx[k], ty[k], TRAIL[k][1], WATER, 256);
    disc(s, x, y, 70, WATER, 256);
    disc(s, x - ux * 54 / 16, y - uy * 54 / 16 + 3, 54, WATER, 256);
    disc(s, x + ux * 13 / 16 - 13, y + uy * 13 / 16 - 14, 35, 0xffffff, 256);
    if (n) disc(s, tx[0] - 8, ty[0] - 10, 18, 0xffffff, 256);
}

/* A hit's steam (sky.py steam): a white flash three steps, then a puff that rises and thins over
 * four frames (the art's, a medium ember's), water flecks and the dead cinder for the first two,
 * and the points rising. */
static const short FLECK[7][2] = { { 921, 389 },   { 267, 964 },  { -589, 808 }, { -999, 42 },
                                   { -654, -757 }, { 187, -982 }, { 886, -465 } };
static const unsigned char PUFF_STEPS[5] = { 3, 5, 4, 5, 4 }; /* each frame's steps: 21 in all */
static void draw_puff(const struct eg *g, struct eng_surface *s, const struct puff *p) {
    int f = p->f, r0 = (5 + p->size * 2 + (p->size == 2)) * 16;
    int x = p->x * 16, y = p->y * 16;
    if (!f) {
        disc(s, x, y, r0 + 48, 0xffffff, 256);
        disc(s, x, y, r0, 0xfff2b0, 256);
    } else
        sprite(g, s, EG_PUFF1 + f - 1, p->x, p->y, 0);
    if (f < 3) {
        if (f) disc(s, x, y + 16 + f * 32, r0 * 36 / 100, 0x5a3a30, 256);
        for (int i = 0; i < 7; ++i) {
            int d = r0 + 64 + f * 80;
            disc(s, x + FLECK[i][0] * d / 1000, y + FLECK[i][1] * d * 8 / 10000 + f * f * 13,
                 22 - 4 * f, WATER, 256);
        }
    }
    if (f) {
        int px = p->x - r0 / 16 - 9, py = p->y + r0 / 16 + 3 - f * 5 / 2;
        int w = part(g, EG_POP_PLUS)->adv + part(g, EG_POP_0 + 2 * (p->pts % 10))->adv;
        glyph(g, s, EG_POP_PLUS_LINE, px - w / 2, py, 0x3a1e10, f < 4 ? 256 : 140);
        glyph(g, s, EG_POP_PLUS, px - w / 2, py, 0xffe27a, f < 4 ? 256 : 140);
        numeral(g, s, EG_POP_0, 2, 0, (unsigned)p->pts, px - w / 2 + part(g, EG_POP_PLUS)->adv, py,
                0, 0xffe27a, 0x3a1e10, f < 4 ? 256 : 140);
    }
}

/* An ember reaching the ground (sky.py landing): eight sparks and a flash, four frames. */
static void draw_sparks(struct eng_surface *s, int x, int f) {
    for (int k = 0; k < 8; ++k) {
        int a = 512 + k * 512 / 7, d = 48 + f * 72;
        disc(s, x * 16 + (eng_cos(a) * d >> 16),
             G_GROUND * 16 + (eng_sin(a) * d * 8 / 10 >> 16) + f * f * 8, 24 - 4 * f,
             k % 2 ? 0xffe27a : EMBER, 256);
    }
    if (f < 2) disc(s, x * 16, G_GROUND * 16, 96 - 32 * f, 0xfff2b0, 256);
}

/* The spray at the nozzle as a spurt leaves, three frames. */
static void draw_spray(const struct eg *g, struct eng_surface *s) {
    int f = 3 - g->spray, a = angle(g->aim);
    int x = 3000 + (eng_cos(a) * 24 >> 12), y = 4320 + (eng_sin(a) * 24 >> 12);
    for (int i = 0; i < 7; ++i) {
        int b = a + (i - 3) * 26, r = (6 + f * 5 + (i % 2) * 3);
        disc(s, x + (eng_cos(b) * r >> 12), y + (eng_sin(b) * r >> 12), 30 - 6 * f,
             i % 2 ? 0xffffff : WATER, 256);
    }
}

/* The card's shadow: black at 110 of 256 over a box, its corners square. */
static void shade(struct eng_surface *s, int x, int y, int w, int h) {
    int c[4];
    if (!eng_clip(s, x, y, w, h, c)) return;
    for (int j = c[1]; j < c[3]; ++j)
        for (unsigned *p = at(s, c[0], j), *end = p + c[2] - c[0]; p < end; ++p)
            *p = 0xff000000 | ((*p & 0xff00ff) * 146 >> 8 & 0xff00ff) |
                 ((*p & 0xff00) * 146 >> 8 & 0xff00);
}

/* The seed packet (render.py packet): the title card at the start, the result card at the end. */
static void draw_card(const struct eg *g, struct eng_surface *s) {
    int over = g->mode == LOST, x = CARD_X, y = (over ? CARD_Y[g->card] : 128) - 75, cx = G_PX + 1;
    if (s->y1 <= y || s->y0 >= y + 156) return;
    shade(s, x + 150, y + 11, 3, 139),
        shade(s, x + 9, y + 150, 144, 5);     /* the shadow, 3 right and 5 down */
    eng_round(s, x, y, 150, 16, 6, 0xe0503a); /* rounded bands top and bottom, fills between */
    eng_fill(s, x, y + 8, 150, 32, 0xe0503a);
    eng_fill(s, x, y + 40, 150, 102, 0xfff6dc);
    eng_round(s, x, y + 134, 150, 16, 6, 0xfff6dc);
    for (int k = 0; k < 15; ++k) eng_fill(s, x + 5 + k * 96 / 10, y + 3, 4, 1, 0xb83a2a);
    eng_round(s, cx - 58, y + 123, 116, 18, 9, 0x3e8a3a);
    if (over && g->card < G_DROP)
        return; /* its words print as it lands: a falling card is a plain one */
    word(g, s, over ? EG_T_LOST : EG_T_TITLE, cx, y + 22, 1, 0xfff6dc, 0, 0, 256);
    int yy = y + 58;
    if (over) {
        word(g, s, EG_T_SCORE, x + 16, yy, 0, 0x8a6a4a, 0, 0, 256);
        numeral(g, s, EG_CARD_0, 1, 1, g->score, x + 134, yy, 2, 0x3a2414, 0, 256);
        word(g, s, EG_T_BEST, x + 16, yy + 24, 0, 0x8a6a4a, 0, 0, 256);
        numeral(g, s, EG_SMALL_0, 1, 1, g->save.best, x + 134, yy + 24, 2, 0x3a2414, 0, 256);
        word(g, s, EG_T_WAVE, x + 16, yy + 43, 0, 0x8a6a4a, 0, 0, 256);
        numeral(g, s, EG_SMALL_0, 1, 0, (unsigned)g->wave, x + 134, yy + 43, 2, 0x3a2414, 0, 256);
        if (g->score && g->score == g->save.best) {
            eng_round(s, x + 97, y + 46, 61, 16, 8, 0xffd23f);
            word(g, s, EG_T_NEWBEST, cx + 49, y + 54, 1, 0x3a2414, 0, 0, 256);
        }
    } else {
        word(g, s, EG_T_KEEP, cx, yy, 1, 0x3a2414, 0, 0, 256);
        word(g, s, EG_T_BEST, x + 16, yy + 19, 0, 0x8a6a4a, 0, 0, 256);
        numeral(g, s, EG_CARD_0, 1, 1, g->save.best, x + 134, yy + 19, 2, 0x3a2414, 0, 256);
        word(g, s, EG_T_BESTWAVE, x + 16, yy + 43, 0, 0x8a6a4a, 0, 0, 256);
        numeral(g, s, EG_SMALL_0, 1, 0, g->save.wave, x + 134, yy + 43, 2, 0x3a2414, 0, 256);
    }
    word(g, s, over ? EG_T_REPLANT : EG_T_START, cx, y + 132, 1, 0xffffff, 0, 0, 256);
}

static const unsigned char LIGHT_OF[10] = { DAWN, DAWN, DAY,   DAY,   DAY,
                                            DUSK, DUSK, NIGHT, NIGHT, NIGHT };
static int banner_alpha(const struct eg *g) {
    return g->banner <= G_HOLD ? 256 : 256 * (G_HOLD + G_FADE - g->banner) / G_FADE;
}

static void draw(const void *state, struct eng_surface *s) {
    const struct eg *g = state;
    if (!g->ok) { /* no art: the game leaves at its first step */
        eng_fill(s, s->x0, s->y0, s->x1 - s->x0, s->y1 - s->y0, 0);
        return;
    }
    int top =
        g->mode == PLAY ? -999 : (g->mode == LOST ? CARD_Y[g->card] : 128) - 75; /* the card's */
    scene(g, s, top + 6, top + 144);
    draw_sky(g, s, top + 6, top + 144);
    if (g->bolt) draw_bolt(g, s);
    for (int k = 0; k < 8; ++k) {
        int x = BED_X[k];
        if (g->bed[k] == BURNING) {
            glyph(g, s, EG_FIREGLOW, x, G_GROUND + 1, EMBER, 256);
            sprite(g, s, EG_FIRE0 + g->flame[k] % 3, x, G_GROUND + 1, 0);
        }
        if (g->bed[k] == GROWING && k == g->last[k / 4] && g->blink < 60) {
            unsigned y = 0xffe27a;
            eng_fill(s, x - 18, G_BED_Y - 14, 36, 2, y),
                eng_fill(s, x - 18, G_BED_Y + 21, 36, 2, y);
            eng_fill(s, x - 18, G_BED_Y - 12, 2, 33, y),
                eng_fill(s, x + 16, G_BED_Y - 12, 2, 33, y);
            word(g, s, EG_T_LAST, x, G_BED_Y - 20, 1, y, 1, 0x120c26, 256);
        }
    }
    if (g->mode == PLAY) draw_aim(g, s);
    draw_sprinkler(g, s);
    if (g->mode != LOST && s->y1 > 282 && s->y0 < 300) {
        numeral(g, s, EG_SIGN_0, 1, 1, g->score, 84, 291, 1, lit(g, 0x3a2414), 0, 256);
        numeral(g, s, EG_SIGN_0, 1, 0, (unsigned)(g->wave ? g->wave : 1), 316, 291, 2,
                lit(g, 0x3a2414), 0, 256);
    }
    for (int k = 0; k < G_SHOTS; ++k)
        if (g->sh[k].alive) draw_shot(s, &g->sh[k]);
    for (int k = 0; k < G_EMBERS; ++k) {
        const struct ember *e = &g->em[k];
        if (!e->alive) continue;
        sprite(g, s, ember_part(e), ember_x(e), FX_INT(e->y), ember_lean(e));
    }
    for (int k = 0; k < G_PUFFS; ++k)
        if (g->pf[k].alive) draw_puff(g, s, &g->pf[k]);
    if (g->land_t > 0) draw_sparks(s, g->land_x, 4 - g->land_t);
    if (g->spray) draw_spray(g, s);
    if (g->banner) {
        int a = banner_alpha(g), w = part(g, EG_T_WAVE_BIG)->w + 8, n = 0;
        for (unsigned v = (unsigned)g->wave; v; v /= 10)
            n += part(g, EG_BIG_0 + 2 * (int)(v % 10))->adv;
        int x = G_PX - (w + n) / 2;
        word(g, s, EG_T_WAVE_BIG, x, 96, 0, 0xfff6dc, 1, 0x2a1838, a);
        numeral(g, s, EG_BIG_0, 2, 0, (unsigned)g->wave, x + w, 96, 0, 0xfff6dc, 0x2a1838, a);
        word(g, s, EG_T_DAWN + 2 * LIGHT_OF[(g->wave - 1) % 10], G_PX, 120, 1, 0xffe27a, 1,
             0x2a1838, a);
    }
    if (g->lull && g->mode == PLAY) word(g, s, EG_T_LULL, G_PX, 22, 1, 0xffe27a, 1, 0x120c26, 256);
    if (g->mode != PLAY) draw_card(g, s);
}

/* ---- a step ---- */

/* This part of the sheet changed: kept until the step's end, when the boxes are joined. */
static void mark(struct eg *g, int x, int y, int w, int h) {
    int x1 = x + w, y1 = y + h;
    x = x < 0 ? 0 : x, y = y < 0 ? 0 : y;
    x1 = x1 > ENG_W ? ENG_W : x1, y1 = y1 > g->h ? g->h : y1;
    if (x1 <= x || y1 <= y) return;
    int full = g->boxes == G_BOXES, *b = g->box[full ? G_BOXES - 1 : g->boxes++];
    if (full) {
        b[0] = x < b[0] ? x : b[0], b[1] = y < b[1] ? y : b[1];
        b[2] = x1 > b[2] ? x1 : b[2], b[3] = y1 > b[3] ? y1 : b[3];
    } else
        b[0] = x, b[1] = y, b[2] = x1, b[3] = y1;
}
static int area(const int *b) { return (b[2] - b[0]) * (b[3] - b[1]); }

/* What joining boxes a and b adds to the area painted: below 0 where they overlap. */
static int join_cost(const int *a, const int *b) {
    int w = (a[2] > b[2] ? a[2] : b[2]) - (a[0] < b[0] ? a[0] : b[0]);
    return w * ((a[3] > b[3] ? a[3] : b[3]) - (a[1] < b[1] ? a[1] : b[1])) - area(a) - area(b);
}
/* Box i's cheapest partner among the n, into near[i] and cost[i]. */
static void partner(int (*b)[4], int n, int i, unsigned char *near, int *cost) {
    cost[i] = 0x7fffffff;
    for (int j = 0; j < n; ++j) {
        int c = j == i ? 0x7fffffff : join_cost(b[i], b[j]);
        if (c < cost[i]) cost[i] = c, near[i] = (unsigned char)j;
    }
}

/* The step's boxes to the engine, at most ENG_RECTS: the two whose joined box adds least to the
 * area painted are joined, any that overlap first, until few enough are left. Each box keeps its
 * cheapest partner, found again only for the boxes a join touched. */
static void flush(struct eg *g) {
    int n = g->boxes, (*b)[4] = g->box, cost[G_BOXES];
    unsigned char near[G_BOXES];
    for (int i = 0; i < n; ++i) partner(b, n, i, near, cost);
    while (n > 1) {
        int i = 0;
        for (int k = 1; k < n; ++k) i = cost[k] < cost[i] ? k : i;
        if (n <= ENG_RECTS && cost[i] > 0) break;
        int j = near[i], *a = b[i], *z = b[j], last = n - 1;
        a[0] = z[0] < a[0] ? z[0] : a[0], a[1] = z[1] < a[1] ? z[1] : a[1];
        a[2] = z[2] > a[2] ? z[2] : a[2], a[3] = z[3] > a[3] ? z[3] : a[3];
        for (int k = 0; k < 4; ++k) z[k] = b[last][k]; /* the last box into j's place */
        cost[j] = cost[last], near[j] = near[last], n = last;
        if (i == last) i = j;
        for (int k = 0; k < n; ++k) {
            if (near[k] == last) near[k] = (unsigned char)j; /* the moved box's new number */
            if (k == i || near[k] == i || near[k] == j)
                partner(b, n, k, near, cost);
            else if (join_cost(b[k], b[i]) < cost[k])
                cost[k] = join_cost(b[k], b[i]), near[k] = (unsigned char)i;
        }
    }
    for (int i = 0; i < n; ++i) eng_dirty(b[i][0], b[i][1], b[i][2] - b[i][0], b[i][3] - b[i][1]);
    g->boxes = 0;
}

/* A run of n of one sound, six steps apart: the first now. */
static void sound(struct eg *g, int kind, int n) {
    eng_sfx(kind);
    g->seq = kind, g->runs = n - 1, g->wait = 6;
}

static void ember_box(struct eg *g, const struct ember *e) {
    int x = ember_x(e), y = FX_INT(e->y);
    const struct part *p = part(g, ember_part(e));
    int lean = ember_lean(e) * p->ay / 256; /* the top row's shift; the anchor's row has none */
    mark(g, x - p->ax + (lean < 0 ? lean - 1 : 0), y - p->ay, p->w + (lean < 0 ? -lean : lean) + 2,
         p->h);
}
/* The old box and the new of something that moved, as one when they overlap. */
static void moved(struct eg *g, int n0, int n1) {
    if (n1 - n0 != 2) return;
    int *a = g->box[n0], *b = g->box[n0 + 1];
    if (a[0] < b[2] && b[0] < a[2] && a[1] < b[3] && b[1] < a[3]) {
        a[0] = b[0] < a[0] ? b[0] : a[0], a[1] = b[1] < a[1] ? b[1] : a[1];
        a[2] = b[2] > a[2] ? b[2] : a[2], a[3] = b[3] > a[3] ? b[3] : a[3];
        --g->boxes;
    }
}
/* A spurt's box: its lead drop, and its trail 26 px back along its heading, sagging. */
static void shot_box(struct eg *g, const struct shot *h) {
    int x = FX_INT(h->x), y = FX_INT(h->y), tx = FX_INT(h->x - h->ux * 26),
        ty = FX_INT(h->y - h->uy * 26);
    mark(g, (x < tx ? x : tx) - 7, (y < ty ? y : ty) - 7, (x < tx ? tx - x : x - tx) + 15,
         (y < ty ? ty - y : y - ty) + 20); /* below: the trail's sag, 7 px, and its last drop */
}
static void sight_box(struct eg *g) { /* a box a dot: the line's own box is mostly sky */
    int a = angle(g->aim);
    for (int k = 0; k < 5; ++k)
        mark(g, G_PX + (eng_cos(a) * (40 + 9 * k) >> 16) - 2,
             G_PY + (eng_sin(a) * (40 + 9 * k) >> 16) - 2, 6, 6);
}
/* The lightning's box: its bolt and its branch, and their strokes' widths. */
static void bolt_box(struct eg *g) {
    int pts[16], br[8], x0 = 9999, y0 = 9999, x1 = -9999, y1 = -9999;
    bolt_shape(g->bolt_seed, g->bolt_x, pts, br);
    for (int k = 0; k < 12; ++k) {
        int x = (k < 8 ? pts[2 * k] : br[2 * (k - 8)]) >> 4,
            y = (k < 8 ? pts[2 * k + 1] : br[2 * (k - 8) + 1]) >> 4;
        x0 = x < x0 ? x : x0, x1 = x > x1 ? x : x1, y0 = y < y0 ? y : y0, y1 = y > y1 ? y : y1;
    }
    mark(g, x0 - 4, y0 - 4, x1 - x0 + 9, y1 - y0 + 9);
}
/* The sprinkler head's box at an aim, kicked back or not: its pivot's disc, its barrel and collar
 * 23 px out along the aim and 6 to the sides, its arm flipped out to 13 px beside it. */
static void head_box(struct eg *g, int aim) {
    int a = angle(aim), ux = eng_cos(a), uy = eng_sin(a), x0 = G_PX - 12, y0 = G_PY - 12,
        x1 = G_PX + 13, y1 = G_PY + 13;
    int px[4] = { G_PX + (ux * 23 >> 16), G_PX + ((ux * 18 - uy * 13) >> 16),
                  G_PX + ((ux * 18 + uy * 13) >> 16) };
    int py[4] = { G_PY + (uy * 23 >> 16), G_PY + ((uy * 18 + ux * 13) >> 16),
                  G_PY + ((uy * 18 - ux * 13) >> 16) };
    for (int k = 0; k < 3; ++k) {
        x0 = px[k] - 7 < x0 ? px[k] - 7 : x0, x1 = px[k] + 8 > x1 ? px[k] + 8 : x1;
        y0 = py[k] - 7 < y0 ? py[k] - 7 : y0, y1 = py[k] + 8 > y1 ? py[k] + 8 : y1;
    }
    mark(g, x0, y0, x1 - x0, y1 - y0);
}

/* A puff's box at its frame: a frame's box holds the one before it. */
static void puff_box(struct eg *g, const struct puff *p) {
    if (p->f) /* the steam rising, the flecks flying, the points beside */
        mark(g, p->x - 32, p->y - 36, 60, 60);
    else /* the flash and the flecks leaving it */
        mark(g, p->x - 18, p->y - 18, 36, 36);
}
static void hud_box(struct eg *g) { mark(g, 52, 282, 64, 18); }
static void bed_box(struct eg *g, int k) { mark(g, BED_X[k] - 25, 226, 50, 50); }
static void card_box(struct eg *g, int y) { mark(g, 108, y - 78, 162, 164); }

/* The wave banner's box: its number's line and the light's line under it, the wider. */
static void banner_box(struct eg *g) {
    int w = part(g, EG_T_WAVE_BIG + 1)->w + 8,
        sub = part(g, EG_T_DAWN + 1 + 2 * LIGHT_OF[(g->wave + 9) % 10])->w;
    for (unsigned v = (unsigned)g->wave; v; v /= 10)
        w += part(g, EG_BIG_0 + 1 + 2 * (int)(v % 10))->w;
    w = sub > w ? sub : w;
    mark(g, G_PX - w / 2 - 8, 74, w + 17, 60);
}

/* The time of day for this wave: a turn begins from the one now, a pass of strips at a time. */
static void turn(struct eg *g, int light) {
    if (light == g->light) return;
    g->from = g->light, g->light = light, g->k = 0, g->strip = 0;
}

static void next_wave(struct eg *g) {
    ++g->wave;
    int lull = g->wave % 10 == 0;
    if (lull != g->lull) mark(g, 130, 10, 115, 24);
    g->lull = lull;
    g->left = lull ? 5 : 5 + g->wave > 30 ? 30 : 5 + g->wave;
    g->gap = lull ? 90 : 180 - 10 * g->wave < 44 ? 44 : 180 - 10 * g->wave;
    g->spawn = G_HOLD;
    g->banner = 1;
    banner_box(g);
    hud_box(g), mark(g, 286, 282, 32, 18);
    turn(g, LIGHT_OF[(g->wave - 1) % 10]);
    sound(g, ENG_SFX_TICK, 2);
}

/* The embers that may fall at once: two in the first wave, one more every second wave. */
static int sky(const struct eg *g) {
    int n = 2 + g->wave / 2;
    return g->lull ? 5 : n > G_EMBERS ? G_EMBERS : n;
}

static void spawn(struct eg *g) {
    for (int k = 0; k < G_EMBERS; ++k) {
        struct ember *e = &g->em[k];
        if (e->alive) continue;
        int pc = g->wave < G_CRACK      ? 0
                 : 3 * g->wave - 6 > 30 ? 30
                                        : 3 * g->wave - 6,
            pm = 15 + 4 * g->wave > 40 ? 40 : 15 + 4 * g->wave;
        int r = (int)(eng_rng(&g->seed) % 100), cinders = 0, alive = 0;
        for (int j = 0; j < G_EMBERS; ++j)
            cinders += g->em[j].alive && g->em[j].size == 2, alive += g->em[j].alive;
        e->size = g->lull ? 2 : r < pc ? 2 : r < pc + pm ? 1 : 0;
        if (e->size == 2 && cinders >= G_CINDERS) e->size = 1; /* the frame's cost: see G_CINDERS */
        e->amp = 5 + 3 * g->wave / 2 > G_SWAY ? G_SWAY : 5 + 3 * g->wave / 2;
        e->phase = (int)(eng_rng(&g->seed) % G_PERIOD), e->vx = 0;
        e->bx = FX(30 + G_SWAY + (int)(eng_rng(&g->seed) % (315 - 2 * G_SWAY)));
        e->y = FX(40), e->f = 0;
        e->vy = g->lull ? FX(0.42) : FX(0.42) + (g->wave - 1) * (FX(5) / 100);
        e->vy = e->vy > FX(1.8) ? FX(1.8) : e->vy;
        e->alive = 1;
        ember_box(g, e);
        --g->left;
        return;
    }
}

static void lose(struct eg *g);
static void sky_turn(struct eg *g, int *load);

/* An ember on the ground: the nearest growing bed on its side catches; the side's third lost
 * lights its last bed's frame, its fourth ends the game. */
static void land(struct eg *g, int x) {
    int side = x < G_PX ? 0 : 1, best = -1, lost = 0;
    for (int k = side * 4; k < side * 4 + 4; ++k) {
        if (g->bed[k] != GROWING) {
            ++lost;
            continue;
        }
        int d = x - BED_X[k] < 0 ? BED_X[k] - x : x - BED_X[k];
        if (best < 0 || d < (x - BED_X[best] < 0 ? BED_X[best] - x : x - BED_X[best])) best = k;
    }
    if (g->land_t) mark(g, g->land_x - 24, G_GROUND - 20, 48, 30);
    g->land_x = x, g->land_t = 5;
    mark(g, x - 24, G_GROUND - 20, 48, 30);
    sound(g, ENG_SFX_ALERT, 1);
    if (best < 0) return;
    g->bed[best] = CAUGHT, g->bed_t[best] = G_CATCH, ++lost;
    if (g->last[side] >= 0) bed_box(g, g->last[side]);
    g->last[side] = -1;
    if (lost == 3)
        for (int k = side * 4; k < side * 4 + 4; ++k)
            if (g->bed[k] == GROWING) g->last[side] = k, g->blink = 0, bed_box(g, k);
    if (lost == 4) lose(g);
}

/* A spurt's lead drop on an ember: steam and points; a cinder cracks into two small embers that
 * fly apart, and the embers and spurts stop for a moment. */
static void hit(struct eg *g, struct ember *e) {
    int pts = (e->size + 1) * (g->lull ? 2 : 1);
    g->score += (unsigned)pts;
    hud_box(g);
    for (int k = 0; k < G_PUFFS; ++k)
        if (!g->pf[k].alive || k == G_PUFFS - 1) {
            struct puff *p = &g->pf[k];
            if (p->alive) puff_box(g, p);
            *p = (struct puff){ ember_x(e), FX_INT(e->y), 0, 0, e->size, pts, 1 };
            puff_box(g, p);
            break;
        }
    if (e->size < 2) {
        e->alive = 0;
        sound(g, ENG_SFX_TAP, 1);
        return;
    }
    fx x = FX(ember_x(e));
    e->size = 0, e->amp = 0, e->bx = x, e->vx = -FX(1.6);
    for (int k = 0; k < G_EMBERS; ++k)
        if (!g->em[k].alive) {
            g->em[k] = *e;
            g->em[k].vx = FX(1.6);
            break;
        }
    if (!g->stop) g->stop = G_STOP - 1; /* this step, then G_STOP - 1 more */
    if (g->spawn < G_CALM) g->spawn = G_CALM;
    sound(g, ENG_SFX_TAP, 2);
}

static int fire(struct eg *g) {
    for (int k = 0; k < G_SHOTS; ++k) {
        struct shot *h = &g->sh[k];
        if (h->alive) continue;
        int a = angle(g->aim);
        h->ux = eng_cos(a), h->uy = eng_sin(a);
        h->x = FX(G_PX) + FX(1) / 2 + h->ux * 22, h->y = FX(G_PY) + h->uy * 22;
        h->run = 0, h->alive = 1;
        g->recoil = 9, g->spray = 4; /* painted from the next frame: 8 and 3 */
        sound(g, ENG_SFX_TICK, 1);
        return 1;
    }
    return 0;
}

static void save_best(struct eg *g) {
    if (g->save.best + g->save.wave != g->saved &&
        eng_save("embergarden", &g->save, sizeof g->save, G_MAGIC))
        g->saved = g->save.best + g->save.wave;
}

/* The fourth bed on a side lost: the scene dims, the result card drops in. */
static void lose(struct eg *g) {
    g->mode = LOST, g->card = 0, g->boxes = 0;
    for (int k = 0; k < 8; ++k) /* the bed that ended it shows its fire, as the game holds */
        if (g->bed[k] == CAUGHT) g->bed[k] = BURNING;
    for (int k = 0; k < G_EMBERS; ++k) g->em[k].alive = 0;
    for (int k = 0; k < G_SHOTS; ++k) g->sh[k].alive = 0;
    for (int k = 0; k < G_PUFFS; ++k) g->pf[k].alive = 0;
    g->banner = g->bolt = g->land_t = g->spray = g->recoil = 0;
    g->k = G_TURN, g->strip = 0, g->dim = 1;
    palette(g);
    if (g->score > g->save.best) g->save.best = g->score;
    if ((unsigned)g->wave > g->save.wave) g->save.wave = (unsigned)g->wave;
    save_best(g); /* the game's end, not a frame of play */
    sound(g, ENG_SFX_ALERT, 3);
    eng_dirty_all();
}

/* A new game at dawn, every bed growing: the title card (START) or play at once. */
static void new_game(struct eg *g, int mode) {
    unsigned char *idx = scene_idx(g);
    for (int k = 0; k < 8; ++k) {
        if (g->bed[k] == CHARRED)
            for (int j = 0; j < EG_CH_H; ++j)
                memcpy(idx + (EG_CH_Y + j) * ENG_W + CH_X[k], g->grow[k] + j * EG_CH_W, EG_CH_W);
        g->bed[k] = GROWING;
    }
    for (int k = 0; k < G_EMBERS; ++k) g->em[k].alive = 0;
    for (int k = 0; k < G_SHOTS; ++k) g->sh[k].alive = 0;
    for (int k = 0; k < G_PUFFS; ++k) g->pf[k].alive = 0;
    g->last[0] = g->last[1] = -1;
    g->score = 0, g->wave = 0, g->lull = 0, g->left = 0, g->banner = 0, g->bolt = 0;
    g->bolt_wait = G_FLASH, g->land_t = g->spray = g->recoil = g->stop = 0;
    g->aim = -9000, g->mode = mode, g->card = 0, g->dim = 0;
    g->light = g->from = DAWN, g->k = G_TURN, g->strip = 0;
    g->seed = rand_below(~0u) | 1;
#if Q2POD_DEV
    /* a --dev build: [Q2POD] EMBERWAVE=N in config.ini starts play at wave N, in its light */
    if (mode == PLAY && (g->wave = config_value("Q2POD", "EMBERWAVE", 100, 1) - 1) > 0)
        g->light = g->from = LIGHT_OF[g->wave % 10];
    g->wave = g->wave < 0 ? 0 : g->wave;
#endif
    palette(g);
    if (mode == PLAY) next_wave(g);
    g->boxes = 0;
    eng_dirty_all();
}

static void init(void *state, int h) {
    struct eg *g = state;
    g->h = h;
    g->ok = load(g);
    if (!g->ok)
        toast("Ember Garden's art is missing");
    else {
        const unsigned char *idx = scene_idx(g);
        for (int k = 0; k < 8; ++k)
            for (int j = 0; j < EG_CH_H; ++j)
                memcpy(g->grow[k] + j * EG_CH_W, idx + (EG_CH_Y + j) * ENG_W + CH_X[k], EG_CH_W);
    }
    if (!eng_load("embergarden", &g->save, sizeof g->save, G_MAGIC) || g->save.best > 99999999 ||
        g->save.wave > 9999)
        g->save.best = g->save.wave = 0; /* a save that no game made */
    g->saved = g->save.best + g->save.wave;
    for (int load = 0; g->ok && g->tick < SKY_N; ++g->tick)
        sky_turn(g, &load); /* each at its start */
    new_game(g, START);
}

/* The aim from the wheel, the side buttons and a finger. */
static void aim(struct eg *g, const struct eng_input *in) {
    int a = g->aim + in->wheel * G_WHEEL;
    if (in->held & ENG_PREV) a -= G_KEY;
    if (in->held & ENG_NEXT) a += G_KEY;
    if ((in->held | in->pressed) &
        ENG_TOUCH) /* a tap too; level with the pivot or under: the nearer end */
        a = eng_atan2(in->ty < G_PY ? (in->ty - G_PY) * 16 : -1, in->tx * 16 + 8 - G_PX * 16) *
                36000 / 1024 -
            36000;
    g->aim = a < G_AIM_LO ? G_AIM_LO : a > G_AIM_HI ? G_AIM_HI : a;
}

static void play(struct eg *g, const struct eng_input *in) {
    if (in->pressed & (ENG_CENTRE | ENG_TOUCH)) {
        fire(g);
        g->repeat = G_REPEAT;
    } else if (in->held & ENG_CENTRE && !--g->repeat) {
        fire(g);
        g->repeat = G_REPEAT;
    }
    int frozen = g->stop > 0;
    if (frozen) --g->stop;
    for (int k = 0; k < G_SHOTS && !frozen; ++k) {
        struct shot *h = &g->sh[k];
        if (!h->alive) continue;
        int n0 = g->boxes;
        shot_box(g, h);
        h->x += h->ux * 6, h->y += h->uy * 6, h->run += 6;
        if (h->y < FX(-30) || h->x < FX(-30) || h->x > FX(ENG_W + 30)) {
            h->alive = 0;
            continue;
        }
        for (int j = 0; j < G_EMBERS && h->alive; ++j) {
            struct ember *e = &g->em[j];
            if (!e->alive) continue;
            int dx = ember_x(e) * 16 + 8 - (h->x >> 12), dy = (e->y >> 12) - (h->y >> 12),
                rr = G_HIT + R16[e->size];
            if (dx * dx + dy * dy < rr * rr) {
                ember_box(g, e);
                h->alive = 0;
                hit(g, e);
            }
        }
        if (h->alive) shot_box(g, h);
        moved(g, n0, g->boxes);
    }
    frozen |= g->stop > 0; /* a split in this step: the embers stop from now */
    for (int k = 0; k < G_EMBERS && !frozen; ++k) {
        struct ember *e = &g->em[k];
        if (!e->alive) continue;
        int n0 = g->boxes;
        ember_box(g, e);
        e->f = (g->tick / G_LICK + k) & 3;
        if (e->amp) e->phase = (e->phase + 1) % G_PERIOD;
        e->bx += e->vx, e->vx = e->vx * 15 / 16;
        int r = R16[e->size] / 16 + 1;
        if (FX_INT(e->bx) - e->amp < 24 + r) e->bx = FX(24 + r + e->amp);
        if (FX_INT(e->bx) + e->amp > 351 - r) e->bx = FX(351 - r - e->amp);
        e->y += e->vy;
        if (FX_INT(e->y) >= G_GROUND) {
            e->alive = 0;
            g->boxes = n0 + 1; /* its old box: it is gone */
            land(g, ember_x(e));
            if (g->mode != PLAY) return;
            continue;
        }
        ember_box(g, e);
        moved(g, n0, g->boxes);
    }
    int alive = 0;
    for (int k = 0; k < G_EMBERS; ++k) alive += g->em[k].alive;
    if (alive >= sky(g)) { /* a full sky: the cloud holds its next ember */
        if (g->spawn < G_BREATH) g->spawn = G_BREATH;
    } else if (!frozen && g->left && !--g->spawn) {
        spawn(g);
        g->spawn = g->gap, ++alive;
    }
    if (!g->left && !alive && !g->banner) next_wave(g);
    /* lightning in the ash column, after dusk and in a lull, never over many embers */
    if (g->bolt_wait) --g->bolt_wait;
}

/* The sky's life: each step one of SKY's things takes its turn (so each every SKY_N steps), and is
 * marked where its picture changed. A cloud slides a pixel to the right every p turns and comes
 * round again; a billow circles its place on the canopy, 4 px to each side and 2 up and down; a
 * puff climbs the ash column from the crater, strongest half way; the crater's glow swells and
 * sinks; each of the lava's three masks of dashes brightens and fades a third of a round after the
 * one before, so the dashes run down the cone. A turn whose boxes would take the step past its
 * budget is passed over. */
static void sky_turn(struct eg *g, int *load) {
    int i = g->tick % SKY_N, t = g->tick / SKY_N, first = g->tick < SKY_N;
    const struct sky *k = &SKY[i];
    const struct part *p = part(g, k->part);
    int x = first ? k->x : g->sky[i].x, y = k->y, a = 256;
    if (k->kind == DRIFT) {
        if (!first && t % k->p == 0 && ++x > ENG_W + 44) x = -44;
    } else if (k->kind == BOB) {
        x = k->x + (eng_cos(t * 40 + k->p * 4) * 4 >> 16),
        y += eng_sin(t * 40 + k->p * 4) * 2 >> 16;
        a = 220;
    } else if (k->kind == RISE) {
        int u = (t * 3 + k->p) & 255;
        x = VX - 2 - 46 * u / 256, y = VY - 12 - 84 * u / 256,
        a = (u < 128 ? u : 255 - u) * 220 / 128;
    } else if (k->kind == PULSE)
        a = 100 + (eng_sin(t * 56) * 90 >> 16);
    else {
        int d = (t * 85 - k->p * 341) & 1023;
        d = d < 512 ? d : 1024 - d;
        a = d > 341 ? 0 : 256 - d * 3 / 4;
    }
    if (x == g->sky[i].x && y == g->sky[i].y && a == g->sky[i].a) return;
    if (!first) {
        int px = (p->w + 2) * (p->h + 2); /* its old box and its new, a pixel or two apart */
        if (*load + px > G_BUDGET) return;
        *load += px;
        int n0 = g->boxes;
        mark(g, g->sky[i].x - p->ax, g->sky[i].y - p->ay, p->w, p->h);
        mark(g, x - p->ax, y - p->ay, p->w, p->h);
        moved(g, n0, g->boxes);
    }
    g->sky[i].x = (short)x, g->sky[i].y = (short)y, g->sky[i].a = (short)a;
}

/* What waits for a lighter frame: the extras of a step (the banner's fade, the lightning's next
 * frame, a puff's and the flames' next frames, a strike, the light's strip, the sky's turn) are
 * painted only while the step's boxes, with theirs, stay within G_BUDGET pixels; one that does not
 * fit waits for a step that has room, so no frame pays for everything at once
 * (docs/games-engine.md#ember-garden).
 */
#define ROOM(a) (load + (a) <= G_BUDGET ? (load += (a), 1) : 0)
static void extras(struct eg *g) {
    int load = 0, alive = 0;
    for (int k = 0; k < g->boxes; ++k) load += area(g->box[k]);
    for (int k = 0; k < G_EMBERS; ++k) alive += g->em[k].alive;
    if (g->banner && (g->banner < G_HOLD || ROOM(8000))) { /* its hold counts on; its fade waits */
        if (g->banner >= G_HOLD) banner_box(g);
        if (++g->banner > G_HOLD + G_FADE) g->banner = 0;
    }
    if (g->bolt && ROOM(4000)) { /* its frames: the bolt two, dimmer two, then gone */
        bolt_box(g);
        if (++g->bolt > 4) g->bolt = 0;
    }
    for (int k = 0, turned = 0; k < G_PUFFS; ++k) { /* one puff turns a step at most */
        struct puff *p = &g->pf[k];
        if (!p->alive || ++p->t < PUFF_STEPS[p->f] || turned || !ROOM(3600)) continue;
        p->t = 0, turned = 1;
        if (++p->f > 4) p->alive = 0;
        puff_box(g, p);
    }
    for (int k = 0; k < 8; ++k) /* a flame's next frame: one bed's a step */
        if (g->bed[k] == BURNING && (g->tick + k) % G_FLAME == 0 && ROOM(576))
            ++g->flame[k], mark(g, BED_X[k] - 12, 238, 24, 24);
    if (!g->bolt && !g->bolt_wait && (g->light >= DUSK || g->lull) && alive <= G_STRIKE &&
        !g->banner && eng_rng(&g->seed) % 64 == 0 && ROOM(9000)) {
        g->bolt = 1, g->bolt_seed = (int)eng_rng(&g->seed),
        g->bolt_x = 164 + (int)(eng_rng(&g->seed) % 16);
        g->bolt_wait = G_FLASH + (int)(eng_rng(&g->seed) % 120);
        bolt_box(g);
    }
    /* the light's turn: a strip of rows a step, the palette a pass further at each pass's start */
    if ((g->k < G_TURN || g->strip) && ROOM(ENG_W * G_STRIP)) {
        if (!g->strip) ++g->k, palette(g);
        mark(g, 0, g->strip * G_STRIP, ENG_W, G_STRIP);
        g->strip = (g->strip + 1) % (320 / G_STRIP);
    }
    sky_turn(g, &load);
}
#undef ROOM

static int step(void *state, const struct eng_input *in) {
    struct eg *g = state;
    if (!g->ok) { /* its art could not be read: back to the list */
        eng_quit();
        return 0;
    }
    int aim0 = g->aim, recoil = g->recoil, spray = g->spray, left = shots(g);
    if (g->runs && !--g->wait) eng_sfx(g->seq), g->wait = 6, --g->runs;
    ++g->tick;
    if (g->mode == START) {
        int load = 0;
        if (in->pressed & (ENG_CENTRE | ENG_TOUCH))
            new_game(g, PLAY);
        else
            sky_turn(g, &load); /* the title card's sky moves too */
    } else if (g->mode == PLAY) {
        aim(g, in);
        play(g, in);
    } else {
        if (g->card < G_DROP) {
            card_box(g, CARD_Y[g->card]);
            ++g->card;
            card_box(g, CARD_Y[g->card]);
            moved(g, g->boxes - 2, g->boxes);
        } else if (in->pressed & (ENG_CENTRE | ENG_TOUCH))
            new_game(g, PLAY);
    }
    if (g->mode == PLAY) {
        if (g->recoil) --g->recoil;
        if (g->spray) --g->spray;
        if (g->aim != aim0) sight_box(g);
        if (g->spray || spray) { /* the spray, out to 45 px along the aim */
            int a = angle(g->aim);
            mark(g, G_PX + (eng_cos(a) * 34 >> 16) - 13, G_PY + (eng_sin(a) * 34 >> 16) - 13, 28,
                 28);
        }
        if (g->aim != aim0 || g->recoil || recoil || shots(g) != left) {
            head_box(g, g->aim), head_box(g, aim0);
            if (shots(g) != left) mark(g, 180, 279, 17, 7); /* the collar's drops */
            if (g->aim != aim0) {
                int a = g->aim;
                g->aim = aim0, sight_box(g), g->aim = a;
            }
        }
        if (g->land_t) mark(g, g->land_x - 24, G_GROUND - 20, 48, 30), --g->land_t;
        for (int k = 0; k < 8; ++k) {
            if (g->bed[k] == CAUGHT && !--g->bed_t[k])
                g->bed[k] = BURNING, g->bed_t[k] = G_BURN, bed_box(g, k);
            else if (g->bed[k] == BURNING) {
                if (!--g->bed_t[k]) {
                    g->bed[k] = CHARRED;
                    {
                        unsigned char *idx = scene_idx(g);
                        const unsigned char *ch =
                            (const unsigned char *)data(g, EG_CHARRED) + k * EG_CH_W * EG_CH_H;
                        for (int j = 0; j < EG_CH_H; ++j)
                            memcpy(idx + (EG_CH_Y + j) * ENG_W + CH_X[k], ch + j * EG_CH_W,
                                   EG_CH_W);
                    }
                    bed_box(g, k);
                }
            }
        }
        if (g->last[0] >= 0 || g->last[1] >= 0) {
            g->blink = (g->blink + 1) % 90;
            if (g->blink == 0 || g->blink == 60)
                for (int s = 0; s < 2; ++s)
                    if (g->last[s] >= 0) bed_box(g, g->last[s]);
        }
    }
    if (g->mode == PLAY) extras(g);
    flush(g);
    return 1;
}

/* A best is written at the game's end; one that could not be then is tried again here. */
static void pause(void *state) {
    struct eg *g = state;
    if (g->mode == LOST) save_best(g);
}

const struct eng_game eng_embergarden = {
    "Ember Garden", sizeof(struct eg), ENG_RAW | ENG_FULL, init, step, draw, pause
};
