/* OLED's feedback forms (docs/oled-design.md#dialogs-and-toasts): stock's toast as a pill over the
 * page, and stock's confirm pop-up as a dialog page whose status row asks the question over Cancel
 * and the action's verb; an empty or offline page's note (Folder's too); the confirmation after a shuffle. */
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
#define PILL_PX TYPE(16)
#define PILL_PAD 16
#define PILL_R 12
#define PILL_RGB RGB_RAISED
#define PILL_MAX 327 /* the text column to the right column */

#define PILL_GLYPH 15 /* an icon toast's glyph (tools/art.py USB_TOAST_PX), PILL_GLYPH_GAP before its words */
#define PILL_GLYPH_GAP 8

/* The next toast's glyph, a theme image's name, while toast_icon's toast is up. */
static char pill_glyph[48] __attribute__((section(".scratch")));

int feedback_toast(void *top) { return top_named(top, "msginfo_dialog"); }

/* A toast with a glyph before its words (docs/oled-design.md#dialogs-and-toasts): glyph a theme image's
 * whole name. */
void toast_icon(const char *glyph, const char *text) {
    tk_snprintf(pill_glyph, sizeof pill_glyph, "%s", glyph ? glyph : "");
    toast(text);
    pill_glyph[0] = 0;
}
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
    int glyph = pill_glyph[0] ? PILL_GLYPH + PILL_GLYPH_GAP : 0;
    int pw = (int)(canvas_measure_text(canvas, s, n) + 0.5f) + 2 * PILL_PAD + glyph;
    if (pw > PILL_MAX) pw = PILL_MAX;
    rect_t pill = { (375 - pw) / 2, PILL_Y, pw, PILL_H };
    if (I(line, W_Y) != PILL_Y || I(line, W_W) != pw - glyph) {
        widget_move_resize(line, pill.x + glyph, pill.y, pill.w - glyph, pill.h);
        widget_set_prop_int(line, "line_wrap", 0);
        widget_set_prop_str(line, "style:normal:font_name", face ? face : "default");
        widget_set_prop_int(line, "style:normal:font_size", px);
        widget_set_prop_str(line, "style:normal:text_align_h", "center");
        widget_set_prop_str(line, "style:normal:text_align_v", "middle");
        text_ink(line, 0xffffffffu, INK_LABEL);
    }
    unsigned fill = (unsigned)I(P(canvas, CANVAS_LCD), LCD_FILL_COLOR);
    fill_box(canvas, &pill, RGBA(flip_if(ipod_light(), PILL_RGB)), PILL_R);
    if (glyph) paint_art(w, canvas, pill_glyph, pill.x + PILL_PAD, PILL_Y + (PILL_H - PILL_GLYPH) / 2);
    stock_canvas_set_fill_color(canvas, fill);
}

/* A click handler on the check, so the wheel's rows (navigation.c clickable) count it: the check
 * turns itself on a click, Centre's as a tap's. */
static int feedback_check_click(void *check, void *event) { return 0; }

/* The confirm pop-up as a dialog (docs/oled-design.md#dialogs-and-toasts): stock's prompt becomes
 * the status row's title, in sentence case and asking the question, and its two cells are the
 * list's first rows, Cancel then the verb that says what the other does ("Shut Down", "Delete",
 * "Reset"), never OK. Prompts not listed keep their words, and their verb is the prompt's first
 * word when it is one of verbs[]; else OK. A prompt with a note says under its rows what the
 * answer leaves alone, as iOS's reset alert does, in the empty note's form (feedback_empty). */
static const struct {
    const char *prompt, *title, *verb, *note;
} asks[] = {
    { "Shut Down?", "Shut down?", "Shut Down", 0 },
    { "Restart?", "Restart?", "Restart", 0 }, /* Power's Restart (power_rows.c) */
    { "Reset Settings Only", "Reset settings?", "Reset", "Music, playlists and ratings are kept" },
    { "Reset Database", "Reset the database?", "Reset", 0 },
    { "Full Factory Reset", "Full factory reset?", "Reset", 0 },
    { "Delete Song?", "Delete this song?", "Delete", 0 },
    { "Delete File?", "Delete this file?", "Delete", 0 },
    { "Scan music?", "Scan music?", "Scan", 0 },
};
static const char *const verbs[] = {
    "Delete", "Import", "Export", "Quit", "Disconnect", "Reconnect"
};
#define CONFIRM_ROW 32
#define CONFIRM_Y 58 /* the list's top (oled-design.md Spacing) */
/* The footer (feedback_confirm_foot): 13 px Text Tertiary lines 8 px under the rows, each line's
 * own label, at the rows' text column */
#define FOOT_GAP 8
#define FOOT_LINE 17
#define FOOT_PX TYPE(13) /* Inter 13 px, AWTK's size (13 x 1.21) */

/* The next confirm's footer, its lines split at '\n': why it asks, where the setting lives. */
static const char *confirm_foot __attribute__((section(".scratch")));
void feedback_confirm_foot(const char *foot) { confirm_foot = foot; }

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
    const char *title = prompt, *verb = st.confirm_verb ? st.confirm_verb : "OK", *note = 0;
    for (unsigned k = 0; k < sizeof asks / sizeof *asks; ++k)
        if (!tk_strcmp(prompt, asks[k].prompt))
            title = asks[k].title, verb = asks[k].verb, note = asks[k].note;
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
    if (note) feedback_empty(w, CONFIRM_Y + 2 * CONFIRM_ROW, note, (const char *)0);
    /* the footer under the answers (Streaming Songs' asks, library_settings.c) */
    int k = 0;
    for (const char *line = confirm_foot; line && *line; ++k) {
        char words[96];
        unsigned n = 0;
        while (line[n] && line[n] != '\n') ++n;
        tk_snprintf(words, sizeof words, "%.*s", (int)n, line);
        int px = FOOT_PX;
        void *label = text(w, geom_val(V_TEXT_X) - 2,
                           CONFIRM_Y + (2 + !!check) * CONFIRM_ROW + FOOT_GAP + k * FOOT_LINE,
                           375 - 2 * geom_val(V_TEXT_X), FOOT_LINE);
        widget_set_name(label, "_foot");
        widget_set_text_utf8(label, words);
        widget_set_prop_int(label, "loop", 0);
        const char *face = theme_face(widget_get_text(label), -1, &px);
        widget_set_prop_str(label, "style:normal:font_name", face ? face : "default");
        widget_set_prop_int(label, "style:normal:font_size", px);
        widget_set_prop_str(label, "style:normal:text_align_h", "left");
        text_ink(label, RGBA(tok(TOK_TERTIARY)), INK_LABEL);
        widget_set_sensitive(label, 0);
        line += n + (line[n] == '\n');
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
                      : RGBA(flip_if(ipod_light(), RGB_TRACK)),
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

/* An overlay card's scrim (Buttons Lock's card, a letter strip's Return held): over the top window
 * and the status bar, black at argb's alpha, so the whole screen dims. 1 on the top window, where
 * the card goes, its y on the screen in *dy; 0 on the bar or any other widget. From ringnav_paint,
 * after stock painted w. */
int overlay_scrim(void *w, void *canvas, unsigned argb, int *dy) {
    void *lcd = w && canvas ? P(canvas, CANVAS_LCD) : (void *)0, *bar = *(void *const *)system_bar,
         *top = window_manager_get_top_window(window_manager());
    if (!lcd || (w != bar && w != top)) return 0;
    unsigned fill = (unsigned)I(lcd, LCD_FILL_COLOR);
    stock_canvas_set_fill_color(canvas, argb);
    canvas_fill_rect(canvas, 0, 0, I(w, W_W), I(w, W_H));
    stock_canvas_set_fill_color(canvas, fill);
    *dy = I(w, W_Y);
    return w != bar;
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
        text_ink(both[k], RGBA(k ? (tok(TOK_TERTIARY)) : tok(TOK_SECONDARY)), INK_LABEL);
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

/* The empty state (docs/oled-design.md#empty-and-offline), the one form for every page with nothing
 * to show: three stacked tiles (ES_TILE in front, two ES_SIDE ones ES_SIDE_DX either side behind
 * it, faint) with the page's glyph (tools/art.py EMPTY_GLYPHS, ES_GLYPH px) on the front one, a
 * title in Inter Medium 20, a hint in 15 px Text Secondary and, when the page has one, one action
 * row 24 px under it, already selected; the whole stack centred between free_top (or the status
 * row's foot) and the screen's. A view over the page holds it (ES_NAME, its words and top in its
 * props, painted by empty_state_paint; insensitive, so rows above it keep their taps), and the row
 * is a one-row list (ES_ROW_NAME) the wheel and Centre reach as any. A page drawn by its own
 * painter (Search's sheet) paints the stack itself (empty_paint, at empty_top).
 */
#define ES_NAME "_empty_state"
#define ES_ROW_NAME "_empty_row"
#define ES_TOP 40 /* the status row's foot, as the design measures it */
#define ES_END 320
#define ES_TILE 58
#define ES_SIDE 48
#define ES_SIDE_DX 30
#define ES_TILE_R 9
#define ES_GLYPH 28
#define ES_TITLE_GAP 18 /* the tiles to the title's band */
#define ES_TITLE_H 24
#define ES_HINT_H 20
#define ES_ROW_GAP 24
#define ES_ROW_H 32
#define ES_TITLE_PX TYPE(20) /* Inter Medium 20, AWTK's size */
#define ES_HINT_PX TYPE(15)  /* Inter 15 */
#define ES_TEXT (ES_TILE + ES_TITLE_GAP + ES_TITLE_H + ES_HINT_H)
/* The stack's top, centred between free_top (the status row's foot at least) and free_end. */
int empty_top(int free_top, int free_end, int row) {
    int stack = ES_TEXT + (row ? ES_ROW_GAP + ES_ROW_H : 0),
        from = free_top > ES_TOP ? free_top : ES_TOP;
    return from + (free_end - from - stack) / 2;
}
/* The action row's screen y, for a page whose own widget is the row (Songs' Scan Music Now). */
int empty_row_y(int free_top) { return empty_top(free_top, ES_END, 1) + ES_TEXT + ES_ROW_GAP; }
/* The empty state in page, made once and written again on each call: e 0 hides it (0). An e with a
 * row but no on_row keeps the row's place in the stack for the caller's own (empty_row_y). The row
 * list is made with the view; a later call shows or hides it but keeps its words and click. */
int empty_state(void *page, int free_top, const empty_t *e, int (*on_row)(void *ctx, void *event)) {
    if (!page) return 0;
    void *art = widget_lookup(page, ES_NAME, 0), *row = widget_lookup(page, ES_ROW_NAME, 0);
    if (!e) {
        if (art) widget_set_visible(art, 0, 0);
        if (row) widget_set_visible(row, 0, 0);
        return 0;
    }
    int y = screen_y(page), top = empty_top(free_top, ES_END, e->row != 0);
    if (!art) {
        art = widget_factory_create_widget(widget_factory(), "view", page, 0, 0, 375, I(page, W_H));
        widget_set_name(art, ES_NAME);
        widget_set_sensitive(art, 0);
    }
    widget_set_prop_str(art, "_es_glyph", e->glyph ? e->glyph : "");
    widget_set_prop_str(art, "_es_title", e->title ? e->title : "");
    widget_set_prop_str(art, "_es_hint", e->hint ? e->hint : "");
    widget_set_prop_int(art, "_es_top", top);
    widget_set_visible(art, 1, 0);
    widget_invalidate_force(art, 0);
    if (row)
        widget_set_visible(row, e->row && on_row, 0);
    else if (e->row && on_row) {
        void *view = rows_view(page, top + ES_TEXT + ES_ROW_GAP - y, 375, ES_ROW_H, 1, page_h(48));
        widget_set_name(P(view, W_PARENT), ES_ROW_NAME);
        page_row_detail(view, 0, e->row, 0, on_row);
        ringnav_select(view, 0, 1); /* already selected, as the design has it */
    }
    return 1;
}
/* Whether page, or a view in it, shows the empty state. */
int empty_shown(void *page) {
    void *art = page ? widget_lookup(page, ES_NAME, 1) : (void *)0;
    return art && widget_get_visible(art);
}
/* The stack painted in w at top (a y in w). */
void empty_paint(void *w, void *canvas, const empty_t *e, int top) {
    int light = ipod_light();
    unsigned fill = (unsigned)I(P(canvas, CANVAS_LCD), LCD_FILL_COLOR);
    static const signed char dx[] = { -ES_SIDE_DX, ES_SIDE_DX, 0 };
    for (int k = 0; k < 3; ++k) { /* the two behind at about a quarter's strength */
        int s = k < 2 ? ES_SIDE : ES_TILE;
        rect_t r = { 375 / 2 + dx[k] - s / 2, top + ES_TILE / 2 - s / 2, s, s };
        fill_box(canvas, &r, RGBA(k < 2 ? flip_if(light, 0x0a0a0b) : tok(TOK_RAISED_2)), ES_TILE_R);
        round_ring(canvas, &r, ES_TILE_R, RGBA(flip_if(light, k < 2 ? 0x111112 : 0x48484c)));
    }
    stock_canvas_set_fill_color(canvas, fill);
    char art[48];
    tk_snprintf(art, sizeof art, "empty_%s", e->glyph ? e->glyph : "");
    paint_art(w, canvas, theme_art(art), 375 / 2 - ES_GLYPH / 2, top + ES_TILE / 2 - ES_GLYPH / 2);
    int ty = top + ES_TILE + ES_TITLE_GAP;
    ar_text(canvas, e->title ? e->title : "", 1, ES_TITLE_PX, flip_if(light, 0xffffff), 0, 375 / 2,
            ty + ES_TITLE_H / 2 - 1, 375 - 48, 1);
    ar_text(canvas, e->hint ? e->hint : "", 0, ES_HINT_PX, tok(TOK_SECONDARY), 0, 375 / 2,
            ty + ES_TITLE_H + ES_HINT_H / 2, 375 - 48, 1);
}
void empty_state_paint(void *w, void *canvas) {
    if (!w || !P(canvas, CANVAS_LCD) || tk_strcmp(widget_get_prop_str(w, "name", ""), ES_NAME))
        return;
    empty_t e = { widget_get_prop_str(w, "_es_glyph", ""), widget_get_prop_str(w, "_es_title", ""),
                  widget_get_prop_str(w, "_es_hint", ""), 0 };
    empty_paint(w, canvas, &e, widget_get_prop_int(w, "_es_top", ES_TOP) - screen_y(w));
}
/* A list page with nothing listed (page_list's, no rows): the empty state in body, the page's name
 * hidden on its first line for the status row (navigation.c page_caption). Its list view. */
void *empty_list(void *page, void *body, const char *name, const empty_t *e,
                 int (*on_row)(void *ctx, void *event)) {
    void *view = page_list(page, body, 0, name, 0, 48);
    widget_set_visible(widget_get_child(body, 0), 0, 0);
    empty_state(body, 0, e, on_row);
    bar_title_sync(page); /* after: Cover Flow's title follows its empty state (coverflow_bar_title) */
    return view;
}
/* No card in the slot (stock's mount marks it with /tmp/mmc_add): the Library's empty pages say
 * so, with no row (oled-design.md Empty and offline: Library). 1 when e was rewritten. */
int empty_card(empty_t *e) {
    if (!access("/tmp/mmc_add", 0)) return 0;
    *e = (empty_t){ "sd-card", "No SD Card", "Insert an SD card with music", 0 };
    return 1;
}
/* A page of its own holding only the empty state, its status row naming it (Albums, Artists,
 * Composers and Genres with nothing to list; a streaming service with no link): Return leaves. One
 * at a time; the page, or 0. */
static void *empty_pg __attribute__((section(".scratch")));
static int empty_gone(void *page, void *event) {
    if (empty_pg == page) empty_pg = (void *)0;
    return 0;
}
void *empty_open(const char *window, const char *name, const empty_t *e,
                 int (*on_row)(void *ctx, void *event)) {
    if (empty_pg) return empty_pg;
    void *page = page_open(window, empty_gone, page_keyup);
    if (!page) return (void *)0;
    empty_pg = page;
    empty_list(page, page, name, e, on_row);
    return page;
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

/* A streaming service's page with no link (oled-design.md Empty and offline: Streaming): the empty
 * state on a page of its own, Turn on Wi-Fi opening Settings > Wi-Fi (whose open wakes Smart
 * Wi-Fi's radio, smart_wifi.c ringnav_wifi_page). 1 when shown. */
static int offline_wifi(void *ctx, void *event) {
    navigator_to("systemset/wifi_page");
    return 0;
}
int feedback_offline(void) {
    static const empty_t e = { "cloud-slash", "Not Connected", "Turn on Wi-Fi to stream",
                               "Turn on Wi-Fi" };
    return empty_open("empty_page", "Streaming", &e, offline_wifi) != (void *)0;
}

/* Songs over an empty library: stock's own panel (allmusic_page's "view": a picture, a line and a
 * Scan Music Now pill) when it shows, else 0. Its button, btn_upmusic, is what Centre presses
 * (navigation.c) and a tap clicks. */
static void *scan_button(void *top) {
    void *view = widget_lookup(top, "view", 0);
    void *btn = view ? widget_lookup(view, "btn_upmusic", 0) : (void *)0;
    return btn && widget_get_visible(view) ? btn : (void *)0;
}

/* Stock's panel as the empty state's one selected row (at screen y), Scan Music Now: the button
 * itself at the text column with no fill of its own (feedback_scan_bar paints the selection's bar
 * under it); the picture and the line go, as the empty state says it. */
#define SCAN_ROW_H 32
static void scan_row(void *btn, int y) {
    static const char *const fills[] = { "style:normal:bg_color", "style:pressed:bg_color",
                                         "style:over:bg_color", "style:focused:bg_color" };
    void *view = P(btn, W_PARENT);
    for (unsigned i = 0, n = widget_count_children(view); i < n; ++i) {
        void *c = widget_get_child(view, i);
        if (c != btn) widget_set_visible(c, 0, 0);
    }
    widget_move_resize(view, I(view, W_X), y - screen_y(P(view, W_PARENT)), I(view, W_W),
                       I(view, W_H));
    widget_move_resize(btn, 0, 0, I(view, W_W), SCAN_ROW_H);
    for (unsigned i = 0; i < sizeof fills / sizeof *fills; ++i) widget_set_prop_int(btn, fills[i], 0);
    widget_set_prop_int(btn, "style:normal:border_color", 0);
    widget_set_prop_str(btn, "style:normal:text_align_h", "left");
    widget_set_prop_int(btn, "style:normal:margin_left", geom_val(V_TEXT_X) + 2); /* a label's 2 px margin */
    widget_set_text_utf8(btn, "Scan Music Now");
    theme_font(btn, ROLE_TITLE, FONT_ROW);
    widget_set_prop_int(btn, "_scan_row", 1);
}

/* The Scan Music Now row's bar: the selection's, as feedback_switch_bar's, under its words. */
void feedback_scan_bar(void *w, void *canvas) {
    if (!w || !P(canvas, CANVAS_LCD) || !widget_get_prop_int(w, "_scan_row", 0) ||
        tk_strcmp(widget_get_prop_str(w, "name", ""), "btn_upmusic"))
        return;
    const theme_t *T = theme();
    unsigned fill = (unsigned)I(P(canvas, CANVAS_LCD), LCD_FILL_COLOR);
    rect_t bar = { T->sel_inset, 0, I(w, W_W) - 2 * T->sel_inset, I(w, W_H) };
    fill_box(canvas, &bar, RGBA(scheme()->tones[0]), (unsigned)T->sel_radius);
    stock_canvas_set_fill_color(canvas, fill);
    st.sel_accent = w;
}

/* Songs' page (allmusic_page) over an empty list, under a shuffle_row theme: the Shuffle row
 * would stand over nothing, so it hides and the page shows the empty state, by the list's class
 * (g_class_type): My Fav (0xf00a), Recent (0xf007), Most Played's stock list (0xf008), Recently
 * Added (0xf009), else the library's songs. Over an empty library stock shows its own panel
 * instead of the list: its button becomes the empty state's Scan Music Now row (scan_row), or,
 * with no card in, the panel goes (empty_card). Runs on the bar's paint; writes only when the
 * list's emptiness, or the panel's, changes. */
void feedback_songs(void *top, void *row) {
    if (!theme()->shuffle_row || !row || window_of(row) != top) return;
    void *list = P(p_deque_showlist, 0);
    void *scan = list && !deque_size(list) ? scan_button(top) : (void *)0;
    int empty = (list && !deque_size(list)) + !!scan, was = widget_get_prop_int(top, "_empty_songs", 0);
    if (empty == was) return;
    widget_set_prop_int(top, "_empty_songs", empty);
    widget_set_visible(row, !empty, 0);
    if (!empty) {
        empty_state(top, 0, (const empty_t *)0, 0);
        return;
    }
    int cls = scan ? 0 : I(g_class_type, 0); /* stock's panel stands for the whole library */
    empty_t e =
        cls == 0xf00a
            ? (empty_t){ "heart", "No Favorites", "Hold Play/Pause on a song to add it", 0 }
        : cls == 0xf007 ? (empty_t){ "clock-counter-clockwise", "Nothing Played Yet",
                                     "Songs you play show up here", 0 }
        : cls == 0xf008
            ? (empty_t){ "chart-bar", "No Plays Yet", "Your most played songs show up here", 0 }
        : cls == 0xf009
            ? (empty_t){ "sparkle", "No Songs Added Yet", "New music on the card shows up here", 0 }
            : (empty_t){ "music-notes", "No Songs", "Copy music to the SD card",
                         scan ? "Scan Music Now" : 0 };
    int card = cls < 0xf007 || cls > 0xf00a ? empty_card(&e) : 0; /* the library's songs */
    if (scan && card)
        widget_set_visible(P(scan, W_PARENT), 0, 0);
    else if (scan)
        scan_row(scan, empty_row_y(0));
    empty_state(top, 0, &e, 0);
}

/* Folder's page (folder_page) over a level with nothing in it, an empty card or a folder with no
 * music: stock's page is its table alone, with no message of its own, and would be blank. It shows
 * the empty state, "No Folders", or "No SD Card" with none in (empty_card). The level is stock's
 * p_deque_showlist, which the table's rows read (folder_into). Runs on the bar's paint; writes only
 * when the level's emptiness changes. */
void feedback_folder(void *top) {
    if (!top || tk_strcmp(widget_get_prop_str(top, "name", ""), "folder_page")) return;
    void *list = P(p_deque_showlist, 0);
    int empty = list && !deque_size(list), was = widget_get_prop_int(top, "_empty_folder", 0);
    if (empty == was) return;
    widget_set_prop_int(top, "_empty_folder", empty);
    empty_t e = { "folder", "No Folders", "Copy music to the SD card", 0 };
    if (empty) empty_card(&e);
    empty_state(top, 0, empty ? &e : (const empty_t *)0, 0);
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
    conf_line(page, 200, 32, what, ROLE_NP, 0, tok(TOK_PRIMARY));
    char count[24];
    int k = songs / 1000;
    if (k)
        tk_snprintf(count, sizeof count, "%d,%03d song%s", k, songs % 1000, "s");
    else
        tk_snprintf(count, sizeof count, "%d song%s", songs, songs == 1 ? "" : "s");
    conf_line(page, 228, 24, count, -1, 18, tok(TOK_TERTIARY));
    conf.timer = timer_add(conf_due, (void *)0, CONF_MS);
}

/* The confirmation card (docs/oled-design.md#confirmation-card): stock's sortselect_dialog, as the
 * inset modal's pickers use it (menu_modal.c modal_list), its list the card's answers and the rest
 * painted here. The scrim dims the page and the status row (confirm_bar_dim); the card is
 * CARD_W wide at CARD_X, its stack centred on CARD_CY and kept within CARD_TOP..CARD_FOOT: 18 px
 * of padding, the title (Inter Medium 20), the body (15 px Text Secondary, 19 px lines, 4 px under
 * the title), the code (Inter Medium 30), then 14 px down the switch row and the answers, 32 px
 * each, a destructive answer 12 px after the rest, and 8 px of padding. The answers' bar is 8 px in
 * from the card's sides; a destructive one is red (Negative), its bar too when lit. */
#define CARD_X 42
#define CARD_W 291
#define CARD_R 16
#define CARD_CY 168
#define CARD_TOP 44
#define CARD_FOOT 302
#define CARD_PAD_TOP 18
#define CARD_PAD_FOOT 8
#define CARD_IN 20 /* the text column, in from each side */
#define CARD_TITLE_H 24
#define CARD_BODY_GAP 4
#define CARD_BODY_LINE 19
#define CARD_CODE_GAP 6
#define CARD_CODE_H 40
#define CARD_ROWS_GAP 14
#define CARD_ROW 32
#define CARD_DEST_GAP 12
#define CARD_BAR_IN 8
#define CARD_SCRIM 0x80000000u /* the inset modal's dim */
#define CARD_TIMER_MS 100      /* the timeout's seconds and bar */
#define CARD_COUNT_PX TYPE(15)

static struct {
    void *dialog, *view;
    confirm_t c;
    char title[96], body[2][64], code[16];
    int lines, toggle_on, top, rows_y, h, chosen;
    unsigned timer, answer_timer, until;
} cf __attribute__((section(".scratch")));

int confirm_up(void) { return cf.dialog != (void *)0; }
static int card_items(void) { return cf.c.n_rows + !!cf.c.toggle; }
static int card_destructive(int row) {
    return cf.c.kind == CONFIRM_DESTRUCTIVE && cf.c.n_rows > 1 && row == cf.c.n_rows - 1;
}

/* The card's height and top, and where its switch row and answers start (screen y). */
static void card_layout(void) {
    int h = CARD_PAD_TOP + CARD_TITLE_H;
    if (cf.lines) h += CARD_BODY_GAP + cf.lines * CARD_BODY_LINE;
    if (cf.code[0]) h += CARD_CODE_GAP + CARD_CODE_H;
    int rows = h + CARD_ROWS_GAP;
    h = rows + card_items() * CARD_ROW + (card_destructive(cf.c.n_rows - 1) ? CARD_DEST_GAP : 0) +
        CARD_PAD_FOOT;
    int top = CARD_CY - h / 2;
    if (top + h > CARD_FOOT) top = CARD_FOOT - h;
    if (top < CARD_TOP) top = CARD_TOP;
    cf.top = top, cf.h = h, cf.rows_y = top + rows;
}

void confirm_close(void) {
    void *d = cf.dialog;
    cf.dialog = cf.view = (void *)0;
    if (cf.timer) timer_remove(cf.timer), cf.timer = 0;
    if (d) window_close(d);
}

static int card_answer(const void *unused) {
    cf.answer_timer = 0;
    confirm_t c = cf.c;
    int row = cf.chosen, on = cf.toggle_on;
    confirm_close();
    if (c.answer) c.answer(c.ctx, row, on);
    return 0;
}
/* Answers row once, from the next pass: the dialog closes before the caller's answer runs. */
static void card_choose(int row) {
    if (!cf.dialog || cf.answer_timer) return;
    cf.chosen = row;
    cf.answer_timer = timer_add(card_answer, (void *)0, 0);
}
static int card_back_row(void) { return cf.c.back >= 0 ? cf.c.back : 0; }

static int card_click(void *ctx, void *event) {
    int k = (int)(long)ctx;
    if (k < 0) { /* the switch row */
        cf.toggle_on = !cf.toggle_on;
        if (cf.dialog) widget_invalidate_force(cf.dialog, (void *)0);
    } else
        card_choose(k);
    return 0;
}
/* Return answers the back row; the music's keys go nowhere while the card asks. */
static int card_key(void *dialog, void *event) {
    unsigned key = (unsigned)I(event, EVENT_KEY);
    if (key == KEY_PLAY || key == KEY_PREV || key == KEY_NEXT) return STOP;
    if (I(event, EVENT_TYPE) != EVT_KEY_UP || key != KEY_RETURN) return 0;
    card_choose(card_back_row());
    return STOP;
}
static int card_gone(void *dialog, void *event) {
    if (cf.dialog == dialog) {
        cf.dialog = cf.view = (void *)0;
        if (cf.timer) timer_remove(cf.timer), cf.timer = 0;
    }
    return 0;
}
/* The timeout's tick: a repaint for its seconds and bar, the answer at the end. */
static int card_tick(const void *unused) {
    if (!cf.dialog) return cf.timer = 0, 0;
    if ((int)(cf.until - (unsigned)time_now_ms()) <= 0) {
        cf.timer = 0;
        card_choose(cf.c.timeout_row);
        return 0;
    }
    widget_invalidate_force(cf.dialog, (void *)0);
    return 1;
}

int confirm(const confirm_t *c) {
    void *wm = window_manager(), *page = window_manager_get_top_window(wm);
    if (cf.dialog || !c || c->n_rows <= 0) return 0;
    navigator_to("dialog/sortselect_dialog");
    void *dialog = window_manager_get_top_window(wm);
    if (dialog == page) return 0;
    if (tk_strcmp(widget_get_prop_str(dialog, "name", ""), "sortselect_dialog")) {
        window_close(dialog);
        return 0;
    }
    memset(&cf, 0, sizeof cf);
    cf.c = *c;
    cf.toggle_on = c->toggle_on;
    tk_snprintf(cf.title, sizeof cf.title, c->title ? c->title : "", c->name ? c->name : "");
    for (const char *s = c->body; s && *s && cf.lines < 2; ++cf.lines) {
        unsigned n = 0;
        while (s[n] && s[n] != '\n') ++n;
        tk_snprintf(cf.body[cf.lines], sizeof cf.body[0], "%.*s", (int)n, s);
        s += n + (s[n] == '\n');
    }
    tk_snprintf(cf.code, sizeof cf.code, "%s", c->code ? c->code : "");
    card_layout();
    void *back = widget_lookup(dialog, "img_return", 1),
         *list = widget_lookup(dialog, "list_view", 1),
         *view = widget_lookup(dialog, "scroll_view", 1),
         *nav = widget_lookup(dialog, "view_navbar", 1);
    widget_off_by_func(dialog, EVT_KEY_UP, (void *)SORTSELECT_KEYUP, dialog);
    widget_off_by_func(back, EVT_CLICK, (void *)SORTSELECT_CLOSE, dialog);
    widget_on(dialog, EVT_KEY_UP, card_key, dialog);
    widget_on(dialog, EVT_KEY_DOWN, card_key, dialog);
    widget_on(dialog, EVT_DESTROY, card_gone, dialog);
    if (nav) widget_set_visible(nav, 0, 0);
    widget_set_prop_str(dialog, "highlight", "");
    widget_set_prop_int(dialog, "style:normal:bg_color", 0);
    widget_set_prop_int(dialog, "style:normal:border_color", 0);
    widget_destroy_children(view);
    int out = theme()->sel_inset - CARD_BAR_IN, w = CARD_W + 2 * out, y = 0;
    for (int k = c->toggle ? -1 : 0; k < c->n_rows; ++k) {
        if (card_destructive(k)) {
            void *gap = label_create(view, 0, y, w, CARD_DEST_GAP);
            widget_set_text_utf8(gap, "");
            y += CARD_DEST_GAP;
        }
        void *item = list_item_create(view, 0, y, w, CARD_ROW);
        widget_use_style(item, "s_listitem_black");
        widget_set_prop_int(item, "style:normal:bg_color", 0);
        widget_on(item, EVT_CLICK, card_click, (void *)(long)k);
        y += CARD_ROW;
    }
    if (list) {
        I(list, ROW_HEIGHT) = 0;
        I(list, LIST_DEFAULT_ITEM_HEIGHT) = 0;
        widget_set_prop_int(list, "style:normal:bg_color", 0);
        widget_move_resize(list, CARD_X - out, cf.rows_y - I(dialog, W_Y), w, y);
        widget_set_prop_int(view, "virtual_h", y);
        widget_set_prop_str(view, "self_layout", "default(x=0,y=0,w=100%,h=100%)");
    }
    cf.dialog = dialog, cf.view = view;
    int first = c->kind == CONFIRM_NEUTRAL ? c->n_rows - 1 : 0;
    st.touch_mode = 0;
    ringnav_select(view, first + !!c->toggle, card_items());
    if (c->timeout_ms > 0) {
        cf.until = (unsigned)time_now_ms() + (unsigned)c->timeout_ms;
        cf.timer = timer_add(card_tick, (void *)0, CARD_TIMER_MS);
    }
    return 1;
}

/* The status row under the card's scrim, from ringnav_paint after stock painted it. */
void confirm_bar_dim(void *w, void *canvas) {
    void *bar = *(void *const *)system_bar, *lcd = canvas ? P(canvas, CANVAS_LCD) : (void *)0;
    if (!cf.dialog || w != bar || !lcd) return;
    unsigned fill = (unsigned)I(lcd, LCD_FILL_COLOR);
    stock_canvas_set_fill_color(canvas, CARD_SCRIM);
    canvas_fill_rect(canvas, 0, 0, I(w, W_W), I(w, W_H));
    stock_canvas_set_fill_color(canvas, fill);
}

/* Under the dialog's rows: the scrim over the page, the card, its title, body, code and the
 * timeout's bar along its foot. */
void confirm_paint(void *w, void *canvas) {
    void *lcd = canvas ? P(canvas, CANVAS_LCD) : (void *)0;
    if (!cf.dialog || w != cf.dialog || !lcd) return;
    unsigned fill = (unsigned)I(lcd, LCD_FILL_COLOR), raw = ui_ink_raw(1);
    int dy = I(w, W_Y), top = cf.top - dy, cx = CARD_X + CARD_W / 2, tw = CARD_W - 2 * CARD_IN;
    stock_canvas_set_fill_color(canvas, CARD_SCRIM);
    canvas_fill_rect(canvas, 0, 0, I(w, W_W), I(w, W_H));
    rect_t card = { CARD_X, top, CARD_W, cf.h };
    fill_box(canvas, &card, RGBA(ipod_light() ? 0xffffff : RGB_RAISED), CARD_R);
    int y = top + CARD_PAD_TOP + CARD_TITLE_H / 2;
    ar_text(canvas, cf.title, 1, TYPE(20), tok(TOK_PRIMARY), 1, cx, y, tw, 1);
    y += CARD_TITLE_H / 2;
    for (int i = 0; i < cf.lines; ++i, y += CARD_BODY_LINE)
        ar_text(canvas, cf.body[i], 0, TYPE(15), tok(TOK_SECONDARY), 1, cx,
                y + (i ? 0 : CARD_BODY_GAP) + CARD_BODY_LINE / 2, tw, 1);
    if (cf.lines) y += CARD_BODY_GAP;
    if (cf.code[0])
        ar_text(canvas, cf.code, 1, TYPE(30), tok(TOK_PRIMARY), 1, cx,
                y + CARD_CODE_GAP + CARD_CODE_H / 2, tw, 1);
    if (cf.c.timeout_ms > 0) { /* 2 px along the foot, emptying toward the answer it gives */
        int left = (int)(cf.until - (unsigned)time_now_ms());
        if (left < 0) left = 0;
        rect_t track = { CARD_X + CARD_IN, top + cf.h - CARD_PAD_FOOT / 2 - 1, tw, 2 }, bar = track;
        bar.w = (int)((long long)tw * left / cf.c.timeout_ms);
        fill_box(canvas, &track, RGBA(tok(TOK_OFF)), 0);
        if (bar.w > 0) fill_box(canvas, &bar, RGBA(tok(TOK_TERTIARY)), 0);
    }
    stock_canvas_set_fill_color(canvas, fill);
    ui_ink_raw(raw);
}

/* Over the answers' list after the selection's bar: each row's words centred (a destructive one
 * red, its lit bar red), the switch row's label and switch, the timeout's seconds. */
void confirm_rows_paint(void *w, void *canvas) {
    void *lcd = canvas ? P(canvas, CANVAS_LCD) : (void *)0;
    if (!cf.dialog || w != cf.view || !lcd) return;
    unsigned fill = (unsigned)I(lcd, LCD_FILL_COLOR), raw = ui_ink_raw(1), red = RGB_NEGATIVE,
             primary = tok(TOK_PRIMARY);
    int out = theme()->sel_inset - CARD_BAR_IN, cx = out + CARD_W / 2, k = cf.c.toggle ? -1 : 0;
    for (unsigned i = 0, n = widget_count_children(w); i < n; ++i) {
        void *item = widget_get_child(w, i);
        if (tk_strcmp(widget_get_type(item), "list_item")) continue;
        int y = I(item, W_Y) + CARD_ROW / 2, lit = ringnav_row_selected(item) && !st.touch_mode;
        if (k < 0) {
            ar_text(canvas, cf.c.toggle, 0, TYPE(20), lit ? 0xffffff : primary, 1, out + CARD_IN, y,
                    CARD_W - 2 * CARD_IN - 44, 2);
            switch_mark(canvas, out + CARD_W - CARD_IN, y, cf.toggle_on, lit);
        } else {
            if (lit && card_destructive(k)) {
                rect_t bar = { out + CARD_BAR_IN, I(item, W_Y), CARD_W - 2 * CARD_BAR_IN,
                               CARD_ROW };
                fill_box(canvas, &bar, RGBA(red), (unsigned)theme()->sel_radius);
            }
            ar_text(canvas, cf.c.rows[k], 0, TYPE(20),
                    lit                   ? 0xffffff
                    : card_destructive(k) ? red
                                          : primary,
                    1, cx, y, CARD_W - 2 * CARD_IN, 1);
            if (cf.c.timeout_ms > 0 && k == cf.c.timeout_row) {
                char s[16];
                int left = (int)(cf.until - (unsigned)time_now_ms());
                tk_snprintf(s, sizeof s, "%d s", left > 0 ? (left + 999) / 1000 : 0);
                ar_text(canvas, s, 0, CARD_COUNT_PX, lit ? 0xffffff : tok(TOK_TERTIARY), 1,
                        out + CARD_W - CARD_IN, y, 60, 3);
            }
        }
        ++k;
    }
    stock_canvas_set_fill_color(canvas, fill);
    ui_ink_raw(raw);
}
