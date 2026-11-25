/* q2bt's device side (docs/internals.md#bluetooth-link-monitor): q2guard sleeps in bt_sleep, which
 * reads the controller's byte counter and its list of links at each of q2guard's ticks and, while an
 * A2DP stream runs or any classic link is up, wakes every BT_TICK_MS to write a line to the q2-bt
 * log. A stream's line costs three ioctls, two HCI reads (RSSI, the AFH map) on a raw socket bound
 * while a link is up (to the ACL handle listed now: a codec change reconnects on a new one), which
 * also carries the controller's Number Of Completed Packets for each delivered packet, and a few
 * small /proc and /sys reads, bluealsa's encoder thread's schedstat among them, and the player's
 * CPU and open songs with the card's busy time beside them; bluealsa is asked for the codec
 * (dbus-send, in the background) at the start, when its encoder thread changes and every
 * BT_PCMS_MS. An idle link's second (the recorder, docs/bluetooth.md's sampler) is files and ioctls
 * only: the byte counters, Wi-Fi's, the A2DP socket's queue and the UART's error counts. Every
 * BT_LINK_MS each link's RSSI and AFH map are read (the stream's link from its own line), and once
 * a handle its role and timeouts and the maximum transmit power; every BT_COEX_MS, while a link is
 * up, wpa_supplicant's STATUS (the band) and Realtek's coex dump (its moving lines). While a stream
 * runs the band is asked every BT_LINK_MS too, and BT_PACE_FILE says whether downloads are paced
 * (bt_pace). With nothing connected a tick is the two ioctls. Two changes to how the Q2
 * streams: the A2DP sink's link (its address from GetPCMs) is sent Change Connection Packet Type,
 * allowing EDR, once to each of its handles while a stream runs; and under Auto with the
 * step-down's setting on (Adapt to Signal), a link the radio cannot keep up with is moved from aptX
 * HD or aptX straight to SBC and back (bt_step), each switch q2bt-codec's SelectCodec; and on SBC
 * (under Auto, or SBC chosen, with the same setting on) its bitpool is lowered a rung at a
 * time and raised again
 * (bt_sbc_step), written into bluealsa's BT_SBC_POOL byte through /proc/PID/mem, which its encoder
 * takes from the next frame, and on AAC under Auto its peak the same way (bt_aac_step, BT_AAC_RATE's
 * byte). Every read that fails leaves its
 * field "-", and with no Bluetooth socket at all (the emulator) it tries again a minute later and
 * does nothing else. Apart from streams, the kernel's lines q2guard reads feed the controller
 * watchdog (bt_kmsg, bt_watch, docs/internals.md#bluetooth-controller-watchdog), and bt_sleep waits
 * on the kernel's management channel, whose device events (connects and who began them, drops,
 * failures, pairings, Forgets, discoveries and the devices each found) go to the unified log as
 * they come (docs/internals.md#bluetooth-devices), and on the system bus's socket, whose A2DP
 * transport signals (q2dbus.c) do too (docs/internals.md#bluetooth-event-log). */
#include "q2bt.h"
#include "q2dbus.h"
#include "q2log.h"

#define PCMS_FILE "/tmp/q2bt.pcms"
#define CFG_FILE "/tmp/q2bt.cfg" /* the CODEC note's cfg= and cap=: GetCodecs, then the transport's object (cfg_start) */
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
#ifndef WPA_CTRL
#define WPA_CTRL "/var/run/wpa_supplicant/wlan0" /* its control socket (smart_wifi.c's WPA_SOCK) */
#endif
#define WPA_MS 300 /* its STATUS answer waited for this long at most */

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
    int cfg_pid;             /* cfg_start's fetch running, 0 none */
    char cfg_line[96];       /* the CODEC note waiting for it */
    unsigned char cap_of[6]; /* the sink whose cap= was noted last */
    int cap_said;
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
    struct bt_rung sbc;  /* SBC's bitpool, afresh at each START */
    int sbc_allowed;     /* bt_sbc_allowed's, from config.ini */
    int sbc_failed;      /* bluealsa's byte would not take a write: none until the next START */
    struct bt_rung aac;  /* AAC's peak, the same, under Auto (allowed) */
    int aac_failed;      /* as sbc_failed, for AAC's byte */
    int pl;              /* the player's pid, 0 not found (looked for at pl_found_at) */
    unsigned pl_found_at, pl_ticks, dk_ms;
    int pl_ok, dk_ok;         /* pl_ticks and dk_ms hold the last line's counters */
    char songs[SONGS][128];   /* the card files the player held at the last line */
    unsigned char first[6];   /* the first ACL link's address at the last acl_handle */
    unsigned char down[6];    /* down_h's address */
    int mgmt;                 /* the management channel (devices), -1 none */
    int mgmt_tried, mgmt_warned;
    unsigned mgmt_failed_at;
    struct bt_names names;
    struct bt_found found; /* the addresses this discovery has answered from */
    /* the recorder: the classic links listed at the last tick, and those whose ACLINFO was asked */
    struct bt_acl links[BT_LINKS];
    int nlinks, rec; /* links listed; a stream or a link is being recorded (bt.was and bt.at hold) */
    struct bt_acl told[BT_LINKS];
    int ntold;
    unsigned link_at, coex_at; /* the last link pass and Wi-Fi and coex read */
    int link_due, coex_due;    /* one is due at the next tick (a new link, a stream's start) */
    unsigned uart[BT_UART_N];  /* the UART's counts at the last line, when uart_ok */
    int uart_ok;
    int freq;        /* the Wi-Fi frequency wpa_supplicant last said, 0 none */
    unsigned freq_at;
    int paced, pace_said; /* BT_PACE_FILE's state, and whether it has been written */
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
    return bt_acl_handle((const unsigned char *)req, 4, bt.first);
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
/* Events until k has want answers or ms is up (0: only what is waiting, all of it), completions
 * counted on the way. 1 when k had them. */
static int events(int ms, struct bt_link *k, int want) {
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
        if (k && k != &bt.link) bt_event(k, b, n);
        char line[72];
        int said = bt_edr_event(&bt.edr, b, n, line, sizeof line);
        if (said) note(line);
        if (said == 2) /* a refusal or an unexpected mask warns */
            ulog_add(bt.ulog, time(0), now_ms(),
                     strstr(line, "ptype=0x0018 status=0x00") ? 'I' : 'W', "bt", line);
        if (bt_down_event(b, n, bt.down_h, bt.down, line, sizeof line)) {
            note(line);
            ulog_add(bt.ulog, time(0), now_ms(), 'W', "bt", line);
            bt.down_h = -1;
        }
        if (k && k->answered >= want) return 1;
    }
}
static void link_read(void) {
    static const int ops[2] = { BT_OP_RSSI, BT_OP_AFH };
    unsigned char c[8];
    char line[32];
    int h = acl_handle(); /* each line: a reconnect (a codec change) brings a new handle */
    /* the link followed until now ended: its Disconnection Complete is waiting, read before the
     * handle is let go */
    if (bt.raw >= 0 && bt.down_h >= 0 && h != bt.down_h) events(0, 0, 0);
    if (h >= 0) bt.down_h = h, memcpy(bt.down, bt.first, 6);
    if (bt_handle_follow(&bt.link, h)) {
        snprintf(line, sizeof line, "LINK h=%d", h), note(line);
        bt.fly.have = 0; /* a new link's packets: counted afresh */
    }
    bt_link_clear(&bt.link, h);
    /* EDR, once to each of the sink's handles (bt_edr_step): a phone's link and LE are never sent
     * it */
    if (bt.raw >= 0 && bt_edr_step(&bt.edr, bt.sink_h, c)) write(bt.raw, c, 8);
    if (bt.raw < 0 || h < 0) return;
    events(0, 0, 0); /* the second's completions, and last second's late answers */
    bt_link_clear(&bt.link, h);
    for (int i = 0; i < 2; ++i) write(bt.raw, c, (size_t)bt_cmd(c, ops[i], h));
    events(ANSWER_MS, &bt.link, 2);
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
 * What fed the encoder: the player's CPU, the microSD's busy time, grow.c's wait for a song still
 * arriving, and a PLAY note when the player opens a card file it did not hold at the last line (a
 * song, or the next one preloaded under 6 s from the end). Reads only. */
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
    char p[40];
    int ok;
    unsigned t;
    s->pl = -1;
    s->gw = bt.pl ? access(GROW_WAIT, F_OK) == 0 : -1;
    if (!bt.pl && now - bt.pl_found_at >= FIND_MS) bt.pl = find_comm(PLAYER), bt.pl_found_at = now, bt.pl_ok = 0;
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
/* The CODEC note's cfg= and cap=, read only (docs/internals.md#bluetooth-link-monitor): in the background,
 * bluealsa's GetCodecs on the A2DP source PCM (each codec's Capabilities, the sink's filtered by the Q2's own),
 * then BlueZ's objects cut to the sink's MediaTransport1 (its Configuration: the bytes the stream was set up with).
 * The note waits for them; 0 when there is nothing to ask (no PCM path or sink known): noted at once. */
static int cfg_start(const char *line) {
    char cmd[640], dev[18];
    if (bt.cfg_pid > 0 || !bt.pcm[0] || !bt.have_sink) return 0;
    bt_addr_text(bt.sink, dev);
    for (char *c = dev; *c; ++c)
        if (*c == ':') *c = '_';
    snprintf(cmd, sizeof cmd,
             "dbus-send --system --print-reply --dest=org.bluealsa %s org.bluealsa.PCM1.GetCodecs > " CFG_FILE
             " 2>/dev/null; dbus-send --system --print-reply --dest=org.bluez / "
             "org.freedesktop.DBus.ObjectManager.GetManagedObjects 2>/dev/null | "
             "sed -n '/object path \"\\/org\\/bluez\\/hci0\\/dev_%s\\/sep[0-9]*\\/fd[0-9]*\"/,/object path/p' >> " CFG_FILE,
             bt.pcm, dev);
    int pid = fork();
    if (!pid) {
        execl("/bin/sh", "sh", "-c", cmd, (char *)0);
        _exit(127);
    }
    if (pid < 0) return 0;
    bt.cfg_pid = pid;
    snprintf(bt.cfg_line, sizeof bt.cfg_line, "%s", line);
    return 1;
}
static void cfg_reap(void) {
    int status = 0;
    if (bt.cfg_pid <= 0 || waitpid(bt.cfg_pid, &status, WNOHANG) != bt.cfg_pid) return;
    bt.cfg_pid = 0;
    static char b[8192];
    char cfg[64] = "-", cap[64] = "", after[40], line[256];
    int n = slurp(CFG_FILE, b, sizeof b) > 0;
    if (n && !bt_dbus_bytes(b, "/fd", "\"Configuration\"", cfg, sizeof cfg)) snprintf(cfg, sizeof cfg, "-");
    snprintf(after, sizeof after, "string \"%s\"", bt.codec);
    int same = bt.cap_said && !memcmp(bt.cap_of, bt.sink, 6); /* cap= once a device */
    if (n && !same && bt_dbus_bytes(b, after, "\"Capabilities\"", cap, sizeof cap))
        memcpy(bt.cap_of, bt.sink, 6), bt.cap_said = 1;
    snprintf(line, sizeof line, "%s cfg=%s%s%s", bt.cfg_line, cfg, cap[0] ? " cap=" : "", cap);
    note(line);
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
    if (!cfg_start(line)) note(line); /* else noted with cfg= and cap= once they are read (cfg_reap) */
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
/* ---- SBC's bitpool and AAC's peak: bluealsa's BT_SBC_POOL and BT_AAC_RATE bytes, through /proc/PID/mem ---- */
#define POOL_BYTES (BT_AAC_RATE + 4 - BT_SBC_POOL)
#define AAC_AT (BT_AAC_RATE - BT_SBC_POOL) /* AAC's four in pool_read's */
static int pool_at(int fd, int at) { return lseek(fd, BT_SBC_POOL + at, SEEK_SET) == BT_SBC_POOL + at; }
/* into b[POOL_BYTES]: SBC's bitpool asked for, the encoder thread's own and the last used, then from AAC_AT AAC's
 * rate asked for, the stream's own, the one in use and its mode: 1 read (0 with no bluealsa, or one without the
 * bytes: an unpatched build's address is not mapped) */
static int pool_read(unsigned char *b) {
    char p[32];
    if (!bt.ba) return 0;
    snprintf(p, sizeof p, "/proc/%d/mem", bt.ba);
    int fd = open(p, O_RDONLY | O_CLOEXEC);
    if (fd < 0) return 0;
    int ok = pool_at(fd, 0) && read(fd, b, POOL_BYTES) == POOL_BYTES;
    close(fd);
    return ok;
}
/* v asked for at the byte at (0 SBC's, AAC_AT AAC's), and read back: 1 when bluealsa has it */
static int pool_write(int at, int v) {
    char p[32];
    unsigned char b[POOL_BYTES], c = (unsigned char)v;
    if (!bt.ba) return 0;
    snprintf(p, sizeof p, "/proc/%d/mem", bt.ba);
    int fd = open(p, O_RDWR | O_CLOEXEC);
    if (fd < 0) return 0;
    int ok = pool_at(fd, at) && write(fd, &c, 1) == 1;
    close(fd);
    return ok && pool_read(b) && b[at] == c;
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
    int was = bt.sbc.rung ? bt.sbc.rung : own;
    int to = bt_sbc_step(&bt.sbc, now, bt.sbc_allowed, rank, rank == 1 ? own : 0, wq, fly, bufs);
    if (to >= 0) {
        int now_pool = to ? to : own;
        snprintf(line, sizeof line, "STEP SBC bitpool %d -> %d", was, now_pool);
        say('I', line);
    }
    if (!have || bt.sbc_failed || pb[0] == bt.sbc.rung) return;
    if (pool_write(0, bt.sbc.rung)) return;
    snprintf(line, sizeof line, "STEP failed SBC bitpool %d: /proc/%d/mem", bt.sbc.rung, bt.ba);
    say('W', line);
    bt.sbc_failed = 1;
}
/* One line on AAC: bt_aac_step's peak, and bluealsa's byte made to agree with it, as sbc_step */
static void aac_step(unsigned now, int have, const unsigned char *pb, int wq, int fly, int bufs) {
    char line[64];
    int own = have ? pb[AAC_AT + 1] : 0, was = bt.aac.rung ? bt.aac.rung : own;
    int to = bt_aac_step(&bt.aac, now, bt.allowed, !strcmp(bt.codec, "AAC"), own, wq, fly, bufs);
    if (to >= 0) {
        snprintf(line, sizeof line, "STEP AAC peak %d -> %d kbit/s", was, to ? to : own);
        say('I', line);
    }
    if (!have || bt.aac_failed || pb[AAC_AT] == bt.aac.rung) return;
    if (pool_write(AAC_AT, bt.aac.rung)) return;
    snprintf(line, sizeof line, "STEP failed AAC peak %d: /proc/%d/mem", bt.aac.rung, bt.ba);
    say('W', line);
    bt.aac_failed = 1;
}

static void step(unsigned now, const struct bt_sample *ls, int own, int bufs) {
    char line[160];
    step_reap(now);
    int rank = bt_codec_rank(bt.codec);
    struct bt_step_line l = { ls->kbps, ls->acl, ls->done, ls->wq, ls->fly,
                              bufs,     ls->afh, own,      0,      bt.hz };
    l.lowered = bt.sbc.rung != 0;
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

/* The H5 UART's error counts since boot (bt_uart_counts), at each START, each H5 error line that turns the driver's
 * debug lines on, and each storm's relaunch: the difference says whether the damaged packets were bytes the UART
 * dropped (oe, bo) or misread (fe, pe, brk). With them the Wi-Fi driver's power-save counts (bt_wifi_ips), ips=
 * entered/left, when its ps_info is there: the errors on record all came with Wi-Fi off or not joined, so a count
 * that moved since the last line, or did not, is evidence (docs/internals.md#bluetooth-controller-watchdog). */
static void uart_note(const char *when, int unified) {
    char b[768], line[128];
    unsigned v[BT_UART_N], e, l;
    if (slurp(BT_UART_PROC, b, sizeof b) <= 0 || !bt_uart_counts(b, BT_UART_PORT, v)) return;
    int n = snprintf(line, sizeof line, "UART %s tx=%u rx=%u fe=%u pe=%u brk=%u oe=%u bo=%u", when, v[0], v[1], v[2],
                     v[3], v[4], v[5], v[6]);
    if (slurp(BT_WIFI_PS_PROC, b, sizeof b) > 0 && bt_wifi_ips(b, &e, &l))
        snprintf(line + n, sizeof line - (size_t)n, " ips=%u/%u", e, l);
    if (unified) say('I', line);
    else note(line);
}

/* ---- a stream ---- */
static void begin(unsigned now, const struct bt_counts *c) {
    int ok, cores;
    char line[112], dev[18] = "-";
    bt.was = *c, bt.at = now;
    bt.link.handle = acl_handle();
    if (bt.link.handle >= 0) bt_addr_text(bt.first, dev);
    if (bt.raw < 0) raw_open();
    bt.done_was = bt.done;         /* the raw socket may have been open through an idle spell */
    bt.freq_at = now - BT_LINK_MS; /* the band asked at once: downloads paced from the start */
    if (!bt.ba || !(ba_ticks(&ok), ok)) bt.ba = find_bluealsa(), bt.found_at = now;
    bt.ba_ticks = ba_ticks(&ok);
    bt.own_ticks = ticks_of("/proc/self/stat", &ok);
    bt.wlan = wlan(&bt.wtx, &bt.wrx);
    busy_since(&bt.cpu, &cores);
    bt.codec[0] = 0, bt.hz = 0, bt.enc_name[0] = 0, bt.fly.have = 0, bt.codec_failed = 0;
    memset(&bt.sbc, 0, sizeof bt.sbc), bt.sbc_failed = 0;
    memset(&bt.aac, 0, sizeof bt.aac), bt.aac_failed = 0;
    allowed_read();
    enc_find(now);
    bt.pl_ok = bt.dk_ok = 0;            /* the player's and the card's first rates come a line on */
    memset(bt.songs, 0, sizeof bt.songs);     /* each START notes what the player holds */
    card_busy(1000);
    snprintf(line, sizeof line, "START h=%d mtu=%d bufs=%d raw=%s dev=%s", bt.link.handle,
             c->acl_mtu, c->acl_pkts, bt.raw >= 0 ? "ok" : "none", dev);
    note(line);
    uart_note("start", 0);
    pcms_start(now);
    bt.flush = 1;
}
static void end(void) {
    note("STOP");
    bt.flush = 1;
    bt_edr_reset(&bt.edr); /* a reconnect in the pause may bring the same handle number back */
    if (bt.raw >= 0 && !bt.nlinks) close(bt.raw), bt.raw = -1; /* else kept for the idle link's reads */
}

/* ---- the recorder (docs/bluetooth.md's sampler) ---- */
/* The UART's error counts since the last line into ue (fe, pe, brk, oe, bo): 1 when read twice running */
static int uart_delta(int *ue) {
    char b[768];
    unsigned v[BT_UART_N];
    int had = bt.uart_ok;
    bt.uart_ok = slurp(BT_UART_PROC, b, sizeof b) > 0 && bt_uart_counts(b, BT_UART_PORT, v);
    if (!bt.uart_ok) return 0;
    for (int i = 0; i < 5; ++i) ue[i] = (int)(v[2 + i] - bt.uart[2 + i]);
    memcpy(bt.uart, v, sizeof v);
    return had;
}
static void wifi_rates(unsigned ms, int *wtx, int *wrx) {
    unsigned tx, rx;
    *wtx = *wrx = -1;
    if (!wlan(&tx, &rx)) {
        bt.wlan = 0;
        return;
    }
    if (bt.wlan) *wtx = bt_rate(tx - bt.wtx, ms) / 1024, *wrx = bt_rate(rx - bt.wrx, ms) / 1024;
    bt.wtx = tx, bt.wrx = rx, bt.wlan = 1;
}
/* An idle link's second: files and ioctls, nothing sent to the controller */
static void idle_sample(unsigned now, const struct bt_counts *c) {
    struct bt_idle s;
    char b[4096], line[160];
    unsigned ms = now - bt.at;
    s.wall = time(0), s.up = now / 1000;
    s.btx = bt_rate(c->byte_tx - bt.was.byte_tx, ms), s.brx = bt_rate(c->byte_rx - bt.was.byte_rx, ms);
    wifi_rates(ms, &s.wtx, &s.wrx);
    s.wq = slurp("/proc/net/l2cap", b, sizeof b) > 0 ? bt_l2cap_wmem(b) : -1;
    s.ue_ok = uart_delta(s.ue);
    bt_idle_line(line, sizeof line, &s);
    heat_add(bt.log, line);
    bt.was = *c, bt.at = now;
}
/* The classic links listed now (HCIGETCONNLIST, no radio traffic): a handle told of that is gone is
 * noted (an idle link's end, which the stream's LINK down does not see) and forgotten; a new one
 * makes the link pass due at once, so its ACLINFO comes as it connects. */
static void links_list(void) {
    unsigned req[1 + 4 * 4] = { 0 }; /* dev_id 0, room for four links */
    char line[64], dev[18];
    ((unsigned short *)req)[1] = BT_LINKS;
    bt.nlinks = ioctl(bt.ctl, HCIGETCONNLIST, req)
                    ? 0
                    : bt_acl_links((const unsigned char *)req, BT_LINKS, bt.links, BT_LINKS);
    for (int i = 0; i < bt.ntold;) {
        int up = 0;
        for (int j = 0; j < bt.nlinks && !up; ++j) up = bt.links[j].handle == bt.told[i].handle;
        if (up) {
            ++i;
            continue;
        }
        bt_addr_text(bt.told[i].addr, dev);
        snprintf(line, sizeof line, "ACL gone h=%d dev=%s", bt.told[i].handle, dev);
        note(line);
        bt.told[i] = bt.told[--bt.ntold];
    }
    for (int j = 0; j < bt.nlinks; ++j) {
        int known = 0;
        for (int i = 0; i < bt.ntold && !known; ++i) known = bt.told[i].handle == bt.links[j].handle;
        if (!known) bt.link_due = 1;
    }
}
/* Every BT_LINK_MS, each link's RSSI and AFH map (the stream's link from its own line, nothing sent
 * for it), and the first time a handle is seen its role, supervision and flush timeouts and maximum
 * transmit power: read-only commands, the answers waited for ANSWER_MS a link at most. */
static void links_pass(unsigned now) {
    static const int ops[6] = { BT_OP_RSSI, BT_OP_AFH, BT_OP_ROLE, BT_OP_LSTO, BT_OP_FLUSH, BT_OP_TPL };
    char line[160];
    unsigned char c[8];
    if (!bt.nlinks || (!bt.link_due && now - bt.link_at < BT_LINK_MS)) return;
    bt.link_at = now, bt.link_due = 0;
    if (bt.raw < 0) raw_open();
    for (int j = 0; j < bt.nlinks; ++j) {
        struct bt_link k;
        int h = bt.links[j].handle, told = 0, streamed = bt.det.on && h == bt.link.handle;
        for (int i = 0; i < bt.ntold && !told; ++i) told = bt.told[i].handle == h;
        bt_link_clear(&k, h);
        int first = streamed ? 2 : 0, last = told ? 2 : 6;
        if (bt.raw >= 0 && first < last) {
            for (int i = first; i < last; ++i) write(bt.raw, c, (size_t)bt_cmd(c, ops[i], h));
            events(ANSWER_MS, &k, last - first);
        }
        if (streamed) k.rssi = bt.link.rssi, k.afh = bt.link.afh;
        bt_acl_line(line, sizeof line, time(0), &k, bt.links[j].addr, 0);
        heat_add(bt.log, line);
        if (told || bt.ntold >= BT_LINKS) continue;
        bt_acl_line(line, sizeof line, time(0), &k, bt.links[j].addr, 1);
        heat_add(bt.log, line);
        bt.told[bt.ntold++] = bt.links[j];
    }
}
/* wpa_supplicant's STATUS over its control socket (what wpa_cli asks: its own state, no driver call),
 * from an autobound address so nothing is left in /tmp: the frequency in MHz, 0 when not joined or
 * not answering; its wpa_state into state */
static int wpa_status(char *state, int n) {
    struct {
        unsigned short family;
        char path[108];
    } to = { AF_UNIX, WPA_CTRL };
    unsigned short me = AF_UNIX;
    char b[1024];
    int got = 0;
    if (n > 0) state[0] = 0;
    int s = socket(AF_UNIX, SOCK_DGRAM | SOCK_CLOEXEC, 0);
    if (s < 0) return 0;
    if (!bind(s, (void *)&me, sizeof me) && !connect(s, (void *)&to, sizeof to) && send(s, "STATUS", 6, 0) == 6) {
        struct pollfd p = { s, POLLIN, 0 };
        int k = poll(&p, 1, WPA_MS) > 0 ? (int)read(s, b, sizeof b - 1) : 0;
        if (k > 0) b[k] = 0, got = bt_wpa_freq(b, state, n);
    }
    close(s);
    return got;
}
/* Every BT_COEX_MS while a link is up: the band and the coex lines that move (a driver-to-firmware
 * query, so never more often) */
static void coex_pass(unsigned now) {
    static char b[8192], f[2048];
    char state[24], line[256];
    if (!bt.nlinks || (!bt.coex_due && now - bt.coex_at < BT_COEX_MS)) return;
    bt.coex_at = now, bt.coex_due = 0;
    bt.freq = wpa_status(state, sizeof state), bt.freq_at = now;
    bt_wifi_line(line, sizeof line, time(0), bt.freq, state);
    heat_add(bt.log, line);
    if (slurp(BT_COEX_PROC, b, sizeof b) <= 0 || !bt_coex_filter(b, f, sizeof f)) return;
    for (char *l = f; l && *l;) {
        char *nl = strchr(l, '\n');
        if (nl) *nl = 0;
        snprintf(line, sizeof line, "COEX %s", l);
        note(line);
        l = nl ? nl + 1 : 0;
    }
}
/* BT_PACE_FILE, through a temporary file renamed over it, so a reader never sees half of it */
static void pace_write(int paced, int freq) {
    char text[64];
    int n = bt_pace_text(text, sizeof text, paced, freq);
    int fd = open(PEQ_ROOT BT_PACE_FILE ".tmp", O_WRONLY | O_CREAT | O_TRUNC | O_CLOEXEC, 0644);
    if (fd < 0) return;
    int ok = write(fd, text, (size_t)n) == n;
    close(fd);
    if (ok && !rename(PEQ_ROOT BT_PACE_FILE ".tmp", PEQ_ROOT BT_PACE_FILE)) bt.paced = paced, bt.pace_said = 1;
}
/* While a stream runs, the band every BT_LINK_MS; paced while it is 2.4 GHz (bt_pace), noted at each
 * change in both logs */
static void pace_update(unsigned now) {
    char state[24], line[64];
    if (bt.det.on && now - bt.freq_at >= BT_LINK_MS) bt.freq = wpa_status(state, sizeof state), bt.freq_at = now;
    int want = bt_pace(bt.det.on, bt.freq), was = bt.pace_said ? bt.paced : 0;
    if (bt.pace_said && want == bt.paced) return;
    pace_write(want, bt.freq);
    if (!bt.pace_said || want == was) return; /* not written (tried again next tick), or nothing changed */
    snprintf(line, sizeof line, want ? "PACE on freq=%d" : "PACE off freq=%d", bt.freq);
    say('I', line);
}
/* Bluetooth gone (or the stack stopped): nothing is listed, nothing kept open */
static void record_off(void) {
    bt.nlinks = bt.ntold = bt.rec = 0;
    if (bt.raw >= 0) close(bt.raw), bt.raw = -1;
}
static void sample(unsigned now, const struct bt_counts *c) {
    struct bt_sample s;
    unsigned ms = now - bt.at, t;
    int ok, cores;
    char b[4096], line[288];
    s.wall = time(0), s.up = now / 1000;
    s.kbps = bt_rate(c->byte_tx - bt.was.byte_tx, ms) / 125; /* bytes to kbit */
    s.brx = bt_rate(c->byte_rx - bt.was.byte_rx, ms) / 125;
    s.acl = bt_rate(c->acl_tx - bt.was.acl_tx, ms);
    s.wq = slurp("/proc/net/l2cap", b, sizeof b) > 0 ? bt_l2cap_wmem(b) : -1;
    link_read();
    s.rssi = bt.link.rssi, s.afh = bt.link.afh;
    wifi_rates(ms, &s.wtx, &s.wrx);
    s.ue_ok = uart_delta(s.ue);
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
    if (got) bt.enc_run = run, bt.enc_wait = wait, bt.enc_stat = got;
    unsigned char pb[POOL_BYTES];
    int have = pool_read(pb);
    s.bp = have && !strcmp(bt.enc_name, "ba-a2dp-sbc") ? pb[2] : -1;
    player_read(now, ms, &s);
    s.dk = card_busy(ms);
    bt_line(line, sizeof line, &s);
    heat_add(bt.log, line);
    bt.was = *c, bt.at = now;
    sbc_step(now, have, pb, s.wq, s.fly, c->acl_pkts);
    aac_step(now, have, pb, s.wq, s.fly, c->acl_pkts);
    step(now, &s, have ? pb[1] : 0, c->acl_pkts);
}
static void tick(unsigned now) {
    struct bt_counts c;
    pcms_reap();
    cfg_reap();
    step_reap(now); /* a switch that outlived its stream, too */
    if (!ctl_ok(now) || !counts(&c)) {
        /* hci0 gone under a stream (Bluetooth switched off, the stack stopped or relaunched): the stream
         * ends here, and the next controller's count is read afresh. Left on, no sample could run, so
         * bt_sleep would find the next one always due and loop without sleeping. */
        if (bt.det.on) bt.det.on = bt.det.have = 0, end();
        record_off();
        pace_update(now);
        return;
    }
    links_list();
    int ev = bt_detect_step(&bt.det, now, c.byte_tx), was = bt.rec;
    bt.rec = bt.det.on || bt.nlinks;
    if (ev == BT_EV_START) {
        begin(now, &c);
    } else if (ev == BT_EV_STOP) {
        end();
        bt.was = c, bt.at = now; /* an idle link's seconds from here */
    } else if (bt.det.on) {
        sample(now, &c);
        if (now - bt.pcms_at >= BT_PCMS_MS) pcms_start(now);
    } else if (bt.rec && was) {
        idle_sample(now, &c);
    } else if (bt.rec) { /* a link came: its seconds counted from here */
        int ue[5];
        bt.was = c, bt.at = now;
        uart_delta(ue);
        bt.coex_due = 1;
    }
    if (!bt.nlinks && !bt.det.on && bt.raw >= 0) close(bt.raw), bt.raw = -1;
    links_pass(now);
    coex_pass(now);
    pace_update(now);
}

/* ---- a wedged controller (docs/internals.md#bluetooth-controller-watchdog) ----
 * The kernel's lines feed bt_wedge (q2bt_core.c); a suspicion is probed with Read Local Version on
 * a raw socket of its own, and a confirmed wedge asks demo, through the state file's bt=, to
 * relaunch the stack (heatguard.c); demo answers "bt N done" (or "off") in the ack file. */
static struct bt_wedge wedge;
/* The H5 driver's debug lines (q2bt.h bt_h5dbg): an H5 error line turns them on through the kernel's dynamic debug,
 * and they go to the q2-bt log, "H5 <kernel ms> <line>", each state once (bt_h5dbg_line), until BT_H5D_MS after the
 * last error line, BT_H5D_MAX_MS after they came on, or BT_H5D_CAP lines. They are the numbers a storm's cause is read from: the controller's
 * acknowledgement number against the Q2's sequence number, and whether the controller's packets are reliable ones.
 * The lines cost a printk a packet while on, which is why they are not on all the time. */
static struct bt_h5dbg h5dbg;
static int h5dbg_on, h5dbg_seen, h5dbg_failed;
static unsigned h5dbg_at, h5dbg_since; /* the last H5 error line; when the lines came on */
static void h5dbg_set(int on) {
    char q[320], line[64];
    int n = bt_h5dbg_ctl(q, sizeof q, on), ok = 0;
    for (int i = 0; n > 0 && !ok && i < 2; ++i) {
        int fd = open(i ? BT_H5D_CTL_DEBUGFS : BT_H5D_CTL, O_WRONLY | O_CLOEXEC);
        if (fd < 0) continue;
        ok = write(fd, q, (size_t)n) == n;
        close(fd);
    }
    if (!ok) {
        if (!h5dbg_failed) note("H5 DEBUG unavailable: no dynamic debug control took the queries");
        h5dbg_failed = 1, h5dbg_on = 0;
        return;
    }
    h5dbg_on = on;
    if (on) snprintf(line, sizeof line, "H5 DEBUG on");
    else snprintf(line, sizeof line, "H5 DEBUG off after %d lines", h5dbg.lines);
    note(line);
}
/* An H5 error line. One that comes BT_H5D_MS or more after the last is another event: the counters are noted and the
 * debug lines asked for, all of them new. One that comes sooner belongs to the event going on, whose lines are being
 * logged or have had their share. */
static void h5dbg_sign(unsigned now) {
    int fresh = !h5dbg_seen || now - h5dbg_at >= BT_H5D_MS;
    h5dbg_at = now, h5dbg_seen = 1;
    if (h5dbg_on || !fresh) return;
    memset(&h5dbg, 0, sizeof h5dbg);
    uart_note("sign", 0);
    if (!h5dbg_failed) h5dbg_since = now, h5dbg_set(1);
}
void bt_kmsg(const char *rec, int n) {
    char b[256], line[96];
    const char *msg;
    unsigned kms, now = now_ms();
    if (n <= 0) return;
    if (n > (int)sizeof b - 1) n = (int)sizeof b - 1;
    memcpy(b, rec, (size_t)n), b[n] = 0;
    if (!bt_kmsg_split(b, &kms, &msg)) return;
    char *nl = strchr(b, 10); /* the record's continuation lines are not the message */
    if (nl) *nl = 0;
    if (bt_wedge_line(&wedge, now, kms, msg) == BT_SIGN_H5) h5dbg_sign(now);
    else if (h5dbg_on && bt_h5dbg_line(&h5dbg, msg)) {
        snprintf(line, sizeof line, "H5 %u %s", kms, msg);
        note(line);
    }
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
    if (h5dbg_on && (now - h5dbg_at >= BT_H5D_MS || now - h5dbg_since >= BT_H5D_MAX_MS || h5dbg.lines >= BT_H5D_CAP))
        h5dbg_set(0);
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
        uart_note("reset", 1);
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

/* ---- devices (docs/internals.md#bluetooth-devices) ----
 * The kernel's Bluetooth management interface, the control channel bluetoothd itself drives:
 * bound to no controller (HCI_DEV_NONE), it carries every controller's events. In Linux 5.10
 * (net/bluetooth/mgmt.c mgmt_event, net/bluetooth/hci_sock.c __hci_send_to_channel) each event is
 * copied to every bound control-channel socket a root process opened (HCI_SOCK_TRUSTED), and only
 * the socket whose command caused it may be skipped, so this one sees what bluetoothd sees. A
 * socket that sends no command changes nothing: it only queues copies, and this one sends none.
 * Non-blocking, drained at each of bt_sleep's wakes and as soon as an event arrives, so a line has
 * its event's time; the kernel queues what comes meanwhile. A read is one event whole (each is a
 * datagram). */
#define MGMT_RETRY_MS 60000
#define BT_STORE                                                                                   \
    "/mnt/data/bluetooth" /* BlueZ's: ADAPTER/DEVICE/info (docs/internals.md#bluetooth-name) */
#define MGMT_MAX 32       /* events read in one drain; the rest wait for the next */
static int mgmt_ok(unsigned now) {
    /* sockaddr_hci: family, HCI_DEV_NONE, HCI_CHANNEL_CONTROL */
    unsigned short addr[3] = { AF_BLUETOOTH, 0xffff, 3 };
    if (bt.mgmt >= 0) return 1;
    if (bt.mgmt_tried && now - bt.mgmt_failed_at < MGMT_RETRY_MS) return 0;
    bt.mgmt = socket(AF_BLUETOOTH, SOCK_RAW | SOCK_CLOEXEC | SOCK_NONBLOCK, BTPROTO_HCI);
    if (bt.mgmt >= 0 && bind(bt.mgmt, addr, sizeof addr)) close(bt.mgmt), bt.mgmt = -1;
    if (bt.mgmt < 0) {
        if (!bt.mgmt_tried) /* the emulator: said once */
            ulog_add(bt.ulog, time(0), now_ms(), 'I', "bt",
                     "devices: the management channel does not open");
        bt.mgmt_failed_at = now;
    }
    bt.mgmt_tried = 1;
    return bt.mgmt >= 0;
}
/* The device's name from BlueZ's store: ADAPTER/ADDR/info under each adapter's directory, its start
 * only (Name= is in [General], before the keys) */
static int store_name(const char *a, char *name, int size) {
    void *d = opendir(BT_STORE);
    int got = 0;
    if (!d) return 0;
    for (struct dirent *e; !got && (e = readdir(d));) {
        char path[96], b[1024];
        if (strlen(e->d_name) != 17 || e->d_name[2] != ':') continue;
        snprintf(path, sizeof path, BT_STORE "/%s/%s/info", e->d_name, a);
        got = slurp(path, b, sizeof b) > 0 && bt_info_name(b, name, size);
    }
    closedir(d);
    return got;
}
static void mgmt_drain(void) {
    unsigned char b[1024];
    if (bt.mgmt < 0) return;
    for (int k = 0; k < MGMT_MAX; ++k) {
        struct bt_mgmt_ev e;
        int n = (int)read(bt.mgmt, b, sizeof b);
        if (n <= 0) return; /* none left (EAGAIN) */
        if (!bt_mgmt_parse(b, n, &e)) continue;
        char a[18], stored[64], line[192];
        const char *name = e.name;
        if (e.op == BT_MGMT_EV_DISCOVERING && e.code) bt_found_reset(&bt.found);
        if (e.op == BT_MGMT_EV_DEVICE_FOUND) { /* each device's first answer in this discovery */
            int first = bt_found_first(&bt.found, e.addr);
            if (first < 0)
                ulog_add(bt.ulog, time(0), now_ms(), 'I', "bt",
                         "found: more devices this discovery, not logged");
            if (first <= 0) continue;
            if (!name[0]) name = bt_names_get(&bt.names, e.addr); /* no store read for these */
            bt_names_put(&bt.names, e.addr, name);
        } else if (e.op != BT_MGMT_EV_DISCOVERING) {
            bt_addr_text(e.addr, a);
            if (!name[0]) name = bt_names_get(&bt.names, e.addr);
            if (!name && store_name(a, stored, sizeof stored)) name = stored;
            bt_names_put(&bt.names, e.addr, name);
        }
        if (e.op == BT_MGMT_EV_DEVICE_CONNECTED && ctl_ok(now_ms())) { /* who began it */
            unsigned req[1 + 4 * 4] = { 0 }; /* dev_id 0, room for four links */
            ((unsigned short *)req)[1] = 4;
            if (!ioctl(bt.ctl, HCIGETCONNLIST, req))
                e.out = bt_acl_out((const unsigned char *)req, 4, e.addr);
        }
        char level = bt_mgmt_line(&e, name, line, sizeof line);
        ulog_add(bt.ulog, time(0), now_ms(), level, "bt", line);
        if (level == 'W') bt.mgmt_warned = 1;
    }
}
/* ---- A2DP transports (docs/internals.md#bluetooth-event-log) ----
 * BlueZ's MediaTransport1 objects as the system bus announces them (q2dbus.c): each stream made,
 * started (AVDTP START accepted), suspended and closed, as it comes, beside the management
 * channel's device lines. */
static struct bt_dbus dbus;
static void dbus_drain(void) {
    struct dbus_tr t;
    for (int k = 0; k < MGMT_MAX && dbus_next(&dbus, &t, now_ms()); ++k) {
        char line[192];
        const char *name = bt_names_get(&bt.names, t.bdaddr);
        char level = dbus_tr_line(&t, name, line, sizeof line);
        ulog_add(bt.ulog, time(0), now_ms(), level, "bt", line);
    }
}
static int dbus_up(unsigned now) {
    int was = dbus.tried;
    int ok = dbus_ok(&dbus, PEQ_ROOT DBUS_SOCK, now);
    if (!ok && !was) /* the emulator, or no bus yet: said once */
        ulog_add(bt.ulog, time(0), now_ms(), 'I', "bt", "transports: the system bus does not open");
    return ok;
}

int bt_dev_warned(void) {
    int w = bt.mgmt_warned;
    bt.mgmt_warned = 0;
    return w;
}

void bt_init(card_log *l, card_log *u) {
    bt.log = l, bt.ulog = u, bt.ctl = bt.raw = bt.mgmt = -1, bt.sink_h = bt.down_h = -1;
    dbus_init(&dbus);
    bt_edr_reset(&bt.edr);
    pace_write(0, 0); /* a q2guard started again: whatever the last one left said, nothing streams yet */
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
        if (bt.rec) { /* the next sample is due a second after the last */
            int due = (int)(bt.at + BT_TICK_MS - now);
            step = due <= 0 ? 0 : due < left ? due : left;
        }
        struct pollfd p[2];
        int np = 0;
        if (step > 0 && mgmt_ok(now)) p[np++] = (struct pollfd){ bt.mgmt, POLLIN, 0 };
        if (step > 0 && dbus_up(now)) p[np++] = (struct pollfd){ dbus.fd, POLLIN, 0 };
        if (np) { /* the wait, or until a device or transport event: logged at once */
            int got = poll(p, (unsigned long)np, step); /* a signal ends it early (EINTR) */
            mgmt_drain();
            dbus_drain();
            /* woken early by an event: the rest of the wait, so the tick below keeps its time */
            if (got > 0 && (int)(now_ms() - now) < step) continue;
        } else if (step > 0) {
            usleep((unsigned)step * 1000u); /* a signal ends it early (EINTR) */
        }
        if (guard_woke) return; /* a request to answer now (q2guard.c) */
        now = now_ms();
        if (!bt.rec || (int)(now - bt.at) >= BT_TICK_MS) tick(now);
    }
}
