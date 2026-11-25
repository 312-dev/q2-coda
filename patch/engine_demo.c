/* The engine's demo scene (docs/games-engine.md#the-demo-scene): a ball that bounces and a paddle
 * the wheel, a finger or Previous and Next move. It is here to measure a frame and to show a game's
 * four calls; a --dev build lists it in Games with [Q2POD] ENGINEDEMO=1. */
#include "engine.h"

#define D_LEFT 20 /* the field, clear of the glass's corners */
#define D_RIGHT 355
#define D_TOP 30
#define D_R 6      /* the ball's radius */
#define D_PAD_W 64 /* the paddle */
#define D_PAD_H 8
#define D_PAD_UP 44        /* its top, above the sheet's end */
#define D_WHEEL 2          /* px a wheel position: half a turn crosses the field */
#define D_KEY 4            /* px a step while Previous or Next is held */
#define D_MAGIC 0x314f4d44 /* "DMO1" */

struct demo {
    fx x, y, vx, vy;
    int pad, h, hits, fast;
    struct {
        unsigned magic, best, sum;
    } save;
    unsigned saved; /* the best on the card */
};

static void serve(struct demo *d) {
    d->x = FX((D_LEFT + D_RIGHT) / 2), d->y = FX(D_TOP + 40);
    d->vx = d->fast ? FX(3.375) : FX(2.25), d->vy = d->fast ? FX(3.75) : FX(2.5);
    d->hits = 0;
}

static void init(void *state, int h) {
    struct demo *d = state;
    d->h = h;
    d->pad = (D_LEFT + D_RIGHT) / 2;
    eng_load("demo", &d->save, sizeof d->save, D_MAGIC);
    d->saved = d->save.best;
    serve(d);
}

static void ball_dirty(const struct demo *d) {
    eng_dirty(FX_INT(d->x) - D_R - 1, FX_INT(d->y) - D_R - 1, 2 * D_R + 3, 2 * D_R + 3);
}
static void pad_dirty(const struct demo *d) {
    eng_dirty(d->pad - D_PAD_W / 2, d->h - D_PAD_UP, D_PAD_W, D_PAD_H);
}

static int step(void *state, const struct eng_input *in) {
    struct demo *d = state;
    int pad = d->pad + in->wheel * D_WHEEL, hits = d->hits, top = d->h - D_PAD_UP;
    if (in->held & ENG_TOUCH) pad = in->tx;
    if (in->held & ENG_PREV) pad -= D_KEY;
    if (in->held & ENG_NEXT) pad += D_KEY;
    pad = pad < D_LEFT + D_PAD_W / 2 ? D_LEFT + D_PAD_W / 2 : pad;
    pad = pad > D_RIGHT - D_PAD_W / 2 ? D_RIGHT - D_PAD_W / 2 : pad;
    if (pad != d->pad) pad_dirty(d), d->pad = pad, pad_dirty(d);
    if (in->pressed & ENG_CENTRE) { /* the other speed, from the next serve on */
        d->fast = !d->fast;
        eng_sfx(ENG_SFX_TAP);
    }
    if (in->pressed & ENG_PLAY) eng_quit();
    ball_dirty(d);
    d->x += d->vx, d->y += d->vy;
    if (d->x < FX(D_LEFT + D_R)) d->x = 2 * FX(D_LEFT + D_R) - d->x, d->vx = -d->vx;
    if (d->x > FX(D_RIGHT - D_R)) d->x = 2 * FX(D_RIGHT - D_R) - d->x, d->vx = -d->vx;
    if (d->y < FX(D_TOP + D_R)) d->y = 2 * FX(D_TOP + D_R) - d->y, d->vy = -d->vy;
    int dx = FX_INT(d->x) - d->pad;
    if (d->vy > 0 && d->y >= FX(top - D_R) && d->y < FX(top + D_PAD_H) &&
        dx >= -D_PAD_W / 2 - D_R && dx <= D_PAD_W / 2 + D_R) {
        d->y = 2 * FX(top - D_R) - d->y, d->vy = -d->vy;
        d->vx += dx * (FX(1) / 64); /* the paddle's end sends it wider */
        d->vx = d->vx > FX(4) ? FX(4) : d->vx < -FX(4) ? -FX(4) : d->vx;
        d->hits++;
        if ((unsigned)d->hits > d->save.best) d->save.best = (unsigned)d->hits;
        eng_sfx(ENG_SFX_TICK);
    } else if (d->y > FX(d->h + D_R)) {
        serve(d);
        eng_sfx(ENG_SFX_ALERT);
    }
    ball_dirty(d);
    if (hits != d->hits) eng_dirty(0, 0, ENG_W, D_TOP - 2);
    return 1;
}

static void number(char *out, const char *name, unsigned v) {
    char digits[10];
    int n = 0;
    while (*name) *out++ = *name++;
    do digits[n++] = (char)('0' + v % 10);
    while (v /= 10);
    while (n) *out++ = digits[--n];
    *out = 0;
}

static void draw(const void *state, struct eng_surface *s) {
    const struct demo *d = state;
    char text[24];
    eng_fill(s, 0, 0, ENG_W, s->h, 0x000000);
    eng_fill(s, D_LEFT - 1, D_TOP - 1, D_RIGHT - D_LEFT + 2, 1, 0x222222);
    eng_fill(s, D_LEFT - 1, D_TOP - 1, 1, s->h - D_TOP, 0x222222);
    eng_fill(s, D_RIGHT, D_TOP - 1, 1, s->h - D_TOP, 0x222222);
    eng_round(s, d->pad - D_PAD_W / 2, d->h - D_PAD_UP, D_PAD_W, D_PAD_H, D_PAD_H / 2, 0xffffff);
    eng_disc(s, d->x, d->y, FX(D_R), 0x2160d8);
    if (s->y0 < D_TOP - 2) {
        number(text, "Bounces ", (unsigned)d->hits);
        eng_text(s, text, 120, 2, 24, 16, 0xffffff, 1);
        number(text, "Best ", d->save.best);
        eng_text(s, text, 262, 2, 24, 16, 0x858585, 1);
    }
}

/* A new best reaches the card at a pause, never in a frame. */
static void pause(void *state) {
    struct demo *d = state;
    if (d->save.best != d->saved && eng_save("demo", &d->save, sizeof d->save, D_MAGIC))
        d->saved = d->save.best;
}

const struct eng_game eng_demo = {
    "Engine Demo", sizeof(struct demo), ENG_RAW, init, step, draw, pause, "demo", "Test", 0, 0
};
