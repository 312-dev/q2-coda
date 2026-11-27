/* A list's own order and choice of rows, and its editor (docs/oled-design.md#edit-library): Edit Library, on the
 * Library's rows. The order is demo's file (patch/library_edit.c, docs/internals.md#library-order), so either program
 * reads what the other wrote: each row's letter in the user's order, each led by '+' or '-' once the user has shown or
 * hidden it, padded with NULs to ORDER_MAX, read against the list's default order (a letter it does not know dropped, a
 * row it does not name put in after the row before it there).
 *
 * The editor lists every row of the page's that can show, each with its check circle and grip, and a hint under them;
 * the list ends a row higher than other lists, its rows past that end fading into the hint's band. Centre lifts the
 * selected row onto a raised card, the wheel carries it a row a click (the row it passes sliding aside), Centre puts it
 * down and saves; Return while it is carried puts it back where it was. Play/Pause shows or hides the selected row,
 * saved at once: it is demo's key elsewhere, so the editor asks for it while open ("playkey 1" down q2ui's pipe,
 * patch/q2ui_gate.c q2ui_input) and gives it back as it closes. Return, or Centre held again, saves and leaves. A row
 * the editor does not list (one of demo's that q2ui has no page for, Shuffle, Videos) keeps its place: the listed
 * rows' new order fills the places they held (library_edit.c lr_merge).
 *
 * ponytail: Centre's hold that opens it is q2ui's one hold, KEY_HOLD_MS (600 ms), not demo's LR_HOLD_MS (550 ms); a
 * finger's drag (the grip, a held row) waits for q2ui's touch. */
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#include "q2ui.h"

#define EDIT_END 250  /* the list's end: a row higher than a list's, the hint under it */
#define EDIT_FADE 284 /* the rows past it faded into the ground by here, the hint's band's top */
#define HINT_Y 298    /* the hint's middle */
#define HINT_GLYPH 13 /* the room of the grip drawn in the hint, between its two runs */

static int find(const char *s, int n, int c) {
    for (int i = 0; i < n; ++i)
        if (s[i] == c) return i;
    return -1;
}

int order_resolve(const char *def, const char *saved, char *out, char *marks) {
    int n = 0, mark = 0, nd = (int)strlen(def);
    if (marks) memset(marks, 0, ORDER_MAX);
    for (int i = 0; saved[i] && i < ORDER_MAX - 1; ++i) {
        int c = saved[i], at = find(def, nd, c);
        if (c == '+' || c == '-') {
            mark = c;
            continue;
        }
        if (at >= 0 && find(out, n, c) < 0) {
            out[n++] = (char)c;
            if (marks) marks[at] = (char)mark;
        }
        mark = 0;
    }
    for (int i = 0; i < nd; ++i) {
        if (find(out, n, def[i]) >= 0) continue;
        int at = 0;
        for (int j = i; j-- > 0;) {
            int k = find(out, n, def[j]);
            if (k >= 0) {
                at = k + 1;
                break;
            }
        }
        for (int k = n++; k > at; --k) out[k] = out[k - 1];
        out[at] = def[i];
    }
    out[n] = 0;
    return n;
}
int order_read(const char *file, const char *def, char *out, char *marks) {
    char saved[ORDER_MAX] = "";
    FILE *f = fopen(file, "rb");
    if (f) saved[fread(saved, 1, sizeof saved - 1, f)] = 0, fclose(f);
    return order_resolve(def, saved, out, marks);
}
int order_shows(int mark, int rule) { return rule == EDIT_LOCKED || (mark ? mark == '+' : rule == EDIT_SHOWN); }

/* order (resolved) with each row's mark before it, as demo writes it (library_edit.c lr_store): beside the file, synced,
 * renamed over it, so a power cut leaves the old file or the new one. */
static int order_write(const char *file, const char *def, const char *order, const char *marks) {
    char out[ORDER_MAX] = { 0 }, tmp[256];
    for (int i = 0, n = 0; order[i] && n < ORDER_MAX - 2; ++i) {
        int at = find(def, (int)strlen(def), order[i]);
        if (at >= 0 && marks[at]) out[n++] = marks[at];
        out[n++] = order[i];
    }
    snprintf(tmp, sizeof tmp, "%s.tmp", file);
    int fd = open(tmp, O_WRONLY | O_CREAT | O_TRUNC, 0644);
    if (fd < 0) return -1;
    int bad = write(fd, out, sizeof out) != (long)sizeof out;
    bad |= fsync(fd) != 0;
    bad |= close(fd) != 0;
    if (bad || rename(tmp, file)) {
        unlink(tmp);
        return -1;
    }
    return 0;
}

static struct {
    const edit_row *rows;
    int nrows;
    const char *def, *file;
    void (*done)(void);
    char marks[ORDER_MAX]; /* each row's mark by its place in def: 0, '+' or '-' */
    char shown[ORDER_MAX]; /* the editor's rows' letters, in order */
    char before[ORDER_MAX]; /* ... as they were when Centre lifted one */
    int n, from;            /* how many; the lifted row's place then */
    ui_list *list;
} E;

static const edit_row *row_of(int c) {
    for (int k = 0; k < E.nrows; ++k)
        if (E.rows[k].letter == c) return &E.rows[k];
    return 0;
}
static int mark_of(int c) {
    int at = find(E.def, (int)strlen(E.def), c);
    return at >= 0 ? E.marks[at] : 0;
}
static void save(void) {
    char full[ORDER_MAX], marks[ORDER_MAX];
    int n = order_read(E.file, E.def, full, marks), k = 0;
    for (int i = 0; i < n && k < E.n; ++i) /* the listed rows' places take them in their new order */
        if (find(E.shown, E.n, full[i]) >= 0) full[i] = E.shown[k++];
    order_write(E.file, E.def, full, E.marks);
}

static int count(void *ctx) {
    (void)ctx;
    return E.n;
}
static void row(void *ctx, int i, ui_row *out) {
    (void)ctx;
    const edit_row *r = row_of(E.shown[i]);
    int shows = r && order_shows(mark_of(E.shown[i]), r->rule);
    out->title = r ? r->title : "";
    out->flags = ROW_CIRCLE | ROW_GRIP | (shows ? ROW_ON : ROW_DIM) | (r && r->rule == EDIT_LOCKED ? ROW_LOCK : 0);
}
/* Centre: lift the selected row, or put the lifted one down and save */
static void chosen(void *ctx, int i) {
    (void)ctx;
    if (list_lifted(E.list)) {
        list_lift(E.list, 0);
        save();
        return;
    }
    memcpy(E.before, E.shown, sizeof E.before);
    E.from = i;
    list_lift(E.list, 1);
}
static void leave(void) {
    if (list_lifted(E.list)) list_lift(E.list, 0); /* a row still carried stays where it got to */
    save();
    ui_pop();
}
static void held(void *ctx, int i) {
    (void)ctx, (void)i;
    leave();
}
/* the lifted row a row on, the one it passes sliding aside */
static void carry(int dir) {
    int a = list_selected(E.list), b = a + dir;
    if (b < 0 || b >= E.n) return;
    char c = E.shown[a];
    E.shown[a] = E.shown[b], E.shown[b] = c;
    list_moved_row(E.list, a, b);
    ui_click(0);
}
/* Play/Pause: the selected row shown or hidden, the user's from now on, saved at once */
static void toggle(void) {
    int i = list_selected(E.list), at = find(E.def, (int)strlen(E.def), E.shown[i]);
    const edit_row *r = row_of(E.shown[i]);
    if (!r || at < 0 || r->rule == EDIT_LOCKED) return;
    E.marks[at] = order_shows(E.marks[at], r->rule) ? '-' : '+';
    save();
    list_reload(E.list);
}

static void *e_open(void *arg) {
    (void)arg;
    char full[ORDER_MAX];
    int n = order_read(E.file, E.def, full, E.marks);
    E.n = 0;
    for (int i = 0; i < n; ++i)
        if (row_of(full[i])) E.shown[E.n++] = full[i];
    ui_source src = { .count = count, .row = row, .chosen = chosen, .held = held,
                      .box = { 0, 58, SCREEN_W, EDIT_END - 58 }, .fade_to = EDIT_FADE };
    E.list = list_new(&src);
    player_line("playkey", "1"); /* Play/Pause is the editor's while it is open */
    ui_mark_all();
    return &E;
}
static void e_event(void *st, const input_t *e) {
    (void)st;
    if (e->type == IN_TICK && list_lifted(E.list)) {
        carry(e->value);
        return;
    }
    if (e->type == IN_KEY_UP && e->value == KEY_UI_PLAY) {
        if (!list_lifted(E.list)) toggle();
        return;
    }
    if (e->type == IN_KEY_UP && e->value == KEY_UI_RETURN) {
        if (!list_lifted(E.list)) {
            leave();
            return;
        }
        int at = list_selected(E.list); /* put back where it was, still in the editor */
        memcpy(E.shown, E.before, sizeof E.shown);
        list_moved_row(E.list, at, E.from);
        list_lift(E.list, 0);
        return;
    }
    list_event(E.list, e);
}
static void e_step(void *st) {
    (void)st;
    list_step(E.list);
}
static void e_draw(void *st, rect_t r) {
    (void)st;
    static run_t drag, rest;
    list_draw(E.list, r);
    rect_t band = { 0, EDIT_FADE, SCREEN_W, SCREEN_H - EDIT_FADE }, part;
    if (!rect_meet(r, band, &part)) return;
    clip_set(part);
    fill(part, tok(TOK_CANVAS));
    if (!drag.n || run_stale(&drag)) run_make(&drag, FACE_REGULAR, TYPE(13), "Drag", -1);
    if (!rest.n || run_stale(&rest))
        run_make(&rest, FACE_REGULAR, TYPE(13), " to move \xc2\xb7 Play/Pause shows or hides", -1);
    float x = ((float)SCREEN_W - drag.w - HINT_GLYPH - rest.w) / 2, y = HINT_Y - TYPE(13) / 2.0f;
    uint32_t ink = tok(TOK_TERTIARY);
    run_draw(&drag, x, y, ink, 256);
    for (int k = -1; k <= 1; ++k) /* the grip, as the hint's glyph */
        fill((rect_t){ (int)(x + drag.w) + 4, HINT_Y + 3 * k, 9, 1 }, ink);
    run_draw(&rest, x + drag.w + HINT_GLYPH, y, ink, 256);
}
static void e_close(void *st) {
    (void)st;
    player_line("playkey", "0");
    list_free(E.list);
    E.list = 0;
    if (E.done) E.done();
}
static void e_shown(void *st) {
    (void)st;
    ui_mark_all();
}
static const ui_page page_edit = { "edit", e_open, e_event, e_step, e_draw, e_close, e_shown, 0, 0 };

void edit_push(const char *def, const char *file, const edit_row *rows, int n, void (*done)(void)) {
    E.def = def, E.file = file, E.rows = rows, E.nrows = n, E.done = done;
    ui_push(&page_edit, 0);
}
