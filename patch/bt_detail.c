/* A Bluetooth device's page (docs/oled-design.md#bluetooth, docs/internals.md#bluetooth-page):
 * Centre on a paired device's row in Settings > Bluetooth opens it, connected or not, as Play/Pause
 * held does (bt_ui_hold). Under the status row the path the sound takes,
 * "Q2 → WH-1000XM5", and its codec line in 14 px Text Tertiary ("LDAC · 96 kHz · 990 kbps", what is
 * known of it, bt_codec_line, or "Not Connected"); then Battery (when the device reports one),
 * Disconnect (Connect, the first row, when it is not connected), Volume Limit (its value No Limit
 * or the limit, opening the slider page, crossfade.c xfade_open 5; bt_vol_limit.c keeps it), Volume
 * Curve (its value Even or Gentle, opening its picker, library_settings.c bt_curve_open;
 * bt_vol_limit.c keeps it too), Rename and Forget This Device in Negative red.
 *
 * Disconnect and Connect are stock's own row click on the device (bt_stock_click), so its output
 * switch and its toasts stay stock's. Forget asks on the confirmation card (feedback.c confirm:
 * destructive, Cancel first) and then does what stock's click does after its Unpair? dialog: the
 * pairing removed (handleBluetoothPair: BlueZ's RemoveDevice, which needs no link, so a device out
 * of range goes too), the output back to a jack when it was this device, the list read again; and
 * the saved link (BTLINKMAC) cleared when it names the device, so boot and Bluetooth on do not try
 * it again, and its Volume Limit and Volume Curve dropped; the page closes. Rename sets BlueZ's
 * Alias for the device, which its list shows, through the letter strip. */
#include "navigation.h"

#define BD_HEAD 64   /* the path's lines above the rows */
#define BD_PATH_Y 10 /* the path's line's box in that head */
#define BD_PATH_PX TYPE(17)
#define BD_CODEC_Y 36 /* ... and the codec line's */
#define BD_CODEC_PX TYPE(14)
#define BD_LINE_H 22
#define BD_NAME_MAX 30     /* a name, as the Q2's own (bt_name.c BT_NAME_MAX) */
#define BD_VALUE_W 120     /* Battery's value, right-aligned in this much */
#define BD_LIMIT_RIGHT 318 /* the two values end here, as Settings' values do */
#define BD_ROWS 6
/* Volume Limit's value, which the slider page sets (crossfade.c) */
#define BD_LIMIT_ROW "label_btlimit"
/* BTLINKMAC: stock's last connected device, [SYSSET] BTLINKMAC in config.ini, 32 bytes at a fixed
 * address (readBtLinkMac fills it, 0x4fa1f0); autoLinkBluetooth connects it at boot and Bluetooth
 * on */
#define BD_LINK_MAC ((char *)0xa4c0d8)
#define BD_LINK_MAC_N 32

enum { ROW_BATTERY, ROW_LINK, ROW_LIMIT, ROW_CURVE, ROW_RENAME, ROW_FORGET };

static struct {
    void *page, *view, *path, *codec;
    char mac[24], name[64];
    int rows[BD_ROWS], n;
    int shown; /* what fill showed: the link and the level (seen) */
} bd __attribute__((section(".scratch")));

/* The stream's sample rate as kHz text ("44.1", "96"), into s. */
static void khz(char *s, unsigned n, unsigned hz) {
    if (hz % 1000)
        tk_snprintf(s, n, "%u.%u", hz / 1000, hz % 1000 / 100);
    else
        tk_snprintf(s, n, "%u", hz / 1000);
}

/* The bit rate (kbps) the codec runs at, where it is fixed: LDAC's Best and Standard by Quality's
 * choice (g_btcoding 0 and 1; Auto steps with the signal and bluealsa does not say where it is),
 * aptX HD and aptX by the rate's family (44.1 kHz's or 48 kHz's); 0 for AAC and SBC, whose bit
 * rate the headset negotiates and bluealsa does not report. */
unsigned bt_kbps(const char *codec, unsigned hz) {
    int k441 = hz && hz % 8000;
    if (!codec || !hz) return 0;
    if (!tk_strcmp(codec, "LDAC"))
        return g_btcoding == 0 ? (k441 ? 909 : 990) : g_btcoding == 1 ? (k441 ? 606 : 660) : 0;
    if (!tk_strcmp(codec, "aptX HD")) return k441 ? 529 : 576;
    if (!tk_strcmp(codec, "aptX")) return k441 ? 352 : 384;
    return 0;
}

/* The connected device's codec line: "LDAC · 96 kHz · 990 kbps", what is known of it, into s; 0
 * with no stream. */
int bt_codec_line(char *s, unsigned n) {
    const char *codec = bt_codec();
    if (!codec) return 0;
    unsigned hz = bt_headset_rate(), kbps = bt_kbps(codec, hz),
             at = (unsigned)tk_snprintf(s, n, "%s", codec);
    if (hz && at < n) {
        char k[12];
        khz(k, sizeof k, hz);
        at += (unsigned)tk_snprintf(s + at, n - at, " · %s kHz", k);
    }
    if (kbps && at < n) tk_snprintf(s + at, n - at, " · %u kbps", kbps);
    return 1;
}

/* Whether the page's device is the one connected now, by its row on the Bluetooth page. */
static int connected(void) { return bt_dev_state(bt_dev_find(bd.mac)) == BT_ROW_CONNECTED; }

static int bd_click(void *ctx, void *event);

/* What the page shows of the link: connected or not, and the level. */
static int seen(void) { return connected() ? 1000 + bt_headset_battery() : -1000; }

static void fill(void) {
    if (!bd.page) return;
    int level = connected() ? bt_headset_battery() : -1;
    char s[96];
    bd.shown = seen();
    bd.n = 0;
    if (level >= 0) bd.rows[bd.n++] = ROW_BATTERY;
    bd.rows[bd.n++] = ROW_LINK;
    bd.rows[bd.n++] = ROW_LIMIT;
    bd.rows[bd.n++] = ROW_CURVE;
    bd.rows[bd.n++] = ROW_RENAME;
    bd.rows[bd.n++] = ROW_FORGET;
    /* where it was, or first Disconnect (Connect): Battery only says */
    int sel =
        bd.view ? widget_get_prop_int(bd.view, "_ringnav_index", 0) : bd.rows[0] == ROW_BATTERY;
    bd.view = page_list(bd.page, bd.page, 0, bd.name, bd.n, 48);
    void *list = P(bd.view, W_PARENT);
    int room = widget_get_prop_int(bd.page, "h", client_h()) - I(list, W_Y) - BD_HEAD,
        h = I(list, W_H) < room ? I(list, W_H) : room;
    widget_move_resize(list, 0, I(list, W_Y) + BD_HEAD, 375, h);
    widget_resize(bd.view, 375, h);
    int y = I(list, W_Y) - BD_HEAD, w = 375 - 2 * geom_val(V_TEXT_X);
    tk_snprintf(s, sizeof s, "Q2 → %s", bd.name);
    bd.path = label(bd.page, geom_val(V_TEXT_X), y + BD_PATH_Y, w, BD_LINE_H, "s_scrlabel_white20c",
                    BD_PATH_PX, 0xff000000u | tok(TOK_PRIMARY));
    widget_set_text_utf8(bd.path, s);
    bd.codec = label(bd.page, geom_val(V_TEXT_X), y + BD_CODEC_Y, w, BD_LINE_H,
                     "s_scrlabel_white20c", BD_CODEC_PX, 0xff000000u | tok(TOK_TERTIARY));
    if (!connected() || !bt_codec_line(s, sizeof s)) tk_snprintf(s, sizeof s, "Not Connected");
    widget_set_text_utf8(bd.codec, s);
    for (int i = 0; i < bd.n; ++i) {
        static const char *const names[] = { "Battery",      0,        "Volume Limit",
                                             "Volume Curve", "Rename", "Forget This Device" };
        const char *caption =
            bd.rows[i] == ROW_LINK ? (connected() ? "Disconnect" : "Connect") : names[bd.rows[i]];
        void *item = bd.rows[i] == ROW_RENAME || bd.rows[i] == ROW_LIMIT || bd.rows[i] == ROW_CURVE
                         ? page_row_detail_into(bd.view, i, caption, 0, bd_click)
                         : page_row_detail(bd.view, i, caption, 0, bd_click);
        if (bd.rows[i] ==
            ROW_FORGET) /* the row that loses something, as Settings' Reset Settings */
            text_ink(widget_get_child(item, 0), 0xff000000u | tok(TOK_NEGATIVE), INK_LABEL);
        if (bd.rows[i] == ROW_BATTERY) {
            int right = 375 - geom_val(V_RIGHT_X);
            void *v = label(item, right - BD_VALUE_W, 0, BD_VALUE_W, I(item, W_H),
                            "s_scrlabel_white20r", TYPE(20), 0xff000000u | tok(TOK_SECONDARY));
            tk_snprintf(s, sizeof s, "%d%%", level > 100 ? 100 : level);
            widget_set_text_utf8(v, s);
        }
        if (bd.rows[i] == ROW_LIMIT || bd.rows[i] == ROW_CURVE) { /* before row_into's chevron */
            void *v = label(item, BD_LIMIT_RIGHT - BD_VALUE_W, 0, BD_VALUE_W, I(item, W_H),
                            "s_scrlabel_white20r", TYPE(20), 0xff000000u | tok(TOK_SECONDARY));
            if (bd.rows[i] == ROW_LIMIT) {
                widget_set_name(v, BD_LIMIT_ROW);
                btlim_text(btlim_get(bd.mac), s, sizeof s);
            } else {
                widget_set_name(v, BTLIM_CURVE_ROW);
                tk_snprintf(s, sizeof s, "%s", btlim_curve_name(btlim_curve(bd.mac)));
            }
            widget_set_text_utf8(v, s);
        }
    }
    ringnav_select(bd.view, sel < bd.n ? sel : bd.n - 1, bd.n);
}

static int bd_closed(void *ctx, void *event) {
    (void)event;
    if (ctx == bd.page) bd.page = bd.view = bd.path = bd.codec = (void *)0;
    return 0;
}

/* Device dev's page. */
void bt_detail_open(int dev) {
    const char *mac = bt_dev_mac(dev), *name = bt_dev_name(dev);
    if (bd.page || !mac || !name || !(bd.page = page_open("btdevice_page", bd_closed, page_keyup)))
        return;
    tk_snprintf(bd.mac, sizeof bd.mac, "%s", mac);
    tk_snprintf(bd.name, sizeof bd.name, "%s", name);
    bd.view = 0;
    fill();
}

/* The page's hidden bar title, its device's name; 0 for another page. */
const char *bt_detail_title(const char *name) {
    return bd.page && !tk_strcmp(name, "btdevice_page") ? bd.name : (const char *)0;
}

/* The saved link, cleared when it is mac: in memory, which autoLinkBluetooth reads at Bluetooth on,
 * and in config.ini, which readBtLinkMac reads at boot. Stock's Unpair leaves it. */
static void forget_link(const char *mac) {
    if (strncasecmp(BD_LINK_MAC, mac, BD_LINK_MAC_N) || !*mac) return;
    memset(BD_LINK_MAC, 0, BD_LINK_MAC_N);
    toolsWriteConfig("/mnt/data/config.ini", "SYSSET", "BTLINKMAC", "");
}

/* The output back to a jack, as stock's row click does after a device that was the output is
 * unpaired or disconnected (btmenu_item_on_click's check_outputdevice and config_outputchannel).
 * Shared with q2ui's connect (bt_ui.c bt_q2ui_link). */
void bt_output_back(void) {
    int out = check_outputdevice((signed char)g_headset_output, 2);
    if ((signed char)g_headset_output != out) {
        g_headset_output = (unsigned char)out;
        if (mclGetSavePlayStatus() != 1) config_outputchannel((signed char)g_headset_output, 3);
    }
}

/* Device mac forgotten: stock's row click's own steps after its Unpair? dialog's OK
 * (btmenu_item_on_click, 0x4c44b8 to 0x4c45b4), then the saved link. 1 when it was. Shared with
 * q2ui's Bluetooth page (bt_ui.c bt_q2ui_forget). */
int bt_forget(const char *mac) {
    char b[96];
    int dev = bt_dev_find(mac), r = 0;
    /* each answer a bt line (docs/internals.md#bluetooth-connection-trace): on 2026-11-19 a Forget
     * took two taps and nothing said why */
    if (dev < 0)
        tk_snprintf(b, sizeof b, "forget %s: not in the device list now, nothing done", mac);
    else if ((r = handleBluetoothPair(dev)) != 1 && r != 2)
        tk_snprintf(b, sizeof b, "forget %s: stock's unpair refused it (%d), nothing done", mac, r);
    else
        tk_snprintf(b, sizeof b, "forget %s: %s", mac, r == 2 ? "disconnected and unpaired" : "unpaired");
    ulog(r != 1 && r != 2 ? 'W' : 'I', "bt", b);
    if (r != 1 && r != 2) return 0;
    if (r == 2) bt_output_back(); /* it was the output: back to a jack */
    forget_link(mac);
    btlim_forget(mac);
    toast("Device Forgotten");
    getSearchResult();
    player_save_start(1);
    g_btsetting_flag = 1; /* the Bluetooth page's timer rebuilds its list */
    return 1;
}

/* The forget, once the card's Forget Device is chosen; the page closes. */
static void forget(void *ctx, int row, int toggle_on) {
    (void)ctx, (void)toggle_on;
    if (row != 1) {
        char b[96];
        tk_snprintf(b, sizeof b, "forget %s: Cancel chosen", bd.mac);
        ulog('I', "bt", b);
        return;
    }
    if (bt_forget(bd.mac) && bd.page) window_close(bd.page);
}

/* Forget This Device's question (the confirmation card, docs/oled-design.md#confirmation-card). */
static void forget_ask(void) {
    static const char *const rows[] = { "Cancel", "Forget Device" };
    confirm(&(confirm_t){ "Forget %s?", bd.name, "You'll need to pair again to use it.", 0, rows, 2,
                          CONFIRM_DESTRUCTIVE, -1, 0, 0, 0, 0, forget, 0, 0, 0, 0 });
}

/* Rename's Next: why the name can't be used, or 0 once it is set. */
static const char *renamed(const char *text) {
    char name[BD_NAME_MAX + 1], cmd[512], dev[24];
    unsigned a = 0, b = strlen(text);
    while (text[a] == ' ') ++a;
    while (b > a && text[b - 1] == ' ') --b;
    tk_snprintf(name, sizeof name, "%.*s", (int)(b - a), text + a);
    if (!*name) return "Name can't be empty";
    if (!bt_name_valid(name)) return "Can't use that name";
    if (!bt_mac_ok(bd.mac)) return "Can't rename it now";
    tk_snprintf(dev, sizeof dev, "dev_%s", bd.mac);
    for (char *p = dev; *p; ++p)
        if (*p == ':') *p = '_';
    tk_snprintf(cmd, sizeof cmd,
                "dbus-send --system --print-reply --dest=org.bluez /org/bluez/hci0/%s "
                "org.freedesktop.DBus.Properties.Set string:org.bluez.Device1 string:Alias "
                "'variant:string:%s' >/dev/null 2>&1 &",
                dev, name);
    system(cmd);
    bt_dev_rename(bt_dev_find(bd.mac), name);
    tk_snprintf(bd.name, sizeof bd.name, "%s", name);
    fill();
    toast("Renamed");
    return 0;
}

static int bd_click(void *ctx, void *event) {
    (void)event;
    int i = (int)(long)ctx, dev = bt_dev_find(bd.mac);
    switch (i >= 0 && i < bd.n ? bd.rows[i] : -1) {
    case ROW_LINK:
        if (dev >= 0) bt_stock_click(dev, 0);
        fill();
        break;
    case ROW_LIMIT:
        btlim_edit(bd.mac);
        xfade_open((void *)5, 0);
        break;
    case ROW_CURVE:
        btlim_edit(bd.mac);
        bt_curve_open();
        break;
    case ROW_RENAME:
        search_strip_note("Shows on this Q2");
        search_strip_label("Rename");    /* typed in its row, after the label (search_strip.c) */
        search_strip_return(STRIP_SAVE); /* the return key's check */
        search_strip_text("Rename", bd.name, BD_NAME_MAX, renamed);
        break;
    case ROW_FORGET:
        forget_ask();
        break;
    }
    return 0;
}

/* While the page is open: its rows again when the link or the battery changed (bt_ui_poll). */
void bt_detail_poll(void) {
    if (bd.page && seen() != bd.shown) fill();
}
