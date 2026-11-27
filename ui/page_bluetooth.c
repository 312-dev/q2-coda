/* Settings > Bluetooth, q2ui's own (docs/ui-rewrite.md, step 4): the switch, standing as demo says
 * (bton= in its status file, patch/control_center.c q2ui_cc_state) and set through demo down
 * q2ui's own pipe ("set bt 0|1", q2ui_cc_set: stock's switchBluetooth, as demo's own switch row
 * calls it; refused while the stack is starting, when the row keeps demo's word). Off, the switch
 * is the page, as on demo's; on, the rows under it open demo's pages through the gate: Devices its
 * Bluetooth page (pairing, connecting, the scan, the name), then Quality and Bluetooth Headset
 * Volume Control, the head rows of that page that open pages of their own.
 * ponytail: the devices are demo's page's alone: q2ui listing them needs demo to tell its paired
 * and found lists (bt_ui.c dev_at, bt_dev_state) and a connect by address. */
#include <stdlib.h>

#include "q2ui.h"

static const char *const rows[] = { "Bluetooth", "Devices", "Quality", "Bluetooth Headset Volume Control" };
static const char *const pages[] = { 0, "systemset/bluetooth_page", "systemset/btquality_page",
                                      "systemset/btadjustvol_page" };
static int want = -1; /* a flip made here, until demo has it */
static int on(void) {
    char v[8];
    int now = status_told_value("bton", v, sizeof v) && atoi(v) == 1;
    if (want >= 0 && want == now) want = -1;
    return want >= 0 ? want : now;
}
static int count(void *ctx) {
    (void)ctx;
    return on() ? (int)(sizeof rows / sizeof *rows) : 1;
}
static void row(void *ctx, int i, ui_row *out) {
    (void)ctx;
    out->title = rows[i];
    out->flags = i ? ROW_CHEVRON : ROW_SWITCH | (on() ? ROW_ON : 0);
}
static void chosen(void *ctx, int i) {
    (void)ctx;
    if (i) {
        ui_demo_page(pages[i]);
        return;
    }
    int next = !on();
    if (player_set("bt", next)) return;
    want = next;
    list_rows_changed();
    ui_mark_all();
}
void bluetooth_push(void) {
    want = -1;
    ui_source src = { .count = count, .row = row, .chosen = chosen, .name = "bluetooth" };
    ui_push(&list_page, &src);
}
