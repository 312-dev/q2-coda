/* Tug-to-tilt (docs/oled-design.md#selection-bar, approved on 2026-11-15 from the mock-up
 * design/mockups/tilt-menu.html): while a finger turns the wheel short of its next tick, the
 * selection bar leans toward the row it is heading for, by the way it has come (wheel_part):
 * eased 1 - (1 - a)^2, it moves up to TILT_LIFT px that way, grows up to TILT_GROW thousandths,
 * casts an accent shadow under it and carries a white gleam that slides the other way. The tick
 * itself is unchanged (the wheel's travel per tick, Wheel Sensitivity's): past it the selection
 * steps a row and the next tug starts from the leftover travel. Let go short of it and the bar
 * springs back over TILT_SPRING_MS. The mock-up's tilt of 1 degree is under a pixel on a 32 px
 * row, so it is not drawn. Only the bar's own box is painted again (tilt.box): tilt_poll, from
 * each UI loop pass, invalidates it in its window while the bar moves and that window is on top,
 * and tilt_clip lets the bar paint all of that box, past its list's edge, down to the status row.
 * ponytail: a touch drag scrolls the list with the bar hidden, as every list does under touch
 * (navigation.c hide_outline), so only the wheel tilts it; a drag that stepped rows would be what
 * lifts that. */
#include "navigation.h"
#include "playback.h"

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
} tilt __attribute__((section(".scratch")));

/* The lean to draw now: the wheel's while a finger holds it, else a spring back to 0. */
static int tilt_now(void) {
    int part = wheel_part();
    if (part || wheel_held) return part;
    if (!tilt.at) return 0;
    unsigned t = (unsigned)time_now_ms() - tilt.at;
    if (t >= TILT_SPRING_MS) return 0;
    int k = 256 - (int)(t * 256 / TILT_SPRING_MS); /* cubic-bezier(.2,0,0,1), near enough */
    return tilt.from * (k * k / 256 * k / 256) / 256;
}

/* Each UI loop pass: the bar's box painted again while its lean changes; a lift starts a spring. */
void tilt_poll(void) {
    if (!tilt.win || tilt.win != window_manager_get_top_window(window_manager())) return;
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
 * the lean's box (tilt.box, which tilt_poll repaints) but never above the status row, so the lift,
 * growth and shadow of a list's first or last row are not cut at the list's edge. 0, the clip
 * untouched, when nothing leans. */
int tilt_clip(void *canvas, int *old) {
    if (!tilt.p || !tilt.win) return 0;
    canvas_get_clip_rect(canvas, old);
    int wy = I(tilt.win, W_Y), top = wy + tilt.box.y, foot = top + tilt.box.h;
    if (top < geom_val(V_BAR)) top = geom_val(V_BAR);
    int y0 = old[1] < top ? old[1] : top, y1 = old[1] + old[3] > foot ? old[1] + old[3] : foot;
    int clip[4] = { old[0], y0, old[2], y1 - y0 };
    canvas_set_clip_rect(canvas, clip);
    return 1;
}

/* a's ease, 1 - (1 - a)^2, both in 256ths */
static int ease(int a) { return 256 - (256 - a) * (256 - a) / 256; }

/* Before the bar paints (navigation.c paint_selection): bar moved and grown by the lean, and the
 * shadow under it. w's bar is the one tilt_poll watches from now. */
void tilt_bar(void *w, void *canvas, rect_t *bar, unsigned accent, int radius) {
    if (w != tilt.w) tilt.w = w, tilt.p = 0, tilt.at = 0;
    tilt.win = window_of(w);
    int x = 0, y = 0; /* w's place in its window */
    for (void *q = w; q && q != tilt.win; q = P(q, W_PARENT)) x += I(q, W_X), y += I(q, W_Y);
    tilt.box = (rect_t){ x + bar->x - TILT_MARGIN, y + bar->y - TILT_MARGIN,
                         bar->w + 2 * TILT_MARGIN, bar->h + 2 * TILT_MARGIN };
    int p = tilt.p, a = p < 0 ? -p : p > 256 ? 256 : p, e = ease(a > 256 ? 256 : a);
    if (!e) return;
    int dir = p < 0 ? -1 : 1, gw = bar->w * TILT_GROW * e / 256000,
        gh = bar->h * TILT_GROW * e / 256000;
    bar->x -= gw / 2, bar->w += gw;
    bar->y += dir * TILT_LIFT * e / 256 - gh / 2, bar->h += gh;
    for (int i = TILT_SHADOW; i > 0; --i) { /* outermost first, each fainter */
        rect_t s = { bar->x - i, bar->y + i * 2 * e / 256, bar->w + 2 * i, bar->h + i };
        unsigned alpha = (unsigned)(TILT_SHADOW_A * e / 256 / (i + 1));
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
