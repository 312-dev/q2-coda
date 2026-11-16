/* The design's shared components (docs/oled-design.md#what-the-engine-needs): its colour tokens by
 * half, and the primitives every painter draws with. The tokens' values are tools/theme.py's
 * TOKENS, written to tokens.h, which tools/art.py inks its glyphs from too. */
#include "navigation.h"

static const unsigned ui_tokens[TOK_N][2] = UI_TOKENS;

unsigned tok(int t) { return ui_tokens[t][ipod_light() ? 1 : 0]; }
