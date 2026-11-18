/* The Q2's OTG host power is never turned on while a computer's or a charger's VBUS is on the port
 * (docs/internals.md#otg-host-power). Stock's check_otg_status, from the one-second status pass,
 * reads the ID pin PD18 and on a low read turns the bus power on (PC20 high) and the charger off
 * (PE22 high) at once. PD18 has no pull of its own (stock leaves its pad at pull_hiz), so after an
 * unplug it drifted low for seconds with no cable in, and a cable put into a computer then found
 * the Q2 driving 5 V into the computer's port. S12usbcharge (patch/usb_charge.sh) turns PD18's
 * pull-up on at boot, which ends that drift; an OTG adapter's grounded ID still reads low. The
 * board still pulls the line low by itself with nothing attached, in pulses of 2 to 4 s (most
 * likely the USB-C port's role logic), so a low ID reaches stock only once it has read low, with
 * the cable sense PD17 showing no VBUS, on every pass for OTG_SETTLE_MS, longer than those pulses;
 * until then stock does not run and the Q2 stays a USB device.
 */
#include "navigation.h"

/* The ID low and no VBUS this long before the Q2 powers the bus: the empty port's pulses were 4.3 s
 * at most (2026-11-17). */
#define OTG_SETTLE_MS 6000
#define OTG_GAP_MS 5000 /* passes further apart than this (a Standby) start the wait again */

static struct {
    unsigned since, last; /* time_now_ms the wait started, and of the last pass */
    int waiting, have_last;
} otg __attribute__((section(".scratch")));

/* Stock's pass, with a line in the USB record (usb_record.c) when it turned the bus power on or
 * off. */
static int otg_stock(void) {
    int was = g_last_otgid;
    int result = stock_otg_status_trampoline();
    if (g_last_otgid != was) usb_record(g_last_otgid == 1 ? "otg-on" : "otg-off");
    return result;
}

int ringnav_otg_status(void) {
    unsigned now = time_now_ms();
    int gap = otg.have_last && now - otg.last > OTG_GAP_MS;
    otg.last = now;
    otg.have_last = 1;
    /* Host already: PD17 reads the Q2's own bus power now, so it tells nothing. Stock turns the
     * bus power off in this same pass once PD18 reads high, the dwc2 controller's switch back to
     * device. ponytail: a computer plugged in while the Q2 powers the bus is not seen until then;
     * the pull-up keeps an unplug's ID from reading low, and the USB record shows whether it ever
     * happens. */
    if (g_last_otgid == 1) {
        otg.waiting = 0;
        return otg_stock();
    }
    int id_low = toolsGpioGetValue("PD18") == 0;
    int vbus = toolsGpioGetValue("PD17") == 0; /* usb_detect's read: 0 is a cable with VBUS */
    if (!id_low || vbus || gap) {
        otg.waiting = 0;
    } else if (!otg.waiting) {
        otg.waiting = 1;
        otg.since = now;
    }
    /* A high ID is stock's device pass (it also ends a DAC's output). A low one with VBUS on the
     * port, or not yet settled, is not passed on: stock would power the bus. */
    if (!id_low || (otg.waiting && now - otg.since >= OTG_SETTLE_MS)) return otg_stock();
    return 0;
}
