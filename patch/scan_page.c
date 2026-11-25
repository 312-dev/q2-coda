/* OLED's form of the library scan's message screen (oled_scan): Update Local Music's dialog as the
 * empty states' stack (docs/oled-design.md#update-library). */
#include "navigation.h"

/* The scan's page (msg_open; st.msg_win is its window): stock's label_msg in the dialog, btn_no,
 * the folder's line, the songs before the scan, the count stock last wrote, the ring's percent
 * (-1: no total to measure against), the spinner's frame, stock's text as last read and the poll
 * following it. msg_counting: 1 msg_count runs, 3 + n it has answered n (-1 failed): one word, so
 * the UI thread can't see the flag before the value. */
static struct {
    void *msg_src, *msg_btn, *msg_folder;
    int msg_total, msg_count;
    volatile int msg_counting;
    short msg_pct, msg_frame, msg_dirty;
    unsigned msg_timer;
    char msg_seen[128];
} cp __attribute__((section(".scratch")));

/* The stack (27 v4), centred from the status row's foot (SCAN_TOP) to the screen's end: a
 * SCAN_FIG figure, the title in Inter Medium 20, the line in 15 px Text Secondary, then the shared
 * button; in screen rows, the window's own origin read when it paints. */
#define SCAN_TOP 40
#define SCAN_END 320
#define SCAN_FIG 60
#define SCAN_FIG_GAP 14
#define SCAN_TITLE_H 24
#define SCAN_LINE_H 20
#define SCAN_BTN_GAP 20
#define SCAN_STACK (SCAN_FIG + SCAN_FIG_GAP + SCAN_TITLE_H + SCAN_LINE_H + SCAN_BTN_GAP + CTA_H)
#define SCAN_Y (SCAN_TOP + (SCAN_END - SCAN_TOP - SCAN_STACK) / 2) /* the figure's top */
#define SCAN_FIG_CY (SCAN_Y + SCAN_FIG / 2)
#define SCAN_TITLE_CY (SCAN_Y + SCAN_FIG + SCAN_FIG_GAP + SCAN_TITLE_H / 2)
#define SCAN_LINE_CY (SCAN_TITLE_CY + SCAN_TITLE_H / 2 + SCAN_LINE_H / 2)
#define SCAN_BTN_CY (SCAN_LINE_CY + SCAN_LINE_H / 2 + SCAN_BTN_GAP + CTA_H / 2)
#define SCAN_RING_R 26 /* the ring's line's middle: 56 px across, its 4 px line included */
#define SCAN_RING_LINE 4
#define SCAN_SPIN_PX                                                                               \
    48 /* tools/art.py SW_SPIN_PX, Software Update's spinner, in the figure's box */
#define SCAN_FOLDER_PX 15 /* tools/art.py SCAN_FOLDER_PX */
#define SCAN_FOLDER_GAP 6 /* the folder glyph to its name */
#define SCAN_NAME_W 240   /* the folder's name at most, then it scrolls (the house marquee) */
#define SCAN_SPIN_MS 80   /* a spinner frame of the twelve, as Software Update's */
/* How the scan stands: running, or over (stock's SCAN_DONE counter set by wait_scan) and why, by
 * toolsGetUserStop: 0 it ran to the end, 1 Stop (toolsStopUpdateMusic), 2 /mnt/data filled up
 * (tools_insert_song_to_db; stock's msg_no_storage). */
enum { SCAN_RUNNING, SCAN_FINISHED, SCAN_STOPPED, SCAN_FULL };
static int scan_state(void) {
    if (*(volatile int *)SCAN_DONE <= 0) return SCAN_RUNNING;
    int stop = toolsGetUserStop();
    return stop == 2 ? SCAN_FULL : stop ? SCAN_STOPPED : SCAN_FINISHED;
}

static int msg_gone(void *win, void *event) {
    if (win == st.msg_win) {
        stop_timer(&cp.msg_timer);
        st.msg_win = cp.msg_folder = (void *)0;
    }
    return 0;
}
/* Follows stock's label_msg, which it writes from the running count in the language's format, one
 * number (stock's msg_no_storage has none: the count stays the last one), and which is hidden
 * here, as the folder's line follows its hidden label_scanfolder; a change sets cp.msg_dirty. The
 * ring's percent holds under 100 until the scan ends; with no total to measure against (the first
 * scan, a scan past it, a folder's scan, or until msg_count answers) cp.msg_pct is -1 and the
 * spinner turns. Whether anything changed. */
static int msg_sync(void) {
    char now[sizeof cp.msg_seen];
    int changed = 0;
    *now = 0;
    if (cp.msg_folder) { /* the folder's line: the last name in stock's hidden label_scanfolder */
        void *from = widget_lookup(st.msg_win, "label_scanfolder", 1);
        char was[128];
        if (from && !widget_get_text_utf8(from, now, sizeof now)) {
            int n = (int)strlen(now);
            while (n > 1 && now[n - 1] == '/') now[--n] = 0; /* "SD:/Music/" */
            const char *last = now + n;
            while (last > now && last[-1] != '/') --last;
            if (!*last) last = now; /* the card's root, "SD:/" */
            if (widget_get_text_utf8(cp.msg_folder, was, sizeof was) || tk_strcmp(last, was))
                widget_set_text_utf8(cp.msg_folder, last), changed = cp.msg_dirty = 1;
        }
        *now = 0;
    }
    int answer = cp.msg_counting;
    if (answer > 1) /* the total: read stock's text against it */
        cp.msg_total = answer - 3, cp.msg_counting = 0, cp.msg_seen[0] = 0;
    widget_get_text_utf8(cp.msg_src, now, sizeof now);
    if (!tk_strcmp(now, cp.msg_seen) && cp.msg_seen[0]) return changed;
    tk_snprintf(cp.msg_seen, sizeof cp.msg_seen, "%s", now);
    const char *p = now;
    while (*p && (*p < '0' || *p > '9')) ++p;
    if (*p) cp.msg_count = atoi(p);
    int total = cp.msg_total, count = cp.msg_count;
    cp.msg_pct = -1;
    if (*p && total > 0 && count <= total && *(volatile char *)SCAN_WHOLE == 1)
        cp.msg_pct = (short)(count * 100 / total > 99 ? 99 : count * 100 / total);
    cp.msg_dirty = 1;
    return 1;
}
/* Stock's hidden labels repaint nothing, so this does, at the spinner's rate while it turns and
 * whenever stock writes them (cp.msg_dirty: a stock repaint of only its hidden label's box may
 * have run msg_sync first, and the stack's every line follows the change). */
static int msg_poll(const void *info) {
    (void)info;
    int spin = scan_state() == SCAN_RUNNING && cp.msg_pct < 0;
    if (spin) cp.msg_frame = (short)((cp.msg_frame + 1) % 12);
    msg_sync();
    if (cp.msg_dirty || spin) /* a change a paint's own msg_sync took first repaints all too */
        cp.msg_dirty = 0,
        widget_invalidate_force(st.msg_win, &(rect_t){ 0, SCAN_TOP, 375, SCAN_END - SCAN_TOP });
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
/* The bar's hidden title over the scan's dialog (navigation.c bar_title_of), which paints the bar
 * it covers as Control Center does (msg_paint); 0 for another window. */
const char *msg_title(const char *name) {
    return !tk_strcmp(name, "updatemusic_dialog") ? "Update Library" : (const char *)0;
}
/* The scan page (docs/oled-design.md#update-library): the folder's name as a label the house
 * marquee scrolls, and stock's btn_no laid over the painted button, itself unseen, so a tap and
 * Centre (ringnav's click on it) answer as stock's. */
static void *scan_label(void *win, const char *s, int x, int y, int w, int h, const char *face,
                        int px, unsigned ink, const char *align) {
    void *l = text(win, x - 2, y, w + 4, h); /* hscroll_label's own 2 px margins */
    if (!l) return 0;
    if (s) widget_set_text_utf8(l, s);
    slider_label(l, x - 2, y, w + 4, h, px, face);
    widget_set_prop_str(l, "style:normal:text_align_h", align);
    text_ink(l, ink, INK_LABEL);
    return l;
}
static void oled_scan(void *win, void *src, void *btn) {
    static const char *const state[] = { "normal", "pressed", "over", "disable", "focused" };
    char key[STYLE_KEY];
    cp.msg_folder =
        scan_label(win, 0, (375 - SCAN_NAME_W) / 2, SCAN_LINE_CY - SCAN_LINE_H / 2, SCAN_NAME_W,
                   SCAN_LINE_H, "inter", TYPE(15), 0xff000000u | RGB_SECONDARY, "left");
    if (cp.msg_folder) {
        widget_set_name(cp.msg_folder, "msg_folder");
        hscroll_label_set_only_focus(cp.msg_folder, 0); /* a long name runs as the marquee */
    }
    widget_move_resize(btn, (375 - 160) / 2, SCAN_BTN_CY - CTA_H / 2, 160, CTA_H);
    for (unsigned i = 0; i < sizeof state / sizeof *state; ++i) {
        widget_set_prop_int(btn, style_key(key, state[i], "bg_color"), 0);
        widget_set_prop_int(btn, style_key(key, state[i], "border_color"), 0);
        widget_set_prop_int(btn, style_key(key, state[i], "text_color"), 0);
    }
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
    cp.msg_seen[0] = 0, cp.msg_pct = -1, cp.msg_total = cp.msg_count = cp.msg_frame = 0;
    if (cp.msg_counting != 1) { /* one running answers this dialog too */
        unsigned long thread;
        cp.msg_counting = 1; /* before it starts: it may answer before create returns */
        if (pthread_create(&thread, 0, msg_count, 0))
            cp.msg_counting = 0;
        else
            pthread_detach(thread);
    }
    cp.msg_timer = timer_add(msg_poll, (void *)0, SCAN_SPIN_MS);
}
/* n with a comma between thousands, as the design writes counts ("1,284"). */
static void scan_num(char *s, int size, int n) {
    if (n >= 1000)
        tk_snprintf(s, size, "%d,%03d", n / 1000, n % 1000);
    else
        tk_snprintf(s, size, "%d", n);
}
/* The ring (late, a total known): Track all round, the accent from 12 o'clock clockwise to the
 * percent, the percent in Inter Medium 16 inside. cy in the canvas's rows. */
static void scan_ring(void *canvas, int cy, int pct, unsigned accent) {
    void *vg = canvas_get_vgcanvas(canvas);
    char s[8];
    if (vg) {
        float cx = 375 / 2.0f, fy = (float)cy;
        vgcanvas_save(vg);
        vgcanvas_translate(vg, (float)I(canvas, CANVAS_X), (float)I(canvas, CANVAS_Y));
        vgcanvas_set_line_width(vg, SCAN_RING_LINE);
        vgcanvas_begin_path(vg);
        vgcanvas_arc(vg, cx, fy, SCAN_RING_R, 0, 6.2831853f, 0);
        vgcanvas_set_stroke_color(vg, RGBA(tok(TOK_TRACK)));
        vgcanvas_stroke(vg);
        if (pct > 0) {
            vgcanvas_begin_path(vg);
            vgcanvas_arc(vg, cx, fy, SCAN_RING_R, -1.5707963f,
                         -1.5707963f + 6.2831853f * (float)pct / 100, 0);
            vgcanvas_set_stroke_color(vg, RGBA(accent));
            vgcanvas_stroke(vg);
        }
        vgcanvas_restore(vg);
    }
    tk_snprintf(s, sizeof s, "%d%%", pct);
    ui_text(canvas, s, 1, TYPE(16), tok(TOK_PRIMARY), 1, 375 / 2, cy, SCAN_FIG, 1);
}
/* Under the window's children (the folder's name, stock's unseen button): the status row, then the
 * figure, the title,
 * the line or the folder's glyph before its name, and the button (27 v4):
 *
 * - scanning, no total yet: the spinner, "1,284 songs found", the folder (or "Scanning the SD card"
 *   while stock names none: it writes label_scanfolder on a whole scan alone), Stop (grey);
 * - scanning against a total: the ring, "312 of 704 songs", the folder, Stop;
 * - over: the check circle, "Library Updated" and the songs ("738 songs, 34 new" when a whole
 *   scan measured them against the library before it), Done (the accent);
 * - stopped by Stop: the check circle, "Scan Stopped", the songs found;
 * - /mnt/data full: the memory chip, "Not Enough Space", "Some songs weren't added".
 * - over with the card gone: the memory chip, "SD Card Removed", "Put it back and scan again".
 *
 * Stock shows its ending's text three polls, then sleeps 2 s on the UI thread and quits the
 * dialog: the ending holds that long and closes by itself, Done or not. */
void msg_paint(void *w, void *canvas) {
    void *lcd = P(canvas, CANVAS_LCD);
    if (!lcd || w != st.msg_win) return;
    unsigned fill = (unsigned)I(lcd, LCD_FILL_COLOR), text = (unsigned)I(lcd, LCD_TEXT_COLOR),
             raw = st.raw_ink, accent = scheme()->tones[0] & 0xffffffu;
    int dy = I(w, W_Y), light = ipod_light(), state = scan_state();
    char title[48], line[48], n[16], m[16];
    void *bar = *(void *const *)system_bar;
    if (bar) widget_paint(bar, canvas); /* the status row the dialog covers, its title msg_title */
    msg_sync();
    st.raw_ink = 1;
    /* a finish with the card gone (stock's /tmp/mmc_add, docs/oled-design.md#update-library): stock
     * ends a scan the card left as a finish, its library cut to the songs it had read */
    int gone = state == SCAN_FINISHED && access("/tmp/mmc_add", 0);
    const char *glyph = state == SCAN_FULL || gone ? "card" : "done";
    scan_num(n, sizeof n, cp.msg_count);
    *line = 0;
    if (state == SCAN_RUNNING && cp.msg_pct >= 0) {
        scan_num(m, sizeof m, cp.msg_total);
        tk_snprintf(title, sizeof title, "%s of %s songs", n, m);
        scan_ring(canvas, SCAN_FIG_CY - dy, cp.msg_pct, accent);
    } else if (state == SCAN_RUNNING) {
        tk_snprintf(title, sizeof title, "%s song%s found", n, cp.msg_count == 1 ? "" : "s");
        tk_snprintf(m, sizeof m, "swspin_%c_%d", light ? 'l' : 'd', cp.msg_frame);
        paint_art(w, canvas, theme_art(m), 375 / 2 - SCAN_SPIN_PX / 2,
                  SCAN_FIG_CY - dy - SCAN_SPIN_PX / 2);
    } else {
        tk_snprintf(m, sizeof m, "scan_%s_%c", glyph, light ? 'l' : 'd');
        paint_art(w, canvas, theme_art(m), 375 / 2 - SCAN_FIG / 2, SCAN_Y - dy);
        int added = cp.msg_count - cp.msg_total;
        if (state == SCAN_FULL)
            tk_snprintf(title, sizeof title, "Not Enough Space"),
                tk_snprintf(line, sizeof line, "Some songs weren't added");
        else if (gone)
            tk_snprintf(title, sizeof title, "SD Card Removed"),
                tk_snprintf(line, sizeof line, "Put it back and scan again");
        else if (state == SCAN_STOPPED)
            tk_snprintf(title, sizeof title, "Scan Stopped"),
                tk_snprintf(line, sizeof line, "%s song%s found", n, cp.msg_count == 1 ? "" : "s");
        else if (*(volatile char *)SCAN_WHOLE == 1 && cp.msg_total > 0 && added > 0)
            scan_num(m, sizeof m, added), tk_snprintf(title, sizeof title, "Library Updated"),
                tk_snprintf(line, sizeof line, "%s song%s, %s new", n, cp.msg_count == 1 ? "" : "s",
                            m);
        else
            tk_snprintf(title, sizeof title, "Library Updated"),
                tk_snprintf(line, sizeof line, "%s song%s", n, cp.msg_count == 1 ? "" : "s");
    }
    ui_text(canvas, title, 1, TYPE(20), tok(TOK_PRIMARY), 1, 375 / 2, SCAN_TITLE_CY - dy, 375 - 48,
            1);
    char name[128];
    int named = state == SCAN_RUNNING && cp.msg_folder &&
                !widget_get_text_utf8(cp.msg_folder, name, sizeof name) && *name;
    if (cp.msg_folder) widget_set_visible(cp.msg_folder, named, 0);
    if (state == SCAN_RUNNING && !named) /* stock names a folder on a whole scan alone */
        tk_snprintf(line, sizeof line, "Scanning the SD card");
    if (named) { /* the glyph before the name, both centred */
        int nw = ui_measure(canvas, name, "inter", TYPE(15));
        if (nw > SCAN_NAME_W) nw = SCAN_NAME_W;
        int x = (375 - nw - SCAN_FOLDER_PX - SCAN_FOLDER_GAP) / 2;
        tk_snprintf(m, sizeof m, "scan_folder_%c", light ? 'l' : 'd');
        paint_art(w, canvas, theme_art(m), x, SCAN_LINE_CY - dy - SCAN_FOLDER_PX / 2);
        widget_move_resize(cp.msg_folder, x + SCAN_FOLDER_PX + SCAN_FOLDER_GAP - 2,
                           SCAN_LINE_CY - SCAN_LINE_H / 2 - dy, nw + 4, SCAN_LINE_H);
    } else
        ui_text(canvas, line, 0, TYPE(15), tok(TOK_SECONDARY), 1, 375 / 2, SCAN_LINE_CY - dy,
                375 - 48, 1);
    int bw = ui_button(canvas, 375 / 2, SCAN_BTN_CY - dy, state == SCAN_RUNNING ? "Stop" : "Done",
                       state != SCAN_RUNNING, 1, 0);
    if (cp.msg_btn && I(cp.msg_btn, W_W) != bw)
        widget_move_resize(cp.msg_btn, (375 - bw) / 2, SCAN_BTN_CY - CTA_H / 2 - dy, bw, CTA_H);
    st.raw_ink = raw;
    stock_canvas_set_fill_color(canvas, fill);
    stock_canvas_set_text_color(canvas, text);
}
