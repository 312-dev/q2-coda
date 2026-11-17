/* Now Playing's art, artist and album as links (docs/ipod.md#now-playing), and a swipe up there for
 * Up Next: a tap on the artist
 * opens that artist, on the album or the art that album, as the More page's Look up Artist and
 * Look up Album do (navigation.c np_goto, which also says whether there is anywhere to go); a hold
 * on the art, ART_HOLD_MS without moving, opens stock's full-screen art (bigcover_page), whatever
 * Enlarged Album Art says, and its lift is no tap.
 *
 * The window takes the touch, as for the right-hand time (np_bar.c np_time_tap): whichever child
 * gets it (the slide_view, its page, a label), the page's own handler sees it come down, move and
 * lift, and hit-tests the three boxes, each theme's own rects. Only the art page counts, the
 * slide_view at rest on its first page, and not while the lyrics are full screen. A touch that
 * moves more than TAP_PX is a swipe or a drag: the slide_view's, never a link or a hold. While the
 * touch is held on a link that goes somewhere (the art: or opens full screen) it is lit: the
 * label's text in the accent (np_tap_ink; navigation.c ringnav_style_color) or the art dimmed
 * DIM_ALPHA. The move is deferred to the next pass, outside the touch's dispatch. */
#include "navigation.h"

enum { NPT_NONE, NPT_ART, NPT_ARTIST, NPT_ALBUM };
#define DIM_ALPHA 38    /* 15% black over the held art */
#define ART_HOLD_MS 500 /* a touch held on the art this long opens it full screen */
#define SWIPE_UP_PX 48  /* a lift this far above its press is a swipe up */

static struct {
    int lit;        /* NPT_*: the link the touch came down on, while it is held there */
    int x, y;       /* where the touch came down */
    unsigned timer; /* npt_go's */
    unsigned hold;  /* npt_hold's, while a touch is held on the art */
    int held;       /* the full-screen art opened from this touch: its lift goes nowhere */
} npt __attribute__((section(".scratch")));

static void *npt_widget(int k) {
    return k == NPT_ART      ? st.np_cover
           : k == NPT_ARTIST ? st.np_artist
           : k == NPT_ALBUM  ? st.np_album
                             : (void *)0;
}

/* 1 when a hold on the art may open the full-screen art: Now Playing in front and usable, the
 * player not stopped (stock's tap opened it only then) */
static int npt_full_ok(void) {
    void *wm = window_manager();
    return st.np_win && window_manager_get_top_window(wm) == st.np_win && usable() &&
           !window_manager_is_animating(wm) && mclGetPlayStatus() != 1;
}

/* The link at the screen's (x, y), when it goes somewhere (the art: or opens full screen) */
static int npt_at(int x, int y) {
    if (!st.np_win || !st.np_slide || st.np_full || widget_get_prop_int(st.np_slide, "value", 0))
        return NPT_NONE;
    for (int k = NPT_ART; k <= NPT_ALBUM; ++k) {
        void *w = npt_widget(k);
        if (w && widget_get_visible(w) && np_hit(w, x, y))
            return np_goto(k != NPT_ARTIST, 0) || (k == NPT_ART && npt_full_ok()) ? k : NPT_NONE;
    }
    return NPT_NONE;
}

static void npt_light(int k) {
    if (k == npt.lit) return;
    void *was = npt_widget(npt.lit), *now = npt_widget(k);
    npt.lit = k;
    if (was) widget_invalidate_force(was, (void *)0);
    if (now) widget_invalidate_force(now, (void *)0);
}

static int npt_go(const void *info) {
    npt.timer = 0;
    np_goto((int)(long)P(info, TIMER_CTX) != NPT_ARTIST, 1);
    return 0;
}

/* The hold's end: the art still held, unmoved, opens full screen; the touch is done with */
static int npt_hold(const void *info) {
    npt.hold = 0;
    if (npt.lit != NPT_ART || !npt_full_ok()) return 0;
    npt_light(NPT_NONE);
    npt.held = 1;
    navigator_to("bigcover_page");
    return 0;
}

/* navigation.c ringnav_dispatch, before anything else: the lift of the touch whose hold opened the
 * full-screen art reaches that page, whose pointer-up closes it (bigcover_page_on_pointer_up,
 * 0x52d73c), so the page would shut as it opened. That lift and its click are kept from it; the
 * next touch on it is its own. 1 to stop the event. */
int np_tap_lift(void *target, void *event) {
    if (!npt.held || !target || !event || target == window_manager()) return 0;
    int type = I(event, EVENT_TYPE);
    void *win = window_of(target);
    if (!win || tk_strcmp(widget_get_prop_str(win, "name", ""), "bigcover_page")) return 0;
    if (type == EVT_POINTER_DOWN || type == EVT_POINTER_DOWN - 1) {
        npt.held = 0;
        return 0;
    }
    return type == EVT_POINTER_UP_BEFORE || type == EVT_POINTER_UP_BEFORE - 1 || type == EVT_CLICK;
}

/* The window's pointer handler (ringnav_playing). Returns 0, so the touch goes on to the child
 * that has it: the slide_view still swipes, the bar still seeks. */
int np_tap(void *ctx, void *event) {
    (void)ctx;
    int type = I(event, EVENT_TYPE), x = I(event, EVENT_X), y = I(event, EVENT_Y);
    if (type == EVT_POINTER_DOWN) {
        npt.x = x, npt.y = y, npt.held = 0;
        stop_timer(&npt.hold);
        npt_light(npt_at(x, y));
        if (npt.lit == NPT_ART && npt_full_ok())
            npt.hold = timer_add(npt_hold, (void *)0, ART_HOLD_MS);
        return 0;
    }
    int k = npt.lit, near = (unsigned)(x - npt.x + TAP_PX) <= 2 * TAP_PX &&
                            (unsigned)(y - npt.y + TAP_PX) <= 2 * TAP_PX;
    if (type == EVT_POINTER_MOVE_BEFORE) {
        if (!near) stop_timer(&npt.hold), npt_light(NPT_NONE);
        return 0;
    }
    stop_timer(&npt.hold);
    npt_light(NPT_NONE);
    if (type == EVT_POINTER_UP_BEFORE && k && near && !npt.held && npt_at(x, y) == k &&
        np_goto(k != NPT_ARTIST, 0) && !npt.timer)
        npt.timer = timer_add(npt_go, (void *)(long)k, 0);
    /* a swipe up on the art's page, more up than across: Up Next (navigation.c np_queue) */
    int up = npt.y - y, across = x > npt.x ? x - npt.x : npt.x - x;
    if (type == EVT_POINTER_UP_BEFORE && up >= SWIPE_UP_PX && 2 * across < up && st.np_slide &&
        !st.np_full && !widget_get_prop_int(st.np_slide, "value", 0))
        timer_add(np_queue, (void *)0, 0);
    return 0;
}

/* The held artist's or album's text, 0xRRGGBB: the accent's text tone, or Text Primary (white,
 * Light Mode's ink) for a grey accent (Graphite), whose tone is the line's own grey */
unsigned np_tap_ink(void) {
    unsigned c = accent_tone(TONE_RED);
    return flip_if(ipod_light(), neutral(c) ? 0xffffff : c);
}

/* 1 when w is the artist or album held lit */
int np_tap_lit(void *w) { return w && npt.lit > NPT_ART && w == npt_widget(npt.lit); }

/* The held art, dimmed (navigation.c paint_cover: over the image, under the rounded corners) */
void np_tap_dim(void *w, void *canvas) {
    if (!w || npt.lit != NPT_ART || w != st.np_cover || !P(canvas, CANVAS_LCD)) return;
    unsigned fill = (unsigned)I(P(canvas, CANVAS_LCD), LCD_FILL_COLOR);
    int box[4];
    np_settle_box(w, box); /* where the art is: whole, or settled smaller on pause */
    stock_canvas_set_fill_color(canvas, (RGBA(0) & 0xffffffu) | (unsigned)DIM_ALPHA << 24);
    canvas_fill_rect(canvas, box[0], box[1], box[2], box[3]);
    stock_canvas_set_fill_color(canvas, fill);
}

/* A page opening or gone: nothing held, nothing to go to */
void np_tap_reset(void) {
    if (npt.lit == NPT_ART && st.np_cover) widget_set_opacity(st.np_cover, 255);
    npt.lit = NPT_NONE;
    npt.held = 0;
    stop_timer(&npt.timer);
    stop_timer(&npt.hold);
}
