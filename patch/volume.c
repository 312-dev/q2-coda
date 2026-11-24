/* The volume panel: stock's volume dialog, drawn over Now Playing's times or on its own. */
#include "navigation.h"

/* The dialog vol_paint last drew, its value and the redraw timer. */
static struct {
    void *vol_dialog;
    int vol_drawn;
    unsigned vol_timer;
} vd __attribute__((section(".scratch")));
/* The volume bar's ease (levels.c level_ease), from vol_next: the dialog's bar and Control
 * Center's slider draw it. */
level_ease_t vol_bar __attribute__((section(".scratch")));

/* A wheel tick's volume steps at the wheel's speed (wheel_speed, degrees a second): Rockbox's iPod
 * list acceleration (firmware/drivers/button.c button_apply_acceleration, WHEEL_ACCELERATION 3 and
 * WHEEL_ACCEL_START 270 in its ipodvideo.h and ipod6g.h), (speed / 270)^4 rounded, at least 1 and
 * at most VOL_ACCEL_MAX. Rockbox steps an iPod's volume 1 a click at any speed; Apple's own curve
 * is not published (docs/internals.md#wheel-volume). */
int vol_accel(void) {
    unsigned v = (unsigned)wheel_speed;
    if (v > VOL_ACCEL_TOP) v = VOL_ACCEL_TOP;
    unsigned r = v * 256 / VOL_ACCEL_START, r2 = r * r >> 8, n = ((r2 * r2 >> 8) + 128) >> 8;
    return n < 1 ? 1 : n > VOL_ACCEL_MAX ? VOL_ACCEL_MAX : (int)n;
}
/* The volume a wheel tick (KEY_NEXT up, KEY_PREV down) moves to: vol_accel's steps (levels.c
 * wheel_level), up to vol_cap (100, Max Volume and a Bluetooth device's Volume Limit,
 * bt_vol_limit.c; never down to it from above), down to 0. A change
 * starts the bar's ease from where it was (vol_bar). Nothing is set: the caller does, as stock's
 * dialog would. */
int vol_next(unsigned key) {
    int v = g_volume, limit = vol_cap();
    int to = wheel_level(v, 0, limit, 100, key == KEY_NEXT ? 1 : -1);
    if (to != v) level_ease_from(&vol_bar, v);
    return to;
}
/* volume_dialog_on_keyup (HOOKS): a wheel tick takes g_volume to one step short of vol_next's, so
 * stock's own step (its clamps, device_set_volume, the save, slider_vol and the 3 s count) lands on
 * it; every other key is stock's. Stock's step clamps at Max Volume alone, so a tick up at a
 * Bluetooth device's lower Volume Limit is also set one short, landing on the limit again, and a
 * limit of 0 is set back after stock's step. */
int vol_keyup(void *dialog, void *e) {
    unsigned key = e ? (unsigned)I(e, EVENT_KEY) : 0;
    if (key == KEY_NEXT || key == KEY_PREV) {
        /* A dialog left open past the screen-off: its tick goes on to vol_wm_keyup, which drops it
         * dark, as stock's own dialog leaves it (tools/build.py VOL_DARK) */
        if (!g_backlight_status) return 0;
        int to = vol_next(key), top = vol_cap();
        if (to != g_volume || (key == KEY_NEXT && to >= top && to > 0 && top < g_maxvolume))
            g_volume = (unsigned char)(to + (key == KEY_NEXT ? -1 : 1));
        int r = stock_vol_keyup_trampoline(dialog, e);
        if (g_volume > top) { /* stock's step past a limit of 0 */
            g_volume = (unsigned char)top;
            device_set_volume(top, 1);
            write_int_config(top, "PLAYSET", "VOLUME");
            void *slider = widget_lookup(dialog, "slider_vol", 1);
            if (slider) widget_set_prop_int(slider, "value", top);
        }
        return r;
    }
    return stock_vol_keyup_trampoline(dialog, e);
}

/* The volume moved from `from` by something other than the wheel (a headset's own keys or its
 * AVRCP volume, bt_volume_poll; the Car Remote, coda_rc_shim.c): shown at once as a wheel's change
 * shows, with the screen lit. The volume dialog in front takes the new value and its 3 s start
 * again (stock's count, VOL_DIALOG_COUNT, as its keyup zeroes it); Control Center's slider eases to
 * it; Playback's Volume page shows it itself (xfade_volume_shown). On a volume page (vol_page: Now
 * Playing and the rest) stock's volume dialog opens on it, Now Playing's band (vol_paint); on any
 * other page the status row's pill shows it (vol_pill.c), which takes no keys, so over a dialog
 * too. Not over a video, which has its own volume, nor where stock's on_wm_keyup_fun opens none
 * (the lock window, test mode, the first-boot guide, powering off). */
void vol_show(int from) {
    void *top = window_manager_get_top_window(window_manager());
    if (xfade_volume_shown(top) || !g_backlight_status || !top || video_on()) return;
    const char *name = widget_get_prop_str(top, "name", "");
    level_ease_from(&vol_bar, from);
    if (!tk_strcmp(name, "volume_dialog")) {
        void *slider = widget_lookup(top, "slider_vol", 1);
        if (slider) widget_set_prop_int(slider, "value", g_volume);
        *(volatile signed char *)VOL_DIALOG_COUNT = 0;
    } else if (!tk_strcmp(name, "statusbar_dialog"))
        level_follow(top);
    else if (g_lockscreen_pageflag || g_testmode_flag || g_guideflag || g_poweroff_state)
        ; /* stock's own exits */
    else if (!vol_page(page_in_use()))
        vol_pill_show();
    else if (tk_strcmp(widget_get_type(top), "dialog"))
        navigator_to("dialog/volume_dialog");
}

/* The windows whose wheel is the volume: playback screens. Stock's Now Playing (TIDAL's songs and
 * the card apps' play there too), the AirPlay and DLNA receiver, Internet Radio's Now Playing and
 * Spotify's while it shows a track (spot_np_track). Videos (videos.c video_key), Control Center's
 * slider and Playback's Volume row (crossfade.c) set it themselves. */
static const char *const vol_pages[] = { "playing_page", "airplay_page", "radionp_page" };
/* Whether page (page_in_use's) is one of those, its wheel the volume. */
int vol_page(void *page) {
    const char *name = page ? widget_get_prop_str(page, "name", "") : "";
    for (unsigned i = 0; i < sizeof vol_pages / sizeof *vol_pages; ++i)
        if (!tk_strcmp(name, vol_pages[i])) return 1;
    return page && spot_np_track(page);
}
/* on_wm_keyup_fun (HOOKS): the key-up no page took. Stock opens its volume dialog on any wheel tick
 * there, lit or dark; here only on a lit playback screen (vol_page, under a toast), so a list's
 * end, a page with nothing to scroll (an empty state's own page, Spotify's note) or a QR code take
 * the tick as nothing and Return still goes back (docs/internals.md#wheel-volume). A dropped tick
 * returns before stock clears its power-key flags, which the next Centre release clears. Every
 * other key, and the Centre releases the payload replays, are stock's. */
int vol_wm_keyup(void *ctx, void *e) {
    unsigned key = e ? (unsigned)I(e, EVENT_KEY) : 0;
    if ((key == KEY_NEXT || key == KEY_PREV) &&
        !vol_page(g_backlight_status ? page_in_use() : (void *)0))
        return 0;
    return stock_wm_keyup_trampoline(ctx, e);
}

/* Every VOL_POLL_MS while the dialog vol_paint drew is still on top: a changed volume, or a bar
 * still easing towards it (level_ease), repaints it whole. Stock's own invalidation of the hidden
 * slider does not reliably reach the screen, so the bar would stop following the wheel after a tick
 * or two. */
static int vol_poll(const void *info) {
    void *top = window_manager_get_top_window(window_manager());
    void *vol = top && top == vd.vol_dialog ? widget_lookup(top, "slider_vol", 1) : (void *)0;
    if (!vol) {
        vd.vol_timer = 0;
        vd.vol_dialog = (void *)0;
        return 0; /* RET_OK: removed */
    }
    int level = widget_get_prop_int(vol, "value", 0);
    if (level_ease(&vol_bar, level) | (level != vd.vol_drawn))
        widget_invalidate_force(top, (void *)0);
    return 8; /* RET_REPEAT */
}

/* The volume, drawn by stock's dialog/volume_dialog itself: transparent and full-screen (its
 * dimming highlight removed at build time, tools/ipod.py), it opens on the wheel's volume and
 * sets its slider_vol. Over Now Playing, as on an iPod classic, the band from the progress bar to
 * the times turns black with a white bar over the track and "Volume N"; under a status row's set
 * (OLED's) the bar, inset, is white between two speakers with no text. Over any other window
 * (Quick Settings included) a VOL_PANEL_* rounded panel in the fast-scroll letter's style holds a
 * pill bar and the number alone. slider_vol and label_vol are hidden, and slider_vol is moved onto
 * the area drawn, so stock's partial repaints land on it; vol_poll catches the changes those miss.
 * The bar draws vol_bar, which a wheel tick (vol_next) eases towards the value; the number is the
 * value. */
void vol_paint(void *top, void *canvas) {
    void *wm = window_manager(), *lcd = P(canvas, CANVAS_LCD);
    if (!lcd || tk_strcmp(widget_get_prop_str(top, "name", ""), "volume_dialog")) return;
    unsigned n = widget_count_children(wm);
    void *vol = widget_lookup(top, "slider_vol", 1), *label = widget_lookup(top, "label_vol", 1);
    if (n < 2 || !vol) return;
    int np = widget_get_child(wm, n - 2) == st.np_win && st.np_slider && st.np_elapsed;
    rect_t area, bar, text, lo;
    if (np) {
        void *slider = st.np_slider, *times = st.np_elapsed;
        int dy = I(st.np_win, W_Y) - I(top, W_Y), y = dy + I(slider, W_Y),
            bh = widget_get_prop_int(slider, "bar_size", 8), ty = dy + I(times, W_Y);
        bar = (rect_t){ I(slider, W_X), y + (I(slider, W_H) - bh) / 2, I(slider, W_W), bh };
        lo = bar; /* the progress bar's own ends, which the speakers keep */
        bar.x += VOL_SPK_INSET, bar.w -= 2 * VOL_SPK_INSET;
        text = (rect_t){ 0, ty, I(st.np_win, W_W), I(times, W_H) };
        area = (rect_t){ 0, y, text.w, ty + text.h - y };
    } else {
        area = (rect_t){ VOL_PANEL_X, VOL_PANEL_Y, I(top, W_W) - 2 * VOL_PANEL_X, VOL_PANEL_H };
        int x = area.x + VOL_PANEL_PAD, bw = area.w - 2 * VOL_PANEL_PAD - VOL_PANEL_TEXT;
        bar = (rect_t){ x, area.y + (area.h - VOL_PANEL_BAR) / 2, bw, VOL_PANEL_BAR };
        text = (rect_t){ x + bw, area.y, VOL_PANEL_TEXT, area.h };
    }
    if (I(vol, W_Y) != area.y) widget_move_resize(vol, area.x, area.y, area.w, area.h);
    widget_set_visible(vol, 0, 0);
    if (label) widget_set_visible(label, 0, 0);
    int max = widget_get_prop_int(vol, "max", 100), level = widget_get_prop_int(vol, "value", 0);
    if (top != vd.vol_dialog) vol_bar.easing = 0; /* a new dialog opens on its value */
    /* the bar's level: the eased one, in 1/256 steps */
    int eased = max > 0 ? clamp_step(0, max << 8, level_shown(&vol_bar, level)) : 0;
    vd.vol_dialog = top;
    vd.vol_drawn = level;
    if (!vd.vol_timer) vd.vol_timer = timer_add(vol_poll, (void *)0, VOL_POLL_MS);
    unsigned fill = (unsigned)I(lcd, LCD_FILL_COLOR), s[12];
    int w = max > 0 ? bar.w * eased / (max << 8) : 0;
    if (np) {
        canvas_set_fill_color(canvas, RGBA(0));
        canvas_fill_rect(canvas, area.x, area.y, area.w, area.h);
    } else {
        /* unsigned, as draw_letter's: 0xd9 << 24 is past INT_MAX */
        fill_box(canvas, &area, ((unsigned)LETTER_ALPHA << 24) | FILL_RGB, LETTER_RADIUS);
    }
    /* Pills, like the progress capsule: a non-zero fill is at least round, never a sliver. */
    fill_box(canvas, &bar, RGBA(np ? theme()->np_track_rgb : VOL_PANEL_TRACK), bar.h / 2);
    rect_t on = { bar.x, bar.y, w < bar.h ? bar.h : w, bar.h };
    /* white: over Now Playing on the progress bar, between its speakers */
    if (w) fill_box(canvas, &on, RGBA(0xffffff), bar.h / 2);
    stock_canvas_set_fill_color(canvas, fill);
    if (np) { /* no number: a VOL_SPK_PX speaker VOL_SPK_X in from each end of the progress bar */
        int cy = bar.y + bar.h / 2, L = ipod_light();
        paint_art(top, canvas, theme_art(L ? "vol_lo_l" : "vol_lo_d"),
                  lo.x + VOL_SPK_X - VOL_SPK_PX / 2, cy - VOL_SPK_PX / 2);
        paint_art(top, canvas, theme_art(L ? "vol_hi_l" : "vol_hi_d"),
                  lo.x + lo.w - VOL_SPK_X - VOL_SPK_PX / 2, cy - VOL_SPK_PX / 2);
        return;
    }
    unsigned k = put_num(s, (unsigned)clamp_step(0, 999, level));
    draw_centred(canvas, s, k, &text, VOL_PANEL_PX, 0xffffffff);
}
