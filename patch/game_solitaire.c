/* Cabin Solitaire (docs/games-engine.md#cabin-solitaire): Klondike on a cabin's card table. Draw one or
 * three, unlimited passes; the wheel walks only cards that can move, and a card picked up hovers over
 * the best place it can go while the others wear a gold outline; Previous undoes, Next sends a card
 * home, Play draws and, once every card is up, finishes. Winnable deals come from a table of seeds a
 * solver in the test tree won (game_solitaire_seeds.h). The art (the felt, the deck, the type) is a
 * file in the root filesystem read once when the game opens (game_solitaire_art.h). */
#include "engine.h"
#include "navigation.h"
#include "game_solitaire_art.h"
#include "game_solitaire_seeds.h"

#define ART_PATH "/usr/share/q2games/solitaire.bin"
#define S_MAGIC 0x31534343 /* "CCS1": the game and the record */

/* The table (the design's src/cards.py), in sheet px with the sheet the whole screen */
#define CW 43
#define CH 58
#define TOP_Y 26
#define TAB_Y 88
#define STOCK_X 22
#define WASTE_X 73
#define WASTE_FAN 13  /* draw three: the waste's three, this far apart */
#define FAN_ROOM 24   /* a pile ends this far above the sheet's foot, over the rail */
#define RAIL_H 22     /* the rail, the sheet's last rows */
#define SLIP_X0 94    /* the score slip on it */
#define SLIP_X1 281
#define NTAB 20 /* a column's cards at most: six face down and a king to an ace */
#define UNDO 64
#define MAXF 32   /* cards in flight */
#define MAXSRC 96 /* the cursor's places */
#define SAVE_EVERY 10
#define GLIDE 8  /* frames: a drop, an undo, a card home */
#define HOVER 6  /* frames: the hand to another place */
#define DEAL_GAP 2
#define DEAL_FLY 6
#define AUTO_GAP 4
#define FLASH 6
#define SPARK 8
#define FLIP 6
#define MSG_FRAMES 90
#define WIN_GAP 6
#define DROP_AT 24 /* the win board starts down this many frames after the last card lands */
#define DROP 14
#define TAG 10

/* colours (STYLE.md) */
#define FACE 0xf6eedc
#define EDGE 0xb9ae96
#define RED 0xb8242e
#define BLACK 0x22201e
#define GOLD 0xf2b632
#define GOLD_L 0xffe08a
#define SHADOW 0x06140c
#define CONTACT 0x2a2010
#define SLOT_IN 0x0c2e1e
#define SLOT_LINE 0x6fb08a
#define RAIL 0x5a3418
#define RAIL_L 0x8a5630
#define RAIL_D 0x3a2010
#define PEN 0x22201e
#define PEN_SOFT 0x4a3a28
#define SLIP_SHADOW 0x1a0c04
#define RULE 0xcfc3a6
#define PIN 0xc8901e
#define WOOD_LABEL 0xf2d9a8
#define WOOD_TEXT 0xfff6e0
#define HINT_INK 0xfff0c8
#define FELT 0x1b5c3e

#define UP 0x40 /* a card: suit * 13 + rank, and UP when face up in a column */
enum { P_STOCK, P_WASTE, P_FOUND, P_TAB = P_FOUND + 4, PLACES = P_TAB + 7 };
enum { START, DEAL, PLAY, AUTO, WON };
enum { M_DRAW, M_RECYCLE, M_MOVE };
enum { MSG_NONE, MSG_UNDO, MSG_STUCK };
enum { F_DOWN = 1, F_HOME = 2, F_FLIP = 4, F_HAND = 8 }; /* a flight: face down, lands home, turns up */

struct move {
    unsigned char kind, from, to, n; /* n: cards; bit 7 of turned: the source's next card turned */
    unsigned char turned, pad[3];
};

struct flight {
    unsigned char card, place, idx, flags;
    short x0, y0, x1, y1;
    unsigned char t, dur, wait, arc;
};

/* What is saved: the game, the start board's choices and the record. */
struct record {
    unsigned magic;
    unsigned char tab[7][NTAB], tlen[7], stock[24], waste[24], found[4];
    unsigned char slen, wlen, draw3, live;  /* live: a game in progress */
    unsigned char opt_draw3, opt_random, won, pad;
    unsigned seed, moves, time_ms;
    unsigned wins, played, best_ms, fewest;
    unsigned sum;
};

struct sol {
    struct record g;
    int h, rail, limit, mode, sel, art;
    int nsrc, cur, hand, hfrom, hn, hover, ndst; /* the cursor; the hand, from hfrom, hn cards */
    unsigned char src[MAXSRC][2], dst[8];
    int hx, hy, hx0, hy0, ht; /* the hand's place (its landing spot's), and the glide to it */
    int fan_d[7], fan_u[7];
    struct move undo[UNDO];
    int uhead, ucount;
    struct flight fl[MAXF];
    int nfl;
    int flash_t, flash_p, spark_t, spark_p, flip_t, flip_p, flip_i;
    int msg, msg_t, stuck;
    int t, kings, best_new, deal_left, auto_wait;
    int seq, runs, wait;     /* a run of one sound, as Bricks has */
    unsigned last_ms;        /* the clock at the last step of play: 0 after a pause */
    int shown_s, since_save, unsaved;
    unsigned char tile[64 * 64];
    unsigned felt_lut[ART_LUT_LEVELS * 15];
    unsigned char a_o[5][5], a_f[5][5]; /* a card's corner: the edge's cover, the face's (of 255) */
    unsigned file[ART_BYTES / 4];
};

static const short colx[7] = { 19, 68, 116, 165, 213, 262, 310 };

/* ---- cards and places ---- */

static int rank_of(int c) { return (c & 63) % 13; }
static int suit_of(int c) { return (c & 63) / 13; }
static int red(int c) { return !(suit_of(c) & 1); } /* hearts, spades, diamonds, clubs: even is red */

static int plen(const struct sol *b, int p) {
    return p == P_STOCK  ? b->g.slen
           : p == P_WASTE ? b->g.wlen
           : p < P_TAB    ? b->g.found[p - P_FOUND]
                          : b->g.tlen[p - P_TAB];
}

/* place p's card i from the bottom, UP when face up */
static int pcard(const struct sol *b, int p, int i) {
    return p == P_STOCK  ? b->g.stock[i]
           : p == P_WASTE ? b->g.waste[i] | UP
           : p < P_TAB    ? ((p - P_FOUND) * 13 + i) | UP
                          : b->g.tab[p - P_TAB][i];
}

/* Place p takes card c (the first of n) on top. */
static int accepts(const struct sol *b, int p, int c, int n) {
    if (p >= P_FOUND && p < P_TAB)
        return n == 1 && suit_of(c) == p - P_FOUND && rank_of(c) == b->g.found[p - P_FOUND];
    if (p < P_TAB) return 0;
    int len = b->g.tlen[p - P_TAB];
    if (!len) return rank_of(c) == 12;
    int top = b->g.tab[p - P_TAB][len - 1];
    return (top & UP) && red(top) != red(c) && rank_of(top) == rank_of(c) + 1 && len + n <= NTAB;
}

/* The top n cards of p can be lifted together: one from the waste or a foundation, a column's
 * face-up run that steps down in alternate colours. */
static int liftable(const struct sol *b, int p, int n) {
    int len = plen(b, p);
    if (n < 1 || n > len || p == P_STOCK) return 0;
    if (p < P_TAB) return n == 1;
    const unsigned char *t = b->g.tab[p - P_TAB];
    for (int i = len - n; i < len; ++i)
        if (!(t[i] & UP) ||
            (i > len - n && (red(t[i]) == red(t[i - 1]) || rank_of(t[i - 1]) != rank_of(t[i]) + 1)))
            return 0;
    return 1;
}

/* Where the top n of from can go, best first: home, then the longest column, then an empty one (a
 * king that is its column's last card has nowhere new to go). How many, into out. */
static int dests(const struct sol *b, int from, int n, unsigned char *out) {
    int k = 0, c = pcard(b, from, plen(b, from) - n), whole = from >= P_TAB && n == plen(b, from);
    if (!liftable(b, from, n)) return 0;
    if (n == 1 && from != P_FOUND + suit_of(c) && accepts(b, P_FOUND + suit_of(c), c, 1))
        out[k++] = (unsigned char)(P_FOUND + suit_of(c));
    int first = k;
    for (int p = P_TAB; p < PLACES; ++p) {
        if (p == from || !b->g.tlen[p - P_TAB] || !accepts(b, p, c, n)) continue;
        int i = k++;
        for (; i > first && b->g.tlen[out[i - 1] - P_TAB] < b->g.tlen[p - P_TAB]; --i)
            out[i] = out[i - 1];
        out[i] = (unsigned char)p;
    }
    for (int p = P_TAB; p < PLACES && !whole; ++p)
        if (p != from && !b->g.tlen[p - P_TAB] && accepts(b, p, c, n)) out[k++] = (unsigned char)p;
    return k;
}

/* The cursor's places, in the wheel's order: the waste, the foundations, then each column's runs
 * from the deepest to its top card; only those with somewhere to go. */
static void sources(struct sol *b) {
    unsigned char d[8];
    int n = 0;
    for (int p = P_WASTE; p < PLACES; ++p) {
        int len = plen(b, p);
        for (int k = p >= P_TAB ? len : 1; k >= 1 && len; --k)
            if (n < MAXSRC && dests(b, p, k, d)) b->src[n][0] = (unsigned char)p, b->src[n++][1] = (unsigned char)k;
    }
    b->nsrc = n;
    if (b->cur >= n) b->cur = n ? n - 1 : 0;
}

/* Something can still move: a place now, or a card in the stock or the waste once drawn. */
static int can_move(const struct sol *b) {
    if (b->nsrc) return 1;
    for (int i = 0; i < b->g.slen + b->g.wlen; ++i) {
        int c = i < b->g.slen ? b->g.stock[i] : b->g.waste[i - b->g.slen];
        for (int p = P_FOUND; p < PLACES; ++p)
            if (accepts(b, p, c, 1)) return 1;
    }
    return 0;
}

static int won(const struct sol *b) {
    return b->g.found[0] + b->g.found[1] + b->g.found[2] + b->g.found[3] == 52;
}

/* Every card face up and nothing left to draw: Play finishes. */
static int finishable(const struct sol *b) {
    if (b->g.slen || b->g.wlen) return 0;
    for (int k = 0; k < 7; ++k)
        for (int i = 0; i < b->g.tlen[k]; ++i)
            if (!(b->g.tab[k][i] & UP)) return 0;
    return 1;
}

static void push(struct sol *b, int p, int c) {
    if (p == P_WASTE)
        b->g.waste[b->g.wlen++] = (unsigned char)(c & 63);
    else if (p == P_STOCK)
        b->g.stock[b->g.slen++] = (unsigned char)(c & 63);
    else if (p < P_TAB)
        b->g.found[p - P_FOUND]++;
    else
        b->g.tab[p - P_TAB][b->g.tlen[p - P_TAB]++] = (unsigned char)(c | UP);
}
/* The top card off place p, its slot cleared: a move undone leaves the state as it was, byte for byte. */
static int pop(struct sol *b, int p) {
    int c = pcard(b, p, plen(b, p) - 1);
    if (p == P_WASTE)
        b->g.waste[--b->g.wlen] = 0;
    else if (p == P_STOCK)
        b->g.stock[--b->g.slen] = 0;
    else if (p < P_TAB)
        b->g.found[p - P_FOUND]--;
    else
        b->g.tab[p - P_TAB][--b->g.tlen[p - P_TAB]] = 0;
    return c;
}

/* A move made, on the state alone (no picture): the solver's replay and the game's both. */
static void apply(struct sol *b, struct move *m) {
    if (m->kind == M_DRAW) {
        for (int i = 0; i < m->n; ++i) push(b, P_WASTE, pop(b, P_STOCK));
    } else if (m->kind == M_RECYCLE) {
        for (int i = 0; i < m->n; ++i) push(b, P_STOCK, pop(b, P_WASTE));
    } else {
        unsigned char run[NTAB];
        for (int i = m->n; i--;) run[i] = (unsigned char)pop(b, m->from);
        for (int i = 0; i < m->n; ++i) push(b, m->to, run[i]);
        int k = m->from - P_TAB, len = k >= 0 ? b->g.tlen[k] : 0;
        m->turned = k >= 0 && len && !(b->g.tab[k][len - 1] & UP);
        if (m->turned) b->g.tab[k][len - 1] |= UP;
    }
}

static void unapply(struct sol *b, const struct move *m) {
    if (m->kind == M_DRAW) {
        for (int i = 0; i < m->n; ++i) push(b, P_STOCK, pop(b, P_WASTE));
    } else if (m->kind == M_RECYCLE) {
        for (int i = 0; i < m->n; ++i) push(b, P_WASTE, pop(b, P_STOCK));
    } else {
        int k = m->from - P_TAB;
        if (m->turned) b->g.tab[k][b->g.tlen[k] - 1] &= (unsigned char)~UP;
        unsigned char run[NTAB];
        for (int i = m->n; i--;) run[i] = (unsigned char)pop(b, m->to);
        for (int i = 0; i < m->n; ++i) push(b, m->from, run[i]);
    }
}

/* Play's draw: the stock's next one or three, or the waste back over when the stock is out. */
static int draw_move(const struct sol *b, struct move *m) {
    m->from = P_STOCK, m->to = P_WASTE, m->turned = 0;
    if (b->g.slen) {
        int n = b->g.draw3 ? 3 : 1;
        m->kind = M_DRAW, m->n = (unsigned char)(n < b->g.slen ? n : b->g.slen);
    } else {
        m->kind = M_RECYCLE, m->n = b->g.wlen;
    }
    return m->n != 0;
}

/* The deal of seed: the deck shuffled by an xorshift of it (Fisher and Yates), the columns dealt row
 * by row, each column's last card face up, the rest to the stock, its top the deck's last. */
static void deal(struct sol *b, unsigned seed, int draw3) {
    unsigned char deck[52];
    unsigned x = seed ? seed : 1;
    for (int i = 0; i < 52; ++i) deck[i] = (unsigned char)i;
    for (int i = 51; i > 0; --i) {
        x ^= x << 13, x ^= x >> 17, x ^= x << 5;
        int j = (int)(x % (unsigned)(i + 1));
        unsigned char t = deck[i];
        deck[i] = deck[j], deck[j] = t;
    }
    memset(b->g.tab, 0, sizeof b->g.tab);
    memset(b->g.tlen, 0, sizeof b->g.tlen);
    memset(b->g.found, 0, sizeof b->g.found);
    memset(b->g.waste, 0, sizeof b->g.waste);
    int n = 0;
    for (int r = 0; r < 7; ++r)
        for (int c = r; c < 7; ++c) b->g.tab[c][b->g.tlen[c]++] = (unsigned char)(deck[n++] | (r == c ? UP : 0));
    for (int i = 0; i < 24; ++i) b->g.stock[i] = deck[n++];
    b->g.slen = 24, b->g.wlen = 0;
    b->g.seed = seed, b->g.draw3 = (unsigned char)draw3;
    b->g.moves = b->g.time_ms = 0;
}

/* ---- layout ---- */

/* A column's fan: face-down and face-up steps so its last card ends by the limit; 7 and 17 px
 * while there is room, tighter (5, then 3, and 11 at the least) only when there is not (SPEC.md). */
static void fan(int nd, int nu, int room, int *d, int *u) {
    static const unsigned char downs[3] = { 7, 5, 3 };
    int dd = 7, uu = 17;
    for (int k = 0; k < 3; ++k) {
        dd = downs[k];
        uu = nu <= 1 ? 17 : (room - dd * nd) / (nu - 1);
        uu = uu > 17 ? 17 : uu;
        if (uu >= 12) break;
    }
    *d = dd, *u = uu < 11 ? 11 : uu;
}

static void refan(struct sol *b) {
    for (int k = 0; k < 7; ++k) {
        int nd = 0, len = b->g.tlen[k];
        while (nd < len && !(b->g.tab[k][nd] & UP)) ++nd;
        fan(nd, len - nd, b->limit - TAB_Y - CH, &b->fan_d[k], &b->fan_u[k]);
    }
}

/* Where place p's card i lies (its top left); i may be the length: where the next would land. */
static void place_xy(const struct sol *b, int p, int i, int *x, int *y) {
    *y = TOP_Y;
    if (p == P_STOCK)
        *x = STOCK_X;
    else if (p == P_WASTE) {
        int len = b->g.wlen, first = b->g.draw3 ? (len > 3 ? len - 3 : 0) : len - 1;
        *x = WASTE_X + (b->g.draw3 && i > first ? (i - first) * WASTE_FAN : 0);
    } else if (p < P_TAB)
        *x = colx[3 + p - P_FOUND];
    else {
        int k = p - P_TAB, nd = 0;
        while (nd < b->g.tlen[k] && nd < i && !(b->g.tab[k][nd] & UP)) ++nd;
        *x = colx[k];
        *y = TAB_Y + (i <= nd ? i * b->fan_d[k] : nd * b->fan_d[k] + (i - nd) * b->fan_u[k]);
    }
}

/* The marks for a change: a card's box with its shadow and gold edge; a whole column. */
static void dirty_card(int x, int y, int h) { eng_dirty(x - 4, y - 5, CW + 10, h + 15); }
static void dirty_place(const struct sol *b, int p) {
    if (p >= P_TAB)
        eng_dirty(colx[p - P_TAB] - 4, TAB_Y - 6, CW + 10, b->limit - TAB_Y + 14);
    else if (p == P_WASTE)
        eng_dirty(WASTE_X - 4, TOP_Y - 6, CW + 2 * WASTE_FAN + 10, CH + 16);
    else {
        int x, y;
        place_xy(b, p, 0, &x, &y);
        eng_dirty(x - 6, y - 6, CW + 12, CH + 16);
    }
}
static void dirty_slip(const struct sol *b) { eng_dirty(SLIP_X0, b->rail, SLIP_X1 - SLIP_X0 + 4, RAIL_H); }

/* ---- sound: the Clicker's three, as rhythm ---- */

static void sound(struct sol *b, int kind, int n) {
    eng_sfx(kind);
    b->seq = kind, b->runs = n - 1, b->wait = 8;
}

/* ---- motion ---- */

static int in_flight(const struct sol *b, int p, int i) {
    for (int k = 0; k < b->nfl; ++k)
        if (b->fl[k].place == p && b->fl[k].idx == i) return k + 1;
    return 0;
}

static void fly(struct sol *b, int card, int p, int i, int x0, int y0, int wait, int dur, int arc,
                int flags) {
    if (b->nfl >= MAXF) return;
    struct flight *f = &b->fl[b->nfl++];
    int x1, y1;
    place_xy(b, p, i, &x1, &y1);
    f->card = (unsigned char)card, f->place = (unsigned char)p, f->idx = (unsigned char)i;
    f->flags = (unsigned char)flags;
    f->x0 = (short)x0, f->y0 = (short)y0, f->x1 = (short)x1, f->y1 = (short)y1;
    f->t = 0, f->dur = (unsigned char)dur, f->wait = (unsigned char)wait, f->arc = (unsigned char)arc;
}

/* ease-out cubic, of 256 */
static int ease(int t, int dur) {
    int r = 256 - t * 256 / dur;
    return 256 - r * r / 256 * r / 256;
}

static void flight_xy(const struct flight *f, int *x, int *y) {
    int e = ease(f->t, f->dur);
    *x = f->x0 + (f->x1 - f->x0) * e / 256;
    *y = f->y0 + (f->y1 - f->y0) * e / 256 -
         (f->arc ? f->arc * eng_sin(f->t * 512 / f->dur) / 65536 : 0);
}

static void land(struct sol *b, const struct flight *f) {
    int x = f->x1, y = f->y1;
    dirty_card(x, y, CH);
    if (f->flags & F_HOME) {
        b->flash_t = FLASH, b->flash_p = f->place;
        b->spark_t = SPARK, b->spark_p = f->place;
        eng_dirty(x - 40, y - 44, CW + 80, CH + 88);
    }
    if (f->flags & F_FLIP) b->flip_t = FLIP, b->flip_p = f->place, b->flip_i = f->idx;
}

/* Every card in flight lands now: input is never kept waiting for a glide. */
static void settle(struct sol *b) {
    for (int k = 0; k < b->nfl; ++k) {
        int x, y;
        flight_xy(&b->fl[k], &x, &y);
        dirty_card(x, y, CH);
        land(b, &b->fl[k]);
    }
    b->nfl = 0;
    if (b->flip_t) dirty_place(b, b->flip_p), b->flip_t = 0;
}

static int run_h(const struct sol *b, int p, int n) {
    return p >= P_TAB ? CH + (n - 1) * b->fan_u[p - P_TAB] : CH;
}

/* ---- the game ---- */

static void cursor_dirty(struct sol *b) {
    if (b->cur >= b->nsrc || b->mode != PLAY) return;
    int p = b->src[b->cur][0], n = b->src[b->cur][1], x, y;
    place_xy(b, p, plen(b, p) - n, &x, &y);
    dirty_card(x, y - 2, run_h(b, p, n) + 2);
}

/* The state changed: the cursor's places again, the fans, and the stuck message. */
static void changed(struct sol *b) {
    refan(b);
    sources(b);
    int stuck = b->mode == PLAY && !can_move(b);
    if (stuck != b->stuck) b->stuck = stuck, dirty_slip(b);
    if (stuck && b->msg != MSG_STUCK) {
        b->msg = MSG_STUCK, b->msg_t = 0;
        sound(b, ENG_SFX_ALERT, 1);
    } else if (!stuck && b->msg == MSG_STUCK)
        b->msg = MSG_NONE;
}

static void remember(struct sol *b, const struct move *m) {
    b->uhead = (b->uhead + 1) % UNDO;
    b->undo[b->uhead] = *m;
    b->ucount += b->ucount < UNDO;
}

/* Where the hand is now, gliding from hx0, hy0 to its spot hx, hy. */
static void hand_xy(const struct sol *b, int *x, int *y) {
    int e = ease(b->ht, HOVER);
    *x = b->hx0 + (b->hx - b->hx0) * e / 256, *y = b->hy0 + (b->hy - b->hy0) * e / 256;
}

/* A move made on the table: the cards glide from where they lay (or from the hand, at hx, hy) to
 * their new places; a card home flashes, a card turned turns. */
static void play_move(struct sol *b, struct move *m, int from_hand) {
    int n = m->n, x0[NTAB], y0[NTAB], len = plen(b, m->from);
    for (int i = 0; i < n; ++i) {
        if (from_hand) {
            hand_xy(b, &x0[i], &y0[i]);
            x0[i] += 2, y0[i] += 8 + i * (m->from >= P_TAB ? b->fan_u[m->from - P_TAB] : 0);
        } else
            place_xy(b, m->from, len - n + i, &x0[i], &y0[i]);
    }
    dirty_place(b, m->from), dirty_place(b, m->to);
    apply(b, m);
    remember(b, m);
    ++b->g.moves;
    b->unsaved = 1, ++b->since_save;
    changed(b);
    int to_len = plen(b, m->to);
    for (int i = 0; i < n; ++i)
        fly(b, pcard(b, m->to, to_len - n + i), m->to, to_len - n + i, x0[i], y0[i], 0, GLIDE, 0,
            m->to < P_TAB && m->to >= P_FOUND ? F_HOME : 0);
    dirty_place(b, m->from), dirty_place(b, m->to), dirty_slip(b);
    if (m->turned) b->flip_t = FLIP, b->flip_p = m->from, b->flip_i = plen(b, m->from) - 1;
    if (m->to >= P_FOUND && m->to < P_TAB)
        sound(b, ENG_SFX_TAP, 1);
    else
        sound(b, ENG_SFX_TAP, 1 + (m->turned != 0));
}

/* Play: draw, or the waste back to the stock. */
static void play_draw(struct sol *b) {
    struct move m;
    if (!draw_move(b, &m)) return;
    dirty_place(b, P_STOCK), dirty_place(b, P_WASTE);
    apply(b, &m);
    remember(b, &m);
    ++b->g.moves;
    b->unsaved = 1, ++b->since_save;
    changed(b);
    if (m.kind == M_DRAW)
        for (int i = 0; i < m.n; ++i)
            fly(b, b->g.waste[b->g.wlen - m.n + i] | UP, P_WASTE, b->g.wlen - m.n + i, STOCK_X, TOP_Y,
                i * 2, HOVER, 0, 0);
    else
        fly(b, b->g.stock[b->g.slen - 1], P_STOCK, b->g.slen - 1, WASTE_X, TOP_Y, 0, GLIDE, 0, F_DOWN);
    dirty_slip(b);
    sound(b, m.kind == M_DRAW ? ENG_SFX_TICK : ENG_SFX_TICK, m.kind == M_DRAW ? 1 : 3);
}

/* Previous: the last move undone, its cards gliding back. */
static void play_undo(struct sol *b) {
    if (!b->ucount) return;
    struct move m = b->undo[b->uhead];
    b->uhead = (b->uhead + UNDO - 1) % UNDO, --b->ucount;
    int n = m.kind == M_MOVE ? m.n : 1, x0[NTAB], y0[NTAB], at = m.kind == M_MOVE ? m.to : P_WASTE;
    if (m.kind == M_RECYCLE) at = P_STOCK;
    for (int i = 0; i < n; ++i) place_xy(b, at, plen(b, at) - n + i, &x0[i], &y0[i]);
    dirty_place(b, m.from), dirty_place(b, m.to);
    unapply(b, &m);
    if (b->g.moves) --b->g.moves;
    b->unsaved = 1, ++b->since_save;
    changed(b);
    if (m.kind == M_MOVE)
        for (int i = 0; i < n; ++i) {
            int len = plen(b, m.from);
            fly(b, pcard(b, m.from, len - n + i), m.from, len - n + i, x0[i], y0[i], 0, GLIDE, 0, 0);
        }
    else if (m.kind == M_DRAW)
        fly(b, b->g.stock[b->g.slen - 1], P_STOCK, b->g.slen - 1, x0[0], y0[0], 0, GLIDE, 0, F_DOWN);
    else
        fly(b, b->g.waste[b->g.wlen - 1] | UP, P_WASTE, b->g.wlen - 1, x0[0], y0[0], 0, GLIDE, 0, 0);
    dirty_place(b, m.from), dirty_place(b, m.to);
    b->msg = MSG_UNDO, b->msg_t = MSG_FRAMES;
    dirty_slip(b);
    sound(b, ENG_SFX_TICK, 1);
}

static void hand_dirty(const struct sol *b) {
    int h = run_h(b, b->hfrom, b->hn), x, y;
    hand_xy(b, &x, &y);
    eng_dirty(x - 4, y + 1, CW + 20, h + 34);
    for (int k = 0; k < b->ndst; ++k) { /* the gold outlines */
        int x, y, p = b->dst[k];
        place_xy(b, p, plen(b, p), &x, &y);
        eng_dirty(x - 4, y - 4, CW + 8, CH + 8);
    }
}

/* The hand's spot over destination k: it glides there from where it hovered. */
static void hover_to(struct sol *b, int k, int glide) {
    int x, y, p = b->dst[k];
    place_xy(b, p, plen(b, p), &x, &y);
    int x0 = x, y0 = y;
    if (b->hand) hand_dirty(b);
    if (glide) hand_xy(b, &x0, &y0);
    b->hx0 = x0, b->hy0 = y0;
    b->hover = k, b->hx = x, b->hy = y, b->ht = glide ? 0 : HOVER;
    hand_dirty(b);
}

static void pick(struct sol *b) {
    if (b->cur >= b->nsrc) return;
    int p = b->src[b->cur][0], n = b->src[b->cur][1], x, y;
    b->ndst = dests(b, p, n, b->dst);
    if (!b->ndst) return;
    cursor_dirty(b);
    place_xy(b, p, plen(b, p) - n, &x, &y);
    b->hand = 1, b->hfrom = p, b->hn = n;
    b->hx = x - 2, b->hy = y - 8, b->ht = HOVER; /* lifted where it lay, then away to the best place */
    hover_to(b, 0, 1);
    dirty_place(b, p);
    sound(b, ENG_SFX_TAP, 1);
}

static void put_back(struct sol *b) {
    hand_dirty(b);
    dirty_place(b, b->hfrom);
    b->hand = 0;
    cursor_dirty(b);
    sound(b, ENG_SFX_TICK, 1);
}

/* The cursor to the place nearest the one a move left: the same place, or the first after it. */
static void cursor_near(struct sol *b, int p) {
    for (int k = 0; k < b->nsrc; ++k)
        if (b->src[k][0] >= p) {
            b->cur = k;
            return;
        }
    b->cur = 0;
}

static void drop(struct sol *b, int k) {
    struct move m = { M_MOVE, (unsigned char)b->hfrom, b->dst[k], (unsigned char)b->hn, 0, { 0 } };
    hand_dirty(b);
    b->hand = 0;
    play_move(b, &m, 1);
    cursor_near(b, b->hfrom);
    cursor_dirty(b);
}

/* Next: the cursor's card (or the hand's) home, if it can go. */
static void send_home(struct sol *b) {
    int p;
    if (b->hand) {
        if (b->dst[0] >= P_FOUND && b->dst[0] < P_TAB) drop(b, 0);
        return;
    }
    if (b->cur >= b->nsrc) return;
    p = b->src[b->cur][0];
    int c = pcard(b, p, plen(b, p) - 1), home = P_FOUND + suit_of(c);
    if (p == home || !(c & UP) || !accepts(b, home, c, 1)) return;
    struct move m = { M_MOVE, (unsigned char)p, (unsigned char)home, 1, 0, { 0 } };
    cursor_dirty(b);
    play_move(b, &m, 0);
    cursor_near(b, p);
    cursor_dirty(b);
}

static void new_deal(struct sol *b) {
    unsigned seed = b->g.opt_random ? rand_below(~0u) | 1 : sol_seeds[rand_below(SOL_SEEDS)];
    deal(b, seed, b->g.opt_draw3);
    b->g.live = 1, b->g.won = 0;
    ++b->g.played;
    b->mode = DEAL, b->ucount = 0, b->hand = 0, b->cur = 0, b->msg = MSG_NONE, b->stuck = 0;
    b->nfl = 0, b->flash_t = b->spark_t = b->flip_t = 0, b->last_ms = 0, b->shown_s = 0;
    b->unsaved = 1, b->since_save = 0;
    refan(b);
    int n = 0; /* row by row, one every DEAL_GAP frames, each turned face up on its column as it lands */
    for (int r = 0; r < 7; ++r)
        for (int c = r; c < 7; ++c, ++n)
            fly(b, b->g.tab[c][r], P_TAB + c, r, STOCK_X, TOP_Y, n * DEAL_GAP, DEAL_FLY, 10,
                F_DOWN | (r == c ? F_FLIP : 0));
    b->deal_left = n;
    eng_dirty_all();
}

static void finish_deal(struct sol *b) {
    settle(b);
    b->mode = PLAY;
    changed(b);
    eng_dirty_all();
}

/* The next card the auto-finish sends home: the lowest any column's top or the waste offers. */
static int auto_next(const struct sol *b) {
    int best = -1, rank = 99;
    for (int p = P_WASTE; p < PLACES; ++p) {
        if (p >= P_FOUND && p < P_TAB) continue;
        int len = plen(b, p);
        if (!len) continue;
        int c = pcard(b, p, len - 1);
        if (accepts(b, P_FOUND + suit_of(c), c, 1) && rank_of(c) < rank) best = p, rank = rank_of(c);
    }
    return best;
}

static void win(struct sol *b) {
    b->mode = WON, b->t = 0, b->kings = 0, b->hand = 0;
    b->g.live = 0, b->g.won = 1;
    ++b->g.wins;
    b->best_new = !b->g.best_ms || b->g.time_ms < b->g.best_ms;
    if (b->best_new) b->g.best_ms = b->g.time_ms;
    if (!b->g.fewest || b->g.moves < b->g.fewest) b->g.fewest = b->g.moves;
    b->unsaved = 1;
    eng_save("solitaire", &b->g, sizeof b->g, S_MAGIC); /* the game's end, not a frame of play */
    b->unsaved = 0, b->since_save = 0;
    dirty_slip(b);
}

/* ---- the start board ---- */

#define BOARD_W 210
#define BOARD_H 258
#define BOARD_Y 22
#define ROW_H 28
static int rows(const struct sol *b) { return b->g.live ? 3 : 2; }
static int row_y(int r) { return BOARD_Y + 44 + 4 + r * ROW_H; }

static void start_act(struct sol *b) {
    if (b->sel == 0) b->g.opt_random ^= 1;
    if (b->sel == 1) b->g.opt_draw3 ^= 1;
    b->unsaved = 1;
    if (b->sel == 2) { /* resume */
        b->mode = PLAY, b->last_ms = 0;
        changed(b);
        eng_dirty_all();
        return;
    }
    eng_dirty((ENG_W - BOARD_W) / 2, row_y(0), BOARD_W, 2 * ROW_H);
    sound(b, ENG_SFX_TAP, 1);
}

/* ---- a step ---- */

static int hit(int x, int y, int w, int h, const struct eng_input *in) {
    return in->tx >= x && in->tx < x + w && in->ty >= y && in->ty < y + h;
}

/* A finger in play: the stock draws; with a card in hand a legal place takes it and anywhere else
 * puts it back; else the card or run under the finger, if it can move, is picked up. */
static void touch(struct sol *b, const struct eng_input *in) {
    if (b->hand) {
        for (int k = 0; k < b->ndst; ++k) {
            int x, y, p = b->dst[k];
            place_xy(b, p, plen(b, p), &x, &y);
            if (p >= P_TAB && plen(b, p)) y -= b->fan_u[p - P_TAB];
            if (hit(x, y, CW, CH + 20, in)) {
                drop(b, k);
                return;
            }
        }
        put_back(b);
        return;
    }
    if (hit(STOCK_X, TOP_Y, CW, CH, in)) {
        play_draw(b);
        return;
    }
    for (int k = b->nsrc; k--;) { /* the topmost first: a column's top card over its runs */
        int p = b->src[k][0], n = b->src[k][1], x, y, len = plen(b, p);
        place_xy(b, p, len - n, &x, &y);
        int h = n == 1 ? CH : (p >= P_TAB ? b->fan_u[p - P_TAB] : CH);
        if (hit(x, y, CW, h, in)) {
            cursor_dirty(b);
            b->cur = k;
            pick(b);
            return;
        }
    }
}

static void play_input(struct sol *b, const struct eng_input *in) {
    if (in->ticks || in->pressed) settle(b);
    if (b->hand) {
        if (in->ticks && b->ndst > 1) {
            int k = ((b->hover + in->ticks) % b->ndst + b->ndst) % b->ndst;
            hover_to(b, k, 1);
            sound(b, ENG_SFX_TICK, 1);
        }
        if (in->pressed & ENG_CENTRE)
            drop(b, b->hover);
        else if (in->pressed & ENG_PREV)
            put_back(b);
        else if (in->pressed & ENG_NEXT)
            send_home(b);
        else if (in->pressed & ENG_TOUCH)
            touch(b, in);
        return;
    }
    if (in->ticks && b->nsrc) {
        cursor_dirty(b);
        b->cur = ((b->cur + in->ticks) % b->nsrc + b->nsrc) % b->nsrc;
        cursor_dirty(b);
        sound(b, ENG_SFX_TICK, 1);
    }
    if (in->pressed & ENG_CENTRE) {
        if (b->nsrc)
            pick(b);
        else
            play_draw(b);
    } else if (in->pressed & ENG_PLAY) {
        if (finishable(b)) {
            cursor_dirty(b);
            b->mode = AUTO, b->auto_wait = 0;
            dirty_slip(b);
        } else
            cursor_dirty(b), play_draw(b), cursor_near(b, 0), cursor_dirty(b);
    } else if (in->pressed & ENG_PREV) {
        cursor_dirty(b);
        play_undo(b);
        cursor_dirty(b);
    } else if (in->pressed & ENG_NEXT)
        send_home(b);
    else if (in->pressed & ENG_TOUCH)
        touch(b, in);
}

static void start_input(struct sol *b, const struct eng_input *in) {
    int n = rows(b);
    if (in->ticks) {
        b->sel = ((b->sel + in->ticks) % n + n) % n;
        eng_dirty((ENG_W - BOARD_W) / 2, row_y(0), BOARD_W, 3 * ROW_H);
        sound(b, ENG_SFX_TICK, 1);
    }
    if (in->pressed & ENG_TOUCH)
        for (int r = 0; r < n; ++r)
            if (hit((ENG_W - BOARD_W) / 2 + 13, row_y(r), BOARD_W - 26, ROW_H, in)) b->sel = r, start_act(b);
    if (in->pressed & ENG_CENTRE)
        start_act(b);
    else if (in->pressed & ENG_PLAY || (in->pressed & ENG_TOUCH && in->ty >= b->rail))
        new_deal(b), sound(b, ENG_SFX_TAP, 1);
}

/* The time of play: the clock's, between steps of play; a pause starts it over (last_ms 0), so time
 * away is not counted. The slip shows it as of the last step that painted. */
static void clock(struct sol *b) {
    unsigned now = time_now_ms();
    if (b->last_ms) {
        unsigned gap = now - b->last_ms;
        b->g.time_ms += gap > 600000 ? 600000 : gap;
    }
    b->last_ms = now ? now : 1;
    int s = (int)(b->g.time_ms / 1000);
    if (s != b->shown_s) b->shown_s = s, dirty_slip(b);
}

static int animate(struct sol *b) {
    int moving = 0;
    for (int k = 0; k < b->nfl;) {
        struct flight *f = &b->fl[k];
        int x, y;
        moving = 1;
        if (f->wait) {
            if (!--f->wait && b->mode == DEAL && (f->idx & 1)) sound(b, ENG_SFX_TICK, 1);
            if (f->wait) {
                ++k;
                continue;
            }
        }
        flight_xy(f, &x, &y);
        dirty_card(x, y, CH);
        ++f->t;
        flight_xy(f, &x, &y);
        dirty_card(x, y, CH);
        if (f->t >= f->dur) {
            land(b, f);
            if (b->mode == DEAL) --b->deal_left;
            b->fl[k] = b->fl[--b->nfl];
        } else
            ++k;
    }
    if (b->flash_t) {
        int x, y;
        place_xy(b, b->flash_p, 0, &x, &y);
        if (!--b->flash_t) dirty_card(x, y, CH);
        moving = 1;
    }
    if (b->spark_t) {
        int x, y;
        place_xy(b, b->spark_p, 0, &x, &y);
        eng_dirty(x - 40, y - 44, CW + 80, CH + 88);
        --b->spark_t;
        moving = 1;
    }
    if (b->flip_t) {
        dirty_place(b, b->flip_p);
        --b->flip_t;
        moving = 1;
    }
    if (b->hand && b->ht < HOVER) {
        hand_dirty(b);
        ++b->ht;
        hand_dirty(b);
        moving = 1;
    }
    if (b->msg == MSG_UNDO && b->msg_t && !--b->msg_t) b->msg = MSG_NONE, dirty_slip(b);
    if (b->msg_t) moving = 1;
    if (b->runs && !--b->wait) eng_sfx(b->seq), b->wait = 8, --b->runs;
    return moving || b->runs;
}

static int step(void *state, const struct eng_input *in) {
    struct sol *b = state;
    int moving;
    if (b->mode == PLAY || b->mode == AUTO) clock(b);
    if (b->mode == START)
        start_input(b, in);
    else if (b->mode == DEAL) {
        if (in->pressed || in->ticks) finish_deal(b);
    } else if (b->mode == PLAY)
        play_input(b, in);
    else if (b->mode == AUTO) {
        if (in->pressed & (ENG_CENTRE | ENG_PLAY)) { /* the rest home at once */
            settle(b);
            for (int p; (p = auto_next(b)) >= 0; ++b->g.moves) {
                struct move m = { M_MOVE, (unsigned char)p, (unsigned char)(P_FOUND + suit_of(pcard(b, p, plen(b, p) - 1))), 1, 0, { 0 } };
                apply(b, &m);
            }
            eng_dirty_all();
        }
    } else if (b->mode == WON && (in->pressed & (ENG_CENTRE | ENG_PLAY | ENG_TOUCH)))
        new_deal(b), sound(b, ENG_SFX_TAP, 1);
    moving = animate(b);
    if (b->mode == DEAL && !b->deal_left && !b->nfl && !b->flip_t) finish_deal(b);
    if (b->mode == AUTO) {
        moving = 1;
        if (b->auto_wait)
            --b->auto_wait;
        else if (auto_next(b) >= 0) {
            int p = auto_next(b);
            struct move m = { M_MOVE, (unsigned char)p, (unsigned char)(P_FOUND + suit_of(pcard(b, p, plen(b, p) - 1))), 1, 0, { 0 } };
            play_move(b, &m, 0);
            b->auto_wait = AUTO_GAP - 1;
        }
    }
    if ((b->mode == PLAY || b->mode == AUTO) && won(b) && !b->nfl) win(b);
    if (b->mode == WON) {
        ++b->t;
        if (b->t <= 4 * WIN_GAP && b->t % WIN_GAP == 0) {
            b->kings = b->t / WIN_GAP;
            dirty_place(b, P_FOUND + b->kings - 1);
            eng_sfx(ENG_SFX_TICK);
        }
        if (b->t >= DROP_AT && b->t <= DROP_AT + DROP + 1) eng_dirty(60, 0, 256, 300);
        if (b->best_new && b->t >= DROP_AT + DROP && b->t <= DROP_AT + DROP + TAG + 1)
            eng_dirty(220, 70, 155, 50);
        if (b->best_new && b->t == DROP_AT + DROP + 1) sound(b, ENG_SFX_TAP, 2);
        moving = b->t <= DROP_AT + DROP + TAG + 1 || moving;
    }
    /* every tenth move reaches the card, never during a glide (docs/games-engine.md#cabin-solitaire) */
    if (b->since_save >= SAVE_EVERY && !b->nfl && b->mode == PLAY) {
        if (eng_save("solitaire", &b->g, sizeof b->g, S_MAGIC)) b->unsaved = 0;
        b->since_save = 0;
    }
    return moving;
}

/* ---- drawing ---- */

static const unsigned char *bytes_at(const struct sol *b, int off) {
    return (const unsigned char *)b->file + off;
}

static inline unsigned blendc(unsigned dst, unsigned rgb, unsigned a) { /* a of 256 */
    unsigned rb = ((rgb & 0xff00ff) * a + (dst & 0xff00ff) * (256 - a)) >> 8 & 0xff00ff;
    unsigned g = ((rgb & 0xff00) * a + (dst & 0xff00) * (256 - a)) >> 8 & 0xff00;
    return 0xff000000 | rb | g;
}

static inline unsigned *px(const struct eng_surface *s, int x, int y) {
    return s->px + (y + s->oy) * s->stride + x + s->ox;
}

static int clip(const struct eng_surface *s, int x, int y, int w, int h, int *c) {
    c[0] = x < s->x0 ? s->x0 : x, c[1] = y < s->y0 ? s->y0 : y;
    c[2] = x + w > s->x1 ? s->x1 : x + w, c[3] = y + h > s->y1 ? s->y1 : y + h;
    return c[2] > c[0] && c[3] > c[1];
}

/* The felt: the fibre tile repeated, shaded by the map's 4 px cells (cards.felt_image). */
static void felt(const struct sol *b, struct eng_surface *s, int x, int y, int w, int h) {
    int c[4];
    if (!clip(s, x, y, w, h, c)) return;
    if (!b->art) {
        eng_fill(s, c[0], c[1], c[2] - c[0], c[3] - c[1], FELT);
        return;
    }
    const unsigned char *map = bytes_at(b, ART_SHADE);
    for (int j = c[1]; j < c[3]; ++j) {
        const unsigned char *t = b->tile + (j & 63) * 64, *m = map + (j >> 2) * ART_SHADE_W;
        unsigned *p = px(s, c[0], j);
        for (int i = c[0]; i < c[2];) {
            const unsigned *lut = b->felt_lut + m[i >> 2] * 15;
            int end = (i | 3) + 1 < c[2] ? (i | 3) + 1 : c[2];
            for (; i < end; ++i) *p++ = lut[t[i & 63]];
        }
    }
}

/* A rounded box with a smoothed edge at a (of 256), its corners in 1/16 px (x0, y0, x1, y1 and the
 * radius r); with line, only its rim line/16 px wide. The pixels of hole (x, y, w, h, whole px), which
 * a card painted after covers, are left. */
static void rbox(struct eng_surface *s, int x0, int y0, int x1, int y1, int r, unsigned rgb, int a,
                 int line, const int *hole) {
    int c[4];
    if (!clip(s, x0 >> 4, y0 >> 4, ((x1 + 15) >> 4) - (x0 >> 4), ((y1 + 15) >> 4) - (y0 >> 4), c))
        return;
    for (int j = c[1]; j < c[3]; ++j) {
        int cy = j * 16 + 8, qy = y0 + r - cy > cy - y1 + r ? y0 + r - cy : cy - y1 + r;
        int hx0 = 1 << 20, hx1 = -1;
        if (hole && j >= hole[1] && j < hole[1] + hole[3]) {
            int corner = j < hole[1] + 5 || j >= hole[1] + hole[3] - 5;
            hx0 = hole[0] + (corner ? 5 : 0), hx1 = hole[0] + hole[2] - (corner ? 5 : 0);
        }
        unsigned *p = px(s, c[0], j);
        for (int i = c[0]; i < c[2]; ++i, ++p) {
            if (i >= hx0 && i < hx1) continue;
            int cx = i * 16 + 8, qx = x0 + r - cx > cx - x1 + r ? x0 + r - cx : cx - x1 + r, d;
            if (qx <= 0 && qy <= 0)
                d = (qx > qy ? qx : qy) - r;
            else if (qx <= 0)
                d = qy - r;
            else if (qy <= 0)
                d = qx - r;
            else
                d = (int)eng_sqrt((unsigned long long)(qx * qx + qy * qy)) - r;
            int cov = 8 - d, k = cov <= 0 ? 0 : cov >= 16 ? 256 : cov * 16;
            if (line) {
                int e = 8 - d - line, kin = e <= 0 ? 0 : e >= 16 ? 256 : e * 16;
                k -= kin;
            }
            k = k * a >> 8;
            if (k >= 256)
                *p = 0xff000000 | rgb;
            else if (k > 0)
                *p = blendc(*p, rgb, (unsigned)k);
        }
    }
}
#define R16(v) ((int)((v) * 16))

static void box(struct eng_surface *s, int x, int y, int w, int h, unsigned rgb, int a) {
    rbox(s, x * 16, y * 16, (x + w) * 16, (y + h) * 16, 0, rgb, a, 0, 0);
}

/* An entry's bits: a mask's coverage drawn in rgb, or a palette image's colours, its index 0 left. */
static void art(const struct sol *b, struct eng_surface *s, int e, int x, int y, unsigned rgb) {
    if (!b->art) return;
    const unsigned char *ent = bytes_at(b, 4 + 8 * e);
    unsigned off = ent[0] | ent[1] << 8 | ent[2] << 16 | (unsigned)ent[3] << 24;
    int w = ent[4], h = ent[5], c[4], pal = off >> 31;
    x += (signed char)ent[6], y += (signed char)ent[7];
    if (!clip(s, x, y, w, h, c)) return;
    const unsigned char *d = bytes_at(b, (int)(off & 0x7fffffff));
    const unsigned *colour = (const unsigned *)d;
    if (pal) d += 64;
    for (int j = c[1]; j < c[3]; ++j) {
        unsigned *p = px(s, c[0], j);
        int n = (j - y) * w + c[0] - x;
        for (int i = c[0]; i < c[2]; ++i, ++n, ++p) {
            int v = d[n >> 1] >> ((n & 1) << 2) & 15;
            if (!v) continue;
            if (pal)
                *p = 0xff000000 | colour[v];
            else
                *p = v == 15 ? 0xff000000 | rgb : blendc(*p, rgb, (unsigned)(v * 273 >> 4));
        }
    }
}

static const unsigned char style_px[ART_STYLE_COUNT] = { 15, 15, 16, 18, 14, 14, 22, 23 };

static int text_w(const struct sol *b, int style, const char *t) { /* in 1/4 px */
    const unsigned char *adv = bytes_at(b, ART_STYLES + style * 192 + 96);
    int w = 0;
    for (; *t; ++t)
        if (*t >= 32) w += adv[*t - 32];
    return w;
}

/* Text in a style of the art's type, its middle line at y; from x (0), centred (1) or ending (2). */
static void say(const struct sol *b, struct eng_surface *s, int style, const char *t, int x, int y,
                 unsigned rgb, int align) {
    if (!b->art) {
        eng_text(s, t, x, y - style_px[style] / 2 - 3, style_px[style] + 6, style_px[style], rgb, align);
        return;
    }
    const unsigned char *map = bytes_at(b, ART_STYLES + style * 192), *adv = map + 96;
    int pen = x * 4 - (align == 1 ? text_w(b, style, t) / 2 : align == 2 ? text_w(b, style, t) : 0);
    for (; *t; ++t) {
        if (*t < 32) continue;
        if (map[*t - 32] != 255) art(b, s, map[*t - 32], (pen + 2) >> 2, y, rgb);
        pen += adv[*t - 32];
    }
}

static char *digits(char *out, unsigned v) {
    char d[12];
    int n = 0;
    do d[n++] = (char)('0' + v % 10);
    while (v /= 10);
    while (n) *out++ = d[--n];
    *out = 0;
    return out;
}
static char *words(char *out, const char *w) {
    while (*w) *out++ = *w++;
    *out = 0;
    return out;
}
static char *time_text(char *out, unsigned ms) {
    unsigned s = ms / 1000;
    if (s > 359999) s = 359999;
    out = digits(out, s / 60);
    *out++ = ':', *out++ = (char)('0' + s % 60 / 10), *out++ = (char)('0' + s % 10), *out = 0;
    return out;
}

/* A card's body: the 1 px warm edge and the face, its corners smoothed over what lies under it. */
static void body(const struct sol *b, struct eng_surface *s, int x, int y, int w) {
    int c[4];
    if (!clip(s, x, y, w, CH, c)) return;
    unsigned rim = blendc(EDGE, FACE, 26);
    for (int j = c[1]; j < c[3]; ++j) {
        int v = j - y, cv = v < 5 ? v : v >= CH - 5 ? CH - 1 - v : -1;
        unsigned *p = px(s, c[0], j);
        for (int i = c[0]; i < c[2]; ++i, ++p) {
            int u = i - x, cu = u < 5 ? u : u >= w - 5 ? w - 1 - u : -1;
            if (cu >= 0 && cv >= 0) {
                unsigned q = blendc(*p, EDGE, b->a_o[cv][cu]);
                *p = blendc(q, FACE, b->a_f[cv][cu]);
            } else
                *p = 0xff000000 | (u == 0 || u == w - 1 || v == 0 || v == CH - 1 ? rim : FACE);
        }
    }
}

/* ink of a suit */
static unsigned ink(int c) { return red(c) ? RED : BLACK; }

/* The card's face or back over its body. */
static void face(const struct sol *b, struct eng_surface *s, int x, int y, int c) {
    if (!(c & UP)) {
        if (b->art)
            art(b, s, A_BACK, x, y, 0);
        else
            eng_round(s, x + 3, y + 3, CW - 6, CH - 6, 3, 0xa8232b);
        return;
    }
    int r = rank_of(c), k = suit_of(c);
    unsigned col = ink(c);
    if (!b->art) {
        static const char *const names[13] = { "A", "2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K" };
        eng_text(s, names[r], x + 3, y, 14, 12, col, 0);
        return;
    }
    art(b, s, A_RANK_A + r, x, y, col);
    art(b, s, A_SUIT_h + k, x, y, col);
    if (r >= 10)
        art(b, s, A_COURT_J_RED + (r - 10) * 2 + !red(c), x, y, 0);
    else
        art(b, s, (r ? A_PIP_h : A_ACE_h) + k, x, y, col);
}

enum { C_PLAIN, C_FLAT, C_CURSOR, C_HOME, C_LIFT }; /* C_FLAT: no drop shadow (raised in a run) */

/* A card at x, y in one of its looks, drawn only above row lim (the next card in a fan covers the
 * rest). card: UP when face up. */
static void card(const struct sol *b, struct eng_surface *s0, int x, int y, int c, int look, int lim) {
    struct eng_surface sc = *s0, *s = &sc;
    if (lim < s->y1) s->y1 = lim;
    if (s->y1 <= s->y0 || x - 6 >= s->x1 || x + CW + 10 <= s->x0 || y - 6 >= s->y1 || y + CH + 24 <= s->y0) return;
    int hole[4] = { x, y, CW, CH };
    if (look == C_LIFT) {
        rbox(s, R16(x + 5), R16(y + 9), R16(x + CW + 5), R16(y + CH + 9), 80, SHADOW, 120 * 256 / 255, 0, 0);
        x -= 3, y -= 6, hole[0] = x, hole[1] = y;
        rbox(s, R16(x - 2), R16(y - 2), R16(x + CW + 2), R16(y + CH + 2), 104, GOLD, 256, 0, hole);
    } else if (look == C_CURSOR) {
        rbox(s, R16(x + 1.5), R16(y + 2.5), R16(x + CW + 1.5), R16(y + CH + 2.5), 80, SHADOW, 120 * 256 / 255, 0, hole);
        y -= 1, hole[1] = y;
        rbox(s, R16(x - 2), R16(y - 2), R16(x + CW + 2), R16(y + CH + 2), 104, GOLD, 256, 0, hole);
    } else if (look == C_HOME) {
        rbox(s, R16(x - 3), R16(y - 3), R16(x + CW + 3), R16(y + CH + 3), 120, GOLD_L, 150 * 256 / 255, 0, hole);
        rbox(s, R16(x - 1.5), R16(y - 1.5), R16(x + CW + 1.5), R16(y + CH + 1.5), 96, GOLD, 256, 0, hole);
    } else if (look == C_PLAIN) {
        rbox(s, R16(x + 1), R16(y + 2), R16(x + CW + 1), R16(y + CH + 2), 72, SHADOW, 60 * 256 / 255, 0, hole);
        rbox(s, R16(x + 0.5), R16(y + 1), R16(x + CW + 0.5), R16(y + CH + 1), 72, SHADOW, 90 * 256 / 255, 0, hole);
    }
    if (look != C_LIFT)
        rbox(s, R16(x - 0.5), R16(y - 1.2), R16(x + CW + 0.5), R16(y + 3), 72, CONTACT, 70 * 256 / 255, 0, hole);
    body(b, s, x, y, CW);
    face(b, s, x, y, c);
}

/* An empty place: a pressed outline, a foundation's suit, the stock's ring, a column's K. */
static void slot(const struct sol *b, struct eng_surface *s, int x, int y, int mark) {
    rbox(s, x * 16, y * 16, (x + CW) * 16, (y + CH) * 16, 72, SLOT_IN, 150 * 256 / 255, 0, 0);
    rbox(s, x * 16, y * 16, (x + CW) * 16, (y + CH) * 16, 72, SLOT_LINE, 256, 16, 0);
    art(b, s, mark, x, y, SLOT_LINE);
}

/* A legal place for the hand: a gold outline where it would land, the hovered one brighter. */
static void ghost(struct eng_surface *s, int x, int y, int best) {
    rbox(s, R16(x - 1), R16(y - 1), R16(x + CW + 1), R16(y + CH + 1), 88, GOLD, (best ? 70 : 30) * 256 / 255, 0, 0);
    rbox(s, R16(x - 1.5), R16(y - 1.5), R16(x + CW + 1.5), R16(y + CH + 1.5), 96, best ? GOLD_L : GOLD, 256,
         best ? R16(1.8) : R16(1.2), 0);
}

static void rail(const struct sol *b, struct eng_surface *s, const char *msg, unsigned moves,
                 unsigned ms) {
    int y = b->rail, x0 = SLIP_X0, x1 = SLIP_X1, top = y + 2;
    if (s->y1 <= y - 2) return;
    box(s, 0, y, ENG_W, b->h - y, RAIL, 256);
    box(s, 0, y, ENG_W, 2, RAIL_L, 256);
    box(s, 0, y - 2, ENG_W, 2, SHADOW, 110 * 256 / 255);
    for (int k = 0; k < 2; ++k) { /* worn grain along the rail */
        int gx = k ? 290 : 40, gw = k ? 40 : 44;
        rbox(s, gx * 16, (y + 8) * 16, (gx + gw) * 16, R16(y + 8.8), 0, RAIL_D, 120 * 256 / 255, 0, 0);
        rbox(s, (gx + 8) * 16, (y + 15) * 16, (gx + gw + 20) * 16, R16(y + 15.6), 0, RAIL_D, 90 * 256 / 255, 0, 0);
    }
    rbox(s, (x0 + 2) * 16, (top + 2) * 16, (x1 + 2) * 16, (b->h + 6) * 16, 48, SLIP_SHADOW, 150 * 256 / 255, 0, 0);
    rbox(s, x0 * 16, top * 16, x1 * 16, (b->h + 6) * 16, 48, FACE, 256, 0, 0);
    box(s, x0 + 3, top, x1 - x0 - 6, 1, 0xffffff, 256);
    int ty = top + 10; /* the design's 9.6 */
    char t[32];
    if (msg) {
        say(b, s, T_SLIP, msg, (x0 + x1) / 2, ty, PEN, 1);
        return;
    }
    say(b, s, T_LABEL, "Moves", x0 + 12, ty, PEN_SOFT, 0);
    digits(t, moves);
    say(b, s, T_FIGURE, t, x0 + 18 + (b->art ? text_w(b, T_LABEL, "Moves") / 4 : 44), ty, PEN, 0);
    time_text(t, ms);
    say(b, s, T_FIGURE, t, x1 - 12, ty, PEN, 2);
}

static void disc(struct eng_surface *s, int x16, int y16, int r16, unsigned rgb) {
    eng_disc(s, x16 << 12, y16 << 12, r16 << 12, rgb);
}

/* A wooden board with brass pins, centred on x, y (render.board). */
static void board(struct eng_surface *s, int x, int y, int w, int h) {
    int l = x * 16 - w * 8, t = y * 16 - h * 8, r = x * 16 + w * 8, bt = y * 16 + h * 8;
    if (s->x1 * 16 <= l || s->x0 * 16 >= r + 48 || s->y1 * 16 <= t || s->y0 * 16 >= bt + 80) return;
    rbox(s, l + 48, t + 80, r + 48, bt + 80, 128, SHADOW, 130 * 256 / 255, 0, 0);
    rbox(s, l, t, r, bt, 128, RAIL_D, 256, 0, 0);
    rbox(s, l + 48, t + 48, r - 48, bt - 48, 96, RAIL, 256, 0, 0);
    rbox(s, l + 48, t + 48, r - 48, t + 80, 32, RAIL_L, 256, 0, 0);
    for (int k = 0; k < 4; ++k) {
        int px_ = k & 1 ? r - 160 : l + 160, py = k & 2 ? bt - 160 : t + 160;
        disc(s, px_, py, R16(2.2), PIN);
        disc(s, px_ - 8, py - 8, R16(0.8), GOLD_L);
    }
}

/* A paper score card on a board (render.score_card): labels and figures, sel's row on gold. The
 * rows' labels, values; x0..x1 and y0 in 1/16 px; its foot, in px. */
static int score_card(const struct sol *b, struct eng_surface *s, int x0, int x1, int y0, int n,
                      const char *const *label, const char *const *value, int rh, int sel) {
    int y1 = y0 + (8 + rh * n) * 16;
    rbox(s, x0 + 24, y0 + 32, x1 + 24, y1 + 32, 48, SLIP_SHADOW, 150 * 256 / 255, 0, 0);
    rbox(s, x0, y0, x1, y1, 48, FACE, 256, 0, 0);
    for (int i = 0; i < n; ++i) {
        int yy = y0 + (4 * 2 + rh * (2 * i + 1)) * 8; /* the row's middle, 1/16 px */
        if (i == sel) rbox(s, x0 + 48, yy - rh * 8 + 16, x1 - 48, yy + rh * 8 - 16, 64, GOLD, 256, 0, 0);
        say(b, s, T_LABEL, label[i], (x0 >> 4) + 10, (yy + 8) >> 4, i == sel ? PEN : PEN_SOFT, 0);
        say(b, s, T_VALUE, value[i], (x1 >> 4) - 10, (yy + 8) >> 4, PEN, 2);
        if (i < n - 1 && i != sel && i + 1 != sel)
            rbox(s, x0 + 128, yy + rh * 8 - 5, x1 - 128, yy + rh * 8 + 5, 0, RULE, 256, 0, 0);
    }
    return (y1 + 15) >> 4;
}

static void start_board(const struct sol *b, struct eng_surface *s) {
    char wins[24], best[12], fewest[12], resume[12];
    for (int i = 0; i < 5; ++i) { /* the squared deck waits, three court cards fanned by the board */
        static const unsigned char off[5] = { 0, 1, 1, 2, 3 };
        card(b, s, 30 - off[i], 120 - off[i], 0, C_PLAIN, 1 << 20);
    }
    card(b, s, 304, 92, (12 + 13) | UP, C_PLAIN, 1 << 20);
    card(b, s, 312, 126, 11 | UP, C_PLAIN, 1 << 20);
    card(b, s, 300, 160, (10 + 39) | UP, C_PLAIN, 1 << 20);
    int x = ENG_W / 2, y = BOARD_Y + BOARD_H / 2;
    board(s, x, y, BOARD_W, BOARD_H);
    say(b, s, T_TITLE, "Cabin Solitaire", x, BOARD_Y + 25, GOLD_L, 1);
    const char *labels[3] = { "Deal", "Draw", "Resume" };
    time_text(resume, b->g.time_ms);
    const char *values[3] = { b->g.opt_random ? "Random" : "Winnable",
                              b->g.opt_draw3 ? "Three cards" : "One card", resume };
    int x0 = x * 16 + 8 - (BOARD_W / 2 - 13) * 16, x1 = x * 16 + 8 + (BOARD_W / 2 - 13) * 16;
    int y1 = score_card(b, s, x0, x1, (BOARD_Y + 44) * 16, rows(b), labels, values, ROW_H, b->sel);
    *digits(words(digits(wins, b->g.wins), " of "), b->g.played) = 0;
    if (b->g.best_ms)
        time_text(best, b->g.best_ms);
    else
        words(best, "-");
    if (b->g.fewest)
        digits(fewest, b->g.fewest);
    else
        words(fewest, "-");
    const char *const wl[3] = { "Wins", "Best time", "Fewest moves" }, *const wv[3] = { wins, best, fewest };
    for (int i = 0, yy = y1 + 18; i < 3; ++i, yy += 23) {
        say(b, s, T_WOOD, wl[i], (x0 >> 4) + 4, yy, WOOD_LABEL, 0);
        say(b, s, T_FIGURE, wv[i], (x1 >> 4) - 4, yy, WOOD_TEXT, 2);
    }
    say(b, s, T_HINT, "Center: change", x, BOARD_Y + BOARD_H - 17, HINT_INK, 1);
}


static void win_board(const struct sol *b, struct eng_surface *s) {
    int t = b->t - DROP_AT;
    if (t < 0) return;
    int w = 232, h = 12 + 32 + 8 + 24 * 4 + 30, x = ENG_W / 2, rest = 192;
    /* down past its place by 5 px in 10 frames, eased out, then back up in the last 4 */
    int y = t >= DROP ? rest : t < DROP - 4 ? -h + (rest + 5 + h) * ease(t, DROP - 4) / 256 : rest + 5 - 5 * (t - DROP + 4) / 4;
    char tm[12], best[12], moves[12], wins[24];
    for (int i = 0; i < 26; ++i) { /* gold motes, where the light caught them */
        int a = i * 391, r = 40 + (i * 37) % 120; /* 2.4 rad apart */
        disc(s, R16(110) + R16(1.3) * r * eng_cos(a) / 65536, R16(190) + 8 * r * eng_sin(a) / 65536,
             R16(1.2) + (i % 3) * 8, GOLD_L);
    }
    board(s, x, y, w, h);
    say(b, s, T_TITLE_L, "Well played", x, y - h / 2 + 25, GOLD_L, 1);
    time_text(tm, b->g.time_ms);
    time_text(best, b->g.best_ms);
    digits(moves, b->g.moves);
    *digits(words(digits(wins, b->g.wins), " of "), b->g.played) = 0;
    const char *const l[4] = { "Time", "Best", "Moves", "Wins" }, *const v[4] = { tm, best, moves, wins };
    int x0 = x * 16 + 8 - (w / 2 - 14) * 16, x1 = x * 16 + 8 + (w / 2 - 14) * 16;
    int y1 = score_card(b, s, x0, x1, (y - h / 2 + 44) * 16, 4, l, v, 24, -1);
    say(b, s, T_HINT, "Center: deal again", x, y1 + 14, HINT_INK, 1);
    if (b->best_new && t >= DROP) { /* the tag grows to 1.15 and settles */
        int k = t - DROP, sc = k >= TAG ? 256 : k < TAG * 2 / 3 ? 294 * k * 3 / (TAG * 2) : 294 - 38 * (k - TAG * 2 / 3) * 3 / TAG;
        int cx = (x + w / 2 - 45) * 16, cy = (y - h / 2) * 16, hw = 51 * sc / 16, hh = 11 * sc / 16;
        rbox(s, cx - hw, cy - hh, cx + hw, cy + hh, 176 * sc / 256, RAIL_D, 256, 0, 0);
        rbox(s, cx - hw + 24, cy - hh + 24, cx + hw - 24, cy + hh - 24, 152 * sc / 256, GOLD, 256, 0, 0);
        if (sc >= 240) say(b, s, T_SLIP, "New best", x + w / 2 - 45, y - h / 2, PEN, 1);
    }
}

/* A column, from its first card: each drawn down to where the next covers it; the run under the
 * cursor raised in one gold edge; cards in the hand or in flight left out. */
static void column(const struct sol *b, struct eng_surface *s, int k) {
    int p = P_TAB + k, len = b->g.tlen[k], x = colx[k], run = -1, held = len;
    if (s->x1 <= x - 6 || s->x0 >= x + CW + 10) return;
    if (b->hand && b->hfrom == p) held = len - b->hn;
    if (b->mode == PLAY && !b->hand && b->cur < b->nsrc && b->src[b->cur][0] == p) run = len - b->src[b->cur][1];
    if (!len || held == 0) slot(b, s, x, TAB_Y, A_SLOT_K);
    for (int i = 0; i < held; ++i) {
        int cx, cy, ny = 1 << 20;
        if (in_flight(b, p, i)) continue;
        place_xy(b, p, i, &cx, &cy);
        if (i + 1 < held && !in_flight(b, p, i + 1)) {
            int nx;
            place_xy(b, p, i + 1, &nx, &ny);
            ny += 5;
        }
        int c = b->g.tab[k][i];
        if (b->flip_t && b->flip_p == p && b->flip_i == i) { /* turning: the back narrows, the face widens */
            int e = FLIP - b->flip_t, w = e <= 3 ? CW - CW * 4 / 5 * e / 3 : CW / 5 + (CW - CW / 5) * (e - 3) / 3;
            int nx = cx + (CW - w) / 2;
            struct eng_surface sc = *s;
            if (ny < sc.y1) sc.y1 = ny;
            body(b, &sc, nx, cy, w);
            sc.x0 = nx + 1 > sc.x0 ? nx + 1 : sc.x0, sc.x1 = nx + w - 1 < sc.x1 ? nx + w - 1 : sc.x1;
            face(b, &sc, cx, cy, e <= 3 ? c & ~UP : c | UP);
            continue;
        }
        if (run >= 0 && i >= run) {
            if (i == run) { /* one gold edge round the whole run, each card raised 1 px */
                int ex, ey;
                place_xy(b, p, len - 1, &ex, &ey);
                int bot = ey + CH, hole[4] = { x, cy - 1, CW, bot - cy };
                rbox(s, R16(x + 1.5), R16(cy + 2.5), R16(x + CW + 1.5), R16(bot + 2.5), 80, SHADOW, 120 * 256 / 255, 0, hole);
                rbox(s, R16(x - 2), R16(cy - 3), R16(x + CW + 2), R16(bot + 1), 104, GOLD, 256, 0, hole);
            }
            card(b, s, cx, cy - 1, c, C_FLAT, ny - 1);
        } else
            card(b, s, cx, cy, c, C_PLAIN, ny);
    }
    if (b->hand && b->hfrom == p) { /* the place it left keeps a dark slot */
        int sx, sy;
        place_xy(b, p, held, &sx, &sy);
        rbox(s, sx * 16, sy * 16, (sx + CW) * 16, (sy + 17) * 16, 72, SHADOW, 60 * 256 / 255, 0, 0);
    }
}

static void top_row(const struct sol *b, struct eng_surface *s) {
    if (s->y0 >= TOP_Y + CH + 12) return;
    int sl = b->g.slen + (b->mode == DEAL ? 1 : 0);
    if (sl) {
        for (int d = (sl < 3 ? sl : 3) - 1; d >= 0; --d)
            if (!(d == 0 && in_flight(b, P_STOCK, b->g.slen - 1) && b->g.slen == 1))
                card(b, s, STOCK_X - d, TOP_Y - d, 0, C_PLAIN, 1 << 20);
    } else
        slot(b, s, STOCK_X, TOP_Y, A_RECYCLE);
    int len = b->g.wlen, first = b->g.draw3 ? (len > 3 ? len - 3 : 0) : (len ? len - 1 : 0);
    for (int i = first; i < len; ++i) {
        int x, y, cur = b->mode == PLAY && !b->hand && b->cur < b->nsrc && b->src[b->cur][0] == P_WASTE && i == len - 1;
        if (in_flight(b, P_WASTE, i) || (b->hand && b->hfrom == P_WASTE && i == len - 1)) continue;
        place_xy(b, P_WASTE, i, &x, &y);
        card(b, s, x, y, b->g.waste[i] | UP, cur ? C_CURSOR : C_PLAIN, 1 << 20);
    }
    for (int k = 0; k < 4; ++k) {
        int p = P_FOUND + k, n = b->g.found[k], x = colx[3 + k];
        if (b->hand && b->hfrom == p) --n;
        while (n && in_flight(b, p, n - 1)) --n;
        if (!n) {
            slot(b, s, x, TOP_Y, A_SLOT_h + k);
            continue;
        }
        int look = b->mode == WON ? (k < b->kings ? C_HOME : C_PLAIN)
                   : b->flash_t && b->flash_p == p ? C_HOME
                   : b->mode == PLAY && !b->hand && b->cur < b->nsrc && b->src[b->cur][0] == p ? C_CURSOR
                                                                                              : C_PLAIN;
        card(b, s, x, TOP_Y, (k * 13 + n - 1) | UP, look, 1 << 20);
    }
}

static void sparks(const struct sol *b, struct eng_surface *s) {
    if (!b->spark_t) return;
    int x, y, e = ease(SPARK - b->spark_t + 1, SPARK); /* out 34 px across, 40 down, eased */
    place_xy(b, b->spark_p, 0, &x, &y);
    for (int i = 0; i < 8; ++i) {
        int a = i * 128 + 49; /* 0.785 rad apart from 0.3 */
        disc(s, (x * 16 + CW * 8) + 34 * 16 * (eng_cos(a) >> 8) * e / 65536,
             (y * 16 + CH * 8) + 40 * 16 * (eng_sin(a) >> 8) * e / 65536, R16(1.6), GOLD_L);
    }
}

static void hand(const struct sol *b, struct eng_surface *s) {
    if (!b->hand) return;
    for (int k = 0; k < b->ndst; ++k) {
        int x, y, p = b->dst[k];
        place_xy(b, p, plen(b, p), &x, &y);
        ghost(s, x, y, k == b->hover);
    }
    int x, y;
    hand_xy(b, &x, &y);
    int len = plen(b, b->hfrom), u = b->hfrom >= P_TAB ? b->fan_u[b->hfrom - P_TAB] : 0;
    for (int i = 0; i < b->hn; ++i)
        card(b, s, x + 5, y + 14 + i * u, pcard(b, b->hfrom, len - b->hn + i) | UP, C_LIFT,
             i + 1 < b->hn ? y + 14 - 6 + (i + 1) * u + 5 : 1 << 20);
}

static void flights(const struct sol *b, struct eng_surface *s) {
    for (int k = 0; k < b->nfl; ++k) {
        const struct flight *f = &b->fl[k];
        int x, y;
        if (f->wait) continue;
        flight_xy(f, &x, &y);
        rbox(s, R16(x + 4), R16(y + 7), R16(x + CW + 4), R16(y + CH + 7), 80, SHADOW, 100 * 256 / 255, 0, 0);
        card(b, s, x, y, f->flags & F_DOWN ? f->card & 63 : f->card | UP, C_FLAT, 1 << 20);
    }
}

static void draw(const void *state, struct eng_surface *s) {
    const struct sol *b = state;
    char msg[32];
    felt(b, s, s->x0, s->y0, s->x1 - s->x0, s->y1 - s->y0);
    if (b->mode == START) {
        rail(b, s, "Play: deal", 0, 0);
        start_board(b, s);
        return;
    }
    const char *m = 0;
    if (b->mode == WON)
        *words(digits(words(msg, "Won in "), b->g.moves), " moves") = 0, m = msg;
    else if (b->mode == AUTO)
        m = "Finishing...";
    else if (b->msg == MSG_STUCK)
        m = "No moves. Prev: undo";
    else if (b->msg == MSG_UNDO)
        *words(digits(words(msg, "Undo: "), b->g.moves), b->g.moves == 1 ? " move" : " moves") = 0, m = msg;
    rail(b, s, m, b->g.moves, b->g.time_ms);
    top_row(b, s);
    if (b->mode != WON)
        for (int k = 0; k < 7; ++k) column(b, s, k);
    sparks(b, s);
    flights(b, s);
    hand(b, s);
    if (b->mode == WON) win_board(b, s);
}

/* ---- opening, saving ---- */

/* A save is a game only if every card is in it once, the columns' face-down cards are under their
 * face-up ones, and the lengths and the record are sane. */
static int sane(const struct record *g) {
    unsigned char seen[52] = { 0 };
    if (g->slen > 24 || g->wlen > 24 || g->draw3 > 1 || g->opt_draw3 > 1 || g->opt_random > 1 || g->live > 1 ||
        g->wins > g->played || g->time_ms > 360000000u || g->best_ms > 360000000u)
        return 0;
    if (!g->live) return 1;
    for (int k = 0; k < 4; ++k) {
        if (g->found[k] > 13) return 0;
        for (int r = 0; r < g->found[k]; ++r) seen[k * 13 + r]++;
    }
    for (int k = 0; k < 7; ++k) {
        if (g->tlen[k] > NTAB) return 0;
        for (int i = 0, up = 0; i < g->tlen[k]; ++i) {
            int c = g->tab[k][i];
            if ((c & ~(UP | 63)) || (c & 63) >= 52 || (up && !(c & UP))) return 0;
            up |= c & UP, seen[c & 63]++;
        }
        if (g->tlen[k] && !(g->tab[k][g->tlen[k] - 1] & UP)) return 0;
    }
    for (int i = 0; i < g->slen + g->wlen; ++i) {
        int c = i < g->slen ? g->stock[i] : g->waste[i - g->slen];
        if (c >= 52) return 0;
        seen[c]++;
    }
    for (int i = 0; i < 52; ++i)
        if (seen[i] != 1) return 0;
    return 1;
}

/* The art file read whole and checked, the felt's tables made from it; without it the game draws
 * plainer (a flat felt, Inter for the type). */
static void load_art(struct sol *b) {
    b->art = blob_io(ART_PATH, ART_PATH, b->file, ART_BYTES, 0) && games_ok(b->file, ART_BYTES, ART_MAGIC);
    if (!b->art) return;
    const unsigned char *t = bytes_at(b, ART_TILE), *map = bytes_at(b, ART_SHADE);
    for (int i = 0; i < 64 * 64; ++i) b->tile[i] = t[i >> 1] >> ((i & 1) << 2) & 15;
    for (int i = 0; i < ART_SHADE_W * ART_SHADE_H; ++i)
        if (map[i] < ART_LUT_LO || map[i] >= ART_LUT_LO + ART_LUT_LEVELS) b->art = 0;
    unsigned char *m = (unsigned char *)b->file + ART_SHADE; /* levels from 0 */
    for (int i = 0; b->art && i < ART_SHADE_W * ART_SHADE_H; ++i) m[i] = (unsigned char)(m[i] - ART_LUT_LO);
    const unsigned char *lut = bytes_at(b, ART_LUT);
    for (int i = 0; i < ART_LUT_LEVELS * 15; ++i)
        b->felt_lut[i] = 0xff000000 | lut[4 * i] | lut[4 * i + 1] << 8 | lut[4 * i + 2] << 16;
}

/* A card's corner: how much of each of its 5 x 5 pixels the edge's box (4.5 px round) and the face's
 * (inset 0.9, 3.8 round) cover, as rbox measures it. */
static void corners(struct sol *b) {
    for (int j = 0; j < 5; ++j)
        for (int i = 0; i < 5; ++i) {
            int cov[2];
            for (int f = 0; f < 2; ++f) {
                int in = f ? R16(0.9) : 0, r = f ? R16(3.8) : 72, cx = i * 16 + 8, cy = j * 16 + 8;
                int qx = in + r - cx, qy = in + r - cy, d;
                if (qx <= 0 && qy <= 0)
                    d = (qx > qy ? qx : qy) - r;
                else if (qx <= 0)
                    d = qy - r;
                else if (qy <= 0)
                    d = qx - r;
                else
                    d = (int)eng_sqrt((unsigned long long)(qx * qx + qy * qy)) - r;
                int c = 8 - d;
                cov[f] = c <= 0 ? 0 : c >= 16 ? 256 : c * 16;
            }
            b->a_o[j][i] = (unsigned char)(cov[0] > 255 ? 255 : cov[0]);
            b->a_f[j][i] = (unsigned char)(cov[1] > 255 ? 255 : cov[1]);
        }
}

static void init(void *state, int h) {
    struct sol *b = state;
    b->h = h, b->rail = h - RAIL_H, b->limit = h - FAN_ROOM;
    corners(b);
    load_art(b);
    if (!eng_load("solitaire", &b->g, sizeof b->g, S_MAGIC) || !sane(&b->g)) {
        memset(&b->g, 0, sizeof b->g);
        deal(b, 1, 0);
    }
    if (!b->g.live) deal(b, b->g.seed ? b->g.seed : 1, b->g.draw3);
    b->mode = START, b->sel = 0;
    refan(b);
    eng_dirty_all();
}

/* Before the menu, the screen going off or leaving: the game saved when it changed; the clock stops
 * until play goes on. */
static void pause(void *state) {
    struct sol *b = state;
    b->last_ms = 0;
    settle(b);
    if (b->mode == DEAL) finish_deal(b);
    if (b->unsaved && eng_save("solitaire", &b->g, sizeof b->g, S_MAGIC)) b->unsaved = 0, b->since_save = 0;
}

const struct eng_game eng_solitaire = { "Cabin Solitaire", sizeof(struct sol), ENG_STILL | ENG_FULL,
                                        init, step, draw, pause };
