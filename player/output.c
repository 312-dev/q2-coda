/* Where PCM goes. On the Q2 that is ALSA, opened as stock opens it (docs/player.md#output): the device by output
 * way and hardware type, the file's own rate and width with ALSA's resampling off, a 500 ms buffer in 16 periods.
 * With Q2PLAYER_PCM set to a directory it is a file a test reads back: one per open, NNNN_RATE_CH_BYTES.raw,
 * written as fast as it would play, or Q2PLAYER_SPEED times as fast (a test's clock runs that much faster too). */
#include "q2player.h"
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>
#ifdef HAVE_ALSA
#include "alsa_min.h"
#endif

enum { BUFFER_US = 500000, PERIODS = 16 };

struct output {
    struct pcm_format f;
#ifdef HAVE_ALSA
    snd_pcm_t *pcm;
#endif
    FILE *file;
    int paused;
    double speed;         /* file sink: how many seconds of sound pass in one */
    int64_t written;      /* frames, file sink */
    struct timespec t0;   /* when the file sink's clock started, moved on by each pause */
    struct timespec paused_at;
};

static double between(const struct timespec *t, const struct timespec *now) {
    return (now->tv_sec - t->tv_sec) + (now->tv_nsec - t->tv_nsec) / 1e9;
}

static double since(const struct timespec *t) {
    struct timespec now;
    clock_gettime(CLOCK_MONOTONIC, &now);
    return between(t, &now);
}

#ifdef HAVE_ALSA
/* Stock's ao_alsa init (hciplayer 0x438f64-0x4391f8): Bluetooth through bluealsa, a USB DAC as card 2, and the
 * local DAC as "default" on a Q2 (hardware type 17); types 14 and 15 are other Shanling players' "plughw:1,0". */
static const char *device(int outputway, int hw) {
    const char *forced = getenv("Q2PLAYER_DEVICE");
    if (forced) return forced;
    if (outputway == 1) return "plug:bluealsa";
    if (outputway == 2) return "hw:2,0";
    if (hw == 14 || hw == 15) return "plughw:1,0";
    if (hw == 7 || hw == 11) return "hw:1,0";
    return "default";
}

static int alsa_open(struct output *o, int outputway, int hw) {
    snd_pcm_hw_params_t *p = NULL;
    unsigned rate = o->f.rate, buffer = BUFFER_US, periods = PERIODS;
    const char *name = device(outputway, hw);
    int r = snd_pcm_open(&o->pcm, name, SND_PCM_STREAM_PLAYBACK, 0);
    if (r < 0) { fprintf(stderr, "q2player: %s: %s\n", name, snd_strerror(r)); return -1; }
    if ((r = snd_pcm_hw_params_malloc(&p)) < 0 || (r = snd_pcm_hw_params_any(o->pcm, p)) < 0 ||
        (r = snd_pcm_hw_params_set_access(o->pcm, p, SND_PCM_ACCESS_RW_INTERLEAVED)) < 0 ||
        (r = snd_pcm_hw_params_set_format(o->pcm, p, o->f.bytes == 2 ? SND_PCM_FORMAT_S16_LE : SND_PCM_FORMAT_S32_LE)) < 0 ||
        (r = snd_pcm_hw_params_set_channels(o->pcm, p, o->f.channels)) < 0 ||
        (r = snd_pcm_hw_params_set_rate_resample(o->pcm, p, 0)) < 0 ||
        (r = snd_pcm_hw_params_set_rate_near(o->pcm, p, &rate, NULL)) < 0 ||
        (r = snd_pcm_hw_params_set_buffer_time_near(o->pcm, p, &buffer, NULL)) < 0 ||
        (r = snd_pcm_hw_params_set_periods_near(o->pcm, p, &periods, NULL)) < 0 ||
        (r = snd_pcm_hw_params(o->pcm, p)) < 0 || (r = snd_pcm_prepare(o->pcm)) < 0)
        fprintf(stderr, "q2player: %s %d Hz %d ch %d bytes: %s\n", name, o->f.rate, o->f.channels, o->f.bytes, snd_strerror(r));
    if (p) snd_pcm_hw_params_free(p);
    if (r >= 0 && rate != (unsigned)o->f.rate) { /* a device that plays another rate would play the song off speed */
        fprintf(stderr, "q2player: %s gave %u Hz for %d\n", name, rate, o->f.rate);
        r = -1;
    }
    if (r < 0) { snd_pcm_close(o->pcm); o->pcm = NULL; return -1; }
    return 0;
}
#endif

struct output *output_open(const struct pcm_format *f, int outputway, int hw) {
    static int opens;
    struct output *o = calloc(1, sizeof *o);
    const char *dir = getenv("Q2PLAYER_PCM");
    if (!o) return NULL;
    o->f = *f;
    o->speed = getenv("Q2PLAYER_SPEED") ? atof(getenv("Q2PLAYER_SPEED")) : 1;
    if (o->speed <= 0) o->speed = 1;
    if (dir) {
        char path[1024];
        snprintf(path, sizeof path, "%s/%04d_%d_%d_%d.raw", dir, opens++, f->rate, f->channels, f->bytes);
        if (!(o->file = fopen(path, "wb"))) { perror(path); free(o); return NULL; }
        clock_gettime(CLOCK_MONOTONIC, &o->t0);
        return o;
    }
#ifdef HAVE_ALSA
    if (alsa_open(o, outputway, hw) == 0) return o;
#else
    (void)outputway, (void)hw;
    fprintf(stderr, "q2player: built without ALSA; set Q2PLAYER_PCM\n");
#endif
    free(o);
    return NULL;
}

int64_t output_delay(struct output *o) {
#ifdef HAVE_ALSA
    if (o->pcm) {
        snd_pcm_sframes_t d = 0;
        return snd_pcm_delay(o->pcm, &d) < 0 || d < 0 ? 0 : d;
    }
#endif
    int64_t played = (int64_t)((o->paused ? between(&o->t0, &o->paused_at) : since(&o->t0)) * o->speed * o->f.rate);
    return o->written > played ? o->written - played : 0;
}

int output_write(struct output *o, const uint8_t *data, int frames) {
#ifdef HAVE_ALSA
    if (o->pcm) {
        while (frames > 0) {
            snd_pcm_sframes_t n = snd_pcm_writei(o->pcm, data, frames);
            if (n == -EINTR || n == -EAGAIN) continue;
            if (n == -EPIPE || n == -ESTRPIPE) { /* the buffer ran dry: start again from here */
                if (snd_pcm_prepare(o->pcm) < 0) return -1;
                continue;
            }
            if (n < 0) return -1;
            data += n * o->f.channels * o->f.bytes, frames -= n;
        }
        return 0;
    }
#endif
    if (fwrite(data, (size_t)o->f.channels * o->f.bytes, frames, o->file) != (size_t)frames) return -1;
    if ((int64_t)(since(&o->t0) * o->speed * o->f.rate) > o->written) { /* it ran dry: the clock starts again here */
        clock_gettime(CLOCK_MONOTONIC, &o->t0);
        o->written = 0;
    }
    o->written += frames;
    int64_t ahead = output_delay(o) - (int64_t)o->f.rate * BUFFER_US / 1000000;
    if (ahead > 0) usleep((useconds_t)(ahead * 1000000 / o->f.rate / o->speed));
    return 0;
}

void output_pause(struct output *o, int on) {
    if (o->paused == on) return;
    o->paused = on;
#ifdef HAVE_ALSA
    if (o->pcm) {
        /* A device that cannot pause keeps playing what it holds, then waits for more. */
        snd_pcm_pause(o->pcm, on);
        return;
    }
#endif
    if (on) clock_gettime(CLOCK_MONOTONIC, &o->paused_at);
    else {
        double gap = since(&o->paused_at);
        o->t0.tv_sec += (time_t)gap;
        o->t0.tv_nsec += (long)((gap - (time_t)gap) * 1e9);
        if (o->t0.tv_nsec >= 1000000000) o->t0.tv_sec++, o->t0.tv_nsec -= 1000000000;
    }
}

void output_drop(struct output *o) {
#ifdef HAVE_ALSA
    if (o->pcm) {
        snd_pcm_drop(o->pcm);
        snd_pcm_prepare(o->pcm);
        o->paused = 0;
        return;
    }
#endif
    fflush(o->file);
    clock_gettime(CLOCK_MONOTONIC, &o->t0);
    o->written = 0, o->paused = 0;
}

void output_drain(struct output *o) {
#ifdef HAVE_ALSA
    if (o->pcm) { snd_pcm_drain(o->pcm); return; }
#endif
    fflush(o->file);
    int64_t d = output_delay(o);
    if (d > 0) usleep((useconds_t)(d * 1000000 / o->f.rate / o->speed));
}

void output_close(struct output *o) {
    if (!o) return;
#ifdef HAVE_ALSA
    if (o->pcm) snd_pcm_close(o->pcm);
#endif
    if (o->file) fclose(o->file);
    free(o);
}
