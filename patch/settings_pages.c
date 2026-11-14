/* A set_switch theme's settings pages that are no list of rows. About as an inert table
 * (docs/oled-design.md Screens: About): stock's rows, each a key and its value, stay where set_row
 * maps them but take no selection, wheel or tap (disabled, so ringnav's collect passes them), the
 * key in Text Primary and the value in the theme's value grey, in the disable state too; from
 * ipod_list_layout, after set_row. Lock Screen's picture grid (lock_grid) on the text and right
 * columns, the chosen picture's check its one accent. */
#include "navigation.h"

#define ABOUT_INK_STATES 4 /* text_ink's normal, pressed, over and disable */

int about_list(void *list) {
    return list && theme()->set_switch &&
           !tk_strcmp(widget_get_prop_str(window_of(list), "name", ""), "about_page");
}

void about_rows(void *view) {
    unsigned value = theme()->set_value_ink[ipod_light()];
    for (unsigned i = 0, n = widget_count_children(view); i < n; ++i) {
        void *item = widget_get_child(view, i),
             *b = widget_count_children(item) ? widget_get_child(item, 0) : (void *)0;
        widget_set_enable(item, 0);
        for (unsigned j = 0, k = b ? widget_count_children(b) : 0; j < k; ++j) {
            void *c = widget_get_child(b, j);
            if (tk_strcmp(widget_get_type(c), "hscroll_label")) continue;
            /* a key on the text column, its value on the row's right side (set_child) */
            text_ink(c, 0xff000000u | (I(c, W_X) >= SET_RIGHT_SIDE && value ? value : 0xffffff),
                     ABOUT_INK_STATES);
        }
    }
}

/* Lock Screen's picture grid (lockscreen.c lockscreen_rows, after stock builds it): its thumbnails
 * (image_0, image_1, ...) three to a row from the text column to the right column, LOCK_GAP apart
 * and the screen's shape, their row as tall as they need; each keeps its check badge (img_choice,
 * the accent's), and stock's blue default-picture corner (scrsaver_defaulticon) goes, so the check
 * is the grid's one accent. */
#define LOCK_COLS 3
#define LOCK_GAP 8
void lock_grid(void *view) {
    if (!view || !theme()->set_switch) return;
    int x0 = geom_val(V_TEXT_X),
        w = (375 - geom_val(V_RIGHT_X) - x0 - (LOCK_COLS - 1) * LOCK_GAP) / LOCK_COLS,
        h = w * 320 / 375, at = 0; /* the thumbnail's place in its own row: stock's hold a few */
    void *row = (void *)0;
    char name[16];
    for (unsigned n = 0;; ++n) {
        tk_snprintf(name, sizeof name, "image_%u", n);
        void *img = widget_lookup(view, name, 1);
        if (row && (!img || P(img, W_PARENT) != row)) { /* the row before: as tall as its lines */
            widget_resize(row, I(row, W_W),
                          LOCK_GAP + (at + LOCK_COLS - 1) / LOCK_COLS * (h + LOCK_GAP));
            at = 0;
        }
        if (!img) break;
        row = P(img, W_PARENT);
        widget_move_resize(img, x0 + at % LOCK_COLS * (w + LOCK_GAP) - I(row, W_X),
                           LOCK_GAP + at / LOCK_COLS * (h + LOCK_GAP), w, h);
        ++at;
        for (unsigned j = 0, k = widget_count_children(img); j < k; ++j) {
            void *c = widget_get_child(img, j);
            if (!tk_strcmp(widget_get_prop_str(c, "image", ""), "scrsaver_defaulticon"))
                widget_set_visible(c, 0, 0);
        }
    }
}
