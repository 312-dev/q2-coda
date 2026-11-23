/* Standby and the USB link (docs/internals.md#standby-and-the-usb-link). A suspend under a host's live link left the
 * link dead after the wake: Ingenic's dwc2 keeps a configured gadget through it and pulls nothing down or up again,
 * so the host never learns the Q2 slept (2026-11-22, a laptop, 23 min until the cable was handled). And a host that
 * reconnects at once enumerates before the card is back (dev3: "new address" 0.25 s before the card mounted): adbd's
 * key on the card unread (unauthorized), q2-iap's library and covers without the card. So, around stock's Standby:
 *
 * - before stock's device_standby (hooked here: it stops the player, unmounts the card and unloads its driver, then
 *   calls toolsIntoStandby), Car Audio stopped, the player's car PCM released first (usb_car.c usb_car_stop_now):
 *   q2-iap and q2car.sh's runner end, and their logs on the card close before stock's umount, which is
 *   card_release.c's (every holder let go of, the umount verified);
 * - just before the suspend (system.c ringnav_standby), the gadget's pull-up let go (soft_connect "disconnect");
 * - after the wake, once device_standby has returned, on a later UI pass (standby_usb_poll): the pull-up back
 *   ("connect") once the card is mounted read-write, or SB_CARD_WAIT_MS on for a card not back, or at once for a Q2
 *   that had no card mounted as the Standby began, card_release.c's card_back first; the controller's
 *   state and role then logged, a role left at host (seen after a wake on 2026-11-17) held at device again
 *   (usb_otg.c otg_hold) and the pull-up given once more; then Car Audio started again if it was on and the cable is
 *   still in. SB_LOOK_MS later the state is logged once more, so the log shows whether the host came back. */
#include "navigation.h"

#define SB_UDC "/sys/class/udc/13500000.otg/"
#define SB_SOFT SB_UDC "soft_connect"
#define SB_STATE SB_UDC "state"
#define SB_ROLE "/sys/devices/platform/ahb2/13500000.otg/id_status" /* "device" or "host" (Ingenic's dwc2) */
#define SB_CARD_WAIT_MS 3000 /* the card mounts within 2 s of device_standby's return (dev3: 0.25 s after) */
#define SB_LOOK_MS 3000      /* a host enumerates within a second or two of the pull-up */

static struct {
    int car;     /* Car Audio stopped for this Standby (ringnav_device_standby) */
    int had_card; /* the card mounted read-write as the Standby began */
    int let_go;  /* the pull-up let go for the suspend (standby_usb_suspend) */
    int reconnect; /* this wake gives the pull-up back (let_go, carried past device_standby's return) */
    int waking;  /* 1 the reconnect (and card_back) waits for the card, 2 the state's second look waits */
    unsigned at; /* when device_standby returned, then when the pull-up came back */
} sb __attribute__((section(".scratch")));

static void sb_write(const char *path, const char *what) {
    void *f = fopen(path, "w");
    if (!f) return;
    fwrite(what, 1, strlen(what), f);
    fclose(f);
}

/* The first word of a sysfs file, "?" unreadable. */
static void sb_read(const char *path, char *out, unsigned cap) {
    void *f = fopen(path, "rb");
    unsigned n = f ? fread(out, 1, cap - 1, f) : 0;
    if (f) fclose(f);
    out[n] = 0;
    for (unsigned i = 0; i < n; ++i)
        if (out[i] == '\n') out[i] = 0;
    if (!out[0]) out[0] = '?', out[1] = 0;
}

/* /proc/mounts has the card mounted read-write (its options' first word "rw"). */
static int sb_card_rw(void) {
    char m[4096]; /* ponytail: the Q2's mounts fit (705 bytes measured with the ADB gadget up, 2026-11-22) */
    void *f = fopen("/proc/mounts", "rb");
    if (!f) return 0;
    unsigned n = fread(m, 1, sizeof m - 1, f);
    fclose(f);
    m[n] = 0;
    char *p = strstr(m, " /mnt/mmc ");
    if (!p) return 0;
    for (p += 10; *p && *p != ' '; ++p) {} /* past the file system's type */
    return p[0] == ' ' && p[1] == 'r' && p[2] == 'w' && (p[3] == ',' || p[3] == ' ');
}

/* The controller as it is: its state and role, said in the log after why. */
static int sb_say(const char *why) {
    char state[32], role[16], line[128];
    sb_read(SB_STATE, state, sizeof state);
    sb_read(SB_ROLE, role, sizeof role);
    tk_snprintf(line, sizeof line, "standby: %s: udc %s, role %s", why, state, role);
    ulog('I', "usb", line);
    return !tk_strcmp(role, "host");
}

/* device_standby (0x5021bc, tools/build.py HOOKS): Car Audio down before stock's card unmount, then stock's Standby,
 * which returns at the wake. */
int ringnav_device_standby(void) {
    sb.had_card = sb_card_rw(); /* before stock unmounts it */
    sb.car = usb_car_stop_now();
    if (sb.car) ulog('I', "usb", "standby: Car Audio stopped for it");
    int result = stock_device_standby_trampoline();
    /* the card let go of at stock's umount (card_release.c): its hold ends once it is back, suspended or not */
    sb.reconnect = sb.let_go, sb.let_go = 0, sb.waking = 1, sb.at = (unsigned)time_now_ms();
    return result;
}

/* ringnav_standby's, just before stock's suspend: the host told the Q2 is gone. */
void standby_usb_suspend(void) {
    sb_write(SB_SOFT, "disconnect");
    sb.let_go = 1;
}

/* Every UI loop pass (system.c power_poll). */
void standby_usb_poll(void) {
    if (!sb.waking) return;
    unsigned now = (unsigned)time_now_ms();
    if (sb.waking == 2) {
        if (now - sb.at < SB_LOOK_MS) return;
        sb.waking = 0;
        sb_say("3 s after");
        return;
    }
    int card = sb_card_rw();
    if (!card && sb.had_card && now - sb.at < SB_CARD_WAIT_MS) return;
    card_back(); /* card_release.c: the hold gone, the mounts counted, the kept log lines written */
    if (!sb.reconnect) {
        sb.waking = 0;
        return;
    }
    sb.reconnect = 0;
    sb_write(SB_SOFT, "connect");
    if (sb_say(card            ? "the link back, the card mounted"
               : sb.had_card ? "the link back, no card after 3 s"
                             : "the link back, no card before the Standby")) {
        otg_hold();
        sb_write(SB_SOFT, "connect");
        sb_say("held at device");
    }
    if (sb.car && g_usbdet_value == 1 && !usb_car_on()) usb_car_start();
    sb.car = 0;
    sb.waking = 2, sb.at = now;
}
