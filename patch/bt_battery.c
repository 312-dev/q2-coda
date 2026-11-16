/* A Bluetooth headset's battery for the status row (docs/internals.md#headset-battery):
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
 * BT_EXPERIMENTAL). The sample rate is bluealsa's: its A2DP source PCM's Sampling, from
 * org.bluealsa.Manager1's GetPCMs.
 *
 * demo has no D-Bus client of its own, so a shell loop asks: started once per connection (the
 * first call that finds a connected headset in stock's link list and no loop for it), it reads the
 * device's Class once, then Percentage and the rate every BATT_POLL_S seconds while the device
 * stays Connected, writing "MAC PCT RATE CLASS" (each empty for none) whole to BATT_FILE, and
 * empties the file when it ends. A call reads that file at most every BATT_READ_MS and takes it
 * only for the headset connected now; a loop that ended while stock still lists the headset as
 * connected (BlueZ answered once with the link going) is started again, at most every
 * BATT_RESTART_MS. */
#include "navigation.h"

#define BATT_FILE "/tmp/q2-btbatt"
#define BATT_POLL_S 30
#define BATT_READ_MS 5000
#define BATT_RESTART_MS                                                                            \
    60000 /* a loop that ended with the headset still listed starts again after this */

static struct {
    char mac[24];        /* the headset the loop was started for */
    unsigned read_at;    /* BATT_FILE last read (time_now_ms), 0 never */
    unsigned started_at; /* the loop started (time_now_ms) */
    int level;           /* ... what it said for mac, -1 none */
    unsigned rate, cls;  /* ... the stream's Hz and the Class, 0 none */
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

/* The loop for mac, in the background. */
static void batt_start(const char *mac) {
    char dev[24], cmd[1536];
    tk_snprintf(dev, sizeof dev, "dev_%s", mac);
    for (char *p = dev; *p; ++p)
        if (*p == ':') *p = '_';
    tk_snprintf(
        cmd, sizeof cmd,
        "(g='dbus-send --system --print-reply --dest=org.bluez /org/bluez/hci0/%s "
        "org.freedesktop.DBus.Properties.Get'; f=" BATT_FILE "; "
        "c=$($g string:org.bluez.Device1 string:Class 2>/dev/null | sed -n 's/.*uint32 //p'); "
        "while $g string:org.bluez.Device1 string:Connected 2>/dev/null | grep -q 'boolean true'; "
        "do "
        "p=$($g string:org.bluez.Battery1 string:Percentage 2>/dev/null | sed -n 's/.*byte //p'); "
        "r=$(dbus-send --system --print-reply --dest=org.bluealsa /org/bluealsa "
        "org.bluealsa.Manager1.GetPCMs 2>/dev/null | sed -n '/object path "
        "\"\\/org\\/bluealsa\\/hci0\\/%s\\/a2dpsrc/,/object path \"\\/org\\/bluealsa/"
        "{/\"Sampling\"/{n;s/.*uint32 //p;}}' | head -n 1); "
        "echo \"%s $p $r $c\" > $f.new && mv -f $f.new $f; sleep %d; done; : > $f) "
        ">/dev/null 2>&1 &",
        dev, dev, mac, BATT_POLL_S);
    system(cmd);
}

/* A field of digits at *p, past it and its space; 0 for an empty one, max+1 past max. */
static unsigned batt_field(const char **p, unsigned max) {
    unsigned v = 0;
    for (; **p >= '0' && **p <= '9'; ++*p) v = v > max ? v : v * 10 + (unsigned)(**p - '0');
    if (**p == ' ') ++*p;
    return v;
}

int bt_headset_battery(void) {
    char mac[24], raw[96];
    unsigned now = (unsigned)time_now_ms();
    if (!g_bluetoothflag || !batt_connected(mac) || !bt_mac_ok(mac)) {
        bb.mac[0] = 0; /* gone: its next connection starts a loop of its own */
        return -1;
    }
    if (strcmp(mac, bb.mac)) { /* a new connection: its own loop, nothing known yet */
        tk_snprintf(bb.mac, sizeof bb.mac, "%s", mac);
        bb.level = -1, bb.rate = bb.cls = 0, bb.read_at = 0, bb.started_at = now;
        batt_start(mac);
    }
    if (bb.read_at && now - bb.read_at < BATT_READ_MS) return bb.level;
    bb.read_at = now ? now : 1;
    bb.level = -1, bb.rate = bb.cls = 0;
    if (!state_read(BATT_FILE, raw, sizeof raw)) {
        if (now - bb.started_at >= BATT_RESTART_MS) bb.started_at = now, batt_start(mac);
        return -1;
    }
    unsigned n = strlen(mac);
    if (strncmp(raw + 1, mac, n) || raw[1 + n] != ' ') return -1; /* another device's, or none */
    const char *p = raw + 2 + n;
    int none = *p < '0' || *p > '9';
    unsigned level = batt_field(&p, 100);
    bb.level = none || level > 100 ? -1 : (int)level;
    bb.rate = batt_field(&p, 384000);
    bb.cls = batt_field(&p, 0xffffff);
    if (bb.rate > 384000) bb.rate = 0;
    if (bb.cls > 0xffffff) bb.cls = 0;
    return bb.level;
}
/* The connected device's stream rate (Hz) and Class, as bt_headset_battery reads them; 0 unknown.
 */
unsigned bt_headset_rate(void) {
    bt_headset_battery();
    return bb.mac[0] ? bb.rate : 0;
}
unsigned bt_headset_class(void) {
    bt_headset_battery();
    return bb.mac[0] ? bb.cls : 0;
}
