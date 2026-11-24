/* Klondike solver for Cabin Solitaire's winnable deals (patch/game_solitaire.c, docs/games-engine.md#cabin-solitaire).
 * A host program of the test tree, never the firmware's: a depth-first search with a visited set, bounded in states,
 * over the game's own deal and rules (deal, the draw and the recycle as game_solitaire.c makes them).
 *
 *     solitaire_solver DRAW STATES SEED...
 *
 * prints a line a seed: "SEED win MOVE..." (D: Play's draw or recycle; FROM-TO-N: N cards from place FROM to TO,
 * places numbered as the game's: 0 stock, 1 waste, 2-5 the foundations, 6-12 the columns) or "SEED none". It finds a
 * win for many deals, not all that have one: a deal it gives up on may still be winnable. */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define UP 0x40
#define NTAB 20
#define TABLE (1u << 22)
#define DEPTH 1500

struct st {
    unsigned char tab[7][NTAB], tlen[7], stock[24], waste[24], found[4], slen, wlen;
};
struct mv {
    unsigned char kind, from, to, n, turned;
};

static struct st s;
static int draw3;
static uint64_t *seen;
static long states, limit;
static struct mv path[DEPTH];
static int depth;

static int rank_of(int c) { return (c & 63) % 13; }
static int suit_of(int c) { return (c & 63) / 13; }
static int red(int c) { return !(suit_of(c) & 1); }

static void deal(unsigned seed) {
    unsigned char deck[52];
    unsigned x = seed ? seed : 1;
    for (int i = 0; i < 52; ++i) deck[i] = (unsigned char)i;
    for (int i = 51; i > 0; --i) {
        x ^= x << 13, x ^= x >> 17, x ^= x << 5;
        int j = (int)(x % (unsigned)(i + 1));
        unsigned char t = deck[i];
        deck[i] = deck[j], deck[j] = t;
    }
    memset(&s, 0, sizeof s);
    int n = 0;
    for (int r = 0; r < 7; ++r)
        for (int c = r; c < 7; ++c) s.tab[c][s.tlen[c]++] = (unsigned char)(deck[n++] | (r == c ? UP : 0));
    for (int i = 0; i < 24; ++i) s.stock[i] = deck[n++];
    s.slen = 24;
}

static uint64_t hash(void) {
    uint64_t h = 1469598103934665603ull;
    const unsigned char *p = (const unsigned char *)&s;
    for (int k = 0; k < 7; ++k) {
        for (int i = 0; i < s.tlen[k]; ++i) h = (h ^ s.tab[k][i]) * 1099511628211ull;
        h = (h ^ 0xff) * 1099511628211ull;
    }
    for (int i = 0; i < s.slen; ++i) h = (h ^ s.stock[i]) * 1099511628211ull;
    h = (h ^ 0xfe) * 1099511628211ull;
    for (int i = 0; i < s.wlen; ++i) h = (h ^ s.waste[i]) * 1099511628211ull;
    for (int i = 0; i < 4; ++i) h = (h ^ s.found[i]) * 1099511628211ull;
    (void)p;
    return h | 1;
}

/* 1 when the state was seen before; else it is remembered. */
static int visited(void) {
    uint64_t h = hash();
    for (unsigned i = (unsigned)h & (TABLE - 1);; i = (i + 1) & (TABLE - 1)) {
        if (seen[i] == h) return 1;
        if (!seen[i]) {
            seen[i] = h;
            return 0;
        }
    }
}

static int top(int k) { return s.tlen[k] ? s.tab[k][s.tlen[k] - 1] : -1; }
static int home_ok(int c) { return rank_of(c) == s.found[suit_of(c)]; }
static int on(int k, int c) {
    int t = top(k);
    if (t < 0) return rank_of(c) == 12;
    return (t & UP) && red(t) != red(c) && rank_of(t) == rank_of(c) + 1;
}
/* a card home that no later move could want back: the other colour's foundations are close behind */
static int safe(int c) {
    int r = rank_of(c), o1 = red(c) ? 1 : 0, o2 = o1 + 2;
    return r <= 1 || (s.found[o1] >= r && s.found[o2] >= r);
}

static int pcard(int p, int i) {
    return p == 1 ? s.waste[i] : p < 6 ? (p - 2) * 13 + i : s.tab[p - 6][i];
}
static int plen(int p) { return p == 0 ? s.slen : p == 1 ? s.wlen : p < 6 ? s.found[p - 2] : s.tlen[p - 6]; }

static void apply(struct mv *m) {
    if (m->kind == 0) {
        if (s.slen) {
            int n = draw3 ? 3 : 1;
            m->n = (unsigned char)(n < s.slen ? n : s.slen);
            for (int i = 0; i < m->n; ++i) s.waste[s.wlen++] = s.stock[--s.slen];
            m->turned = 0;
        } else {
            m->n = s.wlen, m->turned = 1; /* a recycle */
            for (int i = 0; i < m->n; ++i) s.stock[i] = s.waste[m->n - 1 - i];
            s.slen = m->n, s.wlen = 0;
        }
        return;
    }
    unsigned char run[NTAB];
    int n = m->n;
    for (int i = n; i--;) {
        int p = m->from;
        run[i] = (unsigned char)pcard(p, plen(p) - 1);
        if (p == 1) --s.wlen;
        else if (p < 6) --s.found[p - 2];
        else --s.tlen[p - 6];
    }
    for (int i = 0; i < n; ++i) {
        if (m->to < 6) ++s.found[m->to - 2];
        else s.tab[m->to - 6][s.tlen[m->to - 6]++] = (unsigned char)(run[i] | UP);
    }
    int k = m->from - 6;
    m->turned = k >= 0 && s.tlen[k] && !(s.tab[k][s.tlen[k] - 1] & UP);
    if (m->turned) s.tab[k][s.tlen[k] - 1] |= UP;
}

static void unapply(const struct mv *m) {
    if (m->kind == 0) {
        if (!m->turned)
            for (int i = 0; i < m->n; ++i) s.stock[s.slen++] = s.waste[--s.wlen];
        else {
            for (int i = 0; i < m->n; ++i) s.waste[i] = s.stock[m->n - 1 - i];
            s.wlen = m->n, s.slen = 0;
        }
        return;
    }
    int k = m->from - 6;
    if (m->turned) s.tab[k][s.tlen[k] - 1] &= (unsigned char)~UP;
    unsigned char run[NTAB];
    int n = m->n;
    for (int i = n; i--;) {
        int p = m->to;
        run[i] = (unsigned char)pcard(p, plen(p) - 1);
        if (p < 6) --s.found[p - 2];
        else --s.tlen[p - 6];
    }
    for (int i = 0; i < n; ++i) {
        int p = m->from;
        if (p == 1) s.waste[s.wlen++] = run[i] & 63;
        else if (p < 6) ++s.found[p - 2];
        else s.tab[p - 6][s.tlen[p - 6]++] = run[i];
    }
}

static int moves(struct mv *out) {
    int n = 0;
    /* a safe card home is the only move worth making */
    for (int p = 1; p < 13; ++p) {
        if (p >= 2 && p < 6) continue;
        int len = plen(p);
        if (!len) continue;
        int c = pcard(p, len - 1);
        if (home_ok(c) && safe(c)) {
            out[0] = (struct mv){ 1, (unsigned char)p, (unsigned char)(2 + suit_of(c)), 1, 0 };
            return 1;
        }
    }
    for (int p = 1; p < 13; ++p) { /* home */
        if (p >= 2 && p < 6) continue;
        int len = plen(p);
        if (len && home_ok(pcard(p, len - 1)))
            out[n++] = (struct mv){ 1, (unsigned char)p, (unsigned char)(2 + suit_of(pcard(p, len - 1))), 1, 0 };
    }
    for (int k = 0; k < 7; ++k) { /* a column's whole face-up run elsewhere, when it turns a card or frees a column */
        int len = s.tlen[k], first = len;
        while (first > 0 && (s.tab[k][first - 1] & UP)) --first;
        if (first == len) continue;
        for (int j = first; j < len; ++j) {
            int c = s.tab[k][j], whole = j == first;
            if (!whole) { /* part of a run: only to free the card under it for home */
                int under = s.tab[k][j - 1];
                if (!home_ok(under)) continue;
            }
            for (int d = 0; d < 7; ++d) {
                if (d == k || !on(d, c)) continue;
                if (!s.tlen[d] && whole && first == 0) continue; /* a king already alone */
                out[n++] = (struct mv){ 1, (unsigned char)(6 + k), (unsigned char)(6 + d), (unsigned char)(len - j), 0 };
                if (!s.tlen[d]) break; /* one empty column is as good as another */
            }
        }
    }
    if (s.wlen) { /* the waste's top to a column */
        int c = s.waste[s.wlen - 1];
        for (int d = 0; d < 7; ++d)
            if (on(d, c)) {
                out[n++] = (struct mv){ 1, 1, (unsigned char)(6 + d), 1, 0 };
                if (!s.tlen[d]) break;
            }
    }
    for (int f = 0; f < 4; ++f) /* a card back from home, to take one from the waste or a column */
        if (s.found[f] > 2) {
            int c = f * 13 + s.found[f] - 1;
            for (int d = 0; d < 7; ++d)
                if (s.tlen[d] && on(d, c)) out[n++] = (struct mv){ 1, (unsigned char)(2 + f), (unsigned char)(6 + d), 1, 0 };
        }
    if (s.slen || s.wlen) out[n++] = (struct mv){ 0, 0, 1, 0, 0 };
    return n;
}

static int dfs(void) {
    if (s.found[0] + s.found[1] + s.found[2] + s.found[3] == 52) return 1;
    if (depth >= DEPTH || ++states > limit || visited()) return 0;
    struct mv m[64];
    int n = moves(m);
    for (int i = 0; i < n; ++i) {
        apply(&m[i]);
        path[depth++] = m[i];
        if (dfs()) return 1;
        --depth;
        unapply(&m[i]);
    }
    return 0;
}

int main(int argc, char **argv) {
    if (argc < 4) {
        fprintf(stderr, "usage: %s DRAW STATES SEED...\n", argv[0]);
        return 2;
    }
    draw3 = atoi(argv[1]) == 3;
    limit = atol(argv[2]);
    seen = calloc(TABLE, sizeof *seen);
    for (int a = 3; a < argc; ++a) {
        unsigned seed = (unsigned)strtoul(argv[a], 0, 0);
        memset(seen, 0, TABLE * sizeof *seen);
        deal(seed);
        states = 0, depth = 0;
        if (!dfs()) {
            printf("%u none\n", seed);
            continue;
        }
        printf("%u win", seed);
        for (int i = 0; i < depth; ++i)
            if (path[i].kind == 0)
                printf(" D");
            else
                printf(" %d-%d-%d", path[i].from, path[i].to, path[i].n);
        printf("\n");
        fflush(stdout);
    }
    return 0;
}
