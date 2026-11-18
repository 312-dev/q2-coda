/* q2guard: the heat recorder and overheat guardian, a process of its own so that it keeps
 * watching when demo is hung (docs/internals.md#overheat-guardian). S90play starts it before demo
 * and again if it ends: q2guard [--dev] VERSION LAST, LAST how the previous session ended (clean,
 * forced, stall; respawn after a restart of q2guard itself). Every GUARD_TICK_MS it reads the
 * temperature (the PMIC's die, q2guard_pmic.c, or anything else real that reports one) and the CPU's
 * busy share, and q2guard_core.c decides;
 * a log line (q2guard.h heat_line) goes to RAM every LINE_MS, every GUARD_TICK_MS while warm,
 * plugged in or busy, and the lines go to the card's /mnt/mmc/.q2muse/logs in batches. demo draws
 * the cards it is told about in /tmp/q2guard.state and answers in /tmp/q2guard.ack (heatguard.c).
 * It writes nothing to /mnt/data. No MIPS sysroot: glibc 2.28's declarations, as boot.c. */
#include "q2guard.h"

#ifndef Q2GUARD_HOST
struct timespec {
    long tv_sec, tv_nsec;
};
struct dirent { /* glibc's, MIPS o32, no LFS */
    unsigned d_ino;
    long d_off;
    unsigned short d_reclen;
    unsigned char d_type;
    char d_name[256];
};
typedef int pid_t;
ssize_t read(int, void *, size_t);
int clock_gettime(int, struct timespec *), usleep(unsigned), kill(int, int), reboot(int);
pid_t fork(void), waitpid(pid_t, int *, int);
void _exit(int), sync(void);
long time(long *);
void *opendir(const char *);
struct dirent *readdir(void *);
int flock(int, int), closedir(void *), strcmp(const char *, const char *), strncmp(const char *, const char *, size_t);
char *strstr(const char *, const char *);
int atoi(const char *);
int ioctl(int, unsigned long, ...);
#define O_RDONLY 0
#define O_RDWR 2
#define WNOHANG 1
#define CLOCK_MONOTONIC 1
#define RB_POWER_OFF 0x4321fedc
#define LOCK_EX 2
#define LOCK_NB 4
#endif

#define GUARD_TICK_MS 5000 /* a sample; 1 s while powering off */
#define LINE_MS 30000      /* a log line when nothing is warm, plugged in or busy */
#define FAST_DC 450        /* warm: a line every tick */
#define FAST_BUSY 50
#define FLUSH_MS 300000 /* the RAM lines to the card: every 5 min, a minute while fast, and at once on an event */
#define FLUSH_FAST_MS 60000
#define LOGS "/mnt/mmc/.q2muse/logs"
#define STATE "/tmp/q2guard.state"
#define STATE_TMP "/tmp/q2guard.state.tmp"
#define ACK "/tmp/q2guard.ack"
#define LOCK "/tmp/q2guard.lock" /* one q2guard at a time: S90play's loop starts another only once it ended */
#define SENSOR_HOOK "/tmp/q2guard.sensor" /* --dev only: a temperature in tenths of a degree, or "none" */
#define PS "/sys/class/power_supply/"
#define PMIC_DEV "/dev/i2c-6" /* the AXP2101's bus on the Q2 (axp20x-i2c owns 0x34, so I2C_SLAVE_FORCE) */
#define PMIC_ADDR 0x34

static int slurp(const char *path, char *buf, int size) { /* the file's start, NUL-terminated */
    int fd = open(path, O_RDONLY | O_CLOEXEC), n = 0, got;
    if (fd < 0) return buf[0] = 0, -1;
    while (n < size - 1 && (got = (int)read(fd, buf + n, size - 1 - n)) > 0) n += got;
    close(fd);
    buf[n] = 0;
    return n;
}
static int read_int(const char *path, int *v) {
    char b[32];
    if (slurp(path, b, sizeof b) <= 0 || ((b[0] < '0' || b[0] > '9') && b[0] != '-')) return -1;
    *v = atoi(b);
    return 0;
}
static void word(const char *path, char *out, int size) { /* the first word, or "" */
    char b[64];
    int i = 0;
    slurp(path, b, sizeof b);
    while (b[i] && b[i] != '\n' && b[i] != ' ' && i < size - 1) out[i] = b[i], ++i;
    out[i] = 0;
}
static unsigned now_ms(void) {
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return (unsigned)t.tv_sec * 1000u + (unsigned)(t.tv_nsec / 1000000);
}

/* ---- the PMIC's bus: i2c-dev, one SMBus byte transaction at a time (linux/i2c-dev.h) ---- */
#define I2C_SLAVE_FORCE 0x0706
#define I2C_SMBUS 0x0720
#define I2C_SMBUS_BYTE_DATA 2
struct smbus_args {
    char read_write; /* 1 read, 0 write */
    unsigned char command;
    unsigned size;
    unsigned char *data; /* union i2c_smbus_data: 34 bytes, the byte first */
};
static int pmic_fd = -1;
static int smbus(int read, int reg, unsigned char *data) {
    struct smbus_args a = { (char)read, (unsigned char)reg, I2C_SMBUS_BYTE_DATA, data };
    return ioctl(pmic_fd, I2C_SMBUS, &a);
}
static int bus_rd(void *ctx, int reg) {
    unsigned char d[34];
    return smbus(1, reg, d) < 0 ? -1 : d[0];
}
static int bus_wr(void *ctx, int reg, int v) {
    unsigned char d[34] = { (unsigned char)v };
    return smbus(0, reg, d) < 0 ? -1 : 0;
}
static void bus_sleep(void *ctx, int ms) { usleep((unsigned)ms * 1000u); }
static const struct pmic_bus bus = { bus_rd, bus_wr, bus_sleep, 0 };
static struct pmic pmic;
static void pmic_open(void) {
    pmic_fd = open(PMIC_DEV, O_RDWR | O_CLOEXEC);
    if (pmic_fd >= 0 && ioctl(pmic_fd, I2C_SLAVE_FORCE, PMIC_ADDR) >= 0) return;
    pmic.state = PMIC_OFF; /* the emulator: no PMIC */
    snprintf(pmic.note, sizeof pmic.note, "SENSOR pmic none: %s %s", PMIC_DEV,
             pmic_fd < 0 ? "does not open" : "refuses 0x34");
}

/* The temperature, in tenths of a degree: the PMIC's die (die, read this sample) while its reader is
 * on, a misread included (GUARD_NONE, which q2guard_core.c counts against it); with the reader off, a
 * thermal zone (millidegrees), a hwmon input (millidegrees), the battery supply's temp (tenths)
 * unless it is the AXP2101's, which returns the low byte of the die's ADC count (docs/internals.md#overheat-guardian).
 * The stock Q2 has none of those, so with the PMIC off this reads GUARD_NONE and q2guard_core.c
 * watches the CPU load instead. */
static int dev_build;
static int read_temp(int *raw, int die) {
    int v;
    char b[16];
    *raw = read_int(PS "battery/temp", &v) ? GUARD_NONE : v;
    if (dev_build && slurp(SENSOR_HOOK, b, sizeof b) > 0)
        return b[0] == '-' || (b[0] >= '0' && b[0] <= '9') ? atoi(b) : GUARD_NONE;
    if (pmic.state != PMIC_OFF) return die;
    if (!read_int("/sys/class/thermal/thermal_zone0/temp", &v)) return v / 100;
    if (!read_int("/sys/class/hwmon/hwmon0/temp1_input", &v)) return v / 100;
    word(PS "battery/manufacturer", b, sizeof b);
    if (*raw != GUARD_NONE && strcmp(b, "X-POWERS")) return *raw;
    return GUARD_NONE;
}

/* /proc/stat's first line: all ticks and the idle ones (idle + iowait), and the cores */
static int cpu_ticks(unsigned long long *all, unsigned long long *idle) {
    char b[1024];
    if (slurp("/proc/stat", b, sizeof b) < 5 || strncmp(b, "cpu ", 4)) return 0;
    unsigned long long v[8] = { 0 };
    const char *s = b + 4;
    for (int i = 0; i < 8; ++i) {
        while (*s == ' ') ++s;
        while (*s >= '0' && *s <= '9') v[i] = v[i] * 10 + (*s++ - '0');
    }
    *all = 0;
    for (int i = 0; i < 8; ++i) *all += v[i];
    *idle = v[3] + v[4];
    int cores = 0;
    for (const char *p = b; (p = strstr(p, "\ncpu")); ++p) cores += p[4] >= '0' && p[4] <= '9';
    return cores ? cores : 1;
}
struct cpu {
    unsigned long long all, idle;
};
static int busy_since(struct cpu *c, int *cores) { /* percent since the last call, -1 the first */
    unsigned long long all = 0, idle = 0;
    int n = cpu_ticks(&all, &idle);
    if (!n) return -1;
    *cores = n;
    int pct = -1;
    if (c->all && all > c->all) { /* deltas over seconds fit 32 bits: no 64-bit division (no libgcc) */
        unsigned da = (unsigned)(all - c->all), di = (unsigned)(idle - c->idle);
        pct = (int)(100 - (di > da ? da : di) * 100u / da);
    }
    c->all = all, c->idle = idle;
    return pct < -1 ? 0 : pct > 100 ? 100 : pct;
}

/* ---- processes ---- */
static int pids[TOP_PIDS];
static char names[TOP_PIDS][16];
static unsigned ticks[TOP_PIDS];
static struct top_table table;
static int scan(int *demo_threads, int *demo_pid) { /* /proc's processes; how many */
    void *d = opendir("/proc");
    int n = 0;
    *demo_threads = -1;
    if (!d) return -1;
    for (struct dirent *e; (e = readdir(d));) {
        if (e->d_name[0] < '1' || e->d_name[0] > '9') continue;
        char path[40], b[512];
        snprintf(path, sizeof path, "/proc/%s/stat", e->d_name);
        if (slurp(path, b, sizeof b) <= 0) continue;
        char *open_ = strstr(b, " ("), *close_ = 0;
        for (char *p = b; *p; ++p)
            if (*p == ')') close_ = p; /* the comm may hold ")" */
        if (!open_ || !close_ || n >= TOP_PIDS) continue;
        int len = (int)(close_ - open_ - 2);
        if (len > 15) len = 15;
        memcpy(names[n], open_ + 2, len);
        names[n][len] = 0;
        unsigned long f[20] = { 0 }; /* fields 3.. after the comm */
        const char *s = close_ + 2;
        for (int i = 0; i < 20 && *s; ++i) {
            while (*s && *s != ' ') { /* a number, or the state letter */
                if (*s >= '0' && *s <= '9') f[i] = f[i] * 10 + (*s - '0');
                ++s;
            }
            while (*s == ' ') ++s;
        }
        pids[n] = atoi(e->d_name);
        ticks[n] = (unsigned)(f[11] + f[12]); /* utime, stime: fields 14, 15 */
        if (!strcmp(names[n], "demo")) *demo_threads = (int)f[17], *demo_pid = pids[n]; /* 20 */
        ++n;
    }
    closedir(d);
    /* readdir gives /proc in creation order, which is not quite pid order: sort for top_three */
    for (int i = 1; i < n; ++i)
        for (int j = i; j > 0 && pids[j] < pids[j - 1]; --j) {
            int p = pids[j];
            unsigned t = ticks[j];
            char nm[16];
            memcpy(nm, names[j], 16);
            pids[j] = pids[j - 1], ticks[j] = ticks[j - 1], memcpy(names[j], names[j - 1], 16);
            pids[j - 1] = p, ticks[j - 1] = t, memcpy(names[j - 1], nm, 16);
        }
    return n;
}
static void kill_players(void) { /* demo is not answering: stop the audio the only way left */
    int threads, demo;
    int n = scan(&threads, &demo);
    for (int i = 0; i < n; ++i)
        if (!strcmp(names[i], "hciplayer") || !strcmp(names[i], "q2player")) kill(pids[i], 9);
}

/* ---- the device's state ---- */
static void first_in(const char *dir, const char *file, char *out, int size) { /* dir/<first>/file */
    void *d = opendir(dir);
    out[0] = 0;
    if (!d) return;
    for (struct dirent *e; (e = readdir(d));) {
        if (e->d_name[0] == '.') continue;
        char p[160];
        snprintf(p, sizeof p, "%s/%s/%s", dir, e->d_name, file);
        word(p, out, size);
        if (out[0]) break;
    }
    closedir(d);
}
static void gadget(char *out, int size) { /* the configfs gadget bound to a UDC */
    void *d = opendir("/sys/kernel/config/usb_gadget");
    out[0] = 0;
    if (!d) return;
    for (struct dirent *e; (e = readdir(d));) {
        char p[160], udc[32];
        if (e->d_name[0] == '.') continue;
        snprintf(p, sizeof p, "/sys/kernel/config/usb_gadget/%s/UDC", e->d_name);
        word(p, udc, sizeof udc);
        if (udc[0]) {
            snprintf(out, size, "%s", e->d_name);
            break;
        }
    }
    closedir(d);
}
static int lun_busy(void) { /* the card handed to a computer (USB Transfer Files) */
    void *d = opendir("/sys/kernel/config/usb_gadget"), *f;
    int busy = 0;
    if (!d) return 0;
    for (struct dirent *e; !busy && (e = readdir(d));) {
        char p[200], v[8];
        if (e->d_name[0] == '.') continue;
        snprintf(p, sizeof p, "/sys/kernel/config/usb_gadget/%s/functions", e->d_name);
        if (!(f = opendir(p))) continue;
        for (struct dirent *g; !busy && (g = readdir(f));) {
            if (strncmp(g->d_name, "mass_storage", 12)) continue;
            snprintf(p, sizeof p, "/sys/kernel/config/usb_gadget/%s/functions/%s/lun.0/file",
                     e->d_name, g->d_name);
            word(p, v, sizeof v);
            busy = v[0] != 0;
        }
        closedir(f);
    }
    closedir(d);
    return busy;
}
static int card_ready(void) { /* mounted read-write, and not the computer's */
    char b[4096];
    slurp("/proc/mounts", b, sizeof b);
    const char *m = strstr(b, " /mnt/mmc ");
    if (!m) return 0;
    m = strstr(m + 10, " ");
    return m && !strncmp(m + 1, "rw", 2) && !lun_busy();
}

static void sample(struct heat_sample *s, int busy, int temp, int raw, int die, int adc, const char *state) {
    int v, threads = -1, demo = 0;
    char p[32];
    s->wall = time(0);
    s->up = now_ms() / 1000;
    s->temp = temp, s->raw_temp = raw, s->die = die, s->adc = adc;
    s->level = read_int("/sys/devices/i2c-0/0-0062/cw2015_capacity", &v) ? -1 : v;
    s->mv = read_int(PS "battery/voltage_now", &v) ? -1 : v / 1000;
    word(PS "battery/status", s->status, sizeof s->status);
    s->cable = read_int(PS "ac/online", &v) ? -1 : v;
    first_in("/sys/class/udc", "state", s->udc, sizeof s->udc);
    gadget(s->gadget, sizeof s->gadget);
    first_in("/sys/class/backlight", "bl_power", p, sizeof p);
    s->screen = p[0] ? p[0] == '0' : -1;
    first_in("/sys/class/backlight", "brightness", p, sizeof p);
    s->bright = p[0] ? atoi(p) : -1;
    word("/sys/class/net/wlan0/operstate", p, sizeof p);
    s->wifi = !strcmp(p, "up");
    s->bt = slurp("/sys/class/bluetooth/hci0/uevent", p, sizeof p) > 0;
    s->mhz = read_int("/sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq", &v) ? -1 : v / 1000;
    s->busy = busy;
    s->procs = scan(&threads, &demo);
    s->demo_threads = threads;
    s->state = state;
}

/* ---- the log's writer: a child, so a card that hangs never stops the watching ----
 * The watching process keeps every line in card_store.c's ring: card_mounted is false there, so
 * card_log_append only holds. The child is forked when card_ready and says the card is up, so its
 * card_log_flush appends the ring to the file; the watcher then drops what the child was given. */
static card_log heat;
static int in_writer; /* true in the child */
int card_mounted(void) { return in_writer; }
static struct {
    pid_t pid;
    unsigned len, dropped; /* what the child was given */
    int done;              /* the last flush ended (written, failed or given up) */
} fl;
static void flush_start(void) {
    if (fl.pid > 0) return;
    if (!card_ready()) {
        fl.done = 1;
        return;
    }
    fl.len = heat.len, fl.dropped = heat.dropped, fl.done = 0;
    pid_t pid = fork();
    if (!pid) in_writer = 1, _exit(heat_flush(&heat, LOGS) ? 1 : 0);
    fl.pid = pid;
    if (pid < 0) fl.pid = 0, fl.done = 1;
}
static void flush_reap(void) {
    int status = 0;
    if (fl.pid <= 0 || waitpid(fl.pid, &status, WNOHANG) != fl.pid) return;
    fl.pid = 0, fl.done = 1;
    if (!status && heat.dropped == fl.dropped) { /* if the ring moved since, keep it (twice beats lost) */
        memmove(heat.ring, heat.ring + fl.len, heat.len - fl.len);
        heat.len -= fl.len;
        heat.dropped = 0;
    }
}

/* ---- demo ---- */
static void publish(int seq, const char *card, int left, const struct guard *g, int temp, unsigned beat) {
    char b[160];
    int n = snprintf(b, sizeof b, "seq=%d card=%s left=%d state=%s t=%d beat=%u\n", seq, card, left,
                     guard_state(g), temp == GUARD_NONE ? -1 : temp, beat);
    int fd = open(STATE_TMP, O_WRONLY | O_CREAT | O_TRUNC | O_CLOEXEC, 0644);
    if (fd < 0) return;
    write(fd, b, n);
    close(fd);
    rename(STATE_TMP, STATE);
}
static void answers(int *ack, int *cancel) { /* "ack N" and "cancel N" lines, the latest of each */
    char b[128];
    *ack = *cancel = -1;
    slurp(ACK, b, sizeof b);
    for (char *s = b; *s;) {
        if (!strncmp(s, "ack ", 4)) *ack = atoi(s + 4);
        if (!strncmp(s, "cancel ", 7)) *cancel = atoi(s + 7);
        while (*s && *s != '\n') ++s;
        if (*s) ++s;
    }
}

/* The last lines before the power-off (the player stopped, the power-off itself): written while there is time
 * before the deadline, at most FINAL_MS, then the power-off goes ahead whatever the card does. */
#define FINAL_MS 3000
static void flush_final(unsigned deadline) {
    while (fl.pid > 0 && (int)(deadline - now_ms()) > 0) usleep(100000), flush_reap();
    flush_start();
    unsigned end = now_ms() + FINAL_MS;
    if ((int)(end - deadline) > 0) end = deadline;
    while (fl.pid > 0 && (int)(end - now_ms()) > 0) usleep(100000), flush_reap();
}

static void note(const char *what) { /* a line of its own in the log */
    char b[200];
    heat_note(b, sizeof b, time(0), what);
    heat_add(&heat, b);
}

int main(int argc, char **argv) {
    static struct guard g;
    struct cpu cpu5 = { 0, 0 }, cpu_line = { 0, 0 };
    int lock = open(LOCK, O_WRONLY | O_CREAT | O_CLOEXEC, 0644);
    if (lock >= 0 && flock(lock, LOCK_EX | LOCK_NB)) return 1; /* another is watching */
    card_log_at(&heat, LOGS "/q2-heat.0", HEAT_RAM);
    dev_build = argc > 1 && !strcmp(argv[1], "--dev");
    argv += dev_build, argc -= dev_build;
    const char *build = argc > 1 ? argv[1] : "?", *last = argc > 2 ? argv[2] : "?";
    char b[160];
    snprintf(b, sizeof b, "BOOT build=%s last=%s", build, last);
    note(b);
    int seq = 0, cores = 1, raw, temp, busy, line_due = 1;
    const char *card = "none";
    unsigned beat = 0, start = now_ms(), last_line = start, last_flush = start, off_seq = 0;
    unsigned load_end = 0, window_ms = 0;
    int cancel_seen = -1;
    busy_since(&cpu5, &cores);
    busy_since(&cpu_line, &cores);
    struct heat_sample s = { 0 };
    char line[512];
    s.cable = -1;
    pmic_open();
    flush_start();
    for (;;) {
        /* a tick every GUARD_TICK_MS, the PMIC's window included */
        usleep(g.off ? 1000000 : (GUARD_TICK_MS - (window_ms < GUARD_TICK_MS ? window_ms : 0)) * 1000);
        unsigned now = now_ms();
        flush_reap();
        /* the die: not while powering off (its window would hold up the 1 s ticks, and nothing reads it) */
        int adc = -1, die = g.off ? GUARD_NONE : pmic_sample(&bus, &pmic, &adc);
        window_ms = now_ms() - now;
        if (pmic.note[0]) note(pmic.note), pmic.note[0] = 0;
        if (pmic.measured == 1) {
            pmic.measured = 2;
            if (pmic.first_ms >= 0)
                snprintf(b, sizeof b, "SENSOR pmic first conversion after %d ms", pmic.first_ms);
            else
                snprintf(b, sizeof b, "SENSOR pmic no new conversion in %d ms", PMIC_WINDOW_MS);
            note(b);
        }
        temp = read_temp(&raw, die);
        busy = g.off ? -1 : busy_since(&cpu5, &cores);
        int ack, cancel;
        answers(&ack, &cancel);
        struct guard_in in = { now, temp, busy, 0, 0, 0 };
        in.cancel = !strcmp(card, "loadwarn") && cancel == seq && cancel != cancel_seen;
        if (in.cancel) cancel_seen = cancel;
        in.acked = g.off && ack >= (int)off_seq;
        in.saved = g.off && fl.done && fl.pid <= 0;
        struct guard_out o;
        guard_step(&g, &in, &o);
        int was_off = g.off && o.event == EV_OFF;
        switch (o.event) {
        case EV_WARN: card = "warn", ++seq, note("WARN hot"); break;
        case EV_SENSOR_DOWN:
            card = "nosensor", ++seq;
            snprintf(b, sizeof b, "SENSOR none usable: %s", guard_why(&g));
            note(b);
            break;
        case EV_SENSOR_OK: card = "none", ++seq, note("SENSOR ok"); break;
        case EV_LOAD_WARN:
            card = "loadwarn", ++seq, load_end = now + GUARD_LOAD_MS, note("LOADWARN 60 s");
            break;
        case EV_LOAD_CANCELLED: card = "none", ++seq, note("LOADWARN cancelled"); break;
        case EV_OFF:
            card = "off", off_seq = ++seq;
            note(g.off_why == 'L' ? "POWEROFF load with no sensor" : "POWEROFF hot");
            break;
        }
        if (o.event != EV_NONE || was_off) line_due = 1;
        int left = !strcmp(card, "loadwarn") ? (int)(load_end - now)
                   : g.off                   ? GUARD_CARD_MS - (int)(now - g.off_at)
                                             : 0;
        publish(seq, card, left < 0 ? 0 : left, &g, temp, ++beat);
        int fast = (temp != GUARD_NONE && temp >= FAST_DC) || busy >= FAST_BUSY || g.off ||
                   g.load == LOAD_COUNTING || s.cable == 1;
        if (line_due || now - last_line >= (fast ? GUARD_TICK_MS : LINE_MS)) {
            unsigned long long before = cpu_line.all; /* the span in ticks of one core */
            int lb = busy_since(&cpu_line, &cores);
            unsigned span = before ? (unsigned)(cpu_line.all - before) / (unsigned)cores : 0;
            sample(&s, lb, temp, raw, die, adc, guard_state(&g));
            top_three(&table, s.procs > 0 ? s.procs : 0, pids, (const char(*)[16])names, ticks, span,
                      s.top);
            heat_line(line, sizeof line, &s);
            heat_add(&heat, line);
            last_line = now;
        }
        if (o.kill_player) note("NOANSWER demo: player stopped"), kill_players();
        if (o.flush || (line_due && o.event != EV_NONE) ||
            now - last_flush >= (fast ? FLUSH_FAST_MS : FLUSH_MS)) {
            if (g.off && !card_ready()) note("CARD not mounted: log in RAM only");
            flush_start();
            last_flush = now;
        }
        line_due = 0;
        if (o.power_off) {
            static int now_said;
            if (!now_said++) note("POWER off now");
            flush_final(g.off_at + GUARD_DEADLINE_MS);
            sync();
            reboot(RB_POWER_OFF);
            /* still here: refused (the emulator's sandbox can); said once, tried again every tick */
            static int said;
            if (!said++) note("POWER off refused, trying again");
            g.powered = 0;
        }
    }
}
