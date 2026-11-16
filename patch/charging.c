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

/* usb_ui_poll, every UI loop pass: a toast left by a tap or the cable. */
void charging_poll(void) {
    if (!chg.text[0]) return;
    char text[sizeof chg.text];
    tk_snprintf(text, sizeof text, "%s", chg.text);
    chg.text[0] = 0;
    if (g_backlight_status) toast_icon(theme_art(chg.glyph), text);
}
