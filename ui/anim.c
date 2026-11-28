/* The frame clock and what moves by it (docs/ui-rewrite.md#motion). Every motion is a function of
 * the frame's time, never of how many frames were drawn: a frame that comes late shows the motion
 * where it should be by then, so a dropped frame costs a step's smoothness and never the
 * motion's length. */
#include <math.h>
#include <time.h>

#include "q2ui.h"

static uint64_t frame_us;
static int faked;

uint64_t mono_us(void) {
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return (uint64_t)t.tv_sec * 1000000u + (uint64_t)t.tv_nsec / 1000u;
}
uint64_t cpu_ns(void) {
    struct timespec t;
    clock_gettime(CLOCK_PROCESS_CPUTIME_ID, &t);
    return (uint64_t)t.tv_sec * 1000000000u + (uint64_t)t.tv_nsec;
}
uint64_t ui_time(void) { return frame_us; }
void ui_clock_read(void) {
    if (!faked) frame_us = mono_us();
}
void ui_clock_fake(uint64_t us) { faked = 1, frame_us = us; }

/* The closed form of x'' = -w^2 (x - to) - 2 z w x', so a step of any length lands where many
 * small ones would. At rest within 1/100 px and 1 px/s it snaps to its target and stops. */
int spring_step(spring_t *s, float dt, float response, float damping) {
    if (!spring_moving(s)) return 0;
    if (dt <= 0) return 1;
    double w = 6.283185307 / (double)response, z = (double)damping, t = (double)dt, d = s->x - s->to, x, v;
    if (z >= 1) {
        double c2 = s->v + w * d, e = exp(-w * t);
        x = (d + c2 * t) * e;
        v = (c2 - w * (d + c2 * t)) * e;
    } else {
        double wd = w * sqrt(1 - z * z), e = exp(-z * w * t);
        double c2 = (s->v + z * w * d) / wd, c = cos(wd * t), sn = sin(wd * t);
        x = e * (d * c + c2 * sn);
        v = e * ((c2 * wd - z * w * d) * c - (d * wd + z * w * c2) * sn);
    }
    if (fabs(x) < 0.01 && fabs(v) < 1.0) {
        s->x = s->to, s->v = 0;
        return 0;
    }
    s->x = s->to + x, s->v = v;
    return 1;
}

float ease_cubic_out(float t) {
    t = 1 - clampf(t, 0, 1);
    return 1 - t * t * t;
}
float ease_cubic_in(float t) {
    t = clampf(t, 0, 1);
    return t * t * t;
}

uint32_t rgb_mix(uint32_t a, uint32_t b, float t) {
    unsigned k = (unsigned)(clampf(t, 0, 1) * 256 + 0.5f);
    uint32_t rb = ((b & 0xff00ff) * k + (a & 0xff00ff) * (256 - k)) >> 8 & 0xff00ff;
    uint32_t g = ((b & 0xff00) * k + (a & 0xff00) * (256 - k)) >> 8 & 0xff00;
    return rb | g;
}
