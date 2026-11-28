/* A Bluetooth headset's battery for Settings > Bluetooth (docs/internals.md#headset-battery):
 * bt_headset_battery() is its level, 0 to 100, or -1 with none connected or none reported; with it,
 * bt_headset_rate() is the stream's sample rate and bt_headset_class() the device's Class of
 * Device, each 0 when unknown (Settings > Bluetooth's line and page, the status row's car,
 * bt_ui.c).
 *
 * BlueZ shows a device's level as org.bluez.Battery1's Percentage, on the device's own object
 * (/org/bluez/hci0/dev_AA_BB_...): from the LE Battery Service, or for a classic headset from
 * bluealsa, which reads the level a headset sends over HFP (+IPHONEACCEV, +XAPL) and hands it to
 * BlueZ through org.bluez.BatteryProviderManager1. BlueZ 5.66 offers that manager only with its
 * experimental interfaces on (Experimental = true in /etc/bluetooth/main.conf, tools/build.py
 * BT_EXPERIMENTAL). The sample rate is bluealsa's: its A2DP source PCM's Sampling (stock's 4.0.0) or Rate
 * (5.0.0), from org.freedesktop.DBus.ObjectManager's GetManagedObjects on /org/bluealsa, which both answer
 * (docs/bluealsa.md#the-callers).
 *
 * demo has no D-Bus client of its own, so a shell asks, off the UI thread (bt_ui.c bt_shell), one
 * answer a run: "MAC PCT RATE CLASS NAME" (each empty for none; NAME, BlueZ's Alias, to the line's
 * end) written whole to BATT_FILE, read once as the run ends. The first run of a connection (the
 * first call that finds a connected headset in stock's link list) asks for all four; a later one,
 * the level and the rate alone, the Class and the name kept. A headset's level moves by a percent
 * in minutes and shows only on a lit screen (the Bluetooth page, the low toasts),
 * so a later run starts only with the screen lit: BATT_POLL_MS after the last, BATT_RETRY_MS while
 * the level is not known yet (BlueZ has it some seconds after the link, as the headset sends it),
 * at most BATT_TRIES times, and at once when the codec streaming changes, which moves the rate.
 * Dark, nothing runs. */
#include "navigation.h"

#define BATT_FILE "/tmp/q2-btbatt"
#define BATT_POLL_MS 300000 /* the level asked again this long after the last answer, lit */
#define BATT_RETRY_MS 30000 /* ... or this long while none came yet, */
#define BATT_TRIES 10       /* ... this many times a connection */

static struct {
    char mac[24];       /* the headset the runs are for */
    int asked;          /* a run started for it */
    unsigned asked_at;  /* ... the last one (time_now_ms) */
    int tries, coding;  /* unanswered runs for the level; bt_showcoding at the last run */
    unsigned seen;      /* bb.job's runs ended as last read */
    int level;          /* what it said for mac, -1 none */
    unsigned rate, cls; /* ... the stream's Hz and the Class, 0 none */
    char name[64];      /* ... and BlueZ's Alias for it, "" none */
    bt_job job;
} bb __attribute__((section(".scratch")));

/* The connected (status 3) device in stock's link list into mac[24]; 0 for none. */
static int batt_connected(char *mac) {
    char item[32] = "";
    int status = 0;
    for (int i = getBtLinkSize(); i-- > 0;) {
        getBtLinkItem(i, &status, item);
        if (status == 3) {
            tk_snprintf(mac, 24, "%s", item);
            return 1;
        }
    }
    return 0;
}

/* A run for mac: with whole, the Class and the Alias too. 1 when it started. */
static int batt_fetch(const char *mac, int whole) {
    char dev[24], cmd[1280];
    tk_snprintf(dev, sizeof dev, "dev_%s", mac);
    for (char *p = dev; *p; ++p)
        if (*p == ':') *p = '_';
    tk_snprintf(
        cmd, sizeof cmd,
        "(g='dbus-send --system --print-reply --dest=org.bluez /org/bluez/hci0/%s "
        "org.freedesktop.DBus.Properties.Get'; f=" BATT_FILE "; %s"
        "p=$($g string:org.bluez.Battery1 string:Percentage 2>/dev/null | sed -n 's/.*byte //p'); "
        "r=$(dbus-send --system --print-reply --dest=org.bluealsa /org/bluealsa "
        "org.freedesktop.DBus.ObjectManager.GetManagedObjects 2>/dev/null | sed -n '/object path "
        "\"\\/org\\/bluealsa\\/hci0\\/%s\\/a2dpsrc/,/object path \"\\/org\\/bluealsa/"
        "{/\"Sampling\"/{n;s/.*uint32 //p;};/\"Rate\"/{n;s/.*uint32 //p;}}' | head -n 1); "
        "echo \"%s $p $r $c $a\" > $f.new && mv -f $f.new $f) >/dev/null 2>&1",
        dev,
        whole ? "c=$($g string:org.bluez.Device1 string:Class 2>/dev/null | sed -n 's/.*uint32 "
                "//p'); a=$($g string:org.bluez.Device1 string:Alias 2>/dev/null | sed -n "
                "'s/.*string \"\\(.*\\)\"$/\\1/p'); "
              : "",
        dev, mac);
    return bt_shell(&bb.job, cmd);
}

/* A field of digits at *p, past it and its space; 0 for an empty one, max+1 past max. */
static unsigned batt_field(const char **p, unsigned max) {
    unsigned v = 0;
    for (; **p >= '0' && **p <= '9'; ++*p) v = v > max ? v : v * 10 + (unsigned)(**p - '0');
    if (**p == ' ') ++*p;
    return v;
}

/* BATT_FILE's answer for bb.mac: the level (none: -1), the rate when it has one, and the Class and
 * name when it has them (a later run's has neither: those read before are kept). */
static void batt_read(void) {
    char raw[96];
    unsigned n = strlen(bb.mac);
    state_read(BATT_FILE, raw, sizeof raw);
    if (strncmp(raw + 1, bb.mac, n) || raw[1 + n] != ' ') return; /* another device's, or none */
    const char *p = raw + 2 + n;
    int none = *p < '0' || *p > '9';
    unsigned level = batt_field(&p, 100), rate = batt_field(&p, 384000),
             cls = batt_field(&p, 0xffffff), n_name = 0;
    bb.level = none || level > 100 ? -1 : (int)level;
    bb.rate = rate > 384000 ? 0 : rate;
    if (cls && cls <= 0xffffff) bb.cls = cls;
    while (p[n_name] && p[n_name] != '\n' && n_name < sizeof bb.name - 1) ++n_name;
    if (n_name) tk_snprintf(bb.name, sizeof bb.name, "%.*s", (int)n_name, p);
}

int bt_headset_battery(void) {
    char mac[24];
    unsigned now = (unsigned)time_now_ms();
    if (!g_bluetoothflag || !batt_connected(mac) || !bt_mac_ok(mac)) {
        bb.mac[0] = 0; /* gone: its next connection is asked again */
        return -1;
    }
    if (strcmp(mac, bb.mac)) { /* a new connection: nothing known yet */
        tk_snprintf(bb.mac, sizeof bb.mac, "%s", mac);
        bb.level = -1, bb.rate = bb.cls = 0, bb.name[0] = 0, bb.asked = bb.tries = 0;
    }
    int coding = I(bt_showcoding, 0),
        due = bb.level < 0 && bb.tries < BATT_TRIES ? BATT_RETRY_MS : BATT_POLL_MS;
    if (!bb.asked ||
        (g_backlight_status && (now - bb.asked_at >= (unsigned)due || coding != bb.coding)))
        if (batt_fetch(mac, !bb.asked)) {
            bb.asked = 1, bb.asked_at = now, bb.coding = coding;
            if (bb.level < 0) ++bb.tries;
        }
    unsigned done = bt_job_done(&bb.job);
    if (done != bb.seen) { /* a run ended: its answer */
        bb.seen = done;
        batt_read();
        if (bb.level >= 0) bb.tries = 0;
    }
    return bb.level;
}
/* The connected device's stream rate (Hz) and Class, as bt_headset_battery reads them; 0 unknown.
 */
unsigned bt_headset_rate(void) {
    bt_headset_battery();
    return bb.mac[0] ? bb.rate : 0;
}
const char *bt_headset_name(void) {
    bt_headset_battery();
    return bb.mac[0] ? bb.name : "";
}
unsigned bt_headset_class(void) {
    bt_headset_battery();
    return bb.mac[0] ? bb.cls : 0;
}
