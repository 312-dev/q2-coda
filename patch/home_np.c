/* Home's first row, led by the play state (docs/ipod.md#home, docs/oled-design.md#homes-first-row;
 * approved as 2a with its morph on 2026-11-16, design/home-np-icon): stock's Now Playing row
 * (btn_playing, its label_playing translated by name and img_playing bound by stock) reads Playing
 * while something plays, Paused while it is paused and Guest Play while a guest plays (guest.c
 * guest_active); it is there only while one of those holds (library_edit.c lr_home_apply,
 * coverflow.c np_loaded). Its glyph leads it at the text column, the status row's play state moved
 * here while the row shows (status_row.c play_shown): Up Next's level bars (upnext.c un_bars, 3 px
 * wide, 4.5 px apart, HN_BAR_LO to 12 px, ui_kit.h's one level-bar motion repainting only the
 * glyph's box every MO_LOOP_MS, still under Reduce Motion) in the
 * accent while playing, Phosphor pause's two bars (4 x 12 px, 2 px apart) in Text Secondary while
 * paused, either white on the selection; a guest's the same by the guest's state. Only this row's
 * words move: they start HN_TEXT_DX after the glyph's column (the label's left margin), the other
 * rows' stay. In Edit Home the row reads Playing, with no glyph, as the editor lists it.
 *
 * Between playing and paused the glyph morphs over HN_MORPH_MS on the house ease-out (hn_glyph at
 * hn_p): the bars freeze, the middle one narrows and fades, the outer two widen and rise into the
 * pause's bars, the accent turning Text Secondary; back the same way, then the bars move again. The
 * words cross-fade in the same time: the old ones on a copy of the label (hn.was) fading out over
 * the new ones fading in, both through their widgets' opacity, set by the shared frame timer
 * (ui_kit.c ovm_bind), never from a paint. Under Reduce Motion both are a cut. */
#include "navigation.h"

#define HN_BAR_LO 5     /* the bars' least height, Up Next's UN_BAR_LO */
#define HN_MORPH_MS 250 /* bars <-> pause, and the words' cross-fade */
#define HN_TEXT_DX 18   /* the words after the glyph's column: its 12 px and a 6 px gap */
#define HN_MARGIN 2     /* hscroll_label's own left margin, the text column's start */
#define HN_BASE 6       /* the glyph stands on the row's middle + this, 12 px high at most */
#define HN_PAUSE_H 12   /* the pause's bars: 4 px wide at x 1 and 7 of the glyph's 12 */

enum { HN_PLAYING, HN_PAUSED, HN_GUEST };

static struct {
    void *win, *row, *img, *label, *was; /* Home, its first row, the click target, the words, the
                                            old words while they fade */
    void *click, *click_ctx; /* stock's click on img (home_page_bind_widget's), its ctx */
    lvl_clock_t clock;       /* the bars' time along their loop (ui_kit.h lvl_h16) */
    int state, editing;
    anim_t morph;       /* the glyph's run from p_from to p_to */
    short p_from, p_to; /* 0 the bars, 1000 the pause */
    ovm_t in, out;      /* the words' cross-fade: the new ones' level, the old ones' */
} hn __attribute__((section(".scratch")));

static int hn_state(void) {
    if (guest_active()) return HN_GUEST;
    return now_status() == 1 ? HN_PLAYING : HN_PAUSED; /* the owner's (now_source.c) */
}
/* Whether the glyph is the pause: the owner's paused, or the guest's */
static int hn_paused(int state) {
    if (state != HN_GUEST) return state == HN_PAUSED;
    char s[12];
    guest_field("state", s, sizeof s);
    return !tk_strcmp(s, "paused");
}
/* The glyph now: 0 the bars .. 1000 the pause, eased */
static int hn_p(void) { return hn.p_from + (hn.p_to - hn.p_from) * anim_at(&hn.morph) / 1000; }

static void hn_margin(void *label, int px) {
    static const char *const keys[] = { "style:normal:margin_left", "style:focused:margin_left",
                                        "style:pressed:margin_left", "style:over:margin_left" };
    for (unsigned i = 0; i < sizeof keys / sizeof *keys; ++i)
        widget_set_prop_int(label, keys[i], px);
}
static const char *const hn_text[] = { "Playing", "Paused", "Guest Play" };

/* The old words gone: the copy destroyed, the fades unbound, the words whole. */
static void hn_fade_done(void) {
    ovm_bind(&hn.in, (void *)0);
    ovm_bind(&hn.out, (void *)0);
    if (hn.was) widget_destroy(hn.was), hn.was = (void *)0;
    if (hn.label) widget_set_opacity(hn.label, 255);
}

/* The row's words for its state, and their margin past the glyph: library_edit.c lr_home_apply on
 * each Home layout (editing: Edit Home's), at once. */
void home_np_row(void *row, int editing) {
    void *label = row ? widget_lookup(row, "label_playing", 1) : (void *)0;
    if (!label) return;
    if (label != hn.label) hn_fade_done();
    hn.row = row, hn.label = label, hn.editing = editing, hn.state = hn_state();
    hn.p_from = hn.p_to = hn_paused(hn.state) ? 1000 : 0, hn.morph.at = 0;
    widget_set_text_utf8(label, editing ? hn_text[HN_PLAYING] : hn_text[hn.state]);
    hn_margin(label, editing ? HN_MARGIN : HN_MARGIN + HN_TEXT_DX);
}

/* The words from the old ones to state's: a cross-fade, the old on a copy of the label over it. */
static void hn_words_to(int state) {
    void *label = hn.label, *parent = label ? P(label, W_PARENT) : (void *)0;
    if (!label) return;
    hn_fade_done();
    if (!motion_reduced() && parent) {
        void *was = hscroll_label_create(parent, I(label, W_X), I(label, W_Y), I(label, W_W),
                                         I(label, W_H));
        if (was) {
            widget_use_style(was, widget_get_prop_str(label, "style", "default"));
            widget_set_text_utf8(was, hn_text[hn.state]);
            hn_margin(was, HN_MARGIN + HN_TEXT_DX);
            widget_set_sensitive(was, 0);
            hn.was = was;
            hn.out.a.at = 0, hn.in.a.at = 0;
            ovm_enter(&hn.out, 0, EASE_CUBIC_OUT); /* in, at once: then out */
            ovm_exit(&hn.out, HN_MORPH_MS, EASE_CUBIC_OUT);
            ovm_enter(&hn.in, HN_MORPH_MS, EASE_CUBIC_OUT);
            ovm_bind(&hn.out, was);
            ovm_bind(&hn.in, label);
        }
    }
    hn.state = state;
    widget_set_text_utf8(label, hn_text[state]);
}

/* Whether the row leads with the play state now: Home on top, its main list in sight and the row
 * shown. */
int home_np_lead(void) {
    void *top = window_manager_get_top_window(window_manager());
    if (!hn.win || top != hn.win || !hn.row || hn.editing || !widget_get_visible(hn.row)) return 0;
    void *list = P(P(hn.row, W_PARENT), W_PARENT); /* the scroll view's list_view_home */
    return list && widget_get_visible(list);
}

/* Whether the bars move now: lit, playing, the morph done, the row in sight and motion allowed
 * (MO_DECOR: still under Reduce Motion). Their clock runs only then. */
static int hn_moving(void) {
    return hn.img && hn.label && !hn.p_to && !anim_running(&hn.morph) && g_backlight_status &&
           home_np_lead() && motion_ms(LVL_STEP_MS, MO_DECOR);
}
/* The glyph's box on img_playing: the text column, HN_PAUSE_H and the bars' part-covered top row
 * above base, 13 px wide (the outer bar's right edge at 12). */
static rect_t hn_box(void) {
    int x = I(hn.label, W_X) - I(hn.img, W_X) + HN_MARGIN, base = I(hn.img, W_H) / 2 + HN_BASE;
    return (rect_t){ x, base - HN_PAUSE_H - 1, 13, HN_PAUSE_H + 1 };
}
/* The shared loop timer's (ui_kit.c motion_loop), MO_LOOP_MS apart while the bars move: the
 * glyph's box repainted at the clock's time; 0 (dropped) once they stop. */
static int hn_frame(void) {
    int on = hn_moving();
    lvl_run(&hn.clock, on);
    if (on) {
        rect_t r = hn_box();
        widget_invalidate_force(hn.img, &r);
    }
    return on;
}

/* coverflow.c coverflow_home_np, on every bar paint (each second at least, and as the play state
 * changes): a change of state starts the morph and the words' cross-fade (the shared frame timer
 * paints them, ui_kit.c motion_frames), and the bars' loop runs while they move. */
void home_np_sync(void) {
    if (!hn.win) return;
    if (hn.was && !ovm_running(&hn.out)) hn_fade_done();
    int state = hn_state();
    if (!hn.editing && hn.label) {
        int to = hn_paused(state) ? 1000 : 0;
        if (to != hn.p_to) {
            hn.p_from = (short)hn_p(), hn.p_to = (short)to;
            anim_start(&hn.morph, HN_MORPH_MS, EASE_CUBIC_OUT, MO_SPATIAL);
            if (hn.img) widget_invalidate_force(hn.img, (void *)0);
        }
        if (state != hn.state) hn_words_to(state);
    }
    int on = hn_moving();
    lvl_run(&hn.clock, on);
    if (on) motion_loop(hn_frame);
}

static int hn_gone(void *win, void *event) {
    (void)event;
    if (win != hn.win) return 0;
    ovm_bind(&hn.in, (void *)0);
    ovm_bind(&hn.out, (void *)0);
    memset(&hn, 0, sizeof hn);
    return 0;
}

/* Stock's click, unless a guest plays: then the guest's Now Playing (guest_ui.c guest_home_np);
 * or Radio owns Now Playing: its page (radio_tuner.c tuner_open). */
static int hn_click(void *ctx, void *event) {
    (void)ctx;
    if (guest_home_np()) return 0;
    if (now_owner() == NOW_RADIO) return tuner_open((void *)0, (void *)0), STOP;
    if (!hn.click) return 0;
    return ((int (*)(void *, void *))hn.click)(hn.click_ctx, event);
}

/* coverflow_home, after stock bound Home's widgets: stock's click on img_playing swapped for
 * hn_click. */
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
}

static int hn_mix(int a, int b, int p) { return a + (b - a) * p / 1000; }
static unsigned hn_mix_rgb(unsigned a, unsigned b, int p) {
    unsigned out = 0;
    for (int s = 0; s < 24; s += 8)
        out |= (unsigned)hn_mix((int)(a >> s & 0xff), (int)(b >> s & 0xff), p) << s;
    return out;
}

/* The glyph at x (the text column) standing on base, at p (0 the bars, 1000 the pause; between,
 * the morph: design/home-np-icon/mock_transition.py glyph), in rgb. */
static void hn_glyph(void *canvas, int x, int base, int p, unsigned rgb) {
    unsigned t = lvl_time(&hn.clock);
    for (int i = 0; i < 3; ++i) { /* left to right, as Up Next's */
        unsigned ink = RGBA(rgb);
        int h16 = lvl_h16(i, t, HN_BAR_LO, HN_PAUSE_H); /* sixteenths, lvl_fill's */
        if (i == 1) {                                   /* the middle one narrows and fades */
            int wm = hn_mix(3, 0, p);
            if (wm <= 0) continue;
            ink = (ink & 0xffffffu) | (unsigned)(255 * (1000 - p) / 1000) << 24;
            lvl_fill(canvas, x + 4 + (3 - wm) / 2, base, wm, hn_mix(h16, HN_PAUSE_H * 8, p), ink);
        } else { /* the outer two: x 0 and 9 (4.5 apart) to the pause's 1 and 7, 3 to 4 px wide */
            lvl_fill(canvas, x + hn_mix(i ? 9 : 0, i ? 7 : 1, p), base, hn_mix(3, 4, p),
                     hn_mix(h16, HN_PAUSE_H * 16, p), ink);
        }
    }
}

/* ringnav_paint, after a widget's own paint: the glyph, over img_playing (the row's top child) at
 * its label's text column. */
void home_np_paint(void *w, void *canvas) {
    if (!w || w != hn.img || !hn.label || hn.editing || !P(canvas, CANVAS_LCD)) return;
    int p = hn_p(), lit = ringnav_row_selected(hn.img), light = ipod_light();
    unsigned rgb = lit ? tok(TOK_ON_ACCENT)
                       : hn_mix_rgb(scheme()->tones[0] & 0xffffffu,
                                    light ? RGB_SECONDARY_L : RGB_SECONDARY, p);
    rect_t box = hn_box();
    hn_glyph(canvas, box.x, box.y + HN_PAUSE_H + 1, p, rgb);
}
