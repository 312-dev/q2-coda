/* Called by stock long Return and short Return on Home, after their gates.
 * Elsewhere it opens Now Playing over the page, remembered as np_origin; on Now Playing it goes
 * back to that page while it lies right under it, else Home. With Hold Return: Home it goes Home
 * from any page but Home itself, where it still opens Now Playing.
 * Over Quick Settings the hold closes it, as Return does (ringnav), and acts on the page under it
 * once it has gone (quick_poll): Now Playing opened over the dialog left it open underneath, and
 * stock's pull-down starts none while one is (g_dropdown_menu_state), so Now Playing had no Quick
 * Settings until Return had gone back to the one left open and closed it (#303). Over a prompt it
 * cancels it, and over Search (search_strip.c) it closes Search.
 * ponytail: every way leaves the latch to the stock release filter, as stock's own Home call here
 * did: the hold's key-up, where it reaches the filter, is swallowed there; clearing it let that
 * release act on Home, and since V1.34 go straight back from the Now Playing it had opened (#303).
 * A release that never arrives leaves it for chord_gate's next press to clear. */
int compact_now_playing(void) {
    pull_cancel();
    drop_input();
    void *wm = window_manager();
    void *top = window_manager_get_top_window(wm);
    if (usable() && top && !window_manager_is_animating(wm)) {
        const char *name = widget_get_prop_str(top, "name", "");
        if (!tk_strcmp(name, "statusbar_dialog")) {
            if (!dialog_quit(top, 0)) st.qs_hold = (unsigned)time_now_ms(); /* not one refused */
            return 0;
        }
        /* A prompt (Shut Down? and ours) is usable, but Now Playing opened under its modal loop
         * and the prompt came back over it: a second Shut Down? (#303). The hold cancels it, as
         * the prompt's own Return does (dialog_quit 4, 0x499bdc). */
        if (!tk_strcmp(name, "confirminfo_dialog")) {
            dialog_quit(top, 4);
            return 0;
        }
        if (search_strip_hold(top)) return 0; /* Search closes: its Return erases */
        pod_settings();
        if (st.hold_home && tk_strcmp(name, "home_page")) {
            st.np_origin = (void *)0;
            navigator_back_to_home();
        } else if (tuner_state() && tk_strcmp(name, "tuner_page")) {
            st.np_origin = top; /* Radio owns Now Playing: its page, as Now Playing's below */
            tuner_open((void *)0, (void *)0);
        } else if (!tk_strcmp(name, "playing_page") || !tk_strcmp(name, "tuner_page")) {
            /* ponytail: np_origin is only compared, never followed; a reused address goes Back
             * instead of Home */
            unsigned n = st.np_origin ? widget_count_children(wm) : 0;
            int back = n >= 2 && widget_get_child(wm, n - 1) == top &&
                       widget_get_child(wm, n - 2) == st.np_origin;
            st.np_origin = (void *)0;
            if (back) {
                navigator_back();
            } else {
                navigator_back_to_home();
            }
        } else {
            static const int context[4] = { 0, 0, 0xff, 2 };
            st.np_origin = top;
            navigator_switch_to_with_context("playing_page", context, 0);
        }
    }
    return 0;
}

/* Play/Pause hold queue menu (docs/internals.md). Stock long press fires once per press, so a hold
 * on a local song, album, artist/composer/genre, folder or playlist row opens the stock sortselect
 * dialog rebuilt as that row's menu. */
enum {
    QM_SONG = 1,
    QM_ALBUM,
    QM_GROUP,
    QM_FOLDER,
    QM_COVERFLOW,
    QM_COVERALBUM,
    QM_PLAYING,
    QM_PLAYLIST
};
enum {
    QA_NEXT = 1,
    QA_ADD,
    QA_SHUFFLE,
    QA_FAV,
    QA_UNFAV,
    QA_PLAYLIST,
    QA_ALBUM,
    QA_ARTIST,
    QA_ADV_SHUFFLE,
    QA_REPEAT,
    QA_GROUP,
    QA_NEXT_GROUP,
    QA_PREV_GROUP,
    QA_RESUME,
    QA_QUEUE,
    QA_FOLDER,
    QA_CHAPTERS,
    QA_MORE,
    QA_OTG,  /* Add to On-The-Go (playlist_pages.c) */
    QA_PLAY, /* the menu sheet's playlist: Play, Rename, Delete (playlists.c) */
    QA_RENAME,
    QA_DELETE,
    QA_REMOVE, /* a payload page's own last row (qm_song_hold): Remove from Playlist */
    QA_OFFLINE /* Sync Offline, or Stop Syncing Offline, for a server's item (offline.c) */
};

static char *play_key(void) {
    char *s = window_manager_get_input_device_status(window_manager());
    for (int i = 0; s && i < INPUT_KEY_COUNT; ++i)
        if (I(s, INPUT_KEYS + i * INPUT_KEY_SIZE) == KEY_PLAY)
            return s + INPUT_KEYS + i * INPUT_KEY_SIZE;
    return (char *)0;
}

/* Any Play/Pause release ends the latch; only the held press itself is swallowed. */
int play_hold_released(void) {
    char *k = play_key();
    int held = k && st.qm_press && *(unsigned long long *)(k + INPUT_KEY_TIME) == st.qm_press;
    st.qm_press = 0;
    return held;
}

/* A record's fields from REC_NAME to last, and its CUE range: to REC_PATH its identity, to
 * REC_ARTIST its tags too. */
static unsigned rec_hash_to(void *r, int last) {
    unsigned h = FNV_SEED;
    for (int o = REC_NAME; o <= last; o += 4) h = fnv(h, P(r, o));
    return hash_bytes(h, (const unsigned char *)r + REC_CUE_START, 8);
}
/* The row a hold menu is on: an album row (a cover's) or a playlist's is told apart by its tags. */
static unsigned rec_hash(void *r) { return rec_hash_to(r, REC_ARTIST); }

/* Everything a row's tracks are resolved from; a change while the menu is open cancels it. */
static unsigned browse_hash(void) { return local_hash(hash_bytes(FNV_SEED, g_folder_path, 1024)); }

/* Coverflow's or Most Played's tracks, when page is theirs. */
static void *page_tracks(void *page) {
    return !page                ? (void *)0
           : page == st.mp_page ? st.mp_list
           : page == st.rt_page ? st.rt_list
                                : coverflow_tracks(page);
}

/* A queue's identity: its records' names, paths and CUE ranges, not their tags, which card_tags
 * fills as a song starts (the queue is not changed by that). */
unsigned queue_hash(void *q) {
    unsigned h = FNV_SEED;
    for (unsigned i = 0, n = deque_size(q); i < n; ++i) {
        unsigned r = rec_hash_to(deque_at(q, i), REC_PATH);
        h = hash_bytes(h, (const unsigned char *)&r, sizeof r);
    }
    return h;
}

/* A payload page's own songs held (qm_song_hold): the page, its list, its last row's words and
 * what that row runs. */
static struct {
    void *page, *list;
    const char *extra;
    void (*pick)(void);
} qm_held __attribute__((section(".scratch")));
static int qm_held_on(void *page) {
    return st.qm_kind == QM_COVERFLOW && page && page == qm_held.page;
}

static void *qm_list(void) {
    if (st.qm_kind == QM_PLAYING) return P(mcl_pdeqplaylist, 0);
    void *top = window_manager_get_top_window(window_manager());
    if (qm_held_on(top)) return qm_held.list;
    if (st.qm_kind == QM_PLAYLIST) return P(p_deque_playlist, 0);
    return st.qm_kind == QM_COVERFLOW ? page_tracks(window_manager_get_top_window(window_manager()))
                                      : P(p_deque_showlist, 0);
}

void *qm_record(void) {
    if (st.qm_kind == QM_PLAYING) {
        unsigned at, n;
        void *r = queue_now(&at, &n), *q = P(mcl_pdeqplaylist, 0);
        unsigned type = (unsigned)MCL(MCL_TYPE);
        return r && q == st.qm_queue && at == st.qm_idx && n == st.qm_rows &&
                       (type == 1 || (type & 0xf000) == 0xf000) && airplayGetFlag() != 2 &&
                       rec_hash(r) == st.qm_hash && queue_hash(q) == st.qm_queue_hash
                   ? r
                   : 0;
    }
    if (st.qm_kind == QM_COVERALBUM) {
        void *r = coverflow_album(window_manager_get_top_window(window_manager()));
        return r && rec_hash(r) == st.qm_hash ? r : 0;
    }
    void *list = qm_list();
    if (!list || deque_size(list) != st.qm_rows || st.qm_idx >= st.qm_rows ||
        (st.qm_kind < QM_COVERFLOW && browse_hash() != st.qm_browse))
        return (void *)0;
    void *r = deque_at(list, st.qm_idx);
    return r && rec_hash(r) == st.qm_hash ? r : (void *)0;
}

/* The queue menu's album's album_id, 0 when not an album it rates: a Coverflow cover's, a Library
 * Albums row's (split as Coverflow's), an artist's albums tab row's (ringnav_artist_list). */
unsigned qm_album_id(void *r) {
    if (st.qm_kind == QM_COVERALBUM || (st.qm_kind == QM_ALBUM && st.qm_cls == CLASS_ALBUMS))
        return album_id(r);
    return st.qm_kind == QM_ALBUM && st.qm_cls == CLASS_ARTIST_ALBUMS && st.art_ids &&
                   st.art_n == st.qm_rows && st.qm_idx < st.art_n
               ? st.art_ids[st.qm_idx]
               : 0;
}

/* Now Playing's More… (img_more, on both layouts), while that page is open; else 0. */
static void *np_more(void) {
    if (st.np_win) return widget_lookup(st.np_win, "img_more", 1);
    return (void *)0;
}

static void qm_close(void) {
    void *dialog = st.qm_dialog;
    st.qm_dialog = (void *)0;
    if (dialog) window_close(dialog);
}

/* Return and the title bar arrow dismiss without the stock sort-change flag. */
static int qm_open(const void *unused);
static void qm_values(void *dialog);
static int qm_back(void *dialog, void *event) {
    if (I(event, EVENT_TYPE) == EVT_KEY_UP && I(event, EVENT_KEY) != KEY_RETURN) return 0;
    void *menu = st.qm_dialog; /* forgotten at once, as qm_close: the next hold may open another */
    st.qm_dialog = (void *)0;
    modal_close(menu); /* a sheet falls away first (menu_modal.c) */
    return STOP;
}

static int qm_gone(void *dialog, void *event) {
    if (st.qm_dialog == dialog) st.qm_dialog = (void *)0;
    return 0;
}

/* An album, artist/composer/genre or folder row: the query stock would run for it, as
 * batch_add_file (0x4ff344) expands each class. Artist 0xf004/0xff01 rows name the artist at +0x18,
 * composer 0xf005/0xff02 at +0x20, genre 0xf006/0xff03 at +0x1c; classinfo +0xa.. flags Unknown. */
static const unsigned char qm_by[] = { REC_ARTIST, 0x20, 0x1c };
static int class_query(unsigned cls, void *r) {
    unsigned g = (cls & 0xf) - (cls < 0xff00 ? 4 : 1);
    int artist = I(g_artist_type, 0) == 1, id = I(r, REC_ID);
    if (cls == CLASS_ALBUMS) return getMusicByAlbum(id == -1 ? (const char *)0 : P(r, REC_ALBUM));
    if (cls < 0xff00)
        return (g == 2   ? getMusicByGenre
                : g      ? getMusicByComposer
                : artist ? getMusicByAlbumArtist
                         : getMusicBySonger)(id == -1 ? (const char *)0 : P(r, qm_by[g]));
    /* load_album_detaillist 0xff01 -> load_localclass_list 0xff11, and its composer/genre kin */
    return (g == 2   ? getMusicByAlbumAndGenre
            : g      ? getMusicByAlbumAndComposer
            : artist ? getMusicByAlbumAndAlbumSonger
                     : getMusicByAlbumAndSonger)(
        P(r, REC_ALBUM), g_local_classinfo_save[0xa + g] ? (const char *)0 : P(r, qm_by[g]),
        id == -2);
}

/* A playlist's songs, as its row opens them (cd_num, as ple_open). */
static int qm_query(void *r) {
    return st.qm_kind == QM_PLAYLIST ? getMusicByPlayList(I(r, REC_DISC))
                                     : class_query(st.qm_cls, r);
}

/* An Albums row (load_album_detaillist) or Go to album opens its name's songs, 0xff10 with
 * classinfo +0xd the name and +0x10d the artist: r is kept, a copy, to keep its album alone. */
static void album_open(void *r) {
    if (st.album) deque_destroy(st.album);
    st.album = 0;
    if (!r || I(r, REC_ID) < 0) return;
    st.album = _create_deque("stSongInfo");
    deque_init(st.album);
    _deque_push_back(st.album, r);
}
int ringnav_album_detail(int cls, void *r) {
    album_open(cls == CLASS_ALBUMS ? r : 0);
    return stock_detail_trampoline(cls, r);
}
/* The opened album, when the classinfo 0xff10 loads is still its name and artist. */
static void *album_opened(void) {
    const char *info = (const char *)g_local_classinfo_save;
    void *a = st.album ? deque_at(st.album, 0) : 0;
    const char *name = a ? P(a, REC_ALBUM) : 0, *artist = a ? P(a, REC_ARTIST) : 0;
    int same = name && artist && !info[9] && !strcasecmp(info + 0xd, name);
    return same && !strcmp(info + 0x10d, artist) ? a : 0;
}

/* Stock's album art cache, /mnt/mmc/.sldp/<md5 of the name>0.jpg (1.jpg big), keys by name, so
 * split Albums rows of a name shared a cover: theirs adds the album artist, else the album's
 * folder. A name of one album keeps its key and cached art. buf holds 256 bytes, as stock's. */
static int twin(void *o, void *r) {
    const char *x = P(o, REC_ALBUM), *y = P(r, REC_ALBUM);
    return x && !strcasecmp(x, y) && album_cmp(o, r);
}
static const char *art_key(char *buf, void *list, unsigned i) {
    void *r = deque_at(list, i);
    const char *name = P(r, REC_ALBUM), *aa = P(r, REC_ALBUM_ARTIST), *path = P(r, REC_PATH);
    if (!name || !((i && twin(deque_at(list, i - 1), r)) ||
                   (i + 1 < deque_size(list) && twin(deque_at(list, i + 1), r))))
        return name;
    if (aa && *aa)
        snprintf(buf, 256, "%s\x1f%s", name, aa);
    else
        snprintf(buf, 256, "%s\x1f%.*s", name, path ? (int)album_dir(path, 1) : 0,
                 path ? path : "");
    return buf;
}
/* The Albums row renderer's snprintf(buf, 0x100, "%s", album) (0x4a52bc), a2 the row. */
int ringnav_art_name(char *buf, unsigned size, unsigned row, const char *album) {
    char key[256];
    void *list = P(p_deque_showlist, 0);
    return snprintf(buf, size, "%s", row < deque_size(list) ? art_key(key, list, row) : album);
}
/* albumcoverinfo_init's and foldercoverinfo_init's _deque_push_back of each row's cover task
 * (0x4f79c4, 0x4f7b6c): the list's copy (0x5c639c) duplicates the task's name and path, which stock
 * malloc'd for it and never freed. */
void ringnav_cover_push(void *covers, void *task) {
    _deque_push_back(covers, task);
    free(P(task, 4)), free(P(task, 8));
}
/* A list's albumcoverinfo_init: the art thread hashes each cover task's copy of its row's name
 * (+4, read under aclist_mutex): key renames a row's, or returns 0 to keep it. */
static int rename_covers(void *list, const char *(*key)(char *, void *, unsigned)) {
    int result = albumcoverinfo_init(list);
    void *covers = P(pdeq_albumcoverlist, 0);
    char buf[256], *s;
    const char *k;
    pthread_mutex_lock((void *)aclist_mutex);
    for (unsigned i = 0; i < deque_size(list) && i < deque_size(covers); ++i) {
        void *r = deque_at(covers, i);
        char **name = (char **)((char *)r + 4);
        if ((k = key(buf, list, i)) && (s = strdup(k))) {
            if (strcmp(*name, s)) ((unsigned char *)r)[0] = ((unsigned char *)r)[1] = 0;
            free(*name), *name = s;
        }
    }
    pthread_mutex_unlock((void *)aclist_mutex);
    return result;
}
static const char *split_key(char *buf, void *list, unsigned i) {
    return art_key(buf, list, i) == buf ? buf : 0;
}
/* The Albums page's albumcoverinfo_init calls. */
int ringnav_albumcovers(void *list) { return rename_covers(list, split_key); }

/* A job can finish after navigation rebuilt the list: its row number now belongs to another
 * album. Compare the key copied by the worker before setting either cover-ready flag. */
void ringnav_cover_done(int row, int big, const char *key) {
    pthread_mutex_lock((void *)aclist_mutex);
    void *covers = P(pdeq_albumcoverlist, 0);
    if ((unsigned)row < deque_size(covers) && (big == 0 || big == 1)) {
        void *r = deque_at(covers, (unsigned)row);
        const char *name = P(r, 4);
        if (name && key && !strcmp(name, key)) ((unsigned char *)r)[big] = 1;
    }
    pthread_mutex_unlock((void *)aclist_mutex);
}

/* An artist's albums (load_localartist_list 1) group by name too, and a name there is usually one
 * row though another artist's album shares it, so each row takes its album's art_key in the split
 * Albums order: the same-named album in its folder, else by its album artist. ponytail: an album
 * across folders whose album artist isn't the row's keeps stock's key; look up its track's
 * albumsonger by fileurl if that shows. */
static void artist_keys_free(void) {
    for (unsigned i = 0; i < st.art_n; ++i) free(st.art_keys[i]);
    free(st.art_keys);
    free(st.art_ids);
    st.art_keys = 0, st.art_ids = 0, st.art_n = 0;
}
static int same_dir(void *a, void *b) {
    const char *x = P(a, REC_PATH), *y = P(b, REC_PATH);
    unsigned n = x ? album_dir(x, 1) : 0;
    return n && y && album_dir(y, 1) == n && !memcmp(x, y, n);
}
/* r's album among all's rows of its name: by folder, else album artist as r's artist; or -1. */
static int artist_album(void *all, void *r) {
    const char *name = P(r, REC_ALBUM), *artist = P(r, REC_ARTIST);
    for (int by_dir = 1; by_dir >= 0; --by_dir)
        for (unsigned j = 0; name && j < deque_size(all); ++j) {
            void *a = deque_at(all, j);
            const char *x = P(a, REC_ALBUM), *aa = P(a, REC_ALBUM_ARTIST);
            if (x && !strcasecmp(x, name) &&
                (by_dir ? same_dir(a, r) : aa && artist && !strcasecmp(aa, artist)))
                return (int)j;
        }
    return -1;
}
extern void coverflow_by_year(void *albums);
/* The artist page's songs, as its Songs tab lists them (staged_music: with a server signed in,
 * the server library's too); also library_artist.c's. */
int artist_songs(void *unused) {
    return (I(artist_type, 0) == 1 ? getMusicByAlbumArtist : getMusicBySonger)(
        g_local_classinfo_save[0xa] ? (const char *)0
                                    : (const char *)g_local_classinfo_save + 0x10d);
}
/* Split artwork keys use the whole library; the layout uses only this artist's albums. */
static void *artist_albums(void *all) {
    int n;
    void *songs = staged_music(artist_songs, 0, &n), *out = _create_deque("stSongInfo");
    deque_init(out);
    for (unsigned i = 0; i < deque_size(all); ++i) {
        void *a = deque_at(all, i);
        for (unsigned j = 0; j < deque_size(songs); ++j) {
            void *s = deque_at(songs, j);
            const char *name = P(s, REC_ALBUM);
            if (I(a, REC_ID) < 0 ? !name || !*name : !album_cmp(a, s)) {
                _deque_push_back(out, a);
                break;
            }
        }
    }
    deque_destroy(songs);
    return out;
}
/* The artist page's grid rows in mine's order (artist_albums, by year), as artist_album finds
 * them; rows it misses keep their order after. */
static void artist_order(void *list, void *mine) {
    unsigned n = deque_size(list), m = deque_size(mine);
    int *at = calloc(n + 1, sizeof *at);
    if (!at) return;
    for (unsigned i = 0; i < n; ++i) at[i] = artist_album(mine, deque_at(list, i));
    void *out = _create_deque("stSongInfo");
    deque_init(out);
    for (unsigned k = 0; k <= m; ++k) /* k == m: the unmatched */
        for (unsigned i = 0; i < n; ++i)
            if (k < m ? at[i] == (int)k : at[i] < 0) _deque_push_back(out, deque_at(list, i));
    free(at);
    deque_clear(list);
    deque_assign(list, out);
    deque_destroy(out);
}
/* The page Coverflow is about to cover (Playlists, from playlists.c) opens unseen
 * and without its slide, so the list cuts straight to the covers, as Home's Coverflow card does;
 * once they are up it is shown again under them, its slide back for its own Return. */
void cf_veil(void *win, int on) {
    if (on) {
        tk_snprintf(st.art_cf_hint, sizeof st.art_cf_hint, "%s",
                    widget_get_prop_str(win, "anim_hint", ""));
        widget_set_prop_str(win, "anim_hint", "");
    } else
        widget_set_prop_str(win, "anim_hint", st.art_cf_hint);
    widget_set_opacity(win, on ? 0 : 0xff);
}
static int artist_list(int tab) {
    int n = stock_artist_trampoline(tab);
    void *list = P(p_deque_showlist, 0), *all;
    /* An artist with no name: its title (the navbar's, the bar's) is the language's "Unknown
     * Artist", as Songs' rows say, not glibc's "(null)" */
    if (!strcmp((const char *)ARTIST_TITLE, "(null)"))
        getFormatString("player_unknown", (char *)ARTIST_TITLE, 0x200);
    /* the design's page in place of the tabs (library_artist.c), on the top artistinfo_page */
    artist_keys_free();
    if (artist_local()) return n;
    if (tab != 1 || n <= 0 || !(st.art_keys = calloc((unsigned)n, sizeof(char *)))) return n;
    st.art_ids = calloc((unsigned)n, sizeof *st.art_ids);
    st.art_n = (unsigned)n;
    all = _create_deque("stSongInfo");
    deque_init_copy(all, list);
    coverflow_split(all);
    /* The artist's albums by year (Playback settings' Year order) or A-Z, the grid's rows in
     * theirs. */
    void *mine = artist_albums(all);
    coverflow_by_year(mine);
    artist_order(list, mine);
    for (unsigned i = 0; i < st.art_n && i < deque_size(list); ++i) {
        void *r = deque_at(list, i);
        int j = artist_album(all, r);
        const char *k;
        char buf[256];
        if (j >= 0 && (k = split_key(buf, all, (unsigned)j))) st.art_keys[i] = strdup(k);
        if (j >= 0 && st.art_ids) st.art_ids[i] = album_id(deque_at(all, (unsigned)j));
    }
    deque_destroy(mine);
    deque_destroy(all);
    return n;
}
int ringnav_artist_list(int tab) {
    int saved = library_music;
    library_music = 1;
    int n = artist_list(tab);
    library_music = saved;
    return n;
}
static const char *artist_key(char *buf, void *list, unsigned i) {
    return st.art_keys && list == P(p_deque_showlist, 0) && st.art_n == deque_size(list) &&
                   i < st.art_n
               ? st.art_keys[i]
               : 0;
}
/* The artist page's albums renderer's snprintf of the name (0x4adb4c), a2 the row. */
int ringnav_artist_art_name(char *buf, unsigned size, unsigned row, const char *album) {
    const char *k = artist_key(0, P(p_deque_showlist, 0), row);
    return snprintf(buf, size, "%s", k ? k : album);
}
/* The artist page's albumcoverinfo_init calls. */
int ringnav_artist_covers(void *list) {
    return st.art_keys ? rename_covers(list, artist_key) : albumcoverinfo_init(list);
}
/* The album page's big cover, snprintf of classinfo +0xd (0x4a910c): its Albums row's cover task
 * name, as the thread hashed it, when that is this album's key. */
int ringnav_art_header(char *buf, unsigned size, const char *fmt, const char *album) {
    void *covers = P(pdeq_albumcoverlist, 0);
    unsigned i = (unsigned)I(ALBUMINFO_ROW, 0), n = strlen(album);
    const char *key = i < deque_size(covers) ? P(deque_at(covers, i), 4) : 0;
    return snprintf(buf, size, fmt,
                    key && !strncasecmp(key, album, n) && key[n] == 0x1f ? key : album);
}

/* An empty Library list (Albums, Artists, Composers, Genres): once stock marks its page opened
 * (the "stage" property the window manager sets after the open animation), the page is closed
 * again and the empty state's page (feedback.c empty_open) takes its place. Stock opened a black
 * page, and an empty Albums grid's Return then did nothing until a restart (Hold Return: Home,
 * back_to_home, still left it); the cause was not found in the window manager's close path, so the
 * page goes before a Return can reach it, with the forced close that path uses. The page is its
 * class's (g_class_type), on top, whichever of the two list pages shows it, polled every
 * EMPTY_POLL_MS until it opens or stops being on top. */
static int empty_class(const void *unused) {
    static const empty_t none[] = {
        { "vinyl-record", "No Albums", "Copy music to the SD card", 0, 0, 0 },
        { "microphone-stage", "No Artists", "Copy music to the SD card", 0, 0, 0 },
        { "music-notes-simple", "No Composers", "Songs need a composer tag", 0, 0, 0 },
        { "guitar", "No Genres", "Songs need a genre tag", 0, 0, 0 },
    };
    static const char *const names[] = { "Albums", "Artists", "Composers", "Genres" };
    void *wm = window_manager(), *top = window_manager_get_top_window(wm);
    const char *name = top ? widget_get_prop_str(top, "name", "") : "";
    int mine = (!tk_strcmp(name, "album_page") || !tk_strcmp(name, "localclass_page")) &&
               I(g_class_type, 0) == CLASS_ALBUMS + st.empty_kind;
    if (mine && widget_get_prop_int(top, "stage", 0) != WINDOW_STAGE_OPENED) {
        timer_add(empty_class, (void *)0, EMPTY_POLL_MS);
        return 0;
    }
    if (mine) window_manager_close_window_force(wm, top);
    empty_t e = none[st.empty_kind];
    empty_card(&e);
    empty_open("empty_page", names[st.empty_kind], &e, 0);
    return 0;
}

/* Artists, Composers and Genres rows have no picture: the rows are one line (one_line, the set's
 * text_art 0), so the name gets the width (compact_row_layout skips hidden siblings). */
int ringnav_class_icon(void *row, int i) {
    (void)i;
    widget_set_visible(widget_lookup(row, "img_icon", 1), 0, 0);
    return 0;
}

/* load_localclass_list: stock fills p_deque_showlist and returns its size (Playlists and Baidu:
 * their own deque's). getAllAlbum,
 * getAllArtist (and its album-artist twin), getAllComposer and getAllGenre end with an Unknown row
 * (id -1) whenever the library has songs; it goes when the query its press runs finds none. Stock
 * clears the staging deque before returning, and so does this. Stock never sets g_artist_type,
 * which picks the album-artist queries for Artists and an artist's albums, so those lists always
 * went by the Artist tag; it follows artist_type, the setting the artist page itself uses.
 * getAllAlbum groups by name alone: Albums is split as Coverflow is (album artist, else folder),
 * and the name's songs an album row opens (0xff10) keep only that album (album_open). An empty
 * list opens the empty state's page for what it lacks. */
/* list_get_img_pic: a song row's format badge (img_flac, img_mp3, ...) by its extension. "null" is
 * stock's no badge, which hides it, as an iPod's rows carry none. */
int ringnav_badge(const char *path, char *out, int size) {
    tk_snprintf(out, (unsigned)size, "null");
    return 0;
}
/* A deque's songs by path and CUE start (listen_key), open addressing at most half full: slot j + 1
 * for record j, so a key's first record in deque order is met first. calloc'd, 0 if that fails. */
unsigned *rec_index(void *songs, unsigned *mask) {
    unsigned n = deque_size(songs), size = 2, *slot;
    while (size < 2 * n) size <<= 1;
    *mask = size - 1;
    if (!(slot = calloc(size, sizeof *slot))) return 0;
    for (unsigned j = 0; j < n; ++j) {
        void *s = deque_at(songs, j);
        if (!P(s, REC_PATH)) continue;
        unsigned h = listen_key(s);
        while (slot[h & *mask]) ++h;
        slot[h & *mask] = j + 1;
    }
    return slot;
}
/* The first of rec_index's songs with r's path and CUE start, else 0. */
void *rec_find(void *songs, const unsigned *slot, unsigned mask, void *r) {
    const char *path = P(r, REC_PATH);
    for (unsigned h = path ? listen_key(r) : 0; path && slot[h & mask]; ++h) {
        void *s = deque_at(songs, slot[h & mask] - 1);
        if (!strcmp(P(s, REC_PATH), path) && I(s, REC_CUE_START) == I(r, REC_CUE_START)) return s;
    }
    return 0;
}
/* My Fav's, Frequent's and Recent's rows are stock's copies of the songs as added or played, some
 * with an empty title though the library has the tag (a song played from Folder, or a server
 * song's row): an empty one takes the library's, by path and CUE start, so it reads as every other
 * list's instead of falling back to its file name and track number. The library is asked for
 * those rows alone, the card's songtable and a signed-in server's servertable (era_sql), in one
 * query of their paths; the first answer for a row is its title, the card's before the server's. */
typedef struct {
    void *list;
    int n;
} fav_ask;
static int untitled(void *r) {
    const char *t = P(r, REC_TITLE);
    return I(r, REC_TYPE) == 8 && !(t && *t) && P(r, REC_PATH);
}
static int fav_title_row(void *ctx, int cols, char **v, char **names) {
    const fav_ask *a = ctx;
    if (cols < 3 || !v[0] || !v[2] || !*v[2]) return 0;
    int cue = v[1] ? atoi(v[1]) : 0;
    for (int i = 0; i < a->n; ++i) {
        void *r = deque_at(a->list, (unsigned)i);
        char **title = (char **)((char *)r + REC_TITLE), *t;
        if (untitled(r) && I(r, REC_CUE_START) == cue && !strcmp(P(r, REC_PATH), v[0]) &&
            (t = strdup(v[2])))
            free(*title), *title = t;
    }
    return 0;
}
static void fav_titles(void *list, int n) {
    unsigned size = 96, at;
    for (int i = 0; i < n; ++i) {
        void *r = deque_at(list, (unsigned)i);
        if (untitled(r)) size += 2 * (unsigned)strlen(P(r, REC_PATH)) + 3;
    }
    char *sql = size > 96 ? calloc(size, 1) : (char *)0;
    if (!sql) return;
    at = (unsigned)tk_snprintf(sql, size,
                               "select fileurl,time,name from songtable where fileurl in (");
    for (int i = 0, k = 0; i < n; ++i) {
        void *r = deque_at(list, (unsigned)i);
        if (!untitled(r)) continue;
        if (k++) sql[at++] = ',';
        sql[at++] = '\'';
        for (const char *c = P(r, REC_PATH); *c; ++c) /* a quote doubled, as SQL writes it */
            if ((sql[at++] = *c) == '\'') sql[at++] = '\'';
        sql[at++] = '\'';
    }
    sql[at++] = ')', sql[at] = 0;
    fav_ask ask = { list, n };
    era_sql(sql, fav_title_row, &ask);
    free(sql);
}
/* Playlists bypass songtable; filter their displayed copies, never their stored rows (Cover
 * Flow's turned playlist too). */
int music_records(void *list, int discovery) {
    void *music = _create_deque("stSongInfo");
    deque_init(music);
    for (unsigned i = 0; i < deque_size(list); ++i) {
        void *r = deque_at(list, i);
        const char *path = P(r, REC_PATH);
        int hidden = discovery ? library_scope_hidden(path) : spoken(path);
        if (I(r, REC_TYPE) != 8 || !hidden) _deque_push_back(music, r);
    }
    deque_clear(list);
    deque_assign(list, music);
    deque_destroy(music);
    return (int)deque_size(list);
}
/* Library -> Songs (load_localclass_list's default): stock reads into the staging deque and copies
 * that to the showlist, the library held twice; this reads straight into the showlist. */
static int songs_sorted(void *unused) { return getAllMusic(I(g_sort_flag, 0)); }
static int songs_load(void) {
    void *list = P(p_deque_showlist, 0);
    deque_clear(list);
    staged_into(list, songs_sorted, 0);
    song_order(list);                        /* the Sort picker's order (song_sort.c) */
    deque_clear(P(tools_pdeq_directory, 0)); /* as stock leaves it */
    music_nagvation_title(0xf001);
    return (int)deque_size(list);
}

static int albums_query(void *unused) { return getAllAlbum(); }
static int artists_query(void *unused) {
    return I(g_artist_type, 0) ? GetAllAlbumArtist() : getAllArtist();
}
/* name_order.c's load ahead: list cls's query into into, as ringnav_localclass's open runs it
 * (the server's songs beside the card's, the artist tag the artist page uses). */
void library_warm(unsigned cls, void *into) {
    int saved = library_music;
    library_music = 1;
    I(g_artist_type, 0) = I(artist_type, 0) == 1;
    staged_into(into,
                cls == NO_SONGS    ? songs_sorted
                : cls == NO_ALBUMS ? albums_query
                                   : artists_query,
                0);
    library_music = saved;
}

/* The step a late UI pass names while a Library list loads (ui_watch.c). */
static const char *class_step(int cls) {
    switch (cls) {
    case 0xf001:
        return "songs list";
    case CLASS_ALBUMS:
        return "albums list";
    case 0xf004:
        return "artists list";
    case 0xf005:
        return "composers list";
    case 0xf006:
        return "genres list";
    default:
        return "library list";
    }
}
int ringnav_localclass(int cls) {
    ui_watch_step(class_step(cls));
    I(g_artist_type, 0) = I(artist_type, 0) == 1;
    int saved = library_music;
    library_music = 1;
    name_order_list(cls == 0xf001         ? NO_SONGS
                    : cls == CLASS_ALBUMS ? NO_ALBUMS
                    : cls == 0xf004       ? NO_ARTISTS
                                          : 0);
    int n = cls == 0xf001 ? songs_load() : stock_localclass_trampoline(cls);
    name_order_list(0);
    void *list = P(p_deque_showlist, 0), *dir = P(tools_pdeq_directory, 0), *a = album_opened();
    if (n > 0 && cls == 0xff20) n = music_records(list, 0);
    /* These stored lists bypass songtable too. Keep Discovery out of their displayed copies,
     * without removing favourites/history or changing explicit playlist and folder queues. */
    if (n > 0 && (cls == 0xf00a || cls == 0xf008 || cls == 0xf007)) n = music_records(list, 1);
    if (n > 0 && cls == 0xff10 && a) album_only(list, a, 0), n = (int)deque_size(list);
    if (n > 0 && cls == CLASS_ALBUMS) n = coverflow_split(list);
    if (n > 0 && (cls == 0xf00a || cls == 0xf008 || cls == 0xf007)) /* My Fav, Frequent, Recent */
        fav_titles(list, n);
    /* an untagged song's row reads its file name without the extension (track_name). Bounded by
     * list's own size: Playlists (0xf00b) counts p_deque_playlist's rows, Baidu's (0xff26) its own.
     */
    for (int i = 0, k = (int)deque_size(list); i < n && i < k; ++i) {
        void *r = deque_at(list, (unsigned)i);
        char buf[512], **title = (char **)((char *)r + REC_TITLE), *t;
        if (I(r, REC_TYPE) != 8 || (*title && **title) ||
            !(t = strdup(track_name(buf, sizeof buf, r) ?: "")))
            continue;
        free(*title), *title = t;
    }
    if (cls < CLASS_ALBUMS || cls > 0xf006) {
        library_music = saved;
        return n;
    }
    if (n > 0 && I(deque_at(list, (unsigned)n - 1), REC_ID) == -1) {
        library_first = 1; /* a song is enough: its query stops at the first (library_query) */
        class_query((unsigned)cls, deque_at(list, (unsigned)n - 1));
        library_first = 0;
        int empty = !deque_size(dir);
        deque_clear(dir);
        if (empty) deque_pop_back(list), n--;
    }
    library_music = saved;
    if (n <= 0) { /* say so, not a black page; once the page that loads it is up */
        st.empty_kind = cls - CLASS_ALBUMS;
        timer_add(empty_class, (void *)0, 0);
    }
    return n;
}

static int dir_query(void *path) { return toolsLoadDirectory((const char *)path); }
/* A folder's songs and, in stock's listing order, its subfolders' (dot folders such as .tidal
 * left out), so a folder of albums queues them all.
 * ponytail: one toolsLoadDirectory per folder on the UI thread; a big tree takes a while. */
static void folder_tracks(const char *path, void *add, int depth) {
    int n;
    void *rows = staged(dir_query, (void *)path, &n);
    for (unsigned i = 0; i < deque_size(rows); ++i) {
        void *t = deque_at(rows, i);
        const char *name = P(t, REC_NAME);
        if (I(t, REC_TYPE) == 8 && !playback_video(t))
            _deque_push_back(add, t);
        else if (I(t, REC_TYPE) == 4 && depth < 8 && name && *name != '.') {
            char sub[1024];
            if ((unsigned)tk_snprintf(sub, sizeof sub, "%s/%s", path, name) < sizeof sub)
                folder_tracks(sub, add, depth + 1);
        }
    }
    deque_destroy(rows);
}

/* The tracks stock would play for that row, in its order; a folder's take in its subfolders. */
static void qm_tracks(void *r, void *add) {
    int n;
    if (st.qm_kind == QM_FOLDER) {
        char path[1024];
        if ((unsigned)tk_snprintf(path, sizeof path, "%s/%s", (const char *)g_folder_path,
                                  (const char *)P(r, REC_NAME)) >= sizeof path)
            return; /* cut: a prefix is another folder's path, so nothing, as folder_tracks does */
        ringnav_wdt_long();
        folder_tracks(path, add, 0);
        return;
    }
    void *rows = staged_music(qm_query, r, &n);
    if (st.qm_kind == QM_PLAYLIST) music_records(rows, 0); /* as its opened list (0xff20) */
    for (unsigned i = 0; i < deque_size(rows); ++i) {
        void *t = deque_at(rows, i);
        if (I(t, REC_TYPE) == 8) _deque_push_back(add, t);
    }
    deque_destroy(rows);
}

/* The row's tracks: the song itself, or what stock would play for the row; an Albums row's own
 * album alone (getMusicByAlbum finds every album of the name), in album order, as Coverflow's. */
void *qm_collect(void *r) {
    if (st.qm_kind == QM_COVERALBUM || (st.qm_kind == QM_ALBUM && st.qm_cls == CLASS_ALBUMS))
        return coverflow_album_tracks(r);
    void *add = _create_deque("stSongInfo");
    deque_init(add);
    if (st.qm_kind == QM_SONG || st.qm_kind == QM_COVERFLOW || st.qm_kind == QM_PLAYING) {
        if (!playback_video(r)) _deque_push_back(add, r);
    } else
        qm_tracks(r, add);
    return add;
}

/* Play Next (next 1) at pos+1, or Play Last (2) at the end of Playing Next; playback_queue_insert
 * keeps every queue index right. Shuffle picks at random, so Play Next's first track is forced
 * once (ringnav_shuffle). */
static void qm_insert(void *queue, void *add, int next) {
    unsigned at = playback_queue_insert(queue, add, next);
    if (next == 1) {
        /* stock Shuffle's only: advanced Shuffle Songs (also MCL_MODE 2) plays its own order */
        st.qm_forced = MCL(MCL_MODE) == 2 && !playback_option(0) ? at + 1 : 0;
        st.qm_forced_hash = rec_hash(deque_at(queue, at));
    }
}

/* Up Next (upnext.c), from the hold menu's View Queue, Now Playing's queue button and its swipe up.
 * A shuffle's play order is not the list's, so stock's Shuffle is given one first
 * (playback_listable), and a Play Next it was holding (ringnav_shuffle) goes first in it, as
 * advanced play has no forced pick. A timer's: out of the dispatch that asked. */
int np_queue(const void *unused) {
    (void)unused;
    if (playback_listable()) {
        void *queue = P(mcl_pdeqplaylist, 0);
        unsigned at = st.qm_forced - 1;
        st.qm_forced = 0;
        if (queue && at < deque_size(queue) && rec_hash(deque_at(queue, at)) == st.qm_forced_hash)
            playback_move(at);
    }
    up_next();
    return 0;
}

static int qm_apply(void *add, int next) {
    void *queue = P(mcl_pdeqplaylist, 0);
    if (!queue || airplayGetFlag() == 2) return 0;
    unsigned size = deque_size(queue), type = (unsigned)MCL(MCL_TYPE), n = deque_size(add);
    /* Only a local (folder or library) queue grows; streams keep theirs. */
    if (size && type != 1 && (type & 0xf000) != 0xf000) return 0;
    int album = st.qm_kind == QM_COVERFLOW || st.qm_kind == QM_COVERALBUM;
    if (n && !size) { /* loads without starting playback; only a real folder is class 1 */
        if (album) album_memory(deque_at(add, 0));
        mclLoadPlayList(add, 0, st.qm_kind == QM_FOLDER ? 1 : album ? 0xff10 : (int)st.qm_cls);
    } else if (n)
        qm_insert(queue, add, next);
    return n != 0;
}

/* A 1.5 s modal dialog (oled-design.md's toast): its loop runs every ringnav_sleep poll
 * again, so a poll clears or latches what it toasts first (logo_poll). */
void toast(const char *text) {
    struct {
        int kind, ms;
        char text[0x400];
    } info = { 1, 1500, { 0 } };
    for (unsigned i = 0; text[i] && i < sizeof info.text - 1; ++i) info.text[i] = text[i];
    coda_rc_note(info.text); /* the car shows it too (coda_rc_shim.c) */
    ++st.toasts;             /* its loop runs the UI's polls, which may toast again */
    navigator_to_with_context("dialog/msginfo_dialog", &info);
    --st.toasts;
}

/* Every song in stock's title order: music (LIBRARY_MUSIC) or every card song (0) or, beside them,
 * the server's (LIBRARY_ALL), as library_music says. */
int all_songs(void *music) {
    int saved = library_music;
    library_music = (int)(long)music;
    int n = getAllMusic(0);
    library_music = saved;
    return n;
}

/* Shuffle does not need the expensive pinyin title sort; groups order their tracks themselves.
 * Non-zero music leaves audiobooks and podcasts out, as the Library's Artists and Albums
 * (MUSIC_WHERE). */
int songs_unsorted(void *music) {
    int saved = library_music;
    library_music = music != 0;
    int n = getAllMusic(3);
    library_music = saved;
    return n;
}

/* Return for the payload's own pages; each passes page_open its own close. */
int page_keyup(void *ctx, void *event) {
    if (I(event, EVENT_KEY) != KEY_RETURN) return 0;
    navigator_back();
    return STOP;
}

/* The close of navigation.c's own page, Chapters. */
static int page_closed(void *ctx, void *event) {
    if (ctx == st.ch_page) {
        st.ch_page = 0;
        if (st.ch && !st.ch->done)
            chapters_cancel(st.ch);
        else {
            free(st.ch);
            st.ch = 0;
        }
    }
    return 0;
}

/* Places and play counts, on the card with the songs they are of (card_store.c,
 * docs/internals.md#where-q2-muse-writes); the flash files earlier releases wrote are read until
 * the card has a copy. */
#define RESUME_FILE CARD_STORE_DIR "/ringnav-resume"
#define PLAYS_FILE CARD_STORE_DIR "/ringnav-plays"
static card_blob resume_store __attribute__((section(".scratch"))),
    plays_store __attribute__((section(".scratch")));
#define RESUME_STORE                                                                               \
    card_blob_at(&resume_store, RESUME_FILE, "/mnt/data/ringnav-resume", sizeof st.spots)
#define PLAYS_STORE                                                                                \
    card_blob_at(&plays_store, PLAYS_FILE, "/mnt/data/ringnav-plays", sizeof st.plays)
