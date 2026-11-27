/* Settings (docs/ipod.md, Home's Settings list in demo: tools/ipod.py HOME_SETS), in demo's order.
 * Each row opens demo's page for it through the gate (ui_demo_page): stock's settings pages by
 * their path, as stock's own rows open them, and Car Remote and Controls by the payload's openers
 * (patch/q2ui_gate.c openers). As q2ui gets pages of its own (docs/ui-rewrite.md, step 4), a row's
 * demo name gives way to its page: Wi-Fi (page_wifi.c), Bluetooth (page_bluetooth.c), General
 * (page_general.c), Playback (page_playback.c) and Display (page_display.c) are q2ui's. */
#include <string.h>

#include "q2ui.h"

static const struct {
    const char *title, *demo; /* demo 0: a page of q2ui's, opened by push */
    void (*push)(void);
} rows[] = { { "Wi-Fi", 0, wifi_push },                 { "Bluetooth", 0, bluetooth_push },
             { "General", 0, general_push },     { "Playback", 0, playback_push },
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
/* A page of Settings' by its name in a route (main.c state_restore): opened, 1; 0 a name not
 * Settings'. About is General's. */
int settings_route(const char *name) {
    static const struct {
        const char *name;
        void (*push)(void);
    } pages[] = { { "settings", settings_push }, { "wifi", wifi_push },         { "bluetooth", bluetooth_push },
                  { "general", general_push },   { "about", about_push },       { "playback", playback_push },
                  { "display", display_push } };
    for (unsigned i = 0; i < sizeof pages / sizeof *pages; ++i)
        if (!strcmp(name, pages[i].name)) return pages[i].push(), 1;
    return 0;
}
void settings_push(void) {
    ui_source src = { .count = count, .row = row, .chosen = chosen, .name = "settings" };
    ui_push(&list_page, &src);
}
