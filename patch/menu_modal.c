/* The inset modal (docs/oled-design.md#menus): the hold menus, Now Playing's More and a playlist's
 * More as one flat panel over the page they act on, under a set with Geometry.modal (OLED's).
 * The page stays painted under it, dimmed half way to black; the panel is #1C1C1E, MODAL_W wide
 * at MODAL_X, from MODAL_TOP, with MODAL_R corners and MODAL_PAD padding round 32 px rows, at most
 * MODAL_ROWS of them in view before the rows scroll inside it. A row's text stands MODAL_TEXT in
 * from the panel; an action's symbol (Phosphor, regular, tools/art.py MENU_ICONS) at its right, a
 * setting's before its label with the value at the right. The list's own selection bar lights the
 * rows: their boxes reach sel_inset - MODAL_PAD past the panel, so the bar is MODAL_PAD in from it
 * with the list's 8 px ends. */
#include "navigation.h"

#define MODAL_X 58
#define MODAL_W 259
#define MODAL_TOP 58
#define MODAL_PAD 8
#define MODAL_R 16
#define MODAL_ROW 32
#define MODAL_ROWS 6
#define MODAL_GAP 12    /* between Sorting and the actions, and before a destructive row */
#define MODAL_TEXT 16   /* a row's text and symbols from the panel's sides */
#define MODAL_LEAD 26   /* a setting's or a choice's label after its leading symbol */
#define MODAL_ICON 19   /* tools/art.py MENU_ICON_PX */
#define MODAL_CARET 4   /* a value's room before its direction caret */
#define MODAL_SWITCH 34 /* list_marks.c switch_mark's width */
#define MODAL_FILL 0x1c1c1e
#define MODAL_DIM 0x80000000u    /* black at 50% */
#define MODAL_SECONDARY 0xababab /* Text Secondary; the light half's below */
#define MODAL_SECONDARY_L 0x5c5c5c

/* A row's props: its symbol's art stem (tools/art.py menu_<stem>_<ink>), its value, and how they
 * stand (MM_*). */
#define MM_ICON "_mm_icon"
#define MM_VALUE "_mm_value"
#define MM_HOW "_mm_how"
#define MM_DIR "_mm_dir"

/* The open modals, the top one last: a picker over More keeps More's (hidden) under it. */
#define MODAL_MAX 2
static struct {
    void *win, *list, *view;
    int top, high; /* the rows' box on screen, which the panel pads */
} modals[MODAL_MAX] __attribute__((section(".scratch")));

int modal_set(void) { return geom_val(V_MODAL); }

/* The row box's left and width in the panel's list, which reaches past the panel by what the
 * selection bar's inset takes back. */
static int row_out(void) { return theme()->sel_inset - MODAL_PAD; }
int modal_row_w(void) { return MODAL_W + 2 * row_out(); }
static int text_x(void) { return row_out() + MODAL_TEXT; }

static int modal_gone(void *win, void *event) {
    for (int i = 0; i < MODAL_MAX; ++i)
        if (modals[i].win == win) modals[i].win = modals[i].list = modals[i].view = (void *)0;
    return 0;
}

/* An open modal's window (navigation.c paint_selection keeps the row under it lit). */
int modal_win(void *w) {
    for (int i = 0; w && i < MODAL_MAX; ++i)
        if (modals[i].win == w) return 1;
    return 0;
}

/* The rows' surface (the list's scroll_view) of an open modal, painted by modal_row_paint. */
int modal_rows_of(void *w) {
    for (int i = 0; w && i < MODAL_MAX; ++i)
        if (modals[i].view == w) return 1;
    return 0;
}

/* A 12 px space in the rows, never a rule; it is no row (nothing to click), so the wheel steps
 * over it. */
void *modal_gap(void *view) {
    void *gap = label_create(view, 0, 0, modal_row_w(), MODAL_GAP);
    widget_set_text_utf8(gap, "");
    return gap;
}

/* Row lays out its label for the panel and keeps its symbol and value for modal_row_paint: an
 * action's label from the text column to its symbol, a setting's or choice's after its leading
 * symbol, short of the value, a switch's short of the switch. icon 0: none. */
void modal_row(void *row, void *label, const char *icon, const char *value, int how) {
    int x = text_x() + (how == MM_SETTING || how == MM_CHOICE ? MODAL_LEAD : 0),
        end = modal_row_w() - text_x() -
              (value              ? 96
               : icon             ? MODAL_ICON + 8
               : how == MM_SWITCH ? MODAL_SWITCH + 8
                                  : 0);
    widget_set_prop_str(row, MM_ICON, icon ? icon : "");
    widget_set_prop_str(row, MM_VALUE, value ? value : "");
    widget_set_prop_int(row, MM_HOW, how);
    widget_set_prop_int(row, "style:normal:bg_color", 0);
    if (!label) return;
    widget_move_resize(label, x, 0, end - x, MODAL_ROW);
    theme_font(label, ROLE_TITLE, FONT_ROW);
    if (!tk_strcmp(widget_get_type(label), "hscroll_label")) { /* a long one scrolls when lit */
        hscroll_label_set_only_focus(label, 1);
        hscroll_label_set_ellipses(label, 1);
    }
}

static int modal_lit(const void *unused);
/* An open dialog as the modal: its title bar gone, its list the panel's rows, the page under it
 * dimmed by modal_paint. Rows are stacked as they stand, a gap's 12 px included, each row
 * MODAL_ROW high and the panel's width; at most MODAL_ROWS rows show. The list's forced row height
 * and default go, so the gaps keep theirs (ipod_list_layout stacks each child by its own) and the
 * list never reads as a settings list (navigation.c settings_list), which would move it. A dialog
 * with no list (a playlist's More) has its list_items for rows, placed in it the same way. */
void modal_frame(void *win, void *list) {
    void *view = list ? widget_lookup(list, "scroll_view", 1) : win;
    if (!view) view = list && widget_count_children(list) ? widget_get_child(list, 0) : (void *)0;
    if (!win || !view) return;
    void *nav = widget_lookup(win, "view_navbar", 1);
    if (nav) widget_set_visible(nav, 0, 0);
    widget_set_prop_str(win, "highlight", "");
    widget_set_prop_int(win, "style:normal:bg_color", 0);
    widget_set_prop_int(win, "style:normal:border_color", 0);
    if (list) {
        widget_set_prop_int(list, "style:normal:bg_color", 0);
        I(list, ROW_HEIGHT) = 0; /* and no default either, or it would read as a settings list */
        I(list, LIST_DEFAULT_ITEM_HEIGHT) = 0;
    }
    int w = modal_row_w(), x = list ? 0 : MODAL_X - row_out(), top = MODAL_TOP + MODAL_PAD,
        y = list ? 0 : top, shown = 0, rows = 0;
    for (unsigned i = 0, n = widget_count_children(view); i < n; ++i) {
        void *c = widget_get_child(view, i);
        const char *type = widget_get_type(c);
        int gap = !tk_strcmp(type, "label"), h = gap ? MODAL_GAP : MODAL_ROW;
        if (!widget_get_visible(c) || (!list && !gap && tk_strcmp(type, "list_item"))) continue;
        widget_move_resize(c, x, y, w, h);
        y += h;
        if (!gap && ++rows == MODAL_ROWS) shown = y;
    }
    if (!shown) shown = y;
    if (list) {
        widget_set_prop_int(view, "virtual_h", y);
        widget_move_resize(list, MODAL_X - row_out(), top, w, shown);
        widget_set_prop_str(view, "self_layout", "default(x=0,y=0,w=100%,h=100%)");
    } else
        shown -= top;
    int k = 0; /* its own slot, else a free one, else the last */
    while (k < MODAL_MAX && modals[k].win != win) ++k;
    if (k == MODAL_MAX) {
        k = 0;
        while (k < MODAL_MAX - 1 && modals[k].win) ++k;
        widget_on(win, EVT_DESTROY, modal_gone, win);
    }
    modals[k].win = win, modals[k].list = list, modals[k].view = view;
    modals[k].top = top, modals[k].high = shown;
    st.touch_mode = 0; /* opens on its row, lit, however it was asked for */
    timer_add(modal_lit, (void *)0, 0);
}

/* The modal's row lit from the pass after it opened: a tap that opened it (Now Playing's More)
 * hides the selection as its pointer events finish, after the init. */
static int modal_lit(const void *unused) {
    st.touch_mode = 0;
    void *top = window_manager_get_top_window(window_manager());
    if (top) widget_invalidate_force(top, (void *)0);
    return 0;
}

/* The dim and the panel, under an open modal's rows. */
void modal_paint(void *w, void *canvas) {
    int i = 0;
    while (i < MODAL_MAX && (!w || modals[i].win != w)) ++i;
    /* a modal whose rows hide under a picker (modal_pick) draws neither */
    if (i == MODAL_MAX || !P(canvas, CANVAS_LCD) ||
        (modals[i].list && !widget_get_visible(modals[i].list)))
        return;
    unsigned fill = (unsigned)I(P(canvas, CANVAS_LCD), LCD_FILL_COLOR);
    rect_t all = { 0, 0, I(w, W_W), I(w, W_H) },
           panel = { MODAL_X, modals[i].top - MODAL_PAD, MODAL_W, modals[i].high + 2 * MODAL_PAD };
    for (int y = 0; y < all.h; ++y) scrub_row(canvas, MODAL_DIM, 0, y, all.w);
    unsigned c = 0xff000000u | flip_if(ipod_light(), MODAL_FILL);
    for (int y = 0; y < panel.h; ++y) round_row(canvas, &panel, MODAL_R, y, c);
    stock_canvas_set_fill_color(canvas, fill);
}

static int modal_ink(void *canvas, const unsigned *s, unsigned n) {
    int px = theme()->font_px[ROLE_TITLE];
    canvas_set_font(canvas, theme_face(s, (int)n, &px), (unsigned)px);
    return (int)(canvas_measure_text(canvas, s, n) + 0.5f);
}

/* s (ASCII) right-aligned on column right of r, in the row's face and size, in a final colour;
 * at most room wide, ending in an ellipsis where it is wider. */
static void value_text(void *canvas, const char *s, int right, int room, const rect_t *r,
                       unsigned rgb) {
    unsigned buf[40], n = 0;
    while (s[n] && n < sizeof buf / sizeof *buf - 1) buf[n] = (unsigned char)s[n], ++n;
    buf[n] = 0;
    int ink = modal_ink(canvas, buf, n);
    while (n > 1 && ink > room) buf[--n - 1] = 0x2026, buf[n] = 0, ink = modal_ink(canvas, buf, n);
    rect_t box = { r->x + right - ink, r->y, ink + 1, r->h };
    draw_centred(canvas, buf, n, &box, (unsigned)theme()->font_px[ROLE_TITLE], RGBA(rgb));
}

static void icon_at(void *w, void *canvas, const char *stem, const char *ink, int x,
                    const rect_t *r) {
    char name[48];
    tk_snprintf(name, sizeof name, "menu_%s_%s", stem, ink);
    paint_art(w, canvas, theme_art(name), r->x + x, r->y + (r->h - MODAL_ICON) / 2);
}

/* A row's direction (DIR_*): a caret on the right column, after any value. */
void modal_dir(void *row, int dir) { widget_set_prop_int(row, MM_DIR, dir); }

/* One row of an open modal (navigation.c paint_modal_rows): its symbol and value, white on the
 * selection, Text Secondary off it; a choice's check before its label in the accent; a direction's
 * caret at the right, the value before it. */
void modal_row_paint(void *w, void *canvas, void *row, rect_t r, int selected) {
    const char *icon = widget_get_prop_str(row, MM_ICON, ""),
               *value = widget_get_prop_str(row, MM_VALUE, "");
    if (!icon) icon = "";
    if (!value) value = "";
    int how = widget_get_prop_int(row, MM_HOW, MM_ACTION), light = ipod_light();
    int dir = widget_get_prop_int(row, MM_DIR, DIR_NONE);
    int right = modal_row_w() - text_x(), lead = text_x(), raw = st.raw_ink;
    if (how == MM_SWITCH) { /* by stock's own switch image, hidden on the row */
        for (unsigned i = 0, n = widget_count_children(row); i < n; ++i) {
            const char *image = widget_get_prop_str(widget_get_child(row, i), "image", "");
            if (image && !strncmp(image, "switch_", 7))
                switch_mark(canvas, r.x + right, r.y + r.h / 2, !tk_strcmp(image, "switch_on"),
                            selected);
        }
        return;
    }
    if (!*icon && !*value && !dir) return;
    const char *ink = selected ? "w" : light ? "l" : "d";
    unsigned text = (unsigned)I(P(canvas, CANVAS_LCD), LCD_TEXT_COLOR);
    if (*icon && how != MM_ACTION)
        icon_at(w, canvas, icon, how == MM_CHOICE && !selected ? "a" : ink, lead, &r);
    else if (*icon && !*value)
        icon_at(w, canvas, icon, ink, right - MODAL_ICON, &r);
    if (dir) {
        icon_at(w, canvas, dir == DIR_UP ? "caret-up" : "caret-down", ink, right - MODAL_ICON, &r);
        right -= MODAL_ICON + MODAL_CARET;
    }
    if (*value) {
        st.raw_ink = 1;
        /* clear of its label's text: a long value gives way */
        int room = right - text_x() - MODAL_LEAD - MODAL_TEXT;
        for (unsigned i = 0, n = widget_count_children(row); i < n; ++i) {
            void *c = widget_get_child(row, i);
            const unsigned *t =
                tk_strcmp(widget_get_type(c), "hscroll_label") || !widget_get_visible(c)
                    ? (const unsigned *)0
                    : widget_get_text(c);
            if (t) room = right - I(c, W_X) - modal_ink(canvas, t, wide_len(t)) - MODAL_TEXT;
        }
        value_text(canvas, value, right, room, &r,
                   selected ? 0xffffff
                   : light  ? MODAL_SECONDARY_L
                            : MODAL_SECONDARY);
        st.raw_ink = raw;
    }
    stock_canvas_set_text_color(canvas, text);
}

/* A picker in the panel (Multiple Speed's, ratings.c's): stock's sortselect dialog, its rows
 * rebuilt as names, the current one checked, as the hold menu's choices are (navigation.c qm_open).
 * The rows of the dialog over which it opens, under, hide while it is up, and with them its dim and
 * panel, so one panel shows; they come back on the picker's close, the selection where it was. The
 * dialog itself stays shown: a hidden window would hand the keys to the page under it. A pick runs
 * from the next loop pass, after the close, with the picker's index. 1 if it opened. */
static struct {
    void (*pick)(int i);
    void *dialog, *under;
    unsigned timer;
    int chosen;
} pk __attribute__((section(".scratch")));

static void pick_close(void) {
    void *d = pk.dialog;
    pk.dialog = (void *)0;
    if (d) window_close(d);
}
static int pick_run(const void *unused) {
    pk.timer = 0;
    if (!pk.dialog) return 0;
    pick_close();
    if (pk.pick) pk.pick(pk.chosen);
    return 0;
}
static int pick_click(void *ctx, void *event) {
    if (pk.dialog && !pk.timer) {
        pk.chosen = (int)(long)ctx;
        pk.timer = timer_add(pick_run, (void *)0, 0);
    }
    return 0;
}
static int pick_back(void *dialog, void *event) {
    if (I(event, EVENT_TYPE) == EVT_KEY_UP && I(event, EVENT_KEY) != KEY_RETURN) return 0;
    pick_close();
    return STOP;
}
static int pick_gone(void *dialog, void *event) {
    if (pk.dialog == dialog) pk.dialog = (void *)0;
    if (pk.under)
        widget_set_visible(pk.under, 1, 0), widget_invalidate_force(window_of(pk.under), 0);
    pk.under = (void *)0;
    return 0;
}

int modal_pick(void *under, const char *const *names, int n, int current, void (*pick)(int i)) {
    void *wm = window_manager(), *page = window_manager_get_top_window(wm);
    if (pk.dialog || n <= 0) return 0;
    navigator_to("dialog/sortselect_dialog");
    void *dialog = window_manager_get_top_window(wm);
    if (dialog == page) return 0;
    if (tk_strcmp(widget_get_prop_str(dialog, "name", ""), "sortselect_dialog")) {
        window_close(dialog);
        return 0;
    }
    void *back = widget_lookup(dialog, "img_return", 1),
         *view = widget_lookup(dialog, "scroll_view", 1);
    widget_off_by_func(dialog, EVT_KEY_UP, (void *)SORTSELECT_KEYUP, dialog);
    widget_off_by_func(back, EVT_CLICK, (void *)SORTSELECT_CLOSE, dialog);
    widget_on(dialog, EVT_KEY_UP, pick_back, dialog);
    widget_on(back, EVT_CLICK, pick_back, dialog);
    widget_on(dialog, EVT_DESTROY, pick_gone, dialog);
    widget_destroy_children(view);
    for (int i = 0; i < n; ++i) {
        void *item = list_item_create(view, 0, i * MODAL_ROW, modal_row_w(), MODAL_ROW);
        widget_use_style(item, "s_listitem_black");
        void *label = hscroll_label_create(item, 0, 0, 0, MODAL_ROW);
        widget_use_style(label, "s_scrlabel_white24l");
        widget_set_text_utf8(label, names[i]);
        modal_row(item, label, i == current ? "check" : (const char *)0, (const char *)0,
                  MM_CHOICE);
        widget_on(item, EVT_CLICK, pick_click, (void *)(long)i);
    }
    pk.dialog = dialog, pk.pick = pick;
    pk.under = under;
    if (under) widget_set_visible(under, 0, 0);
    modal_frame(dialog, widget_lookup(dialog, "list_view", 1));
    ringnav_select(view, current >= 0 && current < n ? current : 0, n);
    return 1;
}

/* Now Playing's More (onlinematch.c ringnav_playermore): a dialog under a modal set
 * (tools/ipod.py modal_page), modeless, as the hold menu's, so navigator_to returns. Its rows as
 * the panel's, each with its symbol: stock's by their kind (PLAYMORE_ROWS, the button's name its
 * index), AirPlay's and Download Album Lyrics' by their images. Delete Song, which asks first, goes
 * last after a gap. A-B Repeat's stock switch hides, and the row paints the theme's (switch_mark)
 * on the right column by its image. Multiple Speed is a setting row, its value the speed, opening
 * a picker of the four (M3); a pick is a click on stock's own speed button for it, which the
 * hidden row keeps. The menu opens on its first row. */
static const char *const more_icons[] = { 0,
                                          "speedometer",
                                          "queue",
                                          "list-plus",
                                          "download-simple",
                                          "vinyl-record",
                                          "microphone-stage",
                                          "trash" };
static const char *const speeds[] = { "1x", "1.25x", "1.5x", "2x" };
static void *more_win __attribute__((section(".scratch")));

static void more_speed_pick(int i) {
    char name[16];
    tk_snprintf(name, sizeof name, "img_forward_%d", i);
    void *b = more_win ? widget_lookup(more_win, name, 1) : (void *)0;
    char click[0x30];
    if (b) stock_dispatch_trampoline(b, pointer_event_init(click, EVT_CLICK, b, 0, 0));
    void *row = more_win ? widget_lookup(more_win, "_mm_speed", 1) : (void *)0;
    if (row) widget_set_prop_str(row, MM_VALUE, speeds[g_playspeed_mode & 3]);
}
static int more_speed(void *win, void *event) {
    more_win = win;
    modal_pick(widget_lookup(win, "list_view_more", 1), speeds, 4, g_playspeed_mode & 3,
               more_speed_pick);
    return STOP;
}
/* Return closes the dialog: stock's key-up (on_common_keyup) goes back a page, which would close
 * the page under it and leave the menu over the one before. */
#define COMMON_KEYUP 0x4f2394
static int modal_back(void *win, void *event) {
    if (I(event, EVENT_KEY) != KEY_RETURN) return 0;
    if (!widget_get_prop_int(win, "_mm_closing", 0)) { /* the list's and the dialog's: once */
        widget_set_prop_int(win, "_mm_closing", 1);
        window_close(win);
    }
    return STOP;
}
/* ... on stock's list w and on the dialog, which has the keys when nothing in it has the focus
 * (back from a picker) */
static void modal_return(void *w, void *win) {
    widget_off_by_func(w, EVT_KEY_UP, (void *)COMMON_KEYUP, win);
    widget_on(w, EVT_KEY_UP, modal_back, win);
    if (w != win) widget_on(win, EVT_KEY_UP, modal_back, win);
}

/* A switch row's flip, which stock draws on its hidden image: the row shows it. */
static int switch_flipped(void *row, void *event) {
    widget_invalidate_force(row, (void *)0);
    return 0;
}

static int more_gone(void *win, void *event) {
    if (more_win == win) more_win = (void *)0;
    return 0;
}

void modal_more(void *win) {
    void *list = widget_lookup(win, "list_view_more", 1),
         *view = list ? widget_lookup(list, "scroll_view_more", 1) : (void *)0;
    if (!view || tk_strcmp(widget_get_type(win), "dialog")) return;
    widget_set_prop_int(win, "to_modal", 0);
    modal_return(list, win); /* stock binds it on the list, its context the page */
    more_win = win;
    widget_on(win, EVT_DESTROY, more_gone, win);
    void *last = (void *)0;
    unsigned n = widget_count_children(view);
    for (unsigned i = 0; i < n; ++i) {
        void *item = widget_get_child(view, i),
             *button = widget_count_children(item) ? widget_get_child(item, 0) : (void *)0;
        if (!button) continue;
        const char *name = widget_get_prop_str(button, "name", ""), *icon = (const char *)0;
        int kind = -1, how = MM_ACTION;
        if (!name) name = ""; /* an unnamed widget's name is null */
        void *label = (void *)0, *sw = (void *)0;
        if (*name >= '0' && *name <= '9' && (unsigned)(*name - '0') < PLAYMORE_COUNT)
            kind = ((const int *)PLAYMORE_ROWS)[*name - '0'];
        for (unsigned j = 0, m = widget_count_children(button); j < m; ++j) {
            void *c = widget_get_child(button, j);
            const char *type = widget_get_type(c);
            if (!tk_strcmp(type, "hscroll_label")) {
                if (label)
                    widget_set_visible(c, 0, 0); /* Speed's note under it */
                else
                    label = c;
            } else if (!tk_strcmp(type, "image")) {
                const char *image = widget_get_prop_str(c, "image", "");
                if (!image) image = "";
                if (!tk_strcmp(image, "airplay")) icon = "airplay";
                if (!tk_strcmp(image, "player_onlinesearch")) icon = "cloud-arrow-down";
                if (!strncmp(image, "switch_", 7))
                    sw = c;
                else
                    widget_set_visible(c, 0, 0); /* stock's icons; Speed's buttons */
            }
        }
        if (widget_lookup(button, "img_forward_0", 1)) kind = 1; /* the speed row, unnamed */
        if (kind >= 0 && kind < (int)(sizeof more_icons / sizeof *more_icons))
            icon = more_icons[kind];
        widget_move_resize(button, 0, 0, modal_row_w(), MODAL_ROW);
        if (kind == 1) { /* Multiple Speed */
            how = MM_SETTING;
            widget_set_prop_str(button, "name", "_mm_speed");
            widget_on(button, EVT_CLICK, more_speed, win);
            if (label) widget_set_text_utf8(label, "Speed");
        }
        if (sw) { /* A-B Repeat: the theme's switch in its place (rule 7), by its state */
            how = MM_SWITCH;
            widget_set_visible(sw, 0, 0);
            widget_on(button, EVT_CLICK, switch_flipped,
                      button); /* after stock's, which flips it */
        }
        modal_row(button, label, icon, kind == 1 ? speeds[g_playspeed_mode & 3] : (const char *)0,
                  how);
        if (kind == PLAYMORE_DELETE && widget_get_visible(item)) last = item;
    }
    if (last) { /* last, after a gap */
        widget_restack(last, n - 1);
        widget_restack(modal_gap(view), n - 1);
    }
    modal_frame(win, list);
    ringnav_select(view, 0, (int)widget_count_children(view));
}

/* A playlist's More (playlists.c ringnav_playlistsmore), a dialog over Playlists under a modal
 * set as Now Playing's is: Rename and Edit Playlist, then Delete Playlist after a gap, which asks
 * first (stock's confirm). Its rows are list_items in the dialog itself, placed in the panel. */
void modal_playlist_more(void *win) {
    if (tk_strcmp(widget_get_type(win), "dialog")) return;
    widget_set_prop_int(win, "to_modal", 0);
    modal_return(win, win);
    void *del = (void *)0;
    unsigned n = widget_count_children(win);
    for (unsigned i = 0; i < n; ++i) {
        void *item = widget_get_child(win, i),
             *button = widget_count_children(item) ? widget_get_child(item, 0) : (void *)0;
        if (!button || tk_strcmp(widget_get_type(item), "list_item")) continue;
        void *label = (void *)0;
        for (unsigned j = 0, m = widget_count_children(button); j < m; ++j) {
            void *c = widget_get_child(button, j);
            if (!tk_strcmp(widget_get_type(c), "hscroll_label"))
                label = c;
            else
                widget_set_visible(c, 0, 0); /* stock's icons */
        }
        for (unsigned j = 0, m = widget_count_children(item); j < m; ++j)
            if (widget_get_child(item, j) != button)
                widget_set_visible(widget_get_child(item, j), 0, 0);
        const char *name = widget_get_prop_str(item, "name", "");
        if (!name) name = "";
        int is_del = !tk_strcmp(name, "item_del");
        const char *icon = is_del                            ? "trash"
                           : !tk_strcmp(name, "item_rename") ? "pencil-simple"
                                                             : "list-checks";
        widget_move_resize(button, 0, 0, modal_row_w(), MODAL_ROW);
        modal_row(button, label, icon, (const char *)0, MM_ACTION);
        modal_row(item, (void *)0, icon, (const char *)0, MM_ACTION); /* stock's rows click there */
        if (is_del && widget_get_visible(item)) del = item;
    }
    if (del) { /* last, after a gap */
        widget_restack(del, n - 1);
        widget_restack(modal_gap(win), n - 1);
    }
    modal_frame(win, (void *)0);
}
