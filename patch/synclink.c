/* SyncLink, Shanling's phone remote, off unless the user turns it on (docs/internals.md#synclink).
 * Stock starts each of its listeners (Bluetooth RFCOMM and LE, Wi-Fi TCP 1751 and its multicast
 * discovery) only while g_synclinkflag, SYSSET SYNCLINK, is 1 (Bluetooth) or 2 (Wi-Fi), and only
 * its own page (System > Wireless > SyncLink) writes that key. So it stays as stock has it, and
 * this file turns off once what older firmware left on, and puts a caption under the SyncLink row
 * saying what turning it on allows: none of the listeners asks a phone for a password. */
#include "navigation.h"

/* After config_init (ringnav_usb_config), before platform_init goes on to Bluetooth, Wi-Fi and
 * initSyncLink: a SyncLink on from before Q2POD SYNCLINKOWN was written is turned off and saved so,
 * then the mark is written, after which SYNCLINK is the user's choice and is left alone. The
 * default config.ini stock copies in at first boot or a reset says SYNCLINK=0. ponytail: a SyncLink
 * the user turned on deliberately before this firmware goes off once too; the changelog says so, as
 * no file tells the two apart. */
void synclink_default(void) {
    if (config_value("Q2POD", "SYNCLINKOWN", 2, 0)) return;
    if (g_synclinkflag) {
        g_synclinkflag = 0;
        write_int_config(0, "SYSSET", "SYNCLINK");
    }
    write_int_config(1, "Q2POD", "SYNCLINKOWN");
}

#define HINT_NAME "_synclink_hint"
#define HINT_PX 18    /* 15 px, AWTK's size (oled-design.md Type: captions are Text Tertiary) */
#define HINT_GAP 4    /* under the row */
#define HINT_LINE 22  /* one line of it */
#define HINT_STATES 4 /* text_ink's normal, pressed, over and disable */
#define HINT_INSET 2  /* hscroll_label's own margin before its text; a label has none */

/* The Wireless page's list (systemset_wifiset_page_init: Wi-Fi, Bluetooth, SyncLink, in that
 * order). */
int synclink_list(void *list) {
    return list && !tk_strcmp(widget_get_prop_str(list, "name", ""), "list_view_wifiset");
}

/* Before stock's layout stacks the rows: the caption, once, as a last item the wheel and touch
 * pass, two lines high, wrapped at words. */
void synclink_hint_pre(void *view) {
    if (widget_lookup(view, HINT_NAME, 0)) return;
    int h = HINT_GAP + 2 * HINT_LINE;
    void *item = list_item_create(view, 0, 0, 375, h);
    widget_set_name(item, HINT_NAME);
    widget_use_style(item, "s_listitem_black"); /* the rows' items', with no border */
    widget_set_enable(item, 0);
    void *l = label_create(item, 0, HINT_GAP, 375, 2 * HINT_LINE);
    widget_set_text_utf8(l, "Lets nearby phones control this Q2 without a password");
    widget_set_prop_int(l, "line_wrap", 1);
    widget_set_prop_int(l, "word_wrap", 1);
    face_font(l, theme()->font[ROLE_META], HINT_PX, FONT_ROW);
    /* the item is disabled, so the disable state's look is the normal one's */
    widget_set_prop_str(l, "style:normal:text_align_h", "left");
    widget_set_prop_str(l, "style:disable:text_align_h", "left");
    widget_set_prop_str(l, "style:normal:text_align_v", "top");
    widget_set_prop_str(l, "style:disable:text_align_v", "top");
    text_ink(l, 0xff000000u | scheme()->muted, HINT_STATES);
}

/* After set_row has mapped the rows: the caption starts where the SyncLink row's words do and ends
 * on the right column. */
void synclink_hint_post(void *view) {
    void *item = widget_lookup(view, HINT_NAME, 0), *row = (void *)0;
    for (unsigned i = 0, n = widget_count_children(view); i < n; ++i) {
        void *c = widget_get_child(view, i);
        if (c == item) break;
        row = c;
    }
    void *b = row && widget_count_children(row) ? widget_get_child(row, 0) : (void *)0;
    for (unsigned j = 0, k = b ? widget_count_children(b) : 0; item && j < k; ++j) {
        void *c = widget_get_child(b, j);
        if (tk_strcmp(widget_get_type(c), "hscroll_label")) continue;
        int x = I(b, W_X) + I(c, W_X) + HINT_INSET;
        widget_move_resize(widget_get_child(item, 0), x, HINT_GAP, 375 - geom_val(V_RIGHT_X) - x,
                           2 * HINT_LINE);
        return;
    }
}
