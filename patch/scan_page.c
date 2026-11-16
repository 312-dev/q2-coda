/* OLED's form of the library scan's message screen (oled_scan): Update Local Music's dialog as a
 * page of rows. */
#include "navigation.h"

/* The scan's page (msg_open; st.msg_win is its window): stock's label_msg in the dialog, the Songs
 * value, btn_no, the Folder value, the songs before the scan, the bar's part in px, stock's text as
 * last read and the poll following it. msg_counting: 1 msg_count runs, 3 + n it has answered n (-1
 * failed): one word, so the UI thread can't see the flag before the value. */
static struct {
    void *msg_src, *msg_line, *msg_btn, *msg_folder;
    int msg_total;
    volatile int msg_counting;
    short msg_part;
    unsigned msg_timer;
    char msg_seen[128];
} cp __attribute__((section(".scratch")));

/* The scan page (oled_scan): its rows, columns and bar, in screen rows; the window's own origin is
 * read when it paints */
#define SCAN_TOP 58
#define SCAN_ROW 32
#define SCAN_X 24
#define SCAN_RIGHT 347 /* the right column */
#define SCAN_NAME_W 110
#define SCAN_BAR_Y 228
#define SCAN_BAR_W (SCAN_RIGHT - SCAN_X)
static int msg_gone(void *win, void *event) {
    if (win == st.msg_win) {
        stop_timer(&cp.msg_timer);
        st.msg_win = cp.msg_folder = (void *)0;
    }
    return 0;
}
/* Follows stock's label_msg, which it writes from the running count in the language's format, one
 * number, and which is hidden here: "312 of 704" and the bar's part, held under its end until the
 * dialog closes. With no total to measure against (the first scan, a scan past it, a folder's scan,
 * or until msg_count answers) the line is the count alone, or stock's text before it has one, and
 * cp.msg_part -1 hides the bar. Whether anything changed. */
static int msg_sync(void) {
    char now[sizeof cp.msg_seen], line[48];
    *now = 0;
    if (cp.msg_folder) { /* the Folder row: the last name in stock's hidden label_scanfolder */
        void *from = widget_lookup(st.msg_win, "label_scanfolder", 1);
        char was[128];
        if (from && !widget_get_text_utf8(from, now, sizeof now)) {
            int n = (int)strlen(now);
            while (n > 1 && now[n - 1] == '/') now[--n] = 0; /* "SD:/Music/" */
            const char *last = now + n;
            while (last > now && last[-1] != '/') --last;
            if (!*last) last = now; /* the card's root, "SD:/" */
            if (widget_get_text_utf8(cp.msg_folder, was, sizeof was) || tk_strcmp(last, was))
                widget_set_text_utf8(cp.msg_folder, last);
        }
        *now = 0;
    }
    int answer = cp.msg_counting;
    if (answer > 1) { /* the total: read stock's text against it */
        cp.msg_total = answer - 3, cp.msg_counting = 0, cp.msg_seen[0] = 0;
    }
    if (!cp.msg_seen[0]) { /* over stock's translation; runs in paints, so only if it differs */
        widget_get_text_utf8(cp.msg_btn, now, sizeof now);
        if (tk_strcmp(now, "Cancel")) widget_set_text_utf8(cp.msg_btn, "Cancel");
        *now = 0;
    }
    widget_get_text_utf8(cp.msg_src, now, sizeof now);
    if (!tk_strcmp(now, cp.msg_seen)) return 0;
    tk_snprintf(cp.msg_seen, sizeof cp.msg_seen, "%s", now);
    const char *p = now;
    while (*p && (*p < '0' || *p > '9')) ++p;
    int count = atoi(p), total = cp.msg_total, w = SCAN_BAR_W;
    cp.msg_part = -1;
    if (*p && total && count <= total && *(volatile char *)SCAN_WHOLE == 1) {
        cp.msg_part = (short)(w * count / total);
        if (cp.msg_part > w * 99 / 100) cp.msg_part = (short)(w * 99 / 100);
        tk_snprintf(line, sizeof line, "%d of %d", count, total);
        widget_set_text_utf8(cp.msg_line, line);
    } else if (*p) { /* the Songs row's value: the count alone */
        tk_snprintf(line, sizeof line, "%d", count);
        widget_set_text_utf8(cp.msg_line, line);
    } else
        widget_set_text_utf8(cp.msg_line, now);
    return 1;
}
/* Stock's hidden label repaints nothing, so this does, at the rate stock writes it. */
static int msg_poll(const void *info) {
    (void)info;
    if (msg_sync())
        widget_invalidate_force(st.msg_win, &(rect_t){ 0, SCAN_TOP, 375, 282 - SCAN_TOP });
    return 8; /* RET_REPEAT */
}
int library_count(const char *sql);
/* The songs before the scan, off the UI thread as coverflow.c's count_worker: a count, as a
 * 47,000-song library's rows are too many to hold beside the scan. Detached, as nothing may wait
 * for it when the dialog closes; msg_sync takes the answer. */
static void *msg_count(void *unused) {
    /* main.: the card's files alone, which the scan walks (no server library union, catalogue.c) */
    cp.msg_counting = 3 + library_count("select count(*) from main.songtable");
    return 0;
}
int msg_counting(void) { return cp.msg_counting == 1; } /* coverflow.c's library_counting */
/* The scan page (docs/oled-design.md#screens): a page over stock's full-screen dialog, its title
 * in the status row's place and face (Inter Medium 15, Text Secondary, from STATUS_ROW_INSET; the
 * dialog covers the bar, so its play state and battery are not shown), then rows from y 58: Songs
 * with the count (or "312 of 704" against the library's songs before the scan), Folder with the
 * name of the folder stock is scanning, and Cancel, stock's btn_no as the page's one action row on
 * the selection bar; the 6 px progress bar at y 228 from the text column to the right column while
 * the scan is measurable (msg_sync's part), as RULES.md 12 has it. Sizes are AWTK's, the design's
 * px times 1.21. */
static void *scan_label(void *win, const char *s, int x, int y, int w, const char *face, int px,
                        unsigned ink, const char *align) {
    void *l = text(win, x - 2, y, w + 4, SCAN_ROW); /* hscroll_label's own 2 px margins */
    if (!l) return 0;
    if (s) widget_set_text_utf8(l, s);
    slider_label(l, x - 2, y, w + 4, SCAN_ROW, px, face);
    widget_set_prop_str(l, "style:normal:text_align_h", align);
    text_ink(l, ink, INK_LABEL);
    hscroll_label_set_only_focus(l, 1), hscroll_label_set_ellipses(l, 1);
    return l;
}
static void oled_scan(void *win, void *src, void *btn) {
    static const char *const state[] = { "normal", "pressed", "over", "disable", "focused" };
    char key[STYLE_KEY];
    void *c =
        scan_label(win, "Update Library", STATUS_ROW_INSET, geom_val(V_STATUS_Y) - SCAN_ROW / 2,
                   240, "inter-medium", 18, 0xffababab, "left");
    if (c) widget_set_name(c, "msg_heading");
    scan_label(win, "Songs", SCAN_X, SCAN_TOP, SCAN_NAME_W, "inter", 24, 0xffffffff, "left");
    scan_label(win, "Folder", SCAN_X, SCAN_TOP + SCAN_ROW, SCAN_NAME_W, "inter", 24, 0xffffffff,
               "left");
    int vx = SCAN_X + SCAN_NAME_W, vw = SCAN_RIGHT - vx;
    cp.msg_line = scan_label(win, 0, vx, SCAN_TOP, vw, "inter", 24, 0xffababab, "right");
    cp.msg_folder =
        scan_label(win, 0, vx, SCAN_TOP + SCAN_ROW, vw, "inter", 24, 0xffababab, "right");
    if (cp.msg_line) widget_set_name(cp.msg_line, "msg_line");
    widget_move_resize(btn, 16, SCAN_TOP + 2 * SCAN_ROW, 343, SCAN_ROW);
    for (unsigned i = 0; i < sizeof state / sizeof *state; ++i) {
        widget_set_prop_int(btn, style_key(key, state[i], "bg_color"),
                            (int)RGBA(scheme()->tones[0]));
        widget_set_prop_int(btn, style_key(key, state[i], "round_radius"), 8);
        widget_set_prop_str(btn, style_key(key, state[i], "font_name"), "inter");
        widget_set_prop_int(btn, style_key(key, state[i], "font_size"), 24);
        widget_set_prop_str(btn, style_key(key, state[i], "text_align_h"), "left");
        widget_set_prop_int(btn, style_key(key, state[i], "margin_left"), SCAN_X - 16);
    }
    text_ink(btn, 0xffffffffu, INK_BUTTON);
    cp.msg_src = src, cp.msg_btn = btn;
}
/* Stock's window as it loads, before its init. Every look-up stock makes by name still finds its
 * widget, and btn_no keeps its binding. USB's page stays stock's. */
void msg_open(void *win) {
    static const char *const hide[] = { "img_animation", "label_msg", "label_scanfolder" };
    void *c, *src = widget_lookup(win, "label_msg", 1), *btn = widget_lookup(win, "btn_no", 1);
    if (tk_strcmp(widget_get_prop_str(win, "name", ""), "updatemusic_dialog") || !src || !btn)
        return;
    for (unsigned i = 0; i < sizeof hide / sizeof *hide; ++i)
        if ((c = widget_lookup(win, hide[i], 1))) widget_set_visible(c, 0, 0);
    widget_on(win, EVT_DESTROY, msg_gone, win);
    cp.msg_folder = 0;
    st.msg_win = win;
    oled_scan(win, src, btn);
    cp.msg_seen[0] = 0, cp.msg_part = -1, cp.msg_total = 0;
    if (cp.msg_counting != 1) { /* one running answers this dialog too */
        unsigned long thread;
        cp.msg_counting = 1; /* before it starts: it may answer before create returns */
        if (pthread_create(&thread, 0, msg_count, 0))
            cp.msg_counting = 0;
        else
            pthread_detach(thread);
    }
    cp.msg_timer = timer_add(msg_poll, (void *)0, 200);
}
/* Under the window's children: the rows are widgets; the bar while measurable. */
void msg_paint(void *w, void *canvas) {
    void *lcd = P(canvas, CANVAS_LCD);
    if (!lcd || w != st.msg_win) return;
    unsigned fill = (unsigned)I(lcd, LCD_FILL_COLOR);
    int dy = I(w, W_Y);
    msg_sync();
    rect_t track = { SCAN_X, SCAN_BAR_Y - dy, SCAN_BAR_W, 6 }, part = track;
    part.w = cp.msg_part < 6 ? 6 : cp.msg_part;
    unsigned rest = ipod_light() ? 0xffd1d1d1u : 0xff000000u | theme()->np_track_rgb;
    for (int i = 0; cp.msg_part >= 0 && i < 6; ++i) {
        round_row(canvas, &track, 3, i, rest);
        round_row(canvas, &part, 3, i, 0xff000000u | scheme()->tones[0]);
    }
    stock_canvas_set_fill_color(canvas, fill);
}
