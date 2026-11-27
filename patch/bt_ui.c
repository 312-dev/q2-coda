/* Settings > Bluetooth (docs/oled-design.md#bluetooth, docs/internals.md#bluetooth-page): devices
 * before settings. Over the list stock's btmenu_rows builds (and rebuilds on every scan result and
 * link change, ringnav_btrows), the page is the switch, Name, then My Devices (the paired ones),
 * Other Devices (the found ones a scan heard while the page is open; a stored record with no key
 * removed through BlueZ, seen_fetch) and an Audio group: Quality, Headset Volume, Resume on Connect and
 * Link Volume. Stock's Scan now row goes: the page scans by itself while it is open (bt_ui_poll),
 * a spinner beside Other Devices while it runs, and stops a scan when a connect starts. With
 * Bluetooth off, the switch, Name and a line saying what turning it on gives.
 *
 * A device row keeps stock's button, its name over its index in pdeq_btshowlist, and stock's state
 * label (msg_connected, msg_connecting, msg_paired, which tools/build.py LABELS words "Connected",
 * "Connecting…" and "Not Connected"); laid out here after stock's layout (bt_rows_pre, bt_rows_post
 * from navigation.c ipod_list_layout), each row leads with its kind's glyph (headphones, car,
 * speaker, phone or the Bluetooth rune, from the device's BlueZ Class, bt_class). The connected one
 * is a two-line row: its name over "Connected · LDAC · 72%" (stock's codec, bt_showcoding, and the
 * headset's battery, bt_battery.c) after the Positive dot; another paired one is a key/value row,
 * "Not Connected", or "Connecting…" with the activity spinner on the right column. Centre on a
 * paired row, connected or not, opens its page (bt_detail.c: Connect or Disconnect, Rename, Forget),
 * on one connecting stops that (cancel_connect); a found one pairs, as stock.
 */
#include "navigation.h"

#define BT_HEAD_H 32 /* a section header's item: a whole row, so the list scrolls in whole rows */
#define BT_HEAD_Y 8  /* ... its label's box in it, the text on the row's lower part */
#define BT_HEAD_PX TYPE(15) /* the section header's size (oled-design.md Type) */
#define BT_TWO_H 52         /* the connected device's two-line row (oled-design.md Spacing) */
#define BT_TITLE_Y 4        /* ... its title's box, the baseline 25 px down */
#define BT_TITLE_H 28
#define BT_LINE_Y 30 /* ... its second line's box, the text's top 32 px down */
#define BT_LINE_H 20
#define BT_LINE_PX TYPE(15)   /* Row second line */
#define BT_GLYPH_CX 33        /* a row's kind glyph centred here (Storage > Downloads' column) */
#define BT_TEXT_X 52          /* ... the name from here */
#define BT_ROW_GLYPH 20       /* the glyph's box (tools/art.py ROW_ICON_PX) */
#define BT_DOT 6              /* the Positive dot before "Connected", centred on the second line */
#define BT_DOT_GAP 4          /* ... and the room after it */
#define BT_SPIN 16            /* the spinner's box (list_marks.c SPIN_BOX) */
#define BT_SPIN_GAP 8         /* between "Other Devices" and its spinner */
#define BT_POLL_MS 500        /* the scan's and the class file's checks, the link's */
#define BT_RESULTS_MS 1000    /* a running scan's results read this often (page_poll) */
#define BT_CLASS_WAIT_MS 6000 /* a connect toast waits this long at most for the device's Class */
#define BT_STATE_W 150      /* a paired row's state ("Not Connected"), right-aligned in this much */
#define BT_NOTE_H 52        /* the switch-off line, two lines of 13 px */
#define BT_NOTE_PX TYPE(13) /* the caption's size */
#define BT_ROWS_MAX 48
#define BT_CLASS_FILE "/tmp/q2-btclass"
#define BT_CLASS_MAX 12 /* devices whose Class one fetch asks for */
#define BT_SEEN_FILE "/tmp/q2-btseen"
#define BT_FOUND_MAX 16 /* found devices the page keeps track of */
#define BT_SEEN_MAX 8   /* ... and asks BlueZ about at once (seen_fetch) */
#define BT_SEEN_MS 2000 /* while the page scans, the ones not heard yet asked again this often */
#define BT_STORE "/mnt/data/bluetooth" /* BlueZ's records, ADAPTER/ADDRESS/info */
#define BT_MARK "_btmark"

/* What a row's state label says, from stock's tr_text (navigation.h BT_ROW_*). */
enum { BT_FOUND = BT_ROW_FOUND, BT_PAIRED, BT_CONNECTING, BT_CONNECTED };
/* A mark image's job (its BT_MARK prop). */
enum {
    MARK_NONE,
    MARK_GLYPH,
    MARK_DOT,
    MARK_SPIN,
    MARK_CHEVRON,
    MARK_HEAD_SPIN,
    MARK_CHECK = BT_MARK_CHECK
};

static struct {
    void *view;      /* the Bluetooth page's list while it is open */
    void *head_spin; /* Other Devices' spinner mark */
    void *line;      /* the connected row's second line */
    unsigned poll_at, class_hash, class_seen;
    int scans;          /* scans started for this view */
    int scan_shown;     /* Other Devices' spinner shown at the last poll */
    int linking;        /* a device connecting or connected at the last poll; -1 not yet looked */
    unsigned read_at, listed; /* a scan's results last read (page_poll); the list the rows were built from (listed_hash) */
    char link[24];      /* the device connected at the last look, "" none */
    char link_name[64]; /* ... its name: its record's, else BlueZ's Alias (bt_battery.c), "" none */
    unsigned link_at, link_cls, link_poll_at;
    int link_pending; /* its connect toast not yet shown */
    int low;          /* the battery's steps toasted this connection: 1 at 20%, 2 at 10% */
    char classes[BT_CLASS_MAX * 32 + 2];
    char found[BT_FOUND_MAX][18]; /* the found devices stock listed at the last build */
    int n_found, n_heard;
    unsigned heard[BT_FOUND_MAX]; /* ... those BlueZ had an RSSI for since the page opened */
    unsigned seen_ask, seen_hash, seen_at, seen_file; /* the last ask: its number, what, when */
    int q2scan, q2waits; /* q2ui's scan: 1 asked for, 2 counted down (q2ui_tick); passes it may wait to start */
} bt __attribute__((section(".scratch")));

/* ---- the devices ---- */

/* Device i of pdeq_btshowlist: its MAC (@4, 32 bytes) and name (@0x24); 0 past the list. */
static const char *dev_at(int i, int name) {
    void *list = P(pdeq_btshowlist, 0);
    if (i < 0 || !list || (unsigned)i >= deque_size(list)) return (const char *)0;
    return (const char *)deque_at(list, (unsigned)i) + (name ? 0x24 : 4);
}
/* The devices pdeq_btshowlist holds, in order, as one number: what a row's place names. */
static unsigned listed_hash(void) {
    unsigned h = 5381;
    const char *m;
    for (int i = 0; (m = dev_at(i, 0)); ++i, h = h * 33 + '|')
        while (*m) h = h * 33 + (unsigned char)*m++;
    return h;
}

const char *bt_dev_mac(int i) { return dev_at(i, 0); }
const char *bt_dev_name(int i) { return dev_at(i, 1); }
/* The index in pdeq_btshowlist of mac, -1 for none. */
int bt_dev_find(const char *mac) {
    const char *m;
    for (int i = 0; mac && (m = dev_at(i, 0)); ++i)
        if (!strcmp(m, mac)) return i;
    return -1;
}

/* Device i's name changed in place (a rename, bt_detail.c), as the list shows it till its next
 * read. */
void bt_dev_rename(int i, const char *name) {
    char *at = (char *)dev_at(i, 1);
    if (at) tk_snprintf(at, 0x100, "%s", name);
}

/* Stock's link list's last item is mac at a status other than 3 while the kernel holds a connected
 * ACL link to it (bt_trace.c bt_acl_up): stock's state is behind the link. On 2026-11-19 the Pixel
 * Buds' row said Connecting for minutes while their AAC stream ran (docs/internals.md#bluetooth-page). */
static int link_ahead(const char *mac) {
    char item[32] = "";
    int status = 0, n = g_bluetoothflag ? getBtLinkSize() : 0;
    if (!mac || n <= 0) return 0;
    getBtLinkItem(n - 1, &status, item);
    return status != 3 && !strcmp(item, mac) && bt_acl_up(mac);
}

/* Where device i is in stock's link list: BT_ROW_CONNECTED as the last item at status 3 (stock's
 * own test on its rows) or as the last item whose link the kernel holds (link_ahead),
 * BT_ROW_CONNECTING while a link thread runs for it, else BT_ROW_PAIRED. */
int bt_dev_state(int i) {
    const char *mac = dev_at(i, 0);
    char item[32] = "";
    int status = 0, n = getBtLinkSize();
    if (!mac || n <= 0) return BT_ROW_PAIRED;
    getBtLinkItem(n - 1, &status, item);
    if (strcmp(item, mac)) return BT_ROW_PAIRED;
    return status == 3 || link_ahead(mac) ? BT_ROW_CONNECTED
           : I(bt_connectstatus, 0) == 1  ? BT_ROW_CONNECTING
                                          : BT_ROW_PAIRED;
}

/* A row's stock button (its first child, named by its device's index) and that index, -1 for a
 * row that is no device's. */
static void *row_button(void *item) {
    void *b = item && widget_count_children(item) ? widget_get_child(item, 0) : (void *)0;
    return b && !tk_strcmp(widget_get_type(b), "button") ? b : (void *)0;
}
static int row_dev(void *item) { return widget_get_prop_int(item, "_bt_dev", 0) - 1; }

/* The state stock's label in b says. */
static void *state_label(void *b) {
    for (unsigned i = 0, n = b ? widget_count_children(b) : 0; i < n; ++i) {
        const char *tr = widget_get_prop_str(widget_get_child(b, i), "tr_text", "");
        if (tr && (!tk_strcmp(tr, "msg_connected") || !tk_strcmp(tr, "msg_connecting") ||
                   !tk_strcmp(tr, "msg_paired")))
            return widget_get_child(b, i);
    }
    return (void *)0;
}
static int row_state(void *b) {
    void *l = state_label(b);
    const char *tr = l ? widget_get_prop_str(l, "tr_text", "") : "";
    return !l                                 ? BT_FOUND
           : !tk_strcmp(tr, "msg_connected")  ? BT_CONNECTED
           : !tk_strcmp(tr, "msg_connecting") ? BT_CONNECTING
                                              : BT_PAIRED;
}

/* A paired device row bt_dev_state calls connected whose label stock left saying otherwise:
 * "msg_connected", the label in *l; else 0. Stock labels a row msg_connecting while its link thread runs,
 * whatever its item's status, and msg_connected only at 3 with a codec; on 2026-11-19 (V1JCi) the
 * Pixel Buds' item was 3 from 22:14:49Z with the link up while the thread ran to 22:15:02Z, and the
 * row, rebuilt in that time, said Connecting until something else made stock rebuild it
 * (docs/internals.md#bluetooth-page). */
static const char *state_tr(void *b, int dev, void **l) {
    const char *now = (*l = state_label(b)) ? widget_get_prop_str(*l, "tr_text", "") : (const char *)0;
    if (!now || !tk_strcmp(now, "msg_connected") || bt_dev_state(dev) != BT_ROW_CONNECTED)
        return (const char *)0;
    return "msg_connected";
}
/* A device row whose label stock left behind its state (state_tr): the list is to be rebuilt. */
static int labels_stale(void) {
    void *l;
    for (unsigned i = 0, n = bt.view ? widget_count_children(bt.view) : 0; i < n; ++i) {
        void *item = widget_get_child(bt.view, i), *b = row_button(item);
        if (b && row_dev(item) >= 0 && state_tr(b, row_dev(item), &l)) return 1;
    }
    return 0;
}

/* Stock's codec number n (bt_showcoding, a2dp_eventstate: 1 SBC, 2 AAC, 3 aptX, 4 LDAC, 13 aptX
 * HD) as the page names it, 0 for none. */
static const char *codec_name(int n) {
    switch (n) {
    case 4:
        return "LDAC";
    case 13:
        return "aptX HD";
    case 3:
        return "aptX";
    case 2:
        return "AAC";
    case 1:
        return "SBC";
    }
    return (const char *)0;
}
/* ... as bluealsa names it (GetCodecs, SelectCodec) */
static const char *codec_wire(int n) { return n == 13 ? "aptX-HD" : codec_name(n); }
/* Quality's choice row (g_btcoding's index: LDAC Best, Standard, Auto, aptX HD, aptX, AAC, SBC HQ,
 * SBC) as stock's codec number, Auto as LDAC, the head of its list; 0 for none. */
static int row_codec(int row) {
    return row < 0 ? 0 : row <= 2 ? 4 : row == 3 ? 13 : row == 4 ? 3 : row == 5 ? 2 : row <= 7;
}
/* The codec stock streams with (bt_showcoding, as its status bar's badge reads it), 0 for none. */
const char *bt_codec(void) { return codec_name(I(bt_showcoding, 0)); }

/* ---- kinds, from BlueZ's Class ---- */

/* A device's Class of Device, as the background fetch (class_fetch) wrote it: 0 unknown. */
unsigned bt_class(const char *mac) {
    unsigned n = mac ? strlen(mac) : 0;
    for (const char *p = bt.classes; n && (p = strstr(p, mac)); p += n)
        if ((p == bt.classes || p[-1] == '\n') && p[n] == ' ') {
            unsigned v = 0;
            for (p += n + 1; *p >= '0' && *p <= '9'; ++p) v = v * 10 + (unsigned)(*p - '0');
            return v;
        }
    return 0;
}
/* The kind's glyph stem (tools/art.py BT_ROW_GLYPHS): the Class's major Audio/Video (4) by its
 * minor (Bluetooth's assigned numbers: 1 and 2 headset, 6 headphones, 8 car audio, 5, 7 and 10
 * speakers), major Phone (2), else the rune. */
const char *bt_kind(unsigned cls) {
    unsigned major = cls >> 8 & 0x1f, minor = cls >> 2 & 0x3f;
    if (major == 2) return "phone";
    if (major != 4) return "bt";
    return minor == 1 || minor == 2 || minor == 6    ? "headphones"
           : minor == 8                              ? "car"
           : minor == 5 || minor == 7 || minor == 10 ? "speaker"
                                                     : "bt";
}

/* Whether s is a MAC as stock lists it, AA:BB:CC:DD:EE:FF: it goes into a shell command. */
int bt_mac_ok(const char *s) {
    for (int i = 0; i < 17; ++i) {
        char c = s ? s[i] : 0;
        if (i % 3 == 2
                ? c != ':'
                : !((c >= '0' && c <= '9') || (c >= 'A' && c <= 'F') || (c >= 'a' && c <= 'f')))
            return 0;
    }
    return !s[17];
}

/* demo has no D-Bus client of its own, so BlueZ and bluealsa are asked through dbus-send in a
 * shell. The shell is started on a thread of its own, never the UI thread: system() forks demo,
 * whose copy of a large address space is the UI's time otherwise. The thread waits for the shell,
 * then marks its job done, so the answer's file is read once, after it is whole; a job still
 * running is not started again. 0 when it could not start (no memory, no thread): the caller asks
 * again later. */
struct bt_run {
    bt_job *job;
    char cmd[];
};
static void *bt_run_worker(void *arg) {
    struct bt_run *r = arg;
    system(r->cmd);
    if (r->job) {
        __atomic_add_fetch(&r->job->done, 1, __ATOMIC_RELEASE);
        __atomic_store_n(&r->job->running, 0, __ATOMIC_RELEASE);
    }
    free(r);
    return 0;
}
int bt_shell(bt_job *job, const char *cmd) {
    if (job && __atomic_load_n(&job->running, __ATOMIC_ACQUIRE)) return 0;
    unsigned n = (unsigned)strlen(cmd);
    struct bt_run *r = calloc(1, sizeof *r + n + 1);
    if (!r) return 0;
    r->job = job;
    memcpy(r->cmd, cmd, n + 1);
    if (job) __atomic_store_n(&job->running, 1, __ATOMIC_RELEASE);
    unsigned long t = 0;
    if (pthread_create(&t, 0, bt_run_worker, r) || !t) {
        if (job) __atomic_store_n(&job->running, 0, __ATOMIC_RELEASE);
        free(r);
        return 0;
    }
    pthread_detach(t);
    return 1;
}
unsigned bt_job_done(bt_job *job) { return __atomic_load_n(&job->done, __ATOMIC_ACQUIRE); }
/* the page's asks (class_fetch, seen_fetch) and the connected device's codecs' (sink_fetch) */
static bt_job class_job __attribute__((section(".scratch"))),
    seen_job __attribute__((section(".scratch"))), sink_job __attribute__((section(".scratch")));

/* BlueZ's Class for each listed device, in the background, written whole to BT_CLASS_FILE as
 * "MAC CLASS" lines: started when the list holds a device the file has not answered for. */
static void class_fetch(void) {
    char cmd[BT_CLASS_MAX * 220 + 128];
    unsigned at = 0, hash = 5381, k = 0;
    const char *m;
    for (int i = 0; (m = dev_at(i, 0)) && k < BT_CLASS_MAX; ++i)
        if (bt_mac_ok(m) && !bt_class(m) && !strstr(bt.classes, m)) {
            for (const char *p = m; *p; ++p) hash = hash * 33 + (unsigned char)*p;
            ++k;
        }
    if (!k || hash == bt.class_hash) return; /* nothing new, or asked already */
    at = (unsigned)tk_snprintf(cmd, sizeof cmd, "(f=" BT_CLASS_FILE "; { cat $f 2>/dev/null; ");
    k = 0;
    for (int i = 0; (m = dev_at(i, 0)) && k < BT_CLASS_MAX && at < sizeof cmd - 260; ++i) {
        if (!bt_mac_ok(m) || strstr(bt.classes, m)) continue;
        char path[24];
        tk_snprintf(path, sizeof path, "%s", m);
        for (char *p = path; *p; ++p)
            if (*p == ':') *p = '_';
        at += (unsigned)tk_snprintf(
            cmd + at, sizeof cmd - at,
            "c=$(dbus-send --system --print-reply --dest=org.bluez /org/bluez/hci0/dev_%s "
            "org.freedesktop.DBus.Properties.Get string:org.bluez.Device1 string:Class 2>/dev/null"
            " | sed -n 's/.*uint32 //p'); [ -n \"$c\" ] && echo \"%s $c\"; ",
            path, m);
        ++k;
    }
    tk_snprintf(cmd + at, sizeof cmd - at, "} > $f.new; mv -f $f.new $f) >/dev/null 2>&1");
    if (bt_shell(&class_job, cmd)) bt.class_hash = hash;
}

/* BT_CLASS_FILE into bt.classes when it changed; 1 then. */
static int class_read(void) {
    char raw[sizeof bt.classes + 1];
    unsigned n = state_read(BT_CLASS_FILE, raw, sizeof raw), hash = 5381;
    for (unsigned i = 1; i <= n; ++i) hash = hash * 33 + (unsigned char)raw[i];
    if (hash == bt.class_seen) return 0;
    bt.class_seen = hash;
    tk_snprintf(bt.classes, sizeof bt.classes, "%s", raw + 1);
    return 1;
}

/* ---- what the scan heard ----
 * libbtctl's btctl_get_device_list (stock's list, getSearchResult) is every Device1 BlueZ has,
 * paired first by its Paired: a record BlueZ loaded from its store with no key (a pairing that
 * never finished, kept as Trusted) is listed as found wherever the radio is. On 2026-11-20 (V1N6i)
 * an Arctis Nova Pro Wireless (20:AF:1B:18:FD:03) was in Other Devices 20 miles from it. So a found
 * device is listed once BlueZ has had an RSSI for it while the page is open (a discovery heard it),
 * and a stored record with no key, not paired, not connected and not heard is removed through
 * BlueZ (Adapter1.RemoveDevice), never while a connect runs (docs/internals.md#bluetooth-page). */

static unsigned mac_hash(const char *m) {
    unsigned h = 5381;
    for (; m && *m; ++m) h = h * 33 + (unsigned)(*m >= 'a' && *m <= 'f' ? *m - 32 : *m);
    return h;
}
static int heard(const char *mac) {
    unsigned h = mac_hash(mac);
    for (int i = 0; i < bt.n_heard; ++i)
        if (bt.heard[i] == h) return 1;
    return 0;
}

static int busy(void);
/* Asked of BlueZ in the background, for each found device not heard yet, into BT_SEEN_FILE whole:
 * "#N" (this ask's number), then "MAC seen" for one with an RSSI, "MAC removed" for a stale record
 * removed. Asked again when that set changes, a removal becomes allowed, or every BT_SEEN_MS while
 * the page scans. */
static void seen_fetch(unsigned now) {
    char cmd[BT_SEEN_MAX * 600 + 320], link[32] = "";
    int status = 0, n = getBtLinkSize(), k = 0;
    int rm = I(bt_connectstatus, 0) != 1 && !busy(); /* a pairing or connect runs: nothing removed */
    unsigned hash = 5381u + (unsigned)rm, at;
    if (n > 0) getBtLinkItem(n - 1, &status, link); /* the link thread's target: never removed */
    for (int i = 0; i < bt.n_found && k < BT_SEEN_MAX; ++i)
        if (!heard(bt.found[i])) hash = hash * 33 + mac_hash(bt.found[i]), ++k;
    if (!k || (hash == bt.seen_hash && !(bt_searchtime > 1 && now - bt.seen_at >= BT_SEEN_MS)))
        return;
    if (__atomic_load_n(&seen_job.running, __ATOMIC_ACQUIRE)) return; /* the last ask runs yet */
    at = (unsigned)tk_snprintf(cmd, sizeof cmd,
                               "(f=" BT_SEEN_FILE "; g() { dbus-send --system --print-reply "
                               "--dest=org.bluez $1 org.freedesktop.DBus.Properties.Get "
                               "string:org.bluez.Device1 string:$2 2>/dev/null; }; { echo '#%u'; ",
                               bt.seen_ask + 1);
    k = 0;
    for (int i = 0; i < bt.n_found && k < BT_SEEN_MAX; ++i) {
        const char *m = bt.found[i];
        if (heard(m)) continue;
        ++k;
        char path[24];
        tk_snprintf(path, sizeof path, "%s", m);
        for (char *p = path; *p; ++p)
            if (*p == ':') *p = '_';
        at += (unsigned)tk_snprintf(cmd + at, sizeof cmd - at,
                                    "d=/org/bluez/hci0/dev_%s; if g $d RSSI >/dev/null; then "
                                    "echo '%s seen'; ",
                                    path, m);
        if (rm && strcmp(m, link))
            at += (unsigned)tk_snprintf(
                cmd + at, sizeof cmd - at,
                "elif g $d Paired | grep -q false && g $d Connected | grep -q false && "
                "i=$(ls " BT_STORE "/*/%s/info 2>/dev/null) && [ -n \"$i\" ] && "
                "! grep -q 'Key\\]' $i && dbus-send --system --print-reply --dest=org.bluez "
                "/org/bluez/hci0 org.bluez.Adapter1.RemoveDevice objpath:$d >/dev/null 2>&1; "
                "then echo '%s removed'; ",
                m, m);
        at += (unsigned)tk_snprintf(cmd + at, sizeof cmd - at, "fi; ");
    }
    tk_snprintf(cmd + at, sizeof cmd - at, "} > $f.new; mv -f $f.new $f) >/dev/null 2>&1");
    if (bt_shell(&seen_job, cmd)) bt.seen_hash = hash, bt.seen_at = now, ++bt.seen_ask;
}

/* BT_SEEN_FILE's answer to the last ask, once: each device seen joins the heard set, each record
 * removed is logged. 1 when a device was heard that was not before. */
static int seen_read(void) {
    char raw[BT_SEEN_MAX * 32 + 32], tok[16];
    unsigned n = state_read(BT_SEEN_FILE, raw, sizeof raw), h = 5381, t;
    int added = 0;
    for (unsigned i = 1; i <= n; ++i) h = h * 33 + (unsigned char)raw[i];
    t = (unsigned)tk_snprintf(tok, sizeof tok, "\n#%u\n", bt.seen_ask);
    if (!bt.seen_ask || h == bt.seen_file || strncmp(raw, tok, t)) return 0;
    bt.seen_file = h;
    for (const char *p = raw + t, *next; *p; p = next) {
        char mac[18], s[96];
        for (next = p; *next && *next++ != '\n';) {}
        tk_snprintf(mac, sizeof mac, "%s", p);
        if (!bt_mac_ok(mac)) continue;
        if (!strncmp(p + 17, " seen\n", 6) && !heard(mac) && bt.n_heard < BT_FOUND_MAX) {
            bt.heard[bt.n_heard++] = mac_hash(mac);
            added = 1;
        } else if (!strncmp(p + 17, " removed\n", 9)) {
            const char *name = dev_at(bt_dev_find(mac), 1);
            tk_snprintf(s, sizeof s, "stale record removed %s \"%s\"", mac, name ? name : "");
            ulog('I', "bt", s);
        }
    }
    return added;
}

/* ---- the connected device's codecs ---- */

/* What the device connected now can take (docs/internals.md#bluetooth-page, Quality), as Android
 * learns its peer's endpoints before it picks: bluealsa's org.bluealsa.PCM1.GetCodecs on the
 * device's A2DP source PCM names each codec of the sink endpoints BlueZ found on it (bluez-alsa
 * 4.0.0 bluealsa_pcm_get_codecs). Asked in the background once the link streams (a codec in
 * bt_showcoding: the PCM is there), written to BT_CODECS_FILE as the MAC's line then one name a
 * line (with the inner keys, which sink_read passes), and asked again once an ask has ended
 * unanswered, at most every BT_CODECS_RETRY_MS and BT_CODECS_TRIES times; the file is read once per
 * ask, as it ends. */
#define BT_CODECS_FILE "/tmp/q2bt.codecs"
#define BT_CODECS_RETRY_MS 3000
#define BT_CODECS_TRIES 10
static struct {
    char mac[24];  /* the device they are for, "" none */
    unsigned bits; /* stock's codec numbers it takes, as 1 << n; 0 not known */
    unsigned asked_at;
    int tries;
    unsigned seen; /* sink_job's runs ended as last read */
} sink __attribute__((section(".scratch")));

/* Whether the device connected now is known to lack stock's codec n. SBC every sink has (A2DP
 * makes it mandatory), so the ordered walk always ends there. */
static int sink_lacks(int n) { return sink.bits && n > 1 && !(sink.bits & 1u << n); }

static int sink_fetch(const char *mac) {
    char cmd[640], dev[24];
    tk_snprintf(dev, sizeof dev, "%s", mac);
    for (char *p = dev; *p; ++p)
        if (*p == ':') *p = '_';
    tk_snprintf(
        cmd, sizeof cmd,
        "(p=$(dbus-send --system --print-reply --dest=org.bluealsa /org/bluealsa "
        "org.freedesktop.DBus.ObjectManager.GetManagedObjects 2>/dev/null | sed -n "
        "'s|.*object path \"\\(/org/bluealsa/[A-Za-z0-9_]*/dev_%s/a2dpsrc/[A-Za-z0-9_/]*\\)\".*"
        "|\\1|p' | head -n 1); [ -n \"$p\" ] && { echo %s; dbus-send --system --print-reply "
        "--dest=org.bluealsa \"$p\" org.bluealsa.PCM1.GetCodecs 2>/dev/null | sed -n "
        "'s/^ *string \"\\([A-Za-z0-9-]*\\)\"$/\\1/p'; } >" BT_CODECS_FILE
        ".new && mv -f " BT_CODECS_FILE ".new " BT_CODECS_FILE ") >/dev/null 2>&1",
        dev, mac);
    return bt_shell(&sink_job, cmd);
}

/* BT_CODECS_FILE's answer for sink.mac into sink.bits: the five codecs Quality offers, by name
 * (GetCodecs's inner keys, "Capabilities" and the like, match none). */
static void sink_read(void) {
    char raw[512];
    state_read(BT_CODECS_FILE, raw, sizeof raw);
    unsigned n = strlen(sink.mac), bits = 0;
    if (!n || strncmp(raw + 1, sink.mac, n) || raw[1 + n] != '\n') return;
    for (char *line = raw + 1 + n + 1, *end; *line; line = end) {
        for (end = line; *end && *end != '\n'; ++end) {}
        char c = *end;
        *end = 0;
        for (int k = 1; k <= 13; ++k)
            if (codec_wire(k) && !tk_strcmp(line, codec_wire(k))) bits |= 1u << k;
        if (c) ++end;
    }
    if (bits) sink.bits = bits | 1u << 1;
}

/* Each link_poll: mac connected now, "" none. */
static void sink_poll(const char *mac, unsigned now) {
    if (strcmp(mac, sink.mac)) {
        tk_snprintf(sink.mac, sizeof sink.mac, "%s", bt_mac_ok(mac) ? mac : "");
        sink.bits = 0, sink.tries = 0;
    }
    if (!sink.mac[0] || sink.bits) return;
    unsigned done = bt_job_done(&sink_job);
    if (done != sink.seen) sink.seen = done, sink_read(); /* an ask ended: its answer */
    if (sink.bits || sink.tries >= BT_CODECS_TRIES || I(bt_showcoding, 0) <= 0 ||
        (sink.tries && now - sink.asked_at < BT_CODECS_RETRY_MS))
        return;
    if (sink_fetch(sink.mac)) sink.asked_at = now, ++sink.tries;
}

/* ---- the page's rows ---- */

static int view_gone(void *ctx, void *event) {
    (void)event;
    if (bt.view == ctx) bt.view = bt.head_spin = bt.line = (void *)0, bt.scans = 0;
    return 0;
}
static int head_spin_gone(void *ctx, void *event) {
    (void)event;
    if (bt.head_spin == ctx) bt.head_spin = (void *)0;
    return 0;
}
static int line_gone(void *ctx, void *event) {
    (void)event;
    if (bt.line == ctx) bt.line = (void *)0;
    return 0;
}

/* A label in the section header's type (15 px Text Tertiary) on the text column. */
static void *small_label(void *parent, int x, int y, int w, int h, int px) {
    void *l = label_create(parent, x, y, w, h);
    face_font(l, theme()->font[ROLE_META], px, FONT_ROW);
    widget_set_prop_str(l, "style:normal:text_align_h", "left");
    widget_set_prop_str(l, "style:disable:text_align_h", "left");
    text_ink(l, 0xff000000u | tok(TOK_TERTIARY), 4);
    return l;
}

/* A mark image in parent, drawn by bt_ui_paint (guest_ui.c's checks too). */
void *bt_mark(void *parent, const char *name, int kind, int x, int y, int w, int h) {
    void *m = widget_lookup(parent, name, 0);
    if (!m) {
        m = image_create(parent, x, y, w, h);
        widget_set_name(m, name);
        widget_set_prop_int(m, BT_MARK, kind);
    } else
        widget_move_resize(m, x, y, w, h);
    widget_set_visible(m, 1, 0);
    return m;
}

/* A section header: item h high, a heading the wheel passes, its label text in Text Tertiary at
 * px (stock's own centred label, if any, hidden). */
static void *head_px(void *view, void *item, const char *text, int px) {
    if (!item) {
        item = list_item_create(view, 0, 0, 375, BT_HEAD_H);
        widget_use_style(item, "s_listitem_black");
    }
    for (unsigned i = 0, n = widget_count_children(item); i < n; ++i)
        widget_set_visible(widget_get_child(item, i), 0, 0);
    widget_resize(item, I(item, W_W), BT_HEAD_H);
    widget_set_enable(item, 0);
    widget_set_prop_int(item, "_bt_head", 1);
    void *l = small_label(item, geom_val(V_TEXT_X), BT_HEAD_Y, 240, BT_HEAD_H - BT_HEAD_Y, px);
    widget_set_name(l, "_bt_head");
    widget_set_text_utf8(l, text);
    return item;
}
/* ... in the section header's type */
static void *head(void *view, void *item, const char *text) {
    return head_px(view, item, text, BT_HEAD_PX);
}

/* Stock's click on device dev's row on the Bluetooth page, as a tap at x: connect a paired or
 * found one, disconnect the connected one (x under 276), or stock's Unpair? (x 276 on, a paired
 * one's right end). 0 with no such row. */
int bt_stock_click(int dev, int x) {
    bt_trace_why("tap", dev_at(dev, 0)); /* the connect it may start, in the log (bt_trace.c) */
    for (unsigned i = 0, n = bt.view ? widget_count_children(bt.view) : 0; i < n; ++i) {
        void *item = widget_get_child(bt.view, i), *b = row_button(item);
        if (b && row_dev(item) == dev) {
            int event[16] = { 0 }; /* pointer_event_t: x at +0x18, the one field stock reads */
            event[6] = x;
            btmenu_item_on_click(b, event);
            return 1;
        }
    }
    return 0;
}

/* The connect to mac in flight, cancelled: the link thread's target marked failed (4), as
 * headset.c's adoption ends its wait, so the thread's 100 ms loop (0x4fc930) ends and
 * checkBluetoothStatus takes stock's failure path, which shows nothing: btDisconnect (BlueZ fails
 * the pending Connect as cancelled), the link list cleared, bt_connectstatus 0. Only while the
 * thread runs for mac with its Connect started (5): at 1 the thread has yet to set 5 over it.
 * 1 when it was cancelled. ponytail: BlueZ's Disconnect cancels its own Connect; whether the
 * controller's page in progress stops with it is for a Q2 to show. */
static int cancel_connect(const char *mac) {
    char target[32] = "";
    int status = 0, n = getBtLinkSize();
    if (!mac || I(bt_connectstatus, 0) != 1 || n <= 0) return 0;
    getBtLinkItem(n - 1, &status, target);
    if (status != 5 || strcmp(target, mac)) return 0;
    updateBtLinkStatus(4, target);
    return 1;
}

/* A device row's Centre or tap (docs/oled-design.md#bluetooth): a paired device, connected or not,
 * opens its page, where Connect is the first row for one that is not; one connecting stops
 * connecting (stock ignores a tap then); a found one pairs, as stock's click. */
static int row_click(void *b, void *event) {
    (void)event;
    void *item = P(b, W_PARENT);
    int dev = item ? row_dev(item) : -1, state = row_state(b);
    if (dev < 0) return 0;
    if (state == BT_CONNECTED || state == BT_PAIRED)
        bt_detail_open(dev);
    else if (state != BT_CONNECTING || !cancel_connect(dev_at(dev, 0)))
        bt_stock_click(dev, 0);
    return STOP;
}

/* ringnav_keylong: Play/Pause held on a paired device's row opens its page; 1 when it was one. */
int bt_ui_hold(void *top) {
    if (!bt.view || top != window_of(bt.view)) return 0;
    int at = widget_get_prop_int(bt.view, "_ringnav_index", -1);
    void *item = at >= 0 ? widget_get_child(bt.view, (unsigned)at) : (void *)0,
         *b = item ? row_button(item) : (void *)0;
    if (!b || row_dev(item) < 0 || row_state(b) == BT_FOUND) return 0;
    bt_detail_open(row_dev(item));
    return 1;
}

/* Bluetooth off: what turning it on gives, under the Name row. */
static void off_note(void *view) {
    void *item = list_item_create(view, 0, 0, 375, BT_NOTE_H);
    widget_use_style(item, "s_listitem_black");
    widget_set_enable(item, 0);
    widget_set_prop_int(item, "_bt_head", 1);
    void *l = small_label(item, geom_val(V_TEXT_X), 6, 375 - 2 * geom_val(V_TEXT_X), BT_NOTE_H - 6,
                          BT_NOTE_PX);
    widget_set_prop_int(l, "line_wrap", 1);
    widget_set_prop_int(l, "word_wrap", 1);
    widget_set_prop_str(l, "style:normal:text_align_v", "top");
    widget_set_text_utf8(l, "Turn on to play to headphones, a car or a speaker.");
}

/* Stock's Quality row, its title over the choice (a second line), as a key/value row: Quality and
 * the choice's name (Auto) on the right column before its chevron. */
/* A row's title: the first hscroll_label in its button b. */
static void *row_title(void *b) {
    for (unsigned i = 0, n = b ? widget_count_children(b) : 0; i < n; ++i)
        if (!tk_strcmp(widget_get_type(widget_get_child(b, i)), "hscroll_label"))
            return widget_get_child(b, i);
    return (void *)0;
}
static void quality_value(void *item) {
    void *b = row_button(item), *title = (void *)0, *choice = (void *)0;
    for (unsigned i = 0, n = b ? widget_count_children(b) : 0; i < n; ++i) {
        void *c = widget_get_child(b, i);
        if (tk_strcmp(widget_get_type(c), "hscroll_label")) continue;
        if (!title)
            title = c;
        else if (!choice)
            choice = c;
    }
    if (!title || !choice) return;
    char s[64] = "";
    widget_get_text_utf8(choice, s, sizeof s);
    widget_destroy(choice);
    list_value(title, "Quality", s);
}

/* ringnav_btrows, after stock's builder and pod_rows' two (Resume on Connect, Link Volume, last):
 * the page's order. Stock's rows by place: 0 the switch, 1 Bluetooth Name (bt_name.c's Name row),
 * 2 Quality, 3 Headset Volume, 4 Scan now; then My Devices' header and rows, Other Devices' header
 * and rows (stock's headers only where it lists such a device). Any other count is left as built.
 */
void bt_page(void *view, int on) {
    bt_name_row(view);
    unsigned n = widget_count_children(view);
    bt.listed = listed_hash(); /* the list these rows were built from */
    if (bt.view != view) {
        bt.view = view, bt.scans = 0, bt.linking = -1;
        bt.n_heard = 0, bt.seen_hash = 0; /* what the scan heard, from this open on */
        bt_trace_page(); /* "page opened", and its found devices logged afresh */
        widget_on(view, EVT_DESTROY, view_gone, view);
    }
    if (!on) {
        off_note(view);
        return;
    }
    if (n < 7 || n - 2 > BT_ROWS_MAX - 8) return;
    void *rows[BT_ROWS_MAX], *quality = widget_get_child(view, 2),
                             *volume = widget_get_child(view, 3), *scan = widget_get_child(view, 4);
    void *unheard[BT_ROWS_MAX];
    unsigned k = 0, other = 0, u = 0;
    bt.n_found = 0; /* the found devices listed, then BlueZ asked which a scan heard */
    for (unsigned i = 5; i < n - 2; ++i) {
        void *b = row_button(widget_get_child(view, i));
        const char *m = b && !state_label(b) ? dev_at(atoi(widget_get_prop_str(b, "name", "")), 0)
                                             : (const char *)0;
        if (m && bt_mac_ok(m) && bt.n_found < BT_FOUND_MAX)
            tk_snprintf(bt.found[bt.n_found++], sizeof bt.found[0], "%s", m);
    }
    seen_fetch(time_now_ms());
    seen_read();
    rows[k++] = widget_get_child(view, 0);
    rows[k++] = widget_get_child(view, 1);
    for (unsigned i = 5; i < n - 2; ++i) {
        void *item = widget_get_child(view, i), *b = row_button(item);
        if (b && !state_label(b) &&
            !heard(dev_at(atoi(widget_get_prop_str(b, "name", "")), 0))) { /* not in range */
            unheard[u++] = item;
            continue;
        }
        if (b) {
            widget_set_prop_int(item, "_bt_dev", atoi(widget_get_prop_str(b, "name", "")) + 1);
            widget_off_by_func(b, EVT_CLICK, (void *)PEQ_AT_btmenu_item_on_click, b);
            widget_on(b, EVT_CLICK, row_click, b);
        } else { /* stock's header (btmenu_msginfo_layout): paired first, then found */
            other = k > 2;
            for (unsigned j = 2; j < k; ++j)
                if (!widget_get_prop_int(rows[j], "_bt_head", 0)) other = 1;
            head(view, item, other ? "Other Devices" : "My Devices");
            if (other) bt.head_spin = 0, other = k + 1;
        }
        rows[k++] = item;
    }
    if (!other) rows[k++] = head(view, (void *)0, "Other Devices");
    /* Other Devices' spinner: beside its label (bt_ui_paint measures it) */
    void *oh = rows[other ? other - 1 : k - 1];
    bt.head_spin = bt_mark(oh, "_bt_headspin", MARK_HEAD_SPIN, 0, 0, 375, BT_HEAD_H);
    widget_on(bt.head_spin, EVT_DESTROY, head_spin_gone, bt.head_spin);
    rows[k++] = head(view, (void *)0, "Audio");
    void *guest = list_row_into(view, 0, guest_settings_open, 0);
    list_value(guest, "Guest Play", guest_ui_value());
    rows[k++] = P(P(guest, W_PARENT), W_PARENT);
    quality_value(quality);
    rows[k++] = quality;
    rows[k++] = volume;
    rows[k++] = widget_get_child(view, n - 2);
    rows[k++] = widget_get_child(view, n - 1);
    for (unsigned j = k - 2; j < k; ++j) widget_resize(rows[j], I(rows[j], W_W), SET_STOCK_ROW);
    for (unsigned i = 0; i < k; ++i) widget_restack(rows[i], i);
    widget_destroy(scan);
    for (unsigned i = 0; i < u; ++i) widget_destroy(unheard[i]);
    class_read();
    class_fetch();
}

/* ---- Quality: Adapt to Signal, then the codecs (docs/oled-design.md#bluetooth) ----
 * [Q2POD] BTSTEPDOWN, off unless 1 (a Q2 that never saved the key is off): under Auto,
 * q2guard moves a link the radio cannot keep up with from aptX HD or aptX straight to SBC, and back
 * once it is clean, and on SBC (Auto's, or SBC HQ or SBC chosen) lowers its bitpool and on AAC
 * (Auto's) its peak, and raises them again (q2bt.c, docs/internals.md#bluetooth-link-monitor);
 * q2guard reads the key itself
 * (q2bt.h's BT_STEP_KEY, the same name: q2guard's headers are not the payload's).
 * Added before the picker's first layout and found again by its item's _bt_step on every later
 * one; no state of its own. quality_order puts it first, over a CODEC header and the choices. */
#define BT_STEP_KEY "BTSTEPDOWN"
#define BT_STEP_TITLE "Adapt to Signal"
#define BT_STEP_CAPTION "On Auto or SBC, lowers quality to keep playing when the signal is weak"
static int step_on(void) { return config_value("Q2POD", BT_STEP_KEY, 2, 0); }
static int step_click(void *label, void *event) {
    int on = !step_on();
    toolsWriteConfig("/mnt/data/config.ini", "Q2POD", BT_STEP_KEY, on ? "1" : "0");
    list_toggle(label, BT_STEP_TITLE, on);
    return 0;
}
static void step_row(void *view) {
    for (unsigned i = 0, n = widget_count_children(view); i < n; ++i)
        if (widget_get_prop_int(widget_get_child(view, i), "_bt_step", 0)) return;
    void *label = list_row(view, 0, 0, 0), *b = P(label, W_PARENT);
    widget_set_prop_int(P(b, W_PARENT), "_bt_step", 1);
    widget_on(b, EVT_CLICK, step_click, label);
    list_toggle(label, BT_STEP_TITLE, step_on());
    caption_row(label, BT_STEP_CAPTION);
}

/* The picker's order: Adapt to Signal, a CODEC header (13 px Text Tertiary caps, a heading the
 * wheel passes), then Auto and the fixed choices from best to SBC. Stock's rows keep their buttons'
 * names, g_btcoding's index (0 LDAC Best to 7 SBC), which its click reads (atoi of the name) and
 * its check finds them by, so only their places change; the picker opens on the checked row
 * wherever it is (contexts.inc CHOICE). Restacked on every layout; the header is made once, found
 * again by its item's _bt_codec. */
#define BT_AUTO 2 /* Auto's row in g_btcoding */
#define BT_CODEC_PX TYPE(13)
static void quality_order(void *view) {
    static const signed char order[] = { BT_AUTO, 0, 1, 3, 4, 5, 6, 7 };
    void *step = (void *)0, *codec = (void *)0, *rows[sizeof order] = { 0 };
    for (unsigned i = 0, n = widget_count_children(view); i < n; ++i) {
        void *item = widget_get_child(view, i), *b = row_button(item);
        if (widget_get_prop_int(item, "_bt_step", 0))
            step = item;
        else if (widget_get_prop_int(item, "_bt_codec", 0))
            codec = item;
        else if (b) {
            int k = atoi(widget_get_prop_str(b, "name", ""));
            if (k >= 0 && k < (int)sizeof order) rows[k] = item;
        }
    }
    if (!codec) {
        codec = head_px(view, (void *)0, "CODEC", BT_CODEC_PX);
        widget_set_prop_int(codec, "_bt_codec", 1);
    }
    I(codec, W_H) = BT_HEAD_H; /* before the layout, as bt_rows_pre's own headers */
    unsigned at = 0;
    if (step) widget_restack(step, at++);
    widget_restack(codec, at++);
    for (unsigned i = 0; i < sizeof order; ++i)
        if (rows[(int)order[i]]) widget_restack(rows[(int)order[i]], at++);
}

/* Before stock's layout: each device row's height, the connected one two lines (navigation.c maps
 * the others to the set's row as stock's settings rows); on Quality's picker, the step-down's row
 * and the picker's order. */
void bt_rows_pre(void *view) {
    void *win = window_of(view);
    if (win && !tk_strcmp(widget_get_prop_str(win, "name", ""), "btquality_page")) {
        step_row(view);
        quality_order(view);
        return;
    }
    for (unsigned i = 0, n = widget_count_children(view); i < n; ++i) {
        void *item = widget_get_child(view, i), *b = row_button(item), *l;
        const char *tr = b && row_dev(item) >= 0 ? state_tr(b, row_dev(item), &l) : (const char *)0;
        if (tr) widget_set_prop_str(l, "tr_text", tr); /* connected by bt_dev_state: says so */
        if (b && row_dev(item) >= 0 && !state_label(b)) /* a found device the page shows */
            bt_trace_shown(dev_at(row_dev(item), 0));
        if (widget_get_prop_int(item, "_bt_head", 0) && I(item, W_H) != BT_NOTE_H)
            I(item, W_H) = BT_HEAD_H;
        else if (row_dev(item) >= 0 && b && I(item, W_H) == SET_STOCK_ROW)
            I(item, W_H) = row_state(b) == BT_CONNECTED ? BT_TWO_H : geom_val(V_SET_ROW);
    }
}

/* The connected row's second line: "Connected · LDAC · 72%", what is known of it. */
static void connected_line(char *s, unsigned n) {
    const char *codec = bt_codec();
    int level = bt_headset_battery();
    unsigned at = (unsigned)tk_snprintf(s, n, "Connected");
    if (codec) at += (unsigned)tk_snprintf(s + at, n - at, " · %s", codec);
    if (level >= 0 && at < n) tk_snprintf(s + at, n - at, " · %d%%", level > 100 ? 100 : level);
}

/* After the layout: a device row's children on the design's columns. */
static void dress(void *item, void *b) {
    int h = I(item, W_H), right = I(b, W_W) - geom_val(V_RIGHT_X), state = row_state(b);
    void *title = (void *)0, *line = state_label(b);
    for (unsigned i = 0, n = widget_count_children(b); i < n; ++i) {
        void *c = widget_get_child(b, i);
        const char *type = widget_get_type(c);
        if (!tk_strcmp(type, "hscroll_label") && c != line && !title)
            title = c;
        else if (!tk_strcmp(type, "image") && !widget_get_prop_int(c, BT_MARK, 0))
            widget_set_visible(c, 0,
                               0); /* stock's icon, and a paired row's Unpair (navbar_delete) */
    }
    bt_mark(b, "_bt_glyph", MARK_GLYPH, BT_GLYPH_CX - BT_ROW_GLYPH / 2, (h - BT_ROW_GLYPH) / 2,
            BT_ROW_GLYPH, BT_ROW_GLYPH);
    int end = right - (state == BT_FOUND ? 0 : VALUE_CHEVRON_GAP);
    if (state == BT_PAIRED || state == BT_CONNECTED)
        bt_mark(b, "_bt_chevron", MARK_CHEVRON, right - BT_SPIN, (h - BT_SPIN) / 2, BT_SPIN,
                BT_SPIN);
    if (state == BT_CONNECTING)
        bt_mark(b, "_bt_spin", MARK_SPIN, right - BT_SPIN, (h - BT_SPIN) / 2, BT_SPIN, BT_SPIN);
    if (state == BT_CONNECTED && title && line) {
        char s[64];
        widget_move_resize(title, BT_TEXT_X - 2, BT_TITLE_Y, end - BT_TEXT_X, BT_TITLE_H);
        int x = BT_TEXT_X + BT_DOT + BT_DOT_GAP;
        bt_mark(b, "_bt_dot", MARK_DOT, BT_TEXT_X, BT_LINE_Y, BT_DOT, BT_LINE_H);
        widget_move_resize(line, x - 2, BT_LINE_Y, end - x, BT_LINE_H);
        face_font(line, theme()->font[ROLE_META], BT_LINE_PX, FONT_ROW);
        text_ink(line, 0xff000000u | tok(TOK_TERTIARY), INK_LABEL);
        connected_line(s, sizeof s);
        widget_set_text_utf8(line, s);
        if (bt.line != line) {
            bt.line = line;
            widget_on(line, EVT_DESTROY, line_gone, line);
        }
        return;
    }
    if (title) widget_move_resize(title, BT_TEXT_X - 2, 0, end - BT_TEXT_X, h);
    if (line) { /* the state as a value, ending before the chevron or the spinner */
        widget_move_resize(line, end - BT_STATE_W, 0, BT_STATE_W, h);
        theme_font(line, ROLE_TITLE, FONT_ROW);
        widget_set_prop_str(line, "style:normal:text_align_h", "right");
        unsigned ink = theme()->set_value_ink[ipod_light()];
        if (ink) text_ink(line, 0xff000000u | ink, INK_LABEL);
        if (title) widget_resize(title, end - BT_STATE_W - 8 - BT_TEXT_X, I(title, W_H));
    }
}

static void quality_rows(void *view), quality_lacks(void *view);
void bt_rows_post(void *view) {
    void *win = window_of(view);
    if (win && !tk_strcmp(widget_get_prop_str(win, "name", ""), "btquality_page")) {
        quality_rows(view);
        return;
    }
    for (unsigned i = 0, n = widget_count_children(view); i < n; ++i) {
        void *item = widget_get_child(view, i), *b = row_button(item);
        if (b && row_dev(item) >= 0) dress(item, b);
    }
}

/* The list's own page: Settings > Bluetooth's, or its Quality picker's. */
int bt_list(void *list) {
    if (!list) return 0;
    if (!tk_strcmp(widget_get_prop_str(list, "name", ""), "list_view_bluetooth")) return 1;
    void *win = window_of(list);
    return win && !tk_strcmp(widget_get_prop_str(win, "name", ""), "btquality_page");
}

/* ---- Quality (P5): each choice's bit rate, Auto's caption ---- */

/* Quality's choice i (g_btcoding's index: LDAC Best, Standard, Auto, aptX HD, aptX, AAC, SBC HQ,
 * SBC): its bit rate at 48 kHz where the codec fixes one (bt_detail.c bt_kbps), 0
 * for Auto, AAC and SBC, whose rate the link sets. */
static unsigned choice_kbps(int i) {
    static const unsigned short kbps[] = { 990, 660, 0, 576, 384, 0, 0, 0 };
    return i >= 0 && i < (int)(sizeof kbps / sizeof *kbps) ? kbps[i] : 0;
}
#define BT_AUTO_CAPTION "Steps with the signal, 330 to 990 kbps"
#define BT_KBPS_W 90    /* a choice's rate, right-aligned on the right column */
#define BT_CHECK_GAP 24 /* ... and on the checked row before the check: its 12 px and the gap */

/* Auto's caption for the headset connected now. bluealsa offers aptX HD, aptX, AAC and SBC beside
 * LDAC whatever the choice, so a headset without LDAC streams one of those on Auto, at that codec's
 * own rate (a Bowers & Wilkins Px7: aptX HD, a fixed 576 kbps): the caption names it, "Best this
 * headset supports: aptX HD, 576 kbps". On LDAC, with no device connected (stock's rule: the link
 * list's last item at status 3) or no codec known, it says what Auto does. s holds it, 48
 * bytes (caption_row copies it). */
static struct {
    void *view;    /* the picker's list while it is open */
    int coding;    /* g_btcoding its rates were placed for */
    unsigned lack; /* lack_key its marks were set for */
} quality __attribute__((section(".scratch")));
static const char *auto_caption(char *s) {
    const char *codec = bt_codec();
    char item[32] = "";
    int status = 0, n = getBtLinkSize();
    if (n > 0) getBtLinkItem(n - 1, &status, item);
    if (!codec || status != 3 || !tk_strcmp(codec, "LDAC")) return BT_AUTO_CAPTION;
    unsigned kbps = bt_kbps(codec, bt_headset_rate()),
             k = (unsigned)tk_snprintf(s, 48, "Best this headset supports: %s", codec);
    if (kbps && k < 48) tk_snprintf(s + k, 48 - k, ", %u kbps", kbps);
    return s;
}

/* The choices whose codec the device connected now lacks (sink_lacks: LDAC Best and Standard,
 * aptX HD, aptX, AAC; Auto and SBC never) are marked as the Library's grayed rows are
 * (docs/server-library.md#grayed-rows: #666666 off the selection, the selection's own ink at 70%
 * on it, bt_quality_dim) and still pickable, the pick kept for the next device; each says why
 * under it while selected, "BMW 6R81 doesn't support aptX HD: using AAC" (the device's name, UTF-8,
 * cut at BT_LACK_NAME characters, the codec streaming now if known). Run after each layout and again
 * from bt_ui_poll when the device, its codecs or the codec streaming change (lack_key). s holds
 * the caption, BT_LACK_LEN bytes (caption_row copies it). */
#define BT_LACK_NAME 20
#define BT_LACK_LEN 128 /* 20 characters of up to 4 bytes, " doesn't support aptX HD: using aptX HD" */
#define BT_LACK_GREY 0x666666 /* catalogue.c CAT_GREY */
#define BT_LACK_DIM 0xb3      /* ... CAT_DIM */
static const char *lack_caption(int codec, char *s) {
    int now = I(bt_showcoding, 0);
    const char *name = *bt.link_name ? bt.link_name : "This device";
    unsigned len = (unsigned)strlen(name), cut = 0;
    for (int c = 0; c < BT_LACK_NAME && cut < len; ++c) /* characters, not bytes: UTF-8 */
        utf8_char((const unsigned char *)name, len, &cut);
    unsigned n = (unsigned)tk_snprintf(s, BT_LACK_LEN, "%.*s doesn't support %s", (int)cut, name,
                                       codec_name(codec));
    if (codec_name(now) && now != codec && n < BT_LACK_LEN)
        tk_snprintf(s + n, BT_LACK_LEN - n, ": using %s", codec_name(now));
    return s;
}
static unsigned lack_key(void) {
    unsigned h = sink.bits ^ (unsigned)I(bt_showcoding, 0) << 16;
    for (const char *p = bt.link_name; *p; ++p) h = h * 33 + (unsigned char)*p;
    return h | 1; /* never 0, the picker's start */
}

/* Each rate's place: flush with the right column, the edge the switch and the check end on, as
 * a Settings row's value; on the checked row (g_btcoding) just before the check. */
static void quality_rates(void *view) {
    for (unsigned i = 0, n = widget_count_children(view); i < n; ++i) {
        void *b = row_button(widget_get_child(view, i)), *l = b ? widget_lookup(b, "_bt_kbps", 0) : (void *)0;
        if (!l) continue;
        int right = I(b, W_W) - geom_val(V_RIGHT_X) -
                    (atoi(widget_get_prop_str(b, "name", "")) == g_btcoding ? BT_CHECK_GAP : 0);
        widget_move_resize(l, right - BT_KBPS_W, 0, BT_KBPS_W, I(b, W_H));
    }
    quality.coding = g_btcoding;
}
static int quality_gone(void *ctx, void *event) {
    (void)event;
    if (quality.view == ctx) quality.view = (void *)0;
    return 0;
}

/* After the layout: the picker's rows get their rates, Auto its caption. A pick moves the check
 * with no layout (stock's click sets the images), so bt_ui_poll places the rates again. */
static void quality_rows(void *view) {
    if (quality.view != view) {
        quality.view = view;
        widget_on(view, EVT_DESTROY, quality_gone, view);
    }
    for (unsigned i = 0, n = widget_count_children(view); i < n; ++i) {
        void *item = widget_get_child(view, i), *b = row_button(item), *title = (void *)0;
        if (!b || widget_lookup(b, "_bt_kbps", 0) || widget_get_prop_int(item, "_bt_step", 0))
            continue;
        int choice = atoi(widget_get_prop_str(b, "name", ""));
        title = row_title(b);
        unsigned kbps = choice_kbps(choice);
        char said[48];
        if (choice == BT_AUTO && title) caption_row(title, auto_caption(said));
        if (!kbps) continue;
        char s[16];
        void *l = small_label(b, 0, 0, BT_KBPS_W, I(b, W_H), BT_HEAD_PX);
        widget_set_name(l, "_bt_kbps");
        widget_set_prop_str(l, "style:normal:text_align_h", "right");
        tk_snprintf(s, sizeof s, "%u kbps", kbps);
        widget_set_text_utf8(l, s);
    }
    quality_rates(view);
    quality_lacks(view);
}

/* Each choice row's mark and caption for the device connected now (lack_caption above). */
static void quality_lacks(void *view) {
    for (unsigned i = 0, n = widget_count_children(view); i < n; ++i) {
        void *item = widget_get_child(view, i), *b = row_button(item);
        if (!b || widget_get_prop_int(item, "_bt_step", 0) ||
            widget_get_prop_int(item, "_bt_codec", 0))
            continue;
        int choice = atoi(widget_get_prop_str(b, "name", ""));
        if (choice == BT_AUTO || choice < 0 || choice > 7) continue;
        int codec = row_codec(choice), lacks = sink_lacks(codec);
        widget_set_prop_int(item, "_bt_lack", lacks);
        char said[BT_LACK_LEN];
        caption_row(row_title(b), lacks ? lack_caption(codec, said) : (const char *)0);
    }
    quality.lack = lack_key();
    widget_invalidate_force(view, (void *)0);
}

/* Whether w, painted now, is text of a choice row the device connected now lacks: its colour
 * then as the Library's grayed rows' (quality_lacks; navigation.c ringnav_style_color). */
int bt_quality_dim(void *w, unsigned *color) {
    if (!quality.view || !sink.bits) return 0;
    for (int k = 0; w && k < 4 && P(w, W_PARENT) != quality.view; ++k) w = P(w, W_PARENT);
    if (!w || P(w, W_PARENT) != quality.view || !widget_get_prop_int(w, "_bt_lack", 0)) return 0;
    unsigned a = *color >> 24;
    *color = ringnav_row_selected(w) ? (*color & 0xffffffu) | (a * BT_LACK_DIM / 255) << 24
                                     : (*color & 0xff000000u) | BT_LACK_GREY;
    return 1;
}

/* ---- painting ---- */

static void art(void *w, void *canvas, const char *stem, int cx, int cy) {
    unsigned bitmap[64]; /* bitmap_t */
    if (!widget_load_image(w, theme_art(stem), bitmap)) canvas_draw_icon(canvas, bitmap, cx, cy);
}

/* A mark (bt_page's, dress's), in final colours: white on the selection. */
void bt_ui_paint(void *w, void *canvas) {
    int kind =
        w && !tk_strcmp(widget_get_type(w), "image") ? widget_get_prop_int(w, BT_MARK, 0) : 0;
    if (!kind || !widget_get_visible(w)) return;
    void *b = P(w, W_PARENT), *item = b ? P(b, W_PARENT) : (void *)0;
    int selected = st.sel_accent && painting_in(st.sel_accent), light = ipod_light();
    int cx = I(w, W_W) / 2, cy = I(w, W_H) / 2;
    char stem[32];
    switch (kind) {
    case MARK_GLYPH: {
        int dev = item ? row_dev(item) : -1;
        tk_snprintf(stem, sizeof stem, "btdev_%s_%c", bt_kind(bt_class(dev_at(dev, 0))),
                    selected ? 'w'
                    : light  ? 'l'
                             : 'd');
        art(w, canvas, stem, cx, cy);
        break;
    }
    case MARK_DOT: {
        rect_t r = { 0, cy - BT_DOT / 2, BT_DOT, BT_DOT };
        fill_box(canvas, &r, RGBA(selected ? tok(TOK_ON_ACCENT) : tok(TOK_POSITIVE)), BT_DOT / 2);
        break;
    }
    case MARK_CHECK: /* a picker's choice, the theme's check (tools/art.py CHECK_PX) */
        art(w, canvas, selected ? "check_w" : "check_a", cx, cy);
        break;
    case MARK_CHEVRON:
        chevron_icon(w, canvas, I(w, W_W) - BT_SPIN / 2 + 5, cy, selected);
        break;
    case MARK_SPIN:
    case MARK_HEAD_SPIN: {
        int x = cx;
        if (kind == MARK_HEAD_SPIN) { /* beside its header's label, while a scan runs */
            void *l = b ? widget_lookup(b, "_bt_head", 0) : (void *)0;
            const unsigned *s = l ? widget_get_text(l) : (const unsigned *)0;
            if (bt_searchtime <= 1 || !s) return;
            int px = BT_HEAD_PX;
            unsigned n = wide_len(s);
            canvas_set_font(canvas, theme_face(s, (int)n, &px), (unsigned)px);
            x = I(l, W_X) + 2 + (int)(canvas_measure_text(canvas, s, n) + 0.5f) + BT_SPIN_GAP +
                BT_SPIN / 2;
            cy = I(l, W_Y) + I(l, W_H) / 2;
        }
        if (selected)
            tk_snprintf(stem, sizeof stem, "spin_%d", spinner_frame());
        else
            tk_snprintf(stem, sizeof stem, "btspin_%c_%d", light ? 'l' : 'd', spinner_frame());
        art(w, canvas, stem, x, cy);
        spinner_painted(w, (const rect_t *)0); /* list_marks.c turns it */
        break;
    }
    }
}

/* ---- the page's upkeep, every UI loop pass ---- */

/* Whether a device is connected or connecting, by the rows stock built. */
static int busy(void) {
    for (unsigned i = 0, n = bt.view ? widget_count_children(bt.view) : 0; i < n; ++i) {
        void *item = widget_get_child(bt.view, i), *b = row_button(item);
        if (b && row_dev(item) >= 0 && row_state(b) >= BT_CONNECTING) return 1;
    }
    return 0;
}

/* While the page is in front with Bluetooth on: a scan when none runs, the first as it opens and
 * then again each time one ends while nothing is connected (a scan beside a stream costs its air
 * time); the spinner's frames; the classes as the fetch answers. A connect that starts while a scan
 * runs stops it as stock's row click does first (btmenu_item_on_click's btStopSearch), with the
 * results so far read as its timer reads them at the scan's end: the headset reconnecting by
 * itself, headset.c's or the boot's connect, which go by no click. The opening scan beside a
 * device already connected runs on, so Other Devices fills (docs/internals.md#bluetooth-page). A
 * running scan's results are read each second, and a row whose label stock left behind its state
 * gets the list rebuilt. */
static void page_poll(unsigned now) {
    void *win = bt.view ? window_of(bt.view) : (void *)0;
    if (!win || win != window_manager_get_top_window(window_manager())) return;
    if (bt.head_spin && (bt_searchtime > 1) != bt.scan_shown) { /* the scan's spinner on or off */
        bt.scan_shown = bt_searchtime > 1;
        widget_invalidate_force(bt.head_spin, (void *)0);
    }
    if (now - bt.poll_at < BT_POLL_MS) return;
    bt.poll_at = now;
    if (class_read()) widget_invalidate_force(win, (void *)0);
    seen_fetch(now);
    if (seen_read()) ringnav_btrows(win, (void *)0); /* a device heard: listed now */
    /* stock rebuilds only when its own state moves: a row it left behind is rebuilt here */
    if (labels_stale()) g_btsetting_flag = 1;
    int linking = I(bt_connectstatus, 0) == 1 || busy();
    if (bt_searchtime > 1 && linking && !bt.linking) {
        btStopSearch();
        getSearchResult();
        g_btsetting_flag = 1;
    }
    bt.linking = linking;
    /* stock's timer reads the results only as the scan's count ends (10 s): a device found early
     * waited for it, 5 s on 2026-11-19 (docs/internals.md#bluetooth-page). Read each second meanwhile.
     * A row knows its device by its place in the list read (its button's name, which stock's click
     * reads too), so a list that changed is rebuilt here and now, as stock's timer reads and rebuilds
     * in one call (0x4c4848): left to the timer, a row painted, and a tap went to, the device that had
     * moved into its place (V1JDi, a glyph changing under a row as the list shifted). */
    if (bt_searchtime > 1 && now - bt.read_at >= BT_RESULTS_MS) {
        unsigned was = bt.listed;
        bt.read_at = now;
        getSearchResult();
        if ((bt.listed = listed_hash()) != was) ringnav_btrows(win, (void *)0);
    }
    if (!g_bluetoothflag || bt_searchtime || I(bt_initstatus, 0) || I(bt_connectstatus, 0) == 1)
        return;
    if (bt.scans && busy()) return;
    ++bt.scans;
    btStartSearch();
    bt.read_at = now;
    g_btsetting_flag = 1; /* the page's timer rebuilds the list from the scan's first results */
}

/* ---- q2ui's Bluetooth page (ui/page_bluetooth.c, docs/ui-rewrite.md#cutover) ----
 * Stock's page is not open under q2ui, and its timer (every BT_Q2UI_TICK_MS) is what counts a scan
 * down from 50 (btStartSearch), reads its results at 25 and stops it at 1 (btStopSearch,
 * getSearchResult), so a scan q2ui asks for is counted here the same way, its results read each
 * second as page_poll reads them; a found device is told once BlueZ heard it, as the page lists it
 * (seen_fetch, seen_read). */
#define BT_Q2UI_TICK_MS 200  /* stock's Bluetooth page's timer */
#define BT_Q2UI_WAIT_TICKS 150 /* a scan asked for while the stack starts or a connect runs waits 30 s */

/* The found devices in stock's list now (past the paired ones, getSearchResult puts them first),
 * asked of BlueZ whether a scan heard them. */
static void q2ui_found(void) {
    const char *m;
    bt.n_found = 0;
    for (int i = getBtPairSize(); (m = dev_at(i, 0)) && bt.n_found < BT_FOUND_MAX; ++i)
        if (bt_mac_ok(m)) tk_snprintf(bt.found[bt.n_found++], sizeof bt.found[0], "%s", m);
    seen_fetch(time_now_ms());
    seen_read();
}
static int q2ui_tick(const void *unused) {
    (void)unused;
    void *win = bt.view ? window_of(bt.view) : (void *)0;
    if (!g_bluetoothflag || win) return bt.q2scan = 0; /* off, or stock's page has it: RET_REMOVE */
    if (bt.q2scan == 1) {
        if (bt_searchtime || I(bt_initstatus, 0) || I(bt_connectstatus, 0) == 1)
            return --bt.q2waits > 0 ? 8 : (bt.q2scan = 0); /* RET_REPEAT */
        bt.n_heard = 0, bt.seen_hash = 0; /* what the scan heard, from this one on */
        btStartSearch();
        q2ui_found();
        bt.q2scan = 2;
        return 8;
    }
    if (I(bt_initstatus, 0)) return 8; /* stock's timer waits for the stack too */
    if ((signed char)bt_searchtime <= 0) {
        q2ui_found();
        return bt.q2scan = 0;
    }
    if (--bt_searchtime == 1) { /* the scan's end, as stock's */
        btStopSearch();
        getSearchResult();
        q2ui_found();
    } else if (bt_searchtime % 5 == 0) { /* each second (stock's own read at 25 among them) */
        getSearchResult();
        q2ui_found();
    }
    return 8;
}

/* q2ui's "btscan" (q2ui_gate.c ctl): 0 when a scan is under way or set going, 1 with Bluetooth off. */
int bt_q2ui_scan(void) {
    if (!g_bluetoothflag) return 1;
    if (bt.q2scan) return 0;
    bt.q2scan = 1, bt.q2waits = BT_Q2UI_WAIT_TICKS;
    if (!timer_add(q2ui_tick, 0, BT_Q2UI_TICK_MS)) bt.q2scan = 0;
    return !bt.q2scan;
}

/* q2ui's list of the devices (q2ui_gate.c tell, its own file): "scan 1" while a scan runs, else
 * "scan 0", then with Bluetooth on a line a device, "PAIRED STATE MAC NAME", the paired ones first
 * (stock's order) and a found one only once a scan heard it; STATE 2 connected, 1 connecting, else 0
 * (bt_dev_state); the name last, its backslashes and newlines as \\ and \n. Whole lines only, as
 * many as fit room. Its length. */
unsigned bt_q2ui_devices(char *b, unsigned room) {
    int k = tk_snprintf(b, room, "scan %d\n", bt.q2scan || (signed char)bt_searchtime > 1);
    unsigned n = k > 0 && (unsigned)k < room ? (unsigned)k : 0;
    int paired = g_bluetoothflag ? getBtPairSize() : 0;
    const char *m;
    for (int i = 0; g_bluetoothflag && (m = dev_at(i, 0)); ++i) {
        if (!bt_mac_ok(m) || (i >= paired && !heard(m))) continue;
        int state = bt_dev_state(i);
        char line[32 + 2 * 64];
        unsigned at = (unsigned)tk_snprintf(line, sizeof line, "%d %d %s ", i < paired,
                                            state == BT_ROW_CONNECTED ? 2 : state == BT_ROW_CONNECTING, m);
        const char *name = dev_at(i, 1);
        for (unsigned j = 0; name[j] && j < 63 && at < sizeof line - 3; ++j) {
            if (name[j] == '\\' || name[j] == '\n') line[at++] = '\\';
            line[at++] = name[j] == '\n' ? 'n' : name[j];
        }
        line[at++] = '\n';
        if (n + at > room) break;
        memcpy(b + n, line, at);
        n += at;
    }
    return n;
}

/* q2ui's "btlink MAC": stock's row click's own steps on device mac (btmenu_item_on_click at x 0):
 * a scan stopped, then handleBluetoothLink, which connects a paired or found one (a found one is
 * paired first) and disconnects the connected one, the output back to a jack after a disconnect
 * (its 1), the list read again. One connecting stops connecting, as its row on the page does
 * (cancel_connect). 1 when nothing was done: Bluetooth off or starting, another connect running, or
 * no such device. */
int bt_q2ui_link(const char *mac) {
    int dev = bt_dev_find(mac);
    if (!g_bluetoothflag || I(bt_initstatus, 0) || dev < 0) return 1;
    if (I(bt_connectstatus, 0) == 1) return !cancel_connect(mac);
    bt_trace_why("q2ui", mac); /* the connect it may start, in the log (bt_trace.c) */
    btStopSearch();
    if (handleBluetoothLink(dev) == 1) bt_output_back();
    getSearchResult();
    g_btsetting_flag = 1;
    return 0;
}

/* q2ui's "btforget MAC": Forget This Device's steps (bt_detail.c bt_forget), asked on q2ui's side. 1
 * when nothing was done. */
int bt_q2ui_forget(const char *mac) {
    if (!g_bluetoothflag || I(bt_initstatus, 0) || I(bt_connectstatus, 0) == 1) return 1;
    return !bt_forget(mac);
}

/* ---- the connection toasts (docs/oled-design.md#dialogs-and-toasts) ---- */

/* The device connected now (stock's link list's last item at status 3) into mac[24]; 0 none. */
static int linked(char *mac) {
    char item[32] = "";
    int status = 0, n = g_bluetoothflag ? getBtLinkSize() : 0;
    if (n > 0) getBtLinkItem(n - 1, &status, item);
    if (status != 3) return 0;
    tk_snprintf(mac, 24, "%s", item);
    return 1;
}

/* A toast for mac: "<name> <what>", the kind's glyph (bttoast_<kind>, _off for a drop). */
static void link_toast(const char *mac, const char *what, int off) {
    const char *kind = bt_kind(bt.link_cls);
    char s[96], glyph[40];
    tk_snprintf(s, sizeof s, "%s %s", *bt.link_name ? bt.link_name : "Bluetooth", what);
    tk_snprintf(glyph, sizeof glyph, "bttoast_%s%s", kind, off ? "_off" : "");
    toast_icon(theme_art(glyph), s);
}

/* Every BT_POLL_MS: a device connecting or dropping toasts it ("WH-1000XM5 connected", a car
 * "BMW 6R81 connected · resuming" with Resume on Connect on); its connect toast waits up to
 * BT_CLASS_WAIT_MS for its Class (bt_battery.c's first run) so a car says so. A headset's battery
 * toasts once as it reaches 20% and once at 10% each connection. A drop the user asked for (stock
 * toasts its own Disconnected) is not toasted again. The connected device's codecs are learned
 * here too (sink_poll). */
static void link_poll(unsigned now) {
    char mac[24];
    int on = linked(mac);
    sink_poll(on ? mac : "", now);
    if (!on) {
        if (bt.link[0] && !feedback_toast(window_manager_get_top_window(window_manager())) &&
            !bt.link_pending)
            link_toast(bt.link, "disconnected", 1);
        bt.link[0] = 0, bt.link_pending = 0, bt.low = 0;
        return;
    }
    if (strcmp(mac, bt.link)) { /* a new connection */
        tk_snprintf(bt.link, sizeof bt.link, "%s", mac);
        bt.link_at = now, bt.link_pending = 1, bt.low = 0, bt.link_cls = 0, bt.link_name[0] = 0;
    }
    unsigned cls = bt_headset_class();
    if (cls) bt.link_cls = cls;
    const char *name = bt_dev_name(bt_dev_find(mac));
    if (!name || !*name) name = bt_headset_name();
    if (*name) tk_snprintf(bt.link_name, sizeof bt.link_name, "%s", name);
    if (bt.link_pending &&
        ((bt.link_cls && *bt.link_name) || now - bt.link_at >= BT_CLASS_WAIT_MS)) {
        bt.link_pending = 0;
        int car = !tk_strcmp(bt_kind(bt.link_cls), "car");
        link_toast(mac, car && ringnav_bt_resume() ? "connected · resuming" : "connected", 0);
        return;
    }
    int level = bt_headset_battery();
    int step = level < 0 ? 0 : level <= 10 ? 2 : level <= 20 ? 1 : 0;
    if (!bt.link_pending && step > bt.low) {
        char s[48];
        bt.low = step;
        tk_snprintf(s, sizeof s, "Headphones low · %d%%", level);
        toast_icon(theme_art("bttoast_low"), s);
    }
}

/* ---- Quality on a live link: the codec switched in place ---- */

/* Stock's Quality click (0x4c54f4), on a choice other than the saved one with a headset linked
 * (bt_linkstatus, a codec in bt_showcoding), asks to reconnect (msg_btreconnect), stops the player,
 * forgets the link and runs bluetoothLink(2): btDisconnect, then connectBtDevice, as the codec
 * priority switchBtPriority gives bluetoothd is read only when a link is set up. The headset is
 * dropped for some seconds and comes back on a new ACL handle, or does not when its page fails.
 * bt_quality_linked stands in for the click's read of bt_linkstatus (BT_QUALITY, tools/build.py),
 * given the choice: what it returns is that byte to stock, so 0 sends the click down its tail for
 * no link (switchBtPriority, BTCODING saved, the checks redrawn) and anything else to its dialog.
 *
 * A choice of another codec than the one streaming is switched on the link as it is: bluealsa's
 * SelectCodec (q2bt-codec, as the link monitor's step-down), which closes the AVDTP stream and
 * opens one on the other codec's endpoint with the ACL link, AVRCP and the pairing untouched; the
 * player opens the PCM again when it is back (player/output.c bt_reopen). The choice is the head of
 * an ordered list, as Android's (its codec, then LDAC, aptX HD, aptX, AAC, SBC; Auto's is LDAC's),
 * and the first codec on it the device takes is used: one the device is known to lack (sink.bits,
 * GetCodecs) is refused here, before anything touches the link, and when what is left starts with
 * the codec streaming nothing is switched at all; the Quality row then says so (quality_lacks).
 * With the device's codecs not yet known the whole list is tried in that order, bluealsa refusing
 * one the device has not ("SEP codec not available") before it touches the stream. A choice of
 * the codec streaming: aptX HD, aptX and AAC have one rate, so nothing is done; LDAC's three and
 * SBC's two differ in a rate bluealsa reads as the encoder starts, so those keep stock's
 * reconnect. */
#define BT_PICK_FILE "/tmp/q2bt.pick" /* what the switch said: each refusal, then the codec taken */
int bt_quality_linked(int row) {
    static const signed char order[] = { 4, 13, 3, 2, 1 }; /* Auto's: LDAC down to SBC */
    int now = I(bt_showcoding, 0), want = row_codec(row), first = 0;
    char list[48], cmd[512];
    unsigned k = 0;
    if (!bt_linkstatus || now <= 0 || !want) return 0;
    if (want == now) return want == 4 || want == 1;
    list[0] = 0;
    for (int i = -1; i < (int)sizeof order && k < sizeof list; ++i) {
        int c = i < 0 ? want : order[i];
        if ((i >= 0 && c == want) || sink_lacks(c)) continue;
        if (!first) first = c;
        k += (unsigned)tk_snprintf(list + k, sizeof list - k, "%s%s", k ? " " : "", codec_wire(c));
        if (c == 1) break; /* SBC: every device has it, nothing after it is tried */
    }
    if (first == now) { /* the device lacks the choice: what streams is already its best */
        tk_snprintf(cmd, sizeof cmd, "Quality: %s not on the device, %s kept", codec_wire(want),
                    codec_wire(now));
        ulog('I', "bt", cmd);
        return 0;
    }
    /* the PCM's object path through a pattern of what one holds, as it goes to the shell */
    tk_snprintf(cmd, sizeof cmd,
                "(p=$(dbus-send --system --print-reply --dest=org.bluealsa /org/bluealsa "
                "org.freedesktop.DBus.ObjectManager.GetManagedObjects 2>/dev/null | sed -n "
                "'s|.*object path \"\\(/org/bluealsa/[A-Za-z0-9_/]*/a2dpsrc/[A-Za-z0-9_/]*\\)\".*"
                "|\\1|p' | head -n 1); [ -n \"$p\" ] && for c in %s; do q2bt-codec \"$p\" $c && "
                "echo $c && break; done) >" BT_PICK_FILE " 2>&1",
                list);
    if (!bt_shell((bt_job *)0, cmd)) { /* each pick its own, as the link monitor's step-down */
        tk_snprintf(cmd, sizeof cmd, "Quality: %s not asked for: no thread to ask on", list);
        ulog('W', "bt", cmd);
        return 0;
    }
    tk_snprintf(cmd, sizeof cmd, "Quality: %s asked for on the live link", list);
    ulog('I', "bt", cmd);
    return 0;
}
/* BT_QUALITY's call: the choice is in the click's $s0 (the site's delay slot hands it over), and
 * the click reads $gp next without loading it again, which the calls above have changed: back from
 * the click's own frame, where its prologue saved it (0x10($sp)). */
__asm__(".set push\n.set noreorder\n.globl bt_quality_site\nbt_quality_site:\n"
        "addiu $sp, $sp, -24\nsw $ra, 20($sp)\njal bt_quality_linked\nnop\n"
        "lw $ra, 20($sp)\naddiu $sp, $sp, 24\njr $ra\nlw $gp, 16($sp)\n.set pop\n");

/* bluealsa names the codec at each SetConfiguration in its status event, which stock keeps in
 * a2dp_eventstate (1 SBC, 2 AAC, 3 aptX, 4 LDAC, 13 aptX HD; 0x65 a refused configuration, which
 * makes stock reconnect). checkBluetoothStatus moves it to bt_showcoding only while it sets a link
 * up (bt_a2dp_direction), so after a switch in place (the pick above, the link monitor's step) the
 * badge, the connected row and Quality's caption kept the codec the link began on. The event is
 * left as it is: stock's own paths read and clear it. */
static void codec_follow(void) {
    int event = I(a2dp_eventstate, 0), shown = I(bt_showcoding, 0);
    if (!bt_linkstatus || shown <= 0 || shown == event || I(bt_a2dp_direction, 0)) return;
    if (event == 1 || event == 2 || event == 3 || event == 4 || event == 13)
        *(volatile int *)bt_showcoding = event;
}

void bt_ui_poll(void) {
    unsigned now = (unsigned)time_now_ms();
    codec_follow();
    if (quality.view && quality.coding != g_btcoding) quality_rates(quality.view);
    if (bt.view) page_poll(now);
    guest_ui_poll();
    bt_pair_poll(); /* a device pairing: its card (bt_pair.c) */
    bt_detail_poll();
    if (now - bt.link_poll_at >= BT_POLL_MS) {
        bt.link_poll_at = now;
        link_poll(now);
    }
    if (quality.view && quality.lack != lack_key()) quality_lacks(quality.view);
}
