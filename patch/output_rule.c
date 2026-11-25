/* No playback without an output (docs/internals.md#no-playback-without-an-output). Stock V1.36's
 * Headphones Required (g_block_withoutphone_flag, PLAYSET WITHOUTPHONE) refuses a start with nothing
 * to play to, and its gate, check_forbid_play_no_headset, already stands before every stock start and
 * Q2 Muse's own local ones. Q2 Muse holds the switch on whatever config.ini says (output_rule_config,
 * after config_init) and hides its row, so the rule cannot be turned off; test mode (g_testmode_flag)
 * still passes, as stock's gate lets it.
 *
 * Stock's answer to "is there an output" (has_valid_output_device: either jack, a Bluetooth link with
 * its codec up, a USB DAC) does not know the car's USB audio, the player's way 3, which q2-iap runs
 * while the car asks for audio. ringnav_valid_output adds it (usb_car.c usb_car_output), so the car's
 * Play is not refused and stock's restarts after an output change (player_reconfig,
 * player_save_start) do not pause under it.
 *
 * Stock asks its gate inside Now Playing's init (playing_page_init), once the navigator has made
 * the page: refused, the page closes itself, but it has already been laid out with its asset's
 * placeholder title and artist (Shanling's slogan, "Shanling") and slides in under the toast before
 * it goes (the owner's report, 2026-11-22). ringnav_nav_to asks the same question at the
 * navigator's door instead, so a refused start opens nothing. */
#include "navigation.h"

void output_rule_config(void) { g_block_withoutphone_flag = 1; }

int ringnav_valid_output(void) { return stock_valid_output_trampoline() || usb_car_output(); }

/* navigator_to_with_context, every page the navigator opens (navigator_switch_to_with_context's
 * too): Now Playing with a track to start, ctx {queue, index, class, mode} with a class other than
 * 0xff, is how a list row's tap or Centre, a song in Search or Cover Flow and play_folder start a
 * queue. Refused (stock's gate, which shows its toast), the navigator is not asked: the list stays
 * with its selection, and nothing is loaded, so the song playing or paused, the queue, its place
 * and a server song's app (catalogue.c catalogue_load, reached only from the page's player_start)
 * are as they were. The condition is playing_page_init's own (0x53e4a8), asked earlier. */
int ringnav_nav_to(const char *name, const int *ctx) {
    if (upnext_instead(name)) return STOP; /* stock's queue page: Up Next in its place (upnext.c) */
    if (name && ctx && !tk_strcmp(name, "playing_page") && ctx[2] != 0xff &&
        check_forbid_play_no_headset(0))
        return STOP;
    return stock_nav_to_trampoline(name, ctx);
}
