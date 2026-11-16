/* A set_switch theme's settings pages that are no list of rows, and the rows' captions (below).
 * About as a key/value table (docs/oled-design.md Screens: About): stock's rows, each a key and its
 * value, stay where set_row maps them and take the selection, so the wheel walks them and a value
 * cut off at its column scrolls on the selected row (the house marquee, navigation.c marq_moved),
 * but nothing opens: stock's row click (its serial number's QR code) gives way to one that does
 * nothing. The key is in Text Primary and the value in the theme's value grey, each value as wide
 * as its key leaves it (about_rows); from ipod_list_layout, after set_row.
 * A --dev build's Save Diagnostics row (diagnostics.c) keeps its own click. */
#include "navigation.h"

#define ABOUT_INK_STATES 4 /* text_ink's normal, pressed, over and disable */
#define ABOUT_GAP 12       /* between a key's text and the widest its value may reach */
#define ABOUT_ROW_CLICK ((void *)0x4c12f8) /* systemset_about_row_on_click */

/* A row's tap and Centre do nothing: About is to read. The click makes the row a wheel stop. */
static int about_click(void *ctx, void *event) { return 0; }
/* w's stock row click off, ours on, once. */
static void about_inert(void *w) {
    if (!w || widget_get_prop_int(w, "_about_click", 0)) return;
    void *emitter = P(w, W_EMITTER);
    for (void *e = emitter ? P(emitter, 0) : (void *)0; e; e = P(e, EMIT_NEXT))
        if (I(e, EMIT_TYPE) == EVT_CLICK && P(e, 12) == ABOUT_ROW_CLICK &&
            !B(e, EMIT_PENDING_REMOVE)) {
            widget_off_by_func(w, EVT_CLICK, ABOUT_ROW_CLICK, P(e, 0));
            break;
        }
    widget_on(w, EVT_CLICK, about_click, w);
    widget_set_prop_int(w, "_about_click", 1);
}

/* label's text's width in px, in the face and size its row draws it; -1 with no canvas yet */
static int about_text_w(void *label) {
    void *canvas = widget_get_canvas(label);
    const unsigned *s = widget_get_text(label);
    int px = theme()->font_px[ROLE_TITLE];
    if (!canvas || !s || !px) return -1;
    unsigned n = wide_len(s);
    canvas_set_font(canvas, theme_face(s, (int)n, &px), (unsigned)px);
    return (int)(canvas_measure_text(canvas, s, n) + 0.5f);
}

int about_list(void *list) {
    return list && theme()->set_switch &&
           !tk_strcmp(widget_get_prop_str(window_of(list), "name", ""), "about_page");
}

/* Each row's first hscroll_label is its key, the next its value (stock's order, and
 * ringnav_about's for CFW. Version). Stock's value box is 149 px from x 176, too narrow for a dev
 * build's version or a Wi-Fi MAC beside a short key: it takes the row from ABOUT_GAP after its
 * key's text to the right column, right-aligned, so it ends in an ellipsis only when it is wider
 * than that (docs/oled-design.md Type: truncate, never shrink). This runs on every layout, so the
 * value's right edge, which set_child put on the right column, is the one fixed point. */
void about_rows(void *view) {
    unsigned value = theme()->set_value_ink[ipod_light()],
             primary = flip_if(ipod_light(), 0xffffff); /* Text Primary: black in Light Mode */
    for (unsigned i = 0, n = widget_count_children(view); i < n; ++i) {
        void *item = widget_get_child(view, i),
             *b = widget_count_children(item) ? widget_get_child(item, 0) : (void *)0,
             *key = (void *)0, *val = (void *)0;
        if (diag_row(item)) continue; /* --dev builds' Save Diagnostics: its own click */
        about_inert(b);
        for (unsigned j = 0, k = b ? widget_count_children(b) : 0; j < k && !val; ++j) {
            void *c = widget_get_child(b, j);
            if (tk_strcmp(widget_get_type(c), "hscroll_label")) continue;
            *(key ? &val : &key) = c;
        }
        if (key) text_ink(key, 0xff000000u | primary, ABOUT_INK_STATES);
        if (!val) continue;
        text_ink(val, 0xff000000u | (value ? value : primary), ABOUT_INK_STATES);
        int kw = key ? about_text_w(key) : -1, right = I(val, W_X) + I(val, W_W);
        if (kw < 0) continue;
        int left = I(key, W_X) + 2 + kw + ABOUT_GAP; /* after hscroll_label's 2 px margin */
        if (left < right) widget_move_resize(val, left, I(val, W_Y), right - left, I(val, W_H));
    }
}

/* Settings rows with a caption (docs/oled-design.md#list-row, approved as S3 on 2026-11-15): the
 * caption shows only under the selected row, which grows over CAP_MS to hold its title and the
 * caption's lines inside the accent's bar; every other captioned row stays one row high. The row's
 * button keeps its CAP_TOP px at the item's top (set_row mapped it once; a later layout leaves it
 * alone), so the title and switch stay where they were and the item's own height, which the
 * settings list stacks by and the bar spans (navigation.c paint_selection), makes the room. The
 * caption is wrapped at words to the bar's text column, at most CAP_LINES lines, measured at its
 * first paint. One page's captions at a time, CAP_MAX of them (caption_row drops any more): a row
 * from another view clears the table. */
#define CAP_MAX 8
#define CAP_LINES 3
#define CAP_TOP 32    /* the row's own height, title and switch */
#define CAP_PX 16     /* Inter 13 px, AWTK's size (13 x 1.21) */
#define CAP_LINE 17   /* a line's pitch */
#define CAP_FOOT 5    /* under the last line, inside the bar */
#define CAP_INSET 8   /* the caption's left, past the bar's start: the title's column */
#define CAP_INK 0xd6e2fa /* white, a little quieter than the title, on the accent */
#define CAP_MS 150
#define CAP_STEP_MS 16
static struct {
    void *view, *item[CAP_MAX];
    const unsigned *text[CAP_MAX];
    unsigned char cut[CAP_MAX][CAP_LINES]; /* each line's end in text; 0: not yet measured */
    unsigned char lines[CAP_MAX];
    void *open, *closing; /* the row growing (or grown) and the one shrinking back */
    unsigned timer, at;   /* the change's timer and its start, time_now_ms */
} cap __attribute__((section(".scratch")));

static int cap_find(void *item) {
    for (int i = 0; i < CAP_MAX; ++i)
        if (item && cap.item[i] == item) return i;
    return -1;
}
static int cap_gone(void *view, void *event) {
    if (view != cap.view) return 0;
    stop_timer(&cap.timer);
    memset(&cap, 0, sizeof cap);
    return 0;
}
/* The row a list_row label stands in (its button's list_item) gets text as its caption; no text,
 * none. */
void caption_row(void *label, const unsigned *text) {
    void *item = label && text ? P(P(label, W_PARENT), W_PARENT) : (void *)0,
         *view = item ? P(item, W_PARENT) : (void *)0;
    if (!view) return;
    if (view != cap.view) {
        cap_gone(cap.view, 0);
        cap.view = view;
        widget_on(view, EVT_DESTROY, cap_gone, view);
    }
    for (int i = 0; i < CAP_MAX; ++i)
        if (!cap.item[i]) {
            cap.item[i] = item, cap.text[i] = text;
            return;
        }
}
#define STR_(x) #x
#define STR(x) STR_(x)
#define W(s) ((const unsigned *)L##s)
/* pod_rows' captions by POD_* (0: none), each one line at CAP_PX on OLED's settings bar, each from
 * what the code does: In-Vehicle Mode, stock's boot resume and its shutdown when the cable goes
 * (docs/internals.md#boot-resume-ipod); Standby Limit's alarm (system.c ringnav_standby); Charge
 * Limit's (system.c charge_poll); Wake's (navigation.c wake_gate); Hold Return's
 * (compact_now_playing); Fast Navigation's year letters and their hold (coverflow.c lettered,
 * coverflow_thumb.c coverflow_wheel_poll); Scan Song Durations' (durations.c durations_scanned);
 * the Position Bar (coverflow_position.c); Enlarged Album Art's countdown (offsets.inc BC_IDLE);
 * On-The-Go's rows (playlists.c pl_dress, navigation.c qm_open). Volume Guard has none. */
const unsigned *pod_caption(int i) {
    static const unsigned *const text[POD_COUNT] = {
        [POD_CARMODE] = W("Plays at power-on, shuts down when unplugged"),
        [POD_STANDBY] = W("Shuts down a Q2 left in Standby this long"),
        [POD_CHARGE] =
            W("Stops charging at " STR(CHARGE_STOP) "%, starts again at " STR(CHARGE_RESUME) "%"),
        [POD_WAKE] = W("Center presses it takes to wake the screen"),
        [POD_HOLD_HOME] = W("What holding Return opens: Now Playing or Home"),
        [POD_BIG_LIB] = W("Year sort spins by year too; letters stay till you lift"),
        [POD_SCAN_DURATIONS] = W("Measures song lengths after each library update"),
        [POD_CF_POSITION] = W("Shows your place in the covers while you browse"),
        [POD_COVERMODE] = W("Shows the art full screen after 10s on Now Playing"),
        [POD_OTG] = W("Shows On-The-Go in Playlists and hold menus"),
    };
    return i >= 0 && i < POD_COUNT ? text[i] : (const unsigned *)0;
}
/* Smart Awake's (navigation.c keep_lit_poll) and stock's Folder skip's (playback.c ringnav_next's
 * rollover): rows that are not pod_rows'. */
const unsigned *const smart_awake_caption =
    W("Keeps the screen on for lyrics and art while playing");
const unsigned *const folder_skip_caption = W("Plays on into the next folder when one ends");

/* Captioned row i's grown height, its caption measured. */
static int cap_full(int i) { return CAP_TOP + cap.lines[i] * CAP_LINE + CAP_FOOT; }
/* The heights for the change's progress, the list laid out again, and the grown row kept in view,
 * the view moved by whole rows so the others stay on the list's grid. */
static int cap_step(const void *unused) {
    unsigned t = (unsigned)time_now_ms() - cap.at;
    int k = t >= CAP_MS ? 256 : (int)(t * 256 / CAP_MS);
    k = 256 - (256 - k) * (256 - k) / 256; /* ease out */
    for (int i = 0; i < CAP_MAX; ++i) {
        void *item = cap.item[i];
        if (!item) continue;
        int full = cap_full(i), h = item == cap.open      ? CAP_TOP + (full - CAP_TOP) * k / 256
                                    : item == cap.closing ? full - (full - CAP_TOP) * k / 256
                                                          : CAP_TOP;
        I(item, W_H) = h;
    }
    void *list = P(cap.view, W_PARENT);
    if (list) widget_layout(list);
    void *o = cap.open;
    if (o) { /* its foot in sight: the view scrolls by whole rows, as far as it overruns */
        int over = I(o, W_Y) + I(o, W_H) - I(cap.view, SCROLL_Y) - I(cap.view, W_H);
        if (over > 0)
            scroll_view_set_offset(cap.view, I(cap.view, SCROLL_X),
                                   I(cap.view, SCROLL_Y) + (over + CAP_TOP - 1) / CAP_TOP * CAP_TOP);
    }
    widget_invalidate_force(cap.view, (void *)0);
    if (k < 256) return 8; /* RET_REPEAT */
    cap.timer = 0, cap.closing = (void *)0;
    return 0;
}
/* Each paint of a settings list's selection (navigation.c paint_selection): item the selected
 * row's list_item, 0 with none showing. A change starts the rows' grow and shrink. */
void caption_sel(void *view, void *item) {
    if (!cap.view || view != cap.view) return;
    void *open = cap_find(item) >= 0 ? item : (void *)0;
    if (open == cap.open) return;
    int i = cap_find(open);
    if (i >= 0 && !cap.lines[i]) return; /* measured at its first paint, below; then it grows */
    cap.closing = cap.open, cap.open = open;
    cap.at = (unsigned)time_now_ms();
    if (!cap.timer) cap.timer = timer_add(cap_step, (void *)0, CAP_STEP_MS);
}
/* The selected row's caption, after its bar (bar: the bar's box in the list's coordinates): the
 * lines the bar holds by now, from CAP_TOP down. Measured, then wrapped, at the first paint. */
void caption_paint(void *view, void *item, void *canvas, const rect_t *bar) {
    int i = view == cap.view ? cap_find(item) : -1;
    if (i < 0) return;
    const unsigned *s = cap.text[i];
    int px = CAP_PX, w = bar->w - 2 * CAP_INSET;
    unsigned n = wide_len(s);
    canvas_set_font(canvas, theme_face(s, (int)n, &px), (unsigned)px);
    if (!cap.lines[i]) { /* greedy, at spaces: a word wider than the line ends it there */
        unsigned at = 0;
        while (at < n && cap.lines[i] < CAP_LINES) {
            unsigned end = at, best = 0;
            while (end < n) {
                unsigned e = end + 1;
                while (e < n && s[e] != ' ') ++e;
                if (best && canvas_measure_text(canvas, s + at, e - at) > (float)w) break;
                best = end = e;
            }
            cap.cut[i][cap.lines[i]++] = (unsigned char)(best > 255 ? 255 : best);
            at = best;
            while (at < n && s[at] == ' ') ++at;
        }
        caption_sel(view, item); /* measured: now it can grow */
    }
    int align_h = I(canvas, CANVAS_ALIGN_H), align_v = I(canvas, CANVAS_ALIGN_V);
    I(canvas, CANVAS_ALIGN_H) = 2, I(canvas, CANVAS_ALIGN_V) = 1; /* AWTK's left, middle */
    canvas_set_text_color(canvas, RGBA(CAP_INK));
    unsigned from = 0;
    for (int l = 0; l < cap.lines[i]; ++l) {
        unsigned to = cap.cut[i][l];
        rect_t r = { bar->x + CAP_INSET, bar->y + CAP_TOP + l * CAP_LINE, w, CAP_LINE };
        if (r.y + r.h > bar->y + bar->h) break; /* not grown that far yet */
        canvas_draw_text_in_rect(canvas, s + from, to - from, &r);
        from = to;
        while (from < n && s[from] == ' ') ++from;
    }
    I(canvas, CANVAS_ALIGN_H) = align_h, I(canvas, CANVAS_ALIGN_V) = align_v;
}
