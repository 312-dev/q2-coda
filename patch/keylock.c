/* Buttons Lock as the iPod's Hold switch (docs/internals.md#buttons-lock), under OLED's pages:
 * while it is on, a padlock sits in the status row (status_row.c's SLOT_LOCK, left of the others),
 * and a press the lock blocks with the screen off lights the screen for KEYLOCK_CARD_MS with a card
 * over the page dimmed: a padlock, "Buttons Locked" and how to get past it (keylock_gate,
 * keylock_paint). The lock is still stock's, the dark screen's alone (tools/build.py KEYLOCK_LIT):
 * Centre wakes the screen and the buttons work while it is lit, so Centre on the card is that wake,
 * and the card says so. Without OLED's pages a blocked press stays stock's, dropped in the dark. */
#include "navigation.h"

#define KEYLOCK_CARD_MS 1500
#define KEYLOCK_DIM 0x8c000000u /* the page at 45% of its light, the card's ground */
/* The card, in screen pixels (the mock-up's, design px times 1.21 for AWTK's type): 185 x 140 with
 * 22 px corners round the screen's middle, the 56 px padlock centred on y 140, the title's line on
 * y 192 and the hint's on y 212. */
#define CARD_X 95
#define CARD_Y 92
#define CARD_W 185
#define CARD_H 140
#define CARD_R 22
#define CARD_RGB RGB_RAISED
#define CARD_GLYPH 56
#define CARD_GLYPH_Y (140 - CARD_GLYPH / 2)
#define CARD_TITLE_Y 192
#define CARD_TITLE_PX TYPE(16) /* Inter Medium 16 */
#define CARD_HINT_Y 212
#define CARD_HINT_PX TYPE(13) /* Inter Regular 13 */
#define CARD_LINE 22

static struct {
    unsigned eat;   /* the keys whose press was dropped here: their long press and release go too */
    unsigned timer; /* the card's end */
    int shown;      /* the card is up, the screen lit for it */
} kl __attribute__((section(".scratch")));

/* A key's bit in kl.eat: Return, Play/Pause, the wheel's two, the previous and next buttons, and
 * Centre; 0 for any other. */
static unsigned key_bit(unsigned key) {
    return key >= KEY_RETURN && key <= KEY_NEXT ? 1u << (key - KEY_RETURN)
           : key == KEY_BACK_BTN                ? 1u << 4
           : key == KEY_FWD_BTN                 ? 1u << 5
           : key == KEY_CENTER                  ? 1u << 6
                                                : 0;
}

/* Buttons Lock is Off or On (navigation.c lock_set saves On as SYSSET LOCKMODE 2): the dark wheel
 * sets nothing (volume.c vol_wm_keyup), so stock's modes that lock it, 1 (Volume only) and 3
 * (Everything), lock nothing more. One saved by an earlier version is made one of the two once at
 * boot, before or after config_init reads it (playback.c ringnav_bootmode): 1, whose buttons were
 * free, is Off; any other is On. */
void keylock_boot(void) {
    if (!config_value("SYSSET", "LOCKFLAG", 2, 0)) return;
    int mode = config_value("SYSSET", "LOCKMODE", 4, 3);
    if (mode == 1) {
        g_keylock_flag = 0;
        write_int_config(0, "SYSSET", "LOCKFLAG");
    } else if (mode != 2) {
        g_keylock_mode = 2;
        write_int_config(2, "SYSSET", "LOCKMODE");
    }
}

/* What stock's dark filter drops (on_wm_keyup_before_fun, 0x4f2660) for SYSSET
 * LOCKMODE: 1 the wheel, 2 Return, Play/Pause and the previous and next buttons, 3 both; any other
 * mode is 3. keylock_boot leaves only 2. */
static int locked(unsigned key) {
    if (!g_keylock_flag) return 0;
    int mode = g_keylock_mode, wheel = key == KEY_PREV || key == KEY_NEXT;
    int buttons = key == KEY_RETURN || key == KEY_PLAY || key == KEY_BACK_BTN || key == KEY_FWD_BTN;
    return (mode != 2 && wheel) || (mode != 1 && buttons);
}

static void card_off(void) {
    stop_timer(&kl.timer);
    kl.shown = 0;
    widget_invalidate_force(window_manager(), (void *)0);
}

/* The card's time is up: the screen goes off as stock's own timeout turns it off
 * (check_to_poweroption): the flag cleared, then screen_action(0). */
static int card_end(const void *info) {
    (void)info;
    kl.timer = 0;
    kl.shown = 0;
    if (g_backlight_status) {
        g_backlight_status = 0;
        screen_action(0);
    }
    return 0; /* RET_OK: a timer's end */
}

/* A blocked press in the dark: the screen lights as stock's awake_screen lights it, but painted
 * first, the card on it, so the panel never shows the page left at screen-off. */
static void card_on(void) {
    void *wm = window_manager();
    kl.shown = 1;
    g_backlight_status = 1; /* the paint gate (ringnav_wm_paint) opens */
    widget_invalidate_force(wm, (void *)0);
    ringnav_wm_paint(wm);
    enable_fb();
    usleep(50000); /* awake_screen's settle before screen_action */
    screen_action(1);
    rearm(&kl.timer, card_end, KEYLOCK_CARD_MS);
}

/* Whether a press of key now is the lock's: held back, its card shown dark (keylock_gate). */
int keylock_holds(unsigned key) {
    return key_bit(key) && locked(key) && !video_on() && !addon_game_on();
}

/* ringnav_input, after the wake gate: 1 drops the event. remote: SyncLink's play control
 * (ringnav_post_key), which the lock never holds. */
int keylock_gate(void *e, int remote) {
    unsigned type = (unsigned)I(e, EVENT_TYPE), key = (unsigned)I(e, EVENT_KEY);
    if (kl.shown && type >= EVT_POINTER_FIRST && type <= EVT_POINTER_ABORT)
        return 1; /* a touch on the card: the page under it is out of reach */
    if (type != EVT_KEY_DOWN && type != EVT_KEY_LONG && type != EVT_KEY_UP) return 0;
    unsigned bit = key_bit(key);
    if (!bit || remote) return 0;
    if (kl.eat & bit) { /* a dropped press's long press and release */
        if (type == EVT_KEY_UP) kl.eat &= ~bit;
        return 1;
    }
    if (type != EVT_KEY_DOWN) return 0;
    if (kl.shown && g_backlight_status) {
        if (key == KEY_CENTER) { /* the wake: the card goes, the screen stays lit */
            kl.eat |= bit;
            card_off();
            reset_poweroptions_timer(1, 1, 1);
            return 1;
        }
        if (!locked(key)) return 0;
        kl.eat |= bit;
        rearm(&kl.timer, card_end, KEYLOCK_CARD_MS);
        return 1;
    }
    if (g_backlight_status || !locked(key) || video_on() || addon_game_on()) return 0;
    kl.eat |= bit;
    card_on();
    return 1;
}

/* ringnav_paint, after stock painted w: over the top window and the status bar while the card is
 * up, the dim (overlay_scrim); over the window, the card. */
void keylock_paint(void *w, void *canvas) {
    int dy;
    if (!kl.shown || !w || !overlay_scrim(w, canvas, KEYLOCK_DIM, &dy)) return;
    unsigned fill = (unsigned)I(P(canvas, CANVAS_LCD), LCD_FILL_COLOR);
    int light = ipod_light();
    rect_t card = { CARD_X, CARD_Y - dy, CARD_W, CARD_H };
    fill_box(canvas, &card, RGBA(flip_if(light, CARD_RGB)), CARD_R);
    char glyph[32];
    tk_snprintf(glyph, sizeof glyph, "%skeylock_card_%c", theme()->art, light ? 'l' : 'd');
    paint_art(w, canvas, glyph, CARD_X + (CARD_W - CARD_GLYPH) / 2, CARD_GLYPH_Y - dy);
    rect_t title = { CARD_X, CARD_TITLE_Y - CARD_LINE / 2 - dy, CARD_W, CARD_LINE },
           hint = { CARD_X, CARD_HINT_Y - CARD_LINE / 2 - dy, CARD_W, CARD_LINE };
    final_text(canvas, "inter-medium", (const unsigned *)L"Buttons Locked", &title, CARD_TITLE_PX,
               light ? 0xff000000u : 0xffffffffu);
    final_text(canvas, "inter", (const unsigned *)L"Press Center to unlock", &hint, CARD_HINT_PX,
               0xff000000u | (light ? RGB_SECONDARY_L : RGB_SECONDARY));
    stock_canvas_set_fill_color(canvas, fill);
}
