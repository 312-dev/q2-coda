/* The library's songs, from stock's database (/mnt/data/database.db, table songtable: name is the
 * title, songer the artist) and, when the card has one, the server library's catalogue (attached
 * as cat: servertable has songtable's columns and holds the server rows the Library shows,
 * docs/server-library.md), read once into one block: a row is two offsets into it. The order is
 * the title's, case folded, then the row's id. The tables are read whole;
 * docs/ui-rewrite.md (#the-library) has what a library of its own would keep instead. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include <sqlite3.h>

#include "q2ui.h"

static struct {
    char *text;
    unsigned used, room;
    unsigned (*row)[2];
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
        server ? "select name, songer from (select name, songer, id from main.songtable union all "
                 "select name, songer, id from cat.servertable) order by name collate nocase, id "
                 "limit ?"
               : "select name, songer from songtable order by name collate nocase, id limit ?";
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
        }
        lib.row[lib.n][0] = keep(sqlite3_column_text(q, 0));
        lib.row[lib.n][1] = keep(sqlite3_column_text(q, 1));
        ++lib.n;
    }
    if (rc != SQLITE_DONE) fprintf(stderr, "q2ui: songs: %s\n", sqlite3_errmsg(db));
    sqlite3_finalize(q);
    sqlite3_close(db);
    return 0;
}
