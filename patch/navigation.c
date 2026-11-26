/* Logical menu selection is independent of native touch focus. Stock code owns gestures. */
#include "navigation.h"
#include "card_store.h"
#include "playlist_art.h"
extern int stock_sorted_query_trampoline(const char *, const char *, void *, int);
extern int stock_seek_time_trampoline(int);
extern int stock_dbquery_trampoline(const char *, const char *, void *, int),
    stock_content_query_trampoline(const char *, const char *, void *, void *, int);
extern int stock_quicksettings_trampoline(void *, void *);
extern int stock_timezone_trampoline(void);
extern void stock_timezone_row_trampoline(void *, int);

static const char *const tz_names[] = TIMEZONE_NAMES;
#define TZ_COUNT (sizeof tz_names / sizeof *tz_names)
#define TZ_CHECK_GAP 24 /* the picker's 12 px check and its gap, at the right column */

static void clock_zone(void);
int ringnav_timezone(void) {
    unsigned n = g_time_zone;
    int result = n < TZ_COUNT ? toolsSetTimeZone(tz_names[n]) : stock_timezone_trampoline();
    tzset(); /* localtime_r does not reload TZ itself */
    clock_zone();
    return result;
}

/* Past 2038, 28 years back for stock's 32-bit localtime: the same calendar until 2100. */
long clock_back(long long wall) {
    unsigned back = (unsigned)wall; /* 32-bit: the payload has no 64-bit division */
    while (back > 0x7fffffff) back -= 883612800;
    return (long)back;
}

/* Each zone's offset from UTC now, in seconds, daylight saving included: localtime's, with each
 * zone's TZ set in turn; then the saved zone is set again. rows gets the saved zone first, then the
 * rest west to east by it, ties in TIMEZONE_ORDER (by city). ponytail: another thread's localtime
 * in that moment may read one of these zones. */
static long tz_offset[TZ_COUNT];
static void tz_now(unsigned char *rows) {
    static const unsigned char order[] = TIMEZONE_ORDER;
    long now = clock_back(wall_time());
    for (unsigned i = 0; i < TZ_COUNT; ++i) {
        toolsSetTimeZone(tz_names[i]);
        tzset();
        int buf[11];
        const int *tm = localtime_r(&now, buf); /* struct tm: tm_gmtoff @36 */
        tz_offset[i] = tm ? tm[9] : 0;
    }
    ringnav_timezone();
    for (unsigned i = 0; i < TZ_COUNT; ++i) {
        unsigned j = i;
        for (; j && tz_offset[rows[j - 1]] > tz_offset[order[i]]; --j) rows[j] = rows[j - 1];
        rows[j] = order[i];
    }
    unsigned saved = g_time_zone, i = 0; /* pinned at the top, with its check */
    while (i < TZ_COUNT && rows[i] != saved) ++i;
    for (; i < TZ_COUNT && i; --i) rows[i] = rows[i - 1];
    if (saved < TZ_COUNT) rows[0] = (unsigned char)saved;
}

/* Row i > 0 shows zone rows[i - 1] as a key/value row, "New York" and "UTC-4", the offset it has
 * now, so a zone on daylight saving reads as its clocks do (docs/oled-design.md#screens); stock
 * names, ticks and clicks by the index it is given. The checked row's value stops short of its
 * check. Focus-only, a label too long for the row keeps its ellipsis instead of scrolling once as
 * the page opens. */
void ringnav_timezone_row(void *item, int i) {
    static const char *const cities[] = TIMEZONE_CITIES;
    static unsigned char rows[TZ_COUNT];
    if (i == 1) tz_now(rows); /* stock builds rows 0, 1, 2, ... */
    int zone = i > 0 && i <= (int)TZ_COUNT ? rows[i - 1] : -1;
    stock_timezone_row_trampoline(item, zone < 0 ? i : zone + 1);
    /* the last: Automatic Time Zone under row 0's switch, on the page's own list */
    void *view = i == (int)TZ_COUNT ? P(item, W_PARENT) : (void *)0, *win = window_of(view);
    if (win && !tk_strcmp(widget_get_prop_str(win, "name", ""), "autotime_page"))
        pod_row_at(view, POD_AUTOZONE, "system_time", 1);
    void *row = widget_get_child(item, widget_count_children(item) - 1); /* stock's button */
    for (unsigned c = 0; row && c < widget_count_children(row); ++c) {
        void *label = widget_get_child(row, c);
        if (tk_strcmp(widget_get_type(label), "hscroll_label")) continue;
        hscroll_label_set_only_focus(label, 1);
        if (zone < 0) continue;
        long off = tz_offset[zone];
        int m = (int)((off < 0 ? -off : off) / 60);
        char s[16];
        if (m % 60)
            tk_snprintf(s, sizeof s, "UTC%s%d:%02d", off < 0 ? "-" : "+", m / 60, m % 60);
        else
            tk_snprintf(s, sizeof s, "UTC%s%d", off < 0 ? "-" : "+", m / 60);
        list_value(label, cities[zone], s);
        void *v = zone == g_time_zone ? widget_lookup(row, "_setting_value", 0) : (void *)0;
        if (v) widget_move_resize(v, I(v, W_X) - TZ_CHECK_GAP, I(v, W_Y), I(v, W_W), I(v, W_H));
        break; /* the value is a label too */
    }
}
/* A load's songtable reads (library_query): 0 stock's own; 1 music (LIBRARY_MUSIC): spoken
 * tracks and the Library's scope left out, the server's songs beside the card's; 2 (LIBRARY_ALL):
 * every card song and a signed-in server's, as the Ratings lists read them. */
static int library_music __attribute__((section(".scratch")));
static int library_first __attribute__((section(".scratch")));

/* Failed exec in a forked child must not flush demo's copied stdio or run its exit handlers. */
__asm__(".globl child_exit\nchild_exit:\nli $v0, 4001\nsyscall\n");
/* Out of memory, the kernel kills the fattest process, which was demo (#169): every child demo
 * forks, whose one job can need tens of MB, makes itself the first to go, between fork and exec. */
int open(const char *, int, ...);
void child_oom_first(void) {
    int fd = open("/proc/self/oom_score_adj", 1); /* O_WRONLY */
    if (fd >= 0) write(fd, "1000", 4), close(fd);
}

/* Stock's album-artist queries (Artists, an artist's albums and songs, search) take a song without
 * an album artist by its artist, so an untagged album stays under its artist, not Unknown.
 * Coverflow's grouping (ifnull(albumsonger,'')) keeps the tag. Then restrict the Library's category
 * queries, including Unknown and split albums, before grouping. Coverflow and folder queries keep
 * their own scope. */
#define ALBUM_ARTIST_OR_ARTIST "ifnull(nullif(albumsonger,''),songer)"
/* toolsQueryDbTable, or (content) toolsQueryContentDbTable, which an artist's albums tab
 * (getAlbumByAlbumArtist) runs with its row's arg and the name sort on the stack. */
static int library_query(int content, const char *db, const char *sql, void *row, void *arg,
                         int sort) {
    char aa[2048], filtered[2048];
    const char *s = sql ? strstr(sql, "albumsonger") : 0, *p = sql;
    if (s && !strstr(sql, "ifnull(albumsonger")) {
        unsigned n = 0;
        for (; s && n < sizeof aa; p = s + 11, s = strstr(p, "albumsonger"))
            n += (unsigned)tk_snprintf(aa + n, sizeof aa - n, "%.*s" ALBUM_ARTIST_OR_ARTIST,
                                       (int)(s - p), p);
        if (n >= sizeof aa ||
            n + (unsigned)tk_snprintf(aa + n, sizeof aa - n, "%s", p) >= sizeof aa)
            return -1;
        sql = aa;
    }
    const char *server = library_music ? catalogue_rows() : "";
    const char *from = sql && (library_music == LIBRARY_MUSIC || (library_music && *server))
                           ? strstr(sql, " from songtable")
                           : 0;
    if (from) { /* library_first: the Unknown row's check, which one song answers */
        /* a signed-in server's songs beside them (catalogue.c) */
        int n = tk_snprintf(filtered, sizeof filtered,
                            "%.*s from (select * from songtable where %s%s)%s%s", (int)(from - sql),
                            sql, library_music == LIBRARY_MUSIC ? MUSIC_WHERE : "1", server,
                            from + 15, library_first ? " limit 1" : "");
        if (n < 0 || (unsigned)n >= sizeof filtered) return -1;
        sql = filtered;
    }
    char *search = from ? 0 : catalogue_search(sql); /* Search finds server songs too */
    if (search) sql = search;
    if (sql && strstr(sql, "select * from playlist where id > 100 and cd_num=") == sql &&
        !strstr(sql, "order by")) {
        int n = tk_snprintf(filtered, sizeof filtered, "%s order by id asc", sql);
        if (n < 0 || (unsigned)n >= sizeof filtered) return -1;
        sql = filtered;
    }
    /* getAllPlayList's playlist headers, in Sorting's Playlists order (playlist_sort.c): by name
     * either way, not creation (stock Rename sets name too), and Recently Added by date once
     * loaded */
    int headers = sql && !strcmp(sql, "select * from playlist where id <= 100");
    if (headers) {
        int n = tk_snprintf(filtered, sizeof filtered, "%s%s", sql, playlist_sort_sql());
        if (n < 0 || (unsigned)n >= sizeof filtered) return -1;
        sql = filtered;
        row = playlist_sort_row(row);
    }
    /* Time added (ascending) would walk songtable_time_create backwards and reverse one second's
     * songs (a CUE image's tracks): id keeps stock's order. aa is free: this SQL has no
     * albumsonger. */
    const char *asc = sql ? strstr(sql, " ORDER BY time_create asc;") : 0;
    if (asc) {
        int n =
            tk_snprintf(aa, sizeof aa, "%.*s ORDER BY time_create asc, id", (int)(asc - sql), sql);
        if (n < 0 || (unsigned)n >= sizeof aa) return -1;
        sql = aa;
    }
    /* no memory to expand it: the token alone is SQL's 1, the whole library (library_scope.c) */
    char *expanded = sql && strstr(sql, LIBRARY_SCOPE_TOKEN) ? library_scope_query(sql) : 0;
    const char *query = expanded ? expanded : sql;
    /* A sorted load on the UI thread: the records unsorted (toolsQueryDbTable's sort 0, the same
     * exec and row callback; toolsQueryDbTableAndSort sorts in every mode), then name_order.c puts
     * them in stock's order, kept or sorted. Another thread's load sorts in stock. */
    int order = content == 2 ? 0x100 | (int)arg : content ? 0 : (int)arg; /* 0: no sort */
    int result;
    if (order && pthread_self() == ui_thread) {
        result = stock_dbquery_trampoline(db, query, row, 0);
        if (result >= 0) name_order(query, order, P(tools_pdeq_directory, 0));
    } else
        result = content == 2 ? stock_sorted_query_trampoline(db, query, row, (int)arg)
                 : content    ? stock_content_query_trampoline(db, query, row, arg, sort)
                              : stock_dbquery_trampoline(db, query, row, (int)arg);
    if (headers) playlist_sort_order();
    if (expanded) free(expanded);
    free(search);
    return result;
}
int ringnav_library_query(const char *db, const char *sql, void *row, int sort) {
    return library_query(0, db, sql, row, (void *)sort, 0);
}
int ringnav_sorted_query(const char *db, const char *sql, void *row, int sort) {
    return library_query(2, db, sql, row, (void *)sort, 0);
}
int ringnav_content_query(const char *db, const char *sql, void *row, void *arg, int sort) {
    return library_query(1, db, sql, row, arg, sort);
}
extern void *coverflow_tracks(void *page);
extern const char *coverflow_bar_title(void);
extern int coverflow_jump(void *w, int from, int dir, unsigned *letter), coverflow_bounded(void *w),
    coverflow_slide(void *w);
/* a slide_menu's indexes and the children standing for them (Cover Flow's pool, coverflow.c) */
extern int slide_count(void *w), slide_child_index(void *w, int j);
extern void *slide_child(void *w, int i);
extern void coverflow_lost_touch(void), coverflow_wheel_land(void);
extern int coverflow_wheel_live(void), coverflow_wheel_poll(int usable, int gated),
    coverflow_wheel_on(void *w);
extern void *coverflow_album(void *page);
extern int coverflow_goto_album(void *r), coverflow_shows_album(void *r);
extern unsigned coverflow_scope(void *page);
extern void coverflow_home_art(void *top);
extern void coverflow_home_np(void);
extern void coverflow_home_layout(void);
extern int ringnav_reboot(void);
extern void coverflow_home_clip(void *w, void *canvas, int begin);
extern void coverflow_home_bar(void *canvas, void *top, unsigned ground);
extern int coverflow_home_back(void *top), coverflow_home_owns(void *w);
extern const char *coverflow_home_title(void);
extern int coverflow_home_sub(void), coverflow_home_split(void *top);
extern void coverflow_home_row(void *surface, void *row, int id, int painting);
extern void coverflow_paint(void *w, void *canvas), video_poll(void);
extern int video_on(void), video_click(const char *path);
extern void games_paint(void *w, void *canvas);
extern void spot_poll(void), spot_paint(void *w, void *canvas), *spot_art(void);
extern void up_next(void);
extern int un_hold(void *top), un_key(void *top, unsigned key, int owned);
extern void un_keydown(unsigned key), upnext_paint(void *w, void *canvas);
extern void radio_poll(void);
extern void wheel_row(void *label), resume_row(void *label),
    mono_row(void *label), swap_row(void *label), night_row(void *label), volume_row(void *label);
extern int mono_click(void *ctx, void *event), swap_click(void *ctx, void *event);
extern void xfade_row(void *label);
extern int xfade_click(void *ctx, void *event);
extern int xfade_open(void *ctx, void *event), xfade_key(void *top, unsigned key), resume_min(void);
extern int eq_key(void *top, unsigned key), eq_tabs_hold(void *view); /* peq_ui.c */
extern const char *radio_hold(void *top);
extern int games_live(void);
extern unsigned char now_cover(void *r, const char **file);
extern unsigned now_cover_written(const char *file);
extern int scrobble_append(const char *line, unsigned n);
extern int staged_into(void *into, int (*query)(void *), void *arg);
/* Scope music detail loads too: spoken tracks can share all their tags with music. */
void *staged_music(int (*query)(void *), void *arg, int *count) {
    int saved = library_music;
    library_music = 1;
    void *out = staged(query, arg, count);
    library_music = saved;
    return out;
}

extern int album_only(void *, void *, int), coverflow_split(void *);
#define HOME_FAST_WINDOW_MS 200
#define WHEEL_RUN_MS 140
#define WHEEL_RAMP_MS 100 /* scrub and the pixel fallback: one step for this long, then */
#define WHEEL_MAX_STEP 8  /* one more per this much spin, up to this many */
#define LIST_FIRST_MS 300 /* iPod row lists: one row per step for this long, */
#define LIST_RAMP_MS 200  /* then one row more per this much spin */
#define SHORT_LIST_MAX 16
scratch_t st __attribute__((section(".scratch")));

/* Single shared view: no entry point keeps a menu live across a nested load(). */
menu_t g_menu __attribute__((section(".scratch")));

/* Audited top windows, each tagged with its content-identity class and its flags (contexts.inc).
 * The index is remembered instead of the name pointer: AWTK owns and frees the window's name
 * string. */
enum { CTX_DYNAMIC, CTX_FIXED, CTX_FOLDER, CTX_LOCAL };
/* a stock page opened again as a sub-page of its own: which, on its window (context_now) */
#define SUB_PAGE "_sub_page"
enum { DRILL = 2, BUTTONS = 4, CHOICE = 8 };
typedef struct {
    const char *name;
    unsigned char kind, flags;
} context_t;
static const context_t contexts[] = {
#include "contexts.inc"
};

static int context_id(const char *name) {
    if (!name) return -1;
    for (unsigned i = 0; i < sizeof(contexts) / sizeof(*contexts); ++i)
        if (!tk_strcmp(name, contexts[i].name)) return (int)i;
    return -1;
}

/* The view kinds load() actually navigates: a vertical scroll view, a table client, a slide
 * menu or (iPod) a BUTTONS dialog, which does not scroll. A horizontal or page-snapping scroll
 * view is not a candidate, so it cannot make a page look like it has two panes. */
static int kind(void *w) {
    const char *t = widget_get_type(w);
    if (!tk_strcmp(t, "slide_menu")) return 3;
    if (!tk_strcmp(t, "table_client")) return 2;
    if (!tk_strcmp(t, "dialog") || !tk_strcmp(t, "window")) {
        int ctx = context_id(widget_get_prop_str(w, "name", (void *)0));
        return ctx >= 0 && (contexts[ctx].flags & BUTTONS) ? 4 : 0;
    }
    return !tk_strcmp(t, "scroll_view") && B(w, VIEW_VERTICAL) && !B(w, VIEW_HORIZONTAL) &&
                   !B(w, VIEW_SNAP)
               ? 1
               : 0;
}

/* Collect up to two candidate panes. ponytail: bounded tree walk per event; a persistent cache
 * would go stale because the tree changes without notification, so it waits for a measured
 * stutter. */
static void find_surface(void *w, void **found, int *count, int *aborted, int depth, int *budget) {
    if (!w || !widget_get_visible(w) || !widget_get_prop_bool(w, "enable", 1)) return;
    if (depth == 16 || --*budget < 0) {
        *aborted = 1;
        return;
    }
    if (kind(w)) {
        if (*count < 2) found[*count] = w;
        ++*count;
        return;
    }
    if (!tk_strcmp(widget_get_type(w), "pages")) {
        int active = widget_get_prop_int(w, "active", -1);
        if (active >= 0)
            find_surface(widget_get_child(w, active), found, count, aborted, depth + 1, budget);
        return;
    }
    unsigned n = widget_count_children(w);
    for (unsigned i = 0; i < n && *count < 2; ++i)
        find_surface(widget_get_child(w, i), found, count, aborted, depth + 1, budget);
}

int clamp_step(int offset, int maximum, int delta) {
    if (maximum < 0) maximum = 0;
    offset = offset < 0 ? 0 : offset > maximum ? maximum : offset;
    if (delta > maximum - offset) return maximum;
    if (delta < -offset) return 0;
    return offset + delta;
}

/* One-row steps until a sustained run of accepted same-direction ticks has spun for `first` ms;
 * the step is then two and gains one per `more` ms, capped at WHEEL_MAX_STEP. A pause longer than
 * WHEEL_RUN_MS, a reversal or a change of menu resets the run, so stopping and reversing stay
 * precise. */
static int ramp(void *top, void *surface, unsigned scope, int ctx, int dir, unsigned now, int first,
                int more) {
    int same = st.wheel_dir == dir && st.wheel_top == top && st.wheel_surface == surface &&
               st.wheel_scope == scope && st.wheel_ctx == ctx;
    unsigned gap = now - st.last_wheel;
    if (same && st.wheel_run && gap <= WHEEL_RUN_MS)
        st.wheel_run =
            (unsigned)clamp_step(st.wheel_run, first + (WHEEL_MAX_STEP - 2) * more + 1, gap);
    else
        st.wheel_run = 1;
    st.last_wheel = now;
    st.wheel_dir = dir;
    st.wheel_top = top;
    st.wheel_surface = surface;
    st.wheel_scope = scope;
    st.wheel_ctx = ctx;
    int spin = (int)st.wheel_run - 1;
    return spin < first ? 1 : 2 + (spin - first) / more;
}

/* A tap target has an EVT_CLICK handler. V1.36 widget emitter @0x60; emitter_on_with_tag items are
 * {ctx, id, type @8, handler, tag, working @0x14, pending_remove @0x15, next @0x20}. */
static int clickable(void *w) {
    void *emitter = P(w, W_EMITTER);
    for (void *item = emitter ? P(emitter, 0) : (void *)0; item; item = P(item, EMIT_NEXT))
        if (I(item, EMIT_TYPE) == EVT_CLICK && !B(item, EMIT_PENDING_REMOVE)) return 1;
    return 0;
}

typedef struct {
    void **at;
    int n, cap, budget;
} entries_t;

/* Visible, enabled tap targets in pre-order; a target's descendants belong to it.
 * ponytail: capped walk; raise MAX_ENTRIES if a real list outgrows 512. */
static void collect(void *w, entries_t *s, int depth) {
    if (!w || !widget_get_visible(w) || !widget_get_prop_bool(w, "enable", 1)) return;
    if (depth == 16 || s->n == s->cap || --s->budget < 0) return;
    if (clickable(w)) {
        s->at[s->n++] = w;
        return;
    }
    /* A pages widget exposes only its active child; inactive tabs must not look tappable. */
    if (!tk_strcmp(widget_get_type(w), "pages")) {
        int active = widget_get_prop_int(w, "active", -1);
        if (active >= 0) collect(widget_get_child(w, active), s, depth + 1);
        return;
    }
    unsigned n = widget_count_children(w);
    for (unsigned i = 0; i < n; ++i) collect(widget_get_child(w, i), s, depth + 1);
}

/* Widget-owned properties die with the surface; never retain recycled row pointers. */
#define TOUCH "_ringnav_touch"
#define SCOPE "_ringnav_scope"

/* UI timers, shared with coverflow.c; a failed add leaves the timer 0. */
void stop_timer(unsigned *timer) {
    if (*timer) timer_remove(*timer);
    *timer = 0;
}

void rearm(unsigned *timer, int (*fn)(const void *), unsigned ms) {
    stop_timer(timer);
    *timer = timer_add(fn, (void *)0, ms);
}

/* A page's keyup (Radio, Games, card apps): Return runs its leave from the next loop pass. */
int return_key(void *event, unsigned *timer, int (*leave)(const void *)) {
    if (I(event, EVENT_KEY) != KEY_RETURN) return 0;
    rearm(timer, leave, 0);
    return 11; /* RET_STOP */
}

/* A centre press on a page telling single from double presses (Now Playing, Spotify's): 1 the
 * first, whose single runs DOUBLE_CLICK_MS on unless a second comes; 2 that second; 0 a press
 * after an overdue first, whose single runs now, and stock takes this one. Scrubbing, a press is
 * 2 at once, and one within DOUBLE_CLICK_MS after it 1 with no single, so ending a scrub with one
 * press or two never turns the screen off. */
int center_press(unsigned *timer, unsigned *at, int (*single)(const void *), int scrubbing) {
    unsigned now = (unsigned)time_now_ms();
    if (scrubbing) {
        *at = now;
        return 2;
    }
    if (!*timer) {
        if (now - *at < DOUBLE_CLICK_MS) return 1;
        *at = now;
        *timer = timer_add(single, (void *)0, DOUBLE_CLICK_MS);
        return 1;
    }
    stop_timer(timer);
    if (now - *at < DOUBLE_CLICK_MS) return 2;
    single((void *)0);
    return 0;
}

static void cancel_center(void) {
    stop_timer(&st.center_timer);
    st.center_top = st.center_surface = (void *)0;
}

/* The list's spin ramp starts over, and the next tick is the first of its run. */
static void drop_wheel(void) { st.wheel_run = 0; }

/* A wheel or centre step supersedes any run: drop the spin ramp and home slide. */
static void drop_spin(void) {
    drop_wheel();
    st.home_surface = (void *)0;
}

/* Input that is not navigation here: no pending confirmation and no run survive it. */
void drop_input(void) {
    cancel_center();
    drop_spin();
}

/* The rest of the UI, by area (tools/ci/split_sources.py): one translation unit with the above. */
#include "nav/home_carousel.c"
#include "nav/lists.c"
#include "nav/pull.c"
#include "nav/row_paint.c"
#include "nav/rows_settings.c"
#include "nav/lyrics_status_bar.c"
#include "nav/now_playing.c"
#include "nav/theme_titles_paint.c"
#include "nav/settings_display.c"
#include "nav/touch_dispatch_layout.c"
#include "nav/library_pages.c"
#include "nav/chapters_streams_folders.c"
#include "nav/hold_menu.c"
#include "nav/playback_power.c"
#include "nav/input.c"
