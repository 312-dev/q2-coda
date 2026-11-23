/* q2guard's decisions, log line and log rotation (docs/internals.md#overheat-guardian). No reads of
 * the device here: q2guard.c feeds a sample every 5 s and acts on what guard_step says. */
#include "q2guard.h"

/* ---- the sensor's verdict ----
 * A reading counts only inside GUARD_MIN_DC..GUARD_MAX_DC and within GUARD_JUMP_DC of the last one that
 * counted (the die's ADC moves a few tenths a sample; a jump is a misread). The charger is on the same die,
 * so its turning on or off (a cable in or out, charging begun or ended) steps the reading by a few degrees
 * in one sample (+4.5 and -3.3 C on a Q2): for GUARD_SETTLE readings from such a change the bound is
 * GUARD_STEP_DC, and the reading that steps becomes the one the next is held to. GUARD_ERRORS bad or missing
 * readings in a row put the sensor down. One that has read a single value for GUARD_VARY readings is a
 * constant (a placeholder, no thermistor) and is down too: before it was trusted, whatever the load;
 * after, when the CPU load swung GUARD_SWING points or more meanwhile, which moves a real die. It is
 * trusted once two readings differ, and again after it went down as soon as it reads, and varies,
 * again. 1 when this reading counts. */
static void down(struct guard *g, int why, struct guard_out *o) {
    g->seen = g->same = 0;
    if (g->sensor != SENSOR_DOWN) g->sensor = SENSOR_DOWN, g->why = why, o->event = EV_SENSOR_DOWN;
}
static int sensor_read(struct guard *g, int t, int busy, int charge, struct guard_out *o) {
    if (charge != g->charge) g->charge = charge, g->settle = GUARD_SETTLE;
    int jump = g->settle ? GUARD_STEP_DC : GUARD_JUMP_DC;
    if (g->settle) --g->settle;
    int bad = t == GUARD_NONE                             ? GUARD_WHY_NONE
              : t < GUARD_MIN_DC || t > GUARD_MAX_DC      ? GUARD_WHY_RANGE
              : g->seen && (t - g->last > jump || g->last - t > jump) ? GUARD_WHY_JUMP
                                                          : 0;
    if (bad) {
        if (++g->errors >= GUARD_ERRORS) down(g, bad, o);
        return 0;
    }
    g->errors = 0;
    if (!g->seen++)
        g->lo = g->hi = t;
    else if (t < g->lo)
        g->lo = t;
    else if (t > g->hi)
        g->hi = t;
    if (g->seen == 1 || t != g->last) g->same = 0, g->busy_lo = 101, g->busy_hi = -1;
    g->last = t, ++g->same;
    if (busy >= 0) {
        if (busy < g->busy_lo) g->busy_lo = busy;
        if (busy > g->busy_hi) g->busy_hi = busy;
    }
    if (g->sensor == SENSOR_OK && g->same >= GUARD_VARY && g->busy_hi - g->busy_lo >= GUARD_SWING) {
        down(g, GUARD_WHY_STUCK, o);
        return 0;
    }
    if (g->hi != g->lo) {
        if (g->sensor != SENSOR_OK) g->sensor = SENSOR_OK, o->event = EV_SENSOR_OK;
    } else if (g->seen >= GUARD_VARY && g->sensor == SENSOR_PENDING) {
        down(g, GUARD_WHY_CONST, o);
        return 0;
    }
    return 1;
}

static void begin_off(struct guard *g, unsigned now, int why, struct guard_out *o) {
    g->off = 1, g->off_at = now, g->off_why = why;
    o->event = EV_OFF, o->flush = 1;
}

static void load_reset(struct guard *g) { g->load = LOAD_ARMED, g->busy_n = g->busy_i = 0; }

/* With no usable sensor: the CPU's mean over the last GUARD_WIN samples. */
static void load_step(struct guard *g, const struct guard_in *in, struct guard_out *o) {
    if (in->busy >= 0) {
        g->busy[g->busy_i] = in->busy;
        g->busy_i = (g->busy_i + 1) % GUARD_WIN;
        if (g->busy_n < GUARD_WIN) ++g->busy_n;
    }
    if (g->load == LOAD_COUNTING) {
        if (in->cancel) {
            g->load = LOAD_CANCELLED, g->calm = 0, o->event = EV_LOAD_CANCELLED;
        } else if ((int)(in->now - g->load_end) >= 0) {
            begin_off(g, in->now, 'L', o);
        }
        return;
    }
    if (g->load == LOAD_CANCELLED) {
        if (in->busy < 0) return;
        g->calm = in->busy < GUARD_CALM_PCT ? g->calm + 1 : 0;
        if (g->calm >= GUARD_CALM) load_reset(g);
        return;
    }
    if (g->busy_n < GUARD_WIN) return;
    int sum = 0;
    for (int i = 0; i < GUARD_WIN; ++i) sum += g->busy[i];
    if (sum >= GUARD_LOAD_PCT * GUARD_WIN) {
        g->load = LOAD_COUNTING, g->load_end = in->now + GUARD_LOAD_MS, o->event = EV_LOAD_WARN;
    }
}

/* One sample. A power-off, once begun, is never undone: demo is asked (EV_OFF) to pause the audio,
 * show its countdown card and power off through stock's own path; with no answer in GUARD_ACK_MS
 * q2guard stops the player and powers off itself as soon as the log is saved, and in any case at
 * GUARD_DEADLINE_MS. */
void guard_step(struct guard *g, const struct guard_in *in, struct guard_out *o) {
    o->event = EV_NONE, o->kill_player = o->flush = o->power_off = 0;
    if (g->off) {
        unsigned t = in->now - g->off_at;
        if (!in->acked && t >= GUARD_ACK_MS && !g->killed) g->killed = 1, o->kill_player = 1;
        int due = t >= GUARD_DEADLINE_MS || (g->killed && in->saved);
        if (due && !g->powered) g->powered = 1, o->power_off = 1;
        return;
    }
    int counts = sensor_read(g, in->temp, in->busy, in->charge, o);
    if (g->sensor == SENSOR_OK) {
        if (g->load == LOAD_COUNTING) o->event = EV_LOAD_CANCELLED;
        if (g->load != LOAD_ARMED) load_reset(g);
        if (!counts) return; /* a misread neither counts towards a threshold nor breaks a run */
        int t = in->temp;
        g->shut_n = t >= GUARD_SHUT_DC ? g->shut_n + 1 : 0;
        g->warn_n = t >= GUARD_WARN_DC ? g->warn_n + 1 : 0;
        if (t < GUARD_REARM_DC) g->warned = 0;
        if (g->shut_n >= GUARD_OVER)
            begin_off(g, in->now, 'T', o);
        else if (g->warn_n >= GUARD_OVER && !g->warned)
            g->warned = 1, o->event = EV_WARN;
        return;
    }
    g->shut_n = g->warn_n = 0;
    int was = o->event;
    load_step(g, in, o);
    if (was == EV_SENSOR_DOWN && o->event == EV_NONE) o->event = was;
}

const char *guard_state(const struct guard *g) {
    if (g->off) return g->off_why == 'L' ? "poweroff-load" : "poweroff-hot";
    if (g->load == LOAD_COUNTING) return "loadwarn";
    if (g->sensor == SENSOR_DOWN) return "nosensor";
    if (g->sensor == SENSOR_PENDING) return "pending";
    return g->warned ? "warm" : "ok";
}

const char *guard_why(const struct guard *g) {
    switch (g->why) {
    case GUARD_WHY_NONE: return "none read";
    case GUARD_WHY_RANGE: return "out of range";
    case GUARD_WHY_JUMP: return "jumps";
    case GUARD_WHY_CONST: return "constant";
    case GUARD_WHY_STUCK: return "stuck while the load changed";
    }
    return "-";
}

/* ---- the line ---- */
struct put {
    char *p, *end;
};
static void puts_(struct put *b, const char *s) {
    while (*s && b->p < b->end) *b->p++ = *s++;
}
static void putn(struct put *b, long v) {
    char d[12];
    int n = 0;
    unsigned long u = v < 0 ? -(unsigned long)v : (unsigned long)v;
    if (v < 0) puts_(b, "-");
    do d[n++] = '0' + u % 10;
    while (u /= 10);
    while (n && b->p < b->end) *b->p++ = d[--n];
}
static void put_pad(struct put *b, long v, int width) { /* zero-padded */
    char d[12];
    int n = 0;
    do d[n++] = '0' + v % 10;
    while ((v /= 10) || n < width);
    while (n && b->p < b->end) *b->p++ = d[--n];
}
static void field(struct put *b, const char *key, long v) { /* " key=v", "-" for -1 */
    puts_(b, " "), puts_(b, key), puts_(b, "=");
    if (v == -1)
        puts_(b, "-");
    else
        putn(b, v);
}
static void tenths(struct put *b, int v) { /* 415 as 41.5 */
    if (v < 0) puts_(b, "-"), v = -v;
    putn(b, v / 10), puts_(b, "."), putn(b, v % 10);
}
/* seconds since 1970 as 2026-11-17T14:03:05Z (days to a date: Howard Hinnant's civil_from_days) */
static void put_time(struct put *b, long t) {
    long days = t / 86400, s = t % 86400;
    if (s < 0) s += 86400, --days;
    long z = days + 719468, era = (z >= 0 ? z : z - 146096) / 146097;
    long doe = z - era * 146097, yoe = (doe - doe / 1460 + doe / 36524 - doe / 146096) / 365;
    long doy = doe - (365 * yoe + yoe / 4 - yoe / 100), mp = (5 * doy + 2) / 153;
    long d = doy - (153 * mp + 2) / 5 + 1, m = mp < 10 ? mp + 3 : mp - 9, y = yoe + era * 400 + (m <= 2);
    put_pad(b, y, 4), puts_(b, "-"), put_pad(b, m, 2), puts_(b, "-"), put_pad(b, d, 2), puts_(b, "T");
    put_pad(b, s / 3600, 2), puts_(b, ":"), put_pad(b, s / 60 % 60, 2), puts_(b, ":"), put_pad(b, s % 60, 2);
    puts_(b, "Z");
}

/* One line, newline included; its length. Every field is there, "-" where not read. */
int heat_line(char *out, int size, const struct heat_sample *s) {
    struct put b = { out, out + size - 2 }; /* room for the newline and NUL */
    put_time(&b, s->wall);
    field(&b, "up", s->up);
    puts_(&b, " t=");
    if (s->temp == GUARD_NONE)
        puts_(&b, "-");
    else
        tenths(&b, s->temp);
    puts_(&b, " die=");
    if (s->die == GUARD_NONE)
        puts_(&b, "-");
    else
        tenths(&b, s->die);
    field(&b, "adc", s->adc);
    field(&b, "raw", s->raw_temp == GUARD_NONE ? -1 : s->raw_temp);
    field(&b, "lv", s->level), field(&b, "mv", s->mv);
    puts_(&b, " st="), puts_(&b, s->status[0] ? s->status : "-");
    field(&b, "cab", s->cable);
    puts_(&b, " udc="), puts_(&b, s->udc[0] ? s->udc : "-");
    puts_(&b, " gad="), puts_(&b, s->gadget[0] ? s->gadget : "-");
    field(&b, "scr", s->screen), field(&b, "br", s->bright);
    field(&b, "wifi", s->wifi), field(&b, "bt", s->bt), field(&b, "mhz", s->mhz);
    field(&b, "cpu", s->busy);
    puts_(&b, " top=");
    int any = 0;
    for (int i = 0; i < 3; ++i) {
        if (!s->top[i].name[0]) continue;
        if (any++) puts_(&b, ",");
        puts_(&b, s->top[i].name), puts_(&b, ":"), tenths(&b, s->top[i].pct);
    }
    if (!any) puts_(&b, "-");
    field(&b, "thr", s->demo_threads), field(&b, "np", s->procs);
    puts_(&b, " g="), puts_(&b, s->state ? s->state : "-");
    *b.p++ = '\n';
    *b.p = 0;
    return (int)(b.p - out);
}

int heat_note(char *out, int size, long wall, const char *what) {
    struct put b = { out, out + size - 2 };
    put_time(&b, wall);
    puts_(&b, " "), puts_(&b, what);
    *b.p++ = '\n';
    *b.p = 0;
    return (int)(b.p - out);
}

/* ---- the top three ---- */
void top_three(struct top_table *prev, int n, const int *pid, const char (*name)[16],
               const unsigned *ticks, unsigned span, struct proc_top *top) {
    unsigned best[3] = { 0, 0, 0 };
    for (int k = 0; k < 3; ++k) top[k].name[0] = 0, top[k].pct = 0;
    int j = 0, had = prev->n > 0;
    for (int i = 0; i < n; ++i) {
        /* /proc lists pids in rising order, so prev is walked once */
        while (j < prev->n && prev->pid[j] < pid[i]) ++j;
        unsigned used = j < prev->n && prev->pid[j] == pid[i] ? ticks[i] - prev->ticks[j]
                        : had                                  ? ticks[i] /* born since */
                                                               : 0;
        if (!used || !span) continue;
        int k = used > best[0] ? 0 : used > best[1] ? 1 : used > best[2] ? 2 : 3;
        if (k == 3) continue;
        for (int m = 2; m > k; --m) best[m] = best[m - 1], top[m] = top[m - 1];
        best[k] = used;
        memcpy(top[k].name, name[i], 16);
        top[k].name[15] = 0;
        unsigned pct = used > 4000000u ? 99999u : used * 1000u / span; /* no 64-bit division (no libgcc) */
        top[k].pct = pct > 99999 ? 99999 : (int)pct;
    }
    int m = n < TOP_PIDS ? n : TOP_PIDS;
    for (int i = 0; i < m; ++i) prev->pid[i] = pid[i], prev->ticks[i] = ticks[i];
    prev->n = m;
}

/* ---- the log ----
 * Lines are held in card_store.c's RAM ring (card_log_append, with the card_mounted that q2guard.c
 * gives: false in the watching process, true in the child that writes). heat_flush is that child's
 * part: the folder made, the file rotated, the ring appended in order. */
void heat_add(card_log *l, const char *line) {
    card_log_append(l, line, (unsigned)strlen(line));
}

static void mkdirs(char *path) { /* each parent of path's last part, and path itself */
    for (char *p = path + 1; *p; ++p)
        if (*p == '/') *p = 0, mkdir(path, 0755), *p = '/';
    mkdir(path, 0755);
}

/* name.0 moved on (.1 to .2 first, and so on: the oldest goes) once it has reached max, so each file
 * ends below max plus one flush (at most the ring's size). */
static void rotate(const char *dir, const char *name, long max, int files) {
    char from[160], to[160];
    snprintf(from, sizeof from, "%s/%s.0", dir, name);
    int fd = open(from, O_WRONLY | O_APPEND | O_CLOEXEC);
    long size = fd < 0 ? 0 : lseek(fd, 0, SEEK_END);
    if (fd >= 0) close(fd);
    if (size < max) return;
    for (int k = files - 1; k > 0; --k) {
        snprintf(from, sizeof from, "%s/%s.%d", dir, name, k - 1);
        snprintf(to, sizeof to, "%s/%s.%d", dir, name, k);
        rename(from, to);
    }
}

int log_flush(card_log *l, const char *dir, const char *name, long max, int files) {
    char d[128];
    int n = (int)strlen(dir);
    if (n >= (int)sizeof d) return -1;
    memcpy(d, dir, n + 1);
    mkdirs(d);
    rotate(dir, name, max, files);
    return card_log_flush(l) ? 0 : -1;
}

int flush_every(int fast, int streaming) { return fast || streaming ? FLUSH_FAST_MS : FLUSH_MS; }

int card_hold_live(const char *path) {
    char b[16];
    int fd = open(path, O_RDONLY | O_CLOEXEC), n = 0;
    if (fd < 0) return 0;
    n = (int)read(fd, b, sizeof b - 1);
    close(fd);
    b[n > 0 ? n : 0] = 0;
    int pid = atoi(b);
    return pid > 1 && !kill(pid, 0);
}

int heat_flush(card_log *l, const char *dir) {
    return log_flush(l, dir, "q2-heat", HEAT_FILE_MAX, HEAT_FILES);
}
