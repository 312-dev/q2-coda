/* test_q2guard.py's handles on patch/q2guard_core.c: a guard, a log and a top table made here, and
 * one step fed as plain ints. */
#include <stdlib.h>

#include "../../patch/q2guard.h"

struct guard *guard_new(void) { return calloc(1, sizeof(struct guard)); }
/* q2guard.c's role, as the tests play it: card_mounted is false while lines are added (the watching process
 * only holds them) and true for the flush (the writing child). */
static int card_up;
int card_mounted(void) { return card_up; }
void card(int up) { card_up = up; }
card_log *log_new(const char *dir) {
    char *path = malloc(256);
    snprintf(path, 256, "%s/q2-heat.0", dir);
    return card_log_at(calloc(1, sizeof(card_log)), path, HEAT_RAM);
}
/* a log of another name in dir, its ring cap bytes (test_q2bt.py's q2-bt.0) */
card_log *log_named(const char *dir, const char *name, unsigned cap) {
    char *path = malloc(256);
    snprintf(path, 256, "%s/%s", dir, name);
    return card_log_at(calloc(1, sizeof(card_log)), path, cap);
}
int log_dropped(const card_log *l) { return (int)l->dropped; }
struct top_table *table_new(void) { return calloc(1, sizeof(struct top_table)); }
int log_used(const card_log *l) { return (int)l->len; }
const char *log_ram(const card_log *l) { return l->ring; }
int guard_load(const struct guard *g) { return g->load; }

/* One step; out: kill_player, flush, power_off. The event. slept: a suspend since the last step. */
int feed_slept(struct guard *g, unsigned now, int temp, int busy, int cancel, int acked, int saved, int charge,
               int slept, int *out) {
    struct guard_in in = { now, temp, busy, cancel, acked, saved, charge, slept };
    struct guard_out o;
    guard_step(g, &in, &o);
    out[0] = o.kill_player, out[1] = o.flush, out[2] = o.power_off;
    return o.event;
}
int feed(struct guard *g, unsigned now, int temp, int busy, int cancel, int acked, int saved, int charge, int *out) {
    return feed_slept(g, now, temp, busy, cancel, acked, saved, charge, 0, out);
}

/* A line with every field set (or none, empty): its text. */
int line(char *out, int size, int empty) {
    struct heat_sample s = { 0 };
    if (empty) {
        s.wall = 0, s.temp = s.raw_temp = s.die = GUARD_NONE, s.adc = -1;
        s.level = s.mv = s.cable = s.screen = s.bright = s.wifi = s.bt = s.mhz = s.busy = -1;
        s.demo_threads = s.procs = -1;
        return heat_line(out, size, &s);
    }
    struct heat_sample f = { 1795442585, 1234, 415, 27, 415, 6884, 87, 4105, "Charging", 1, "configured", "adb", 1, 80,
                             1, 0, -1, 23, { { "hciplayer", 123 }, { "demo", 80 }, { "adbd", 3 } }, 27, 63, "ok" };
    return heat_line(out, size, &f);
}
