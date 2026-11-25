/* The Library's own list pages: Shuffle, Most Played, the card's songs (Tidal Downloads and a card
 * app's local rows), Era and Long Songs. */
#include "navigation.h"

/* Tidal Downloads' (or a card app's) songs page and its songs, its timer, and whether it shows
 * Clear All Downloads' confirmation; the library generation Most Played's kept tracks are of
 * (~library_gen once stale); the Library Shuffle menu's page, kept across picks. */
static struct {
    void *td_page, *td_list;
    unsigned td_timer;
    unsigned mp_gen;
    void *sp_page;
} ll __attribute__((section(".scratch")));

extern int coverflow_goto_album(void *r);

/* all's songs in a random order, the one at at first; 0 when all has no server row. A list with
 * server rows is handed to their app online (catalogue.c catalogue_load), which plays it in the
 * order it was handed from the start on, so Shuffle Songs' own order, drawn over the queue
 * play_folder loads, does not reach it (the drive, 2026-11-19: the Library's title order from a
 * random start, "rockman", "rockstar", "rockstar"). */
static void *shuffled_for_app(void *all, int at) {
    unsigned n = deque_size(all), server = 0;
    if (!catalogue_has()) return 0; /* no server signed in: no list holds a server row */
    for (unsigned i = 0; i < n && !server; ++i)
        server = catalogue_path(P(deque_at(all, i), REC_PATH));
    unsigned *ix = server ? calloc(n, sizeof *ix) : 0;
    if (!ix) return 0;
    for (unsigned i = 0; i < n; ++i) ix[i] = i;
    ix[0] = (unsigned)at, ix[at] = 0;
    for (unsigned i = n - 1; i > 1; --i) { /* Fisher-Yates over all but the first */
        unsigned j = 1 + rand_below(i), t = ix[i];
        ix[i] = ix[j], ix[j] = t;
    }
    void *out = _create_deque("stSongInfo");
    deque_init(out);
    for (unsigned i = 0; i < n; ++i) _deque_push_back(out, deque_at(all, ix[i]));
    free(ix);
    return out;
}

/* Collection Shuffle is Shuffle Songs over the collection: the list's size, 0 for an empty one.
 * With nothing to play to (docs/internals.md#no-playback-without-an-output) stock's gate refuses
 * with its message before the play mode, Up Next's order or the confirmation changes; the size is
 * still returned, so a caller's "No songs" is not added to it. */
int shuffle_play(void *all) {
    int size = (int)deque_size(all);
    if (size && check_forbid_play_no_headset(0)) return size;
    if (size) {
        int at = (int)rand_below((unsigned)size);
        if (playback_keeps(all, at)) { /* not a pick of it: another song, or alone, Now Playing */
            if (size == 1) {
                playback_np();
                return size;
            }
            at = (at + 1 + (int)rand_below((unsigned)size - 1)) % size;
        }
        int mode = playback_unshuffle();
        playback_clear(); /* stock Shuffle keeps Shuffle Albums/Folders otherwise */
        config_playmode(2, 1);
        void *order = shuffled_for_app(all, at);
        play_folder(order ? order : all, order ? 0 : at, 0xf001);
        if (order) deque_destroy(order); /* playing_page copies the queue at once */
        playback_shuffle(mode); /* an order Up Next can list; stock's shuffle stays the fallback */
        feedback_shuffled("Shuffled Songs", size); /* OLED's confirmation */
    }
    return size;
}

/* Shuffle Songs in steps a loading indicator can run between (docs/internals.md#shuffle-order,
 * Shuffle Songs in steps), each a part of one UI pass: shuffle_songs_start takes the request,
 * shuffle_songs_poll does the next step, the read of every song and then the play, and is 1 once
 * none is left, and shuffle_songs_cancel lets the songs read go. Until the play's step nothing
 * the user hears or sees has changed. The Shuffle page's Shuffle Albums and Folders take the same
 * steps (sg.kind 1 and 2: the play groups them, shuffle_groups). sg.step: 0 none, 1 the read is
 * next, 2 the play. */
static struct {
    void *all;
    int step, kind;
} sg __attribute__((section(".scratch")));
static void shuffle_groups(void *all, int folder);
void shuffle_songs_cancel(void) {
    if (sg.all) deque_destroy(sg.all);
    sg.all = 0, sg.step = 0, sg.kind = 0;
}
void shuffle_songs_start(void) {
    shuffle_songs_cancel();
    sg.step = 1;
}
/* Shuffle Albums (kind 1) or Folders (2): the gate asks first, before the library is read; refused,
 * there is no step left. */
static void shuffle_groups_start(int kind) {
    shuffle_songs_start();
    sg.kind = kind;
    if (check_forbid_play_no_headset(0)) sg.step = 0;
}
int shuffle_songs_poll(void) {
    int n;
    if (sg.step == 1) {
        ui_watch_step("shuffle read"); /* the UI pass watchdog's name for this pass */
#if Q2POD_DEV /* a slow card's read, to see the wait in the emulator: the file there adds 1.5 s */
        if (!access("/mnt/mmc/.q2pod/slow-read", 0)) usleep(1500000);
#endif
        sg.all = staged(songs_unsorted, (void *)1, &n);
        sg.step = 2;
        return 0;
    }
    if (sg.step == 2) ui_watch_step("shuffle play");
    if (sg.step == 2 && sg.kind)
        shuffle_groups(sg.all, sg.kind - 1);
    else if (sg.step == 2 && !shuffle_play(sg.all))
        toast("Update Local Music first");
    shuffle_songs_cancel();
    return 1;
}

/* Shuffle Albums (folder 0) or Folders (1): the whole library, read, loaded as the queue and
 * grouped. */
static void shuffle_groups(void *all, int folder) {
    if (!playback_groups(all, folder))
        toast(deque_size(all) ? "Too many songs to shuffle" : "Update Local Music first");
    else
        feedback_shuffled(folder ? "Shuffled Folders" : "Shuffled Albums", (int)deque_size(all));
}

/* The Library's Shuffle row opens these three actions on one page. A pick starts playback and
 * leaves the page open, as Most Played's rows do. Album and folder shuffles queue and group the
 * whole library, hence the note. Each runs its steps under the loading wait (list_marks.c
 * load_wait_sync, docs/internals.md#loading-wait): the overlay over the page from 300 ms on, and
 * Return before the play's step lets the songs read go and leaves the page as it was. */
static int sp_pick(void *ctx, void *event) {
    static const char *const what[] = { "Shuffling songs\xe2\x80\xa6",
                                        "Shuffling albums\xe2\x80\xa6",
                                        "Shuffling folders\xe2\x80\xa6" };
    long kind = (long)ctx;
    if (kind)
        shuffle_groups_start((int)kind);
    else
        shuffle_songs_start();
    load_wait_sync(ll.sp_page, what[kind], shuffle_songs_poll, shuffle_songs_cancel);
    return 0;
}

static int sp_closed(void *ctx, void *event) {
    ll.sp_page = 0;
    return 0;
}

int shuffle_menu(void *ctx, void *event) {
    if (ll.sp_page || !(ll.sp_page = page_open("shuffle_page", sp_closed, page_keyup))) return 0;
    void *view = page_list(ll.sp_page, ll.sp_page, 0, "Shuffle", 3, 48);
    page_row_detail(view, 0, "Shuffle Songs", 0, sp_pick);
    page_row_detail(view, 1, "Shuffle Albums", 0, sp_pick);
    page_row_detail(view, 2, "Shuffle Folders", 0, sp_pick);
    /* With the title in the bar: centred at the page's foot (28 px up from the window's), its box
     * clear of the glass. */
    int top = bar_foot(ll.sp_page);
    void *note = theme()->page_title_h ? text(ll.sp_page, 20, top + 200, 335, 24)
                                       : text(ll.sp_page, 46, top + client_h() - 28, 283, 24);
    widget_set_prop_int(note, "style:normal:text_color", (int)0xffaaaaaau);
    widget_set_text_utf8(note, "Albums and folders take a moment to shuffle");
    theme_font(note, ROLE_INFO, FONT_ROW);
    widget_set_prop_int(note, "style:normal:font_size", 16);
    return 0;
}

/* Most Played's row: the ranked list from that track, as a library (not folder) queue. */
static int mp_play(void *ctx, void *event) {
    play_folder(st.mp_list, (int)(long)ctx, 0xf001);
    return 0;
}

/* The play table's keys found by hash, for a pass over a library: PLAYS_INDEX slots (twice
 * PLAYS_SLOTS, so at most half full), each 0 or a slot j + 1, open addressing from the key's low
 * bits, filled in table order, so a key's first slot is met first. calloc'd; 0 without memory, and
 * plays_slot then looks through the table. */
#define PLAYS_INDEX (2 * PLAYS_SLOTS)
static unsigned short *plays_index(void) {
    unsigned short *ix = calloc(PLAYS_INDEX, sizeof *ix);
    for (int j = 0; ix && j < PLAYS_SLOTS; j++) {
        unsigned h = st.plays[j].key;
        if (!h) continue; /* an empty slot: listen_key is never 0 */
        while (ix[h & (PLAYS_INDEX - 1)]) ++h;
        ix[h & (PLAYS_INDEX - 1)] = (unsigned short)(j + 1);
    }
    return ix;
}
/* key's first slot in the play table, -1 when it has none. */
static int plays_slot(const unsigned short *ix, unsigned key) {
    if (!key) return -1; /* no song's: listen_key is never 0 */
    if (!ix) {
        for (int j = 0; j < PLAYS_SLOTS; j++)
            if (st.plays[j].key == key) return j;
        return -1;
    }
    for (unsigned h = key; ix[h & (PLAYS_INDEX - 1)]; ++h) {
        int j = ix[h & (PLAYS_INDEX - 1)] - 1;
        if (st.plays[j].key == key) return j;
    }
    return -1;
}

/* The PLAYS_TOP most played of songs, their indices in idx, most played first and, among equal
 * counts, most recently counted first (the table keeps that order); returns how many. */
static int mp_rank(void *songs, int *idx, unsigned *score) {
    int top = 0, size = (int)deque_size(songs);
    plays_load();
    unsigned short *ix = plays_index();
    for (int i = 0; i < size; i++) {
        unsigned key = listen_key(deque_at(songs, i)), c = 0;
        int j = plays_slot(ix, key);
        if (j >= 0) c = st.plays[j].n * PLAYS_SLOTS + PLAYS_SLOTS - j;
        if (!c || (top == PLAYS_TOP && score[top - 1] >= c)) continue;
        int at = top < PLAYS_TOP ? top++ : PLAYS_TOP - 1;
        for (; at > 0 && score[at - 1] < c; at--) score[at] = score[at - 1], idx[at] = idx[at - 1];
        score[at] = c;
        idx[at] = i;
    }
    free(ix);
    return top;
}

/* songs' PLAYS_TOP most played as mp_rank orders them, in a new deque: an artist's Top Songs
 * (library_artist.c). Empty when none of them is counted. */
void *plays_ranked(void *songs) {
    int idx[PLAYS_TOP];
    unsigned score[PLAYS_TOP];
    int top = mp_rank(songs, idx, score);
    void *out = _create_deque("stSongInfo");
    deque_init(out);
    for (int i = 0; i < top; i++) _deque_push_back(out, deque_at(songs, (unsigned)idx[i]));
    return out;
}

/* Coverflow's Most Played order: every counted song's listens added to sum[album_of(song)], the
 * album's index in Coverflow's list (-1 none). One pass over the library, a hash look a song
 * (plays_index). */
void album_plays(int (*album_of)(void *), unsigned *sum) {
    int n;
    void *songs = staged(songs_unsorted, (void *)1, &n);
    plays_load();
    unsigned short *ix = plays_index();
    for (unsigned i = 0; i < deque_size(songs); i++) {
        void *r = deque_at(songs, i);
        int j = plays_slot(ix, listen_key(r));
        unsigned c = j >= 0 ? st.plays[j].n : 0;
        int a = c ? album_of(r) : -1;
        if (a >= 0) sum[a] += c;
    }
    free(ix);
    deque_destroy(songs);
}

/* Where key is among Most Played's tracks, -1 when not (or none are kept). */
static int mp_find(unsigned key) {
    for (unsigned i = 0; st.mp_list && i < deque_size(st.mp_list); i++)
        if (listen_key(deque_at(st.mp_list, i)) == key) return (int)i;
    return -1;
}

/* navigation.c's play_count: r's play counted (key), taking evicted's slot (0 for none). Most
 * Played's kept tracks take a newly counted song, unless its page shows them (its rows and song
 * menu match the deque); one that loses its count may have hidden the next best, which only the
 * whole library knows. */
void mp_counted(void *r, unsigned key, unsigned evicted) {
    if (evicted && mp_find(evicted) >= 0)
        ll.mp_gen = ~library_gen;
    else if (st.mp_list && mp_find(key) < 0) {
        /* r is the counted song's own record, not an offline copy counted as its server row */
        if (!st.mp_page && deque_size(st.mp_list) < 2 * PLAYS_TOP && listen_key(r) == key)
            _deque_push_back(st.mp_list, r);
        else
            ll.mp_gen = ~library_gen;
    }
}

static int mp_closed(void *ctx, void *event) {
    st.mp_page = 0;
    return 0;
}

/* Most Played: a page of the PLAYS_TOP most played songs of the library. The whole library is read
 * only when nothing is kept; otherwise the kept tracks are ranked again.
 * ponytail: the first open after boot or a library change still reads every song, and a song first
 * counted from stock's folder browser shows without its artist until then (no library tags). */
int most_played(void *ctx, void *event) {
    if (st.mp_page || !(st.mp_page = page_open("mostplayed_page", mp_closed, page_keyup))) return 0;
    int n, idx[PLAYS_TOP];
    unsigned score[PLAYS_TOP], gen = library_gen;
    void *from = st.mp_list;
    if (!from || ll.mp_gen != gen) {
        from = staged(songs_unsorted, (void *)1, &n);
        ll.mp_gen = gen;
    }
    int top = mp_rank(from, idx, score);
    void *ranked = _create_deque("stSongInfo");
    deque_init(ranked);
    for (int i = 0; i < top; i++) _deque_push_back(ranked, deque_at(from, idx[i]));
    if (st.mp_list && st.mp_list != from) deque_destroy(st.mp_list);
    deque_destroy(from);
    st.mp_list = ranked;
    static const empty_t none = { "chart-bar", "No Plays Yet",
                                  "Your most played songs show up here", 0, 0, 0 };
    void *view = top ? page_list(st.mp_page, st.mp_page, 0, "Most Played", top, 64)
                     : empty_list(st.mp_page, st.mp_page, "Most Played", &none, 0);
    char name[512], detail[300];
    for (int i = 0; i < top; i++) { /* the artist, if tagged, and the play count, under the title */
        void *t = deque_at(st.mp_list, (unsigned)i);
        const char *artist = P(t, REC_ARTIST);
        unsigned plays = (score[i] - 1) / PLAYS_SLOTS, a = artist && *artist;
        tk_snprintf(detail, sizeof detail, a ? "%s · %u play%s" : "%s%u play%s", a ? artist : "",
                    plays, plays == 1 ? "" : "s");
        playing_mark(page_row_detail(view, i, track_name(name, sizeof name, t), detail, mp_play), t,
                     0);
    }
    return 0;
}

/* Songs on the card, played from it as a folder queue, without Wi-Fi: Tidal Downloads
 * (docs/internals.md#tidal-cache), Streaming's row listing tidal.c's complete downloads, and an
 * add-on's local rows (songs_open: q2-deezer's downloads). A folder lists its songs newest first,
 * or, an album's (q2-deezer's Artist/Album folders, their files "01 - Title [id].flac"), by name:
 * in track order. ponytail: newest download first, not last played; demo imports no utime to touch
 * a replay. */
typedef struct {
    long mtime;
    void *r;
} td_row;
static int td_newer(const void *a, const void *b) {
    long x = ((const td_row *)a)->mtime, y = ((const td_row *)b)->mtime;
    return (y > x) - (y < x);
}
static int td_named(const void *a, const void *b) {
    const char *x = P(((const td_row *)a)->r, REC_NAME), *y = P(((const td_row *)b)->r, REC_NAME);
    return strcmp(x ? x : "", y ? y : "");
}
static int td_play(void *ctx, void *event) {
    void *t = deque_at(ll.td_list, (unsigned)(long)ctx);
    if (t && !access(P(t, REC_PATH), 0)) play_folder(ll.td_list, (int)(long)ctx, 0xf001);
    return 0;
}
/* dir's songs, newest first (or, album, by name), a deque the caller destroys. */
static void *card_songs(const char *dir, int album) {
    int n = 0;
    void *rows = staged(directory, (void *)dir, &n);
    unsigned size = deque_size(rows), info[36];
    td_row *v = calloc(size + 1, sizeof *v);
    n = 0;
    for (unsigned i = 0; v && i < size; i++) { /* songs only: .tmp, .part, folders aren't audio */
        void *r = deque_at(rows, i);
        if (I(r, REC_TYPE) == 8 && P(r, REC_PATH) && !__xstat(3, P(r, REC_PATH), info))
            v[n++] = (td_row){ (long)info[16], r };
    }
    if (n) qsort(v, (unsigned)n, sizeof *v, album ? td_named : td_newer);
    void *songs = _create_deque("stSongInfo");
    deque_init(songs);
    for (int i = 0; i < n; i++) _deque_push_back(songs, v[i].r);
    free(v);
    deque_destroy(rows);
    return songs;
}
/* A card app's stream folder's songs in play order (addon.c stream_poll), a deque the caller
 * destroys. */
void *app_album_songs(const char *dir) { return card_songs(dir, 1); }

/* A song's label from its file: Tidal's after its "<id>-<quality> ", a card app's before a
 * trailing " [<id>]" (q2-deezer's, as OpenDeezer names them), either less its extension. */
static void song_label(const char *f, int tidal, char *out, unsigned size) {
    const char *sp = tidal && f ? strstr(f, " ") : 0;
    tk_snprintf(out, size, "%s", sp ? sp + 1 : f ? f : "");
    char *dot = strrchr(out, '.'), *id;
    if (dot && dot != out) *dot = 0;
    if (!tidal && (id = strrchr(out, '[')) && id > out + 1 && id[-1] == ' ' &&
        id[strlen(id) - 1] == ']')
        id[-1] = 0;
}
static int td_show(const void *unused);
/* Clear All Downloads…: the confirmation card asks (docs/oled-design.md#confirmation-card), and
 * Remove (row 1) clears them (tidal_clear); the list again from a timer, as a row's click may not
 * rebuild the list it is in. */
static void td_answer(void *ctx, int row, int toggle_on) {
    if (row == 1) tidal_clear();
    if (ll.td_page) rearm(&ll.td_timer, td_show, 0);
}
static char td_body[48] __attribute__((section(".scratch"))); /* the card's: the count */
static int td_clear(void *ctx, void *event) {
    static const char *const rows[] = { "Cancel", "Remove" };
    unsigned n = ll.td_list ? deque_size(ll.td_list) : 0;
    tk_snprintf(td_body, sizeof td_body,
                n == 1 ? "%u track. Settings are kept." : "%u tracks. Settings are kept.", n);
    confirm(&(confirm_t){ .title = "Remove Downloads?",
                          .body = td_body,
                          .rows = rows,
                          .n_rows = 2,
                          .kind = CONFIRM_DESTRUCTIVE,
                          .back = -1,
                          .answer = td_answer });
    return 0;
}
/* The page's songs, read again; Tidal's end with Clear All Downloads…, as Spotify's Storage
 * Management does. Returns the list. */
static void *songs_rows(const char *dir, const char *title, int tidal, int album) {
    if (ll.td_list) deque_destroy(ll.td_list);
    ll.td_list = card_songs(dir, album);
    int n = (int)deque_size(ll.td_list);
    static const empty_t none = { "download-simple", "No Downloads Yet",
                                  "Synced music shows up here", 0, 0, 0 };
    void *view = n ? page_list(ll.td_page, ll.td_page, 0, title, n + tidal, 48)
                   : empty_list(ll.td_page, ll.td_page, title, &none, 0);
    char name[256];
    for (int i = 0; i < n; i++) {
        void *r = deque_at(ll.td_list, (unsigned)i);
        song_label(P(r, REC_NAME), tidal, name, sizeof name);
        playing_mark(page_row_detail(view, i, name, 0, td_play), r, 0);
    }
    if (tidal && n) page_row_detail(view, n, "Clear All Downloads…", 0, td_clear);
    return view;
}
/* The page's list, again after Clear All Downloads' card answered. */
static int td_show(const void *unused) {
    ll.td_timer = 0;
    if (!ll.td_page) return 0;
    void *view = songs_rows(TIDAL_DIR, "Tidal Downloads", 1, 0);
    unsigned n = deque_size(ll.td_list);
    if (n) ringnav_select(view, (int)n, (int)n + 1);
    return 0;
}
static int td_closed(void *ctx, void *event) {
    ll.td_page = 0;
    stop_timer(&ll.td_timer);
    if (ll.td_list) deque_destroy(ll.td_list);
    ll.td_list = 0;
    return 0;
}
static int songs_page(const char *window, const char *dir, const char *title, int tidal,
                      int album) {
    if (ll.td_page || !(ll.td_page = page_open(window, td_closed, page_keyup))) return 0;
    songs_rows(dir, title, tidal, album);
    return 0;
}
int tidal_downloads(void *ctx, void *event) {
    (void)ctx, (void)event;
    return songs_page("tidal_downloads", TIDAL_DIR, "Tidal Downloads", 1, 0);
}
/* A card app's local row (addon.c): a folder ("/" last) lists its songs; a song plays its folder
 * from it, as Tidal Downloads does. album: the folder is an album's, listed and played in track
 * order. */
int songs_open(const char *path, const char *title, int album) {
    char dir[256];
    unsigned k = strlen(path);
    if (!k || k >= sizeof dir) return 0;
    tk_snprintf(dir, sizeof dir, "%s", path);
    if (dir[k - 1] == '/') {
        if (k > 1) dir[k - 1] = 0;
        return songs_page("app_songs", dir, title, 0, album);
    }
    char *slash = strrchr(dir, '/');
    if (!slash || slash == dir) return 0;
    *slash = 0;
    void *songs = card_songs(dir, album);
    int at = -1;
    for (unsigned i = 0; at < 0 && i < deque_size(songs); i++) {
        const char *p = P(deque_at(songs, i), REC_PATH);
        if (p && !strcmp(p, path)) at = (int)i;
    }
    if (at >= 0)
        play_folder(songs, at, 0xf001); /* playing_page copies the queue at once */
    else
        toast("It isn't on the card any more");
    deque_destroy(songs);
    return 0;
}

/* Era (docs/internals.md#era): a row per decade with dated music, oldest first, and a decade's
 * albums by year. An album's year is its tracks' latest, as Coverflow's Year sort; undated albums
 * and the untagged have none. ponytail: decades only, no year level or covers, and the album rows
 * have no hold menu; a year page, or the decade as a scoped Coverflow, if asked for. */
#define ERA_DECADES 32
#define ERA_SELECT                                                                                 \
    "select album,ifnull(nullif(albumsonger,''),songer)," ALBUM_YEAR                               \
    " as release_year," ALBUM_TRACK " from songtable "                                             \
    "where " MUSIC_WHERE " and ifnull(album,'')<>''"
/* ALBUM_GROUP's three keys as an order, after the year and the name: the page's rows and Play All's
 * songs in one order, two albums of one name and year included. */
#define ERA_ORDER                                                                                  \
    "ifnull(albumsonger,'') COLLATE NOCASE,case ifnull(albumsonger,'') when '' then "              \
    "rtrim(fileurl,replace(fileurl,'/','')) end"
/* A decade's page makes its first ERA_FIRST album rows as it opens and ERA_CHUNK more each UI pass
 * after (era_rows_due), so a large decade costs a pass a screenful rather than one pass for every
 * row's four widgets (docs/internals.md#era). */
#define ERA_FIRST 16
#define ERA_CHUNK 96
typedef struct {
    char *album, *artist, *path;
    int year;
} era_t;
static struct {
    void *page, *albums; /* the decades page, a decade's page */
    int decade[ERA_DECADES], count[ERA_DECADES], n;
    era_t *v; /* the open decade's albums, oldest first, room for cap */
    unsigned m, cap;
    void *view;     /* the decade's rows */
    unsigned built; /* its album rows made so far, of m */
    unsigned timer; /* era_rows_due's while built < m */
} era __attribute__((section(".scratch")));

int era_sql(const char *sql, int (*row)(void *, int, char **, char **), void *ctx) {
    void *db = 0;
    /* Era uses SQLite directly rather than the stock library query wrappers. */
    char *expanded = library_scope_query(sql); /* none: the whole library, as library_query */
    /* a signed-in server's songs too (catalogue.c), its file attached to this connection.
     * ponytail: the scope's folders (MUSIC_WHERE) apply to the union here, so a scope leaves the
     * server's songs out of Era; library_query scopes the card's alone. */
    char *both = catalogue_union(expanded ? expanded : sql);
    /* Keep a full-library grouping query from competing with the player's song records for RAM. */
    int bad = sqlite3_open("/mnt/data/database.db", &db) ||
              sqlite3_exec(db, "PRAGMA cache_size=64;PRAGMA temp_store=FILE", 0, 0, 0);
    if (!bad && (both || strstr(sql, " cat."))) catalogue_attach_db(db); /* the union, or cat named */
    bad = bad || sqlite3_exec(db, both ? both : expanded ? expanded : sql, row, ctx, 0);
    if (db) sqlite3_close(db);
    free(expanded), free(both);
    return !bad;
}
/* Home pane's counts: sql's one number, -1 when it can't be had. On coverflow.c count_worker's
 * thread: era_sql opens its own connection, and the device's libsqlite3 is THREADSAFE=1. */
static int count_row(void *ctx, int n, char **v, char **c) {
    if (n == 1 && v[0]) *(int *)ctx = atoi(v[0]);
    return 0;
}
int library_count(const char *sql) {
    int n = 0;
    return era_sql(sql, count_row, &n) ? n : -1;
}
static int era_decade_row(void *ctx, int n, char **v, char **c) {
    if (n == 2 && v[0] && v[1] && era.n < ERA_DECADES)
        era.decade[era.n] = atoi(v[0]), era.count[era.n++] = atoi(v[1]);
    return 0;
}
static int era_album_row(void *ctx, int n, char **v, char **c) {
    if (n != 4 || !v[0] || !v[2] || !v[3] || era.m == era.cap) return 0;
    era_t *e = &era.v[era.m];
    e->album = strdup(v[0]), e->artist = strdup(v[1] ? v[1] : ""), e->path = strdup(v[3]);
    e->year = atoi(v[2]);
    if (!e->album || !e->artist || !e->path) {
        free(e->album), free(e->artist), free(e->path);
        return 1;
    }
    ++era.m;
    return 0;
}
static void era_free(void) {
    for (unsigned i = 0; i < era.m; ++i)
        free(era.v[i].album), free(era.v[i].artist), free(era.v[i].path);
    free(era.v);
    era.v = 0, era.m = era.cap = 0;
}
static int era_closed(void *ctx, void *event) {
    if (ctx == era.page) era.page = 0;
    if (ctx == era.albums) {
        stop_timer(&era.timer);
        era.albums = era.view = 0, era.built = 0;
        era_free();
    }
    return 0;
}
/* "60s" to "90s", then "2000s" on. */
static const char *era_name(char *buf, int decade) {
    tk_snprintf(buf, 16, decade < 2000 ? "%02ds" : "%ds", decade < 2000 ? decade % 100 : decade);
    return buf;
}

/* Album i's record: the album name's songs, the one at its path. *songs holds it, the caller's;
 * *n is the query's count, -1 when it failed. */
static int era_by_name(void *name) { return getMusicByAlbum(name); }
static void *era_record(unsigned i, void **songs, int *n) {
    *songs = staged_music(era_by_name, era.v[i].album, n);
    for (unsigned k = 0; k < deque_size(*songs); ++k)
        if (!strcmp(P(deque_at(*songs, k), REC_PATH), era.v[i].path)) return deque_at(*songs, k);
    return 0;
}
/* The decade's songs in one query, then the library's music filtered by them: the files of
 * ERA_SELECT's groups dated in the decade, matched on ALBUM_GROUP's keys. ordered (Play All): in
 * the page's album order, each album in album order (disc, track, path), as Coverflow's All albums
 * plays; else (Shuffle) in the library's order, about to be shuffled. 0 if a query failed. One
 * query, as two stock queries an album cost 1.2 s for 2,664 albums in the emulator. */
typedef struct {
    unsigned key, rank; /* a song-table id, and its place in the query's order */
} era_key_t;
static struct {
    era_key_t *v;
    unsigned n, cap;
} era_keys __attribute__((section(".scratch")));
static int era_key_row(void *ctx, int n, char **v, char **c) {
    if (n != 1 || !v[0]) return 0;
    if (era_keys.n == era_keys.cap) {
        unsigned cap = era_keys.cap ? era_keys.cap * 2 : 1024;
        era_key_t *p = calloc(cap, sizeof *p);
        if (!p) return 1;
        if (era_keys.n) memcpy(p, era_keys.v, era_keys.n * sizeof *p);
        free(era_keys.v);
        era_keys.v = p, era_keys.cap = cap;
    }
    era_keys.v[era_keys.n].key = (unsigned)atoi(v[0]);
    era_keys.v[era_keys.n].rank = era_keys.n;
    ++era_keys.n;
    return 0;
}
static int era_key_cmp(const void *a, const void *b) {
    const era_key_t *x = a, *y = b;
    return x->key != y->key ? (x->key < y->key ? -1 : 1)
                            : (x->rank > y->rank) - (x->rank < y->rank);
}
static void *era_decade_songs(int ordered) {
    char sql[2048];
    int from = era.m ? era.v[0].year / 10 * 10 : 0, n;
    tk_snprintf(
        sql, sizeof sql,
        "select s.id from songtable s join (select album a,ifnull(albumsonger,'') b,case "
        "ifnull(albumsonger,'') when '' then rtrim(fileurl,replace(fileurl,'/','')) end d,"
        "%s as y from songtable where %s and ifnull(album,'')<>''%s having y between %d and %d) g "
        "on s.album=g.a COLLATE NOCASE and ifnull(s.albumsonger,'')=g.b COLLATE NOCASE and "
        "(g.d is null or rtrim(s.fileurl,replace(s.fileurl,'/',''))=g.d)%s",
        ALBUM_YEAR, MUSIC_WHERE, ALBUM_GROUP, from, from + 9,
        ordered ? " order by g.y,g.a COLLATE NOCASE,g.b COLLATE NOCASE,g.d,ifnull(s.cd_num,0),"
                  "ifnull(s.track,0),s.fileurl,s.id"
                : "");
    era_keys.n = 0;
    void *out = 0, **slot = 0;
    if (!era.m || era_sql(sql, era_key_row, 0)) out = _create_deque("stSongInfo"), deque_init(out);
    if (out && era_keys.n && ordered && !(slot = calloc(era_keys.n, sizeof *slot)))
        deque_destroy(out), out = 0;
    if (out && era_keys.n) {
        qsort(era_keys.v, era_keys.n, sizeof *era_keys.v, era_key_cmp);
        void *all = staged(songs_unsorted, (void *)1, &n);
        if (n < 0) deque_destroy(out), out = 0; /* the library load failed */
        for (unsigned i = 0; out && i < deque_size(all); ++i) {
            unsigned key = (unsigned)I(deque_at(all, i), REC_ID), lo = 0, hi = era_keys.n;
            while (lo < hi) /* the first key not below this one: its first place */
                if (era_keys.v[(lo + hi) / 2].key < key)
                    lo = (lo + hi) / 2 + 1;
                else
                    hi = (lo + hi) / 2;
            if (lo == era_keys.n || era_keys.v[lo].key != key) continue;
            if (!ordered)
                _deque_push_back(out, deque_at(all, i));
            else if (!slot[era_keys.v[lo].rank])
                slot[era_keys.v[lo].rank] = deque_at(all, i);
        }
        for (unsigned i = 0; out && slot && i < era_keys.n; ++i)
            if (slot[i]) _deque_push_back(out, slot[i]);
        deque_destroy(all);
    }
    free(slot);
    free(era_keys.v);
    era_keys.v = 0, era_keys.n = era_keys.cap = 0;
    return out;
}
/* Play All from the first song as a library queue, as Coverflow's All albums; Shuffle is Shuffle
 * Songs over them; an album opens its tracks in Coverflow. */
static int era_pick(void *ctx, void *event) {
    int i = (int)(long)ctx;
    if (i < 2) {
        ui_watch_step(i ? "decade shuffle" : "decade play all");
        void *all = era_decade_songs(!i);
        if (!all) {
            toast("Years unavailable"); /* as a decade's albums: not a card that's away */
            return 0;
        }
        if (!deque_size(all) || catalogue_missing(P(deque_at(all, 0), REC_PATH)))
            toast("Storage unavailable");
        else if (i)
            shuffle_play(all);
        else
            play_folder(all, 0, 0xf001);
        deque_destroy(all);
        return 0;
    }
    ui_watch_step("decade album");
    int n = 0;
    void *songs, *r = (unsigned)(i - 2) < era.m ? era_record((unsigned)(i - 2), &songs, &n) : 0;
    if (n < 0) /* partial rows then an error: not an album to open */
        toast("Years unavailable");
    else if (!r || !coverflow_goto_album(r))
        toast("Album not found");
    if ((unsigned)(i - 2) < era.m) deque_destroy(songs);
    return 0;
}
/* The decade's album rows up to its row upto (of m), after those made. */
static void era_rows_to(unsigned upto) {
    char detail[300];
    for (; era.built < upto && era.built < era.m; ++era.built) {
        const era_t *e = &era.v[era.built];
        tk_snprintf(detail, sizeof detail, *e->artist ? "%s \xe2\x80\xa2 %d" : "%s%d", e->artist,
                    e->year);
        page_row_detail_into(era.view, (int)era.built + 2, e->album, detail, era_pick);
    }
}
/* The rows' timer: ERA_CHUNK more a pass until all are made. */
static int era_rows_due(const void *unused) {
    ui_watch_step("decade rows");
    era_rows_to(era.built + ERA_CHUNK);
    if (era.built < era.m) return 8; /* RET_REPEAT */
    era.timer = 0;
    return 0;
}
/* Every row of page made now, when it is a decade's still being filled: the search bar's
 * (search_bar.c), whose search reads the page's rows. */
void era_rows_finish(void *page) {
    if (!page || page != era.albums || era.built >= era.m) return;
    stop_timer(&era.timer);
    ui_watch_step("decade rows");
    era_rows_to(era.m);
}
static int era_open_decade(void *ctx, void *event) {
    int k = (int)(long)ctx, from = era.decade[k];
    if (era.albums) return 0;
    ui_watch_step("decade albums");
    char sql[1536], name[16], detail[32];
    /* The groups of albums with a track dated in the decade: an album's year is its latest
     * track's, so no other group can be in it, and the grouping reads that much less. */
    tk_snprintf(sql,
                sizeof sql, /* MUSIC_WHERE's like patterns hold '%': an argument, not the format */
                "%s and album COLLATE NOCASE in (select album from songtable where " TRACK_YEAR
                " between %d and %d)" ALBUM_GROUP " having release_year between %d and %d order by "
                "release_year,album COLLATE NOCASE," ERA_ORDER,
                ERA_SELECT, from, from + 9, from, from + 9);
    era.v =
        calloc((unsigned)era.count[k] + 1, sizeof *era.v); /* the decade's count, from era_menu */
    era.cap = era.v ? (unsigned)era.count[k] : 0;
    if (!era.v || !era_sql(sql, era_album_row, 0)) { /* as era_menu: not a decade of no albums */
        era_free();
        toast("Years unavailable");
        return 0;
    }
    if (!(era.albums = page_open("era_albums_page", era_closed, page_keyup))) {
        era_free();
        return 0;
    }
    era.view = page_list(era.albums, era.albums, 0, era_name(name, from), (int)era.m + 2, 64);
    tk_snprintf(detail, sizeof detail, era.m == 1 ? "%u album" : "%u albums", era.m);
    page_row_detail(era.view, 0, "Play All", detail, era_pick);
    page_row_detail(era.view, 1, "Shuffle", "Every song, shuffled", era_pick);
    era.built = 0;
    era_rows_to(ERA_FIRST);
    if (era.built < era.m) era.timer = timer_add(era_rows_due, (void *)0, 0);
    return 0;
}
int era_menu(void *ctx, void *event) {
    if (era.page) return 0;
    ui_watch_step("years list");
    era.n = 0;
    if (!era_sql("select year/10*10,count(*) from (select " ALBUM_YEAR
                 " year from songtable where " MUSIC_WHERE " and ifnull(album,'')<>''" ALBUM_GROUP
                 ") where year>0 group by 1 order by 1",
                 era_decade_row, 0)) {
        toast("Years unavailable");
        return 0;
    }
    if (!(era.page = page_open("era_page", era_closed, page_keyup))) return 0;
    if (!era.n) { /* the empty state (docs/oled-design.md#empty-and-offline) */
        static const empty_t e = { "calendar-blank", "No Dated Albums Yet",
                                   "Albums need a year tag", 0, 0, 0 };
        empty_list(era.page, era.page, "Years", &e, 0);
        return 0;
    }
    void *view = page_list(era.page, era.page, 0, "Years", era.n, 64);
    char name[16], detail[32];
    for (int i = 0; i < era.n; ++i) {
        tk_snprintf(detail, sizeof detail, era.count[i] == 1 ? "%d album" : "%d albums",
                    era.count[i]);
        page_row_detail_into(view, i, era_name(name, era.decade[i]), detail, era_open_decade);
    }
    return 0;
}

/* Long Songs shares durations.c's cache and worker with Cover Flow. With Scan Song Durations off,
 * closing the page stops its pass as Cancel does, keeping what was measured; on, the pass goes on
 * and Cover Flow shows what it measures. */
typedef struct {
    unsigned key, sec;
} length_t;
static struct {
    unsigned timer;
    void *songs, *title;
} ls __attribute__((section(".scratch")));

static int by_length(const void *a, const void *b) {
    unsigned x = ((const length_t *)a)->sec, y = ((const length_t *)b)->sec;
    return (x < y) - (x > y);
}

/* The page's list: the songs over LONG_SONG_S, longest first, each row's artist and length under
 * its title, a row playing the list from itself as Most Played's. */
static void ls_show(void) {
    void *songs = ls.songs, *list = _create_deque("stSongInfo");
    unsigned size = deque_size(songs), n = 0;
    length_t *v = calloc(size + 1, sizeof *v); /* key: the song's index */
    deque_init(list);
    for (unsigned i = 0; v && i < size; i++) {
        int sec = duration_seconds(deque_at(songs, i));
        if (sec > LONG_SONG_S) v[n].key = i, v[n++].sec = (unsigned)sec;
    }
    if (n) qsort(v, n, sizeof *v, by_length);
    for (unsigned i = 0; i < n; i++) _deque_push_back(list, deque_at(songs, v[i].key));
    st.rt_list = list;
    static const empty_t none = { "timer", "No Long Songs", "None over 10 minutes", 0, 0, 0 };
    void *view = n ? page_list(st.rt_page, st.rt_page, 0, "Long Songs", (int)n, 64)
                   : empty_list(st.rt_page, st.rt_page, "Long Songs", &none, 0);
    char name[512], detail[300], t[16];
    for (unsigned i = 0; i < n; i++) {
        void *r = deque_at(list, i);
        const char *artist = P(r, REC_ARTIST);
        toolsTimeItoa(t, (int)v[i].sec);
        tk_snprintf(detail, sizeof detail, artist && *artist ? "%s · %s" : "%s%s",
                    artist && *artist ? artist : "", t);
        playing_mark(
            page_row_detail(view, (int)i, track_name(name, sizeof name, r), detail, rt_play), r, 0);
    }
    free(v);
    deque_destroy(songs);
    ls.songs = 0;
}

/* The page gone: what was probed is kept. Its list is ratings.c's slot, whose rows play it
 * (rt_play). */
static int ls_page_closed(void *ctx, void *event) {
    rt_closed(ctx, event);
    stop_timer(&ls.timer);
    if (!st.scan_durations) durations_cancel(); /* switch off: the pass is the page's own */
    durations_busy(0, 0); /* harvest if finished; with the switch on a pass goes on */
    if (ls.songs) deque_destroy(ls.songs);
    ls.songs = 0;
    return 0;
}

static int ls_done(const void *unused) {
    ls.timer = 0;
    if (st.rt_page && ls.songs) ls_show();
    return 0;
}

static int ls_cancel(void *ctx, void *event) {
    durations_cancel(); /* publish completed jobs when the worker exits */
    return 0;
}

static int ls_poll(const void *unused) {
    int done, total;
    if (!durations_busy(&done, &total)) return ls_done(0);
    char t[64];
    tk_snprintf(t, sizeof t, "Measuring songs\xe2\x80\xa6 %d/%d", done, total);
    page_retitle(st.rt_page, ls.title, t);
    return 8; /* RET_REPEAT */
}

/* Long Songs' row: every song measured first, whatever Scan Song Durations is, unless a whole pass
 * already covered this library or every song already has a length; a pass running is observed,
 * never duplicated. */
int long_songs(void *ctx, void *event) {
    if (st.rt_page || !(st.rt_page = page_open("longsongs_page", ls_page_closed, page_keyup)))
        return 0;
    int n;
    ls.songs = staged(songs_unsorted, (void *)1, &n);
    if (durations_start(ls.songs)) {
        page_row_detail(
            page_list(st.rt_page, st.rt_page, &ls.title, "Measuring songs\xe2\x80\xa6", 1, 48), 0,
            "Cancel", 0, ls_cancel);
        ls.timer = timer_add(ls_poll, (void *)0, 250);
    } else
        ls_show();
    return 0;
}
