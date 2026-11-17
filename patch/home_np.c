/* Home's first row, led by the play state (docs/ipod.md#home, docs/oled-design.md#home; approved
 * as 2a on 2026-11-16, design/home-np-icon): stock's Now Playing row (btn_playing, its
 * label_playing translated by name and img_playing bound by stock) reads Playing while something
 * plays, Paused while it is paused and Guest Play while a guest plays (guest.c guest_active); it is
 * there only while one of those holds (library_edit.c lr_home_apply, coverflow.c np_loaded). Its
 * glyph leads it at the text column, the status row's play state moved here while the row shows
 * (status_row.c play_shown): Up Next's level bars (upnext.c un_bars, 3 px wide, 4.5 px apart, up to
 * 12 px, a frame every HN_FRAME_MS, still under Reduce Motion) in the accent while playing or a
 * guest plays, Phosphor pause's two bars (4 x 12 px, 2 px apart) in Text Secondary while paused,
 * either white on the selection. Only this row's words move: they start HN_TEXT_DX after the
 * glyph's column (the label's left margin), the other rows' stay. In Edit Home the row reads
 * Playing, with no glyph, as the editor lists it. The switch between the two glyphs is a cut
 * (hn_glyph); ponytail: the approved morph is not built (design/home-np-icon/mock_transition.py).
 */
#include "navigation.h"

#define HN_FRAME_MS 125 /* the bars' frame, Up Next's UN_BARS_MS */
#define HN_GLYPH_W 12   /* the bars' box: 3 + 2 x 4.5 */
#define HN_TEXT_DX 18   /* the words after the glyph's column: its 12 px and a 6 px gap */
#define HN_MARGIN 2     /* hscroll_label's own left margin, the text column's start */
#define HN_BASE 6       /* the glyph stands on the row's middle + this, 12 px high at most */

enum { HN_PLAYING, HN_PAUSED, HN_GUEST };

static struct {
    void *win, *row, *img,
        *label;              /* Home, its Now Playing row, the row's click target, its words */
    void *click, *click_ctx; /* stock's click on img (home_page_bind_widget's) and its ctx */
    unsigned timer;
    int frame, state, editing;
} hn __attribute__((section(".scratch")));

static int hn_state(void) {
    if (guest_active()) return HN_GUEST;
    return media_status() == 2 ? HN_PLAYING : HN_PAUSED;
}

static void hn_margin(void *label, int px) {
    static const char *const keys[] = { "style:normal:margin_left", "style:focused:margin_left",
                                        "style:pressed:margin_left", "style:over:margin_left" };
    for (unsigned i = 0; i < sizeof keys / sizeof *keys; ++i)
        widget_set_prop_int(label, keys[i], px);
}

/* The row's words for its state, and their margin past the glyph: library_edit.c lr_home_apply on
 * each Home layout (editing: Edit Home's), and hn_tick when the state changes. */
static void hn_words(void) {
    void *label = hn.label;
    if (!label) return;
    static const char *const words[] = { "Playing", "Paused", "Guest Play" };
    widget_set_text_utf8(label, hn.editing ? words[HN_PLAYING] : words[hn.state]);
    hn_margin(label, hn.editing ? HN_MARGIN : HN_MARGIN + HN_TEXT_DX);
}
void home_np_row(void *row, int editing) {
    void *label = row ? widget_lookup(row, "label_playing", 1) : (void *)0;
    if (!label) return;
    hn.row = row, hn.label = label, hn.editing = editing, hn.state = hn_state();
    hn_words();
}

/* Whether the row leads with the play state now: Home on top, its main list in sight and the row
 * shown (status_row.c leaves its play slot empty then). */
int home_np_lead(void) {
    void *top = window_manager_get_top_window(window_manager());
    if (!hn.win || top != hn.win || !hn.row || hn.editing || !widget_get_visible(hn.row)) return 0;
    void *list = P(P(hn.row, W_PARENT), W_PARENT); /* the scroll view's list_view_home */
    return list && widget_get_visible(list);
}

/* Every HN_FRAME_MS while Home is open: the words follow the state; the bars step while they play
 * and the row is in sight (MO_DECOR: still under Reduce Motion, motion_ms 0). */
static int hn_tick(const void *info) {
    (void)info;
    if (!hn.win) return hn.timer = 0;
    int state = hn_state();
    if (state != hn.state && !hn.editing) {
        hn.state = state;
        hn_words();
        if (hn.img) widget_invalidate_force(hn.img, (void *)0);
    }
    if (state != HN_PAUSED && home_np_lead() && motion_ms(HN_FRAME_MS, MO_DECOR) && hn.img) {
        ++hn.frame;
        widget_invalidate_force(hn.img, (void *)0);
    }
    return 8; /* RET_REPEAT */
}

static int hn_gone(void *win, void *event) {
    (void)event;
    if (win != hn.win) return 0;
    stop_timer(&hn.timer);
    memset(&hn, 0, sizeof hn);
    return 0;
}

/* Stock's click, unless a guest plays: then the guest's Now Playing (guest_ui.c guest_home_np). */
static int hn_click(void *ctx, void *event) {
    (void)ctx;
    if (guest_home_np() || !hn.click) return 0;
    return ((int (*)(void *, void *))hn.click)(hn.click_ctx, event);
}

/* coverflow_home, after stock bound Home's widgets: stock's click on img_playing swapped for
 * hn_click, and the row's timer. */
void home_np_bind(void *win) {
    void *img = win ? widget_lookup(win, "img_playing", 1) : (void *)0;
    if (!img) return;
    if (hn.win && hn.win != win) hn_gone(hn.win, (void *)0);
    hn.win = win, hn.img = img;
    void *emitter = P(img, W_EMITTER);
    for (void *e = emitter ? P(emitter, 0) : (void *)0; e; e = P(e, EMIT_NEXT))
        if (I(e, EMIT_TYPE) == EVT_CLICK && !B(e, EMIT_PENDING_REMOVE)) {
            hn.click = P(e, 12), hn.click_ctx = P(e, 0);
            widget_off_by_func(img, EVT_CLICK, hn.click, hn.click_ctx);
            break;
        }
    widget_on(img, EVT_CLICK, hn_click, (void *)0);
    widget_on(win, EVT_DESTROY, hn_gone, win);
    if (!hn.timer) hn.timer = timer_add(hn_tick, (void *)0, HN_FRAME_MS);
}

/* The glyph at x (the text column) standing on base: the bars, or the pause's two bars. The one
 * place the playing and paused glyphs switch: a cut. */
static void hn_glyph(void *canvas, int x, int base, int state, unsigned rgb) {
    if (state == HN_PAUSED) {
        for (int i = 0; i < 2; ++i) {
            rect_t r = { x + 1 + i * 6, base - 12, 4, 12 };
            fill_box(canvas, &r, RGBA(rgb), 1);
        }
        return;
    }
    static const unsigned char h[4][3] = { { 7, 11, 5 }, { 10, 6, 9 }, { 5, 9, 11 }, { 9, 12, 6 } };
    for (int i = 0; i < 3; ++i) {
        int bh = h[hn.frame & 3][i];
        rect_t r = { x + i * 9 / 2, base - bh, 3, bh };
        fill_box(canvas, &r, RGBA(rgb), 1);
    }
}

/* ringnav_paint, after a widget's own paint: the glyph, over img_playing (the row's top child) at
 * its label's text column. */
void home_np_paint(void *w, void *canvas) {
    if (!w || w != hn.img || !hn.label || hn.editing || !P(canvas, CANVAS_LCD)) return;
    int lit = ringnav_row_selected(hn.img), light = ipod_light();
    unsigned rgb = lit                     ? tok(TOK_ON_ACCENT)
                   : hn.state == HN_PAUSED ? (light ? RGB_SECONDARY_L : RGB_SECONDARY)
                                           : scheme()->tones[0] & 0xffffffu;
    int x = I(hn.label, W_X) - I(w, W_X) + HN_MARGIN, base = I(w, W_H) / 2 + HN_BASE;
    hn_glyph(canvas, x, base, hn.state, rgb);
}
