/* A song's file opened a second time, for crossfade to read its last seconds from instead of keeping them as PCM
 * (docs/internals.md#the-tail-from-the-file).
 *
 * The open runs on a thread of its own, so the playback thread never waits on it, and gives up by itself when nobody
 * wants it any more. It answers only for a whole file on the card whose decoder gives the same samples however the
 * decode began (TWICE: FLAC, ALAC and PCM, each a function of its packets alone) and whose seek lands on the frame
 * asked for; a song in any other codec, a stream and a song still arriving are refused, and their tails are held in
 * memory. MPEG audio is refused on a measurement: FFmpeg's integer decoder carries its rounding from sample to
 * sample (dither_state), so a decode begun at a seek never gives the first decode's samples again.
 *
 * Nothing here is taken on trust. dsp.c compares every frame read before the hold with the first decode's own, and
 * for the frames it cannot compare (the first decode is by then ahead) it notes each of the first decode's blocks
 * here, its length and a hash, and the read checks them as it comes to them. A difference is logged, once. */
#include "q2player.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static const char *const TWICE[] = {"FLAC", "ALAC", "WAV", "AIFF"}; /* source.c codec_name's names */

enum { T_OPENING, T_READY, T_FAILED };
struct note {
    uint64_t hash;
    int frames;
};
struct tail {
    struct song song;
    struct pcm_format fmt; /* the first open's: the second must give the same */
    int64_t from;          /* the frame it begins at */
    int state, cancel;     /* under mu. cancel: nobody wants it; the thread closes what it opened and frees this */
    struct source *src;
    const uint8_t *data; /* the last block read, from the frame not yet handed out */
    int left, unit;      /* that block's frames still to hand out; a frame's bytes */
    int64_t at;          /* the frame the next read gives */
    struct note *notes;  /* the first decode's blocks still to come, oldest at head */
    unsigned cap, head, len;
    uint64_t run; /* the hash of the noted block being read */
    int run_left; /* its frames still to read; 0 between blocks */
    int differs;
};
static pthread_mutex_t mu = PTHREAD_MUTEX_INITIALIZER;

static void say(const char *what, long long n) {
    char line[200];
    int k = snprintf(line, sizeof line, what, n);
    plog('I', line, k < (int)sizeof line ? k : (int)sizeof line - 1);
}

/* A hash that does not care how the frames were cut into blocks: 16 bits at a time (a frame is a whole number of
 * them), FNV-1a's step. */
typedef uint16_t __attribute__((may_alias)) unit16;
static uint64_t hash(uint64_t h, const uint8_t *data, size_t bytes) {
    const unit16 *p = (const unit16 *)data;
    for (size_t i = 0; i < bytes / 2; ++i) h = (h ^ p[i]) * 0x100000001b3ULL;
    return h;
}
#define HASH_START 0xcbf29ce484222325ULL

static void *open_thread(void *arg) {
    struct tail *t = arg;
    const char *u = t->song.url, *why = NULL;
    struct source *s = NULL;
    int error;
    if (stream_is_url(u) || strstr(u, "qm_songid=") || grow_arriving(u) || sacd_is_image(u))
        why = "q2player: crossfade: the tail held in memory, the song is not a whole file on the card";
    else if (!(s = source_open(&t->song, &error, &t->cancel)))
        why = "q2player: crossfade: the tail held in memory, the song would not open a second time";
    else {
        struct info i;
        const struct pcm_format *f = source_format(s);
        int known = 0;
        source_info(s, &i);
        source_keep(s);
        for (unsigned k = 0; k < sizeof TWICE / sizeof *TWICE; ++k) known |= !strcmp(i.codec, TWICE[k]);
        if (!known) why = "q2player: crossfade: the tail held in memory, a second decode of this codec is not the first's";
        else if (f->rate != t->fmt.rate || f->channels != t->fmt.channels || f->bytes != t->fmt.bytes ||
                 f->is_float != t->fmt.is_float || f->dsd)
            why = "q2player: crossfade: the tail held in memory, the song opened in another format the second time";
        else if (source_seek_frame(s, t->from) < 0)
            why = "q2player: crossfade: the tail held in memory, the second open would not seek";
    }
    if (why) source_close(s), s = NULL;
    pthread_mutex_lock(&mu);
    if (t->cancel) {
        pthread_mutex_unlock(&mu);
        source_close(s);
        free(t);
        return NULL;
    }
    t->src = s, t->state = s ? T_READY : T_FAILED;
    pthread_mutex_unlock(&mu);
    if (why) say(why, 0);
    return NULL;
}

struct tail *tail_open(const char *url, const struct pcm_format *f, int64_t frame) {
    struct tail *t = calloc(1, sizeof *t);
    pthread_attr_t a;
    pthread_t th;
    if (!t) return NULL;
    snprintf(t->song.url, sizeof t->song.url, "%s", url);
    t->fmt = *f, t->from = t->at = frame, t->unit = f->channels * f->bytes;
    pthread_attr_init(&a);
    pthread_attr_setdetachstate(&a, PTHREAD_CREATE_DETACHED);
    int r = pthread_create(&th, &a, open_thread, t);
    pthread_attr_destroy(&a);
    if (r) {
        free(t);
        return NULL;
    }
    return t;
}

int tail_ready(struct tail *t) {
    pthread_mutex_lock(&mu);
    int s = t->state;
    pthread_mutex_unlock(&mu);
    return s == T_READY ? 1 : s == T_FAILED ? -1 : 0;
}

int64_t tail_at(const struct tail *t) { return t->at; }

void tail_close(struct tail *t) {
    if (!t) return;
    pthread_mutex_lock(&mu);
    int opening = t->state == T_OPENING;
    if (opening) __atomic_store_n(&t->cancel, 1, __ATOMIC_RELAXED); /* the thread frees it; a read in its open stops */
    pthread_mutex_unlock(&mu);
    if (opening) return;
    source_close(t->src);
    free(t->notes);
    free(t);
}

void tail_note(struct tail *t, const uint8_t *data, int frames) {
    if (t->len == t->cap) { /* the ring of notes, grown in order */
        unsigned cap = t->cap ? 2 * t->cap : 256;
        struct note *n = malloc(cap * sizeof *n);
        if (!n) return; /* this block and the rest go unchecked: said when the read comes to them */
        for (unsigned i = 0; i < t->len; ++i) n[i] = t->notes[(t->head + i) % t->cap];
        free(t->notes);
        t->notes = n, t->cap = cap, t->head = 0;
    }
    struct note *n = &t->notes[(t->head + t->len++) % t->cap];
    n->frames = frames, n->hash = hash(HASH_START, data, (size_t)frames * t->unit);
}

/* The frames just read against the notes, as far as there are notes for them. */
static void check(struct tail *t, const uint8_t *data, int frames) {
    while (frames > 0 && (t->run_left || t->len)) {
        if (!t->run_left) t->run_left = t->notes[t->head].frames, t->run = HASH_START;
        int n = frames < t->run_left ? frames : t->run_left;
        t->run = hash(t->run, data, (size_t)n * t->unit);
        data += (size_t)n * t->unit, frames -= n, t->run_left -= n;
        if (t->run_left) break;
        if (t->run != t->notes[t->head].hash && !t->differs++)
            say("q2player: crossfade: the tail read from the file differs from the first decode near frame %lld", t->at);
        t->head = (t->head + 1) % t->cap, t->len--;
    }
}

int tail_read(struct tail *t, uint8_t *out, int frames) {
    int got = 0;
    while (got < frames) {
        if (!t->left && (t->left = source_read(t->src, &t->data)) <= 0) {
            t->left = 0;
            break;
        }
        int n = frames - got < t->left ? frames - got : t->left;
        memcpy(out + (size_t)got * t->unit, t->data, (size_t)n * t->unit);
        check(t, t->data, n);
        t->data += (size_t)n * t->unit, t->left -= n, t->at += n, got += n;
    }
    return got;
}
