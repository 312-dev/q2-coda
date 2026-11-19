/* q2bt's device side (docs/internals.md#bluetooth-link-monitor): q2guard sleeps in bt_sleep, which
 * reads the controller's byte counter at each of q2guard's ticks and, while an A2DP stream runs,
 * wakes every BT_TICK_MS to write a line to the q2-bt log. Each line costs two ioctls, five HCI
 * commands on a raw socket bound only while the stream runs (to the ACL handle listed now: a codec
 * change reconnects on a new one), which also carries the controller's Number Of Completed Packets
 * for each delivered packet, and a few small /proc and /sys reads, bluealsa's encoder
 * thread's schedstat among them, and the player's CPU, I/O counters and open songs with the card's
 * busy time beside them; bluealsa is asked for the codec (dbus-send, in the background) at
 * the start, when its encoder thread changes and every BT_PCMS_MS. Two changes to how the Q2
 * streams: the A2DP sink's link (its address from GetPCMs) is sent Change Connection Packet Type,
 * allowing EDR, once to each of its handles while a stream runs; and under Auto with the
 * step-down's setting on (Adapt to Signal), a link the radio cannot keep up with is moved from aptX
 * HD or aptX straight to SBC and back (bt_step), each switch q2bt-codec's SelectCodec; and on SBC
 * (under Auto, or SBC chosen, with the same setting on) its bitpool is lowered a rung at a
 * time and raised again
 * (bt_sbc_step), written into bluealsa's BT_SBC_POOL byte through /proc/PID/mem, which its encoder
 * takes from the next frame. Every read that fails leaves its
 * field "-", and with no Bluetooth socket at all (the emulator) it tries again a minute later and
 * does nothing else. */
#include "q2bt.h"
#include "q2log.h"

#define PCMS_FILE "/tmp/q2bt.pcms"
#define PCMS_CMD                                                                                   \
    "dbus-send --system --print-reply --dest=org.bluealsa /org/bluealsa "                          \
    "org.bluealsa.Manager1.GetPCMs > " PCMS_FILE " 2>/dev/null"
#define ANSWER_MS 150  /* the five answers are waited for this long at most */
#define RETRY_MS 60000 /* a Bluetooth socket that would not open is tried again after this */
#define FIND_MS 10000  /* bluealsa looked for again, while not found, after this */
#define WLAN "/sys/class/net/wlan0/statistics/"
#define CONFIG "/mnt/data/config.ini" /* Auto and the step-down's setting, read only */
#define CODEC_FILE "/tmp/q2bt.codec"  /* q2bt-codec's answer to a switch */
#define CODEC_PROG "/usr/bin/q2bt-codec"
#define STEP_ASK_MS 3000 /* bluealsa asked for the codec this long after a switch is sent */
#define PLAYER "hciplayer\n" /* q2player, at stock's path and name (tools/build.py) */
#ifndef CARD_DEV
#define CARD_DEV "mmcblk4" /* the microSD (docs/hardware.md); a host test names its own disk */
#endif
#define GROW_WAIT "/tmp/q2player.wait" /* grow.c's, while a song still arriving is waited for */
#define SONGS 4                         /* the player's open songs remembered: one and its preload */

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
    card_log *ulog;        /* the unified log: the EDR outcome, once; each STEP */
    unsigned char sink[6]; /* the A2DP sink's address (bluealsa's a2dpsrc PCM), have_sink */
    int have_sink, sink_h; /* and its ACL handle at the last line, -1 none */
    int down_h;            /* the link whose end is to be noted (bt_down_event), -1 none */
    struct bt_edr edr;
    struct bt_step step; /* the step-down, kept through a pause; afresh for another headset */
    int allowed;         /* bt_step_allowed's, from config.ini */
    char pcm[96];        /* the A2DP source PCM's object path, "" none */
    int codec_pid;       /* q2bt-codec running a switch, 0 none */
    int codec_failed;    /* a switch failed in this stream: no more until the next */
    struct bt_sbc sbc;   /* SBC's bitpool, afresh at each START */
    int sbc_allowed;     /* bt_sbc_allowed's, from config.ini */
    int sbc_failed;      /* bluealsa's byte would not take a write: none until the next START */
    int pl;              /* the player's pid, 0 not found (looked for at pl_found_at) */
    unsigned pl_found_at, pl_ticks, io_r, io_w, io_d, dk_ms;
    int pl_ok, io_ok, dk_ok;  /* pl_ticks, io_* and dk_ms hold the last line's counters */
    char songs[SONGS][128];   /* the card files the player held at the last line */
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
/* the first ACL link's handle; the A2DP sink's into bt.sink_h (-1 none, or no sink known) */
static int acl_handle(void) {
    unsigned req[1 + 4 * 4] = { 0 }; /* dev_id 0, room for four links */
    ((unsigned short *)req)[1] = 4;
    bt.sink_h = -1;
    if (ioctl(bt.ctl, HCIGETCONNLIST, req)) return -1;
    if (bt.have_sink) bt.sink_h = bt_acl_handle_of((const unsigned char *)req, 4, bt.sink);
    return bt_acl_handle((const unsigned char *)req, 4);
}
/* bound to hci0's raw channel: Command Complete (0x0e), for the five answers, Number Of Completed
 * Packets (0x13), the controller's account of each packet it delivered, for the EDR command
 * Command Status (0x0f) and Connection Packet Type Changed (0x1d), and Disconnection Complete
 * (0x05), which says which side ended the link */
static void raw_open(void) {
    unsigned short addr[3] = { AF_BLUETOOTH, 0,
                               0 }; /* sockaddr_hci: family, hci0, HCI_CHANNEL_RAW */
    /* hci_filter: event packets; events 0x05, 0x0e, 0x0f, 0x13 and 0x1d; any opcode */
    unsigned filter[4] = { 1u << 4,
                           1u << 0x05 | 1u << 0x0e | 1u << 0x0f | 1u << 0x13 | 1u << 0x1d, 0, 0 };
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
        char line[48];
        int said = bt_edr_event(&bt.edr, b, n, line, sizeof line);
        if (said) note(line);
        if (said == 2) /* a refusal or an unexpected mask warns */
            ulog_add(bt.ulog, time(0), now_ms(),
                     strstr(line, "ptype=0x0018 status=0x00") ? 'I' : 'W', "bt", line);
        if (bt_down_event(b, n, bt.down_h, line, sizeof line)) {
            note(line);
            ulog_add(bt.ulog, time(0), now_ms(), 'W', "bt", line);
            bt.down_h = -1;
        }
        if (until_answered && bt.link.answered >= 5) return 1;
    }
}
static void link_read(void) {
    static const int ops[5] = { BT_OP_RSSI, BT_OP_LQ, BT_OP_TPL, BT_OP_AFH, BT_OP_FCC };
    unsigned char c[8];
    char line[32];
    int h = acl_handle(); /* each line: a reconnect (a codec change) brings a new handle */
    /* the link followed until now ended: its Disconnection Complete is waiting, read before the
     * handle is let go */
    if (bt.raw >= 0 && bt.down_h >= 0 && h != bt.down_h) events(0, 0);
    if (h >= 0) bt.down_h = h;
    if (bt_handle_follow(&bt.link, h)) {
        snprintf(line, sizeof line, "LINK h=%d", h), note(line);
        bt.fly.have = 0; /* a new link's packets: counted afresh */
    }
    bt_link_clear(&bt.link, h);
    /* EDR, once to each of the sink's handles (bt_edr_step): a phone's link and LE are never sent
     * it */
    if (bt.raw >= 0 && bt_edr_step(&bt.edr, bt.sink_h, c)) write(bt.raw, c, 8);
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
static int find_comm(const char *name) { /* the first process whose comm is name ("x\n") */
    void *d = opendir("/proc");
    int pid = 0;
    if (!d) return 0;
    for (struct dirent *e; !pid && (e = readdir(d));) {
        char p[40], comm[24];
        if (e->d_name[0] < '1' || e->d_name[0] > '9') continue;
        snprintf(p, sizeof p, "/proc/%s/comm", e->d_name);
        if (slurp(p, comm, sizeof comm) > 0 && !strcmp(comm, name)) pid = atoi(e->d_name);
    }
    closedir(d);
    return pid;
}
static int find_bluealsa(void) { return find_comm("bluealsa\n"); }
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

/* ---- the player's side ----
 * What fed the encoder: the player's CPU, what it read (rchar, and read_bytes from storage) and
 * wrote (wchar, mostly its PCM into bluealsa's FIFO), the microSD's busy time, grow.c's wait for a
 * song still arriving, and a PLAY note when the player opens a card file it did not hold at the
 * last line (a song, or the next one preloaded under 6 s from the end). Reads only. */
static void player_songs(void) {
    char p[48], link[160], now_[SONGS][128];
    int n = 0;
    void *d;
    snprintf(p, sizeof p, "/proc/%d/fd", bt.pl);
    if (!(d = opendir(p))) return;
    for (struct dirent *e; n < SONGS && (e = readdir(d));) {
        char f[64];
        if (e->d_name[0] < '0' || e->d_name[0] > '9') continue;
        snprintf(f, sizeof f, "/proc/%d/fd/%s", bt.pl, e->d_name);
        int k = (int)readlink(f, link, sizeof link - 1);
        if (k <= 0) continue;
        link[k] = 0;
        if (!bt_song_path(link)) continue;
        snprintf(now_[n++], sizeof now_[0], "%s", link);
    }
    closedir(d);
    for (int i = 0; i < n; ++i) {
        int seen = 0;
        for (int j = 0; j < SONGS && !seen; ++j) seen = !strcmp(now_[i], bt.songs[j]);
        if (seen) continue;
        char line[160];
        snprintf(line, sizeof line, "PLAY %s", now_[i]);
        note(line);
    }
    memset(bt.songs, 0, sizeof bt.songs);
    memcpy(bt.songs, now_, (size_t)n * sizeof now_[0]);
}
static void player_read(unsigned now, unsigned ms, struct bt_sample *s) {
    char p[40], b[256];
    int ok;
    unsigned r, w, dr, t;
    s->pl = s->pr = s->pd = s->pw = -1;
    s->gw = bt.pl ? access(GROW_WAIT, F_OK) == 0 : -1;
    if (!bt.pl && now - bt.pl_found_at >= FIND_MS) bt.pl = find_comm(PLAYER), bt.pl_found_at = now, bt.pl_ok = bt.io_ok = 0;
    if (!bt.pl) return;
    snprintf(p, sizeof p, "/proc/%d/stat", bt.pl);
    t = ticks_of(p, &ok);
    if (!ok) { /* the player ended (an idle exit) or was started again */
        bt.pl = 0, bt.pl_ok = 0, bt.pl_found_at = now - FIND_MS;
        memset(bt.songs, 0, sizeof bt.songs);
        return;
    }
    s->pl = bt.pl_ok ? bt_ticks_pct(t - bt.pl_ticks, ms) : -1;
    bt.pl_ticks = t, bt.pl_ok = 1;
    snprintf(p, sizeof p, "/proc/%d/io", bt.pl);
    if (slurp(p, b, sizeof b) > 0 && bt_proc_io(b, &r, &w, &dr)) {
        if (bt.io_ok)
            s->pr = bt_rate(r - bt.io_r, ms) / 1024, s->pw = bt_rate(w - bt.io_w, ms) / 1024,
            s->pd = bt_rate(dr - bt.io_d, ms) / 1024;
        bt.io_r = r, bt.io_w = w, bt.io_d = dr, bt.io_ok = 1;
    } else {
        bt.io_ok = 0;
    }
    player_songs();
}
static int card_busy(unsigned ms) { /* the microSD's busy ms in a second since the last line */
    char b[8192];
    unsigned v;
    int r = -1;
    if (slurp("/proc/diskstats", b, sizeof b) > 0 && bt_disk_busy(b, CARD_DEV, &v)) {
        if (bt.dk_ok) r = bt_rate(v - bt.dk_ms, ms);
        bt.dk_ms = v, bt.dk_ok = 1;
    } else {
        bt.dk_ok = 0;
    }
    return r;
}

/* ---- the codec ---- */
static void allowed_read(void) {
    char b[8192];
    int n = slurp(CONFIG, b, sizeof b);
    bt.allowed = n > 0 ? bt_step_allowed(b) : -1;
    bt.sbc_allowed = n > 0 ? bt_sbc_allowed(b) : -1;
}
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
    if (slurp(PCMS_FILE, b, sizeof b) <= 0) return;
    unsigned char was[6];
    int had = bt.have_sink, same = 1;
    memcpy(was, bt.sink, 6);
    bt.have_sink =
        bt_pcms_sink(b, bt.sink); /* the headset, for the EDR command from the next line */
    for (int i = 0; i < 6; ++i) same &= was[i] == bt.sink[i];
    if (!had || !bt.have_sink || !same) /* another headset: where it started is its own */
        memset(&bt.step, 0, sizeof bt.step);
    if (!bt_pcms_path(b, bt.pcm, sizeof bt.pcm)) bt.pcm[0] = 0;
    allowed_read();
    if (!bt_pcms(b, codec, sizeof codec, &hz, &delay)) return;
    if (!strcmp(codec, bt.codec) && hz == bt.hz) return;
    memcpy(bt.codec, codec, sizeof codec), bt.hz = hz;
    snprintf(line, sizeof line, "CODEC %s %u Hz delay=%u.%u ms", codec, hz, delay / 10, delay % 10);
    note(line);
}

/* ---- the step-down (bt_step): a switch is q2bt-codec, in the background ---- */
static void step_reap(unsigned now) {
    int status = 0;
    if (bt.codec_pid <= 0 || waitpid(bt.codec_pid, &status, WNOHANG) != bt.codec_pid) return;
    bt.codec_pid = 0;
    bt.pcms_at = now - BT_PCMS_MS; /* the codec it has now: asked at the next line */
    if (!status) return;           /* exited 0 */
    char b[96] = "", line[128];
    int n = slurp(CODEC_FILE, b, sizeof b);
    for (int i = 0; i < n && i < (int)sizeof b; ++i)
        if (b[i] == '\n') b[i] = 0;
    snprintf(line, sizeof line, "STEP failed %s", n > 0 ? b : "-");
    note(line);
    ulog_add(bt.ulog, time(0), now_ms(), 'W', "bt", line);
    bt.codec_failed = 1;
}
/* ---- SBC's bitpool: bluealsa's BT_SBC_POOL bytes, through /proc/PID/mem ---- */
static int pool_at(int fd) { return lseek(fd, BT_SBC_POOL, SEEK_SET) == BT_SBC_POOL; }
/* the bitpool asked for, the encoder thread's own and the last used, into b[3]: 1 read (0 with no
 * bluealsa, or one without the bytes: an unpatched build's address is not mapped) */
static int pool_read(unsigned char *b) {
    char p[32];
    if (!bt.ba) return 0;
    snprintf(p, sizeof p, "/proc/%d/mem", bt.ba);
    int fd = open(p, O_RDONLY | O_CLOEXEC);
    if (fd < 0) return 0;
    int ok = pool_at(fd) && read(fd, b, 3) == 3;
    close(fd);
    return ok;
}
/* v asked for, and read back: 1 when bluealsa has it */
static int pool_write(int v) {
    char p[32];
    unsigned char b[3], c = (unsigned char)v;
    if (!bt.ba) return 0;
    snprintf(p, sizeof p, "/proc/%d/mem", bt.ba);
    int fd = open(p, O_RDWR | O_CLOEXEC);
    if (fd < 0) return 0;
    int ok = pool_at(fd) && write(fd, &c, 1) == 1;
    close(fd);
    return ok && pool_read(b) && b[0] == c;
}
static void say(char level, const char *line) {
    note(line);
    ulog_add(bt.ulog, time(0), now_ms(), level, "bt", line);
}
/* One line on SBC: bt_sbc_step's rung, and bluealsa's byte made to agree with it (a q2guard started
 * again, or a new stream, finds whatever was left there). have: pb was read. */
static void sbc_step(unsigned now, int have, const unsigned char *pb, int wq, int fly, int bufs) {
    char line[64];
    int rank = bt_codec_rank(bt.codec), own = have ? pb[1] : 0;
    int was = bt.sbc.pool ? bt.sbc.pool : own;
    int to = bt_sbc_step(&bt.sbc, now, bt.sbc_allowed, rank, rank == 1 ? own : 0, wq, fly, bufs);
    if (to >= 0) {
        int now_pool = to ? to : own;
        snprintf(line, sizeof line, "STEP SBC bitpool %d -> %d", was, now_pool);
        say('I', line);
    }
    if (!have || bt.sbc_failed || pb[0] == bt.sbc.pool) return;
    if (pool_write(bt.sbc.pool)) return;
    snprintf(line, sizeof line, "STEP failed SBC bitpool %d: /proc/%d/mem", bt.sbc.pool, bt.ba);
    say('W', line);
    bt.sbc_failed = 1;
}

static void step(unsigned now, const struct bt_sample *ls, int own, int bufs) {
    char line[160];
    step_reap(now);
    int rank = bt_codec_rank(bt.codec);
    struct bt_step_line l = { ls->kbps, ls->acl, ls->done, ls->wq, ls->fly,
                              bufs,     ls->afh, own,      0,      bt.hz };
    l.lowered = bt.sbc.pool != 0;
    int to = bt_step(&bt.step, now, bt.allowed, rank, &l);
    if (!to || bt.codec_pid > 0 || bt.codec_failed || !bt.pcm[0]) return;
    snprintf(line, sizeof line, "STEP %s -> %s", bt.codec, bt_codec_name(to));
    say('I', line);
    snprintf(line, sizeof line, CODEC_PROG " %s %s > " CODEC_FILE " 2>&1", bt.pcm,
             bt_codec_name(to)); /* bt_pcms_path lets no shell character through */
    int pid = fork();
    if (!pid) {
        execl("/bin/sh", "sh", "-c", line, (char *)0);
        _exit(127);
    }
    bt.codec_pid = pid > 0 ? pid : 0;
    bt.pcms_at = now - BT_PCMS_MS + STEP_ASK_MS;
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
    bt.codec[0] = 0, bt.hz = 0, bt.enc_name[0] = 0, bt.fly.have = 0, bt.codec_failed = 0;
    memset(&bt.sbc, 0, sizeof bt.sbc), bt.sbc_failed = 0;
    allowed_read();
    enc_find(now);
    bt.pl_ok = bt.io_ok = bt.dk_ok = 0; /* the player's and the card's first rates come a line on */
    memset(bt.songs, 0, sizeof bt.songs);     /* each START notes what the player holds */
    card_busy(1000);
    snprintf(line, sizeof line, "START h=%d mtu=%d bufs=%d raw=%s", bt.link.handle, c->acl_mtu,
             c->acl_pkts, bt.raw >= 0 ? "ok" : "none");
    note(line);
    pcms_start(now);
    bt.flush = 1;
}
static void end(void) {
    note("STOP");
    bt.flush = 1;
    bt_edr_reset(&bt.edr); /* a reconnect in the pause may bring the same handle number back */
    if (bt.raw >= 0) close(bt.raw), bt.raw = -1;
}
static void sample(unsigned now, const struct bt_counts *c) {
    struct bt_sample s;
    unsigned ms = now - bt.at, tx, rx, t;
    int ok, cores;
    char b[4096], line[288];
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
    unsigned char pb[3];
    int have = pool_read(pb);
    s.bp = have && !strcmp(bt.enc_name, "ba-a2dp-sbc") ? pb[2] : -1;
    player_read(now, ms, &s);
    s.dk = card_busy(ms);
    bt_line(line, sizeof line, &s);
    heat_add(bt.log, line);
    bt.was = *c, bt.at = now;
    sbc_step(now, have, pb, s.wq, s.fly, c->acl_pkts);
    step(now, &s, have ? pb[1] : 0, c->acl_pkts);
}
static void tick(unsigned now) {
    struct bt_counts c;
    pcms_reap();
    step_reap(now); /* a switch that outlived its stream, too */
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

/* ---- a wedged controller (docs/internals.md#bluetooth-controller-watchdog) ----
 * The kernel's lines feed bt_wedge (q2bt_core.c); a suspicion is probed with Read Local Version on
 * a raw socket of its own, and a confirmed wedge asks demo, through the state file's bt=, to
 * relaunch the stack (heatguard.c); demo answers "bt N done" (or "off") in the ack file. */
static struct bt_wedge wedge;
void bt_kmsg(const char *rec, int n) {
    char b[256];
    const char *msg;
    unsigned kms;
    if (n <= 0) return;
    if (n > (int)sizeof b - 1) n = (int)sizeof b - 1;
    memcpy(b, rec, (size_t)n), b[n] = 0;
    if (bt_kmsg_split(b, &kms, &msg)) bt_wedge_line(&wedge, now_ms(), kms, msg);
}
/* 1 answered, 0 not within BT_PROBE_MS, -1 no verdict (no hci0, or it is down) */
static int probe(unsigned now) {
    unsigned di[BT_DEVINFO_SIZE / 4] = { 0 };
    unsigned short addr[3] = { AF_BLUETOOTH, 0, 0 }; /* hci0, HCI_CHANNEL_RAW */
    unsigned filter[4] = { 1u << 4, 1u << 0x0e, 0, 0 }; /* event packets: Command Complete */
    static const unsigned char cmd[4] = { 1, BT_OP_VERSION & 0xff, BT_OP_VERSION >> 8, 0 };
    unsigned char b[64];
    if (!ctl_ok(now) || ioctl(bt.ctl, HCIGETDEVINFO, di) || !bt_dev_up((unsigned char *)di))
        return -1;
    int s = socket(AF_BLUETOOTH, SOCK_RAW | SOCK_CLOEXEC, BTPROTO_HCI), got = -1;
    if (s < 0) return -1;
    if (!bind(s, addr, sizeof addr) && !setsockopt(s, 0, 2, filter, 14) && write(s, cmd, 4) == 4) {
        got = 0;
        for (unsigned end = now_ms() + BT_PROBE_MS; !got;) {
            int left = (int)(end - now_ms());
            struct pollfd p = { s, POLLIN, 0 };
            if (left <= 0 || poll(&p, 1, left) <= 0) break;
            int n = (int)read(s, b, sizeof b);
            if (n <= 0) break;
            got = bt_probe_answer(b, n);
        }
    }
    close(s);
    return got;
}
int bt_watch(unsigned now, const char *ack) {
    char line[96];
    static int seeded;
    int off, n = bt_answer_parse(ack, &off);
    /* a q2guard started again numbers its asks on from demo's last answer, which would otherwise
     * read as the answer to an ask of the same number */
    if (!seeded) seeded = 1, wedge.req = n;
    if (wedge.asked && n == wedge.req) {
        bt_wedge_done(&wedge, now, n);
        say(off ? 'W' : 'I', off ? "BT RESET not made: Bluetooth is off" : "BT RESET demo relaunched the stack");
    }
    switch (bt_wedge_step(&wedge, now)) {
    case BT_W_LATE: say('W', "BT RESET unanswered: demo busy or not running"); return wedge.req;
    case BT_W_PROBE: break;
    default: return wedge.req;
    }
    int busy = bt.codec_pid > 0 || find_comm("q2bt-codec\n");
    switch (bt_wedge_probed(&wedge, now, probe(now), busy)) {
    case BT_W_RESET:
        snprintf(line, sizeof line, "BT RESET reason=%s%s after %us", wedge.storm ? "storm" : "",
                 wedge.timeout ? (wedge.storm ? "+timeout" : "timeout") : "",
                 (now - wedge.first) / 1000u);
        say('W', line);
        break;
    case BT_W_DEFER:
        say('I', busy ? "BT RESET waits: a codec switch is running" : "BT RESET waits: too soon after the last");
        break;
    case BT_W_GIVEUP:
        snprintf(line, sizeof line, "BT RESET gave up: %d in a row did not cure it", BT_RESET_MAX);
        say('E', line);
        break;
    case BT_W_HEALTHY: say('I', "BT PROBE answered: the controller is alive"); break;
    }
    return wedge.req;
}

void bt_init(card_log *l, card_log *u) {
    bt.log = l, bt.ulog = u, bt.ctl = bt.raw = -1, bt.sink_h = bt.down_h = -1;
    bt_edr_reset(&bt.edr);
}
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
        if (guard_woke) return;                       /* a request to answer now (q2guard.c) */
        now = now_ms();
        if (!bt.det.on || (int)(now - bt.at) >= BT_TICK_MS) tick(now);
    }
}
