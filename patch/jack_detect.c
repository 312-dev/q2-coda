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
 * thread. */
#include "navigation.h"

static struct {
    unsigned char known;   /* a read has been answered */
    unsigned char out;     /* the last answer: 1 unplugged */
    unsigned char pending; /* an unplugged read held back since at */
    unsigned at;
} jack[2] __attribute__((section(".scratch")));

static const char *const jack_said[2][3] = {
    { "3.5 mm jack plugged", "3.5 mm jack unplugged",
      "3.5 mm jack: an unplugged read that did not last, ignored" },
    { "4.4 mm jack plugged", "4.4 mm jack unplugged",
      "4.4 mm jack: an unplugged read that did not last, ignored" },
};

/* What stock's poll is given for pin name, raw being its reading. */
int jack_read(const char *name, int raw) {
    int i = !name ? -1 : !strcmp(name, "PA07") ? 0 : !strcmp(name, "PA08") ? 1 : -1;
    if (i < 0) return raw;
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
    if (!jack[i].known || jack[i].out != out) ulog('I', "jack", jack_said[i][out]);
    jack[i].known = 1;
    jack[i].out = (unsigned char)out;
    return raw;
}
