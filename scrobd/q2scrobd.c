/* q2scrobd: Vamp OS's scrobble log (docs/vamp-os-services.md#scrobbling). It watches Q2PLAY/1 (/run/vamp/play.sock)
 * and, for each song heard long enough, appends a line to the card's /mnt/mmc/.scrobbler.log in Rockbox's
 * AudioScrobbler 1.1 format, as today's Vamp does (docs/internals.md#scrobbling), for any .scrobbler.log uploader.
 *
 * A song is a now line's id (a new play when the id changes or pos goes back to 0); the seconds heard grow while
 * state=1 and pos moves on by 1 to 3 s (a seek or a pause adds nothing). A listen: half the song or 240 s, whichever is
 * less, for a song over 30 s; with [SCROBBLE] PERCENT=N in /mnt/mmc/.scrobble.ini, N % of the song less 2 s. Once a
 * play. The artist (or with [SCROBBLE] ALBUM_ARTIST=1 the album artist, when there is one) and the track number come
 * from the song's library row (Library 1: library.db's songtable, catalogue.db's servertable); the title, artist and
 * album otherwise from the now line. No artist, or a clock before 2020: not logged. When Q2PLAY/1 is not there it
 * tries again every 2 s. --root prefixes every path, for test/scrobd.py. */
#define _GNU_SOURCE
#include <ctype.h>
#include <errno.h>
#include <poll.h>
#include <signal.h>
#include <sqlite3.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <time.h>
#include <unistd.h>

#define LISTEN_MIN_S 30
#define LISTEN_MAX_S 240
#define CAT_ID_BASE 0x40000000u
#define T 512

static char root[256];
static volatile sig_atomic_t stopping;

static char *path(const char *rel) {
    static char b[2][700];
    static int i;
    i ^= 1;
    snprintf(b[i], sizeof b[i], "%s%s", root, rel);
    return b[i];
}

static void on_term(int s) { (void)s, stopping = 1; }

static int hexv(int c) { return isdigit(c) ? c - '0' : isxdigit(c) ? (tolower(c) - 'a' + 10) : -1; }

/* a now field's value, percent-decoded (docs/platform/README.md#the-common-shape) */
static void field(const char *line, const char *key, char *out, size_t room) {
    char k[32];
    snprintf(k, sizeof k, " %s=", key);
    const char *p = strstr(line, k);
    size_t n = 0;
    if (p) {
        for (p += strlen(k); *p && *p != ' ' && n + 1 < room; ++p) {
            if (*p == '%' && hexv(p[1]) >= 0 && hexv(p[2]) >= 0) out[n++] = (char)(hexv(p[1]) << 4 | hexv(p[2])), p += 2;
            else out[n++] = *p;
        }
    }
    out[n] = 0;
}

static int config_int(const char *section, const char *key) { /* .scrobble.ini's [section] key=, 0 when missing */
    FILE *f = fopen(path("/mnt/mmc/.scrobble.ini"), "r");
    char line[300], sec[64] = "";
    int v = 0;
    while (f && fgets(line, sizeof line, f)) {
        char *s = line + strspn(line, " \t");
        s[strcspn(s, "\r\n")] = 0;
        if (*s == '[') snprintf(sec, sizeof sec, "%.*s", (int)strcspn(s + 1, "]"), s + 1);
        else if (!strcasecmp(sec, section) && !strncasecmp(s, key, strlen(key)) && s[strlen(key)] == '=') v = atoi(s + strlen(key) + 1);
    }
    if (f) fclose(f);
    return v;
}

/* the library row's album artist and track for id (0 and "" when none) */
static void row_of(int id, char *aa, size_t room, int *track) {
    sqlite3 *db;
    sqlite3_stmt *st;
    int server = (unsigned)id >= CAT_ID_BASE;
    aa[0] = 0, *track = 0;
    if (sqlite3_open_v2(path(server ? "/mnt/mmc/.vamp/catalogue.db" : "/mnt/data/vamp/library.db"), &db, SQLITE_OPEN_READONLY, 0) == SQLITE_OK &&
        sqlite3_prepare_v2(db, server ? "select albumsonger, track from servertable where id = ?" : "select albumsonger, track from songtable where id = ?",
                           -1, &st, 0) == SQLITE_OK) {
        sqlite3_bind_int(st, 1, id);
        if (sqlite3_step(st) == SQLITE_ROW) {
            const char *a = (const char *)sqlite3_column_text(st, 0);
            snprintf(aa, room, "%s", a ? a : "");
            *track = sqlite3_column_int(st, 1);
        }
        sqlite3_finalize(st);
    }
    sqlite3_close(db);
}

static char *tag(char *o, const char *s) { /* a column: tabs and line ends made spaces, then the tab */
    for (; *s; ++s) *o++ = *s == '\t' || *s == '\n' || *s == '\r' ? ' ' : *s;
    *o++ = '\t';
    return o;
}

static struct { int id, pos, heard, len, sent; char title[T], artist[T], album[T]; } s = {.id = -1};

static void log_listen(void) {
    char aa[T], line[4 * T + 64], *o = line;
    int track;
    time_t now = time(0);
    row_of(s.id, aa, sizeof aa, &track);
    const char *artist = config_int("SCROBBLE", "ALBUM_ARTIST") == 1 && *aa ? aa : s.artist;
    if (!*artist) return (void)fprintf(stderr, "q2scrobd: %s has no artist: not logged\n", s.title);
    if (now < 1577836800) return (void)fprintf(stderr, "q2scrobd: the clock is not set: not logged\n");
    FILE *f = fopen(path("/mnt/mmc/.scrobbler.log"), "a");
    if (!f) return (void)fprintf(stderr, "q2scrobd: .scrobbler.log: %s\n", strerror(errno));
    if (ftell(f) == 0) fputs("#AUDIOSCROBBLER/1.1\n#TZ/UTC\n#CLIENT/Vamp\n", f);
    o = tag(o, artist), o = tag(o, s.album), o = tag(o, s.title);
    if (track > 0) o += sprintf(o, "%d", track);
    o += sprintf(o, "\t%d\tL\t%lld\t\n", s.len, (long long)(now - s.heard));
    fwrite(line, 1, (size_t)(o - line), f);
    fclose(f);
    fprintf(stderr, "q2scrobd: logged %s by %s\n", s.title, artist);
}

static void now_line(const char *line) {
    char v[T];
    field(line, "id", v, sizeof v);
    int id = atoi(v), pos, state, len;
    field(line, "pos", v, sizeof v), pos = atoi(v);
    field(line, "state", v, sizeof v), state = atoi(v);
    field(line, "len", v, sizeof v), len = atoi(v);
    if (id != s.id || (pos < s.pos && pos <= 1)) { /* a new play */
        s.id = id, s.heard = 0, s.sent = 0;
    } else if (state == 1 && pos - s.pos >= 1 && pos - s.pos <= 3) {
        s.heard += pos - s.pos;
    }
    s.pos = pos, s.len = len;
    field(line, "title", s.title, sizeof s.title), field(line, "artist", s.artist, sizeof s.artist), field(line, "album", s.album, sizeof s.album);
    int pct = config_int("SCROBBLE", "PERCENT"), listen = len / 2 < LISTEN_MAX_S ? len / 2 : LISTEN_MAX_S;
    if (pct > 0) listen = (len - 2) * pct / 100;
    if (!s.sent && id && len > LISTEN_MIN_S && s.heard >= listen) s.sent = 1, log_listen();
}

int main(int argc, char **argv) {
    for (int i = 1; i < argc; ++i)
        if (!strcmp(argv[i], "--root") && i + 1 < argc) snprintf(root, sizeof root, "%s", argv[++i]);
        else return fprintf(stderr, "usage: q2scrobd [--root DIR]\n"), 2;
    struct sigaction sa = {.sa_handler = on_term};
    sigaction(SIGTERM, &sa, 0), sigaction(SIGINT, &sa, 0);
    signal(SIGPIPE, SIG_IGN);
    while (!stopping) {
        struct sockaddr_un a = {.sun_family = AF_UNIX};
        snprintf(a.sun_path, sizeof a.sun_path, "%s", path("/run/vamp/play.sock"));
        int fd = socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0);
        if (fd < 0 || connect(fd, (struct sockaddr *)&a, sizeof a) || write(fd, "Q2PLAY/1\nwatch\nnow\n", 19) != 19) {
            if (fd >= 0) close(fd);
            sleep(2);
            continue;
        }
        char buf[8192];
        size_t n = 0;
        for (;;) {
            ssize_t got = read(fd, buf + n, sizeof buf - 1 - n);
            if (got <= 0) break;
            n += (size_t)got, buf[n] = 0;
            char *p = buf, *nl;
            while ((nl = strchr(p, '\n'))) {
                *nl = 0;
                if (!strncmp(p, "now ", 4)) now_line(p + 3);
                p = nl + 1;
            }
            n = strlen(p), memmove(buf, p, n + 1);
            if (n >= sizeof buf - 1) n = 0;
        }
        close(fd);
        if (!stopping) sleep(2);
    }
    return 0;
}
