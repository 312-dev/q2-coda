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
#include <errno.h>
#include <fcntl.h>
#include <poll.h>
#include <signal.h>
#include <sqlite3.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/un.h>
#include <time.h>
#include <unistd.h>

#include "q2perm.h" /* perm/: the permission check */

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
    unsigned seq;                /* moves with every change of what is written */
} q = {.volume = 24};

static unsigned long long now_written_at;
static unsigned now_written_seq = ~0u;

/* ---- the library: stock's songtable (docs/internals.md#library) ---- */

#define CAT_ID_BASE 0x40000000 /* server rows' ids (q2libd, patch/catalogue.c) */

static int song_lookup(int id) {
    sqlite3 *db;
    sqlite3_stmt *st;
    int ok = 0, server = (unsigned)id >= CAT_ID_BASE;
    q.title[0] = q.artist[0] = q.album[0] = q.file[0] = 0;
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
 * cache in KB by the file's kind and its fill percent, Gapless off (no preload in this slice), the Q2's hardware type
 * 17. */
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
    snprintf(arg, sizeof arg, "%s|ss=%d|%d|%d|0|17|90", url, ss, cache, fill);
    player_send(cmd, arg);
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

/* A played-as-paths song: its card row's title, artist and album; a Stream Cache copy's its server row's; else the
 * file's name as the title. */
static int path_lookup(int at) {
    const char *p = q.paths[at], *base = strrchr(p, '/');
    char app[64], sid[128], like[300];
    snprintf(q.file, sizeof q.file, "%s", p);
    q.row_url[0] = 0;
    if (row_of(db_path, "select name, songer, album, fileurl from songtable where fileurl = ?1", p)) return 1;
    if (stream_sid(p, app, sizeof app, sid, sizeof sid)) {
        snprintf(like, sizeof like, "/mnt/mmc/.%s/cat/%%/%s.%%", app, sid);
        if (row_of(cat_path, "select name, songer, album, fileurl from servertable where fileurl like ?1", like)) return 1;
    }
    snprintf(q.title, sizeof q.title, "%s", base ? base + 1 : p);
    q.artist[0] = q.album[0] = 0;
    return 1;
}

/* The queue's song at `at` from ss seconds (a queue given back after a source starts where it was put aside). */
static int start_from(int at, int ss) {
    while (at >= 0 && at < q.n) {
        q.at = at;
        if (q.paths ? path_lookup(at) : song_lookup(q.ids[q.order[at]])) {
            int cut = q.loaded;
            send_song(cut ? "set-nexturl" : "set-songurl", q.file, ss);
            set_cover(q.paths ? -1 : q.ids[q.order[at]], q.paths ? q.row_url : q.file);
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
    int ids[QUEUE_MAX], order[QUEUE_MAX], n, at, shuffle, state, pos_ms, len_s;
    char **paths;
    int np;
} src;
static void source_end(const char *why);
static void source_drop(void);

static void stop(void) {
    if (q.loaded) {
        player_send("mcl-stop", "null");
        q.expect_cut = 1;
    }
    q.loaded = 0, q.state = STOPPED, q.pos_ms = 0;
    ++q.seq;
}

/* A song ended on its own (played out, or did not open): the next, or the queue's end. */
static void ended(int error) {
    logf_("song %d %s", q.n && !q.paths ? q.ids[q.order[q.at]] : 0, error ? "did not play" : "ended");
    q.loaded = 0;
    if (src.on) { /* a source's URL: its holder is told and the queue given back */
        source_end(error ? "error" : "end");
        return;
    }
    q.errors = error ? q.errors + 1 : 0;
    if (q.errors < ERRORS_MAX && q.at + 1 < q.n && !start_at(q.at + 1)) return;
    player_send("mcl-orderstop", "null");
    q.state = STOPPED, q.pos_ms = 0;
    ++q.seq;
}

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

static void dac_volume(void) {
    if (dac < 0 && !dac_missing) {
        dac = open(path("/dev/shanling_dac"), O_RDWR | O_CLOEXEC);
        if (dac < 0) dac_missing = 1, logf_("no /dev/shanling_dac: the volume is kept and shown, not set");
    }
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

static void volume_load(void) {
    char *t = read_all(path("/mnt/data/config.ini"));
    if (!t) return;
    max_volume = ini_get(t, "PLAYSET", "MAXVOLUME", 100);
    if (max_volume < 0 || max_volume > 100) max_volume = 100;
    int v = ini_get(t, "PLAYSET", "BOOTVOLFLAG", 0) ? ini_get(t, "PLAYSET", "BOOTVOLUME", -1) : ini_get(t, "PLAYSET", "VOLUME", -1);
    if (v >= 0 && v <= 100) q.volume = v > max_volume ? max_volume : v;
    other_volume = ini_get(t, "PLAYSET", "BT_VOLUME", -1); /* Bluetooth's, while the DAC is the output */
    if (other_volume > 100) other_volume = -1;
    free(t);
}

/* KEY=v in [SECTION], the rest of the file as it was */
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
 * is played from its card rows alone. An app that is not listening is started first by q2pkgd (Q2PKG/1 start, the
 * launcher: docs/platform/packages.md#starting-and-stopping-q2pkg1), and the catplay written once its socket is there
 * (an app takes a .catplay there at its start as one answered); q2pkgd refusing, or no socket within CATPLAY_START_MS,
 * and the card rows alone play. */
#define CATPLAY_WAIT_MS 4000
#define CATPLAY_START_MS 15000 /* an app started for a catplay has this long to listen (today's Vamp's catalogue.c) */
#define PKG_ASK_MS 3000        /* q2pkgd's answer to a start, waited for in the loop */
static struct { int on, at, starting; char app[64], before[600]; unsigned seq; unsigned long long until; } cp;

static void free_paths(void) {
    for (int i = 0; i < q.np; ++i) free(q.paths[i]);
    free(q.paths), q.paths = 0, q.np = 0;
}

static void read_small(const char *p, char *out, size_t room) {
    FILE *f = fopen(p, "r");
    size_t n = f ? fread(out, 1, room - 1, f) : 0;
    if (f) fclose(f);
    out[n] = 0;
}

/* the card rows of the queue alone, from the first at or after the one asked for (at) */
static int play_offline(int at) {
    int n = 0, start = -1;
    for (int i = 0; i < q.n; ++i)
        if ((unsigned)q.ids[q.order[i]] < CAT_ID_BASE) {
            if (i >= at && start < 0) start = n;
            q.order[n++] = q.order[i];
        }
    q.n = n;
    logf_("server songs left out: %d card songs to play", n);
    return n && start >= 0 ? start_at(start) : -1;
}

/* the app of a server row's path, /mnt/mmc/.<id>/cat/... */
static int app_of(const char *fileurl, char *app, size_t room) {
    const char *pre = "/mnt/mmc/.", *p;
    if (strncmp(fileurl, pre, strlen(pre)) || !(p = strstr(fileurl + strlen(pre), "/cat/"))) return -1;
    snprintf(app, room, "%.*s", (int)(p - fileurl - strlen(pre)), fileurl + strlen(pre));
    return 0;
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

/* the catplay written for cp.app: 0, or the card rows alone played */
static int catplay_write(void) {
    char tmp[300], dst[300], play[300];
    snprintf(tmp, sizeof tmp, "/tmp/q2-%s.catplay.q2playd", cp.app), snprintf(dst, sizeof dst, "/tmp/q2-%s.catplay", cp.app);
    snprintf(play, sizeof play, "/tmp/q2-%s.play", cp.app);
    read_small(path(play), cp.before, sizeof cp.before);
    FILE *f = fopen(path(tmp), "w");
    if (!f) return cp.on = 0, play_offline(cp.at), -1;
    fprintf(f, "%u %d\n", ++cp.seq, cp.at);
    for (int i = 0; i < q.n; ++i) fprintf(f, "%s\n", song_lookup(q.ids[q.order[i]]) ? q.file : "");
    if (fclose(f) || rename(path(tmp), path(dst))) return cp.on = 0, play_offline(cp.at), -1;
    cp.starting = 0, cp.until = now_ms() + CATPLAY_WAIT_MS;
    logf_("list handed to %s (catplay %u)", cp.app, cp.seq);
    return 0;
}

static int listening(const char *app) {
    char sock[300];
    snprintf(sock, sizeof sock, "/tmp/q2-%s.browse.sock", app);
    return !access(path(sock), F_OK);
}

/* 1: handed to the app, or to q2pkgd to start it (an answer awaited); 0: nothing of the list is a server's */
static int catplay(int at) {
    char app[64] = "";
    int server = 0;
    for (int i = 0; i < q.n && !server; ++i)
        if ((unsigned)q.ids[q.order[i]] >= CAT_ID_BASE && song_lookup(q.ids[q.order[i]]) && !app_of(q.file, app, sizeof app)) server = 1;
    if (!server) return 0;
    snprintf(cp.app, sizeof cp.app, "%s", app);
    cp.on = 1, cp.at = at;
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
        FILE *f = fopen(path(m3u), "r");
        char line[1100];
        free_paths();
        while (f && fgets(line, sizeof line, f)) {
            line[strcspn(line, "\r\n")] = 0;
            if (!*line || *line == '#') continue;
            char **g = realloc(q.paths, (size_t)(q.np + 1) * sizeof *g);
            if (!g) break;
            q.paths = g, q.paths[q.np++] = strdup(line);
        }
        if (f) fclose(f);
        logf_("%s answered catplay %u with %d songs", cp.app, seq, q.np);
        q.n = q.np;
        if (!q.np || start_at(0)) free_paths(), q.state = STOPPED, ++q.seq;
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

/* play AT SHUFFLE over ids (n of them): the song at AT first, then on in order, or, shuffled, the rest in a random order */
static int play_ids(const int *ids, int n, int at, int shuffle) {
    if (n <= 0 || at < 0 || at >= n) return -1;
    if (n > QUEUE_MAX) n = QUEUE_MAX;
    memcpy(q.ids, ids, (size_t)n * sizeof *ids);
    q.n = n, q.shuffle = shuffle, q.errors = 0;
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
    free_paths(), cp.on = cp.starting = 0;
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
static int command(const char *verb, const int *ids, int n_ids) {
    int a = 0, b = 0, c = 0;
    if (sscanf(verb, "play %d %d %d", &a, &b, &c) == 3) {
        if (!ids) n_ids = read_list(pending_ids), ids = pending_ids;
        if (c != n_ids) logf_("play names %d songs, the list holds %d", c, n_ids);
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
            player_send("mcl-play", "null");
            q.state = PLAYING, ++q.seq;
            return 0;
        }
        return q.state == STOPPED && q.n ? start_at(q.at) : 0;
    }
    if (src.on && (!strcmp(verb, "next") || !strcmp(verb, "previous"))) return -1; /* a source is one URL */
    if (!strcmp(verb, "next")) return q.at + 1 < q.n ? start_at(q.at + 1) : -1;
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

/* The FIFO: "<seq> <verb>\n" lines (patch/coda_rc_cmdq.h). */
static char cq[2048];
static size_t cq_n;
static void cmdq_read(void) {
    ssize_t got = read(cmdq_fd, cq + cq_n, sizeof cq - 1 - cq_n);
    if (got <= 0) return;
    cq_n += (size_t)got;
    char *p = cq, *nl;
    while ((nl = memchr(p, '\n', cq_n - (size_t)(p - cq)))) {
        *nl = 0;
        char *verb = strchr(p, ' ');
        if (verb && command(verb + 1, 0, 0)) logf_("refused: %s", verb + 1);
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
    int id = q.n && !q.paths ? q.ids[q.order[q.at]] : 0;
    field(t, sizeof t, q.title, encode), field(a, sizeof a, q.artist, encode), field(b, sizeof b, q.album, encode);
    snprintf(out, room, "title=%s%sartist=%s%salbum=%s%sstate=%d%spos=%d%slen=%d%svolume=%d%sid=%d", t, sep, a, sep, b, sep,
             q.state, sep, encode ? q.pos_ms / 1000 : q.pos_ms, sep, q.len_s, sep, q.volume, sep, id);
}

static void now_write(void) {
    char text[2600], tmp[340];
    now_text(text, sizeof text, "\n", 0);
    snprintf(tmp, sizeof tmp, "%s.q2playd", path(now_path_rel));
    FILE *f = fopen(tmp, "w");
    if (!f) return;
    fprintf(f, "%s\n", text);
    if (fclose(f) == 0) rename(tmp, path(now_path_rel)); /* whole or not at all */
}

static struct client { int fd, hello, watch; char in[LINE]; size_t n; int want_ids, got_ids, a, b; } cl[CLIENTS];
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
    q.n = src.n, q.at = src.at, q.shuffle = src.shuffle, q.paths = src.paths, q.np = src.np, src.paths = 0, src.np = 0;
    q.loaded = 0, q.errors = 0;
    q.state = src.state == STOPPED || (why && !strcmp(why, "stop")) || !q.n ? STOPPED : PAUSED;
    q.pos_ms = q.state == PAUSED ? src.pos_ms : 0, q.len_s = q.state == PAUSED ? src.len_s : 0;
    q.title[0] = q.artist[0] = q.album[0] = q.file[0] = q.row_url[0] = 0;
    if (q.n && (q.paths ? path_lookup(q.at) : song_lookup(q.ids[q.order[q.at]])))
        set_cover(q.paths ? -1 : q.ids[q.order[q.at]], q.paths ? q.row_url : q.file);
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
        src.n = q.n, src.at = q.at, src.shuffle = q.shuffle, src.state = q.state, src.pos_ms = q.pos_ms, src.len_s = q.len_s;
        src.paths = q.paths, src.np = q.np, q.paths = 0, q.np = 0;
    } else if (src.holder != slot) tell_holder("taken");
    src.on = 1, src.holder = slot, cp.on = 0;
    q.n = 0, q.at = 0;
    snprintf(q.file, sizeof q.file, "%s", url);
    const char *base = strrchr(url, '/');
    snprintf(q.title, sizeof q.title, "%s", *t ? t : base + 1);
    snprintf(q.artist, sizeof q.artist, "%s", a), snprintf(q.album, sizeof q.album, "%s", b);
    int cut = q.loaded;
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
            int r = command(verb, pending_ids, c->want_ids);
            c->want_ids = 0;
            client_say(c, r ? "err play" : "ok");
        }
        return;
    }
    int a, b, n;
    if (sscanf(line, "play %d %d %d", &a, &b, &n) == 3) {
        if (n <= 0 || n > QUEUE_MAX || a < 0 || a >= n) { client_say(c, "err play"); return; }
        c->want_ids = n, c->got_ids = 0, c->a = a, c->b = b;
        return;
    }
    if (!strcmp(line, "caps")) { /* Vamp API 1's capability words (docs/platform/README.md#capabilities) */
        client_say(c, "caps play toggle pause resume next previous seek volume stop now watch source focus output");
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
        if (cp.on && (wait < 0 || wait > 200)) wait = 200;
        int beat = air.t ? CAST_BEAT_MS : CAST_LOOK_MS; /* AirPlay's tap: its beat, or the look for it */
        if (wait < 0 || wait > beat) wait = beat;
        if (poll(p, (nfds_t)n, wait) < 0 && errno != EINTR) break;
        catplay_poll();
        air_poll();
        if (src.on && cl[src.holder].fd < 0) source_release(src.holder); /* its holder gone (here and after the reads): before a new client takes its slot */
        if (p[0].revents & POLLIN) {
            player = accept4(listen_fd, 0, 0, SOCK_CLOEXEC);
            if (player >= 0) logf_("the player connected");
            if (player >= 0 && way) player_send("mcl-setoutputway", "1"), volume_apply(); /* a player started again: the output it had */
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
        if (q.seq != now_written_seq && t >= now_written_at + NOW_EVERY_MS) {
            now_write(), tell_watchers();
            now_written_seq = q.seq, now_written_at = t;
        }
    }
    if (volume_dirty_at) volume_save();
    unlink(path(sock_rel)), unlink(path(push_rel)), unlink(path(api_rel)), unlink(path(cmdq_rel));
    return 0;
}
