/* The game engine (docs/games-engine.md): a fixed 60 Hz step under Games' sheet (games.c), one
 * frame a refresh; the wheel, the keys and a finger as one record a step; drawing straight into
 * the screen's buffer, clipped to the rects a step said changed; the Clicker's sounds; sealed
 * saves. A game is an eng_game in eng_games[]; its state is on the heap with the engine's, from
 * eng_open to eng_close, and nothing is allocated between. */
#include "engine.h"
#include "navigation.h"

static struct eng {
    const struct eng_game *game;
    void *page, *sheet;
    void (*held)(void), (*left)(void); /* games.c: the pause shown, the list again */
    unsigned timer, at, acc;     /* the frame timer, its last pass (ms), time owed (ENG_STEP) */
    int running, quit, h, first; /* first: the timer's first pass is a step, whenever it comes */
    unsigned keys, pressed, released; /* ENG_* held, and the edges since the last frame */
    int ticks, fast, tx, ty;
    int rects, rect[ENG_RECTS][4];    /* x0 y0 x1 y1 on the sheet */
    unsigned steps, frames;           /* counted: steps taken, frames that painted */
    unsigned second;                  /* ENG_CLOCK's timer while the frame timer is off */
    unsigned play, play_at, input_at; /* play time (ms), when it was last counted, the last input */
    long long state[];                /* the game's, aligned for anything */
} *en __attribute__((section(".scratch")));

/* The registry: Games' rows after its own, in this order. The demo scene is a --dev build's, last,
 * and only with [Q2POD] ENGINEDEMO=1 in config.ini; a release build links none of it. */
static const struct eng_game *const eng_games[] = {
    &eng_popstall, &eng_solitaire, &eng_sudoku, &eng_callerten, &eng_embergarden,
#if Q2POD_DEV
    &eng_demo,
#endif
};
int eng_count(void) {
    int n = (int)(sizeof eng_games / sizeof *eng_games);
    return Q2POD_DEV && !config_value("Q2POD", "ENGINEDEMO", 2, 0) ? n - 1 : n;
}
const struct eng_game *eng_game(int k) { return eng_games[k]; }

int eng_live(void) { return en && en->timer; }
/* game's state while it is the one open, held or running; else 0 (it was never opened, or closed)
 */
void *eng_state_of(const struct eng_game *game) { return en && en->game == game ? en->state : 0; }

void eng_dirty(int x, int y, int w, int h) {
    struct eng *e = en;
    int x1 = x + w, y1 = y + h;
    x = x < 0 ? 0 : x, y = y < 0 ? 0 : y;
    x1 = x1 > ENG_W ? ENG_W : x1, y1 = y1 > e->h ? e->h : y1;
    if (x1 <= x || y1 <= y) return;
    if (e->rects < ENG_RECTS) {
        int *r = e->rect[e->rects++];
        r[0] = x, r[1] = y, r[2] = x1, r[3] = y1;
        return;
    }
    int *r = e->rect[ENG_RECTS - 1];
    r[0] = x < r[0] ? x : r[0], r[1] = y < r[1] ? y : r[1];
    r[2] = x1 > r[2] ? x1 : r[2], r[3] = y1 > r[3] ? y1 : r[3];
}
void eng_dirty_all(void) {
    en->rects = 0;
    eng_dirty(0, 0, ENG_W, en->h);
}
void eng_quit(void) { en->quit = 1; }
void eng_awake(void) { reset_poweroptions_timer(1, 1, 1); }

/* The glass's round corners (GLASS_R) at the sheet's row y: the columns they hide at each end. */
int eng_inset(int y) {
    int row = y + 320 - en->h, d = row < GLASS_R ? GLASS_R - row : row - (319 - GLASS_R);
    return d <= 0 ? 0 : GLASS_R - (int)eng_sqrt((unsigned long long)(GLASS_R * GLASS_R - d * d));
}

/* One of the Clicker's three sounds, by its route (clicker.c): mixed into the song by q2player, or
 * the buzzer's click, or nothing with the Clicker off. The music is never paused or ducked. */
void eng_sfx(int kind) {
    clicker_kind = kind == ENG_SFX_TICK  ? CLICK_KIND_WHEEL
                   : kind == ENG_SFX_TAP ? CLICK_KIND_BUTTON
                                         : CLICK_KIND_BEEP;
    buzzeer_switch(1);
    clicker_kind = 0;
}

static void save_path(char *path, char *tmp, const char *name) {
    tk_snprintf(path, 48, "/mnt/data/game-%s", name);
    tk_snprintf(tmp, 52, "%s.tmp", path);
}
int eng_load(const char *name, void *buf, unsigned size, unsigned magic) {
    char path[48], tmp[52];
    save_path(path, tmp, name);
    if (blob_io(path, tmp, buf, size, 0) && games_ok(buf, size, magic)) return 1;
    memset(buf, 0, size);
    return 0;
}
int eng_save(const char *name, void *buf, unsigned size, unsigned magic) {
    char path[48], tmp[52];
    save_path(path, tmp, name);
    games_seal(buf, size, magic);
    if (blob_io(path, tmp, buf, size, 1)) return 1;
    toast("Couldn't save");
    return 0;
}

/* Play time to now: the part up to ENG_REST_MS past the last input, while the game runs. */
static void count_play(struct eng *e, unsigned now) {
    unsigned cap = e->input_at + ENG_REST_MS, to = (int)(now - cap) < 0 ? now : cap;
    if (e->running && (int)(to - e->play_at) > 0) e->play += to - e->play_at;
    e->play_at = now;
}
unsigned eng_play_ms(void) {
    count_play(en, (unsigned)time_now_ms());
    return en->play;
}

/* The rects the step marked, to the sheet. */
static void flush(struct eng *e) {
    e->frames += e->rects != 0;
    for (int k = 0; k < e->rects; ++k) {
        int *r = e->rect[k], box[4] = { r[0], r[1], r[2] - r[0], r[3] - r[1] };
        widget_invalidate_force(e->sheet, box);
    }
    e->rects = 0;
}

static int eng_frame(const void *unused);
static int eng_second(const void *unused);
/* An ENG_CLOCK game at rest: its second timer on. */
static void tick(struct eng *e) {
    if (e->running && !e->timer && !e->second && e->game->flags & ENG_CLOCK)
        e->second = timer_add(eng_second, 0, ENG_SECOND_MS);
}
/* The screen off or another window on top: the game is held (games.c), the passing timer ending. */
static int covered(struct eng *e) {
    if (g_backlight_status && window_manager_get_top_window(window_manager()) == e->page) return 0;
    e->held();
    return 1;
}

/* ENG_CLOCK's pass at rest: a step with no input, its rects painted (the shown time's); the frame
 * timer again if it starts something moving. The screen off, another window or ENG_REST_MS past
 * the last input ends it: nothing wakes the loop for a clock that stands. */
static void arm(void);
static int eng_second(const void *unused) {
    struct eng *e = en;
    struct eng_input in = { 0 };
    unsigned id = e->second;
    e->second = 0; /* ending, unless it repeats: no timer to remove */
    if (covered(e)) return 0;
    int moving = e->game->step(e->state, &in);
    ++e->steps;
    flush(e);
    if (moving) return arm(), 0;
    if ((int)((unsigned)time_now_ms() - e->input_at) >= ENG_REST_MS) return 0;
    e->second = id;
    return 8; /* RET_REPEAT */
}

/* The frame timer on, its first pass a step: a turn-based game's answer to a key comes with the
 * next pass of the loop. */
static void arm(void) {
    struct eng *e = en;
    if (e->timer || !e->running) return;
    stop_timer(&e->second);
    e->at = (unsigned)time_now_ms();
    e->first = 1;
    e->timer = timer_add(eng_frame, 0, ENG_TIMER_MS);
}

/* Every pass of the UI loop while a game runs. A pass a refresh or more after the last (the paint
 * waits for the panel, so that is every pass while the picture moves) is that many refreshes to
 * the nearest, one step each and ENG_MAX_STEPS at most, whatever the millisecond clock made of
 * them; a sooner pass adds its time to what is owed. The frame's input goes to its first step.
 * The screen off or another window on top pauses. */
static int eng_frame(const void *unused) {
    struct eng *e = en;
    unsigned id = e->timer;
    e->timer = 0; /* ending: no timer to remove */
    if (covered(e)) return 0;
    e->timer = id;
    unsigned now = (unsigned)time_now_ms(), gap = now - e->at;
    int n, moving = 0;
    e->at = now;
    if (e->first)
        n = 1, e->first = 0;
    else if (gap < ENG_SNAP_LO) { /* a pass that painted nothing: the time is owed */
        e->acc += gap * ENG_HZ;
        n = (int)(e->acc / ENG_STEP);
        e->acc -= (unsigned)n * ENG_STEP;
        if (!n) return 8; /* RET_REPEAT */
    } else {
        n = (int)(((gap > 1000 ? 1000 : gap) * ENG_HZ + ENG_STEP / 2) / ENG_STEP);
        n = n > ENG_MAX_STEPS ? ENG_MAX_STEPS : n;
    }
    struct eng_input in = { 0, e->ticks, e->fast, e->keys, e->pressed, e->released, e->tx, e->ty };
    /* the wheel's x falls clockwise (playback.c wheel_sample) */
    if (e->game->flags & ENG_RAW) in.wheel = -__sync_lock_test_and_set(&wheel_travel, 0);
    e->ticks = e->fast = 0;
    e->pressed = e->released = 0;
    if (in.wheel || in.ticks || in.pressed || in.released) {
        eng_awake();
        count_play(e, now); /* the rest before this input is counted to its cap, then starts over */
        e->input_at = now;
    }
    for (int k = 0; k < n && !e->quit; ++k) {
        moving = e->game->step(e->state, &in);
        in.wheel = in.ticks = in.fast = 0;
        in.pressed = in.released = 0;
    }
    e->steps += (unsigned)n;
    flush(e);
    if (e->quit || ((e->game->flags & ENG_STILL) && !moving)) {
        e->timer = 0;
        if (e->quit)
            e->left();
        else
            tick(e); /* at rest: an ENG_CLOCK game's second timer */
        return 0;
    }
    return 8;
}

/* The wheel's steps a tick at its speed: the volume's curve (volume.c vol_accel), Rockbox's. */
static int accel(void) {
    unsigned v = (unsigned)wheel_speed;
    if (v > VOL_ACCEL_TOP) v = VOL_ACCEL_TOP;
    unsigned r = v * 256 / VOL_ACCEL_START, r2 = r * r >> 8, n = ((r2 * r2 >> 8) + 128) >> 8;
    return n < 1 ? 1 : n > ENG_ACCEL_MAX ? ENG_ACCEL_MAX : (int)n;
}

/* games.c, a key of the running game's: a press (ringnav_keydown, its repeats too) or a release
 * (ringnav). A wheel tick is its release. 1: the game's. */
int eng_key(unsigned key, int down) {
    struct eng *e = en;
    if (!e || !e->running) return 0;
    if (key == KEY_NEXT || key == KEY_PREV) {
        int dir = key == KEY_NEXT ? 1 : -1;
        if (!down && !(e->game->flags & ENG_RAW)) e->ticks += dir, e->fast += dir * accel();
    } else {
        unsigned bit = key == KEY_CENTER     ? ENG_CENTRE
                       : key == KEY_PLAY     ? ENG_PLAY
                       : key == KEY_BACK_BTN ? ENG_PREV
                       : key == KEY_FWD_BTN  ? ENG_NEXT
                                             : 0;
        if (!bit) return 0;
        if (!(e->keys & bit)) e->pressed |= bit; /* a release whose press went elsewhere is both */
        if (down)
            e->keys |= bit;
        else
            e->keys &= ~bit, e->released |= bit;
    }
    arm();
    return 1;
}

/* The sheet's pointer events (games.c registers it): the finger as ENG_TOUCH, placed on the sheet.
 */
int eng_touch(void *ctx, void *event) {
    struct eng *e = en;
    if (!e || !e->running) return 0;
    int type = I(event, EVENT_TYPE), xy[2] = { I(event, EVENT_X), I(event, EVENT_Y) };
    if (type != EVT_POINTER_DOWN && !(e->keys & ENG_TOUCH)) return 0;
    widget_to_local(e->sheet, xy);
    e->tx = xy[0], e->ty = xy[1];
    if (type == EVT_POINTER_DOWN)
        e->keys |= ENG_TOUCH, e->pressed |= ENG_TOUCH;
    else if (type == EVT_POINTER_UP_BEFORE)
        e->keys &= ~ENG_TOUCH, e->released |= ENG_TOUCH;
    arm();
    return 0;
}

void eng_run(void) {
    struct eng *e = en;
    if (!e || e->running) return;
    e->running = 1;
    e->play_at = e->input_at = (unsigned)time_now_ms(); /* back from a pause: as an input */
    e->acc = 0;
    e->keys = e->pressed = e->released = 0;
    e->ticks = e->fast = 0;
    __sync_lock_test_and_set(&wheel_travel, 0);
    wheel_raw = e->game->flags & ENG_RAW ? WHEEL_RAW : WHEEL_TICKS;
    arm();
}

void eng_hold(void) {
    struct eng *e = en;
    if (!e || !e->running) return;
    count_play(e, (unsigned)time_now_ms());
    e->running = 0;
    wheel_raw = WHEEL_TICKS;
    stop_timer(&e->timer);
    stop_timer(&e->second);
    if (e->game->pause) e->game->pause(e->state);
}

int eng_open(const struct eng_game *game, void *page, void *sheet, int h, void (*held)(void),
             void (*left)(void)) {
    struct eng *e = en ? (void *)0 : calloc(1, sizeof *e + game->size);
    if (!e) return 0;
    e->game = game, e->page = page, e->sheet = sheet, e->h = h;
    e->held = held, e->left = left;
    en = e;
    if (game->init) game->init(e->state, h);
    eng_run();
    return 1;
}

void eng_close(void) {
    if (!en) return;
    eng_hold();
    free(en);
    en = 0;
}

/* ---- drawing ---- */

static inline unsigned blend1(unsigned dst, unsigned rgb, unsigned a) { /* a of 256 */
    unsigned rb = ((rgb & 0xff00ff) * a + (dst & 0xff00ff) * (256 - a)) >> 8 & 0xff00ff;
    unsigned g = ((rgb & 0xff00) * a + (dst & 0xff00) * (256 - a)) >> 8 & 0xff00;
    return 0xff000000 | rb | g;
}

/* The part of x, y, w, h being painted, as x0 y0 x1 y1 in c; 0 when none of it is. */
int eng_clip(const struct eng_surface *s, int x, int y, int w, int h, int *c) {
    c[0] = x < s->x0 ? s->x0 : x, c[1] = y < s->y0 ? s->y0 : y;
    c[2] = x + w > s->x1 ? s->x1 : x + w, c[3] = y + h > s->y1 ? s->y1 : y + h;
    return c[2] > c[0] && c[3] > c[1];
}
static inline unsigned *row(const struct eng_surface *s, int x, int y) {
    return s->px + (y + s->oy) * s->stride + x + s->ox;
}

void eng_fill(struct eng_surface *s, int x, int y, int w, int h, unsigned rgb) {
    int c[4];
    if (!eng_clip(s, x, y, w, h, c)) return;
    rgb |= 0xff000000;
    for (int j = c[1]; j < c[3]; ++j)
        for (unsigned *p = row(s, c[0], j), *end = p + (c[2] - c[0]); p < end; ++p) *p = rgb;
}

static unsigned root(unsigned n) {
    unsigned r = 0, bit = 1u << 30;
    while (bit > n) bit >>= 2;
    for (; bit; bit >>= 2)
        if (n >= r + bit)
            n -= r + bit, r = (r >> 1) + bit;
        else
            r >>= 1;
    return r;
}

/* How much of a pixel a disc of radius r covers, 0-256, its centre dx, dy away; all in 1/16 px.
 * The edge is a pixel wide, by the distance alone. */
static inline unsigned cover(int dx, int dy, int r) {
    unsigned d2 = (unsigned)(dx * dx + dy * dy);
    int in = r - 8, out = r + 8;
    if (in > 0 && d2 <= (unsigned)(in * in)) return 256;
    if (d2 >= (unsigned)(out * out)) return 0;
    return (unsigned)(out - (int)root(d2)) * 16;
}

void eng_disc(struct eng_surface *s, fx cx, fx cy, fx r, unsigned rgb) {
    int x16 = cx >> 12, y16 = cy >> 12, r16 = r >> 12, c[4];
    int x = (x16 - r16 - 8) >> 4, y = (y16 - r16 - 8) >> 4, d = ((r16 + 8) >> 3) + 2;
    if (r16 <= 0 || !eng_clip(s, x, y, d, d, c)) return;
    for (int j = c[1]; j < c[3]; ++j) {
        int dy = j * 16 + 8 - y16;
        unsigned *p = row(s, c[0], j);
        for (int i = c[0]; i < c[2]; ++i, ++p) {
            unsigned a = cover(i * 16 + 8 - x16, dy, r16);
            if (a) *p = a == 256 ? 0xff000000 | rgb : blend1(*p, rgb, a);
        }
    }
}

void eng_round(struct eng_surface *s, int x, int y, int w, int h, int r, unsigned rgb) {
    int c[4];
    r = 2 * r > w ? w / 2 : r, r = 2 * r > h ? h / 2 : r;
    if (!eng_clip(s, x, y, w, h, c)) return;
    for (int j = c[1]; j < c[3]; ++j) {
        int v = j - y, cyr = v < r ? r : v >= h - r ? h - r : -1; /* the corner's centre row */
        unsigned *p = row(s, c[0], j);
        for (int i = c[0]; i < c[2]; ++i, ++p) {
            int u = i - x, cxr = u < r ? r : u >= w - r ? w - r : -1;
            unsigned a = 256;
            if (cyr >= 0 && cxr >= 0)
                a = cover(u * 16 + 8 - cxr * 16, v * 16 + 8 - cyr * 16, r * 16);
            if (a) *p = a == 256 ? 0xff000000 | rgb : blend1(*p, rgb, a);
        }
    }
}

int eng_text(struct eng_surface *s, const char *text, int x, int y, int h, unsigned px,
             unsigned rgb, int align) {
    return games_text(s->canvas, text, x, y, h, px, rgba(rgb, 255), align);
}

/* games_paint: the game's draw over the sheet stock just painted, onto the screen's own buffer
 * (stock's 375 x 320 lcd_mem, BGRA8888, as nightshift.c's warm reads it), within the rows and
 * columns this pass paints: stock paints each changed rect apart, the canvas clipped to it. A
 * buffer of another layout is left as stock drew it. */
void eng_paint(void *canvas) {
    struct eng *e = en;
    void *lcd = canvas ? P(canvas, CANVAS_LCD) : (void *)0;
    if (!e || !lcd || !P(lcd, LCD_OFFLINE_FB) || I(lcd, LCD_FORMAT) != LCD_BGRA8888 ||
        I(lcd, LCD_LINE_LENGTH) != 4 * I(lcd, LCD_W))
        return;
    int r[4] = { 0, 0, 0, 0 }, w = I(lcd, LCD_W), h = I(lcd, LCD_H);
    struct eng_surface s = { P(lcd, LCD_OFFLINE_FB),
                             w,
                             I(canvas, CANVAS_X),
                             I(canvas, CANVAS_Y),
                             ENG_W,
                             e->h,
                             0,
                             0,
                             0,
                             0,
                             canvas };
    canvas_get_clip_rect(canvas, r);
    int x0 = r[0], y0 = r[1], x1 = r[0] + r[2], y1 = r[1] + r[3];
    x0 = x0 < 0 ? 0 : x0, y0 = y0 < 0 ? 0 : y0;
    x1 = x1 > w ? w : x1, y1 = y1 > h ? h : y1;
    s.x0 = x0 - s.ox < 0 ? 0 : x0 - s.ox, s.y0 = y0 - s.oy < 0 ? 0 : y0 - s.oy;
    s.x1 = x1 - s.ox > ENG_W ? ENG_W : x1 - s.ox, s.y1 = y1 - s.oy > e->h ? e->h : y1 - s.oy;
    /* an ENG_OWN_PAUSE game held: the status row's band is stock's again, over the sheet's top rows
     */
    if ((e->game->flags & ENG_OWN_PAUSE) && !e->running && s.y0 < ENG_BAR) s.y0 = ENG_BAR;
    if (s.x1 > s.x0 && s.y1 > s.y0) e->game->draw(e->state, &s);
}
