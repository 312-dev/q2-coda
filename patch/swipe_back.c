/* Swipe back (docs/internals.md#swipe-back, docs/oled-design.md#gestures): a finger that comes down
 * within SB_EDGE_PX of the screen's left edge and drags right takes the page with it, as iOS's
 * edge swipe does; let go past SB_DONE_PCT of the width, or flicked right at SB_FLICK px a second
 * or faster, and it goes on off the right edge and Return goes back; otherwise it springs back.
 * The layer that moves is what Return leaves: the top window, when there is a window under it to go
 * back to, or on Home the list one of its rows opened (coverflow.c coverflow_home_sublist). What it
 * uncovers is black: the window manager paints from the top normal window up, so the page under is
 * not drawn until Return has closed the one over it (ponytail: painting it there would take a
 * snapshot of it, as stock's window animator does, a full paint's cost at the claim).
 *
 * A touch is a candidate from its press (swipe_back_down, from navigation.c's touch hook) and
 * claimed once it has moved SB_CLAIM_PX right and more right than up or down: the page's own
 * pressed widgets are aborted (EVT_POINTER_ABORT, as pull-to-search and the A-Z index claim
 * theirs), so a row, a slider or Now Playing's slide_view lets go, and from there the moves and the
 * lift are swallowed. A drag up or down first, or left, is left to the page: a list scrolls as it
 * would, from the edge too. A drag that starts anywhere else is never this one's, so the screens
 * with sideways drags of their own (Now Playing's pages, Cover Flow, the letter strip, sliders)
 * keep them; at the edge the swipe wins.
 *
 * Completing, the layer slides on to the right edge over the theme's swap motion's time
 * (motion.swap.ms, as long as a page's slide), eased cubic_out (sb_ease), then Return goes in as a
 * press and a release through the window manager's input (navigation.c ringnav_input), so it does
 * whatever a Return press does there; the closing window's own slide (its close_anim_hint) is
 * cleared first, as the page has already left. Where Return does not leave the page after all, the
 * layer is put back at its place. A spring back takes the same time. Each frame moves the layer
 * (widget_move), and the window manager repaints what it uncovers; swipe_back_poll steps it from
 * each UI loop pass.
 *
 * Not on dialogs, Control Center, the keyboard or Home's own list (no Return there leaves a page),
 * nor while a window animates; a card game or q2video takes every touch first (addon.c, videos.c).
 */
#include "navigation.h"

#define SB_EDGE_PX 24  /* a press this near the left edge */
#define SB_CLAIM_PX 10 /* moved this far right, more than up or down: the swipe's (TAP_PX) */
#define SB_DONE_PCT 40 /* let go past this share of the width: back */
#define SB_FLICK 500   /* or moving right this fast, px a second, over the last SB_VEL_MS */
#define SB_VEL_MS 100  /* a sample older than this gives no speed */
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
    int last_x, prev_x;    /* the last two moves' x and times, for the lift's speed */
    unsigned last_t, prev_t;
    int from, to;    /* a glide: the offset it started from and goes to */
    unsigned at, ms; /* ... its start (time_now_ms, 0 with none) and length */
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

/* The normal window under top in the window manager's stack, 0 if top is the last. */
static void *window_under(void *wm, void *top) {
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
}

static void reset(void) {
    sb.page = sb.layer = (void *)0;
    sb.claimed = sb.at = 0;
    sb.off = 0;
}

/* The layer from where it is to off over the theme's swap motion, which a page's slide keeps pace
 * with (both PAGE_SLIDE_MS); at once without one. It finishes what the finger did, so Reduce Motion
 * keeps it (MO_FEEDBACK). */
static void glide(int off) {
    sb.from = sb.off;
    sb.to = off;
    sb.ms = motion_ms(theme()->motion[MOTION_SWAP].ms, MO_FEEDBACK);
    sb.at = (unsigned)time_now_ms() | 1;
}

/* Return, as its button gives it, with the layer at the right edge. */
static void go_back(void) {
    void *page = sb.page, *layer = sb.layer, *wm = window_manager();
    int window = layer == page;
    const char *close = "";
    if (window) {
        close = widget_get_prop_str(page, "close_anim_hint", "");
        widget_set_prop_str(page, "close_anim_hint", "");
    }
    unsigned e[16] = { 0 };
    e[0] = EVT_KEY_DOWN;
    e[EVENT_KEY / 4] = KEY_RETURN;
    e[EVENT_TIME / 4] = (unsigned)time_now_ms();
    ringnav_input(wm, e);
    e[0] = EVT_KEY_UP;
    e[EVENT_TIME / 4] = (unsigned)time_now_ms();
    ringnav_input(wm, e);
    if (!sb.page) return; /* destroyed with the window it closed */
    if (window) widget_set_prop_str(page, "close_anim_hint", close);
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
            return 0;
        }
        if (dx < SB_CLAIM_PX || dx <= ady) return 0;
        sb.claimed = 1;
        pull_cancel();
        unsigned abort_event[12];
        pointer_event_init(abort_event, EVT_POINTER_ABORT, page, sb.x0, sb.y0);
        sb.aborting = 1;
        widget_dispatch_event_to_target_recursive(page, abort_event);
        sb.aborting = 0;
        sb.prev_x = sb.last_x = sb.x0;
        sb.prev_t = sb.last_t = (unsigned)time_now_ms();
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

/* navigation.c's touch hook, on a press stock and the A-Z index left alone: one near the left edge
 * of a page Return leaves becomes a candidate. allowed is 0 where Return stays on the page
 * (full-screen lyrics, the rating picker). */
void swipe_back_down(void *event, int allowed) {
    /* stock's move-before comes through the same hook: only a press starts anything */
    if (!event || I(event, EVENT_TYPE) != EVT_POINTER_DOWN || sb.at) return;
    if (sb.page) reset(); /* a press after a lift that never came */
    if (!allowed || I(event, EVENT_X) >= SB_EDGE_PX || !usable()) return;
    void *wm = window_manager(), *top = window_manager_get_top_window(wm);
    if (!top || window_manager_is_animating(wm) || tk_strcmp(widget_get_type(top), "window"))
        return;
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
