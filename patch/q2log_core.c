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

/* Who a line names, in this order: the player first, as "hciplayer" holds "hci", but bluealsa
 * before alsa, so its own lines are Bluetooth's. */
static const struct {
    const char *word, *src;
    int whole;
} names[] = {
    { "parse_bluealsa_status_msg", "player", 0 }, /* the player's: bluealsa's events as it reads them */
    { "q2player", "player", 0 },
    { "mplayer", "player", 0 },
    { "playback", "player", 0 },
    { "audio codec", "player", 0 },
    { "hciplayer", "player", 0 },
    { "bluealsa", "bt", 0 }, /* bluealsa's own lines ("bluealsa: E: ..."), before alsa's */
    { "alsa", "player", 0 },
    { "decoder", "player", 0 },
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
    int copied; /* the run's line went to the Bluetooth ring too: its count follows it */
} rep;

static unsigned fnv(const char *s, int n, unsigned h) {
    for (int i = 0; i < n; ++i) h = (h ^ (unsigned char)s[i]) * 16777619u;
    return h;
}

/* ---- the Bluetooth event log's copy (docs/internals.md#bluetooth-event-log) ----
 * Each line that goes into the unified log's ring and is about Bluetooth goes into q2guard's q2-btev
 * ring too, as it was written, so the Bluetooth lines outlast the unified log's churn. */
static card_log *bt_from, *bt_to;
void ulog_bt_to(card_log *from, card_log *to) { bt_from = from, bt_to = to; }

/* The ring's first bytes q2guard's writer holds (flush_start), while its drop count is still the
 * one they were handed with: no line goes in before them. */
static unsigned bt_frozen, bt_frozen_dropped;
void ulog_bt_freeze(unsigned bytes, unsigned dropped) { bt_frozen = bytes, bt_frozen_dropped = dropped; }

/* A ulog line's uptime, its second field */
static unsigned line_up(const char *s, unsigned n) {
    unsigned i = 0, up = 0;
    while (i < n && s[i] != ' ') ++i;
    for (++i; i < n && s[i] >= '0' && s[i] <= '9'; ++i) up = up * 10 + (unsigned)(s[i] - '0');
    return up;
}

#define BT_SORT_LINES 64 /* how far back a late line is carried: about a tick's worth and more */
/* line (len bytes, whole) appended to l, then carried back before the lines with a later uptime
 * (at most BT_SORT_LINES, never into the frozen bytes), so the ring is in uptime order. */
static void insert_sorted(card_log *l, const char *line, int len) {
    if (!card_log_append(l, line, (unsigned)len) || !l->ring || l->len < (unsigned)len) return;
    unsigned up = line_up(line, (unsigned)len), at = l->len - (unsigned)len;
    unsigned floor = bt_frozen && l->dropped == bt_frozen_dropped ? bt_frozen : 0;
    for (int k = 0; k < BT_SORT_LINES && at > floor; ++k) {
        unsigned q = at - 1; /* the newline ending the line before */
        while (q > floor && l->ring[q - 1] != '\n') --q;
        if (line_up(l->ring + q, at - q) <= up) break;
        at = q;
    }
    unsigned end = l->len - (unsigned)len;
    if (at == end) return;
    memmove(l->ring + at + len, l->ring + at, end - at);
    memcpy(l->ring + at, line, (unsigned)len);
}

unsigned ulog_settled(const card_log *l, unsigned before_ms) {
    unsigned at = 0;
    while (at < l->len) {
        unsigned e = at;
        while (e < l->len && l->ring[e] != '\n') ++e;
        if (e == l->len || line_up(l->ring + at, e - at) > before_ms) break;
        at = e + 1;
    }
    return at;
}

/* Words that make any source's line a Bluetooth one: bluealsa's own lines (an AVDTP start the
 * headset refused is its "Couldn't acquire transport"), the kernel's "Bluetooth: hci0: ..." */
static const struct {
    const char *word;
    int whole;
} bt_words[] = { { "bluealsa", 0 }, { "bluez", 0 },   { "bluetooth", 0 }, { "a2dp", 0 }, { "avdtp", 0 },
                 { "rtk_hci", 0 },  { "hci0", 1 },    { "l2cap", 0 } };

int ulog_bt_wanted(char level, const char *src, const char *s, int n) {
    if (!strcmp(src, "bt")) return 1;
    /* named but not Bluetooth's: the player's reading of bluealsa's events, and the kernel's note
     * at each stack start that hci0's debugfs files are there already */
    if (has(s, n, "parse_bluealsa_status_msg", 0) || (!strcmp(src, "kernel") && has(s, n, "debugfs:", 0)))
        return 0;
    /* where a run of the Q2 begins and ends, so a reboot between two lines shows */
    if (!strcmp(src, "guard") && ((n >= 5 && !memcmp(s, "boot ", 5)) || (n >= 9 && !memcmp(s, "snapshot ", 9))))
        return 1;
    if (n >= 9 && !memcmp(s, "shutdown:", 9)) return 1; /* demo's ulog_end */
    if (!strcmp(src, "player") && level == 'E') return 1; /* a song that would not open, with its path */
    if (!strcmp(src, "coda-rc") && n >= 4 && !memcmp(s, "ble:", 4)) return 1; /* its LE advertising and links */
    for (unsigned i = 0; i < sizeof bt_words / sizeof *bt_words; ++i)
        if (has(s, n, bt_words[i].word, bt_words[i].whole)) return 1;
    return 0;
}

/* line (len bytes, its newline included) into the Bluetooth ring as well when l is the unified ring
 * and copy says so: 1 yes, 0 no, -1 by ulog_bt_wanted. 1 when it went. */
static int bt_copy(card_log *l, const char *line, int len, int copy, char level, const char *src,
                   const char *s, int n) {
    if (!bt_to || l != bt_from) return 0;
    if (copy < 0) copy = ulog_bt_wanted(level, src, s, n);
    if (copy) insert_sorted(bt_to, line, len);
    return copy;
}

/* 1 when the line was copied to the Bluetooth ring too (bt_copy's copy) */
static int stamp(card_log *l, long wall, unsigned up_ms, char level, const char *src,
                 const char *s, int n, int copy) {
    char line[ULOG_PART + 64];
    int len = ulog_line(line, sizeof line, wall, up_ms, level, src, s, n);
    card_log_append(l, line, (unsigned)len);
    return bt_copy(l, line, len, copy, level, src, s, n);
}

void ulog_repeats(card_log *l) {
    if (rep.l != l || !rep.n) return;
    char b[64];
    int n = snprintf(b, sizeof b, "last line repeated %u more times", rep.n);
    stamp(l, rep.wall, rep.up_ms, rep.level, rep.src, b, n, rep.copied); /* where the line went */
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
    rep.copied = stamp(l, wall, up_ms, level, src, s, n, -1);
    return 1;
}

void ulog_add(card_log *l, long wall, unsigned up_ms, char level, const char *src,
              const char *msg) {
    add_line(l, wall, up_ms, level, src, msg, (int)strlen(msg));
}

/* The last stamped line read into each ring (ulog_add_raw), whose time an unstamped line after it
 * takes: it was written between that line and the read. */
static struct {
    card_log *l;
    long wall;
    unsigned up;
} last;
#define RAW_LAG_MS 60000 /* a stamped line older than this is no guide to the next unstamped one */

/* A ulog line's time, "YYYY-MM-DDTHH:MM:SSZ", as seconds since 1970 (days from the civil date,
 * Howard Hinnant's, as ulog_time writes it) */
static long line_wall(const char *s) {
    long v[6] = { 0 };
    static const int at[6] = { 0, 5, 8, 11, 14, 17 }, len[6] = { 4, 2, 2, 2, 2, 2 };
    for (int f = 0; f < 6; ++f)
        for (int i = 0; i < len[f]; ++i) v[f] = v[f] * 10 + (s[at[f] + i] - '0');
    long y = v[0] - (v[1] <= 2), era = (y >= 0 ? y : y - 399) / 400, yoe = y - era * 400;
    long doy = (153 * (v[1] > 2 ? v[1] - 3 : v[1] + 9) + 2) / 5 + v[2] - 1;
    long doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
    return (era * 146097 + doe - 719468) * 86400 + v[3] * 3600 + v[4] * 60 + v[5];
}

/* Unstamped lines known to come at every boot, said for what they are (docs/internals.md#bluetooth-name) */
static const char hci_boot[] = "Can't init device hci0: Function not implemented (89)";

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
        last.l = l, last.wall = line_wall(s), last.up = up;
        if (repeated(l, wall, up, level, src, s + m, n - m)) return 0;
        char line[ULOG_PART + 2];
        memcpy(line, s, n);
        line[n] = '\n';
        card_log_append(l, line, (unsigned)n + 1);
        rep.copied = bt_copy(l, line, n + 1, -1, level, src, s + m, n - m);
        return level;
    }
    if (last.l == l && last.up <= up_ms && up_ms - last.up <= RAW_LAG_MS) wall = last.wall, up_ms = last.up;
    const char *src;
    char level = ulog_guess(s + start, n - start, &src);
    char note[ULOG_PART];
    if (n - start == (int)sizeof hci_boot - 1 && !memcmp(s + start, hci_boot, sizeof hci_boot - 1)) {
        int k = snprintf(note, sizeof note, "%s: openBluetooth's hciconfig hci0 reset, expected at a boot's "
                         "Bluetooth on; the name is written after it", hci_boot);
        level = 'I', src = "bt", s = note, start = 0, n = k;
    }
    if (repeated(l, wall, up_ms, level, src, s + start, n - start)) return 0;
    rep.copied = stamp(l, wall, up_ms, level, src, s + start, n - start, -1);
    return level;
}

/* ---- a file another process appends to ---- */

void ulog_tail_at(struct ulog_tail *t, const char *path, int from_end) {
    t->path = path, t->off = 0, t->plen = 0, t->src = 0;
    if (!from_end) return;
    int fd = open(path, O_RDONLY | O_CLOEXEC);
    if (fd < 0) return;
    long size = lseek(fd, 0, SEEK_END);
    close(fd);
    t->off = size > 0 ? size : 0;
}

/* A card app's line: the date Go's log puts first ("2006/01/02 15:04:05 ", or "2006-01-02 ..."
 * as q2-subsonic's applog writes it, or "2006-01-02T15:04:05Z " as coda-rc's crc_log does) left
 * out, the level guessed from its words, the source the app's id. Its level, 0 for a blank line or
 * a repeat. */
static char app_line(card_log *l, long wall, unsigned up_ms, const char *src, const char *s,
                     int n) {
    while (n && (s[n - 1] == '\n' || s[n - 1] == '\r' || s[n - 1] == ' ')) --n;
    static const char date[] = "0000/00/00 00:00:00 ";
    int k = 0;
    while (k < (int)sizeof date - 1 && k < n &&
           (date[k] == '0'   ? s[k] >= '0' && s[k] <= '9'
            : date[k] == '/' ? s[k] == '/' || s[k] == '-'
            : k == 10        ? s[k] == ' ' || s[k] == 'T'
            : k == 19        ? s[k] == ' ' || s[k] == 'Z'
                             : s[k] == date[k]))
        ++k;
    if (k == (int)sizeof date - 1) s += k, n -= k;
    while (n && (*s == ' ' || *s == '\t')) ++s, --n;
    if (!n) return 0;
    const char *named;
    char level = ulog_guess(s, n, &named);
    return add_line(l, wall, up_ms, level, src, s, n) ? level : 0;
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
        char level = t->src ? app_line(l, wall, up_ms, t->src, t->part, t->plen)
                            : ulog_add_raw(l, wall, up_ms, t->part, t->plen);
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

/* ---- the card apps' ---- */

/* name is "q2-<id>.log", id letters, digits, '-' and '_' only: the id into id, 1 */
static int app_log_name(const char *name, char *id) {
    int n = (int)strlen(name);
    if (n < 8 || strncmp(name, "q2-", 3) || strcmp(name + n - 4, ".log") || n - 7 >= ULOG_APP_ID)
        return 0;
    for (int i = 3; i < n - 4; ++i) {
        char c = name[i];
        if (!((c >= 'a' && c <= 'z') || (c >= '0' && c <= '9') || c == '-' || c == '_')) return 0;
        id[i - 3] = c;
    }
    id[n - 7] = 0;
    return 1;
}

static int exists(const char *path) {
    int fd = open(path, O_RDONLY | O_CLOEXEC);
    if (fd < 0) return 0;
    close(fd);
    return 1;
}

int ulog_apps(struct ulog_apps *a, card_log *l, const char *dir, long wall, unsigned up_ms) {
    int got = 0, seen[ULOG_APPS] = { 0 };
    char id[ULOG_APP_ID], pid[ULOG_APP_PATH];
    void *d = opendir(dir);
    for (struct dirent *e; d && (e = readdir(d));) {
        if (!app_log_name(e->d_name, id)) continue;
        snprintf(pid, sizeof pid, "%s/q2-%s", dir, id);
        if (!exists(pid)) continue; /* no app runs by that id: the firmware's own (q2-demo.log) */
        int k = 0, free_k = -1;
        while (k < ULOG_APPS && (!a->id[k][0] || strcmp(a->id[k], id))) {
            if (!a->id[k][0] && free_k < 0) free_k = k;
            ++k;
        }
        if (k == ULOG_APPS) { /* new: a free slot, or none and it waits */
            if (free_k < 0) continue;
            k = free_k;
            snprintf(a->id[k], sizeof a->id[k], "%s", id);
            snprintf(a->path[k], sizeof a->path[k], "%s/q2-%s.log", dir, id);
            ulog_tail_at(&a->t[k], a->path[k], a->from_end);
            a->t[k].src = a->id[k];
        }
        seen[k] = 1;
    }
    if (d) closedir(d);
    a->from_end = 0; /* a log found from here on was begun since */
    for (int k = 0; k < ULOG_APPS; ++k) {
        if (!a->id[k][0]) continue;
        got |= ulog_tail(&a->t[k], l, wall, up_ms);
        if (!seen[k]) a->id[k][0] = 0; /* stopped: what it wrote last is in, and the slot is free */
    }
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
