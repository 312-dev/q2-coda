/* Control Center (docs/oled-design.md#control-center), q2ui's own while it has the screen: opened
 * by holding Return on any page (main.c), closed by Return. Its switches are demo's settings,
 * shown as demo says they stand (ui/status.c status_told_value, from patch/control_center.c
 * q2ui_cc_state) and flipped down q2ui's own pipe to demo ("set NAME 0|1", ui/player.c player_set,
 * control_center.c q2ui_cc_set); a row turns at once and keeps its new state until demo's word catches up, half a
 * second or so, as status.c has every row asked again when demo's word changes. Bluetooth, USB and the EQ's own page open demo's pages through the gate.
 * ponytail: a list, not demo's two-column sheet of circles, Now Playing and sliders; no
 * Brightness or Volume slider yet (the toolkit has no slider), no AirPlay. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "q2ui.h"

enum { CC_WIFI, CC_BT, CC_CAR, CC_SMART, CC_EQ, CC_USB, CC_ROWS };
static const struct {
    const char *title, *key, *demo; /* key: demo's word for a switch; demo: the page a chevron opens */
} rows[CC_ROWS] = { [CC_WIFI] = { "Wi-Fi", "wifi", 0 },          [CC_BT] = { "Bluetooth", "bton", "systemset/bluetooth_page" },
                    [CC_CAR] = { "Car Remote", "car", 0 },       [CC_SMART] = { "Smart Wi-Fi", "smart", 0 },
                    [CC_EQ] = { "EQ", "eq", 0 },                 [CC_USB] = { "USB", "usb", "usbpick_page" } };

static struct {
    int want[CC_ROWS]; /* a switch flipped here: its state until demo says so too; -1 none */
    char value[CC_ROWS][24];
} cc;

static int told_on(int i) {
    char v[8];
    return status_told_value(rows[i].key, v, sizeof v) && atoi(v) == 1;
}
static int count(void *ctx) {
    (void)ctx;
    return CC_ROWS;
}
static void row(void *ctx, int i, ui_row *out) {
    (void)ctx;
    out->title = rows[i].title;
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

void control_push(void) {
    for (int i = 0; i < CC_ROWS; ++i) cc.want[i] = -1;
    ui_source src = { .count = count, .row = row, .chosen = chosen, .name = "control" };
    ui_push(&list_page, &src);
}
