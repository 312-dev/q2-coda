/* Playback > Library (docs/ipod.md#settings): what the Library holds and how it is ordered and
 * shown, on one page with three of its own, each stock's Playback page opened again
 * (navigation.c library_open, ringnav_audioset):
 *   Library    On-The-Go, Sorting >, Artists, Artist View, Playlists >,
 *              Folders >, Scan Song Durations
 *   Sorting    Songs > and Playlists > (pickers), Artist's Albums, Years
 *   Playlists  Playlist View, then after a gap Import Playlists and Export Playlists
 *   Folders    Folder Root, Folder Skip (stock's own row), Folder Album Cover
 * The pickers (Songs' and Playlists' orders here, Display > Cover Flow's Sort, a Bluetooth device's
 * Volume Curve) are stock's Buttons Lock page opened again (picker_open), its rows ours.
 *
 * A signed-in card app's server songs are always in the Library (catalogue.c; the user's,
 * 2026-11-19: no setting). An older build's [Q2POD] LIBRARYSOURCE is never read. Card apps that
 * still read the state find "server" in LIB_FILE, written with the caps file (addon.c
 * addon_scan, so before any app starts) (docs/internals.md#server-library). */
#include "navigation.h"

#define LIB_FILE "/tmp/q2-addon.library"
#define PICK_SONGS 1
#define PICK_CF_SORT 2
#define PICK_BT_CURVE 3
#define PICK_PLAYLISTS 4
#define ARROW_UP " \xe2\x86\x91" /* ↑: ascending, as Title's A to Z */
#define ARROW_DOWN " \xe2\x86\x93"

static struct {
    void *songs, *lists; /* Sorting's Songs and Playlists rows' labels, while its page is open */
    int pending, kind;   /* a picker asked for (picker_open) and which */
    void *win;           /* the picker's page, once built */
} ls __attribute__((section(".scratch")));

static const char *const song_names[SORT_N] = { "Title", "Artist", "Album", "Recently Added" };
static const char *const list_names[PLORDER_N] = { "Name", "Recently Added" };

/* Order i of Songs' (lists 0) or Playlists' as a row shows it, the current one with its
 * direction: "Title ↑" */
static void sort_text(char *out, unsigned size, int lists, int i) {
    int now = lists ? playlist_sort() : song_sort(), down = lists ? playlist_down() : song_down();
    tk_snprintf(out, size, "%s%s", lists ? list_names[i] : song_names[i],
                i != now ? ""
                : down   ? ARROW_DOWN
                         : ARROW_UP);
}
/* Sorting's two picker rows, "Songs: Title ↑" and "Playlists: Name ↑" */
static void sorting_text(void) {
    char t[48], v[40];
    if (ls.songs) {
        sort_text(v, sizeof v, 0, song_sort());
        tk_snprintf(t, sizeof t, "Songs: %s", v);
        row_value_text(ls.songs, t);
    }
    if (ls.lists) {
        sort_text(v, sizeof v, 1, playlist_sort());
        tk_snprintf(t, sizeof t, "Playlists: %s", v);
        row_value_text(ls.lists, t);
    }
}
static int sorting_gone(void *label, void *event) {
    if (ls.songs == label) ls.songs = (void *)0;
    if (ls.lists == label) ls.lists = (void *)0;
    return 0;
}

/* ---- The pickers ---- */

static int picker_count(void) {
    return ls.kind == PICK_SONGS       ? SORT_N
           : ls.kind == PICK_PLAYLISTS ? PLORDER_N
           : ls.kind == PICK_BT_CURVE  ? 2
                                       : coverflow_sort_count();
}
static int picker_now(void) {
    return ls.kind == PICK_SONGS       ? song_sort()
           : ls.kind == PICK_PLAYLISTS ? playlist_sort()
           : ls.kind == PICK_BT_CURVE  ? btlim_edit_curve()
                                       : coverflow_sort_now();
}
static void picker_text(void *label, int i) {
    char t[40];
    if (ls.kind == PICK_SONGS || ls.kind == PICK_PLAYLISTS)
        sort_text(t, sizeof t, ls.kind == PICK_PLAYLISTS, i);
    else if (ls.kind == PICK_BT_CURVE)
        tk_snprintf(t, sizeof t, "%s", btlim_curve_name(i));
    else
        tk_snprintf(t, sizeof t, "%s", coverflow_sort_at(i));
    widget_set_text_utf8(label, t);
}
/* each row's mark and text again: a pick changes the current one, and an order's direction */
static void picker_marks(void *view) {
    int now = picker_now();
    for (unsigned i = 0; view && i < widget_count_children(view); ++i) {
        void *button = widget_get_child(widget_get_child(view, i), 0);
        image_base_set_image(widget_get_child(button, 0), (int)i == now ? "select" : "unselect");
        picker_text(widget_get_child(button, 1), (int)i);
    }
}
/* A pick: Songs' or Playlists' current order again flips its direction, another takes its natural
 * one (titles, artists, albums and names A to Z, the newest first), and Songs reloads in it when
 * it is on top again, a list of the playlists at its next load;
 * Cover Flow takes its order at its next open; a device's Volume Curve is saved and sent to it at
 * once when it is connected. The picker stays, as Buttons Lock's does. */
static int picker_pick(void *ctx, void *event) {
    int i = (int)(long)ctx;
    if (ls.kind == PICK_SONGS) {
        int again = i == song_sort();
        song_sort_set(i, again ? !song_down() : i == SORT_ADDED);
        song_sort_commit();
        sorting_text();
    } else if (ls.kind == PICK_PLAYLISTS) {
        int again = i == playlist_sort();
        playlist_sort_set(i, again ? !playlist_down() : i == PLORDER_ADDED);
        sorting_text();
    } else if (ls.kind == PICK_BT_CURVE) {
        btlim_edit_curve_take(i);
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
    if (title)
        widget_set_text_utf8(title, ls.kind == PICK_SONGS       ? "Songs"
                                    : ls.kind == PICK_PLAYLISTS ? "Playlists"
                                    : ls.kind == PICK_BT_CURVE  ? "Volume Curve"
                                                                : "Sort");
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
/* A Bluetooth device page's Volume Curve row (bt_detail.c), for btlim_edit's device */
void bt_curve_open(void) { picker_open((void *)PICK_BT_CURVE, 0); }

/* ---- The server library's state ---- */

/* LIB_FILE: "server", the server's songs in the Library, for card apps (the caps word libsource
 * says the firmware writes it). /tmp empties at each start, so the file is always the running
 * firmware's. */
void library_source_file(void) {
    static const char v[] = "server\n";
    blob_io(LIB_FILE, LIB_FILE ".tmp", (void *)v, sizeof v - 1, 1);
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
        INTO("Sorting", LIB_SORTING);
        ROWS(POD_ARTISTS, 1);
        ROWS(POD_ARTIST_CF, 1);
        INTO("Playlists", LIB_PLAYLISTS);
        INTO("Folders", LIB_FOLDERS);
        ROWS(POD_SCAN_DURATIONS, 1);
        caption_row(st.pod_label[POD_SCAN_DURATIONS], "Measures lengths after each update");
    } else if (page == LIB_SORTING) {
        ls.songs = list_row_into(view, icons[0], picker_open, (void *)PICK_SONGS);
        widget_on(ls.songs, EVT_DESTROY, sorting_gone, ls.songs);
        ls.lists = list_row_into(view, icons[0], picker_open, (void *)PICK_PLAYLISTS);
        widget_on(ls.lists, EVT_DESTROY, sorting_gone, ls.lists);
        sorting_text();
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
