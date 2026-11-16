/* Charging (docs/internals.md#charging, docs/oled-design.md#status-row): what the Q2 says while a
 * cable is in. */
#include "navigation.h"

/* 1 while the charger cannot keep up with the Q2: orange in the status row (list_marks.c
 * battery_slim) and its toasts. ponytail: nothing reports it. The AXP2101's driver
 * (axp2101-power-supply) exposes the battery's status, level, voltage, temperature and times to
 * empty and full, and stock's demo reads only battery/status and voltage_now; no input current
 * limit, charger type or VBUS state reaches user space that the firmware or the docs know of. A
 * real signal (the PMIC's input-limit flag, or the battery's current going negative while it says
 * Charging) would lift this; a guess from the level would not. */
int charge_weak(void) { return 0; }

/* A charging toast waits for the next UI loop pass (charging_poll, from usb_ui_poll): those that
 * start inside stock's USB pass or an input's dispatch, whose own loop a toast's would run again. */
static struct {
    char text[48];
    char glyph[32];
    int down;      /* a touch went down on the battery */
    int down_x, down_y;
} chg __attribute__((section(".scratch")));

static void charge_toast(const char *glyph, const char *text) {
    tk_snprintf(chg.glyph, sizeof chg.glyph, "%s", glyph);
    tk_snprintf(chg.text, sizeof chg.text, "%s", text);
}

/* The status battery's state in words and its toast glyph (a theme image's name without the art
 * prefix), for battery_now's key: too weak, held at Charge Limit, full, charging, low, or the level
 * alone. "·" is U+00B7, UTF-8. */
static const char *charge_words(unsigned key, char *out, int size) {
    int level = (int)BATT_KEY_LEVEL(key);
    if (key & BATT_KEY_WEAK) {
        tk_snprintf(out, size, "Charger too weak \xc2\xb7 %d%%", level);
        return "chargetoast_weak";
    }
    if (key & BATT_KEY_PLUGGED) {
        if (st.charge_held) tk_snprintf(out, size, "Charge Limit Reached (%d%%)", CHARGE_STOP);
        else tk_snprintf(out, size, "Fully charged");
        return "chargetoast_full";
    }
    if (key & BATT_KEY_CHARGING) {
        tk_snprintf(out, size, "Charging \xc2\xb7 %d%%", level);
        return "chargetoast_charging";
    }
    if (level < BATT_LOW_PCT) {
        tk_snprintf(out, size, "Low battery \xc2\xb7 %d%%", level);
        return "chargetoast_low";
    }
    tk_snprintf(out, size, "Battery %d%%", level);
    return ipod_light() ? "chargetoast_battery_l" : "chargetoast_battery_d";
}

/* A tap on the status row's battery (docs/oled-design.md#status-row): a target TAP_W x TAP_H
 * round the body's centre, so at least 40 px round it, whose lift within TAP_SLOP of its press
 * toasts the battery's state. The touch goes on to stock as before, so a pull from the row still
 * opens Control Center. */
#define TAP_W 44
#define TAP_H 40
#define TAP_SLOP 12
#define TOUCH_DOWN 0xff /* the raw pointer events ringnav_input sees (offsets.inc EVT_POINTER_FIRST) */
#define TOUCH_UP 0x103
static int on_battery(int x, int y) {
    void *slot = st.bar_slot, *bar = *(void *const *)system_bar;
    if (!slot || !bar || !widget_get_visible(slot) || !widget_get_visible(bar) || st.batt_mode)
        return 0;
    int xy[2] = { x, y };
    widget_to_local(slot, xy);
    /* the body's centre: centred on its row's height, as navigation.c batt_top places it */
    int cx = geom_val(V_BATT_W) / 2, cy = (I(P(slot, W_PARENT), W_H) + 1) / 2;
    return xy[0] >= cx - TAP_W / 2 && xy[0] < cx + TAP_W / 2 && xy[1] >= cy - TAP_H / 2 &&
           xy[1] < cy + TAP_H / 2;
}
void charge_touch(unsigned type, int x, int y) {
    if (type == TOUCH_DOWN) {
        chg.down = g_backlight_status && usable() && on_battery(x, y);
        chg.down_x = x, chg.down_y = y;
        return;
    }
    if (type != TOUCH_UP || !chg.down) return;
    chg.down = 0;
    int dx = x - chg.down_x, dy = y - chg.down_y;
    if (dx < -TAP_SLOP || dx > TAP_SLOP || dy < -TAP_SLOP || dy > TAP_SLOP || !on_battery(x, y))
        return;
    char words[48];
    const char *glyph = charge_words(st.batt_key, words, sizeof words);
    charge_toast(glyph, words);
}

/* ---- the cable's toasts and the plug-in screen ---- */

/* What a cable just put in says, as a battery key: the level stock last read, held at Charge
 * Limit or full, else charging (too weak if charge_weak says so). */
static unsigned cable_key(void) {
    int level = I(g_power_capacity, 0);
    unsigned key = (unsigned)(level < 0 ? 0 : level > 100 ? 100 : level) << 4;
    if (charge_weak()) return key | BATT_KEY_WEAK;
    if (st.charge_held || I(g_power_chargestate, 0) == 2) return key | BATT_KEY_PLUGGED;
    return key | BATT_KEY_CHARGING;
}

/* The plug-in screen (S1, docs/oled-design.md#plug-in-screen, from mock_charge_anim.py's V1): with
 * the screen off at the plug-in, it lights for S1_MS with a big battery, its fill to the level and
 * a sweep up the last S1_SWEEP percent to it on a S1_LOOP_MS loop, the level beside a green
 * lightning, and "Charging" (or "Charged"); then the screen goes off again. Black in both halves:
 * it is a lit dark screen, not a page. In screen pixels: */
#define S1_MS 3000
#define S1_FRAME_MS 50
#define S1_LOOP_MS 2000
#define S1_GROW_MS 1300 /* the sweep grows to the level (ease out), holds to S1_HOLD_MS, then fades */
#define S1_HOLD_MS 1700
#define S1_SWEEP 14
#define S1_CX 187.5f /* the body: S1_W x S1_H round (S1_CX, S1_CY), S1_R corners, a S1_LINE outline */
#define S1_CY 132.0f
#define S1_W 170.0f
#define S1_H 76.0f
#define S1_R 16.0f
#define S1_LINE 3.0f
#define S1_BODY_RGB 0x3a3a3c
#define S1_IN 8.0f /* the fill S1_IN inside the body, S1_FILL_R corners */
#define S1_FILL_R 9.0f
#define S1_NUB_GAP 5.0f /* the nub S1_NUB_GAP past the body, S1_NUB_W x S1_NUB_H, S1_NUB_R corners */
#define S1_NUB_W 7.0f
#define S1_NUB_H 28.0f
#define S1_NUB_R 4.0f
#define S1_GREEN 0x34c759
#define S1_PCT_Y 212   /* the level's line, Inter Medium 30 (36 in AWTK's sizes), S1_PCT_DX right of */
#define S1_PCT_PX 36   /* centre, the bolt (tools/art.py CHARGE_BOLT_PX) S1_BOLT_GAP left of it */
#define S1_PCT_DX 12
#define S1_BOLT 26
#define S1_BOLT_GAP 3
#define S1_WORD_Y 244 /* "Charging", Inter Regular 16 (19), Text Secondary */
#define S1_WORD_PX 19
#define S1_WORD_RGB 0xababab
#define S1_LINE_H 40

static struct {
    int pending, shown, level, still; /* still: full or held, no sweep */
    int eat; /* the key or touch that ended it: dropped to its release */
    unsigned at, timer;
    void *top; /* the window it lit over: another opening (the chooser) ends it */
    int weak_was;
} s1 __attribute__((section(".scratch")));

/* The end: the screen goes off as keylock.c's card leaves it, unless something else took it (a
 * page opened over it) or a key woke it (s1_off). */
static void s1_off(void) {
    stop_timer(&s1.timer);
    s1.shown = 0;
    widget_invalidate_force(window_manager(), (void *)0);
}
static int s1_frame(const void *info) {
    (void)info;
    s1.timer = 0;
    if (!s1.shown) return 0;
    void *top = window_manager_get_top_window(window_manager());
    if (top != s1.top) { /* the chooser or another page: it stays lit for that */
        s1_off();
        return 0;
    }
    if ((unsigned)time_now_ms() - s1.at >= S1_MS) {
        s1.shown = 0;
        if (g_backlight_status) {
            g_backlight_status = 0;
            screen_action(0);
        }
        return 0;
    }
    widget_invalidate_force(window_manager(), (void *)0);
    rearm(&s1.timer, s1_frame, S1_FRAME_MS);
    return 0;
}
/* Lit as keylock.c's card is: painted first, so the panel never shows the page left at
 * screen-off. */
static void s1_on(void) {
    void *wm = window_manager();
    unsigned key = cable_key();
    s1.level = (int)BATT_KEY_LEVEL(key);
    s1.still = (key & BATT_KEY_PLUGGED) || s1.level >= 100;
    s1.top = window_manager_get_top_window(wm);
    s1.at = (unsigned)time_now_ms();
    s1.shown = 1;
    g_backlight_status = 1; /* the paint gate (ringnav_wm_paint) opens */
    widget_invalidate_force(wm, (void *)0);
    ringnav_wm_paint(wm);
    enable_fb();
    usleep(50000); /* awake_screen's settle before screen_action */
    screen_action(1);
    rearm(&s1.timer, s1_frame, S1_FRAME_MS);
}

/* The sweep's reach in tenths of a percent and its alpha (0-255) at t ms into a loop, from base
 * up to level. */
static int s1_sweep(int base, int level, unsigned t, unsigned *alpha) {
    *alpha = 255;
    if (t < S1_GROW_MS) {
        float u = 1 - (float)t / S1_GROW_MS; /* ease out: 1 - (1 - t)^3 */
        return base * 10 + (int)((float)(level - base) * 10 * (1 - u * u * u));
    }
    if (t >= S1_HOLD_MS)
        *alpha = 255u * (S1_LOOP_MS - (t < S1_LOOP_MS ? t : S1_LOOP_MS)) / (S1_LOOP_MS - S1_HOLD_MS);
    return level * 10;
}

static void s1_fill(void *vg, float x, float y, float tenths, float h, unsigned rgba) {
    float w = (S1_W - 2 * S1_IN) * tenths / 1000;
    if (w <= 0) return;
    round_path(vg, x, y, w, h, w < 2 * S1_FILL_R ? w / 2 : S1_FILL_R);
    vgcanvas_set_fill_color(vg, rgba);
    vgcanvas_fill(vg);
}

void charge_paint(void *w, void *canvas) {
    if (!s1.shown || !w) return;
    void *lcd = P(canvas, CANVAS_LCD), *bar = *(void *const *)system_bar;
    if (!lcd || (w != bar && w != s1.top)) return;
    unsigned fill = (unsigned)I(lcd, LCD_FILL_COLOR);
    stock_canvas_set_fill_color(canvas, RGBA(0x000000));
    canvas_fill_rect(canvas, 0, 0, I(w, W_W), I(w, W_H));
    void *vg = canvas_get_vgcanvas(canvas);
    if (w == bar || !vg) {
        stock_canvas_set_fill_color(canvas, fill);
        return;
    }
    float dy = (float)I(w, W_Y), x0 = S1_CX - S1_W / 2, y0 = S1_CY - S1_H / 2 - dy;
    unsigned raw = st.raw_ink;
    st.raw_ink = 1; /* final colours */
    vgcanvas_save(vg);
    vgcanvas_translate(vg, (float)I(canvas, CANVAS_X), (float)I(canvas, CANVAS_Y));
    round_path(vg, x0 + S1_LINE / 2, y0 + S1_LINE / 2, S1_W - S1_LINE, S1_H - S1_LINE,
               S1_R - S1_LINE / 2);
    vgcanvas_set_line_width(vg, S1_LINE);
    vgcanvas_set_stroke_color(vg, RGBA(S1_BODY_RGB));
    vgcanvas_stroke(vg);
    round_path(vg, x0 + S1_W + S1_NUB_GAP, S1_CY - S1_NUB_H / 2 - dy, S1_NUB_W, S1_NUB_H, S1_NUB_R);
    vgcanvas_set_fill_color(vg, RGBA(S1_BODY_RGB));
    vgcanvas_fill(vg);
    int base = s1.still ? s1.level : s1.level > S1_SWEEP ? s1.level - S1_SWEEP : 0;
    float fx = x0 + S1_IN, fy = y0 + S1_IN, fh = S1_H - 2 * S1_IN;
    if (!s1.still) {
        unsigned alpha, t = ((unsigned)time_now_ms() - s1.at) % S1_LOOP_MS;
        int reach = s1_sweep(base, s1.level, t, &alpha);
        s1_fill(vg, fx, fy, (float)reach, fh, (RGBA(S1_GREEN) & 0xffffffu) | alpha << 24);
    }
    s1_fill(vg, fx, fy, (float)base * 10, fh, RGBA(S1_GREEN));
    vgcanvas_restore(vg);
    st.raw_ink = raw;
    /* the level, the bolt S1_BOLT_GAP left of it, and the word under them */
    unsigned s[8], n = 0;
    char digits[8];
    tk_snprintf(digits, sizeof digits, "%d%%", s1.level);
    for (; digits[n]; ++n) s[n] = (unsigned char)digits[n];
    s[n] = 0;
    canvas_set_font(canvas, "inter-medium", S1_PCT_PX);
    int tw = (int)(canvas_measure_text(canvas, s, n) + 0.5f), cx = (int)S1_CX + S1_PCT_DX;
    rect_t pct = { cx - tw / 2 - 4, S1_PCT_Y - S1_LINE_H / 2 - (int)dy, tw + 8, S1_LINE_H };
    final_text(canvas, "inter-medium", s, &pct, S1_PCT_PX, 0xffffffffu);
    paint_art(w, canvas, theme_art("chargebolt"), cx - tw / 2 - S1_BOLT_GAP - S1_BOLT,
              S1_PCT_Y - S1_BOLT / 2 - (int)dy);
    rect_t word = { 0, S1_WORD_Y - S1_LINE_H / 2 - (int)dy, I(w, W_W), S1_LINE_H };
    final_text(canvas, "inter",
               s1.level >= 100 ? (const unsigned *)L"Charged" : (const unsigned *)L"Charging", &word,
               S1_WORD_PX, 0xff000000u | S1_WORD_RGB);
    stock_canvas_set_fill_color(canvas, fill);
}

/* ringnav_input, after Buttons Lock's gate: while the plug-in screen shows, a key or a touch ends
 * it, the screen left lit as a wake, and is dropped to its release, so it does nothing else. 1
 * drops the event. */
int charge_gate(void *e) {
    unsigned type = (unsigned)I(e, EVENT_TYPE);
    if (s1.eat) {
        if (type == EVT_KEY_UP || type == TOUCH_UP) s1.eat = 0;
        return 1;
    }
    if (!s1.shown || (type != EVT_KEY_DOWN && type != TOUCH_DOWN)) return 0;
    s1_off();
    reset_poweroptions_timer(1, 1, 1);
    s1.eat = 1;
    return 1;
}

/* usb_ui_status: a cable in. The chirp in the headphones (clicker.c clicker_chirp), and dark, the
 * plug-in screen at the next pass (not inside stock's USB pass); lit, the toast waits for
 * usb_charger_in, which knows it is not a computer. */
void charge_plugged(void) {
    clicker_chirp();
    if (!g_backlight_status) s1.pending = 1;
}

/* usb_ui.c: a cable no computer answered, or a remembered Charge Only. Lit, T1: the level and
 * what the cable does with it. */
void usb_charger_in(void) {
    if (!g_backlight_status || s1.shown || s1.pending) return;
    char words[48];
    unsigned key = cable_key();
    const char *glyph = charge_words(key, words, sizeof words);
    charge_toast(glyph, words);
}

/* system.c charge_poll: Charge Limit has stopped charging on a cable (T3). */
void charge_limited(void) {
    char words[48];
    tk_snprintf(words, sizeof words, "Charged to %d%%", CHARGE_STOP);
    charge_toast("chargetoast_full", words);
}

/* usb_ui_poll, every UI loop pass: the plug-in screen a dark plug-in asked for, a weak charger's
 * start (T6), and a toast left by a tap or the cable. */
void charging_poll(void) {
    if (s1.pending) {
        s1.pending = 0;
        if (!g_backlight_status && !video_on() && !addon_game_on()) s1_on();
    }
    int weak = g_usbdet_value == 1 && charge_weak();
    if (weak && !s1.weak_was) charge_toast("chargetoast_weak", "Charger too weak");
    s1.weak_was = weak;
    if (!chg.text[0]) return;
    char text[sizeof chg.text];
    tk_snprintf(text, sizeof text, "%s", chg.text);
    chg.text[0] = 0;
    if (g_backlight_status) toast_icon(theme_art(chg.glyph), text);
}
