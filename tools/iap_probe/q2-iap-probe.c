/* q2-iap-probe: route 1's experiment 1 (docs/iphone-mode.md). It owns the FunctionFS instances of the
 * q2iap gadget that q2-iap-probe.sh makes, writes each one's descriptors (an iPhone's configuration 1, 2,
 * 3 or 4 shape), and logs every event and control request the host sends, answering only what an iPhone
 * in media mode answers: the HID report descriptor, Apple's C0 53 (four zero bytes), GET_REPORT with
 * zeros; everything OUT is read and logged, every other IN request stalled. With echo on, a SET_REPORT
 * carrying iAP2's detect (FF 55 02 00 EE 10) is echoed on the HID interrupt endpoint, so the host's
 * next reports carry its link SYN. With --iap2 it speaks iAP2 instead (iap2.c, milestone M1): the detect
 * answered, the link set up, the car authenticated and identified, and the answers an iPhone gives next, every
 * packet and message logged.
 *
 *   q2-iap-probe [--echo] [--echo-from N] [--iap2] [--np FILE] [--uuid HEX] [--sessions N] [--for S]
 *                [--gap-ms MS] [--carplay] [--phase NAME] LOG DIR:KIND...
 *   q2-iap-probe --descs KIND     the descriptor and strings blobs for KIND on stdout (tests)
 *
 * KIND is ptp (configuration 1), hid (2), ptpmux (3) or ptpmuxeth (4). A session is the time from an
 * ENABLE to a DISABLE or SUSPEND not followed by another ENABLE or RESUME within --gap-ms (a cable out, not a
 * reset: in the car a cable out was a SUSPEND, the controller left suspended with no VBUS); with
 * --sessions N the probe exits 10 after the Nth ends, and with --for S, 0 after S seconds. Exit 1 is a
 * set-up failure (an ep0 that would not take its descriptors). Every ep0 stays open until the end:
 * closing one unbinds the gadget.
 *
 * Built for the Q2 by tools/iap_probe/build.py against the rootfs's libc with no headers (the
 * declarations and MIPS constants below), and for this machine with -DPROBE_HOST (test/iap_probe.py). */
#ifdef PROBE_HOST
#include <errno.h>
#include <fcntl.h>
#include <poll.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>
#define ERRNO errno
#else
typedef unsigned int size_t;
typedef int ssize_t;
struct timespec {
    long tv_sec, tv_nsec;
};
struct pollfd {
    int fd;
    short events, revents;
};
int open(const char *, int, ...);
int close(int);
ssize_t read(int, void *, size_t);
ssize_t write(int, const void *, size_t);
int poll(struct pollfd *, unsigned long, int);
int clock_gettime(int, struct timespec *);
long time(long *);
int snprintf(char *, size_t, const char *, ...);
int strcmp(const char *, const char *);
int strncmp(const char *, const char *, size_t);
size_t strlen(const char *);
char *strchr(const char *, int);
void *memcpy(void *, const void *, size_t);
void *memset(void *, int, size_t);
int memcmp(const void *, const void *, size_t);
int atoi(const char *);
int fork(void);
void _exit(int);
unsigned alarm(unsigned);
void (*signal(int, void (*)(int)))(int);
int fsync(int);
void *memmove(void *, const void *, size_t);
int pipe(int *);
int fcntl(int, int, ...);
int kill(int, int);
int *__errno_location(void);
#define ERRNO (*__errno_location())
#define O_RDONLY 0
#define O_WRONLY 1
#define O_RDWR 2
#define O_APPEND 0x0008 /* MIPS */
#define O_CREAT 0x0100  /* MIPS */
#define POLLIN 1
#define CLOCK_MONOTONIC 1
#define F_SETFL 4
#define O_NONBLOCK 0x0080 /* MIPS */
#define SIGHUP 1
#define SIGINT 2
#define SIGPIPE 13
#define SIGALRM 14
#define SIGTERM 15
#define SIGCHLD 18 /* MIPS */
#define SIG_IGN ((void (*)(int))1)
#define SIG_DFL ((void (*)(int))0)
#define EINTR 4
#endif

/* <linux/usb/functionfs.h> */
#define FFS_DESC_MAGIC_V2 3
#define FFS_STR_MAGIC 2
#define FFS_HAS_FS 1
#define FFS_HAS_HS 2
#define FFS_ALL_CTRL_RECIP 64
#define FFS_CONFIG0_SETUP 128
enum { EV_BIND, EV_UNBIND, EV_ENABLE, EV_DISABLE, EV_SETUP, EV_SUSPEND, EV_RESUME };
static const char *const ev_names[] = { "BIND", "UNBIND", "ENABLE", "DISABLE", "SETUP", "SUSPEND", "RESUME" };

#define MAX_INST 4
#define BLOB 512

/* The 208-byte HID report descriptor OrangeCar copied from a commercial dongle (its docs/protocol.md
 * section 1.3): vendor page 0xFF00, input reports 1 to 12 of 5 to 767 bytes, output reports 13 to 21
 * of 5 to 255 bytes. */
static const unsigned char hid_report[] = {
    0x06, 0x00, 0xff, 0x09, 0x01, 0xa1, 0x01, 0x75, 0x08, 0x26, 0x80, 0x00, 0x15, 0x00,
    0x09, 0x01, 0x85, 0x01, 0x95, 0x05, 0x82, 0x02, 0x01, 0x09, 0x01, 0x85, 0x02, 0x95, 0x09, 0x82, 0x02, 0x01,
    0x09, 0x01, 0x85, 0x03, 0x95, 0x0d, 0x82, 0x02, 0x01, 0x09, 0x01, 0x85, 0x04, 0x95, 0x11, 0x82, 0x02, 0x01,
    0x09, 0x01, 0x85, 0x05, 0x95, 0x19, 0x82, 0x02, 0x01, 0x09, 0x01, 0x85, 0x06, 0x95, 0x31, 0x82, 0x02, 0x01,
    0x09, 0x01, 0x85, 0x07, 0x95, 0x5f, 0x82, 0x02, 0x01, 0x09, 0x01, 0x85, 0x08, 0x95, 0xc1, 0x82, 0x02, 0x01,
    0x09, 0x01, 0x85, 0x09, 0x96, 0x01, 0x01, 0x82, 0x02, 0x01, 0x09, 0x01, 0x85, 0x0a, 0x96, 0x81, 0x01, 0x82,
    0x02, 0x01, 0x09, 0x01, 0x85, 0x0b, 0x96, 0x01, 0x02, 0x82, 0x02, 0x01, 0x09, 0x01, 0x85, 0x0c, 0x96, 0xff,
    0x02, 0x82, 0x02, 0x01, 0x09, 0x01, 0x85, 0x0d, 0x95, 0x05, 0x92, 0x02, 0x01, 0x09, 0x01, 0x85, 0x0e, 0x95,
    0x09, 0x92, 0x02, 0x01, 0x09, 0x01, 0x85, 0x0f, 0x95, 0x0d, 0x92, 0x02, 0x01, 0x09, 0x01, 0x85, 0x10, 0x95,
    0x11, 0x92, 0x02, 0x01, 0x09, 0x01, 0x85, 0x11, 0x95, 0x19, 0x92, 0x02, 0x01, 0x09, 0x01, 0x85, 0x12, 0x95,
    0x31, 0x92, 0x02, 0x01, 0x09, 0x01, 0x85, 0x13, 0x95, 0x5f, 0x92, 0x02, 0x01, 0x09, 0x01, 0x85, 0x14, 0x95,
    0xc1, 0x92, 0x02, 0x01, 0x09, 0x01, 0x85, 0x15, 0x95, 0xff, 0x92, 0x02, 0x01, 0xc0,
};
_Static_assert(sizeof hid_report == 208, "the report descriptor is 208 bytes");
/* The HID class descriptor: HID 1.11, one report descriptor of 208 bytes. */
static const unsigned char hid_class[] = { 0x09, 0x21, 0x11, 0x01, 0x00, 0x01, 0x22, sizeof hid_report, 0x00 };
/* Input report sizes after the id, ids 1 to 12 (the descriptor's Report Counts). */
static const unsigned short in_sizes[] = { 5, 9, 13, 17, 25, 49, 95, 193, 257, 385, 513, 767 };
static const unsigned char detect[] = { 0xff, 0x55, 0x02, 0x00, 0xee, 0x10 };

enum { K_PTP, K_HID, K_PTPMUX, K_PTPMUXETH, K_COUNT };
static const char *const kinds[] = { "ptp", "hid", "ptpmux", "ptpmuxeth" };
static const char *const kind_strings[] = { "PTP", "iAP Interface", "PTP", "PTP" };

struct blob {
    unsigned char b[BLOB];
    unsigned n;
};
static void put(struct blob *d, const unsigned char *p, unsigned n) {
    if (d->n + n <= BLOB) memcpy(d->b + d->n, p, n);
    d->n += n;
}
static void put32(struct blob *d, unsigned v) {
    unsigned char p[4] = { v, v >> 8, v >> 16, v >> 24 };
    put(d, p, 4);
}
static void intf(struct blob *d, int *count, int num, int alt, int eps, int cls, int sub, int proto, int str) {
    unsigned char p[9] = { 9, 4, num, alt, eps, cls, sub, proto, str };
    put(d, p, 9), ++*count;
}
/* An endpoint: bulk 64 bytes at full speed, 512 at high; interrupt 64 bytes, every 8 ms. */
static void ep(struct blob *d, int *count, int addr, int interrupt, int hs) {
    int size = interrupt ? 64 : hs ? 512 : 64;
    unsigned char p[7] = { 7, 5, addr, interrupt ? 3 : 2, size & 255, size >> 8, interrupt ? (hs ? 7 : 8) : 0 };
    put(d, p, 7), ++*count;
}
static void ptp(struct blob *d, int *c, int hs) {
    intf(d, c, 0, 0, 3, 0x06, 0x01, 0x01, 1);
    ep(d, c, 0x01, 0, hs), ep(d, c, 0x82, 0, hs), ep(d, c, 0x83, 1, hs);
}
/* One speed's descriptors for a kind; its count. */
static int speed_set(struct blob *d, int kind, int hs) {
    int c = 0;
    if (kind == K_HID) {
        intf(d, &c, 0, 0, 1, 0x03, 0x00, 0x00, 1);
        put(d, hid_class, sizeof hid_class), ++c;
        ep(d, &c, 0x81, 1, hs);
        return c;
    }
    ptp(d, &c, hs);
    if (kind >= K_PTPMUX) { /* Apple Mobile Device (usbmux) */
        intf(d, &c, 1, 0, 2, 0xff, 0xfe, 0x02, 0);
        ep(d, &c, 0x04, 0, hs), ep(d, &c, 0x85, 0, hs);
    }
    if (kind == K_PTPMUXETH) { /* Apple USB Ethernet: alternate 0 with no endpoints, 1 with two */
        intf(d, &c, 2, 0, 0, 0xff, 0xfd, 0x01, 0);
        intf(d, &c, 2, 1, 2, 0xff, 0xfd, 0x01, 0);
        ep(d, &c, 0x86, 0, hs), ep(d, &c, 0x07, 0, hs);
    }
    return c;
}
/* FunctionFS's v2 descriptor blob: magic, length, flags, the two counts, full speed then high speed. */
static void descs(struct blob *out, int kind) {
    struct blob fs = { { 0 }, 0 }, hs = { { 0 }, 0 };
    int nf = speed_set(&fs, kind, 0), nh = speed_set(&hs, kind, 1);
    out->n = 0;
    put32(out, FFS_DESC_MAGIC_V2);
    put32(out, 20 + fs.n + hs.n);
    put32(out, FFS_HAS_FS | FFS_HAS_HS | FFS_ALL_CTRL_RECIP | FFS_CONFIG0_SETUP);
    put32(out, nf), put32(out, nh);
    put(out, fs.b, fs.n), put(out, hs.b, hs.n);
}
/* The strings blob: one language (0x0409), one string. */
static void strings(struct blob *out, int kind) {
    const char *s = kind_strings[kind];
    unsigned n = strlen(s) + 1;
    out->n = 0;
    put32(out, FFS_STR_MAGIC), put32(out, 16 + 2 + n), put32(out, 1), put32(out, 1);
    unsigned char lang[2] = { 0x09, 0x04 };
    put(out, lang, 2), put(out, (const unsigned char *)s, n);
}

/* ---- the log ---- */

static int logfd = 2;
static struct timespec t0;
static long mono_ms(void) {
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return t.tv_sec * 1000L + t.tv_nsec / 1000000L;
}
/* One line, written whole: the shell's lines go to the same file (O_APPEND). Stamped with
 * CLOCK_MONOTONIC as seconds, dmesg's clock (equal to /proc/uptime with no suspend between). */
static void logline(const char *tag, const char *msg) {
    char line[3200];
    long ms = mono_ms();
    int n = snprintf(line, sizeof line, "%ld.%03ld %s %s\n", ms / 1000, ms % 1000, tag, msg);
    if (n > (int)sizeof line - 1) n = sizeof line - 1, line[n - 1] = '\n';
    if (n > 0) write(logfd, line, n);
}
static void hex(char *out, unsigned cap, const unsigned char *p, unsigned n) {
    static const char d[] = "0123456789abcdef";
    unsigned o = 0;
    for (unsigned i = 0; i < n && o + 4 < cap; ++i) {
        out[o++] = d[p[i] >> 4], out[o++] = d[p[i] & 15];
        if (i + 1 < n) out[o++] = ' ';
    }
    if (o + 4 >= cap && cap > 4) out[o++] = '.', out[o++] = '.';
    out[o] = 0;
}

/* ---- the instances ---- */

struct inst {
    const char *dir;
    int kind, ep0, config;
    char name[16];
};
static struct inst in[MAX_INST];
static int ninst;
static volatile int stopping;
static int echo_from = -1, sessions_max, session, active, carplay;
static long disabled_at = -1, gap_ms = 8000;

static void on_signal(int s) { stopping = s; }

/* ---- iAP2 (--iap2) ---- */

#include "iap2.c"

static int iap2_mode, writer_fd = -1, writer_pid;
static const char *np_file = "/tmp/coda-rc.now";
static char uuid[40] = "51320000-0000-4000-8000-000000000000", library_id[64];

/* The input reports go out through a child that writes them to the HID instance's ep1 one at a time: a write there
 * waits for the host to collect it, and the event loop must never wait (the car's control requests go unanswered
 * meanwhile). The pipe is non-blocking on this side, so a host that stops collecting costs reports, not the loop. */
static void writer_start(const char *dir) {
    int fds[2];
    char path[160];
    if (pipe(fds)) {
        logline("iap2", "pipe failed: no input reports");
        return;
    }
    snprintf(path, sizeof path, "%s/ep1", dir);
    writer_pid = fork();
    if (writer_pid == 0) {
        signal(SIGTERM, SIG_DFL), signal(SIGINT, SIG_DFL), signal(SIGHUP, SIG_DFL); /* the probe's only set a flag */
        close(fds[1]);
        int ep = open(path, O_RDWR), fails = 0;
        unsigned char b[2 + 128];
        for (;;) {
            if (read(fds[0], b, 1) != 1) _exit(0);
            int n = b[0], got = 0;
            while (got < n) {
                int r = (int)read(fds[0], b + 1 + got, n - got);
                if (r <= 0) _exit(0);
                got += r;
            }
            if (ep < 0 || write(ep, b + 1, n) != n) {
                char msg[96];
                if (fails++ < 20) {
                    snprintf(msg, sizeof msg, "input report %u (%d bytes) not sent, errno %d", b[1], n, ERRNO);
                    logline("iap2", msg);
                }
            } else {
                fails = 0;
            }
        }
    }
    close(fds[0]);
    writer_fd = fds[1];
    fcntl(writer_fd, F_SETFL, O_NONBLOCK);
}

static void io_send(const unsigned char *r, unsigned len) {
    unsigned char b[1 + 128];
    char h[300], msg[360];
    if (len > 128) return;
    b[0] = len;
    memcpy(b + 1, r, len);
    int w = writer_fd < 0 ? -1 : (int)write(writer_fd, b, len + 1);
    hex(h, sizeof h, r, len < 24 ? len : 24);
    snprintf(msg, sizeof msg, "report > %s%s", h, w == (int)len + 1 ? "" : " (dropped: the writer is behind)");
    logline("iap2", msg);
}
static void io_log(const char *m) { logline("iap2", m); }

/* The shim's Now Playing (patch/coda_rc_shim.c cr_now: key=value lines; pos in ms, len in seconds), written while
 * Car Remote is on. */
static int io_now_playing(struct iap2_np *n) {
    char b[1600];
    int fd = open(np_file, O_RDONLY), len = fd < 0 ? -1 : (int)read(fd, b, sizeof b - 1);
    if (fd >= 0) close(fd);
    if (len <= 0) return 0;
    b[len] = 0;
    for (char *line = b; line && *line;) {
        char *end = strchr(line, '\n'), *eq = strchr(line, '=');
        if (end) *end = 0;
        if (eq) {
            *eq = 0;
            const char *v = eq + 1;
            if (!strcmp(line, "state")) n->state = atoi(v);
            else if (!strcmp(line, "pos")) n->pos_ms = atoi(v);
            else if (!strcmp(line, "len")) n->len_ms = atoi(v) * 1000u;
            else if (!strcmp(line, "qpos")) n->qpos = atoi(v);
            else if (!strcmp(line, "qlen")) n->qlen = atoi(v);
            else if (!strcmp(line, "shuffle")) n->shuffle = atoi(v);
            else if (!strcmp(line, "repeat")) n->repeat = atoi(v);
            else if (!strcmp(line, "title")) snprintf(n->title, sizeof n->title, "%s", v);
            else if (!strcmp(line, "artist")) snprintf(n->artist, sizeof n->artist, "%s", v);
            else if (!strcmp(line, "album")) snprintf(n->album, sizeof n->album, "%s", v);
        }
        line = end ? end + 1 : 0;
    }
    return 1;
}

static const struct iap2_host io = { io_send, mono_ms, io_log, io_now_playing, "Q2 Muse", uuid, library_id };

/* The input report that echoes payload (link control 0, whole): the smallest that fits, zero padded,
 * written to the HID instance's ep1 in a child so the event loop never waits on the host's poll. */
static void echo_report(struct inst *i, const unsigned char *payload, unsigned n) {
    unsigned char r[1 + 767] = { 0 };
    unsigned id = 0;
    while (id < sizeof in_sizes / sizeof *in_sizes && in_sizes[id] < n + 1) ++id;
    if (id == sizeof in_sizes / sizeof *in_sizes) return;
    r[0] = id + 1, r[1] = 0;
    memcpy(r + 2, payload, n);
    unsigned len = 1 + in_sizes[id];
    char path[160], h[64], msg[256];
    snprintf(path, sizeof path, "%s/ep1", i->dir);
    hex(h, sizeof h, r, len < 16 ? len : 16);
    int pid = fork();
    if (pid == 0) {
        alarm(5); /* a host that never polls: the write is interrupted, the request dequeued */
        int fd = open(path, O_RDWR);
        int w = fd < 0 ? -1 : (int)write(fd, r, len);
        snprintf(msg, sizeof msg, "echo report %u (%u bytes: %s) written: %d%s", id + 1, len, h, w,
                 w < 0 ? " (failed or timed out)" : "");
        logline(i->name, msg);
        _exit(0);
    }
    snprintf(msg, sizeof msg, "detect seen: echoing it as input report %u%s", id + 1, pid < 0 ? " (fork failed)" : "");
    logline(i->name, msg);
}

static int echo_on(void) { return echo_from >= 0 && session >= echo_from; }

/* A control request: answered as an iPhone in media mode would, then logged (the host's timeout is
 * short, the card can be slow). */
static void setup(struct inst *i, const unsigned char *s) {
    unsigned rt = s[0], rq = s[1], wv = s[2] | s[3] << 8, wi = s[4] | s[5] << 8, wl = s[6] | s[7] << 8;
    unsigned char buf[4096], reply[1024];
    const unsigned char *ans = 0;
    unsigned an = 0;
    int r = 0, stalled = 0;
    char note[96] = "";
    char msg[3000], h[2600];
    long at = mono_ms();
    if (rt & 0x80) {
        if (rt == 0x81 && rq == 6 && wv >> 8 == 0x22 && i->kind == K_HID)
            ans = hid_report, an = sizeof hid_report, snprintf(note, sizeof note, "HID report descriptor");
        else if (rt == 0x81 && rq == 6 && wv >> 8 == 0x21 && i->kind == K_HID)
            ans = hid_class, an = sizeof hid_class, snprintf(note, sizeof note, "HID descriptor");
        else if (rt == 0xc0 && rq == 0x53) {
            memset(reply, 0, 4), reply[0] = carplay;
            ans = reply, an = 4, snprintf(note, sizeof note, "Apple C0 53: %s", carplay ? "01 (CarPlay)" : "00 (media mode)");
        } else if (rt == 0xa1 && rq == 1) {
            memset(reply, 0, sizeof reply);
            ans = reply, an = wl < sizeof reply ? wl : sizeof reply, snprintf(note, sizeof note, "GET_REPORT: zeros");
        }
        if (ans) {
            if (an > wl) an = wl;
            r = write(i->ep0, ans, an);
        } else {
            stalled = 1;
            r = read(i->ep0, buf, 0); /* the wrong direction: FunctionFS stalls ep0 */
        }
        snprintf(msg, sizeof msg, "SETUP %02x %02x %04x %04x %04x -> %s%s (%d, errno %d, %ld ms)%s%s", rt, rq, wv, wi,
                 wl, stalled ? "stalled" : "answered ", stalled ? "" : (snprintf(h, 16, "%u bytes", an), h), r,
                 r < 0 ? ERRNO : 0, mono_ms() - at, note[0] ? ": " : "", note);
        logline(i->name, msg);
        return;
    }
    unsigned want = wl < sizeof buf ? wl : sizeof buf;
    r = read(i->ep0, buf, want); /* the data stage, and the status stage acknowledged */
    int e = r < 0 ? ERRNO : 0;
    if (rt == 0x21 && rq == 0x09) snprintf(note, sizeof note, "SET_REPORT type %u id %u", wv >> 8, wv & 255);
    else if (rt == 0x21 && rq == 0x0a) snprintf(note, sizeof note, "SET_IDLE");
    else if (rt == 0x40 && rq == 0x40) snprintf(note, sizeof note, "Apple charging request");
    else if (rt == 0x40 && rq == 0x51) snprintf(note, sizeof note, "Apple role switch (wired CarPlay), value %u", wv);
    hex(h, sizeof h, buf, r > 0 ? (unsigned)r : 0);
    snprintf(msg, sizeof msg, "SETUP %02x %02x %04x %04x %04x -> read %d (errno %d, %ld ms)%s%s%s%s", rt, rq, wv, wi, wl,
             r, e, mono_ms() - at, note[0] ? ": " : "", note, r > 0 ? " data " : "", r > 0 ? h : "");
    logline(i->name, msg);
    if (iap2_mode && i->kind == K_HID && rt == 0x21 && rq == 0x09 && r > 0) {
        iap2_report(buf, (unsigned)r);
        return;
    }
    if (rt == 0x21 && rq == 0x09 && r >= 2 + (int)sizeof detect && !memcmp(buf + 2, detect, sizeof detect)) {
        if (echo_on()) echo_report(i, detect, sizeof detect);
        else logline(i->name, "detect seen: not echoed (echo off in this session)");
    }
}

static void event(struct inst *i, const unsigned char *e) {
    int type = e[8];
    char msg[160];
    if (type == EV_SETUP) {
        setup(i, e);
        return;
    }
    snprintf(msg, sizeof msg, "%s%s", type <= EV_RESUME ? ev_names[type] : "event?",
             type == EV_ENABLE ? " (the host chose this instance's configuration)" : "");
    logline(i->name, msg);
    if (type == EV_ENABLE) {
        disabled_at = -1;
        if (!active) {
            active = 1, ++session;
            if (iap2_mode) iap2_connect(&io);
            snprintf(msg, sizeof msg, "session %d begins on configuration %d, echo %s", session, i->config,
                     echo_on() ? "on" : "off");
            logline("probe", msg);
        }
    } else if ((type == EV_DISABLE || type == EV_SUSPEND) && active) {
        if (disabled_at < 0) disabled_at = mono_ms();
    } else if (type == EV_RESUME) {
        disabled_at = -1;
    }
}

static int kind_of(const char *s) {
    for (int k = 0; k < K_COUNT; ++k)
        if (!strcmp(s, kinds[k])) return k;
    return -1;
}

static int usage(void) {
    static const char u[] = "usage: q2-iap-probe [--echo] [--echo-from N] [--sessions N] [--for S] [--gap-ms MS] [--carplay] "
                            "[--phase NAME] LOG DIR:KIND...\n       q2-iap-probe --descs KIND\n";
    write(2, u, sizeof u - 1);
    return 2;
}

int main(int argc, char **argv) {
    int a = 1;
    long for_s = 0;
    const char *phase = "-";
    if (argc == 3 && !strcmp(argv[1], "--descs")) {
        int k = kind_of(argv[2]);
        if (k < 0) return usage();
        struct blob d, s;
        descs(&d, k), strings(&s, k);
        write(1, d.b, d.n), write(1, s.b, s.n);
        return 0;
    }
    for (; a < argc && argv[a][0] == '-' && argv[a][1] == '-'; ++a) {
        const char *o = argv[a];
        const char *v = a + 1 < argc ? argv[a + 1] : "0";
        if (!strcmp(o, "--echo")) echo_from = 0;
        else if (!strcmp(o, "--carplay")) carplay = 1;
        else if (!strcmp(o, "--echo-from")) echo_from = atoi(v), ++a;
        else if (!strcmp(o, "--sessions")) sessions_max = atoi(v), ++a;
        else if (!strcmp(o, "--for")) for_s = atoi(v), ++a;
        else if (!strcmp(o, "--gap-ms")) gap_ms = atoi(v), ++a;
        else if (!strcmp(o, "--phase")) phase = v, ++a;
        else if (!strcmp(o, "--iap2")) iap2_mode = 1;
        else if (!strcmp(o, "--np")) np_file = v, ++a;
        else if (!strcmp(o, "--uuid")) {
            /* the serial's first 32 hex digits as a UUID */
            if (strlen(v) >= 32)
                snprintf(uuid, sizeof uuid, "%.8s-%.4s-%.4s-%.4s-%.12s", v, v + 8, v + 12, v + 16, v + 20);
            ++a;
        }
        else return usage();
    }
    if (argc - a < 2 || argc - a - 1 > MAX_INST) return usage();
    logfd = open(argv[a++], O_WRONLY | O_CREAT | O_APPEND, 0644);
    if (logfd < 0) logfd = 2;
    clock_gettime(CLOCK_MONOTONIC, &t0);
    signal(SIGTERM, on_signal), signal(SIGINT, on_signal), signal(SIGHUP, on_signal);
    signal(SIGPIPE, SIG_IGN), signal(SIGCHLD, SIG_IGN); /* echo children reaped by the kernel */
    char msg[512];
    snprintf(msg, sizeof msg, "start: phase %s, wall clock %ld, echo %s, sessions %d, for %ld s, gap %ld ms%s", phase,
             time(0), echo_from < 0 ? "never" : echo_from == 0 ? "always" : "from session N", sessions_max, for_s, gap_ms,
             carplay ? ", C0 53 answers CarPlay" : "");
    logline("probe", msg);
    for (; a < argc; ++a, ++ninst) {
        struct inst *i = &in[ninst];
        char *colon = strchr(argv[a], ':'), path[160];
        if (!colon || (i->kind = kind_of(colon + 1)) < 0) return usage();
        *colon = 0;
        i->dir = argv[a], i->config = i->kind + 1;
        snprintf(i->name, sizeof i->name, "c%d.%s", i->config, kinds[i->kind]);
        snprintf(path, sizeof path, "%s/ep0", i->dir);
        struct blob d, s;
        descs(&d, i->kind), strings(&s, i->kind);
        i->ep0 = open(path, O_RDWR);
        int wd = i->ep0 < 0 ? -1 : (int)write(i->ep0, d.b, d.n);
        int ws = wd < 0 ? -1 : (int)write(i->ep0, s.b, s.n);
        snprintf(msg, sizeof msg, "%s: descriptors %d of %u bytes, strings %d of %u (errno %d)", path, wd, d.n, ws, s.n,
                 ws < 0 ? ERRNO : 0);
        logline(i->name, msg);
        if (ws < 0) return 1;
    }
    /* In an iPhone's form: a UUID, -MPN- for the device's own library, then a version (PodBox: an ID ending
     * otherwise may be what silenced its car). */
    snprintf(library_id, sizeof library_id, "%s-MPN-26.6.2", uuid);
    for (int k = 0; iap2_mode && k < ninst; ++k)
        if (in[k].kind == K_HID) writer_start(in[k].dir);
    if (iap2_mode) iap2_connect(&io);
    long end = for_s > 0 ? mono_ms() + for_s * 1000L : 0;
    int rc = 0;
    while (!stopping) {
        struct pollfd p[MAX_INST];
        for (int k = 0; k < ninst; ++k) p[k].fd = in[k].ep0, p[k].events = POLLIN, p[k].revents = 0;
        int n = poll(p, ninst, iap2_mode ? 10 : 500);
        if (n < 0 && ERRNO != EINTR) {
            snprintf(msg, sizeof msg, "poll failed, errno %d", ERRNO);
            logline("probe", msg), rc = 1;
            break;
        }
        for (int k = 0; n > 0 && k < ninst; ++k) {
            if (!(p[k].revents & POLLIN)) continue;
            unsigned char e[12];
            int r = read(in[k].ep0, e, sizeof e); /* one event at a time: a SETUP must be answered first */
            if (r == (int)sizeof e) event(&in[k], e);
        }
        if (iap2_mode) iap2_tick();
        long now = mono_ms();
        if (active && disabled_at >= 0 && now - disabled_at > gap_ms) {
            active = 0, disabled_at = -1;
            snprintf(msg, sizeof msg, "session %d ended (no ENABLE within %ld ms of the DISABLE)", session, gap_ms);
            logline("probe", msg);
            if (sessions_max && session >= sessions_max) {
                rc = 10;
                break;
            }
        }
        if (end && now >= end) {
            logline("probe", "time is up");
            break;
        }
    }
    snprintf(msg, sizeof msg, "end: %s, %d session%s, exit %d", stopping ? "signalled" : "by itself", session,
             session == 1 ? "" : "s", rc);
    logline("probe", msg);
    if (writer_pid > 0) kill(writer_pid, SIGTERM);
    if (logfd != 2) fsync(logfd);
    for (int k = 0; k < ninst; ++k) close(in[k].ep0);
    return rc;
}
