/* Radio's receiver's decisions and the dial's click (tuner_core.h, docs/internals.md#radio-static). */
#include "tuner_core.h"

double exp(double), sin(double), cos(double); /* libm, as fmrx.c's: no system headers on the Q2 */

/* A stream that ends is started again for as long as its station is tuned: after 1 s, then 2, 4 and
 * 8, then every RETRY_MAX_MS (a network that is gone for long is tried at that pace, and one that
 * comes back is found within it; demo's nudge, tuner_nudge, finds it at once when the Wi-Fi link
 * returns). */
#define RETRY_MAX_MS 15000u

int tuner_take(tuner_feed *f, unsigned have) {
    if (!f->primed && have >= TUNER_PRIME)
        f->primed = 1, f->stalled = 0, f->tries = 0; /* sounding: count anew */
    if (f->primed && have < TUNER_FRAMES)
        f->primed = 0, f->stalled = 1; /* dry: the carrier drops */
    return f->primed;
}

void tuner_ended(tuner_feed *f) {
    ++f->tries;
    f->primed = f->stalled = 0;
}

void tuner_nudge(tuner_feed *f) { f->tries = 0; }

unsigned tuner_wait_ms(const tuner_feed *f) {
    unsigned wait = 1000u << (f->tries > 1 ? (f->tries < 5 ? f->tries - 1 : 4) : 0);
    return wait < RETRY_MAX_MS ? wait : RETRY_MAX_MS;
}

int tuner_retry_due(const tuner_feed *f, unsigned since_ms) { return since_ms >= tuner_wait_ms(f); }

void tuner_click_start(tuner_click *c, int tenths, float fs) {
    /* RBJ's highpass at the Web Audio biquad's default Q (1 dB, 1.122) */
    float hz = tenths > 10 ? 900.0f : 2500.0f, w = 6.2831853f * hz / fs, q = 1.122f;
    float cw = (float)cos(w), al = (float)sin(w) / (2 * q), a0 = 1 + al;
    c->b0 = (1 + cw) / 2 / a0, c->b1 = -(1 + cw) / a0, c->b2 = c->b0;
    c->a1 = -2 * cw / a0, c->a2 = (1 - al) / a0;
    c->x1 = c->x2 = c->y1 = c->y2 = 0;
    c->n = c->left = (int)(fs * 0.004f + 0.5f);
    c->gain = 0.018f * (float)tenths;
    if (!c->rng) c->rng = 0x9e3779b9u;
}

void tuner_click_mix(tuner_click *c, float *out, int frames) {
    for (int i = 0; i < frames && (c->left > 0 || c->y1 != 0 || c->y2 != 0); ++i) {
        float x = 0;
        if (c->left > 0) {
            c->rng = c->rng * 1664525u + 1013904223u;
            int k = c->n - c->left--;
            x = ((float)(c->rng >> 8) / 8388608.0f - 1) * (float)exp(-5.0 * k / c->n);
        }
        float y = c->b0 * x + c->b1 * c->x1 + c->b2 * c->x2 - c->a1 * c->y1 - c->a2 * c->y2;
        c->x2 = c->x1, c->x1 = x, c->y2 = c->y1, c->y1 = y;
        if (c->left <= 0 && y * y < 1e-14f && c->y2 * c->y2 < 1e-14f) c->y1 = c->y2 = 0; /* rung out */
        out[2 * i] += c->gain * y, out[2 * i + 1] += c->gain * y;
    }
}
