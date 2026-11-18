/* A Library artist's page (docs/oled-design.md#artist-page
 * and docs/internals.md#library-artist-page): stock's artistinfo_page, whichever way it opened
 * (Library > Artists, Go to Artist, Now Playing's artist tap, Search), keeps its window and its
 * Return, but its tabs are hidden and the page is drawn as a card app's artist (artist_page.c):
 * the header (the name, "N albums · K songs", the songs' commonest genre), Shuffle alone (no
 * radio for card or server-library songs), then Top Songs (only when Most Played has counted one
 * of the artist's songs: their ranking), Albums and All Songs with their counts. Albums opens a
 * page of cover rows newest first, an album there its album page (library_album.c), whatever
 * Artist View is; Top Songs and All Songs open cover rows of songs. Everything is read once,
 * at the page's first tab load (navigation.c artist_list), from the artist page's own songs query
 * (artist_songs), so with Local + Server a mixed artist counts both libraries' songs.
 * ponytail: read once per page, so a library change while it is open shows on its next open. */
#include "navigation.h"

extern void coverflow_newest(void *albums), album_page_open(void *r); /* navigation.c */
extern unsigned album_key(void *r);
extern char *art_path(char *out, unsigned key, const char *suffix);

enum { LA_TOP, LA_ALBUMS, LA_SONGS };
static const char *const la_names[] = { "Top Songs", "Albums", "All Songs" };

/* the dressed artistinfo_page, the view its design is in and its header; owned: the artist's
 * songs, their albums (newest first) and Top Songs; a rows page (artistrows_page) and the deque
 * its rows are; the drill-down rows' kinds; the lit pair stop (artist_page.c pair_key) */
static struct {
    void *win, *body, *head;
    void *songs, *albums, *top;
    void *page, *list;
    int row[3], n;
    signed char stop;
} la __attribute__((section(".scratch")));

int artist_local_on(void *win) { return win && win == la.win; }

static int la_rows_gone(void *page, void *event);
static int la_gone(void *win, void *event) {
    if (win != la.win) return 0;
    if (la.page) la_rows_gone(la.page, 0); /* a rows page outliving it reads none of its deques */
    deque_destroy(la.songs), deque_destroy(la.albums), deque_destroy(la.top);
    la.win = la.body = la.head = la.songs = la.albums = la.top = (void *)0;
    return 0;
}

/* r's album cover as a file:// URL in url (520 bytes), or 0: the server library's for a server
 * row, else Cover Flow's cached cover of its album, once Cover Flow has made it (an empty file is
 * its note of an album with no art). check 0: the URL alone, unchecked. */
static const char *la_cover(void *r, char *url, int check) {
    tk_snprintf(url, 8, "file://");
    if (catalogue_cover(P(r, REC_PATH), url + 7, 513)) return url;
    art_path(url + 7, album_key(r), "");
    return !check || file_size(url + 7) > 0 ? url : (const char *)0;
}

static int la_pick(void *ctx, void *event) {
    int i = (int)(long)ctx;
    if (!la.list || i < 0 || (unsigned)i >= deque_size(la.list)) return 0;
    if (la.list == la.albums)
        album_page_open(deque_at(la.albums, (unsigned)i));
    else
        play_folder(la.list, i, 0xf001); /* a song list, as Most Played's */
    return 0;
}

static int la_rows_gone(void *page, void *event) {
    char url[520];
    if (page != la.page) return 0;
    for (unsigned i = 0; la.list && i < deque_size(la.list); ++i) /* their covers decoded */
        image_manager_unload_bitmap_by_name(image_manager(),
                                            la_cover(deque_at(la.list, i), url, 0));
    la.page = la.list = (void *)0;
    return 0;
}

/* Albums, Top Songs or All Songs as cover rows, as a card app's (docs/oled-design.md#artist-page):
 * an album its year on the right, a song its album after the title and its length. */
static void la_rows(int kind) {
    if (la.page || !(la.page = page_open("artistrows_page", la_rows_gone, page_keyup))) return;
    void *list = la.list = kind == LA_ALBUMS ? la.albums : kind == LA_TOP ? la.top : la.songs;
    int n = (int)deque_size(list), album = kind == LA_ALBUMS;
    unsigned key = 0;
    char url[520], text[256], right[16];
    const char *cover = 0;
    void *view = page_list(la.page, la.page, 0, la_names[kind], n, album ? 40 : 36);
    for (int i = 0; i < n; ++i) {
        void *r = deque_at(list, (unsigned)i);
        const char *name = P(r, REC_ALBUM);
        if (album_key(r) != key || !i) /* a song's album as the row before's: its cover too */
            key = album_key(r), cover = la_cover(r, url, 1);
        if (album) {
            int y = I(r, REC_YEAR);
            tk_snprintf(right, sizeof right, y > 0 ? "%d" : "", y);
            cover_row(view, i, 40, 32, 0, name && *name ? name : "Unknown Album", right, cover,
                      la_pick, i);
        } else
            cover_after(cover_row(view, i, 36, 28, AR_SONG_PX, track_name(text, sizeof text, r),
                                  duration_text(right, sizeof right, r), cover, la_pick, i),
                        name);
    }
    ringnav_select(view, 0, n);
}

static int la_open(void *ctx, void *event) {
    la_rows(la.row[(int)(long)ctx & 3]);
    return 0;
}

static int la_shuffle(void *ctx, void *event) {
    if (!shuffle_play(la.songs)) toast("No songs");
    return 0;
}

/* The commonest genre of the first 256 songs, "" for none. */
static const char *la_genre(void) {
    unsigned n = deque_size(la.songs), most = 0;
    const char *best = "";
    if (n > 256) n = 256;
    for (unsigned i = 0; i < n; ++i) {
        const char *g = P(deque_at(la.songs, i), REC_STYLE);
        unsigned c = 0;
        for (unsigned j = 0; g && *g && j < n; ++j) {
            const char *h = P(deque_at(la.songs, j), REC_STYLE);
            c += h && !strcmp(g, h);
        }
        if (c > most) most = c, best = g;
    }
    return best;
}

/* The page's design in la.body: the drill-down rows, the header, Shuffle. */
static void la_show(void) {
    static const char *const label[2] = { "Shuffle", 0 }, *const icon[2] = { "shuffle", 0 };
    static const int ctx[2] = { 0, 0 };
    unsigned albums = deque_size(la.albums), songs = deque_size(la.songs);
    char sub[48], count[12], name[0x100];
    void *view = page_list(la.win, la.body, 0, "", la.n, 48);
    int h = drill_list(view, la.n);
    for (int k = 0; k < la.n; ++k) {
        void *item = page_row_detail_into(view, k, la_names[la.row[k]], 0, la_open);
        widget_move_resize(item, 0, k * h, 375, h);
        if (la.row[k] == LA_TOP) continue;
        tk_snprintf(count, sizeof count, "%u", la.row[k] == LA_ALBUMS ? albums : songs);
        drill_count(item, count);
    }
    tk_snprintf(sub, sizeof sub, "%u album%s · %u song%s", albums, albums == 1 ? "" : "s", songs,
                songs == 1 ? "" : "s");
    /* the name the songs query asked by (stock writes ARTIST_TITLE after the first tab load) */
    if (g_local_classinfo_save[0xa])
        getFormatString("player_unknown", name, sizeof name);
    else
        tk_snprintf(name, sizeof name, "%s", (const char *)g_local_classinfo_save + 0x10d);
    la.head = hero_open(la.body, name, sub, la_genre());
    pair_open(la.head, view, label, icon, ctx, la_shuffle, &la.stop);
    ringnav_select(view, 0, la.n);
}

/* Another artist page shown over this one (a card app's) took artist_page.c's header: drawn again
 * as the page comes back. */
static int la_front(void *win, void *event) {
    if (win == la.win && hero_now() != la.head) la_show();
    return 0;
}

/* navigation.c artist_list, at each tab load: 1 when the top artistinfo_page is (now) drawn as
 * the design, so stock's tabs, Grid's toolbar and Artist View's Cover Flow stay away. */
int artist_local(void) {
    void *top = window_manager_get_top_window(window_manager()), *a;
    int n;
    if (!top || tk_strcmp(widget_get_prop_str(top, "name", ""), "artistinfo_page"))
        return 0;
    if (top == la.win) return 1;
    if (la.win) la_gone(la.win, 0); /* one artist page at a time; its data goes with the old one */
    la.songs = staged_music(artist_songs, 0, &n);
    la.albums = _create_deque("stSongInfo");
    deque_init(la.albums);
    for (unsigned i = 0; i < deque_size(la.songs); ++i) { /* each album once, by its first song */
        void *s = deque_at(la.songs, i);
        unsigned j = 0;
        while (j < deque_size(la.albums) && album_cmp(deque_at(la.albums, j), s)) ++j;
        if (j == deque_size(la.albums)) _deque_push_back(la.albums, s);
    }
    coverflow_newest(la.albums);
    la.top = plays_ranked(la.songs);
    la.n = 0;
    if (deque_size(la.top)) la.row[la.n++] = LA_TOP;
    if (deque_size(la.albums)) la.row[la.n++] = LA_ALBUMS;
    if (deque_size(la.songs)) la.row[la.n++] = LA_SONGS;
    for (unsigned i = 0, k = widget_count_children(top); i < k; ++i) /* the tabs and toolbar */
        widget_set_visible(widget_get_child(top, i), 0, 0);
    a = widget_factory_create_widget(widget_factory(), "view", top, 0, 0, 375, client_h());
    la.win = top, la.body = a, la.stop = 1; /* opens on Shuffle */
    widget_on(top, EVT_DESTROY, la_gone, top);
    widget_on(top, EVT_WINDOW_FOREGROUND, la_front, top);
    la_show();
    return 1;
}

/* ringnav_paint's: the design's parts on the page and on its rows pages. */
void artist_local_paint(void *w, void *canvas) {
    void *win = la.win ? window_of(w) : (void *)0;
    if (win && (win == la.win || win == la.page)) artist_paint(w, canvas);
}
