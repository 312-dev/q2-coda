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
 *     (docs/playd.md#q2play1), the interface the UI is meant to move to.
 *
 * One thread, one poll loop. The player's status is asked for (player-status, media-info) when its push datagram
 * says it changed (/tmp/q2player.push, docs/internals.md#player-status-push) and once a second while a song is
 * loaded, never while nothing is. --root prefixes every path, for test/playd.py. */
#define _GNU_SOURCE
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
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/un.h>
#include <time.h>
#include <unistd.h>

#define QUEUE_MAX 20000  /* ids one play may queue (q2ui's PLAY_MAX, coda_rc_shim.c CR_LIST_MAX) */
#define CLIENTS 8        /* Q2PLAY/1 connections at once */
#define LINE 512         /* a Q2PLAY/1 line */
#define RX 4096          /* the player's messages waiting to be parsed */
#define NOW_EVERY_MS 500 /* the now file's position, rewritten at most this often */
#define POLL_MS 1000     /* the player asked for its status this often while a song is loaded, besides its pushes */
#define ERRORS_MAX 20    /* songs in a row that do not open before the queue stops */

static char root[256], db_path[300];
static int dac = -1, dac_missing;
static const char *now_path_rel = "/tmp/coda-rc.now", *list_rel = "/tmp/coda-rc.list", *cmdq_rel = "/tmp/coda-rc.cmdq",
                  *sock_rel = "/tmp/sock_play", *push_rel = "/tmp/q2player.push", *api_rel = "/run/vamp/play.sock";

static char *path(const char *rel) { /* rel under --root, in one of four buffers (no two calls in an expression need more) */
    static char b[4][320];
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
    unsigned seq;                /* moves with every change of what is written */
} q = {.volume = 24};

static unsigned long long now_written_at;
static unsigned now_written_seq = ~0u;

/* ---- the library: stock's songtable (docs/internals.md#library) ---- */

static int song_lookup(int id) {
    sqlite3 *db;
    sqlite3_stmt *st;
    int ok = 0;
    q.title[0] = q.artist[0] = q.album[0] = q.file[0] = 0;
    if (sqlite3_open_v2(db_path, &db, SQLITE_OPEN_READONLY, 0) != SQLITE_OK) {
        logf_("cannot read %s", db_path);
        sqlite3_close(db);
        return 0;
    }
    if (sqlite3_prepare_v2(db, "select fileurl, name, songer, album from songtable where id = ?", -1, &st, 0) == SQLITE_OK) {
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

/* set-songurl or set-nexturl as demo's mclStartPlayer builds it (docs/player.md#commands): the start, the cache in
 * KB by the file's kind and its fill percent, Gapless off (no preload in this slice), the Q2's hardware type 17. */
static void send_song(const char *cmd, const char *file) {
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
    snprintf(arg, sizeof arg, "%s|ss=0|%d|%d|0|17|90", url, cache, fill);
    player_send(cmd, arg);
}

/* The song at q.order[q.at] to the player: set-songurl when nothing plays, set-nexturl to cut the one that does. */
static int start_at(int at) {
    while (at >= 0 && at < q.n) {
        q.at = at;
        if (song_lookup(q.ids[q.order[at]])) {
            int cut = q.loaded;
            send_song(cut ? "set-nexturl" : "set-songurl", q.file);
            q.expect_cut = cut;
            q.loaded = 1, q.state = PLAYING, q.pos_ms = 0, q.len_s = 0;
            ++q.seq;
            return 0;
        }
        logf_("no song %d in the library", q.ids[q.order[at]]);
        if (++q.errors >= ERRORS_MAX) break;
        ++at;
    }
    return -1;
}

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
    logf_("song %d %s", q.n ? q.ids[q.order[q.at]] : 0, error ? "did not play" : "ended");
    q.loaded = 0;
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
 * volume itself; the driver maps that to the chips' attenuation. Sent at the start and at each change. */
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
    free(t);
}

/* VOLUME=q.volume in [PLAYSET], the rest of the file as it was */
static void volume_save(void) {
    char *t = read_all(path("/mnt/data/config.ini")), tmp[340], line[32];
    if (!t) return;
    snprintf(line, sizeof line, "VOLUME=%d", q.volume);
    char *sec = strstr(t, "[PLAYSET]"), *at = 0, *end = 0;
    for (char *l = sec ? strchr(sec, '\n') : 0; l && *++l && *l != '['; l = strchr(l, '\n'))
        if (!strncmp(l, "VOLUME=", 7)) { at = l, end = l + strcspn(l, "\r\n"); break; }
    snprintf(tmp, sizeof tmp, "%s.q2playd", path("/mnt/data/config.ini"));
    FILE *f = fopen(tmp, "w");
    if (f) {
        if (at) fprintf(f, "%.*s%s%s", (int)(at - t), t, line, end);
        else if (sec) { /* no VOLUME line: one after the section's header */
            char *nl = strchr(sec, '\n');
            if (nl) fprintf(f, "%.*s%s\n%s", (int)(nl + 1 - t), t, line, nl + 1);
            else fprintf(f, "%s\n%s\n", t, line);
        } else fprintf(f, "%s%s[PLAYSET]\n%s\n", t, *t && t[strlen(t) - 1] != '\n' ? "\n" : "", line);
        if (!fclose(f)) rename(tmp, path("/mnt/data/config.ini"));
    }
    free(t);
}

/* ---- commands: the same verbs from the FIFO and from Q2PLAY/1 ---- */

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
        if (q.state == PAUSED) {
            player_send("mcl-play", "null");
            q.state = PLAYING, ++q.seq;
            return 0;
        }
        return q.state == STOPPED && q.n ? start_at(q.at) : 0;
    }
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
        dac_volume();
        return 0;
    }
    if (!strcmp(verb, "stop")) {
        stop();
        return 0;
    }
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
 * (docs/playd.md#q2play1), every byte below 0x21, '%', '"', '=' and 0x7f as %XX, so a value holds no space, no line
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

static void now_text(char *out, size_t room, const char *sep, int encode) {
    char t[800], a[800], b[800];
    int id = q.n ? q.ids[q.order[q.at]] : 0;
    field(t, sizeof t, q.title, encode), field(a, sizeof a, q.artist, encode), field(b, sizeof b, q.album, encode);
    snprintf(out, room, "title=%s%sartist=%s%salbum=%s%sstate=%d%spos=%d%slen=%d%svolume=%d%sid=%d", t, sep, a, sep, b, sep,
             q.state, sep, q.pos_ms, sep, q.len_s, sep, q.volume, sep, id);
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

/* One Q2PLAY/1 line (docs/playd.md#q2play1). */
static void client_line(struct client *c, char *line) {
    if (!c->hello) {
        if (strcmp(line, "Q2PLAY/1")) { client_say(c, "err version"); close(c->fd), c->fd = -1; return; }
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
    if (!strcmp(line, "now")) {
        char text[2600];
        now_text(text, sizeof text, " ", 1);
        client_say(c, "now %s", text);
    } else if (!strcmp(line, "watch")) {
        c->watch = 1;
        client_say(c, "ok");
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
        else return fprintf(stderr, "usage: q2playd [--root DIR] [--db FILE]\n"), 2;
    }
    if (!*db_path) snprintf(db_path, sizeof db_path, "%s/mnt/data/database.db", root);
    signal(SIGPIPE, SIG_IGN);
    signal(SIGTERM, on_term);
    signal(SIGINT, on_term);
    for (int i = 0; i < CLIENTS; ++i) cl[i].fd = -1;
    listen_fd = listen_unix(path(sock_rel), SOCK_STREAM, 1);
    push_fd = listen_unix(path(push_rel), SOCK_DGRAM, 0);
    mkdir(path("/run/vamp"), 0755);
    api_fd = listen_unix(path(api_rel), SOCK_STREAM, CLIENTS);
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
        if (poll(p, (nfds_t)n, wait) < 0 && errno != EINTR) break;
        if (p[0].revents & POLLIN) {
            player = accept4(listen_fd, 0, 0, SOCK_CLOEXEC);
            if (player >= 0) logf_("the player connected");
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
