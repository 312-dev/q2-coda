/* Now Playing (docs/oled-design.md#now-playing): the cover as a card at the left, the title, artist
 * and album beside it, the progress bar and the times under them. Centre plays and pauses, the
 * wheel is the volume, Return leaves, and Centre held opens demo's More (its page of actions on the
 * song, which demo opens from the dots at the art's foot). While the volume changes, by the wheel here or the side
 * buttons, its level takes the bar's place for VOLUME_SHOW_US, white on the same track, and the
 * times stand aside (demo shows its own volume window then, which q2ui's page is over). The cover is a card of layers (docs/ui-rewrite.md#cards):
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
#define VOLUME_SHOW_US 1500000u
/* a title too long for its column: at rest for MQ_REST_US, then slid left at MQ_SPEED until its end
 * meets the column's, held MQ_END_US, and back (docs/oled-design.md#motion) */
#define MQ_REST_US 2750000u
#define MQ_END_US 1500000u
#define MQ_SPEED 35.0f
#define MQ_FADE 12
#define TITLE_Y 64
#define MODE_Y 286 /* the modes row's glyphs' centre */
/* a lit shuffle or repeat's dot: DOT px, its centre DOT_DY under the glyph's (patch/np_bar.c NP_DOT, NP_DOT_DY) */
#define DOT 4
#define DOT_DY 15
/* More's dots: the glyph's centre, at the right of the art's foot as demo's (tools/ipod.py np_grid, img_more) */
#define MORE_X 340
#define MORE_Y 203

struct now_st {
    spring_t push, lift;
    uint64_t t;
    unsigned seen, cover_seen;
    int pos_s, volume;   /* volume: the level last shown, -1 before the first read */
    uint64_t volume_at;  /* when it last changed, 0: the bar is the song's */
    run_t title, artist, album, left, right, whole; /* whole: the title uncut, when it is too long */
    uint64_t mq_at;                               /* when the title's turn began, 0: it fits */
    float mq_x;
    char title_s[160], artist_s[160], album_s[160];
    int marks_s; /* the modes, the hearts and whether a song is loaded, as last drawn */
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
    s->seen = ~0u, s->t = ui_time() - fb_refresh_us(), s->volume = -1;
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
    } else if (e->type == IN_KEY_HOLD && e->value == KEY_UI_CENTRE && *now_get()->title) {
        ui_demo_page("playermore_page"); /* a dialog over demo's page under q2ui (patch/onlinematch.c) */
    } else if (e->type == IN_TICK) {
        char verb[24];
        /* a tick down the wheel is quieter, as on a list it is further down */
        int to = (s->volume < 0 ? now_get()->volume : s->volume) - e->value;
        to = to < 0 ? 0 : to > 100 ? 100 : to;
        snprintf(verb, sizeof verb, "volume %d", to);
        player_send(verb);
        /* shown at once: the player's answer comes a moment later and says the same */
        s->volume = to, s->volume_at = ui_time();
        ui_mark((rect_t){ 0, BAR_Y - 6, SCREEN_W, 44 });
        ui_wake_ms(VOLUME_SHOW_US / 1000u);
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
            s->title.n = s->artist.n = s->album.n = s->whole.n = 0;
            s->mq_at = 0, s->mq_x = 0;
            ui_mark((rect_t){ TEXT_X, 58, SCREEN_W - TEXT_X, 100 });
        }
        ui_mark((rect_t){ 0, BAR_Y - 2, SCREEN_W, 40 });
        /* the modes and the heart, set from elsewhere too (the car, the phone, demo's own page) */
        int marks = (n->shuffle > 0) | (n->repeat & 3) << 1 | (n->hearts + 1) << 3 | !*n->title << 12;
        if (marks != s->marks_s) {
            s->marks_s = marks;
            ui_mark((rect_t){ 0, MODE_Y - 20, SCREEN_W, SCREEN_H - (MODE_Y - 20) });
            ui_mark((rect_t){ TEXT_X, 140, SCREEN_W - TEXT_X, 76 }); /* the heart and More */
        }
        if (s->volume < 0)
            s->volume = n->volume; /* the level the page opened at: nothing changed */
        else if (n->volume != s->volume && ui_time() - s->volume_at > 600000u) {
            /* changed from elsewhere (the side buttons); a wheel tick's own echo arrives within the 600 ms */
            s->volume = n->volume, s->volume_at = ui_time();
            ui_mark((rect_t){ 0, BAR_Y - 6, SCREEN_W, 44 });
            ui_wake_ms(VOLUME_SHOW_US / 1000u);
        }
    }
    if (s->volume_at && ui_time() - s->volume_at >= VOLUME_SHOW_US) s->volume_at = 0, ui_mark((rect_t){ 0, BAR_Y - 6, SCREEN_W, 44 });
    if (s->title.n && s->title.cut && !s->mq_at) s->mq_at = ui_time(); /* laid out and found too long */
    if (s->mq_at && s->whole.n) {
        float over = s->whole.w - (float)(SCREEN_W - TEXT_X - 24), x = 0;
        uint64_t t = ui_time() - s->mq_at, slide = over > 0 ? (uint64_t)(over / MQ_SPEED * 1e6f) : 0;
        if (t < MQ_REST_US)
            ui_wake_ms((unsigned)((MQ_REST_US - t) / 1000u) + 1);
        else if (t < MQ_REST_US + slide)
            x = (float)(t - MQ_REST_US) / 1e6f * MQ_SPEED, ui_again();
        else if (t < MQ_REST_US + slide + MQ_END_US)
            x = over, ui_wake_ms((unsigned)((MQ_REST_US + slide + MQ_END_US - t) / 1000u) + 1);
        else
            s->mq_at = ui_time(), ui_wake_ms(MQ_REST_US / 1000u);
        if (x != s->mq_x) s->mq_x = x, ui_mark((rect_t){ TEXT_X, TITLE_Y, SCREEN_W - TEXT_X, 30 });
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
    if (s->title.cut) {
        /* the title whole, slid in its column with its ends faded while it is away from them */
        if (!s->whole.n) run_make(&s->whole, FACE_MEDIUM, TYPE(22), n->title, -1);
        fade_set(TEXT_X, TEXT_X + room, s->mq_x > 0.5f ? MQ_FADE : 0, s->whole.w - s->mq_x > (float)room + 0.5f ? MQ_FADE : 0);
        run_draw(&s->whole, TEXT_X - s->mq_x, TITLE_Y, tok(TOK_PRIMARY), 256);
        fade_clear();
    } else
        run_draw(&s->title, TEXT_X, TITLE_Y, tok(TOK_PRIMARY), 256);
    if (*n->artist) run_draw(&s->artist, TEXT_X, 96, tok(TOK_SECONDARY), 256);
    if (*n->album) run_draw(&s->album, TEXT_X, 120, tok(TOK_SECONDARY), 256);
    /* the row of modes at the foot, as marks of what is set: shuffle, the song's radio, repeat, Up
     * Next, each in the theme's own ink for its state (the accent when on), a lit shuffle or repeat
     * with the accent's dot under it as demo's. Repeat is demo's file's: 0 off, 1 one, 2 all
     * (patch/coda_rc_shim.c cr_modes). They are not pressed from here yet: the wheel is the volume
     * and Centre plays. */
    static const float mode_x[] = { 64, 146, 228, 311 };
    const char *mode[] = { n->shuffle > 0 ? "mode_shuffle_on." : "mode_shuffle_d.", "mode_radio_d.",
                           n->repeat == 1 ? "mode_repeat1_on." : n->repeat > 0 ? "mode_repeat_on." : "mode_repeat_d.", "mode_queue_d." };
    const int lit[] = { n->shuffle > 0, 0, n->repeat > 0, 0 };
    for (int k = 0; k < 4; ++k) {
        const image_t *im = icon(mode[k]);
        if (im) image_draw(im, (rectf_t){ mode_x[k] - (float)im->w / 2, MODE_Y - (float)im->h / 2, (float)im->w, (float)im->h }, 0, 0, 256, -1);
        if (lit[k]) fill_round((rectf_t){ mode_x[k] - DOT / 2, MODE_Y + DOT_DY - DOT / 2, DOT, DOT }, DOT / 2, 0, RGB_ACCENT, 1, 0);
    }
    /* liked, and not: the image's box at y 146 puts its ink's top on demo's, y 155 (ui/tools/compare.py on the
     * emulator's shots of the same song, m17c-04 and -09) */
    const image_t *heart = icon(n->hearts > 0 ? "np_fav_d." : "np_unfav_d.");
    if (heart && *n->title) image_draw(heart, (rectf_t){ 194, 146, (float)heart->w, (float)heart->h }, 0, 0, 256, -1);
    const image_t *more = icon("np_more_d.");
    if (more && *n->title)
        image_draw(more, (rectf_t){ MORE_X - (float)more->w / 2, MORE_Y - (float)more->h / 2, (float)more->w, (float)more->h }, 0, 0, 256, -1);
    if (s->volume_at) {
        /* the volume's level in the bar's place: a shorter track between two marks, the level in white */
        float level = (float)s->volume / 100, x0 = BAR_X + 22, w = BAR_W - 44;
        fill_round((rectf_t){ x0, BAR_Y, w, BAR_H }, 2, 0, tok(TOK_TRACK), 1, 0);
        if (level > 0) fill_round((rectf_t){ x0, BAR_Y, BAR_H + (w - BAR_H) * level, BAR_H }, 2, 0, tok(TOK_PRIMARY), 1, 0);
        fill_disc(BAR_X + 5, BAR_Y + 2, 3, tok(TOK_SECONDARY), 1);                 /* quiet */
        fill_disc(BAR_X + BAR_W - 7, BAR_Y + 2, 5, tok(TOK_SECONDARY), 1);          /* loud */
        return;
    }
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
