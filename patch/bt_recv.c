/* Bluetooth receiver mode (docs/internals.md#bluetooth-receiver, docs/oled-design.md#screens): what
 * the Q2 shows while a phone plays through it. Stock opens bluetoothrecv_page when a phone's A2DP
 * stream starts (btrecvA2dpFunc), with a 170 px Bluetooth picture, the phone's title and artist
 * (btrecv_info_labels_update, from AVRCP) in plain labels, the codec as a picture, a back arrow and
 * a gear (Audio settings). Here it is a mode page, as USB Audio's (feedback.c mode_page_paint): the
 * accent's solid circle with Phosphor's Bluetooth glyph, the song's title (or "Playing from Phone"
 * before the phone names one), its artist (or "Wheel sets the volume") and the codec in the pill.
 * Stock's labels and pictures stay, hidden, for stock to fill; the back arrow stays at opacity 0,
 * as stock's Return clicks it. The wheel stays stock's volume.
 * ponytail: stock's gear (Audio settings) is hidden with the rest: it sat under the status row's
 * battery, where a tap is the battery's toast. Audio settings are Settings > Playback; a pill or a
 * hold to reach them from here would bring it back. */
#include "navigation.h"

static struct {
    void *page;
} br __attribute__((section(".scratch")));

static int bt_recv_gone(void *ctx, void *event) {
    (void)event;
    if (ctx == br.page) br.page = (void *)0;
    return 0;
}

static void bt_recv_hide(void *win) {
    static const char *const hide[] = { "img_bticon", "label_title", "label_artist", "img_btcode",
                                        "img_setup" };
    void *c;
    for (unsigned i = 0; i < sizeof hide / sizeof *hide; ++i)
        if ((c = widget_lookup(win, hide[i], 1)) && widget_get_visible(c))
            widget_set_visible(c, 0, 0);
    if ((c = widget_lookup(win, "img_return", 1))) widget_set_opacity(c, 0);
}

/* ringnav_ui_load: the page as it loads, its asset's sample title and artist emptied so a phone
 * that sends none reads as none. */
void bt_recv_load(void *win) {
    if (tk_strcmp(widget_get_prop_str(win, "name", ""), "bluetoothrecv_page")) return;
    void *c;
    br.page = win;
    if ((c = widget_lookup(win, "label_title", 1))) widget_set_text_utf8(c, "");
    if ((c = widget_lookup(win, "label_artist", 1))) widget_set_text_utf8(c, "");
    bt_recv_hide(win);
    widget_on(win, EVT_DESTROY, bt_recv_gone, win);
}

/* The bar's hidden title, the page's name; 0 for another page. */
const char *bt_recv_title(const char *name) {
    return tk_strcmp(name, "bluetoothrecv_page") ? (const char *)0 : "Bluetooth";
}

/* The codec stock shows (img_btcode's btrecv_<codec> picture) as its name; 0 for none. */
const char *bt_recv_codec(void *win) {
    static const char *const codecs[][2] = { { "btrecv_ldac", "LDAC" },
                                             { "btrecv_aptxhd", "aptX HD" },
                                             { "btrecv_aptx", "aptX" },
                                             { "btrecv_aac", "AAC" },
                                             { "btrecv_sbc", "SBC" } };
    void *c = widget_lookup(win, "img_btcode", 1);
    const char *image = c ? widget_get_prop_str(c, "image", "") : "";
    for (unsigned i = 0; i < sizeof codecs / sizeof *codecs; ++i)
        if (!tk_strcmp(image, codecs[i][0])) return codecs[i][1];
    return (const char *)0;
}

/* ringnav_paint, after w's children: the page as a mode page. */
void bt_recv_paint(void *w, void *canvas) {
    if (!w || w != br.page || window_manager_is_animating(window_manager())) return;
    void *lcd = P(canvas, CANVAS_LCD);
    if (!lcd) return;
    unsigned fill = (unsigned)I(lcd, LCD_FILL_COLOR), text = (unsigned)I(lcd, LCD_TEXT_COLOR),
             raw = st.raw_ink;
    char title[128] = "", artist[128] = "";
    void *c;
    bt_recv_hide(w); /* stock's init and its updates may show them again */
    if ((c = widget_lookup(w, "label_title", 1))) widget_get_text_utf8(c, title, sizeof title);
    if ((c = widget_lookup(w, "label_artist", 1))) widget_get_text_utf8(c, artist, sizeof artist);
    st.raw_ink = 1;
    mode_page_paint(w, canvas, I(w, W_Y),
                    &(mode_page_t){ scheme()->tones[0] & 0xffffffu, "modepage_receive", 0,
                                    *title ? title : "Playing from Phone",
                                    *artist ? artist : "Wheel sets the volume", bt_recv_codec(w) });
    st.raw_ink = raw;
    stock_canvas_set_fill_color(canvas, fill);
    stock_canvas_set_text_color(canvas, text);
}
