/* q2-iap's library (docs/iphone-mode.md#the-library-on-idrive): the Q2's songs as the car's media library, read from
 * stock's database (songtable, the songs coda_rc_lib.c's views read: audiobooks and podcasts left out) with the lists
 * it sends as playlists (lib_lists) into RAM at the car's StartMediaLibraryUpdates, and the record of what the car was last sent in full (LIB_SENT), so a car that
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
static const char *card_root = ""; /* put before a catalogue row's /mnt/mmc path (--card-root, for the host tests) */
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
    free(lib_t.tracks), free(lib_t.pool), free(lib_t.sent), free(lib_t.lists), free(lib_t.lrids);
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
static struct {
    struct lib_sid *v; /* the index, sorted; n of them */
    unsigned n, cap;
    int fd;               /* the child's pipe while it reads the catalogue, else -1 */
    int asked;            /* a child was started this run: not again */
    unsigned char *in;    /* its records so far */
    unsigned in_n, in_cap;
} sids = {.fd = -1};

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
/* v (n rows) sorted and made the index, in place of the one before */
static void sid_install(struct lib_sid *v, unsigned n) {
    if (v) qsort(v, n, sizeof *v, sid_cmp);
    free(sids.v);
    sids.v = n ? realloc(v, n * sizeof *v) : (free(v), (struct lib_sid *)0);
    if (n && !sids.v) sids.v = v;
    sids.n = sids.v ? n : 0;
}

/* The child that reads the catalogue's (app, sid, id) rows for the index: each as 12 bytes on the pipe, unsorted
 * (sid_key's halves and the rid, in this machine's order), the loop reading them as they come (sid_index_poll). */
static void sid_index_start(void) {
    int fds[2];
    if (sids.asked || sids.n || access(lib_cat, 0)) return;
    sids.asked = 1;
    if (pipe(fds)) return;
    int pid = fork();
    if (pid == 0) {
        sqlite3 *db = 0;
        sqlite3_stmt *st = 0;
        close(fds[0]);
        if (!sqlite3_open_v2(lib_cat, &db, SQLITE_OPEN_READONLY, 0) &&
            !sqlite3_prepare_v2(db, "select app, sid, id from servermeta where sid is not null", -1, &st, 0)) {
            struct lib_sid b[256];
            unsigned k = 0;
            while (sqlite3_step(st) == SQLITE_ROW) {
                const char *app = (const char *)sqlite3_column_text(st, 0), *sid = (const char *)sqlite3_column_text(st, 1);
                unsigned rid = (unsigned)sqlite3_column_int(st, 2);
                if (!app || !sid || !rid) continue;
                sid_key(app, (int)strlen(app), sid, (int)strlen(sid), &b[k].hi, &b[k].lo);
                b[k].rid = rid;
                if (++k == 256) {
                    if (write(fds[1], b, sizeof b) != (int)sizeof b) _exit(1);
                    k = 0;
                }
            }
            if (k && write(fds[1], b, k * sizeof *b) != (int)(k * sizeof *b)) _exit(1);
        }
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

/* The child's records taken in, as many as wait; at its end (end of file) the index made of them, unless lib_load
 * has made one meanwhile. */
static void sid_index_poll(void) {
    for (;;) {
        if (sids.fd < 0) return;
        if (sids.in_n + 4096 > sids.in_cap) {
            unsigned char *w = realloc(sids.in, sids.in_cap = sids.in_cap ? sids.in_cap * 2 : 65536);
            if (!w) break;
            sids.in = w;
        }
        int r = (int)read(sids.fd, sids.in + sids.in_n, sids.in_cap - sids.in_n);
        if (r > 0) {
            sids.in_n += (unsigned)r;
            continue;
        }
        if (r < 0 && (ERRNO == EAGAIN || ERRNO == EINTR)) return;
        break;
    }
    close(sids.fd), sids.fd = -1;
    unsigned n = sids.in_n / sizeof(struct lib_sid);
    if (!sids.n && sids.in) {
        char msg[120];
        sid_install((struct lib_sid *)sids.in, n);
        snprintf(msg, sizeof msg, "library: %u server songs found by their stream path, from the catalogue", sids.n);
        logline("lib", msg);
    } else {
        free(sids.in);
    }
    sids.in = 0, sids.in_n = sids.in_cap = 0;
}

/* Each of n paths (0: none) whose rids[i] is 0 looked for in the index; the count found. */
static unsigned lib_stream_rids(const char *const *paths, unsigned *rids, unsigned n) {
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
        sid_add(&sv, &sn, &scap, (const char *)sqlite3_column_text(st, 10), (const char *)sqlite3_column_text(st, 11),
                t->rid);
    }
    sqlite3_finalize(st);
    sid_install(sv, sn); /* the server songs' stream paths, as this library has them (lib_stream_rids) */
    if (pool) lib_lists(db, server, &pool, &len, &cap);
    sqlite3_close(db);
    lib_t.pool = pool, lib_t.n = n;
    snprintf(lib_t.rev, sizeof lib_t.rev, "q2.%u.%08x", n, rev);
    lib_read_sent();
    char msg[240];
    snprintf(msg, sizeof msg, "library: %u songs read (%s), %u lists, revision %s; the car was last sent %s (%u songs)",
             n, server ? "the card's and the server's" : "the card's", lib_t.nlists, lib_t.rev,
             lib_t.sent_rev[0] ? lib_t.sent_rev : "nothing", lib_t.sent_n);
    logline("lib", msg);
    return 0;
}

/* A server song's cover as its app keeps it (catalogue.c catalogue_cover, what the Q2's own Now Playing shows): the
 * song's server row (its rid, from 0x40000000, is servermeta's id) has the path /mnt/mmc/.<app>/cat/<album id>/...,
 * and the cover is /mnt/mmc/.<app>/covers/<album id>.jpg, a baseline JPEG of 160 px at most (q2-subsonic's
 * covers.go). Its path into out, 1; 0 for a card song, or a row with no such path. One row by its key, as the song
 * changes. */
static int lib_app_cover(unsigned rid, char *out, unsigned cap) {
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
            if (e && e > c + 5 && !strncmp(u, "/mnt/mmc/.", 10) && !memchr(u + 10, '/', (unsigned)(c - u - 10))) {
                int n = snprintf(out, cap, "%s%.*s/covers/%.*s.jpg", card_root, (int)(c - u), u, (int)(e - c - 5), c + 5);
                ok = n > 0 && (unsigned)n < cap;
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
static void lib_saved(void) {
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
        long at = mono_ms();
        int fd = open(tmp, O_WRONLY | O_CREAT | O_TRUNC, 0600), ok = fd >= 0 && write(fd, b, len) == (int)len;
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
