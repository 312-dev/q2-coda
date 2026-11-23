#ifndef Q2_UI_KIT_H
#define Q2_UI_KIT_H
/* ui_kit.c: the design's components (docs/oled-design.md#what-the-engine-needs). Every payload file
 * reaches this through peq.h. */
#include "tokens.h" /* tools/theme.py TOKENS: TOK_*, RGB_* and RGB_*_L */

/* A colour token (TOK_*) in the half Light Mode picks, 0xRRGGBB final. */
unsigned tok(int t) __attribute__((pure));
/* The design's px as AWTK's size, Inter's line being 1.21 em, rounded (docs/oled-design.md#type):
 * 13 is 16, 15 is 18, 16 is 19, 17 is 21, 18 is 22, 20 is 24, 22 is 27, 30 is 36. */
#define TYPE(px) (((px) * 121 + 50) / 100)
/* Show-then-fade (the scroll indicator, the A-Z index): shown UI_SHOW_MS after its last arm,
 * fading over the last UI_FADE_MS, one repaint a UI_FADE_STEP_MS. ui_fade_alpha: 255 until the
 * fade, then down to 0 at UI_SHOW_MS after at (a time_now_ms). */
#define UI_SHOW_MS 1000
#define UI_FADE_MS 200
#define UI_FADE_STEP_MS 40
unsigned ui_fade_alpha(unsigned at);
/* Text on a line (the payload's text painter): UTF-8 s in Inter (Inter Medium when medium) at AWTK
 * size px, in rgb (0xRRGGBB; final when raw), its line centred on y, from x (align 2), ending at x
 * (3) or centred on x (1), at most w wide, an ellipsis ending a longer one; a character Inter lacks
 * takes the theme's fallback face. Returns its width. ui_wide: s's UTF-8 as characters in out (at
 * most n - 1, then 0). ui_measure_w: n characters' width in face at px. */
int ui_text(void *canvas, const char *s, int medium, int px, unsigned rgb, int raw, int x, int y,
            int w, int align);
unsigned ui_wide(const char *s, unsigned *out, unsigned n);
/* Set text (docs/oled-design.md#type): UTF-8 s drawn glyph by glyph at Inter's own advances
 * (stock.h FONT_ADV) plus track thousandths of an em between glyphs, each glyph at its own pen
 * rounded, so its gaps are even: AWTK's own text rounds every advance to a pixel, carrying the
 * error on, and draws a space about 3 px wider than Inter's at 13 px. Faces and colour as ui_text,
 * from x, its row centred on y, never cut or ellipsized (ui_set_in fits it to a column). Returns
 * its width; ui_set_w the width alone. TRACK_CAPTION: the house tracking for the 13 px captions. */
#define TRACK_CAPTION 0
int ui_set(void *canvas, const char *s, int medium, int px, unsigned rgb, int raw, int x, int y,
           int track);
int ui_set_w(void *canvas, const char *s, int medium, int px, int track);
/* ... in the column x to x + w, its row centred on y and px rows tall (AWTK's line): centred when
 * it fits; wider, from the column's left less off (a scroller's xoffset, kept within the overflow),
 * clipped to the column and never ellipsized, its ends fading (list_marks.c text_fade, the
 * marquee's fade: the text itself onto what is behind it): the right while text runs past it, the
 * left while off > 0. Nothing is drawn outside that box. Returns the text's width. */
int ui_set_in(void *canvas, const char *s, int medium, int px, unsigned rgb, int x, int w, int y,
              int off, int track);
int ui_measure_w(void *canvas, const char *face, int px, const unsigned *s, unsigned n);
/* Text in a box: s (n characters, or UTF-8 for ui_line) centred on row y in the columns x to x + w,
 * aligned (1 centre, 2 left, 3 right), in face at px, in color (ui_line_w: 0xAARRGGBB; ui_line:
 * 0xRRGGBB), through ui_ink unless the draw is raw. ui_measure: UTF-8 s's width in face at px. */
void ui_line_w(void *canvas, const unsigned *s, unsigned n, int x, int w, int y, int align,
               const char *face, int px, unsigned color);
void ui_line(void *canvas, const char *s, int x, int w, int y, int align, const char *face, int px,
             unsigned rgb);
int ui_measure(void *canvas, const char *s, const char *face, int px);
/* feedback.c mode_page_paint: a mode page (docs/oled-design.md#usb) in w's coordinates, dy its y
 * on screen; USB's, the sign-in steps', SD Card Update's and Bluetooth's receiver's. MODE_R: the
 * solid circle's radius; MODE_GLYPH: its glyph's size (tools/art.py MODE_GLYPH_PX); MODE_BARE: the
 * ring that draws no circle (a spinner in its place); MODE_BUTTON, or'd into the ring: the pill is
 * the page's one button (Centre presses it), the empty states' secondary button (ui_button),
 * ringed under the focus rule. Returns the stack's top in w's coordinates.
 */
#define MODE_R 46
#define MODE_GLYPH 44
#define MODE_BARE 0x1000000u
#define MODE_BUTTON 0x2000000u
typedef struct {
    unsigned ring;                   /* 0xRRGGBB, or MODE_BARE */
    const char *glyph;               /* theme image stem, white */
    int glyph_px;                    /* 0: MODE_GLYPH */
    const char *title, *line, *pill; /* pill 0: none */
} mode_page_t;
int mode_page_paint(void *w, void *canvas, int dy, const mode_page_t *m);
/* list_marks.c: the loading overlay's spinner (docs/oled-design.md#loading), on window w over what
 * it shows, centred on the whole screen (status row included) whatever w's own y; whether a
 * pointer event's screen point is on it; and a touch on window page while it shows, taken from its
 * rows and cancel called for a lift off it (RET_STOP). */
#define LOAD_SPIN_BOX 64
void load_spinner_paint(void *w, void *canvas);
int load_spinner_hit(int x, int y);
int load_touch(void *page, void *event, void (*cancel)(void));
/* ... and the same overlay over a window another module owns, until a page opens from it
 * (load_over: win 0 ends it), its paint and its input gate (nav/row_paint.c, nav/input.c). */
void load_over(void *win, void (*cancel)(void));
int load_over_for(void (*cancel)(void));
void load_over_paint(void *w, void *canvas);
int load_over_input(void *e);

/* The focus ring's rule (docs/oled-design.md#focus-ring), for every screen with one or two buttons
 * and the confirmation card: shown while the last navigation input was the wheel (a turn, or
 * Centre after one), hidden after a tap, and so on arrival by whichever opened the screen.
 * navigation.c's st.touch_mode is the one record of it: a touch sets it, the wheel clears it.
 * Centre with no ring still answers the screen's default. */
int ui_focus_shown(void);
/* The empty states' buttons (docs/oled-design.md#buttons): one, a filled Accent Blue capsule
 * CTA_H tall sized to its label (CTA_PAD each side, at least CTA_MIN), or a pair of equal capsules
 * CTA_GAP apart, the second grey with its label in Text Primary, together within CTA_BOX; centred
 * on the screen, their top at y. sel: the selected one (-1 none), ringed 2 px in Text Primary
 * CTA_RING_OFF out while ui_focus_shown; pressed darkens its fill a step. ui_cta_x: button i's
 * left, its width in *w. ui_text_w: UTF-8 s's width as ui_text draws it. */
#define CTA_H 38
#define CTA_PX TYPE(17) /* Inter Medium 17 */
#define CTA_PAD 24
#define CTA_MIN 140
#define CTA_PAIR_PAD 20
#define CTA_PAIR_MIN 120
#define CTA_GAP 10
#define CTA_BOX (375 - 48)
#define CTA_INSET 14 /* a label's least room inside its capsule */
#define CTA_RING_OFF 3
#define CTA_RING_W 2                                         /* tools/art.py CTA_RING_W */
#define CTA_RING_H (CTA_H + 2 * (CTA_RING_OFF + CTA_RING_W)) /* tools/art.py CTA_RING_H */
#define CTA_ACCENT_DOWN 0x1a4dad
int ui_text_w(void *canvas, const char *s, int medium, int px);
int ui_cta_w(void *canvas, const char *const *labels, int n);
int ui_cta_x(void *canvas, const char *const *labels, int n, int i, int *w);
void ui_cta_paint(void *canvas, const char *const *labels, int n, int y, int sel, int pressed);
/* text's QR code (ECC level M), its modules dark on a white plate of side px at (x, y), each module
 * a whole number of pixels with a two-module quiet zone */
void ui_qr(void *canvas, const char *text, int x, int y, int px);
/* One button of the same form centred on (cx, cy) (Software Update's, the library scan's): primary
 * the accent with white words (a screen's main action), else the grey with Text Primary words;
 * focused: ringed while ui_focus_shown; pressed: a step darker. Returns its width. */
int ui_button(void *canvas, int cx, int cy, const char *label, int primary, int focused,
              int pressed);
/* A label/value row's split (docs/oled-design.md#labelvalue-rows: About, Storage, Bluetooth's
 * device page, Configuration): the label at its natural width, KV_GAP px, then the value's box to
 * the row's end, the value right-aligned in it. A value wider than its box scrolls in it (the house
 * marquee); a label that leaves the value less than KV_VALUE_MIN shrinks to keep it, and scrolls.
 * span is the row's text width, label_w and value_w the texts' natural widths; *box the value's
 * box width. Returns the label's width. */
#define KV_GAP 16
#define KV_VALUE_MIN 96
int kv_fit(int span, int label_w, int value_w, int *box);
/* feedback.c confirm: the confirmation card (docs/oled-design.md#confirmation-card), a card over
 * the dimmed page that asked. title names the object ("Forget %s?": the one %s is name); body is at
 * most two lines
 * ('\n' between); code a large line ("482 913"); rows the answers top down, the safe one first.
 * kind sets the default row and inks: CONFIRM_DESTRUCTIVE (the last row red, opens on
 * row 0), CONFIRM_CONSEQUENTIAL (opens on row 0), CONFIRM_NEUTRAL (opens on the last row, Return
 * answers row 0), CONFIRM_INFO (one row). back: the row Return answers, -1 for row 0. toggle: a
 * switch row's label above the answers, toggle_on its start. timeout_ms: the card answers
 * timeout_row by itself after this long, the seconds left shown on that row and a bar emptying
 * along the card's foot. answer runs once, after the card has closed, with the chosen row and the
 * switch's state. across: the wide form (adb's Allow this computer?, usb_ui.c): the glyph beside a
 * leading title, the body left-aligned with name (its %s) in Text Primary, note a small line under
 * it, the switch row, then two answers side by side as buttons, the lit one in the accent; it shows
 * no countdown for its timeout. */
enum { CONFIRM_NEUTRAL, CONFIRM_DESTRUCTIVE, CONFIRM_CONSEQUENTIAL, CONFIRM_INFO };
typedef struct {
    const char *title, *name, *body, *code;
    const char *const *rows;
    int n_rows, kind, back;
    const char *toggle;
    int toggle_on;
    int timeout_ms, timeout_row;
    void (*answer)(void *ctx, int row, int toggle_on);
    void *ctx;
    const char *glyph; /* a theme image's name over the title (pair_prompt's phone), or 0 */
    const char *note;  /* across: the line under the body (a key's fingerprint), or 0 */
    int across;        /* the wide form, two answers side by side */
} confirm_t;
int confirm(const confirm_t *c); /* 1: shown; 0: another card is up, or no dialog */
void *confirm_dialog(void);      /* the card's dialog while one is up, else 0 */
void confirm_close(void);        /* closes the card without an answer */
void confirm_words(const char *name, const char *body); /* the card's %s and body anew */
int confirm_up(void);            /* a card is up */
/* Motion (docs/oled-design.md#motion): every animation's duration, curve and timer go through
 * here, so one switch, Reduce Motion (Settings > Display, IPOD REDUCEMOTION), turns them down.
 * A motion has a kind: MO_SPATIAL moves, scales or bounces something (a slide, a spring, a lean)
 * and under Reduce Motion happens at once, or as a MO_FADE_MS cross-fade where it has a fade form
 * (motion_fade_ms, motion_row); MO_DECOR is a loop that says nothing new (the charging sweep, Up
 * Next's level bars) and stands still; MO_FEEDBACK follows a finger or reports a value or progress
 * (swipe back, the volume bar's ease, the marquee, spinners) and always runs.
 * motion_ms: a duration as this kind runs it now (0: at once, or still). motion_fade_ms: a spatial
 * motion's fade form, ms as it is or MO_FADE_MS under the flag. motion_row: the theme's motion row
 * (theme.h motion_t), its reduced form under the flag (a window's slide a fade, a swap or in-place
 * slide immediate, wheel steps as they are). motion_ease: t (0..1000) along AWTK's easing_type_t
 * ease (EASE_*: linear, quadratic and cubic in and out; any other cubic out), 0..1000.
 * An anim_t is one run: anim_start stamps it (its duration through motion_ms), anim_at reads its
 * eased progress 0..1000 (1000 once done, or at once), anim_running whether it still moves. While
 * any runs, one shared timer repaints the top window every MO_FRAME_MS and removes itself
 * MO_FRAME_MS after the last one ends (motion_frames), so a painter needs no timer of its own. */
enum { MO_SPATIAL, MO_DECOR, MO_FEEDBACK };
#define MO_FADE_MS 150
#define MO_OUT_FADE_MS 120
#define MO_FRAME_MS 16
int motion_reduced(void);
int motion_ms(int ms, int kind);
int motion_fade_ms(int ms);
const motion_t *motion_row(int kind);
int motion_ease(int t, int ease);
typedef struct {
    unsigned at;        /* time_now_ms at the start; 0 never started */
    unsigned short ms;  /* as motion_ms gave it */
    unsigned char ease; /* motion_ease's */
} anim_t;
void anim_start(anim_t *a, int ms, int ease, int kind);
int anim_at(const anim_t *a);
int anim_running(const anim_t *a);
void motion_frames(unsigned ms); /* the shared timer repaints for ms more at least */
/* A painter outside the top window (the status row, on the system bar) has fn called on each of
 * the shared timer's frames, to repaint its own box while its run moves, and once more after the
 * last run ends; at most MO_HOOKS, each added once. */
#define MO_HOOKS 2
void motion_hook(void (*fn)(void));
/* A loop that says nothing new (MO_DECOR: the level bars) repaints only its own box: fn, added
 * once, is called every MO_LOOP_MS by one shared loop timer while it returns 1 (it invalidates its
 * box then), and dropped when it returns 0; the timer ends with the last loop. Unlike motion_frames
 * it never repaints the top window. At most MO_LOOPS. */
#define MO_LOOP_MS 33 /* 30 frames a second */
/* the status row's bars, strip and arrows, the volume pill, and Home's and Up Next's bars */
#define MO_LOOPS 6
void motion_loop(int (*fn)(void));
/* The level bars (docs/oled-design.md#level-bars): Up Next's header, Home's first row and the
 * status row draw the one motion. Bar i's height follows its column of LVL_KEYS keyframes
 * LVL_STEP_MS apart, through a Catmull-Rom spline, so it moves continuously at any frame rate and
 * passes each keyframe without stopping; the loop is LVL_KEYS * LVL_STEP_MS. lvl_clock_t is one
 * user's time along it: running, it goes on with time_now_ms; stopped (paused, Reduce Motion) it
 * holds, and running again goes on from where it held, so the bars never jump. Zero is stopped at
 * the loop's start. lvl_h16: bar i's height at t, lo to hi px, in sixteenths. lvl_fill: a bar w px
 * wide standing on base, h16 sixteenths tall, its part-covered top row at that part of its alpha,
 * so a height between pixels shows as one. */
#define LVL_KEYS 10
#define LVL_STEP_MS 125
typedef struct {
    unsigned base, at; /* running: t is now - base; stopped: t is at */
    unsigned char run;
} lvl_clock_t;
void lvl_run(lvl_clock_t *c, int run);
unsigned lvl_time(const lvl_clock_t *c);
int lvl_h16(int i, unsigned t, int lo, int hi);
void lvl_fill(void *canvas, int x, int base, int w, int h16, unsigned rgba);
/* An overlay's enter and exit (docs/oled-design.md#overlay-motion): a toast, the confirmation
 * card, the menu sheet and Up Next in it all move through one ovm_t each, painted from its level.
 * ovm_enter runs it in over ms along ease, ovm_exit out, either from where it is now (a turn
 * mid-run starts there). ovm_level: 0 gone .. 1000 in, eased. ovm_alpha: alpha (a scrim's or a
 * surface's full one) at that level. ovm_shift: px at gone .. 0 in, the rise or fall's offset.
 * ovm_scale: thousandths, from at gone .. 1000 in. ovm_running: still moving; ovm_shown: in, or
 * still leaving. Under Reduce Motion an enter takes MO_FADE_MS and an exit MO_OUT_FADE_MS
 * whatever ms asks, ovm_shift is 0 and ovm_scale 1000: a fade only. */
typedef struct {
    anim_t a;
    short from;        /* the level at the run's start */
    unsigned char out; /* 1 leaving */
} ovm_t;
void ovm_enter(ovm_t *o, int ms, int ease);
void ovm_exit(ovm_t *o, int ms, int ease);
int ovm_level(const ovm_t *o);
int ovm_alpha(const ovm_t *o, int alpha);
int ovm_shift(const ovm_t *o, int px);
int ovm_scale(const ovm_t *o, int from);
int ovm_running(const ovm_t *o);
int ovm_shown(const ovm_t *o);
/* An overlay drawn through its widget's opacity (a toast's or the card's dialog): ovm_bind ties w
 * to o, and the shared frame timer sets w's opacity to ovm_opacity on each frame until w goes (or
 * ovm_bind(o, 0)). Never from w's own paint: stock's widget_paint (demo 0x672848) skips a widget
 * whose opacity is under OVM_PAINT_MIN, so a paint that raised it from 0 would never run.
 * ovm_opacity: 0..255 at o's level; 255 at once for an enter under Reduce Motion. */
#define OVM_PAINT_MIN 9
void ovm_bind(ovm_t *o, void *w);
int ovm_opacity(const ovm_t *o);
#endif
