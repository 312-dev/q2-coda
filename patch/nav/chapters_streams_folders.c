/* key's saved place, 0 for none. */
static int spot_at(unsigned key) {
    if (!st.spots_read) {
        st.spots_read = 1;
        int n = card_blob_read(RESUME_STORE, st.spots, sizeof st.spots);
        if (n != (int)sizeof st.spots && n != 32 * (int)sizeof *st.spots) /* earlier releases kept 32 */
            memset(st.spots, 0, sizeof st.spots);
    }
    for (int i = 0; i < RESUME_SLOTS; i++)
        if (st.spots[i].key == key) return st.spots[i].sec;
    return 0;
}

void plays_load(void) {
    if (!st.plays_read) {
        st.plays_read = 1;
        if (card_blob_read(PLAYS_STORE, st.plays, sizeof st.plays) != (int)sizeof st.plays)
            memset(st.plays, 0, sizeof st.plays);
    }
}

/* A play count's key: the path's hash, with a CUE track's start mixed in, since a CUE image's
 * tracks share one path. Never 0, which marks a free slot. */
unsigned listen_key(void *r) {
    unsigned key = fnv(FNV_SEED, P(r, REC_PATH));
    int cue = I(r, REC_CUE_START);
    if (cue) key = hash_bytes(key, (const unsigned char *)&cue, sizeof cue);
    return key | !key;
}

/* Chapters are read only while this snapshot is open: an M4B's, or a spoken file's in any format
 * ffmpeg reads them from, such as an MP3 book's ID3 chapters. A spoken CUE track other than an
 * M4B's has none, as before: its sheet lists its chapters. An M4B CUE track's rows are moved to
 * its own clock in chapter_poll. */
static int chapter_file(void *r) {
    const char *path = P(r, REC_PATH), *ext = path ? strrchr(path, '.') : 0;
    if (ext && !strcasecmp(ext, ".m4b")) return 1;
    return spoken(path) && !I(r, REC_CUE_START) && !I(r, REC_CUE_END);
}
static int chapter_pick(void *ctx, void *event) {
    unsigned i = (unsigned)(long)ctx;
    if (st.ch && st.ch->done && !st.ch->failed && i < (unsigned)st.ch->count &&
        np_track() == st.ch_track && (mclGetPlayStatus() == 2 || mclGetPlayStatus() == 3)) {
        /* Suppress even a resume poll that has not yet settled this track. */
        unsigned at, n;
        void *r = queue_now(&at, &n);
        st.ch_key = listen_key(r);
        st.rs_pending = 0;
        st.rs_go_key = 0;
        st.scrub_track = st.ch_track;
        st.scrub_to = st.ch->rows[i].sec;
        st.scrub = st.scrub_moved = 1;
        if (st.np_win) playing_timer_clear(st.np_win);
        if (st.np_slider) widget_set_prop_int(st.np_slider, "value", st.scrub_to);
        if (st.np_elapsed) clock_text(st.np_elapsed, 0, st.scrub_to);
        scrub_end();
    }
    navigator_back();
    return 0;
}
/* Chapters given up while they load under the overlay (Return, a tap off the spinner, its window
 * gone): ffmpeg ended, and the poll frees them once it is reaped, as a closed page's. */
static void chapters_gone(void) {
    if (st.ch && !st.ch->done) chapters_cancel(st.ch);
}
static int chapter_poll(const void *unused) {
    if (!chapters_poll(st.ch, time_now_ms())) return 8;
    st.ch_timer = 0;
    if (!st.ch_page && load_over_for(chapters_gone)) { /* read under the overlay: the page now */
        st.ch_page = page_open("chapters_page", page_closed, page_keyup);
        load_over(0, 0);
    }
    if (!st.ch_page) {
        free(st.ch);
        st.ch = 0;
        return 0;
    }
    /* ffmpeg's times are the file's; the player's clock and seek start at a CUE track's start. Rows
     * move to the track's clock: the chapter playing at its start shows at 0:00, earlier ones go,
     * and so do those from its end on. Untitled rows keep the file's numbering. */
    int n = 0;
    for (int i = 0; !st.ch->failed && i < st.ch->count; ++i) {
        chapter *c = &st.ch->rows[i];
        int sec = c->sec - st.ch_cue;
        if (st.ch_end && c->sec >= st.ch_end) break;
        if (!*c->title) tk_snprintf(c->title, sizeof c->title, "Chapter %d", i + 1);
        if (st.ch_cue && sec <= 0) {
            n = 0;
            sec = 0;
        }
        if (n != i) st.ch->rows[n] = *c;
        st.ch->rows[n++].sec = sec;
    }
    st.ch->count = n;
    static const empty_t none[] = {
        { "list-numbers", "No Chapters", "This file has no chapter marks", 0, 0, 0 },
        { "list-numbers", "No Chapters", "This file's chapters couldn't be read", 0, 0, 0 },
    };
    void *view = n ? page_list(st.ch_page, st.ch_page, 0, "Chapters", n, 64)
                   : empty_list(st.ch_page, st.ch_page, "Chapters", &none[!!st.ch->failed], 0);
    int sec = 0, total, current = 0;
    player_playtime_and_length(&sec, &total);
    for (int i = 0; i < n; ++i) {
        char at[32];
        chapter *c = &st.ch->rows[i];
        tk_snprintf(at, sizeof at, "%d:%02d:%02d", c->sec / 3600, c->sec / 60 % 60, c->sec % 60);
        page_row_detail(view, i, c->title, at, chapter_pick);
        if (c->sec <= sec) current = i;
    }
    if (n) ringnav_select(view, current, n);
    return 0;
}
static void chapters_open(void *r) {
    if (st.ch || !chapter_file(r)) return;
    if (!mp4_fits(P(r, REC_PATH))) {
        toast("Too long to read chapters on the Q2");
        return;
    }
    st.ch_track = np_track();
    st.ch_cue = I(r, REC_CUE_START);
    st.ch_end = I(r, REC_CUE_END);
    st.ch = chapters_start(P(r, REC_PATH), time_now_ms());
    if (!st.ch) {
        toast("Could not read chapters");
        return;
    }
    /* the page opens once they are read, the loading overlay over the one the menu was held on
     * (its sheet may still be falling away); at once, with its Loading… page, with none */
    void *from = page_in_use();
    if (from && tk_strcmp(widget_get_type(from), "window"))
        from = window_under(window_manager(), from);
    if (!from) st.ch_page = page_open("chapters_page", page_closed, page_keyup);
    st.ch_timer = timer_add(chapter_poll, 0, 50);
    if ((!from && !st.ch_page) || !st.ch_timer) {
        /* No polling timer: kill and reap the child before releasing its storage. */
        chapters_cancel(st.ch);
        if (st.ch->pid > 0) {
            int status;
            while (waitpid(st.ch->pid, &status, 0) < 0 && *__errno_location() == 4) {}
        }
        free(st.ch);
        st.ch = 0;
        if (st.ch_page) navigator_back();
        stop_timer(&st.ch_timer);
        return;
    }
    if (st.ch_page)
        page_list(st.ch_page, st.ch_page, 0, "Loading chapters…", 0, 64);
    else
        load_over(from, chapters_gone);
}

/* 1 + the MEDIA folder s names (up to its end or a '/'), 0 for none. MEDIA is letters only. */
static int media_kind(const char *s) {
    for (int k = 0; k < VIDEOS; k++) {
        unsigned n = strlen(MEDIA[k]);
        if (!strncasecmp(s, MEDIA[k], n) && (!s[n] || s[n] == '/')) return k + 1;
    }
    return 0;
}

/* Folder view lists, in the card's Videos folder, its videos (playback.c ringnav_audio_extension):
 * stock lists audio files only, so their rows never reached ringnav_folder_click. */
int folder_videos(void) {
    const char *p = (const char *)g_folder_path;
    return tk_str_start_with(p, "/mnt/mmc/") && media_kind(p + 9) == VIDEOS;
}

/* A track in Podcasts or Audiobooks: always resumed, never counted or scrobbled. Shared (peq.h). */
int spoken(const char *path) {
    const char *ext = path ? strrchr(path, '.') : 0;
    return (ext && !strcasecmp(ext, ".m4b")) ||
           (path && tk_str_start_with(path, "/mnt/mmc/") && media_kind(path + 9) - 1u < AUDIOBOOKS);
}

/* A spoken CUE track's book: the chapter of list (r's file's, the last starting at or before the
 * saved place) to resume, its index among list's songs (*at) and the seconds into it (*off); 0
 * without a saved place. */
static void *book_place(void *list, void *r, int *at, int *off) {
    if (!list || !r || !(I(r, REC_CUE_START) || I(r, REC_CUE_END)) || !spoken(P(r, REC_PATH)))
        return 0;
    unsigned key = fnv(FNV_SEED, P(r, REC_PATH));
    int place = spot_at(key), j = 0;
    void *best = 0;
    for (unsigned i = 0, n = deque_size(list); place && i < n; i++) {
        void *t = deque_at(list, i);
        if (I(t, REC_TYPE) != 8) continue;
        if (fnv(FNV_SEED, P(t, REC_PATH)) == key && I(t, REC_CUE_START) <= place &&
            (!best || I(t, REC_CUE_START) > I(best, REC_CUE_START)))
            best = t, *at = j;
        j++;
    }
    if (best) *off = place - I(best, REC_CUE_START);
    return best;
}

/* The card's folder for kind into path (sizeof st.media_root, which a MEDIA name always fits); 0
 * when there is none. */
int media_find(int kind, char *path) {
    void *dir = opendir("/mnt/mmc");
    int found = 0;
    for (struct dirent *e; dir && !found && (e = readdir(dir));)
        if ((e->d_type == 4 || !e->d_type) && media_kind(e->d_name) == kind)
            found = tk_snprintf(path, sizeof st.media_root, "/mnt/mmc/%s", e->d_name) > 0;
    if (dir) closedir(dir);
    return found;
}

/* A Local Music row's (Podcasts, Audiobooks): the folder found again, as the card may have
 * changed since; without it, a toast. */
int media_click(void *ctx, void *event) {
    int kind = (int)(long)ctx;
    char root[sizeof st.media_root], t[48];
    if (!media_find(kind, root)) {
        tk_snprintf(t, sizeof t, "No %s folder on the card", MEDIA[kind - 1]);
        toast(t);
    } else {
        memcpy(st.media_root, root, sizeof root);
        st.media_open = 1;
        navigator_to("folder_page");
    }
    return 0;
}

/* Go to Folder: a card song's folder into path (sizeof st.go_folder) and its g_folder_layer, as
 * folder_enter counts (the card 2), so stock Back climbs to the card; 0 when off the card, too
 * long, holding a name stock's enter never builds ("", "." or "..") or no longer a directory. */
static int song_folder(void *r, char *path) {
    const char *p = P(r, REC_PATH);
    unsigned info[36], layer = 2;
    if (!p || strlen(p) <= 9 || memcmp(p, "/mnt/mmc/", 9)) return 0;
    unsigned n = (unsigned)(strrchr(p, '/') - p);
    if (n >= sizeof st.go_folder) return 0;
    memcpy(path, p, n);
    path[n] = 0;
    for (unsigned i = 8, j; i < n; i = j) { /* path[i] is the '/' before a name */
        for (j = i + 1; j < n && path[j] != '/'; ++j) {}
        unsigned len = j - i - 1;
        if (!len || (path[i + 1] == '.' && (len == 1 || (len == 2 && path[i + 2] == '.'))) ||
            ++layer > 255) /* g_folder_layer is a byte */
            return 0;
    }
    return !__xstat(3, path, info) && (info[5] & 0170000) == 0040000 ? (int)layer : 0;
}

/* folder_page_init lists the storage roots at g_folder_layer 1, a root's folders at 2 and deeper
 * folders at 3 on (folder_enter). Videos browse here too, folder by folder. A media root becomes a
 * layer-3 folder, so stock's reload, title (its name, folder_reinit_navbarname) and entering all
 * treat it as one. Each open starts a fresh visit (folder_reset_variable): folder_back pops the
 * enter history kept for each layer and leaves an M3U before stepping up, and a visit left without
 * Back (Home, playing) keeps both. */
int ringnav_folder(void *win, void *ctx) {
    int result = stock_folder_trampoline(win, ctx), layer = 3;
    char root[265];
    const char *path = st.media_root;
    if (!st.media_open || !win) {
        int card = win && !access("/tmp/mmc_add", 0);
        st.media_root[0] = 0;
        /* No drive and a card (stock's check_tfstatus): the card is the only root, so open it. */
        if (card && !g_otg_status) memcpy(st.media_root, "/mnt/mmc", 9), layer = 2;
        /* Folder Root: an ordinary folder below the card, so Back walks up to it, then as above. */
        if (card && root_dir(root)) path = root, layer = 3;
        /* Go to Folder, checked as it was picked (song_folder) */
        if (card && st.go_layer) path = st.go_folder, layer = st.go_layer;
    }
    if (path[0]) {
        folder_reset_variable();
        memcpy((char *)g_folder_path, path, strlen(path) + 1);
        g_folder_layer = layer;
        folder_reload_data();
        folder_reinit_navbarname();
        folder_refresh(win);
    }
    st.media_open = st.go_layer = 0;
    return result;
}

/* folder_load_top: stock lists SD and OTG whatever is plugged in, and OTG alone opens a full
 * page saying there is none; without a drive (g_otg_status) the card is the only root. */
void ringnav_folder_top(void) {
    stock_folder_top_trampoline();
    void *roots = P(p_deque_showlist, 0);
    if (!g_otg_status && deque_size(roots) == 2) deque_pop_back(roots);
}

/* A folder level's list, stock's table on the open page, pushes in as a page_list level does. */
static void folder_push(int dir) {
    if (!motion_row(MOTION_INPLACE)->ms) return;
    void *table =
        widget_lookup(window_manager_get_top_window(window_manager()), "tableview_folder", 1);
    if (table) page_push(widget_get_child(table, 0), dir);
}

/* Back at a shortcut or bypassed SD root (media_root, each its own layer) leaves the page (stock's
 * layer 0). */
int ringnav_folder_back(void *yoffset, void *index) {
    if (st.media_root[0] && !tk_strcmp((const char *)g_folder_path, st.media_root)) {
        g_folder_layer = 1;
        st.media_root[0] = 0;
    }
    int layer = g_folder_layer, result = stock_folder_back_trampoline(yoffset, index);
    if (g_folder_layer < layer && g_folder_layer) folder_push(-1);
    return result;
}

/* folder_page's row click (stock's, 0x532414: the tapped or Centre-pressed row's index into
 * p_deque_showlist, then folder_enter, whose 8, a file, it queues as songs): a video plays
 * (videos.c video_click). Select mode (g_navbar_status) and everything else is stock's.
 */
int ringnav_folder_click(void *ctx, void *event) {
    void *row = event && !g_navbar_status ? table_row_of(P(event, EVENT_TARGET)) : (void *)0;
    void *list = P(p_deque_showlist, 0);
    unsigned i = row ? (unsigned)I(row, ROW_INDEX) : ~0u;
    if (i < deque_size(list)) {
        void *r = deque_at(list, i);
        if (I(r, REC_TYPE) == 8 && video_click(P(r, REC_PATH))) return 0;
    }
    int layer = g_folder_layer, result = stock_folder_click_trampoline(ctx, event);
    if (g_folder_layer > layer) folder_push(1);
    return result;
}

/* usbmode_page_init, as a cable to a computer opens USB mode, before stock unmounts the card for
 * USB storage: the card apps, which write to it (downloads, uploads), end first, so nothing of
 * theirs keeps it busy (they run from /tmp: addon.c), as do Get Album Lyrics' job, playlist
 * art's worker and the song durations' (durations.c). */
int ringnav_usbmode(void *a, void *b, void *c, void *d) {
    addon_card_away();
    album_lyrics_stop();
    playlist_art_stop();
    durations_shutdown(0);
    return stock_usbmode_trampoline(a, b, c, d);
}

/* mclStartPlayer, every local track's start: a playing librespot or radio station lets the DAC's
 * PCM go first, a hold on the Library's place (now_source.c now_hold) ends, and where the song
 * starts is noted for Now Playing until it is heard (now_source.c now_starting). A track too long
 * for RAM fails as stock's own checks do, with -1; so, silently, does any start while a game has
 * the sound (addon.c addon_game_audio: a card game's, Caller Ten's contest): a jack or USB DAC
 * change and a Bluetooth drop come here through player_save_start, and the game keeps its PCM; and
 * so does player_start's replay of the queue it had after a press that loaded nothing (a server
 * row, playback.c playback_start_refused). */
int ringnav_start_player(void) {
    if (playback_start_refused()) return -1;
    if (addon_game_audio()) return -1;
    if (!playback_start_fits()) return -1;
    headset_manual();
    spot_yield();
    radio_stop();
    now_drop();     /* the player's own place from now on, not a hold's (now_source.c) */
    now_starting(); /* where it starts, read until it is heard (now_source.c) */
    playback_track_speed();
    return stock_start_player_trampoline();
}

/* systemset_about_page_init: stock's seven rows (0x4c0de4) and its thanks (about_more, a 100 px
 * list_item of wrapped text, the eighth child, dropped here: stock builds it and never reads it
 * again), each row a s_listitem_black list_item holding a 335x70 s_btn_listitem button, a 166px
 * s_scrlabel_white24l title at x 10 and a 149px s_scrlabel_white20r value at x 176, both focus-only
 * scrolling with ellipses. Firmware (row 1, stock's FW. Version, tools/build.py LABELS) reads
 * demo's version literal, which carries the updater tag (tools/build.py TAG), so it shows the stock
 * firmware's again; a row named PRODUCT_NAME, this build's version, in the same widgets follows it.
 * Its button has no name, so stock's row click (atoi of the name, 0x4c12f8) ignores it. A --dev
 * build adds Save Diagnostics above them (diagnostics.c). */
/* A row's value in the approved words (audit-2 #31): stock's sizes "779.72G" with their unit,
 * "779.72 GB", and the IP address of no network, "0.0.0.0", as "Not connected". */
static void about_words(void *item) {
    void *b = widget_count_children(item) ? widget_get_child(item, 0) : (void *)0;
    void *val = b && widget_count_children(b) > 1 ? widget_get_child(b, 1) : (void *)0;
    char t[48];
    if (!val || tk_strcmp(widget_get_type(val), "hscroll_label")) return;
    *t = 0;
    widget_get_text_utf8(val, t, sizeof t - 2);
    unsigned n = strlen(t);
    if (n > 1 && (t[n - 1] == 'G' || t[n - 1] == 'M') && t[n - 2] >= '0' && t[n - 2] <= '9') {
        t[n + 1] = 'B', t[n + 2] = 0, t[n] = t[n - 1], t[n - 1] = ' ';
        widget_set_text_utf8(val, t);
    } else if (!tk_strcmp(t, "0.0.0.0")) {
        widget_set_text_utf8(val, "Not connected");
    }
}
static void about_label(void *button, int x, int w, const char *style, const char *text) {
    void *label = hscroll_label_create(button, x, 0, w, 70);
    widget_use_style(label, style);
    hscroll_label_set_only_focus(label, 1);
    hscroll_label_set_ellipses(label, 1);
    widget_set_text_utf8(label, text);
    theme_font(label, x < 100 ? ROLE_TITLE : ROLE_META, FONT_ROW);
}

int ringnav_about(void *win, void *ctx) {
    int result = stock_about_trampoline(win, ctx);
    void *view = win ? widget_lookup(win, "scroll_view_about", 1) : (void *)0;
    if (view && widget_count_children(view) == 8) {
        widget_destroy(widget_get_child(view, 7)); /* stock's thanks */
        widget_set_text_utf8(widget_get_child(widget_get_child(widget_get_child(view, 1), 0), 1),
                             STOCK_VERSION);
        void *button = list_button(view);
        char v[sizeof Q2POD_VERSION];
        const char *pod = strstr(Q2POD_VERSION, " Muse");
        tk_snprintf(v, sizeof v, "%s", Q2POD_VERSION);
        if (pod) /* "1.8.2" then its " dev ..." or " beta 2": the row's name is the product's */
            tk_snprintf(v + (pod - Q2POD_VERSION), sizeof v - (pod - Q2POD_VERSION), "%s", pod + sizeof " Muse" - 1);
        about_label(button, 10, 166, "s_scrlabel_white24l", PRODUCT_NAME);
        about_label(button, 176, 149, "s_scrlabel_white20r", v);
        widget_restack(P(button, W_PARENT), 2);
        for (unsigned i = 0, n = widget_count_children(view); i < n; ++i)
            about_words(widget_get_child(view, i));
        diag_about_row(view);
    }
    return result;
}

