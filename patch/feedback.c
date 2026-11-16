/* OLED's feedback forms (docs/oled-design.md#dialogs-and-toasts): stock's toast as a pill over the
 * page, and stock's confirm pop-up as a dialog page whose status row asks the question over Cancel
 * and the action's verb; an empty or offline page's note (Folder's too); the confirmation after a shuffle.
 * page_fit, a page_list cut to whole rows above the glass, shares the note's screen arithmetic. */
#include "navigation.h"

static int top_named(void *w, const char *name) {
    return w && P(w, W_PARENT) == window_manager() &&
           !tk_strcmp(widget_get_prop_str(w, "name", ""), name);
}

/* The toast: one line of 16 px Text Primary (19 in AWTK's sizes) on a #1C1C1E pill 32 px tall with
 * 12 px corners, 16 px of pill each side of the text, centred on y 252 so the page and its status
 * row stay in sight round it. */
#define PILL_Y 236
#define PILL_H 32
#define PILL_PX 19
#define PILL_PAD 16
#define PILL_R 12
#define PILL_RGB 0x1c1c1e
#define PILL_MAX 327 /* the text column to the right column */

int feedback_toast(void *top) { return top_named(top, "msginfo_dialog"); }
/* The window under OLED's toast: the window manager's last window before it. */
void *feedback_under(void *toast) {
    void *wm = window_manager(), *under = (void *)0;
    for (unsigned i = 0, n = widget_count_children(wm); i < n; ++i) {
        void *w = widget_get_child(wm, i);
        if (w == toast) break;
        if (!tk_strcmp(widget_get_type(w), "window")) under = w;
    }
    return under;
}
/* The top window, or the page under OLED's toast, which stays the page in sight. */
void *feedback_top(void) {
    void *top = window_manager_get_top_window(window_manager());
    return feedback_toast(top) ? feedback_under(top) : top;
}

/* Under the dialog's children: its background cleared once, its first line placed on the pill and
 * every other line hidden (stock's kind 1 is one word-wrapping label; V1.32's kinds a line each).
 */
void feedback_toast_paint(void *w, void *canvas) {
    if (!feedback_toast(w) || !P(canvas, CANVAS_LCD)) return;
    if (!widget_get_prop_int(w, "_pill", 0)) {
        widget_set_prop_int(w, "_pill", 1);
        widget_set_prop_int(w, "style:normal:bg_color", 0);
        widget_set_prop_int(w, "style:normal:border_color", 0);
    }
    void *line = (void *)0;
    for (unsigned i = 0, n = widget_count_children(w); i < n; ++i) {
        void *c = widget_get_child(w, i);
        if (tk_strcmp(widget_get_type(c), "label") &&
            tk_strcmp(widget_get_type(c), "hscroll_label"))
            continue;
        if (line || !widget_get_text(c) || !*widget_get_text(c))
            widget_set_visible(c, 0, 0);
        else
            line = c;
    }
    if (!line) return;
    const unsigned *s = widget_get_text(line);
    int px = PILL_PX, n = (int)wide_len(s);
    const char *face = theme_face(s, n, &px);
    canvas_set_font(canvas, face, px);
    int pw = (int)(canvas_measure_text(canvas, s, n) + 0.5f) + 2 * PILL_PAD;
    if (pw > PILL_MAX) pw = PILL_MAX;
    rect_t pill = { (375 - pw) / 2, PILL_Y, pw, PILL_H };
    if (I(line, W_Y) != PILL_Y || I(line, W_W) != pw) {
        widget_move_resize(line, pill.x, pill.y, pill.w, pill.h);
        widget_set_prop_int(line, "line_wrap", 0);
        widget_set_prop_str(line, "style:normal:font_name", face ? face : "default");
        widget_set_prop_int(line, "style:normal:font_size", px);
        widget_set_prop_str(line, "style:normal:text_align_h", "center");
        widget_set_prop_str(line, "style:normal:text_align_v", "middle");
        text_ink(line, 0xffffffffu, INK_LABEL);
    }
    unsigned fill = (unsigned)I(P(canvas, CANVAS_LCD), LCD_FILL_COLOR);
    fill_box(canvas, &pill, RGBA(flip_if(ipod_light(), PILL_RGB)), PILL_R);
    stock_canvas_set_fill_color(canvas, fill);
}

/* A click handler on the check, so the wheel's rows (navigation.c clickable) count it: the check
 * turns itself on a click, Centre's as a tap's. */
static int feedback_check_click(void *check, void *event) { return 0; }

/* The confirm pop-up as a dialog (docs/oled-design.md#dialogs-and-toasts): stock's prompt becomes
 * the status row's title, in sentence case and asking the question, and its two cells are the
 * list's first rows, Cancel then the verb that says what the other does ("Shut Down", "Delete",
 * "Reset"), never OK. Prompts not listed keep their words, and their verb is the prompt's first
 * word when it is one of verbs[]; else OK. */
static const struct {
    const char *prompt, *title, *verb;
} asks[] = {
    { "Shut Down?", "Shut down?", "Shut Down" },
    { "Restart?", "Restart?", "Restart" }, /* Power's Restart (power_rows.c) */
    { "Reset Settings Only", "Reset settings only?", "Reset" },
    { "Reset Database", "Reset the database?", "Reset" },
    { "Full Factory Reset", "Full factory reset?", "Reset" },
    { "Delete Song?", "Delete this song?", "Delete" },
    { "Delete File?", "Delete this file?", "Delete" },
    { "Scan music?", "Scan music?", "Scan" },
};
static const char *const verbs[] = {
    "Delete", "Import", "Export", "Quit", "Disconnect", "Reconnect"
};
#define CONFIRM_ROW 32
#define CONFIRM_Y 58 /* the list's top (oled-design.md Spacing) */

static int confirm_on(void *w) { return top_named(w, "confirminfo_dialog"); }

/* The dialog's first paint, before its background: its prompt into the bar and hidden, the cells
 * moved onto the rows (stock binds them by name), the second one's text the verb. Once: the first
 * cell's height says it is done. */
int feedback_confirm_dress(void *w) {
    if (!confirm_on(w)) return 0;
    void *cell[2] = { widget_lookup(w, "img_cancel", 0), widget_lookup(w, "img_enter", 0) };
    if (!cell[0] || !cell[1] || I(cell[0], W_H) == CONFIRM_ROW) return 1;
    char prompt[128] = "", word[16];
    for (unsigned i = 0, n = widget_count_children(w); i < n; ++i) {
        void *c = widget_get_child(w, i);
        if (!tk_strcmp(widget_get_type(c), "button") ||
            !tk_strcmp(widget_get_prop_str(c, "name", ""), "msg_delcheckbutton"))
            continue;
        if (!*prompt) widget_get_text_utf8(c, prompt, sizeof prompt);
        widget_set_visible(c, 0, 0);
    }
    const char *title = prompt, *verb = st.confirm_verb ? st.confirm_verb : "OK";
    for (unsigned k = 0; k < sizeof asks / sizeof *asks; ++k)
        if (!tk_strcmp(prompt, asks[k].prompt)) title = asks[k].title, verb = asks[k].verb;
    unsigned len = 0;
    while (prompt[len] && prompt[len] != ' ' && len < sizeof word - 1)
        word[len] = prompt[len], ++len;
    word[len] = 0;
    for (unsigned k = 0; !st.confirm_verb && k < sizeof verbs / sizeof *verbs; ++k)
        if (!tk_strcmp(word, verbs[k])) verb = verbs[k];
    widget_set_prop_int(w, "style:normal:bg_color", (int)RGBA(scheme()->paper));
    widget_set_prop_int(w, "style:normal:border_color", 0);
    for (int k = 0; k < 2; ++k) {
        widget_move_resize(cell[k], 0, CONFIRM_Y + k * CONFIRM_ROW, 375, CONFIRM_ROW);
        widget_set_text_utf8(cell[k], k ? verb : "Cancel");
        widget_set_prop_str(cell[k], "style:normal:text_align_h", "left");
        widget_set_prop_int(cell[k], "style:normal:margin_left", geom_val(V_TEXT_X));
        theme_font(cell[k], ROLE_TITLE, FONT_ROW);
    }
    /* Delete Song's "Also delete source file": the third row, its words a label of ours at the text
     * column (stock's check_button centres its own with its icon) that lets taps through to it, and
     * a click handler that makes it a stop for the wheel (feedback_check_click) */
    void *check = widget_lookup(w, "msg_delcheckbutton", 0);
    if (check) {
        int y = CONFIRM_Y + 2 * CONFIRM_ROW;
        char words[64];
        widget_get_text_utf8(check, words, sizeof words);
        widget_set_text_utf8(check, "");
        widget_move_resize(check, 0, y, 375, CONFIRM_ROW);
        widget_on(check, EVT_CLICK, feedback_check_click, check);
        void *label = text(w, geom_val(V_TEXT_X) - 2, y, 280, CONFIRM_ROW);
        widget_set_name(label, "_check_words");
        widget_set_text_utf8(label, words);
        widget_set_prop_str(label, "style:normal:text_align_h", "left");
        theme_font(label, ROLE_TITLE, FONT_ROW);
        widget_set_sensitive(label, 0);
    }
    if (st.bar_title) {
        widget_set_text_utf8(st.bar_title, title);
        theme_font(st.bar_title, ROLE_BAR, FONT_ROW);
        widget_invalidate_force(st.bar_title, (void *)0);
    }
    return 1;
}

/* Delete Song's third row, a switch on the right column (docs/oled-design.md Switch row): 34x20, a
 * 16 px white knob 2 px in; off the bar the Track grey or the accent, on the bar a white track with
 * the accent's knob when on, a white outline round a white knob when off. Over the selection. */
#define SWITCH_W 34
#define SWITCH_H 20
#define KNOB 16
void feedback_confirm_switch(void *w, void *canvas) {
    void *check = confirm_on(w) ? widget_lookup(w, "msg_delcheckbutton", 0) : (void *)0;
    if (!check || I(check, W_H) != CONFIRM_ROW || !P(canvas, CANVAS_LCD)) return;
    int on = widget_get_prop_int(check, "value", 0), lit = st.sel_w == w && st.sel_row == 2;
    unsigned accent = RGBA(scheme()->tones[0]), white = RGBA(0xffffff),
             fill = (unsigned)I(P(canvas, CANVAS_LCD), LCD_FILL_COLOR);
    rect_t track = { 375 - geom_val(V_RIGHT_X) - SWITCH_W,
                     I(check, W_Y) + (CONFIRM_ROW - SWITCH_H) / 2, SWITCH_W, SWITCH_H },
           knob = { track.x + (on ? SWITCH_W - 2 - KNOB : 2), track.y + 2, KNOB, KNOB };
    if (lit && !on)
        round_ring(canvas, &track, SWITCH_H / 2, white);
    else
        fill_box(canvas, &track,
                 lit  ? white
                 : on ? accent
                      : RGBA(flip_if(ipod_light(), 0x333333)),
                 SWITCH_H / 2);
    fill_box(canvas, &knob, lit && on ? accent : white, KNOB / 2);
    stock_canvas_set_fill_color(canvas, fill);
}

/* Over the dialog's background, under its rows: the status row, which the full-screen dialog
 * covers (over Quick Settings nothing else draws it). */
void feedback_confirm_bar(void *w, void *canvas) {
    void *bar = *(void *const *)system_bar;
    if (!confirm_on(w) || !bar || !P(canvas, CANVAS_LCD)) return;
    widget_paint(bar, canvas);
    st.paint_w = w;
}

/* An empty or offline page's note (docs/oled-design.md#empty-and-offline): one line of 16 px Text
 * Secondary centred in the free area, from free (a screen y: the end of any rows above it) to the
 * list's end at y 282, and an optional hint in 15 px Text Tertiary 24 px lower. Its two labels are
 * parent's, made once and written again on each call; message 0 hides them. 0 when the theme has
 * no such note (the caller keeps its own form). */
#define NOTE_END 282
#define NOTE_H 24
#define NOTE_HINT_DY 24
static int screen_y(void *w) {
    int y = 0;
    for (; w && w != window_manager(); w = P(w, W_PARENT)) y += I(w, W_Y);
    return y;
}
int feedback_below(void *w) { return w ? screen_y(w) + I(w, W_H) : 0; }
/* A page_list's view (coverflow.c) under a list_fit set: its list box cut to the whole rows that
 * end by the set's list end (y 282 on OLED), as tools/ipod.py fit_rows cuts stock's, so no row runs
 * under the bottom corners' glass. */
void page_fit(void *view) {
    void *lv = view ? P(view, W_PARENT) : (void *)0;
    int item = lv ? widget_get_prop_int(lv, "item_height", 0) : 0;
    if (!geom_val(V_LIST_FIT) || item <= 0) return;
    int room = geom_val(V_BAR) + geom_val(V_LIST_END) - screen_y(lv), h = room - room % item;
    if (h <= 0 || I(lv, W_H) <= h) return;
    widget_resize(lv, I(lv, W_W), h);
    widget_resize(view, I(view, W_W), h);
}
/* Two labels of one parent placed and inked as the note, their text left as it is: line centred
 * between free and NOTE_END, hint (if any) 24 px lower. */
int feedback_note(void *line, void *hint, int free) {
    if (!line) return 0;
    int py = screen_y(P(line, W_PARENT)), light = ipod_light();
    if (free < py) free = py;
    int mid = (free + NOTE_END) / 2 - NOTE_H / 2 - py;
    void *both[2] = { line, hint };
    for (int k = 0; k < 2 && both[k]; ++k) {
        widget_move_resize(both[k], 0, mid + k * NOTE_HINT_DY, 375, NOTE_H);
        int px = k ? 18 : 19;
        const char *face = theme_face(widget_get_text(both[k]), -1, &px);
        widget_set_prop_str(both[k], "style:normal:font_name", face ? face : "default");
        widget_set_prop_int(both[k], "style:normal:font_size", px);
        widget_set_prop_str(both[k], "style:normal:text_align_h", "center");
        text_ink(both[k],
                 RGBA(k       ? (light ? 0x6e6e6e : 0x858585)
                      : light ? 0x5c5c5c
                              : 0xababab),
                 INK_LABEL);
    }
    return 1;
}
int feedback_empty(void *parent, int free, const char *message, const char *hint) {
    if (!parent) return 0;
    void *line = widget_lookup(parent, "_empty", 0), *sub = widget_lookup(parent, "_empty_hint", 0);
    if (!line) {
        line = text(parent, 0, 0, 375, NOTE_H);
        widget_set_name(line, "_empty");
        sub = text(parent, 0, 0, 375, NOTE_H);
        widget_set_name(sub, "_empty_hint");
    }
    if (!message) hint = (const char *)0;
    widget_set_visible(line, message && *message, 0);
    widget_set_visible(sub, hint && *hint, 0);
    widget_set_text_utf8(line, message ? message : "");
    widget_set_text_utf8(sub, hint ? hint : "");
    return feedback_note(line, sub, free);
}

/* Sleep Timer's page with its switch off shows only the switch's row (navigation.c
 * ringnav_sleeptimer), which Centre turns (edit_single): the selection's bar is on it from the
 * moment the page opens, its words white as a selected row's (st.sel_accent). Under the row. */
void feedback_switch_bar(void *w, void *canvas) {
    if (!w || !P(canvas, CANVAS_LCD) || tk_strcmp(widget_get_type(w), "button") ||
        !widget_count_children(w))
        return;
    void *label = widget_get_child(w, 0), *win = window_of(w),
         *fields = win ? widget_lookup(win, "view_sleep", 1) : (void *)0;
    if (!fields || tk_strcmp(widget_get_prop_str(label, "name", ""), "_switch_row")) return;
    if (widget_get_visible(fields)) { /* on: the fields have the selection, the words their ink */
        if (st.sel_accent == w) st.sel_accent = (void *)0;
        return;
    }
    const theme_t *T = theme();
    unsigned fill = (unsigned)I(P(canvas, CANVAS_LCD), LCD_FILL_COLOR);
    rect_t bar = { T->sel_inset, 0, I(w, W_W) - 2 * T->sel_inset, I(w, W_H) };
    fill_box(canvas, &bar, RGBA(scheme()->tones[0]), (unsigned)T->sel_radius);
    stock_canvas_set_fill_color(canvas, fill);
    st.sel_accent = w;
}

/* A service's page with no link (oled-design.md Empty and offline): its name in the status row over
 * the note, "Not connected" and the setting that gives it one; Return leaves. 1 when shown. */
static void *offline_page __attribute__((section(".scratch")));
static int offline_gone(void *page, void *event) {
    if (offline_page == page) offline_page = (void *)0;
    return 0;
}
int feedback_offline(const char *title) {
    if (offline_page) return 1;
    void *page = page_open("offline_page", offline_gone, page_keyup);
    if (!page) return 0;
    offline_page = page;
    page_title(page, title, 1);
    bar_title_sync(page);
    return feedback_empty(page, 0, "Not connected", "Turn on Wi-Fi in System > Wireless");
}

/* Songs' page (allmusic_page) over an empty list, under a shuffle_row theme: the Shuffle row
 * would stand over nothing, so it hides and the page says what is missing, by the list's class
 * (g_class_type): My Fav (0xf00a), Recent (0xf007), Most Played's stock list (0xf008), Recently
 * Added (0xf009). Runs on the bar's paint; writes only when the list's emptiness changes. */
void feedback_songs(void *top, void *row) {
    if (!theme()->shuffle_row || !row || window_of(row) != top) return;
    void *list = P(p_deque_showlist, 0);
    int empty = list && !deque_size(list), was = widget_get_prop_int(top, "_empty_songs", 0);
    if (empty == was) return;
    widget_set_prop_int(top, "_empty_songs", empty);
    widget_set_visible(row, !empty, 0);
    int cls = I(g_class_type, 0);
    const char *message = cls == 0xf00a   ? "No favorites"
                          : cls == 0xf007 ? "No recent songs"
                          : cls == 0xf008 ? "No plays yet"
                          : cls == 0xf009 ? "No songs added yet"
                                          : "No songs";
    feedback_empty(top, CONFIRM_Y, empty ? message : (const char *)0,
                   cls == 0xf00a ? "Hold Play/Pause on a song to add it" : (const char *)0);
}

/* Folder's page (folder_page) over a level with nothing in it, an empty card or a folder with no
 * music: stock's page is its table alone, with no message of its own, and would be blank. It
 * says "No folders" with a hint, as OLED's note (oled-design.md Empty and offline), else as two
 * centred lines in stock's white label styles. The level is stock's p_deque_showlist, which the
 * table's rows read (folder_into). Runs on the bar's paint; writes only when the level's emptiness
 * changes. */
#define FOLDER_NOTE_Y 120
void feedback_folder(void *top) {
    if (!top || tk_strcmp(widget_get_prop_str(top, "name", ""), "folder_page")) return;
    void *list = P(p_deque_showlist, 0);
    int empty = list && !deque_size(list), was = widget_get_prop_int(top, "_empty_folder", 0);
    if (empty == was) return;
    widget_set_prop_int(top, "_empty_folder", empty);
    const char *message = empty ? "No folders" : (const char *)0, *hint = "Copy music to the SD card";
    if (feedback_empty(top, 0, message, hint)) return;
    void *line = widget_lookup(top, "_empty", 0), *sub = widget_lookup(top, "_empty_hint", 0);
    if (!line) {
        line = text(top, 0, FOLDER_NOTE_Y, 375, 30);
        widget_set_name(line, "_empty");
        sub = text(top, 0, FOLDER_NOTE_Y + 34, 375, 24);
        widget_use_style(sub, "s_scrlabel_white16c");
        widget_set_name(sub, "_empty_hint");
        widget_set_text_utf8(line, "No folders");
        widget_set_text_utf8(sub, hint);
    }
    widget_set_visible(line, empty, 0);
    widget_set_visible(sub, empty, 0);
}

/* The confirmation after a shuffle (docs/oled-design.md#confirmation): over Now Playing, the
 * accent's 56 px bold shuffle centred on y 140, what was shuffled in the Song title role centred on
 * y 200 and the queue's song count in 15 px Text Tertiary centred on y 228, under a status row that
 * names Now Playing. It goes after 1.2 s or at the first key, which it takes. */
#define CONF_MS 1200
#define CONF_GLYPH 56
static struct {
    void *page;
    unsigned timer;
} conf __attribute__((section(".scratch")));
static int conf_gone(void *page, void *event) {
    if (conf.page == page) {
        conf.page = (void *)0;
        stop_timer(&conf.timer);
    }
    return 0;
}
static int conf_due(const void *info) {
    conf.timer = 0;
    if (conf.page) window_close(conf.page);
    return 0;
}
static int conf_key(void *ctx, void *event) {
    if (conf.page && !conf.timer) return STOP; /* closing */
    stop_timer(&conf.timer);
    conf.timer = timer_add(conf_due, (void *)0, 0);
    return STOP;
}
static void *conf_line(void *page, int cy, int h, const char *s, int role, int px, unsigned ink) {
    void *label = text(page, 0, cy - h / 2 - geom_val(V_BAR), 375, h);
    widget_set_text_utf8(label, s);
    if (role >= 0) theme_font(label, role, FONT_ROW);
    if (px) widget_set_prop_int(label, "style:normal:font_size", px);
    text_ink(label, RGBA(ink), INK_LABEL);
    return label;
}
void feedback_shuffled(const char *what, int songs) {
    if (conf.page || songs <= 0) return;
    void *page = window_create(0, 0, 0, 0, 0);
    if (!page) return;
    conf.page = page;
    widget_set_name(page, "shuffle_confirm");
    widget_set_prop_int(page, "style:normal:bg_color", (int)RGBA(scheme()->paper));
    widget_on(page, EVT_DESTROY, conf_gone, page);
    widget_on(page, EVT_KEY_DOWN, conf_key, 0);
    widget_on(page, EVT_KEY_UP, conf_key, 0);
    page_title(page, "Now Playing", 1); /* the status row's, hidden here */
    void *glyph = image_create(page, (375 - CONF_GLYPH) / 2, 140 - CONF_GLYPH / 2 - geom_val(V_BAR),
                               CONF_GLYPH, CONF_GLYPH);
    image_base_set_image(glyph, theme_art("confirm_shuffle"));
    int light = ipod_light();
    conf_line(page, 200, 32, what, ROLE_NP, 0, light ? 0x000000 : 0xffffff);
    char count[24];
    int k = songs / 1000;
    if (k)
        tk_snprintf(count, sizeof count, "%d,%03d song%s", k, songs % 1000, "s");
    else
        tk_snprintf(count, sizeof count, "%d song%s", songs, songs == 1 ? "" : "s");
    conf_line(page, 228, 24, count, -1, 18, light ? 0x6e6e6e : 0x858585);
    conf.timer = timer_add(conf_due, (void *)0, CONF_MS);
}
