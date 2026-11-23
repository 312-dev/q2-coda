/* Settings > Wi-Fi's flows past stock's (docs/internals.md#wi-fi-page-flows): a failed join asks
 * for the password again with why; a held saved network offers Forget; Reset Network Settings at
 * the page's foot; Search Again shows its search at once; and the switch (and Control Center's Wi-Fi
 * circle) or the network's own row ends a join that is still trying, where stock's click handlers ignore both until it gives up. */
#include "navigation.h"

/* g_wifiStatus at 0xd8: wpa_supplicant's wpa_state as stock's monitor thread last read its STATUS,
 * once a second ("COMPLETED" and "ASSOCIATED" are its link, networkGetPhysicStatus) */
#define WPA_STATE ((const char *)g_wifiStatus + 0xd8)
#define WIFI_CONF "/mnt/data/wpa_supplicant.conf"
#define WIFI_CONF_DEFAULT "/release/assets/default/raw/wpa_supplicant.conf" /* no network in it */
#define WIFI_PAGE "wifi_page"
#define WIFI_VIEW "scroll_view_wifi"
#define SSID_MAX 33 /* 32 bytes and its end */

/* ssid: the network a join is trying (stock's wifi_name while wifi_connectstatus is 1); shook: that
 * join reached the handshake that checks its password; linked: it got past it (COMPLETED); ask: it
 * failed and a timer asks for the password again (ask_again), wrong: as a wrong password; tries:
 * the timer passes left to find the entry's box (reason_tip); forget: the network the Forget card
 * asks about. */
static struct {
    char ssid[SSID_MAX], forget[SSID_MAX];
    int shook, linked, ask, wrong, tries;
    const char *why; /* the reason the password entry shows */
} wf __attribute__((section(".scratch")));

static const char *name_of(void *w) { return w ? widget_get_prop_str(w, "name", "") : ""; }

/* The Wi-Fi page when it is the page in use, else 0. */
static void *wifi_page(void) {
    void *w = page_in_use();
    return w && !tk_strcmp(name_of(w), WIFI_PAGE) ? w : (void *)0;
}

/* The scan record at, 0 past the list: name at 0, flags at 4 (wifi_showlist). */
static void *record(int at) {
    void *list = P(wifi_showlist, 0);
    return list && at >= 0 && (unsigned)at < deque_size(list) ? deque_at(list, (unsigned)at) : 0;
}
static const char *ssid_of(int at) {
    void *r = record(at);
    return r ? (const char *)P(r, 0) : (const char *)0;
}
/* Where network ssid is in the scan's list now, -1 when it is not. */
static int find(const char *ssid) {
    const char *s;
    for (int at = 0; (s = ssid_of(at)); ++at)
        if (!tk_strcmp(s, ssid)) return at;
    return -1;
}
/* Whether the record asks for a password: its flags name WPA or WEP, as wifi_rows.c's lock. */
static int secured(int at) {
    void *r = record(at);
    const char *flags = r ? (const char *)P(r, 4) : (const char *)0;
    return flags && (strstr(flags, "WPA") || strstr(flags, "WEP"));
}

static int connecting(void) { return wifi_connectstatus == 1; }

/* A join ended where it stands: stock's own end of one that timed out (network_checkstatus,
 * 0x518e84), without the "connect failed" toast its caller then sets. */
static void cancel(void) {
    networkDisconnect();
    ((char *)wifi_name)[0] = 0;
    wifi_connectstatus = 0;
    wf.ssid[0] = 0;
    wf.ask = 0;
    wifi_rows_end(); /* nothing is first under Networks (wifi_rows.c) */
}

/* Ends the join that is trying, if one is (wifi_switch's guard: Wi-Fi on, a join going, stock's init
 * finished), so a switch click after it turns Wi-Fi off. Shared with Control Center's Wi-Fi circle
 * (control_center.c cc_act). 1 when a join ended. */
int wifi_flows_cancel_join(void) {
    if (!g_wififlag || !connecting() || wifi_initstatus) return 0;
    cancel();
    return 1;
}

/* systemset_wifi_page's switch and Search Again rows (one click handler, 0x4d61e0, by the row's
 * name: 0 the switch, 1 Search Again). Stock ignores both while a join is trying
 * (wifi_connectstatus 1, for up to a minute: network_checkstatus's two 30-tick tries). The switch
 * ends the join and turns Wi-Fi off now (wifi_switch's off clears what is left and stops
 * wpa_supplicant); Search Again says why it waits. A search started shows "Searching..." now: stock
 * sets it going (wifi_scan) but rebuilds the list, the only place the line is written, only once it
 * ends. */
int ringnav_wifi_switch_row(void *button, void *event) {
    int row = atoi(name_of(button));
    if (row == 0) wifi_flows_cancel_join();
    if (row == 1 && g_wififlag && connecting() && !wifi_initstatus) {
        toast("Connecting to Wi-Fi\xe2\x80\xa6");
        return 0;
    }
    int r = stock_wifi_switch_row_trampoline(button, event);
    if (row == 1 && wifi_searchtime) wifi_rows_scanned(); /* a failed network's mark goes */
    if (row == 1) wifi_busy_show(window_of(button));
    return r;
}

/* A network's row (0x4d62d8, its button named by its scan record): stock ignores it while a join is
 * trying; the row of the network that is joining ends the join instead, and the row shows it idle
 * again. Any other row stays stock's. */
int ringnav_wifi_network_row(void *button, void *event) {
    const char *ssid = ssid_of(atoi(name_of(button)));
    if (connecting() && !wifi_initstatus && ssid && !tk_strcmp(ssid, (const char *)wifi_name)) {
        void *win = window_of(button);
        cancel();
        ringnav_wifi_rows(win); /* as stock's click rebuilds after its actions */
        return 0;
    }
    if (ssid) wifi_rows_follow(ssid); /* the selection goes with it to the top (wifi_rows.c) */
    return stock_wifi_network_row_trampoline(button, event);
}

/* network_checkstatus, stock's 500 ms Wi-Fi poll (on_wm_timer_networkstatus): 2 when a join timed
 * out, which its caller turns into a toast and nothing else, the password it tried still saved, so
 * the next tap tries it again unasked. While a join tries, its wpa_state is watched: one that
 * reached 4WAY_HANDSHAKE (or GROUP_HANDSHAKE) and never COMPLETED was refused its key, as
 * wpa_supplicant itself judges (events.c could_be_psk_mismatch: the AP drops a station in the
 * handshake); one that never got there did not reach the network (out of range, refused, no
 * address). A failed join of a network that asks for a password, on the Wi-Fi page, comes back as
 * 1 (a change: the page rebuilds) and a timer pass after asks for the password (ask_again).
 * ponytail: the state is sampled every 500 ms against a 1 s monitor, and a handshake fails over
 * about four seconds of retries, so a join that reached it is seen; one that failed faster than a
 * second would read as "Couldn't connect". */
static int ask_again(const void *unused);
int ringnav_wifi_checkstatus(void) {
    if (connecting()) {
        if (tk_strcmp(wf.ssid, (const char *)wifi_name)) {
            tk_snprintf(wf.ssid, sizeof wf.ssid, "%s", (const char *)wifi_name);
            wf.shook = wf.linked = 0;
        }
        if (!tk_strcmp(WPA_STATE, "4WAY_HANDSHAKE") || !tk_strcmp(WPA_STATE, "GROUP_HANDSHAKE"))
            wf.shook = 1;
        if (!tk_strcmp(WPA_STATE, "COMPLETED")) wf.linked = 1;
    }
    int r = stock_wifi_checkstatus_trampoline();
    if (r != 2 || !wf.ssid[0]) {
        if (!connecting()) wf.ssid[0] = 0;
        return r;
    }
    /* first under Networks with its mark until the next join or scan (wifi_rows.c) */
    wifi_rows_failed(wf.ssid, wf.shook && !wf.linked);
    int at = find(wf.ssid);
    if (!wifi_page() || at < 0 || !secured(at)) {
        wf.ssid[0] = 0;
        return r;
    }
    wf.wrong = wf.shook && !wf.linked;
    if (!wf.ask) wf.ask = timer_add(ask_again, 0, 50) != 0;
    return wf.ask ? 1 : r;
}

/* The password entry's empty box (edit_passwd) reads why the join failed, in place of stock's
 * "Please enter password", from the first timer pass once the entry is up (wf.tries passes at
 * most); the keyboard's own window may be over it. A toast would sit under the keyboard. */
static int reason_tip(const void *unused) {
    void *dialog = widget_lookup(window_manager(), "kbwifipass_dialog", 0),
         *edit = dialog ? widget_lookup(dialog, "edit_passwd", 1) : (void *)0;
    if (edit) widget_set_prop_str(edit, "tips", wf.why);
    return !edit && --wf.tries > 0 ? 8 : 0; /* RET_REPEAT, RET_REMOVE */
}

/* A failed join's network asked for again, by stock's own password entry (kbwifipass_dialog, as a
 * tap on a network it has no password for opens it), its box reading why the join failed
 * (reason_tip). From a timer, as stock's own dialogs open from on_wm_timer_dialog: the entry runs
 * its own loop, and one opened from ringnav_sleep's polls took no input (the emulator, 2026-11-17).
 * A password entered replaces the saved one (stock's Forget, then its join); Cancel, or an OK with
 * no password fit for the network, keeps it. */
static int ask_again(const void *unused) {
    if (!wf.ask) return 0; /* the join was ended or the networks reset since */
    wf.why = wf.wrong ? "Wrong password" : "Couldn't connect";
    void *page = wifi_page();
    if (page && (window_manager_is_animating(window_manager()) || confirm_up()))
        return 8; /* RET_REPEAT */
    wf.ask = 0;
    char ssid[SSID_MAX];
    tk_snprintf(ssid, sizeof ssid, "%s", wf.ssid);
    wf.ssid[0] = 0;
    if (!page || find(ssid) < 0) {
        toast(wf.why);
        return 0;
    }
    wf.tries = 20; /* a second at 50 ms */
    timer_add(reason_tip, 0, 50);
    memset((char *)prepare_linkpasswd, 0, 128);
    tk_snprintf((char *)prepare_linkssid, 256, "%s", ssid); /* check_wifinextaction's */
    if (navigator_to("dialog/kbwifipass_dialog")) return 0;
    int at = find(ssid);
    void *r = record(at);
    /* the entry's OK with no fit password (Return can give it one, empty) changes nothing, as
     * wifi_action_connect would refuse it: the saved one stays */
    if (!r || !networkCheckSsidPassword((const char *)P(r, 4), (const char *)prepare_linkpasswd))
        return 0;
    if (networkCheckConnectedSsid(ssid)) wifi_action_deleterecord(at);
    wifi_rows_follow(ssid);
    wifi_action_connect(at);
    g_wifisetting_flag = 1; /* the page's timer rebuilds: the row reads Connecting */
    return 0;
}

static void forget_answer(void *ctx, int row, int toggle_on) {
    (void)ctx, (void)toggle_on;
    int at = row == 1 ? find(wf.forget) : -1;
    if (at < 0) return;
    if (connecting() && !tk_strcmp(wf.forget, (const char *)wifi_name)) cancel();
    wifi_rows_end();              /* the joined network, or one that failed, first no more */
    wifi_action_deleterecord(at); /* stock's Forget: disconnects the joined one first */
    g_wifisetting_flag = 1;
    toast("Network Forgotten");
}

/* ringnav_keylong: Play/Pause held on a saved network's row asks to forget it (the confirmation
 * card); 1 when it was one. Stock's own Forget is a tap on a saved row's right end, which the wheel
 * cannot reach. */
int wifi_flows_hold(void *top) {
    if (tk_strcmp(name_of(top), WIFI_PAGE)) return 0;
    void *view = widget_lookup(top, WIFI_VIEW, 1);
    /* the selection's id counts the list's tap targets (navigation.c load_rows) */
    void *b = view ? wifi_tap_button(view, widget_get_prop_int(view, SEL, -1)) : (void *)0;
    int network = 0;
    for (unsigned i = 0, n = b ? widget_count_children(b) : 0; i < n; ++i)
        network |= wifi_sig(widget_get_prop_str(widget_get_child(b, i), "image", "")) > 0;
    const char *ssid = network ? ssid_of(atoi(name_of(b))) : (const char *)0;
    if (!ssid || !networkCheckConnectedSsid(ssid)) return 0;
    tk_snprintf(wf.forget, sizeof wf.forget, "%s", ssid);
    static const char *const rows[] = { "Cancel", "Forget Network" };
    confirm(&(confirm_t){ "Forget %s?", wf.forget, "You'll need its password to join again.", 0,
                          rows, 2, CONFIRM_DESTRUCTIVE, -1, 0, 0, 0, 0, forget_answer, 0, 0, 0, 0 });
    return 1;
}

/* Reset Network Settings: every saved network gone, as stock's Reset Settings leaves them
 * (init_usersetting) but without the reboot, so wpa_supplicant's file is put back to the shipped
 * one rather than removed (wifi_connect.sh starts no wpa_supplicant without a file, and the page
 * could not search). Wi-Fi on goes off first (wpa_supplicant stopped, any join ended) and back on
 * after, searching as the switch does. */
static void reset_answer(void *ctx, int row, int toggle_on) {
    (void)ctx, (void)toggle_on;
    if (row != 1) return;
    int on = g_wififlag;
    if (on) wifi_switch(1);
    system("cp -f " WIFI_CONF_DEFAULT " " WIFI_CONF);
    networkClearSsid();
    networkGetSsidList(WIFI_CONF);
    wf.ssid[0] = wf.ask = 0;
    wifi_rows_end();
    if (on) {
        wifi_switch(1);
        wifi_searchtime = 50; /* the switch row's own, a search as Wi-Fi comes on */
    }
    g_wifisetting_flag = 1;
    toast("Network Settings Reset");
}
static int reset_click(void *ctx, void *event) {
    (void)ctx, (void)event;
    static const char *const rows[] = { "Cancel", "Reset" };
    confirm(&(confirm_t){ "Reset Network Settings?", 0, "Forgets every saved Wi-Fi network.", 0,
                          rows, 2, CONFIRM_DESTRUCTIVE, -1, 0, 0, 0, 0, reset_answer, 0, 0, 0, 0 });
    return 0;
}

/* wifimenu_rows_build (0x4d5950), every rebuild of the Wi-Fi page's list: the selected network
 * noted (wifi_rows.c wifi_rows_keep), stock's rows, then Reset Network Settings at its foot, Wi-Fi
 * on or off. */
int ringnav_wifi_rows(void *win) {
    void *view = win ? widget_lookup(win, WIFI_VIEW, 1) : (void *)0;
    if (view) wifi_rows_keep(view); /* the selected network, its rows about to be made again */
    int r = stock_wifi_rows_trampoline(win);
    view = win ? widget_lookup(win, WIFI_VIEW, 1) : (void *)0;
    if (view) /* the label across the button, as stock's Add Network: no right column to stop at */
        widget_set_text_utf8(list_row_core(view, 0, reset_click, 0, 325, 0),
                             "Reset Network Settings");
    return r;
}
