/* Scan's time-stretch (docs/player.md#scan): a held Previous or Next heard at 2, 4 or 8 times the song's speed at
 * its own pitch, by WSOLA, the method of MPlayer's scaletempo, which stock's hciplayer plays its Speed setting
 * through. The output is cut into STRIDE_MS segments; each comes from where the song would be at that speed, moved
 * by up to SEARCH_MS to the place that best continues the segment before (the most correlated over OVERLAP_MS), and
 * is crossfaded into it over OVERLAP_MS. Faster than about 4 times, the segments are short stretches heard from
 * further and further apart, as a CD player's scan sounds.
 *
 * The search correlates a mono sum, and above 48 kHz only every DECIMATE-th frame at every DECIMATE-th place, so it
 * costs about the same at every rate (about 7.5 million multiply-adds a second of output). Speed 0 passes blocks
 * through untouched. Turning it off plays on from the frame after the last segment's, so nothing is skipped or
 * repeated at the release; turning it on starts from the first frame given. A reversed song (engine.c's rewind)
 * is stretched the same way. */
#include "q2player.h"
#include <math.h>
#include <stdlib.h>
#include <string.h>

enum { STRIDE_MS = 40, OVERLAP_MS = 10, SEARCH_MS = 15 };

struct stretch {
    int nch, bytes, is_float, rate;
    int stride, overlap, search, dec; /* in frames; dec: the search's step above 48 kHz */
    double speed;                     /* input frames a frame out; 0 passing through */
    float *in;                        /* input not yet passed, as float, from frame 0 */
    int in_n, in_cap;
    double at;   /* where the next segment would begin at speed, frames into in */
    int cont;    /* the frame after the last segment, frames into in: what plays on from it */
    float *tail; /* the overlap's frames from cont: what the next segment is crossfaded from */
    int have_tail;
    float *fade; /* the crossfade's rising gain, overlap long */
    float *mono, *ref; /* the search's sums: of in, and of tail */
    int mono_cap;
    float *fo; /* frames out, float, then in the input's format at out */
    uint8_t *out;
    int fo_cap, out_cap;
    int64_t fed, used; /* frames given, and frames the output has reached, since the open or the last reset */
};

static int grow(void **p, int *cap, int need, size_t unit) {
    if (need <= *cap) return 1;
    int n = need + need / 2;
    void *b = realloc(*p, (size_t)n * unit);
    if (!b) return 0;
    *p = b, *cap = n;
    return 1;
}

/* The highest speed for a rate: no more frames a second decoded than a 384 kHz file at its own speed. */
double stretch_top(int rate) { return rate > 0 ? 384000.0 / rate : 0; }

struct stretch *stretch_open(const struct pcm_format *f) {
    if (f->dsd || stretch_top(f->rate) < 2 || f->channels < 1 || (f->bytes != 2 && f->bytes != 4)) return NULL;
    struct stretch *s = calloc(1, sizeof *s);
    if (!s) return NULL;
    s->nch = f->channels, s->bytes = f->bytes, s->is_float = f->is_float, s->rate = f->rate;
    s->stride = f->rate * STRIDE_MS / 1000, s->overlap = f->rate * OVERLAP_MS / 1000, s->search = f->rate * SEARCH_MS / 1000;
    s->dec = f->rate > 48000 ? f->rate / 48000 : 1;
    s->tail = malloc((size_t)s->overlap * s->nch * sizeof(float));
    s->fade = malloc((size_t)s->overlap * sizeof(float));
    s->ref = malloc((size_t)s->overlap * sizeof(float));
    if (!s->tail || !s->fade || !s->ref) {
        stretch_close(s);
        return NULL;
    }
    for (int i = 0; i < s->overlap; ++i) s->fade[i] = (i + 0.5f) / s->overlap;
    return s;
}

void stretch_close(struct stretch *s) {
    if (!s) return;
    free(s->in), free(s->tail), free(s->fade), free(s->mono), free(s->ref), free(s->fo), free(s->out);
    free(s);
}

/* Forget what is held: a seek, or a rewind's next block from elsewhere in the song. */
void stretch_reset(struct stretch *s) {
    s->in_n = 0, s->at = 0, s->cont = 0, s->have_tail = 0, s->fed = s->used = 0;
}

void stretch_speed(struct stretch *s, double speed) {
    double top = stretch_top(s->rate);
    s->speed = speed <= 0 ? 0 : speed > top ? top : speed;
}

double stretch_rate(const struct stretch *s) { return s ? s->speed : 0; }

/* Frames given and not yet reached by the output: the stretch's lag behind the decoder, in the song's frames. */
int64_t stretch_behind(const struct stretch *s) { return s ? s->fed - s->used : 0; }

/* The best place for the next segment, 0 to search frames on from p: the most like the tail, by the correlation of
 * mono sums over the overlap, over the candidate's energy's square root. */
static int best(struct stretch *s, int p) {
    int n = s->overlap, d = s->dec, nch = s->nch, span = s->search + n;
    if (!grow((void **)&s->mono, &s->mono_cap, span / d + 1, sizeof(float))) return 0;
    for (int i = 0, k = 0; i < span; i += d, ++k) {
        const float *x = s->in + (size_t)(p + i) * nch;
        float m = 0;
        for (int c = 0; c < nch; ++c) m += x[c];
        s->mono[k] = m;
    }
    for (int i = 0, k = 0; i < n; i += d, ++k) {
        float m = 0;
        for (int c = 0; c < nch; ++c) m += s->tail[i * nch + c];
        s->ref[k] = m;
    }
    int m = (n + d - 1) / d, places = s->search / d;
    float energy = 1e-9f;
    for (int k = 0; k < m; ++k) energy += s->mono[k] * s->mono[k];
    int at = 0;
    float top = -1e30f;
    for (int o = 0; o < places; ++o) {
        const float *x = s->mono + o, *r = s->ref;
        float d0 = 0, d1 = 0, d2 = 0, d3 = 0; /* four sums: the FPU's add latency, not one chain of m */
        int k = 0;
        for (; k + 4 <= m; k += 4) d0 += x[k] * r[k], d1 += x[k + 1] * r[k + 1], d2 += x[k + 2] * r[k + 2], d3 += x[k + 3] * r[k + 3];
        for (; k < m; ++k) d0 += x[k] * r[k];
        float score = (d0 + d1 + d2 + d3) / sqrtf(energy);
        if (score > top) top = score, at = o;
        energy += x[m] * x[m] - x[0] * x[0];
        if (energy < 1e-9f) energy = 1e-9f;
    }
    return at * d;
}

/* The float frames at fo put in the input's format at out: rounded and saturated as dsp.c's from_float. */
static const uint8_t *put(struct stretch *s, int frames) {
    int n = frames * s->nch;
    if (!grow((void **)&s->out, &s->out_cap, n * s->bytes, 1)) return NULL;
    if (s->is_float) memcpy(s->out, s->fo, (size_t)n * 4);
    else if (s->bytes == 2)
        for (int i = 0; i < n; ++i) {
            float v = s->fo[i] * 32768.0f;
            ((int16_t *)s->out)[i] = v >= 32767.0f ? 32767 : v <= -32768.0f ? -32768 : (int16_t)lrintf(v);
        }
    else
        for (int i = 0; i < n; ++i) {
            double v = s->fo[i] * 2147483648.0;
            ((int32_t *)s->out)[i] = v >= 2147483647.0 ? 2147483647 : v <= -2147483648.0 ? INT32_MIN : (int32_t)llrint(v);
        }
    return s->out;
}

/* frames of data in, the frames to play out at *out: data itself while passing through. */
int stretch_run(struct stretch *s, const uint8_t *data, int frames, const uint8_t **out) {
    *out = data;
    if (!s || frames <= 0 || (!s->speed && !s->in_n)) {
        if (s) s->fed += frames, s->used += frames;
        return frames;
    }
    int nch = s->nch;
    if (!grow((void **)&s->in, &s->in_cap, (s->in_n + frames) * nch, sizeof(float))) return frames;
    float *x = s->in + (size_t)s->in_n * nch;
    int n = frames * nch;
    if (s->is_float) memcpy(x, data, (size_t)n * 4);
    else if (s->bytes == 2)
        for (int i = 0; i < n; ++i) x[i] = ((const int16_t *)data)[i] * (1.0f / 32768);
    else
        for (int i = 0; i < n; ++i) x[i] = (float)(((const int32_t *)data)[i] * (1.0 / 2147483648.0));
    s->in_n += frames;
    s->fed += frames;

    int made = 0, ov = s->overlap, st = s->stride;
    if (!s->speed) { /* turned off: the tail, then everything held from after it, then on as it comes */
        int from = s->have_tail ? s->cont + ov : s->cont, rest = s->in_n - from;
        if (rest < 0) rest = 0;
        int all = (s->have_tail ? ov : 0) + rest;
        if (!grow((void **)&s->fo, &s->fo_cap, all * nch, sizeof(float))) return frames;
        if (s->have_tail) memcpy(s->fo, s->tail, (size_t)ov * nch * 4);
        memcpy(s->fo + (size_t)(all - rest) * nch, s->in + (size_t)from * nch, (size_t)rest * nch * 4);
        s->used = s->fed;
        s->in_n = 0, s->at = 0, s->cont = 0, s->have_tail = 0;
        *out = put(s, all);
        return *out ? all : 0;
    }
    while ((int)s->at + s->search + st + ov <= s->in_n) {
        int p = (int)s->at, q = p + (s->have_tail ? best(s, p) : 0);
        if (!grow((void **)&s->fo, &s->fo_cap, (made + st) * nch, sizeof(float))) break;
        float *o = s->fo + (size_t)made * nch;
        const float *a = s->in + (size_t)q * nch;
        int i = 0;
        if (s->have_tail)
            for (; i < ov; ++i)
                for (int c = 0; c < nch; ++c) {
                    float w = s->fade[i];
                    o[i * nch + c] = s->tail[i * nch + c] * (1 - w) + a[i * nch + c] * w;
                }
        memcpy(o + (size_t)i * nch, a + (size_t)i * nch, (size_t)(st - i) * nch * 4);
        made += st;
        s->cont = q + st;
        memcpy(s->tail, s->in + (size_t)s->cont * nch, (size_t)ov * nch * 4);
        s->have_tail = 1;
        s->used = s->fed - s->in_n + s->cont;
        s->at += st * s->speed;
        /* what neither the next segment nor a turn back to 1x can need goes */
        int drop = (int)s->at < s->cont + ov ? (int)s->at : s->cont + ov;
        if (drop > s->in_n) drop = s->in_n;
        if (drop > 0) {
            memmove(s->in, s->in + (size_t)drop * nch, (size_t)(s->in_n - drop) * nch * 4);
            s->in_n -= drop, s->at -= drop, s->cont -= drop;
        }
    }
    if (!made) return 0;
    *out = put(s, made);
    return *out ? made : 0;
}
