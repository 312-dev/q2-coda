/* A set_switch theme's settings pages that are no list of rows, and the rows' captions (below).
 * About as a key/value table (docs/oled-design.md Screens: About): stock's rows, each a key and its
 * value, stay where set_row maps them and take the selection, so the wheel walks them and a value
 * cut off at its column scrolls on the selected row (the house marquee, navigation.c marq_moved),
 * but nothing opens: stock's row click (its serial number's QR code) gives way to one that does
 * nothing. The key is in Text Primary and the value in the theme's value grey, each value as wide
 * as its key leaves it (about_rows); from ipod_list_layout, after set_row.
 * A --dev build's Save Diagnostics row (diagnostics.c) keeps its own click. */
#include "navigation.h"

#define ABOUT_INK_STATES 4                 /* text_ink's normal, pressed, over and disable */
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
int kv_text_w(void *label) {
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

/* A label/value row's two hscroll_labels laid out by kv_fit (docs/oled-design.md#labelvalue-rows):
 * the key at its text's width kw, the value's box from KV_GAP after it to right, its text vw wide.
 * Either one cut short scrolls when its row is selected and fades at its cut end at rest (the
 * house marquee: ellipses off); one that fits keeps stock's ellipsis setting, unused. */
void kv_row(void *key, void *val, int kw, int vw, int right) {
    int x = I(key, W_X), box, lw = kv_fit(right - x - 4, kw, vw, &box); /* 2 px margins each */
    widget_move_resize(key, x, I(key, W_Y), lw + 4, I(key, W_H));
    widget_move_resize(val, right - box - 4, I(val, W_Y), box + 4, I(val, W_H));
    if (!tk_strcmp(widget_get_type(key), "hscroll_label"))
        hscroll_label_set_ellipses(key, lw >= kw);
    hscroll_label_set_ellipses(val, box >= vw);
}

/* Each row's first hscroll_label is its key, the next its value (stock's order, and
 * ringnav_about's for the product's version row). Stock's value box is 149 px from x 176, too
 * narrow for a dev build's version or a Wi-Fi MAC beside a short key: kv_row splits the row. This
 * runs on every layout, so the value's right edge, which set_child put on the right column, is the
 * one fixed point. */
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
        int kw = key ? kv_text_w(key) : -1, vw = kv_text_w(val), right = I(val, W_X) + I(val, W_W);
        if (kw < 0 || vw < 0) continue;
        kv_row(key, val, kw, vw, right);
    }
}

/* Settings rows with a caption (docs/oled-design.md#list-row, approved as S3 on 2026-11-15): the
 * caption shows only under the selected row, which grows over CAP_MS (at once under Reduce Motion)
 * to hold its title and the caption's lines inside the accent's bar; every other captioned row
 * stays one row high. The row's button keeps its CAP_TOP px at the item's top (set_row mapped it
 * once; a later layout leaves it alone), so the title and switch stay where they were and the
 * item's own height, which the settings list stacks by and the bar spans (navigation.c
 * paint_selection), makes the room. The caption is wrapped at words to the bar's text column, at
 * most CAP_LINES lines, measured at its first paint. One page's captions at a time, CAP_MAX of them
 * (caption_row drops any more): a row from another view clears the table. */
#define CAP_MAX 8
#define CAP_LINES 3
#define CAP_TOP 32    /* the row's own height, title and switch */
#define CAP_PX TYPE(13) /* Inter 13 px, AWTK's size (13 x 1.21) */
#define CAP_LINE 17   /* a line's pitch */
#define CAP_FOOT 5    /* under the last line, inside the bar */
#define CAP_INSET 8   /* the caption's left, past the bar's start: the title's column */
#define CAP_INK RGB_CAPTION_ON_ACCENT /* white, a little quieter than the title, on the accent */
#define CAP_MS 150
#define CAP_STEP_MS 16
static struct {
    void *view, *item[CAP_MAX];
    unsigned *text[CAP_MAX];               /* each caption widened, on the heap (cap_wide) */
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
/* Entry i emptied: its row and its caption go. */
static void cap_clear(int i) {
    if (cap.open == cap.item[i]) cap.open = (void *)0;
    if (cap.closing == cap.item[i]) cap.closing = (void *)0;
    free(cap.text[i]);
    cap.item[i] = (void *)0, cap.text[i] = (unsigned *)0, cap.lines[i] = 0;
    memset(cap.cut[i], 0, CAP_LINES);
}
static int cap_gone(void *view, void *event) {
    if (view != cap.view) return 0;
    stop_timer(&cap.timer);
    for (int i = 0; i < CAP_MAX; ++i) free(cap.text[i]);
    memset(&cap, 0, sizeof cap);
    return 0;
}
/* s, UTF-8 (a Bluetooth device's name in its caption is BlueZ's), as the canvas draws it: a code
 * unit a character (lyrics.c utf8_char: a byte that starts no valid sequence is its own Latin-1
 * character), on the heap; 0 with no memory. */
static unsigned *cap_wide(const char *s) {
    unsigned n = (unsigned)strlen(s), *w = calloc(n + 1, sizeof *w), k = 0;
    for (unsigned i = 0; w && i < n;) w[k++] = utf8_char((const unsigned char *)s, n, &i);
    return w;
}
/* Whether the widened w says s. */
static int cap_says(const unsigned *w, const char *s) {
    unsigned n = (unsigned)strlen(s), i = 0;
    while (i < n && *w && *w == utf8_char((const unsigned char *)s, n, &i)) ++w;
    return i >= n && !*w;
}
/* The row a list_row label stands in (its button's list_item) gets text as its caption, copied;
 * no text, none, a caption it had dropped. A row already in the table keeps its one entry, so a
 * caller may run on every layout: a second entry for the row, never measured, would set its height
 * in cap_step after the first. */
void caption_row(void *label, const char *text) {
    void *item = label ? P(P(label, W_PARENT), W_PARENT) : (void *)0,
         *view = item ? P(item, W_PARENT) : (void *)0;
    if (!view) return;
    if (!text) { /* its entry, if any, goes; the row is one row high at the next step */
        int i = view == cap.view ? cap_find(item) : -1;
        if (i < 0) return;
        cap_clear(i);
        I(item, W_H) = CAP_TOP;
        return;
    }
    if (view != cap.view) {
        cap_gone(cap.view, 0);
        cap.view = view;
        widget_on(view, EVT_DESTROY, cap_gone, view);
    }
    int had = cap_find(item);
    if (had >= 0) {
        unsigned *w;
        if (cap_says(cap.text[had], text) || !(w = cap_wide(text))) return;
        free(cap.text[had]); /* another caption: measured again at its next paint */
        cap.text[had] = w, cap.lines[had] = 0, memset(cap.cut[had], 0, CAP_LINES);
        return;
    }
    for (int i = 0; i < CAP_MAX; ++i)
        if (!cap.item[i]) {
            if ((cap.text[i] = cap_wide(text))) cap.item[i] = item;
            return;
        }
}
/* The Wi-Fi page rebuilds its rows whole, so the table's rows of the last build are gone: entries
 * for items no longer in view are dropped (their pointers only compared, never read). */
void caption_prune(void *view) {
    if (!cap.view || view != cap.view) return;
    for (int i = 0; i < CAP_MAX; ++i) {
        void *item = cap.item[i];
        int here = 0;
        for (unsigned k = 0, n = widget_count_children(view); item && k < n && !here; ++k)
            here = widget_get_child(view, k) == item;
        if (!item || here) continue;
        cap_clear(i);
    }
}
#define STR_(x) #x
#define STR(x) STR_(x)
/* Reset Settings (stock's reset_page; approved as 30 v2 on 2026-11-16): its three rows act at
 * once behind a confirmation card (feedback.c asks[]), so none has a chevron; under the selected
 * one a caption says what it takes, as stock's handlers do (feedback.c's asks[] note). Each row's
 * button is named by its index; its list_into image goes, insensitive too, as stock's hit test
 * skips only the insensitive. Runs on every layout; a row is captioned once. */
void reset_rows(void *view) {
    static const char *const caps[] = {
        "Keeps your music, playlists and ratings",
        "Erases playlists and Favorites, then rescans",
        "Erases all settings and playlists; music stays",
    };
    for (unsigned i = 0, n = widget_count_children(view); i < n; ++i) {
        void *item = widget_get_child(view, i),
             *b = widget_count_children(item) ? widget_get_child(item, 0) : (void *)0;
        const char *name = b ? widget_get_prop_str(b, "name", "") : "";
        if (!name || name[0] < '0' || name[0] > '2' || name[1]) continue;
        void *label = (void *)0;
        for (unsigned k = 0, m = widget_count_children(b); k < m; ++k) {
            void *c = widget_get_child(b, k);
            if (!tk_strcmp(widget_get_type(c), "hscroll_label") && !label)
                label = c;
            else if (!tk_strcmp(widget_get_prop_str(c, "image", ""), "list_into"))
                widget_set_visible(c, 0, 0), widget_set_sensitive(c, 0);
        }
        if (label && cap_find(item) < 0) caption_row(label, caps[name[0] - '0']);
    }
}
int reset_list(void *list) {
    return list && theme()->set_switch &&
           !tk_strcmp(widget_get_prop_str(window_of(list), "name", ""), "reset_page");
}

/* pod_rows' captions by POD_* (0: none), each one line at CAP_PX on OLED's settings bar, each from
 * what the code does: In-Vehicle Mode, stock's boot resume and its shutdown when the cable goes
 * (docs/internals.md#boot-resume-ipod); Standby Limit's alarm (system.c ringnav_standby); Charge
 * Limit's (system.c charge_poll); Wake's (navigation.c wake_gate); Hold Return's
 * (compact_now_playing); Fast Navigation's year letters and their hold (coverflow.c lettered,
 * coverflow_thumb.c coverflow_wheel_poll); Scan Song Durations' (durations.c durations_scanned);
 * the Position Bar (coverflow_position.c); Enlarged Album Art's countdown (offsets.inc BC_IDLE);
 * On-The-Go's rows (playlists.c pl_dress, navigation.c qm_open); Automatic Time Zone's
 * lookup (autozone.c), which sends the network's address to the service only while on. Volume Guard
 * has none. */
const char *pod_caption(int i) {
    static const char *const text[POD_COUNT] = {
        [POD_CARMODE] = "Plays at power-on, shuts down when unplugged",
        [POD_STANDBY] = "Shuts down a Q2 left in Standby this long",
        [POD_CHARGE] =
            "Stops charging at " STR(CHARGE_STOP) "%, starts again at " STR(CHARGE_RESUME) "%",
        [POD_WAKE] = "Any Button: every button wakes it. Double Press: Center twice",
        [POD_HOLD_HOME] = "What holding Return opens: Now Playing or Home",
        [POD_BIG_LIB] = "Year sort spins by year too; letters stay till you lift",
        [POD_SCAN_DURATIONS] = "Measures song lengths after each library update",
        [POD_CF_POSITION] = "Shows your place in the covers while you browse",
        [POD_COVERMODE] = "Shows the art full screen after 10s on Now Playing",
        [POD_OTG] = "Shows On-The-Go in Playlists and hold menus",
        [POD_AUTOZONE] = "Sets the zone from your network's location",
    };
    return i >= 0 && i < POD_COUNT ? text[i] : (const char *)0;
}
/* Smart Awake's (navigation.c keep_lit_poll), stock's Folder skip's (playback.c ringnav_next's
 * rollover) and Reduce Motion's (ui_kit.c motion_reduced): rows that are not pod_rows'. */
const char *const smart_awake_caption = "Keeps the screen on for lyrics and art while playing";
const char *const folder_skip_caption = "Plays on into the next folder when one ends";
const char *const reduce_motion_caption = "Fades in place of slides, zooms and bounces";

/* Captioned row i's grown height, its caption measured. */
static int cap_full(int i) { return CAP_TOP + cap.lines[i] * CAP_LINE + CAP_FOOT; }
/* The heights for the change's progress, the list laid out again, and the grown row kept in view,
 * the view moved by whole rows so the others stay on the list's grid and the grown row's foot on or
 * above the list end (nav/lists.c list_shown_h). */
static int cap_step(const void *unused) {
    unsigned t = (unsigned)time_now_ms() - cap.at, ms = (unsigned)motion_ms(CAP_MS, MO_SPATIAL);
    int k = t >= ms ? 256 : motion_ease((int)(t * 1000 / ms), EASE_QUAD_OUT) * 256 / 1000;
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
    if (o) list_grown_foot(cap.view, o, CAP_TOP); /* its foot on or above the list end */
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
