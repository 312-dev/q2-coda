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
 * A cable out stops it all (usb_car_stop). */
#include "navigation.h"

#define USB_CAR_SCRIPT "/usr/bin/q2car"
#define USB_CAR_STATE "/tmp/q2car.state" /* q2_iap.c state_write, q2car.sh's down=1 */
#define USB_CAR_LOOK_MS 200
#define USB_CAR_CHARGER_MS 3000 /* usb_ui.c USB_PROBE_MS: a host addresses a gadget within a second or two */

static struct {
    int on, decided, host, flowing, troubles, charger_told;
    unsigned at, look_at;
} car __attribute__((section(".scratch")));

int usb_car_on(void) { return car.on; }

void usb_car_start(void) {
    car.on = 1, car.decided = car.host = car.flowing = car.troubles = car.charger_told = 0;
    car.at = car.look_at = (unsigned)time_now_ms();
    system(USB_CAR_SCRIPT " start >/dev/null 2>&1");
}

/* The gadget removed: in the background at a cable out (q2car.sh's stop waits for q2-iap, and the next start for
 * the stop), here and now (wait) when another gadget is to have the controller next. */
void usb_car_stop(int wait) {
    if (!car.on) return;
    car.on = 0;
    system(wait ? USB_CAR_SCRIPT " stop >/dev/null 2>&1" : "( " USB_CAR_SCRIPT " stop >/dev/null 2>&1 & )");
}

/* A toast for the car: the screen lit for it, as the cable's plug-in lit it, so a glance shows whether it worked. */
static void usb_car_toast(const char *glyph, const char *text) {
    awake_screen();
    toast_later(glyph, text);
}

/* The state file's news; what usb_ui.c is to do about it. */
int usb_car_poll(void) {
    char raw[512], v[64], line[96];
    unsigned now = (unsigned)time_now_ms();
    if (!car.on || now - car.look_at < USB_CAR_LOOK_MS) return USB_CAR_NONE;
    car.look_at = now;
    state_read(USB_CAR_STATE, raw, sizeof raw);
    field(raw, "verdict", v, sizeof v);
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
