/* A Library album's page (docs/oled-design.md#album-page
 * and docs/internals.md#library-album-page): stock's two album pages, albuminfo_page (Library >
 * Albums, Search's albums) and playerjumpinfo_page (Now Playing's album and art taps, Look up
 * Album, Go to Album), keep their window, their list of tracks (song_rows.c draws them: number,
 * title, length), the rows' clicks, Return and the hold menu; dressed as their UI loads
 * (navigation.c ringnav_ui_load), the list moves under artist_page.c's header (the album's cover,
 * its name, its artist and "2015 · 18 songs · 1h12m") and its pair, Play and Shuffle, which
 * take the Shuffle row's place. The page opens on Play, as an artist's on Shuffle.
 * The header's lines are read from the page's songs (p_deque_showlist, which stock fills as the
 * page opens) at its first paint with the page on top, and again when their count changes: the
 * album, its album artist (else the first song's artist), the first song's year (the library's
 * songtable, or a server song's catalogue row), the songs' count and their lengths' sum
 * (durations.c), a part left out when unknown. The cover is album_cover.c's: the server library's
 * for a server album, else Cover Flow's cached cover of it, else the one Now Playing shows when
 * the album plays, else one fetched from a copy of its songs on the card (lb_art_wait). */
#include "navigation.h"

/* the page drawn now (the one on top of the dressed pages), its header and list; a dressed page
 * keeps its lit stop + 1 in LB_DRESSED while another is drawn */
#define LB_DRESSED "_lb_page"
#define LB_HEAD "_lb_head"
static struct {
    void *win, *head, *surface;
    unsigned n;       /* the songs the header's lines were read from; 0 not yet */
    unsigned art;     /* album_cover_gen then */
    unsigned wait;    /* lb_art_wait's timer while a cover is fetched */
    signed char stop; /* the lit pair stop (artist_page.c pair_key) */
} lb __attribute__((section(".scratch")));

int album_local_on(void *win) { return win && widget_get_prop_int(win, LB_DRESSED, 0) > 0; }

/* the page goes; its header with it, whose cover artist_page.c lets go */
static int lb_gone(void *win, void *event) {
    (void)event;
    if (win == lb.win) memset(&lb, 0, sizeof lb);
    return 0;
}

/* win, a dressed page, drawn from now on: the page drawn before keeps its stop. */
static void lb_take(void *win) {
    int jump = !tk_strcmp(widget_get_prop_str(win, "name", ""), "playerjumpinfo_page");
    if (win == lb.win) return;
    if (lb.win) widget_set_prop_int(lb.win, LB_DRESSED, lb.stop + 1);
    lb.win = win, lb.head = (void *)0;
    lb.surface = widget_lookup(win, jump ? "music_table_client" : "scroll_view", 1);
    lb.stop = (signed char)(widget_get_prop_int(win, LB_DRESSED, 2) - 1);
}

static int lb_year_found __attribute__((section(".scratch")));
static int lb_year_row(void *unused, int n, char **v, char **c) {
    (void)unused, (void)n, (void)c;
    lb_year_found = v[0] ? atoi(v[0]) : 0;
    return 0;
}
/* r's year, 0 when unknown: a server song's from the catalogue, a card song's from songtable (its
 * first four digits, as ALBUM_YEAR reads them). */
static int lb_year(void *r) {
    const char *path = P(r, REC_PATH);
    /* end: room after it for a quote doubled at its edge and the tail, "' limit 1" and its NUL */
    char sql[640], genre[96], *o = sql, *end = sql + sizeof sql - 11;
    int year = 0;
    if (!path) return 0;
    if (catalogue_np_info(path, genre, &year)) return year > 0 ? year : 0;
    o += tk_snprintf(sql, sizeof sql, "select substr(year,1,4) from songtable where fileurl='");
    for (const char *s = path; *s && o < end; ++s) /* a quote doubled */
        if ((*o++ = *s) == '\'') *o++ = '\'';
    if (o >= end) return 0;
    memcpy(o, "' limit 1", 10);
    lb_year_found = 0;
    toolsQueryDbTable("/mnt/data/database.db", sql, lb_year_row, 0);
    year = lb_year_found;
    return year >= 1000 && year <= 9999 ? year : 0;
}

/* While a cover of the page's album is fetched (album_cover.c): the header drawn again once it
 * lands, so lb_fill takes it. */
static int lb_art_wait(const void *unused) {
    (void)unused;
    if (!lb.win || !lb.head) return 0; /* the page went: lb_gone cleared lb.wait with the rest */
    if (album_cover_busy()) return 8;  /* RET_REPEAT */
    lb.wait = 0;
    widget_invalidate_force(lb.head, (void *)0);
    return 0;
}

/* The header's lines and cover from the page's songs, when their count is not the one read, or a
 * fetched album cover landed since. */
static void lb_fill(void) {
    void *list = P(p_deque_showlist, 0), *r;
    unsigned n = list ? deque_size(list) : 0, sec = 0;
    char sub[128], meta[96], part[32], url[520];
    const char *artist, *name;
    if (!n || (n == lb.n && lb.art == album_cover_gen()) ||
        window_manager_get_top_window(window_manager()) != lb.win)
        return;
    lb.n = n, lb.art = album_cover_gen();
    r = deque_at(list, 0);
    artist = P(r, REC_ALBUM_ARTIST);
    if (!artist || !*artist) artist = P(r, REC_ARTIST);
    tk_snprintf(sub, sizeof sub, "%s", artist ? artist : "");
    for (unsigned i = 0; i < n; ++i) {
        int s = duration_seconds(deque_at(list, i));
        sec += s > 0 ? (unsigned)s : 0;
    }
    int year = lb_year(r), min = (int)(sec + 30) / 60, k = 0;
    part[0] = 0;
    if (sec) /* q2-subsonic's span in the house time style (docs/oled-design.md#type) */
        tk_snprintf(part, sizeof part,
                    min < 60        ? " · %d min"
                    : min % 60 == 0 ? " · %dh"
                                    : " · %dh%dm",
                    min < 60 ? (min ? min : 1) : min / 60, min % 60);
    if (year) k = tk_snprintf(meta, sizeof meta, "%d · ", year);
    if (n == 1)
        tk_snprintf(meta + k, sizeof meta - (unsigned)k, "1 song%s", part);
    else
        tk_snprintf(meta + k, sizeof meta - (unsigned)k, "%u songs%s", n, part);
    name = P(r, REC_ALBUM);
    hero_text(lb.head, name && *name ? name : (const char *)0, sub, meta);
    void *songs[8];
    unsigned k_songs = n < 8 ? n : 8;
    for (unsigned i = 0; i < k_songs; ++i) songs[i] = deque_at(list, i);
    album_cover_url(songs, k_songs, url, sizeof url); /* url empty: none yet */
    hero_cover(lb.head, url);
    if (!url[0] && album_cover_busy() && !lb.wait) lb.wait = timer_add(lb_art_wait, 0, 500);
}

/* Play from the first track, or Shuffle, the page's songs. */
static int lb_press(void *ctx, void *event) {
    void *list = P(p_deque_showlist, 0);
    (void)event;
    if (!list || !deque_size(list) || window_manager_get_top_window(window_manager()) != lb.win)
        return 0;
    if (ctx)
        shuffle_play(list);
    else
        play_folder(list, 0, 0xff10); /* as Cover Flow's album tracks play */
    return 0;
}

/* The header and the pair in win (lb.win), in place of any header it had, its name from the album
 * the page opened (classinfo +0xd, which the songs' own replace at the first fill). */
static void lb_dress(void *win) {
    static const char *const label[2] = { "Play", "Shuffle" }, *const icon[2] = { "play",
                                                                                  "shuffle" };
    static const int ctx[2] = { 0, 1 };
    for (unsigned i = widget_count_children(win); i-- > 0;)
        if (widget_get_prop_int(widget_get_child(win, i), LB_HEAD, 0))
            widget_destroy(widget_get_child(win, i));
    lb.head = hero_open(win, (const char *)g_local_classinfo_save + 0xd, "", "");
    widget_set_prop_int(lb.head, LB_HEAD, 1);
    hero_cover(lb.head, 0);
    pair_open(lb.head, lb.surface, label, icon, ctx, lb_press, &lb.stop);
    lb.n = 0;
}

/* A dressed page back on top (from Now Playing, or over another artist or album page, which took
 * artist_page.c's header and lb): drawn again, its stop kept. */
static int lb_front(void *win, void *event) {
    (void)event;
    if (!album_local_on(win)) return 0;
    lb_take(win);
    lb_dress(win);
    return 0;
}

/* navigation.c ringnav_ui_load, as a window's UI loads: an album page is dressed. One is drawn at a time, as artist_page.c keeps one header: a second album page
 * opened over the first takes it, and the first takes it back as it comes to the top again
 * (lb_front). */
void album_local_load(void *win) {
    const char *name = widget_get_prop_str(win, "name", "");
    int jump = !tk_strcmp(name, "playerjumpinfo_page");
    if (!jump && tk_strcmp(name, "albuminfo_page")) return;
    void *surface = widget_lookup(win, jump ? "music_table_client" : "scroll_view", 1);
    if (!surface) return;
    widget_set_prop_int(win, LB_DRESSED, 2); /* it opens on Play */
    lb_take(win);
    for (unsigned i = 0, n = widget_count_children(win); i < n; ++i) { /* the bars over the list */
        void *c = widget_get_child(win, i);
        const char *cn = widget_get_prop_str(c, "name", "");
        if (!tk_strcmp(cn, "view_navbar") || !tk_strcmp(cn, "view_navbar_allplay"))
            widget_set_visible(c, 0, 0);
    }
    drill_list(surface, MAX_ENTRIES); /* the list under the pair, as many whole rows as fit */
    lb_dress(win);
    widget_on(win, EVT_DESTROY, lb_gone, win);
    widget_on(win, EVT_WINDOW_FOREGROUND, lb_front, win);
}

/* ringnav_paint's: the header (its lines read as the page shows), the pair, on the dressed page. */
void album_local_paint(void *w, void *canvas) {
    if (!lb.win || window_of(w) != lb.win) return;
    if (w == lb.head) lb_fill();
    artist_paint(w, canvas);
}
