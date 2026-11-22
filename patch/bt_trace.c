/* Stock's Bluetooth connection state in the unified log
 * (docs/internals.md#bluetooth-connection-trace): every BT_TRACE_MS from the UI loop (system.c
 * power_poll), each change of what the Bluetooth page and stock's link thread go by, as a bt line:
 * the Bluetooth switch, the stack's start-up status (bt_initstatus), the link thread
 * (bt_connectstatus), a scan starting and ending (bt_searchtime), a link being set up
 * (bt_a2dp_direction), the link list's last item (its address and status), the saved device
 * (BTLINKMAC), and each connect the link thread starts, with its target and why: auto (headset.c's
 * autolink, at boot and at each Bluetooth on), usb (the same at a cable in, headset_bt_usb), tap
 * (a row or Connect, through bt_stock_click) or other (stock started it with neither), each
 * followed by one line of how it ended (connected, failed, no link, or abandoned when Bluetooth
 * goes off or the stack restarts under it: pend_end); and each auto-link decision, the device it
 * pages or why none (bt_trace_autolink). q2guard's lines beside them say what the kernel did
 * (docs/internals.md#bluetooth-devices).
 *
 * bt_acl_up answers whether the kernel holds a connected ACL link to an address (HCIGETCONNLIST on
 * an unbound HCI socket), which the Bluetooth page goes by where stock's state stays behind the
 * link (bt_ui.c bt_dev_state). */
#include "navigation.h"

#define BT_TRACE_MS 500 /* as heatguard's and the Bluetooth page's polls */
#define BT_WHY_MS 3000  /* a hint this old no longer names the next connect's reason */
#define BTLINKMAC                                                                                  \
    0xa4c0d8 /* stock's saved device, a MAC string (docs/internals.md#bluetooth-page) */
#define HCIGETCONNLIST 0x400448d4u /* _IOR('H', 212, int), MIPS: _IOR is 2 << 29 */
#define BT_CONNS 8                 /* links the conn list is asked for */
#define BT_SHOWN 32                /* found devices logged as shown, each page open */

static struct {
    unsigned at, why_at;
    int seen; /* the first poll has run: what it found is the baseline, logged once */
    int on, init, conn, scan, dir, n, status;
    char last[24], saved[24];
    char why[8]; /* the next connect's reason ("" none), set at why_at */
    char why_mac[24];
    unsigned acl_at;
    char acl_mac[24]; /* the address bt_acl_up last asked of, and its answer at acl_at */
    int acl;
    char held[24]; /* the address whose link the kernel holds while stock's item says otherwise */
    unsigned shown[BT_SHOWN]; /* the found devices the page has shown since it opened (hashes) */
    int n_shown;
    char pend[24]; /* the connect started and not yet ended: its target, since pend_at */
    unsigned pend_at;
} tr __attribute__((section(".scratch")));

static int mac_eq(const char *a, const char *b) { /* a MAC either case */
    for (; *a && *b; ++a, ++b) {
        char x = *a >= 'a' && *a <= 'f' ? (char)(*a - 32) : *a;
        char y = *b >= 'a' && *b <= 'f' ? (char)(*b - 32) : *b;
        if (x != y) return 0;
    }
    return *a == *b;
}

/* hci_conn_list_req's answer (include/net/bluetooth/hci_sock.h, 5.10): dev_id, conn_num, then
 * hci_conn_info of 16 bytes: handle, bdaddr[6] (last byte first), type (8; ACL_LINK 1), out, state
 * (10, 16 bits; BT_CONNECTED 1), link_mode. 1 when mac is a connected ACL link in it. */
int bt_acl_listed(const unsigned char *req, int max, const char *mac) {
    int n = req[2] | req[3] << 8;
    for (int i = 0; i < n && i < max; ++i) {
        const unsigned char *c = req + 4 + 16 * i;
        char a[24];
        if (c[8] != 1 || (c[10] | c[11] << 8) != 1) continue;
        tk_snprintf(a, sizeof a, "%02X:%02X:%02X:%02X:%02X:%02X", c[7], c[6], c[5], c[4], c[3],
                    c[2]);
        if (mac_eq(a, mac)) return 1;
    }
    return 0;
}

/* Whether the kernel holds a connected ACL link to mac on hci0, asked at most every BT_TRACE_MS
 * for one address (the page's rows ask on each paint). */
int bt_acl_up(const char *mac) {
    unsigned now = time_now_ms();
    if (!mac || !*mac) return 0;
    if (tr.acl_at && now - tr.acl_at < BT_TRACE_MS && mac_eq(mac, tr.acl_mac)) return tr.acl;
    unsigned req[1 + 4 * BT_CONNS] = { 0 }; /* dev_id 0: hci0 */
    ((unsigned short *)req)[1] = BT_CONNS;
    int fd = socket(31 /* AF_BLUETOOTH */, 3 /* SOCK_RAW */, 1 /* BTPROTO_HCI */), up = 0;
    if (fd >= 0) {
        up = !ioctl(fd, HCIGETCONNLIST, req) &&
             bt_acl_listed((const unsigned char *)req, BT_CONNS, mac);
        close(fd);
    }
    tr.acl_at = now | !now, tr.acl = up;
    tk_snprintf(tr.acl_mac, sizeof tr.acl_mac, "%s", mac);
    return up;
}

/* The reason for the next connect the link thread starts: "auto" or "usb" (headset.c's autolink)
 * or "tap" (bt_stock_click), for mac when known. */
void bt_trace_why(const char *why, const char *mac) {
    tk_snprintf(tr.why, sizeof tr.why, "%s", why ? why : "");
    tr.why_at = time_now_ms() | 1;
    tk_snprintf(tr.why_mac, sizeof tr.why_mac, "%s", mac ? mac : "");
}

/* The Bluetooth page opened (bt_ui.c bt_page): said, and the found devices it shows logged
 * afresh, so a device the radio heard (q2guard's "found") can be set against when it was shown. */
void bt_trace_page(void) {
    tr.n_shown = 0;
    ulog('I', "bt", "page opened");
}
static void who(char *out, unsigned n, const char *mac);
/* A found device on the page's list (bt_ui.c bt_rows_pre): logged the first time this open. */
void bt_trace_shown(const char *mac) {
    unsigned h = 5381;
    char a[96], b[128];
    if (!mac || !*mac) return;
    for (const char *c = mac; *c; ++c) h = h * 33 + (unsigned)(*c >= 'a' ? *c - 32 : *c);
    for (int i = 0; i < tr.n_shown; ++i)
        if (tr.shown[i] == h) return;
    if (tr.n_shown == BT_SHOWN) return;
    tr.shown[tr.n_shown++] = h;
    who(a, sizeof a, mac);
    tk_snprintf(b, sizeof b, "page shows found %s", a);
    ulog('I', "bt", b);
}

static const char *conn_word(int v) {
    return v == 0 ? "idle" : v == 1 ? "running" : v == 2 ? "ended" : v == 3 ? "checking" : "?";
}
/* A link item's status as stock's code and headset.c use it */
static const char *item_word(int v) {
    return v == 1   ? "queued"
           : v == 3 ? "connected"
           : v == 4 ? "failed"
           : v == 5 ? "connecting"
                    : "?";
}
static void say(char level, const char *msg) { ulog(level, "bt", msg); }
/* "MAC "name"", the name from the page's device list when it is there */
static void who(char *out, unsigned n, const char *mac) {
    const char *name = bt_dev_name(bt_dev_find(mac));
    if (name && *name)
        tk_snprintf(out, n, "%s \"%s\"", mac, name);
    else
        tk_snprintf(out, n, "%s", *mac ? mac : "-");
}

/* An auto-link decision (headset.c headset_bt_autolink, at boot, at each Bluetooth on and, as usb,
 * at a cable in): the device it pages and why, or why it pages none. connected: the link list's
 * connected item, which stops it; else go says whether the target was paired, or (-1) that it is
 * the car Car Audio has on the cable (usb_car.c usb_car_name). */
void bt_trace_autolink(const char *why, const char *target, const char *connected, int go) {
    char a[96], c[96], b[256];
    const char *saved = (const char *)BTLINKMAC;
    who(a, sizeof a, target ? target : "");
    const char *which =
        target && mac_eq(target, saved) ? "the saved device" : "not the saved device";
    if (go < 0) {
        tk_snprintf(b, sizeof b,
                    "auto-connect (%s) skips %s: the car on the cable, whose audio is Car Audio's",
                    why, a);
    } else if (connected) {
        who(c, sizeof c, connected);
        tk_snprintf(b, sizeof b, "auto-connect (%s) skips %s: %s is connected", why, a, c);
    } else if (go)
        tk_snprintf(b, sizeof b, "auto-connect (%s) picks %s: %s, paired, nothing connected", why,
                    a, which);
    else
        tk_snprintf(b, sizeof b, "auto-connect (%s) skips %s: %s, not paired", why, a, which);
    say(connected || go ? 'I' : 'W', b);
}

/* The link list's status for mac, -1 when no item is it */
static int item_status(const char *mac) {
    char m[32];
    int status = 0;
    for (int i = getBtLinkSize(); i-- > 0;) {
        m[0] = 0, getBtLinkItem(i, &status, m);
        if (mac_eq(m, mac)) return status;
    }
    return -1;
}

/* The connect started to tr.pend, ended now when it has: connected or failed by its item's status,
 * no link when the link thread stopped without one, abandoned when Bluetooth went off or the stack
 * is starting again (bt_initstatus not 0: a relaunch). One line, said once. */
static void pend_end(int on, int init, int conn, unsigned now) {
    char a[96], b[192];
    if (!tr.pend[0]) return;
    int st = on ? item_status(tr.pend) : -1;
    who(a, sizeof a, tr.pend);
    if (!on)
        tk_snprintf(b, sizeof b, "connect to %s abandoned: Bluetooth switched off", a);
    else if (init)
        tk_snprintf(b, sizeof b, "connect to %s abandoned: the stack restarted", a);
    else if (st == 3 || st == 4)
        tk_snprintf(b, sizeof b, "connect to %s ended: %s after %u ms", a,
                    st == 3 ? "connected" : "failed", now - tr.pend_at);
    else if (conn != 1)
        tk_snprintf(b, sizeof b, "connect to %s ended: no link, %s%s", a,
                    st < 0 ? "its item is gone" : "the link list says ",
                    st < 0 ? "" : item_word(st));
    else
        return;
    say(st == 3 ? 'I' : 'W', b);
    tr.pend[0] = 0;
}

void bt_trace_poll(void) {
    unsigned now = time_now_ms();
    if (tr.seen && now - tr.at < BT_TRACE_MS) return;
    tr.at = now;
    int on = g_bluetoothflag, init = I(bt_initstatus, 0), conn = I(bt_connectstatus, 0);
    int scan = bt_searchtime != 0, dir = I(bt_a2dp_direction, 0), n = on ? getBtLinkSize() : 0;
    int status = 0;
    char last[32] = "", saved[24], a[96], b[192];
    if (n > 0) getBtLinkItem(n - 1, &status, last);
    tk_snprintf(saved, sizeof saved, "%s", (const char *)BTLINKMAC);
    int first = !tr.seen;
    if (first || on != tr.on) say('I', on ? "switch on" : "switch off");
    if (first || init != tr.init) {
        tk_snprintf(b, sizeof b, "stack status %d", init);
        say(init < 0 ? 'W' : 'I', b);
    }
    if (first || strcmp(saved, tr.saved)) {
        who(a, sizeof a, saved);
        tk_snprintf(b, sizeof b, "saved device %s", a);
        say('I', b);
    }
    if (!first && scan != tr.scan) say('I', scan ? "scan started" : "scan ended");
    if (first || dir != tr.dir)
        say('I', dir == 1   ? "link setup to a headset"
                 : dir == 2 ? "link setup from a phone"
                            : "link setup none");
    if (first || n != tr.n || status != tr.status || strcmp(last, tr.last)) {
        who(a, sizeof a, last);
        if (n > 0)
            tk_snprintf(b, sizeof b, "link list %d, last %s status=%d %s", n, a, status,
                        item_word(status));
        else
            tk_snprintf(b, sizeof b, "link list 0");
        say('I', b);
    }
    if (first || conn != tr.conn) {
        tk_snprintf(b, sizeof b, "link thread %s (%d)", conn_word(conn), conn);
        say('I', b);
        pend_end(on, init, conn, now); /* the one before, ended by this change */
        if (!first && conn == 1) {     /* a connect starts: to the last item, and why */
            int fresh = tr.why[0] && now - tr.why_at < BT_WHY_MS &&
                        (!tr.why_mac[0] || mac_eq(tr.why_mac, last));
            if (tr.pend[0]) { /* the one before, still open: this one takes its place */
                char p[96];
                who(p, sizeof p, tr.pend);
                tk_snprintf(b, sizeof b, "connect to %s ended: another connect started", p);
                say('W', b);
            }
            who(a, sizeof a, last);
            tk_snprintf(b, sizeof b, "connect starts to %s why=%s", a, fresh ? tr.why : "other");
            say('I', b);
            tk_snprintf(tr.pend, sizeof tr.pend, "%s", last);
            tr.pend_at = now;
        }
        if (conn == 1) tr.why[0] = 0;
    } else
        pend_end(on, init, conn, now);
    /* stock's last item not connected while the kernel holds its link: the page's row would say
     * Connecting (docs/internals.md#bluetooth-page) */
    int held = on && n > 0 && status != 3 && last[0] && bt_acl_up(last);
    if (held && !mac_eq(tr.held, last)) {
        who(a, sizeof a, last);
        tk_snprintf(b, sizeof b,
                    "link list says %s for %s, the kernel holds its link: shown Connected",
                    item_word(status), a);
        say('W', b);
    }
    tk_snprintf(tr.held, sizeof tr.held, "%s", held ? last : "");
    tr.seen = 1, tr.on = on, tr.init = init, tr.conn = conn, tr.scan = scan, tr.dir = dir;
    tr.n = n, tr.status = status;
    tk_snprintf(tr.last, sizeof tr.last, "%s", last);
    tk_snprintf(tr.saved, sizeof tr.saved, "%s", saved);
}
