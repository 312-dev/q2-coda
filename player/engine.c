/* The playback thread: takes demo's requests from ctl, plays one song after another and reports each one's end.
 *
 * What demo sees is stock's (docs/player.md#events): after set-songurl the status is "ready" until the song is heard,
 * media-info answers once the file is open, and a song ends with exactly one of
 *   {player-status\exit}      it played to its end
 *   {player-nextstatus\exit}  set-nexturl replaced it
 *   {player-stopstatus\exit}  mcl-stop
 *   {player-status\error}     it would not open, after /tmp/notfile or /tmp/notsupport is made for demo's dialog
 * A song preloaded by set-gapless is opened on a thread of its own while the one before plays, and starts on the frame
 * after that one's last, in the same PCM when rate, channels and width are the same. A preload that a queue edit, a
 * stop or a skip cancelled is closed by its thread when its open returns, and never played. */
#include "q2player.h"
#include <fcntl.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

struct ctl ctl = {.mu = PTHREAD_MUTEX_INITIALIZER, .wake = PTHREAD_COND_INITIALIZER};

static struct output *out;
static struct pcm_format out_format;
static int out_way; /* the output way out was opened on */
static int out_cut; /* out was dropped for a skip: its tail is not to be played out */

enum { ENDED, NEXT, STOPPED, FAILED };

static void touch(const char *path) {
    int fd = open(path, O_WRONLY | O_CREAT, 0644);
    if (fd >= 0) close(fd);
}

static int64_t now_ms(void) {
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return t.tv_sec * 1000LL + t.tv_nsec / 1000000;
}

/* demo learns of a song's end only from the newest player-status it reads: a second exit inside one of its UI passes
 * (up to 222 ms) is lost, and with it the move to the next song, as when a preloaded file has no audio and ends as it
 * starts. So an exit goes no sooner than 300 ms after the one before, as in Q2 Pod's player
 * (docs/internals.md#a-bad-file-next). A song of 300 ms or more never waits. */
enum { EXIT_HOLD_MS = 300 };
static void send_exit(void) {
    static int64_t last;
    int64_t now = now_ms();
    if (last && now - last < EXIT_HOLD_MS) {
        usleep((useconds_t)(EXIT_HOLD_MS - (now - last)) * 1000);
        now = last + EXIT_HOLD_MS;
    }
    last = now;
    ipc_send("{player-status\\exit}");
}

/* ---- the preload: set-gapless's song, opened on its own thread so the song playing never waits on a slow open */

struct preload {
    struct song song;
    struct source *src; /* the open song, once done and if it opened */
    int error, done;
    int cancel; /* set under ctl.mu: nobody wants it; the thread closes what it opens and frees this */
};
static struct preload *pre; /* the newest set-gapless's; the playback thread's own */

static void *preload_thread(void *arg) {
    struct preload *p = arg;
    int error;
    struct source *s = source_open(&p->song, &error, &p->cancel);
    pthread_mutex_lock(&ctl.mu);
    if (p->cancel) {
        pthread_mutex_unlock(&ctl.mu);
        source_close(s);
        free(p);
        return NULL;
    }
    p->src = s, p->error = error, p->done = 1;
    pthread_cond_broadcast(&ctl.wake);
    pthread_mutex_unlock(&ctl.mu);
    return NULL;
}

static void preload_cancel(void) {
    struct preload *p = pre;
    if (!p) return;
    pre = NULL;
    pthread_mutex_lock(&ctl.mu);
    int done = p->done;
    if (!done) __atomic_store_n(&p->cancel, 1, __ATOMIC_RELAXED); /* the thread frees it; a read in its open stops */
    pthread_mutex_unlock(&ctl.mu);
    if (done) {
        source_close(p->src);
        free(p);
    }
}

static void preload_start(const struct song *song) {
    pthread_attr_t a;
    pthread_t t;
    preload_cancel();
    if (!(pre = calloc(1, sizeof *pre))) return;
    pre->song = *song;
    pthread_attr_init(&a);
    pthread_attr_setdetachstate(&a, PTHREAD_CREATE_DETACHED);
    if (pthread_create(&t, &a, preload_thread, pre)) { /* no thread: the song's end opens it, as a cold start */
        pre->done = 1, pre->error = ERR_OTHER;
    }
    pthread_attr_destroy(&a);
}

/* ---- playing */

static void close_output(int drain) {
    if (!out) return;
    if (drain) output_drain(out);
    output_close(out);
    out = NULL;
}

/* A level for each of the first two channels, for mcl-getuvmeter: the block's root mean square in dB below full
 * scale. ponytail: stock's scale is not known (it read -21 for one tone and -24 for a louder mix); phase 3
 * reads its meter's code and a Q2 confirms. */
static void meter(const uint8_t *data, int frames, const struct pcm_format *f, int uv[2]) {
    for (int c = 0; c < 2; c++) {
        double sum = 0;
        int ch = c < f->channels ? c : 0;
        for (int i = 0; i < frames; i++) {
            double v = f->is_float ? ((const float *)data)[i * f->channels + ch]
                     : f->bytes == 2 ? ((const int16_t *)data)[i * f->channels + ch] / 32768.0
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
    /* until the song is heard its position reads 0, a start offset's too, as stock's does (fixtures/pod-start.txt) */
    c->info = info, c->has_info = 1, c->position = 0, c->state = OPENING;
    pthread_mutex_unlock(&c->mu);

    pthread_mutex_lock(&c->mu);
    int way = c->outputway;
    pthread_mutex_unlock(&c->mu);
    dac_song(way);
    int natural = 0; /* a preloaded change that reopens: the old song played out first, and nobody pressed */
    if (out && (memcmp(&out_format, f, sizeof *f) || out_way != way)) {
        if (!out_cut) output_drain(out), natural = 1;
        dac_mute(out_way);
        close_output(0);
    }
    if (!out) {
        dac_mute(way);
        if (!(out = output_open(f, way, song->hw))) return FAILED;
        out_format = *f, out_way = way;
    }
    out_cut = 0;
    dac_unmute(way, natural || dac_stamp(1) || dac_stamp(0));
    int64_t origin = source_frames(s); /* frames of this song written to the PCM count from here */
    int64_t offset = origin;           /* and this is where in the song that was */
    int heard = 0, pausing = 0;

    for (;;) {
        pthread_mutex_lock(&c->mu);
        while (c->paused && !(c->play && c->next) && !c->stop && !c->seek && !c->preload && !c->close_preload) {
            if (!pausing) dac_mute(way), output_pause(out, 1), pausing = 1;
            pthread_cond_wait(&c->wake, &c->mu);
        }
        /* set-nexturl cuts the song. set-songurl alone does not: stock only notes it and plays it when this song
         * has ended (captured: fixtures/stock-replace.txt). demo sends it only when it believes nothing plays. */
        if (c->play && c->next) { pthread_mutex_unlock(&c->mu); return NEXT; }
        if (c->stop) { c->stop = 0; pthread_mutex_unlock(&c->mu); return STOPPED; }
        int seek = c->seek, to = c->seek_to, want = c->preload, unwant = c->close_preload, metering = c->explay == 1;
        struct song ps = c->preload_song;
        c->seek = c->preload = c->close_preload = 0;
        int paused = c->paused;
        pthread_mutex_unlock(&c->mu);

        if (unwant) preload_cancel();
        if (want) preload_start(&ps);
        if (seek) {
            dac_mute(way);
            if (!paused) usleep(150000); /* stock's wait after the mute, before the drop */
            output_drop(out);
            if (source_seek(s, to) < 0) return ENDED;
            if (!paused) dac_unmute(way, dac_stamp(1)); /* paused, the resume unmutes */
            origin = offset = source_frames(s);
            /* paused, the position shows the seek only once playing again, as stock's */
        }
        if (paused) continue; /* a preload or a seek taken while paused: back to waiting */
        if (pausing) dac_unmute(way, 1), output_pause(out, 0), pausing = 0; /* a resume is always a person's */

        const uint8_t *data;
        int n = source_read(s, &data);
        if (n <= 0) return ENDED;
        int uv[2] = {0, 0};
        if (metering) meter(data, n, f, uv);
        if (output_write(out, data, n) < 0) return FAILED;

        /* What is still in the PCM is this song's newest frames, and under a gapless change the tail of the song
         * before as well: the position stays at the start, and the status "ready", until that tail has played. */
        int64_t played = source_frames(s) - origin - output_delay(out);
        if (played > 0) heard = 1;
        pthread_mutex_lock(&c->mu);
        if (played > 0) c->position = (int)((offset + played) / f->rate);
        if (heard) c->state = PLAYING;
        c->uv[0] = uv[0], c->uv[1] = uv[1];
        pthread_mutex_unlock(&c->mu);
    }
}

/* The preload's own requests (set-gapless, mcl-closegapless) that came after the song's last block; with wait, then
 * also a wait while it is still opening, until it is done or demo asks for something else. */
static void settle_preload(int wait) {
    struct ctl *c = &ctl;
    for (;;) {
        pthread_mutex_lock(&c->mu);
        int want = c->preload, unwant = c->close_preload;
        struct song ps = c->preload_song;
        c->preload = c->close_preload = 0;
        if (!want && !unwant) {
            if (!wait || !pre || pre->done || c->play || c->stop) { pthread_mutex_unlock(&c->mu); return; }
            pthread_cond_wait(&c->wake, &c->mu);
            pthread_mutex_unlock(&c->mu);
            continue;
        }
        pthread_mutex_unlock(&c->mu);
        if (unwant) preload_cancel();
        if (want) preload_start(&ps);
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
                c->stop = c->seek = c->preload = c->close_preload = 0; /* nothing is playing: stock answers none of these */
                pthread_cond_wait(&c->wake, &c->mu);
            }
            song = c->play_song;
            c->play = c->next = c->stop = c->seek = c->paused = 0;
            c->state = OPENING;
            pthread_mutex_unlock(&c->mu);
            int error;
            preload_cancel();
            if (!(cur = source_open(&song, &error, NULL))) {
                close_output(0);
                touch(error == ERR_MISSING ? "/tmp/notfile" : "/tmp/notsupport");
                ipc_send("{player-status\\error}");
                continue;
            }
        }
        int why = play(cur, &song);
        source_close(cur);
        cur = NULL;
        int sent = 0; /* the song's exit has gone */
        if (why == ENDED) {
            settle_preload(0);
            pthread_mutex_lock(&c->mu);
            int cancelled = c->play || c->stop;
            pthread_mutex_unlock(&c->mu);
            if (cancelled) preload_cancel();
            if (pre && !pre->done) {
                /* The song ended before its successor opened. demo hears of the end now, as from stock, whose main
                 * loop then opens that file itself; here the preload's own open goes on and this thread waits. */
                pthread_mutex_lock(&c->mu);
                c->has_info = 0, c->state = OPENING;
                pthread_mutex_unlock(&c->mu);
                send_exit(), sent = 1;
                settle_preload(1);
            }
        }
        pthread_mutex_lock(&c->mu);
        int skipped = c->next, waiting = c->play, stopping = c->stop;
        c->has_info = 0;
        if (why == STOPPED) c->play = c->next = 0; /* a stop also forgets a song that was waiting, as stock's does */
        pthread_mutex_unlock(&c->mu);
        switch (why) {
        case ENDED:
            /* a set-songurl that came while this song played goes next, not the preload; a stop leaves nothing */
            if (waiting || stopping) preload_cancel();
            if (pre && pre->src) { /* the preloaded song follows without a break; demo sends nothing more */
                cur = pre->src, song = pre->song;
                source_keep(cur);
                free(pre);
                pre = NULL;
                if (!sent) send_exit();
                break;
            }
            if (pre && pre->done) { /* it did not open: this thread opens it again, as stock's main loop does, and reports that */
                pthread_mutex_lock(&c->mu);
                if (!c->play) c->play_song = pre->song, c->play = 1;
                pthread_mutex_unlock(&c->mu);
                preload_cancel();
            }
            close_output(1);
            pthread_mutex_lock(&c->mu);
            c->state = IDLE;
            pthread_mutex_unlock(&c->mu);
            if (!sent) send_exit();
            break;
        case NEXT: /* the PCM stays open for the next song, which closes it if its format differs */
            if (out) dac_mute(out_way), output_drop(out), out_cut = 1;
            preload_cancel();
            if (skipped) ipc_send("{player-nextstatus\\exit}");
            break;
        case STOPPED:
            if (out) dac_mute(out_way), output_drop(out);
            close_output(0);
            preload_cancel();
            pthread_mutex_lock(&c->mu);
            c->state = IDLE;
            pthread_mutex_unlock(&c->mu);
            ipc_send("{player-stopstatus\\exit}");
            break;
        case FAILED:
            close_output(0);
            preload_cancel();
            touch("/tmp/notsupport");
            ipc_send("{player-status\\error}");
            break;
        }
    }
}
