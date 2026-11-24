/* A Bluetooth device's Volume Limit (docs/internals.md#headset-controls-and-volume): each paired
 * device may cap the Q2's volume, 0 to 100 on its scale, while it is the connected Bluetooth
 * output; 100, No Limit, is the default and is never stored. Max Volume (g_maxvolume) still applies
 * everywhere, so over Bluetooth the volume's top is the lower of the two (vol_cap), which the wheel,
 * Control Center, Playback's Volume page, the Car Remote and the headset's own keys all stop at.
 * Wired and USB outputs never see a device's limit.
 *
 * The limits are kept in BTLIM_FILE, one "AA:BB:CC:DD:EE:FF 60" line a device with a limit,
 * BTLIM_MAX at most, written whole under another name and renamed, and read once into the cache
 * here. A line that does not read as one (a cut or corrupt file) is passed over, so a broken or
 * missing file reads as no limit for every device. Forget This Device drops its line (bt_detail.c).
 *
 * The limit pulls the volume down without saving the lower level: a device's limit is not written
 * into PLAYSET BT_VOLUME, which every Bluetooth device shares. The level it was pulled from is kept
 * (bl.from) until the volume is next saved, and is what goes back to BT_VOLUME when the output
 * leaves Bluetooth (btlim_departing) or what the next device starts from (btlim_poll). */
#include "navigation.h"

#define BTLIM_FILE "/mnt/data/q2-btvollimit"
#define BTLIM_TMP BTLIM_FILE ".tmp"
#define BTLIM_MAX 16      /* devices with a limit; stock's paired list holds fewer */
#define BTLIM_POLL_MS 250 /* how often the connected device is looked at */
#define BTLIM_NONE 100

static struct {
    int read, n;
    char mac[BTLIM_MAX][18];
    unsigned char lim[BTLIM_MAX];
    char on[18];      /* the Bluetooth output's device at the last look; "" none */
    char edit[18];    /* the device whose limit the slider page sets (btlim_edit) */
    int from, pulled; /* the level a limit pulled the volume down from, and to plus 1; 0 none */
    unsigned at;
} bl __attribute__((section(".scratch")));

/* The cache from BTLIM_FILE, once. */
static void load(void) {
    if (bl.read) return;
    bl.read = 1;
    bl.n = 0;
    char buf[BTLIM_MAX * 24 + 1];
    void *f = fopen(BTLIM_FILE, "rb");
    if (!f) return;
    int len = (int)fread(buf, 1, sizeof buf - 1, f);
    fclose(f);
    buf[len < 0 ? 0 : len] = 0;
    for (char *p = buf; *p && bl.n < BTLIM_MAX;) {
        char *end = p;
        while (*end && *end != '\n') ++end;
        int v = 0, digits = 0;
        char *q = p + 18;
        if (end - p > 18 && p[17] == ' ') {
            for (; q < end && *q >= '0' && *q <= '9' && digits < 3; ++q, ++digits)
                v = v * 10 + *q - '0';
            char save = p[17];
            p[17] = 0;
            if (digits && q == end && v < BTLIM_NONE && bt_mac_ok(p)) {
                memcpy(bl.mac[bl.n], p, 18);
                bl.lim[bl.n++] = (unsigned char)v;
            }
            p[17] = save;
        }
        p = *end ? end + 1 : end;
    }
}

static int find(const char *mac) {
    load();
    for (int i = 0; mac && i < bl.n; ++i)
        if (!strncasecmp(bl.mac[i], mac, 17)) return i;
    return -1;
}

/* The cache to BTLIM_FILE, whole. */
static void store(void) {
    char buf[BTLIM_MAX * 24 + 1];
    unsigned at = 0;
    for (int i = 0; i < bl.n; ++i)
        at += (unsigned)tk_snprintf(buf + at, sizeof buf - at, "%s %d\n", bl.mac[i], bl.lim[i]);
    void *f = fopen(BTLIM_TMP, "wb");
    if (!f) return;
    int ok = fwrite(buf, 1, at, f) == at;
    if (fclose(f) || !ok || rename(BTLIM_TMP, BTLIM_FILE)) unlink(BTLIM_TMP);
}

/* mac's limit, BTLIM_NONE for none. */
int btlim_get(const char *mac) {
    int i = find(mac);
    return i < 0 ? BTLIM_NONE : bl.lim[i];
}

/* The device the Bluetooth output goes to (stock's link list's last item, connected or still
 * connecting) into mac[24]; 0 when the output is not Bluetooth or no device is listed. */
static int output_mac(char *mac) {
    char item[32] = "";
    int status = 0, n = g_bluetoothflag && mclGetOutputWay() == 1 ? getBtLinkSize() : 0;
    if (n <= 0 || airplay_on()) return 0; /* a speaker's level is its own (airplay.c) */
    getBtLinkItem(n - 1, &status, item);
    tk_snprintf(mac, 24, "%s", item);
    return bt_mac_ok(mac);
}

/* The volume's top now: Max Volume, at most 100, and over Bluetooth the device's limit too. */
int vol_cap(void) {
    int top = g_maxvolume < 100 ? g_maxvolume : 100;
    char mac[24];
    if (output_mac(mac)) {
        int l = btlim_get(mac);
        if (l < top) top = l;
    }
    return top;
}

/* The volume down to the device's limit when above it, not saved (the top of this file). */
static void pull(int top) {
    if (g_volume <= top) return;
    if (!bl.pulled) bl.from = g_volume;
    bl.pulled = top + 1;
    g_volume = (unsigned char)top;
    device_set_volume(top, 1);
}

/* A volume save (ringnav_write_config): the level is the user's now, the pulled-from level gone. */
void btlim_saved(void) { bl.pulled = 0; }

/* The level PLAYSET BT_VOLUME keeps as the output leaves Bluetooth: the level a limit pulled the
 * volume down from while it has not moved since, else v. */
int btlim_departing(int v) {
    int keep = bl.pulled && v == bl.pulled - 1 ? bl.from : v;
    bl.pulled = 0;
    *bl.on = 0;
    return keep;
}

/* The output becoming Bluetooth with v its saved level (ringnav_output): v held to the device's
 * limit, remembering v as the level it came from. */
int btlim_entering(int v) {
    char mac[24];
    bl.pulled = 0;
    *bl.on = 0;
    int top = g_maxvolume < 100 ? g_maxvolume : 100;
    char item[32] = "";
    int status = 0, n = g_bluetoothflag ? getBtLinkSize() : 0;
    if (n > 0) getBtLinkItem(n - 1, &status, item);
    tk_snprintf(mac, sizeof mac, "%s", item);
    if (!bt_mac_ok(mac)) return v;
    tk_snprintf(bl.on, sizeof bl.on, "%s", mac);
    int l = btlim_get(mac);
    if (l >= top || v <= l) return v;
    bl.from = v, bl.pulled = l + 1;
    return l;
}

/* Each UI pass (bt_volume_poll), every BTLIM_POLL_MS: a device that has become the Bluetooth
 * output, at boot too, holds the volume to its limit; another device in its place starts from the
 * level the last one pulled down from. */
void btlim_poll(void) {
    unsigned now = time_now_ms();
    if (bl.at && now - bl.at < BTLIM_POLL_MS) return;
    bl.at = now ? now : 1;
    char mac[24] = "";
    if (!output_mac(mac)) *mac = 0;
    if (!strcmp(mac, bl.on)) return;
    if (*bl.on) { /* the last device's pull ends with it */
        if (bl.pulled && g_volume == bl.pulled - 1 && *mac) {
            g_volume = (unsigned char)bl.from; /* the next device's level is the saved one */
            device_set_volume(g_volume, 1);
        }
        bl.pulled = 0;
    }
    tk_snprintf(bl.on, sizeof bl.on, "%s", mac);
    if (*mac) pull(vol_cap());
}

/* mac's limit to v (BTLIM_NONE or more: none), in the cache; the volume down to it at once when mac
 * is the output now. A new device past BTLIM_MAX takes the oldest one's place. */
void btlim_set(const char *mac, int v) {
    if (!bt_mac_ok(mac)) return;
    int i = find(mac);
    v = v < 0 ? 0 : v;
    if (v >= BTLIM_NONE) {
        if (i < 0) return;
        memmove(bl.mac[i], bl.mac[i + 1], (unsigned)(bl.n - i - 1) * sizeof bl.mac[0]);
        memmove(&bl.lim[i], &bl.lim[i + 1], (unsigned)(bl.n - i - 1));
        --bl.n;
    } else {
        if (i < 0) {
            if (bl.n == BTLIM_MAX) { /* the oldest out */
                memmove(bl.mac[0], bl.mac[1], (unsigned)(bl.n - 1) * sizeof bl.mac[0]);
                memmove(&bl.lim[0], &bl.lim[1], (unsigned)(bl.n - 1));
                --bl.n;
            }
            i = bl.n++;
            tk_snprintf(bl.mac[i], sizeof bl.mac[i], "%s", mac);
        }
        bl.lim[i] = (unsigned char)v;
    }
    char on[24];
    if (output_mac(on) && !strncasecmp(on, mac, 17)) pull(vol_cap());
}

/* A device forgotten: its limit gone, from the file too. */
void btlim_forget(const char *mac) {
    if (find(mac) < 0) return;
    btlim_set(mac, BTLIM_NONE);
    store();
}

/* "No Limit" or the limit, into s. */
void btlim_text(int v, char *s, unsigned n) {
    if (v >= BTLIM_NONE)
        tk_snprintf(s, n, "No Limit");
    else
        tk_snprintf(s, n, "%d", v);
}

/* The slider page (crossfade.c xfade_open 5) sets mac's limit. */
void btlim_edit(const char *mac) { tk_snprintf(bl.edit, sizeof bl.edit, "%s", mac); }
int btlim_edit_get(void) { return btlim_get(bl.edit); }
void btlim_edit_take(int v) { btlim_set(bl.edit, v); }
void btlim_edit_save(void) { store(); }
/* The device page's Volume Limit value label (bt_detail.c), as the slider page moves it. */
void btlim_edit_row(void *label) {
    char s[16];
    btlim_text(btlim_edit_get(), s, sizeof s);
    widget_set_text_utf8(label, s);
}
