/* Pop Stall (docs/games-engine.md#pop-stall): a carnival balloon-pop stall seen over its counter. A
 * spiky ball bounces off a small trampoline into a lattice of hexagon-packed balloons pinned to the
 * cork; a balloon pops on a hit, a double skin takes two, a sandbag never pops, the gold star
 * balloon pops its six neighbours in a chain. Pops without a trampoline touch between them build a
 * combo; a popped balloon now and then drops a prize ticket to catch. Clearing the balloons is the
 * next level, made from the game's seed and the level number; three misses end the game.
 *
 * The game takes the whole panel (ENG_FULL): sheet rows are screen rows. Its pictures are drawn by
 * the design's own code and read once from usr/share/q2games/popstall/art.bin into the state
 * (game_popstall_art.h says where each is); without that file the game draws flat stand-ins. */
#include "engine.h"
#include "game_popstall_art.h"
#include "peq.h"

#define P_L 24 /* the wall faces and the beam's underside: the ball's edge stays inside them */
#define P_R 351
#define P_TOP 56
#define P_RB 7  /* the ball's collision radius */
#define P_RC 8  /* a balloon's */
#define P_PX 20 /* the lattice: a row's pitch across, the rows' pitch down */
#define P_PY 17
#define P_ROWS 7
#define P_COLS 16 /* in an even row; an odd row has one fewer, half a pitch in */
#define P_X0 37   /* an even row's first centre is P_X0 + 0.5 */
#define P_Y0 78   /* the first row's centre */
#define P_CELLS (P_ROWS * P_COLS)
#define P_MAT 280     /* the mat's top at rest: the bounce line */
#define P_REST 273    /* the ball's centre resting on the mat */
#define P_COUNTER 291 /* the counter's ledge: everything behind it is hidden below */
#define P_LOST 302    /* a ball whose centre is this low is wholly behind the counter: missed */
#define P_PAD_W 60
#define P_WIDE_W 90
#define P_WHEEL 2       /* px a wheel position */
#define P_KEY 4         /* px a step while Previous or Next is held */
#define P_STEEP 43      /* 15 degrees (1024 a turn): the shallowest a path may be */
#define P_TILT 171      /* 60 degrees: the trampoline's end turns a path this far from vertical */
#define P_TRIO 85       /* 30 degrees: TRIO's two balls leave this far either side */
#define P_HUD_Y 21      /* the HUD plaques' middle row; paused, P_HUD_Y2 */
#define P_HUD_Y2 38     /* 38.5 in the design */
#define P_SEQ 6         /* frames between the sounds of a run */
#define P_FX 20         /* effects at once */
#define P_BULBS 32      /* the beam's 14 and 9 down each wall: one bit each */
#define P_BULBS_STEP 10 /* bulbs that change in a frame (step) */
#define P_FX_AREA 12000 /* px a frame's boxes may cover before an effect waits (effects) */
#define P_MAGIC 0x31535450 /* "PTS1": the save */
#define P_ART "/usr/share/q2games/popstall/art.bin"

enum { READY, PLAY, CLEAR, INFLATE, OVER };
enum { NONE, BAL, DBL, STAR, SACK };                 /* a cell */
enum { T_WIDE = 1, T_TRIO, T_GLUE, T_SLOW, T_LIFE }; /* a ticket */
enum { F_POP = 1, F_STRIP, F_BAG, F_DUST, F_SCORE, F_CALL, F_SPARK, F_STARS, F_KNOCK };
enum { C_WIDE, C_TRIO, C_GLUE, C_SLOW, C_LIFE, C_GRAND }; /* the callouts */

/* The design's palette (STYLE.md). */
#define K_CREAM 0xf4e3c1
#define K_INK 0x1b1240
#define K_HINT 0x4a2a14
#define K_GOLD 0xffd23f
#define K_GOLD_L 0xffefa8
#define K_CRIMSON 0xc8324a
#define K_CRIMSON_D 0xae2a40
#define K_CRIMSON_DD 0x7a1730
#define K_SHADOW 0x6b4526
#define K_STRING 0xe6dcc8
#define K_KNOCK 0xffe9c4
#define K_FACE 0xdda568
#define K_SACK_L 0xd2b07e
#define K_LEDGE 0xe8c98a
#define K_MINT 0xa8f0cc
#define K_WOOD_1 0x7e4e2c
#define K_WOOD_SEAM 0x3e2614

static const unsigned row_col[8] = { 0xff5470, 0xff9f1c, 0xffd23f, 0x3bceac,
                                     0x5aa9f5, 0xf4f1ea, 0xf15bb5, 0xf5c542 }; /* 7: the star */
static const unsigned row_light[8] = { 0xffb1be, 0xffd08c, 0xffefa8, 0xa6ecda,
                                       0xb4d8fb, 0xffffff, 0xf9b4dd, 0xffefa8 };
static const unsigned tk_paper[5] = { 0xffe8a3, 0x9fe3ff, 0xffc46b, 0xa8f0cc, 0xffb1be };
static const unsigned tk_ink[5] = { 0xc8324a, 0x1b3a7a, 0x7a3a0a, 0x0e5a42, 0x8e1f36 };
static const char *const callouts[6] = { "WIDE!", "TRIO!",    "GLUE!",
                                         "SLOW!", "+1 BALL!", "GRAND PRIZE!" };
static const unsigned call_fill[6] = { K_GOLD, 0x9fe3ff, 0xffc46b, 0xa8f0cc, 0xffb1be, K_GOLD };
static const unsigned call_edge[6] = { K_CRIMSON_DD, 0x1b3a7a, 0x7a3a0a,
                                       0x0e5a42,     0x8e1f36, K_CRIMSON_DD };
/* The mat's depth at the contact, quarter px, a frame each from the contact: down in two frames,
 * back through one 1.5 px overshoot, at rest on frame 11 (fair.py DIP_CURVE). */
static const signed char dip_curve[12] = { 8, 16, 15, 11, 5, 0, -4, -6, -5, -3, -1, 0 };
static const unsigned char hop_curve[10] = { 0, 5, 8, 10, 10, 8, 5, 3, 1, 0 }; /* tenths */
static const short plush_x[4] = { 13, 13, 362, 362 }, plush_y[4] = { 118, 196, 118, 196 };

struct ball {
    fx x, y;
    int ang, live, spin, bounce; /* bounce 4..1: the squash then the stretches after a bounce */
    int stuck, held, cx; /* GLUE: stuck to the mat this far from its middle, for held frames */
};
struct effect {
    short kind, t, x, y;
    int v; /* a pop's row, a score, a callout, a cell, a side */
};
struct rect {
    short x0, y0, x1, y1;
};

struct pstall {
    struct ball ball[3];
    fx spd;
    int pad, h, mode, t, level, lives, side, left, combo, swell, seq, runs, wait;
    unsigned score, seed, rs;
    unsigned char cell[P_CELLS], squash[P_CELLS];
    signed char lean_x[P_CELLS], lean_y[P_CELLS];
    int dip_t, dip_x,
        dip_q; /* the dip's frame (12: at rest), its x, its depth in eighths of 4 px */
    int wide_t, slow_t, glue_n, frame;
    struct {
        int kind, x, t;
        fx y;
    } tk[2];
    struct effect fx[P_FX];
    int chain[6], chain_n, chain_t, star_fx; /* the star's ring still to pop, two frames apart */
    unsigned bulbs, flip;                    /* the bulbs lit; flipped for flip_t by a beam hit */
    int flip_t, allon_t, brown_t, chase;
    int hop_t[4], hop_amp[4], hop_wait[4];
    int lost_t, gain_t, slots, paused, best_new;
    int marks;
    struct rect mark[ENG_RECTS];
    struct {
        unsigned magic, best, level, sum;
    } save;
    unsigned saved;
    int fresh; /* a level's start: Ready shows its title and hint; after a miss it shows none */
    int art_ok;
    unsigned art[]; /* ART_BYTES, read at init */
};

static fx cell_x(int r, int c) { return FX(P_X0 + (r & 1) * P_PX / 2 + c * P_PX) + FX(1) / 2; }
static int cell_xi(int r, int c) { return P_X0 + (r & 1) * P_PX / 2 + c * P_PX; }
static int cell_y(int r) { return P_Y0 + r * P_PY; }
static int cols(int r) { return P_COLS - (r & 1); }
static int pad_w(const struct pstall *b) { return b->wide_t ? P_WIDE_W : P_PAD_W; }

/* ---- changed rects ---- */

/* This part of the sheet changes: kept as at most ENG_RECTS boxes that never overlap, one that
 * overlaps another joined to it, and past ENG_RECTS a new one joined to the box it grows least, so
 * a frame's many small changes never become one box over the whole stall. */
static void mark(struct pstall *b, int x, int y, int w, int h) {
    int x1 = x + w, y1 = y + h, best = 0;
    long long grow = -1;
    x = x < 0 ? 0 : x, y = y < 0 ? 0 : y, x1 = x1 > ENG_W ? ENG_W : x1, y1 = y1 > b->h ? b->h : y1;
    if (x1 <= x || y1 <= y) return;
    for (int k = 0; k < b->marks;) { /* a box this overlaps joins it: no pixel is painted twice */
        struct rect *r = &b->mark[k];
        if (r->x0 < x1 && x < r->x1 && r->y0 < y1 && y < r->y1) {
            x = x < r->x0 ? x : r->x0, y = y < r->y0 ? y : r->y0;
            x1 = x1 > r->x1 ? x1 : r->x1, y1 = y1 > r->y1 ? y1 : r->y1;
            *r = b->mark[--b->marks], k = 0;
        } else
            ++k;
    }
    if (b->marks < ENG_RECTS) {
        struct rect *r = &b->mark[b->marks++];
        r->x0 = (short)x, r->y0 = (short)y, r->x1 = (short)x1, r->y1 = (short)y1;
        return;
    }
    for (int k = 0; k < ENG_RECTS; ++k) {
        struct rect *r = &b->mark[k];
        int a0 = (r->x1 - r->x0) * (r->y1 - r->y0);
        int u0 = (x < r->x0 ? x : r->x0), v0 = (y < r->y0 ? y : r->y0);
        int u1 = (x1 > r->x1 ? x1 : r->x1), v1 = (y1 > r->y1 ? y1 : r->y1);
        long long g = (long long)(u1 - u0) * (v1 - v0) - a0;
        if (grow < 0 || g < grow) grow = g, best = k;
    }
    struct rect *r = &b->mark[best];
    r->x0 = (short)(x < r->x0 ? x : r->x0), r->y0 = (short)(y < r->y0 ? y : r->y0);
    r->x1 = (short)(x1 > r->x1 ? x1 : r->x1), r->y1 = (short)(y1 > r->y1 ? y1 : r->y1);
}
static void flush_marks(struct pstall *b) {
    for (int k = 0; k < b->marks; ++k) {
        struct rect *r = &b->mark[k];
        eng_dirty(r->x0, r->y0, r->x1 - r->x0, r->y1 - r->y0);
    }
    b->marks = 0;
}
/* The area the frame's boxes cover so far. */
static int marked(const struct pstall *b) {
    int n = 0;
    for (int k = 0; k < b->marks; ++k)
        n += (b->mark[k].x1 - b->mark[k].x0) * (b->mark[k].y1 - b->mark[k].y0);
    return n;
}

static void mark_all(struct pstall *b) {
    b->marks = 0;
    eng_dirty_all();
}

/* ---- the level ---- */

/* A cell's six neighbours that are on the lattice, as indices into cell[]; how many. */
static int around(int r, int c, int *out) {
    int n = 0, lo = r & 1 ? c : c - 1;
    for (int dr = -1; dr <= 1; ++dr)
        for (int k = dr ? lo : c - 1; k <= (dr ? lo + 1 : c + 1); k += dr ? 1 : 2)
            if (r + dr >= 0 && r + dr < P_ROWS && k >= 0 && k < cols(r + dr))
                out[n++] = (r + dr) * P_COLS + k;
    return n;
}
static int touching(int i, int j) {
    int near[6], n = around(i / P_COLS, i % P_COLS, near);
    while (n--)
        if (near[n] == j) return 1;
    return 0;
}

/* The level's mask over the lattice, mirrored about the middle column: ax is a cell's distance
 * from it in half pitches (10 px), ay its row's from the middle row. */
static int shape(int kind, int p, int q, int ax, int ay, int r) {
    if (kind == 0) return ax + 2 * ay <= p && ax + 2 * ay >= p - q; /* a diamond, maybe hollow */
    if (kind == 1) {                                                /* a ring, p px out, q thick */
        int d = ax * ax * 100 + ay * ay * P_PY * P_PY;
        return d <= p * p && d >= (p - q) * (p - q);
    }
    if (kind == 2) return ax >= p && ax < p + q; /* twin columns */
    return (r * 2 + ax) % p < q;                 /* chevrons */
}

/* How many sandbags a level hangs: none before level 3, then 2 and 2 more every 2 levels, 6 at
 * most. */
static int bags_for(int level) {
    int n = level < 3 ? 0 : 2 + 2 * ((level - 3) / 2);
    return n > 6 ? 6 : n;
}

/* The level from the seed and the level number alone (fair.py build()): one of four
 * masks with its sizes drawn, double skins more often as the levels go, the star on the middle
 * column where it can be; then the sandbags in mirrored pairs on rows 2 to 6, never touching
 * another bag or the star. A mask that leaves under 12 balloons is drawn again; the eighth try
 * fills the lattice. bags < 0: the level's own count. */
static void build(struct pstall *b, int bags) {
    unsigned s = b->seed ^ (unsigned)b->level * 0x9e3779b9u,
             g = 9u - (unsigned)(b->level < 7 ? b->level : 7);
    int n = 0;
    s = s ? s : 1;
    for (int tries = 0; n < 12; ++tries) {
        int kind = (int)((unsigned)(b->level - 1 + tries) + b->seed) & 3;
        int p = kind == 0   ? 9 + (int)(eng_rng(&s) % 7)
                : kind == 1 ? 60 + (int)(eng_rng(&s) % 50)
                : kind == 2 ? 2 + (int)(eng_rng(&s) % 7)
                            : 5 + (int)(eng_rng(&s) % 4);
        int q = kind == 0   ? 4 + (int)(eng_rng(&s) % 12)
                : kind == 1 ? 20 + (int)(eng_rng(&s) % 16)
                : kind == 2 ? 3 + (int)(eng_rng(&s) % 3)
                            : 2 + (int)(eng_rng(&s) % 2);
        unsigned dbl = eng_rng(&s) % g;
        n = 0;
        for (int r = 0; r < P_ROWS; ++r)
            for (int c = 0; c < P_COLS; ++c) {
                int x = 2 * c - 15 + (r & 1), ax = x < 0 ? -x : x, ay = r < 3 ? 3 - r : r - 3;
                int on = c < cols(r) && (tries >= 7 || shape(kind, p, q, ax, ay, r));
                b->cell[r * P_COLS + c] = !on                                  ? NONE
                                          : ((unsigned)(ax + 2 * r) + dbl) % g ? BAL
                                                                               : DBL;
                n += on;
            }
    }
    int axis = 0, key = -1, cand[P_CELLS], m = 0, placed = 0;
    for (int r = 1; r < P_ROWS; r += 2) axis += b->cell[r * P_COLS + 7] != NONE;
    int k = (int)(eng_rng(&s) % (unsigned)(axis ? axis : n));
    for (int i = 0; i < P_CELLS; ++i)
        if (b->cell[i] && (!axis || (i / P_COLS & 1 && i % P_COLS == 7)) && !k--) {
            b->cell[key = i] = STAR;
            break;
        }
    for (int i = 0; i < P_CELLS; ++i)
        if (i / P_COLS >= 1 && i / P_COLS <= 5 && b->cell[i] == BAL) cand[m++] = i;
    bags = bags < 0 ? bags_for(b->level) : bags;
    for (int guard = 0; placed < bags && m && guard < 60; ++guard) {
        int i = cand[eng_rng(&s) % (unsigned)m], r = i / P_COLS,
            j = r * P_COLS + cols(r) - 1 - i % P_COLS;
        if (j == i || b->cell[j] != BAL || touching(i, j) || touching(i, key) || touching(j, key))
            continue;
        int clash = 0;
        for (int q2 = 0; q2 < P_CELLS && !clash; ++q2)
            clash = b->cell[q2] == SACK && (touching(q2, i) || touching(q2, j));
        if (clash) continue;
        b->cell[i] = b->cell[j] = SACK;
        placed += 2, n -= 2;
    }
    b->left = n;
    b->spd = FX(2.5) + (b->level - 1) * (FX(15) / 100);
    b->spd = b->spd > FX(5) ? FX(5) : b->spd;
}

/* ---- effects ---- */

static const short fx_box[10][4] = {
    /* x y w h from the effect's x y */
    { 0, 0, 0, 0 },       { -32, -30, 66, 68 }, { -30, -28, 62, 62 },  { -12, -14, 26, 30 },
    { -22, -16, 46, 34 }, { -30, -19, 62, 28 }, { -76, -18, 152, 30 }, { -58, -26, 118, 32 },
    { -38, -32, 78, 66 }, { -9, -8, 18, 16 },
};
static const unsigned char fx_life[10] = { 0, 12, 8, 10, 8, 30, 40, 24, 16, 3 };

/* Whether effect e's frame t looks other than its frame t - 1 (or ends it): text rises a pixel at a
 * time; a pop holds its flash, its burst and its first shreds two or three frames each; the star's
 * stars move every other frame; the sparkles stand still. */
static int fx_paints(const struct effect *e, int t) {
    if (t <= 0) return !t; /* not shown yet; shown first */
    if (t >= fx_life[e->kind]) return 1;
    return e->kind == F_SCORE   ? t * 10 / 30 != (t - 1) * 10 / 30
           : e->kind == F_CALL  ? t * 6 / 40 != (t - 1) * 6 / 40
           : e->kind == F_POP   ? t == 2 || t == 4 || t >= 7
           : e->kind == F_STARS ? !(t & 1)
                                : e->kind != F_SPARK;
}

static void fx_mark(struct pstall *b, const struct effect *e) {
    const short *x = fx_box[e->kind];
    mark(b, e->x + x[0], e->y + x[1], x[2], x[3]);
}
/* The frame's room left for effects: P_FX_AREA less what is marked and what each ball in play will
 * mark at the step's end. */
static int fx_room(const struct pstall *b) {
    return P_FX_AREA - marked(b) - 1200 * (b->ball[0].live + b->ball[1].live + b->ball[2].live);
}

/* A new effect, shown delay frames from now; one that would not fit the frame's room waits a frame
 * (its cell already gone, the old picture stays that frame). Its slot, or -1 when all are taken. */
static int fx_new(struct pstall *b, int kind, int x, int y, int v, int delay) {
    for (int k = 0; k < P_FX; ++k)
        if (!b->fx[k].kind) {
            struct effect *e = &b->fx[k];
            e->kind = (short)kind, e->x = (short)x, e->y = (short)y, e->v = v;
            if (!delay && fx_box[kind][2] * fx_box[kind][3] > fx_room(b)) delay = 1;
            e->t = (short)-delay;
            if (!delay) fx_mark(b, e);
            return k;
        }
    return -1;
}
static int add_fx(struct pstall *b, int kind, int x, int y, int v) {
    return fx_new(b, kind, x, y, v, 0);
}

static void sound(struct pstall *b, int kind, int n) {
    eng_sfx(kind);
    b->seq = kind, b->runs = n - 1, b->wait = P_SEQ;
}

static void hop(struct pstall *b, int k, int amp, int wait) {
    b->hop_t[k] = 10, b->hop_amp[k] = amp, b->hop_wait[k] = wait;
}
static int hop_dy(const struct pstall *b, int k) {
    if (!b->hop_t[k] || b->hop_wait[k]) return 0;
    int a = b->hop_amp[k] * hop_curve[10 - b->hop_t[k]];
    return -(a >= 0 ? (a + 5) / 10 : -((-a + 5) / 10));
}

/* The dip restarts at x, q eighths of the full 4 px deep. */
static void dip(struct pstall *b, int x, int q) { b->dip_t = 0, b->dip_x = x, b->dip_q = q; }

static void tramp_mark(struct pstall *b) {
    int w = pad_w(b);
    mark(b, b->pad - w / 2 - 2, 272, w + 5, 23);
}

/* ---- the game ---- */

static void ball_rest(struct pstall *b) {
    struct ball *o = &b->ball[0];
    b->ball[1].live = b->ball[2].live = 0;
    o->live = 1, o->stuck = 0, o->bounce = 0;
    o->x = FX(b->pad + b->side * P_PAD_W / 3), o->y = FX(P_REST);
}

static void band_mark(struct pstall *b) { mark(b, 60, 212, 256, 46); }

static void ready(struct pstall *b) {
    b->mode = READY, b->fresh = 1;
    b->side = b->side > 0 ? -1 : 1;
    ball_rest(b);
    band_mark(b);
}

static void new_game(struct pstall *b) {
    b->score = 0, b->lives = 3, b->level = 1, b->combo = 0, b->slots = 3, b->best_new = 0;
    b->seed = rand_below(~0u) | 1;
    b->rs = b->seed;
    b->wide_t = b->slow_t = b->glue_n = 0;
    b->tk[0].kind = b->tk[1].kind = 0;
    for (int k = 0; k < P_FX; ++k) b->fx[k].kind = 0;
    for (int i = 0; i < P_CELLS; ++i) b->lean_x[i] = b->lean_y[i] = 0, b->squash[i] = 0;
    b->chain_n = 0;
    build(b, -1);
    ready(b);
    mark_all(b);
}

static void init(void *state, int h) {
    struct pstall *b = state;
    b->h = h;
    b->pad = ENG_W / 2;
    b->dip_t = 12;
    b->art_ok =
        blob_io(P_ART, P_ART, b->art, ART_BYTES, 0) && games_ok(b->art, ART_BYTES, ART_MAGIC);
    if (!eng_load("popstall", &b->save, sizeof b->save, P_MAGIC) || b->save.best > 99999999 ||
        b->save.level > 99)
        b->save.best = b->save.level = 0; /* a save that no game made */
    b->saved = b->save.best + b->save.level;
    new_game(b);
}

/* No angle shallower than P_STEEP from the horizontal: the ball cannot stall sideways. */
static int steep(int a) {
    int h = (a &= 1023) & 511;
    return h < P_STEEP ? (a & 512) + P_STEEP : h > 512 - P_STEEP ? (a & 512) + 512 - P_STEEP : a;
}

/* When on its path v (1/64 px) a ball dx, dy from a balloon's centre first touches it, in 1024ths
 * of the path; 1025: not on this path. Already touching and closing is at once. */
static int sweep(int dx, int dy, int vx, int vy) {
    int bv = dx * vx + dy * vy, rr = (P_RB + P_RC) * 64, a = vx * vx + vy * vy;
    if (bv >= 0) return 1025;
    int cc = dx * dx + dy * dy - rr * rr;
    if (cc <= 0) return 0;
    long long disc = (long long)bv * bv - (long long)a * cc;
    if (disc < 0) return 1025;
    int num = -bv - (int)eng_sqrt((unsigned long long)disc);
    return num >= a ? 1025 : num * 1024 / a;
}

static void score(struct pstall *b, unsigned n) {
    b->score += n;
    mark(b, 30, 11, 150, 21);
}

/* A pop or a strip without a trampoline touch since the last: the combo's next, its points. */
static unsigned combo_up(struct pstall *b) {
    ++b->combo;
    if (b->combo >= 2) b->swell = 6, mark(b, 128, 9, 120, 25);
    if (b->combo == 10) hop(b, 0, 2, 0), hop(b, 2, 2, 0); /* x10: the two top prizes hop */
    unsigned pts = (unsigned)(b->level * (b->combo < 10 ? b->combo : 10));
    score(b, pts);
    return pts;
}

static void lean(struct pstall *b, int i, int on) {
    int near[6], r = i / P_COLS, c = i % P_COLS;
    for (int k = around(r, c, near); k--;) {
        int j = near[k];
        if (b->cell[j] != BAL && b->cell[j] != DBL && b->cell[j] != STAR) continue;
        int dx = cell_xi(j / P_COLS, j % P_COLS) - cell_xi(r, c),
            dy = cell_y(j / P_COLS) - cell_y(r);
        /* 1.5 px away from the pop: 2 across a row, 1 and 1 to a row above or below */
        b->lean_x[j] = (signed char)(on ? ((dx > 0) - (dx < 0)) * (dy ? 1 : 2) : 0);
        b->lean_y[j] = (signed char)(on ? (dy > 0) - (dy < 0) : 0);
    }
}

/* The ball's touch on cell i: a balloon pops, a double skin loses its skin, a sandbag swings, the
 * star pops and its six neighbours follow round the ring. */
static void hit(struct pstall *b, int i, int from_x) {
    int r = i / P_COLS, c = i % P_COLS, x = cell_xi(r, c), y = cell_y(r), k = b->cell[i];
    if (k == SACK) {
        add_fx(b, F_BAG, x, y, i * 2 + (from_x > x));
        add_fx(b, F_DUST, x - 4, y + 9, 0x100 | 5);
        sound(b, ENG_SFX_TICK, 1);
        return;
    }
    if (k == DBL) {
        b->cell[i] = BAL, b->squash[i] = 4; /* drawn squashed for 3 frames */
        add_fx(b, F_STRIP, x, y, r);
        unsigned pts = combo_up(b);
        add_fx(b, F_SCORE, x + 2, y - 18, (int)pts | (b->combo >= 10) << 24);
        sound(b, ENG_SFX_TICK, 1);
        return;
    }
    b->cell[i] = NONE;
    --b->left;
    lean(b, i, 1);
    add_fx(b, F_POP, x, y, k == STAR ? 7 | i << 8 : r | i << 8);
    unsigned pts = combo_up(b);
    if (k == STAR) {
        int near[6], key[6], n = around(r, c, near), m = 0;
        /* round the ring by angle from the upper left, clockwise, as the design's chain runs */
        for (int q = 0; q < n; ++q) {
            int j = near[q];
            if (b->cell[j] != BAL && b->cell[j] != DBL) continue;
            int a = eng_atan2((cell_y(j / P_COLS) - y) * 64,
                              (cell_xi(j / P_COLS, j % P_COLS) - x) * 64) &
                    1023;
            a = a > 512 ? a - 1024 : a;
            int at = m++;
            for (; at && key[at - 1] > a; --at)
                key[at] = key[at - 1], b->chain[at] = b->chain[at - 1];
            key[at] = a, b->chain[at] = j;
        }
        b->chain_n = m, b->chain_t = 2;
        b->star_fx = add_fx(b, F_SCORE, x, y - 29, (int)pts | 1 << 24);
        fx_new(b, F_CALL, x, y - 44, C_GRAND, 3); /* staggered: each its own frame to appear */
        fx_new(b, F_STARS, x, y, 0, 5);
        b->allon_t = 60;
        for (int p = 0; p < 4; ++p) hop(b, p, p == 0 || p == 3 ? 2 : -2, 2);
        sound(b, ENG_SFX_TAP, 2);
        return;
    }
    add_fx(b, F_SCORE, x + 3, y - 17, (int)pts | (b->combo >= 10) << 24);
    if ((b->tk[0].kind == 0 || b->tk[1].kind == 0) && eng_rng(&b->rs) % 8 == 0) {
        int t = b->tk[0].kind ? 1 : 0;
        b->tk[t].kind = 1 + (int)(eng_rng(&b->rs) % 5), b->tk[t].x = x, b->tk[t].y = FX(y),
        b->tk[t].t = 0;
    }
    sound(b, ENG_SFX_TAP, 1);
}

/* One of the star's ring, popped by the chain: points, no ticket. */
static void chain_pop(struct pstall *b, int i) {
    int r = i / P_COLS, x = cell_xi(r, i % P_COLS), y = cell_y(r);
    if (b->cell[i] != BAL && b->cell[i] != DBL) return;
    b->cell[i] = NONE, b->squash[i] = 0;
    --b->left;
    add_fx(b, F_POP, x, y, r | i << 8);
    unsigned pts = combo_up(b);
    if (b->star_fx >= 0 && b->fx[b->star_fx].kind == F_SCORE) {
        struct effect *e = &b->fx[b->star_fx];
        e->v += (int)pts;
        fx_mark(b, e);
    }
    eng_sfx(ENG_SFX_TAP);
}

static void knock(struct pstall *b, int side, int at) {
    if (side < 2) {
        add_fx(b, F_KNOCK, side ? 354 : 21, at, side);
        add_fx(b, F_DUST, side ? 349 : 26, at + 2, 0x200 | 5);
    } else {
        add_fx(b, F_KNOCK, at, 53, 2);
        add_fx(b, F_DUST, at, 58, 0x200 | 5);
        int k = (at - 25 + 12) / 25;
        k = k < 0 ? 0 : k > 13 ? 13 : k;
        int other = at > 25 + 25 * k ? k + 1 : k - 1;
        b->flip = 1u << k | (other >= 0 && other < 14 ? 1u << other : 0), b->flip_t = 6;
    }
    sound(b, ENG_SFX_TICK, 1);
}

/* The way out of a trampoline touch by where it hit: the middle straight up. */
static int off_angle(int off, int w) {
    int a = 768 + off * P_TILT / (w / 2 + P_RB);
    return a < 768 - P_TILT ? 768 - P_TILT : a > 768 + P_TILT ? 768 + P_TILT : a;
}

/* A ball's travel this step, swept: the first thing its path meets (a cell within two of its
 * place on the lattice, a wall, the beam, the mat's top) stops it there and turns it, four times a
 * step at most. Positions are 16.16; the tests run in 1/64 px. */
static void travel(struct pstall *b, struct ball *o) {
    fx spd = b->slow_t ? fx_mul(b->spd, FX(0.6)) : b->spd;
    int w = pad_w(b);
    for (int rem = 1024, k = 0; k < 4 && rem > 0 && !o->stuck; ++k) {
        fx vx = fx_mul(spd, eng_cos(o->ang)) * rem >> 10;
        fx vy = fx_mul(spd, eng_sin(o->ang)) * rem >> 10;
        int qx = vx >> 10, qy = vy >> 10, bx = o->x >> 10, by = o->y >> 10;
        int t = 1025, what = -1, d, r0 = (FX_INT(o->y) - P_Y0 + P_PY / 2) / P_PY;
        for (int r = r0 - 2; r <= r0 + 2; ++r) {
            int c0 = (FX_INT(o->x) - P_X0 - (r & 1) * P_PX / 2 + P_PX / 2) / P_PX;
            for (int c = c0 - 2; r >= 0 && r < P_ROWS && c <= c0 + 2; ++c) {
                if (c < 0 || c >= cols(r) || !b->cell[r * P_COLS + c]) continue;
                int u = sweep(bx - (cell_x(r, c) >> 10), by - cell_y(r) * 64, qx, qy);
                if (u < t) t = u, what = r * P_COLS + c;
            }
        }
        if (qx < 0 && (d = bx - (P_L + P_RB) * 64) + qx < 0 && (d <= 0 ? 0 : d * 1024 / -qx) < t)
            t = d <= 0 ? 0 : d * 1024 / -qx, what = -2;
        if (qx > 0 && (d = (P_R - P_RB) * 64 - bx) - qx < 0 && (d <= 0 ? 0 : d * 1024 / qx) < t)
            t = d <= 0 ? 0 : d * 1024 / qx, what = -5;
        if (qy < 0 && (d = by - (P_TOP + P_RB) * 64) + qy < 0 && (d <= 0 ? 0 : d * 1024 / -qy) < t)
            t = d <= 0 ? 0 : d * 1024 / -qy, what = -3;
        if (qy > 0 && (d = (P_MAT - P_RB) * 64 - by) > -64 && d <= qy && /* a pixel's grace */
            (d <= 0 ? 0 : d * 1024 / qy) < t) {
            int u = d <= 0 ? 0 : d * 1024 / qy, off = FX_INT(o->x + (vx >> 5) * u / 32) - b->pad;
            if (off >= -w / 2 - P_RB && off <= w / 2 + P_RB) t = u, what = -4;
        }
        if (t > 1024) t = 1024;
        o->x += (vx >> 5) * t / 32;
        o->y += (vy >> 5) * t / 32;
        rem -= rem * t >> 10;
        if (what >= 0) { /* off the balloon's face: its normal is the mirror */
            int r = what / P_COLS, nx = (o->x - cell_x(r, what % P_COLS)) >> 10;
            int n = eng_atan2((o->y >> 10) - cell_y(r) * 64, nx);
            o->ang = steep(2 * n - o->ang + 512);
            hit(b, what, FX_INT(o->x));
        } else if (what == -2 || what == -5) {
            o->ang = steep(512 - o->ang);
            knock(b, what == -5, FX_INT(o->y));
        } else if (what == -3) {
            o->ang = steep(-o->ang);
            knock(b, 2, FX_INT(o->x));
        } else if (what == -4) {
            int off = FX_INT(o->x) - b->pad;
            o->ang = off_angle(off, w);
            o->bounce = 4, o->cx = FX_INT(o->x);
            dip(b, FX_INT(o->x), 8);
            if (b->combo) b->combo = 0, mark(b, 128, 9, 120, 25);
            if (b->glue_n) { /* GLUE: it stays where it landed */
                o->stuck = 1, o->held = 0, o->y = FX(P_REST), o->cx = off;
                --b->glue_n;
                band_mark(b);
            }
            sound(b, ENG_SFX_TICK, 1);
        }
    }
}

static void over(struct pstall *b) {
    b->mode = OVER, b->t = 0;
    b->best_new = b->score && b->score > b->save.best;
    if (b->score > b->save.best) b->save.best = b->score;
    if ((unsigned)b->level > b->save.level) b->save.level = (unsigned)b->level;
    /* the game's end, not a frame of play: a new best reaches the card now */
    if (b->save.best + b->save.level != b->saved &&
        eng_save("popstall", &b->save, sizeof b->save, P_MAGIC))
        b->saved = b->save.best + b->save.level;
    b->ball[0].live = b->ball[1].live = b->ball[2].live =
        0; /* the rows dim and the ticket rises in step */
}

/* The last ball in play went behind the counter: a life, and the next ball onto the mat. */
static void missed(struct pstall *b, int x) {
    --b->lives;
    sound(b, ENG_SFX_ALERT, 1);
    add_fx(b, F_DUST, x, 289, 0x300 | 7);
    b->brown_t = 30, b->lost_t = 30, b->combo = 0;
    b->wide_t = b->slow_t = b->glue_n = 0;
    for (int k = 0; k < 4; ++k) hop(b, k, -2, 0);
    mark(b, 120, 9, 230, 25);
    if (!b->lives) {
        over(b);
        return;
    }
    ready(b);
    b->fresh = 0;
    b->ball[0].bounce = 4, b->ball[0].cx = FX_INT(b->ball[0].x);
    dip(b, FX_INT(b->ball[0].x), 8);
}

static void caught(struct pstall *b, int kind) {
    int call = kind - 1;
    if (kind == T_WIDE) b->wide_t = 20 * ENG_HZ;
    if (kind == T_SLOW) b->slow_t = 10 * ENG_HZ;
    if (kind == T_GLUE) b->glue_n = 3;
    if (kind == T_LIFE) {
        if (b->lives < 5) {
            ++b->lives, b->gain_t = 40;
            if (b->lives > b->slots) b->slots = b->lives;
        } else
            score(b, (unsigned)(50 * b->level));
        mark(b, 270, 9, 80, 25);
    }
    if (kind == T_TRIO) {
        struct ball *src = 0;
        for (int k = 0; k < 3 && !src; ++k)
            if (b->ball[k].live) src = &b->ball[k];
        for (int k = 0, n = 0; src && k < 3 && n < 2; ++k)
            if (!b->ball[k].live) {
                struct ball *o = &b->ball[k];
                int a = src->stuck ? 768 : src->ang;
                *o = *src;
                o->stuck = 0, o->bounce = 0;
                if (src->stuck) o->y = FX(P_REST - 1);
                o->ang = steep(a + (n++ ? P_TRIO : -P_TRIO));
            }
    }
    add_fx(b, F_CALL, b->pad, 256, call);
    add_fx(b, F_SPARK, b->pad, 280, kind);
    dip(b, b->dip_t < 12 ? b->dip_x : b->pad, 3);
    hop(b, (int)(eng_rng(&b->rs) & 3), 3, 0);
    sound(b, ENG_SFX_TAP, 2);
}

static void launch(struct pstall *b, struct ball *o) {
    int w = pad_w(b);
    o->stuck = 0;
    o->ang = off_angle(FX_INT(o->x) - b->pad, w);
    if (o->ang == 768) o->ang = 768 + b->side * P_TILT * (P_PAD_W / 3) / (w / 2 + P_RB);
}

/* What a ball's paint depends on, as one word: alive, its pixel, its frame, stuck or not. */
static int ball_look(const struct ball *o) {
    if (!o->live) return 0;
    int x =
        o->bounce == 4 ? o->cx : FX_INT(o->x + FX(1) / 2); /* the squash is drawn where it hit */
    return 1 | (x & 511) << 1 | (FX_INT(o->y + FX(1) / 2) & 511) << 10 | o->spin << 19 |
           o->bounce << 21 | o->stuck << 24;
}

/* The ball's box with its trail and motes, as a rect. */
static void ball_box(const struct pstall *b, const struct ball *o, int *box) {
    int x = FX_INT(o->x), y = FX_INT(o->y), m = b->slow_t ? 14 : 11;
    box[0] = x - m, box[1] = y - m, box[2] = x + m + 1, box[3] = y + m + 1;
    if (o->bounce == 4)
        box[1] = box[1] < P_REST - 10 ? box[1] : P_REST - 10, box[3] = P_REST + 13,
        box[0] = o->cx - 12 < box[0] ? o->cx - 12 : box[0],
        box[2] = o->cx + 13 > box[2] ? o->cx + 13 : box[2];
    if (b->mode == PLAY && !o->stuck) {
        int tx = x - (22 * eng_cos(o->ang) >> 16), ty = y - (22 * eng_sin(o->ang) >> 16);
        box[0] = tx - 5 < box[0] ? tx - 5 : box[0], box[1] = ty - 5 < box[1] ? ty - 5 : box[1];
        box[2] = tx + 6 > box[2] ? tx + 6 : box[2], box[3] = ty + 6 > box[3] ? ty + 6 : box[3];
    }
}

static int fade_row(int r) { return r * 16 / (P_ROWS - 1); }

/* The level clear's banner's top at frame t: it drops from behind the beam to 116 with one 3 px
 * bounce. */
static int banner_y(int t) {
    static const signed char drop[10] = { -54, -40, -22, -5, 10, 22, 31, 63, 60, 60 };
    return t < 0 ? P_TOP - 54 : t < 9 ? P_TOP + drop[t] : 116;
}
/* The game over's ticket, this far below its place at frame t: up from behind the counter in 10
 * frames after the rows dim. */
static int ticket_dy(int t) { return t < 5 ? 230 : t < 15 ? (15 - t) * 23 : 0; }

/* The level clear's confetti piece k, the same every time: x, y, w, h and its colour's index. Four
 * come a frame once the banner has landed. */
static void confetto_next(int first, int *p) { /* the next piece, or the first again */
    static const unsigned char sz[4][2] = { { 3, 2 }, { 2, 3 }, { 3, 3 }, { 4, 2 } };
    static const unsigned seed = 0x1b873593u;
    if (first) p[5] = (int)seed;
    unsigned r = (unsigned)p[5];
    p[0] = 34 + (int)(eng_rng(&r) % 304), p[1] = 70 + (int)(eng_rng(&r) % 207);
    int z = (int)(eng_rng(&r) & 3);
    p[2] = sz[z][0], p[3] = sz[z][1], p[4] = (int)(eng_rng(&r) % 9), p[5] = (int)r;
}
static void confetto(int k, int *p) {
    for (int n = 0; n <= k; ++n) confetto_next(!n, p);
}

static void confetto_mark(struct pstall *b, int k) {
    int p[6];
    confetto(k, p);
    mark(b, p[0], p[1], p[2] + 1, p[3] + 1);
}

static unsigned lit(const struct pstall *b) {
    unsigned all = ~0u, m = 0;
    if (b->mode == OVER || b->brown_t) return 0;
    if (b->allon_t || b->mode == CLEAR || b->combo >= 10) return all ^ (b->flip_t ? b->flip : 0);
    for (int i = 0; i < P_BULBS; ++i)
        if (b->combo >= 5 ? (i + b->chase) % 3 != 0 : !(i & 1)) m |= 1u << i;
    return m ^ (b->flip_t ? b->flip : 0);
}
/* Bulb i's place: 14 along the beam, then 9 down each wall (fair.py beam_bulbs, side_bulbs). */
static void bulb_box(int i, int *x, int *y) {
    *x = i < 14 ? 25 + 25 * i : i < 23 ? 5 : 370;
    *y = i < 14 ? 52 : 92 + 22 * (i < 23 ? i - 14 : i - 23);
}
static void bulbs_mark(struct pstall *b, unsigned was, unsigned now) {
    unsigned d = was ^ now;
    int lo[3] = { 999, 999, 999 }, hi[3] = { -1, -1, -1 };
    for (int i = 0; i < P_BULBS; ++i)
        if (d >> i & 1) {
            int x, y, g = i < 14 ? 0 : i < 23 ? 1 : 2, v;
            bulb_box(i, &x, &y);
            v = g ? y : x;
            lo[g] = v < lo[g] ? v : lo[g], hi[g] = v > hi[g] ? v : hi[g];
        }
    if (hi[0] >= 0) mark(b, lo[0] - 7, 45, hi[0] - lo[0] + 15, 15);
    if (hi[1] >= 0) mark(b, 0, lo[1] - 7, 13, hi[1] - lo[1] + 15);
    if (hi[2] >= 0) mark(b, 362, lo[2] - 7, 13, hi[2] - lo[2] + 15);
}

/* Each effect a frame on. One whose next frame would repaint more of the stall than a frame has
 * room for (P_FX_AREA, with what the balls and the rest marked) waits a frame instead, its picture
 * held: a crowd of pops slows a little rather than a frame running long. Each frame starts from
 * another effect, and the first always moves, so none waits for long. */
static void effects(struct pstall *b) {
    for (int n = 0, k = b->frame % P_FX; n < P_FX; ++n, k = (k + 1) % P_FX) {
        struct effect *e = &b->fx[k];
        if (!e->kind) continue;
        const short *x = fx_box[e->kind];
        if (n && x[2] * x[3] > fx_room(b) && fx_paints(e, e->t + 1)) continue;
        ++e->t;
        if (e->kind == F_POP && e->t == 4) lean(b, e->v >> 8, 0);
        if (e->kind == F_BAG) {
            static const unsigned char swing[10] = { 1, 2, 3, 3, 2, 2, 1, 1, 0, 0 };
            int i = e->v >> 1;
            b->lean_x[i] =
                (signed char)(e->t >= 0 && e->t < 10 ? (e->v & 1 ? -1 : 1) * swing[e->t] : 0);
        }
        int moving = fx_paints(e, e->t);
        if (e->t >= fx_life[e->kind]) {
            fx_mark(b, e);
            if (e->kind == F_BAG) b->lean_x[e->v >> 1] = 0;
            if (k == b->star_fx) b->star_fx = -1;
            e->kind = 0;
        } else if (moving)
            fx_mark(b, e);
    }
    for (int i = 0; i < P_CELLS; ++i)
        if (b->squash[i] && !--b->squash[i]) {
            int r = i / P_COLS;
            mark(b, cell_xi(r, i % P_COLS) - 11, cell_y(r) - 11, 23, 26);
        }
}

static void hops(struct pstall *b) {
    for (int k = 0; k < 4; ++k) {
        if (!b->hop_t[k]) continue;
        int before = hop_dy(b, k);
        if (b->hop_wait[k])
            --b->hop_wait[k];
        else
            --b->hop_t[k];
        if (hop_dy(b, k) != before || !b->hop_t[k]) {
            mark(b, plush_x[k] - 9, plush_y[k] - 20, 18,
                 42); /* a prize: 14 x 30 from 13 above, and its hop */
        }
    }
}

static void tickets(struct pstall *b) {
    for (int k = 0; k < 2; ++k) {
        if (!b->tk[k].kind) continue;
        int y = FX_INT(b->tk[k].y), x = b->tk[k].x;
        mark(b, x - 16, y - 9, 33, 20);
        b->tk[k].y += FX(1.5), ++b->tk[k].t;
        int ny = FX_INT(b->tk[k].y), w = pad_w(b);
        if (b->mode == PLAY && ny + 6 >= P_MAT && ny - 6 <= P_MAT + 4 && x >= b->pad - w / 2 - 13 &&
            x <= b->pad + w / 2 + 13) {
            caught(b, b->tk[k].kind);
            b->tk[k].kind = 0;
        } else if (ny > 310 || b->mode != PLAY)
            b->tk[k].kind = 0;
        else
            mark(b, x - 16, ny - 9, 33, 20);
    }
}

static int step(void *state, const struct eng_input *in) {
    struct pstall *b = state;
    unsigned was = b->bulbs;
    int balls[3][4] = { { 0 } }, look[3], w0 = pad_w(b), pad0 = b->pad, mode0 = b->mode,
        slow0 = !b->slow_t;
    for (int k = 0; k < 3; ++k) {
        if (b->ball[k].live) ball_box(b, &b->ball[k], balls[k]);
        look[k] = ball_look(&b->ball[k]);
        if (b->ball[k].bounce) --b->ball[k].bounce;
    }
    if (b->dip_t < 12) ++b->dip_t, tramp_mark(b); /* a hit this step starts it again at 0 */
    if (b->paused) { /* back from the pause: the HUD steps up, the sign goes, the bulbs return */
        b->paused = 0;
        mark(b, 0, 0, ENG_W, 60);
        mark(b, 100, 56, 178, 136);
        bulbs_mark(b, 0, ~0u);
    }
    int pad = b->pad + in->wheel * P_WHEEL, wv = pad_w(b);
    if (in->held & ENG_TOUCH) pad = in->tx;
    if (in->held & ENG_PREV) pad -= P_KEY;
    if (in->held & ENG_NEXT) pad += P_KEY;
    pad = pad < P_L + wv / 2 ? P_L + wv / 2 : pad > P_R - wv / 2 ? P_R - wv / 2 : pad;
    if (b->mode != OVER) b->pad = pad;
    if (b->runs && !--b->wait) eng_sfx(b->seq), b->wait = P_SEQ, --b->runs;
    ++b->frame;
    if (b->mode == READY || b->mode == CLEAR || b->mode == INFLATE) /* the ball rides the mat */
        b->ball[0].x = FX(b->pad + b->side * P_PAD_W / 3);
    if (b->mode == READY) {
        struct ball *o = &b->ball[0];
        if (in->pressed & ENG_CENTRE) {
            b->mode = PLAY;
            o->ang = 768 + b->side * P_TILT * (P_PAD_W / 3) / (P_PAD_W / 2 + P_RB);
            band_mark(b);
        }
    } else if (b->mode == PLAY) {
        int alive = 0, last_x = b->pad;
        for (int k = 0; k < 3; ++k) {
            struct ball *o = &b->ball[k];
            if (!o->live) continue;
            if (o->stuck) {
                o->x = FX(b->pad + o->cx), o->y = FX(P_REST);
                if (in->pressed & ENG_CENTRE || ++o->held >= 2 * ENG_HZ) launch(b, o), band_mark(b);
            } else
                travel(b, o);
            if (FX_INT(o->y) > P_LOST) o->live = 0, last_x = FX_INT(o->x);
            alive += o->live;
        }
        if (b->chain_n && !--b->chain_t) {
            chain_pop(b, b->chain[0]);
            for (int k = 1; k < b->chain_n; ++k) b->chain[k - 1] = b->chain[k];
            --b->chain_n, b->chain_t = 2;
        }
        if (!b->left && !b->chain_n) { /* cleared: the banner drops, the next rows inflate */
            b->mode = CLEAR, b->t = 0;
            score(b, (unsigned)(100 * b->level));
            ++b->level;
            ball_rest(b);
            b->tk[0].kind = b->tk[1].kind = 0;
            for (int k = 0; k < 4; ++k) hop(b, k, 3, k);
            sound(b, ENG_SFX_TICK, 3);
            for (int i = 0; i < P_CELLS;
                 ++i) /* the bags go with the level (pops finish on their own) */
                if (b->cell[i] == SACK)
                    mark(b, cell_xi(i / P_COLS, i % P_COLS) - 11, cell_y(i / P_COLS) - 12, 24, 28);
        } else if (!alive)
            missed(b, last_x);
    } else if (b->mode == CLEAR) {
        ++b->t;
        if (b->t <= 9 ||
            b->t == 90) { /* the banner dropping, its old place and its new, then gone */
            int was = banner_y(b->t - 1), now = b->t == 90 ? was : banner_y(b->t);
            mark(b, 62, P_TOP, 252, (was > now ? was : now) + 62 - P_TOP);
        }
        int in = (b->t - 9) * 4,
            out = (b->t - 91) * 4; /* four pieces of confetti a frame, in and out */
        for (int k = in - 4; k < in && k < 32; ++k)
            if (k >= 0) confetto_mark(b, k);
        for (int k = out - 4; k < out && k < 32; ++k)
            if (k >= 0) confetto_mark(b, k);
        if (b->t == 100) { /* the next level's rows to come */
            b->mode = INFLATE, b->t = 0;
            build(b, -1);
        }
    } else if (b->mode == INFLATE) {
        ++b->t;
        for (int r = 0; r < P_ROWS; ++r)
            if (b->t > fade_row(r) && b->t <= fade_row(r) + 5) mark(b, 24, cell_y(r) - 14, 327, 30);
        if (b->t == 1) band_mark(b);
        if (b->t >= fade_row(P_ROWS - 1) + 5) ready(b);
    } else {
        ++b->t;
        if (b->t <= 4) mark(b, 24, cell_y(2 * b->t - 2) - 12, 327, 2 * P_PY + 10);
        if (b->t > 4 && b->t <= 15) { /* the ticket sliding up: its old place and its new */
            int was = ticket_dy(b->t - 1), now = ticket_dy(b->t);
            mark(b, 47, 88 + now, 281, 150 + was - now);
        }
        if (b->t > 15 && b->t <= 26 && b->best_new) mark(b, 132, 162, 112, 36);
        if (b->t > 15 && in->pressed & ENG_CENTRE) new_game(b);
    }
    /* timers */
    if (b->wide_t && !--b->wide_t) {
        tramp_mark(b);
        mark(b, b->pad - P_WIDE_W / 2 - 2, 272, P_WIDE_W + 5, 23);
    }
    if (b->wide_t && b->wide_t < 3 * ENG_HZ && b->wide_t % 10 == 0) tramp_mark(b);
    if (b->slow_t) --b->slow_t;
    if (b->allon_t) --b->allon_t;
    if (b->brown_t) --b->brown_t;
    if (b->flip_t) --b->flip_t;
    if (b->combo >= 5 && b->frame % 8 == 0) ++b->chase;
    if (b->swell) --b->swell, mark(b, 128, 9, 120, 25);
    if (b->lost_t && !--b->lost_t) mark(b, 270, 9, 80, 25);
    if (b->gain_t && !--b->gain_t) mark(b, 270, 9, 80, 25);
    effects(b);
    hops(b);
    tickets(b);
    /* the bulbs change P_BULBS_STEP a frame at most, the rest the frames after: a chase starting, a
     * brown-out or every bulb coming on is a ripple, not one frame repainting the stall's edges */
    unsigned want = lit(b), now = was;
    for (int i = 0, n = 0; i < P_BULBS && n < P_BULBS_STEP; ++i) {
        if ((want ^ now) >> i & 1) now ^= 1u << i, ++n; /* the beam's first, then down the walls */
    }
    if (now != was) bulbs_mark(b, was, now);
    if (b->pad != pad0 || pad_w(b) != w0) {
        int lo = pad0 < b->pad ? pad0 : b->pad, hi = pad0 < b->pad ? b->pad : pad0,
            ww = w0 > pad_w(b) ? w0 : pad_w(b);
        mark(b, lo - ww / 2 - 2, 272, hi - lo + ww + 5, 23);
    }
    for (int k = 0; k < 3; ++k) {
        struct ball *o = &b->ball[k];
        int box[4];
        if (o->live && !o->stuck && b->mode == PLAY) {
            int dx = o->ang > 256 && o->ang < 768 ? -1 : 1;
            if (!b->slow_t || b->frame & 1) o->spin = (o->spin + dx) & 3;
        }
        if (o->live) ball_box(b, o, box);
        /* a ball that looks as it did, where it was, with the same trail and motes: nothing to
         * paint */
        if (look[k] == ball_look(o) && mode0 == b->mode && slow0 == !b->slow_t &&
            !(b->slow_t && o->live))
            continue;
        if (o->live && balls[k][2] > balls[k][0]) {
            int *p = balls[k];
            int u0 = p[0] < box[0] ? p[0] : box[0], v0 = p[1] < box[1] ? p[1] : box[1];
            int u1 = p[2] > box[2] ? p[2] : box[2], v1 = p[3] > box[3] ? p[3] : box[3];
            if ((u1 - u0) * (v1 - v0) <=
                2 * ((p[2] - p[0]) * (p[3] - p[1]) + (box[2] - box[0]) * (box[3] - box[1])))
                mark(b, u0, v0, u1 - u0, v1 - v0);
            else
                mark(b, p[0], p[1], p[2] - p[0], p[3] - p[1]),
                    mark(b, box[0], box[1], box[2] - box[0], box[3] - box[1]);
        } else if (o->live)
            mark(b, box[0], box[1], box[2] - box[0], box[3] - box[1]);
        else if (balls[k][2] > balls[k][0])
            mark(b, balls[k][0], balls[k][1], balls[k][2] - balls[k][0], balls[k][3] - balls[k][1]);
    }
    b->bulbs = now;
    flush_marks(b);
    return 1;
}

/* A best is written at the game's end; one that could not be then is tried again here. The
 * stall's own pause look is drawn from here until the next step. */
static void pause(void *state) {
    struct pstall *b = state;
    b->paused = 1;
    if (b->mode == OVER && b->save.best + b->save.level != b->saved &&
        eng_save("popstall", &b->save, sizeof b->save, P_MAGIC))
        b->saved = b->save.best + b->save.level;
}

/* ---- drawing ---- */

static inline unsigned *at(const struct eng_surface *s, int x, int y) {
    return s->px + (y + s->oy) * s->stride + x + s->ox;
}
static inline unsigned blend(unsigned dst, unsigned rgb, unsigned a) { /* a of 256 */
    unsigned rb = ((rgb & 0xff00ff) * a + (dst & 0xff00ff) * (256 - a)) >> 8 & 0xff00ff;
    unsigned g = ((rgb & 0xff00) * a + (dst & 0xff00) * (256 - a)) >> 8 & 0xff00;
    return 0xff000000 | rb | g;
}
/* x, y, w, h in rgb, four at a time: eng_fill's work for less. */
static void fill(struct eng_surface *s, int x, int y, int w, int h, unsigned rgb) {
    int c[4];
    if (!eng_clip(s, x, y, w, h, c)) return;
    rgb |= 0xff000000;
    for (int j = c[1]; j < c[3]; ++j) {
        unsigned *p = at(s, c[0], j);
        int n = c[2] - c[0];
        for (; n >= 4; n -= 4, p += 4) p[0] = rgb, p[1] = rgb, p[2] = rgb, p[3] = rgb;
        while (n-- > 0) *p++ = rgb;
    }
}

/* A premultiplied 0xAARRGGBB pixel v laid over d. */
static inline unsigned lay(unsigned d, unsigned v) {
    unsigned ia = 255 - (v >> 24);
    return 0xff000000 | ((v & 0xffffff) + (((d & 0xff00ff) * ia >> 8) & 0xff00ff) +
                         (((d & 0xff00) * ia >> 8) & 0xff00));
}

/* rgb a 256ths of the way up from black: the game over's dimmed rows. */
static unsigned dim(unsigned rgb, int a) {
    return a >= 256 ? rgb
                    : ((rgb & 0xff00ff) * (unsigned)a >> 8 & 0xff00ff) |
                          ((rgb & 0xff00) * (unsigned)a >> 8 & 0xff00);
}

/* n pixels of the stall at rest from its indices q, through the palette, four at a time. */
static void indexed(unsigned *p, const unsigned char *q, int n, const unsigned *pal) {
    for (; n >= 4; n -= 4, p += 4, q += 4)
        p[0] = pal[q[0]], p[1] = pal[q[1]], p[2] = pal[q[2]], p[3] = pal[q[3]];
    while (n-- > 0) *p++ = pal[*q++];
}

/* The stall at rest, rows y0 to y1 of the paint. */
static void stall(const struct pstall *b, struct eng_surface *s, int y0, int y1) {
    int c[4];
    if (!eng_clip(s, 0, y0, ENG_W, y1 - y0, c)) return;
    if (!b->art_ok) { /* no art: flat stand-ins in the stall's colours */
        for (int y = c[1]; y < c[3]; ++y) {
            unsigned rgb = y < 56           ? (y < 46 ? 0xd62c42 : 0x8a5a34)
                           : y >= P_COUNTER ? (y < 299 ? K_LEDGE : K_CREAM)
                                            : 0xb5885a;
            eng_fill(s, c[0], y, c[2] - c[0], 1, rgb);
            if (y >= 56 && y < P_COUNTER)
                eng_fill(s, 0, y, P_L, 1, K_WOOD_1), eng_fill(s, P_R, y, ENG_W - P_R, 1, K_WOOD_1);
        }
        return;
    }
    const unsigned *pal = b->art + ART_PAL / 4;
    const unsigned char *bg = (const unsigned char *)b->art + ART_BG,
                        *cork = bg - ART_BG + ART_BOARD;
    const unsigned short *rows = (const unsigned short *)((const char *)b->art + ART_FLECK_ROWS);
    const unsigned char *flecks = (const unsigned char *)b->art + ART_FLECKS;
    for (int y = c[1]; y < c[3]; ++y) {
        const unsigned char *q = bg + y * ENG_W;
        unsigned *row = at(s, 0, y);
        if (y < BOARD_Y0 || y >= BOARD_Y1 || c[2] <= BOARD_X0 || c[0] >= BOARD_X1) {
            indexed(row + c[0], q + c[0], c[2] - c[0], pal);
            continue;
        }
        /* the cork: its row's color filled, then its flecks; the walls' and beam's shade either
         * side */
        int f0 = c[0] > BOARD_X0 ? c[0] : BOARD_X0, f1 = c[2] < BOARD_X1 ? c[2] : BOARD_X1;
        indexed(row + c[0], q + c[0], f0 - c[0], pal);
        indexed(row + f1, q + f1, c[2] - f1, pal);
        unsigned v = pal[cork[y - BOARD_Y0]], *p = row + f0;
        int n = f1 - f0;
        for (; n >= 4; n -= 4, p += 4) p[0] = v, p[1] = v, p[2] = v, p[3] = v;
        while (n--) *p++ = v;
        for (int k = rows[y - BOARD_Y0]; k < rows[y - BOARD_Y0 + 1]; ++k) {
            const unsigned char *e = flecks + 4 * k;
            int x = e[0] | e[1] << 8;
            if (x >= f0 && x < f1) row[x] = pal[e[2]];
        }
    }
}

/* A sprite with its anchor at x, y; its colours scaled by a (256: as drawn). Its rows are runs
 * (export_art.py runs): a clear run is skipped, a solid one copied, a blended one laid over what is
 * there by its coverage, each cut to the columns being painted. */
static void sprite(const struct pstall *b, struct eng_surface *s, int id, int x, int y, int a) {
    const struct art_pic *pic = (const struct art_pic *)((const char *)b->art + ART_SPRITES) + id;
    int c[4];
    x += pic->dx, y += pic->dy;
    if (!b->art_ok || !eng_clip(s, x, y, pic->w, pic->h, c)) return;
    const unsigned *src = b->art + pic->off / 4;
    int whole = c[0] == x && c[2] == x + pic->w && a >= 256; /* every column: no run is cut */
    for (int j = c[1]; j < c[3]; ++j) {
        const unsigned *q = src + src[j - y];
        unsigned *row = at(s, 0, j), *p = row + x;
        for (int u = x; u < c[2];) {
            unsigned head = *q++, kind = head >> 16;
            int n = (int)(head & 0xffff);
            if (whole) { /* the quick way: each run as it comes */
                if (kind == 1) {
                    for (; n >= 4; n -= 4, p += 4, q += 4)
                        p[0] = q[0], p[1] = q[1], p[2] = q[2], p[3] = q[3];
                    while (n--) *p++ = *q++;
                } else if (kind)
                    for (; n--; ++p, ++q) *p = lay(*p, *q);
                else
                    p += n;
                u = (int)(p - row);
                continue;
            }
            int i0 = u < c[0] ? c[0] : u, i1 = u + n > c[2] ? c[2] : u + n;
            const unsigned *w = q + (i0 - u);
            if (kind) q += n;
            u += n;
            for (unsigned *d = row + i0; kind && d < row + i1; ++d, ++w) {
                unsigned v = a < 256 ? (*w & 0xff000000) | dim(*w & 0xffffff, a) : *w;
                *d = kind == 1 ? v : lay(*d, v);
            }
        }
    }
}

/* A mask in rgb, its anchor at x, y. */
static void masked(const struct pstall *b, struct eng_surface *s, const struct art_pic *pic, int x,
                   int y, unsigned rgb) {
    int c[4];
    x += pic->dx, y += pic->dy;
    if (!b->art_ok || !eng_clip(s, x, y, pic->w, pic->h, c)) return;
    const unsigned char *src = (const unsigned char *)b->art + pic->off;
    for (int j = c[1]; j < c[3]; ++j) {
        const unsigned char *q = src + (j - y) * pic->w + (c[0] - x);
        unsigned *p = at(s, c[0], j);
        for (int i = c[0]; i < c[2]; ++i, ++p) {
            unsigned a = *q++;
            if (a) *p = a == 255 ? 0xff000000 | rgb : blend(*p, rgb, a + (a >> 7));
        }
    }
}
static void mask(const struct pstall *b, struct eng_surface *s, int id, int x, int y,
                 unsigned rgb) {
    masked(b, s, (const struct art_pic *)((const char *)b->art + ART_MASKS) + id, x, y, rgb);
}

/* ---- type: Bungee from the art's glyphs ---- */

static const struct art_face *face(const struct pstall *b, int px, int edged) {
    const struct art_face *f = (const struct art_face *)((const char *)b->art + ART_FACES);
    for (int k = 0; k < ART_FACE_COUNT; ++k)
        if (f[k].px == px && f[k].edged == edged) return &f[k];
    return f;
}
static const struct art_glyph *glyph(const struct pstall *b, const struct art_face *f, char ch) {
    const struct art_glyph *g = (const struct art_glyph *)((const char *)b->art + f->glyphs);
    for (int k = 0; k < f->n; ++k)
        if (g[k].ch == ch) return &g[k];
    return 0;
}
static int text_w(const struct pstall *b, int px, const char *t) { /* 1/16 px */
    if (!b->art_ok) return 0;
    const struct art_face *f = face(b, px, 0);
    int w = 0;
    for (; *t; ++t) {
        const struct art_glyph *g = glyph(b, f, *t);
        w += g ? g->adv : 0;
    }
    return w;
}
/* t in Bungee px high, its middle row at y, centred on x (align 1) or from it (0); with an edge
 * of 1 px in edge when edge is not ~0u. Its width. */
static int type(const struct pstall *b, struct eng_surface *s, const char *t, int x, int y, int px,
                unsigned rgb, unsigned edge, int align) {
    const struct art_face *f = face(b, px, 0), *e = face(b, px, 1);
    int w = text_w(b, px, t);
    if (!b->art_ok)
        return eng_text(s, t, align ? x : x, y - px, 2 * px, px < 15 ? 15 : px, rgb, align);
    int x16 = align ? x * 16 + 8 - w / 2 : x * 16, base = y + (f->mid + 8) / 16;
    for (int pass = edge == ~0u; pass < 2; ++pass) {
        int pen = x16;
        for (const char *c = t; *c; ++c) {
            const struct art_glyph *g = glyph(b, f, *c), *ge = pass ? 0 : glyph(b, e, *c);
            const struct art_glyph *use = pass ? g : ge;
            if (use) {
                struct art_pic pic = { use->off, use->w, use->h, use->dx, use->dy };
                masked(b, s, &pic, (pen + 8) >> 4, base, pass ? rgb : edge);
            }
            pen += g ? g->adv : 0;
        }
    }
    return (w + 15) / 16;
}

/* v at out with a comma between each three digits; the end of it. */
static char *digits(char *out, unsigned v) {
    char d[16];
    int n = 0;
    do {
        if (n % 4 == 3) d[n++] = ',';
        d[n++] = (char)('0' + v % 10);
    } while (v /= 10);
    while (n) *out++ = d[--n];
    *out = 0;
    return out;
}
static char *put(char *out, const char *w) {
    while (*w) *out++ = *w++;
    *out = 0;
    return out;
}

/* ---- the scene ---- */

/* Row r's step of the inflate, 1 to 4 its small and bigger discs; 99 (any other time): whole. */
static int cell_shown(const struct pstall *b, int r) {
    return b->mode == INFLATE ? b->t - fade_row(r) : 99;
}

/* The balloons within reach of the paint, row by row from the top, so a lower row's balloons cover
 * the strings and shadows of the row above (each balloon's sprite carries its shadow). */
static void lattice(const struct pstall *b, struct eng_surface *s) {
    int r0 = (s->y0 - P_Y0 - 16) / P_PY, r1 = (s->y1 - P_Y0 + 14) / P_PY;
    r0 = r0 < 0 ? 0 : r0, r1 = r1 >= P_ROWS ? P_ROWS - 1 : r1;
    if (s->y1 < P_Y0 - 16 || s->y0 > cell_y(P_ROWS - 1) + 20) return;
    for (int r = r0; r <= r1; ++r) {
        int y = cell_y(r), step = cell_shown(b, r),
            a = b->mode == OVER && 2 * b->t > r ? 107 : 256; /* 0.42 */
        int c0 = (s->x0 - P_X0 - (r & 1) * 10 - 14) / P_PX,
            c1 = (s->x1 - P_X0 - (r & 1) * 10 + 14) / P_PX;
        c0 = c0 < 0 ? 0 : c0, c1 = c1 >= cols(r) ? cols(r) - 1 : c1;
        for (int c = c0; step > 0 && c <= c1; ++c) {
            int i = r * P_COLS + c, k = b->cell[i], x = cell_xi(r, c) + b->lean_x[i],
                yy = y + b->lean_y[i];
            if (!k || (b->mode == OVER && b->t >= 15 && x > 62 && x < 313 && yy > 96 && yy < 228))
                continue;               /* none, or under the game over's ticket */
            if (step <= 4 && k != SACK) /* inflating: a small disc, then a bigger */
                eng_disc(s, FX(x) + FX(1) / 2, FX(yy), step <= 2 ? FX(3) : FX(6),
                         row_col[k == STAR ? 7 : r]);
            else if (!b->art_ok)
                eng_disc(s, FX(x) + FX(1) / 2, FX(yy), FX(P_RC),
                         dim(k == SACK ? 0xb08a58 : row_col[k == STAR ? 7 : r], a));
            else
                sprite(b, s,
                       k == SACK   ? SP_SACK
                       : k == STAR ? SP_STAR
                                   : SP_BAL_0N + 4 * r +
                                         (k == DBL       ? 1
                                          : b->squash[i] ? 3
                                                         : 0),
                       x, yy, a);
        }
    }
}

static void dust(struct eng_surface *s, int x, int y, int t, int n, unsigned rgb, int up) {
    static const unsigned char jit[7] = { 1, 3, 0, 2, 4, 1, 3 },
                               rad[7] = { 22, 14, 30, 18, 26, 16, 24 };
    for (int i = 0; i < n; ++i) {
        int a = up ? 512 + (2 * i + 1) * 512 / (2 * n) : i * 1024 / n;
        int v = 3 * 16 + t * 16 * 10 / 8 + jit[i] * 16; /* 1/16 px */
        int r = rad[i] * (88 - 5 * t) / 80;             /* tenths of a px, shrinking */
        fx dx = v * eng_cos(a) >> 4,
           dy = v * eng_sin(a) / 10 * 6 >> 4; /* under 2^31: v is 300 at most */
        eng_disc(s, FX(x) + dx, FX(y) + dy, r * FX(1) / 10, rgb);
    }
}

static void shreds(struct eng_surface *s, int x, int y, int t, int n, unsigned col,
                   unsigned light) {
    /* t in hundredths of the way: fair.py shreds(), the jitter fixed */
    static const signed char ja[9] = { 20, -31, 12, -8, 40, -22, 5, 33, -15 };
    static const unsigned char v0[9] = { 12, 9, 14, 11, 15, 10, 13, 9, 12 },
                               v1[9] = { 10, 12, 8, 13, 9, 11, 12, 10, 8 };
    for (int i = 0; i < n; ++i) {
        int a = i * 1024 / n + ja[i];
        int v = v0[i] * 100 + t * v1[i]; /* hundredths */
        int px = x + v * eng_cos(a) / 6553600,
            py = y + v * 9 / 10 * eng_sin(a) / 6553600 + t * t / 1000;
        fill(s, px - 1, py - 1, 3, 2, i % 3 ? light : col); /* 3 x 2: too small to round */
    }
}

static void effect(const struct pstall *b, struct eng_surface *s, const struct effect *e) {
    const short *box = fx_box[e->kind];
    char t[24];
    if (e->x + box[0] >= s->x1 || e->x + box[0] + box[2] <= s->x0 || e->y + box[1] >= s->y1 ||
        e->y + box[1] + box[3] <= s->y0)
        return;
    int x = e->x, y = e->y, f = e->t;
    if (f < 0) return; /* not shown yet */
    if (e->kind == F_POP) {
        int r = e->v & 255, gold = r == 7;
        if (f < 2)
            gold ? sprite(b, s, SP_STAR_F, x, y, 256) : sprite(b, s, SP_BAL_0F + 4 * r, x, y, 256);
        else if (f < 4) {
            mask(b, s, gold ? MK_BURST_BIG : MK_BURST, x, y, row_light[r]);
            eng_disc(s, FX(x) + FX(1) / 2, FX(y), gold ? FX(7) : FX(5.25), 0xffffff);
        } else {
            int tt = f < 7 ? 15 : 15 + (f - 6) * 14; /* hundredths: 0.15, then on to 0.85 */
            shreds(s, x, y, tt, gold ? 9 : 7, row_col[r], row_light[r]);
            int sy = y + 10 + (f < 7 ? 3 : 12);
            eng_fill(s, x, sy, 1, 4, K_STRING);
            if (f < 7) mask(b, s, MK_RING, x, y, row_light[r]);
        }
    } else if (e->kind == F_STRIP) {
        shreds(s, x, y, 25 + f * 8, 6, row_light[e->v], row_light[e->v]);
        if (f < 5) mask(b, s, MK_RING_BIG, x, y, row_light[e->v]);
    } else if (e->kind == F_DUST) {
        int kind = e->v >> 8;
        dust(s, x, y, f, e->v & 255,
             kind == 1   ? K_SACK_L
             : kind == 2 ? K_FACE
                         : K_LEDGE,
             kind == 3);
    } else if (e->kind == F_SCORE) {
        digits(put(t, "+"), (unsigned)(e->v & 0xffffff));
        int big = e->v >> 24;
        type(b, s, t, x, y - f * 10 / 30, big ? 11 : 9, big ? K_GOLD : 0xffffff, K_INK, 1);
    } else if (e->kind == F_CALL) {
        type(b, s, callouts[e->v], x, y - f * 6 / 40, 14, call_fill[e->v], call_edge[e->v], 1);
    } else if (e->kind == F_SPARK) {
        static const signed char sp[4][2] = { { -52, -9 }, { 50, -12 }, { -30, -18 }, { 34, -20 } };
        unsigned rgb = e->v == T_WIDE ? K_GOLD_L : 0xffffff;
        for (int k = 0; k < 4; ++k)
            mask(b, s, k & 2 ? MK_SPARKLE_S : MK_SPARKLE, x + sp[k][0], y + sp[k][1], rgb);
    } else if (e->kind == F_STARS) {
        for (int k = 0; k < 8; ++k) {
            int a = k * 128 + 49, d = 8 + (f & ~1) * 23 / 16; /* out to 31 px across, 25 down */
            mask(b, s, MK_STARP, x + d * eng_cos(a) / 65536, y + d * 25 / 31 * eng_sin(a) / 65536,
                 K_GOLD);
        }
    } else if (e->kind == F_KNOCK) {
        if (e->v < 2)
            eng_fill(s, e->v ? P_R : 18, y - 7, 6, 14, K_KNOCK);
        else
            eng_fill(s, x - 8, 51, 16, 5, K_KNOCK);
    }
}

static int mat_depth(const struct pstall *b, int px,
                     int w) { /* px: a column; px + 0.5 its middle */
    if (b->dip_t >= 12) return 0;
    int k = 2 * px + 1 - 2 * b->dip_x,
        e = 2 * (w / 2 - (px < b->pad ? b->pad - px : px - b->pad)); /* half px */
    if (k * k >= 1600 || e <= 0) return 0;
    int sn = 1600 - k * k, q = dip_curve[b->dip_t] * b->dip_q; /* 1/32 px */
    e = e > 12 ? 12 : e;
    int v = q * (sn * sn >> 10) * e,
        den = 32 * 2500 * 12; /* (1 - u^2)^2 as sn^2 / 2,560,000, in 32 bits */
    return (v >= 0 ? v + den / 2 : v - den / 2) / den;
}

static void tramp_back(const struct pstall *b, struct eng_surface *s) {
    int w = pad_w(b), x = b->pad;
    sprite(b, s, w == P_WIDE_W ? SP_TBACK_90 : SP_TBACK_60, x, P_MAT, 256);
    if (!b->art_ok) eng_fill(s, x - w / 2 + 1, P_MAT - 4, w - 2, 2, 0xa82840);
    for (int k = -w / 2 + 5; k <= w / 2 - 5; k += 5) {
        int d = mat_depth(b, x + k, w);
        eng_fill(s, x + k, P_MAT - 2, 1, 3 + d, d < 2 ? 0xc9ced8 : 0xeef1f6);
    }
}

static void tramp_front(const struct pstall *b, struct eng_surface *s) {
    int w = pad_w(b), x = b->pad, x0 = x - 27 - (w - P_PAD_W) / 2, n = w - 4;
    int glue = b->glue_n > 0, blink = b->wide_t && b->wide_t < 3 * ENG_HZ && b->wide_t / 10 & 1;
    sprite(b, s, w == P_WIDE_W ? SP_TFRONT_90 : SP_TFRONT_60, x, P_MAT, 256);
    if (!b->art_ok) eng_round(s, x - w / 2, P_MAT + 3, w, 5, 2, 0xe0405a);
    if (x0 + n <= s->x0 || x0 >= s->x1) return;
    for (int i = 0; i < n; ++i) {
        int px = x0 + i, d = mat_depth(b, px, w), end = i == 0 || i == n - 1;
        unsigned col = glue ? 0xffb43a : 0xfff1c9;
        if (b->wide_t && (i < 15 || n - 1 - i < 15)) col = blink ? 0xffffff : K_GOLD;
        if (px < s->x0 || px >= s->x1) continue;
        eng_fill(s, px, P_MAT + d + end, 1, 3 - end, col);
        if (!end) eng_fill(s, px, P_MAT + 2 + d, 1, 1, blend(col, 0x7a4a20, 72));
    }
    if (glue) {
        eng_fill(s, x - w / 2 + 8, P_MAT, w - 16, 1, 0xffe08a);
        for (int i = 0; i < b->glue_n; ++i)
            eng_round(s, x - w / 4 + i * w / 4 - 1, P_MAT + 2, 3, 5 + (i & 1), 1, 0xffb43a);
    }
}

static void ball(const struct pstall *b, struct eng_surface *s, const struct ball *o) {
    int x = FX_INT(o->x + FX(1) / 2), y = FX_INT(o->y + FX(1) / 2);
    if (b->mode == PLAY && !o->stuck && o->bounce != 4)
        for (int i = 0; i < 3; ++i) {
            int d = 9 * 2 + i * 13; /* half px: 9, 15.5, 22 */
            sprite(b, s, SP_TRAIL_0 + i, x - ((d * eng_cos(o->ang) / 2 + 32768) >> 16),
                   y - ((d * eng_sin(o->ang) / 2 + 32768) >> 16), 256);
        }
    if (b->slow_t && o->live && (b->slow_t > 2 * ENG_HZ || b->slow_t / 8 & 1))
        for (int i = 0; i < 6; ++i) {
            int a = b->frame * 20 + i * 1024 / 6; /* 0.12 rad a frame */
            eng_disc(s, FX(x) + 23 * eng_cos(a) / 2, FX(y) + 23 * eng_sin(a) / 2, FX(1.3), K_MINT);
        }
    if (!b->art_ok) {
        eng_disc(s, FX(x), FX(y), FX(P_RB), 0x965ae6);
        return;
    }
    if (o->bounce == 4)
        sprite(b, s, SP_SQUASH, o->cx, P_REST + 2, 256);
    else if (o->bounce)
        sprite(b, s, SP_STRETCH_1 + 3 - o->bounce, x, y, 256);
    else
        sprite(b, s, SP_BALL_0 + o->spin, x, y, 256);
}

static void ticket(const struct pstall *b, struct eng_surface *s, int k) {
    int kind = b->tk[k].kind - 1, x = b->tk[k].x, y = FX_INT(b->tk[k].y), f = b->tk[k].t / 6 & 3;
    if (f == 0)
        sprite(b, s, SP_TICKET_0 + 2 * kind, x, y, 256);
    else if (f == 2) {
        eng_round(s, x - 13, y - 2, 26, 4, 2, tk_paper[kind]);
        eng_fill(s, x - 11, y, 22, 1, blend(tk_paper[kind], tk_ink[kind], 120));
    } else
        sprite(b, s, SP_TICKET_0S + 2 * kind, x, y, 256);
}

/* A rounded box as eng_round draws it, for less: its straight parts filled, each corner a mask
 * (export_art.py CORNERS). */
static void panel(const struct pstall *b, struct eng_surface *s, int x, int y, int w, int h, int r,
                  unsigned rgb) {
    if (!b->art_ok || r < 3 || r > 8) {
        eng_round(s, x, y, w, h, r, rgb);
        return;
    }
    fill(s, x + r, y, w - 2 * r, h, rgb);
    fill(s, x, y + r, r, h - 2 * r, rgb);
    fill(s, x + w - r, y + r, r, h - 2 * r, rgb);
    for (int k = 0; k < 4;
         ++k) /* each corner a disc 2r across, smoothed as eng_round smooths one */
        mask(b, s, MK_CORNER_3 + r - 3, k & 1 ? x + w - 2 * r : x, k & 2 ? y + h - 2 * r : y, rgb);
}

static int outside(const struct eng_surface *s, const int *h, struct eng_surface *part);
/* A panel with its shadow 2 px right and 4 down, the shadow drawn only where it shows. */
static void shadowed_panel(const struct pstall *b, struct eng_surface *s, int x, int y, int w,
                           int h, int r, unsigned rgb) {
    int hole[4] = { x + r, y + r, x + w, y + h }; /* the shadow's part the panel covers */
    struct eng_surface part[4];
    for (int k = 0, n = outside(s, hole, part); k < n; ++k)
        panel(b, &part[k], x + 2, y + 4, w, h, r, 0x7c5232);
    panel(b, s, x, y, w, h, r, rgb);
}

static void plaque(const struct pstall *b, struct eng_surface *s, int x0, int y0, int x1, int y1,
                   unsigned rgb) {
    panel(b, s, x0, y0, x1 - x0, y1 - y0, (y1 - y0) / 2, rgb);
}

static void hud(const struct pstall *b, struct eng_surface *s) {
    int y = b->paused ? P_HUD_Y2 : P_HUD_Y, in = b->paused ? 27 : 36;
    char t[24];
    if (s->y0 >= y + 14 || s->y1 <= y - 13) return;
    digits(t, b->score);
    int tw = text_w(b, 14, t);
    plaque(b, s, in, y - 7, in + (tw + 15) / 16 + 13, y + 7, K_INK);
    type(b, s, t, in + 6, y, 14, K_GOLD, ~0u, 0);
    int combo = b->combo, sw = b->swell;
    if (combo >= 2 && !b->paused) {
        int w = combo < 5 ? 32 : combo < 10 ? 74 : 78, h = combo < 5 ? 14 : combo < 10 ? 15 : 16;
        w = w * (100 + 22 * sw / 6) / 100, h = h * (100 + 14 * sw / 6) / 100;
        unsigned bg = combo < 5 ? K_INK : combo < 10 ? 0xff9f1c : K_GOLD;
        unsigned ink = combo < 5 ? K_CREAM : combo < 10 ? K_INK : K_CRIMSON_DD;
        if (combo >= 10)
            plaque(b, s, 187 - w / 2 - 1, y - h / 2 - 2, 188 + w / 2 + 2, y + (h + 1) / 2 + 2,
                   0xffffff);
        plaque(b, s, 187 - w / 2, y - h / 2, 188 + w / 2, y + (h + 1) / 2, bg);
        if (combo < 5)
            *digits(put(t, "x"), (unsigned)combo) = 0;
        else if (combo < 10)
            put(digits(put(t, "x"), (unsigned)combo), " COMBO");
        else
            put(t, "x10 MAX!");
        type(b, s, t, 187, y, 11, ink, ~0u, 1);
    }
    int n = b->slots, r0 = ENG_W - in - 10; /* 329.5 */
    plaque(b, s, r0 - (n - 1) * 25 / 2 - 9, y - 7, r0 + 10, y + 8, K_INK);
    for (int i = 0; i < n; ++i) {
        int x = r0 - (i * 25 + 1) / 2;
        if (i < b->lives)
            b->art_ok ? sprite(b, s, SP_MINI, x, y, 256)
                      : eng_disc(s, FX(x), FX(y), FX(4), 0xb07cf6);
        else
            eng_disc(s, FX(x) + FX(1) / 2, FX(y) + FX(1) / 2, FX(2.6), 0x3a2c22);
        if (b->lost_t && i == b->lives) mask(b, s, MK_RING_LOST, x, y, 0xffffff);
        if (b->gain_t && i == b->lives - 1) {
            mask(b, s, MK_RING_GAIN, x, y, K_GOLD);
            mask(b, s, MK_SPARKLE_S, x + 6, y - 6, 0xffffff);
        }
    }
}

static void banner(const struct pstall *b, struct eng_surface *s) {
    int y0 = banner_y(b->t), x0 = 74, x1 = 301, y1 = y0 + 54;
    char t[40];
    struct eng_surface under = *s; /* it hangs behind the beam until it drops below it */
    s = &under;
    s->y0 = s->y0 < P_TOP ? P_TOP : s->y0;
    if (s->y1 <= s->y0 || s->y0 >= y1 + 8 || s->x1 <= x0 - 12 || s->x0 >= x1 + 12) return;
    eng_fill(s, x0 + 22, P_TOP, 1, y0 + 2 - P_TOP, K_STRING);
    eng_fill(s, x1 - 22, P_TOP, 1, y0 + 2 - P_TOP, K_STRING);
    sprite(b, s, SP_TAIL_L, x0, y0, 256);
    sprite(b, s, SP_TAIL_R, x1, y0, 256);
    int in[4] = { x0 + 6, y0 + 6, x1 - 6, y1 - 6 }; /* the crimson only where the cream leaves it */
    struct eng_surface part[4];
    for (int k = 0, n = outside(s, in, part); k < n; ++k)
        panel(b, &part[k], x0, y0, x1 - x0, y1 - y0, 5, K_CRIMSON);
    panel(b, s, x0 + 3, y0 + 3, x1 - x0 - 6, y1 - y0 - 6, 3, K_CREAM);
    type(b, s, "LEVEL CLEAR!", 187, y0 + 19, 20, K_CRIMSON, ~0u, 1);
    char *p = digits(put(t, "+"), (unsigned)(100 * (b->level - 1)));
    p = put(p, " BONUS   LEVEL ");
    put(digits(p, (unsigned)b->level), " NEXT");
    type(b, s, t, 187, y0 + 40, 9, K_INK, ~0u, 1);
}

static void confetti(const struct pstall *b, struct eng_surface *s) {
    static const unsigned cc[9] = { 0xff5470, 0xff9f1c, 0xffd23f, 0x3bceac, 0x5aa9f5,
                                    0xf4f1ea, 0xf15bb5, K_GOLD,   K_CREAM };
    for (int k = 0, p[6]; k < 32 && k < (b->t - 9) * 4; ++k) {
        confetto_next(k ? 0 : 1, p);
        if (k >= (b->t > 91 ? (b->t - 91) * 4 : 0))
            eng_round(s, p[0], p[1], p[2], p[3], 1, cc[p[4]]);
    }
}

static void big_ticket(const struct pstall *b, struct eng_surface *s) {
    int dy = ticket_dy(b->t), x0 = 58, x1 = 317, y0 = 92 + dy, y1 = 232 + dy;
    char t[40];
    if (s->y1 <= y0 || s->x1 <= x0 - 9 || s->x0 >= x1 + 9 || b->t < 5) return;
    shadowed_panel(b, s, x0, y0, x1 - x0, y1 - y0, 7, 0xffe8a3);
    for (int x = x0; x <= x1; x += x1 - x0) eng_disc(s, FX(x), FX((y0 + y1) / 2), FX(9), 0xb6875a);
    for (int x = x0 + 26; x <= x1 - 26; x += x1 - x0 - 52)
        for (int y = y0 + 6; y < y1 - 6; y += 6) eng_fill(s, x, y, 1, 3, 0xc8a45a);
    for (int k = 0; k < 4; ++k)
        mask(b, s, MK_STAR5, k & 1 ? x1 - 15 : x0 + 15, k & 2 ? y1 - 16 : y0 + 16, K_CRIMSON);
    type(b, s, "GAME OVER", 187, y0 + 22, 20, K_CRIMSON, ~0u, 1);
    digits(t, b->score);
    type(b, s, t, 187, y0 + 56, 30, K_INK, ~0u, 1);
    if (b->best_new) {
        /* the badge pulses once as the ticket lands: 2 px wider a side and back over 10 frames */
        int pulse = b->t > 15 && b->t < 26 ? (b->t - 15 < 5 ? b->t - 15 : 25 - b->t) * 2 / 5 : 0;
        mask(b, s, MK_BADGE, 187, y0 + 88, K_GOLD);
        eng_round(s, 137 - pulse, y0 + 80 - pulse / 2, 101 + 2 * pulse, 16 + pulse, 8, K_GOLD);
        type(b, s, "NEW BEST!", 187, y0 + 88, 11, K_CRIMSON_DD, ~0u, 1);
    } else {
        char *p = digits(put(t, "BEST "), b->save.best);
        p = put(p, "   LEVEL ");
        digits(p, b->save.level);
        type(b, s, t, 187, y0 + 88, 9, 0x8a5a34, ~0u, 1);
    }
    type(b, s, "PRESS CENTER TO PLAY AGAIN", 187, y0 + 120, 9, K_CRIMSON_D, ~0u, 1);
}

static void sign(const struct pstall *b, struct eng_surface *s) {
    if (s->y1 <= P_TOP || s->y0 >= 192 || s->x1 <= 100 || s->x0 >= 278) return;
    eng_fill(s, 150, P_TOP, 1, 132 - P_TOP, K_STRING);
    eng_fill(s, 225, P_TOP, 1, 132 - P_TOP, K_STRING);
    shadowed_panel(b, s, 102, 128, 171, 58, 6, K_WOOD_1);
    panel(b, s, 105, 131, 165, 52, 4, 0x8f5c36);
    eng_disc(s, FX(150), FX(133.5), FX(1.6), K_WOOD_SEAM);
    eng_disc(s, FX(225), FX(133.5), FX(1.6), K_WOOD_SEAM);
    type(b, s, "PAUSED", 187, 151, 20, K_CREAM, ~0u, 1);
    type(b, s, "CENTER TO RESUME", 187, 171, 9, 0xf0d2a0, ~0u, 1);
}

/* Everything under the panels, in the rows and columns s paints. */
static void scene(const struct pstall *b, struct eng_surface *s) {
    unsigned on = b->paused ? 0x11111111u : b->bulbs; /* paused: one in four */
    char t[24];
    stall(b, s, s->y0, s->y1);
    for (int i = 0; b->art_ok && i < P_BULBS; ++i) /* a lit bulb: its patch of the lit stall */
        if (on >> i & 1) {
            int x, y, c[4];
            bulb_box(i, &x, &y);
            if (!eng_clip(s, x - 6, y - 6, 13, 13, c)) continue;
            const unsigned char *q = (const unsigned char *)b->art + ART_BULBS + 169 * i;
            for (int j = c[1]; j < c[3]; ++j)
                indexed(at(s, c[0], j), q + 13 * (j - y + 6) + c[0] - x + 6, c[2] - c[0],
                        b->art + ART_PAL / 4);
        }
    for (int k = 0; k < P_FX; ++k) /* a knocked plank or beam flashes, over its bulbs' glow */
        if (b->fx[k].kind == F_KNOCK) effect(b, s, &b->fx[k]);
    if (b->combo >= 10 && !b->paused &&
        s->y0 < 60) /* x10: a sparkle on every fourth bulb of the beam */
        for (int k = 1; k < 14; k += 4) mask(b, s, MK_SPARKLE, 25 + 25 * k, 54, 0xffffff);
    for (int k = 0; k < 4; ++k)
        sprite(b, s, SP_PLUSH_0 + k, plush_x[k], plush_y[k] + hop_dy(b, k), 256);
    if (b->mode != CLEAR) lattice(b, s);
    for (int k = 0; k < P_FX; ++k)
        if (b->fx[k].kind && b->fx[k].kind != F_KNOCK &&
            !(b->fx[k].kind == F_DUST && b->fx[k].v >> 8 == 3))
            effect(b, s, &b->fx[k]);
    for (int k = 0; k < 2; ++k)
        if (b->tk[k].kind) ticket(b, s, k);
    if (b->mode == CLEAR && b->t > 9) confetti(b, s);
    if (((b->mode == READY && b->fresh) || b->mode == INFLATE) && s->y1 > 212 && s->y0 < 258) {
        *digits(put(t, "LEVEL "), (unsigned)b->level) = 0;
        type(b, s, t, 187, 228, 20, K_CREAM, K_INK, 1);
        if (b->mode == READY) type(b, s, "PRESS CENTER TO LAUNCH", 187, 248, 9, K_HINT, ~0u, 1);
    } else if (b->mode == PLAY && s->y1 > 240 && s->y0 < 256)
        for (int k = 0; k < 3; ++k)
            if (b->ball[k].live && b->ball[k].stuck) {
                type(b, s, "CENTER TO LAUNCH", 187, 248, 9, K_HINT, ~0u, 1);
                break;
            }
    if (s->y1 > 270 && s->y0 < 296) tramp_back(b, s);
    for (int k = 0; k < 3; ++k)
        if (b->ball[k].live) ball(b, s, &b->ball[k]);
    if (s->y1 > 270 && s->y0 < 296) tramp_front(b, s);
    if (s->y1 > P_COUNTER) stall(b, s, P_COUNTER, 320);
    for (int k = 0; k < P_FX; ++k)
        if (b->fx[k].kind == F_DUST && b->fx[k].v >> 8 == 3) effect(b, s, &b->fx[k]);
    hud(b, s);
}

/* The parts of s's paint outside the box h (x0 y0 x1 y1): up to four, as part; how many. */
static int outside(const struct eng_surface *s, const int *h, struct eng_surface *part) {
    int n = 0;
    if (h[2] <= s->x0 || h[0] >= s->x1 || h[3] <= s->y0 || h[1] >= s->y1 || h[3] <= h[1]) {
        part[0] = *s;
        return 1;
    }
    int y0 = h[1] > s->y0 ? h[1] : s->y0, y1 = h[3] < s->y1 ? h[3] : s->y1;
    int box[4][4] = { { s->x0, s->y0, s->x1, y0 }, /* above, below, then beside it */
                      { s->x0, y1, s->x1, s->y1 },
                      { s->x0, y0, h[0] < s->x1 ? h[0] : s->x1, y1 },
                      { h[2] > s->x0 ? h[2] : s->x0, y0, s->x1, y1 } };
    for (int k = 0; k < 4; ++k)
        if (box[k][2] > box[k][0] && box[k][3] > box[k][1]) {
            part[n] = *s;
            part[n].x0 = box[k][0], part[n].y0 = box[k][1], part[n].x1 = box[k][2],
            part[n].y1 = box[k][3];
            ++n;
        }
    return n;
}

/* The scene, then the panel over it: what the panel's solid middle hides is not drawn (the banner,
 * the game over's ticket, the pause's sign), so a frame that moves one pays for the panel alone. */
static void draw(const void *state, struct eng_surface *s) {
    const struct pstall *b = state;
    int h[4] = { 0, 0, 0, 0 }; /* the panel's solid middle: x0 y0 x1 y1 */
    if (b->mode == OVER && b->t >= 5) {
        int dy = ticket_dy(b->t);
        h[0] = 65, h[1] = 99 + dy, h[2] = 310, h[3] = 225 + dy;
    } else if (b->paused)
        h[0] = 108, h[1] = 134, h[2] = 267, h[3] = 180;
    else if (b->mode == CLEAR && b->t < 90)
        h[0] = 80, h[1] = banner_y(b->t) + 6, h[2] = 295, h[3] = banner_y(b->t) + 48;
    h[1] = h[1] < P_TOP ? P_TOP : h[1];
    struct eng_surface part[4];
    for (int k = 0, n = outside(s, h, part); k < n; ++k) scene(b, &part[k]);
    if (b->mode == CLEAR && b->t < 90) banner(b, s);
    if (b->mode == OVER) big_ticket(b, s);
    if (b->paused) sign(b, s);
}

const struct eng_game eng_popstall = { "Pop Stall",
                                       sizeof(struct pstall) + ART_BYTES,
                                       ENG_RAW | ENG_FULL | ENG_OWN_PAUSE,
                                       init,
                                       step,
                                       draw,
                                       pause };
