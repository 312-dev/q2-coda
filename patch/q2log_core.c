/* The unified log's parsers, guesses, rotation and snapshot (docs/internals.md#unified-log): no
 * reads of the device but the files it is handed, so test/unit/test_q2log.py runs it on this
 * machine. q2guard.c reads the feeds and calls these; the lines wait in card_store.c's RAM ring
 * until its writing child appends them (q2guard_core.c's heat log works the same way). */
#include "q2log.h"

/* ---- a line's level and source ---- */

static int lower(int c) { return c >= 'A' && c <= 'Z' ? c + 32 : c; }
static int alnum(int c) { return (c >= '0' && c <= '9') || (lower(c) >= 'a' && lower(c) <= 'z'); }

/* w (lower case) in s, any case; whole: only as a word of its own ("bt" not in "subtitle") */
static int has(const char *s, int n, const char *w, int whole) {
    int k = (int)strlen(w);
    for (int i = 0; i + k <= n; ++i) {
        int j = 0;
        while (j < k && lower(s[i + j]) == w[j]) ++j;
        if (j < k) continue;
        if (!whole || ((!i || !alnum(s[i - 1])) && (i + k == n || !alnum(s[i + k])))) return 1;
    }
    return 0;
}

/* Who a line names, in this order: the player first, as "hciplayer" holds "hci". */
static const struct {
    const char *word, *src;
    int whole;
} names[] = {
    { "q2player", "player", 0 },
    { "mplayer", "player", 0 },
    { "playback", "player", 0 },
    { "audio codec", "player", 0 },
    { "hciplayer", "player", 0 },
    { "alsa", "player", 0 },
    { "decoder", "player", 0 },
    { "bluealsa", "bt", 0 },
    { "rtk_hci", "bt", 0 },
    { "bluetooth", "bt", 0 },
    { "hci", "bt", 1 },
    { "hci0", "bt", 1 },
    { "bt", "bt", 1 },
    { "a2dp", "bt", 0 },
    { "l2cap", "bt", 0 },
    { "wpa_", "wifi", 0 },
    { "wlan", "wifi", 0 },
    { "wifi", "wifi", 0 },
    { "wi-fi", "wifi", 0 },
    { "udhcpc", "wifi", 0 },
    { "8733bs", "wifi", 0 },
    { "usb", "usb", 0 },
    { "gadget", "usb", 0 },
    { "udc", "usb", 1 },
};
static const char *const errors[] = { "error",   "fail",   "fatal",   "panic",   "cannot",
                                      "can't",   "unable", "denied",  "refused", "timed out",
                                      "timeout", "abort",  "segfault" };

char ulog_guess(const char *s, int n, const char **src) {
    *src = "ui"; /* demo itself, and stock's lines that name nothing */
    for (unsigned i = 0; i < sizeof names / sizeof *names; ++i)
        if (has(s, n, names[i].word, names[i].whole)) {
            *src = names[i].src;
            break;
        }
    for (unsigned i = 0; i < sizeof errors / sizeof *errors; ++i)
        if (has(s, n, errors[i], 0)) return 'E';
    return has(s, n, "warn", 0) ? 'W' : 'I';
}

/* ---- into the ring ---- */

/* A run of the same line (a driver that fails ten times a second) is one line and a count: the
 * first goes in, the rest are counted, and a "last line repeated N more times" line, at the last
 * one's time, goes in when another line comes or ulog_repeats is called. One run is kept, for the
 * last ring a line went to. */
static struct {
    card_log *l;
    unsigned hash, n;
    long wall;
    unsigned up_ms;
    char level, src[12];
} rep;

static unsigned fnv(const char *s, int n, unsigned h) {
    for (int i = 0; i < n; ++i) h = (h ^ (unsigned char)s[i]) * 16777619u;
    return h;
}

static void stamp(card_log *l, long wall, unsigned up_ms, char level, const char *src,
                  const char *s, int n) {
    char line[ULOG_PART + 64];
    int len = ulog_line(line, sizeof line, wall, up_ms, level, src, s, n);
    card_log_append(l, line, (unsigned)len);
}

void ulog_repeats(card_log *l) {
    if (rep.l != l || !rep.n) return;
    char b[64];
    int n = snprintf(b, sizeof b, "last line repeated %u more times", rep.n);
    stamp(l, rep.wall, rep.up_ms, rep.level, rep.src, b, n);
    rep.n = 0; /* the run goes on being counted from here */
}

/* 1 when this line repeats the last one: counted, not added */
static int repeated(card_log *l, long wall, unsigned up_ms, char level, const char *src,
                    const char *msg, int n) {
    unsigned h = fnv(msg, n, fnv(src, (int)strlen(src), 2166136261u ^ (unsigned char)level));
    if (rep.l == l && rep.hash == h) {
        ++rep.n, rep.wall = wall, rep.up_ms = up_ms;
        return 1;
    }
    if (rep.l) ulog_repeats(rep.l);
    rep.l = l, rep.hash = h, rep.n = 0, rep.level = level;
    int k = 0;
    while (src[k] && k < (int)sizeof rep.src - 1) rep.src[k] = src[k], ++k;
    rep.src[k] = 0;
    return 0;
}

/* 1 when the line went in, 0 when it was a repeat, counted */
static int add_line(card_log *l, long wall, unsigned up_ms, char level, const char *src,
                    const char *s, int n) {
    if (repeated(l, wall, up_ms, level, src, s, n)) return 0;
    stamp(l, wall, up_ms, level, src, s, n);
    return 1;
}

void ulog_add(card_log *l, long wall, unsigned up_ms, char level, const char *src,
              const char *msg) {
    add_line(l, wall, up_ms, level, src, msg, (int)strlen(msg));
}

char ulog_add_raw(card_log *l, long wall, unsigned up_ms, const char *s, int n) {
    while (n && (s[n - 1] == '\n' || s[n - 1] == '\r' || s[n - 1] == ' ')) --n;
    int start = 0;
    while (start < n && (s[start] == ' ' || s[start] == '\t')) ++start;
    if (start == n) return 0;
    if (ulog_is_line(s, n)) {
        if (n > ULOG_PART) n = ULOG_PART;
        int i = 21; /* past the time and its space: the uptime, then the level and the source */
        while (s[i] != ' ') ++i;
        char level = s[i + 1], src[12];
        int k = 0, m = i + 3;
        while (m < n && s[m] != ' ' && k < (int)sizeof src - 1) src[k++] = s[m++];
        src[k] = 0;
        /* a repeat of demo's: its uptime is in the line, its wall time the run's */
        unsigned up = 0;
        for (int d = 21; d < i; ++d) up = up * 10 + (unsigned)(s[d] - '0');
        if (repeated(l, wall, up, level, src, s + m, n - m)) return 0;
        char line[ULOG_PART + 2];
        memcpy(line, s, n);
        line[n] = '\n';
        card_log_append(l, line, (unsigned)n + 1);
        return level;
    }
    const char *src;
    char level = ulog_guess(s + start, n - start, &src);
    if (repeated(l, wall, up_ms, level, src, s + start, n - start)) return 0;
    stamp(l, wall, up_ms, level, src, s + start, n - start);
    return level;
}

/* ---- a file another process appends to ---- */

void ulog_tail_at(struct ulog_tail *t, const char *path, int from_end) {
    t->path = path, t->off = 0, t->plen = 0;
    if (!from_end) return;
    int fd = open(path, O_RDONLY | O_CLOEXEC);
    if (fd < 0) return;
    long size = lseek(fd, 0, SEEK_END);
    close(fd);
    t->off = size > 0 ? size : 0;
}

/* buf's lines into l, after the start held from the last read; a start with no end yet is held */
static int take(struct ulog_tail *t, card_log *l, long wall, unsigned up_ms, const char *buf,
                int n) {
    int got = 0;
    for (int i = 0; i < n; ++i) {
        if (buf[i] != '\n') {
            if (t->plen < ULOG_PART) t->part[t->plen++] = buf[i];
            if (t->plen < ULOG_PART) continue;
        }
        /* a line's end, or its start cut at ULOG_PART */
        char level = ulog_add_raw(l, wall, up_ms, t->part, t->plen);
        t->plen = 0;
        if (level) got |= 1 | (level == 'E' || level == 'W' ? 2 : 0);
        if (buf[i] != '\n') /* the cut line goes on: skip to its end */
            while (i + 1 < n && buf[i + 1] != '\n') ++i;
    }
    return got;
}

/* path from off to its end (max bytes at most) into l; the new offset, or -1 for no file */
static long read_from(struct ulog_tail *t, const char *path, long off, int max, card_log *l,
                      long wall, unsigned up_ms, int *got, long *size) {
    static char buf[ULOG_READ_MAX];
    int fd = open(path, O_RDONLY | O_CLOEXEC);
    if (fd < 0) return -1;
    *size = lseek(fd, 0, SEEK_END);
    long want = *size - off;
    if (want > max) want = max;
    int n = 0;
    if (want > 0 && lseek(fd, off, SEEK_SET) == off) n = (int)read(fd, buf, (size_t)want);
    close(fd);
    if (n <= 0) return off;
    *got |= take(t, l, wall, up_ms, buf, n);
    return off + n;
}

int ulog_tail(struct ulog_tail *t, card_log *l, long wall, unsigned up_ms) {
    int got = 0;
    long size = 0, old = 0;
    int fd = open(t->path, O_RDONLY | O_CLOEXEC);
    if (fd < 0) return 0;
    size = lseek(fd, 0, SEEK_END);
    close(fd);
    if (size < t->off) { /* rolled: what was added before the roll is at the end of path.1 */
        char p1[160];
        snprintf(p1, sizeof p1, "%s.1", t->path);
        read_from(t, p1, t->off, ULOG_READ_MAX, l, wall, up_ms, &got, &old);
        t->off = 0;
    }
    long off = read_from(t, t->path, t->off, ULOG_READ_MAX, l, wall, up_ms, &got, &size);
    if (off >= 0) t->off = off;
    return got;
}

/* ---- the kernel's ---- */

char ulog_kmsg(card_log *l, long wall, const char *rec, int n) {
    int i = 0, pri = 0;
    while (i < n && rec[i] >= '0' && rec[i] <= '9') pri = pri * 10 + (rec[i++] - '0');
    if (i == 0 || i >= n || rec[i] != ',') return 0;
    /* not the kernel's (a program's, facility 1 and up), or under warning */
    if (pri >> 3 || (pri & 7) > 4) return 0;
    ++i;
    while (i < n && rec[i] != ',') ++i; /* the sequence number */
    int usec = ++i, digits = 0;
    while (i < n && rec[i] >= '0' && rec[i] <= '9') ++i, ++digits;
    /* microseconds to ms by dropping three digits: no 64-bit division on the Q2 (no libgcc) */
    unsigned ms = 0;
    for (int k = usec; k < usec + digits - 3; ++k) ms = ms * 10 + (unsigned)(rec[k] - '0');
    while (i < n && rec[i] != ';') ++i;
    if (i >= n) return 0;
    int start = ++i;
    while (i < n && rec[i] != '\n') ++i; /* the continuation lines ( KEY=value) are left out */
    char level = (pri & 7) <= 3 ? 'E' : 'W';
    return add_line(l, wall, ms, level, "kernel", rec + start, i - start) ? level : 0;
}

/* ---- the card ---- */

int ulog_flush(card_log *l, const char *dir, const char *name, long max, int files) {
    char d[128], from[160], to[160];
    int n = (int)strlen(dir);
    if (n >= (int)sizeof d) return -1;
    memcpy(d, dir, n + 1);
    for (char *p = d + 1; *p; ++p) /* each parent, then dir */
        if (*p == '/') *p = 0, mkdir(d, 0755), *p = '/';
    mkdir(d, 0755);
    snprintf(from, sizeof from, "%s/%s.0", dir, name);
    int fd = open(from, O_RDONLY | O_CLOEXEC);
    long size = fd < 0 ? 0 : lseek(fd, 0, SEEK_END);
    if (fd >= 0) close(fd);
    if (size >= max) /* .2 to .3 (the old .3 goes), .1 to .2, .0 to .1 */
        for (int k = files - 1; k > 0; --k) {
            snprintf(from, sizeof from, "%s/%s.%d", dir, name, k - 1);
            snprintf(to, sizeof to, "%s/%s.%d", dir, name, k);
            rename(from, to);
        }
    return card_log_flush(l) ? 0 : -1;
}

/* ---- a snapshot ---- */

/* the value of "Key:" in meminfo, in kB, or -1 */
static long mem_kb(const char *m, const char *key) {
    const char *p = strstr(m, key);
    if (!p) return -1;
    p += strlen(key);
    while (*p == ' ') ++p;
    long v = 0;
    while (*p >= '0' && *p <= '9') v = v * 10 + (*p++ - '0');
    return v;
}

void ulog_snapshot(card_log *l, long wall, unsigned up_ms, char level, const char *why,
                   const char *meminfo, const char *loadavg, const char *state) {
    char b[ULOG_PART];
    snprintf(b, sizeof b, "snapshot %s", why);
    ulog_add(l, wall, up_ms, level, "guard", b);
    if (meminfo) {
        snprintf(b, sizeof b, "mem total=%ld free=%ld available=%ld cached=%ld kB",
                 mem_kb(meminfo, "MemTotal:"), mem_kb(meminfo, "MemFree:"),
                 mem_kb(meminfo, "MemAvailable:"), mem_kb(meminfo, "Cached:"));
        ulog_add(l, wall, up_ms, 'I', "guard", b);
    }
    if (loadavg) {
        int n = (int)strlen(loadavg);
        while (n && loadavg[n - 1] == '\n') --n;
        snprintf(b, sizeof b, "load %.*s", n, loadavg);
        ulog_add(l, wall, up_ms, 'I', "guard", b);
    }
    if (state) {
        /* the heat line from its uptime on: its time is the snapshot line's */
        const char *s = strstr(state, " up=");
        snprintf(b, sizeof b, "state%s", s ? s : state);
        ulog_add(l, wall, up_ms, 'I', "guard", b);
    }
}
