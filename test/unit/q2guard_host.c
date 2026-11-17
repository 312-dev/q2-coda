/* test_q2guard.py's handles on patch/q2guard_core.c: a guard, a log and a top table made here, and
 * one step fed as plain ints. */
#include <stdlib.h>

#include "../../patch/q2guard.h"

struct guard *guard_new(void) { return calloc(1, sizeof(struct guard)); }
struct heat_log *log_new(void) { return calloc(1, sizeof(struct heat_log)); }
struct top_table *table_new(void) { return calloc(1, sizeof(struct top_table)); }
int log_used(const struct heat_log *l) { return l->used; }
const char *log_ram(const struct heat_log *l) { return l->ram; }
int guard_load(const struct guard *g) { return g->load; }

/* One step; out: kill_player, flush, power_off. The event. */
int feed(struct guard *g, unsigned now, int temp, int busy, int cancel, int acked, int saved, int *out) {
    struct guard_in in = { now, temp, busy, cancel, acked, saved };
    struct guard_out o;
    guard_step(g, &in, &o);
    out[0] = o.kill_player, out[1] = o.flush, out[2] = o.power_off;
    return o.event;
}

/* A line with every field set (or none, empty): its text. */
int line(char *out, int size, int empty) {
    struct heat_sample s = { 0 };
    if (empty) {
        s.wall = 0, s.temp = s.raw_temp = GUARD_NONE;
        s.level = s.mv = s.cable = s.screen = s.bright = s.wifi = s.bt = s.mhz = s.busy = -1;
        s.demo_threads = s.procs = -1;
        return heat_line(out, size, &s);
    }
    struct heat_sample f = { 1795442585, 1234, 415, 27, 87, 4105, "Charging", 1, "configured", "adb", 1, 80,
                             1, 0, -1, 23, { { "hciplayer", 123 }, { "demo", 80 }, { "adbd", 3 } }, 27, 63, "ok" };
    return heat_line(out, size, &f);
}
