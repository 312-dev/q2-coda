/* Settings > Bluetooth, q2ui's own (docs/ui-rewrite.md, step 4): the switch, standing as demo says
 * (bton= in its status file, patch/control_center.c q2ui_cc_state) and set through demo down
 * q2ui's own pipe ("set bt 0|1", q2ui_cc_set: stock's switchBluetooth, as demo's own switch row
 * calls it; one while the stack is starting demo keeps and does once it is up, the row showing
 * the flip till then). Off, the switch is the page, as on demo's. On, the devices demo lists in
 * their own file (--bt-devices, patch/bt_ui.c bt_q2ui_devices: a line each, "PAIRED STATE MAC
 * NAME"), as demo's page has them: My Devices, the paired ones with their state after the name,
 * each opening its own page (Connect or Disconnect, Forget This Device); Other Devices, the ones a
 * scan heard, Centre pairing and connecting one ("btlink MAC" down the pipe, stock's own row click
 * in demo). The page asks demo to scan as it opens and as Bluetooth comes on ("btscan"). Then
 * Quality and Bluetooth Headset Volume Control, which open demo's pages, and More Bluetooth
 * Settings, demo's page itself (the name, Resume on Connect, Link Volume, Rename). */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "q2ui.h"

#define DEVS_MAX 32 /* demo's file holds what fits 2 KB (patch/q2ui_gate.c Q2UI_WIFI_ROOM) */

const char *bt_devices_file = "/tmp/q2ui.bt";

static struct dev {
    char mac[18], name[65];
    int paired, state;
} devs[DEVS_MAX];
static int n_devs, n_paired, scanning, want = -1; /* want: a flip made here, until demo has it */
static int was_on = -1; /* the switch as last seen, to scan as Bluetooth comes on */
static char picked[18]; /* the device whose own page is open */

static int on(void) {
    char v[8];
    int now = status_told_value("bton", v, sizeof v) && atoi(v) == 1;
    if (want >= 0 && want == now) want = -1;
    return want >= 0 ? want : now;
}

/* demo's file read again: its "scan N" line, then a device a line, the paired ones first (the
 * name's \\ and \n undone) */
static void read_devices(void) {
    char line[200];
    FILE *f = fopen(bt_devices_file, "r");
    n_devs = n_paired = scanning = 0;
    while (f && fgets(line, sizeof line, f)) {
        int paired, state, at = 0;
        struct dev *d = &devs[n_devs];
        if (sscanf(line, "scan %d", &scanning) == 1) continue;
        if (n_devs == DEVS_MAX || sscanf(line, "%d %d %17s %n", &paired, &state, d->mac, &at) < 3 ||
            !at)
            continue;
        unsigned k = 0;
        for (const char *s = line + at; *s && *s != '\n' && k < sizeof d->name - 1; ++s)
            d->name[k++] = *s == '\\' && s[1] ? (*++s == 'n' ? '\n' : *s) : *s;
        d->name[k] = 0;
        d->paired = paired, d->state = state;
        n_paired += paired != 0;
        ++n_devs;
    }
    if (f) fclose(f);
}

/* The rows while on: the switch; My Devices and the paired ones; Other Devices and the found ones
 * (or one grey row saying why there are none); Quality, Headset Volume and demo's page. */
enum { R_SWITCH, R_MINE };
static int others(void) { return n_devs - n_paired ? n_devs - n_paired : 1; }
static int other_head(void) { return R_MINE + 1 + n_paired; }
static int tail(void) { return other_head() + 1 + others(); }
static const char *const tails[] = { "Quality", "Bluetooth Headset Volume Control",
                                     "More Bluetooth Settings" };
static const char *const tail_pages[] = { "systemset/btquality_page", "systemset/btadjustvol_page",
                                          "systemset/bluetooth_page" };
static int count(void *ctx) {
    (void)ctx;
    return on() ? tail() + 3 : 1;
}
static void row(void *ctx, int i, ui_row *out) {
    (void)ctx;
    static const char *const states[] = { "Not Connected", "Connecting\xe2\x80\xa6", "Connected" };
    int other = i - other_head() - 1;
    if (i == R_SWITCH)
        out->title = "Bluetooth", out->flags = ROW_SWITCH | (on() ? ROW_ON : 0);
    else if (i == R_MINE)
        out->title = "My Devices", out->flags = ROW_HEADER;
    else if (i < other_head()) {
        const struct dev *d = &devs[i - R_MINE - 1];
        out->title = d->name, out->value = states[d->state >= 0 && d->state <= 2 ? d->state : 0];
        out->flags = ROW_CHEVRON;
    } else if (i == other_head())
        out->title = "Other Devices", out->flags = ROW_HEADER;
    else if (other < others() && n_devs == n_paired)
        out->title = scanning ? "Searching\xe2\x80\xa6" : "No Devices", out->flags = ROW_GREY;
    else if (other < others()) {
        const struct dev *d = &devs[n_paired + other];
        out->title = d->name, out->value = d->state == 1 ? states[1] : 0;
    } else
        out->title = tails[i - tail()], out->flags = ROW_CHEVRON;
}

/* A paired device's own page: Connect or Disconnect, which acts and comes back, then Forget This
 * Device, which asks first on the confirmation card (as demo's bt_detail.c forget_ask: destructive, on
 * Cancel) and forgets and comes back only at Forget Device. */
static const struct dev *picked_dev(void) {
    for (int k = 0; k < n_devs; ++k)
        if (!strcmp(devs[k].mac, picked)) return &devs[k];
    return 0;
}
static int dev_count(void *ctx) {
    (void)ctx;
    return picked_dev() ? 2 : 0;
}
static void dev_row(void *ctx, int i, ui_row *out) {
    (void)ctx;
    const struct dev *d = picked_dev();
    out->title = i ? "Forget This Device" : d && d->state ? "Disconnect" : "Connect";
}
static char forgetting[18]; /* the device the card asks about: the page may have moved on by the answer */
static void forget_answered(int n) {
    if (n != 2) return; /* Cancel, Return, the screen off or the timeout: nothing is forgotten */
    player_line("btforget", forgetting);
    if (!strcmp(picked, forgetting)) ui_pop();
}
static void dev_chosen(void *ctx, int i) {
    (void)ctx;
    if (!i) {
        player_line("btlink", picked);
        ui_pop();
        return;
    }
    static const char *const rows[] = { "Cancel", "Forget Device" };
    const struct dev *d = picked_dev();
    char title[48];
    snprintf(title, sizeof title, "Forget %.30s?", d && *d->name ? d->name : "This Device");
    snprintf(forgetting, sizeof forgetting, "%s", picked);
    notice_ask_local(title, "You'll need to pair again to use it.", rows, 2, 0, 1, forget_answered);
}

static void chosen(void *ctx, int i) {
    (void)ctx;
    int other = i - other_head() - 1;
    if (i == R_SWITCH) {
        int next = !on();
        if (player_set("bt", next)) return;
        want = next;
        list_rows_changed();
        ui_mark_all();
    } else if (i > R_MINE && i < other_head()) {
        snprintf(picked, sizeof picked, "%s", devs[i - R_MINE - 1].mac);
        ui_source src = { .count = dev_count, .row = dev_row, .chosen = dev_chosen };
        ui_push(&list_page, &src);
    } else if (other >= 0 && other < others() && n_devs > n_paired)
        player_line("btlink", devs[n_paired + other].mac);
    else if (i >= tail())
        ui_demo_page(tail_pages[i - tail()]);
}

/* The row a device is on, -1 for none. */
static int row_of(const char *mac) {
    for (int k = 0; k < n_devs; ++k)
        if (!strcmp(devs[k].mac, mac))
            return k < n_paired ? R_MINE + 1 + k : other_head() + 1 + (k - n_paired);
    return -1;
}
/* now.c's watch: demo's devices changed. The selection stays on the device it was on. */
void bt_devices_changed(void) {
    char kept[18] = "";
    int sel = !strcmp(ui_top_name(), "bluetooth") ? list_last_selected() : -1;
    for (int k = 0; sel >= 0 && k < n_devs; ++k)
        if (row_of(devs[k].mac) == sel) snprintf(kept, sizeof kept, "%s", devs[k].mac);
    read_devices();
    if (*kept && row_of(kept) >= 0) ui_select_top(row_of(kept));
    list_rows_changed();
    ui_mark_all();
}

/* The page's step: a scan asked for as Bluetooth comes on (the page opened on, or switched on). */
static void bt_step(void *st) {
    int now = on();
    if (now && was_on != 1) player_line("btscan", 0);
    was_on = now;
    list_page.step(st);
}
static ui_page bt_page;
void bluetooth_push(void) {
    want = -1, was_on = -1;
    read_devices();
    if (!bt_page.step) bt_page = list_page, bt_page.name = "bluetooth", bt_page.step = bt_step;
    ui_source src = { .count = count, .row = row, .chosen = chosen, .name = "bluetooth" };
    ui_push(&bt_page, &src);
}
