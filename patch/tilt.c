/* Tug-to-tilt (docs/oled-design.md#selection-bar, approved on 2026-11-15 from the mock-up
 * design/mockups/tilt-menu.html): while a finger turns the wheel short of its next tick, the
 * selection bar leans toward the row it is heading for, by the way it has come (wheel_part):
 * eased 1 - (1 - a)^2, it moves up to TILT_LIFT px that way, grows up to TILT_GROW thousandths,
 * casts an accent shadow under it and carries a white gleam that slides the other way. The tick
 * itself is unchanged (the wheel's travel per tick, Wheel Sensitivity's): past it the selection
 * steps a row and the next tug starts from the leftover travel; until ringnav takes the tick, the
 * lean stays whole (playback.c wheel_part), and the step paints with the leftover (input.c calls
 * tilt_poll), docs/internals.md#wheel-movement. Let go short of it and the bar
 * springs back over TILT_SPRING_MS. The mock-up's tilt of 1 degree is under a pixel on a 32 px
 * row, so it is not drawn. Only the bar's own box is painted again (tilt.box): tilt_poll, from
 * each UI loop pass, invalidates it in its window while the bar moves and that window is on top,
 * and tilt_clip lets the bar paint all of that box, past its list's edges. At a list's end the
 * lean holds full while the finger turns on (tilt_end, docs/internals.md#list-ends).
 * ponytail: a touch drag scrolls the list with the bar hidden, as every list does under touch
 * (navigation.c hide_outline), so only the wheel tilts it, at an end too, where a drag just stops;
 * a drag that stepped rows would be what lifts that.
 *
 * The press (docs/internals.md#row-press, after Apple TV, design/row-press option C): the bar at
 * rest sits raised, PRESS_FOCUS thousandths of its size with a soft accent shadow (PRESS_LIFT);
 * Centre's press sinks it to PRESS_DOWN over PRESS_DOWN_MS, cubic out, the shadow pulled in to a
 * quarter, and brightens its blue (tilt_tone); the release springs it back to PRESS_FOCUS over
 * PRESS_SPRING_MS with one small overshoot (spring[]) and fades the blue back over PRESS_FADE_MS.
 * The bar has one transform: a press wins over a lean, which goes to 0. The runs are anim_t on
 * ui_kit.h's shared frame timer, and tilt_poll, from the UI loop, paints the bar's box again
 * whenever what it shows changes, never the bar's own paint. Under Reduce Motion nothing scales or
 * casts a shadow: the blue steps up for the press and down PRESS_RM_HOLD_MS after the release. */
#include "navigation.h"
#include "playback.h"
#include "ui_kit.h"

#define TILT_LIFT 3          /* px toward the tug, at the tick */
#define TILT_GROW 35         /* thousandths of the bar's size it grows, at the tick */
#define TILT_SPRING_MS 220   /* back to rest after a lift */
#define TILT_SHADOW 3        /* the shadow's rings, each 2 px wider and lower */
#define TILT_SHADOW_A 77     /* its first ring's alpha at the tick (0.3) */
#define TILT_GLEAM_A 107     /* the gleam's centre at the tick (0.42 white) */
#define TILT_GLEAM_LEVELS 4  /* its falloff, in steps */
#define TILT_MARGIN 12       /* round the bar: the growth, the lift and the shadow */

static struct {
    void *w, *win;  /* the list whose bar leans, as last painted, and its window */
    rect_t box;     /* that bar's box and TILT_MARGIN round it, in the window's coordinates */
    int p;          /* the lean shown, 256ths of a tick, + toward the next row */
    int from;       /* a spring's start */
    unsigned at;    /* ... and its time, time_now_ms; 0 with none */
    void *end_w;    /* the list whose end a tick met with this finger down (tilt_end), */
    int end;        /* ... and which: +1 its last row, -1 its first; 0 none */
} tilt __attribute__((section(".scratch")));

#define PRESS_FOCUS 1020     /* the bar's size at rest, thousandths */
#define PRESS_DOWN 965       /* ... at the bottom of a press */
#define PRESS_DOWN_MS 90     /* Centre down to PRESS_DOWN, cubic out */
#define PRESS_SPRING_MS 280  /* Centre up back to PRESS_FOCUS, spring[] */
#define PRESS_FADE_MS 120    /* the blue's way back after the release, linear */
#define PRESS_RM_HOLD_MS 80  /* Reduce Motion: the blue held this long past the release */
#define PRESS_LIFT 160       /* the rest shadow's strength, 256ths of the lean's at the tick */
#define PRESS_BLUE 0x4080f0  /* the pressed step of OLED's one accent, its Blue scheme */

static struct {
    anim_t down, up, fade; /* the press, the spring and the blue's way back */
    short from;            /* the size at the release, thousandths */
    short bright_from;     /* the blue's step at the release, 0..1000 */
    int held;              /* Centre is down on the bar */
    unsigned shown;        /* what the bar last showed (press_sign), for tilt_poll */
} press __attribute__((section(".scratch")));

/* The release's spring, after the mock-up's e^(-5.5 x) cos(1.6 pi x): x in sixteenths of
 * PRESS_SPRING_MS, the share of the way still to go in thousandths, below 0 past rest (the one
 * overshoot, about +3 thousandths at x 0.46). */
static const short spring[17] = { 1000, 674, 407, 210, 78, 0,  -39, -53, -52,
                                  -43,  -32, -22, -13, -7, -3, 0,   1 };

/* The bar's size now, thousandths: PRESS_FOCUS at rest; 1000 under Reduce Motion. */
static int press_scale(void) {
    if (motion_reduced()) return 1000;
    if (press.held) return PRESS_FOCUS - (PRESS_FOCUS - PRESS_DOWN) * anim_at(&press.down) / 1000;
    if (!anim_running(&press.up)) return PRESS_FOCUS;
    int t = (int)((unsigned)time_now_ms() - press.up.at) * 16 * 64 / PRESS_SPRING_MS; /* 64ths */
    int i = t / 64, f = t % 64, s = (spring[i] * (64 - f) + spring[i + 1] * f) / 64;
    return PRESS_FOCUS - (PRESS_FOCUS - press.from) * s / 1000;
}

/* The blue's step toward its pressed tone now, 0..1000. Under Reduce Motion it steps: full from
 * the press to PRESS_RM_HOLD_MS past the release. */
static int press_bright(void) {
    if (press.held) return anim_at(&press.down);
    if (!anim_running(&press.fade)) return 0;
    if (motion_reduced()) return 1000;
    return press.bright_from * (1000 - anim_at(&press.fade)) / 1000;
}

static unsigned press_sign(void) {
    return (unsigned)press_scale() << 11 | (unsigned)press_bright();
}

/* A press on the bar runs or settles: the lean has no say (the press wins). */
static int pressing(void) {
    return press.held || anim_running(&press.up) || anim_running(&press.fade);
}

/* Centre's press (down 1) and release (0), from ringnav_input past its gates: a press only on the
 * top window's bar (tilt.w); its repeats while held change nothing. */
void tilt_press(int down) {
    if (down) {
        if (press.held || !tilt.w || tilt.win != window_manager_get_top_window(window_manager()))
            return;
        press.held = 1;
        press.up.at = press.fade.at = 0;
        tilt.p = 0, tilt.at = 0, tilt.end = 0; /* the lean goes */
        anim_start(&press.down, PRESS_DOWN_MS, EASE_CUBIC_OUT, MO_SPATIAL);
        return;
    }
    if (!press.held) return;
    press.from = (short)press_scale();
    press.bright_from = (short)press_bright();
    press.held = 0;
    anim_start(&press.up, PRESS_SPRING_MS, EASE_LINEAR, MO_SPATIAL);
    anim_start(&press.fade, motion_reduced() ? PRESS_RM_HOLD_MS : PRESS_FADE_MS, EASE_LINEAR,
               MO_FEEDBACK);
}

/* The bar's tone c (the accent's top, bottom or highlight) at the press's blue step (row_paint.c
 * paint_selection): toward PRESS_BLUE. ponytail: OLED has one accent, Blue (theme.xml oled-blue),
 * whose five tones are all the one blue, so every tone steps to PRESS_BLUE; a second accent would
 * need its own pressed step, carried in its scheme. */
unsigned tilt_tone(unsigned c) {
    int b = press_bright();
    return b ? mix(c, PRESS_BLUE, b, 1000) : c;
}

/* A wheel tick that met an end of w's list, toward dir (+1 the last row): the selection stays, and
 * the lean holds full against that end while the finger turns on that way (tilt_now). */
void tilt_end(void *w, int dir) {
    tilt.end_w = w;
    tilt.end = dir;
}

/* The lean to draw now: the wheel's while a finger holds it, else a spring back to 0. At an end
 * (tilt_end), turning on that way holds it full: each tick there moves nothing, so the leftover
 * travel would drop it to nothing and build it again. Turning back leaves the end. A lean is
 * MO_SPATIAL (ui_kit.h): under Reduce Motion there is none, the bar steps without leaning. */
static int tilt_now(void) {
    if (motion_reduced() || pressing()) return tilt.end = 0;
    int part = wheel_part();
    if (!wheel_held || wheel_raw || tilt.end_w != tilt.w) tilt.end = 0;
    if (tilt.end && part * tilt.end >= 0) return tilt.end * 256;
    tilt.end = 0;
    if (part || wheel_held) return part;
    if (!tilt.at) return 0;
    unsigned t = (unsigned)time_now_ms() - tilt.at;
    if (t >= TILT_SPRING_MS) return 0;
    /* cubic out, cubic-bezier(.2,0,0,1) near enough */
    return tilt.from * (1000 - motion_ease((int)(t * 1000 / TILT_SPRING_MS), EASE_CUBIC_OUT)) /
           1000;
}

/* Each UI loop pass: the bar's box painted again while its lean or its press changes; a lift
 * starts a spring. A press whose window left the top (a hold that opened a page, whose release
 * AWTK drops) ends there, so the bar is not found pressed on the way back. */
void tilt_poll(void) {
    if (!tilt.win || tilt.win != window_manager_get_top_window(window_manager())) {
        press.held = 0;
        return;
    }
    unsigned sign = press_sign();
    if (sign != press.shown) {
        press.shown = sign;
        widget_invalidate_force(tilt.win, &tilt.box);
    }
    if (!wheel_held && tilt.p && !tilt.at) tilt.from = tilt.p, tilt.at = (unsigned)time_now_ms();
    if (wheel_held) tilt.at = 0;
    int p = tilt_now();
    if (p == tilt.p) {
        if (!p) tilt.at = 0;
        return;
    }
    tilt.p = p;
    widget_invalidate_force(tilt.win, &tilt.box);
}

/* While the bar leans, before it paints: the canvas's clip (saved in old) widened up and down to
 * the lean's box (tilt.box, which tilt_poll repaints), into the black under the status row's
 * glyphs, so the lift, growth and shadow of a list's first or last row are not cut at the list's
 * edge; never past frame, the clip the list's own surface clip was cut from (row_paint.c
 * clip_surface: this pass's dirty area). Past it the bar would cover rows' words this pass never
 * paints again: any small repaint over the list while the bar leans (the art pane's drift, a
 * clock tick) left the lit row's words under the bar on every other frame. 0, the clip
 * untouched, when nothing leans. */
int tilt_clip(void *canvas, int *old, const int *frame) {
    if (!tilt.p || !tilt.win) return 0;
    canvas_get_clip_rect(canvas, old);
    int wy = I(tilt.win, W_Y), top = wy + tilt.box.y, foot = top + tilt.box.h;
    if (top < 0) top = 0;
    int y0 = old[1] < top ? old[1] : top, y1 = old[1] + old[3] > foot ? old[1] + old[3] : foot;
    if (y0 < frame[1]) y0 = frame[1];
    if (y1 > frame[1] + frame[3]) y1 = frame[1] + frame[3];
    int clip[4] = { old[0], y0, old[2], y1 > y0 ? y1 - y0 : 0 };
    canvas_set_clip_rect(canvas, clip);
    return 1;
}

/* a's ease, 1 - (1 - a)^2 (quadratic out), both in 256ths */
static int ease(int a) { return motion_ease(a * 1000 / 256, EASE_QUAD_OUT) * 256 / 1000; }

/* Before the bar paints (navigation.c paint_selection): bar sized by the press (its rest lift,
 * PRESS_FOCUS, under no press), moved and grown by the lean, and the shadow under it: the rest
 * lift's PRESS_LIFT, pulled in to a quarter as the press goes down, deepened by a lean. w's bar is
 * the one tilt_poll watches from now. */
void tilt_bar(void *w, void *canvas, rect_t *bar, unsigned accent, int radius) {
    if (w != tilt.w) tilt.w = w, tilt.p = 0, tilt.at = 0, tilt.end = 0, press.held = 0;
    tilt.win = window_of(w);
    int x = 0, y = 0; /* w's place in its window */
    for (void *q = w; q && q != tilt.win; q = P(q, W_PARENT)) x += I(q, W_X), y += I(q, W_Y);
    tilt.box = (rect_t){ x + bar->x - TILT_MARGIN, y + bar->y - TILT_MARGIN,
                         bar->w + 2 * TILT_MARGIN, bar->h + 2 * TILT_MARGIN };
    press.shown = press_sign();
    int p = tilt.p, a = p < 0 ? -p : p > 256 ? 256 : p, e = ease(a > 256 ? 256 : a);
    int sc = press_scale(), grow = sc - 1000 + TILT_GROW * e / 256; /* thousandths */
    /* the rest lift's shadow, 1/4 of it at PRESS_DOWN and all of it from PRESS_FOCUS on */
    int k = 0;
    if (sc != 1000) {
        int lift = 250 + 750 * (sc - PRESS_DOWN) / (PRESS_FOCUS - PRESS_DOWN);
        k = PRESS_LIFT * (lift > 1000 ? 1000 : lift < 250 ? 250 : lift) / 1000;
    }
    k += (256 - k) * e / 256;
    if (!grow && !k) return;
    int dir = p < 0 ? -1 : 1, gw = bar->w * grow / 1000, gh = bar->h * grow / 1000, rest = bar->y;
    bar->x -= gw / 2, bar->w += gw;
    bar->y += dir * TILT_LIFT * e / 256 - gh / 2, bar->h += gh;
    /* never higher past its window's top than it rests, where the status row's window covers it: the
     * first row's bar grows down there instead of being cut (tilt_clip widens the clip for the
     * rest). A row resting above its window's top leans from where it rests: brought down to the
     * window's top it would stand up to that rise below its words, the bar sliding off its row on
     * every tug. A list in no
     * window (being built or torn down) paints only its rest lift, with no top to keep under. */
    if (tilt.win) {
        int oy = I(canvas, CANVAS_Y), wy = I(tilt.win, W_Y); /* the bar's origin and its window */
        int top = wy - oy < rest ? wy - oy : rest;
        if (bar->y < top) bar->y = top;
    }
    /* the box tilt_poll paints again reaches the bar's foot and its shadow wherever it now is, a
     * full lean's outermost shadow ring a px past the MARGIN round its row */
    int foot = y + bar->y + bar->h + TILT_MARGIN;
    if (foot > tilt.box.y + tilt.box.h) tilt.box.h = foot - tilt.box.y;
    for (int i = TILT_SHADOW; i > 0 && k; --i) { /* outermost first, each fainter */
        rect_t s = { bar->x - i, bar->y + i * 2 * k / 256, bar->w + 2 * i, bar->h + i };
        unsigned alpha = (unsigned)(TILT_SHADOW_A * k / 256 / (i + 1));
        fill_box(canvas, &s, (alpha << 24) | (RGBA(accent) & 0xffffffu), (unsigned)(radius + i));
    }
}

/* After the bar paints: its gleam, a white glow whose centre slides against the lean, laid row by
 * row inside the bar's rounded ends in TILT_GLEAM_LEVELS steps of its falloff. */
void tilt_gleam(void *canvas, const rect_t *bar, int radius) {
    int p = tilt.p, a = p < 0 ? -p : p > 256 ? 256 : p, e = ease(a > 256 ? 256 : a);
    if (!e) return;
    int dir = p < 0 ? -1 : 1, rx = bar->w / 2, ry = bar->h * 69 / 100;
    int cy = bar->h / 2 - dir * bar->h * (92 + 147 * e / 256) / 256; /* 0.36 + 0.58 e of h */
    /* opacity 0.15 to 1 over the lean, of the centre's 0.42 white, shared by the levels */
    unsigned step = (unsigned)(TILT_GLEAM_A * (38 + 218 * e / 256) / 256 / TILT_GLEAM_LEVELS);
    canvas_set_fill_color(canvas, step << 24 | (RGBA(0xffffff) & 0xffffffu));
    for (int j = 0; j < bar->h; ++j) {
        int in = round_inset(radius, j, bar->h), dy = (j - cy) * 256 / ry;
        for (int k = 1; k <= TILT_GLEAM_LEVELS; ++k) {
            int r = 256 - k * 256 / (TILT_GLEAM_LEVELS + 1), q = r * r - dy * dy;
            if (q <= 0) break;
            int half = 0;
            for (int bit = 256; bit; bit >>= 1) /* q's square root, a bit at a time */
                if ((half + bit) * (half + bit) <= q) half += bit;
            half = half * rx / 256;
            int x0 = bar->x + bar->w / 2 - half, x1 = bar->x + bar->w / 2 + half;
            if (x0 < bar->x + in) x0 = bar->x + in;
            if (x1 > bar->x + bar->w - in) x1 = bar->x + bar->w - in;
            if (x1 > x0) canvas_fill_rect(canvas, x0, bar->y + j, x1 - x0, 1);
        }
    }
}
