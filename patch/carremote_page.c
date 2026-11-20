/* Settings > Car Remote (docs/internals.md#car-remote, docs/oled-design.md#car-remote): the switch, then, while it
 * is on, Pair a Phone and a row per paired phone, each opening the phone's page (Status, Paired, Last Connected,
 * Rename, Forget Phone); with none paired, the empty state with Pair a Phone as its button. Pair a Phone opens a
 * page that says how a phone pairs (the phone starts it, so the Q2 Muse can only wait): with q2-iroh running, a QR
 * code for Q2 Muse Remote to scan, which carries the pairing's code (shown under it) and how to reach this Q2 Muse
 * from any network; else step by step over the link a phone can find it on now: Bluetooth while the daemon
 * advertises, else Wi-Fi with the address to type.
 *
 * What it shows comes from the daemon's files (patch/coda_rc.c): the peers file (a phone's id, key, name and when
 * it paired), /tmp/coda-rc.online (the connected phones and their links) and the seen file (when each was last
 * connected). Rename and Forget rewrite the peers file whole, as the daemon does. While a page is open it is built
 * again whenever what it shows changes (carremote_poll, from coda_rc_shim.c's poll), so a phone paired,
 * connected, gone or forgotten shows at once. */
#include "navigation.h"

#define CR_PEERS "/mnt/data/coda-rc/peers"
#define CR_SEEN "/mnt/data/coda-rc/seen"   /* coda_rc.c seen_stamp: id[8] when[4], little-endian */
#define CR_ONLINE "/tmp/coda-rc.online"    /* coda_rc.c online_poll: id[8] link[1] per connected phone */
#define CR_PEER_REC 76                     /* coda_rc.c crc_peers_save: id[8] ltk[32] name[32] created[4] */
#define CR_NAME_AT 40
#define CR_CREATED_AT 72
#define CR_SEEN_REC 12
#define CR_ONLINE_REC 9
#define CR_PEERS_MAX 8
#define CR_BLE "/tmp/coda-rc.ble"       /* coda_rc.c crc_ble_advertising: while a phone's Bluetooth scan can find it */
#define CR_IROH "/tmp/coda-rc.iroh"     /* q2-iroh --info (coda_rc_shim.c starts it): "id HEX" and "relay URL" lines */
#define CR_QR "/tmp/coda-rc.qr"         /* the code the QR code carries, while it shows (coda_rc.c crc_code_shown) */
#define CR_QR_X 20                      /* the QR code's plate: version 6 at 4 px a module, its quiet zone in */
#define CR_QR_Y 58
#define CR_QR_PX 180
#define CR_QR_SIDE_X 216                /* the words beside it */

/* The rows' geometry (docs/oled-design.md#car-remote, design/carremote-page mock_carremote.py) */
#define CR_ROW 32       /* a one-line row */
#define CR_PHONE_H 52   /* a phone's two-line row */
#define CR_GAP 10       /* the switch to Pair a Phone */
#define CR_FORGET_GAP 12 /* Rename to Forget Phone */
#define CR_CAP_TOP 32   /* the switch's caption under its title, when selected */
#define CR_CAP_LINE 17
#define CR_CAP_FOOT 5
#define CR_CAP_PX TYPE(13)
#define CR_GLYPH_X 26   /* Pair a Phone's plus-circle (playlist_pages.c PV_ICON_X) */
#define CR_ACTION_X 54  /* its label */
#define CR_PHONE_GLYPH 20 /* tools/art.py CR_PHONE_PX, centred on x 34 */
#define CR_PHONE_X 56   /* a phone's name and state */
#define CR_DOT 7        /* the Positive dot before "Connected" */
#define CR_DOT_GAP 5
#define CR_VALUE_PX TYPE(20)
/* The capsule's row reaches this far past it, its focus ring's CTA_RING_W px CTA_RING_OFF out (ui_kit.c cta_capsule) and no
 * further: under the switch the row's foot then stays above the set's list end (y 282 on OLED), so the wheel moving
 * on to it scrolls nothing. A foot past the list end, by a pixel even, has the list scroll the capsule to its top
 * (nav/lists.c reveal, then fit_top's whole rows), the switch gone. */
#define CR_CTA_OUT (CTA_RING_OFF + CTA_RING_W)
#define CR_STEP_PX TYPE(15)
#define CR_ROW_KIND "_cr_kind"
#define CR_VALUE "_cr_value"

enum { K_SWITCH = 1, K_ACTION, K_PHONE, K_VALUE, K_FORGET, K_CTA, K_CTA_GAP };

typedef struct {
    unsigned char id[8];
    char name[32];
    unsigned created, seen; /* wall clock; 0 unknown */
    int online, link;       /* link 0 Wi-Fi, 1 Bluetooth */
} cr_peer;

static struct {
    void *page, *view;       /* Settings > Car Remote and its list */
    void *phone, *phone_view; /* a phone's page */
    void *howto;             /* Pair a Phone */
    unsigned howto_shown;    /* what it shows (cr_howto_mode, the address and the QR code, hashed) */
    char code[8];            /* the pairing code the QR code carries; empty with none made */
    unsigned char phone_id[8]; /* whose page */
    unsigned shown, phone_shown; /* what each shows (cr_read's sum) */
    int sel;                 /* 0 while the switch's bar shows (it is laid out tall), else -1 */
    int cta_y;               /* with no phones: the capsule row's top in the list, else 0 */
    char forget_name[32];
} cp __attribute__((section(".scratch")));

/* ---- the daemon's files ---- */

/* The paired phones, their links and when each paired and was last connected; their count. *sum, when asked,
 * hashes all a page shows (names, ids, connections, times and the switch). */
static int cr_read(cr_peer *p, unsigned *sum) {
    unsigned char b[CR_PEERS_MAX * CR_PEER_REC + 2], on[(CR_PEERS_MAX + 1) * CR_ONLINE_REC + 2],
        seen[CR_PEERS_MAX * CR_SEEN_REC + 2];
    unsigned n = state_read(CR_PEERS, (char *)b, sizeof b) / CR_PEER_REC;
    unsigned k = state_read(CR_ONLINE, (char *)on, sizeof on) / CR_ONLINE_REC;
    unsigned s = state_read(CR_SEEN, (char *)seen, sizeof seen) / CR_SEEN_REC;
    int sw = coda_rc_enabled();
    unsigned h = hash_bytes(FNV_SEED, (const unsigned char *)&sw, sizeof sw);
    if (n > CR_PEERS_MAX) n = CR_PEERS_MAX;
    for (unsigned i = 0; i < n; ++i) {
        const unsigned char *r = b + 1 + i * CR_PEER_REC;
        cr_peer *q = p + i;
        memset(q, 0, sizeof *q);
        memcpy(q->id, r, 8);
        memcpy(q->name, r + CR_NAME_AT, 32);
        q->name[31] = 0;
        q->created = r[72] | r[73] << 8 | r[74] << 16 | (unsigned)r[75] << 24;
        for (unsigned j = 0; j < k; ++j)
            if (!memcmp(on + 1 + j * CR_ONLINE_REC, r, 8)) q->online = 1, q->link = on[1 + j * CR_ONLINE_REC + 8];
        for (unsigned j = 0; j < s; ++j) {
            const unsigned char *t = seen + 1 + j * CR_SEEN_REC;
            if (!memcmp(t, r, 8)) q->seen = t[8] | t[9] << 8 | t[10] << 16 | (unsigned)t[11] << 24;
        }
        h = hash_bytes(h, (const unsigned char *)q, sizeof *q);
    }
    memset(b, 0, sizeof b); /* the keys were in it */
    if (sum) *sum = h;
    return (int)n;
}

/* The peers file with phone id's record changed by name (rename) or taken out (name 0: Forget); the daemon
 * closes a forgotten phone's sessions within a second (coda_rc.c peers_poll). 0 when it could not be written. */
static int cr_rewrite(const unsigned char *id, const char *name) {
    unsigned char b[CR_PEERS_MAX * CR_PEER_REC + 2];
    unsigned n = state_read(CR_PEERS, (char *)b, sizeof b) / CR_PEER_REC, i = 0;
    if (n > CR_PEERS_MAX) n = CR_PEERS_MAX;
    while (i < n && memcmp(b + 1 + i * CR_PEER_REC, id, 8)) ++i;
    int ok = 1;
    if (i < n) {
        unsigned char *r = b + 1 + i * CR_PEER_REC;
        if (name) {
            memset(r + CR_NAME_AT, 0, 32);
            tk_snprintf((char *)r + CR_NAME_AT, 32, "%s", name);
        } else {
            memmove(r, r + CR_PEER_REC, (n - i - 1) * CR_PEER_REC);
            --n;
        }
        ok = blob_io(CR_PEERS, CR_PEERS ".new", b + 1, n * CR_PEER_REC, 1);
    }
    memset(b, 0, sizeof b);
    return ok;
}

/* The Q2 Muse's Wi-Fi address as a.b.c.d into out, or 0 with none (Wi-Fi off, or not joined). */
static const char *cr_address(char *out, unsigned n) {
    unsigned char ifr[40] = "wlan0";
    int fd = socket(2 /* AF_INET */, 1 /* MIPS SOCK_DGRAM */, 0), ok = 0;
    if (fd >= 0) {
        ok = ioctl(fd, 0x8915 /* SIOCGIFADDR */, ifr) == 0 && ifr[16 + 4];
        close(fd);
    }
    if (!ok) return 0;
    tk_snprintf(out, n, "%u.%u.%u.%u", ifr[20], ifr[21], ifr[22], ifr[23]);
    return out;
}

/* ---- words for times ---- */

static const char *const months[] = { "Jan", "Feb", "Mar", "Apr", "May", "Jun",
                                      "Jul", "Aug", "Sep", "Oct", "Nov", "Dec" };
static const char *const days[] = { "Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday" };

/* A local calendar day's number, for "today" and "yesterday" */
static long cr_day(const int *tm, long t) { return (t + tm[9]) / 86400; }

/* when (wall clock) as a date: "Nov 12", the year added when it is not this one; 0 for a clock never set. */
static const char *cr_date(char *out, unsigned n, unsigned when) {
    int a[11], b[11];
    long t = clock_back(when), now = clock_back(wall_time());
    const int *tm = when >= CLOCK_SET ? localtime_r(&t, a) : (const int *)0, *tn = localtime_r(&now, b);
    if (!tm) return 0;
    if (tn && tn[5] == tm[5])
        tk_snprintf(out, n, "%s %d", months[tm[4] % 12], tm[3]);
    else
        tk_snprintf(out, n, "%s %d, %d", months[tm[4] % 12], tm[3], tm[5] + 1900);
    return out;
}

/* when as the day it was: Today, Yesterday, else the date ("Nov 12"); 0 for a clock never set. Settings >
 * Developer Options > Trusted Computers' added dates (adb_trust.c). */
const char *day_or_date(char *out, unsigned n, unsigned when) {
    int a[11], b[11];
    long t = clock_back(when), now = clock_back(wall_time());
    const int *tm = when >= CLOCK_SET ? localtime_r(&t, a) : (const int *)0, *tn = localtime_r(&now, b);
    if (!tm || !tn) return 0;
    long ago = cr_day(tn, now) - cr_day(tm, t);
    if (ago == 0 || ago == 1) {
        tk_snprintf(out, n, "%s", ago ? "Yesterday" : "Today");
        return out;
    }
    return cr_date(out, n, when);
}

/* when for a line's end: "today, 18:11", "yesterday, 6:11 PM" (the clock's 12 or 24 hours), else
 * the date ("Nov 12"); 0 for a clock never set. Storage's Update Library (storage.c st_updated). */
const char *when_said(char *out, unsigned n, unsigned when) {
    int a[11], b[11];
    long t = clock_back(when), now = clock_back(wall_time());
    const int *tm = when >= CLOCK_SET ? localtime_r(&t, a) : (const int *)0, *tn = localtime_r(&now, b);
    if (!tm || !tn) return 0;
    long ago = cr_day(tn, now) - cr_day(tm, t);
    if (ago != 0 && ago != 1) return cr_date(out, n, when);
    int h = tm[2], m = tm[1];
    if (g_time24h_flag)
        tk_snprintf(out, n, "%s, %d:%02d", ago ? "yesterday" : "today", h, m);
    else
        tk_snprintf(out, n, "%s, %d:%02d %s", ago ? "yesterday" : "today", h % 12 ? h % 12 : 12, m,
                    h < 12 ? "AM" : "PM");
    return out;
}

/* when as a day: Today, Yesterday, the weekday within the week, else the date; lower makes the first letter
 * small, for "last seen friday"'s place in a line. 0 for a clock never set. */
static const char *cr_when(char *out, unsigned n, unsigned when, int lower) {
    int a[11], b[11];
    long t = clock_back(when), now = clock_back(wall_time());
    const int *tm = when >= CLOCK_SET ? localtime_r(&t, a) : (const int *)0, *tn = localtime_r(&now, b);
    if (!tm || !tn) return 0;
    long ago = cr_day(tn, now) - cr_day(tm, t);
    if (ago == 0)
        tk_snprintf(out, n, "%s", lower ? "today" : "Today");
    else if (ago == 1)
        tk_snprintf(out, n, "%s", lower ? "yesterday" : "Yesterday");
    else if (ago > 1 && ago < 7)
        tk_snprintf(out, n, "%s", days[tm[6] % 7]);
    else
        return cr_date(out, n, when);
    return out;
}

/* A phone's state under its name: "Connected over Wi-Fi", "Not connected · last seen Friday" */
static void cr_state(char *out, unsigned n, const cr_peer *q) {
    char day[24];
    if (q->online)
        tk_snprintf(out, n, "Connected over %s", q->link ? "Bluetooth" : "Wi-Fi");
    else if (cr_when(day, sizeof day, q->seen, 1))
        tk_snprintf(out, n, "Not connected \xc2\xb7 last seen %s", day);
    else
        tk_snprintf(out, n, "Not connected");
}

/* ---- the rows ---- */

/* w's top on the screen */
static int cr_screen_y(void *w) { return feedback_below(w) - I(w, W_H); }

/* A row of kind k at index, its label (caption) moved to x; the value (K_VALUE) painted at the right. */
static void *cr_row(void *view, int index, int k, const char *caption, const char *detail, int x,
                    int (*click)(void *, void *)) {
    void *item = page_row_detail(view, index, caption, detail, click);
    widget_set_prop_int(item, CR_ROW_KIND, k);
    for (unsigned i = 0, n = widget_count_children(item); i < n; ++i) {
        void *label = widget_get_child(item, i);
        widget_move_resize(label, x, I(label, W_Y), 375 - geom_val(V_RIGHT_X) - x, I(label, W_H));
    }
    return item;
}

/* A gap h px tall at the end of view: a row with no click, so the wheel passes it (navigation.c collect) */
static void cr_gap(void *view, int h) {
    widget_use_style(list_item_create(view, 0, 0, 375, h), "s_listitem_black");
}

/* The switch's height: grown under its caption while its bar shows (tall) */
static int cr_switch_h(int tall) { return tall ? CR_CAP_TOP + 2 * CR_CAP_LINE + CR_CAP_FOOT : CR_ROW; }

/* view's list with item_height 0, so stock's layout stacks the rows at their own heights (a phone's two lines
 * included), and as tall as they are with the switch grown, up to the screen's foot. */
static void cr_fit(void *view) {
    int y = 0;
    for (unsigned i = 0, n = widget_count_children(view); i < n; ++i) {
        void *item = widget_get_child(view, i);
        y += widget_get_prop_int(item, CR_ROW_KIND, 0) == K_SWITCH ? cr_switch_h(1) : I(item, W_H);
    }
    void *list = P(view, W_PARENT);
    int room = 320 - cr_screen_y(list), h = y < room ? y : room;
    widget_set_prop_int(list, "item_height", 0);
    widget_resize(list, 375, h);
    widget_resize(view, 375, h);
    widget_layout(list);
}

static int cr_selected(void *view) { return view ? widget_get_prop_int(view, "_ringnav_index", 0) : 0; }

/* ---- Pair a Phone: how a phone pairs ---- */

static unsigned cr_howto_sum(void);
static void cr_qr_code(void);

static int cr_howto_closed(void *ctx, void *event) {
    if (cp.howto == ctx) cp.howto = 0;
    unlink(CR_QR); /* no code stands once the page has gone */
    memset(cp.code, 0, sizeof cp.code);
    return 0;
}

static int cr_howto_open(void *ctx, void *event) {
    if (cp.howto || !(cp.howto = page_open("carhowto_page", cr_howto_closed, page_keyup))) return 0;
    cr_qr_code();
    cp.howto_shown = cr_howto_sum();
    page_list(cp.howto, cp.howto, 0, "Pair a Phone", 1, 48); /* the title; no rows */
    widget_invalidate_force(cp.howto, 0);
    return 0;
}

/* How a phone can find this Q2 Muse now, the address into addr with Wi-Fi: CR_BY_BLE while the daemon advertises
 * (Bluetooth on and bluetoothd took the GATT application, docs/remote-protocol.md#bluetooth-le-primary), else
 * CR_BY_WIFI while Wi-Fi has an address (Q2 Muse Remote's Wi-Fi address tab), else CR_BY_NONE. Q2 Muse Remote's
 * Bluetooth tab finds a Q2 Muse by its advertisement only, and its Wi-Fi tab lists the ones the daemon announces
 * over mDNS (docs/remote-protocol.md#discovery) or takes an address typed, so the page gives the steps of the one
 * that can work and never the other's. */
enum { CR_BY_NONE, CR_BY_WIFI, CR_BY_BLE, CR_BY_QR };

/* The QR code's text into out ("" with no q2-iroh running): Q2MUSE:1:<code>:<endpoint id>:<Wi-Fi address>:<relay>, in
 * capitals so that QR's alphanumeric mode packs it into version 6 (Q2 Muse Remote reads it in any case; an empty
 * field is unknown). The endpoint id and relay are q2-iroh's (CR_IROH), the code the one the daemon takes for the
 * next pairing (CR_QR, docs/remote-protocol.md#pairing-by-qr-code). */
/* q2-iroh's endpoint id and home relay from CR_IROH into id[72] and relay[200] ("" each when not known) */
static void cr_iroh(char *id, char *relay) {
    char raw[400];
    *id = *relay = 0;
    state_read(CR_IROH, raw, sizeof raw);
    for (char *l = raw + 1; *l;) {
        char *end = l;
        while (*end && *end != '\n') ++end;
        int more = *end != 0;
        *end = 0;
        if (!strncmp(l, "id ", 3)) tk_snprintf(id, 72, "%s", l + 3);
        if (!strncmp(l, "relay ", 6)) tk_snprintf(relay, 200, "%s", l + 6);
        l = end + more;
    }
}

static void cr_qr_text(char *out, unsigned n, const char *addr) {
    char id[72], relay[200];
    *out = 0;
    cr_iroh(id, relay);
    if (strlen(id) != 64 || !cp.code[0]) return;
    tk_snprintf(out, n, "Q2MUSE:1:%s:%s:%s:%s", cp.code, id, addr, relay);
    for (char *c = out; *c; ++c)
        if (*c >= 'a' && *c <= 'z') *c -= 32;
}

/* A new code for the QR code, in CR_QR for the daemon: on opening, and after a pairing on the last one ended (the
 * daemon removed the file). Drawn as the daemon draws its own (coda_rc_core.c new_code); none without randomness. */
static void cr_qr_code(void) {
    unsigned v = 0xffffffffu;
    memset(cp.code, 0, sizeof cp.code);
    for (int i = 0; i < 8 && v >= 4294000000u; ++i)
        if (!blob_io("/dev/urandom", 0, &v, sizeof v, 0)) return;
    if (v >= 4294000000u) return;
    tk_snprintf(cp.code, sizeof cp.code, "%06u", v % 1000000);
    char line[8];
    tk_snprintf(line, sizeof line, "%s\n", cp.code);
    if (!blob_io(CR_QR, CR_QR ".new", line, 7, 1)) memset(cp.code, 0, sizeof cp.code);
}

static int cr_howto_mode(char *addr, unsigned n) {
    int wifi = cr_address(addr, n) != 0;
    if (!wifi) *addr = 0;
    /* q2-iroh running, and reachable: through the relay it has (any network), or at the Wi-Fi address the QR code
     * carries (a phone on the same Wi-Fi) */
    char id[72], relay[200];
    cr_iroh(id, relay);
    if (strlen(id) == 64 && (wifi || *relay)) return CR_BY_QR;
    if (g_bluetoothflag && !access(CR_BLE, 0)) return CR_BY_BLE; /* the flag: a killed daemon's file says nothing */
    return wifi ? CR_BY_WIFI : CR_BY_NONE;
}

static unsigned cr_howto_sum(void) {
    char addr[20], qr[400] = "";
    int mode = cr_howto_mode(addr, sizeof addr);
    if (mode == CR_BY_QR) cr_qr_text(qr, sizeof qr, addr);
    unsigned h = hash_bytes(FNV_SEED, (const unsigned char *)&mode, sizeof mode);
    return hash_bytes(hash_bytes(h, (const unsigned char *)addr, strlen(addr)), (const unsigned char *)qr, strlen(qr));
}

/* With q2-iroh running: the QR code, the code under it in two groups of three, and beside it what to do with it in
 * Q2 Muse Remote's words (its Pair a Q2 Muse screen's Scan QR Code). Scanned on the same Wi-Fi or on any other
 * network, the phone pairs on the code with nothing typed; the code is there to type in the app's Wi-Fi address tab,
 * at the address under it, when the phone is on this Wi-Fi and cannot scan. */
static void cr_howto_qr(void *w, void *canvas, const char *addr) {
    char text[400], code[8];
    int sy = cr_screen_y(w), x = CR_QR_SIDE_X, tw = 375 - CR_QR_SIDE_X - 12, foot = CR_QR_Y + CR_QR_PX + 26 - sy;
    unsigned primary = 0xffffff, secondary = tok(TOK_SECONDARY), muted = tok(TOK_TERTIARY);
    cr_qr_text(text, sizeof text, addr);
    if (!*text) return; /* no code could be made: nothing to scan */
    ui_qr(canvas, text, CR_QR_X, CR_QR_Y - sy, CR_QR_PX);
    tk_snprintf(code, sizeof code, "%.3s %.3s", cp.code, cp.code + 3);
    ui_text(canvas, code, 1, TYPE(28), primary, 0, CR_QR_X + CR_QR_PX / 2, foot, CR_QR_PX, 1);
    ui_text(canvas, "Scan with", 0, TYPE(14), secondary, 0, x, 72 - sy, tw, 2);
    ui_text(canvas, "Q2 Muse Remote", 1, TYPE(16), primary, 0, x, 94 - sy, tw, 2);
    ui_text(canvas, "Pair a Q2 Muse >", 0, TYPE(14), secondary, 0, x, 122 - sy, tw, 2);
    ui_text(canvas, "Scan QR Code", 0, TYPE(14), secondary, 0, x, 140 - sy, tw, 2);
    ui_text(canvas, "Works from any", 0, TYPE(14), muted, 0, x, 172 - sy, tw, 2);
    ui_text(canvas, "network", 0, TYPE(14), muted, 0, x, 190 - sy, tw, 2);
    if (*addr) { /* the Wi-Fi address tab's way, for a phone on this Wi-Fi with no camera to hand */
        ui_text(canvas, "Or type the code at", 0, TYPE(13), muted, 0, x, 222 - sy, tw, 2);
        ui_text(canvas, addr, 0, TYPE(13), muted, 0, x, 239 - sy, tw, 2);
    }
    ui_text(canvas, "Waiting\xe2\x80\xa6", 0, TYPE(14), secondary, 0, x, foot, tw, 2);
}

/* Its steps in Q2 Muse Remote's own words (its Pair a Q2 Muse screen, its Bluetooth and Wi-Fi address tabs, Next),
 * then what they need: the address, for a phone that does not list it (the app's port, 7720, is coda-rc's), and
 * that it waits. The Wi-Fi tab lists this Q2 Muse by its Bluetooth name (the mDNS instance), so its step names
 * that; the Bluetooth tab's advertisement is always "Q2 Muse" (coda_rc_ble.c). With neither
 * link, the steps join a Wi-Fi network first; the page turns to the Wi-Fi steps once it has (carremote_poll). Each
 * line fits the 297 px beside its number at CR_STEP_PX. With q2-iroh running, the QR code instead (cr_howto_qr). */
static void cr_howto_paint(void *w, void *canvas) {
    static const char *const steps[3][4] = {
        [CR_BY_NONE] = { "Open Settings > Wi-Fi", "Join the Wi-Fi your phone is on", "Come back here to pair", 0 },
        [CR_BY_WIFI] = { "Open the Q2 Muse Remote app", "Pair a Q2 Muse > Wi-Fi address",
                         0 /* "Pick <name>, then tap Next" */, "Type the code shown here" },
        [CR_BY_BLE] = { "Open the Q2 Muse Remote app", "Pair a Q2 Muse > Bluetooth", "Pick Q2 Muse, then tap Next",
                        "Type the code shown here" },
    };
    char addr[20], line[64], name[40], pick[64];
    int mode = cr_howto_mode(addr, sizeof addr), y = 66 - cr_screen_y(w);
    if (mode == CR_BY_QR) {
        cr_howto_qr(w, canvas, addr);
        return;
    }
    tk_snprintf(pick, sizeof pick, "Pick %s, then tap Next", bt_name(name, sizeof name));
    /* Text Primary as the dark theme's: ui_text's ink turns it for Light Mode, as the muted lines'. The digits sit
     * on Raised 2's disc, which fill_box leaves dark in either mode, so theirs is white in both (turned twice). */
    unsigned primary = 0xffffff, digit_ink = flip_if(ipod_light(), 0xffffff), muted = tok(TOK_TERTIARY);
    for (int i = 0; i < 4 && (steps[mode][i] || mode == CR_BY_WIFI); ++i, y += 28) {
        rect_t disc = { 24, y - 10, 20, 20 };
        char digit[4];
        fill_box(canvas, &disc, RGBA(tok(TOK_RAISED_2)), 10);
        tk_snprintf(digit, sizeof digit, "%d", i + 1);
        ui_text(canvas, digit, 1, TYPE(13), digit_ink, 0, 34, y, 20, 1);
        ui_text(canvas, steps[mode][i] ? steps[mode][i] : pick, 0, CR_STEP_PX, primary, 0, 54, y, 375 - 54 - 24, 2);
    }
    y = 190 - cr_screen_y(w);
    ui_text(canvas, "Q2 Muse", 1, TYPE(17), primary, 0, 24, y, 327, 2);
    if (mode == CR_BY_NONE) {
        ui_text(canvas, "Wi-Fi  not connected", 0, TYPE(14), muted, 0, 24, y + 24, 327, 2);
        return; /* no phone can reach it yet: nothing to wait for */
    }
    if (mode == CR_BY_WIFI) {
        tk_snprintf(line, sizeof line, "Address  %s", addr);
        ui_text(canvas, line, 0, TYPE(14), muted, 0, 24, y + 24, 327, 2);
        ui_text(canvas, "Phone and Q2 Muse on one Wi-Fi", 0, TYPE(14), muted, 0, 24, y + 44, 327, 2);
    } else if (*addr) {
        tk_snprintf(line, sizeof line, "Or Wi-Fi address  %s", addr);
        ui_text(canvas, line, 0, TYPE(14), muted, 0, 24, y + 24, 327, 2);
    }
    ui_text(canvas, "Waiting for a phone\xe2\x80\xa6", 0, TYPE(14), tok(TOK_SECONDARY), 0, 24,
            y + (mode == CR_BY_WIFI ? 64 : *addr ? 44 : 24), 327, 2);
}

/* ---- a phone's page ---- */

static int cr_find(const cr_peer *p, int n, const unsigned char *id) {
    for (int i = 0; i < n; ++i)
        if (!memcmp(p[i].id, id, 8)) return i;
    return -1;
}

static int cr_phone_show(const void *ctx);

static const char *cr_rename_done(const char *text) {
    char name[32];
    unsigned a = 0, b = strlen(text);
    while (text[a] == ' ') ++a;
    while (b > a && text[b - 1] == ' ') --b;
    tk_snprintf(name, sizeof name, "%.*s", (int)(b - a), text + a);
    if (!*name) return "Name can't be empty";
    if (!cr_rewrite(cp.phone_id, name)) return "Could not rename the phone";
    if (cp.phone) timer_add(cr_phone_show, (void *)1, 0);
    return 0;
}

static void cr_forget_answer(void *ctx, int row, int toggle_on) {
    if (row != 1) return;
    if (!cr_rewrite(cp.phone_id, 0)) {
        toast("Could not forget the phone");
        return;
    }
    if (cp.phone) window_close(cp.phone); /* back on Car Remote, which drops the row at its next poll */
}

static int cr_phone_click(void *ctx, void *event) {
    int k = (int)(long)ctx;
    cr_peer p[CR_PEERS_MAX];
    int n = cr_read(p, 0), i = cr_find(p, n, cp.phone_id);
    if (i < 0) return 0;
    if (k == 3) {
        search_strip_label("Rename"); /* typed in its row, after the label (search_strip.c) */
        search_strip_return(STRIP_GO);
        search_strip_text("Rename Phone", p[i].name, 31, cr_rename_done);
    } else if (k == 4) {
        static const char *const rows[] = { "Cancel", "Forget Phone" };
        tk_snprintf(cp.forget_name, sizeof cp.forget_name, "%s", p[i].name);
        confirm(&(confirm_t){ .title = "Forget %s?",
                              .name = cp.forget_name,
                              .body = "It has to pair again to control\nthis Q2 Muse.",
                              .rows = rows,
                              .n_rows = 2,
                              .kind = CONFIRM_DESTRUCTIVE,
                              .back = -1,
                              .answer = cr_forget_answer });
    }
    return 0; /* Status, Paired and Last Connected are to read */
}

/* The phone's page: Status, Paired, Last Connected, Rename (its name, a chevron), then Forget Phone in red. ctx 0
 * opens on Rename; 1 (a rebuild) keeps the selection. A phone gone (forgotten by its own app's pairing again
 * elsewhere, say) closes it. */
static int cr_phone_show(const void *ctx) {
    if (!cp.phone) return 0;
    cr_peer p[CR_PEERS_MAX];
    int n = cr_read(p, &cp.phone_shown), i = cr_find(p, n, cp.phone_id);
    if (i < 0) {
        window_close(cp.phone);
        return 0;
    }
    int sel = ctx && cp.phone_view ? cr_selected(cp.phone_view) : 3;
    const cr_peer *q = p + i;
    char status[40], paired[24], last[24];
    tk_snprintf(status, sizeof status, "%s", !q->online ? "Not connected" : q->link ? "Connected \xc2\xb7 Bluetooth"
                                                                                    : "Connected \xc2\xb7 Wi-Fi");
    if (!cr_date(paired, sizeof paired, q->created)) tk_snprintf(paired, sizeof paired, "Unknown");
    if (q->online)
        tk_snprintf(last, sizeof last, "Now");
    else if (!cr_when(last, sizeof last, q->seen, 0))
        tk_snprintf(last, sizeof last, "Unknown");
    cp.phone_view = 0;
    void *view = page_list(cp.phone, cp.phone, 0, q->name, 6, 48);
    const char *labels[] = { "Status", "Paired", "Last Connected", "Rename" }, *values[] = { status, paired, last, q->name };
    for (int r = 0; r < 4; ++r) {
        void *item = cr_row(view, r, K_VALUE, labels[r], 0, page_x(), cr_phone_click);
        widget_set_prop_str(item, CR_VALUE, values[r]);
        widget_set_prop_str(item, "_cr_label", labels[r]);
        if (r == 3) row_into(item, page_h(48));
    }
    cr_gap(view, CR_FORGET_GAP);
    void *forget = cr_row(view, 4, K_FORGET, "Forget Phone", 0, page_x(), cr_phone_click);
    widget_set_prop_int(widget_get_child(forget, 0), "style:normal:text_color", (int)RGBA(tok(TOK_NEGATIVE)));
    cr_fit(view);
    /* five rows: the wheel's count passes the gap (navigation.c collect), so Forget Phone is row 4, and a count
     * that took the gap in would not match ringnav's own at its next load, which then drops the row (nav/lists.c
     * load) and the page showed Status selected */
    ringnav_select(view, sel < 5 ? sel : 3, 5);
    cp.phone_view = view;
    return 0;
}

static int cr_phone_closed(void *ctx, void *event) {
    if (cp.phone == ctx) cp.phone = cp.phone_view = 0;
    return 0;
}

/* ---- Settings > Car Remote ---- */

static int cr_page_show(const void *ctx);

static int cr_click(void *ctx, void *event) {
    int i = (int)(long)ctx;
    if (i == 0) {
        coda_rc_toggle();
        timer_add(cr_page_show, (void *)1, 0); /* a row's click may not rebuild its own list */
    } else if (i == 1) {
        cr_howto_open(0, 0);
    } else {
        cr_peer p[CR_PEERS_MAX];
        int n = cr_read(p, 0);
        if (i - 2 >= n || cp.phone || !(cp.phone = page_open("carphone_page", cr_phone_closed, page_keyup)))
            return 0;
        memcpy(cp.phone_id, p[i - 2].id, 8);
        cr_phone_show(0);
    }
    return 0;
}

/* The page: the switch (its caption under it while selected); while on, Pair a Phone (the blue top-action row)
 * and the phones, or, with none, the empty state with Pair a Phone as its button. ctx 0 opens on the switch, or
 * on the empty state's button when it shows; 1 (a change) keeps the selected row. */
static int cr_page_show(const void *ctx) {
    if (!cp.page) return 0;
    cr_peer p[CR_PEERS_MAX];
    int n = cr_read(p, &cp.shown), on = coda_rc_enabled(), empty = on && !n, rows = on && n ? 2 + n : empty ? 2 : 1;
    int sel = ctx && cp.view ? cr_selected(cp.view) : empty ? 1 : 0;
    cp.view = 0;
    void *view = page_list(cp.page, cp.page, 0, "Car Remote", rows > 1 ? rows + 1 : 1, 48);
    void *sw = cr_row(view, 0, K_SWITCH, "", 0, page_x(), cr_click);
    list_toggle(widget_get_child(sw, 0), "Car Remote", on);
    /* none: the empty state under the switch, in its compact form as rows stand above it (feedback.c
     * empty_compact; docs/oled-design.md#empty-and-offline). Its button, Pair a Phone, is a row of this list
     * (empty_state keeps its place in the stack and leaves it to the page), so the wheel has the one list: two
     * lists that each hold a selection give it none (nav/lists.c surface_under), and it moved nothing. */
    static const empty_t none = { "car-profile", "No Paired Phones", "Open Q2 Muse Remote on your phone to pair",
                                  "Pair a Phone" };
    int free_top = cr_screen_y(view) + cr_switch_h(1);
    cp.cta_y = empty ? empty_row_y(free_top) - cr_screen_y(view) - CR_CTA_OUT : 0;
    if (empty) {
        void *gap = list_item_create(view, 0, 0, 375, cp.cta_y - CR_ROW);
        widget_use_style(gap, "s_listitem_black");
        widget_set_prop_int(gap, CR_ROW_KIND, K_CTA_GAP);
        void *cta = cr_row(view, 1, K_CTA, "", 0, page_x(), cr_click); /* its index opens the how-to page */
        widget_set_prop_int(cta, "_rings_itself", 1); /* no selection bar (nav/row_paint.c paint_selection) */
        widget_resize(cta, 375, CTA_H + 2 * CR_CTA_OUT); /* no chevron: it acts, as a button */
    }
    if (rows > 1 && !empty) {
        cr_gap(view, CR_GAP);
        void *pair = cr_row(view, 1, K_ACTION, "Pair a Phone", 0, CR_ACTION_X - 2, cr_click);
        widget_set_prop_int(widget_get_child(pair, 0), "style:normal:text_color", (int)RGBA(scheme()->tones[0]));
    }
    for (int i = 0; on && i < n; ++i) {
        char state[64];
        cr_state(state, sizeof state, p + i);
        void *item = cr_row(view, 2 + i, K_PHONE, p[i].name, state, CR_PHONE_X, cr_click);
        void *detail = widget_get_child(item, 1);
        widget_set_prop_int(detail, "style:normal:text_color", (int)RGBA(tok(TOK_TERTIARY)));
        if (p[i].online) widget_move_resize(detail, CR_PHONE_X + CR_DOT + CR_DOT_GAP, I(detail, W_Y),
                                            I(detail, W_W) - CR_DOT - CR_DOT_GAP, I(detail, W_H));
        widget_set_prop_int(item, "_cr_online", p[i].online);
        row_into(item, page_h(64));
    }
    /* rows counts what the wheel stops on (a gap is passed, so Pair a Phone is row 1 either way, as its click's
     * index) */
    if (sel < 0) sel = 0;
    if (sel >= rows) sel = rows - 1;
    cr_fit(view); /* the switch grows at its first paint with the bar on it (carremote_paint) */
    cp.sel = -1;
    ringnav_select(view, sel, rows);
    cp.view = view;
    empty_state(cp.page, free_top, empty ? &none : 0, 0);
    return 0;
}

/* ringnav_paint's, for the three pages: Pair a Phone's glyph and accent, a phone's glyph and dot, a value on its
 * row's right and Forget Phone's red, each white on the selection; the switch's caption; the how-to page. The
 * list is laid out again when its selection moved, the switch growing or shrinking with it. */
void carremote_paint(void *w, void *canvas) {
    if (!w || !P(canvas, CANVAS_LCD) || (!cp.page && !cp.howto)) return;
    if (w == cp.howto) {
        cr_howto_paint(w, canvas);
        return;
    }
    void *view = P(w, W_PARENT);
    if (!view || (view != cp.view && view != cp.phone_view)) return;
    if (view == cp.view && w == widget_get_child(view, 0)) {
        /* the switch's bar shows: room for its caption, the rows under it laid out again (settings_pages.c's
         * captioned rows' way, without their 150 ms grow) */
        int lit = ringnav_row_selected(w) ? 0 : -1;
        if (lit != cp.sel) {
            cp.sel = lit;
            I(w, W_H) = cr_switch_h(!lit);
            /* the capsule keeps its place under the empty state's words: its gap takes up the change */
            void *gap = cp.cta_y && widget_count_children(view) > 1 ? widget_get_child(view, 1) : (void *)0;
            if (gap && widget_get_prop_int(gap, CR_ROW_KIND, 0) == K_CTA_GAP) I(gap, W_H) = cp.cta_y - I(w, W_H);
            widget_layout(P(view, W_PARENT));
            widget_invalidate_force(window_of(view), 0);
        }
    }
    int k = widget_get_prop_int(w, CR_ROW_KIND, 0), on = ringnav_row_selected(w), h = I(w, W_H);
    void *label = widget_count_children(w) ? widget_get_child(w, 0) : (void *)0;
    char art[32];
    if (k == K_SWITCH && on && h > CR_ROW) {
        ui_line(canvas, "Control your Q2 Muse from Android Auto", page_x(), 375 - 2 * page_x(),
                CR_CAP_TOP + CR_CAP_LINE / 2 + 1, 2, "inter", CR_CAP_PX, RGB_CAPTION_ON_ACCENT);
        ui_line(canvas, "with Q2 Muse Remote.", page_x(), 375 - 2 * page_x(),
                CR_CAP_TOP + CR_CAP_LINE + CR_CAP_LINE / 2 + 1, 2, "inter", CR_CAP_PX, RGB_CAPTION_ON_ACCENT);
    } else if (k == K_CTA) { /* the empty state's button, Pair a Phone: ringed while selected, darker pressed */
        const char *label = "Pair a Phone";
        ui_cta_paint(canvas, &label, 1, CR_CTA_OUT, on ? 0 : -1, on && pointer_down());
    } else if (k == K_ACTION || k == K_FORGET) {
        unsigned ink = RGBA(on ? 0xffffff : k == K_ACTION ? scheme()->tones[0] & 0xffffffu : tok(TOK_NEGATIVE));
        if (k == K_ACTION) {
            tk_snprintf(art, sizeof art, "plact_add_%c", on ? 'w' : 'a');
            paint_art(w, canvas, theme_art(art), CR_GLYPH_X, (h - 18) / 2);
        }
        /* written when it differs: the selection's own ink may have changed it */
        if (label && widget_get_prop_int(label, "style:normal:text_color", 0) != (int)ink)
            widget_set_prop_int(label, "style:normal:text_color", (int)ink);
    } else if (k == K_PHONE) {
        tk_snprintf(art, sizeof art, "crphone_%c", on ? 'w' : ipod_light() ? 'l' : 'd');
        paint_art(w, canvas, theme_art(art), 34 - CR_PHONE_GLYPH / 2, (h - CR_PHONE_GLYPH) / 2);
        void *detail = widget_count_children(w) > 1 ? widget_get_child(w, 1) : (void *)0;
        if (widget_get_prop_int(w, "_cr_online", 0) && detail) {
            int cy = I(detail, W_Y) + I(detail, W_H) / 2;
            rect_t dot = { CR_PHONE_X, cy - CR_DOT / 2, CR_DOT, CR_DOT };
            fill_box(canvas, &dot, RGBA(on ? 0xffffff : RGB_POSITIVE), CR_DOT / 2);
        }
    } else if (k == K_VALUE) {
        const char *value = widget_get_prop_str(w, CR_VALUE, "");
        int end = 375 - geom_val(V_RIGHT_X) - (widget_count_children(w) > 1 ? VALUE_CHEVRON_GAP : 0),
            start = page_x() + (label ? ui_text_w(canvas, widget_get_prop_str(w, "_cr_label", ""), 0, CR_VALUE_PX) : 0) + KV_GAP;
        ui_text(canvas, value, 0, CR_VALUE_PX, on ? 0xffffff : tok(TOK_SECONDARY), 1, end, h / 2, end - start, 3);
    }
}

/* Every poll while a page is open: built again when what it shows has changed. Not under a confirmation card,
 * whose answer acts first; the change is seen at the first poll after. */
void carremote_poll(void) {
    if (cp.howto) { /* Pair a Phone: painted again when Bluetooth or Wi-Fi changes what a phone can find */
        if (access(CR_QR, 0)) cr_qr_code(); /* a pairing on its code ended: the QR code carries a new one */
        unsigned h = cr_howto_sum();
        if (h != cp.howto_shown) widget_invalidate_force(cp.howto, 0);
        cp.howto_shown = h;
    }
    if ((!cp.page && !cp.phone) || confirm_up()) return;
    unsigned sum;
    cr_peer p[CR_PEERS_MAX];
    cr_read(p, &sum);
    if (cp.page && sum != cp.shown) timer_add(cr_page_show, (void *)1, 0);
    if (cp.phone && sum != cp.phone_shown) timer_add(cr_phone_show, (void *)1, 0);
    cp.shown = cp.phone_shown = sum; /* one rebuild per change, however long the timer takes */
}

/* A phone paired (coda_rc_shim.c): the how-to page has done its work, and the toast names the phone. */
void carremote_paired(const char *name) {
    char text[48];
    if (cp.howto) window_close(cp.howto);
    tk_snprintf(text, sizeof text, "%s paired", name && *name ? name : "Phone");
    toast_icon(theme_art("usbtoast_ejected"), text); /* the Positive check-circle (tools/art.py USB_TOASTS) */
}

static int cr_page_closed(void *ctx, void *event) {
    cp.page = cp.view = 0;
    return 0;
}

/* System Settings' Car Remote row (navigation.c ringnav_systemset) */
int carremote_open(void *ctx, void *event) {
    if (cp.page || !(cp.page = page_open("carremote_page", cr_page_closed, page_keyup))) return 0;
    cr_page_show(0);
    return 0;
}
