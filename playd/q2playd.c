/* q2playd: Vamp OS's playback service (docs/vamp-os.md#who-owns-what, docs/playd.md). It is the server end of the
 * player's socket that demo is on today's Vamp (docs/player.md#the-socket): q2player connects to /tmp/sock_play and
 * q2playd says what to play. It owns the queue (the songs' ids from the library, an index, shuffle), what plays now,
 * and the volume, and takes commands from two places:
 *
 *   - the command queue q2ui and Car Remote write today (patch/coda_rc_cmdq.h): one "<seq> <verb>" line a command
 *     down the FIFO /tmp/coda-rc.cmdq, a play's ids in /tmp/coda-rc.list; what plays is written, as demo's shim
 *     writes it, to /tmp/coda-rc.now (key=value lines, on a change, twice a second at most), so q2ui runs on it
 *     unchanged;
 *   - its own socket, /run/vamp/play.sock: Q2PLAY/1, a line protocol with answers and a subscription to what plays
 *     (docs/platform/playback.md), the interface the UI is meant to move to.
 *
 * One thread, one poll loop. The player's status is asked for (player-status, media-info) when its push datagram
 * says it changed (/tmp/q2player.push, docs/internals.md#player-status-push) and once a second while a song is
 * loaded, never while nothing is. --root prefixes every path, for test/playd.py. */
#define _GNU_SOURCE
#include <ctype.h>
#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <poll.h>
#include <signal.h>
#include <sqlite3.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/un.h>
#include <time.h>
#include <unistd.h>

#include "q2perm.h" /* perm/: the permission check */
#include "peq_types.h" /* patch/: the EQ state */

/* patch/peq.c and patch/peq_level.c, linked as the player links peq.c (playd/Makefile): the EQ state demo's editor
 * keeps in /mnt/data/peq-active, read and written as it reads and writes it */
int eq_load_from(const char *path, eq_state *s), eq_save_to(const char *path, const eq_state *s);
int eq_auto_level(eq_state *s);

#define QUEUE_MAX 20000  /* ids one play may queue (q2ui's PLAY_MAX, coda_rc_shim.c CR_LIST_MAX) */
#define CLIENTS 8        /* Q2PLAY/1 connections at once */
#define LINE 512         /* a Q2PLAY/1 line */
#define RX 4096          /* the player's messages waiting to be parsed */
#define NOW_EVERY_MS 500 /* the now file's position, rewritten at most this often */
#define POLL_MS 1000     /* the player asked for its status this often while a song is loaded, besides its pushes */
#define ERRORS_MAX 20    /* songs in a row that do not open before the queue stops */

static char root[256], db_path[300], cat_path[300];
static int dac = -1, dac_missing;
static const char *now_path_rel = "/tmp/coda-rc.now", *list_rel = "/tmp/coda-rc.list", *cmdq_rel = "/tmp/coda-rc.cmdq",
                  *sock_rel = "/tmp/sock_play", *push_rel = "/tmp/q2player.push", *api_rel = "/run/vamp/play.sock";

static char *path(const char *rel) { /* rel under --root, in one of four buffers (no two calls in an expression need more) */
    static char b[4][640];
    static int i;
    i = (i + 1) & 3;
    snprintf(b[i], sizeof b[i], "%s%s", root, rel);
    return b[i];
}

static unsigned long long now_ms(void) {
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return (unsigned long long)t.tv_sec * 1000 + (unsigned long long)t.tv_nsec / 1000000;
}

static void logf_(const char *fmt, ...) {
    va_list ap;
    va_start(ap, fmt);
    fprintf(stderr, "q2playd: ");
    vfprintf(stderr, fmt, ap);
    fputc('\n', stderr);
    va_end(ap);
}

/* ---- what plays ---- */

enum { STOPPED = 0, PLAYING = 1, PAUSED = 2 }; /* the now file's state: AVRCP's order, as q2ui reads it */

static struct {
    int ids[QUEUE_MAX], order[QUEUE_MAX], n, at; /* order: the play order (shuffled or not); at indexes it */
    int shuffle;
    int state, loaded;           /* loaded: a song was sent and has not ended */
    int pos_ms, len_s, volume;
    int errors;                  /* songs in a row that did not open */
    int expect_cut;              /* a set-nexturl or mcl-stop was sent: its answer is no song's end */
    char title[256], artist[256], album[256], file[1024];
    char row_url[1024];          /* a played-as-paths song's library row's fileurl, "" with none */
    char **paths;                /* a server list's answer: the app's m3u, played as paths in place of ids (np of them) */
    int np;
    int offline;                 /* the list played by q2playd itself: its server rows by their offline copies */
    unsigned seq;                /* moves with every change of what is written */
    unsigned list;               /* moves with each new queue (a play, a source's start and end): the now file's qlist */
} q = {.volume = 24};
/* the queue file's (queue_write): its gen and content's hash, and its lines' first index into the play order and count */
static struct { unsigned gen, hash; int start, listed; } ql;
/* the play mode (docs/playd.md#the-play-mode, pm_shuffle): stock's MCL_MODE and Now Playing's two keys */
static struct { int mode, shuf_one, rep_off; } pm;

/* A server list's answer as it grows (docs/playd.md#the-answer-as-it-grows): the app's m3u, read again when it changes,
 * its entries taken in order up to the first whose file is not there yet (want), as the firmware's m3u_songs. next
 * counts the entries taken or passed over, so the app may rewrite the lines after them. waiting: the queue has played
 * up to want, which plays once it lands. */
static struct {
    int on, next, waiting;
    char file[300], want[360];
    long long stamp, stream; /* the m3u's, and the app's .stream's */
    char app[64];  /* whose answer it is, and the seq its .play carried: Download Ahead's .ahead is written for them */
    unsigned seq;
} mq;

static unsigned long long now_written_at;
static unsigned now_written_seq = ~0u;

/* ---- the library: stock's songtable (docs/internals.md#library) ---- */

#define CAT_ID_BASE 0x40000000 /* server rows' ids (q2libd, patch/catalogue.c) */

/* A server row's offline copy (servermeta's local, docs/libd.md#offline-copies) into out: 1 with one */
static int copy_of(int id, char *out, size_t room) {
    sqlite3 *db;
    sqlite3_stmt *st;
    int ok = 0;
    if (sqlite3_open_v2(cat_path, &db, SQLITE_OPEN_READONLY, 0) == SQLITE_OK &&
        sqlite3_prepare_v2(db, "select local from servermeta where id = ? and local is not null", -1, &st, 0) == SQLITE_OK) {
        sqlite3_bind_int(st, 1, id);
        if (sqlite3_step(st) == SQLITE_ROW && sqlite3_column_text(st, 0))
            snprintf(out, room, "%s", (const char *)sqlite3_column_text(st, 0)), ok = 1;
        sqlite3_finalize(st);
    }
    sqlite3_close(db);
    return ok;
}

/* id's row into q: its path, title, artist and album. In a list played offline a server row's path is its copy's, and
 * row_url its own fileurl (its cover's). */
static int song_lookup(int id) {
    sqlite3 *db;
    sqlite3_stmt *st;
    int ok = 0, server = (unsigned)id >= CAT_ID_BASE;
    q.title[0] = q.artist[0] = q.album[0] = q.file[0] = q.row_url[0] = 0;
    if (sqlite3_open_v2(server ? cat_path : db_path, &db, SQLITE_OPEN_READONLY, 0) != SQLITE_OK) {
        logf_("cannot read %s", server ? cat_path : db_path);
        sqlite3_close(db);
        return 0;
    }
    if (sqlite3_prepare_v2(db, server ? "select fileurl, name, songer, album from servertable where id = ?"
                                      : "select fileurl, name, songer, album from songtable where id = ?",
                           -1, &st, 0) == SQLITE_OK) {
        sqlite3_bind_int(st, 1, id);
        if (sqlite3_step(st) == SQLITE_ROW && sqlite3_column_text(st, 0)) {
            snprintf(q.file, sizeof q.file, "%s", (const char *)sqlite3_column_text(st, 0));
            snprintf(q.title, sizeof q.title, "%s", sqlite3_column_text(st, 1) ? (const char *)sqlite3_column_text(st, 1) : "");
            snprintf(q.artist, sizeof q.artist, "%s", sqlite3_column_text(st, 2) ? (const char *)sqlite3_column_text(st, 2) : "");
            snprintf(q.album, sizeof q.album, "%s", sqlite3_column_text(st, 3) ? (const char *)sqlite3_column_text(st, 3) : "");
            ok = 1;
        }
        sqlite3_finalize(st);
    }
    sqlite3_close(db);
    char copy[1024];
    if (ok && server && q.offline && copy_of(id, copy, sizeof copy))
        snprintf(q.row_url, sizeof q.row_url, "%s", q.file), snprintf(q.file, sizeof q.file, "%s", copy);
    return ok;
}

/* ---- the cover (docs/playd.md#the-cover) ----
 * The song's cover is copied to /tmp/externpic.jpg, where stock's cover thread leaves it and q2ui reads it (written
 * beside and renamed); none, and the file is removed. A card song's is q2libd's (library.db's vamp_cover), a server
 * song's its app's covers/<album id>.jpg (docs/server-library.md#covers); a song of an app's m3u, its row's: the
 * card song of that path, or the server row of a Stream Cache copy's sid. */
static void cover_lookup(int id, const char *file, char *out, size_t room) {
    sqlite3 *db;
    sqlite3_stmt *st;
    const char *pre = "/mnt/mmc/.", *cat;
    out[0] = 0;
    if (!*file) return;
    if ((id >= 0 && (unsigned)id >= CAT_ID_BASE) || (id < 0 && strstr(file, "/cat/") && !strncmp(file, pre, strlen(pre)))) {
        /* a server row: /mnt/mmc/.<app>/cat/<album id>/<sid>.<suffix> */
        const char *alb, *end;
        if (!strncmp(file, pre, strlen(pre)) && (cat = strstr(file, "/cat/")) && (end = strchr(alb = cat + 5, '/')))
            snprintf(out, room, "%.*s/covers/%.*s.jpg", (int)(cat - file), file, (int)(end - alb), alb);
        return;
    }
    if (sqlite3_open_v2(db_path, &db, SQLITE_OPEN_READONLY, 0) == SQLITE_OK &&
        sqlite3_prepare_v2(db, id >= 0 ? "select file from vamp_cover where id = ?1"
                                       : "select c.file from songtable s join vamp_cover c on c.id = s.id where s.fileurl = ?2",
                           -1, &st, 0) == SQLITE_OK) {
        sqlite3_bind_int(st, 1, id), sqlite3_bind_text(st, 2, file, -1, SQLITE_STATIC);
        if (sqlite3_step(st) == SQLITE_ROW && sqlite3_column_text(st, 0)) snprintf(out, room, "%s", (const char *)sqlite3_column_text(st, 0));
        sqlite3_finalize(st);
    }
    sqlite3_close(db);
}

static void set_cover(int id, const char *file) {
    char from[700], tmp[400];
    cover_lookup(id, file, from, sizeof from);
    FILE *in = *from ? fopen(path(from), "rb") : 0;
    snprintf(tmp, sizeof tmp, "%s.q2playd", path("/tmp/externpic.jpg"));
    FILE *out = in ? fopen(tmp, "wb") : 0;
    char b[16384];
    size_t n;
    int ok = out != 0;
    while (ok && (n = fread(b, 1, sizeof b, in)) > 0) ok = fwrite(b, 1, n, out) == n;
    if (in) fclose(in);
    if (out) {
        int bad = fclose(out) || !ok || rename(tmp, path("/tmp/externpic.jpg"));
        if (bad) unlink(tmp), ok = 0;
    }
    if (!ok) unlink(path("/tmp/externpic.jpg"));
}

/* ---- the player's socket ---- */

static int listen_fd = -1, player = -1, push_fd = -1, cmdq_fd = -1, api_fd = -1;
static char rx[RX];
static size_t rx_n;

static void player_send(const char *name, const char *arg) {
    char m[1200];
    int n = snprintf(m, sizeof m, "{%s\\%s}", name, arg);
    if (player < 0 || n <= 0 || n >= (int)sizeof m) return;
    if (send(player, m, (size_t)n, MSG_NOSIGNAL) != n) logf_("send to the player failed: %s", strerror(errno));
}

/* set-songurl or set-nexturl as demo's mclStartPlayer builds it (docs/player.md#commands): the start (ss, seconds), the
 * cache in KB by the file's kind and its fill percent, the Gapless setting, the Q2's hardware type 17; set-gapless as
 * mclAutoChange sends it, its cache -1 and no hardware type. */
static int gapless_on(void);
static void send_song(const char *cmd, const char *file, int ss) {
    char url[1100], arg[1200];
    size_t j = 0;
    for (const char *p = file; *p && j + 6 < sizeof url; ++p)
        if (*p == '}') j += (size_t)snprintf(url + j, sizeof url - j, "%%0x7d");
        else url[j++] = *p;
    url[j] = 0;
    const char *dot = strrchr(file, '.');
    int cache = 512, fill = 20;
    if (dot && (!strcasecmp(dot, ".ape") || !strcasecmp(dot, ".flac"))) cache = 1024;
    if (dot && (!strcasecmp(dot, ".dff") || !strcasecmp(dot, ".dsf") || !strcasecmp(dot, ".iso"))) cache = 4096, fill = 50;
    if (!strcmp(cmd, "set-gapless")) snprintf(arg, sizeof arg, "%s|ss=%d|-1|%d|%d|90", url, ss, fill, gapless_on());
    else snprintf(arg, sizeof arg, "%s|ss=%d|%d|%d|%d|17|90", url, ss, cache, fill, gapless_on());
    player_send(cmd, arg);
}

/* ---- Gapless (docs/playd.md#gapless) ----
 * demo's mclAutoChange: with [PLAYSET] GAPLESS on, a song of 3 s or more less than 6 s from its end has the next song
 * sent ahead as set-gapless, never a DSD file (.dsf, .dff, .iso) either side; the player opens it on its own thread and
 * plays it at the end with nothing between, so the end's exit sends nothing (mclNextSong(1) only moves its index). Any
 * other change of what plays next first sends mcl-closegapless, as stock's queue edits do. */
#define GAPLESS_MIN_S 3
#define GAPLESS_AHEAD_S 6
static struct { int sent, at; } gl;
static void gapless_forget(void) {
    if (gl.sent) player_send("mcl-closegapless", "null"), gl.sent = 0;
}

/* The song at q.order[q.at] to the player: set-songurl when nothing plays, set-nexturl to cut the one that does. */
/* A Stream Cache copy's app and sid: /mnt/mmc/.<app>/stream/.../<title> [<sid>].<suffix> (patch/stream_path.h); 0 for
 * any other path. */
static int stream_sid(const char *p, char *app, size_t an, char *sid, size_t sn) {
    const char *pre = "/mnt/mmc/.", *a = p + strlen(pre), *s, *o, *c;
    if (strncmp(p, pre, strlen(pre)) || !(s = strchr(a, '/')) || strncmp(s, "/stream/", 8)) return 0;
    if (!(o = strrchr(p, '[')) || !(c = strchr(o, ']')) || c == o + 1 || strchr(o, '/')) return 0;
    for (const char *k = o + 1; k < c; ++k)
        if (!isalnum((unsigned char)*k) && *k != '-' && *k != '_') return 0;
    snprintf(app, an, "%.*s", (int)(s - a), a), snprintf(sid, sn, "%.*s", (int)(c - o - 1), o + 1);
    return 1;
}

/* the row of a song played as a path, by its fileurl (q.paths' songs: the app's m3u): 1 with it in q */
static int row_of(const char *db_file, const char *sql, const char *arg) {
    sqlite3 *db;
    sqlite3_stmt *st;
    int ok = 0;
    if (sqlite3_open_v2(db_file, &db, SQLITE_OPEN_READONLY, 0) == SQLITE_OK && sqlite3_prepare_v2(db, sql, -1, &st, 0) == SQLITE_OK) {
        sqlite3_bind_text(st, 1, arg, -1, SQLITE_STATIC);
        if (sqlite3_step(st) == SQLITE_ROW) {
            for (int i = 0; i < 3; ++i) {
                const char *t = (const char *)sqlite3_column_text(st, i);
                snprintf(i == 0 ? q.title : i == 1 ? q.artist : q.album, sizeof q.title, "%s", t ? t : "");
            }
            const char *u = (const char *)sqlite3_column_text(st, 3);
            snprintf(q.row_url, sizeof q.row_url, "%s", u ? u : "");
            ok = 1;
        }
        sqlite3_finalize(st);
    }
    sqlite3_close(db);
    return ok;
}

/* A played-as-paths song: its card row's title, artist and album; an offline copy's (servermeta's local) or a Stream
 * Cache copy's (by the sid in its name) its server row's; else the file's name as the title. */
static int path_lookup(int at) {
    const char *p = q.paths[at], *base = strrchr(p, '/');
    char app[64], sid[128], like[300];
    snprintf(q.file, sizeof q.file, "%s", p);
    q.row_url[0] = 0;
    if (row_of(db_path, "select name, songer, album, fileurl from songtable where fileurl = ?1", p)) return 1;
    if (row_of(cat_path, "select s.name, s.songer, s.album, s.fileurl from servertable s join servermeta m on m.id = s.id"
                         " where m.local = ?1", p))
        return 1;
    if (stream_sid(p, app, sizeof app, sid, sizeof sid)) {
        snprintf(like, sizeof like, "/mnt/mmc/.%s/cat/%%/%s.%%", app, sid);
        if (row_of(cat_path, "select name, songer, album, fileurl from servertable where fileurl like ?1", like)) return 1;
    }
    snprintf(q.title, sizeof q.title, "%s", base ? base + 1 : p);
    q.artist[0] = q.album[0] = 0;
    return 1;
}

/* Now Playing while a grown answer waits for mq.want: that song, by its row as path_lookup finds it, nothing sent */
static void path_lookup_want(void) {
    char *p[1] = {mq.want}, **was = q.paths;
    q.paths = p;
    path_lookup(0);
    q.paths = was, q.pos_ms = 0, q.len_s = 0, ++q.seq;
}

/* The queue's song at `at` from ss seconds (a queue given back after a source starts where it was put aside). */
static void resume_leave(void), dac_ready(void);
static int start_from(int at, int ss) {
    resume_leave(); /* the song read before keeps its place; the one started is read anew */
    gapless_forget();
    while (at >= 0 && at < q.n) {
        q.at = at;
        if (q.paths ? path_lookup(at) : song_lookup(q.ids[q.order[at]])) {
            int cut = q.loaded;
            dac_ready();
            send_song(cut ? "set-nexturl" : "set-songurl", q.file, ss);
            set_cover(q.paths ? -1 : q.ids[q.order[at]], q.paths || *q.row_url ? q.row_url : q.file);
            q.expect_cut = cut;
            q.loaded = 1, q.state = PLAYING, q.pos_ms = ss * 1000, q.len_s = 0;
            ++q.seq;
            return 0;
        }
        logf_("no song %d in the library", q.ids[q.order[at]]);
        if (++q.errors >= ERRORS_MAX) break;
        ++at, ss = 0;
    }
    return -1;
}

static int start_at(int at) { return start_from(at, 0); }

/* A source (docs/playd.md#sources-and-audio-focus): one URL a Q2PLAY/1 client plays with audio focus, the queue put
 * aside meanwhile, as it was, and given back when the source ends. holder is the client's slot in cl[]. */
static struct {
    int on, holder;
    int ids[QUEUE_MAX], order[QUEUE_MAX], n, at, shuffle, offline, state, pos_ms, len_s;
    char **paths;
    int np;
} src;
static void source_end(const char *why);
static void source_drop(void);

static void stop(void) {
    gapless_forget();
    if (q.loaded) {
        player_send("mcl-stop", "null");
        q.expect_cut = 1;
    }
    q.loaded = 0, q.state = STOPPED, q.pos_ms = 0;
    ++q.seq;
}

/* A song ended on its own (played out, or did not open): the next, or the queue's end. */
static void reshuffle(int first);
static int pm_shuffle(void);

static int gapless_took(void);
static void ended(int error) {
    logf_("song %d %s", q.n && !q.paths ? q.ids[q.order[q.at]] : 0, error ? "did not play" : "ended");
    if (!error && !src.on && gapless_took()) return; /* the player plays the preloaded song on alone */
    gl.sent = 0;
    q.loaded = 0;
    if (src.on) { /* a source's URL: its holder is told and the queue given back */
        source_end(error ? "error" : "end");
        return;
    }
    q.errors = error ? q.errors + 1 : 0;
    if (!error && pm.mode == 1 && q.n && !start_at(q.at)) return; /* Repeat One: the song again */
    if (q.errors < ERRORS_MAX && q.at + 1 < q.n && !start_at(q.at + 1)) return;
    /* Repeat All, and stock's Shuffle, which never ends: the queue again from its first song (a shuffle's drawn again) */
    if (q.errors < ERRORS_MAX && (pm.mode == 3 || pm.mode == 2) && q.n && !q.paths) {
        if (q.shuffle) reshuffle(0);
        if (!start_at(0)) return;
    }
    if (q.errors < ERRORS_MAX && q.paths && mq.on && *mq.want) { /* the next not there yet: it plays once it lands */
        mq.waiting = 1, q.at = q.n - 1;
        logf_("waiting for %s", mq.want);
        path_lookup_want();
        return;
    }
    player_send("mcl-orderstop", "null");
    q.state = STOPPED, q.pos_ms = 0;
    ++q.seq;
}

static void listen_tick(int sec, int len), resume_tick(int sec, int len), gapless_tick(int sec, int len);

static void player_message(const char *name, const char *arg) {
    if (!strcmp(name, "player-status")) {
        if (!strcmp(arg, "exit") || !strcmp(arg, "error")) {
            if (q.expect_cut) q.expect_cut = 0; /* the cut song's close came as an end: the next song is already sent */
            else ended(!strcmp(arg, "error"));
        }
    } else if (!strcmp(name, "player-nextstatus") || !strcmp(name, "player-stopstatus")) {
        q.expect_cut = 0;
    } else if (!strcmp(name, "media-info") && strcmp(arg, "no-playing-status")) {
        int rate, len, pos;
        if (sscanf(arg, "%*d|%d|%d|%d", &rate, &len, &pos) == 3 && q.loaded) {
            if (len != q.len_s) q.len_s = len, ++q.seq;
            if (pos * 1000 != q.pos_ms) q.pos_ms = pos * 1000, ++q.seq;
            listen_tick(pos, len), resume_tick(pos, len);
            gapless_tick(pos, len);
        }
    }
}

static void player_read(void) {
    ssize_t got = recv(player, rx + rx_n, sizeof rx - 1 - rx_n, 0);
    if (got <= 0) {
        logf_("the player went away");
        close(player), player = -1, rx_n = 0;
        if (q.loaded) q.loaded = 0, q.state = STOPPED, ++q.seq; /* runsv starts it again; nothing plays */
        if (src.on) source_end("error");
        return;
    }
    rx_n += (size_t)got;
    rx[rx_n] = 0;
    char *p = rx;
    for (char *open; (open = strchr(p, '{'));) {
        char *close_ = strchr(open, '}');
        if (!close_) { p = open; break; }
        *close_ = 0;
        char *bs = strchr(open + 1, '\\');
        if (bs) *bs = 0, player_message(open + 1, bs + 1);
        p = close_ + 1;
    }
    if (!strchr(p, '{')) p = rx + rx_n; /* nothing but noise left */
    rx_n -= (size_t)(p - rx);
    memmove(rx, p, rx_n);
    if (rx_n >= sizeof rx - 1) rx_n = 0; /* a message no longer than the buffer: dropped, as both stock sides do */
}

static void ask_status(void) {
    if (player >= 0 && q.loaded) {
        player_send("player-status", "request");
        player_send("media-info", "get-music-info");
    }
}

/* ---- the DAC's volume (docs/playd.md#the-volume) ----
 * The CS43131s' volume, as stock's mclSetLocalVol sets it: one ioctl on /dev/shanling_dac (codec_cs43131.ko), its
 * argument the left channel's level in bits 8-15 and the right's in bits 0-7, each on the same 0 to 100 scale as the
 * volume itself; the driver maps that to the chips' attenuation. Sent at the start and at each change while the DAC is
 * the output. */
#define DAC_SET_VOLUME 0xc0044d00u

static void dac_volume_open(void) {
    if (dac < 0 && !dac_missing) {
        dac = open(path("/dev/shanling_dac"), O_RDWR | O_CLOEXEC);
        if (dac < 0) dac_missing = 1, logf_("no /dev/shanling_dac: the volume is kept and shown, not set");
    }
}
static void dac_volume(void) {
    dac_volume_open();
    if (dac < 0) return;
    int both = (q.volume & 0xff) << 8 | (q.volume & 0xff);
    if (ioctl(dac, DAC_SET_VOLUME, &both) < 0) logf_("the DAC refused volume %d: %s", q.volume, strerror(errno));
}

/* ---- the volume's settings, in stock's config.ini (docs/playd.md#the-volume) ----
 * [PLAYSET] VOLUME (the level), MAXVOLUME (a cap, 100 when absent), BOOTVOLFLAG and BOOTVOLUME (Default Volume: the
 * level every boot starts at). Read at the start; VOLUME written back VOLUME_SAVE_MS after the last change, the file
 * rewritten whole with that one line changed, so stock and today's Vamp read it as before. */
#define VOLUME_SAVE_MS 2000
static int max_volume = 100;
static unsigned long long volume_dirty_at;
/* The output (docs/playd.md#the-output): 0 the DAC, 1 Bluetooth, as q2player's mcl-setoutputway; on Bluetooth soft is
 * the player's own gain, else unity in the player and the level the headset's (absolute volume, q2btd's). Each output
 * keeps its own level, as today's Vamp does: [PLAYSET] VOLUME the DAC's, BT_VOLUME Bluetooth's (-1 until saved: the
 * DAC's then). */
static int way, soft, other_volume = -1;

/* ---- the DAC's output (docs/playd.md#the-dacs-output) ----
 * As stock's config_outputchannel(g_headset_output, 2), which player_start, player_play_pause, player_change_music and
 * a paused seek call before the player plays: on the DAC, the headset mode for the jack in use (mclSetHeadsetMode,
 * ioctl DAC_HEADSET: 1 the 4.4 mm jack when it is plugged, else 0 the 3.5 mm, check_outputdevice's choice) and the DAC
 * powered (mclSetDacPwr, DAC_POWER 1, the Q2's case of it). A plug while playing does the same (config_po_output,
 * config_bal_output); 30 s with nothing playing powers it off (check_dacoff_state, a count each second to 31), and so
 * does an output other than the DAC. The jacks are q2sysd's, in /run/vamp/jack ("po=N bal=N"). */
#define DAC_HEADSET 0xc0044d1cu
#define DAC_POWER 0xc0044d1au
#define DAC_OFF_S 31
static void dac_volume(void), dac_volume_open(void), read_small(const char *p, char *out, size_t room);
static struct { int on, bal; char seen[64]; unsigned long long idle_at; } dp;
static int dac_io(unsigned long req, int v) {
    dac_volume_open();
    if (dac < 0) return -1;
    if (ioctl(dac, req, &v) < 0) return logf_("the DAC refused 0x%lx %d: %s", req, v, strerror(errno)), -1;
    return 0;
}
static int jack_read(void) { /* 1 when the file says something new (read whole: a plug can keep its size and second) */
    char b[64] = "";
    read_small(path("/run/vamp/jack"), b, sizeof b);
    if (!strcmp(b, dp.seen)) return 0;
    memcpy(dp.seen, b, sizeof b);
    const char *p = strstr(b, "bal=");
    dp.bal = p && p[4] == '1';
    return 1;
}
/* before the player plays on the DAC: the jack's mode and the power */
static void dac_ready(void) {
    if (way) return;
    jack_read();
    dac_io(DAC_HEADSET, dp.bal ? 1 : 0); /* every time, as stock's are: the driver's to keep */
    if (!dac_io(DAC_POWER, 1)) dp.on = 1;
    dp.idle_at = 0;
}
static void dac_off(void) {
    if (dp.on && !dac_io(DAC_POWER, 0)) dp.on = 0;
}
/* each time round the loop: a plug while playing readies the jack; 30 s not playing powers the DAC off */
static void dac_poll(unsigned long long t) {
    if (way) return;
    int bal = dp.bal, moved = jack_read();
    if (q.state == PLAYING) {
        if (moved && dp.bal != bal) dac_ready();
        dp.idle_at = 0;
        return;
    }
    if (!dp.on) return;
    if (!dp.idle_at) dp.idle_at = t | 1;
    else if (t >= dp.idle_at + DAC_OFF_S * 1000ull) dac_off(), logf_("the DAC powered off: %d s with nothing playing", DAC_OFF_S);
}

static int ini_get(const char *text, const char *section, const char *key, int fallback) {
    char head[64];
    snprintf(head, sizeof head, "[%s]", section);
    const char *sec = strstr(text, head);
    if (!sec) return fallback;
    size_t k = strlen(key);
    for (const char *l = strchr(sec, '\n'); l && *++l && *l != '['; l = strchr(l, '\n'))
        if (!strncmp(l, key, k) && l[k] == '=') return atoi(l + k + 1);
    return fallback;
}

static char *read_all(const char *p) {
    FILE *f = fopen(p, "r");
    if (!f) return 0;
    char *b = calloc(1, 65537);
    size_t n = b ? fread(b, 1, 65536, f) : 0;
    fclose(f);
    if (b) b[n] = 0;
    return b;
}

/* The play mode (docs/playd.md#the-play-mode): stock's MCL_MODE, saved as [PLAYSET] PLAYMODE (0 in order, 1 Repeat
 * One, 2 Shuffle, 3 Repeat All), and Now Playing's two controls over it as today's Vamp keeps them (np_bar.c): shuffle
 * under Repeat One ([IPOD] NPSHUFONE) and Off chosen under shuffle ([IPOD] NPREPOFF), since stock's Shuffle never
 * ends. Read at the start (pm, above). */
static int pm_shuffle(void) { return pm.mode == 2 || (pm.mode == 1 && pm.shuf_one); }
static int pm_repeat(void) { /* Car Remote's numbers: 0 Off, 1 One, 2 All */
    return pm.mode == 1 ? 1 : pm.mode == 3 || (pm.mode == 2 && !pm.rep_off) ? 2 : 0;
}

static void volume_load(void) {
    char *t = read_all(path("/mnt/data/config.ini"));
    if (!t) return;
    max_volume = ini_get(t, "PLAYSET", "MAXVOLUME", 100);
    if (max_volume < 0 || max_volume > 100) max_volume = 100;
    int v = ini_get(t, "PLAYSET", "BOOTVOLFLAG", 0) ? ini_get(t, "PLAYSET", "BOOTVOLUME", -1) : ini_get(t, "PLAYSET", "VOLUME", -1);
    if (v >= 0 && v <= 100) q.volume = v > max_volume ? max_volume : v;
    other_volume = ini_get(t, "PLAYSET", "BT_VOLUME", -1); /* Bluetooth's, while the DAC is the output */
    if (other_volume > 100) other_volume = -1;
    pm.mode = ini_get(t, "PLAYSET", "PLAYMODE", 0);
    if (pm.mode < 0 || pm.mode > 3) pm.mode = 0;
    pm.shuf_one = ini_get(t, "IPOD", "NPSHUFONE", 0) != 0, pm.rep_off = ini_get(t, "IPOD", "NPREPOFF", 0) != 0;
    free(t);
}

/* KEY=v in [SECTION], the rest of the file as it was */
static void ini_set(const char *section, const char *key, int v);

/* ---- the EQ switch (docs/playd.md#the-eq-switch): Control Center's EQ circle, as patch/peq_ui.c has it ---- */
static const char *eq_rel = "/mnt/data/peq-active";

/* the mode playing: EQ_OFF with no state saved, as demo reads it (eq_load's defaults) */
static int eq_mode(void) {
    eq_state s;
    eq_load_from(path(eq_rel), &s);
    return s.mode;
}

/* peq_ui.c eq_toggle and eq_commit: on, PEQ as the EQ page's Enabled switch turns it on (a mode on already stays);
 * off, Off; the hold the open page's. Saved with PEQ's Auto as the cut for every rate, the stock switch's [PLAYSET]
 * EQFLAG written (today's Vamp's boot puts the filter in only when it is set) and the player told with stock's call.
 * -1 when the state is not saved: nothing changes. */
static int eq_switch(int on) {
    eq_state s;
    eq_load_from(path(eq_rel), &s);
    if ((s.mode != EQ_OFF) == on) return 0;
    s.mode = on ? EQ_PEQ : EQ_OFF, s.hold = 0;
    eq_auto_level(&s);
    if (!eq_save_to(path(eq_rel), &s)) return -1;
    ini_set("PLAYSET", "EQFLAG", on);
    player_send("mcl-seteq", "0.000000|0.000000|0.000000|0.000000|0.000000|0.000000|0.000000|0.000000|0.000000|0.000000|1");
    return 0;
}

/* peq_ui.c peq_boot, at the start: Off's comparison preamp (hold) belongs to a page, which cannot survive a restart,
 * and an Auto saved by another version becomes the cut for every rate before anything plays */
static void eq_boot(void) {
    eq_state s;
    if (!eq_load_from(path(eq_rel), &s)) return;
    int changed = eq_auto_level(&s);
    if (s.hold) s.hold = 0, changed = 1;
    if (changed && !eq_save_to(path(eq_rel), &s)) logf_("%s: not saved", eq_rel);
}

static void ini_set(const char *section, const char *key, int v) {
    char *t = read_all(path("/mnt/data/config.ini")), tmp[340], line[64], head[64], k[40];
    if (!t) return;
    snprintf(line, sizeof line, "%s=%d", key, v), snprintf(head, sizeof head, "[%s]", section), snprintf(k, sizeof k, "%s=", key);
    char *sec = strstr(t, head), *at = 0, *end = 0;
    for (char *l = sec ? strchr(sec, '\n') : 0; l && *++l && *l != '['; l = strchr(l, '\n'))
        if (!strncmp(l, k, strlen(k))) { at = l, end = l + strcspn(l, "\r\n"); break; }
    snprintf(tmp, sizeof tmp, "%s.q2playd", path("/mnt/data/config.ini"));
    FILE *f = fopen(tmp, "w");
    if (f) {
        if (at) fprintf(f, "%.*s%s%s", (int)(at - t), t, line, end);
        else if (sec) { /* no KEY line: one after the section's header */
            char *nl = strchr(sec, '\n');
            if (nl) fprintf(f, "%.*s%s\n%s", (int)(nl + 1 - t), t, line, nl + 1);
            else fprintf(f, "%s\n%s\n", t, line);
        } else fprintf(f, "%s%s%s\n%s\n", t, *t && t[strlen(t) - 1] != '\n' ? "\n" : "", head, line);
        if (!fclose(f)) rename(tmp, path("/mnt/data/config.ini"));
    }
    free(t);
}

static struct { int on, local, limit; } air_vol; /* a speaker playing: the earphones' level and Max Volume kept */

/* the level saved: the earphones' as PLAYSET VOLUME (BT_VOLUME on Bluetooth), an AirPlay speaker's as Q2POD AIRVOL
 * while one plays */
static void volume_save(void) {
    if (air_vol.on) ini_set("Q2POD", "AIRVOL", q.volume);
    else ini_set("PLAYSET", way ? "BT_VOLUME" : "VOLUME", q.volume);
}

/* The level where the output takes it: the DAC's ioctl, or on Bluetooth the player's mcl-setvol (L << 8 | R): the
 * level itself with soft, else 100, which takes the player's volume filter out (docs/player.md#output). */
static void volume_apply(void) {
    char a[16];
    if (!way) {
        dac_volume();
        return;
    }
    snprintf(a, sizeof a, "%d", soft ? q.volume << 8 | q.volume : 100 << 8 | 100);
    player_send("mcl-setvol", a);
}

/* The output moved: the departing level saved, the incoming one's restored, the player told, and a song loaded
 * started again where it is heard (q2player takes a new way at its next song: docs/player.md#commands), paused if
 * it was. */
static void output_set(int w, int s) {
    if (w == way && s == soft) return;
    if (w != way) {
        if (volume_dirty_at) volume_save(), volume_dirty_at = 0;
        int incoming = other_volume >= 0 ? other_volume : q.volume;
        other_volume = q.volume;
        way = w;
        q.volume = incoming > max_volume ? max_volume : incoming;
        ++q.seq;
    }
    soft = s;
    logf_("output %s%s, volume %d", way ? "bluetooth" : "dac", way && soft ? " soft" : "", q.volume);
    if (way) dac_off(); /* config_outputchannel's Bluetooth: mclSetDacPwr(0) */
    player_send("mcl-setoutputway", way ? "1" : "0");
    volume_apply();
    if (q.loaded) {
        int state = q.state, pos = q.pos_ms / 1000;
        player_send("mcl-stop", "null");
        q.expect_cut = 1;
        send_song("set-songurl", q.file, pos);
        if (state == PAUSED) player_send("mcl-pause", "null");
    }
}

/* ---- commands: the same verbs from the FIFO and from Q2PLAY/1 ---- */

/* ---- server songs: the catplay hand-off to their app (docs/server-library.md, docs/playd.md#server-songs) ----
 * A list with server rows is handed to the app whose catalogue they are: /tmp/q2-<id>.catplay, "<seq> <start>" (the
 * chosen song's place in the list) and a path a line in play order (a server row's fileurl, a card file's path), written whole, while the app listens
 * (/tmp/q2-<id>.browse.sock). Its answer is /tmp/q2-<id>.play, "<seq> <m3u>": the m3u's card paths (Stream Cache copies
 * for server songs) from the row at <start> on, played in place of the list. No answer within CATPLAY_WAIT_MS: the list
 * is played offline, its card rows and its server rows by their offline copies (servermeta, docs/playd.md#offline-copies).
 * An app that is not listening is started first by q2pkgd (Q2PKG/1 start, the launcher:
 * docs/platform/packages.md#starting-and-stopping-q2pkg1), and the catplay written once its socket is there (an app
 * takes a .catplay there at its start as one answered); q2pkgd refusing, or no socket within CATPLAY_START_MS, and the
 * list is played offline. An app whose state says server=down is handed nothing: its list is played offline at once, as
 * it is with no Wi-Fi link; Smart Wi-Fi's radio off is woken first (docs/playd.md#wi-fis-state). */
#define CATPLAY_WAIT_MS 4000
#define CATPLAY_START_MS 15000 /* an app started for a catplay has this long to listen (today's Vamp's catalogue.c) */
#define PKG_ASK_MS 3000        /* q2pkgd's answer to a start, waited for in the loop */
#define LINK_WAIT_MS 15000     /* Smart Wi-Fi's radio woken for a press has this long to join (the firmware's SMART_CONNECT_MS) */
#define LINK_LOOK_MS 500       /* Q2NET/1's status asked this often while it joins */
static struct {
    int on, at, starting, linking;
    char app[64], before[600];
    unsigned seq;
    unsigned long long until, looked;
} cp;

static void free_paths(void) {
    for (int i = 0; i < q.np; ++i) free(q.paths[i]);
    free(q.paths), q.paths = 0, q.np = 0;
    mq.on = mq.waiting = 0;
}

/* a path an app names: on the card, with no ".." segment (patch/addon.c card_path) */
static int card_path(const char *p) {
    if (strncmp(p, "/mnt/mmc/", 9)) return 0;
    for (const char *s = p;;) {
        if (s[0] == '.' && s[1] == '.' && (!s[2] || s[2] == '/')) return 0;
        if (!(s = strchr(s, '/'))) return 1;
        ++s;
    }
}

static long long file_stamp(const char *p) {
    struct stat st;
    return stat(p, &st) ? -1 : (long long)st.st_mtime * 1000000007LL + (long long)st.st_size;
}

/* the m3u's entries from mq.next on into q.paths, up to the first not there yet (mq.want); an entry off the card is
 * passed over. The file read whole each time (it is the app's few lines). */
/* ---- a song played while it arrives (grow: docs/server-library.md, "Starting before the end") ----
 * With the caps line's grow the app writes a song in place as it arrives and tells how far it has come in
 * /tmp/q2-<id>.stream: "#Q2STREAM/1\tseq=N", then "<state>\t<bytes>\t<total>\t<length ms>\t<path>" a song. A line of
 * the answer whose file is there is taken once its line is done or fail, or get or stall with START bytes: 3 s of
 * the song (total x 3000 / length), at least 256 KB, or the whole file when total or length is unknown; a get line in
 * a file not written for 3 s is stale and reads as fail. A song with no line is whole. q2player waits at the bytes
 * there are (player/grow.c). */
#define GROW_MIN_BYTES (256 * 1024)
#define STREAM_STALE_S 3
static long long stream_stamp(void) {
    char p[300];
    snprintf(p, sizeof p, "/tmp/q2-%s.stream", mq.app);
    return file_stamp(path(p));
}
/* whether e, there on the card, may be queued */
static int grow_ready(const char *e) {
    char p[300], line[1300];
    snprintf(p, sizeof p, "/tmp/q2-%s.stream", mq.app);
    struct stat st;
    FILE *f = stat(path(p), &st) ? 0 : fopen(path(p), "r");
    int ready = 1, stale = f && time(0) - st.st_mtime >= STREAM_STALE_S;
    while (f && fgets(line, sizeof line, f)) {
        char state[8];
        long long bytes, total, ms;
        int at = 0;
        line[strcspn(line, "\r\n")] = 0;
        if (sscanf(line, "%7[a-z]\t%lld\t%lld\t%lld\t%n", state, &bytes, &total, &ms, &at) != 4 || !at) continue;
        if (strcmp(line + at, e)) continue;
        if (!strcmp(state, "done") || !strcmp(state, "fail") || (stale && !strcmp(state, "get"))) ready = 1;
        else if (!strcmp(state, "wait")) ready = 0;
        else { /* get, stall: its start there */
            long long start = total > 0 && ms > 0 ? total * 3000 / ms : -1;
            if (start >= 0 && start < GROW_MIN_BYTES) start = GROW_MIN_BYTES;
            ready = start >= 0 ? bytes >= start : 0;
        }
    }
    if (f) fclose(f);
    return ready;
}

static void m3u_read(void) {
    FILE *f = fopen(path(mq.file), "r");
    char line[1100], e[sizeof mq.want];
    int entry = 0;
    mq.want[0] = 0, mq.stamp = file_stamp(path(mq.file)), mq.stream = stream_stamp();
    while (f && fgets(line, sizeof line, f)) {
        char *l = line;
        if (!strncmp(l, "\xEF\xBB\xBF", 3)) l += 3; /* a BOM */
        l[strcspn(l, "\r\n")] = 0;
        if (!*l || *l == '#' || entry++ < mq.next) continue;
        if (!card_path(l) || strlen(l) >= sizeof e) { ++mq.next; continue; } /* off the card, or too long to be a path here */
        memcpy(e, l, strlen(l) + 1);
        if (access(path(e), F_OK) || !grow_ready(e)) { memcpy(mq.want, e, strlen(e) + 1); break; }
        char **g = realloc(q.paths, (size_t)(q.np + 1) * sizeof *g);
        if (!g) break;
        q.paths = g, q.paths[q.np++] = strdup(e), ++mq.next;
    }
    if (f) fclose(f);
    q.n = q.np;
}

/* while a grown answer plays: its file changed or the song waited for landed, read again; a queue waiting at its end
 * goes on with what came */
static void m3u_poll(void) {
    if (!mq.on || src.on) return;
    if (mq.stamp == file_stamp(path(mq.file)) && mq.stream == stream_stamp() &&
        !(*mq.want && !access(path(mq.want), F_OK) && grow_ready(mq.want)))
        return;
    int had = q.n;
    m3u_read();
    if (q.n > had) ++q.seq; /* the now file's qlen and the queue file */
    if (mq.waiting && q.n > had) {
        mq.waiting = 0;
        logf_("%s landed", q.paths[had]);
        if (q.state == PAUSED) q.at = had, q.pos_ms = 0, ++q.seq; /* paused while it waited: resume plays it */
        else start_at(had);
    }
}

/* ---- Download Ahead (docs/server-library.md#download-ahead-and-the-streaming-state, docs/playd.md#download-ahead) ----
 * Streaming > Configuration's Download Ahead, [Q2POD] AHEAD (0 Off, 1 to 3 songs; anything else 2, the firmware's
 * stream_state.c ahead_n), told to the app in /tmp/q2-<id>.ahead: "#Q2AHEAD/1\tseq=<the .play's seq>\tn=<n>", then
 * the card paths of the songs the queue holds after the one playing, at most n. Written whole (beside and renamed), and
 * only when it would change. */
#define AHEAD_DEFAULT 2
#define AHEAD_MAX 3
static struct {
    int n;
    long long stamp;
    char last[2200]; /* what was written last, its app's id first */
} ah = {.stamp = -2};

static int ahead_n(void) {
    long long st = file_stamp(path("/mnt/data/config.ini"));
    if (st != ah.stamp) {
        char *text = read_all(path("/mnt/data/config.ini"));
        int v = text ? ini_get(text, "Q2POD", "AHEAD", AHEAD_DEFAULT) : AHEAD_DEFAULT;
        free(text);
        ah.n = v >= 0 && v <= AHEAD_MAX ? v : AHEAD_DEFAULT, ah.stamp = st;
    }
    return ah.n;
}

static void ahead_write(const char *app, const char *text) {
    char key[sizeof ah.last], tmp[300], dst[300];
    snprintf(key, sizeof key, "%s\n%s", app, text);
    if (!strcmp(key, ah.last)) return;
    snprintf(dst, sizeof dst, "/tmp/q2-%s.ahead", app), snprintf(tmp, sizeof tmp, "/tmp/q2-%s.ahead.q2playd", app);
    FILE *f = fopen(path(tmp), "w");
    if (!f) return;
    fputs(text, f);
    if (fclose(f) || rename(path(tmp), path(dst))) return;
    memcpy(ah.last, key, strlen(key) + 1);
}

/* a list handed to app (before its .catplay of seq): n and no songs, as the queue holds none of it yet (ahead_start) */
static void ahead_start(const char *app, unsigned seq) {
    char text[64];
    snprintf(text, sizeof text, "#Q2AHEAD/1\tseq=%u\tn=%d\n", seq, ahead_n());
    ahead_write(app, text);
}

/* while the queue is an app's answer: the songs after the one playing (ahead_poll) */
static void ahead_poll(void) {
    if (!mq.on || src.on) return;
    char text[sizeof ah.last - 80];
    int n = ahead_n(), len = snprintf(text, sizeof text, "#Q2AHEAD/1\tseq=%u\tn=%d\n", mq.seq, n);
    for (int i = q.at + 1; i <= q.at + n && i < q.np; ++i) {
        if (len + strlen(q.paths[i]) + 2 > sizeof text) break;
        len += snprintf(text + len, sizeof text - (size_t)len, "%s\n", q.paths[i]);
    }
    ahead_write(mq.app, text);
}

static void read_small(const char *p, char *out, size_t room) {
    FILE *f = fopen(p, "r");
    size_t n = f ? fread(out, 1, room - 1, f) : 0;
    if (f) fclose(f);
    out[n] = 0;
}

/* the queue as q2playd plays it itself: its card rows and the server rows with an offline copy, from the first of them
 * at or after the one asked for (at), as the firmware's cat_offline */
static int play_offline(int at) {
    int n = 0, start = -1, copies = 0;
    char copy[1024];
    for (int i = 0; i < q.n; ++i) {
        int server = (unsigned)q.ids[q.order[i]] >= CAT_ID_BASE;
        if (!server || copy_of(q.ids[q.order[i]], copy, sizeof copy)) {
            if (i >= at && start < 0) start = n;
            q.order[n++] = q.order[i], copies += server;
        }
    }
    q.n = n, q.offline = 1;
    logf_("the list played offline: %d songs, %d of them offline copies", n, copies);
    return n ? start_at(start < 0 ? 0 : start) : -1;
}

/* the app of a server row's path, /mnt/mmc/.<id>/cat/... */
static int app_of(const char *fileurl, char *app, size_t room) {
    const char *pre = "/mnt/mmc/.", *p;
    if (strncmp(fileurl, pre, strlen(pre)) || !(p = strstr(fileurl + strlen(pre), "/cat/"))) return -1;
    snprintf(app, room, "%.*s", (int)(p - fileurl - strlen(pre)), fileurl + strlen(pre));
    return 0;
}

/* One Q2NET/1 command to q2netd (docs/platform/network.md): its answer line into out, 0; -1 with no q2netd */
static int net_ask(const char *cmd, char *out, size_t room) {
    struct sockaddr_un a = {.sun_family = AF_UNIX};
    snprintf(a.sun_path, sizeof a.sun_path, "%s", path("/run/vamp/net.sock"));
    int fd = socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0);
    struct timeval tv = {1, 0};
    char b[512], req[64];
    size_t n = 0;
    if (fd < 0) return -1;
    setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof tv), setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, &tv, sizeof tv);
    int len = snprintf(req, sizeof req, "Q2NET/1\n%s\n", cmd);
    if (connect(fd, (struct sockaddr *)&a, sizeof a) || send(fd, req, (size_t)len, MSG_NOSIGNAL) != len) return close(fd), -1;
    for (char *nl; n < sizeof b - 1;) {
        ssize_t k = recv(fd, b + n, sizeof b - 1 - n, 0);
        if (k <= 0) break;
        n += (size_t)k, b[n] = 0;
        if ((nl = strchr(b, '\n')) && strchr(nl + 1, '\n')) break;
    }
    close(fd);
    b[n] = 0;
    char *nl = strchr(b, '\n');
    if (strncmp(b, "Q2NET/1 ok\n", 11) || !nl || !strchr(nl + 1, '\n')) return -1;
    *strchr(nl + 1, '\n') = 0;
    snprintf(out, room, "%s", nl + 1);
    return 0;
}

/* Wi-Fi's state for a server list, as the firmware's cat_online and smart_wifi_wait judge it: 1 joined, or no q2netd to
 * ask (as before it); 2 Smart Wi-Fi has the radio off (a press wakes it); 0 no link (Wi-Fi off, or on and joined to
 * nothing) */
static int link_state(void) {
    char s[400];
    if (net_ask("status", s, sizeof s) || strncmp(s, "status ", 7)) return 1;
    if (strstr(s, " state=connected") || !strncmp(s, "status state=connected", 22)) return 1;
    return strstr(s, "state=off") && strstr(s, " smart=1") ? 2 : 0;
}

/* q2pkgd asked to start app (Q2PKG/1 start): 0 when it answers ok */
static int pkg_start(const char *app) {
    struct sockaddr_un a = {.sun_family = AF_UNIX};
    snprintf(a.sun_path, sizeof a.sun_path, "%s", path("/run/vamp/pkg.sock"));
    int fd = socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0);
    struct timeval tv = {PKG_ASK_MS / 1000, (PKG_ASK_MS % 1000) * 1000};
    char b[256], req[100];
    size_t n = 0;
    if (fd < 0) return -1;
    setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof tv), setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, &tv, sizeof tv);
    int len = snprintf(req, sizeof req, "Q2PKG/1\nstart %s\n", app);
    if (connect(fd, (struct sockaddr *)&a, sizeof a) || send(fd, req, (size_t)len, MSG_NOSIGNAL) != len) return close(fd), -1;
    for (char *nl; n < sizeof b - 1;) { /* two lines: the hello's answer and the start's */
        ssize_t k = recv(fd, b + n, sizeof b - 1 - n, 0);
        if (k <= 0) break;
        n += (size_t)k, b[n] = 0;
        if ((nl = strchr(b, '\n')) && strchr(nl + 1, '\n')) break;
    }
    close(fd);
    b[n] = 0;
    if (strncmp(b, "Q2PKG/1 ok\n", 11)) return logf_("q2pkgd did not answer the start of %s", app), -1;
    b[strcspn(b + 11, "\n") + 11] = 0;
    if (strcmp(b + 11, "ok")) return logf_("q2pkgd will not start %s: %s", app, b + 11), -1;
    return 0;
}

/* the catplay written for cp.app: 0, or the list played offline */
static int catplay_write(void) {
    char tmp[300], dst[300], play[300];
    snprintf(tmp, sizeof tmp, "/tmp/q2-%s.catplay.q2playd", cp.app), snprintf(dst, sizeof dst, "/tmp/q2-%s.catplay", cp.app);
    snprintf(play, sizeof play, "/tmp/q2-%s.play", cp.app);
    read_small(path(play), cp.before, sizeof cp.before);
    FILE *f = fopen(path(tmp), "w");
    if (!f) return cp.on = 0, play_offline(cp.at), -1;
    ahead_start(cp.app, cp.seq + 1); /* before the catplay, so the app never runs the list on its default */
    fprintf(f, "%u %d\n", ++cp.seq, cp.at);
    for (int i = 0; i < q.n; ++i) fprintf(f, "%s\n", song_lookup(q.ids[q.order[i]]) ? q.file : "");
    if (fclose(f) || rename(path(tmp), path(dst))) return cp.on = 0, play_offline(cp.at), -1;
    cp.starting = 0, cp.until = now_ms() + CATPLAY_WAIT_MS;
    logf_("list handed to %s (catplay %u)", cp.app, cp.seq);
    return 0;
}

/* the app's /tmp/q2-<id>.state says server=down (docs/server-library.md: its server is gone) */
static int server_down(const char *app) {
    char p[300], s[600];
    snprintf(p, sizeof p, "/tmp/q2-%s.state", app);
    read_small(path(p), s, sizeof s);
    for (char *l = s; l; l = strchr(l, '\n') ? strchr(l, '\n') + 1 : 0)
        if (!strncmp(l, "server=down", 11) && (!l[11] || l[11] == '\n' || l[11] == '\r')) return 1;
    return 0;
}

/* whether a play from ids' at'th song must be refused: a server song with no offline copy while its app's server is
 * down (the firmware's "Not available offline"); what plays goes on */
static int unavailable(const int *ids, int at) {
    sqlite3 *db;
    sqlite3_stmt *st;
    char url[1024] = "", app[64], copy[1024];
    if ((unsigned)ids[at] < CAT_ID_BASE) return 0;
    if (sqlite3_open_v2(cat_path, &db, SQLITE_OPEN_READONLY, 0) == SQLITE_OK &&
        sqlite3_prepare_v2(db, "select fileurl from servertable where id = ?", -1, &st, 0) == SQLITE_OK) {
        sqlite3_bind_int(st, 1, ids[at]);
        if (sqlite3_step(st) == SQLITE_ROW && sqlite3_column_text(st, 0))
            snprintf(url, sizeof url, "%s", (const char *)sqlite3_column_text(st, 0));
        sqlite3_finalize(st);
    }
    sqlite3_close(db);
    if (!*url || app_of(url, app, sizeof app) || !server_down(app) || copy_of(ids[at], copy, sizeof copy)) return 0;
    logf_("song %d is not available offline: %s's server is down", ids[at], app);
    return 1;
}

static int listening(const char *app) {
    char sock[300];
    snprintf(sock, sizeof sock, "/tmp/q2-%s.browse.sock", app);
    return !access(path(sock), F_OK);
}

static int catplay_go(void);
/* 1: handed to the app, or to q2pkgd to start it, or Wi-Fi woken for it (an answer awaited); 0: nothing of the list is
 * a server's */
static int catplay(int at) {
    char app[64] = "";
    int server = 0;
    for (int i = 0; i < q.n && !server; ++i)
        if ((unsigned)q.ids[q.order[i]] >= CAT_ID_BASE && song_lookup(q.ids[q.order[i]]) && !app_of(q.file, app, sizeof app)) server = 1;
    if (!server) return 0;
    snprintf(cp.app, sizeof cp.app, "%s", app);
    if (server_down(app)) {
        logf_("%s's server is down", app);
        return play_offline(at), 1;
    }
    int link = link_state();
    if (!link) {
        logf_("no Wi-Fi link: the list played offline");
        return play_offline(at), 1;
    }
    cp.on = 1, cp.at = at, cp.linking = 0;
    if (link == 2) { /* Smart Wi-Fi's radio off: woken, and the list handed over once it joins */
        char ok[64];
        net_ask("wake", ok, sizeof ok);
        cp.linking = 1, cp.until = now_ms() + LINK_WAIT_MS, cp.looked = now_ms();
        logf_("Wi-Fi woken for the list");
        return 1;
    }
    return catplay_go();
}

/* the list to the app: written while it listens, else the app started first (q2pkgd); 1 */
static int catplay_go(void) {
    const char *app = cp.app;
    int at = cp.at;
    if (listening(app)) return catplay_write(), 1;
    if (pkg_start(app)) {
        cp.on = 0;
        logf_("%s is not running: the list's server songs cannot stream", app);
        return play_offline(at), 1;
    }
    cp.starting = 1, cp.until = now_ms() + CATPLAY_START_MS;
    logf_("%s started for the list (q2pkgd)", app);
    return 1;
}

/* while a catplay waits: its answer taken, or its time out */
static void catplay_poll(void) {
    char p[300], now[600];
    if (!cp.on) return;
    if (cp.linking) {
        unsigned long long t = now_ms();
        if (t >= cp.looked + LINK_LOOK_MS) {
            cp.looked = t;
            if (link_state() == 1) cp.linking = 0, logf_("Wi-Fi joined"), catplay_go();
        }
        if (cp.linking && t >= cp.until) {
            cp.on = cp.linking = 0;
            logf_("no Wi-Fi link within %d s: the list played offline", LINK_WAIT_MS / 1000);
            play_offline(cp.at);
        }
        return;
    }
    if (cp.starting) {
        if (listening(cp.app)) catplay_write();
        else if (now_ms() >= cp.until) {
            cp.on = cp.starting = 0;
            logf_("%s did not listen within %d s of its start", cp.app, CATPLAY_START_MS / 1000);
            play_offline(cp.at);
        }
        return;
    }
    snprintf(p, sizeof p, "/tmp/q2-%s.play", cp.app);
    read_small(path(p), now, sizeof now);
    unsigned seq = 0;
    char m3u[512] = "";
    if (strcmp(now, cp.before) && sscanf(now, "%u %511[^\n]", &seq, m3u) == 2 && seq == cp.seq) {
        cp.on = 0;
        free_paths();
        mq.on = 1, mq.next = 0, mq.waiting = 0, mq.seq = seq;
        snprintf(mq.app, sizeof mq.app, "%s", cp.app);
        if (strlen(m3u) >= sizeof mq.file) m3u[0] = 0; /* no such path here: an empty answer */
        memcpy(mq.file, m3u, strlen(m3u) + 1);
        m3u_read();
        logf_("%s answered catplay %u with %d songs there%s%s", cp.app, seq, q.np, *mq.want ? ", waiting for " : "", mq.want);
        if (q.np) {
            if (start_at(0)) free_paths(), q.state = STOPPED, ++q.seq;
        } else if (*mq.want) {
            mq.waiting = 1, q.at = 0, q.state = PLAYING, q.loaded = 0; /* nothing there yet: its first plays as it lands */
            path_lookup_want();
        } else {
            free_paths(), q.state = STOPPED, ++q.seq;
        }
        return;
    }
    if (now_ms() >= cp.until) {
        cp.on = 0;
        logf_("%s did not answer catplay %u", cp.app, cp.seq);
        play_offline(cp.at);
    }
}

static unsigned rnd = 0x9e3779b9u;
static unsigned next_rand(void) { return rnd = rnd * 1664525u + 1013904223u; }

/* The play order shuffled, the song at order index first first, the rest in a random order; q.at 0 */
static void reshuffle(int first) {
    int keep = q.order[first];
    rnd ^= (unsigned)now_ms();
    q.order[first] = q.order[0], q.order[0] = keep;
    for (int i = q.n - 1; i > 1; --i) {
        int j = 1 + (int)(next_rand() % (unsigned)i), t = q.order[i];
        q.order[i] = q.order[j], q.order[j] = t;
    }
    q.at = 0, q.shuffle = 1, ++q.seq;
}

/* Car Remote's SetMode, "mode SHUFFLE REPEAT" (shuffle 0 or 1, repeat 0 Off, 1 One, 2 All; -1 leaves either), as
 * np_bar.c np_mode_set saves Now Playing's two controls: Repeat One plays as 1 whatever shuffle says, shuffle with
 * Off or All as 2, All alone as 3, neither as 0. The queue playing follows a shuffle turned on (the song playing
 * first, the rest drawn) or off (its own order, at the song playing). 0 set; -1 refused. */
static int set_mode(int shuffle, int repeat) {
    if (shuffle < -1 || shuffle > 1 || repeat < -1 || repeat > 2) return -1;
    gapless_forget();
    int on = shuffle >= 0 ? shuffle : pm_shuffle(), rep = repeat >= 0 ? repeat : pm_repeat();
    if (rep != 1 && pm.rep_off != (rep == 0)) pm.rep_off = rep == 0, ini_set("IPOD", "NPREPOFF", pm.rep_off);
    if (pm.shuf_one != on) pm.shuf_one = on, ini_set("IPOD", "NPSHUFONE", pm.shuf_one);
    int to = rep == 1 ? 1 : on ? 2 : rep == 2 ? 3 : 0;
    if (to != pm.mode) pm.mode = to, ini_set("PLAYSET", "PLAYMODE", to);
    if (q.n && !q.paths && !src.on && on != q.shuffle) {
        if (on) reshuffle(q.at);
        else {
            int id_at = q.order[q.at];
            for (int i = 0; i < q.n; ++i) q.order[i] = i;
            q.at = id_at, q.shuffle = 0;
        }
    }
    ++q.seq;
    return 0;
}

/* play AT SHUFFLE over ids (n of them): the song at AT first, then on in order, or, shuffled, the rest in a random order */
static int play_ids(const int *ids, int n, int at, int shuffle) {
    if (n <= 0 || at < 0 || at >= n) return -1;
    if (n > QUEUE_MAX) n = QUEUE_MAX;
    shuffle = shuffle || pm_shuffle(); /* under Shuffle every play is shuffled, as stock's mode plays it */
    memcpy(q.ids, ids, (size_t)n * sizeof *ids);
    q.n = n, q.shuffle = shuffle, q.errors = 0, ++q.list;
    for (int i = 0; i < n; ++i) q.order[i] = i;
    if (shuffle) {
        rnd ^= (unsigned)now_ms();
        if (at) q.order[0] = at, q.order[at] = 0;
        for (int i = n - 1; i > 1; --i) {
            int j = 1 + (int)(next_rand() % (unsigned)i), t = q.order[i];
            q.order[i] = q.order[j], q.order[j] = t;
        }
        at = 0;
    }
    free_paths(), cp.on = cp.starting = cp.linking = 0, q.offline = 0;
    if (catplay(at)) return 0;
    return start_at(at);
}

/* the ids of a play from the list file q2ui writes (one a line), into ids; how many */
static int read_list(int *ids) {
    FILE *f = fopen(path(list_rel), "r");
    int n = 0;
    char line[32];
    while (f && n < QUEUE_MAX && fgets(line, sizeof line, f)) ids[n++] = atoi(line);
    if (f) fclose(f);
    return n;
}

static int pending_ids[QUEUE_MAX];

/* One command. 0 done; -1 refused. */
/* ---- ratings and likes (docs/playd.md#ratings-and-likes) ----
 * A song's 0 to 5 stars in today's Vamp's table (patch/ratings.c, docs/internals.md#ratings), so either system reads
 * what the other wrote: /mnt/data/ringnav-ratings, words each holding a song's listen_key with its low three bits the
 * stars (0 a free slot, RATE_GONE a rating taken out), placed by the key's hash (rate_home) and probed on from there;
 * RATE_MIN words to start, doubled whenever a new rating would put more than three quarters of the slots in use, up
 * to RATE_SLOTS; the last two words the layout mark and the slots in use, written whole on each change. An older file,
 * its ratings from the first slot, is laid out at its first use and kept once as it was, ringnav-ratings.v1. The like
 * (the heart) is apart from the stars and is stock's My Fav, as today's is: q2libd's library has no myfav, so a like
 * is kept in /mnt/data/vamp/likes.db's myfav (stock's table's fileurl column alone, what coda-rc lists) and written
 * through to stock's myfav in /mnt/data/database.db in stock's form (fav_flush), waiting there when it cannot be yet;
 * at each start the likes are taken again from stock's myfav, which today's Vamp may have changed. A song is keyed by its path, a server song playing from its offline copy
 * by its server row's fileurl (catalogue.c catalogue_song_key). A source's URL has neither. */
#define RATE_GONE 7u
#define RATE_MIN 4096
#define RATE_SLOTS 16384
#define RATE_LAYOUT 0x48415356u /* "VSAH", its low bits 6 */
static const char *likes_rel = "/mnt/data/vamp/likes.db", *stock_db_rel = "/mnt/data/database.db";
/* The two tables, a song's stars and an album's hearts (ringnav-album-ratings, keyed by album_key), alike */
static struct rtab { unsigned *t, cap, used; int read; const char *rel; } rt[2] = {{.rel = "/mnt/data/ringnav-ratings"},
                                                                                   {.rel = "/mnt/data/ringnav-album-ratings"}};
static struct { unsigned key; int v; } lk; /* the song's like as last read (likes.db changes only here) */
static void ratings_copy_due(unsigned long long t);

static unsigned fnv(unsigned h, const void *p, size_t n) {
    for (size_t i = 0; i < n; ++i) h = (h ^ ((const unsigned char *)p)[i]) * 16777619u;
    return h;
}

static unsigned rate_room(unsigned cap) { return (cap - 2) / 4 * 3; } /* slots in use at most */
static unsigned rate_home(unsigned key, unsigned m) { return ((key >> 3) * 2654435761u) % m; }
static void rate_place(unsigned *t, unsigned m, unsigned v, unsigned *used) { /* v at its key's place in t's m slots */
    unsigned k = rate_home(v, m);
    for (unsigned n = 0; n < m && t[k] && (t[k] ^ v) >= 8; n++) k = (k + 1) % m;
    *used += !t[k];
    t[k] = v;
}
/* t's n words' ratings (marks and malformed slots passed over) in a new table of cap words for r; 0 with no memory */
static unsigned *rate_layout(struct rtab *r, const unsigned *t, unsigned n, unsigned cap) {
    unsigned *u = calloc(cap, sizeof *u), used = 0;
    for (unsigned i = 0; u && i < n && used < rate_room(cap); i++)
        if ((t[i] & 7) && (t[i] & 7) <= 5) rate_place(u, cap - 2, t[i], &used);
    if (u) r->cap = cap, r->used = used;
    return u;
}

/* buf written whole to p through p.tmp, synced and renamed: 1 when it was */
static int blob_save(const char *p, const void *buf, size_t n) {
    char tmp[700];
    snprintf(tmp, sizeof tmp, "%s.tmp", p);
    FILE *f = fopen(tmp, "wb");
    if (!f) return 0;
    int ok = fwrite(buf, 1, n, f) == n;
    if (fflush(f) || fsync(fileno(f))) ok = 0;
    if (fclose(f) || !ok || rename(tmp, p)) return unlink(tmp), 0;
    return 1;
}

static int rate_save(struct rtab *r) {
    unsigned m = r->cap - 2;
    r->t[m] = RATE_LAYOUT, r->t[m + 1] = r->used << 3 | 6;
    return blob_save(path(r->rel), r->t, r->cap * sizeof *r->t);
}

/* r's table, read at its first use (ratings.c rates) */
static unsigned *rates(struct rtab *r) {
    if (r->t || r->read) return r->t;
    r->read = 1;
    unsigned n = 0, room = RATE_MIN, rated = 0, cap = RATE_MIN;
    unsigned *in = calloc(room, sizeof *in);
    FILE *file = in ? fopen(path(r->rel), "rb") : 0;
    while (file && (n += (unsigned)fread(in + n, sizeof *in, room - n, file)) == room && room < RATE_SLOTS &&
           in[room - 2] != RATE_LAYOUT) {
        unsigned *more = calloc(room * 2, sizeof *more);
        if (!more) break;
        memcpy(more, in, room * sizeof *in);
        free(in), in = more, room *= 2;
    }
    if (file) fclose(file);
    if (!in) return 0;
    if (n == room && in[n - 2] == RATE_LAYOUT) { /* laid out already */
        r->cap = n, r->used = in[n - 1] >> 3;
        return r->t = in;
    }
    for (unsigned i = 0; i < n; i++) rated += (in[i] & 7) && (in[i] & 7) <= 5;
    while (cap < RATE_SLOTS && rate_room(cap) < rated + 1) cap *= 2;
    char v1[700];
    snprintf(v1, sizeof v1, "%s.v1", path(r->rel));
    if (rated && access(v1, F_OK)) blob_save(v1, in, n * sizeof *in); /* kept once as it was */
    r->t = rate_layout(r, in, n, cap);
    free(in);
    return r->t;
}

static int rate_grow(struct rtab *r) { /* twice the size, laid out again; 0 at RATE_SLOTS or with no memory */
    unsigned *u;
    if (r->cap >= RATE_SLOTS || !(u = rate_layout(r, r->t, r->cap - 2, r->cap * 2))) return 0;
    free(r->t), r->t = u;
    return 1;
}

/* key's slot; with room, the first free one on its probe when it has none (the table grown first when a new slot
 * would fill it past its room); -1 when neither */
static int rate_slot(struct rtab *r, unsigned key, int room) {
    unsigned *t = rates(r), m = t ? r->cap - 2 : 0, k = rate_home(key, m | !m);
    int empty = -1;
    for (unsigned n = 0; t && n < m; n++, k = (k + 1) % m) {
        if (!t[k]) {
            if (!room) return -1;
            if (empty >= 0) return empty;
            if (r->used < rate_room(r->cap)) return (int)k;
            return rate_grow(r) ? rate_slot(r, key, room) : -1;
        }
        if (t[k] == RATE_GONE) {
            if (empty < 0) empty = (int)k;
        } else if ((t[k] ^ key) < 8)
            return (int)k;
    }
    return room ? empty : -1;
}

static int table_kept(struct rtab *r, unsigned key) { /* 1 to 5, else 0 */
    int i = rate_slot(r, key, 0), n = i < 0 ? 0 : (int)(r->t[i] & 7);
    return n <= 5 ? n : 0;
}

/* n for key, none for 0: 1 saved, 0 the table full, -1 not written (the slot as it was) */
static int table_keep(struct rtab *r, unsigned key, int n) {
    int some = n > 0, i = rate_slot(r, key, some);
    if (i < 0) return !some;
    unsigned old = r->t[i];
    r->t[i] = some ? (key & ~7u) | (unsigned)n : RATE_GONE;
    r->used += !old;
    if (rate_save(r)) return 1;
    r->t[i] = old, r->used -= !old;
    return -1;
}
static int rate_kept(unsigned key) { return table_kept(&rt[0], key); }
static int rate_keep(unsigned key, int n) { return table_keep(&rt[0], key, n); }

/* What is rated: the queue's song, by its server row's fileurl for an offline copy; a source's URL has an empty queue
 * (its own put aside: source_take) */
static const char *rated_path(void) { return !q.n || !*q.file ? 0 : *q.row_url ? q.row_url : q.file; }
static unsigned listen_key(const char *p) { /* listen_key's: the path's FNV-1a and fnv's separator, never 0 */
    unsigned k = fnv(2166136261u, p, strlen(p)) * 16777619u;
    return k | !k;
}
static int playing_id(void) { return q.n && !q.paths ? q.ids[q.order[q.at]] : 0; } /* the now file's id= */

/* likes.db: myfav, the likes as coda-rc lists them, and waiting, likes not yet written through to stock's myfav
 * (fav_flush); 0 when it cannot be opened */
static sqlite3 *likes_open(void) {
    sqlite3 *db;
    char dir[640];
    snprintf(dir, sizeof dir, "%s/mnt/data/vamp", root);
    mkdir(dir, 0755);
    if (sqlite3_open_v2(path(likes_rel), &db, SQLITE_OPEN_READWRITE | SQLITE_OPEN_CREATE | SQLITE_OPEN_URI, 0) != SQLITE_OK ||
        sqlite3_exec(db, "create table if not exists myfav(fileurl text primary key);"
                         "create table if not exists waiting(fileurl text primary key, on_ int)", 0, 0, 0) != SQLITE_OK) {
        logf_("cannot open %s", path(likes_rel));
        sqlite3_close(db);
        return 0;
    }
    sqlite3_busy_timeout(db, 500);
    return db;
}

#define LIKES_LOOK_MS 2000 /* likes waiting for stock's myfav: fav_flush tried again this often */
static int fav_waiting;
static unsigned long long fav_looked;

/* The waiting likes written through to stock's myfav in /mnt/data/database.db, as today's Vamp writes it
 * (catalogue.c fav_set_on): the song's row out, and for a like its row copied in from the library's songtable, else
 * the catalogue's servertable, the columns stock's myfav has; a like whose row cannot be had (no myfav yet, the
 * catalogue's card away) waits. 1 when stock's myfav is there. */
static int fav_flush(sqlite3 *db) {
    char *u;
    int same = !strcmp(db_path, path(stock_db_rel)), cat = access(cat_path, R_OK) == 0, there = 0;
    if (access(path(stock_db_rel), W_OK)) return fav_waiting = 0, 0;
    u = sqlite3_mprintf("attach %Q as stock", path(stock_db_rel));
    int ok = sqlite3_exec(db, u, 0, 0, 0) == SQLITE_OK;
    sqlite3_free(u);
    if (!ok) return 0;
    sqlite3_stmt *st;
    if (sqlite3_prepare_v2(db, "select 1 from stock.sqlite_master where type = 'table' and name = 'myfav'", -1, &st, 0) == SQLITE_OK)
        there = sqlite3_step(st) == SQLITE_ROW, sqlite3_finalize(st);
    if (there && !same) {
        u = sqlite3_mprintf("attach %Q as lib", db_path);
        if (sqlite3_exec(db, u, 0, 0, 0) != SQLITE_OK) there = 0;
        sqlite3_free(u);
    }
    if (there && cat) {
        u = sqlite3_mprintf("attach %Q as cat", cat_path);
        if (sqlite3_exec(db, u, 0, 0, 0) != SQLITE_OK) cat = 0;
        sqlite3_free(u);
    }
    char cols[2][700] = {"", ""};
    for (int k = 0; there && k < 1 + cat; ++k) { /* the columns both have, stock's id its own */
        u = sqlite3_mprintf("select group_concat(name) from pragma_table_info('myfav', 'stock') where name != 'id' and "
                            "name in (select name from pragma_table_info(%Q, %Q))", k ? "servertable" : "songtable",
                            k ? "cat" : same ? "stock" : "lib");
        if (sqlite3_prepare_v2(db, u, -1, &st, 0) == SQLITE_OK && sqlite3_step(st) == SQLITE_ROW && sqlite3_column_text(st, 0))
            snprintf(cols[k], sizeof cols[k], "%s", (const char *)sqlite3_column_text(st, 0));
        sqlite3_finalize(st);
        sqlite3_free(u);
    }
    int left = 0;
    if (there && sqlite3_prepare_v2(db, "select fileurl, on_ from waiting", -1, &st, 0) == SQLITE_OK) {
        while (sqlite3_step(st) == SQLITE_ROW) {
            const char *f = (const char *)sqlite3_column_text(st, 0);
            int on = sqlite3_column_int(st, 1), done = 0;
            sqlite3_exec(db, "begin", 0, 0, 0);
            u = sqlite3_mprintf("delete from stock.myfav where fileurl = %Q", f);
            done = sqlite3_exec(db, u, 0, 0, 0) == SQLITE_OK && !on;
            sqlite3_free(u);
            for (int k = 0; on && !done && k < 1 + cat; ++k) {
                if (!*cols[k]) continue;
                u = sqlite3_mprintf("insert into stock.myfav(%s) select %s from %s.%s where fileurl = %Q limit 1", cols[k],
                                    cols[k], k ? "cat" : same ? "stock" : "lib", k ? "servertable" : "songtable", f);
                done = sqlite3_exec(db, u, 0, 0, 0) == SQLITE_OK && sqlite3_changes(db) > 0;
                sqlite3_free(u);
            }
            sqlite3_exec(db, done ? "commit" : "rollback", 0, 0, 0);
            if (done) {
                u = sqlite3_mprintf("delete from waiting where fileurl = %Q", f);
                sqlite3_exec(db, u, 0, 0, 0);
                sqlite3_free(u);
            } else
                ++left;
        }
        sqlite3_finalize(st);
    } else if (sqlite3_prepare_v2(db, "select count(*) from waiting", -1, &st, 0) == SQLITE_OK) {
        left = sqlite3_step(st) == SQLITE_ROW ? sqlite3_column_int(st, 0) : 0;
        sqlite3_finalize(st);
    }
    fav_waiting = left;
    sqlite3_exec(db, "detach stock", 0, 0, 0);
    if (there && !same) sqlite3_exec(db, "detach lib", 0, 0, 0);
    if (there && cat) sqlite3_exec(db, "detach cat", 0, 0, 0);
    return there;
}

/* At the start: likes waiting written through, then the likes are stock's myfav as it is now (today's Vamp may have
 * changed it since) with those still waiting over it; with no stock myfav, likes.db's own stand */
static void likes_sync(void) {
    sqlite3 *db = likes_open();
    if (!db) return;
    if (fav_flush(db)) {
        char *u = sqlite3_mprintf("attach %Q as stock;begin;delete from myfav;"
                                  "insert or ignore into myfav select fileurl from stock.myfav where fileurl is not null;"
                                  "insert or ignore into myfav select fileurl from waiting where on_;"
                                  "delete from myfav where fileurl in (select fileurl from waiting where not on_);commit;"
                                  "detach stock", path(stock_db_rel));
        if (sqlite3_exec(db, u, 0, 0, 0) == SQLITE_OK) logf_("likes: stock's favorites taken");
        sqlite3_free(u);
    }
    sqlite3_close(db);
}

static void likes_poll(unsigned long long t) {
    if (!fav_waiting || t < fav_looked + LIKES_LOOK_MS) return;
    fav_looked = t;
    sqlite3 *db = likes_open();
    if (db) fav_flush(db);
    sqlite3_close(db);
}

static int liked_now(void) { /* the song's like, 0 or 1, -1 when nothing is rated */
    const char *p = rated_path();
    if (!p) return -1;
    unsigned key = listen_key(p);
    if (lk.key == key) return lk.v;
    sqlite3 *db = likes_open();
    sqlite3_stmt *st;
    int v = 0;
    if (db && sqlite3_prepare_v2(db, "select 1 from myfav where fileurl = ?", -1, &st, 0) == SQLITE_OK) {
        sqlite3_bind_text(st, 1, p, -1, SQLITE_STATIC);
        v = sqlite3_step(st) == SQLITE_ROW;
        sqlite3_finalize(st);
    }
    sqlite3_close(db);
    if (db) lk.key = key, lk.v = v;
    return v;
}

static int hearts_now(void) { /* the song's stars (the shim's hearts), -1 when nothing is rated */
    const char *p = rated_path();
    return p ? rate_kept(listen_key(p)) : -1;
}

/* A change of the song's stars or like told to the servers (catalogue.c rated_line, docs/server-library.md#ratings-
 * and-favorites): "<listen_key>\t<sid>\t<stars=N|like=0|1>\t<time>" appended to the ratings.out of every app on the
 * card whose q2app says ratings=2 (ratings=1 takes one number and is sent nothing); the sid is the song's server row's
 * (servermeta), or for a card song a duplicate of one names (serverdup), and only in that row's app's line. */
static void rated_out_for(const char *p, const char *what, int v) {
    char app[64] = "", sid[256] = "", line[600], q2app[700], out[700];
    if (!p) return;
    sqlite3 *db;
    sqlite3_stmt *st;
    if (sqlite3_open_v2(cat_path, &db, SQLITE_OPEN_READONLY, 0) == SQLITE_OK &&
        (sqlite3_prepare_v2(db, "select m.app, m.sid from servermeta m where m.id in (select id from servertable where "
                                "fileurl = ?1 union select id from serverdup where fileurl = ?1) limit 1", -1, &st, 0) == SQLITE_OK ||
         sqlite3_prepare_v2(db, "select m.app, m.sid from servermeta m join servertable s on s.id = m.id where s.fileurl = ?1",
                            -1, &st, 0) == SQLITE_OK)) {
        sqlite3_bind_text(st, 1, p, -1, SQLITE_STATIC);
        if (sqlite3_step(st) == SQLITE_ROW && sqlite3_column_text(st, 0) && sqlite3_column_text(st, 1))
            snprintf(app, sizeof app, "%s", (const char *)sqlite3_column_text(st, 0)),
                snprintf(sid, sizeof sid, "%s", (const char *)sqlite3_column_text(st, 1));
        sqlite3_finalize(st);
    }
    sqlite3_close(db);
    DIR *d = opendir(path("/mnt/mmc"));
    for (struct dirent *e; d && (e = readdir(d));) {
        if (e->d_name[0] != '.' || !e->d_name[1] || !strcmp(e->d_name, "..")) continue;
        snprintf(q2app, sizeof q2app, "%s/mnt/mmc/%s/q2app", root, e->d_name);
        char *t = read_all(q2app);
        const char *r = t ? strstr(t, "ratings=") : 0;
        int takes = r && (r == t || r[-1] == '\n') && atoi(r + 8) == 2;
        free(t);
        if (!takes) continue;
        int n = snprintf(line, sizeof line, "%08x\t%s\t%s=%d\t%u\n", listen_key(p), !strcmp(app, e->d_name + 1) ? sid : "",
                         what, v, (unsigned)time(0));
        snprintf(out, sizeof out, "%s/mnt/mmc/%s/ratings.out", root, e->d_name);
        FILE *f = n > 0 && n < (int)sizeof line ? fopen(out, "ab") : 0;
        if (f) fwrite(line, 1, (size_t)n, f), fclose(f);
    }
    if (d) closedir(d);
}

/* The FIFO's "rate N ID" (coda_rc_shim.c): 0 set, 1 refused (past 5, the table full or not written), 3 when ID is not
 * the song playing */
/* n stars for the song p names (its path, or a server row's fileurl): 0 set, 1 refused (past 5, the table full or not
 * written) */
static int rate_path(const char *p, int n) {
    if (n < 0 || n > 5) return 1;
    int k = rate_keep(listen_key(p), n);
    if (k <= 0) logf_(k ? "rating not saved" : "ratings are full");
    if (k > 0) rated_out_for(p, "stars", n), ratings_copy_due(now_ms());
    ++q.seq;
    return k > 0 ? 0 : 1;
}
static int rate_cmd(int n, int id) {
    const char *p = rated_path();
    if (!p || id != playing_id()) return 3;
    return rate_path(p, n);
}

/* The FIFO's "like ID" (the pin's coda_rc_shim.c, ratings.c rate_now_like): the like flipped */
/* path's like set to on in likes.db, and through to stock's myfav now or once it can be: 1 when kept */
static int like_set(sqlite3 *db, const char *p, int on) {
    char *u = sqlite3_mprintf(on ? "insert or ignore into myfav values (%Q);insert or replace into waiting values (%Q, 1)"
                                 : "delete from myfav where fileurl = %Q;insert or replace into waiting values (%Q, 0)",
                              p, p);
    int ok = db && sqlite3_exec(db, u, 0, 0, 0) == SQLITE_OK;
    sqlite3_free(u);
    lk.key = 0; /* read again */
    return ok;
}

static int path_liked(const char *p) { /* p's like in likes.db, 0 or 1 */
    sqlite3 *db = likes_open();
    sqlite3_stmt *st;
    int v = 0;
    if (db && sqlite3_prepare_v2(db, "select 1 from myfav where fileurl = ?", -1, &st, 0) == SQLITE_OK) {
        sqlite3_bind_text(st, 1, p, -1, SQLITE_STATIC);
        v = sqlite3_step(st) == SQLITE_ROW;
        sqlite3_finalize(st);
    }
    sqlite3_close(db);
    return v;
}

/* the song p names liked (on 1) or not: 0 done (nothing to change counts), 1 not saved */
static int like_path(const char *p, int on) {
    if (path_liked(p) == on) return 0;
    sqlite3 *db = likes_open();
    int ok = like_set(db, p, on);
    if (ok) fav_flush(db);
    sqlite3_close(db);
    if (!ok) return logf_("like not saved"), 1;
    ++q.seq;
    rated_out_for(p, "like", on);
    ratings_copy_due(now_ms());
    return 0;
}

static int like_cmd(int id) {
    const char *p = rated_path();
    if (!p || id != playing_id()) return 3;
    return like_path(p, !liked_now());
}

/* What Q2PLAY/1's rating words name: song ID's path (a card song's, or a server row's fileurl), 0 the song playing;
 * 1 when there is one */
static int id_path(int id, char *out, size_t room) {
    if (!id) {
        const char *p = rated_path();
        return p && snprintf(out, room, "%s", p) < (int)room;
    }
    sqlite3 *db;
    sqlite3_stmt *st;
    int ok = 0;
    *out = 0;
    if (sqlite3_open_v2((unsigned)id >= CAT_ID_BASE ? cat_path : db_path, &db, SQLITE_OPEN_READONLY, 0) == SQLITE_OK &&
        sqlite3_prepare_v2(db, (unsigned)id >= CAT_ID_BASE ? "select fileurl from servertable where id = ?"
                                                           : "select fileurl from songtable where id = ?", -1, &st, 0) == SQLITE_OK) {
        sqlite3_bind_int(st, 1, id);
        if (sqlite3_step(st) == SQLITE_ROW && sqlite3_column_text(st, 0))
            ok = snprintf(out, room, "%s", (const char *)sqlite3_column_text(st, 0)) < (int)room && *out;
        sqlite3_finalize(st);
    }
    sqlite3_close(db);
    return ok;
}

/* A server's stars and likes coming in (docs/server-library.md#ratings-and-favorites, catalogue.c's ratings.in): each
 * app on the card with ratings=1 or 2 may write .<id>/ratings.in whole, in ratings.out's columns. stars=N is kept by
 * the line's listen_key; like=0|1 is the song's path's (a server row's by its sid in servermeta, a card song's by its
 * key in songtable), a server row's offline copy taken out of the likes either way; a ratings=1 app's one number is
 * its star at five (the like, the stars left alone), else that many stars and no like. A line is passed over when the
 * song's own ratings.out line of the same kind is newer (a line of one number counts as both). The file is then
 * removed. Looked for every LIKES_LOOK_MS; it waits while the card's catalogue is not there for a sid's line. */
static unsigned long long in_looked;

static int out_newer(const char *app, const char *key, char kind, unsigned t) {
    char f[700], line[600];
    int newer = 0;
    snprintf(f, sizeof f, "%s/mnt/mmc/.%s/ratings.out", root, app);
    FILE *o = fopen(f, "r");
    while (o && !newer && fgets(line, sizeof line, o)) {
        char k[16], sid[256], v[32];
        unsigned when;
        if (sscanf(line, "%15[^\t]\t%255[^\t]\t%31[^\t]\t%u", k, sid, v, &when) != 4 &&
            sscanf(line, "%15[^\t]\t\t%31[^\t]\t%u", k, v, &when) != 3)
            continue;
        newer = !strcmp(k, key) && when > t && (kind == 'h' || v[0] == kind);
    }
    if (o) fclose(o);
    return newer;
}

static void in_song(sqlite3 *cat, const char *app, const char *sid, unsigned key, char *path_, size_t room, char *copy, size_t croom) {
    sqlite3_stmt *st;
    path_[0] = copy[0] = 0;
    if (*sid && cat && sqlite3_prepare_v2(cat, "select s.fileurl, m.local from servermeta m join servertable s on s.id = m.id "
                                                "where m.app = ? and m.sid = ?", -1, &st, 0) == SQLITE_OK) {
        sqlite3_bind_text(st, 1, app, -1, SQLITE_STATIC), sqlite3_bind_text(st, 2, sid, -1, SQLITE_STATIC);
        if (sqlite3_step(st) == SQLITE_ROW && sqlite3_column_text(st, 0)) {
            snprintf(path_, room, "%s", (const char *)sqlite3_column_text(st, 0));
            if (sqlite3_column_text(st, 1)) snprintf(copy, croom, "%s", (const char *)sqlite3_column_text(st, 1));
        }
        sqlite3_finalize(st);
    }
    sqlite3 *db;
    if (*path_ || sqlite3_open_v2(db_path, &db, SQLITE_OPEN_READONLY, 0) != SQLITE_OK) return;
    if (sqlite3_prepare_v2(db, "select fileurl from songtable", -1, &st, 0) == SQLITE_OK) {
        while (!*path_ && sqlite3_step(st) == SQLITE_ROW)
            if (sqlite3_column_text(st, 0) && listen_key((const char *)sqlite3_column_text(st, 0)) == key)
                snprintf(path_, room, "%s", (const char *)sqlite3_column_text(st, 0));
        sqlite3_finalize(st);
    }
    sqlite3_close(db);
}

static void ratings_in_poll(unsigned long long t) {
    if (t < in_looked + LIKES_LOOK_MS) return;
    in_looked = t;
    DIR *d = opendir(path("/mnt/mmc"));
    for (struct dirent *e; d && (e = readdir(d));) {
        char f[700], line[600];
        if (e->d_name[0] != '.' || !e->d_name[1] || !strcmp(e->d_name, "..")) continue;
        snprintf(f, sizeof f, "%s/mnt/mmc/%s/q2app", root, e->d_name);
        char *m = read_all(f);
        const char *r = m ? strstr(m, "ratings=") : 0;
        int level = r && (r == m || r[-1] == '\n') ? atoi(r + 8) : 0;
        free(m);
        snprintf(f, sizeof f, "%s/mnt/mmc/%s/ratings.in", root, e->d_name);
        if (level < 1 || level > 2 || access(f, R_OK)) continue;
        const char *app = e->d_name + 1;
        sqlite3 *cat = 0, *likes = likes_open();
        if (sqlite3_open_v2(cat_path, &cat, SQLITE_OPEN_READONLY, 0) != SQLITE_OK) sqlite3_close(cat), cat = 0;
        FILE *in = fopen(f, "r");
        int changed = 0, wait = 0;
        while (in && fgets(line, sizeof line, in)) {
            char k[16] = "", sid[256] = "", v[32] = "";
            unsigned when = 0;
            if (sscanf(line, "%15[^\t]\t%255[^\t]\t%31[^\t]\t%u", k, sid, v, &when) != 4 &&
                (sid[0] = 0, sscanf(line, "%15[^\t]\t\t%31[^\t]\t%u", k, v, &when) != 3))
                continue;
            unsigned key = (unsigned)strtoul(k, 0, 16);
            int stars = -1, like = -1;
            if (!strncmp(v, "stars=", 6)) stars = atoi(v + 6);
            else if (!strncmp(v, "like=", 5)) like = atoi(v + 5) != 0;
            else if (*v >= '0' && *v <= '9') like = atoi(v) == 5, stars = atoi(v) < 5 ? atoi(v) : -1;
            char kind = v[0] >= '0' && v[0] <= '9' ? 'h' : v[0];
            if (!key || out_newer(app, k, kind, when)) continue;
            if (stars >= 0 && stars <= 5 && rate_keep(key, stars) > 0) changed = 1;
            if (like >= 0) {
                char song[1024], copy[1024];
                if (*sid && !cat) { wait = 1; break; }
                in_song(cat, app, sid, key, song, sizeof song, copy, sizeof copy);
                if (*song && like_set(likes, song, like)) changed = 1;
                if (*copy) like_set(likes, copy, 0);
            }
        }
        if (in) fclose(in);
        if (likes && changed) fav_flush(likes);
        sqlite3_close(likes), sqlite3_close(cat);
        if (!wait) unlink(f);
        if (changed) lk.key = 0, ++q.seq, ratings_copy_due(t), logf_("ratings from %s taken", app);
    }
    if (d) closedir(d);
}

/* ---- the card's copy of the ratings (docs/playd.md#the-cards-copy) ----
 * Today's Vamp's /mnt/mmc/.ratings.tsv (patch/ratings.c rc_*, docs/internals.md#ratings "Card copy"), for a computer:
 * /mnt/data is not on the card that USB storage shows. Two comment lines naming the columns, then "song", stars,
 * liked, path, a CUE track's start (none here: q2playd has no CUE tracks), and "album", hearts, name, album artist
 * and, for an album without one of those, its folder. The tables hold hashes, so a pass reads the paths and tags back:
 * the likes first (liked, with any stars kept for them), then the library's songtable, then the card catalogue's
 * servertable, a page of rows each time round the loop, the rated still to find sorted for a binary search; a table
 * is read on only while ratings are still to find. The file is built in memory and written only if it differs (a
 * temporary name, synced, renamed); a file with more lines is kept as .ratings.tsv.1 once the new one is written,
 * unless that has more still, so a lost table or a half-built library cannot take the only copy. A pass starts
 * RC_WAIT_MS after a change of the stars, the likes or an album's hearts, after the start, when the card comes back
 * and when either library file changes (a scan, a catalogue built); with the card away it waits for the card. A
 * rating whose song or album is in none of the three has only its hash and stays out until it is back. */
#define RC_WAIT_MS 5000
#define RC_PAGE 256
#define RC_HEADER "#song\tstars\tliked\tpath\tcue_start\n#album\thearts\tname\talbum_artist\tfolder\n"
static const char *rc_rel = "/mnt/mmc/.ratings.tsv";
static struct {
    int due, on, table, card, oom;
    long long last;           /* the last row id read of the table */
    unsigned long long at;    /* the last change, or the last look */
    unsigned *rated[2], n[2]; /* the rated still to find: keys with their stars or hearts, sorted; taken: low bits 0 */
    unsigned left[2];
    char *out;
    size_t len, cap;
    struct { long long ino, mtime, size; } lib, cat;
} rc;
static int card_mounted(void);

static void ratings_copy_due(unsigned long long t) { rc.due = 1, rc.at = t; }

/* coverflow.c album_dir(p, 1): the length of p's folder, a disc folder ("CD2", "Disc 1", "disk2") as its album's */
static unsigned album_dir(const char *p) {
    const char *e = strrchr(p, '/');
    unsigned n = e ? (unsigned)(e - p) : 0, i = n;
    while (i && p[i - 1] >= '0' && p[i - 1] <= '9') --i;
    if (i == n) return n;
    i -= i && (p[i - 1] == ' ' || p[i - 1] == '_' || p[i - 1] == '-');
    if (i >= 3 && p[i - 3] == '/' && !strncasecmp(p + i - 2, "cd", 2)) return i - 3;
    if (i >= 5 && p[i - 5] == '/' && !strncasecmp(p + i - 4, "dis", 3) && ((p[i - 1] | 32) == 'c' || (p[i - 1] | 32) == 'k'))
        return i - 5;
    return n;
}
static unsigned fnv_lower(unsigned h, const char *s) {
    for (; s && *s; ++s) {
        unsigned char c = (unsigned char)*s;
        c += (unsigned char)((unsigned)(c - 'A') < 26u) * 32;
        h = (h ^ c) * 16777619u;
    }
    return h * 16777619u;
}
/* coverflow.c album_ident, an album's key in ringnav-album-ratings: the name and the album artist ASCII-lowercased,
 * else the name and the folder (disc folders as one, case kept); never 0 */
static unsigned album_key(const char *album, const char *artist, const char *p) {
    unsigned h = fnv_lower(2166136261u, album);
    p = p ? p : "";
    h = album && *album && artist && *artist ? fnv_lower(h, artist) : fnv(h, p, album_dir(p)) * 16777619u;
    return h | !h;
}
static unsigned unknown_album_key(void) { /* coverflow.c album_id's Unknown Album card: fnv(FNV_SEED, "?") */
    unsigned h = fnv(2166136261u, "?", 1) * 16777619u;
    return h | !h;
}

static int rc_cmp(const void *a, const void *b) {
    unsigned x = *(const unsigned *)a >> 3, y = *(const unsigned *)b >> 3;
    return x < y ? -1 : x > y;
}
/* key's stars or hearts in table a's still to find, taken; 0 when it has none there */
static int rc_take(int a, unsigned key) {
    unsigned lo = 0, hi = rc.n[a];
    while (lo < hi) {
        unsigned mid = (lo + hi) / 2, w = rc.rated[a][mid] >> 3;
        if (w == key >> 3) {
            int v = (int)(rc.rated[a][mid] & 7);
            rc.rated[a][mid] &= ~7u;
            rc.left[a] -= v != 0;
            return v;
        }
        if (w < key >> 3) lo = mid + 1;
        else hi = mid;
    }
    return 0;
}

static void rc_put(const char *s, size_t n) {
    if (rc.oom) return;
    if (rc.len + n + 1 > rc.cap) {
        size_t cap = (rc.cap + n) * 2 + 4096;
        char *o = realloc(rc.out, cap);
        if (!o) {
            rc.oom = 1; /* ends the pass: a copy with rows missing must not replace the last */
            return;
        }
        rc.out = o, rc.cap = cap;
    }
    memcpy(rc.out + rc.len, s, n), rc.len += n;
}
/* playback_power.c scrobble_tag: s, at most room bytes, its tabs and line ends as spaces, then end */
static void rc_field(const char *s, size_t room, char end) {
    char b[1100];
    size_t n = 0;
    for (; s && *s && n < room && n < sizeof b; ++s) b[n++] = *s == '\t' || *s == '\n' || *s == '\r' ? ' ' : *s;
    rc_put(b, n), rc_put(&end, 1);
}
static void rc_song(const char *p, int stars, int liked) {
    char b[32];
    rc_put(b, (size_t)snprintf(b, sizeof b, "song\t%d\t%d\t", stars, liked));
    rc_field(p, 1099, '\t');
    rc_put("\n", 1);
}
static void rc_album(const char *name, const char *artist, const char *p, int hearts) {
    char b[32];
    rc_put(b, (size_t)snprintf(b, sizeof b, "album\t%d\t", hearts));
    rc_field(name, 599, '\t');
    rc_field(artist, 299, '\t');
    unsigned dir = name && *name && artist && *artist ? 0 : p ? album_dir(p) : 0;
    rc_field(p, dir < 1100 ? dir : 1100, '\n');
}

static void rc_end(void) {
    free(rc.rated[0]), free(rc.rated[1]), free(rc.out);
    memset(rc.rated, 0, sizeof rc.rated), rc.out = 0, rc.len = rc.cap = 0, rc.on = rc.oom = 0;
}

/* table a's valid ratings (1 to 5), sorted; 0 with no memory */
static int rc_rated(int a) {
    unsigned *t = rates(&rt[a]), m = t ? rt[a].cap - 2 : 0;
    rc.n[a] = rc.left[a] = 0;
    if (!(rc.rated[a] = calloc(m + 1, sizeof *rc.rated[a]))) return 0;
    for (unsigned i = 0; i < m; i++)
        if ((t[i] & 7) && (t[i] & 7) <= 5u) rc.rated[a][rc.n[a]++] = t[i];
    qsort(rc.rated[a], rc.n[a], sizeof *rc.rated[a], rc_cmp);
    rc.left[a] = rc.n[a];
    return 1;
}

static void rc_start(void) {
    int h;
    if (!rc_rated(0) || !rc_rated(1)) {
        rc_end();
        return;
    }
    rc.on = 1, rc.table = 0, rc.last = 0;
    rc_put(RC_HEADER, sizeof RC_HEADER - 1);
    if ((h = rc_take(1, unknown_album_key()))) rc_album("Unknown Album", "", 0, h);
}

/* path's lines, 0 with no file; same, if asked, whether it is this copy */
static unsigned rc_read(const char *p, int *same) {
    FILE *f = fopen(p, "rb");
    char b[1024];
    size_t n, at = 0;
    unsigned lines = 0;
    int eq = f != 0;
    while (f && (n = fread(b, 1, sizeof b, f)) > 0) {
        eq = eq && at + n <= rc.len && !memcmp(b, rc.out + at, n);
        at += n;
        for (size_t i = 0; i < n; i++) lines += b[i] == '\n';
    }
    if (f) fclose(f);
    if (same) *same = eq && at == rc.len;
    return lines;
}

static void rc_write(void) {
    char file[640], tmp[700], one[700];
    int same;
    snprintf(file, sizeof file, "%s", path(rc_rel));
    snprintf(tmp, sizeof tmp, "%s.tmp", file), snprintf(one, sizeof one, "%s.1", file);
    unsigned lines = rc_read(file, &same), now = 0;
    if (same) return;
    FILE *f = fopen(tmp, "wb");
    int ok = f && fwrite(rc.out, 1, rc.len, f) == rc.len;
    if (f && (fflush(f) || fsync(fileno(f)))) ok = 0;
    if (f && fclose(f)) ok = 0;
    if (!ok) {
        unlink(tmp);
        logf_("cannot write %s", file);
        return;
    }
    for (size_t i = 0; i < rc.len; i++) now += rc.out[i] == '\n';
    if (lines > now && lines >= rc_read(one, 0)) rename(file, one);
    if (rename(tmp, file)) unlink(tmp);
}

static void rc_row(sqlite3_stmt *st) { /* a library row: id, fileurl, album, albumsonger */
    const char *p = (const char *)sqlite3_column_text(st, 1);
    int v;
    rc.last = sqlite3_column_int64(st, 0);
    if (!p) return;
    if (rc.left[0] && (v = rc_take(0, listen_key(p)))) rc_song(p, v, 0);
    const char *al = (const char *)sqlite3_column_text(st, 2), *aa = (const char *)sqlite3_column_text(st, 3);
    if (rc.left[1] && (v = rc_take(1, album_key(al, aa, p)))) rc_album(al, aa, p, v);
}

/* A page of the pass, then its end: a read that fails leaves the copy as it was, for the next change */
static void rc_page(void) {
    if (rc.table == 0) { /* the likes, liked, with any stars kept for them */
        sqlite3 *db = likes_open();
        sqlite3_stmt *st;
        if (!db || sqlite3_prepare_v2(db, "select fileurl from myfav order by rowid", -1, &st, 0) != SQLITE_OK) {
            sqlite3_close(db);
            rc_end();
            return;
        }
        while (sqlite3_step(st) == SQLITE_ROW)
            if (sqlite3_column_text(st, 0)) {
                const char *p = (const char *)sqlite3_column_text(st, 0);
                rc_song(p, rc_take(0, listen_key(p)), 1);
            }
        sqlite3_finalize(st), sqlite3_close(db);
        rc.table = 1, rc.last = 0;
        return;
    }
    if (rc.table < 3 && (rc.left[0] || rc.left[1])) {
        const char *file = rc.table == 1 ? db_path : cat_path,
                   *table = rc.table == 1 ? "songtable" : "servertable";
        sqlite3 *db = 0;
        sqlite3_stmt *st;
        int rows = 0, there = 0;
        if (access(file, R_OK) == 0 && sqlite3_open_v2(file, &db, SQLITE_OPEN_READONLY, 0) == SQLITE_OK) {
            if (sqlite3_prepare_v2(db, "select 1 from sqlite_master where type = 'table' and name = ?", -1, &st, 0) == SQLITE_OK) {
                sqlite3_bind_text(st, 1, table, -1, SQLITE_STATIC);
                there = sqlite3_step(st) == SQLITE_ROW;
                sqlite3_finalize(st);
            }
            char *u = sqlite3_mprintf("select id, fileurl, album, albumsonger from %s where id > ? order by id limit %d",
                                      table, RC_PAGE);
            if (there && sqlite3_prepare_v2(db, u, -1, &st, 0) != SQLITE_OK) there = -1;
            else if (there) {
                sqlite3_bind_int64(st, 1, rc.last);
                while (sqlite3_step(st) == SQLITE_ROW) rc_row(st), rows++;
                sqlite3_finalize(st);
            }
            sqlite3_free(u);
        }
        sqlite3_close(db);
        if (there < 0) {
            logf_("cannot read %s for the ratings' copy", file);
            rc_end();
            return;
        }
        if (rows == RC_PAGE && !rc.oom) return;
        rc.table++, rc.last = 0;
        if (rc.table < 3) return;
    }
    if (!rc.oom) {
        if (card_mounted()) rc_write();
        else rc.due = 1; /* the card away (shared over USB): the pass again once it is back */
    }
    rc_end();
}

static void stamp(const char *p, long long *ino, long long *mtime, long long *size, int *moved) {
    struct stat s;
    long long i = 0, m = 0, z = 0;
    if (!stat(p, &s)) i = (long long)s.st_ino, m = (long long)s.st_mtime, z = (long long)s.st_size;
    *moved |= i != *ino || m != *mtime || z != *size;
    *ino = i, *mtime = m, *size = z;
}

/* Each RC_WAIT_MS: a library file changed, or the card back, makes a pass due; a due one starts once nothing has
 * changed for RC_WAIT_MS and the card is there. 1 while a pass is under way, for the loop's wait. */
static int ratings_copy_poll(unsigned long long t) {
    if (rc.on) return rc_page(), rc.on;
    if (!rc.at) {
        rc.due = 1, rc.at = t | !t;
        stamp(db_path, &rc.lib.ino, &rc.lib.mtime, &rc.lib.size, &rc.due);
        stamp(cat_path, &rc.cat.ino, &rc.cat.mtime, &rc.cat.size, &rc.due);
        return 0;
    }
    if (t < rc.at + RC_WAIT_MS) return 0;
    rc.at = t;
    int card = card_mounted(), moved = 0;
    stamp(db_path, &rc.lib.ino, &rc.lib.mtime, &rc.lib.size, &moved);
    stamp(cat_path, &rc.cat.ino, &rc.cat.mtime, &rc.cat.size, &moved);
    if (moved || (card && !rc.card)) rc.due = 1;
    rc.card = card;
    if (!rc.due || !card) return 0;
    rc.due = 0;
    rc_start();
    return rc.on;
}

/* ---- albums' hearts (docs/playd.md#albums-hearts) ----
 * Today's Vamp's (patch/ratings.c, docs/internals.md#ratings "Albums"): an album's 0 to 5 hearts in
 * /mnt/data/ringnav-album-ratings, the song table's format, keyed by album_key. Only Favorite and Unfavorite are given
 * (5 or 0, the queue menu's; there is no Rate Album, so 1 to 4 kept before stay as they are). An album's favourite is
 * its songs' like: reaching 5 likes every song of the album (the library's and the card catalogue's rows with its
 * key), leaving 5 takes them all out of the likes, each written through and told to the servers as any like, their
 * stars left alone. ponytail: leaving 5 takes out a song liked on its own too, as today's does. */

/* row id's album, album artist and path (cut to their buffers), from the library or for a server id the card catalogue: 1 when found */
static int album_of(int id, char al[600], char aa[600], char p[1100]) {
    sqlite3 *db;
    sqlite3_stmt *st;
    int found = 0;
    if (sqlite3_open_v2(id >= CAT_ID_BASE ? cat_path : db_path, &db, SQLITE_OPEN_READONLY, 0) == SQLITE_OK &&
        sqlite3_prepare_v2(db, id >= CAT_ID_BASE ? "select album, albumsonger, fileurl from servertable where id = ?"
                                                 : "select album, albumsonger, fileurl from songtable where id = ?",
                           -1, &st, 0) == SQLITE_OK) {
        sqlite3_bind_int(st, 1, id);
        if (sqlite3_step(st) == SQLITE_ROW && sqlite3_column_text(st, 2)) {
            snprintf(al, 600, "%s", sqlite3_column_text(st, 0) ? (const char *)sqlite3_column_text(st, 0) : "");
            snprintf(aa, 600, "%s", sqlite3_column_text(st, 1) ? (const char *)sqlite3_column_text(st, 1) : "");
            snprintf(p, 1100, "%s", (const char *)sqlite3_column_text(st, 2));
            found = 1;
        }
        sqlite3_finalize(st);
    }
    sqlite3_close(db);
    return found;
}

/* every song of album key (its name al) in file's table liked or not; how many changed */
static int album_likes(sqlite3 *likes, const char *file, const char *table, const char *al, unsigned key, int on) {
    sqlite3 *db;
    sqlite3_stmt *st;
    int n = 0;
    char *u = sqlite3_mprintf("select fileurl, album, albumsonger from %s where coalesce(album, '') = ? collate nocase "
                              "order by id", table);
    if (access(file, R_OK) == 0 && sqlite3_open_v2(file, &db, SQLITE_OPEN_READONLY, 0) == SQLITE_OK) {
        if (sqlite3_prepare_v2(db, u, -1, &st, 0) == SQLITE_OK) {
            sqlite3_bind_text(st, 1, al, -1, SQLITE_STATIC);
            while (sqlite3_step(st) == SQLITE_ROW) {
                const char *p = (const char *)sqlite3_column_text(st, 0);
                if (!p || album_key((const char *)sqlite3_column_text(st, 1), (const char *)sqlite3_column_text(st, 2), p) != key)
                    continue;
                if (like_set(likes, p, on)) rated_out_for(p, "like", on), ++n;
            }
            sqlite3_finalize(st);
        }
        sqlite3_close(db);
    }
    sqlite3_free(u);
    return n;
}

/* The FIFO's "album N ID": song ID's album made a favourite (5) or not (0). 0 done, 1 refused (another N, the table
 * full, not written), 3 when ID is no song of the library */
static int album_cmd(int n, int id) {
    char al[600], aa[600], p[1100];
    if (n != 0 && n != 5) return 1;
    if (!album_of(id, al, aa, p)) return 3;
    unsigned key = album_key(al, aa, p);
    int was = table_kept(&rt[1], key), k = table_keep(&rt[1], key, n);
    if (k <= 0) return logf_(k ? "album rating not saved" : "ratings are full"), 1;
    if ((was == 5) != (n == 5)) {
        sqlite3 *likes = likes_open();
        int changed = album_likes(likes, db_path, "songtable", al, key, n == 5) +
                      album_likes(likes, cat_path, "servertable", al, key, n == 5);
        if (likes && changed) fav_flush(likes);
        sqlite3_close(likes);
    }
    lk.key = 0, ++q.seq;
    ratings_copy_due(now_ms());
    return 0;
}

/* ---- play counts (docs/playd.md#play-counts) ----
 * Today's Vamp's (docs/internals.md#play-counts, playback_power.c play_count), in its file: a song over LISTEN_MIN_S
 * counts once half of it, or LISTEN_MAX_S, has been heard, a step of up to 3 s between the player's seconds while it
 * plays (a longer one is a seek, a pause hears nothing); a new song, or back under 2 s from past LISTEN_MIN_S (a
 * repeat, a seek to the start), is a new play. PLAYS_SLOTS key/count pairs, 4 KB, the card's .q2pod/ringnav-plays,
 * read at the first count and written whole: the song counted moves first, and with no free slot the furthest back of
 * the lowest counts is replaced. A song is keyed as its rating is (rated_path); Podcasts, Audiobooks and M4B files
 * are not counted (navigation.c spoken). With no card copy the flash file a release before wrote,
 * /mnt/data/ringnav-plays, is read and copied to the card once, never written; with the card away the counts are
 * kept here and written once it is back (card_store.c's blob). */
#define LISTEN_MIN_S 30
#define LISTEN_MAX_S 240
#define PLAYS_SLOTS 512
#define CARD_LOOK_MS 2000 /* a count kept while the card is away: the card looked for this often */
static const char *plays_card_rel = "/mnt/mmc/.q2pod/ringnav-plays", *plays_flash_rel = "/mnt/data/ringnav-plays";
static struct { unsigned key, n; } plays[PLAYS_SLOTS];
static struct { int read, held, moved; unsigned key; int sec, heard, done; unsigned long long looked; } pc;

static int card_mounted(void) { /* the card's mount line in /proc/mounts, as q2libd reads it */
    char line[600];
    int on = 0;
    FILE *f = fopen(path("/proc/mounts"), "r");
    while (f && !on && fgets(line, sizeof line, f)) on = strstr(line, " /mnt/mmc ") != 0;
    if (f) fclose(f);
    return on;
}

static int plays_card_save(void) {
    char dir[640];
    snprintf(dir, sizeof dir, "%s/mnt/mmc/.q2pod", root);
    mkdir(dir, 0755);
    return blob_save(path(plays_card_rel), plays, sizeof plays);
}

static void plays_load(void) {
    if (pc.read) return;
    pc.read = 1;
    int card = card_mounted(), n = -1;
    FILE *f = card ? fopen(path(plays_card_rel), "rb") : 0;
    if (f) n = (int)fread(plays, 1, sizeof plays, f), fclose(f);
    else if ((f = fopen(path(plays_flash_rel), "rb"))) { /* no card copy yet: the flash file, copied there once */
        n = (int)fread(plays, 1, sizeof plays, f), fclose(f);
        if (n == (int)sizeof plays && card) pc.moved = plays_card_save();
    }
    if (n != (int)sizeof plays) memset(plays, 0, sizeof plays);
}

static void plays_write(void) { /* on the card, or kept for it */
    pc.held = !card_mounted() || !plays_card_save();
    if (!pc.held) pc.moved = 1;
    if (pc.held) logf_("play counts kept for the card");
}

static void play_count(unsigned key) {
    plays_load();
    int i = 0;
    for (int j = 0; j < PLAYS_SLOTS && plays[i].key != key; j++)
        if (plays[j].key == key || plays[j].n <= plays[i].n) i = j;
    unsigned n = plays[i].key == key ? plays[i].n + 1 : 1;
    for (; i > 0; i--) plays[i] = plays[i - 1];
    plays[0].key = key, plays[0].n = n;
    plays_write();
}

static int spoken(const char *p) { /* under the card's Podcasts or Audiobooks, or an M4B */
    const char *ext = strrchr(p, '.'), *in = "/mnt/mmc/";
    if (ext && !strcasecmp(ext, ".m4b")) return 1;
    if (strncmp(p, in, strlen(in))) return 0;
    p += strlen(in);
    return (!strncasecmp(p, "Podcasts", 8) && (!p[8] || p[8] == '/')) || (!strncasecmp(p, "Audiobooks", 10) && (!p[10] || p[10] == '/'));
}

/* The player's second (media-info's) for the song playing, len its length */
static void listen_tick(int sec, int len) {
    const char *p = rated_path();
    if (!p || spoken(q.file)) return;
    unsigned key = listen_key(p);
    if (key != pc.key || (sec < 2 && pc.sec > LISTEN_MIN_S)) pc.key = key, pc.sec = sec, pc.heard = pc.done = 0;
    int d = sec - pc.sec;
    if (d > 0 && d <= 3 && q.state == PLAYING) pc.heard += d;
    pc.sec = sec;
    int listen = len / 2 < LISTEN_MAX_S ? len / 2 : LISTEN_MAX_S;
    if (!pc.done && len > LISTEN_MIN_S && pc.heard >= listen) pc.done = 1, play_count(key);
}

static void plays_flush(unsigned long long t) { /* counts kept while the card was away: written once it is back */
    if (!pc.held || t < pc.looked + CARD_LOOK_MS) return;
    pc.looked = t;
    if (card_mounted() && plays_card_save()) pc.held = 0, pc.moved = 1;
}

/* ---- Resume (docs/playd.md#resume) ----
 * Today's Vamp's long-song places (docs/internals.md#resume, playback_power.c resume_poll), in its file: a song at
 * least [Q2POD] RESUMEMIN minutes long (20 to 120 in fives, 30 by default, 0 Off), or spoken (an M4B, or under the
 * card's Podcasts or Audiobooks) whatever its length, keeps its place by its path's hash (listen_key's with no CUE start)
 * in RESUME_SLOTS key/second pairs, 4 KB, the card's .q2pod/ringnav-resume, the most recent first, written whole: each
 * RESUME_SAVE_S of movement, once the second stands still two reads (a pause) and as another song starts. A place
 * within RESUME_EDGE_S of either end is forgotten. A long song with a place that starts playing under RESUME_START_S
 * seeks there (paused, it waits for Play). A song's first second read only notes it, so the song before's time never
 * counts for it. The flash file of a release before is read until the card has one; with the card away the ring is
 * kept here for it, as the play counts are. */
#define RESUME_SLOTS 512
#define RESUME_EDGE_S 30
#define RESUME_SAVE_S 60
#define RESUME_START_S 5
static const char *resume_card_rel = "/mnt/mmc/.q2pod/ringnav-resume", *resume_flash_rel = "/mnt/data/ringnav-resume";
static struct { unsigned key; int sec; } spots[RESUME_SLOTS];
static struct {
    int read, held, min;
    unsigned key;                            /* the song read, 0 none: its first second then only noted */
    int settle, on, sec, saved, still, pending, total;
    unsigned long long looked;
} rs;

static int resume_min(void) { /* Resume Songs' minutes, 0 Off (crossfade.c resume_min) */
    char *t = read_all(path("/mnt/data/config.ini"));
    int n = t ? ini_get(t, "Q2POD", "RESUMEMIN", 30) : 30;
    free(t);
    return !n ? 0 : n >= 20 && n <= 120 && !(n % 5) ? n : 30;
}

static void spots_load(void) {
    if (rs.read) return;
    rs.read = 1, rs.min = resume_min();
    int card = card_mounted(), n = -1;
    FILE *f = card ? fopen(path(resume_card_rel), "rb") : 0;
    if (!f) f = fopen(path(resume_flash_rel), "rb");
    if (f) n = (int)fread(spots, 1, sizeof spots, f), fclose(f);
    if (n != (int)sizeof spots) memset(spots, 0, sizeof spots);
}

static void spots_write(void) {
    char dir[640];
    snprintf(dir, sizeof dir, "%s/mnt/mmc/.q2pod", root);
    if (card_mounted() && (mkdir(dir, 0755), blob_save(path(resume_card_rel), spots, sizeof spots))) rs.held = 0;
    else rs.held = 1;
}

static int spot_at(unsigned key) {
    for (int i = 0; i < RESUME_SLOTS; ++i)
        if (spots[i].key == key) return spots[i].sec;
    return 0;
}

/* key's place moved first at sec, or forgotten near either end (playback_power.c spot_keep) */
static void spot_keep(unsigned key, int sec, int total) {
    int i = 0, keep = sec >= RESUME_EDGE_S && sec < total - RESUME_EDGE_S;
    while (i < RESUME_SLOTS - 1 && spots[i].key != key) i++;
    if (keep ? !i && spots[0].key == key && spots[0].sec == sec : spots[i].key != key) return;
    if (keep) {
        for (; i > 0; i--) spots[i] = spots[i - 1];
        spots[0].key = key, spots[0].sec = sec;
    } else {
        for (; i < RESUME_SLOTS - 1; i++) spots[i] = spots[i + 1];
        spots[i].key = 0, spots[i].sec = 0;
    }
    spots_write();
}

/* the song read before: its place kept as another starts (start_from) */
static void resume_leave(void) {
    if (rs.key && rs.on && !rs.pending) spot_keep(rs.key, rs.sec, rs.total);
    rs.key = 0;
}

/* The player's second for the song playing (media-info's), len its length */
static void resume_tick(int sec, int len) {
    if (!q.n || !*q.file || len <= 0) return;
    unsigned key = listen_key(q.file);
    if (key != rs.key) {
        resume_leave();
        rs.key = key, rs.on = 0, rs.settle = 1;
        return;
    }
    if (rs.settle) {
        rs.settle = 0;
        spots_load();
        rs.on = (rs.min && len >= rs.min * 60) || spoken(q.file);
        rs.pending = rs.on ? spot_at(key) : 0;
        rs.sec = rs.saved = sec, rs.still = 0;
    }
    rs.total = len;
    if (rs.pending) {
        if (sec < 1 || q.state != PLAYING) return; /* a paused start waits for Play */
        if (sec < RESUME_START_S) {
            char s[16];
            snprintf(s, sizeof s, "%d", rs.pending);
            player_send("mcl-seek", s);
            sec = rs.pending, q.pos_ms = sec * 1000, ++q.seq;
        }
        rs.pending = 0, rs.sec = rs.saved = sec;
        return;
    }
    if (!rs.on) return;
    rs.still = sec == rs.sec ? rs.still + 1 : 0;
    rs.sec = sec;
    int moved = sec - rs.saved;
    if (moved && (rs.still >= 2 || moved >= RESUME_SAVE_S || moved <= -RESUME_SAVE_S)) rs.saved = sec, spot_keep(key, sec, len);
}

static void resume_flush(unsigned long long t) { /* the ring kept while the card was away */
    if (!rs.held || t < rs.looked + CARD_LOOK_MS) return;
    rs.looked = t;
    if (card_mounted() && blob_save(path(resume_card_rel), spots, sizeof spots)) rs.held = 0;
}

/* ---- Memory Playback (docs/playd.md#memory-playback) ----
 * Today's Vamp's queue snapshot (playback.c playback_save, docs/internals.md#memory-playback-and-a-streamed-queue), in
 * its format, so either system brings back what the other kept: the card's .q2pod/ringnav-queue, version 4 (a queue
 * without advanced play): the header's words, the queue's order twice and n zeros, then each song's identity (its
 * path's length with the NUL, CUE start and end) and path; the checksum FNV-1a over the whole with its own word 0. The
 * second goes in ringnav-queue-elapsed beside it (the checkpoint: its checksum, the snapshot's, the place, the second)
 * at a pause or a stop and every CHECKPOINT_MS playing, and the snapshot is written again when the queue or its place
 * changes. Kept only while [PLAYSET] MEMORYPLAY (1 Track, 2 Location) or [SYSSET] CARMODE is on, for a queue of
 * library songs or an app's answer (its songs on the card by path, the rest in the tail beside: tail_write), not a
 * source's URL, at most SAVE_LIMIT songs (past it the kept one is dropped). At the start the queue comes back paused, at its second for Location and from the song's start for Track;
 * in car mode playing at its second, once the player connects (memeory_startplayer, ringnav_boot). A snapshot with
 * advanced play (version 3, or 2) comes back as its queue's rows; a song no longer in the library is left out; one
 * holding a Stream Cache copy comes back as the paths whose files are there, and its app is asked for the rest
 * (stream_ask). With no
 * card copy the flash files of a release before, /mnt/data/ringnav-queue and its -elapsed, are read. */
#define SAVE_LIMIT 999
#define SNAP_MAGIC 0x5132524eu
#define CHECKPOINT_MS 30000
#define MEMORY_LOOK_MS 500
#define TAIL_MAGIC 0x51325254u
static const char *snap_card_rel = "/mnt/mmc/.q2pod/ringnav-queue", *snap_flash_rel = "/mnt/data/ringnav-queue",
                  *tail_card_rel = "/mnt/mmc/.q2pod/ringnav-queue-stream";
static struct {
    int mode;                /* 0 off, 1 Track, 2 Location, 3 car mode */
    long long stamp;         /* config.ini's, when mode was read */
    unsigned sig, sum;       /* the queue's as kept (queue_sig), the kept snapshot's checksum */
    int at, sec, go;         /* the place and second kept; go: car mode's second + 1, started once the player is there */
    unsigned long long looked, at_ms;
} mp = {.at = -1, .sec = -1};

static void memory_mode(void) {
    struct stat st;
    long long stamp = stat(path("/mnt/data/config.ini"), &st) ? 0 : (long long)st.st_mtime * 1000000000 + st.st_size;
    if (stamp == mp.stamp) return;
    mp.stamp = stamp;
    char *t = read_all(path("/mnt/data/config.ini"));
    int m = t ? ini_get(t, "PLAYSET", "MEMORYPLAY", 0) : 0, car = t ? ini_get(t, "SYSSET", "CARMODE", 0) : 0;
    free(t);
    mp.mode = car == 1 ? 3 : m == 1 || m == 2 ? m : 0;
}

/* A streamed queue's songs not on the card at a restore, asked of their app (stream_ask, docs/playd.md#a-streamed-queue-
 * at-a-restore): the request until it is written, the saved songs after the current one not restored (each with its
 * NUL), the seq, the app, the queue's list counter then and when the step began */
enum { SR_NONE, SR_ASK, SR_SENT };
static struct {
    int state, woke;
    char app[64], *req, *rows;
    size_t size, rows_len;
    unsigned seq, list, nrows;
    unsigned long long at;
} sr;

/* The songs still to come of a stream, for the tail: while the queue is an app's answer, its m3u's entries from the one
 * waited for on (card paths, as m3u_read takes them); while a restore's ask waits, the saved ones not restored. Each
 * with its NUL, at most room of them; 0 with none. */
static char *stream_tail(unsigned room, unsigned *k, size_t *bytes) {
    char *out = 0, line[1100];
    size_t len = 0;
    *k = 0, *bytes = 0;
    if (sr.state && sr.rows_len) {
        if (!(out = malloc(sr.rows_len))) return 0;
        memcpy(out, sr.rows, sr.rows_len);
        for (const char *p = out; p < out + sr.rows_len && *k < room; p += strlen(p) + 1) ++*k, len = (size_t)(p - out) + strlen(p) + 1;
        *bytes = len;
        return out;
    }
    FILE *f = mq.on ? fopen(path(mq.file), "r") : 0;
    for (int entry = 0; f && *k < room && fgets(line, sizeof line, f);) {
        char *l = line;
        if (!strncmp(l, "\xEF\xBB\xBF", 3)) l += 3;
        l[strcspn(l, "\r\n")] = 0;
        if (!*l || *l == '#' || entry++ < mq.next || !card_path(l)) continue;
        size_t n = strlen(l) + 1;
        char *g = realloc(out, len + n);
        if (!g) break;
        out = g, memcpy(out + len, l, n), len += n, ++*k;
    }
    if (f) fclose(f);
    if (!*k) free(out), out = 0;
    *bytes = len;
    return out;
}
static unsigned queue_sig(void) { /* the ids in play order, or the paths and the tail kept beside them */
    unsigned h = fnv(2166136261u, &q.n, sizeof q.n);
    for (int i = 0; i < q.n; ++i)
        h = q.paths ? fnv(h, q.paths[i], strlen(q.paths[i]) + 1) : fnv(h, &q.ids[q.order[i]], sizeof *q.ids);
    if (q.paths) {
        unsigned k;
        size_t bytes;
        char *tail = stream_tail(SAVE_LIMIT, &k, &bytes);
        if (tail) h = fnv(h, tail, bytes), free(tail);
    }
    return h;
}

/* id's path (a card song's, else a server row's fileurl) through the two statements */
static int path_of(sqlite3_stmt *card, sqlite3_stmt *cat, int id, char *out, size_t room) {
    sqlite3_stmt *st = (unsigned)id >= CAT_ID_BASE ? cat : card;
    int ok = 0;
    if (!st) return 0;
    sqlite3_reset(st);
    sqlite3_bind_int(st, 1, id);
    if (sqlite3_step(st) == SQLITE_ROW && sqlite3_column_text(st, 0))
        ok = snprintf(out, room, "%s", (const char *)sqlite3_column_text(st, 0)) < (int)room;
    return ok && *out;
}

static int snap_save(const char *rel, const void *buf, size_t n) {
    char dir[640];
    snprintf(dir, sizeof dir, "%s/mnt/mmc/.q2pod", root);
    mkdir(dir, 0755);
    return blob_save(path(rel), buf, n);
}

static void checkpoint_write(int sec) {
    char rel[64];
    unsigned c[4] = {0, mp.sum, (unsigned)q.at, (unsigned)sec};
    c[0] = fnv(2166136261u, c + 1, 3 * sizeof *c);
    snprintf(rel, sizeof rel, "%s-elapsed", snap_card_rel);
    if (snap_save(rel, c, sizeof c)) mp.sec = sec;
}

static void snap_drop(void) {
    char rel[64];
    snprintf(rel, sizeof rel, "%s-elapsed", snap_card_rel);
    unlink(path(snap_card_rel)), unlink(path(rel));
}

/* The tail beside the snapshot whose checksum is sum (playback.c save_tail): TAIL_MAGIC, a checksum of what follows,
 * sum, the count, then the paths of the songs still to come of a stream (stream_tail), each with its NUL, at most
 * SAVE_LIMIT with the queue's n. None: the file goes. */
static void tail_write(unsigned sum, unsigned n) {
    unsigned k = 0;
    size_t bytes = 0;
    char *paths = q.paths ? stream_tail(SAVE_LIMIT - n, &k, &bytes) : 0;
    unsigned *buf = paths ? malloc(16 + bytes) : 0;
    if (!buf) {
        free(paths);
        unlink(path(tail_card_rel));
        return;
    }
    buf[0] = TAIL_MAGIC, buf[2] = sum, buf[3] = k;
    memcpy(buf + 4, paths, bytes);
    buf[1] = fnv(2166136261u, buf + 2, 8 + bytes);
    snap_save(tail_card_rel, buf, 16 + bytes);
    free(buf), free(paths);
}

/* the queue as a version 4 snapshot: 1 when the card took it */
static int snap_write(int sec) {
    sqlite3 *db = 0, *cat = 0;
    sqlite3_stmt *a = 0, *b = 0;
    if (sqlite3_open_v2(db_path, &db, SQLITE_OPEN_READONLY, 0) == SQLITE_OK)
        sqlite3_prepare_v2(db, "select fileurl from songtable where id = ?", -1, &a, 0);
    if (sqlite3_open_v2(cat_path, &cat, SQLITE_OPEN_READONLY, 0) == SQLITE_OK)
        sqlite3_prepare_v2(cat, "select fileurl from servertable where id = ?", -1, &b, 0);
    unsigned n = (unsigned)q.n, size = 56 + n * 12, room = size + n * 1036, len;
    unsigned char *buf = malloc(room), *p = buf ? buf + size : 0;
    int ok = buf != 0;
    char song[1024];
    for (unsigned i = 0; ok && i < n; ++i) {
        ok = q.paths ? snprintf(song, sizeof song, "%s", q.paths[i]) < (int)sizeof song
                     : path_of(a, b, q.ids[q.order[i]], song, sizeof song);
        unsigned id3[3] = {(len = (unsigned)strlen(song)) + 1, 0, 0};
        if (ok) memcpy(p, id3, sizeof id3), memcpy(p + 12, song, len + 1), p += 12 + len + 1;
    }
    sqlite3_finalize(a), sqlite3_finalize(b), sqlite3_close(db), sqlite3_close(cat);
    if (ok) {
        size = (unsigned)(p - buf);
        unsigned h[14] = {SNAP_MAGIC, 4, size, 0, n, (unsigned)q.at, (unsigned)sec, 0, 2, 0, (unsigned)q.at, 0, 0, 0xf001};
        memcpy(buf, h, sizeof h);
        for (unsigned i = 0; i < n * 3; ++i) {
            unsigned v = i < n * 2 ? i % n : 0;
            memcpy(buf + 56 + i * 4, &v, 4);
        }
        unsigned sum = fnv(2166136261u, buf, size);
        memcpy(buf + 12, &sum, 4);
        ok = snap_save(snap_card_rel, buf, size);
        if (ok) mp.sum = sum, mp.sec = sec, tail_write(sum, n);
    }
    free(buf);
    return ok;
}

/* the queue kept as it changes (the main loop's, and at the end) */
static void memory_poll(unsigned long long t, int last) {
    if (!last && t < mp.looked + MEMORY_LOOK_MS) return;
    mp.looked = t;
    memory_mode();
    if (!mp.mode || src.on || !q.n || (q.paths && q.np != q.n)) return;
    unsigned sig = queue_sig();
    int sec = q.pos_ms / 1000;
    if (sig == mp.sig && q.at == mp.at) {
        if (sec != mp.sec && (last || q.state != PLAYING || t >= mp.at_ms + CHECKPOINT_MS) && card_mounted())
            checkpoint_write(sec), mp.at_ms = t;
        return;
    }
    if (!card_mounted()) return;
    if (q.n > SAVE_LIMIT) snap_drop(), mp.sig = sig, mp.at = q.at;
    else if (snap_write(sec)) mp.sig = sig, mp.at = q.at, mp.at_ms = t;
}

/* rel read whole (at most room bytes): its length, -1 for none; the card's, else the flash's */
static long snap_read(const char *card_rel, const char *flash_rel, unsigned char *buf, size_t room) {
    FILE *f = card_mounted() ? fopen(path(card_rel), "rb") : 0;
    if (!f) f = fopen(path(flash_rel), "rb");
    if (!f) return -1;
    size_t n = fread(buf, 1, room, f);
    fclose(f);
    return (long)n;
}

/* ---- a streamed queue at a restore (docs/playd.md#a-streamed-queue-at-a-restore) ----
 * Today's Vamp's ask (patch/addon.c addon_resume_ask, docs/internals.md#memory-playback-and-a-streamed-queue): Memory
 * Playback brings back only the songs whose files are on the card, so a stream's others are asked of the app whose
 * folder holds a saved song, when its q2app says resume=1 and a song after the current one was not restored. Once the
 * app listens (started through q2pkgd as for a catplay) /tmp/q2-<id>.resume is written whole: "<seq> <index> <seconds>
 * paused|playing", then every saved song's path a line, the snapshot's then the tail's, index the current song's among
 * them. The app answers resumeack=<seq> <code> in its state file (0 resumed, 1 not its songs, 2 it cannot) and a .play
 * of that seq whose m3u starts with the song restored: that song stays where it is (paused stays paused, nothing
 * loaded) and the m3u's songs after it are the queue's rest, as any answer grows. The app not listening within
 * SR_START_MS, no .play within SR_WAIT_MS of the request, a code other than 0, an answer starting with another song or
 * another queue played first: the songs on the card are the queue, and the log says why. */
#define SR_START_MS 30000
#define SR_WAIT_MS 30000

/* path's app and sid, a Stream Cache copy's or a server row's (/mnt/mmc/.<id>/cat/<album>/<sid>.<suffix>) */
static int song_sid(const char *p, char *app, size_t an, char *sid, size_t sn) {
    if (stream_sid(p, app, an, sid, sn)) return 1;
    const char *base = strrchr(p, '/'), *dot = base ? strrchr(base, '.') : 0;
    if (app_of(p, app, an) || !base || !dot || dot == base + 1) return 0;
    snprintf(sid, sn, "%.*s", (int)(dot - base - 1), base + 1);
    return 1;
}
static int same_song(const char *a, const char *b) { /* catalogue.c catalogue_same_song */
    char a1[64], s1[128], a2[64], s2[128];
    return !strcmp(a, b) || (song_sid(a, a1, sizeof a1, s1, sizeof s1) && song_sid(b, a2, sizeof a2, s2, sizeof s2) &&
                             !strcmp(a1, a2) && !strcmp(s1, s2));
}

/* the app id in a card path /mnt/mmc/.<id>/..., 1 to 15 of a-z0-9- (patch/addon.c card_id) */
static int card_app(const char *p, char *id, size_t room) {
    const char *pre = "/mnt/mmc/.", *s = p + strlen(pre), *e;
    if (strncmp(p, pre, strlen(pre)) || !(e = strchr(s, '/')) || e == s || e - s > 15 || (size_t)(e - s) >= room) return 0;
    for (const char *c = s; c < e; ++c)
        if (!(*c >= 'a' && *c <= 'z') && !(*c >= '0' && *c <= '9') && *c != '-') return 0;
    snprintf(id, room, "%.*s", (int)(e - s), s);
    return 1;
}

static int resumes(const char *app) { /* its q2app says resume=1 */
    char f[400];
    snprintf(f, sizeof f, "%s/mnt/mmc/.%s/q2app", root, app);
    char *m = read_all(f);
    const char *r = m ? strstr(m, "resume=") : 0;
    int on = r && (r == m || r[-1] == '\n') && atoi(r + 7) == 1;
    free(m);
    return on;
}

static void stream_end(const char *why) {
    if (sr.state && why) logf_("resume: %s: %s; the restored songs on the card play as they are", sr.app, why);
    else if (sr.state) logf_("resume: %s resumed the stream, %u songs to come", sr.app, sr.nrows);
    free(sr.req), free(sr.rows);
    sr.req = sr.rows = 0, sr.size = sr.rows_len = 0, sr.nrows = 0, sr.state = SR_NONE;
}

/* The restored stream's ask: saved, n paths in the snapshot's order, in[i] whether path i was restored, at the index
 * of the song made current, sec its second; the tail beside the snapshot whose checksum is sum adds its paths. */
static void stream_ask(const char *const *saved, const unsigned char *in, unsigned n, unsigned at, int sec, unsigned sum) {
    size_t room = 16 + (size_t)SAVE_LIMIT * 1025;
    unsigned *tail = card_mounted() ? malloc(room) : 0, k = 0;
    FILE *f = tail ? fopen(path(tail_card_rel), "rb") : 0;
    size_t got = f ? fread(tail, 1, room, f) : 0;
    if (f) fclose(f);
    if (got < 16 || tail[0] != TAIL_MAGIC || tail[2] != sum || tail[3] > SAVE_LIMIT ||
        fnv(2166136261u, tail + 2, got - 8) != tail[1] || ((char *)tail)[got - 1])
        got = 0;
    for (const char *p = (char *)(tail + 4), *e = (char *)tail + got; got && p < e; p += strlen(p) + 1) ++k;
    if (got && k != tail[3]) got = 0, k = 0;
    if (!got) k = 0;
    char app[64] = "", id[64];
    unsigned missing = 0;
    size_t bytes = 64, rows = 0;
    const char *tp = (const char *)(tail + 4);
    for (unsigned i = 0; i < n + k; ++i) {
        const char *p = i < n ? saved[i] : tp;
        if (i >= n) tp += strlen(tp) + 1;
        if (strchr(p, '\n') || strchr(p, '\r')) { free(tail); return; } /* a line each */
        bytes += strlen(p) + 1;
        if (i > at && (i >= n || !in[i])) ++missing, rows += strlen(p) + 1;
        if (!*app && card_app(p, id, sizeof id) && resumes(id)) snprintf(app, sizeof app, "%s", id);
    }
    stream_end("another restore");
    if (!*app || !missing || at >= n || !(sr.req = malloc(bytes)) || !(sr.rows = malloc(rows))) {
        free(sr.req), sr.req = 0, free(tail);
        return;
    }
    sr.seq = (unsigned)now_ms() | 1;
    size_t len = (size_t)snprintf(sr.req, 64, "%u %u %d %s\n", sr.seq, at, sec, mp.mode == 3 ? "playing" : "paused"), r = 0;
    tp = (const char *)(tail + 4);
    for (unsigned i = 0; i < n + k; ++i) {
        const char *p = i < n ? saved[i] : tp;
        size_t l = strlen(p);
        if (i >= n) tp += l + 1;
        memcpy(sr.req + len, p, l), len += l, sr.req[len++] = '\n';
        if (i > at && (i >= n || !in[i])) memcpy(sr.rows + r, p, l + 1), r += l + 1;
    }
    snprintf(sr.app, sizeof sr.app, "%s", app);
    sr.size = len, sr.rows_len = r, sr.nrows = missing, sr.state = SR_ASK, sr.woke = 0, sr.at = now_ms();
    logf_("resume: asking %s to resume %u songs, %u not on the card", app, n + k, missing);
    free(tail);
}

/* the app's answer taken: the song playing kept at its place, the m3u's songs after its first the queue's rest */
static void stream_take(unsigned seq, const char *m3u) {
    FILE *f = fopen(path(m3u), "r");
    char line[1100], first[1100] = "";
    while (f && !*first && fgets(line, sizeof line, f)) {
        char *l = line;
        if (!strncmp(l, "\xEF\xBB\xBF", 3)) l += 3;
        l[strcspn(l, "\r\n")] = 0;
        if (*l && *l != '#') snprintf(first, sizeof first, "%s", l);
    }
    if (f) fclose(f);
    if (!q.paths || q.at >= q.np || strlen(m3u) >= sizeof mq.file || !same_song(first, q.paths[q.at])) {
        stream_end("its queue starts with another song");
        return;
    }
    char *keep = q.paths[q.at];
    gapless_forget();
    for (int i = 0; i < q.np; ++i)
        if (i != q.at) free(q.paths[i]);
    q.paths[0] = keep, q.np = q.n = 1, q.at = 0;
    mq.on = 1, mq.next = 1, mq.waiting = 0, mq.seq = seq;
    snprintf(mq.app, sizeof mq.app, "%s", sr.app);
    memcpy(mq.file, m3u, strlen(m3u) + 1);
    m3u_read();
    ++q.seq;
    stream_end(0);
}

static void stream_poll(void) {
    if (!sr.state) return;
    unsigned long long t = now_ms();
    if (q.list != sr.list) {
        stream_end("another queue played");
        return;
    }
    if (sr.state == SR_ASK) {
        if (!sr.woke && (sr.woke = 1) && !listening(sr.app) && pkg_start(sr.app)) {
            stream_end("the app cannot start");
            return;
        }
        if (listening(sr.app)) {
            char tmp[300], dst[300];
            snprintf(tmp, sizeof tmp, "/tmp/q2-%s.resume.q2playd", sr.app), snprintf(dst, sizeof dst, "/tmp/q2-%s.resume", sr.app);
            FILE *f = fopen(path(tmp), "w");
            int ok = f && fwrite(sr.req, 1, sr.size, f) == sr.size;
            if (f && fclose(f)) ok = 0;
            if (!ok || rename(path(tmp), path(dst))) {
                unlink(path(tmp));
                stream_end("the request could not be written");
                return;
            }
            free(sr.req), sr.req = 0, sr.state = SR_SENT, sr.at = t;
        } else if (t >= sr.at + SR_START_MS)
            stream_end("the app did not start");
        return;
    }
    char p[300], s[600], m3u[300]; /* mq.file's room */
    unsigned seq;
    int code;
    snprintf(p, sizeof p, "/tmp/q2-%s.state", sr.app);
    read_small(path(p), s, sizeof s);
    for (char *l = s; l; l = strchr(l, '\n') ? strchr(l, '\n') + 1 : 0)
        if (sscanf(l, "resumeack=%u %d", &seq, &code) == 2 && seq == sr.seq && code) {
            char why[40];
            snprintf(why, sizeof why, "the app declined (%d)", code);
            stream_end(why);
            return;
        }
    snprintf(p, sizeof p, "/tmp/q2-%s.play", sr.app);
    read_small(path(p), s, sizeof s);
    if (sscanf(s, "%u %299[^\n]", &seq, m3u) == 2 && seq == sr.seq) {
        stream_take(seq, m3u);
        return;
    }
    if (t >= sr.at + SR_WAIT_MS) stream_end("no queue from the app");
}

static void memory_restore(void) {
    memory_mode();
    if (!mp.mode) return;
    size_t room = 60 + (size_t)SAVE_LIMIT * 4 * 3 + 64 * 4 * 2 + (size_t)SAVE_LIMIT * 1036 + 1;
    unsigned char *buf = malloc(room);
    long got = buf ? snap_read(snap_card_rel, snap_flash_rel, buf, room) : -1;
    unsigned h[15] = {0}, hsize = 0;
    int ok = got >= 56;
    if (ok) memcpy(h, buf, 56), hsize = h[1] == 2 ? 60 : 56;
    unsigned n = h[4], pos = h[5], hn = h[11], npend = h[1] == 2 ? 0 : h[12];
    ok = ok && h[0] == SNAP_MAGIC && h[1] >= 2 && h[1] <= 4 && (long)h[2] == got && got >= (long)hsize && n && n <= SAVE_LIMIT &&
         pos < n && hn <= 4 * SAVE_LIMIT && npend <= 4 * SAVE_LIMIT && h[6] <= 0x7fffffff &&
         h[2] >= hsize + (n * 3 + hn + npend) * 4 + n * 14;
    if (ok) {
        unsigned sum = h[3], zero = 0;
        memcpy(buf + 12, &zero, 4);
        ok = fnv(2166136261u, buf, h[2]) == sum;
        memcpy(buf + 12, &sum, 4);
    }
    unsigned char *p = buf + hsize + (n * 3 + hn + npend) * 4, *end = buf + (ok ? h[2] : 0);
    for (unsigned i = 0; ok && i < n * 3; ++i) {
        unsigned v;
        memcpy(&v, buf + hsize + i * 4, 4);
        if (i >= n * 2 ? v > 1 : v >= n) ok = 0;
    }
    int at = -1, kept = 0, streamed = 0, *sid = ok ? malloc(n * sizeof *sid) : 0;
    const char **saved = ok ? malloc(n * sizeof *saved) : 0;
    char app[64], sd[128];
    if (ok && (!sid || !saved)) ok = 0;
    sqlite3 *db = 0, *cat = 0;
    sqlite3_stmt *a = 0, *b = 0;
    if (ok && sqlite3_open_v2(db_path, &db, SQLITE_OPEN_READONLY, 0) == SQLITE_OK)
        sqlite3_prepare_v2(db, "select id from songtable where fileurl = ?", -1, &a, 0);
    if (ok && sqlite3_open_v2(cat_path, &cat, SQLITE_OPEN_READONLY, 0) == SQLITE_OK)
        sqlite3_prepare_v2(cat, "select id from servertable where fileurl = ?", -1, &b, 0);
    for (unsigned i = 0; ok && i < n; ++i) {
        unsigned id3[3];
        if (end - p < 12) { ok = 0; break; }
        memcpy(id3, p, 12), p += 12;
        if (id3[0] < 2 || id3[0] > 1024 || id3[0] > (unsigned)(end - p) || p[id3[0] - 1] || strlen((char *)p) != id3[0] - 1) {
            ok = 0;
            break;
        }
        int id = -1;
        for (sqlite3_stmt *st = a; id < 0 && st; st = st == a ? b : 0) {
            sqlite3_reset(st);
            sqlite3_bind_text(st, 1, (const char *)p, -1, SQLITE_STATIC);
            if (sqlite3_step(st) == SQLITE_ROW) id = sqlite3_column_int(st, 0);
        }
        saved[i] = (const char *)p, sid[i] = id;
        streamed |= stream_sid((const char *)p, app, sizeof app, sd, sizeof sd);
        p += id3[0];
    }
    sqlite3_finalize(a), sqlite3_finalize(b), sqlite3_close(db), sqlite3_close(cat);
    if (ok && p != end) ok = 0;
    /* A stream's queue (a Stream Cache copy in it: an app's answer) comes back as the paths whose files are there, as
     * it played; any other as its library rows */
    unsigned char *in = ok ? calloc(n, 1) : 0;
    char **paths = ok && streamed ? calloc(n, sizeof *paths) : 0;
    int ask_at = -1;
    if (ok && (!in || (streamed && !paths))) ok = 0;
    for (unsigned i = 0; ok && i < n; ++i) {
        int here = streamed ? !access(path(saved[i]), F_OK) && card_path(saved[i]) : sid[i] >= 0;
        if (!here) continue;
        if (i == pos || (i > pos && at < 0)) at = kept, ask_at = (int)i; /* the place's song, else the next kept */
        if (streamed && !(paths[kept] = strdup(saved[i]))) { ok = 0; break; }
        if (!streamed) q.ids[kept] = sid[i], q.order[kept] = kept;
        in[i] = 1, ++kept;
    }
    if (ok && kept && at < 0) {
        at = kept - 1;
        for (int i = (int)n - 1; i >= 0 && ask_at < 0; --i)
            if (in[i]) ask_at = i;
    }
    unsigned c[4];
    char rel[64], frel[64];
    snprintf(rel, sizeof rel, "%s-elapsed", snap_card_rel), snprintf(frel, sizeof frel, "%s-elapsed", snap_flash_rel);
    int sec = ok ? (int)h[6] : 0;
    if (ok && snap_read(rel, frel, (unsigned char *)c, sizeof c) == (long)sizeof c && c[1] == h[3] && c[2] == pos &&
        c[3] <= 0x7fffffff && fnv(2166136261u, c + 1, 3 * sizeof *c) == c[0])
        sec = (int)c[3];
    if (mp.mode == 1) sec = 0;
    if (ok && kept && streamed) stream_ask(saved, in, n, (unsigned)ask_at, sec, h[3]);
    free(buf), free(sid), free(saved), free(in);
    if (!ok || !kept) {
        for (int i = 0; paths && i < kept; ++i) free(paths[i]);
        free(paths);
        if (got >= 0) logf_("the kept queue is not read: %s", ok ? "none of its songs is in the library" : "damaged");
        return;
    }
    if (streamed) q.paths = paths, q.np = kept;
    q.n = kept, q.at = at, q.shuffle = 0, q.errors = 0, q.loaded = 0, ++q.list, ++q.seq;
    if (streamed) path_lookup(q.at), set_cover(-1, *q.row_url ? q.row_url : q.file);
    else song_lookup(q.ids[q.at]), set_cover(q.ids[q.at], *q.row_url ? q.row_url : q.file);
    q.state = PAUSED, q.pos_ms = sec * 1000;
    if (mp.mode == 3) mp.go = sec + 1; /* playing once the player is there */
    if (sr.state) sr.list = q.list;
    mp.sig = queue_sig(), mp.at = q.at, mp.sum = h[3], mp.sec = sec;
    logf_("the kept queue: %d songs, song %d at %d s", kept, q.at + 1, sec);
}

static int gapless_on(void) { /* [PLAYSET] GAPLESS, read again when config.ini changes */
    static long long stamp = -2;
    static int on;
    long long st = file_stamp(path("/mnt/data/config.ini"));
    if (st != stamp) {
        char *text = read_all(path("/mnt/data/config.ini"));
        on = text ? ini_get(text, "PLAYSET", "GAPLESS", 0) == 1 : 0;
        free(text), stamp = st;
    }
    return on;
}

static int dsd(const char *p) {
    const char *dot = strrchr(p, '.');
    return dot && (!strcasecmp(dot, ".dsf") || !strcasecmp(dot, ".dff") || !strcasecmp(dot, ".iso"));
}

/* the play order's index after the song playing, as the end would take it: the same song for Repeat One, the first
 * again for Repeat All unshuffled (a shuffle is drawn again there), -1 for none known yet */
static int gapless_next(void) {
    if (pm.mode == 1) return q.at;
    if (q.at + 1 < q.n) return q.at + 1;
    return !q.paths && !q.shuffle && pm.mode == 3 && q.n ? 0 : -1;
}

/* index at's file into out, q's own fields as they were */
static int peek_file(int at, char *out, size_t room) {
    char title[sizeof q.title], artist[sizeof q.artist], album[sizeof q.album], file[sizeof q.file], row[sizeof q.row_url];
    memcpy(title, q.title, sizeof title), memcpy(artist, q.artist, sizeof artist), memcpy(album, q.album, sizeof album);
    memcpy(file, q.file, sizeof file), memcpy(row, q.row_url, sizeof row);
    int ok = q.paths ? path_lookup(at) : song_lookup(q.ids[q.order[at]]);
    if (ok) snprintf(out, room, "%s", q.file);
    memcpy(q.title, title, sizeof title), memcpy(q.artist, artist, sizeof artist), memcpy(q.album, album, sizeof album);
    memcpy(q.file, file, sizeof file), memcpy(q.row_url, row, sizeof row);
    return ok;
}

static void gapless_tick(int sec, int len) {
    char next[sizeof q.file];
    int at;
    if (gl.sent || src.on || !q.loaded || q.state != PLAYING || len < GAPLESS_MIN_S || sec < 0 || len - sec >= GAPLESS_AHEAD_S ||
        !gapless_on() || (at = gapless_next()) < 0 || dsd(q.file) || !peek_file(at, next, sizeof next) || dsd(next))
        return;
    send_song("set-gapless", next, 0);
    gl.sent = 1, gl.at = at;
    logf_("next song sent ahead: %s", next);
}

/* the end of a song whose next was sent ahead: that one plays, nothing sent; 1 when it was */
static int gapless_took(void) {
    if (!gl.sent) return 0;
    gl.sent = 0;
    resume_leave();
    q.at = gl.at, q.errors = 0;
    if (q.paths ? path_lookup(q.at) : song_lookup(q.ids[q.order[q.at]]))
        set_cover(q.paths ? -1 : q.ids[q.order[q.at]], q.paths || *q.row_url ? q.row_url : q.file);
    q.state = PLAYING, q.pos_ms = 0, q.len_s = 0;
    ++q.seq;
    return 1;
}

/* ---- the player's shared words: Mono Audio, Swap L/R and Smart Crossfade (docs/playd.md#mono-swap-and-crossfade) ----
 * As today's Vamp's crossfade.c: an int in a file of its own under /tmp, which q2player's chain maps read-only once it
 * appears (player/dsp.c watch) and reads as it plays, so a change is heard at once. Each file is made once (written
 * whole beside and renamed, as shared_int does) and from then on changed in place, never replaced, since the player
 * keeps the mapping it has; one left by a q2playd before is taken as it is. [Q2POD] MONO and SWAPLR, 1 on, are read
 * again when config.ini changes; a file that is not there reads as off, so none is made for a setting that is. */
#define XF_FALLBACK_MS 5000 /* the plain fade a smart one falls back to (docs/internals.md#smart-crossfade) */
#define XF_SMART 0x8000
#define XF_STOPS 16
static volatile int *shared_word(const char *rel) {
    char p[700], tmp[720];
    snprintf(p, sizeof p, "%s", path(rel)), snprintf(tmp, sizeof tmp, "%s.tmp", p);
    int fd = open(p, O_RDWR | O_CLOEXEC), made = 0;
    if (fd < 0) {
        int zero = 0;
        fd = open(tmp, O_RDWR | O_CREAT | O_TRUNC | O_CLOEXEC, 0644);
        if (fd < 0 || write(fd, &zero, sizeof zero) != sizeof zero) {
            if (fd >= 0) close(fd), unlink(tmp);
            return 0;
        }
        made = 1;
    }
    void *m = mmap(0, sizeof(int), PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
    close(fd);
    if (m != MAP_FAILED && made && rename(tmp, p)) munmap(m, sizeof(int)), m = MAP_FAILED;
    if (m == MAP_FAILED && made) unlink(tmp);
    return m == MAP_FAILED ? 0 : m;
}

static struct {
    volatile int *mono, *swap, *fade;
    long long stamp;
    int mono_on, swap_on, fade_on, stops, was_stopped, done;
    long long key[8];
} xf = {.stamp = -2};

static void words_settings(void) {
    long long st = file_stamp(path("/mnt/data/config.ini"));
    if (st == xf.stamp) return;
    char *text = read_all(path("/mnt/data/config.ini"));
    xf.mono_on = text ? ini_get(text, "Q2POD", "MONO", 0) == 1 : 0;
    xf.swap_on = text ? ini_get(text, "Q2POD", "SWAPLR", 0) == 1 : 0;
    xf.fade_on = text ? ini_get(text, "Q2POD", "XFADE", 0) == 1 : 0;
    free(text), xf.stamp = st, xf.done = 0;
    if (!xf.mono && xf.mono_on) xf.mono = shared_word("/tmp/q2mono");
    if (xf.mono) *xf.mono = xf.mono_on;
    if (!xf.swap && xf.swap_on) xf.swap = shared_word("/tmp/q2swap");
    if (xf.swap) *xf.swap = xf.swap_on;
}

/* the album key of play order index at (album_key: the library row's, else what path_lookup finds), 0 for none */
static unsigned album_at(int at) {
    char al[600], aa[600], p[1100];
    if (!q.paths) return album_of(q.ids[q.order[at]], al, aa, p) ? album_key(al, aa, p) : 0;
    char title[sizeof q.title], artist[sizeof q.artist], album[sizeof q.album], row[sizeof q.row_url], f0[sizeof q.file];
    memcpy(title, q.title, sizeof title), memcpy(artist, q.artist, sizeof artist), memcpy(album, q.album, sizeof album);
    memcpy(row, q.row_url, sizeof row), memcpy(f0, q.file, sizeof f0);
    path_lookup(at);
    unsigned k = album_key(q.album, "", *q.row_url ? q.row_url : q.file);
    memcpy(q.title, title, sizeof title), memcpy(q.artist, artist, sizeof artist), memcpy(q.album, album, sizeof album);
    memcpy(q.row_url, row, sizeof row), memcpy(q.file, f0, sizeof f0);
    return k;
}

/* /tmp/q2xfade.next: the playing song's path and the next one's, a line each, written whole and renamed */
static void xfade_next_write(const char *cur, const char *next) {
    char tmp[720], dst[700];
    snprintf(dst, sizeof dst, "%s", path("/tmp/q2xfade.next")), snprintf(tmp, sizeof tmp, "%s.tmp", dst);
    FILE *f = fopen(tmp, "w");
    if (!f) return;
    fprintf(f, "%s\n%s\n", cur, next);
    if (fclose(f) || rename(tmp, dst)) unlink(tmp);
}

/* Each time round the loop (crossfade.c xfade_poll): the next change's fade in /tmp/q2xfade, XF_FALLBACK_MS with
 * XF_SMART (the player plans the overlap from both songs and falls back to that plain fade), only with Smart
 * Crossfade and Gapless on and the next song another album's (a shuffle's every next counts as one), never into a
 * DSD file or one not there; the two paths in /tmp/q2xfade.next. Each stop moves the count above XF_STOPS, so no
 * held tail outlives a stop. Worked out again only when what it rests on changes. */
static void words_poll(void) {
    words_settings();
    int stopped = q.state == STOPPED;
    if (stopped && !xf.was_stopped) ++xf.stops;
    xf.was_stopped = stopped;
    int at = q.n && q.loaded && !src.on ? gapless_next() : -1;
    long long key[8] = {xf.fade_on, gapless_on(), q.list, q.at, at, q.np, xf.stops, q.shuffle};
    if (xf.done && !memcmp(key, xf.key, sizeof key)) return;
    memcpy(xf.key, key, sizeof key);
    char cur[sizeof q.file], next[sizeof q.file], there[sizeof root + sizeof q.file];
    int ms = 0;
    if (xf.fade_on && gapless_on() && at >= 0 && at != q.at && peek_file(q.at, cur, sizeof cur) &&
        peek_file(at, next, sizeof next) && !dsd(next) &&
        snprintf(there, sizeof there, "%s%s", root, next) < (int)sizeof there && !access(there, F_OK) &&
        (q.shuffle || album_at(q.at) != album_at(at)))
        ms = XF_FALLBACK_MS | XF_SMART;
    xf.done = 0;
    if (!xf.fade && ms && !(xf.fade = shared_word("/tmp/q2xfade"))) return; /* tried again next time round */
    if (ms) xfade_next_write(cur, next);
    if (xf.fade) *xf.fade = ms | (xf.stops & 0x7fff) << XF_STOPS;
    xf.done = 1;
}

static int command(const char *verb, const int *ids, int n_ids) {
    int a = 0, b = 0, c = 0;
    if (sscanf(verb, "play %d %d %d", &a, &b, &c) == 3) {
        if (!ids) n_ids = read_list(pending_ids), ids = pending_ids;
        if (c != n_ids) logf_("play names %d songs, the list holds %d", c, n_ids);
        if (n_ids > 0 && a >= 0 && a < n_ids && unavailable(ids, a)) return -1;
        if (n_ids > 0 && a >= 0 && a < n_ids && src.on) source_drop();
        return play_ids(ids, n_ids, a, b);
    }
    if (!strcmp(verb, "toggle")) return command(q.state == PLAYING ? "pause" : "resume", 0, 0);
    if (!strcmp(verb, "pause")) {
        if (q.state != PLAYING) return 0;
        player_send("mcl-pause", "null");
        q.state = PAUSED, ++q.seq;
        return 0;
    }
    if (!strcmp(verb, "resume")) {
        if (q.state == PAUSED && !q.loaded) return start_from(q.at, q.pos_ms / 1000); /* a queue given back */
        if (q.state == PAUSED) {
            dac_ready();
            player_send("mcl-play", "null");
            q.state = PLAYING, ++q.seq;
            return 0;
        }
        return q.state == STOPPED && q.n ? start_at(q.at) : 0;
    }
    if (src.on && (!strcmp(verb, "next") || !strcmp(verb, "previous"))) return -1; /* a source is one URL */
    if (!strcmp(verb, "next")) {
        if (q.at + 1 < q.n) return start_at(q.at + 1);
        if (mq.on && mq.waiting && !src.on) return 0; /* already waiting for it: nothing changes, as the firmware's */
        if (!q.paths || !mq.on || !*mq.want || src.on) return -1;
        stop(); /* the next not there yet: Now Playing on it, played as it lands (the firmware's addon_next_waits) */
        mq.waiting = 1, q.at = q.n - 1, q.state = PLAYING;
        logf_("next: waiting for %s", mq.want);
        path_lookup_want();
        return 0;
    }
    if (!strcmp(verb, "previous")) {
        if (q.loaded && q.pos_ms > 3000) {
            player_send("mcl-seek", "0");
            q.pos_ms = 0, ++q.seq;
            return 0;
        }
        return q.at > 0 ? start_at(q.at - 1) : -1;
    }
    if (sscanf(verb, "seek %d", &a) == 1 && a >= 0) {
        char s[16];
        if (!q.loaded) return -1;
        snprintf(s, sizeof s, "%d", a);
        player_send("mcl-seek", s);
        q.pos_ms = a * 1000, ++q.seq;
        return 0;
    }
    if (sscanf(verb, "volume %d", &a) == 1 && a >= 0 && a <= 100) {
        if (a > max_volume) a = max_volume; /* Max Volume's cap */
        if (a != q.volume) q.volume = a, ++q.seq, volume_dirty_at = now_ms() | 1;
        if (!air_vol.on) volume_apply(); /* a speaker's level is the AirPlay app's to send; the Q2 is silent */
        return 0;
    }
    if (!strcmp(verb, "stop")) {
        stop();
        if (src.on) source_end("stop");
        return 0;
    }
    if (!strcmp(verb, "output dac")) return output_set(0, 0), 0;
    if (!strcmp(verb, "output bluetooth")) return output_set(1, 0), 0;
    if (!strcmp(verb, "output bluetooth soft")) return output_set(1, 1), 0;
    return -1;
}

/* The FIFO: "<seq> <verb>\n" lines (patch/coda_rc_cmdq.h). Each is answered in /tmp/coda-rc.ack as demo's shim answers
 * it (coda_rc_shim.c cr_ack): a "<seq> <code>" line for it and the three before it, newest first, the file replaced
 * whole, so the coda-rc daemon and q2-iap, which wait for their seq there (coda_rc.c ack_poll), each find theirs though
 * they read it once for several. The code is 0 done, 1 not done, 3 a jump to a line that no longer holds its song. */
#define ACKS 4 /* coda_rc_shim.c CR_ACKS */
static const char *ack_rel = "/tmp/coda-rc.ack";
static unsigned long acks[ACKS][2];
static void ack(unsigned long seq, int code) {
    char b[ACKS * 24], tmp[340];
    int n = 0;
    memmove(acks[1], acks[0], sizeof acks - sizeof acks[0]);
    acks[0][0] = seq, acks[0][1] = (unsigned long)code;
    for (int i = 0; i < ACKS && (i == 0 || acks[i][0]); ++i) n += snprintf(b + n, sizeof b - (size_t)n, "%lu %lu\n", acks[i][0], acks[i][1]);
    snprintf(tmp, sizeof tmp, "%s.q2playd", path(ack_rel));
    FILE *f = fopen(tmp, "w");
    if (!f) return;
    fwrite(b, 1, (size_t)n, f);
    if (fclose(f) == 0) rename(tmp, path(ack_rel));
}

/* Car Remote's "jump INDEX ID" (coda_rc.c queue_item), the FIFO's alone: line INDEX of the queue file played if it
 * still holds song ID, as a View Queue row plays it (coda_rc_shim.c cr_jump): the one playing plays on, paused or
 * not; else 3, the phone's queue stale. */
static int jump(int line, int id) {
    int at = ql.start + line;
    if (src.on || line < 0 || line >= ql.listed || at >= q.n || (q.paths ? 0 : q.ids[q.order[at]]) != id) return 3;
    if (at == q.at && q.loaded) return q.state == PAUSED ? command("resume", 0, 0) : 0;
    q.errors = 0;
    return start_at(at) ? 1 : 0;
}

/* q2ui's Play Next and Play Last (ui/player.c player_enqueue, the shim's "queue next|last N": library_pages.c
 * queue_apply; Q2PLAY/1's queue): n ids put after the song playing in the play order (next) or after the last
 * (last), the song playing left alone; with nothing queued, loaded as the queue and not started. A source's URL and a
 * server list's answer, which keep their own queue, refuse it, as stock's streams do. */
static int enqueue_ids(int next, const int *ids, int n) {
    if (src.on || q.paths || n <= 0) return -1;
    gapless_forget();
    if (n > QUEUE_MAX - q.n) n = QUEUE_MAX - q.n;
    if (n <= 0) return -1;
    memcpy(q.ids + q.n, ids, (size_t)n * sizeof *q.ids);
    if (!q.n) { /* nothing queued: loaded, stopped at its first song */
        for (int i = 0; i < n; ++i) q.order[i] = i;
        q.n = n, q.at = 0, q.shuffle = 0, q.errors = 0, ++q.list, ++q.seq;
        return 0;
    }
    int at = next ? q.at + 1 : q.n;
    memmove(q.order + at + n, q.order + at, (size_t)(q.n - at) * sizeof *q.order);
    for (int i = 0; i < n; ++i) q.order[at + i] = q.n + i;
    q.n += n, ++q.seq;
    return 0;
}

/* the FIFO's: the list file's first n ids */
static int enqueue(int next, int n) {
    if (src.on || q.paths || n <= 0) return -1;
    gapless_forget();
    int got = read_list(pending_ids);
    return enqueue_ids(next, pending_ids, got < n ? got : n);
}

static char cq[2048];
static size_t cq_n;
static int find_cmd(int stop);
static void cmdq_read(void) {
    ssize_t got = read(cmdq_fd, cq + cq_n, sizeof cq - 1 - cq_n);
    if (got <= 0) return;
    cq_n += (size_t)got;
    char *p = cq, *nl;
    while ((nl = memchr(p, '\n', cq_n - (size_t)(p - cq)))) {
        *nl = 0;
        char *verb = strchr(p, ' ');
        char where[8];
        int a, b, r = !verb                                                     ? -1
                      : sscanf(verb + 1, "jump %d %d", &a, &b) == 2               ? jump(a, b)
                      : sscanf(verb + 1, "mode %d %d", &a, &b) == 2               ? set_mode(a, b)
                      : sscanf(verb + 1, "rate %d %d", &a, &b) == 2               ? rate_cmd(a, b)
                      : sscanf(verb + 1, "like %d", &a) == 1                      ? like_cmd(a)
                      : sscanf(verb + 1, "album %d %d", &a, &b) == 2              ? album_cmd(a, b)
                      : !strcmp(verb + 1, "find") || !strcmp(verb + 1, "find stop") ? find_cmd(verb[5] == ' ')
                      : sscanf(verb + 1, "queue %7s %d", where, &a) == 2 && (!strcmp(where, "next") || !strcmp(where, "last"))
                          ? enqueue(where[0] == 'n', a)
                          : command(verb + 1, 0, 0);
        if (verb && r) logf_("refused: %s", verb + 1);
        if (verb) ack(strtoul(p, 0, 10), r == 3 || r == 2 ? r : r ? 1 : 0);
        p = nl + 1;
    }
    cq_n -= (size_t)(p - cq);
    memmove(cq, p, cq_n);
    if (cq_n >= sizeof cq - 1) cq_n = 0;
}

/* ---- what plays, written for q2ui and told to Q2PLAY/1's watchers ---- */

/* A free-text field (a title, an artist, an album: from tags, so anything) for one line: Q2PLAY/1's percent-encoding
 * (docs/platform/README.md#the-common-shape), every byte below 0x21, '%', '"', '=' and 0x7f as %XX, so a value holds no space, no line
 * end and no '=' and a client splits on spaces safely; or, for the now file, whose format is demo's shim's (one
 * key=value a line, read by ui/now.c), only line ends and other control bytes made spaces. */
static void field(char *out, size_t room, const char *in, int encode) {
    static const char hex[] = "0123456789ABCDEF";
    size_t j = 0;
    for (const unsigned char *p = (const unsigned char *)in; *p && j + 4 < room; ++p) {
        if (encode && (*p < 0x21 || *p == '%' || *p == '"' || *p == '=' || *p == 0x7f))
            out[j++] = '%', out[j++] = hex[*p >> 4], out[j++] = hex[*p & 15];
        else out[j++] = !encode && *p < 0x20 ? ' ' : (char)*p;
    }
    out[j] = 0;
}

/* What plays: Q2PLAY/1's now fields (encode 1: free text percent-encoded, pos in seconds), or the now file's (pos in
 * ms, as demo's shim writes it). */
static void now_text(char *out, size_t room, const char *sep, int encode) {
    char t[800], a[800], b[800];
    int id = playing_id();
    field(t, sizeof t, q.title, encode), field(a, sizeof a, q.artist, encode), field(b, sizeof b, q.album, encode);
    int k = snprintf(out, room, "title=%s%sartist=%s%salbum=%s%sstate=%d%spos=%d%slen=%d%svolume=%d%sid=%d", t, sep, a, sep, b,
                     sep, q.state, sep, encode ? q.pos_ms / 1000 : q.pos_ms, sep, q.len_s, sep, q.volume, sep, id);
    /* Q2PLAY/1's: the play mode as the now file has it after these (now_write) */
    if (encode && k > 0 && (size_t)k < room)
        snprintf(out + k, room - (size_t)k, " shuffle=%d repeat=%d", q.shuffle != 0 || pm_shuffle(), pm_repeat());
}

/* Car Remote's view of the queue (coda_rc_shim.c cr_queue, read by coda_rc_lib.c's GetQueue): /tmp/coda-rc.queue,
 * "gen place lines" and then a line a song in the order it plays (a shuffle's too), its library id, or for a
 * played-as-paths song 0 and its path, title and artist after tabs (the names blank: q2playd knows a path's names
 * only while it plays); a queue past QUEUE_LISTED songs as the QUEUE_LISTED around the one playing. Rewritten, gen
 * moved, only when that changes; the now file's qpos, qlen and qgen are its place, lines and gen. */
#define QUEUE_LISTED 5000 /* coda_rc_shim.c CR_QUEUE_MAX */
static const char *queue_rel = "/tmp/coda-rc.queue";

static void queue_write(void) {
    int n = q.n, start = 0, listed = n < QUEUE_LISTED ? n : QUEUE_LISTED;
    if (n > QUEUE_LISTED) {
        start = q.at - QUEUE_LISTED / 2;
        if (start > n - QUEUE_LISTED) start = n - QUEUE_LISTED;
        if (start < 0) start = 0;
    }
    unsigned h = fnv(fnv(fnv(2166136261u, &q.list, sizeof q.list), &start, sizeof start), &q.at, sizeof q.at);
    size_t cap = 40;
    for (int i = 0; i < listed; ++i) {
        int id = q.paths ? 0 : q.ids[q.order[start + i]];
        h = fnv(h, &id, sizeof id);
        if (q.paths) h = fnv(h, q.paths[start + i], strlen(q.paths[start + i])), cap += strlen(q.paths[start + i]) + 4;
        cap += 12;
    }
    ql.start = start, ql.listed = listed;
    if (ql.gen && h == ql.hash) return;
    char *b = malloc(cap), tmp[340];
    if (!b) return;
    size_t len = (size_t)snprintf(b, cap, "%u %d %d\n", ql.gen + 1, q.at - start, listed);
    for (int i = 0; i < listed; ++i) {
        if (!q.paths) {
            len += (size_t)snprintf(b + len, cap - len, "%d\n", q.ids[q.order[start + i]]);
            continue;
        }
        b[len++] = '0', b[len++] = '\t';
        for (const char *c = q.paths[start + i]; *c; ++c) b[len++] = *c == '\t' || *c == '\n' || *c == '\r' ? ' ' : *c;
        b[len++] = '\t', b[len++] = '\t', b[len++] = '\n';
    }
    snprintf(tmp, sizeof tmp, "%s.q2playd", path(queue_rel));
    FILE *f = fopen(tmp, "w");
    if (f) {
        fwrite(b, 1, len, f);
        if (fclose(f) == 0 && rename(tmp, path(queue_rel)) == 0) ++ql.gen, ql.hash = h;
    }
    free(b);
}

/* The now file: seq= first, a count that moves with each change (coda-rc reads the rest only when it moved:
 * coda_rc.c np_read), what plays, then Car Remote's fields as demo's shim writes them (coda_rc_shim.c cr_now): the
 * modes (the play mode's: pm_shuffle, pm_repeat), hearts (the song's stars, 0 for none) and liked (its like, the pin's
 * shim's), each -1 for a source's URL, which cannot be rated, the queue file's place, lines and gen, qlist (the queue
 * itself), and the playing file's path. */
/* ---- Find My Vamp (docs/playd.md#find-my-vamp) ----
 * Car Remote's Find reaches the FIFO as the shim's "find" and "find stop" (coda_rc_shim.c); the jingle is q2sysd's
 * (Q2SYS/1 find, docs/sysd.md#find-my-vamp): answered 0 once it plays, 2 (UNAVAILABLE) when q2sysd refuses it or is
 * not there. While it plays q2sysd keeps /run/vamp/finding, and the now file says find=1. */
static int finding;
static int sys_ask(const char *cmd, char *out, size_t room) { /* one Q2SYS/1 command's answer line, 0; -1 with none */
    struct sockaddr_un a = {.sun_family = AF_UNIX};
    snprintf(a.sun_path, sizeof a.sun_path, "%s", path("/run/vamp/sys.sock"));
    int fd = socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0);
    struct timeval tv = {1, 0};
    char b[256], req[64];
    size_t n = 0;
    if (fd < 0) return -1;
    setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof tv), setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, &tv, sizeof tv);
    int len = snprintf(req, sizeof req, "Q2SYS/1\n%s\n", cmd);
    if (connect(fd, (struct sockaddr *)&a, sizeof a) || send(fd, req, (size_t)len, MSG_NOSIGNAL) != len) return close(fd), -1;
    for (char *nl; n < sizeof b - 1;) {
        ssize_t k = recv(fd, b + n, sizeof b - 1 - n, 0);
        if (k <= 0) break;
        n += (size_t)k, b[n] = 0;
        if ((nl = strchr(b, '\n')) && strchr(nl + 1, '\n')) break;
    }
    close(fd);
    b[n] = 0;
    char *nl = strchr(b, '\n');
    if (strncmp(b, "Q2SYS/1 ok\n", 11) || !nl || !strchr(nl + 1, '\n')) return -1;
    *strchr(nl + 1, '\n') = 0;
    snprintf(out, room, "%s", nl + 1);
    return 0;
}
static int find_cmd(int stop) {
    char r[64];
    if (sys_ask(stop ? "find stop" : "find", r, sizeof r) || strcmp(r, "ok")) return logf_("find: q2sysd did not play it"), 2;
    return 0;
}
static void find_poll(void) { /* q2sysd's mark, for the now file */
    int on = !access(path("/run/vamp/finding"), F_OK);
    if (on != finding) finding = on, ++q.seq;
}

static void now_write(void) {
    static unsigned writes;
    char text[2600], tmp[340];
    queue_write();
    now_text(text, sizeof text, "\n", 0);
    snprintf(tmp, sizeof tmp, "%s.q2playd", path(now_path_rel));
    FILE *f = fopen(tmp, "w");
    if (!f) return;
    fprintf(f, "seq=%u\n%s\nshuffle=%d\nrepeat=%d\nhearts=%d\nliked=%d\nqpos=%d\nqlen=%d\nqgen=%u\nqlist=%u\npath=", ++writes,
            text, q.shuffle != 0 || pm_shuffle(), pm_repeat(), hearts_now(), liked_now(), q.at - ql.start, ql.listed, ql.gen,
            q.list);
    for (const char *c = q.file; *c; ++c) fputc(*c == '\n' || *c == '\r' ? ' ' : *c, f);
    fputc('\n', f);
    if (finding) fputs("find=1\n", f); /* the shim's, while Find My Vamp's jingle plays (coda-rc's NowPlaying.finding) */
    if (fclose(f) == 0) rename(tmp, path(now_path_rel)); /* whole or not at all */
}

/* want_ids: the ids still to come after a play (queue 0) or a queue next (1) or last (2) */
static struct client { int fd, hello, watch; char in[LINE]; size_t n; int want_ids, got_ids, a, b, queue; } cl[CLIENTS];
#define SAY_MAX 2700 /* a line out: three encoded fields at most 3 x 255 x 3 bytes, and the rest */
static void client_say(struct client *c, const char *fmt, ...);

static void client_say(struct client *c, const char *fmt, ...) {
    char line[SAY_MAX];
    va_list ap;
    va_start(ap, fmt);
    int n = vsnprintf(line, sizeof line - 1, fmt, ap);
    va_end(ap);
    if (n < 0) return;
    if (n > (int)sizeof line - 2) n = (int)sizeof line - 2;
    line[n++] = '\n';
    if (send(c->fd, line, (size_t)n, MSG_NOSIGNAL | MSG_DONTWAIT) != n) close(c->fd), c->fd = -1;
}

static void tell_watchers(void) {
    char text[2600];
    now_text(text, sizeof text, " ", 1);
    for (int i = 0; i < CLIENTS; ++i)
        if (cl[i].fd >= 0 && cl[i].watch) client_say(&cl[i], "now %s", text);
}

/* ---- sources and audio focus (docs/playd.md#sources-and-audio-focus) ---- */

/* a Q2PLAY/1 free-text value back to its bytes: %XX decoded, the rest as it is */
static void unfield(char *out, size_t room, const char *in, size_t len) {
    size_t j = 0;
    static const char hex[] = "0123456789abcdef";
    for (size_t i = 0; i < len && j + 1 < room; ++i) {
        const char *h, *l;
        if (in[i] == '%' && i + 2 < len && in[i + 1] && in[i + 2] && (h = strchr(hex, tolower((unsigned char)in[i + 1]))) &&
            (l = strchr(hex, tolower((unsigned char)in[i + 2]))))
            out[j++] = (char)((h - hex) << 4 | (l - hex)), i += 2;
        else out[j++] = in[i];
    }
    out[j] = 0;
}

static void tell_holder(const char *why) {
    if (cl[src.holder].fd >= 0) client_say(&cl[src.holder], "focus lost why=%s", why);
}

/* the source's URL has stopped playing (why: end, error, stop; NULL for the holder's own release): the holder told,
 * and the queue back as it was put aside, paused where it was (stopped, if it was), its song shown again */
static void source_end(const char *why) {
    if (!src.on) return;
    if (why) tell_holder(why);
    src.on = 0;
    memcpy(q.ids, src.ids, (size_t)src.n * sizeof *q.ids), memcpy(q.order, src.order, (size_t)src.n * sizeof *q.order);
    q.n = src.n, q.at = src.at, q.shuffle = src.shuffle, q.offline = src.offline, q.paths = src.paths, q.np = src.np, src.paths = 0, src.np = 0;
    q.loaded = 0, q.errors = 0, ++q.list;
    q.state = src.state == STOPPED || (why && !strcmp(why, "stop")) || !q.n ? STOPPED : PAUSED;
    q.pos_ms = q.state == PAUSED ? src.pos_ms : 0, q.len_s = q.state == PAUSED ? src.len_s : 0;
    q.title[0] = q.artist[0] = q.album[0] = q.file[0] = q.row_url[0] = 0;
    if (q.n && (q.paths ? path_lookup(q.at) : song_lookup(q.ids[q.order[q.at]])))
        set_cover(q.paths ? -1 : q.ids[q.order[q.at]], q.paths || *q.row_url ? q.row_url : q.file);
    else set_cover(-1, "");
    ++q.seq;
}

/* a new play replaces the queue put aside: the holder told, the queue not given back */
static void source_drop(void) {
    tell_holder("taken");
    src.on = 0;
    for (int i = 0; i < src.np; ++i) free(src.paths[i]);
    free(src.paths), src.paths = 0, src.np = 0;
}

/* source URL [title=T] [artist=A] [album=B]: 0 played, -1 refused (not http:// or https://, or a byte the player's
 * message cannot carry) */
static int source_take(int slot, const char *args) {
    char url[sizeof q.file], t[256] = "", a[256] = "", b[256] = "";
    const char *sp = strchr(args, ' ');
    size_t ul = sp ? (size_t)(sp - args) : strlen(args);
    unfield(url, sizeof url, args, ul);
    if (strncmp(url, "http://", 7) && strncmp(url, "https://", 8)) return -1;
    for (const unsigned char *p = (const unsigned char *)url; *p; ++p)
        if (*p < 0x21 || *p == '|' || *p == 0x7f) return -1;
    for (const char *w = sp; w && *w; w = strchr(w, ' ')) { /* the fields; a key this service does not know is passed over */
        const char *k = ++w, *eq = strchr(k, '='), *end = strchr(k, ' ');
        size_t n = end ? (size_t)(end - k) : strlen(k);
        if (!eq || (end && eq > end)) continue;
        char *dst = !strncmp(k, "title=", 6) ? t : !strncmp(k, "artist=", 7) ? a : !strncmp(k, "album=", 6) ? b : 0;
        if (dst) unfield(dst, 256, eq + 1, n - (size_t)(eq + 1 - k));
    }
    if (!src.on) { /* the queue put aside as it is */
        memcpy(src.ids, q.ids, (size_t)q.n * sizeof *q.ids), memcpy(src.order, q.order, (size_t)q.n * sizeof *q.order);
        src.n = q.n, src.at = q.at, src.shuffle = q.shuffle, src.offline = q.offline, src.state = q.state, src.pos_ms = q.pos_ms, src.len_s = q.len_s;
        src.paths = q.paths, src.np = q.np, q.paths = 0, q.np = 0;
    } else if (src.holder != slot) tell_holder("taken");
    src.on = 1, src.holder = slot, cp.on = 0;
    q.n = 0, q.at = 0, ++q.list;
    snprintf(q.file, sizeof q.file, "%s", url);
    const char *base = strrchr(url, '/');
    snprintf(q.title, sizeof q.title, "%s", *t ? t : base + 1);
    snprintf(q.artist, sizeof q.artist, "%s", a), snprintf(q.album, sizeof q.album, "%s", b);
    int cut = q.loaded;
    gapless_forget();
    dac_ready();
    send_song(cut ? "set-nexturl" : "set-songurl", url, 0);
    set_cover(-1, "");
    q.expect_cut = cut;
    q.loaded = 1, q.state = PLAYING, q.pos_ms = 0, q.len_s = 0;
    ++q.seq;
    return 0;
}

/* focus release: the holder's source stopped and the queue given back */
static int source_release(int slot) {
    if (!src.on || src.holder != slot) return -1;
    if (q.loaded) player_send("mcl-stop", "null"), q.expect_cut = 1;
    source_end(0);
    return 0;
}

/* One Q2PLAY/1 line (docs/platform/playback.md). */
static void client_line(struct client *c, char *line) {
    if (!c->hello) {
        if (strcmp(line, "Q2PLAY/1")) { client_say(c, "err version"); close(c->fd), c->fd = -1; return; }
        char who[100];
        if (!q2perm_allowed(c->fd, path("/etc/passwd"), path("/etc/group"), "playback", who, sizeof who)) {
            logf_("refused %s: no playback permission", who); /* docs/platform/packages.md#users-and-groups */
            client_say(c, "err permission");
            close(c->fd), c->fd = -1;
            return;
        }
        c->hello = 1;
        client_say(c, "Q2PLAY/1 ok");
        return;
    }
    if (c->want_ids) { /* a play's ids, one a line */
        pending_ids[c->got_ids++] = atoi(line);
        if (c->got_ids == c->want_ids) {
            char verb[48];
            snprintf(verb, sizeof verb, "play %d %d %d", c->a, c->b, c->want_ids);
            int r = c->queue ? enqueue_ids(c->queue == 1, pending_ids, c->want_ids) : command(verb, pending_ids, c->want_ids);
            c->want_ids = 0;
            client_say(c, !r ? "ok" : c->queue ? "err queue" : "err play");
        }
        return;
    }
    int a, b, n;
    char where[8];
    if (sscanf(line, "play %d %d %d", &a, &b, &n) == 3) {
        if (n <= 0 || n > QUEUE_MAX || a < 0 || a >= n) { client_say(c, "err play"); return; }
        c->want_ids = n, c->got_ids = 0, c->a = a, c->b = b, c->queue = 0;
        return;
    }
    if (sscanf(line, "queue %7s %d%n", where, &a, &n) == 2 && !line[n] && (!strcmp(where, "next") || !strcmp(where, "last")) &&
        a > 0 && a <= QUEUE_MAX) {
        c->want_ids = a, c->got_ids = 0, c->queue = where[0] == 'n' ? 1 : 2;
        return;
    }
    if (!strcmp(line, "caps")) { /* Vamp API 1's capability words (docs/platform/README.md#capabilities) */
        client_say(c, "caps play toggle pause resume next previous seek volume stop now watch source focus output rating rate like album click eq queue mode");
    } else if (!strncmp(line, "source ", 7)) {
        client_say(c, source_take((int)(c - cl), line + 7) ? "err source" : "ok");
    } else if (!strcmp(line, "focus")) {
        client_say(c, "focus source=%d held=%d", src.on, src.on && src.holder == (int)(c - cl));
    } else if (!strcmp(line, "focus release")) {
        client_say(c, source_release((int)(c - cl)) ? "err focus release" : "ok");
    } else if (!strcmp(line, "now")) {
        char text[2600];
        now_text(text, sizeof text, " ", 1);
        client_say(c, "now %s", text);
    } else if (sscanf(line, "rating %d%n", &a, &n) == 1 && !line[n]) { /* the stars and like of song a, 0 the one playing */
        char p[1100];
        if (!id_path(a, p, sizeof p)) client_say(c, "err rating");
        else client_say(c, "rating id=%d stars=%d liked=%d", a ? a : playing_id(), rate_kept(listen_key(p)), path_liked(p));
    } else if (sscanf(line, "rate %d %d%n", &a, &b, &n) == 2 && !line[n]) {
        char p[1100];
        client_say(c, !id_path(b, p, sizeof p) || rate_path(p, a) ? "err rate" : "ok");
    } else if (sscanf(line, "like %d %d%n", &a, &b, &n) == 2 && !line[n]) {
        char p[1100];
        client_say(c, (a != 0 && a != 1) || !id_path(b, p, sizeof p) || like_path(p, a) ? "err like" : "ok");
    } else if (sscanf(line, "album %d %d%n", &a, &b, &n) == 2 && !line[n]) {
        client_say(c, album_cmd(a, b) ? "err album" : "ok");
    } else if (!strcmp(line, "click wheel") || !strcmp(line, "click button") || !strcmp(line, "click beep")) {
        /* the Clicker's click mixed into what the player plays (clicker.c's {mcl-click\KIND}, docs/playd.md#key-tone):
         * only while the queue's player owns the output, as clicker.c sends it only while the Library's does */
        if (player < 0 || src.on) client_say(c, "err click");
        else player_send("mcl-click", line + 6), client_say(c, "ok");
    } else if (sscanf(line, "mode %d %d%n", &a, &b, &n) == 2 && !line[n]) { /* the FIFO's mode */
        client_say(c, set_mode(a, b) ? "err %s" : "ok", line);
    } else if (!strcmp(line, "eq")) {
        int m = eq_mode();
        client_say(c, "eq on=%d mode=%s", m != EQ_OFF, m == EQ_PEQ ? "peq" : m == EQ_3 ? "3eq" : "off");
    } else if (!strcmp(line, "eq on") || !strcmp(line, "eq off")) {
        client_say(c, eq_switch(line[4] == 'n') ? "err %s" : "ok", line);
    } else if (!strcmp(line, "watch")) {
        c->watch = 1;
        client_say(c, "ok");
    } else if (!strcmp(line, "output")) {
        client_say(c, "output %s", !way ? "dac" : soft ? "bluetooth soft" : "bluetooth");
    } else {
        client_say(c, command(line, 0, 0) ? "err %s" : "ok", line);
    }
}

static void client_read(struct client *c) {
    ssize_t got = recv(c->fd, c->in + c->n, sizeof c->in - 1 - c->n, 0);
    if (got <= 0) { close(c->fd), c->fd = -1; return; }
    c->n += (size_t)got;
    char *p = c->in, *nl;
    while (c->fd >= 0 && (nl = memchr(p, '\n', c->n - (size_t)(p - c->in)))) {
        *nl = 0;
        if (nl > p && nl[-1] == '\r') nl[-1] = 0;
        client_line(c, p);
        p = nl + 1;
    }
    if (c->fd < 0) return;
    c->n -= (size_t)(p - c->in);
    memmove(c->in, p, c->n);
    if (c->n >= sizeof c->in - 1) { client_say(c, "err line too long"); close(c->fd), c->fd = -1; }
}

/* ---- AirPlay's tap, the host's half (docs/playd.md#airplay) ----
 * The q2-airplay app makes /tmp/q2cast (a cast_tap, patch/peq.h) and beats it; q2player's DSP chain (player/dsp.c)
 * writes what plays into it while the beat is fresh, and keeps the Q2's own output silent while `volume` is 0 or
 * more. On today's Vamp demo writes the rest (patch/airplay.c airplay_poll); here q2playd does: the player's status,
 * Max Volume and the volume (the speaker's while one plays, else -1) and its beat, every CAST_BEAT_MS; the playing
 * song's tags to /tmp/q2cast.track once a song; and the volume's swap, the earphones' level kept while a speaker
 * plays and given back after. The layout is peq.h's, mirrored as the app mirrors it (test/playd.py compares them). */
#define CAST_MAGIC 0x31544351u /* "QCT1" */
#define CAST_RING 262144
#define CAST_CLOCK(ns) ((unsigned)((unsigned long long)(ns) >> 20)) /* 1.048576 ms ticks */
#define CAST_STALE 1430                                             /* 1.5 s of ticks: the host lets go first */
#define CAST_BEAT_MS 250
#define CAST_LOOK_MS 1000 /* the file looked for this often until the app makes it */
#define AIR_VOLUME 35     /* a speaker's level when the app gives none and Q2POD AIRVOL is not set */
enum { CAST_OFF, CAST_CONNECTING, CAST_ON };
typedef struct {
    unsigned magic, size;
    unsigned want; /* the app's: its CAST_CLOCK while it reads */
    int mute, state, pos_ms;
    unsigned pos_id, pos_at;
    int pos_run;
    char speaker[64];
    unsigned seq, id; /* the player's */
    int pts_ms;
    unsigned jumps, jump_seq;
    int dsd, spk; /* spk: the app's, the speaker's own level */
    long long stamp;
    unsigned beat;      /* the host's: bumped each pass */
    int status;         /* 1 stopped, 2 playing, 3 paused */
    int volume, maxvol; /* the speaker's level while one plays, else -1; Max Volume */
    unsigned track;     /* /tmp/q2cast.track's writes */
    unsigned spk_seq;   /* the app's, bumped with each spk */
    short ring[CAST_RING][2];
} cast_tap;
_Static_assert(__builtin_offsetof(cast_tap, stamp) == 128 && __builtin_offsetof(cast_tap, ring) == 160, "peq.h's cast_tap layout");

static struct { cast_tap *t; unsigned long long look, beat_at; unsigned spk_seq; char song[1300]; int len; } air;

static unsigned long long mono_ns(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (unsigned long long)ts.tv_sec * 1000000000ull + (unsigned long long)ts.tv_nsec;
}

static void air_map(void) {
    unsigned long long t = now_ms();
    if (air.t || t - air.look < CAST_LOOK_MS) return;
    air.look = t;
    int fd = open(path("/tmp/q2cast"), O_RDWR | O_CLOEXEC);
    if (fd < 0) return;
    unsigned head[2] = {0, 0}; /* the app's, whole before it renames it in */
    void *p = MAP_FAILED;
    if (read(fd, head, sizeof head) == sizeof head && head[0] == CAST_MAGIC && head[1] == sizeof(cast_tap))
        p = mmap(0, sizeof(cast_tap), PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
    close(fd);
    if (p != MAP_FAILED) air.t = p, logf_("the AirPlay app's tap mapped");
}

/* the app's state while it beats; CAST_OFF once it stopped */
static int air_state(void) {
    return air.t && CAST_CLOCK(mono_ns()) - air.t->want < CAST_STALE ? air.t->state : CAST_OFF;
}

/* the speaker's level: its own, as the app said, else the one it last had (Q2POD AIRVOL) */
static int air_speaker(void) {
    char *c = read_all(path("/mnt/data/config.ini"));
    int saved = c ? ini_get(c, "Q2POD", "AIRVOL", AIR_VOLUME) : AIR_VOLUME;
    free(c);
    air.spk_seq = air.t ? air.t->spk_seq : 0;
    if (air.t && air.t->spk_seq && air.t->spk >= 0 && air.t->spk <= 100) return air.t->spk;
    return saved >= 0 && saved <= 100 ? saved : AIR_VOLUME;
}

/* a speaker starting: the earphones' level and Max Volume kept (the level saved), the limit lifted (it is the
 * earphones'), the speaker's taken up and the DAC left alone (q2player silences the Q2); stopping: the speaker's
 * saved as AIRVOL and the earphones' back, set on the DAC and saved */
static void air_swap(int on) {
    if (on) {
        air_vol.local = q.volume, air_vol.limit = max_volume;
        ini_set("PLAYSET", way ? "BT_VOLUME" : "VOLUME", q.volume); /* the output's own level (volume_save) */
        air_vol.on = 1, max_volume = 100;
        q.volume = air_speaker();
    } else {
        ini_set("Q2POD", "AIRVOL", q.volume);
        air_vol.on = 0, max_volume = air_vol.limit;
        q.volume = air_vol.local > max_volume ? max_volume : air_vol.local;
        volume_apply();
        ini_set("PLAYSET", way ? "BT_VOLUME" : "VOLUME", q.volume);
    }
    volume_dirty_at = 0, ++q.seq;
}

/* "key=value" lines of the playing song for the app (the speaker's now-playing), written beside and renamed */
static void air_track(void) {
    char id[1300];
    snprintf(id, sizeof id, "%s\n%s", q.file, q.title);
    if (!q.loaded || (!strcmp(id, air.song) && q.len_s == air.len)) return;
    char tmp[340], v[4][800];
    const char *src[4] = {q.file, q.title, q.artist, q.album};
    for (int i = 0; i < 4; ++i) field(v[i], sizeof v[i], src[i], 0); /* line ends made spaces */
    snprintf(tmp, sizeof tmp, "%s.q2playd", path("/tmp/q2cast.track"));
    FILE *f = fopen(tmp, "w");
    if (!f) return;
    fprintf(f, "path=%s\ntitle=%s\nartist=%s\nalbum=%s\nlength=%d\n", v[0], v[1], v[2], v[3], q.len_s);
    if (fclose(f) || rename(tmp, path("/tmp/q2cast.track"))) { unlink(tmp); return; }
    snprintf(air.song, sizeof air.song, "%s", id), air.len = q.len_s;
    ++air.t->track;
}

/* each pass of the loop, at most every CAST_BEAT_MS */
static void air_poll(void) {
    unsigned long long t = now_ms();
    if (t - air.beat_at < CAST_BEAT_MS) return;
    air.beat_at = t;
    air_map();
    int s = air_state();
    if (air.t) {
        air.t->status = q.state == PLAYING ? 2 : q.state == PAUSED ? 3 : 1;
        air.t->volume = air_vol.on ? q.volume : -1; /* the speaker's once it plays, never the earphones' */
        air.t->maxvol = max_volume;
        __atomic_thread_fence(__ATOMIC_RELEASE);
        ++air.t->beat;
        if (s != CAST_OFF) air_track();
    }
    if ((s == CAST_ON) != air_vol.on) air_swap(s == CAST_ON);
    else if (air_vol.on && air.t->spk_seq != air.spk_seq) q.volume = air_speaker(), ++q.seq; /* reconnected: the speaker's again */
}

/* ---- setup ---- */

static int listen_unix(const char *where, int type, int backlog) {
    struct sockaddr_un a = {.sun_family = AF_UNIX};
    snprintf(a.sun_path, sizeof a.sun_path, "%s", where);
    unlink(where);
    int fd = socket(AF_UNIX, type | SOCK_CLOEXEC, 0);
    if (fd < 0 || bind(fd, (struct sockaddr *)&a, sizeof a) || (type == SOCK_STREAM && listen(fd, backlog))) {
        logf_("%s: %s", where, strerror(errno));
        if (fd >= 0) close(fd);
        return -1;
    }
    chmod(where, 0600);
    return fd;
}

static volatile sig_atomic_t stopping;
static void on_term(int s) { (void)s, stopping = 1; }

int main(int argc, char **argv) {
    for (int i = 1; i < argc; ++i) {
        if (!strcmp(argv[i], "--root") && i + 1 < argc) snprintf(root, sizeof root, "%s", argv[++i]);
        else if (!strcmp(argv[i], "--db") && i + 1 < argc) snprintf(db_path, sizeof db_path, "%s", argv[++i]);
        else if (!strcmp(argv[i], "--catalogue") && i + 1 < argc) snprintf(cat_path, sizeof cat_path, "%s", argv[++i]);
        else return fprintf(stderr, "usage: q2playd [--root DIR] [--db FILE] [--catalogue FILE]\n"), 2;
    }
    if (!*db_path) snprintf(db_path, sizeof db_path, "%s/mnt/data/database.db", root);
    if (!*cat_path) snprintf(cat_path, sizeof cat_path, "%s/mnt/mmc/.vamp/catalogue.db", root);
    signal(SIGPIPE, SIG_IGN);
    signal(SIGTERM, on_term);
    signal(SIGINT, on_term);
    for (int i = 0; i < CLIENTS; ++i) cl[i].fd = -1;
    listen_fd = listen_unix(path(sock_rel), SOCK_STREAM, 1);
    push_fd = listen_unix(path(push_rel), SOCK_DGRAM, 0);
    mkdir(path("/run/vamp"), 0755);
    api_fd = listen_unix(path(api_rel), SOCK_STREAM, CLIENTS);
    q2perm_socket(path(api_rel), path("/etc/group"), "playback"); /* 0660, the playback group's */
    unlink(path(cmdq_rel));
    if (mkfifo(path(cmdq_rel), 0600)) logf_("%s: %s", path(cmdq_rel), strerror(errno));
    cmdq_fd = open(path(cmdq_rel), O_RDWR | O_NONBLOCK | O_CLOEXEC); /* RDWR: never an EOF when a writer leaves */
    if (listen_fd < 0) return 1;
    volume_load();
    eq_boot();
    likes_sync();
    memory_restore();
    now_write();
    dac_volume();
    unsigned long long polled = 0;
    while (!stopping) {
        struct pollfd p[5 + CLIENTS];
        int n = 0;
        p[n++] = (struct pollfd){.fd = player >= 0 ? -1 : listen_fd, .events = POLLIN};
        p[n++] = (struct pollfd){.fd = player, .events = POLLIN};
        p[n++] = (struct pollfd){.fd = push_fd, .events = POLLIN};
        p[n++] = (struct pollfd){.fd = cmdq_fd, .events = POLLIN};
        p[n++] = (struct pollfd){.fd = api_fd, .events = POLLIN};
        for (int i = 0; i < CLIENTS; ++i) p[n++] = (struct pollfd){.fd = cl[i].fd, .events = POLLIN};
        unsigned long long t = now_ms();
        int wait = -1;
        if (q.loaded) wait = (int)(polled + POLL_MS > t ? polled + POLL_MS - t : 0);
        if (q.seq != now_written_seq) {
            int w = (int)(now_written_at + NOW_EVERY_MS > t ? now_written_at + NOW_EVERY_MS - t : 0);
            if (wait < 0 || w < wait) wait = w;
        }
        if (volume_dirty_at) {
            int w = (int)(volume_dirty_at + VOLUME_SAVE_MS > t ? volume_dirty_at + VOLUME_SAVE_MS - t : 0);
            if (wait < 0 || w < wait) wait = w;
        }
        if ((cp.on || mq.on) && (wait < 0 || wait > 200)) wait = 200;
        if (rc.on) wait = 0; /* the ratings' copy: a page each time round */
        int beat = air.t ? CAST_BEAT_MS : CAST_LOOK_MS; /* AirPlay's tap: its beat, or the look for it */
        if (wait < 0 || wait > beat) wait = beat;
        if (poll(p, (nfds_t)n, wait) < 0 && errno != EINTR) break;
        catplay_poll();
        m3u_poll();
        words_poll();
        find_poll();
        dac_poll(now_ms());
        ahead_poll();
        air_poll();
        if (src.on && cl[src.holder].fd < 0) source_release(src.holder); /* its holder gone (here and after the reads): before a new client takes its slot */
        if (p[0].revents & POLLIN) {
            player = accept4(listen_fd, 0, 0, SOCK_CLOEXEC);
            if (player >= 0) logf_("the player connected");
            if (player >= 0 && way) player_send("mcl-setoutputway", "1"), volume_apply(); /* a player started again: the output it had */
            if (player >= 0 && mp.go) start_from(q.at, mp.go - 1), mp.go = 0; /* car mode's kept queue */
        }
        if (player >= 0 && p[1].fd == player && p[1].revents) player_read();
        if (p[2].revents & POLLIN) {
            char b[16];
            while (recv(push_fd, b, sizeof b, MSG_DONTWAIT) > 0) {
            }
            ask_status(), polled = now_ms();
        }
        if (p[3].revents & POLLIN) cmdq_read();
        if (p[4].revents & POLLIN) {
            int fd = accept4(api_fd, 0, 0, SOCK_CLOEXEC);
            int i = 0;
            while (fd >= 0 && i < CLIENTS && cl[i].fd >= 0) ++i;
            if (fd >= 0 && i == CLIENTS) close(fd);
            else if (fd >= 0) cl[i] = (struct client){.fd = fd};
        }
        for (int i = 0; i < CLIENTS; ++i)
            if (cl[i].fd >= 0 && p[5 + i].fd == cl[i].fd && p[5 + i].revents) client_read(&cl[i]);
        if (src.on && cl[src.holder].fd < 0) source_release(src.holder);
        t = now_ms();
        if (q.loaded && t >= polled + POLL_MS) ask_status(), polled = t;
        if (volume_dirty_at && t >= volume_dirty_at + VOLUME_SAVE_MS) volume_save(), volume_dirty_at = 0;
        plays_flush(t);
        memory_poll(t, 0);
        likes_poll(t);
        resume_flush(t);
        ratings_in_poll(t);
        ratings_copy_poll(t);
        stream_poll();
        if (q.seq != now_written_seq && t >= now_written_at + NOW_EVERY_MS) {
            now_write(), tell_watchers();
            now_written_seq = q.seq, now_written_at = t;
        }
    }
    if (volume_dirty_at) volume_save();
    memory_poll(now_ms(), 1);
    unlink(path(sock_rel)), unlink(path(push_rel)), unlink(path(api_rel)), unlink(path(cmdq_rel));
    return 0;
}
