/* q2wifi's device side (docs/internals.md#wi-fi-fault-recorder): q2guard calls wifi_tick each
 * tick and waits on the monitor's socket in bt_sleep (wifi_fd, wifi_drain). Everything here is a
 * read: files under /proc and /sys, and wpa_supplicant's ATTACH, STATUS, SCAN_RESULTS,
 * LIST_NETWORKS and SIGNAL_POLL. It never asks for a scan, a reconnect, an interface change or a
 * driver reload, as the owner ruled ("just persistently record it do not restart it itself").
 * With no wpa_supplicant (Wi-Fi off, Smart Wi-Fi's radio off) a tick is one access(); joined, a
 * STATUS a minute; neither opens a file. */
#include "q2wifi.h"
#include "q2log.h"

#ifndef WIFI_CTRL
#define WIFI_CTRL "/var/run/wpa_supplicant/wlan0" /* its control socket (smart_wifi.c's WPA_SOCK) */
#endif
#define WIFI_MS 300 /* an answer waited for this long at most */
#define WLAN0 PEQ_ROOT "/sys/class/net/wlan0"
#define DRV PEQ_ROOT "/proc/net/rtl8733bs/wlan0"
#define KMSG_DEV PEQ_ROOT "/dev/kmsg"
#define KMSG_READS 20000 /* records read at most for the boot snapshot */

static struct {
    card_log *log, *ulog;
    unsigned salt, start;
    int boot_done, boot_missing, listed;
    int mon;              /* the attached monitor, -1 none */
    unsigned mon_tried;   /* the last attach tried */
    int wpa;              /* wpa_supplicant's socket was there at the last tick */
    int joined;           /* COMPLETED, by its event or STATUS */
    int calm;             /* INACTIVE or DISABLED: nothing to join, asked as seldom as joined */
    unsigned asked_at;    /* the last STATUS while joined */
    unsigned joined_at, left_at; /* the last association and disassociation */
    int scan_new;         /* a scan finished since the last count */
    unsigned scan_at;     /* the last count */
    int scans, failed, fail_said; /* scans begun and failed since the last snapshot */
    struct wifi_watch w;
    struct wifi_scan last;
    char status[1024];    /* the last STATUS answer */
    int temp;
    const char *heat;
    int flush;
} wf;

static unsigned up_s(void) { return now_ms() / 1000; }
static void put(const char *what) {
    char b[600];
    wifi_line(b, sizeof b, time(0), up_s(), what);
    heat_add(wf.log, b);
}
/* "TAG name" and each line of text after it (already scrubbed), one log line each */
static void put_lines(const char *tag, const char *text) {
    char b[400];
    for (const char *l = text; l && *l;) {
        const char *e = strchr(l, '\n');
        int n = e ? (int)(e - l) : (int)strlen(l);
        if (n > 0) {
            snprintf(b, sizeof b, "%s %.*s", tag, n > 360 ? 360 : n, l);
            put(b);
        }
        l = e ? e + 1 : 0;
    }
}

/* ---- wpa_supplicant ---- */
struct sun {
    unsigned short family;
    char path[108];
};
/* cmd to its control socket from an autobound address, so nothing is left in /tmp: the answer into b,
 * its length, or -1 */
static int ask(const char *cmd, char *b, int size) {
    struct sun to = { AF_UNIX, WIFI_CTRL };
    unsigned short me = AF_UNIX;
    int got = -1, s = socket(AF_UNIX, SOCK_DGRAM | SOCK_CLOEXEC, 0);
    b[0] = 0;
    if (s < 0) return -1;
    int n = (int)strlen(cmd);
    if (!bind(s, (void *)&me, sizeof me) && !connect(s, (void *)&to, sizeof to) && send(s, cmd, n, 0) == n) {
        struct pollfd p = { s, POLLIN, 0 };
        got = poll(&p, 1, WIFI_MS) > 0 ? (int)read(s, b, size - 1) : -1;
        if (got >= 0) b[got] = 0;
    }
    close(s);
    return got;
}
static void attach(unsigned now) {
    struct sun to = { AF_UNIX, WIFI_CTRL };
    unsigned short me = AF_UNIX;
    char b[16];
    wf.mon_tried = now | !now;
    int s = socket(AF_UNIX, SOCK_DGRAM | SOCK_CLOEXEC | SOCK_NONBLOCK, 0);
    if (s < 0) return;
    if (!bind(s, (void *)&me, sizeof me) && !connect(s, (void *)&to, sizeof to) && send(s, "ATTACH", 6, 0) == 6) {
        struct pollfd p = { s, POLLIN, 0 };
        int n = poll(&p, 1, WIFI_MS) > 0 ? (int)read(s, b, sizeof b - 1) : -1;
        if (n >= 2 && !strncmp(b, "OK", 2)) {
            wf.mon = s;
            return;
        }
    }
    close(s);
}
static void detach(void) {
    if (wf.mon >= 0) close(wf.mon);
    wf.mon = -1;
}

/* ---- snapshots ---- */
static const char *const drv_files[] = { "fwstate",  "mlmext_state", "rf_info",       "trx_info",       "rx_signal",
                                         "rx_stat",  "mac_qinfo",    "scan_param",    "chan_plan",      "ps_info",
                                         "disconnect_info", "sdio_card_info", "sec_info" };
/* The driver's directory, names only, once a boot: what a later reading could take */
static void drv_list(void) {
    char b[400];
    int n = snprintf(b, sizeof b, "F drvdir");
    void *d = opendir(DRV);
    if (!d) return;
    for (struct dirent *e; (e = readdir(d)) && n < (int)sizeof b - 1;)
        if (e->d_name[0] != '.') n += snprintf(b + n, sizeof b - n, " %s", e->d_name);
    closedir(d);
    put(b), wf.listed = 1;
}
static void put_file(const char *tag, const char *path, int max) {
    static char raw[WIFI_PROC_MAX + 1], clean[WIFI_PROC_MAX + 256];
    if (slurp(path, raw, max + 1 < (int)sizeof raw ? max + 1 : (int)sizeof raw) <= 0) return;
    wifi_scrub(raw, wf.salt, clean, sizeof clean);
    put_lines(tag, clean);
}
/* "F tag" and the lines of path holding any of words */
static void put_matching(const char *tag, const char *path, const char *words) {
    static char raw[8192];
    char b[600];
    if (slurp(path, raw, sizeof raw) <= 0) return;
    int n = snprintf(b, sizeof b, "F %s ", tag);
    if (wifi_lines_with(raw, words, b + n, sizeof b - n)) put(b);
}
static void put_mem(void) {
    static char m[2048];
    char b[256], t[200];
    if (slurp(PEQ_ROOT "/proc/meminfo", m, sizeof m) > 0) {
        snprintf(b, sizeof b, "F mem ");
        wifi_mem_brief(m, b + 6, sizeof b - 6);
        put(b);
    }
    int raw;
    snprintf(t, sizeof t, "%s", wf.heat ? wf.heat : "");
    for (char *p = t; *p; ++p)
        if (*p == '\n') *p = 0;
    char *sp = strchr(t, ' '); /* the heat line without its time */
    snprintf(b, sizeof b, "F temp t=%d raw=%d heat=%s", wf.temp == GUARD_NONE ? -1 : wf.temp,
             read_int(PEQ_ROOT "/sys/class/power_supply/battery/temp", &raw) ? -1 : raw, sp ? sp + 1 : "-");
    put(b);
}
static void put_scan(void) {
    char b[200];
    int n = snprintf(b, sizeof b, "SNAP scan ");
    n += wifi_scan_text(&wf.last, b + n, sizeof b - n);
    snprintf(b + n, sizeof b - n, " scans=%d failed=%d", wf.scans, wf.failed);
    put(b);
    wf.scans = wf.failed = 0;
}
/* One snapshot of the radio (a deaf period's start, repeat or end): wpa_supplicant, the driver, the bus */
static void snapshot(int assoc) {
    char b[600], s[400];
    wifi_status_line(wf.status, wf.salt, s, sizeof s);
    snprintf(b, sizeof b, "SNAP status %s", s);
    put(b);
    put_scan();
    if (assoc && ask("SIGNAL_POLL", b, sizeof b) > 0) { /* RSSI=, LINKSPEED=, NOISE=, FREQUENCY= */
        for (char *p = b; *p; ++p)
            if (*p == '\n') *p = ' ';
        snprintf(s, sizeof s, "SNAP signal %s", b);
        put(s);
    }
    if (!wf.listed) drv_list();
    for (unsigned i = 0; i < sizeof drv_files / sizeof drv_files[0]; ++i) {
        char path[256], tag[40];
        snprintf(path, sizeof path, DRV "/%s", drv_files[i]);
        snprintf(tag, sizeof tag, "F %s", drv_files[i]);
        put_file(tag, path, WIFI_PROC_MAX);
    }
    put_matching("wireless", PEQ_ROOT "/proc/net/wireless", "wlan0\0");
    put_matching("netdev", PEQ_ROOT "/proc/net/dev", "wlan0\0");
    put_matching("irq", PEQ_ROOT "/proc/interrupts", "mmc\0sdio\0msc\0");
    put_mem();
    wf.flush = 1;
}
static const char *kind(int k) { return k == WIFI_DEAF_EMPTY ? "empty" : "nojoin"; }
/* What wifi_step said, recorded */
static void act(int a, unsigned now) {
    char b[300], sc[120], st[24];
    wifi_scan_text(&wf.last, sc, sizeof sc);
    wifi_state(wf.status, st, sizeof st);
    switch (a) {
    case WA_START:
        snprintf(b, sizeof b, "DEAF start kind=%s after=%u s state=%s %s", kind(wf.w.deaf),
                 (wf.w.deaf == WIFI_DEAF_EMPTY ? WIFI_EMPTY_MS : WIFI_NOJOIN_MS) / 1000, st, sc);
        put(b);
        snprintf(b, sizeof b, "deaf radio (%s): state=%s %s, snapshot in q2-wifi", kind(wf.w.deaf), st, sc);
        ulog_add(wf.ulog, time(0), now, 'W', "wifi", b);
        snapshot(0);
        break;
    case WA_AGAIN:
    case WA_EMPTY:
        snprintf(b, sizeof b, "DEAF %s n=%d for=%u s state=%s %s", a == WA_EMPTY ? "empty" : "again", wf.w.snaps,
                 (now - wf.w.deaf_at) / 1000, st, sc);
        put(b);
        snapshot(0);
        break;
    case WA_END: {
        static const char *const by[] = { "", "scan", "assoc", "off" };
        snprintf(b, sizeof b, "DEAF end by=%s lasted=%u s state=%s %s", by[wf.w.end], wf.w.lasted / 1000,
                 st[0] ? st : "-", sc);
        put(b);
        snprintf(b, sizeof b, "deaf radio ended (%s) after %u s", by[wf.w.end], wf.w.lasted / 1000);
        ulog_add(wf.ulog, time(0), now, 'I', "wifi", b);
        if (wf.w.end != WIFI_END_OFF) snapshot(wf.w.end == WIFI_END_ASSOC);
        else wf.flush = 1;
        break;
    }
    }
}
static void step(unsigned now, int state, int nets, int known) {
    struct wifi_obs o = { now, state, nets, known };
    act(wifi_step(&wf.w, &o), now);
}
/* SCAN_RESULTS and LIST_NETWORKS counted (the names compared in memory, never kept) */
static int count(unsigned now) {
    static char r[8192], nw[2048];
    wf.scan_new = 0, wf.scan_at = now | !now;
    if (ask("SCAN_RESULTS", r, sizeof r) < 0) return -1;
    if (ask("LIST_NETWORKS", nw, sizeof nw) < 0) nw[0] = 0;
    wifi_scan_count(r, nw[0] ? nw : 0, &wf.last);
    return wf.last.n;
}

/* ---- the boot ---- */
/* The kernel's records since boot through a /dev/kmsg descriptor of its own (q2guard's, the unified
 * log's feed, keeps its place): those wifi_kmsg_keep keeps, a failure's and the WIFI_KMSG_AFTER after
 * it, as "K [S.US] text". A regular file (the host test's) is read the same way, line by line. */
static void boot_kernel(void) {
    static char buf[4096], clean[1200];
    char line[1100];
    int fd = open(KMSG_DEV, O_RDONLY | O_NONBLOCK | O_CLOEXEC), have = 0, used = 0, after = 0, records = 0;
    if (fd < 0) return put("K none: /dev/kmsg does not open");
    for (int k = 0; k < KMSG_READS; ++k) {
        int n = (int)read(fd, buf + have, sizeof buf - 1 - have);
        if (n < 0 && *__errno_location() == EPIPE) continue; /* overwritten before it was read */
        if (n <= 0) break;
        have += n, buf[have] = 0;
        char *l = buf, *e;
        while ((e = strchr(l, '\n'))) {
            *e = 0;
            /* "PRI,SEQ,USEC,FLAGS;text"; a line starting with a space is a record's dictionary */
            char *semi = l[0] != ' ' ? strchr(l, ';') : 0, *c1 = strchr(l, ','), *c2 = c1 ? strchr(c1 + 1, ',') : 0;
            if (semi && c2 && c2 < semi) {
                ++records;
                int keep = wifi_kmsg_keep(semi + 1);
                if (keep == 2) after = WIFI_KMSG_AFTER;
                else if (after) keep = 2, --after;
                if (keep && used < (keep == 2 ? WIFI_KMSG_MAX : WIFI_KMSG_PLAIN)) {
                    char us[24];
                    int d = 0;
                    for (const char *p = c2 + 1; *p >= '0' && *p <= '9' && d < 20;) us[d++] = *p++;
                    us[d] = 0;
                    /* the kernel's microseconds as seconds, by their digits: no 64-bit division on the Q2 */
                    if (d > 6) snprintf(line, sizeof line, "[%.*s.%s] %.900s", d - 6, us, us + d - 6, semi + 1);
                    else snprintf(line, sizeof line, "[0.%06d] %.900s", atoi(us), semi + 1);
                    wifi_scrub(line, wf.salt, clean, sizeof clean);
                    put_lines("K", clean);
                    used += (int)strlen(clean) + 32;
                }
            }
            l = e + 1;
        }
        have = (int)(buf + have - l);
        memmove(buf, l, have);
        if (have >= (int)sizeof buf - 1) have = 0; /* a line longer than the buffer: dropped */
    }
    close(fd);
    snprintf(line, sizeof line, "K read %d records, kept %d bytes", records, used);
    put(line);
}
static void boot_check(unsigned now) {
    static char b[8192];
    char line[400];
    int there = !access(WLAN0, F_OK);
    wf.boot_done = 1, wf.boot_missing = !there;
    int n = snprintf(line, sizeof line, "BOOT wlan0=%s after=%u s module=", there ? "yes" : "no",
                     (now - wf.start) / 1000);
    if (slurp(PEQ_ROOT "/proc/modules", b, sizeof b) <= 0 || !wifi_lines_with(b, "8733bs\0", line + n, sizeof line - n))
        snprintf(line + n, sizeof line - n, "none");
    put(line);
    wf.flush = 1;
    if (there) return;
    ulog_add(wf.ulog, time(0), now, 'W', "wifi", "no wlan0 30 s after start: the driver did not come up (q2-wifi)");
    boot_kernel();
    put_file("F meminfo", PEQ_ROOT "/proc/meminfo", WIFI_PROC_MAX);
    put_file("F buddyinfo", PEQ_ROOT "/proc/buddyinfo", WIFI_PROC_MAX);
    char *vm = calloc(1, 262144);
    if (vm) {
        struct wifi_vm v;
        int got = slurp(PEQ_ROOT "/proc/vmallocinfo", vm, 262144);
        if (got >= 0) {
            wifi_vmalloc_sum(vm, &v);
            n = snprintf(line, sizeof line, "F ");
            n += wifi_vm_text(&v, line + n, sizeof line - n);
            snprintf(line + n, sizeof line - n, "%s", got >= 262143 ? " (cut)" : "");
            put(line);
        }
        free(vm);
    }
    put_matching("irq", PEQ_ROOT "/proc/interrupts", "mmc\0sdio\0msc\0");
    put_mem();
}

/* ---- events ---- */
static void event(const char *msg, unsigned now) {
    char out[300], b[600];
    int reason, local, e = wifi_event(msg, wf.salt, out, sizeof out, &reason, &local);
    switch (e) {
    case WE_CONNECTED:
        if (ask("STATUS", wf.status, sizeof wf.status) > 0) {
            char f[16];
            const char *q = strstr(wf.status, "freq=");
            int k = 0;
            for (q = q ? q + 5 : ""; *q >= '0' && *q <= '9' && k < 15;) f[k++] = *q++;
            f[k] = 0;
            snprintf(b, sizeof b, "%s freq=%s", out, k ? f : "-");
        } else {
            snprintf(b, sizeof b, "%s", out);
        }
        if (wf.left_at) snprintf(b + strlen(b), sizeof b - strlen(b), " down=%u s", (now - wf.left_at) / 1000);
        put(b);
        wf.joined = 1, wf.joined_at = wf.asked_at = now | !now, wf.flush = 1;
        step(now, WS_COMPLETED, -1, 0);
        break;
    case WE_DISCONNECTED: {
        static char d[1024];
        int n = snprintf(b, sizeof b, "%s", out);
        if (wf.joined && wf.joined_at) n += snprintf(b + n, sizeof b - n, " up=%u s", (now - wf.joined_at) / 1000);
        if (slurp(DRV "/disconnect_info", d, sizeof d) > 0) { /* the driver's own: "last disconnect reason: ..." */
            static char c[1200];
            wifi_scrub(d, wf.salt, c, sizeof c);
            n += snprintf(b + n, sizeof b - n, " drv=");
            if (!wifi_lines_with(c, "disconnect\0", b + n, sizeof b - n)) snprintf(b + n, sizeof b - n, "-");
        }
        put(b);
        wf.joined = wf.calm = 0, wf.asked_at = 0, wf.left_at = now | !now, wf.flush = 1;
        break;
    }
    case WE_TERMINATING: put(out), detach(), wf.joined = 0; break;
    case WE_SCAN_STARTED: ++wf.scans; break;
    case WE_SCAN_RESULTS: wf.scan_new = 1, wf.fail_said = 0, wf.calm = 0; break;
    case WE_SCAN_FAILED:
        ++wf.failed;
        if (!wf.fail_said++) put(out); /* the first of a run; the rest are counted in the next snapshot */
        break;
    case WE_LOG: put(out), wf.flush = 1; break;
    }
}
void wifi_drain(void) {
    char b[1024];
    unsigned now = now_ms();
    for (int k = 0; wf.mon >= 0 && k < 256; ++k) {
        int n = (int)read(wf.mon, b, sizeof b - 1);
        if (n <= 0) break;
        b[n] = 0;
        event(b, now);
    }
}

/* ---- each tick ---- */
void wifi_init(card_log *l, card_log *u, int again) {
    char id[64];
    wf.log = l, wf.ulog = u, wf.mon = -1, wf.boot_done = again, wf.temp = GUARD_NONE;
    wf.salt = wifi_salt(slurp(PEQ_ROOT "/proc/sys/kernel/random/boot_id", id, sizeof id) > 0 ? id : "");
}
int wifi_fd(void) { return wf.mon; }
int wifi_flush_due(void) {
    int due = wf.flush;
    wf.flush = 0;
    return due;
}
void wifi_tick(unsigned now, int temp, const char *heat) {
    char b[80];
    wf.temp = temp, wf.heat = heat;
    if (!wf.start) wf.start = now | !now;
    wifi_drain();
    if (!wf.boot_done && now - wf.start >= WIFI_BOOT_MS) boot_check(now);
    if (wf.boot_missing && !access(WLAN0, F_OK)) {
        snprintf(b, sizeof b, "BOOT wlan0 appeared after=%u s", (now - wf.start) / 1000);
        put(b), wf.boot_missing = 0, wf.flush = 1;
    }
    if (access(WIFI_CTRL, F_OK)) { /* no wpa_supplicant: Wi-Fi off, or Smart Wi-Fi's radio off */
        if (wf.wpa) put("WPA gone"), detach();
        wf.wpa = wf.joined = 0, wf.status[0] = 0;
        step(now, WS_NONE, -1, 0);
        return;
    }
    if (!wf.wpa) put("WPA up"), wf.wpa = 1, wf.mon_tried = 0;
    if (wf.mon < 0 && (!wf.mon_tried || now - wf.mon_tried >= WIFI_RETRY_MS)) attach(now);
    if ((wf.joined || wf.calm) && now - wf.asked_at < WIFI_JOINED_MS) return; /* joined and healthy: nothing */
    if (ask("STATUS", wf.status, sizeof wf.status) < 0) return;  /* not answering: as it was */
    int ws = wifi_state(wf.status, b, sizeof b);
    wf.asked_at = now | !now;
    if (ws == WS_COMPLETED) {
        if (!wf.joined) wf.joined = 1, wf.joined_at = now | !now; /* joined with its event missed */
        step(now, ws, -1, 0);
        return;
    }
    wf.joined = 0, wf.calm = ws == WS_INACTIVE || ws == WS_DISABLED;
    int nets = -1;
    if (ws != WS_NONE && ws != WS_DISABLED && ws != WS_INACTIVE &&
        (wf.scan_new || !wf.scan_at || now - wf.scan_at >= WIFI_SCAN_MS))
        nets = count(now);
    step(now, ws, nets, wf.last.known);
}
