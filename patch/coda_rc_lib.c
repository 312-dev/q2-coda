/* coda-rc's library: Browse, Search, Play, GetQueue and GetCover over stock's database, read-only
 * (docs/internals.md#car-remote). The tree, its orders and its letter buckets are the q2-auto-remote app's
 * (its tools/q2bridge.py, remote/coda-rc.proto "browse"), built here with SQL over two temporary views of
 * stock's songtable and, with Local + Server on, the catalogue's servertable (docs/server-library.md). The
 * database is opened per request and closed after it, so no lock or stale schema outlives an answer, and
 * nothing is ever written to it. Play.resume's places, the song each list last played from a phone was on,
 * are the daemon's own file (PLACES). */
#include "coda_rc_sys.h"
#include "coda_rc.h"

#define DB "/mnt/data/database.db"
#define CAT "/mnt/mmc/.q2pod/catalogue.db"
#define CONFIG "/mnt/data/config.ini"
#define DURATIONS "/mnt/data/ringnav-durations-v1"
#define SPLIT 120         /* a list longer than this opens as letter buckets (q2bridge.py SPLIT) */
#define CAP 500           /* and a bucket longer than this splits again; no list sends more (CAP) */
#define PLAY_MAX 20000    /* songs one Play may queue */
#define NO_LETTER "123"
#define SERVER_ID 0x40000000 /* catalogue.c: server song ids start here */
#define PLACES "/mnt/data/coda-rc/places" /* "rid key" a line, the most recent first */
#define PLACES_MAX 16

/* ---- small helpers ---- */

static uint64_t fnv64(const char *s, size_t n) {
    uint64_t h = 0xcbf29ce484222325ull;
    for (size_t i = 0; i < n; ++i) h = (h ^ (unsigned char)s[i]) * 0x100000001b3ull;
    return h;
}

static void hex10(char *out, uint64_t h) {
    static const char d[] = "0123456789abcdef";
    for (int i = 0; i < 10; ++i) out[i] = d[(h >> (60 - 4 * i)) & 15];
    out[10] = 0;
}

static unsigned fnv32(unsigned h, const char *s) { /* coverflow.c fnv: with its separator */
    while (s && *s) h = (h ^ (unsigned char)*s++) * 16777619u;
    return h * 16777619u;
}

/* "song:t12" -> 12 (and 'c' ids are the catalogue's); 0 when not a song id */
int lib_song_rid(const char *id) {
    if (strncmp(id, "song:", 5) || (id[5] != 't' && id[5] != 'c')) return 0;
    char *end;
    unsigned long v = strtoul(id + 6, &end, 10);
    return *end || !v || v > 0x7fffffff ? 0 : (int)v;
}

/* A server song's path: /mnt/mmc/.<app>/cat/<album>/<sid>.<suffix> (catalogue.c); the album's bounds */
static int server_album(const char *url, const char **a, size_t *an, const char **app, size_t *appn) {
    const char *c = strstr(url, "/cat/");
    if (!c || strncmp(url, "/mnt/mmc/.", 10)) return 0;
    const char *slash = strchr(c + 5, '/');
    if (!slash || slash == c + 5) return 0;
    *a = c + 5;
    *an = slash - (c + 5);
    *app = url + 9;
    *appn = c - (url + 9);
    return memchr(*app, '/', *appn) == 0;
}

/* An album's id (q2bridge.py's s.akey): a server album's own id, else the card folder's hash */
static void akey(const char *url, char *out, size_t cap) {
    const char *a, *app;
    size_t an, appn;
    if (server_album(url, &a, &an, &app, &appn)) {
        snprintf(out, cap, "s%.*s", (int)(an > 40 ? 40 : an), a);
        return;
    }
    const char *slash = strrchr(url, '/');
    char h[11];
    hex10(h, fnv64(url, slash ? (size_t)(slash - url) : 0));
    snprintf(out, cap, "l%s", h);
}

void lib_art_of(const char *path, char *art, size_t cap) {
    if (!path || !*path) { *art = 0; return; }
    akey(path, art, cap);
}

/* The sort form of a name (q2bridge.py norm): trimmed, ASCII lowercased, a leading "the " dropped. */
static int norm(const char *s, char *out, int cap) {
    if (!s) s = "";
    while (*s == ' ') ++s;
    int n = 0;
    for (; *s && n < cap - 1; ++s) out[n++] = *s >= 'A' && *s <= 'Z' ? *s + 32 : *s;
    while (n && out[n - 1] == ' ') --n;
    out[n] = 0;
    if (!strncmp(out, "the ", 4)) { memmove(out, out + 4, n - 3); n -= 4; }
    return n;
}

/* ---- SQL functions ---- */

static void f_norm(sqlite3_context *c, int n, sqlite3_value **v) {
    char b[512];
    int k = norm((const char *)sqlite3_value_text(v[0]), b, sizeof b);
    sqlite3_result_text(c, b, k, SQLITE_TRANSIENT);
}

static void f_akey(sqlite3_context *c, int n, sqlite3_value **v) {
    char b[48];
    const char *u = (const char *)sqlite3_value_text(v[0]);
    akey(u ? u : "", b, sizeof b);
    sqlite3_result_text(c, b, -1, SQLITE_TRANSIENT);
}

static void f_hkey(sqlite3_context *c, int n, sqlite3_value **v) {
    const char *s = (const char *)sqlite3_value_text(v[0]);
    char b[11];
    hex10(b, fnv64(s ? s : "", s ? strlen(s) : 0));
    sqlite3_result_text(c, b, 10, SQLITE_TRANSIENT);
}

static int alpha(char c) { return (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z'); }

/* q2bridge.py prefix_of: up to the key's first n letters, padded to n and uppercased, or "123" */
static void f_prefix(sqlite3_context *c, int n, sqlite3_value **v) {
    const char *s = (const char *)sqlite3_value_text(v[0]);
    int len = sqlite3_value_int(v[1]);
    char b[8];
    if (!s || !alpha(s[0]) || len < 1 || len > 6) { sqlite3_result_text(c, NO_LETTER, 3, SQLITE_STATIC); return; }
    int k = 0;
    while (k < len && alpha(s[k])) { b[k] = s[k] >= 'a' ? s[k] - 32 : s[k]; ++k; }
    while (k < len) b[k++] = ' ';
    sqlite3_result_text(c, b, len, SQLITE_TRANSIENT);
}

static void f_decade(sqlite3_context *c, int n, sqlite3_value **v) {
    int y = sqlite3_value_int(v[0]);
    char b[16];
    if (y <= 0) sqlite3_result_text(c, "Unknown year", -1, SQLITE_STATIC);
    else sqlite3_result_text(c, b, snprintf(b, sizeof b, "%ds", y / 10 * 10), SQLITE_TRANSIENT);
}

/* the title, or the file name without its extension when the song is untagged */
static void f_title(sqlite3_context *c, int n, sqlite3_value **v) {
    const char *t = (const char *)sqlite3_value_text(v[0]), *f = (const char *)sqlite3_value_text(v[1]);
    if (t && *t) { sqlite3_result_text(c, t, -1, SQLITE_TRANSIENT); return; }
    if (!f) f = "";
    const char *dot = strrchr(f, '.');
    sqlite3_result_text(c, f, dot ? (int)(dot - f) : -1, SQLITE_TRANSIENT);
}

/* a card file's leading track number ("03. Song.flac" -> 3), as q2bridge.py numbers them */
static void f_lead(sqlite3_context *c, int n, sqlite3_value **v) {
    const char *f = (const char *)sqlite3_value_text(v[0]);
    int k = 0;
    while (f && *f >= '0' && *f <= '9' && k < 100000) k = k * 10 + *f++ - '0';
    sqlite3_result_int(c, k);
}

/* Song lengths: songtable has none. Card files' come from Long Songs' cache (durations.c: magic, then
 * records sorted by the path's FNV-1a 64), read once per request. */
static struct {
    uint8_t *blob;
    unsigned n;
} dur;

static void dur_load(void) {
    char p[256];
    dur.n = 0;
    free(dur.blob);
    dur.blob = 0;
    int fd = open(crc_path(p, sizeof p, DURATIONS), O_RDONLY | O_CLOEXEC);
    if (fd < 0) return;
    long size = lseek(fd, 0, 2);
    if (size >= 8 && size < 8 + 24L * 200000 && lseek(fd, 0, 0) == 0 && (dur.blob = malloc(size))) {
        long got = 0;
        while (got < size) {
            ssize_t r = read(fd, dur.blob + got, size - got);
            if (r <= 0) break;
            got += r;
        }
        if (got == size && !memcmp(dur.blob, "Q2DUR2", 7)) dur.n = (size - 8) / 24;
    }
    close(fd);
}

static void f_dur(sqlite3_context *c, int n, sqlite3_value **v) {
    const char *u = (const char *)sqlite3_value_text(v[0]);
    uint64_t key = fnv64(u ? u : "", u ? strlen(u) : 0);
    unsigned lo = 0, hi = dur.n;
    while (lo < hi) {
        unsigned mid = lo + (hi - lo) / 2;
        uint64_t k;
        memcpy(&k, dur.blob + 8 + mid * 24, 8);
        if (k < key) lo = mid + 1;
        else hi = mid;
    }
    int sec = 0;
    if (lo < dur.n) {
        uint64_t k;
        memcpy(&k, dur.blob + 8 + lo * 24, 8);
        if (k == key) memcpy(&sec, dur.blob + 8 + lo * 24 + 20, 4);
    }
    sqlite3_result_int(c, sec > 0 ? sec : 0);
}

/* ---- the database ---- */

/* Local + Server ([Q2POD] LIBRARYSOURCE=1 in config.ini) and a catalogue on the card */
int lib_has_server(void) {
    char p[256], b[4096];
    int fd = open(crc_path(p, sizeof p, CONFIG), O_RDONLY | O_CLOEXEC), n = 0;
    if (fd < 0) return 0;
    n = (int)read(fd, b, sizeof b - 1);
    close(fd);
    if (n <= 0) return 0;
    b[n] = 0;
    const char *q = strstr(b, "[Q2POD]"), *k = q ? strstr(q, "\nLIBRARYSOURCE=") : 0, *next = q ? strstr(q + 1, "\n[") : 0;
    if (!k || (next && k > next) || k[15] != '1') return 0;
    return access(crc_path(p, sizeof p, CAT), 0) == 0;
}

/* stock's library filter (offsets.inc MUSIC_WHERE): no audiobooks, podcasts or M4B in the music library */
#define MUSIC_WHERE "fileurl not like '/mnt/mmc/audiobooks/%' and fileurl not like '/mnt/mmc/podcasts/%' and fileurl not like '%.m4b'"
#define SONG_COLS(src, prefix, secs, srv)                                                                         \
    "select '" prefix "'||" src ".id sid, " src ".id rid, ifnull(" src ".filename,'') filename, ifnull(" src     \
    ".fileurl,'') url, ifnull(album,'') album, ifnull(songer,'') artist, ifnull(nullif(style,''),'[Missing "  \
    "genre]') genre, title(name," src ".filename) title, ifnull(nullif(albumsonger,''),ifnull(songer,'')) "      \
    "aartist, case when cast(year as text) glob '[1-9][0-9][0-9][0-9]*' then cast(substr(year,1,4) as "       \
    "integer) else 0 end year, ifnull(track,0) track, ifnull(cd_num,0) disc, ifnull(time_create,0) added, "      \
    secs " secs, " srv " srv"

static sqlite3 *db;
static char sql[6144];

static void lib_close(void) {
    if (db) sqlite3_close(db);
    db = 0;
}

static int lib_open(void) {
    char p[256];
    if (sqlite3_open_v2(crc_path(p, sizeof p, DB), &db, SQLITE_OPEN_READONLY, 0) != SQLITE_OK) {
        crc_log("lib: %s", db ? sqlite3_errmsg(db) : "no database");
        lib_close();
        return -1;
    }
    sqlite3_busy_timeout(db, 500);
    struct { const char *name; int n; void (*f)(sqlite3_context *, int, sqlite3_value **); } fns[] = {
        {"norm", 1, f_norm}, {"akey", 1, f_akey}, {"hkey", 1, f_hkey}, {"prefix", 2, f_prefix},
        {"decade", 1, f_decade}, {"title", 2, f_title}, {"lead", 1, f_lead}, {"dur", 1, f_dur}};
    for (unsigned i = 0; i < sizeof fns / sizeof *fns; ++i)
        sqlite3_create_function(db, fns[i].name, fns[i].n, SQLITE_UTF8 | SQLITE_DETERMINISTIC, 0, fns[i].f, 0, 0);
    dur_load();
    int server = lib_has_server();
    if (server) {
        char q[300];
        snprintf(sql, sizeof sql, "attach '%s' as cat", crc_path(q, sizeof q, CAT));
        if (strchr(q, '\'') || sqlite3_exec(db, sql, 0, 0, 0) != SQLITE_OK) server = 0;
    }
    /* no format string: MUSIC_WHERE's LIKE patterns hold '%' */
    snprintf(sql, sizeof sql, "%s%s%s",
             "create temp view songs as " SONG_COLS("songtable", "t", "dur(songtable.fileurl)", "0")
             " from main.songtable where " MUSIC_WHERE,
             server ? " union all " SONG_COLS("s", "c", "ifnull(m.seconds,0)", "1")
                      " from cat.servertable s left join cat.servermeta m on m.fileurl = s.fileurl"
                    : "",
             ";create temp view s2 as select *, akey(url) ak, norm(title) nt, norm(artist) nar, norm(album) nal, "
             "norm(aartist) naa, norm(genre) ng, case when srv then printf('%05d%05d',disc,track)||norm(title) "
             "else filename end ord, case when srv then track else lead(filename) end num from songs;"
             "create temp view albums as select ak, max(album) title, max(aartist) artist, max(year) year, "
             "max(added) added, norm(max(album)) nt, norm(max(aartist)) naa from s2 group by ak;");
    if (sqlite3_exec(db, sql, 0, 0, 0) != SQLITE_OK) {
        crc_log("lib: views: %s", sqlite3_errmsg(db));
        lib_close();
        return -1;
    }
    return 0;
}

/* ---- items ---- */

static void clock_of(char *b, size_t cap, int secs) {
    if (secs <= 0) *b = 0;
    else if (secs >= 3600) snprintf(b, cap, "%d:%02d:%02d", secs / 3600, secs / 60 % 60, secs % 60);
    else snprintf(b, cap, "%d:%02d", secs / 60, secs % 60);
}

typedef struct {
    const char *id, *title, *subtitle, *art, *group;
    int icon, browsable, playable, secs, number;
} item_t;

static void put_item(pbw *w, unsigned field, const item_t *it) {
    size_t m = pb_begin(w, field);
    pb_str(w, 1, it->id);
    pb_str(w, 2, it->title);
    pb_str(w, 3, it->subtitle);
    pb_str(w, 4, it->art);
    if (it->icon) pb_varint(w, 5, it->icon);
    if (it->browsable) pb_varint(w, 6, 1);
    if (it->playable) pb_varint(w, 7, 1);
    pb_str(w, 8, it->group);
    if (it->secs > 0) pb_varint(w, 9, it->secs * 1000u);
    if (it->number > 0) pb_varint(w, 10, it->number);
    pb_end(w, m);
}

/* A query's rows as items. Its columns: id, title, subtitle, art, icon, browsable, secs, number (an album's
 * rows only, coda-rc.proto Item.number), timed (1: the subtitle is the row's length, as an album's in track
 * order). Titles go out as the library has them; a client shows the number itself. */
static int put_rows(pbw *w, unsigned field, sqlite3_stmt *st, const char *group) {
    int n = 0, rc;
    while ((rc = sqlite3_step(st)) == SQLITE_ROW) {
        char clock[16];
        const char *t = (const char *)sqlite3_column_text(st, 1), *sub = (const char *)sqlite3_column_text(st, 2);
        int br = sqlite3_column_int(st, 5), secs = sqlite3_column_int(st, 6), num = sqlite3_column_int(st, 7);
        if (sqlite3_column_int(st, 8)) {
            clock_of(clock, sizeof clock, secs);
            sub = clock;
        }
        item_t it = {(const char *)sqlite3_column_text(st, 0), t, sub, (const char *)sqlite3_column_text(st, 3), group,
                     sqlite3_column_int(st, 4), br, !br, secs, num};
        put_item(w, field, &it);
        ++n;
    }
    return rc == SQLITE_DONE ? n : -1;
}

/* ---- the tree (remote/coda-rc.proto "browse") ---- */

typedef struct { const char *key, *label; } sort_t;
static const sort_t TRACK_SORTS[] = {{"title", "Title"}, {"artist", "Artist"}, {"album", "Album"},
                                     {"added", "Recently added"}, {"year", "Year"}, {0, 0}};
static const sort_t ARTIST_TRACK_SORTS[] = {{"album", "Album"}, {"title", "Title"}, {"year", "Year"},
                                            {"added", "Recently added"}, {0, 0}};
static const sort_t ALBUM_SORTS[] = {{"name", "Name"}, {"artist", "Artist"}, {"year", "Year"},
                                     {"added", "Recently added"}, {0, 0}};
static const sort_t IN_ALBUM_SORTS[] = {{"track", "Track order"}, {"title", "Title"}, {0, 0}};
static const sort_t PL_SORTS[] = {{"order", "Playlist order"}, {"title", "Title"}, {"artist", "Artist"},
                                  {"album", "Album"}, {0, 0}};
static const sort_t PLS_SORTS[] = {{"order", "Q2 order"}, {"name", "Name"}, {0, 0}};

/* a song's row outside an album: no number (Item.number is the track number in an album) */
#define TRACK_ITEM "'song:'||sid c0, title c1, artist c2, ak c3, 0 c4, 0 c5, secs c6, 0 c7, 0 c8"
#define ALBUM_ITEM                                                                                                 \
    "'album:'||ak c0, case when title = '' then '[Unknown album]' else title end c1, artist c2, ak c3, 0 c4, 1 c5, " \
    "0 c6, 0 c7, 0 c8"
#define ICON_TRACKS_ 1 /* coda-rc.proto ItemIcon */
#define ICON_PLAYLISTS_ 2
#define ICON_ARTISTS_ 3
#define ICON_ALBUMS_ 4
#define ICON_GENRES_ 5
#define ICON_YEARS_ 6
#define ICON_FOLDERS_ 7

/* A list: rows of `src` (item columns then bk, the bucket key, or null) in `order`, with its bucket filter. */
typedef struct {
    char src[2400];
    const char *order;
    int bucketed, years;  /* the order has a bucket key; it is a year order (decades) */
    const sort_t *sorts;
    const char *sort;
    int actions;          /* bit 0 Play, bit 1 Shuffle, bit 2 Resume */
    char title[200];
} list_t;

static const char *order_of(const char *kind, const char *sort, int *bucketed, int *years, const char **bk) {
    *bucketed = 1;
    *years = 0;
    if (!strcmp(kind, "albums") || !strcmp(kind, "artist") || !strcmp(kind, "genre") || !strcmp(kind, "year")) {
        if (!strcmp(sort, "artist")) { *bk = "naa"; return "naa, -year, nt"; }
        if (!strcmp(sort, "year")) { *bk = "year"; *years = 1; return "-year, nt"; }
        if (!strcmp(sort, "added")) { *bucketed = 0; *bk = "null"; return "-added, nt"; }
        if (!strcmp(sort, "newest")) { *bucketed = 0; *bk = "null"; return "-year, nt"; }
        *bk = "nt";
        return "nt, naa";
    }
    if (!strcmp(sort, "artist")) { *bk = "nar"; return "nar, nal, ord"; }
    if (!strcmp(sort, "album")) { *bk = "nal"; return "nal, ak, ord"; }
    if (!strcmp(sort, "added")) { *bucketed = 0; *bk = "null"; return "-added, nt"; }
    if (!strcmp(sort, "year")) { *bk = "year"; *years = 1; return "-year, nal, ord"; }
    if (!strcmp(sort, "track")) { *bucketed = 0; *bk = "null"; return "ord"; }
    if (!strcmp(sort, "order")) { *bucketed = 0; *bk = "null"; return "pos"; }
    *bk = "nt";
    return "nt";
}

static const sort_t *pick(const sort_t *sorts, const char *want) {
    for (const sort_t *s = sorts; s->key; ++s)
        if (!strcmp(s->key, want)) return s;
    return sorts;
}

/* the SQL-quoted form of an id's argument; 0 when it cannot be (a quote or too long) */
static const char *arg_ok(const char *a) {
    return strchr(a, '\'') || strlen(a) > 300 ? 0 : a;
}

/* Every list's rows have the same columns: the item's c0..c8 (put_rows), then bk, the bucket key or null,
 * and the names its orders sort by. */
#define TAIL(bk, pos) ", " bk " bk, nt, ord, " pos " pos, nal, nar, naa, added, year, ak"
#define NO_TAIL(bk, nt, pos, year) ", " bk " bk, " nt " nt, '' ord, " pos " pos, '' nal, '' nar, '' naa, 0 added, " year " year, '' ak"

/* Fills l for a node's base id (no bucket). 0 when the id names no list. */
static int list_of(const char *base, const char *sort, list_t *l) {
    char kind[24], cond[400];
    const char *colon = strchr(base, ':'), *arg = colon ? colon + 1 : "", *bk, *where = "1";
    size_t kn = colon ? (size_t)(colon - base) : strlen(base);
    if (kn >= sizeof kind || !arg_ok(arg)) return 0;
    memcpy(kind, base, kn);
    kind[kn] = 0;
    l->actions = 2;
    l->sorts = 0;
    l->sort = "";
    l->bucketed = l->years = 0;
    l->title[0] = 0;
    if (!strcmp(kind, "tracks") || !strcmp(kind, "genre-tracks") || !strcmp(kind, "artist-tracks")) {
        int artist = kind[0] == 'a';
        l->sorts = artist ? ARTIST_TRACK_SORTS : TRACK_SORTS;
        if (kind[0] != 't') {
            snprintf(cond, sizeof cond, "'%c'||hkey(%s) = '%s'", artist ? 'r' : 'g', artist ? "naa" : "ng", arg);
            where = cond;
        } else {
            snprintf(l->title, sizeof l->title, "Tracks");
        }
        l->sort = pick(l->sorts, sort)->key;
        l->order = order_of("tracks", l->sort, &l->bucketed, &l->years, &bk);
        snprintf(l->src, sizeof l->src, "select " TRACK_ITEM TAIL("%s", "0") " from s2 where %s", bk, where);
    } else if (!strcmp(kind, "album")) {
        l->sorts = IN_ALBUM_SORTS;
        l->sort = pick(l->sorts, sort)->key;
        int track = !strcmp(l->sort, "track");
        l->order = track ? "ord" : "nt";
        snprintf(l->src, sizeof l->src,
                 "select 'song:'||sid c0, title c1, '' c2, ak c3, 0 c4, 0 c5, secs c6, num c7, %d c8" TAIL("null", "0")
                 " from s2 where ak = '%s'", track, arg);
    } else if (!strcmp(kind, "pl")) {
        char *end;
        unsigned long n = strtoul(arg, &end, 10);
        if (*end || !*arg || n > 100) return 0;
        l->sorts = PL_SORTS;
        l->actions = 7;
        l->sort = pick(l->sorts, sort)->key;
        l->order = order_of("pl", l->sort, &l->bucketed, &l->years, &bk);
        /* a playlist row's place is its row id, so a song listed twice keeps both places */
        snprintf(l->src, sizeof l->src, "select " TRACK_ITEM TAIL("%s", "pid") " from (select s2.*, p.id pid from "
                 "main.playlist p join s2 on s2.url = p.fileurl where p.id > 100 and p.cd_num = %lu)", bk, n);
    } else if (!strcmp(kind, "albums") || !strcmp(kind, "year") || !strcmp(kind, "genre") || !strcmp(kind, "artist")) {
        int all = kind[1] == 'l';
        l->sorts = all ? ALBUM_SORTS : 0;
        /* an artist's albums newest first, a genre's or a year's by name (q2bridge.py) */
        const char *s = all ? pick(l->sorts, sort)->key : kind[0] == 'a' ? "newest" : "name";
        l->sort = all ? s : "";
        l->order = order_of("albums", s, &l->bucketed, &l->years, &bk);
        if (kind[0] == 'y') {
            snprintf(cond, sizeof cond, "year = %d", atoi(arg));
            where = cond;
        } else if (!all) {
            snprintf(cond, sizeof cond, "ak in (select ak from s2 where '%c'||hkey(%s) = '%s')", kind[0] == 'a' ? 'r' : 'g',
                     kind[0] == 'a' ? "naa" : "ng", arg);
            where = cond;
        }
        if (!all) l->bucketed = 0;
        snprintf(l->src, sizeof l->src, "select " ALBUM_ITEM ", %s bk, nt, '' ord, 0 pos, '' nal, '' nar, naa, added, "
                 "year, ak from albums where %s", bk, where);
    } else if (!strcmp(kind, "artists") || !strcmp(kind, "genres")) {
        int a = kind[0] == 'a';
        const char *key = a ? "naa" : "ng";
        l->order = "nt";
        l->bucketed = 1;
        snprintf(l->src, sizeof l->src, "select '%s:%c'||hkey(%s) c0, max(%s) c1, '' c2, min(ak) c3, 0 c4, 1 c5, 0 c6, "
                 "0 c7, 0 c8" NO_TAIL("%s", "%s", "0", "0") " from s2 %s group by %s", a ? "artist" : "genre",
                 a ? 'r' : 'g', key, a ? "aartist" : "genre", key, key, a ? "where naa != ''" : "", key);
    } else if (!strcmp(kind, "years")) {
        l->order = "-year";
        snprintf(l->src, sizeof l->src, "select 'year:'||year c0, case when year then year else 'Unknown year' end c1, "
                 "'' c2, min(ak) c3, %d c4, 1 c5, 0 c6, 0 c7, 0 c8" NO_TAIL("null", "''", "0", "year")
                 " from albums group by year", ICON_YEARS_);
    } else if (!strcmp(kind, "playlists")) {
        l->sorts = PLS_SORTS;
        l->sort = pick(l->sorts, sort)->key;
        l->order = !strcmp(l->sort, "name") ? "nt, pos" : "pos";
        snprintf(l->src, sizeof l->src, "select 'pl:'||h.id c0, h.name c1, '' c2, (select s2.ak from main.playlist p "
                 "join s2 on s2.url = p.fileurl where p.id > 100 and p.cd_num = h.id order by p.id limit 1) c3, %d c4, "
                 "1 c5, 0 c6, 0 c7, 0 c8" NO_TAIL("null", "norm(h.name)", "h.id", "0")
                 " from main.playlist h where h.id <= 100", ICON_PLAYLISTS_);
    } else if (!strcmp(kind, "dir")) {
        /* a card folder (q2bridge.py index_folders): its subfolders by name, then its songs by file name.
         * Server songs live in hidden folders, and hidden folders are left out. */
        char pre[320];
        int pn = snprintf(pre, sizeof pre, "/mnt/mmc/%s%s", arg, *arg ? "/" : "");
        l->order = "pos, nt";
        /* rest: the path below the folder; a subfolder's name is rest up to its first slash */
        snprintf(l->src, sizeof l->src,
                 "select distinct 'dir:'||substr(url,10,%d-10+instr(rest,'/')-1) c0, substr(rest,1,instr(rest,'/')-1) c1, "
                 "'' c2, '' c3, %d c4, 1 c5, 0 c6, 0 c7, 0 c8" NO_TAIL("null", "norm(substr(rest,1,instr(rest,'/')-1))",
                 "0", "0") " from (select url, substr(url,%d) rest from s2 where srv = 0 and substr(url,1,%d) = '%s') "
                 "where instr(rest,'/') > 0 and substr(rest,1,1) != '.' union all select " TRACK_ITEM
                 ", null bk, ord nt, ord, 1 pos, nal, nar, naa, added, year, ak from s2 where srv = 0 and "
                 "substr(url,1,%d) = '%s' and instr(substr(url,%d),'/') = 0",
                 pn + 1, ICON_FOLDERS_, pn + 1, pn, pre, pn, pre, pn + 1);
        const char *slash = strrchr(arg, '/');
        snprintf(l->title, sizeof l->title, "%s", !*arg ? "Folders" : slash ? slash + 1 : arg);
    } else {
        return 0;
    }
    return 1;
}

/* The display title of a list's own node, for BrowseResp.title */
static void title_of(const char *kind, const char *arg, list_t *l) {
    if (l->title[0]) return;
    const char *fixed = !strcmp(kind, "albums") ? "Albums" : !strcmp(kind, "artists") ? "All artists"
                      : !strcmp(kind, "genres") ? "Genres" : !strcmp(kind, "years") ? "Years"
                      : !strcmp(kind, "playlists") ? "Playlists" : 0;
    if (fixed) { snprintf(l->title, sizeof l->title, "%s", fixed); return; }
    char sql[300];
    sqlite3_stmt *st;
    if (!strcmp(kind, "album")) snprintf(sql, sizeof sql, "select title from albums where ak = '%s'", arg);
    else if (!strcmp(kind, "artist") || !strcmp(kind, "artist-tracks"))
        snprintf(sql, sizeof sql, "select aartist from s2 where 'r'||hkey(naa) = '%s' limit 1", arg);
    else if (!strcmp(kind, "genre") || !strcmp(kind, "genre-tracks"))
        snprintf(sql, sizeof sql, "select genre from s2 where 'g'||hkey(ng) = '%s' limit 1", arg);
    else if (!strcmp(kind, "pl")) snprintf(sql, sizeof sql, "select name from main.playlist where id = %d", atoi(arg));
    else if (!strcmp(kind, "year")) { snprintf(l->title, sizeof l->title, "%s", atoi(arg) ? arg : "Unknown year"); return; }
    else return;
    if (sqlite3_prepare_v2(db, sql, -1, &st, 0) != SQLITE_OK) return;
    if (sqlite3_step(st) == SQLITE_ROW && sqlite3_column_text(st, 0))
        snprintf(l->title, sizeof l->title, "%s", sqlite3_column_text(st, 0));
    sqlite3_finalize(st);
}

static int count_of(const char *sql) {
    sqlite3_stmt *st;
    int n = -1;
    if (sqlite3_prepare_v2(db, sql, -1, &st, 0) != SQLITE_OK) return -1;
    if (sqlite3_step(st) == SQLITE_ROW) n = sqlite3_column_int(st, 0);
    sqlite3_finalize(st);
    return n;
}

static uint8_t out[CRC_MAX_PAYLOAD];

/* the bucket filter on e (the list's rows) */
static void bucket_where(char *b, size_t cap, const list_t *l, const char *bucket) {
    if (!bucket) snprintf(b, cap, "1");
    else if (l->years) snprintf(b, cap, "decade(bk) = '%s'", bucket);
    else snprintf(b, cap, "bk is not null and prefix(bk, %d) = '%s'", (int)strlen(bucket), bucket);
}

static int browse(crc_session *s, unsigned req_id, const uint8_t *p, size_t n) {
    pbr r = {p, p + n};
    pbf f;
    char node[400] = "", sort[24] = "";
    unsigned offset = 0, limit = 50;
    int k;
    while ((k = pb_next(&r, &f)) > 0) {
        if (f.field == 1) pb_cstr(&f, node, sizeof node);
        if (f.field == 2) pb_cstr(&f, sort, sizeof sort);
        if (f.field == 3 && f.wire == 0) offset = (unsigned)f.v;
        if (f.field == 4 && f.wire == 0 && f.v) limit = (unsigned)f.v;
    }
    if (k < 0 || limit > 100 || offset > 1000000) return crc_error(s, req_id, E_BAD_REQUEST, "Browse");
    pbw w = {out, 0, sizeof out, 0};
    pb_str(&w, 1, node);
    if (!strcmp(node, "root") || !strcmp(node, "library")) {
        static const item_t lib[] = {
            {"tracks", "Tracks", 0, 0, 0, ICON_TRACKS_, 1, 0, 0, 0}, {"playlists", "Playlists", 0, 0, 0, ICON_PLAYLISTS_, 1, 0, 0, 0},
            {"artists", "All artists", 0, 0, 0, ICON_ARTISTS_, 1, 0, 0, 0}, {"albums", "Albums", 0, 0, 0, ICON_ALBUMS_, 1, 0, 0, 0},
            {"genres", "Genres", 0, 0, 0, ICON_GENRES_, 1, 0, 0, 0}, {"years", "Years", 0, 0, 0, ICON_YEARS_, 1, 0, 0, 0},
            {"dir:", "Folders", 0, 0, 0, ICON_FOLDERS_, 1, 0, 0, 0}};
        static const item_t top = {"library", "Library", 0, 0, 0, 0, 1, 0, 0, 0};
        int root_ = node[0] == 'r', total = root_ ? 1 : 7;
        pb_str(&w, 2, root_ ? "Q2" : "Library");
        pb_varint(&w, 3, total);
        pb_varint(&w, 4, offset);
        for (unsigned i = offset; i < (unsigned)total && i < offset + limit; ++i) put_item(&w, 5, root_ ? &top : &lib[i]);
        return crc_send(s, T_BROWSE_RESP, req_id, out, w.n);
    }
    /* base|sort|bucket (q2bridge.py: a bucket's id carries the order it was made in) */
    char base[400], *bucket = 0, *bar = strchr(node, '|');
    snprintf(base, sizeof base, "%s", node);
    if (bar) {
        base[bar - node] = 0;
        char *second = strchr(bar + 1, '|');
        if (!second) return crc_error(s, req_id, E_NOT_FOUND, "node");
        snprintf(sort, sizeof sort, "%.*s", (int)(second - bar - 1), bar + 1);
        bucket = second + 1;
        if (!arg_ok(bucket) || strlen(bucket) > 12) return crc_error(s, req_id, E_NOT_FOUND, "bucket");
    }
    if (lib_open()) return crc_error(s, req_id, E_UNAVAILABLE, "no library");
    list_t l;
    memset(&l, 0, sizeof l);
    if (!list_of(base, sort, &l)) {
        lib_close();
        return crc_error(s, req_id, E_NOT_FOUND, "node");
    }
    char kind[24];
    const char *colon = strchr(base, ':');
    snprintf(kind, sizeof kind, "%.*s", colon ? (int)(colon - base) : (int)strlen(base), base);
    title_of(kind, colon ? colon + 1 : "", &l);
    char bw[64];
    bucket_where(bw, sizeof bw, &l, bucket);
    snprintf(sql, sizeof sql, "select count(*) from (%s) where %s", l.src, bw);
    int total = count_of(sql), split = 0;
    if (total < 0) {
        crc_log("lib: %s", sqlite3_errmsg(db));
        lib_close();
        return crc_error(s, req_id, E_INTERNAL, "query");
    }
    /* An empty list under an id names nothing: albums, artists, genres, years, folders and buckets are made
     * from their songs, so only a playlist (when it exists) and the card's top folder can be empty. */
    if (!total && (bucket || (colon && strcmp(base, "dir:")))) {
        int none = 1;
        if (!bucket && !strcmp(kind, "pl")) {
            snprintf(sql, sizeof sql, "select count(*) from main.playlist where id = %d", atoi(colon + 1));
            none = count_of(sql) <= 0;
        }
        if (none) {
            lib_close();
            return crc_error(s, req_id, E_NOT_FOUND, "node");
        }
    }
    if (!bucket) split = l.bucketed && total > SPLIT;
    else split = l.bucketed && total > CAP && !l.years && strcmp(bucket, NO_LETTER) && !strchr(bucket, ' ') && strlen(bucket) < 3;
    /* "All tracks" leads an artist's or a genre's albums (q2bridge.py) */
    int lead = !bucket && (!strcmp(kind, "artist") || !strcmp(kind, "genre"));
    pb_str(&w, 2, l.title);
    sqlite3_stmt *st = 0;
    int got;
    if (split) {
        char bx[32];
        if (l.years) snprintf(bx, sizeof bx, "decade(bk)");
        else snprintf(bx, sizeof bx, "prefix(bk, %d)", bucket ? (int)strlen(bucket) + 1 : 1);
        snprintf(sql, sizeof sql, "select count(distinct %s) from (%s) where %s", bx, l.src, bw);
        int buckets = count_of(sql);
        /* buckets in the order their first entries come in the list's order */
        snprintf(sql, sizeof sql, "select %s b, count(*) from (select *, row_number() over (order by %s) rn from (%s)) "
                 "where %s group by b order by min(rn) limit %u offset %u", bx, l.order, l.src, bw, limit, offset);
        pb_varint(&w, 3, buckets < 0 ? 0 : buckets);
        pb_varint(&w, 4, offset);
        if (sqlite3_prepare_v2(db, sql, -1, &st, 0) != SQLITE_OK) goto fail;
        while ((got = sqlite3_step(st)) == SQLITE_ROW) {
            char id[480], t[16];
            const char *b = (const char *)sqlite3_column_text(st, 0);
            snprintf(id, sizeof id, "%s|%s|%s", base, l.sort, b);
            /* "B " reads "B", "BA" reads "Ba" (q2bridge.py: k.strip().title()) */
            int tn = 0;
            for (const char *c = b; *c && tn < 15; ++c)
                if (*c != ' ') t[tn] = tn && !l.years && alpha(*c) && strcmp(b, NO_LETTER) ? *c | 32 : *c, ++tn;
            t[tn] = 0;
            item_t it = {id, t, 0, 0, 0, 0, 1, 0, 0, 0};
            put_item(&w, 5, &it);
        }
        if (got != SQLITE_DONE) goto fail;
    } else {
        int shown = total > CAP ? CAP : total;
        pb_varint(&w, 3, shown + lead);
        pb_varint(&w, 4, offset);
        if (lead && offset == 0) {
            char id[420];
            snprintf(id, sizeof id, "%s-tracks:%s", kind, colon + 1);
            item_t it = {id, "All tracks", 0, 0, 0, ICON_TRACKS_, 1, 0, 0, 0};
            put_item(&w, 5, &it);
        }
        unsigned off = offset ? offset - lead : 0, lim = offset ? limit : limit - lead;
        if (off + lim > (unsigned)shown) lim = off < (unsigned)shown ? shown - off : 0;
        snprintf(sql, sizeof sql, "select * from (%s) where %s order by %s limit %u offset %u", l.src, bw, l.order, lim, off);
        if (sqlite3_prepare_v2(db, sql, -1, &st, 0) != SQLITE_OK || put_rows(&w, 5, st, 0) < 0) goto fail;
    }
    sqlite3_finalize(st);
    pb_str(&w, 6, l.sort);
    for (const sort_t *so = l.sorts; !bucket && so && so->key; ++so) {
        size_t m = pb_begin(&w, 7);
        pb_str(&w, 1, so->key);
        pb_str(&w, 2, so->label);
        pb_end(&w, m);
    }
    if (l.actions & 1) pb_varint(&w, 8, 1);
    if (l.actions & 2) pb_varint(&w, 8, 2);
    if (l.actions & 4) pb_varint(&w, 8, 3);
    lib_close();
    if (w.over) return crc_error(s, req_id, E_TOO_LARGE, "ask for fewer items");
    return crc_send(s, T_BROWSE_RESP, req_id, out, w.n);
fail:
    crc_log("lib: browse %s: %s", node, sqlite3_errmsg(db));
    sqlite3_finalize(st);
    lib_close();
    return crc_error(s, req_id, E_INTERNAL, "query");
}

/* ---- search ---- */

static int search(crc_session *s, unsigned req_id, const uint8_t *p, size_t n) {
    pbr r = {p, p + n};
    pbf f;
    char q[128] = "", nq[128];
    unsigned limit = 20;
    int k;
    while ((k = pb_next(&r, &f)) > 0) {
        if (f.field == 1) pb_cstr(&f, q, sizeof q);
        if (f.field == 2 && f.wire == 0 && f.v) limit = (unsigned)f.v;
    }
    if (k < 0 || limit > 50) return crc_error(s, req_id, E_BAD_REQUEST, "Search");
    norm(q, nq, sizeof nq);
    pbw w = {out, 0, sizeof out, 0};
    if (!*nq) return crc_send(s, T_SEARCH_RESP, req_id, out, 0);
    if (lib_open()) return crc_error(s, req_id, E_UNAVAILABLE, "no library");
    static const struct { const char *title, *from, *match, *cols, *order; } groups[] = {
        {"Tracks", "s2", "instr(nt, ?1) or instr(nar, ?1)", TRACK_ITEM, "nt"},
        {"Albums", "albums", "instr(nt, ?1)", ALBUM_ITEM, "nt"},
        {"Artists", "(select naa, max(aartist) aartist, min(ak) ak from s2 where naa != '' group by naa)", "instr(naa, ?1)",
         "'artist:r'||hkey(naa), aartist, '', ak, 0, 1, 0, 0, 0", "naa"}};
    for (unsigned g = 0; g < 3; ++g) {
        sqlite3_stmt *st;
        snprintf(sql, sizeof sql, "select count(*) from %s where %s", groups[g].from, groups[g].match);
        if (sqlite3_prepare_v2(db, sql, -1, &st, 0) != SQLITE_OK) goto fail;
        sqlite3_bind_text(st, 1, nq, -1, SQLITE_TRANSIENT);
        int total = sqlite3_step(st) == SQLITE_ROW ? sqlite3_column_int(st, 0) : 0;
        sqlite3_finalize(st);
        if (!total) continue;
        snprintf(sql, sizeof sql, "select %s from %s where %s order by %s limit %u", groups[g].cols, groups[g].from,
                 groups[g].match, groups[g].order, limit);
        if (sqlite3_prepare_v2(db, sql, -1, &st, 0) != SQLITE_OK) goto fail;
        sqlite3_bind_text(st, 1, nq, -1, SQLITE_TRANSIENT);
        size_t m = pb_begin(&w, 1);
        pb_str(&w, 1, groups[g].title);
        int e = put_rows(&w, 2, st, groups[g].title);
        pb_varint(&w, 3, total);
        pb_end(&w, m);
        sqlite3_finalize(st);
        if (e < 0) goto fail;
    }
    lib_close();
    if (w.over) return crc_error(s, req_id, E_TOO_LARGE, "ask for fewer results");
    return crc_send(s, T_SEARCH_RESP, req_id, out, w.n);
fail:
    crc_log("lib: search: %s", sqlite3_errmsg(db));
    lib_close();
    return crc_error(s, req_id, E_INTERNAL, "query");
}

/* ---- Play.resume: where each list played from a phone was left ---- */

/* The list the last Play started ("node#sort", empty after a shuffle or a single song) and its queue's
 * version once the shim took it; the song noted last; the key a Play in flight will start. */
static struct {
    char key[440], pending[440];
    int gen, id;
} cur;
static char places[PLACES_MAX * 452 + 1];

static int places_read(void) {
    char p[256];
    int fd = open(crc_path(p, sizeof p, PLACES), O_RDONLY | O_CLOEXEC), n = 0;
    while (fd >= 0 && n < (int)sizeof places - 1) {
        int r = (int)read(fd, places + n, sizeof places - 1 - n);
        if (r <= 0) break;
        n += r;
    }
    if (fd >= 0) close(fd);
    places[n] = 0;
    return n;
}

/* key's line in places, or 0 */
static char *place_line(const char *key) {
    size_t kn = strlen(key);
    for (char *line = places; *line;) {
        char *sp = strchr(line, ' '), *end = strchr(line, '\n');
        if (!end) break;
        if (sp && sp < end && (size_t)(end - sp - 1) == kn && !memcmp(sp + 1, key, kn)) return line;
        line = end + 1;
    }
    return 0;
}

static int place_of(const char *key) {
    places_read();
    char *line = place_line(key);
    return line ? atoi(line) : 0;
}

/* key's song is now id: its line first, the oldest past PLACES_MAX dropped */
static void place_put(const char *key, int id) {
    static char b[sizeof places + 460];
    places_read();
    char *old = place_line(key);
    int n = snprintf(b, sizeof b, "%d %s\n", id, key), lines = 1;
    for (char *line = places; *line && lines < PLACES_MAX; ++lines) {
        char *end = strchr(line, '\n');
        if (!end) break;
        if (line != old) memcpy(b + n, line, end + 1 - line), n += (int)(end + 1 - line);
        else --lines;
        line = end + 1;
    }
    char p[256];
    mkdir(crc_path(p, sizeof p, "/mnt/data/coda-rc"), 0700);
    crc_write_file(PLACES, b, n, 0600);
}

void lib_played(int ok, int qgen) {
    if (!ok) return; /* the queue the Q2 had plays on, and so does what it was */
    snprintf(cur.key, sizeof cur.key, "%s", cur.pending);
    cur.gen = qgen;
    cur.id = 0;
}

void lib_now(int qgen, int id) {
    if (!cur.key[0] || qgen != cur.gen || id <= 0 || id == cur.id) return;
    cur.id = id;
    place_put(cur.key, id);
}

/* ---- play: the list's song ids to the shim (coda_rc_shim.c), which queues them in demo ---- */

static int play(crc_session *s, unsigned req_id, const uint8_t *p, size_t n, int qgen) {
    pbr r = {p, p + n};
    pbf f;
    char node[400] = "", sort[24] = "", start[40] = "", key[440] = "";
    int shuffle = 0, resume = 0, k;
    while ((k = pb_next(&r, &f)) > 0) {
        if (f.field == 1) pb_cstr(&f, node, sizeof node);
        if (f.field == 2) pb_cstr(&f, sort, sizeof sort);
        if (f.field == 3) pb_cstr(&f, start, sizeof start);
        if (f.field == 4 && f.wire == 0) shuffle = f.v != 0;
        if (f.field == 5 && f.wire == 0) resume = f.v != 0;
    }
    if (k < 0 || !*node || strchr(node, '\n')) return crc_error(s, req_id, E_BAD_REQUEST, "Play");
    if (resume) start[0] = 0, shuffle = 0;
    if (!crc_shim_alive()) return crc_error(s, req_id, E_UNAVAILABLE, "demo is not answering");
    int one = lib_song_rid(node), want = start[0] ? lib_song_rid(start) : 0;
    if (start[0] && !want) return crc_error(s, req_id, E_BAD_REQUEST, "start_id");
    char *ids = malloc(PLAY_MAX * 11 + 1);
    if (!ids) return crc_error(s, req_id, E_INTERNAL, "memory");
    size_t len = 0;
    int count = 0, at = 0;
    if (one) {
        len = snprintf(ids, 16, "%d\n", one);
        count = 1;
    } else {
        char base[400], *bucket = 0, *bar;
        snprintf(base, sizeof base, "%s", node);
        if ((bar = strchr(base, '|'))) {
            *bar = 0;
            char *second = strchr(bar + 1, '|');
            if (!second) { free(ids); return crc_error(s, req_id, E_NOT_FOUND, "node"); }
            *second = 0;
            if (!*sort) snprintf(sort, sizeof sort, "%s", bar + 1);
            bucket = second + 1;
            if (!arg_ok(bucket)) { free(ids); return crc_error(s, req_id, E_NOT_FOUND, "bucket"); }
        }
        /* a list of lists plays the songs under it: an artist's or genre's albums are its tracks */
        char tracks_of[420];
        if (!strncmp(base, "artist:", 7) || !strncmp(base, "genre:", 6)) {
            const char *c = strchr(base, ':');
            snprintf(tracks_of, sizeof tracks_of, "%.*s-tracks%s", (int)(c - base), base, c);
            snprintf(base, sizeof base, "%s", tracks_of);
        } else if (!strcmp(base, "albums") || !strcmp(base, "artists") || !strcmp(base, "genres") ||
                   !strcmp(base, "years") || !strcmp(base, "library")) {
            snprintf(base, sizeof base, "tracks");
            bucket = 0;
        }
        if (lib_open()) { free(ids); return crc_error(s, req_id, E_UNAVAILABLE, "no library"); }
        list_t l;
        memset(&l, 0, sizeof l);
        int ok = list_of(base, sort, &l) && strcmp(base, "playlists") && strncmp(base, "year:", 5);
        if (!ok && !strncmp(base, "year:", 5)) {  /* a year's songs, album by album */
            snprintf(l.src, sizeof l.src, "select " TRACK_ITEM TAIL("null", "0") " from s2 where year = %d", atoi(base + 5));
            l.order = "nal, ak, ord";
            ok = 1;
        }
        if (!ok) { lib_close(); free(ids); return crc_error(s, req_id, E_NOT_FOUND, "not a playable list"); }
        if (!shuffle) snprintf(key, sizeof key, "%s#%s", node, l.sort);
        if (resume && cur.key[0] && !strcmp(key, cur.key) && qgen == cur.gen) {
            lib_close(); /* the Q2 still plays this list: it carries on */
            free(ids);
            return crc_shim_command(s, req_id, "resume");
        }
        if (resume) want = place_of(key);
        char bw[64];
        bucket_where(bw, sizeof bw, &l, bucket);
        snprintf(sql, sizeof sql, "select * from (%s) where %s order by %s limit %d", l.src, bw, l.order, PLAY_MAX);
        sqlite3_stmt *st;
        if (sqlite3_prepare_v2(db, sql, -1, &st, 0) != SQLITE_OK) {
            crc_log("lib: play %s: %s", node, sqlite3_errmsg(db));
            lib_close();
            free(ids);
            return crc_error(s, req_id, E_INTERNAL, "query");
        }
        while (sqlite3_step(st) == SQLITE_ROW) {
            int rid = lib_song_rid((const char *)sqlite3_column_text(st, 0));
            if (!rid) continue;  /* a folder row in dir: */
            if (rid == want) at = count;
            len += snprintf(ids + len, 12, "%d\n", rid);
            ++count;
        }
        sqlite3_finalize(st);
        lib_close();
    }
    if (!count) { free(ids); return crc_error(s, req_id, E_NOT_FOUND, "an empty list"); }
    int e = crc_write_file("/tmp/coda-rc.list", ids, len, 0600);
    free(ids);
    if (e) return crc_error(s, req_id, E_INTERNAL, "list");
    char verb[48];
    snprintf(verb, sizeof verb, "play %d %d %d", at, shuffle, count);
    snprintf(cur.pending, sizeof cur.pending, "%s", key);
    return crc_shim_command(s, req_id, verb);
}

/* ---- the queue: the shim's /tmp/coda-rc.queue, demo's queue as song ids (coda_rc_shim.c) ---- */

static int queue(crc_session *s, unsigned req_id, const uint8_t *p, size_t n, int qgen, int qpos) {
    pbr r = {p, p + n};
    pbf f;
    unsigned offset = 0, limit = 50;
    int k;
    while ((k = pb_next(&r, &f)) > 0) {
        if (f.field == 1 && f.wire == 0) offset = (unsigned)f.v;
        if (f.field == 2 && f.wire == 0 && f.v) limit = (unsigned)f.v;
    }
    if (k < 0 || limit > 100) return crc_error(s, req_id, E_BAD_REQUEST, "GetQueue");
    char path[256];
    int fd = open(crc_path(path, sizeof path, "/tmp/coda-rc.queue"), O_RDONLY | O_CLOEXEC);
    char *b = malloc(256 * 1024 + 1);
    int len = 0;
    while (fd >= 0 && b && len < 256 * 1024) {
        int got = (int)read(fd, b + len, 256 * 1024 - len);
        if (got <= 0) break;
        len += got;
    }
    if (fd >= 0) close(fd);
    if (!b) return crc_error(s, req_id, E_INTERNAL, "memory");
    b[len] = 0;
    /* "gen pos total\n" then a song id per line */
    unsigned gen = 0, pos = 0, total = 0;
    char *line = strchr(b, '\n');
    if (len) {
        char *e;
        gen = strtoul(b, &e, 10);
        pos = strtoul(e, &e, 10);
        total = strtoul(e, &e, 10);
    }
    pbw w = {out, 0, sizeof out, 0};
    pb_varint(&w, 1, len ? gen : (unsigned)qgen);
    /* the queue file keeps the place it had when written; Now Playing's is current while both name this queue */
    pb_varint(&w, 2, len && gen == (unsigned)qgen ? (unsigned)qpos : pos);
    pb_varint(&w, 3, total);
    pb_varint(&w, 4, offset);
    if (line && offset < total && !lib_open()) {
        sqlite3_stmt *st;
        if (sqlite3_prepare_v2(db, "select " TRACK_ITEM " from s2 where rid = ?1", -1, &st, 0) == SQLITE_OK) {
            unsigned i = 0;
            for (char *c = line + 1; *c && i < offset + limit; ++i) {
                char *end = strchr(c, '\n');
                if (i >= offset) {
                    sqlite3_bind_int(st, 1, atoi(c));
                    if (put_rows(&w, 5, st, 0) == 0) {  /* gone from the library since it was queued */
                        item_t it = {"", "[Not in the library]", 0, 0, 0, 0, 0, 0, 0, 0};
                        put_item(&w, 5, &it);
                    }
                    sqlite3_reset(st);
                }
                if (!end) break;
                c = end + 1;
            }
            sqlite3_finalize(st);
        }
        lib_close();
    }
    free(b);
    if (w.over) return crc_error(s, req_id, E_TOO_LARGE, "ask for fewer items");
    return crc_send(s, T_QUEUE_RESP, req_id, out, w.n);
}

/* ---- covers ---- */

/* Where an album's picture is: Cover Flow's 160 px cache when it is small enough, a server app's cover,
 * a picture file in the folder, or the first FLAC's embedded picture (q2bridge.py cover). */
typedef struct {
    char path[400];
    long off, len;
    int png, px;
} cover_t;

static long file_len(const char *path) {
    int fd = open(path, O_RDONLY | O_CLOEXEC);
    if (fd < 0) return -1;
    long n = lseek(fd, 0, 2);
    close(fd);
    return n;
}

static int be32(const uint8_t *b) { return b[0] << 24 | b[1] << 16 | b[2] << 8 | b[3]; }

/* a FLAC's first PICTURE block: where its image bytes are */
static int flac_picture(const char *path, cover_t *c) {
    int fd = open(path, O_RDONLY | O_CLOEXEC);
    if (fd < 0) return 0;
    uint8_t h[8];
    long at = 4;
    int found = 0;
    if (read(fd, h, 4) == 4 && !memcmp(h, "fLaC", 4)) {
        for (int blocks = 0; blocks < 64; ++blocks) {
            if (lseek(fd, at, 0) != at || read(fd, h, 4) != 4) break;
            long size = h[1] << 16 | h[2] << 8 | h[3];
            if ((h[0] & 127) == 6) {
                uint8_t m[8];
                long p = at + 8;  /* past the block header and the picture type */
                if (lseek(fd, p, 0) != p || read(fd, m, 4) != 4) break;
                long mime = be32(m);
                char mt[16] = "";
                if (mime < 0 || mime > 64) break;
                if (read(fd, mt, mime < 15 ? mime : 15) < 0) break;
                c->png = !strncmp(mt, "image/png", 9);
                p += 4 + mime;
                if (lseek(fd, p, 0) != p || read(fd, m, 4) != 4) break;
                p += 4 + be32(m) + 16;  /* the description, then width, height, depth and colours */
                if (lseek(fd, p, 0) != p || read(fd, m, 4) != 4) break;
                c->off = p + 4;
                c->len = be32(m);
                found = c->len > 0 && c->off + c->len <= at + 4 + size;
                break;
            }
            if (h[0] & 128) break;  /* the last metadata block */
            at += 4 + size;
        }
    }
    close(fd);
    return found;
}

static int cover_find(const char *art, int max_px, cover_t *c) {
    sqlite3_stmt *st;
    char url[400] = "", aartist[256] = "", album[256] = "", artist[256] = "";
    memset(c, 0, sizeof *c);
    if (sqlite3_prepare_v2(db, "select url, aartist, album, artist from s2 where ak = ?1 order by ord limit 1", -1, &st, 0))
        return 0;
    sqlite3_bind_text(st, 1, art, -1, SQLITE_TRANSIENT);
    if (sqlite3_step(st) == SQLITE_ROW) {
        snprintf(url, sizeof url, "%s", sqlite3_column_text(st, 0));
        snprintf(aartist, sizeof aartist, "%s", sqlite3_column_text(st, 1));
        snprintf(album, sizeof album, "%s", sqlite3_column_text(st, 2));
        snprintf(artist, sizeof artist, "%s", sqlite3_column_text(st, 3));
    }
    sqlite3_finalize(st);
    if (!*url) return 0;
    char p[512];
    /* Cover Flow's cache (coverflow.c art_path): 160 px, keyed by the album's artist and its name;
     * an empty file marks an album with no art */
    if (max_px <= 160) {
        const char *who[] = {aartist, artist};
        for (int i = 0; i < 2; ++i) {
            char rel[64];
            snprintf(rel, sizeof rel, "/mnt/mmc/.coverflow/%08x.jpg", fnv32(fnv32(2166136261u, who[i]), album));
            crc_path(p, sizeof p, rel);
            long n = file_len(p);
            if (n > 0) { snprintf(c->path, sizeof c->path, "%s", p); c->len = n; c->px = 160; return 1; }
        }
    }
    const char *a, *app;
    size_t an, appn;
    if (server_album(url, &a, &an, &app, &appn)) {
        char rel[160];
        snprintf(rel, sizeof rel, "/mnt/mmc/%.*s/covers/%.*s.jpg", (int)appn, app, (int)an, a);
        crc_path(p, sizeof p, rel);
        long n = file_len(p);
        if (n > 0) { snprintf(c->path, sizeof c->path, "%s", p); c->len = n; return 1; }
        return 0;
    }
    static const char *names[] = {"cover.jpg", "folder.jpg", "front.jpg", "cover.png", "folder.png"};
    const char *slash = strrchr(url, '/');
    for (unsigned i = 0; slash && i < 5; ++i) {
        char rel[460];
        snprintf(rel, sizeof rel, "%.*s/%s", (int)(slash - url), url, names[i]);
        crc_path(p, sizeof p, rel);
        long n = file_len(p);
        if (n > 0) { snprintf(c->path, sizeof c->path, "%s", p); c->len = n; c->png = i > 2; return 1; }
    }
    crc_path(p, sizeof p, url);
    if (flac_picture(p, c)) { snprintf(c->path, sizeof c->path, "%s", p); return 1; }
    return 0;
}

static int cover(crc_session *s, unsigned req_id, const uint8_t *p, size_t n) {
    pbr r = {p, p + n};
    pbf f;
    char art[48] = "";
    unsigned offset = 0, limit = 4096, max_px = 0;
    int k;
    while ((k = pb_next(&r, &f)) > 0) {
        if (f.field == 1) pb_cstr(&f, art, sizeof art);
        if (f.field == 2 && f.wire == 0) offset = (unsigned)f.v;
        if (f.field == 3 && f.wire == 0 && f.v) limit = (unsigned)f.v;
        if (f.field == 4 && f.wire == 0) max_px = (unsigned)f.v;
    }
    if (k < 0 || limit > 4096 || !*art) return crc_error(s, req_id, E_BAD_REQUEST, "GetCover");
    if (lib_open()) return crc_error(s, req_id, E_UNAVAILABLE, "no library");
    cover_t c;
    int found = cover_find(art, max_px ? (int)max_px : 160, &c);
    lib_close();
    if (!found) return crc_error(s, req_id, E_NOT_FOUND, "no cover");
    if (c.len > 16 * 1024 * 1024 || (long)offset > c.len) return crc_error(s, req_id, E_BAD_REQUEST, "offset");
    unsigned take = c.len - offset < limit ? c.len - offset : limit;
    uint8_t *data = malloc(take + 1);
    int fd = open(c.path, O_RDONLY | O_CLOEXEC), ok = 0;
    if (data && fd >= 0 && lseek(fd, c.off + offset, 0) == c.off + (long)offset) {
        unsigned got = 0;
        while (got < take) {
            ssize_t rr = read(fd, data + got, take - got);
            if (rr <= 0) break;
            got += rr;
        }
        ok = got == take;
    }
    if (fd >= 0) close(fd);
    if (!ok) { free(data); return crc_error(s, req_id, E_INTERNAL, "read"); }
    pbw w = {out, 0, sizeof out, 0};
    pb_varint(&w, 1, c.len);
    pb_varint(&w, 2, offset);
    pb_bytes(&w, 3, data, take);
    pb_str(&w, 4, c.png ? "image/png" : "image/jpeg");
    pb_varint(&w, 5, fnv32(fnv32(2166136261u, c.path), "") ^ (unsigned)c.len ^ (unsigned)c.off);
    if (c.px) { pb_varint(&w, 6, c.px); pb_varint(&w, 7, c.px); }
    free(data);
    return crc_send(s, T_COVER, req_id, out, w.n);
}

int lib_request(crc_session *s, int type, unsigned req_id, const uint8_t *p, size_t n, int qgen, int qpos) {
    switch (type) {
    case T_BROWSE: return browse(s, req_id, p, n);
    case T_SEARCH: return search(s, req_id, p, n);
    case T_PLAY: return play(s, req_id, p, n, qgen);
    case T_GET_QUEUE: return queue(s, req_id, p, n, qgen, qpos);
    case T_GET_COVER: return cover(s, req_id, p, n);
    }
    return crc_error(s, req_id, E_UNSUPPORTED, "unknown type");
}
