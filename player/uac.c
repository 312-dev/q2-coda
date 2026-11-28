/* The USB audio sink (docs/usb-audio.md#the-player-s-output-in-the-gadget): the output tap's stream (tap.c) played
 * into the sound card of the q2adb gadget's UAC2 function, so a computer on the cable records the Q2's output as a
 * line input, while the DAC plays as ever.
 *
 * While UAC_FLAG is there (q2adb-gadget audio on makes it with the function), the thread opens the gadget card's
 * playback PCM at the rate the computer chose (Shanling's u_audio opens it at that rate alone, and says it as the
 * card's Playback Rate control, looked at every UAC_LOOK_MS: a change reopens). It takes the tap's frames, holds
 * the newest HOLD_MS back (the device's own buffer is half a second, BUFFER_US, so a take-back, a click mixed in or
 * a drop, is applied before they go, and the computer hears the frames about when the headphones do; with nothing
 * more for HOLD_MS, a song's end or a pause, the held frames go as the device plays them out), converts what
 * it lets go to float stereo, resamples it to the card's rate and keeps it in a buffer of at most MAX_MS, which it
 * writes a period at a time.
 *
 * Two clocks: the DAC's crystal paces what comes in, the computer's USB frames what goes out, so the buffer drifts.
 * Past MAX_MS the oldest is dropped to LEAD_MS; empty, a period of silence goes instead. Either is a click, counted
 * in the session's line; at USB's worst error, 500 ppm, a drop comes every few minutes. ponytail: a resampler that
 * followed the buffer's level (as q2dacloop's pitch control does the other way) would never click.
 *
 * Never the playback thread's: the tap drops what this thread does not take, so a computer that stops reading
 * (snd_pcm_wait times out: it closed its recording) costs the player nothing. DSD's bits are not PCM: silence.
 *
 * On a host build (no ALSA) Q2PLAYER_UAC names a file the card's frames go to (S32 stereo), at Q2PLAYER_UAC_RATE
 * (48000), paced by the clock and Q2PLAYER_SPEED, for test/player/run.py. */
#include "q2player.h"
#include <errno.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>
#include <libavutil/channel_layout.h>
#include <libswresample/swresample.h>
#ifdef HAVE_ALSA
#include "alsa_min.h"
#endif

#define UAC_FLAG PEQ_ROOT "/tmp/q2adb.audio"
#define UAC_CARD "hw:CARD=UAC2Gadget"
#define UAC_PCM UAC_CARD ",DEV=0"
enum { UAC_LOOK_MS = 500, HOLD_MS = 500, PERIOD_MS = 10, LEAD_MS = 60, MAX_MS = 300, WAIT_MS = 100 };

struct sink {
    int rate, bytes; /* the card's; bytes a sample, 4 or 2 */
#ifdef HAVE_ALSA
    snd_pcm_t *pcm;
    snd_ctl_t *ctl;
    snd_ctl_elem_value_t *val;
#endif
    FILE *file;
    double speed;
    struct timespec next;
};

/* ---- the stream's state: the source's format, the frames held back, the resampler, the card's buffer */
struct uac {
    struct pcm_format f;
    int pcm_ok;                     /* f is PCM: integer or float, not DSD */
    uint8_t *pend;                  /* frames held, in f */
    size_t pend_n, pend_cap;        /* bytes */
    float *x;                       /* frames let go, float stereo at f's rate */
    int x_cap;
    SwrContext *swr;
    float *out;                     /* the card's buffer, float stereo at its rate */
    int out_n, out_cap;
    int64_t frames, under, dropped; /* the session's: written to the card, periods of silence, frames dropped */
    uint8_t *rec;                   /* a record's payload */
    size_t rec_cap;
};

static int grow_to(void **p, size_t *cap, size_t need) {
    if (need <= *cap) return 0;
    size_t n = need * 2;
    void *q = realloc(*p, n);
    if (!q) return -1;
    *p = q, *cap = n;
    return 0;
}

static float sample(const uint8_t *s, const struct pcm_format *f) {
    if (f->is_float) { float v; memcpy(&v, s, 4); return v; }
    if (f->bytes == 2) { int16_t v; memcpy(&v, s, 2); return v / 32768.0f; }
    if (f->bytes == 3) return (int32_t)((uint32_t)s[0] << 8 | (uint32_t)s[1] << 16 | (uint32_t)s[2] << 24) / 2147483648.0f;
    int32_t v;
    memcpy(&v, s, 4);
    return (float)(v / 2147483648.0);
}

static void out_push(struct uac *u, const float *x, int n) {
    size_t cap = (size_t)u->out_cap * 8;
    if (grow_to((void **)&u->out, &cap, (size_t)(u->out_n + n) * 8)) return;
    u->out_cap = (int)(cap / 8);
    memcpy(u->out + 2 * u->out_n, x, (size_t)n * 8);
    u->out_n += n;
}

static void out_take(struct uac *u, int n) {
    if (n > u->out_n) n = u->out_n;
    memmove(u->out, u->out + 2 * n, (size_t)(u->out_n - n) * 8);
    u->out_n -= n;
}

/* The resampler from the source's rate to the card's, on float stereo; none when they are the same. */
static void resampler(struct uac *u, int to) {
    swr_free(&u->swr);
    if (!u->pcm_ok || u->f.rate == to) return;
    AVChannelLayout st = AV_CHANNEL_LAYOUT_STEREO;
    if (swr_alloc_set_opts2(&u->swr, &st, AV_SAMPLE_FMT_FLT, to, &st, AV_SAMPLE_FMT_FLT, u->f.rate, 0, NULL) < 0 ||
        swr_init(u->swr) < 0)
        swr_free(&u->swr);
}

/* The oldest n held frames let go: to float stereo, to the card's rate, into its buffer. */
static void let_go(struct uac *u, int n, int to) {
    size_t fb = (size_t)u->f.channels * u->f.bytes;
    if (n <= 0) return;
    size_t cap = (size_t)u->x_cap * 8;
    if (u->pcm_ok && !grow_to((void **)&u->x, &cap, (size_t)n * 8)) {
        u->x_cap = (int)(cap / 8);
        for (int i = 0; i < n; ++i) {
            const uint8_t *fr = u->pend + (size_t)i * fb;
            u->x[2 * i] = sample(fr, &u->f);
            u->x[2 * i + 1] = u->f.channels > 1 ? sample(fr + u->f.bytes, &u->f) : u->x[2 * i];
        }
        if (!u->swr) out_push(u, u->x, n);
        else {
            int m = swr_get_out_samples(u->swr, n);
            float *y = m > 0 ? malloc((size_t)m * 8) : NULL;
            const uint8_t *in = (const uint8_t *)u->x;
            if (y && (m = swr_convert(u->swr, (uint8_t **)&y, m, &in, n)) > 0) out_push(u, y, m);
            free(y);
        }
    }
    memmove(u->pend, u->pend + (size_t)n * fb, u->pend_n - (size_t)n * fb);
    u->pend_n -= (size_t)n * fb;
}

/* One record of the tap's stream (tap.c's comment has them). */
static void take(struct uac *u, const char tag[4], const uint8_t *p, uint32_t n, int to) {
    if (!memcmp(tag, "Q2TF", 4) && n >= 8) {
        size_t fb = u->f.channels * u->f.bytes;
        if (fb) let_go(u, (int)(u->pend_n / fb), to); /* the last format's held frames go before the new ones */
        uint32_t rate;
        uint16_t ch;
        memcpy(&rate, p, 4), memcpy(&ch, p + 4, 2);
        u->f = (struct pcm_format){.rate = (int)rate, .channels = ch, .bytes = p[6], .is_float = p[7] == 1,
                                   .dsd = p[7] == 2 ? DSD_DOP : p[7] == 3 ? DSD_NATIVE : DSD_PCM};
        u->pcm_ok = p[7] < 2 && ch > 0 && (p[6] == 2 || p[6] == 3 || p[6] == 4) && rate > 0;
        u->pend_n = 0;
        resampler(u, to);
        return;
    }
    size_t fb = (size_t)u->f.channels * u->f.bytes;
    if (!fb) return; /* nothing before the first format */
    uint32_t count = 0;
    if (n == 4) memcpy(&count, p, 4);
    if (!memcmp(tag, "Q2TD", 4)) {
        if (grow_to((void **)&u->pend, &u->pend_cap, u->pend_n + n)) return;
        memcpy(u->pend + u->pend_n, p, n), u->pend_n += n;
    } else if (!memcmp(tag, "Q2TB", 4)) {
        size_t cut = (size_t)count * fb;
        u->pend_n = cut < u->pend_n ? u->pend_n - cut : 0;
    } else if (!memcmp(tag, "Q2TL", 4)) {
        size_t add = (size_t)count * fb;
        if (grow_to((void **)&u->pend, &u->pend_cap, u->pend_n + add)) return;
        memset(u->pend + u->pend_n, 0, add), u->pend_n += add;
    }
    int hold = u->f.rate * HOLD_MS / 1000, held = (int)(u->pend_n / fb);
    if (held > hold) let_go(u, held - hold, to);
}

/* Every whole record in the ring, taken; 1 when there was one. */
static int drain(struct tap_ring *r, struct uac *u, int to) {
    for (int any = 0;; any = 1) {
        size_t h = __atomic_load_n(&r->head, __ATOMIC_ACQUIRE);
        if (h - r->tail < PCM_TAP_HEAD) return any;
        uint8_t hd[PCM_TAP_HEAD];
        for (int i = 0; i < PCM_TAP_HEAD; ++i) hd[i] = r->buf[(r->tail + i) % PCM_TAP_RING];
        uint32_t n;
        memcpy(&n, hd + 4, 4);
        if (grow_to((void **)&u->rec, &u->rec_cap, n ? n : 1)) return any;
        for (uint32_t i = 0; i < n; ++i) u->rec[i] = r->buf[(r->tail + PCM_TAP_HEAD + i) % PCM_TAP_RING];
        __atomic_store_n(&r->tail, r->tail + PCM_TAP_HEAD + n, __ATOMIC_RELEASE);
        take(u, (const char *)hd, u->rec, n, to);
    }
}

/* ---- the card */
static int64_t ms_now(void) {
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return t.tv_sec * 1000LL + t.tv_nsec / 1000000;
}

#ifdef HAVE_ALSA
/* The computer's rate as the gadget's card says it, 0 when it is not streaming or the card has no such control. */
static int card_rate(struct sink *s) {
    if (!s->ctl && (snd_ctl_open(&s->ctl, UAC_CARD, 0) < 0 || snd_ctl_elem_value_malloc(&s->val) < 0)) {
        if (s->ctl) snd_ctl_close(s->ctl);
        s->ctl = NULL;
        return 0;
    }
    snd_ctl_elem_value_set_interface(s->val, SND_CTL_ELEM_IFACE_PCM);
    snd_ctl_elem_value_set_name(s->val, "Playback Rate");
    return snd_ctl_elem_read(s->ctl, s->val) < 0 ? 0 : (int)snd_ctl_elem_value_get_integer(s->val, 0);
}
#endif

static void sink_close(struct sink *s) {
#ifdef HAVE_ALSA
    if (s->pcm) snd_pcm_close(s->pcm);
    if (s->ctl) snd_ctl_close(s->ctl);
    if (s->val) snd_ctl_elem_value_free(s->val);
    s->pcm = NULL, s->ctl = NULL, s->val = NULL;
#endif
    if (s->file) fclose(s->file);
    s->file = NULL;
}

static int sink_open(struct sink *s) {
    memset(s, 0, sizeof *s);
    s->speed = 1;
    const char *file = getenv("Q2PLAYER_UAC"), *rate = getenv("Q2PLAYER_UAC_RATE"), *speed = getenv("Q2PLAYER_SPEED");
    if (file) {
        if (!(s->file = fopen(file, "ab"))) return -1;
        s->rate = rate && atoi(rate) > 0 ? atoi(rate) : 48000, s->bytes = 4;
        s->speed = speed && atof(speed) > 0 ? atof(speed) : 1;
        clock_gettime(CLOCK_MONOTONIC, &s->next);
        return 0;
    }
#ifdef HAVE_ALSA
    snd_pcm_hw_params_t *p = NULL;
    unsigned r = 48000, buffer = 4 * PERIOD_MS * 1000, periods = 4;
    int e = snd_pcm_open(&s->pcm, UAC_PCM, SND_PCM_STREAM_PLAYBACK, 0);
    if (e < 0) { s->pcm = NULL; return -1; }
    int want = card_rate(s);
    if (want > 0) r = (unsigned)want;
    if ((e = snd_pcm_hw_params_malloc(&p)) >= 0 && (e = snd_pcm_hw_params_any(s->pcm, p)) >= 0 &&
        (e = snd_pcm_hw_params_set_access(s->pcm, p, SND_PCM_ACCESS_RW_INTERLEAVED)) >= 0) {
        s->bytes = snd_pcm_hw_params_test_format(s->pcm, p, SND_PCM_FORMAT_S32_LE) >= 0 ? 4 : 2;
        if ((e = snd_pcm_hw_params_set_format(s->pcm, p, s->bytes == 4 ? SND_PCM_FORMAT_S32_LE : SND_PCM_FORMAT_S16_LE)) >= 0 &&
            (e = snd_pcm_hw_params_set_channels(s->pcm, p, 2)) >= 0 &&
            (e = snd_pcm_hw_params_set_rate_resample(s->pcm, p, 0)) >= 0 &&
            (e = snd_pcm_hw_params_set_rate_near(s->pcm, p, &r, NULL)) >= 0 &&
            (e = snd_pcm_hw_params_set_buffer_time_near(s->pcm, p, &buffer, NULL)) >= 0 &&
            (e = snd_pcm_hw_params_set_periods_near(s->pcm, p, &periods, NULL)) >= 0 && (e = snd_pcm_hw_params(s->pcm, p)) >= 0)
            e = snd_pcm_prepare(s->pcm);
    }
    if (p) snd_pcm_hw_params_free(p);
    if (e < 0) {
        fprintf(stderr, "q2player: usb audio: %s %u Hz: %s\n", UAC_PCM, r, snd_strerror(e));
        sink_close(s);
        return -1;
    }
    s->rate = (int)r;
    if (s->rate > 192000) { sink_close(s); return -1; } /* sink_write's period buffer holds 192 kHz's */
    return 0;
#else
    return -1;
#endif
}

/* One period to the card, from its buffer or silence. 0 written, 1 the computer is not reading, -1 the card failed. */
static int sink_write(struct sink *s, struct uac *u) {
    int n = s->rate * PERIOD_MS / 1000;
    static int32_t buf[2 * 48 * 4 * PERIOD_MS]; /* a period at 192 kHz, stereo */
    if (u->out_n > s->rate * MAX_MS / 1000) {
        int drop = u->out_n - s->rate * LEAD_MS / 1000;
        out_take(u, drop), u->dropped += drop;
    }
    int have = u->out_n >= n;
    if (!have && u->out_n) u->under++;
    for (int i = 0; i < 2 * n; ++i) {
        double v = have ? u->out[i] * 2147483648.0 : 0;
        int32_t w = v >= 2147483647.0 ? INT32_MAX : v <= -2147483648.0 ? INT32_MIN : (int32_t)lrint(v);
        if (s->bytes == 2) ((int16_t *)buf)[i] = (int16_t)(w >> 16);
        else buf[i] = w;
    }
    if (have) out_take(u, n);
    u->frames += n;
    if (s->file) {
        fwrite(buf, (size_t)s->bytes * 2, (size_t)n, s->file);
        fflush(s->file);
        long ns = (long)(PERIOD_MS * 1000000L / s->speed);
        s->next.tv_nsec += ns;
        while (s->next.tv_nsec >= 1000000000L) s->next.tv_sec++, s->next.tv_nsec -= 1000000000L;
        clock_nanosleep(CLOCK_MONOTONIC, TIMER_ABSTIME, &s->next, NULL);
        return 0;
    }
#ifdef HAVE_ALSA
    int w = snd_pcm_wait(s->pcm, WAIT_MS);
    if (w == 0) return 1;
    snd_pcm_sframes_t r = snd_pcm_writei(s->pcm, buf, (snd_pcm_uframes_t)n);
    if (r == -EPIPE || r == -ESTRPIPE) r = snd_pcm_prepare(s->pcm);
    return r < 0 && r != -EAGAIN ? -1 : 0;
#else
    return -1;
#endif
}

static void session(struct tap_ring *r) {
    struct sink s;
    struct uac u;
    memset(&u, 0, sizeof u);
    if (sink_open(&s)) return;
    fprintf(stderr, "q2player: usb audio: the card open at %d Hz\n", s.rate);
    tap_ring_open(r);
    int64_t look = ms_now() + UAC_LOOK_MS, last = 0;
    for (;;) {
        if (drain(r, &u, s.rate)) last = ms_now();
        size_t fb = (size_t)u.f.channels * u.f.bytes;
        /* nothing more for HOLD_MS of audio (a song's end, a pause): what is held is what the device plays out, so
         * it goes too, at the card's pace (all at once it would overflow the card's buffer and be dropped) */
        if (fb && u.pend_n && ms_now() - last >= HOLD_MS / s.speed) {
            int want = (int)((int64_t)(s.rate * LEAD_MS / 1000 - u.out_n) * u.f.rate / s.rate) + 1, held = (int)(u.pend_n / fb);
            if (want > 0) let_go(&u, want < held ? want : held, s.rate);
        }
        int w = sink_write(&s, &u);
        if (w < 0) break;
        if (w == 1) u.out_n = 0; /* nobody records: what came is old by the time someone does */
        if (ms_now() >= look) {
            look = ms_now() + UAC_LOOK_MS;
            if (access(UAC_FLAG, F_OK)) break;
#ifdef HAVE_ALSA
            int now = s.pcm ? card_rate(&s) : 0;
            if (now > 0 && now != s.rate) break; /* the computer chose another rate: open again at it */
#endif
        }
    }
    tap_ring_close(r);
    fprintf(stderr, "q2player: usb audio: the card closed: %lld frames, %lld short periods, %lld frames dropped\n",
            (long long)u.frames, (long long)u.under, (long long)u.dropped);
    sink_close(&s);
    swr_free(&u.swr);
    free(u.pend), free(u.x), free(u.out), free(u.rec);
}

void *uac_thread(void *arg) {
    for (;;) {
        if (!access(UAC_FLAG, F_OK)) session(arg);
        usleep(UAC_LOOK_MS * 1000);
    }
    return NULL;
}
