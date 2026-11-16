/* Control Center (docs/oled-design.md#control-center, approved as V3a on 2026-11-15 from
 * design/mockups/mock_cc3.py cc3(True)): stock's pull-down (statusbar_dialog, Quick Settings)
 * drawn as iOS's Control Center under the status row. A block of four round toggles, Wi-Fi,
 * Bluetooth, AirPlay and Car Remote; beside it Now Playing, the song's cover, title and artist over
 * Previous, Play/Pause and Next; under both a Brightness slider and a Volume slider; then five
 * square tiles, Buttons Lock, Sleep Timer, Night Shift, EQ and Smart Wi-Fi, each named under it;
 * and a grab bar at the foot, where the swipe up that closes it starts.
 *
 * Stock's widgets stay for what stock does: its Wi-Fi and Bluetooth buttons, at opacity 0 over their
 * circles, take the taps and holds (navigation.c quick_down: held, their settings pages), and its
 * brightness slider, at opacity 0 over the Brightness slider, takes the drags; Centre on those
 * clicks them as a tap would. Every other control is a clear button of this file's (cc_click).
 * The wheel walks CC_STOPS, the toggles, the two sliders and the tiles in reading order, a white
 * ring round the one it is on; Centre acts on it, and on a slider gives it the wheel (an accent
 * ring) until Centre or Return gives it back. Return on a stop closes the page.
 * ponytail: AirPlay and Sleep Timer open their settings pages (stock's receiver switch and timer
 * have no call this file can make without their page), so their circle and tile are lit only by
 * AirPlay's flag and never by a running timer; the Wi-Fi circle reads "Wi-Fi", not the network's
 * name, which only the Wi-Fi page reads. */
#include "navigation.h"

extern int ar_text(void *canvas, const char *s, int medium, int px, unsigned rgb, int raw, int x,
                   int y, int w, int align);
extern void ar_corners(void *canvas, const rect_t *r, int R, unsigned behind); /* artist_page.c */
extern void keylock_toggle(void), coda_rc_toggle(void);                       /* navigation.c, coda_rc_shim.c */
extern unsigned char now_cover(void *r, const char **file);                   /* coverflow.c */

/* The layout, in the window's (the screen's) pixels, as mock_cc3.py draws it at 1x */
#define CC_X0 20
#define CC_X1 355
#define CC_Y0 38
#define CC_BLOCK_H 124
#define CC_BLOCK_W 138
#define CC_BLOCK_R 22
#define CC_GAP 8
#define CC_CIRCLE_R 20
#define CC_ICON 22
#define CC_LABEL_PX 13 /* Inter Medium 11 px, AWTK's size */
#define CC_ART 52
#define CC_ART_R 9
#define CC_SLIDER_Y (CC_Y0 + CC_BLOCK_H + CC_GAP)
#define CC_SLIDER_H 38
#define CC_TILE 44
#define CC_TILE_R 12
#define CC_TILE_Y (CC_SLIDER_Y + CC_SLIDER_H + 30)
#define CC_GRAB_Y 304
#define CC_RING 3 /* the focus ring's outer edge past the control, 2 px wide */
#define CC_BLOCK 0x1c1c1e
#define CC_OFF 0x3a3a3c
#define CC_LIGHT 0xe5e5ea
#define CC_SECONDARY 0xababab
#define CC_GRAB 0x5a5a5e
#define CC_FOCUS "_cc_focus"   /* the window's stop the wheel is on */
#define CC_ADJUST "_cc_adjust" /* 1 while a slider has the wheel */

enum { CC_WIFI, CC_BT, CC_AIRPLAY, CC_CAR, CC_BRIGHT, CC_VOLUME, CC_LOCK, CC_SLEEP, CC_NIGHT,
       CC_EQ, CC_SMART, CC_STOPS, CC_PREV = CC_STOPS, CC_PLAY, CC_NEXT, CC_BUTTONS };
static const char *const cc_labels[CC_STOPS] = { "Wi-Fi", "Bluetooth",   "AirPlay",
                                                 "Car",   0,             0,
                                                 "Lock",  "Sleep",       "Night Shift",
                                                 "EQ",    "Smart Wi-Fi" };
/* Each stop's glyph (tools/art.py CC_ICONS): <art>cc_<name> */
static const char *const cc_glyphs[CC_BUTTONS] = {
    "wifi", "bluetooth", "airplay", "car", "sun", "speaker", "lock", "timer",
    "moon", "eq",        "leaf",    "back", "pause", "forward"
};

/* Stop i's box on the window: a circle's square, a slider's track, a tile, a transport glyph's. */
static rect_t cc_box(int i) {
    if (i <= CC_CAR) {
        int cx = CC_X0 + 35 + (i % 2) * 68, cy = CC_Y0 + 28 + (i / 2) * 60;
        return (rect_t){ cx - CC_CIRCLE_R, cy - CC_CIRCLE_R, 2 * CC_CIRCLE_R, 2 * CC_CIRCLE_R };
    }
    int half = (CC_X1 - CC_X0 - CC_GAP) / 2;
    if (i == CC_BRIGHT || i == CC_VOLUME)
        return (rect_t){ i == CC_BRIGHT ? CC_X0 : CC_X1 - half, CC_SLIDER_Y, half, CC_SLIDER_H };
    if (i < CC_STOPS) { /* five tiles, 12 px in from the ends, evenly apart */
        int k = i - CC_LOCK, cx = CC_X0 + 12 + CC_TILE / 2 + k * (CC_X1 - CC_X0 - 24 - CC_TILE) / 4;
        return (rect_t){ cx - CC_TILE / 2, CC_TILE_Y - CC_TILE / 2, CC_TILE, CC_TILE };
    }
    int nx = CC_X0 + CC_BLOCK_W + CC_GAP, mid = nx + (CC_X1 - nx) / 2,
        cx = mid + (i - CC_PLAY) * 52, s = i == CC_PLAY ? 30 : CC_ICON;
    return (rect_t){ cx - s / 2 - 6, CC_Y0 + 94 - s / 2 - 6, s + 12, s + 12 }; /* a 6 px reach */
}

static int cc_lit(int i) {
    switch (i) {
    case CC_WIFI: return g_wififlag != 0;
    case CC_BT: return g_bluetoothflag != 0;
    case CC_AIRPLAY: return g_airplayflag != 0;
    case CC_CAR: return coda_rc_enabled();
    case CC_LOCK: return g_keylock_flag != 0;
    case CC_NIGHT: return night_on() != 0;
    case CC_EQ: return g_equalizer_flag != 0;
    case CC_SMART: return st.smart_wifi != 0;
    default: return 0;
    }
}

/* A rounded box of rgb (0xRRGGBB) with its ring when lit by the wheel: white, or the accent while
 * a slider has the wheel. */
static void cc_shape(void *canvas, rect_t r, int radius, unsigned rgb, int ring, unsigned behind) {
    if (ring) {
        rect_t o = { r.x - CC_RING, r.y - CC_RING, r.w + 2 * CC_RING, r.h + 2 * CC_RING },
               g = { r.x - 1, r.y - 1, r.w + 2, r.h + 2 };
        fill_box(canvas, &o, RGBA(ring == 2 ? scheme()->tones[0] : 0xffffff),
                 (unsigned)(radius + CC_RING));
        fill_box(canvas, &g, RGBA(behind), (unsigned)(radius + 1));
    }
    fill_box(canvas, &r, RGBA(rgb), (unsigned)radius);
}
static void cc_glyph(void *w, void *canvas, int i, int cx, int cy, int px) {
    char name[40];
    tk_snprintf(name, sizeof name, "%scc_%s", theme()->art, cc_glyphs[i]);
    if (i == CC_PLAY && mclGetPlayStatus() != 2) tk_snprintf(name, sizeof name, "%scc_play", theme()->art);
    paint_art(w, canvas, name, cx - px / 2, cy - px / 2);
}

/* A slider's level, 0 to 256: Brightness's stock slider, Volume's g_volume of 100. */
static int cc_level(void *win, int i) {
    if (i == CC_VOLUME) return g_volume >= 100 ? 256 : g_volume * 256 / 100;
    void *s = widget_lookup(win, "slider_backlight", 1);
    int min = s ? widget_get_prop_int(s, "min", 0) : 0, max = s ? widget_get_prop_int(s, "max", 100) : 100;
    return s && max > min ? (widget_get_prop_int(s, "value", 0) - min) * 256 / (max - min) : 0;
}

/* The page, under the window's children (navigation.c's paint of statusbar_dialog): the status row
 * the dialog covers first, its clock centred, then the blocks, whose top reaches up into it. */
void cc_paint(void *win, void *canvas) {
    void *bar = *(void *const *)system_bar;
    unsigned fill = (unsigned)I(P(canvas, CANVAS_LCD), LCD_FILL_COLOR), accent = scheme()->tones[0];
    int focus = widget_get_prop_int(win, CC_FOCUS, 0), adjust = widget_get_prop_int(win, CC_ADJUST, 0);
    rect_t block = { CC_X0, CC_Y0, CC_BLOCK_W, CC_BLOCK_H },
           np = { CC_X0 + CC_BLOCK_W + CC_GAP, CC_Y0, CC_X1 - CC_X0 - CC_BLOCK_W - CC_GAP, CC_BLOCK_H };
    if (bar) widget_paint(bar, canvas);
    fill_box(canvas, &block, RGBA(CC_BLOCK), CC_BLOCK_R);
    fill_box(canvas, &np, RGBA(CC_BLOCK), CC_BLOCK_R);
    for (int i = 0; i < CC_STOPS; ++i) {
        rect_t r = cc_box(i);
        int lit = cc_lit(i), ring = i == focus ? 1 + (adjust && (i == CC_BRIGHT || i == CC_VOLUME)) : 0;
        if (i == CC_BRIGHT || i == CC_VOLUME) {
            cc_shape(canvas, r, r.h / 2, CC_OFF, ring, 0);
            int k = cc_level(win, i), fw = r.w * k / 256;
            rect_t f = { r.x, r.y, fw > r.h ? fw : r.h, r.h };
            fill_box(canvas, &f, RGBA(CC_LIGHT), (unsigned)(r.h / 2));
            cc_glyph(win, canvas, i, r.x + r.h / 2 + 2, r.y + r.h / 2, 18);
            continue;
        }
        cc_shape(canvas, r, i <= CC_CAR ? CC_CIRCLE_R : CC_TILE_R, lit ? accent : CC_OFF, ring,
                 i <= CC_CAR ? CC_BLOCK : 0);
        cc_glyph(win, canvas, i, r.x + r.w / 2, r.y + r.h / 2, CC_ICON);
        ar_text(canvas, cc_labels[i], 1, CC_LABEL_PX, lit ? 0xffffff : CC_SECONDARY, 0, r.x + r.w / 2,
                r.y + r.h + 9, 120, 1);
    }
    /* Now Playing: the cover, the title and artist, and the transport */
    unsigned pos, n;
    void *rec = queue_now(&pos, &n);
    rect_t art = { np.x + 12, CC_Y0 + 12, CC_ART, CC_ART };
    const char *cover = 0;
    char url[600] = "file://";
    if (rec) now_cover(rec, &cover);
    unsigned bitmap[64]; /* bitmap_t: width @0, height @4 */
    if (cover && *cover && strlen(cover) < sizeof url - 8) memcpy(url + 7, cover, strlen(cover) + 1);
    if (cover && *cover && !widget_load_image(win, url, bitmap) && bitmap[0] && bitmap[1]) {
        int bw = (int)bitmap[0], bh = (int)bitmap[1], side = bw < bh ? bw : bh;
        rect_t src = { (bw - side) / 2, (bh - side) / 2, side, side };
        canvas_draw_image(canvas, bitmap, &src, &art);
        ar_corners(canvas, &art, CC_ART_R, CC_BLOCK);
    } else
        fill_box(canvas, &art, RGBA(CC_OFF), CC_ART_R);
    int tx = art.x + CC_ART + 8, tw = np.x + np.w - 12 - tx;
    ar_text(canvas, rec ? now_tag(rec, REC_TITLE) : "Not Playing", 1, 19, 0xffffff, 0, tx,
            CC_Y0 + 28, tw, 2);
    if (rec) ar_text(canvas, now_tag(rec, REC_ARTIST), 0, 16, CC_SECONDARY, 0, tx, CC_Y0 + 48, tw, 2);
    for (int i = CC_PREV; i <= CC_NEXT; ++i) {
        rect_t r = cc_box(i);
        cc_glyph(win, canvas, i, r.x + r.w / 2, r.y + r.h / 2, i == CC_PLAY ? 30 : CC_ICON);
    }
    rect_t grab = { 375 / 2 - 18, CC_GRAB_Y, 36, 5 };
    fill_box(canvas, &grab, RGBA(CC_GRAB), 3);
    st.paint_w = win;
    stock_canvas_set_fill_color(canvas, fill);
}

/* Volume to v of 100, up to Volume Limit, saved for the output in use, as Car Remote sets it. */
static void cc_volume(int v) {
    if (v < 0) v = 0;
    if (v > g_maxvolume) v = g_maxvolume;
    if (v == g_volume) return;
    g_volume = (unsigned char)v;
    device_set_volume(v, 1);
    write_int_config(v, "PLAYSET", mclGetOutputWay() == 1 ? "BT_VOLUME" : "VOLUME");
}

/* Stock's own widget under stop i, which its tap and its Centre click: Wi-Fi's, Bluetooth's, the
 * brightness slider. */
static void *cc_stock(void *win, int i) {
    return widget_lookup(win, i == CC_WIFI ? "img_wifiswitch" : i == CC_BT ? "img_btswitch" : "slider_backlight", 1);
}

/* Stop i acted on (a tap or Centre); 1 when the page goes, for a page that opens. */
static int cc_act(void *win, int i) {
    unsigned click[12];
    switch (i) {
    case CC_WIFI:
    case CC_BT: {
        void *b = cc_stock(win, i);
        if (b) stock_dispatch_trampoline(b, pointer_event_init(click, EVT_CLICK, b, 0, 0));
        break;
    }
    case CC_AIRPLAY:
    case CC_SLEEP:
        dialog_quit(win, 0);
        navigator_to(i == CC_AIRPLAY ? "systemset/netservice_page" : "systemset/sleepshutdown_page");
        return 1;
    case CC_CAR: coda_rc_toggle(); break;
    case CC_LOCK: keylock_toggle(); break;
    case CC_NIGHT:
        night_set(night_on() ? 0 : 1, night_kelvin());
        night_save();
        break;
    case CC_EQ: eq_toggle(); break;
    case CC_SMART: /* Battery's row's setting (navigation.c pod_click, smart_wifi.c) */
        st.smart_wifi = !st.smart_wifi;
        write_int_config(st.smart_wifi, "Q2POD", "SMARTWIFI");
        break;
    case CC_PREV: prev_quick_click(); break;
    case CC_PLAY: player_play_pause(); break;
    case CC_NEXT: next_quick_click(); break;
    }
    widget_invalidate_force(win, (void *)0);
    return 0;
}

static int cc_click(void *ctx, void *event) {
    void *win = window_manager_get_top_window(window_manager());
    if (win) cc_act(win, (int)(long)ctx);
    return 0;
}
/* A finger on the Volume slider sets it where it is, from its press through its drag. */
static int cc_drag(void *track, void *event) {
    int x = I(event, EVENT_X), w = I(track, W_W), at = x;
    for (void *p = track; p; p = P(p, W_PARENT)) at -= I(p, W_X);
    if (w > 0 && pointer_down()) {
        cc_volume((at < 0 ? 0 : at > w ? w : at) * 100 / w);
        widget_invalidate_force(window_of(track), (void *)0);
    }
    return 0;
}

/* The page laid out over stock's (navigation.c ringnav_quick_settings): its buttons and labels
 * hidden but Wi-Fi's and Bluetooth's, at opacity 0 over their circles, the brightness slider at
 * opacity 0 over its track, and clear buttons over the rest. */
void cc_open(void *win) {
    static const char *const hidden[] = { "img_lock", "img_gain", "img_usbmode", "img_po",
                                          "img_playset", "img_sysset", "label_wifi", "label_bt",
                                          "label_lock", "label_gain", "label_usbmode",
                                          "label_outputway", "label_playset", "label_sysset",
                                          "image0", "image1" };
    void *c = widget_lookup(win, "view_menu", 1);
    if (c) widget_move_resize(c, 0, 0, 375, 320);
    for (unsigned i = 0; i < sizeof hidden / sizeof *hidden; ++i)
        if ((c = widget_lookup(win, hidden[i], 1))) widget_set_visible(c, 0, 0);
    for (int i = CC_WIFI; i <= CC_BT; ++i) {
        rect_t r = cc_box(i);
        if (!(c = cc_stock(win, i))) continue;
        widget_move_resize(c, r.x, r.y, r.w, r.h);
        widget_set_opacity(c, 0);
    }
    rect_t b = cc_box(CC_BRIGHT);
    if ((c = widget_lookup(win, "view_backlight", 1))) widget_move_resize(c, b.x, b.y, b.w, b.h);
    if ((c = cc_stock(win, CC_BRIGHT))) {
        widget_set_self_layout_params(c, "0", "0", "100%", "100%");
        widget_move_resize(c, 0, 0, b.w, b.h);
        widget_set_opacity(c, 0);
    }
    for (int i = CC_AIRPLAY; i < CC_BUTTONS; ++i) {
        if (i == CC_BRIGHT) continue;
        rect_t r = cc_box(i);
        void *button = button_create(win, r.x, r.y, r.w, r.h);
        widget_set_opacity(button, 0);
        if (i == CC_VOLUME) {
            widget_on(button, EVT_POINTER_DOWN, cc_drag, button);
            widget_on(button, EVT_POINTER_MOVE_BEFORE, cc_drag, button);
        } else
            widget_on(button, EVT_CLICK, cc_click, (void *)(long)i);
    }
}

/* The wheel on the page: from stop to stop, ends kept, or a slider's level while it has it. */
void cc_wheel(void *win, int dir) {
    int focus = widget_get_prop_int(win, CC_FOCUS, 0);
    if (widget_get_prop_int(win, CC_ADJUST, 0)) {
        if (focus == CC_VOLUME) {
            cc_volume(g_volume + dir);
        } else {
            void *s = cc_stock(win, CC_BRIGHT);
            if (!s) return;
            int value = widget_get_prop_int(s, "value", 0), min = widget_get_prop_int(s, "min", 0),
                max = widget_get_prop_int(s, "max", 100), next = min + clamp_step(value - min, max - min, dir);
            if (next != value) widget_set_prop_int(s, "value", next);
        }
    } else {
        int next = focus + dir;
        if (next < 0 || next >= CC_STOPS) return;
        widget_set_prop_int(win, CC_FOCUS, next);
    }
    widget_invalidate_force(win, (void *)0);
}

/* Centre and Return on the page: Centre acts on the stop, or gives a slider the wheel and takes it
 * back; Return takes it back from a slider. 0 leaves the key to close the page. */
int cc_key(void *win, unsigned key) {
    int focus = widget_get_prop_int(win, CC_FOCUS, 0), adjust = widget_get_prop_int(win, CC_ADJUST, 0);
    if (key == KEY_RETURN && !adjust) return 0;
    if (key == KEY_RETURN || focus == CC_BRIGHT || focus == CC_VOLUME)
        widget_set_prop_int(win, CC_ADJUST, !adjust);
    else if (cc_act(win, focus))
        return 1;
    widget_invalidate_force(win, (void *)0);
    return 1;
}
