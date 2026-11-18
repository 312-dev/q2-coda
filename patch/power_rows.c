/* General's last three rows, Restart, Shut Down and Reset Settings (docs/ipod.md#settings), the first two after a 12 px gap: each
 * asks in stock's confirm (confirminfo_dialog, whatever form the theme gives it; OLED's says the
 * verb, feedback.c asks[]), and Cancel or Return leaves the page as it was. Restart calls
 * ringnav_reboot, which saves the queue and ends cleanly before stock's device_reboot. Shut Down takes stock's: its power-off page, which
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

/* reset, the Reset Settings row, goes last, directly under Shut Down, in Negative red: it loses
 * settings (docs/oled-design.md#menus, destructive rows). Its chevron stays, as it opens a page.
 * Shut Down is no loss, so it keeps the text color (the confirm card's consequential kind). */
void power_rows(void *view, void *reset) {
    if (!view) return;
    widget_set_text_utf8(label_create(view, 0, 0, 0, POWER_GAP), "");
    widget_set_text_utf8(list_row(view, "system_powermanager", power_restart, 0), "Restart");
    widget_set_text_utf8(list_row(view, "system_powermanager", power_off, 0), "Shut Down");
    if (!reset) return;
    widget_restack(reset, (int)widget_count_children(view) - 1);
    void *button = widget_count_children(reset) ? widget_get_child(reset, 0) : (void *)0;
    for (unsigned i = 0, n = button ? widget_count_children(button) : 0; i < n; ++i) {
        void *c = widget_get_child(button, i);
        if (!tk_strcmp(widget_get_type(c), "hscroll_label")) {
            text_ink(c, 0xff000000u | tok(TOK_NEGATIVE), INK_LABEL);
            break;
        }
    }
}
