/* player/dsp.c on this machine: the checks test/peq.py's PLAYER makes of hciplayer's filter (patch/peq_player.c),
 * made of the new player's chain, one dsp per song. Built and run by run.py (test_dsp_chain) with PEQ_ROOT a scratch
 * directory holding mnt/data and tmp. A block here is float stereo unless named; pts is the end of the block. */
#include <assert.h>
#include <stdio.h>
#include <sys/stat.h>
#include "q2player.h"
#include "peq.h"

static unsigned clock_reads;
static long clock_skew; /* seconds added: a pause without waiting for one */
int __real_clock_gettime(clockid_t clock, struct timespec *t);
int __wrap_clock_gettime(clockid_t clock, struct timespec *t) {
    ++clock_reads;
    int result = __real_clock_gettime(clock, t);
    t->tv_sec += clock_skew;
    return result;
}

static int length = 10; /* each song's media-info length */

/* smart.c's side, as a check sets it: the analysis answers smart_answer with smart_given at once; begun counts asks */
static int smart_answer = SMART_PLAIN, smart_begun, smart_cancels;
static struct xf_plan smart_given;
void smart_begin(const char *url, float rg) { ++smart_begun; }
int smart_poll(struct xf_plan *p) {
    if (smart_answer == SMART_PLAN) *p = smart_given;
    return smart_answer;
}
void smart_cancel(const char *why) { ++smart_cancels; }
void plog(char level, const char *msg, int n) { fprintf(stderr, "%.*s\n", n, msg); }

static peq_preset active(double preamp, double gain) {
    peq_preset p;
    peq_default(&p);
    p.bypass = 0, p.preamp = preamp, p.count = 1;
    p.automatic = 2; /* a set preamp: these check the gain as saved */
    p.bands[0] = (peq_band){1, 0, 1000, gain, 1};
    assert(peq_save(PEQ_ACTIVE, &p, 1) == 1);
    return p;
}

static volatile int *shared(const char *path) {
    int fd = open(path, O_RDWR | O_CREAT, 0644);
    assert(fd >= 0 && !ftruncate(fd, sizeof(int)));
    volatile int *f = mmap(0, sizeof *f, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
    assert(f != MAP_FAILED && !close(fd));
    return f;
}
static volatile int *xfade_view(int ms) {
    static volatile int *f;
    if (!f) f = shared(XFADE_FILE);
    *f = ms;
    return f;
}

static struct dsp *track(int rate, int nch) {
    struct pcm_format f = {rate, nch, 4, 1, DSD_PCM};
    struct dsp *d = dsp_open(&f, 0, 1, length);
    assert(d);
    return d;
}
/* frames of buf through d, the frames out copied back into buf; their count. */
static unsigned run(struct dsp *d, float *buf, unsigned frames, int nch, double pts) {
    const uint8_t *out;
    int k = dsp_run(d, (const uint8_t *)buf, (int)frames, pts, &out);
    assert(k >= 0 && (unsigned)k <= frames);
    if (out != (const uint8_t *)buf) memcpy(buf, out, (size_t)k * nch * 4);
    return (unsigned)k;
}
/* One stereo block: channel 0 a ramp from *n (the song's frame count), channel 1 level. */
static unsigned block(struct dsp *d, float *out, unsigned frames, unsigned *n, float level, int rate) {
    for (unsigned i = 0; i < frames; ++i) out[2 * i] = (float)(*n + i), out[2 * i + 1] = level;
    *n += frames;
    return run(d, out, frames, 2, (double)*n / rate);
}

/* One block through the chain equals the same block through the reference DSP, and is filtered. */
static void same(struct dsp *d, peq_dsp *ref, int nch) {
    float a[1024], b[1024], in[1024];
    unsigned frames = 1024 / nch;
    for (int i = 0; i < 1024; ++i) a[i] = b[i] = in[i] = 0.5f * sinf(i * 0.05f);
    assert(run(d, a, frames, nch, 1) == frames);
    peq_process(ref, b, frames);
    assert(!memcmp(a, b, sizeof a) && memcmp(a, in, sizeof a));
}

static void crossfade_check(void) {
    static float buf[2 * 1024];
    struct dsp *a, *b;
    unlink(PEQ_ACTIVE); /* the defaults: bypass, so the chain leaves samples exact */
    xfade_view(1000);
    length = 10;
    /* Outgoing: untouched until the hold window (2 x 1 s + 2 s before the end), then half of each block until a
     * second is held; what comes out is the song in order, nothing skipped. */
    a = track(48000, 2);
    unsigned n = 0, out = 0, held;
    while (n < 10 * 48000) {
        unsigned k = block(a, buf, 1024, &n, 1, 48000);
        if (n <= 6 * 48000) assert(k == 1024);
        for (unsigned i = 0; i < k; ++i) assert(buf[2 * i] == (float)out++);
    }
    held = n - out;
    assert(held == 48000);
    dsp_close(a, 1);
    /* Incoming: its first second mixed with the held tail, equal power (ch 1: 1 out, 0 in, so the tail's cos), the
     * tail's frames in order; then the song alone. Every frame comes out. */
    b = track(48000, 2);
    xfade_view(0); /* turning the next fade off must still finish the tail already held */
    unsigned m = 0, mixed = 0;
    while (m < 2 * 48000) {
        unsigned start = m, k = block(b, buf, 1024, &m, 0, 48000);
        assert(k == 1024);
        for (unsigned i = 0; i < k; ++i, ++mixed) {
            if (mixed < held) {
                float p = (mixed + 0.5f) / held * 1.5707963f;
                assert(fabsf(buf[2 * i + 1] - cosf(p)) < 1e-5f);
                assert(fabsf(buf[2 * i] - ((start + i) * sinf(p) + (out + mixed) * cosf(p))) < 0.5f);
            } else
                assert(buf[2 * i] == (float)(start + i) && buf[2 * i + 1] == 0);
        }
    }
    float p = (held / 2 + 0.5f) / held * 1.5707963f; /* halfway the two are at equal power, each at 0.707 */
    assert(fabsf(cosf(p) - 0.70710678f) < 1e-3f);
    dsp_close(b, 1);

    /* No fade asked (another album next, Crossfade off, Gapless off): every block whole, and no clock read. */
    xfade_view(0);
    a = track(48000, 2);
    unsigned reads = clock_reads;
    for (n = 0; n < 10 * 48000;) assert(block(a, buf, 1024, &n, 1, 48000) == 1024);
    assert(clock_reads == reads);
    dsp_close(a, 1);

    /* A seek while holding drops the held audio (it was left behind) and plays on whole. */
    xfade_view(1000);
    a = track(48000, 2);
    for (n = 0; n < 9 * 48000;) block(a, buf, 1024, &n, 1, 48000);
    n = 2 * 48000; /* back to 2 s */
    assert(block(a, buf, 1024, &n, 1, 48000) == 1024 && buf[0] == 2 * 48000);
    dsp_close(a, 0);
    /* A tail followed by another rate plays at that rate, linearly interpolated: 48000 held frames are 44100 frames
     * out, the ramp 48000/44100 a frame, under the same equal-power gains. */
    a = track(48000, 2);
    for (n = out = 0; n < 10 * 48000;) {
        unsigned k = block(a, buf, 1024, &n, 1, 48000);
        for (unsigned i = 0; i < k; ++i) assert(buf[2 * i] == (float)out++);
    }
    assert(n - out == 48000);
    dsp_close(a, 1);
    b = track(44100, 2);
    /* the mix's length in output frames is the held frames over the step, truncated: 44099 here, as on a Q2 (in
     * x87's extended precision, where test/peq.py's -m32 build runs, it comes out 44100) */
    unsigned total = (unsigned)(48000 / (48000.0 / 44100));
    for (m = mixed = 0; m < 2 * 44100;) {
        unsigned start = m, k = block(b, buf, 1024, &m, 0, 44100);
        assert(k == 1024);
        for (unsigned i = 0; i < k; ++i, ++mixed) {
            if (mixed < total) {
                float q = (mixed + 0.5f) / total * 1.5707963f;
                assert(fabsf(buf[2 * i + 1] - cosf(q)) < 1e-5f);
                assert(fabsf(buf[2 * i] - ((start + i) * sinf(q) + (out + mixed * (48000.0f / 44100)) * cosf(q))) < 0.5f);
            } else
                assert(buf[2 * i] == (float)(start + i) && buf[2 * i + 1] == 0);
        }
    }
    dsp_close(b, 1);
    /* A mono song next: each held frame's mean (ramp and level 1) under it, all of it. */
    a = track(48000, 2);
    for (n = out = 0; n < 10 * 48000;) out += block(a, buf, 1024, &n, 1, 48000);
    dsp_close(a, 1);
    b = track(48000, 1);
    for (m = 0; m < 48000; m += 1024) {
        for (unsigned i = 0; i < 1024; ++i) buf[i] = 0;
        assert(run(b, buf, 1024, 1, (m + 1024.0) / 48000) == 1024);
        for (unsigned i = 0; i < 1024 && m + i < 48000; ++i)
            assert(fabsf(buf[i] - (out + m + i + 1.0f) / 2 * cosf((m + i + 0.5f) / 48000 * 1.5707963f)) < 1);
    }
    dsp_close(b, 1);
    /* The flag clearing in the hold finishes it: in order, no jump, what is held mixed next. */
    xfade_view(1000);
    a = track(48000, 2);
    for (n = out = 0; n < 10 * 48000;) {
        if (n >= 7 * 48000) xfade_view(0);
        unsigned k = block(a, buf, 1024, &n, 1, 48000);
        for (unsigned i = 0; i < k; ++i) assert(buf[2 * i] == (float)out++);
    }
    dsp_close(a, 1);
    held = n - out;
    assert(held > 0 && held < 48000);
    b = track(48000, 2);
    for (m = mixed = 0; mixed < held;) {
        unsigned k = block(b, buf, 1024, &m, 0, 48000);
        for (unsigned i = 0; i < k && mixed < held; ++i, ++mixed) {
            float q = (mixed + 0.5f) / held * 1.5707963f;
            assert(fabsf(buf[2 * i + 1] - cosf(q)) < 1e-5f);
            assert(fabsf(buf[2 * i] - (mixed * sinf(q) + (out + mixed) * cosf(q))) < 1);
        }
    }
    dsp_close(b, 1);
    /* A stop since the hold began drops the tail, however soon the next song comes. */
    xfade_view(1000);
    a = track(48000, 2);
    for (n = 0; n < 10 * 48000;) block(a, buf, 1024, &n, 1, 48000);
    dsp_close(a, 1);
    xfade_view(1 << XF_STOPS);
    b = track(48000, 2);
    m = 0;
    assert(block(b, buf, 1024, &m, 0, 48000) == 1024 && buf[0] == 0 && buf[1] == 0);
    dsp_close(b, 1);
    /* Unknown length: no hold. */
    xfade_view(1000);
    length = 0;
    a = track(48000, 2);
    for (n = 0; n < 10 * 48000;) assert(block(a, buf, 1024, &n, 1, 48000) == 1024);
    dsp_close(a, 1);
    /* A 15 s fade, the longest: the hold wraps the ring; odd block sizes; order and the equal-power gain hold. */
    xfade_view(15000);
    length = 60;
    static const unsigned sizes[] = {1024, 333, 17, 1000};
    unsigned j = 0;
    a = track(48000, 2);
    for (n = out = 0; n < 60 * 48000;) {
        unsigned k = block(a, buf, sizes[j++ % 4], &n, 1, 48000);
        for (unsigned i = 0; i < k; ++i) assert(buf[2 * i] == (float)out++);
    }
    held = n - out;
    dsp_close(a, 1);
    b = track(48000, 2);
    for (m = mixed = 0; mixed < held;) {
        unsigned k = block(b, buf, sizes[j++ % 4], &m, 0, 48000);
        for (unsigned i = 0; i < k && mixed < held; ++i, ++mixed)
            assert(fabsf(buf[2 * i + 1] - cosf((mixed + 0.5f) / held * 1.5707963f)) < 1e-5f);
    }
    dsp_close(b, 1);
    /* A song cut short (a skip, a stop) leaves a tail for 3 s, one that ended by itself waits 30 s. */
    length = 10;
    xfade_view(1000);
    for (int ended = 0; ended < 2; ++ended) {
        a = track(48000, 2);
        for (n = 0; n < 10 * 48000;) block(a, buf, 1024, &n, 1, 48000);
        dsp_close(a, ended);
        clock_skew += 4;
        b = track(48000, 2);
        m = 0;
        assert(block(b, buf, 1024, &m, 0, 48000) == 1024 && (buf[1] != 0) == ended);
        dsp_close(b, 1);
        clock_skew = 0;
    }
    xfade_view(0);
}

/* Mid-mix: demo's stop drops what is left, as does another song more than XF_STALE_NS after the last mixed block,
 * while the mixing song's own pause only waits. A song cut short hands the rest to the next at its own rate. */
static unsigned hold_tail(void) { /* a 10 s song at 48 kHz holds its last second; its frames out */
    static float buf[2 * 1024];
    unsigned n = 0, out = 0;
    struct dsp *a = track(48000, 2);
    while (n < 10 * 48000) out += block(a, buf, 1024, &n, 1, 48000);
    dsp_close(a, 1);
    assert(n - out == 48000);
    return out;
}
static void alone(const float *buf, unsigned k, unsigned m) {
    for (unsigned i = 0; i < k; ++i) assert(buf[2 * i] == (float)(m - k + i) && buf[2 * i + 1] == 0);
}
static void midmix_check(void) {
    static float buf[2 * 1024];
    struct dsp *b, *c;
    unsigned m, k, out, mixed;
    unlink(PEQ_ACTIVE);
    xfade_view(1000);
    length = 10;
    hold_tail();
    b = track(48000, 2);
    for (m = 0; m < 10 * 1024;) assert(block(b, buf, 1024, &m, 0, 48000) == 1024 && buf[2 * 1023 + 1] > 0.9f);
    xfade_view(1000 | 1 << XF_STOPS); /* a stop since the tail was held */
    k = block(b, buf, 1024, &m, 0, 48000);
    assert(k == 1024);
    alone(buf, k, m);
    dsp_close(b, 1);

    xfade_view(1000);
    hold_tail();
    b = track(48000, 2);
    for (m = 0; m < 10 * 1024;) block(b, buf, 1024, &m, 0, 48000);
    clock_skew += 4; /* the mixing song paused */
    k = block(b, buf, 1024, &m, 0, 48000);
    assert(k == 1024 && fabsf(buf[1] - cosf((10240 + 0.5f) / 48000 * 1.5707963f)) < 1e-5f);
    dsp_close(b, 0);
    clock_skew += 4; /* the next song as long after */
    c = track(48000, 2);
    m = 0;
    k = block(c, buf, 1024, &m, 0, 48000);
    assert(k == 1024);
    alone(buf, k, m);
    dsp_close(c, 1);
    clock_skew = 0;

    out = hold_tail();
    b = track(48000, 2);
    for (m = 0; m < 10 * 1024;) block(b, buf, 1024, &m, 0, 48000); /* 10240 of the tail's 48000 */
    dsp_close(b, 0);
    c = track(96000, 2);
    for (m = mixed = 0; m < 2 * 96000;) {
        unsigned start = m;
        k = block(c, buf, 1024, &m, 0, 96000);
        assert(k == 1024);
        for (unsigned i = 0; i < k; ++i, ++mixed)
            if (mixed < 2 * (48000 - 10240)) {
                float p = (2 * 10240 + mixed + 0.5f) / 96000 * 1.5707963f;
                assert(fabsf(buf[2 * i + 1] - cosf(p)) < 1e-5f);
                assert(fabsf(buf[2 * i] - ((start + i) * sinf(p) + (out + 10240 + mixed * 0.5f) * cosf(p))) < 0.5f);
            } else
                assert(buf[2 * i] == (float)(start + i) && buf[2 * i + 1] == 0);
    }
    dsp_close(c, 1);
    xfade_view(0);
}

/* ---- Smart Crossfade's hold and mix (dsp.c smart_want and the mix), with smart.c's plan given (smart_given) */
typedef float (*wave)(unsigned i, int c);
static float *song_a, *song_b; /* each song's frames out */
static unsigned out_a, out_b;
/* A (ra Hz, na channels, fa) to its end at length, then bframes of B (rb, nb, fb) through the mix; word: demo's,
 * stopped: its stop count moved after A's end. */
static void smart_pair(int ra, int na, wave fa, int rb, int nb, wave fb, unsigned bframes, int word, int stopped) {
    static float buf[8 * 1024];
    free(song_a), free(song_b);
    song_a = calloc((size_t)length * ra * na, sizeof(float)), song_b = calloc((size_t)bframes * nb, sizeof(float));
    out_a = out_b = 0;
    xfade_view(word);
    struct pcm_format f = {ra, na, 4, 1, DSD_PCM};
    struct dsp *a = dsp_open(&f, 0, 1, length);
    dsp_path(a, "a");
    for (unsigned n = 0, e = (unsigned)(length * ra); n < e;) {
        unsigned m = e - n < 1024 ? e - n : 1024;
        for (unsigned i = 0; i < m; ++i)
            for (int c = 0; c < na; ++c) buf[i * na + c] = fa(n + i, c);
        n += m;
        unsigned k = run(a, buf, m, na, (double)n / ra);
        memcpy(song_a + (size_t)out_a * na, buf, (size_t)k * na * 4), out_a += k;
    }
    dsp_close(a, 1);
    if (stopped) xfade_view(word + (1 << XF_STOPS));
    struct pcm_format g = {rb, nb, 4, 1, DSD_PCM};
    struct dsp *b = dsp_open(&g, 0, 1, length);
    dsp_path(b, "b");
    for (unsigned n = 0; n < bframes;) {
        unsigned m = bframes - n < 1024 ? bframes - n : 1024;
        for (unsigned i = 0; i < m; ++i)
            for (int c = 0; c < nb; ++c) buf[i * nb + c] = fb(n + i, c);
        n += m;
        unsigned k = run(b, buf, m, nb, (double)n / rb);
        memcpy(song_b + (size_t)out_b * nb, buf, (size_t)k * nb * 4), out_b += k;
    }
    dsp_close(b, 1);
    xfade_view(0);
}
static struct xf_plan plan_of(int64_t overlap, int64_t trail, int64_t skip, int64_t fade, int rate) {
    struct xf_plan p = {overlap, trail, skip, fade, rate, "b"};
    return p;
}
static float tone(unsigned i, int c) { return 0.99f * sinf(6.2831853f * 1000 * (i % 48) / 48000); }
static float ramp(unsigned i, int c) { return i * 1e-6f; }
static float zero(unsigned i, int c) { return 0; }
static float ramp_level(unsigned i, int c) { return c ? 0.5f : i * 1e-6f; }

static void smart_check(void) {
    unlink(PEQ_ACTIVE);
    length = 10;
    smart_answer = SMART_PLAN;
    int smart = 5000 | XF_SMART;
    /* The same full-scale tone in phase on both sides: an equal-power mix sums to 1.41 times it, a smart one stays
     * under XF_CEIL throughout and after, then the next song is its own again. */
    smart_given = plan_of(24000, 0, 0, 0, 48000);
    smart_pair(48000, 2, tone, 48000, 2, tone, 3 * 48000, smart, 0);
    float peak = 0;
    for (unsigned i = 0; i < out_b * 2; ++i) peak = fabsf(song_b[i]) > peak ? fabsf(song_b[i]) : peak;
    assert(peak <= 0.99f + 1e-6f && peak > 0.9f && out_b == 3 * 48000);
    /* recovered within 12 of XF_RELEASE_S after the overlap: the song bit for bit */
    for (unsigned i = 24000 + 12 * 2400; i < out_b; ++i) assert(song_b[2 * i] == tone(i, 0));
    smart_pair(48000, 2, tone, 48000, 2, tone, 2 * 48000, 500, 0); /* the plain fade of the same */
    peak = 0;
    for (unsigned i = 0; i < out_b * 2; ++i) peak = fabsf(song_b[i]) > peak ? fabsf(song_b[i]) : peak;
    assert(peak > 1.3f);

    /* 44.1 kHz stereo into 48 kHz mono: A's frames all come out, alone up to out_point, then under the next song
     * through the overlap, read at 44.1/48 a frame; its trail never. */
    smart_given = plan_of(22050, 4410, 0, 0, 48000);
    smart_pair(44100, 2, ramp, 48000, 1, zero, 48000, smart, 0);
    unsigned cut = 441000 - 4410 - 22050;
    assert(out_a == cut);
    for (unsigned i = 0; i < out_a; ++i) assert(song_a[2 * i] == ramp(i, 0));
    double step = 44100.0 / 48000;
    for (unsigned t = 0; t < 24000; ++t) {
        float go = cosf((t + 0.5f) / 24000 * 1.5707963f);
        assert(fabsf(song_b[t] - go * (float)((cut + t * step) * 1e-6)) < 2e-6f);
    }
    assert((unsigned)(cut + 23999 * step) == 441000 - 4410 - 1); /* the overlap's last frame read */
    for (unsigned t = 24000; t < out_b; ++t) assert(song_b[t] == 0);

    /* The next song: its leading frames dropped, then in over its fade-in, or at full level from its first frame (a
     * hard start). */
    smart_given = plan_of(24000, 0, 4800, 9600, 48000);
    smart_pair(48000, 2, zero, 48000, 2, ramp_level, 48000, smart, 0);
    assert(out_b == 48000 - 4800);
    for (unsigned t = 0; t < out_b; ++t) {
        float gi = t < 9600 ? sinf((t + 0.5f) / 9600 * 1.5707963f) : 1;
        assert(fabsf(song_b[2 * t] - ramp_level(4800 + t, 0) * gi) < 1e-6f && fabsf(song_b[2 * t + 1] - 0.5f * gi) < 1e-6f);
    }
    smart_given = plan_of(24000, 0, 4800, 0, 48000);
    smart_pair(48000, 2, zero, 48000, 2, ramp_level, 48000, smart, 0);
    for (unsigned t = 0; t < out_b; ++t) assert(song_b[2 * t] == ramp_level(4800 + t, 0) && song_b[2 * t + 1] == 0.5f);

    /* A stop after the hold: the next song plays whole, nothing mixed, nothing dropped. */
    smart_given = plan_of(24000, 0, 4800, 9600, 48000);
    smart_pair(48000, 2, tone, 48000, 2, ramp_level, 48000, smart, 1);
    assert(out_b == 48000);
    for (unsigned t = 0; t < out_b; ++t) assert(song_b[2 * t] == ramp_level(t, 0) && song_b[2 * t + 1] == 0.5f);

    /* No plan (smart.c's SMART_PLAIN): the plain 5 s fade. */
    smart_answer = SMART_PLAIN;
    smart_pair(48000, 2, ramp, 48000, 2, zero, 6 * 48000, smart, 0);
    assert(out_a == 10 * 48000 - 5 * 48000);
    smart_answer = SMART_PLAIN;
    free(song_a), free(song_b), song_a = song_b = 0;
}

/* Mono Audio and Swap L/R: demo's files, looked for once a second of audio; set, each stereo frame is its channels'
 * mean, or the two swapped; cleared, the block plays exactly. */
static void mono_check(void) {
    static float buf[2 * 1024];
    volatile int *f = shared(MONO_FILE), *swap = shared(SWAP_FILE);
    *swap = 1, *f = 1;
    unlink(PEQ_ACTIVE);
    struct dsp *a = track(48000, 2);
    unsigned n = 0, k = 0;
    for (int i = 0; i < 60; ++i) k = block(a, buf, 1024, &n, -1, 48000); /* found within a second */
    for (unsigned i = 0; i < k; ++i) assert(buf[2 * i] == buf[2 * i + 1] && buf[2 * i] == (n - k + i - 1.0f) * 0.5f);
    *f = 0;
    k = block(a, buf, 1024, &n, -1, 48000);
    for (unsigned i = 0; i < k; ++i) assert(buf[2 * i] == -1 && buf[2 * i + 1] == (float)(n - k + i));
    *swap = 0;
    k = block(a, buf, 1024, &n, -1, 48000);
    for (unsigned i = 0; i < k; ++i) assert(buf[2 * i] == (float)(n - k + i) && buf[2 * i + 1] == -1);
    dsp_close(a, 1);
    unlink(MONO_FILE), unlink(SWAP_FILE);
}

/* AirPlay's tap: CAST_FILE found within a second of audio; while the app beats, what plays goes to the ring at
 * CAST_RATE, 16-bit, and mute silences the Q2's own output; a stale beat gives the sound back and writes nothing;
 * another rate is resampled at unity gain. */
static void cast_check(void) {
    static float buf[2 * 1024];
    int fd = open(CAST_FILE, O_RDWR | O_CREAT, 0644);
    assert(fd >= 0 && !ftruncate(fd, sizeof(cast_tap)));
    cast_tap *t = mmap(0, sizeof *t, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
    assert(t != MAP_FAILED && !close(fd));
    t->magic = CAST_MAGIC, t->size = sizeof *t, t->mute = 1, t->want = CAST_CLOCK(now_ns());
    unlink(PEQ_ACTIVE);
    struct dsp *a = track(CAST_RATE, 2);
    unsigned n = 0, k = 0;
    for (int i = 0; i < 60; ++i) k = block(a, buf, 1024, &n, 0.25f, CAST_RATE);
    assert(t->seq && t->seq % 1024 == 0 && t->seq < n);
    for (unsigned i = 1; i <= 1024; ++i) assert(t->ring[(t->seq - i) & (CAST_RING - 1)][1] == 8192);
    for (unsigned i = 0; i < 2 * k; ++i) assert(buf[i] == 0); /* muted */
    unsigned seq = t->seq;
    t->want = CAST_CLOCK(now_ns()) - CAST_STALE_PLAYER; /* the app stopped beating */
    t->volume = 40;                                      /* demo still at the speaker's level: silent */
    k = block(a, buf, 1024, &n, 0.25f, CAST_RATE);
    assert(t->seq == seq && buf[1] == 0 && buf[2 * k - 1] == 0);
    t->volume = -1; /* the earphones' level back */
    k = block(a, buf, 1024, &n, 0.25f, CAST_RATE);
    assert(t->seq == seq && buf[1] == 0.25f && buf[2 * k - 1] == 0.25f);
    dsp_close(a, 1);
    t->want = CAST_CLOCK(now_ns());
    a = track(48000, 2);
    n = 0;
    for (int i = 0; i < 60 && t->seq == seq; ++i) block(a, buf, 1024, &n, 0.25f, 48000);
    assert(t->seq != seq);
    unsigned from = n;
    seq = t->seq;
    for (int i = 0; i < 47; ++i) block(a, buf, 1024, &n, 0.25f, 48000);
    unsigned got = t->seq - seq, want = (n - from) * 441u / 480;
    assert(got + 2 >= want && got <= want + 2);
    short last = t->ring[(t->seq - 1) & (CAST_RING - 1)][1];
    assert(last >= 8192 - 2 && last <= 8192 + 2);
    dsp_close(a, 1);
    t->want = CAST_CLOCK(now_ns()) - CAST_STALE_PLAYER; /* the chain keeps its map: the app gone */
    munmap(t, sizeof *t);
    unlink(CAST_FILE);
}

/* A gapless change: the next song carries on the last one's filter memory, so a continuous signal comes out as one
 * uninterrupted DSP puts it; another preset, a skip mid-song, a pause and a passed-through song start from zero. */
static void sine(float *x, unsigned *n) {
    for (unsigned i = 0; i < 1024; ++i, ++*n) x[i] = 0.5f * sinf(*n / 2 * 0.05f);
}
static double pts = 0.5;
static void continues(struct dsp *d, peq_dsp *ref, unsigned *n) {
    float x[1024], y[1024];
    sine(x, n);
    memcpy(y, x, sizeof x);
    assert(run(d, x, 512, 2, pts) == 512);
    peq_process(ref, y, 512);
    assert(!memcmp(x, y, sizeof x));
}
static void carry_check(void) {
    xfade_view(0);
    length = 1, pts = 0.5; /* each song filtered to its end */
    peq_preset p = active(-3, 6);
    peq_dsp ref;
    peq_reset(&ref, 44100, 2, &p);
    unsigned n = 0;
    struct dsp *a = track(44100, 2);
    continues(a, &ref, &n);
    dsp_close(a, 1);
    struct dsp *b = track(44100, 2);
    continues(b, &ref, &n);
    dsp_close(b, 1);
    p = active(-3, 3); /* another preset: from zero */
    peq_reset(&ref, 44100, 2, &p);
    a = track(44100, 2);
    continues(a, &ref, &n);
    dsp_close(a, 1);
    /* A pause or stop before the next song: the old memory would ring into it, so it starts from zero. */
    clock_skew += 4;
    a = track(44100, 2);
    peq_reset(&ref, 44100, 2, &p);
    continues(a, &ref, &n);
    dsp_close(a, 1);
    clock_skew = 0;
    /* A passed-through song in between (here a rate the EQ leaves alone): the next starts from zero. */
    b = track(7999, 2);
    float pass[2] = {0.5f, 0.5f};
    assert(run(b, pass, 1, 2, 0.5) == 1 && pass[0] == 0.5f);
    dsp_close(b, 1);
    a = track(44100, 2);
    peq_reset(&ref, 44100, 2, &p);
    continues(a, &ref, &n);
    dsp_close(a, 1);
    /* A skip mid-song (or a stop): the next starts from silence, so from zero. */
    length = 60, pts = 10;
    b = track(44100, 2); /* still after a song that ended */
    continues(b, &ref, &n);
    dsp_close(b, 0);
    length = 1, pts = 0.5;
    a = track(44100, 2);
    peq_reset(&ref, 44100, 2, &p);
    continues(a, &ref, &n);
    dsp_close(a, 1);
    /* Mid-song with EQ on: no clock reads. */
    length = 60, pts = 10;
    a = track(44100, 2);
    for (int i = 0; i < 100; ++i) continues(a, &ref, &n);
    unsigned reads = clock_reads;
    for (int i = 0; i < 20; ++i) continues(a, &ref, &n);
    assert(clock_reads == reads);
    dsp_close(a, 1);
    length = 10, pts = 0.5;
    assert(!unlink(PEQ_ACTIVE));
}

/* With every stage off, integers come back as the very bytes given (no float in the path); with one on (here a
 * bypassed EQ under a fade's flag) 16-bit and 24-bit samples survive the float round
 * trip unchanged, and values past full scale saturate at the one conversion back. */
static void bypass_check(void) {
    int16_t s[2048], keep[2048];
    int32_t w[2048], wkeep[2048];
    for (int i = 0; i < 2048; ++i) s[i] = keep[i] = (int16_t)(i * 37 - 32768), w[i] = wkeep[i] = (int32_t)((i * 4099 - 4000000) * 256);
    unlink(PEQ_ACTIVE), xfade_view(0);
    const struct pcm_format f16 = {44100, 2, 2, 0, DSD_PCM}, f32 = {44100, 2, 4, 0, DSD_PCM};
    struct dsp *d = dsp_open(&f16, 0, 1, 60); /* a 60 s song: its fade's window is far off */
    const uint8_t *out;
    assert(dsp_run(d, (uint8_t *)s, 1024, 1, &out) == 1024 && out == (uint8_t *)s && !memcmp(s, keep, sizeof s));
    xfade_view(5000); /* a fade asked, nowhere near the end: float, and the same samples */
    assert(dsp_run(d, (uint8_t *)s, 1024, 1, &out) == 1024 && out != (uint8_t *)s && !memcmp(out, keep, sizeof s));
    dsp_close(d, 1);
    d = dsp_open(&f32, 0, 1, 60);
    assert(dsp_run(d, (uint8_t *)w, 1024, 1, &out) == 1024 && out != (uint8_t *)w && !memcmp(out, wkeep, sizeof w));
    dsp_close(d, 1);
    /* ReplayGain's scale, then a hard clip at full scale (MPlayer's volume filter); 2.0 doubles and saturates. */
    xfade_view(0);
    d = dsp_open(&f16, 0, 2, 10);
    assert(dsp_run(d, (uint8_t *)s, 1024, 1, &out) == 1024);
    for (int i = 0; i < 2048; ++i) {
        int v = 2 * keep[i];
        assert(((const int16_t *)out)[i] == (v > 32767 ? 32767 : v < -32768 ? -32768 : v));
    }
    dsp_close(d, 1);
    /* the clip is ReplayGain's own, before the EQ: a float stream, which no conversion saturates, stops at 1 */
    const struct pcm_format ff = {44100, 2, 4, 1, DSD_PCM};
    float fl[4] = {0.75f, -0.75f, 0.25f, -0.25f};
    d = dsp_open(&ff, 0, 2, 60);
    assert(dsp_run(d, (uint8_t *)fl, 2, 1, &out) == 2);
    assert(((const float *)out)[0] == 1 && ((const float *)out)[1] == -1 && ((const float *)out)[2] == 0.5f && ((const float *)out)[3] == -0.5f);
    dsp_close(d, 1);
    /* DSD (untouched) passes as it is, whatever is on, and drops a held tail. */
    xfade_view(1000);
    d = dsp_open(&f16, 1, 0.5f, 10);
    assert(dsp_run(d, (uint8_t *)s, 1024, 9.9, &out) == 1024 && out == (uint8_t *)s);
    dsp_close(d, 1);
    xfade_view(0);
}

/* The EQ at every rate from 8 kHz to 384 kHz, mono to eight channels: the chain's block is the reference DSP's;
 * past 384 kHz, or beyond eight channels, the song passes untouched. mcl-seteq's reread plays a new state through
 * the 20 ms ramp, and an unreadable state keeps the filter. */
static void rates_check(void) {
    static const int rates[] = {8000, 11025, 22050, 32000, 44100, 48000, 88200, 96000, 176400, 192000, 352800, 384000};
    peq_preset p = active(-6, 6);
    for (unsigned r = 0; r < sizeof rates / sizeof *rates; ++r)
        for (int nch = 1; nch <= 8; nch *= 2) {
            peq_dsp ref;
            peq_reset(&ref, rates[r], nch, &p);
            struct dsp *d = track(rates[r], nch);
            same(d, &ref, nch);
            dsp_close(d, 0);
        }
    float x[1024] = {0.5f}, keep[1024];
    memcpy(keep, x, sizeof x);
    struct dsp *d = track(384001, 2);
    assert(run(d, x, 512, 2, 1) == 512 && !memcmp(x, keep, sizeof x));
    dsp_close(d, 0);
    d = track(48000, 9);
    assert(run(d, x, 100, 9, 1) == 100 && !memcmp(x, keep, sizeof x));
    dsp_close(d, 0);
    peq_dsp ref;
    peq_reset(&ref, 48000, 2, &p);
    d = track(48000, 2);
    same(d, &ref, 2);
    peq_preset q = active(-3, -4);
    dsp_eq_changed(d);
    assert(peq_update(&ref, &q));
    for (int i = 0; i < 4; ++i) same(d, &ref, 2); /* through the ramp */
    FILE *junk = fopen(PEQ_ACTIVE, "wb");
    assert(junk && fputs("junk", junk) >= 0 && !fclose(junk));
    dsp_eq_changed(d);
    same(d, &ref, 2);
    /* A 3EQ state record: the reread plays the 3EQ at its saved preamp; then Off's held preamp alone (the 3EQ page
     * comparing against bypass); then nothing at all. */
    eq_state st;
    double g[3] = {3, -2, 1};
    assert(!eq_load(&st)); /* the junk on disk: st holds the defaults */
    eq3_bands(&st.eq3, g);
    st.eq3.preamp = -3, st.mode = EQ_3;
    assert(eq_save(&st));
    dsp_eq_changed(d);
    assert(peq_update(&ref, &st.eq3));
    for (int i = 0; i < 4; ++i) same(d, &ref, 2);
    st.mode = EQ_OFF, st.hold = 1;
    assert(eq_save(&st));
    dsp_eq_changed(d);
    peq_preset held;
    eq_effective(&st, &held);
    assert(!held.bypass && !held.count && held.preamp == -3 && peq_update(&ref, &held));
    for (int i = 0; i < 4; ++i) same(d, &ref, 2);
    st.hold = 0;
    assert(eq_save(&st));
    dsp_eq_changed(d);
    float y[1024], z[1024];
    for (int i = 0; i < 4; ++i) { /* the ramp to bypass, then every sample as it came */
        for (int j = 0; j < 1024; ++j) y[j] = z[j] = 0.5f * sinf(j * 0.05f);
        run(d, y, 512, 2, 1);
    }
    run(d, y, 512, 2, 1);
    assert(!memcmp(y, z, sizeof y));
    dsp_close(d, 0);
    unlink(PEQ_ACTIVE);
}

/* mcl-getuvmeter as hciplayer's output computes it: the peak of the first 480 bytes, even samples left. */
static void meter_check(void) {
    int16_t s[512] = {0};
    int uv[2];
    const struct pcm_format f16 = {44100, 2, 2, 0, DSD_PCM}, ff = {44100, 2, 4, 1, DSD_PCM};
    s[0] = 3276, s[1] = -32767, s[239] = 100, s[240] = 32767; /* s[240] is past the first 480 bytes */
    dsp_meter(&f16, 0, (uint8_t *)s, 256, uv);
    assert(uv[0] == -20 && uv[1] == 0);
    dsp_meter(&f16, 0, (uint8_t *)s, 120, uv); /* 480 bytes: too few */
    assert(uv[0] == -60 && uv[1] == -60);
    memset(s, 0, sizeof s);
    dsp_meter(&f16, 0, (uint8_t *)s, 256, uv);
    assert(uv[0] == -60 && uv[1] == -60);
    float a[256] = {0.5f, 0.25f};
    dsp_meter(&ff, 0, (uint8_t *)a, 128, uv);
    assert(uv[0] == -6 && uv[1] == -12); /* -6.02 and -12.04, toward 0 */
    dsp_meter(&ff, 1, (uint8_t *)a, 128, uv);
    assert(uv[0] == -60 && uv[1] == -60);
}

int main(void) {
    assert(!mkdir(PEQ_ROOT "/mnt", 0755) && !mkdir(PEQ_ROOT "/mnt/data", 0755) && !mkdir(PEQ_ROOT "/tmp", 0755));
    bypass_check();
    rates_check();
    crossfade_check();
    midmix_check();
    smart_check();
    mono_check();
    cast_check();
    carry_check();
    meter_check();
    puts("dsp: bypass and the round trip, ReplayGain's clip, DSD, the EQ at 8-384 kHz and 1-8 channels, "
         "the reread, 3EQ and its held preamp, crossfade, mid-mix, smart crossfade, mono and swap, AirPlay's tap, carried filter memory, the meter");
    return 0;
}
