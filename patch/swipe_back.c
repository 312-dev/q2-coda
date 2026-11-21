/* Swipe back (docs/internals.md#swipe-back, docs/oled-design.md#gestures): a finger that comes down
 * within SB_EDGE_PX of the screen's left edge and drags right takes the page with it, as iOS's
 * edge swipe does; let go past SB_DONE_PCT of the width, or flicked right at SB_FLICK px a second
 * or faster, and it goes on off the right edge and Return goes back; otherwise it springs back.
 * The layer that moves is what Return leaves: the top window, when there is a window under it to go
 * back to, or on Home the list one of its rows opened (coverflow.c coverflow_home_sublist). Under a
 * window it uncovers the page under it, as a push's parallax leaves it (page_parallax.c): the
 * window manager paints from the top normal window up, so at the claim the window under is painted
 * once into a bitmap, as stock's window animator snapshots it (under_snap), and each frame's
 * swipe_back_paint, the top window's background paint, draws it left of the page, PX_UNDER_PCT of
 * the width short of its place and darkened to PX_DIM_ALPHA at the start, both easing to nothing
 * as the page leaves; Reduce Motion keeps it still and undimmed. Each move invalidates the whole
 * screen, as the strip left of the page moves too; whatever the page's painters put on its own
 * columns rides with it through page_ox. Home's lists uncover black (ponytail: Home's
 * main list could be drawn there the same way).
 *
 * A touch is a candidate from its press (swipe_back_down, from navigation.c's touch hook) and
 * claimed once it has moved SB_CLAIM_PX right and more right than up or down: the page's own
 * pressed widgets are aborted (EVT_POINTER_ABORT, as pull-to-search and the A-Z index claim
 * theirs), so a row, a slider or Now Playing's slide_view lets go, and from there the moves and the
 * lift are swallowed. Meanwhile the touch is no touch on the rows: the selected row's bar stays lit
 * and rides with the page (swipe_back_holds, docs/internals.md#touch-mode). A drag up or down
 * first, or left, is left to the page, which then hides the bar as any touch does (touch_hide): a
 * list scrolls as it would, from the edge too. A drag that starts anywhere else is never this
 * one's, so the screens with sideways drags of their own (Now Playing's pages, Cover Flow, the
 * letter strip, sliders) keep them; at the edge the swipe wins.
 *
 * Completing, the layer slides on to the right edge over SB_GLIDE_MS from where the finger let
 * go, eased cubic_out (sb_ease), then Return goes in as a
 * press and a release through the window manager's input (navigation.c ringnav_input), so it does
 * whatever a Return press does there; the closing window's own slide (its close_anim_hint) is
 * cleared first, as the page has already left. Where Return does not leave the page after all, the
 * layer is put back at its place. A spring back takes the same time. Each frame moves the layer
 * (widget_move), and the window manager repaints what it uncovers; swipe_back_poll steps it from
 * each UI loop pass.
 *
 * On a page whose letter strip is open (search_strip.c, a text box's keyboard window among them)
 * the claim shuts the strip instead and the page stays where it is, as Return there does
 * (search_strip_close; docs/oled-design.md#text-entry); a second swipe then takes the page. Not on
 * dialogs, Control Center or Home's own list (no Return there leaves a page), nor while a window
 * animates; a card game or q2video takes every touch first (addon.c, videos.c).
 */
#include "navigation.h"

#define SB_EDGE_PX 24   /* a press this near the left edge */
#define SB_CLAIM_PX 10  /* moved this far right, more than up or down: the swipe's (TAP_PX) */
#define SB_DONE_PCT 40  /* let go past this share of the width: back */
#define SB_FLICK 500    /* or moving right this fast, px a second, over the last SB_VEL_MS */
#define SB_VEL_MS 100   /* a sample older than this gives no speed */
#define SB_GLIDE_MS 160 /* the finish or the spring back, from where the finger let go */
#define SB_W 375
#define SB_H 320
#define SB_BOUND "_sb_bound"

extern int ringnav_input(void *wm, void *e);
extern void *coverflow_home_sublist(void *top);

static struct {
    void *page;            /* the window the touch came down on, while it is a candidate or ours */
    void *layer;           /* what moves: page, or a list of Home's */
    int x0, y0;            /* the press */
    int lx;                /* the layer's x at rest */
    int off;               /* how far right the layer is now */
    int claimed, aborting; /* the touch is ours; our own abort is passing through the page */
    int shut; /* ... and it shut the letter strip, the page staying (search_strip_close) */
    int last_x, prev_x; /* the last two moves' x and times, for the lift's speed */
    unsigned last_t, prev_t;
    int from, to;    /* a glide: the offset it started from and goes to */
    unsigned at, ms; /* ... its start (time_now_ms, 0 with none) and length */
    void *under;     /* the window under, painted at the claim (under_snap); 0 none */
} sb __attribute__((section(".scratch")));

/* cubic_out over k 0..256: 1 - (1 - k)^3 */
int sb_ease(int k) {
    int r = 256 - k;
    return 256 - r * r / 256 * r / 256;
}

/* At the lift, the layer off px right of w moving v px a second: 1 goes back, 0 springs back. A
 * flick goes back however short; one left never does. */
int sb_done(int off, int w, int v) {
    if (v < -SB_FLICK) return 0;
    return v >= SB_FLICK || off * 100 >= w * SB_DONE_PCT;
}

/* The normal window under top in the window manager's stack, 0 if top is the last (also the
 * letter strip's, search_strip.c ss_place). */
void *window_under(void *wm, void *top) {
    int n = (int)widget_count_children(wm), below = 0;
    for (int i = n - 1; i >= 0; --i) {
        void *w = widget_get_child(wm, (unsigned)i);
        if (w == top)
            below = 1;
        else if (below && !tk_strcmp(widget_get_type(w), "window"))
            return w;
    }
    return (void *)0;
}

/* The layer off px right of its place. widget_move, not widget_move_resize, which lays a list's
 * children out again: Home's lists would lose their fades and scroll (coverflow.c
 * home_overflow). */
static void place(int off) {
    void *w = sb.layer;
    sb.off = off;
    widget_move(w, sb.lx + off, I(w, W_Y));
    if (sb.under) widget_invalidate_force(window_manager(), (void *)0); /* the strip moves too */
}

/* canvas's origin as a column of w's page: its x on the screen less how far a swipe has moved w's
 * window (place). A painter that puts something on one of the page's columns (the text column, the
 * right column, the screen's middle) takes this from that column, not the canvas's own x, which
 * would hold it at its screen column while the page slides out from under it. Only a normal window
 * moves so; a dialog or a sheet keeps its own x in the canvas's. ponytail: a list of Home's, the
 * layer a swipe moves inside Home's window, is not taken off; sb.off for a w under sb.layer
 * would. */
int page_ox(void *canvas, void *w) {
    void *win = window_of(w);
    int ox = I(canvas, CANVAS_X);
    return win && !tk_strcmp(widget_get_type(win), "window") ? ox - I(win, W_X) : ox;
}

/* The window under, painted once into a bitmap as stock's window animator snapshots it; 0 when
 * there is no memory for it (the strip stays black). The letter strip snaps the page it types for
 * the same way (search_strip.c ss_place). */
void *under_snap(void *under) {
    void *c = under ? canvas_offline_create(SB_W, SB_H, LCD_BGRA8888) : (void *)0, *b = (void *)0;
    if (!c) return (void *)0;
    canvas_offline_begin_draw(c);
    widget_paint(under, c);
    canvas_offline_end_draw(c);
    void *lcd = P(c, CANVAS_LCD);
    const unsigned char *fb = lcd ? P(lcd, LCD_OFFLINE_FB) : (void *)0;
    if (fb && I(lcd, LCD_W) == SB_W && I(lcd, LCD_H) == SB_H &&
        I(lcd, LCD_FORMAT) == LCD_BGRA8888 && I(lcd, LCD_LINE_LENGTH) == SB_W * 4)
        b = bitmap_create_ex(SB_W, SB_H, SB_W * 4, LCD_BGRA8888);
    unsigned char *px = b ? bitmap_lock_buffer_for_write(b) : (unsigned char *)0;
    if (px) {
        memcpy(px, fb, SB_W * SB_H * 4);
        bitmap_unlock_buffer(b);
        *(unsigned short *)((char *)b + 0xc) |= 1; /* opaque: drawn a row at a time */
    } else if (b) {
        bitmap_destroy(b);
        b = (void *)0;
    }
    canvas_offline_destroy(c);
    return b;
}

/* ringnav_paint_bg, the top window's background: while a swipe moves it, the window under in the
 * strip it uncovers, at its parallax place and darkened (in the window's canvas, whose origin is
 * the page's place, off px right and below the status row; the bitmap is the whole screen). */
void swipe_back_paint(void *w, void *canvas) {
    if (!w || w != sb.page || !sb.under || !sb.off || !P(canvas, CANVAS_LCD)) return;
    int off = sb.off, still = motion_reduced(),
        from = still ? 0 : parallax_under_x(SB_W, 1.0f - (float)off / SB_W);
    rect_t src = { from, 0, off, SB_H }, dst = { -off, -I(w, W_Y), off, SB_H }; /* the screen's */
    canvas_draw_image(canvas, sb.under, &src, &dst);
    unsigned a = still ? 0 : parallax_dim(1.0f - (float)off / SB_W);
    if (!a) return;
    unsigned fill = (unsigned)I(P(canvas, CANVAS_LCD), LCD_FILL_COLOR);
    stock_canvas_set_fill_color(canvas, a << 24); /* black in both halves, as a push dims it */
    canvas_fill_rect(canvas, -off, -I(w, W_Y), off, SB_H);
    stock_canvas_set_fill_color(canvas, fill);
}

static void reset(void) {
    if (sb.under) bitmap_destroy(sb.under);
    sb.under = (void *)0;
    sb.page = sb.layer = (void *)0;
    sb.claimed = sb.at = sb.shut = 0;
    sb.off = 0;
}

/* The layer from where it is to off over SB_GLIDE_MS, as iOS finishes an interactive pop faster
 * than a push. It finishes what the finger did, so Reduce Motion keeps it (MO_FEEDBACK). */
static void glide(int off) {
    sb.from = sb.off;
    sb.to = off;
    sb.ms = motion_ms(SB_GLIDE_MS, MO_FEEDBACK);
    sb.at = (unsigned)time_now_ms() | 1;
}

/* Return, as its button gives it, with the layer at the right edge. The window's close_anim_hint is
 * emptied for Return and put back after, from a copy: stock's widget_get_prop_str answers the
 * window's own field (window_base_get_prop), and the clear's tk_str_copy reallocs that block to one
 * byte, so the pointer read would be "" or freed by then (test/patch.py, 02_navigation_pull).
 * No hint (NULL, as stock answers for a window never given one, or "") is left alone, and one too
 * long for the copy is left in place: the window slides out once more. */
static void go_back(void) {
    void *page = sb.page, *wm = window_manager();
    char close[64];
    const char *hint =
        sb.layer == page ? widget_get_prop_str(page, "close_anim_hint", "") : (void *)0;
    int cleared = hint && *hint && tk_snprintf(close, sizeof close, "%s", hint) < (int)sizeof close;
    if (cleared) widget_set_prop_str(page, "close_anim_hint", "");
    unsigned e[16] = { 0 };
    e[0] = EVT_KEY_DOWN;
    e[EVENT_KEY / 4] = KEY_RETURN;
    e[EVENT_TIME / 4] = (unsigned)time_now_ms();
    ringnav_input(wm, e);
    e[0] = EVT_KEY_UP;
    e[EVENT_TIME / 4] = (unsigned)time_now_ms();
    ringnav_input(wm, e);
    if (!sb.page) return; /* destroyed with the window it closed */
    if (cleared) widget_set_prop_str(page, "close_anim_hint", close);
    place(0); /* closed, it is off the stack; staying, it is back in its place */
    reset();
}

/* Each UI loop pass: a glide's step, and at its end Return or rest. */
void swipe_back_poll(void) {
    if (!sb.at || !sb.layer) return;
    unsigned t = (unsigned)time_now_ms() - sb.at;
    if (t < sb.ms) {
        place(sb.from + (sb.to - sb.from) * sb_ease((int)(t * 256 / sb.ms)) / 256);
        return;
    }
    sb.at = 0;
    place(sb.to);
    if (sb.to)
        go_back();
    else
        reset();
}

/* The page's move, lift and abort while a touch is a candidate or ours (bound once a page, as
 * pull_event is). */
static int sb_event(void *page, void *event) {
    unsigned type = I(event, EVENT_TYPE);
    if (type == EVT_DESTROY || type == EVT_WINDOW_CLOSE) {
        if (page == sb.page) {
            if (type == EVT_DESTROY) sb.page = (void *)0; /* go_back reads it */
            if (!sb.page || !sb.at) reset();
        }
        return 0;
    }
    if (page != sb.page || sb.at || sb.aborting) return 0;
    if (type == EVT_POINTER_ABORT) {
        if (sb.claimed)
            glide(0);
        else
            reset();
        return 0;
    }
    int x = I(event, EVENT_X), dx = x - sb.x0, dy = I(event, EVENT_Y) - sb.y0;
    int ady = dy < 0 ? -dy : dy, up = type == EVT_POINTER_UP_BEFORE;
    if (!sb.claimed) {
        if (up || dx <= -SB_CLAIM_PX || (ady >= SB_CLAIM_PX && ady >= dx)) {
            reset();
            touch_hide(); /* the page's touch after all: its rows' bar hides, as for any touch */
            return 0;
        }
        if (dx < SB_CLAIM_PX || dx <= ady) return 0;
        sb.claimed = 1;
        sb.shut = search_strip_close(page); /* the strip open: it shuts, and the page stays */
        pull_cancel();
        unsigned abort_event[12];
        pointer_event_init(abort_event, EVT_POINTER_ABORT, page, sb.x0, sb.y0);
        sb.aborting = 1;
        widget_dispatch_event_to_target_recursive(page, abort_event);
        sb.aborting = 0;
        sb.prev_x = sb.last_x = sb.x0;
        sb.prev_t = sb.last_t = (unsigned)time_now_ms();
        if (sb.layer == page && !sb.shut)
            sb.under = under_snap(window_under(window_manager(), page));
    }
    if (sb.shut) { /* the rest of the touch is the swipe's, and moves nothing */
        if (up) reset();
        return STOP;
    }
    unsigned now = (unsigned)time_now_ms();
    if (x != sb.last_x) {
        sb.prev_x = sb.last_x, sb.prev_t = sb.last_t;
        sb.last_x = x, sb.last_t = now;
    }
    int w = I(sb.layer, W_W);
    if (!up) {
        place(dx < 0 ? 0 : dx > w ? w : dx);
        return STOP;
    }
    int v = 0, ms = (int)(sb.last_t - sb.prev_t);
    if (ms > 0 && now - sb.last_t < SB_VEL_MS && ms < SB_VEL_MS)
        v = (sb.last_x - sb.prev_x) * 1000 / ms;
    glide(sb_done(sb.off, w, v) ? w : 0);
    return STOP;
}

/* A press is a candidate or claimed, and not let go: navigation.c's touch hook leaves the rows' bar
 * lit meanwhile (docs/internals.md#touch-mode). */
int swipe_back_holds(void) { return sb.page && !sb.at; }

/* navigation.c's touch hook, on a press stock and the A-Z index left alone: one near the left edge
 * of a page Return leaves, under the status row, becomes a candidate. A press on the row is the
 * row's, as when it was a window of its own over the page's top (docs/oled-design.md#full-bleed).
 * allowed is 0 where Return stays on the page (full-screen lyrics, the rating picker). */
void swipe_back_down(void *event, int allowed) {
    /* stock's move-before comes through the same hook: only a press starts anything */
    if (!event || I(event, EVENT_TYPE) != EVT_POINTER_DOWN || sb.at) return;
    if (sb.page) reset(); /* a press after a lift that never came */
    if (!allowed || I(event, EVENT_X) >= SB_EDGE_PX || I(event, EVENT_Y) < 320 - client_h() ||
        !usable())
        return;
    void *wm = window_manager(), *top = window_manager_get_top_window(wm);
    if (!top || window_manager_is_animating(wm) ||
        (tk_strcmp(widget_get_type(top), "window") && !search_strip_open(top)))
        return; /* a text box's keyboard window is a page here while the strip types on it */
    void *layer = coverflow_home_sublist(top);
    if (!layer) {
        if (!window_under(wm, top)) return; /* Home's own list: nothing to go back to */
        layer = top;
    }
    if (!widget_get_prop_int(top, SB_BOUND, 0)) {
        static const unsigned types[] = { EVT_DESTROY, EVT_WINDOW_CLOSE, EVT_POINTER_ABORT,
                                          EVT_POINTER_MOVE_BEFORE, EVT_POINTER_UP_BEFORE };
        for (unsigned i = 0; i < sizeof types / sizeof *types; ++i)
            if (!widget_on(top, types[i], sb_event, top)) {
                while (i) widget_off_by_func(top, types[--i], sb_event, top);
                return;
            }
        prop(top, SB_BOUND, 1);
    }
    sb.page = top;
    sb.layer = layer;
    sb.lx = I(layer, W_X);
    sb.x0 = I(event, EVENT_X);
    sb.y0 = I(event, EVENT_Y);
    sb.claimed = sb.off = 0;
}
