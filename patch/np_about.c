/* Now Playing's About (docs/ipod.md#now-playing): ⋯'s About row opens the track's details as a
 * page of their own. They are Now Playing's info page (tools/ipod.py np_info_out), which the build
 * takes out of the swipe and leaves hidden in Now Playing's window, where stock's np_info finds
 * and fills its labels. The page moves that view into itself while it is open, at the place it
 * has in Now Playing's window (the page's window has the same box; below the title where that is
 * a row of the page), and gives it back hidden as
 * it closes, so each theme's layout of the details stays its own. Return goes back to what
 * opened it, as ⋯'s other rows' pages do: under OLED ⋯'s panel, else ⋯'s page. While it is open
 * Now Playing's fills go to it (navigation.c ringnav_np_info). */
#include "navigation.h"

static struct {
    void *page, *info, *home; /* the open page, the details it holds and their window */
} ab __attribute__((section(".scratch")));

void *np_about_page(void) { return ab.page; }

/* The details back in Now Playing's window, hidden and its first child, if both are still there;
 * with Now Playing gone first they stay the page's and go with it. */
static void ab_return(void) {
    void *info = ab.info, *home = ab.home, *page = ab.page;
    ab.info = ab.home = (void *)0;
    if (!info || !home || !page || P(info, W_PARENT) != page) return;
    widget_remove_child(page, info);
    widget_set_visible(info, 0, 0);
    widget_add_child(home, info);
    /* first again, as the build put it: stock's AWTK hands a touch to a hidden widget too, and
     * last it took every touch on Now Playing (tools/ipod.py np_info_out) */
    widget_restack(info, 0);
}

static int ab_closed(void *page, void *event) {
    if (ab.page != page) return 0;
    ab_return();
    ab.page = (void *)0;
    return 0;
}

static int ab_key(void *ctx, void *event) {
    if (I(event, EVENT_KEY) != KEY_RETURN) return 0;
    ab_return(); /* before the close, while every widget is whole */
    return page_keyup(ctx, event);
}

/* Now Playing's window going (navigation.c np_gone) while the page holds its details: they stay
 * the page's, and go with it. */
void np_about_np_gone(void *win) {
    if (ab.home == win) ab.home = (void *)0;
}

/* ⋯'s About row (onlinematch.c more_rows). */
int np_about_open(void *ctx, void *event) {
    void *home = st.np_win, *info = home ? widget_lookup(home, "view_info", 1) : (void *)0;
    if (ab.page || !info || !(ab.page = page_open("npabout_page", ab_closed, ab_key))) return 0;
    void *title = page_title(ab.page, "About", 1);
    widget_remove_child(P(info, W_PARENT), info);
    widget_add_child(ab.page, info);
    /* under the title where the theme shows it as the page's first row: Centred Now Playing's
     * details start at the window's top, where its Return and More stood */
    int below = title && widget_get_visible(title) ? I(title, W_Y) + I(title, W_H) : 0;
    if (I(info, W_Y) < below)
        widget_move_resize(info, I(info, W_X), below, I(info, W_W), I(info, W_H));
    widget_set_visible(info, 1, 0);
    ab.info = info, ab.home = home;
    ringnav_np_info(ab.page); /* filled at once, not at Now Playing's next tick */
    return 0;
}
