/* Settings > Wi-Fi, q2ui's own (docs/ui-rewrite.md, step 4): the switch, standing as demo says
 * (wifi= in its status file, patch/control_center.c q2ui_cc_state) and set through demo down
 * q2ui's own pipe ("set wifi 0|1", q2ui_cc_set: stock's wifi_switch, as demo's own switch row and
 * Control Center call it). Off, the switch is the page, as on demo's. On, the networks demo's
 * search found, as it lists them in their own file (--networks, patch/wifi_flows.c
 * wifi_flows_networks: a line each, "LEVEL LOCK STATE NAME", the one joining, joined or failed
 * first), each with its signal and, when it asks for a password, a lock at the right column and its
 * join's state after its name; Centre joins it by its name ("join NAME" down the pipe), demo
 * opening its own password entry when one is needed, for which q2ui steps aside and comes back
 * here (patch/q2ui_gate.c). The page asks demo to search as it opens and as Wi-Fi comes on
 * ("scan"), and Search Again asks again. Its last row opens demo's Wi-Fi page for the rest (Add
 * Network, Forget, Reset Network Settings). */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "q2ui.h"

#define NETS_MAX 40 /* demo's file holds what fits 2 KB (patch/q2ui_gate.c Q2UI_WIFI_ROOM) */

const char *wifi_networks_file = "/tmp/q2ui.wifi";

static struct net {
    char ssid[33];
    int level, lock, state;
} nets[NETS_MAX];
static int n_nets, scanning, want = -1; /* want: a flip made here, until demo has it */
static int was_on = -1;                 /* the switch as last seen, to search as Wi-Fi comes on */

static int on(void) {
    char v[8];
    int now = status_told_value("wifi", v, sizeof v) && atoi(v) == 1;
    if (want >= 0 && want == now) want = -1;
    return want >= 0 ? want : now;
}

/* demo's file read again: its "scan N" line, then a network a line (the name's \\ and \n undone) */
static void read_networks(void) {
    char line[160];
    FILE *f = fopen(wifi_networks_file, "r");
    n_nets = scanning = 0;
    while (f && fgets(line, sizeof line, f)) {
        int level, lock, state, at = 0;
        if (sscanf(line, "scan %d", &scanning) == 1) continue;
        if (n_nets == NETS_MAX || sscanf(line, "%d %d %d %n", &level, &lock, &state, &at) < 3 ||
            !at)
            continue;
        struct net *w = &nets[n_nets];
        unsigned k = 0;
        for (const char *s = line + at; *s && *s != '\n' && k < sizeof w->ssid - 1; ++s)
            w->ssid[k++] = *s == '\\' && s[1] ? (*++s == 'n' ? '\n' : *s) : *s;
        w->ssid[k] = 0;
        w->level = level, w->lock = lock, w->state = state;
        ++n_nets;
    }
    if (f) fclose(f);
}

/* The rows while on: the switch, the "Networks" header, the networks (or one grey row saying why
 * there are none), Search Again, then demo's page. */
enum { ROW_FIRST_NET = 2 };
static int shown(void) { return n_nets ? n_nets : 1; }
static int count(void *ctx) {
    (void)ctx;
    return on() ? ROW_FIRST_NET + shown() + 2 : 1;
}
static void row(void *ctx, int i, ui_row *out) {
    (void)ctx;
    static const char *const states[] = { 0, "Connecting\xe2\x80\xa6", "Connected",
                                          "Couldn't connect", "Wrong password" };
    static const char *const bars[] = { "wifi_1", "wifi_1", "wifi_2",
                                        "wifi_3" }; /* levels 1-4, as demo's wifi_mark */
    int net = i - ROW_FIRST_NET;
    if (i == 0)
        out->title = "Wi-Fi", out->flags = ROW_SWITCH | (on() ? ROW_ON : 0);
    else if (i == 1)
        out->title = "Networks", out->flags = ROW_HEADER;
    else if (net < shown() && !n_nets)
        out->title = scanning ? "Searching\xe2\x80\xa6" : "No Networks", out->flags = ROW_GREY;
    else if (net < n_nets) {
        const struct net *w = &nets[net];
        out->title = w->ssid;
        out->detail = w->state > 0 && w->state < 5 ? states[w->state] : 0;
        out->marks[0] = bars[(w->level < 1 ? 1 : w->level > 4 ? 4 : w->level) - 1];
        out->marks[1] = w->lock ? "lock" : 0;
    } else if (net == shown())
        out->title = "Search Again";
    else
        out->title = "More Wi-Fi Settings", out->flags = ROW_CHEVRON;
}
static void chosen(void *ctx, int i) {
    (void)ctx;
    int net = i - ROW_FIRST_NET;
    if (i == 0) {
        int next = !on();
        if (player_set("wifi", next)) return;
        want = next;
        list_rows_changed();
        ui_mark_all();
    } else if (net >= 0 && net < n_nets)
        player_wifi("join", nets[net].ssid);
    else if (net == shown())
        player_wifi("scan", 0);
    else if (net == shown() + 1)
        ui_demo_page("systemset/wifi_page");
}

/* now.c's watch: demo's networks changed. The selection stays on the network it was on. */
void wifi_networks_changed(void) {
    char kept[33] = "";
    int top = !strcmp(ui_top_name(), "wifi"), sel = top ? list_last_selected() - ROW_FIRST_NET : -1;
    if (sel >= 0 && sel < n_nets) snprintf(kept, sizeof kept, "%s", nets[sel].ssid);
    read_networks();
    for (int k = 0; *kept && k < n_nets; ++k)
        if (!strcmp(nets[k].ssid, kept)) ui_select_top(k + ROW_FIRST_NET);
    list_rows_changed();
    ui_mark_all();
}

/* The page's step: a search asked for as Wi-Fi comes on (the page opened on, or switched on). */
static void step(void) {
    int now = on();
    if (now && was_on != 1) player_wifi("scan", 0);
    was_on = now;
}

static void wifi_step(void *st) {
    step();
    list_page.step(st);
}
static ui_page wifi_page;
void wifi_push(void) {
    want = -1, was_on = -1;
    read_networks();
    if (!wifi_page.step) wifi_page = list_page, wifi_page.name = "wifi", wifi_page.step = wifi_step;
    ui_source src = { .count = count, .row = row, .chosen = chosen, .name = "wifi" };
    ui_push(&wifi_page, &src);
}
