/* q2bt's device side (docs/internals.md#bluetooth-link-monitor): q2guard sleeps in bt_sleep, which
 * reads the controller's byte counter at each of q2guard's ticks and, while an A2DP stream runs,
 * wakes every BT_TICK_MS to write a line to the q2-bt log. Each line costs two ioctls, five HCI
 * commands on a raw socket bound only while the stream runs (to the ACL handle listed now: a codec
 * change reconnects on a new one), which also carries the controller's Number Of Completed Packets
 * for each delivered packet, and a few small /proc and /sys reads, bluealsa's encoder
 * thread's schedstat among them; bluealsa is asked for the codec (dbus-send, in the background) at
 * the start, when its encoder thread changes and every BT_PCMS_MS. Nothing here changes how the Q2
 * streams. Every read that fails leaves its field "-", and with no Bluetooth socket at all (the
 * emulator) it tries again a minute later and does nothing else. */
#include "q2bt.h"

#define PCMS_FILE "/tmp/q2bt.pcms"
#define PCMS_CMD                                                                                   \
    "dbus-send --system --print-reply --dest=org.bluealsa /org/bluealsa "                          \
    "org.bluealsa.Manager1.GetPCMs > " PCMS_FILE " 2>/dev/null"
#define ANSWER_MS 150  /* the five answers are waited for this long at most */
#define RETRY_MS 60000 /* a Bluetooth socket that would not open is tried again after this */
#define FIND_MS 10000  /* bluealsa looked for again, while not found, after this */
#define WLAN "/sys/class/net/wlan0/statistics/"

static struct {
    card_log *log;
    int ctl, raw; /* the HCI socket the ioctls use; the raw one, bound while a stream runs */
    int tried;    /* ctl has been tried; failed_at its last failure */
    unsigned failed_at;
    struct bt_detect det;
    struct bt_counts was; /* the counts at the last line, and when (ms) */
    unsigned at;
    struct bt_link link;
    int ba; /* bluealsa's pid, 0 not found (looked for at found_at) */
    unsigned found_at, ba_ticks, own_ticks, wtx, wrx;
    int wlan; /* wtx and wrx were read */
    struct cpu cpu;
    int pcms_pid;
    unsigned pcms_at;
    char codec[24];
    unsigned hz;
    int flush; /* a stream started or ended: the log to the card now */
    int enc; /* bluealsa's encoder thread (its tid), 0 none; its name, and its last run and wait */
    char enc_name[24];
    unsigned enc_run, enc_wait;
    int enc_stat; /* 1 its schedstat read, 2 only its ticks (no schedstat in this kernel), 0 neither
                   */
    unsigned done, done_was; /* completions counted on the raw socket, and at the last line */
    struct bt_fly fly;
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
/* bound to hci0's raw channel: Command Complete (0x0e), for the five answers, and Number Of
 * Completed Packets (0x13), the controller's account of each packet it delivered */
static void raw_open(void) {
    unsigned short addr[3] = { AF_BLUETOOTH, 0,
                               0 }; /* sockaddr_hci: family, hci0, HCI_CHANNEL_RAW */
    /* hci_filter: event packets; events 0x0e and 0x13; any opcode */
    unsigned filter[4] = { 1u << 4, 1u << 0x0e | 1u << 0x13, 0, 0 };
    bt.raw = socket(AF_BLUETOOTH, SOCK_RAW | SOCK_CLOEXEC, BTPROTO_HCI);
    if (bt.raw < 0) return;
    if (bind(bt.raw, addr, sizeof addr) ||
        setsockopt(bt.raw, 0 /* SOL_HCI */, 2 /* HCI_FILTER */, filter, 14))
        close(bt.raw), bt.raw = -1;
}
/* Events until the five have answered or ms is up (0: only what is waiting, all of it), completions
 * counted on the way. 1 when the five answered. */
static int events(int ms, int until_answered) {
    unsigned char b[64];
    unsigned end = now_ms() + (unsigned)ms;
    for (;;) {
        int left = (int)(end - now_ms());
        struct pollfd p = { bt.raw, POLLIN, 0 };
        if (left < 0) left = 0;
        if (poll(&p, 1, left) <= 0) return 0;
        int n = (int)read(bt.raw, b, sizeof b);
        if (n <= 0) return 0;
        bt.done += (unsigned)bt_completed(b, n, bt.link.handle);
        bt_event(&bt.link, b, n);
        if (until_answered && bt.link.answered >= 5) return 1;
    }
}
static void link_read(void) {
    static const int ops[5] = { BT_OP_RSSI, BT_OP_LQ, BT_OP_TPL, BT_OP_AFH, BT_OP_FCC };
    unsigned char c[8];
    char line[32];
    int h = acl_handle(); /* each line: a reconnect (a codec change) brings a new handle */
    if (bt_handle_follow(&bt.link, h)) {
        snprintf(line, sizeof line, "LINK h=%d", h), note(line);
        bt.fly.have = 0; /* a new link's packets: counted afresh */
    }
    bt_link_clear(&bt.link, h);
    if (bt.raw < 0 || h < 0) return;
    events(0, 0); /* the second's completions, and last second's late answers */
    bt_link_clear(&bt.link, h);
    for (int i = 0; i < 5; ++i) write(bt.raw, c, (size_t)bt_cmd(c, ops[i], h));
    events(ANSWER_MS, 1);
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

/* ---- bluealsa's encoder thread ----
 * Its run and run-queue wait (schedstat, in ns) tell an encoder that cannot keep up (on a CPU most
 * of the second) or is kept from one (waiting for it) from one that sleeps, waiting for PCM or for
 * room in the socket. Found by its name, ba-a2dp-<codec>; looked for again when its files go (the
 * thread ends at a codec change), which is also noted, as it names the new codec at once. */
static int enc_read(unsigned *run, unsigned *wait) { /* 1 schedstat, 2 ticks (run only), 0 none */
    char p[64], b[96];
    int ok;
    if (!bt.ba || !bt.enc) return 0;
    snprintf(p, sizeof p, "/proc/%d/task/%d/schedstat", bt.ba, bt.enc);
    if (slurp(p, b, sizeof b) > 0 && bt_schedstat(b, run, wait)) return 1;
    snprintf(p, sizeof p, "/proc/%d/task/%d/stat", bt.ba, bt.enc);
    *run = ticks_of(p, &ok) * 10u, *wait = 0;
    return ok ? 2 : 0;
}
static void enc_find(unsigned now) {
    char p[48], comm[24];
    void *d;
    bt.enc = 0;
    if (!bt.ba) return;
    snprintf(p, sizeof p, "/proc/%d/task", bt.ba);
    if (!(d = opendir(p))) return;
    for (struct dirent *e; !bt.enc && (e = readdir(d));) {
        char c[64];
        if (e->d_name[0] < '1' || e->d_name[0] > '9') continue;
        snprintf(c, sizeof c, "/proc/%d/task/%s/comm", bt.ba, e->d_name);
        if (slurp(c, comm, sizeof comm) > 0 && bt_encoder_thread(comm)) bt.enc = atoi(e->d_name);
    }
    closedir(d);
    if (!bt.enc) return;
    for (char *s = comm; *s; ++s)
        if (*s == '\n') *s = 0;
    bt.enc_stat = enc_read(&bt.enc_run, &bt.enc_wait);
    if (!strcmp(comm, bt.enc_name)) return;
    char line[48];
    memcpy(bt.enc_name, comm, sizeof comm);
    snprintf(line, sizeof line, "ENCODER %s", comm);
    note(line);
    bt.pcms_at = now - BT_PCMS_MS; /* bluealsa asked for the codec at the next line */
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
    bt.codec[0] = 0, bt.hz = 0, bt.enc_name[0] = 0, bt.fly.have = 0;
    enc_find(now);
    snprintf(line, sizeof line, "START h=%d mtu=%d bufs=%d raw=%s", bt.link.handle, c->acl_mtu,
             c->acl_pkts, bt.raw >= 0 ? "ok" : "none");
    note(line);
    pcms_start(now);
    bt.flush = 1;
}
static void end(void) {
    note("STOP");
    bt.flush = 1;
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
        if (bt.wlan)
            s.wtx = bt_rate(tx - bt.wtx, ms) / 1024, s.wrx = bt_rate(rx - bt.wrx, ms) / 1024;
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
    s.h = bt.link.handle;
    s.fcc = bt.link.fcc;
    s.done = s.fly = -1;
    if (bt.raw >= 0 && s.h >= 0) {
        s.done = bt_rate(bt.done - bt.done_was, ms);
        s.fly = bt_fly_step(&bt.fly, c->acl_tx, bt.done);
    }
    bt.done_was = bt.done;
    unsigned run, wait;
    int got = enc_read(&run, &wait);
    if (!got)
        enc_find(now),
            got = 0; /* gone: a codec change, or not found yet; read from the next line */
    s.er = got && got == bt.enc_stat ? bt_rate(run - bt.enc_run, ms) : -1;
    s.ew = got == 1 && bt.enc_stat == 1 ? bt_rate(wait - bt.enc_wait, ms) : -1;
    if (got) bt.enc_run = run, bt.enc_wait = wait, bt.enc_stat = got;
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

void bt_init(card_log *l) { bt.log = l, bt.ctl = bt.raw = -1; }
int bt_streaming(void) { return bt.det.on; }
int bt_flush_due(void) {
    int due = bt.flush;
    bt.flush = 0;
    return due;
}

void bt_sleep(unsigned ms) {
    unsigned end_at = now_ms() + ms;
    for (;;) {
        if (guard_woke) return; /* a request that came while q2guard was busy: answered now */
        unsigned now = now_ms();
        int left = (int)(end_at - now), step = left;
        if (left <= 0) return;
        if (bt.det.on) { /* the next sample is due a second after the last */
            int due = (int)(bt.at + BT_TICK_MS - now);
            step = due <= 0 ? 0 : due < left ? due : left;
        }
        if (step > 0) usleep((unsigned)step * 1000u); /* a signal ends it early (EINTR) */
        if (guard_woke) return;                        /* a request to answer now (q2guard.c) */
        now = now_ms();
        if (!bt.det.on || (int)(now - bt.at) >= BT_TICK_MS) tick(now);
    }
}
