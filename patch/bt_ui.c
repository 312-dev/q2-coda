/* Settings > Bluetooth (docs/oled-design.md#bluetooth, docs/internals.md#bluetooth-page): devices
 * before settings. Over the list stock's btmenu_rows builds (and rebuilds on every scan result and
 * link change, ringnav_btrows), the page is the switch, Name, then My Devices (the paired ones),
 * Other Devices (the found ones) and an Audio group: Quality, Headset Volume, Resume on Connect and
 * Link Volume. Stock's Scan now row goes: the page scans by itself while it is open (bt_ui_poll),
 * a spinner beside Other Devices while it runs. With Bluetooth off, the switch, Name and a line
 * saying what turning it on gives.
 *
 * A device row keeps stock's button, its name over its index in pdeq_btshowlist, and stock's state
 * label (msg_connected, msg_connecting, msg_paired, which tools/build.py LABELS words "Connected",
 * "Connecting…" and "Not Connected"); laid out here after stock's layout (bt_rows_pre, bt_rows_post
 * from navigation.c ipod_list_layout), each row leads with its kind's glyph (headphones, car,
 * speaker, phone or the Bluetooth rune, from the device's BlueZ Class, bt_class). The connected one
 * is a two-line row: its name over "Connected · LDAC · 72%" (stock's codec, bt_showcoding, and the
 * headset's battery, bt_battery.c) after the Positive dot; another paired one is a key/value row,
 * "Not Connected", or "Connecting…" with the activity spinner on the right column. Centre on a
 * paired row connects it, on the connected one opens its page (bt_detail.c); a found one pairs, as
 * stock.
 */
#include "navigation.h"

#define BT_HEAD_H 32 /* a section header's item: a whole row, so the list scrolls in whole rows */
#define BT_HEAD_Y 8  /* ... its label's box in it, the text on the row's lower part */
#define BT_HEAD_PX TYPE(15) /* the section header's size (oled-design.md Type) */
#define BT_TWO_H 52         /* the connected device's two-line row (oled-design.md Spacing) */
#define BT_TITLE_Y 4        /* ... its title's box, the baseline 25 px down */
#define BT_TITLE_H 28
#define BT_LINE_Y 30 /* ... its second line's box, the text's top 32 px down */
#define BT_LINE_H 20
#define BT_LINE_PX TYPE(15)   /* Row second line */
#define BT_GLYPH_CX 33        /* a row's kind glyph centred here (Storage > Downloads' column) */
#define BT_TEXT_X 52          /* ... the name from here */
#define BT_ROW_GLYPH 20       /* the glyph's box (tools/art.py ROW_ICON_PX) */
#define BT_DOT 6              /* the Positive dot before "Connected", centred on the second line */
#define BT_DOT_GAP 4          /* ... and the room after it */
#define BT_SPIN 16            /* the spinner's box (list_marks.c SPIN_BOX) */
#define BT_SPIN_GAP 8         /* between "Other Devices" and its spinner */
#define BT_SPIN_MS 100        /* a spoke a frame */
#define BT_POLL_MS 500        /* the scan's and the class file's checks, the link's */
#define BT_CLASS_WAIT_MS 3000 /* a connect toast waits this long at most for the device's Class */
#define BT_STATE_W 170        /* a paired row's state, right-aligned in this much */
#define BT_NOTE_H 52          /* the switch-off line, two lines of 13 px */
#define BT_NOTE_PX TYPE(13)   /* the caption's size */
#define BT_ROWS_MAX 48
#define BT_CLASS_FILE "/tmp/q2-btclass"
#define BT_CLASS_MAX 12 /* devices whose Class one fetch asks for */
#define BT_MARK "_btmark"

/* What a row's state label says, from stock's tr_text (navigation.h BT_ROW_*). */
enum { BT_FOUND = BT_ROW_FOUND, BT_PAIRED, BT_CONNECTING, BT_CONNECTED };
/* A mark image's job (its BT_MARK prop). */
enum { MARK_NONE, MARK_GLYPH, MARK_DOT, MARK_SPIN, MARK_CHEVRON, MARK_HEAD_SPIN };

static struct {
    void *view;      /* the Bluetooth page's list while it is open */
    void *head_spin; /* Other Devices' spinner mark */
    void *line;      /* the connected row's second line */
    unsigned poll_at, spin_at, class_hash, class_seen;
    int frame, scans; /* the spinner's frame; scans started for this view */
    char link[24];    /* the device connected at the last look, "" none */
    unsigned link_at, link_cls, link_poll_at;
    int link_pending,
        low; /* its connect toast not yet shown; the battery steps toasted (1 20%, 2 10%) */
    char classes[BT_CLASS_MAX * 32 + 2];
} bt __attribute__((section(".scratch")));

/* ---- the devices ---- */

/* Device i of pdeq_btshowlist: its MAC (@4, 32 bytes) and name (@0x24); 0 past the list. */
static const char *dev_at(int i, int name) {
    void *list = P(pdeq_btshowlist, 0);
    if (i < 0 || !list || (unsigned)i >= deque_size(list)) return (const char *)0;
    return (const char *)deque_at(list, (unsigned)i) + (name ? 0x24 : 4);
}
const char *bt_dev_mac(int i) { return dev_at(i, 0); }
const char *bt_dev_name(int i) { return dev_at(i, 1); }
/* The index in pdeq_btshowlist of mac, -1 for none. */
int bt_dev_find(const char *mac) {
    const char *m;
    for (int i = 0; mac && (m = dev_at(i, 0)); ++i)
        if (!strcmp(m, mac)) return i;
    return -1;
}

/* Device i's name changed in place (a rename, bt_detail.c), as the list shows it till its next
 * read. */
void bt_dev_rename(int i, const char *name) {
    char *at = (char *)dev_at(i, 1);
    if (at) tk_snprintf(at, 0x100, "%s", name);
}

/* Where device i is in stock's link list: BT_ROW_CONNECTED as the last item at status 3 (stock's
 * own test on its rows), BT_ROW_CONNECTING while a link thread runs for it, else BT_ROW_PAIRED. */
int bt_dev_state(int i) {
    const char *mac = dev_at(i, 0);
    char item[32] = "";
    int status = 0, n = getBtLinkSize();
    if (!mac || n <= 0) return BT_ROW_PAIRED;
    getBtLinkItem(n - 1, &status, item);
    if (strcmp(item, mac)) return BT_ROW_PAIRED;
    return status == 3                   ? BT_ROW_CONNECTED
           : I(bt_connectstatus, 0) == 1 ? BT_ROW_CONNECTING
                                         : BT_ROW_PAIRED;
}

/* A row's stock button (its first child, named by its device's index) and that index, -1 for a
 * row that is no device's. */
static void *row_button(void *item) {
    void *b = item && widget_count_children(item) ? widget_get_child(item, 0) : (void *)0;
    return b && !tk_strcmp(widget_get_type(b), "button") ? b : (void *)0;
}
static int row_dev(void *item) { return widget_get_prop_int(item, "_bt_dev", 0) - 1; }

/* The state stock's label in b says. */
static void *state_label(void *b) {
    for (unsigned i = 0, n = b ? widget_count_children(b) : 0; i < n; ++i) {
        const char *tr = widget_get_prop_str(widget_get_child(b, i), "tr_text", "");
        if (tr && (!tk_strcmp(tr, "msg_connected") || !tk_strcmp(tr, "msg_connecting") ||
                   !tk_strcmp(tr, "msg_paired")))
            return widget_get_child(b, i);
    }
    return (void *)0;
}
static int row_state(void *b) {
    void *l = state_label(b);
    const char *tr = l ? widget_get_prop_str(l, "tr_text", "") : "";
    return !l                                 ? BT_FOUND
           : !tk_strcmp(tr, "msg_connected")  ? BT_CONNECTED
           : !tk_strcmp(tr, "msg_connecting") ? BT_CONNECTING
                                              : BT_PAIRED;
}

/* The codec stock streams with (bt_showcoding, as its status bar's badge reads it), 0 for none. */
const char *bt_codec(void) {
    switch (I(bt_showcoding, 0)) {
    case 4:
        return "LDAC";
    case 13:
        return "aptX HD";
    case 3:
        return "aptX";
    case 2:
        return "AAC";
    case 1:
        return "SBC";
    }
    return (const char *)0;
}

/* ---- kinds, from BlueZ's Class ---- */

/* A device's Class of Device, as the background fetch (class_fetch) wrote it: 0 unknown. */
unsigned bt_class(const char *mac) {
    unsigned n = mac ? strlen(mac) : 0;
    for (const char *p = bt.classes; n && (p = strstr(p, mac)); p += n)
        if ((p == bt.classes || p[-1] == '\n') && p[n] == ' ') {
            unsigned v = 0;
            for (p += n + 1; *p >= '0' && *p <= '9'; ++p) v = v * 10 + (unsigned)(*p - '0');
            return v;
        }
    return 0;
}
/* The kind's glyph stem (tools/art.py BT_ROW_GLYPHS): the Class's major Audio/Video (4) by its
 * minor (Bluetooth's assigned numbers: 1 and 2 headset, 6 headphones, 8 car audio, 5, 7 and 10
 * speakers), major Phone (2), else the rune. */
const char *bt_kind(unsigned cls) {
    unsigned major = cls >> 8 & 0x1f, minor = cls >> 2 & 0x3f;
    if (major == 2) return "phone";
    if (major != 4) return "bt";
    return minor == 1 || minor == 2 || minor == 6    ? "headphones"
           : minor == 8                              ? "car"
           : minor == 5 || minor == 7 || minor == 10 ? "speaker"
                                                     : "bt";
}

/* Whether s is a MAC as stock lists it, AA:BB:CC:DD:EE:FF: it goes into a shell command. */
int bt_mac_ok(const char *s) {
    for (int i = 0; i < 17; ++i) {
        char c = s ? s[i] : 0;
        if (i % 3 == 2
                ? c != ':'
                : !((c >= '0' && c <= '9') || (c >= 'A' && c <= 'F') || (c >= 'a' && c <= 'f')))
            return 0;
    }
    return !s[17];
}

/* BlueZ's Class for each listed device, in the background, written whole to BT_CLASS_FILE as
 * "MAC CLASS" lines: started when the list holds a device the file has not answered for. */
static void class_fetch(void) {
    char cmd[BT_CLASS_MAX * 220 + 128];
    unsigned at = 0, hash = 5381, k = 0;
    const char *m;
    for (int i = 0; (m = dev_at(i, 0)) && k < BT_CLASS_MAX; ++i)
        if (bt_mac_ok(m) && !bt_class(m) && !strstr(bt.classes, m)) {
            for (const char *p = m; *p; ++p) hash = hash * 33 + (unsigned char)*p;
            ++k;
        }
    if (!k || hash == bt.class_hash) return; /* nothing new, or asked already */
    bt.class_hash = hash;
    at = (unsigned)tk_snprintf(cmd, sizeof cmd, "(f=" BT_CLASS_FILE "; { cat $f 2>/dev/null; ");
    k = 0;
    for (int i = 0; (m = dev_at(i, 0)) && k < BT_CLASS_MAX && at < sizeof cmd - 260; ++i) {
        if (!bt_mac_ok(m) || strstr(bt.classes, m)) continue;
        char path[24];
        tk_snprintf(path, sizeof path, "%s", m);
        for (char *p = path; *p; ++p)
            if (*p == ':') *p = '_';
        at += (unsigned)tk_snprintf(
            cmd + at, sizeof cmd - at,
            "c=$(dbus-send --system --print-reply --dest=org.bluez /org/bluez/hci0/dev_%s "
            "org.freedesktop.DBus.Properties.Get string:org.bluez.Device1 string:Class 2>/dev/null"
            " | sed -n 's/.*uint32 //p'); [ -n \"$c\" ] && echo \"%s $c\"; ",
            path, m);
        ++k;
    }
    tk_snprintf(cmd + at, sizeof cmd - at, "} > $f.new; mv -f $f.new $f) >/dev/null 2>&1 &");
    system(cmd);
}

/* BT_CLASS_FILE into bt.classes when it changed; 1 then. */
static int class_read(void) {
    char raw[sizeof bt.classes + 1];
    unsigned n = state_read(BT_CLASS_FILE, raw, sizeof raw), hash = 5381;
    for (unsigned i = 1; i <= n; ++i) hash = hash * 33 + (unsigned char)raw[i];
    if (hash == bt.class_seen) return 0;
    bt.class_seen = hash;
    tk_snprintf(bt.classes, sizeof bt.classes, "%s", raw + 1);
    return 1;
}

/* ---- the page's rows ---- */

static int view_gone(void *ctx, void *event) {
    (void)event;
    if (bt.view == ctx) bt.view = bt.head_spin = bt.line = (void *)0, bt.scans = 0;
    return 0;
}
static int head_spin_gone(void *ctx, void *event) {
    (void)event;
    if (bt.head_spin == ctx) bt.head_spin = (void *)0;
    return 0;
}
static int line_gone(void *ctx, void *event) {
    (void)event;
    if (bt.line == ctx) bt.line = (void *)0;
    return 0;
}

/* A label in the section header's type (15 px Text Tertiary) on the text column. */
static void *small_label(void *parent, int x, int y, int w, int h, int px) {
    void *l = label_create(parent, x, y, w, h);
    face_font(l, theme()->font[ROLE_META], px, FONT_ROW);
    widget_set_prop_str(l, "style:normal:text_align_h", "left");
    widget_set_prop_str(l, "style:disable:text_align_h", "left");
    text_ink(l, 0xff000000u | tok(TOK_TERTIARY), 4);
    return l;
}

/* A mark image in parent, drawn by bt_ui_paint. */
static void *mark(void *parent, const char *name, int kind, int x, int y, int w, int h) {
    void *m = widget_lookup(parent, name, 0);
    if (!m) {
        m = image_create(parent, x, y, w, h);
        widget_set_name(m, name);
        widget_set_prop_int(m, BT_MARK, kind);
    } else
        widget_move_resize(m, x, y, w, h);
    widget_set_visible(m, 1, 0);
    return m;
}

/* A section header: item h high, a heading the wheel passes, its label text in the header's type
 * (stock's own centred label, if any, hidden). */
static void *head(void *view, void *item, const char *text) {
    if (!item) {
        item = list_item_create(view, 0, 0, 375, BT_HEAD_H);
        widget_use_style(item, "s_listitem_black");
    }
    for (unsigned i = 0, n = widget_count_children(item); i < n; ++i)
        widget_set_visible(widget_get_child(item, i), 0, 0);
    widget_resize(item, I(item, W_W), BT_HEAD_H);
    widget_set_enable(item, 0);
    widget_set_prop_int(item, "_bt_head", 1);
    void *l =
        small_label(item, geom_val(V_TEXT_X), BT_HEAD_Y, 240, BT_HEAD_H - BT_HEAD_Y, BT_HEAD_PX);
    widget_set_name(l, "_bt_head");
    widget_set_text_utf8(l, text);
    return item;
}

/* Stock's click on device dev's row on the Bluetooth page, as a tap at x: connect a paired or
 * found one, disconnect the connected one (x under 276), or stock's Unpair? (x 276 on, a paired
 * one's right end). 0 with no such row. */
int bt_stock_click(int dev, int x) {
    for (unsigned i = 0, n = bt.view ? widget_count_children(bt.view) : 0; i < n; ++i) {
        void *item = widget_get_child(bt.view, i), *b = row_button(item);
        if (b && row_dev(item) == dev) {
            int event[16] = { 0 }; /* pointer_event_t: x at +0x18, the one field stock reads */
            event[6] = x;
            btmenu_item_on_click(b, event);
            return 1;
        }
    }
    return 0;
}

/* A device row's Centre or tap (docs/oled-design.md#bluetooth): the connected device opens its
 * page, any other connects (or pairs), as stock's click. */
static int row_click(void *b, void *event) {
    (void)event;
    void *item = P(b, W_PARENT);
    int dev = item ? row_dev(item) : -1;
    if (dev < 0) return 0;
    if (row_state(b) == BT_CONNECTED)
        bt_detail_open(dev);
    else
        bt_stock_click(dev, 0);
    return STOP;
}

/* ringnav_keylong: Play/Pause held on a paired device's row opens its page; 1 when it was one. */
int bt_ui_hold(void *top) {
    if (!bt.view || top != window_of(bt.view)) return 0;
    int at = widget_get_prop_int(bt.view, "_ringnav_index", -1);
    void *item = at >= 0 ? widget_get_child(bt.view, (unsigned)at) : (void *)0,
         *b = item ? row_button(item) : (void *)0;
    if (!b || row_dev(item) < 0 || row_state(b) == BT_FOUND) return 0;
    bt_detail_open(row_dev(item));
    return 1;
}

/* Bluetooth off: what turning it on gives, under the Name row. */
static void off_note(void *view) {
    void *item = list_item_create(view, 0, 0, 375, BT_NOTE_H);
    widget_use_style(item, "s_listitem_black");
    widget_set_enable(item, 0);
    widget_set_prop_int(item, "_bt_head", 1);
    void *l = small_label(item, geom_val(V_TEXT_X), 6, 375 - 2 * geom_val(V_TEXT_X), BT_NOTE_H - 6,
                          BT_NOTE_PX);
    widget_set_prop_int(l, "line_wrap", 1);
    widget_set_prop_int(l, "word_wrap", 1);
    widget_set_prop_str(l, "style:normal:text_align_v", "top");
    widget_set_text_utf8(l, "Turn on to play to headphones, a car or a speaker.");
}

/* Stock's Quality row, its title over the choice (a second line), as a key/value row: Quality and
 * the choice's name (LDAC Auto) on the right column before its chevron. */
static void quality_value(void *item) {
    void *b = row_button(item), *title = (void *)0, *choice = (void *)0;
    for (unsigned i = 0, n = b ? widget_count_children(b) : 0; i < n; ++i) {
        void *c = widget_get_child(b, i);
        if (tk_strcmp(widget_get_type(c), "hscroll_label")) continue;
        if (!title)
            title = c;
        else if (!choice)
            choice = c;
    }
    if (!title || !choice) return;
    char s[64] = "";
    widget_get_text_utf8(choice, s, sizeof s);
    widget_destroy(choice);
    list_value(title, "Quality", s);
}

/* ringnav_btrows, after stock's builder and pod_rows' two (Resume on Connect, Link Volume, last):
 * the page's order. Stock's rows by place: 0 the switch, 1 Bluetooth Name (bt_name.c's Name row),
 * 2 Quality, 3 Headset Volume, 4 Scan now; then My Devices' header and rows, Other Devices' header
 * and rows (stock's headers only where it lists such a device). Any other count is left as built.
 */
void bt_page(void *view, int on) {
    bt_name_row(view);
    unsigned n = widget_count_children(view);
    if (bt.view != view) {
        bt.view = view, bt.scans = 0;
        widget_on(view, EVT_DESTROY, view_gone, view);
    }
    if (!on) {
        off_note(view);
        return;
    }
    if (n < 7 || n - 2 > BT_ROWS_MAX - 8) return;
    void *rows[BT_ROWS_MAX], *quality = widget_get_child(view, 2),
                             *volume = widget_get_child(view, 3), *scan = widget_get_child(view, 4);
    unsigned k = 0, other = 0;
    rows[k++] = widget_get_child(view, 0);
    rows[k++] = widget_get_child(view, 1);
    for (unsigned i = 5; i < n - 2; ++i) {
        void *item = widget_get_child(view, i), *b = row_button(item);
        if (b) {
            widget_set_prop_int(item, "_bt_dev", atoi(widget_get_prop_str(b, "name", "")) + 1);
            widget_off_by_func(b, EVT_CLICK, (void *)btmenu_item_on_click, b);
            widget_on(b, EVT_CLICK, row_click, b);
        } else { /* stock's header (btmenu_msginfo_layout): paired first, then found */
            other = k > 2;
            for (unsigned j = 2; j < k; ++j)
                if (!widget_get_prop_int(rows[j], "_bt_head", 0)) other = 1;
            head(view, item, other ? "Other Devices" : "My Devices");
            if (other) bt.head_spin = 0, other = k + 1;
        }
        rows[k++] = item;
    }
    if (!other) rows[k++] = head(view, (void *)0, "Other Devices");
    /* Other Devices' spinner: beside its label (bt_ui_paint measures it) */
    void *oh = rows[other ? other - 1 : k - 1];
    bt.head_spin = mark(oh, "_bt_headspin", MARK_HEAD_SPIN, 0, 0, 375, BT_HEAD_H);
    widget_on(bt.head_spin, EVT_DESTROY, head_spin_gone, bt.head_spin);
    rows[k++] = head(view, (void *)0, "Audio");
    quality_value(quality);
    rows[k++] = quality;
    rows[k++] = volume;
    rows[k++] = widget_get_child(view, n - 2);
    rows[k++] = widget_get_child(view, n - 1);
    for (unsigned j = k - 2; j < k; ++j) widget_resize(rows[j], I(rows[j], W_W), SET_STOCK_ROW);
    for (unsigned i = 0; i < k; ++i) widget_restack(rows[i], i);
    widget_destroy(scan);
    class_read();
    class_fetch();
}

/* Before stock's layout: each device row's height, the connected one two lines (navigation.c maps
 * the others to the set's row as stock's settings rows). */
void bt_rows_pre(void *view) {
    for (unsigned i = 0, n = widget_count_children(view); i < n; ++i) {
        void *item = widget_get_child(view, i), *b = row_button(item);
        if (widget_get_prop_int(item, "_bt_head", 0) && I(item, W_H) != BT_NOTE_H)
            I(item, W_H) = BT_HEAD_H;
        else if (row_dev(item) >= 0 && b && I(item, W_H) == SET_STOCK_ROW)
            I(item, W_H) = row_state(b) == BT_CONNECTED ? BT_TWO_H : geom_val(V_SET_ROW);
    }
}

/* The connected row's second line: "Connected · LDAC · 72%", what is known of it. */
static void connected_line(char *s, unsigned n) {
    const char *codec = bt_codec();
    int level = bt_headset_battery();
    unsigned at = (unsigned)tk_snprintf(s, n, "Connected");
    if (codec) at += (unsigned)tk_snprintf(s + at, n - at, " · %s", codec);
    if (level >= 0 && at < n) tk_snprintf(s + at, n - at, " · %d%%", level > 100 ? 100 : level);
}

/* After the layout: a device row's children on the design's columns. */
static void dress(void *item, void *b) {
    int h = I(item, W_H), right = I(b, W_W) - geom_val(V_RIGHT_X), state = row_state(b);
    void *title = (void *)0, *line = state_label(b);
    for (unsigned i = 0, n = widget_count_children(b); i < n; ++i) {
        void *c = widget_get_child(b, i);
        const char *type = widget_get_type(c);
        if (!tk_strcmp(type, "hscroll_label") && c != line && !title)
            title = c;
        else if (!tk_strcmp(type, "image") && !widget_get_prop_int(c, BT_MARK, 0))
            widget_set_visible(c, 0,
                               0); /* stock's icon, and a paired row's Unpair (navbar_delete) */
    }
    mark(b, "_bt_glyph", MARK_GLYPH, BT_GLYPH_CX - BT_ROW_GLYPH / 2, (h - BT_ROW_GLYPH) / 2,
         BT_ROW_GLYPH, BT_ROW_GLYPH);
    int end = right - (state == BT_FOUND ? 0 : VALUE_CHEVRON_GAP);
    if (state == BT_PAIRED || state == BT_CONNECTED)
        mark(b, "_bt_chevron", MARK_CHEVRON, right - BT_SPIN, (h - BT_SPIN) / 2, BT_SPIN, BT_SPIN);
    if (state == BT_CONNECTING)
        mark(b, "_bt_spin", MARK_SPIN, right - BT_SPIN, (h - BT_SPIN) / 2, BT_SPIN, BT_SPIN);
    if (state == BT_CONNECTED && title && line) {
        char s[64];
        widget_move_resize(title, BT_TEXT_X - 2, BT_TITLE_Y, end - BT_TEXT_X, BT_TITLE_H);
        int x = BT_TEXT_X + BT_DOT + BT_DOT_GAP;
        mark(b, "_bt_dot", MARK_DOT, BT_TEXT_X, BT_LINE_Y, BT_DOT, BT_LINE_H);
        widget_move_resize(line, x - 2, BT_LINE_Y, end - x, BT_LINE_H);
        face_font(line, theme()->font[ROLE_META], BT_LINE_PX, FONT_ROW);
        text_ink(line, 0xff000000u | tok(TOK_TERTIARY), INK_LABEL);
        connected_line(s, sizeof s);
        widget_set_text_utf8(line, s);
        if (bt.line != line) {
            bt.line = line;
            widget_on(line, EVT_DESTROY, line_gone, line);
        }
        return;
    }
    if (title) widget_move_resize(title, BT_TEXT_X - 2, 0, end - BT_TEXT_X, h);
    if (line) { /* the state as a value, ending before the chevron or the spinner */
        widget_move_resize(line, end - BT_STATE_W, 0, BT_STATE_W, h);
        theme_font(line, ROLE_TITLE, FONT_ROW);
        widget_set_prop_str(line, "style:normal:text_align_h", "right");
        unsigned ink = theme()->set_value_ink[ipod_light()];
        if (ink) text_ink(line, 0xff000000u | ink, INK_LABEL);
        if (title) widget_resize(title, end - BT_STATE_W - 8 - BT_TEXT_X, I(title, W_H));
    }
}

static void quality_rows(void *view);
void bt_rows_post(void *view) {
    void *win = window_of(view);
    if (win && !tk_strcmp(widget_get_prop_str(win, "name", ""), "btquality_page")) {
        quality_rows(view);
        return;
    }
    for (unsigned i = 0, n = widget_count_children(view); i < n; ++i) {
        void *item = widget_get_child(view, i), *b = row_button(item);
        if (b && row_dev(item) >= 0) dress(item, b);
    }
}

/* The list's own page: Settings > Bluetooth's, or its Quality picker's. */
int bt_list(void *list) {
    if (!list) return 0;
    if (!tk_strcmp(widget_get_prop_str(list, "name", ""), "list_view_bluetooth")) return 1;
    void *win = window_of(list);
    return win && !tk_strcmp(widget_get_prop_str(win, "name", ""), "btquality_page");
}

/* ---- Quality (P5): each choice's bit rate, Auto's caption ---- */

/* Quality's choice i (g_btcoding's, stock's row order: LDAC Best, Standard, Auto, aptX HD, aptX,
 * AAC, SBC HQ, SBC): its bit rate at 48 kHz where the codec fixes one (bt_detail.c bt_kbps), 0
 * for Auto, AAC and SBC, whose rate the link sets. */
static unsigned choice_kbps(int i) {
    static const unsigned short kbps[] = { 990, 660, 0, 576, 384, 0, 0, 0 };
    return i >= 0 && i < (int)(sizeof kbps / sizeof *kbps) ? kbps[i] : 0;
}
#define BT_AUTO 2    /* LDAC Auto's row */
#define BT_KBPS_W 90 /* a choice's rate, right-aligned before the check */
#define BT_KBPS_GAP 24

/* After the layout: the picker's rows get their rates, Auto its caption. */
static void quality_rows(void *view) {
    for (unsigned i = 0, n = widget_count_children(view); i < n; ++i) {
        void *item = widget_get_child(view, i), *b = row_button(item), *title = (void *)0;
        if (!b || widget_lookup(b, "_bt_kbps", 0)) continue;
        int choice = atoi(widget_get_prop_str(b, "name", ""));
        for (unsigned j = 0, k = widget_count_children(b); j < k && !title; ++j)
            if (!tk_strcmp(widget_get_type(widget_get_child(b, j)), "hscroll_label"))
                title = widget_get_child(b, j);
        unsigned kbps = choice_kbps(choice);
        if (choice == BT_AUTO && title)
            caption_row(title, (const unsigned *)L"Steps with the signal, 330 to 990 kbps");
        if (!kbps) continue;
        char s[16];
        int right = I(b, W_W) - geom_val(V_RIGHT_X) - BT_KBPS_GAP;
        void *l = small_label(b, right - BT_KBPS_W, 0, BT_KBPS_W, I(b, W_H), BT_HEAD_PX);
        widget_set_name(l, "_bt_kbps");
        widget_set_prop_str(l, "style:normal:text_align_h", "right");
        tk_snprintf(s, sizeof s, "%u kbps", kbps);
        widget_set_text_utf8(l, s);
    }
}

/* ---- painting ---- */

static void art(void *w, void *canvas, const char *stem, int cx, int cy) {
    unsigned bitmap[64]; /* bitmap_t */
    if (!widget_load_image(w, theme_art(stem), bitmap)) canvas_draw_icon(canvas, bitmap, cx, cy);
}

/* A mark (bt_page's, dress's), in final colours: white on the selection. */
void bt_ui_paint(void *w, void *canvas) {
    int kind = w && bt.view ? widget_get_prop_int(w, BT_MARK, 0) : 0;
    if (!kind || !widget_get_visible(w)) return;
    void *b = P(w, W_PARENT), *item = b ? P(b, W_PARENT) : (void *)0;
    int selected = st.sel_accent && painting_in(st.sel_accent), light = ipod_light();
    int cx = I(w, W_W) / 2, cy = I(w, W_H) / 2;
    char stem[32];
    switch (kind) {
    case MARK_GLYPH: {
        int dev = item ? row_dev(item) : -1;
        tk_snprintf(stem, sizeof stem, "btdev_%s_%c", bt_kind(bt_class(dev_at(dev, 0))),
                    selected ? 'w'
                    : light  ? 'l'
                             : 'd');
        art(w, canvas, stem, cx, cy);
        break;
    }
    case MARK_DOT: {
        rect_t r = { 0, cy - BT_DOT / 2, BT_DOT, BT_DOT };
        fill_box(canvas, &r, RGBA(selected ? tok(TOK_ON_ACCENT) : tok(TOK_POSITIVE)), BT_DOT / 2);
        break;
    }
    case MARK_CHEVRON:
        chevron_icon(w, canvas, I(w, W_W) - BT_SPIN / 2 + 5, cy, selected);
        break;
    case MARK_SPIN:
    case MARK_HEAD_SPIN: {
        int x = cx;
        if (kind == MARK_HEAD_SPIN) { /* beside its header's label, while a scan runs */
            void *l = b ? widget_lookup(b, "_bt_head", 0) : (void *)0;
            const unsigned *s = l ? widget_get_text(l) : (const unsigned *)0;
            if (bt_searchtime <= 1 || !s) return;
            int px = BT_HEAD_PX;
            unsigned n = wide_len(s);
            canvas_set_font(canvas, theme_face(s, (int)n, &px), (unsigned)px);
            x = I(l, W_X) + 2 + (int)(canvas_measure_text(canvas, s, n) + 0.5f) + BT_SPIN_GAP +
                BT_SPIN / 2;
            cy = I(l, W_Y) + I(l, W_H) / 2;
        }
        if (selected)
            tk_snprintf(stem, sizeof stem, "spin_%d", bt.frame % 8);
        else
            tk_snprintf(stem, sizeof stem, "btspin_%c_%d", light ? 'l' : 'd', bt.frame % 8);
        art(w, canvas, stem, x, cy);
        break;
    }
    }
}

/* ---- the page's upkeep, every UI loop pass ---- */

/* Whether a device is connected or connecting, by the rows stock built. */
static int busy(void) {
    for (unsigned i = 0, n = bt.view ? widget_count_children(bt.view) : 0; i < n; ++i) {
        void *item = widget_get_child(bt.view, i), *b = row_button(item);
        if (b && row_dev(item) >= 0 && row_state(b) >= BT_CONNECTING) return 1;
    }
    return 0;
}

/* While the page is in front with Bluetooth on: a scan when none runs, the first as it opens and
 * then again each time one ends while nothing is connected (a scan beside a stream costs its air
 * time); the spinner's frames; the classes as the fetch answers. */
static void page_poll(unsigned now) {
    void *win = bt.view ? window_of(bt.view) : (void *)0;
    if (!win || win != window_manager_get_top_window(window_manager())) return;
    if (bt.head_spin && bt_searchtime > 1 && now - bt.spin_at >= BT_SPIN_MS) {
        bt.spin_at = now;
        ++bt.frame;
        widget_invalidate_force(P(bt.head_spin, W_PARENT), (void *)0);
    }
    if (now - bt.poll_at < BT_POLL_MS) return;
    bt.poll_at = now;
    if (class_read()) widget_invalidate_force(win, (void *)0);
    if (!g_bluetoothflag || bt_searchtime || I(bt_initstatus, 0) || I(bt_connectstatus, 0) == 1)
        return;
    if (bt.scans && busy()) return;
    ++bt.scans;
    btStartSearch();
    g_btsetting_flag = 1; /* the page's timer rebuilds the list from the scan's first results */
}

/* ---- the connection toasts (docs/oled-design.md#dialogs-and-toasts) ---- */

/* The device connected now (stock's link list's last item at status 3) into mac[24]; 0 none. */
static int linked(char *mac) {
    char item[32] = "";
    int status = 0, n = g_bluetoothflag ? getBtLinkSize() : 0;
    if (n > 0) getBtLinkItem(n - 1, &status, item);
    if (status != 3) return 0;
    tk_snprintf(mac, 24, "%s", item);
    return 1;
}

/* A toast for mac: "<name> <what>", the kind's glyph (bttoast_<kind>, _off for a drop). */
static void link_toast(const char *mac, const char *what, int off) {
    const char *name = bt_dev_name(bt_dev_find(mac)), *kind = bt_kind(bt.link_cls);
    char s[96], glyph[40];
    tk_snprintf(s, sizeof s, "%s %s", name && *name ? name : "Bluetooth", what);
    tk_snprintf(glyph, sizeof glyph, "bttoast_%s%s", kind, off ? "_off" : "");
    toast_icon(theme_art(glyph), s);
}

/* Every BT_POLL_MS: a device connecting or dropping toasts it ("WH-1000XM5 connected", a car
 * "BMW 6R81 connected · resuming" with Resume on Connect on); its connect toast waits up to
 * BT_CLASS_WAIT_MS for its Class (bt_battery.c's loop) so a car says so. A headset's battery
 * toasts once as it reaches 20% and once at 10% each connection. A drop the user asked for (stock
 * toasts its own Disconnected) is not toasted again. */
static void link_poll(unsigned now) {
    char mac[24];
    int on = linked(mac);
    if (!on) {
        if (bt.link[0] && !feedback_toast(window_manager_get_top_window(window_manager())) &&
            !bt.link_pending)
            link_toast(bt.link, "disconnected", 1);
        bt.link[0] = 0, bt.link_pending = 0, bt.low = 0;
        return;
    }
    if (strcmp(mac, bt.link)) { /* a new connection */
        tk_snprintf(bt.link, sizeof bt.link, "%s", mac);
        bt.link_at = now, bt.link_pending = 1, bt.low = 0, bt.link_cls = 0;
    }
    unsigned cls = bt_headset_class();
    if (cls) bt.link_cls = cls;
    if (bt.link_pending && (bt.link_cls || now - bt.link_at >= BT_CLASS_WAIT_MS)) {
        bt.link_pending = 0;
        int car = !tk_strcmp(bt_kind(bt.link_cls), "car");
        link_toast(mac, car && ringnav_bt_resume() ? "connected · resuming" : "connected", 0);
        return;
    }
    int level = bt_headset_battery();
    int step = level < 0 ? 0 : level <= 10 ? 2 : level <= 20 ? 1 : 0;
    if (!bt.link_pending && step > bt.low) {
        char s[48];
        bt.low = step;
        tk_snprintf(s, sizeof s, "Headphones low · %d%%", level);
        toast_icon(theme_art("bttoast_low"), s);
    }
}

void bt_ui_poll(void) {
    unsigned now = (unsigned)time_now_ms();
    if (bt.view) page_poll(now);
    bt_detail_poll();
    if (now - bt.link_poll_at >= BT_POLL_MS) {
        bt.link_poll_at = now;
        link_poll(now);
    }
}
