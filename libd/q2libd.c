/* q2libd: Vamp OS's library service (docs/vamp-os.md#who-owns-what, docs/libd.md), its second slice. It keeps two
 * databases in the shapes q2ui and q2playd already read, so they need no change:
 *
 *   - /mnt/data/vamp/library.db: the card's songs, a songtable with stock's columns (id, filename, fileurl, album,
 *     songer, style, name, composer, albumsonger, year, track, cd_num, time, ...), from a walk of /mnt/mmc and each
 *     file's tags through FFmpeg's demuxers. A song keeps its id across scans while its path stays; a file whose size
 *     and time are unchanged is not read again. Stock's own /mnt/data/database.db is never touched.
 *   - /mnt/mmc/.vamp/catalogue.db: the server library, a servertable of every signed-in card app's catalogue
 *     (/mnt/mmc/.<id>/catalogue.tsv, Q2CAT/1, docs/server-library.md), ids from 0x40000000 as the payload gives them.
 *     On the card, as today's Vamp keeps its own (/mnt/data is too small for a large server), but a file of Vamp OS's
 *     own, so today's Vamp's catalogue.db is never touched either. A server song that is a card song is left out
 *     (serverdup names it). servermeta gives each server song's app, sid and offline copy (docs/libd.md#offline-copies):
 *     the catalogue's local path, then catalogue.avail's lines.
 *   - Besides: each album's cover in /mnt/mmc/.vamp/covers/ (vamp_cover names each song's), and the card's M3U
 *     playlists (/mnt/mmc/_explaylist_data/) in library.db's playlist, stock's table.
 *
 * Each is written whole beside itself and renamed over it, so a reader never sees half a scan; only catalogue.avail's
 * new lines change servermeta in place, one transaction each look, looked at every second. It scans at its start
 * once the card is mounted, when the card's mount or a playlist file changes, when an app's catalogue.tsv or credentials change (looked
 * at every 10 s), and at SIGHUP; --once scans and ends. Before the card is there it writes an empty library, so a
 * reader always has one; after each write it runs --after's command (the image: q2ui's restart in place, so its lists
 * are the new ones). --root prefixes every path, for test/libd.py. */
#define _GNU_SOURCE
#include <ctype.h>
#include <dirent.h>
#include <errno.h>
#include <libavformat/avformat.h>
#include <libavutil/log.h>
#include <signal.h>
#include <sqlite3.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>

#define CAT_ID_BASE 0x40000000u /* server rows' ids, above any card song's (patch/catalogue.c) */
#define CAT_APP_SPAN 0x01000000u
#define CAT_APPS 4
#define LOOK_S 10 /* the card and the catalogues; catalogue.avail every second */
#define LINE 8192

static char root[256], after[256];
static int once;
static volatile sig_atomic_t again, stopping;

static char *path(const char *rel) {
    static char b[6][600];
    static int i;
    i = (i + 1) % 6;
    snprintf(b[i], sizeof b[i], "%s%s", root, rel);
    return b[i];
}

static void say(const char *fmt, ...) {
    va_list ap;
    va_start(ap, fmt);
    fprintf(stderr, "q2libd: ");
    vfprintf(stderr, fmt, ap);
    fputc('\n', stderr);
    va_end(ap);
}

/* What the player plays (player/README's formats, tools/player.py's demuxers), by extension */
static int audio(const char *name) {
    static const char *ext[] = {"flac", "mp3", "wav", "aif", "aiff", "m4a", "mp4", "alac", "ogg", "oga", "opus", "ape", "wv",
                                "wma", "aac", "ac3", "dts", "dsf", "dff", "tak", 0};
    const char *dot = strrchr(name, '.');
    if (!dot) return 0;
    for (int i = 0; ext[i]; ++i)
        if (!strcasecmp(dot + 1, ext[i])) return 1;
    return 0;
}

/* ---- the card ---- */

static const char *SONGTABLE =
    "create table songtable(id integer primary key autoincrement, filename varchar(512), fileurl varchar(1024),"
    " album varchar(512), songer varchar(256), style varchar(128), name varchar(512), composer varchar(256),"
    " albumsonger varchar(256), year integer, track integer, cd_num integer, time integer, time_end integer,"
    " time_create integer, Initial integer, bitdepth integer, samprate integer);"
    "create table vamp_files(fileurl text primary key, size integer, mtime integer, id integer);"
    "create table vamp_cover(id integer primary key, file text);"
    "create table playlist(id integer primary key, filename varchar(512), fileurl varchar(1024), album varchar(512),"
    " songer varchar(256), style varchar(128), name varchar(512), composer varchar(256), albumsonger varchar(256),"
    " year integer, track integer, cd_num integer, time integer, time_end integer, time_create integer, Initial integer,"
    " bitdepth integer, samprate integer);";

static int initial(const char *t) { /* songtable's Initial as patch/catalogue.c gives it */
    unsigned char c = t ? (unsigned char)*t : 0;
    if (c >= 'a' && c <= 'z') c -= 32;
    return c >= 'A' && c <= 'Z' ? c : '#';
}

static const char *tag(AVDictionary *m, const char *k) {
    AVDictionaryEntry *e = av_dict_get(m, k, 0, AV_DICT_IGNORE_SUFFIX);
    return e && e->value ? e->value : "";
}

static sqlite3 *db;
static sqlite3_stmt *ins, *find, *ins_file, *ins_cover;
static int next_id, songs, read_n;

/* One file: its old row when unchanged, else its tags read. */
/* ---- covers (docs/libd.md#covers) ----
 * An album's cover is kept once, in /mnt/mmc/.vamp/covers/<key>.jpg (the bytes as found: a JPEG or a PNG, which q2ui
 * reads either way), key the FNV-1a of the album artist (else the artist) and the album, lower-cased, or of the song's
 * folder when it has no album tag. The picture is the file's own (FFmpeg's attached picture), else a cover.jpg,
 * folder.jpg or front.jpg (or .png) in its folder. vamp_cover names each song's. A cover file there already is kept. */
#define COVER_MAX (4 << 20)

static unsigned fnv_lower(unsigned h, const char *s) {
    for (; *s; ++s) h = (h ^ (unsigned char)tolower((unsigned char)*s)) * 16777619u;
    return h;
}

static int write_whole(const char *to, const void *b, size_t n) {
    char tmp[700];
    snprintf(tmp, sizeof tmp, "%s.q2libd", to);
    FILE *f = fopen(tmp, "wb");
    if (!f) return -1;
    int ok = fwrite(b, 1, n, f) == n;
    if (fclose(f) || !ok || rename(tmp, to)) return unlink(tmp), -1;
    return 0;
}

static int folder_picture(const char *full, char *out, size_t room) {
    static const char *names[] = {"cover.jpg", "folder.jpg", "front.jpg", "cover.png", "folder.png", "front.png",
                                  "Cover.jpg", "Folder.jpg", "Front.jpg", 0};
    const char *slash = strrchr(full, '/');
    struct stat st;
    for (int i = 0; slash && names[i]; ++i) {
        snprintf(out, room, "%.*s/%s", (int)(slash - full), full, names[i]);
        if (!stat(out, &st) && S_ISREG(st.st_mode) && st.st_size > 0 && st.st_size <= COVER_MAX) return 0;
    }
    return -1;
}

static void cover_of(int id, AVFormatContext *f, const char *full, const char *url, const char *aa, const char *album) {
    char dir[400], file[500], pic[1300];
    unsigned h = 2166136261u;
    if (*album) {
        h = fnv_lower(fnv_lower(fnv_lower(h, aa), "\t"), album);
    } else { /* no album: the folder, the url less its name */
        const char *slash = strrchr(url, '/');
        for (const char *p = url; slash && p < slash; ++p) h = (h ^ (unsigned char)tolower((unsigned char)*p)) * 16777619u;
    }
    snprintf(dir, sizeof dir, "%s", path("/mnt/mmc/.vamp/covers"));
    snprintf(file, sizeof file, "%s/%08x.jpg", dir, h);
    struct stat st;
    if (stat(file, &st)) {
        const AVPacket *pk = 0;
        for (unsigned i = 0; f && i < f->nb_streams; ++i)
            if (f->streams[i]->disposition & AV_DISPOSITION_ATTACHED_PIC && f->streams[i]->attached_pic.size > 0) {
                pk = &f->streams[i]->attached_pic;
                break;
            }
        mkdir(path("/mnt/mmc/.vamp"), 0755), mkdir(dir, 0755);
        if (pk && pk->size <= COVER_MAX) {
            if (write_whole(file, pk->data, (size_t)pk->size)) return;
        } else if (!folder_picture(full, pic, sizeof pic)) {
            FILE *in = fopen(pic, "rb");
            char *b = malloc(COVER_MAX);
            size_t n = in && b ? fread(b, 1, COVER_MAX, in) : 0;
            if (in) fclose(in);
            int bad = !n || write_whole(file, b, n);
            free(b);
            if (bad) return;
        } else {
            return;
        }
    }
    snprintf(file, sizeof file, "/mnt/mmc/.vamp/covers/%08x.jpg", h); /* the Q2's own path, whatever --root is */
    sqlite3_bind_int(ins_cover, 1, id);
    sqlite3_bind_text(ins_cover, 2, file, -1, SQLITE_TRANSIENT);
    sqlite3_step(ins_cover);
    sqlite3_reset(ins_cover);
}

static void add_file(const char *full, const char *url, const struct stat *st) {
    int id = 0, same = 0;
    if (find) {
        sqlite3_bind_text(find, 1, url, -1, SQLITE_STATIC);
        if (sqlite3_step(find) == SQLITE_ROW) {
            id = sqlite3_column_int(find, 0);
            same = sqlite3_column_int64(find, 1) == st->st_size && sqlite3_column_int64(find, 2) == st->st_mtime;
        }
        sqlite3_reset(find);
    }
    if (!id) id = next_id++;
    if (same) {
        char *e = 0;
        char *q = sqlite3_mprintf("insert into songtable select * from old.songtable where id = %d", id);
        if (sqlite3_exec(db, q, 0, 0, &e) != SQLITE_OK) say("%s", e), sqlite3_free(e), same = 0;
        sqlite3_free(q);
        if (sqlite3_changes(db) != 1) same = 0;
        q = sqlite3_mprintf("insert into vamp_cover select * from old.vamp_cover where id = %d", id);
        if (same) sqlite3_exec(db, q, 0, 0, 0); /* none in a library from before covers: read again next time it changes */
        sqlite3_free(q);
    }
    if (!same) {
        AVFormatContext *f = 0;
        const char *title = "", *artist = "", *album = "", *aa = "", *genre = "", *composer = "";
        int year = 0, track = 0, disc = 0, seconds = 0, rate = 0, bits = 0;
        if (avformat_open_input(&f, full, 0, 0) == 0) {
            avformat_find_stream_info(f, 0);
            AVDictionary *m = f->metadata;
            if (f->nb_streams && !av_dict_count(m)) m = f->streams[0]->metadata; /* Ogg keeps them on the stream */
            title = tag(m, "title"), artist = tag(m, "artist"), album = tag(m, "album"), aa = tag(m, "album_artist");
            genre = tag(m, "genre"), composer = tag(m, "composer");
            year = atoi(tag(m, "date")), track = atoi(tag(m, "track")), disc = atoi(tag(m, "disc"));
            if (f->duration > 0) seconds = (int)(f->duration / AV_TIME_BASE);
            for (unsigned i = 0; i < f->nb_streams; ++i)
                if (f->streams[i]->codecpar->codec_type == AVMEDIA_TYPE_AUDIO) {
                    rate = f->streams[i]->codecpar->sample_rate;
                    bits = f->streams[i]->codecpar->bits_per_raw_sample;
                    break;
                }
        } else {
            say("cannot read %s", url);
        }
        const char *base = strrchr(url, '/') ? strrchr(url, '/') + 1 : url;
        char name[1200];
        snprintf(name, sizeof name, "%s", base);
        if (!*title) { /* no title tag: the file's name without its extension, as stock lists it */
            char *dot = strrchr(name, '.');
            if (dot) *dot = 0;
        }
        int c = 1;
        sqlite3_bind_int(ins, c++, id);
        sqlite3_bind_text(ins, c++, base, -1, SQLITE_TRANSIENT);
        sqlite3_bind_text(ins, c++, url, -1, SQLITE_TRANSIENT);
        sqlite3_bind_text(ins, c++, album, -1, SQLITE_TRANSIENT);
        sqlite3_bind_text(ins, c++, artist, -1, SQLITE_TRANSIENT);
        sqlite3_bind_text(ins, c++, genre, -1, SQLITE_TRANSIENT);
        sqlite3_bind_text(ins, c++, *title ? title : name, -1, SQLITE_TRANSIENT);
        sqlite3_bind_text(ins, c++, composer, -1, SQLITE_TRANSIENT);
        sqlite3_bind_text(ins, c++, aa, -1, SQLITE_TRANSIENT);
        sqlite3_bind_int(ins, c++, year);
        sqlite3_bind_int(ins, c++, track);
        sqlite3_bind_int(ins, c++, disc);
        sqlite3_bind_int(ins, c++, seconds);
        sqlite3_bind_int(ins, c++, (int)st->st_mtime);
        sqlite3_bind_int(ins, c++, initial(*title ? title : name));
        sqlite3_bind_int(ins, c++, bits);
        sqlite3_bind_int(ins, c++, rate);
        if (sqlite3_step(ins) != SQLITE_DONE) say("insert %s: %s", url, sqlite3_errmsg(db));
        sqlite3_reset(ins);
        cover_of(id, f, full, url, *aa ? aa : artist, album);
        if (f) avformat_close_input(&f);
        ++read_n;
    }
    sqlite3_bind_text(ins_file, 1, url, -1, SQLITE_TRANSIENT);
    sqlite3_bind_int64(ins_file, 2, st->st_size);
    sqlite3_bind_int64(ins_file, 3, st->st_mtime);
    sqlite3_bind_int(ins_file, 4, id);
    sqlite3_step(ins_file);
    sqlite3_reset(ins_file);
    ++songs;
}

/* The card's tree under dir (its path as the Q2 names it, rel), in name order; dot folders and files are an app's or
 * the system's, not music. */
static void walk(const char *rel, int depth) {
    struct dirent **e;
    char full[1200];
    snprintf(full, sizeof full, "%s%s", root, rel);
    int n = depth > 16 ? -1 : scandir(full, &e, 0, alphasort);
    for (int i = 0; i < n; ++i) {
        if (e[i]->d_name[0] != '.') {
            char r[1200], f[1500];
            struct stat st;
            snprintf(r, sizeof r, "%s/%s", rel, e[i]->d_name);
            snprintf(f, sizeof f, "%s%s", root, r);
            if (!stat(f, &st)) {
                if (S_ISDIR(st.st_mode)) walk(r, depth + 1);
                else if (S_ISREG(st.st_mode) && audio(e[i]->d_name)) add_file(f, r, &st);
            }
        }
        free(e[i]);
    }
    if (n >= 0) free(e);
}

static int exec_sql(sqlite3 *d, const char *sql) {
    char *e = 0;
    int r = sqlite3_exec(d, sql, 0, 0, &e);
    if (r != SQLITE_OK) say("%s", e ? e : "sql"), sqlite3_free(e);
    return r == SQLITE_OK ? 0 : -1;
}

/* ---- playlists (docs/libd.md#playlists) ----
 * Each M3U in the card's /mnt/mmc/_explaylist_data/ (.m3u or .m3u8, name order, the first PLAYLISTS) into library.db's
 * playlist table in stock's shape: a header row, id and cd_num its number from 1 and name and filename the file's name
 * less its extension; then its songs from id 101 on, in the file's order, each songtable's row with cd_num the
 * playlist's. An entry is a path on the card (/mnt/mmc/...) or one relative to the folder ('..' allowed), '\' read as '/'; one that is
 * no song in songtable is left out. */
#define PLAYLISTS 100

static void tidy(char *u) { /* "." and "x/.." segments and doubled slashes out of an absolute path, in place */
    char *seg[64], *o = u;
    int n = 0;
    for (char *t = strtok(u + 1, "/"); t; t = strtok(0, "/"))
        if (!strcmp(t, "..")) n -= n > 0;
        else if (strcmp(t, ".") && n < 64) seg[n++] = t;
    char out[1200];
    size_t k = 0;
    for (int i = 0; i < n && k < sizeof out - 1; ++i) k += (size_t)snprintf(out + k, sizeof out - k, "/%s", seg[i]);
    snprintf(o, 1200, "%s", k ? out : "/");
}

static int m3u(const char *name) {
    const char *dot = strrchr(name, '.');
    return name[0] != '.' && dot && (!strcasecmp(dot, ".m3u") || !strcasecmp(dot, ".m3u8"));
}

static void playlists(void) {
    struct dirent **e;
    int n = scandir(path("/mnt/mmc/_explaylist_data"), &e, 0, alphasort), lists = 0, next = 101;
    sqlite3_stmt *head = 0, *song = 0;
    sqlite3_prepare_v2(db, "insert into playlist(id, filename, fileurl, album, songer, style, name, composer, albumsonger,"
                           " year, track, cd_num, time, time_end, time_create, Initial, bitdepth, samprate)"
                           " values (?1, ?2, '', '', '', '', ?2, '', '', 0, 0, ?1, 0, 0, 0, 0, 0, 0)", -1, &head, 0);
    sqlite3_prepare_v2(db, "insert into playlist select ?1, filename, fileurl, album, songer, style, name, composer, albumsonger,"
                           " year, track, ?2, time, time_end, time_create, Initial, bitdepth, samprate from songtable"
                           " where fileurl = ?3 limit 1", -1, &song, 0);
    for (int i = 0; i < n; ++i) {
        const char *d = e[i]->d_name;
        if (m3u(d) && lists < PLAYLISTS && head && song) {
            char p[600], line[1100], url[1200], name[300];
            snprintf(p, sizeof p, "%s/%s", path("/mnt/mmc/_explaylist_data"), d);
            FILE *f = fopen(p, "r");
            if (f) {
                int no = ++lists, kept = 0, left = 0;
                snprintf(name, sizeof name, "%.*s", (int)(strrchr(d, '.') - d), d);
                sqlite3_bind_int(head, 1, no), sqlite3_bind_text(head, 2, name, -1, SQLITE_TRANSIENT);
                sqlite3_step(head), sqlite3_reset(head);
                for (int first = 1; fgets(line, sizeof line, f); first = 0) {
                    char *l = line;
                    if (first && !strncmp(l, "\xef\xbb\xbf", 3)) l += 3; /* a UTF-8 BOM */
                    l[strcspn(l, "\r\n")] = 0;
                    if (!*l || *l == '#') continue;
                    for (char *c = l; *c; ++c)
                        if (*c == '\\') *c = '/';
                    if (*l == '/') snprintf(url, sizeof url, "%s", l);
                    else snprintf(url, sizeof url, "/mnt/mmc/_explaylist_data/%s", l);
                    tidy(url);
                    sqlite3_bind_int(song, 1, next), sqlite3_bind_int(song, 2, no);
                    sqlite3_bind_text(song, 3, url, -1, SQLITE_TRANSIENT);
                    sqlite3_step(song), sqlite3_reset(song);
                    if (sqlite3_changes(db) == 1) ++next, ++kept;
                    else ++left;
                }
                fclose(f);
                say("playlist %s: %d songs%s", name, kept, left ? ", some not in the library left out" : "");
            }
        }
        free(e[i]);
    }
    if (n >= 0) free(e);
    sqlite3_finalize(head), sqlite3_finalize(song);
}

/* the playlists folder's files, to tell a change by */
static void playlist_stamp(char *out, size_t room) {
    struct dirent **e;
    int n = scandir(path("/mnt/mmc/_explaylist_data"), &e, 0, alphasort);
    size_t used = 0;
    out[0] = 0;
    for (int i = 0; i < n; ++i) {
        struct stat st;
        char p[600];
        snprintf(p, sizeof p, "%s/%s", path("/mnt/mmc/_explaylist_data"), e[i]->d_name);
        if (m3u(e[i]->d_name) && !stat(p, &st) && used < room)
            used += (size_t)snprintf(out + used, room - used, "%s:%lld:%lld;", e[i]->d_name, (long long)st.st_size, (long long)st.st_mtime);
        free(e[i]);
    }
    if (n >= 0) free(e);
}

/* The library from the card (walk) or, without walk, an empty one. */
static int scan_card(int walk_card) {
    char final[600], tmp[620];
    snprintf(final, sizeof final, "%s", path("/mnt/data/vamp/library.db"));
    snprintf(tmp, sizeof tmp, "%s.new", final);
    mkdir(path("/mnt/data/vamp"), 0755);
    unlink(tmp);
    if (sqlite3_open(tmp, &db) != SQLITE_OK || exec_sql(db, SONGTABLE) || exec_sql(db, "begin")) return sqlite3_close(db), -1;
    next_id = 1, songs = read_n = 0, find = 0;
    struct stat st;
    if (!stat(final, &st)) {
        char *q = sqlite3_mprintf("attach %Q as old", final);
        if (!exec_sql(db, q) &&
            sqlite3_prepare_v2(db, "select id, size, mtime from old.vamp_files where fileurl = ?", -1, &find, 0) == SQLITE_OK) {
            sqlite3_stmt *m;
            if (sqlite3_prepare_v2(db, "select max(id) from old.songtable", -1, &m, 0) == SQLITE_OK) {
                if (sqlite3_step(m) == SQLITE_ROW) next_id = sqlite3_column_int(m, 0) + 1;
                sqlite3_finalize(m);
            }
        }
        sqlite3_free(q);
    }
    sqlite3_prepare_v2(db, "insert into songtable(id, filename, fileurl, album, songer, style, name, composer, albumsonger, year,"
                           " track, cd_num, time, time_create, Initial, bitdepth, samprate) values (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                       -1, &ins, 0);
    sqlite3_prepare_v2(db, "insert or replace into vamp_files values (?,?,?,?)", -1, &ins_file, 0);
    sqlite3_prepare_v2(db, "insert or replace into vamp_cover values (?,?)", -1, &ins_cover, 0);
    if (walk_card) walk("/mnt/mmc", 0), playlists();
    sqlite3_finalize(ins), sqlite3_finalize(ins_file), sqlite3_finalize(ins_cover);
    if (find) sqlite3_finalize(find), find = 0;
    int ok = !exec_sql(db, "commit");
    sqlite3_close(db);
    if (!ok || rename(tmp, final)) return say("library not written"), -1;
    say("card: %d songs, %d read", songs, read_n);
    return 0;
}

/* ---- the server catalogue ---- */

static int hexv(char c) {
    return c >= '0' && c <= '9' ? c - '0' : (c | 32) >= 'a' && (c | 32) <= 'f' ? (c | 32) - 'a' + 10 : -1;
}
static void unescape(char *s) { /* the card-app channel's percent escapes (patch/catalogue.c) */
    char *o = s;
    for (; *s; ++s)
        if (*s == '%' && hexv(s[1]) >= 0 && hexv(s[2]) >= 0) *o++ = (char)(hexv(s[1]) << 4 | hexv(s[2])), s += 2;
        else *o++ = *s;
    *o = 0;
}
static int split(char *line, char **f, int max) {
    int n = 0;
    for (char *p = line; n < max;) {
        f[n++] = p;
        char *t = strchr(p, '\t');
        if (!t) break;
        *t = 0, p = t + 1;
    }
    for (int i = 0; i < n; ++i) unescape(f[i]);
    return n;
}
static int plain(const char *s) { return *s && !strchr(s, '/') && strcmp(s, ".") && strcmp(s, ".."); }

/* A path an app names: on the card, with no ".." segment (patch/addon.c card_path) */
static int card_path(const char *p) {
    if (strncmp(p, "/mnt/mmc/", 9)) return 0;
    for (const char *s = p;;) {
        if (s[0] == '.' && s[1] == '.' && (!s[2] || s[2] == '/')) return 0;
        if (!(s = strchr(s, '/'))) return 1;
        ++s;
    }
}

/* One app's catalogue.tsv into servertable (ins) and servermeta (meta: its copy, the local path when it is a card
 * path), ids from base. Its rows=, counted over s, p and i lines, must be
 * met, so a file cut short is not taken for a smaller library: -1 then, and nothing of it is kept. */
static int import_app(sqlite3 *d, sqlite3_stmt *st, sqlite3_stmt *meta, const char *id, unsigned base) {
    char p[300], *line = malloc(LINE + 2), *f[20];
    snprintf(p, sizeof p, "/mnt/mmc/.%s/catalogue.tsv", id);
    FILE *in = line ? fopen(path(p), "r") : 0;
    int rows = -1, counted = 0, first = 1, ok = in != 0;
    unsigned next = base;
    exec_sql(d, "savepoint app");
    while (ok && fgets(line, LINE + 1, in)) {
        size_t k = strlen(line);
        if (k && line[k - 1] != '\n' && !feof(in)) { /* too long: counted, skipped */
            int c;
            while ((c = fgetc(in)) != EOF && c != '\n') {
            }
            if (line[0] == 's' || line[0] == 'p' || line[0] == 'i') ++counted;
            continue;
        }
        while (k && (line[k - 1] == '\n' || line[k - 1] == '\r')) line[--k] = 0;
        if (first) {
            first = 0;
            const char *r = strstr(line, "\trows=");
            if (strncmp(line, "#Q2CAT/1\t", 9) || !r) ok = 0;
            else rows = atoi(r + 6);
            continue;
        }
        if ((line[0] == 's' || line[0] == 'p' || line[0] == 'i') && line[1] == '\t') ++counted;
        int n = split(line, f, 20);
        /* s sid title artist albumartist album albumid year disc track seconds genre suffix [local size mbid] */
        if (strcmp(f[0], "s") || n < 13 || !plain(f[1]) || !plain(f[6]) || !plain(f[12])) continue;
        if (next - base >= CAT_APP_SPAN) break;
        char url[700];
        snprintf(url, sizeof url, "/mnt/mmc/.%s/cat/%s/%s.%s", id, f[6], f[1], f[12]);
        int c = 1;
        sqlite3_bind_int64(st, c++, next++);
        sqlite3_bind_text(st, c++, *f[2] ? f[2] : f[1], -1, SQLITE_TRANSIENT); /* filename: the title */
        sqlite3_bind_text(st, c++, url, -1, SQLITE_TRANSIENT);
        sqlite3_bind_text(st, c++, f[5], -1, SQLITE_TRANSIENT);
        sqlite3_bind_text(st, c++, f[3], -1, SQLITE_TRANSIENT);
        sqlite3_bind_text(st, c++, f[11], -1, SQLITE_TRANSIENT);
        sqlite3_bind_text(st, c++, f[2], -1, SQLITE_TRANSIENT);
        sqlite3_bind_text(st, c++, f[4], -1, SQLITE_TRANSIENT);
        sqlite3_bind_int(st, c++, atoi(f[7]));
        sqlite3_bind_int(st, c++, atoi(f[9]));
        sqlite3_bind_int(st, c++, atoi(f[8]));
        sqlite3_bind_int(st, c++, atoi(f[10]));
        sqlite3_bind_int(st, c++, initial(f[2]));
        sqlite3_step(st);
        sqlite3_reset(st);
        sqlite3_bind_int64(meta, 1, next - 1);
        sqlite3_bind_text(meta, 2, id, -1, SQLITE_TRANSIENT);
        sqlite3_bind_text(meta, 3, f[1], -1, SQLITE_TRANSIENT);
        if (n > 13 && card_path(f[13])) sqlite3_bind_text(meta, 4, f[13], -1, SQLITE_TRANSIENT);
        else sqlite3_bind_null(meta, 4);
        sqlite3_step(meta);
        sqlite3_reset(meta);
    }
    if (in) fclose(in);
    free(line);
    if (!ok || counted != rows) {
        say("%s: catalogue.tsv is not a whole Q2CAT/1 file (rows=%d, %d read): left out", id, rows, counted);
        exec_sql(d, "rollback to app"), exec_sql(d, "release app");
        return -1;
    }
    exec_sql(d, "release app");
    say("%s: %u server songs", id, next - base);
    return 0;
}

/* The signed-in apps with a catalogue (a card folder .<id> with catalogue.tsv and credentials), in name order, the
 * first CAT_APPS; their ids, and a stamp of their files to tell a change by. */
static int apps(char ids[CAT_APPS][64], char *stamp, size_t room) {
    struct dirent **e;
    int n = scandir(path("/mnt/mmc"), &e, 0, alphasort), k = 0;
    size_t used = 0;
    stamp[0] = 0;
    for (int i = 0; i < n; ++i) {
        const char *d = e[i]->d_name;
        if (d[0] == '.' && d[1] && strcmp(d, "..") && strcmp(d, ".vamp") && strlen(d) < 60 && k < CAT_APPS) {
            char c[300], cr[300];
            struct stat sc, scr;
            snprintf(c, sizeof c, "%s/%s/catalogue.tsv", path("/mnt/mmc"), d);
            snprintf(cr, sizeof cr, "%s/%s/credentials", path("/mnt/mmc"), d);
            if (!stat(c, &sc) && !stat(cr, &scr)) {
                snprintf(ids[k++], 64, "%s", d + 1);
                used += (size_t)snprintf(stamp + used, used < room ? room - used : 0, "%s:%lld:%lld:%lld;", d, (long long)sc.st_size,
                                         (long long)sc.st_mtime, (long long)scr.st_mtime);
            }
        }
        free(e[i]);
    }
    if (n >= 0) free(e);
    return k;
}

/* Duplicates (docs/libd.md#duplicates): a server song that is already a card song is left out of servertable, so the
 * Library lists the card's file once, and serverdup names the card file each was (the payload's cat_match, its tags
 * layer): the same title, album and track, the same artist or album artist (case aside), and lengths within 2 s, both
 * known. Matched again at each catalogue write, which follows each card scan too. */
static void dups(sqlite3 *d) {
    struct stat st;
    exec_sql(d, "create table serverdup(id integer primary key, fileurl)");
    if (stat(path("/mnt/data/vamp/library.db"), &st)) return;
    char *q = sqlite3_mprintf("attach %Q as card", path("/mnt/data/vamp/library.db"));
    int bad = exec_sql(d, q);
    sqlite3_free(q);
    if (bad) return;
    exec_sql(d, "begin;insert or ignore into serverdup select s.id, c.fileurl from servertable s join card.songtable c"
                " on c.name = s.name collate nocase and c.album = s.album collate nocase and c.track = s.track"
                " and (c.songer = s.songer collate nocase or (c.albumsonger <> '' and c.albumsonger = s.albumsonger collate nocase))"
                " and c.time > 0 and s.time > 0 and abs(c.time - s.time) <= 2 where s.name <> '';"
                "delete from servertable where id in (select id from serverdup);"
                "delete from servermeta where id in (select id from serverdup);commit");
    sqlite3_stmt *c;
    if (sqlite3_prepare_v2(d, "select count(*) from serverdup", -1, &c, 0) == SQLITE_OK) {
        if (sqlite3_step(c) == SQLITE_ROW && sqlite3_column_int(c, 0)) say("%d server songs are card songs: left out", sqlite3_column_int(c, 0));
        sqlite3_finalize(c);
    }
}

/* ---- offline copies: catalogue.avail (docs/libd.md#offline-copies) ----
 * An app appends "<sid>\t<local path>" when a song's copy is whole on the card and "-<sid>" when it is evicted; each
 * line sets a state, so the lines are applied in order from where the last reading stopped, and from the start when
 * the file is shorter than that (rewritten) or a catalogue is imported. A cut last line waits for its newline. */
static char cat_ids[CAT_APPS][64]; /* the apps of the catalogue.db written last, by slot */
static int cat_n;
static long avail_at[CAT_APPS]; /* catalogue.avail read this far, by slot */

static long avail_size(int slot) {
    char p[300];
    struct stat st;
    snprintf(p, sizeof p, "/mnt/mmc/.%s/catalogue.avail", cat_ids[slot]);
    return stat(path(p), &st) ? -1 : (long)st.st_size;
}

/* slot's catalogue.avail from byte from into d's servermeta; where the reading stopped */
static long apply_avail(sqlite3 *d, int slot, long from) {
    char p[300], *line = malloc(LINE + 2), *f[2];
    snprintf(p, sizeof p, "/mnt/mmc/.%s/catalogue.avail", cat_ids[slot]);
    FILE *in = line ? fopen(path(p), "r") : 0;
    sqlite3_stmt *up = 0;
    if (!in || fseek(in, from, SEEK_SET) ||
        sqlite3_prepare_v2(d, "update servermeta set local = ?1 where app = ?2 and sid = ?3", -1, &up, 0) != SQLITE_OK) {
        if (in) fclose(in);
        return free(line), from;
    }
    long at = from;
    while (fgets(line, LINE + 1, in)) {
        size_t k = strlen(line);
        if (k && line[k - 1] != '\n' && k == LINE) { /* too long for a path: passed over once its newline is there */
            long skip = (long)k;
            int c;
            while ((c = fgetc(in)) != EOF && c != '\n') ++skip;
            if (c == EOF) break;
            at += skip + 1;
            continue;
        }
        if (!k || line[k - 1] != '\n') break; /* a cut last line waits for its newline */
        at += (long)k;
        while (k && (line[k - 1] == '\n' || line[k - 1] == '\r')) line[--k] = 0;
        int gone = line[0] == '-', n = split(line + gone, f, 2);
        if (!gone && n >= 2 && card_path(f[1])) sqlite3_bind_text(up, 1, f[1], -1, SQLITE_TRANSIENT);
        else sqlite3_bind_null(up, 1);
        sqlite3_bind_text(up, 2, cat_ids[slot], -1, SQLITE_STATIC);
        sqlite3_bind_text(up, 3, f[0], -1, SQLITE_TRANSIENT);
        sqlite3_step(up);
        sqlite3_reset(up);
    }
    sqlite3_finalize(up);
    fclose(in);
    free(line);
    return at;
}

/* the live catalogue.db's servermeta brought up to each app's catalogue.avail, in place */
static void look_avail(void) {
    sqlite3 *d = 0;
    for (int i = 0; i < cat_n; ++i) {
        long size = avail_size(i);
        if (size < 0 || size == avail_at[i]) continue;
        if (!d && (sqlite3_open_v2(path("/mnt/mmc/.vamp/catalogue.db"), &d, SQLITE_OPEN_READWRITE, 0) != SQLITE_OK ||
                   exec_sql(d, "begin immediate")))
            break;
        avail_at[i] = apply_avail(d, i, size < avail_at[i] ? 0 : avail_at[i]);
    }
    if (d && exec_sql(d, "commit")) say("catalogue.avail not applied");
    sqlite3_close(d);
}

static int scan_catalogue(void) {
    char ids[CAT_APPS][64], stamp[1024], final[600], tmp[620];
    int n = apps(ids, stamp, sizeof stamp);
    snprintf(final, sizeof final, "%s", path("/mnt/mmc/.vamp/catalogue.db"));
    snprintf(tmp, sizeof tmp, "%s.new", final);
    mkdir(path("/mnt/mmc/.vamp"), 0755);
    unlink(tmp);
    sqlite3 *d;
    sqlite3_stmt *st = 0, *meta = 0;
    if (sqlite3_open(tmp, &d) != SQLITE_OK ||
        exec_sql(d, "create table servertable(id int, filename, fileurl, album, songer, style, name, composer, albumsonger,"
                    " year int, track int, cd_num int, time int, time_end int, time_create int, Initial int, bitdepth int,"
                    " samprate int); create table servermeta(id integer primary key, app, sid, local);"
                    " create index servermeta_sid on servermeta(app, sid); begin") ||
        sqlite3_prepare_v2(d, "insert into servermeta values (?,?,?,?)", -1, &meta, 0) != SQLITE_OK ||
        sqlite3_prepare_v2(d, "insert into servertable(id, filename, fileurl, album, songer, style, name, albumsonger, year, track,"
                              " cd_num, time, Initial) values (?,?,?,?,?,?,?,?,?,?,?,?,?)", -1, &st, 0) != SQLITE_OK)
        return sqlite3_finalize(meta), sqlite3_close(d), -1;
    memcpy(cat_ids, ids, sizeof ids), cat_n = n;
    for (int i = 0; i < n; ++i) {
        import_app(d, st, meta, ids[i], CAT_ID_BASE + (unsigned)i * CAT_APP_SPAN);
        avail_at[i] = apply_avail(d, i, 0); /* the whole file again: the catalogue's paths, then its lines */
    }
    sqlite3_finalize(st), sqlite3_finalize(meta);
    int ok = !exec_sql(d, "commit");
    if (ok) dups(d); /* after the commit: an attach cannot be made inside a transaction */
    sqlite3_close(d);
    if (!ok || rename(tmp, final)) return cat_n = 0, -1; /* the old file's slots are not known: its lines wait */
    return 0;
}

/* ---- when ---- */

static void run_after(void) {
    if (*after && system(after) != 0) say("%s failed", after);
}

static void card_stamp(char *out, size_t room) { /* the card's mount line, "" when it is not mounted */
    char line[600];
    FILE *f = fopen(path("/proc/mounts"), "r");
    out[0] = 0;
    while (f && fgets(line, sizeof line, f))
        if (strstr(line, " /mnt/mmc ")) snprintf(out, room, "%s", line);
    if (f) fclose(f);
}

static void on_hup(int s) { (void)s, again = 1; }
static void on_term(int s) { (void)s, stopping = 1; }

int main(int argc, char **argv) {
    for (int i = 1; i < argc; ++i) {
        if (!strcmp(argv[i], "--root") && i + 1 < argc) snprintf(root, sizeof root, "%s", argv[++i]);
        else if (!strcmp(argv[i], "--once")) once = 1;
        else if (!strcmp(argv[i], "--after") && i + 1 < argc) snprintf(after, sizeof after, "%s", argv[++i]);
        else return fprintf(stderr, "usage: q2libd [--root DIR] [--once] [--after COMMAND]\n"), 2;
    }
    av_log_set_level(AV_LOG_QUIET);
    signal(SIGHUP, on_hup);
    signal(SIGTERM, on_term);
    signal(SIGINT, on_term);
    char card[600] = "", seen_card[600] = "-", cat[1024], seen_cat[1024] = "-", ids[CAT_APPS][64], pls[2048], seen_pls[2048] = "-";
    struct stat st;
    if (!once && stat(path("/mnt/data/vamp/library.db"), &st) && !scan_card(0)) run_after(); /* none yet: an empty one */
    while (!stopping) {
        card_stamp(card, sizeof card);
        apps(ids, cat, sizeof cat);
        playlist_stamp(pls, sizeof pls);
        int mounted = once || *card;
        int wrote = 0, card_wrote = 0;
        if (mounted && (again || strcmp(card, seen_card) || strcmp(pls, seen_pls))) {
            wrote |= card_wrote = !scan_card(1);
            snprintf(seen_card, sizeof seen_card, "%s", card), snprintf(seen_pls, sizeof seen_pls, "%s", pls);
        }
        if (mounted && (again || card_wrote || strcmp(cat, seen_cat))) { /* a card scan: its duplicates matched again */
            wrote |= !scan_catalogue();
            snprintf(seen_cat, sizeof seen_cat, "%s", cat);
        }
        if (wrote) run_after();
        again = 0;
        if (once) break;
        for (int s = 0; s < LOOK_S && !again && !stopping; ++s) { /* a signal ends a sleep early */
            sleep(1);
            if (mounted) look_avail();
        }
    }
    return 0;
}
