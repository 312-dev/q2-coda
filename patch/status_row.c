/* The status row's glyph slots (docs/oled-design.md#status-row,
 * docs/internals.md#status-row-slots): right to left the battery, the play state's level bars,
 * Wi-Fi, a card app's sync, a Bluetooth headset's battery and Buttons Lock's padlock. Each slot
 * that shows takes its box and the gap before the one on its right; the clock sits beside the
 * leftmost (navigation.c clock_place), so nothing in the row ever wraps: the title gives way. The
 * battery is stock's view_battery, laid out by the bar's right group and painted by navigation.c
 * paint_battery; every other slot is painted here, from the bar's background (ringnav_paint_bg), so
 * Home in Split inks them by contrast with the rest (coverflow.c home_ink): slots keep INK_GAP
 * columns of ground between them, and the animated ones repaint only their own box. */
#include "navigation.h"

#define SR_FRAME_MS 125 /* the level bars and the sync arrows: 8 frames a second */
#define SR_FRAMES 10    /* the bars' loop, 1.25 s */
#define SR_SPIN 8       /* the arrows' frames, tools/art.py STATUS_SPIN: a half turn a second */
#define SR_BARS_W 10    /* three 2 px bars, 4 px apart */
#define SR_BAR_W 2
#define SR_BAR_PITCH 4
#define SR_BAR_DROP 6  /* the bars stand this far below the row's centre */
#define SR_GLYPH 14    /* tools/art.py STATUS_PX */
#define SR_LOCK 13     /* the padlock's box, tools/art.py STATE_PX */
#define SR_RING 16     /* the download ring's box: a 14 px ring and its stroke */
#define SR_RING_R 7.0f /* its centre line's radius */
#define SR_RING_LINE 1.6f
#define SR_DOWN 8   /* the arrow inside it */
#define SR_BT_GAP 2 /* the Bluetooth glyph's box, then the headset's percentage */
#define SR_TAP_W 44 /* a slot's tap target round its centre, as the battery's */
#define SR_TAP_H 40
#define SR_TAP_SLOP 12
#define SR_TOUCH_DOWN 0xff /* the raw pointer events ringnav_input sees (EVT_POINTER_FIRST) */
#define SR_TOUCH_UP 0x103
#define SR_SECONDARY_D 0xababab /* Text Secondary per half: the paused bars */
#define SR_SECONDARY_L 0x5c5c5c
#define SR_TRACK_D 0x333333 /* the ring's track per half */
#define SR_TRACK_L 0xd9d9d9

/* A slot: its box's width now (0: not shown), the room before the shown slot to its right, and its
 * painter, given its box's left and the row's centre in the bar's pixels, in final colours. */
typedef struct {
    int (*shown)(void);
    unsigned char gap;
    void (*paint)(void *bar, void *canvas, int x, int cy);
} status_slot_t;

static struct {
    int x[SLOT_N], w[SLOT_N]; /* the last layout, in the bar's pixels */
    int frame, spin;          /* the bars' frame (held while paused) and the arrows' */
    unsigned timer, sig, nav;
    int down, down_x, down_y; /* the slot a touch went down on, + 1 */
} sr __attribute__((section(".scratch")));

/* The Bluetooth headset's battery, 0-100, or -1 with none connected or none reported. ponytail:
 * a stub. The bt worker's file reads BlueZ's org.bluez.Battery1 and provides the real one; this
 * one goes at that merge. */
int bt_headset_battery(void) { return -1; }

/* Stock's status-bar Wi-Fi (img_wifi, off-screen under a status row, its image kept current by
 * systembar_showface): bar_wifi1 to bar_wifi4 while connected, as get_wifisignal's bars; 0 for
 * anything else (off, Smart Wi-Fi's radio off, not connected, bar_wifi0). No I/O. */
static int wifi_level(void) {
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
static int wifi_offline(void) { return networkGetConnectStatus() <= 0; }

/* The percentage's width at BATT_PX, as navigation.c measures the battery's (st.pct_digit, sign),
 * estimated before the battery's first paint measured them. */
static int pct_w(int level) {
    int digit = st.pct_digit ? st.pct_digit : 10, sign = st.pct_sign ? st.pct_sign : 14;
    return (level >= 100 ? 3 : level >= 10 ? 2 : 1) * digit + sign;
}

static int play_shown(void) {
    int s = bar_status();
    return s == 2 || s == 3 ? SR_BARS_W : 0;
}
static int wifi_shown(void) { return wifi_level() ? SR_GLYPH : 0; }
static int sync_shown(void) { return addon_sync((int *)0) ? SR_RING : 0; }
/* The headset's slot at level (bt_headset_battery's): its width, 0 for none */
static int headset_w(int level) {
    return level >= 0 ? SR_GLYPH + SR_BT_GAP + pct_w(level > 100 ? 100 : level) : 0;
}
static int headset_shown(void) { return headset_w(bt_headset_battery()); }
static int lock_shown(void) { return g_keylock_flag ? SR_LOCK : 0; }

/* The level bars (Up Next's, upnext.c un_level): playing, Text Primary, moving a frame a tick;
 * paused, held at their last heights in Text Secondary. */
static const unsigned char bar_h[SR_FRAMES][3] = { { 7, 10, 3 }, { 9, 8, 3 },  { 11, 5, 5 },
                                                   { 11, 3, 7 }, { 9, 3, 10 }, { 7, 4, 11 },
                                                   { 5, 6, 11 }, { 3, 9, 9 },  { 3, 11, 7 },
                                                   { 5, 11, 4 } };
static void play_paint(void *bar, void *canvas, int x, int cy) {
    (void)bar;
    int light = ipod_light(), paused = bar_status() != 2;
    unsigned rgb = paused ? (light ? SR_SECONDARY_L : SR_SECONDARY_D) : (light ? 0 : 0xffffff);
    for (int i = 0; i < 3; ++i) {
        int h = bar_h[sr.frame % SR_FRAMES][i];
        rect_t r = { x + i * SR_BAR_PITCH, cy + SR_BAR_DROP - h, SR_BAR_W, h };
        fill_box(canvas, &r, RGBA(rgb), 1);
    }
}

/* A theme glyph of box px centred in a slot box w wide on cy, its top rounded up as stock's image
 * widget centres one (an odd box's half row above), so the padlock lands where its widget put it.
 */
static void glyph(void *bar, void *canvas, const char *stem, int x, int w, int cy, int px) {
    char name[40];
    tk_snprintf(name, sizeof name, "%s_%c", stem, ipod_light() ? 'l' : 'd');
    paint_art(bar, canvas, theme_art(name), x + (w - px) / 2, cy - (px + 1) / 2);
}

/* Wi-Fi: 1 to 3 lit arcs by the bars (1 one, 2 two, 3 and 4 all three), the rest dim
 * (tools/art.py status_wifi); wifi-x connected with no internet. */
static void wifi_paint(void *bar, void *canvas, int x, int cy) {
    int level = wifi_level();
    char stem[24];
    if (wifi_offline())
        tk_snprintf(stem, sizeof stem, "status_wifi_x");
    else
        tk_snprintf(stem, sizeof stem, "status_wifi_%d", level > 3 ? 3 : level);
    glyph(bar, canvas, stem, x, SR_GLYPH, cy, SR_GLYPH);
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
        vgcanvas_set_stroke_color(vg, RGBA(light ? SR_TRACK_L : SR_TRACK_D));
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

/* The headset: the Bluetooth glyph, then its level and "%" in the battery's percentage type, Text
 * Primary, on one line. */
static void headset_draw(void *bar, void *canvas, int x, int cy, int level) {
    int px = BATT_PX;
    unsigned s[5], n = put_num(s, (unsigned)(level > 100 ? 100 : level));
    s[n++] = '%';
    glyph(bar, canvas, "status_bt", x, SR_GLYPH, cy, SR_GLYPH);
    void *lcd = P(canvas, CANVAS_LCD);
    unsigned text = lcd ? (unsigned)I(lcd, LCD_TEXT_COLOR) : 0;
    canvas_set_font(canvas, theme_face(s, (int)n, &px), (unsigned)px);
    canvas_set_text_color(canvas, RGBA(ipod_light() ? 0 : 0xffffff));
    canvas_draw_text(canvas, s, n, x + SR_GLYPH + SR_BT_GAP, cy - px / 2);
    if (lcd) stock_canvas_set_text_color(canvas, text);
}
static void headset_paint(void *bar, void *canvas, int x, int cy) {
    headset_draw(bar, canvas, x, cy, bt_headset_battery());
}

/* Buttons Lock's padlock (keylock.c), the theme's keylock art in Text Primary. */
static void lock_paint(void *bar, void *canvas, int x, int cy) {
    glyph(bar, canvas, "keylock", x, SR_LOCK, cy, SR_LOCK);
}

/* Right to left; the battery's own row is stock's widget (shown is never called for it). The gaps
 * are mock_status_bars.py's, mock_wifi_status.py's and mock_sync_status.py's: each at least INK_GAP
 * (coverflow.c) columns of ground, so Split inks every glyph by itself. */
static const status_slot_t slots[SLOT_N] = {
    [SLOT_BATTERY] = { 0, 0, 0 },
    [SLOT_PLAY] = { play_shown, 4, play_paint },
    [SLOT_WIFI] = { wifi_shown, 5, wifi_paint },
    [SLOT_SYNC] = { sync_shown, 4, sync_paint },
    [SLOT_HEADSET] = { headset_shown, 5, headset_paint },
    [SLOT_LOCK] = { lock_shown, 5, lock_paint },
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
    if (playing && sr.w[SLOT_PLAY]) {
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

/* bar_state_sync, each bar paint and each second: the frames' timer started while lit and
 * something moves; 1 when what the slots show changed (which slots, the play state, Wi-Fi's level
 * or its internet, the sync's kind and its ring to the 50th, the headset's level, the lock), so the
 * bar repaints whole after the frame and the clock moves beside the new leftmost slot. */
int status_row_sync(void) {
    if (!geom_val(V_STATUS_Y)) return 0;
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
                   (unsigned)!!g_keylock_flag << 21 | 1u << 22;
    if (sig == sr.sig) return 0;
    sr.sig = sig;
    return 1;
}
