/* q2-iap (/usr/bin/q2-iap): the iPhone gadget's daemon (docs/iphone-mode.md). It owns the FunctionFS instances of
 * the iPhone-shaped gadget that patch/q2car.sh makes for Car Audio (and tools/iap_probe/q2-iap-probe.sh for the
 * experiments), writes each one's descriptors (an iPhone's configuration 1, 2, 3 or 4 shape), and logs every event
 * and control request the host sends, answering only what an iPhone in media mode answers: the HID report
 * descriptor, Apple's C0 53 (four zero bytes), GET_REPORT with zeros; everything OUT is read and logged, every other
 * IN request stalled. With echo on, a SET_REPORT carrying iAP2's detect (FF 55 02 00 EE 10) is echoed on the HID
 * interrupt endpoint, so the host's next reports carry its link SYN. With --iap2 it speaks iAP2 instead (iap2_*.c):
 * the detect answered, the link set up, the car authenticated and identified, and the answers an iPhone gives next,
 * every packet and message logged. With --state (Car Audio) it decides what the host is and watches the car's
 * audio (below, "Car Audio in the image").
 *
 *   q2-iap [--echo] [--echo-from N] [--iap2] [--np FILE] [--uuid HEX] [--car-pcm DEV] [--car-file F]
 *          [--cmd-file F] [--rate-file F] [--art-dir D] [--sessions N] [--for S] [--gap-ms MS] [--carplay]
 *          [--phase NAME] [--state F] [--udc F] [--counters DIR] [--under F] [--auto-ms MS] [--car-ms MS]
 *          [--lib-db F] [--lib-cat F] [--lib-sent F] [--list-file F] [--queue-file F] [--card-wait-ms MS]
 *          [--clock-file F]
 *          LOG DIR:KIND...
 *   q2-iap --descs KIND     the descriptor and strings blobs for KIND on stdout (tests)
 *
 * KIND is ptp (configuration 1), hid (2), ptpmux (3) or ptpmuxeth (4). A session is the time from an
 * ENABLE to a DISABLE or SUSPEND not followed by another ENABLE or RESUME within --gap-ms on the boot clock (a cable out, not a
 * reset: in the car a cable out was a SUSPEND, the controller left suspended with no VBUS); with
 * --sessions N it exits 10 after the Nth ends, and with --for S, 0 after S seconds. Exit 1 is a
 * set-up failure (an ep0 that would not take its descriptors); 20 and 21 are --state's computer and silent host.
 * Every ep0 stays open until the end: closing one unbinds the gadget.
 *
 * Built for the Q2 by tools/build.py's compile_helper (and tools/iap_probe/probe_build.py for the experiments)
 * against the rootfs's libc with no headers (the declarations and MIPS constants of q2_iap_sys.h), and for this
 * machine with -DPROBE_HOST (test/iap_probe.py). */
#include "q2_iap_sys.h"
#include "q2_iap.h"
#include "coda_rc_cmdq.h" /* the shim's command queue: needs open, write, close, O_NONBLOCK, O_CLOEXEC, ssize_t */

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
static long log_slowest; /* this loop pass's slowest log write, ms */
long mono_ms(void) {
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return t.tv_sec * 1000L + t.tv_nsec / 1000000L;
}
/* The clock of every timer and deadline (the session's gap, the link's, the host's windows): CLOCK_BOOTTIME, which
 * goes on while the Q2 is suspended, where CLOCK_MONOTONIC stops, so hours asleep are hours (uac1-power-app.md #6).
 * The log's stamps and the loop's step timer stay on the monotonic clock, dmesg's. clock_skew: --clock-file's
 * milliseconds, a sleep the host tests stand in for (read every pass, sleep_poll). */
static const char *clock_file;
static long clock_skew;
long boot_ms(void) {
    struct timespec t;
    clock_gettime(CLOCK_BOOTTIME, &t);
    return t.tv_sec * 1000L + t.tv_nsec / 1000000L + clock_skew;
}
/* One line, written whole: the shell's lines go to the same file (O_APPEND). Stamped with
 * CLOCK_MONOTONIC as seconds, dmesg's clock (equal to /proc/uptime with no suspend between). */
/* The lines go to a child over a pipe (log_start), which writes them to the card, so no card write is ever in the
 * loop: on the 2026-11-22 drive three of the stalls, 109 to 278 ms, were a log write. A line is one write, under
 * PIPE_BUF, so it is never split or mixed with a child's. A full pipe (the card stalled for seconds) costs lines, not
 * the loop: they are counted and said with the next line that goes. */
static unsigned log_dropped;
void logline(const char *tag, const char *msg) {
    char line[3200];
    long ms = mono_ms();
    int n = snprintf(line, sizeof line, "%ld.%03ld %s %s\n", ms / 1000, ms % 1000, tag, msg);
    if (n > (int)sizeof line - 1) n = sizeof line - 1, line[n - 1] = '\n';
    if (log_dropped) {
        char said[96];
        int k = snprintf(said, sizeof said, "%ld.%03ld log %u lines dropped (the log's writer was behind)\n",
                         ms / 1000, ms % 1000, log_dropped);
        if (write(logfd, said, k) == k) log_dropped = 0;
    }
    if (n > 0 && write(logfd, line, n) != n) ++log_dropped;
    long took = mono_ms() - ms;
    if (took > log_slowest) log_slowest = took; /* the pipe's pace: the card's, before log_start */
}

/* ---- the event loop's steps (docs/iphone-mode.md#the-loop-s-steps) ----
 * Each pass of the loop is cut into named steps (loop_step: the one that starts now, the last one closed); a pass that
 * held the loop stall_ms or more is logged with the step that took the most of it, the slowest log write in it and,
 * past STALL_KMSG_MS, the kernel's USB lines (kernel_usb_lines), so a stall names its cause in the log. A mark costs
 * a clock read. */
#define STALL_KMSG_MS 1000
static long stall_ms = 100; /* --stall-ms */
static long pass_at, step_at, step_most;
static const char *step_now = "start", *step_most_name = "start";
static const char *loop_step(const char *name) {
    long now = mono_ms();
    const char *was = step_now;
    if (now - step_at > step_most) step_most = now - step_at, step_most_name = step_now;
    step_at = now, step_now = name;
    return was;
}
static void kernel_usb_lines(const char *why);
static void loop_begin(void) {
    pass_at = step_at = mono_ms(), step_most = 0, log_slowest = 0, step_now = step_most_name = "poll's wake";
}
static void loop_end(void) {
    loop_step("poll");
    long took = step_at - pass_at;
    if (took < stall_ms) return;
    char msg[200];
    snprintf(msg, sizeof msg, "loop: a pass held %ld ms, %ld of it in %s; the slowest log write %ld ms", took, step_most,
             step_most_name, log_slowest);
    logline("probe", msg);
    if (took >= STALL_KMSG_MS) kernel_usb_lines("after a stall");
}

/* The kernel's last USB lines (the controller's, dwc2, FunctionFS's, the gadget's and the UAC1 module's), from its
 * ring buffer (klogctl's read-all, which takes nothing out of it), into the log: at a long stall and at a lost link,
 * before the DAC's chatter rolls them out (on 2026-11-21 the cable's dmesg, saved at its end, began 190 s after the
 * stall). At most once in 10 s, and the last KMSG_LINES of them. */
#define KMSG_LINES 24
static void kernel_usb_lines(const char *why) {
    static long last = -100000;
    long now = mono_ms();
    if (now - last < 10000) return;
    last = now;
    enum { CAP = 1 << 17 };
    char *b = malloc(CAP + 1), msg[300];
    int n = b ? klogctl(3, b, CAP) : -1; /* SYSLOG_ACTION_READ_ALL */
    if (n < 0) {
        snprintf(msg, sizeof msg, "kernel: its log unreadable %s (errno %d)", why, b ? ERRNO : 0);
        logline("probe", msg);
        free(b);
        return;
    }
    b[n] = 0;
    static const char *const keys[] = { "dwc2", "ffs_", "functionfs", "FunctionFS", "gadget", "udc", "UDC", "usb", "USB", "uac", "UAC" };
    const char *at[KMSG_LINES];
    unsigned found = 0;
    for (char *line = b; *line;) {
        char *end = strchr(line, '\n');
        if (end) *end = 0;
        for (unsigned k = 0; k < sizeof keys / sizeof *keys; ++k)
            if (strstr(line, keys[k])) {
                at[found++ % KMSG_LINES] = line;
                break;
            }
        line = end ? end + 1 : line + strlen(line);
    }
    snprintf(msg, sizeof msg, "kernel: %u USB lines in its log %s%s", found, why, found > KMSG_LINES ? ", the last ones:" : "");
    logline("probe", msg);
    for (unsigned i = found > KMSG_LINES ? found - KMSG_LINES : 0; i < found; ++i)
        snprintf(msg, sizeof msg, "kernel: %.280s", at[i % KMSG_LINES]), logline("probe", msg);
    free(b);
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

/* Every fd the loop opens is close-on-exec (O_CLOEXEC, pipe_cloexec), so ffmpeg holds none, and a child that does
 * not exec drops the ep0s at its start (child_drop): an ep0 a child held kept FunctionFS busy, so a lost link's
 * teardown could not unmount it and the relink failed for as long as the child ran (car-bt-power.md #9). */
int pipe_cloexec(int *fds) {
    if (pipe(fds)) return -1;
    fcntl(fds[0], F_SETFD, FD_CLOEXEC), fcntl(fds[1], F_SETFD, FD_CLOEXEC);
    return 0;
}
void child_drop(void) {
    for (int k = 0; k < ninst; ++k)
        if (in[k].ep0 >= 0) close(in[k].ep0), in[k].ep0 = -1;
}

/* The log's writer (logline): a child that takes the lines from a pipe and appends each read whole to the card's
 * file. Within a cable the file is capped: past LOG_TRIM (--log-cap) it keeps no packet trace (the pkt, msg, report,
 * SETUP and file lines), and past twice that only q2-iap's own events (the probe and car lines: stalls, sessions, the verdict, the
 * summary) and the link's (health, losses); each mark is said once. At its end (every write end closed: q2-iap and
 * its children gone) it syncs the file. */
#define LOG_TRIM (6 << 20)
static unsigned long long log_trim = LOG_TRIM; /* --log-cap; the hard mark twice it */
static int log_pid;
static int log_keeps(const char *l, unsigned n, unsigned long long size) {
    if (size < log_trim) return 1;
    const char *sp = memchr(l, ' ', n), *m = sp ? memchr(sp + 1, ' ', n - (unsigned)(sp + 1 - l)) : 0;
    if (!m) return 1;
    ++m; /* the line's text after its stamp and tag */
    unsigned rest = n - (unsigned)(m - l);
    static const char *const trace[] = { "pkt ", "msg ", "report ", "SETUP ", "file " };
    for (unsigned k = 0; k < sizeof trace / sizeof *trace; ++k)
        if (rest >= strlen(trace[k]) && !memcmp(m, trace[k], strlen(trace[k]))) return 0;
    if (size < 2 * log_trim) return 1;
    return !memcmp(sp + 1, "probe ", 6) || !memcmp(sp + 1, "car ", 4) || (rest >= 5 && !memcmp(m, "link:", 5));
}
static void log_child(int in, int out) {
    static char b[65536 + 200];
    unsigned long long size = 0;
    int said = 0;
    for (;;) {
        int r = (int)read(in, b, 65536);
        if (r < 0 && ERRNO == EINTR) continue;
        if (r <= 0) break;
        if (size >= log_trim && said < 1 + (size >= 2 * log_trim)) {
            int k = snprintf(b + r, 200, "log: past %llu bytes this cable: %s\n", size,
                             size >= 2 * log_trim ? "only q2-iap's events and the link's kept from here"
                                                  : "no packet trace from here");
            write(out, b + r, k), ++said;
        }
        unsigned o = 0;
        for (unsigned i = 0; i < (unsigned)r;) { /* whole lines, each kept or not */
            char *nl = memchr(b + i, '\n', (unsigned)r - i);
            unsigned e = nl ? (unsigned)(nl - b) + 1 : (unsigned)r;
            if (log_keeps(b + i, e - i, size)) memmove(b + o, b + i, e - i), o += e - i;
            i = e;
        }
        if (o && write(out, b, o) > 0) size += o;
    }
    fsync(out);
    _exit(0);
}
static void log_start(void) {
    int fds[2];
    if (logfd == 2 || pipe_cloexec(fds)) return;
    int pid = fork();
    if (pid == 0) {
        signal(SIGTERM, SIG_IGN), signal(SIGINT, SIG_IGN), signal(SIGHUP, SIG_IGN); /* it ends with the pipe */
        close(fds[1]);
        log_child(fds[0], logfd);
    }
    close(fds[0]);
    if (pid < 0) {
        close(fds[1]);
        return;
    }
    close(logfd);
    logfd = fds[1], log_pid = pid;
    fcntl(logfd, F_SETFL, O_NONBLOCK);
}
/* q2-iap's end: the pipe closed and its writer given LOG_END_MS to write the last lines and sync, so q2car.sh finds
 * the summary when it reads the file. */
#define LOG_END_MS 1000
static void log_end(void) {
    if (!log_pid) return;
    close(logfd), logfd = 2;
    for (long waited = 0; waited < LOG_END_MS && !kill(log_pid, 0); waited += 20) {
        struct timespec ts = { 0, 20000000L };
        nanosleep(&ts, 0);
    }
}

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

#include "iap2.h"
#include "iap2_link.h"
#include "iap2_names.h"
#include "iap2_nowplaying.h"
#include "iap2_session.h"
#include "q2_iap_lib.h"

static int iap2_mode, writer_fd = -1, writer_pid, done_fd = -1, report_ms;
static unsigned wr_queued, wr_done; /* input reports handed to the writer, and those it has written to ep1 */
static const char *np_file = "/tmp/coda-rc.now";
/* What the player and the shim read (player/output.c output_car, patch/coda_rc_shim.c cr_command), and Cover Flow's
 * cache of covers (patch/coverflow.c art_path); each can be moved for the host tests. */
static const char *cmdq_file = CRC_CMDQ; /* --cmdq-file: the shim's command queue (coda_rc_cmdq.h) */
static const char *car_file = "/tmp/q2-iap.audio", *car_pcm = "null", *cmd_file = "/tmp/coda-rc.cmd",
                  *art_dir = "/mnt/mmc/.coverflow", *rate_file = "/tmp/q2-iap.rate";
/* A relink under way (patch/usb_car.c USB_CAR_RELINK): written at a lost link's exit 22, before the car's file goes,
 * so demo holds its pause for a car whose audio comes back; removed once the car asks for audio again. */
static const char *relink_file = "/tmp/q2car.relink";
static char uuid[40] = "51320000-0000-4000-8000-000000000000", library_id[64];

/* The input reports go out through a child that writes them to the HID instance's ep1 one at a time: a write there
 * waits for the host to collect it, and the event loop must never wait (the car's control requests go unanswered
 * meanwhile). The pipe is non-blocking on this side, so a host that stops collecting costs reports, not the loop. */
static void writer_start(const char *dir) {
    int fds[2], dn[2];
    char path[160];
    if (pipe_cloexec(dn)) dn[0] = dn[1] = -1;
    if (pipe_cloexec(fds)) {
        logline("iap2", "pipe failed: no input reports");
        return;
    }
    snprintf(path, sizeof path, "%s/ep1", dir);
    writer_pid = fork();
    if (writer_pid == 0) {
        signal(SIGTERM, SIG_DFL), signal(SIGINT, SIG_DFL), signal(SIGHUP, SIG_DFL); /* the probe's only set a flag */
        child_drop();
        close(fds[1]);
        if (dn[0] >= 0) close(dn[0]);
        int ep = open(path, O_RDWR | O_CLOEXEC), fails = 0;
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
    if (trace_quiet && w == (int)len + 1) return; /* a position update's or a bare acknowledgement's (iap2_link.c) */
    iap2_hex(h, sizeof h, r, len < 24 ? len : 24);
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
    if (done_fd >= 0 && !n) close(done_fd), done_fd = -1; /* the writer gone: its hang-up would wake poll for ever */
    return done_fd < 0 ? 0 : (int)(wr_queued - wr_done);
}

static int read_some(const char *path, char *b, unsigned cap);
/* The shim's Now Playing (patch/coda_rc_shim.c cr_now, from now_source.c: key=value lines; pos in ms, len in
 * seconds, live=1 for a radio's stream), written while Car Remote is on or the car's iPhone is on the cable. */
static int io_now_playing(struct iap2_np *n) {
    char b[1600];
    if (read_some(np_file, b, sizeof b) <= 0) return 0;
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
    /* a server song from its app's stream cache, rid 0 here: its library rid, as the car was sent it (lib_stream_rids,
     * an index in RAM) */
    if (!n->id && n->path[0]) {
        const char *p = n->path;
        lib_stream_rids(&p, &n->id, 1);
    }
    return 1;
}

/* Up to cap - 1 bytes of a small file, a 0 after them: their count, or -1 with no file (the tmpfs files read every
 * pass or second: Now Playing, the rate, the controller's state, the clock, a counter) */
static int read_some(const char *path, char *b, unsigned cap) {
    int fd = open(path, O_RDONLY | O_CLOEXEC), n = fd < 0 ? -1 : (int)read(fd, b, cap - 1);
    if (fd >= 0) close(fd);
    b[n > 0 ? n : 0] = 0;
    return n;
}

/* A file written whole, through a temporary name, mode 0600 (the shim takes only root's own, private files). */
int write_whole(const char *path, const char *b, unsigned n) {
    char tmp[200];
    snprintf(tmp, sizeof tmp, "%s.iap", path);
    int fd = open(tmp, O_WRONLY | O_CREAT | O_TRUNC | O_CLOEXEC, 0600);
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
    unlink(relink_file); /* the relink, if one was under way, is over */
}

/* The player's rate for the song about to play (player/output.c car_ask, docs/iphone-mode.md#per-song-rate), read
 * every RATE_POLL_MS while the car takes audio, RATE_IDLE_MS while a car is on the link otherwise, and not with no
 * car (a charger), and handed to iap2_session.c, which tells the car when it changes; none written, 44.1 kHz. */
#define RATE_POLL_MS 20
#define RATE_IDLE_MS 250 /* with a car on the link and its audio not asked for */
static long rate_at;
static long rate_every(void) { return audio_on ? RATE_POLL_MS : seen ? RATE_IDLE_MS : -1; } /* -1: not read */
static unsigned io_rate(void) { /* the host's rate: the file's now, also read when the car asks for audio */
    char b[16];
    read_some(rate_file, b, sizeof b);
    return atoi(b) > 0 ? (unsigned)atoi(b) : 44100;
}
static void rate_poll(long now) {
    long every = rate_every();
    if (every < 0 || (rate_at && now - rate_at < every)) return;
    rate_at = now;
    iap2_audio_rate(io_rate());
}

/* A button or a seek, as the Remote app's commands reach the shim (Car Remote on): down the shim's queue
 * (coda_rc_cmdq.h), each press its own line, else its single command file. Its numbers start high, so an ack
 * of one is never taken for coda-rc's own. */
void io_command(const char *verb) {
    static unsigned seq = 0x51000000u;
    char b[96];
    int n = snprintf(b, sizeof b, "%u %s\n", ++seq, verb);
    if (!crc_cmdq_send(cmdq_file, b, (unsigned)n)) return;
    if (write_whole(cmd_file, b, (unsigned)n)) logline("iap2", "command: the shim's file could not be written");
}

unsigned read_file(const char *path, unsigned char *out, unsigned cap) {
    int fd = open(path, O_RDONLY | O_CLOEXEC), got = 0, r;
    if (fd < 0) return 0;
    while (got < (int)cap && (r = (int)read(fd, out + got, cap - got)) > 0) got += r;
    close(fd);
    return got < (int)cap ? (unsigned)got : 0; /* too large: none */
}

/* Cover Flow's key (patch/coverflow.c tags_key, coda_rc_lib.c fnv32): FNV-1a with a separator after each part */
unsigned fnv_part(unsigned h, const char *s) {
    while (*s) h = (h ^ (unsigned char)*s++) * 16777619u;
    return h * 16777619u;
}

/* ---- the car's cover (docs/iphone-mode.md#the-car-s-cover) ----
 *
 * Looked for in a child (art_child), one song at a time, so the loop never waits on the card, the app or ffmpeg: on
 * the 2026-11-22 drive a cover only ffmpeg could make took 13.4 s. In this order: Cover Flow's 160 px copy (keyed by
 * artist and album); one made for the album before (art_made.<key>.jpg); a server song's cover from its app
 * (covers/<album id>.jpg); a cover beside the song; a server song's cover asked of its app (art_ask: the app fetches
 * it from the server), waited for ART_ASK_MS; the picture in the song's own file, fitted into 160 px as a baseline
 * JPEG by ffmpeg, niced, killed after ART_MADE_MS. The key is the album's (its artist and name, as Cover Flow and the
 * Bluetooth cover key it), else the song's path, so the album's next songs take the same cover with no child.
 *
 * The child writes 'w' to its pipe as it starts on an ask or ffmpeg, 'm' when ffmpeg made the cover, then 'c' and
 * the JPEG (none: no cover). Now Playing for a new song waits for the quick sources (iap2_nowplaying.c ART_HOLD_MS,
 * until 'w' or the end); a cover found after it went is sent with Now Playing again (iap2_art_again). */
#define ART_ASK_MS 4000   /* --art-ask-ms: the app's answer to an ask waited for this long */
#define ART_MADE_MS 20000 /* ffmpeg's longest */
#define ART_CAP 65536     /* iap2_xfer.c ART_MAX */
static const char *art_made = "/tmp/q2-iap.art", *art_ffmpeg = "/usr/bin/ffmpeg"; /* --art-made, --ffmpeg */
static const char *ask_dir = "/tmp"; /* --ask-dir: the apps' ask, state and socket files (q2-<id>.*) */
static long art_ask_ms = ART_ASK_MS;
static struct {
    int pid, fd;   /* the child and its pipe while it looks, else 0 and -1 */
    int state;     /* 0 nothing, 1 looking, 2 looking slowly (an ask or ffmpeg), 3 found or not (got_n) */
    unsigned key, seq, made_key;
    long at;
    unsigned char *got; /* what the pipe brought: the markers, then the JPEG from jpeg_at (-1: not yet) */
    unsigned got_n, got_cap;
    int jpeg_at, made; /* made: ffmpeg made it */
} artw = {.fd = -1, .jpeg_at = -1};

static unsigned art_key(const struct iap2_np *n) {
    return n->album[0] ? fnv_part(fnv_part(2166136261u, n->artist), n->album) : fnv_part(2166136261u, n->path);
}

/* the art child's SIGTERM: its ffmpeg killed and reaped before it goes */
static volatile int art_term;
static void art_on_term(int s) { art_term = s; }
static void art_nap(long ms) {
    struct timespec ts = { ms / 1000, (ms % 1000) * 1000000L };
    nanosleep(&ts, 0);
}

/* A file written whole through a temporary name, readable by the app (mode 0644) */
static int art_write(const char *path, const char *b, unsigned n) {
    char tmp[220];
    snprintf(tmp, sizeof tmp, "%s.iap", path);
    int fd = open(tmp, O_WRONLY | O_CREAT | O_TRUNC | O_CLOEXEC, 0644);
    if (fd < 0) return -1;
    int ok = write(fd, b, n) == (int)n;
    close(fd);
    if (!ok || rename(tmp, path)) return unlink(tmp), -1;
    return 0;
}

/* The app's q2app says covers=1: it answers asks */
static int art_app_answers(const char *app) {
    char p[300];
    unsigned char b[1025];
    snprintf(p, sizeof p, "%s/mnt/mmc/.%s/q2app", card_root ? card_root : "", app);
    unsigned n = read_file(p, b, sizeof b - 1);
    b[n] = 0;
    for (char *l = (char *)b; l && *l; l = strchr(l, '\n'), l = l ? l + 1 : 0)
        if (!strncmp(l, "covers=1", 8) && (l[8] == '\n' || l[8] == '\r' || !l[8])) return 1;
    return 0;
}

/* The ask (the contract coda-rc's covers use, docs/internals.md#car-remote-covers, in a file and key of q2-iap's own):
 * <ask_dir>/q2-<app>.coverask-iap written whole with a line, "<seq> <px> <album>"; the app answers in its state file,
 * coverack-iap=<seq> <code> (0 saved), saving the cover where it saves .coverask's. The app named in coda-rc.wake when
 * its socket is not there (demo starts it). The code, or -1 with no answer in art_ask_ms. The file goes at the end. */
static int art_ask(const char *app, const char *album, unsigned seq) {
    char ask[200], st[200], line[96];
    snprintf(ask, sizeof ask, "%s/q2-%s.coverask-iap", ask_dir, app);
    snprintf(st, sizeof st, "%s/q2-%s.state", ask_dir, app);
    int n = snprintf(line, sizeof line, "%u 160 %s\n", seq, album);
    if (n <= 0 || n >= (int)sizeof line || art_write(ask, line, (unsigned)n)) return -1;
    char sock[200];
    snprintf(sock, sizeof sock, "%s/q2-%s.browse.sock", ask_dir, app);
    if (access(sock, 0)) {
        char wake[200], who[24];
        snprintf(wake, sizeof wake, "%s/coda-rc.wake", ask_dir);
        art_write(wake, who, (unsigned)snprintf(who, sizeof who, "%s\n", app));
    }
    int code = -1;
    for (long waited = 0; code < 0 && waited < art_ask_ms && !art_term; waited += 100) {
        unsigned char b[1601];
        unsigned got = read_file(st, b, sizeof b - 1);
        b[got] = 0;
        char *v = strstr((char *)b, "coverack-iap=");
        if (v && v != (char *)b && v[-1] != '\n') v = 0;
        char *sp = v ? strchr(v, ' ') : 0;
        if (v && (int)((unsigned)strtoul(v + 13, 0, 10) - seq) >= 0) code = sp ? atoi(sp + 1) : 3;
        if (code < 0) art_nap(100);
    }
    unlink(ask);
    return code;
}

/* ffmpeg on the song's own picture, into made (through made.ff): 1 when it made a JPEG. Niced, killed after
 * ART_MADE_MS or at the child's SIGTERM, and reaped either way. */
static int art_ffmpeg_run(const char *path, const char *made) {
    char tmp[620];
    snprintf(tmp, sizeof tmp, "%s.ff", made);
    int pid = fork();
    if (pid == 0) {
        nice(10);
        execl(art_ffmpeg, art_ffmpeg, "-nostdin", "-v", "quiet", "-xerror", "-i", path, "-frames:v", "1", "-vf",
              "scale=160:160:force_original_aspect_ratio=decrease", "-pix_fmt", "yuvj420p", "-q:v", "3", "-f", "mjpeg",
              "-y", tmp, (char *)0);
        _exit(127);
    }
    if (pid < 0) return 0;
    long waited = 0;
    int st = 0, done = 0;
    while (!(done = waitpid(pid, &st, WNOHANG) == pid) && waited < ART_MADE_MS && !art_term) art_nap(50), waited += 50;
    if (!done) kill(pid, 9), waitpid(pid, &st, 0);
    unsigned char b[4];
    int fd = open(tmp, O_RDONLY | O_CLOEXEC), ok = done && fd >= 0 && read(fd, b, sizeof b) == (int)sizeof b &&
                                                    b[0] == 0xff && b[1] == 0xd8;
    if (fd >= 0) close(fd);
    ok = ok && !rename(tmp, made);
    if (!ok) unlink(tmp);
    char msg[120];
    snprintf(msg, sizeof msg, "art: the song's own picture %s in %ld ms%s", ok ? "made" : "not made", waited,
             done ? "" : " (ffmpeg killed)");
    logline("iap2", msg);
    return ok;
}

static void art_child(const struct iap2_np *n, unsigned key, unsigned seq, int out) {
    static unsigned char b[ART_CAP];
    char p[600], app[24], album[48];
    unsigned got = 0;
    const char *from = "none";
    signal(SIGTERM, art_on_term), signal(SIGCHLD, SIG_DFL); /* its ffmpeg is waited for */
    if (n->album[0]) {
        snprintf(p, sizeof p, "%s/%08x.jpg", art_dir, key);
        if ((got = read_file(p, b, sizeof b))) from = "Cover Flow's";
    }
    if (!got) {
        snprintf(p, sizeof p, "%s.%08x.jpg", art_made, key);
        if ((got = read_file(p, b, sizeof b))) from = "made before";
    }
    int server = lib_app_album(n->id, app, sizeof app, album, sizeof album);
    if (!got && server) {
        snprintf(p, sizeof p, "%s/mnt/mmc/.%s/covers/%s.jpg", card_root ? card_root : "", app, album);
        if ((got = read_file(p, b, sizeof b))) from = "its app's";
    }
    static const char *const names[] = { "cover.jpg", "folder.jpg", "front.jpg" };
    const char *slash = strrchr(n->path, '/');
    for (unsigned i = 0; !got && slash && i < 3; ++i) {
        snprintf(p, sizeof p, "%.*s/%s", (int)(slash - n->path), n->path, names[i]);
        if ((got = read_file(p, b, sizeof b))) from = "beside the song";
    }
    if (!got && server && art_app_answers(app)) {
        long at = boot_ms();
        write(out, "w", 1);
        int code = art_ask(app, album, seq);
        snprintf(p, sizeof p, "%s/mnt/mmc/.%s/covers/%s.jpg", card_root ? card_root : "", app, album);
        if (!code && (got = read_file(p, b, sizeof b))) from = "asked of its app";
        char msg[160];
        snprintf(msg, sizeof msg, "art: asked %s for %s (ask %u): %s in %ld ms", app, album, seq,
                 code < 0 ? "no answer" : !code ? "saved" : "none", boot_ms() - at);
        logline("iap2", msg);
    }
    if (!got && n->path[0] && !art_term) {
        write(out, "w", 1);
        logline("iap2", "art: none found; the song's own picture asked of ffmpeg");
        snprintf(p, sizeof p, "%s.%08x.jpg", art_made, key);
        if (art_ffmpeg_run(n->path, p) && (got = read_file(p, b, sizeof b))) from = "the song's own", write(out, "m", 1);
    }
    char msg[120];
    snprintf(msg, sizeof msg, "art: %u bytes, %s", got, from);
    logline("iap2", msg);
    if (write(out, "c", 1) == 1) lib_write_all(out, b, got);
    _exit(0);
}

/* The cover's child for this song started, the last one's ended (it kills its ffmpeg) */
static void art_start_child(const struct iap2_np *n, unsigned key) {
    int fds[2];
    if (artw.pid > 0) kill(artw.pid, SIGTERM);
    if (artw.fd >= 0) close(artw.fd);
    artw.pid = 0, artw.fd = -1, artw.got_n = 0, artw.jpeg_at = -1, artw.made = 0, artw.key = key, artw.state = 3;
    artw.at = boot_ms();
    if (!artw.seq) artw.seq = (unsigned)boot_ms() | 1; /* above any seq an earlier q2-iap gave since boot */
    unsigned seq = artw.seq++;
    if (pipe_cloexec(fds)) return;
    int pid = fork();
    if (pid == 0) {
        signal(SIGINT, SIG_DFL), signal(SIGHUP, SIG_DFL);
        child_drop();
        close(fds[0]);
        art_child(n, key, seq, fds[1]);
    }
    close(fds[1]);
    if (pid < 0) {
        close(fds[0]);
        return;
    }
    fcntl(fds[0], F_SETFL, O_NONBLOCK);
    artw.pid = pid, artw.fd = fds[0], artw.state = 1;
}

/* The host's art_pending: 1 while the song's quick sources are still being looked at (its child started here if it
 * is a new one), so Now Playing can wait for its cover. */
static int io_art_pending(const struct iap2_np *n) {
    unsigned key = art_key(n);
    if (artw.key != key || !artw.state) art_start_child(n, key);
    return artw.state == 1;
}

/* The host's artwork: the song's cover as the child found it, or 0 (none, or not yet) */
static unsigned io_artwork(const struct iap2_np *n, unsigned char *out, unsigned cap) {
    if (artw.state != 3 || artw.key != art_key(n) || artw.jpeg_at < 0) return 0;
    unsigned len = artw.got_n - (unsigned)artw.jpeg_at;
    if (!len || len > cap) return 0;
    memcpy(out, artw.got + artw.jpeg_at, len);
    return len;
}

/* The child's bytes taken in, its markers read as they come; at its end Now Playing is told (iap2_nowplaying.c
 * iap2_art_done). Every pass of the loop. */
static void art_poll(void) {
    if (artw.fd < 0) return;
    for (;;) {
        if (artw.got_n + 4096 > artw.got_cap) {
            unsigned char *w = realloc(artw.got, artw.got_cap + ART_CAP + 4096);
            if (!w) break;
            artw.got = w, artw.got_cap += ART_CAP + 4096;
        }
        int r = (int)read(artw.fd, artw.got + artw.got_n, artw.got_cap - artw.got_n);
        if (r <= 0) {
            if (r < 0 && (ERRNO == EAGAIN || ERRNO == EINTR)) return;
            break;
        }
        for (unsigned i = artw.got_n; artw.jpeg_at < 0 && i < artw.got_n + (unsigned)r; ++i) {
            if (artw.got[i] == 'w' && artw.state == 1) artw.state = 2, iap2_art_slow();
            else if (artw.got[i] == 'm') artw.made = 1;
            else if (artw.got[i] == 'c') artw.jpeg_at = (int)i + 1;
        }
        artw.got_n += (unsigned)r;
    }
    close(artw.fd), artw.fd = -1, artw.pid = 0, artw.state = 3;
    if (artw.made && artw.made_key && artw.made_key != artw.key) { /* the last album's made cover goes: one kept, in RAM */
        char p[600];
        snprintf(p, sizeof p, "%s.%08x.jpg", art_made, artw.made_key);
        unlink(p);
    }
    if (artw.made) artw.made_key = artw.key;
    iap2_art_done(artw.jpeg_at >= 0 && (unsigned)artw.jpeg_at < artw.got_n);
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
    if (read_some(path, b, sizeof b) <= 0) return 0;
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
    int fd = open(p, O_WRONLY | O_TRUNC | O_CLOEXEC), n = snprintf(b, sizeof b, "%u\n", v);
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
    long now = boot_ms();
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
        if (read_some(udc_file, s, sizeof s) > 0 && (!strncmp(s, "addressed", 9) || !strncmp(s, "configured", 10))) {
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

/* Every HEALTH_MS while the car has asked for audio, and until its problem is said over: is it flowing, and cleanly.
 * Otherwise nothing is read. */
static void health_poll(long now) {
    unsigned sent, missed, under;
    char msg[200];
    if (!state_file) return;
    if (!audio_on && !flowing && !*trouble) { /* nothing to count: only a name newly identified to write */
        audio_at = -1;
        if (strcmp(state_name, car_name)) state_write();
        return;
    }
    if (now - health_at < HEALTH_MS) return;
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

/* The link lost (the car stopped acknowledging through every retry, went quiet with packets waiting, or a request's
 * data stage was not its report: iap2_link.c iap2_lost): with --state, q2-iap ends (exit 22) and q2car.sh
 * shows the car the iPhone again (the gadget off the controller and back), which is how a car starts a session
 * afresh; it sent nothing more by itself after the loss on 2026-11-21 (126 s, to the cable out). */
static void io_link_lost(void) {
    kernel_usb_lines("at the lost link");
    if (state_file && !leave) {
        leave = 22, logline("car", "link: lost; the iPhone shown again");
        if (write_whole(relink_file, "22\n", 3)) logline("car", "link: the relink's note could not be written");
    } else
        logline("car", leave ? "link: lost; q2-iap already ending" : "link: lost; no --state, so q2-iap goes on");
}

/* The host's calls that read or write a file or the database, each a step of the loop's of its own (loop_step), so
 * a stall names the one that held it. */
#define STEPPED(ret, name, what, params, args)                                                                      \
    static ret name##_stepped params {                                                                                \
        const char *was = loop_step(what);                                                                            \
        ret r = name args;                                                                                            \
        loop_step(was);                                                                                               \
        return r;                                                                                                     \
    }
#define STEPPED_VOID(name, what, params, args)                                                                       \
    static void name##_stepped params {                                                                               \
        const char *was = loop_step(what);                                                                            \
        name args;                                                                                                    \
        loop_step(was);                                                                                               \
    }
STEPPED(int, io_now_playing, "the Now Playing file", (struct iap2_np * n), (n))
STEPPED(int, io_art_pending, "the cover's child", (const struct iap2_np *n), (n))
STEPPED(int, lib_load, "the library's read", (void), ()) /* its child's start, and below the tables' hand-over */
STEPPED(int, lib_ready, "the library's read", (void), ())
STEPPED_VOID(lib_saved, "the library's record", (void), ())
STEPPED_VOID(io_audio, "the car's audio file", (int on), (on))
STEPPED_VOID(io_command, "the shim's command file", (const char *verb), (verb))
STEPPED_VOID(lib_play, "the shim's play list", (const unsigned *rids, unsigned n, unsigned at, int shuffle),
             (rids, n, at, shuffle))
STEPPED(int, lib_queue, "the shim's queue file", (unsigned *rids, unsigned cap), (rids, cap))

static const struct iap2_host io = { io_send,           boot_ms,           io_log,        io_now_playing_stepped,
                                     "Vamp",            uuid,              library_id,    io_audio_stepped,
                                     io_command_stepped, io_artwork,    io_art_pending_stepped, &lib_t,  lib_load_stepped,
                                     lib_ready_stepped, lib_saved_stepped, lib_play_stepped,  lib_queue_stepped,
                                     io_backlog,        io_link_lost,      io_rate };

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
    iap2_hex(h, sizeof h, r, len < 16 ? len : 16);
    int pid = fork();
    if (pid == 0) {
        child_drop();
        alarm(5); /* a host that never polls: the write is interrupted, the request dequeued */
        int fd = open(path, O_RDWR | O_CLOEXEC);
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
    int r = 0, stalled = 0, timed_out = 0;
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
            /* at most DATA_STAGE_S, as the OUT data stage: a host that never takes it would hold the loop */
            const char *was = loop_step("an answer on ep0");
            alarm(DATA_STAGE_S);
            r = write(i->ep0, ans, an);
            timed_out = r < 0 && ERRNO == EINTR;
            alarm(0);
            loop_step(was);
        } else {
            stalled = 1;
            r = read(i->ep0, buf, 0); /* the wrong direction: FunctionFS stalls ep0 */
        }
        snprintf(msg, sizeof msg, "SETUP %02x %02x %04x %04x %04x -> %s%s (%d, errno %d, %ld ms)%s%s", rt, rq, wv, wi,
                 wl, stalled ? "stalled" : "answered ", stalled ? "" : (snprintf(h, 16, "%u bytes", an), h), r,
                 r < 0 ? ERRNO : 0, mono_ms() - at, note[0] ? ": " : "", note);
        logline(i->name, msg);
        if (timed_out && iap2_mode && seen) /* the control pipe out of step with the car */
            iap2_lost("link: lost (an answer on ep0 not taken in 2 s)");
        return;
    }
    unsigned want = wl < sizeof buf ? wl : sizeof buf;
    /* the data stage, and the status stage acknowledged; at most DATA_STAGE_S, or the loop stops with it: on
     * 2026-11-21 a SET_REPORT's data never came, and this read held everything else for 35 s (the car dropped the link) */
    const char *was = loop_step("a request's data stage");
    alarm(DATA_STAGE_S);
    r = read(i->ep0, buf, want);
    alarm(0);
    loop_step(was);
    int e = r < 0 ? ERRNO : 0;
    if (rt == 0x21 && rq == 0x09) snprintf(note, sizeof note, "SET_REPORT type %u id %u", wv >> 8, wv & 255);
    else if (rt == 0x21 && rq == 0x0a) snprintf(note, sizeof note, "SET_IDLE");
    else if (rt == 0x40 && rq == 0x40) snprintf(note, sizeof note, "Apple charging request");
    else if (rt == 0x40 && rq == 0x51) snprintf(note, sizeof note, "Apple role switch (wired CarPlay), value %u", wv);
    /* the car's bare acknowledgement (one a position update, twice a second while a song plays): counted in the link's
     * health line, not logged */
    if (!(iap2_mode && rt == 0x21 && rq == 0x09 && r > 0 && (unsigned)r == want && mono_ms() - at < stall_ms &&
          iap2_bare_ack(buf, (unsigned)r))) {
        iap2_hex(h, sizeof h, buf, r > 0 ? (unsigned)r : 0);
        snprintf(msg, sizeof msg, "SETUP %02x %02x %04x %04x %04x -> read %d (errno %d, %ld ms)%s%s%s%s", rt, rq, wv, wi,
                 wl, r, e, mono_ms() - at, note[0] ? ": " : "", note, r > 0 ? " data " : "", r > 0 ? h : "");
        logline(i->name, msg);
    }
    if (rt == 0x21 && rq == 0x09 && r >= 2 + (int)sizeof iap2_probe && !memcmp(buf + 2, iap2_probe, sizeof iap2_probe))
        apple_seen(2, "iAP2's detect");
    if (iap2_mode && i->kind == K_HID && rt == 0x21 && rq == 0x09 && r <= 0)
        logline(i->name, "SET_REPORT: no data stage (the car gave the transfer up, or the read's 2 s ran out): nothing "
                         "taken, the loop goes on");
    if (iap2_mode && i->kind == K_HID && rt == 0x21 && rq == 0x09 && r > 0) {
        /* A data stage shorter than asked, or not of the report named: on 2026-11-21 (q2muse-1125) a SET_REPORT of 14
         * bytes came back after a 12.3 s stall as 8, 01 0b 00 00 01 00 00 00, the car's next request (SET_INTERFACE)
         * read as data: the car had given the transfer up, its next request was never answered, and it sent nothing
         * more. Not iAP2's bytes, and the control pipe out of step with the car: the link lost (the iPhone shown
         * again, io_link_lost). */
        if (seen && ((unsigned)r != want || buf[0] != (wv & 255))) {
            logline(i->name, "SET_REPORT: not the report asked for (the car gave the transfer up)");
            iap2_lost("link: lost (a request's data stage was not its report)");
            return;
        }
        iap2_report(buf, (unsigned)r);
        return;
    }
    if (rt == 0x21 && rq == 0x09 && r >= 2 + (int)sizeof iap2_probe && !memcmp(buf + 2, iap2_probe, sizeof iap2_probe)) {
        if (echo_on()) echo_report(i, iap2_probe, sizeof iap2_probe);
        else logline(i->name, "detect seen: not echoed (echo off in this session)");
    }
}

static void event(struct inst *i, const unsigned char *e) {
    int type = e[8];
    char msg[160];
    if (state_file && host_at < 0 && (type == EV_SETUP || type == EV_ENABLE)) { /* a request: the host is there */
        host_at = boot_ms();
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
        if (disabled_at < 0) disabled_at = boot_ms();
    } else if (type == EV_RESUME) {
        disabled_at = -1;
    }
}

/* A sleep: the boot clock gone further than the monotonic one since the last pass (the Q2 was suspended that long,
 * SLEEP_MS or more). Said in the log, and a link that was up is lost: the car on the other end has long given it up,
 * and a car that re-enumerates at the wake starts a session afresh (uac1-power-app.md #6). */
#define SLEEP_MS 1000
static void sleep_poll(void) {
    static long apart, seen_apart;
    if (clock_file) {
        char b[24];
        if (read_some(clock_file, b, sizeof b) > 0) clock_skew = atol(b);
    }
    long d = boot_ms() - mono_ms();
    if (!seen_apart) seen_apart = 1, apart = d;
    if (d - apart < SLEEP_MS) return;
    char msg[96];
    snprintf(msg, sizeof msg, "suspend: the Q2 slept %ld ms", d - apart);
    logline("probe", msg);
    if (iap2_mode && link_up) {
        snprintf(msg, sizeof msg, "link: lost (the Q2 slept %ld s)", (d - apart) / 1000);
        iap2_lost(msg);
    }
    apart = d;
}

/* How long the loop's poll may sleep (iAP2), in ms: until the soonest of iAP2's deadlines (iap2_link.c iap2_due), the
 * rate file's look, the host's verdict (the controller's state every 100 ms until the host has addressed the Q2, the
 * window after), the audio's health, the session's gap and --for's end; at most LOOP_MAX_MS, a guard. It was 10 ms
 * whatever was due: 6,000 wakeups a minute on a charger overnight (car-bt-power.md #6). With --clock-file at most
 * 100 ms, so a sleep the tests stand in for is seen. */
#define LOOP_MAX_MS 1000
static long loop_due(long end) {
    long now = boot_ms(), w = iap2_due(now), d;
#define DUE(at)                                                                                                       \
    (d = (at) - now, d = d < 0 ? 0 : d, w = w < 0 || d < w ? d : w)
    if (rate_every() >= 0) DUE(rate_at + rate_every());
    if (state_file && !leave && host_at < 0 && udc_file) DUE(udc_at + 100);
    if (state_file && !leave && verdict == V_WAIT && host_at >= 0) DUE(host_at + (config2 ? car_ms : auto_ms) + 1);
    if (state_file && (audio_on || flowing || *trouble)) DUE(health_at + HEALTH_MS);
    if (active && disabled_at >= 0) DUE(disabled_at + gap_ms + 1);
    if (end) DUE(end);
    if (artw.fd >= 0 || lload.fd >= 0) DUE(now + 250); /* a child's end is a hang-up poll sees; a guard all the same */
#undef DUE
    if (w < 0 || w > LOOP_MAX_MS) w = LOOP_MAX_MS;
    if (clock_file && w > 100) w = 100;
    return w;
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
        else if (!strcmp(o, "--relink-file")) relink_file = v, ++a;
        else if (!strcmp(o, "--art-made")) art_made = v, ++a;
        else if (!strcmp(o, "--ffmpeg")) art_ffmpeg = v, ++a;
        else if (!strcmp(o, "--ask-dir")) ask_dir = v, ++a;
        else if (!strcmp(o, "--art-ask-ms")) art_ask_ms = atoi(v), ++a;
        else if (!strcmp(o, "--cmd-file")) cmd_file = v, ++a;
        else if (!strcmp(o, "--cmdq-file")) cmdq_file = v, ++a;
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
        else if (!strcmp(o, "--health-ms")) health_ms = atoi(v), ++a;
        else if (!strcmp(o, "--stall-ms")) stall_ms = atoi(v), ++a;
        else if (!strcmp(o, "--clock-file")) clock_file = v, ++a;
        else if (!strcmp(o, "--log-cap")) log_trim = strtoul(v, 0, 10), ++a;
        else if (!strcmp(o, "--card-wait-ms")) card_wait_ms = atoi(v), ++a;
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
    logfd = open(argv[a++], O_WRONLY | O_CREAT | O_APPEND | O_CLOEXEC, 0644);
    if (logfd < 0) logfd = 2;
    signal(SIGCHLD, SIG_IGN); /* children reaped by the kernel, the log's writer first among them */
    log_start();
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
        i->ep0 = open(path, O_RDWR | O_CLOEXEC);
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
    if (iap2_mode) sid_index_start(); /* the server songs' index, made while the car identifies (q2_iap_lib.c) */
    if (iap2_mode) iap2_connect(&io);
    long end = for_s > 0 ? boot_ms() + for_s * 1000L : 0;
    int rc = 0;
    started_at = boot_ms();
    state_write();
    while (!stopping && !leave) {
        struct pollfd p[MAX_INST + 4];
        int np = 0;
        for (int k = 0; k < ninst; ++k) p[np].fd = in[k].ep0, p[np].events = POLLIN, p[np++].revents = 0;
        /* the writer's reports gone and the children's bytes wake it too (loop_due) */
        int more[4] = { done_fd, lload.fd, artw.fd, sids.fd };
        for (int k = 0; iap2_mode && k < 4; ++k)
            if (more[k] >= 0) p[np].fd = more[k], p[np].events = POLLIN, p[np++].revents = 0;
        int n = poll(p, (unsigned long)np, iap2_mode ? loop_due(end) : 500);
        loop_begin();
        if (n < 0 && ERRNO != EINTR) {
            snprintf(msg, sizeof msg, "poll failed, errno %d", ERRNO);
            logline("probe", msg), rc = 1;
            break;
        }
        for (int k = 0; n > 0 && k < ninst; ++k) {
            if (!(p[k].revents & POLLIN)) continue;
            unsigned char e[12];
            loop_step("an ep0 event's read");
            int r = read(in[k].ep0, e, sizeof e); /* one event at a time: a SETUP must be answered first */
            loop_step("an ep0 event");
            if (r == (int)sizeof e) event(&in[k], e);
            else {
                snprintf(msg, sizeof msg, "ep0: read %d of an event's 12 bytes (errno %d): not an event, dropped", r,
                         r < 0 ? ERRNO : 0);
                logline(in[k].name, msg);
            }
        }
        loop_step("the writer's reports");
        if (iap2_mode) io_backlog(); /* its pipe drained each pass, as poll wakes for it */
        loop_step("the clocks");
        sleep_poll();
        loop_step("the rate file");
        if (iap2_mode) rate_poll(boot_ms());
        loop_step("iAP2's tick");
        if (iap2_mode) iap2_tick();
        long now = boot_ms();
        loop_step("the cover's making");
        if (iap2_mode) art_poll();
        loop_step("the library's read");
        if (iap2_mode) lib_load_poll(); /* its child's bytes, whether or not the car still waits for them */
        if (iap2_mode) sid_index_poll(); /* the stream-path index's child's, so it never waits on a full pipe */
        loop_step("the host's verdict");
        host_poll(now);
        loop_step("the audio's health");
        health_poll(now);
        loop_end();
        if (active && disabled_at >= 0 && now - disabled_at > gap_ms) {
            active = 0, disabled_at = -1;
            snprintf(msg, sizeof msg, "session %d ended (no ENABLE within %ld ms of the DISABLE)", session, gap_ms);
            logline("probe", msg);
            summary();
            if (iap2_mode) iap2_session_end(); /* the car's file and the link with it (uac1-power-app.md #19) */
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
    if (lload.pid > 0) kill(lload.pid, SIGTERM);
    if (artw.pid > 0) kill(artw.pid, SIGTERM); /* it kills and reaps its ffmpeg */
    for (int k = 0; k < ninst; ++k) close(in[k].ep0); /* first: a relink waits on them, not on the card */
    log_end(); /* the log's writer syncs the card's file */
    return rc;
}
