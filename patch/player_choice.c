/* The Player row (docs/internals.md#player-choice): Settings > General > "Player", New (q2player,
 * the default) or Stock (Q2 Muse's patched hciplayer, which the image keeps xz-compressed as the
 * fallback). The choice is a file player/main.c looks for each time the player starts, so it takes
 * effect at the next start of the player: the next boot, as General's Restart makes. Switching
 * players mid-song would cut the song and demo's state of it. Under the row a caption says so, or,
 * once q2player has crashed three times in a row this boot (FALLBACK_FILE), that Stock plays until
 * a restart whatever the row says. */
#include "navigation.h"

#define STOCK_SWITCH "/mnt/data/stock-player"  /* player/main.c SWITCH_FILE: there, Stock plays */
#define FALLBACK_FILE "/tmp/q2player.fallback" /* player/main.c: the crash-loop guard tripped */
#define W(s) ((const unsigned *)L##s)

static int stock_chosen(void) { return !access(STOCK_SWITCH, 0); } /* F_OK */

static void player_text(void *label) {
    list_value(label, "Player", stock_chosen() ? "Stock" : "New");
    caption_row(label, access(FALLBACK_FILE, 0)
                           ? W("Applies after a restart")
                           : W("The new player kept crashing: Stock plays until a restart"));
}

/* The row's click: the other player chosen, saved for the player's next start. */
static int player_click(void *label, void *event) {
    int ok = stock_chosen() ? !unlink(STOCK_SWITCH)
                            : blob_io(STOCK_SWITCH, STOCK_SWITCH ".tmp", (void *)"1", 1, 1);
    if (!ok) {
        unlink(STOCK_SWITCH ".tmp");
        toast("Could not save the player choice");
        return 0;
    }
    player_text(label);
    return 0;
}

/* ringnav_systemset (nav/settings_display.c): General's Player row, after USB. */
void player_choice_row(void *view) {
    if (!view) return;
    void *label = list_row(view, "playset_folderjump", 0, 0);
    widget_on(P(label, W_PARENT), EVT_CLICK, player_click, label);
    player_text(label);
}
