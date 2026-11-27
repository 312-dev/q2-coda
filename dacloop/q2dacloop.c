/* q2dacloop: USB DAC mode's audio (docs/dacloop.md). With the Q2 a USB Audio 2.0 device for a computer (q2sysd's
 * `usb dac`, its gadget's capture side), what the computer plays is read from the gadget's capture PCM and written to
 * the Q2's own DAC, at the rate the computer chose, as it is (32-bit, two channels).
 *
 * The rate: the gadget card's "Capture Rate" control, looked at every 200 ms (--rate-file FILE reads it from a file
 * instead, for a kernel without the control and for test/dacloop.py; --rate N fixes it). 0 is a computer not
 * streaming: nothing is open. A change closes both PCMs and opens them again at the new rate. Before the first write
 * at a rate, a prebuffer of the computer's audio is gathered (100 ms up to 96 kHz, 200 to 192, 300 above), so the DAC
 * does not run dry while the computer's packets settle. With no packets for 200 ms the rate is looked at again. A capture overrun or a playback underrun is recovered and
 * counted; a PCM that cannot be opened is tried again each second. SIGTERM ends it, both PCMs closed.
 *
 * The fill level: once a second the frames between the computer and the DAC (the capture's waiting frames and the
 * playback's delay, averaged over the second) are held to the prebuffer's size by the gadget's "Capture Pitch 1000000"
 * control, which scales the rate the Q2's feedback asks the computer for (pitch.h, docs/dacloop.md#holding-the-fill-level).
 * Nominal at each session's start and end; a kernel without the control is left as it is (--pitch-file FILE writes
 * the value to a file instead, for test/dacloop.py).
 *
 * Learned from stock's USB DAC mode (its gadget script and uac2_loopback's behaviour as its usage text gives it); not
 * done here: DSD from "Capture Bits" (docs/dacloop.md#what-is-missing). */
#define _GNU_SOURCE
#include <errno.h>
#include <stdarg.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

#include "alsa_min.h" /* player/alsa_min.h: the Q2's libasound has no headers */
#include "pitch.h"

enum { STREAM_CAPTURE = 1 };
typedef struct _snd_ctl snd_ctl_t;
typedef struct _snd_ctl_elem_value snd_ctl_elem_value_t;
snd_pcm_sframes_t snd_pcm_readi(snd_pcm_t *, void *, snd_pcm_uframes_t);
int snd_pcm_recover(snd_pcm_t *, int err, int silent);
int snd_pcm_start(snd_pcm_t *);
int snd_pcm_hw_params_set_rate(snd_pcm_t *, snd_pcm_hw_params_t *, unsigned val, int dir);
int snd_ctl_open(snd_ctl_t **, const char *, int mode);
int snd_ctl_close(snd_ctl_t *);
int snd_ctl_elem_value_malloc(snd_ctl_elem_value_t **);
void snd_ctl_elem_value_free(snd_ctl_elem_value_t *);
void snd_ctl_elem_value_set_interface(snd_ctl_elem_value_t *, int iface);
void snd_ctl_elem_value_set_name(snd_ctl_elem_value_t *, const char *);
int snd_ctl_elem_read(snd_ctl_t *, snd_ctl_elem_value_t *);
long snd_ctl_elem_value_get_integer(const snd_ctl_elem_value_t *, unsigned idx);
void snd_ctl_elem_value_set_integer(snd_ctl_elem_value_t *, unsigned idx, long val);
int snd_ctl_elem_write(snd_ctl_t *, snd_ctl_elem_value_t *);
typedef struct _snd_ctl_elem_info snd_ctl_elem_info_t;
int snd_ctl_elem_info_malloc(snd_ctl_elem_info_t **);
void snd_ctl_elem_info_free(snd_ctl_elem_info_t *);
void snd_ctl_elem_info_set_interface(snd_ctl_elem_info_t *, int iface);
void snd_ctl_elem_info_set_name(snd_ctl_elem_info_t *, const char *);
int snd_ctl_elem_info(snd_ctl_t *, snd_ctl_elem_info_t *);
long snd_ctl_elem_info_get_min(const snd_ctl_elem_info_t *);
long snd_ctl_elem_info_get_max(const snd_ctl_elem_info_t *);
#define CTL_ELEM_IFACE_PCM 2

#define CHANNELS 2
#define FRAME (4 * CHANNELS) /* S32_LE */
#define CHUNK 1024           /* frames a read */

static volatile sig_atomic_t stopping;
static const char *cap_name = "hw:CARD=UAC2Gadget,0", *play_name = "plughw:0,0", *ctl_name = "hw:CARD=UAC2Gadget",
                  *rate_file, *pitch_file;
static unsigned fixed_rate;
static unsigned long xruns_cap, xruns_play;

static void on_term(int s) { (void)s, stopping = 1; }

static unsigned long long now_ms(void) {
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return (unsigned long long)t.tv_sec * 1000 + (unsigned long long)t.tv_nsec / 1000000;
}

static void say(const char *fmt, ...) __attribute__((format(printf, 1, 2)));
static void say(const char *fmt, ...) {
    va_list a;
    va_start(a, fmt);
    fputs("q2dacloop: ", stderr), vfprintf(stderr, fmt, a), fputc('\n', stderr);
    va_end(a);
}

/* the computer's rate now: 0 when it is not streaming (or it cannot be read) */
static unsigned host_rate(void) {
    if (fixed_rate) return fixed_rate;
    if (rate_file) {
        FILE *f = fopen(rate_file, "r");
        unsigned r = 0;
        if (f && fscanf(f, "%u", &r) != 1) r = 0;
        if (f) fclose(f);
        return r;
    }
    snd_ctl_t *c;
    snd_ctl_elem_value_t *v;
    long r = 0;
    if (snd_ctl_open(&c, ctl_name, 0) < 0) return 0;
    if (!snd_ctl_elem_value_malloc(&v)) {
        snd_ctl_elem_value_set_interface(v, CTL_ELEM_IFACE_PCM);
        snd_ctl_elem_value_set_name(v, "Capture Rate");
        if (snd_ctl_elem_read(c, v) >= 0) r = snd_ctl_elem_value_get_integer(v, 0);
        snd_ctl_elem_value_free(v);
    }
    snd_ctl_close(c);
    return r > 0 ? (unsigned)r : 0;
}

/* ---- the fill level (pitch.h) ---- */

#define PITCH_NAME "Capture Pitch 1000000" /* the gadget card's control: its feedback scaled, 1000000 nominal */

static snd_ctl_t *pitch_ctl;     /* open while a session has the control; 0 without it */
static long pitch_lo, pitch_hi;  /* its range */
static int pitch_now;

/* the control's range, or the file's stand-in; 0 when there is neither (a kernel without the control) */
static int pitch_open(void) {
    if (pitch_file) return pitch_lo = PITCH_NOMINAL - PITCH_SPAN_PPM, pitch_hi = PITCH_NOMINAL + PITCH_SPAN_PPM, 1;
    snd_ctl_elem_info_t *i;
    if (snd_ctl_open(&pitch_ctl, ctl_name, 0) < 0) return pitch_ctl = 0, 0;
    int ok = !snd_ctl_elem_info_malloc(&i);
    if (ok) {
        snd_ctl_elem_info_set_interface(i, CTL_ELEM_IFACE_PCM);
        snd_ctl_elem_info_set_name(i, PITCH_NAME);
        ok = snd_ctl_elem_info(pitch_ctl, i) >= 0;
        if (ok) pitch_lo = snd_ctl_elem_info_get_min(i), pitch_hi = snd_ctl_elem_info_get_max(i);
        snd_ctl_elem_info_free(i);
    }
    if (!ok) snd_ctl_close(pitch_ctl), pitch_ctl = 0;
    return ok;
}

static void pitch_set(int v) {
    pitch_now = v;
    if (pitch_file) {
        FILE *f = fopen(pitch_file, "w");
        if (f) fprintf(f, "%d\n", v), fclose(f);
        return;
    }
    snd_ctl_elem_value_t *e;
    if (!pitch_ctl || snd_ctl_elem_value_malloc(&e)) return;
    snd_ctl_elem_value_set_interface(e, CTL_ELEM_IFACE_PCM);
    snd_ctl_elem_value_set_name(e, PITCH_NAME);
    snd_ctl_elem_value_set_integer(e, 0, v);
    if (snd_ctl_elem_write(pitch_ctl, e) < 0) say("%s: cannot set %d", PITCH_NAME, v);
    snd_ctl_elem_value_free(e);
}

static void pitch_close(void) {
    if (pitch_ctl) snd_ctl_close(pitch_ctl), pitch_ctl = 0;
}

static snd_pcm_t *open_pcm(const char *name, int stream, unsigned rate) {
    snd_pcm_t *p;
    snd_pcm_hw_params_t *hw;
    int e;
    if ((e = snd_pcm_open(&p, name, stream, 0)) < 0) return say("%s: cannot open: %s", name, snd_strerror(e)), (snd_pcm_t *)0;
    snd_pcm_hw_params_malloc(&hw);
    unsigned buffer_us = 500000;
    int periods = 4;
    if ((e = snd_pcm_hw_params_any(p, hw)) < 0 || (e = snd_pcm_hw_params_set_access(p, hw, SND_PCM_ACCESS_RW_INTERLEAVED)) < 0 ||
        (e = snd_pcm_hw_params_set_format(p, hw, SND_PCM_FORMAT_S32_LE)) < 0 || (e = snd_pcm_hw_params_set_channels(p, hw, CHANNELS)) < 0 ||
        (e = snd_pcm_hw_params_set_rate(p, hw, rate, 0)) < 0 || (e = snd_pcm_hw_params_set_buffer_time_near(p, hw, &buffer_us, 0)) < 0 ||
        (e = snd_pcm_hw_params_set_periods_near(p, hw, (unsigned *)&periods, 0)) < 0 || (e = snd_pcm_hw_params(p, hw)) < 0) {
        say("%s: %u Hz S32_LE 2 ch refused: %s", name, rate, snd_strerror(e));
        snd_pcm_hw_params_free(hw), snd_pcm_close(p);
        return 0;
    }
    snd_pcm_hw_params_free(hw);
    return p;
}

static unsigned prebuffer_ms(unsigned rate) { return rate <= 96000 ? 100 : rate <= 192000 ? 200 : 300; }

/* One session at rate: until the rate changes, a PCM fails beyond recovery, or SIGTERM. */
static void session(unsigned rate) {
    snd_pcm_t *cap = open_pcm(cap_name, STREAM_CAPTURE, rate), *play = cap ? open_pcm(play_name, SND_PCM_STREAM_PLAYBACK, rate) : 0;
    if (!cap || !play) {
        if (cap) snd_pcm_close(cap);
        sleep(1);
        return;
    }
    say("session %u Hz: %s to %s", rate, cap_name, play_name);
    size_t pre = (size_t)rate * prebuffer_ms(rate) / 1000, have = 0, room = pre + CHUNK;
    char *ring = malloc(room * FRAME), buf[CHUNK * FRAME];
    int fed = 0, pitched = pitch_open();
    unsigned long long look = now_ms() + 200, pitch_at = 0;
    struct pitch pc = {0};
    double fill_sum = 0;
    unsigned long fill_n = 0;
    if (pitched) pitch_set(PITCH_NOMINAL);
    else say("no %s: the fill level is not held", PITCH_NAME);
    snd_pcm_start(cap);
    while (!stopping && ring) {
        if (snd_pcm_wait(cap, 200) == 0) goto look_rate; /* no packets from the computer: look at its rate */
        snd_pcm_sframes_t n = snd_pcm_readi(cap, buf, CHUNK);
        if (n < 0) {
            ++xruns_cap;
            if (snd_pcm_recover(cap, (int)n, 1) < 0) break;
            snd_pcm_start(cap);
            continue;
        }
        if (!fed) { /* gathering the prebuffer */
            size_t k = (size_t)n < room - have ? (size_t)n : room - have;
            memcpy(ring + have * FRAME, buf, k * FRAME), have += k;
            if (have < pre) goto look_rate;
            for (size_t off = 0; off < have;) {
                snd_pcm_sframes_t w = snd_pcm_writei(play, ring + off * FRAME, have - off);
                if (w < 0) {
                    ++xruns_play;
                    if (snd_pcm_recover(play, (int)w, 1) < 0) goto done;
                    continue;
                }
                off += (size_t)w;
            }
            fed = 1, pitch_at = now_ms() + 1000;
            say("prebuffered %zu frames (%u ms), feeding", pre, prebuffer_ms(rate));
            goto look_rate;
        }
        for (snd_pcm_sframes_t off = 0; off < n;) {
            snd_pcm_sframes_t w = snd_pcm_writei(play, buf + off * FRAME, (snd_pcm_uframes_t)(n - off));
            if (w < 0) {
                ++xruns_play;
                if (snd_pcm_recover(play, (int)w, 1) < 0) goto done;
                continue;
            }
            off += w;
        }
        if (pitched) { /* the frames between the computer and the DAC, after this write */
            snd_pcm_sframes_t d = 0, a = snd_pcm_avail(cap);
            if (!snd_pcm_delay(play, &d) && a >= 0) fill_sum += (double)(d + a), ++fill_n;
            unsigned long long t = now_ms();
            if (t >= pitch_at) {
                if (fill_n) {
                    double err = (fill_sum / (double)fill_n - (double)pre) / rate;
                    int v = pitch_step(&pc, err, (double)(t - pitch_at + 1000) / 1000, pitch_lo, pitch_hi);
                    if (v != pitch_now) pitch_set(v);
                }
                fill_sum = 0, fill_n = 0, pitch_at = t + 1000;
            }
        }
    look_rate:
        if (now_ms() >= look) {
            unsigned r = host_rate();
            look = now_ms() + 200;
            if (r != rate) {
                say("rate %u -> %u", rate, r);
                break;
            }
        }
    }
done:
    free(ring);
    snd_pcm_drop(cap), snd_pcm_close(cap), snd_pcm_drain(play), snd_pcm_close(play);
    if (pitched) say("pitch %d at the end, back to %d", pitch_now, PITCH_NOMINAL), pitch_set(PITCH_NOMINAL), pitch_close();
    say("session %u Hz closed (overruns %lu, underruns %lu)", rate, xruns_cap, xruns_play);
}

int main(int argc, char **argv) {
    for (int i = 1; i < argc; ++i) {
        const char *a = argv[i], *v = i + 1 < argc ? argv[i + 1] : 0;
        if (!v) goto usage;
        if (!strcmp(a, "--capture")) cap_name = v;
        else if (!strcmp(a, "--playback")) play_name = v;
        else if (!strcmp(a, "--ctl")) ctl_name = v;
        else if (!strcmp(a, "--rate-file")) rate_file = v;
        else if (!strcmp(a, "--rate")) fixed_rate = (unsigned)atoi(v);
        else if (!strcmp(a, "--pitch-file")) pitch_file = v;
        else goto usage;
        ++i;
    }
    struct sigaction sa = {.sa_handler = on_term}; /* no SA_RESTART: a read waiting on the gadget ends at SIGTERM */
    sigaction(SIGTERM, &sa, 0), sigaction(SIGINT, &sa, 0);
    while (!stopping) {
        unsigned r = host_rate();
        if (r) session(r);
        else usleep(200000); /* the computer is not streaming */
    }
    return 0;
usage:
    fprintf(stderr, "usage: q2dacloop [--capture PCM] [--playback PCM] [--ctl CTL] [--rate-file FILE | --rate HZ] [--pitch-file FILE]\n");
    return 2;
}
