/* q2-usbtrace's device side (q2usbtrace.h, docs/internals.md#usb-tracer): it opens what it watches, waits on all
 * of it in one poll() and hands each value to q2usbtrace_core.c. It writes to no device: /dev/mem is opened
 * O_RDONLY and mapped PROT_READ, GPIO lines are asked for only as input event requests (never a line handle, which
 * could drive one), and the PMIC is asked only SMBus byte reads; its own files are the card's trace, a line now
 * and then in demo's --dev console log, and the debugfs mount it makes when the dwc2's regdump is not there.
 *
 * PD17 belongs to demo (patch/usb_event.c holds its edges as q2-usb-cable, so a second request would fail, and
 * taking it first would leave demo on stock's once-a-second poll): its level is read from the GPIO register
 * at each sample, and in a --dev build demo sends each edge it reads, with the kernel's time, to the abstract
 * datagram socket UT_EDGES_NAME, where this program listens. PD18 and PD19 are unclaimed and taken here.
 *
 * Every device call is one of the t_* functions, defined at the end for the device; the host test gives its
 * own (Q2USBTRACE_HOST, test/unit/q2usbtrace_host.c) and checks each call they were asked. */
#include "q2usbtrace.h"
#include "ulog_line.h"

#define MS 1000000LL

static struct trace tr;
static struct {
    int line[3];               /* the event fds of PD17 (never taken), PD18, PD19; -1 none */
    long long line_retry[3];   /* a re-request's earliest time after the last */
    int edges, uev, udc, i2c;  /* -1 none */
    const volatile unsigned *cpm, *gpio;
    int regdump;
    long long slept_base;      /* CLOCK_BOOTTIME - CLOCK_MONOTONIC at the last look */
    int flush_pid;
    unsigned flush_len, flush_dropped;
} d;
static int in_writer;     /* true in the flush's child: card_store.c writes then */
static volatile int term; /* SIGTERM: write out and end */
int card_mounted(void) { return in_writer; }

/* ---- reads ---- */
static int read_text(const char *path, char *b, int size) {
    b[0] = 0;
    int fd = t_open(path, O_RDONLY);
    if (fd < 0) return -1;
    int n = t_read(fd, b, size - 1);
    t_close(fd);
    if (n < 0) return -1;
    b[n] = 0;
    return n;
}
static void read_word(const char *path, char *w, int size) { /* the file's first word, "" when unreadable */
    char b[64];
    w[0] = 0;
    if (read_text(path, b, sizeof b) <= 0) return;
    int i = 0;
    while (b[i] && b[i] != ' ' && b[i] != '\n' && i < size - 1) w[i] = b[i], ++i;
    w[i] = 0;
}
static int read_num(const char *path) {
    char w[16];
    read_word(path, w, sizeof w);
    return w[0] >= '0' && w[0] <= '9' ? atoi(w) : -1;
}
static int pin(unsigned port, int bit) {
    return d.gpio ? (int)(d.gpio[(port + UT_PIN) / 4] >> bit & 1) : -1;
}

static void sample(long long now) {
    struct ut_sample s;
    s.pd17 = pin(UT_PORT_D, UT_PD17), s.pd18 = pin(UT_PORT_D, UT_PD18), s.pd19 = pin(UT_PORT_D, UT_PD19);
    s.pd14 = pin(UT_PORT_D, UT_PD14), s.pd15 = pin(UT_PORT_D, UT_PD14 + 1), s.pd16 = pin(UT_PORT_D, UT_PD14 + 2);
    s.pc20 = pin(UT_PORT_C, UT_PC20), s.pe22 = pin(UT_PORT_E, UT_PE22);
    s.online = read_num(UT_ONLINE);
    read_word(UT_OTG "/id_status", s.id, sizeof s.id);
    read_word(UT_OTG "/dwc2_mode", s.mode, sizeof s.mode);
    read_word(UT_OTG "/connected", s.conn, sizeof s.conn);
    s.dev = t_exists(UT_HOSTDEV);
    char cards[512];
    s.snd = read_text(UT_CARDS, cards, sizeof cards) < 0 ? -1 : ut_cards_second(cards);
    s.pcr = d.cpm ? (long)d.cpm[UT_USBPCR / 4] : -1;
    s.rdt = d.cpm ? (long)d.cpm[UT_USBRDT / 4] : -1;
    trace_sample(&tr, &s, now);
}

static void slow(long long now) {
    struct ut_slow s;
    for (int i = 0; i < UT_AXP_REGS; ++i) {
        unsigned char data[34] = { 0 };
        struct ut_smbus_args a;
        ut_smbus_read(&a, ut_axp_regs[i], data);
        s.axp[i] = d.i2c >= 0 && t_ioctl(d.i2c, UT_I2C_SMBUS, &a) >= 0 ? data[0] : -1;
    }
    s.gotgctl = s.gintsts = s.hprt0 = -1;
    if (d.regdump) {
        static char dump[16384];
        if (read_text(UT_REGDUMP, dump, sizeof dump) > 0) {
            ut_regdump_word(dump, "GOTGCTL", &s.gotgctl);
            ut_regdump_word(dump, "GINTSTS", &s.gintsts);
            ut_regdump_word(dump, "HPRT0", &s.hprt0);
        }
    }
    trace_slow(&tr, &s, now);
}

/* At an edge: the PMIC asked at once (and 50 and 250 ms on, from the loop), its registers and the regdump. */
static void edge_reads(long long now) {
    trace_online(&tr, read_num(UT_ONLINE), now);
    slow(now);
}

static void udc_read(long long now) {
    char b[32];
    if (d.udc < 0) return;
    int n = t_reread(d.udc, b, sizeof b - 1);
    if (n <= 0) return;
    b[n] = 0;
    for (int i = 0; i < n; ++i)
        if (b[i] == '\n' || b[i] == ' ') b[i] = 0;
    trace_udc(&tr, b, now);
}

/* ---- the lines ---- */
static int request(unsigned line) {
    int chip = t_open(UT_GPIOCHIP, O_RDONLY);
    if (chip < 0) return -1;
    struct ut_event_request r;
    ut_event_request(&r, line);
    int ok = t_ioctl(chip, UT_GPIO_GET_LINEEVENT, &r) >= 0 && r.fd >= 0;
    t_close(chip);
    return ok ? r.fd : -1;
}
/* A line asked for again when its interrupt bit is clear: demo's `cmd_gpio set_func PD18 input pull_up` (at
 * boot and every wake, usb_record.c) sets the pin up afresh through soc_gpio, which the kernel's line request
 * does not see, and no edge comes after it. */
static void lines_check(long long now) {
    if (!d.gpio) return;
    unsigned ints = d.gpio[(UT_PORT_D + UT_INT) / 4];
    for (int i = 1; i < 3; ++i) {
        unsigned bit = (unsigned)(UT_PD17 + i);
        if (d.line[i] < 0 || ints >> bit & 1 || now < d.line_retry[i]) continue;
        t_close(d.line[i]);
        d.line[i] = request(bit);
        d.line_retry[i] = now + 5000 * MS;
        trace_line(&tr, now, "note PD%u's events asked for again (its interrupt bit was clear)%s", bit,
                   d.line[i] < 0 ? ": refused" : "");
    }
}

static void edge_line(int i, long long now) {
    struct ut_event_data e;
    if (t_read(d.line[i], &e, sizeof e) != (int)sizeof e) return;
    trace_edge(&tr, UT_PD17 + i, e.id == UT_EDGE_RISING, (long long)e.timestamp, "");
    edge_reads(now);
}
static void edge_forwarded(long long now) {
    struct ut_event_data e;
    while (t_recv(d.edges, &e, sizeof e) == (int)sizeof e) {
        trace_edge(&tr, UT_PD17, e.id == UT_EDGE_RISING, (long long)e.timestamp, "fwd");
        edge_reads(now);
    }
}
static void uevents(long long now) {
    static char msg[2048];
    int n;
    while ((n = t_recv(d.uev, msg, sizeof msg - 1)) > 0) {
        msg[n] = 0;
        trace_uevent(&tr, msg, n, now);
    }
}

/* ---- the card ---- */
/* The child's part: the folder, the move of a full .0 to .1, the ring appended (card_store.c). */
static int flush_child(void) {
    in_writer = 1;
    mkdir(PEQ_ROOT "/mnt/mmc/.q2muse", 0755);
    mkdir(PEQ_ROOT UT_LOGS, 0755);
    void *f = fopen(tr.path0, "rb");
    long size = f && !fseek(f, 0, 2) ? ftell(f) : 0;
    if (f) fclose(f);
    if (trace_rotate_needed(size)) rename(tr.path0, tr.path1);
    return card_log_flush(&tr.log) ? 0 : 1;
}
static void written(int ok) {
    if (ok && tr.log.dropped == d.flush_dropped) {
        memmove(tr.log.ring, tr.log.ring + d.flush_len, tr.log.len - d.flush_len);
        tr.log.len -= d.flush_len;
        tr.log.dropped = 0;
    }
    d.flush_pid = 0;
}
static void flush_reap(void) {
#ifndef Q2USBTRACE_HOST
    int status = 0;
    if (d.flush_pid <= 0 || waitpid(d.flush_pid, &status, WNOHANG) != d.flush_pid) return;
    written(!(status & 0x7f) && !(status >> 8 & 0xff));
#endif
}
/* A child writes, so a card that hangs never stops the watching; the lines it was given are dropped from the
 * ring once it says they are on the card. */
static void flush(long long now, int force) {
    flush_reap();
    if (d.flush_pid > 0 || (!tr.log.len && !tr.log.dropped)) return;
    if (!force && now < tr.next_flush && tr.log.len < UT_FLUSH_BYTES) return;
    tr.next_flush = now + UT_FLUSH_MS * MS;
    if (!t_card_ready()) return;
    d.flush_len = tr.log.len, d.flush_dropped = tr.log.dropped;
#ifdef Q2USBTRACE_HOST
    unsigned len = tr.log.len, dropped = tr.log.dropped;
    int ok = !flush_child();
    in_writer = 0, tr.log.len = len, tr.log.dropped = dropped; /* the child's changes are its own */
    d.flush_pid = 1;
    written(ok);
#else
    int pid = fork();
    if (!pid) _exit(flush_child());
    d.flush_pid = pid > 0 ? pid : 0;
#endif
}

void ut_ulog(struct trace *t, char level, const char *msg) {
    char line[320];
    int n = ulog_line(line, sizeof line, t_wall(), (unsigned)(t_now(CLOCK_MONOTONIC) / MS), level, "usbtrace", msg,
                      (int)strlen(msg));
    t_append(UT_ULOG, line, n);
}

static void on_term(int sig) { term = 1; }

static int stop_asked(void) { return t_exists(UT_STOP_FLASH) || t_exists(UT_STOP_CARD); }

/* ---- the loop ---- */
int q2usbtrace_main(int argc, char **argv) {
    if (stop_asked()) return 0;
    memset(&d, 0, sizeof d);
    d.line[0] = d.line[1] = d.line[2] = d.edges = d.uev = d.udc = d.i2c = -1;
    term = 0;
    long long now = t_now(CLOCK_MONOTONIC);
    trace_init(&tr, PEQ_ROOT UT_LOGS, now);
    int mem = t_open(UT_MEM, O_RDONLY | UT_O_SYNC);
    if (mem >= 0) {
        d.cpm = t_mmap(4096, mem, UT_CPM_PAGE);
        d.gpio = t_mmap(4096, mem, UT_GPIO_PAGE);
        t_close(mem);
    }
    d.line[1] = request(UT_PD18), d.line[2] = request(UT_PD19);
    struct {
        unsigned short family;
        char path[108];
    } un;
    memset(&un, 0, sizeof un);
    un.family = AF_UNIX;
    memcpy(un.path + 1, UT_EDGES_NAME, sizeof UT_EDGES_NAME - 1);
    d.edges = t_socket(AF_UNIX, UT_SOCK_DGRAM | SOCK_CLOEXEC | SOCK_NONBLOCK, 0);
    if (d.edges >= 0 && t_bind(d.edges, &un, 2 + 1 + sizeof UT_EDGES_NAME - 1) < 0) t_close(d.edges), d.edges = -1;
    struct {
        unsigned short family, pad;
        unsigned pid, groups;
    } nl = { UT_AF_NETLINK, 0, 0, 1 };
    d.uev = t_socket(UT_AF_NETLINK, UT_SOCK_DGRAM | SOCK_CLOEXEC | SOCK_NONBLOCK, UT_NETLINK_KOBJECT_UEVENT);
    if (d.uev >= 0 && t_bind(d.uev, &nl, sizeof nl) < 0) t_close(d.uev), d.uev = -1;
    d.udc = t_open(UT_UDC_STATE, O_RDONLY);
    d.i2c = t_open(UT_I2C, O_RDONLY);
    if (d.i2c >= 0 && t_ioctl(d.i2c, UT_I2C_SLAVE_FORCE, (void *)UT_I2C_ADDR) < 0) t_close(d.i2c), d.i2c = -1;
    if (!t_exists(UT_REGDUMP)) t_mount_debugfs();
    d.regdump = t_exists(UT_REGDUMP);
    d.slept_base = t_now(CLOCK_BOOTTIME) - t_now(CLOCK_MONOTONIC);
    signal(SIGTERM, on_term);
    trace_line(&tr, now, "start pd17=%s pd18=%s pd19=%s mem=%s i2c=%s regdump=%s uevent=%s udc=%s wall=%ld",
               d.edges >= 0 ? "forwarded" : "sampled", d.line[1] >= 0 ? "edges" : "sampled",
               d.line[2] >= 0 ? "edges" : "sampled", d.gpio && d.cpm ? "ok" : "none", d.i2c >= 0 ? "ok" : "none",
               d.regdump ? "ok" : "none", d.uev >= 0 ? "ok" : "none", d.udc >= 0 ? "ok" : "none", t_wall());
    udc_read(now);
    sample(now);
    slow(now);
    for (;;) {
        now = t_now(CLOCK_MONOTONIC);
        long long slept = t_now(CLOCK_BOOTTIME) - now - d.slept_base;
        if (slept > UT_WAKE_MS * MS) {
            d.slept_base += slept;
            trace_wake(&tr, slept / MS, now);
            d.line_retry[1] = d.line_retry[2] = 0;
            udc_read(now);
        }
        long long wake = trace_next_wake(&tr);
        int timeout = wake <= now ? 0 : (int)((wake - now + MS - 1) / MS);
        if (timeout > UT_IDLE_MS) timeout = UT_IDLE_MS;
        struct pollfd p[5];
        int src[5], n = 0;
        for (int i = 1; i < 3; ++i)
            if (d.line[i] >= 0) p[n].fd = d.line[i], p[n].events = POLLIN, src[n++] = i;
        if (d.edges >= 0) p[n].fd = d.edges, p[n].events = POLLIN, src[n++] = 3;
        if (d.uev >= 0) p[n].fd = d.uev, p[n].events = POLLIN, src[n++] = 4;
        if (d.udc >= 0) p[n].fd = d.udc, p[n].events = UT_POLLPRI | UT_POLLERR, src[n++] = 5;
        for (int i = 0; i < n; ++i) p[i].revents = 0;
        int ready = t_poll(p, n, timeout);
        now = t_now(CLOCK_MONOTONIC);
        for (int i = 0; ready > 0 && i < n; ++i) {
            if (!p[i].revents) continue;
            if (src[i] < 3) edge_line(src[i], now);
            else if (src[i] == 3) edge_forwarded(now);
            else if (src[i] == 4) uevents(now);
            else udc_read(now);
        }
        if (tr.online_at1 != UF_NEVER && now >= tr.online_at1) {
            tr.online_at1 = UF_NEVER;
            edge_reads(now); /* online, and the PMIC's 00h and 49h again (the second USB study's 50 ms) */
        }
        if (tr.online_at2 != UF_NEVER && now >= tr.online_at2) {
            tr.online_at2 = UF_NEVER;
            trace_online(&tr, read_num(UT_ONLINE), now);
        }
        if (now >= tr.next_sample) {
            int burst = now < tr.burst_until;
            sample(now);
            if (burst && tr.samples_in_burst % UT_SLOW_EVERY == 0) slow(now);
            lines_check(now);
            if (stop_asked() || term) {
                trace_line(&tr, now, "stop %s", term ? "SIGTERM" : "flag");
                trace_tick(&tr, now);
                for (int i = 0; i < 30 && d.flush_pid > 0; ++i) usleep(100000), flush_reap();
                flush(now, 1);
                for (int i = 0; i < 30 && d.flush_pid > 0; ++i) usleep(100000), flush_reap();
                return 0;
            }
        }
        trace_tick(&tr, now);
        flush(now, 0);
    }
}

#ifndef Q2USBTRACE_HOST
/* ---- the device's calls ---- */
int t_open(const char *path, int flags) { return open(path, flags | O_CLOEXEC); }
int t_close(int fd) { return close(fd); }
int t_read(int fd, void *buf, int n) { return (int)read(fd, buf, (size_t)n); }
int t_reread(int fd, void *buf, int n) { /* a sysfs attribute read again from its start, which re-arms its poll */
    lseek(fd, 0, SEEK_SET);
    return (int)read(fd, buf, (size_t)n);
}
int t_ioctl(int fd, unsigned long req, void *arg) { return ioctl(fd, req, arg); }
int t_poll(struct pollfd *p, int n, int timeout_ms) { return poll(p, (unsigned long)n, timeout_ms); }
void *t_mmap(unsigned len, int fd, unsigned long off) {
    void *m = mmap(0, len, UT_PROT_READ, UT_MAP_SHARED, fd, (long)off);
    return m == (void *)-1 ? 0 : m;
}
int t_socket(int domain, int type, int protocol) { return socket(domain, type, protocol); }
int t_bind(int fd, const void *addr, unsigned len) { return bind(fd, addr, len); }
int t_recv(int fd, void *buf, int n) { return (int)recv(fd, buf, (size_t)n, 0x40 /* MSG_DONTWAIT */); }
int t_mount_debugfs(void) { return mount("debugfs", UT_DEBUGFS, "debugfs", 0, 0); }
long long t_now(int clock) {
    struct timespec t;
    clock_gettime(clock, &t);
    return (long long)t.tv_sec * 1000000000LL + t.tv_nsec;
}
long t_wall(void) { return time(0); }
int t_exists(const char *path) { return !access(path, F_OK); }
int t_append(const char *path, const char *s, int n) { /* no O_CREAT: only to demo's log where it is */
    int fd = open(path, O_WRONLY | O_APPEND | O_CLOEXEC);
    if (fd < 0) return -1;
    int w = (int)write(fd, s, (size_t)n);
    close(fd);
    return w;
}
static int hold_live(const char *path) { /* a pid file whose process is alive */
    char b[16];
    if (read_text(path, b, sizeof b) <= 0) return 0;
    int pid = atoi(b);
    return pid > 1 && !kill(pid, 0);
}
/* the card mounted read-write, not being let go of (card_release.c), not lent to a computer (a storage
 * gadget's LUN with a file), as q2guard.c card_ready asks */
int t_card_ready(void) {
    static char b[4096];
    if (hold_live(UT_CARD_HOLD)) return 0;
    if (read_text("/proc/mounts", b, sizeof b) <= 0) return 0;
    const char *m = strstr(b, " /mnt/mmc ");
    if (!m || !(m = strchr(m + 10, ' ')) || strncmp(m + 1, "rw", 2)) return 0;
    void *g = opendir("/sys/kernel/config/usb_gadget");
    int busy = 0;
    for (struct dirent *e; g && !busy && (e = readdir(g));) {
        if (e->d_name[0] == '.') continue;
        char p[200];
        snprintf(p, sizeof p, "/sys/kernel/config/usb_gadget/%s/functions", e->d_name);
        void *f = opendir(p);
        for (struct dirent *x; f && !busy && (x = readdir(f));) {
            if (strncmp(x->d_name, "mass_storage", 12)) continue;
            char w[8];
            snprintf(p, sizeof p, "/sys/kernel/config/usb_gadget/%s/functions/%s/lun.0/file", e->d_name,
                     x->d_name);
            read_word(p, w, sizeof w);
            busy = w[0] != 0;
        }
        if (f) closedir(f);
    }
    if (g) closedir(g);
    return !busy;
}

int main(int argc, char **argv) {
    nice(19); /* the lowest priority: the kernel stamps the edges, so a late sample is all it costs */
    return q2usbtrace_main(argc, argv);
}
#endif
