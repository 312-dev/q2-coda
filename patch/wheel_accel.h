/* The wheel's momentum on a long list (docs/internals.md#wheel-acceleration): a list-agnostic step
 * from timed ticks, which a list (navigation's rows) or Cover Flow's covers turn into their move.
 * Feed each tick of the wheel; the answer is rows to move (1 for a slow or new turn, more as a
 * sustained spin goes on) or WA_LETTER, a whole letter's jump, once the spin has been fast for a
 * while on a list long enough. A pause, a reversal or another list starts over. */
#ifndef WHEEL_ACCEL_H
#define WHEEL_ACCEL_H

#define WA_RUN_MS 140      /* a longer gap ends the run (navigation.c WHEEL_RUN_MS) */
#define WA_FIRST_MS 300    /* one row a tick for this long into a run, */
#define WA_RAMP_MS 200     /* then one row more per this much more, */
#define WA_MAX_ROWS 8      /* up to this many a tick */
#define WA_LETTER_MS 1200  /* letter jumps once the run has lasted this long, */
#define WA_FAST_GAP_MS 60  /* while ticks come this close (about 17 a second or faster), */
#define WA_LETTER_ROWS 200 /* on a list this long or longer */
#define WA_LETTER (-1)

typedef struct {
    void *key;     /* the list the run is on */
    unsigned last; /* the last tick's time, ms */
    unsigned run;  /* how long the run has gone on, ms */
    int dir;       /* its direction, 1 or -1 */
} wheel_accel_t;

/* One tick in dir (1 or -1) at now (ms) on list key of length items: rows to move, 1 or more, or
 * WA_LETTER. */
int wheel_accel(wheel_accel_t *a, void *key, int dir, unsigned now, int length);

#endif
