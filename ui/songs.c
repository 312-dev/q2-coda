/* The library's songs, from stock's database (/mnt/data/database.db, table songtable: name is the
 * title, songer the artist), read once into one block: a row is two offsets into it. The order is
 * the title's, case folded, then the row's id. The spike reads the table whole; docs/ui-rewrite.md
 * (#the-library) has what a library of its own would keep instead. */
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
static const char *title(int i) { return lib.text + lib.row[i][0]; }
static const char *artist(int i) { return lib.text + lib.row[i][1]; }

int songs_open(const char *path, source_t *out, int limit) {
    sqlite3 *db;
    sqlite3_stmt *q;
    if (sqlite3_open_v2(path, &db, SQLITE_OPEN_READONLY, 0) != SQLITE_OK) return -1;
    if (sqlite3_prepare_v2(
            db, "select name, songer from songtable order by name collate nocase, id limit ?", -1,
            &q, 0) != SQLITE_OK) {
        sqlite3_close(db);
        return -1;
    }
    sqlite3_bind_int(q, 1, limit > 0 ? limit : -1);
    while (sqlite3_step(q) == SQLITE_ROW) {
        if (lib.n == lib.rows) {
            lib.rows = lib.rows ? lib.rows * 2 : 1024;
            lib.row = realloc(lib.row, (size_t)lib.rows * sizeof *lib.row);
        }
        lib.row[lib.n][0] = keep(sqlite3_column_text(q, 0));
        lib.row[lib.n][1] = keep(sqlite3_column_text(q, 1));
        ++lib.n;
    }
    sqlite3_finalize(q);
    sqlite3_close(db);
    *out = (source_t){ lib.n, title, artist };
    return 0;
}
