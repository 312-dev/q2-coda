/* The volume pill (docs/oled-design.md#status-row, docs/internals.md#volume-pill): a volume change
 * made off Now Playing (a headset's own keys or its AVRCP volume, the Car Remote) shows in the
 * status row, in the clock's place at its left: a Raised pill VP_H tall on the row's centre, a
 * speaker where the clock's text starts, a thin track filled in Text Primary to the level, and the
 * number in Text Secondary. It holds VP_HOLD_MS
 * after the last change, then fades back to the clock over VP_FADE_MS (at once under Reduce
 * Motion). Volume 0 is the mute: the muted speaker and an empty track. The level is the Q2's 0 to
 * 100 in every case: with Link Volume on a headset, its 0 to 127 as bt_volume_poll reads it back
 * (bt_vol_q2, within the volume limit), so a number here means what it means everywhere else.
 *
 * It is drawn by the status row itself, from the bar's background (status_row.c
 * status_slots_paint), so it goes wherever the bar is painted; the clock under it is faded out by
 * its opacity while it shows. A change repaints the pill's box alone, a fade frame
 * the pill and the clock beside it (vp_dirty): never the page. Nothing shows on a volume page
 * (volume.c vol_page: Now Playing has its own volume), over Control Center (its slider follows the
 * volume) or a video.
 *
 * The same pill says a short thing that happened, a glyph and words (status_pill_show: adb's
 * "MacBook-Pro allowed", adb_trust.c), as wide as they are (VP_MSG_GLYPH, VP_SPK_GAP, the words
 * in Inter Regular 15, VP_PAD from each end), from the same left, or where the slots leave too
 * little ending VP_CLEAR short of them; it shows on Now Playing too (no volume), holds and fades as the volume's
 * does, and a volume change takes its place. */
#include "navigation.h"

#define VP_W 199        /* the widest pill */
#define VP_MIN_W 141    /* narrower, it leaves the clock's place and ends at the slots' edge */
#define VP_X (STATUS_ROW_INSET - VP_PAD) /* its left: the speaker where the clock's text starts */
#define VP_H 30         /* rows 8 to 37 on the row's centre, 23 */
#define VP_CLEAR 8      /* the ground left before the leftmost status slot, as the clock's */
#define VP_PAD 12       /* from each end to the speaker and to the number's last digit */
#define VP_SPK 14       /* the speaker's box (tools/art.py STATUS_PX), its ink 11 px tall */
#define VP_SPK_GAP 8    /* speaker to track, track to number */
#define VP_TRACK_H 4    /* the track, round-ended */
#define VP_NUM_W 32     /* the number's column: three tabular digits at 15 px, "100" */
#define VP_MSG_GLYPH 15 /* a message's glyph, tools/art.py ADB_PILL_PX */
#define VP_MSG_PX TYPE(15)
#define VP_MSG_LEFT 24  /* the furthest left it goes, the text column's */
#define VP_HOLD_MS 2000
#define VP_FADE_MS 250

static struct {
    unsigned at;             /* time_now_ms of the last change */
    unsigned fade_at;        /* ... and when the fade began */
    unsigned timer;          /* the hold's timer */
    unsigned char on;        /* shown or fading */
    unsigned char fading;    /* past its hold, fading */
    unsigned char dim;       /* 255 less the clock's opacity as last set */
    unsigned char alpha;     /* the pill's, as the last frame set it */
    unsigned short msg_w;    /* a message's box, measured at its first paint; 0 until then */
    char glyph[24], msg[48]; /* a message instead of the volume, or "" */
} vp __attribute__((section(".scratch")));

/* The pill's box in the bar's pixels, from VP_X, the clock's place: VP_W wide when the slots leave it
 * room, narrower down to VP_MIN_W to end VP_CLEAR short of the leftmost slot, then VP_MIN_W ending
 * there, never nearer the screen's left than VP_MSG_LEFT. A message hugs its words from VP_X, or
 * ends VP_CLEAR short of the slots when they leave too little, its words cut. */
static rect_t vp_box(void) {
    int edge = status_slots_left() - VP_CLEAR, x = VP_X,
        w = vp.msg[0] ? (vp.msg_w ? vp.msg_w : VP_W) : edge - x < VP_W ? edge - x : VP_W;
    if (vp.msg[0] ? w > edge - x : w < VP_MIN_W) {
        if (!vp.msg[0]) w = VP_MIN_W;
        x = edge - w < VP_MSG_LEFT ? VP_MSG_LEFT : edge - w;
        if (vp.msg[0]) w = edge - x;
    }
    return (rect_t){ x, geom_val(V_STATUS_Y) - VP_H / 2, w, VP_H };
}

/* What a change repaints: the pill's box; with whole, from the screen's left to the leftmost slot
 * in the pill's rows, where the clock stands, as its opacity changes. */
static void vp_dirty(int whole) {
    void *bar = *(void *const *)system_bar;
    if (!bar) return;
    rect_t r = vp_box();
    if (whole) r.w = status_slots_left() - (r.x = 0);
    widget_invalidate_force(bar, &r);
}

/* The clock's opacity, 255 less dim; set only when it changes. */
static void vp_clock(int dim) {
    void *bar = *(void *const *)system_bar;
    if (!bar || dim == vp.dim) return;
    void *clock = widget_lookup(bar, "label_clock", 1);
    vp.dim = (unsigned char)dim;
    if (clock) widget_set_opacity(clock, 255u - (unsigned)dim);
}

/* Gone: the clock back at once. */
static void vp_end(void) {
    vp.on = vp.fading = vp.alpha = 0;
    vp.msg[0] = 0, vp.msg_w = 0;
    vp_clock(0);
}

/* Where the pill has no place: a volume page in use (Now Playing's own volume; a message is no
 * volume and shows there), Control Center in front (its slider follows), a video playing. */
static int vp_hidden(void) {
    void *top = window_manager_get_top_window(window_manager());
    return !g_backlight_status || video_on() || (!vp.msg[0] && vol_page(page_in_use())) ||
           (top && !tk_strcmp(widget_get_prop_str(top, "name", ""), "statusbar_dialog"));
}

/* The pill's alpha now: 255 while it holds; along the fade, cubic out, to 0. */
static int vp_alpha_now(void) {
    if (!vp.on) return 0;
    if (!vp.fading) return 255;
    int t = (int)((unsigned)time_now_ms() - vp.fade_at) * 1000 / VP_FADE_MS;
    return t >= 1000 ? 0 : 255 - 255 * motion_ease(t, EASE_CUBIC_OUT) / 1000;
}

/* The shared loop's (ui_kit.c motion_loop) frame while the pill fades: the pill a step fainter and
 * the clock a step back, its band repainted; dropped once the pill is gone, or shown again. */
static int vp_frame(void) {
    if (!vp.on || !vp.fading) return 0;
    int a = vp_alpha_now();
    vp.alpha = (unsigned char)a;
    vp_clock(a); /* the clock at 255 less the pill's alpha */
    vp_dirty(1);
    if (a) return 1;
    vp_end();
    return 0;
}

/* VP_HOLD_MS after the last change (a later one puts it off): the fade, or under Reduce Motion the
 * clock back at once. */
static int vp_due(const void *info) {
    (void)info;
    vp.timer = 0;
    if (!vp.on) return 0;
    unsigned since = (unsigned)time_now_ms() - vp.at;
    if (since < VP_HOLD_MS) {
        vp.timer = timer_add(vp_due, (void *)0, VP_HOLD_MS - since);
        return 0;
    }
    if (motion_reduced()) {
        vp_end();
        vp_dirty(1);
        return 0;
    }
    vp.fade_at = (unsigned)time_now_ms(), vp.fading = 1;
    motion_loop(vp_frame);
    return 0;
}

/* A change to show (volume.c vol_show): the pill at full, the clock out, its hold started again.
 * A pill already up repaints its box alone; a new one (or one fading) the clock's band too. */
void vol_pill_show(void) {
    if (vp.msg[0]) { /* a message gives way: the volume's own box, and its own places, from here */
        vp_dirty(1);
        vp.msg[0] = 0, vp.msg_w = 0;
        if (vp_hidden()) {
            vp_end();
            return;
        }
    }
    if (vp_hidden()) return;
    int fresh = !vp.on || vp.fading;
    vp.at = (unsigned)time_now_ms();
    vp.on = 1, vp.fading = 0, vp.alpha = 255;
    vp_clock(255);
    vp_dirty(fresh);
    if (!vp.timer) vp.timer = timer_add(vp_due, (void *)0, VP_HOLD_MS);
}

/* A message in the pill: glyph (a theme image's name less its _d or _l, or 0) and text, held
 * VP_HOLD_MS, then faded. */
void status_pill_show(const char *glyph, const char *text) {
    if (vp.on) vp_dirty(1); /* what showed before, the volume's wider box */
    tk_snprintf(vp.glyph, sizeof vp.glyph, "%s", glyph ? glyph : "");
    tk_snprintf(vp.msg, sizeof vp.msg, "%s", text && *text ? text : " ");
    if (vp_hidden()) {
        vp_end();
        return;
    }
    vp.msg_w = 0;
    vp.at = (unsigned)time_now_ms();
    vp.on = 1, vp.fading = 0, vp.alpha = 255;
    vp_clock(255);
    vp_dirty(1);
    if (!vp.timer) vp.timer = timer_add(vp_due, (void *)0, VP_HOLD_MS);
}

/* Whether the pill is up (the status row's clock is out). */
int vol_pill_up(void) { return vp.on; }

/* status_slots_paint, in final colours: the pill at its alpha, or nothing; where it has no place it
 * ends here, so the clock painted after the bar's background is back in this frame. */
void vol_pill_paint(void *bar, void *canvas) {
    if (!vp.on) return;
    if (vp_hidden()) {
        vp_end();
        return;
    }
    int a = vp.alpha, level = g_volume > 100 ? 100 : g_volume;
    if (!a) return;
    if (vp.msg[0]) {
        int g = vp.glyph[0] ? VP_MSG_GLYPH + VP_SPK_GAP : 0;
        if (!vp.msg_w) { /* measured once: the box hugs it from the next paint */
            vp.msg_w = (unsigned short)(2 * VP_PAD + g + ui_text_w(canvas, vp.msg, 0, VP_MSG_PX));
            vp_dirty(1);
        }
        rect_t box = vp_box();
        int cy = box.y + VP_H / 2, x = box.x + VP_PAD;
        canvas_set_global_alpha(canvas, (unsigned)a);
        fill_box(canvas, &box, RGBA(tok(TOK_RAISED)), VP_H / 2);
        if (g) { /* the glyph's half for the theme's: NAME_d on black, NAME_l on paper */
            char art[32];
            tk_snprintf(art, sizeof art, "%s_%c", vp.glyph, ipod_light() ? 'l' : 'd');
            paint_art(bar, canvas, theme_art(art), x, cy - VP_MSG_GLYPH / 2);
        }
        ui_text(canvas, vp.msg, 0, VP_MSG_PX, tok(TOK_PRIMARY), 1, x + g, cy,
                box.x + box.w - VP_PAD - x - g, 2);
        canvas_set_global_alpha(canvas, 0xff);
        return;
    }
    rect_t box = vp_box();
    int cy = box.y + VP_H / 2, x = box.x + VP_PAD, L = ipod_light();
    canvas_set_global_alpha(canvas, (unsigned)a);
    fill_box(canvas, &box, RGBA(tok(TOK_RAISED)), VP_H / 2);
    paint_art(bar, canvas,
              theme_art(level ? (L ? "status_vol_l" : "status_vol_d")
                              : (L ? "status_mute_l" : "status_mute_d")),
              x, cy - (VP_SPK + 1) / 2);
    int tx = x + VP_SPK + VP_SPK_GAP, num = box.x + box.w - VP_PAD,
        tw = num - VP_NUM_W - VP_SPK_GAP - tx;
    rect_t track = { tx, cy - VP_TRACK_H / 2, tw, VP_TRACK_H };
    fill_box(canvas, &track, RGBA(tok(TOK_TRACK)), VP_TRACK_H / 2);
    int w = tw * level / 100;
    if (w) { /* a pill, never a sliver: at least round */
        rect_t on = { tx, track.y, w < VP_TRACK_H ? VP_TRACK_H : w, VP_TRACK_H };
        fill_box(canvas, &on, RGBA(tok(TOK_PRIMARY)), VP_TRACK_H / 2);
    }
    char s[8];
    tk_snprintf(s, sizeof s, "%d", level);
    ui_text(canvas, s, 1, TYPE(15), tok(TOK_SECONDARY), 1, num, cy, VP_NUM_W, 3);
    canvas_set_global_alpha(canvas, 0xff);
}

#if Q2POD_DEV
/* --dev builds only, for the emulator, which has no headset: STATUS_FAKE's "volume=N"
 * (status_row.c status_fake) moves the volume to N as a headset's own keys would (bt_volume_poll),
 * once for each new N, so the pill can be shown and shot there. status_row_sync, each second and
 * each bar paint. */
void vol_pill_fake(void) {
    static int seen __attribute__((section(".scratch"))); /* the last N + 1, 0 none */
    int v = status_fake('v');
    if (v < 0 || v > 100 || v + 1 == seen) return;
    int from = g_volume;
    seen = v + 1;
    g_volume = (unsigned char)v;
    device_set_volume(v, 1);
    vol_show(from);
}
#endif
