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
#endif
