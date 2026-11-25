/* Smart Wi-Fi: the radio off while nothing uses the network, back for what needs it; the scan
 * hold, wpa_supplicant's rescans held off while A2DP streams and backed off while dark; and the
 * join watch, a log of a wpa_supplicant that does not join. */
#include "navigation.h"

/* Smart Wi-Fi (docs/internals.md#smart-wi-fi): with Power's row on and the user's Wi-Fi switch on,
 * the radio goes off once nothing has needed the network for SMART_IDLE_MS, and comes back as soon
 * as something does. Both go through stock's own switch, wifi_switch, as Quick Settings and the
 * Wi-Fi page call it: off clears its flags, stops SyncLink's server and runs networkStop
 * (network_checkstatus then sends ips=1), on runs networkStart's background wifi_connect.sh. Its
 * last act, the WIFI= write, is dropped for these (smart_config), so config.ini, a reboot and the
 * user's own switch stay stock's. off: Smart Wi-Fi turned the radio off; quiet: its own toggle
 * runs; busy_at: the last use; woke_at: its last wake, whose link smart_wifi_wait's callers wait
 * for; up: the link at the last poll; armed: the row was on then; hold_*: the action held for the
 * link (smart_hold), its argument, the page it was pressed on and that page's name, and the timer;
 * held: that action runs now, so it is never held again. */
static struct {
    unsigned at, busy_at, woke_at, hold_timer;
    int off, quiet, armed, up, held;
    int (*hold_fn)(void *, void *);
    void *hold_ctx, *hold_win;
    char hold_name[32];
} sw __attribute__((section(".scratch")));

/* Stock's own conditions for its switch: the network module up for Wi-Fi (NET_TYPE 2; networkStart
 * and networkStop do nothing otherwise), its first scan taken (wifi_initstatus 0, which Quick
 * Settings and the Wi-Fi page require) and no connect half done (wifi_connectstatus 1, the Wi-Fi
 * page's); never in test mode, while powering off or in the first-boot guide, where
 * on_wm_timer_networkstatus also leaves the network alone. */
static int smart_ready(void) {
    return *(volatile int *)NET_TYPE == 2 && !wifi_initstatus && wifi_connectstatus != 1 &&
           !g_testmode_flag && g_poweroff_state != 2 && (signed char)g_guideflag <= 0;
}

/* wifi_switch(1) for Smart Wi-Fi: the radio off when g_wififlag is set, else back on. */
static void smart_switch(unsigned now) {
    int wake = !g_wififlag;
    sw.quiet = 1;
    wifi_switch(1);
    sw.quiet = 0;
    sw.off = !g_wififlag;
    if (wake) sw.woke_at = now | !now; /* never 0; |1 would eat a millisecond when now is even */
    sw.busy_at = now;
}

/* Streaming's page and the Wi-Fi page: Smart Wi-Fi's radio back at once. */
void smart_wake(void) {
    if (sw.off && smart_ready()) smart_switch(time_now_ms());
}

/* write_int_config's hook (ringnav_write_config): wifi_switch's last act writes SYSSET WIFI. Smart
 * Wi-Fi's own toggles skip it (1). Any other is the user's (Quick Settings, the Wi-Fi page, test
 * mode): Smart Wi-Fi forgets it had the radio off and counts SMART_IDLE_MS from now (0). */
int smart_config(const char *section, const char *key) {
    if (tk_strcmp(section, "SYSSET") || tk_strcmp(key, "WIFI")) return 0;
    if (sw.quiet) return 1;
    sw.off = 0;
    sw.woke_at = 0;
    sw.busy_at = time_now_ms();
    return 0;
}

/* systemset_wifi_page_init: the Wi-Fi page builds its switch from g_wififlag, so the radio comes
 * back first and the page shows the user's setting. */
int ringnav_wifi_page(void *win, void *ctx) {
    smart_wake();
    return stock_wifi_page_trampoline(win, ctx);
}

/* A page that uses the network, open anywhere in the window stack: Streaming and its services'
 * pages (Spotify's, Internet Radio's, a card app's and its QR code, Tidal's but Tidal Downloads), Car
 * Remote's Pair a Phone (carremote_page.c: its QR code and Wi-Fi address),
 * System settings' Apps, AirPlay's, Network service, the Wi-Fi page, LocalSend's page
 * (localsend_ui.c), the Wi-Fi page's dialogs, the online lyrics and cover search, and Home's Radio
 * (radio_tuner.c), whose stations stream while it is open. */
static int net_page_open(void) {
    static const char *const pages[] = {
        "stream_page",     "spotify_page",     "spotbrowse_page",   "radio_page",
        "radionp_page",    "addon_page",       "qrcode_page",       "apps_page",
        "airplay_page",    "netservice_page",  "wifi_page",         "localsend_",
        "editwifi_dialog", "kbwifiadd_dialog", "kbwifipass_dialog", "onlinematching_page",
        "tidal_",          "tuner_page",       "carhowto_page"
    };
    void *wm = window_manager();
    for (unsigned i = 0, n = widget_count_children(wm); i < n; ++i) {
        const char *name = widget_get_prop_str(widget_get_child(wm, i), "name", "");
        if (!tk_strcmp(name, "tidal_downloads")) continue; /* the card's tracks */
        for (unsigned k = 0; k < sizeof pages / sizeof *pages; ++k)
            if (tk_str_start_with(name, pages[k])) return 1;
    }
    return 0;
}

#if Q2POD_DEV
/* --dev builds: q2remote (remote.c, docs/remote.md) reaches the Q2 over Wi-Fi alone, so it needs
 * the network for REMOTE_HOLD_MS after its last request and for demo's first REMOTE_BOOT_MS
 * (offsets.inc says why). Each authenticated request leaves its pid in REMOTE_USED (remote.c
 * mark_used); a text there unlike the last (seen) is a request, timed by demo's own clock (at), so
 * the two processes share none. start: demo's first poll; boot: its hold runs. A demo started later
 * takes a file left from before as a request. A log line as each hold starts and as it ends. */
#define REMOTE_USED "/tmp/q2remote.used"
static struct {
    unsigned start, at;
    int boot;
    char seen[16];
} rh __attribute__((section(".scratch")));
#define remote_say(what, ms)                                                                       \
    do {                                                                                           \
        char line_[96];                                                                            \
        tk_snprintf(line_, sizeof line_, what, (ms) / 60000u);                                     \
        ulog('I', "wifi", line_);                                                                  \
    } while (0)
static void remote_poll(unsigned now) {
    char raw[sizeof rh.seen + 2];
    if (!rh.start) {
        rh.start = now | !now, rh.boot = 1;
        remote_say("q2remote: Wi-Fi needed for %u min after boot", REMOTE_BOOT_MS);
    }
    if (rh.boot && now - rh.start >= REMOTE_BOOT_MS) {
        rh.boot = 0;
        remote_say("q2remote: the hold after boot ended, %u min on", REMOTE_BOOT_MS);
    }
    if (state_read(REMOTE_USED, raw, sizeof raw) && strcmp(raw + 1, rh.seen)) {
        if (!rh.at)
            remote_say("q2remote: a session needs Wi-Fi until %u min after its last request",
                       REMOTE_HOLD_MS);
        rh.at = now | !now;
        tk_snprintf(rh.seen, sizeof rh.seen, "%s", raw + 1);
    } else if (rh.at && now - rh.at >= REMOTE_HOLD_MS) {
        rh.at = 0;
        remote_say("q2remote: the session's hold ended, %u min after its last request",
                   REMOTE_HOLD_MS);
    }
}
static int remote_wants_wifi(void) { return rh.boot || rh.at; }
#else
#define remote_poll(now) ((void)(now))
#define remote_wants_wifi() 0
#endif

/* Something needs the network: a USB cable in; AirPlay or DLNA on (Network service); Car Remote
 * while a phone may come over this Wi-Fi or through q2-iroh: a link up and Car Remote used within
 * its last 10 minutes (coda_rc_shim.c coda_rc_wants_wifi; with no network joined its phone comes
 * over Bluetooth); SyncLink over Wi-Fi or a SyncLink session, LocalSend's daemon (a transfer runs
 * on past its page); a stock stream queue (Tidal's: the types on_wm_timer_networkstatus stops once
 * offline, whatever it is doing, as Play must fetch its URL at once), Spotify's librespot running
 * or waiting to start (it never retries a Connect login or mDNS that failed), a station playing, a
 * card app running, a scrobble upload, Network Update's download or an Apps job, Get Album Lyrics,
 * a Tidal download, a time zone lookup (autozone.c) or an action held for the link (smart_hold); a
 * time sync pending on a link; a page above; or, in a --dev build, q2remote (remote_wants_wifi). */
static int smart_needed(void) {
    unsigned type = (unsigned)mclGetPlayType();
    return g_usbdet_value || g_airplayflag || g_dlnaflag || coda_rc_wants_wifi() ||
           g_synclinkflag == 2 || g_syncLink_status || localsend_running() ||
           airplayGetFlag() == 2 || type - 2 < 2 || type - 0xf1 < 2 || spot_running() ||
           radio_status() == 2 || addon_running() || scrobble_busy() || netupdate_busy() ||
           album_lyrics_busy() || tidal_busy() || autozone_busy() || sw.hold_timer ||
           (g_syncnettime_flag && networkGetConnectStatus() > 0) || net_page_open() ||
           remote_wants_wifi();
}

/* Background scans held while A2DP streams (docs/internals.md#scan-hold): the RTL8733BS is one
 * radio for Wi-Fi and Bluetooth, and a scan's off-channel dwells are air time the headset's link
 * loses. The scan that runs on its own is wpa_supplicant's: with saved networks and none joined it
 * scans again scan_interval (5 s) after each scan that found none, for as long as the radio is on
 * (the shipped config has no bgscan or autoscan; demo scans only from the Wi-Fi page, its switch
 * and a join). So while music plays to a headset (or a phone streams to the Q2), Wi-Fi is on and
 * nothing needs the network, scan_interval is SCAN_HOLD_S; otherwise 5 s, or the dark backoff
 * below. smart_needed includes the Wi-Fi page and its dialogs, so the page scans as stock's does
 * whatever plays, and a change of scan_interval moves the rescan already due (scan.c's
 * wpa_supplicant_update_scan_int), so the hold and its end take effect at once. Nothing joined or
 * a join's own scans are touched.
 *
 * Dark backoff (docs/internals.md#scan-hold): with the screen off, nothing joined, no join trying,
 * no cable and no Wi-Fi page open, each rescan waits twice the last, 5 s up to SCAN_BACKOFF_MAX_S,
 * counted from when all of that began (since). Car Remote does not stop it, as it stops the hold:
 * its car's network is joined within SCAN_BACKOFF_MAX_S of appearing, or at once on a wake. Any
 * of them ending puts 5 s back. sent: the interval wpa_supplicant was last given; sock: its control
 * socket's inode then, so a new wpa_supplicant (the switch, Smart Wi-Fi's wake) or a new demo
 * beside an old one is told again; tried: the last failed try, retried after SCAN_RETRY_MS. */
#define WPA_SOCK "/var/run/wpa_supplicant/wlan0"
#define SCAN_HOLD_S 3600
#define SCAN_BACKOFF_MAX_S 120
#define SCAN_RETRY_MS 10000
static struct {
    unsigned sock, tried, since, up_at, said;
    int sent, assoc, linked;
} sh __attribute__((section(".scratch")));
static int scan_interval_s(unsigned now) {
    int a2dp = (media_playing() && mclGetOutputWay() == 1) || (signed char)bt__recv_pageflag == 2;
    if (a2dp && wifi_connectstatus != 1 && !smart_needed()) return SCAN_HOLD_S;
    if (g_backlight_status || networkGetConnectStatus() > 0 || wifi_connectstatus == 1 ||
        g_usbdet_value || net_page_open()) {
        sh.since = 0;
        return 5;
    }
    if (!sh.since) sh.since = now | !now;
    unsigned s = 5, end = 5, t = (now - sh.since) / 1000;
    while (t >= end && s < SCAN_BACKOFF_MAX_S) {
        s = s * 2 < SCAN_BACKOFF_MAX_S ? s * 2 : SCAN_BACKOFF_MAX_S;
        end += s;
    }
    return (int)s;
}
/* The join watch (docs/internals.md#scan-hold): what a wpa_supplicant that never joins was doing,
 * in the log. From its control socket's appearance (up_at), the association (wlan0's operstate) and
 * the link (networkGetConnectStatus, a lease) are said as they change, with the seconds since;
 * unassociated, JOIN_FIRST_MS on and every JOIN_EVERY_MS after, one line says its wpa_state, the
 * networks its last scan saw (a count, no names), the rescan interval it was given and why, and
 * stock's switch flags (g_wififlag, wifi_initstatus, wifi_connectstatus). */
#define JOIN_FIRST_MS 30000
#define JOIN_EVERY_MS 60000
#define WPA_OUT "/tmp/q2-wpa-status"
static void join_watch(unsigned now, int want) {
    char raw[700], line[200];
    if (!sh.up_at) {
        sh.up_at = now | !now, sh.assoc = sh.linked = 0;
        sh.said = now - (JOIN_EVERY_MS - JOIN_FIRST_MS);
    }
    unsigned s = (now - sh.up_at) / 1000;
    state_read("/sys/class/net/wlan0/operstate", raw, 16);
    int assoc = !strncmp(raw + 1, "up", 2), linked = networkGetConnectStatus() > 0;
    if (assoc != sh.assoc || linked != sh.linked) {
        tk_snprintf(line, sizeof line, "%s, %u s after wpa_supplicant started",
                    linked != sh.linked ? (linked ? "link up (a lease)" : "link down")
                                        : (assoc ? "associated" : "association lost"),
                    s);
        ulog('I', "wifi", line);
        sh.assoc = assoc, sh.linked = linked;
    }
    if (assoc || now - sh.said < JOIN_EVERY_MS) return;
    sh.said = now;
    system("wpa_cli -i wlan0 status >" WPA_OUT " 2>/dev/null; wpa_cli -i wlan0 scan_results | wc -l >>" WPA_OUT);
    unsigned n = state_read(WPA_OUT, raw, sizeof raw);
    unlink(WPA_OUT);
    int seen = -1; /* scan_results' lines, its header the first */
    if (n) {
        char *c = raw + n + 1; /* the count, the output's last line */
        while (c > raw + 1 && c[-1] == '\n') *--c = 0;
        while (c > raw && c[-1] >= '0' && c[-1] <= '9') --c;
        seen = atoi(c) - 1;
    }
    char st[24] = "?", *at = strstr(raw, "\nwpa_state=");
    for (unsigned k = 0; at && k + 1 < sizeof st && at[11 + k] && at[11 + k] != '\n'; ++k)
        st[k] = at[11 + k], st[k + 1] = 0;
    tk_snprintf(line, sizeof line,
                "not joined %u s after wpa_supplicant started: wpa_state=%s, %d networks in its last "
                "scan, rescans every %d s (%s), switch %d scan %d connect %d",
                s, st, seen, want,
                want == SCAN_HOLD_S ? "held: a headset plays"
                : want > 5          ? "dark backoff"
                                    : "not held",
                g_wififlag, wifi_initstatus, wifi_connectstatus);
    ulog('W', "wifi", line);
}

static void scan_hold_poll(unsigned now) {
    unsigned info[36] = { 0 };
    if (!g_wififlag || __xstat(3, WPA_SOCK, info)) {
        sh.sock = 0; /* no wpa_supplicant: the next one starts at 5 s */
        sh.since = sh.up_at = 0;
        return;
    }
    int want = scan_interval_s(now);
    join_watch(now, want);
    if (sh.sock == info[4] && sh.sent == want) return;
    if (sh.tried && now - sh.tried < SCAN_RETRY_MS) return;
    char cmd[64];
    tk_snprintf(cmd, sizeof cmd, "wpa_cli -i wlan0 scan_interval %d >/dev/null 2>&1", want);
    if (system(cmd)) {
        sh.tried = now | !now;
        return;
    }
    sh.tried = 0;
    sh.sock = info[4];
    sh.sent = want;
}

/* power_poll, every SMART_POLL_MS. The row off, or the user's Wi-Fi off: stock's, and a radio Smart
 * Wi-Fi had off comes back. Otherwise a use or a new link restarts the count, a use wakes the
 * radio, and SMART_IDLE_MS without one, associated or not, turns it off. q2remote's requests (a
 * --dev build) and the scan hold are kept first, whether the row is on or not. */
void smart_poll(void) {
    unsigned now = time_now_ms();
    if (sw.at && now - sw.at < SMART_POLL_MS) return;
    sw.at = now | !now;
    remote_poll(now); /* before smart_needed's first ask, the scan hold's */
    scan_hold_poll(now);
    /* ponytail: while stock refuses its switch (smart_ready), a waiter that asks after this expiry
     * starts another SMART_CONNECT_MS: one more 15 s at most; keep each waiter's own start if it
     * bites. */
    if (sw.woke_at && now - sw.woke_at >= SMART_CONNECT_MS) sw.woke_at = 0;
    if (!st.smart_wifi || (!g_wififlag && !sw.off)) {
        sw.armed = 0;
        smart_wake();
        return;
    }
    int up = networkGetConnectStatus() > 0;
    if (!sw.armed || (up && !sw.up))
        sw.busy_at = now; /* turned on, or a new link: time sync first */
    sw.armed = 1;
    sw.up = up;
    if (smart_needed()) {
        sw.busy_at = now;
        smart_wake();
    } else if (!sw.off && now - sw.busy_at >= SMART_IDLE_MS && smart_ready())
        smart_switch(now);
}

/* Smart Wi-Fi has the radio off (the user's switch is on). */
int smart_wifi_off(void) { return sw.off; }

/* Smart Wi-Fi has the radio off or is bringing it back: whatever asks (smart_wifi_wait) gets the
 * link, so a server that was reachable still counts as one (catalogue.c). */
int smart_wifi_back(void) { return sw.off || sw.woke_at; }

/* Something that needs the link starting (Spotify's launch, a radio list or station, an automatic
 * scrobble upload): Smart Wi-Fi's radio woken now if it is off, and 1 while that wake's link is not
 * up yet, SMART_CONNECT_MS at most, so the caller waits for it; then, or with the radio as the user
 * left it, 0, and the caller goes on as stock's would. Up is both the status bar's bars, which the
 * payload's own actions check (get_wifisignal), and the monitor thread's connect status, which
 * stock's check (networkGetConnectStatus): each comes from its own poll, about a second apart. */
int smart_wifi_wait(void) {
    if (!sw.off && !sw.woke_at) return 0;
    unsigned now = time_now_ms();
    sw.busy_at = now;
    if (sw.off && !sw.woke_at) sw.woke_at = now | !now; /* the wait counts from the ask */
    if (sw.off && smart_ready()) smart_switch(now);
    if (now - sw.woke_at < SMART_CONNECT_MS &&
        (sw.off || get_wifisignal() <= 0 || networkGetConnectStatus() <= 0))
        return 1;
    sw.woke_at = 0;
    return 0;
}

/* The page in use: the top window, past stock's self-closing overlays: toasts (msginfo_dialog, ours
 * or stock's) and the volume (volume_dialog, which a wheel turn opens over a toast). */
void *page_in_use(void) {
    void *wm = window_manager();
    for (int i = (int)widget_count_children(wm) - 1; i >= 0; --i) {
        void *w = widget_get_child(wm, i);
        const char *name = widget_get_prop_str(w, "name", "");
        if (tk_strcmp(name, "msginfo_dialog") && tk_strcmp(name, "volume_dialog")) return w;
    }
    return (void *)0;
}

static int hold_link(const void *unused) {
    /* A page closing mid-slide is still the top window (iPod): the next tick looks again. */
    if (window_manager_is_animating(window_manager())) return 8; /* RET_REPEAT */
    void *win = sw.hold_win;
    int held = page_in_use() == win; /* compared first: a closed page is never read */
    if (held && smart_wifi_wait()) return 8;
    int (*fn)(void *, void *) = sw.hold_fn;
    sw.hold_fn = 0;
    sw.hold_timer = 0;
    if (held && !tk_strcmp(widget_get_prop_str(win, "name", ""), sw.hold_name)) {
        sw.held = 1; /* a radio that could not come back fails it now, as without Smart Wi-Fi */
        fn(sw.hold_ctx, (void *)0);
        sw.held = 0;
    }
    return 0;
}

/* An action that needs the link, pressed while Smart Wi-Fi brings the radio back (smart_wifi_wait),
 * on page (any page when 0): 1, "Connecting to Wi-Fi…", and fn(ctx, 0) runs from a 500 ms timer
 * once the link is up, or SMART_CONNECT_MS on (to fail as it would have), if the page it was
 * pressed on is still the one in use past toasts and the volume overlay; otherwise it is dropped.
 * 0: it runs now, as without Smart Wi-Fi: the radio as the user left it, the link up, another page,
 * or no timer. One waits at a time: a press while one waits takes its place, link or not, so it
 * never runs beside the held one. fn reads no event. */
int smart_hold(int (*fn)(void *, void *), void *ctx, const char *page) {
    void *win = page_in_use();
    const char *name = win ? widget_get_prop_str(win, "name", "") : "";
    if (sw.held || !win || (page && tk_strcmp(name, page)) || strlen(name) >= sizeof sw.hold_name)
        return 0;
    if (!sw.hold_timer &&
        (!smart_wifi_wait() || !(sw.hold_timer = timer_add(hold_link, (void *)0, 500))))
        return 0;
    sw.hold_fn = fn;
    sw.hold_ctx = ctx;
    sw.hold_win = win;
    tk_snprintf(sw.hold_name, sizeof sw.hold_name, "%s", name);
    toast("Connecting to Wi-Fi\xe2\x80\xa6");
    return 1;
}

/* A press of the payload's that needs the link, fn(ctx, event) itself: 1, it goes on; 0, it waits
 * for Smart Wi-Fi's link (smart_hold) or, with none coming, "Connect to Wi-Fi first". Held first,
 * so a second press after the link is up but before the held one runs joins it rather than running
 * beside it (and cancelling it). */
int need_link(int (*fn)(void *, void *), void *ctx) {
    if (smart_hold(fn, ctx, 0)) return 0;
    if (get_wifisignal() > 0) return 1;
    toast("Connect to Wi-Fi first");
    return 0;
}

/* Stock actions that check the link once (networkGetConnectStatus) and say msg_notnetwork without
 * it, held the same way: Streaming's Tidal row and Now Playing More's online lyrics and cover
 * match. None reads its event, and the rows are found again by name
 * (stock names each row's list_item and button by its index and reads only that name), so nothing
 * pressed is kept but the page. */
static int row_of(void *button) {
    const char *name = widget_get_prop_str(button, "name", "");
    return name[0] >= '0' && name[0] <= '9' && !name[1] ? name[0] - '0' : -1;
}
static void *held_row(void *view, void *ctx) {
    char name[2] = { (char)('0' + (int)(long)ctx), 0 };
    return view ? widget_lookup(view, name, 1) : (void *)0; /* its list_item */
}
static int stream_row_again(void *ctx, void *unused) {
    /* in the rows' own view: Home's Library list, beside its Streaming list, names its rows so too
     */
    void *row = held_row(widget_lookup(sw.hold_win, "scroll_view_streamsset", 1), ctx);
    return row ? stock_stream_row_trampoline(row, (void *)0) : 0;
}
static int online_match_again(void *win, void *unused) {
    return stock_online_match_trampoline(win);
}

/* Streaming's stock row (stream_page_init binds 0x540f7c): Tidal (0), a login page with the link
 * and msg_notnetwork without; on Streaming's page or Home's Split Streaming list (home.c
 * stream_rows), both waking the radio. ponytail: on Home it runs while Home is still the page in
 * use, as Spotify's row does, the Streaming list gone to another of Home's lists or not; drop it
 * there too if that surprises. */
int ringnav_stream_row(void *button, void *event) {
    int row = row_of(button);
    if (row >= 0 && smart_hold(stream_row_again, (void *)(long)row, 0)) return 0;
    /* OLED: Tidal with no link is a page saying so (feedback.c), not stock's toast */
    if (!row && get_wifisignal() <= 0 && feedback_offline()) return 0;
    return stock_stream_row_trampoline(button, event);
}

/* Network service's rows (0x4cce64): DLNA (0) and AirPlay (1) switch at once (stock starts their
 * services once the link is up); the third, stock's Wi-Fi File Transfer, is LocalSend's
 * (docs/localsend.md#what-it-replaces), whose page wakes the radio itself (net_page_open) and says
 * when Wi-Fi is off. */
int ringnav_netservice_row(void *button, void *event) {
    return row_of(button) == 2 ? localsend_open(0, 0) : stock_netservice_row_trampoline(button, event);
}

/* Now Playing More's Download Cover & Lyrics (0x539614, its row's case, given More's window): More
 * itself is no network page, so the radio wakes only for this press. */
int ringnav_online_match(void *win) {
    return win == page_in_use() && smart_hold(online_match_again, win, "playermore_page")
               ? 0
               : stock_online_match_trampoline(win);
}
