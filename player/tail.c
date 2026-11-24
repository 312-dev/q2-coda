/* A song's file opened a second time, for crossfade to read its last seconds from instead of keeping them as PCM
 * (docs/internals.md#the-tail-from-the-file).
 *
 * Everything that touches the file runs on a thread of its own: the open, the seek, and then the decode, which keeps
 * TAIL_AHEAD_S of the file's frames decoded ahead of what has been taken, in the file's own sample format. The
 * playback thread only ever copies frames that are already decoded (tail_read), so a read of the card that is slow,
 * or waits on a page the kernel let go, is this thread's wait and not the song's; a stall of the card shorter than
 * what is decoded ahead changes nothing that is heard. What a longer one does is dsp.c's to decide, and differs by
 * where it falls (before the hold, in it, under the next song).
 *
 * It answers only for a whole file on the card whose decoder gives the same samples however the decode began
 * (TWICE: FLAC, ALAC and PCM, each a function of its packets alone) and whose seek lands on the frame asked for; a
 * song in any other codec, a stream and a song still arriving are refused, and their tails are held in memory. MPEG
 * audio is refused on a measurement: FFmpeg's integer decoder carries its rounding from sample to sample
 * (dither_state), so a decode begun at a seek never gives the first decode's samples again.
 *
 * Nothing here is taken on trust. dsp.c compares every frame read before the hold with the first decode's own, and
 * for the frames it cannot compare (the first decode is by then ahead) it notes each of the first decode's blocks
 * here, its length and a hash, and the read checks them as it comes to them. A difference is logged, once.
 *
 * Two settings for test/player/run.py, as smart.c's Q2PLAYER_SMART_DELAY_MS:
 *   Q2PLAYER_XF_WAIT          the playback thread waits for this thread wherever it would otherwise go on without it
 *                             (the open not done, the frames not decoded yet), so a test's outcome does not depend
 *                             on how busy the machine is. Never while a stall below is on.
 *   Q2PLAYER_TAIL_STALL=A:N   the decode stops once it has decoded the file's frame A, as a card that does not answer,
 *                             until N more frames have been taken (N of audio at any speed); N below 0, for good.
 *   Q2PLAYER_TAIL_STALL=A:msN the same, for N ms of this machine's time.
 *   Q2PLAYER_TAIL_DELAY_MS    the open comes this late. */
#include "q2player.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

static const char *const TWICE[] = {"FLAC", "ALAC", "WAV", "AIFF"}; /* source.c codec_name's names */
#define TAIL_AHEAD_S 2.5 /* decoded ahead of what has been taken: a card may stall this long unheard */

enum { T_OPENING, T_READY, T_FAILED };
struct note {
    uint64_t hash;
    int frames;
};
struct tail {
    struct song song;
    struct pcm_format fmt; /* the first open's: the second must give the same */
    int64_t from;          /* the frame it begins at */
    /* under mu */
    int state;
    int closed;         /* nobody wants it: the thread closes the file and frees this, wherever it is */
    int whole;          /* the file's end is decoded: nothing more comes */
    int stalled;        /* Q2PLAYER_TAIL_STALL is on */
    uint8_t *ring;      /* the frames decoded and not yet taken */
    unsigned cap, head, fill;
    int64_t at;         /* the frame the next read gives */
    pthread_cond_t more, room; /* frames were decoded; frames were taken */
    /* the playback thread's alone */
    int unit;            /* a frame's bytes */
    struct note *notes;  /* the first decode's blocks still to come, oldest at head */
    unsigned n_cap, n_head, n_len;
    uint64_t run;        /* the hash of the noted block being read */
    int run_left;        /* its frames still to read; 0 between blocks */
    int differs, waits;  /* a difference was logged; reads that found the frames not decoded while a stall was on */
    int64_t stalled_for; /* frames taken while a stall was on */
    /* the thread's alone */
    struct source *src;
};
static pthread_mutex_t mu = PTHREAD_MUTEX_INITIALIZER;

static void say(const char *what, long long a, long long b) {
    char line[240];
    int k = snprintf(line, sizeof line, what, a, b);
    plog('I', line, k < (int)sizeof line ? k : (int)sizeof line - 1);
}

static int test_wait(void) { return getenv("Q2PLAYER_XF_WAIT") != NULL; }

/* A hash that does not care how the frames were cut into blocks: 16 bits at a time (a frame is a whole number of
 * them), FNV-1a's step. */
typedef uint16_t __attribute__((may_alias)) unit16;
static uint64_t hash(uint64_t h, const uint8_t *data, size_t bytes) {
    const unit16 *p = (const unit16 *)data;
    for (size_t i = 0; i < bytes / 2; ++i) h = (h ^ p[i]) * 0x100000001b3ULL;
    return h;
}
#define HASH_START 0xcbf29ce484222325ULL

/* The file opened at t->from, or why not. */
static struct source *open_file(struct tail *t, const char **why) {
    const char *u = t->song.url;
    struct source *s;
    int error;
    for (long wait = getenv("Q2PLAYER_TAIL_DELAY_MS") ? atol(getenv("Q2PLAYER_TAIL_DELAY_MS")) : 0;
         wait > 0 && !__atomic_load_n(&t->closed, __ATOMIC_RELAXED); wait -= 10)
        usleep(10000);
    if (stream_is_url(u) || strstr(u, "qm_songid=") || grow_arriving(u) || sacd_is_image(u))
        return *why = "q2player: crossfade: the tail held in memory, the song is not a whole file on the card", NULL;
    if (!(s = source_open(&t->song, &error, &t->closed)))
        return *why = "q2player: crossfade: the tail held in memory, the song would not open a second time", NULL;
    struct info i;
    const struct pcm_format *f = source_format(s);
    int known = 0;
    source_info(s, &i);
    for (unsigned k = 0; k < sizeof TWICE / sizeof *TWICE; ++k) known |= !strcmp(i.codec, TWICE[k]);
    if (!known) *why = "q2player: crossfade: the tail held in memory, a second decode of this codec is not the first's";
    else if (f->rate != t->fmt.rate || f->channels != t->fmt.channels || f->bytes != t->fmt.bytes ||
             f->is_float != t->fmt.is_float || f->dsd)
        *why = "q2player: crossfade: the tail held in memory, the song opened in another format the second time";
    else if (source_seek_frame(s, t->from) < 0)
        *why = "q2player: crossfade: the tail held in memory, the second open would not seek";
    else return s; /* its reads still stop when nobody wants it (closed) */
    source_close(s);
    return NULL;
}

/* Q2PLAYER_TAIL_STALL, mu held: the decode has reached frame `decoded`; waits out the stall asked for there. */
static void stall(struct tail *t, int64_t decoded) {
    static int done; /* once a player */
    const char *e = getenv("Q2PLAYER_TAIL_STALL"), *colon = e ? strchr(e, ':') : NULL;
    if (!colon || done || decoded < atoll(e)) return;
    done = 1, t->stalled = 1;
    pthread_cond_broadcast(&t->more); /* a read waiting for this thread stops waiting */
    int64_t began = t->at;
    if (!strncmp(colon + 1, "ms", 2)) {
        struct timespec until;
        clock_gettime(CLOCK_REALTIME, &until);
        long ms = atol(colon + 3);
        until.tv_sec += ms / 1000, until.tv_nsec += ms % 1000 * 1000000L;
        if (until.tv_nsec >= 1000000000L) until.tv_sec++, until.tv_nsec -= 1000000000L;
        while (!t->closed && !pthread_cond_timedwait(&t->room, &mu, &until)) {
        }
    } else {
        long long n = atoll(colon + 1);
        while (!t->closed && (n < 0 || t->at - began < n)) pthread_cond_wait(&t->room, &mu);
    }
    t->stalled_for = t->at - began;
    t->stalled = 0;
}

static void *reader(void *arg) {
    struct tail *t = arg;
    const char *why = NULL;
    struct source *s = open_file(t, &why);
    unsigned cap = (unsigned)(TAIL_AHEAD_S * t->fmt.rate);
    uint8_t *ring = s ? malloc((size_t)cap * t->unit) : NULL;
    if (s && !ring) why = "q2player: crossfade: the tail held in memory, no memory to decode ahead in", source_close(s), s = NULL;
    pthread_mutex_lock(&mu);
    t->src = s, t->ring = ring, t->cap = cap;
    if (!s) t->state = T_FAILED;
    pthread_cond_broadcast(&t->more);
    pthread_mutex_unlock(&mu);
    if (why && !__atomic_load_n(&t->closed, __ATOMIC_RELAXED)) say(why, 0, 0);
    int64_t decoded = t->from;
    while (s) {
        const uint8_t *data;
        int n = __atomic_load_n(&t->closed, __ATOMIC_RELAXED) ? 0 : source_read(s, &data); /* the card: unlocked */
        pthread_mutex_lock(&mu);
        for (int done = 0; n > 0 && done < n && !t->closed;) {
            if (t->fill == t->cap) { /* full: the open is ready from the first time it is */
                t->state = T_READY;
                pthread_cond_broadcast(&t->more);
                pthread_cond_wait(&t->room, &mu);
                continue;
            }
            unsigned at = (t->head + t->fill) % t->cap, m = t->cap - t->fill;
            if (m > (unsigned)(n - done)) m = (unsigned)(n - done);
            if (m > t->cap - at) m = t->cap - at;
            memcpy(t->ring + (size_t)at * t->unit, data + (size_t)done * t->unit, (size_t)m * t->unit);
            t->fill += m, done += (int)m, decoded += m;
            pthread_cond_broadcast(&t->more);
            stall(t, decoded);
        }
        if (n <= 0 || t->closed) { /* the file's end, an error that reads as one, or nobody wants it */
            t->whole = 1, t->state = T_READY;
            pthread_cond_broadcast(&t->more);
            pthread_mutex_unlock(&mu);
            break;
        }
        pthread_mutex_unlock(&mu);
    }
    source_close(s);
    pthread_mutex_lock(&mu);
    while (!t->closed) pthread_cond_wait(&t->room, &mu); /* what is decoded is still to be taken */
    pthread_mutex_unlock(&mu);
    pthread_cond_destroy(&t->more), pthread_cond_destroy(&t->room);
    free(t->ring);
    free(t->notes);
    free(t);
    return NULL;
}

struct tail *tail_open(const char *url, const struct pcm_format *f, int64_t frame) {
    struct tail *t = calloc(1, sizeof *t);
    pthread_attr_t a;
    pthread_t th;
    if (!t) return NULL;
    snprintf(t->song.url, sizeof t->song.url, "%s", url);
    t->fmt = *f, t->from = t->at = frame, t->unit = f->channels * f->bytes;
    pthread_cond_init(&t->more, NULL), pthread_cond_init(&t->room, NULL);
    pthread_attr_init(&a);
    pthread_attr_setdetachstate(&a, PTHREAD_CREATE_DETACHED);
    int r = pthread_create(&th, &a, reader, t);
    pthread_attr_destroy(&a);
    if (r) {
        free(t);
        return NULL;
    }
    return t;
}

int tail_ready(struct tail *t) {
    pthread_mutex_lock(&mu);
    while (t->state == T_OPENING && test_wait() && !t->stalled && !getenv("Q2PLAYER_TAIL_DELAY_MS")) pthread_cond_wait(&t->more, &mu);
    int s = t->state;
    pthread_mutex_unlock(&mu);
    return s == T_READY ? 1 : s == T_FAILED ? -1 : 0;
}

int64_t tail_at(const struct tail *t) { return t->at; }

/* The thread owns the file and this: it lets both go when it finds closed set, even from inside a read of the card
 * that is not answering, so the playback thread never waits here. */
void tail_close(struct tail *t) {
    if (!t) return;
    pthread_mutex_lock(&mu);
    if (t->stalled_for || t->stalled || t->waits)
        say("q2player: crossfade: the tail's reader was stalled while %lld frames were taken, and the song found it short %lld times",
            t->stalled_for, t->waits);
    __atomic_store_n(&t->closed, 1, __ATOMIC_RELAXED);
    pthread_cond_broadcast(&t->room);
    pthread_mutex_unlock(&mu);
}

void tail_note(struct tail *t, const uint8_t *data, int frames) {
    if (t->n_len == t->n_cap) { /* the ring of notes, grown in order */
        unsigned cap = t->n_cap ? 2 * t->n_cap : 256;
        struct note *n = malloc(cap * sizeof *n);
        if (!n) return; /* this block and the rest go unchecked */
        for (unsigned i = 0; i < t->n_len; ++i) n[i] = t->notes[(t->n_head + i) % t->n_cap];
        pthread_mutex_lock(&mu); /* the thread frees notes */
        free(t->notes);
        t->notes = n, t->n_cap = cap, t->n_head = 0;
        pthread_mutex_unlock(&mu);
    }
    struct note *n = &t->notes[(t->n_head + t->n_len++) % t->n_cap];
    n->frames = frames, n->hash = hash(HASH_START, data, (size_t)frames * t->unit);
}

/* The frames just read against the notes, as far as there are notes for them. */
static void check(struct tail *t, const uint8_t *data, int frames) {
    while (frames > 0 && (t->run_left || t->n_len)) {
        if (!t->run_left) t->run_left = t->notes[t->n_head].frames, t->run = HASH_START;
        int n = frames < t->run_left ? frames : t->run_left;
        t->run = hash(t->run, data, (size_t)n * t->unit);
        data += (size_t)n * t->unit, frames -= n, t->run_left -= n;
        if (t->run_left) break;
        if (t->run != t->notes[t->n_head].hash && !t->differs++)
            say("q2player: crossfade: the tail read from the file differs from the first decode near frame %lld", t->at, 0);
        t->n_head = (t->n_head + 1) % t->n_cap, t->n_len--;
    }
}

/* mu held: waits until need frames are decoded or none more will be, at most ms (Q2PLAYER_XF_WAIT: as long as it
 * takes, but not through a stall, which is a card that does not answer). The frames there are. */
static unsigned await(struct tail *t, unsigned need, int ms) {
    struct timespec until;
    clock_gettime(CLOCK_REALTIME, &until);
    until.tv_sec += ms / 1000, until.tv_nsec += ms % 1000 * 1000000L;
    if (until.tv_nsec >= 1000000000L) until.tv_sec++, until.tv_nsec -= 1000000000L;
    if (need > t->cap) need = t->cap;
    if (t->fill < need && !t->whole && t->stalled) ++t->waits;
    while (t->fill < need && !t->whole) {
        if (test_wait() && !t->stalled) pthread_cond_wait(&t->more, &mu);
        else if (ms <= 0 || pthread_cond_timedwait(&t->more, &mu, &until)) break;
    }
    return t->fill;
}

int tail_ahead(struct tail *t, int need, int wait_ms, int *whole) {
    pthread_mutex_lock(&mu);
    unsigned fill = await(t, (unsigned)need, wait_ms);
    *whole = t->whole;
    pthread_mutex_unlock(&mu);
    return (int)fill;
}

int tail_read(struct tail *t, uint8_t *out, int frames, int wait_ms) {
    int got = 0;
    pthread_mutex_lock(&mu);
    while (got < frames) {
        unsigned need = (unsigned)(frames - got) < t->cap ? (unsigned)(frames - got) : t->cap;
        unsigned fill = await(t, need, wait_ms), take = fill < need ? fill : need;
        for (unsigned done = 0; done < take;) { /* two spans where the ring wraps */
            unsigned m = take - done < t->cap - t->head ? take - done : t->cap - t->head;
            memcpy(out + (size_t)got * t->unit, t->ring + (size_t)t->head * t->unit, (size_t)m * t->unit);
            t->head = (t->head + m) % t->cap, t->fill -= m, t->at += m, got += (int)m, done += m;
        }
        pthread_cond_broadcast(&t->room);
        if (fill < need) break; /* not decoded yet, or the file's end */
    }
    pthread_mutex_unlock(&mu);
    check(t, out, got);
    return got;
}
