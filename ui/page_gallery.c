/* The toolkit's examples, for whoever builds on it (docs/ui-rewrite.md#the-toolkit) and for its
 * tests. `--page gallery` is a list holding every kind of row; `--page card` is a card of layers
 * that the wheel pushes. Neither is reached from a menu. */
#include <math.h>
#include <stdio.h>
#include <stdlib.h>

#include "q2ui.h"

/* ---- gallery: a list is a count, a row and what Centre does ---- */
static struct {
    int wifi, lights; /* the switches' state: a page keeps its own */
    int quality;
} G = { 1, 0, 1 };
static const char *const quality[] = { "Low", "High", "Lossless" };

static int g_count(void *ctx) {
    (void)ctx;
    return 12;
}
static void g_row(void *ctx, int i, ui_row *out) {
    (void)ctx;
    switch (i) {
    case 0: out->title = "Rows", out->flags = ROW_HEADER; break;
    case 1: out->title = "Opens a page", out->flags = ROW_CHEVRON; break;
    case 2: out->title = "A song", out->detail = "Its Artist"; break;
    case 3: out->title = "The playing song", out->detail = "Its Artist", out->flags = ROW_PLAYING; break;
    case 4: out->title = "Wi-Fi", out->flags = ROW_SWITCH | (G.wifi ? ROW_ON : 0); break;
    case 5: out->title = "Lights", out->flags = ROW_SWITCH | (G.lights ? ROW_ON : 0); break;
    case 6: out->title = "Quality", out->value = quality[G.quality]; break;
    case 7: out->title = "Not now", out->value = "Off", out->flags = ROW_GREY; break;
    case 8: out->title = "Choices", out->flags = ROW_HEADER; break;
    case 9: out->title = "The current one", out->flags = ROW_CHECK; break;
    case 10: out->title = "A title far too long for its row, which is cut with an ellipsis and slides when selected", out->flags = ROW_CHEVRON; break;
    default: out->title = "Shuffle", out->icon = icon("shuffle"), out->flags = ROW_ACCENT; break;
    }
}
static ui_list *g_list;
static void g_chosen(void *ctx, int i) {
    (void)ctx;
    if (i == 1) ui_push(&page_card, 0);
    if (i == 4) G.wifi = !G.wifi;
    if (i == 5) G.lights = !G.lights;
    if (i == 6) G.quality = (G.quality + 1) % 3;
    if (i >= 4 && i <= 6) list_reload(g_list);
}
static void *g_open(void *arg) {
    void *st = list_page.open(arg);
    g_list = list_page_list(st);
    return st;
}
static ui_page gallery;
void gallery_push(void) {
    ui_source src = { .count = g_count, .row = g_row, .chosen = g_chosen };
    gallery = list_page, gallery.name = "gallery", gallery.open = g_open;
    ui_push(&gallery, &src);
}

/* ---- card: a page with its own state, step and draw ---- */
struct card_st {
    spring_t push, lift;
    image_t *cover, *badge;
    uint64_t t;
    rect_t was;
};
#define CARD_BOX ((rectf_t){ 112, 84, 152, 152 })

/* a made-up cover: a diagonal ramp with rings, so a shift inside the frame is plain to see */
static image_t *cover_make(void) {
    image_t *im = image_new(200, 200);
    for (int y = 0; y < 200; ++y)
        for (int x = 0; x < 200; ++x) {
            float d = sqrtf((float)((x - 100) * (x - 100) + (y - 100) * (y - 100)));
            int ring = ((int)d / 14) & 1;
            uint32_t base = rgb_mix(0xd8402a, 0x2a2f8a, (float)(x + y) / 400);
            im->px[y * 200 + x] = 0xff000000 | rgb_mix(base, 0xffffff, ring ? 0.08f : 0);
        }
    return im;
}
static image_t *badge_make(void) {
    image_t *im = image_new(48, 48);
    for (int y = 0; y < 48; ++y)
        for (int x = 0; x < 48; ++x) {
            float d = sqrtf((float)((x - 24) * (x - 24) + (y - 24) * (y - 24)));
            unsigned a = d < 22 ? 255 : d < 23 ? (unsigned)((23 - d) * 255) : 0;
            im->px[y * 48 + x] = a << 24 | 0xffffff;
        }
    return im;
}
static void *c_open(void *arg) {
    (void)arg;
    struct card_st *s = calloc(1, sizeof *s);
    s->cover = cover_make(), s->badge = badge_make();
    s->lift.to = 1, s->t = ui_time();
    s->was = card_bounds(CARD_BOX);
    ui_again();
    return s;
}
static void c_event(void *st, const input_t *e) {
    struct card_st *s = st;
    if (e->type == IN_KEY_UP && e->value == KEY_UI_RETURN) {
        ui_pop();
        return;
    }
    if (e->type == IN_TUG) s->push.to = clampf(e->share, -1, 1);
    if (e->type == IN_TICK) s->push.v = e->value * 9.0;
    if (e->type == IN_TUG || e->type == IN_TICK) {
        if (!spring_moving(&s->push) || s->t == 0) s->t = ui_time() - fb_refresh_us();
        ui_again();
    }
}
static void c_step(void *st) {
    struct card_st *s = st;
    float dt = (float)(ui_time() - s->t) / 1e6f;
    s->t = ui_time();
    int moving = spring_step(&s->push, dt, 0.35f, 0.7f) | spring_step(&s->lift, dt, 0.4f, 0.8f);
    if (moving) ui_mark(s->was), ui_again();
}
static void c_draw(void *st, rect_t r) {
    const struct card_st *s = st;
    ui_layer layers[] = { { s->cover, 0, -6, 1.12f, 0 }, { s->badge, 0, 7, 0, 52 } };
    (void)r; /* the clip is r already: the ramp is the whole screen's, cut to it */
    fill_ramp((rect_t){ 0, 0, SCREEN_W, SCREEN_H }, 0x1a1a1e, 0x000000);
    card_draw(CARD_BOX, 10, layers, 2, (float)s->push.x, (float)s->lift.x);
}
static void c_close(void *st) {
    struct card_st *s = st;
    image_free(s->cover), image_free(s->badge);
    free(s);
}
const ui_page page_card = { .name = "card", .open = c_open, .event = c_event, .step = c_step, .draw = c_draw, .close = c_close };
