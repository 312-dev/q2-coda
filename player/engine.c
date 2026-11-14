/* The playback thread: takes demo's requests from ctl, plays one song after another and reports each one's end.
 *
 * What demo sees is stock's (docs/player.md#events): after set-songurl the status is "ready" until sound starts,
 * media-info answers once the file is open, and a song ends with exactly one of
 *   {player-status\exit}      it played to its end
 *   {player-nextstatus\exit}  set-nexturl replaced it
 *   {player-stopstatus\exit}  mcl-stop
 *   {player-status\error}     it would not open, after /tmp/notfile or /tmp/notsupport is made for demo's dialog
 * A song preloaded by set-gapless starts on the frame after the last of the one before it, in the same PCM when
 * rate, channels and width are the same. */
#include "q2player.h"
#include <fcntl.h>
#include <math.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>

struct ctl ctl = {.mu = PTHREAD_MUTEX_INITIALIZER, .wake = PTHREAD_COND_INITIALIZER};

static struct output *out;
static struct pcm_format out_format;
static struct source *next;     /* the preloaded song, open and waiting */
static struct song next_song;

enum { ENDED, NEXT, STOPPED, FAILED };

static void touch(const char *path) {
    int fd = open(path, O_WRONLY | O_CREAT, 0644);
    if (fd >= 0) close(fd);
}

static void drop_preload(void) {
    source_close(next);
    next = NULL;
}

static void close_output(int drain) {
    if (!out) return;
    if (drain) output_drain(out);
    output_close(out);
    out = NULL;
}

/* Root mean square of each of the first two channels in dB below full scale, as mcl-getuvmeter reports it. */
static void meter(const uint8_t *data, int frames, const struct pcm_format *f, int uv[2]) {
    for (int c = 0; c < 2; c++) {
        double sum = 0;
        int ch = c < f->channels ? c : 0;
        for (int i = 0; i < frames; i++) {
            double v = f->bytes == 2 ? ((const int16_t *)data)[i * f->channels + ch] / 32768.0
                                     : ((const int32_t *)data)[i * f->channels + ch] / 2147483648.0;
            sum += v * v;
        }
        uv[c] = sum > 0 ? (int)(10 * log10(sum / frames)) : 0;
    }
}

/* Plays s to its end or to the request that cuts it short. The PCM stays open on return. */
static int play(struct source *s, const struct song *song) {
    struct ctl *c = &ctl;
    const struct pcm_format *f = source_format(s);
    struct info info;
    source_info(s, &info);
    pthread_mutex_lock(&c->mu);
    c->info = info, c->has_info = 1, c->position = song->start;
    pthread_mutex_unlock(&c->mu);

    if (out && memcmp(&out_format, f, sizeof *f)) close_output(1); /* another format: the old song plays out first */
    if (!out) {
        pthread_mutex_lock(&c->mu);
        int way = c->outputway;
        pthread_mutex_unlock(&c->mu);
        if (!(out = output_open(f, way, song->hw))) return FAILED;
        out_format = *f;
    }
    int64_t origin = source_frames(s); /* frames of this song written to the PCM count from here */
    int64_t offset = origin;           /* and this is where in the song that was */

    for (;;) {
        pthread_mutex_lock(&c->mu);
        c->state = PLAYING;
        while (c->paused && !c->play && !c->stop && !c->seek) {
            output_pause(out, 1);
            pthread_cond_wait(&c->wake, &c->mu);
        }
        if (c->play) { pthread_mutex_unlock(&c->mu); return NEXT; }
        if (c->stop) { c->stop = 0; pthread_mutex_unlock(&c->mu); return STOPPED; }
        int seek = c->seek, to = c->seek_to, want = c->preload, unwant = c->close_preload, metering = c->explay == 1;
        struct song ps = c->preload_song;
        c->seek = c->preload = c->close_preload = 0;
        int paused = c->paused;
        pthread_mutex_unlock(&c->mu);

        if (unwant) drop_preload();
        if (want) {
            /* ponytail: opened on this thread, between two writes; the PCM's 500 ms covers a file on the card.
             * A slow source (a URL) needs its own thread, as stock's preload has: phase 2. */
            int e;
            drop_preload();
            next = source_open(&ps, &e);
            next_song = ps;
        }
        if (seek) {
            output_drop(out);
            if (source_seek(s, to) < 0) return ENDED;
            origin = offset = source_frames(s);
            if (paused) continue;
        }
        output_pause(out, 0);

        const uint8_t *data;
        int n = source_read(s, &data);
        if (n <= 0) return ENDED;
        int uv[2] = {0, 0};
        if (metering) meter(data, n, f, uv);
        if (output_write(out, data, n) < 0) return FAILED;

        /* What is still in the PCM is this song's newest frames, and under a gapless change the tail of the song
         * before as well: the position stays at the start until that tail has played. */
        int64_t heard = source_frames(s) - origin - output_delay(out);
        pthread_mutex_lock(&c->mu);
        c->position = (int)((offset + (heard > 0 ? heard : 0)) / f->rate);
        c->uv[0] = uv[0], c->uv[1] = uv[1];
        pthread_mutex_unlock(&c->mu);
    }
}

void *engine_thread(void *unused) {
    struct ctl *c = &ctl;
    struct source *cur = NULL;
    struct song song;
    (void)unused;
    for (;;) {
        if (!cur) {
            pthread_mutex_lock(&c->mu);
            c->state = IDLE, c->has_info = 0;
            while (!c->play) {
                c->stop = c->seek = c->preload = 0; /* nothing is playing: stock answers none of these */
                pthread_cond_wait(&c->wake, &c->mu);
            }
            song = c->play_song;
            c->play = c->next = c->stop = c->seek = c->paused = 0;
            c->state = OPENING;
            pthread_mutex_unlock(&c->mu);
            int error;
            drop_preload();
            if (!(cur = source_open(&song, &error))) {
                close_output(0);
                touch(error == ERR_MISSING ? "/tmp/notfile" : "/tmp/notsupport");
                ipc_send("{player-status\\error}");
                continue;
            }
        }
        int why = play(cur, &song);
        source_close(cur);
        cur = NULL;
        pthread_mutex_lock(&c->mu);
        int skipped = c->next;
        c->has_info = 0;
        pthread_mutex_unlock(&c->mu);
        switch (why) {
        case ENDED:
            if (next) { /* the preloaded song follows without a break; demo sends nothing more */
                cur = next, next = NULL, song = next_song;
                ipc_send("{player-status\\exit}");
                break;
            }
            close_output(1);
            pthread_mutex_lock(&c->mu);
            c->state = IDLE;
            pthread_mutex_unlock(&c->mu);
            ipc_send("{player-status\\exit}");
            break;
        case NEXT:
            if (out) output_drop(out);
            close_output(0);
            drop_preload();
            if (skipped) ipc_send("{player-nextstatus\\exit}");
            break;
        case STOPPED:
            if (out) output_drop(out);
            close_output(0);
            drop_preload();
            pthread_mutex_lock(&c->mu);
            c->state = IDLE;
            pthread_mutex_unlock(&c->mu);
            ipc_send("{player-stopstatus\\exit}");
            break;
        case FAILED:
            close_output(0);
            drop_preload();
            touch("/tmp/notsupport");
            ipc_send("{player-status\\error}");
            break;
        }
    }
}
