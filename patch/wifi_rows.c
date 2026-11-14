/* A set_switch theme's Wi-Fi page (docs/oled-design.md Screens: Wi-Fi), over the list stock's
 * wifimenu_rows_build (0x4d5950) makes each time it rebuilds: the switch row; stock's Scanning row
 * (its refresh image or spinner, wifi_search_icon) taken out; its centred "Select Network" header
 * made a section label, "Networks", after a 12 px gap, in the section header's type on the text
 * column, with "Searching..." on the same line's right column while a scan runs (wifi_searchtime);
 * each network a row whose right column holds its signal (stock's wifi_sig1..4 image, its art
 * emptied by the image hook) and, before it, a lock when the network asks for a password (its
 * scan record's flags name WPA or WEP), and the Accent check when it is the connected one (stock's
 * second line reads Connected); Add Network last, as stock has it. Laid out from ipod_list_layout
 * (wifi_rows_pre before stock's layout stacks the rows, wifi_rows_post after set_row has mapped
 * them) and drawn by wifi_mark from paint_into. ponytail: the emulator has no radio, so the
 * connected row and a real scan were seen only with a stubbed wpa_cli. */
#include "navigation.h"

#define WIFI_GAP 12       /* the section's space above its label (docs/oled-design.md Spacing) */
#define WIFI_HEAD 32      /* ... and the label's line */
#define WIFI_HEAD_PX 18   /* 15 px, AWTK's size (oled-design.md Type: Section header) */
#define WIFI_GLYPH 20     /* a row symbol (Phosphor regular, tools/art.py) */
#define WIFI_GLYPH_GAP 8  /* between the right column's glyphs */
#define WIFI_INK_STATES 4 /* text_ink's normal, pressed, over and disable */
#define WIFI_BUSY "_wifi_busy"
#define WIFI_HEAD_NAME "_wifi_head"

/* Stock's signal image's name, wifi_sig1 to wifi_sig4: its level, else 0. */
int wifi_sig(const char *image) {
    static const char sig[] = "wifi_sig";
    for (unsigned i = 0; i < sizeof sig - 1; ++i)
        if (!image || image[i] != sig[i]) return 0;
    return image[8] >= '1' && image[8] <= '4' && !image[9] ? image[8] - '0' : 0;
}

/* The list's own page: stock's list_view_wifi on a set_switch theme. */
int wifi_list(void *list) {
    return list && theme()->set_switch &&
           !tk_strcmp(widget_get_prop_str(list, "name", ""), "list_view_wifi");
}

/* The header item's label: stock's label_create in a list_item with no button (wifimenu_msginfo_
 * layout), else 0. */
static void *head_label(void *item) {
    void *c = widget_count_children(item) == 1 ? widget_get_child(item, 0) : (void *)0;
    return c && !tk_strcmp(widget_get_type(c), "label") ? c : (void *)0;
}

static void *small_label(void *parent, int x, int w, const char *name, const char *align) {
    void *l = label_create(parent, x, WIFI_GAP, w, WIFI_HEAD);
    widget_set_name(l, name);
    face_font(l, theme()->font[ROLE_META], WIFI_HEAD_PX, FONT_ROW);
    /* its item is disabled (wifi_rows_pre), so the disable state's look is the normal one's */
    widget_set_prop_str(l, "style:normal:text_align_h", align);
    widget_set_prop_str(l, "style:disable:text_align_h", align);
    text_ink(l, 0xff000000u | scheme()->muted, WIFI_INK_STATES);
    return l;
}

/* Before stock's layout: the Scanning row out of the flow, the header's height and its label, and,
 * when the rows run past the box, the list's box ending on the last whole row that fits from its
 * top, as list_fit's boxes do for one pitch: the section's gap puts the rows below it off the
 * 32 px grid. */
void wifi_rows_pre(void *view) {
    void *list = P(view, W_PARENT);
    int room = geom_val(V_LIST_END) - I(list, W_Y), fits = 0;
    for (unsigned i = 0, n = widget_count_children(view); i < n; ++i) {
        void *item = widget_get_child(view, i), *label = head_label(item);
        if (!label) label = widget_lookup(item, WIFI_HEAD_NAME, 0); /* laid out before */
        if (widget_lookup(item, "wifi_search_icon", 1)) {
            widget_set_visible(item, 0, 0);
            continue;
        }
        if (!label) continue;
        I(item, W_H) = WIFI_GAP + WIFI_HEAD;
        /* a heading, not a row: the wheel and touch pass it (stock's click only scrolled to it) */
        widget_set_enable(item, 0);
        int x = geom_val(V_TEXT_X), right = 375 - geom_val(V_RIGHT_X); /* labels keep no margin */
        if (tk_strcmp(widget_get_prop_str(label, "name", ""), WIFI_HEAD_NAME)) {
            widget_set_visible(label, 0, 0); /* stock's centred header; ours in its place */
            widget_set_name(label, WIFI_HEAD_NAME);
            widget_set_text_utf8(small_label(item, x, 200, "_wifi_section", "left"), "Networks");
            small_label(item, right - 150, 150, WIFI_BUSY, "right");
        }
        void *busy = widget_lookup(item, WIFI_BUSY, 0);
        if (busy) widget_set_text_utf8(busy, wifi_searchtime ? "Searching..." : "");
    }
    int y = 0;
    for (unsigned i = 0, n = widget_count_children(view); i < n; ++i) {
        void *item = widget_get_child(view, i);
        int h = !widget_get_visible(item)       ? 0
                : I(item, W_H) == SET_STOCK_ROW ? geom_val(V_SET_ROW) /* set_row's, to come */
                                                : I(item, W_H);
        if ((y += h) <= room) fits = y;
    }
    /* rows that all fit keep the whole box */
    if (y <= room) fits = room;
    if (fits && fits != I(list, W_H)) widget_resize(list, I(list, W_W), fits);
    /* ... and the scroll view in it, which a rebuild leaves at the size the list had */
    if (fits && fits != I(view, W_H)) widget_resize(view, I(view, W_W), fits);
}

/* After set_row: each network's signal image (which set_child hides as a leading icon) shown
 * again, across the right column's glyphs, for wifi_mark to draw in. */
void wifi_rows_post(void *view) {
    for (unsigned i = 0, n = widget_count_children(view); i < n; ++i) {
        void *item = widget_get_child(view, i),
             *b = widget_count_children(item) ? widget_get_child(item, 0) : (void *)0;
        for (unsigned j = 0, k = b ? widget_count_children(b) : 0; j < k; ++j) {
            void *c = widget_get_child(b, j);
            const char *image = widget_get_prop_str(c, "image", "");
            if (!wifi_sig(image) || tk_strcmp(widget_get_type(c), "image")) continue;
            /* the right column, or left of stock's forget image on the connected row */
            int w = 3 * WIFI_GLYPH + 2 * WIFI_GLYPH_GAP, right = I(b, W_W) - geom_val(V_RIGHT_X);
            for (unsigned m = 0; m < k; ++m) {
                void *t = widget_get_child(b, m);
                if (widget_get_visible(t) &&
                    !tk_strcmp(widget_get_prop_str(t, "image", ""), "navbar_delete") &&
                    I(t, W_X) < right)
                    right = I(t, W_X) + (I(t, W_W) - WIFI_GLYPH) / 2 - WIFI_GLYPH_GAP;
            }
            widget_move_resize(c, right - w, 0, w, I(b, W_H));
            widget_set_visible(c, 1, 0);
            /* the name ends before the glyphs */
            for (unsigned m = 0; m < k; ++m) {
                void *t = widget_get_child(b, m);
                if (!tk_strcmp(widget_get_type(t), "hscroll_label") &&
                    I(t, W_X) + I(t, W_W) > right - w - 8)
                    widget_resize(t, right - w - 8 - I(t, W_X), I(t, W_H));
            }
        }
    }
}

/* Whether the scan record of the row button b (stock names it by its index) asks for a password. */
static int locked(void *b) {
    const char *name = widget_get_prop_str(b, "name", "");
    void *list = P(wifi_showlist, 0);
    if (!name || name[0] < '0' || name[0] > '9' || !list) return 0;
    unsigned at = (unsigned)atoi(name);
    if (at >= deque_size(list)) return 0;
    const char *flags = P(deque_at(list, at), 4);
    return flags && (strstr(flags, "WPA") || strstr(flags, "WEP"));
}

/* Whether stock gave the row its Connected line (msg_connected). */
static int connected(void *b) {
    for (unsigned i = 0, n = widget_count_children(b); i < n; ++i) {
        const char *tr = widget_get_prop_str(widget_get_child(b, i), "tr_text", "");
        if (tr && !tk_strcmp(tr, "msg_connected")) return 1;
    }
    return 0;
}

static void glyph(void *w, void *canvas, const char *name, int cx, int cy) {
    unsigned bitmap[64]; /* bitmap_t */
    if (!widget_load_image(w, theme_art(name), bitmap)) canvas_draw_icon(canvas, bitmap, cx, cy);
}

/* paint_into, for a network row's signal image w (wifi_sig1..4: level 1..4): from the right
 * column leftwards, the signal, the lock, the check. White on the selection, else Text Primary
 * (the check the accent). */
void wifi_mark(void *w, void *canvas, int level, int selected) {
    void *b = P(w, W_PARENT);
    int cy = I(w, W_H) / 2, cx = I(w, W_W) - WIFI_GLYPH / 2, step = WIFI_GLYPH + WIFI_GLYPH_GAP;
    const char *half = selected || !ipod_light() ? "_d" : "_l";
    char name[16];
    tk_snprintf(name, sizeof name, "wifi_%d%s", level >= 4 ? 3 : level == 3 ? 2 : 1, half);
    glyph(w, canvas, name, cx, cy);
    if (locked(b)) {
        cx -= step;
        tk_snprintf(name, sizeof name, "lock%s", half);
        glyph(w, canvas, name, cx, cy);
    }
    if (connected(b)) glyph(w, canvas, selected ? "check_w" : "check_a", cx - step, cy);
}
