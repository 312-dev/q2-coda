/* Now Playing's art, artist and album as links (docs/ipod.md#now-playing): a tap on the artist
 * opens that artist, on the album or the art that album, as the hold menu's Go to Artist and Go to
 * Album do (navigation.c np_goto, which also says whether there is anywhere to go).
 *
 * The window takes the touch, as for the right-hand time (np_bar.c np_time_tap): whichever child
 * gets it (the slide_view, its page, a label), the page's own handler sees it come down, move and
 * lift, and hit-tests the three boxes, each theme's own rects. Only the art page counts, the
 * slide_view at rest on its first page, and not while the lyrics are full screen. A touch that
 * moves more than TAP_PX is a swipe or a drag: the slide_view's, never a link. While the touch is
 * held on a link that goes somewhere it is lit: the label's text in the accent (np_tap_ink;
 * navigation.c ringnav_style_color, and paint_caps for Minimal's album) or the art dimmed DIM_ALPHA
 * (Classic's slanted art faded as much over its paper). The move is deferred to the next pass,
 * outside the touch's dispatch. */
#include "navigation.h"

enum { NPT_NONE, NPT_ART, NPT_ARTIST, NPT_ALBUM };
#define DIM_ALPHA 38 /* 15% black over the held art */

static struct {
    int lit;        /* NPT_*: the link the touch came down on, while it is held there */
    int x, y;       /* where the touch came down */
    unsigned timer; /* npt_go's */
} npt __attribute__((section(".scratch")));

static void *npt_widget(int k) {
    return k == NPT_ART      ? st.np_cover
           : k == NPT_ARTIST ? st.np_artist
           : k == NPT_ALBUM  ? st.np_album
                             : (void *)0;
}

/* The link at the screen's (x, y), when it goes somewhere */
static int npt_at(int x, int y) {
    if (!st.np_win || !st.np_slide || st.np_full || widget_get_prop_int(st.np_slide, "value", 0))
        return NPT_NONE;
    for (int k = NPT_ART; k <= NPT_ALBUM; ++k) {
        void *w = npt_widget(k);
        if (w && widget_get_visible(w) && np_hit(w, x, y))
            return np_goto(k != NPT_ARTIST, 0) ? k : NPT_NONE;
    }
    return NPT_NONE;
}

/* 1 for Classic's slanted art, drawn with its reflection in a box taller than wide on the paper
 * (navigation.c paint_cover) */
static int npt_slanted(void *w) { return w && I(w, W_H) > I(w, W_W); }

static void npt_light(int k) {
    if (k == npt.lit) return;
    void *was = npt_widget(npt.lit), *now = npt_widget(k);
    npt.lit = k;
    /* the slanted art can't take a box of shade (the paper round it would grey): it is drawn
     * DIM_ALPHA less opaque instead, over the paper */
    if (npt_slanted(st.np_cover) && (was == st.np_cover || now == st.np_cover))
        widget_set_opacity(st.np_cover, k == NPT_ART ? 255 - DIM_ALPHA : 255);
    if (was) widget_invalidate_force(was, (void *)0);
    if (now) widget_invalidate_force(now, (void *)0);
}

static int npt_go(const void *info) {
    npt.timer = 0;
    np_goto((int)(long)P(info, TIMER_CTX) != NPT_ARTIST, 1);
    return 0;
}

/* The window's pointer handler (ringnav_playing). Returns 0, so the touch goes on to the child
 * that has it: the slide_view still swipes, the bar still seeks. */
int np_tap(void *ctx, void *event) {
    (void)ctx;
    int type = I(event, EVENT_TYPE), x = I(event, EVENT_X), y = I(event, EVENT_Y);
    if (type == EVT_POINTER_DOWN) {
        npt.x = x, npt.y = y;
        npt_light(npt_at(x, y));
        return 0;
    }
    int k = npt.lit, near = (unsigned)(x - npt.x + TAP_PX) <= 2 * TAP_PX &&
                            (unsigned)(y - npt.y + TAP_PX) <= 2 * TAP_PX;
    if (type == EVT_POINTER_MOVE_BEFORE) {
        if (!near) npt_light(NPT_NONE);
        return 0;
    }
    npt_light(NPT_NONE);
    if (type == EVT_POINTER_UP_BEFORE && k && near && npt_at(x, y) == k && !npt.timer)
        npt.timer = timer_add(npt_go, (void *)(long)k, 0);
    return 0;
}

/* The held artist's or album's text, 0xRRGGBB: the accent's text tone, or Text Primary (white,
 * Light Mode's ink) for a grey accent (Minimal's Graphite), whose tone is the line's own grey */
unsigned np_tap_ink(void) {
    unsigned c = accent_tone(TONE_RED);
    return flip_if(ipod_light(), neutral(c) ? 0xffffff : c);
}

/* 1 when w is the artist or album held lit */
int np_tap_lit(void *w) { return w && npt.lit > NPT_ART && w == npt_widget(npt.lit); }

/* The held art, dimmed (navigation.c paint_cover: over the image, under the rounded corners); the
 * slanted art is faded instead (npt_light) */
void np_tap_dim(void *w, void *canvas) {
    if (!w || npt.lit != NPT_ART || w != st.np_cover || npt_slanted(w) || !P(canvas, CANVAS_LCD))
        return;
    unsigned fill = (unsigned)I(P(canvas, CANVAS_LCD), LCD_FILL_COLOR);
    stock_canvas_set_fill_color(canvas, (RGBA(0) & 0xffffffu) | (unsigned)DIM_ALPHA << 24);
    canvas_fill_rect(canvas, 0, 0, I(w, W_W), I(w, W_H));
    stock_canvas_set_fill_color(canvas, fill);
}

/* A page opening or gone: nothing held, nothing to go to */
void np_tap_reset(void) {
    if (npt.lit == NPT_ART && st.np_cover) widget_set_opacity(st.np_cover, 255);
    npt.lit = NPT_NONE;
    stop_timer(&npt.timer);
}
