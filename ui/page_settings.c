/* Settings (docs/ipod.md, Home's Settings list in demo: tools/ipod.py HOME_SETS), in demo's order.
 * Each row opens demo's page for it through the gate (ui_demo_page): stock's settings pages by
 * their path, as stock's own rows open them, and Car Remote and Controls by the payload's openers
 * (patch/q2ui_gate.c openers). As q2ui gets pages of its own (docs/ui-rewrite.md, step 4), a row's
 * demo name gives way to its page: Display (page_display.c) and Playback (page_playback.c) are
 * q2ui's. */
#include "q2ui.h"

static const struct {
    const char *title, *demo; /* demo 0: a page of q2ui's, opened by push */
    void (*push)(void);
} rows[] = { { "Wi-Fi", "systemset/wifi_page" },         { "Bluetooth", "systemset/bluetooth_page" },
             { "General", "systemset/sysset_page" },     { "Playback", 0, playback_push },
             { "Streaming", "stream_page" },             { "Car Remote", "carremote_page" },
             { "Display", 0, display_push },    { "Controls", "controls_page" },
             { "Battery", "systemset/powermanager_page" } };
#define ROWS ((int)(sizeof rows / sizeof *rows))

static int count(void *ctx) {
    (void)ctx;
    return ROWS;
}
static void row(void *ctx, int i, ui_row *out) {
    (void)ctx;
    out->title = rows[i].title, out->flags = ROW_CHEVRON;
}
static void chosen(void *ctx, int i) {
    (void)ctx;
    if (!rows[i].demo)
        rows[i].push();
    else
        ui_demo_page(rows[i].demo);
}
void settings_push(void) {
    ui_source src = { .count = count, .row = row, .chosen = chosen, .name = "settings" };
    ui_push(&list_page, &src);
}
