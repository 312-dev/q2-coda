/* A song's lyrics from the library, Q2LIB/1's `lyrics ID` on lib.sock (docs/platform/library.md#q2lib1-the-library-socket
 * on feat/vamp-os-base): a header `lyrics id=ID kind=word|line|plain source=file|tag lines=N`, a `line at=MS text=T
 * words=MS:BYTE,...` each in time order, then `ok`; `err none` for a song with none. Asked on a connection of its own
 * when a page wants another song's, read as it comes (ui_watch), so the loop never waits on q2libd.
 *
 * The text is taken as strict UTF-8: each ill-formed sequence (a GB18030 file q2libd could not convert arrives as its
 * raw bytes) becomes one U+FFFD, the maximal subpart rule of Unicode's chapter 3 (as browsers decode), never bytes read
 * as Latin-1 or dropped; a word mark's byte offset follows its byte into the cleaned text. */
#include <errno.h>
#include <poll.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <unistd.h>

#include "q2ui.h"

static lyrics_t cur = { .kind = LYR_NONE, .id = -1 };
static unsigned seq;
static int fd = -1;
static char *buf;
static size_t len, cap;
static int lines_room, marks_room;
static size_t text_room;

/* Bytes in[0..n) as well-formed UTF-8 appended to cur.text; map[i] (when map) the cleaned offset of in[i]. */
static int clean(const unsigned char *in, size_t n, size_t *map) {
    for (size_t i = 0; i < n;) {
        unsigned c = in[i];
        size_t need = c < 0x80 ? 0 : c >= 0xc2 && c <= 0xdf ? 1 : c >= 0xe0 && c <= 0xef ? 2 : c >= 0xf0 && c <= 0xf4 ? 3 : 4;
        size_t k = 1;
        if (need && need < 4) {
            /* the second byte's range narrows for E0, ED, F0 and F4 (no overlongs, surrogates or past U+10FFFF) */
            unsigned lo = c == 0xe0 ? 0xa0 : c == 0xf0 ? 0x90 : 0x80, hi = c == 0xed ? 0x9f : c == 0xf4 ? 0x8f : 0xbf;
            while (k <= need && i + k < n && in[i + k] >= (k == 1 ? lo : 0x80) && in[i + k] <= (k == 1 ? hi : 0xbf)) ++k;
        }
        int good = need < 4 && k == need + 1;
        size_t out = good ? k : 3;
        if (cur.text_len + out + 1 > text_room) {
            size_t room = text_room ? text_room * 2 : 4096;
            while (room < cur.text_len + out + 1) room *= 2;
            char *t = realloc(cur.text, room);
            if (!t) return -1;
            cur.text = t, text_room = room;
        }
        for (size_t j = 0; map && j < k; ++j) map[i + j] = cur.text_len;
        if (good)
            memcpy(cur.text + cur.text_len, in + i, k);
        else
            memcpy(cur.text + cur.text_len, "\xef\xbf\xbd", 3);
        cur.text_len += out, i += k;
    }
    cur.text[cur.text_len] = 0;
    return 0;
}

static void reset(int id, int kind) {
    free(cur.line), free(cur.mark), free(cur.text);
    cur = (lyrics_t){ .kind = kind, .id = id };
    lines_room = marks_room = 0, text_room = 0;
}
static void finish(int kind) {
    if (fd >= 0) ui_unwatch(fd), close(fd);
    fd = -1, len = 0;
    if (kind == LYR_NONE) reset(cur.id, LYR_NONE);
    else cur.kind = kind;
    ++seq;
    ui_mark_all();
}

/* one `line at=MS text=T words=MS:BYTE,...` */
static int add_line(const char *l, int kind) {
    char at[16];
    size_t tn = strlen(l);
    char *raw = malloc(tn + 1);
    size_t *map = malloc((tn + 1) * sizeof *map);
    if (!raw || !map || !api_field(l, "at", at, sizeof at) || !api_field(l, "text", raw, tn + 1))
        return free(raw), free(map), -1;
    if (cur.n == lines_room) {
        int room = lines_room ? lines_room * 2 : 64;
        lyr_line *g = realloc(cur.line, (size_t)room * sizeof *g);
        if (!g) return free(raw), free(map), -1;
        cur.line = g, lines_room = room;
    }
    lyr_line *o = &cur.line[cur.n];
    size_t rn = strlen(raw); /* a NUL the text cannot hold: percent-encoding never sends one */
    *o = (lyr_line){ .at = atoi(at), .text = (unsigned)cur.text_len, .mark = (unsigned)cur.marks };
    int bad = clean((const unsigned char *)raw, rn, map);
    map[rn] = cur.text_len;
    o->len = (unsigned)(cur.text_len - o->text);
    /* word marks, kept only where the header said the song is word-timed */
    const char *w = strstr(l, " words=");
    for (w = w ? w + 7 : 0; !bad && kind == LYR_WORD && w && *w && *w != ' ';) {
        char *end;
        long ms = strtol(w, &end, 10);
        if (*end != ':') break;
        long at_byte = strtol(end + 1, &end, 10);
        if (at_byte >= 0 && (size_t)at_byte <= rn) {
            if (cur.marks == marks_room) {
                int room = marks_room ? marks_room * 2 : 128;
                lyr_mark *g = realloc(cur.mark, (size_t)room * sizeof *g);
                if (!g) break;
                cur.mark = g, marks_room = room;
            }
            cur.mark[cur.marks++] = (lyr_mark){ (int)ms, (unsigned)(map[at_byte] - o->text) };
            ++o->marks;
        }
        w = *end == ',' ? end + 1 : end;
    }
    free(raw), free(map);
    if (bad) return -1;
    ++cur.n;
    return 0;
}

static int pending_kind;
static void take_line(char *l) {
    char v[16];
    if (!strcmp(l, "Q2LIB/1 ok")) return;
    if (!strncmp(l, "err", 3)) {
        finish(LYR_NONE);
        return;
    }
    if (!strncmp(l, "lyrics ", 7)) {
        api_field(l, "kind", v, sizeof v) || (*v = 0);
        pending_kind = !strcmp(v, "word") ? LYR_WORD : !strcmp(v, "line") ? LYR_LINE : LYR_PLAIN;
        return;
    }
    if (!strncmp(l, "line ", 5)) {
        if (add_line(l, pending_kind)) finish(LYR_NONE);
        return;
    }
    if (!strcmp(l, "ok")) finish(cur.n ? pending_kind : LYR_NONE);
}

static void readable(void) {
    for (;;) {
        if (len + 4096 > cap) {
            size_t room = cap ? cap * 2 : 16384;
            char *b = room <= LYRICS_ANSWER_MAX ? realloc(buf, room + 1) : 0;
            if (!b) { /* past any answer of 64 KB of text */
                finish(LYR_NONE);
                return;
            }
            buf = b, cap = room;
        }
        ssize_t r = recv(fd, buf + len, cap - len, MSG_DONTWAIT);
        if (r < 0 && errno == EINTR) continue;
        if (r < 0 && (errno == EAGAIN || errno == EWOULDBLOCK)) return;
        if (r <= 0) { /* q2libd went away mid-answer */
            finish(LYR_NONE);
            return;
        }
        len += (size_t)r;
        char *nl;
        while (fd >= 0 && (nl = memchr(buf, '\n', len))) {
            *nl = 0;
            take_line(buf);
            if (fd < 0) return;
            len -= (size_t)(nl + 1 - buf);
            memmove(buf, nl + 1, len);
        }
    }
}

const lyrics_t *lyrics_for(int id) {
    if (id == cur.id) return &cur;
    if (fd >= 0) ui_unwatch(fd), close(fd), fd = -1, len = 0;
    reset(id, LYR_NONE);
    ++seq;
    if (!api_vamp || id <= 0) return &cur;
    char path[300], ask[64];
    struct sockaddr_un a = { .sun_family = AF_UNIX };
    snprintf(path, sizeof path, "%s/lib.sock", api_dir);
    if (strlen(path) >= sizeof a.sun_path) return &cur;
    strcpy(a.sun_path, path);
    fd = socket(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0);
    if (fd < 0) return &cur;
    int n = snprintf(ask, sizeof ask, "Q2LIB/1\nlyrics %d\n", id);
    if (connect(fd, (struct sockaddr *)&a, sizeof a) || send(fd, ask, (size_t)n, MSG_NOSIGNAL) != n) {
        close(fd), fd = -1;
        return &cur;
    }
    cur.kind = LYR_ASKING, pending_kind = LYR_PLAIN;
    ui_watch(fd, readable);
    return &cur;
}

/* A script's wait for an answer asked for (main.c's `api`): up to ms of real time */
void lyrics_wait(unsigned ms) {
    for (uint64_t end = mono_us() + (uint64_t)ms * 1000u; fd >= 0 && mono_us() < end;) {
        struct pollfd p = { fd, POLLIN, 0 };
        if (poll(&p, 1, 5) > 0) readable();
    }
}

unsigned lyrics_seq(void) { return seq; }
const lyrics_t *lyrics_held(void) { return &cur; }

/* The line playing at ms: the last whose start is at or before it, the first of the lines that start with it (a line
 * and its translation); -1 before the first, or for untimed lyrics. */
int lyrics_line_at(const lyrics_t *l, int ms) {
    int at = -1;
    if (l->kind != LYR_LINE && l->kind != LYR_WORD) return -1;
    for (int i = 0; i < l->n && l->line[i].at <= ms; ++i)
        if (l->line[i].at >= 0 && (at < 0 || l->line[i].at != l->line[at].at)) at = i;
    return at;
}
