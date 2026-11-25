/* Control Center (docs/oled-design.md#control-center, v4 "A, tightened", approved on 2026-11-16
 * from design/control-center-v4 mock_cc4_tight.py): stock's pull-down (statusbar_dialog, Quick
 * Settings) drawn as iOS's Control Center under the status row, in two columns. On the left a
 * block of six round toggles, two by three, each named under it: Wi-Fi (its glyph the link's,
 * cc_wifi), Bluetooth, AirPlay, Car Remote, Smart Wi-Fi and EQ. On the right Now Playing, the
 * song's cover beside its title and artist over Previous, Play/Pause and Next; under it a
 * Brightness slider and under that a Volume slider, ending level with the block; while a cable is
 * in, the USB row under both, the cluster's width, with the remembered choice (usb_ui.c
 * usb_ui_value), whose Centre opens the USB picker; and a grab bar along the sheet's foot, where the
 * swipe up that closes it starts. Buttons Lock keeps its hardware hold,
 * Night Shift and Sleep Timer their Settings rows, Guest Play its Bluetooth row.
 *
 * Stock's widgets stay for what stock does: its Wi-Fi and Bluetooth switches, at opacity 0 under
 * their circles, are clicked by a tap or Centre there (cc_act), and its brightness slider, at
 * opacity 0 over the Brightness slider, takes the drags. Every circle, the Now Playing block, its
 * transport and the Volume slider are clear buttons of this file's (cc_click, cc_drag).
 * The wheel walks CC_STOPS, the toggles by rows, the Now Playing block (while something is
 * loaded), the two sliders and the USB row (while a cable is in), a white ring round the one it is on; Centre acts on it, and on a
 * slider gives it the wheel (an accent ring) until Centre or Return gives it back.
 * Return on a stop closes the page. The ring is not there when the page is pulled down, nor under
 * touch: the first wheel tick or Centre brings it up on the stop the wheel is on, without stepping
 * or acting, and it goes CC_RING_MS after the last (cc_ring_on). The sliders step at the wheel's
 * speed and their fills ease, as Now Playing's volume does (levels.c). A tap on the Now Playing
 * block, or Centre on it, opens Now Playing; its transport keeps its own taps. A toggle held
 * QUICK_HOLD_MS, by a finger (navigation.c quick_hold) or by Centre (cc_keydown), opens its
 * settings page (cc_hold). Landing open, the blocks overshoot and settle (cc_spring). A toggle's
 * name is set glyph by glyph (ui_set) and, wider than its column, fades out at its edge (cc_label).
 * Everything is drawn inside the window, whose whole is invalidated on an input but a Volume
 * drag's move (the slider's box alone, cc_drag, which sets the volume at most once every
 * CC_VOL_WRITE_MS), inside the band cc_land invalidates while the blocks spring, the Wi-Fi circle
 * inside the box cc_wifi_poll invalidates when the link changes, and the whole when a cable comes
 * or goes (cc_wifi_poll too).
 * ponytail: AirPlay opens its settings page (stock's receiver switch has no call this file can
 * make without its page), so its circle is lit only by AirPlay's flag; the Wi-Fi circle reads
 * "Wi-Fi", not the network's name, which only the Wi-Fi page reads. */
#include "navigation.h"

extern int ui_text(void *canvas, const char *s, int medium, int px, unsigned rgb, int raw, int x,
                   int y, int w, int align);
extern void ar_corners(void *canvas, const rect_t *r, int R, unsigned behind); /* artist_page.c */
extern void coda_rc_toggle(void);                                              /* coda_rc_shim.c */
extern unsigned now_cover_written(const char *file);                           /* coverflow.c */

/* The layout, in the window's (the screen's) pixels, as mock_cc4_tight.py draws it at 1x: one
 * spacing token, CC_GAP, between every two parts (the status row's glyphs and the blocks, the
 * block and the right column, Now Playing and the sliders, the sliders) and round the toggles
 * inside their block. The grab bar is not among them: it stands CC_GRAB_INSET off the sheet's
 * foot, the screen's, wherever the blocks end. */
#define CC_X0 20
#define CC_X1 355
#define CC_Y0 38       /* the status row's glyphs' foot (y 28) + CC_GAP */
#define CC_GAP 10      /* the one spacing token */
#define CC_PAD 10      /* the toggles' block's padding round its circles and names */
#define CC_PITCH 66    /* the circles, centre to centre, across and down: 26 px between them */
#define CC_BLOCK_W 158 /* the toggles' block: as wide as its widest pair of names needs */
#define CC_BLOCK_H 210 /* CC_PAD, three rows of a circle and its name, CC_PAD */
#define CC_BLOCK_R 22
#define CC_RIGHT_X (CC_X0 + CC_BLOCK_W + CC_GAP)
#define CC_RIGHT_W (CC_X1 - CC_RIGHT_X)
#define CC_NOW_H 114 /* CC_ART_IN, the cover, 8, the transport's 30 px row, CC_ART_IN */
#define CC_CIRCLE_R 20
#define CC_ICON 22 /* a circle's glyph's box; its ink is 18 px (tools/art.py CC_INK) */
/* The toggles' names: every number their geometry takes, here alone (cc_label, cc_open). A name's
 * room is centred on its circle and keeps 8 px from the block's edge: "Smart Wi-Fi" is 75 px at
 * the house 13 px (AWTK's 16, an em of 13.2), so CC_PAD's 10 would leave it 72 and fade it. */
#define CC_LABEL_W 76
#define CC_LABEL_PX TYPE(13) /* Inter Medium 13 px */
#define CC_LABEL_DY 29       /* a name's row under its circle's centre: its line 1 px under */
#define CC_LABEL_TRACK TRACK_CAPTION /* thousandths of an em between glyphs (ui_set) */
#define CC_ART 52
#define CC_ART_R 9
#define CC_ART_IN 12   /* the cover's inset in the Now Playing block */
#define CC_TEXT_GAP 10 /* between the cover and the title */
#define CC_TRANSPORT_Y                                                                             \
    87                     /* the transport's row's centre under the block's top: 12 + 52 + 8 + 15 \
                            */
#define CC_TRANSPORT_DX 46 /* Previous and Next either side of Play/Pause */
#define CC_SLIDER_Y (CC_Y0 + CC_NOW_H + CC_GAP)
#define CC_SLIDER_H 38
#define CC_H 320       /* the sheet's height: the page spans the screen, to its foot */
#define CC_GRAB_W 36   /* the grab bar, as the page sheets' (menu_modal.c SH_GRAB_Y) */
#define CC_GRAB_H 5
#define CC_GRAB_INSET 6 /* its gap to the sheet's edge, as the page sheets' from their top */
#define CC_GRAB_Y (CC_H - CC_GRAB_INSET - CC_GRAB_H)
#define CC_WIFI_SEEN "_cc_wifi" /* the Wi-Fi circle's glyph as last drawn (cc_wifi), + 2 */
#define CC_CABLE_SEEN "_cc_cable" /* a cable in as last drawn (the USB row), + 1 */
/* The USB row (design/usb-row mock_usb_row.py cc()): CC_GAP under the cluster, its width, the sliders'
 * height and form; the glyph centred CC_USB_ICON_X in, "USB" from CC_USB_TEXT_X, the value ending
 * CC_USB_VALUE_END from its end and the chevron centred CC_USB_CHEV_X from it; its ring 2 px wide
 * CC_USB_RING out. */
#define CC_USB_Y (CC_Y0 + CC_BLOCK_H + CC_GAP)
#define CC_USB_ICON 20
#define CC_USB_ICON_X 22
#define CC_USB_TEXT_X 40
#define CC_USB_VALUE_END 30
#define CC_USB_CHEV 13
#define CC_USB_CHEV_X 18
#define CC_USB_PX TYPE(15)
#define CC_USB_RING 5
#define CC_WIFI_POLL_MS 1000    /* stock updates the link about once a second */
#define CC_RING 3               /* the focus ring's outer edge past the control, 2 px wide */
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

/* The stops in the wheel's order: the six toggles by rows, then the right column top down */
enum {
    CC_WIFI,
    CC_BT,
    CC_AIRPLAY,
    CC_CAR,
    CC_SMART,
    CC_EQ,
    CC_NOW,
    CC_BRIGHT,
    CC_VOLUME,
    CC_USB,
    CC_STOPS,
    CC_PREV = CC_STOPS,
    CC_PLAY,
    CC_NEXT,
    CC_BUTTONS
};
static const char *const cc_labels[CC_STOPS] = {
    "Wi-Fi", "Bluetooth", "AirPlay", "Car", "Smart Wi-Fi", "EQ", 0, 0, 0, 0
};
/* Each stop's glyph (tools/art.py CC_ICONS): <art>cc_<name>; Wi-Fi's is the link's (cc_wifi) */
static const char *const cc_glyphs[CC_BUTTONS] = { 0,         "bluetooth", "airplay", "car",
                                                   "leaf",    "eq",        0,         "sun",
                                                   "speaker", "usb",       "back",    "pause",
                                                   "forward" };
/* The landing's offset of the blocks from rest, in quarter pixels every CC_SPRING_STEP ms from
 * the moment the page comes to rest: a damped sine, 8 px past rest at about 50 ms, back through it
 * at 140, 2 px short at about 190 and still at CC_SPRING_MS (amplitude 14.6 px, decay ln 4 per
 * 140 ms, period 280 ms): a sheet settling as iOS's does, ease-out, not wobbly. */
static const signed char cc_springs[CC_SPRING_MS / CC_SPRING_STEP + 1] = { 0,  21, 31, 31, 26,
                                                                           17, 8,  0,  -5, -8,
                                                                           -8, -6, -4, -2, 0 };

/* Stop i's box on the window: a circle's square, the Now Playing block, a slider's track, the USB
 * row, a transport glyph's. */
static rect_t cc_box(int i) {
    if (i <= CC_EQ) {
        int cx = CC_X0 + CC_BLOCK_W / 2 - CC_PITCH / 2 + (i % 2) * CC_PITCH,
            cy = CC_Y0 + CC_PAD + CC_CIRCLE_R + (i / 2) * CC_PITCH;
        return (rect_t){ cx - CC_CIRCLE_R, cy - CC_CIRCLE_R, 2 * CC_CIRCLE_R, 2 * CC_CIRCLE_R };
    }
    if (i == CC_NOW) return (rect_t){ CC_RIGHT_X, CC_Y0, CC_RIGHT_W, CC_NOW_H };
    if (i == CC_BRIGHT || i == CC_VOLUME)
        return (rect_t){ CC_RIGHT_X, CC_SLIDER_Y + (i - CC_BRIGHT) * (CC_SLIDER_H + CC_GAP),
                         CC_RIGHT_W, CC_SLIDER_H };
    if (i == CC_USB) return (rect_t){ CC_X0, CC_USB_Y, CC_X1 - CC_X0, CC_SLIDER_H };
    int cx = CC_RIGHT_X + CC_RIGHT_W / 2 + (i - CC_PLAY) * CC_TRANSPORT_DX,
        cy = CC_Y0 + CC_TRANSPORT_Y, s = i == CC_PLAY ? 30 : CC_ICON;
    return (rect_t){ cx - s / 2 - 6, cy - s / 2 - 6, s + 12, s + 12 }; /* a 6 px reach */
}

/* The Wi-Fi circle's glyph, by the link as the status row reads it (status_row.c wifi_level,
 * wifi_offline), so the two never disagree: 1 to 3 joined by stock's bars (1, 2, then 3 and 4:
 * wifi-low to wifi-high), 4 joined with no link up (wifi-x), else -1 off (wifi-slash) or 0 on but
 * not joined (wifi-none). */
static int cc_wifi(void) {
    int level = wifi_level();
    return level ? (wifi_offline() ? 4 : level > 3 ? 3 : level) : g_wififlag ? 0 : -1;
}
/* Every CC_WIFI_POLL_MS while the page is open: the Wi-Fi circle drawn again when its glyph
 * changes, its box, ring and all, nothing else moving with it; and the page when a cable comes or
 * goes, the USB row with it (the wheel back on Volume if it was on the row). */
static int cc_wifi_poll(const void *info) {
    void *win = P(info, TIMER_CTX);
    int now = cc_wifi() + 2, cable = usb_ui_cable() + 1;
    if (cable != widget_get_prop_int(win, CC_CABLE_SEEN, 0)) {
        widget_set_prop_int(win, CC_CABLE_SEEN, cable);
        if (cable == 1 && widget_get_prop_int(win, CC_FOCUS, 0) == CC_USB)
            widget_set_prop_int(win, CC_FOCUS, CC_VOLUME);
        widget_invalidate_force(win, (void *)0);
    }
    if (now != widget_get_prop_int(win, CC_WIFI_SEEN, 0)) {
        rect_t r = cc_box(CC_WIFI);
        rect_t o = { r.x - CC_RING, r.y - CC_RING, r.w + 2 * CC_RING, r.h + 2 * CC_RING };
        widget_set_prop_int(win, CC_WIFI_SEEN, now);
        widget_invalidate_force(win, &o);
    }
    return 8; /* RET_REPEAT */
}

static int cc_lit(int i) {
    switch (i) {
    case CC_WIFI: /* on, or joined as the status row reads it (cc_wifi) */
        return g_wififlag != 0 || wifi_level() != 0;
    case CC_BT:
        return g_bluetoothflag != 0;
    case CC_AIRPLAY:
        return g_airplayflag != 0;
    case CC_CAR:
        return coda_rc_enabled();
    case CC_EQ:
        return g_equalizer_flag != 0;
    case CC_SMART:
        return st.smart_wifi != 0;
    default:
        return 0;
    }
}

/* Toggle i's name's scroller (cc_open): a clear hscroll_label over the name's row, 2 px wider
 * each side than its column for stock's margins. */
static void *cc_name(void *win, int i) {
    char n[12];
    tk_snprintf(n, sizeof n, "cc_name%d", i);
    return widget_lookup(win, n, 0);
}

/* Toggle i's name, its row CC_LABEL_DY under the circle's centre cx, cy, in its column (CC_LABEL_W,
 * centred on the circle), set at the captions' tracking (ui_set). One that fits is centred. A
 * wider one starts at the column's left, clipped to it, never shrunk or ellipsized, and scrolls as
 * the house marquee while the wheel is on its circle (cc_marq): its scroller, which never paints,
 * holds its width and runs stock's timer, holds and pace (ringnav_hscroll_start, the lull), and its
 * xoffset is where this draws the text (ui_set_in). Its ends fade as a marquee's do
 * (list.marquee.fade, text_fade: the text itself blended onto the block): the right while text runs
 * past it, the left while it is scrolled off its start. Everything stays inside the scroller's box,
 * which is what stock's timer invalidates. */
static void cc_label(void *win, void *canvas, int i, unsigned rgb, int cx, int cy) {
    const char *s = cc_labels[i];
    void *lab = cc_name(win, i);
    int w = ui_set_w(canvas, s, 1, CC_LABEL_PX, CC_LABEL_TRACK);
    if (lab) I(lab, HSCROLL_TEXT_W) = w; /* its range: w less the column */
    ui_set_in(canvas, s, 1, CC_LABEL_PX, rgb, cx - CC_LABEL_W / 2, CC_LABEL_W, cy + CC_LABEL_DY,
              lab ? I(lab, HSCROLL_XOFFSET) : 0, CC_LABEL_TRACK);
}

/* The name of the toggle the wheel is on scrolls while its ring shows, if it is wider than its
 * column; every other name is still at its start. A scroller's HSCROLL_LOOP says it runs: its
 * pass ends on the lull, which holds the end, puts the start back and starts it again. */
static int cc_ring_on(void *win);
static void cc_marq(void *win) {
    int focus = widget_get_prop_int(win, CC_FOCUS, 0),
        on = cc_ring_on(win) && !widget_get_prop_int(win, CC_ADJUST, 0);
    for (int i = 0; i <= CC_EQ; ++i) {
        void *lab = cc_name(win, i);
        if (!lab) continue;
        int run = on && i == focus && I(lab, HSCROLL_TEXT_W) > CC_LABEL_W;
        if (run == (B(lab, HSCROLL_LOOP) != 0)) continue;
        B(lab, HSCROLL_LOOP) = (unsigned char)run;
        if (run)
            hscroll_label_start(lab);
        else {
            hscroll_label_stop(lab);
            hscroll_label_set_xoffset(lab, 0);
        }
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
    char name[40], stem[16];
    tk_snprintf(stem, sizeof stem, "%s",
                i == CC_PLAY && now_status() != 1 ? "play"
                : cc_glyphs[i]                          ? cc_glyphs[i]
                                                        : "");
    if (i == CC_WIFI) { /* the link's (cc_wifi), recorded for cc_wifi_poll */
        int k = cc_wifi();
        widget_set_prop_int(w, CC_WIFI_SEEN, k + 2);
        if (k < 0)
            tk_snprintf(stem, sizeof stem, "wifi_slash");
        else if (k == 4)
            tk_snprintf(stem, sizeof stem, "wifi_x");
        else
            tk_snprintf(stem, sizeof stem, "wifi_%d", k);
    }
    /* Light Mode's ink (tools/art.py CC_LIGHT), but on a lit circle, whose white stays */
    tk_snprintf(name, sizeof name, "%scc_%s%s", theme()->art, stem,
                ipod_light() && (i > CC_EQ || !cc_lit(i)) ? "_l" : "");
    paint_art(w, canvas, name, cx - px / 2, cy - px / 2);
}

/* The USB row in r, ringed as the mock rings it when the wheel is on it: the glyph, "USB", the
 * remembered choice in Text Secondary and the chevron. */
static void cc_usb_row(void *win, void *canvas, rect_t r, int ring) {
    char name[40];
    int cy = r.y + r.h / 2, x1 = r.x + r.w;
    if (ring) {
        rect_t o = { r.x - CC_USB_RING, r.y - CC_USB_RING, r.w + 2 * CC_USB_RING, r.h + 2 * CC_USB_RING },
               g = { o.x + 2, o.y + 2, o.w - 4, o.h - 4 };
        fill_box(canvas, &o, RGBA(0xffffff), (unsigned)(o.h / 2));
        fill_box(canvas, &g, RGBA(0), (unsigned)(g.h / 2));
    }
    fill_box(canvas, &r, RGBA(CC_BLOCK), (unsigned)(r.h / 2));
    cc_glyph(win, canvas, CC_USB, r.x + CC_USB_ICON_X, cy, CC_USB_ICON);
    ui_text(canvas, "USB", 1, CC_USB_PX, 0xffffff, 0, r.x + CC_USB_TEXT_X, cy, 120, 2);
    ui_text(canvas, usb_ui_value(), 0, CC_USB_PX, RGB_SECONDARY, 0, x1 - CC_USB_VALUE_END, cy, 200, 3);
    tk_snprintf(name, sizeof name, "%scc_chev%s", theme()->art, ipod_light() ? "_l" : "");
    paint_art(win, canvas, name, x1 - CC_USB_CHEV_X - CC_USB_CHEV / 2, cy - CC_USB_CHEV / 2);
}

/* A drag's level not yet set (cc_drag): want is it + 1, 0 none; at, when the drag last set one;
 * timer, 1 while cc_vol_due is to run. */
static struct {
    unsigned at;
    unsigned char want, timer;
} ccv __attribute__((section(".scratch")));

/* A slider's level as its fill draws it, 0 to 256: Brightness's stock slider, Volume's g_volume
 * of 100 (a drag's level while it waits to be set, so the fill keeps up with the finger), each at
 * its bar's eased level (levels.c). */
static int cc_level(void *win, int i) {
    if (i == CC_VOLUME)
        return clamp_step(0, 256, level_shown(&vol_bar, ccv.want ? ccv.want - 1 : g_volume) / 100);
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
/* Something loaded, as Home's Now Playing row is there for: playing or paused (now_source.c). */
static int cc_np_loaded(void) {
    int status = now_status();
    return status == 1 || status == 2;
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
    else {
        cc_marq(win); /* no ring: no name scrolls */
        widget_invalidate_force(win, (void *)0);
    }
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
 * the dialog covers first, its clock at the left, then the blocks, whose top reaches up into it,
 * moved down by the landing's spring (cc_spring). */
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
        if (i == CC_USB) { /* recorded for cc_wifi_poll */
            widget_set_prop_int(win, CC_CABLE_SEEN, usb_ui_cable() + 1);
            if (usb_ui_cable()) cc_usb_row(win, canvas, r, ring);
            continue;
        }
        if (i == CC_BRIGHT || i == CC_VOLUME) {
            cc_shape(canvas, r, r.h / 2, CC_OFF, ring, 0);
            int k = cc_level(win, i), fw = r.w * k / 256;
            rect_t f = { r.x, r.y, fw > r.h ? fw : r.h, r.h };
            fill_box(canvas, &f, RGBA(CC_LIGHT), (unsigned)(r.h / 2));
            cc_glyph(win, canvas, i, r.x + r.h / 2 + 2, r.y + r.h / 2, 18);
            continue;
        }
        cc_shape(canvas, r, CC_CIRCLE_R, lit ? accent : CC_OFF, ring, CC_BLOCK);
        cc_glyph(win, canvas, i, r.x + r.w / 2, r.y + r.h / 2, CC_ICON);
        cc_label(win, canvas, i, lit ? 0xffffff : RGB_SECONDARY, r.x + r.w / 2, r.y + r.h / 2);
    }
    /* Now Playing: the cover, the title and artist beside it, and the transport. The Library's
     * song as Now Playing shows it (now_song), or another owner's words and no cover (now_get) */
    unsigned pos, n;
    now_info now;
    int other = now_owner() != NOW_LIBRARY;
    if (other) now_get(&now);
    void *rec = other ? 0 : now_song(&pos, &n, 0);
    rect_t art = { np.x + CC_ART_IN, np.y + CC_ART_IN, CC_ART, CC_ART };
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
        ar_corners(canvas, &art, CC_ART_R, flip_if(ipod_light(), CC_BLOCK)); /* final colour */
    } else
        fill_box(canvas, &art, RGBA(CC_OFF), CC_ART_R);
    int tx = art.x + CC_ART + CC_TEXT_GAP, tw = np.x + np.w - CC_ART_IN - tx,
        mid = art.y + CC_ART / 2;
    ui_text(canvas, other ? now.title : rec ? now_tag(rec, REC_TITLE) : "Not Playing", 1, TYPE(16),
            0xffffff, 0, tx, mid - 10, tw, 2);
    if (rec || other)
        ui_text(canvas, other ? now.artist : now_tag(rec, REC_ARTIST), 0, TYPE(13), RGB_SECONDARY,
                0, tx, mid + 10, tw, 2);
    for (int i = CC_PREV; i <= CC_NEXT; ++i) {
        rect_t r = cc_box(i);
        cc_glyph(win, canvas, i, r.x + r.w / 2, r.y + r.h / 2, i == CC_PLAY ? 30 : CC_ICON);
    }
    rect_t grab = { 375 / 2 - CC_GRAB_W / 2, CC_GRAB_Y, CC_GRAB_W, CC_GRAB_H };
    fill_box(canvas, &grab, RGBA(CC_GRAB), 3);
    I(canvas, CANVAS_Y) -= dy;
    st.paint_w = win;
    stock_canvas_set_fill_color(canvas, fill);
}

/* Volume to v of 100, up to vol_cap (Max Volume and a Bluetooth device's Volume Limit), saved for
 * the output in use, as Car Remote sets it. */
static void cc_volume(int v) {
    int top = vol_cap();
    if (v < 0) v = 0;
    if (v > top) v = top;
    if (v == g_volume) return;
    g_volume = (unsigned char)v;
    device_set_volume(v, 1);
    write_int_config(v, "PLAYSET", mclGetOutputWay() == 1 ? "BT_VOLUME" : "VOLUME");
}

static void cc_vol_write(void) {
    if (!ccv.want) return;
    int v = ccv.want - 1;
    ccv.want = 0;
    ccv.at = (unsigned)time_now_ms();
    cc_volume(v);
}
static int cc_vol_due(const void *unused) {
    ccv.timer = 0;
    cc_vol_write();
    return 7; /* RET_REMOVE */
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

/* Whether stop i has a settings page, which a hold on it opens (cc_hold): the toggles. */
static int cc_paged(int i) { return i <= CC_EQ; }
/* A settings page Control Center (or stock's quick settings, nav/rows_settings.c quick_long)
 * opens, unless it is open already: then it is the goal, and a second would stack on it (the
 * emulator, 2026-11-17: held on Settings > Wi-Fi, a second wifi_page, and Return took two presses
 * to leave Wi-Fi), or share its one state (peq_ui.c's EQ). path is its folder and name; the
 * window's name is the part after the '/'. */
void cc_page(const char *path) {
    const char *name = path;
    for (const char *c = path; *c; ++c)
        if (*c == '/') name = c + 1;
    if (!navigator_window_is_exist(name)) navigator_to(path);
}

/* Stop i's settings page, opened by a hold on it once the page is closed: by a finger (navigation.c
 * quick_long) or Centre (cc_hold_due). Smart Wi-Fi's is Wi-Fi's. */
void cc_hold(int i) {
    switch (i) {
    case CC_WIFI:
    case CC_SMART:
        cc_page("systemset/wifi_page");
        break;
    case CC_BT:
        cc_page("systemset/bluetooth_page");
        break;
    case CC_AIRPLAY:
        cc_page("systemset/netservice_page");
        break;
    case CC_CAR: /* one page: carremote_open opens none over its own */
        carremote_open((void *)0, (void *)0);
        break;
    case CC_EQ:
        cc_page("playset/equalizer_page");
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
        if (i == CC_WIFI) wifi_flows_cancel_join(); /* stock's click ignores a join that is trying */
        if (b) stock_dispatch_trampoline(b, pointer_event_init(click, EVT_CLICK, b, 0, 0));
        break;
    }
    case CC_AIRPLAY:
        dialog_quit(win, 0);
        cc_page("systemset/netservice_page");
        return 1;
    case CC_CAR:
        coda_rc_toggle();
        break;
    case CC_EQ:
        eq_toggle();
        break;
    case CC_SMART: /* Battery's row's setting (navigation.c pod_click, smart_wifi.c) */
        st.smart_wifi = !st.smart_wifi;
        write_int_config(st.smart_wifi, "Q2POD", "SMARTWIFI");
        break;
    case CC_USB: /* the USB picker, as Settings > General > USB opens it; only with a cable in */
        if (!usb_ui_cable()) break;
        dialog_quit(win, 0);
        usb_pick_open((void *)0, (void *)0);
        return 1;
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
/* A finger on the Volume slider sets it where it is, from its press through its drag, and its
 * release sets where it stopped (docs/internals.md#control-center-volume-drag). The fill follows
 * every move and only the slider's box (and its ring) is drawn again, as stock's Brightness slider
 * draws its own; the volume itself is set at most once every CC_VOL_WRITE_MS, a move sooner than
 * that held for cc_vol_due, which sets the latest. */
static int cc_drag(void *track, void *event) {
    if (I(event, EVENT_TYPE) == EVT_POINTER_UP_BEFORE) {
        cc_vol_write();
        return 0;
    }
    int x = I(event, EVENT_X), w = I(track, W_W), at = x;
    for (void *p = track; p; p = P(p, W_PARENT)) at -= I(p, W_X);
    if (w > 0 && pointer_down()) {
        int v = (at < 0 ? 0 : at > w ? w : at) * 100 / w;
        int top = vol_cap();
        ccv.want = (unsigned char)((v > top ? top : v) + 1);
        unsigned since = (unsigned)time_now_ms() - ccv.at;
        if (since >= CC_VOL_WRITE_MS)
            cc_vol_write();
        else if (!ccv.timer)
            ccv.timer = timer_add(cc_vol_due, (void *)0, CC_VOL_WRITE_MS - since) != 0;
        rect_t r = cc_box(CC_VOLUME);
        r.x -= CC_RING, r.y -= CC_RING, r.w += 2 * CC_RING, r.h += 2 * CC_RING;
        widget_invalidate_force(window_of(track), &r);
    }
    return 0;
}

/* The page laid out over stock's (navigation.c ringnav_quick_settings): its buttons and labels
 * hidden but Wi-Fi's and Bluetooth's, at opacity 0 over their circles, the brightness slider at
 * opacity 0 over its track, clear buttons over the rest, and the toggles' names' scrollers. */
void cc_open(void *win) {
    static const char *const hidden[] = { "img_lock",      "img_gain",      "img_usbmode",
                                          "img_po",        "img_playset",   "img_sysset",
                                          "label_wifi",    "label_bt",      "label_lock",
                                          "label_gain",    "label_usbmode", "label_outputway",
                                          "label_playset", "label_sysset",  "image0",
                                          "image1" };
    void *c = widget_lookup(win, "view_menu", 1);
    if (c) widget_move_resize(c, 0, 0, 375, CC_H);
    /* the names' scrollers (cc_label), under the buttons made below, so no tap lands on one */
    for (int i = 0; i <= CC_EQ; ++i) {
        rect_t r = cc_box(i);
        int x0 = r.x + r.w / 2 - CC_LABEL_W / 2, y = r.y + r.h / 2 + CC_LABEL_DY;
        void *name =
            hscroll_label_create(win, x0 - 2, y - CC_LABEL_PX / 2, CC_LABEL_W + 4, CC_LABEL_PX);
        if (!name) continue;
        char n[12];
        tk_snprintf(n, sizeof n, "cc_name%d", i);
        widget_set_name(name, n);
        widget_set_opacity(name, 0); /* never painted: cc_label draws its text */
    }
    for (unsigned i = 0; i < sizeof hidden / sizeof *hidden; ++i)
        if ((c = widget_lookup(win, hidden[i], 1))) widget_set_visible(c, 0, 0);
    /* stock's Wi-Fi and Bluetooth switches, clicked by cc_act, under their circles' buttons: left
     * at stock's places, a tap on the block's padding would toggle them */
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
    /* a clear button over every stop but Brightness, in order, so the transport's lie over the Now
     * Playing block's and take its taps. Wi-Fi and Bluetooth have theirs too: stock's hidden
     * label_wifi and label_bt still take a touch (its hit test skips no hidden widget) and lie over
     * their switches inside view_menu, so a tap or hold never reached the switch. */
    for (int i = CC_WIFI; i < CC_BUTTONS; ++i) {
        if (i == CC_BRIGHT) continue;
        rect_t r = cc_box(i);
        void *button = button_create(win, r.x, r.y, r.w, r.h);
        widget_set_opacity(button, 0);
        if (i == CC_VOLUME) {
            widget_on(button, EVT_POINTER_DOWN, cc_drag, button);
            widget_on(button, EVT_POINTER_MOVE_BEFORE, cc_drag, button);
            widget_on(button, EVT_POINTER_UP_BEFORE, cc_drag, button);
        } else
            widget_on(button, EVT_CLICK, cc_click, (void *)(long)i);
        if (cc_paged(i)) quick_hold(button, QUICK_CC + i);
    }
    vol_bar.easing = bright_bar.easing = 0; /* the sliders open on their levels */
    widget_set_prop_int(win, CC_CABLE_SEEN, usb_ui_cable() + 1); /* as it will be drawn */
    widget_add_timer(win, cc_land, VOL_POLL_MS);
    widget_add_timer(win, cc_wifi_poll, CC_WIFI_POLL_MS);
}

/* The wheel on the page: from stop to stop, ends kept, past the Now Playing block while nothing is
 * loaded and short of the USB row while no cable is in; or a slider's level while it has it,
 * Volume's as Now Playing's wheel sets it (vol_next) and Brightness's as the Brightness page's
 * (bright_wheel). A tick with no ring showing only
 * brings it up. Any tick is the wheel's input for the focus rule's one record (st.touch_mode,
 * docs/oled-design.md#focus-ring), as a list's is: a page Centre opens from here (the USB
 * picker) shows its selection. */
void cc_wheel(void *win, int dir) {
    st.touch_mode = 0;
    if (cc_ring_wake(win)) {
        cc_marq(win);
        return;
    }
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
        if (next == CC_USB && !usb_ui_cable()) return; /* no row: Volume is the end */
        if (next < 0 || next >= CC_STOPS) return;
        widget_set_prop_int(win, CC_FOCUS, next);
    }
    cc_marq(win);
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
/* navigation.c's key-down: Centre pressed with the ring on a toggle starts its hold; its
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
    if (cc_ring_wake(win) && key == KEY_CENTER) { /* no ring: Centre only brings it up */
        cc_marq(win);
        return 1;
    }
    if (key == KEY_RETURN || focus == CC_BRIGHT || focus == CC_VOLUME)
        widget_set_prop_int(win, CC_ADJUST, !adjust);
    else if (cc_act(win, focus))
        return 1;
    cc_marq(win);
    widget_invalidate_force(win, (void *)0);
    return 1;
}
