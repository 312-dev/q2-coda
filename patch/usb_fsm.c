/* The USB port's state machine (usb_fsm.h, docs/internals.md#usb-shadow): the rules of the USB study's section 5
 * as one pure function. Every time is the caller's, in ns; nothing here reads a clock, a pin or a file. */
#include "usb_fsm.h"

#define MS 1000000LL

static const char *const state_names[UF_STATES] = {
    "idle",   "sink-debounce", "residue", "sink",     "host-present", "charger",
    "src-candidate", "src-trial", "src-cooldown", "refused", "host", "host-end",
};

const char *usb_fsm_state_name(int state) {
    return state >= 0 && state < UF_STATES ? state_names[state] : "?";
}

const char *usb_fsm_action_name(unsigned bit) {
    switch (bit) {
    case UA_GRANT: return "grant";
    case UA_REVOKE: return "revoke";
    case UA_GADGET_ON: return "gadget-on";
    case UA_GADGET_OFF: return "gadget-off";
    case UA_CABLE_IN: return "cable-in";
    case UA_CABLE_OUT: return "cable-out";
    case UA_HOST_SEEN: return "host-seen";
    case UA_CHARGER: return "charger";
    case UA_REPLUG: return "replug";
    case UA_DAC: return "dac";
    case UA_DAC_GONE: return "dac-gone";
    default: return "";
    }
}

void usb_fsm_init(struct usb_fsm *f) {
    f->state = UF_IDLE;
    f->vbus = f->idlow = f->online = f->power = f->hostdev = f->pcm = f->udc = f->connect = -1;
    f->granted = f->retries = f->said_replug = f->refused = f->done = f->shadow = f->no_device = 0;
    f->t_enter = 0;
    f->t_vbus = f->t_vbus_high = f->t_gone = f->t_idlow = f->t_off = f->t_power = UF_NEVER;
    f->need_ns = UF_ID_FRESH_MS * MS;
}

static void enter(struct usb_fsm *f, int s, long long t) {
    f->state = s, f->t_enter = t, f->done = 0;
}

static int sink_state(int s) {
    return s == UF_SINK_DEBOUNCE || s == UF_RESIDUE || s == UF_SINK || s == UF_HOST_PRESENT || s == UF_CHARGER;
}
static int cable_state(int s) { /* charging shown and a gadget offered */
    return s == UF_SINK || s == UF_HOST_PRESENT || s == UF_CHARGER;
}

/* PD17 low is the Q2's own VBUS: bus power on, or dying away for UF_VBUS_OFF_MS after it ended */
static int own_vbus(const struct usb_fsm *f, long long t) {
    return f->granted || f->power == 1 || (f->t_off != UF_NEVER && t - f->t_off < UF_VBUS_OFF_MS * MS);
}

/* The machine's own bus power ends (or someone's it was holding): the dying VBUS window starts */
static unsigned power_off(struct usb_fsm *f, long long t) {
    unsigned a = f->granted ? UA_REVOKE : 0;
    f->granted = 0, f->t_off = t, f->t_gone = t;
    return a;
}

/* A real source: charging and the gadget at once; a host that already addressed it is seen too */
static unsigned to_sink(struct usb_fsm *f, long long t, struct usb_fsm_out *out) {
    enter(f, UF_SINK, t);
    f->refused = 0, f->said_replug = 0, f->retries = 0;
    out->why = "online 1: a real source";
    if (f->udc == 1) {
        enter(f, UF_HOST_PRESENT, t);
        return UA_CABLE_IN | UA_GADGET_ON | UA_HOST_SEEN;
    }
    return UA_CABLE_IN | UA_GADGET_ON;
}

static void sink_starts(struct usb_fsm *f, long long t, struct usb_fsm_out *out) {
    enter(f, UF_SINK_DEBOUNCE, t);
    f->t_vbus = t, f->t_vbus_high = UF_NEVER;
    out->why = "PD17 low: ask the PMIC";
}

/* ID low with nothing else going on: wait out the board's phantom, longer inside the window after VBUS left */
static void id_low_starts(struct usb_fsm *f, long long t, struct usb_fsm_out *out) {
    if (f->refused) {
        enter(f, UF_REFUSED, t);
        out->why = "ID low: refused until VBUS, an ID high or a wake";
        return;
    }
    int window = f->t_gone != UF_NEVER && f->t_idlow - f->t_gone < UF_WINDOW_MS * MS;
    f->need_ns = (window ? UF_ID_PHANTOM_MS : UF_ID_FRESH_MS) * MS;
    enter(f, UF_SRC_CANDIDATE, t);
    out->why = window ? "ID low inside the window: wait UF_ID_PHANTOM_MS" : "ID low: wait UF_ID_FRESH_MS";
}

/* Bus power only with the PMIC saying no source (online 0) and PD17 high, or low from bus power already on */
static int may_grant(const struct usb_fsm *f) {
    return f->online == 0 && (f->vbus == 0 || f->power == 1);
}

static unsigned grant(struct usb_fsm *f, long long t, struct usb_fsm_out *out, const char *why) {
    f->granted = 1, ++f->retries;
    f->t_power = !f->shadow || f->power == 1 ? t : UF_NEVER; /* in shadow, timed once stock's power is seen */
    enter(f, UF_SRC_TRIAL, t);
    out->why = why;
    return UA_GRANT;
}

/* The trial's length: UF_CONNECT_MS while HPRT0 reads no connect, UF_TRIAL_SLOW_MS once it has, UF_TRIAL_MS when it
 * has not been read */
static long long trial_ns(const struct usb_fsm *f) {
    return (f->connect == 0 ? UF_CONNECT_MS : f->connect == 1 ? UF_TRIAL_SLOW_MS : UF_TRIAL_MS) * MS;
}

long long usb_fsm_deadline(const struct usb_fsm *f) {
    long long d = UF_NEVER;
#define AT(x)                                                                                                 \
    do {                                                                                                      \
        long long x_ = (x);                                                                                   \
        if (d == UF_NEVER || x_ < d) d = x_;                                                                  \
    } while (0)
    if (sink_state(f->state) && f->t_vbus_high != UF_NEVER) AT(f->t_vbus_high + UF_VBUS_GONE_MS * MS);
    switch (f->state) {
    case UF_SINK_DEBOUNCE: AT(f->t_enter + UF_RESIDUE_MS * MS); break;
    case UF_RESIDUE:
        if (!f->done) AT(f->t_vbus + UF_RESIDUE_REPLUG_MS * MS);
        break;
    case UF_SINK: AT(f->t_enter + UF_CHARGER_MS * MS); break;
    case UF_SRC_CANDIDATE: AT(f->t_idlow + f->need_ns); break;
    case UF_SRC_TRIAL:
        if (f->t_power != UF_NEVER) AT(f->t_power + trial_ns(f));
        break;
    case UF_SRC_COOLDOWN:
        if (!f->done) AT(f->t_off + UF_WRONG_MS * MS);
        AT(f->t_off + UF_RETRY_MS * MS);
        break;
    case UF_HOST_END:
        if (f->power != 1) AT(f->t_off + UF_VBUS_OFF_MS * MS); /* else the power's end starts the wait */
        break;
    }
#undef AT
    return d;
}

static unsigned timer(struct usb_fsm *f, long long t, struct usb_fsm_out *out) {
    if (sink_state(f->state) && f->t_vbus_high != UF_NEVER && t - f->t_vbus_high >= UF_VBUS_GONE_MS * MS) {
        unsigned a = cable_state(f->state) ? UA_CABLE_OUT | UA_GADGET_OFF : 0;
        f->t_gone = f->t_vbus_high, f->t_vbus_high = UF_NEVER;
        f->said_replug = 0; /* the next seat's replug is its own */
        enter(f, UF_IDLE, t);
        out->why = "PD17 high for UF_VBUS_GONE_MS: the source left";
        if (f->idlow == 1) {
            f->t_idlow = t; /* the low held under the source counts from now */
            id_low_starts(f, t, out);
        }
        return a;
    }
    switch (f->state) {
    case UF_SINK_DEBOUNCE:
        if (t - f->t_enter >= UF_RESIDUE_MS * MS) {
            enter(f, UF_RESIDUE, t);
            out->why = "online not 1 for UF_RESIDUE_MS: no source";
        }
        return 0;
    case UF_RESIDUE:
        if (!f->done && t - f->t_vbus >= UF_RESIDUE_REPLUG_MS * MS) {
            f->done = 1;
            if (!f->said_replug) {
                f->said_replug = 1;
                out->why = "no source for UF_RESIDUE_REPLUG_MS: replug";
                return UA_REPLUG;
            }
        }
        return 0;
    case UF_SINK:
        if (t - f->t_enter >= UF_CHARGER_MS * MS) {
            enter(f, UF_CHARGER, t);
            out->why = "no address in UF_CHARGER_MS: a charger";
            return UA_CHARGER;
        }
        return 0;
    case UF_SRC_CANDIDATE:
        if (t - f->t_idlow < f->need_ns) return 0;
        if (may_grant(f)) return grant(f, t, out, "ID low long enough, online 0, PD17 high: grant");
        f->refused = 1;
        enter(f, UF_REFUSED, t);
        out->why = f->online == 1 ? "no grant: online 1" : f->online < 0 ? "no grant: online not read" : "no grant: PD17 low";
        return 0;
    case UF_SRC_TRIAL:
        if (f->hostdev == 1) { /* there before the grant (followed under another's power) */
            enter(f, UF_HOST, t);
            return 0;
        }
        if (f->t_power == UF_NEVER || t - f->t_power < trial_ns(f)) return 0;
        f->no_device = f->connect == 0;
        enter(f, UF_SRC_COOLDOWN, t);
        out->why = f->no_device ? "no connect on the host port in UF_CONNECT_MS: off"
                                : "nothing on the host port in UF_TRIAL_MS: off";
        return power_off(f, t);
    case UF_SRC_COOLDOWN: {
        unsigned a = 0;
        if (!f->done && t - f->t_off >= UF_WRONG_MS * MS) {
            f->done = 1;
            if (f->idlow == 1 && (f->vbus != 1 || own_vbus(f, t)) && !f->said_replug) {
                f->said_replug = 1;
                out->why = "ID still low after the trial, no VBUS: replug";
                a = UA_REPLUG;
            }
        }
        if (t - f->t_off < UF_RETRY_MS * MS) return a;
        if (f->idlow != 1) {
            enter(f, UF_IDLE, t);
            return a;
        }
        /* none after a trial with no connect: nothing is there to enumerate, and a replug (said by now) must not
         * meet the Q2's 5 V (F3) */
        if (!f->no_device && f->retries <= UF_RETRIES && may_grant(f))
            return a | grant(f, t, out, "the trial once more");
        f->refused = 1;
        enter(f, UF_REFUSED, t);
        out->why = f->no_device ? "no device on the host port: refused" : "trials used: refused";
        return a;
    }
    case UF_HOST_END:
        if (f->power == 1 || t - f->t_off < UF_VBUS_OFF_MS * MS) return 0;
        if (f->vbus == 1)
            sink_starts(f, t, out);
        else if (f->idlow == 1) {
            f->refused = 1;
            enter(f, UF_REFUSED, t);
            out->why = "device gone, ID still low: refused";
        } else
            enter(f, UF_IDLE, t);
        return 0;
    }
    return 0;
}

unsigned usb_fsm_step(struct usb_fsm *f, int ev, int v, long long t, struct usb_fsm_out *out) {
    unsigned a = 0;
    out->why = "";
    switch (ev) {
    case UF_EV_VBUS: {
        int was = f->vbus;
        f->vbus = v, f->online = -1; /* the PMIC's last read was of the other VBUS */
        if (v) {
            if (sink_state(f->state))
                f->t_vbus_high = UF_NEVER; /* a bounce back */
            else if (own_vbus(f, t))
                out->why = "PD17 low: the Q2's own VBUS";
            else
                sink_starts(f, t, out);
        } else {
            if (sink_state(f->state)) {
                if (f->t_vbus_high == UF_NEVER) f->t_vbus_high = t;
            } else if (was == 1)
                f->t_gone = t; /* VBUS left (a first read of none is not a leaving) */
        }
        break;
    }
    case UF_EV_IDLOW:
        f->idlow = v;
        if (v) {
            f->t_idlow = t;
            if (f->state == UF_IDLE) id_low_starts(f, t, out);
        } else {
            switch (f->state) {
            case UF_SRC_CANDIDATE:
            case UF_REFUSED:
            case UF_SRC_COOLDOWN:
                enter(f, UF_IDLE, t);
                out->why = "ID high";
                /* fall through */
            case UF_IDLE: f->refused = 0, f->retries = 0, f->said_replug = 0; break;
            case UF_SRC_TRIAL:
                if (f->t_power != UF_NEVER) break; /* powered: the trial decides, whatever ID reads */
                a = power_off(f, t);
                enter(f, UF_IDLE, t);
                f->refused = 0, f->retries = 0, f->said_replug = 0;
                out->why = "ID high before the bus power was seen";
                break;
            }
        }
        break;
    case UF_EV_ONLINE:
        f->online = v;
        if (v == 1 && f->granted) { /* a real source on a powered bus: never both */
            a = power_off(f, t);
            out->why = "online 1 with bus power on: off";
            if (f->vbus == 1) a |= to_sink(f, t, out);
            else enter(f, UF_IDLE, t);
        } else if (v == 1 && f->vbus == 1 && (f->state == UF_SINK_DEBOUNCE || f->state == UF_RESIDUE))
            a = to_sink(f, t, out);
        break;
    case UF_EV_POWER: {
        int was = f->power;
        f->power = v;
        if (v && f->t_power == UF_NEVER && f->state == UF_SRC_TRIAL) f->t_power = t;
        if (v && (f->state == UF_SINK_DEBOUNCE || f->state == UF_RESIDUE)) {
            /* the PD17 low just taken for a source was this power's: back to what it interrupted */
            if (f->idlow == 1) {
                long long low = f->t_idlow;
                id_low_starts(f, t, out);
                f->t_idlow = low;
            } else
                enter(f, UF_IDLE, t);
            out->why = "PD17 low was the bus power's";
        }
        if (!v) f->t_power = UF_NEVER;
        if (!v && was == 1) { /* the power's end, not a first read of it off */
            f->t_off = t, f->t_gone = t;
            if (f->state == UF_SRC_TRIAL) { /* in shadow: stock ended the power the trial was timed by */
                a = power_off(f, t);
                enter(f, f->idlow == 1 ? UF_SRC_COOLDOWN : UF_IDLE, t);
                out->why = "the bus power went off during the trial";
            }
            if (f->state == UF_HOST && !f->granted && f->hostdev != 1) {
                enter(f, UF_HOST_END, t);
                out->why = "the bus power it was using went off";
            }
        }
        break;
    }
    case UF_EV_HOSTDEV:
        f->hostdev = v;
        if (v) {
            if (f->state == UF_SRC_TRIAL) {
                enter(f, UF_HOST, t);
                out->why = "a device on the host port: kept";
            } else if (f->power == 1 && !sink_state(f->state) && f->state != UF_HOST) {
                enter(f, UF_HOST, t); /* under bus power the machine did not give: followed, not owned */
                out->why = "a device on the host port under bus power already on";
            }
        } else if (f->state == UF_HOST) {
            a = power_off(f, t);
            if (f->pcm == 1) a |= UA_DAC_GONE;
            f->pcm = 0;
            enter(f, UF_HOST_END, t);
            out->why = "the host port's device left: off";
        }
        break;
    case UF_EV_PCM:
        if (f->state == UF_HOST && v != f->pcm) a = v ? UA_DAC : UA_DAC_GONE;
        f->pcm = v;
        break;
    case UF_EV_UDC:
        f->udc = v;
        if (v == 1) {
            if (f->state == UF_SINK || f->state == UF_CHARGER) {
                enter(f, UF_HOST_PRESENT, t);
                out->why = "a host addressed the gadget";
                a = UA_HOST_SEEN;
            } else if (f->state == UF_SINK_DEBOUNCE && f->vbus == 1)
                a = to_sink(f, t, out); /* to_sink sees the address: host present */
        }
        break;
    case UF_EV_WAKE:
        /* stock's device_standby turned any bus power off before the suspend */
        if (f->granted || f->power == 1 || f->state == UF_HOST || f->state == UF_SRC_TRIAL) f->t_off = t;
        f->granted = 0, f->refused = 0, f->retries = 0, f->said_replug = 0;
        f->vbus = f->idlow = f->online = f->power = f->hostdev = f->pcm = f->udc = f->connect = -1;
        f->t_vbus_high = UF_NEVER, f->t_gone = t;
        enter(f, UF_IDLE, t);
        out->why = "wake: the levels are read again";
        break;
    case UF_EV_CONNECT: f->connect = v; break; /* the trial's deadline follows it */
    case UF_EV_TIMER: a = timer(f, t, out); break;
    }
    out->actions = a;
    out->state = f->state;
    out->deadline = usb_fsm_deadline(f);
    return a;
}

unsigned usb_fsm_advance(struct usb_fsm *f, long long t, struct usb_fsm_out *out) {
    unsigned all = 0;
    const char *why = "";
    for (int guard = 0; guard < 64; ++guard) { /* each timer moves the deadline on; 64 is far past any chain */
        long long d = usb_fsm_deadline(f);
        if (d == UF_NEVER || d > t) break;
        unsigned a = usb_fsm_step(f, UF_EV_TIMER, 0, d, out);
        all |= a;
        if (a || *out->why) why = out->why;
    }
    out->actions = all, out->why = why;
    out->state = f->state;
    out->deadline = usb_fsm_deadline(f);
    return all;
}

unsigned usb_fsm_feed(struct usb_fsm *f, int ev, int v, long long t, struct usb_fsm_out *out) {
    unsigned a = usb_fsm_advance(f, t, out);
    const char *why = out->why;
    a |= usb_fsm_step(f, ev, v, t, out);
    if (!*out->why) out->why = why;
    out->actions = a;
    return a;
}
