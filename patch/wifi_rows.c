/* A set_switch theme's Wi-Fi page (docs/oled-design.md Screens: Wi-Fi), over the list stock's
 * wifimenu_rows_build (0x4d5950) makes each time it rebuilds: the switch row; stock's Scanning row,
 * whose tap starts a scan, as a plain "Search Again" row that acts in place (its refresh image or
 * spinner, wifi_search_icon, hidden); its centred "Select Network" header made a section label,
 * "Networks", after a 12 px gap, in the section header's type on the text column, with
 * "Searching..." on the same line's right column while a scan runs (wifi_searchtime); each network
 * a one-line row whose right column holds its signal (stock's wifi_sig1..4 image, its art emptied by
 * the image hook) and, before it, a lock when the network asks for a password (its scan record's
 * flags name WPA or WEP); Add Network after the networks, as stock has it (Reset Network Settings,
 * wifi_flows.c, after it).
 *
 * The network joining, joined or whose join failed (wa) is the first row under "Networks", in the
 * same rows, the others in scan order: before its signal and lock the list's spinner while it joins,
 * the Accent check once joined (and stock's Forget, its trash, after the signal), the Negative
 * warning mark once failed; while it is selected its state is a caption under the bar
 * ("Connecting\u2026", "Connected", "Wrong password" or "Couldn't connect", settings_pages.c
 * caption_row), stock's own second line hidden. A failed one stays first until the next join or
 * scan. Stock's state comes and goes with its 500 ms check, so a joining or joined network it stops
 * showing is kept first for WIFI_SETTLE_MS before the list falls back to scan order. A rebuild keeps
 * the selection on the network it was on, and a press on a network (wifi_rows_follow) leaves it on
 * that network once it is first. Laid out from ipod_list_layout (wifi_rows_pre before stock's
 * layout stacks the rows, wifi_rows_post after set_row has mapped them) and drawn by wifi_mark from
 * paint_into. ponytail: the emulator has no radio, so the states were seen only with a stubbed
 * wpa_cli and stock's globals written by hand. */
#include "navigation.h"

#define WIFI_GAP 12  /* the section's space above its label (docs/oled-design.md Spacing) */
#define WIFI_HEAD 32 /* ... and the label's line */
#define WIFI_HEAD_PX TYPE(15) /* 15 px, AWTK's size (oled-design.md Type: Section header) */
#define WIFI_GLYPH 20         /* a row symbol (Phosphor regular, tools/art.py) */
#define WIFI_GLYPH_GAP 8      /* between the right column's glyphs */
#define WIFI_INK_STATES 4     /* text_ink's normal, pressed, over and disable */
#define WIFI_BUSY "_wifi_busy"
#define WIFI_HEAD_NAME "_wifi_head"
#define WIFI_TRASH "_wifi_trash" /* the connected row's Forget image, its art wifi_mark's */
#define WIFI_SETTLE_MS 2000   /* a joining or joined network stock stops showing stays first */
#define WIFI_PRESS_MS 60000   /* a press's network is followed by the selection this long */
#define WIFI_SSID 33          /* 32 bytes and its end */
enum { WA_NONE, WA_JOINING, WA_JOINED, WA_FAILED };

/* ssid: the network first under Networks, kind (WA_*) its state, wrong: a failed join's password
 * was refused; seen: when stock last showed it joining or joined; keep: the selected network's name
 * as stock began a rebuild, rebuilt then; press: the network pressed, at pressed_at; settle: the
 * timer that rebuilds once a kept state is over. */
static struct {
    char ssid[WIFI_SSID], keep[WIFI_SSID], press[WIFI_SSID];
    unsigned char kind, wrong, rebuilt;
    unsigned seen, pressed_at, settle;
} wa __attribute__((section(".scratch")));

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

/* The scan record's name of the row button b (stock names it by the record's index), 0 when b is no
 * network's (the switch and Search Again rows have no signal image). */
static const char *row_ssid(void *b) {
    int network = 0;
    for (unsigned i = 0, n = b ? widget_count_children(b) : 0; i < n; ++i)
        network |= wifi_sig(widget_get_prop_str(widget_get_child(b, i), "image", "")) > 0;
    const char *name = network ? widget_get_prop_str(b, "name", "") : (const char *)0;
    void *list = P(wifi_showlist, 0);
    if (!name || name[0] < '0' || name[0] > '9' || !list) return 0;
    unsigned at = (unsigned)atoi(name);
    return at < deque_size(list) ? (const char *)P(deque_at(list, at), 0) : (const char *)0;
}
/* Stock's second line on the row button b (its tr_text, msg_connecting or msg_connected), "" none. */
static const char *stock_line(void *b) {
    for (unsigned i = 0, n = b ? widget_count_children(b) : 0; i < n; ++i) {
        const char *tr = widget_get_prop_str(widget_get_child(b, i), "tr_text", "");
        if (tr && (!tk_strcmp(tr, "msg_connecting") || !tk_strcmp(tr, "msg_connected"))) return tr;
    }
    return "";
}
/* The state (WA_*) the row button b shows: the first row's, WA_NONE for every other. */
static int state_of(void *b) {
    const char *ssid = wa.kind ? row_ssid(b) : (const char *)0;
    return ssid && !tk_strcmp(ssid, wa.ssid) ? wa.kind : WA_NONE;
}
static void *row_button_of(void *item) {
    void *b = widget_count_children(item) ? widget_get_child(item, 0) : (void *)0;
    return b && !tk_strcmp(widget_get_type(b), "button") ? b : (void *)0;
}
/* The list's tap target id (its rows' buttons, as navigation.c load_rows counts them: a shown,
 * enabled item's button, the disabled heading passed) of the row button b, or -1; with b 0, the
 * count of them. Shared with wifi_flows.c (wifi_tap_button). */
static int tap_id(void *view, void *b) {
    int id = 0;
    for (unsigned i = 0, n = view ? widget_count_children(view) : 0; i < n; ++i) {
        void *item = widget_get_child(view, i), *c = row_button_of(item);
        if (!c || !widget_get_visible(item) || !widget_get_prop_bool(item, "enable", 1)) continue;
        if (c == b) return id;
        ++id;
    }
    return b ? -1 : id;
}
void *wifi_tap_button(void *view, int id) {
    for (unsigned i = 0, n = view ? widget_count_children(view) : 0; i < n && id >= 0; ++i) {
        void *item = widget_get_child(view, i), *c = row_button_of(item);
        if (c && widget_get_visible(item) && widget_get_prop_bool(item, "enable", 1) && !id--) return c;
    }
    return (void *)0;
}

/* The page's timer rebuilds the list once a state kept past stock's (wifi_active) is over. */
static int wa_settled(const void *unused) {
    (void)unused;
    wa.settle = 0;
    g_wifisetting_flag = 1;
    return 0; /* RET_REMOVE */
}
static void wa_set(const char *ssid, int kind) {
    if (tk_strcmp(ssid, wa.ssid)) tk_snprintf(wa.ssid, sizeof wa.ssid, "%s", ssid);
    wa.kind = (unsigned char)kind;
}
/* The first row's network from stock's rows of view, before they are laid out: one stock shows
 * joining (msg_connecting), else one it shows joined (msg_connected), else the failed one (until
 * a scan starts, wifi_rows_scanned), else the last joining or joined one while it is not WIFI_SETTLE_MS since stock
 * last showed it, a timer rebuilding the list when that is over. */
static void wifi_active(void *view) {
    unsigned now = (unsigned)time_now_ms();
    const char *joining = 0, *joined = 0;
    for (unsigned i = 0, n = widget_count_children(view); i < n; ++i) {
        void *b = row_button_of(widget_get_child(view, i));
        const char *ssid = b ? row_ssid(b) : (const char *)0, *line = ssid ? stock_line(b) : "";
        if (!tk_strcmp(line, "msg_connecting")) joining = ssid;
        if (!tk_strcmp(line, "msg_connected")) joined = ssid;
    }
    if (!g_wififlag) wa.kind = WA_NONE;
    if (joining || joined) {
        wa_set(joining ? joining : joined, joining ? WA_JOINING : WA_JOINED);
        wa.seen = now;
        stop_timer(&wa.settle);
    } else if (wa.kind == WA_JOINING || wa.kind == WA_JOINED) {
        if (now - wa.seen >= WIFI_SETTLE_MS)
            wa.kind = WA_NONE;
        else if (!wa.settle)
            wa.settle = timer_add(wa_settled, (void *)0, WIFI_SETTLE_MS - (now - wa.seen));
    }
}
/* The first row's item moved to just under the "Networks" header (head), the rest kept in order. */
static void wifi_first(void *view, int head) {
    for (unsigned i = 0, n = widget_count_children(view); wa.kind && head >= 0 && i < n; ++i) {
        void *item = widget_get_child(view, i), *b = row_button_of(item);
        const char *ssid = b ? row_ssid(b) : (const char *)0;
        if (!ssid || tk_strcmp(ssid, wa.ssid)) continue;
        if ((int)i != head + 1) widget_restack(item, (unsigned)head + 1);
        return;
    }
}

/* ringnav_wifi_rows, as stock begins a rebuild of view: the selected network's name, which the
 * rebuilt list's selection is put back on (wifi_rows_post). */
void wifi_rows_keep(void *view) {
    const char *ssid = row_ssid(wifi_tap_button(view, widget_get_prop_int(view, SEL, -1)));
    tk_snprintf(wa.keep, sizeof wa.keep, "%s", ssid ? ssid : "");
    wa.rebuilt = 1;
}
/* A press on network ssid's row (wifi_flows.c): the selection follows it once it is first. */
void wifi_rows_follow(const char *ssid) {
    tk_snprintf(wa.press, sizeof wa.press, "%s", ssid ? ssid : "");
    wa.pressed_at = (unsigned)time_now_ms();
}
/* A join of ssid failed (wifi_flows.c ringnav_wifi_checkstatus): it stays first, marked, until the
 * next join or scan; wrong: its password was refused. */
void wifi_rows_failed(const char *ssid, int wrong) {
    wa_set(ssid, WA_FAILED);
    wa.wrong = (unsigned char)(wrong != 0);
    stop_timer(&wa.settle);
}
/* A scan started (wifi_flows.c, Search Again): a failed network is first no more. */
void wifi_rows_scanned(void) {
    if (wa.kind == WA_FAILED) wa.kind = WA_NONE;
}
/* The user ended the join or forgot or reset the networks (wifi_flows.c): nothing is first. */
void wifi_rows_end(void) {
    wa.kind = WA_NONE;
    stop_timer(&wa.settle);
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
    int head = -1;
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
        head = (int)i;
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
    wifi_active(view);
    wifi_first(view, head);
}

/* The section line's "Searching..." set now, from wifi_searchtime, on the Wi-Fi page win: stock's
 * Search Again starts a scan (wifi_scan) without rebuilding the list, which is when wifi_rows_pre
 * writes the line, so the search would show nothing until it ended (wifi_flows.c). */
void wifi_busy_show(void *win) {
    void *busy = win ? widget_lookup(win, WIFI_BUSY, 1) : (void *)0;
    if (busy) widget_set_text_utf8(busy, wifi_searchtime ? "Searching..." : "");
}

/* ipod_list_layout: a row stock gave a second line is one line high all the same, its state the
 * first row's caption (wifi_rows_post) */
int wifi_connected_row(void *b) { return stock_line(b)[0] != 0; }
static const char *wa_caption(int kind) {
    return kind == WA_JOINING  ? "Connecting\xe2\x80\xa6"
           : kind == WA_JOINED ? "Connected"
           : wa.wrong           ? "Wrong password"
                                : "Couldn't connect";
}

/* The row button b's name (its first hscroll_label) and its second line (stock's next label,
 * whichever kind), 0 when it has none. */
static void *second_line(void *b, void **name) {
    void *line = (void *)0;
    *name = (void *)0;
    for (unsigned i = 0, n = widget_count_children(b); i < n && !line; ++i) {
        void *c = widget_get_child(b, i);
        const char *type = widget_get_type(c);
        if (!*name && !tk_strcmp(type, "hscroll_label"))
            *name = c;
        else if (*name && (!tk_strcmp(type, "label") || !tk_strcmp(type, "hscroll_label")))
            line = c;
    }
    return line;
}

/* After set_row: each network's signal image (which set_child hides as a leading icon) shown
 * again, across the right column's glyphs, for wifi_mark to draw in: on the connected row the
 * last of them is stock's Forget, whose image (navbar_delete) stays its tap, centred on that slot
 * with no art of its own, so the trash wifi_mark draws ends on the right column like every row's
 * signal, selected or not (docs/oled-design.md Screens: Wi-Fi). */
void wifi_rows_post(void *view) {
    caption_prune(view); /* the last build's rows are gone */
    for (unsigned i = 0, n = widget_count_children(view); i < n; ++i) {
        void *item = widget_get_child(view, i),
             *b = widget_count_children(item) ? widget_get_child(item, 0) : (void *)0,
             *name = (void *)0, *line = b ? second_line(b, &name) : (void *)0;
        int kind = b ? state_of(b) : WA_NONE;
        if (line && stock_line(b)[0]) { /* stock's state line: one line high, the name alone */
            widget_set_visible(line, 0, 0);
            widget_move_resize(name, I(name, W_X), 0, I(name, W_W), I(b, W_H));
        }
        /* the first row's state under the bar when selected; any other row drops a caption its
         * item holds: stock's rebuild can make an item at a freed one's address, whose entry in the
         * caption table would then read as the new row's */
        if (name) caption_row(name, kind ? wa_caption(kind) : (const char *)0);
        for (unsigned j = 0, k = b ? widget_count_children(b) : 0; j < k; ++j) {
            void *c = widget_get_child(b, j);
            const char *image = widget_get_prop_str(c, "image", "");
            if (!wifi_sig(image) || tk_strcmp(widget_get_type(c), "image")) continue;
            int slots = 3 + (kind == WA_JOINED), right = I(b, W_W) - geom_val(V_RIGHT_X),
                w = slots * WIFI_GLYPH + (slots - 1) * WIFI_GLYPH_GAP;
            for (unsigned m = 0; m < k; ++m) {
                void *t = widget_get_child(b, m);
                if (!widget_get_prop_int(t, WIFI_TRASH, 0) &&
                    tk_strcmp(widget_get_prop_str(t, "image", ""), "navbar_delete"))
                    continue;
                widget_set_prop_int(t, WIFI_TRASH, 1);
                widget_set_prop_str(t, "image", ""); /* wifi_mark draws its trash */
                /* on a row stock still shows joined but no longer first, nothing to forget is shown */
                widget_set_visible(t, kind == WA_JOINED, 0);
                widget_move_resize(t, right - (WIFI_GLYPH + I(t, W_W)) / 2, I(t, W_Y), I(t, W_W),
                                   I(t, W_H));
            }
            widget_move_resize(c, right - w, 0, w, I(b, W_H));
            widget_set_visible(c, 1, 0);
            /* the name ends before the glyphs (set_child made it only_focus: navigation.c) */
            for (unsigned m = 0; m < k; ++m) {
                void *t = widget_get_child(b, m);
                if (!tk_strcmp(widget_get_type(t), "hscroll_label") &&
                    I(t, W_X) + I(t, W_W) > right - w - 8)
                    widget_resize(t, right - w - 8 - I(t, W_X), I(t, W_H));
            }
        }
    }
    if (!wa.rebuilt) return;
    /* after a rebuild: the selection on the pressed network once it is first, else on the network
     * it was on (rows moved under it), else where it was */
    wa.rebuilt = 0;
    unsigned now = (unsigned)time_now_ms();
    const char *want = wa.keep;
    if (wa.press[0] && now - wa.pressed_at > WIFI_PRESS_MS) wa.press[0] = 0;
    if (wa.press[0] && wa.kind && !tk_strcmp(wa.press, wa.ssid)) want = wa.press;
    for (unsigned i = 0, n = want[0] ? widget_count_children(view) : 0; i < n; ++i) {
        void *b = row_button_of(widget_get_child(view, i));
        const char *ssid = b ? row_ssid(b) : (const char *)0;
        int id = ssid && !tk_strcmp(ssid, want) ? tap_id(view, b) : -1;
        if (id < 0) continue;
        if (id != widget_get_prop_int(view, SEL, -1)) ringnav_select(view, id, tap_id(view, 0));
        break;
    }
    if (want == wa.press) wa.press[0] = 0;
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

static void glyph(void *w, void *canvas, const char *name, int cx, int cy) {
    unsigned bitmap[64]; /* bitmap_t */
    if (!widget_load_image(w, theme_art(name), bitmap)) canvas_draw_icon(canvas, bitmap, cx, cy);
}

/* paint_into, for a network row's signal image w (wifi_sig1..4: level 1..4): from the right
 * column leftwards, the joined row's trash (Forget), the signal, the lock, then the first row's
 * state: the check once joined, the list's spinner while joining (list_marks.c turns it), the
 * warning mark once failed; each drawn at its optical size (tools/art.py ROW_INK). White on the
 * selection, else Text Primary (the check the accent, the warning Negative). */
void wifi_mark(void *w, void *canvas, int level, int selected) {
    void *b = P(w, W_PARENT);
    int cy = I(w, W_H) / 2, cx = I(w, W_W) - WIFI_GLYPH / 2, step = WIFI_GLYPH + WIFI_GLYPH_GAP;
    int kind = state_of(b), light = ipod_light() && !selected;
    const char *half = light ? "_l" : "_d";
    char name[16];
    if (kind == WA_JOINED) {
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
    cx -= step;
    if (kind == WA_JOINED) glyph(w, canvas, selected ? "check_w" : "check_a", cx, cy);
    if (kind == WA_FAILED)
        glyph(w, canvas, selected ? "wifi_warn_w" : light ? "wifi_warn_l" : "wifi_warn_d", cx, cy);
    if (kind == WA_JOINING) { /* white, the light half's in Text Tertiary as Bluetooth's */
        if (light)
            tk_snprintf(name, sizeof name, "btspin_l_%d", spinner_frame());
        else
            tk_snprintf(name, sizeof name, "spin_%d", spinner_frame());
        glyph(w, canvas, name, cx, cy);
        rect_t r = { cx - WIFI_GLYPH / 2, cy - WIFI_GLYPH / 2, WIFI_GLYPH, WIFI_GLYPH };
        spinner_painted(w, &r);
    }
}
