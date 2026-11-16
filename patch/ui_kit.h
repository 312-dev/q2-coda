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
 * solid circle's radius; MODE_GLYPH: its glyph's size (tools/art.py USB_MODE_PX); MODE_BARE: the
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
