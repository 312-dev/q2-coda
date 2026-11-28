/* The lyrics of the song playing (docs/ipod.md#lyrics, today's patch/lyrics.c), from Q2LIB/1 (ui/lyrics.c), opened
 * from Now Playing by a swipe to the left; Return or a swipe to the right goes back. The playing line is centred, the
 * one before above it and the next below, on the 24 px text column, every line Inter at the design's 22 px: the
 * playing line in Medium, the rest in Regular and Text Secondary. A long line wraps at a space or beside a CJK
 * character onto up to LINE_ROWS rows. The playing line lights in Text Primary: word-timed lyrics word by word, each
 * word from its mark to the next and the last at WORD_UNIT_MS a letter (three a CJK character), WORD_MIN_MS at least,
 * done by the next line's start; line-timed lyrics whole as the line starts. Before the first line the first waits in
 * the middle, unlit; untimed lyrics stand from the top with no playing line. The view's top and foot FADE px fade
 * to the page's ground. The wheel moves the lines a line a tick; left alone RESUME_US they follow the song again. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "q2ui.h"

#define TEXT_X 24
#define TEXT_W (SCREEN_W - 2 * TEXT_X)
#define PX TYPE(22)
#define PITCH (PX + PX / 8) /* a row's step within a line (patch/lyrics.c LY_PITCH) */
#define GAP 21              /* between lines (LYRIC_GAP) */
#define LINE_ROWS 4
#define VIEW_Y 36 /* under the status row */
#define VIEW_H (SCREEN_H - VIEW_Y)
#define FADE 32
#define WORD_UNIT_MS 90
#define WORD_MIN_MS 800
#define RESUME_US 5000000u
#define SWIPE_PX 60

struct laid {
    int rows;
    unsigned start[LINE_ROWS], len[LINE_ROWS]; /* bytes of the line's text */
    float w[LINE_ROWS];
    int y; /* the line's top in the column of lines */
};
struct lyr_st {
    unsigned seq;
    int id, n, total; /* total: the column's height */
    struct laid *laid;
    spring_t scroll; /* the column's offset: the view's top is at this y of the column */
    uint64_t t, held_at; /* held_at: the wheel moved the lines; 0 following */
    int cur, lit, touch_x, touch_y, touching;
};

static int cjk(unsigned c) { return (c >= 0x2e80 && c < 0xa000) || (c >= 0xac00 && c < 0xd7b0) || (c >= 0xff00 && c < 0xfff0); }
static unsigned cp_at(const char *s, unsigned i, unsigned *next) {
    const unsigned char *p = (const unsigned char *)s + i;
    unsigned c = *p, n = c < 0x80 ? 0 : c < 0xe0 ? 1 : c < 0xf0 ? 2 : 3; /* the text is well-formed (lyrics.c) */
    c &= n ? 0x3fu >> n : 0x7fu;
    for (unsigned k = 1; k <= n; ++k) c = c << 6 | (p[k] & 0x3f);
    *next = i + n + 1;
    return c;
}
static float width_of(const char *s, unsigned from, unsigned to) {
    char tmp[1024];
    unsigned n = to - from < sizeof tmp - 1 ? to - from : sizeof tmp - 1;
    memcpy(tmp, s + from, n), tmp[n] = 0;
    return text_width(FACE_MEDIUM, PX, tmp); /* measured in the playing line's face, so it fits in either */
}

/* line's rows: a break before a space (which goes) or beside a CJK character, else mid-word */
static void wrap(const char *s, unsigned len, struct laid *o) {
    o->rows = 0;
    for (unsigned i = 0; i < len && o->rows < LINE_ROWS;) {
        while (i < len && s[i] == ' ') ++i;
        if (i >= len) break;
        unsigned start = i, brk = 0, resume = 0, prev = 0, next;
        for (unsigned c; i < len; prev = c, i = next) {
            c = cp_at(s, i, &next);
            if (c == ' ') brk = i, resume = next;
            else if (i > start && (cjk(c) || cjk(prev))) brk = i, resume = i;
            if (i > start && width_of(s, start, next) > TEXT_W) break;
        }
        unsigned end = i;
        if (i < len && brk > start) end = brk, i = resume;
        while (end > start && s[end - 1] == ' ') --end;
        o->start[o->rows] = start, o->len[o->rows] = end - start, o->w[o->rows] = width_of(s, start, end);
        ++o->rows;
    }
    if (!o->rows) o->rows = 1, o->start[0] = o->len[0] = 0, o->w[0] = 0;
}

static const lyrics_t *held(struct lyr_st *s) {
    const lyrics_t *l = lyrics_for(now_get()->id);
    if (s->seq == lyrics_seq() && s->id == l->id) return l;
    free(s->laid);
    s->laid = 0, s->n = 0, s->total = 0, s->seq = lyrics_seq(), s->id = l->id, s->cur = -2, s->held_at = 0;
    if (l->kind >= LYR_PLAIN && (s->laid = calloc((size_t)l->n, sizeof *s->laid))) {
        for (int i = 0; i < l->n; ++i) {
            wrap(l->text + l->line[i].text, l->line[i].len, &s->laid[i]);
            s->laid[i].y = s->total;
            s->total += s->laid[i].rows * PITCH + GAP;
        }
        s->n = l->n;
    }
    return l;
}

/* where the column's top sits for line i centred; untimed lyrics from the top */
static double scroll_for(const struct lyr_st *s, int i) {
    if (i < 0 || i >= s->n) return 0;
    const struct laid *d = &s->laid[i];
    return d->y + (d->rows * PITCH) / 2.0 - VIEW_H / 2.0;
}

/* how far along line i, laid end to end over its rows, byte b of its text is */
static float along(const lyrics_t *l, const struct laid *d, int i, unsigned b) {
    const char *t = l->text + l->line[i].text;
    float x = 0;
    for (int r = 0; r < d->rows; x += d->w[r++]) {
        if (b < d->start[r]) return x;
        if (b < d->start[r] + d->len[r]) return x + width_of(t, d->start[r], b);
    }
    return x;
}
/* how long line i takes sung from byte b: a letter WORD_UNIT_MS, a CJK character three, WORD_MIN_MS at least */
static int sung(const lyrics_t *l, int i, unsigned b) {
    const char *t = l->text + l->line[i].text;
    int units = 0;
    for (unsigned next, c; b < l->line[i].len; b = next)
        c = cp_at(t, b, &next), units += cjk(c) ? 3 : c > ' ' && c != ',' && c != '.' ? 1 : 0;
    return units * WORD_UNIT_MS > WORD_MIN_MS ? units * WORD_UNIT_MS : WORD_MIN_MS;
}
/* line i's lit width at ms (patch/lyrics.c ly_lit) */
static float lit_of(const lyrics_t *l, const struct laid *d, int i, int ms) {
    const lyr_line *ln = &l->line[i];
    float full = 0, x0 = 0;
    for (int r = 0; r < d->rows; ++r) full += d->w[r];
    int t0 = ln->at, next = -1;
    if (ms < t0) return 0;
    if (!ln->marks) return full;
    for (int k = i + 1; k < l->n && next < 0; ++k)
        if (l->line[k].at > ln->at) next = l->line[k].at;
    unsigned b = 0;
    for (unsigned k = 0; k <= ln->marks; ++k) {
        int t1;
        float x1;
        if (k < ln->marks) {
            const lyr_mark *w = &l->mark[ln->mark + k];
            t1 = w->ms < t0 ? t0 : w->ms, x1 = along(l, d, i, w->at), b = w->at;
        } else {
            if (x0 >= full) break;
            t1 = t0 + sung(l, i, b), x1 = full;
            if (next >= 0 && t1 > next) t1 = next;
        }
        if (ms < t1) return x0 + (x1 - x0) * (float)(ms - t0) / (float)(t1 - t0);
        t0 = t1, x0 = x1;
    }
    return full;
}

static void *l_open(void *arg) {
    (void)arg;
    struct lyr_st *s = calloc(1, sizeof *s);
    if (!s) return 0;
    s->t = ui_time(), s->seq = ~0u, s->id = -2, s->cur = -2, s->lit = -1;
    ui_again();
    return s;
}
static void l_close(void *st) {
    struct lyr_st *s = st;
    free(s->laid), free(s);
}
static void l_event(void *st, const input_t *e) {
    struct lyr_st *s = st;
    if (e->type == IN_KEY_UP && e->value == KEY_UI_RETURN) {
        ui_pop();
        return;
    }
    if (e->type == IN_TOUCH_DOWN) s->touching = 1, s->touch_x = e->x, s->touch_y = e->y;
    if (e->type == IN_TOUCH_UP && s->touching) {
        s->touching = 0;
        int dx = e->x - s->touch_x, dy = e->y - s->touch_y;
        if (dx > SWIPE_PX && abs(dy) < dx) { /* the swipe back to Now Playing */
            ui_pop();
            return;
        }
    }
    if (e->type == IN_TICK && s->n) {
        double top = s->total - GAP - VIEW_H / 2.0, to = s->scroll.to + e->value * (PITCH + GAP);
        s->scroll.to = to < -VIEW_H / 2.0 ? -VIEW_H / 2.0 : to > top ? top : to;
        s->held_at = ui_time();
        ui_again();
    }
}
static void l_step(void *st) {
    struct lyr_st *s = st;
    const lyrics_t *l = held(s);
    float dt = (float)(ui_time() - s->t) / 1e6f;
    s->t = ui_time();
    int ms = now_pos_ms(), cur = s->n ? lyrics_line_at(l, ms) : -1;
    if (s->held_at && ui_time() - s->held_at >= RESUME_US) s->held_at = 0;
    if (cur != s->cur) {
        int first = s->cur == -2;
        s->cur = cur;
        if (!s->held_at && s->n) {
            double to = l->kind == LYR_PLAIN ? 0 : scroll_for(s, cur >= 0 ? cur : 0);
            if (first) spring_set(&s->scroll, to);
            else s->scroll.to = to;
        }
        ui_mark((rect_t){ 0, VIEW_Y, SCREEN_W, VIEW_H });
    }
    int lit = cur >= 0 ? (int)lit_of(l, &s->laid[cur], cur, ms) : -1;
    if (lit != s->lit) s->lit = lit, ui_mark((rect_t){ 0, VIEW_Y, SCREEN_W, VIEW_H });
    if (spring_step(&s->scroll, dt, 0.35f, 1)) ui_mark((rect_t){ 0, VIEW_Y, SCREEN_W, VIEW_H }), ui_again();
    if (s->held_at) ui_wake_ms(RESUME_US / 1000u);
    if (now_get()->playing && cur >= 0 && l->line[cur].marks) ui_again(); /* the light sweeps */
    else if (now_get()->playing && l->kind >= LYR_LINE) ui_wake_ms(50); /* the next line's start */
}

static void l_draw(void *st, rect_t r) {
    struct lyr_st *s = st;
    const lyrics_t *l = lyrics_held();
    fill(r, tok(TOK_CANVAS));
    if (l->id != s->id) return; /* another song's: the next step lays them out */
    rect_t was = clip_get(), view;
    if (!rect_meet(r, (rect_t){ 0, VIEW_Y, SCREEN_W, VIEW_H }, &view)) return;
    if (l->kind == LYR_NONE || !s->n) {
        if (l->kind == LYR_ASKING) return; /* none shown until they come */
        run_t run;
        run_make(&run, FACE_REGULAR, PX, "No Lyrics", TEXT_W);
        run_draw(&run, (SCREEN_W - run.w) / 2, VIEW_Y + (VIEW_H - PX) / 2.0f, tok(TOK_SECONDARY), 256);
        return;
    }
    clip_set(view);
    int on_at = s->cur >= 0 ? l->line[s->cur].at : -1;
    for (int i = 0; i < s->n; ++i) {
        const struct laid *d = &s->laid[i];
        float y = (float)(VIEW_Y + d->y - s->scroll.x);
        if (y + d->rows * PITCH < view.y || y > view.y + view.h) continue;
        int on = on_at >= 0 && l->line[i].at == on_at;
        float lit = on ? lit_of(l, d, i, now_pos_ms()) : 0, x = 0;
        const char *t = l->text + l->line[i].text;
        for (int k = 0; k < d->rows; x += d->w[k++], y += PITCH) {
            char row[1024];
            unsigned n = d->len[k] < sizeof row - 1 ? d->len[k] : sizeof row - 1;
            memcpy(row, t + d->start[k], n), row[n] = 0;
            run_t run;
            run_make(&run, on ? FACE_MEDIUM : FACE_REGULAR, PX, row, -1);
            float y0 = y + (PITCH - PX) / 2.0f;
            run_draw(&run, TEXT_X, y0, tok(TOK_SECONDARY), 256);
            if (lit > x) { /* the sung part over it, clipped to the lit width */
                rect_t sungr, inside;
                int to = TEXT_X + (int)(lit - x + 0.5f);
                sungr = (rect_t){ 0, (int)y, to, PITCH + 1 };
                if (rect_meet(sungr, view, &inside)) {
                    clip_set(inside);
                    run_draw(&run, TEXT_X, y0, tok(TOK_PRIMARY), 256);
                    clip_set(view);
                }
            }
        }
    }
    fade_rows(VIEW_Y, VIEW_Y + FADE, 0, 255);
    fade_rows(SCREEN_H - FADE, SCREEN_H, 255, 0);
    clip_set(was);
}
static int l_where(void *st, char *name, size_t room) {
    (void)st;
    snprintf(name, room, "lyrics");
    return 1;
}
static int l_trace(void *st, char *out, size_t room) {
    struct lyr_st *s = st;
    const lyrics_t *l = lyrics_held();
    int fffd = 0; /* U+FFFD in the text held: each ill-formed sequence the text came with */
    for (const char *p = l->text; p && (p = strstr(p, "\xef\xbf\xbd")); p += 3) ++fffd;
    return snprintf(out, room, "sel=%d kind=%d lines=%d lit=%d scroll=%.0f fffd=%d", s->cur, l->kind, s->n, s->lit, s->scroll.x, fffd);
}

const ui_page page_lyrics = { "lyrics", l_open, l_event, l_step, l_draw, l_close, 0, l_where, 0, l_trace };
