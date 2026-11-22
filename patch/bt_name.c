/* The Q2's Bluetooth name (docs/internals.md#bluetooth-name): what a phone or a car lists it as.
 *
 * config.ini's [Q2POD] BTNAME holds a name the user chose; with none the name is BT_NAME_DEFAULT,
 * which the build also writes as Name in /etc/bluetooth/main.conf (tools/build.py BT_NAME). Each
 * time demo turns Bluetooth on (openBluetooth, through headset.c's headset_status_register) and
 * after a rename, bt_name_apply sets BlueZ's adapter Alias over D-Bus to the chosen name, or to ""
 * with none, which BlueZ takes as "use main.conf's Name", then writes the same name into the
 * controller itself: so config.ini is the one record, and Q2 Muse's first-boot defaults (a
 * config.ini without BTNAME) give the Q2 its default name back even where BlueZ kept an old alias
 * in /mnt/data/bluetooth. The change is live: a phone scanning sees the new name at once, and one
 * already paired shows it the next time it reads the Q2's name.
 *
 * Settings > Bluetooth's Name row shows the name and opens the letter strip (search_strip_text),
 * the name typed in the row itself after its label and who sees it said under the row:
 * submitting saves, a held Return leaves it as it was. */
#include "navigation.h"

#define BT_NAME_DEFAULT "Q2 Muse"
#define BT_NAME_KEY "BTNAME"
#define BT_NAME_MAX                                                                                \
    30 /* characters; BlueZ takes 248 bytes, the row and the strip about this many */
/* dbus-send waits for bluetoothd: tries this often for this many times (20 s) */
#define BT_NAME_TRIES 40
#define BT_NAME_WAIT_US 500000

static struct {
    void *label; /* the Name row's title, while the Bluetooth page shows it */
} bn __attribute__((section(".scratch")));

/* The name into out (size n): the saved one, or the default. */
const char *bt_name(char *out, unsigned n) {
    char raw[BT_NAME_MAX * 2 + 2] = "";
    toolsReadConfig("/mnt/data/config.ini", "Q2POD", BT_NAME_KEY, raw, "");
    if (!bt_name_valid(raw)) tk_snprintf(raw, sizeof raw, "%s", BT_NAME_DEFAULT);
    tk_snprintf(out, n, "%s", raw);
    return out;
}

/* Whether s can be the name: 1 to BT_NAME_MAX printable ASCII characters, no quote or backslash
 * (the shell and dbus-send take it as it is), not starting or ending in a space. The strip types
 * nothing else; this guards a hand-edited config.ini. */
int bt_name_valid(const char *s) {
    unsigned n = s ? strlen(s) : 0;
    if (!n || n > BT_NAME_MAX || s[0] == ' ' || s[n - 1] == ' ') return 0;
    for (unsigned i = 0; i < n; ++i)
        if (s[i] < ' ' || s[i] > '~' || s[i] == '\'' || s[i] == '"' || s[i] == '\\' || s[i] == '`')
            return 0;
    return 1;
}

/* BlueZ's Alias to the saved name, or "" (main.conf's Name) with none, and then the controller's
 * own local name to the name itself (hciconfig's Write Local Name), in the background: tried until
 * bluetoothd and the controller answer, as demo starts them a moment before. The second write is
 * for the controller alone: at boot openBluetooth's `hciconfig hci0 reset` fails ("Can't init
 * device hci0: Function not implemented (89)") and leaves the RTL8733BS answering a name request
 * with its ROM's RT_BT while BlueZ holds the right name, and BlueZ writes nothing for an Alias of
 * "" with none stored or for one it already has (docs/internals.md#bluetooth-name). */
void bt_name_apply(void) {
    char raw[BT_NAME_MAX * 2 + 2] = "", name[BT_NAME_MAX + 1], cmd[640];
    toolsReadConfig("/mnt/data/config.ini", "Q2POD", BT_NAME_KEY, raw, "");
    if (!bt_name_valid(raw)) raw[0] = 0;
    tk_snprintf(name, sizeof name, "%s", *raw ? raw : BT_NAME_DEFAULT);
    tk_snprintf(cmd, sizeof cmd,
                "(i=0; while [ $i -lt %d ]; do dbus-send --system --print-reply --dest=org.bluez "
                "/org/bluez/hci0 org.freedesktop.DBus.Properties.Set string:org.bluez.Adapter1 "
                "string:Alias 'variant:string:%s' >/dev/null 2>&1 && hciconfig hci0 name '%s' "
                ">/dev/null 2>&1 && break; usleep %d; i=$((i+1)); done) >/dev/null 2>&1 &",
                BT_NAME_TRIES, raw, name, BT_NAME_WAIT_US);
    system(cmd);
}

/* The strip's Next: why the name can't be saved, or 0 once it is (and applied). */
static const char *bt_name_done(const char *text) {
    char name[BT_NAME_MAX + 1];
    unsigned a = 0, b = strlen(text);
    while (text[a] == ' ') ++a;
    while (b > a && text[b - 1] == ' ') --b;
    tk_snprintf(name, sizeof name, "%.*s", (int)(b - a), text + a);
    if (!*name) return "Name can't be empty";
    if (!bt_name_valid(name)) return "Can't use that name";
    /* the default saved as none, so a later default change reaches it */
    toolsWriteConfig("/mnt/data/config.ini", "Q2POD", BT_NAME_KEY,
                     tk_strcmp(name, BT_NAME_DEFAULT) ? name : "");
    bt_name_apply();
    if (bn.label) list_value(bn.label, "Name", name);
    toast("Renamed");
    return 0;
}

static int bt_name_open(void *ctx, void *event) {
    (void)ctx, (void)event;
    char name[BT_NAME_MAX + 1];
    search_strip_note("Phones and cars see this name");
    search_strip_label("Name");      /* typed in its row, after the label (search_strip.c) */
    search_strip_return(STRIP_SAVE); /* the return key's check */
    search_strip_text("Bluetooth", bt_name(name, sizeof name), BT_NAME_MAX, bt_name_done);
    return 0;
}
static int bt_name_gone(void *ctx, void *event) {
    (void)ctx, (void)event;
    bn.label = 0;
    return 0;
}

/* bt_ui.c bt_page: stock's second head row, its "Bluetooth Name" over what the radio reports (row
 * 1, which stock's click ignores), becomes Name with the saved name as its value, opening the
 * strip; the row keeps stock's place and height. With Bluetooth off stock builds only the switch,
 * and Name is made after it. */
void bt_name_row(void *view) {
    unsigned n = view ? widget_count_children(view) : 0;
    if (!n) return;
    void *old = n > 1 ? widget_get_child(view, 1) : (void *)0;
    int h = old ? I(old, W_H) : SET_STOCK_ROW;
    if (old) widget_destroy(old);
    char name[BT_NAME_MAX + 1];
    bn.label = list_row_into(view, 0, bt_name_open, 0);
    void *row = P(P(bn.label, W_PARENT), W_PARENT);
    widget_resize(row, I(row, W_W), h);
    widget_restack(row, 1);
    widget_on(P(bn.label, W_PARENT), EVT_DESTROY, bt_name_gone, 0);
    list_value(bn.label, "Name", bt_name(name, sizeof name));
}
