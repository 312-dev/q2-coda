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
#endif
