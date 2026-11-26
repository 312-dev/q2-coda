/* Jack detect (docs/internals.md#jack-detect): an unplugged read of a headphone jack's pin counts
 * only once it lasts. Stock's jack poll, check_headset_status (0x4f335c, once a second from
 * on_wm_idle_status), reads PA07 (3.5 mm) and PA08 (4.4 mm) through toolsGpioGetValue, 0 plugged,
 * and at a change sets g_po_status or g_bal_status and calls config_po_output or config_bal_output,
 * whose unplug pauses what plays. Powering the DAC up at Play reads PA07 unplugged for about 120 ms
 * with a cable in (the user's Q2, 2026-11-25): a poll inside that window paused the song just
 * started and took the headphones glyph away.
 *
 * jack_read stands between stock's read and the poll (usb_otg.c ringnav_gpio_value, the hook on
 * toolsGpioGetValue): a first unplugged read where the jack was plugged answers plugged, and the
 * unplug goes through only when a read at least JACK_UNPLUG_MS later is still unplugged. With
 * stock's one poll a second that is the next poll, so a real unplug pauses about a second later
 * than stock's; a plug goes through at once. Each change, and each unplugged read that did not
 * last, is a line in the Vamp log. Only check_headset_status reads these two pins, on the UI
 * thread.
 *
 * Once PA07 has read unplugged with the DAC powered, it reads unplugged with the cable back in for
 * as long as the DAC stays on and muted, and true again about 0.6 s after it powers off (the
 * user's Q2, 2026-11-25, Build J). Stock's check_dacoff_state (0x4f3f98), just before this poll
 * in each on_wm_idle_status pass, powers the DAC off only when g_dacoff_time, its count of passes
 * not playing, reaches JACK_DACOFF_AT, so after an unplug's pause a plug back in went unseen for
 * about 31 s. An unplug of the Library's player that leaves no jack in (dac_early) sets the count
 * to its last step: the next pass, a second on, powers the DAC off under every condition stock's
 * own check holds (nothing playing, no AirPlay, no USB mode), the pause landed by then. A pause
 * with a cable in is not touched, so its resume stays instant, and Play powers the DAC back up as
 * after stock's 31 s (player_play_pause's config_outputchannel(g_headset_output, 2)). */
#include "navigation.h"

static struct {
    unsigned char known;   /* a read has been answered */
    unsigned char out;     /* the last answer: 1 unplugged */
    unsigned char pending; /* an unplugged read held back since at */
    unsigned at;
} jack[2] __attribute__((section(".scratch")));
/* dac_early set the DAC's count for the unplug of this jack, plus one; 0 none waiting */
static unsigned char dac_for __attribute__((section(".scratch")));

static const char *const jack_said[2][3] = {
    { "3.5 mm jack plugged", "3.5 mm jack unplugged",
      "3.5 mm jack: an unplugged read that did not last, ignored" },
    { "4.4 mm jack plugged", "4.4 mm jack unplugged",
      "4.4 mm jack: an unplugged read that did not last, ignored" },
};

static const char *const dac_off_said[2][2] = {
    { "3.5 mm jack unplugged with no jack left: the DAC powered off now, not 31 s on, so a plug "
      "back in is read",
      "3.5 mm jack unplugged with no jack left: the DAC kept on (in use or a key), so a plug back "
      "in is read once it powers off" },
    { "4.4 mm jack unplugged with no jack left: the DAC powered off now, not 31 s on, so a plug "
      "back in is read",
      "4.4 mm jack unplugged with no jack left: the DAC kept on (in use or a key), so a plug back "
      "in is read once it powers off" },
};

/* An unplug of jack i went through. With the other jack out and the Library's player the one
 * stock's unplug pauses (no radio, video, Spotify or card game holding the DAC), and the DAC on
 * (g_dacoff_time not -1), stock's DAC-off check gets its last step. */
static void dac_early(int i) {
    if (!jack[1 - i].known || !jack[1 - i].out || media_owner() != 1 || addon_game_audio()) return;
    if (I(g_dacoff_time, 0) < 0 || I(g_dacoff_time, 0) >= JACK_DACOFF_AT - 1) return;
    I(g_dacoff_time, 0) = JACK_DACOFF_AT - 1;
    dac_for = (unsigned char)(i + 1);
}

/* The pass after dac_early: the DAC off (-1), or the count started again (a key, a Play, a
 * player that holds it), is said once. */
static void dac_said(void) {
    int left = I(g_dacoff_time, 0);
    if (!dac_for || left == JACK_DACOFF_AT - 1) return;
    ulog('I', "jack", left < 0 ? dac_off_said[dac_for - 1][0] : dac_off_said[dac_for - 1][1]);
    dac_for = 0;
}

/* What stock's poll is given for pin name, raw being its reading. */
int jack_read(const char *name, int raw) {
    int i = !name ? -1 : !strcmp(name, "PA07") ? 0 : !strcmp(name, "PA08") ? 1 : -1;
    if (i < 0) return raw;
    dac_said();
    int out = raw != 0;
    unsigned now = time_now_ms();
    if (jack[i].known && !jack[i].out && out) {
        if (!jack[i].pending) {
            jack[i].pending = 1;
            jack[i].at = now;
            return 0;
        }
        if (now - jack[i].at < JACK_UNPLUG_MS) return 0;
    }
    if (jack[i].pending && !out) ulog('I', "jack", jack_said[i][2]);
    jack[i].pending = 0;
    int unplugged = jack[i].known && !jack[i].out && out;
    if (!jack[i].known || jack[i].out != out) ulog('I', "jack", jack_said[i][out]);
    jack[i].known = 1;
    jack[i].out = (unsigned char)out;
    if (unplugged) dac_early(i);
    return raw;
}
