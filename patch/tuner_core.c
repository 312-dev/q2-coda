/* Radio's receiver's decisions (tuner_core.h, docs/internals.md#radio-static). */
#include "tuner_core.h"

#define RESTARTS 5 /* a stream that ends is started again this often, a second more each time */

int tuner_take(tuner_feed *f, unsigned have) {
    if (!f->primed && have >= TUNER_PRIME) f->primed = 1, f->stalled = 0;
    if (f->primed && have < TUNER_FRAMES) f->primed = 0, f->stalled = 1; /* dry: the carrier drops */
    return f->primed;
}

void tuner_ended(tuner_feed *f) {
    ++f->tries;
    f->primed = f->stalled = 0;
}

int tuner_retry_due(const tuner_feed *f, unsigned since_ms) {
    return f->tries <= RESTARTS && since_ms > 1000u * (unsigned)f->tries;
}
