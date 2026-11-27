/* Settings > Display, the first of q2ui's own settings pages (docs/ui-rewrite.md, step 4): Brightness
 * first, as demo's page has it (an inline slider, demo's word on it its level: control_center.c
 * q2ui_cc_state's bright=, set as Control Center's slider sets it, "set bright N"), then Light
 * Mode (Off, On, Auto: light from 7:00 to 19:00), Reduce Motion, Night Shift and its Warmth, and the song
 * rows' Song Artist and Song Length, which q2ui itself draws by, then the rest of demo's Display page through
 * the gate. A setting's value is the file's
 * (ui/config.c reads demo's config.ini whenever it is written); a choice goes to demo, which owns
 * the file, down q2ui's own pipe ("set light N", "set motion N", "set songartist N", ui/player.c player_set,
 * patch/control_center.c q2ui_cc_set), and the row shows it at once until the file says so too. */
#include <stdio.h>
#include <stdlib.h>

#include "../patch/night_scale.h"
#include "q2ui.h"

enum { D_LIGHT, D_MOTION, D_NIGHT, D_WARMTH, D_BRIGHT, D_ARTIST, D_LENGTH, D_MORE, D_ROWS };
static int want[D_MORE] = { -1, -1, -1, -1, -1, -1, -1 }; /* a choice made here, until the file (or demo's word) has it */
static char warmth[12], bright[8];
static int told_bright(void) {
    char v[16];
    int b = status_told_value("bright", v, sizeof v) && *v ? atoi(v) : 0;
    return b < 0 ? 0 : b > 100 ? 100 : b;
}
static int value(int i) {
    int now = i == D_LIGHT ? config_light_setting() : i == D_MOTION ? config_motion_setting()
              : i == D_NIGHT ? config_night_setting() : i == D_BRIGHT ? told_bright() : i == D_ARTIST ? config_song_artist()
              : i == D_LENGTH ? config_song_length() : config_night_kelvin();
    if (want[i] >= 0 && want[i] == now) want[i] = -1; /* demo has written it */
    return want[i] >= 0 ? want[i] : now;
}
static int count(void *ctx) {
    (void)ctx;
    return D_ROWS;
}
/* the list's row i is which setting: Brightness first, as on demo's page */
static int at(int i) { return i == 0 ? D_BRIGHT : i <= D_BRIGHT ? i - 1 : i; }
static void row(void *ctx, int i, ui_row *out) {
    (void)ctx;
    static const char *const lights[] = { "Off", "On", "Auto" };
    i = at(i);
    if (i == D_BRIGHT) { /* inline_slider.c IS_BRIGHT: 0 to 100, its value as a percentage */
        int b = value(D_BRIGHT);
        snprintf(bright, sizeof bright, "%d%%", b);
        out->title = "Brightness", out->value = bright, out->level = (float)b / 100, out->flags = ROW_SLIDER;
    } else if (i == D_LIGHT) {
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
    } else if (i == D_ARTIST) { /* the song rows' options, OLED's own on demo's page (theme.xml) */
        static const char *const how[] = { "Inline", "Selected Row", "Right", "Off" };
        int v = value(D_ARTIST);
        out->title = "Song Artist", out->value = how[v >= 0 && v <= SONG_ARTIST_OFF ? v : 0];
    } else if (i == D_LENGTH) {
        static const char *const lists[] = { "Albums", "All Lists", "Off" };
        int v = value(D_LENGTH);
        out->title = "Song Length", out->value = lists[v >= 0 && v <= SONG_LENGTH_OFF ? v : 0];
    } else
        out->title = "More Display Settings", out->flags = ROW_CHEVRON;
}
static void chosen(void *ctx, int i) {
    (void)ctx;
    i = at(i);
    if (i == D_BRIGHT) return; /* Centre grabs it (list.c) */
    if (i == D_MORE) {
        ui_demo_page("systemset/display_page");
        return;
    }
    if (i == D_WARMTH) return; /* Centre grabs it (list.c) */
    int next = i == D_LIGHT    ? (value(D_LIGHT) + 1) % 3
               : i == D_NIGHT  ? (value(D_NIGHT) + 1) % 3
               : i == D_ARTIST ? (value(D_ARTIST) + 1) % (SONG_ARTIST_OFF + 1)
               : i == D_LENGTH ? (value(D_LENGTH) + 1) % (SONG_LENGTH_OFF + 1)
                               : !value(D_MOTION);
    static const char *const verb[] = { [D_LIGHT] = "light", [D_MOTION] = "motion", [D_NIGHT] = "night",
                                        [D_ARTIST] = "songartist", [D_LENGTH] = "songlength" };
    if (player_set(verb[i], next)) return;
    want[i] = next;
    list_rows_changed();
    ui_mark_all();
}
/* a grabbed slider turned, clockwise more, as demo's sliders (patch/inline_slider.c): Brightness by its steps,
 * Warmth NS_STEP kelvin a step */
static void adjust(void *ctx, int i, int steps) {
    (void)ctx;
    i = at(i);
    if (i == D_BRIGHT) { /* a step a tick, more on a fast spin (list.c), as demo's bright_wheel */
        int b = value(D_BRIGHT) + steps;
        b = b < 0 ? 0 : b > 100 ? 100 : b;
        if (b == value(D_BRIGHT) || player_set("bright", b)) return;
        want[D_BRIGHT] = b;
        list_rows_changed();
        return;
    }
    if (i != D_WARMTH) return;
    int k = value(D_WARMTH) + steps * NS_STEP;
    k = k < NS_WARM ? NS_WARM : k > NS_COOL ? NS_COOL : k;
    if (k == value(D_WARMTH) || player_set("nightk", k)) return;
    want[D_WARMTH] = k;
    list_rows_changed();
}
void display_push(void) {
    for (int i = 0; i < D_MORE; ++i) want[i] = -1;
    ui_source src = { .count = count, .row = row, .chosen = chosen, .adjust = adjust, .name = "display" };
    ui_push(&list_page, &src);
}
