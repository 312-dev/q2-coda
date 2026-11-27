/* Settings (docs/ipod.md, Home's Settings list in demo: tools/ipod.py HOME_SETS), in demo's order.
 * Each row opens demo's page for it through the gate (ui_demo_page): stock's settings pages by
 * their path, as stock's own rows open them, and Car Remote and Controls by the payload's openers
 * (patch/q2ui_gate.c openers). As q2ui gets pages of its own (docs/ui-rewrite.md, step 4), a row's
 * demo name gives way to its page. */
#include "q2ui.h"

static const struct {
    const char *title, *demo;
} rows[] = { { "Wi-Fi", "systemset/wifi_page" },         { "Bluetooth", "systemset/bluetooth_page" },
             { "General", "systemset/sysset_page" },     { "Playback", "playset/playset_page" },
             { "Streaming", "stream_page" },             { "Car Remote", "carremote_page" },
             { "Display", "systemset/display_page" },    { "Controls", "controls_page" },
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
    ui_demo_page(rows[i].demo);
}
void settings_push(void) {
    ui_source src = { .count = count, .row = row, .chosen = chosen, .name = "settings" };
    ui_push(&list_page, &src);
}
