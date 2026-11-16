/* A set_switch theme's Wi-Fi page (docs/oled-design.md Screens: Wi-Fi), over the list stock's
 * wifimenu_rows_build (0x4d5950) makes each time it rebuilds: the switch row; stock's Scanning row,
 * whose tap starts a scan, as a plain "Search Again" row that acts in place (its refresh image or
 * spinner, wifi_search_icon, hidden); its centred "Select Network" header
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
#define WIFI_HEAD_PX TYPE(15) /* 15 px, AWTK's size (oled-design.md Type: Section header) */
#define WIFI_GLYPH 20     /* a row symbol (Phosphor regular, tools/art.py) */
#define WIFI_GLYPH_GAP 8  /* between the right column's glyphs */
#define WIFI_INK_STATES 4 /* text_ink's normal, pressed, over and disable */
#define WIFI_BUSY "_wifi_busy"
#define WIFI_HEAD_NAME "_wifi_head"
#define WIFI_TRASH "_wifi_trash" /* the connected row's Forget image, its art wifi_mark's */

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

/* Before stock's layout: the Scanning row as Search Again, the header's height and its label. The
 * list's box runs on to the window's foot, its rows past the list end fading (navigation.c
 * list_fade). */
void wifi_rows_pre(void *view) {
    for (unsigned i = 0, n = widget_count_children(view); i < n; ++i) {
        void *item = widget_get_child(view, i), *label = head_label(item);
        if (!label) label = widget_lookup(item, WIFI_HEAD_NAME, 0); /* laid out before */
        void *icon = widget_lookup(item, "wifi_search_icon", 1);
        if (icon) {
            widget_set_visible(icon, 0, 0);
            void *b = widget_count_children(item) ? widget_get_child(item, 0) : (void *)0;
            for (unsigned j = 0, k = b ? widget_count_children(b) : 0; j < k; ++j)
                if (!tk_strcmp(widget_get_type(widget_get_child(b, j)), "hscroll_label"))
                    widget_set_text_utf8(widget_get_child(b, j), "Search Again");
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
}

static int connected(void *b);

/* After set_row: each network's signal image (which set_child hides as a leading icon) shown
 * again, across the right column's glyphs, for wifi_mark to draw in: on the connected row the
 * last of them is stock's Forget, whose image (navbar_delete) stays its tap, centred on that slot
 * with no art of its own, so the trash wifi_mark draws ends on the right column like every row's
 * signal, selected or not (docs/oled-design.md Screens: Wi-Fi). */
void wifi_rows_post(void *view) {
    for (unsigned i = 0, n = widget_count_children(view); i < n; ++i) {
        void *item = widget_get_child(view, i),
             *b = widget_count_children(item) ? widget_get_child(item, 0) : (void *)0;
        for (unsigned j = 0, k = b ? widget_count_children(b) : 0; j < k; ++j) {
            void *c = widget_get_child(b, j);
            const char *image = widget_get_prop_str(c, "image", "");
            if (!wifi_sig(image) || tk_strcmp(widget_get_type(c), "image")) continue;
            int slots = 3 + connected(b), right = I(b, W_W) - geom_val(V_RIGHT_X),
                w = slots * WIFI_GLYPH + (slots - 1) * WIFI_GLYPH_GAP;
            for (unsigned m = 0; m < k; ++m) {
                void *t = widget_get_child(b, m);
                if (!widget_get_prop_int(t, WIFI_TRASH, 0) &&
                    tk_strcmp(widget_get_prop_str(t, "image", ""), "navbar_delete"))
                    continue;
                widget_set_prop_int(t, WIFI_TRASH, 1);
                widget_set_prop_str(t, "image", ""); /* wifi_mark draws its trash */
                widget_move_resize(t, right - (WIFI_GLYPH + I(t, W_W)) / 2, I(t, W_Y), I(t, W_W),
                                   I(t, W_H));
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
 * column leftwards, the connected row's trash (Forget), the signal, the lock, the check, each
 * drawn at its optical size (tools/art.py ROW_INK). White on the selection, else Text Primary
 * (the check the accent). */
void wifi_mark(void *w, void *canvas, int level, int selected) {
    void *b = P(w, W_PARENT);
    int cy = I(w, W_H) / 2, cx = I(w, W_W) - WIFI_GLYPH / 2, step = WIFI_GLYPH + WIFI_GLYPH_GAP;
    const char *half = selected || !ipod_light() ? "_d" : "_l";
    char name[16];
    if (connected(b)) {
        tk_snprintf(name, sizeof name, "trash%s", half);
        glyph(w, canvas, name, cx, cy);
        cx -= step;
    }
    tk_snprintf(name, sizeof name, "wifi_%d%s", level >= 4 ? 3 : level == 3 ? 2 : 1, half);
    glyph(w, canvas, name, cx, cy);
    if (locked(b)) {
        cx -= step;
        tk_snprintf(name, sizeof name, "lock%s", half);
        glyph(w, canvas, name, cx, cy);
    }
    if (connected(b)) glyph(w, canvas, selected ? "check_w" : "check_a", cx - step, cy);
}
