/* Holding Previous or Next (keys 222 and 223) scans the playing track, faster the longer the
 * button stays down (docs/ipod.md#previous-and-next, docs/internals.md#scan). With q2player
 * playing a song it can stretch (its SCAN_FILE), the song is heard running at 2, then 4, then 8
 * times its speed at its own pitch ({mcl-scan\HUNDREDTHS}, player/stretch.c), and past that in
 * jumps of growing length, each played at 4 times. Previous steps back as a CD player does,
 * each stretch heard forward at twice the speed, the steps growing to the same speeds; with
 * SEEK_REWIND_SNIPPETS 0 it runs the song backward instead, as Next runs it forward.
 * Otherwise (the stock player, a paused song, DSD) it scans as an iPod's click wheel does: a
 * jump, a short stretch at the song's own speed, another jump, the jumps growing. A press let go
 * before SEEK_HOLD_MS is a tap and stays stock's: Next changes track, Previous restarts the track
 * or goes back one (ringnav_seek_start). */
#include "navigation.h"

#define SEEK_HOLD_MS 500        /* a press held this long scans instead of skipping */
#define SEEK_CYCLE_MS 500       /* one jump and the stretch heard after it */
#define SEEK_LOST_MS 700        /* no repeat for this long: the release was lost */
#define SEEK_CAR_LOST_MS 2000   /* the car's scan (seek_car): no word from the car for this long, the release was lost */
#define SCAN_FILE "/tmp/q2scan" /* player/q2player.h: there while q2player can scan the song */
/* 1: Previous steps back by stretches played forward; 0: it runs the song backward */
#ifndef SEEK_REWIND_SNIPPETS
#define SEEK_REWIND_SNIPPETS 1
#endif

/* A scan's phases: from `after` ms held past SEEK_HOLD_MS, `times` the song's speed overall;
 * `play` the speed the player runs at, in hundredths (mcl-scan, below 0 backward; 0 its own);
 * `jump` set where each cycle also seeks on by times / 2 seconds, so the scan covers `times`. */
typedef struct {
    unsigned short after;
    signed char times;
    short play;
    unsigned char jump;
} seek_phase_t;
#define PHASES 5
static const seek_phase_t seek_phases[][PHASES] = {
    /* SEEK_PLAN_JUMPS: the stock player, a pause, DSD: 4, 8, 16 then 32 times (the last twice:
     * every row has PHASES) */
    { { 0, 4, 0, 1 },
      { 3000, 8, 0, 1 },
      { 6000, 16, 0, 1 },
      { 10000, 32, 0, 1 },
      { 10000, 32, 0, 1 } },
    /* SEEK_PLAN_FORWARD: heard at 2, 4 and 8 times, then jumps heard at 4 */
    { { 0, 2, 200, 0 },
      { 2000, 4, 400, 0 },
      { 4000, 8, 800, 0 },
      { 6000, 16, 400, 1 },
      { 10000, 32, 400, 1 } },
    /* SEEK_PLAN_BACKWARD: the same, backward */
    { { 0, 2, -200, 0 },
      { 2000, 4, -400, 0 },
      { 4000, 8, -800, 0 },
      { 6000, 16, -400, 1 },
      { 10000, 32, -400, 1 } },
    /* SEEK_PLAN_SNIPPETS: steps back, each heard forward at twice the speed */
    { { 0, 2, 200, 1 },
      { 2000, 4, 200, 1 },
      { 4000, 8, 200, 1 },
      { 6000, 16, 200, 1 },
      { 10000, 32, 200, 1 } },
};
enum { SEEK_PLAN_JUMPS, SEEK_PLAN_FORWARD, SEEK_PLAN_BACKWARD, SEEK_PLAN_SNIPPETS };

/* key: the button held (0 none); scanning once the hold began; at its press and its last key-down
 * (the Q2's buttons repeat theirs while held); to the track second the scan has reached, of the
 * record at queue place track; edge set at the track's start or end, where it waits for another
 * track (Next) or for the release (Previous); eat the key whose release ringnav stops; plan, a
 * row of seek_phases; sent, the mcl-scan the player was last sent (0: none, or its own speed);
 * live while the phase is heard at speed without jumps. */
static struct {
    unsigned key, at, seen, timer, track, eat;
    int scanning, to, edge, paused, total, plan, sent, live, car;
} sk __attribute__((section(".scratch")));

static const seek_phase_t *seek_phase(int plan, unsigned held_ms) {
    const seek_phase_t *p = seek_phases[plan];
    for (int i = 1; i < PHASES; ++i)
        if (held_ms >= seek_phases[plan][i].after) p = &seek_phases[plan][i];
    return p;
}

/* The track seconds each jump of the stock player's scan moves. */
int seek_step(unsigned held_ms) { return seek_phase(SEEK_PLAN_JUMPS, held_ms)->times / 2; }

/* The plan for a scan one way (dir 1 forward): heard at speed when the song plays on q2player and
 * it can stretch the song. */
int seek_plan(int dir, int paused) {
    if (paused || access(SCAN_FILE, 0)) return SEEK_PLAN_JUMPS; /* F_OK */
    return dir > 0                ? SEEK_PLAN_FORWARD
           : SEEK_REWIND_SNIPPETS ? SEEK_PLAN_SNIPPETS
                                  : SEEK_PLAN_BACKWARD;
}

/* The player run at play hundredths of the song's speed, unless it already is. Never waits on
 * the player, nor dies with it (MSG_DONTWAIT | MSG_NOSIGNAL), as patch/clicker.c's clicks. */
static void seek_send(int play) {
    if (play == sk.sent) return;
    char m[24];
    int fd = *(volatile int *)MCL_FD, n = tk_snprintf(m, sizeof m, "{mcl-scan\\%d}", play);
    if (fd != -1) send(fd, m, (unsigned)n, 0x4040);
    sk.sent = play;
}

/* The next target from `to`, a step one way, within a track of `total` seconds: Next stops a second
 * short of the end, where the track runs out as it would anyway, Previous at its start. */
int seek_target(int to, int dir, int step, int total) {
    to += dir * step;
    if (total > 0 && to > total - 1) to = total - 1;
    return to < 0 ? 0 : to;
}

/* A seek of a paused track to `to` track seconds (docs/internals.md#scrub-ipod): the player alone is sent there,
 * in file seconds, and the track stays paused, Play going on from the target; stock's player_seek_time and
 * player_seek_start would resume it (config_outputchannel, then mclSetResume). A target past the track's last second
 * stops on it, as a scan does, so the player stays on the track. 1 when sent; 0 when nothing is paused. */
int seek_paused(int to) {
    unsigned at, n;
    void *r = mclGetPlayStatus() == 3 ? queue_now(&at, &n) : 0;
    if (!r) return 0;
    int sec = -1, total = 0;
    player_playtime_and_length(&sec, &total);
    mclSetSeek(seek_target(to, 0, 0, total) + I(r, REC_CUE_START));
    return 1;
}

/* Whether a hold here scans. The track must be a local one (a folder or the library: no stream,
 * Spotify, radio or video owns the buttons) playing or paused, and the buttons must not be another
 * page's: a game's, the playlist editor's, a time field's, the letter strip's, or a dialog's (the
 * restart toast's among them) other than the volume's. With the screen off the buttons work as
 * stock lets them, so not under a Buttons lock that holds them (modes 2 and 3). */
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
    if (search_strip_owns(top)) return 0; /* the letter strip's: Previous held erases there */
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
    seek_send(0); /* the player plays on at its own speed from what was heard */
    if (sk.scanning && !sk.edge) {
        int cue, sec = -1, total;
        if (sk.live) seek_now(&cue, &sec, &total);
        np_seek_bar(2, sk.live && sec >= 0 ? sec : sk.to, sk.paused);
    }
    sk.scanning = sk.edge = sk.live = sk.car = 0;
    sk.key = 0;
}

/* One cycle of the scan. Heard at speed, the player runs on its own and the bar follows its
 * second; at the track's start a backward run turns and plays on from there. Otherwise one jump:
 * the player goes to the target and plays on from it until the next, or, paused, only the bar
 * moves and the release sends the seek. At an end the scan waits: Next for the following track,
 * which the player starts as it would at the end of any track, Previous for the release. */
int seek_tick(void) {
    unsigned now = (unsigned)time_now_ms();
    if (!sk.key || now - sk.seen > (sk.car ? SEEK_CAR_LOST_MS : SEEK_LOST_MS)) {
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
        sk.plan = seek_plan(dir, sk.paused);
        np_seek_bar(0, sk.to, sk.paused);
    }
    if (sk.edge) return 8;
    sk.paused = status == 3;
    if (sk.paused) sk.plan = SEEK_PLAN_JUMPS; /* paused mid-scan: nothing more is heard */
    sk.total = total;
    const seek_phase_t *p = seek_phase(sk.plan, now - sk.at - SEEK_HOLD_MS);
    seek_send(p->play);
    if (!p->jump) {
        sk.live = 1;
        sk.to = sec < 0 ? sk.to : sec;
        np_seek_bar(1, sk.to, sk.paused);
        if (dir < 0 && sk.to == 0) {
            seek_send(0);
            np_seek_bar(2, 0, sk.paused);
            sk.edge = 1;
        }
        return 8;
    }
    sk.live = 0;
    int to = seek_target(sk.to, dir, p->times / 2, total);
    if (!sk.paused) mclSetSeek(to + cue);
    sk.to = to;
    np_seek_bar(1, to, sk.paused);
    if ((dir > 0 && total > 0 && to >= total - 1) || (dir < 0 && to == 0)) {
        /* playing on from the edge (at the start, forward at its own speed): the bar goes back
         * to the player's second */
        if (dir < 0) seek_send(0);
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

/* A scrub (navigation.c np_key; docs/internals.md#scrub-ipod) is heard as it goes. While the track
 * plays, each move of the target sends the player there, at most every SCRUB_SEEK_MS, and the
 * latest target once that wait is over, so a fast spin sends a few seeks, not one a tick. Paused,
 * nothing is sent until the scrub ends, which seeks once and stays paused (seek_position). */
#define SCRUB_SEEK_MS 200
static struct {
    unsigned at, timer;
    int to, sent, live;
} sc __attribute__((section(".scratch")));

static void scrub_send(void) {
    unsigned at, n;
    void *r = queue_now(&at, &n);
    sc.at = (unsigned)time_now_ms();
    sc.sent = sc.to;
    sc.live = 1;
    if (r) mclSetSeek(sc.to + I(r, REC_CUE_START));
}

static int scrub_due(const void *info) {
    (void)info;
    sc.timer = 0;
    if (sc.to != sc.sent && mclGetPlayStatus() == 2) scrub_send();
    return 0;
}

void seek_scrub(int to) {
    if (mclGetPlayStatus() != 2) return;
    sc.to = to;
    if (sc.timer) return; /* sent when it fires */
    unsigned since = (unsigned)time_now_ms() - sc.at;
    if (!sc.live || since >= SCRUB_SEEK_MS)
        scrub_send();
    else
        sc.timer = timer_add(scrub_due, (void *)0, SCRUB_SEEK_MS - since);
}

/* The scrub's end, its target `to` committed when `seek`: 1 when the track plays and the player
 * followed the scrub, sent there now unless the last seek already went there; 0 leaves the commit
 * to the caller's seek_position: paused, or a target the wheel never moved live (a chapter row's).
 */
int seek_scrub_end(int to, int seek) {
    stop_timer(&sc.timer);
    int live = sc.live;
    sc.live = 0;
    if (!seek || !live || mclGetPlayStatus() != 2) return 0;
    if (sc.sent != to) {
        sc.to = to;
        scrub_send();
        sc.live = 0;
    }
    return 1;
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

/* The car's Next or Previous held (patch/iap2_hid.c, through Car Remote's shim: coda_rc_shim.c "scan"): dir 1 or -1
 * scans as a button held past SEEK_HOLD_MS does, from now, the player's own scan (mcl-scan) heard at speed where it
 * can; the car's word again within SEEK_CAR_LOST_MS keeps it going (its button stays down); dir 0 is the release,
 * and the song plays on from where the scan reached. The car held its buttons in the first drive (2026-11-20) through
 * a seek every 500 ms, which jumped unevenly; this is the scan the Q2's own buttons make. A key of the Q2's own
 * already scanning is left alone. */
void seek_car(int dir) {
    unsigned now = (unsigned)time_now_ms(), key = dir > 0 ? KEY_FWD_BTN : KEY_BACK_BTN;
    if (!dir) {
        if (sk.car) seek_stop();
        return;
    }
    if (sk.key) {
        if (sk.car && key == sk.key) sk.seen = now;
        return;
    }
    if (!seek_allowed()) return;
    sk.key = key, sk.car = 1, sk.eat = 0;
    sk.at = now - SEEK_HOLD_MS, sk.seen = now;
    if (seek_tick()) rearm(&sk.timer, seek_timer, SEEK_CYCLE_MS);
}

/* on_wm_keylong_fun (ringnav_keylong): a scan's long press does nothing more. */
int seek_held(unsigned key) { return sk.eat && key == sk.eat; }
