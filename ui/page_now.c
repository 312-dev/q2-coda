/* Now Playing (docs/oled-design.md#now-playing): the cover as a card at the left, the title, artist
 * and album beside it, the progress bar and the times under them. Centre plays and pauses, the
 * wheel is the volume, Return leaves. The cover is a card of layers (docs/ui-rewrite.md#cards):
 * it lifts as the page opens, settles smaller on pause, and each wheel tick pushes it. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "q2ui.h"

#define ART ((rectf_t){ 24, 58, 150, 150 })
#define TEXT_X 192
#define BAR_X 24
#define BAR_Y 229
#define BAR_W (SCREEN_W - 48)
#define BAR_H 4
#define TIMES_Y 240

struct now_st {
    spring_t push, lift;
    uint64_t t;
    unsigned seen, cover_seen;
    int pos_s, volume;
    run_t title, artist, album, left, right;
    char title_s[160], artist_s[160], album_s[160];
};

static int position_ms(void) {
    const ui_now *n = now_get();
    int pos = n->pos_ms;
    /* between demo's writes of it the song goes on by the clock */
    if (n->playing) pos += (int)((mono_us() - n->read_us) / 1000u);
    if (n->len_s && pos > n->len_s * 1000) pos = n->len_s * 1000;
    return pos;
}
static void *n_open(void *arg) {
    (void)arg;
    struct now_st *s = calloc(1, sizeof *s);
    s->seen = ~0u, s->t = ui_time() - fb_refresh_us();
    s->lift.to = now_get()->paused ? 0 : 1;
    ui_again();
    return s;
}
static void n_event(void *st, const input_t *e) {
    struct now_st *s = st;
    if (e->type == IN_KEY_UP && e->value == KEY_UI_RETURN) {
        ui_pop();
    } else if (e->type == IN_KEY_UP && e->value == KEY_UI_CENTRE) {
        player_send("toggle");
    } else if (e->type == IN_TICK) {
        char verb[24];
        /* a tick down the wheel is quieter, as on a list it is further down */
        s->volume = now_get()->volume - e->value;
        s->volume = s->volume < 0 ? 0 : s->volume > 100 ? 100 : s->volume;
        snprintf(verb, sizeof verb, "volume %d", s->volume);
        player_send(verb);
        s->push.v = e->value * 7.0;
        ui_again();
    } else if (e->type == IN_TUG) {
        s->push.to = clampf(e->share, -1, 1) * 0.6f;
        ui_again();
    }
}
static void n_step(void *st) {
    struct now_st *s = st;
    const ui_now *n = now_get();
    float dt = (float)(ui_time() - s->t) / 1e6f;
    s->t = ui_time();
    s->lift.to = n->paused ? 0 : 1; /* the art settles on pause (docs/oled-design.md#motion) */
    if (spring_step(&s->push, dt, 0.35f, 0.7f) | spring_step(&s->lift, dt, 0.32f, 1)) ui_mark(card_bounds(ART)), ui_again();
    if (now_seq() != s->seen) {
        s->seen = now_seq();
        if (strcmp(s->title_s, n->title) || strcmp(s->artist_s, n->artist) || strcmp(s->album_s, n->album)) {
            snprintf(s->title_s, sizeof s->title_s, "%s", n->title);
            snprintf(s->artist_s, sizeof s->artist_s, "%s", n->artist);
            snprintf(s->album_s, sizeof s->album_s, "%s", n->album);
            s->title.n = s->artist.n = s->album.n = 0;
            ui_mark((rect_t){ TEXT_X, 58, SCREEN_W - TEXT_X, 100 });
        }
        ui_mark((rect_t){ 0, BAR_Y - 2, SCREEN_W, 40 });
    }
    now_cover();
    if (now_cover_seq() != s->cover_seen) s->cover_seen = now_cover_seq(), ui_mark(card_bounds(ART));
    int pos = position_ms() / 1000;
    if (pos != s->pos_s) s->pos_s = pos, ui_mark((rect_t){ 0, BAR_Y - 2, SCREEN_W, 40 });
    if (n->playing) ui_wake_ms(250); /* the bar's next pixel and the times' next second */
}
static void clock_text(char *out, size_t room, int s, int minus) {
    snprintf(out, room, "%s%d:%02d", minus ? "-" : "", s / 60, s % 60);
}
static void n_draw(void *st, rect_t r) {
    struct now_st *s = st;
    const ui_now *n = now_get();
    const image_t *cover = now_cover();
    fill(r, tok(TOK_CANVAS));
    ui_layer layers[] = { { cover, tok(TOK_RAISED), -5, 1.10f, 0 } };
    /* paused, the art is 88% of its size about its middle; the card's lift takes it the rest */
    float small = 0.06f * (1 - (float)s->lift.x);
    rectf_t art = { ART.x + ART.w * small, ART.y + ART.h * small, ART.w * (1 - 2 * small), ART.h * (1 - 2 * small) };
    card_draw(art, 6, layers, 1, (float)s->push.x, 0);
    int room = SCREEN_W - TEXT_X - 24;
    if (!s->title.n) run_make(&s->title, FACE_MEDIUM, TYPE(22), *n->title ? n->title : "Not Playing", room);
    if (!s->artist.n && *n->artist) run_make(&s->artist, FACE_REGULAR, TYPE(18), n->artist, room);
    if (!s->album.n && *n->album) run_make(&s->album, FACE_REGULAR, TYPE(18), n->album, room);
    run_draw(&s->title, TEXT_X, 64, tok(TOK_PRIMARY), 256);
    if (*n->artist) run_draw(&s->artist, TEXT_X, 96, tok(TOK_SECONDARY), 256);
    if (*n->album) run_draw(&s->album, TEXT_X, 120, tok(TOK_SECONDARY), 256);
    /* the bar: its fill ends between pixels, so it creeps and never steps */
    int pos = position_ms();
    float share = n->len_s ? clampf((float)pos / ((float)n->len_s * 1000), 0, 1) : 0;
    fill_round((rectf_t){ BAR_X, BAR_Y, BAR_W, BAR_H }, 2, 0, tok(TOK_TRACK), 1, 0);
    if (share > 0) fill_round((rectf_t){ BAR_X, BAR_Y, BAR_H + (BAR_W - BAR_H) * share, BAR_H }, 2, 0, RGB_ACCENT, 1, 0);
    char text[16];
    clock_text(text, sizeof text, pos / 1000, 0);
    run_make(&s->left, FACE_REGULAR, TYPE(16), text, -1);
    run_draw(&s->left, BAR_X, TIMES_Y, tok(TOK_PRIMARY), 256);
    if (n->len_s) {
        clock_text(text, sizeof text, n->len_s - pos / 1000, 1);
        run_make(&s->right, FACE_REGULAR, TYPE(16), text, -1);
        run_draw(&s->right, BAR_X + BAR_W - s->right.w, TIMES_Y, tok(TOK_PRIMARY), 256);
    }
}
static void n_close(void *st) { free(st); }
static int n_where(void *st, char *name, size_t room) {
    (void)st;
    snprintf(name, room, "now");
    return -1;
}
const ui_page page_now = { "now", n_open, n_event, n_step, n_draw, n_close, 0, n_where, 0 };
