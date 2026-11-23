/* OLED's feedback forms (docs/oled-design.md#dialogs-and-toasts): stock's toast as a pill over the
 * page; the confirmation card, ours (confirm) and stock's confirm pop-up drawn as it; an empty or
 * offline page's note (Folder's too); the confirmation after a shuffle. */
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

/* The toast's motion (docs/oled-design.md#overlay-motion), through ui_kit.h's ovm_t: it rises
 * TOAST_RISE px and fades in over TOAST_IN_MS, cubic out (the dialog's opacity, set by the frame
 * timer through ovm_bind, its line moved
 * with the pill); when its dialog goes, the pill stays painted over the page under it
 * (feedback_toast_leave), sinking TOAST_SINK px and fading over TOAST_OUT_MS, cubic in. */
#define TOAST_IN_MS 200
#define TOAST_OUT_MS 150
#define TOAST_RISE 10
#define TOAST_SINK 4
static struct {
    ovm_t mo;
    void *w;           /* the toast's dialog while it is up */
    unsigned text[64]; /* its line, kept for the way out */
    unsigned n;
    int px;
    const char *face;
    char glyph[48];
} tm __attribute__((section(".scratch")));
static int toast_gone(void *w, void *event) {
    (void)event;
    if (tm.w == w)
        tm.w = (void *)0, ovm_bind(&tm.mo, 0), ovm_exit(&tm.mo, TOAST_OUT_MS, EASE_CUBIC_IN);
    return 0;
}

int feedback_toast(void *top) { return top_named(top, "msginfo_dialog"); }

/* A toast that stays (docs/oled-design.md#dialogs-and-toasts): the toast's pill and line, its tokens and its place,
 * painted by a page on its own window w (top: the window's y on screen) for as long as what it says holds, as
 * Radio's "No Wi-Fi" (radio_tuner.c). Stock's toast is a dialog that takes the keys and closes itself after its
 * time, so a toast that must stay while the page is used is this, with no dialog. */
void toast_stay(void *canvas, int top, const char *text) {
    void *lcd = canvas ? P(canvas, CANVAS_LCD) : (void *)0;
    if (!lcd || !text || !*text) return;
    unsigned fill = (unsigned)I(lcd, LCD_FILL_COLOR);
    int pw = ui_text_w(canvas, text, 0, PILL_PX) + 2 * PILL_PAD;
    if (pw > PILL_MAX) pw = PILL_MAX;
    rect_t pill = { (375 - pw) / 2, PILL_Y - top, pw, PILL_H };
    fill_box(canvas, &pill, RGBA(flip_if(ipod_light(), PILL_RGB)), PILL_R);
    ui_text(canvas, text, 0, PILL_PX, ipod_light() ? 0 : 0xffffff, 1, 187, pill.y + PILL_H / 2, pw - 2 * PILL_PAD, 1);
    stock_canvas_set_fill_color(canvas, fill);
}

/* A toast with a glyph before its words (docs/oled-design.md#dialogs-and-toasts): glyph a theme image's
 * whole name. */
void toast_icon(const char *glyph, const char *text) {
    tk_snprintf(pill_glyph, sizeof pill_glyph, "%s", glyph ? glyph : "");
    toast(text);
    pill_glyph[0] = 0;
}
/* A toast shown by the next UI loop pass (toast_later_poll, from ringnav_sleep's usb_ui_poll),
 * never inside stock's USB pass or an input's dispatch, whose own loop a toast's would run again:
 * USB's and charging's. glyph is a theme image's name without the art prefix; the last one asked
 * for wins. Dropped while the screen is dark. */
static struct {
    char glyph[32], text[96]; /* a card app's Sync Offline answer names an album (addon.c) */
} later __attribute__((section(".scratch")));
void toast_later(const char *glyph, const char *text) {
    tk_snprintf(later.glyph, sizeof later.glyph, "%s", glyph ? glyph : "");
    tk_snprintf(later.text, sizeof later.text, "%s", text ? text : "");
}
void toast_later_poll(void) {
    if (!later.text[0]) return;
    char text[sizeof later.text];
    tk_snprintf(text, sizeof text, "%s", later.text);
    later.text[0] = 0;
    if (g_backlight_status)
        toast_icon(later.glyph[0] ? theme_art(later.glyph) : (const char *)0, text);
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
    int first = !widget_get_prop_int(w, "_pill", 0);
    if (first) {
        widget_set_prop_int(w, "_pill", 1);
        widget_set_prop_int(w, "style:normal:bg_color", 0);
        widget_set_prop_int(w, "style:normal:border_color", 0);
        widget_on(w, EVT_DESTROY, toast_gone, w);
        tm.w = w;
        ovm_enter(&tm.mo, TOAST_IN_MS, EASE_CUBIC_OUT);
        ovm_bind(&tm.mo, w); /* the frame timer fades it in, from the next frame */
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
    /* its first frame is painted at stock's opacity, before ovm_bind's: nothing shows */
    widget_set_visible(line, !first, 0);
    if (first) return;
    const unsigned *s = widget_get_text(line);
    int px = PILL_PX, n = (int)wide_len(s);
    const char *face = theme_face(s, n, &px);
    canvas_set_font(canvas, face, px);
    int glyph = pill_glyph[0] ? PILL_GLYPH + PILL_GLYPH_GAP : 0;
    int pw = (int)(canvas_measure_text(canvas, s, n) + 0.5f) + 2 * PILL_PAD + glyph;
    if (pw > PILL_MAX) pw = PILL_MAX;
    rect_t pill = { (375 - pw) / 2, PILL_Y + ovm_shift(&tm.mo, TOAST_RISE), pw, PILL_H };
    for (tm.n = 0; tm.n < (unsigned)n && tm.n < sizeof tm.text / sizeof *tm.text; ++tm.n)
        tm.text[tm.n] = s[tm.n];
    tm.px = px, tm.face = face;
    tk_snprintf(tm.glyph, sizeof tm.glyph, "%s", glyph ? pill_glyph : "");
    if (I(line, W_Y) != pill.y || I(line, W_W) != pw - glyph) {
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
    if (glyph)
        paint_art(w, canvas, pill_glyph, pill.x + PILL_PAD, pill.y + (PILL_H - PILL_GLYPH) / 2);
    stock_canvas_set_fill_color(canvas, fill);
}

/* ringnav_paint, after stock painted w: the toast on its way out, once its dialog has gone, over
 * the top window (the page it was over): its pill and line as feedback_toast_paint last drew them,
 * sinking and fading. */
void feedback_toast_leave(void *w, void *canvas) {
    void *lcd = canvas ? P(canvas, CANVAS_LCD) : (void *)0;
    if (tm.w || !ovm_shown(&tm.mo) || !lcd || w != window_manager_get_top_window(window_manager()))
        return;
    unsigned fill = (unsigned)I(lcd, LCD_FILL_COLOR), a = (unsigned)ovm_alpha(&tm.mo, 255);
    int glyph = tm.glyph[0] ? PILL_GLYPH + PILL_GLYPH_GAP : 0, px = tm.px;
    canvas_set_font(canvas, tm.face, (unsigned)px);
    int pw = (int)(canvas_measure_text(canvas, tm.text, tm.n) + 0.5f) + 2 * PILL_PAD + glyph;
    if (pw > PILL_MAX) pw = PILL_MAX;
    rect_t pill = { (375 - pw) / 2, PILL_Y + ovm_shift(&tm.mo, TOAST_SINK) - I(w, W_Y), pw,
                    PILL_H };
    fill_box(canvas, &pill, a << 24 | (RGBA(flip_if(ipod_light(), PILL_RGB)) & 0xffffffu), PILL_R);
    unsigned raw = ui_ink_raw(1);
    ui_line_w(canvas, tm.text, tm.n, pill.x + glyph, pill.w - glyph, pill.y + PILL_H / 2, 1,
              tm.face ? tm.face : "default", px, a << 24 | (ipod_light() ? 0 : 0xffffffu));
    ui_ink_raw(raw);
    stock_canvas_set_fill_color(canvas, fill);
}

/* A click handler on the check, so the wheel's rows (navigation.c clickable) count it: the check
 * turns itself on a click, Centre's as a tap's. */
static int feedback_check_click(void *check, void *event) { return 0; }

/* The next confirm's body, its lines split at '\n': why it asks, where the setting lives
 * (library_settings.c). */
static const char *confirm_foot __attribute__((section(".scratch")));
void feedback_confirm_foot(const char *foot) { confirm_foot = foot; }

static int confirm_on(void *w) { return top_named(w, "confirminfo_dialog"); }

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
    int py = screen_y(P(line, W_PARENT));
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
 * to show: the page's glyph in outline (tools/art.py EMPTY_GLYPHS, ES_GLYPH px, Text Secondary), a
 * title ES_GLYPH_GAP under it in Inter Medium 20, a hint in 15 px Text Secondary and, when the page
 * has one, its button (ui_kit.c ui_cta_paint) CTA_GAP_ABOVE under it; the whole stack centred
 * between free_top (or the status row's foot) and the screen's. Under rows the page keeps above it
 * (a free_top past the set's bar: Car Remote's switch, Playlists' On-The-Go) it is the compact
 * form, the title and hint (and button) with no glyph, as the glyph is for a page that is empty
 * whole. A view over the page holds it (ES_NAME, its words and top in its props, painted by
 * empty_state_paint; insensitive, so rows above it keep their taps). The button is a one-row list
 * (ES_ROW_NAME) the wheel and Centre reach as any, its row the button's band, its own words hidden:
 * the capsule and its label are painted with the stack, ringed by the focus rule
 * (docs/oled-design.md#focus-ring), and the list's selection bar is not drawn (empty_button). A
 * page drawn by its own painter (Search's sheet) paints the stack itself (empty_paint, at
 * empty_top).
 */
#define ES_NAME "_empty_state"
#define ES_ROW_NAME "_empty_row"
#define ES_TOP 40 /* the status row's foot, as the design measures it */
#define ES_END 320
#define ES_GLYPH 60
#define ES_GLYPH_GAP 14 /* the glyph's foot to the title's band */
#define ES_TITLE_H 24
#define ES_HINT_H 20
#define CTA_GAP_ABOVE 20
#define ES_TITLE_PX TYPE(20) /* Inter Medium 20, AWTK's size */
#define ES_HINT_PX TYPE(15)  /* Inter 15 */
#define ES_WORDS (ES_TITLE_H + ES_HINT_H)
#define ES_TEXT (ES_GLYPH + ES_GLYPH_GAP + ES_WORDS)
/* a status page's progress bar: this far under the title, and over its lines */
#define ES_BAR_GAP 10
#define ES_BAR_H 4
#define ES_BAR_W 232
#define ES_FOOT_Y 304 /* its footer's centre on screen, 13 px Text Tertiary, clear of the glass */
#define ES_FOOT_PX TYPE(13)
/* Whether the empty state under free_top is the compact form: rows above it on the page. */
int empty_compact(int free_top) { return free_top > geom_val(V_BAR) && free_top > ES_TOP; }
/* The lines of a hint ('\n' apart; at least one band, as one empty hint had) */
static int es_lines(const char *hint) {
    int n = 1;
    for (const char *s = hint; s && *s; ++s) n += *s == '\n';
    return n;
}
/* The stack's height down to its button's gap: the glyph's part only in the full form, a status
 * page's bar and each further line of its hint. */
static int es_text_e(int compact, const char *hint, int bar) {
    return (compact ? ES_WORDS : ES_TEXT) + (es_lines(hint) - 1) * ES_HINT_H +
           (bar ? 2 * ES_BAR_GAP + ES_BAR_H : 0);
}
static int es_text(int compact) { return es_text_e(compact, 0, 0); }
static int es_top(int free_top, int free_end, int row, const char *hint, int bar) {
    int stack = es_text_e(empty_compact(free_top), hint, bar) + (row ? CTA_GAP_ABOVE + CTA_H : 0),
        from = free_top > ES_TOP ? free_top : ES_TOP;
    return from + (free_end - from - stack) / 2;
}
/* The stack's top, centred between free_top (the status row's foot at least) and free_end. */
int empty_top(int free_top, int free_end, int row) { return es_top(free_top, free_end, row, 0, 0); }
/* The button's screen y, for a page whose own widget is the button (Songs' Scan Music Now). */
int empty_row_y(int free_top) {
    return empty_top(free_top, ES_END, 1) + es_text(empty_compact(free_top)) + CTA_GAP_ABOVE;
}
/* The empty state in page, made once and written again on each call: e 0 hides it (0). An e with a
 * row but no on_row keeps the button's place in the stack for the caller's own (empty_row_y). The
 * row list is made with the view; a later call shows or hides it but keeps its words and click. */
int empty_state(void *page, int free_top, const empty_t *e, int (*on_row)(void *ctx, void *event)) {
    if (!page) return 0;
    void *art = widget_lookup(page, ES_NAME, 0), *row = widget_lookup(page, ES_ROW_NAME, 0);
    if (!e) {
        if (art) widget_set_visible(art, 0, 0);
        if (row) widget_set_visible(row, 0, 0);
        return 0;
    }
    int y = screen_y(page), top = es_top(free_top, ES_END, e->row != 0, e->hint, e->bar),
        compact = empty_compact(free_top);
    if (!art) {
        art = widget_factory_create_widget(widget_factory(), "view", page, 0, 0, 375, I(page, W_H));
        widget_set_name(art, ES_NAME);
        widget_set_sensitive(art, 0);
    }
    widget_set_prop_str(art, "_es_glyph", e->glyph && !compact ? e->glyph : ""); /* "": compact */
    widget_set_prop_str(art, "_es_title", e->title ? e->title : "");
    widget_set_prop_str(art, "_es_hint", e->hint ? e->hint : "");
    widget_set_prop_str(art, "_es_row", e->row && on_row ? e->row : "");
    widget_set_prop_str(art, "_es_foot", e->foot ? e->foot : "");
    widget_set_prop_int(art, "_es_bar", e->bar);
    widget_set_prop_int(art, "_es_top", top);
    widget_set_visible(art, 1, 0);
    widget_invalidate_force(art, 0);
    if (row)
        widget_set_visible(row, e->row && on_row, 0);
    else if (e->row && on_row) {
        void *view = rows_view(page, top + es_text_e(compact, e->hint, e->bar) + CTA_GAP_ABOVE - y,
                               375, CTA_H, 1, CTA_H);
        widget_set_name(P(view, W_PARENT), ES_ROW_NAME);
        widget_set_prop_int(P(view, W_PARENT), "style:normal:bg_color",
                            0);                  /* the capsule under it */
        page_row_detail(view, 0, "", 0, on_row); /* its words are the capsule's (_es_row) */
        ringnav_select(view, 0, 1);              /* already selected, as the design has it */
    }
    return 1;
}
/* Whether page, or a view in it, shows the empty state. */
int empty_shown(void *page) {
    void *art = page ? widget_lookup(page, ES_NAME, 1) : (void *)0;
    return art && widget_get_visible(art);
}
/* Whether list is an empty state's button, which paints its own selection (no bar). */
int empty_button(void *list) {
    void *p = list ? P(list, W_PARENT) : (void *)0;
    return p && !tk_strcmp(widget_get_prop_str(p, "name", ""), ES_ROW_NAME);
}
/* A status page's bar: a w x h capsule at x, y (the canvas's), in rgb */
static void es_fill(void *canvas, int x, int y, int w, int h, int r, unsigned rgb) {
    void *vg = canvas_get_vgcanvas(canvas);
    if (!vg) return;
    vgcanvas_save(vg);
    vgcanvas_translate(vg, (float)I(canvas, CANVAS_X), (float)I(canvas, CANVAS_Y));
    round_path(vg, (float)x, (float)y, (float)w, (float)h, (float)r);
    vgcanvas_set_fill_color(vg, RGBA(rgb));
    vgcanvas_fill(vg);
    vgcanvas_restore(vg);
}
/* The stack painted in w at top (a y in w); its button, when e->row, selected or not. No glyph
 * (0 or ""): the compact form, the title at top. */
void empty_paint(void *w, void *canvas, const empty_t *e, int top) {
    int light = ipod_light(), glyph = e->glyph && *e->glyph;
    char art[48];
    tk_snprintf(art, sizeof art, "empty_%s_%c", glyph ? e->glyph : "", light ? 'l' : 'd');
    if (glyph) paint_art(w, canvas, theme_art(art), 375 / 2 - ES_GLYPH / 2, top);
    int ty = top + (glyph ? ES_GLYPH + ES_GLYPH_GAP : 0);
    /* Text Primary: white, which ui_ink turns black on Light Mode's paper */
    ui_text(canvas, e->title ? e->title : "", 1, ES_TITLE_PX, 0xffffff, 0, 375 / 2,
            ty + ES_TITLE_H / 2 - 1, 375 - 48, 1);
    ty += ES_TITLE_H;
    if (e->bar) { /* a status page's progress: Track, filled in the accent to the permille */
        int x = (375 - ES_BAR_W) / 2, fill = ES_BAR_W * (e->bar > 1001 ? 1000 : e->bar - 1) / 1000;
        es_fill(canvas, x, ty + ES_BAR_GAP, ES_BAR_W, ES_BAR_H, ES_BAR_H / 2, tok(TOK_TRACK));
        if (fill > 0)
            es_fill(canvas, x, ty + ES_BAR_GAP, fill, ES_BAR_H, ES_BAR_H / 2,
                    scheme()->tones[0] & 0xffffffu);
        ty += 2 * ES_BAR_GAP + ES_BAR_H;
    }
    char line[128];
    for (const char *s = e->hint ? e->hint : ""; s; ty += ES_HINT_H) {
        const char *nl = strstr(s, "\n");
        unsigned k = nl ? (unsigned)(nl - s) : (unsigned)strlen(s);
        tk_snprintf(line, sizeof line, "%.*s", (int)k, s);
        ui_text(canvas, line, 0, ES_HINT_PX, tok(TOK_SECONDARY), 0, 375 / 2, ty + ES_HINT_H / 2,
                375 - 48, 1);
        s = nl ? nl + 1 : 0;
    }
    if (e->foot && *e->foot)
        ui_text(canvas, e->foot, 0, ES_FOOT_PX, tok(TOK_TERTIARY), 0, 375 / 2,
                ES_FOOT_Y - screen_y(w), 375 - 96, 1);
}
void empty_state_paint(void *w, void *canvas) {
    if (!w || !P(canvas, CANVAS_LCD) || tk_strcmp(widget_get_prop_str(w, "name", ""), ES_NAME))
        return;
    empty_t e = { widget_get_prop_str(w, "_es_glyph", ""), widget_get_prop_str(w, "_es_title", ""),
                  widget_get_prop_str(w, "_es_hint", ""), 0, widget_get_prop_str(w, "_es_foot", ""),
                  widget_get_prop_int(w, "_es_bar", 0) };
    int top = widget_get_prop_int(w, "_es_top", ES_TOP) - screen_y(w);
    empty_paint(w, canvas, &e, top);
    const char *label = widget_get_prop_str(w, "_es_row", "");
    void *row = *label ? widget_lookup(P(w, W_PARENT), ES_ROW_NAME, 0) : (void *)0,
         *list = row && widget_count_children(row) ? widget_get_child(row, 0) : (void *)0;
    if (list && widget_get_visible(row)) {
        /* selected: the wheel's list is the button's, on its one row */
        int sel = g_menu.w == list && widget_get_prop_int(list, SEL, -1) == 0 ? 0 : -1;
        ui_cta_paint(canvas, &label, 1, top + es_text_e(!*e.glyph, e.hint, e.bar) + CTA_GAP_ABOVE,
                     sel, pointer_down() && !sel);
    }
}
/* A list page with nothing listed (page_list's, no rows): the empty state in body, the page's name
 * hidden on its first line for the status row (navigation.c page_caption). Its list view. */
void *empty_list(void *page, void *body, const char *name, const empty_t *e,
                 int (*on_row)(void *ctx, void *event)) {
    void *view = page_list(page, body, 0, name, 0, 48);
    widget_set_visible(widget_get_child(body, 0), 0, 0);
    empty_state(body, 0, e, on_row);
    /* after: Cover Flow's title follows its empty state (coverflow_bar_title). The bar looks for
     * the page's title once (bar_title_sync, st.title_win); one already titled while its covers
     * loaded would keep that, so it looks again. */
    title_gone(0, 0);
    bar_title_sync(page);
    return view;
}
/* No card in the slot (stock's mount marks it with /tmp/mmc_add): the Library's empty pages say
 * so, with no row (oled-design.md Empty and offline: Library). 1 when e was rewritten. */
int empty_card(empty_t *e) {
    if (!access("/tmp/mmc_add", 0)) return 0;
    *e = (empty_t){ "sd-card", "No SD Card", "Insert an SD card with music", 0, 0, 0 };
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
    sel_bar_flat(canvas, 0, 0, I(w, W_W), I(w, W_H));
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
                               "Turn on Wi-Fi", 0, 0 };
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

/* Stock's panel as the empty state's button (at screen y), Scan Music Now: the button itself, the
 * capsule's band across the screen with no fill or words of its own (feedback_scan_bar paints the
 * capsule and its label); the picture and the line go, as the empty state says it. */
static const char *const scan_words[] = { "Scan Music Now" };
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
    widget_move_resize(btn, 0, 0, I(view, W_W), CTA_H);
    for (unsigned i = 0; i < sizeof fills / sizeof *fills; ++i) widget_set_prop_int(btn, fills[i], 0);
    widget_set_prop_int(btn, "style:normal:border_color", 0);
    widget_set_text_utf8(btn, "");
    widget_set_prop_int(btn, "_scan_row", 1);
}

/* The Scan Music Now button: the capsule under the button's band, selected while the wheel's
 * selection is on it. */
void feedback_scan_bar(void *w, void *canvas) {
    if (!w || !P(canvas, CANVAS_LCD) || !widget_get_prop_int(w, "_scan_row", 0) ||
        tk_strcmp(widget_get_prop_str(w, "name", ""), "btn_upmusic"))
        return;
    int x = I(canvas, CANVAS_X);
    I(canvas, CANVAS_X) = x - page_ox(canvas, w); /* ui_cta_paint centres on the page's origin */
    ui_cta_paint(canvas, scan_words, 1, 0, 0, pointer_down()); /* its one stop: selected */
    I(canvas, CANVAS_X) = x;
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
            ? (empty_t){ "heart-slash", "No Favorites", "Hold Play/Pause on a song to add it", 0, 0, 0 }
        : cls == 0xf007 ? (empty_t){ "clock-counter-clockwise", "Nothing Played Yet",
                                     "Songs you play show up here", 0, 0, 0 }
        : cls == 0xf008
            ? (empty_t){ "chart-bar", "No Plays Yet", "Your most played songs show up here", 0, 0, 0 }
        : cls == 0xf009
            ? (empty_t){ "sparkle", "No Songs Added Yet", "New music on the card shows up here", 0, 0, 0 }
            : (empty_t){ "music-notes", "No Songs", "Copy music to the SD card",
                         scan ? "Scan Music Now" : 0, 0, 0 };
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
    empty_t e = { "folder", "No Folders", "Copy music to the SD card", 0, 0, 0 };
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
    void *label = text(page, 0, cy - h / 2, 375, h);
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
    void *glyph =
        image_create(page, (375 - CONF_GLYPH) / 2, 140 - CONF_GLYPH / 2, CONF_GLYPH, CONF_GLYPH);
    image_base_set_image(glyph, theme_art("confirm_shuffle"));
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

/* A mode page (docs/oled-design.md#usb, U2-U6; the sign-in steps, SD Card Update, Bluetooth's
 * receiver): the stack, circle, title, line and the optional pill or button, centred as one
 * between the status row's foot (MODE_TOP) and the screen's bottom. */
#define MODE_TOP 40
#define MODE_STACK 158
#define MODE_STACK_PILL 204
#define MODE_TITLE_Y 120
#define MODE_TITLE_PX TYPE(22) /* Inter Medium 22 */
#define MODE_LINE_Y 148
#define MODE_LINE_PX TYPE(15) /* Inter Regular 15 */
#define MODE_PILL_Y 172
#define MODE_PILL_H 32
#define MODE_PILL_PAD 22
#define MODE_PILL_PX TYPE(17) /* Inter Medium 17 */

/* A mode page in w's coordinates (dy its y on screen): m's glyph on its solid circle (none for
 * MODE_BARE: a spinner), the title, the line and the optional pill, or a button centred where the
 * pill would be (MODE_BUTTON). Returns the stack's top in w's coordinates. */
int mode_page_paint(void *w, void *canvas, int dy, const mode_page_t *m) {
    const char *pill = m->pill;
    unsigned raw = ui_ink_raw(1); /* final colours, each half's */
    int stack = pill ? MODE_STACK_PILL : MODE_STACK, px = m->glyph_px ? m->glyph_px : MODE_GLYPH;
    int top = MODE_TOP + (320 - MODE_TOP - stack) / 2 - dy, cx = 375 / 2;
    rect_t circle = { cx - MODE_R, top, 2 * MODE_R, 2 * MODE_R };
    unsigned ring = m->ring & ~MODE_BUTTON;
    if (ring != MODE_BARE) fill_box(canvas, &circle, RGBA(ring), MODE_R);
    if (m->glyph) paint_art(w, canvas, theme_art(m->glyph), cx - px / 2, top + MODE_R - px / 2);
    ui_line(canvas, m->title, 0, 375, top + MODE_TITLE_Y, 1, "inter-medium", MODE_TITLE_PX,
            tok(TOK_PRIMARY));
    ui_line(canvas, m->line, 0, 375, top + MODE_LINE_Y, 1, "inter", MODE_LINE_PX,
            tok(TOK_SECONDARY));
    if (pill && (m->ring & MODE_BUTTON)) {
        ui_button(canvas, cx, top + MODE_PILL_Y + MODE_PILL_H / 2, pill, 0, 1, 0);
    } else if (pill) {
        int pw = ui_measure(canvas, pill, "inter-medium", MODE_PILL_PX) + 2 * MODE_PILL_PAD;
        rect_t r = { cx - pw / 2, top + MODE_PILL_Y, pw, MODE_PILL_H };
        fill_box(canvas, &r, RGBA(tok(TOK_RAISED_2)), MODE_PILL_H / 2);
        ui_line(canvas, pill, r.x, r.w, r.y + MODE_PILL_H / 2, 1, "inter-medium", MODE_PILL_PX,
                tok(TOK_PRIMARY));
    }
    ui_ink_raw(raw);
    return top;
}

/* The confirmation card (docs/oled-design.md#confirmation-card): stock's sortselect_dialog, as the
 * menu sheet's pickers use it (menu_modal.c modal_list), its list the card's answers and the rest
 * painted here. The scrim dims the page and the status row (confirm_bar_dim); the card is
 * CARD_W wide at CARD_X, its stack centred on CARD_CY and kept within CARD_TOP..CARD_FOOT: 18 px
 * of padding, the title (Inter Medium 20), the body (15 px Text Secondary, wrapped to the column on
 * 19 px lines, 4 px under the title), the code (Inter Medium 30), then 14 px down the switch row
 * and the answers, 32 px each, evenly spaced whatever the kind (as Shut Down's), and 8 px of
 * padding. The answers' bar is 8 px in from the card's sides; a destructive one is red (Negative),
 * its bar too when lit. */
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
#define CARD_BODY_LINES 4 /* the body wraps to as many lines as it needs, this many at most */
#define CARD_CODE_GAP 6
#define CARD_CODE_H 40
#define CARD_ROWS_GAP 14
#define CARD_ROW 32
#define CARD_BAR_IN 8
#define CARD_SCRIM 0x80000000u /* black at 50% */
#define CARD_TIMER_MS 100      /* the timeout's seconds and bar */
#define CARD_COUNT_PX TYPE(15)
#define CARD_GLYPH 28    /* tools/art.py PAIR_GLYPH_PX: a glyph over the title */
#define CARD_GLYPH_GAP 6 /* its foot to the title's box */
/* A title wider than the text column scrolls: the house marquee (docs/oled-design.md#motion),
 * held CARD_MARQ_HOLD_MS at its start, then CARD_MARQ_PX px a second, a copy CARD_MARQ_GAP px
 * behind, its ends faded CARD_MARQ_FADE px into the card. */
#define CARD_MARQ_HOLD_MS 2750
#define CARD_MARQ_PX 35
#define CARD_MARQ_GAP 40
#define CARD_MARQ_FADE 12
#define CARD_MARQ_MS 33
/* The wide form (confirm_t across, adb's Allow this computer?, from the approved mock of
 * 2026-11-18): AC_W wide at AC_X with AC_R corners, AC_PAD of padding above and below and AC_IN at
 * the sides; the AC_GLYPH glyph and AC_GLYPH_GAP before the title on one AC_HEAD_H line; the body
 * AC_BODY_GAP under it (15 px on CARD_BODY_LINE lines, left-aligned); the note AC_NOTE_GAP under
 * that (13 px Text Tertiary); AC_GAP down the switch row (AC_SWITCH_H, 16 px words AC_ROW_IN in
 * from the text column); AC_GAP down the two answers, AC_BTN_H buttons AC_GAP apart with round
 * ends, 16 px words, the lit one the accent and the other Raised 2. */
#define AC_X 18
#define AC_W 339
#define AC_R 26
#define AC_PAD 16
#define AC_IN 18
#define AC_GLYPH 24 /* tools/art.py ADB_CARD_PX */
#define AC_GLYPH_GAP 10
#define AC_HEAD_H 24
#define AC_BODY_GAP 6
#define AC_NOTE_GAP 2
#define AC_NOTE_H 17
#define AC_NOTE_PX TYPE(13)
#define AC_GAP 10
#define AC_SWITCH_H 34
#define AC_ROW_IN 4
#define AC_ROW_PX TYPE(16)
#define AC_BTN_H 40
/* The answers' list holds the switch row, an AC_GAP gap and the two answers stacked AC_BTN_H / 2
 * tall each inside the buttons' band, as the list lays its rows one under another: the wheel and
 * Centre move over them as rows, and two buttons over the band (left and right halves) take a tap,
 * so no tap in the band reaches a row under it. */
#define AC_ANS_H (AC_BTN_H / 2)
/* The card's motion (docs/oled-design.md#overlay-motion), through ui_kit.h's ovm_t: in over
 * CARD_IN_MS cubic out, the scrim and the card fading up together (the dialog's opacity, which
 * ovm_bind hands to the frame timer: stock never paints a dialog at opacity 0); an
 * answer fades it out over CARD_OUT_MS, linear, before the dialog closes and the answer runs.
 * ponytail: the approved 1.06 to 1 scale on the way in is not drawn; the rows are stock list items
 * the canvas cannot scale, so it would need the card painted once into a snapshot first. */
#define CARD_IN_MS 200
#define CARD_OUT_MS 120

static struct {
    void *dialog, *view, *cell[2], *check; /* a dressed stock confirm's answers and its switch */
    int stock;                             /* the dialog is stock's confirminfo_dialog */
    confirm_t c;
    char title[96], body[CARD_BODY_LINES][64], code[16], row[2][24], toggle[48];
    int lead; /* across: the body's first characters in Text Primary, the name's */
    const char *rows[2];
    int lines, toggle_on, top, rows_y, h, chosen;
    unsigned timer, answer_timer, until, marq_timer, marq_at;
    int title_w; /* the title's width, measured at its first paint; 0 until then */
    ovm_t mo;    /* the card's enter and exit (CARD_IN_MS, CARD_OUT_MS) */
    const char *glyph; /* the caller's glyph, which card_layout may leave out */
} cf __attribute__((section(".scratch")));

int confirm_up(void) { return cf.dialog != (void *)0; }
/* w holds the card's answers as rows: navigation.c's selection keeps their own width */
int confirm_surface(void *w) { return w && cf.dialog && w == (cf.stock ? cf.dialog : cf.view); }
static int card_items(void) { return cf.c.n_rows + !!cf.c.toggle; }
static int card_destructive(int row) {
    return cf.c.kind == CONFIRM_DESTRUCTIVE && cf.c.n_rows > 1 && row == cf.c.n_rows - 1;
}

/* The card's height and top, and where its switch row and answers start (screen y). A glyph
 * that would take the card past CARD_TOP to CARD_FOOT goes (a guest's pairing: two body lines,
 * the code and three answers), so the card shows whole. */
static void card_body(void *w, const char *s);
static void card_layout(void) {
    int h = 0, rows = 0;
    if (cf.c.across) {
        rows = AC_PAD + AC_HEAD_H + (cf.lines ? AC_BODY_GAP + cf.lines * CARD_BODY_LINE : 0) +
               (cf.c.note ? AC_NOTE_GAP + AC_NOTE_H : 0) + AC_GAP;
        h = rows + (cf.c.toggle ? AC_SWITCH_H + AC_GAP : 0) + AC_BTN_H + AC_PAD;
        int top = CARD_CY - h / 2;
        if (top + h > CARD_FOOT) top = CARD_FOOT - h;
        if (top < CARD_TOP) top = CARD_TOP;
        cf.top = top, cf.h = h, cf.rows_y = top + rows;
        return;
    }
    for (int pass = 0; pass < 2; ++pass) {
        h = CARD_PAD_TOP + CARD_TITLE_H + (cf.c.glyph ? CARD_GLYPH + CARD_GLYPH_GAP : 0);
        if (cf.lines) h += CARD_BODY_GAP + cf.lines * CARD_BODY_LINE;
        if (cf.code[0]) h += CARD_CODE_GAP + CARD_CODE_H;
        rows = h + CARD_ROWS_GAP;
        h = rows + card_items() * CARD_ROW + CARD_PAD_FOOT;
        if (h <= CARD_FOOT - CARD_TOP || !cf.c.glyph) break;
        cf.c.glyph = (const char *)0;
    }
    int top = CARD_CY - h / 2;
    if (top + h > CARD_FOOT) top = CARD_FOOT - h;
    if (top < CARD_TOP) top = CARD_TOP;
    cf.top = top, cf.h = h, cf.rows_y = top + rows;
}

void confirm_close(void) {
    if (cf.stock) return; /* stock's runs its own loop to its answer */
    void *d = cf.dialog;
    cf.dialog = cf.view = (void *)0;
    if (cf.timer) timer_remove(cf.timer), cf.timer = 0;
    if (cf.marq_timer) timer_remove(cf.marq_timer), cf.marq_timer = 0;
    if (d) window_close(d);
}
void *confirm_dialog(void) { return cf.dialog; }

static int card_answer(const void *unused) {
    cf.answer_timer = 0;
    confirm_t c = cf.c;
    int row = cf.chosen, on = cf.toggle_on;
    confirm_close();
    if (c.answer) c.answer(c.ctx, row, on);
    return 0;
}
/* Answers row once, once the card has faded out (at once for stock's, which closes itself): the
 * dialog closes before the caller's answer runs. */
static void card_choose(int row) {
    if (!cf.dialog || cf.answer_timer) return;
    cf.chosen = row;
    if (!cf.stock) ovm_exit(&cf.mo, CARD_OUT_MS, EASE_LINEAR);
    cf.answer_timer = timer_add(card_answer, (void *)0, cf.stock ? 0 : cf.mo.a.ms);
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
        cf.stock = 0;
        if (cf.timer) timer_remove(cf.timer), cf.timer = 0;
        if (cf.marq_timer) timer_remove(cf.marq_timer), cf.marq_timer = 0;
    }
    return 0;
}
/* s's width in the title's face (Inter Medium 20, a fallback face for a character Inter lacks). */
static int card_text_w(void *canvas, const char *s) {
    unsigned buf[96], n = ui_wide(s, buf, sizeof buf / sizeof *buf);
    int px = TYPE(20);
    const char *face = theme_face(buf, (int)n, &px);
    if (face && !tk_strcmp(face, "inter")) face = "inter-medium";
    canvas_set_font(canvas, face, (unsigned)px);
    return (int)(canvas_measure_text(canvas, buf, n) + 0.5f);
}
/* A column of h px from (x, y) in c, its alpha c's top byte. */
static void scrub_col(void *canvas, unsigned c, int x, int y, int h) {
    stock_canvas_set_fill_color(canvas, (RGBA(c & 0xffffffu) & 0xffffffu) | (c & 0xff000000u));
    canvas_fill_rect(canvas, x, y, 1, h);
}
/* The scrolling title's frame: a repaint, and the next frame (RET_REPEAT: AWTK drops a timer that
 * answers anything else). */
static int card_marq(const void *unused) {
    if (!cf.dialog) return cf.marq_timer = 0, 0;
    widget_invalidate_force(cf.dialog, (void *)0);
    return 8; /* RET_REPEAT */
}
/* The title's offset now, px from its start: the hold, then a run of its width and the gap. */
static int card_marq_off(void) {
    unsigned t = (unsigned)time_now_ms() - cf.marq_at, run = (unsigned)(cf.title_w + CARD_MARQ_GAP),
             run_ms = run * 1000 / CARD_MARQ_PX, cycle = CARD_MARQ_HOLD_MS + run_ms;
    t %= cycle;
    return t < CARD_MARQ_HOLD_MS ? 0 : (int)((t - CARD_MARQ_HOLD_MS) * CARD_MARQ_PX / 1000);
}
/* The title: centred when it fits, else scrolling in the text column with its ends faded into
 * the card (fill, its colour). */
static void card_title(void *canvas, int cx, int y, int tw, unsigned fill) {
    if (!cf.title_w) cf.title_w = card_text_w(canvas, cf.title);
    if (cf.title_w <= tw) {
        ui_text(canvas, cf.title, 1, TYPE(20), tok(TOK_PRIMARY), 1, cx, y, tw, 1);
        return;
    }
    if (!cf.marq_timer) {
        cf.marq_at = (unsigned)time_now_ms();
        cf.marq_timer = timer_add(card_marq, (void *)0, CARD_MARQ_MS);
    }
    int x0 = cx - tw / 2, off = card_marq_off(), was[4];
    canvas_get_clip_rect(canvas, was);
    int ox = I(canvas, CANVAS_X), oy = I(canvas, CANVAS_Y);
    rect_t col = { x0 + ox, y - CARD_TITLE_H / 2 + oy, tw, CARD_TITLE_H }, in = col;
    if (in.x < was[0]) in.w -= was[0] - in.x, in.x = was[0];
    if (in.y < was[1]) in.h -= was[1] - in.y, in.y = was[1];
    if (in.x + in.w > was[0] + was[2]) in.w = was[0] + was[2] - in.x;
    if (in.y + in.h > was[1] + was[3]) in.h = was[1] + was[3] - in.y;
    if (in.w <= 0 || in.h <= 0) return;
    canvas_set_clip_rect(canvas, &in);
    for (int k = 0; k < 2; ++k)
        ui_text(canvas, cf.title, 1, TYPE(20), tok(TOK_PRIMARY), 1,
                x0 - off + k * (cf.title_w + CARD_MARQ_GAP), y, cf.title_w + 8, 2);
    for (int i = 0; i < CARD_MARQ_FADE; ++i) { /* the ends into the card */
        unsigned a = (unsigned)(255 - 255 * i / CARD_MARQ_FADE) << 24 | (fill & 0xffffffu);
        if (off) scrub_col(canvas, a, x0 + i, y - CARD_TITLE_H / 2, CARD_TITLE_H);
        scrub_col(canvas, a, x0 + tw - 1 - i, y - CARD_TITLE_H / 2, CARD_TITLE_H);
    }
    canvas_set_clip_rect(canvas, (rect_t *)was);
}
/* The timeout's tick: a repaint for its seconds and bar, repeating, the answer at the end. */
static int card_tick(const void *unused) {
    if (!cf.dialog) return cf.timer = 0, 0;
    if ((int)(cf.until - (unsigned)time_now_ms()) <= 0) {
        cf.timer = 0;
        card_choose(cf.c.timeout_row);
        return 0;
    }
    widget_invalidate_force(cf.dialog, (void *)0);
    return 8; /* RET_REPEAT */
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
    cf.glyph = c->glyph;
    cf.toggle_on = c->toggle_on;
    tk_snprintf(cf.title, sizeof cf.title, c->title ? c->title : "", c->name ? c->name : "");
    if (c->across) { /* the name in the body, where its %s is, and in Text Primary there */
        char body[160];
        const char *at = c->body ? strstr(c->body, "%s") : (const char *)0;
        tk_snprintf(body, sizeof body, c->body ? c->body : "", c->name ? c->name : "");
        cf.lead = at == c->body && c->name ? (int)strlen(c->name) : 0;
        card_body(dialog, body);
    } else
        card_body(dialog, c->body);
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
    int out = theme()->sel_inset - CARD_BAR_IN, cx = c->across ? AC_X : CARD_X,
        w = (c->across ? AC_W : CARD_W) + 2 * out, y = 0;
    for (int k = c->toggle ? -1 : 0; k < c->n_rows; ++k) {
        if (c->across && k == 0 && c->toggle) {
            void *gap = label_create(view, 0, y, w, AC_GAP);
            widget_set_text_utf8(gap, "");
            y += AC_GAP;
        }
        if (c->across) {
            void *item = list_item_create(view, 0, y, w, k < 0 ? AC_SWITCH_H : AC_ANS_H);
            widget_use_style(item, "s_listitem_black");
            widget_set_prop_int(item, "style:normal:bg_color", 0);
            widget_on(item, EVT_CLICK, card_click, (void *)(long)k);
            y += k < 0 ? AC_SWITCH_H : AC_ANS_H;
            continue;
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
        widget_move_resize(list, cx - out, cf.rows_y - I(dialog, W_Y), w, y);
        widget_set_prop_int(view, "virtual_h", y);
        widget_set_prop_str(view, "self_layout", "default(x=0,y=0,w=100%,h=100%)");
    }
    if (c->across && c->n_rows == 2) { /* the band's taps: its left half the first answer */
        int band = cf.rows_y + (c->toggle ? AC_SWITCH_H + AC_GAP : 0) - I(dialog, W_Y);
        for (int k = 0; k < 2; ++k) {
            void *b = button_create(dialog, cx - out + k * (w / 2), band, k ? w - w / 2 : w / 2, AC_BTN_H);
            widget_use_style(b, "s_btn_listitem"); /* a tap's click; confirm_rows_paint draws it */
            widget_on(b, EVT_CLICK, card_click, (void *)(long)k);
        }
    }
    cf.dialog = dialog, cf.view = view;
    ovm_enter(&cf.mo, CARD_IN_MS, EASE_CUBIC_OUT);
    ovm_bind(&cf.mo, dialog); /* the frame timer fades it in */
    int first = c->kind == CONFIRM_NEUTRAL ? c->n_rows - 1 : 0;
    st.touch_mode = 0;
    ringnav_select(view, first + !!c->toggle, card_items());
    if (c->timeout_ms > 0) {
        cf.until = (unsigned)time_now_ms() + (unsigned)c->timeout_ms;
        cf.timer = timer_add(card_tick, (void *)0, CARD_TIMER_MS);
    }
    return 1;
}

/* The card up (one confirm() showed) with name as its title's %s and body as its body: a name
 * known only after the card opened (bt_pair.c). The card is laid out again round them, its
 * answers moved with it; the selection, the switch and the timeout stay. */
void confirm_words(const char *name, const char *body) {
    void *d = cf.dialog, *list = d ? widget_lookup(d, "list_view", 1) : (void *)0;
    if (!d || cf.stock || cf.c.across) return;
    tk_snprintf(cf.title, sizeof cf.title, cf.c.title ? cf.c.title : "", name ? name : "");
    cf.title_w = 0; /* measured again at the next paint; the marquee starts over */
    if (cf.marq_timer) timer_remove(cf.marq_timer), cf.marq_timer = 0;
    cf.lines = 0;
    cf.c.glyph = cf.glyph;
    card_body(d, body);
    card_layout();
    if (list) {
        int out = theme()->sel_inset - CARD_BAR_IN;
        widget_move_resize(list, CARD_X - out, cf.rows_y - I(d, W_Y), I(list, W_W), I(list, W_H));
    }
    widget_invalidate_force(d, (void *)0);
}

/* The body's lines from s, '\n' between its paragraphs, each wrapped at spaces to the text
 * column in w's canvas (a word wider than the column ends its line there, cut), CARD_BODY_LINES at
 * most. With no canvas each paragraph is a line. */
static void card_body(void *w, const char *s) {
    void *canvas = w ? widget_get_canvas(w) : (void *)0;
    while (s && *s && cf.lines < CARD_BODY_LINES) {
        unsigned n = 0, take;
        while (s[n] && s[n] != '\n') ++n;
        take = n;
        if (canvas) {
            char line[sizeof cf.body[0]];
            unsigned end = 0;
            take = 0;
            while (end < n) {
                unsigned e = end + 1;
                while (e < n && s[e] != ' ') ++e;
                tk_snprintf(line, sizeof line, "%.*s", (int)e, s);
                if (take && ui_text_w(canvas, line, 0, TYPE(15)) >
                                (cf.c.across ? AC_W - 2 * AC_IN : CARD_W - 2 * CARD_IN))
                    break;
                take = end = e;
            }
        }
        tk_snprintf(cf.body[cf.lines++], sizeof cf.body[0], "%.*s", (int)take, s);
        s += take;
        if (take < n)
            while (*s == ' ') ++s;
        else if (*s == '\n')
            ++s;
    }
}
static int card_toggle_on(void) {
    return cf.check ? widget_get_prop_int(cf.check, "value", 0) : cf.toggle_on;
}

/* Stock's confirm pop-up (confirminfo_dialog, its own loop answering 10 for the verb) as the card:
 * its prompt named as asks[] says (else its words, its verb the prompt's first word when one of
 * verbs[], else OK), a note or the caller's foot as the body, its two cells the answers Cancel and
 * the verb, Delete Song's "Also delete source file" the switch row above them. Every stock caller
 * gets the card with no change of its own. The dialog's first paint, before its background; once.
 */
static const struct {
    const char *prompt, *title, *verb, *body;
} asks[] = {
    { "Shut Down?", "Shut Down?", "Shut Down", 0 },
    { "Restart?", "Restart?", "Restart", 0 }, /* Power's Restart (power_rows.c) */
    { "Restart to apply?", "Restart to Apply?", "Restart", 0 },
    /* what each really takes (stock's reset_page row click: init_usersetting deletes config.ini,
     * wpa_supplicant.conf, bluetooth, userequalizer, memoryfile and the histories; init_database
     * deletes database.db, its playlists and My Fav with it, then the Q2 restarts to scan; the
     * factory reset runs both; none touches the card's music or the payload's ratings) */
    { "Reset Settings Only", "Reset Settings?", "Reset Settings",
      "Forgets Wi-Fi, Bluetooth, EQ;\nmusic and playlists stay." },
    { "Reset Database", "Reset the Database?", "Reset Database",
      "Erases playlists, Favorites;\nthen restarts and rescans." },
    { "Full Factory Reset", "Full Factory Reset?", "Reset All",
      "Erases settings, playlists\nand Favorites. Music stays." },
    { "Delete Song?", "Delete This Song?", "Delete", 0 },
    { "Delete File?", "Delete This File?", "Delete", 0 },
    { "Scan music?", "Scan Music?", "Scan", 0 },
    { "unpair?", "Unpair This Device?", "Unpair", 0 },
    { "Unpair?", "Unpair This Device?", "Unpair", 0 },
    { "Delete Record?", "Forget This Network?", "Forget", 0 },
    { "Disconnect Now?", "Disconnect?", "Disconnect", 0 },
    { "Line Out sets max volume, continue?", "Use Line Out?", "Use Line Out",
      "Line Out sets the volume to its maximum." },
    { "Export Playlist?", "Export Playlists?", "Export", 0 },
};
static const char *const verbs[] = {
    "Delete", "Import", "Export", "Quit", "Disconnect", "Reconnect"
};
/* the verbs that lose something: red, after the gap */
static const char *const losing[] = { "Delete", "Reset", "Forget", "Unpair", "Remove", "Clear" };
/* the verbs whose mistake costs least: the card opens on them (Return is still Cancel) */
static const char *const mild[] = { "Scan", "Import", "Export", "Reconnect", "Turn On" };

int feedback_confirm_dress(void *w) {
    if (!confirm_on(w)) return 0;
    if (cf.dialog == w) return 1;
    void *cell[2] = { widget_lookup(w, "img_cancel", 0), widget_lookup(w, "img_enter", 0) };
    if (!cell[0] || !cell[1] || cf.dialog) return 1;
    char prompt[128] = "", word[16];
    for (unsigned i = 0, n = widget_count_children(w); i < n; ++i) {
        void *c = widget_get_child(w, i);
        if (!tk_strcmp(widget_get_type(c), "button") ||
            !tk_strcmp(widget_get_prop_str(c, "name", ""), "msg_delcheckbutton"))
            continue;
        if (!*prompt) widget_get_text_utf8(c, prompt, sizeof prompt);
        widget_set_visible(c, 0, 0);
    }
    const char *title = prompt, *verb = st.confirm_verb ? st.confirm_verb : "OK",
               *body = confirm_foot;
    int asked = 0;
    for (unsigned k = 0; k < sizeof asks / sizeof *asks; ++k)
        if (!tk_strcmp(prompt, asks[k].prompt))
            title = asks[k].title, verb = asks[k].verb, body = asks[k].body ? asks[k].body : body,
            asked = 1;
    unsigned len = 0;
    while (prompt[len] && prompt[len] != ' ' && len < sizeof word - 1)
        word[len] = prompt[len], ++len;
    word[len] = 0;
    for (unsigned k = 0; !st.confirm_verb && !asked && k < sizeof verbs / sizeof *verbs; ++k)
        if (!tk_strcmp(word, verbs[k])) verb = verbs[k];
    memset(&cf, 0, sizeof cf);
    cf.dialog = w, cf.stock = 1, cf.cell[0] = cell[0], cf.cell[1] = cell[1];
    tk_snprintf(cf.title, sizeof cf.title, "%s", title);
    tk_snprintf(cf.row[0], sizeof cf.row[0], "Cancel");
    tk_snprintf(cf.row[1], sizeof cf.row[1], "%s", verb);
    cf.rows[0] = cf.row[0], cf.rows[1] = cf.row[1];
    cf.c.rows = cf.rows, cf.c.n_rows = 2, cf.c.kind = CONFIRM_CONSEQUENTIAL;
    for (unsigned k = 0; k < sizeof losing / sizeof *losing; ++k)
        if (!strncmp(verb, losing[k], strlen(losing[k]))) cf.c.kind = CONFIRM_DESTRUCTIVE;
    for (unsigned k = 0; k < sizeof mild / sizeof *mild; ++k)
        if (!tk_strcmp(verb, mild[k])) cf.c.kind = CONFIRM_NEUTRAL;
    card_body(w, body);
    cf.check = widget_lookup(w, "msg_delcheckbutton", 0);
    if (cf.check) { /* the switch row: its words painted (confirm_rows_paint), the check a stop */
        widget_get_text_utf8(cf.check, cf.toggle, sizeof cf.toggle);
        widget_set_text_utf8(cf.check, "");
        widget_on(cf.check, EVT_CLICK, feedback_check_click, cf.check);
        cf.c.toggle = cf.toggle;
    }
    card_layout();
    widget_on(w, EVT_DESTROY, card_gone, w);
    widget_set_prop_int(w, "style:normal:bg_color", 0);
    widget_set_prop_int(w, "style:normal:border_color", 0);
    int out = theme()->sel_inset - CARD_BAR_IN, x = CARD_X - out, cw = CARD_W + 2 * out,
        y = cf.rows_y - I(w, W_Y);
    if (cf.check) widget_move_resize(cf.check, x, y, cw, CARD_ROW), y += CARD_ROW;
    for (int k = 0; k < 2; ++k) {
        widget_move_resize(cell[k], x, y, cw, CARD_ROW);
        widget_set_text_utf8(cell[k], "");
        y += CARD_ROW;
    }
    /* a neutral question opens on its verb, the rest on Cancel, stock's own first stop */
    if (cf.c.kind == CONFIRM_NEUTRAL) ringnav_select(w, 1 + !!cf.check, 2 + !!cf.check);
    return 1;
}

/* The status row under the card's scrim, from ringnav_paint after stock painted it. */
void confirm_bar_dim(void *w, void *canvas) {
    void *bar = *(void *const *)system_bar, *lcd = canvas ? P(canvas, CANVAS_LCD) : (void *)0;
    if (!cf.dialog || w != bar || !lcd) return;
    unsigned fill = (unsigned)I(lcd, LCD_FILL_COLOR);
    stock_canvas_set_fill_color(
        canvas,
        cf.stock ? CARD_SCRIM : (CARD_SCRIM >> 24) * (unsigned)ovm_opacity(&cf.mo) / 255 << 24);
    canvas_fill_rect(canvas, 0, 0, I(w, W_W), I(w, W_H));
    stock_canvas_set_fill_color(canvas, fill);
}

/* The wide form's card (confirm_t across): the glyph and the title on one line, the body with the
 * name's characters (cf.lead) in Text Primary, the note. top: the card's top in w. */
static void card_across_paint(void *w, void *canvas, int top) {
    int x = AC_X + AC_IN, tw = AC_W - 2 * AC_IN, y = top + AC_PAD, tx = x, lead = cf.lead;
    rect_t card = { AC_X, top, AC_W, cf.h };
    fill_box(canvas, &card, RGBA(ipod_light() ? 0xffffff : RGB_RAISED), AC_R);
    if (cf.c.glyph) {
        paint_art(w, canvas, theme_art(cf.c.glyph), x, y + (AC_HEAD_H - AC_GLYPH) / 2);
        tx += AC_GLYPH + AC_GLYPH_GAP;
    }
    ui_text(canvas, cf.title, 1, TYPE(20), tok(TOK_PRIMARY), 1, tx, y + AC_HEAD_H / 2, x + tw - tx, 2);
    y += AC_HEAD_H + AC_BODY_GAP;
    for (int i = 0; i < cf.lines; ++i, y += CARD_BODY_LINE) {
        const char *line = cf.body[i];
        int n = (int)strlen(line), lit = lead < n ? lead : n, cy = y + CARD_BODY_LINE / 2, at = x;
        if (lit) { /* the name's part of this line, then the rest after it */
            char part[sizeof cf.body[0]];
            tk_snprintf(part, sizeof part, "%.*s", lit, line);
            at += ui_text(canvas, part, 0, TYPE(15), tok(TOK_PRIMARY), 1, x, cy, tw, 2);
            lead -= lit;
        }
        if (line[lit]) ui_text(canvas, line + lit, 0, TYPE(15), tok(TOK_SECONDARY), 1, at, cy, x + tw - at, 2);
    }
    if (cf.c.note)
        ui_text(canvas, cf.c.note, 0, AC_NOTE_PX, tok(TOK_TERTIARY), 1, x, y + AC_NOTE_GAP + AC_NOTE_H / 2, tw, 2);
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
    if (cf.c.across) {
        card_across_paint(w, canvas, top);
        stock_canvas_set_fill_color(canvas, fill);
        ui_ink_raw(raw);
        return;
    }
    rect_t card = { CARD_X, top, CARD_W, cf.h };
    fill_box(canvas, &card, RGBA(ipod_light() ? 0xffffff : RGB_RAISED), CARD_R);
    int y = top + CARD_PAD_TOP + CARD_TITLE_H / 2;
    if (cf.c.glyph) {
        paint_art(w, canvas, theme_art(cf.c.glyph), cx - CARD_GLYPH / 2, top + CARD_PAD_TOP);
        y += CARD_GLYPH + CARD_GLYPH_GAP;
    }
    card_title(canvas, cx, y, tw, ipod_light() ? 0xffffff : RGB_RAISED);
    y += CARD_TITLE_H / 2;
    if (cf.lines) y += CARD_BODY_GAP;
    for (int i = 0; i < cf.lines; ++i, y += CARD_BODY_LINE)
        ui_text(canvas, cf.body[i], 0, TYPE(15), tok(TOK_SECONDARY), 1, cx, y + CARD_BODY_LINE / 2,
                tw, 1);
    if (cf.code[0])
        ui_text(canvas, cf.code, 1, TYPE(30), tok(TOK_PRIMARY), 1, cx,
                y + CARD_CODE_GAP + CARD_CODE_H / 2, tw, 1);
    if (cf.c.timeout_ms > 0) { /* 2 px along the foot, emptying toward the answer it gives */
        int left = (int)(cf.until - (unsigned)time_now_ms());
        if (left < 0) left = 0;
        rect_t track = { CARD_X + CARD_IN, top + cf.h - CARD_PAD_FOOT / 2 - 1, tw, 2 }, bar = track;
        bar.w = tw * (left / 16) / (cf.c.timeout_ms / 16 + 1); /* no 64-bit divide in the payload */
        fill_box(canvas, &track, RGBA(tok(TOK_OFF)), 0);
        if (bar.w > 0) fill_box(canvas, &bar, RGBA(tok(TOK_TERTIARY)), 0);
    }
    stock_canvas_set_fill_color(canvas, fill);
    ui_ink_raw(raw);
}

/* The wide form's row k (confirm_t across) in the answers' list, its card's left at left: the switch
 * row's words and switch, or an answer's button. The first answer's paint first covers the band
 * with the card, the selection's bar under it too (the answers are rows AC_ANS_H tall), so the two
 * buttons stand on the card as drawn; the selected answer's is the accent, lit or not, so the card
 * always shows which one Centre gives. */
static void card_across_row(void *canvas, void *item, int k, int left, int lit) {
    int y = I(item, W_Y), tw = AC_W - 2 * AC_IN, x = left + AC_IN;
    if (k < 0) {
        int cy = y + AC_SWITCH_H / 2;
        ui_text(canvas, cf.c.toggle, 0, AC_ROW_PX, lit ? 0xffffff : tok(TOK_PRIMARY), 1, x + AC_ROW_IN, cy,
                tw - 2 * AC_ROW_IN - 50, 2); /* clear of the 40 px switch */
        switch_mark(canvas, x + tw - AC_ROW_IN, cy, card_toggle_on(), lit);
        return;
    }
    int top = y - k * AC_ANS_H, half = (tw - AC_GAP) / 2;
    if (k == 0) {
        rect_t band = { I(item, W_X), top, I(item, W_W), AC_BTN_H };
        fill_box(canvas, &band, RGBA(ipod_light() ? 0xffffff : RGB_RAISED), 0);
    }
    rect_t b = { k ? x + half + AC_GAP : x, top, k ? tw - half - AC_GAP : half, AC_BTN_H };
    int on = ringnav_row_selected(item);
    fill_box(canvas, &b, RGBA(on ? scheme()->tones[0] & 0xffffffu : tok(TOK_RAISED_2)), AC_BTN_H / 2);
    ui_text(canvas, cf.c.rows[k], 0, AC_ROW_PX, on ? 0xffffff : tok(TOK_PRIMARY), 1, b.x + b.w / 2,
            top + AC_BTN_H / 2, b.w - 16, 1);
}

/* Over the answers' list after the selection's bar: each row's words centred (a destructive one
 * red, its lit bar red), the switch row's label and switch, the timeout's seconds. */
void confirm_rows_paint(void *w, void *canvas) {
    void *lcd = canvas ? P(canvas, CANVAS_LCD) : (void *)0;
    if (!cf.dialog || w != (cf.stock ? cf.dialog : cf.view) || !lcd) return;
    unsigned fill = (unsigned)I(lcd, LCD_FILL_COLOR), raw = ui_ink_raw(1), red = RGB_NEGATIVE,
             primary = tok(TOK_PRIMARY);
    void *slot[8];
    int n = 0, k = cf.c.toggle ? -1 : 0;
    if (cf.stock) {
        if (cf.check) slot[n++] = cf.check;
        slot[n++] = cf.cell[0], slot[n++] = cf.cell[1];
    } else
        for (unsigned i = 0, m = widget_count_children(w); i < m && n < 8; ++i) {
            void *item = widget_get_child(w, i);
            if (!tk_strcmp(widget_get_type(item), "list_item")) slot[n++] = item;
        }
    for (int i = 0; i < n; ++i, ++k) {
        void *item = slot[i];
        int x = I(item, W_X), y = I(item, W_Y) + CARD_ROW / 2, cx = x + I(item, W_W) / 2,
            out = theme()->sel_inset - CARD_BAR_IN,
            lit = ringnav_row_selected(item) && ui_focus_shown();
        if (cf.c.across) {
            card_across_row(canvas, item, k, x + out, lit);
            continue;
        }
        if (k < 0) {
            ui_text(canvas, cf.c.toggle, 0, TYPE(20), lit ? 0xffffff : primary, 1,
                    x + out + CARD_IN, y, CARD_W - 2 * CARD_IN - 50,
                    2); /* clear of the 40 px switch */
            switch_mark(canvas, x + out + CARD_W - CARD_IN, y, card_toggle_on(), lit);
            continue;
        }
        if (lit && card_destructive(k)) {
            rect_t bar = { x + theme()->sel_inset, I(item, W_Y),
                           I(item, W_W) - 2 * theme()->sel_inset, CARD_ROW };
            fill_box(canvas, &bar, RGBA(red), (unsigned)theme()->sel_radius);
        }
        ui_text(canvas, cf.c.rows[k], 0, TYPE(20),
                lit                   ? 0xffffff
                : card_destructive(k) ? red
                                      : primary,
                1, cx, y, CARD_W - 2 * CARD_IN, 1);
        if (cf.c.timeout_ms > 0 && k == cf.c.timeout_row) {
            char t[16];
            int left = (int)(cf.until - (unsigned)time_now_ms());
            tk_snprintf(t, sizeof t, "%d s", left > 0 ? (left + 999) / 1000 : 0);
            ui_text(canvas, t, 0, CARD_COUNT_PX, lit ? 0xffffff : tok(TOK_TERTIARY), 1,
                    x + out + CARD_W - CARD_IN, y, 60, 3);
        }
    }
    stock_canvas_set_fill_color(canvas, fill);
    ui_ink_raw(raw);
}
