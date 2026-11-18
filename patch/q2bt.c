/* q2bt's device side (docs/internals.md#bluetooth-link-monitor): q2guard sleeps in bt_sleep, which
 * reads the controller's byte counter at each of q2guard's ticks and, while an A2DP stream runs,
 * wakes every BT_TICK_MS to write a line to the q2-bt log. Each line costs one ioctl, four HCI
 * commands on a raw socket bound only while the stream runs, and a few small /proc and /sys reads;
 * bluealsa is asked for the codec (dbus-send, in the background) at the start and every BT_PCMS_MS.
 * Nothing here changes how the Q2 streams. Every read that fails leaves its field "-", and with no
 * Bluetooth socket at all (the emulator) it tries again a minute later and does nothing else. */
#include "q2bt.h"

#define PCMS_FILE "/tmp/q2bt.pcms"
#define PCMS_CMD                                                                                   \
    "dbus-send --system --print-reply --dest=org.bluealsa /org/bluealsa "                         \
    "org.bluealsa.Manager1.GetPCMs > " PCMS_FILE " 2>/dev/null"
#define ANSWER_MS 150   /* the four answers are waited for this long at most */
#define RETRY_MS 60000  /* a Bluetooth socket that would not open is tried again after this */
#define FIND_MS 10000   /* bluealsa looked for again, while not found, after this */
#define WLAN "/sys/class/net/wlan0/statistics/"

static struct {
    card_log *log;
    int ctl, raw;        /* the HCI socket the ioctls use; the raw one, bound while a stream runs */
    int tried;           /* ctl has been tried; failed_at its last failure */
    unsigned failed_at;
    struct bt_detect det;
    struct bt_counts was; /* the counts at the last line, and when (ms) */
    unsigned at;
    struct bt_link link;
    int ba;              /* bluealsa's pid, 0 not found (looked for at found_at) */
    unsigned found_at, ba_ticks, own_ticks, wtx, wrx;
    int wlan;            /* wtx and wrx were read */
    struct cpu cpu;
    int pcms_pid;
    unsigned pcms_at;
    char codec[24];
    unsigned hz;
} bt;

static void note(const char *what) {
    char b[160];
    heat_note(b, sizeof b, time(0), what);
    heat_add(bt.log, b);
}

/* ---- the controller ---- */
static int ctl_ok(unsigned now) {
    if (bt.ctl >= 0) return 1;
    if (bt.tried && now - bt.failed_at < RETRY_MS) return 0;
    bt.tried = 1;
    bt.ctl = socket(AF_BLUETOOTH, SOCK_RAW | SOCK_CLOEXEC, BTPROTO_HCI);
    if (bt.ctl < 0) bt.failed_at = now;
    return bt.ctl >= 0;
}
static int counts(struct bt_counts *c) {
    unsigned di[BT_DEVINFO_SIZE / 4] = { 0 }; /* dev_id 0: hci0 */
    if (ioctl(bt.ctl, HCIGETDEVINFO, di)) return 0;
    bt_devinfo((const unsigned char *)di, c);
    return 1;
}
static int acl_handle(void) {
    unsigned req[1 + 4 * 4] = { 0 }; /* dev_id 0, room for four links */
    ((unsigned short *)req)[1] = 4;
    if (ioctl(bt.ctl, HCIGETCONNLIST, req)) return -1;
    return bt_acl_handle((const unsigned char *)req, 4);
}
/* bound to hci0's raw channel, Command Complete events only */
static void raw_open(void) {
    unsigned short addr[3] = { AF_BLUETOOTH, 0, 0 }; /* sockaddr_hci: family, hci0, HCI_CHANNEL_RAW */
    unsigned filter[4] = { 1u << 4, 1u << 0x0e, 0, 0 }; /* hci_filter: event packets; event 0x0e; any opcode */
    bt.raw = socket(AF_BLUETOOTH, SOCK_RAW | SOCK_CLOEXEC, BTPROTO_HCI);
    if (bt.raw < 0) return;
    if (bind(bt.raw, addr, sizeof addr) || setsockopt(bt.raw, 0 /* SOL_HCI */, 2 /* HCI_FILTER */, filter, 14))
        close(bt.raw), bt.raw = -1;
}
static int answers(int ms) { /* events until the four have answered or ms is up */
    unsigned char b[64];
    unsigned end = now_ms() + (unsigned)ms;
    for (;;) {
        int left = (int)(end - now_ms());
        struct pollfd p = { bt.raw, POLLIN, 0 };
        if (left < 0) left = 0;
        if (poll(&p, 1, left) <= 0) return 0;
        int n = (int)read(bt.raw, b, sizeof b);
        if (n <= 0) return 0;
        bt_event(&bt.link, b, n);
        if (bt.link.answered >= 4) return 1;
    }
}
static void link_read(void) {
    static const int ops[4] = { BT_OP_RSSI, BT_OP_LQ, BT_OP_TPL, BT_OP_AFH };
    unsigned char c[8];
    if (bt.link.handle < 0) bt.link.handle = acl_handle();
    int h = bt.link.handle;
    bt_link_clear(&bt.link, h);
    if (bt.raw < 0 || h < 0) return;
    answers(0); /* last second's late answers */
    bt_link_clear(&bt.link, h);
    for (int i = 0; i < 4; ++i) write(bt.raw, c, (size_t)bt_cmd(c, ops[i], h));
    answers(ANSWER_MS);
}

/* ---- processes and Wi-Fi ---- */
static unsigned ticks_of(const char *stat_path, int *ok) { /* utime + stime */
    char b[512];
    *ok = 0;
    if (slurp(stat_path, b, sizeof b) <= 0) return 0;
    char *close_ = 0;
    for (char *p = b; *p; ++p)
        if (*p == ')') close_ = p;
    if (!close_) return 0;
    unsigned f[13] = { 0 }; /* fields 3.. after the comm */
    const char *s = close_ + 2;
    for (int i = 0; i < 13 && *s; ++i) {
        while (*s && *s != ' ') {
            if (*s >= '0' && *s <= '9') f[i] = f[i] * 10 + (unsigned)(*s - '0');
            ++s;
        }
        while (*s == ' ') ++s;
    }
    *ok = 1;
    return f[11] + f[12]; /* fields 14 and 15 */
}
static int find_bluealsa(void) {
    void *d = opendir("/proc");
    int pid = 0;
    if (!d) return 0;
    for (struct dirent *e; !pid && (e = readdir(d));) {
        char p[40], comm[24];
        if (e->d_name[0] < '1' || e->d_name[0] > '9') continue;
        snprintf(p, sizeof p, "/proc/%s/comm", e->d_name);
        if (slurp(p, comm, sizeof comm) > 0 && !strcmp(comm, "bluealsa\n")) pid = atoi(e->d_name);
    }
    closedir(d);
    return pid;
}
static unsigned ba_ticks(int *ok) {
    char p[40];
    *ok = 0;
    if (!bt.ba) return 0;
    snprintf(p, sizeof p, "/proc/%d/stat", bt.ba);
    return ticks_of(p, ok);
}
static int counter(const char *path, unsigned *v) { /* a /sys counter, modulo 2^32 */
    char b[32];
    if (slurp(path, b, sizeof b) <= 0 || b[0] < '0' || b[0] > '9') return 0;
    unsigned n = 0;
    for (const char *s = b; *s >= '0' && *s <= '9'; ++s) n = n * 10 + (unsigned)(*s - '0');
    *v = n;
    return 1;
}
static int wlan(unsigned *tx, unsigned *rx) {
    return counter(WLAN "tx_bytes", tx) && counter(WLAN "rx_bytes", rx);
}

/* ---- the codec ---- */
static void pcms_start(unsigned now) {
    if (bt.pcms_pid > 0) return;
    bt.pcms_at = now;
    int pid = fork();
    if (!pid) {
        execl("/bin/sh", "sh", "-c", PCMS_CMD, (char *)0);
        _exit(127);
    }
    bt.pcms_pid = pid > 0 ? pid : 0;
}
static void pcms_reap(void) {
    int status = 0;
    if (bt.pcms_pid <= 0 || waitpid(bt.pcms_pid, &status, WNOHANG) != bt.pcms_pid) return;
    bt.pcms_pid = 0;
    char b[4096], codec[24], line[96];
    unsigned hz, delay;
    if (slurp(PCMS_FILE, b, sizeof b) <= 0 || !bt_pcms(b, codec, sizeof codec, &hz, &delay)) return;
    if (!strcmp(codec, bt.codec) && hz == bt.hz) return;
    memcpy(bt.codec, codec, sizeof codec), bt.hz = hz;
    snprintf(line, sizeof line, "CODEC %s %u Hz delay=%u.%u ms", codec, hz, delay / 10, delay % 10);
    note(line);
}

/* ---- a stream ---- */
static void begin(unsigned now, const struct bt_counts *c) {
    int ok, cores;
    char line[96];
    bt.was = *c, bt.at = now;
    bt.link.handle = acl_handle();
    raw_open();
    if (!bt.ba || !(ba_ticks(&ok), ok)) bt.ba = find_bluealsa(), bt.found_at = now;
    bt.ba_ticks = ba_ticks(&ok);
    bt.own_ticks = ticks_of("/proc/self/stat", &ok);
    bt.wlan = wlan(&bt.wtx, &bt.wrx);
    busy_since(&bt.cpu, &cores);
    bt.codec[0] = 0, bt.hz = 0;
    snprintf(line, sizeof line, "START h=%d mtu=%d bufs=%d raw=%s", bt.link.handle, c->acl_mtu, c->acl_pkts,
             bt.raw >= 0 ? "ok" : "none");
    note(line);
    pcms_start(now);
}
static void end(void) {
    note("STOP");
    if (bt.raw >= 0) close(bt.raw), bt.raw = -1;
}
static void sample(unsigned now, const struct bt_counts *c) {
    struct bt_sample s;
    unsigned ms = now - bt.at, tx, rx, t;
    int ok, cores;
    char b[4096], line[256];
    s.wall = time(0), s.up = now / 1000;
    s.kbps = bt_rate(c->byte_tx - bt.was.byte_tx, ms) / 125; /* bytes to kbit */
    s.acl = bt_rate(c->acl_tx - bt.was.acl_tx, ms);
    s.err = (int)(c->err_tx - bt.was.err_tx);
    s.wq = slurp("/proc/net/l2cap", b, sizeof b) > 0 ? bt_l2cap_wmem(b) : -1;
    link_read();
    s.rssi = bt.link.rssi, s.lq = bt.link.lq, s.tpl = bt.link.tpl, s.afh = bt.link.afh;
    s.wtx = s.wrx = -1;
    if (wlan(&tx, &rx)) {
        if (bt.wlan) s.wtx = bt_rate(tx - bt.wtx, ms) / 1024, s.wrx = bt_rate(rx - bt.wrx, ms) / 1024;
        bt.wtx = tx, bt.wrx = rx, bt.wlan = 1;
    } else {
        bt.wlan = 0;
    }
    s.cpu = busy_since(&bt.cpu, &cores);
    t = ba_ticks(&ok);
    s.ba = ok ? bt_ticks_pct(t - bt.ba_ticks, ms) : -1;
    if (ok)
        bt.ba_ticks = t;
    else if (now - bt.found_at >= FIND_MS) /* bluealsa restarted, or not yet found */
        bt.ba = find_bluealsa(), bt.found_at = now, bt.ba_ticks = ba_ticks(&ok);
    t = ticks_of("/proc/self/stat", &ok);
    s.own = ok ? bt_ticks_pct(t - bt.own_ticks, ms) : -1;
    bt.own_ticks = t;
    bt_line(line, sizeof line, &s);
    heat_add(bt.log, line);
    bt.was = *c, bt.at = now;
}
static void tick(unsigned now) {
    struct bt_counts c;
    pcms_reap();
    if (!ctl_ok(now) || !counts(&c)) return;
    int ev = bt_detect_step(&bt.det, now, c.byte_tx);
    if (ev == BT_EV_START) {
        begin(now, &c);
        return;
    }
    if (ev == BT_EV_STOP) {
        end();
        return;
    }
    if (!bt.det.on) return;
    sample(now, &c);
    if (now - bt.pcms_at >= BT_PCMS_MS) pcms_start(now);
}

void bt_init(card_log *l) {
    bt.log = l, bt.ctl = bt.raw = -1;
}
int bt_streaming(void) { return bt.det.on; }

void bt_sleep(unsigned ms) {
    unsigned end_at = now_ms() + ms;
    for (;;) {
        unsigned now = now_ms();
        int left = (int)(end_at - now), step = left;
        if (left <= 0) return;
        if (bt.det.on) { /* the next sample is due a second after the last */
            int due = (int)(bt.at + BT_TICK_MS - now);
            step = due <= 0 ? 0 : due < left ? due : left;
        }
        if (step > 0) usleep((unsigned)step * 1000u);
        now = now_ms();
        if (!bt.det.on || (int)(now - bt.at) >= BT_TICK_MS) tick(now);
    }
}
