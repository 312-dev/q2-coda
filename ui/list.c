/* The Songs list as OLED draws it (assets/themes/oled/theme.xml, docs/oled-design.md): a 58 px
 * status row, 32 px rows from y 58 with the text at x 24, seven whole rows to y 282 and the next
 * fading into the screen's foot, the title in Inter 24 with the artist after it in the muted 18
 * (patch/song_rows.c song_fit's rule), the accent's bar on the selected row, and the scroll
 * indicator while the wheel turns. Row 0 is the Shuffle row.
 *
 * State and drawing are apart. A tick or the motion's clock changes the state and marks rects
 * (mark); list_draw then draws whatever of the page lies in a rect, from the state alone. A row
 * in sight keeps its laid-out text (rowc), so drawing it again measures nothing. */
#include <stdio.h>
#include <string.h>

#include "../patch/wheel_accel.h"
#include "q2ui.h"

#define BAR_H 58 /* the status row */
#define ROW_H 32
#define LIST_END 282 /* whole rows end here; the row below fades */
#define ROWS ((LIST_END - BAR_H) / ROW_H)
#define SHOWN (ROWS + 1) /* with the fading one */
#define TEXT_X 24
#define TEXT_MARGIN 2 /* a label's own margin (patch/navigation.h HSCROLL_MARGIN) */
#define RIGHT_X 28
#define TITLE_PX 24
#define META_PX 18
#define META_DY 2     /* the small text's box below the title's (song_rows.c SONG_SMALL_DY) */
#define ARTIST_MIN 40 /* an artist with less room than this is dropped (SONG_ARTIST_MIN) */
#define SHUFFLE_X 54
#define SEL_X 13 /* the bar at rest: the 16 px inset, raised 2% (patch/tilt.c PRESS_FOCUS) */
#define SEL_W 349
#define SEL_R 8
#define ACCENT 0x2160d8
#define MUTED 0x858585
#define CLOCK_X 62
#define CLOCK_Y 14
#define SB_X 366 /* the indicator: 4 px wide, 5 px from the edge, y 134 to 206 */
#define SB_W 4
#define SB_TOP 134
#define SB_BOTTOM 206
#define SB_MIN 8
#define SB_SHOW_MS 1000 /* shown this long after the last tick (patch/ui_kit.h UI_SHOW_MS), */
#define SB_FADE_MS 200  /* the last of it fading */
#define LEAN_PX 3       /* the bar's lean toward the row it came to (tilt.c TILT_LIFT), */
#define LEAN_MS 220     /* sprung back over this (TILT_SPRING_MS) */
#define LEAN_MARGIN 4   /* what a leaning bar is drawn past its row */
#define CACHE 16        /* rows whose text is kept: a power of two, more than SHOWN */

int list_copy_scroll;
int list_motion = LIST_MOTION_SPRING;

static struct {
    const source_t *src;
    int n, top, sel;
    unsigned lean_at, sb_at; /* when the lean and the indicator began, 0 for neither */
    int lean_dir, lean, lean_fine, sb_alpha; /* lean in px, lean_fine in 1/256 px */
    char clock[8];
    run_t clock_run, shuffle_run;
    struct rowc {
        int index; /* -1: empty */
        run_t title, artist;
        int artist_x;
    } rows[CACHE];
    wheel_accel_t accel;
    int ndraw;
    rect_t draw[8];
} L;

static rect_t row_rect(int i) {
    return (rect_t){ 0, BAR_H + (i - L.top) * ROW_H, SCREEN_W, ROW_H };
}
static rect_t list_rect(void) { return (rect_t){ 0, BAR_H, SCREEN_W, SCREEN_H - BAR_H }; }
static rect_t sb_rect(void) { return (rect_t){ SB_X, SB_TOP, SB_W, SB_BOTTOM - SB_TOP }; }

/* r is to be drawn again and shown; shown alone when the canvas already holds it (a copied scroll)
 */
static void mark_draw(rect_t r) {
    if (L.ndraw < (int)(sizeof L.draw / sizeof *L.draw))
        L.draw[L.ndraw++] = r;
    else
        L.draw[0] = list_rect(), L.draw[1] = (rect_t){ 0, 0, SCREEN_W, BAR_H }, L.ndraw = 2;
}
static void mark(rect_t r) {
    mark_draw(r);
    dmg_add(r);
}
static void mark_row(int i) {
    rect_t r = row_rect(i);
    r.y -= LEAN_MARGIN, r.h += 2 * LEAN_MARGIN;
    if (r.y < BAR_H) r.h -= BAR_H - r.y, r.y = BAR_H;
    mark(r);
}
int list_dirty(rect_t *out, int max) {
    int n = L.ndraw < max ? L.ndraw : max;
    memcpy(out, L.draw, (size_t)n * sizeof *out);
    L.ndraw = 0;
    return n;
}

void list_open(const source_t *src) {
    memset(&L, 0, sizeof L);
    L.src = src, L.n = src->n + 1;
    for (int i = 0; i < CACHE; ++i) L.rows[i].index = -1;
    mark((rect_t){ 0, 0, SCREEN_W, SCREEN_H });
}
void list_open_again(void) { mark((rect_t){ 0, 0, SCREEN_W, SCREEN_H }); }
int list_selected(void) { return L.sel; }

void list_clock(const char *hhmm) {
    if (!strcmp(hhmm, L.clock)) return;
    snprintf(L.clock, sizeof L.clock, "%s", hhmm);
    L.clock_run.n = 0;
    mark((rect_t){ CLOCK_X, CLOCK_Y, 60, 18 });
}

/* Row i's text, laid out once while the row stays in sight: the title keeps what it needs of the
 * room and the artist takes what is left after two spaces, cut with an ellipsis, or is dropped. */
static struct rowc *row_text(int i) {
    struct rowc *c = &L.rows[i & (CACHE - 1)];
    if (c->index == i && !run_stale(&c->title) && !run_stale(&c->artist)) return c;
    int room = SCREEN_W - 1 - RIGHT_X - TEXT_MARGIN - TEXT_X - TEXT_MARGIN;
    static run_t gap;
    if (!gap.n || run_stale(&gap)) run_make(&gap, FACE_REGULAR, TITLE_PX, "  ", -1);
    c->index = i;
    run_make(&c->title, FACE_REGULAR, TITLE_PX, L.src->title(i - 1), room);
    int left = room - c->title.w - gap.w;
    c->artist.n = 0;
    c->artist.made = c->title.made;
    if (left >= ARTIST_MIN && *L.src->artist(i - 1)) {
        run_make(&c->artist, FACE_REGULAR, META_PX, L.src->artist(i - 1), left);
        c->artist_x = TEXT_X + TEXT_MARGIN + c->title.w + gap.w;
    }
    return c;
}

static void draw_row(int i, rect_t r) {
    int selected = i == L.sel;
    if (i == 0) {
        if (!L.shuffle_run.n || run_stale(&L.shuffle_run))
            run_make(&L.shuffle_run, FACE_REGULAR, TITLE_PX, "Shuffle", -1);
        run_draw(&L.shuffle_run, SHUFFLE_X, r.y + (ROW_H - TITLE_PX) / 2,
                 selected ? 0xffffff : ACCENT);
        return;
    }
    struct rowc *c = row_text(i);
    run_draw(&c->title, TEXT_X + TEXT_MARGIN, r.y + (ROW_H - TITLE_PX) / 2, 0xffffff);
    if (c->artist.n)
        run_draw(&c->artist, c->artist_x, r.y + (ROW_H - META_PX) / 2 + META_DY,
                 selected ? 0xffffff : MUTED);
}

static void draw_indicator(void) {
    if (!L.sb_alpha || L.n <= ROWS) return;
    int track = SB_BOTTOM - SB_TOP, at = (int)((long long)track * L.top / L.n),
        len = track * ROWS / L.n;
    if (len < SB_MIN) len = SB_MIN;
    if (len > track - at) len = track - at;
    rect_t t = sb_rect(), thumb = { SB_X, SB_TOP + at, SB_W, len };
    /* 4 px wide: its ends are a 2 px square short of round, which the fade's alpha covers */
    fill_alpha(t, 0x3a3a3a, (unsigned)L.sb_alpha);
    fill_alpha(thumb, 0xe5e5e5, (unsigned)L.sb_alpha);
}

void list_draw(rect_t r) {
    rect_t part;
    clip_set(r);
    fill(r, 0x000000);
    if (r.y < BAR_H) {
        if (!L.clock_run.n || run_stale(&L.clock_run))
            run_make(&L.clock_run, FACE_MEDIUM, META_PX, L.clock, -1);
        run_draw(&L.clock_run, CLOCK_X, CLOCK_Y, 0xffffff);
    }
    if (!rect_meet(r, list_rect(), &part)) return;
    clip_set(part);
    if (L.sel >= L.top && L.sel < L.top + SHOWN) {
        rect_t bar = row_rect(L.sel);
        bar.x = SEL_X, bar.w = SEL_W, bar.y += L.lean;
        fill_round(bar, SEL_R, ACCENT);
    }
    for (int i = L.top; i < L.top + SHOWN && i < L.n; ++i) {
        rect_t row = row_rect(i);
        if (rect_meet(row, part, 0)) draw_row(i, row);
    }
    shade_rows(LIST_END, SCREEN_H, 150, 0);
    if (rect_meet(part, sb_rect(), 0)) draw_indicator();
}

/* The view moved from top `was` with the canvas still showing it: the rows both views hold are
 * moved in the canvas (they are whole and unshaded above LIST_END), and only the rows new to the
 * view, the two the selection left and came to and the shaded foot are drawn. */
static void scroll_by_copy(int was, int old_sel) {
    int d = L.top - was, keep = ROWS - (d < 0 ? -d : d);
    uint32_t *first = canvas + BAR_H * SCREEN_W;
    if (d > 0)
        memmove(first, first + d * ROW_H * SCREEN_W,
                (size_t)(keep * ROW_H * SCREEN_W) * sizeof *canvas);
    else
        memmove(first - d * ROW_H * SCREEN_W, first,
                (size_t)(keep * ROW_H * SCREEN_W) * sizeof *canvas);
    dmg_add(list_rect());
    for (int i = L.top; i < L.top + ROWS; ++i)
        if (i < was || i >= was + ROWS || i == old_sel || i == L.sel) mark_draw(row_rect(i));
    mark_draw((rect_t){ 0, LIST_END, SCREEN_W, SCREEN_H - LIST_END });
    mark_draw((rect_t){ SB_X, BAR_H, SB_W, LIST_END - BAR_H });
}

/* The first row at or after i, going dir, whose title starts with another letter than row i's */
static int letter_step(int i, int dir) {
    if (i < 1) return dir > 0 ? 1 : 0;
    int c = (unsigned char)L.src->title(i - 1)[0] | 0x20, j = i;
    while (j + dir >= 1 && j + dir < L.n &&
           ((unsigned char)L.src->title(j + dir - 1)[0] | 0x20) == c)
        j += dir;
    return j + dir >= 1 && j + dir < L.n ? j + dir : j;
}

void list_tick(int dir, unsigned ms) {
    int step = wheel_accel(&L.accel, &L, dir, ms, L.n), old = L.sel, was = L.top;
    int sel = step == WA_LETTER ? letter_step(L.sel, dir) : L.sel + dir * step;
    sel = sel < 0 ? 0 : sel >= L.n ? L.n - 1 : sel;
    L.sb_at = ms ? ms : 1;
    if (L.sb_alpha != 255) L.sb_alpha = 255, mark(sb_rect());
    if (sel == old) return;
    L.sel = sel;
    if (sel < L.top) L.top = sel;
    if (sel >= L.top + ROWS) L.top = sel - ROWS + 1;
    int lean_was = L.lean;
    if (list_motion)
        L.lean_at = ms ? ms : 1, L.lean_dir = dir, L.lean = dir * LEAN_PX,
        L.lean_fine = L.lean * 256;
    int moved = L.top - was;
    if (!moved) {
        mark_row(old);
        mark_row(sel);
    } else if (list_copy_scroll && !lean_was && moved > -ROWS && moved < ROWS) {
        scroll_by_copy(was, old);
        mark_row(sel);
    } else
        mark(list_rect());
    if (moved) mark(sb_rect());
}

void list_step(unsigned ms) {
    if (L.lean_at) {
        unsigned t = ms - L.lean_at;
        /* eased out: (1 - t)^2 of the lean is left */
        int left = t >= LEAN_MS ? 0 : (int)(LEAN_MS - t);
        int fine = L.lean_dir * LEAN_PX * 256 * left / LEAN_MS * left / LEAN_MS, lean = fine / 256;
        if (t >= LEAN_MS) L.lean_at = 0;
        /* the row is drawn again when the bar's place changes; LIST_MOTION_EVERY draws it at every
         * refresh of the spring, as a bar that also grew and cast a shadow would need */
        if (lean != L.lean || (list_motion == LIST_MOTION_EVERY && fine != L.lean_fine))
            mark_row(L.sel);
        L.lean = lean, L.lean_fine = fine;
    }
    if (L.sb_at) {
        unsigned t = ms - L.sb_at;
        int a = t < SB_SHOW_MS - SB_FADE_MS ? 255
                : t >= SB_SHOW_MS           ? 0
                                            : (int)(255 * (SB_SHOW_MS - t) / SB_FADE_MS);
        if (t >= SB_SHOW_MS) L.sb_at = 0;
        if (a != L.sb_alpha) L.sb_alpha = a, mark(sb_rect());
    }
}

/* How long the loop may sleep at ms with nothing to draw: a refresh while the bar springs back or
 * the indicator fades, the rest of the indicator's show, or until input (-1). */
int list_wait_ms(unsigned ms) {
    if (L.lean_at) return 16;
    if (L.sb_at) {
        unsigned t = ms - L.sb_at;
        return t < SB_SHOW_MS - SB_FADE_MS ? (int)(SB_SHOW_MS - SB_FADE_MS - t) : 16;
    }
    return -1;
}
