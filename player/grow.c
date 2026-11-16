/* A song still arriving on the card (docs/server-library.md#download-ahead-and-the-streaming-state): a card app
 * with the firmware's "grow" word writes a streamed song in place, at the path its queue names, and says how far it
 * has come in /tmp/q2-<id>.stream, <id> its folder's (/mnt/mmc/.<id>/...):
 *   #Q2STREAM/1	seq=<n>
 *   <wait|get|stall|done|fail>	<bytes on the card>	<total, 0 unknown>	<length ms>	<card path>
 * rewritten every 500 ms while a song arrives. A file whose line says get or stall is read through here: at the end
 * of the bytes there are, the read waits for more (WAIT_FILE names the song meanwhile, for Now Playing's spinner)
 * instead of ending the song, and so does a read after a seek past them. It ends at the file's end once the line
 * says done or fail, the line is gone, the file has not been rewritten for STALE_S (the app is gone), or a stop or
 * a skip (ctl.cut) gives the song up.
 * ponytail: a pause or a seek asked while the read waits is taken once the bytes come; the engine reads between
 * requests. A demuxer that reads the file's end while it opens would wait there for the whole
 * song and start it only then, as before; measured for FLAC (test/player/run.py grow), not yet for MP3, whose demuxer
 * may look for ID3v1 and APE tags at the end. */
#include "q2player.h"
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>
#include <libavformat/avformat.h>
#include "peq.h" /* PEQ_ROOT */

#define CARD "/mnt/mmc/."
#define WAIT_FILE PEQ_ROOT "/tmp/q2player.wait"
enum { IO_BUF = 32768, POLL_US = 100000, STALE_S = 3, STATUS_LINE = 8192 };

struct grow {
    FILE *f;
    AVIOContext *io;
    char status[96]; /* the app's .stream */
    char path[URL_MAX]; /* the card path its lines name: the url from "/mnt/mmc/." on */
    int64_t pos, total;
    int waiting;
};

/* The .stream file for url, into out, and the card path in url; NULL when url is not under an app's folder. */
static const char *status_of(const char *url, char *out, size_t n) {
    const char *card = strstr(url, CARD), *id = card ? card + strlen(CARD) : NULL, *slash = id ? strchr(id, '/') : NULL;
    if (!slash || slash == id || slash - id >= 16) return NULL;
    snprintf(out, n, "%s/tmp/q2-%.*s.stream", PEQ_ROOT, (int)(slash - id), id);
    return card;
}

/* Whether path's line in status says it is still arriving (get or stall, the file fresh), and its total. */
static int arriving(const char *status, const char *path, int64_t *total) {
    struct stat st;
    if (stat(status, &st) || st.st_mtime < time(NULL) - STALE_S) return 0;
    FILE *f = fopen(status, "rb");
    if (!f) return 0;
    char *line = malloc(STATUS_LINE);
    int yes = 0, header = 1;
    while (line && fgets(line, STATUS_LINE, f)) {
        line[strcspn(line, "\r\n")] = 0;
        if (header) {
            if (strncmp(line, "#Q2STREAM/1", 11)) break;
            header = 0;
            continue;
        }
        char *f5[5], *p = line;
        int k = 0;
        for (; k < 5 && p; ++k) {
            f5[k] = p;
            if ((p = strchr(p, '\t'))) *p++ = 0;
        }
        if (k < 5) continue;
        /* the app escapes %, tab, LF and CR in a path; a card path here has none of them but % */
        char want[URL_MAX];
        size_t o = 0;
        for (const char *c = f5[4]; *c && o + 1 < sizeof want; ++c) {
            unsigned v;
            if (c[0] == '%' && sscanf(c + 1, "%2x", &v) == 1) want[o++] = (char)v, c += 2;
            else want[o++] = *c;
        }
        want[o] = 0;
        if (strcmp(want, path)) continue;
        yes = !strcmp(f5[0], "get") || !strcmp(f5[0], "stall");
        if (total) *total = strtoll(f5[2], NULL, 10);
        break;
    }
    free(line);
    fclose(f);
    return yes;
}

int grow_arriving(const char *url) {
    char status[96];
    const char *path = status_of(url, status, sizeof status);
    return path && arriving(status, path, NULL);
}

static void wait_mark(struct grow *g, int on) {
    if (on == g->waiting) return;
    g->waiting = on;
    if (!on) {
        unlink(WAIT_FILE);
        return;
    }
    char tmp[sizeof WAIT_FILE + 4];
    snprintf(tmp, sizeof tmp, "%s.tmp", WAIT_FILE);
    FILE *f = fopen(tmp, "wb");
    if (!f) return;
    fprintf(f, "%s\n", g->path);
    if (fclose(f) == 0) rename(tmp, WAIT_FILE);
}

static int grow_read(void *opaque, uint8_t *to, int n) {
    struct grow *g = opaque;
    for (;;) {
        if (fseeko(g->f, g->pos, SEEK_SET)) return AVERROR(EIO);
        size_t got = fread(to, 1, n, g->f);
        if (got) {
            wait_mark(g, 0);
            g->pos += got;
            return (int)got;
        }
        clearerr(g->f);
        if (!arriving(g->status, g->path, &g->total)) {
            wait_mark(g, 0);
            return AVERROR_EOF;
        }
        if (__atomic_load_n(&ctl.cut, __ATOMIC_RELAXED)) { /* a stop or a skip: the engine takes it */
            wait_mark(g, 0);
            return AVERROR_EXIT;
        }
        wait_mark(g, 1);
        usleep(POLL_US);
    }
}

static int64_t grow_seek(void *opaque, int64_t offset, int whence) {
    struct grow *g = opaque;
    if (whence & AVSEEK_SIZE) { /* the whole song's, as the app says it, while it arrives */
        struct stat st;
        int64_t now = fstat(fileno(g->f), &st) ? -1 : st.st_size;
        return g->total > now ? g->total : now;
    }
    whence &= ~AVSEEK_FORCE;
    if (whence == SEEK_CUR) offset += g->pos;
    else if (whence == SEEK_END) {
        int64_t size = grow_seek(g, 0, AVSEEK_SIZE);
        if (size < 0) return AVERROR(EINVAL);
        offset += size;
    } else if (whence != SEEK_SET) return AVERROR(EINVAL);
    if (offset < 0) return AVERROR(EINVAL);
    return g->pos = offset;
}

struct grow *grow_open(const char *url, int *error) {
    struct grow *g = calloc(1, sizeof *g);
    uint8_t *buf = NULL;
    *error = ERR_MISSING;
    const char *path = g ? status_of(url, g->status, sizeof g->status) : NULL;
    if (!path || !(g->f = fopen(url, "rb"))) goto fail;
    snprintf(g->path, sizeof g->path, "%s", path);
    arriving(g->status, g->path, &g->total);
    *error = ERR_OTHER;
    if (!(buf = av_malloc(IO_BUF)) || !(g->io = avio_alloc_context(buf, IO_BUF, 0, g, grow_read, NULL, grow_seek))) {
        av_free(buf);
        goto fail;
    }
    *error = ERR_NONE;
    return g;
fail:
    grow_close(g);
    return NULL;
}

struct AVIOContext *grow_avio(struct grow *g) { return g->io; }

void grow_close(struct grow *g) {
    if (!g) return;
    wait_mark(g, 0);
    if (g->io) av_freep(&g->io->buffer), avio_context_free(&g->io);
    if (g->f) fclose(g->f);
    free(g);
}
