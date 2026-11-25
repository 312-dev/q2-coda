/* The USB port's state machine (docs/internals.md#usb-shadow, the design in the USB study's section 5): one pure
 * function, usb_fsm_step, that takes one event and its time and says what the Q2 would do. No I/O and no clock of
 * its own: the caller feeds it the port's signals as they change and calls usb_fsm_advance as its deadline comes.
 * q2-usbtrace (patch/q2usbtrace.c) runs it in shadow, beside stock's own logic, and only logs what it says;
 * test/unit/test_usb_fsm.py replays recorded traces through it. No libc. */
#ifndef USB_FSM_H
#define USB_FSM_H

/* Starting values, to be confirmed by the shadow's data; ms unless named otherwise. [Q] a datasheet, spec or the
 * kernel's header; [M] measured from a device log; [E] an estimate. */
#define UF_VBUS_GONE_MS 20       /* PD17 high this long ends a sink session: usb_event.c's contact bounce [M] */
#define UF_ONLINE_MS 250         /* the PMIC's online read 1 by then: a real source (read at the edge, 50 and 250 ms) */
#define UF_RESIDUE_MS 500        /* PD17 low with online 0 this long: no source, own power dying or F4 [E] */
#define UF_RESIDUE_REPLUG_MS 2000 /* still so this long after PD17 went low: "replug" once (usb_otg.c OTG_NO_SOURCE_MS) */
#define UF_CHARGER_MS 3000       /* no address from the host this long: a charger (usb_ui.c USB_PROBE_MS) */
#define UF_WINDOW_MS 10000       /* an ID low starting this soon after VBUS left may be the board's phantom
                                  * (every phantom logged began 2.15 to 7.02 s after: usb_otg.c OTG_BLANK_MS) */
#define UF_ID_FRESH_MS 200       /* outside the window: grant after this much continuous low (tCCDebounce is 100
                                  * to 200 ms [Q PD_T_CC_DEBOUNCE]; the CC chip has applied it once already) */
#define UF_ID_PHANTOM_MS 5000    /* inside the window: grant after this much [E]. The study's 3.5 s (the longest uncut
                                  * phantom logged was 2.15 s at 0.54 s samples) is short of one logged low that
                                  * lasted 4.28 s, nothing attached, before stock's power held it (q2-pull-state.log
                                  * 21:0x, test_usb_fsm.py pull-up phantom 1) */
#define UF_TRIAL_MS 3000         /* bus power with no device on the host port this long ends the trial [M: the AB13X
                                  * earbuds' 1-1 appeared 1.87 and 2.06 s after otg-on, 2026-11-23; the study's 1.5 s
                                  * would have cut both] */
#define UF_WRONG_MS 1000         /* ID still low this long after a failed trial, no VBUS: "replug" (F5) */
#define UF_RETRY_MS 2000         /* a failed trial is tried once more this long after its end */
#define UF_RETRIES 1
#define UF_VBUS_OFF_MS 650       /* after bus power ends PD17 is the Q2's own dying VBUS for this long
                                  * (tVBUSOFF [Q PD_T_SAFE_0V]); online judges it after */

#define UF_NEVER (-0x7fffffffffffffffLL) /* a time that has not happened */

enum usb_fsm_state {
    UF_IDLE,          /* no VBUS, ID high */
    UF_SINK_DEBOUNCE, /* PD17 low, the PMIC not yet asked */
    UF_RESIDUE,       /* PD17 low with online 0: nothing real; no charging, no gadget */
    UF_SINK,          /* a real source: gadget offered, waiting for an address */
    UF_HOST_PRESENT,  /* a host addressed the gadget: the mode's verdict runs (car, chooser, storage) */
    UF_CHARGER,       /* no address in UF_CHARGER_MS: charging; a late host is still taken */
    UF_SRC_CANDIDATE, /* ID low, no VBUS: waiting out the phantom */
    UF_SRC_TRIAL,     /* bus power on, waiting for a device on the host port */
    UF_SRC_COOLDOWN,  /* a trial ended with nothing: the retry, or the replug message */
    UF_REFUSED,       /* no more trials until VBUS, an ID high or a wake */
    UF_HOST,          /* a device on the host port: power kept whatever ID reads */
    UF_HOST_END,      /* the device left, power off: the own VBUS dying away */
    UF_STATES
};

enum usb_fsm_event {
    UF_EV_VBUS,    /* v 1: PD17 low (VBUS on the port, anyone's); 0: high */
    UF_EV_IDLOW,   /* v 1: PD18 low (the CC chip asks the Q2 to be the source); 0: high */
    UF_EV_ONLINE,  /* v: power_supply/usb/online as just read (1 a real source's VBUS) */
    UF_EV_POWER,   /* v: bus power seen on (PC20), whoever turned it on: the shadow reads stock's */
    UF_EV_HOSTDEV, /* v 1: the host port's device appeared (uevent add usb 1-1); 0: removed */
    UF_EV_PCM,     /* v 1: the USB DAC's playback PCM appeared (pcmC1D0p); 0: removed */
    UF_EV_UDC,     /* v 1: the UDC is addressed or configured by a host; 0: any other state */
    UF_EV_TIMER,   /* a deadline came (usb_fsm_advance gives these) */
    UF_EV_WAKE,    /* back from Standby: stock's device_standby turned the bus power off */
    UF_EVENTS
};

/* What the Q2 would do, a set of bits. */
#define UA_GRANT 0x1       /* PC20 on, the role host (USBRDT) */
#define UA_REVOKE 0x2      /* PC20 off, the role device */
#define UA_GADGET_ON 0x4   /* the chosen gadget's pull-up on (soft_connect) */
#define UA_GADGET_OFF 0x8  /* the pull-up off, so nothing is back-powered (hypothesis H1) */
#define UA_CABLE_IN 0x10   /* a real source: charging shown */
#define UA_CABLE_OUT 0x20  /* the source gone */
#define UA_HOST_SEEN 0x40  /* a host addressed the gadget: the mode's verdict starts */
#define UA_CHARGER 0x80    /* nothing addressed it: "charger" */
#define UA_REPLUG 0x100    /* "Unplug the cable and plug it in again", once */
#define UA_DAC 0x200       /* the DAC's PCM is there: tell demo and the player */
#define UA_DAC_GONE 0x400  /* it went */

struct usb_fsm {
    int state;
    int vbus, idlow, online, power, hostdev, pcm, udc; /* the last value fed of each, -1 none yet */
    int granted;              /* the machine's own bus power */
    int retries;              /* trials used since the ID went low */
    int said_replug;          /* UA_REPLUG given this episode */
    int refused;              /* no trial until VBUS from a real source, an ID high or a wake */
    int done;                 /* the state's one-shot timer has fired (RESIDUE's replug, SRC_COOLDOWN's check) */
    long long t_enter;        /* the state's start */
    long long t_vbus;         /* PD17 went low (the sink session's start) */
    long long t_vbus_high;    /* PD17 went high in a sink state, the debounce's start; UF_NEVER while low */
    long long t_gone;         /* VBUS last left the port, the Q2's own power's end included */
    long long t_idlow;        /* the present ID low's start */
    long long t_off;          /* bus power last went off */
    long long need_ns;        /* the continuous low SRC_CANDIDATE waits for: UF_ID_FRESH_MS or UF_ID_PHANTOM_MS */
    long long t_power;        /* the bus power seen on for the present grant: the trial is timed from it */
    int shadow;               /* the bus power is another's (stock's): a grant only says what would be done, and
                               * its trial waits for the power to be seen. 0 when the machine owns PC20 itself. */
};

struct usb_fsm_out {
    unsigned actions;   /* UA_* */
    int state;          /* after the step */
    long long deadline; /* call usb_fsm_advance at this time; UF_NEVER for none */
    const char *why;    /* the rule that acted, for the log ("" none) */
};

void usb_fsm_init(struct usb_fsm *f);
/* One event at t_ns (CLOCK_MONOTONIC, never earlier than the last): the actions it causes in out. Timers due by
 * t_ns are not fired here: usb_fsm_feed does both. */
unsigned usb_fsm_step(struct usb_fsm *f, int ev, int v, long long t_ns, struct usb_fsm_out *out);
/* The timers due by t_ns fired in order, each at its own time; the actions ORed into out (out->why the last). */
unsigned usb_fsm_advance(struct usb_fsm *f, long long t_ns, struct usb_fsm_out *out);
/* usb_fsm_advance to t_ns, then the event: what a caller feeds a recorded trace with. */
unsigned usb_fsm_feed(struct usb_fsm *f, int ev, int v, long long t_ns, struct usb_fsm_out *out);
/* The earliest deadline pending, or UF_NEVER. */
long long usb_fsm_deadline(const struct usb_fsm *f);
const char *usb_fsm_state_name(int state);
/* An action bit's word ("grant", "revoke", ...), or "" for none or several. */
const char *usb_fsm_action_name(unsigned bit);

#endif
