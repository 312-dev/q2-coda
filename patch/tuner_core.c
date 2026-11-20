/* Radio's receiver's decisions (tuner_core.h, docs/internals.md#radio-static). */
#include "tuner_core.h"

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
