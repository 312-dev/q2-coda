/* Ratings: a song's stars and an album's hearts, Now Playing's heart, the Library's Ratings lists,
 * and the card's copy of the ratings. The heart is the like alone (stock's My Fav); a song's stars
 * are set only on Now Playing's rating page (np_rate.c) and are kept here, apart from it. */
#include "navigation.h"

/* Ratings: the songs' 1-5 star and albums' 1-5 heart tables (calloc'd on first use); Now Playing's
 * heart and the heart's stock click context; the Ratings menu. navigation.h's st has the windows
 * and the list page. */
static struct {
    unsigned *rates, *album_rates;
    void *rate_fav, *rate_ctx;
    unsigned rate_row; /* the copy's row key whose heart rate_row_heart filled */
    unsigned rate_gen; /* catalogue_fav_gen when rate_row_heart last read My Fav */
    unsigned rate_tick;
    void *rt_menu;
    unsigned used[2]; /* each table's slots not free (ratings and RATE_GONE marks): a walk stops there */
    unsigned cap[2];  /* each table's words: RATE_MIN, doubled as it fills, up to RATE_SLOTS */
} ra __attribute__((section(".scratch")));

/* Ratings (docs/internals.md#ratings): 0 to 5 stars a song, kept here by listen_key, its low three
 * bits holding the stars, read once into a table calloc'd on first use. The like is stock's My Fav,
 * so Now Playing's heart, the queue menu's Favourites rows and Loved stay one, and it says nothing
 * of the stars. */
#define RATINGS_FILE "/mnt/data/ringnav-ratings"
/* Albums keep 1 to 5 hearts apart from their songs, by coverflow.c's album_id: 5 is the album's
 * favourite, there being no stock one, and puts its songs in My Fav (rate_album_favs). Only the
 * favourite is set now; 1 to 4 kept before stay, and Ratings' Albums lists them. */
#define ALBUM_RATINGS_FILE "/mnt/data/ringnav-album-ratings"
/* Now Playing's heart click (playing_page_init binds it to img_fav with the window): stock's My
 * Fav toggled for the playing track (manage_myfav), and the heart's image with it. */
#define FAV_CLICK ((int (*)(void *, void *))0x53c968)

/* A slot's place: a key's slots are probed from its hash's (open addressing), so a lookup reads a
 * few words, not the whole table. RATE_GONE marks a rating taken out, which a probe passes over
 * and an insert reuses; it reads as no rating (7). A table is RATE_MIN words to start (16 KB, a
 * firmware before this one's size) and doubles, up to RATE_SLOTS, whenever its slots in use would
 * pass three quarters of it, so probes stay short and a small library pays for a small table. Its
 * last two words are no slots: a file laid out this way says so (RATE_LAYOUT) and keeps its slots
 * in use (<< 3, then 6), so it is read as it is; both read as malformed slots (6) to a firmware
 * from before. */
#define RATE_GONE 7u
#define RATE_MIN 4096
#define RATE_LAYOUT 0x48415356u /* "VSAH", its low bits 6 */
static unsigned probe(int album) { return ra.cap[album] - 2; } /* the slots */
static unsigned rate_room(unsigned cap) { return (cap - 2) / 4 * 3; } /* slots in use at most */
static unsigned rate_home(unsigned key, unsigned m) { return ((key >> 3) * 2654435761u) % m; }
/* v at its key's place in t (m slots), over a slot of the same key; *used counts a new one. */
static void rate_place(unsigned *t, unsigned m, unsigned v, unsigned *used) {
    unsigned k = rate_home(v, m);
    for (unsigned n = 0; n < m && t[k] && (t[k] ^ v) >= 8; n++) k = (k + 1) % m;
    *used += !t[k];
    t[k] = v; /* a key twice in a damaged file: its later slot's, as rate_all reads it */
}
/* t's ratings (n words, marks and malformed slots passed over) in a new table of cap words, the
 * album's from now on; 0 with no memory. */
static unsigned *rate_layout(int album, const unsigned *t, unsigned n, unsigned cap) {
    unsigned *u = calloc(cap, sizeof *u), used = 0;
    for (unsigned i = 0; u && i < n && used < rate_room(cap); i++)
        if ((t[i] & 7) && (t[i] & 7) <= 5) rate_place(u, cap - 2, t[i], &used);
    if (u) ra.cap[album] = cap, ra.used[album] = used;
    return u;
}

/* The songs' table, or with album the albums', written whole (its cap words). */
static int rate_io(int album) {
    const char *f = album ? ALBUM_RATINGS_FILE : RATINGS_FILE;
    const char *tmp = album ? ALBUM_RATINGS_FILE ".tmp" : RATINGS_FILE ".tmp";
    unsigned *t = album ? ra.album_rates : ra.rates, m = probe(album);
    t[m] = RATE_LAYOUT, t[m + 1] = ra.used[album] << 3 | 6;
    return blob_io(f, tmp, t, ra.cap[album] * sizeof *t, 1);
}
/* The table, read at its first use: a file this firmware wrote as it is, an older one (its
 * ratings from the first slot, 16 KB) laid out in a table large enough for what it holds. */
static unsigned *rates(int album) {
    unsigned **t = album ? &ra.album_rates : &ra.rates;
    if (*t) return *t;
    const char *f = album ? ALBUM_RATINGS_FILE : RATINGS_FILE;
    /* read in RATE_MIN words, then twice the room while the file goes on (up to RATE_SLOTS),
     * unless what was read ends in a layout mark: a laid-out file is that long */
    unsigned n = 0, room = RATE_MIN, rated = 0, cap = RATE_MIN;
    unsigned *in = calloc(room, sizeof *in);
    void *file = in ? fopen(f, "rb") : 0;
    while (file && (n += (unsigned)fread(in + n, sizeof *in, room - n, file)) == room &&
           room < RATE_SLOTS && in[room - 2] != RATE_LAYOUT) {
        unsigned *more = calloc(room * 2, sizeof *more);
        if (!more) break;
        memcpy(more, in, room * sizeof *in);
        free(in), in = more, room *= 2;
    }
    if (file) fclose(file);
    if (!in) return 0;
    if (n == room && in[n - 2] == RATE_LAYOUT) { /* laid out already */
        ra.cap[album] = n, ra.used[album] = in[n - 1] >> 3;
        return *t = in;
    }
    for (unsigned i = 0; i < n; i++) rated += (in[i] & 7) && (in[i] & 7) <= 5;
    while (cap < RATE_SLOTS && rate_room(cap) < rated + 1) cap *= 2;
    /* an older file kept once as it was (RATINGS_FILE ".v1"), before the first write in this
     * layout replaces it: an older firmware, or a fault in this one, can't lose those ratings */
    char v1[64], v1_tmp[72];
    tk_snprintf(v1, sizeof v1, "%s.v1", f), tk_snprintf(v1_tmp, sizeof v1_tmp, "%s.tmp", v1);
    if (rated && access(v1, 0)) blob_io(v1, v1_tmp, in, n * sizeof *in, 1);
    *t = rate_layout(album, in, n, cap);
    free(in);
    return *t;
}
/* The table twice its size, its ratings laid out again; 0 when it is RATE_SLOTS or no memory. */
static int rate_grow(int album) {
    unsigned **t = album ? &ra.album_rates : &ra.rates, *u;
    if (ra.cap[album] >= RATE_SLOTS || !(u = rate_layout(album, *t, probe(album), ra.cap[album] * 2)))
        return 0;
    free(*t), *t = u;
    return 1;
}

/* key's slot; with free, the first free one on its probe when it has none (the table grown first
 * when a new slot would fill it past its room); -1 when neither. */
static int rate_slot(int album, unsigned key, int free) {
    unsigned *t = rates(album), m = t ? probe(album) : 0, k = rate_home(key, m | !m);
    int empty = -1;
    for (unsigned n = 0; t && n < m; n++, k = (k + 1) % m) {
        if (!t[k]) { /* a mark's slot reused first; a new one only within the table's room */
            if (!free) return -1;
            if (empty >= 0) return empty;
            if (ra.used[album] < rate_room(ra.cap[album])) return (int)k;
            return rate_grow(album) ? rate_slot(album, key, free) : -1;
        }
        if (t[k] == RATE_GONE) {
            if (empty < 0) empty = (int)k;
        } else if ((t[k] ^ key) < 8)
            return (int)k;
    }
    return free ? empty : -1;
}

/* The stars (a song's) or hearts (an album's) kept for key, 1 to 5, else 0 (a malformed slot
 * too). */
int rate_kept(int album, unsigned key) {
    int i = rate_slot(album, key, 0), n = i < 0 ? 0 : (int)(rates(album)[i] & 7);
    return n <= 5 ? n : 0;
}

/* rate_hold's: the songs' table changed while held, written by rate_release. */
static int rate_held __attribute__((section(".scratch")));

/* Keeps n for key, 1 to 5, or none for 0. 1 when saved, 0 when the table is full, -1 when the file
 * was not written (the slot then as it was). Between rate_hold and rate_release a song's is kept
 * in the table only, and saved once by rate_release. */
int rate_keep(int album, unsigned key, int n) {
    int some = n > 0 && n <= 5, i = rate_slot(album, key, some);
    if (i < 0) return !some;
    unsigned *t = rates(album), old = t[i];
    t[i] = some ? (key & ~7u) | (unsigned)n : RATE_GONE;
    ra.used[album] += !old;
    if (!album && rate_held) {
        rate_held = 2;
        return 1;
    }
    if (rate_io(album)) {
        ratings_copy_due();
        return 1;
    }
    t[i] = old;
    return -1;
}

/* A run of songs' rate_keep (catalogue.c's ratings_in: a server's lines) written once, at
 * rate_release: 1 when it was, or nothing changed. */
void rate_hold(void) { rate_held = 1; }
int rate_release(void) {
    int changed = rate_held == 2;
    rate_held = 0;
    if (!changed) return 1;
    if (!rate_io(0)) return 0;
    ratings_copy_due();
    return 1;
}

/* Report only failures from rate_keep. */
int rate_saved(int k) {
    if (k <= 0) toast(k ? "Rating not saved" : "Ratings are full");
    return k > 0;
}

/* r's My Fav changed to on: the card app whose favdir holds it is told (addon_fav_path), as for
 * Now Playing's heart and the queue menu's Favourites rows. 0 when it wasn't, said in a toast. */
int fav_told(void *r, int on) {
    catalogue_fav_changed();
    if (!r || addon_fav_path(P(r, REC_PATH), on)) return 1;
    toast("Favorite not sent to the app");
    return 0;
}

/* An album's favourite is its songs' like: the queue menu's album's tracks into My Fav through
 * batch-select's Add (0xf00a skips those already in it), or out. Their stars stay as they were.
 * ponytail: taking an album out takes every song; nothing records which were favourites first. */
void rate_album_favs(void *album, int on) {
    void *t = qm_collect(album);
    unsigned n = deque_size(t);
    if (on && n) {
        batch_init_selectrecord((int)n);
        for (unsigned i = 0; i < n; i++) batch_set_selectitem((int)i);
        batch_add_file(0xf001, 0xf00a, t, P(p_vector_select_record, 0), 0);
    }
    /* Off tells the app only of songs that were in My Fav: a song liked on its server, never
     * hearted here, is not the Q2's to unlike. The notices stop at the first the card refuses. */
    int told = 1;
    for (unsigned i = 0; i < n; i++) {
        void *r = deque_at(t, i);
        int was = on || checkFavExist(r) != 0;
        if (!on) deleteMusicFromFav(r);
        if (was) catalogue_liked(r, on);
        if (was && told) told = fav_told(r, on);
    }
    deque_destroy(t);
    ratings_copy_due();
}

/* The key r's stars are kept under: its listen_key, or a server song's offline copy's row's
 * (catalogue_song_key), so stars given while the copy plays are the song's on the server and in
 * the Library (docs/server-library.md#ratings-and-favorites). */
unsigned rate_song_key(void *r) { return catalogue_song_key(r, listen_key(r)); }

/* r's My Fav, its like: stock's, by its own path, or a server song's Stream Cache copy's by its
 * server row (catalogue_row_fav), where a star from the server puts it. */
int rate_fav(void *r) { return checkFavExist(r) || catalogue_row_fav(r) > 0; }

/* The playing local track (the queue menu's types), or 0. */
static void *rate_track(void) {
    unsigned at, n, type = (unsigned)MCL(MCL_TYPE);
    if (type != 1 && (type & 0xf000) != 0xf000) return (void *)0;
    return queue_now(&at, &n);
}

/* play_fav, or the theme's own full heart (np_bar.c np_glyphs: "<art>np_fav_d" or "_l") */
static int heart_full(const char *image) {
    unsigned k = (unsigned)strlen(image);
    return (k >= 4 && !tk_strcmp(image + k - 4, "_fav")) || strstr(image, "np_fav_") != 0;
}
static void heart_set(const char *image) {
    image_base_set_image(ra.rate_fav, image);
    widget_invalidate_force(ra.rate_fav, (void *)0);
}
/* Stock draws the heart from the playing file's own My Fav as a track starts. A server song's
 * Stream Cache copy is a favourite by its row instead (rate_fav), so its empty heart (stock's
 * play_unfav or the theme's np_unfav_*) is filled here, and emptied again once neither the row nor
 * the copy's own path is in My Fav (a change from the server: ratings_in takes both out);
 * ra.rate_row says which copy's heart this filled. A full heart stock drew from the copy's own path
 * is checked again (checkFavExist) only when My Fav has changed since the last tick
 * (catalogue_fav_gen), not twice a second. */
static void rate_row_heart(void *r) {
    int row = r ? catalogue_row_fav(r) : -1;
    unsigned key = row >= 0 ? rate_song_key(r) : 0, gen = catalogue_fav_gen();
    int changed = ra.rate_gen != gen;
    const char *image = widget_get_prop_str(ra.rate_fav, "image", "");
    ra.rate_gen = gen;
    if (ra.rate_row != key) ra.rate_row = 0; /* another track: stock's heart */
    if (row > 0 && !heart_full(image) && strstr(image, "unfav"))
        heart_set("play_fav"), ra.rate_row = key;
    else if (!row && heart_full(image) && (ra.rate_row || (changed && !checkFavExist(r))))
        heart_set("play_unfav"), ra.rate_row = 0;
}
/* The heart on Now Playing is the like alone: the playing track's server row fills or empties it
 * (rate_row_heart). The song's stars are the rating page's (np_rate.c), not drawn here. */
static void rate_heart(void) {
    if (ra.rate_fav) rate_row_heart(rate_track());
}

static int rate_tick(const void *unused) {
    if (g_backlight_status) rate_heart();
    return 8; /* RET_REPEAT */
}

/* n stars for the playing track, 0 clearing them: kept by its key and told to the server
 * (docs/server-library.md#ratings-and-favorites); its like, the heart, is left as it is. 1 when
 * kept, 0 when nothing plays that can be rated or it was not saved. */
static int rate_song(int n) {
    void *r = rate_track();
    if (!r || !rate_saved(rate_keep(0, rate_song_key(r), n))) return 0;
    catalogue_rated(r, n);
    return 1;
}

/* The playing track's stars, set (Now Playing's rating page, np_rate.c, and Car Remote's Rate,
 * coda_rc_shim.c: 1 kept) and read: 0 to 5, -1 when nothing plays that can be rated (a stream, a
 * radio). */
int rate_now_set(int n) { return n >= 0 && n <= 5 && rate_song(n); }
int rate_now(void) {
    void *r = rate_track();
    return r ? rate_kept(0, rate_song_key(r)) : -1;
}

/* The heart's click: the like toggled, the stars left alone. Returns 0 as stock's does: a STOP
 * skips the slide_view's own release, and it keeps its grab. A card app's streamed song with an
 * item has the app's heart instead (tidal.c app_fav), flipped at once in stock's Tidal images, as
 * stock's own click draws a 0xf1 track's. */
static int rate_fav_click(void *ctx, void *event) {
    int app = app_fav();
    if (app >= 0) image_base_set_image(ra.rate_fav, app ? "tidal_fav_add" : "tidal_fav_rm");
    if (app == -2) toast("Favorite not saved");
    if (app == -3)
        toast("This app has no favorites"); /* an app's song without an item: not Tidal's */
    if (app != -1) return 0;
    /* A heart filled by a copy's server row (rate_row_heart) is emptied by taking the row out:
     * stock's toggle would put the copy in instead. */
    void *r = rate_track();
    int row = r && catalogue_row_fav(r) > 0;
    ra.rate_row = 0;
    if (row && !checkFavExist(r))
        heart_set("play_unfav");
    else
        FAV_CLICK(ctx, event);
    ratings_copy_due();
    if (r) {
        int fav = checkFavExist(r) != 0;
        if (row && !fav) catalogue_row_unfav(r);
        fav_told(r, fav);
        catalogue_liked(r, fav);
    }
    rate_heart();
    return 0;
}

/* The playing song's like, the heart: an app's streamed song's from its queue (isFav_playing, app_fav), a song
 * on the card's or a server song's Stream Cache copy's from My Fav (rate_fav). 1 liked, 0 not, -1 nothing playing
 * that has a heart here (a stream of the player's own, the radio, a Tidal song). */
int rate_now_liked(void) {
    unsigned type = (unsigned)mclGetPlayType();
    if (type == 2 || type == 3 || type == 0xf2) return -1; /* the types stock's heart click refuses */
    void *r = rate_track();
    if (type == 0xf1) return r && tk_str_start_with(P(r, REC_TIDAL_ID), "app:") ? isFav_playing != 0 : -1;
    return r ? rate_fav(r) != 0 : -1;
}

/* The playing song's like flipped with no page of demo's open, as the heart's click flips it (rate_fav_click):
 * q2ui's Now Playing ring heart, down Car Remote's queue ("like ID", coda_rc_shim.c). An app's streamed song is
 * the app's heart (app_fav); a Tidal song is left alone (stock's click would reach Tidal's API); any other song's
 * My Fav is flipped as stock's click flips it, manage_myfav for the queue's playing song (My Fav's own list read
 * again when that is the page under, g_delete_flag) and then its notifyFavor, or a server row's like taken out
 * where the row filled the heart; then the app and the server are told. 1 liked, 0 not, -1 nothing with a heart
 * here (rate_now_liked), -2 an app that refused it. */
int rate_now_like(void) {
    if (rate_now_liked() < 0) return -1;
    if ((unsigned)mclGetPlayType() == 0xf1) {
        int app = app_fav();
        if (app == -2) toast("Favorite not saved");
        if (app == -3) toast("This app has no favorites");
        return app >= 0 ? app : -2;
    }
    void *r = rate_track();
    int row = catalogue_row_fav(r) > 0;
    if (!row || checkFavExist(r)) {
        if (manage_myfav() != 1 && I(g_class_type, 0) == 0xf00a) g_delete_flag = 1;
        notifyFavor();
    }
    ratings_copy_due();
    int fav = checkFavExist(r) != 0;
    if (row && !fav) catalogue_row_unfav(r);
    fav_told(r, fav);
    catalogue_liked(r, fav);
    rate_heart(); /* demo's own page, if it is open under */
    return fav;
}

static int rate_gone(void *win, void *event) {
    if (win != st.rate_win) return 0;
    stop_timer(&ra.rate_tick);
    st.rate_win = ra.rate_fav = ra.rate_ctx = (void *)0;
    return 0;
}

/* Now Playing's heart (playing_page_init): stock's click is swapped for rate_fav_click, which
 * keeps a server row's like and tells the card app and the server. A press held on it is a tap:
 * the heart has no hold of its own. */
void rate_bind(void *win) {
    void *fav = widget_lookup(win, "img_fav", 1), *emitter = fav ? P(fav, W_EMITTER) : (void *)0;
    stop_timer(&ra.rate_tick);
    ra.rate_ctx = (void *)0;
    for (void *item = emitter ? P(emitter, 0) : 0; item; item = P(item, EMIT_NEXT))
        if (I(item, EMIT_TYPE) == EVT_CLICK && P(item, 12) == (void *)FAV_CLICK &&
            !B(item, EMIT_PENDING_REMOVE)) {
            ra.rate_ctx = P(item, 0);
            break;
        }
    if (!ra.rate_ctx) return;
    st.rate_win = win;
    ra.rate_fav = fav;
    ra.rate_row = 0;
    catalogue_fav_changed(); /* My Fav read again: stock's lists may have changed it meanwhile */
    widget_off_by_func(fav, EVT_CLICK, (void *)FAV_CLICK, ra.rate_ctx);
    widget_on(fav, EVT_CLICK, rate_fav_click, ra.rate_ctx);
    widget_on(win, EVT_DESTROY, rate_gone, win);
    ra.rate_tick = timer_add(rate_tick, (void *)0, 500);
    rate_heart();
}

/* The Library's Ratings row: its menu, then a page of one list's songs, as Most Played's. */
static int fav_songs(void *unused) {
    return getAllFav(); /* select * from myfav, stock's song rows */
}

int rt_play(void *ctx, void *event) {
    play_folder(st.rt_list, (int)(long)ctx, 0xf001);
    return 0;
}
/* A list's page closed (this one's or Long Songs', library_lists.c): its songs go. */
int rt_closed(void *ctx, void *event) {
    st.rt_page = 0;
    if (st.rt_list) deque_destroy(st.rt_list);
    st.rt_list = 0;
    return 0;
}
static int rt_menu_closed(void *ctx, void *event) {
    ra.rt_menu = 0;
    return 0;
}

/* A rated key and its place in rate_all's order: My Fav's songs first (their keys' low bits 7,
 * the like), then the kept slots (their stars in the low three bits) in slot order. */
typedef struct {
    unsigned key, at;
} rated_t;
static int rated_order(const void *a, const void *b) {
    const rated_t *x = a, *y = b;
    if (x->key >> 3 != y->key >> 3) return x->key >> 3 < y->key >> 3 ? -1 : 1;
    return x->at < y->at ? -1 : x->at > y->at;
}

#define RATE_LIKED 0x80 /* rate_all's mark of a song in My Fav, above its stars */
/* Each of songs' stars into h, RATE_LIKED added for a song in My Fav (a server song's Stream Cache
 * copy, which stock's heart on Now Playing puts there, counting as its server row). The keys are
 * sorted once by key, then by place; a song's own run is found by binary search and read in place
 * order, each kept slot's 1 to 5 setting the stars (a damaged table's later slot of a key wins; a
 * malformed one, 0, 6 or 7, is passed over). */
static void rate_all(void *songs, unsigned char *h) {
    int nf, kept = 0;
    void *favs = staged(fav_songs, 0, &nf);
    unsigned nfav = deque_size(favs), n = deque_size(songs), *t = rates(0);
    rated_t *keys = calloc(nfav + ra.used[0] + 1, sizeof *keys);
    for (unsigned i = 0; keys && i < nfav; i++)
        keys[i] = (rated_t){ rate_song_key(deque_at(favs, i)) | 7, i }; /* a copy: its row */
    for (int i = 0; keys && t && i < (int)probe(0) && (unsigned)kept < ra.used[0]; i++)
        if (t[i]) keys[nfav + kept] = (rated_t){ t[i], nfav + kept }, kept++;
    unsigned m = nfav + (unsigned)kept;
    if (keys) qsort(keys, m, sizeof *keys, rated_order);
    for (unsigned i = 0; keys && i < n; i++) {
        unsigned key = listen_key(deque_at(songs, i)) >> 3, lo = 0, hi = m;
        while (lo < hi) {
            unsigned mid = lo + (hi - lo) / 2;
            if (keys[mid].key >> 3 < key)
                lo = mid + 1;
            else
                hi = mid;
        }
        unsigned stars = 0, liked = 0;
        for (unsigned j = lo; j < m && keys[j].key >> 3 == key; j++) {
            unsigned v = keys[j].key & 7;
            if (keys[j].at < nfav)
                liked = RATE_LIKED;
            else if (v && v <= 5)
                stars = v;
        }
        h[i] = (unsigned char)(stars | liked);
    }
    free(keys);
    deque_destroy(favs);
}

/* OLED's Ratings rows: the stars on the second line are Phosphor glyphs, filled for the song's
 * and outlined for the rest, in Text Primary or white on the selection (tools/art.py RATE_PX,
 * oled_rate_on_* and oled_rate_off_*), drawn with the row from its RATE_STARS; the line's words
 * start after them. An unrated song (Loved's) has none, its words where they are. */
#define RATE_PX 14
#define RATE_GAP 2
#define RATE_ROOM (5 * RATE_PX + 4 * RATE_GAP + 6) /* the stars and a space */
#define RATE_STARS "_stars"
static void rate_row(void *item, int n) {
    void *label = n ? widget_get_child(item, 1) : (void *)0;
    if (!label) return;
    widget_set_prop_int(item, RATE_STARS, n);
    widget_move_resize(label, I(label, W_X) + RATE_ROOM, I(label, W_Y), I(label, W_W) - RATE_ROOM,
                       I(label, W_H));
}
void rate_stars_paint(void *w, void *canvas) {
    if (!w) return; /* ringnav_paint calls it while st.rt_page is open */
    int n = widget_get_prop_int(w, RATE_STARS, 0);
    void *label = n ? widget_get_child(w, 1) : (void *)0;
    if (!label) return;
    const char *ink = st.sel_accent && painting_in(st.sel_accent) ? "w" : ipod_light() ? "l" : "d";
    int x = I(label, W_X) - RATE_ROOM, y = I(label, W_Y) + (I(label, W_H) - RATE_PX) / 2;
    char name[16];
    for (int i = 0; i < 5; i++, x += RATE_PX + RATE_GAP) {
        tk_snprintf(name, sizeof name, "rate_%s_%s", i < n ? "on" : "off", ink);
        paint_art(w, canvas, theme_art(name), x, y);
    }
}

/* Top Rated (4-5 stars), Loved (the liked songs, My Fav's, whatever their stars), All Rated (1-5)
 * and Disliked (1): most stars first, then the library's title order, each row's stars under its
 * title (OLED's as glyphs, rate_row). */
static const char *const rt_names[] = { "Top Rated", "Loved", "All Rated", "Disliked" };
#define RT_LOVED 1
#define RT_ALBUMS 2 /* the Ratings menu's Albums row, after Loved */
static int rated_open(void *ctx, void *event) {
    static const unsigned char lo[] = { 4, 0, 1, 1 }, hi[] = { 5, 5, 5, 1 };
    int k = (int)(long)ctx - ((int)(long)ctx > RT_ALBUMS), n;
    if (st.rt_page || !(st.rt_page = page_open("rated_page", rt_closed, page_keyup))) return 0;
    void *songs = staged(all_songs, (void *)LIBRARY_ALL, &n), *list = _create_deque("stSongInfo");
    unsigned size = deque_size(songs);
    unsigned char *h = calloc(size + 1, 1), *of = calloc(size + 1, 1);
    deque_init(list);
    if (h && of) {
        rate_all(songs, h);
        for (int v = hi[k]; v >= lo[k]; v--)
            for (unsigned i = 0; i < size; i++)
                if ((h[i] & 7) == v && (k != RT_LOVED || h[i] & RATE_LIKED))
                    of[deque_size(list)] = (unsigned char)v,
                    _deque_push_back(list, deque_at(songs, i));
    }
    st.rt_list = list;
    deque_destroy(songs);
    int count = (int)deque_size(list);
    static const empty_t none = { "star", "No Rated Songs Yet", "Swipe to the stars on Now Playing",
                                  0, 0, 0 },
                         unloved = { "heart-slash", "No Loved Songs Yet",
                                     "Tap the heart on Now Playing", 0, 0, 0 };
    void *view = count ? page_list(st.rt_page, st.rt_page, 0, rt_names[k], count, 64)
                       : empty_list(st.rt_page, st.rt_page, rt_names[k],
                                    k == RT_LOVED ? &unloved : &none, 0);
    char name[512], detail[300];
    for (int i = 0; i < count; i++) {
        void *t = deque_at(list, (unsigned)i);
        const char *artist = P(t, REC_ARTIST);
        /* a space for no artist: an empty detail would centre the title alone */
        tk_snprintf(detail, sizeof detail, artist && *artist ? "· %s" : " ",
                    artist && *artist ? artist : "");
        void *item = page_row_detail(view, i, track_name(name, sizeof name, t), detail, rt_play);
        playing_mark(item, t, 0);
        rate_row(item, of[i]);
    }
    free(h);
    free(of);
    return 0;
}

/* Albums: the rated albums as Coverflow (coverflow_rated), by most hearts unless sorted. */
extern int coverflow_rated(int (*hearts)(void *));
static int album_hearts(void *r) { return rate_kept(1, album_id(r)); }
int rated_albums(void *ctx, void *event) {
    if (!coverflow_rated(album_hearts)) toast("No rated albums yet");
    return 0;
}

/* The songs' best first, then the albums, then the songs' lists to tidy. */
int ratings_menu(void *ctx, void *event) {
    if (ra.rt_menu || !(ra.rt_menu = page_open("ratings_page", rt_menu_closed, page_keyup)))
        return 0;
    void *view = page_list(ra.rt_menu, ra.rt_menu, 0, "Ratings", 5, 48);
    for (int i = 0; i < 5; i++)
        page_row_into(view, i, i == RT_ALBUMS ? "Albums" : rt_names[i - (i > RT_ALBUMS)],
                      i == RT_ALBUMS ? rated_albums : rated_open);
    return 0;
}

/* The card's copy of the ratings (docs/internals.md#ratings): the tables hold only hashes, so a
 * pass reads the paths and tags back from the library, a page of rows each quiet poll on this
 * thread (stock's shared connection waits only 500 ms for a reader, so a worker's longer read lock
 * could still make stock's own My Fav writes fail), and writes the card once at the end, only if
 * the file would change; the longest copy so far is kept as .1, the one way back from a lost table.
 * The rows are My Fav's, then the card's songtable, then a signed-in server's servertable (cat,
 * catalogue.c), whose songs' stars are kept under their row's fileurl (rate_song_key).
 * ponytail: every row against every rated one still to find (rc_take); a rating whose song or
 * album is in none of the three (deleted from the card, or a server signed out) has no name to
 * write, and stays out until it is back. */
#define RC_FILE "/mnt/mmc/.ratings.tsv"
#define RC_HEADER                                                                                  \
    "#song\tstars\tliked\tpath\tcue_start\n#album\thearts\tname\talbum_artist\tfolder\n"
#define RC_PAGE 64
#define RC_WAIT_MS 5000 /* after a change, so a run of them writes once; between checks */
#define RC_LINE 3200    /* the fields' limits below, and room */
#define RC_GROW 0x10000
#define RC_QUIET_MS 1000 /* no pages this soon after input: a long walk stays off the wheel */
static struct {
    void *db;
    unsigned *song, *album; /* the rated still to find, their stars or hearts in the low bits */
    unsigned songs, albums, len, cap, at;
    char *out;
    int table, last, rows, oom;
    unsigned gen;
} rc __attribute__((section(".scratch")));
static int rc_due __attribute__((section(".scratch")));

void ratings_copy_due(void) {
    rc_due = 1;
    rc.at = time_now_ms();
}

/* key's stars or hearts in the first n of t, its entry gone, or 0. */
static int rc_take(unsigned *t, unsigned *n, unsigned key) {
    for (unsigned i = 0; i < *n; i++)
        if ((t[i] ^ key) < 8) {
            int hearts = (int)(t[i] & 7);
            t[i] = t[--*n];
            return hearts;
        }
    return 0;
}

/* A line's start, its kind and stars or hearts written, with room for RC_LINE more; 0 when there's
 * no memory. */
static char *rc_line(const char *kind, int hearts) {
    if (rc.cap - rc.len < RC_LINE) {
        char *out = realloc(rc.out, rc.cap + RC_GROW);
        if (!out) {
            rc.oom = 1; /* ends the pass: a copy with rows missing must not replace the last */
            return (char *)0;
        }
        rc.out = out, rc.cap += RC_GROW;
    }
    char *o = rc.out + rc.len;
    return o + tk_snprintf(o, 16, "%s\t%d\t", kind, hearts);
}
static void rc_song(const char *path, int stars, int liked, int cue) {
    char *o = rc_line("song", stars);
    if (!o) return;
    *o++ = (char)('0' + liked), *o++ = '\t';
    o = scrobble_tag(o, o + 1100, path);
    if (cue) o += tk_snprintf(o, 16, "%d", cue);
    *o++ = '\n';
    rc.len = (unsigned)(o - rc.out);
}
static void rc_album(const char *name, const char *artist, const char *path, int hearts) {
    char *o = rc_line("album", hearts);
    if (!o) return;
    o = scrobble_tag(o, o + 600, name);
    o = scrobble_tag(o, o + 300, artist);
    unsigned dir = name && *name && artist && *artist ? 0 : path ? album_dir(path, 1) : 0;
    o = scrobble_tag(o, o + (dir < 1100 ? dir : 1100) + 1, path);
    o[-1] = '\n';
    rc.len = (unsigned)(o - rc.out);
}

/* A row as a record, for the keys its song and album are rated under. */
static int rc_row(void *ctx, int n, char **v, char **c) {
    int rec[REC_CUE_END / 4 + 1] = { 0 };
    rc.last = atoi(v[0]), rc.rows++;
    if (!v[1]) return 0;
    P(rec, REC_PATH) = v[1], P(rec, REC_ALBUM) = v[3], P(rec, REC_ALBUM_ARTIST) = v[4];
    int cue = I(rec, REC_CUE_START) = v[2] ? atoi(v[2]) : 0, hearts;
    if (!rc.table) { /* My Fav's: liked, with any stars kept for it */
        rc_song(v[1], rc_take(rc.song, &rc.songs, listen_key(rec)), 1, cue);
        return rc.oom;
    }
    if (rc.songs && (hearts = rc_take(rc.song, &rc.songs, listen_key(rec))))
        rc_song(v[1], hearts, 0, cue);
    if (rc.albums && (hearts = rc_take(rc.album, &rc.albums, album_id(rec))))
        rc_album(v[3], v[4], v[1], hearts);
    return rc.oom;
}

static void rc_end(void) {
    if (rc.db) sqlite3_close(rc.db);
    free(rc.song), free(rc.album), free(rc.out);
    rc.db = 0, rc.song = rc.album = 0, rc.out = 0;
    rc.len = rc.cap = rc.oom = 0;
}

/* path's lines, 0 with no file; same, if asked, whether it is this copy. */
static unsigned rc_read(const char *path, int *same) {
    void *f = fopen(path, "rb");
    char b[1024];
    unsigned n, at = 0, lines = 0;
    int eq = f != 0;
    while (f && (n = fread(b, 1, sizeof b, f)) > 0) {
        eq = eq && at + n <= rc.len && !memcmp(b, rc.out + at, n);
        at += n;
        for (unsigned i = 0; i < n; i++) lines += b[i] == '\n';
    }
    if (f) fclose(f);
    if (same) *same = eq && at == rc.len;
    return lines;
}

/* The card's copy replaced, unless it is this one already. Once the new one is safely written, a
 * copy with more lines goes to .1, unless .1 has more still: a rating given and cleared after a
 * lost table must not take the way back. */
static void rc_write(void) {
    int same;
    unsigned lines = rc_read(RC_FILE, &same), now = 0;
    if (same) return;
    if (!blob_io(RC_FILE ".tmp", RC_FILE ".tmp", rc.out, rc.len, 1)) {
        unlink(RC_FILE ".tmp");
        return;
    }
    for (unsigned i = 0; i < rc.len; i++) now += rc.out[i] == '\n';
    if (lines > now && lines >= rc_read(RC_FILE ".1", 0)) rename(RC_FILE, RC_FILE ".1");
    if (rename(RC_FILE ".tmp", RC_FILE)) unlink(RC_FILE ".tmp");
}

/* The rated of table album, valid stars or hearts only (1 to 5), for rc_take. */
static unsigned *rc_rated(int album, unsigned *n) {
    unsigned *t = rates(album), *c = t ? calloc(ra.used[album] + 1, sizeof *c) : (unsigned *)0;
    for (unsigned i = 0, seen = 0; c && i < probe(album) && seen < ra.used[album]; seen += !!t[i++])
        if ((t[i] & 7) && (t[i] & 7) <= 5u) c[(*n)++] = t[i];
    return c;
}

static void rc_start(void) {
    int unknown[REC_CUE_END / 4 + 1] = { 0 }, hearts;
    rc.songs = rc.albums = 0, rc.table = rc.last = 0;
    rc.song = rc_rated(0, &rc.songs), rc.album = rc_rated(1, &rc.albums);
    rc.out = calloc(rc.cap = RC_GROW, 1);
    if (!rc.song || !rc.album || !rc.out || sqlite3_open("/mnt/data/database.db", &rc.db) ||
        sqlite3_exec(rc.db, "PRAGMA cache_size=64", 0, 0, 0)) {
        rc_end();
        return;
    }
    catalogue_attach_db(rc.db); /* a signed-in server's songs, as cat, while its index is built */
    rc.len = (unsigned)tk_snprintf(rc.out, RC_LINE, RC_HEADER);
    I(unknown, REC_ID) = -1; /* the Unknown Album card, which no row is */
    if ((hearts = rc_take(rc.album, &rc.albums, album_id(unknown))))
        rc_album("Unknown Album", "", (const char *)0, hearts);
}

static int rc_exists(void *ctx, int n, char **v, char **c) {
    rc.rows = 1;
    return 0;
}

/* A page of the pass, then its end. A failed read or write stays as it was, for the next change or
 * boot; a scan rewriting the library ends the pass, and library_gen starts another after it. */
static void rc_page(void) {
    static const char *const tables[] = { "myfav", "songtable", "cat.servertable" };
    char sql[128];
    if (library_scanning()) {
        rc_end();
        return;
    }
    if (time_now_ms() - st.last_input < RC_QUIET_MS) return;
    tk_snprintf(sql, sizeof sql,
                "select id,fileurl,time,album,albumsonger from %s where id>%d order by id limit %d",
                tables[rc.table], rc.last, RC_PAGE);
    rc.rows = 0;
    if (sqlite3_exec(rc.db, sql, rc_row, 0, 0)) {
        rc_end();
        return;
    }
    if (rc.rows == RC_PAGE && (!rc.table || rc.songs || rc.albums)) return;
    /* The next table that is there, while ratings are still to find. No songtable before the first
     * scan; no cat while no server is signed in (its query fails): My Fav's alone, still worth a
     * copy. */
    while (++rc.table < 3 && (rc.table == 1 || rc.songs || rc.albums)) {
        rc.last = rc.rows = 0;
        if (sqlite3_exec(rc.db,
                         rc.table == 1 ? "select 1 from sqlite_master where name='songtable'"
                                       : "select 1 from cat.sqlite_master where name='servertable'",
                         rc_exists, 0, 0) &&
            rc.table == 1) {
            rc_end();
            return;
        }
        if (rc.rows) return;
    }
    if (!card_mounted())
        rc_due = 1; /* shared over USB meanwhile */
    else
        rc_write();
    rc_end();
}

/* card_release.c, before Standby, a power-off or a restart: a pass under way holds its connection,
 * with the server catalogue on the card attached, from page to page; it ends, and starts again once
 * the card is back. */
int ratings_card_release(void) {
    if (!rc.db) return 0;
    rc_end();
    rc_due = 1;
    return 1;
}

/* Every RC_WAIT_MS: a change, the boot (stock's own My Fav rows change it unseen) or the library
 * rewritten, once the card is there, no scan is running and the card is not being let go of. */
void ratings_copy_poll(void) {
    if (catalogue_away()) return;
    if (rc.db) {
        rc_page();
        return;
    }
    unsigned now = time_now_ms();
    if (!rc.at) {
        rc_due = 1, rc.at = now | !now;
        return;
    }
    if (now - rc.at < RC_WAIT_MS) return;
    rc.at = now;
    if (rc.gen != library_gen) rc.gen = library_gen, rc_due = 1;
    if (!rc_due || library_scanning() || !card_mounted()) return;
    rc_due = 0;
    rc_start();
}
