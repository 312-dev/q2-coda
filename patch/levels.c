/* The levels the wheel sets, one way each (docs/internals.md#wheel-volume): a tick's step at the
 * wheel's speed (wheel_level, vol_accel's curve) and the bar's ease after it (level_ease). The
 * volume takes them through vol_next (volume.c), on Now Playing's dialog and Control Center's
 * slider alike. */
#include "navigation.h"

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
/* Every VOL_POLL_MS while the volume's bar eases on win (Control Center): it towards g_volume, win
 * drawn again while it moved; it ends once it has landed. */
static int level_tick(const void *info) {
    void *win = P(info, TIMER_CTX);
    if (level_ease(&vol_bar, g_volume)) widget_invalidate_force(win, (void *)0);
    if (vol_bar.easing) return 8; /* RET_REPEAT */
    widget_set_prop_int(win, LEVEL_TICK, 0);
    return 7; /* RET_REMOVE */
}
/* win's bars followed by level_tick until they land. */
void level_follow(void *win) {
    if (!win || widget_get_prop_int(win, LEVEL_TICK, 0)) return;
    widget_set_prop_int(win, LEVEL_TICK, 1);
    widget_add_timer(win, level_tick, VOL_POLL_MS);
}
