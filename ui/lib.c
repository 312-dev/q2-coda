/* The library's songs, from stock's database (/mnt/data/database.db, table songtable: name is the
 * title, songer the artist) and, when the card has one, the server library's catalogue (attached
 * as cat: servertable has songtable's columns and holds the server rows the Library shows,
 * docs/server-library.md), read once into one block: a row is two offsets into it and its id,
 * which is what the player is told to play. The order is
 * the title's, case folded, then the row's id. The tables are read whole;
 * docs/ui-rewrite.md (#the-library) has what a library of its own would keep instead. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>

#include <sqlite3.h>

#include "q2ui.h"

static struct {
    char *text;
    unsigned used, room;
    unsigned (*row)[3]; /* title, artist, album */
    int *id, *track;
    struct group {
        int n;       /* groups */
        int *first;  /* each group's first place in order, and one past the last group's */
        int *order;  /* the songs, group by group */
    } groups[LIB_KINDS];
    int n, rows;
} lib;

static unsigned keep(const unsigned char *s) {
    unsigned len = s ? (unsigned)strlen((const char *)s) + 1 : 1, at = lib.used;
    if (lib.used + len > lib.room) {
        lib.room = (lib.room + len) * 2;
        lib.text = realloc(lib.text, lib.room);
    }
    memcpy(lib.text + at, s ? (const char *)s : "", len);
    lib.used += len;
    return at;
}
int lib_songs(void) { return lib.n; }
const int *lib_ids(void) { return lib.id; }
const char *lib_album(int i) { return lib.text + lib.row[i][2]; }
int lib_track(int i) { return lib.track[i]; }

/* Artists and Albums: the songs put in the group's order (its name, case folded; inside an album
 * by track, inside an artist by title, which is the order they were read in), and the places a
 * new name starts. Made the first time a list of them opens: a sort of the songs' numbers. */
static int group_kind;
static const char *group_key(int song) { return lib.text + lib.row[song][group_kind == LIB_ALBUM ? 2 : 1]; }
static int group_order(const void *a, const void *b) {
    int x = *(const int *)a, y = *(const int *)b, c = strcasecmp(group_key(x), group_key(y));
    if (c) return c;
    if (group_kind == LIB_ALBUM && lib.track[x] != lib.track[y]) return lib.track[x] - lib.track[y];
    return x - y;
}
static struct group *group(int kind) {
    struct group *g = &lib.groups[kind];
    if (g->order || !lib.n) return g;
    g->order = malloc((size_t)lib.n * sizeof *g->order);
    g->first = malloc((size_t)(lib.n + 1) * sizeof *g->first);
    for (int i = 0; i < lib.n; ++i) g->order[i] = i;
    group_kind = kind;
    qsort(g->order, (size_t)lib.n, sizeof *g->order, group_order);
    for (int i = 0; i < lib.n; ++i)
        if (!i || strcasecmp(group_key(g->order[i]), group_key(g->order[i - 1]))) g->first[g->n++] = i;
    g->first[g->n] = lib.n;
    return g;
}
int lib_groups(int kind) { return group(kind)->n; }
const char *lib_group_name(int kind, int g) {
    group_kind = kind;
    const char *name = group_key(group(kind)->order[group(kind)->first[g]]);
    return *name ? name : kind == LIB_ALBUM ? "Unknown Album" : "Unknown Artist";
}
const char *lib_group_artist(int g) { return lib_artist(group(LIB_ALBUM)->order[group(LIB_ALBUM)->first[g]]); }
const int *lib_group_songs(int kind, int g, int *n) {
    struct group *k = group(kind);
    *n = k->first[g + 1] - k->first[g];
    return k->order + k->first[g];
}
const char *lib_title(int i) { return lib.text + lib.row[i][0]; }
const char *lib_artist(int i) { return lib.text + lib.row[i][1]; }

/* catalogue: a server library's catalogue.db, or 0; one that cannot be attached is left out */
int lib_open(const char *path, const char *catalogue, int limit) {
    sqlite3 *db;
    sqlite3_stmt *q;
    if (sqlite3_open_v2(path, &db, SQLITE_OPEN_READONLY, 0) != SQLITE_OK) return -1;
    int server = 0;
    if (catalogue && sqlite3_prepare_v2(db, "attach ? as cat", -1, &q, 0) == SQLITE_OK) {
        sqlite3_bind_text(q, 1, catalogue, -1, SQLITE_STATIC);
        server = sqlite3_step(q) == SQLITE_DONE;
        sqlite3_finalize(q);
    }
    const char *sql =
        server ? "select name, songer, id, album, track from (select name, songer, id, album, track from "
                 "main.songtable union all select name, songer, id, album, track from cat.servertable) "
                 "order by name collate nocase, id limit ?"
               : "select name, songer, id, album, track from songtable order by name collate nocase, id limit ?";
    if (sqlite3_prepare_v2(db, sql, -1, &q, 0) != SQLITE_OK) {
        sqlite3_close(db);
        return -1;
    }
    sqlite3_bind_int(q, 1, limit > 0 ? limit : -1);
    int rc;
    while ((rc = sqlite3_step(q)) == SQLITE_ROW) {
        if (lib.n == lib.rows) {
            lib.rows = lib.rows ? lib.rows * 2 : 1024;
            lib.row = realloc(lib.row, (size_t)lib.rows * sizeof *lib.row);
            lib.id = realloc(lib.id, (size_t)lib.rows * sizeof *lib.id);
            lib.track = realloc(lib.track, (size_t)lib.rows * sizeof *lib.track);
        }
        lib.id[lib.n] = sqlite3_column_int(q, 2);
        lib.row[lib.n][2] = keep(sqlite3_column_text(q, 3));
        lib.track[lib.n] = sqlite3_column_int(q, 4);
        lib.row[lib.n][0] = keep(sqlite3_column_text(q, 0));
        lib.row[lib.n][1] = keep(sqlite3_column_text(q, 1));
        ++lib.n;
    }
    if (rc != SQLITE_DONE) fprintf(stderr, "q2ui: songs: %s\n", sqlite3_errmsg(db));
    sqlite3_finalize(q);
    sqlite3_close(db);
    return 0;
}
