/* Shuffle's waits (docs/internals.md#loading-wait): Now Playing's Shuffle turned on and the
 * Library's Shuffle page (Shuffle Songs, Albums, Folders) build a play order over the whole queue,
 * on the owner's library about 9,000 songs, so each shows the loading wait (list_marks.c) while
 * that runs past LOAD_SHOW_MS.
 *
 * The order's build is a job of three calls (shuffle_order_*): start begins the build the queue
 * and the mode now call for (what playback.c shuffle_own does at the next UI pass) and answers 1
 * while it runs; poll, on the UI thread each frame, takes its next slice or looks at its worker and
 * answers 1 once the order is in place; cancel gives it up, keeping nothing of it (stock's own
 * shuffle plays meanwhile). Here they are synchronous: start builds the order at once
 * (playback_shuffle_now) under load_sync's overlay and answers 0, so poll and cancel are never
 * reached. */
#include "navigation.h"
#include "playback.h"

#define SHUFFLING "Shuffling songs\xe2\x80\xa6"

int shuffle_order_start(void) {
    int shown = load_sync_begin(SHUFFLING);
    playback_shuffle_now();
    if (shown) load_sync_end();
    return 0;
}
int shuffle_order_poll(void) { return 1; }
void shuffle_order_cancel(void) {}

/* Now Playing's modes before Shuffle went on (np_mode_set's on and rep), put back if its wait is
 * given up: the user is left where they were. */
static int sw_was[2] __attribute__((section(".scratch")));
static void np_given_up(void) {
    shuffle_order_cancel();
    np_mode_set(sw_was[0], sw_was[1]);
}
/* np_mode_set turned stock Shuffle on from a mode without it (on 0, repeat rep), on the UI thread:
 * its order built under the loading wait over the top window. */
void shuffle_wait_np(int rep) {
    if (pthread_self() != ui_thread) return; /* the next UI pass's shuffle_own takes it */
    sw_was[0] = 0, sw_was[1] = rep;
    if (shuffle_order_start()) load_wait(page_in_use(), SHUFFLING, shuffle_order_poll, np_given_up);
}

/* The Shuffle page's rows (library_lists.c sp_pick): the library's songs loaded by one stock query
 * and the queue played, which the UI thread cannot slice, under load_sync's overlay; then the
 * order's build, waited for if it still runs. */
void shuffle_wait_pick(int (*pick)(void *, void *), void *ctx) {
    static const char *const what[] = { SHUFFLING, "Shuffling albums\xe2\x80\xa6",
                                        "Shuffling folders\xe2\x80\xa6" };
    int shown = load_sync_begin(what[(unsigned long)ctx < 3 ? (unsigned long)ctx : 0]);
    pick(ctx, 0);
    if (shown) load_sync_end();
    if (!ctx && shuffle_order_start())
        load_wait(page_in_use(), SHUFFLING, shuffle_order_poll, shuffle_order_cancel);
}
