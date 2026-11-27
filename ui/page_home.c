/* Home (docs/oled-design.md#homes-first-row, docs/ipod.md#home), Split: the list at the left, and
 * at the right the playing song's cover over the whole pane, a card whose picture moves inside
 * its frame as the selection moves (docs/ui-rewrite.md#cards). The first row is Playing or Paused
 * while something is, and opens Now Playing. A row with a page of demo's named asks demo for it
 * (ui_demo_page): demo takes the screen for that page and gives it back when the page is left.
 * The last row, Old Menus, leaves q2ui for demo's own menus. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "q2ui.h"

#define LIST_W 196
#define HOME_TOP 40 /* Home's rows start 18 px above a list page's, as demo's Home has them */
#define PANE ((rectf_t){ LIST_W, 0, SCREEN_W - LIST_W, SCREEN_H })

static const struct home_row {
    const char *title, *demo; /* demo: the page of demo's that row opens; 0: a page of q2ui's */
} rows[] = { { "Library", 0 },                  { "Radio", "tuner_page" },     { "Games", "games_page" },
             { "Folders", "folder_page" },      { "Cover Flow", "coverflow_page" }, { "Search", "searchstrip_page" },
             { "Settings", 0 },                 { "Old Menus", "" } };
/* Every row but Library and Settings is demo's, as demo's Home has them in this order: opened
 * through the gate by the payload's own openers, or by name for a stock page (patch/q2ui_gate.c
 * openers). Settings is q2ui's own list (page_settings.c), whose rows open demo's settings pages:
 * demo's is Home's own second list in Home's window, which the gate cannot leave on Return. Old
 * Menus, which demo has not, leaves q2ui. */
#define ROWS ((int)(sizeof rows / sizeof *rows))

struct home_st {
    ui_list *list;
    spring_t push;
    uint64_t t;
    unsigned seen, cover_seen;
    int state; /* 0 nothing queued, 1 playing, 2 paused */
};

static int state_now(void) { return now_get()->playing ? 1 : now_get()->paused ? 2 : 0; }
static int h_count(void *ctx) { return ROWS + (((struct home_st *)ctx)->state != 0); }
static void h_row(void *ctx, int i, ui_row *out) {
    struct home_st *s = ctx;
    out->flags = ROW_CHEVRON;
    if (s->state && !i) {
        out->title = s->state == 1 ? "Playing" : "Paused";
        return;
    }
    out->title = rows[i - (s->state != 0)].title;
}
static void h_chosen(void *ctx, int i) {
    struct home_st *s = ctx;
    if (s->state && !i) {
        ui_push(&page_now, 0);
        return;
    }
    const struct home_row *r = &rows[i - (s->state != 0)];
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
static void *h_open(void *arg) {
    (void)arg;
    struct home_st *s = calloc(1, sizeof *s);
    s->state = state_now(), s->seen = now_seq(), s->t = ui_time();
    ui_source src = { .count = h_count, .row = h_row, .chosen = h_chosen, .focus = h_focus, .ctx = s,
                      .box = { 0, HOME_TOP, LIST_W, 7 * 32 } };
    s->list = list_new(&src);
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
            int had = s->state != 0, sel = list_selected(s->list);
            s->state = state_now();
            list_changed(s->list);
            /* the row the selection was on keeps it when the first row comes or goes */
            if ((s->state != 0) != had) list_select(s->list, sel + (s->state ? 1 : -1), 0);
            ui_mark_all();
        }
    }
    now_cover();
    if (now_cover_seq() != s->cover_seen) s->cover_seen = now_cover_seq(), ui_mark(rect_of(PANE, 0));
}
static void h_draw(void *st, rect_t r) {
    struct home_st *s = st;
    rect_t pane = rect_of(PANE, 0), part;
    fill(r, tok(TOK_CANVAS));
    if (rect_meet(r, pane, &part)) {
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
        shade_cols(LIST_W, LIST_W + 10, 150, 255);
    }
    clip_set(r);
    list_draw(s->list, r);
    clip_set(r);
}
static void h_close(void *st) {
    struct home_st *s = st;
    list_free(s->list);
    free(s);
}
static void h_shown(void *st) {
    (void)st;
    ui_mark_all();
}
static int h_where(void *st, char *name, size_t room) {
    snprintf(name, room, "home");
    return list_selected(((struct home_st *)st)->list);
}
static void h_select(void *st, int sel) { list_select(((struct home_st *)st)->list, sel, 0); }
const ui_page page_home = { "home", h_open, h_event, h_step, h_draw, h_close, h_shown, h_where, h_select };
