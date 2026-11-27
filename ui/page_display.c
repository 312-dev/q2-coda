/* Settings > Display, the first of q2ui's own settings pages (docs/ui-rewrite.md, step 4): Light
 * Mode (Off, On, Auto: light from 7:00 to 19:00) and Reduce Motion, which q2ui itself draws by, then
 * the rest of demo's Display page through the gate. A setting's value is the file's
 * (ui/config.c reads demo's config.ini whenever it is written); a choice goes to demo, which owns
 * the file, down q2ui's own pipe ("set light N", "set motion N", ui/player.c player_set,
 * patch/control_center.c q2ui_cc_set), and the row shows it at once until the file says so too. */
#include <stdio.h>

#include "../patch/night_scale.h"
#include "q2ui.h"

enum { D_LIGHT, D_MOTION, D_NIGHT, D_WARMTH, D_MORE, D_ROWS };
static int want[4] = { -1, -1, -1, -1 }; /* a choice made here, until the file has it */
static char warmth[12];
static int value(int i) {
    int now = i == D_LIGHT ? config_light_setting() : i == D_MOTION ? config_motion_setting()
              : i == D_NIGHT ? config_night_setting() : config_night_kelvin();
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
        out->title = "Reduce Motion", out->flags = ROW_SWITCH | (value(D_MOTION) ? ROW_ON : 0),
        out->caption = "Fades in place of slides, zooms and bounces"; /* patch/settings_pages.c reduce_motion_caption */
    else if (i == D_NIGHT) {
        int v = value(D_NIGHT);
        out->title = "Night Shift", out->value = lights[v >= 0 && v <= 2 ? v : 0];
    } else if (i == D_WARMTH) { /* its warmth in every mode, greyed while Off (docs/oled-design.md, the slider page) */
        int k = value(D_WARMTH);
        snprintf(warmth, sizeof warmth, "%d K", k);
        out->title = "Warmth", out->value = warmth, out->level = (float)(k - NS_WARM) / (NS_COOL - NS_WARM);
        out->flags = ROW_SLIDER | (value(D_NIGHT) ? 0 : ROW_GREY);
    } else
        out->title = "More Display Settings", out->flags = ROW_CHEVRON;
}
static void chosen(void *ctx, int i) {
    (void)ctx;
    if (i == D_MORE) {
        ui_demo_page("systemset/display_page");
        return;
    }
    if (i == D_WARMTH) return; /* Centre grabs it (list.c) */
    int next = i == D_LIGHT ? (value(D_LIGHT) + 1) % 3 : i == D_NIGHT ? (value(D_NIGHT) + 1) % 3 : !value(D_MOTION);
    if (player_set(i == D_LIGHT ? "light" : i == D_NIGHT ? "night" : "motion", next)) return;
    want[i] = next;
    list_rows_changed();
    ui_mark_all();
}
/* the grabbed Warmth row turned: NS_STEP a step, clockwise more kelvin, as demo's sliders (patch/inline_slider.c) */
static void adjust(void *ctx, int i, int steps) {
    (void)ctx;
    if (i != D_WARMTH) return;
    int k = value(D_WARMTH) + steps * NS_STEP;
    k = k < NS_WARM ? NS_WARM : k > NS_COOL ? NS_COOL : k;
    if (k == value(D_WARMTH) || player_set("nightk", k)) return;
    want[D_WARMTH] = k;
    list_rows_changed();
}
void display_push(void) {
    want[0] = want[1] = want[2] = want[3] = -1;
    ui_source src = { .count = count, .row = row, .chosen = chosen, .adjust = adjust, .name = "display" };
    ui_push(&list_page, &src);
}
