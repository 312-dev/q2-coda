/* A Bluetooth headset's battery for the status row (docs/internals.md#headset-battery):
 * bt_headset_battery() is its level, 0 to 100, or -1 with none connected or none reported.
 *
 * BlueZ shows a device's level as org.bluez.Battery1's Percentage, on the device's own object
 * (/org/bluez/hci0/dev_AA_BB_...): from the LE Battery Service, or for a classic headset from
 * bluealsa, which reads the level a headset sends over HFP (+IPHONEACCEV, +XAPL) and hands it to
 * BlueZ through org.bluez.BatteryProviderManager1. BlueZ 5.66 offers that manager only with its
 * experimental interfaces on (Experimental = true in /etc/bluetooth/main.conf, tools/build.py
 * BT_EXPERIMENTAL).
 *
 * demo has no D-Bus client of its own, so a shell loop asks: started once per connection (the
 * first call that finds a connected headset in stock's link list and no loop for it), it reads
 * Percentage every BATT_POLL_S seconds while the device stays Connected, writing "MAC PCT" (PCT
 * empty for none) whole to BATT_FILE, and empties the file when it ends. A call reads that file
 * at most every BATT_READ_MS and takes the level only for the headset connected now; a loop that
 * ended while stock still lists the headset as connected (BlueZ answered once with the link
 * going) is started again, at most every BATT_RESTART_MS. */
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
    char path[48], cmd[1024];
    tk_snprintf(path, sizeof path, "/org/bluez/hci0/dev_%s", mac);
    for (char *p = path; *p; ++p)
        if (*p == ':') *p = '_';
    tk_snprintf(
        cmd, sizeof cmd,
        "(g='dbus-send --system --print-reply --dest=org.bluez %s "
        "org.freedesktop.DBus.Properties.Get'; f=" BATT_FILE "; "
        "while $g string:org.bluez.Device1 string:Connected 2>/dev/null | grep -q 'boolean true'; "
        "do "
        "p=$($g string:org.bluez.Battery1 string:Percentage 2>/dev/null | sed -n 's/.*byte //p'); "
        "echo \"%s $p\" > $f.new && mv -f $f.new $f; sleep %d; done; : > $f) >/dev/null 2>&1 &",
        path, mac, BATT_POLL_S);
    system(cmd);
}

int bt_headset_battery(void) {
    char mac[24], raw[64];
    unsigned now = (unsigned)time_now_ms();
    if (!g_bluetoothflag || !batt_connected(mac) || !bt_mac_ok(mac)) {
        bb.mac[0] = 0; /* gone: its next connection starts a loop of its own */
        return -1;
    }
    if (strcmp(mac, bb.mac)) { /* a new connection: its own loop, nothing known yet */
        tk_snprintf(bb.mac, sizeof bb.mac, "%s", mac);
        bb.level = -1, bb.read_at = 0, bb.started_at = now;
        batt_start(mac);
    }
    if (bb.read_at && now - bb.read_at < BATT_READ_MS) return bb.level;
    bb.read_at = now ? now : 1;
    bb.level = -1;
    if (!state_read(BATT_FILE, raw, sizeof raw)) {
        if (now - bb.started_at >= BATT_RESTART_MS) bb.started_at = now, batt_start(mac);
        return -1;
    }
    unsigned n = strlen(mac);
    if (strncmp(raw + 1, mac, n) || raw[1 + n] != ' ') return -1; /* another device's, or none */
    const char *p = raw + 2 + n;
    int v = 0, digits = 0;
    for (; *p >= '0' && *p <= '9' && digits < 4; ++p, ++digits) v = v * 10 + (*p - '0');
    bb.level = digits && digits < 4 && v <= 100 ? v : -1;
    return bb.level;
}
