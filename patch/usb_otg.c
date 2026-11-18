/* The Q2's OTG host power, only for a device on an OTG adapter (docs/internals.md#otg-host-power).
 * Stock's check_otg_status, from the one-second status pass, reads the ID pin PD18 and on a low
 * read turns the bus power on (PC20 high) and the charger off (PE22 high) at once. The board drives
 * PD18 low by itself, with nothing attached, in one pulse of 2 to 4 s some 3 to 12 s after VBUS
 * leaves the port, the Q2's own bus power included; a cable put into a computer then met the Q2's
 * 5 V, and a USB-C laptop will not source onto a live VBUS, so nothing enumerated. Here:
 * - a low ID read within OTG_BLANK_MS of VBUS leaving the port is ignored until it reads high
 *   again (B);
 * - any other low reaches stock only once it has read low, with the cable sense PD17 showing no
 *   VBUS, on every pass for OTG_SETTLE_MS;
 * - the bus power is a trial: with no device on the host port within OTG_TRIAL_MS it goes off,
 *   and is not tried again until a cable's VBUS is seen or the Q2 wakes from Standby (A);
 * - the PHY's ID pull-up is held on and the controller's role held at device (C, D) except while
 *   the bus power is granted, so a phantom ID low moves neither.
 * PD18's own pull-up is set at boot (S12usbcharge, and platform_init: tools/build.py ID_PULL_UP).
 */
#include "navigation.h"

/* The ID low and no VBUS this long before the Q2 powers the bus: the empty port's pulses were 4.3 s
 * at most (2026-11-17). */
#define OTG_SETTLE_MS 6000
/* A low ID starting this soon after VBUS left the port is the board's, not an adapter's: the
 * pulses started 2.7 to 11.8 s after it (2026-11-17, 0.25 s samples). */
#define OTG_BLANK_MS 15000
/* Bus power with no device enumerated on the host port goes off after this. */
#define OTG_TRIAL_MS 8000
#define OTG_GAP_MS 5000 /* passes further apart than this (a Standby) start the wait again */
#define OTG_HOST_DEV "/sys/bus/usb/devices/1-1/idVendor" /* a device on the Q2's host port */
/* The X2000's CPM: USBPCR (0x1000003c) bit 29, the PHY's ID pull-up always on (D); USBRDT
 * (0x10000040) bits 24 and 23, the controller's ID from IDDIG_REG, set to device (C), or from the
 * pin. Volatile writes through busybox devmem, as tested on the Q2 2026-11-17; a reboot clears
 * them. */
#define OTG_RMW(addr, op) "devmem " addr " 32 $(( $(devmem " addr ") " op " ))"
#define OTG_HOLD OTG_RMW("0x1000003c", "| 0x20000000") "; " OTG_RMW("0x10000040", "| 0x01800000")
#define OTG_FROM_PIN OTG_RMW("0x10000040", "& ~0x01800000")

static struct {
    unsigned since, last, gone_at, host_at; /* time_now_ms: the wait's start, the last pass, VBUS
                                                leaving the port, the bus power granted */
    int waiting, have_last, have_gone, vbus_prev, blanked, refused;
} otg __attribute__((section(".scratch")));

/* The PHY's ID pull-up on and the controller held at device: at boot (usb_record.c
 * ringnav_platform_init) and every wake (usb_record_wake), as the PHY's init writes USBPCR again.
 */
void otg_hold(void) { system(OTG_HOLD); }

/* VBUS leaving the port, now: a cable out, or the Q2's own bus power ended, whose decay the next
 * pass's PD17 then does not count again. */
static void gone(unsigned now) {
    otg.gone_at = now;
    otg.have_gone = 1;
    otg.vbus_prev = 0;
}

/* Stock's pass, with a line in the USB record (usb_record.c) when it turned the bus power on or
 * off; its end is VBUS leaving the port, and the controller held at device again. */
static int otg_stock(unsigned now) {
    int was = g_last_otgid;
    int result = stock_otg_status_trampoline();
    if (g_last_otgid != was) {
        usb_record(g_last_otgid == 1 ? "otg-on" : "otg-off");
        if (g_last_otgid == 1) {
            otg.host_at = now;
        } else {
            gone(now);
            otg_hold();
        }
    }
    return result;
}

static int host_device(void) {
    void *f = fopen(OTG_HOST_DEV, "rb");
    if (f) fclose(f);
    return f != 0;
}

int ringnav_otg_status(void) {
    unsigned now = time_now_ms();
    int gap = otg.have_last && now - otg.last > OTG_GAP_MS;
    otg.last = now;
    otg.have_last = 1;
    if (gap) otg.refused = 0;                  /* back from Standby: a fresh try */
    int vbus = toolsGpioGetValue("PD17") == 0; /* usb_detect's read: 0 is VBUS on the port */
    if (otg.vbus_prev && !vbus) gone(now);
    otg.vbus_prev = vbus;
    int id_low = toolsGpioGetValue("PD18") == 0;
    /* Host already: PD17 reads the Q2's own bus power now, so it tells nothing. A device on the
     * host port keeps it; none within OTG_TRIAL_MS ends it, stock's pins written as its own device
     * pass writes them. Stock turns it off itself once PD18 reads high. ponytail: a DAC unplugged
     * from the adapter leaves the empty adapter powered. */
    if (g_last_otgid == 1) {
        otg.waiting = 0;
        if (id_low && now - otg.host_at >= OTG_TRIAL_MS && !host_device()) {
            toolsGpioSetValue("PC20", 0);
            toolsGpioSetValue("PE22", 0); /* the charge guard (usb_charge.c) sets its own next */
            g_last_otgid = 0;
            otg.refused = 1;
            gone(now);
            otg_hold();
            usb_record("otg-trial-off");
            return 0;
        }
        return otg_stock(now);
    }
    if (vbus) otg.refused = 0; /* a cable's VBUS: PC20 is off, so it is not the Q2's own */
    /* An ID low read within OTG_BLANK_MS of VBUS leaving is ignored until it reads high. */
    if (id_low && otg.have_gone && now - otg.gone_at < OTG_BLANK_MS) otg.blanked = 1;
    if (!id_low) otg.blanked = 0;
    if (!id_low || vbus || gap || otg.blanked || otg.refused) {
        otg.waiting = 0;
    } else if (!otg.waiting) {
        otg.waiting = 1;
        otg.since = now;
    }
    /* A high ID is stock's device pass (it also ends a DAC's output). A low one is passed on only
     * once settled, with the controller's role handed back to the pin first. */
    if (!id_low) return otg_stock(now);
    if (otg.waiting && now - otg.since >= OTG_SETTLE_MS) {
        system(OTG_FROM_PIN);
        int result = otg_stock(now);
        if (g_last_otgid != 1) otg_hold();
        return result;
    }
    return 0;
}
