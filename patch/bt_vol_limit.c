/* A Bluetooth device's Volume Limit and Volume Curve
 * (docs/internals.md#headset-controls-and-volume): each paired device may cap the Q2's volume, 0 to
 * 100 on its scale, while it is the connected Bluetooth output; 100, No Limit, is the default and
 * is never stored. Its Volume Curve is how Link Volume maps the Q2's 0 to 100 onto the headset's 0
 * to 127 (playback_power.c bt_vol_avrcp): Even, the default and never stored, or Gentle, for a
 * headset that applies no taper of its own. Max Volume (g_maxvolume) still applies everywhere, so
 * over Bluetooth the volume's top is the lower of the two (vol_cap), which the wheel, Control
 * Center, Playback's Volume page, the Car Remote and the headset's own keys all stop at. Wired and
 * USB outputs never see a device's limit.
 *
 * Both are kept in BTLIM_FILE: one "AA:BB:CC:DD:EE:FF 60" line a device with a limit, and one
 * "AA:BB:CC:DD:EE:FF gentle" line a device with the Gentle curve, BTLIM_MAX devices at most,
 * written whole under another name and renamed, and read once into the cache here. The curve has a
 * line of its own so a build that knows only limits still reads every limit line and passes over
 * the curve lines. A line that does not read as either (a cut or corrupt file) is passed over, so a
 * broken or missing file reads as no limit and Even for every device. Forget This Device drops a
 * device's lines (bt_detail.c).
 *
 * The limit pulls the volume down without saving the lower level: a device's limit is not written
 * into PLAYSET BT_VOLUME, which every Bluetooth device shares. The level it was pulled from is kept
 * (bl.from) until the volume is next saved, and is what goes back to BT_VOLUME when the output
 * leaves Bluetooth (btlim_departing) or what the next device starts from (btlim_poll). */
#include "navigation.h"

#define BTLIM_FILE "/mnt/data/q2-btvollimit"
#define BTLIM_TMP BTLIM_FILE ".tmp"
#define BTLIM_MAX 16      /* devices with a limit or a curve; stock's paired list holds fewer */
#define BTLIM_LINE 26     /* the longest line: an address, " gentle" and its newline, and a spare */
#define BTLIM_POLL_MS 250 /* how often the connected device is looked at */
#define BTLIM_NONE 100
#define BTLIM_GENTLE "gentle"

static struct {
    int read, n;
    char mac[BTLIM_MAX][18];
    unsigned char lim[BTLIM_MAX];
    unsigned char curve[BTLIM_MAX]; /* BTLIM_EVEN or BTLIM_GENTLE_CURVE */
    char on[18];                    /* the Bluetooth output's device at the last look; "" none */
    char edit[18];                  /* the device the slider page or the picker sets (btlim_edit) */
    int from, pulled; /* the level a limit pulled the volume down from, and to plus 1; 0 none */
    unsigned at;
} bl __attribute__((section(".scratch")));

static int find(const char *mac);
static int slot(const char *mac);

/* The cache from BTLIM_FILE, once. */
static void load(void) {
    if (bl.read) return;
    bl.read = 1;
    bl.n = 0;
    char buf[2 * BTLIM_MAX * BTLIM_LINE + 1];
    void *f = fopen(BTLIM_FILE, "rb");
    if (!f) return;
    int len = (int)fread(buf, 1, sizeof buf - 1, f);
    fclose(f);
    buf[len < 0 ? 0 : len] = 0;
    for (char *p = buf; *p;) {
        char *end = p;
        while (*end && *end != '\n') ++end;
        int v = 0, digits = 0;
        char *q = p + 18;
        if (end - p > 18 && p[17] == ' ') {
            for (; q < end && *q >= '0' && *q <= '9' && digits < 3; ++q, ++digits)
                v = v * 10 + *q - '0';
            int gentle = !digits && end - q == sizeof BTLIM_GENTLE - 1 &&
                         !strncmp(q, BTLIM_GENTLE, sizeof BTLIM_GENTLE - 1);
            char save = p[17];
            p[17] = 0;
            int ok = (gentle || (digits && q == end && v < BTLIM_NONE)) && bt_mac_ok(p);
            /* a device past BTLIM_MAX in the file is passed over, never in an older one's place */
            int i = ok && (find(p) >= 0 || bl.n < BTLIM_MAX) ? slot(p) : -1;
            if (i >= 0 && gentle)
                bl.curve[i] = BTLIM_GENTLE_CURVE;
            else if (i >= 0)
                bl.lim[i] = (unsigned char)v;
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

/* mac's place in the cache, a new one with no limit and Even when it has none; a new device past
 * BTLIM_MAX takes the oldest one's place. */
static int slot(const char *mac) {
    int i = find(mac);
    if (i >= 0) return i;
    if (bl.n == BTLIM_MAX) { /* the oldest out */
        memmove(bl.mac[0], bl.mac[1], (unsigned)(bl.n - 1) * sizeof bl.mac[0]);
        memmove(&bl.lim[0], &bl.lim[1], (unsigned)(bl.n - 1));
        memmove(&bl.curve[0], &bl.curve[1], (unsigned)(bl.n - 1));
        --bl.n;
    }
    i = bl.n++;
    tk_snprintf(bl.mac[i], sizeof bl.mac[i], "%s", mac);
    bl.lim[i] = BTLIM_NONE, bl.curve[i] = BTLIM_EVEN;
    return i;
}

/* Entry i out of the cache once it holds neither a limit nor a curve. */
static void tidy(int i) {
    if (i < 0 || bl.lim[i] < BTLIM_NONE || bl.curve[i] != BTLIM_EVEN) return;
    memmove(bl.mac[i], bl.mac[i + 1], (unsigned)(bl.n - i - 1) * sizeof bl.mac[0]);
    memmove(&bl.lim[i], &bl.lim[i + 1], (unsigned)(bl.n - i - 1));
    memmove(&bl.curve[i], &bl.curve[i + 1], (unsigned)(bl.n - i - 1));
    --bl.n;
}

/* The cache to BTLIM_FILE, whole. */
static void store(void) {
    char buf[2 * BTLIM_MAX * BTLIM_LINE + 1];
    unsigned at = 0;
    for (int i = 0; i < bl.n; ++i) {
        if (bl.lim[i] < BTLIM_NONE)
            at += (unsigned)tk_snprintf(buf + at, sizeof buf - at, "%s %d\n", bl.mac[i], bl.lim[i]);
        if (bl.curve[i] != BTLIM_EVEN)
            at += (unsigned)tk_snprintf(buf + at, sizeof buf - at, "%s " BTLIM_GENTLE "\n",
                                        bl.mac[i]);
    }
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
 * is the output now. */
void btlim_set(const char *mac, int v) {
    if (!bt_mac_ok(mac)) return;
    v = v < 0 ? 0 : v;
    if (v >= BTLIM_NONE) {
        int i = find(mac);
        if (i < 0) return;
        bl.lim[i] = BTLIM_NONE;
        tidy(i);
    } else {
        bl.lim[slot(mac)] = (unsigned char)v;
    }
    char on[24];
    if (output_mac(on) && !strncasecmp(on, mac, 17)) pull(vol_cap());
}

/* mac's Volume Curve, BTLIM_EVEN when it has none. */
int btlim_curve(const char *mac) {
    int i = find(mac);
    return i < 0 ? BTLIM_EVEN : bl.curve[i];
}

/* The Volume Curve of the device the Bluetooth output goes to; Even with none. */
int vol_gentle(void) {
    char mac[24];
    return output_mac(mac) && btlim_curve(mac) == BTLIM_GENTLE_CURVE;
}

/* mac's Volume Curve to c (BTLIM_EVEN or BTLIM_GENTLE_CURVE), in the cache; when mac is the output
 * now, the volume is sent again (device_set_volume, so ringnav_btvol), and the headset takes the
 * level the new curve gives it at once. */
void btlim_curve_set(const char *mac, int c) {
    if (!bt_mac_ok(mac)) return;
    c = c == BTLIM_GENTLE_CURVE ? c : BTLIM_EVEN;
    if (c == btlim_curve(mac)) return;
    int i = slot(mac);
    bl.curve[i] = (unsigned char)c;
    tidy(i);
    char on[24];
    if (output_mac(on) && !strncasecmp(on, mac, 17)) device_set_volume(g_volume, 1);
}

/* A device forgotten: its limit and curve gone, from the file too. */
void btlim_forget(const char *mac) {
    int i = find(mac);
    if (i < 0) return;
    bl.curve[i] = BTLIM_EVEN;
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

/* The slider page (crossfade.c xfade_open 5) sets mac's limit, the picker its curve (below). */
void btlim_edit(const char *mac) { tk_snprintf(bl.edit, sizeof bl.edit, "%s", mac); }
int btlim_edit_get(void) { return btlim_get(bl.edit); }
void btlim_edit_take(int v) { btlim_set(bl.edit, v); }
void btlim_edit_save(void) { store(); }
/* The Volume Curve picker (library_settings.c, PICK_BT_CURVE) for the same device: its choice
 * now, and a pick, saved at once with the device page's Volume Curve value following
 * (BTLIM_CURVE_ROW). */
int btlim_edit_curve(void) { return btlim_curve(bl.edit); }
void btlim_edit_curve_take(int c) {
    btlim_curve_set(bl.edit, c);
    store();
    void *row = widget_lookup(window_manager(), BTLIM_CURVE_ROW, 1);
    if (row) widget_set_text_utf8(row, btlim_curve_name(btlim_curve(bl.edit)));
}
/* A curve's name, as its row and its picker show it. */
const char *btlim_curve_name(int c) { return c == BTLIM_GENTLE_CURVE ? "Gentle" : "Even"; }
/* The device page's Volume Limit value label (bt_detail.c), as the slider page moves it. */
void btlim_edit_row(void *label) {
    char s[16];
    btlim_text(btlim_edit_get(), s, sizeof s);
    widget_set_text_utf8(label, s);
}
