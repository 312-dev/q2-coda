/* Holding Previous or Next (keys 222 and 223) scans the playing track as an iPod's click wheel
 * does: a short stretch of the song at its own speed and pitch, a jump, another stretch, the jumps
 * growing the longer the button stays down (docs/ipod.md#previous-and-next,
 * docs/internals.md#scan). A press let go before SEEK_HOLD_MS is a tap and stays stock's: Next
 * changes track, Previous restarts the track or goes back one (ringnav_seek_start). */
#include "navigation.h"

#define SEEK_HOLD_MS 500  /* a press held this long scans instead of skipping */
#define SEEK_CYCLE_MS 500 /* one jump and the stretch heard after it */
#define SEEK_LOST_MS 700  /* no repeat for this long: the release was lost */

/* Track seconds each jump moves while the button has been held at least `after` ms past
 * SEEK_HOLD_MS: 4, 8, 16 then 32 times the song's speed. */
static const struct {
    unsigned after;
    int step;
} seek_steps[] = { { 0, 2 }, { 3000, 4 }, { 6000, 8 }, { 10000, 16 } };

/* key: the button held (0 none); scanning once the hold began; at its press and its last key-down
 * (the Q2's buttons repeat theirs while held); to the track second the scan has reached, of the
 * record at queue place track; edge set at the track's start or end, where it waits for another
 * track (Next) or for the release (Previous); eat the key whose release ringnav stops. */
static struct {
    unsigned key, at, seen, timer, track, eat;
    int scanning, to, edge, paused, total;
} sk __attribute__((section(".scratch")));

int seek_step(unsigned held_ms) {
    int step = seek_steps[0].step;
    for (unsigned i = 0; i < sizeof seek_steps / sizeof *seek_steps; ++i)
        if (held_ms >= seek_steps[i].after) step = seek_steps[i].step;
    return step;
}

/* The next target from `to`, a step one way, within a track of `total` seconds: Next stops a second
 * short of the end, where the track runs out as it would anyway, Previous at its start. */
int seek_target(int to, int dir, int step, int total) {
    to += dir * step;
    if (total > 0 && to > total - 1) to = total - 1;
    return to < 0 ? 0 : to;
}

/* Whether a hold here scans. The track must be a local one (a folder or the library: no stream,
 * Spotify, radio or video owns the buttons) playing or paused, and the buttons must not be another
 * page's: a game's, the playlist editor's, a time field's, or a dialog's (the restart toast's among
 * them) other than the volume's. With the screen off the buttons work as stock lets them, so not
 * under a Buttons lock that holds them (modes 2 and 3). */
static int seek_allowed(void) {
    static const char *const own[] = { "games_page", "playlistedit_page", "manualtime_page",
                                       "sleepshutdown_page" };
    if (!g_backlight_status) {
        if (g_keylock_flag && (g_keylock_mode == 2 || g_keylock_mode == 3)) return 0;
    } else if (!usable())
        return 0;
    if (g_lockscreen_pageflag || g_testmode_flag || g_guideflag || g_poweroff_state) return 0;
    if (video_on() || addon_game_on() || media_owner() != 1 || media_focused()) return 0;
    int cls = *(volatile int *)MCL_TYPE, status = mclGetPlayStatus();
    if ((cls != 1 && (cls & 0xf000) != 0xf000) || (status != 2 && status != 3)) return 0;
    void *top = window_manager_get_top_window(window_manager());
    const char *name = top ? widget_get_prop_str(top, "name", "") : "";
    if (!top || (!tk_strcmp(widget_get_type(top), "dialog") && tk_strcmp(name, "volume_dialog")))
        return 0;
    for (unsigned i = 0; i < sizeof own / sizeof *own; ++i)
        if (!tk_strcmp(name, own[i])) return 0;
    return 1;
}

/* The playing record's queue place plus one (0: none), its CUE start, and the player's second and
 * the track's length. */
static unsigned seek_now(int *cue, int *sec, int *total) {
    unsigned at, n;
    void *r = queue_now(&at, &n);
    *sec = -1;
    *total = 0;
    player_playtime_and_length(sec, total);
    *cue = r ? I(r, REC_CUE_START) : 0;
    return r ? at + 1 : 0;
}

static void seek_stop(void) {
    stop_timer(&sk.timer);
    if (sk.scanning && !sk.edge) np_seek_bar(2, sk.to, sk.paused);
    sk.scanning = sk.edge = 0;
    sk.key = 0;
}

/* One jump: the player goes to the target and plays on from it until the next, or, paused, only
 * the bar moves and the release sends the seek. At an end the scan waits: Next for the following
 * track, which the player starts as it would at the end of any track, Previous for the release. */
int seek_tick(void) {
    unsigned now = (unsigned)time_now_ms();
    if (!sk.key || now - sk.seen > SEEK_LOST_MS) {
        seek_stop();
        return 0;
    }
    int cue, sec, total, status = mclGetPlayStatus();
    unsigned track = seek_now(&cue, &sec, &total);
    if (!track || (status != 2 && status != 3)) {
        seek_stop();
        return 0;
    }
    int dir = sk.key == KEY_FWD_BTN ? 1 : -1;
    if (!sk.scanning || track != sk.track) {  /* the start, or the next track after an end */
        if (sk.scanning && dir < 0) return 8; /* RET_REPEAT: Previous waits at the start */
        sk.scanning = 1;
        sk.edge = 0;
        sk.track = track;
        sk.to = sec < 0 ? 0 : sec;
        sk.paused = status == 3;
        np_seek_bar(0, sk.to, sk.paused);
    }
    if (sk.edge) return 8;
    int to = seek_target(sk.to, dir, seek_step(now - sk.at - SEEK_HOLD_MS), total);
    sk.paused = status == 3;
    sk.total = total;
    if (!sk.paused) mclSetSeek(to + cue);
    sk.to = to;
    np_seek_bar(1, to, sk.paused);
    if ((dir > 0 && total > 0 && to >= total - 1) || (dir < 0 && to == 0)) {
        /* playing on from the edge: the bar goes back to the player's second */
        np_seek_bar(2, to, sk.paused);
        sk.edge = 1;
    }
    return 8;
}

static int seek_timer(const void *info) {
    (void)info;
    int ret = seek_tick();
    if (!ret) sk.timer = 0;
    return ret;
}

static int seek_hold(const void *info) {
    (void)info;
    sk.timer = 0;
    if (!sk.key || !seek_allowed()) {
        sk.key = 0;
        return 0;
    }
    sk.eat = sk.key; /* from here the release is the scan's, not a skip */
    if (seek_tick()) rearm(&sk.timer, seek_timer, SEEK_CYCLE_MS);
    return 0;
}

/* window_manager_dispatch_input_event (ringnav_input), before stock: 1 drops the event. A press
 * passes, so stock's input status sees it and its release; its repeats are dropped, which would
 * otherwise step Now Playing's slider (0x53e000), and SEEK_HOLD_MS on the scan starts. A release
 * passes too, for stock's input status, and ringnav stops it from skipping when it ended a scan
 * (seek_release). A press of the other button meanwhile is dropped whole. */
int seek_input(void *e) {
    unsigned type = (unsigned)I(e, EVENT_TYPE), key = (unsigned)I(e, EVENT_KEY);
    if ((type != EVT_KEY_DOWN && type != EVT_KEY_UP) || (key != KEY_FWD_BTN && key != KEY_BACK_BTN))
        return 0;
    unsigned now = (unsigned)time_now_ms();
    if (sk.key && key != sk.key) return 1;
    if (type == EVT_KEY_UP) {
        if (!sk.key) return 0;
        seek_stop();
        return 0;
    }
    if (sk.key) { /* a repeat: the button is still down */
        sk.seen = now;
        return 1;
    }
    sk.eat = 0;
    if (!seek_allowed()) return 0;
    sk.key = key;
    sk.at = sk.seen = now;
    rearm(&sk.timer, seek_hold, SEEK_HOLD_MS);
    return 0;
}

/* The key-up hook (ringnav), before the windows and stock's skip: 1 for the release of a press
 * that scanned. Now Playing's long press (0x53b4d0) marked the key held (NP_KEY_HELD), which its
 * own release handler would clear, stepping the slider at the next press if left set. */
int seek_release(unsigned key) {
    if (!sk.eat || key != sk.eat) return 0;
    sk.eat = 0;
    *(volatile unsigned char *)NP_KEY_HELD = 0;
    *(volatile unsigned char *)NP_KEY_MODE = 0;
    return 1;
}

/* on_wm_keylong_fun (ringnav_keylong): a scan's long press does nothing more. */
int seek_held(unsigned key) { return sk.eat && key == sk.eat; }
