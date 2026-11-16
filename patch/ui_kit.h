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
 * ring that draws no circle (a spinner in its place). Returns the stack's top in w's coordinates.
 */
#define MODE_R 46
#define MODE_GLYPH 44
#define MODE_BARE 0x1000000u
typedef struct {
    unsigned ring;                   /* 0xRRGGBB, or MODE_BARE */
    const char *glyph;               /* theme image stem, white */
    int glyph_px;                    /* 0: MODE_GLYPH */
    const char *title, *line, *pill; /* pill 0: none */
} mode_page_t;
int mode_page_paint(void *w, void *canvas, int dy, const mode_page_t *m);
/* feedback.c confirm: the confirmation card (docs/oled-design.md#confirmation-card), a card over
 * the dimmed page that asked. title names the object ("Forget %s?": the one %s is name); body is at
 * most two lines
 * ('\n' between); code a large line ("482 913"); rows the answers top down, the safe one first.
 * kind sets the default row and inks: CONFIRM_DESTRUCTIVE (the last row red after a gap, opens on
 * row 0), CONFIRM_CONSEQUENTIAL (opens on row 0), CONFIRM_NEUTRAL (opens on the last row, Return
 * answers row 0), CONFIRM_INFO (one row). back: the row Return answers, -1 for row 0. toggle: a
 * switch row's label above the answers, toggle_on its start. timeout_ms: the card answers
 * timeout_row by itself after this long, the seconds left shown on that row and a bar emptying
 * along the card's foot. answer runs once, after the card has closed, with the chosen row and the
 * switch's state. */
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
#define CTA_ACCENT_DOWN 0x1a4dad
int ui_text_w(void *canvas, const char *s, int medium, int px);
int ui_cta_w(void *canvas, const char *const *labels, int n);
int ui_cta_x(void *canvas, const char *const *labels, int n, int i, int *w);
void ui_cta_paint(void *canvas, const char *const *labels, int n, int y, int sel, int pressed);
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
} confirm_t;
int confirm(const confirm_t *c); /* 1: shown; 0: another card is up, or no dialog */
void confirm_close(void);        /* closes the card without an answer */
int confirm_up(void);            /* a card is up */
#endif
