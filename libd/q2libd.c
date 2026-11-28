/* q2libd: VampOS's library service (docs/vamp-os.md#who-owns-what, docs/libd.md). It keeps two
 * databases in the shapes q2ui and q2playd already read, so they need no change:
 *
 *   - /mnt/data/vamp/library.db: the card's songs, a songtable with stock's columns (id, filename, fileurl, album,
 *     songer, style, name, composer, albumsonger, year, track, cd_num, time, ...), from a walk of /mnt/mmc (a CUE
 *     sheet's tracks as rows of its file) and each file's tags through FFmpeg's demuxers. A song keeps its id across
 *     scans while its path stays; a file whose size
 *     and time are unchanged is not read again. Stock's own /mnt/data/database.db is never touched.
 *   - /mnt/mmc/.vamp/catalogue.db: the server library, a servertable of every signed-in card app's catalogue
 *     (/mnt/mmc/.<id>/catalogue.tsv, Q2CAT/1, docs/server-library.md), ids from 0x40000000 as the payload gives them.
 *     On the card, as today's Vamp keeps its own (/mnt/data is too small for a large server), but a file of VampOS's
 *     own, so today's Vamp's catalogue.db is never touched either. A server song that is a card song is left out
 *     (serverdup names it). servermeta gives each server song's app, sid and offline copy (docs/libd.md#offline-copies):
 *     the catalogue's local path, then catalogue.avail's lines. serverpl and serveritem are the servers' playlists
 *     and their songs in order (docs/libd.md#the-servers-playlists).
 *   - Besides: each album's cover in /mnt/mmc/.vamp/covers/ (vamp_cover names each song's), and the card's M3U
 *     playlists (/mnt/mmc/_explaylist_data/) in library.db's playlist, stock's table.
 *
 * Each is written whole beside itself and renamed over it, so a reader never sees half a scan; only catalogue.avail's
 * new lines change servermeta in place, one transaction each look, looked at every second. It scans at its start
 * once the card is mounted, when the card's mount or a playlist file changes, when an app's catalogue.tsv or credentials change (looked
 * at every 10 s), and at SIGHUP; --once scans and ends. Before the card is there it writes an empty library, so a
 * reader always has one; after each write it runs --after's command (the image: q2ui's restart in place, so its lists
 * are the new ones). --root prefixes every path, for test/libd.py.
 *
 * Unless --once, it serves Q2LIB/1 on /run/vamp/lib.sock (docs/platform/library.md#q2lib1-the-library-socket) from a
 * thread: queries of the two files, read-only, for callers with the library permission. */
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

#include <poll.h>
#include <pthread.h>
#include <sys/socket.h>
#include <sys/un.h>

#include "q2perm.h" /* perm/: the library permission's check */

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

/* A row's id by its key in vamp_files (a file's path; a CUE track's path#N), the last scan's when the key is there, and
 * whether its size and time are those the last scan saw: then its rows are copied from the old library, 1, else 0. */
static int keep_id(const char *key, long long size, long long mtime, int *id) {
    int same = 0;
    *id = 0;
    if (find) {
        sqlite3_bind_text(find, 1, key, -1, SQLITE_STATIC);
        if (sqlite3_step(find) == SQLITE_ROW) {
            *id = sqlite3_column_int(find, 0);
            same = sqlite3_column_int64(find, 1) == size && sqlite3_column_int64(find, 2) == mtime;
        }
        sqlite3_reset(find);
    }
    if (!*id) *id = next_id++;
    if (same) {
        char *e = 0;
        char *q = sqlite3_mprintf("insert into songtable select * from old.songtable where id = %d", *id);
        if (sqlite3_exec(db, q, 0, 0, &e) != SQLITE_OK) say("%s", e), sqlite3_free(e), same = 0;
        sqlite3_free(q);
        if (sqlite3_changes(db) != 1) same = 0;
        q = sqlite3_mprintf("insert into vamp_cover select * from old.vamp_cover where id = %d", *id);
        if (same) sqlite3_exec(db, q, 0, 0, 0); /* none in a library from before covers: read again next time it changes */
        sqlite3_free(q);
    }
    return same;
}

static void stamp(const char *key, long long size, long long mtime, int id) {
    sqlite3_bind_text(ins_file, 1, key, -1, SQLITE_TRANSIENT);
    sqlite3_bind_int64(ins_file, 2, size);
    sqlite3_bind_int64(ins_file, 3, mtime);
    sqlite3_bind_int(ins_file, 4, id);
    sqlite3_step(ins_file);
    sqlite3_reset(ins_file);
    ++songs;
}

/* A song's tags and stream as add_file and add_cue read them */
struct song {
    const char *title, *artist, *album, *aa, *genre, *composer;
    int year, track, disc, seconds, rate, bits;
};

static AVFormatContext *read_song(const char *full, const char *url, struct song *g) {
    AVFormatContext *f = 0;
    *g = (struct song){"", "", "", "", "", "", 0, 0, 0, 0, 0, 0};
    if (avformat_open_input(&f, full, 0, 0) != 0) return say("cannot read %s", url), (AVFormatContext *)0;
    avformat_find_stream_info(f, 0);
    AVDictionary *m = f->metadata;
    if (f->nb_streams && !av_dict_count(m)) m = f->streams[0]->metadata; /* Ogg keeps them on the stream */
    g->title = tag(m, "title"), g->artist = tag(m, "artist"), g->album = tag(m, "album"), g->aa = tag(m, "album_artist");
    g->genre = tag(m, "genre"), g->composer = tag(m, "composer");
    g->year = atoi(tag(m, "date")), g->track = atoi(tag(m, "track")), g->disc = atoi(tag(m, "disc"));
    if (f->duration > 0) g->seconds = (int)(f->duration / AV_TIME_BASE);
    for (unsigned i = 0; i < f->nb_streams; ++i)
        if (f->streams[i]->codecpar->codec_type == AVMEDIA_TYPE_AUDIO) {
            g->rate = f->streams[i]->codecpar->sample_rate;
            g->bits = f->streams[i]->codecpar->bits_per_raw_sample;
            break;
        }
    return f;
}

/* One songtable row. time and time_end are stock's CUE start and end in seconds (patch/offsets.inc REC_CUE_START, the
 * column patch/catalogue.c reads as it; q2ui's lib.c takes time_end - time as the length): a whole file is 0 to its
 * length, a CUE track its start to the next one's (docs/libd.md#cue-sheets). */
static void insert_row(int id, const char *url, const struct song *g, const char *name, int start, int end, long long mtime) {
    const char *base = strrchr(url, '/') ? strrchr(url, '/') + 1 : url;
    int c = 1;
    sqlite3_bind_int(ins, c++, id);
    sqlite3_bind_text(ins, c++, base, -1, SQLITE_TRANSIENT);
    sqlite3_bind_text(ins, c++, url, -1, SQLITE_TRANSIENT);
    sqlite3_bind_text(ins, c++, g->album, -1, SQLITE_TRANSIENT);
    sqlite3_bind_text(ins, c++, g->artist, -1, SQLITE_TRANSIENT);
    sqlite3_bind_text(ins, c++, g->genre, -1, SQLITE_TRANSIENT);
    sqlite3_bind_text(ins, c++, name, -1, SQLITE_TRANSIENT);
    sqlite3_bind_text(ins, c++, g->composer, -1, SQLITE_TRANSIENT);
    sqlite3_bind_text(ins, c++, g->aa, -1, SQLITE_TRANSIENT);
    sqlite3_bind_int(ins, c++, g->year);
    sqlite3_bind_int(ins, c++, g->track);
    sqlite3_bind_int(ins, c++, g->disc);
    sqlite3_bind_int(ins, c++, start);
    sqlite3_bind_int(ins, c++, end);
    sqlite3_bind_int(ins, c++, (int)mtime);
    sqlite3_bind_int(ins, c++, initial(name));
    sqlite3_bind_int(ins, c++, g->bits);
    sqlite3_bind_int(ins, c++, g->rate);
    if (sqlite3_step(ins) != SQLITE_DONE) say("insert %s: %s", url, sqlite3_errmsg(db));
    sqlite3_reset(ins);
}

static void add_file(const char *full, const char *url, const struct stat *st) {
    int id;
    if (!keep_id(url, st->st_size, st->st_mtime, &id)) {
        struct song g;
        AVFormatContext *f = read_song(full, url, &g);
        const char *base = strrchr(url, '/') ? strrchr(url, '/') + 1 : url;
        char name[1200];
        snprintf(name, sizeof name, "%s", base);
        if (!*g.title) { /* no title tag: the file's name without its extension, as stock lists it */
            char *dot = strrchr(name, '.');
            if (dot) *dot = 0;
        }
        insert_row(id, url, &g, *g.title ? g.title : name, 0, g.seconds, st->st_mtime);
        cover_of(id, f, full, url, *g.aa ? g.aa : g.artist, g.album);
        if (f) avformat_close_input(&f);
        ++read_n;
    }
    stamp(url, st->st_size, st->st_mtime, id);
}

/* ---- CUE sheets (docs/libd.md#cue-sheets) ----
 * A .cue beside its audio: each TRACK a row of the FILE it is in, from its INDEX 01 to the next track's (the file's
 * last track to the file's end), with the sheet's TITLE as the album and PERFORMER as the album artist, a track's own
 * TITLE and PERFORMER over them, REM DATE, GENRE and DISCNUMBER. A FILE named with another audio extension than the
 * file on the card has (a sheet made for the .wav before it was compressed) is found by its name without the
 * extension. The audio a sheet names is listed only through it. ponytail: a sheet in a legacy code page (stock read
 * GBK) shows its bytes as they are; uchardet and iconv would read it. */
#define CUE_TRACKS 99
#define CUE_FILES 16

struct cue_track { int file, n, start; char title[256], performer[256]; };
struct cue {
    char title[256], performer[256], genre[128], files[CUE_FILES][256];
    int year, disc, n_files, n;
    struct cue_track t[CUE_TRACKS];
};

/* the word after a keyword: "quoted text" or up to the end of the line */
static void cue_text(const char *s, char *out, size_t room) {
    while (*s == ' ' || *s == '\t') ++s;
    size_t k = 0;
    if (*s == '"') {
        for (++s; *s && *s != '"' && k + 1 < room; ++s) out[k++] = *s;
    } else {
        for (; *s && *s != '\r' && *s != '\n' && k + 1 < room; ++s) out[k++] = *s;
        while (k && (out[k - 1] == ' ' || out[k - 1] == '\t')) --k;
    }
    out[k] = 0;
}

static int cue_read(const char *full, struct cue *c) {
    FILE *f = fopen(full, "r");
    char line[1100];
    memset(c, 0, sizeof *c);
    if (!f) return -1;
    for (int first = 1; fgets(line, sizeof line, f); first = 0) {
        char *l = line;
        if (first && !strncmp(l, "\xef\xbb\xbf", 3)) l += 3; /* a UTF-8 BOM */
        while (*l == ' ' || *l == '\t') ++l;
        struct cue_track *t = c->n ? &c->t[c->n - 1] : 0;
        int mm, ss, ff;
        if (!strncasecmp(l, "FILE ", 5)) {
            if (c->n_files < CUE_FILES) cue_text(l + 5, c->files[c->n_files++], sizeof c->files[0]);
        } else if (!strncasecmp(l, "TRACK ", 6)) {
            if (c->n < CUE_TRACKS && c->n_files) c->t[c->n++] = (struct cue_track){.file = c->n_files - 1, .n = atoi(l + 6), .start = -1};
        } else if (!strncasecmp(l, "TITLE ", 6)) {
            cue_text(l + 6, t ? t->title : c->title, sizeof c->title);
        } else if (!strncasecmp(l, "PERFORMER ", 10)) {
            cue_text(l + 10, t ? t->performer : c->performer, sizeof c->performer);
        } else if (!strncasecmp(l, "INDEX 01 ", 9) && t && sscanf(l + 9, "%d:%d:%d", &mm, &ss, &ff) == 3) {
            t->start = mm * 60 + ss; /* whole seconds, as stock's column and the player's ss= take it */
        } else if (!strncasecmp(l, "REM DATE ", 9) && !t) {
            c->year = atoi(l + 9);
        } else if (!strncasecmp(l, "REM GENRE ", 10) && !t) {
            cue_text(l + 10, c->genre, sizeof c->genre);
        } else if (!strncasecmp(l, "REM DISCNUMBER ", 15) && !t) {
            c->disc = atoi(l + 15);
        }
    }
    fclose(f);
    return 0;
}

/* the file a sheet's FILE names among the folder's entries: as named, else the same name with an audio extension;
 * its name in found */
static int cue_file(const char *named, struct dirent **e, int n, char *found, size_t room) {
    const char *dot = strrchr(named, '.');
    size_t stem = dot ? (size_t)(dot - named) : strlen(named);
    for (int pass = 0; pass < 2; ++pass)
        for (int i = 0; i < n; ++i) {
            const char *d = e[i]->d_name;
            if (!audio(d)) continue;
            if (pass ? !strncmp(d, named, stem) && d[stem] == '.' : !strcmp(d, named))
                return snprintf(found, room, "%s", d), 0;
        }
    return -1;
}

/* a sheet's tracks into songtable; the audio files it took, by name, added to took */
static void add_cue(const char *rel, const char *cue_name, struct dirent **e, int n, char took[][256], int *n_took, int max_took) {
    static struct cue c;
    char full[1500];
    struct stat cst;
    snprintf(full, sizeof full, "%s%s/%s", root, rel, cue_name);
    if (stat(full, &cst) || cue_read(full, &c)) return;
    for (int fi = 0; fi < c.n_files; ++fi) {
        char name[256], url[1200], afull[1500];
        struct stat st;
        if (cue_file(c.files[fi], e, n, name, sizeof name)) {
            say("%s/%s: no %s beside it", rel, cue_name, c.files[fi]);
            continue;
        }
        snprintf(url, sizeof url, "%s/%s", rel, name);
        snprintf(afull, sizeof afull, "%s%s", root, url);
        if (stat(afull, &st)) continue;
        long long mtime = st.st_mtime > cst.st_mtime ? st.st_mtime : cst.st_mtime, size = st.st_size + cst.st_size;
        struct song g = {"", "", "", "", "", "", 0, 0, 0, 0, 0, 0};
        AVFormatContext *f = 0;
        int opened = 0, rows = 0;
        for (int i = 0; i < c.n; ++i) {
            struct cue_track *t = &c.t[i];
            if (t->file != fi || t->start < 0) continue;
            char key[1300];
            int id;
            snprintf(key, sizeof key, "%s#%d", url, t->n);
            ++rows;
            if (!keep_id(key, size, mtime, &id)) {
                if (!opened++) f = read_song(afull, url, &g);
                int end = g.seconds;
                for (int j = i + 1; j < c.n; ++j)
                    if (c.t[j].file == fi && c.t[j].start >= 0) { end = c.t[j].start; break; }
                struct song row = g;
                char title[300];
                row.album = c.title, row.aa = c.performer, row.artist = *t->performer ? t->performer : c.performer;
                row.genre = c.genre, row.year = c.year, row.disc = c.disc, row.track = t->n;
                if (*t->title) snprintf(title, sizeof title, "%s", t->title);
                else snprintf(title, sizeof title, "Track %02d", t->n);
                insert_row(id, url, &row, title, t->start, end, mtime);
                cover_of(id, f, afull, url, row.aa[0] ? row.aa : row.artist, row.album);
                ++read_n;
            }
            stamp(key, size, mtime, id);
        }
        if (f) avformat_close_input(&f);
        if (rows && *n_took < max_took) snprintf(took[(*n_took)++], 256, "%s", name);
    }
}

/* The card's tree under dir (its path as the Q2 names it, rel), in name order; dot folders and files are an app's or
 * the system's, not music. A folder's CUE sheets first, so the audio they name is listed as their tracks alone. */
static void walk(const char *rel, int depth) {
    struct dirent **e;
    char full[1200], took[32][256];
    int n_took = 0;
    snprintf(full, sizeof full, "%s%s", root, rel);
    int n = depth > 16 ? -1 : scandir(full, &e, 0, alphasort);
    for (int i = 0; i < n; ++i) {
        const char *d = e[i]->d_name, *dot = strrchr(d, '.');
        if (d[0] != '.' && dot && !strcasecmp(dot, ".cue")) add_cue(rel, d, e, n, took, &n_took, 32);
    }
    for (int i = 0; i < n; ++i) {
        if (e[i]->d_name[0] != '.') {
            char r[1200], f[1500];
            struct stat st;
            int taken = 0;
            for (int k = 0; k < n_took && !taken; ++k) taken = !strcmp(took[k], e[i]->d_name);
            snprintf(r, sizeof r, "%s/%s", rel, e[i]->d_name);
            snprintf(f, sizeof f, "%s%s", root, r);
            if (!taken && !stat(f, &st)) {
                if (S_ISDIR(st.st_mode)) walk(r, depth + 1);
                else if (S_ISREG(st.st_mode) && audio(e[i]->d_name)) add_file(f, r, &st);
            }
        }
    }
    for (int i = 0; i < n; ++i) free(e[i]);
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
                           " track, cd_num, time, time_end, time_create, Initial, bitdepth, samprate)"
                           " values (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
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
 * path), ids from base; its p and i rows into serverpl (pl) and serveritem (item, each at the next pos of its
 * playlist, its path a synced song's card file or NULL). Its rows=, counted over s, p and i lines, must be
 * met, so a file cut short is not taken for a smaller library: -1 then, and nothing of it is kept. */
static int import_app(sqlite3 *d, sqlite3_stmt *st, sqlite3_stmt *meta, sqlite3_stmt *pl, sqlite3_stmt *item, const char *id,
                      unsigned base) {
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
        if (!strcmp(f[0], "p") && n >= 3) { /* p pid name */
            sqlite3_bind_text(pl, 1, id, -1, SQLITE_TRANSIENT), sqlite3_bind_text(pl, 2, f[1], -1, SQLITE_TRANSIENT);
            sqlite3_bind_text(pl, 3, f[2], -1, SQLITE_TRANSIENT);
            sqlite3_step(pl), sqlite3_reset(pl);
            continue;
        }
        if (!strcmp(f[0], "i") && n >= 3) { /* i pid sid [card path] */
            sqlite3_bind_text(item, 1, id, -1, SQLITE_TRANSIENT), sqlite3_bind_text(item, 2, f[1], -1, SQLITE_TRANSIENT);
            sqlite3_bind_text(item, 3, f[2], -1, SQLITE_TRANSIENT);
            if (n > 3 && card_path(f[3])) sqlite3_bind_text(item, 4, f[3], -1, SQLITE_TRANSIENT);
            else sqlite3_bind_null(item, 4);
            sqlite3_step(item), sqlite3_reset(item);
            continue;
        }
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
                " and c.time_end - c.time > 0 and s.time > 0 and abs(c.time_end - c.time - s.time) <= 2 where s.name <> '';"
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
    sqlite3_stmt *st = 0, *meta = 0, *pl = 0, *item = 0;
    if (sqlite3_open(tmp, &d) != SQLITE_OK ||
        exec_sql(d, "create table servertable(id int, filename, fileurl, album, songer, style, name, composer, albumsonger,"
                    " year int, track int, cd_num int, time int, time_end int, time_create int, Initial int, bitdepth int,"
                    " samprate int); create table servermeta(id integer primary key, app, sid, local);"
                    " create index servermeta_sid on servermeta(app, sid);"
                    " create table serverpl(app, pid, name); create table serveritem(app, pid, pos int, sid, path);"
                    " create index serveritem_pid on serveritem(app, pid); begin") ||
        sqlite3_prepare_v2(d, "insert into servermeta values (?,?,?,?)", -1, &meta, 0) != SQLITE_OK ||
        sqlite3_prepare_v2(d, "insert into serverpl values (?,?,?)", -1, &pl, 0) != SQLITE_OK ||
        sqlite3_prepare_v2(d, "insert into serveritem select ?1, ?2, (select count(*) from serveritem where app = ?1 and pid = ?2),"
                              " ?3, ?4", -1, &item, 0) != SQLITE_OK ||
        sqlite3_prepare_v2(d, "insert into servertable(id, filename, fileurl, album, songer, style, name, albumsonger, year, track,"
                              " cd_num, time, Initial) values (?,?,?,?,?,?,?,?,?,?,?,?,?)", -1, &st, 0) != SQLITE_OK)
        return sqlite3_finalize(meta), sqlite3_finalize(pl), sqlite3_finalize(item), sqlite3_close(d), -1;
    memcpy(cat_ids, ids, sizeof ids), cat_n = n;
    for (int i = 0; i < n; ++i) {
        import_app(d, st, meta, pl, item, ids[i], CAT_ID_BASE + (unsigned)i * CAT_APP_SPAN);
        avail_at[i] = apply_avail(d, i, 0); /* the whole file again: the catalogue's paths, then its lines */
    }
    sqlite3_finalize(st), sqlite3_finalize(meta), sqlite3_finalize(pl), sqlite3_finalize(item);
    int ok = !exec_sql(d, "commit");
    if (ok) dups(d); /* after the commit: an attach cannot be made inside a transaction */
    sqlite3_close(d);
    if (!ok || rename(tmp, final)) return cat_n = 0, -1; /* the old file's slots are not known: its lines wait */
    return 0;
}

/* ---- Q2LIB/1: the library socket (docs/platform/library.md#q2lib1-the-library-socket) ----
 *
 * A thread of its own, so a scan never holds an answer up. Each command opens the two files as they are (read-only,
 * catalogue.db attached as cat) through the temp view s, card and server songs alike. It uses none of the scan's
 * state: its paths are made once at its start (path()'s buffers are the main thread's). */

#define LIB_CLIENTS 8
#define PAGE_DEFAULT 100
#define PAGE_MAX 500

static struct lclient { int fd, hello, watch; size_t n; char in[513]; } lc[LIB_CLIENTS];
static char lib_db[600], cat_db[600], lib_passwd[600], lib_group[600], lib_sock[600];

static void lenc(char *out, size_t room, const char *in) { /* Vamp API 1's percent-encoding (docs/platform/README.md) */
    static const char hex[] = "0123456789ABCDEF";
    size_t j = 0;
    for (const unsigned char *p = (const unsigned char *)(in ? in : ""); *p && j + 4 < room; ++p)
        if (*p < 0x21 || *p == '%' || *p == '"' || *p == '=' || *p == 0x7f) out[j++] = '%', out[j++] = hex[*p >> 4], out[j++] = hex[*p & 15];
        else out[j++] = (char)*p;
    out[j] = 0;
}

static void ldec(char *s) {
    char *o = s;
    for (; *s; ++s)
        if (*s == '%' && hexv(s[1]) >= 0 && hexv(s[2]) >= 0) *o++ = (char)(hexv(s[1]) << 4 | hexv(s[2])), s += 2;
        else *o++ = *s;
    *o = 0;
}

static void lsay(struct lclient *c, const char *fmt, ...) { /* a whole line, or the client closed (2 s at most: SO_SNDTIMEO) */
    char line[4096];
    va_list ap;
    va_start(ap, fmt);
    int n = vsnprintf(line, sizeof line - 1, fmt, ap);
    va_end(ap);
    if (n < 0 || c->fd < 0) return;
    if (n > (int)sizeof line - 2) n = (int)sizeof line - 2;
    line[n++] = '\n';
    for (int off = 0; off < n;) {
        ssize_t w = send(c->fd, line + off, (size_t)(n - off), MSG_NOSIGNAL);
        if (w <= 0) {
            close(c->fd), c->fd = -1;
            return;
        }
        off += (int)w;
    }
}

/* SQLite's file: URI for a path (its %, ? and # escaped), read-only */
static void ro_uri(char *out, size_t room, const char *p) {
    size_t j = (size_t)snprintf(out, room, "file:");
    for (; *p && j + 4 < room; ++p)
        if (*p == '%' || *p == '?' || *p == '#') j += (size_t)snprintf(out + j, room - j, "%%%02X", (unsigned char)*p);
        else out[j++] = *p;
    snprintf(out + j, room - j, "?mode=ro");
}

static sqlite3 *lib_open(void) {
    char uri[1400];
    sqlite3 *d = 0;
    ro_uri(uri, sizeof uri, lib_db);
    if (sqlite3_open_v2(uri, &d, SQLITE_OPEN_READONLY | SQLITE_OPEN_URI, 0) != SQLITE_OK) return sqlite3_close(d), (sqlite3 *)0;
    sqlite3_busy_timeout(d, 2000);
    struct stat st;
    sqlite3_stmt *at = 0;
    ro_uri(uri, sizeof uri, cat_db);
    int cat = !stat(cat_db, &st) && sqlite3_prepare_v2(d, "attach database ? as cat", -1, &at, 0) == SQLITE_OK &&
              sqlite3_bind_text(at, 1, uri, -1, SQLITE_TRANSIENT) == SQLITE_OK && sqlite3_step(at) == SQLITE_DONE;
    sqlite3_finalize(at);
    if (!cat && exec_sql(d, "attach database ':memory:' as cat; create table cat.servertable(id int, filename, fileurl, album,"
                            " songer, style, name, composer, albumsonger, year int, track int, cd_num int, time int);"
                            " create table cat.servermeta(id integer primary key, app, sid, local);"
                            " create table cat.serverpl(app, pid, name); create table cat.serveritem(app, pid, pos int, sid, path)"))
        return sqlite3_close(d), (sqlite3 *)0;
    if (exec_sql(d, "create temp view s as"
                    " select id, name, songer, albumsonger, album, style, composer, year, track, cd_num,"
                    " max(time_end - time, 0) as time, 'card' as source,"
                    " fileurl, coalesce(nullif(albumsonger, ''), songer, '') as aa from main.songtable"
                    " union all select t.id, t.name, t.songer, t.albumsonger, t.album, t.style, t.composer, t.year, t.track,"
                    " t.cd_num, t.time, coalesce(m.app, ''), t.fileurl, coalesce(nullif(t.albumsonger, ''), t.songer, '')"
                    " from cat.servertable t left join cat.servermeta m on m.id = t.id"))
        return sqlite3_close(d), (sqlite3 *)0;
    return d;
}

#define SONG_COLS "s.id, s.name, s.songer, s.albumsonger, s.album, s.style, s.composer, s.year, s.track, s.cd_num, s.time, s.source"
#define SONG_ORDER " order by s.aa collate nocase, s.album collate nocase, s.cd_num, s.track, s.name collate nocase, s.id"

static const char *col(sqlite3_stmt *st, int i) {
    const unsigned char *t = sqlite3_column_text(st, i);
    return t ? (const char *)t : "";
}

static void song_line(struct lclient *c, sqlite3_stmt *st) {
    char t[6][1200];
    for (int i = 0; i < 6; ++i) lenc(t[i], sizeof t[i], col(st, 1 + i));
    char src[200];
    lenc(src, sizeof src, col(st, 11));
    lsay(c, "song id=%lld title=%s artist=%s albumartist=%s album=%s genre=%s composer=%s year=%d track=%d disc=%d time=%d source=%s",
         sqlite3_column_int64(st, 0), t[0], t[1], t[2], t[3], t[4], t[5], sqlite3_column_int(st, 7), sqlite3_column_int(st, 8),
         sqlite3_column_int(st, 9), sqlite3_column_int(st, 10), src);
}

/* a command's key=value words: each key one of keys (vals[i] its value, decoded, or 0), from and count numbers; -1 when
 * a word is not one of them or a number is not one */
struct largs { const char *v[8]; long from, count; };
static int largs(char *rest, const char *const *keys, struct largs *a) {
    memset(a, 0, sizeof *a), a->count = PAGE_DEFAULT;
    for (char *w = strtok(rest, " "); w; w = strtok(0, " ")) {
        char *eq = strchr(w, '=');
        if (!eq || eq == w) return -1;
        *eq++ = 0;
        if (!strcmp(w, "from") || !strcmp(w, "count")) {
            char *end;
            long n = strtol(eq, &end, 10);
            if (!*eq || *end || !isdigit((unsigned char)*eq) || n < 0 || (!strcmp(w, "count") && (n < 1 || n > PAGE_MAX))) return -1;
            *(w[0] == 'f' ? &a->from : &a->count) = n;
            continue;
        }
        int k = 0;
        while (keys[k] && strcmp(keys[k], w)) ++k;
        if (!keys[k]) return -1;
        ldec(eq), a->v[k] = eq;
    }
    return 0;
}

/* runs sql (its ?s: the given texts in order, then the page's count and from when page), a song line a row */
static int songs_of(struct lclient *c, sqlite3 *d, const char *sql, const char *const *texts, int nt, const struct largs *page) {
    sqlite3_stmt *st = 0;
    if (sqlite3_prepare_v2(d, sql, -1, &st, 0) != SQLITE_OK) return say("Q2LIB/1: %s", sqlite3_errmsg(d)), -1;
    int b = 1;
    for (int i = 0; i < nt; ++i) sqlite3_bind_text(st, b++, texts[i], -1, SQLITE_TRANSIENT);
    if (page) sqlite3_bind_int64(st, b++, page->count), sqlite3_bind_int64(st, b++, page->from);
    while (c->fd >= 0 && sqlite3_step(st) == SQLITE_ROW) song_line(c, st);
    sqlite3_finalize(st);
    return 0;
}

static void lib_command(struct lclient *c, char *line) {
    char *rest = strchr(line, ' ');
    if (rest) *rest++ = 0;
    else rest = line + strlen(line);
    if (!strcmp(line, "caps")) return lsay(c, "caps songs song albums artists genres playlists playlist search art watch");
    if (!strcmp(line, "watch")) return c->watch = 1, lsay(c, "ok");
    static const char *const SONG_KEYS[] = {"album", "artist", "genre", "source", 0}, *const NONE[] = {0},
                             *const ALBUM_KEYS[] = {"artist", 0}, *const PL_KEYS[] = {"id", "app", "pid", 0};
    int known = !strcmp(line, "songs") || !strcmp(line, "song") || !strcmp(line, "albums") || !strcmp(line, "artists") ||
                !strcmp(line, "genres") || !strcmp(line, "playlists") || !strcmp(line, "playlist") || !strcmp(line, "search") ||
                !strcmp(line, "art");
    if (!known) return lsay(c, "err command");
    struct largs a;
    char q[1500], *end;
    long long id = 0;
    char text[600] = "";
    if (!strcmp(line, "song") || !strcmp(line, "art")) {
        id = strtoll(rest, &end, 10);
        if (!isdigit((unsigned char)*rest) || *end) return lsay(c, "err argument");
    } else if (!strcmp(line, "search")) {
        char *sp = strchr(rest, ' ');
        if (sp) *sp++ = 0;
        snprintf(text, sizeof text, "%s", rest);
        ldec(text);
        if (!*text || largs(sp ? sp : rest + strlen(rest), NONE, &a)) return lsay(c, "err argument");
    } else if (largs(rest, !strcmp(line, "songs") ? SONG_KEYS : !strcmp(line, "albums") ? ALBUM_KEYS : !strcmp(line, "playlist") ? PL_KEYS : NONE, &a)) {
        return lsay(c, "err argument");
    }
    sqlite3 *d = lib_open();
    if (!d) return lsay(c, "err library");
    sqlite3_stmt *st = 0;
    if (!strcmp(line, "songs")) {
        static const char *const where[] = {" and s.album = ?%d collate nocase", " and (s.songer = ?%d collate nocase or s.albumsonger = ?%d collate nocase)",
                                            " and s.style = ?%d collate nocase", " and s.source = ?%d"};
        const char *texts[4];
        int nt = 0;
        size_t k = (size_t)snprintf(q, sizeof q, "select " SONG_COLS " from s where 1");
        for (int i = 0; i < 4; ++i)
            if (a.v[i]) texts[nt++] = a.v[i], k += (size_t)snprintf(q + k, sizeof q - k, where[i], nt, nt);
        snprintf(q + k, sizeof q - k, SONG_ORDER " limit ?%d offset ?%d", nt + 1, nt + 2);
        if (!songs_of(c, d, q, texts, nt, &a)) lsay(c, "ok");
        else lsay(c, "err library");
    } else if (!strcmp(line, "song")) {
        if (sqlite3_prepare_v2(d, "select " SONG_COLS " from s where s.id = ?", -1, &st, 0) == SQLITE_OK) {
            sqlite3_bind_int64(st, 1, id);
            if (sqlite3_step(st) == SQLITE_ROW) song_line(c, st);
            else lsay(c, "err none");
        }
    } else if (!strcmp(line, "search")) {
        char pat[1300];
        size_t j = 0;
        pat[j++] = '%';
        for (const char *p = text; *p && j + 4 < sizeof pat; ++p) {
            if (*p == '%' || *p == '_' || *p == '\\') pat[j++] = '\\';
            pat[j++] = *p;
        }
        pat[j++] = '%', pat[j] = 0;
        const char *texts[] = {pat};
        if (!songs_of(c, d, "select " SONG_COLS " from s where s.name like ?1 escape '\\' or s.songer like ?1 escape '\\'"
                            " or s.albumsonger like ?1 escape '\\' or s.album like ?1 escape '\\'" SONG_ORDER " limit ?2 offset ?3",
                      texts, 1, &a))
            lsay(c, "ok");
        else lsay(c, "err library");
    } else if (!strcmp(line, "albums")) {
        snprintf(q, sizeof q, "select s.album, s.aa, max(s.year), count(*) from s where coalesce(s.album, '') != ''%s"
                              " group by s.aa collate nocase, s.album collate nocase order by s.aa collate nocase, s.album collate nocase"
                              " limit ? offset ?", a.v[0] ? " and s.aa = ? collate nocase" : "");
        sqlite3_stmt *first = 0;
        if (sqlite3_prepare_v2(d, q, -1, &st, 0) == SQLITE_OK &&
            sqlite3_prepare_v2(d, "select s.id from s where s.album = ?1 collate nocase and s.aa = ?2 collate nocase"
                                  " order by s.cd_num, s.track, s.name collate nocase, s.id limit 1", -1, &first, 0) == SQLITE_OK) {
            int b = 1;
            if (a.v[0]) sqlite3_bind_text(st, b++, a.v[0], -1, SQLITE_TRANSIENT);
            sqlite3_bind_int64(st, b++, a.count), sqlite3_bind_int64(st, b, a.from);
            while (c->fd >= 0 && sqlite3_step(st) == SQLITE_ROW) {
                char al[1200], aa[1200];
                lenc(al, sizeof al, col(st, 0)), lenc(aa, sizeof aa, col(st, 1));
                sqlite3_reset(first);
                sqlite3_bind_text(first, 1, col(st, 0), -1, SQLITE_TRANSIENT), sqlite3_bind_text(first, 2, col(st, 1), -1, SQLITE_TRANSIENT);
                long long f = sqlite3_step(first) == SQLITE_ROW ? sqlite3_column_int64(first, 0) : 0;
                lsay(c, "album name=%s artist=%s year=%d songs=%d first=%lld", al, aa, sqlite3_column_int(st, 2), sqlite3_column_int(st, 3), f);
            }
            lsay(c, "ok");
        } else lsay(c, "err library");
        sqlite3_finalize(first);
    } else if (!strcmp(line, "artists") || !strcmp(line, "genres")) {
        int artists = line[0] == 'a';
        if (sqlite3_prepare_v2(d, artists ? "select s.aa, count(distinct case when coalesce(s.album, '') != '' then lower(s.album) end), count(*)"
                                            " from s where s.aa != '' group by s.aa collate nocase order by s.aa collate nocase limit ? offset ?"
                                          : "select s.style, 0, count(*) from s where coalesce(s.style, '') != '' group by s.style collate nocase"
                                            " order by s.style collate nocase limit ? offset ?", -1, &st, 0) == SQLITE_OK) {
            sqlite3_bind_int64(st, 1, a.count), sqlite3_bind_int64(st, 2, a.from);
            while (c->fd >= 0 && sqlite3_step(st) == SQLITE_ROW) {
                char n[1200];
                lenc(n, sizeof n, col(st, 0));
                if (artists) lsay(c, "artist name=%s albums=%d songs=%d", n, sqlite3_column_int(st, 1), sqlite3_column_int(st, 2));
                else lsay(c, "genre name=%s songs=%d", n, sqlite3_column_int(st, 2));
            }
            lsay(c, "ok");
        } else lsay(c, "err library");
    } else if (!strcmp(line, "playlists")) {
        if (sqlite3_prepare_v2(d, "select p.cd_num, p.name, (select count(*) from main.playlist i join s on s.fileurl = i.fileurl and"
                                  " s.source = 'card' where i.cd_num = p.cd_num and i.id > 100) from main.playlist p where p.id <= 100"
                                  " order by p.cd_num", -1, &st, 0) == SQLITE_OK) {
            while (c->fd >= 0 && sqlite3_step(st) == SQLITE_ROW) {
                char n[1200];
                lenc(n, sizeof n, col(st, 1));
                lsay(c, "playlist id=%d name=%s songs=%d", sqlite3_column_int(st, 0), n, sqlite3_column_int(st, 2));
            }
        }
        sqlite3_finalize(st), st = 0;
        if (sqlite3_prepare_v2(d, "select p.app, p.pid, p.name, (select count(*) from cat.serveritem i join s on (coalesce(i.path, '') != ''"
                                  " and s.source = 'card' and s.fileurl = i.path) or (coalesce(i.path, '') = '' and s.id = (select m.id from"
                                  " cat.servermeta m where m.app = i.app and m.sid = i.sid)) where i.app = p.app and i.pid = p.pid)"
                                  " from cat.serverpl p order by p.app, p.rowid", -1, &st, 0) == SQLITE_OK) {
            while (c->fd >= 0 && sqlite3_step(st) == SQLITE_ROW) {
                char ap[200], pid[600], n[1200];
                lenc(ap, sizeof ap, col(st, 0)), lenc(pid, sizeof pid, col(st, 1)), lenc(n, sizeof n, col(st, 2));
                lsay(c, "playlist app=%s pid=%s name=%s songs=%d", ap, pid, n, sqlite3_column_int(st, 3));
            }
        }
        lsay(c, "ok");
    } else if (!strcmp(line, "playlist")) {
        const char *texts[2] = {0, 0};
        int card = a.v[0] && !a.v[1] && !a.v[2], server = !a.v[0] && a.v[1] && a.v[2];
        long n = card ? strtol(a.v[0], &end, 10) : 0;
        if ((!card && !server) || (card && (*end || !isdigit((unsigned char)*a.v[0])))) {
            lsay(c, "err argument");
        } else {
            if (card) snprintf(q, sizeof q, "select count(*) from main.playlist where id <= 100 and cd_num = %ld", n);
            else snprintf(q, sizeof q, "select count(*) from cat.serverpl where app = ?1 and pid = ?2");
            int there = 0;
            if (sqlite3_prepare_v2(d, q, -1, &st, 0) == SQLITE_OK) {
                if (server) sqlite3_bind_text(st, 1, a.v[1], -1, SQLITE_TRANSIENT), sqlite3_bind_text(st, 2, a.v[2], -1, SQLITE_TRANSIENT);
                there = sqlite3_step(st) == SQLITE_ROW && sqlite3_column_int(st, 0) > 0;
            }
            if (!there) {
                lsay(c, "err none");
            } else if (card) {
                snprintf(q, sizeof q, "select " SONG_COLS " from main.playlist i join s on s.fileurl = i.fileurl and s.source = 'card'"
                                      " where i.cd_num = %ld and i.id > 100 order by i.id limit ?1 offset ?2", n);
                if (!songs_of(c, d, q, texts, 0, &a)) lsay(c, "ok");
                else lsay(c, "err library");
            } else {
                texts[0] = a.v[1], texts[1] = a.v[2];
                if (!songs_of(c, d, "select " SONG_COLS " from cat.serveritem i join s on (coalesce(i.path, '') != '' and s.source = 'card'"
                                    " and s.fileurl = i.path) or (coalesce(i.path, '') = '' and s.id = (select m.id from cat.servermeta m"
                                    " where m.app = i.app and m.sid = i.sid)) where i.app = ?1 and i.pid = ?2 order by i.pos limit ?3 offset ?4",
                              texts, 2, &a))
                    lsay(c, "ok");
                else lsay(c, "err library");
            }
        }
    } else if (!strcmp(line, "art")) {
        if (sqlite3_prepare_v2(d, "select c.file from main.vamp_cover c join main.songtable t on t.id = c.id where c.id = ?"
                                  " and coalesce(c.file, '') != ''", -1, &st, 0) == SQLITE_OK) {
            sqlite3_bind_int64(st, 1, id);
            if (sqlite3_step(st) == SQLITE_ROW) {
                char f[1200];
                lenc(f, sizeof f, col(st, 0));
                lsay(c, "art id=%lld file=%s", id, f);
            } else {
                lsay(c, "err none");
            }
        }
    }
    sqlite3_finalize(st);
    sqlite3_close(d);
}

static void lib_line(struct lclient *c, char *line) {
    if (!c->hello) {
        if (strcmp(line, "Q2LIB/1")) return lsay(c, "err version"), (void)(c->fd >= 0 && close(c->fd)), (void)(c->fd = -1);
        char who[100];
        if (!q2perm_allowed(c->fd, lib_passwd, lib_group, "library", who, sizeof who)) {
            say("refused %s: no library permission", who); /* docs/platform/packages.md#users-and-groups */
            lsay(c, "err permission");
            if (c->fd >= 0) close(c->fd), c->fd = -1;
            return;
        }
        c->hello = 1;
        return lsay(c, "Q2LIB/1 ok");
    }
    lib_command(c, line);
}

static void lib_read(struct lclient *c) {
    ssize_t got = recv(c->fd, c->in + c->n, sizeof c->in - 1 - c->n, 0);
    if (got <= 0) {
        close(c->fd), c->fd = -1;
        return;
    }
    c->n += (size_t)got;
    char *p = c->in, *nl;
    while (c->fd >= 0 && (nl = memchr(p, '\n', c->n - (size_t)(p - c->in)))) {
        *nl = 0;
        if (nl > p && nl[-1] == '\r') nl[-1] = 0;
        lib_line(c, p);
        p = nl + 1;
    }
    if (c->fd < 0) return;
    c->n -= (size_t)(p - c->in);
    memmove(c->in, p, c->n);
    if (c->n >= sizeof c->in - 1) close(c->fd), c->fd = -1; /* a line past 512 bytes */
}

struct fstamp { ino_t ino; long long size, sec, nsec; };
static struct fstamp stamp_of(const char *p) {
    struct stat st;
    if (stat(p, &st)) return (struct fstamp){0, -1, 0, 0};
    return (struct fstamp){st.st_ino, (long long)st.st_size, (long long)st.st_mtim.tv_sec, (long long)st.st_mtim.tv_nsec};
}

static void *lib_serve(void *arg) {
    int lfd = *(int *)arg;
    struct fstamp seen[2] = {stamp_of(lib_db), stamp_of(cat_db)};
    for (int i = 0; i < LIB_CLIENTS; ++i) lc[i].fd = -1;
    for (;;) {
        struct pollfd p[1 + LIB_CLIENTS];
        p[0] = (struct pollfd){.fd = lfd, .events = POLLIN};
        for (int i = 0; i < LIB_CLIENTS; ++i) p[1 + i] = (struct pollfd){.fd = lc[i].fd, .events = POLLIN};
        poll(p, 1 + LIB_CLIENTS, 1000);
        if (p[0].revents & POLLIN) {
            int fd = accept4(lfd, 0, 0, SOCK_CLOEXEC), i = 0;
            while (fd >= 0 && i < LIB_CLIENTS && lc[i].fd >= 0) ++i;
            if (fd >= 0 && i == LIB_CLIENTS) close(fd);
            else if (fd >= 0) {
                struct timeval tv = {2, 0};
                setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, &tv, sizeof tv);
                lc[i] = (struct lclient){.fd = fd};
            }
        }
        for (int i = 0; i < LIB_CLIENTS; ++i)
            if (lc[i].fd >= 0 && p[1 + i].fd == lc[i].fd && p[1 + i].revents) lib_read(&lc[i]);
        const char *files[2] = {lib_db, cat_db}, *names[2] = {"library", "catalogue"};
        for (int f = 0; f < 2; ++f) { /* written again: a new file renamed over it, or changed in place */
            struct fstamp now = stamp_of(files[f]);
            if (!memcmp(&now, &seen[f], sizeof now)) continue;
            seen[f] = now;
            for (int i = 0; i < LIB_CLIENTS; ++i)
                if (lc[i].fd >= 0 && lc[i].watch) lsay(&lc[i], "changed %s", names[f]);
        }
    }
    return 0;
}

/* the socket, 0660 and the library group's, and its thread; signals stay the main thread's */
static void lib_start(void) {
    static int lfd;
    snprintf(lib_db, sizeof lib_db, "%s", path("/mnt/data/vamp/library.db"));
    snprintf(cat_db, sizeof cat_db, "%s", path("/mnt/mmc/.vamp/catalogue.db"));
    snprintf(lib_passwd, sizeof lib_passwd, "%s", path("/etc/passwd"));
    snprintf(lib_group, sizeof lib_group, "%s", path("/etc/group"));
    snprintf(lib_sock, sizeof lib_sock, "%s", path("/run/vamp/lib.sock"));
    mkdir(path("/run"), 0755), mkdir(path("/run/vamp"), 0755);
    struct sockaddr_un a = {.sun_family = AF_UNIX};
    if (strlen(lib_sock) >= sizeof a.sun_path) return say("Q2LIB/1: %s too long", lib_sock);
    memcpy(a.sun_path, lib_sock, strlen(lib_sock) + 1);
    unlink(lib_sock);
    lfd = socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0);
    if (lfd < 0 || bind(lfd, (struct sockaddr *)&a, sizeof a) || listen(lfd, LIB_CLIENTS))
        return say("Q2LIB/1: %s: %s", lib_sock, strerror(errno));
    q2perm_socket(lib_sock, lib_group, "library");
    sigset_t all, old;
    sigfillset(&all);
    pthread_sigmask(SIG_BLOCK, &all, &old);
    pthread_t t;
    if (pthread_create(&t, 0, lib_serve, &lfd)) say("Q2LIB/1: no thread");
    else pthread_detach(t);
    pthread_sigmask(SIG_SETMASK, &old, 0);
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
    signal(SIGPIPE, SIG_IGN);
    if (!once) lib_start(); /* Q2LIB/1 */
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
