/* Bluetooth Stopped (docs/oled-design.md#bluetooth, docs/internals.md#bluetooth-boot-reconnect):
 * when the Bluetooth controller never answers (stock's bt_initstatus BT_ABNORMAL, system.c
 * reboot_record), stock's idle loop toasts "Bluetooth abnormal" and calls device_reboot at once.
 * Here that call (ringnav_reboot, playback.c, from on_wm_idle_status) opens a page that says what
 * happened and that the Q2 restarts to fix it, counting down BS_COUNT seconds, with Not Now:
 * Centre, Return or a tap keeps the Q2 running without Bluetooth until it restarts some other way,
 * and stock's later calls (its loop asks again each pass that finds the radio dead) do nothing.
 * Stock's toast before each call is not shown (bt_stopped_toast): the page says it.
 * The emulator has no radio at all, so with BLUETOOTH=1 in its config.ini stock always reaches
 * BT_ABNORMAL: there the page is not opened and the restart waits for ever (EMU_MARK). Which
 * machine this is comes from libq2emu.so, the emulator's shim, present in its guest and on no Q2,
 * not from the radio being absent: a Q2 whose radio has failed still gets the page. */
#include "navigation.h"

#define BT_ABNORMAL (-102) /* system.c's */
#define BS_COUNT 10        /* seconds before the restart */
#define BS_TICK_MS 1000
#define EMU_MARK "/lib/libq2emu.so" /* the emulator's LD_PRELOAD shim (~/q2-emu-server/run.sh) */

static struct {
    void *page, *caller; /* the page; the stock call that asked to restart (reboot_record's) */
    unsigned timer;
    int left, declined, restarting, emulator; /* emulator: 0 unknown, 1 no, 2 yes */
} bs __attribute__((section(".scratch")));

int ringnav_reboot_from(void *caller); /* playback.c: the reboot itself */

static int bs_tick(const void *unused) {
    (void)unused;
    if (!bs.page) return bs.timer = 0, 0;
    if (--bs.left > 0) {
        widget_invalidate_force(bs.page, (void *)0);
        return 8; /* RET_REPEAT */
    }
    bs.timer = 0, bs.restarting = 1;
    ringnav_reboot_from(bs.caller);
    return 0;
}

static int bs_closed(void *ctx, void *event) {
    (void)event;
    if (ctx == bs.page) {
        bs.page = (void *)0;
        stop_timer(&bs.timer);
        if (!bs.restarting) bs.declined = 1; /* Not Now */
    }
    return 0;
}
/* Not Now: Centre, Return or a tap. */
static int bs_key(void *ctx, void *event) {
    (void)ctx;
    unsigned key = (unsigned)I(event, EVENT_KEY);
    if (key != KEY_CENTER && key != KEY_RETURN) return 0;
    window_close(bs.page);
    return STOP;
}
static int bs_tap(void *ctx, void *event) {
    (void)ctx, (void)event;
    if (bs.page) window_close(bs.page);
    return STOP;
}

/* on_wm_idle_status's "Bluetooth abnormal" toast before its device_reboot (tools/build.py
 * BT_ABNORMAL_TOASTS): none, as the page says it, and none after Not Now. */
int bt_stopped_toast(const char *name, void *ctx) {
    (void)name, (void)ctx;
    return 0;
}

/* playback.c ringnav_reboot, before it restarts: 1 when the restart waits on the page (or the user
 * said Not Now, or this is the emulator), 0 to restart now. */
int bt_stopped_hold(void *caller) {
    if (I(bt_initstatus, 0) != BT_ABNORMAL || bs.restarting) return 0;
    if (!bs.emulator) bs.emulator = access(EMU_MARK, 0) ? 1 : 2;
    if (bs.emulator == 2) return 1; /* no radio to wait for, no page, no restart */
    if (bs.declined || bs.page) return 1;
    if (!(bs.page = page_open("btstopped_page", bs_closed, bs_key))) return 0;
    widget_on(bs.page, EVT_CLICK, bs_tap, 0);
    bs.caller = caller, bs.left = BS_COUNT;
    bs.timer = timer_add(bs_tick, (void *)0, BS_TICK_MS);
    return 1;
}

/* The bar's hidden title, the page's name; 0 for another page. */
const char *bt_stopped_title(const char *name) {
    return tk_strcmp(name, "btstopped_page") ? (const char *)0 : "Bluetooth";
}

/* ringnav_paint, after w's children: the page as a mode page, the warning's orange circle. */
void bt_stopped_paint(void *w, void *canvas) {
    if (!w || w != bs.page || window_manager_is_animating(window_manager())) return;
    void *lcd = P(canvas, CANVAS_LCD);
    if (!lcd) return;
    unsigned fill = (unsigned)I(lcd, LCD_FILL_COLOR), text = (unsigned)I(lcd, LCD_TEXT_COLOR),
             raw = st.raw_ink;
    char line[48];
    tk_snprintf(line, sizeof line, "Restarting the Q2 to fix it in %d s", bs.left);
    st.raw_ink = 1;
    mode_page_paint(
        w, canvas, I(w, W_Y),
        &(mode_page_t){ tok(TOK_WARNING), "modepage_bad", 0, "Bluetooth Stopped", line, "Not Now" });
    st.raw_ink = raw;
    stock_canvas_set_fill_color(canvas, fill);
    stock_canvas_set_text_color(canvas, text);
}
