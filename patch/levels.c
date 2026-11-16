/* The levels the wheel sets, one way each (docs/internals.md#wheel-volume): a tick's step at the
 * wheel's speed (wheel_level, vol_accel's curve) and the bar's ease after it (level_ease). The
 * volume takes them through vol_next (volume.c), on Now Playing's dialog and Control Center's
 * slider alike; the brightness through bright_wheel, on Control Center's slider and Settings'
 * Brightness page alike. */
#include "navigation.h"

/* The brightness bar's ease (bright_wheel), drawn by Control Center and the Brightness page */
level_ease_t bright_bar __attribute__((section(".scratch")));

/* value a tick on (dir 1 up, -1 down): vol_accel's steps, scaled so a range of full takes the ticks
 * the volume's 100 takes, at least 1; up to hi (never down to it from above), down to lo. */
int wheel_level(int value, int lo, int hi, int full, int dir) {
    int n = (vol_accel() * full + 50) / 100;
    if (n < 1) n = 1;
    if (dir > 0) return value >= hi ? value : value + n > hi ? hi : value + n;
    return value <= lo ? value : value - n < lo ? lo : value - n;
}

/* A move of the level from from: the bar's ease starts there, unless it is still easing. */
void level_ease_from(level_ease_t *e, int from) {
    if (e->easing) return;
    e->shown = from << 8;
    e->easing = 1;
    e->at = (unsigned)time_now_ms();
}
/* The bar's level towards level while easing: a first-order follow with time constant
 * VOL_EASE_MS, by the time since the last move, so a slow pass jumps further rather than lagging;
 * 1 while it moved. Within a 1/256 step of level it lands and stops. */
int level_ease(level_ease_t *e, int level) {
    if (!e->easing) return 0;
    unsigned now = (unsigned)time_now_ms();
    int dt = (int)(now - e->at), d = (level << 8) - e->shown;
    e->at = now;
    int step = dt > 0 ? d * dt / (VOL_EASE_MS + dt) : 0;
    if (!step && dt > 0) step = d > 0 ? 1 : -1; /* the last few 1/256ths */
    if (d > -2 && d < 2) step = d, e->easing = 0;
    e->shown += step;
    return step != 0 || !e->easing;
}
/* The level a bar draws, in 1/256 steps: the eased one, or level once it has landed. */
int level_shown(const level_ease_t *e, int level) { return e->easing ? e->shown : level << 8; }

#define LEVEL_TICK "_level_tick" /* 1 while level_tick runs on the window */
/* Every VOL_POLL_MS while a bar eases on win (Control Center, the Brightness page): the volume's
 * bar towards g_volume and the brightness's towards win's slider, win drawn again while either
 * moved; it ends once both have landed. */
static int level_tick(const void *info) {
    void *win = P(info, TIMER_CTX), *s = widget_lookup(win, "slider_backlight", 1);
    int moved = level_ease(&vol_bar, g_volume);
    if (s) moved |= level_ease(&bright_bar, widget_get_prop_int(s, "value", 0));
    if (moved) widget_invalidate_force(win, (void *)0);
    if (vol_bar.easing || (s && bright_bar.easing)) return 8; /* RET_REPEAT */
    widget_set_prop_int(win, LEVEL_TICK, 0);
    return 7; /* RET_REMOVE */
}
/* win's bars followed by level_tick until they land. */
void level_follow(void *win) {
    if (!win || widget_get_prop_int(win, LEVEL_TICK, 0)) return;
    widget_set_prop_int(win, LEVEL_TICK, 1);
    widget_add_timer(win, level_tick, VOL_POLL_MS);
}

/* A wheel tick (dir 1 up, -1 down) on a brightness slider, Control Center's or the Brightness
 * page's: its value moved by wheel_level's steps over its range, which fires stock's change
 * callback on either (config_lightness: the backlight set and saved, as a drag's release or the
 * page's +/- do); the bar eases after it. 1 when it moved. */
int bright_wheel(void *slider, int dir) {
    int value = widget_get_prop_int(slider, "value", 0),
        min = widget_get_prop_int(slider, "min", 0), max = widget_get_prop_int(slider, "max", 100),
        to = wheel_level(value, min, max, max - min, dir);
    if (to == value) return 0;
    level_ease_from(&bright_bar, value);
    widget_set_prop_int(slider, "value", to);
    level_follow(window_of(slider));
    return 1;
}
