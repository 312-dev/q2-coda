/* Control Center (docs/oled-design.md#control-center, approved as V3a on 2026-11-15 from
 * design/mockups/mock_cc3.py cc3(True)): stock's pull-down (statusbar_dialog, Quick Settings)
 * drawn as iOS's Control Center under the status row. A block of four round toggles, Wi-Fi,
 * Bluetooth, AirPlay and Car Remote; beside it Now Playing, the song's cover, title and artist over
 * Previous, Play/Pause and Next; under both a Brightness slider and a Volume slider; then five
 * square tiles, Buttons Lock, Sleep Timer, Night Shift, EQ and Smart Wi-Fi, each named under it;
 * and a grab bar at the foot, where the swipe up that closes it starts.
 *
 * Stock's widgets stay for what stock does: its Wi-Fi and Bluetooth buttons, at opacity 0 over
 * their circles, take the taps and holds (navigation.c quick_down: held, their settings pages), and
 * its brightness slider, at opacity 0 over the Brightness slider, takes the drags; Centre on those
 * clicks them as a tap would. Every other control is a clear button of this file's (cc_click).
 * The wheel walks CC_STOPS, the toggles, the Now Playing block (while something is loaded), the
 * two sliders and the tiles in reading order, a white ring round the one it is on; Centre acts on
 * it, and on a slider gives it the wheel (an accent ring) until Centre or Return gives it back.
 * Return on a stop closes the page. The ring is not there when the page is pulled down, nor under
 * touch: the first wheel tick or Centre brings it up on the stop the wheel is on, without stepping
 * or acting, and it goes CC_RING_MS after the last (cc_ring_on). The sliders step at the wheel's
 * speed and their fills ease, as Now Playing's volume does (levels.c). A tap on the Now Playing
 * block, or Centre on it, opens Now Playing; its transport keeps its own taps. A toggle or tile
 * held QUICK_HOLD_MS, by a finger (navigation.c quick_hold) or by Centre (cc_keydown), opens its
 * settings page (cc_hold). Landing open, the blocks overshoot and settle (cc_spring).
 * ponytail: AirPlay and Sleep Timer open their settings pages (stock's receiver switch and timer
 * have no call this file can make without their page), so their circle and tile are lit only by
 * AirPlay's flag and never by a running timer; the Wi-Fi circle reads "Wi-Fi", not the network's
 * name, which only the Wi-Fi page reads. */
#include "navigation.h"

extern unsigned now_cover_written(const char *file); /* coverflow.c */

extern int ui_text(void *canvas, const char *s, int medium, int px, unsigned rgb, int raw, int x,
                   int y, int w, int align);
extern void ar_corners(void *canvas, const rect_t *r, int R, unsigned behind); /* artist_page.c */
extern void keylock_toggle(void), coda_rc_toggle(void); /* navigation.c, coda_rc_shim.c */
extern int xfade_open(void *ctx, void *event);          /* crossfade.c */

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
#define CC_LABEL_PX TYPE(13) /* Inter Medium 13 px */
#define CC_ART 52
#define CC_ART_R 9
#define CC_SLIDER_Y (CC_Y0 + CC_BLOCK_H + CC_GAP)
#define CC_SLIDER_H 38
#define CC_TILE 44
#define CC_TILE_R 12
#define CC_TILE_Y (CC_SLIDER_Y + CC_SLIDER_H + 30)
#define CC_GRAB_Y 304
#define CC_RING 3 /* the focus ring's outer edge past the control, 2 px wide */
#define CC_BLOCK RGB_RAISED
#define CC_OFF RGB_OFF
#define CC_LIGHT 0xe5e5ea
#define CC_GRAB 0x5a5a5e
#define CC_FOCUS "_cc_focus"   /* the window's stop the wheel is on */
#define CC_ADJUST "_cc_adjust" /* 1 while a slider has the wheel */
#define CC_SEEN "_cc_seen"     /* the last wheel tick or key, time_now_ms + 1; 0 none yet */
#define CC_RING_MS 5000        /* the ring stays this long after it */
#define CC_LANDED "_cc_landed" /* when the page came to rest open, time_now_ms + 1; 0 not yet */
#define CC_SPRING_MS 280       /* the landing's overshoot and settle (cc_spring) */
#define CC_SPRING_STEP 20      /* ms between cc_springs' samples */

enum {
    CC_WIFI,
    CC_BT,
    CC_AIRPLAY,
    CC_CAR,
    CC_NOW,
    CC_BRIGHT,
    CC_VOLUME,
    CC_LOCK,
    CC_SLEEP,
    CC_NIGHT,
    CC_EQ,
    CC_SMART,
    CC_STOPS,
    CC_PREV = CC_STOPS,
    CC_PLAY,
    CC_NEXT,
    CC_BUTTONS
};
static const char *const cc_labels[CC_STOPS] = {
    "Wi-Fi", "Bluetooth", "AirPlay", "Car",         0,    0,
    0,       "Lock",      "Sleep",   "Night Shift", "EQ", "Smart Wi-Fi"
};
/* Each stop's glyph (tools/art.py CC_ICONS): <art>cc_<name> */
static const char *const cc_glyphs[CC_BUTTONS] = { "wifi",  "bluetooth", "airplay", "car",
                                                   0,       "sun",       "speaker", "lock",
                                                   "timer", "moon",      "eq",      "leaf",
                                                   "back",  "pause",     "forward" };
/* The landing's offset of the blocks from rest, in quarter pixels every CC_SPRING_STEP ms from
 * the moment the page comes to rest: a damped sine, 8 px past rest at about 50 ms, back through it
 * at 140, 2 px short at about 190 and still at CC_SPRING_MS (amplitude 14.6 px, decay ln 4 per
 * 140 ms, period 280 ms): a sheet settling as iOS's does, ease-out, not wobbly. */
static const signed char cc_springs[CC_SPRING_MS / CC_SPRING_STEP + 1] = { 0,  21, 31, 31, 26,
                                                                           17, 8,  0,  -5, -8,
                                                                           -8, -6, -4, -2, 0 };

/* Stop i's box on the window: a circle's square, the Now Playing block, a slider's track, a
 * tile, a transport glyph's. */
static rect_t cc_box(int i) {
    if (i == CC_NOW)
        return (rect_t){ CC_X0 + CC_BLOCK_W + CC_GAP, CC_Y0, CC_X1 - CC_X0 - CC_BLOCK_W - CC_GAP,
                         CC_BLOCK_H };
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
    case CC_WIFI:
        return g_wififlag != 0;
    case CC_BT:
        return g_bluetoothflag != 0;
    case CC_AIRPLAY:
        return g_airplayflag != 0;
    case CC_CAR:
        return coda_rc_enabled();
    case CC_LOCK:
        return g_keylock_flag != 0;
    case CC_NIGHT:
        return night_on() != 0;
    case CC_EQ:
        return g_equalizer_flag != 0;
    case CC_SMART:
        return st.smart_wifi != 0;
    default:
        return 0;
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
    if (i == CC_PLAY && mclGetPlayStatus() != 2)
        tk_snprintf(name, sizeof name, "%scc_play", theme()->art);
    paint_art(w, canvas, name, cx - px / 2, cy - px / 2);
}

/* A slider's level as its fill draws it, 0 to 256: Brightness's stock slider, Volume's g_volume
 * of 100, each at its bar's eased level (levels.c). */
static int cc_level(void *win, int i) {
    if (i == CC_VOLUME) return clamp_step(0, 256, level_shown(&vol_bar, g_volume) / 100);
    void *s = widget_lookup(win, "slider_backlight", 1);
    int min = s ? widget_get_prop_int(s, "min", 0) : 0,
        max = s ? widget_get_prop_int(s, "max", 100) : 100;
    return s && max > min
               ? clamp_step(
                     0, 256,
                     (level_shown(&bright_bar, widget_get_prop_int(s, "value", 0)) - (min << 8)) /
                         (max - min))
               : 0;
}
/* Something loaded, as Home's Now Playing row is there for: playing or paused. */
static int cc_np_loaded(void) {
    int status = media_status();
    return status == 2 || status == 3;
}

/* Whether the wheel's ring shows: a wheel tick or key within CC_RING_MS. */
static int cc_ring_on(void *win) {
    unsigned seen = (unsigned)widget_get_prop_int(win, CC_SEEN, 0);
    return seen && (unsigned)time_now_ms() - (seen - 1) < CC_RING_MS;
}
/* CC_RING_MS after the last input, the page drawn again without the ring; an input since moved
 * the end on, so the timer is added again for what is left. */
static int cc_ring_timer(const void *info) {
    void *win = P(info, TIMER_CTX);
    unsigned seen = (unsigned)widget_get_prop_int(win, CC_SEEN, 0),
             left = CC_RING_MS - ((unsigned)time_now_ms() - (seen - 1));
    if (cc_ring_on(win))
        widget_add_timer(win, cc_ring_timer, left);
    else
        widget_invalidate_force(win, (void *)0);
    return 7; /* RET_REMOVE */
}
/* A wheel tick or key on the page: 1 when the ring was not showing, which the input only brings
 * up. */
static int cc_ring_wake(void *win) {
    int was = cc_ring_on(win);
    if (!was) widget_add_timer(win, cc_ring_timer, CC_RING_MS);
    widget_set_prop_int(win, CC_SEEN, (int)((unsigned)time_now_ms() + 1));
    widget_invalidate_force(win, (void *)0);
    return !was;
}

/* The blocks' offset from rest, in pixels down, CC_SPRING_MS from the landing (cc_springs). A
 * spring is MO_SPATIAL (ui_kit.h): under Reduce Motion the blocks land still. */
static int cc_spring(void *win) {
    unsigned at = (unsigned)widget_get_prop_int(win, CC_LANDED, 0),
             t = (unsigned)time_now_ms() - (at - 1);
    if (!at || t >= (unsigned)motion_ms(CC_SPRING_MS, MO_SPATIAL)) return 0;
    unsigned k = t / CC_SPRING_STEP, f = t % CC_SPRING_STEP;
    int q =
        (cc_springs[k] * (int)(CC_SPRING_STEP - f) + cc_springs[k + 1] * (int)f) / CC_SPRING_STEP;
    return (q + (q < 0 ? -2 : 2)) / 4;
}
/* Every VOL_POLL_MS from the page's open: once it is at rest open (g_dropdown_menu_state 3, stock's
 * slide timer gone), however it got there, the landing starts, and the blocks under the status row
 * are drawn again each pass until CC_SPRING_MS has gone by. */
static int cc_land(const void *info) {
    void *win = P(info, TIMER_CTX);
    unsigned now = (unsigned)time_now_ms(), at = (unsigned)widget_get_prop_int(win, CC_LANDED, 0);
    if (!at) {
        if (I(g_dropdown_menu_state, 0) != 3 || widget_get_prop_int(win, "timer_idex", -1) != -1)
            return 8; /* RET_REPEAT: not down yet */
        widget_set_prop_int(win, CC_LANDED, (int)(at = now + 1));
    }
    rect_t r = { 0, CC_Y0 - CC_RING - 2, I(win, W_W), I(win, W_H) - (CC_Y0 - CC_RING - 2) };
    widget_invalidate_force(win, &r); /* the blocks' band, 2 px short of rest to the foot */
    return now - (at - 1) < CC_SPRING_MS ? 8 : 7; /* RET_REPEAT, RET_REMOVE */
}

/* The page, under the window's children (navigation.c's paint of statusbar_dialog): the status row
 * the dialog covers first, its clock centred, then the blocks, whose top reaches up into it, moved
 * down by the landing's spring (cc_spring). */
void cc_paint(void *win, void *canvas) {
    void *bar = *(void *const *)system_bar;
    unsigned fill = (unsigned)I(P(canvas, CANVAS_LCD), LCD_FILL_COLOR), accent = scheme()->tones[0];
    int focus = widget_get_prop_int(win, CC_FOCUS, 0),
        adjust = widget_get_prop_int(win, CC_ADJUST, 0), shown = cc_ring_on(win),
        dy = cc_spring(win);
    rect_t block = { CC_X0, CC_Y0, CC_BLOCK_W, CC_BLOCK_H }, np = cc_box(CC_NOW);
    if (bar) widget_paint(bar, canvas);
    I(canvas, CANVAS_Y) += dy;
    fill_box(canvas, &block, RGBA(CC_BLOCK), CC_BLOCK_R);
    cc_shape(canvas, np, CC_BLOCK_R, CC_BLOCK, focus == CC_NOW && shown, 0);
    for (int i = 0; i < CC_STOPS; ++i) {
        rect_t r = cc_box(i);
        int lit = cc_lit(i),
            ring = i == focus && shown ? 1 + (adjust && (i == CC_BRIGHT || i == CC_VOLUME)) : 0;
        if (i == CC_NOW) continue; /* drawn under the rest, above */
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
        ui_text(canvas, cc_labels[i], 1, CC_LABEL_PX, lit ? 0xffffff : RGB_SECONDARY, 0,
                r.x + r.w / 2, r.y + r.h + 9, 120, 1);
    }
    /* Now Playing: the cover, the title and artist, and the transport */
    unsigned pos, n;
    void *rec = queue_now(&pos, &n);
    rect_t art = { np.x + 12, CC_Y0 + 12, CC_ART, CC_ART };
    /* the song's cover as Home's pane finds it (coverflow.c song_cover_url), else the grey square.
     * The player writes every song's cover to one file: when the song or that file changes, the
     * image cache's bitmap of the last is dropped first, as Now Playing's np_art_poll does. */
    static unsigned art_key __attribute__((section(".scratch")));
    char url[600];
    unsigned bitmap[64]; /* bitmap_t: width @0, height @4 */
    int found = song_cover_url(rec, url, sizeof url);
    unsigned key = found ? fnv(fnv(FNV_SEED, (const unsigned char *)url),
                               (const unsigned char *)P(rec, REC_PATH)) ^
                               now_cover_written(url)
                         : 0;
    if (key != art_key && found) image_manager_unload_bitmap_by_name(image_manager(), url);
    art_key = key;
    if (found && !widget_load_image(win, url, bitmap) && bitmap[0] && bitmap[1]) {
        int bw = (int)bitmap[0], bh = (int)bitmap[1], side = bw < bh ? bw : bh;
        rect_t src = { (bw - side) / 2, (bh - side) / 2, side, side };
        canvas_draw_image(canvas, bitmap, &src, &art);
        ar_corners(canvas, &art, CC_ART_R, CC_BLOCK);
    } else
        fill_box(canvas, &art, RGBA(CC_OFF), CC_ART_R);
    int tx = art.x + CC_ART + 8, tw = np.x + np.w - 12 - tx;
    ui_text(canvas, rec ? now_tag(rec, REC_TITLE) : "Not Playing", 1, 19, 0xffffff, 0, tx,
            CC_Y0 + 28, tw, 2);
    if (rec)
        ui_text(canvas, now_tag(rec, REC_ARTIST), 0, 16, RGB_SECONDARY, 0, tx, CC_Y0 + 48, tw, 2);
    for (int i = CC_PREV; i <= CC_NEXT; ++i) {
        rect_t r = cc_box(i);
        cc_glyph(win, canvas, i, r.x + r.w / 2, r.y + r.h / 2, i == CC_PLAY ? 30 : CC_ICON);
    }
    rect_t grab = { 375 / 2 - 18, CC_GRAB_Y, 36, 5 };
    fill_box(canvas, &grab, RGBA(CC_GRAB), 3);
    I(canvas, CANVAS_Y) -= dy;
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
    return widget_lookup(win,
                         i == CC_WIFI ? "img_wifiswitch"
                         : i == CC_BT ? "img_btswitch"
                                      : "slider_backlight",
                         1);
}

/* Whether stop i has a settings page, which a hold on it opens (cc_hold): the toggles and tiles. */
static int cc_paged(int i) { return i <= CC_CAR || (i >= CC_LOCK && i < CC_STOPS); }
/* Stop i's settings page, opened by a hold on it once the page is closed: by a finger (navigation.c
 * quick_long) or Centre (cc_hold_due). Smart Wi-Fi's is Wi-Fi's. */
void cc_hold(int i) {
    switch (i) {
    case CC_WIFI:
    case CC_SMART:
        navigator_to("systemset/wifi_page");
        break;
    case CC_BT:
        navigator_to("systemset/bluetooth_page");
        break;
    case CC_AIRPLAY:
        navigator_to("systemset/netservice_page");
        break;
    case CC_CAR:
        carremote_open((void *)0, (void *)0);
        break;
    case CC_LOCK:
        navigator_to("systemset/keylock_page");
        break;
    case CC_SLEEP:
        navigator_to("systemset/sleepshutdown_page");
        break;
    case CC_NIGHT:
        xfade_open((void *)3, (void *)0);
        break;  /* Display's Night Shift row's page */
    case CC_EQ: /* an open EQ page is already the goal: a second would share peq_ui.c's one state */
        if (!navigator_window_is_exist("equalizer_page")) navigator_to("playset/equalizer_page");
        break;
    }
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
        navigator_to(i == CC_AIRPLAY ? "systemset/netservice_page"
                                     : "systemset/sleepshutdown_page");
        return 1;
    case CC_CAR:
        coda_rc_toggle();
        break;
    case CC_LOCK:
        keylock_toggle();
        break;
    case CC_NIGHT:
        night_set(night_on() ? 0 : 1, night_kelvin());
        night_save();
        break;
    case CC_EQ:
        eq_toggle();
        break;
    case CC_SMART: /* Battery's row's setting (navigation.c pod_click, smart_wifi.c) */
        st.smart_wifi = !st.smart_wifi;
        write_int_config(st.smart_wifi, "Q2POD", "SMARTWIFI");
        break;
    case CC_NOW: /* as Home's Now Playing row: only while something is loaded */
        if (!cc_np_loaded()) break;
        dialog_quit(win, 0);
        playback_np_open();
        return 1;
    case CC_PREV:
        prev_quick_click();
        break;
    case CC_PLAY:
        player_play_pause();
        break;
    case CC_NEXT:
        next_quick_click();
        break;
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
    static const char *const hidden[] = { "img_lock",      "img_gain",      "img_usbmode",
                                          "img_po",        "img_playset",   "img_sysset",
                                          "label_wifi",    "label_bt",      "label_lock",
                                          "label_gain",    "label_usbmode", "label_outputway",
                                          "label_playset", "label_sysset",  "image0",
                                          "image1" };
    void *c = widget_lookup(win, "view_menu", 1);
    if (c) widget_move_resize(c, 0, 0, 375, 320);
    for (unsigned i = 0; i < sizeof hidden / sizeof *hidden; ++i)
        if ((c = widget_lookup(win, hidden[i], 1))) widget_set_visible(c, 0, 0);
    for (int i = CC_WIFI; i <= CC_BT; ++i) {
        rect_t r = cc_box(i);
        if (!(c = cc_stock(win, i))) continue;
        widget_move_resize(c, r.x, r.y, r.w, r.h);
        widget_set_opacity(c, 0); /* held: their settings pages (navigation.c quick_long) */
    }
    rect_t b = cc_box(CC_BRIGHT);
    if ((c = widget_lookup(win, "view_backlight", 1))) widget_move_resize(c, b.x, b.y, b.w, b.h);
    if ((c = cc_stock(win, CC_BRIGHT))) {
        widget_set_self_layout_params(c, "0", "0", "100%", "100%");
        widget_move_resize(c, 0, 0, b.w, b.h);
        widget_set_opacity(c, 0);
    }
    /* in order, so the transport's buttons lie over the Now Playing block's and take its taps */
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
        if (cc_paged(i)) quick_hold(button, QUICK_CC + i);
    }
    vol_bar.easing = bright_bar.easing = 0; /* the sliders open on their levels */
    widget_add_timer(win, cc_land, VOL_POLL_MS);
}

/* The wheel on the page: from stop to stop, ends kept, past the Now Playing block while nothing is
 * loaded; or a slider's level while it has it, Volume's as Now Playing's wheel sets it (vol_next)
 * and Brightness's as the Brightness page's (bright_wheel). A tick with no ring showing only
 * brings it up. */
void cc_wheel(void *win, int dir) {
    if (cc_ring_wake(win)) return;
    int focus = widget_get_prop_int(win, CC_FOCUS, 0);
    if (widget_get_prop_int(win, CC_ADJUST, 0)) {
        if (focus == CC_VOLUME) {
            cc_volume(vol_next(dir > 0 ? KEY_NEXT : KEY_PREV));
            level_follow(win);
        } else {
            void *s = cc_stock(win, CC_BRIGHT);
            if (s) bright_wheel(s, dir);
        }
    } else {
        int next = focus + dir;
        if (next == CC_NOW && !cc_np_loaded()) next += dir;
        if (next < 0 || next >= CC_STOPS) return;
        widget_set_prop_int(win, CC_FOCUS, next);
    }
    widget_invalidate_force(win, (void *)0);
}

/* Centre held QUICK_HOLD_MS (cc_keydown): the page closed and the stop's own opened; the release is
 * then cc_key_release's. */
static struct {
    unsigned hold;
    unsigned char held;
} cch __attribute__((section(".scratch")));
static void *cc_top(void) {
    void *top = window_manager_get_top_window(window_manager());
    return top && !tk_strcmp(widget_get_prop_str(top, "name", ""), "statusbar_dialog") ? top
                                                                                       : (void *)0;
}
static int cc_hold_due(const void *unused) {
    cch.hold = 0;
    void *win = cc_top();
    int focus = win ? widget_get_prop_int(win, CC_FOCUS, 0) : -1;
    if (!win || !cc_paged(focus)) return 0;
    cch.held = 1;
    dialog_quit(win, 0);
    cc_hold(focus);
    return 0; /* RET_OK: removed */
}
/* navigation.c's key-down: Centre pressed with the ring on a toggle or tile starts its hold; its
 * repeats while held leave it running. */
void cc_keydown(unsigned key) {
    void *win = key == KEY_CENTER && !cch.hold && !cch.held ? cc_top() : (void *)0;
    if (win && cc_ring_on(win) && !widget_get_prop_int(win, CC_ADJUST, 0) &&
        cc_paged(widget_get_prop_int(win, CC_FOCUS, 0)))
        cch.hold = timer_add(cc_hold_due, (void *)0, QUICK_HOLD_MS);
}
/* navigation.c's key filter, a key's release: 1 for the release of a Centre whose hold opened a
 * page, which then does nothing more; a shorter one's hold is dropped and its release is cc_key's.
 */
int cc_key_release(unsigned key) {
    if (key != KEY_CENTER) return 0;
    stop_timer(&cch.hold);
    if (!cch.held) return 0;
    cch.held = 0;
    return 1;
}

/* Centre and Return on the page: Centre acts on the stop, or gives a slider the wheel and takes it
 * back; Return takes it back from a slider. 0 leaves the key to close the page. */
int cc_key(void *win, unsigned key) {
    int focus = widget_get_prop_int(win, CC_FOCUS, 0),
        adjust = widget_get_prop_int(win, CC_ADJUST, 0);
    if (key == KEY_RETURN && !adjust) return 0;
    if (cc_ring_wake(win) && key == KEY_CENTER) return 1; /* no ring: Centre only brings it up */
    if (key == KEY_RETURN || focus == CC_BRIGHT || focus == CC_VOLUME)
        widget_set_prop_int(win, CC_ADJUST, !adjust);
    else if (cc_act(win, focus))
        return 1;
    widget_invalidate_force(win, (void *)0);
    return 1;
}
