/* The theme's colours (docs/oled-design.md#color): tools/theme.py's tokens, each with its Light
 * Mode half, chosen where a colour is used, and the accent the Accent setting picks (UI_ACCENT_KEY
 * in [IPOD], OLED_ACCENT: tools/theme.py ui_accents writes each value's colours into
 * patch/tokens.h). Text in the accent and a caption on it are the chosen accent's, not the tokens'
 * (which are the default's). */
#include "q2ui.h"

int ui_light;
static const uint32_t tokens[TOK_N][2] = UI_TOKENS;
static const uint32_t accents[UI_ACCENT_N][5] = UI_ACCENTS;
const uint32_t *ui_accent = accents[UI_ACCENT_DEFAULT];

/* a value the theme does not offer (a zero row, or out of range) reads as the default, as demo's
 * settings_read has it */
int accent_set_value(int v) {
    const uint32_t *was = ui_accent;
    ui_accent = accents[v >= 0 && v < UI_ACCENT_N && accents[v][0] ? v : UI_ACCENT_DEFAULT];
    return ui_accent != was;
}

uint32_t tok(int token) {
    if (token == TOK_ACCENT_TEXT) return ui_accent[ui_light ? 3 : 2];
    if (token == TOK_CAPTION_ON_ACCENT) return ui_accent[4];
    return tokens[token][ui_light != 0];
}
