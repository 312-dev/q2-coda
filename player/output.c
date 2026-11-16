/* Where PCM goes. On the Q2 that is ALSA, opened as stock opens it (docs/player.md#output): the device by output
 * way and hardware type, the file's own rate and width with ALSA's resampling off, a 500 ms buffer in 16 periods,
 * and stock's software parameters (alsa_sw: a dry PCM plays on through silence, and sink moves past what it missed).
 * Bluetooth is asked for 48 kHz, as stock asks it, and plays at the rate bluealsa gives (its transport's), the song
 * resampled to it here; a device that refuses the song's width is given another (stock's fallback, and S24_3LE);
 * Bluetooth's and, when demo says so, a USB DAC's volume is done here in software, by stock's law. With
 * Q2PLAYER_PCM set to a directory it is a file a test reads back: one per open, NNNN_RATE_CH_FORMAT[_WAY].raw (s16,
 * s24, s32, f32, dop or dsd; _bt or _usb off the DAC), written as fast as it would play, or Q2PLAYER_SPEED times as
 * fast (a test's clock runs that much faster too). There Q2PLAYER_BT_RATE stands for the transport's rate (48000
 * unset) and Q2PLAYER_FORMATS, a list such as "s16,s24", for the only formats a device takes. */
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
#include <libavutil/channel_layout.h>
#include <libavutil/opt.h>
#include <libswresample/swresample.h>
#ifdef HAVE_ALSA
#include "alsa_min.h"
#endif

enum { BUFFER_US = 500000, PERIODS = 16 };
enum { BT_ASK = 48000 }; /* what stock asks bluealsa for, whatever the file (reinit_audio_chain, 0x42ecc0) */

struct output {
    struct pcm_format in; /* what is written: the song's */
    struct pcm_format f;  /* what the device takes */
    int way;
#ifdef HAVE_ALSA
    snd_pcm_t *pcm;
    snd_pcm_uframes_t ring, period; /* the buffer and a period, in frames, as hw_params set them */
#endif
    /* software volume (output_volume), and Bluetooth's resampler from the song's rate to the device's */
    float gain[2];
    int gained, vol16[2];
    SwrContext *swr;
    float *fbuf, *rbuf;
    int16_t *sbuf;
    uint8_t *dbuf;
    int fcap, rcap, dcap, scap;
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

static int same_format(const struct pcm_format *a, const struct pcm_format *b) {
    return a->rate == b->rate && a->channels == b->channels && a->bytes == b->bytes && a->is_float == b->is_float && a->dsd == b->dsd;
}

/* buf holding at least need elements of unit bytes; cap counts elements */
static int grow(void **buf, int *cap, int need, int unit) {
    if (need <= *cap) return 0;
    void *b = realloc(*buf, (size_t)need * unit);
    if (!b) return -1;
    *buf = b, *cap = need;
    return 0;
}

/* The formats a device is offered for f, in turn: f's own, then stock's fallback ("Format %s is not supported by
 * hardware, trying default", 0x439060: S16_LE, then S32_LE), with S32_LE and S24_3LE first for a song wider than 16
 * bits so that a USB DAC without S32_LE keeps 24 of them. DSD has its one format. Each is {bytes, is_float}. */
static int candidates(const struct pcm_format *f, int out[4][2]) {
    int n = 0, wide = f->is_float || f->bytes > 2;
    out[n][0] = f->bytes, out[n++][1] = f->is_float;
    if (f->dsd) return n;
    static const int order_wide[3][2] = {{4, 0}, {3, 0}, {2, 0}}, order_16[3][2] = {{2, 0}, {4, 0}, {3, 0}};
    for (int i = 0; i < 3; ++i) {
        const int *c = wide ? order_wide[i] : order_16[i];
        if (c[0] == f->bytes && c[1] == f->is_float) continue;
        out[n][0] = c[0], out[n++][1] = c[1];
    }
    return n;
}

#ifdef HAVE_ALSA
/* Stock's ao_alsa init (hciplayer 0x438f64-0x4391f8): Bluetooth through bluealsa, a USB DAC as card 1 on a Q2
 * (hardware type 17; captured), and the local DAC as "default"; types 14 and 15 are other Shanling players'
 * "plughw:1,0". "default" is a plug over the DAC, which is what takes a float decoder's FLOAT_LE. */
static const char *device(int outputway, int hw) {
    const char *forced = getenv("Q2PLAYER_DEVICE");
    if (forced) return forced;
    if (outputway == 1) return "plug:bluealsa";
    if (outputway == 2) return "hw:1,0";
    if (hw == 14 || hw == 15) return "plughw:1,0";
    if (hw == 7 || hw == 11) return "hw:1,0";
    return "default";
}

static int alsa_format(const struct pcm_format *f) {
    if (f->dsd == DSD_DOP) return SND_PCM_FORMAT_S24_3LE;
    if (f->dsd == DSD_NATIVE) return SND_PCM_FORMAT_DSD_U32_BE;
    return f->is_float ? SND_PCM_FORMAT_FLOAT_LE : f->bytes == 2 ? SND_PCM_FORMAT_S16_LE : f->bytes == 3 ? SND_PCM_FORMAT_S24_3LE : SND_PCM_FORMAT_S32_LE;
}

/* Stock's software parameters (ao_alsa's init, 0x438d64-0x438dc4, the same on every output way): the stream starts
 * once a period is written, and stop_threshold and silence_size are the boundary, so a PCM that runs dry is never
 * stopped: the hardware plays the silence ALSA fills the ring with and waits, where at ALSA's defaults (start at the
 * first frame, stop at a buffer's worth of underrun) it would stop and start the stream again (docs/player.md#output).
 * What is written after such an underrun lands behind hw_ptr, so sink() moves past it first. avail_min (a period)
 * and silence_threshold (0) are ALSA's defaults, which stock leaves too. */
static int alsa_sw(struct output *o) {
    snd_pcm_sw_params_t *s = NULL;
    snd_pcm_uframes_t boundary = 0;
    int r;
    if ((r = snd_pcm_get_params(o->pcm, &o->ring, &o->period)) < 0 || (r = snd_pcm_sw_params_malloc(&s)) < 0) return r;
    if ((r = snd_pcm_sw_params_current(o->pcm, s)) >= 0 && (r = snd_pcm_sw_params_get_boundary(s, &boundary)) >= 0 &&
        (r = snd_pcm_sw_params_set_start_threshold(o->pcm, s, o->period)) >= 0 &&
        (r = snd_pcm_sw_params_set_stop_threshold(o->pcm, s, boundary)) >= 0 &&
        (r = snd_pcm_sw_params_set_silence_size(o->pcm, s, boundary)) >= 0)
        r = snd_pcm_sw_params(o->pcm, s);
    snd_pcm_sw_params_free(s);
    return r;
}

static int alsa_open(struct output *o, int outputway, int hw) {
    snd_pcm_hw_params_t *p = NULL;
    unsigned want = outputway == 1 ? BT_ASK : (unsigned)o->f.rate, rate = want, buffer = BUFFER_US, periods = PERIODS;
    const char *name = device(outputway, hw);
    int r = snd_pcm_open(&o->pcm, name, SND_PCM_STREAM_PLAYBACK, 0), c[4][2], n = candidates(&o->f, c), i = 0;
    if (r < 0) { fprintf(stderr, "q2player: %s: %s\n", name, snd_strerror(r)); return -1; }
    if ((r = snd_pcm_hw_params_malloc(&p)) < 0 || (r = snd_pcm_hw_params_any(o->pcm, p)) < 0 ||
        (r = snd_pcm_hw_params_set_access(o->pcm, p, SND_PCM_ACCESS_RW_INTERLEAVED)) < 0)
        goto done;
    for (; i < n; ++i) {
        struct pcm_format t = o->f;
        t.bytes = c[i][0], t.is_float = c[i][1];
        if (snd_pcm_hw_params_test_format(o->pcm, p, alsa_format(&t)) >= 0) break;
        fprintf(stderr, "q2player: %s takes no format %d\n", name, alsa_format(&t));
    }
    if (i == n) { r = -EINVAL; goto done; }
    o->f.bytes = c[i][0], o->f.is_float = c[i][1];
    if ((r = snd_pcm_hw_params_set_format(o->pcm, p, alsa_format(&o->f))) < 0 ||
        (r = snd_pcm_hw_params_set_channels(o->pcm, p, o->f.channels)) < 0 ||
        (r = snd_pcm_hw_params_set_rate_resample(o->pcm, p, 0)) < 0 ||
        (r = snd_pcm_hw_params_set_rate_near(o->pcm, p, &rate, NULL)) < 0 ||
        (r = snd_pcm_hw_params_set_buffer_time_near(o->pcm, p, &buffer, NULL)) < 0 ||
        (r = snd_pcm_hw_params_set_periods_near(o->pcm, p, &periods, NULL)) < 0 ||
        (r = snd_pcm_hw_params(o->pcm, p)) < 0 || (r = alsa_sw(o)) < 0 || (r = snd_pcm_prepare(o->pcm)) < 0)
        ;
done:
    if (r < 0) fprintf(stderr, "q2player: %s %d Hz %d ch %d bytes: %s\n", name, o->f.rate, o->f.channels, o->f.bytes, snd_strerror(r));
    if (p) snd_pcm_hw_params_free(p);
    /* a device that plays another rate would play the song off speed; Bluetooth's is resampled to */
    if (r >= 0 && outputway != 1 && rate != want) {
        fprintf(stderr, "q2player: %s gave %u Hz for %u\n", name, rate, want);
        r = -1;
    }
    if (r < 0) { snd_pcm_close(o->pcm); o->pcm = NULL; return -1; }
    o->f.rate = (int)rate;
    return 0;
}
#endif


/* ---- the headphone DAC's controls, /dev/shanling_dac (codec_cs43131.ko), called as stock's player calls them
 * (docs/player.md#output; the order is the emulator shim's log of stock's): each song sets the mode (dac_song), and
 * on output way 0 alone the line is muted before the PCM opens, closes, pauses or drops for a seek, and unmuted
 * after. The unmute lets the line go 90-100 ms later and the volume ramps back, so where a
 * person is waiting for sound (a press, a seek, a resume) the first frame follows DAC_RELEASE_MS after it, as in
 * Q2 Pod's player (docs/internals.md#wired-start). */
enum { DAC_MODE = 0xc0044d1b, DAC_INIT = 0xc0044d0a, DAC_MUTE = 0xc0044d1f };
enum { DAC_RELEASE_MS = 130, DAC_PRESS_MS = 5000 };
static int dac_fd = -2, dac_muted;

static void dac_ioctl(unsigned long request, int value) {
    if (dac_fd == -2) dac_fd = open("/dev/shanling_dac", O_RDWR | O_CLOEXEC); /* kept open, as stock's is */
    if (dac_fd >= 0) ioctl(dac_fd, request, &value);
}

/* Stock sends the mode at each song on every output, 1 for DoP and 0 otherwise (Native too, captured), and the init
 * request only on the DAC's. */
void dac_song(int outputway, int dsd) {
    dac_ioctl(DAC_MODE, dsd == DSD_DOP);
    if (!outputway) dac_ioctl(DAC_INIT, 0);
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

/* The file sink as a device: Q2PLAYER_FORMATS, when set, is all it takes, and on Bluetooth it plays at
 * Q2PLAYER_BT_RATE, as bluealsa at its transport's. */
static const char *sink_name(const struct pcm_format *f) {
    return f->dsd == DSD_DOP ? "dop" : f->dsd ? "dsd" : f->is_float ? "f32" : f->bytes == 2 ? "s16" : f->bytes == 3 ? "s24" : "s32";
}

static int sink_open(struct output *o, const char *dir, int outputway) {
    static int opens;
    const char *only = getenv("Q2PLAYER_FORMATS"), *bt = getenv("Q2PLAYER_BT_RATE");
    int c[4][2], n = candidates(&o->f, c), i = 0;
    for (; only && i < n; ++i) {
        struct pcm_format t = o->f;
        t.bytes = c[i][0], t.is_float = c[i][1];
        if (strstr(only, sink_name(&t))) break;
    }
    if (i == n) return -1;
    o->f.bytes = c[i][0], o->f.is_float = c[i][1];
    if (outputway == 1) o->f.rate = bt && atoi(bt) > 0 ? atoi(bt) : BT_ASK;
    char path[1024];
    snprintf(path, sizeof path, "%s/%04d_%d_%d_%s%s.raw", dir, opens++, o->f.rate, o->f.channels, sink_name(&o->f),
             outputway == 1 ? "_bt" : outputway == 2 ? "_usb" : "");
    if (!(o->file = fopen(path, "wb"))) { perror(path); return -1; }
    clock_gettime(CLOCK_MONOTONIC, &o->t0);
    return 0;
}

/* Bluetooth's resampler from the song's rate to the device's, with stock's settings: MPlayer's lavcresample, which
 * stock appends for a file at another rate (af_lavcresample: filter length 16, phase shift 10, not linear, cutoff
 * max(1 - 6.5 / (16 + 8), 0.8)), here on float. docs/internals.md#bluetooth-resampler */
static int resampler(struct output *o) {
    swr_free(&o->swr);
    if (o->in.rate == o->f.rate) return 0;
    AVChannelLayout l;
    av_channel_layout_default(&l, o->f.channels);
    if (swr_alloc_set_opts2(&o->swr, &l, AV_SAMPLE_FMT_FLT, o->f.rate, &l, AV_SAMPLE_FMT_FLT, o->in.rate, 0, NULL) < 0) return -1;
    av_opt_set_int(o->swr, "filter_size", 16, 0);
    av_opt_set_int(o->swr, "phase_shift", 10, 0);
    av_opt_set_int(o->swr, "linear_interp", 0, 0);
    av_opt_set_double(o->swr, "cutoff", 0.8, 0);
    if (swr_init(o->swr) < 0) { swr_free(&o->swr); return -1; }
    return 0;
}

struct output *output_open(const struct pcm_format *f, int outputway, int hw) {
    struct output *o = calloc(1, sizeof *o);
    const char *dir = getenv("Q2PLAYER_PCM");
    if (!o) return NULL;
    o->in = o->f = *f, o->way = outputway;
    o->gain[0] = o->gain[1] = 1;
    o->speed = getenv("Q2PLAYER_SPEED") ? atof(getenv("Q2PLAYER_SPEED")) : 1;
    if (o->speed <= 0) o->speed = 1;
    int r = -1;
    if (dir) r = sink_open(o, dir, outputway);
#ifdef HAVE_ALSA
    else r = alsa_open(o, outputway, hw);
#else
    else (void)hw, fprintf(stderr, "q2player: built without ALSA; set Q2PLAYER_PCM\n");
#endif
    if (r == 0 && resampler(o) == 0) return history(o);
    output_close(o);
    return NULL;
}

/* The device's frames written and not yet played. */
static int64_t dev_delay(struct output *o) {
#ifdef HAVE_ALSA
    if (o->pcm) {
        snd_pcm_sframes_t d = 0;
        return snd_pcm_delay(o->pcm, &d) < 0 || d < 0 ? 0 : d;
    }
#endif
    int64_t played = (int64_t)((o->paused ? between(&o->t0, &o->paused_at) : since(&o->t0)) * o->speed * o->f.rate);
    return o->written > played ? o->written - played : 0;
}

int64_t output_delay(struct output *o) {
    int64_t d = dev_delay(o);
    if (o->in.rate != o->f.rate) d = d * o->in.rate / o->f.rate;
    return d + (o->swr ? swr_get_delay(o->swr, o->in.rate) : 0);
}

/* ---- software volume: stock's mixer_setvolume (0x4287a4) as mcl-setvol reaches it (0x42c858), the bytes L and R of
 * L << 8 | R, as floats. Either at 100 takes the volume filter out: unity. Otherwise its vendor's law: from 50 up a
 * level is 0.1 + 0.018 per step past 50, so 255 is 3.79 (+11.6 dB); below 50 it is 0.002 a step (soft_vol_max 20
 * times 1e-4). Which of the two is chosen by L alone, so R's own law goes with it; a level below 0 (R well below L) is
 * taken here as silence, where stock's dB of it is not a number. MPlayer's volume filter then scales a 16-bit song in
 * integers, (x * (int)(255 * level)) >> 8 saturated (so 50 is 25/256, 0.0977: captured), and anything wider in float,
 * x * level clipped at full scale. */
void volume_gains(int volume, float gain[2]) {
    int l = volume >> 8 & 255, r = volume & 255;
    double g[2] = {l * 0.002, r * 0.002};
    if (l == 100 || r == 100) g[0] = g[1] = 1;
    else if (l >= 50) g[0] = 0.1 + 0.018 * (l - 50), g[1] = 0.1 + 0.018 * (r - 50);
    for (int i = 0; i < 2; ++i) gain[i] = g[i] > 0 ? (float)g[i] : 0;
}

void output_volume(struct output *o, int volume) {
    if (!o || o->f.dsd) return; /* DSD's bits go out as they are */
    volume_gains(volume, o->gain);
    o->gained = o->gain[0] != 1 || o->gain[1] != 1;
    for (int i = 0; i < 2; ++i) o->vol16[i] = (int)(255.0 * o->gain[i]);
}

/* the gain a channel's samples are given: a 16-bit song's in steps of 1/256 */
static float gain_of(const struct output *o, int ch) {
    int k = ch < 2 ? ch : 0;
    return o->in.bytes == 2 && !o->in.is_float ? o->vol16[k] / 256.0f : o->gain[k];
}

int output_take(struct output *o, const struct pcm_format *f, int outputway) {
    if (!o || outputway != o->way) return 0;
    if (outputway != 1 || f->dsd || o->in.dsd) return same_format(f, &o->in);
    if (f->channels != o->in.channels || f->bytes != o->in.bytes || f->is_float != o->in.is_float) return 0; /* stock asks the song's width */
    if (f->rate != o->in.rate) { /* the old rate's resampler: what it holds back is played, then a fresh one */
        if (o->swr) {
            int n = swr_get_out_samples(o->swr, 0);
            if (n > 0 && !grow((void **)&o->rbuf, &o->rcap, n * o->f.channels, sizeof(float))) {
                n = swr_convert(o->swr, (uint8_t **)&o->rbuf, n, NULL, 0);
                if (n > 0) output_write(o, NULL, n);
            }
        }
        o->in = *f;
        if (resampler(o) < 0) return 0;
    }
    o->in = *f;
    return 1;
}

/* frames of the song's, in o->in's format, as interleaved float in o->fbuf, times the volume, clipped at full scale */
static int to_float(struct output *o, const uint8_t *data, int frames) {
    int ch = o->in.channels, n = frames * ch;
    if (grow((void **)&o->fbuf, &o->fcap, n, sizeof(float))) return -1;
    float *x = o->fbuf;
    if (o->in.is_float) memcpy(x, data, (size_t)n * 4);
    else if (o->in.bytes == 2) for (int i = 0; i < n; ++i) x[i] = ((const int16_t *)data)[i] * (1.0f / 32768);
    else for (int i = 0; i < n; ++i) x[i] = (float)(((const int32_t *)data)[i] * (1.0 / 2147483648.0));
    if (o->gained && !(o->in.bytes == 2 && !o->in.is_float)) /* 16 bits were scaled already, in integers */
        for (int i = 0; i < n; ++i) {
            float v = x[i] * o->gain[i % ch < 2 ? i % ch : 0];
            x[i] = v > 1 ? 1 : v < -1 ? -1 : v;
        }
    return 0;
}

/* frames of float at x into o->dbuf in the device's format: rounded to nearest and saturated, as dsp.c's from_float
 * (hciplayer's converters) */
static int from_float(struct output *o, const float *x, int frames) {
    int n = frames * o->f.channels;
    if (grow((void **)&o->dbuf, &o->dcap, n, 4)) return -1;
    if (o->f.is_float) memcpy(o->dbuf, x, (size_t)n * 4);
    else if (o->f.bytes == 2)
        for (int i = 0; i < n; ++i) {
            float v = x[i] * 32768.0f;
            ((int16_t *)o->dbuf)[i] = v >= 32767.0f ? 32767 : v <= -32768.0f ? -32768 : (int16_t)lrintf(v);
        }
    else
        for (int i = 0; i < n; ++i) {
            double v = x[i] * 2147483648.0;
            int32_t w = v >= 2147483647.0 ? 2147483647 : v <= -2147483648.0 ? INT32_MIN : (int32_t)llrint(v);
            if (o->f.bytes == 4) ((int32_t *)o->dbuf)[i] = w;
            else { /* S24_3LE: the top three bytes, rounded */
                int64_t t = ((int64_t)w + 128) >> 8;
                if (t > 8388607) t = 8388607;
                uint8_t *d = o->dbuf + 3 * i;
                d[0] = (uint8_t)t, d[1] = (uint8_t)(t >> 8), d[2] = (uint8_t)(t >> 16);
            }
        }
    return 0;
}

/* frames in o->in's format made the device's: as they are when nothing differs, which is the bit-exact path */
static int convert(struct output *o, const uint8_t *data, int frames, const uint8_t **out) {
    if (!data) { /* the resampler's tail, already in o->rbuf */
        if (from_float(o, o->rbuf, frames)) return -1;
        *out = o->dbuf;
        return frames;
    }
    if (o->gained && o->in.bytes == 2 && !o->in.is_float) { /* af_volume's integer path */
        int ch = o->in.channels, n = frames * ch;
        if (grow((void **)&o->sbuf, &o->scap, n, 2)) return -1;
        for (int i = 0; i < n; ++i) {
            int v = ((const int16_t *)data)[i] * o->vol16[i % ch < 2 ? i % ch : 0] >> 8;
            o->sbuf[i] = (int16_t)(v > 32767 ? 32767 : v < -32768 ? -32768 : v);
        }
        data = (const uint8_t *)o->sbuf;
    }
    if (!o->swr && o->in.bytes == o->f.bytes && o->in.is_float == o->f.is_float && (!o->gained || o->in.bytes == 2)) {
        *out = data;
        return frames;
    }
    if (to_float(o, data, frames)) return -1;
    const float *x = o->fbuf;
    if (o->swr) {
        int room = swr_get_out_samples(o->swr, frames);
        if (grow((void **)&o->rbuf, &o->rcap, room * o->f.channels, sizeof(float))) return -1;
        frames = swr_convert(o->swr, (uint8_t **)&o->rbuf, room, (const uint8_t **)&o->fbuf, frames);
        if (frames < 0) return -1;
        x = o->rbuf;
    }
    if (from_float(o, x, frames)) return -1;
    *out = o->dbuf;
    return frames;
}

static int sink(struct output *o, const uint8_t *data, int frames) {
#ifdef HAVE_ALSA
    if (o->pcm) {
        /* Off Bluetooth a PCM that ran dry plays on (alsa_sw): hw_ptr is past appl_ptr, and frames written there would
         * be lost until appl_ptr caught up, heard as the song jumping ahead. So appl_ptr moves to a period past hw_ptr,
         * over the silence ALSA left there: a gap, and the song carries on where it stopped (as Q2 Pod's player does,
         * patch/peq_player.c ao_write). What the history holds leads up to the gap, so a click no longer rewinds into
         * it. Bluetooth's plugin stops at its own underrun (docs/internals.md#bluetooth-resampler): EPIPE below. */
        snd_pcm_sframes_t avail = o->way != 1 ? snd_pcm_avail(o->pcm) : 0;
        if (avail > (snd_pcm_sframes_t)o->ring) {
            snd_pcm_forward(o->pcm, (snd_pcm_uframes_t)avail - o->ring + o->period);
            o->hist_n = 0;
        }
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
    int64_t ahead = dev_delay(o) - (int64_t)o->f.rate * BUFFER_US / 1000000;
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

/* The plug-in chirp (patch/charging.c, design/mockups/charge_chimes2.py's "8-click-up-chirp"): a CHIRP_TICK_MS
 * tick of one-pole low-passed noise (a seeded LCG's, so every chirp is the same), then CHIRP_LO_HZ and CHIRP_HI_HZ
 * blips with a 2 ms rise, their second harmonic at 0.15, at CHIRP_LO_MS and CHIRP_HI_MS; its peak CHIRP_DB. */
#define CHIRP_DB (-12.0)
#define CHIRP_MS 340
#define CHIRP_TICK_MS 3.0
#define CHIRP_TICK_SMOOTH 0.7 /* the low-pass's share of each new noise sample: near 1 a crisp tick */
#define CHIRP_TICK_AMP 0.7
#define CHIRP_LO_HZ 880.0
#define CHIRP_LO_MS 30.0
#define CHIRP_LO_LEN_MS 180.0
#define CHIRP_LO_DECAY_MS 50.0
#define CHIRP_HI_HZ 1320.0
#define CHIRP_HI_MS 90.0
#define CHIRP_HI_LEN_MS 250.0
#define CHIRP_HI_DECAY_MS 70.0
#define CHIRP_BLIP_AMP 0.6
#define CHIRP_RISE_MS 2.0

/* The chirp's raw sample i at rate and the most it could be (its components' envelopes), before the level. tick is
 * the tick's low-pass state, seed its noise's. */
static double chirp_at(int i, int rate, double *tick, uint32_t *seed, double *bound) {
    double t = (double)i / rate, y = 0, e = 0;
    if (t < CHIRP_TICK_MS / 1000) {
        *seed = *seed * 1664525u + 1013904223u; /* uniform noise in -1..1 */
        double noise = (double)(*seed >> 8) / (1 << 23) - 1;
        *tick = i ? CHIRP_TICK_SMOOTH * noise + (1 - CHIRP_TICK_SMOOTH) * *tick : 0;
        double env = CHIRP_TICK_AMP * exp(-t * 4000 / CHIRP_TICK_MS);
        y += env * *tick, e += env;
    }
    static const double blips[2][4] = {{CHIRP_LO_HZ, CHIRP_LO_MS, CHIRP_LO_LEN_MS, CHIRP_LO_DECAY_MS},
                                       {CHIRP_HI_HZ, CHIRP_HI_MS, CHIRP_HI_LEN_MS, CHIRP_HI_DECAY_MS}};
    for (int b = 0; b < 2; ++b) {
        double u = t - blips[b][1] / 1000;
        if (u < 0 || u >= blips[b][2] / 1000) continue;
        double env = CHIRP_BLIP_AMP * fmin(u * 1000 / CHIRP_RISE_MS, 1) * exp(-u * 1000 / blips[b][3]),
               ph = 2 * M_PI * blips[b][0] * u;
        y += env * (sin(ph) + 0.15 * sin(2 * ph)), e += env * 1.15;
    }
    *bound = e;
    return y;
}

/* The sound kind at rate, as samples (clk) and its envelope (duck), made at the first use of each. */
static int click_table(int kind, int rate, const float **clk, const float **duck) {
    static struct { int rate, len; float *s, *e; } made[CLICK_CHARGE];
    if (kind < CLICK_WHEEL || kind > CLICK_CHARGE) return 0;
    __typeof__(made[0]) *m = &made[kind - CLICK_WHEEL];
    if (m->rate != rate) {
        int len = (int)((kind == CLICK_CHARGE ? CHIRP_MS : SOUNDS[kind].ms) * rate / 1000);
        float *s = realloc(m->s, (size_t)len * sizeof *s), *e = realloc(m->e, (size_t)len * sizeof *e);
        if (s) m->s = s;
        if (e) m->e = e;
        if (!s || !e || len <= 0) return m->rate = 0, 0;
        if (kind == CLICK_CHARGE) { /* normalized to its peak: |s| <= e, so the mix's room holds */
            double tick = 0, peak = 0, bound, level = pow(10, CHIRP_DB / 20);
            uint32_t seed = 7;
            for (int i = 0; i < len; ++i) {
                double y = chirp_at(i, rate, &tick, &seed, &bound);
                s[i] = (float)y, e[i] = (float)bound, peak = fmax(peak, fabs(y));
            }
            for (int i = 0; i < len; ++i)
                s[i] = (float)(level * s[i] / peak), e[i] = (float)(level * fmin(e[i] / peak, 1));
        } else {
            double level = pow(10, CLICK_DB / 20), rise = CLICK_ATTACK_MS * rate / 1000, fall = CLICK_TAPER_MS * rate / 1000;
            for (int i = 0; i < len; ++i) {
                double t = (double)i / rate, env = level * exp(-t * 1000 / SOUNDS[kind].decay_ms);
                if (i < rise) env *= 0.5 - 0.5 * cos(M_PI * i / rise);
                if (len - i < fall) env *= 0.5 - 0.5 * cos(M_PI * (len - i) / fall);
                e[i] = (float)env, s[i] = (float)(env * sin(2 * M_PI * SOUNDS[kind].hz * t));
            }
        }
        m->rate = rate, m->len = len;
    }
    *clk = m->s, *duck = m->e;
    return m->len;
}

/* The click, from where it is, into frames of data in place, in the output's format. Software volume has scaled
 * what is written, so it scales the click too: x * (1 - e) + gain * c. */
static void click_mix(struct output *o, uint8_t *data, int frames) {
    int ch = o->f.channels;
    for (int i = 0; i < frames && o->clk_at < o->clk_len; ++i, ++o->clk_at) {
        float g = 1 - o->duck[o->clk_at], c = o->clk[o->clk_at];
        for (int k = 0; k < ch; ++k) {
            int j = i * ch + k;
            float cg = o->gained ? c * gain_of(o, k) : c;
            if (o->f.is_float) ((float *)data)[j] = ((float *)data)[j] * g + cg;
            else if (o->f.bytes == 2) {
                float v = (((int16_t *)data)[j] * (1.0f / 32768) * g + cg) * 32768;
                ((int16_t *)data)[j] = v >= 32767 ? 32767 : v <= -32768 ? -32768 : (int16_t)lrintf(v);
            } else if (o->f.bytes == 3) {
                uint8_t *d = data + 3 * j;
                int32_t x = (int32_t)((uint32_t)d[0] << 8 | (uint32_t)d[1] << 16 | (uint32_t)d[2] << 24) >> 8;
                double v = (x * (1.0 / 8388608.0) * g + cg) * 8388608.0;
                int32_t w = v >= 8388607.0 ? 8388607 : v <= -8388608.0 ? -8388608 : (int32_t)llrint(v);
                d[0] = (uint8_t)w, d[1] = (uint8_t)(w >> 8), d[2] = (uint8_t)(w >> 16);
            } else {
                double v = (((int32_t *)data)[j] * (1.0 / 2147483648.0) * g + cg) * 2147483648.0;
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

/* frames of data, the song's (NULL: as many of the resampler's tail, in o->rbuf), out through the volume and the
 * resampler to the device, with a click that is sounding mixed in. */
int output_write(struct output *o, const uint8_t *data, int frames) {
    uint8_t *t;
    if ((frames = convert(o, data, frames, &data)) <= 0) return frames;
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
    if (!o || o->f.dsd || !(o->clk_len = click_table(kind, o->f.rate, &o->clk, &o->duck))) return -1; /* not into DSD's bits */
    o->clk_at = 0;
    int want = (int)(dev_delay(o) - (int64_t)o->f.rate * CLICK_LEAD_MS / 1000), fb = frame_bytes(o);
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
    resampler(o);                          /* and what the resampler held back of it */
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
    int n = o->swr ? swr_get_out_samples(o->swr, 0) : 0; /* the resampler's tail is the song's too */
    if (n > 0 && !grow((void **)&o->rbuf, &o->rcap, n * o->f.channels, sizeof(float)) &&
        (n = swr_convert(o->swr, (uint8_t **)&o->rbuf, n, NULL, 0)) > 0)
        output_write(o, NULL, n);
#ifdef HAVE_ALSA
    if (o->pcm) { snd_pcm_drain(o->pcm); return; }
#endif
    fflush(o->file);
    int64_t d = dev_delay(o);
    if (d > 0) usleep((useconds_t)(d * 1000000 / o->f.rate / o->speed));
}

void output_close(struct output *o) {
    if (!o) return;
#ifdef HAVE_ALSA
    if (o->pcm) snd_pcm_close(o->pcm);
#endif
    if (o->file) fclose(o->file);
    swr_free(&o->swr);
    free(o->fbuf), free(o->rbuf), free(o->dbuf), free(o->sbuf);
    free(o->hist);
    free(o->tmp);
    free(o);
}
