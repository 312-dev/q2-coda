/* Caller Ten (docs/games-engine.md#caller-ten): a radio call-in contest on the user's own library.
 * A table radio plays a clip of a song, a rotary phone is the control (the wheel dials 1 to 4) and
 * a notepad holds the four answers; ten callers a contest, three wrong ends it.
 *
 * The art is a file, read whole at init (CT_ART, made by the design's src/export.py): one palette,
 * row run-length coded pictures cut from the mockups' own renders, each at its place on the screen,
 * and glyph masks of the two display faces. The library's titles and artists go through stock's
 * canvas in the theme's face and its fallbacks, so any script the firmware draws draws here.
 *
 * Playback is the game's while a contest runs: the Library's song is held where it was (now_hold),
 * each clip goes straight to q2player over demo's player socket (never the queue, so no play count,
 * scrobble, resume place or car title moves), the clock starts when the player answers that it is
 * heard, and when the game is left the Library's song is given back as it was, playing or paused
 * (ct_watch). Songs are picked on a worker thread (pick_worker), SQL never on the UI thread. */
#include "engine.h"
#include "navigation.h"
#include "ui_kit.h"
#include "game_callerten_art.h"

#define CT_ART PEQ_ROOT "/usr/share/q2games/callerten.bin"
#define CT_ROUNDS 10
#define CT_SPARES 3 /* answers checked beyond the ten, for a song that cannot play */
#define CT_ANS (CT_ROUNDS + CT_SPARES)
#define CT_CAND 64   /* songs the worker keeps: the answers and every round's decoys */
#define CT_MIN 12    /* songs a contest needs (BRIEFS: fewer shows the honest message) */
#define CT_TITLE 128 /* bytes of a title kept, cut at a character */
#define CT_ARTIST 80
#define CT_PATH 512
#define CT_MIN_SEC 40 /* a clip starts 30 to 60 percent in and plays about 13 s at most */
#define CT_CLOCK 600  /* the round's clock: 100 points falling to 10 over 10 s */
#define CT_CALL 20    /* steps the clip plays on after the call is placed */
#define CT_REVEAL 60  /* steps the answer shows before the next caller */
#define CT_ASK 6      /* steps between asks of the player's state while a clip is cued */
#define CT_CUE_MAX 300 /* steps a clip may take to be heard before its song is passed over */
#define CT_MAGIC 0x314e5443 /* "CTN1" */
#define CT_WATCH_MS 100     /* how soon after the game is left its music is given back */

enum { S_BROKEN, S_TUNE, S_STANDBY, S_NOSIGNAL, S_ROUND, S_END };
enum { P_CUE, P_AIR, P_CALL, P_REVEAL };
enum { M_TITLES, M_ARTISTS, M_DEEP };
enum { WHY_OK, WHY_SONGS, WHY_ARTISTS, WHY_PLAYABLE, WHY_LIBRARY };

/* the pad (radio.py's NX0.. and ROW0/ROWH), the summary's sheet, the radio's glass and lamps */
#define NX0 152
#define NY0 122
#define NX1 348
#define NY1 294
#define ROW_Y(i) (161 + 30 * (i))
#define TUNE_Y(i) (166 + 40 * (i))
#define SX0 40
#define SY0 122
#define SX1 335
#define SUM_Y(i) (233 + 26 * (i))
#define SUM_ROWS 3
#define TITLE_X 188 /* a row's title: from here, 152 px at most */
#define TITLE_W 152
#define LAMP_X4(i) (624 + (i) * 344 / 5) /* 156 + 17.2 i, in 1/4 px */
#define STATION_X4(k) (702 + (k) * 237)  /* the needle on 90.1, 98.1, 106.1: 160 + (f - 88) 7.4 */

/* What the worker reads, kept for the game: the candidates (the answers first), and the ten
 * rounds' paths. The game reads none of it before done; whichever of the game and the worker lets go
 * last frees it (refs), so a game left while the worker runs leaks nothing. */
struct song {
    unsigned id, key, style;
    int sec, year;
    char title[CT_TITLE], artist[CT_ARTIST];
};
struct pick {
    int refs, done, mode, why, n, answers;
    int played_n;
    unsigned played[PLAYS_SLOTS]; /* Deep cuts: the songs counted twice or more, sorted */
    struct song song[CT_CAND];
    char path[CT_ANS][CT_PATH];
    int there[CT_ANS]; /* the check one round ahead: 1 the file is there, -1 gone, 0 not looked */
};

/* What outlives the game's state: Games frees that at a close without a word to the game, so what
 * must be given back then (the music, the art, the worker's rows) is here, for ct_watch. */
struct keep {
    unsigned char *art;
    struct pick *pk;
    int owned, was_playing; /* the sound is the game's; the Library's song played when it was taken */
    unsigned watch;
};

struct ct {
    struct keep *k;
    int h, screen, phase, mode, sel, t, at, reduced;
    int round, rounds, wrongs, rights, streak, score, shown, pts, best_new, slot;
    int row[4], right, pick, song; /* the round's rows (song indices), the right row, the call */
    int missed[CT_ROUNDS], nmissed, top;
    int clip_sec, clip_on, clip_held, heard, ask, replay;
    int needle4, needle_from, needle_t, turn_t, lift, lift_dir, ring, ring_from, lamps, dim, dim_t;
    int cut, end_t, swell;
    int fit_px[4], fit_n[4], full_w[4], maxw[4], scroll[4], scroll_t;
    unsigned char sfx[8];
    int sfx_n, sfx_wait;
    unsigned marks; /* M_*: what this step changed, each marked once at its end (flush) */
    struct {
        unsigned magic, best[3], played, sum;
    } save;
};

/* ---- the player: clips over demo's socket, its answers from demo's reply list ---- */

/* m to q2player; never waits on it, nor dies with it (MSG_DONTWAIT | MSG_NOSIGNAL). */
static void say(const char *m) {
    int fd = *(volatile int *)MCL_FD;
    if (fd != -1) send(fd, m, strlen(m), 0x4040);
}

/* The clip: path from second sec, the song stopped before it so it starts at once (docs/player.md:
 * a stop just before a song is the playing one's). Fields as demo's own start sends them. */
static void clip_start(struct ct *g, const char *path, int sec) {
    char m[CT_PATH + 64];
    unsigned n = (unsigned)tk_snprintf(m, sizeof m, "{mcl-stop\\null}{set-songurl\\");
    for (const char *p = path; *p && n < sizeof m - 40; ++p) /* demo writes } as %0x7d */
        if (*p == '}')
            memcpy(m + n, "%0x7d", 5), n += 5;
        else
            m[n++] = *p;
    tk_snprintf(m + n, sizeof m - n, "|ss=%d|512|20|0|17|90}", sec);
    char vol[5];
    if (!output_device(vol)) dac_take(); /* the jack readied as stock's start readies it */
    char *a; /* answers about the clip before: each a malloc'd copy */
    while ((a = mcl_reply_take_by_key("player-status"))) free(a);
    while ((a = mcl_reply_take_by_key("player-stopstatus"))) free(a);
    say(m);
    g->clip_on = 1, g->clip_held = 0, g->heard = 0, g->ask = CT_ASK;
}

/* The player's answers since the last look: 1 heard, -1 the clip cannot play (an error, or the
 * song ended), 0 nothing yet. Each answer is a malloc'd copy, freed here. */
static int clip_heard(void) {
    int got = 0;
    char *a;
    while ((a = mcl_reply_take_by_key("player-status"))) {
        if (!strcmp(a, "playing") && !got)
            got = 1;
        else if (!strcmp(a, "error") || !strcmp(a, "exit"))
            got = -1;
        free(a);
    }
    while ((a = mcl_reply_take_by_key("player-stopstatus"))) free(a);
    return got;
}

/* The sound taken for a contest: Spotify, a radio and the Library's song stopped as a video stops
 * them, the Library's song held where it was (now_source.c). */
static void take(struct keep *k) {
    if (k->owned) return;
    int status = mclGetPlayStatus();
    headset_manual();
    spot_yield();
    radio_stop();
    now_hold();
    k->was_playing = status == 2;
    k->owned = 1;
}

/* The clip stopped and the Library's song given back as it was: playing on from its place if it
 * played, else loaded paused there (now_release); a Library that was stopped stays stopped. */
static void give_back(struct keep *k) {
    if (!k->owned) return;
    k->owned = 0;
    say("{mcl-stop\\null}");
    int held = now_page_status() == 3 && mclGetPlayStatus() != 3; /* held: stopped, read as paused */
    if (held && k->was_playing)
        now_take_back();
    else if (held)
        now_release();
}

/* While a game holds the sound (addon.c addon_game_audio): stock's player starts nothing, headset
 * keys reach nothing, the car remote waits. */
int callerten_sound(void) {
    const struct ct *g = eng_state_of(&eng_callerten);
    return g && g->k && g->k->owned;
}

static void keep_free(struct keep *k) {
    struct pick *p = k->pk;
    if (p && __atomic_sub_fetch(&p->refs, 1, __ATOMIC_ACQ_REL) == 0) free(p);
    free(k->art);
    free(k);
}

/* After every pause, until the game runs again: once Games has closed it (Quit, Home, the page
 * gone), the music back, the art and the rows freed. */
static int ct_watch(const void *info) {
    struct keep *k = P(info, TIMER_CTX); /* AWTK hands a timer its timer_info_t, the ctx inside */
    if (eng_state_of(&eng_callerten)) return 8; /* RET_REPEAT: still open, held */
    give_back(k);
    keep_free(k);
    return 0;
}

/* ---- the worker ---- */

static int cmp_key(const void *a, const void *b) {
    unsigned x = *(const unsigned *)a, y = *(const unsigned *)b;
    return x < y ? -1 : x > y;
}
static int played(const struct pick *p, unsigned key) {
    int lo = 0, hi = p->played_n;
    while (lo < hi) {
        int mid = (lo + hi) / 2;
        if (p->played[mid] < key)
            lo = mid + 1;
        else
            hi = mid;
    }
    return lo < p->played_n && p->played[lo] == key;
}

/* s into d (size bytes), cut at a whole UTF-8 character */
static void copy_text(char *d, const char *s, unsigned size) {
    unsigned n = strlen(s);
    if (n >= size) {
        n = size - 1;
        while (n && ((unsigned char)s[n] & 0xc0) == 0x80) --n;
    }
    memcpy(d, s, n);
    d[n] = 0;
}

static unsigned fold_hash(const char *s) {
    unsigned h = FNV_SEED;
    for (; *s; ++s) h = (h ^ (unsigned char)(*s >= 'A' && *s <= 'Z' ? *s + 32 : *s)) * 16777619u;
    return h | !h;
}

struct scan {
    struct pick *p;
    char (*paths)[CT_PATH]; /* the first 2 * CT_ANS candidates' files, for the answers' checks */
};

/* A song of the query: kept unless a server's row or, for Deep cuts, played twice or more. 1 (enough)
 * stops the query. */
static int cand_row(void *ctx, int n, char **v, char **c) {
    struct scan *sc = ctx;
    struct pick *p = sc->p;
    (void)c;
    if (n < 6 || !v[0] || !v[1] || !v[2] || !v[2][0] || catalogue_path(v[1])) return 0;
    unsigned key = fnv(FNV_SEED, (const unsigned char *)v[1]);
    key |= !key;
    if (p->mode == M_DEEP && played(p, key)) return 0;
    struct song *s = &p->song[p->n];
    s->id = (unsigned)atoi(v[0]), s->key = key, s->style = v[4] && v[4][0] ? fold_hash(v[4]) : 0;
    s->year = v[5] ? atoi(v[5]) : 0, s->sec = 0;
    copy_text(s->title, v[2], sizeof s->title);
    copy_text(s->artist, v[3] ? v[3] : "", sizeof s->artist);
    if (p->n < 2 * CT_ANS) copy_text(sc->paths[p->n], v[1], CT_PATH);
    return ++p->n >= CT_CAND;
}

/* path's length in seconds by TagLib, as durations.c measures it; 0 unknown */
static int seconds(const char *path) {
    void *f = ogg_tags(path, 0) ? 0 : taglib_file_new(path, 1);
    void *a = f ? taglib_file_audioproperties(f) : (void *)0;
    int sec = a ? ((int (*)(void *))(*(void ***)a)[3])(a) : 0; /* AudioProperties::length() */
    if (f) taglib_file_free(f);
    return sec;
}

static const char *visible(const struct pick *p, int i) {
    return p->mode == M_ARTISTS ? p->song[i].artist : p->song[i].title;
}

/* Songs in a random order (the library's music, MUSIC_WHERE, the card's table alone: a server's
 * rows are cat.servertable's), CT_CAND kept; then the answers, each a file that is there and long
 * enough, moved to the front. */
static void *pick_worker(void *arg) {
    struct pick *p = arg;
    thread_lower(0);
    struct scan sc = { p, calloc(2 * CT_ANS, CT_PATH) };
    char sql[640];
    tk_snprintf(sql, sizeof sql,
                "select id,fileurl,name,ifnull(songer,''),ifnull(style,''),ifnull(year,0) from "
                "main.songtable where %s and ifnull(time_end,0)=0 and ifnull(name,'')<>''%s order "
                "by random()",
                MUSIC_WHERE, p->mode == M_ARTISTS ? " and ifnull(songer,'')<>''" : "");
    unsigned seen[CT_CAND];
    int distinct = 0;
    if (!sc.paths || !era_sql(sql, cand_row, &sc)) p->why = WHY_LIBRARY, p->n = 0;
    for (int i = 0; i < p->n; ++i) { /* the answers' rows must differ, four at least */
        unsigned h = fold_hash(visible(p, i)), k = 0;
        while (k < (unsigned)distinct && seen[k] != h) ++k;
        if (k == (unsigned)distinct) seen[distinct++] = h;
    }
    for (int i = 0; sc.paths && i < p->n && i < 2 * CT_ANS && p->answers < CT_ANS; ++i) {
        int a = p->answers, sec = access(sc.paths[i], 4) ? 0 : seconds(sc.paths[i]); /* R_OK */
        if (sec < CT_MIN_SEC) continue;
        struct song s = p->song[i];
        p->song[i] = p->song[a], p->song[a] = s;
        copy_text(p->path[a], sc.paths[i], CT_PATH);
        if (i != a) memcpy(sc.paths[i], sc.paths[a], CT_PATH); /* the row moved to i takes its path */
        p->song[a].sec = sec;
        p->answers++;
    }
    if (p->why == WHY_OK)
        p->why = p->n < CT_MIN                 ? WHY_SONGS
                 : distinct < 4                ? p->mode == M_ARTISTS ? WHY_ARTISTS : WHY_SONGS
                 : p->answers < 1              ? WHY_PLAYABLE
                                               : WHY_OK;
    free(sc.paths);
    __atomic_store_n(&p->done, 1, __ATOMIC_RELEASE);
    if (__atomic_sub_fetch(&p->refs, 1, __ATOMIC_ACQ_REL) == 0) free(p);
    return 0;
}

/* One round ahead: whether the next answer's file is still there, so a song gone mid-game is
 * passed over before its round, never asked of the player. */
struct ahead {
    struct pick *p;
    int slot;
};
static void *ahead_worker(void *arg) {
    struct ahead a = *(struct ahead *)arg;
    free(arg);
    int there = access(a.p->path[a.slot], 4) ? -1 : 1;
    __atomic_store_n(&a.p->there[a.slot], there, __ATOMIC_RELEASE);
    if (__atomic_sub_fetch(&a.p->refs, 1, __ATOMIC_ACQ_REL) == 0) free(a.p);
    return 0;
}
static void look_ahead(struct pick *p, int slot) {
    struct ahead *a = slot < p->answers ? calloc(1, sizeof *a) : 0;
    unsigned long t = 0;
    if (!a) return;
    a->p = p, a->slot = slot;
    __atomic_add_fetch(&p->refs, 1, __ATOMIC_ACQ_REL);
    if (pthread_create(&t, 0, ahead_worker, a) || !t) {
        __atomic_sub_fetch(&p->refs, 1, __ATOMIC_ACQ_REL);
        free(a);
        return;
    }
    pthread_detach(t);
}

/* ---- drawing: the art file's pictures and faces, the library's text through stock's canvas ---- */

#define W32(b, o) (*(const unsigned *)((const unsigned char *)(b) + (o)))
#define INK 0x1E1410
#define INK_SOFT 0x4A3020
#define INK_FADED 0x8E8272
#define GLASS_INK 0x6E0F22
#define NEEDLE 0xD62C42
#define WRONG 0xD62C42
#define RIGHT_INK 0x17703A
#define ON_GOLD 0x14102E

static inline unsigned mixc(unsigned d, unsigned c, unsigned a) { /* c over d, a of 256 */
    unsigned rb = ((c & 0xff00ff) * a + (d & 0xff00ff) * (256 - a)) >> 8 & 0xff00ff;
    unsigned gg = ((c & 0xff00) * a + (d & 0xff00) * (256 - a)) >> 8 & 0xff00;
    return 0xff000000 | rb | gg;
}
static inline unsigned *row_at(const struct eng_surface *s, int y) {
    return s->px + (y + s->oy) * s->stride + s->ox;
}
static const unsigned *pic_entry(const unsigned char *art, int id) {
    return (const unsigned *)(art + W32(art, 16) + 12 * id);
}

/* Picture id at its place moved dx, dy: its rows decoded within the part being painted and outside
 * the hole (x0 y0 x1 y1 on the sheet, a box a picture drawn after it covers whole; 0 none), at a of
 * 256 over what is there, its first w columns only (w < 0: all). */
static void blit(struct eng_surface *s, const unsigned char *art, int id, int dx, int dy, unsigned a,
                 int w, const int *hole) {
    const unsigned *e = pic_entry(art, id), *pal = (const unsigned *)(art + W32(art, 8));
    int x = (short)(e[1] >> 16) + dx, y = (short)(e[1] & 0xffff) + dy, h = (int)(e[2] & 0xffff);
    int pw = (int)(e[2] >> 16), c1 = x + (w >= 0 && w < pw ? w : pw);
    int c0 = x < s->x0 ? s->x0 : x, r0 = y < s->y0 ? s->y0 : y, r1 = y + h > s->y1 ? s->y1 : y + h;
    c1 = c1 > s->x1 ? s->x1 : c1;
    if (c0 >= c1 || r0 >= r1) return;
    const unsigned char *data = art + e[0];
    for (int j = r0; j < r1; ++j) {
        const unsigned char *p = data + ((const unsigned *)data)[j - y];
        unsigned *out = row_at(s, j);
        /* the hole's columns in this row, h0 <= q < h1: none when h1 <= h0 */
        int h0 = c1, h1 = c1;
        if (hole && j >= hole[1] && j < hole[3]) h0 = hole[0], h1 = hole[2];
        for (int i = x; i < c1;) {
            unsigned n = *p++;
            int k = n < 128 ? (int)n + 1 : (int)n - 126, lo = i > c0 ? i : c0, hi = i + k < c1 ? i + k : c1;
            if (lo < hi) {
                /* a run across the hole is written in two parts, round it */
                int m0 = hi > h0 && lo < h1 ? (h0 > lo ? h0 : lo) : hi, m1 = m0 < hi ? (h1 < hi ? h1 : hi) : hi;
                if (n < 128) {
                    const unsigned char *q = p - i;
                    if (a >= 256) {
                        for (int c = lo; c < m0; ++c) out[c] = pal[q[c]];
                        for (int c = m1; c < hi; ++c) out[c] = pal[q[c]];
                    } else {
                        for (int c = lo; c < m0; ++c) out[c] = mixc(out[c], pal[q[c]], a);
                        for (int c = m1; c < hi; ++c) out[c] = mixc(out[c], pal[q[c]], a);
                    }
                } else {
                    unsigned v = pal[*p];
                    if (a >= 256) {
                        for (unsigned *o = out + lo, *end = out + m0; o < end; ++o) *o = v;
                        for (unsigned *o = out + m1, *end = out + hi; o < end; ++o) *o = v;
                    } else {
                        for (int c = lo; c < m0; ++c) out[c] = mixc(out[c], v, a);
                        for (int c = m1; c < hi; ++c) out[c] = mixc(out[c], v, a);
                    }
                }
            }
            p += n < 128 ? k : 1;
            i += k;
        }
    }
}
static void pic(struct eng_surface *s, const unsigned char *art, int id, int dx, int dy, unsigned a,
                int w) {
    blit(s, art, id, dx, dy, a, w, 0);
}

static void mark_pic(const struct ct *g, int id, int dx, int dy) {
    const unsigned *e = pic_entry(g->k->art, id);
    eng_dirty((short)(e[1] >> 16) + dx, (short)(e[1] & 0xffff) + dy, (int)(e[2] >> 16),
              (int)(e[2] & 0xffff));
}

/* A face's glyphs: its width of t, in 1/4 px; t drawn with its middle (the mockups' anchor "m") at
 * y4 and from, centred on or ending at x4 (align 0, 1, 2), all in 1/4 px. */
static int gwidth(const unsigned char *art, int face, const char *t) {
    unsigned words = W32(art, W32(art, 24) + 8 * face);
    int w = 0;
    for (; *t; ++t) {
        unsigned c = (unsigned char)*t, rec = c >= 32 && c < 128 ? W32(art, words + 4 * (c - 32)) : 0;
        if (rec) w += *(const short *)(art + rec);
    }
    return w;
}
static void gtext(struct eng_surface *s, const unsigned char *art, int face, const char *t, int x4,
                  int y4, unsigned rgb, int align) {
    unsigned ft = W32(art, 24) + 8 * face, words = W32(art, ft);
    int base = (y4 + (int)W32(art, ft + 4) + 2) >> 2;
    x4 -= gwidth(art, face, t) * align / 2;
    for (; *t; ++t) {
        unsigned c = (unsigned char)*t, rec = c >= 32 && c < 128 ? W32(art, words + 4 * (c - 32)) : 0;
        if (!rec) continue;
        const unsigned char *r = art + rec, *m = r + 6;
        int w = r[2], h = r[3], gx = ((x4 + 2) >> 2) + (signed char)r[4], gy = base + (signed char)r[5];
        int c0 = gx < s->x0 ? s->x0 : gx, c1 = gx + w > s->x1 ? s->x1 : gx + w;
        int r0 = gy < s->y0 ? s->y0 : gy, r1 = gy + h > s->y1 ? s->y1 : gy + h;
        for (int j = r0; j < r1; ++j) {
            unsigned *out = row_at(s, j);
            for (int i = c0; i < c1; ++i) {
                unsigned v = m[(j - gy) * w + i - gx];
                if (v) out[i] = v == 255 ? 0xff000000 | rgb : mixc(out[i], rgb, v + (v >> 7));
            }
        }
        x4 += *(const short *)r;
    }
}

/* Row y from l16 to r16 (1/16 px) in rgb, its two ends covered in part. */
static void span(struct eng_surface *s, int y, int l16, int r16, unsigned rgb) {
    if (y < s->y0 || y >= s->y1 || r16 <= l16) return;
    unsigned *out = row_at(s, y);
    for (int x = l16 >> 4; x <= (r16 - 1) >> 4; ++x) {
        int a = (r16 < (x + 1) * 16 ? r16 : (x + 1) * 16) - (l16 > x * 16 ? l16 : x * 16);
        if (x >= s->x0 && x < s->x1 && a > 0) out[x] = mixc(out[x], rgb, (unsigned)a * 16);
    }
}

/* The library's text: UTF-8 as code points; measured and drawn in the theme's face, or the fallback
 * that has the script (theme_face), as stock's lists draw it. */
static int wide(const char *u, unsigned *w, int max) {
    unsigned i = 0, n = strlen(u);
    int k = 0;
    while (i < n && k < max) w[k++] = utf8_char((const unsigned char *)u, n, &i);
    return k;
}
static int measure(void *canvas, const unsigned *w, int n, int px) {
    int fpx = px;
    const char *face = theme_face(w, n, &fpx);
    canvas_set_font(canvas, face, (unsigned)fpx);
    return (int)(canvas_measure_text(canvas, w, (unsigned)n) + 0.5f);
}
static int utext(struct eng_surface *s, const unsigned *w, int n, int x, int ym, int px, unsigned rgb,
                 int align) {
    if (n <= 0) return 0;
    int tw = measure(s->canvas, w, n, px);
    x -= tw * align / 2;
    int r[4] = { x - 1, ym - 12, tw + 2, 24 };
    draw_centred(s->canvas, w, (unsigned)n, r, (unsigned)px, rgba(rgb, 255));
    return tw;
}
static int atext(struct eng_surface *s, const char *a, int x, int ym, int px, unsigned rgb,
                 int align) {
    unsigned w[48];
    return utext(s, w, wide(a, w, 48), x, ym, px, rgb, align);
}

/* The canvas's clip (screen rows and columns) and the surface's, cut to x0..x1, y0..y1 of the sheet
 * for what follows; was keeps the canvas's to put back. 0: nothing of it is painted. */
static int clip_to(struct eng_surface *s, int x0, int y0, int x1, int y1, int *was) {
    canvas_get_clip_rect(s->canvas, was);
    int ax0 = x0 + s->ox, ay0 = y0 + s->oy, ax1 = x1 + s->ox, ay1 = y1 + s->oy;
    ax0 = ax0 < was[0] ? was[0] : ax0, ay0 = ay0 < was[1] ? was[1] : ay0;
    ax1 = ax1 > was[0] + was[2] ? was[0] + was[2] : ax1;
    ay1 = ay1 > was[1] + was[3] ? was[1] + was[3] : ay1;
    if (ax1 <= ax0 || ay1 <= ay0) return 0;
    int r[4] = { ax0, ay0, ax1 - ax0, ay1 - ay0 };
    canvas_set_clip_rect(s->canvas, r);
    s->x0 = s->x0 < x0 ? x0 : s->x0, s->y0 = s->y0 < y0 ? y0 : s->y0;
    s->x1 = s->x1 > x1 ? x1 : s->x1, s->y1 = s->y1 > y1 ? y1 : s->y1;
    return 1;
}

/* w[0..n) fitted to maxw (SPEC: Long titles): at px, else a px smaller down to 14, else cut at 14
 * px with "...". Returns the px; *keep is n when it fits whole, else -1 - the characters kept before
 * the "..."; *full is its whole width at the px returned (14 when cut), which a scroll runs over. */
static int fit_text(void *cv, const unsigned *w, int n, int maxw, int px, int *keep, int *full) {
    unsigned b[CT_TITLE + 3];
    for (*keep = n; px >= 14; --px)
        if ((*full = measure(cv, w, n, px)) <= maxw || px == 14) break;
    if (*full <= maxw) return px;
    int lo = 0, hi = n;
    while (lo < hi) {
        int mid = (lo + hi + 1) / 2;
        memcpy(b, w, (unsigned)mid * sizeof *b);
        b[mid] = b[mid + 1] = b[mid + 2] = '.';
        if (measure(cv, b, mid + 3, 14) <= maxw)
            lo = mid;
        else
            hi = mid - 1;
    }
    while (lo > 0 && w[lo - 1] == ' ') --lo;
    *keep = -1 - lo;
    return 14;
}

/* w as fit_text kept it: the "..." after a cut; the characters to draw */
static int kept(unsigned *w, int keep) {
    if (keep >= 0) return keep;
    keep = -1 - keep;
    w[keep] = w[keep + 1] = w[keep + 2] = '.';
    return keep + 3;
}

/* A row's title fitted from 17 px, kept in g until its text or width changes. */
static void fit(struct ct *g, void *cv, int i, const unsigned *w, int n, int maxw) {
    g->maxw[i] = maxw;
    g->fit_px[i] = fit_text(cv, w, n, maxw, 17, &g->fit_n[i], &g->full_w[i]);
}

static const char *const STATION[] = { "TITLES", "ARTISTS", "DEEP CUTS" };

static const char *song_text(const struct ct *g, int song) {
    return visible(g->k->pk, song);
}

static int answered(const struct ct *g) { return g->screen == S_END || (g->screen == S_ROUND && g->phase == P_REVEAL); }

/* The glass's one line. */
static const char *glass_words(const struct ct *g, char *b) {
    switch (g->screen) {
    case S_TUNE: return STATION[g->sel];
    case S_STANDBY: return STATION[g->mode];
    case S_NOSIGNAL: return "NO SIGNAL";
    case S_END: return g->wrongs >= 3 ? "LINES ARE CLOSED" : "THAT'S THE SHOW";
    case S_ROUND:
        if (g->phase != P_REVEAL) return g->mode == M_ARTISTS ? "WHO IS PLAYING?" : "NAME THE SONG";
        if (g->pick != g->right) return "NOT THIS TIME";
        if (g->streak < 3) return "YOU GOT IT!";
        tk_snprintf(b, 16, "HOT LINE x%d", g->streak);
        return b;
    }
    return "";
}

/* The clock lamp i: 0 out, else its picture and how lit, of 256. */
static int lamp(const struct ct *g, int i, unsigned *a) {
    *a = 256;
    if (g->screen == S_STANDBY) {
        int c = g->t / 5 % 10;
        return (i - c + 10) % 10 < 3 ? A_LAMP0_AMBER + 2 * i : 0;
    }
    if (g->screen != S_ROUND || !g->heard) return 0;
    int lit = g->lamps, red = lit <= 3; /* the last three burn red */
    if (g->phase == P_AIR && g->at < 10 && i > g->at) return 0; /* lighting left to right */
    if (i == g->dim && g->dim_t < 6) {
        *a = (unsigned)(256 * (6 - g->dim_t) / 6);
        return A_LAMP0_AMBER + 2 * i + red;
    }
    return i < lit ? A_LAMP0_AMBER + 2 * i + red : 0;
}

static void draw_radio(struct eng_surface *s, const struct ct *g, const unsigned char *art) {
    int air = g->screen == S_ROUND && g->heard;
    if (g->screen == S_STANDBY)
        pic(s, art, A_ONAIR_STANDBY, 0, 0, 256, -1);
    else if (air)
        pic(s, art, g->swell > 4 && g->swell < 16 ? A_ONAIR_SWELL : A_ONAIR_LIT, 0, 0, 256, -1);
    if (g->screen == S_ROUND && g->replay < 3)
        pic(s, art, g->replay ? A_REPLAY_PLAYING : A_REPLAY_READY, 0, 0, 256, -1);
    if (air && g->phase != P_REVEAL)
        pic(s, art, A_RINGS_1 + (g->reduced ? 0 : g->ring), 0, 0, 256, -1);
    for (int i = 0; i < 10; ++i) {
        unsigned a;
        int id = lamp(g, i, &a);
        if (id) pic(s, art, id, 0, 0, a, -1);
    }
    if (g->screen == S_TUNE) pic(s, art, A_DOTS_ON, 0, 0, 256, -1);
    int station = g->screen == S_TUNE ? g->sel : g->mode;
    pic(s, art, A_KNOB_0 + station, 0, 0, 256, -1);
    int x16 = g->needle4 * 4;
    for (int y = 23; y < 36; ++y) span(s, y, x16 - 19, x16 + 19, NEEDLE);
    for (int y = 31; y < 37; ++y) {
        int hw = (2 * y + 1 - 62) * 32 / 5; /* the head: 4 px either side at y 36, a point at 31 */
        span(s, y, x16 - hw, x16 + hw, NEEDLE);
    }
    char b[16];
    gtext(s, art, F_R15, glass_words(g, b), 934, 185, GLASS_INK, 1);
}

/* the handset's pose by frame: up 1..6 over the back-out, down 3..1 */
static int pose(int lift) { return lift >= 5 ? 4 : lift - 1; }
static const signed char TURN[9] = { 1, 2, 3, 3, 2, 2, 1, 0, 0 }; /* 16 degree steps: out, back */

static void draw_phone(struct eng_surface *s, const struct ct *g, const unsigned char *art) {
    if (g->lift > 0) pic(s, art, A_POSE_0 + pose(g->lift), 0, 0, 256, -1);
    int sel = g->screen == S_ROUND || g->screen == S_END ? g->sel : 1;
    pic(s, art, A_DIAL_1 + sel - 1, 0, 0, 256, -1);
    if (g->turn_t >= 0 && g->turn_t < 9 && TURN[g->turn_t])
        pic(s, art, A_TURN_11 + 3 * (sel - 1) + TURN[g->turn_t] - 1, 0, 0, 256, -1);
    if (answered(g) && g->pick >= 0)
        pic(s, art, A_HUB_1R + 2 * (sel - 1) + (g->pick != g->right), 0, 0, 256, -1);
}

static void heading(struct eng_surface *s, const unsigned char *art, const char *t, int x0) {
    gtext(s, art, F_R15, t, 4 * (x0 + 10), 534, 0xffffff, 0);
}

/* An answer row's title: fitted, or scrolled through its whole width within its line. */
static void row_text(struct eng_surface *s, struct ct *g, int i, unsigned ink) {
    unsigned w[CT_TITLE + 3];
    int n = wide(song_text(g, g->row[i]), w, CT_TITLE), y = ROW_Y(i), was[4];
    int maxw = TITLE_W - (answered(g) && i == g->right ? 20 : 0);
    if (!g->fit_px[i] || g->maxw[i] != maxw) fit(g, s->canvas, i, w, n, maxw);
    if (g->scroll[i] > 0) {
        struct eng_surface c = *s;
        if (clip_to(&c, TITLE_X, y - 14, TITLE_X + maxw, y + 15, was)) {
            utext(&c, w, n, TITLE_X - g->scroll[i], y, 14, ink, 0);
            canvas_set_clip_rect(s->canvas, was);
        }
        return;
    }
    utext(s, w, kept(w, g->fit_n[i]), TITLE_X, y, g->fit_px[i], ink, 0);
}

/* the drawn width of row i's title, as row_text draws it: what a strike runs under */
static int row_width(const struct ct *g, int i) {
    int full = g->full_w[i], maxw = g->maxw[i];
    return g->fit_n[i] < 0 || g->scroll[i] > 0 ? maxw : full < maxw ? full : maxw;
}

static void draw_round_pad(struct eng_surface *s, struct ct *g, const unsigned char *art) {
    char b[40];
    pic(s, art, A_PAD_ROUND, 0, 0, 256, -1);
    tk_snprintf(b, sizeof b, "CALLER %d OF %d", g->round + 1 > g->rounds ? g->rounds : g->round + 1,
                g->rounds);
    heading(s, art, b, NX0);
    for (int k = 0; k < 3; ++k) pic(s, art, k < g->wrongs ? A_BOX_CROSS : A_BOX_EMPTY, 17 * k, 0, 256, -1);
    int shown = answered(g);
    for (int i = 0; i < 4; ++i) {
        int dy = 30 * i, look = 0;
        unsigned ink = INK;
        if (!shown) {
            if (i == g->sel - 1) pic(s, art, A_ROW_GOLD, 0, dy, 256, -1), look = 1, ink = ON_GOLD;
        } else if (i == g->right) {
            int t = g->screen == S_END ? 60 : g->t; /* the wash in three steps over six frames */
            pic(s, art, A_ROW_WASH, 0, dy, t >= 6 ? 256 : t >= 4 ? 171 : t >= 2 ? 85 : 0, -1);
            look = 2, ink = 0x0B2E1A;
        } else
            look = i == g->pick ? 3 : 4, ink = i == g->pick ? 0x6E5048 : INK_FADED;
        pic(s, art, A_BADGE_1N + 5 * i + look, 0, dy, 256, -1);
        row_text(s, g, i, ink);
        if (shown && i == g->right) { /* the check, drawn left to right in 6 frames */
            int t = g->screen == S_END ? 6 : g->t;
            pic(s, art, A_CHECK, 0, dy, 256, t >= 6 ? -1 : t * 20 / 6);
        }
        if (shown && i == g->pick && i != g->right) { /* the strike, across in 8 frames */
            int t = g->screen == S_END ? 8 : g->t, w = (row_width(g, i) + 4) * (t > 8 ? 8 : t) / 8;
            for (int y = ROW_Y(i); y < ROW_Y(i) + 2; ++y)
                span(s, y, (TITLE_X - 2) * 16, (TITLE_X - 2 + w) * 16, WRONG);
        }
    }
    int sw = gwidth(art, F_R15, "SCORE");
    gtext(s, art, F_R15, "SCORE", 4 * (NX0 + 10), 1124, INK_SOFT, 0);
    tk_snprintf(b, sizeof b, "%d", g->shown);
    gtext(s, art, F_B21, b, 4 * (NX0 + 16) + sw, 1122, INK, 0);
    if (g->pts && shown && g->pick == g->right && g->screen == S_ROUND) {
        tk_snprintf(b, sizeof b, "+%d", g->pts);
        gtext(s, art, F_B18, b, 4 * (NX1 - 10), 1122, RIGHT_INK, 2);
    }
}

static void draw_desk(struct eng_surface *s, struct ct *g, const unsigned char *art) {
    draw_phone(s, g, art);
    char b[40];
    switch (g->screen) {
    case S_TUNE: {
        static const char *const name[] = { "Titles", "Artists", "Deep cuts" };
        static const char *const sub[] = { "Name the song", "Who is playing?", "Songs you rarely play" };
        pic(s, art, A_PAD_TUNE, 0, 0, 256, -1);
        heading(s, art, "TONIGHT'S CONTESTS", NX0);
        for (int i = 0; i < 3; ++i) {
            int y = TUNE_Y(i), on = i == g->sel;
            if (on) pic(s, art, A_TUNE_GOLD, 0, 40 * i, 256, -1);
            if (g->save.best[i])
                tk_snprintf(b, sizeof b, "Best %u", g->save.best[i]);
            else
                tk_snprintf(b, sizeof b, "No best yet");
            atext(s, name[i], NX0 + 12, y - 8, 17, on ? ON_GOLD : INK, 0);
            atext(s, b, NX1 - 12, y - 8, 14, on ? ON_GOLD : INK_SOFT, 2);
            atext(s, sub[i], NX0 + 12, y + 10, 14, on ? 0x3A2A10 : INK_SOFT, 0);
        }
        atext(s, "Center: call in", (NX0 + NX1) / 2, NY1 - 13, 14, INK_SOFT, 1);
        break;
    }
    case S_STANDBY:
        pic(s, art, A_PAD_STANDBY, 0, 0, 256, -1);
        heading(s, art, "ONE MOMENT", NX0);
        atext(s, "Choosing songs...", (NX0 + NX1) / 2, NY0 + 97, 20, INK, 1);
        break;
    case S_NOSIGNAL: {
        int why = g->k->pk ? g->k->pk->why : WHY_LIBRARY;
        heading(s, art, "NO CONTEST TONIGHT", NX0);
        atext(s, why == WHY_LIBRARY ? "The library" : "Add more music", (NX0 + NX1) / 2, NY0 + 56, 20,
              INK, 1);
        atext(s, why == WHY_LIBRARY ? "is not ready" : "to play", (NX0 + NX1) / 2, NY0 + 80, 20, INK, 1);
        atext(s,
              why == WHY_ARTISTS    ? "A contest needs 4 artists"
              : why == WHY_PLAYABLE ? "None of them can play"
              : why == WHY_LIBRARY  ? "Try again in a moment"
                                    : "A contest needs 12 songs",
              (NX0 + NX1) / 2, NY0 + 112, 14, INK_SOFT, 1);
        atext(s, "Center: quit", (NX0 + NX1) / 2, NY1 - 13, 14, INK_SOFT, 1);
        break;
    }
    default: draw_round_pad(s, g, art);
    }
}

static void draw_summary(struct eng_surface *s, struct ct *g, const unsigned char *art) {
    char b[48];
    pic(s, art, A_SUMMARY, 0, 0, 256, -1);
    if (g->wrongs >= 3)
        tk_snprintf(b, sizeof b, "THREE WRONG: ENDED AT CALLER %d", g->round);
    else
        tk_snprintf(b, sizeof b, "%s: FINAL SCORE", STATION[g->mode]);
    heading(s, art, b, SX0);
    tk_snprintf(b, sizeof b, "%d", g->shown);
    int lx = SX0 + 24 + gwidth(art, F_B36, b) / 4;
    gtext(s, art, F_B36, b, 4 * (SX0 + 12), 4 * (SY0 + 49), INK, 0);
    tk_snprintf(b, sizeof b, "%d of %d right", g->rights, g->round);
    atext(s, b, lx, SY0 + 39, 16, INK, 0);
    if (g->best_new && g->end_t >= 14)
        pic(s, art, A_TAG, lx - 130, 0, 256, -1);
    else if (!g->best_new) {
        if (g->save.best[g->mode])
            tk_snprintf(b, sizeof b, "Best %u", g->save.best[g->mode]);
        else
            tk_snprintf(b, sizeof b, "No best yet");
        atext(s, b, lx, SY0 + 59, 15, INK_SOFT, 0);
    }
    gtext(s, art, F_R14, g->nmissed ? "MISSED: CENTER PLAYS ONE" : "NONE MISSED", 4 * (SX0 + 10),
          838, INK_SOFT, 0);
    if (!g->nmissed) atext(s, "Play/Pause: a new contest", (SX0 + SX1) / 2, SUM_Y(1), 15, INK_SOFT, 1);
    for (int r = 0; r < SUM_ROWS && g->top + r < g->nmissed; ++r) {
        int m = g->top + r, y = SUM_Y(r), on = m == g->sel;
        const struct song *sg = &g->k->pk->song[g->missed[m]];
        pic(s, art, on ? A_SUM_GOLD : A_SUM_NOTE, 0, 26 * r, 256, -1);
        unsigned w[CT_TITLE + 3], a[CT_ARTIST];
        int an = wide(sg->artist, a, CT_ARTIST);
        int aw = utext(s, a, an, SX1 - 34, y, 14, on ? 0x3A2A10 : INK_SOFT, 2);
        int n = wide(sg->title, w, CT_TITLE), room = SX1 - 34 - aw - 10 - (SX0 + 30), keep, full;
        if (room > 0) {
            int px = fit_text(s->canvas, w, n, room, 16, &keep, &full);
            utext(s, w, kept(w, keep), SX0 + 30, y, px, on ? ON_GOLD : INK, 0);
        }
    }
}

static void draw(const void *state, struct eng_surface *s) {
    struct ct *g = (struct ct *)state; /* draw keeps its titles' measures (fit) in the state */
    const unsigned char *art = g->k ? g->k->art : 0;
    if (!art) {
        eng_fill(s, 0, 0, ENG_W, g->h, 0);
        return;
    }
    /* the room, but where the pad's or the summary's sheet is drawn over it whole */
    const unsigned *pad = pic_entry(art, A_PAD_ROUND);
    int hole[4] = { (short)(pad[1] >> 16), (short)(pad[1] & 0xffff), 0, 0 }, open = 0;
    hole[2] = hole[0] + (int)(pad[2] >> 16), hole[3] = hole[1] + (int)(pad[2] & 0xffff);
    if (g->screen == S_END)
        hole[0] = 0, hole[1] = g->cut, hole[2] = ENG_W, hole[3] = g->h, open = 1;
    else
        open = g->screen == S_TUNE || g->screen == S_STANDBY || g->screen == S_ROUND;
    blit(s, art, A_BG, 0, 0, 256, -1, open ? hole : 0);
    draw_radio(s, g, art);
    if (g->screen != S_END) {
        draw_desk(s, g, art);
        return;
    }
    int was[4];
    struct eng_surface up = *s, low = *s;
    if (g->cut > 0 && clip_to(&up, 0, 0, ENG_W, g->cut, was)) { /* the desk until the sheet covers it */
        draw_desk(&up, g, art);
        canvas_set_clip_rect(s->canvas, was);
    }
    if (clip_to(&low, 0, g->cut, ENG_W, g->h, was)) {
        draw_summary(&low, g, art);
        canvas_set_clip_rect(s->canvas, was);
    }
}

/* ---- the contest ---- */

static void sfx(struct ct *g, int kind) {
    if (!g->sfx_n && !g->sfx_wait) {
        eng_sfx(kind);
        g->sfx_wait = 5;
    } else if (g->sfx_n < 8)
        g->sfx[g->sfx_n++] = (unsigned char)kind;
}
static int sfx_step(struct ct *g) {
    if (g->sfx_wait && !--g->sfx_wait && g->sfx_n) {
        eng_sfx(g->sfx[0]);
        memmove(g->sfx, g->sfx + 1, (unsigned)--g->sfx_n);
        g->sfx_wait = 5;
    }
    return g->sfx_wait || g->sfx_n;
}

/* What a step can change, each a box of the sheet: flush marks each set one once, so a step never
 * passes the engine's eight rects and none is joined into a large one. */
enum {
    M_GLASS = 1, M_ONAIR = 2, M_REPLAY = 4, M_RINGS = 8, M_LAMPS = 16, M_KNOB = 32, M_POSE = 64,
    M_DIAL = 128, M_BAND = 256, M_SCORE = 512, M_ROW = 1024,          /* rows: 4 bits */
    M_TUNE = 1 << 14, M_SUMSCORE = 1 << 17, M_SUMROWS = 1 << 18       /* tune rows: 3 bits */
};
static void flush(struct ct *g) {
    unsigned m = g->marks;
    g->marks = 0;
    if (!m || !g->k || !g->k->art) return;
    if (m & M_GLASS) eng_dirty(146, 20, 176, 41);
    if (m & M_ONAIR) mark_pic(g, A_ONAIR_LIT, 0, 0);
    if (m & M_REPLAY) mark_pic(g, A_REPLAY_READY, 0, 0);
    if (m & M_RINGS) mark_pic(g, A_RINGS_1, 0, 0);
    if (m & M_LAMPS) eng_dirty(148, 89, 172, 16);
    if (m & M_KNOB) mark_pic(g, A_KNOB_0, 0, 0);
    if (m & M_POSE) mark_pic(g, A_POSE_0, 0, 0);
    if (m & M_DIAL) mark_pic(g, A_DIAL_1, 0, 0);
    if (m & M_BAND) eng_dirty(NX0, NY0, NX1 - NX0, 23);
    if (m & M_SCORE) eng_dirty(NX0, NY1 - 28, NX1 - NX0, 28);
    for (int i = 0; i < 4; ++i) /* adjacent rows as one box */
        if (m & M_ROW << i) {
            int k = i;
            while (k < 3 && m & M_ROW << (k + 1)) ++k;
            const unsigned *e = pic_entry(g->k->art, A_ROW_GOLD);
            eng_dirty((short)(e[1] >> 16), (short)(e[1] & 0xffff) + 30 * i, (int)(e[2] >> 16),
                      (int)(e[2] & 0xffff) + 30 * (k - i));
            i = k;
        }
    for (int i = 0; i < 3; ++i)
        if (m & M_TUNE << i) mark_pic(g, A_TUNE_GOLD, 0, 40 * i);
    if (m & M_SUMSCORE) eng_dirty(SX0, SY0 + 25, SX1 - SX0, 50);
    if (m & M_SUMROWS) eng_dirty(SX0, SUM_Y(0) - 13, SX1 - SX0, 26 * SUM_ROWS);
}
#define MARK(g, m) ((g)->marks |= (m))
static void mark_row(struct ct *g, int i) { MARK(g, M_ROW << i); }

static int same(const char *a, const char *b) { return fold_hash(a) == fold_hash(b); }

/* The round's four rows: its answer and three decoys whose rows read otherwise, the ones of the
 * same genre or decade first, in a random order. */
static void deal(struct ct *g) {
    const struct pick *p = g->k->pk;
    const struct song *a = &p->song[g->song];
    int chosen[3], n = 0;
    while (n < 3) {
        int best = -1, best_score = -1;
        for (int i = 0; i < p->n; ++i) {
            if (i == g->song || same(visible(p, i), visible(p, g->song))) continue;
            int dup = 0;
            for (int k = 0; k < n; ++k) dup |= i == chosen[k] || same(visible(p, i), visible(p, chosen[k]));
            if (dup) continue;
            int sc = (a->style && p->song[i].style == a->style ? 8 : 0) +
                     (a->year > 0 && p->song[i].year / 10 == a->year / 10 ? 4 : 0) + (int)rand_below(6);
            if (sc > best_score) best = i, best_score = sc;
        }
        if (best < 0) break; /* the worker saw four that read otherwise: not reached */
        chosen[n++] = best;
    }
    g->right = (int)rand_below(4);
    for (int i = 0, k = 0; i < 4; ++i) g->row[i] = i == g->right ? g->song : k < n ? chosen[k++] : g->song;
}

static void contest_end(struct ct *g);

/* The next answer that is still there, its rows dealt, its clip asked for; with none left the
 * contest ends. */
static void round_start(struct ct *g) {
    struct pick *p = g->k->pk;
    while (g->slot < p->answers && __atomic_load_n(&p->there[g->slot], __ATOMIC_ACQUIRE) < 0) ++g->slot;
    if (g->slot >= p->answers) {
        contest_end(g);
        return;
    }
    g->song = g->slot++;
    deal(g);
    look_ahead(p, g->slot);
    g->phase = P_CUE, g->t = g->at = 0, g->sel = 1, g->pick = -1, g->pts = 0, g->replay = 0;
    g->lamps = 10, g->dim = -1, g->ring = g->ring_from = 0, g->swell = 0, g->turn_t = -1, g->scroll_t = 0;
    g->lift = g->lift > 3 ? 3 : g->lift, g->lift_dir = -1; /* the handset back down */
    for (int i = 0; i < 4; ++i) g->fit_px[i] = 0, g->scroll[i] = 0;
    g->clip_sec = p->song[g->song].sec * (30 + (int)rand_below(31)) / 100;
    sfx(g, ENG_SFX_TAP); /* on air: before the clip, never over it */
    clip_start(g, p->path[g->song], g->clip_sec);
    eng_dirty_all();
}

/* A clip that cannot play: its song is passed over for the next, this caller's number kept. */
static void pass_over(struct ct *g) {
    __atomic_store_n(&g->k->pk->there[g->song], -1, __ATOMIC_RELEASE);
    round_start(g);
}

static void call(struct ct *g, int row) {
    g->pick = row;
    g->sel = row + 1;
    g->pts = 100 - 3 * (g->at < CT_CLOCK ? g->at : CT_CLOCK) / 20;
    g->phase = P_CALL, g->t = 0, g->turn_t = -1;
    g->lift = g->reduced ? 6 : 0, g->lift_dir = 1;
    sfx(g, ENG_SFX_TICK);
    MARK(g, M_POSE | M_DIAL);
    for (int i = 0; i < 4; ++i) mark_row(g, i);
}

static void answer(struct ct *g) {
    say("{mcl-pause\\null}"); /* the clip waits under the answer */
    g->phase = P_REVEAL, g->t = 0;
    if (g->pick == g->right) {
        g->rights++, g->streak++;
        g->score += g->pts;
        for (int k = 0; k < 3; ++k) sfx(g, ENG_SFX_TAP);
        if (g->streak >= 3) sfx(g, ENG_SFX_TICK), sfx(g, ENG_SFX_TICK), g->swell = 1;
    } else {
        g->wrongs++, g->streak = 0;
        g->missed[g->nmissed++] = g->song;
        sfx(g, ENG_SFX_ALERT);
    }
    for (int i = 0; i < 4; ++i) mark_row(g, i);
    MARK(g, M_GLASS | M_DIAL | M_ONAIR | M_BAND | M_SCORE); /* the hub, a strike box */
}

static void contest_end(struct ct *g) {
    say("{mcl-stop\\null}");
    g->clip_on = 0;
    g->screen = S_END, g->end_t = 0, g->cut = g->reduced ? 108 : 320, g->sel = g->top = 0;
    g->lift_dir = -1, g->lift = g->lift > 3 ? 3 : g->lift; /* the handset back on its cradle */
    g->best_new = g->score > (int)g->save.best[g->mode];
    if (g->best_new) g->save.best[g->mode] = (unsigned)g->score;
    g->save.played++;
    eng_save("callerten", &g->save, sizeof g->save, CT_MAGIC);
    g->shown = g->reduced ? g->score : 0;
    sfx(g, ENG_SFX_TAP);
    if (g->best_new)
        for (int k = 0; k < 3; ++k) sfx(g, ENG_SFX_TAP);
    eng_dirty_all();
}

/* Station mode tuned: the songs picked on the worker, STAND BY meanwhile. */
static void begin(struct ct *g) {
    struct keep *k = g->k;
    struct pick *old = k->pk, *p = calloc(1, sizeof *p);
    if (old && __atomic_sub_fetch(&old->refs, 1, __ATOMIC_ACQ_REL) == 0) free(old);
    k->pk = p;
    g->mode = g->sel, g->screen = S_STANDBY, g->t = 0;
    eng_dirty_all();
    if (!p) return;
    p->refs = 2, p->mode = g->mode;
    if (g->mode == M_DEEP) { /* the play counts are the UI thread's: a sorted copy for the worker */
        plays_load();
        for (int j = 0; j < PLAYS_SLOTS; ++j)
            if (st.plays[j].key && st.plays[j].n >= 2) p->played[p->played_n++] = st.plays[j].key;
        qsort(p->played, (unsigned)p->played_n, sizeof *p->played, cmp_key);
    }
    unsigned long t = 0;
    if (pthread_create(&t, 0, pick_worker, p) || !t) {
        p->refs = 1, p->why = WHY_LIBRARY, p->done = 1;
        return;
    }
    pthread_detach(t);
}

static void contest_start(struct ct *g) {
    struct pick *p = g->k->pk;
    take(g->k);
    g->rounds = p->answers < CT_ROUNDS ? p->answers : CT_ROUNDS;
    g->round = g->slot = g->score = g->shown = g->wrongs = g->rights = g->streak = g->nmissed = 0;
    g->screen = S_ROUND;
    round_start(g);
}

/* The chosen row's title, scrolled when it was cut (SPEC: 40 steps still, 1 px every 2 steps, 40
 * still, back), and on the reveal the right one's once through. */
static int scroll_step(struct ct *g) {
    int moving = 0;
    for (int i = 0; i < 4; ++i) {
        int want = 0, ov = g->full_w[i] - g->maxw[i];
        if (g->fit_px[i] && g->fit_n[i] < 0 && ov > 0) {
            if (g->phase == P_REVEAL && i == g->right) {
                int sp = (ov + 39) / 40, t = g->t - 10;
                want = t <= 0 ? 0 : t * sp > ov ? ov : t * sp;
            } else if (g->phase != P_REVEAL && i == g->sel - 1) {
                int t = g->scroll_t % (80 + 2 * ov);
                want = t < 40 ? 0 : t < 40 + 2 * ov ? (t - 40) / 2 : ov;
                moving = 1;
            }
        }
        if (want != g->scroll[i]) g->scroll[i] = want, mark_row(g, i);
    }
    g->scroll_t++;
    return moving;
}

static void dial(struct ct *g, int ticks) {
    int sel = g->sel + (ticks > 0 ? 1 : -1) * (ticks > 0 ? ticks : -ticks);
    sel = sel < 1 ? 1 : sel > 4 ? 4 : sel; /* the dial stops at each end */
    if (sel == g->sel) return;
    mark_row(g, g->sel - 1), mark_row(g, sel - 1);
    g->scroll[g->sel - 1] = 0;
    g->sel = sel, g->scroll_t = 0, g->turn_t = g->reduced ? -1 : 0;
    MARK(g, M_DIAL);
    eng_sfx(ENG_SFX_TICK); /* the wheel's own tick, every detent */
}

/* the answer row under a finger, -1 none */
static int row_at_finger(const struct eng_input *in, int y0, int pitch, int rows, int x0, int x1) {
    if (in->tx < x0 || in->tx >= x1 || in->ty < y0 - pitch / 2) return -1;
    int r = (in->ty - (y0 - pitch / 2)) / pitch;
    return r < rows ? r : -1;
}

static int round_step(struct ct *g, const struct eng_input *in) {
    if (++g->t % 60 == 0) eng_awake(); /* the screen kept lit while the caller listens, as for a video */
    if (g->phase == P_CUE || g->phase == P_AIR) {
        if (in->ticks) dial(g, in->ticks);
        if (g->turn_t >= 0 && ++g->turn_t >= 9) g->turn_t = -1;
        if (g->turn_t >= 0) MARK(g, M_DIAL);
    }
    switch (g->phase) {
    case P_CUE: {
        if (--g->ask > 0) break;
        g->ask = CT_ASK;
        int heard = clip_heard();
        if (heard < 0 || g->t > CT_CUE_MAX) {
            pass_over(g);
            break;
        }
        if (heard > 0) {
            g->heard = 1, g->phase = P_AIR, g->at = 0;
            MARK(g, M_ONAIR | M_RINGS | M_LAMPS | M_REPLAY);
            break;
        }
        say("{player-status\\request}");
        break;
    }
    case P_AIR: {
        g->at++;
        int lit = 10 - g->at / 60;
        lit = lit < 1 ? 1 : lit;
        if (g->at <= 10) MARK(g, M_LAMPS);
        if (lit != g->lamps) {
            g->dim = lit, g->dim_t = 0, g->lamps = lit;
            MARK(g, M_LAMPS);
        }
        int ring = (g->at - g->ring_from) / 10 % 3;
        if (ring != g->ring && !g->reduced) g->ring = ring, MARK(g, M_RINGS);
        if (g->replay == 1 || g->replay == 2) g->replay++, MARK(g, M_REPLAY);
        if (in->pressed & ENG_PLAY && !g->replay) {
            char m[32];
            tk_snprintf(m, sizeof m, "{mcl-seek\\%d}", g->clip_sec);
            say(m);
            g->replay = 1, g->ring_from = g->at, g->ring = 0;
            MARK(g, M_REPLAY | M_RINGS);
        }
        int row = in->pressed & ENG_TOUCH ? row_at_finger(in, ROW_Y(0), 30, 4, NX0, NX1) : -1;
        if (row >= 0)
            call(g, row);
        else if (in->pressed & ENG_CENTRE)
            call(g, g->sel - 1);
        break;
    }
    case P_CALL:
        if (g->t >= CT_CALL) answer(g);
        break;
    case P_REVEAL:
        if (g->t <= 8) /* the wash, the check, the strike */
            mark_row(g, g->right), g->pick >= 0 ? mark_row(g, g->pick) : (void)0;
        if (g->shown != g->score) {
            int from = g->score - g->pts;
            g->shown = g->t >= 20 ? g->score : from + g->pts * g->t / 20;
            MARK(g, M_SCORE);
        }
        if (g->swell && g->swell < 20) g->swell++, MARK(g, M_ONAIR);
        if (g->t >= CT_REVEAL) {
            g->round++;
            if (g->wrongs >= 3 || g->round >= g->rounds)
                contest_end(g);
            else
                round_start(g);
        }
        break;
    }
    if (g->screen == S_ROUND && g->dim >= 0 && g->dim_t < 6) g->dim_t++, MARK(g, M_LAMPS); /* a lamp going out */
    if (g->screen == S_ROUND) scroll_step(g);
    return 1;
}

static const short CUT[15] = { 320, 290, 254, 219, 186, 158, 136, 119, 109, 104, 103, 104, 106, 107, 108 };

static int end_step(struct ct *g, const struct eng_input *in) {
    g->end_t++;
    int moving = 0;
    if (g->end_t <= 14 && !g->reduced) { /* the sheet comes up from under the edge, a 5 px overshoot */
        int was = g->cut;
        g->cut = CUT[g->end_t];
        int y0 = was < g->cut ? was : g->cut, y1 = was < g->cut ? g->cut : was;
        if (y1 > y0) eng_dirty(0, y0, ENG_W, y1 - y0);
        moving = 1;
    }
    if (g->end_t == 14 && g->best_new) MARK(g, M_SUMSCORE);
    if (g->shown != g->score) {
        g->shown = g->end_t >= 40 ? g->score : g->score * g->end_t / 40;
        MARK(g, M_SUMSCORE);
        moving = 1;
    }
    if (in->ticks && g->nmissed) {
        int sel = g->sel + (in->ticks > 0 ? 1 : -1);
        sel = sel < 0 ? 0 : sel >= g->nmissed ? g->nmissed - 1 : sel;
        if (sel != g->sel) {
            g->sel = sel;
            g->top = sel < g->top ? sel : sel >= g->top + SUM_ROWS ? sel - SUM_ROWS + 1 : g->top;
            MARK(g, M_SUMROWS);
            eng_sfx(ENG_SFX_TICK);
        }
    }
    int row = in->pressed & ENG_TOUCH ? row_at_finger(in, SUM_Y(0), 26, SUM_ROWS, SX0, SX1) : -1;
    if (row >= 0 && g->top + row < g->nmissed) g->sel = g->top + row;
    if ((row >= 0 && g->top + row < g->nmissed) || (in->pressed & ENG_CENTRE && g->nmissed)) {
        /* the missed song played in full, from its folder, on Now Playing: the queue is the
         * user's choice now, so nothing held is given back */
        struct pick *p = g->k->pk;
        int song = g->missed[g->sel];
        g->k->owned = 0;
        say("{mcl-stop\\null}");
        songs_open(p->path[song], p->song[song].title, 0);
        eng_quit();
        return 0;
    }
    if (in->pressed & ENG_PLAY || (in->pressed & ENG_CENTRE && !g->nmissed)) { /* a new contest */
        g->screen = S_TUNE, g->sel = g->mode, g->lift = 0;
        eng_dirty_all();
    }
    return moving;
}

static int step_in(struct ct *g, const struct eng_input *in);
static int step(void *state, const struct eng_input *in) {
    struct ct *g = state;
    int moving = step_in(g, in);
    flush(g);
    return moving;
}
static int step_in(struct ct *g, const struct eng_input *in) {
    struct keep *k = g->k;
    if (!k || !k->art) {
        if (g->screen == S_BROKEN && !g->t++) toast("Caller Ten's pictures are missing");
        eng_quit();
        return 0;
    }
    if (k->watch) timer_remove(k->watch), k->watch = 0;
    if (g->clip_held) { /* back from a pause: the clip on again where it waited */
        say("{mcl-play\\null}");
        g->clip_held = 0;
    }
    if (k->owned && I(g_dacoff_time, 0) > 0) I(g_dacoff_time, 0) = 0; /* the DAC kept on, as a video */
    g->reduced = motion_reduced();
    int moving = sfx_step(g);
    if (g->needle_t < 8) { /* the needle slides 59 px in 8 frames, easing out */
        static const unsigned char EASE[9] = { 0, 30, 54, 73, 87, 96, 101, 103, 104 };
        int to = STATION_X4(g->screen == S_TUNE ? g->sel : g->mode);
        g->needle4 = g->needle_from + (to - g->needle_from) * EASE[++g->needle_t] / 104;
        MARK(g, M_GLASS), moving = 1;
    }
    if (g->lift_dir > 0 && g->lift < 6) g->lift++, MARK(g, M_POSE | M_DIAL), moving = 1;
    if (g->lift_dir < 0 && g->lift > 0) g->lift--, MARK(g, M_POSE | M_DIAL), moving = 1;
    switch (g->screen) {
    case S_TUNE: {
        int sel = g->sel + in->ticks;
        sel = sel < 0 ? 0 : sel > 2 ? 2 : sel;
        int row = in->pressed & ENG_TOUCH ? row_at_finger(in, TUNE_Y(0), 40, 3, NX0, NX1) : -1;
        if (row >= 0) sel = row;
        if (sel != g->sel) {
            MARK(g, (M_TUNE << g->sel) | (M_TUNE << sel));
            g->sel = sel, g->needle_from = g->needle4, g->needle_t = g->reduced ? 7 : 0;
            MARK(g, M_GLASS | M_KNOB);
            eng_sfx(ENG_SFX_TICK);
            moving = 1;
        }
        if (row >= 0 || in->pressed & ENG_CENTRE) begin(g);
        return moving;
    }
    case S_STANDBY:
        if (++g->t % 5 == 0) MARK(g, M_LAMPS);
        if (!g->k->pk || __atomic_load_n(&g->k->pk->done, __ATOMIC_ACQUIRE)) {
            if (g->k->pk && g->k->pk->why == WHY_OK)
                contest_start(g);
            else
                g->screen = S_NOSIGNAL, eng_dirty_all();
        }
        return 1;
    case S_NOSIGNAL:
        if (in->pressed & ENG_CENTRE) eng_quit();
        return moving;
    case S_ROUND: return round_step(g, in) | moving;
    case S_END: return end_step(g, in) | moving;
    }
    return moving;
}

/* The menu, the screen going off, or the page closing: the clip waits, and ct_watch looks after
 * what is to be given back if Games closes the game from here. */
static void pause(void *state) {
    struct ct *g = state;
    struct keep *k = g->k;
    if (!k) return;
    if (k->owned && g->screen == S_ROUND && g->phase != P_REVEAL && !g->clip_held) {
        say("{mcl-pause\\null}");
        g->clip_held = 1;
    }
    if (!k->watch) k->watch = timer_add(ct_watch, k, CT_WATCH_MS);
}

static void init(void *state, int h) {
    struct ct *g = state;
    struct keep *k = calloc(1, sizeof *k);
    unsigned n = 0;
    unsigned char *art = k ? read_all(CT_ART, ART_BYTES, &n) : 0;
    if (art && (n != ART_BYTES || !games_ok(art, n, ART_MAGIC))) free(art), art = 0;
    if (k) k->art = art;
    g->k = k, g->h = h;
    eng_load("callerten", &g->save, sizeof g->save, CT_MAGIC);
    if (g->save.best[0] > 1000 || g->save.best[1] > 1000 || g->save.best[2] > 1000 ||
        g->save.played > 99999999)
        memset(&g->save, 0, sizeof g->save); /* sealed, but no contest's: none */
    g->screen = art ? S_TUNE : S_BROKEN;
    g->needle4 = g->needle_from = STATION_X4(0), g->needle_t = 8;
    g->turn_t = g->dim = -1, g->cut = 320, g->sel = 0;
    eng_dirty_all();
}

const struct eng_game eng_callerten = { "Caller Ten", sizeof(struct ct), ENG_STILL | ENG_FULL,
                                        init, step, draw, pause };
