/* General's last two rows, Restart and Shut Down (docs/ipod.md#settings), after a 12 px gap: each
 * asks in stock's confirm (confirminfo_dialog, whatever form the theme gives it; OLED's says the
 * verb, feedback.c asks[]), and Cancel or Return leaves the page as it was. Restart takes the
 * theme change's way (navigation.c restart_confirm): ringnav_reboot, which saves the queue and
 * ends cleanly before stock's device_reboot. Shut Down takes stock's: its power-off page, which
 * the power key's Shut Down? (on_wm_keylong_fun) and its Auto power-off open, and whose
 * into_poweroff saves the queue (playback.c ringnav_savequeue). */
#include "navigation.h"

#define POWER_GAP 12 /* before the destructive rows (docs/oled-design.md#menus) */

extern int ringnav_reboot(void); /* playback.c */

/* Stock's confirm struct: kind 1, a question with Cancel and OK; 10 is OK. */
static int power_ask(const char *question, int stock_words) {
    struct {
        int kind, ms;
        char text[0x400];
    } prompt = { 1, 0, "" };
    if (stock_words)
        getFormatString(question, prompt.text, sizeof prompt.text);
    else
        tk_snprintf(prompt.text, sizeof prompt.text, "%s", question);
    return navigator_to_with_context("dialog/confirminfo_dialog", &prompt) == 10;
}
static int power_restart(void *ctx, void *event) {
    if (power_ask("Restart?", 0)) ringnav_reboot();
    return 0;
}
static int power_off(void *ctx, void *event) {
    if (power_ask("msg_poweroff", 1)) navigator_to("poweroff_page");
    return 0;
}

void power_rows(void *view) {
    if (!view) return;
    widget_set_text_utf8(label_create(view, 0, 0, 0, POWER_GAP), "");
    widget_set_text_utf8(list_row(view, "system_powermanager", power_restart, 0), "Restart");
    widget_set_text_utf8(list_row(view, "system_powermanager", power_off, 0), "Shut Down");
}
