/* Playlists' order, Playback > Library > Sorting's Playlists picker (library_settings.c): Name or
 * Recently Added, each either way, kept as [Q2POD] PLAYLISTSORT (PLORDER_*) and PLAYLISTDOWN (1: Z
 * to A, or the newest first); nothing chosen is Name, A to Z. Every list of the playlists is
 * getAllPlayList's headers (Library > Playlists as rows or as Cover Flow, Home's Playlists, the
 * Add to Playlist picker), whose query navigation.c library_query ends with playlist_sort_sql's
 * ORDER BY, by name either way. Recently Added keeps the name order (A to Z) under it for its ties
 * and moves the headers once loaded (playlist_sort_row, playlist_sort_order): a playlist's date is
 * the later of its header's time_create, which the Q2 writes as it makes one (playlists.c
 * ringnav_playlist_create: New Playlist, On-The-Go's save, an import; catalogue.c pl_create: a
 * server's playlist first seen), and the modification time of its .m3u or .m3u8 in PL_DIR, which a
 * sync or an edit on a computer moves. One with neither (made before this firmware and never
 * imported again) is undated and goes last either way, by name (docs/internals.md#playlist-order).
 * ponytail: songs added, removed or moved on the Q2 change neither date, so an edit there leaves
 * the playlist in its place; a time_create written by those writers would move it. */
#include "navigation.h"
#include "playlist_art.h"

#define PL_DIR PEQ_ROOT "/mnt/mmc/_explaylist_data"
#define PL_IDS 101 /* header ids 0 to 100 */

static struct {
    unsigned char read, sort, down;
    unsigned from;                             /* the staging deque's size before the load */
    int *dated;                                /* each header's time_create by cd_num, loading */
    int (*row)(void *, int, char **, char **); /* the query's own row callback then */
} pls __attribute__((section(".scratch")));

static void pls_read(void) {
    if (pls.read) return;
    pls.sort = (unsigned char)config_value("Q2POD", "PLAYLISTSORT", PLORDER_N, PLORDER_NAME);
    pls.down = (unsigned char)config_value("Q2POD", "PLAYLISTDOWN", 2, 0);
    pls.read = 1;
}
int playlist_sort(void) {
    pls_read();
    return pls.sort;
}
int playlist_down(void) {
    pls_read();
    return pls.down;
}
/* The order and its direction, saved; a list of the playlists takes it at its next load. */
void playlist_sort_set(int sort, int down) {
    pls_read();
    pls.sort = (unsigned char)sort, pls.down = (unsigned char)!!down;
    write_int_config(pls.sort, "Q2POD", "PLAYLISTSORT");
    write_int_config(pls.down, "Q2POD", "PLAYLISTDOWN");
}

/* The headers' ORDER BY: by name, Z to A only when that is the order */
const char *playlist_sort_sql(void) {
    return playlist_sort() == PLORDER_NAME && playlist_down()
               ? " order by name collate nocase desc, id"
               : " order by name collate nocase, id";
}

/* stock's row callback, after noting the header's time_create by its cd_num */
static int dated_row(void *ctx, int n, char **v, char **c) {
    int id = -1, when = 0;
    for (int i = 0; i < n; ++i) {
        if (!c[i] || !v[i]) continue;
        if (!strcmp(c[i], "cd_num"))
            id = atoi(v[i]);
        else if (!strcmp(c[i], "time_create"))
            when = atoi(v[i]);
    }
    if (id >= 0 && id < PL_IDS) pls.dated[id] = when;
    return pls.row(ctx, n, v, c);
}
/* The row callback for the headers' query: under Recently Added on the UI thread, dated_row in
 * front of row, else row itself. playlist_sort_order follows the query either way. */
void *playlist_sort_row(void *row) {
    if (!row || pls.dated || playlist_sort() != PLORDER_ADDED || pthread_self() != ui_thread)
        return row;
    if (!(pls.dated = calloc(PL_IDS, sizeof *pls.dated))) return row;
    void *dir = P(tools_pdeq_directory, 0);
    pls.from = dir ? deque_size(dir) : 0;
    pls.row = row;
    return (void *)dated_row;
}

/* r's date: its time_create, or its file's modification time when later (o32 stat: st_mtime word
 * 16); 0 for neither. */
static int pl_date(void *r, const int *dated) {
    static const char *const ext[] = { ".m3u", ".m3u8" };
    int cd = I(r, REC_DISC), when = cd >= 0 && cd < PL_IDS ? dated[cd] : 0;
    const char *name = P(r, REC_TITLE);
    char fat[256], path[320];
    unsigned st[36];
    if (!name || !*name) return when;
    playlist_fat_name(fat, sizeof fat, name);
    for (unsigned i = 0; i < 2; ++i) {
        tk_snprintf(path, sizeof path, PL_DIR "/%s%s", fat, ext[i]);
        if (__xstat(3, path, st)) continue;
        if ((int)st[16] > when) when = (int)st[16];
        break;
    }
    return when;
}
/* a goes before b: the dated first, the newest or the oldest of them first by the direction */
static int ahead(int a, int b, int down) {
    if (!a || !b) return a && !b;
    return down ? a > b : a < b;
}
/* The headers the query just added to the staging deque, in date order (a stable insertion sort:
 * at most 101 of them), the rows staged before them left first. */
void playlist_sort_order(void) {
    int *dated = pls.dated, *key = 0;
    if (!dated) return;
    pls.dated = 0;
    void *dir = P(tools_pdeq_directory, 0);
    unsigned n = dir ? deque_size(dir) : 0, from = pls.from, *at = 0;
    if (n > from + 1 && (at = calloc(n, sizeof *at)) && (key = calloc(n, sizeof *key))) {
        int down = playlist_down();
        for (unsigned i = 0; i < n; ++i) {
            at[i] = i;
            if (i >= from) key[i] = pl_date(deque_at(dir, i), dated);
        }
        for (unsigned i = from + 1; i < n; ++i) {
            unsigned x = at[i], j = i;
            for (; j > from && ahead(key[x], key[at[j - 1]], down); --j) at[j] = at[j - 1];
            at[j] = x;
        }
        rec_permute(dir, at);
    }
    free(key);
    free(at);
    free(dated);
}
