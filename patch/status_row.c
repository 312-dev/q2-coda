/* The status row's glyph slots (docs/oled-design.md#status-row,
 * docs/internals.md#status-row-slots): right to left the battery, the play state's level bars,
 * Wi-Fi, a card app's sync, wired headphones, Bluetooth (its
 * link, a headset's battery, a car), Buttons Lock's padlock and a cloud service out of reach.
 * Each slot that shows takes its box and the gap before the one on its right. The clock stands at
 * the row's left (navigation.c clock_place) and the slots left of the battery are a strip with the
 * room from the clock to the battery (status_strip_room); a strip wider than that scrolls in it as
 * a marquee, its cut ends faded, and the battery never moves. Nothing in the row ever wraps. The
 * battery is stock's view_battery, laid out by the bar's right group and painted by navigation.c
 * paint_battery; every other slot is painted here, from the bar's background (ringnav_paint_bg), so
 * Home in Split inks them over its art (coverflow.c home_ink, one ink for the row). Every glyph is
 * the clock's digits' height, 11 px on rows 17 to 27 (cy 23), but the Bluetooth rune and wired
 * headphones, the battery's body's 12 on rows 16 to 27, with SR_GAP of ground
 * between two (docs/oled-design.md#status-row); the animated ones repaint only their own box. */
#include "navigation.h"

#define SR_TURN_MS 1000 /* the sync arrows' half turn (the glyph repeats every half turn) */
#define SR_BAR_LO 3     /* the level bars' heights, lo to SR_INK px (ui_kit.h lvl_h16) */
#define SR_SPIN 30      /* the arrows' frames a half turn, tools/art.py STATUS_SPIN: 30 a second */
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
#define SR_RUNE_W 9    /* tools/art.py STATUS_TALL_FIT: the Bluetooth rune 12 px tall is 8.4 wide */
#define SR_LOCK 13     /* the padlock's box, tools/art.py STATE_PX */
#define SR_RING 16     /* the download ring's box: a 14 px ring and its stroke */
#define SR_SPIN_W 12   /* the turning arrows' box: frame 0's ink, 12 of its SR_GLYPH art's 14 */
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
/* cloud-slash: 13.6 px of ink at 11 tall (tools/art.py status_offline) */
#define SR_OFFLINE SR_GLYPH
/* The strip's marquee (docs/oled-design.md#status-row): at rest SR_MARQ_HOLD_MS, its left end in
 * view, then sliding left at SR_MARQ_PX_S until its right end meets the battery's gap, held there
 * MARQUEE_HOLD_MS, then back to rest, as Now Playing's lines (np_marquee.c); a SR_MARQ_FADE px fade
 * on each end whose glyphs are cut, the house marquee's 12 tightened (the user's, 2026-11-19). */
#define SR_MARQ_HOLD_MS 2750
#define SR_MARQ_PX_S 35
#define SR_MARQ_FADE 6
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
    int spin;                 /* the arrows' frame */
    lvl_clock_t clock;        /* the bars' time along their loop */
    unsigned sig, nav;
    anim_t xfade;             /* the play state's dissolve from p_from to p_to */
    short p_from, p_to;       /* 0 the bars, 1000 the pause */
    unsigned char seen;       /* the play slot shown at the last sync */
    unsigned char xf_dirty;   /* the dissolve's box still to repaint */
    int down, down_x, down_y; /* the slot a touch went down on, + 1 */
    int room;                 /* the strip's left edge (status_strip_room), 0 for none */
    int over, off;            /* px the strip runs past its room, and how far it is slid now */
    unsigned marq_at;         /* the marquee's cycle's start */
} sr __attribute__((section(".scratch")));

#if Q2POD_DEV
/* --dev builds only, for the emulator, which has no radio or charger: STATUS_FAKE's "wifi=N
 * charge=N headphones=N bluetooth=N" (charge 1 a charging cable, 2 one holding it full; headphones
 * 0 none, 1 a jack, 2 a USB DAC; bluetooth 1 a device connected; each found by its first letter,
 * key) stands in for what the Q2 would report; -1 when the file does not name it. Read at most
 * every STATUS_FAKE_MS. */
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
    int s = bar_status();
    return s == 2 || s == 3 ? SR_BARS_W : 0;
}
static int wifi_shown(void) { return wifi_level() ? SR_WIFI_W : 0; }
/* The sync's box: the ring while it downloads; while it checks, the arrows' upright ink, so the
 * gaps beside them are SR_GAP of ground as every glyph's; turning, their corners pass up to 1 px
 * into it (tools/art.py status_turn: 10 to 14 px of ink across the frames). */
static int sync_shown(void) {
    int kind = addon_sync((int *)0);
    return kind == 2 ? SR_RING : kind ? SR_SPIN_W : 0;
}
/* The Bluetooth slot (docs/oled-design.md#status-row, P9): what the link is, BT_SLOT_*. Stock's
 * link list's last item is the device it links (status 3 connected, as its rows read it); a link
 * thread running (bt_connectstatus 1) is a device connecting. Nothing while wired headphones take
 * the sound (wired_on), but a guest's phone. */
enum {
    BT_SLOT_NONE,
    BT_SLOT_CONNECTING,
    BT_SLOT_RUNE,
    BT_SLOT_HEADSET,
    BT_SLOT_CAR,
    BT_SLOT_GUEST
};
static int wired_on(void);
static int bt_slot(void) {
    if (guest_active()) return BT_SLOT_GUEST; /* a guest plays (guest_ui.c) */
    if (wired_on()) return BT_SLOT_NONE;      /* the sound goes to the wire: its glyph alone */
    int fake = status_fake('b');              /* a --dev build's stand-in, -1 on a release */
    if (fake >= 0) return fake ? BT_SLOT_RUNE : BT_SLOT_NONE;
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
                                     : SR_RUNE_W;
}
static int lock_shown(void) { return g_keylock_flag ? SR_LOCK : 0; }
/* Wired headphones taking the sound: the player's output way 2, a USB DAC (USB-C headphones among
 * them), or 0, the Q2's own DAC, with either jack in (g_po_status 3.5 mm, g_bal_status 4.4 mm;
 * stock's way stays 0 with both out, and nothing then sounds). Bluetooth is way 1, so with a
 * headset linked and a jack in, the one the sound goes to shows (stock plays to the jack last
 * plugged in, docs/internals.md#eq-per-output). */
static int wired_on(void) {
    int way = status_fake('h'); /* a --dev build's stand-in, -1 on a release */
    if (way >= 0) return way > 0;
    way = mclGetOutputWay();
    return way == 2 || (!way && (g_po_status || g_bal_status));
}
/* No output connected (the user's, 2026-11-25): no wire, no Bluetooth device connected (one
 * connecting is not yet), no guest playing, no AirPlay speaker and not the car's USB audio (way 3,
 * usb_car.c). A Bluetooth way with nothing linked plays to no one, so it counts as none. */
/* The wired slot: headphones while they take the sound; nothing otherwise, with no output connected too (the owner's,
 * 2026-11-27: no deaf ear) */
static int wired_shown(void) { return wired_on() ? SR_GLYPH : 0; }
static int offline_shown(void) { return cloud_offline() ? SR_OFFLINE : 0; }

/* The slots' states as q2ui reads them (q2ui_gate.c q2ui_tell, ui/status.c), a key=value line each,
 * into b; its length. q2ui draws a status row of its own while it has the screen, but where the
 * sound goes, the Bluetooth link, Buttons Lock, a card app's sync and the cloud's reach are demo's alone
 * today, so demo says them, by the same tests its own row paints from:
 *   out=wire|other  wired headphones take the sound, or not
 *   bt=none|connecting|rune|headset|car|guest, btbatt=N  the Bluetooth slot, a headset's level
 *   relay=none|headphones|car  where a guest's phone plays through the Q2, while one plays
  *   lock=0|1  sync=0|1|2 syncpm=N  offline=0|1  net=0|1 (0: Wi-Fi linked with no internet)
 *   clock24=0|1 tz=TZ  the clock's 24-hour setting and time zone (autozone.c autozone_tz; empty for none) */
unsigned status_row_state(char *b, unsigned room) {
    static const char *const bt[] = { "none", "connecting", "rune", "headset", "car", "guest" };
    int slot = bt_slot(), permille = 0, sync = addon_sync(&permille);
    const char *relay = slot != BT_SLOT_GUEST || !guest_relayed() ? "none" : linked_w() == SR_CAR_W ? "car" : "headphones";
    int n = tk_snprintf(b, room, "out=%s\nbt=%s\nbtbatt=%d\nrelay=%s\nlock=%d\nsync=%d\nsyncpm=%d\noffline=%d\nnet=%d\n"
                                 "clock24=%d\ntz=%s\n",
                        wired_on() ? "wire" : "other", bt[slot],
                        slot == BT_SLOT_HEADSET ? bt_headset_battery() : -1, relay, !!g_keylock_flag, sync,
                        sync == 2 ? permille : 0, !!cloud_offline(), !wifi_offline(), !!g_time24h_flag, autozone_tz());
    return n < 0 ? 0 : (unsigned)n >= room ? room - 1 : (unsigned)n;
}

/* The play state, in Text Primary as every glyph: playing, the level bars (ui_kit.h lvl_h16, the
 * motion Up Next's and Home's share), 2 px wide with round ends, SR_BAR_LO to SR_INK tall at their
 * clock's time and standing on the digits' baseline; paused, the pause, two SR_PAUSE_W bars SR_INK
 * tall in the bars' own box. Between the two the stopped bars and the pause cross-dissolve
 * (play_paint, play_xfade_sync). play_bars: the glyph's bars at x on cy, each its left, top,
 * width, height and corner radius, the pause's two when pause, else the three level bars; returns
 * how many. */
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
        float *b = bar[i], h = (float)lvl_h16(i, lvl_time(&sr.clock), SR_BAR_LO, SR_INK) / 16;
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
        glyph(bar, canvas, stem, x, SR_SPIN_W, cy, SR_GLYPH);
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
        status_art(bar, canvas, "status_btdim", x, cy);
        break;
    case BT_SLOT_RUNE:
        status_art(bar, canvas, "status_bt", x, cy);
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

/* Wired headphones: Phosphor's headphones (regular), 12 px tall on rows 16 to 27 (tools/art.py
 * STATUS_TALL_FIT). */
static void wired_paint(void *bar, void *canvas, int x, int cy) { status_art(bar, canvas, "status_wired", x, cy); }

/* Buttons Lock's padlock (keylock.c), the theme's keylock art in Text Primary. */
static void lock_paint(void *bar, void *canvas, int x, int cy) {
    glyph(bar, canvas, "keylock", x, SR_LOCK, cy, SR_LOCK);
}

/* A service out of reach, a card app's server or one of demo's own (cloud_reach.c cloud_offline):
 * Phosphor's cloud-slash (bold) in Warning, in both halves. */
static void offline_paint(void *bar, void *canvas, int x, int cy) {
    status_art(bar, canvas, "status_offline", x, cy);
}

/* Right to left; the battery's own row is stock's widget (shown is never called for it). Every gap
 * is SR_GAP, iOS's (mock_status_icons.py): more than coverflow.c's INK_GAP, so Split still tells a
 * status colour's glyph from the rest. */
static const status_slot_t slots[SLOT_N] = {
    [SLOT_BATTERY] = { 0, 0, 0 },
    [SLOT_PLAY] = { play_shown, SR_GAP, play_paint },
    [SLOT_WIFI] = { wifi_shown, SR_GAP, wifi_paint },
    [SLOT_SYNC] = { sync_shown, SR_GAP, sync_paint },
    [SLOT_WIRED] = { wired_shown, SR_GAP, wired_paint },
    [SLOT_HEADSET] = { headset_shown, SR_GAP, headset_paint },
    [SLOT_LOCK] = { lock_shown, SR_GAP, lock_paint },
    [SLOT_OFFLINE] = { offline_shown, SR_GAP, offline_paint },
};

/* The strip's room's left edge, the bar's pixels (clock_place: past the widest clock at the row's
 * left); 0 for none. */
void status_strip_room(int left) { sr.room = left > 0 ? left : 0; }

/* How far the marquee has slid at t ms into its cycle, 0 to over. */
static int strip_off(int over, unsigned t) {
    unsigned slide = (unsigned)over * 1000u / SR_MARQ_PX_S,
             cycle = SR_MARQ_HOLD_MS + slide + MARQUEE_HOLD_MS;
    t %= cycle;
    if (t < SR_MARQ_HOLD_MS) return 0;
    t -= SR_MARQ_HOLD_MS;
    return t >= slide ? over : (int)(t * SR_MARQ_PX_S / 1000u);
}

/* The slots laid out from the battery's left, in the bar's pixels (its right group is a child of
 * the bar, which sits at the screen's top); returns the leftmost shown slot's left, or -1 with no
 * battery slot yet. A strip that runs past its room's left edge (sr.over px) is moved right by what
 * the marquee has not slid yet, so at rest its left end is in view, and the room's edge is
 * returned. */
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
    sr.over = sr.room && edge < sr.room ? sr.room - edge : 0;
    if (!sr.over) return sr.off = 0, edge;
    if (sr.off > sr.over) sr.off = sr.over;
    for (int i = SLOT_BATTERY + 1; i < SLOT_N; ++i)
        if (sr.w[i]) sr.x[i] += sr.over - sr.off;
    return sr.room;
}

/* The strip's ends that fade now: 1 the left while it is slid, 2 the right while it runs past */
static int strip_ends(void) { return (sr.off > 0) | (sr.off < sr.over) << 1; }

/* The strip's box while it scrolls: from its room's edge to SR_GAP short of the battery. */
static rect_t strip_box(void *bar) {
    rect_t r = { sr.room, 0, sr.x[SLOT_BATTERY] - SR_GAP - sr.room, bar ? I(bar, W_H) : 0 };
    return r;
}

/* The leftmost shown slot's left (the battery's with no other), the edge the volume pill ends short
 * of; the screen's right edge before the bar is found. */
int status_slots_left(void) {
    int left = layout();
    return left < 0 ? 375 : left;
}

/* The room the shown slots take left of the battery: each one's width and the gap before it. */
int status_slots_reach(void) {
    int reach = 0;
    for (int i = SLOT_BATTERY + 1; i < SLOT_N; ++i) {
        int w = slots[i].shown();
        if (w) reach += slots[i].gap + w;
    }
    return reach;
}

/* The bar's background paint under the status row: every shown slot but the battery and the volume
 * pill while it shows, in final colours (st.raw_ink), restoring the canvas's fill. */
void status_slots_paint(void *bar, void *canvas) {
    void *lcd = P(canvas, CANVAS_LCD);
    if (!lcd || layout() < 0) return;
    unsigned fill = (unsigned)I(lcd, LCD_FILL_COLOR), raw = ui_ink_raw(1);
    int cy = geom_val(V_STATUS_Y), was[4], faded = 0;
    /* a strip that scrolls: clipped to its box (the vgcanvas takes the canvas's clip), its cut ends
     * faded */
    if (sr.over) {
        rect_t box = strip_box(bar);
        int ox = I(canvas, CANVAS_X), oy = I(canvas, CANVAS_Y);
        canvas_get_clip_rect(canvas, was);
        rect_t in = { box.x + ox, box.y + oy, box.w, box.h };
        if (in.x < was[0]) in.w -= was[0] - in.x, in.x = was[0];
        if (in.y < was[1]) in.h -= was[1] - in.y, in.y = was[1];
        if (in.x + in.w > was[0] + was[2]) in.w = was[0] + was[2] - in.x;
        if (in.y + in.h > was[1] + was[3]) in.h = was[1] + was[3] - in.y;
        if (in.w < 0) in.w = 0;
        if (in.h < 0) in.h = 0;
        canvas_set_clip_rect(canvas, &in);
        faded = text_fade_under_n(canvas, box.x, box.x + box.w, box.y, box.h, strip_ends(),
                                  SR_MARQ_FADE);
    }
    for (int i = SLOT_BATTERY + 1; i < SLOT_N; ++i)
        if (sr.w[i]) slots[i].paint(bar, canvas, sr.x[i], cy);
    if (sr.over) {
        if (faded) text_fade(canvas);
        canvas_set_clip_rect(canvas, (rect_t *)was);
    }
    vol_pill_paint(bar, canvas); /* a volume change's pill, in the clock's place (vol_pill.c) */
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
    rect_t strip = strip_box(bar);
    for (int i = 0; i < SLOT_N; ++i) {
        if (i != SLOT_BATTERY && !sr.w[i]) continue;
        int cx = sr.x[i] + (i == SLOT_BATTERY && !st.batt_mode ? geom_val(V_BATT_W) : sr.w[i]) / 2,
            d = x - cx;
        if (i != SLOT_BATTERY && sr.over && (cx < strip.x || cx >= strip.x + strip.w))
            continue; /* scrolled out of the strip's box */
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

/* Whether the arrows turn: an app checks. */
static int moving(void) { return addon_sync((int *)0) == 1; }
/* The shared loop's (ui_kit.c motion_loop), MO_LOOP_MS apart while lit and the arrows turn, as the
 * level bars' frames: their frame the clock's place in SR_TURN_MS, and only their art's box
 * repainted when it changed, a px either side of the slot's (ringnav_paint_bg paints it with the
 * bar's background under it); 0 (dropped) once they stop. A report of what the app does, so they
 * turn under Reduce Motion too. */
static int sr_spin_frame(void) {
    void *bar = *(void *const *)system_bar;
    if (!g_backlight_status || !bar || !moving()) return 0;
    layout();
    int spin = (int)((unsigned)time_now_ms() % SR_TURN_MS * SR_SPIN / SR_TURN_MS);
    if (sr.w[SLOT_SYNC] && spin != sr.spin) {
        sr.spin = spin;
        rect_t r = { sr.x[SLOT_SYNC] - (SR_GLYPH - SR_SPIN_W) / 2, 0, SR_GLYPH, I(bar, W_H) };
        widget_invalidate_force(bar, &r);
    }
    return 1;
}

/* The play box repainted, when the bar is lit and the slot shows (only that box: the rest of the
 * row stays as it is). */
static void play_box_dirty(void) {
    void *bar = *(void *const *)system_bar;
    if (!bar || !g_backlight_status || layout() < 0 || !sr.w[SLOT_PLAY]) return;
    rect_t r = { sr.x[SLOT_PLAY], 0, SR_BARS_W, I(bar, W_H) };
    widget_invalidate_force(bar, &r);
}
/* Whether the bars move: lit, playing, the slot shown and not dissolving, and motion allowed
 * (MO_DECOR: still under Reduce Motion). Their clock runs only then. */
static int bars_moving(void) {
    return g_backlight_status && bar_status() == 2 && play_shown() && !anim_running(&sr.xfade) &&
           motion_ms(MO_LOOP_MS, MO_DECOR);
}
/* The shared loop timer's (ui_kit.c motion_loop), MO_LOOP_MS apart while the bars move: their box
 * repainted at the clock's time; 0 (dropped) once they stop. */
static int sr_bars_frame(void) {
    int on = bars_moving();
    lvl_run(&sr.clock, on);
    if (on) play_box_dirty();
    return on;
}
/* The bars' clock and loop started when they move, the clock stopped when they don't. */
static void bars_sync(void) {
    if (bars_moving())
        lvl_run(&sr.clock, 1), motion_loop(sr_bars_frame);
    else
        lvl_run(&sr.clock, 0);
}
/* The shared frame timer's (ui_kit.c motion_hook), while the play state dissolves: its box
 * repainted, and once more after the dissolve's end, when the bars move on from where they stood.
 */
static void sr_xfade_frame(void) {
    if (!sr.xf_dirty) return;
    if (!anim_running(&sr.xfade)) sr.xf_dirty = 0, bars_sync(); /* this repaint shows the end */
    play_box_dirty();
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
    bars_sync();
}

/* The shared loop's (ui_kit.c motion_loop) while the strip runs past its room and the screen is
 * lit: the marquee's offset at its cycle's time, the strip's box alone repainted when it moved; 0
 * (dropped) once the strip fits. A report of what shows (MO_FEEDBACK's kind), so it runs under
 * Reduce Motion too, as the house marquee does. */
static int sr_strip_frame(void) {
    void *bar = *(void *const *)system_bar;
    if (!bar || !g_backlight_status || layout() < 0 || !sr.over) return sr.off = 0, 0;
    int off = strip_off(sr.over, (unsigned)time_now_ms() - sr.marq_at);
    if (off != sr.off) {
        sr.off = off;
        rect_t r = strip_box(bar);
        widget_invalidate_force(bar, &r);
    }
    return 1;
}

/* bar_state_sync, each bar paint and each second: the play state's dissolve started on a change,
 * the bars' loop while they move, the strip's marquee while it runs past its room (from rest when
 * it newly does), the arrows' loop while lit and they turn; 1 when what the slots show changed
 * (which slots, the play state, Wi-Fi's level or its internet, the sync's kind and its ring to the
 * 50th, wired headphones, the Bluetooth slot's kind and the headset's level, the
 * lock, a cloud service out of reach, the strip scrolling or not), so the bar repaints whole after the
 * frame. */
int status_row_sync(void) {
#if Q2POD_DEV
    vol_pill_fake(); /* the emulator's stand-in for a headset's volume (vol_pill.c) */
#endif
    play_xfade_sync();
    int over = sr.over;
    if (layout() >= 0 && sr.over && g_backlight_status) {
        if (sr.over != over)
            sr.marq_at = (unsigned)time_now_ms(), sr.off = 0; /* a new strip starts at rest */
        motion_loop(sr_strip_frame);
    }
    if (g_backlight_status && moving()) motion_loop(sr_spin_frame);
    int permille = 0, kind = addon_sync(&permille), wifi = wifi_level(),
        head = bt_headset_battery();
    unsigned sig = (unsigned)bar_status() | (unsigned)(wifi && wifi_offline() ? 5 : wifi) << 3 |
                   (unsigned)kind << 6 | (unsigned)(kind == 2 ? permille / 20 : 0) << 8 |
                   (unsigned)(head < 0     ? 127
                              : head > 100 ? 100
                                           : head)
                       << 14 |
                   (unsigned)!!g_keylock_flag << 21 | 1u << 22 | (unsigned)bt_slot() << 23 |
                   (unsigned)cloud_offline() << 27 |
                   (unsigned)!!sr.over << 28 | (unsigned)wired_on() << 29;
    if (sig == sr.sig) return 0;
    sr.sig = sig;
    return 1;
}
