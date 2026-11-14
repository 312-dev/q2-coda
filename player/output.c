/* Where PCM goes. On the Q2 that is ALSA, opened as stock opens it (docs/player.md#output): the device by output
 * way and hardware type, the file's own rate and width with ALSA's resampling off, a 500 ms buffer in 16 periods.
 * With Q2PLAYER_PCM set to a directory it is a file a test reads back: one per open, NNNN_RATE_CH_FORMAT.raw (s16, s32 or f32),
 * written as fast as it would play, or Q2PLAYER_SPEED times as fast (a test's clock runs that much faster too). */
#include "q2player.h"
#include <errno.h>
#include <math.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
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
    /* what was written last, for a click mixed in by rewinding (output_click): a ring of hist_cap frames, its
     * newest frame before hist_at, hist_n of it valid; and the click playing, as frames still to mix */
    uint8_t *hist, *tmp;
    int hist_cap, hist_at, hist_n, tmp_cap;
    const float *clk, *duck;
    int clk_len, clk_at;
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
 * local DAC as "default" on a Q2 (hardware type 17); types 14 and 15 are other Shanling players' "plughw:1,0".
 * "default" is a plug over the DAC, which is what takes a float decoder's FLOAT_LE.
 * ponytail: on hw:2,0 a format the DAC lacks fails here; stock falls back to an integer format (phase 4). */
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
        (r = snd_pcm_hw_params_set_format(o->pcm, p, o->f.is_float ? SND_PCM_FORMAT_FLOAT_LE : o->f.bytes == 2 ? SND_PCM_FORMAT_S16_LE : SND_PCM_FORMAT_S32_LE)) < 0 ||
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

/* ---- the headphone DAC's controls, /dev/shanling_dac (codec_cs43131.ko), called as stock's player calls them
 * (docs/player.md#output; the order is the emulator shim's log of stock's): each song sets PCM mode and sends the
 * init request, both with 0; the line is muted before the PCM opens, closes, pauses or drops for a seek, and unmuted
 * after. Only on output way 0. The unmute lets the line go 90-100 ms later and the volume ramps back, so where a
 * person is waiting for sound (a press, a seek, a resume) the first frame follows DAC_RELEASE_MS after it, as in
 * Q2 Pod's player (docs/internals.md#wired-start). */
enum { DAC_MODE = 0xc0044d1b, DAC_INIT = 0xc0044d0a, DAC_MUTE = 0xc0044d1f };
enum { DAC_RELEASE_MS = 130, DAC_PRESS_MS = 5000 };
static int dac_fd = -2, dac_muted;

static void dac_ioctl(unsigned long request, int value) {
    if (dac_fd == -2) dac_fd = open("/dev/shanling_dac", O_RDWR | O_CLOEXEC); /* kept open, as stock's is */
    if (dac_fd >= 0) ioctl(dac_fd, request, &value);
}

void dac_song(int outputway) {
    if (outputway) return;
    dac_ioctl(DAC_MODE, 0); /* PCM; DSD's modes are phase 4's */
    dac_ioctl(DAC_INIT, 0);
}

void dac_mute(int outputway) {
    if (outputway) return;
    dac_ioctl(DAC_MUTE, 1);
    dac_muted = 1;
}

/* Unmutes what dac_mute muted, once; with wait, sound is held back until the line is open. */
void dac_unmute(int outputway, int wait) {
    if (outputway || !dac_muted) return;
    dac_ioctl(DAC_MUTE, 0);
    dac_muted = 0;
    if (wait) usleep(DAC_RELEASE_MS * 1000);
}

/* demo's word in /tmp/q2press (patch/playback.c's press_stamp): CLOCK_MONOTONIC in ms, odd for a person's press, even
 * for a natural start demo did not mute for. It counts when younger than DAC_PRESS_MS, and each word once. */
int dac_stamp(int pressed) {
    static int seen;
    int at = 0, fd = open("/tmp/q2press", O_RDONLY | O_CLOEXEC);
    if (fd < 0) return 0;
    if (read(fd, &at, sizeof at) != sizeof at) at = 0;
    close(fd);
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    unsigned now = (unsigned)(t.tv_sec * 1000ULL + t.tv_nsec / 1000000);
    if (!at || (at & 1) != pressed || at == seen || now - (unsigned)at >= DAC_PRESS_MS) return 0;
    seen = at;
    return 1;
}

static int frame_bytes(const struct output *o) { return o->f.channels * o->f.bytes; }

/* The history is the buffer's length: the most a click can rewind. With no memory for it, clicks come late. */
static struct output *history(struct output *o) {
    o->hist_cap = (int)((int64_t)o->f.rate * BUFFER_US / 1000000);
    if (!(o->hist = malloc((size_t)o->hist_cap * frame_bytes(o)))) o->hist_cap = 0;
    return o;
}

static void remember(struct output *o, const uint8_t *data, int frames) {
    int fb = frame_bytes(o);
    if (!o->hist_cap) return;
    if (frames > o->hist_cap) data += (size_t)(frames - o->hist_cap) * fb, frames = o->hist_cap;
    int m = frames < o->hist_cap - o->hist_at ? frames : o->hist_cap - o->hist_at;
    memcpy(o->hist + (size_t)o->hist_at * fb, data, (size_t)m * fb);
    memcpy(o->hist, data + (size_t)m * fb, (size_t)(frames - m) * fb);
    o->hist_at = (o->hist_at + frames) % o->hist_cap;
    o->hist_n = o->hist_n + frames < o->hist_cap ? o->hist_n + frames : o->hist_cap;
}

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
        snprintf(path, sizeof path, "%s/%04d_%d_%d_%s.raw", dir, opens++, f->rate, f->channels, f->is_float ? "f32" : f->bytes == 2 ? "s16" : "s32");
        if (!(o->file = fopen(path, "wb"))) { perror(path); free(o); return NULL; }
        clock_gettime(CLOCK_MONOTONIC, &o->t0);
        return history(o);
    }
#ifdef HAVE_ALSA
    if (alsa_open(o, outputway, hw) == 0) return history(o);
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

static int sink(struct output *o, const uint8_t *data, int frames) {
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

/* ---- the Clicker's click in the headphones (docs/internals.md#key-tone): the payload sends {mcl-click\KIND} while
 * a song plays, and the click is mixed into what is about to be heard. The PCM holds up to half a second, so the
 * output rewinds to CLICK_LEAD_MS past what is playing, mixes the click into its own copy of those frames and writes
 * them again; where nothing can be rewound (no backlog, a device that cannot rewind) the click starts with the next
 * frames written. The mix makes room for the click: y = x * (1 - e) + e * s, with e the click's envelope (at most
 * its level) and |s| <= 1, so a sample within full scale stays within it and the conversion never clips. The level
 * is the PCM's, so the DAC's volume, the headphones' own, scales it with the music. */
#define CLICK_LEAD_MS 20 /* past the PCM's place, for the rewind and rewrite to land before it gets there */
#define CLICK_DB (-20.0) /* the click's peak, dB of full scale: tune by ear */
#define CLICK_ATTACK_MS 0.25 /* the envelope's rise and fall, so the music's dip under it has no step either */
#define CLICK_TAPER_MS 0.5
/* Rockbox's iPod piezo (firmware/target/arm/ipod/piezo.c, Hz = 91225 / period): a wheel click about 1,000 Hz for
 * 4 ms, a button's about 1,820 Hz for 3 ms, an alert's about 2,280 Hz for 200 ms. Each is a pulse exciting a small
 * resonator, a ringing piezo: a sine at its pitch dying away with time constant decay_ms. */
static const struct { double hz, ms, decay_ms; } SOUNDS[] = {
    [CLICK_WHEEL] = {1000, 4, 1.2}, [CLICK_BUTTON] = {1820, 3, 0.9}, [CLICK_BEEP] = {2280, 200, 60}};

/* The sound kind at rate, as samples (clk) and its envelope (duck), made at the first use of each. */
static int click_table(int kind, int rate, const float **clk, const float **duck) {
    static struct { int rate, len; float *s, *e; } made[3];
    if (kind < CLICK_WHEEL || kind > CLICK_BEEP) return 0;
    __typeof__(made[0]) *m = &made[kind - CLICK_WHEEL];
    if (m->rate != rate) {
        int len = (int)(SOUNDS[kind].ms * rate / 1000);
        float *s = realloc(m->s, (size_t)len * sizeof *s), *e = realloc(m->e, (size_t)len * sizeof *e);
        if (s) m->s = s;
        if (e) m->e = e;
        if (!s || !e || len <= 0) return m->rate = 0, 0;
        double level = pow(10, CLICK_DB / 20), rise = CLICK_ATTACK_MS * rate / 1000, fall = CLICK_TAPER_MS * rate / 1000;
        for (int i = 0; i < len; ++i) {
            double t = (double)i / rate, env = level * exp(-t * 1000 / SOUNDS[kind].decay_ms);
            if (i < rise) env *= 0.5 - 0.5 * cos(M_PI * i / rise);
            if (len - i < fall) env *= 0.5 - 0.5 * cos(M_PI * (len - i) / fall);
            e[i] = (float)env, s[i] = (float)(env * sin(2 * M_PI * SOUNDS[kind].hz * t));
        }
        m->rate = rate, m->len = len;
    }
    *clk = m->s, *duck = m->e;
    return m->len;
}

/* The click, from where it is, into frames of data in place, in the output's format. */
static void click_mix(struct output *o, uint8_t *data, int frames) {
    int ch = o->f.channels;
    for (int i = 0; i < frames && o->clk_at < o->clk_len; ++i, ++o->clk_at) {
        float g = 1 - o->duck[o->clk_at], c = o->clk[o->clk_at];
        for (int k = 0; k < ch; ++k) {
            int j = i * ch + k;
            if (o->f.is_float) ((float *)data)[j] = ((float *)data)[j] * g + c;
            else if (o->f.bytes == 2) {
                float v = (((int16_t *)data)[j] * (1.0f / 32768) * g + c) * 32768;
                ((int16_t *)data)[j] = v >= 32767 ? 32767 : v <= -32768 ? -32768 : (int16_t)lrintf(v);
            } else {
                double v = (((int32_t *)data)[j] * (1.0 / 2147483648.0) * g + c) * 2147483648.0;
                ((int32_t *)data)[j] = v >= 2147483647.0 ? 2147483647 : v <= -2147483648.0 ? INT32_MIN : (int32_t)llrint(v);
            }
        }
    }
}

static uint8_t *scratch(struct output *o, int frames) {
    if (frames > o->tmp_cap) {
        uint8_t *t = realloc(o->tmp, (size_t)frames * frame_bytes(o));
        if (!t) return NULL;
        o->tmp = t, o->tmp_cap = frames;
    }
    return o->tmp;
}

int output_write(struct output *o, const uint8_t *data, int frames) {
    uint8_t *t;
    if (o->clk_at < o->clk_len && (t = scratch(o, frames))) { /* a click still sounding goes on in these frames */
        memcpy(t, data, (size_t)frames * frame_bytes(o));
        click_mix(o, t, frames);
        data = t;
    }
    remember(o, data, frames);
    return sink(o, data, frames);
}

/* Frames back from the newest that can be written again, at most want. */
static int rewind_by(struct output *o, int want) {
#ifdef HAVE_ALSA
    if (o->pcm) {
        snd_pcm_sframes_t can = snd_pcm_rewindable(o->pcm);
        if (can < want) want = can > 0 ? (int)can : 0;
        snd_pcm_sframes_t got = want ? snd_pcm_rewind(o->pcm, (snd_pcm_uframes_t)want) : 0;
        return got > 0 ? (int)got : 0;
    }
#endif
    fflush(o->file);
    if (fseeko(o->file, -(off_t)want * frame_bytes(o), SEEK_END)) return 0;
    o->written -= want;
    return want;
}

int output_click(struct output *o, int kind) {
    if (!o || !(o->clk_len = click_table(kind, o->f.rate, &o->clk, &o->duck))) return -1;
    o->clk_at = 0;
    int want = (int)(output_delay(o) - (int64_t)o->f.rate * CLICK_LEAD_MS / 1000), fb = frame_bytes(o);
    if (want > o->hist_n) want = o->hist_n;
    if (want <= 0 || !scratch(o, want)) return 0; /* the click starts with the next frames written */
    int back = rewind_by(o, want);
    if (!back) return 0;
    int from = (o->hist_at - back + o->hist_cap) % o->hist_cap, m = back < o->hist_cap - from ? back : o->hist_cap - from;
    memcpy(o->tmp, o->hist + (size_t)from * fb, (size_t)m * fb);
    memcpy(o->tmp + (size_t)m * fb, o->hist, (size_t)(back - m) * fb);
    click_mix(o, o->tmp, back);
    o->hist_at = from, o->hist_n -= back; /* written again, so remembered again */
    remember(o, o->tmp, back);
    return sink(o, o->tmp, back);
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
    o->hist_n = 0, o->clk_at = o->clk_len; /* what was written is gone, and a click with it */
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
    free(o->hist);
    free(o->tmp);
    free(o);
}
