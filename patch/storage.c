/* Settings > Storage (docs/internals.md#storage, docs/oled-design.md#progress-and-sliders): how the
 * card is shared between the user's own files and what a card app downloads and caches for them,
 * and the app's Downloads with their limit.
 *
 * A card app that keeps downloads declares storage=1 in its q2app (q2-subsonic) and writes
 * /mnt/mmc/.<id>/storage.tsv whole (temp and rename), UTF-8, tab-separated with the card-app
 * channel's escapes:
 *   #Q2STORE/1	limit=<MB, 0 for none>	downloads=<bytes>	cache=<bytes>
 *   i	<pick|auto>	<key>	<bytes>	<op>	<arg>	<name>
 * downloads: what it downloaded to the card for the user (q2-subsonic's Subsonic/); cache: its own
 * folder, the Stream Cache with it. One i line per downloaded item: picked by the user (pick) or
 * kept by the app on its own (auto), its key, its size, and the op and argument of the app's page
 * for it (an empty op: none). The firmware asks for a change by appending a line to
 * /mnt/mmc/.<id>/storage.out, "limit\t<MB>" or "remove\t<key>", and starts the app
 * (addon_wake) to take it; the app writes storage.tsv again. The page opening appends "look",
 * without starting the app: one that runs counts its folder again for the cache's size. The
 * firmware reads only statfs for the card: Your Music is what is used less the app's downloads and
 * cache, and the card is never walked. Without such an app, Downloads and Stream Cache read 0 and open nothing. */
#include "navigation.h"

#define ST_ITEMS 64
#define ST_FILE_MAX (32 << 10)
#define ST_HEAD 42 /* the used line and the bar, between the status row and the rows */
#define ST_FOOT 44 /* two footer lines under the rows */
/* In the header view, from the rows' original top (y 58 on screen, the window's top): the used
 * line's centre, y 65, 3 px under the design's 62 so its ascent is inside the window, which clips
 * it; and the bar's top, y 76. */
#define ST_TEXT_DY 7
#define ST_BAR_DY 18
#define ST_BAR_H 10
#define ST_DOT 10      /* a row's legend dot, at the text column */
#define ST_DOT_TEXT 44 /* the label after it */
#define ST_ICON 18     /* a download's mark (tools/art.py STORE_PX), centred at x 33 */
#define ST_ICON_X 24
#define ST_ICON_TEXT 52
#define ST_FOOT_PX TYPE(13) /* 13 px, AWTK's size (oled-design.md Type) */
#define ST_HEAD_PX TYPE(15) /* 15 px */
#define ST_POLL_MS 1000
#define ST_ROW "_st_row"  /* a row's index in its list, for storage_paint */
#define ST_TRACK RGB_RAISED_2 /* Free's segment and dot: the bar's track */
#define ST_CACHE 0x6e6e73 /* Stream Cache's */

typedef struct {
    const char *auto_, *key, *op, *arg, *name;
    unsigned mb;
} st_item;

static struct {
    void *page, *view, *head, *foot; /* Settings > Storage */
    void *dl, *dl_view, *dl_foot;    /* Storage > Downloads */
    char id[16];                     /* the storage app's, "" for none */
    char *raw;                       /* storage.tsv as read; the items' strings are in it */
    unsigned hash, timer;
    unsigned total, free, down, cache, limit; /* MB */
    int pending;                              /* a limit chosen here, not yet in storage.tsv */
    int n;
    st_item item[ST_ITEMS];
} so __attribute__((section(".scratch")));

/* Limit's choices, MB (10^6 bytes); 0 is none. */
static const unsigned limits[] = { 0, 4000, 8000, 16000, 32000, 64000 };
static const char *const limit_names[] = { "No Limit", "4 GB", "8 GB", "16 GB", "32 GB", "64 GB" };
#define LIMITS (int)(sizeof limits / sizeof *limits)

static unsigned long long dec(const char *s) {
    unsigned long long v = 0;
    while (s && *s >= '0' && *s <= '9') v = v * 10 + (unsigned)(*s++ - '0');
    return v;
}
/* MiB as MB (10^6 bytes, as the apps and the card's label count): times 1.049, within 0.05%, so
 * that no 64-bit division is needed (the payload has none). */
static unsigned mb_mib(unsigned mib) { return (mib * 1049 + 500) / 1000; }
/* A decimal byte count as MB. */
static unsigned mb_of(const char *s) { return mb_mib((unsigned)((dec(s) + (1u << 19)) >> 20)); }
/* "512 MB" under 1000 MB, else GB to a tenth: "24.1 GB". */
static void size_text(char *out, unsigned n, unsigned mb) {
    unsigned t = (mb + 50) / 100;
    if (mb < 1000)
        tk_snprintf(out, n, "%u MB", mb);
    else
        tk_snprintf(out, n, "%u.%u GB", t / 10, t % 10);
}
static const char *limit_name(unsigned mb) {
    for (int i = 0; i < LIMITS; ++i)
        if (limits[i] == mb) return limit_names[i];
    return 0;
}

static int st_found(const char *id, const char *raw, void *unused) {
    if (strlen(id) >= sizeof so.id || number(raw, "storage") != 1) return 0;
    tk_snprintf(so.id, sizeof so.id, "%s", id);
    return 1;
}
/* The first app on the card whose q2app says storage=1, into so.id. */
static void st_app(void) {
    so.id[0] = 0;
    if (card_mounted()) addon_q2apps(st_found, 0);
}

/* The card's size and free space (statfs, MIPS o32: f_bsize word 1, f_blocks 3, f_bavail 7), and
 * the app's file; 1 when the file changed since the last read. */
static int st_read(void) {
    unsigned fs[32] = { 0 }, size = 0;
    so.total = so.free = 0;
    if (card_mounted() && !statfs("/mnt/mmc", fs))
        so.total = mb_mib((unsigned)((unsigned long long)fs[3] * fs[1] >> 20)),
        so.free = mb_mib((unsigned)((unsigned long long)fs[7] * fs[1] >> 20));
    char path[64];
    tk_snprintf(path, sizeof path, "/mnt/mmc/.%s/storage.tsv", so.id);
    char *raw = so.id[0] ? read_all(path, ST_FILE_MAX, &size) : 0;
    unsigned h = raw ? hash_bytes(FNV_SEED, (const unsigned char *)raw, size) : 0;
    if (raw && h == so.hash) return free(raw), 0;
    free(so.raw);
    so.raw = raw, so.hash = h, so.n = 0, so.down = so.cache = 0;
    if (!so.pending) so.limit = 0;
    so.pending = 0;
    if (!raw || !tk_str_start_with(raw, "#Q2STORE/1\t")) return 1;
    for (char *line = raw, *next; line && *line; line = next) {
        next = strstr(line, "\n");
        if (next) *next++ = 0;
        char *f[7];
        int k = tsv_split(line, f, 7);
        if (line == raw) {
            for (int j = 1; j < k; ++j)
                if (tk_str_start_with(f[j], "limit="))
                    so.limit = (unsigned)dec(f[j] + 6);
                else if (tk_str_start_with(f[j], "downloads="))
                    so.down = mb_of(f[j] + 10);
                else if (tk_str_start_with(f[j], "cache="))
                    so.cache = mb_of(f[j] + 6);
        } else if (k == 7 && !strcmp(f[0], "i") && so.n < ST_ITEMS) {
            st_item *it = &so.item[so.n++];
            it->auto_ = !strcmp(f[1], "auto") ? f[1] : 0;
            it->key = f[2], it->mb = mb_of(f[3]), it->op = f[4], it->arg = f[5], it->name = f[6];
        }
    }
    return 1;
}

/* The used MB that are neither the app's downloads nor its cache. */
static unsigned st_yours(void) {
    unsigned used = so.total - so.free, app = so.down + so.cache;
    return used > app ? used - app : 0;
}

/* A line for the app, "\n<line>\n" appended to storage.out (the LF before it ends a line a cut
 * write left); 1 when the card took it. */
static int st_note(const char *line) {
    char path[64];
    tk_snprintf(path, sizeof path, "/mnt/mmc/.%s/storage.out", so.id);
    void *f = so.id[0] ? fopen(path, "ab") : 0;
    unsigned n = strlen(line);
    int ok =
        f && fwrite("\n", 1, 1, f) == 1 && fwrite(line, n, 1, f) == 1 && fwrite("\n", 1, 1, f) == 1;
    if (f && fclose(f)) ok = 0;
    return ok;
}
/* A request to the app, the app started to take it. 1 when the card took it. */
static int st_ask(const char *line) {
    int ok = st_note(line);
    if (ok) addon_wake(so.id);
    return ok;
}

/* ---- the pages ---- */

static void st_fill(void);
static void dl_fill(void);
static int st_nothing(void *ctx, void *event) { return 0; } /* a row the wheel reaches, inert */
static int st_open_dl(void *ctx, void *event);
static int dl_click(void *ctx, void *event);
static int st_open_cache(void *ctx, void *event) {
    if (so.id[0]) addon_open_at(so.id, "settings", "", 0);
    return 0;
}

/* The rows' list moved down under a header view h high, as battery.c's graph; the header and a
 * footer view under the rows, both painted by storage_paint. */
static void st_frame(void *page, void *view, int head, int rows, void **top, void **foot) {
    void *list = P(view, W_PARENT);
    int rh = page_h(48), y = I(list, W_Y), h = rows * rh;
    widget_move_resize(list, 0, y + head, 375, h);
    widget_resize(view, 375, h);
    if (top) *top = widget_factory_create_widget(widget_factory(), "view", page, 0, y, 375, head);
    *foot = widget_factory_create_widget(widget_factory(), "view", page, 0, y + head + I(list, W_H),
                                         375, ST_FOOT);
}

/* A row's label moved to x, ending ST_VALUE_W before the right column's chevron, an ellipsis
 * where it is cut and scrolling only on the selection. */
#define ST_VALUE_W 75 /* "24.1 GB" at 20 px */
static void *st_row(void *item, int i) {
    widget_set_prop_int(item, ST_ROW, i);
    return item;
}
static void st_label(void *item, int x) {
    void *l = widget_get_child(item, 0);
    if (!l) return;
    widget_move_resize(l, x, I(l, W_Y),
                       375 - geom_val(V_RIGHT_X) - VALUE_CHEVRON_GAP - ST_VALUE_W - x, I(l, W_H));
    hscroll_label_set_only_focus(l, 1);
    hscroll_label_set_ellipses(l, 1);
}

/* Update Library (docs/ipod.md#settings, from the Library's page on 2026-11-19): stock's library
 * scan, as Scan Music Now opens it (cf_empty.c), its second line when the last whole scan ended
 * (storage_scanned, from coverflow.c library_scan): "Last updated today, 18:11", "yesterday, ..."
 * or the date; none before a scan this firmware saw. */
#define ST_SCANNED "/mnt/data/q2-library-updated" /* the wall time, decimal */
static int st_update(void *ctx, void *event) {
    int scan[4] = { 0 };
    navigator_to_with_context("dialog/updatemusic_dialog", scan);
    return 0;
}
/* library_scan's, on stock's scan thread, as a whole scan ends unstopped: its time, written whole. */
void storage_scanned(void) {
    char b[24];
    int n = tk_snprintf(b, sizeof b, "%u", (unsigned)wall_time()); /* 32 bits to 2106 */
    if (!blob_io(ST_SCANNED, ST_SCANNED ".tmp", b, (unsigned)n, 1)) unlink(ST_SCANNED ".tmp");
}
/* The saved time, 0 for none. */
static long long st_scanned_at(void) {
    char b[24] = "";
    void *f = fopen(ST_SCANNED, "rb");
    if (f) b[fread(b, 1, sizeof b - 1, f)] = 0, fclose(f);
    long long when = 0;
    for (const char *s = b; *s >= '0' && *s <= '9'; ++s) when = when * 10 + (*s - '0');
    return when;
}
static long long st_shown_scan __attribute__((section(".scratch"))); /* the time the page shows */
/* "Last updated ..." into out from the saved time; 0 with none (or the clock unset then). */
const char *st_updated(char *out, unsigned n) {
    long long when = st_shown_scan = st_scanned_at();
    char day[24];
    return when && when_said(day, sizeof day, (unsigned)when)
               ? (tk_snprintf(out, n, "Last updated %s", day), out)
               : (const char *)0;
}

#define ST_ROWS 5
static const char *const st_names[] = { "Your Music", "Downloads", "Stream Cache", "Free",
                                        "Update Library" };
static void st_fill(void) {
    static int (*const clicks[])(void *, void *) = { st_nothing, st_open_dl, st_open_cache,
                                                     st_nothing, st_update };
    int sel = so.view ? widget_get_prop_int(so.view, "_ringnav_index", 0) : 0;
    char when[48];
    void *view = so.view = page_list(so.page, so.page, 0, "Storage", ST_ROWS, 48);
    for (int i = 0; i < 4; ++i)
        st_label(st_row(page_row_detail(view, i, st_names[i], 0, clicks[i]), i), ST_DOT_TEXT);
    st_row(page_row_detail(view, 4, st_names[4], st_updated(when, sizeof when), clicks[4]), 4);
    st_frame(so.page, view, ST_HEAD, ST_ROWS, &so.head, &so.foot);
    ringnav_select(view, sel, ST_ROWS);
}

static void dl_fill(void) {
    int sel = so.dl_view ? widget_get_prop_int(so.dl_view, "_ringnav_index", 0) : 0;
    void *view = so.dl_view = page_list(so.dl, so.dl, 0, "Downloads", 1 + so.n, 48);
    st_row(page_row_detail(view, 0, "Limit", 0, dl_click), 0);
    for (int i = 0; i < so.n; ++i)
        st_label(st_row(page_row_detail(view, 1 + i, so.item[i].name, 0, dl_click), 1 + i),
                 ST_ICON_TEXT);
    st_frame(so.dl, view, 0, 1 + so.n, 0, &so.dl_foot);
    ringnav_select(view, sel <= so.n ? sel : so.n, 1 + so.n);
}

/* While either page is open: the app's file read again, the pages rebuilt when it changed. */
static int st_poll(const void *unused) {
    if (!so.page && !so.dl) return so.timer = 0, 0;
    if (st_read()) {
        if (so.dl) dl_fill();
        if (so.page) st_fill();
    } else if (so.page && st_scanned_at() != st_shown_scan)
        st_fill(); /* a scan ended since: Update Library's line */
    return 8; /* RET_REPEAT */
}

static void st_limit_picked(int i) {
    char line[48];
    if (i < 0 || i >= LIMITS) return;
    tk_snprintf(line, sizeof line, "limit\t%u", limits[i]);
    if (!st_ask(line)) {
        toast("Couldn't save the limit");
        return;
    }
    so.limit = limits[i], so.pending = 1;
    if (so.dl) dl_fill();
}

/* A Downloads row: Limit opens its picker; an item, its page in the app. */
static int dl_click(void *ctx, void *event) {
    int i = (int)(long)ctx;
    if (!i) {
        int at = 0;
        while (at < LIMITS && limits[at] != so.limit) ++at;
        modal_pick((void *)0, limit_names, LIMITS, at < LIMITS ? at : 0, st_limit_picked);
    } else if (i <= so.n && *so.item[i - 1].op)
        addon_open_at(so.id, so.item[i - 1].op, so.item[i - 1].arg, so.item[i - 1].name);
    return 0;
}

static int st_closed(void *ctx, void *event) {
    if (ctx == so.dl)
        so.dl = so.dl_view = so.dl_foot = 0;
    else
        so.page = so.view = so.head = so.foot = 0;
    if (!so.page && !so.dl) stop_timer(&so.timer);
    return 0;
}

static int st_open_dl(void *ctx, void *event) {
    if (so.dl || !so.id[0] || !(so.dl = page_open("downloads_page", st_closed, page_keyup)))
        return 0;
    so.dl_view = 0;
    dl_fill();
    return 0;
}

/* System Settings' Storage row (navigation.c ringnav_systemset). */
int storage_open(void *ctx, void *event) {
    if (so.page || !(so.page = page_open("storage_page", st_closed, page_keyup))) return 0;
    st_app();
    /* the app counts its folder again for the page if it runs, or when it next starts; it isn't
     * started for it (q2-subsonic's storage.go stOwn) */
    st_note("look");
    so.hash = 0;
    st_read();
    so.view = 0;
    st_fill();
    if (!so.timer) so.timer = timer_add(st_poll, 0, ST_POLL_MS);
    return 0;
}

/* ringnav_keylong: Play/Pause held on a Downloads item asks the app to remove it; 1 when it was
 * one. Its songs stay until the app's next Sync, which deletes them. */
int storage_hold(void *top) {
    int i = so.dl && top == so.dl ? widget_get_prop_int(so.dl_view, "_ringnav_index", -1) : -1;
    char line[160];
    if (i < 1 || i > so.n) return 0;
    tk_snprintf(line, sizeof line, "remove\t%s", so.item[i - 1].key);
    if (!st_ask(line)) return toast("Couldn't remove it"), 1;
    memmove(&so.item[i - 1], &so.item[i], (unsigned)(so.n - i) * sizeof *so.item);
    --so.n;
    dl_fill();
    toast("Removed from Sync");
    return 1;
}

/* ---- painting (ringnav_paint), OLED's ---- */

/* s, UTF-8, at x, its box w wide centred on y, aligned (2 left, 3 right), in the role's face at
 * px, in rgb (0xRRGGBB): ui_kit.c's ui_line. */
static void st_text(void *canvas, const char *s, int x, int w, int y, int align, int role, int px,
                    unsigned rgb) {
    static const char *const faces[] = FONT_NAMES;
    ui_line(canvas, s, x, w, y, align, faces[theme()->font[role]], px, rgb);
}
static void st_dot(void *canvas, int x, int y, unsigned rgb) {
    rect_t r = { x, y - ST_DOT / 2, ST_DOT, ST_DOT };
    fill_box(canvas, &r, RGBA(rgb), ST_DOT / 2);
}

/* The bar: the track, then Your Music, Downloads and Stream Cache from the left, square where they
 * meet, 1 px of track between them. */
static void st_bar(void *canvas, int x0, int x1, int y) {
    rect_t r = { x0, y, x1 - x0, ST_BAR_H };
    unsigned mb[3] = { st_yours(), so.down, so.cache },
             rgb[3] = { 0xffffff, scheme()->tones[0] & 0xffffffu, ST_CACHE };
    fill_box(canvas, &r, RGBA(ST_TRACK), ST_BAR_H / 2);
    int at = x0;
    for (int k = 0; k < 3 && so.total; ++k) {
        int w = (int)((unsigned long long)mb[k] * (unsigned)(x1 - x0) / so.total);
        if (w < 2) continue;
        if (at + w > x1) w = x1 - at;
        if (at == x0) { /* round at the bar's left end */
            rect_t end = { at, y, w - 1, ST_BAR_H };
            fill_box(canvas, &end, RGBA(rgb[k]), ST_BAR_H / 2);
            canvas_set_fill_color(canvas, RGBA(rgb[k]));
            canvas_fill_rect(canvas, at + ST_BAR_H / 2, y, w - 1 - ST_BAR_H / 2, ST_BAR_H);
        } else {
            canvas_set_fill_color(canvas, RGBA(rgb[k]));
            canvas_fill_rect(canvas, at, y, w - 1, ST_BAR_H);
        }
        at += w;
    }
}

/* A row's value on the right column, beside its chevron when it opens a page. */
static void st_value(void *w, void *canvas, const char *value, int into, int sel) {
    int right = 375 - geom_val(V_RIGHT_X) - page_ox(canvas, w), y = I(w, W_H) / 2;
    unsigned ink = theme()->set_value_ink[0] ? theme()->set_value_ink[0] : RGB_SECONDARY;
    if (into) chevron_icon(w, canvas, right, y, sel), right -= VALUE_CHEVRON_GAP;
    st_text(canvas, value, right - 160, 160, y, 3, ROLE_TITLE, theme()->font_px[ROLE_TITLE],
            sel ? 0xffffff : ink);
}

static void st_foot(void *canvas, const char *a, const char *b) {
    int x = geom_val(V_TEXT_X), w = 375 - x - geom_val(V_RIGHT_X);
    st_text(canvas, a, x, w, 10, 2, ROLE_META, ST_FOOT_PX, scheme()->muted);
    if (b) st_text(canvas, b, x, w, 26, 2, ROLE_META, ST_FOOT_PX, scheme()->muted);
}

void storage_paint(void *w, void *canvas) {
    if (!w || (!so.page && !so.dl) || !P(canvas, CANVAS_LCD)) return;
    void *lcd = P(canvas, CANVAS_LCD), *parent = P(w, W_PARENT);
    unsigned fill = (unsigned)I(lcd, LCD_FILL_COLOR), text = (unsigned)I(lcd, LCD_TEXT_COLOR),
             raw = st.raw_ink;
    char a[48], b[24];
    int x0 = geom_val(V_TEXT_X), x1 = 375 - geom_val(V_RIGHT_X);
    if (w == so.head) {
        size_text(a, sizeof a, so.total - so.free);
        tk_snprintf(a + strlen(a), sizeof a - strlen(a), " of %u GB used", (so.total + 500) / 1000);
        st_text(canvas, a, x0, x1 - x0, ST_TEXT_DY, 2, ROLE_META, ST_HEAD_PX, RGB_SECONDARY);
        st_bar(canvas, x0, x1, ST_BAR_DY);
    } else if (w == so.foot) {
        st_foot(canvas, "Your Music is never changed by Sync.",
                "Downloads stay under the limit you set.");
    } else if (w == so.dl_foot) {
        if (so.n)
            st_foot(canvas, "Hold Play/Pause on an item to remove it.", 0);
        else
            st_foot(canvas, "Nothing downloaded yet.", 0);
    } else if (parent && (parent == so.view || parent == so.dl_view)) {
        int i = widget_get_prop_int(w, ST_ROW, 0), sel = ringnav_row_selected(w);
        st.raw_ink = sel;
        if (parent == so.view && i < 4) {
            unsigned mb[4] = { st_yours(), so.down, so.cache, so.free },
                     rgb[4] = { 0xffffff, scheme()->tones[0] & 0xffffffu, ST_CACHE, ST_TRACK };
            if (i == 1) st.raw_ink = 1; /* the accent's dot: final in both halves */
            st_dot(canvas, x0 - page_ox(canvas, w), I(w, W_H) / 2, rgb[i]);
            st.raw_ink = sel;
            size_text(b, sizeof b, mb[i]);
            st_value(w, canvas, b, so.id[0] && (i == 1 || i == 2), sel);
        } else if (parent == so.dl_view && i == 0) {
            st_value(w, canvas, limit_name(so.limit) ? limit_name(so.limit) : "No Limit", 1, sel);
        } else if (parent == so.dl_view && i <= so.n) {
            const st_item *it = &so.item[i - 1];
            const char *ink = sel ? "w" : it->auto_ ? (ipod_light() ? "l" : "d") : "a";
            tk_snprintf(a, sizeof a, "store_%s_%s", it->auto_ ? "auto" : "pick", ink);
            paint_art(w, canvas, theme_art(a), ST_ICON_X - page_ox(canvas, w),
                      (I(w, W_H) - ST_ICON) / 2);
            size_text(b, sizeof b, it->mb);
            st_value(w, canvas, b, *it->op != 0, sel);
        }
    }
    st.raw_ink = raw;
    stock_canvas_set_fill_color(canvas, fill);
    stock_canvas_set_text_color(canvas, text);
}
