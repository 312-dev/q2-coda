/* q2libd: Vamp OS's library service (docs/vamp-os.md#who-owns-what, docs/libd.md), its first slice. It keeps two
 * databases in the shapes q2ui and q2playd already read, so they need no change:
 *
 *   - /mnt/data/vamp/library.db: the card's songs, a songtable with stock's columns (id, filename, fileurl, album,
 *     songer, style, name, composer, albumsonger, year, track, cd_num, time, ...), from a walk of /mnt/mmc and each
 *     file's tags through FFmpeg's demuxers. A song keeps its id across scans while its path stays; a file whose size
 *     and time are unchanged is not read again. Stock's own /mnt/data/database.db is never touched.
 *   - /mnt/mmc/.vamp/catalogue.db: the server library, a servertable of every signed-in card app's catalogue
 *     (/mnt/mmc/.<id>/catalogue.tsv, Q2CAT/1, docs/server-library.md), ids from 0x40000000 as the payload gives them.
 *     On the card, as today's Vamp keeps its own (/mnt/data is too small for a large server), but a file of Vamp OS's
 *     own, so today's Vamp's catalogue.db is never touched either.
 *
 * Each is written whole beside itself and renamed over it, so a reader never sees half a scan. It scans at its start
 * once the card is mounted, when the card's mount changes, when an app's catalogue.tsv or credentials change (looked
 * at every 10 s), and at SIGHUP; --once scans and ends. Before the card is there it writes an empty library, so a
 * reader always has one; after each write it runs --after's command (the image: q2ui's restart in place, so its lists
 * are the new ones). --root prefixes every path, for test/libd.py. */
#define _GNU_SOURCE
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
#define LOOK_S 10
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
    "create table vamp_files(fileurl text primary key, size integer, mtime integer, id integer);";

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
static sqlite3_stmt *ins, *find, *ins_file;
static int next_id, songs, read_n;

/* One file: its old row when unchanged, else its tags read. */
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
    if (walk_card) walk("/mnt/mmc", 0);
    sqlite3_finalize(ins), sqlite3_finalize(ins_file);
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

/* One app's catalogue.tsv into servertable (ins), ids from base. Its rows=, counted over s, p and i lines, must be
 * met, so a file cut short is not taken for a smaller library: -1 then, and nothing of it is kept. */
static int import_app(sqlite3 *d, sqlite3_stmt *st, const char *id, unsigned base) {
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

static int scan_catalogue(void) {
    char ids[CAT_APPS][64], stamp[1024], final[600], tmp[620];
    int n = apps(ids, stamp, sizeof stamp);
    snprintf(final, sizeof final, "%s", path("/mnt/mmc/.vamp/catalogue.db"));
    snprintf(tmp, sizeof tmp, "%s.new", final);
    mkdir(path("/mnt/mmc/.vamp"), 0755);
    unlink(tmp);
    sqlite3 *d;
    sqlite3_stmt *st = 0;
    if (sqlite3_open(tmp, &d) != SQLITE_OK ||
        exec_sql(d, "create table servertable(id int, filename, fileurl, album, songer, style, name, composer, albumsonger,"
                    " year int, track int, cd_num int, time int, time_end int, time_create int, Initial int, bitdepth int,"
                    " samprate int); begin") ||
        sqlite3_prepare_v2(d, "insert into servertable(id, filename, fileurl, album, songer, style, name, albumsonger, year, track,"
                              " cd_num, time, Initial) values (?,?,?,?,?,?,?,?,?,?,?,?,?)", -1, &st, 0) != SQLITE_OK)
        return sqlite3_close(d), -1;
    for (int i = 0; i < n; ++i) import_app(d, st, ids[i], CAT_ID_BASE + (unsigned)i * CAT_APP_SPAN);
    sqlite3_finalize(st);
    int ok = !exec_sql(d, "commit");
    sqlite3_close(d);
    if (!ok || rename(tmp, final)) return -1;
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
    char card[600] = "", seen_card[600] = "-", cat[1024], seen_cat[1024] = "-", ids[CAT_APPS][64];
    struct stat st;
    if (!once && stat(path("/mnt/data/vamp/library.db"), &st) && !scan_card(0)) run_after(); /* none yet: an empty one */
    while (!stopping) {
        card_stamp(card, sizeof card);
        apps(ids, cat, sizeof cat);
        int mounted = once || *card;
        int wrote = 0;
        if (mounted && (again || strcmp(card, seen_card))) {
            wrote |= !scan_card(1);
            snprintf(seen_card, sizeof seen_card, "%s", card);
        }
        if (mounted && (again || strcmp(cat, seen_cat))) {
            wrote |= !scan_catalogue();
            snprintf(seen_cat, sizeof seen_cat, "%s", cat);
        }
        if (wrote) run_after();
        again = 0;
        if (once) break;
        sleep(LOOK_S); /* a signal ends the sleep early */
    }
    return 0;
}
