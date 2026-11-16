/* One pairing presentation (docs/oled-design.md#pairing): a phone pairing with the Q2, Car Remote's
 * (coda_rc_shim.c) or a Guest Play guest's (guest_ui.c), asks on the confirm card (feedback.c
 * confirm) in one form: the phone's glyph, "Pair with <phone>?" (scrolling when long, never cut in
 * the status row), what to do with the code, the code large in two groups of three, the caller's
 * answers, and a timeout's bar when the pairing expires. pair_done says how it ended. */
#include "navigation.h"

/* the phone over the title: tools/art.py's Phosphor device-mobile, PAIR_GLYPH_PX, the accent */
#define PAIR_GLYPH "pairing_phone"

static char pair_name[64] __attribute__((section(".scratch"))); /* the phone, for pair_done */

/* The card for phone (0 or "": "Phone"), code (6 digits: "482 913"; else as given, or 0 for none)
 * and body; rows, back, the timeout and answer as confirm's. Opens on the first row. Returns the
 * card's dialog, or 0 when another card is up. */
void *pair_prompt(const char *phone, const char *code, const char *body, const char *const *rows,
                  int n_rows, int back, int timeout_ms, int timeout_row,
                  void (*answer)(void *ctx, int row, int toggle_on), void *ctx) {
    char spaced[12];
    tk_snprintf(pair_name, sizeof pair_name, "%s", phone && *phone ? phone : "Phone");
    int six = code && strlen(code) == 6;
    if (six) tk_snprintf(spaced, sizeof spaced, "%.3s %.3s", code, code + 3);
    confirm_t c = { "Pair with %s?",
                    pair_name,
                    body,
                    six             ? spaced
                    : code && *code ? code
                                    : 0,
                    rows,
                    n_rows,
                    CONFIRM_CONSEQUENTIAL,
                    back,
                    0,
                    0,
                    timeout_ms,
                    timeout_row,
                    answer,
                    ctx,
                    PAIR_GLYPH };
    return confirm(&c) ? confirm_dialog() : (void *)0;
}

/* The pairing's end: "Paired with <phone>", or "Pairing failed". */
void pair_done(int paired) {
    char said[96];
    tk_snprintf(said, sizeof said, "Paired with %s", pair_name);
    toast(paired ? said : "Pairing failed");
}
