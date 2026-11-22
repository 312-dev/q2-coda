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
 * player_save_start) do not pause under it. */
#include "navigation.h"

void output_rule_config(void) { g_block_withoutphone_flag = 1; }

int ringnav_valid_output(void) { return stock_valid_output_trampoline() || usb_car_output(); }
