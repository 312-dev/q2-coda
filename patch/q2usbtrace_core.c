/* q2-usbtrace's logic (q2usbtrace.h, docs/internals.md#usb-tracer): the trace's lines, the samples' changes,
 * the shadow state machine fed and compared with stock, and the plug cycles' summaries. No device I/O: the
 * device side (q2usbtrace.c) reads and hands the values here; test/unit/test_q2usbtrace.py runs this on its own. */
#include "q2usbtrace.h"

#ifdef Q2USBTRACE_HOST
#include <stdarg.h>
#else
typedef __builtin_va_list va_list;
#define va_start __builtin_va_start
#define va_end __builtin_va_end
int vsnprintf(char *, size_t, const char *, va_list);
#endif

#define MS 1000000LL

const unsigned char ut_axp_regs[UT_AXP_REGS] = { 0x00, 0x40, 0x41, 0x42, 0x48, 0x49, 0x4a };

/* t as seconds with six decimals into b (32 bytes); "-" for UF_NEVER */
static const char *ts(char *b, long long t) {
    if (t == UF_NEVER) return "-";
    if (t < 0) t = 0;
    unsigned ns;
    unsigned long s = (unsigned long)ut_udiv((unsigned long long)t, 1000000000u, &ns);
    snprintf(b, 32, "%lu.%06u", s, ns / 1000);
    return b;
}
/* d (ns) as seconds with three decimals; "-" for UF_NEVER */
static const char *rel(char *b, long long t, long long from) {
    if (t == UF_NEVER || from == UF_NEVER) return "-";
    long long d = t - from;
    const char *sign = d < 0 ? "-" : "";
    if (d < 0) d = -d;
    unsigned ns;
    unsigned long s = (unsigned long)ut_udiv((unsigned long long)d, 1000000000u, &ns);
    snprintf(b, 32, "%s%lu.%03u", sign, s, ns / 1000000);
    return b;
}

void trace_line(struct trace *tr, long long t, const char *fmt, ...) {
    char line[320], when[32];
    int n = snprintf(line, sizeof line, "%s ", ts(when, t));
    va_list ap;
    va_start(ap, fmt);
    int m = vsnprintf(line + n, sizeof line - n - 1, fmt, ap);
    va_end(ap);
    if (m < 0) return;
    n += m < (int)(sizeof line - n - 1) ? m : (int)(sizeof line - n - 2);
    line[n++] = '\n';
    line[n] = 0;
    unsigned dropped = tr->log.dropped;
    if (card_log_append(&tr->log, line, (unsigned)n)) ++tr->lines;
    if (tr->log.dropped != dropped) tr->dropped_lines += tr->log.dropped - dropped;
}

static void cycle_clear(struct ut_cycle *c) {
    int n = c->n;
    memset(c, 0, sizeof *c);
    c->n = n;
    long long *t[] = { &c->start, &c->last, &c->vbus_on, &c->vbus_off, &c->id_low, &c->id_high, &c->online1,
                       &c->addr, &c->conf, &c->dev_add, &c->dev_rm, &c->card_add, &c->pcm_add, &c->stock_on,
                       &c->stock_off, &c->shadow_on, &c->shadow_off, &c->shadow_replug, &c->shadow_charger,
                       &c->shadow_host };
    for (unsigned i = 0; i < sizeof t / sizeof t[0]; ++i) *t[i] = UF_NEVER;
}
static void first(long long *at, long long t) {
    if (*at == UF_NEVER) *at = t;
}

void trace_init(struct trace *tr, const char *dir, long long now) {
    memset(tr, 0, sizeof *tr);
    usb_fsm_init(&tr->fsm);
    tr->fsm.shadow = 1; /* stock owns PC20: a grant's trial waits for its power */
    for (int i = 0; i < UT_FED_N; ++i) tr->fed[i] = -1;
    snprintf(tr->path0, sizeof tr->path0, "%s/%s.0", dir, UT_LOG_NAME);
    snprintf(tr->path1, sizeof tr->path1, "%s/%s.1", dir, UT_LOG_NAME);
    card_log_at(&tr->log, tr->path0, UT_RAM);
    cycle_clear(&tr->cyc);
    tr->now = now;
    tr->burst_until = UF_NEVER;
    tr->next_sample = now;
    tr->next_flush = now + UT_FLUSH_MS * MS;
    tr->next_clock = now + UT_CLOCK_MS * MS;
    tr->online_at1 = tr->online_at2 = UF_NEVER;
    tr->fsm_last = now;
    tr->udc[0] = 0;
}

/* ---- the shadow ---- */
static const char *fsm_state(const struct trace *tr) { return usb_fsm_state_name(tr->fsm.state); }

static void shadow_out(struct trace *tr, int before, unsigned a, const struct usb_fsm_out *o, long long t) {
    char when[32];
    for (unsigned bit = 1; bit <= UA_DAC_GONE; bit <<= 1) {
        if (!(a & bit)) continue;
        trace_line(tr, t, "shadow: would %s at %s; %s; %s", usb_fsm_action_name(bit), ts(when, t), fsm_state(tr),
                   o->why);
        struct ut_cycle *c = &tr->cyc;
        if (bit == UA_GRANT) first(&c->shadow_on, t), ++c->shadow_grants;
        if (bit == UA_REVOKE) c->shadow_off = t;
        if (bit == UA_REPLUG) first(&c->shadow_replug, t);
        if (bit == UA_CHARGER) first(&c->shadow_charger, t);
        if (bit == UA_HOST_SEEN) first(&c->shadow_host, t);
    }
    if (!a && before != tr->fsm.state)
        trace_line(tr, t, "shadow: %s -> %s; %s", usb_fsm_state_name(before), fsm_state(tr), o->why);
}

/* The shadow's timers due by t, each logged at its own time. */
static void fsm_timers(struct trace *tr, long long t) {
    for (int guard = 0; guard < 64; ++guard) {
        long long d = usb_fsm_deadline(&tr->fsm);
        if (d == UF_NEVER || d > t) break;
        if (d < tr->fsm_last) d = tr->fsm_last;
        int before = tr->fsm.state;
        struct usb_fsm_out o;
        unsigned a = usb_fsm_step(&tr->fsm, UF_EV_TIMER, 0, d, &o);
        tr->fsm_last = d;
        shadow_out(tr, before, a, &o, d);
    }
}

static void feed(struct trace *tr, int ev, int v, long long t) {
    if (t < tr->fsm_last) t = tr->fsm_last; /* a forwarded edge read late: the machine's time never goes back */
    fsm_timers(tr, t);
    int before = tr->fsm.state;
    struct usb_fsm_out o;
    unsigned a = usb_fsm_step(&tr->fsm, ev, v, t, &o);
    tr->fsm_last = t;
    shadow_out(tr, before, a, &o, t);
}
/* a level fed only when it is not what the shadow last saw from any source */
static void feed_level(struct trace *tr, int which, int ev, int v, long long t) {
    if (v < 0 || tr->fed[which] == v) return;
    tr->fed[which] = v;
    feed(tr, ev, v, t);
}

/* ---- the cycles ---- */
void trace_burst(struct trace *tr, long long t) {
    tr->burst_until = t + UT_BURST_FOR_MS * MS;
    if (tr->next_sample > t + UT_BURST_MS * MS) tr->next_sample = t + UT_BURST_MS * MS;
    struct ut_cycle *c = &tr->cyc;
    if (!c->open) {
        cycle_clear(c);
        c->open = 1, ++c->n, c->start = t;
        snprintf(c->start_state, sizeof c->start_state, "%s", fsm_state(tr));
        tr->samples_in_burst = 0;
    }
    c->last = t;
}

static const char *cycle_type(const struct ut_cycle *c) {
    if (c->dev_add != UF_NEVER) return c->pcm_add != UF_NEVER ? "dac" : "accessory";
    if (c->vbus_on != UF_NEVER && c->online1 != UF_NEVER)
        return c->addr != UF_NEVER || c->conf != UF_NEVER ? "computer" : "charger";
    if (c->vbus_on != UF_NEVER && c->stock_on == UF_NEVER && c->stock_off == UF_NEVER) return "no-source";
    if (c->id_low != UF_NEVER && (c->stock_on != UF_NEVER || c->shadow_on != UF_NEVER)) return "id-low-powered";
    if (c->id_low != UF_NEVER) return "id-low";
    if (c->vbus_off != UF_NEVER || c->dev_rm != UF_NEVER) return "unplug";
    return "other";
}

static void cycle_close(struct trace *tr, long long t) {
    struct ut_cycle *c = &tr->cyc;
    char a[32], b[32], d[32], e[32], f[32], g[32], h[32], i[32], j[32], k[32], l[32], m[32], n[32], o[32], p[32];
    const char *stock = c->stock_grants ? "grant" : "none", *shadow = c->shadow_grants ? "grant" : "none";
    if (!c->stock_grants != !c->shadow_grants) {
        trace_line(tr, t, "shadow: disagree in cycle %d: stock=%s at %s, shadow=%s at %s", c->n, stock,
                   rel(a, c->stock_on, c->start), shadow, rel(b, c->shadow_on, c->start));
        c->disagreed = 1;
    }
    const char *type = cycle_type(c);
    trace_line(tr, t,
               "cycle n=%d at=%s type=%s from=%s vbus+=%s vbus-=%s id-=%s id+=%s on1=%s addr=%s conf=%s dev+=%s dev-=%s "
               "pcm+=%s stock-on=%s stock-off=%s shadow-on=%s shadow-replug=%s stock=%s shadow=%s %s%s",
               c->n, ts(p, c->start), type, c->start_state, rel(a, c->vbus_on, c->start), rel(b, c->vbus_off, c->start),
               rel(d, c->id_low, c->start), rel(e, c->id_high, c->start), rel(f, c->online1, c->start),
               rel(g, c->addr, c->start), rel(h, c->conf, c->start), rel(i, c->dev_add, c->start),
               rel(j, c->dev_rm, c->start), rel(k, c->pcm_add, c->start), rel(l, c->stock_on, c->start),
               rel(m, c->stock_off, c->start), rel(n, c->shadow_on, c->start), rel(o, c->shadow_replug, c->start),
               stock, shadow, c->disagreed ? "DISAGREE" : "agree", c->unsafe ? " UNSAFE" : "");
    char msg[200];
    snprintf(msg, sizeof msg, "usbtrace cycle %d %s: stock %s at %s s, shadow %s at %s s; %s%s", c->n, type, stock,
             rel(a, c->stock_on, c->start), shadow, rel(b, c->shadow_on, c->start),
             c->disagreed ? "disagree" : "agree", c->unsafe ? ", bus power with online 1" : "");
    ut_ulog(tr, c->disagreed || c->unsafe ? 'W' : 'I', msg);
    c->open = 0;
}

/* ---- the inputs ---- */
static const char *pin_name(int pin) { return pin == UT_PD17 ? "PD17" : pin == UT_PD18 ? "PD18" : "PD19"; }

void trace_edge(struct trace *tr, int pin, int level, long long t, const char *how) {
    trace_line(tr, t, "e %s %d%s%s", pin_name(pin), level, *how ? " " : "", how);
    trace_burst(tr, t);
    struct ut_cycle *c = &tr->cyc;
    if (pin == UT_PD17) {
        tr->last.pd17 = level;
        if (level) c->vbus_off = t;
        else first(&c->vbus_on, t);
        feed_level(tr, UT_FED_VBUS, UF_EV_VBUS, !level, t);
        tr->online_at1 = t + UT_ONLINE_AT1_MS * MS, tr->online_at2 = t + UT_ONLINE_AT2_MS * MS;
    } else if (pin == UT_PD18) {
        tr->last.pd18 = level;
        if (level) c->id_high = t;
        else first(&c->id_low, t);
        feed_level(tr, UT_FED_IDLOW, UF_EV_IDLOW, !level, t);
        tr->online_at1 = t + UT_ONLINE_AT1_MS * MS, tr->online_at2 = t + UT_ONLINE_AT2_MS * MS;
    } else
        tr->last.pd19 = level;
}

void trace_online(struct trace *tr, int online, long long t) {
    if (online < 0) return;
    trace_line(tr, t, "on %d", online);
    tr->last.online = online;
    if (online == 1) first(&tr->cyc.online1, t);
    feed(tr, UF_EV_ONLINE, online, t);
}

/* the value of KEY=... among a uevent's NUL-separated fields, or "" */
static const char *field(const char *msg, int n, const char *key) {
    int k = (int)strlen(key);
    for (int i = 0; i < n;) {
        const char *s = msg + i;
        int len = (int)strlen(s);
        if (len > k && !strncmp(s, key, k) && s[k] == '=') return s + k + 1;
        i += len + 1;
    }
    return "";
}
static const char *tail(const char *path) {
    const char *t = path;
    for (const char *p = path; *p; ++p)
        if (*p == '/') t = p + 1;
    return t;
}

void trace_uevent(struct trace *tr, const char *msg, int n, long long t) {
    if (n <= 0 || !strncmp(msg, "libudev", 7)) return; /* udev's own resends: the kernel's alone */
    const char *action = field(msg, n, "ACTION"), *sub = field(msg, n, "SUBSYSTEM"),
               *path = field(msg, n, "DEVPATH"), *type = field(msg, n, "DEVTYPE"), *name = tail(path);
    int add = !strcmp(action, "add"), rm = !strcmp(action, "remove");
    struct ut_cycle *c = &tr->cyc;
    if (!strcmp(sub, "usb")) {
        if (strcmp(type, "usb_device")) return; /* its interfaces: the device's own line says enough */
        trace_line(tr, t, "u %s usb %s", action, name);
        trace_burst(tr, t);
        if (!strcmp(name, "1-1") && (add || rm)) {
            if (add) first(&c->dev_add, t);
            else c->dev_rm = t;
            tr->last.dev = add;
            feed_level(tr, UT_FED_HOSTDEV, UF_EV_HOSTDEV, add, t);
        }
    } else if (!strcmp(sub, "sound")) {
        if (!strstr(name, "C1") && strcmp(name, "card1")) return; /* the Q2's own card is 0 */
        trace_line(tr, t, "u %s sound %s", action, name);
        trace_burst(tr, t);
        if (!strcmp(name, "card1") && add) first(&c->card_add, t);
        if (!strcmp(name, "pcmC1D0p") && (add || rm)) {
            if (add) first(&c->pcm_add, t);
            feed_level(tr, UT_FED_PCM, UF_EV_PCM, add, t);
        }
    } else if (!strcmp(sub, "block") || !strcmp(sub, "power_supply") || !strcmp(sub, "udc") ||
               !strcmp(sub, "extcon") || !strcmp(sub, "typec") || !strcmp(sub, "usb_role")) {
        trace_line(tr, t, "u %s %s %s", action, sub, name);
        if (strcmp(sub, "block")) trace_burst(tr, t);
    }
}

int ut_udc_addressed(const char *state) { return !strcmp(state, "addressed") || !strcmp(state, "configured"); }

void trace_udc(struct trace *tr, const char *state, long long t) {
    if (!strcmp(state, tr->udc)) return;
    int first_read = !tr->udc[0]; /* at the start or a wake: no movement */
    snprintf(tr->udc, sizeof tr->udc, "%s", state);
    trace_line(tr, t, "udc %s", state);
    if (!first_read) trace_burst(tr, t);
    if (!strcmp(state, "addressed")) first(&tr->cyc.addr, t);
    if (!strcmp(state, "configured")) first(&tr->cyc.conf, t);
    feed_level(tr, UT_FED_UDC, UF_EV_UDC, ut_udc_addressed(state), t);
}

/* "k=v" appended to b when the value changed (or always when all) */
static void put_int(char *b, int size, const char *k, int v, int old, int all) {
    if (!all && v == old) return;
    int n = (int)strlen(b);
    if (v < 0) snprintf(b + n, size - n, " %s=?", k);
    else snprintf(b + n, size - n, " %s=%d", k, v);
}
static void put_str(char *b, int size, const char *k, const char *v, const char *old, int all) {
    if (!all && !strcmp(v, old)) return;
    int n = (int)strlen(b);
    snprintf(b + n, size - n, " %s=%s", k, *v ? v : "?");
}
static void put_hex(char *b, int size, const char *k, long v, long old, int all) {
    if (!all && v == old) return;
    int n = (int)strlen(b);
    if (v < 0) snprintf(b + n, size - n, " %s=?", k);
    else snprintf(b + n, size - n, " %s=%08lx", k, (unsigned long)v);
}

void trace_sample(struct trace *tr, const struct ut_sample *s, long long t) {
    const struct ut_sample *o = &tr->last;
    int all = !tr->have_last;
    char b[256] = "s";
    put_int(b, sizeof b, "PD17", s->pd17, o->pd17, all);
    put_int(b, sizeof b, "PD18", s->pd18, o->pd18, all);
    put_int(b, sizeof b, "PD19", s->pd19, o->pd19, all);
    put_int(b, sizeof b, "PD14", s->pd14, o->pd14, all);
    put_int(b, sizeof b, "PD15", s->pd15, o->pd15, all);
    put_int(b, sizeof b, "PD16", s->pd16, o->pd16, all);
    put_int(b, sizeof b, "PC20", s->pc20, o->pc20, all);
    put_int(b, sizeof b, "PE22", s->pe22, o->pe22, all);
    put_int(b, sizeof b, "on", s->online, o->online, all);
    put_str(b, sizeof b, "id", s->id, o->id, all);
    put_str(b, sizeof b, "mode", s->mode, o->mode, all);
    put_str(b, sizeof b, "conn", s->conn, o->conn, all);
    put_int(b, sizeof b, "dev", s->dev, o->dev, all);
    put_int(b, sizeof b, "snd", s->snd, o->snd, all);
    put_hex(b, sizeof b, "pcr", s->pcr, o->pcr, all);
    put_hex(b, sizeof b, "rdt", s->rdt, o->rdt, all);
    int changed = b[1] != 0;
    struct ut_cycle *c = &tr->cyc;
    int moved = !all && (s->pd17 != o->pd17 || s->pd18 != o->pd18 || s->pd16 != o->pd16 || s->pc20 != o->pc20 ||
                         s->online != o->online ||
                         s->dev != o->dev || s->snd != o->snd);
    struct ut_sample prev = *o;
    tr->last = *s, tr->have_last = 1;
    if (changed) trace_line(tr, t, "%s", b);
    if (moved) trace_burst(tr, t);
    /* the levels the edges did not already give, for the shadow */
    if (s->pd17 >= 0 && (all || s->pd17 != prev.pd17)) {
        if (s->pd17) c->vbus_off = t;
        else if (!all) first(&c->vbus_on, t);
    }
    if (s->pd18 >= 0 && !all && s->pd18 != prev.pd18) {
        if (s->pd18) c->id_high = t;
        else first(&c->id_low, t);
    }
    /* the bus power first: a PD17 low in the same sample is then known to be its own */
    if (s->pc20 >= 0 && (all || s->pc20 != prev.pc20)) {
        if (!all) trace_line(tr, t, "stock otg-%s", s->pc20 ? "on" : "off");
        feed_level(tr, UT_FED_POWER, UF_EV_POWER, s->pc20, t);
        if (!all && s->pc20) {
            char when[32];
            first(&c->stock_on, t), ++c->stock_grants;
            if (!tr->fsm.granted && (c->shadow_on == UF_NEVER || t - c->shadow_on > UT_AGREE_MS * MS)) {
                trace_line(tr, t, "shadow: disagree stock=otg-on at %s shadow=none (%s)", ts(when, t), fsm_state(tr));
                c->disagreed = 1;
            }
        } else if (!all)
            c->stock_off = t;
    }
    int vbus_fed = tr->fed[UT_FED_VBUS];
    if (s->pd17 >= 0) feed_level(tr, UT_FED_VBUS, UF_EV_VBUS, !s->pd17, t);
    if (s->online >= 0 && (tr->fed[UT_FED_VBUS] != vbus_fed || s->online != prev.online)) {
        if (s->online == 1 && !all) first(&c->online1, t);
        feed(tr, UF_EV_ONLINE, s->online, t); /* the read the VBUS change asks for */
    }
    if (s->pd18 >= 0) feed_level(tr, UT_FED_IDLOW, UF_EV_IDLOW, !s->pd18, t);
    if (s->dev >= 0) feed_level(tr, UT_FED_HOSTDEV, UF_EV_HOSTDEV, s->dev, t);
    if (s->pc20 == 1 && s->online == 1 && !c->unsafe) {
        trace_line(tr, t, "unsafe: bus power on with online 1");
        c->unsafe = 1;
        trace_burst(tr, t);
    }
    ++tr->samples_in_burst;
    tr->next_sample = t + (t < tr->burst_until ? UT_BURST_MS : UT_IDLE_MS) * MS;
}

void trace_slow(struct trace *tr, const struct ut_slow *s, long long t) {
    int all = !tr->have_slow;
    char b[200] = "axp";
    for (int i = 0; i < UT_AXP_REGS; ++i)
        if (all || s->axp[i] != tr->slow.axp[i]) {
            int n = (int)strlen(b);
            if (s->axp[i] < 0) snprintf(b + n, sizeof b - n, " %02x=?", ut_axp_regs[i]);
            else snprintf(b + n, sizeof b - n, " %02x=%02x", ut_axp_regs[i], s->axp[i]);
        }
    if (b[3]) trace_line(tr, t, "%s", b);
    char r[200] = "otg";
    put_hex(r, sizeof r, "gotgctl", s->gotgctl, tr->slow.gotgctl, all);
    put_hex(r, sizeof r, "gintsts", s->gintsts, tr->slow.gintsts, all);
    put_hex(r, sizeof r, "hprt0", s->hprt0, tr->slow.hprt0, all);
    if (r[3]) trace_line(tr, t, "%s", r);
    tr->slow = *s, tr->have_slow = 1;
}

void trace_wake(struct trace *tr, long long slept_ms, long long t) {
    unsigned ms;
    unsigned long s = (unsigned long)ut_udiv((unsigned long long)(slept_ms < 0 ? 0 : slept_ms), 1000u, &ms);
    trace_line(tr, t, "wake slept=%lu.%03u", s, ms);
    feed(tr, UF_EV_WAKE, 0, t);
    for (int i = 0; i < UT_FED_N; ++i) tr->fed[i] = -1;
    tr->have_last = 0, tr->have_slow = 0, tr->udc[0] = 0; /* the next sample is written whole and fed again */
    trace_burst(tr, t);
}

void trace_tick(struct trace *tr, long long t) {
    fsm_timers(tr, t);
    if (tr->cyc.open && t >= tr->burst_until) cycle_close(tr, t);
    if (t >= tr->next_clock) {
        trace_line(tr, t, "clock wall=%ld", t_wall());
        tr->next_clock = t + UT_CLOCK_MS * MS;
    }
}

long long trace_next_wake(const struct trace *tr) {
    long long w = tr->next_sample;
    long long d = usb_fsm_deadline(&tr->fsm);
    if (d != UF_NEVER && d < w) w = d;
    if (tr->online_at1 != UF_NEVER && tr->online_at1 < w) w = tr->online_at1;
    if (tr->online_at2 != UF_NEVER && tr->online_at2 < w) w = tr->online_at2;
    if (tr->cyc.open && tr->burst_until < w) w = tr->burst_until;
    return w;
}

int trace_rotate_needed(long size) { return size >= UT_FILE_MAX; }

unsigned long long ut_udiv(unsigned long long n, unsigned d, unsigned *rem) {
    unsigned long long q = 0, r = 0;
    for (int i = 63; i >= 0; --i) {
        r = r << 1 | (n >> i & 1);
        if (r >= d) r -= d, q |= 1ULL << i;
    }
    if (rem) *rem = (unsigned)r;
    return q;
}

/* ---- parsers ---- */
int ut_regdump_word(const char *text, const char *name, long *v) {
    int k = (int)strlen(name);
    for (const char *s = text; (s = strstr(s, name)); s += k) {
        if (s != text && s[-1] != '\n') continue;
        const char *p = s + k;
        while (*p == ' ') ++p;
        if (*p++ != '=') continue;
        while (*p == ' ') ++p;
        if (p[0] != '0' || (p[1] != 'x' && p[1] != 'X')) continue;
        p += 2;
        unsigned long x = 0;
        int digits = 0;
        for (;; ++p, ++digits) {
            int c = *p, d = c >= '0' && c <= '9' ? c - '0' : c >= 'a' && c <= 'f' ? c - 'a' + 10
                               : c >= 'A' && c <= 'F' ? c - 'A' + 10 : -1;
            if (d < 0) break;
            x = x << 4 | (unsigned long)d;
        }
        if (!digits) continue;
        *v = (long)(x & 0x7fffffffUL) | (long)(x & 0x80000000UL); /* the word as it is */
        return 1;
    }
    return 0;
}

int ut_cards_second(const char *text) {
    for (const char *s = text; *s;) {
        const char *p = s;
        while (*p == ' ') ++p;
        if (p[0] == '1' && p[1] == ' ' && strchr(p, '[') && (p[2] == '[' || p[2] == ' ')) return 1;
        while (*s && *s != '\n') ++s;
        if (*s) ++s;
    }
    return 0;
}

void ut_event_request(struct ut_event_request *r, unsigned line) {
    memset(r, 0, sizeof *r);
    r->lineoffset = line;
    r->handleflags = UT_GPIOHANDLE_REQUEST_INPUT;
    r->eventflags = UT_GPIOEVENT_BOTH_EDGES;
    memcpy(r->consumer_label, "q2-usbtrace", 12);
    r->fd = -1;
}

void ut_smbus_read(struct ut_smbus_args *a, unsigned char reg, unsigned char *data) {
    a->read_write = UT_I2C_SMBUS_READ;
    a->command = reg;
    a->size = UT_I2C_SMBUS_BYTE_DATA;
    a->data = data;
}
