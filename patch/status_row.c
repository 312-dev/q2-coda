/* The status row's glyph slots (docs/oled-design.md#status-row,
 * docs/internals.md#status-row-slots): right to left the battery, the play state's level bars,
 * Wi-Fi, a card app's sync, Bluetooth (its link, a headset's battery, a car) and Buttons Lock's
 * padlock. Each slot that shows takes its box and the gap before the one on its right; the clock
 * sits beside the leftmost (navigation.c clock_place), so nothing in the row ever wraps: the title
 * gives way. The battery is stock's view_battery, laid out by the bar's right group and painted by
 * navigation.c paint_battery; every other slot is painted here, from the bar's background
 * (ringnav_paint_bg), so Home in Split inks them with the clock over its art (coverflow.c home_ink,
 * one ink for the row). Every glyph is the clock's digits' height, 11 px on rows 17 to 27 (cy 23),
 * with SR_GAP of ground between two (docs/oled-design.md#status-row); the animated ones repaint
 * only their own box. */
#include "navigation.h"

#define SR_FRAME_MS 125 /* the level bars and the sync arrows: 8 frames a second */
#define SR_FRAMES 10    /* the bars' loop, 1.25 s */
#define SR_SPIN 8       /* the arrows' frames, tools/art.py STATUS_SPIN: a half turn a second */
#define SR_BARS_W 10    /* three 2 px bars, 4 px apart; paused, the pause in the same box */
#define SR_BAR_W 2
#define SR_BAR_PITCH 4
#define SR_BAR_DROP 5   /* the bars stand on the row's centre's row + this, the digits' baseline */
#define SR_PAUSE_W 3.0f /* the pause's bars, SR_PAUSE_AT and + SR_PAUSE_PITCH into its box */
#define SR_PAUSE_AT 0.5f
#define SR_PAUSE_PITCH 6.0f
#define SR_PAUSE_R 0.9f
#define SR_INK 11      /* every glyph's height, tools/art.py STATUS_INK */
#define SR_GAP 6       /* the ground between two glyphs */
#define SR_GLYPH 14    /* tools/art.py STATUS_PX: a status glyph's box's height */
#define SR_WIFI_W 16   /* tools/art.py STATUS_WIFI_W: the fan's 15.6 px */
#define SR_CAR_W 19    /* tools/art.py STATUS_CAR_W: car-profile 11 px tall is 18.5 wide */
#define SR_LOCK 13     /* the padlock's box, tools/art.py STATE_PX */
#define SR_RING 16     /* the download ring's box: a 14 px ring and its stroke */
#define SR_RING_R 7.0f /* its centre line's radius */
#define SR_RING_LINE 1.6f
#define SR_DOWN 8       /* the arrow inside it */
#define SR_BT_GAP 2     /* the headphones glyph's box, then the headset's battery */
#define SR_HS_BATT_W 15 /* the headset's battery: a 15x8 body (list_marks.c battery_slim) */
#define SR_HS_BATT_H 8
#define SR_HS_NUB 2 /* ... and its nub's columns past it */
#define SR_RELAY 8  /* a guest's relay mark between its phone and the Q2's device (tools/art.py) */
#define SR_TAP_W 44 /* a slot's tap target round its centre, as the battery's */
#define SR_TAP_H 40
#define SR_TAP_SLOP 12
#define SR_TOUCH_DOWN 0xff /* the raw pointer events ringnav_input sees (EVT_POINTER_FIRST) */
#define SR_TOUCH_UP 0x103

/* A slot: its box's width now (0: not shown), the room before the shown slot to its right, and its
 * painter, given its box's left and the row's centre in the bar's pixels, in final colours. */
typedef struct {
    int (*shown)(void);
    unsigned char gap;
    void (*paint)(void *bar, void *canvas, int x, int cy);
} status_slot_t;

static struct {
    int x[SLOT_N], w[SLOT_N]; /* the last layout, in the bar's pixels */
    int frame, spin;          /* the bars' frame and the arrows' */
    unsigned timer, sig, nav;
    anim_t xfade;             /* the play state's dissolve from p_from to p_to */
    short p_from, p_to;       /* 0 the bars, 1000 the pause */
    unsigned char seen;       /* the play slot shown at the last sync */
    unsigned char xf_dirty;   /* the dissolve's box still to repaint */
    int down, down_x, down_y; /* the slot a touch went down on, + 1 */
} sr __attribute__((section(".scratch")));

#if Q2POD_DEV
/* --dev builds only, for the emulator, which has no radio or charger: STATUS_FAKE's "wifi=N
 * charge=1" (either, each found by its first letter, key) stands in for what the Q2 would report;
 * -1 when the file does not name it. Read at most every STATUS_FAKE_MS. */
#define STATUS_FAKE "/mnt/data/q2-status.fake"
#define STATUS_FAKE_MS 500
int status_fake(char key) {
    static char raw[64] __attribute__((section(".scratch")));
    static unsigned at __attribute__((section(".scratch")));
    unsigned now = (unsigned)time_now_ms();
    if (!at || now - at >= STATUS_FAKE_MS) {
        at = now ? now : 1;
        if (!state_read(STATUS_FAKE, raw, sizeof raw)) raw[0] = 0;
    }
    for (const char *s = raw; *s; ++s)
        if (*s == key && (s == raw || s[-1] == ' ' || s[-1] == '\n')) {
            while (*s && *s != '=') ++s;
            if (!*s) return -1;
            int v = 0, any = 0;
            for (++s; *s >= '0' && *s <= '9'; ++s) v = v * 10 + (*s - '0'), any = 1;
            return any ? v : -1;
        }
    return -1;
}
#endif

/* Stock's status-bar Wi-Fi (img_wifi, off-screen under a status row, its image kept current by
 * systembar_showface): bar_wifi1 to bar_wifi4 while connected, as get_wifisignal's bars; 0 for
 * anything else (off, Smart Wi-Fi's radio off, not connected, bar_wifi0). No I/O. Control Center's
 * Wi-Fi circle reads it too (control_center.c cc_wifi). */
int wifi_level(void) {
    if (status_fake('w') >= 0) return status_fake('w');
    void *w = st.bar_wifi;
    if (!w || !widget_get_visible(w)) return 0;
    const char *s = widget_get_prop_str(w, "image", "");
    static const char name[] = "bar_wifi";
    for (unsigned i = 0; i < sizeof name - 1; ++i)
        if (!s || s[i] != name[i]) return 0;
    return s[8] >= '1' && s[8] <= '4' && !s[9] ? s[8] - '0' : 0;
}
/* Connected with no internet: stock's network monitor (networkGetConnectStatus, a word its thread
 * sets about once a second) says no link while the radio's bars say connected. ponytail: that is
 * the link and its address, not the internet: nothing on the Q2 probes past the access point (stock
 * has no such check, and a probe would cost a request a poll); a captive portal or a dead uplink
 * shows the full glyph. A DNS lookup or an HTTP check from a worker would lift it. */
int wifi_offline(void) { return networkGetConnectStatus() <= 0; }

static int play_shown(void) {
    if (home_np_lead()) return 0; /* Home's first row leads with it (home_np.c) */
    int s = bar_status();
    return s == 2 || s == 3 ? SR_BARS_W : 0;
}
static int wifi_shown(void) { return wifi_level() ? SR_WIFI_W : 0; }
static int sync_shown(void) { return addon_sync((int *)0) ? SR_RING : 0; }
/* The Bluetooth slot (docs/oled-design.md#status-row, P9): what the link is, BT_SLOT_*. Stock's
 * link list's last item is the device it links (status 3 connected, as its rows read it); a link
 * thread running (bt_connectstatus 1) is a device connecting. */
enum {
    BT_SLOT_NONE,
    BT_SLOT_CONNECTING,
    BT_SLOT_RUNE,
    BT_SLOT_HEADSET,
    BT_SLOT_CAR,
    BT_SLOT_GUEST
};
static int bt_slot(void) {
    if (guest_active()) return BT_SLOT_GUEST; /* a guest plays (guest_ui.c) */
    char item[32] = "";
    int status = 0, n = g_bluetoothflag ? getBtLinkSize() : 0;
    if (n > 0) getBtLinkItem(n - 1, &status, item);
    if (status != 3)
        return n > 0 && I(bt_connectstatus, 0) == 1 ? BT_SLOT_CONNECTING : BT_SLOT_NONE;
    if (!tk_strcmp(bt_kind(bt_headset_class()), "car")) return BT_SLOT_CAR;
    return bt_headset_battery() >= 0 ? BT_SLOT_HEADSET : BT_SLOT_RUNE;
}
/* A guest's: its phone, and while relayed to a Bluetooth device the mark and that device's glyph */
static int guest_relayed(void) {
    char out[64];
    guest_field("out", out, sizeof out);
    return *out && tk_strcmp(out, "jack");
}
/* The linked device's glyph's width: a car's is wider than the others' 14 px box at 11 px tall */
static int linked_w(void) {
    return !tk_strcmp(bt_kind(bt_headset_class()), "car") ? SR_CAR_W : SR_GLYPH;
}
static int headset_shown(void) {
    int slot = bt_slot();
    if (slot == BT_SLOT_GUEST)
        return SR_GLYPH + (guest_relayed() ? SR_BT_GAP + SR_RELAY + SR_BT_GAP + linked_w() : 0);
    return slot == BT_SLOT_NONE      ? 0
           : slot == BT_SLOT_HEADSET ? SR_GLYPH + SR_BT_GAP + SR_HS_BATT_W + SR_HS_NUB
           : slot == BT_SLOT_CAR     ? SR_CAR_W
                                     : SR_GLYPH;
}
static int lock_shown(void) { return g_keylock_flag ? SR_LOCK : 0; }

/* The play state, in Text Primary as every glyph: playing, the level bars (Up Next's, upnext.c
 * un_level), 2 px wide with round ends, moving a frame a tick and standing on the digits' baseline;
 * paused, the pause, two SR_PAUSE_W bars SR_INK tall in the bars' own box. Between the two the
 * stopped bars and the pause cross-dissolve (play_paint, play_xfade_sync). */
static const unsigned char bar_h[SR_FRAMES][3] = { { 7, 10, 3 }, { 9, 8, 3 },  { 11, 5, 5 },
                                                   { 11, 3, 7 }, { 9, 3, 10 }, { 7, 4, 11 },
                                                   { 5, 6, 11 }, { 3, 9, 9 },  { 3, 11, 7 },
                                                   { 5, 11, 4 } };
/* The play state's bars at x on cy, each its left, top, width, height and corner radius: the
 * pause's two when pause, else three level bars at the current frame; returns how many. */
static int play_bars(int x, int cy, int pause, float (*bar)[5]) {
    float base = (float)(cy + SR_BAR_DROP);
    if (pause) {
        for (int i = 0; i < 2; ++i) {
            float *b = bar[i];
            b[0] = (float)x + SR_PAUSE_AT + (float)i * SR_PAUSE_PITCH, b[1] = base - SR_INK;
            b[2] = SR_PAUSE_W, b[3] = SR_INK, b[4] = SR_PAUSE_R;
        }
        return 2;
    }
    for (int i = 0; i < 3; ++i) {
        float *b = bar[i], h = (float)bar_h[sr.frame % SR_FRAMES][i];
        b[0] = (float)(x + i * SR_BAR_PITCH), b[1] = base - h, b[2] = SR_BAR_W, b[3] = h;
        b[4] = SR_BAR_W / 2.0f;
    }
    return 3;
}
#define SR_XFADE_MS 250 /* bars <-> pause, a cross-dissolve on the house ease-out (home_np.c's) */

/* The glyph now: 0 the bars .. 1000 the pause, eased */
static int play_p(void) { return sr.p_from + (sr.p_to - sr.p_from) * anim_at(&sr.xfade) / 1000; }
/* The bars at 1000 - p's opacity under the pause at p's (one of them alone at either end): the
 * bars stand at their frame while they dissolve, and both glyphs keep to the SR_BARS_W box. */
static void play_paint(void *bar, void *canvas, int x, int cy) {
    (void)bar;
    void *vg = canvas_get_vgcanvas(canvas);
    if (!vg) return;
    int p = play_p();
    unsigned ink = RGBA(ipod_light() ? 0 : 0xffffff);
    vgcanvas_save(vg);
    vgcanvas_translate(vg, (float)I(canvas, CANVAS_X), (float)I(canvas, CANVAS_Y));
    for (int pause = 0; pause < 2; ++pause) {
        int a = pause ? p : 1000 - p;
        if (a <= 0) continue;
        float bars[3][5];
        int n = play_bars(x, cy, pause, bars);
        vgcanvas_set_fill_color(vg, (ink & 0xffffffu) | (unsigned)(255 * a / 1000) << 24);
        for (int i = 0; i < n; ++i) {
            round_path(vg, bars[i][0], bars[i][1], bars[i][2], bars[i][3], bars[i][4]);
            vgcanvas_fill(vg);
        }
    }
    vgcanvas_restore(vg);
}

/* A theme glyph of box px centred in a slot box w wide on cy, its top rounded up as stock's image
 * widget centres one (an odd box's half row above), so the padlock lands where its widget put it,
 * and a SR_GLYPH box's ink (tools/art.py STATUS_TOP) on the digits' rows.
 */
static void glyph(void *bar, void *canvas, const char *stem, int x, int w, int cy, int px) {
    char name[40];
    tk_snprintf(name, sizeof name, "%s_%c", stem, ipod_light() ? 'l' : 'd');
    paint_art(bar, canvas, theme_art(name), x + (w - px) / 2, cy - (px + 1) / 2);
}

/* A status glyph's art (tools/art.py STATUS_FIT, the fan), SR_GLYPH tall and as wide as its ink, at
 * x on cy: its ink on the digits' rows. */
static void status_art(void *bar, void *canvas, const char *stem, int x, int cy) {
    char name[40];
    tk_snprintf(name, sizeof name, "%s_%c", stem, ipod_light() ? 'l' : 'd');
    paint_art(bar, canvas, theme_art(name), x, cy - (SR_GLYPH + 1) / 2);
}

/* Wi-Fi: iOS's fan, 1 to 3 of its dot and two bands lit by the bars (1 one, 2 two, 3 and 4 all
 * three), the rest dim (tools/art.py status_wifi); wifi-x connected with no internet. Both are
 * SR_WIFI_W x SR_GLYPH. */
static void wifi_paint(void *bar, void *canvas, int x, int cy) {
    int level = wifi_level();
    char stem[24];
    if (wifi_offline())
        tk_snprintf(stem, sizeof stem, "status_wifi_x");
    else
        tk_snprintf(stem, sizeof stem, "status_wifi_%d", level > 3 ? 3 : level);
    status_art(bar, canvas, stem, x, cy);
}

/* A card app's sync: checking, the arrows turning in Text Secondary; downloading, a ring that fills
 * in the accent from 12 o'clock clockwise with its arrow in it. */
static void sync_paint(void *bar, void *canvas, int x, int cy) {
    int permille = 0;
    if (addon_sync(&permille) != 2) {
        char stem[24];
        tk_snprintf(stem, sizeof stem, "status_sync_%d", sr.spin % SR_SPIN);
        glyph(bar, canvas, stem, x, SR_RING, cy, SR_GLYPH);
        return;
    }
    void *vg = canvas_get_vgcanvas(canvas);
    if (vg) {
        int light = ipod_light();
        float cx = (float)x + SR_RING / 2.0f, fy = (float)cy;
        vgcanvas_save(vg);
        vgcanvas_translate(vg, (float)I(canvas, CANVAS_X), (float)I(canvas, CANVAS_Y));
        vgcanvas_set_line_width(vg, SR_RING_LINE);
        vgcanvas_begin_path(vg);
        vgcanvas_arc(vg, cx, fy, SR_RING_R, 0, 6.2831853f, 0);
        vgcanvas_set_stroke_color(vg, RGBA(light ? RGB_TRACK_L : RGB_TRACK));
        vgcanvas_stroke(vg);
        if (permille > 0) {
            vgcanvas_begin_path(vg);
            vgcanvas_arc(vg, cx, fy, SR_RING_R, -1.5707963f,
                         -1.5707963f + 6.2831853f * (float)permille / 1000, 0);
            vgcanvas_set_stroke_color(vg, RGBA(scheme()->tones[0] & 0xffffffu));
            vgcanvas_stroke(vg);
        }
        vgcanvas_restore(vg);
    }
    glyph(bar, canvas, "status_down", x, SR_RING, cy, SR_DOWN);
}

/* The Bluetooth slot: a guest playing (Guest Play), its phone in the accent, and relayed to a car
 * or headset "› car"; else nothing while off or idle; the rune dim while a device connects and in
 * Text Primary once one is connected; a headset that reports its level, the headphones glyph and a
 * 15x8 battery, filled to the level and red under 20% as the status battery's; a car, its glyph. */
static void headset_paint(void *bar, void *canvas, int x, int cy) {
    switch (bt_slot()) {
    case BT_SLOT_CONNECTING:
        glyph(bar, canvas, "status_btdim", x, SR_GLYPH, cy, SR_GLYPH);
        break;
    case BT_SLOT_RUNE:
        glyph(bar, canvas, "status_bt", x, SR_GLYPH, cy, SR_GLYPH);
        break;
    case BT_SLOT_CAR:
        status_art(bar, canvas, "status_car", x, cy);
        break;
    case BT_SLOT_GUEST: /* the guest's phone in the accent, then › and where it plays */
        paint_art(bar, canvas, theme_art("status_guest"), x, cy - (SR_GLYPH + 1) / 2);
        if (guest_relayed()) {
            int at = x + SR_GLYPH + SR_BT_GAP;
            glyph(bar, canvas, "status_relay", at, SR_RELAY, cy, SR_RELAY);
            at += SR_RELAY + SR_BT_GAP;
            status_art(bar, canvas, linked_w() == SR_CAR_W ? "status_car" : "status_headphones", at,
                       cy);
        }
        break;
    case BT_SLOT_HEADSET: {
        int level = bt_headset_battery();
        glyph(bar, canvas, "status_headphones", x, SR_GLYPH, cy, SR_GLYPH);
        battery_slim(bar, canvas, x + SR_GLYPH + SR_BT_GAP, cy - SR_HS_BATT_H / 2, SR_HS_BATT_W,
                     SR_HS_BATT_H, (unsigned)(level > 100 ? 100 : level) << 4);
        break;
    }
    }
}

/* Buttons Lock's padlock (keylock.c), the theme's keylock art in Text Primary. */
static void lock_paint(void *bar, void *canvas, int x, int cy) {
    glyph(bar, canvas, "keylock", x, SR_LOCK, cy, SR_LOCK);
}

/* Right to left; the battery's own row is stock's widget (shown is never called for it). Every gap
 * is SR_GAP, iOS's (mock_status_icons.py): more than coverflow.c's INK_GAP, so Split still tells a
 * status colour's glyph from the rest. */
static const status_slot_t slots[SLOT_N] = {
    [SLOT_BATTERY] = { 0, 0, 0 },
    [SLOT_PLAY] = { play_shown, SR_GAP, play_paint },
    [SLOT_WIFI] = { wifi_shown, SR_GAP, wifi_paint },
    [SLOT_SYNC] = { sync_shown, SR_GAP, sync_paint },
    [SLOT_HEADSET] = { headset_shown, SR_GAP, headset_paint },
    [SLOT_LOCK] = { lock_shown, SR_GAP, lock_paint },
};

/* The slots laid out from the battery's left, in the bar's pixels (its right group is a child of
 * the bar, which sits at the screen's top); returns the leftmost shown slot's left, or -1 with no
 * battery slot yet. */
static int layout(void) {
    void *slot = st.bar_slot;
    if (!slot || !P(slot, W_PARENT)) return -1;
    int edge = I(P(slot, W_PARENT), W_X) + I(slot, W_X);
    sr.x[SLOT_BATTERY] = edge, sr.w[SLOT_BATTERY] = I(slot, W_W);
    for (int i = SLOT_BATTERY + 1; i < SLOT_N; ++i) {
        int w = slots[i].shown();
        sr.w[i] = w;
        if (w) edge = sr.x[i] = edge - slots[i].gap - w;
    }
    return edge;
}

/* The leftmost shown slot's left (the battery's with no other), the edge the clock stands beside;
 * the screen's right edge before the bar is found. */
int status_slots_left(void) {
    int left = layout();
    return left < 0 ? 375 : left;
}

/* The bar's background paint under a status row: every shown slot but the battery, in final
 * colours (st.raw_ink), restoring the canvas's fill. */
void status_slots_paint(void *bar, void *canvas) {
    void *lcd = P(canvas, CANVAS_LCD);
    if (!geom_val(V_STATUS_Y) || !lcd || layout() < 0) return;
    unsigned fill = (unsigned)I(lcd, LCD_FILL_COLOR), raw = ui_ink_raw(1);
    int cy = geom_val(V_STATUS_Y);
    for (int i = SLOT_BATTERY + 1; i < SLOT_N; ++i)
        if (sr.w[i]) slots[i].paint(bar, canvas, sr.x[i], cy);
    ui_ink_raw(raw);
    stock_canvas_set_fill_color(canvas, fill);
}

/* The slot under x, y (the bar's pixels, upright, as ringnav_input turns a touch), SLOT_*, or -1:
 * each shown slot's target SR_TAP_W x SR_TAP_H round its centre (the battery's round its body, or
 * its percentage on Percent: navigation.c batt_top's centre), the nearest centre where they meet.
 */
int status_slot_at(int x, int y) {
    void *slot = st.bar_slot, *bar = *(void *const *)system_bar;
    if (!slot || !bar || !widget_get_visible(slot) || !widget_get_visible(bar) || layout() < 0)
        return -1;
    void *row = P(slot, W_PARENT);
    int cy = I(row, W_Y) + (I(row, W_H) + 1) / 2, best = -1, near = 1 << 30;
    if (y < cy - SR_TAP_H / 2 || y >= cy + SR_TAP_H / 2) return -1;
    for (int i = 0; i < SLOT_N; ++i) {
        if (i != SLOT_BATTERY && (!geom_val(V_STATUS_Y) || !sr.w[i])) continue;
        int cx = sr.x[i] + (i == SLOT_BATTERY && !st.batt_mode ? geom_val(V_BATT_W) : sr.w[i]) / 2,
            d = x - cx;
        if (d < -SR_TAP_W / 2 || d >= SR_TAP_W / 2) continue;
        if (d < 0) d = -d;
        if (d < near) near = d, best = i;
    }
    return best;
}

/* Now Playing for a tap on the bars, after the input's dispatch, as Control Center's Now Playing
 * block opens it (playback_np_open). */
static int np_due(const void *info) {
    (void)info;
    sr.nav = 0;
    if (play_shown()) playback_np_open();
    return 0;
}

/* ringnav_input's touches, upright: a press on a slot whose lift lands within SR_TAP_SLOP of it,
 * still on that slot, is a tap: the battery's toasts its state (charging.c charge_tap), the bars'
 * open Now Playing. The touch goes on to stock as before, so a pull from the row still opens
 * Control Center. */
void status_touch(unsigned type, int x, int y) {
    if (type == SR_TOUCH_DOWN) {
        sr.down = g_backlight_status && usable() ? status_slot_at(x, y) + 1 : 0;
        sr.down_x = x, sr.down_y = y;
        return;
    }
    if (type != SR_TOUCH_UP || !sr.down) return;
    int was = sr.down - 1, dx = x - sr.down_x, dy = y - sr.down_y;
    sr.down = 0;
    if (dx < -SR_TAP_SLOP || dx > SR_TAP_SLOP || dy < -SR_TAP_SLOP || dy > SR_TAP_SLOP ||
        status_slot_at(x, y) != was)
        return;
    if (was == SLOT_BATTERY)
        charge_tap();
    else if (was == SLOT_PLAY && !sr.nav)
        sr.nav = timer_add(np_due, (void *)0, 0);
}

/* Whether a slot moves: the bars while playing, the arrows while an app checks. */
static int moving(void) {
    return geom_val(V_STATUS_Y) && (bar_status() == 2 || addon_sync((int *)0) == 1);
}
/* Each frame, while lit and something moves: the next frame, and only the moving slots' boxes
 * repainted (ringnav_paint_bg paints them with the bar's background under them). */
static int sr_tick(const void *info) {
    (void)info;
    void *bar = *(void *const *)system_bar;
    if (!g_backlight_status || !bar || !moving()) {
        sr.timer = 0;
        return 0; /* RET_REMOVE */
    }
    int playing = bar_status() == 2, checking = addon_sync((int *)0) == 1;
    layout();
    if (playing && sr.w[SLOT_PLAY] && !anim_running(&sr.xfade)) { /* still while they dissolve */
        sr.frame = (sr.frame + 1) % SR_FRAMES;
        rect_t r = { sr.x[SLOT_PLAY], 0, SR_BARS_W, I(bar, W_H) };
        widget_invalidate_force(bar, &r);
    }
    if (checking && sr.w[SLOT_SYNC]) {
        sr.spin = (sr.spin + 1) % SR_SPIN;
        rect_t r = { sr.x[SLOT_SYNC], 0, SR_RING, I(bar, W_H) };
        widget_invalidate_force(bar, &r);
    }
    return 8; /* RET_REPEAT */
}

/* The shared frame timer's (ui_kit.c motion_hook), while the play state dissolves: its box
 * repainted, as sr_tick's, and once more after the dissolve's end. */
static void sr_xfade_frame(void) {
    if (!sr.xf_dirty) return;
    if (!anim_running(&sr.xfade)) sr.xf_dirty = 0; /* this repaint shows the end */
    void *bar = *(void *const *)system_bar;
    if (!bar || !g_backlight_status || !geom_val(V_STATUS_Y) || layout() < 0 || !sr.w[SLOT_PLAY])
        return;
    rect_t r = { sr.x[SLOT_PLAY], 0, SR_BARS_W, I(bar, W_H) };
    widget_invalidate_force(bar, &r);
}

/* The play state's glyph to the bars playing or the pause paused: a dissolve over SR_XFADE_MS from
 * where it stands when the slot showed at the last sync too (MO_SPATIAL: a cut under Reduce
 * Motion), else at once, so a page opened on a paused song shows the pause. */
static void play_xfade_sync(void) {
    int shown = !!play_shown(), to = bar_status() == 2 ? 0 : 1000;
    if (shown && to != sr.p_to) {
        if (sr.seen) {
            sr.p_from = (short)play_p(), sr.p_to = (short)to;
            anim_start(&sr.xfade, SR_XFADE_MS, EASE_CUBIC_OUT, MO_SPATIAL);
            if (sr.xfade.ms) sr.xf_dirty = 1, motion_hook(sr_xfade_frame);
        } else {
            sr.p_from = sr.p_to = (short)to, sr.xfade.at = 0;
        }
    }
    sr.seen = (unsigned char)shown;
}

/* bar_state_sync, each bar paint and each second: the play state's dissolve started on a change,
 * the frames' timer started while lit and something moves; 1 when what the slots show changed
 * (which slots, the play state, Wi-Fi's level or its internet, the sync's kind and its ring to the
 * 50th, the Bluetooth slot's kind and the headset's level, the lock), so the bar repaints whole
 * after the frame and the clock moves beside the new leftmost slot. */
int status_row_sync(void) {
    if (!geom_val(V_STATUS_Y)) return 0;
    play_xfade_sync();
    if (!sr.timer && g_backlight_status && moving())
        sr.timer = timer_add(sr_tick, (void *)0, SR_FRAME_MS);
    int permille = 0, kind = addon_sync(&permille), wifi = wifi_level(),
        head = bt_headset_battery();
    unsigned sig = (unsigned)bar_status() | (unsigned)(wifi && wifi_offline() ? 5 : wifi) << 3 |
                   (unsigned)kind << 6 | (unsigned)(kind == 2 ? permille / 20 : 0) << 8 |
                   (unsigned)(head < 0     ? 127
                              : head > 100 ? 100
                                           : head)
                       << 14 |
                   (unsigned)!!g_keylock_flag << 21 | 1u << 22 | (unsigned)bt_slot() << 23 |
                   (unsigned)home_np_lead() << 26;
    if (sig == sr.sig) return 0;
    sr.sig = sig;
    return 1;
}
