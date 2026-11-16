/* The volume panel: stock's volume dialog, drawn over Now Playing's times or on its own. */
#include "navigation.h"

/* The dialog vol_paint last drew, its value and the redraw timer. vol_shown:
 * the bar's eased level in 1/256 steps, moving towards the value while vol_easing (vol_ease), from
 * vol_eased_at. */
static struct {
    void *vol_dialog;
    int vol_drawn, vol_shown, vol_easing;
    unsigned vol_timer, vol_eased_at;
} vd __attribute__((section(".scratch")));

/* A wheel tick's volume steps at the wheel's speed (wheel_speed, degrees a second): Rockbox's iPod
 * list acceleration (firmware/drivers/button.c button_apply_acceleration, WHEEL_ACCELERATION 3 and
 * WHEEL_ACCEL_START 270 in its ipodvideo.h and ipod6g.h), (speed / 270)^4 rounded, at least 1 and
 * at most VOL_ACCEL_MAX. Rockbox steps an iPod's volume 1 a click at any speed; Apple's own curve
 * is not published (docs/internals.md#wheel-volume). */
static int vol_accel(void) {
    unsigned v = (unsigned)wheel_speed;
    if (v > VOL_ACCEL_TOP) v = VOL_ACCEL_TOP;
    unsigned r = v * 256 / VOL_ACCEL_START, r2 = r * r >> 8, n = ((r2 * r2 >> 8) + 128) >> 8;
    return n < 1 ? 1 : n > VOL_ACCEL_MAX ? VOL_ACCEL_MAX : (int)n;
}
/* The volume a wheel tick (KEY_NEXT up, KEY_PREV down) moves to: vol_accel's steps, up to 100 and
 * g_maxvolume (never down to them from above), down to 0. A change starts the bar's ease from where
 * it was (vol_ease). Nothing is set: the caller does, as stock's dialog would. */
int vol_next(unsigned key) {
    int v = g_volume, n = vol_accel(), limit = g_maxvolume < 100 ? g_maxvolume : 100;
    int to = key == KEY_NEXT ? (v >= limit      ? v
                                : v + n > limit ? limit
                                                : v + n)
                             : (v - n < 0 ? 0 : v - n);
    if (to != v && !vd.vol_easing) {
        vd.vol_shown = v << 8;
        vd.vol_easing = 1;
        vd.vol_eased_at = (unsigned)time_now_ms();
    }
    return to;
}
/* volume_dialog_on_keyup (HOOKS): a wheel tick takes g_volume to one step short of vol_next's, so
 * stock's own step (its clamps, device_set_volume, the save, slider_vol and the 3 s count) lands on
 * it; every other key is stock's. */
int vol_keyup(void *dialog, void *e) {
    unsigned key = e ? (unsigned)I(e, EVENT_KEY) : 0;
    if (key == KEY_NEXT || key == KEY_PREV) {
        int to = vol_next(key);
        if (to != g_volume) g_volume = (unsigned char)(to + (key == KEY_NEXT ? -1 : 1));
    }
    return stock_vol_keyup_trampoline(dialog, e);
}

/* The bar's level towards level (in steps) while vol_easing: a first-order follow with time
 * constant VOL_EASE_MS, by the time since the last move, so a slow pass jumps further rather than
 * lagging; 1 while it moved. Within a 1/256 step of level it lands and stops. */
static int vol_ease(int level) {
    if (!vd.vol_easing) return 0;
    unsigned now = (unsigned)time_now_ms();
    int dt = (int)(now - vd.vol_eased_at), d = (level << 8) - vd.vol_shown;
    vd.vol_eased_at = now;
    int step = dt > 0 ? d * dt / (VOL_EASE_MS + dt) : 0;
    if (!step && dt > 0) step = d > 0 ? 1 : -1; /* the last few 1/256ths */
    if (d > -2 && d < 2) step = d, vd.vol_easing = 0;
    vd.vol_shown += step;
    return step != 0 || !vd.vol_easing;
}

/* Every VOL_POLL_MS while the dialog vol_paint drew is still on top: a changed volume, or a bar
 * still easing towards it (vol_ease), repaints it whole. Stock's own invalidation of the hidden
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
    if (vol_ease(level) | (level != vd.vol_drawn)) widget_invalidate_force(top, (void *)0);
    return 8; /* RET_REPEAT */
}

/* The volume, drawn by stock's dialog/volume_dialog itself: transparent and full-screen (its
 * dimming highlight removed at build time, tools/ipod.py), it opens on the wheel's volume and
 * sets its slider_vol. Over Now Playing, as on an iPod classic, the band from the progress bar to
 * the times turns black with a white bar over the track and "Volume N"; under a status row's set
 * (OLED's) the bar, inset, is white between two speakers with no text. Over any other window
 * (Quick Settings included) a VOL_PANEL_* rounded panel in the fast-scroll letter's style holds a
 * pill bar and the number alone. slider_vol and label_vol are hidden, and slider_vol is moved onto the area drawn, so
 * stock's partial repaints land on it; vol_poll catches the changes those miss. The bar draws
 * vol_shown, which a wheel tick (vol_next) eases towards the value; the number is the value. */
void vol_paint(void *top, void *canvas) {
    void *wm = window_manager(), *lcd = P(canvas, CANVAS_LCD);
    if (!lcd || tk_strcmp(widget_get_prop_str(top, "name", ""), "volume_dialog")) return;
    unsigned n = widget_count_children(wm);
    void *vol = widget_lookup(top, "slider_vol", 1), *label = widget_lookup(top, "label_vol", 1);
    if (n < 2 || !vol) return;
    int np = widget_get_child(wm, n - 2) == st.np_win && st.np_slider && st.np_elapsed;
    rect_t area, bar, text, lo;
    int ends = np && geom_val(V_STATUS_Y); /* OLED's: speakers at the bar's ends */
    if (np) {
        void *slider = st.np_slider, *times = st.np_elapsed;
        int dy = I(st.np_win, W_Y) - I(top, W_Y), y = dy + I(slider, W_Y),
            bh = widget_get_prop_int(slider, "bar_size", 8), ty = dy + I(times, W_Y);
        bar = (rect_t){ I(slider, W_X), y + (I(slider, W_H) - bh) / 2, I(slider, W_W), bh };
        lo = bar; /* the progress bar's own ends, which the speakers keep */
        if (ends) bar.x += VOL_SPK_INSET, bar.w -= 2 * VOL_SPK_INSET;
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
    if (top != vd.vol_dialog) vd.vol_easing = 0; /* a new dialog opens on its value */
    if (!vd.vol_easing) vd.vol_shown = level << 8;
    /* the bar's level: the eased one, in 1/256 steps */
    int eased = max > 0 ? clamp_step(0, max << 8, vd.vol_shown) : 0;
    vd.vol_dialog = top;
    vd.vol_drawn = level;
    if (!vd.vol_timer) vd.vol_timer = timer_add(vol_poll, (void *)0, VOL_POLL_MS);
    unsigned fill = (unsigned)I(lcd, LCD_FILL_COLOR), s[12] = { 'V', 'o', 'l', 'u', 'm', 'e', ' ' },
             k = np ? 7 : 0;
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
    /* Over Now Playing it is the progress bar's own: the accent's fill; white between speakers */
    if (w) fill_box(canvas, &on, RGBA(np && !ends ? accent_tone(TONE_LIGHT) : 0xffffff), bar.h / 2);
    stock_canvas_set_fill_color(canvas, fill);
    if (ends) { /* no number: a VOL_SPK_PX speaker VOL_SPK_X in from each end of the progress bar */
        int cy = bar.y + bar.h / 2, L = ipod_light();
        paint_art(top, canvas, theme_art(L ? "vol_lo_l" : "vol_lo_d"),
                  lo.x + VOL_SPK_X - VOL_SPK_PX / 2, cy - VOL_SPK_PX / 2);
        paint_art(top, canvas, theme_art(L ? "vol_hi_l" : "vol_hi_d"),
                  lo.x + lo.w - VOL_SPK_X - VOL_SPK_PX / 2, cy - VOL_SPK_PX / 2);
        return;
    }
    k += put_num(s + k, (unsigned)clamp_step(0, 999, level));
    draw_centred(canvas, s, k, &text, np ? NP_TIMES_PX : VOL_PANEL_PX, 0xffffffff);
}
