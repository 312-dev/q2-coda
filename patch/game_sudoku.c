/* Sudoku (docs/games-engine.md#sudoku), "The Evening Puzzle": a 9 x 9 grid made on the device for
 * one of three levels, on a newspaper's puzzle page over the whole screen, entered with the wheel
 * through a dial of digits on a sticky note. The generator fills a grid by randomized backtracking,
 * then empties it in symmetric pairs while the level's test still finds one solution, a slice of
 * work a step so no frame is late. */
#include "engine.h"
#include "game_sudoku_art.inc"

unsigned time_now_ms(void); /* stock's millisecond clock: the puzzle's time between inputs */

#define S_MAGIC 0x314b4453 /* "SDK1" */
#define S_WORK                                                                                     \
    7200 /* the generator's work a step (gen_run): at most 20 MIPS instructions a unit             \
          */
/* the work of each piece, in units of about the same cost on MIPS (test/patch.py) */
#define W_LOAD 200 /* a grid loaded into the solver */
#define W_MOVE 6   /* a digit tried, or taken back */
#define W_DRAW 40  /* and the random pick of a filling grid's digit */
#define W_PASS 160 /* a pass of naked singles */
#define W_UNIT 20  /* a unit's hidden singles */
#define S_TRIES 9  /* grids a level's last test may try before one is kept */
#define S_LATE                                                                                     \
    (135 * S_WORK) /* and no fresh grid after this much work: the last is kept, in 3 s             \
                    */
#define S_HOLD 45  /* steps Play is held on the board to open the levels */
#define S_SEQ 6    /* steps between the sounds of a run */
#define S_WAVE 3   /* steps a cell of a finished unit's highlighter wave */
#define S_TIME_MAX (99u * 3600 * ENG_HZ) /* elapsed and bests stop at 99 hours */
#define S_REST_MS 300000u /* the most one rest between two steps adds to the puzzle's time */

/* The look (design/games/sudoku/STYLE.md), all of it here and in the draw functions below: the
 * geometry the steps also use (the grid, the dial's note, the level cards, for changed rects and a
 * finger's place), then the colours (0xRRGGBB). */
#define GX 39 /* the grid's left and top rules, its cells 33 px */
#define GY 12
#define CS 33
#define NOTE 61  /* half the dial's sticky note */
#define DR 41    /* the dial's digits from its centre */
#define CARD 102 /* a level card's pitch, the first at x 38 */
static const struct style {
    unsigned paper, grain, fleck, ink, soft, rule, pen, pencil, red, cursor, peer, twin, wave;
    unsigned note, gum, fold, shadow, ruled, strike, track, strip, coffee, blot, blot_in;
} sty = { 0xefe5cd, 0xdacdb0, 0xf8f0de, 0x1e1b18, 0x5a5248, 0x5d584e, 0x1a3fb0, 0x6e685c,
          0xc8202c, 0xffe14d, 0xfbf0b4, 0xcfe0ff, 0xb8f0a0, 0xffe873, 0xf7dc5a, 0xe8c93a,
          0x3a2e10, 0xa9a08c, 0x8e8778, 0xb9ae98, 0xf8f0de, 0x8a5a2a, 0x2e5a48, 0x3a6e58 };

enum { GENTLE, STEADY, SHARP, LEVELS };
enum { CHOOSE, MAKE, PLAY, DONE };
enum { G_FILL, G_PICK, G_TEST, G_FINAL, G_DONE };
static const unsigned char target[LEVELS] = { 36, 30, 24 }; /* the clues a level stops at */
/* each digit's place on the dial from its centre, clockwise from the top, DR out */
static const signed char dial_x[9] = { 0, 26, 40, 36, 14, -14, -36, -40, -26 },
                         dial_y[9] = { -41, -31, -7, 21, 39, 39, 21, -7, -31 };

/* A grid being searched or tested: its digits, the digits each unit holds, and the search's
 * stack, so a search stops when its work runs out and goes on from there. */
struct solver {
    unsigned char g[81], at[81];       /* at: the cell placed at each depth */
    unsigned short used[27], left[81]; /* left: its digits not yet tried */
    int depth, down, found, want, n;   /* down: the next move picks a cell; n: cells filled */
};

/* What is saved: the puzzle in progress and the best time of each level. */
struct save {
    unsigned magic;
    unsigned char clue[81], sol[81], ent[81]; /* 0, or a digit; a clue cell has no entry */
    unsigned char level, done, cur, slips;    /* slips: digits placed that clashed, at most 255 */
    unsigned short note[81];                  /* bit d - 1: d noted */
    unsigned elapsed, best[LEVELS];           /* steps; a best of 0 is none */
    unsigned sum;
};

struct sudoku {
    struct save v;
    int mode, sel, dial, pencil, pick, sx, sy, hold, last, dirty, record, held;
    int seq, runs, wait;
    int timed;         /* at holds the last step's time: the puzzle's time runs from there */
    unsigned at, owed; /* time_now_ms at the last step of play; the part of a step, 1/60000 s */
    int wash, wcur;    /* the peer wash shown, round wcur: a step behind the cursor (look) */
    int wave, wt;      /* a finished unit's wave: the unit and one, its steps */
    unsigned key[81];  /* each cell as last painted (look) */
    unsigned long long side;                          /* the margin as last painted (margin_key) */
    struct solver sv;                                 /* the generator's */
    unsigned rng;                                     /* its xorshift */
    int glevel, gphase, gpair, gclues, gtries, gdeep; /* gdeep: Sharp's test past its singles */
    unsigned gwork;                                   /* the work this puzzle has taken */
    unsigned char order[41], puz[81], gsol[81];
    unsigned char cells[27][9],
        of[81][3]; /* the units (rows, columns, boxes): their cells; a cell's */
};

static int bit(int d) { return d ? 1 << (d - 1) : 0; }
static int low(unsigned m) { return __builtin_ctz(m) + 1; } /* the lowest digit of a mask */
static int bits(unsigned m) {                               /* the digits in a mask */
    int n = 0;
    for (; m; m &= m - 1) ++n;
    return n;
}

static unsigned rng(unsigned *x) {
    *x ^= *x << 13, *x ^= *x >> 17, *x ^= *x << 5;
    return *x;
}

/* ---- the solver ---- */

static void sv_flip(struct sudoku *s, int i, int d) {
    for (int u = 0; u < 3; ++u) s->sv.used[s->of[i][u]] ^= (unsigned short)bit(d);
}
static void sv_put(struct sudoku *s, int i, int d) {
    s->sv.g[i] = (unsigned char)d, ++s->sv.n, sv_flip(s, i, d);
}
static void sv_take(struct sudoku *s, int i) {
    sv_flip(s, i, s->sv.g[i]), s->sv.g[i] = 0, --s->sv.n;
}
static unsigned sv_cand(const struct sudoku *s, int i) {
    const unsigned char *u = s->of[i];
    return ~(unsigned)(s->sv.used[u[0]] | s->sv.used[u[1]] | s->sv.used[u[2]]) & 0x1ff;
}
/* the solver on grid, to search for want solutions */
static void sv_load(struct sudoku *s, const unsigned char *grid, int want, int *work) {
    struct solver *v = &s->sv;
    for (int k = 0; k < 27; ++k) v->used[k] = 0;
    v->n = v->depth = v->found = 0, v->down = 1, v->want = want;
    for (int i = 0; i < 81; ++i)
        if ((v->g[i] = 0, grid[i])) sv_put(s, i, grid[i]);
    *work -= W_LOAD;
}

/* Depth first, the cell with fewest digits left next, until want solutions are found (1) or the
 * search is over (1, fewer found) or work runs out (0: call again to go on). seed set: each cell's
 * digits are tried in a random order, else lowest first. Work: a cell looked at is 1 and half a
 * unit for each of its digits counted, a move W_MOVE. */
static int sv_search(struct sudoku *s, int *work, unsigned *seed) {
    struct solver *v = &s->sv;
    while (*work > 0) {
        if (v->down) {
            int best = -1, bn = 10, i = 0, cost = 0;
            unsigned bm = 0;
            for (; i < 81 && bn > 1; ++i) {
                if (v->g[i]) continue;
                unsigned m = sv_cand(s, i), t = m;
                int n = 0;
                while (t && n < bn) t &= t - 1, ++n;
                if (n < bn) bn = n, bm = m, best = i;
                cost += n;
            }
            *work -= i + cost / 2;
            v->down = 0;
            if (best < 0) { /* full: a solution */
                if (++v->found >= v->want) return 1;
            } else if (bn) /* a dead end has nothing to push */
                v->at[v->depth] = (unsigned char)best, v->left[v->depth++] = (unsigned short)bm;
            continue;
        }
        *work -= W_MOVE;
        if (!v->depth) return 1;
        int k = v->depth - 1, i = v->at[k];
        unsigned m = v->left[k];
        if (v->g[i]) sv_take(s, i);
        if (!m) {
            --v->depth;
            continue;
        }
        if (seed) {
            *work -= W_DRAW;
            for (int j = (int)(rng(seed) % (unsigned)bits(m)); j--;) m &= m - 1;
        }
        v->left[k] &= (unsigned short)~(m & -m);
        sv_put(s, i, low(m));
        v->down = 1;
    }
    return 0;
}

/* One pass of singles: every cell with one digit left takes it and, with hidden, every digit
 * with one place left in a unit goes there. 1: solved; -1: stuck or a cell or a digit with no
 * place; 0: something placed, pass again. Work W_PASS, and W_UNIT a unit with hidden. */
static int sv_pass(struct sudoku *s, int hidden, int *work) {
    struct solver *v = &s->sv;
    int placed = 0;
    *work -= W_PASS;
    for (int i = 0; i < 81; ++i)
        if (!v->g[i]) {
            unsigned m = sv_cand(s, i);
            if (!m) return -1;
            if (!(m & (m - 1))) sv_put(s, i, low(m)), ++placed;
        }
    for (int u = 0; hidden && v->n < 81 && u < 27; ++u) {
        unsigned once = 0, twice = 0;
        *work -= W_UNIT;
        for (int k = 0; k < 9; ++k) { /* a placed digit as its bit above the nine */
            int i = s->cells[u][k];
            unsigned m = v->g[i] ? (unsigned)bit(v->g[i]) << 9 : sv_cand(s, i);
            twice |= once & m, once |= m;
        }
        if (((once | once >> 9) & 0x1ff) != 0x1ff) return -1;
        for (unsigned h = once & ~twice & ~(once >> 9) & 0x1ff; h; h &= h - 1)
            for (int k = 0; k < 9; ++k) {
                int i = s->cells[u][k];
                if (!v->g[i] && sv_cand(s, i) & (h & -h)) {
                    sv_put(s, i, low(h)), ++placed;
                    break;
                }
            }
    }
    return v->n == 81 ? 1 : placed ? 0 : -1;
}

/* ---- the generator ---- */

static void gen_fill(struct sudoku *s) {
    static const unsigned char none[81];
    int work = 0;
    sv_load(s, none, 1, &work);
    s->gphase = G_FILL, s->gpair = 0;
}

/* The generator's next work, at most work of it: a full grid, then its pairs (i, 80 - i) emptied
 * in a random order while the level's test holds, until the level's clues are reached or no pair
 * is left; then the level's last test, which may ask for a fresh grid (S_TRIES, S_LATE). The work
 * left, under 0 by what the last piece overran; the puzzle is in puz and its solution in gsol at
 * G_DONE. */
static int gen_run(struct sudoku *s, int work) {
    struct solver *v = &s->sv;
    int lv = s->glevel, given = work;
    while (work > 0 && s->gphase != G_DONE) {
        int a = s->order[s->gpair < 41 ? s->gpair : 0], r;
        if (s->gphase == G_FILL) {
            if (!sv_search(s, &work, &s->rng)) break;
            for (int i = 0; i < 81; ++i) s->gsol[i] = s->puz[i] = v->g[i];
            for (int k = 0; k < 41; ++k) { /* 0-40 shuffled as they go in */
                int j = (int)(rng(&s->rng) % (unsigned)(k + 1));
                s->order[k] = s->order[j], s->order[j] = (unsigned char)k;
            }
            s->gclues = 81, s->gphase = G_PICK;
        } else if (s->gphase == G_PICK) {
            if (s->gpair == 41 || s->gclues <= target[lv]) {
                sv_load(s, s->puz, 1, &work);
                s->gphase = G_FINAL;
                continue;
            }
            s->puz[a] = s->puz[80 - a] = 0;
            s->gclues -= a == 40 ? 1 : 2;
            sv_load(s, s->puz, 2, &work);
            s->gphase = G_TEST, s->gdeep = 0;
        } else if (s->gphase == G_TEST) {
            /* Gentle: naked singles solve it; Steady: singles. Sharp: singles first, which keep its
             * solutions as they are, then the count to two from where they stopped. */
            if (s->gdeep)
                r = sv_search(s, &work, 0);
            else if ((r = sv_pass(s, lv != GENTLE, &work)) < 0 && lv == SHARP)
                r = 0, s->gdeep = 1;
            else if (r > 0 && lv == SHARP)
                v->found = 1;
            if (!r) continue;
            if (lv == SHARP ? v->found != 1 : r < 0) { /* not this pair: put it back */
                s->puz[a] = s->gsol[a], s->puz[80 - a] = s->gsol[80 - a];
                s->gclues += a == 40 ? 1 : 2;
            }
            ++s->gpair;
            s->gphase = G_PICK;
        } else if (lv == GENTLE)
            s->gphase = G_DONE;
        else if ((r = sv_pass(s, lv == SHARP,
                              &work))) { /* Steady must need hidden singles, Sharp more */
            s->gphase = G_DONE;
            if (r > 0 && ++s->gtries < S_TRIES && s->gwork + (unsigned)(given - work) < S_LATE)
                gen_fill(s);
        }
    }
    s->gwork += (unsigned)(given - work);
    return work;
}

/* ---- the board ---- */

static int digit(const struct sudoku *s, int i) {
    return s->v.clue[i] ? s->v.clue[i] : s->v.ent[i];
}

/* The digits (bit d - 1) in cell i's row, column and box but i, the clues' alone with clues. */
static unsigned seen(const struct sudoku *s, int i, int clues) {
    unsigned m = 0;
    for (int u = 0; u < 3; ++u)
        for (int k = 0; k < 9; ++k) {
            int j = s->cells[s->of[i][u]][k];
            if (j != i) m |= (unsigned)bit(clues ? s->v.clue[j] : digit(s, j));
        }
    return m;
}
static int clash(const struct sudoku *s, int i) {
    return (unsigned)bit(digit(s, i)) & seen(s, i, 0);
}
static int peer(const struct sudoku *s, int i, int j) { /* i itself too */
    return s->of[i][0] == s->of[j][0] || s->of[i][1] == s->of[j][1] || s->of[i][2] == s->of[j][2];
}

/* A cell's washes: the cursor's row, column and box (peer), the cells holding the cursor's digit
 * (twin), the cursor, a finished unit's wave. */
enum { B_PEER = 1, B_TWIN = 2, B_CUR = 4, B_WAVE = 8 };
enum { K_CLUE = 1, K_ENTRY, K_CLASH };
/* How cell i looks: its washes (B_*) in bits 0-3, its digit's kind (K_*) in 4-5, the digit in 6-9,
 * in 10 a clue an entry clashes with, its notes in 11-19 when it has no digit. The peers and twins
 * are of wcur while the wash shows, a step behind the cursor (step). */
static unsigned look(const struct sudoku *s, int i) {
    int d = digit(s, i), w = s->wcur, bg = 0;
    if (s->mode == PLAY) {
        if (s->wash)
            bg = (peer(s, i, w) ? B_PEER : 0) | (i != w && d && d == digit(s, w) ? B_TWIN : 0);
        bg |= i == s->v.cur ? B_CUR : 0;
    }
    for (int k = 0; s->wave && k < 9; ++k)
        if (s->cells[s->wave - 1][k] == i && s->wt >= k * S_WAVE) bg |= B_WAVE;
    int kind = !d ? 0 : s->v.clue[i] ? K_CLUE : clash(s, i) ? K_CLASH : K_ENTRY;
    return (unsigned)(bg | kind << 4 | d << 6 | (kind == K_CLUE && clash(s, i)) << 10 |
                      (d ? 0 : s->v.note[i] << 11));
}

/* Each cell's look again; with mark, a row's changed cells marked as one rect, and the cursor's
 * frame, 2 px over a cell's edge, with them. */
static void refresh(struct sudoku *s, int mark) {
    for (int r = 0; r < 9; ++r) {
        int lo = 9, hi = -1;
        for (int c = 0; c < 9; ++c) {
            unsigned k = look(s, r * 9 + c);
            if (k != s->key[r * 9 + c]) s->key[r * 9 + c] = k, lo = lo < c ? lo : c, hi = c;
        }
        if (hi >= 0 && mark)
            eng_dirty(GX + lo * CS - 2, GY + r * CS - 2, (hi - lo + 1) * CS + 5, CS + 5);
    }
}

/* The digits struck in the margin's tally (bit d - 1): those the board holds nine times, clashes
 * aside. */
static unsigned tally(const struct sudoku *s) {
    unsigned char n[10] = { 0 };
    unsigned m = 0;
    for (int i = 0; i < 81; ++i) n[(look(s, i) >> 4 & 3) == K_CLASH ? 0 : digit(s, i)]++;
    for (int d = 1; d <= 9; ++d) m |= n[d] >= 9 ? (unsigned)bit(d) : 0;
    return m;
}
/* What the right margin shows: the seconds, the tally, the slips; repainted when it changes. */
static unsigned long long margin_key(const struct sudoku *s) {
    return s->v.elapsed / ENG_HZ | (unsigned long long)tally(s) << 32 |
           (unsigned long long)s->v.slips << 41;
}
static void margin(struct sudoku *s) {
    unsigned long long k = margin_key(s);
    if (k != s->side) s->side = k, eng_dirty(337, 60, ENG_W - 337, 210);
}

static void dial_dirty(const struct sudoku *s) {
    eng_dirty(s->sx - NOTE - 1, s->sy - NOTE - 1, 2 * NOTE + 6, 2 * NOTE + 7);
}
static void pick_dirty(const struct sudoku *s, int d) { /* the hub, and d's place */
    int x = s->sx, y = s->sy + 3;
    eng_dirty(x - 17, y - 17, 35, 42);
    if (d) eng_dirty(x + dial_x[d - 1] - 14, y + dial_y[d - 1] - 14, 29, 29);
}

/* A run of n of one sound, S_SEQ steps apart: the first now. */
static void sound(struct sudoku *s, int kind, int n) {
    eng_sfx(kind);
    s->seq = kind, s->runs = n - 1, s->wait = S_SEQ;
}

/* a puzzle on the board and not yet solved (a solution's first cell is never 0) */
static int started(const struct sudoku *s) { return s->v.sol[0] && !s->v.done; }

static void to(struct sudoku *s, int mode) {
    s->mode = mode, s->dial = 0, s->hold = 0, s->wave = 0, s->wash = 1, s->wcur = s->v.cur;
    eng_dirty_all();
    refresh(s, 0);
    s->side = margin_key(s);
}

/* The dial opened for the cursor's cell, on its sticky note in the 4 x 4 block of cells that
 * shares neither the cursor's row nor its column; the cell's entry picked, or the last digit
 * placed, or the first the clues leave. */
static void open_dial(struct sudoku *s) {
    unsigned ok = ~seen(s, s->v.cur, 1) & 0x1ff;
    int d = s->v.ent[s->v.cur] ? s->v.ent[s->v.cur] : s->last;
    s->sx = s->v.cur % 9 >= 4 ? 106 : 269, s->sy = s->v.cur / 9 >= 4 ? 78 : 242;
    s->dial = 1;
    s->pick = (unsigned)bit(d) & ok ? d : ok ? low(ok) : 0;
    dial_dirty(s);
}
static void shut(struct sudoku *s) { s->dial = 0, dial_dirty(s); }

static void finished(struct sudoku *s) {
    unsigned *best = &s->v.best[s->v.level];
    s->record = !*best || s->v.elapsed < *best;
    if (s->record) *best = s->v.elapsed;
    s->v.done = 1;
    to(s, DONE);
    sound(s, ENG_SFX_TAP, 3);
    /* the puzzle's end, not a frame of entry: the time reaches the card now */
    s->dirty = !eng_save("sudoku", &s->v, sizeof s->v, S_MAGIC);
}

/* d into the cursor's cell, the dial shut, d gone from the notes of its row, column and box: a tap,
 * the alert and a slip for a clash, three taps to finish; a unit it fills (nine digits, none
 * twice: never checked against the solution) takes the wave. */
static void place(struct sudoku *s, int d) {
    int i = s->v.cur, k = 0;
    shut(s);
    if (!d || s->v.ent[i] == d) return;
    s->v.ent[i] = (unsigned char)d, s->last = d;
    for (int j = 0; j < 81; ++j)
        if (peer(s, i, j)) s->v.note[j] &= (unsigned short)~bit(d);
    while (k < 81 && digit(s, k) == s->v.sol[k]) ++k;
    if (k == 81) {
        finished(s);
        return;
    }
    if (clash(s, i)) {
        s->v.slips += s->v.slips < 255;
        sound(s, ENG_SFX_ALERT, 1);
        return;
    }
    sound(s, ENG_SFX_TAP, 1);
    for (int u = 0; u < 3 && !s->wave; ++u) {
        unsigned m = 0;
        for (k = 0; k < 9; ++k) m |= (unsigned)bit(digit(s, s->cells[s->of[i][u]][k]));
        if (m == 0x1ff) s->wave = s->of[i][u] + 1, s->wt = 0;
    }
}

/* Centre on the dial: the picked digit placed, or in pencil noted or un-noted, the dial open. */
static void enter(struct sudoku *s) {
    if (!s->pencil) {
        place(s, s->pick);
    } else if (s->pick) {
        s->v.note[s->v.cur] ^= (unsigned short)bit(s->pick);
        pick_dirty(s, s->pick);
        eng_sfx(ENG_SFX_TICK);
    }
}

static void play(struct sudoku *s, const struct eng_input *in) {
    int i = s->v.cur;
    if (s->dial) {
        unsigned ok = ~seen(s, i, 1) & 0x1ff;
        int pick = s->pick, dx = in->tx - s->sx, dy = in->ty - s->sy - 3, hit = 0;
        for (int n = in->ticks < 0 ? -in->ticks : in->ticks; n-- && ok;) /* the next it can take */
            do pick = (pick + (in->ticks < 0 ? 7 : 0)) % 9 + 1;
            while (!(bit(pick) & ok));
        if (pick != s->pick) pick_dirty(s, s->pick), pick_dirty(s, pick), s->pick = pick;
        for (int d = 1; d <= 9; ++d) { /* the dial's digit under the finger */
            int x = dx - dial_x[d - 1], y = dy - dial_y[d - 1];
            if (x * x + y * y <= 14 * 14) hit = d;
        }
        if (in->pressed & ENG_TOUCH) {
            if (bit(hit) & ok)
                pick_dirty(s, s->pick), s->pick = hit, enter(s);
            else if (in->tx < s->sx - NOTE || in->tx >= s->sx + NOTE || in->ty < s->sy - NOTE ||
                     in->ty >= s->sy + NOTE) /* off the note */
                shut(s);
        } else if (in->pressed & ENG_CENTRE)
            enter(s);
        else if (in->pressed & (ENG_PREV | ENG_NEXT)) /* shut, nothing placed */
            shut(s);
        else if (in->pressed & ENG_PLAY) /* ink or pencil */
            s->pencil ^= 1, dial_dirty(s);
        return;
    }
    if (in->fast) i = ((i + in->fast) % 81 + 81) % 81;
    if (in->pressed & (ENG_PREV | ENG_NEXT)) { /* a box along, its place in the box kept */
        int b = (s->of[i][2] - 18 + (in->pressed & ENG_NEXT ? 1 : 8)) % 9;
        i = s->cells[18 + b][i / 9 % 3 * 3 + i % 3];
    }
    int c = (in->tx - GX) / CS, r = (in->ty - GY) / CS, tap = in->pressed & ENG_TOUCH;
    tap = tap && in->tx >= GX && in->ty >= GY && c < 9 && r < 9; /* on a cell */
    if (tap) i = r * 9 + c;
    s->v.cur = (unsigned char)i;
    if (!s->v.clue[i] && (in->pressed & ENG_CENTRE || tap)) open_dial(s);
    if (in->pressed & ENG_PLAY) s->hold = 1;
    if (s->hold && in->held & ENG_PLAY && !(in->released & ENG_PLAY) && ++s->hold >= S_HOLD)
        to(s, CHOOSE); /* held: the levels, the puzzle kept */
    else if (s->hold &&
             in->released & ENG_PLAY) { /* a short press: the entry, else the notes, cleared */
        s->hold = 0;
        if (s->v.ent[i])
            s->v.ent[i] = 0;
        else
            s->v.note[i] = 0;
    }
}

static int step(void *state, const struct eng_input *in) {
    struct sudoku *s = state;
    int lift =
        s->held; /* back from a pause: the blotter lifted, the whole sheet (after the rest) */
    s->held = 0;
    if (s->runs && !--s->wait) eng_sfx(s->seq), s->wait = S_SEQ, --s->runs;
    s->timed &= s->mode == PLAY; /* the levels' and the generator's time is not the puzzle's */
    if (s->mode == PLAY) {
        /* The puzzle's time: the clock's, from step to step, as the engine runs no steps at rest.
         * A rest counts S_REST_MS at most, as the screen may have gone off in it (ponytail: the
         * engine tells a game nothing of a dark rest; docs/games-engine.md#evening-puzzle). */
        unsigned now = time_now_ms(), gap = s->timed ? now - s->at : 0, t;
        s->at = now, s->timed = 1;
        s->owed += (gap > S_REST_MS ? S_REST_MS : gap) * ENG_HZ;
        t = s->v.elapsed + s->owed / 1000, s->owed %= 1000;
        s->v.elapsed = t > S_TIME_MAX ? S_TIME_MAX : t;
        s->dirty = 1;
        play(s, in);
        if (s->mode == PLAY) {
            if (s->wave && ++s->wt > 9 * S_WAVE + 12) s->wave = 0;
            /* the old peers' wash off on the cursor's move, the new one's on the next step: no
             * frame repaints both (docs/games-engine.md#sudoku) */
            if (s->wash && s->wcur != s->v.cur)
                s->wash = 0;
            else if (!s->wash)
                s->wash = 1, s->wcur = s->v.cur;
            refresh(s, 1);
            margin(s);
        }
    } else if (s->mode == CHOOSE) {
        int sel = s->sel + in->ticks, tap = in->pressed & ENG_TOUCH && in->ty >= 64 &&
                                            in->ty < 220 && in->tx >= 34 && in->tx < 34 + 3 * CARD;
        sel = tap ? (in->tx - 34) / CARD : sel < 0 ? 0 : sel > SHARP ? SHARP : sel;
        if (sel != s->sel) eng_dirty(30, 62, 316, 158), s->sel = sel;
        if (in->pressed & ENG_CENTRE || tap) { /* a puzzle of that level, made in slices from now */
            s->mode = MAKE, s->glevel = sel, s->gtries = 0, s->gwork = 0;
            s->rng = rand_below(~0u) | 1;
            gen_fill(s);
            eng_dirty_all();
        } else if (in->pressed & ENG_PLAY && started(s))
            to(s, PLAY);
    } else if (s->mode == MAKE) {
        gen_run(s, S_WORK);
        eng_dirty(29, 68, 6, 184); /* the progress rule */
        if (s->gphase == G_DONE) { /* the new puzzle in place of the last */
            for (int i = 0; i < 81; ++i)
                s->v.clue[i] = s->puz[i], s->v.sol[i] = s->gsol[i], s->v.ent[i] = 0,
                s->v.note[i] = 0;
            s->v.level = (unsigned char)s->glevel, s->v.done = 0, s->v.cur = 40, s->v.elapsed = 0;
            s->v.slips = 0, s->dirty = 1, s->last = 0;
            to(s, PLAY);
        }
    } else if (in->pressed & (ENG_CENTRE | ENG_TOUCH)) {
        s->sel = s->v.level;
        to(s, CHOOSE);
    }
    if (lift) eng_dirty_all();
    return s->mode == MAKE || s->runs || s->hold || (s->mode == PLAY && (!s->wash || s->wave));
}

/* A saved puzzle whose solution is a whole grid and whose clues, entries and notes fit it. */
static int sane(const struct sudoku *s) {
    const struct save *v = &s->v;
    int bad = v->level >= LEVELS || v->done > 1 || v->cur > 80 || v->elapsed > S_TIME_MAX;
    for (int k = 0; k < LEVELS; ++k) bad |= v->best[k] > S_TIME_MAX;
    for (int u = 0; u < 27; ++u) {
        unsigned all = 0;
        for (int k = 0; k < 9; ++k) all |= 1u << v->sol[s->cells[u][k]];
        bad |= all != 0x3fe;
    }
    for (int i = 0; i < 81; ++i)
        bad |= (v->clue[i] && (v->clue[i] != v->sol[i] || v->ent[i])) || v->ent[i] > 9 ||
               v->note[i] > 0x1ff;
    return !bad;
}

static void init(void *state, int h) {
    struct sudoku *s = state;
    (void)h; /* the sheet is the whole screen (ENG_FULL) */
    for (int i = 0; i < 81; ++i) {
        int r = i / 9, c = i % 9, b = r / 3 * 3 + c / 3;
        s->of[i][0] = (unsigned char)r, s->of[i][1] = (unsigned char)(9 + c),
        s->of[i][2] = (unsigned char)(18 + b);
        s->cells[r][c] = s->cells[9 + c][r] = s->cells[18 + b][r % 3 * 3 + c % 3] =
            (unsigned char)i;
    }
    if (eng_load("sudoku", &s->v, sizeof s->v, S_MAGIC) && !sane(s)) /* a save that no game made */
        for (unsigned k = 0; k < sizeof s->v; ++k) ((unsigned char *)&s->v)[k] = 0;
    s->sel = s->v.level;
    to(s, started(s) ? PLAY : CHOOSE);
}

/* ---- drawing ---- */

/* The engine's eng_fill, eng_disc and eng_round, and these of the page's own: a box blended, a
 * packed mask, a ring and a stroke, each clipped to the rows and columns being painted. */
static inline unsigned mix(unsigned dst, unsigned rgb, unsigned a) { /* a of 256 */
    unsigned rb = ((rgb & 0xff00ff) * a + (dst & 0xff00ff) * (256 - a)) >> 8 & 0xff00ff;
    unsigned g = ((rgb & 0xff00) * a + (dst & 0xff00) * (256 - a)) >> 8 & 0xff00;
    return 0xff000000 | rb | g;
}
static int cut(const struct eng_surface *sf, int x, int y, int w, int h, int *c) {
    c[0] = x < sf->x0 ? sf->x0 : x, c[1] = y < sf->y0 ? sf->y0 : y;
    c[2] = x + w > sf->x1 ? sf->x1 : x + w, c[3] = y + h > sf->y1 ? sf->y1 : y + h;
    return c[2] > c[0] && c[3] > c[1];
}
static inline unsigned *at(const struct eng_surface *sf, int x, int y) {
    return sf->px + (y + sf->oy) * sf->stride + x + sf->ox;
}
/* eng_fill, four pixels a turn of the loop: on MIPS one a turn costs 6 instructions a pixel, four
 * about 2, and the paper under every paint is most of a frame's pixels. */
static void fill(struct eng_surface *sf, int x, int y, int w, int h, unsigned rgb) {
    int c[4];
    if (!cut(sf, x, y, w, h, c)) return;
    rgb |= 0xff000000;
    for (int j = c[1]; j < c[3]; ++j) {
        unsigned *p = at(sf, c[0], j), *end = p + (c[2] - c[0]);
        for (; end - p >= 4; p += 4) p[0] = p[1] = p[2] = p[3] = rgb;
        while (p < end) *p++ = rgb;
    }
}
/* a box of rgb at alpha of 256 over what is there */
static void shade(struct eng_surface *sf, int x, int y, int w, int h, unsigned rgb, unsigned a) {
    int c[4];
    if (!cut(sf, x, y, w, h, c)) return;
    for (int j = c[1]; j < c[3]; ++j)
        for (unsigned *p = at(sf, c[0], j), *end = p + (c[2] - c[0]); p < end; ++p)
            *p = mix(*p, rgb, a);
}
/* A packed mask (tools/game_art.py): its width and height, then nibbles, left to right and top to
 * bottom across rows: 1-14 one pixel of that alpha of 15; 0 and 15 a run, the next nibble its
 * length less one, of clear or solid pixels. An edge pixel is blended where it lands; a run is
 * taken a row's part at a time, clipped once, so clear ones cost nothing a pixel. */
static void stamp(struct eng_surface *sf, int x, int y, const unsigned char *m, unsigned rgb) {
    int w = m[0], h = m[1], c[4], u = 0, v = 0, k = 4;
    if (!cut(sf, x, y, w, h, c)) return;
    unsigned *line = at(sf, x, y); /* the mask's row v, at its x */
    m += 2;
    rgb |= 0xff000000;
    while (v < h && y + v < c[3]) {
        unsigned a = *m >> k & 15;
        int n = 1;
        if ((k ^= 4) == 4) ++m;
        if (a && a < 15) {
            if (y + v >= c[1] && x + u >= c[0] && x + u < c[2])
                line[u] = mix(line[u], rgb, a * 17 + (a >> 3));
            if (++u == w) u = 0, ++v, line += sf->stride;
            continue;
        }
        n += *m >> k & 15;
        if ((k ^= 4) == 4) ++m;
        while (n) {
            int span = w - u < n ? w - u : n, i0 = x + u, i1 = i0 + span;
            i0 = i0 < c[0] ? c[0] : i0, i1 = i1 > c[2] ? c[2] : i1;
            if (a && y + v >= c[1])
                for (unsigned *p = line + (i0 - x), *e = line + (i1 - x); p < e; ++p) *p = rgb;
            n -= span, u += span;
            if (u == w) u = 0, ++v, line += sf->stride;
        }
    }
}
static unsigned root(unsigned n) {
    unsigned r = 0, b = 1u << 30;
    while (b > n) b >>= 2;
    for (; b; b >>= 2)
        if (n >= r + b)
            n -= r + b, r = (r >> 1) + b;
        else
            r >>= 1;
    return r;
}
/* A ring round cx, cy, radius r and its line w wide, all in 1/16 px, at alpha of 256: in each row
 * being painted, the pixels within reach of the line alone, smoothed by their distance from it. */
static void ring(struct eng_surface *sf, int cx, int cy, int r, int w, unsigned rgb,
                 unsigned alpha) {
    int out = r + w / 2 + 8, in = r - w / 2 - 8, c[4];
    if (r <= 0 || !cut(sf, (cx - out) >> 4, (cy - out) >> 4, (out >> 3) + 2, (out >> 3) + 2, c))
        return;
    for (int j = c[1]; j < c[3]; ++j) {
        int dy = j * 16 + 8 - cy, o2 = out * out - dy * dy, i2 = in * in - dy * dy;
        if (o2 < 0) continue;
        int xo = (int)root((unsigned)o2), xi = in > 0 && i2 > 0 ? (int)root((unsigned)i2) : 0;
        int l1 = (cx - xi) >> 4, r0 = (cx + xi) >> 4, i = (cx - xo) >> 4, end = (cx + xo) >> 4;
        unsigned *p = at(sf, 0, j);
        r0 = r0 <= l1 ? l1 + 1 : r0;
        i = i < c[0] ? c[0] : i, end = end >= c[2] ? c[2] - 1 : end;
        for (; i <= end; ++i) {
            if (i > l1 && i < r0) i = r0 < c[2] ? r0 : c[2] - 1; /* over the hole */
            int dx = i * 16 + 8 - cx, e = (dx * dx + dy * dy - r * r) / (2 * r);
            int a = (w / 2 + 8 - (e < 0 ? -e : e)) * 16;
            if (a > 0) p[i] = mix(p[i], rgb, (unsigned)(a > 256 ? 256 : a) * alpha >> 8);
        }
    }
}
/* A stroke from x0, y0 to x1, y1, w wide, in 1/16 px: walked a pixel at a time along its longer
 * axis, smoothed across it; no caps. */
static void line(struct eng_surface *sf, int x0, int y0, int x1, int y1, int w, unsigned rgb) {
    int ax = x1 - x0, ay = y1 - y0, steep = (ay < 0 ? -ay : ay) > (ax < 0 ? -ax : ax), t;
    if (steep) t = x0, x0 = y0, y0 = t, t = x1, x1 = y1, y1 = t;
    if (x1 < x0) t = x0, x0 = x1, x1 = t, t = y0, y0 = y1, y1 = t;
    int dx = x1 - x0, dy = y1 - y0;
    int half = dx ? w * (int)root((unsigned)(dx * dx + dy * dy)) / dx / 2 : w / 2;
    for (int i = x0 >> 4; i <= x1 >> 4; ++i) {
        int c = y0 + (dx ? (i * 16 + 8 - x0) * dy / dx : 0);
        for (int j = (c - half - 8) >> 4; j <= (c + half + 8) >> 4; ++j) {
            int d = j * 16 + 8 - c, a = (half + 8 - (d < 0 ? -d : d)) * 16, px = steep ? j : i,
                py = steep ? i : j;
            if (a > 0 && px >= sf->x0 && px < sf->x1 && py >= sf->y0 && py < sf->y1) {
                unsigned *q = at(sf, px, py);
                *q = mix(*q, rgb, (unsigned)(a > 256 ? 256 : a));
            }
        }
    }
}

static const char *const names[LEVELS] = { "Gentle", "Steady", "Sharp" };
static const char *const heads[LEVELS] = { "GENTLE", "STEADY", "SHARP" };

static char *word(char *out, const char *w) {
    while ((*out = *w++)) ++out;
    return out;
}
/* n in digits, as many as there are */
static char *num(char *out, unsigned n) {
    char d[12], *p = d + sizeof d;
    *--p = 0;
    do *--p = (char)('0' + n % 10);
    while (n /= 10);
    return word(out, p);
}
/* steps as m:ss, the minutes as many as there are */
static char *clock(char *out, unsigned steps) {
    unsigned t = steps / ENG_HZ;
    out = num(out, t / 60);
    *out++ = ':', *out++ = (char)('0' + t / 10 % 6);
    return num(out, t % 10);
}

static const unsigned char *glyph(int id) { return art + art_at[id]; }
/* glyph id with its pen at x on the baseline y (nothing drawn without sf); its advance */
static int put(struct eng_surface *sf, int id, int x, int y, unsigned rgb) {
    const unsigned char *a = glyph(id);
    if (sf) stamp(sf, x + (signed char)a[0], y + (signed char)a[1], a + 3, rgb);
    return a[2];
}
/* glyph id's ink centred on x2, y2, in half pixels */
static void mid(struct eng_surface *sf, int id, int x2, int y2, unsigned rgb) {
    const unsigned char *a = glyph(id) + 3;
    stamp(sf, (x2 - a[0]) >> 1, (y2 - a[1]) >> 1, a, rgb);
}
/* t in one of the art's sets (its first glyph and its characters; a space 4 px) on the baseline y:
 * from x (0), centred on it (1) or ending at it (2); its width */
static int say(struct eng_surface *sf, const char *t, int set, const char *chars, int x, int y,
               unsigned rgb, int align) {
    int w = 0;
    if (align) x -= say(0, t, set, chars, 0, 0, 0, 0) / (align == 1 ? 2 : 1);
    for (; *t; ++t) {
        int k = 0;
        while (chars[k] && chars[k] != *t) ++k;
        w += chars[k] ? put(sf, set + k, x + w, y, rgb) : 4;
    }
    return w;
}
static int hand(struct eng_surface *sf, const char *t, int x, int y, unsigned rgb, int align) {
    return say(sf, t, A_HAND, A_HAND_SET, x, y, rgb, align);
}
static int caps(struct eng_surface *sf, const char *t, int x, int y, unsigned rgb, int align) {
    return say(sf, t, A_CAPS, A_CAPS_SET, x, y, rgb, align);
}

/* A box with round corners: four small eng_rounds for the corners, three fills for the rest (only
 * the corners' squares painted twice), as eng_round's corner test on every pixel of a big box costs
 * more than the frame allows. */
static void rbox(struct eng_surface *sf, int x, int y, int w, int h, int r, unsigned rgb) {
    for (int k = 0; k < 4; ++k)
        eng_round(sf, k & 1 ? x + w - 2 * r : x, k & 2 ? y + h - 2 * r : y, 2 * r, 2 * r, r, rgb);
    fill(sf, x + r, y, w - 2 * r, r, rgb), fill(sf, x + r, y + h - r, w - 2 * r, r, rgb);
    fill(sf, x, y + r, w, h - 2 * r, rgb);
}
/* a box's 2 px pen frame */
static void frame(struct eng_surface *sf, int x, int y, int w, int h, unsigned rgb) {
    fill(sf, x, y, w, 2, rgb), fill(sf, x, y + h - 2, w, 2, rgb);
    fill(sf, x, y + 2, 2, h - 4, rgb), fill(sf, x + w - 2, y + 2, 2, h - 4, rgb);
}
/* A sticky note w x h at x, y: its shadow 3 px right and 4 down, its gum strip, with fold its
 * corner turned up. */
static void sticky(struct eng_surface *sf, int x, int y, int w, int h, int fold) {
    shade(sf, x + w, y + 4, 3, h, sty.shadow, 70);
    shade(sf, x + 3, y + h, w - 3, 4, sty.shadow, 70);
    rbox(sf, x, y, w, h, 2, sty.note);
    fill(sf, x + 1, y, w - 2, 9, sty.gum);
    for (int k = 0; fold && k < 12; ++k)
        fill(sf, x + w - 1 - k, y + h - 12 + k, k + 1, 1, sty.fold);
}

/* The newsprint: its colour and a fixed scatter of 1 px flecks, a few a row, from the row alone,
 * so a part repaints as it was. */
static void paper(struct eng_surface *sf) {
    fill(sf, sf->x0, sf->y0, sf->x1 - sf->x0, sf->y1 - sf->y0, sty.paper);
    for (int j = sf->y0; j < sf->y1; ++j) {
        unsigned h = (unsigned)j * 0x9e3779b1u;
        h = (h ^ h >> 15) * 0x85ebca6bu, h ^= h >> 13;
        for (int k = 0; k < 4; ++k, h >>= 8)
            if ((int)(h & 511) >= sf->x0 && (int)(h & 511) < sf->x1)
                fill(sf, (int)(h & 511), j, 1, 1, k ? sty.grain : sty.fleck);
    }
}

static void cell(const struct sudoku *s, struct eng_surface *sf, int i) {
    unsigned k = s->key[i];
    int x = GX + (s->of[i][1] - 9) * CS, y = GY + s->of[i][0] * CS, d = k >> 6 & 15,
        kind = k >> 4 & 3;
    int x2 = 2 * x + CS, y2 = 2 * y + CS; /* its centre in half pixels */
    if (x >= sf->x1 || y >= sf->y1 || x + CS <= sf->x0 || y + CS <= sf->y0) return;
    if (k & (B_WAVE | B_PEER)) fill(sf, x, y, CS, CS, k & B_WAVE ? sty.wave : sty.peer);
    if (k & B_TWIN) rbox(sf, x + 2, y + 2, CS - 3, CS - 3, 3, sty.twin);
    if (k & B_CUR) fill(sf, x + 1, y + 1, CS - 1, CS - 1, sty.cursor);
    if (kind == K_CLUE)
        mid(sf, A_PRINT + d - 1, x2, y2 + 1, sty.ink);
    else if (kind) /* the hand wanders half a pixel */
        mid(sf, A_PEN + d, x2 + i * 7 % 3 - 1, y2 + i * 5 % 3 - 1,
            kind == K_CLASH ? sty.red : sty.pen);
    if (kind == K_CLASH) ring(sf, x2 * 8, y2 * 8, 222, 19, sty.red, 256);
    if (k >> 10 & 1) /* the clue a clash is with: underlined twice */
        line(sf, x * 16 + 120, y * 16 + 464, x * 16 + 408, y * 16 + 448, 26, sty.red),
            line(sf, x * 16 + 136, y * 16 + 498, x * 16 + 392, y * 16 + 486, 16, sty.red);
    for (int n = 0; n < 9; ++n) /* the notes, each digit in its ninth */
        if (k >> (11 + n) & 1)
            mid(sf, A_HAND + n + 1, 2 * x + 22 * (n % 3) + 12, 2 * y + 22 * (n / 3) + 12,
                sty.pencil);
}

/* The cells the paint reaches (a cell's box reaches 2 px past it, its digit not at all), less those
 * the open dial's note hides; then the rules and the cursor's box. */
static void board(const struct sudoku *s, struct eng_surface *sf) {
    int c0 = (sf->x0 - GX) / CS, c1 = (sf->x1 - 1 - GX) / CS, r0 = (sf->y0 - GY) / CS,
        r1 = (sf->y1 - 1 - GY) / CS;
    c0 = c0 < 0 ? 0 : c0, r0 = r0 < 0 ? 0 : r0, c1 = c1 > 8 ? 8 : c1, r1 = r1 > 8 ? 8 : r1;
    for (int r = r0; s->mode != MAKE && r <= r1; ++r)
        for (int c = c0; c <= c1; ++c) {
            int x = GX + c * CS, y = GY + r * CS;
            if (s->dial && s->mode == PLAY && x >= s->sx - NOTE && x + CS <= s->sx + NOTE &&
                y >= s->sy - NOTE && y + CS <= s->sy + NOTE)
                continue;
            cell(s, sf, r * 9 + c);
        }
    for (int k = 1; k < 9; ++k) /* the cells' rules, then every third the boxes', 2 px */
        if (k % 3)
            fill(sf, GX + k * CS, GY, 1, 9 * CS, sty.rule),
                fill(sf, GX, GY + k * CS, 9 * CS, 1, sty.rule);
    for (int k = 0; k <= 9; k += 3)
        fill(sf, GX + k * CS - 1, GY - 1, 2, 9 * CS + 2, sty.ink),
            fill(sf, GX - 1, GY + k * CS - 1, 9 * CS + 2, 2, sty.ink);
    if (s->mode == PLAY) /* the cursor's pen box */
        frame(sf, GX + s->v.cur % 9 * CS - 1, GY + s->v.cur / 9 * CS - 1, CS + 2, CS + 2, sty.pen);
}

/* Left, the paper's running head set sideways and its rule (the generator's progress, filled from
 * the foot); right, the digits' tally, the time in pen, the level in type, the slips in red. */
static void margins(const struct sudoku *s, struct eng_surface *sf) {
    char t[24];
    if (sf->x0 >= 34 && sf->x1 <= 337) return; /* the paint is the grid's alone */
    int lv = s->mode == MAKE ? s->glevel : s->v.level, f = 180 * s->gpair / 41;
    mid(sf, A_HEAD, 38, 320, sty.ink);
    fill(sf, 31, 70, 1, 180, s->mode == MAKE ? sty.track : sty.ink);
    if (s->mode == MAKE) fill(sf, 30, 250 - f, 3, f, sty.ink);
    unsigned struck =
        s->mode == MAKE ? 0 : (unsigned)(s->side >> 32) & 0x1ff; /* tally's, at the step */
    for (int d = 1; s->mode != MAKE && d <= 9; ++d) {
        int y2 = 132 + (d - 1) * 308 / 10, out = (unsigned)bit(d) & struck;
        mid(sf, A_TALLY + d - 1, 712, y2, out ? sty.ruled : sty.soft);
        if (out) line(sf, 350 * 16, y2 * 8 + 40, 362 * 16, y2 * 8 - 40, 21, sty.pen);
    }
    clock(t, s->mode == MAKE ? 0 : s->v.elapsed);
    hand(sf, t, 356, 218, sty.pen, 1);
    line(sf, 342 * 16, 222 * 16 + 8, 370 * 16, 221 * 16 + 8, 14, sty.pen);
    caps(sf, heads[lv], 356, 235, sty.soft, 1);
    if (s->mode == PLAY && s->v.slips)
        word(num(t, s->v.slips), s->v.slips > 1 ? " slips" : " slip"),
            hand(sf, t, 356, 256, sty.red, 1);
}

static void dial(const struct sudoku *s, struct eng_surface *sf) {
    unsigned no = seen(s, s->v.cur, 1), notes = s->v.note[s->v.cur];
    int cx = s->sx, cy = s->sy + 3;
    sticky(sf, s->sx - NOTE, s->sy - NOTE, 2 * NOTE, 2 * NOTE, 1);
    ring(sf, cx * 16 + 8, cy * 16 + 8, 875, 21, sty.pen, 256); /* the pen rings, r 54.7, 27.3 */
    ring(sf, cx * 16 + 8, cy * 16 + 8, 437, 13, sty.pen, 256);
    for (int d = 1; d <= 9; ++d) {
        int x = cx + dial_x[d - 1], y = cy + dial_y[d - 1], out = (unsigned)bit(d) & no;
        if (d == s->pick)
            eng_disc(sf, FX(x) + FX(1) / 2, FX(y) + FX(1) / 2, FX(12), sty.cursor),
                ring(sf, x * 16 + 8, y * 16 + 8, 189, 24, sty.pen, 256);
        mid(sf, A_DIAL + d - 1, 2 * x + 1, 2 * y + 1,
            out                         ? sty.ruled
            : d == s->pick && s->pencil ? sty.ink
                                        : sty.pen);
        if (out) /* ruled out by the clues: struck */
            line(sf, x * 16 - 83, y * 16 + 75, x * 16 + 99, y * 16 + 1, 14, sty.strike);
        if ((unsigned)bit(d) & notes) ring(sf, x * 16 + 8, y * 16 + 8, 200, 16, sty.pencil, 256);
    }
    if (s->pick) /* the hub: the pick in pen, or in pencil over NOTE */
        mid(sf, A_PEN + s->pick, 2 * cx + 1, 2 * cy + (s->pencil ? -5 : 1),
            s->pencil ? sty.pencil : sty.pen);
    if (s->pencil) caps(sf, "NOTE", cx, cy + 21, sty.pencil, 1);
}

/* The levels: the puzzle column's head, a card a level (its name, its clues' pattern, its best),
 * the unfinished puzzle clipped under them. */
static void levels(const struct sudoku *s, struct eng_surface *sf) {
    char t[24];
    mid(sf, A_TITLE, 375, 60, sty.ink);
    fill(sf, 48, 46, 279, 1, sty.ink), fill(sf, 48, 49, 279, 1, sty.soft);
    caps(sf, "SUDOKU      A NEW PUZZLE EVERY TIME      ONE SOLUTION", 187, 60, sty.soft, 1);
    for (int k = 0; k < LEVELS; ++k) {
        int x = 38 + k * CARD, gx = x + 11;
        if (k == s->sel) /* the highlighter in a pen line: a round box in another */
            rbox(sf, x - 5, 67, 105, 150, 5, sty.pen), rbox(sf, x - 3, 69, 101, 146, 3, sty.cursor);
        mid(sf, A_NAME + k, 2 * x + 94, 164, sty.ink);
        for (int q = 0; q <= 9; ++q) /* a thumbnail: the level's share of clues, symmetric */
            fill(sf, gx + 8 * q, 96, 1, 73, q % 3 ? sty.track : sty.ink),
                fill(sf, gx, 96 + 8 * q, 73, 1, q % 3 ? sty.track : sty.ink);
        for (int r = 0; r < 9; ++r)
            for (int c = 0, p; c < 9; ++c)
                if (p = r * 9 + c, p = p > 40 ? 80 - p : p, (p * 17 + k * 11) % 41 < target[k] / 2)
                    fill(sf, gx + c * 8 + 2, 98 + r * 8, 4, 4, sty.ink);
        word(num(t, target[k]), " CLUES");
        caps(sf, t, x + 47, 183, sty.soft, 1);
        if (s->v.best[k])
            clock(word(t, "best "), s->v.best[k]);
        else
            word(t, "none yet");
        hand(sf, t, x + 47, 202, sty.pen, 1);
    }
    if (started(s)) {
        const unsigned char *n = glyph(A_NAME + s->v.level) + 3;
        sticky(sf, 84, 233, 206, 40, 0);
        rbox(sf, 84, 233, 206, 40, 2, sty.strip);
        caps(sf, "UNFINISHED", 96, 249, sty.soft, 0);
        stamp(sf, 96, 261 - n[1] / 2, n, sty.ink);
        clock(t, s->v.elapsed);
        hand(sf, t, 250, 258, sty.pen, 1);
    }
    caps(sf, started(s) ? "CENTER: START      PLAY: CONTINUE THE UNFINISHED ONE" : "CENTER: START",
         187, 296, sty.soft, 1);
}

/* Solved: the red stamp over the page and the result on a note: the time, the best or "new best!",
 * the level, the way on. */
static void solved(const struct sudoku *s, struct eng_surface *sf) {
    char t[24];
    const unsigned char *a = glyph(A_STAMP) + 3;
    int x = 187 - a[0] / 2, y = 132 - a[1] / 2;
    stamp(sf, x, y, a, sty.red);
    for (int b = 0; b < 16; b += 2) { /* its frame, a thick box and a thin one */
        const short *p = art_frame + b, *q = art_frame + (b & 8) + (b + 2) % 8;
        line(sf, x * 16 + p[0], y * 16 + p[1], x * 16 + q[0], y * 16 + q[1], b < 8 ? 38 : 10,
             sty.red);
    }
    sticky(sf, 109, 198, 156, 68, 0);
    clock(t, s->v.elapsed);
    say(sf, t, A_PEN, "0123456789:", 121, 231, sty.pen, 0);
    if (s->record)
        hand(sf, "new best!", 255, 222, sty.red, 2),
            line(sf, 191 * 16, 227 * 16, 255 * 16, 225 * 16, 19, sty.red);
    else
        clock(word(t, "best "), s->v.best[s->v.level]), hand(sf, t, 255, 222, sty.pen, 2);
    hand(sf, names[s->v.level], 255, 238, sty.pencil, 2);
    caps(sf, "CENTER: A NEW PUZZLE", 187, 258, sty.ink, 1);
}

static void draw(const void *state, struct eng_surface *sf) {
    const struct sudoku *s = state;
    char t[24];
    paper(sf);
    if (s->mode == CHOOSE) {
        levels(s, sf);
        return;
    }
    ring(sf, 322 * 16, 292 * 16, 384, 51, sty.coffee, 32); /* last night's coffee, under the ink */
    board(s, sf);
    margins(s, sf);
    if (s->mode == MAKE) { /* the generator's card */
        rbox(sf, 118, 140, 139, 40, 3, sty.ink), rbox(sf, 119, 141, 137, 38, 2, sty.strip);
        caps(sf, "SETTING THE PUZZLE", 187, 156, sty.ink, 1);
        caps(sf, "ONE SOLUTION, CHECKED", 187, 171, sty.soft, 1);
    } else if (s->mode == DONE)
        solved(s, sf);
    else if (s->dial)
        dial(s, sf);
    if (s->held) { /* a pause: a desk blotter over the grid, so it is not free thinking time */
        rbox(sf, GX - 3, GY - 3, 9 * CS + 6, 9 * CS + 6, 5, sty.blot);
        rbox(sf, GX + 4, GY + 4, 9 * CS - 8, 9 * CS - 8, 3, sty.blot_in);
        word(clock(t, s->v.elapsed), " so far");
        hand(sf, t, 187, 250, sty.cursor, 1);
    }
}

/* Before a pause: the blotter down, and the puzzle in progress and the bests written when anything
 * changed since they were last. */
static void pause(void *state) {
    struct sudoku *s = state;
    s->held = s->mode == PLAY;
    s->timed = 0; /* the pause is not the puzzle's time */
    if (s->dirty && s->v.sol[0]) s->dirty = !eng_save("sudoku", &s->v, sizeof s->v, S_MAGIC);
}

const struct eng_game eng_sudoku = {
    "Evening Puzzle", sizeof(struct sudoku), ENG_STILL | ENG_FULL, init, step, draw, pause
};
