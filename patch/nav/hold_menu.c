/* Go to album/artist reuse g_local_classinfo_save, which the pages underneath reload from when
 * they come back, so it is put back as the page opened here closes. */
static int qm_restore(void *page, void *event) {
    memcpy((void *)g_local_classinfo_save, st.qm_classinfo, sizeof st.qm_classinfo);
    return 0;
}

/* Now Playing's More rows, for this record: Album info (0x5397cc) fills the album query and opens
 * playerjumpinfo_page; Artist info opens artistinfo_page with {class, record}. A song played in
 * Coverflow, or with Artist View on Cover Flow or List, goes to the album's tracks in Coverflow
 * instead; album QM_LOOKUP_ALBUM (Now Playing's album and art taps,
 * np_goto) keeps to playerjumpinfo_page, as the ⋯ page's Look up Album does. A card app's song goes
 * to the app's page when it offers one (addon.c addon_goto). */
#define QM_LOOKUP_ALBUM 2
static void qm_goto(void *r, int album) {
    unsigned char *info = (unsigned char *)g_local_classinfo_save;
    void *wm = window_manager(), *page = window_manager_get_top_window(wm);
    /* a card app's song (Qobuz's): the app's own page for its album or artist, when it says so */
    if (addon_goto(P(r, REC_PATH), P(r, album ? REC_ALBUM : REC_ARTIST), album)) return;
    if (album == 1 && (navigator_window_is_exist("coverflow_page") || ringnav_artist_view()) &&
        coverflow_goto_album(r))
        return;
    memcpy(st.qm_classinfo, info, sizeof st.qm_classinfo);
    if (album) {
        info[9] = 0;
        tk_snprintf((char *)info + 0xd, 0x100, "%s", (const char *)P(r, REC_ALBUM));
        tk_snprintf((char *)info + 0x10d, 0x100, "%s", (const char *)P(r, REC_ARTIST));
        *(int *)info = 0xff10;
        album_open(r);
        navigator_to("playerjumpinfo_page");
    } else {
        struct {
            int cls;
            void *r;
        } context = { (int)st.qm_cls, r };
        navigator_to_with_context("localmusic/artistinfo_page", &context);
    }
    void *top = window_manager_get_top_window(wm);
    if (top != page) widget_on(top, EVT_WINDOW_CLOSE, qm_restore, top);
}

/* A Library artist's album (library_artist.c): its album page, as Look up Album opens it. */
void album_page_open(void *r) { qm_goto(r, QM_LOOKUP_ALBUM); }

/* Go to Album (album) or Go to Artist has somewhere to go for r: it names one, and its
 * single-instance page is not open already (nor Cover Flow showing that album alone). */
static int qm_goes(void *r, int album) {
    const char *name = P(r, album ? REC_ALBUM : REC_ARTIST);
    if (!name || !*name) return 0;
    return album ? !navigator_window_is_exist("playerjumpinfo_page") && !coverflow_shows_album(r)
                 : !navigator_window_is_exist("artistinfo_page");
}

/* Now Playing's taps on its album or art (album) and artist (np_taps.c): the playing song's album
 * as the ⋯ page's Look up Album opens it (QM_LOOKUP_ALBUM), or Go to Artist (the artist's page, as
 * Look up Artist), where the hold menu would offer that row (qm_hold's and
 * qm_open's gates: a local or Library queue, not AirPlay, a card app's song only to its app's
 * page), so a stream goes nowhere. With go 0, only whether it would; 1 when it went or would. This
 * is the hold menu's code: its record checks and qm_goto live here. */
int np_goto(int album, int go) {
    void *wm = window_manager();
    unsigned at, n, type = (unsigned)MCL(MCL_TYPE);
    if (!st.np_win || window_manager_get_top_window(wm) != st.np_win || st.qm_dialog ||
        st.qm_timer || !usable() || window_manager_is_animating(wm) || airplayGetFlag() == 2 ||
        g_navbar_status || (type != 1 && (type & 0xf000) != 0xf000))
        return 0;
    void *r = queue_now(&at, &n);
    if (!r || !addon_goes(P(r, REC_PATH)) || !qm_goes(r, album)) return 0;
    if (!go) return 1;
    st.qm_cls = 0xf001; /* Go to Artist's class, as the hold's on Now Playing */
    qm_goto(r, album ? QM_LOOKUP_ALBUM : 0);
    return 1;
}

/* Deferred so the dialog is never closed under its own click dispatch. */
static int qm_open(const void *unused);

/* A song's My Fav, its like, changed from the menu, kept as Now Playing's heart keeps it
 * (ratings.c rate_fav_click): its stars stay, the card's copy of the ratings is due, and the server
 * is told (catalogue_liked, by the row's key and sid for a server song's Stream Cache copy). */
static void qm_liked(void *r, int on) {
    ratings_copy_due();
    catalogue_liked(r, on);
}

static int qm_run(const void *unused) {
    st.qm_timer = 0;
    if (!st.qm_dialog) return 0;
    /* the menu sheet's Group by, two choices, flips in place (audit2 L3) */
    if (st.qm_action == QA_GROUP) {
        if (!playback_set(2, !playback_option(2))) toast("Queue unchanged");
        qm_values(st.qm_dialog);
        return 0;
    }
    qm_close();
    void *r = qm_record();
    int a = st.qm_action, cls = st.qm_kind == QM_COVERFLOW ? 0xf001 : (int)st.qm_cls;
    unsigned aid = r ? qm_album_id(r) : 0; /* an album's own Favourites */
    /* A start with nothing to play to: stock's message, and nothing set first (the queue's class,
     * a chapter's seek, Shuffle's mode), as Now Playing is refused (output_rule.c
     * ringnav_nav_to) */
    if (r && (a == QA_PLAY || a == QA_SHUFFLE || a == QA_RESUME) && check_forbid_play_no_headset(0))
        return 0;
    if (r && a >= 100) {
        if (!playback_set(st.qm_choice - 1, a - 100)) toast("Queue unchanged");
        st.qm_choice = 0;
    } else if (a == QA_OFFLINE) { /* the item worked out for its row (offline.c) */
        offline_press();
    } else if (r && a == QA_CHAPTERS) {
        chapters_open(r);
    } else if (r && a == QA_QUEUE) {
        np_queue(0);
    } else if (a == QA_MORE) {
        void *more = np_more(); /* clicked as a tap would, so stock's own handler opens it */
        char click[0x30];
        if (more && !addon_more())
            stock_dispatch_trampoline(more, pointer_event_init(click, EVT_CLICK, more, 0, 0));
    } else if (r && (a == QA_NEXT_GROUP || a == QA_PREV_GROUP)) {
        if (!playback_group_skip(a == QA_NEXT_GROUP)) toast("No other group");
    } else if (aid && (a == QA_FAV || a == QA_UNFAV)) {
        if (rate_saved(rate_keep(1, aid, a == QA_FAV ? 5 : 0))) rate_album_favs(r, a == QA_FAV);
    } else if (r && a >= QA_ADV_SHUFFLE && a <= QA_GROUP) {
        st.qm_choice = a - QA_ADV_SHUFFLE + 1;
        qm_open(0);
    } else if (r && a == QA_FAV) { /* tags a folder file as batch-select's Add to My Fav does */
        qm_select();
        batch_add_file(cls, 0xf00a, qm_list(), P(p_vector_select_record, 0), 0);
        qm_liked(r, 1);
        if (fav_told(r, 1)) toast("Added to Favorites");
    } else if (r && a == QA_UNFAV) {
        deleteMusicFromFav(r);
        catalogue_row_unfav(r); /* a server song's Stream Cache copy: its row's too (rate_fav) */
        qm_liked(r, 0);
        if (fav_told(r, 0)) toast("Removed from Favorites");
        if (cls == 0xf00a)
            g_delete_flag = 1; /* My Fav reloads, as Now Playing's heart (0x53cafc) */
    } else if (r && a == QA_PLAYLIST && st.qm_kind != QM_PLAYING) {
        playlist_pick_open(r); /* the menu sheet's picker (playlists.c) */
    } else if (r && a == QA_PLAY) {
        void *add = qm_collect(r);
        if (add && deque_size(add)) { /* as its own list plays: Delete takes from the playlist */
            I(g_local_classinfo_save, 4) = I(r, REC_DISC);
            play_folder(add, 0, 0xff13);
        } else
            toast("Queue unchanged");
        if (add) deque_destroy(add);
    } else if (r && a == QA_REMOVE) {
        if (qm_held.pick) qm_held.pick(); /* its page asks first (playlist_pages.c) */
    } else if (r && a == QA_RENAME) {
        playlist_rename_open(I(r, REC_DISC), P(r, REC_TITLE));
    } else if (r && a == QA_DELETE) {
        char ask[200];
        tk_snprintf(ask, sizeof ask, "Delete playlist \u201c%s\u201d?",
                    (const char *)P(r, REC_TITLE));
        if (playlist_confirm(ask, "Delete")) {
            int ok = playlist_delete(I(r, REC_DISC));
            if (ok) g_delete_flag = 1;
            toast(ok ? "Playlist deleted" : "Could not delete playlist");
        }
    } else if (r && a == QA_OTG) {
        int added = playlist_add(r, ON_THE_GO);
        toast(added > 0 ? "Added to On-The-Go"
              : added   ? "Could not save playlist"
                        : "Already in On-The-Go");
    } else if (r && a == QA_PLAYLIST) { /* the playlist page's add mode adds the selected row */
        qm_select();
        navigator_to_with_context(
            "localmusic/playlist_page",
            (void *)(long)((st.qm_kind == QM_PLAYING ? 0x20000 : 0x10000) | cls));
        if (st.qm_kind == QM_PLAYING) {
            void *top = window_manager_get_top_window(window_manager());
            if (!tk_strcmp(widget_get_prop_str(top, "name", ""), "playlist_page")) {
                st.qm_playlist = top;
                qm_playlist_bind(top, 0, qm_playlist_pick);
                widget_on(top, EVT_DESTROY, qm_playlist_gone, top);
            }
        }
    } else if (r && a == QA_RESUME) { /* the list's songs from the chapter; the poll seeks */
        int at = 0, off = 0;
        void *list = qm_list(), *t = book_place(list, r, &at, &off), *songs;
        if (t && (songs = _create_deque("stSongInfo"))) {
            deque_init(songs);
            for (unsigned i = 0; i < deque_size(list); i++)
                if (I(deque_at(list, i), REC_TYPE) == 8) _deque_push_back(songs, deque_at(list, i));
            st.rs_go_key = listen_key(t);
            st.rs_go = off;
            st.rs_go_at = (unsigned)time_now_ms();
            play_folder(songs, at, 0xf001);
            deque_destroy(songs);
        } else
            toast("Queue unchanged");
    } else if (r && a == QA_FOLDER) {
        st.go_layer = song_folder(r, st.go_folder);
        if (st.go_layer)
            navigator_to("folder_page"); /* its init, ringnav_folder, opens there */
        else
            toast("Folder not found");
        st.go_layer = 0; /* never left for a later Folder visit */
    } else if (r && a >= QA_ALBUM)
        qm_goto(r, a == QA_ALBUM);
    else {
        void *add = r ? qm_collect(r) : (void *)0;
        if (!add || !(a == QA_SHUFFLE ? shuffle_play(add) : qm_apply(add, a == QA_NEXT ? 1 : 2)))
            toast("Queue unchanged");
        else if (a != QA_SHUFFLE) /* OLED says what an in-place action did */
            toast(a == QA_NEXT ? "Playing Next" : "Playing Last");
        if (add) deque_destroy(add);
    }
    return 0;
}

/* Touch and centre both arrive as the row's click; the first one wins. */
static int qm_pick(void *action, void *event) {
    if (st.qm_dialog && !st.qm_timer) {
        st.qm_action = (int)(long)action;
        st.qm_timer = timer_add(qm_run, (void *)0, 0);
    }
    return 0;
}

/* Deferred out of the long-press dispatch. The sortselect init is synchronous and non-modal
 * (it clears to_modal), so the dialog is the top window once navigator_to returns. */
/* The title loops, as Now Playing's; stock's sortselect title doesn't. Its holds at its start and
 * end are every label's (ringnav_hscroll_start, ringnav_hscroll_lull). */
static void qm_title(void *dialog, const char *s) {
    void *title = widget_lookup(dialog, "scrlabel_title", 1);
    widget_set_prop_int(title, "loop", 1);
    widget_set_text_utf8(title, s);
}
/* Now Playing's play modes as stock names them, its category as Group by sets it (album or
 * folder): Shuffle's (kind 0), Repeat's (1) and Group by's (2) choices. */
static int qm_choices(int kind) { return kind == 0 ? 5 : kind == 1 ? 6 : 2; }
static const char *qm_choice_name(int kind, int i) {
    static const char *const shuffle[2][5] = {
        { "Off", "All Songs", "Songs in Album", "Albums", "Albums and Songs" },
        { "Off", "All Songs", "Songs in Folder", "Folders", "Folders and Songs" }
    };
    static const char *const repeat[2][6] = { { "Play One Song", "Play One Album", "Play All",
                                                "Repeat Song", "Repeat Album", "Repeat All" },
                                              { "Play One Song", "Play One Folder", "Play All",
                                                "Repeat Song", "Repeat Folder", "Repeat All" } };
    static const char *const groups[] = { "Album", "Folder" };
    int folder = playback_option(2);
    return kind == 0 ? shuffle[folder][i] : kind == 1 ? repeat[folder][i] : groups[i];
}

/* The same as a menu sheet's setting row shows it, its value alone (audit2 L3): "Songs", "All". */
static const char *qm_value_name(int kind, int i) {
    static const char *const shuffle[2][5] = {
        { "Off", "Songs", "In Album", "Albums", "Albums & Songs" },
        { "Off", "Songs", "In Folder", "Folders", "Folders & Songs" }
    };
    static const char *const repeat[2][6] = {
        { "Stop After Song", "Stop After Album", "Off", "Song", "Album", "All" },
        { "Stop After Song", "Stop After Folder", "Off", "Song", "Folder", "All" }
    };
    int folder = playback_option(2);
    return kind == 0 ? shuffle[folder][i] : kind == 1 ? repeat[folder][i] : qm_choice_name(2, i);
}
/* A sheet's setting rows' values again, Group by's flip having renamed them (qm_run). */
static void qm_values(void *dialog) {
    for (int kind = 0; kind < 3; ++kind) {
        char name[16];
        tk_snprintf(name, sizeof name, "_qm_set%d", kind);
        void *row = widget_lookup(dialog, name, 1);
        if (row) widget_set_prop_str(row, "_mm_value", qm_value_name(kind, playback_option(kind)));
    }
    widget_invalidate_force(dialog, (void *)0);
}

#define MODAL_PITCH 32 /* the menu sheet's rows (menu_modal.c) */

static int qm_open(const void *unused) {
    st.qm_timer = 0;
    void *wm = window_manager(), *page = window_manager_get_top_window(wm);
    void *r = usable() && !window_manager_is_animating(wm) ? qm_record() : (void *)0;
    if (!r) return 0;
    navigator_to("dialog/sortselect_dialog");
    void *dialog = window_manager_get_top_window(wm);
    if (dialog == page) return 0;
    if (tk_strcmp(widget_get_prop_str(dialog, "name", ""), "sortselect_dialog")) {
        window_close(dialog);
        return 0;
    }
    /* The Play hold is a button, as Centre and Return: it ends a click's touch hiding
     * (hide_outline). A choice opens from a row's click, which may be a tap. */
    if (!st.qm_choice) st.touch_mode = 0;
    void *back = widget_lookup(dialog, "img_return", 1),
         *view = widget_lookup(dialog, "scroll_view", 1);
    widget_off_by_func(dialog, EVT_KEY_UP, (void *)SORTSELECT_KEYUP, dialog);
    widget_off_by_func(back, EVT_CLICK, (void *)SORTSELECT_CLOSE, dialog);
    widget_on(dialog, EVT_KEY_UP, qm_back, dialog);
    widget_on(back, EVT_CLICK, qm_back, dialog);
    widget_on(dialog, EVT_DESTROY, qm_gone, dialog);
    if (st.qm_choice) {
        int kind = st.qm_choice - 1, count = qm_choices(kind), current = playback_option(kind);
        const char *setting = kind == 0 ? "Shuffle" : kind == 1 ? "Repeat" : "Group by";
        qm_title(dialog, setting);
        modal_head(setting, now_tag(r, REC_TITLE), r);
        widget_destroy_children(view);
        int row = MODAL_PITCH, w = modal_row_w();
        for (int i = 0; i < count; ++i) {
            void *item = list_item_create(view, 0, i * row, w, row);
            widget_use_style(item, "s_listitem_black");
            void *label = hscroll_label_create(item, 30, 0, 310, row);
            widget_use_style(label, "s_scrlabel_white24l");
            widget_set_text_utf8(label, qm_choice_name(kind, i));
            modal_row(item, label, i == current ? "check" : (const char *)0, (const char *)0,
                      MM_CHOICE);
            widget_on(item, EVT_CLICK, qm_pick, (void *)(long)(100 + i));
        }
        widget_set_prop_int(view, "virtual_h", count * row);
        modal_frame(dialog, widget_lookup(dialog, "list_view", 1));
        st.qm_dialog = dialog;
        ringnav_select(view, current, count);
        return 0;
    }
    unsigned cls = st.qm_cls;
    int song = st.qm_kind == QM_SONG || st.qm_kind == QM_COVERFLOW || st.qm_kind == QM_PLAYING;
    const char *album = P(r, REC_ALBUM);
    /* a song by its title: the player's parse names a folder play's untagged record */
    char name[512];
    const char *tag = song ? now_tag(r, REC_TITLE) : (void *)0;
    const char *title = (st.qm_kind == QM_ALBUM || st.qm_kind == QM_COVERALBUM) ? album
                        : st.qm_kind == QM_GROUP ? P(r, qm_by[(cls & 0xf) - 4])
                        : tag && *tag            ? tag
                        : song                   ? track_name(name, sizeof name, r)
                                                 : P(r, REC_NAME);
    if (st.qm_kind == QM_PLAYLIST && *st.qm_name) title = st.qm_name;
    qm_title(dialog, title ? title : "");
    widget_destroy_children(view);
    /* Songs favourite, as stock's heart, and go to their album and artist; collections shuffle.
     * Go to is left out where its single-instance page is already open, or the album is the page.
     */
    unsigned char acts[24], n = 0;
    /* Shuffle Albums/Folders only: skipping the group comes first */
    int group_first = st.qm_kind == QM_PLAYING && playback_option(0) >= 3;
    if (group_first) acts[n++] = QA_NEXT_GROUP, acts[n++] = QA_PREV_GROUP;
    int at, off; /* a spoken CUE book's saved place, from any of its tracks */
    if (st.qm_kind == QM_SONG && book_place(qm_list(), r, &at, &off)) acts[n++] = QA_RESUME;
    /* the menu sheet's song (OLED's, docs/oled-design.md#playlists): Play Next, Play Last, Add to
     * On-The-Go (while it is on), Add to Playlist…, Favorite, then the Go to rows; its playlist:
     * Play, Shuffle, Rename and, after a gap, Delete */
    int list = st.qm_kind == QM_PLAYLIST, own = song && st.qm_kind != QM_PLAYING;
    /* Sync Offline for a server's song, album, artist or playlist (offline.c): a playlist's before
     * its Rename and Delete, any other's after its Favorite and Add to Playlist rows */
    int okind = song                                                    ? OFF_SONG
                : st.qm_kind == QM_ALBUM || st.qm_kind == QM_COVERALBUM ? OFF_ALBUM
                : st.qm_kind == QM_GROUP && cls == 0xf004               ? OFF_ARTIST
                : list                                                  ? OFF_PLAYLIST
                                                                        : 0;
    const char *offl = offline_row(okind, r, list ? (int)st.qm_idx : -1);
    if (list) acts[n++] = QA_PLAY;
    if (!list) acts[n++] = QA_NEXT, acts[n++] = QA_ADD;
    if (own && ringnav_otg()) acts[n++] = QA_OTG;
    if (st.qm_kind == QM_PLAYING) {
        if (chapter_file(r)) acts[n++] = QA_CHAPTERS;
        acts[n++] = QA_QUEUE;
    }
    if (own) acts[n++] = QA_PLAYLIST;
    if (!song) acts[n++] = QA_SHUFFLE;
    if (list && offl) acts[n++] = QA_OFFLINE;
    if (list) acts[n++] = QA_RENAME, acts[n++] = QA_DELETE;
    if (song) acts[n++] = rate_fav(r) ? QA_UNFAV : QA_FAV;
    /* an album's own favourite, its hearts' 5 (ratings.c rate_album_favs) */
    unsigned aid = song ? 0 : qm_album_id(r);
    if (aid) acts[n++] = rate_kept(1, aid) == 5 ? QA_UNFAV : QA_FAV;
    if (!own && (st.qm_kind < QM_COVERFLOW || st.qm_kind == QM_PLAYING)) acts[n++] = QA_PLAYLIST;
    if (!list && offl) acts[n++] = QA_OFFLINE;
    /* a card app's song isn't in the Library: Go to only to the app's own page */
    int hidden = song && !addon_goes(P(r, REC_PATH));
    int held = qm_held_on(page); /* a playlist's page: a song's whole menu, then its Remove */
    if ((st.qm_kind == QM_SONG || st.qm_kind == QM_PLAYING || held) && !hidden &&
        (cls & 0xfff0) != 0xff10 && qm_goes(r, 1))
        acts[n++] = QA_ALBUM;
    if ((song || cls == CLASS_ALBUMS) && !hidden && qm_goes(r, 0)) acts[n++] = QA_ARTIST;
    /* ponytail: hidden while a folder_page is open (as Go to's single-instance pages), so a folder
     * play's Now Playing has none; Back reaches that folder_page instead. */
    if (song && !navigator_window_is_exist("folder_page") && song_folder(r, st.go_folder))
        acts[n++] = QA_FOLDER;
    if (st.qm_kind == QM_PLAYING) {
        acts[n++] = QA_ADV_SHUFFLE;
        acts[n++] = QA_REPEAT;
        acts[n++] = QA_GROUP;
        if (np_more()) acts[n++] = QA_MORE; /* last: Now Playing's own menu */
    }
    if (held && qm_held.extra) acts[n++] = QA_REMOVE; /* destructive, last */
    static const char *const rows[] = {
        "Play Next",       "Play Last",   "Shuffle",        "Favorite",         "Unfavorite",
        "Add to Playlist", "Go to Album", "Go to Artist",   "Shuffle",          "Repeat",
        "Group by",        "Next Album",  "Previous Album", "Resume",           "View Queue",
        "Go to Folder",    "Chapters",    "More…",          "Add to On-The-Go", "Play",
        "Rename",          "Delete",      "Remove",         "Sync Offline"
    };
    /* the menu sheet's symbols (tools/art.py MENU_ICONS), by row; Unfavorite's filled heart is
     * drawn in Favorite pink-red, white on the bar (art.py menu_heart-fill_*) */
    static const char *const icons[] = { "arrow-bend-down-right",
                                         "queue",
                                         "shuffle",
                                         "heart",
                                         "heart-fill",
                                         "list-plus",
                                         "vinyl-record",
                                         "microphone-stage",
                                         "shuffle",
                                         "repeat",
                                         "stack",
                                         "skip-forward",
                                         "skip-back",
                                         "clock-counter-clockwise",
                                         "list-numbers",
                                         "folder",
                                         "bookmark-simple",
                                         "dots-three",
                                         "sneaker-move",
                                         "play",
                                         "pencil-simple",
                                         "trash",
                                         "trash",
                                         "cloud-arrow-down" };
    /* stock sortselect row styles; the menu sheet's rows */
    int pitch = MODAL_PITCH, width = modal_row_w();
    for (int i = 0; i < n; ++i) {
        void *item = list_item_create(view, 0, i * pitch, width, pitch);
        widget_use_style(item, "s_listitem_black");
        void *label = hscroll_label_create(item, 30, 0, 266, pitch);
        widget_use_style(label, "s_scrlabel_white24l");
        /* a setting's value stands on its own on the modal's right column */
        int setting = acts[i] >= QA_ADV_SHUFFLE && acts[i] <= QA_GROUP;
        widget_set_text_utf8(label, acts[i] == QA_NEXT_GROUP && playback_option(2) ? "Next Folder"
                                    : acts[i] == QA_PREV_GROUP && playback_option(2)
                                        ? "Previous Folder"
                                    /* the menu sheet writes US English */
                                    : acts[i] == QA_PLAYLIST && own ? "Add to Playlist\xe2\x80\xa6"
                                    : acts[i] == QA_REMOVE          ? qm_held.extra
                                    : acts[i] == QA_OFFLINE         ? offl
                                                                    : rows[acts[i] - 1]);
        if (acts[i] == QA_REMOVE) /* red, as the card's losing verb */
            widget_set_prop_int(label, "style:normal:text_color", (int)RGBA(RGB_NEGATIVE));
        int kind = acts[i] - QA_ADV_SHUFFLE;
        const char *value = setting ? qm_value_name(kind, playback_option(kind)) : 0;
        if (acts[i] == QA_DELETE || acts[i] == QA_REMOVE)
            modal_gap(view); /* destructive, last after a gap */
        widget_restack(item, widget_count_children(view) - 1);
        modal_row(item, label, acts[i] == QA_OFFLINE ? offline_icon() : icons[acts[i] - 1], value,
                  setting ? MM_SETTING : MM_ACTION);
        if (acts[i] == QA_DELETE || acts[i] == QA_REMOVE) modal_red(item);
        if (setting) {
            char name[16];
            tk_snprintf(name, sizeof name, "_qm_set%d", kind);
            widget_set_name(item, name);
        }
        widget_on(item, EVT_CLICK, qm_pick, (void *)(long)acts[i]);
    }
    widget_set_prop_int(view, "virtual_h", n * pitch);
    /* the sheet's header: the item, what it is, its cover */
    char sub[96] = "";
    const char *artist = song || aid ? now_tag(r, REC_ARTIST) : 0;
    if (song && st.qm_kind != QM_PLAYING && artist && *artist && album && *album)
        tk_snprintf(sub, sizeof sub, "%s \xc2\xb7 %s", artist, album);
    else if (artist)
        tk_snprintf(sub, sizeof sub, "%s", artist);
    else if (st.qm_kind == QM_PLAYLIST)
        tk_snprintf(sub, sizeof sub, "Playlist");
    modal_head(title ? title : "", sub, song || aid ? r : (void *)0);
    modal_frame(dialog, widget_lookup(dialog, "list_view", 1));
    st.qm_dialog = dialog;
    ringnav_select(view, 0, n); /* the first action */
    return 0;
}

/* What holds the row that opens the menu: Play/Pause (0, ringnav_keylong), Centre (KEY_CENTER) or a
 * finger (QM_BY_TOUCH) on a song row (qm_centre_hold, qm_touch_hold, from centre_menu.c). Only
 * Play/Pause's release is latched here (qm_press, play_hold_released); Centre's release and the
 * finger's are centre_menu.c's, and a finger has no key record (qm_key's stand-in is never read).
 */
#define QM_BY_TOUCH 0xffffffffu
static unsigned qm_hold_key __attribute__((section(".scratch")));
static char *key_record(unsigned k) {
    char *s = window_manager_get_input_device_status(window_manager());
    for (int i = 0; s && i < INPUT_KEY_COUNT; ++i)
        if ((unsigned)I(s, INPUT_KEYS + i * INPUT_KEY_SIZE) == k)
            return s + INPUT_KEYS + i * INPUT_KEY_SIZE;
    return (char *)0;
}
static char *qm_key(void) {
    return qm_hold_key == QM_BY_TOUCH  ? (char *)&qm_hold_key
           : qm_hold_key == KEY_CENTER ? key_record(KEY_CENTER)
                                       : play_key();
}
static void qm_latch(char *key) {
    st.qm_press = qm_hold_key ? 0 : *(unsigned long long *)(key + INPUT_KEY_TIME);
}

/* A playlist's hold, from its row or its cover: the list's own menu, at its index in the live list
 * (p_deque_playlist), which its More page takes. A cover's title is the name the covers show: the
 * live list keeps a renamed one's old name until its page is on top again. */
static int qm_list_hold(unsigned at, char *key, void *cover) {
    void *lists = P(p_deque_playlist, 0);
    void *r = lists && at < deque_size(lists) ? deque_at(lists, at) : 0;
    if (!key || !r || !(st.qm_timer = timer_add(qm_open, (void *)0, 0))) return 0;
    st.qm_kind = QM_PLAYLIST;
    const char *name = cover ? P(cover, REC_TITLE) : 0;
    tk_snprintf(st.qm_name, sizeof st.qm_name, "%s", name ? name : "");
    st.qm_cls = 0xf001; /* its songs load as a song list, as Shuffle's (play_folder) */
    st.qm_idx = at;
    st.qm_rows = deque_size(lists);
    st.qm_hash = rec_hash(r);
    qm_latch(key);
    drop_input();
    return 1;
}

/* A payload page's own song list held (a playlist's page, playlist_pages.c): song at of list, on
 * page, gets the menu a Library song gets, and extra, when given, as its last row after a gap, in
 * red, which runs pick. 1 when the menu is on its way. */
int qm_song_hold(void *page, void *list, unsigned at, const char *extra, void (*pick)(void)) {
    char *key = qm_key();
    void *r = list && at < deque_size(list) ? deque_at(list, at) : (void *)0;
    if (!page || !r || !key || playback_video(r) || st.qm_dialog || st.qm_timer ||
        !(st.qm_timer = timer_add(qm_open, (void *)0, 0)))
        return 0;
    st.qm_choice = 0;
    qm_held.page = page, qm_held.list = list, qm_held.extra = extra, qm_held.pick = pick;
    st.qm_kind = QM_COVERFLOW; /* a song of a list on the top page, as Cover Flow's tracks */
    st.qm_cls = 0xf001;
    st.qm_idx = at;
    st.qm_rows = deque_size(list);
    st.qm_hash = rec_hash(r);
    qm_latch(key);
    drop_input();
    return 1;
}

/* The hold: the same gates and row as a centre press, over the stock showlist or Coverflow's
 * own tracks (row count checked), or the album at the centre of Coverflow's covers. Everything
 * else stays stock. With songs, only a song row of a list gets its menu (Centre's hold): no Now
 * Playing, album, artist, folder or playlist row. */
static int qm_hold(int songs) {
    st.qm_choice = 0;
    qm_held.page = (void *)0; /* Cover Flow's own tracks, not a page qm_song_hold held */
    void *wm = window_manager(), *top = window_manager_get_top_window(wm);
    const char *name = top ? widget_get_prop_str(top, "name", "") : "";
    int playing = !tk_strcmp(name, "playing_page");
    if (st.qm_dialog || st.qm_timer || st.qm_playlist || !usable() || (playing && songs) ||
        (!playing && !allowed_top(top)) || window_manager_is_animating(wm) ||
        (qm_hold_key != QM_BY_TOUCH && pointer_down()) || airplayGetFlag() == 2 || g_navbar_status)
        return 0;
    if (playing) {
        unsigned at, n, type = (unsigned)MCL(MCL_TYPE);
        void *r = queue_now(&at, &n), *q = P(mcl_pdeqplaylist, 0);
        char *key = qm_key();
        if (!r || !key || (type != 1 && (type & 0xf000) != 0xf000) ||
            !(st.qm_timer = timer_add(qm_open, 0, 0)))
            return 0;
        st.qm_kind = QM_PLAYING;
        st.qm_cls = 0xf001;
        st.qm_queue = q;
        st.qm_idx = at;
        st.qm_rows = n;
        st.qm_hash = rec_hash(r);
        st.qm_queue_hash = queue_hash(q);
        qm_latch(key);
        drop_input();
        return 1;
    }
    int kind = contexts[context_id(name)].kind;
    void *album = coverflow_album(top), *cover = coverflow_playlist(top);
    char *key = qm_key();
    if (songs && (cover || album)) return 0;
    if (cover)
        return qm_list_hold(playlist_index(I(cover, REC_DISC)), key, cover); /* by its cd_num */
    if (album) {
        if (!key || !(st.qm_timer = timer_add(qm_open, (void *)0, 0))) return 0;
        st.qm_kind = QM_COVERALBUM;
        st.qm_cls = CLASS_ALBUMS;
        st.qm_hash = rec_hash(album);
        qm_latch(key);
        drop_input();
        return 1;
    }
    void *list = page_tracks(top);
    int coverflow = list != 0;
    void *w = surface_under(top, (void *)0, (void *)0, 0);
    if ((!coverflow && kind < CTX_FOLDER) || !w || !load(&g_menu, w, 1) || g_menu.ctx < 0 ||
        g_menu.kind == 3)
        return 0;
    int cur = reconcile(&g_menu, !moving(&g_menu));
    if (cur < 0) return 0;
    /* A playlist row's hold queues its songs; More… opens the page its trailing dots do. The Add
     * to Playlist picker shares playlist_page's name and keeps stock's hold: its add mode tells
     * the two apart, as the showlist under it is another page's. */
    if (!tk_strcmp(name, "playlist_page") && (songs || widget_get_prop_int(top, PL_MODE, 0)))
        return 0;
    if (!tk_strcmp(name, "playlist_page")) {
        /* The row's index counts Import and Export; its dots' name carries the playlist's, else
         * (OLED's rows have none) its button's, as stock names it; On-The-Go's none. */
        void *more = more_image(g_menu.at[cur]);
        const char *at = more ? widget_get_prop_str(more, "name", "") + 8
                              : widget_get_prop_str(g_menu.at[cur], "name", "");
        return qm_list_hold(at && *at >= '0' && *at <= '9' ? (unsigned)atoi(at) : ~0u, key, 0);
    }
    if (!coverflow) list = P(p_deque_showlist, 0);
    if (!list || deque_size(list) != (unsigned)g_menu.rows) return 0;
    void *r = deque_at(list, g_menu.id[cur]);
    /* An artist's tabs reload the showlist with load_localartist_list, which sets classinfo +0
     * (0xff07 songs, 0xff01 albums) but not g_class_type. */
    unsigned cls = (unsigned)(tk_strcmp(name, "artistinfo_page") ? I(g_class_type, 0)
                                                                 : I(g_local_classinfo_save, 0));
    if (!r || !key || playback_video(r)) return 0;
    int qk = I(r, REC_TYPE) == 8 ? QM_SONG : 0;
    if (coverflow) {
        if (qk) qk = QM_COVERFLOW;
    } else if (kind == CTX_FOLDER) {
        if (I(r, REC_TYPE) == 4) qk = QM_FOLDER;
    } else if (cls == CLASS_ALBUMS || (cls >= CLASS_ARTIST_ALBUMS && cls <= 0xff03))
        qk = QM_ALBUM; /* all albums, and an artist's, composer's or genre's */
    else if (cls >= 0xf004 && cls <= 0xf006)
        qk = QM_GROUP; /* artist, composer and genre lists */
    if (!qk || (songs && qk != QM_SONG && qk != QM_COVERFLOW) ||
        !(st.qm_timer = timer_add(qm_open, (void *)0, 0)))
        return 0;
    st.qm_kind = qk;
    st.qm_cls = cls;
    st.qm_idx = (unsigned)g_menu.id[cur];
    st.qm_rows = (unsigned)g_menu.rows;
    st.qm_hash = rec_hash(r);
    st.qm_browse = browse_hash();
    qm_latch(key);
    drop_input();
    return 1;
}

/* Centre held on top's selected row (centre_menu.c): a song's hold menu, the one Play/Pause's hold
 * opens, on Up Next's queued song (upnext.c un_menu_hold), a payload playlist page's song
 * (playlist_page_song_hold), a song result of Search (search_strip.c search_strip_song_hold) or a
 * list's song row (qm_hold's songs); a card app's row its own menu, the app's (addon.c
 * addon_row_hold); 1 when it is on its way. Any other row is left as it was: stock's Centre hold,
 * or the page's own. */
static int qm_song_row_hold(void *top, unsigned by, int centre) {
    qm_hold_key = by;
    int taken = un_menu_hold(top, centre) || playlist_page_song_hold(top) ||
                search_strip_song_hold(top) || addon_row_hold(top) || qm_hold(1);
    qm_hold_key = 0;
    return taken;
}
int qm_centre_hold(void *top) { return qm_song_row_hold(top, KEY_CENTER, 1); }

/* A finger held still at the UI point (x, y) (centre_menu.c): the row under it becomes the
 * selection and gets the menu Centre's hold gives it; a row that is no song gets its selection
 * back. 1 when the menu is on its way. */
int qm_touch_hold(void *top, int x, int y) {
    qm_hold_key = QM_BY_TOUCH; /* Search's results are painted, not rows: its own hit test */
    int found = search_strip_touch_hold(top, x, y);
    qm_hold_key = 0;
    if (found) return 1;
    void *w = top ? surface_under(top, (void *)0, (void *)0, 0) : (void *)0;
    if (!w || !load(&g_menu, w, 1) || g_menu.kind == 3) return 0;
    int was = widget_get_prop_int(w, SEL, -1), rows = g_menu.rows;
    for (int i = 0; i < g_menu.n; ++i) {
        void *row = g_menu.at[i];
        int xy[2] = { x, y };
        if (!row) continue;
        widget_to_local(row, xy);
        if (xy[0] < 0 || xy[1] < 0 || xy[0] >= I(row, W_W) || xy[1] >= I(row, W_H)) continue;
        ringnav_select(w, g_menu.id[i], rows);
        if (qm_song_row_hold(top, QM_BY_TOUCH, 0)) return 1;
        ringnav_select(w, was, rows);
        return 0;
    }
    return 0;
}

/* The window manager's pointer stays "pressed" when a touch's release never reaches it: every tick
 * and Centre press on a list is then dropped (ringnav's gate) and the selection stays hidden until
 * a tap; a stuck pull-down (quick_stuck) stays
 * too. The release went missing in the input queue (playback.c ringnav_queue_event, the found
 * way, now closed); this is the backstop for any other. A press with no pointer event reaching the
 * window manager (ringnav_input) within STALE_TOUCH_MS gets an abort, so a pressed row drops its
 * press and cannot click, then the release stock never got, at the touch's last place: only that
 * path clears the flag (input_device_status_on_input_event, 0x6b3514). A finger resting that long
 * is lifted too; its real lift then finds the flag clear and does nothing (0x6b33e0). ponytail:
 * STALE_TOUCH_MS is a guess, and that the flag is what window_manager_get_pointer_pressed reads
 * (vtable +0x44) and that widgets drop a press on the abort are AWTK's source, not traced in demo;
 * the time stamped here holds a wrong guess to one try per STALE_TOUCH_MS.
 * --dev builds only (Q2POD_DEV): each heal, STALE_NOTES a boot, goes to q2-wheel.log, which the
 * next boot appends to the card's (tools/build.py WHEEL_LOG_HOOK): the page, the pull-down's state
 * and slide timer, the touch thread's own down flag (LOOP_PRESSED) and the last pointer event. */
#define STALE_NOTES 8
static void stale_note(unsigned age) {
    if (st.stale_notes >= STALE_NOTES) return;
    st.stale_notes++;
    unsigned char *loop = main_loop();
    void *top = window_manager_get_top_window(window_manager());
    char s[160];
    tk_snprintf(s, sizeof s,
                "== wheel: stale press at %u s: top=%s pull-down=%d slide=%d thread=%d "
                "last=%x %u ms ago\n",
                (unsigned)time_now_ms() / 1000, top ? widget_get_prop_str(top, "name", "") : "-",
                I(g_dropdown_menu_state, 0), top ? widget_get_prop_int(top, "timer_idex", -1) : -1,
                loop ? loop[LOOP_PRESSED] : -1, st.touch_type, age);
    void *f = fopen("/mnt/data/q2-wheel.log", "ab");
    if (!f) return;
    fputs(s, f);
    fclose(f);
}
static void stale_heal(void) {
    void *wm = window_manager(), *top = window_manager_get_top_window(wm);
    unsigned now = (unsigned)time_now_ms(), age = now - st.touch_at, abort_event[12], up[12];
    if (age < STALE_TOUCH_MS || !window_manager_get_pointer_pressed(wm)) return;
    if (Q2POD_DEV) stale_note(age);
    st.touch_at = now;
    if (top)
        widget_dispatch_event_to_target_recursive(
            top, pointer_event_init(abort_event, EVT_POINTER_ABORT, top, 0, 0));
    stock_input_trampoline(
        wm, pointer_event_init(up, EVT_POINTER_UP_BEFORE - 1, wm, st.touch_x, st.touch_y));
}
/* Stock's swipe down opens Quick Settings at g_dropdown_menu_state 1 and starts its slide only on
 * the touch's release reaching the dialog (its pointer-up handler adds the timer_idex timer). The
 * dialog is modal, opened inside the swipe's pointer move, and above the screen it catches a touch
 * only once opened (window_manager_find_target, stage 3), in an idle its nested loop runs after
 * draining the queue. So a release already queued, by a flick quicker than the dialog loads or a
 * swipe queued behind a track start, goes to the page under it: the dialog stays on top off screen
 * with that page sent to the background, Now Playing's timer stopped (#189). With no touch down
 * and no slide running, the next UI pass hands it the release through ringnav_dispatch: stock's
 * handler (0x4a1e6c, bound at dialog_statusbar_dialog_init) ignores the event's place and at states
 * 1 and 2 with timer_idex -1 always adds its slide timer (0x4a1f4c), which always ends, so one pass
 * does it. */
static int quick_stuck(void *top) {
    return usable() && top &&
           !tk_strcmp(widget_get_prop_str(top, "name", ""), "statusbar_dialog") &&
           (unsigned)(I(g_dropdown_menu_state, 0) - 1) < 2 && !pointer_down() &&
           widget_get_prop_int(top, "timer_idex", -1) == -1;
}
static void quick_poll(void) {
    void *wm = window_manager(), *top = window_manager_get_top_window(wm);
    unsigned up[12];
    if (quick_stuck(top))
        ringnav_dispatch(top, pointer_event_init(up, EVT_POINTER_UP_BEFORE - 1, top, 0, 0));
    /* Return's hold over Quick Settings (compact_now_playing), once the dialog has gone: its own
     * idle closes it, a pass or two on. One still up QS_HOLD_MS later was no hold's to wait for. */
    if (st.qs_hold && (unsigned)time_now_ms() - st.qs_hold >= QS_HOLD_MS) st.qs_hold = 0;
    if (st.qs_hold && top && !window_manager_is_animating(wm) &&
        tk_strcmp(widget_get_prop_str(top, "name", ""), "statusbar_dialog")) {
        st.qs_hold = 0;
        compact_now_playing();
    }
}
/* Home's Cover Flow row held: Display's Cover Flow page over Home. Here
 * (not home.c) for the selection, which is this file's. */
static int home_cf_hold(void *top) {
    void *wm = window_manager();
    if (!usable() || !home_menu(top) || window_manager_is_animating(wm) || pointer_down()) return 0;
    void *w = surface_under(top, (void *)0, (void *)0, 0);
    if (!w || !load(&g_menu, w, 1)) return 0;
    int cur = reconcile(&g_menu, !moving(&g_menu));
    void *row = cur >= 0 ? g_menu.at[cur] : 0;
    if (!row || !cover_flow_row(row)) return 0;
    display_open((void *)DISPLAY_COVERFLOW, 0);
    return 1;
}
/* Stock long-key callback: everything except a taken Play/Pause hold runs the stock body. On
 * Home's Cover Flow row it opens Cover Flow's settings, on an
 * Internet Radio station it is the favourite toggle, on a Spotify track Add to Queue (its reply
 * toasts), on a card app's row its hold op (addon_hold: a Deezer list downloaded whole), in Quick
 * Settings (not OLED's) the wheel's brightness/volume switch, on a saved Wi-Fi network Forget's
 * card (wifi_flows_hold), on View Queue's shuffle list
 * Play Next (un_hold), on a song result of Search its song's menu (search_strip_song_hold), on a
 * Library artist's page under OLED nothing (artist_local_on: its rows
 * are no showlist records), in Edit Library or Edit Home nothing (its tap shows or hides a row),
 * the release latched as the queue menu's. A held Previous or Next that scans (seek_held) stops
 * first. */
int ringnav_keylong(void *ctx, void *event) {
    if (event && ple_key((unsigned)I(event, EVENT_KEY), 3)) return STOP;
    if (event && seek_held((unsigned)I(event, EVENT_KEY))) return STOP;
    /* the letter strip's Previous and Next are its text's cursor, Previous held erasing there */
    if (event && (I(event, EVENT_KEY) == KEY_BACK_BTN || I(event, EVENT_KEY) == KEY_FWD_BTN) &&
        search_strip_owns(window_manager_get_top_window(window_manager())))
        return STOP;
    if (!event || I(event, EVENT_KEY) != KEY_PLAY) return stock_keylong_trampoline(ctx, event);
    void *top = window_manager_get_top_window(window_manager());
    const char *fav = radio_hold(top);
    int taken = fav || spot_hold(top) || addon_hold(top) || storage_hold(top) || bt_ui_hold(top) ||
                wifi_flows_hold(top) || un_hold(top) || home_cf_hold(top) || artist_local_on(top) ||
                playlist_page_hold(top) || search_strip_song_hold(top) ||
                lr_editing(top); /* Edit Library and Edit Home: Play/Pause's tap alone */
    if (!taken && qm_hold(0)) return STOP;
    if (taken) {
        char *key = play_key();
        if (key) st.qm_press = *(unsigned long long *)(key + INPUT_KEY_TIME);
        drop_input();
        if (fav) toast(fav);
        return STOP;
    }
    return stock_keylong_trampoline(ctx, event);
}

/* Hold time (HOLD_KEYS in tools/build.py; docs/internals.md#queue-menu): Play/Pause and Return fire
 * their long press after KEY_HOLD_MS, every other key stock's 1000 ms (500 for key 232, unused on
 * the Q2). ringnav_hold_timer replaces the key-down's timer_add, ringnav_hold_info the timer's
 * threshold lookup. */
#define KEY_HOLD_MS 700
static const int hold_info[][2] = { { KEY_PLAY, KEY_HOLD_MS }, { KEY_RETURN, KEY_HOLD_MS } };
const int *ringnav_hold_info(void *ids, int key) {
    for (unsigned i = 0; i < sizeof hold_info / sizeof *hold_info; i++)
        if (hold_info[i][0] == key) return hold_info[i];
    return ((const int *(*)(void *, int))0x6b384c)(
        ids, key); /* input_device_status_find_key_long_press_info */
}
unsigned ringnav_hold_timer(int (*fn)(const void *), void *ids, unsigned ms, int key) {
    unsigned id = timer_add(fn, ids, key == KEY_PLAY || key == KEY_RETURN ? KEY_HOLD_MS : ms);
    if (key == KEY_CENTER) st.centre_hold = id; /* chord_gate may end it */
    return id;
}
