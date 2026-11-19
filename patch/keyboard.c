/* Stock's on-screen keyboard, gone (the user's, 2026-11-18: "strip their keyboard out of codebase
 * completely"; docs/ipod.md#keyboard, docs/internals.md#the-keyboard). Every keyboard stock opens
 * is opened by its input method's request (input_method_default's show, the two window_open calls
 * that tools/build.py KB_OPEN_SITES names, one for the edit's keyboard and one for its fallback,
 * kb_default_t9): both call kb_host_open instead, which opens a bare keyboard window, no keys, over
 * the screen under the status row, and gives it to the letter strip there and then
 * (search_strip.c search_strip_box), so its first frame is the strip's: the box's text is typed in
 * place, and the box's OK reaches the edit as the keyboard's Enter did. The input method's slide up
 * is never set (kb_open_hint). The window is a keyboard (stock's g_keyboard_vtable), so the input
 * method keeps the edit focused under it, as a page of its own would not, and its close
 * (input_method_request with no edit, the box's OK) ends the strip with it (kb_gone, search_strip.c
 * ss_closed). tools/build.py drops the keyboard's page, style and images from the image. */
#include "navigation.h"

static struct {
    void *kb; /* the keyboard window kb_host_open last opened */
} kw __attribute__((section(".scratch")));

/* The keyboard's window and the input method's edit to the letter strip (the input method keeps
 * both, so the box's OK reaches the edit and nothing takes its focus), titled as the page under. */
static void kb_take(void *kb) {
    void *im = input_method(), *edit = im ? P(im, IM_WIDGET) : (void *)0;
    char title[64] = "";
    if (!kb || kb != kw.kb || !edit) return;
    if (st.bar_title) widget_get_text_utf8(st.bar_title, title, sizeof title);
    search_strip_box(kb, edit, title);
}

/* The keyboard's window going: forgotten, so the next one is new even at its address (stock's
 * input method frees each keyboard as it closes, the next often at the same). */
static int kb_gone(void *ctx, void *event) {
    kw.kb = (void *)0;
    return 0;
}

/* The input method's keyboard window, in place of stock's window_open(name) (KB_OPEN_SITES): a bare
 * keyboard under the status row, named as stock's would be, the strip's before it returns. */
void *kb_host_open(const char *name) {
    int top = geom_val(V_BAR);
    void *kb = window_base_create((void *)0, g_keyboard_vtable, 0, top, 375, 320 - top);
    if (!kb) return kb;
    if (name) widget_set_name(kb, name);
    kw.kb = kb;
    widget_on(kb, EVT_DESTROY, kb_gone, 0);
    kb_take(kb);
    return kb;
}

/* The input method's widget_set_prop_str(keyboard, "open_anim_hint", ...) after it opens one
 * (tools/build.py KB_OPEN_HINT): nothing set, so the window shows in place at once with the strip
 * on it, not sliding up from the screen's foot. */
int kb_open_hint(void *kb, const char *name, const char *value) { return 0; }

/* ringnav_paint, in place of stock's border paint: 1 when w is the keyboard window, which gets none.
 * With no style of its own (tools/build.py drops keyboard.bin) stock drew the theme's default
 * border round it: a light line round the strip's page from the status row's foot down. */
int kb_host_border(void *w) { return w && w == kw.kb; }

/* ringnav_paint_bg, before stock's: the keyboard window's ground, black, under the strip's sheet (a
 * bare keyboard has no style of its own to fill it). 1 when w is that window. */
int kb_host_bg(void *w, void *canvas) {
    if (!w || w != kw.kb || !P(canvas, CANVAS_LCD)) return 0;
    unsigned fill = (unsigned)I(P(canvas, CANVAS_LCD), LCD_FILL_COLOR), raw = ui_ink_raw(0);
    fill_box(canvas, &(rect_t){ 0, 0, I(w, W_W), I(w, W_H) }, RGBA(0), 0);
    ui_ink_raw(raw);
    stock_canvas_set_fill_color(canvas, fill);
    return 1;
}
