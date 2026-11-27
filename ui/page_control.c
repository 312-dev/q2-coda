/* Control Center (docs/oled-design.md#control-center), q2ui's own while it has the screen: opened
 * by holding Return on any page (main.c), closed by Return. Its switches are demo's settings,
 * shown as demo says they stand (ui/status.c status_told_value, from patch/control_center.c
 * q2ui_cc_state) and flipped down q2ui's own pipe to demo ("set NAME 0|1", ui/player.c player_set,
 * control_center.c q2ui_cc_set); a row turns at once and keeps its new state until demo's word catches up, half a
 * second or so, as status.c has every row asked again when demo's word changes. Bluetooth, USB and the EQ's own page open demo's pages through the gate.
 * Brightness and Volume are inline sliders (ROW_SLIDER): Centre grabs one, the wheel sets it
 * (Brightness down the pipe, "set bright N"; Volume down the player's queue, "volume N", as Now
 * Playing sets it), and Centre or Return sets it down. Each shows its level at once and keeps it
 * until demo's word ("bright", "vol") says the same; Volume stops at demo's top ("volmax": Max
 * Volume, a Bluetooth device's Volume Limit), which demo would hold it under. A level demo never
 * took (a full queue) is let go when the row is next asked, LEVEL_HOLD_US after the last turn.
 * ponytail: a list, not demo's two-column sheet of circles, Now Playing and pill sliders; no
 * AirPlay; a grabbed slider's row does not grow as the inline slider's spec has it. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "q2ui.h"

enum { CC_WIFI, CC_BT, CC_CAR, CC_SMART, CC_EQ, CC_BRIGHT, CC_VOLUME, CC_USB, CC_ROWS };
#define LEVEL_HOLD_US 1500000u
static const struct {
    const char *title, *key, *demo; /* key: demo's word for a switch; demo: the page a chevron opens */
} rows[CC_ROWS] = { [CC_WIFI] = { "Wi-Fi", "wifi", 0 },          [CC_BT] = { "Bluetooth", "bton", "systemset/bluetooth_page" },
                    [CC_CAR] = { "Car Remote", "car", 0 },       [CC_SMART] = { "Smart Wi-Fi", "smart", 0 },
                    [CC_EQ] = { "EQ", "eq", 0 },                 [CC_BRIGHT] = { "Brightness", "bright", 0 },
                    [CC_VOLUME] = { "Volume", "vol", 0 },        [CC_USB] = { "USB", "usb", "usbpick_page" } };

static struct {
    int want[CC_ROWS]; /* a switch flipped or a level set here: shown until demo says so too; -1 none */
    uint64_t set_at[CC_ROWS]; /* a level's last turn */
    char value[CC_ROWS][24];
} cc;

static int told_on(int i) {
    char v[8];
    return status_told_value(rows[i].key, v, sizeof v) && atoi(v) == 1;
}
static int told_level(int i) {
    char v[8];
    int n = status_told_value(rows[i].key, v, sizeof v) ? atoi(v) : 0;
    return n < 0 ? 0 : n > 100 ? 100 : n;
}
static int level(int i) {
    if (cc.want[i] >= 0 && (told_level(i) == cc.want[i] || ui_time() - cc.set_at[i] >= LEVEL_HOLD_US)) cc.want[i] = -1;
    return cc.want[i] >= 0 ? cc.want[i] : told_level(i);
}
static int count(void *ctx) {
    (void)ctx;
    return CC_ROWS;
}
static void row(void *ctx, int i, ui_row *out) {
    (void)ctx;
    out->title = rows[i].title;
    if (i == CC_BRIGHT || i == CC_VOLUME) {
        int v = level(i);
        snprintf(cc.value[i], sizeof cc.value[i], i == CC_BRIGHT ? "%d%%" : "%d", v);
        out->flags = ROW_SLIDER, out->level = (float)v / 100, out->value = cc.value[i];
        return;
    }
    if (rows[i].demo) {
        out->flags = ROW_CHEVRON;
        if (i == CC_BT) snprintf(cc.value[i], sizeof cc.value[i], "%s", told_on(i) ? "On" : "Off");
        else status_told_value(rows[i].key, cc.value[i], sizeof cc.value[i]);
        out->value = cc.value[i];
        return;
    }
    if (cc.want[i] >= 0 && told_on(i) == cc.want[i]) cc.want[i] = -1; /* demo has caught up */
    int on = cc.want[i] >= 0 ? cc.want[i] : told_on(i);
    out->flags = ROW_SWITCH | (on ? ROW_ON : 0);
}
static void chosen(void *ctx, int i) {
    (void)ctx;
    if (rows[i].demo) {
        ui_demo_page(rows[i].demo);
        return;
    }
    int on = !(cc.want[i] >= 0 ? cc.want[i] : told_on(i));
    if (player_set(rows[i].key, on)) return; /* not sent (no pipe): the row stays as demo has it */
    cc.want[i] = on;
    list_rows_changed(); /* the row asked again: its switch is in what it gave */
    ui_mark_all();
}

/* a grabbed slider turned: its level moved by steps, sent, and shown at once */
static void adjust(void *ctx, int i, int steps) {
    (void)ctx;
    int from = level(i), to = from + steps, top = 100;
    char v[8];
    if (i == CC_VOLUME && status_told_value("volmax", v, sizeof v)) top = atoi(v);
    to = to < 0 ? 0 : to > top ? top : to;
    if (to == from) return;
    char verb[24];
    snprintf(verb, sizeof verb, "volume %d", to);
    if (i == CC_BRIGHT ? player_set("bright", to) : player_send(verb)) return; /* not sent: as demo has it */
    cc.want[i] = to, cc.set_at[i] = ui_time();
    list_rows_changed();
}

void control_push(void) {
    for (int i = 0; i < CC_ROWS; ++i) cc.want[i] = -1;
    ui_source src = { .count = count, .row = row, .chosen = chosen, .adjust = adjust, .name = "control" };
    ui_push(&list_page, &src);
}
