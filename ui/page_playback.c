/* Settings > Playback, q2ui's own (docs/ui-rewrite.md, step 4): EQ first, as demo's Playback has
 * it, opening demo's EQ page through the gate (patch/peq_ui.c peq_page_init, at stock's
 * playset/equalizer_page), whose Enabled switch, as Control Center's EQ switch, turns it on and
 * off. Then Gapless and Smart
 * Crossfade as switches, as the file has them ([PLAYSET] GAPLESS, [Q2POD] XFADE, ui/config.c reads
 * demo's config.ini whenever it is written). A flip goes to demo down q2ui's own pipe ("set
 * gapless|xfade 0|1", ui/player.c player_set, patch/control_center.c q2ui_cc_set), which sets it
 * as demo's own row does, and the row shows it at once until demo says so too. Smart Crossfade on
 * turns Gapless on with it, as demo's row does (patch/crossfade.c xfade_set). Last, the Sleep Timer
 * and the rest of demo's Playback page through the gate. */
#include "q2ui.h"

enum { P_EQ, P_GAPLESS, P_XFADE, P_SLEEP, P_MORE, P_ROWS };
static int want[3] = { -1, -1, -1 }; /* a flip made here, until demo has it (EQ's slot unused) */
static int value(int i) {
    int now = i == P_GAPLESS ? config_int("PLAYSET", "GAPLESS", 0) == 1 : config_int("Q2POD", "XFADE", 0) == 1;
    if (want[i] >= 0 && want[i] == now) want[i] = -1; /* demo has it */
    return want[i] >= 0 ? want[i] : now;
}
static int count(void *ctx) {
    (void)ctx;
    return P_ROWS;
}
static void row(void *ctx, int i, ui_row *out) {
    (void)ctx;
    static const char *const titles[] = { "EQ", "Gapless", "Smart Crossfade", "Sleep Timer", "More Playback Settings" };
    out->title = titles[i];
    out->flags = i > P_EQ && i < P_SLEEP ? ROW_SWITCH | (value(i) ? ROW_ON : 0) : ROW_CHEVRON;
}
static void chosen(void *ctx, int i) {
    (void)ctx;
    static const char *const names[] = { 0, "gapless", "xfade" };
    static const char *const pages[] = { "playset/equalizer_page", 0, 0, "systemset/sleepshutdown_page",
                                         "playset/playset_page" };
    if (pages[i]) {
        ui_demo_page(pages[i]);
        return;
    }
    int next = !value(i);
    if (player_set(names[i], next)) return;
    want[i] = next;
    if (i == P_XFADE && next) want[P_GAPLESS] = 1;
    list_rows_changed();
    ui_mark_all();
}
void playback_push(void) {
    want[0] = want[1] = want[2] = -1;
    ui_source src = { .count = count, .row = row, .chosen = chosen, .name = "playback" };
    ui_push(&list_page, &src);
}
