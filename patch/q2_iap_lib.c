/* q2-iap's library (docs/iphone-mode.md#the-library-on-idrive): the Q2's songs as the car's media library, read from
 * stock's database (songtable, the songs coda_rc_lib.c's views read: audiobooks and podcasts left out) with the lists
 * it sends as playlists (lib_lists) into RAM at the car's StartMediaLibraryUpdates, and the record of what the car was
 * last sent in full (LIB_SENT), so a car that holds that revision is sent only what changed. q2_iap.c hands the
 * tables to the iAP2 side as its host's (struct iap2_library), which never does this file's I/O. The databases are
 * read in a child (lib_load), opened read-only and closed after the read. */
#include "q2_iap_sys.h"
#include "iap2.h"
#include "q2_iap.h"
#include "q2_iap_lib.h"
#include "stream_path.h"

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

const char *lib_db = LIB_DB, *lib_cat = LIB_CAT, *lib_sent_file = LIB_SENT;
const char *card_root = ""; /* put before a catalogue row's /mnt/mmc path (--card-root, for the host tests) */
struct iap2_library lib_t; /* the tables the iAP2 side reads (its host's lib) */


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

static unsigned char *lib_blob; /* the tables lib_t points into when the child read them (lib_install): one block */
static void lib_free(void) {
    if (lib_blob) free(lib_blob), lib_blob = 0; /* the child's tables: one block */
    else free(lib_t.tracks), free(lib_t.pool), free(lib_t.sent), free(lib_t.lists), free(lib_t.lrids);
    memset(&lib_t, 0, sizeof lib_t);
}

/* What the car was sent in full last: its revision and each song's rid and hash. */
static void lib_read_sent(void) {
    int fd = open(lib_sent_file, O_RDONLY | O_CLOEXEC);
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

/* The lists the car gets as playlists, each song by its rid in the library: Favorites (stock's My Fav, by path, as
 * Car Remote's favorites), Recently Played (stock's recentplay, the last played first), then stock's playlists (a
 * header row of id 0 to 100 names one; its songs are the rows above 100 whose cd_num is that id, in row order, as
 * coda_rc_lib.c lists them). A list none of whose songs is in the library (stock's Audiobooks) is left out. Stock's
 * database always has the three tables; without them there are no lists. */
static void lib_lists(sqlite3 *db, int server, char **pool, unsigned *len, unsigned *cap) {
    sqlite3_stmt *st = 0;
    char sql[1400];
    snprintf(sql, sizeof sql,
             "with s(rid, url) as (select id, fileurl from main.songtable where %s%s) "
             "select %d, 'Favorites', s.rid, 0, f.id from main.myfav f join s on s.url = f.fileurl "
             "union all select %d, 'Recently Played', s.rid, 1, -r.id from main.recentplay r join s on s.url = r.fileurl "
             "union all select h.id, ifnull(h.name,''), s.rid, 2, p.id from main.playlist h join main.playlist p "
             "on p.cd_num = h.id and p.id > 100 join s on s.url = p.fileurl where h.id <= 100 order by 4, 1, 5",
             LIB_WHERE, server ? " union all select id, fileurl from cat.servertable" : "", LIST_FAVORITES, LIST_RECENT);
    if (sqlite3_prepare_v2(db, sql, -1, &st, 0) != 0) {
        char msg[200];
        snprintf(msg, sizeof msg, "library: no lists (%s)", sqlite3_errmsg(db));
        logline("lib", msg);
        return;
    }
    unsigned cap_l = 0, cap_r = 0, nr = 0;
    while (sqlite3_step(st) == SQLITE_ROW) {
        unsigned id = (unsigned)sqlite3_column_int(st, 0), rid = (unsigned)sqlite3_column_int(st, 2);
        if (nr == cap_r) {
            unsigned *r = realloc(lib_t.lrids, (cap_r = cap_r ? cap_r * 2 : 1024) * sizeof *r);
            if (!r) break;
            lib_t.lrids = r;
        }
        if (!lib_t.nlists || lib_t.lists[lib_t.nlists - 1].id != id) {
            if (lib_t.nlists == cap_l) {
                struct iap2_list *l = realloc(lib_t.lists, (cap_l = cap_l ? cap_l * 2 : 16) * sizeof *l);
                if (!l) break;
                lib_t.lists = l;
            }
            const char *name = (const char *)sqlite3_column_text(st, 1);
            struct iap2_list *l = &lib_t.lists[lib_t.nlists++];
            l->id = id, l->name = lib_put(pool, len, cap, name ? name : ""), l->at = nr, l->n = 0;
        }
        lib_t.lrids[nr++] = rid, ++lib_t.lists[lib_t.nlists - 1].n;
    }
    sqlite3_finalize(st);
}

/* Server songs played from their app's stream cache (/mnt/mmc/.<app>/stream/.../<title> [<sid>].<suffix>,
 * stream_path.h) by their library rid: the shim names such a song by rid 0 (no record of the library has its path),
 * though the library the car was sent has it as its server row, servermeta's id for that app and sid. On
 * 2026-11-22 every queue was such songs and Up Next went to the car as "no list", and Now Playing named the song by a
 * hash. The rows are found in an index in RAM, sorted by the 64-bit FNV-1a of "<app> <sid>", 12 bytes a song (110 KB
 * for the owner's 9,199): a lookup reads nothing. A read of servermeta at each change holds the loop 102 to 664 ms
 * (that drive's q2car.1.log: 16 of its 21 stalls). lib_load fills it from
 * the rows it reads anyway, so it is the library the car was sent; before that, a child started with the cable
 * reads it from the catalogue (sid_index_start) and hands it over a pipe, and until it has a song is not found. */
struct lib_sid {
    unsigned hi, lo, rid;
};
struct lib_sids sids = {.fd = -1};
#define HANDOVER_MAX 65536 /* bytes of a child's tables taken in a loop pass at most: never O(library) at once */

static void sid_key(const char *app, int appn, const char *sid, int sidn, unsigned *hi, unsigned *lo) {
    unsigned long long h = 14695981039346656037ull;
    for (int i = 0; i < appn; ++i) h = (h ^ (unsigned char)app[i]) * 1099511628211ull;
    h = (h ^ ' ') * 1099511628211ull;
    for (int i = 0; i < sidn; ++i) h = (h ^ (unsigned char)sid[i]) * 1099511628211ull;
    *hi = (unsigned)(h >> 32), *lo = (unsigned)h;
}
static int sid_cmp(const void *a, const void *b) {
    const struct lib_sid *x = a, *y = b;
    return x->hi != y->hi ? (x->hi < y->hi ? -1 : 1) : x->lo != y->lo ? (x->lo < y->lo ? -1 : 1) : 0;
}
/* one row into v (n of cap, grown); 0 when there was no room */
static int sid_add(struct lib_sid **v, unsigned *n, unsigned *cap, const char *app, const char *sid, unsigned rid) {
    if (!app || !sid || !rid) return 1;
    if (*n == *cap) {
        struct lib_sid *w = realloc(*v, (*cap = *cap ? *cap * 2 : 4096) * sizeof *w);
        if (!w) return 0;
        *v = w;
    }
    struct lib_sid *s = &(*v)[(*n)++];
    sid_key(app, (int)strlen(app), sid, (int)strlen(sid), &s->hi, &s->lo);
    s->rid = rid;
    return 1;
}
/* v (n rows) sorted, unless they are, and made the index, in place of the one before */
/* v (n rows, sorted) made the index, in place of the one before. block: the allocation v lies in, freed with the
 * next index (0: v lies in the library's block, lib_install, and goes with it). O(1): the sort and the copies are
 * the children's. */
static void sid_install(struct lib_sid *v, unsigned n, void *block) {
    free(sids.block);
    sids.block = block, sids.v = n ? v : 0, sids.n = n;
}

/* The child that reads the catalogue's (app, sid, id) rows for the index and sorts them: on the pipe their count,
 * then each as 12 bytes (sid_key's halves and the rid, in this machine's order), the loop taking them in as they
 * come (sid_index_poll). */
void sid_index_start(void) {
    int fds[2];
    if (sids.asked || sids.n || access(lib_cat, 0)) return;
    sids.asked = 1;
    if (pipe_cloexec(fds)) return;
    int pid = fork();
    if (pid == 0) {
        sqlite3 *db = 0;
        sqlite3_stmt *st = 0;
        child_drop();
        close(fds[0]);
        struct lib_sid *v = 0;
        unsigned n = 0, cap = 0;
        nice(10);
        if (!sqlite3_open_v2(lib_cat, &db, SQLITE_OPEN_READONLY, 0) &&
            !sqlite3_prepare_v2(db, "select app, sid, id from servermeta where sid is not null", -1, &st, 0))
            while (sqlite3_step(st) == SQLITE_ROW)
                if (!sid_add(&v, &n, &cap, (const char *)sqlite3_column_text(st, 0),
                             (const char *)sqlite3_column_text(st, 1), (unsigned)sqlite3_column_int(st, 2)))
                    _exit(1);
        if (v) qsort(v, n, sizeof *v, sid_cmp); /* here, so the loop only takes them in */
        if (lib_write_all(fds[1], &n, sizeof n) || lib_write_all(fds[1], v, n * sizeof *v)) _exit(1);
        _exit(0);
    }
    close(fds[1]);
    if (pid < 0) {
        close(fds[0]);
        return;
    }
    fcntl(fds[0], F_SETFL, O_NONBLOCK);
    sids.fd = fds[0];
}

/* The child's rows taken in, HANDOVER_MAX bytes a pass at most, into room for exactly the count it sent first; once
 * all are in, they are the index, unless lib_load's has come meanwhile. */
void sid_index_poll(void) {
    unsigned took = 0;
    int r = 1;
    while (sids.fd >= 0 && took < HANDOVER_MAX) {
        unsigned need = sids.want ? sids.want : sizeof(unsigned);
        if (sids.in_n == need) break; /* all in */
        if (!sids.in || sids.in_n == sizeof(unsigned)) { /* room for the count, then for exactly what it says */
            unsigned char *w = realloc(sids.in, need);
            if (!w) break;
            sids.in = w;
        }
        r = (int)read(sids.fd, sids.in + sids.in_n, need - sids.in_n < HANDOVER_MAX - took ? need - sids.in_n : HANDOVER_MAX - took);
        if (r <= 0) break;
        sids.in_n += (unsigned)r, took += (unsigned)r;
        if (!sids.want && sids.in_n == sizeof(unsigned)) {
            unsigned n;
            memcpy(&n, sids.in, sizeof n);
            sids.want = n < (64u << 20) / sizeof(struct lib_sid) ? sizeof n + n * sizeof(struct lib_sid) : 0;
            if (!sids.want) break;
            if (sids.want == sizeof n) break; /* none */
        }
    }
    if (sids.fd < 0) return;
    int whole = sids.want && sids.in_n == sids.want;
    if (!whole && (took >= HANDOVER_MAX || (r < 0 && (ERRNO == EAGAIN || ERRNO == EINTR)))) return; /* more to come */
    close(sids.fd), sids.fd = -1;
    unsigned n = whole ? (sids.want - (unsigned)sizeof(unsigned)) / sizeof(struct lib_sid) : 0;
    if (whole && n && !sids.n) {
        char msg[120];
        sid_install((struct lib_sid *)(sids.in + sizeof(unsigned)), n, sids.in);
        snprintf(msg, sizeof msg, "library: %u server songs found by their stream path, from the catalogue", sids.n);
        logline("lib", msg);
    } else {
        free(sids.in);
    }
    sids.in = 0, sids.in_n = sids.want = 0;
}

/* Each of n paths (0: none) whose rids[i] is 0 looked for in the index; the count found. */
unsigned lib_stream_rids(const char *const *paths, unsigned *rids, unsigned n) {
    unsigned found = 0;
    sid_index_poll();
    sid_index_start();
    for (unsigned i = 0; sids.n && i < n; ++i) {
        const char *app, *sid;
        int appn, sidn;
        struct lib_sid want;
        if (rids[i] || !paths[i] || !stream_path_sid(paths[i], &app, &appn, &sid, &sidn)) continue;
        sid_key(app, appn, sid, sidn, &want.hi, &want.lo);
        const struct lib_sid *hit = bsearch(&want, sids.v, sids.n, sizeof *sids.v, sid_cmp);
        if (hit) rids[i] = hit->rid, ++found;
    }
    return found;
}

/* The songs into RAM, in rid order, each with the hash of what the car is sent of it; the revision their hashes'
 * hash. The card's songs (songtable) and, while a server is signed in (its catalogue on the card, as Car Remote's
 * library attaches it: coda_rc_lib.c lib_open), the server's (servertable, rids from 0x40000000): on the Q2 of the
 * 2026-11-20 drive songtable was empty and every song the server's, 9,199 of them, and iDrive got none. The server
 * songs' stream-path index rows into *sv (*sn of them), for lib_stream_rids. 0; -1 with no database; -2 when the
 * catalogue cannot be read (absent, or not attached) while the record of what the car holds has server songs: the
 * card not mounted yet (a wake, a card lent to a computer), which is not a server library gone (an app signed out
 * leaves its catalogue, without its rows). Run in lib_load's child, never in the loop. */
static unsigned lib_pool_len; /* lib_t.pool's bytes in use, for the child's hand-over */
long card_wait_ms = 10000; /* --card-wait-ms: how long the child waits for a catalogue it cannot read */
static int lib_read(struct lib_sid **sv_out, unsigned *sn_out) {
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
    if (!server) { /* no catalogue: none signed in ever, or the card not there yet (the record says which) */
        lib_read_sent();
        int had = lib_t.sent_n && lib_t.sent[lib_t.sent_n - 1].rid >= 0x40000000u; /* the record is in rid order */
        lib_free();
        if (had) {
            if (db) sqlite3_close(db);
            return -2;
        }
    }
    snprintf(sql, sizeof sql, "%s%s%s", LIB_COLS("songtable") "0, null, null from main.songtable where " LIB_WHERE,
             server ? " union all " LIB_COLS("s") "ifnull(m.seconds,0), m.app, m.sid from cat.servertable s left join"
                      " cat.servermeta m on m.fileurl = s.fileurl"
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
    struct lib_sid *sv = 0;
    unsigned sn = 0, scap = 0;
    *sv_out = 0, *sn_out = 0;
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
        for (int k = 0; k < 6; ++k) h = fnv_part(h, f[k]);
        t->hash = (h ^ t->track * 131u ^ t->disc * 65537u ^ t->ms) * 16777619u;
        t->album_id = fnv_part(fnv_part(2166136261u, f[3]), f[1]); /* an album is its album artist's */
        t->artist_id = fnv_part(2166136261u, f[2]), t->aartist_id = fnv_part(2166136261u, f[3]);
        t->genre_id = fnv_part(2166136261u, f[4]), t->composer_id = fnv_part(2166136261u, f[5]);
        rev = (rev ^ t->rid) * 16777619u, rev = (rev ^ t->hash) * 16777619u;
        if (t->rid) ++n;
        sid_add(&sv, &sn, &scap, (const char *)sqlite3_column_text(st, 10), (const char *)sqlite3_column_text(st, 11),
                t->rid);
    }
    sqlite3_finalize(st);
    *sv_out = sv, *sn_out = sn; /* the server songs' stream paths, as this library has them (lib_stream_rids) */
    if (pool) lib_lists(db, server, &pool, &len, &cap);
    sqlite3_close(db);
    lib_t.pool = pool, lib_t.n = n, lib_pool_len = len;
    snprintf(lib_t.rev, sizeof lib_t.rev, "q2.%u.%08x", n, rev);
    lib_read_sent();
    char msg[240];
    snprintf(msg, sizeof msg, "library: %u songs read (%s), %u lists, revision %s; the car was last sent %s (%u songs)",
             n, server ? "the card's and the server's" : "the card's", lib_t.nlists, lib_t.rev,
             lib_t.sent_rev[0] ? lib_t.sent_rev : "nothing", lib_t.sent_n);
    logline("lib", msg);
    return 0;
}

/* The library's read, in a child (lib_load), so the loop never waits on it: at the car's StartMediaLibraryUpdates on
 * 2026-11-22 (q2car.1.log, 22028.654) reading the owner's 9,199 server songs and 31 lists held the loop 648 ms. The
 * child reads them as lib_read does and hands the tables over a pipe whole: a header, then the songs, the pool of
 * their strings, the lists, the lists' rids, the sent record and the stream-path index, each array as it lies in
 * memory (this machine's order; the child is a fork of this program). The loop takes the bytes in as they come
 * (lib_load_poll) and, at the end, points lib_t into them; the tables before stay in use until then. */
struct lib_head {
    unsigned magic, n, pool_len, nlists, nlrids, sent_n, sn, unreadable; /* unreadable: lib_read's -2, no tables */
    char rev[40], sent_rev[40];
};
#define LIB_MAGIC 0x51324c42u
struct lib_lload lload = {.fd = -1};

int lib_write_all(int fd, const void *p, unsigned n) {
    const unsigned char *b = p;
    while (n) {
        int w = (int)write(fd, b, n);
        if (w <= 0) {
            if (w < 0 && ERRNO == EINTR) continue;
            return -1;
        }
        b += w, n -= (unsigned)w;
    }
    return 0;
}
static unsigned lib_pad(unsigned n) { return (n + 3u) & ~3u; }

/* The child: the tables read and written to the pipe; nothing written when there is no database. */
static void lib_child(int out) {
    struct lib_sid *sv;
    unsigned sn;
    static const unsigned char zero[4];
    nice(10);
    long until = boot_ms() + card_wait_ms, from = boot_ms();
    int r;
    while ((r = lib_read(&sv, &sn)) == -2 && boot_ms() < until) {
        struct timespec ts = { 0, 250000000L };
        nanosleep(&ts, 0);
    }
    char msg[120];
    if (boot_ms() - from >= 250) {
        snprintf(msg, sizeof msg, "library: waited for the card %ld ms, %s", boot_ms() - from,
                 r == -2 ? "and the catalogue cannot be read" : "and read the catalogue");
        logline("lib", msg);
    }
    if (r == -2) {
        struct lib_head h;
        memset(&h, 0, sizeof h);
        h.magic = LIB_MAGIC, h.unreadable = 1;
        _exit(lib_write_all(out, &h, sizeof h) ? 1 : 0);
    }
    if (r < 0) _exit(1);
    if (sv) qsort(sv, sn, sizeof *sv, sid_cmp); /* here, not in the loop (lib_install) */
    struct lib_head h;
    memset(&h, 0, sizeof h);
    h.magic = LIB_MAGIC, h.n = lib_t.n, h.pool_len = lib_pool_len, h.nlists = lib_t.nlists, h.sent_n = lib_t.sent_n;
    h.sn = sn;
    for (unsigned j = 0; j < lib_t.nlists; ++j) h.nlrids += lib_t.lists[j].n;
    memcpy(h.rev, lib_t.rev, sizeof h.rev), memcpy(h.sent_rev, lib_t.sent_rev, sizeof h.sent_rev);
    if (lib_write_all(out, &h, sizeof h) || lib_write_all(out, lib_t.tracks, h.n * sizeof *lib_t.tracks) ||
        lib_write_all(out, lib_t.pool, h.pool_len) || lib_write_all(out, zero, lib_pad(h.pool_len) - h.pool_len) ||
        lib_write_all(out, lib_t.lists, h.nlists * sizeof *lib_t.lists) ||
        lib_write_all(out, lib_t.lrids, h.nlrids * sizeof *lib_t.lrids) ||
        lib_write_all(out, lib_t.sent, h.sent_n * sizeof *lib_t.sent) || lib_write_all(out, sv, sn * sizeof *sv))
        _exit(1);
    _exit(0);
}

/* The child's bytes made the tables: lib_t pointed into them and the stream-path index replaced. 1, or 0 when they
 * are not whole. */
static unsigned long long lib_want(const struct lib_head *h) { /* the bytes the header announces, itself included */
    return sizeof *h + (unsigned long long)h->n * sizeof(struct iap2_track) + lib_pad(h->pool_len) +
           (unsigned long long)h->nlists * sizeof(struct iap2_list) + h->nlrids * 4ull +
           (unsigned long long)h->sent_n * sizeof(struct iap2_sent) + h->sn * sizeof(struct lib_sid);
}
static int lib_install(unsigned char *b, unsigned len) {
    struct lib_head h;
    if (len < sizeof h) return 0;
    memcpy(&h, b, sizeof h);
    if (h.magic == LIB_MAGIC && h.unreadable && len == sizeof h) return 2; /* the tables before stay */
    if (h.magic != LIB_MAGIC || lib_want(&h) != len || !h.pool_len) return 0;
    lib_free();
    lib_blob = b;
    unsigned char *c = b + sizeof h;
    lib_t.tracks = h.n ? (struct iap2_track *)c : 0, c += h.n * sizeof(struct iap2_track);
    lib_t.pool = (char *)c, c += lib_pad(h.pool_len);
    lib_t.lists = h.nlists ? (struct iap2_list *)c : 0, c += h.nlists * sizeof(struct iap2_list);
    lib_t.lrids = h.nlrids ? (unsigned *)c : 0, c += h.nlrids * 4;
    lib_t.sent = h.sent_n ? (struct iap2_sent *)c : 0, c += h.sent_n * sizeof(struct iap2_sent);
    lib_t.n = h.n, lib_t.nlists = h.nlists, lib_t.sent_n = h.sent_n;
    memcpy(lib_t.rev, h.rev, sizeof h.rev), memcpy(lib_t.sent_rev, h.sent_rev, sizeof h.sent_rev);
    lib_t.rev[sizeof lib_t.rev - 1] = lib_t.sent_rev[sizeof lib_t.sent_rev - 1] = 0;
    sid_install((struct lib_sid *)c, h.sn, 0); /* the rows where they lie, in the block */
    return 1;
}

/* The child's bytes taken in, as many as wait; at its end the tables installed (lib_ready then says how it went).
 * Called every pass of the loop, so the child never waits on a full pipe. */
void lib_load_poll(void) {
    unsigned took = 0;
    int r = 1, whole = 0;
    while (lload.fd >= 0 && took < HANDOVER_MAX) {
        unsigned need = sizeof(struct lib_head);
        if (lload.in_n >= need) { /* the header in: room for exactly what it announces, once */
            unsigned long long want = lib_want((const struct lib_head *)lload.in);
            if (want >= 64u << 20) break;
            need = (unsigned)want;
        }
        if (lload.in_n == need && lload.in_n >= sizeof(struct lib_head)) {
            whole = 1;
            break;
        }
        if (lload.in_cap < need) {
            unsigned char *w = realloc(lload.in, need);
            if (!w) break;
            lload.in = w, lload.in_cap = need;
        }
        r = (int)read(lload.fd, lload.in + lload.in_n, need - lload.in_n < HANDOVER_MAX - took ? need - lload.in_n : HANDOVER_MAX - took);
        if (r <= 0) break;
        lload.in_n += (unsigned)r, took += (unsigned)r;
    }
    if (lload.fd < 0) return;
    if (!whole && (took >= HANDOVER_MAX || (r < 0 && (ERRNO == EAGAIN || ERRNO == EINTR)))) return; /* more to come */
    close(lload.fd), lload.fd = -1, lload.pid = 0;
    char msg[160];
    int ok = lload.in ? lib_install(lload.in, lload.in_n) : 0;
    if (ok != 1) free(lload.in);
    snprintf(msg, sizeof msg, "library: %s by its child in %ld ms (%u bytes)",
             ok == 1 ? "read" : ok == 2 ? "not read, the catalogue unreadable," : "not read", mono_ms() - lload.at,
             lload.in_n);
    logline("lib", msg);
    lload.in = 0, lload.in_n = lload.in_cap = 0;
    lload.ready = ok ? ok : -1;
}

/* The host's lib_load: the library's read begun in a child (one already reading goes on). 0, or -1 when no child
 * could be started. */
int lib_load(void) {
    int fds[2];
    lload.ready = 0;
    if (lload.fd >= 0) return 0;
    if (pipe_cloexec(fds)) return logline("lib", "library: no pipe for its child"), -1;
    int pid = fork();
    if (pid == 0) {
        signal(SIGTERM, SIG_DFL), signal(SIGINT, SIG_DFL), signal(SIGHUP, SIG_DFL);
        child_drop();
        close(fds[0]);
        lib_child(fds[1]);
    }
    close(fds[1]);
    if (pid < 0) {
        close(fds[0]);
        logline("lib", "library: its child not started");
        return -1;
    }
    fcntl(fds[0], F_SETFL, O_NONBLOCK);
    lload.fd = fds[0], lload.pid = pid, lload.at = mono_ms();
    return 0;
}

/* The host's lib_ready: 1 the tables read (in lib_t), 2 the catalogue unreadable (lib_t as it was), -1 none, 0 still
 * reading. */
int lib_ready(void) {
    lib_load_poll();
    return lload.ready;
}

/* A server song's album as its app keeps it (catalogue.c catalogue_cover, what the Q2's own Now Playing shows): the
 * song's server row (its rid, from 0x40000000, is servermeta's id) has the path /mnt/mmc/.<app>/cat/<album id>/...;
 * the app's id (the folder's name without its dot) into app and the album's id into album, 1; 0 for a card song, or a
 * row with no such path. One row by its key. Read in the cover's child (q2_iap.c art_child), never in the loop. */
int lib_app_album(unsigned rid, char *app, unsigned appcap, char *album, unsigned albumcap) {
    sqlite3 *db = 0;
    sqlite3_stmt *st = 0;
    char sql[80];
    int ok = 0;
    if (rid < 0x40000000u || access(lib_cat, 0)) return 0;
    if (sqlite3_open_v2(lib_cat, &db, SQLITE_OPEN_READONLY, 0) == 0) {
        snprintf(sql, sizeof sql, "select fileurl from servermeta where id = %u", rid);
        if (sqlite3_prepare_v2(db, sql, -1, &st, 0) == 0 && sqlite3_step(st) == SQLITE_ROW) {
            const char *u = (const char *)sqlite3_column_text(st, 0), *c = u ? strstr(u, "/cat/") : 0;
            const char *e = c ? strchr(c + 5, '/') : 0;
            if (e && e > c + 5 && !strncmp(u, "/mnt/mmc/.", 10) && c > u + 10 && !memchr(u + 10, '/', (unsigned)(c - u - 10)) &&
                (unsigned)(c - u - 10) < appcap && (unsigned)(e - c - 5) < albumcap && !memchr(c + 5, ' ', (unsigned)(e - c - 5))) {
                snprintf(app, appcap, "%.*s", (int)(c - u - 10), u + 10);
                snprintf(album, albumcap, "%.*s", (int)(e - c - 5), c + 5);
                ok = 1;
            }
        }
        sqlite3_finalize(st);
    }
    sqlite3_close(db);
    return ok;
}

/* The car has all of this revision: what it holds, for the next connection's changes alone. The record is made in
 * RAM and written in one write by a child, so the event loop never waits on it: /mnt/data is UBIFS mounted sync,
 * and on 2026-11-21 (q2muse-1125) the record of 9,199 songs, written a line at a time from the loop, held it 12.3 s
 * while the car's SET_REPORT went unanswered (test/iap_probe.py test_writing_the_sent_record_holds_nothing). */
void lib_saved(void) {
    char tmp[200];
    unsigned cap = (unsigned)strlen(lib_t.rev) + 2 + lib_t.n * 20, len;
    char *b = malloc(cap);
    if (!b) {
        logline("lib", "library: the sent record could not be written (no memory)");
        return;
    }
    len = (unsigned)snprintf(b, cap, "%s\n", lib_t.rev);
    for (unsigned i = 0; i < lib_t.n && len < cap; ++i)
        len += (unsigned)snprintf(b + len, cap - len, "%u %08x\n", lib_t.tracks[i].rid, lib_t.tracks[i].hash);
    snprintf(tmp, sizeof tmp, "%s.iap", lib_sent_file);
    int pid = len < cap ? fork() : -1;
    if (pid == 0) {
        child_drop();
        long at = mono_ms();
        int fd = open(tmp, O_WRONLY | O_CREAT | O_TRUNC | O_CLOEXEC, 0600), ok = fd >= 0 && write(fd, b, len) == (int)len;
        if (fd >= 0) close(fd);
        char msg[120];
        if (ok && !rename(tmp, lib_sent_file))
            snprintf(msg, sizeof msg, "library: the sent record written, %u bytes, %ld ms", len, mono_ms() - at);
        else
            unlink(tmp), snprintf(msg, sizeof msg, "library: the sent record could not be written");
        logline("lib", msg);
        _exit(0);
    }
    free(b);
    if (pid < 0) logline("lib", "library: the sent record could not be written (no child)");
}

/* Songs to play, by rid: the shim's list (/tmp/coda-rc.list, a rid a line) and its play command, as coda-rc's Play
 * sends them (coda_rc_lib.c play). */
const char *list_file = "/tmp/coda-rc.list";
void lib_play(const unsigned *rids, unsigned n, unsigned at, int shuffle) {
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
const char *queue_file = "/tmp/coda-rc.queue";
int lib_queue(unsigned *out, unsigned cap) {
    unsigned room = cap * 300 + 64; /* a song outside songtable is a line with its path, title and artist */
    unsigned char *b = malloc(room);
    const char **paths = malloc(cap * sizeof *paths);
    unsigned got = b && paths ? read_file(queue_file, b, room - 1) : 0;
    int n = -1, zeros = 0;
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
            char *tab = strchr(c, '\t'), *nl = strchr(c, '\n');
            paths[n] = 0;
            if (tab && (!nl || tab < nl)) {
                char *end = strchr(tab + 1, '\t');
                if (end && (!nl || end < nl)) *end = 0, paths[n] = tab + 1, zeros += !atoi(c);
            }
            out[n++] = (unsigned)atoi(c);
            c = nl;
        }
        if ((unsigned)n != total) n = -1;
    }
    if (n > 0 && zeros) lib_stream_rids(paths, out, (unsigned)n);
    free(b), free(paths);
    return n;
}
