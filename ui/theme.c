/* The theme's colours (docs/oled-design.md#color): tools/theme.py's tokens, each with its Light
 * Mode half, chosen where a colour is used. */
#include "q2ui.h"

int ui_light;
static const uint32_t tokens[TOK_N][2] = UI_TOKENS;

uint32_t tok(int token) { return tokens[token][ui_light != 0]; }
