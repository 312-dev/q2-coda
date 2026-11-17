/* q2guard's decisions, log line and log storage (docs/internals.md#overheat-guardian). No reads of
 * the device here: q2guard.c feeds a sample every 5 s and acts on what guard_step says. */
#include "q2guard.h"

/* ---- the sensor's verdict ----
 * A reading counts only inside GUARD_MIN_DC..GUARD_MAX_DC. GUARD_ERRORS bad or missing readings in a
 * row put the sensor down; one that has read a single value for GUARD_VARY readings is a constant
 * (a placeholder, no thermistor) and is down too. It is trusted once two readings differ, and
 * again after it went down as soon as it reads, and varies, again. */
static void sensor_read(struct guard *g, int t, struct guard_out *o) {
    if (t == GUARD_NONE || t < GUARD_MIN_DC || t > GUARD_MAX_DC) {
        if (++g->errors < GUARD_ERRORS) return;
        g->seen = 0;
        if (g->sensor != SENSOR_DOWN) g->sensor = SENSOR_DOWN, o->event = EV_SENSOR_DOWN;
        return;
    }
    g->errors = 0;
    if (!g->seen++)
        g->lo = g->hi = t;
    else if (t < g->lo)
        g->lo = t;
    else if (t > g->hi)
        g->hi = t;
    if (g->hi != g->lo) {
        if (g->sensor != SENSOR_OK) g->sensor = SENSOR_OK, o->event = EV_SENSOR_OK;
    } else if (g->seen >= GUARD_VARY && g->sensor == SENSOR_PENDING) {
        g->sensor = SENSOR_DOWN, o->event = EV_SENSOR_DOWN;
    }
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
    sensor_read(g, in->temp, o);
    if (g->sensor == SENSOR_OK) {
        if (g->load == LOAD_COUNTING) o->event = EV_LOAD_CANCELLED;
        if (g->load != LOAD_ARMED) load_reset(g);
        int t = in->temp; /* valid: the sensor read it */
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
        unsigned long long pct = (unsigned long long)used * 1000 / span;
        top[k].pct = pct > 99999 ? 99999 : (int)pct;
    }
    int m = n < TOP_PIDS ? n : TOP_PIDS;
    for (int i = 0; i < m; ++i) prev->pid[i] = pid[i], prev->ticks[i] = ticks[i];
    prev->n = m;
}

/* ---- the log ---- */
void heat_add(struct heat_log *l, const char *line) {
    int n = (int)strlen(line);
    if (n <= 0 || n > HEAT_RAM / 4) return;
    while (l->used + n > HEAT_RAM) { /* the oldest line goes */
        int k = 0;
        while (k < l->used && l->ram[k] != '\n') ++k;
        k = k < l->used ? k + 1 : l->used;
        memmove(l->ram, l->ram + k, l->used - k);
        l->used -= k;
        ++l->dropped;
    }
    memcpy(l->ram + l->used, line, n);
    l->used += n;
}

void heat_card_away(struct heat_log *l, long now) {
    if (!l->away) l->away = 1, l->away_since = now;
}

struct sink {
    char p0[160], p1[160];
    int fd;
    long size;
};
static int sink_open(struct sink *s, int fresh) {
    s->fd = open(s->p0, O_WRONLY | O_APPEND | O_CREAT | O_CLOEXEC | (fresh ? O_TRUNC : 0), 0644);
    if (s->fd < 0) return -1;
    s->size = fresh ? 0 : lseek(s->fd, 0, SEEK_END);
    return s->size < 0 ? -1 : 0;
}
/* Whole lines of buf, moving to a fresh .0 (the old one to .1) where the next line would pass
 * HEAT_FILE_MAX; how many bytes went out. */
static int sink_write(struct sink *s, const char *buf, int n) {
    int done = 0;
    while (done < n) {
        int k = done;
        while (k < n) { /* the lines that fit, at least one in an empty file */
            int e = k;
            while (e < n && buf[e] != '\n') ++e;
            e += e < n;
            if (s->size + (e - done) > HEAT_FILE_MAX && (k > done || s->size)) break;
            k = e;
        }
        if (k > done) {
            if (write(s->fd, buf + done, k - done) != k - done) return done;
            s->size += k - done;
            done = k;
        }
        if (done < n) {
            fsync(s->fd), close(s->fd);
            s->fd = -1;
            if (rename(s->p0, s->p1) || sink_open(s, 1)) return done;
        }
    }
    return done;
}

static void mkdirs(char *path) { /* each parent of path's last part, and path itself */
    for (char *p = path + 1; *p; ++p)
        if (*p == '/') *p = 0, mkdir(path, 0755), *p = '/';
    mkdir(path, 0755);
}

int heat_flush(struct heat_log *l, const char *dir, long now) {
    if (!l->used) return 0;
    struct sink s;
    char d[128];
    int n = (int)strlen(dir);
    if (n >= (int)sizeof d) return -1;
    memcpy(d, dir, n + 1);
    mkdirs(d);
    snprintf(s.p0, sizeof s.p0, "%s/q2-heat.0", dir);
    snprintf(s.p1, sizeof s.p1, "%s/q2-heat.1", dir);
    if (sink_open(&s, 0)) {
        if (s.fd >= 0) close(s.fd);
        heat_card_away(l, now);
        return -1;
    }
    if (l->away || l->dropped) {
        char m[160];
        int lines = 0;
        for (int i = 0; i < l->used; ++i) lines += l->ram[i] == '\n';
        struct put b = { m, m + sizeof m - 2 };
        put_time(&b, now);
        puts_(&b, " GAP card away since ");
        if (l->away) put_time(&b, l->away_since);
        else puts_(&b, "-");
        field(&b, "held", lines), field(&b, "dropped", l->dropped);
        *b.p++ = '\n';
        int len = (int)(b.p - m);
        if (sink_write(&s, m, len) != len) {
            if (s.fd >= 0) close(s.fd);
            heat_card_away(l, now);
            return -1;
        }
    }
    int done = sink_write(&s, l->ram, l->used);
    if (s.fd >= 0) fsync(s.fd), close(s.fd);
    memmove(l->ram, l->ram + done, l->used - done); /* what went out is not written twice */
    l->used -= done;
    l->dropped = 0;
    if (l->used) {
        l->away = 0; /* the marker is out; a fresh one for what is left */
        heat_card_away(l, now);
        return -1;
    }
    l->away = 0;
    return 0;
}
