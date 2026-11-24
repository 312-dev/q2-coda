/* Centre or a finger held on a song's row opens the song's hold menu, the one a Play/Pause hold
 * opens (hold_menu.c qm_open, docs/internals.md#centre-hold-menu), on every song list the hold menu
 * knows: the Library's lists over the showlist (Songs, an album's or an artist's songs, a genre's
 * and the rest), Folders' audio files, Cover Flow's tracks, Most Played and the Ratings lists, a
 * playlist's page and Up Next's queue (upnext.c un_menu_hold).
 *
 * It fires after CENTRE_MENU_MS, half of stock's Centre hold (STOCK_CENTRE_HOLD_MS, the Shut Down?
 * prompt), so a press meant for the menu is let go well before the prompt could come. The press
 * that opens the menu removes stock's hold timer (st.centre_hold), as Edit Library's and Up Next's
 * holds do, so that press never goes on to Shut Down?, however long it is held; the prompt is a
 * fresh Centre hold anywhere but a song row (the open menu included). The release is taken
 * (cm_key_release), so it neither plays the song nor picks the menu's first row.
 *
 * Centre's hold is timed from its press (cm_keydown) and needs, when it fires, the same press still
 * down with stock's hold still pending: a release, a rim chord (input.c chord_gate ends the hold
 * when an edge button joins within CHORD_MS) or another hold that took it first leaves it be. The
 * second press of a double press (st.center_timer: the first one's open still pending) arms none,
 * so Wake's and the screen off's double press stay. A dark screen, Buttons Lock and a game get
 * nothing: usable() needs the screen lit, and a game's keys stop before ringnav_keydown.
 *
 * A finger is timed from its touch (cm_touch, from input.c ringnav_input's raw pointer events):
 * held CENTRE_MENU_MS within CM_SLOP px of where it landed, the row under it is selected and gets
 * the menu, and the touch is aborted (EVT_POINTER_ABORT through the window) so its lift clicks
 * nothing. Any move past CM_SLOP (a drag, a scroll), a lift or an abort first ends it. */
#include "navigation.h"

/* Stock's long press for Centre: input_device_status_handle_event's key-down adds the key's
 * long-press timer with li $a2, 0x3e8 at 0x6b3654 (500 only for key 232, unused on the Q2), and
 * on_wm_keylong_fun opens Shut Down? from it (tools/build.py HOLD_KEYS). */
#define STOCK_CENTRE_HOLD_MS 1000
#define CENTRE_MENU_MS (STOCK_CENTRE_HOLD_MS / 2)
#define CM_SLOP 8 /* a finger that moves this far before its hold is a drag (LR_SLOP) */

static struct {
    unsigned hold, at;  /* Centre's hold timer and the press it times (chord_gate's centre_at) */
    unsigned touch;     /* the finger's */
    int x, y;           /* where the finger landed, in the UI's coordinates */
    unsigned char held; /* a Centre hold opened the menu: its release is taken */
} cm __attribute__((section(".scratch")));

static int cm_centre_due(const void *unused) {
    cm.hold = 0;
    if (!st.centre_hold || st.centre_at != cm.at || !usable()) return 0;
    if (!qm_centre_hold(window_manager_get_top_window(window_manager()))) return 0;
    timer_remove(st.centre_hold); /* stock's own hold, Shut Down?, never comes */
    st.centre_hold = 0;
    cm.held = 1;
    return 0;
}

/* input.c ringnav_keydown: Centre's press, and its repeats while held, which leave its timer be. */
void cm_keydown(unsigned key) {
    if (key != KEY_CENTER) return;
    if (cm.held && st.centre_at != cm.at) cm.held = 0; /* a release that never came */
    if (cm.hold || cm.held || !st.centre_at || st.center_timer || !usable()) return;
    cm.at = st.centre_at;
    cm.hold = timer_add(cm_centre_due, (void *)0, CENTRE_MENU_MS);
}

/* input.c ringnav's key filter: 1 for the release of the Centre hold that opened the menu. */
int cm_key_release(unsigned key) {
    if (key != KEY_CENTER) return 0;
    stop_timer(&cm.hold);
    if (!cm.held) return 0;
    cm.held = 0;
    *(volatile unsigned char *)KEY_LOCKOUT = 0; /* the wheel at once, as a click's */
    return 1;
}

static int cm_touch_due(const void *unused) {
    cm.touch = 0;
    void *top = window_manager_get_top_window(window_manager());
    if (!pointer_down() || !usable() || !qm_touch_hold(top, cm.x, cm.y)) return 0;
    unsigned abort_event[12]; /* the row's press and the list's drag let go: the lift clicks none */
    widget_dispatch_event_to_target_recursive(
        top, pointer_event_init(abort_event, EVT_POINTER_ABORT, top, 0, 0));
    return 0;
}

/* input.c ringnav_input: a finger's raw event. The panel's axes are portrait (status_touch): the
 * UI's x is the raw y and its y 319 less the raw x. */
void cm_touch(unsigned type, int x, int y) {
    int ux = y, uy = 319 - x;
    if (type == EVT_POINTER_FIRST) {
        cm.x = ux, cm.y = uy;
        rearm(&cm.touch, cm_touch_due, CENTRE_MENU_MS);
    } else if (type == EVT_POINTER_FIRST + 2 || type == EVT_POINTER_MOVE_BEFORE) {
        int dx = ux - cm.x, dy = uy - cm.y;
        if (dx * dx + dy * dy > CM_SLOP * CM_SLOP) stop_timer(&cm.touch);
    } else
        stop_timer(&cm.touch); /* lifted or aborted */
}
