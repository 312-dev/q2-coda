/* The Q2's OTG host power, only for a device on an OTG adapter (docs/internals.md#otg-host-power).
 * Stock's check_otg_status, from the one-second status pass, reads the ID pin PD18 and on a low
 * read turns the bus power on (PC20 high) and the charger off (PE22 high) at once. The board drives
 * PD18 low by itself, with nothing attached and its pull-up on, in one pulse of about 2 s some 2 to
 * 7 s after VBUS leaves the port, the Q2's own bus power included; a cable put into a computer then
 * met the Q2's 5 V, and a USB-C laptop will not source onto a live VBUS, so nothing enumerated.
 * Here:
 * - a low ID read within OTG_BLANK_MS of VBUS leaving the port is ignored until it reads high
 *   again (B), or until it has stayed low for OTG_ACCESSORY_MS, longer than any low logged with
 *   nothing attached: an accessory put in soon after a cable out and left in;
 * - any other low reaches stock only once it has read low, with the cable sense PD17 showing no
 *   VBUS, on every pass for OTG_SETTLE_MS;
 * - the bus power is a trial: with no device on the host port within OTG_TRIAL_MS it goes off,
 *   and is not tried again until a cable's VBUS is seen or the Q2 wakes from Standby (A);
 * - the PHY's ID pull-up is held on and the controller's role held at device (C, D) except while
 *   the bus power is granted, so a phantom ID low moves neither.
 * PD18's own pull-up is set at boot (S12usbcharge, and platform_init: tools/build.py ID_PULL_UP).
 *
 * A Play refused for want of an output while an accessory is on its way (its ID low, waited out or
 * its bus power's trial running, no USB DAC yet) says "Connecting USB audio..." instead of stock's
 * message and is remembered: once stock's host pass has the DAC (g_otg_status 2, the player's way
 * switched to it by the same pass), the song is started again at its place (player_save_stop, then
 * player_save_start), so it opens the DAC's PCM, never the headphone DAC's, which a resume of the
 * paused song would keep. If the accessory does not come, the Play goes with stock's message then.
 */
#include "navigation.h"

/* The ID low and no VBUS this long before the Q2 powers the bus, for a low the blank did not catch.
 * The board's own lows, with nothing attached, came after VBUS left and are the blank's. */
#define OTG_SETTLE_MS 6000
/* A low ID starting this soon after VBUS left the port is the board's, not an adapter's: with
 * PD18's pull-up on, the lows started 2.15 to 7.02 s after it (q2-pull-state.log, 2026-11-17,
 * 0.54 s samples, 4 lows). */
#define OTG_BLANK_MS 10000
/* A low the blank caught and still low this long is an accessory's: with the pull-up on, the lows
 * with nothing attached lasted 2.15 s where nothing cut them, and 3.21 and 4.28 s before stock's
 * bus power cut them (q2-pull-state.log). */
#define OTG_ACCESSORY_MS 8000
/* Bus power with no device enumerated on the host port goes off after this. */
#define OTG_TRIAL_MS 8000
#define OTG_GAP_MS 5000 /* passes further apart than this (a Standby) start the wait again */
/* The Q2's own bus power, ended, still read by stock as a cable's VBUS this long after: 1.6 s on
 * 2026-11-23, when that "cable in" started a Bluetooth connect (usb_ui.c headset_bt_usb). */
#define OTG_OWN_VBUS_MS 3000
/* A remembered Play's longest wait: the blank's accessory, the settle and the trial. */
#define OTG_PLAY_MS (OTG_ACCESSORY_MS + OTG_SETTLE_MS + OTG_TRIAL_MS)
#define OTG_CONNECTING "Connecting USB audio..." /* ringnav_forbid_play's message */
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
    unsigned blanked_at; /* when the blank caught the low still read */
    unsigned own_at;     /* when the Q2's own bus power last ended; own: it has */
    int own, id_low;     /* id_low: PD18 as the last pass read it */
    int play;            /* a Play refused while an accessory is on its way (ringnav_forbid_play) */
    unsigned play_at;
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
            otg.own = 1, otg.own_at = now;
            otg_hold();
        }
    }
    return result;
}

/* 1 while the Q2 powers the bus, and for OTG_OWN_VBUS_MS after: a cable in read then is its own VBUS
 * dying away, no host's (usb_ui.c: no Bluetooth connect for it). */
int usb_otg_own_vbus(void) {
    return g_last_otgid == 1 || (otg.own && (unsigned)time_now_ms() - otg.own_at < OTG_OWN_VBUS_MS);
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
    otg.id_low = id_low;
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
            otg.own = 1, otg.own_at = now;
            otg_hold();
            usb_record("otg-trial-off");
            return 0;
        }
        return otg_stock(now);
    }
    if (vbus) otg.refused = 0; /* a cable's VBUS: PC20 is off, so it is not the Q2's own */
    /* An ID low read within OTG_BLANK_MS of VBUS leaving is ignored until it reads high, or until
     * it has stayed low OTG_ACCESSORY_MS: an accessory's, settled already. */
    if (id_low && otg.have_gone && now - otg.gone_at < OTG_BLANK_MS && !otg.blanked)
        otg.blanked = 1, otg.blanked_at = now;
    if (!id_low) otg.blanked = 0;
    int accessory = otg.blanked && now - otg.blanked_at >= OTG_ACCESSORY_MS;
    if (!id_low || vbus || gap || (otg.blanked && !accessory) || otg.refused) {
        otg.waiting = 0;
    } else if (!otg.waiting) {
        otg.waiting = 1;
        otg.since = now;
    }
    /* A high ID is stock's device pass (it also ends a DAC's output). A low one is passed on only
     * once settled, with the controller's role handed back to the pin first. */
    if (!id_low) return otg_stock(now);
    if (otg.waiting && (accessory || now - otg.since >= OTG_SETTLE_MS)) {
        system(OTG_FROM_PIN);
        int result = otg_stock(now);
        if (g_last_otgid != 1) otg_hold();
        return result;
    }
    return 0;
}

/* An accessory on its way: its ID low and, with no USB DAC yet, waited out (the blank or the settle)
 * or its bus power's trial running. */
static int attaching(unsigned now) {
    if (g_otg_status == 2 || otg.refused || !otg.id_low) return 0;
    if (g_last_otgid == 1) return now - otg.host_at < OTG_TRIAL_MS;
    return otg.blanked || otg.waiting;
}

/* check_forbid_play_no_headset (0x4f2cac, tools/build.py HOOKS): stock's gate, but for a start with
 * nothing to play to while an accessory is on its way (the top of this file). */
int ringnav_forbid_play(int quiet) {
    unsigned now = time_now_ms();
    if (g_testmode_flag || !g_block_withoutphone_flag || !attaching(now) || has_valid_output_device())
        return stock_forbid_play_trampoline(quiet);
    if (!quiet) {
        toast(OTG_CONNECTING);
        otg.play = 1, otg.play_at = now;
    }
    return 1;
}

/* From usb_ui_poll, every UI pass: a remembered Play started once the DAC is there, or given up. */
void usb_otg_poll(void) {
    if (!otg.play) return;
    unsigned now = time_now_ms();
    if (g_otg_status == 2 && has_valid_output_device()) {
        otg.play = 0;
        if (mclGetPlayStatus() != 2) {
            player_save_stop();
            player_save_start(0);
            ulog('I', "usb", "the Play asked while USB audio connected: started on it");
        }
        return;
    }
    if (!attaching(now) || now - otg.play_at >= OTG_PLAY_MS) {
        otg.play = 0;
        stock_forbid_play_trampoline(0); /* stock's no-output message, now nothing is coming */
    }
}
