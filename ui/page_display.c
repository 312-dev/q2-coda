/* Settings > Display, the first of q2ui's own settings pages (docs/ui-rewrite.md, step 4): Light
 * Mode (Off, On, Auto: light from 7:00 to 19:00) and Reduce Motion, which q2ui itself draws by, then
 * the rest of demo's Display page through the gate. A setting's value is the file's
 * (ui/config.c reads demo's config.ini whenever it is written); a choice goes to demo, which owns
 * the file, through the command queue ("set light N", "set motion N", patch/control_center.c
 * q2ui_cc_set), and the row shows it at once until the file says so too. */
#include <stdio.h>

#include "q2ui.h"

enum { D_LIGHT, D_MOTION, D_MORE, D_ROWS };
static int want[2] = { -1, -1 }; /* a choice made here, until the file has it */
static int value(int i) {
    int now = i == D_LIGHT ? config_light_setting() : config_motion_setting();
    if (want[i] >= 0 && want[i] == now) want[i] = -1; /* demo has written it */
    return want[i] >= 0 ? want[i] : now;
}
static int count(void *ctx) {
    (void)ctx;
    return D_ROWS;
}
static void row(void *ctx, int i, ui_row *out) {
    (void)ctx;
    static const char *const lights[] = { "Off", "On", "Auto" };
    if (i == D_LIGHT) {
        int v = value(D_LIGHT);
        out->title = "Light Mode", out->value = lights[v >= 0 && v <= 2 ? v : 0];
    } else if (i == D_MOTION)
        out->title = "Reduce Motion", out->flags = ROW_SWITCH | (value(D_MOTION) ? ROW_ON : 0);
    else
        out->title = "More Display Settings", out->flags = ROW_CHEVRON;
}
static void chosen(void *ctx, int i) {
    (void)ctx;
    if (i == D_MORE) {
        ui_demo_page("systemset/display_page");
        return;
    }
    char verb[24];
    int next = i == D_LIGHT ? (value(D_LIGHT) + 1) % 3 : !value(D_MOTION);
    snprintf(verb, sizeof verb, "set %s %d", i == D_LIGHT ? "light" : "motion", next);
    if (player_send(verb)) return;
    want[i] = next;
    list_rows_changed();
    ui_mark_all();
}
void display_push(void) {
    want[0] = want[1] = -1;
    ui_source src = { .count = count, .row = row, .chosen = chosen, .name = "display" };
    ui_push(&list_page, &src);
}
