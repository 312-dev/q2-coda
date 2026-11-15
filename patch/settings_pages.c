/* A set_switch theme's settings pages that are no list of rows. About as an inert table
 * (docs/oled-design.md Screens: About): stock's rows, each a key and its value, stay where set_row
 * maps them but take no selection, wheel or tap (disabled, so ringnav's collect passes them), the
 * key in Text Primary and the value in the theme's value grey, in the disable state too, each value
 * as wide as its key leaves it (about_rows); from ipod_list_layout, after set_row. */
#include "navigation.h"

#define ABOUT_INK_STATES 4 /* text_ink's normal, pressed, over and disable */
#define ABOUT_GAP 12       /* between a key's text and the widest its value may reach */

/* label's text's width in px, in the face and size its row draws it; -1 with no canvas yet */
static int about_text_w(void *label) {
    void *canvas = widget_get_canvas(label);
    const unsigned *s = widget_get_text(label);
    int px = theme()->font_px[ROLE_TITLE];
    if (!canvas || !s || !px) return -1;
    unsigned n = wide_len(s);
    canvas_set_font(canvas, theme_face(s, (int)n, &px), (unsigned)px);
    return (int)(canvas_measure_text(canvas, s, n) + 0.5f);
}

int about_list(void *list) {
    return list && theme()->set_switch &&
           !tk_strcmp(widget_get_prop_str(window_of(list), "name", ""), "about_page");
}

/* Each row's first hscroll_label is its key, the next its value (stock's order, and
 * ringnav_about's for CFW. Version). Stock's value box is 149 px from x 176, too narrow for a dev
 * build's version or a Wi-Fi MAC beside a short key: it takes the row from ABOUT_GAP after its
 * key's text to the right column, right-aligned, so it ends in an ellipsis only when it is wider
 * than that (docs/oled-design.md Type: truncate, never shrink). This runs on every layout, so the
 * value's right edge, which set_child put on the right column, is the one fixed point. */
void about_rows(void *view) {
    unsigned value = theme()->set_value_ink[ipod_light()],
             primary = flip_if(ipod_light(), 0xffffff); /* Text Primary: black in Light Mode */
    for (unsigned i = 0, n = widget_count_children(view); i < n; ++i) {
        void *item = widget_get_child(view, i),
             *b = widget_count_children(item) ? widget_get_child(item, 0) : (void *)0,
             *key = (void *)0, *val = (void *)0;
        widget_set_enable(item, 0);
        for (unsigned j = 0, k = b ? widget_count_children(b) : 0; j < k && !val; ++j) {
            void *c = widget_get_child(b, j);
            if (tk_strcmp(widget_get_type(c), "hscroll_label")) continue;
            *(key ? &val : &key) = c;
        }
        if (key) text_ink(key, 0xff000000u | primary, ABOUT_INK_STATES);
        if (!val) continue;
        text_ink(val, 0xff000000u | (value ? value : primary), ABOUT_INK_STATES);
        int kw = key ? about_text_w(key) : -1, right = I(val, W_X) + I(val, W_W);
        if (kw < 0) continue;
        int left = I(key, W_X) + 2 + kw + ABOUT_GAP; /* after hscroll_label's 2 px margin */
        if (left < right) widget_move_resize(val, left, I(val, W_Y), right - left, I(val, W_H));
    }
}
