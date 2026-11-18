/* Now Playing's lines as one marquee (docs/oled-design.md#motion), under a theme whose scrolling
 * text fades (list.marquee.fade, OLED's): the title and artist that overflow their column
 * hold at rest NPM_HOLD_MS, then slide left together until each one's end meets its column's end,
 * hold there MARQUEE_HOLD_MS, and go back to rest together. Each slides only what overflows, so
 * some of every line is always in view: V1.36's hscroll_label draws no copy behind the text (its
 * paint draws one only under stop_at_begin, 0x5fc1a0, and not where a loop would need it), and a
 * line slid its whole width left its column blank until the next pass. The longest sets the pass
 * at NPM_PX_S, linear; the others are stretched to it. A line that fits never moves. A new text,
 * the page opening and the page back on top start from rest.
 *
 * Stock's own timer never runs for these labels (ringnav_hscroll_start asks np_marq_owns, and
 * np_marq_reset stops the one stock's page init starts), so stock never paints their ellipsis;
 * npm_tick sets each one's xoffset, a frame per pixel the fastest line moves, and asks nothing
 * while they rest. ringnav_sleep's idle sleep ends by the next frame (np_marq_wait), so frames come
 * when due rather than on the loop's 25 ms pass. */
#include "navigation.h"

#define NPM_HOLD_MS 2750 /* at rest before each pass */
#define NPM_END_MS MARQUEE_HOLD_MS /* at the end of each pass */
#define NPM_PX_S 35      /* the longest line's speed, px a second */

static struct {
    unsigned timer, start, due; /* the frame timer, the cycle's start, and when the timer runs */
} npm __attribute__((section(".scratch")));

/* The album is not one of them: it stays one line with stock's ellipsis (tools/ipod.py np_grid's
 * only_focus and ellipses), so the heart under it keeps its place. */
#define NPM_LINES 2
static void *npm_line(int i) { return i == 0 ? st.np_title : st.np_artist; }

/* 1 when w is one of the lines this drives */
int np_marq_owns(void *w) {
    return w && st.np_win && theme()->marquee_fade && (w == st.np_title || w == st.np_artist);
}

/* px w's text runs past its column, 0 when it fits */
static int npm_over(void *w) {
    void *style = P(w, W_ASTYLE);
    int col = I(w, W_W) - style_get_int(style, "margin_left", 2) -
              style_get_int(style, "margin_right", 2),
        over = I(w, HSCROLL_TEXT_W) - col;
    return over > 0 ? over : 0;
}

static void npm_rest(void) {
    for (int i = 0; i < NPM_LINES; ++i) {
        void *w = npm_line(i);
        if (w && I(w, HSCROLL_XOFFSET)) hscroll_label_set_xoffset(w, 0);
    }
}

static int npm_tick(const void *info);
static void npm_arm(unsigned now, unsigned ms) {
    npm.due = now + ms;
    npm.timer = timer_add(npm_tick, (void *)0, ms);
}

/* One frame: every line's offset at the cycle's time, then the timer for the next pixel or the
 * hold's end. Off screen, dark or covered, the lines go back to rest and nothing runs until a
 * paint asks again (np_marq_kick). */
static int npm_tick(const void *info) {
    (void)info;
    npm.timer = 0;
    unsigned now = (unsigned)time_now_ms();
    if (!st.np_win || !theme()->marquee_fade || !g_backlight_status ||
        window_manager_get_top_window(window_manager()) != st.np_win) {
        npm_rest();
        npm.start = 0;
        return 0;
    }
    int span[3], longest = 0;
    for (int i = 0; i < NPM_LINES; ++i) {
        void *w = npm_line(i);
        span[i] = w && widget_get_visible(w) ? npm_over(w) : 0;
        if (span[i] > longest) longest = span[i];
    }
    if (!longest) { /* everything fits: rest, and nothing to wake for */
        npm_rest();
        npm.start = 0;
        return 0;
    }
    unsigned pass = (unsigned)longest * 1000u / NPM_PX_S, t = now - npm.start;
    if (t >= NPM_HOLD_MS + pass + NPM_END_MS) npm.start = now, t = 0; /* back at rest */
    if (t < NPM_HOLD_MS) {
        npm_rest();
        npm_arm(now, NPM_HOLD_MS - t);
        return 0;
    }
    t -= NPM_HOLD_MS;
    if (t >= pass) { /* at the end, each line's end at its column's */
        for (int i = 0; i < NPM_LINES; ++i) {
            void *w = npm_line(i);
            if (w && I(w, HSCROLL_XOFFSET) != span[i]) hscroll_label_set_xoffset(w, span[i]);
        }
        npm_arm(now, pass + NPM_END_MS - t);
        return 0;
    }
    for (int i = 0; i < NPM_LINES; ++i) {
        void *w = npm_line(i);
        int off = span[i] ? (int)((unsigned)span[i] * t / pass) : 0;
        if (w && I(w, HSCROLL_XOFFSET) != off) hscroll_label_set_xoffset(w, off);
    }
    /* the longest line's next pixel, or the landing */
    unsigned next = (((unsigned)longest * t / pass + 1) * pass + (unsigned)longest - 1) /
                    (unsigned)longest; /* rounded up: the frame lands on the pixel */
    npm_arm(now, next > pass ? pass - t : next - t ? next - t : 1);
    return 0;
}

/* A line of the page painted: the cycle runs if it isn't running. No ellipsis is set here, as
 * stock rebinds the labels' attributes with the page. */
void np_marq_kick(void *w) {
    if (!np_marq_owns(w)) return;
    if (B(w, HSCROLL_ELLIPSES)) hscroll_label_set_ellipses(w, 0);
    if (npm.timer || !npm_over(w)) return;
    npm.start = (unsigned)time_now_ms();
    npm_tick((void *)0);
}

/* A new text on a line (stock's start on a new text, ringnav_hscroll_start) or the page opening:
 * from rest again. Stock's own timer stops on each line: its page init starts the title's before
 * the payload binds the lines, and left running it moved the title through the hold and between
 * this clock's frames, so the line jumped back and forth. */
void np_marq_reset(void) {
    stop_timer(&npm.timer);
    npm.start = 0;
    for (int i = 0; i < NPM_LINES; ++i) {
        void *w = npm_line(i);
        if (np_marq_owns(w) && I(w, HSCROLL_TIMER)) hscroll_label_stop(w);
    }
    if (st.np_album && theme()->marquee_fade && I(st.np_album, HSCROLL_TIMER))
        hscroll_label_stop(st.np_album); /* one line, its ellipsis: never scrolled */
    npm_rest();
}

/* ms until the next frame the marquee wants, or max when it wants none that soon */
unsigned np_marq_wait(unsigned max) {
    if (!npm.timer) return max;
    unsigned left = npm.due - (unsigned)time_now_ms();
    return (int)left < 0 ? 0 : left < max ? left : max;
}
