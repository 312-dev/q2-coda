/* The on-screen keyboard by the wheel, under a list.entry.ipod theme (OLED; docs/ipod.md#keyboard):
 * stock's kb_default_t9, the input method's keyboard window (IM_KEYBOARD), which the Wi-Fi
 * password, an EQ preset's name and the other text boxes open (New Playlist and Rename are the
 * letter strip's, search_strip.c). Its keys sit on the page's black, each
 * a #1C1C1E box with 8 px corners, and the one the wheel is on is the accent's. The wheel walks the
 * keys of the page showing (the candidates first when there are any), Centre presses the key as a
 * tap does (an "abc" key cycles its letters as on a phone), and Return is the box's own: it closes
 * the keyboard, or the box with it.
 *
 * The keys keep stock's layout and labels; only their fill is ours: stock's is cleared once per
 * keyboard (kb_style), and kb_wheel_bg paints in its place under the label stock still draws.
 *
 * Under a list.entry.ipod theme no box keeps it (the user's, 2026-11-17: the letter strip, option
 * P1, for the Wi-Fi password and every other text box): a keyboard seen for the first time gives
 * its window to the letter strip for the same edit, its keys hidden (kb_strip_due, search_strip.c
 * search_strip_box), titled as the page the box is on. The rest of this file is what it was for a
 * keyboard the strip could not take. */
#include "navigation.h"

#define KB_KEY_RGB RGB_RAISED
#define KB_KEY_RADIUS 8
#define KB_KEYS 40 /* a page's keys and its candidates, at most */

static struct {
    void *kb;       /* the keyboard last seen, restyled (kb_style) */
    void *lit;      /* the key the wheel is on, 0 until the wheel turns on this keyboard */
    int at;         /* ... its place among kb_keys' */
    unsigned timer; /* kb_strip_due's */
} kw __attribute__((section(".scratch")));

static void *kb_window(void) {
    void *im = theme()->entry_ipod ? input_method() : (void *)0;
    return im ? P(im, IM_KEYBOARD) : (void *)0;
}

/* The buttons the keyboard shows, in its order, into out (at most max); how many. A pages
 * widget shows only its active child; a hidden widget, nothing. */
static int kb_keys(void *w, void **out, int n, int max) {
    if (!w || !widget_get_visible(w)) return n;
    if (!tk_strcmp(widget_get_type(w), "button")) {
        if (n < max) out[n++] = w;
        return n;
    }
    unsigned count = widget_count_children(w);
    if (!tk_strcmp(widget_get_type(w), "pages")) {
        unsigned active = (unsigned)widget_get_prop_int(w, "active", 0);
        return active < count ? kb_keys(widget_get_child(w, active), out, n, max) : n;
    }
    for (unsigned i = 0; i < count; ++i) n = kb_keys(widget_get_child(w, i), out, n, max);
    return n;
}

/* Whether w is a button of keyboard kb, a key or a candidate, however deep. */
static int kb_owns(void *kb, void *w) {
    if (!kb || !w || tk_strcmp(widget_get_type(w), "button")) return 0;
    for (int i = 0; i < 6 && w; ++i)
        if ((w = P(w, W_PARENT)) == kb) return 1;
    return 0;
}

/* Stock's fills off, the keyboard's and its keys', so the page's black and kb_wheel_bg's keys
 * show; once per keyboard (and again for candidates stock adds while it is up). */
static void kb_clear(void *w) {
    static const char *const state[] = { "normal", "pressed", "over", "disable", "focused" };
    char key[STYLE_KEY];
    for (unsigned s = 0; s < sizeof state / sizeof *state; ++s) {
        widget_set_prop_int(w, style_key(key, state[s], "bg_color"), 0);
        widget_set_prop_int(w, style_key(key, state[s], "border_color"), 0);
    }
    widget_set_prop_int(w, "_kb_clear", 1);
    for (unsigned i = 0, n = widget_count_children(w); i < n; ++i) kb_clear(widget_get_child(w, i));
}
/* The keyboard first seen, from the next loop pass: its window and edit to the letter strip (the
 * input method keeps both, so the box's OK reaches the edit and nothing takes its focus). */
static int kb_strip_due(const void *unused) {
    void *kb = kb_window(), *im = input_method(), *edit = im ? P(im, IM_WIDGET) : (void *)0;
    char title[64] = "";
    kw.timer = 0;
    if (!kb || !edit || !widget_get_visible(kb)) return 0;
    if (st.bar_title) widget_get_text_utf8(st.bar_title, title, sizeof title);
    search_strip_box(kb, edit, title);
    return 0;
}

static void kb_style(void *kb) {
    if (kb == kw.kb) return;
    kw.kb = kb;
    rearm(&kw.timer, kb_strip_due, 0);
    kw.lit = (void *)0;
    kw.at = 0;
    kb_clear(kb);
}

/* ringnav_paint_bg, before stock's: 1 when w is the keyboard or one of its parts and painted
 * here (the black ground, a key's box), so stock's fill is skipped; a key's label and icon are
 * still stock's, drawn after it. */
int kb_wheel_bg(void *w, void *canvas) {
    void *kb = kb_window();
    if (!kb || !w || !P(canvas, CANVAS_LCD)) return 0;
    if (w == kb) kb_style(kb);
    int key = kb_owns(kb, w);
    if (w != kb && !key) return 0;
    if (!widget_get_prop_int(w, "_kb_clear", 0)) kb_clear(w); /* a candidate stock added */
    unsigned fill = (unsigned)I(P(canvas, CANVAS_LCD), LCD_FILL_COLOR), raw = ui_ink_raw(0);
    if (!key)
        fill_box(canvas, &(rect_t){ 0, 0, I(w, W_W), I(w, W_H) }, RGBA(0), 0);
    else if (*widget_get_prop_str(w, "name", "") ||
             wide_len(widget_get_text(w))) /* no empty candidate */
        fill_box(canvas, &(rect_t){ 0, 0, I(w, W_W), I(w, W_H) },
                 RGBA(w == kw.lit ? accent_tone(2) : KB_KEY_RGB), KB_KEY_RADIUS);
    ui_ink_raw(raw);
    stock_canvas_set_fill_color(canvas, fill);
    if (key) stock_paint_bg_trampoline(w, canvas); /* the icon of a key that has one */
    return 1;
}

/* ringnav: the wheel and Centre while the keyboard is up; every other key goes on. */
int kb_wheel_key(unsigned key) {
    void *kb = kb_window(), *keys[KB_KEYS];
    if (!kb || (key != KEY_NEXT && key != KEY_PREV && key != KEY_CENTER)) return 0;
    kb_style(kb);
    int n = kb_keys(kb, keys, 0, KB_KEYS);
    if (!n) return 0;
    if (kw.at >= n) kw.at = n - 1;
    if (key == KEY_CENTER && kw.lit) {
        char click[0x30];
        stock_dispatch_trampoline(keys[kw.at],
                                  pointer_event_init(click, EVT_CLICK, keys[kw.at], 0, 0));
        n = kb_keys(kb, keys, 0, KB_KEYS); /* a page key shows another page */
        if (kw.at >= n) kw.at = n - 1;
    } else if (kw.lit) /* the first turn lights the first key; the next ones move */
        kw.at = clamp_step(kw.at, n - 1, key == KEY_NEXT ? 1 : -1);
    kw.lit = keys[kw.at];
    widget_invalidate_force(kb, (void *)0);
    /* stock's key-up clears the click latch, and ringnav skips it: the next press clicks */
    *(volatile signed char *)KEY_TONE_LATCH = 0;
    return 1;
}
