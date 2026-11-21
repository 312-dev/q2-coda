/* q2-iap's library (docs/iphone-mode.md#the-library-on-idrive): the Q2's songs as the car's media library, read from
 * stock's database (songtable, the songs coda_rc_lib.c's views read: audiobooks and podcasts left out) into RAM at
 * the car's StartMediaLibraryUpdates, and the record of what the car was last sent in full (LIB_SENT), so a car that
 * holds that revision is sent only what changed. Included by q2_iap.c; q2_iap2.c reads the tables through its host
 * (struct iap2_library) and never this file's I/O. The database is opened read-only and closed after the read. */

#define LIB_DB "/mnt/data/database.db"
#define LIB_CAT "/mnt/mmc/.q2pod/catalogue.db" /* a signed-in server's songs (docs/server-library.md) */
#define LIB_SENT "/mnt/data/q2-iap.library" /* "rev\n" then "rid hash\n" a song, in rid order */
#define LIB_WHERE                                                                                                     \
    "fileurl not like '/mnt/mmc/audiobooks/%' and fileurl not like '/mnt/mmc/podcasts/%' and fileurl not like '%.m4b'"

/* SQLite: the rootfs's libsqlite3.so.0 (and the host's for test/iap_probe.py), declared here as coda_rc_sys.h does */
typedef struct sqlite3 sqlite3;
typedef struct sqlite3_stmt sqlite3_stmt;
int sqlite3_open_v2(const char *, sqlite3 **, int, const char *);
int sqlite3_close(sqlite3 *);
int sqlite3_prepare_v2(sqlite3 *, const char *, int, sqlite3_stmt **, const char **);
int sqlite3_step(sqlite3_stmt *);
int sqlite3_finalize(sqlite3_stmt *);
const unsigned char *sqlite3_column_text(sqlite3_stmt *, int);
int sqlite3_column_int(sqlite3_stmt *, int);
const char *sqlite3_errmsg(sqlite3 *);
int sqlite3_exec(sqlite3 *, const char *, int (*)(void *, int, char **, char **), void *, char **);
#define SQLITE_OPEN_READONLY 1
#define SQLITE_ROW 100

static const char *lib_db = LIB_DB, *lib_cat = LIB_CAT, *lib_sent_file = LIB_SENT;
static struct iap2_library lib_t; /* the tables q2_iap2.c reads */

static unsigned lib_fnv(unsigned h, const char *s) {
    while (*s) h = (h ^ (unsigned char)*s++) * 16777619u;
    return h * 16777619u; /* a separator, so "ab"+"c" is not "a"+"bc" */
}

static unsigned lib_put(char **pool, unsigned *len, unsigned *cap, const char *s) {
    unsigned n = strlen(s) + 1, at = *len;
    if (at + n > *cap) {
        unsigned c = *cap ? *cap * 2 : 65536;
        while (at + n > c) c *= 2;
        char *p = realloc(*pool, c);
        if (!p) return 0; /* offset 0 is the pool's empty string */
        *pool = p, *cap = c;
    }
    memcpy(*pool + at, s, n);
    *len += n;
    return at;
}

static void lib_free(void) {
    free(lib_t.tracks), free(lib_t.pool), free(lib_t.sent);
    memset(&lib_t, 0, sizeof lib_t);
}

/* What the car was sent in full last: its revision and each song's rid and hash. */
static void lib_read_sent(void) {
    int fd = open(lib_sent_file, O_RDONLY);
    if (fd < 0) return;
    unsigned cap = 0, len = 0;
    char *b = 0;
    for (;;) {
        if (len + 4096 > cap) {
            char *p = realloc(b, cap = cap ? cap * 2 : 65536);
            if (!p) break;
            b = p;
        }
        int r = (int)read(fd, b + len, cap - len - 1);
        if (r <= 0) break;
        len += r;
    }
    close(fd);
    if (!b) return;
    b[len] = 0;
    char *line = strchr(b, '\n');
    if (line) {
        *line++ = 0;
        snprintf(lib_t.sent_rev, sizeof lib_t.sent_rev, "%s", b);
        unsigned n = 0;
        for (char *c = line; *c; ++n) c = strchr(c, '\n') ? strchr(c, '\n') + 1 : c + strlen(c);
        lib_t.sent = malloc((n + 1) * sizeof *lib_t.sent);
        for (char *c = line; lib_t.sent && *c && lib_t.sent_n < n;) {
            char *sp = strchr(c, ' '), *nl = strchr(c, '\n');
            if (sp && (!nl || sp < nl)) {
                lib_t.sent[lib_t.sent_n].rid = (unsigned)atoi(c);
                lib_t.sent[lib_t.sent_n++].hash = (unsigned)strtoul(sp + 1, 0, 16);
            }
            c = nl ? nl + 1 : c + strlen(c);
        }
    }
    free(b);
}

/* The columns read of a song, songtable's and the catalogue's servertable's alike (stock's schema, which servertable
 * copies): the rid, title, album, artist, album artist, genre, composer, track, disc and, for a server song, its
 * length in seconds from servermeta (a card song's is not in the database: 0, none sent) */
#define LIB_COLS(t)                                                                                                   \
    "select " t ".id, ifnull(nullif(" t ".name,''),ifnull(" t ".filename,'')), ifnull(" t ".album,''), ifnull("       \
    t ".songer,''), ifnull(nullif(" t ".albumsonger,''),ifnull(" t ".songer,'')), ifnull(" t ".style,''), ifnull(" t  \
    ".composer,''), ifnull(" t ".track,0), ifnull(" t ".cd_num,0), "

/* The songs into RAM, in rid order, each with the hash of what the car is sent of it; the revision their hashes'
 * hash. The card's songs (songtable) and, while a server is signed in (its catalogue on the card, as Car Remote's
 * library attaches it: coda_rc_lib.c lib_open), the server's (servertable, rids from 0x40000000): on the Q2 of the
 * 2026-11-20 drive songtable was empty and every song the server's, 9,199 of them, and iDrive got none. 0, or -1 with
 * no database. */
static int lib_load(void) {
    sqlite3 *db = 0;
    sqlite3_stmt *st = 0;
    char *pool = 0, sql[900];
    unsigned len = 1, cap = 0, n = 0, tcap = 0, rev = 2166136261u;
    lib_free();
    int server = 0;
    if (sqlite3_open_v2(lib_db, &db, SQLITE_OPEN_READONLY, 0) == 0 && !access(lib_cat, 0) && !strchr(lib_cat, '\'')) {
        snprintf(sql, sizeof sql, "attach '%s' as cat", lib_cat);
        server = sqlite3_exec(db, sql, 0, 0, 0) == 0;
    }
    snprintf(sql, sizeof sql, "%s%s%s", LIB_COLS("songtable") "0 from main.songtable where " LIB_WHERE,
             server ? " union all " LIB_COLS("s") "ifnull(m.seconds,0) from cat.servertable s left join cat.servermeta m"
                      " on m.fileurl = s.fileurl"
                    : "",
             " order by 1");
    if (!db || sqlite3_prepare_v2(db, sql, -1, &st, 0) != 0) {
        char msg[200];
        snprintf(msg, sizeof msg, "library: no songs read (%s)", db ? sqlite3_errmsg(db) : "no database");
        logline("lib", msg);
        if (db) sqlite3_close(db);
        return -1;
    }
    pool = malloc(cap = 65536);
    if (pool) pool[0] = 0;
    while (pool && sqlite3_step(st) == SQLITE_ROW) {
        if (n == tcap) {
            struct iap2_track *t = realloc(lib_t.tracks, (tcap = tcap ? tcap * 2 : 1024) * sizeof *t);
            if (!t) break;
            lib_t.tracks = t;
        }
        struct iap2_track *t = &lib_t.tracks[n];
        const char *f[6];
        for (int k = 0; k < 6; ++k) f[k] = (const char *)sqlite3_column_text(st, 1 + k), f[k] = f[k] ? f[k] : "";
        t->rid = (unsigned)sqlite3_column_int(st, 0);
        t->track = (unsigned short)sqlite3_column_int(st, 7), t->disc = (unsigned short)sqlite3_column_int(st, 8);
        t->ms = (unsigned)sqlite3_column_int(st, 9) * 1000u;
        t->title = lib_put(&pool, &len, &cap, f[0]), t->album = lib_put(&pool, &len, &cap, f[1]);
        t->artist = lib_put(&pool, &len, &cap, f[2]), t->aartist = lib_put(&pool, &len, &cap, f[3]);
        t->genre = lib_put(&pool, &len, &cap, f[4]), t->composer = lib_put(&pool, &len, &cap, f[5]);
        unsigned h = 2166136261u;
        for (int k = 0; k < 6; ++k) h = lib_fnv(h, f[k]);
        t->hash = (h ^ t->track * 131u ^ t->disc * 65537u ^ t->ms) * 16777619u;
        t->album_id = lib_fnv(lib_fnv(2166136261u, f[3]), f[1]); /* an album is its album artist's */
        t->artist_id = lib_fnv(2166136261u, f[2]), t->aartist_id = lib_fnv(2166136261u, f[3]);
        t->genre_id = lib_fnv(2166136261u, f[4]), t->composer_id = lib_fnv(2166136261u, f[5]);
        rev = (rev ^ t->rid) * 16777619u, rev = (rev ^ t->hash) * 16777619u;
        if (t->rid) ++n;
    }
    sqlite3_finalize(st);
    sqlite3_close(db);
    lib_t.pool = pool, lib_t.n = n;
    snprintf(lib_t.rev, sizeof lib_t.rev, "q2.%u.%08x", n, rev);
    lib_read_sent();
    char msg[240];
    snprintf(msg, sizeof msg, "library: %u songs read (%s), revision %s; the car was last sent %s (%u songs)", n,
             server ? "the card's and the server's" : "the card's", lib_t.rev, lib_t.sent_rev[0] ? lib_t.sent_rev : "nothing",
             lib_t.sent_n);
    logline("lib", msg);
    return 0;
}

/* The car has all of this revision: what it holds, for the next connection's changes alone. */
static void lib_saved(void) {
    char tmp[200];
    snprintf(tmp, sizeof tmp, "%s.iap", lib_sent_file);
    int fd = open(tmp, O_WRONLY | O_CREAT | O_TRUNC, 0600), ok = fd >= 0;
    char b[32];
    if (ok) ok = write(fd, lib_t.rev, strlen(lib_t.rev)) == (int)strlen(lib_t.rev) && write(fd, "\n", 1) == 1;
    for (unsigned i = 0; ok && i < lib_t.n; ++i) {
        int k = snprintf(b, sizeof b, "%u %08x\n", lib_t.tracks[i].rid, lib_t.tracks[i].hash);
        ok = write(fd, b, k) == k;
    }
    if (fd >= 0) close(fd);
    if (!ok || rename(tmp, lib_sent_file)) unlink(tmp), logline("lib", "library: the sent record could not be written");
}

/* Songs to play, by rid: the shim's list (/tmp/coda-rc.list, a rid a line) and its play command, as coda-rc's Play
 * sends them (coda_rc_lib.c play). */
static const char *list_file = "/tmp/coda-rc.list";
static void lib_play(const unsigned *rids, unsigned n, unsigned at, int shuffle) {
    char cmd[48];
    char *b = n ? malloc(n * 12 + 1) : 0;
    unsigned len = 0;
    for (unsigned i = 0; b && i < n; ++i) len += (unsigned)snprintf(b + len, 12, "%u\n", rids[i]);
    if (!b || write_whole(list_file, b, len)) {
        logline("lib", "play: the list could not be written");
        free(b);
        return;
    }
    free(b);
    snprintf(cmd, sizeof cmd, "play %u %d %u", at < n ? at : 0, shuffle, n);
    io_command(cmd);
}

/* The shim's queue (/tmp/coda-rc.queue: "gen pos total" then a rid a line, 0 for a song outside the library): up to
 * cap rids into out; the count, or -1 when there is no queue file or it lists fewer songs than it has. */
static const char *queue_file = "/tmp/coda-rc.queue";
static int lib_queue(unsigned *out, unsigned cap) {
    unsigned char *b = malloc(cap * 12 + 64);
    unsigned got = b ? read_file(queue_file, b, cap * 12 + 63) : 0;
    int n = -1;
    if (got) {
        b[got] = 0;
        unsigned total = 0;
        char *c = strchr((char *)b, '\n');
        const char *e = (const char *)b;
        for (int k = 0; k < 3; ++k) {
            while (*e == ' ') ++e;
            if (k == 2) total = (unsigned)atoi(e);
            while (*e && *e != ' ' && *e != '\n') ++e;
        }
        for (n = 0; c && *++c && (unsigned)n < cap;) {
            out[n++] = (unsigned)atoi(c);
            c = strchr(c, '\n');
        }
        if ((unsigned)n != total) n = -1;
    }
    free(b);
    return n;
}
