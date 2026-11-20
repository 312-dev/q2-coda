/* Cover Flow's thumb: the wheel's travel, the lists' momentum on it (wheel_accel.c), and a follower
 * the covers ride on to their landing. */
#include "coverflow.h"

cf_thumb_t th __attribute__((section(".scratch")));

/* The thumb (docs/internals.md#coverflow-thumb), every UI pass while the covers are open
 * (navigation.c thumb_poll): the wheel's raw travel, 200 units a turn, moves a goal, and the
 * position follows it, critically damped and exact for any step, so its speed stays continuous
 * whatever the wheel or a slow frame does. */
static void th_follow(float *x, float *v, float to, float tau, float ms) {
    float w = 1 / tau, y = *x - to, b = *v + w * y, q = y + b * ms, e = ff_decay(w * ms);
    *x = to + q * e;
    *v = (b - w * q) * e;
}

/* Over 4 albums a second: the album named has run ahead of the covers. */
static int th_moving(void) { return ABS(th.vel) >= .004f; }

/* Where the wheel has got to, the album the caption names and a stop lands on: ahead of the covers
 * at speed. */
static int th_aim(void) {
    float s = cfs.aim_snap / 100.f;
    if (!th_moving()) return cf_round(th.goal);
    return th.dir > 0 ? -cf_floor(s - th.goal) : cf_floor(th.goal + s);
}

static unsigned th_album(int a, unsigned n) {
    if (!n) return 0;
    int c = (I(cf.slide, SLIDE_INDEX) + a) % (int)n;
    return (unsigned)(c < 0 ? c + (int)n : c);
}

/* The album the caption names, from SLIDE_INDEX */
static int th_shown(void) { return th_aim(); }

/* At rest on album q: the slide_menu's index moves, as a glide's end does, its offset 0. */
static void th_commit(int q) {
    th.on = th.jump = th.lean = th.fold = 0, th.enter = th.fold_off = th.fold_vel = 0;
    th.lead = th.lead_v = th.vel = th.pos = th.goal = th.arch = 0;
    carousel_glide(cf.slide, -q * stride_of(cf.slide), 0, 0);
    widget_invalidate_force(cf.slide, 0);
}

/* The covers on the album named, at once: Centre, or nothing to show a landing on. */
void coverflow_wheel_land(void) {
    unsigned n = cf.albums ? deque_size(cf.albums) : 0;
    /* landed, painted or not: the landing album's window kept (fx_unletter) */
    fx_unletter(cf.slide ? (int)th_album(th.on ? th_shown() : 0, n) : 0);
    if (th.on && cf.slide) th_commit(th_shown());
    th.jump = th.lean = th.fold = 0;
    th.on = 0; /* no covers left to land: a page's load replaced them */
}
int coverflow_wheel_live(void) { return th.raw || th.on || (cfs.thumb && cf.slide); }
int coverflow_wheel_on(void *w) { return th.on && w == cf.slide; }

/* From rest, or from a touch's glide or a held tick's step, whose goal it takes over. */
static void th_grab(void *s, unsigned now) {
    void *a = P(s, SLIDE_ANIMATOR);
    float stride = (float)stride_of(s);
    th.pos = (float)-I(s, SLIDE_OFFSET) / stride;
    th.goal = a ? (float)-I(a, ANIM_X_TO) / stride : th.pos;
    if (a) {
        widget_animator_pause(a);
        widget_animator_destroy(a);
        P(s, SLIDE_ANIMATOR) = 0;
        th.dir = th.goal < th.pos ? -1 : 1;
    }
    th.vel = th.lead = th.lead_v = th.rate = th.ticks = th.arch =
        0; /* tick_acc carries: a slow thumb's part-ticks add up across rests */
    th.jump = th.lean = th.fold = 0, th.enter = th.fold_off = th.fold_vel = 0;
    th.tau = cfs.snap;
    th.tick_at = now;
    th.on = 1;
    th.clicked = cf_round(th.goal);
}

/* SLIDE_INDEX on by q albums and the follower back by as many, so nothing moves: its numbers stay
 * small, where a float still resolves the 1e-4 its settling needs. */
static void th_rebase(void *s, int q, int n) {
    int i = (I(s, SLIDE_INDEX) + q) % n;
    I(s, SLIDE_OFFSET) = 0; /* the pass writes it again from pos */
    slide_menu_set_value(s, i < 0 ? i + n : i);
    th.pos -= (float)q, th.goal -= (float)q, th.seen -= (float)q, th.rebased += q;
    th.clicked -= q;
}
/* The album named is the one kept, here: closed() may come mid-destroy, with no slide_menu. */
static void th_named(int n) {
    unsigned c = th_album(th_shown(), (unsigned)n);
    int was = cf.titled;
    titles(c);
    if (cf.titled != was && c + CARDS < (unsigned)n && !cf.scoped)
        cf.saved_album = album_key(deque_at(cf.albums, c));
}
#define TH_REBASE 64 /* albums: a float's step there is 8e-6 */

/* travel raw units, as the lists' wheel moves (docs/internals.md#coverflow-thumb): Wheel
 * Sensitivity scales it into ticks (ticks_turn a turn at 50%), and each whole tick moves the goal
 * as many albums as wheel_accel answers, one for a slow or new turn, more as a sustained spin goes
 * on, back to one as it slows, and the covers follow through every album in between. Cover Flow
 * never jumps by letter: wheel_accel is told a length of 0, under WA_LETTER_ROWS. Turning back
 * drops what's queued past back. The playlists stop at the ends (coverflow_bounded): the first push
 * past one kicks the covers out, and they spring back. */
static void th_turn(void *s, int travel, unsigned now, int n) {
    int dir = travel > 0 ? -1 : 1; /* x rises anticlockwise: back */
    if (dir != th.dir) {
        float l = th.goal - th.pos, m = cfs.back / 100.f;
        th.goal = th.pos + (l > m ? m : l < -m ? -m : l);
        th.rate = 0, th.tick_acc = 0;
    }
    th.dir = dir;
    int scaled = ABS(travel) * cfs.ticks_turn * wheel_pct(); /* a tick is 10000 of these */
    th.ticks += (float)scaled / 10000;
    th.tick_at = now;
    for (th.tick_acc += scaled; th.tick_acc >= 10000; th.tick_acc -= 10000)
        th.goal += (float)(dir * wheel_accel(&th.wa, s, dir, now, 0)); /* 0: rows, never a letter */
    if (coverflow_bounded(s)) {
        int i = I(s, SLIDE_INDEX);
        float lo = (float)-i, hi = (float)(n - 1 - i);
        float g = th.goal < lo ? lo : th.goal > hi ? hi : th.goal;
        if (g != th.goal) {
            th.goal = g;
            if (!th.bounced && ABS(th.pos - g) < .02f && !motion_reduced()) /* MO_SPATIAL */
                th.pos = g + (float)dir / CF_BOUNCE, th.vel = 0, th.bounced = 1;
        } else
            th.bounced = 0;
    }
}

/* The arch (arch_pose) in place of the row at speed, Fast Navigation off. */
static int th_arched(void) { return cfs.arch_span && !ringnav_big_library(); }

/* How far the arch has formed, this pass of ms: fully with the covers at arch_full albums a second,
 * not at all under arch_at, and only with more than an album left to go, so a step of one never
 * forms it and it is gone as the covers land. It forms over arch_in ms and relaxes over arch_out.
 */
static void th_arch(float ms) {
    float v = ABS(th.vel) * 1000, left = ABS(th.goal - th.pos) - 1;
    float want = (v - cfs.arch_at) / (float)(cfs.arch_full - cfs.arch_at);
    want = want < 0 ? 0 : want > 1 ? 1 : want;
    want *= left < 0 ? 0 : left > 1 ? 1 : left;
    th.arch +=
        (want - th.arch) * (1 - ff_decay(ms / (want > th.arch ? cfs.arch_in : cfs.arch_out)));
    if (!want && th.arch < .001f) th.arch = 0;
}

/* Album by album, since ms after the last tick: part-way is for a slow thumb that stays; a lift,
 * the hold's timeout or a stop from speed goes to the album named. The row follows the goal, and
 * the facing point leads it. */
static void th_albums(int since, int ms) {
    int idle = since > cfs.hold_ms;
    float left = ABS(th.goal - th.pos);
    if (th.goal != (float)cf_round(th.goal) &&
        (!th.finger || idle || (since > cfs.stop_ms && left * 100 > cfs.stop_left))) {
        th.goal = (float)th_aim(), left = ABS(th.goal - th.pos);
    }
    float f = cf_smooth((th.rate - cfs.fast_at) / (cfs.fast_full - cfs.fast_at));
    float base = idle || !th.finger ? cfs.snap : cfs.track + (float)(cfs.spin - cfs.track) * f;
    float rest = cfs.snap + (float)(cfs.spin - cfs.snap) * cf_smooth(left * 100 / cfs.far_left);
    /* only the thumb raises it, and how far is left only lets it fall: lag raising it would add lag
     * and snap the row out at one speed */
    th.tau = th.tau < rest ? th.tau : rest;
    th.tau = th.tau > base ? th.tau : base;
    /* one spring, whatever the thumb does. ponytail: a slow step settles in about 0.6 s, not 0.3 s,
     * and touch waits for it (th.on); arch_tau only while th_moving() or th.arch if that shows */
    if (th_arched()) th.tau = cfs.arch_tau;
    /* its soft detent, slow and finger down: towards the albums, never against the thumb */
    int g0 = cf_floor(th.goal);
    float r0 = th.goal - (float)g0, k = th.finger ? (1 - f) * cfs.detent / 100 : 0;
    float to = (float)g0 + (1 - k) * r0 + k * r0 * r0 * (3 - 2 * r0);
    th_follow(&th.pos, &th.vel, to, th.tau, (float)ms);
    if (ABS(th.pos - th.goal) < 1e-4f && ABS(th.vel) < 1e-5f) th.pos = th.goal, th.vel = 0;
    if (th_arched()) { /* no facing point ahead of the covers: they bend in to where they are */
        th_arch((float)ms);
        return;
    }
    /* The facing point is the wheel's target, where the covers will land: off under lead_from, so
     * slow control is exact, and its short follow only smooths a reversal. */
    float l = th.goal - th.pos;
    float want = l * cf_smooth((ABS(l) * 100 - cfs.lead_from) / (cfs.lead_full - cfs.lead_from));
    th_follow(&th.lead, &th.lead_v, want, cfs.lead_ms, (float)ms);
    if (!want && ABS(th.lead) < 1e-4f && ABS(th.lead_v) < 1e-5f) th.lead = th.lead_v = 0;
    /* Once the wheel's stopped, the facing point's follow, lagging as it shrinks, never passes the
     * album the covers land on, or the next one faces for a moment and turns back. While it turns,
     * the gap jumps with each tick, so it's left alone then. */
    if (since > cfs.stop_ms && th.lead * l > 0 && ABS(th.lead) > ABS(l))
        th.lead = l, th.lead_v = -th.vel;
}

/* usable: navigation.c's; gated: travel is to be dropped (its gates). Answers THUMB_*. */
int coverflow_wheel_poll(int usable, int gated) {
    void *s = cf.slide, *wm = window_manager();
    int shown = usable && cfs.thumb && fx.frame && covers_live() && slide_count(s) &&
                window_manager_get_top_window(wm) == cf.page && !window_manager_is_animating(wm);
    int raw = shown && !window_manager_get_pointer_pressed(wm);
    if (raw != th.raw) {
        __sync_lock_test_and_set(&wheel_travel, 0);
        th.raw = raw, th.held = th.finger = th.early = 0;
        if (!raw && wheel_raw == WHEEL_THUMB) wheel_raw = WHEEL_TICKS;
    }
    if (raw) wheel_raw = WHEEL_THUMB; /* decided afresh each pass: lyric_poll may have handed it */
    if (!shown) {
        coverflow_wheel_land();
        return 0;
    }
    if (fx.todo) widget_invalidate_force(s, 0); /* covers still to load, a frame's budget each */
    unsigned now = (unsigned)time_now_ms();
    int ms = (int)(now - th.at), travel = raw ? __sync_lock_test_and_set(&wheel_travel, 0) : 0;
    if (gated) travel = 0;
    ms = ms < 1 ? 1 : ms > 250 ? 250 : ms;
    th.at = now;
    /* A still thumb sends no frames, for seconds: only wheel_held says it's there. A press only
     * anchors (wheel_sample); a contact under contact_ms is nothing, and a lift that short changes
     * nothing. */
    int held = raw && wheel_held;
    if (held != th.held) {
        th.held = held, th.edge_at = now;
        if (held)
            th.dead = th.bounced = 0;
        else if (!th.finger)
            th.early = 0;
    }
    if ((int)(now - th.edge_at) >= cfs.contact_ms && th.finger != held) {
        th.finger = held;
        travel += th.early;
        th.early = 0;
    }
    if (!th.finger) {
        if (held) th.early += travel;
        travel = 0;
    }
    /* its shake, a unit or so, moves nothing: travel runs loose in a band of dead units each way */
    th.dead += travel;
    travel = th.dead > cfs.dead ? th.dead - cfs.dead : th.dead < -cfs.dead ? th.dead + cfs.dead : 0;
    th.dead -= travel;
    if (!th.on && !travel) return 0; /* at rest */
    if (!th.on) th_grab(s, now);
    int n = slide_count(s), r = THUMB_MOVED;
    if (travel) {
        th_turn(s, travel, now, n);
        r |= THUMB_TURNED;
    }
    /* the thumb's rate, ticks/s, which shapes the follow (th_albums): quick to fall, so slowing
     * down gives control back at once */
    float inst = th.ticks * 1000 / (float)ms, tc = inst > th.rate ? cfs.rate_up : cfs.rate_down;
    th.rate += (inst - th.rate) * (1 - ff_decay((float)ms / tc));
    th.ticks = 0;
    th_albums((int)(now - th.tick_at), ms);
    int q = cf_round(th.pos);
    if (q >= TH_REBASE || q <= -TH_REBASE) th_rebase(s, q, n);
    I(s, SLIDE_OFFSET) = -cf_round(th.pos * (float)stride_of(s));
    if (ABS(th.pos - th.seen) >= 1 / 4096.f || ABS(th.lead - th.seen_lead) >= 1 / 4096.f ||
        !th.lead != !th.seen_lead || th.arch != th.seen_arch) {
        th.seen = th.pos, th.seen_lead = th.lead, th.seen_arch = th.arch;
        widget_invalidate_force(s, 0);
    } else if (!(r & THUMB_TURNED))
        r = 0; /* a thumb holding still: the loop may idle */
    th_named(n);
    if (cf_round(th.goal) != th.clicked) {
        th.clicked = cf_round(th.goal);
        r |= THUMB_CLICK;
    }
    if (!th.lead && !th.arch && th.pos == th.goal && th.goal == (float)cf_round(th.goal))
        th_commit(cf_round(th.goal));
    return r;
}
