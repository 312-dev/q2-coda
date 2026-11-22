/* q2-iap (/usr/bin/q2-iap): the iPhone gadget's daemon (docs/iphone-mode.md). It owns the FunctionFS instances of
 * the iPhone-shaped gadget that patch/q2car.sh makes for Car Audio (and tools/iap_probe/q2-iap-probe.sh for the
 * experiments), writes each one's descriptors (an iPhone's configuration 1, 2, 3 or 4 shape), and logs every event
 * and control request the host sends, answering only what an iPhone in media mode answers: the HID report
 * descriptor, Apple's C0 53 (four zero bytes), GET_REPORT with zeros; everything OUT is read and logged, every other
 * IN request stalled. With echo on, a SET_REPORT carrying iAP2's detect (FF 55 02 00 EE 10) is echoed on the HID
 * interrupt endpoint, so the host's next reports carry its link SYN. With --iap2 it speaks iAP2 instead (q2_iap2.c):
 * the detect answered, the link set up, the car authenticated and identified, and the answers an iPhone gives next,
 * every packet and message logged. With --state (Car Audio) it decides what the host is and watches the car's
 * audio (below, "Car Audio in the image").
 *
 *   q2-iap [--echo] [--echo-from N] [--iap2] [--np FILE] [--uuid HEX] [--car-pcm DEV] [--car-file F]
 *          [--cmd-file F] [--rate-file F] [--art-dir D] [--sessions N] [--for S] [--gap-ms MS] [--carplay]
 *          [--phase NAME] [--state F] [--udc F] [--counters DIR] [--under F] [--auto-ms MS] [--car-ms MS]
 *          [--lib-db F] [--lib-cat F] [--lib-sent F] [--list-file F] [--queue-file F]
 *          LOG DIR:KIND...
 *   q2-iap --descs KIND     the descriptor and strings blobs for KIND on stdout (tests)
 *
 * KIND is ptp (configuration 1), hid (2), ptpmux (3) or ptpmuxeth (4). A session is the time from an
 * ENABLE to a DISABLE or SUSPEND not followed by another ENABLE or RESUME within --gap-ms (a cable out, not a
 * reset: in the car a cable out was a SUSPEND, the controller left suspended with no VBUS); with
 * --sessions N it exits 10 after the Nth ends, and with --for S, 0 after S seconds. Exit 1 is a
 * set-up failure (an ep0 that would not take its descriptors); 20 and 21 are --state's computer and silent host.
 * Every ep0 stays open until the end: closing one unbinds the gadget.
 *
 * Built for the Q2 by tools/build.py's compile_helper (and tools/iap_probe/probe_build.py for the experiments)
 * against the rootfs's libc with no headers (the declarations and MIPS constants below), and for this machine with
 * -DPROBE_HOST (test/iap_probe.py). */
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
int nanosleep(const struct timespec *, struct timespec *);
int siginterrupt(int, int);
void _exit(int);
unsigned alarm(unsigned);
void (*signal(int, void (*)(int)))(int);
int fsync(int);
void *memmove(void *, const void *, size_t);
int pipe(int *);
int fcntl(int, int, ...);
int kill(int, int);
int rename(const char *, const char *);
int unlink(const char *);
char *strrchr(const char *, int);
int *__errno_location(void);
void *malloc(size_t);
void *realloc(void *, size_t);
void free(void *);
unsigned long strtoul(const char *, char **, int);
int access(const char *, int);
#define ERRNO (*__errno_location())
#define O_RDONLY 0
#define O_WRONLY 1
#define O_RDWR 2
#define O_APPEND 0x0008 /* MIPS */
#define O_CREAT 0x0100  /* MIPS */
#define O_TRUNC 0x0200  /* MIPS */
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
static void on_alarm(int s) { (void)s; } /* only to end a read that waits too long (setup's data stage) */
#define DATA_STAGE_S 2
/* SIGALRM ends a read rather than restarting it (signal's BSD default would restart it) */
static void alarm_interrupts(void) {
#ifdef PROBE_HOST
    struct sigaction sa;
    memset(&sa, 0, sizeof sa);
    sa.sa_handler = on_alarm;
    sigaction(SIGALRM, &sa, 0);
#else
    signal(SIGALRM, on_alarm), siginterrupt(SIGALRM, 1);
#endif
}

/* ---- iAP2 (--iap2) ---- */

#include "q2_iap2.c"

static int iap2_mode, writer_fd = -1, writer_pid, done_fd = -1, report_ms;
static unsigned wr_queued, wr_done; /* input reports handed to the writer, and those it has written to ep1 */
static const char *np_file = "/tmp/coda-rc.now";
/* What the player and the shim read (player/output.c output_car, patch/coda_rc_shim.c cr_command), and Cover Flow's
 * cache of covers (patch/coverflow.c art_path); each can be moved for the host tests. */
static const char *car_file = "/tmp/q2-iap.audio", *car_pcm = "null", *cmd_file = "/tmp/coda-rc.cmd",
                  *art_dir = "/mnt/mmc/.coverflow", *rate_file = "/tmp/q2-iap.rate";
static char uuid[40] = "51320000-0000-4000-8000-000000000000", library_id[64];

/* The input reports go out through a child that writes them to the HID instance's ep1 one at a time: a write there
 * waits for the host to collect it, and the event loop must never wait (the car's control requests go unanswered
 * meanwhile). The pipe is non-blocking on this side, so a host that stops collecting costs reports, not the loop. */
static void writer_start(const char *dir) {
    int fds[2], dn[2];
    char path[160];
    if (pipe(dn)) dn[0] = dn[1] = -1;
    if (pipe(fds)) {
        logline("iap2", "pipe failed: no input reports");
        return;
    }
    snprintf(path, sizeof path, "%s/ep1", dir);
    writer_pid = fork();
    if (writer_pid == 0) {
        signal(SIGTERM, SIG_DFL), signal(SIGINT, SIG_DFL), signal(SIGHUP, SIG_DFL); /* the probe's only set a flag */
        close(fds[1]);
        if (dn[0] >= 0) close(dn[0]);
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
            /* the bus's own pace, for the host tests (--report-ms): a write to the real ep1 returns once the car has
             * taken the report, one an interrupt interval */
            if (report_ms) {
                struct timespec ts = { report_ms / 1000, (report_ms % 1000) * 1000000L };
                nanosleep(&ts, 0);
            }
            if (dn[1] >= 0) write(dn[1], "", 1); /* one more report gone (io_backlog) */
        }
    }
    close(fds[0]);
    if (dn[1] >= 0) close(dn[1]);
    done_fd = dn[0];
    if (done_fd >= 0) fcntl(done_fd, F_SETFL, O_NONBLOCK);
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
    if (w == (int)len + 1) ++wr_queued;
    hex(h, sizeof h, r, len < 24 ? len : 24);
    snprintf(msg, sizeof msg, "report > %s%s", h, w == (int)len + 1 ? "" : " (dropped: the writer is behind)");
    logline("iap2", msg);
}
static void io_log(const char *m) { logline("iap2", m); }

/* The input reports handed to the writer and not yet taken by the car: at the BMW's pace (an interrupt endpoint of 64
 * bytes every 8 ms, a 95-byte report two of them) a 1024-byte packet is about 170 ms on the wire. */
static int io_backlog(void) {
    char b[256];
    int n;
    while (done_fd >= 0 && (n = (int)read(done_fd, b, sizeof b)) > 0) wr_done += (unsigned)n;
    return done_fd < 0 ? 0 : (int)(wr_queued - wr_done);
}

/* The shim's Now Playing (patch/coda_rc_shim.c cr_now, from now_source.c: key=value lines; pos in ms, len in
 * seconds, live=1 for a radio's stream), written while Car Remote is on or the car's iPhone is on the cable. */
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
            else if (!strcmp(line, "live")) n->live = atoi(v);
            else if (!strcmp(line, "len")) n->len_ms = atoi(v) * 1000u;
            else if (!strcmp(line, "qpos")) n->qpos = atoi(v);
            else if (!strcmp(line, "qlen")) n->qlen = atoi(v);
            else if (!strcmp(line, "id")) n->id = (unsigned)atoi(v);
            else if (!strcmp(line, "qgen")) n->qgen = (unsigned)atoi(v);
            else if (!strcmp(line, "shuffle")) n->shuffle = atoi(v);
            else if (!strcmp(line, "repeat")) n->repeat = atoi(v);
            else if (!strcmp(line, "title")) snprintf(n->title, sizeof n->title, "%s", v);
            else if (!strcmp(line, "artist")) snprintf(n->artist, sizeof n->artist, "%s", v);
            else if (!strcmp(line, "album")) snprintf(n->album, sizeof n->album, "%s", v);
            else if (!strcmp(line, "path")) snprintf(n->path, sizeof n->path, "%s", v);
        }
        line = end ? end + 1 : 0;
    }
    return 1;
}

/* A file written whole, through a temporary name, mode 0600 (the shim takes only root's own, private files). */
static int write_whole(const char *path, const char *b, unsigned n) {
    char tmp[200];
    snprintf(tmp, sizeof tmp, "%s.iap", path);
    int fd = open(tmp, O_WRONLY | O_CREAT | O_TRUNC, 0600);
    if (fd < 0) return -1;
    int ok = write(fd, b, n) == (int)n;
    close(fd);
    if (!ok || rename(tmp, path)) return unlink(tmp), -1;
    return 0;
}

/* The car wants USB audio: the player's car way (player/output.c output_car) takes over while the file is there. Its
 * second line is the rate the car was told (send_audio_rate). */
static void io_audio(int on) {
    char b[160];
    if (!on) {
        unlink(car_file);
        return;
    }
    int n = snprintf(b, sizeof b, "%s\n%u\n", car_pcm, iap2_rates[iap2_rate_index(audio_hz)]);
    if (write_whole(car_file, b, (unsigned)n)) logline("iap2", "audio: the car's file could not be written");
}

/* The player's rate for the song about to play (player/output.c car_ask, docs/iphone-mode.md#per-song-rate), read
 * every RATE_POLL_MS and handed to q2_iap2.c, which tells the car when it changes; none written, 44.1 kHz. */
#define RATE_POLL_MS 20
static void rate_poll(long now) {
    static long at;
    char b[16];
    if (at && now - at < RATE_POLL_MS) return;
    at = now;
    int fd = open(rate_file, O_RDONLY), n = fd < 0 ? -1 : (int)read(fd, b, sizeof b - 1);
    if (fd >= 0) close(fd);
    b[n > 0 ? n : 0] = 0;
    iap2_audio_rate(atoi(b) > 0 ? (unsigned)atoi(b) : 44100);
}

/* A button or a seek, as the Remote app's commands reach the shim (Car Remote on). Its numbers start high, so an ack
 * of one is never taken for coda-rc's own. */
static void io_command(const char *verb) {
    static unsigned seq = 0x51000000u;
    char b[96];
    int n = snprintf(b, sizeof b, "%u %s\n", ++seq, verb);
    if (write_whole(cmd_file, b, (unsigned)n)) logline("iap2", "command: the shim's file could not be written");
}

static unsigned read_file(const char *path, unsigned char *out, unsigned cap) {
    int fd = open(path, O_RDONLY), got = 0, r;
    if (fd < 0) return 0;
    while (got < (int)cap && (r = (int)read(fd, out + got, cap - got)) > 0) got += r;
    close(fd);
    return got < (int)cap ? (unsigned)got : 0; /* too large: none */
}

/* Cover Flow's key (patch/coverflow.c tags_key, coda_rc_lib.c fnv32): FNV-1a with a separator after each part */
static unsigned fnv_part(unsigned h, const char *s) {
    while (*s) h = (h ^ (unsigned char)*s++) * 16777619u;
    return h * 16777619u;
}

/* The song's cover: Cover Flow's 160 px copy (keyed by artist and album), else a cover beside the song. */
static unsigned io_artwork(const struct iap2_np *n, unsigned char *out, unsigned cap) {
    char p[600];
    unsigned got = 0;
    if (n->album[0]) {
        snprintf(p, sizeof p, "%s/%08x.jpg", art_dir, fnv_part(fnv_part(2166136261u, n->artist), n->album));
        got = read_file(p, out, cap);
    }
    static const char *const names[] = { "cover.jpg", "folder.jpg", "front.jpg" };
    const char *slash = strrchr(n->path, '/');
    for (unsigned i = 0; !got && slash && i < 3; ++i) {
        snprintf(p, sizeof p, "%.*s/%s", (int)(slash - n->path), n->path, names[i]);
        got = read_file(p, out, cap);
    }
    return got;
}

/* ---- Car Audio in the image (--state; docs/iphone-mode.md#car-audio-in-the-image) ----
 *
 * What the host is, decided from what it asks the iPhone it was shown, and how the car's audio goes, written to the
 * state file demo reads (patch/usb_ui.c). A car or dock: Apple's C0 53, or iAP2's detect in a SET_REPORT; it stays.
 * A computer: configuration 1, 3 or 4 chosen, usbmux's C0 45, or the role switch 40 51 (wired CarPlay, which the Q2
 * cannot be); q2-iap ends at once (exit 20) and q2car.sh removes the gadget. A host that has addressed the Q2 and
 * asked nothing Apple's within auto_ms (3 s; car_ms, 30 s, once it chose configuration 2, which only an iPod
 * accessory does): q2-iap ends (exit 21), the host taken for a computer. A host that never addresses it (a charger)
 * changes nothing. A computer seen after C0 53 still wins; after the detect it does not. Apple's charging request
 * 40 40 decides nothing. */
static const char *state_file, *udc_file, *counters_dir, *under_file = "/tmp/q2-iap.under";
static long auto_ms = 3000, car_ms = 30000;
enum { V_WAIT, V_CAR, V_COMPUTER, V_SILENT };
static const char *const verdicts[] = { "wait", "car", "computer", "silent" };
static int verdict, apple, config2, leave; /* apple: 1 C0 53, 2 the detect; leave: main's exit code once decided */
static long started_at, host_at = -1, verdict_ms = -1, udc_at;
static char why[64];
/* the car's audio: the module's playback counts (in_sent, in_missed, since it loaded) and the player's underruns
 * on way 3 (player/cartrace.c car_trace_underrun), each read every HEALTH_MS while the car has asked for audio */
#define HEALTH_MS 1000
#define DROPOUT_PERMILLE 10  /* packets missed in a second above this, per thousand: dropouts */
#define NOSTREAM_MS 5000     /* the car asked for audio and took no packet in this long: no sound */
#define CLEAN_MS 10000       /* a problem is over after this long without one */
static unsigned sent0, missed0, under0, sent_last, missed_last, under_last;
static long audio_at = -1, audio_at0 = -1, flowing_at = -1, health_at, trouble_at;
static int counted, flowing, troubles;
static const char *trouble = "";

static int read_uint(const char *path, unsigned *v) {
    char b[24];
    int fd = open(path, O_RDONLY), n = fd < 0 ? -1 : (int)read(fd, b, sizeof b - 1);
    if (fd >= 0) close(fd);
    if (n <= 0) return 0;
    b[n] = 0;
    *v = (unsigned)atoi(b);
    return 1;
}
static void counters(unsigned *sent, unsigned *missed, unsigned *under) {
    char p[160];
    *sent = *missed = *under = 0;
    if (counters_dir) {
        snprintf(p, sizeof p, "%s/in_sent", counters_dir), read_uint(p, sent);
        snprintf(p, sizeof p, "%s/in_missed", counters_dir), read_uint(p, missed);
    }
    read_uint(under_file, under);
}

/* The car's stream, a line a second while the car has asked for audio (docs/iphone-mode.md#the-car-s-stream-counted),
 * from the module's counters (uac1_build.py COUNTED): what each second's completions were, by status, the frames the
 * host took, the packet sizes and the longest gap between completions (windows this restarts by writing them), the
 * gaps over 2 ms and the host's AS IN alternate settings. Kept for the cable's summary: the longest gap, the toggles
 * and the rate changes. Counting only: nothing here changes what is sent. */
enum { C_SENT, C_MISSED, C_ENODATA, C_EXDEV, C_ESHUTDOWN, C_OTHER, C_RESENT, C_BYTES, C_ZERO, C_GAPS2, C_ALT_ON, C_ALT_OFF,
       C_RATE_SETS, C_PKT_MIN, C_PKT_MAX, C_GAP_MAX, C_N };
static const char *const c_names[C_N] = { "in_sent",  "in_missed",  "in_enodata", "in_exdev", "in_eshutdown",
                                          "in_other", "in_resent",  "in_bytes",   "in_zero",  "in_gaps2",
                                          "in_alt_on", "in_alt_off", "rate_sets", "in_pkt_min", "in_pkt_max",
                                          "in_gap_max" };
static unsigned c_last[C_N], c_first[C_N], gap_longest; /* gap_longest in 1024 ns units */
static int c_have;
static void c_write(int k, unsigned v) {
    char p[160], b[16];
    snprintf(p, sizeof p, "%s/%s", counters_dir, c_names[k]);
    int fd = open(p, O_WRONLY | O_TRUNC), n = snprintf(b, sizeof b, "%u\n", v);
    if (fd >= 0) write(fd, b, n), close(fd);
}
static void stream_line(long ms) {
    unsigned c[C_N], d[C_N];
    char p[160], msg[400];
    if (!counters_dir) return;
    for (int k = 0; k < C_N; ++k) {
        c[k] = 0;
        snprintf(p, sizeof p, "%s/%s", counters_dir, c_names[k]);
        read_uint(p, &c[k]);
        d[k] = c[k] - c_last[k];
    }
    if (!c_have) memcpy(c_first, c, sizeof c), c_have = 1, d[C_SENT] = d[C_MISSED] = 0;
    if (c[C_GAP_MAX] > gap_longest) gap_longest = c[C_GAP_MAX];
    snprintf(msg, sizeof msg,
             "usb %ldms: sent=%u missed=%u (enodata %u exdev %u shutdown %u other %u) resent=%u zero=%u frames=%u pkt=%u/%u"
             " gapmax=%u.%ums gaps2=%u alt=+%u/-%u rate_sets=%u",
             ms, d[C_SENT], d[C_MISSED], d[C_ENODATA], d[C_EXDEV], d[C_ESHUTDOWN], d[C_OTHER], d[C_RESENT], d[C_ZERO],
             d[C_BYTES] / 4, c[C_PKT_MIN] == ~0u ? 0 : c[C_PKT_MIN], c[C_PKT_MAX], c[C_GAP_MAX] * 1024u / 1000000u,
             c[C_GAP_MAX] * 1024u / 100000u % 10, d[C_GAPS2], d[C_ALT_ON], d[C_ALT_OFF], d[C_RATE_SETS]);
    logline("car", msg);
    memcpy(c_last, c, sizeof c);
    c_write(C_PKT_MIN, ~0u), c_write(C_PKT_MAX, 0), c_write(C_GAP_MAX, 0); /* the next second's windows */
}

/* The state file, written whole at each change. */
static char state_name[sizeof car_name]; /* the car's name as the state file has it */
static void state_write(void) {
    char b[400];
    if (!state_file) return;
    snprintf(state_name, sizeof state_name, "%s", car_name);
    int n = snprintf(b, sizeof b,
                     "verdict=%s\nwhy=%s\nms=%ld\nhost=%d\nname=%s\naudio=%d\nflowing=%d\ntrouble=%s\ntroubles=%d\n",
                     verdicts[verdict], why, verdict_ms, host_at >= 0, car_name, audio_on, flowing, trouble, troubles);
    if (n > 0 && write_whole(state_file, b, (unsigned)n)) logline("car", "the state file could not be written");
}

static void decide(int v, const char *what) {
    char msg[160];
    long now = mono_ms();
    verdict = v, verdict_ms = now - (host_at >= 0 ? host_at : started_at);
    snprintf(why, sizeof why, "%s", what);
    snprintf(msg, sizeof msg, "host: %s (%s), %ld ms after it %s", verdicts[v], what, verdict_ms,
             host_at >= 0 ? "addressed the Q2" : "was bound");
    logline("car", msg);
    if (v == V_COMPUTER || v == V_SILENT) leave = v == V_COMPUTER ? 20 : 21;
    state_write();
}
static void apple_seen(int strength, const char *what) {
    if (!state_file || strength <= apple || leave) return;
    apple = strength;
    if (verdict != V_CAR || strength == 2) decide(V_CAR, what);
}
static void computer_seen(const char *what) {
    if (!state_file || apple >= 2 || leave) return;
    decide(V_COMPUTER, what);
}

/* The controller's state every 100 ms until the host has addressed the Q2; the time since then against the window. */
static void host_poll(long now) {
    if (!state_file || leave) return;
    if (host_at < 0 && udc_file && now - udc_at >= 100) {
        char s[24] = "";
        udc_at = now;
        int fd = open(udc_file, O_RDONLY), n = fd < 0 ? -1 : (int)read(fd, s, sizeof s - 1);
        if (fd >= 0) close(fd);
        if (n > 0 && (!strncmp(s, "addressed", 9) || !strncmp(s, "configured", 10))) {
            host_at = now;
            logline("car", "host: it addressed the Q2");
            state_write();
        }
    }
    if (verdict == V_WAIT && host_at >= 0 && now - host_at > (config2 ? car_ms : auto_ms)) {
        char what[64];
        snprintf(what, sizeof what, "nothing Apple's in %ld ms%s", config2 ? car_ms : auto_ms,
                 config2 ? " on configuration 2" : "");
        decide(V_SILENT, what);
    }
}

/* Every HEALTH_MS while the car has asked for audio: is it flowing, and cleanly. */
static void health_poll(long now) {
    unsigned sent, missed, under;
    char msg[200];
    if (!state_file || now - health_at < HEALTH_MS) return;
    health_at = now;
    counters(&sent, &missed, &under);
    if (!counted) counted = 1, sent0 = sent_last = sent, missed0 = missed_last = missed, under0 = under_last = under;
    unsigned ds = sent - sent_last, dm = missed - missed_last, du = under - under_last;
    sent_last = sent, missed_last = missed, under_last = under;
    int changed = strcmp(state_name, car_name) != 0; /* identified since the last write */
    if (audio_on) stream_line(HEALTH_MS);
    if (!audio_on) {
        changed |= flowing || *trouble;
        audio_at = -1, flowing = 0, trouble = "";
        if (changed) state_write();
        return;
    }
    if (audio_at < 0) audio_at = now, changed = 1;
    if (audio_at0 < 0) audio_at0 = now; /* the first ask, for the summary's start latency */
    if (ds && !flowing) {
        flowing = 1, changed = 1;
        if (flowing_at < 0) flowing_at = now;
        snprintf(msg, sizeof msg, "audio: the car takes it, %ld ms after it asked", now - audio_at);
        logline("car", msg);
    }
    const char *t = (ds + dm) && dm * 1000 > (ds + dm) * DROPOUT_PERMILLE ? "dropouts"
                    : du                                                  ? "underruns"
                    : !flowing && now - audio_at > NOSTREAM_MS            ? "nostream"
                                                                          : "";
    if (*t) {
        if (strcmp(t, trouble)) {
            ++troubles, changed = 1;
            snprintf(msg, sizeof msg, "audio problem: %s (in a second: %u sent, %u missed, %u underruns)", t, ds, dm, du);
            logline("car", msg);
        }
        trouble = t, trouble_at = now;
    } else if (*trouble && now - trouble_at > CLEAN_MS) {
        trouble = "", changed = 1;
        logline("car", "audio: clean again");
    }
    if (changed) state_write();
}

/* One line that judges the cable's session: what the host was, the car, the handshake, the audio and its counts. */
static void summary(void) {
    char msg[600];
    unsigned sent, missed, under;
    if (!state_file) return;
    counters(&sent, &missed, &under);
    if (!counted) sent0 = sent, missed0 = missed, under0 = under;
    unsigned ds = sent - sent0, dm = missed - missed0, rate[3] = { 0, 0, 0 };
    static const char *const rates[] = { "p_rate", "rate_sets", "rate_asked" }; /* the car's rate, as the module has it */
    for (int k = 0; counters_dir && k < 3; ++k) {
        char path[160];
        snprintf(path, sizeof path, "%s/%s", counters_dir, rates[k]);
        read_uint(path, &rate[k]);
    }
    snprintf(msg, sizeof msg,
             "summary: host %s (%s, %ld ms), car \"%s\", identified %s, audio asked %s, flowing %s, in_sent %u in_missed %u"
             " (%u.%u%%), underruns %u, problems %d, p_rate %u rate_sets %u rate_asked %u, start %ld ms, longest gap"
             " %u.%u ms, alt toggles %u, rate changes %u",
             verdicts[verdict], why[0] ? why : "-", verdict_ms, car_name, ever_identified ? "yes" : "no",
             audio_at >= 0 || flowing_at >= 0 ? "yes" : "no",
             flowing_at >= 0 ? "yes" : "no", ds, dm, ds + dm ? dm * 100 / (ds + dm) : 0,
             ds + dm ? dm * 1000 / (ds + dm) % 10 : 0, under - under0, troubles, rate[0], rate[1], rate[2],
             flowing_at >= 0 && audio_at0 >= 0 ? flowing_at - audio_at0 : -1L, gap_longest * 1024u / 1000000u,
             gap_longest * 1024u / 100000u % 10, c_last[C_ALT_ON] - c_first[C_ALT_ON] + c_last[C_ALT_OFF] - c_first[C_ALT_OFF],
             c_last[C_RATE_SETS] - c_first[C_RATE_SETS]);
    logline("car", msg);
}

#include "q2_iap_lib.c"

/* The link lost (the car stopped acknowledging through every retry): with --state, q2-iap ends (exit 22) and q2car.sh
 * shows the car the iPhone again (the gadget off the controller and back), which is how a car starts a session
 * afresh; it sent nothing more by itself after the loss on 2026-11-21 (126 s, to the cable out). */
static void io_link_lost(void) {
    if (state_file && !leave) leave = 22, logline("car", "link: lost; the iPhone shown again");
}

static const struct iap2_host io = { io_send,   mono_ms,    io_log,  io_now_playing, "Q2 Muse", uuid,     library_id,
                                     io_audio,  io_command, io_artwork, &lib_t,      lib_load,  lib_saved, lib_play,
                                     lib_queue, io_backlog, io_link_lost };

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
    if (rt == 0xc0 && rq == 0x53) apple_seen(1, "Apple's C0 53");
    else if (rt == 0xc0 && rq == 0x45) computer_seen("usbmux's C0 45");
    else if (rt == 0x40 && rq == 0x51) computer_seen("the role switch 40 51");
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
    /* the data stage, and the status stage acknowledged; at most DATA_STAGE_S, or the loop stops with it: on
     * 2026-11-21 a SET_REPORT's data never came, and this read held everything else for 35 s (the car dropped the link) */
    alarm(DATA_STAGE_S);
    r = read(i->ep0, buf, want);
    alarm(0);
    int e = r < 0 ? ERRNO : 0;
    if (rt == 0x21 && rq == 0x09) snprintf(note, sizeof note, "SET_REPORT type %u id %u", wv >> 8, wv & 255);
    else if (rt == 0x21 && rq == 0x0a) snprintf(note, sizeof note, "SET_IDLE");
    else if (rt == 0x40 && rq == 0x40) snprintf(note, sizeof note, "Apple charging request");
    else if (rt == 0x40 && rq == 0x51) snprintf(note, sizeof note, "Apple role switch (wired CarPlay), value %u", wv);
    hex(h, sizeof h, buf, r > 0 ? (unsigned)r : 0);
    snprintf(msg, sizeof msg, "SETUP %02x %02x %04x %04x %04x -> read %d (errno %d, %ld ms)%s%s%s%s", rt, rq, wv, wi, wl,
             r, e, mono_ms() - at, note[0] ? ": " : "", note, r > 0 ? " data " : "", r > 0 ? h : "");
    logline(i->name, msg);
    if (rt == 0x21 && rq == 0x09 && r >= 2 + (int)sizeof detect && !memcmp(buf + 2, detect, sizeof detect))
        apple_seen(2, "iAP2's detect");
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
    if (state_file && host_at < 0 && (type == EV_SETUP || type == EV_ENABLE)) { /* a request: the host is there */
        host_at = mono_ms();
        state_write();
    }
    if (type == EV_SETUP) {
        setup(i, e);
        return;
    }
    snprintf(msg, sizeof msg, "%s%s", type <= EV_RESUME ? ev_names[type] : "event?",
             type == EV_ENABLE ? " (the host chose this instance's configuration)" : "");
    logline(i->name, msg);
    if (type == EV_ENABLE) {
        char what[32];
        snprintf(what, sizeof what, "configuration %d", i->config);
        if (i->kind == K_HID) config2 = 1;
        else computer_seen(what);
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
        else if (!strcmp(o, "--car-pcm")) car_pcm = v, ++a;
        else if (!strcmp(o, "--car-file")) car_file = v, ++a;
        else if (!strcmp(o, "--rate-file")) rate_file = v, ++a;
        else if (!strcmp(o, "--cmd-file")) cmd_file = v, ++a;
        else if (!strcmp(o, "--art-dir")) art_dir = v, ++a;
        else if (!strcmp(o, "--state")) state_file = v, ++a;
        else if (!strcmp(o, "--report-ms")) report_ms = atoi(v), ++a;
        else if (!strcmp(o, "--lib-db")) lib_db = v, ++a;
        else if (!strcmp(o, "--lib-sent")) lib_sent_file = v, ++a;
        else if (!strcmp(o, "--lib-cat")) lib_cat = v, ++a;
        else if (!strcmp(o, "--list-file")) list_file = v, ++a;
        else if (!strcmp(o, "--queue-file")) queue_file = v, ++a;
        else if (!strcmp(o, "--udc")) udc_file = v, ++a;
        else if (!strcmp(o, "--counters")) counters_dir = v, ++a;
        else if (!strcmp(o, "--under")) under_file = v, ++a;
        else if (!strcmp(o, "--auto-ms")) auto_ms = atoi(v), ++a;
        else if (!strcmp(o, "--car-ms")) car_ms = atoi(v), ++a;
        else if (!strcmp(o, "--quiet-ms")) link_quiet_ms = atoi(v), ++a;
        else if (!strcmp(o, "--card-root")) card_root = v, ++a;
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
    alarm_interrupts();
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
    started_at = mono_ms();
    state_write();
    while (!stopping && !leave) {
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
        if (iap2_mode) rate_poll(mono_ms()), iap2_tick();
        long now = mono_ms();
        host_poll(now);
        health_poll(now);
        if (active && disabled_at >= 0 && now - disabled_at > gap_ms) {
            active = 0, disabled_at = -1;
            snprintf(msg, sizeof msg, "session %d ended (no ENABLE within %ld ms of the DISABLE)", session, gap_ms);
            logline("probe", msg);
            summary();
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
    if (leave) rc = leave;
    if (active || leave) summary();
    snprintf(msg, sizeof msg, "end: %s, %d session%s, exit %d", stopping ? "signalled" : "by itself", session,
             session == 1 ? "" : "s", rc);
    logline("probe", msg);
    if (iap2_mode) unlink(car_file); /* the player back to demo's output */
    if (writer_pid > 0) kill(writer_pid, SIGTERM);
    if (logfd != 2) fsync(logfd);
    for (int k = 0; k < ninst; ++k) close(in[k].ep0);
    return rc;
}
