/* The DSP chain (docs/player-plan.md#architecture): what Q2 Muse's filter in hciplayer does (patch/peq_player.c),
 * as stages on the playback thread. In order, each left out while its setting is off: ReplayGain, the PEQ or 3EQ
 * (patch/peq.c, Q2 Muse's engine, compiled in as it is into the payload), Mono Audio, Swap L/R, crossfade (the hold
 * and the equal-power mix), AirPlay's tap. With every stage off a block goes to the output as the decoder gave it,
 * not a bit changed. With any on, the block is float from the first stage to one conversion
 * back at the end, which rounds and saturates as hciplayer's converters do.
 *
 * Each song gets its own state (one filter instance per file in hciplayer). The constants and the logic are
 * peq_player.c's, which the docs it cites describe; what differs is where its inputs come from: the position is the
 * frames this song has handed out, the length is media-info's whole seconds, and whether a song ended by itself is
 * the engine's word rather than hciplayer's next and stop flags. */
#include "q2player.h"
#include "peq.h"
#include <stdio.h>
#include <string.h>

/* One song's chain. pass: the chain leaves this song alone (DSD, or a rate or channel count the EQ does not take),
 * as the filter's channels 0. */
struct dsp {
    peq_dsp dsp;
    peq_preset preset;
    int id, fresh, ending, pass, rate, nch, bytes, is_float;
    float rg;     /* ReplayGain's scale, 1 for none */
    double pts;   /* seconds of this song handed out, up to the end of the block in hand */
    int length;   /* media-info's whole seconds */
    float *buf;   /* the block as float */
    uint8_t *out; /* and back in the output's format */
    size_t cap;
    char *url;           /* the song's file (dsp_path), for Smart Crossfade */
    int smart;           /* SMART_*: its analysis not asked, running, planned, or the plain fade */
    struct xf_plan plan; /* the plan, once SMART_PLAN */
};

/* ---- the EQ's filter memory across a gapless change (docs/internals.md#3eq, "Track changes"): the song that
 * played last leaves its settled filter here when it ends within XF_LEAD_S of its length, and the next adopts it on
 * its first block at the same rate, channels and preset, if that came under CARRY_STALE_NS later. */
#define CARRY_STALE_NS 3000000000LL /* as XF_STALE_NS: older (a pause) would ring into the next */
static struct {
    double z[PEQ_CHANNELS][PEQ_BANDS][2];
    peq_preset p;
    int rate, channels, id;
} carry;
static int last_id;       /* the song that played the last block, filtered or passed through */
static long long last_at; /* now_ns of the last block filtered near its song's end */
static int ids;

static void leave(struct dsp *s) {
    if (s->id != last_id || s->fresh) return;
    carry.id = 0;
    if (!s->ending || s->dsp.ramp || s->dsp.waiting || s->dsp.current.bypass || !s->dsp.channels) return;
    memcpy(carry.z, s->dsp.current.z, sizeof carry.z), carry.p = s->preset;
    carry.rate = s->dsp.rate, carry.channels = s->dsp.channels, carry.id = s->id;
}

static void adopt(struct dsp *s) {
    int id = carry.id;
    s->fresh = 0, carry.id = 0; /* one song takes it */
    if (id && id == last_id && carry.rate == s->dsp.rate && carry.channels == s->dsp.channels &&
        !memcmp(&carry.p, &s->preset, sizeof carry.p) && now_ns() - last_at < CARRY_STALE_NS)
        memcpy(s->dsp.current.z, carry.z, sizeof carry.z);
}


/* ---- an int demo writes (XFADE_FILE, MONO_FILE, SWAP_FILE), mapped read-only, looked for once a second of audio
 * until the file appears; 0 till then, or below. */
typedef struct { volatile int *f; unsigned wait; } watched;
static int watch(watched *w, const char *path, int rate, unsigned frames) {
    if (!w->f && w->wait < (unsigned)rate) {
        w->wait += frames;
    } else if (!w->f) {
        w->wait = 0;
        int fd = open(path, O_RDONLY);
        if (fd >= 0) {
            void *p = mmap(0, sizeof *w->f, PROT_READ, MAP_SHARED, fd, 0);
            close(fd);
            if (p != MAP_FAILED) w->f = p;
        }
    }
    return w->f && *w->f > 0 ? *w->f : 0;
}
static watched xfade_file = {0, ~0u}, mono_file = {0, ~0u}, swap_file = {0, ~0u};

/* ---- crossfade (docs/internals.md#crossfade). Near a song's end the chain holds its last seconds back, putting
 * out half of what comes in until the hold is full, so the decoder runs ahead at twice real time and no gap opens.
 * The next song's chain then mixes the held tail under its own start, at its own rate. The ring outlives both songs.
 * A plain fade (demo's XF_MS) holds that many seconds and mixes them at equal power. A smart one (XF_SMART,
 * docs/internals.md#smart-crossfade) holds what smart.c's plan says: the overlap and the trailing silence after it,
 * which is dropped; the next song loses its leading silence, comes in at full level or over its fade-in, the tail
 * fades out under it, and the sum is kept under XF_CEIL. A plan that is not there in time is the plain fade. */
#define XF_FLOATS (6u << 18) /* ponytail: 6 MB; 15 s at 48 kHz stereo, 7.5 s at 96, 3.75 s at 192 */
#define XF_SLACK (1u << 17)  /* room for one block past the held tail */
#define XF_LEAD_S 2.0        /* the hold starts this much early, for the length's whole seconds */
#define XF_STALE_NS 3000000000LL /* a tail cut short, or a mix, not taken on this soon is dropped */
#define XF_COLD_NS 30000000000LL /* the same for a tail its file's end left: a cold start */
#define XF_SEEK_S 1.5        /* a position jump this large while holding is a seek */
#define XF_SMART_LEAD_S 45.0 /* a smart fade's analysis starts this far from the song's end: the longest hold (the
                              * ring's 16.3 s at 44.1 kHz stereo) starts 34.7 s from it, leaving over 10 s */
#define XF_MAX_S 15          /* a smart fade's longest overlap */
#define XF_CEIL 0.99f        /* a smart mix's highest sample: under full scale */
#define XF_RELEASE_S 0.05    /* its limiter's recovery, a time constant */
static struct {
    float *ring;
    unsigned head, len;           /* frames held from head */
    unsigned total, left;         /* the mix: its frames at the next song's rate, and those still to go */
    double pos, step;             /* its read position in the held frames, and held frames per frame out */
    int out, mixer;               /* the mix's output rate, and the song that mixed last */
    int rate, nch, owner, length; /* the holder's format, song and length when it began */
    int stops;                    /* demo's stop count then: a stop since drops the tail (XF_STOPS) */
    int ended;                    /* the holder's file ended by itself */
    long long last;               /* now_ns of the holder's last block */
    double pts;                   /* its position then */
    int smart;                    /* the hold is a smart plan's */
    unsigned trail;               /* its frames after the plan's tail_end, dropped as the mix begins */
    struct xf_plan plan;          /* the next song's side of it */
    unsigned skip, fade, t;       /* the mix: the next song's frames still to drop, its fade-in's, and those mixed */
    double env, rel;              /* the limiter's gain and its recovery a frame (double: in float it stalls short of 1) */
    int release;                  /* the limiter still recovering after the mix, on the next song alone */
} xf;

/* n frames between a and the ring's frame at (of cap), into the ring if put: two spans where it wraps. */
static void ring_copy(float *a, unsigned at, unsigned n, unsigned cap, int nch, int put) {
    unsigned m = n < cap - at ? n : cap - at;
    float *r = xf.ring + at * nch;
    if (put) memcpy(r, a, 4 * m * nch), memcpy(xf.ring, a + m * nch, 4 * (n - m) * nch);
    else memcpy(a, r, 4 * m * nch), memcpy(a + m * nch, xf.ring, 4 * (n - m) * nch);
}

/* One frame of a smart mix kept under XF_CEIL: a gain that drops at once to what the frame needs and recovers over
 * XF_RELEASE_S, so no sample reaches full scale and none is clipped. */
static void limit(float *y, int nch) {
    float peak = 0;
    for (int c = 0; c < nch; ++c) peak = fabsf(y[c]) > peak ? fabsf(y[c]) : peak;
    double need = peak > XF_CEIL ? XF_CEIL / peak : 1;
    if ((xf.env += (1 - xf.env) * xf.rel) > 0.99999) xf.env = 1;
    if (need < xf.env) xf.env = need;
    if (xf.env < 1)
        for (int c = 0; c < nch; ++c) y[c] = (float)(y[c] * xf.env);
}

static void say(const char *what) {
    char line[160];
    int n = snprintf(line, sizeof line, "q2player: smart crossfade: %s", what);
    plog('I', line, n < (int)sizeof line ? n : (int)sizeof line - 1);
}

/* The hold a smart fade wants, in frames at rate: the plan's overlap and trail, capped at XF_MAX_S and the ring's
 * room; plain (the plain fade's) until the analysis is asked for, while it is not wanted or failed, and when it came
 * too late for its hold; 0 while it runs, so no hold starts. The analysis starts XF_SMART_LEAD_S from the end, and
 * gives way to the plain fade when not done by the plain hold's start. */
static unsigned smart_want(struct dsp *s, double pts, int length, int rate, unsigned room, unsigned plain) {
    if (!s->url || s->smart == SMART_PLAIN) return plain;
    if (s->smart == SMART_NONE) {
        if (!(length > 0 && pts >= 0 && pts > length - XF_SMART_LEAD_S)) return plain;
        smart_begin(s->url, s->rg);
        s->smart = SMART_RUN;
    }
    if (s->smart == SMART_RUN) {
        int r = smart_poll(&s->plan);
        if (r == SMART_RUN) {
            if (pts <= length - 2.0 * plain / rate - XF_LEAD_S) return 0;
            smart_cancel("the analysis took too long");
            s->smart = SMART_PLAIN;
            return plain;
        }
        if (r != SMART_PLAN) {
            s->smart = SMART_PLAIN;
            return plain;
        }
        struct xf_plan *p = &s->plan;
        if (p->overlap > (int64_t)XF_MAX_S * rate) p->overlap = (int64_t)XF_MAX_S * rate;
        if (p->trail + rate / 10 > (int64_t)room) {
            say("plain, its trailing silence is longer than the hold");
            s->smart = SMART_PLAIN;
            return plain;
        }
        if (p->overlap + p->trail > (int64_t)room) {
            p->overlap = room - p->trail;
            say("overlap cut to what the hold has room for at this rate");
        }
        double at = length - 2.0 * (double)(p->overlap + p->trail) / rate - XF_LEAD_S;
        if (at > 0 && pts > at) {
            say("plain, the analysis came too late for its hold");
            s->smart = SMART_PLAIN;
            return plain;
        }
        s->smart = SMART_PLAN;
    }
    return (unsigned)(s->plan.overlap + s->plan.trail);
}

/* Frames of a (interleaved, nch) put out in place: all of them, fewer while the hold fills or a smart mix drops the
 * next song's leading silence. said: XFADE_FILE's word, demo's fade for the next change. */
static unsigned crossfade(struct dsp *s, float *a, unsigned frames, int nch, int rate, int said) {
    int ms = said & XF_MS, stopped = said >> XF_STOPS != xf.stops;
    if (xf.release && (xf.mixer != s->id || xf.total)) xf.release = 0;
    if (xf.release) { /* after a smart mix: the next song alone, until the limiter has let go */
        for (unsigned i = 0; i < frames && xf.env < 1; ++i) limit(a + i * nch, nch);
        xf.release = xf.env < 1;
    }
    if (!ms && !xf.len) return frames;
    unsigned cap = XF_FLOATS / (unsigned)nch;
    long long now = now_ns();
    double pts = s->pts;
    int mine = xf.len && xf.owner == s->id;
    if (xf.len && (xf.total || !mine)) {
        /* The mix begins on the first block after the hold, unless demo saw a stop since it began. A tail whose
         * file ended by itself waits XF_COLD_NS for a cold start, one cut short XF_STALE_NS. Another rate plays it
         * through linear interpolation (step held frames an output frame), a fading tail, so that is enough.
         * Mid-mix, its song's pause only waits; a stop, or a song more than XF_STALE_NS on, drops what is left. A
         * song shorter than the fade hands the rest to the next, retimed at another rate. */
        if (xf.total && (stopped || (s->id != xf.mixer && now - xf.last > XF_STALE_NS)))
            xf.len = xf.total = 0;
        else if (xf.total && rate != xf.out) {
            double step = (double)xf.rate / rate;
            xf.total = (unsigned)(xf.total * xf.step / step);
            xf.left = (unsigned)((xf.len - xf.pos) / step);
            xf.step = step, xf.out = rate;
        } else if (!xf.total) {
            if (!stopped && now - xf.last <= (xf.ended ? XF_COLD_NS : XF_STALE_NS)) {
                if (xf.smart) { /* the trailing silence goes; the next song's side is its own if it is the one planned */
                    xf.len = xf.len > xf.trail ? xf.len - xf.trail : 0;
                    int planned = s->url && !strcmp(s->url, xf.plan.next) && xf.plan.next_rate > 0;
                    double to = planned ? (double)rate / xf.plan.next_rate : 0;
                    xf.skip = (unsigned)(xf.plan.skip * to), xf.fade = (unsigned)(xf.plan.fade * to), xf.t = 0;
                    xf.env = 1, xf.rel = 1 - exp(-1 / (XF_RELEASE_S * rate)), xf.release = 0;
                    if (!planned) say("the next song is not the one analysed: no skip, no fade-in");
                }
                xf.step = (double)xf.rate / rate, xf.pos = 0, xf.out = rate;
                xf.total = xf.left = (unsigned)(xf.len / xf.step);
            }
            if (!xf.total) xf.len = 0;
        }
        if (xf.total && xf.skip) { /* the next song's leading silence */
            unsigned d = frames < xf.skip ? frames : xf.skip;
            memmove(a, a + (size_t)d * nch, (size_t)(frames - d) * nch * 4);
            frames -= d, xf.skip -= d;
            xf.last = now, xf.mixer = s->id;
            if (!frames) return 0;
        }
        if (xf.total) {
            int hn = xf.nch; /* the ring holds the holder's channels */
            unsigned hcap = XF_FLOATS / (unsigned)hn;
            unsigned n = frames < xf.left ? frames : xf.left;
            /* The gains turn by a fixed angle a frame: exact at each block's start, rotated after, in double (and
             * cos d as 1 - 2 sin^2 d/2) or a long fade's rounding drifts to 1e-4. A smart mix's tail fades out the
             * same way over its own quiet run, and as cos squared over an overlap that begins in its body (in_body),
             * so its loud part gives way sooner; the next song is at full level but for its fade-in, and the limiter
             * keeps the sum down. */
            float d = 1.5707963f / xf.total, p = (xf.total - xf.left + 0.5f) * d, h = sinf(d / 2);
            double in = sinf(p), out = cosf(p), sd = sinf(d), cd = 1 - 2.0 * h * h;
            for (unsigned i = 0; i < n; ++i, xf.pos += xf.step) {
                unsigned at = (unsigned)xf.pos, next = at + 1 < xf.len ? at + 1 : at;
                float frac = (float)(xf.pos - at), fo = (float)(xf.smart && xf.plan.in_body ? out * out : out);
                float fi = !xf.smart ? (float)in : xf.t < xf.fade ? sinf((xf.t + 0.5f) / xf.fade * 1.5707963f) : 1;
                if ((at += xf.head) >= hcap) at -= hcap;
                if ((next += xf.head) >= hcap) next -= hcap;
                const float *h0 = xf.ring + at * hn, *h1 = xf.ring + next * hn;
                for (int c = 0; c < nch; ++c) {
                    /* stereo under mono is its mean, as Mono Audio's; mono is both sides */
                    int l = hn > nch ? 0 : c % hn, r = hn > nch ? 1 : l;
                    float x0 = (h0[l] + h0[r]) * 0.5f, x1 = (h1[l] + h1[r]) * 0.5f;
                    a[i * nch + c] = a[i * nch + c] * fi + (x0 + (x1 - x0) * frac) * fo;
                }
                if (xf.smart) limit(a + i * nch, nch), ++xf.t;
                double t = in * cd + out * sd;
                out = out * cd - in * sd, in = t;
            }
            if (!(xf.left -= n)) {
                xf.release = xf.smart && xf.env < 1;
                for (unsigned i = n; i < frames && xf.release && xf.env < 1; ++i) limit(a + i * nch, nch);
                xf.len = xf.total = 0;
            }
            xf.last = now, xf.mixer = s->id;
            return frames;
        }
    }
    int length = mine ? xf.length : s->length;
    unsigned room = cap - XF_SLACK / (unsigned)nch, plain = (unsigned)ms * (unsigned)(rate / 10) / 100;
    if (plain > room) plain = room;
    unsigned want = (said & XF_SMART) && ms ? smart_want(s, pts, length, rate, room, plain) : plain;
    int ending = want && length > 0 && pts >= 0 && pts > length - 2.0 * want / rate - XF_LEAD_S;
    mine = xf.len && xf.owner == s->id;
    if (mine && (__builtin_fabs(pts - xf.pts) > XF_SEEK_S || xf.len + frames > cap))
        xf.len = mine = 0; /* a seek: the held audio was left behind */
    /* A hold once begun is finished, whatever demo says now: no fade any more only stops it growing, and the next
     * song mixes what is held. Chosen over playing it out: no block runs after the file's end to carry the rest. */
    if (mine && !ms) want = xf.len;
    if (!mine && (!ending || frames > cap - want || (!xf.ring && !(xf.ring = calloc(XF_FLOATS, sizeof(float))))))
        return frames;
    if (!xf.len) {
        xf.head = 0, xf.owner = s->id, xf.rate = rate, xf.nch = nch, xf.length = length;
        xf.stops = said >> XF_STOPS, xf.ended = 0;
        xf.smart = s->smart == SMART_PLAN && want == (unsigned)(s->plan.overlap + s->plan.trail);
        if (xf.smart) xf.trail = (unsigned)s->plan.trail, xf.plan = s->plan;
    }
    ring_copy(a, (xf.head + xf.len) % cap, frames, cap, nch, 1);
    xf.len += frames;
    unsigned k = (frames + 1) / 2;
    if (xf.len > want && xf.len - want > k) k = xf.len - want;
    if (k > frames) k = frames;
    ring_copy(a, xf.head, k, cap, nch, 0);
    xf.head = (xf.head + k) % cap;
    xf.len -= k;
    xf.last = now;
    xf.pts = pts;
    return k;
}

/* ---- AirPlay's tap (peq.h cast_tap, docs/internals.md#airplay-sender): what plays, at CAST_RATE, 16-bit, while
 * the q2-airplay app reads. Another rate goes through a windowed sinc (Blackman, RS_ZEROS zero crossings a side at
 * the lower rate's cutoff), its kernel tabled at RS_PHASES points an input frame, read between the nearest two.
 * Dither (TPDF, 1 LSB) only where a sample isn't 16-bit already. */
#define RS_PHASES 512
#define RS_ZEROS 12
#define CAST_SEEK_S 0.5 /* the song's place moving this much off its run: a seek */
#define CAST_END_S 2.5  /* a song left this close to its end runs into the next: no skip */
static struct {
    cast_tap *t;
    unsigned look;      /* frames since the file or, idle, its beat was last looked at */
    int live;           /* the app beat at the last look: looked at every block */
    int rate, half;     /* the kernel's input rate, its taps a side */
    float *kernel;      /* half * RS_PHASES + 1 points, from the centre out */
    float *buf;         /* stereo history, then the block */
    unsigned cap, held; /* buf's frames, those held from earlier blocks */
    double pos;         /* the next output frame's place in buf */
    unsigned noise, id; /* the dither's LCG; the last block's song */
    double pts;         /* its end's place in its song, s */
    int len, muted, in; /* its song's length, s; the last block muted; its rate */
} cs;

static cast_tap *cast_live(int rate, unsigned frames) {
    if (!cs.live && cs.look < (unsigned)rate) { /* idle: looked at once a second of audio, no clock read */
        cs.look += frames;
        return 0;
    }
    cs.look = 0;
    if (!cs.t) {
        unsigned head[2] = {0, 0};
        int fd = open(CAST_FILE, O_RDWR);
        if (fd < 0) return 0;
        if (read(fd, head, sizeof head) == sizeof head && head[0] == CAST_MAGIC && head[1] == sizeof(cast_tap)) {
            void *p = mmap(0, sizeof(cast_tap), PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
            if (p != MAP_FAILED) cs.t = p;
        }
        close(fd);
        if (!cs.t) return 0;
    }
    cs.live = CAST_CLOCK(now_ns()) - cs.t->want < CAST_STALE_PLAYER;
    return cs.live ? cs.t : 0;
}

static void rs_reset(void) {
    cs.held = (unsigned)cs.half; /* a silent run-in, so the first output frame has its history */
    cs.pos = cs.half;
    if (cs.buf) memset(cs.buf, 0, 8 * cs.held);
}

static void rs_kernel(int rate) {
    float ratio = rate > CAST_RATE ? (float)CAST_RATE / rate : 1.0f, fc = 0.46f * ratio, pi = 3.14159265f;
    cs.half = (int)(RS_ZEROS / (2 * fc)) + 1;
    free(cs.kernel);
    free(cs.buf);
    cs.buf = 0, cs.cap = 0;
    cs.kernel = calloc((unsigned)cs.half * RS_PHASES + 1, sizeof(float));
    for (int j = 0; cs.kernel && j <= cs.half * RS_PHASES; ++j) {
        float x = (float)j / RS_PHASES, a = 2 * pi * fc * x, w = x / cs.half;
        cs.kernel[j] = 2 * fc * (j ? sinf(a) / a : 1.0f) * (0.42f + 0.5f * cosf(pi * w) + 0.08f * cosf(2 * pi * w));
    }
    cs.rate = cs.kernel ? rate : 0;
}

static short s16(float x) {
    float v = x * 32768.0f;
    if (v != (float)(int)v) { /* not a 16-bit sample: TPDF dither */
        cs.noise = cs.noise * 1664525u + 1013904223u;
        v += (float)((int)(cs.noise >> 16 & 255) - (int)(cs.noise >> 24)) * (1.0f / 256);
    }
    return v >= 32767.0f ? 32767 : v <= -32768.0f ? -32768 : (short)(v + (v >= 0 ? 0.5f : -0.5f));
}

static void cast_put(cast_tap *t, unsigned seq, float l, float r) {
    short *o = t->ring[seq & (CAST_RING - 1)];
    o[0] = s16(l), o[1] = s16(r);
}

/* frames of a (interleaved, nch) at rate into the ring; the new seq. */
static unsigned cast_copy(cast_tap *t, const float *a, unsigned frames, int nch, int rate) {
    unsigned seq = t->seq, r = nch > 1;
    if (rate == CAST_RATE) {
        for (unsigned i = 0; i < frames; ++i, a += nch) cast_put(t, seq++, a[0], a[r]);
        cs.in = rate;
        return seq;
    }
    if (rate != cs.in) { /* another rate: the resampler's history starts again */
        if (rate != cs.rate) rs_kernel(rate);
        rs_reset();
    }
    cs.in = rate;
    if (!cs.kernel) return seq;
    unsigned n = cs.held + frames;
    if (n > cs.cap) { /* room for the block after the history */
        float *b = calloc(2 * (n + 4096), sizeof(float));
        if (!b) return seq;
        if (cs.buf) memcpy(b, cs.buf, 8 * cs.held);
        free(cs.buf);
        cs.buf = b, cs.cap = n + 4096;
    }
    for (unsigned i = 0; i < frames; ++i, a += nch) cs.buf[2 * (cs.held + i)] = a[0], cs.buf[2 * (cs.held + i) + 1] = a[r];
    double step = (double)rate / CAST_RATE;
    const float *k = cs.kernel;
    int half = cs.half;
    while (cs.pos + half < n) {
        int i = (int)cs.pos;
        float x = (float)(cs.pos - i) * RS_PHASES, ff = x - (int)x;
        int jf = (int)x;
        const float *p = cs.buf + 2 * (i - half + 1), *q;
        float l = 0, rr = 0;
        for (int d = half - 1; d >= 0; --d, p += 2) { /* frames at and before the point */
            q = k + d * RS_PHASES + jf;
            float c = q[0] + ff * (q[1] - q[0]);
            l += p[0] * c, rr += p[1] * c;
        }
        for (int d = 1; d <= half; ++d, p += 2) { /* after it */
            q = k + d * RS_PHASES - jf - 1;
            float c = q[0] + (1 - ff) * (q[1] - q[0]);
            l += p[0] * c, rr += p[1] * c;
        }
        cast_put(t, seq++, l, rr);
        cs.pos += step;
    }
    unsigned drop = (unsigned)((int)cs.pos - half + 1); /* frames no output needs again */
    if (drop > n) drop = n;
    for (unsigned i = 0; i < 2 * (n - drop); ++i) cs.buf[i] = cs.buf[2 * drop + i];
    cs.held = n - drop, cs.pos -= drop;
    return seq;
}

/* A block about to play (frames in, before a fade holds any back): a seek, or a skip (a new song while the last
 * wasn't near its end), marks jump_seq, and the resampler starts afresh there. */
static void cast_jump(cast_tap *t, struct dsp *s, unsigned frames, int rate) {
    double pts = s->pts, start = pts - (double)frames / rate;
    int jump = cs.id && ((unsigned)s->id != cs.id ? !(cs.len > 0 && cs.pts >= cs.len - CAST_END_S)
                                                  : pts >= 0 && cs.pts >= 0 && __builtin_fabs(start - cs.pts) > CAST_SEEK_S);
    if (jump) {
        t->jump_seq = t->seq;
        __atomic_thread_fence(__ATOMIC_RELEASE);
        ++t->jumps;
        if (cs.rate) rs_reset();
    }
    cs.id = (unsigned)s->id, cs.pts = pts, cs.len = s->length;
}

static void cast_tap_block(cast_tap *t, struct dsp *s, const float *a, unsigned frames, int nch, int rate) {
    unsigned seq = cast_copy(t, a, frames, nch, rate);
    t->id = (unsigned)s->id;
    t->pts_ms = s->pts >= 0 ? (int)(s->pts * 1000) : 0;
    t->dsd = 0;
    t->stamp = now_ns();
    __atomic_thread_fence(__ATOMIC_RELEASE); /* the frames before seq */
    t->seq = seq;
}

/* ---- the song's chain */

struct dsp *dsp_open(const struct pcm_format *f, int untouched, float replaygain, int length) {
    struct dsp *s = calloc(1, sizeof *s);
    if (!s) return NULL;
    s->id = ++ids, s->rate = f->rate, s->nch = f->channels, s->bytes = f->bytes, s->is_float = f->is_float;
    s->rg = replaygain, s->length = length;
    s->pass = untouched || f->rate < 8000 || f->rate > 384000 || f->channels < 1 || f->channels > PEQ_CHANNELS;
    if (!s->pass) {
        peq_load_active(&s->preset);
        peq_reset(&s->dsp, f->rate, f->channels, &s->preset);
        s->fresh = 1;
    }
    return s;
}

/* mcl-seteq: the payload saved a new EQ state (patch/peq_ui.c peq_stock_eq); it plays through the engine's 20 ms
 * ramp. An unchanged or unreadable state keeps the current filter. */
void dsp_eq_changed(struct dsp *s) {
    peq_preset p;
    if (s && !s->pass && peq_load_active(&p) && memcmp(&p, &s->preset, sizeof p) && peq_update(&s->dsp, &p)) s->preset = p;
}

int dsp_tail_held(void) { return xf.len != 0; }

void dsp_path(struct dsp *s, const char *url) {
    if (s && !s->url) s->url = strdup(url);
}

void dsp_close(struct dsp *s, int ended) {
    if (!s) return;
    if (!s->pass) leave(s);
    if (xf.len && xf.owner == s->id) xf.ended = ended;
    if (s->smart == SMART_RUN) smart_cancel(NULL); /* the song is gone before its plan */
    free(s->url);
    free(s->buf);
    free(s->out);
    free(s);
}

static int grow(struct dsp *s, int frames) {
    size_t need = (size_t)frames * s->nch;
    if (need <= s->cap) return 1;
    float *b = realloc(s->buf, need * sizeof *b);
    if (b) s->buf = b;
    uint8_t *o = realloc(s->out, need * 4);
    if (o) s->out = o;
    if (!b || !o) return 0;
    s->cap = need;
    return 1;
}

static void to_float(const struct dsp *s, const uint8_t *data, int frames) {
    int n = frames * s->nch;
    if (s->is_float) memcpy(s->buf, data, (size_t)n * 4);
    else if (s->bytes == 2)
        for (int i = 0; i < n; ++i) s->buf[i] = ((const int16_t *)data)[i] * (1.0f / 32768);
    else
        for (int i = 0; i < n; ++i) s->buf[i] = (float)(((const int32_t *)data)[i] * (1.0 / 2147483648.0));
}

/* The one conversion back: round to nearest and saturate, as hciplayer's float-to-integer converters
 * (0x451994 for 16 bits: lrintf of x * 32768, clipped). Float goes out as it is. */
static void from_float(struct dsp *s, int frames) {
    int n = frames * s->nch;
    if (s->is_float) memcpy(s->out, s->buf, (size_t)n * 4);
    else if (s->bytes == 2)
        for (int i = 0; i < n; ++i) {
            float v = s->buf[i] * 32768.0f;
            ((int16_t *)s->out)[i] = v >= 32767.0f ? 32767 : v <= -32768.0f ? -32768 : (int16_t)lrintf(v);
        }
    else
        for (int i = 0; i < n; ++i) {
            double v = s->buf[i] * 2147483648.0;
            ((int32_t *)s->out)[i] = v >= 2147483647.0 ? 2147483647 : v <= -2147483648.0 ? INT32_MIN : (int32_t)llrint(v);
        }
}

static int chain(struct dsp *s, const uint8_t *data, int frames, double pts, const uint8_t **out);
int dsp_run(struct dsp *s, const uint8_t *data, int frames, double pts, const uint8_t **out) {
    *out = data;
    if (!s || frames <= 0) return frames;
    if (s->pass) {
        xf.len = xf.total = xf.release = 0; /* DSD or the like plays between: the tail has nothing to mix under */
        cast_tap *cast = cast_live(s->rate, 4096);
        if (cast) cast->dsd = 1; /* played untouched: the app says so */
        last_id = s->id;
        return frames;
    }
    int k = chain(s, data, frames, pts, out);
    last_id = s->id; /* after the block: adopt compares the song before */
    return k;
}

static int chain(struct dsp *s, const uint8_t *data, int frames, double pts, const uint8_t **out) {
    s->pts = pts;
    unsigned n = (unsigned)frames;
    int rate = s->rate, nch = s->nch;
    if (s->fresh) adopt(s);
    if (!s->dsp.current.bypass) { /* only a song played to its end hands its memory on */
        s->ending = s->length > 0 && pts >= 0 && pts > s->length - XF_LEAD_S;
        if (s->ending) last_at = now_ns();
    }
    cast_tap *cast = cast_live(rate, n);
    /* silent while the app mutes, and until demo has the earphones' volume back (volume -1) */
    int mute = (cast && cast->mute) || (cs.t && cs.t->volume >= 0);
    int eq = !s->dsp.current.bypass || s->dsp.ramp || s->dsp.waiting;
    int mono = nch == 2 && watch(&mono_file, MONO_FILE, rate, n), swap = nch == 2 && watch(&swap_file, SWAP_FILE, rate, n);
    int said = watch(&xfade_file, XFADE_FILE, rate, n), fade = xf.len || xf.release || (said & XF_MS);
    if (!(s->rg != 1 || eq || mono || swap || fade || cast || mute || mute != cs.muted))
        return frames; /* nothing to change: the block as it came */
    if (!grow(s, frames)) return frames;
    to_float(s, data, frames);
    float *a = s->buf;
    if (s->rg != 1) /* hciplayer's replaygain filter, MPlayer's volume filter: the scale, then a hard clip */
        for (unsigned i = 0; i < n * nch; ++i) {
            float x = a[i] * s->rg;
            a[i] = x > 1 ? 1 : x < -1 ? -1 : x;
        }
    if (cast) cast_jump(cast, s, n, rate);
    if (mute != cs.muted && !(cs.muted = mute)) /* the EQ's filters start again from rest */
        peq_reset(&s->dsp, rate, nch, &s->preset);
    if (!mute) peq_process(&s->dsp, a, n);
    if (mono) /* Mono Audio (patch/crossfade.c mono_sync), after the EQ's sides */
        for (float *p = a, *end = a + 2 * n; p < end; p += 2) p[0] = p[1] = (p[0] + p[1]) * 0.5f;
    if (swap)
        for (float *p = a, *end = a + 2 * n; p < end; p += 2) {
            float left = p[0];
            p[0] = p[1], p[1] = left;
        }
    n = crossfade(s, a, n, nch, rate, said);
    if (cast) cast_tap_block(cast, s, a, n, nch, rate);
    if (mute) memset(a, 0, (size_t)n * nch * 4);
    from_float(s, (int)n);
    *out = s->out;
    return (int)n;
}

/* mcl-getuvmeter's two levels, as hciplayer's output computes them for each write (0x43a778): the peak of the first
 * 480 bytes' samples, even ones the left and odd ones the right, as whole dB below full scale truncated toward 0, and
 * -60 for silence, a write under 481 bytes or DSD. demo only asks from its VU page, which Q2 Muse leaves out. */
void dsp_meter(const struct pcm_format *f, int untouched, const uint8_t *data, int frames, int uv[2]) {
    int bytes = frames * f->channels * f->bytes, n = 480 / f->bytes;
    double peak[2] = {0, 0}, full = f->is_float ? 1.0 : f->bytes == 2 ? 32767.0 : 2147483647.0;
    uv[0] = uv[1] = -60;
    if (untouched || bytes < 481) return;
    for (int i = 0; i < n; ++i) {
        double v = f->is_float ? ((const float *)data)[i] : f->bytes == 2 ? ((const int16_t *)data)[i] : ((const int32_t *)data)[i];
        v = v < 0 ? -v : v;
        if (v > peak[i & 1]) peak[i & 1] = v;
    }
    for (int c = 0; c < 2; ++c)
        if (peak[c] > 0) uv[c] = (int)(20 * log10(peak[c] / full));
}
