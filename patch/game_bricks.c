/* Bricks (docs/games-engine.md#bricks): a ball, a paddle and a lattice of hexagon-packed discs.
 * The ball breaks a cell it touches and rebounds off its curved face; clearing the lattice is the
 * next level, generated from the game's seed and the level number; three misses end the game. */
#include "engine.h"

#define B_L 24 /* the field: the ball's edge stays between the walls and under the top */
#define B_R 351
#define B_TOP 26
#define B_HUD 24 /* the score line's rows */
#define B_RB 5   /* the ball's radius */
#define B_RC 8   /* a cell's */
#define B_PX 20  /* the lattice: a row's pitch across, the rows' pitch down */
#define B_PY 17
#define B_ROWS 7
#define B_COLS 16 /* in an even row; an odd row has one fewer, half a pitch in */
#define B_X0 37   /* an even row's first centre is B_X0 + 0.5 */
#define B_Y0 48   /* the first row's centre */
#define B_PAD_W 60
#define B_PAD_H 8
#define B_PAD_UP 40 /* the paddle's top, above the sheet's end */
#define B_WHEEL 2   /* px a wheel position: half a turn crosses the field */
#define B_KEY 4     /* px a step while Previous or Next is held */
#define B_STEEP 43  /* 15 degrees (1024 a turn): the shallowest a path may be */
#define B_TILT 171  /* 60 degrees: the paddle's end turns a path this far from vertical */
#define B_FADE 20   /* frames the next lattice takes to fade in */
#define B_RAMP 4    /* frames one row of it takes; the rows start in turn, two at once at most */
#define B_SEQ 6     /* frames between the sounds of a run */
#define B_MAGIC 0x314b5242 /* "BRK1" */

enum { READY, PLAY, FADE, OVER };
enum { NONE, WHITE, GREY, KEY };

struct bricks {
    fx x, y, spd; /* the ball, and its speed a step */
    int ang;      /* its heading, 1024 a turn, y down */
    int pad, h, top, hud, mode, t, level, lives, side, left, seq, runs, wait;
    unsigned score, seed;
    unsigned char cell[B_ROWS * B_COLS]; /* row by row, NONE to KEY */
    struct {
        unsigned magic, best, level, sum;
    } save;
    unsigned saved; /* the best on the card */
};

static fx cell_x(int r, int c) { return FX(B_X0 + (r & 1) * B_PX / 2 + c * B_PX) + FX(1) / 2; }
static int cell_y(int r) { return B_Y0 + r * B_PY; }
static int cols(int r) { return B_COLS - (r & 1); }

/* A cell's six neighbours that are on the lattice, as indices into cell[]; how many. */
static int around(int r, int c, int *out) {
    int n = 0, lo = r & 1 ? c : c - 1;
    for (int dr = -1; dr <= 1; ++dr)
        for (int k = dr ? lo : c - 1; k <= (dr ? lo + 1 : c + 1); k += dr ? 1 : 2)
            if (r + dr >= 0 && r + dr < B_ROWS && k >= 0 && k < cols(r + dr))
                out[n++] = (r + dr) * B_COLS + k;
    return n;
}

static unsigned rng(unsigned *s) {
    unsigned x = *s;
    x ^= x << 13, x ^= x >> 17, x ^= x << 5;
    return *s = x;
}

/* The level's mask over the lattice, mirrored about the middle column: ax is a cell's distance
 * from it in half pitches (10 px), ay its row's from the middle row. */
static int shape(int kind, int p, int q, int ax, int ay, int r) {
    if (kind == 0) return ax + 2 * ay <= p && ax + 2 * ay >= p - q; /* a diamond, maybe hollow */
    if (kind == 1) {                                                /* a ring, p px out, q thick */
        int d = ax * ax * 100 + ay * ay * B_PY * B_PY;
        return d <= p * p && d >= (p - q) * (p - q);
    }
    if (kind == 2) return ax >= p && ax < p + q; /* twin columns */
    return (r * 2 + ax) % p < q;                 /* chevrons */
}

/* The level's lattice from the seed and the level number alone: one of the four masks with its
 * sizes drawn, greys more often as the levels go, the keystone on the middle column where it can
 * be. A mask that leaves under 12 cells is drawn again; the eighth try fills the lattice. */
static void build(struct bricks *b) {
    unsigned s = b->seed ^ (unsigned)b->level * 0x9e3779b9u,
             g = 9u - (unsigned)(b->level < 7 ? b->level : 7);
    int n = 0;
    s = s ? s : 1;
    for (int tries = 0; n < 12; ++tries) {
        int kind = (int)((unsigned)(b->level - 1 + tries) + b->seed) & 3;
        int p = kind == 0   ? 9 + (int)(rng(&s) % 7)
                : kind == 1 ? 60 + (int)(rng(&s) % 50)
                : kind == 2 ? 2 + (int)(rng(&s) % 7)
                            : 5 + (int)(rng(&s) % 4);
        int q = kind == 0   ? 4 + (int)(rng(&s) % 12)
                : kind == 1 ? 20 + (int)(rng(&s) % 16)
                : kind == 2 ? 3 + (int)(rng(&s) % 3)
                            : 2 + (int)(rng(&s) % 2);
        unsigned grey = rng(&s) % g;
        n = 0;
        for (int r = 0; r < B_ROWS; ++r)
            for (int c = 0; c < B_COLS; ++c) {
                int x = 2 * c - 15 + (r & 1), ax = x < 0 ? -x : x, ay = r < 3 ? 3 - r : r - 3;
                int on = c < cols(r) && (tries >= 7 || shape(kind, p, q, ax, ay, r));
                b->cell[r * B_COLS + c] = !on                                   ? NONE
                                          : ((unsigned)(ax + 2 * r) + grey) % g ? WHITE
                                                                                : GREY;
                n += on;
            }
    }
    int axis = 0;
    for (int r = 1; r < B_ROWS; r += 2) axis += b->cell[r * B_COLS + 7] != NONE;
    int k = (int)(rng(&s) % (unsigned)(axis ? axis : n));
    for (int i = 0; i < B_ROWS * B_COLS; ++i)
        if (b->cell[i] && (!axis || (i / B_COLS & 1 && i % B_COLS == 7)) && !k--) {
            b->cell[i] = KEY;
            break;
        }
    b->left = n;
    b->spd = FX(2.5) + (b->level - 1) * (FX(15) / 100);
    b->spd = b->spd > FX(5) ? FX(5) : b->spd;
}

static void ball_dirty(int x, int y) {
    eng_dirty(x - B_RB - 1, y - B_RB - 1, 2 * B_RB + 3, 2 * B_RB + 3);
}
static void pad_dirty(const struct bricks *b, int from, int to) {
    int lo = from < to ? from : to, hi = from < to ? to : from;
    eng_dirty(lo - B_PAD_W / 2, b->top, hi - lo + B_PAD_W, B_PAD_H);
}
static int band(const struct bricks *b) { return (cell_y(B_ROWS - 1) + B_RC + b->top) / 2 - 22; }

/* A run of n of one sound, B_SEQ frames apart: the first now. */
static void sound(struct bricks *b, int kind, int n) {
    eng_sfx(kind);
    b->seq = kind, b->runs = n - 1, b->wait = B_SEQ;
}

static void ready(struct bricks *b) {
    b->mode = READY;
    b->side = b->side > 0 ? -1 : 1;
    b->x = FX(b->pad + b->side * B_PAD_W / 3), b->y = FX(b->top - B_RB);
    eng_dirty(0, band(b), ENG_W, 44);
}

static void new_game(struct bricks *b) {
    b->score = 0, b->lives = 3, b->level = 1;
    b->seed = rand_below(~0u) | 1;
    build(b);
    ready(b);
    eng_dirty_all();
}

static void init(void *state, int h) {
    struct bricks *b = state;
    int x = eng_inset(4) + 16;
    b->h = h, b->top = h - B_PAD_UP, b->hud = x < 24 ? 24 : x;
    b->pad = ENG_W / 2;
    if (!eng_load("bricks", &b->save, sizeof b->save, B_MAGIC) || b->save.best > 99999999 ||
        b->save.level > 99)
        b->save.best = b->save.level = 0; /* a save that no game made */
    b->saved = b->save.best + b->save.level;
    new_game(b);
}

/* No angle shallower than B_STEEP from the horizontal: the ball cannot stall sideways. */
static int steep(int a) {
    int h = (a &= 1023) & 511;
    return h < B_STEEP ? (a & 512) + B_STEEP : h > 512 - B_STEEP ? (a & 512) + 512 - B_STEEP : a;
}

/* When on its path v (1/64 px) a ball dx, dy from a cell's centre first touches it, in 1024ths
 * of the path; 1025: not on this path. Already touching and closing is at once. */
static int sweep(int dx, int dy, int vx, int vy) {
    int bv = dx * vx + dy * vy, rr = (B_RB + B_RC) * 64, a = vx * vx + vy * vy;
    if (bv >= 0) return 1025;
    int cc = dx * dx + dy * dy - rr * rr;
    if (cc <= 0) return 0;
    long long disc = (long long)bv * bv - (long long)a * cc;
    if (disc < 0) return 1025;
    int num = -bv - (int)eng_sqrt((unsigned long long)disc);
    return num >= a ? 1025 : num * 1024 / a;
}

static void cell_dirty(int r, int c, int dx, int dy) {
    eng_dirty(FX_INT(cell_x(r, c)) - B_RC - 1 - dx, cell_y(r) - B_RC - 1 - dy, 2 * (B_RC + dx) + 3,
              2 * (B_RC + dy) + 3);
}

/* The ball's touch on cell i: a grey turns white, a white goes, the keystone takes its six
 * neighbours with it (one joined rect); each gone is the level's points. */
static void hit(struct bricks *b, int i) {
    int r = i / B_COLS, c = i % B_COLS, near[6], n = 1;
    if (b->cell[i] == GREY) {
        b->cell[i] = WHITE;
        cell_dirty(r, c, 0, 0);
        sound(b, ENG_SFX_TICK, 1);
        return;
    }
    if (b->cell[i] == KEY) {
        for (int k = around(r, c, near); k--;)
            if (b->cell[near[k]]) b->cell[near[k]] = NONE, ++n;
        cell_dirty(r, c, B_PX, B_PY);
        sound(b, ENG_SFX_TAP, 2);
    } else {
        cell_dirty(r, c, 0, 0);
        sound(b, ENG_SFX_TAP, 1);
    }
    b->cell[i] = NONE;
    b->left -= n;
    b->score += (unsigned)(n * b->level);
    eng_dirty(0, 0, ENG_W, B_HUD);
}

/* The ball's travel this step, swept: the first thing its path meets (a cell within two of its
 * place on the lattice, a wall, the paddle's top) stops it there and turns it, four times a step
 * at most. Positions are 16.16; the tests run in 1/64 px. */
static void travel(struct bricks *b) {
    for (int rem = 1024, k = 0; k < 4 && rem > 0 && b->mode == PLAY; ++k) {
        fx vx = fx_mul(b->spd, eng_cos(b->ang)) * rem >> 10;
        fx vy = fx_mul(b->spd, eng_sin(b->ang)) * rem >> 10;
        int qx = vx >> 10, qy = vy >> 10, bx = b->x >> 10, by = b->y >> 10;
        int t = 1025, what = -1, d, r0 = (FX_INT(b->y) - B_Y0 + B_PY / 2) / B_PY;
        for (int r = r0 - 2; r <= r0 + 2; ++r) {
            int c0 = (FX_INT(b->x) - B_X0 - (r & 1) * B_PX / 2 + B_PX / 2) / B_PX;
            for (int c = c0 - 2; r >= 0 && r < B_ROWS && c <= c0 + 2; ++c) {
                if (c < 0 || c >= cols(r) || !b->cell[r * B_COLS + c]) continue;
                int u = sweep(bx - (cell_x(r, c) >> 10), by - cell_y(r) * 64, qx, qy);
                if (u < t) t = u, what = r * B_COLS + c;
            }
        }
        if (qx < 0 && (d = bx - (B_L + B_RB) * 64) + qx < 0 && (d <= 0 ? 0 : d * 1024 / -qx) < t)
            t = d <= 0 ? 0 : d * 1024 / -qx, what = -2;
        if (qx > 0 && (d = (B_R - B_RB) * 64 - bx) - qx < 0 && (d <= 0 ? 0 : d * 1024 / qx) < t)
            t = d <= 0 ? 0 : d * 1024 / qx, what = -2;
        if (qy < 0 && (d = by - (B_TOP + B_RB) * 64) + qy < 0 && (d <= 0 ? 0 : d * 1024 / -qy) < t)
            t = d <= 0 ? 0 : d * 1024 / -qy, what = -3;
        if (qy > 0 && (d = (b->top - B_RB) * 64 - by) > -64 && d <= qy && /* a pixel's grace */
            (d <= 0 ? 0 : d * 1024 / qy) < t) {
            int u = d <= 0 ? 0 : d * 1024 / qy, off = FX_INT(b->x + (vx >> 5) * u / 32) - b->pad;
            if (off >= -B_PAD_W / 2 - B_RB && off <= B_PAD_W / 2 + B_RB) t = u, what = -4;
        }
        if (t > 1024) t = 1024;
        b->x += (vx >> 5) * t / 32;
        b->y += (vy >> 5) * t / 32;
        rem -= rem * t >> 10;
        if (what >= 0) { /* off the cell's face: its normal is the mirror */
            int r = what / B_COLS, nx = (b->x - cell_x(r, what % B_COLS)) >> 10;
            int n = eng_atan2((b->y >> 10) - cell_y(r) * 64, nx);
            b->ang = steep(2 * n - b->ang + 512);
            hit(b, what);
        } else if (what == -2)
            b->ang = steep(512 - b->ang);
        else if (what == -3)
            b->ang = steep(-b->ang);
        else if (what == -4) { /* the contact point sets the way out: the middle straight up */
            int off = FX_INT(b->x) - b->pad;
            b->ang = 768 + off * B_TILT / (B_PAD_W / 2 + B_RB);
            b->ang = b->ang < 768 - B_TILT   ? 768 - B_TILT
                     : b->ang > 768 + B_TILT ? 768 + B_TILT
                                             : b->ang;
            sound(b, ENG_SFX_TICK, 1);
        }
    }
}

static void over(struct bricks *b) {
    b->mode = OVER;
    if (b->score > b->save.best) b->save.best = b->score;
    if ((unsigned)b->level > b->save.level) b->save.level = (unsigned)b->level;
    /* the game's end, not a frame of play: a new best reaches the card now */
    if (b->save.best + b->save.level != b->saved &&
        eng_save("bricks", &b->save, sizeof b->save, B_MAGIC))
        b->saved = b->save.best + b->save.level;
    eng_dirty_all();
}

static int fade_row(int r) { return r * (B_FADE - B_RAMP) / (B_ROWS - 1); }

static int step(void *state, const struct eng_input *in) {
    struct bricks *b = state;
    int pad = b->pad + in->wheel * B_WHEEL, x = FX_INT(b->x), y = FX_INT(b->y), mode = b->mode;
    if (in->held & ENG_TOUCH) pad = in->tx;
    if (in->held & ENG_PREV) pad -= B_KEY;
    if (in->held & ENG_NEXT) pad += B_KEY;
    pad = pad < B_L + B_PAD_W / 2   ? B_L + B_PAD_W / 2
          : pad > B_R - B_PAD_W / 2 ? B_R - B_PAD_W / 2
                                    : pad;
    if (pad != b->pad) pad_dirty(b, b->pad, pad), b->pad = pad;
    if (b->runs && !--b->wait) eng_sfx(b->seq), b->wait = B_SEQ, --b->runs;
    if (b->mode == READY) {
        b->x = FX(pad + b->side * B_PAD_W / 3);
        if (in->pressed & ENG_CENTRE) {
            b->mode = PLAY;
            b->ang = 768 + b->side * B_TILT * (B_PAD_W / 3) / (B_PAD_W / 2 + B_RB);
            eng_dirty(0, band(b), ENG_W, 44);
        }
    } else if (b->mode == PLAY) {
        travel(b);
        if (!b->left) { /* cleared: the next level fades in */
            b->mode = FADE, b->t = 0;
            ++b->level;
            build(b);
            sound(b, ENG_SFX_TICK, 3);
        } else if (b->y > FX(b->top + B_PAD_H)) { /* missed */
            --b->lives;
            sound(b, ENG_SFX_ALERT, 1);
            eng_dirty(0, 0, ENG_W, B_HUD);
            if (b->lives)
                ready(b);
            else
                over(b);
        }
    } else if (b->mode == FADE) {
        ++b->t;
        for (int r = 0; r < B_ROWS; ++r)
            if (b->t > fade_row(r) && b->t <= fade_row(r) + B_RAMP)
                eng_dirty(B_L, cell_y(r) - B_RC - 1, B_R - B_L, 2 * B_RC + 3);
        if (b->t >= B_FADE) ready(b);
    } else if (in->pressed & ENG_CENTRE)
        new_game(b);
    int nx = FX_INT(b->x), ny = FX_INT(b->y), dx = nx - x, dy = ny - y;
    if (mode != OVER && b->mode != OVER && (b->mode == PLAY || dx || dy)) {
        if (dx * dx + dy * dy > 64) /* a jump (a serve, a level's end): each place apart */
            ball_dirty(x, y), ball_dirty(nx, ny);
        else
            eng_dirty((dx < 0 ? nx : x) - B_RB - 1, (dy < 0 ? ny : y) - B_RB - 1,
                      (dx < 0 ? -dx : dx) + 2 * B_RB + 3, (dy < 0 ? -dy : dy) + 2 * B_RB + 3);
    }
    return 1;
}

/* rgb a 256ths of the way up from black: a fading row's colours. The fade draws its discs in
 * these rather than darkening the row with eng_blend afterwards, which cost 634,287 instructions a
 * frame on MIPS (docs/games-engine.md#bricks), over the 600,000 a frame may. */
static unsigned dim(unsigned rgb, int a) {
    return a >= 256 ? rgb
                    : ((rgb & 0xff00ff) * (unsigned)a >> 8 & 0xff00ff) |
                          ((rgb & 0xff00) * (unsigned)a >> 8 & 0xff00);
}

/* v with a comma between each three digits. */
static char *number(char *out, unsigned v) {
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
static char *word(char *out, const char *w) {
    while (*w) *out++ = *w++;
    return out;
}

static void draw(const void *state, struct eng_surface *s) {
    const struct bricks *b = state;
    char text[32];
    eng_fill(s, s->x0, s->y0, s->x1 - s->x0, s->y1 - s->y0, 0x000000);
    if (s->y0 < B_HUD && b->mode != OVER) { /* the score left, the lives right */
        number(text, b->score);
        eng_text(s, text, b->hud, 0, B_HUD, 15, 0xababab, 0);
        for (int k = 0; k < 3; ++k)
            eng_disc(s, FX(ENG_W - b->hud - 4 - 12 * k), FX(B_HUD / 2), FX(3.5),
                     k < b->lives ? 0xababab : 0x333333);
    }
    for (int r = 0; b->mode != OVER && r < B_ROWS; ++r) {
        int y = cell_y(r), a = 256;
        if (y + B_RC + 1 < s->y0 || y - B_RC - 1 >= s->y1) continue;
        if (b->mode == FADE) a = (b->t - fade_row(r)) * 256 / B_RAMP;
        if (a <= 0) continue;
        for (int c = 0; c < cols(r); ++c) {
            int k = b->cell[r * B_COLS + c];
            if (k)
                eng_disc(s, cell_x(r, c), FX(y), FX(B_RC),
                         dim(k == WHITE  ? 0xffffff
                             : k == GREY ? 0xababab
                                         : 0x2160d8,
                             a));
        }
    }
    eng_round(s, b->pad - B_PAD_W / 2, b->top, B_PAD_W, B_PAD_H, B_PAD_H / 2, 0xffffff);
    if (b->mode == READY || b->mode == PLAY) eng_disc(s, b->x, b->y, FX(B_RB), 0xffffff);
    int y = band(b);
    if (b->mode == READY && s->y1 > y && s->y0 < y + 44) {
        *number(word(text, "Level "), (unsigned)b->level) = 0;
        eng_text(s, text, ENG_W / 2, y, 22, 18, 0xababab, 1);
        eng_text(s, "Centre to launch", ENG_W / 2, y + 22, 22, 15, 0x858585, 1);
    }
    if (b->mode == OVER) {
        y = b->h / 2 - 60;
        number(text, b->score);
        eng_text(s, text, ENG_W / 2, y, 30, 22, 0xffffff, 1);
        if (b->score && b->score == b->save.best)
            word(text, "New best")[0] = 0;
        else
            number(word(text, "Best "), b->save.best);
        eng_text(s, text, ENG_W / 2, y + 32, 24, 18, 0xababab, 1);
        eng_text(s, "Centre: play again", ENG_W / 2, y + 64, 22, 15, 0x858585, 1);
    }
}

/* A best is written at the game's end; one that could not be then is tried again here. */
static void pause(void *state) {
    struct bricks *b = state;
    if (b->mode == OVER && b->save.best + b->save.level != b->saved &&
        eng_save("bricks", &b->save, sizeof b->save, B_MAGIC))
        b->saved = b->save.best + b->save.level;
}

const struct eng_game eng_bricks = { "Bricks", sizeof(struct bricks), ENG_RAW, init, step, draw,
                                     pause };
