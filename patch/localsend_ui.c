/* Settings > Network > LocalSend (docs/localsend.md): demo's side of q2send (patch/localsend.c). The page lists
 * Send Logs, then the devices nearby (Center asks what to send one: Logs or Battery Log), then Trusted Devices when
 * there are any; with none found, the empty state under the rows, and with Wi-Fi off the empty state with Turn On
 * Wi-Fi. The share sheet (Battery History's Share Log, Send Logs) lists the devices to send one thing to. A send
 * opens the transfer page (waiting for the other user's answer, then the progress, Cancel), and its end is a toast.
 * An incoming request is the Accept card over whatever is on screen, with "Always accept from this device"; a
 * sender on the trusted list skips it and gets a "Receiving from <name>" toast instead. After a receive the files
 * are scanned into the library once that is safe (addon_library.c library_quiet), as a card app's sync is.
 *
 * q2send runs while one of these pages is up or a transfer has not ended, and is stopped otherwise: nothing listens
 * on the network the rest of the time. demo talks to it through /tmp/q2send.* (localsend.c's header has the
 * format): it reads state and ask, and writes answer, cmd and hold, each whole (temp and rename). localsend_poll,
 * every LS_POLL_MS from power_poll, does all of it. */
#include "navigation.h"

#define LS_DAEMON "/usr/bin/q2send"
#define LS_STATE "/tmp/q2send.state"
#define LS_ASK "/tmp/q2send.ask"
#define LS_ANSWER "/tmp/q2send.answer"
#define LS_CMD "/tmp/q2send.cmd"
#define LS_HOLD "/tmp/q2send.hold"
#define LS_RECEIVED "/tmp/q2send.received"
#define LS_PID "/tmp/q2send.pid"
#define LS_LOGS "/mnt/mmc/.q2muse/logs" /* q2guard.c LOGS */
#define LS_BATTERY "/tmp/battery-log.txt"
#define LS_POLL_MS 250
#define LS_RESTART_MS 3000 /* the least time between two starts of a daemon that died */
#define LS_ASK_MS 59000    /* the card's own Decline: inside localsend.c's ASK_MS (60 s) */
#define LS_DEVS 8          /* the devices a page lists */
#define LS_TRUSTED 8       /* the trusted devices Trusted Devices lists */
#define LS_STATE_MAX 6144  /* localsend.c write_state's buffer */
#define LS_FILES 24        /* the files one send takes */

/* The rows' geometry: Car Remote's (carremote_page.c), the mock-up's (localsend-mock.png) */
#define LS_ROW 32
#define LS_HEAD_GAP 4  /* Send Logs to Nearby */
#define LS_GLYPH_X 26  /* Send Logs' paper plane */
#define LS_ACTION_X 54 /* its label */
#define LS_DEV_GLYPH 20
#define LS_DEV_X 56
#define LS_LIST_END 278 /* the list's foot, clear of the footer line */
#define LS_FOOT_Y 290
#define LS_SHEET_TOP 26 /* the share sheet's file line to its Send to header */
#define LS_CTA_OUT (CTA_RING_OFF + CTA_RING_W)
#define LS_STACK 118 /* the empty state's stack with its glyph (feedback.c ES_TEXT) */
#define LS_WORDS 44  /* and without it (ES_WORDS) */
#define LS_KIND "_ls_kind"
#define LS_VALUE "_ls_value"
#define LS_DEV "_ls_dev"

enum { K_ACTION = 1, K_HEAD, K_DEVICE, K_VALUE, K_CAPTION, K_REMOVE, K_CTA, K_GAP };
enum { P_MAIN = 1, P_PICK, P_SHARE, P_TRUST };

typedef struct {
    char fp[65], alias[40], model[24];
    int phone;
} ls_dev;
typedef struct {
    char phase[16], alias[40], name[64];
    int done, files, trusted;
    unsigned kb_done, kb; /* KB: no 64-bit divide in the payload */
} ls_xfer;
typedef struct {
    ls_dev dev[LS_DEVS];
    int n, ntrust;
    char trust_fp[LS_TRUSTED][65], trust_alias[LS_TRUSTED][40];
    ls_xfer recv, send;
} ls_state;

static struct {
    void *page, *view;   /* LocalSend */
    void *pick, *pick_view; /* Send to <device> */
    void *share, *share_view;
    void *trust, *trust_view;
    void *xfer;          /* Sending or Receiving */
    int xfer_recv;       /* the transfer page's: 1 a receive */
    unsigned at, started_at, shown[5]; /* each page's sum when built (by P_*) */
    int live, asking, scan_due, wifi_off, cta_y, page_none, share_none, trusted_row;
    char rphase[16], sphase[16]; /* the phases at the last poll */
    char ask_id[72];             /* the request the card asks about */
    char to_fp[65], to_alias[40];
    int to_phone, x_phone;       /* the device's glyph: the one sent to, the transfer's */
    char share_path[64], share_title[24], share_line[48];
    char card_name[40], card_body[80], gone_name[40];
} ls __attribute__((section(".scratch")));

/* ---- the daemon's files ---- */

/* A tab-separated field of line into out (cap bytes); the line's next field. */
static const char *ls_field(const char *s, char *out, unsigned cap) {
    unsigned n = 0;
    while (*s && *s != '\t' && *s != '\n') {
        if (n + 1 < cap) out[n++] = *s;
        ++s;
    }
    out[n] = 0;
    return *s == '\t' ? s + 1 : s;
}

/* The end of s's line: its '\n', or its NUL (the payload has no strchr). */
static char *ls_eol(const char *s) {
    while (*s && *s != '\n') ++s;
    return (char *)s;
}

/* A byte count's text as KB, so no 64-bit divide is needed (the daemon writes bytes as long long). */
static unsigned ls_kb(const char *s) {
    unsigned long long v = 0;
    while (*s >= '0' && *s <= '9') v = (v << 3) + (v << 1) + (unsigned)(*s++ - '0');
    return (unsigned)((v + 1023) >> 10);
}

static void ls_xfer_line(const char *s, ls_xfer *x, int recv) {
    char f[24];
    s = ls_field(s, x->phase, sizeof x->phase);
    s = ls_field(s, f, sizeof f), x->done = atoi(f);
    s = ls_field(s, f, sizeof f), x->files = atoi(f);
    s = ls_field(s, f, sizeof f), x->kb_done = ls_kb(f);
    s = ls_field(s, f, sizeof f), x->kb = ls_kb(f);
    s = ls_field(s, x->alias, sizeof x->alias);
    s = ls_field(s, x->name, sizeof x->name);
    if (recv) ls_field(s, f, sizeof f), x->trusted = f[0] == '1';
}

/* The state file into s; the hash of what the lists show (the devices and the trusted ones) into *sum. 0 with no
 * file (no daemon). */
static int ls_read(ls_state *s, unsigned *sum) {
    char raw[LS_STATE_MAX + 2];
    unsigned n = state_read(LS_STATE, raw, sizeof raw);
    memset(s, 0, sizeof *s);
    unsigned h = FNV_SEED;
    for (const char *line = raw + 1; n && *line;) {
        const char *end = ls_eol(line);
        char tag[8];
        const char *rest = ls_field(line, tag, sizeof tag);
        if (!tk_strcmp(tag, "peer") && s->n < LS_DEVS) {
            ls_dev *d = s->dev + s->n++;
            char skip[24], type[16];
            rest = ls_field(rest, d->fp, sizeof d->fp);
            rest = ls_field(rest, skip, sizeof skip); /* ip */
            rest = ls_field(rest, skip, sizeof skip); /* port */
            rest = ls_field(rest, type, sizeof type);
            rest = ls_field(rest, d->alias, sizeof d->alias);
            ls_field(rest, d->model, sizeof d->model);
            d->phone = !tk_strcmp(type, "mobile");
            h = hash_bytes(h, (const unsigned char *)line, (unsigned)(end - line));
        } else if (!tk_strcmp(tag, "trusted") && s->ntrust < LS_TRUSTED) {
            rest = ls_field(rest, s->trust_fp[s->ntrust], sizeof s->trust_fp[0]);
            ls_field(rest, s->trust_alias[s->ntrust], sizeof s->trust_alias[0]);
            ++s->ntrust;
            h = hash_bytes(h, (const unsigned char *)line, (unsigned)(end - line));
        } else if (!tk_strcmp(tag, "recv"))
            ls_xfer_line(rest, &s->recv, 1);
        else if (!tk_strcmp(tag, "send"))
            ls_xfer_line(rest, &s->send, 0);
        line = *end ? end + 1 : end;
    }
    int off = !g_wififlag && !smart_wifi_off(); /* the user's switch off; Smart Wi-Fi's off wakes for the page */
    h = hash_bytes(h, (const unsigned char *)&off, sizeof off);
    if (sum) *sum = h;
    return n != 0;
}

/* text written whole to path (temp and rename), as the daemon reads it. */
static int ls_put(const char *path, const char *text) {
    char tmp[40];
    tk_snprintf(tmp, sizeof tmp, "%s.tmp", path);
    return blob_io(path, tmp, (void *)text, strlen(text), 1);
}

static int ls_running(void) {
    char raw[24], proc[24];
    if (!state_read(LS_PID, raw, sizeof raw)) return 0;
    tk_snprintf(proc, sizeof proc, "/proc/%d", atoi(raw + 1));
    return atoi(raw + 1) > 1 && !access(proc, 0);
}

/* The daemon started as the Q2's Bluetooth name (what a phone lists), quotes and backslashes left out of it for
 * the shell. */
static void ls_start(void) {
    char name[40], safe[40], cmd[120];
    unsigned k = 0;
    bt_name(name, sizeof name);
    for (const char *c = name; *c && k + 1 < sizeof safe; ++c)
        if (*c != '\'' && *c != '\\') safe[k++] = *c;
    safe[k] = 0;
    ls.started_at = time_now_ms() | 1;
    ls.live = 1;
    if (ls_running()) return;
    unlink(LS_STATE);
    unlink(LS_CMD);
    unlink(LS_ANSWER);
    tk_snprintf(cmd, sizeof cmd, "( " LS_DAEMON " --alias '%s' >/dev/null 2>&1 & )", safe);
    system(cmd);
}

static void ls_stop(void) {
    ls.live = 0;
    system("kill $(cat " LS_PID " 2>/dev/null) 2>/dev/null");
    unlink(LS_CMD);
    unlink(LS_ANSWER);
    unlink(LS_HOLD);
    if (ls.asking) ls.asking = 0, confirm_close();
}

/* The daemon runs: Smart Wi-Fi keeps the radio up meanwhile (smart_wifi.c smart_needed). */
int localsend_running(void) { return ls.live; }

/* ---- sizes and words ---- */

static void ls_size(char *out, unsigned n, unsigned kb) {
    if (kb < 1024)
        tk_snprintf(out, n, "%u KB", kb ? kb : 1);
    else if (kb < 1024 * 1024)
        tk_snprintf(out, n, "%u.%u MB", kb / 1024, kb % 1024 * 10 / 1024);
    else
        tk_snprintf(out, n, "%u.%u GB", kb >> 20, (kb >> 10) % 1024 * 10 / 1024);
}

static unsigned ls_file_kb(const char *path) {
    unsigned sx[64]; /* struct statx: stx_size at byte 40 */
    if (ringnav_statx(-100, path, 0, 0x200 /* SIZE */, sx)) return 0;
    return (sx[10] >> 10) + (sx[11] << 22) + ((sx[10] & 1023) != 0);
}

/* The logs' files (LS_LOGS's, as they are) into paths, their count; their size into *kb. Then each
 * card app's own logs as they are now, of the apps the last addon_scan found, each with the .1 it
 * rolls to: its log in RAM, /tmp/q2-<id>.log (Go's standard log, q2-subsonic's SetupLog; the
 * unified log has its lines from the time q2guard read them, q2log_core.c ulog_apps), and its log
 * on the card, .<id>/log.txt (q2-subsonic's applog, where its fetches' failures go, their tokens
 * and passwords redacted by the app), which q2guard never reads: the card may hang. */
static int ls_logs(char paths[][64], int cap, unsigned *kb) {
    int n = 0;
    *kb = 0;
    void *d = opendir(LS_LOGS);
    for (struct dirent *e; d && n < cap && (e = readdir(d));) {
        if (e->d_name[0] == '.') continue;
        tk_snprintf(paths[n], 64, LS_LOGS "/%s", e->d_name);
        *kb += ls_file_kb(paths[n]);
        ++n;
    }
    if (d) closedir(d);
    static const char *const where[] = { "/tmp/q2-%s.log%s", "/mnt/mmc/.%s/log.txt%s" };
    const char *id;
    for (int k = 0; (id = addon_id(k)); ++k)
        for (int f = 0; f < 4 && n < cap; ++f) {
            unsigned sx[64];
            tk_snprintf(paths[n], 64, where[f >> 1], id, f & 1 ? ".1" : "");
            if (ringnav_statx(-100, paths[n], 0, 0x200 /* SIZE */, sx)) continue; /* none */
            *kb += ls_file_kb(paths[n]);
            ++n;
        }
    return n;
}

/* ---- the rows (carremote_page.c's) ---- */

static int ls_screen_y(void *w) { return feedback_below(w) - I(w, W_H); }

static void *ls_row(void *view, int index, int k, const char *caption, const char *detail, int x,
                    int (*click)(void *, void *)) {
    void *item = page_row_detail(view, index, caption, detail, click);
    widget_set_prop_int(item, LS_KIND, k);
    for (unsigned i = 0, n = widget_count_children(item); i < n; ++i) {
        void *label = widget_get_child(item, i);
        widget_move_resize(label, x, I(label, W_Y), 375 - geom_val(V_RIGHT_X) - x, I(label, W_H));
    }
    return item;
}

/* A row the wheel passes (navigation.c collect), h high, of kind k (a header's words, a caption's, painted). */
static void *ls_gap(void *view, int h, int k, const char *words) {
    void *item = list_item_create(view, 0, 0, 375, h);
    widget_use_style(item, "s_listitem_black");
    widget_set_prop_int(item, LS_KIND, k);
    if (words) widget_set_prop_str(item, LS_VALUE, words);
    return item;
}

/* view's rows stacked at their own heights, the list as tall as they are up to LS_LIST_END */
static void ls_fit(void *view) {
    int y = 0;
    for (unsigned i = 0, n = widget_count_children(view); i < n; ++i) y += I(widget_get_child(view, i), W_H);
    void *list = P(view, W_PARENT);
    int room = LS_LIST_END - ls_screen_y(list), h = y < room ? y : room;
    widget_set_prop_int(list, "item_height", 0);
    widget_resize(list, 375, h);
    widget_resize(view, 375, h);
    widget_layout(list);
}

static int ls_selected(void *view) { return view ? widget_get_prop_int(view, "_ringnav_index", 0) : 0; }

static void *ls_device_row(void *view, int index, const ls_dev *d, int (*click)(void *, void *)) {
    void *item = ls_row(view, index, K_DEVICE, d->alias, *d->model ? d->model : d->phone ? "Phone" : "Computer",
                        LS_DEV_X, click);
    widget_set_prop_int(widget_get_child(item, 1), "style:normal:text_color", (int)RGBA(tok(TOK_TERTIARY)));
    widget_set_prop_int(item, LS_DEV, d->phone);
    return item;
}

/* ---- sending ---- */

static int ls_xfer_open(int recv, int phone);

/* cmd "send" to fp with n paths, and the transfer page. */
static void ls_send(const char *fp, const char *alias, int phone, char paths[][64], int n) {
    char cmd[80 + LS_FILES * 66];
    unsigned k = tk_snprintf(cmd, sizeof cmd, "send\t%s\n", fp);
    for (int i = 0; i < n && k < sizeof cmd - 66; ++i) k += tk_snprintf(cmd + k, sizeof cmd - k, "%s\n", paths[i]);
    if (!n || !ls_put(LS_CMD, cmd)) {
        toast("Couldn't send");
        return;
    }
    tk_snprintf(ls.to_alias, sizeof ls.to_alias, "%s", alias);
    tk_snprintf(ls.sphase, sizeof ls.sphase, "wait"); /* the daemon's, from its next state */
    ls_xfer_open(0, phone);
}

/* Logs (what 0) or Battery Log (1) to the device. The logs are flushed first (ulog.c), so they are as they are now,
 * and go with the battery log. */
static void ls_send_what(const char *fp, const char *alias, int phone, int what) {
    char paths[LS_FILES][64];
    unsigned kb;
    int n = 0;
    if (what == 0) {
        ulog_flush();
        n = ls_logs(paths, LS_FILES - 1, &kb);
    }
    if (battery_log_write(LS_BATTERY) > 0) tk_snprintf(paths[n++], 64, LS_BATTERY);
    ls_send(fp, alias, phone, paths, n);
}

/* ---- Send to <device> ---- */

static int ls_pick_click(void *ctx, void *event) {
    int what = (int)(long)ctx;
    char fp[65], alias[40];
    int phone = ls.to_phone;
    tk_snprintf(fp, sizeof fp, "%s", ls.to_fp);
    tk_snprintf(alias, sizeof alias, "%s", ls.to_alias);
    if (ls.pick) window_close(ls.pick);
    ls_send_what(fp, alias, phone, what);
    return 0;
}

static int ls_pick_closed(void *ctx, void *event) {
    if (ls.pick == ctx) ls.pick = ls.pick_view = 0;
    return 0;
}

static void ls_pick_open(const ls_dev *d) {
    if (ls.pick || !(ls.pick = page_open("lssend_page", ls_pick_closed, page_keyup))) return;
    char title[56], logs[16], batt[16], paths[LS_FILES][64];
    unsigned kb;
    tk_snprintf(ls.to_fp, sizeof ls.to_fp, "%s", d->fp);
    tk_snprintf(ls.to_alias, sizeof ls.to_alias, "%s", d->alias);
    ls.to_phone = d->phone;
    ls_logs(paths, LS_FILES - 1, &kb);
    ls_size(logs, sizeof logs, kb);
    int bk = battery_log_write(LS_BATTERY);
    ls_size(batt, sizeof batt, bk > 0 ? ((unsigned)bk + 1023) >> 10 : 0);
    tk_snprintf(title, sizeof title, "Send to %s", d->alias);
    void *view = page_list(ls.pick, ls.pick, 0, title, 2, 48);
    const char *labels[] = { "Logs", "Battery Log" }, *values[] = { logs, batt };
    for (int r = 0; r < 2; ++r) {
        void *item = ls_row(view, r, K_VALUE, labels[r], 0, page_x(), ls_pick_click);
        widget_set_prop_str(item, LS_VALUE, values[r]);
        widget_set_prop_str(item, "_ls_label", labels[r]);
    }
    ls_gap(view, 34, K_CAPTION, "Logs: the Q2's and its apps', as they are now");
    ls_fit(view);
    ringnav_select(view, 0, 2);
    ls.pick_view = view;
}

/* ---- the share sheet ---- */

static int ls_share_show(const void *ctx);

static int ls_share_click(void *ctx, void *event) {
    ls_state s;
    int i = (int)(long)ctx;
    ls_read(&s, 0);
    if (i >= s.n) return 0;
    char paths[LS_FILES][64];
    unsigned kb;
    int n = 1;
    if (*ls.share_path)
        tk_snprintf(paths[0], 64, "%s", ls.share_path);
    else { /* Send Logs */
        ulog_flush();
        n = ls_logs(paths, LS_FILES - 1, &kb);
        if (battery_log_write(LS_BATTERY) > 0) tk_snprintf(paths[n++], 64, LS_BATTERY);
    }
    ls_dev d = s.dev[i];
    if (ls.share) window_close(ls.share);
    ls_send(d.fp, d.alias, d.phone, paths, n);
    return 0;
}

static int ls_share_closed(void *ctx, void *event) {
    if (ls.share == ctx) ls.share = ls.share_view = 0;
    return 0;
}

/* The sheet: the file line (painted), Send to and the devices, or the empty state's words under it. ctx 1 (a
 * change) keeps the selection. */
static int ls_share_show(const void *ctx) {
    if (!ls.share) return 0;
    ls_state s;
    ls_read(&s, &ls.shown[P_SHARE]);
    int sel = ctx && ls.share_view ? ls_selected(ls.share_view) : 0;
    ls.share_view = 0;
    void *view = page_list(ls.share, ls.share, 0, ls.share_title, s.n ? s.n + 2 : 1, 48);
    ls_gap(view, LS_SHEET_TOP, K_GAP, 0);
    ls_gap(view, LS_ROW, K_HEAD, "Send to");
    for (int i = 0; i < s.n; ++i) ls_device_row(view, i, s.dev + i, ls_share_click);
    ls_fit(view);
    if (sel >= s.n) sel = s.n - 1;
    if (s.n) ringnav_select(view, sel < 0 ? 0 : sel, s.n);
    ls.share_view = view;
    ls.share_none = !s.n; /* the empty state's stack, painted (localsend_paint) */
    empty_state(ls.share, 0, 0, 0);
    return 0;
}

static void ls_scan(void) {
    if (ls_running()) ls_put(LS_CMD, "scan\n"); /* the announcement burst; a new daemon makes its own */
}

/* The share sheet for path (0: Send Logs) under title: the devices found, each sent the file at Center. */
void localsend_share(const char *path, const char *title) {
    if (ls.share || !(ls.share = page_open("lsshare_page", ls_share_closed, page_keyup))) return;
    unsigned kb = 0;
    char size[16], paths[LS_FILES][64];
    tk_snprintf(ls.share_path, sizeof ls.share_path, "%s", path ? path : "");
    tk_snprintf(ls.share_title, sizeof ls.share_title, "%s", title);
    if (path) {
        const char *base = strrchr(path, '/');
        ls_size(size, sizeof size, ls_file_kb(path));
        tk_snprintf(ls.share_line, sizeof ls.share_line, "%s \xc2\xb7 %s", base ? base + 1 : path, size);
    } else {
        ls_logs(paths, LS_FILES - 1, &kb);
        ls_size(size, sizeof size, kb);
        tk_snprintf(ls.share_line, sizeof ls.share_line, "q2-log, q2-heat, q2-bt, apps \xc2\xb7 %s", size);
    }
    ls_start();
    ls_scan();
    ls_share_show(0);
}

/* ---- Trusted Devices ---- */

static int ls_trust_show(const void *ctx);

static void ls_remove_all(void *ctx, int row, int toggle_on) {
    if (row != 1) return;
    if (!ls_put(LS_CMD, "untrust-all\n")) toast("Couldn't remove the devices");
}

static int ls_trust_click(void *ctx, void *event) {
    int i = (int)(long)ctx;
    ls_state s;
    ls_read(&s, 0);
    if (i == s.ntrust) { /* Remove All */
        static const char *const rows[] = { "Cancel", "Remove All" };
        confirm(&(confirm_t){ .title = "Remove All Trusted Devices?",
                              .body = "Each one asks again before\nsending to this Vamp.",
                              .rows = rows,
                              .n_rows = 2,
                              .kind = CONFIRM_DESTRUCTIVE,
                              .back = -1,
                              .answer = ls_remove_all });
        return 0;
    }
    if (i > s.ntrust) return 0;
    char cmd[80], text[64];
    tk_snprintf(cmd, sizeof cmd, "untrust\t%s\n", s.trust_fp[i]);
    tk_snprintf(text, sizeof text, "%s removed", s.trust_alias[i]);
    toast(ls_put(LS_CMD, cmd) ? text : "Couldn't remove the device");
    return 0;
}

static int ls_trust_closed(void *ctx, void *event) {
    if (ls.trust == ctx) ls.trust = ls.trust_view = 0;
    return 0;
}

/* The trusted devices, a row each, the caption, then Remove All in red; none left closes the page. */
static int ls_trust_show(const void *ctx) {
    if (!ls.trust) return 0;
    ls_state s;
    ls_read(&s, &ls.shown[P_TRUST]);
    if (!s.ntrust && ctx) { /* the last one removed */
        window_close(ls.trust);
        return 0;
    }
    int sel = ctx && ls.trust_view ? ls_selected(ls.trust_view) : 0;
    ls.trust_view = 0;
    void *view = page_list(ls.trust, ls.trust, 0, "Trusted Devices", s.ntrust + 1, 48);
    for (int i = 0; i < s.ntrust; ++i) ls_row(view, i, K_VALUE, s.trust_alias[i], 0, page_x(), ls_trust_click);
    ls_gap(view, 28, K_CAPTION, "Center to remove a device");
    void *all = ls_row(view, s.ntrust, K_REMOVE, "Remove All", 0, page_x(), ls_trust_click);
    widget_set_prop_int(widget_get_child(all, 0), "style:normal:text_color", (int)RGBA(tok(TOK_NEGATIVE)));
    ls_fit(view);
    if (sel > s.ntrust) sel = s.ntrust;
    ringnav_select(view, sel, s.ntrust + 1);
    ls.trust_view = view;
    return 0;
}

static void ls_trust_open(void) {
    if (ls.trust || !(ls.trust = page_open("lstrust_page", ls_trust_closed, page_keyup))) return;
    ls_trust_show(0);
}

/* ---- the LocalSend page ---- */

static int ls_page_show(const void *ctx);

static int ls_click(void *ctx, void *event) {
    int i = (int)(long)ctx;
    ls_state s;
    ls_read(&s, 0);
    if (ls.wifi_off) {
        if (!g_wififlag) wifi_switch(1); /* stock's own switch, as the Wi-Fi page's */
        timer_add(ls_page_show, (void *)1, 0);
    } else if (i == 0)
        localsend_share(0, "Send Logs");
    else if (i - 1 < s.n)
        ls_pick_open(s.dev + i - 1);
    else
        ls_trust_open();
    return 0;
}

/* The page: Send Logs (the blue top-action row), Nearby and the devices, Trusted Devices when there are any; none
 * found, the empty state's words under the rows; Wi-Fi off, its empty state with Turn On Wi-Fi as the button (a
 * row of this list, as Car Remote's Pair a Phone). ctx 1 (a change) keeps the selection. */
static int ls_page_show(const void *ctx) {
    if (!ls.page) return 0;
    ls_state s;
    ls_read(&s, &ls.shown[P_MAIN]);
    ls.wifi_off = !g_wififlag && !smart_wifi_off();
    int rows = ls.wifi_off ? 1 : 1 + s.n + (s.ntrust > 0);
    int sel = ctx && ls.view ? ls_selected(ls.view) : 0;
    ls.view = 0;
    void *view = page_list(ls.page, ls.page, 0, "LocalSend", rows, 48);
    int free_top = ls_screen_y(view);
    if (ls.wifi_off) {
        static const empty_t off = { "wifi-slash", "Wi-Fi Is Off", "Join the Wi-Fi your phone or computer is on",
                                     "Turn On Wi-Fi", 0, 0 };
        ls.cta_y = empty_row_y(free_top) - free_top - LS_CTA_OUT;
        ls_gap(view, ls.cta_y, K_GAP, 0);
        void *cta = ls_row(view, 0, K_CTA, "", 0, page_x(), ls_click);
        widget_set_prop_int(cta, "_rings_itself", 1); /* no selection bar (nav/row_paint.c paint_selection) */
        widget_set_prop_str(cta, LS_VALUE, off.row);
        widget_resize(cta, 375, CTA_H + 2 * LS_CTA_OUT);
        ls_fit(view);
        ringnav_select(view, 0, 1);
        ls.view = view;
        empty_state(ls.page, free_top, &off, 0);
        return 0;
    }
    void *send = ls_row(view, 0, K_ACTION, "Send Logs", 0, LS_ACTION_X - 2, ls_click);
    widget_set_prop_int(widget_get_child(send, 0), "style:normal:text_color", (int)RGBA(scheme()->tones[0]));
    ls_gap(view, LS_HEAD_GAP, K_GAP, 0);
    ls_gap(view, LS_ROW, K_HEAD, "Nearby");
    for (int i = 0; i < s.n; ++i) ls_device_row(view, 1 + i, s.dev + i, ls_click);
    if (s.ntrust) {
        char count[8];
        tk_snprintf(count, sizeof count, "%d", s.ntrust);
        ls_gap(view, 8, K_GAP, 0);
        void *item = ls_row(view, 1 + s.n, K_VALUE, "Trusted Devices", 0, page_x(), ls_click);
        widget_set_prop_str(item, LS_VALUE, count);
        widget_set_prop_str(item, "_ls_label", "Trusted Devices");
        row_into(item, page_h(48));
    }
    ls_fit(view);
    if (sel >= rows) sel = rows - 1;
    ringnav_select(view, sel < 0 ? 0 : sel, rows);
    ls.view = view;
    ls.page_none = !s.n; /* the empty state's stack, painted (localsend_paint) */
    ls.trusted_row = s.ntrust > 0;
    empty_state(ls.page, 0, 0, 0);
    return 0;
}

static int ls_page_closed(void *ctx, void *event) {
    if (ls.page == ctx) ls.page = ls.view = 0;
    return 0;
}

/* Network's LocalSend row (smart_wifi.c ringnav_netservice_row) */
int localsend_open(void *ctx, void *event) {
    if (ls.page || !(ls.page = page_open("localsend_page", ls_page_closed, page_keyup))) return 0;
    if (!g_wififlag && smart_wifi_off()) smart_wake();
    ls_start();
    ls_scan();
    ls_page_show(0);
    return 0;
}

/* ---- the transfer page ---- */

static int ls_xfer_closed(void *ctx, void *event) {
    if (ls.xfer == ctx) ls.xfer = 0;
    return 0;
}

static void ls_cancel(void) {
    ls_put(LS_CMD, "cancel\n");
    if (ls.xfer) window_close(ls.xfer);
}

/* A tap on Cancel */
static int ls_xfer_touch(void *ctx, void *event) {
    int x = I(event, EVENT_X), y = I(event, EVENT_Y);
    if (x >= 187 - 70 && x < 187 + 70 && y >= 263 - CTA_H / 2 && y < 263 + CTA_H / 2) ls_cancel();
    return 0;
}

static int ls_xfer_open(int recv, int phone) {
    ls.xfer_recv = recv;
    ls.x_phone = phone;
    if (ls.xfer) {
        widget_invalidate_force(ls.xfer, 0);
        return 1;
    }
    if (!(ls.xfer = page_open("lsxfer_page", ls_xfer_closed, page_keyup))) return 0;
    page_list(ls.xfer, ls.xfer, 0, recv ? "Receiving" : "Sending", 1, 48); /* the title; no rows */
    widget_on(ls.xfer, EVT_POINTER_UP_BEFORE, ls_xfer_touch, 0);
    widget_invalidate_force(ls.xfer, 0);
    return 1;
}

/* navigation.c's keys on the transfer page: Center is Cancel, the wheel has nothing to move. Return leaves it, the
 * transfer going on (its end is a toast). */
int localsend_key(void *top, unsigned key) {
    if (!ls.xfer || top != ls.xfer) return 0;
    if (key == KEY_CENTER) ls_cancel();
    return key == KEY_CENTER || key == KEY_NEXT || key == KEY_PREV;
}

/* The page as the mock-up draws it: the device's glyph, what happens, the 6 px progress and its numbers (or, while
 * the other device's user decides, a line and the spinner), Cancel. */
static void ls_xfer_paint(void *w, void *canvas) {
    ls_state s;
    ls_read(&s, 0);
    const ls_xfer *x = ls.xfer_recv ? &s.recv : &s.send;
    int dy = ls_screen_y(w), light = ipod_light(), waiting = !ls.xfer_recv && tk_strcmp(x->phase, "run");
    const char *alias = *x->alias ? x->alias : ls.to_alias;
    char stem[24], line[96], a[16], b[16];
    unsigned sec = tok(TOK_SECONDARY);
    tk_snprintf(stem, sizeof stem, "lsbig_%s_%c", ls.x_phone ? "phone" : "laptop", light ? 'l' : 'd');
    paint_art(w, canvas, theme_art(stem), 187 - 24, 96 - 24 - dy);
    tk_snprintf(line, sizeof line, ls.xfer_recv ? "Receiving from %s" : waiting ? "Waiting for %s" : "Sending to %s",
                alias);
    ui_text(canvas, line, 1, TYPE(20), 0xffffff, 0, 187, 146 - dy, 375 - 48, 1);
    if (waiting) {
        tk_snprintf(line, sizeof line, "Accept on %s to start", alias);
        ui_text(canvas, line, 0, TYPE(15), sec, 0, 187, 172 - dy, 375 - 48, 1);
        tk_snprintf(stem, sizeof stem, "btspin_%c_%d", light ? 'l' : 'd', spinner_frame());
        paint_art(w, canvas, theme_art(stem), 187 - 7, 204 - 7 - dy);
        spinner_painted(w, (const rect_t *)0);
    } else {
        rect_t track = { 24, 170 - dy, 375 - 24 - 28, 6 }, bar = track;
        unsigned total = x->kb ? x->kb : 1, done = x->kb_done < total ? x->kb_done : total;
        bar.w = (int)((unsigned)track.w * (done >> 4) / ((total >> 4) + 1)); /* no 64-bit product */
        if (total < 4096) bar.w = track.w * (int)done / (int)total;
        fill_box(canvas, &track, RGBA(tok(TOK_RAISED_2)), 3);
        if (bar.w > 0) fill_box(canvas, &bar, RGBA(scheme()->tones[0]), 3);
        tk_snprintf(line, sizeof line, "%d of %d files", x->done, x->files);
        ui_text(canvas, line, 0, TYPE(15), sec, 0, 24, 192 - dy, 160, 2);
        ls_size(a, sizeof a, done);
        ls_size(b, sizeof b, total);
        tk_snprintf(line, sizeof line, "%s of %s", a, b);
        ui_text(canvas, line, 0, TYPE(15), sec, 0, 375 - 28, 192 - dy, 180, 3);
        const char *base = strrchr(x->name, '/');
        ui_text(canvas, base ? base + 1 : x->name, 0, TYPE(13), tok(TOK_TERTIARY), 0, 187, 214 - dy, 375 - 48, 1);
    }
    ui_button(canvas, 187, 263 - dy, "Cancel", 0, 0, pointer_down());
}

/* ---- painting ---- */

/* None found: the empty state's stack with its glyph, as the mock-up draws it, centred between top and end (screen
 * rows): the house's compact form under rows has no glyph (feedback.c empty_compact), so the page paints it
 * (empty_paint). */
static void ls_none_paint(void *w, void *canvas, int top, int end, int glyph) {
    static const empty_t none = { "broadcast", "Looking for Devices", "Open LocalSend on a phone or computer", 0, 0, 0 };
    empty_t e = none;
    if (!glyph) e.glyph = 0; /* under Trusted Devices too: no room for it */
    empty_paint(w, canvas, &e, top + (end - top - (glyph ? LS_STACK : LS_WORDS)) / 2 - ls_screen_y(w));
}

/* ringnav_paint's, for the pages: the glyphs, the header's spinner, a value at a row's right, the captions and the
 * footer, Remove All's red, and the transfer page. */
void localsend_paint(void *w, void *canvas) {
    if (!w || !P(canvas, CANVAS_LCD) || (!ls.page && !ls.pick && !ls.share && !ls.trust && !ls.xfer)) return;
    if (w == ls.xfer) {
        ls_xfer_paint(w, canvas);
        return;
    }
    int light = ipod_light();
    if (w == ls.page && !ls.wifi_off) {
        char foot[80], name[40];
        tk_snprintf(foot, sizeof foot, "Visible as %s while this page is open", bt_name(name, sizeof name));
        ui_text(canvas, foot, 0, TYPE(13), tok(TOK_TERTIARY), 0, page_x(), LS_FOOT_Y - ls_screen_y(w), 375 - 2 * page_x(),
                2);
        if (ls.page_none && ls.view) {
            int top = ls_screen_y(P(ls.view, W_PARENT)) + I(P(ls.view, W_PARENT), W_H);
            ls_none_paint(w, canvas, top, LS_LIST_END, !ls.trusted_row);
        }
        return;
    }
    if (w == ls.share) {
        char stem[16];
        int dy = ls_screen_y(w);
        tk_snprintf(stem, sizeof stem, "lsfile_%c", light ? 'l' : 'd');
        paint_art(w, canvas, theme_art(stem), 32 - 7, 70 - 7 - dy);
        ui_text(canvas, ls.share_line, 0, TYPE(15), tok(TOK_TERTIARY), 0, 46, 70 - dy, 375 - 46 - 24, 2);
        if (ls.share_none && ls.share_view) {
            int top = ls_screen_y(P(ls.share_view, W_PARENT)) + I(P(ls.share_view, W_PARENT), W_H);
            ls_none_paint(w, canvas, top, 320, 1);
        }
        return;
    }
    void *view = P(w, W_PARENT);
    if (!view || (view != ls.view && view != ls.pick_view && view != ls.share_view && view != ls.trust_view)) return;
    int k = widget_get_prop_int(w, LS_KIND, 0), on = ringnav_row_selected(w), h = I(w, W_H);
    void *label = widget_count_children(w) ? widget_get_child(w, 0) : (void *)0;
    char art[32];
    if (k == K_HEAD) {
        const char *words = widget_get_prop_str(w, LS_VALUE, "");
        int tw = ui_text(canvas, words, 0, TYPE(15), tok(TOK_TERTIARY), 0, page_x(), h / 2 + 2, 200, 2);
        tk_snprintf(art, sizeof art, "btspin_%c_%d", light ? 'l' : 'd', spinner_frame());
        paint_art(w, canvas, theme_art(art), page_x() + tw + 7, h / 2 + 2 - 7);
        spinner_painted(w, (const rect_t *)0);
    } else if (k == K_CAPTION) {
        ui_text(canvas, widget_get_prop_str(w, LS_VALUE, ""), 0, TYPE(13), tok(TOK_TERTIARY), 0, page_x(), h / 2 + 2,
                375 - 2 * page_x(), 2);
    } else if (k == K_CTA) {
        const char *words = widget_get_prop_str(w, LS_VALUE, "");
        ui_cta_paint(canvas, &words, 1, LS_CTA_OUT, on ? 0 : -1, on && pointer_down());
    } else if (k == K_ACTION || k == K_REMOVE) {
        unsigned ink = RGBA(on ? 0xffffff : k == K_ACTION ? scheme()->tones[0] & 0xffffffu : tok(TOK_NEGATIVE));
        if (k == K_ACTION) paint_art(w, canvas, theme_art(on ? "lssend_w" : "lssend_a"), LS_GLYPH_X, (h - 18) / 2);
        if (label && widget_get_prop_int(label, "style:normal:text_color", 0) != (int)ink)
            widget_set_prop_int(label, "style:normal:text_color", (int)ink);
    } else if (k == K_DEVICE) {
        tk_snprintf(art, sizeof art, "lsdev_%s_%c", widget_get_prop_int(w, LS_DEV, 0) ? "phone" : "laptop",
                    on ? 'w' : light ? 'l' : 'd');
        paint_art(w, canvas, theme_art(art), 34 - LS_DEV_GLYPH / 2, (h - LS_DEV_GLYPH) / 2);
    } else if (k == K_VALUE) {
        const char *value = widget_get_prop_str(w, LS_VALUE, "");
        int end = 375 - geom_val(V_RIGHT_X) - (widget_count_children(w) > 1 ? VALUE_CHEVRON_GAP : 0),
            start = page_x() + (label ? ui_text_w(canvas, widget_get_prop_str(w, "_ls_label", ""), 0, TYPE(20)) : 0) +
                    KV_GAP;
        if (*value) ui_text(canvas, value, 0, TYPE(20), on ? 0xffffff : tok(TOK_SECONDARY), 1, end, h / 2, end - start, 3);
    }
}

/* ---- incoming requests ---- */

static void ls_card_answer(void *ctx, int row, int always) {
    char text[100];
    ls.asking = 0;
    tk_snprintf(text, sizeof text, "%s %d%s\n", ls.ask_id, row == 0, row == 0 && always ? " trust" : "");
    ls_put(LS_ANSWER, text);
    if (row == 0) ls_xfer_open(1, ls.x_phone);
}

/* The request in LS_ASK (id, alias, model, type, files, bytes), on the card, once. */
static void ls_ask(void) {
    char raw[512], id[72], alias[40], model[24], type[16], files[12], bytes[24];
    if (!state_read(LS_ASK, raw, sizeof raw)) {
        if (ls.asking) ls.asking = 0, confirm_close(); /* the sender gave up, or the daemon's time ran out */
        return;
    }
    const char *s = ls_field(raw + 1, id, sizeof id);
    s = ls_field(s, alias, sizeof alias);
    s = ls_field(s, model, sizeof model);
    s = ls_field(s, type, sizeof type);
    s = ls_field(s, files, sizeof files);
    ls_field(s, bytes, sizeof bytes);
    if (ls.asking || !tk_strcmp(id, ls.ask_id) || confirm_up()) return; /* asked, or another card first */
    char size[16];
    int n = atoi(files);
    ls_size(size, sizeof size, ls_kb(bytes));
    tk_snprintf(ls.card_name, sizeof ls.card_name, "%s", alias);
    tk_snprintf(ls.card_body, sizeof ls.card_body, "%d file%s \xc2\xb7 %s, saved to\nReceived on the SD card.", n,
                n == 1 ? "" : "s", size);
    ls.x_phone = !tk_strcmp(type, "mobile");
    static const char *const rows[] = { "Accept", "Decline" };
    awake_screen();
    ls.asking = confirm(&(confirm_t){ .title = "Receive from %s?",
                                      .name = ls.card_name,
                                      .body = ls.card_body,
                                      .rows = rows,
                                      .n_rows = 2,
                                      .kind = CONFIRM_CONSEQUENTIAL,
                                      .back = 1,
                                      .toggle = "Always Accept", /* as USB debugging's "Always Allow" */
                                      .timeout_ms = LS_ASK_MS,
                                      .timeout_row = 1,
                                      .glyph = ls.x_phone ? "lscard_phone" : "lscard_laptop",
                                      .answer = ls_card_answer });
    if (ls.asking) tk_snprintf(ls.ask_id, sizeof ls.ask_id, "%s", id);
}

static int ls_busy(const char *phase) {
    return !tk_strcmp(phase, "ask") || !tk_strcmp(phase, "wait") || !tk_strcmp(phase, "run");
}

static int ls_audio(const char *name) {
    static const char *const ext[] = { ".flac", ".mp3", ".wav", ".m4a", ".aac", ".ogg", ".opus", ".ape", ".dsf",
                                       ".dff", ".wv", ".aif", ".aiff", ".alac", ".cue", ".mka", ".wma" };
    const char *dot = strrchr(name, '.');
    for (unsigned i = 0; dot && i < sizeof ext / sizeof *ext; ++i)
        if (!strcasecmp(dot, ext[i])) return 1;
    return 0;
}

/* The files received whole since the last look: a library scan due when one is a song; the list emptied. */
static void ls_received(void) {
    char raw[4096];
    if (!state_read(LS_RECEIVED, raw, sizeof raw)) return;
    unlink(LS_RECEIVED);
    for (char *line = raw + 1, *nl; *line; line = nl + 1) {
        nl = ls_eol(line);
        int last = !*nl;
        *nl = 0;
        if (ls_audio(line)) ls.scan_due = 1;
        if (last) break;
    }
}

/* A phase changed: what the user sees of it. */
/* phone: the sender's glyph, as the device list has it (a trusted sender gets no card, which reads its type) */
static void ls_recv_moved(const ls_xfer *x, int phone) {
    char text[80];
    const char *ph = x->phase;
    if (!tk_strcmp(ph, "run") && x->trusted) {
        tk_snprintf(text, sizeof text, "Receiving from %s", x->alias);
        toast_later(phone ? "lstoast_phone" : "lstoast_laptop", text);
        return;
    }
    if (!tk_strcmp(ph, "done") || !tk_strcmp(ph, "failed") || !tk_strcmp(ph, "cancelled") ||
        !tk_strcmp(ph, "declined"))
        if (ls.xfer && ls.xfer_recv) window_close(ls.xfer);
    if (!tk_strcmp(ph, "done")) {
        tk_snprintf(text, sizeof text, "%d file%s saved to Received", x->done, x->done == 1 ? "" : "s");
        toast_later("usbtoast_ejected", text);
    } else if (!tk_strcmp(ph, "failed"))
        toast_later("bttoast_warn", "Some files didn't arrive");
    else if (!tk_strcmp(ph, "refused-room"))
        toast_later("bttoast_warn", "Not enough room on the SD card");
    else if (!tk_strcmp(ph, "refused-card"))
        toast_later("bttoast_warn", "No SD card");
    else if (!tk_strcmp(ph, "refused-hold"))
        toast_later("bttoast_warn", "The SD card is in use over USB");
}

static void ls_send_moved(const ls_xfer *x) {
    char text[80];
    const char *ph = x->phase, *alias = *x->alias ? x->alias : ls.to_alias;
    if (ls_busy(ph) || !tk_strcmp(ph, "idle")) {
        if (ls.xfer && !ls.xfer_recv) widget_invalidate_force(ls.xfer, 0);
        return;
    }
    if (ls.xfer && !ls.xfer_recv) window_close(ls.xfer);
    if (!tk_strcmp(ph, "done"))
        tk_snprintf(text, sizeof text, "Sent to %s", alias), toast_later("usbtoast_ejected", text);
    else if (!tk_strcmp(ph, "declined"))
        tk_snprintf(text, sizeof text, "%s declined", alias), toast_later("bttoast_warn", text);
    else if (!tk_strcmp(ph, "busy"))
        tk_snprintf(text, sizeof text, "%s is busy", alias), toast_later("bttoast_warn", text);
    else if (!tk_strcmp(ph, "failed"))
        tk_snprintf(text, sizeof text, "Couldn't send to %s", alias), toast_later("bttoast_warn", text);
}

/* power_poll, every LS_POLL_MS: the daemon kept running while it is needed and stopped once it is not, the card
 * held while a computer has it over USB, its state read and shown, and the library scanned after a receive. */
void localsend_poll(void) {
    unsigned now = time_now_ms();
    if (ls.at && now - ls.at < LS_POLL_MS) return;
    ls.at = now | !now;
    if (ls.scan_due && !ls.live && library_quiet()) {
        int ctx[4] = { 0 }; /* the whole library: stock's scan has no folder of its own to take (docs/localsend.md) */
        ls.scan_due = 0;
        navigator_to_with_context("dialog/updatemusic_dialog", ctx);
    }
    int pages = ls.page || ls.pick || ls.share || ls.trust || ls.xfer;
    if (!ls.live && !pages) return;
    ls_state s;
    unsigned sum;
    int read = ls_read(&s, &sum);
    int busy = read && (ls_busy(s.recv.phase) || ls_busy(s.send.phase));
    if (!pages && !busy && !ls.asking) {
        ls_stop();
        ls_received();
        return;
    }
    if (!ls_running()) {
        if (now - ls.started_at >= LS_RESTART_MS) ls_start();
        return;
    }
    if (navigator_window_is_exist("usbmode_page")) {
        if (access(LS_HOLD, 0)) ls_put(LS_HOLD, "usb\n");
    } else if (!access(LS_HOLD, 0))
        unlink(LS_HOLD);
    if (!read) return;
    ls_ask();
    if (tk_strcmp(s.recv.phase, ls.rphase)) {
        int phone = ls.x_phone;
        for (int i = 0; i < s.n; ++i)
            if (!tk_strcmp(s.dev[i].alias, s.recv.alias)) phone = s.dev[i].phone;
        if (*ls.rphase || tk_strcmp(s.recv.phase, "idle")) ls_recv_moved(&s.recv, phone);
        tk_snprintf(ls.rphase, sizeof ls.rphase, "%s", s.recv.phase);
    }
    if (tk_strcmp(s.send.phase, ls.sphase)) {
        if (tk_strcmp(s.send.phase, "idle")) ls_send_moved(&s.send);
        tk_snprintf(ls.sphase, sizeof ls.sphase, "%s", s.send.phase);
    }
    if (ls.xfer) widget_invalidate_force(ls.xfer, 0); /* the progress */
    ls_received();
    if (confirm_up()) return; /* the change is seen at the first poll after the card */
    if (ls.page && sum != ls.shown[P_MAIN]) timer_add(ls_page_show, (void *)1, 0);
    if (ls.share && sum != ls.shown[P_SHARE]) timer_add(ls_share_show, (void *)1, 0);
    if (ls.trust && sum != ls.shown[P_TRUST]) timer_add(ls_trust_show, (void *)1, 0);
    ls.shown[P_MAIN] = ls.shown[P_SHARE] = ls.shown[P_TRUST] = sum;
}

/* switch_wifitransport's hook: stock's Wi-Fi File Transfer server (thttpd on :8888) never starts; LocalSend has
 * its row (docs/localsend.md#what-it-replaces). Off still runs stock's, which only stops it. */
int ringnav_wifi_transfer(int on) { return on ? 0 : stock_wifi_transfer_trampoline(0); }
