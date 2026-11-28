/* Home (docs/oled-design.md#homes-first-row, docs/ipod.md#home). Split: the list at the left, and
 * at the right the playing song's cover over the whole pane, a card whose picture moves inside
 * its frame as the selection moves (docs/ui-rewrite.md#cards); Full ([IPOD] HOME 1, demo's
 * setting): the list across the screen from under the status row, as every list. The Playing (or
 * Paused) row, there while something is, opens Now Playing. A row with a page of demo's named asks
 * demo for it (ui_demo_page): demo takes the screen for that page and gives it back when the page is
 * left. The rows stand in the user's order with the user's choice of them, demo's Edit Home file
 * (docs/internals.md#library-order: /mnt/data/ringnav-home-order against lr_home), which Centre held
 * edits (Edit Home, ui/page_edit.c), Settings always shown. The last row, Old Menus, q2ui's own and
 * in no order, leaves q2ui for demo's own menus. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "q2ui.h"

int home_split = 1; /* Home is laid out Split (status.c inks the glyphs over its pane) */

/* demo's Split (docs/oled-design.md, Screens: Home): the rows to x 204, their bar 16 to 188 and chevrons
 * at 176, and the art from x 196, under the rows' last columns, where they draw nothing */
#define LIST_W 204
#define PANE_X HOME_PANE_X
#define HOME_TOP 40 /* Home's rows start 18 px above a list page's, as demo's Home has them */
#define HOME_FOOT 311 /* seven rows whole, and the eighth fading into the screen's foot by here, as a list's (list.c) */
#define PANE ((rectf_t){ PANE_X, 0, SCREEN_W - PANE_X, SCREEN_H })

static const struct home_row {
    char letter; /* its letter in Edit Home's file (patch/library_edit.c lr_home); 0: q2ui's own, always last */
    const char *title, *demo; /* demo: the page of demo's that row opens; 0: a page of q2ui's */
} rows[] = { { 'p', "Playing", 0 },
             { 'l', "Library", 0 },
             { 't', "Radio", "tuner_page" },
             { 'g', "Games", "games_page" },
             { 'f', "Folders", "folder_page" },
             { 'c', "Cover Flow", "coverflow_page" },
             { 'q', "Search", "searchstrip_page" },
             { 'S', "Settings", 0 },
             { 0, "Old Menus", "" } };
/* Every row but Library and Settings is demo's, as demo's Home has them in this order: opened
 * through the gate by the payload's own openers, or by name for a stock page (patch/q2ui_gate.c
 * openers). Settings is q2ui's own list (page_settings.c), whose rows open demo's settings pages:
 * demo's is Home's own second list in Home's window, which the gate cannot leave on Return. Old
 * Menus, which demo has not, leaves q2ui. */
#define ROWS ((int)(sizeof rows / sizeof *rows))
#define PLAYING 0 /* rows' Playing row */
const char *home_order_file = "/mnt/data/ringnav-home-order";
static const char lr_home[] = "pltgfcqS";

struct home_st {
    ui_list *list;
    spring_t push;
    uint64_t t;
    unsigned seen, cover_seen;
    int state; /* 0 nothing queued, 1 playing, 2 paused */
    int full;  /* the Full layout */
    int shown[ROWS], n; /* rows' indexes in the order shown */
};
static struct home_st *home; /* the open Home's, for Edit Home's end */

static int state_now(void) { return now_get()->playing ? 1 : now_get()->paused ? 2 : 0; }
static int full_now(void) { return config_int("IPOD", "HOME", 0) == 1; } /* any other value is Split (docs/ipod.md#home) */
/* The rows as the file has them, those the user hides left out and Playing only while something is loaded */
static void h_order(struct home_st *s) {
    char out[ORDER_MAX], marks[ORDER_MAX];
    int n = order_read(home_order_file, lr_home, out, marks);
    s->n = 0;
    for (int i = 0; i < n; ++i)
        for (int k = 0; k < ROWS; ++k) {
            int rule = rows[k].letter == 'S' ? EDIT_LOCKED : EDIT_SHOWN;
            if (rows[k].letter == out[i] && order_shows(marks[strchr(lr_home, out[i]) - lr_home], rule) && (k != PLAYING || s->state))
                s->shown[s->n++] = k;
        }
    s->shown[s->n++] = ROWS - 1; /* Old Menus */
}
/* The order again (a row come or gone, an edit), the selection kept on its row, or where it was when its row went */
static void h_reorder(struct home_st *s) {
    int sel = list_selected(s->list), was = sel < s->n ? s->shown[sel] : -1;
    h_order(s);
    list_changed(s->list);
    for (int i = 0; i < s->n; ++i)
        if (s->shown[i] == was) sel = i;
    list_select(s->list, sel < s->n ? sel : s->n - 1, 0);
    ui_mark_all();
}
static int h_count(void *ctx) { return ((struct home_st *)ctx)->n; }
static void h_row(void *ctx, int i, ui_row *out) {
    struct home_st *s = ctx;
    int k = s->shown[i];
    out->flags = ROW_CHEVRON;
    out->title = k == PLAYING && s->state == 2 ? "Paused" : rows[k].title;
}
static void h_chosen(void *ctx, int i) {
    struct home_st *s = ctx;
    if (s->shown[i] == PLAYING) {
        ui_push(&page_now, 0);
        return;
    }
    const struct home_row *r = &rows[s->shown[i]];
    if (!r->demo && !strcmp(r->title, "Settings"))
        settings_push();
    else if (!r->demo)
        library_push();
    else if (*r->demo)
        ui_demo_page(r->demo);
    else
        ui_quit(0); /* demo's own menus, until demo next starts (docs/ui-rewrite.md#cutover) */
}
static void h_focus(void *ctx, int i) {
    struct home_st *s = ctx;
    (void)i;
    /* the selection moving pushes the cover the way it went, and it settles back */
    int dir = list_selected(s->list) > 0 ? 1 : -1;
    s->push.v += dir * 5.0;
    ui_again();
}
/* Edit Home ended: the rows as it left them */
static void h_edited(void) {
    if (home) h_reorder(home);
}
/* Centre held: Edit Home, on Home's rows but Old Menus, which is in no order */
static void h_held(void *ctx, int i) {
    (void)ctx, (void)i;
    static edit_row edit[ROWS - 1];
    for (int k = 0; k < ROWS - 1; ++k) edit[k] = (edit_row){ rows[k].letter, rows[k].title, rows[k].letter == 'S' ? EDIT_LOCKED : EDIT_SHOWN };
    edit_push(lr_home, home_order_file, edit, ROWS - 1, h_edited);
}
/* the list in the layout: Split's beside the pane from y 40, its eighth row fading into the foot; Full's as a list
 * page's, across the screen */
static void h_list(struct home_st *s) {
    ui_source src = { .count = h_count, .row = h_row, .chosen = h_chosen, .held = h_held, .focus = h_focus, .ctx = s,
                      .box = { 0, HOME_TOP, LIST_W, 7 * 32 }, .fade_to = HOME_FOOT };
    if (s->full) src.box = (rect_t){ 0, 58, SCREEN_W, 7 * 32 }, src.fade_to = 0;
    int sel = s->list ? list_selected(s->list) : 0;
    if (s->list) list_free(s->list);
    s->list = list_new(&src);
    list_select(s->list, sel, 0);
    home_split = !s->full;
}
static void *h_open(void *arg) {
    (void)arg;
    struct home_st *s = calloc(1, sizeof *s);
    s->state = state_now(), s->seen = now_seq(), s->t = ui_time(), s->full = full_now();
    h_order(s);
    h_list(s);
    home = s;
    ui_mark_all();
    return s;
}
static void h_event(void *st, const input_t *e) {
    struct home_st *s = st;
    if (e->type == IN_KEY_UP && e->value == KEY_UI_RETURN) {
        /* Return on Home is Now Playing while something plays, and does nothing with nothing
         * queued, as demo's (docs/ipod.md#home); the way out of q2ui is the Old Menus row */
        if (s->state) ui_push(&page_now, 0);
        return;
    }
    list_event(s->list, e);
}
static void h_step(void *st) {
    struct home_st *s = st;
    float dt = (float)(ui_time() - s->t) / 1e6f;
    s->t = ui_time();
    list_step(s->list);
    if (spring_step(&s->push, dt, 0.5f, 0.75f)) ui_mark(rect_of(PANE, 0)), ui_again();
    if (now_seq() != s->seen) {
        s->seen = now_seq();
        if (state_now() != s->state) {
            /* the row the selection was on keeps it when the Playing row comes or goes */
            s->state = state_now();
            h_reorder(s);
        }
    }
    if (full_now() != s->full) { /* demo's setting changed (q2ui's Display page, or demo's) */
        s->full = !s->full;
        h_list(s);
        ui_mark_all();
    }
    now_cover();
    if (now_cover_seq() != s->cover_seen) s->cover_seen = now_cover_seq(), ui_mark(rect_of(PANE, 0));
}
static void h_draw(void *st, rect_t r) {
    struct home_st *s = st;
    rect_t pane = rect_of(PANE, 0), part;
    fill(r, tok(TOK_CANVAS));
    list_draw(s->list, r); /* first: the pane goes over the ground it lays under its last columns */
    if (!s->full && rect_meet(r, pane, &part)) {
        const image_t *cover = now_cover();
        clip_set(part);
        if (cover) {
            /* the cover fills the pane's height, cut to its width, and moves inside it */
            float zoom = 1.10f, ph = (float)cover->h / zoom, pw = ph * PANE.w / PANE.h;
            float room = ((float)cover->h - ph) / 2, shift = clampf((float)-s->push.x * 6, -room, room);
            image_draw_part(cover, (rectf_t){ ((float)cover->w - pw) / 2, room + shift, pw, ph }, PANE, 0, 0, 256, -1);
        } else
            fill_ramp(pane, 0x2a2a2c, 0x161618);
        /* the pane's ends darken into the screen's, and its left edge into the list */
        shade_rows(0, 58, 64, 255);
        shade_rows(SCREEN_H - 40, SCREEN_H, 255, 40);
        shade_cols(PANE_X, PANE_X + 10, 150, 255);
    }
}
static void h_close(void *st) {
    struct home_st *s = st;
    if (home == s) home = 0, home_split = 1;
    list_free(s->list);
    free(s);
}
static void h_shown(void *st) {
    (void)st;
    ui_mark_all();
}
/* The selection kept by its row among Home's own (rows' index less one: Playing -1, Library 0), so a start that finds
 * a song playing, and so the Playing row, comes back on the same row */
static int h_where(void *st, char *name, size_t room) {
    struct home_st *s = st;
    snprintf(name, room, "home");
    return s->shown[list_selected(s->list)] - 1;
}
static void h_select(void *st, int sel) {
    struct home_st *s = st;
    for (int i = 0; i < s->n; ++i)
        if (s->shown[i] == sel + 1) list_select(s->list, i, 0);
}
const ui_page page_home = { "home", h_open, h_event, h_step, h_draw, h_close, h_shown, h_where, h_select };
