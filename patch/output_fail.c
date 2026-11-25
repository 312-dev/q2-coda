/* A song that will not play, said once, never a loop round the queue
 * (docs/internals.md#output-failure).
 *
 * q2player reports a failed song with {player-status\error}, after a file for stock's dialog timer
 * (on_wm_timer_dialog, 0x4f4d18): /tmp/notfile or /tmp/notsupport, either of which toasts and skips
 * to the next song (player_next_music, 0x4f4dd4), or OUTPUT_FAILED when the output would not open
 * or take audio, which it leaves alone. That last is no property of the song: every next song would
 * meet the same output, so the player stays stopped on it, one toast says why, and the user's next
 * Play tries again. A real run of songs that fail to open or decode skips on until the
 * UNPLAYABLE_RUN-th skip in a row, which is refused, the player left stopped with one toast. Any
 * start that is not that skip (a press, a pick, a song's natural end) begins the count again. */
#include "navigation.h"

#define OUTPUT_FAILED "/tmp/q2outfail" /* player/q2player.h OUTPUT_FAILED_FILE */
#define UNPLAYABLE_RUN 3
#define RUN_TEXT "3" /* UNPLAYABLE_RUN in words */

static struct {
    int run;      /* the unplayable skips since the last other start */
    int skipping; /* inside one of them (output_fail_skip to output_fail_start) */
} of __attribute__((section(".scratch")));

/* playback_poll, each UI pass: the player stopped by an output that failed. */
void output_fail_poll(void) {
    if (mclGetPlayStatus() != 1 || access(OUTPUT_FAILED, 0)) return;
    unlink(OUTPUT_FAILED);
    ulog('W', "player", "stopped: the output would not open or take audio, so no next song");
    toast_later("bttoast_warn", "Audio output unavailable");
}

/* playback.c ringnav_change, for stock's skip past a song that would not play: 0 to let it go on,
 * 1 when it is the run's UNPLAYABLE_RUN-th and is refused. */
int output_fail_skip(void) {
    if (++of.run < UNPLAYABLE_RUN) return of.skipping = 1, 0;
    of.run = of.skipping = 0;
    ulog('W', "player", "stopped: " RUN_TEXT " songs in a row would not play");
    toast_later("bttoast_warn", "Couldn't play " RUN_TEXT " songs in a row");
    return 1;
}
/* playback.c ringnav_change, once the skip's change has run. */
void output_fail_skipped(void) { of.skipping = 0; }

/* ringnav_start_player, every start of the Library's player: one that is not the skip's ends the
 * run. */
void output_fail_start(void) {
    if (!of.skipping) of.run = 0;
}
