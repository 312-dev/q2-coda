/* Settings > Wi-Fi, q2ui's own (docs/ui-rewrite.md, step 4): the switch, standing as demo says
 * (wifi= in its status file, patch/control_center.c q2ui_cc_state) and set through demo down
 * q2ui's own pipe ("set wifi 0|1", q2ui_cc_set: stock's wifi_switch, as demo's own switch row and
 * Control Center call it). Off, the switch is the page, as on demo's; on, Networks under it opens
 * demo's Wi-Fi page through the gate, where a network is searched for, joined and forgotten.
 * ponytail: the networks are demo's page's alone: q2ui listing them needs demo to tell its scan
 * (wifi_showlist) and a join by name, and a password entry of q2ui's. */
#include <stdlib.h>

#include "q2ui.h"

static int want = -1; /* a flip made here, until demo has it */
static int on(void) {
    char v[8];
    int now = status_told_value("wifi", v, sizeof v) && atoi(v) == 1;
    if (want >= 0 && want == now) want = -1;
    return want >= 0 ? want : now;
}
static int count(void *ctx) {
    (void)ctx;
    return on() ? 2 : 1;
}
static void row(void *ctx, int i, ui_row *out) {
    (void)ctx;
    if (i == 0)
        out->title = "Wi-Fi", out->flags = ROW_SWITCH | (on() ? ROW_ON : 0);
    else
        out->title = "Networks", out->flags = ROW_CHEVRON;
}
static void chosen(void *ctx, int i) {
    (void)ctx;
    if (i) {
        ui_demo_page("systemset/wifi_page");
        return;
    }
    int next = !on();
    if (player_set("wifi", next)) return;
    want = next;
    list_rows_changed();
    ui_mark_all();
}
void wifi_push(void) {
    want = -1;
    ui_source src = { .count = count, .row = row, .chosen = chosen, .name = "wifi" };
    ui_push(&list_page, &src);
}
