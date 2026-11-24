/* The UI pass watchdog (docs/internals.md#ui-pass-watchdog): a W line in the unified log for each
 * UI loop pass whose work took UI_LATE_MS or more while the screen was lit, naming the step a
 * caller marked last in that pass (ui_watch_step), so a frozen spinner or a wheel that stops for a
 * moment is named on the next real use, and where its time went: its events, timers and paint
 * (stock's part, and the payload's handlers and timers inside it), and ringnav_sleep's polls with
 * the longest of them (ui_watch_poll), so a pass whose step is unnamed still says which half and
 * which poll held it. A pass's work is the time from the end of its sleep (stock's
 * main_loop_sleep, after ringnav_sleep's loop_wait) through its events, timers and paint to the
 * next ringnav_sleep, plus that call's polls; the sleeps are not counted. At most one line a
 * UI_LOG_MS, the passes left out counted in the next. Nothing changes what a pass does. */
#include "navigation.h"

#define UI_LATE_MS 150 /* a pass this long is a hitch a user sees: 9 frames at 60 Hz */
#define UI_LOG_MS 1000 /* the least time between two lines */

static struct {
    unsigned woke;    /* time_now_ms() its sleep ended, 0 before the first */
    unsigned entered; /* ringnav_sleep's start this pass */
    unsigned work;    /* the pass's work before ringnav_sleep */
    unsigned logged;  /* the last line's time, 0 for none */
    unsigned missed;  /* late passes not logged since */
    const char *step; /* the step marked last this pass, 0 for none */
    const char *poll, *slow; /* the poll running since poll_at; the longest so far this pass */
    unsigned poll_at, slow_ms;
} uw __attribute__((section(".scratch")));

/* A caller's step starts (a string constant): the late pass it is in names it. */
void ui_watch_step(const char *step) { uw.step = step; }

/* The poll running since uw.poll_at ends at now: kept if the longest this pass. */
static void poll_end(unsigned now) {
    if (uw.poll && now - uw.poll_at >= uw.slow_ms) uw.slow = uw.poll, uw.slow_ms = now - uw.poll_at;
    uw.poll = 0;
}
/* ringnav_sleep's start: the work since the sleep before it ended. */
void ui_watch_enter(void) {
    unsigned now = (unsigned)time_now_ms();
    uw.work = uw.woke ? now - uw.woke : 0;
    uw.entered = now;
    uw.poll = uw.slow = 0, uw.slow_ms = 0;
}
/* ringnav_sleep's next poll starts (its name, a string constant), the one before it ending. */
void ui_watch_poll(const char *name) {
    unsigned now = (unsigned)time_now_ms();
    poll_end(now);
    uw.poll = name, uw.poll_at = now;
}

/* ringnav_sleep's polls are done, its wait not begun: the pass's work, logged when late. */
void ui_watch_polled(void) {
    unsigned now = (unsigned)time_now_ms(), ms = uw.work + (now - uw.entered);
    const char *step = uw.step;
    poll_end(now);
    uw.step = 0;
    if (ms < UI_LATE_MS || !g_backlight_status || !uw.woke) return;
    if (uw.logged && now - uw.logged < UI_LOG_MS) {
        ++uw.missed;
        return;
    }
    char msg[160];
    tk_snprintf(msg, sizeof msg, "ui pass %u ms, step %s, %u more late since; events %u ms, polls %u ms",
                ms, step ? step : "-", uw.missed, uw.work, ms - uw.work);
    if (uw.slow)
        tk_snprintf(msg + strlen(msg), sizeof msg - strlen(msg), " (longest %s %u ms)", uw.slow,
                    uw.slow_ms);
    ulog('W', "ui", msg);
    uw.logged = now | !now, uw.missed = 0;
}

/* stock's sleep has returned: the next pass's work starts. */
void ui_watch_woke(void) {
    unsigned now = (unsigned)time_now_ms();
    uw.woke = now | !now;
}
