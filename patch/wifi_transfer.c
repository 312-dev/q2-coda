/* Wi-Fi transfer's PIN and idle timer (docs/internals.md#wi-fi-transfer-pin). */
#include "navigation.h"

/* Stock runs Wi-Fi transfer's web server, thttpd on port 8888, for as long as its page is open: the
 * page's init calls switch_wifitransport(1), which starts it, and the page's close handler calls
 * switch_wifitransport(0), which kills it. ringnav_wifi_transfer stands in front of that switch.
 * Before thttpd starts it writes a fresh PIN and session token, both from /dev/urandom, to WT_DIR
 * in RAM (/tmp is a tmpfs), where the web page's CGI (assets/web/q2cgi) checks every request
 * against them; after thttpd is killed it removes them. ringnav_wifi_transfer_page shows the PIN on
 * the page, above the address. wifi_transfer_poll closes the page, which stops the server as Return
 * would, once no request has been admitted for WT_IDLE_MS and none is running.
 *
 * wt: live, a session this file started and has not ended; pin, its six digits; locked, the page
 * shows the lockout; at, the last poll; idle_at, the last sign of use; seen, the mtime of q2cgi's
 * mark at the last poll; win, pin_l, note_l, the page and its two labels. */
#define WT_DIR "/tmp/q2wifi"
#define WT_TRIES 5 /* q2cgi's MAXTRIES: try.1 to try.5 all taken is the lockout */
#define WT_IDLE_MS (10 * 60 * 1000u)
#define WT_POLL_MS 1000u
static struct {
    int live, locked;
    unsigned at, idle_at, seen[2];
    void *win, *pin_l, *note_l;
    char pin[8];
} wt __attribute__((section(".scratch")));

static int wt_random(unsigned char *b, unsigned n) {
    int fd = open("/dev/urandom", 0 /* O_RDONLY */);
    if (fd < 0) return 0;
    unsigned got = 0;
    while (got < n) {
        int r = read(fd, b + got, n - got);
        if (r <= 0) break;
        got += (unsigned)r;
    }
    close(fd);
    return got == n;
}

/* The session's files gone, and the folder too when gone is set. try.N are folders (q2cgi claims a
 * try with mkdir), the rest files. */
static void wt_clear(int gone) {
    void *d = opendir(WT_DIR);
    for (struct dirent *e; d && (e = readdir(d));) {
        if (e->d_name[0] == '.') continue;
        char path[64];
        snprintf(path, sizeof path, WT_DIR "/%s", e->d_name);
        if (unlink(path)) rmdir(path);
    }
    if (d) closedir(d);
    if (gone) rmdir(WT_DIR);
}

static int wt_write(const char *name, const char *text) {
    char path[32];
    snprintf(path, sizeof path, WT_DIR "/%s", name);
    int fd = open(path, 0x501 /* MIPS O_WRONLY | O_CREAT | O_EXCL */, 0600), n = (int)strlen(text);
    if (fd < 0) return 0;
    int ok = write(fd, text, (unsigned)n) == n;
    close(fd);
    return ok;
}

/* A new session: WT_DIR (root's alone) with a six-digit PIN and a 32-hex-digit token. The PIN is a
 * 32-bit draw below 4,294,000,000, the largest multiple of a million that fits, taken mod a
 * million, so every PIN is as likely. Fails, leaving nothing, when /dev/urandom can't be read. */
static int wt_start(void) {
    unsigned char b[16 + 4 * 8];
    unsigned x = 0, ok = 0;
    if (!wt_random(b, sizeof b)) return 0;
    for (unsigned k = 0; k < 8 && !ok; ++k) {
        x = (unsigned)b[16 + 4 * k] | (unsigned)b[17 + 4 * k] << 8 | (unsigned)b[18 + 4 * k] << 16 |
            (unsigned)b[19 + 4 * k] << 24;
        ok = x < 4294000000u;
    }
    if (!ok) return 0; /* eight draws over the line: a broken source, not bad luck (p = 2^-79) */
    char token[33];
    for (int i = 0; i < 16; ++i) snprintf(token + 2 * i, 3, "%02x", b[i]);
    snprintf(wt.pin, sizeof wt.pin, "%06u", x % 1000000u);
    wt_clear(0);
    mkdir(WT_DIR, 0700);
    if (!wt_write("pin", wt.pin) || !wt_write("token", token)) {
        wt_clear(1);
        return 0;
    }
    memset(b, 0, sizeof b);
    memset(token, 0, sizeof token);
    return 1;
}

/* switch_wifitransport's hook: on, a new session before thttpd starts, and no thttpd without one
 * (q2cgi would refuse every request anyway); off, thttpd killed first, then the session. */
int ringnav_wifi_transfer(int on) {
    if (on) {
        if (!wt_start()) {
            wt.pin[0] = 0;
            return 0;
        }
        wt.live = 1;
        wt.locked = 0;
        wt.idle_at = 0;
        wt.seen[0] = wt.seen[1] = 0;
        return stock_wifi_transfer_trampoline(1);
    }
    int r = stock_wifi_transfer_trampoline(0);
    wt_clear(1);
    wt.live = 0;
    memset(wt.pin, 0, sizeof wt.pin);
    return r;
}

/* The PIN in two groups of three, as a phone shows a code. */
static void wt_show(void) {
    if (!wt.pin_l) return;
    char text[16] = "No PIN";
    if (wt.locked)
        snprintf(text, sizeof text, "Locked");
    else if (wt.pin[0])
        snprintf(text, sizeof text, "%.3s %.3s", wt.pin, wt.pin + 3);
    widget_set_text_utf8(wt.pin_l, text);
    widget_set_text_utf8(wt.note_l, wt.locked   ? "Go back and reopen for a new PIN"
                                    : wt.pin[0] ? "PIN for the web page"
                                                : "Wi-Fi transfer could not start");
}

/* systemset_wifitransport_page_init's hook: stock builds the page and starts the server (the hook
 * above), then the PIN takes the place of the page's picture (img_wifitransport, a note above a
 * radio wave), between the title and the address, which the screen has no room below. */
int ringnav_wifi_transfer_page(void *win, void *ctx) {
    int r = stock_wifi_transfer_page_trampoline(win, ctx);
    void *art = win ? widget_lookup(win, "img_wifitransport", 1) : (void *)0;
    wt.win = win;
    wt.pin_l = wt.note_l = (void *)0;
    if (!art) return r;
    widget_set_visible(art, 0, 0);
    void *parent = P(art, W_PARENT);
    int y = I(art, W_Y) + (I(art, W_H) - 92) / 2; /* the 24 px line, 4 px, the 64 px PIN */
    wt.note_l = label(parent, 0, y, 375, 24, "s_scrlabel_white20c", 18, 0xff9a9a9au);
    wt.pin_l = label(parent, 0, y + 28, 375, 64, "s_scrlabel_white20c", 56, 0xffffffffu);
    wt_show();
    return r;
}

/* The open Wi-Fi transfer page, or 0. */
static void *wt_page(void) {
    void *wm = window_manager();
    for (unsigned i = 0, n = widget_count_children(wm); i < n; ++i) {
        void *w = widget_get_child(wm, i);
        if (!tk_strcmp(widget_get_prop_str(w, "name", ""), "wifitransport_page")) return w;
    }
    return (void *)0;
}

/* A request q2cgi is still answering: its busy.<pid> mark, with that process alive. A mark whose
 * process died without its exit trap (killed) counts for nothing. */
static int wt_busy(void) {
    int busy = 0;
    void *d = opendir(WT_DIR);
    for (struct dirent *e; d && !busy && (e = readdir(d));) {
        if (strncmp(e->d_name, "busy.", 5)) continue;
        char proc[32];
        snprintf(proc, sizeof proc, "/proc/%s", e->d_name + 5);
        busy = !access(proc, 0);
    }
    if (d) closedir(d);
    return busy;
}

/* power_poll, every WT_POLL_MS while a session is live: the page shows the lockout once q2cgi has
 * given out the last try, and closes after WT_IDLE_MS unused. Use is q2cgi's seen mark changing
 * (its mtime, never compared with demo's clock) or a request running; the time is counted on the
 * monotonic clock, so a clock set by a time sync can't end a session early. */
void wifi_transfer_poll(void) {
    unsigned now = time_now_ms();
    if (!wt.live || (wt.at && now - wt.at < WT_POLL_MS)) return;
    wt.at = now | !now;
    void *page = wt_page();
    if (page != wt.win)
        wt.win = wt.pin_l = wt.note_l = (void *)0; /* not the page the labels are on */
    if (!wt.idle_at) wt.idle_at = now;
    unsigned sx[64]; /* struct statx: stx_mtime's seconds at byte 112, its nanoseconds at 120 */
    if (!ringnav_statx(-100, WT_DIR "/seen", 0, 0x40 /* MTIME */, sx) &&
        (sx[28] != wt.seen[0] || sx[30] != wt.seen[1])) {
        wt.seen[0] = sx[28], wt.seen[1] = sx[30];
        wt.idle_at = now;
    }
    if (wt_busy()) wt.idle_at = now;
    char last[32];
    snprintf(last, sizeof last, WT_DIR "/try.%d", WT_TRIES);
    int locked = !access(last, 0);
    if (locked != wt.locked) wt.locked = locked, wt_show();
    if (now - wt.idle_at < WT_IDLE_MS) return;
    if (page)
        window_close(page); /* its close handler: switch_wifitransport(0), so the hook above */
    else
        ringnav_wifi_transfer(0);
    toast("Wi-Fi transfer turned off after 10 minutes unused");
}
