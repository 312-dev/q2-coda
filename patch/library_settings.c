/* Playback > Library (docs/ipod.md#settings): what the Library holds and how it is ordered and
 * shown, on one page with three of its own, each stock's Playback page opened again
 * (navigation.c library_open, ringnav_audioset):
 *   Library    On-The-Go, Streaming Songs, Sorting >, Artists, Artist View, Playlists >,
 *              Folders >, Scan Song Durations
 *   Sorting    Songs > (a picker), Artist's Albums, Years
 *   Playlists  Playlist View, then after a gap Import Playlists and Export Playlists
 *   Folders    Folder Root, Folder Skip (stock's own row), Folder Album Cover
 * The pickers (Songs' order here, Display > Cover Flow's Sort) are stock's Buttons Lock page
 * opened again (picker_open), its rows ours.
 *
 * Streaming Songs is [Q2POD] LIBRARYSOURCE (1: a card app's server songs join the Library,
 * catalogue.c). The firmware is the only writer of the key; card apps read the state from
 * LIB_FILE, written at the first read of the settings and at every change. Turning it off while
 * an app has a catalogue on the card asks first, and so does a card app's needslib row while it is
 * off (addon.c), which turns it on (docs/internals.md#server-library). */
#include "navigation.h"

#define LIB_FILE "/tmp/q2-addon.library"
#define PICK_SONGS 1
#define PICK_CF_SORT 2
#define ARROW_UP " \xe2\x86\x91" /* ↑: ascending, as Title's A to Z */
#define ARROW_DOWN " \xe2\x86\x93"

static struct {
    void *songs;       /* Sorting's Songs row's label, while its page is open */
    int pending, kind; /* a picker asked for (picker_open) and which */
    void *win;         /* the picker's page, once built */
} ls __attribute__((section(".scratch")));

static const char *const song_names[SORT_N] = { "Title", "Artist", "Album", "Recently Added" };

/* Songs' order as a row shows it, with its direction: "Title ↑" */
static void song_sort_text(char *out, unsigned size, int i) {
    tk_snprintf(out, size, "%s%s", song_names[i],
                i != song_sort() ? ""
                : song_down()    ? ARROW_DOWN
                                 : ARROW_UP);
}
static void songs_text(void) {
    char t[48], v[40];
    if (!ls.songs) return;
    song_sort_text(v, sizeof v, song_sort());
    tk_snprintf(t, sizeof t, "Songs: %s", v);
    row_value_text(ls.songs, t);
}
static int songs_gone(void *label, void *event) {
    if (ls.songs == label) ls.songs = (void *)0;
    return 0;
}

/* ---- The pickers ---- */

static int picker_count(void) { return ls.kind == PICK_SONGS ? SORT_N : coverflow_sort_count(); }
static int picker_now(void) { return ls.kind == PICK_SONGS ? song_sort() : coverflow_sort_now(); }
static void picker_text(void *label, int i) {
    char t[40];
    if (ls.kind == PICK_SONGS)
        song_sort_text(t, sizeof t, i);
    else
        tk_snprintf(t, sizeof t, "%s", coverflow_sort_at(i));
    widget_set_text_utf8(label, t);
}
/* each row's mark and text again: a pick changes the current one, and Songs' direction */
static void picker_marks(void *view) {
    int now = picker_now();
    for (unsigned i = 0; view && i < widget_count_children(view); ++i) {
        void *button = widget_get_child(widget_get_child(view, i), 0);
        image_base_set_image(widget_get_child(button, 0), (int)i == now ? "select" : "unselect");
        picker_text(widget_get_child(button, 1), (int)i);
    }
}
/* A pick: Songs' current order again flips its direction, another takes its natural one (titles,
 * artists and albums A to Z, the newest first), and Songs reloads in it when it is on top again;
 * Cover Flow takes its order at its next open. The picker stays, as Buttons Lock's does. */
static int picker_pick(void *ctx, void *event) {
    int i = (int)(long)ctx;
    if (ls.kind == PICK_SONGS) {
        int again = i == song_sort();
        song_sort_set(i, again ? !song_down() : i == SORT_ADDED);
        song_sort_commit();
        songs_text();
    } else {
        coverflow_sort_set(i);
        pod_text_again(POD_CF_SORT);
    }
    picker_marks(widget_lookup(ls.win, "scroll_view_keylock", 1));
    return 0;
}
static int picker_gone(void *win, void *event) {
    if (ls.win == win) ls.win = (void *)0;
    return 0;
}
/* ringnav_keylock_rows: 1 when win is a picker's (its rows built), 0 for Buttons Lock's own. */
int picker_rows(void *win) {
    if (ls.pending && win) {
        ls.pending = 0, ls.win = win;
        widget_on(win, EVT_DESTROY, picker_gone, win);
    }
    void *view = win && win == ls.win ? widget_lookup(win, "scroll_view_keylock", 1) : (void *)0;
    if (!view) return 0;
    void *nav = widget_lookup(win, "view_navbar", 1), *title = nav ? widget_get_child(nav, 0) : 0;
    if (title) widget_set_text_utf8(title, ls.kind == PICK_SONGS ? "Songs" : "Sort");
    widget_destroy_children(view);
    for (int i = 0, n = picker_count(); i < n; ++i)
        list_row(view, "unselect", picker_pick, (void *)(long)i);
    picker_marks(view);
    return 1;
}
static int picker_open(void *ctx, void *event) {
    ls.pending = 1, ls.kind = (int)(long)ctx;
    navigator_to("systemset/keylock_page");
    ls.pending = 0;
    return 0;
}
/* Display > Cover Flow's Sort row (navigation.c pod_click) */
void cf_sort_open(void) { picker_open((void *)PICK_CF_SORT, 0); }

/* ---- Streaming Songs ---- */

/* LIB_FILE: "server" while the server's songs are in the Library, else "local", for card apps
 * (the caps word libsource says the firmware writes it). /tmp empties at each start, so the file
 * is always the running firmware's. */
void library_source_file(void) {
    pod_settings();
    const char *v = catalogue_source == 1 ? "server\n" : "local\n";
    blob_io(LIB_FILE, LIB_FILE ".tmp", (void *)v, strlen(v), 1);
}
static void library_source_set(int on) {
    catalogue_source = on;
    write_int_config(on, "Q2POD", "LIBRARYSOURCE");
    catalogue_changed();
    library_source_file();
}
/* the house confirm, with its footer's two lines */
static int library_ask(const char *question, const char *verb, const char *foot) {
    feedback_confirm_foot(foot);
    int yes = playlist_confirm(question, verb);
    feedback_confirm_foot((const char *)0);
    return yes;
}
/* The Streaming Songs row's press (navigation.c pod_click): on at once; off at once too unless a
 * card app has a catalogue on the card, when it asks first. 0 when nothing changed. */
int library_source_toggle(void) {
    pod_settings();
    if (catalogue_source != 1) {
        library_source_set(1);
        return 1;
    }
    if (catalogue_kept() &&
        !library_ask(
            "Hide streaming songs?", "Turn Off",
            "Streaming songs leave the Library, and\nShow in Library pauses until they return."))
        return 0;
    library_source_set(0);
    return 1;
}
/* A card app's needslib row (addon.c): 1 when Streaming Songs is on, asked for and turned on first
 * while it is off, as its Settings row does; 0 when it stays off. */
int library_needs(void) {
    pod_settings();
    if (catalogue_source == 1) return 1;
    if (!library_ask("Show streaming songs?", "Turn On",
                     "Show in Library needs Streaming Songs on\n(Settings > Playback > Library)."))
        return 0;
    library_source_set(1);
    return 1;
}

/* ---- The pages ---- */

/* The page's rows into stock's Playback page's view (ringnav_audioset, after stock's rows went:
 * Folders keeps stock's Folder Skip, its only one). The value rows show their value and change in
 * place; a row with a chevron opens a page. */
void library_page(void *win, void *view, int page) {
    static const char *const icons[] = { "playset_folderjump" };
#define ROWS(first, n) pod_rows(win, "scroll_view_playset", first, n, icons)
#define INTO(text, ctx)                                                                            \
    widget_set_text_utf8(list_row_into(view, icons[0], library_open, (void *)(ctx)), text)
    if (page == LIB_LIBRARY) {
        ROWS(POD_OTG, 1);
        ROWS(POD_LIBRARY_SOURCE, 1);
        /* a caption shows under the selected row (settings_pages.c) */
        caption_row(st.pod_label[POD_LIBRARY_SOURCE],
                    (const unsigned *)L"Streaming songs join your Library");
        INTO("Sorting", LIB_SORTING);
        ROWS(POD_ARTISTS, 1);
        ROWS(POD_ARTIST_CF, 1);
        INTO("Playlists", LIB_PLAYLISTS);
        INTO("Folders", LIB_FOLDERS);
        ROWS(POD_SCAN_DURATIONS, 1);
        caption_row(st.pod_label[POD_SCAN_DURATIONS],
                    (const unsigned *)L"Measures lengths after each update");
    } else if (page == LIB_SORTING) {
        ls.songs = list_row_into(view, icons[0], picker_open, (void *)PICK_SONGS);
        widget_on(ls.songs, EVT_DESTROY, songs_gone, ls.songs);
        songs_text();
        ROWS(POD_ARTIST_AZ, 1);
        ROWS(POD_YEAR_OLD, 1);
    } else if (page == LIB_PLAYLISTS) {
        ROWS(POD_PL_CF, 1);
        settings_gap(view);
        /* playlists.c: the Playlists page's own rows under the other themes */
        widget_set_text_utf8(list_row(view, icons[0], playlists_transfer, 0), "Import Playlists");
        widget_set_text_utf8(list_row(view, icons[0], playlists_transfer, (void *)1),
                             "Export Playlists");
    } else if (page == LIB_FOLDERS) {
        void *root = folder_root_row(view); /* first, above stock's Folder Skip */
        widget_restack(P(P(root, W_PARENT), W_PARENT), 0);
        ROWS(POD_FOLDERCOVER, 1);
    }
#undef ROWS
#undef INTO
}
