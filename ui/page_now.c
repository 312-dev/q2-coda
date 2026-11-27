/* Now Playing (docs/oled-design.md#now-playing): the cover as a card at the left, the title, artist
 * and album beside it, the progress bar and the times under them. The wheel is the volume, Return
 * leaves, and Centre held opens demo's More (its page of actions on the song, which demo opens from
 * the dots at the art's foot). Centre is demo's iPod Centre cycle (patch/nav/now_playing.c
 * np_centre, docs/internals.md#now-playing-ipod): a press starts the scrub (the wheel moves the
 * place by SCRUB_STEP seconds a tick, more on a fast spin, a playing song following it at most
 * every SCRUB_SEEK_MS); the next ends it there and rings Shuffle; the wheel walks the ring over
 * Shuffle, Repeat and the heart (Shuffle and Repeat not while a radio plays: its order is the
 * radio's); Centre steps the ringed one, shuffle on or off, repeat Off, All, One (down the command
 * queue, "mode SHUFFLE REPEAT", as Car Remote sets them); STAGE_US without a key, or Return, ends
 * either. Play and pause are the Play/Pause key's, which stays demo's. While the volume changes, by
 * the wheel here or the side buttons, its level takes the bar's place for VOLUME_SHOW_US, white on
 * the same track, and the times stand aside (demo shows its own volume window then, which q2ui's
 * page is over). The cover is a card of layers (docs/ui-rewrite.md#cards): it lifts as the page
 * opens, settles smaller on pause, and each wheel tick pushes it. */
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
#define VOL_INSET 24 /* patch/offsets.inc VOL_SPK_INSET, VOL_SPK_X, VOL_SPK_PX */
#define VOL_SPK_X 8
#define VOL_SPK 16
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
/* a lit shuffle or repeat's dot: DOT px, its centre DOT_DY under the glyph's (patch/np_bar.c
 * NP_DOT, NP_DOT_DY) */
#define DOT 4
#define DOT_DY 15
/* More's dots: the glyph's centre, at the right of the art's foot as demo's (tools/ipod.py np_grid,
 * img_more) */
#define MORE_X 340
#define MORE_Y 203
/* the Centre cycle (patch/nav/now_playing.c, patch/offsets.inc, patch/navigation.c ramp,
 * patch/seek.c) */
#define SCRUB_STEP 5 /* seconds a tick */
#define STAGE_US                                                                                   \
    3000000u           /* SCRUB_MS, NP_STAGE_MS: a scrub or a ring with no key for this long ends */
#define RAMP_FIRST 100 /* WHEEL_RAMP_MS: one step for this long of a spin, then one more each */
#define RAMP_RUN 140   /* WHEEL_RUN_MS: a gap longer than this starts the spin again */
#define RAMP_MAX 8     /* WHEEL_MAX_STEP */
#define SCRUB_SEEK_US                                                                              \
    200000u        /* SCRUB_SEEK_MS: a playing song follows the scrub at most this often */
#define KNOB 14    /* the scrub's knob on the fill's end (patch/np_bar.c NP_KNOB) */
#define RING_PAD 6 /* the ring's inner edge from the glyph's ink and its on dot */
#define RING_W 2
#define RING_R 9
enum { STAGE_REST, STAGE_SHUFFLE, STAGE_REPEAT, STAGE_HEART };
/* the radio's pill (patch/np_bar.c np_modes): its box, the row's ends, its grow and the word's fade
 */
#define PILL_W 92
#define PILL_H 34
#define ROW_LEFT 53
#define ROW_RIGHT 322
#define PILL_US 200000u
#define MODE_BOX 22 /* a mode's glyph's box (tools/art.py MODE_PX), the row's spacing's unit */

struct now_st {
    spring_t push, lift;
    uint64_t t;
    unsigned seen, cover_seen;
    int pos_s, volume;  /* volume: the level last shown, -1 before the first read */
    uint64_t volume_at; /* when it last changed, 0: the bar is the song's */
    run_t title, artist, album, left, right,
        whole;      /* whole: the title uncut, when it is too long */
    uint64_t mq_at; /* when the title's turn began, 0: it fits */
    float mq_x;
    char title_s[160], artist_s[160], album_s[160];
    int marks_s; /* the modes, the hearts and whether a song is loaded, as last drawn */
    /* the Centre cycle: scrubbing, its place (s), whether the wheel moved it, the song it began on;
     * the ring's stage; when either last had a key (their timeout); the spin's run (ramp) and the
     * last seek sent */
    int scrub, scrub_to, scrub_moved, scrub_id, stage;
    uint64_t key_at, seek_at;
    unsigned run, last_tick_ms;
    int run_dir;
    /* the radio's pill: on as last seen, and when it turned */
    int radio;
    uint64_t radio_at;
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
    s->seen = ~0u, s->t = ui_time() - fb_refresh_us(), s->volume = -1, s->radio = now_get()->radio;
    s->lift.to = now_get()->paused ? 0 : 1;
    ui_again();
    return s;
}
static void cycle_mark(void) {
    ui_mark((rect_t){ 0, BAR_Y - KNOB, SCREEN_W, 44 + KNOB });
    ui_mark((rect_t){ 0, MODE_Y - 30, SCREEN_W, SCREEN_H - (MODE_Y - 30) });
    ui_mark((rect_t){ TEXT_X - 10, 130, SCREEN_W - TEXT_X + 10, 60 }); /* the heart's ring */
}
/* Whether the ring can stand on stage (np_stop_ok): Shuffle and Repeat with no radio, the heart
 * while it shows. */
static int stop_ok(int stage) {
    const ui_now *n = now_get();
    if (stage == STAGE_HEART) return *n->title != 0;
    return (stage == STAGE_SHUFFLE || stage == STAGE_REPEAT) && !n->radio;
}
/* The stop dir (1 or -1) from stage the ring can stand on, stage itself at an end (np_stop_next).
 */
static int stop_next(int stage, int dir) {
    for (int k = stage + dir; k >= STAGE_SHUFFLE && k <= STAGE_HEART; k += dir)
        if (stop_ok(k)) return k;
    return stage;
}
static void seek_to(int sec) {
    char verb[24];
    snprintf(verb, sizeof verb, "seek %d", sec);
    player_send(verb);
}
/* The scrub over: its place sought once more when the wheel moved it on the song it began on
 * (scrub_end). */
static void scrub_end(struct now_st *s) {
    if (!s->scrub) return;
    s->scrub = 0;
    if (s->scrub_moved && s->scrub_id == now_get()->id) seek_to(s->scrub_to);
    s->scrub_moved = 0;
    cycle_mark();
}
static void stage_set(struct now_st *s, int stage) {
    s->stage = stage;
    s->key_at = ui_time();
    if (stage != STAGE_REST) ui_wake_ms(STAGE_US / 1000u);
    cycle_mark();
}
/* The scrub's step for this tick: 1 until a run of same-way ticks has spun RAMP_FIRST ms, then 2
 * and one more each RAMP_FIRST ms, up to RAMP_MAX (patch/navigation.c ramp) */
static int ramp(struct now_st *s, int dir, unsigned ms) {
    unsigned gap = ms - s->last_tick_ms;
    if (dir == s->run_dir && s->run && gap <= RAMP_RUN) {
        unsigned top = RAMP_FIRST + (RAMP_MAX - 2) * RAMP_FIRST + 1;
        s->run = s->run + gap > top ? top : s->run + gap;
    } else
        s->run = 1;
    s->last_tick_ms = ms, s->run_dir = dir;
    int spin = (int)s->run - 1;
    return spin < RAMP_FIRST ? 1 : 2 + (spin - RAMP_FIRST) / RAMP_FIRST;
}
/* Centre, demo's cycle (np_centre) */
static void centre(struct now_st *s) {
    const ui_now *n = now_get();
    char verb[24];
    if (s->scrub) {
        scrub_end(s);
        stage_set(s, stop_next(STAGE_REST, 1));
    } else if (s->stage == STAGE_REST) {
        if (!*n->title || !n->len_s) return;
        s->scrub = 1, s->scrub_moved = 0, s->scrub_id = n->id, s->scrub_to = position_ms() / 1000;
        s->key_at = ui_time();
        ui_wake_ms(STAGE_US / 1000u);
        cycle_mark();
    } else if (!stop_ok(s->stage)) {
        stage_set(s, STAGE_REST);
    } else if (s->stage == STAGE_SHUFFLE || s->stage == STAGE_REPEAT) {
        /* shuffle on or off, repeat a step Off, All, One (the file's 0, 2, 1), the other left (-1)
         */
        if (s->stage == STAGE_SHUFFLE)
            snprintf(verb, sizeof verb, "mode %d -1", n->shuffle > 0 ? 0 : 1);
        else
            snprintf(verb, sizeof verb, "mode -1 %d", n->repeat == 0 ? 2 : n->repeat == 2 ? 1 : 0);
        player_send(verb);
        stage_set(s, s->stage); /* its timeout from this press */
    } else
        stage_set(s, s->stage); /* ponytail: the heart's like needs a verb of demo's (ratings.c
                                   rate_fav_click) */
}
static void n_event(void *st, const input_t *e) {
    struct now_st *s = st;
    if (e->type == IN_KEY_UP && e->value == KEY_UI_RETURN) {
        if (s->scrub || s->stage != STAGE_REST) { /* np_cancel: the cycle ends, the page stays */
            scrub_end(s);
            stage_set(s, STAGE_REST);
        } else
            ui_pop();
    } else if (e->type == IN_KEY_UP && e->value == KEY_UI_CENTRE) {
        centre(s);
        ui_click(1);
    } else if (e->type == IN_TICK && s->stage != STAGE_REST && !s->scrub) {
        int to = stop_next(s->stage, e->value > 0 ? 1 : -1);
        if (to != s->stage) ui_click(0);
        stage_set(s, to); /* its timeout from here */
    } else if (e->type == IN_TICK && s->scrub) {
        const ui_now *n = now_get();
        if (n->id != s->scrub_id) { /* a new song: the last one's place is dropped */
            scrub_end(s);
            return;
        }
        int dir = e->value > 0 ? 1 : -1, to = s->scrub_to + dir * SCRUB_STEP * ramp(s, dir, e->ms);
        to = to < 0 ? 0 : to > n->len_s ? n->len_s : to;
        if (to != s->scrub_to) s->scrub_moved = 1, ui_click(0);
        s->scrub_to = to, s->key_at = ui_time();
        ui_wake_ms(STAGE_US / 1000u);
        /* heard as it moves while the song plays, at most every SCRUB_SEEK_US (patch/seek.c
         * seek_scrub) */
        if (s->scrub_moved && n->playing && ui_time() - s->seek_at >= SCRUB_SEEK_US)
            seek_to(to), s->seek_at = ui_time();
        cycle_mark();
    } else if (e->type == IN_KEY_HOLD && e->value == KEY_UI_CENTRE && *now_get()->title) {
        ui_demo_page(
            "playermore_page"); /* a dialog over demo's page under q2ui (patch/onlinematch.c) */
    } else if (e->type == IN_TICK) {
        char verb[24];
        /* clockwise (a tick down a list) is louder, as on demo's Now Playing (patch/volume.c
         * vol_next) */
        int to = (s->volume < 0 ? now_get()->volume : s->volume) + e->value;
        to = to < 0 ? 0 : to > 100 ? 100 : to;
        snprintf(verb, sizeof verb, "volume %d", to);
        player_send(verb);
        ui_click(0); /* each tick, as stock's volume clicks on every key */
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
    if (spring_step(&s->push, dt, 0.35f, 0.7f) | spring_step(&s->lift, dt, 0.32f, 1))
        ui_mark(card_bounds(ART)), ui_again();
    if (now_seq() != s->seen) {
        s->seen = now_seq();
        if (strcmp(s->title_s, n->title) || strcmp(s->artist_s, n->artist) ||
            strcmp(s->album_s, n->album)) {
            snprintf(s->title_s, sizeof s->title_s, "%s", n->title);
            snprintf(s->artist_s, sizeof s->artist_s, "%s", n->artist);
            snprintf(s->album_s, sizeof s->album_s, "%s", n->album);
            s->title.n = s->artist.n = s->album.n = s->whole.n = 0;
            s->mq_at = 0, s->mq_x = 0;
            ui_mark((rect_t){ TEXT_X, 58, SCREEN_W - TEXT_X, 100 });
        }
        ui_mark((rect_t){ 0, BAR_Y - 2, SCREEN_W, 40 });
        /* the modes and the heart, set from elsewhere too (the car, the phone, demo's own page) */
        int marks =
            (n->shuffle > 0) | (n->repeat & 3) << 1 | (n->hearts + 1) << 3 | !*n->title << 12;
        if (marks != s->marks_s) {
            s->marks_s = marks;
            ui_mark((rect_t){ 0, MODE_Y - 20, SCREEN_W, SCREEN_H - (MODE_Y - 20) });
            ui_mark((rect_t){ TEXT_X, 140, SCREEN_W - TEXT_X, 76 }); /* the heart and More */
        }
        if (s->volume < 0)
            s->volume = n->volume; /* the level the page opened at: nothing changed */
        else if (n->volume != s->volume && ui_time() - s->volume_at > 600000u) {
            /* changed from elsewhere (the side buttons); a wheel tick's own echo arrives within the
             * 600 ms */
            s->volume = n->volume, s->volume_at = ui_time();
            ui_mark((rect_t){ 0, BAR_Y - 6, SCREEN_W, 44 });
            ui_wake_ms(VOLUME_SHOW_US / 1000u);
        }
    }
    if ((s->scrub || s->stage != STAGE_REST) &&
        ui_time() - s->key_at >= STAGE_US) { /* no key: the cycle ends */
        scrub_end(s);
        stage_set(s, STAGE_REST);
    }
    if (n->radio != s->radio) s->radio = n->radio, s->radio_at = ui_time(), cycle_mark();
    if (s->radio_at && ui_time() - s->radio_at < PILL_US) cycle_mark(), ui_again();
    if (s->volume_at && ui_time() - s->volume_at >= VOLUME_SHOW_US)
        s->volume_at = 0, ui_mark((rect_t){ 0, BAR_Y - 6, SCREEN_W, 44 });
    if (s->title.n && s->title.cut && !s->mq_at)
        s->mq_at = ui_time(); /* laid out and found too long */
    if (s->mq_at && s->whole.n) {
        float over = s->whole.w - (float)(SCREEN_W - TEXT_X - 24), x = 0;
        uint64_t t = ui_time() - s->mq_at,
                 slide = over > 0 ? (uint64_t)(over / MQ_SPEED * 1e6f) : 0;
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
    if (now_cover_seq() != s->cover_seen)
        s->cover_seen = now_cover_seq(), ui_mark(card_bounds(ART));
    int pos = position_ms() / 1000;
    if (pos != s->pos_s) s->pos_s = pos, ui_mark((rect_t){ 0, BAR_Y - 2, SCREEN_W, 40 });
    if (n->playing) ui_wake_ms(250); /* the bar's next pixel and the times' next second */
}
/* The box of im's ink (its alpha's bounds) within dst, which im fills at its own size. */
static void ink_box(const image_t *im, rectf_t *dst) {
    int x0 = im->w, y0 = im->h, x1 = -1, y1 = -1;
    for (int y = 0; y < im->h; ++y)
        for (int x = 0; x < im->w; ++x)
            if (im->px[y * im->w + x] >> 24)
                x0 = x < x0 ? x : x0, x1 = x > x1 ? x : x1, y0 = y < y0 ? y : y0,
                y1 = y > y1 ? y : y1;
    if (x1 < 0) return;
    dst->x += (float)x0, dst->y += (float)y0, dst->w = (float)(x1 - x0 + 1),
                                              dst->h = (float)(y1 - y0 + 1);
}
/* The Centre cycle's ring: RING_W of Text Primary, RING_R corners, its inner edge RING_PAD clear of
 * ink (glyph_framed). */
static void ring(rectf_t ink) {
    float pad = RING_PAD + RING_W;
    rectf_t out = { ink.x - pad, ink.y - pad, ink.w + 2 * pad, ink.h + 2 * pad };
    fill_round(out, RING_R, 0, tok(TOK_PRIMARY), 1, 0);
    fill_round((rectf_t){ out.x + RING_W, out.y + RING_W, out.w - 2 * RING_W, out.h - 2 * RING_W },
               RING_R - RING_W, 0, tok(TOK_CANVAS), 1, 0);
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
    rectf_t art = { ART.x + ART.w * small, ART.y + ART.h * small, ART.w * (1 - 2 * small),
                    ART.h * (1 - 2 * small) };
    card_draw(art, 6, layers, 1, (float)s->push.x, 0);
    int room = SCREEN_W - TEXT_X - 24;
    if (!s->title.n)
        run_make(&s->title, FACE_MEDIUM, TYPE(22), *n->title ? n->title : "Not Playing", room);
    if (!s->artist.n && *n->artist) run_make(&s->artist, FACE_REGULAR, TYPE(18), n->artist, room);
    if (!s->album.n && *n->album) run_make(&s->album, FACE_REGULAR, TYPE(18), n->album, room);
    if (s->title.cut) {
        /* the title whole, slid in its column with its ends faded while it is away from them */
        if (!s->whole.n) run_make(&s->whole, FACE_MEDIUM, TYPE(22), n->title, -1);
        fade_set(TEXT_X, TEXT_X + room, s->mq_x > 0.5f ? MQ_FADE : 0,
                 s->whole.w - s->mq_x > (float)room + 0.5f ? MQ_FADE : 0);
        run_draw(&s->whole, TEXT_X - s->mq_x, TITLE_Y, tok(TOK_PRIMARY), 256);
        fade_clear();
    } else
        run_draw(&s->title, TEXT_X, TITLE_Y, tok(TOK_PRIMARY), 256);
    if (*n->artist) run_draw(&s->artist, TEXT_X, 96, tok(TOK_SECONDARY), 256);
    if (*n->album) run_draw(&s->album, TEXT_X, 120, tok(TOK_SECONDARY), 256);
    /* the row of modes at the foot, as marks of what is set: shuffle, the radio, repeat, Up Next,
     * each in the theme's own ink for its state (the accent when on), a lit shuffle or repeat with
     * the accent's dot under it as demo's. Repeat is demo's file's: 0 off, 1 one, 2 all
     * (patch/coda_rc_shim.c cr_modes). While a radio plays its button is demo's pill, PILL_W x
     * PILL_H in the accent holding the antenna and "Radio" in white, grown from the glyph's width
     * over PILL_US (ease out) with the word fading in over its last 40%, and shuffle and repeat are
     * dimmed; the four stay between ROW_LEFT and ROW_RIGHT with even gaps (patch/np_bar.c
     * np_modes). */
    float t = s->radio_at ? clampf((float)(ui_time() - s->radio_at) / PILL_US, 0, 1) : 1;
    float grow = 1 - (1 - t) * (1 - t), on = n->radio ? grow : 1 - grow; /* ease out */
    const char *mode[] = { n->radio         ? "mode_shuffle_xd."
                           : n->shuffle > 0 ? "mode_shuffle_on."
                                            : "mode_shuffle_d.",
                           "mode_radio_d.",
                           n->radio         ? "mode_repeat_xd."
                           : n->repeat == 1 ? "mode_repeat1_on."
                           : n->repeat > 0  ? "mode_repeat_on."
                                            : "mode_repeat_d.",
                           "mode_queue_d." };
    const int lit[] = { !n->radio && n->shuffle > 0, 0, !n->radio && n->repeat > 0, 0 };
    const image_t *im[4];
    float w[4], sum = 0, x = ROW_LEFT;
    for (int k = 0; k < 4; ++k) {
        im[k] = icon(mode[k]);
        w[k] = MODE_BOX;
    }
    w[1] += (PILL_W - w[1]) * on;
    for (int k = 0; k < 4; ++k) sum += w[k];
    float gap = (ROW_RIGHT - ROW_LEFT - sum) / 3;
    for (int k = 0; k < 4; ++k) {
        float cx = x + w[k] / 2;
        x += w[k] + gap;
        if (k == 1 && on > 0) {
            fill_round((rectf_t){ cx - w[1] / 2, MODE_Y - PILL_H / 2.0f, w[1], PILL_H },
                       PILL_H / 2.0f, 0, RGB_ACCENT, 1, 0);
            const image_t *ant = icon("mode_radio");
            static run_t word;
            if (!word.n || run_stale(&word)) run_make(&word, FACE_MEDIUM, TYPE(16), "Radio", -1);
            float aw = ant ? (float)ant->w : 18, whole = aw + 6 + word.w,
                  ax = cx - (whole * on + aw * (1 - on)) / 2;
            if (ant)
                image_draw(ant, (rectf_t){ ax, MODE_Y - (float)ant->h / 2, aw, (float)ant->h }, 0,
                           0, 256, 0xffffff);
            unsigned fade = on > 0.6f ? (unsigned)((on - 0.6f) / 0.4f * 256) : 0;
            if (fade) run_draw(&word, ax + aw + 6, MODE_Y - TYPE(16) / 2.0f - 2, 0xffffff, fade);
            continue;
        }
        if (s->stage - STAGE_SHUFFLE == (k == 0   ? 0
                                         : k == 2 ? 1
                                                  : -9) &&
            stop_ok(s->stage) && im[k]) {
            /* the ring round the glyph's ink and its on dot (patch/np_bar.c glyph_framed) */
            rectf_t ink = { cx - (float)im[k]->w / 2, MODE_Y - (float)im[k]->h / 2, (float)im[k]->w,
                            (float)im[k]->h };
            ink_box(im[k], &ink);
            if (lit[k]) ink.h = MODE_Y + DOT_DY + DOT / 2.0f - ink.y;
            ring(ink);
        }
        if (im[k])
            image_draw(im[k],
                       (rectf_t){ cx - (float)im[k]->w / 2, MODE_Y - (float)im[k]->h / 2,
                                  (float)im[k]->w, (float)im[k]->h },
                       0, 0, 256, -1);
        if (lit[k])
            fill_round((rectf_t){ cx - DOT / 2, MODE_Y + DOT_DY - DOT / 2, DOT, DOT }, DOT / 2, 0,
                       RGB_ACCENT, 1, 0);
    }
    /* liked, and not: the image's box at y 146 puts its ink's top on demo's, y 155
     * (ui/tools/compare.py on the emulator's shots of the same song, m17c-04 and -09) */
    const image_t *heart = icon(n->hearts > 0 ? "np_fav_d." : "np_unfav_d.");
    if (heart && *n->title && s->stage == STAGE_HEART) { /* its ring, as demo's heart_ring */
        rectf_t ink = { 194, 146, (float)heart->w, (float)heart->h };
        ink_box(heart, &ink);
        ring(ink);
    }
    if (heart && *n->title)
        image_draw(heart, (rectf_t){ 194, 146, (float)heart->w, (float)heart->h }, 0, 0, 256, -1);
    const image_t *more = icon("np_more_d.");
    if (more && *n->title)
        image_draw(more,
                   (rectf_t){ MORE_X - (float)more->w / 2, MORE_Y - (float)more->h / 2,
                              (float)more->w, (float)more->h },
                   0, 0, 256, -1);
    if (s->volume_at) {
        /* the volume's level in the bar's place, as demo's (patch/volume.c vol_paint): the track
         * VOL_INSET in from each end, the level in white, and demo's quiet and loud speakers
         * (tools/art.py vol_lo, vol_hi), VOL_SPK px, centred VOL_SPK_X in from each end of the bar
         */
        float level = (float)s->volume / 100, x0 = BAR_X + VOL_INSET, w = BAR_W - 2 * VOL_INSET,
              cy = BAR_Y + BAR_H / 2.0f;
        fill_round((rectf_t){ x0, BAR_Y, w, BAR_H }, 2, 0, tok(TOK_TRACK), 1, 0);
        if (level > 0)
            fill_round((rectf_t){ x0, BAR_Y, BAR_H + (w - BAR_H) * level, BAR_H }, 2, 0,
                       tok(TOK_PRIMARY), 1, 0);
        const image_t *lo = icon(ui_light ? "vol_lo_l." : "vol_lo_d."),
                      *hi = icon(ui_light ? "vol_hi_l." : "vol_hi_d.");
        if (lo)
            image_draw(
                lo,
                (rectf_t){ BAR_X + VOL_SPK_X - VOL_SPK / 2, cy - VOL_SPK / 2, VOL_SPK, VOL_SPK }, 0,
                0, 256, -1);
        if (hi)
            image_draw(hi,
                       (rectf_t){ BAR_X + BAR_W - VOL_SPK_X - VOL_SPK / 2, cy - VOL_SPK / 2,
                                  VOL_SPK, VOL_SPK },
                       0, 0, 256, -1);
        return;
    }
    /* the bar: its fill ends between pixels, so it creeps and never steps */
    int pos = s->scrub ? s->scrub_to * 1000 : position_ms();
    float share = n->len_s ? clampf((float)pos / ((float)n->len_s * 1000), 0, 1) : 0,
          end = BAR_H + (BAR_W - BAR_H) * share;
    fill_round((rectf_t){ BAR_X, BAR_Y, BAR_W, BAR_H }, 2, 0, tok(TOK_TRACK), 1, 0);
    if (share > 0) fill_round((rectf_t){ BAR_X, BAR_Y, end, BAR_H }, 2, 0, RGB_ACCENT, 1, 0);
    if (s->scrub) { /* the scrub's white knob on the fill's end, kept on the bar (patch/np_bar.c
                       paint_scrubber) */
        float kx = clampf(BAR_X + end, BAR_X + KNOB / 2.0f, BAR_X + BAR_W - KNOB / 2.0f);
        fill_disc(kx, BAR_Y + BAR_H / 2.0f, KNOB / 2.0f, 0xffffff, 1);
    }
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
/* a script's trace line: the Centre cycle (scrubbing, its place in s, the ring's stage) */
static int n_trace(void *st, char *out, size_t room) {
    const struct now_st *s = st;
    return snprintf(out, room, "sel=-1 scrub=%d to=%d stage=%d", s->scrub, s->scrub_to, s->stage);
}
const ui_page page_now = {
    "now", n_open, n_event, n_step, n_draw, n_close, 0, n_where, 0, n_trace
};
