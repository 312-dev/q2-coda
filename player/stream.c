/* A song's bytes when they are not simply a file on the card: a URL (DLNA, the phone's push, Tidal), read through
 * FFmpeg's http and, for https, its tls over mbedTLS with the server's name and chain checked against the bundle Q2
 * Pod's hciplayer checks against (docs/internals.md#stock-https); read ahead into a cache of demo's size; and QQ
 * Music's decryption (qq.c) on top. libavformat reads it through an AVIOContext of its own.
 *
 * The cache is MPlayer's, as hciplayer has it (docs/player.md#sources): `C` KB (`C` below 0, as demo sends in a
 * preload and from some queues, is 3072, MPlayer's size for an HTTP stream; 0 none), the song held back until `P`
 * percent of it is filled or the stream has ended, then filled by a thread of its own as the song is read, keeping up
 * to half of it behind the read position for a seek back. get_cache_buffer's answer is MPlayer's cache_fill_status:
 * what is held ahead of the read position as a percent of the cache, 0 until the first fill is done, and left as it
 * was when the stream ends. A seek inside what is held, or ahead of it by less than a quarter of the cache, is
 * served from it; any other seek starts the transfer again from there. */
#include "q2player.h"
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <libavformat/avformat.h>
#include <libavutil/opt.h>
#include "peq.h" /* PEQ_ROOT */

#define CA_FILE PEQ_ROOT "/etc/ssl/certs/ca-certificates.crt" /* tools/build.py STOCK_CA */
enum { AUTO_KB = 3072, CHUNK = 32768, IO_BUF = 32768 };

struct stream {
    AVIOContext *in, *io;
    struct qq *qq;
    const int *cancel;
    int64_t size; /* the file's length, below 0 when the server does not say */
    /* the cache: ring holds the file's bytes [lo, hi) at their offset modulo cap; at is where libavformat reads */
    uint8_t *ring;
    int64_t cap, lo, hi, at, seek_to;
    int threaded, eof, failed, stop, filled, fill;
    pthread_t thread;
    pthread_mutex_t mu;
    pthread_cond_t more, room;
    int64_t pos; /* uncached: the offset of the next byte read from in */
};

static pthread_mutex_t current_mu = PTHREAD_MUTEX_INITIALIZER;
static struct stream *current; /* the playing song's, whose fill get_cache_buffer reports */

int stream_is_url(const char *url) { return !strncmp(url, "http://", 7) || !strncmp(url, "https://", 8); }

static int interrupted(void *opaque) {
    const struct stream *s = opaque;
    const int *cancel = __atomic_load_n(&s->cancel, __ATOMIC_RELAXED);
    return __atomic_load_n(&s->stop, __ATOMIC_RELAXED) || (cancel && __atomic_load_n(cancel, __ATOMIC_RELAXED));
}

static void ring_put(struct stream *s, const uint8_t *data, int n) {
    int64_t at = s->hi % s->cap;
    int first = (int)(n < s->cap - at ? n : s->cap - at);
    memcpy(s->ring + at, data, first);
    memcpy(s->ring, data + first, n - first);
}

static void ring_get(struct stream *s, int64_t from, uint8_t *data, int n) {
    int64_t at = from % s->cap;
    int first = (int)(n < s->cap - at ? n : s->cap - at);
    memcpy(data, s->ring + at, first);
    memcpy(data + first, s->ring, n - first);
}

/* The fill thread: keeps reading while the ring has room for a chunk beyond what is ahead and what is kept behind. */
static void *fill_thread(void *arg) {
    struct stream *s = arg;
    uint8_t *chunk = malloc(CHUNK);
    pthread_mutex_lock(&s->mu);
    while (chunk && !s->stop) {
        if (s->seek_to >= 0) {
            int64_t to = s->seek_to;
            s->seek_to = -1;
            pthread_mutex_unlock(&s->mu);
            int64_t r = avio_seek(s->in, to, SEEK_SET);
            pthread_mutex_lock(&s->mu);
            if (s->seek_to >= 0) continue; /* another seek came meanwhile */
            s->lo = s->hi = to, s->eof = 0, s->failed = r < 0;
            pthread_cond_broadcast(&s->more);
            continue;
        }
        int64_t behind = s->at - s->lo, ahead = s->hi - s->at;
        if (behind > s->cap / 2) behind = s->cap / 2;
        if (behind < 0) behind = 0;
        if (ahead < 0) ahead = 0;
        int64_t room = s->cap - ahead - behind;
        if (s->eof || s->failed || room < CHUNK) {
            pthread_cond_wait(&s->room, &s->mu);
            continue;
        }
        pthread_mutex_unlock(&s->mu);
        int n = avio_read(s->in, chunk, CHUNK);
        pthread_mutex_lock(&s->mu);
        if (s->seek_to >= 0) continue; /* what was read is from before the seek */
        if (n > 0) {
            ring_put(s, chunk, n);
            s->hi += n;
            if (s->hi - s->lo > s->cap) s->lo = s->hi - s->cap;
            if (s->filled) s->fill = (int)((s->hi - s->at) * 100 / s->cap);
        } else if (n == AVERROR_EOF || n == 0) {
            s->eof = 1;
        } else {
            s->failed = 1;
        }
        pthread_cond_broadcast(&s->more);
    }
    pthread_mutex_unlock(&s->mu);
    free(chunk);
    return NULL;
}

static int wait_more(struct stream *s) {
    struct timespec t;
    clock_gettime(CLOCK_REALTIME, &t);
    t.tv_nsec += 100000000;
    if (t.tv_nsec >= 1000000000) t.tv_sec++, t.tv_nsec -= 1000000000;
    pthread_cond_timedwait(&s->more, &s->mu, &t);
    return !interrupted(s);
}

static int cached_read(void *opaque, uint8_t *buf, int n) {
    struct stream *s = opaque;
    pthread_mutex_lock(&s->mu);
    while (s->at >= s->hi && !s->eof && !s->failed && wait_more(s)) {}
    int64_t from = s->at;
    int got = s->at < s->hi ? (int)(n < s->hi - s->at ? n : s->hi - s->at) : 0;
    if (got) ring_get(s, from, buf, got), s->at += got;
    int end = !got && (s->eof || s->failed);
    pthread_cond_signal(&s->room);
    pthread_mutex_unlock(&s->mu);
    if (!got) return end ? AVERROR_EOF : AVERROR_EXIT;
    return s->qq ? qq_decrypt(s->qq, from, buf, got) : got;
}

static int64_t cached_seek(void *opaque, int64_t offset, int whence) {
    struct stream *s = opaque;
    if (whence & AVSEEK_SIZE) return s->size >= 0 ? s->size : AVERROR(ENOSYS);
    whence &= ~AVSEEK_FORCE;
    if (whence == SEEK_CUR) offset += s->at;
    else if (whence == SEEK_END) {
        if (s->size < 0) return AVERROR(ENOSYS);
        offset += s->size;
    } else if (whence != SEEK_SET) return AVERROR(EINVAL);
    if (offset < 0) return AVERROR(EINVAL);
    pthread_mutex_lock(&s->mu);
    if (offset < s->lo || offset > s->hi + s->cap / 4) {
        s->seek_to = offset;
        s->lo = s->hi = offset;
        s->eof = s->failed = 0;
    }
    s->at = offset;
    pthread_cond_signal(&s->room);
    pthread_mutex_unlock(&s->mu);
    return offset;
}

static int plain_read(void *opaque, uint8_t *buf, int n) {
    struct stream *s = opaque;
    int got = avio_read(s->in, buf, n);
    if (got <= 0) return got ? got : AVERROR_EOF;
    int64_t from = s->pos;
    s->pos += got;
    return s->qq ? qq_decrypt(s->qq, from, buf, got) : got;
}

static int64_t plain_seek(void *opaque, int64_t offset, int whence) {
    struct stream *s = opaque;
    if (whence & AVSEEK_SIZE) return s->size >= 0 ? s->size : AVERROR(ENOSYS);
    int64_t r = avio_seek(s->in, offset, whence & ~AVSEEK_FORCE);
    if (r >= 0) s->pos = r;
    return r;
}

void stream_close(struct stream *s) {
    if (!s) return;
    pthread_mutex_lock(&current_mu);
    if (current == s) current = NULL;
    pthread_mutex_unlock(&current_mu);
    if (s->threaded) {
        pthread_mutex_lock(&s->mu);
        __atomic_store_n(&s->stop, 1, __ATOMIC_RELAXED);
        pthread_cond_broadcast(&s->room);
        pthread_mutex_unlock(&s->mu);
        pthread_join(s->thread, NULL);
    }
    if (s->io) av_freep(&s->io->buffer), avio_context_free(&s->io);
    avio_closep(&s->in);
    qq_close(s->qq);
    free(s->ring);
    pthread_mutex_destroy(&s->mu);
    pthread_cond_destroy(&s->more);
    pthread_cond_destroy(&s->room);
    free(s);
}

struct stream *stream_open(const struct song *song, const int *cancel, int *error) {
    struct stream *s = calloc(1, sizeof *s);
    AVDictionary *o = NULL;
    uint8_t *buf = NULL;
    int r;
    *error = ERR_OTHER;
    if (!s) return NULL;
    pthread_mutex_init(&s->mu, NULL);
    pthread_cond_init(&s->more, NULL);
    pthread_cond_init(&s->room, NULL);
    s->cancel = cancel, s->seek_to = -1;
    if (stream_is_url(song->url)) {
        /* a dropped connection or a read that times out resumes with a range request from where it stopped */
        av_dict_set(&o, "reconnect", "1", 0);
        av_dict_set(&o, "reconnect_on_network_error", "1", 0);
        av_dict_set(&o, "reconnect_max_retries", "2", 0); /* at once, then after a second: a stream cut short ends */
        av_dict_set(&o, "rw_timeout", "15000000", 0);
        av_dict_set(&o, "ca_file", CA_FILE, 0);
        av_dict_set(&o, "tls_verify", "1", 0);
    }
    r = avio_open2(&s->in, song->url, AVIO_FLAG_READ, &(AVIOInterruptCB){interrupted, s}, &o);
    av_dict_free(&o);
    if (r < 0) { /* not found, refused, a certificate that fails its checks: stock's "file not found" (captured) */
        *error = ERR_MISSING;
        goto fail;
    }
    s->size = avio_size(s->in);
    s->qq = qq_open(song->url);
    int kb = !stream_is_url(song->url) ? 0 : song->cache_kb < 0 ? AUTO_KB : song->cache_kb;
    if (kb > 0) {
        s->cap = (int64_t)kb * 1024;
        if (!(s->ring = malloc(s->cap))) goto fail;
        if (pthread_create(&s->thread, NULL, fill_thread, s)) goto fail;
        s->threaded = 1;
        /* the first fill: P percent of the cache, or the whole stream if it is shorter */
        int64_t want = s->cap * (song->cache_pct > 0 ? song->cache_pct : 0) / 100;
        pthread_mutex_lock(&s->mu);
        while (s->hi < want && !s->eof && !s->failed && wait_more(s)) {}
        int ok = s->hi > 0 || s->eof;
        s->filled = 1, s->fill = (int)(s->hi * 100 / s->cap);
        pthread_mutex_unlock(&s->mu);
        if (!ok || interrupted(s)) {
            *error = ERR_UNSUPPORTED;
            goto fail;
        }
    }
    if (!(buf = av_malloc(IO_BUF)) ||
        !(s->io = avio_alloc_context(buf, IO_BUF, 0, s, s->threaded ? cached_read : plain_read, NULL,
                                     s->threaded ? cached_seek : plain_seek))) {
        av_free(buf);
        goto fail;
    }
    s->io->seekable = s->threaded || (s->in->seekable & AVIO_SEEKABLE_NORMAL) ? AVIO_SEEKABLE_NORMAL : 0;
    *error = ERR_NONE;
    return s;
fail:
    stream_close(s);
    return NULL;
}

AVIOContext *stream_avio(struct stream *s) { return s->io; }

void stream_keep(struct stream *s) { __atomic_store_n(&s->cancel, NULL, __ATOMIC_RELAXED); }

void stream_playing(struct stream *s) {
    pthread_mutex_lock(&current_mu);
    current = s;
    pthread_mutex_unlock(&current_mu);
}

int stream_cache_fill(void) {
    int fill = 0;
    pthread_mutex_lock(&current_mu);
    if (current && current->threaded) {
        pthread_mutex_lock(&current->mu);
        fill = current->fill;
        pthread_mutex_unlock(&current->mu);
    }
    pthread_mutex_unlock(&current_mu);
    return fill;
}
