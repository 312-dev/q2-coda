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
 *   and is not tried again until a cable's VBUS is seen or the Q2 wakes from Standby (A); where
 *   the controller's HPRT0 can be read (the dwc2's regdump, while debugfs is mounted) and shows no
 *   device's connect OTG_CONNECT_MS in, the trial ends then: a computer the wrong way round, which
 *   no wait would bring (docs/usb.md#the-plan, phase 2);
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
 * Every low logged with the pull-up on and nothing attached began 2.15 to 7.02 s after VBUS left
 * (OTG_BLANK_MS's), and the longest that nothing cut short lasted 2.15 s: read on stock's
 * once-a-second pass, such a low spans at most three reads 2 s apart, short of this, should one
 * ever begin outside the blank. The power comes on the fourth pass that reads an adapter's low,
 * 3 to 4 s after the plug. */
#define OTG_SETTLE_MS 3000
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
/* Bus power with HPRT0's connect status (bit 0) read clear this long goes off: nothing pulled D+ or
 * D- up, so nothing will enumerate. Each USB DAC of 2026-11-25 read connected at the first read,
 * 0.25 to 0.41 s after otg-on (seven of seven); the two laptops the wrong way round kept HPRT0 at
 * 0x1000 through 3 and 8 s of power (usbtrace-2026-11-25.trace, usb_fsm.h UF_CONNECT_MS, whose
 * reasoning this is). The regdump is read at most every OTG_CONNECT_LOOK_MS of the trial until a
 * connect or a device is seen; [Q2POD] USBCONNECT=0 keeps the OTG_TRIAL_MS trial alone. */
#define OTG_CONNECT_MS 2500
#define OTG_CONNECT_LOOK_MS 250
#define OTG_REGDUMP "/sys/kernel/debug/usb/13500000.otg/regdump"
#define OTG_REGDUMP_MAX 16384
/* In host role an ID high with no device on the host port this long ends it; with one, the device
 * going does. The AB13X earbuds' ID read high 0.98 s after their power came on, while the kernel
 * enumerated them (2026-11-23 12966.731), and stock's pass cut the power then. */
#define OTG_ID_HIGH_MS 2000
#define OTG_GAP_MS 5000 /* passes further apart than this (a Standby) start the wait again */
/* The Q2's own bus power, ended, reads as VBUS on PD17 as it dies away: 1.6 s on 2026-11-23
 * (13404.022 to 13405.770), when stock took it for a cable in and Bluetooth connected for it. It is
 * no cable until PD17 has read no VBUS once, or for this long at most, so a computer's cable put
 * in before PD17 read high is seen then. */
#define OTG_OWN_VBUS_MS 3000
/* A remembered Play waits this long for somewhere to play once the bus power is on (the AB13X
 * earbuds enumerated 0.5 s after it, 2026-11-23), and at most OTG_PLAY_MS in all. */
#define OTG_PLAY_GRANT_MS 3000
#define OTG_PLAY_MS (OTG_ACCESSORY_MS + OTG_SETTLE_MS + OTG_PLAY_GRANT_MS)
/* playpause_quick_click's return from check_forbid_play_no_headset (V1.36): stock's Play key, a Play
 * of the song there (tools/stock_sites.tsv). */
#define OTG_PLAYPAUSE_RET 0x4f2fd8
#define OTG_CONNECTING "Connecting USB audio..." /* ringnav_forbid_play's message */
/* A trial ended with nothing on the host port and the ID still low this long after, no VBUS: most
 * likely a computer whose roles resolved the wrong way (the Q2 the source, it the sink), which
 * stays so while its cable is in (2026-11-23 17777.35 to 17833.67, pd18-owner2.log; on 2026-11-25
 * 16.8 and 48 s past the power's end, usbtrace-2026-11-25.trace): the bus power off does not
 * re-resolve it, a replug does, and was a device at once both times. Past the Q2's own VBUS dying
 * away (PD17 read it 1.6 s, OTG_OWN_VBUS_MS), so a replug at the message meets no 5 V of the Q2's. */
#define OTG_WRONG_MS 2000
#define OTG_REPLUG "Unplug the cable and plug it in again"
/* The PMIC's USB supply: 1 for a computer's or a charger's VBUS, already in the pass of the cable's
 * edge, on some 250 lines of the USB record; 0 for the Q2's own power dying away and for the AB13X
 * earbuds that held PD17 low with no VBUS anywhere (2026-11-23 18191.59 and 18231.97, and demo
 * 510574 on an earlier boot). PD17 low with it 0 is no cable; this long so is the no-source state. */
#define OTG_ONLINE "/sys/class/power_supply/usb/online"
#define OTG_NO_SOURCE_MS 2000
/* The pin trace (usb_otg_poll): a line this often while PD18 reads low out of host role, the bus
 * power is in its trial or kept over an ID high, or VBUS left the port less than OTG_TRACE_AFTER_MS
 * ago; at most OTG_TRACE_MAX lines (a minute of it) until none of that holds and the bus power is
 * off. */
#define OTG_TRACE_MS 250
#define OTG_TRACE_AFTER_MS 30000
#define OTG_TRACE_MAX 240
/* Past its trial a grant is traced on the same beat, a line only when PD18 or the host port's
 * device changes, OTG_TRACE_CHANGES lines a grant at most: so the device's leaving and the ID's
 * high are timed against each other and against the kernel's "USB disconnect". */
#define OTG_TRACE_CHANGES 40
/* The record's word for the bus power's end, and why: the host port's device gone (the ID high, or
 * low past the trial); OTG_ID_HIGH_MS of ID high with nothing enumerated; stock's pass otherwise
 * (its PD18 read high between this file's read and its own). A trial with nothing enumerated is
 * otg-trial-off, and otg-trial-off why=no-connect when HPRT0 read no connect (OTG_CONNECT_MS). */
#define OTG_OFF_GONE "otg-off why=gone"
#define OTG_OFF_ID_HIGH "otg-off why=id-high"
#define OTG_OFF_STOCK "otg-off why=stock"
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
    unsigned id_high_at; /* host role: when PD18 first read high; id_high: it reads high now */
    int id_high, had_dev; /* had_dev: a device seen on the host port in this grant */
    int look, connected, unread; /* this grant: HPRT0 to be read ([Q2POD] USBCONNECT), its connect
                                    seen, a read made with none; look_at the last read */
    unsigned look_at;
    int id_as_low;        /* stock's PD18 read gives 0 (ringnav_gpio_value) */
    unsigned own_at;     /* when the Q2's own bus power last ended; own: PD17 not read high since */
    int own, id_low;     /* id_low: PD18 as the last pass read it */
    int play;            /* a Play refused while an accessory is on its way (ringnav_forbid_play) */
    int resume;          /* the gate asked for a Play of the song there (usb_otg_forbid_resume) */
    unsigned play_at;
    unsigned trace_at; /* the trace's last beat; traced: its lines this episode */
    int traced, sampled; /* sampled: a beat taken this episode */
    int trace_id, trace_dev; /* PD18 and the host port's device at the last beat */
    int changes;             /* lines for a change in this grant past its trial (OTG_TRACE_CHANGES) */
    unsigned trial_end_at; /* the last trial's end; wrong: 1 since, with the ID low; 2 its toast shown */
    int wrong;
    unsigned nosrc_at; /* PD17 low with no source since; nosrc: 1 so, 2 said */
    int nosrc;
    int in_pass; /* inside stock's pass (otg_stock): otg_stop_sleep leaves out player_save_stop's wait */
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
 * off, the off's line naming why (off: OTG_OFF_*); its end is VBUS leaving the port, and the
 * controller held at device again. id_as_low: stock reads PD18 low, so its pass is the host one
 * (the DAC's discovery, its end) whatever the pin. */
static int otg_stock(unsigned now, int id_as_low, const char *off) {
    int was = g_last_otgid;
    otg.id_as_low = id_as_low;
    otg.in_pass = 1;
    int result = stock_otg_status_trampoline();
    otg.in_pass = 0;
    otg.id_as_low = 0;
    if (g_last_otgid != was) {
        usb_record(g_last_otgid == 1 ? "otg-on" : off);
        if (g_last_otgid == 1) {
            otg.host_at = now;
            otg.id_high = 0, otg.had_dev = 0, otg.changes = 0;
            otg.connected = 0, otg.unread = 0, otg.look_at = now - OTG_CONNECT_LOOK_MS;
            otg.look = config_value("Q2POD", "USBCONNECT", 2, 1);
        } else {
            gone(now);
            otg.own = 1, otg.own_at = now;
            otg_hold();
        }
    }
    return result;
}

/* player_save_stop's usleep(300000) after mclStop (0x52077c, tools/build.py OTG_STOP_SLEEP_CALL). Stock's pass calls
 * player_save_stop on the UI thread when a USB DAC comes or goes, then restarts the song on the new way
 * (player_save_start); mclStop has already waited for q2player's stopstatus, which it sends once its PCM is closed
 * (player/engine.c), so inside the pass the wait only held the UI (docs/internals.md#otg-host-power). Every other
 * caller keeps it: q2video or a game opening bluealsa's PCM after a stop needs bluealsa to have seen the close. */
int otg_stop_sleep(unsigned us) { return otg.in_pass ? 0 : usleep(us); }

/* PD17's VBUS (1 for VBUS on the port) as a cable's: 0 while the Q2's own bus power, ended, dies
 * away (OTG_OWN_VBUS_MS). Every PD17 read of this file and stock's cable read (usb_charge.c
 * ringnav_usb_detect, so g_usbdet_value, the cable in, its gadget and its Bluetooth connect) pass
 * through it. While the Q2 powers the bus the read is stock's own, which then counts no cable. */
int usb_otg_cable(int vbus) {
    if (g_last_otgid == 1) return vbus;
    if (otg.own && (!vbus || (unsigned)time_now_ms() - otg.own_at >= OTG_OWN_VBUS_MS)) otg.own = 0;
    if (!vbus || otg.own) {
        otg.nosrc = 0;
        return 0;
    }
    /* PD17 low with the PMIC seeing no source (OTG_ONLINE): an accessory, not a cable; past
     * OTG_NO_SOURCE_MS, said once. The bus power is not tried for it: with PD18 high the grant would
     * leave the controller a device (OTG_FROM_PIN), and a re-seat brought the ID low. With no PMIC
     * file PD17 is read as it is. */
    char on[4] = { 0 };
    void *f = fopen(OTG_ONLINE, "rb");
    int got = f ? (int)fread(on, 1, sizeof on - 1, f) : 0;
    if (f) fclose(f);
    if (got <= 0 || on[0] != '0') {
        otg.nosrc = 0;
        return 1;
    }
    unsigned now = time_now_ms();
    if (!otg.nosrc) otg.nosrc = 1, otg.nosrc_at = now;
    if (otg.nosrc == 1 && now - otg.nosrc_at >= OTG_NO_SOURCE_MS) {
        otg.nosrc = 2;
        usb_record("no-source");
        toast(OTG_REPLUG);
    }
    return 0;
}

/* 1 while the Q2 powers the bus, or its ended power has not yet read as gone (usb_ui.c: no
 * Bluetooth connect for a cable then). */
int usb_otg_own_vbus(void) {
    if (g_last_otgid == 1) return 1;
    if (otg.own && (unsigned)time_now_ms() - otg.own_at >= OTG_OWN_VBUS_MS) otg.own = 0;
    return otg.own;
}

/* toolsGpioGetValue (0x5d1c3c, tools/build.py HOOKS): stock's read of a pin, but PD18 reads 0 inside
 * stock's pass while the host role is kept over an ID high (otg_stock), and the jacks' PA07 and PA08
 * read unplugged only once that lasts (jack_detect.c). Only check_otg_status and this file's passes
 * read PD18, all on the UI thread. */
int ringnav_gpio_value(const char *name) {
    if (otg.id_as_low && name && !strcmp(name, "PD18")) return 0;
    return jack_read(name, stock_gpio_value_trampoline(name));
}

static int host_device(void) {
    void *f = fopen(OTG_HOST_DEV, "rb");
    if (f) fclose(f);
    return f != 0;
}

/* HPRT0's connect status from the dwc2's regdump (OTG_CONNECT_MS): 1 or 0, or -1 when it cannot be
 * read (no debugfs mounted, as in a release build). */
static int host_connect(void) {
    void *f = fopen(OTG_REGDUMP, "rb");
    if (!f) return -1;
    char *dump = calloc(1, OTG_REGDUMP_MAX + 1);
    int n = dump ? (int)fread(dump, 1, OTG_REGDUMP_MAX, f) : 0;
    fclose(f);
    int v = -1;
    for (char *s = n > 0 ? dump : 0; s && (s = strstr(s, "HPRT0")); s += 5) {
        if (s != dump && s[-1] != '\n') continue;
        char *x = strstr(s, "0x");
        if (!x || x - s > 12) break;
        unsigned h = 0;
        for (x += 2; (*x >= '0' && *x <= '9') || (*x >= 'a' && *x <= 'f'); ++x)
            h = h << 4 | (unsigned)(*x <= '9' ? *x - '0' : *x - 'a' + 10);
        v = (int)(h & 1);
        break;
    }
    free(dump);
    return v;
}

int ringnav_otg_status(void) {
    unsigned now = time_now_ms();
    int gap = otg.have_last && now - otg.last > OTG_GAP_MS;
    otg.last = now;
    otg.have_last = 1;
    if (gap) otg.refused = 0;                  /* back from Standby: a fresh try */
    int vbus = usb_otg_cable(toolsGpioGetValue("PD17") == 0); /* usb_detect's read: 0 is VBUS */
    if (otg.vbus_prev && !vbus) gone(now);
    otg.vbus_prev = vbus;
    int id_low = toolsGpioGetValue("PD18") == 0;
    otg.id_low = id_low;
    /* Host already: PD17 reads the Q2's own bus power now, so it tells nothing. A device on the
     * host port keeps it; none within OTG_TRIAL_MS ends it, stock's pins written as its own device
     * pass writes them. An ID high ends it (stock's pass reading the pin, which turns it off) once
     * the port's device has gone, or after OTG_ID_HIGH_MS with none; until then stock's pass is run
     * as the host one. A device gone with the ID still low ends it past the trial as an empty
     * trial's end does, recorded as gone. */
    if (g_last_otgid == 1) {
        otg.waiting = 0;
        int dev = host_device();
        if (dev) otg.had_dev = 1;
        if (!id_low) {
            if (!otg.id_high) otg.id_high = 1, otg.id_high_at = now;
            int end = otg.had_dev ? !dev : now - otg.id_high_at >= OTG_ID_HIGH_MS;
            return otg_stock(now, !end, !end ? OTG_OFF_STOCK : otg.had_dev ? OTG_OFF_GONE : OTG_OFF_ID_HIGH);
        }
        otg.id_high = 0;
        if (otg.look && !dev && !otg.connected && now - otg.look_at >= OTG_CONNECT_LOOK_MS) {
            otg.look_at = now;
            int c = host_connect();
            if (c == 1) otg.connected = 1;
            else if (c == 0) otg.unread = 1;
            else otg.look = 0; /* no regdump: the OTG_TRIAL_MS trial */
        }
        int no_connect =
            otg.look && otg.unread && !otg.connected && now - otg.host_at >= OTG_CONNECT_MS;
        if ((now - otg.host_at >= OTG_TRIAL_MS || no_connect) && !dev) {
            toolsGpioSetValue("PC20", 0);
            toolsGpioSetValue("PE22", 0); /* the charge guard (usb_charge.c) sets its own next */
            g_last_otgid = 0;
            otg.refused = 1;
            otg.wrong = 1, otg.trial_end_at = now;
            gone(now);
            otg.own = 1, otg.own_at = now;
            otg_hold();
            usb_record(otg.had_dev  ? OTG_OFF_GONE
                       : no_connect ? "otg-trial-off why=no-connect"
                                    : "otg-trial-off");
            return 0;
        }
        return otg_stock(now, 0, OTG_OFF_STOCK);
    }
    if (vbus) otg.refused = 0; /* a cable's VBUS: PC20 is off, so it is not the Q2's own */
    /* The roles the wrong way round (OTG_WRONG_MS): said once, until the ID goes high or VBUS comes. */
    if (!id_low || vbus) otg.wrong = 0;
    if (otg.wrong == 1 && now - otg.trial_end_at >= OTG_WRONG_MS) {
        otg.wrong = 2;
        usb_record("wrong-role");
        toast(OTG_REPLUG);
    }
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
    if (!id_low) return otg_stock(now, 0, OTG_OFF_STOCK);
    if (otg.waiting && (accessory || now - otg.since >= OTG_SETTLE_MS)) {
        system(OTG_FROM_PIN);
        int result = otg_stock(now, 0, OTG_OFF_STOCK);
        if (g_last_otgid != 1) otg_hold();
        return result;
    }
    return 0;
}

/* An accessory on its way: with no USB DAC yet, its ID low and waited out (the blank or the
 * settle), or its bus power on for less than OTG_PLAY_GRANT_MS, the ID either way. */
static int attaching(void) {
    if (g_otg_status == 2 || otg.refused) return 0;
    if (g_last_otgid == 1) return (unsigned)time_now_ms() - otg.host_at < OTG_PLAY_GRANT_MS;
    return otg.id_low && (otg.blanked || otg.waiting);
}

/* check_forbid_play_no_headset (0x4f2cac, tools/build.py HOOKS): stock's gate, but for a start with
 * nothing to play to while an accessory is on its way (the top of this file). Only a Play of the
 * song there (stock's Play key, or usb_otg_forbid_resume) is kept: started again later it is the
 * same Play, where a station, a video, a skip or a row's song would be the paused song instead. */
int ringnav_forbid_play(int quiet) {
    if (g_testmode_flag || !g_block_withoutphone_flag || !attaching() || has_valid_output_device())
        return stock_forbid_play_trampoline(quiet); /* stock's calls alone: no clock read before it */
    if (!quiet) {
        toast(OTG_CONNECTING);
        if (otg.resume || __builtin_return_address(0) == (void *)OTG_PLAYPAUSE_RET)
            otg.play = 1, otg.play_at = time_now_ms();
    }
    return 1;
}

/* The gate, check_forbid_play_no_headset(0) and so ringnav_forbid_play above, for a Play of the song
 * paused or stopped there, kept while an accessory attaches: the Play key's player_play_pause
 * (headset.c). */
int usb_otg_forbid_resume(void) {
    otg.resume = 1;
    int refused = check_forbid_play_no_headset(0);
    otg.resume = 0;
    return refused;
}

/* The pin trace, from each UI pass (the top of OTG_TRACE_MS): what the board's ID and VBUS do around
 * an unplug, an accessory and the bus power, in the USB record; nothing while they rest. A grant
 * past its trial (kept: its device on the host port) writes a line only when PD18 or the device
 * changes (OTG_TRACE_CHANGES). */
static void trace(unsigned now) {
    int host = g_last_otgid == 1;
    int moving = (!host && otg.id_low) || (host && (now - otg.host_at < OTG_TRIAL_MS || otg.id_high)) ||
                 (otg.have_gone && now - otg.gone_at < OTG_TRACE_AFTER_MS);
    if (!moving && !host) {
        otg.traced = 0, otg.sampled = 0;
        return;
    }
    if (moving && otg.traced >= OTG_TRACE_MAX) return;
    if (!moving && otg.changes >= OTG_TRACE_CHANGES) return;
    if (otg.sampled && now - otg.trace_at < OTG_TRACE_MS) return;
    /* on a 250 ms beat, not 250 ms after the pass that wrote; passes further apart (the screen off)
     * write one line each, with no catching up */
    otg.trace_at = otg.sampled && now - otg.trace_at < 2 * OTG_TRACE_MS ? otg.trace_at + OTG_TRACE_MS : now;
    int dev = host_device(), id = toolsGpioGetValue("PD18");
    int changed = !otg.sampled || dev != otg.trace_dev || id != otg.trace_id;
    otg.sampled = 1, otg.trace_dev = dev, otg.trace_id = id;
    if (moving)
        ++otg.traced;
    else if (changed)
        ++otg.changes;
    else
        return;
    usb_record_pins(dev);
}

/* From usb_ui_poll, every UI pass: the pin trace, and a remembered Play started once there is
 * somewhere to play (the DAC, its way switched by stock's host pass, or any other output), or given
 * up. */
void usb_otg_poll(void) {
    unsigned now = time_now_ms();
    trace(now);
    /* The bus power on with no USB DAC or drive yet, within its trial: stock's host pass (the DAC's
     * discovery) every UI pass, not only on_wm_idle_status's once a second, so a Play kept for it
     * starts as soon as its PCM is there. */
    if (g_last_otgid == 1 && g_otg_status == 0 && now - otg.host_at < OTG_TRIAL_MS) ringnav_otg_status();
    if (!otg.play) return;
    if (has_valid_output_device()) {
        otg.play = 0;
        if (mclGetPlayStatus() != 2) {
            player_save_stop();
            player_save_start(0);
            ulog('I', "usb", "the Play asked while USB audio connected: started");
        }
        return;
    }
    if (!attaching() || now - otg.play_at >= OTG_PLAY_MS) {
        otg.play = 0;
        stock_forbid_play_trampoline(0); /* stock's no-output message, now nothing is coming */
    }
}
