/* Car Audio, the USB setting's Automatic (docs/iphone-mode.md#car-audio-in-the-image): at a cable in usb_ui.c
 * starts the iPhone gadget (usb_car_start: /usr/bin/q2car, patch/q2car.sh) and q2-iap (patch/q2_iap.c) decides
 * from the host's requests what it is, writing USB_CAR_STATE. Read here from each UI pass while the gadget is
 * shown, every USB_CAR_LOOK_MS:
 *
 * - a car (verdict=car): the iPhone stays; when the car's audio flows the toast "Playing through <its name> (USB)",
 *   and while it plays, a problem q2-iap's health check sees (packets the controller missed, the player running
 *   dry, no packet taken at all) toasts "Car audio problem: ..." each time a new one starts;
 * - not a car (verdict computer, silent or failed, then down=1 once the gadget is gone): usb_car_poll answers
 *   USB_CAR_STORAGE and usb_ui.c gives the cable to Transfer Files;
 * - no host at all within USB_CAR_CHARGER_MS (a charger): USB_CAR_CHARGER, once, for the charge toast; the
 *   gadget stays, unseen by a charger, and a host that comes later is decided as any other.
 * A cable out stops it all (usb_car_stop), as does a mode picked with the iPhone shown (usb_ui.c usb_switch).
 *
 * The stop waits for the player to let go of the car's PCM first, never on the UI thread: the gadget's removal
 * (q2car.sh gadget_down) ends in the kernel's snd_card_free, which waits for the card's last open file, and a
 * paused song keeps the PCM open in a wait that only demo's next command ends (player/engine.c); on 2026-11-22 a
 * held PCM kept the UDC write in snd_card_free until it closed, with every other read of the gadget's UDC (q2guard's
 * too) waiting behind it (docs/iphone-mode.md#the-stream-s-life). So the player is asked to release it
 * ({mcl-carrelease\null}: the PCM closed, the song paused at its place; a Play before the stop opens it again, which
 * gadget_down's own release then covers), later UI passes look for the PCM closed (USB_CAR_PCM) for
 * USB_CAR_RELEASE_MS at most, and q2car stop then runs in the background, touching USB_CAR_STOPPED when it has
 * returned (usb_car_busy until then). Standby's stop is the one made here and now (usb_car_stop_now).
 *
 * Losing the car's audio with nowhere else to play pauses, as stock pauses at a jack's eject (config_po_output,
 * config_bal_output) and reloads paused at a Bluetooth headset's loss (checkBluetoothStatus): the player falls back
 * to the DAC at once (player/engine.c), and with no jack in, no Bluetooth link and no USB DAC
 * (has_valid_output_device) that plays to nothing; on 2026-11-21 it did so for 2 h 09 after the cable came out.
 * So at a cable out, and when the car stops asking for audio while the cable stays, a song playing is paused where
 * it is (stock's pause: Play resumes it there, and the DAC's own timer powers it down). Not while q2car.sh shows
 * the car the iPhone again after a lost link (USB_CAR_RELINK, written by q2-iap before it ends with 22): the car's
 * audio comes back then, and the song with it, unless it has not within USB_CAR_RELINK_MS
 * (docs/iphone-mode.md#nowhere-to-play). */
#include "navigation.h"

#define USB_CAR_SCRIPT "/usr/bin/q2car"
#define USB_CAR_STATE "/tmp/q2car.state" /* q2_iap.c state_write, q2car.sh's down=1 */
#define USB_CAR_LOOK_MS 200
#define USB_CAR_CHARGER_MS 3000 /* usb_ui.c USB_PROBE_MS: a host addresses a gadget within a second or two */
#define USB_CAR_RELINK "/tmp/q2car.relink" /* q2_iap.c's at a lost link's exit 22, q2car.sh's until the runner ends */
#define USB_CAR_RELINK_MS 30000            /* a relink's longest wait for the car's audio before it counts as gone */
#define USB_CAR_PCM "/proc/asound/UAC1Gadget/pcm0p/sub0/status" /* "closed" once nothing has the car's PCM open */
#define USB_CAR_RELEASE_MS 2000                                  /* a stop's longest wait for the player's release */
#define USB_CAR_STOPPED "/tmp/q2car.stopped"                     /* touched once a stop's q2car stop has returned */

static struct {
    int on, decided, host, flowing, troubles, charger_told, audio;
    int stopping; /* 1 waiting for the player's release of the PCM, 2 q2car stop running (usb_car_busy) */
    unsigned at, look_at, gone_at, stop_at; /* gone_at: when the car's audio went during a relink, 0 none */
    char name[64];                 /* the car's name, as it identified itself over iAP2 (q2-iap's name=) */
} car __attribute__((section(".scratch")));

int usb_car_on(void) { return car.on; }

/* The car on the cable as it named itself over iAP2, while Car Audio has it; 0 otherwise. headset.c's
 * auto-connect leaves that car's Bluetooth alone: on 2026-11-21 a Bluetooth relaunch auto-connected A2DP to the very
 * car the Q2 was playing to over USB (docs/iphone-mode.md#car-audio-in-the-image). */
const char *usb_car_name(void) { return car.on && car.decided && car.name[0] ? car.name : 0; }

/* 1 while the car's audio is an output (output_rule.c ringnav_valid_output): the car asks for it, or
 * it went during a relink that may still bring it back (car.gone_at), as usb_car_gone waits for. */
int usb_car_output(void) { return car.on && (car.audio || car.gone_at); }

/* 1 while something has the car's PCM open; with no card (no gadget, or its function unbound) nothing has. */
static int usb_car_pcm_held(void) {
    char s[8] = { 0 };
    void *f = fopen(USB_CAR_PCM, "rb");
    if (!f) return 0;
    fread(s, 1, sizeof s - 1, f);
    fclose(f);
    return memcmp(s, "closed", 6) != 0;
}

/* q2car stop in the background (its own waits are bounded: q2-iap's end, the player's release in gadget_down), and
 * USB_CAR_STOPPED once it has returned; then the start, when a cable came back before the stop had begun. */
static void usb_car_stop_run(int then_start) {
    car.stopping = 2;
    unlink(USB_CAR_STOPPED);
    system(then_start ? "( " USB_CAR_SCRIPT " stop >/dev/null 2>&1; : > " USB_CAR_STOPPED "; " USB_CAR_SCRIPT
                        " start >/dev/null 2>&1 ) &"
                      : "( " USB_CAR_SCRIPT " stop >/dev/null 2>&1; : > " USB_CAR_STOPPED " ) &");
}

void usb_car_start(void) {
    int pending = car.stopping == 1; /* the last cable's stop still waiting for the player: it goes first */
    car.on = 1, car.decided = car.host = car.flowing = car.troubles = car.charger_told = car.audio = 0, car.gone_at = 0;
    car.name[0] = 0;
    car.at = car.look_at = (unsigned)time_now_ms();
    if (pending)
        usb_car_stop_run(1);
    else
        system(USB_CAR_SCRIPT " start >/dev/null 2>&1");
}

/* The player asked to let go of the car's PCM: closed, the car file kept, the song paused where it was heard
 * (docs/player.md#releasing-the-car). Never waits on the player (MSG_DONTWAIT | MSG_NOSIGNAL). */
static void usb_car_release(void) {
    int fd = *(volatile int *)MCL_FD;
    static const char m[] = "{mcl-carrelease\\null}";
    if (fd != -1) send(fd, m, sizeof m - 1, 0x4040);
}

/* The gadget removed (the docs at the top): the player asked to release the car's PCM if it holds it, and q2car stop
 * run once it has, or USB_CAR_RELEASE_MS on (usb_car_poll); at once with the PCM closed. */
static void usb_car_gone(const char *why);
void usb_car_stop(void) {
    if (!car.on) return;
    car.on = 0;
    if (car.audio) car.audio = 0, usb_car_gone("the cable out");
    if (!usb_car_pcm_held()) {
        usb_car_stop_run(0);
        return;
    }
    usb_car_release();
    car.stopping = 1, car.stop_at = (unsigned)time_now_ms();
}

/* Standby's stop (standby_usb.c), before stock's device_standby unmounts the card: q2-iap and the runner end, and with
 * them their logs on the card, which stock's plain umount would otherwise meet open. Here and now, as the suspend
 * follows: the release waited for USB_CAR_RELEASE_MS at most, then q2car stop, whose own waits are bounded. Nothing
 * plays (Standby's gate), so nothing is paused. 1 if Car Audio was on. */
int usb_car_stop_now(void) {
    int was = car.on;
    if (!car.on && !car.stopping) return 0;
    if (car.on && usb_car_pcm_held()) usb_car_release();
    car.on = car.audio = 0, car.gone_at = 0;
    unsigned at = (unsigned)time_now_ms();
    while (usb_car_pcm_held() && (unsigned)time_now_ms() - at < USB_CAR_RELEASE_MS) usleep(50000);
    car.stopping = 0;
    system(USB_CAR_SCRIPT " stop >/dev/null 2>&1");
    return was;
}

/* 1 from usb_car_stop until its q2car stop has returned: the controller is not yet free for another gadget. */
int usb_car_busy(void) { return car.stopping != 0; }

/* A stop's progress, each UI pass: the release seen or given up on, then q2car stop's return. */
static void usb_car_stopping(unsigned now) {
    if (car.stopping == 1) {
        int held = usb_car_pcm_held();
        if (held && now - car.stop_at < USB_CAR_RELEASE_MS) return;
        if (held) ulog('W', "usb", "the car's PCM still open after 2 s: the gadget removed anyway");
        usb_car_stop_run(0);
    } else if (car.stopping == 2 && !access(USB_CAR_STOPPED, 0)) {
        car.stopping = 0;
    }
}

/* A toast for the car: the screen lit for it, as the cable's plug-in lit it, so a glance shows whether it worked. */
static void usb_car_toast(const char *glyph, const char *text) {
    awake_screen();
    toast_later(glyph, text);
}

/* The car's audio gone: a song playing paused where it is when nothing else can sound (docs at the top). */
static void usb_car_gone(const char *why) {
    char line[96];
    car.gone_at = 0;
    /* Radio, Internet Radio, a video or Spotify played to the car's feed (videos.c output_device), which cannot follow
     * to another output mid-stream: paused whatever else is connected */
    if (media_owner() != 1) {
        if (!media_playing()) return;
        media_set_playing(0);
        tk_snprintf(line, sizeof line, "the car's audio gone (%s) under another source: paused", why);
        ulog('I', "usb", line);
        return;
    }
    if (mclGetPlayStatus() != 2 || has_valid_output_device()) return;
    player_play_pause();
    tk_snprintf(line, sizeof line, "the car's audio gone (%s) and nowhere else to play: paused", why);
    ulog('I', "usb", line);
}

/* The car's wish for audio as q2-iap's state file has it (audio=), followed: its end pauses (usb_car_gone), at once,
 * or during a relink only once the relink is over without it, or USB_CAR_RELINK_MS on. */
static void usb_car_audio(int audio, unsigned now) {
    if (audio) {
        car.audio = 1, car.gone_at = 0;
        return;
    }
    if (car.audio) {
        car.audio = 0;
        if (access(USB_CAR_RELINK, 0)) usb_car_gone("the car stopped it");
        else car.gone_at = now ? now : 1;
    } else if (car.gone_at && (access(USB_CAR_RELINK, 0) || now - car.gone_at >= USB_CAR_RELINK_MS)) {
        usb_car_gone("a relink without it");
    }
}

/* The state file's news; what usb_ui.c is to do about it. */
int usb_car_poll(void) {
    char raw[512], v[64], line[96];
    unsigned now = (unsigned)time_now_ms();
    if (car.stopping) usb_car_stopping(now);
    if (!car.on || now - car.look_at < USB_CAR_LOOK_MS) return USB_CAR_NONE;
    car.look_at = now;
    state_read(USB_CAR_STATE, raw, sizeof raw);
    field(raw, "verdict", v, sizeof v);
    usb_car_audio((int)number(raw, "audio"), now);
    if (number(raw, "host")) car.host = 1;
    if (!tk_strcmp(v, "computer") || !tk_strcmp(v, "silent") || !tk_strcmp(v, "failed")) {
        if (!number(raw, "down")) return USB_CAR_NONE; /* the gadget still there: nothing else can start yet */
        field(raw, "why", line, sizeof line);
        tk_snprintf(v + strlen(v), sizeof v - strlen(v), ": %s", line);
        ulog('I', "usb", v);
        car.on = 0;
        return USB_CAR_STORAGE;
    }
    if (tk_strcmp(v, "car")) {
        if (!car.host && !car.charger_told && now - car.at > USB_CAR_CHARGER_MS) {
            car.charger_told = 1;
            return USB_CAR_CHARGER;
        }
        return USB_CAR_NONE;
    }
    if (!car.decided) {
        car.decided = 1;
        field(raw, "why", line, sizeof line);
        ulog('I', "usb", line[0] ? line : "a car");
    }
    field(raw, "name", car.name, sizeof car.name);
    int flowing = (int)number(raw, "flowing"), troubles = (int)number(raw, "troubles");
    if (flowing && !car.flowing) {
        field(raw, "name", v, sizeof v);
        tk_snprintf(line, sizeof line, "Playing through %s (USB)", v[0] ? v : "the car");
        usb_car_toast("usbtoast_car", line);
    }
    car.flowing = flowing;
    if (troubles > car.troubles) {
        field(raw, "trouble", v, sizeof v);
        if (v[0])
            usb_car_toast("usbtoast_carwarn",
                          !tk_strcmp(v, "nostream") ? "Car audio problem: no sound" : "Car audio problem: dropouts");
    }
    car.troubles = troubles;
    return USB_CAR_NONE;
}
