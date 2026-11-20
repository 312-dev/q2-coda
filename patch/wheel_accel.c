/* The wheel's momentum on a long list (wheel_accel.h, docs/internals.md#wheel-acceleration), as an
 * iPod classic's: a slow turn moves a row a tick, a sustained spin more rows a tick, and a fast
 * spin that goes on, on a long list, a whole letter a tick, back to rows as soon as it slows.
 * No state but the caller's; navigation's lists and Cover Flow each keep their own. */
#include "wheel_accel.h"

int wheel_accel(wheel_accel_t *a, void *key, int dir, unsigned now, int length) {
    unsigned gap = now - a->last;
    if (a->key == key && a->dir == dir && a->last && gap <= WA_RUN_MS)
        a->run += gap;
    else
        a->run = 0;
    a->key = key, a->dir = dir, a->last = now ? now : 1;
    if (length >= WA_LETTER_ROWS && a->run >= WA_LETTER_MS && gap <= WA_FAST_GAP_MS)
        return WA_LETTER;
    if (a->run < WA_FIRST_MS) return 1;
    int rows = 2 + (int)((a->run - WA_FIRST_MS) / WA_RAMP_MS);
    return rows > WA_MAX_ROWS ? WA_MAX_ROWS : rows;
}
