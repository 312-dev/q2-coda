/* The DSP chain (docs/player-plan.md#architecture): what Q2 Muse's filter in hciplayer does (patch/peq_player.c),
 * as stages on the playback thread. In order, each left out while its setting is off: ReplayGain, Swap L/R, the PEQ
 * or 3EQ (patch/peq.c, Q2 Muse's engine, compiled in as it is into the payload), Mono Audio, crossfade (the hold and
 * the equal-power mix), AirPlay's tap. Swap L/R comes before the EQ so that the EQ's balance and its Channel: L and R
 * bands act on the ear each channel is heard in, as stock's Balance (the DAC's or the output's volume) does. With every stage off a block goes to the output as the decoder gave it,
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
    /* The tail from the file (docs/internals.md#the-tail-from-the-file): the song's second open while it follows the
     * song, until a hold takes it. */
    struct tail *rd;
    int rd_state;          /* RD_* */
    int rd_ahead;          /* it became level inside the block in hand, and stands at that block's end */
    int64_t frame;         /* the block in hand's first frame in the file; below 0, it has no place (dsp_frame) */
    const uint8_t *raw;    /* that block as the decoder gave it */
    peq_dsp before;        /* the EQ as it stood before the block in hand, kept while the second open is level */
    int was_muted;         /* and whether the block before it played muted */
    int mono, swap, mute;  /* the block in hand's settings */
    int eq_gen;            /* the EQ changes taken (dsp_eq_changed) */
};
/* The second open: not asked for; opening, or open and ahead of the song; level with the song, every frame so far
 * the same; given up, or taken by a hold. */
enum { RD_NONE, RD_OPEN, RD_LEVEL, RD_OFF };

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
 * The next song's chain then mixes the held tail under its own start, at its own rate. The tail outlives the song
 * that held it: in a ring mapped for it, or, for a file whose second decode is its first, not kept at all and read
 * from the file again (below, "the tail from the file").
 * A plain fade (demo's XF_MS) holds that many seconds and mixes them at equal power. A smart one (XF_SMART,
 * docs/internals.md#smart-crossfade) holds what smart.c's plan says: the overlap and the trailing silence after it,
 * which is dropped; the next song loses its leading silence, comes in at full level or over its fade-in, the tail
 * fades out under it, and the sum is kept under XF_CEIL. A plan that is not there in time is the plain fade. */
/* A tail held in memory has a ring of float frames, mapped for the hold at the hold's own size and unmapped when the
 * mix ends: XF_FLOATS is the most it may be. ponytail: 6 MB; 15 s at 48 kHz stereo, 7.5 s at 96, 3.75 s at 192. A tail
 * read from the file has no ring and no such limit (XF_FILED_S). */
#define XF_FLOATS (ring_mb() << 18)
#define XF_SLACK (1u << 17)  /* room for one block past the held tail */
#define XF_FILED_S 120u      /* the longest hold of a tail read from the file: beyond any plan */
#define XF_EVENTS 16         /* changes of the stages' settings a hold remembers for the frames not yet read back */
#define RD_AHEAD_S 3.0       /* the second open begins this far ahead of the song */
#define RD_LEAD_S 6.0        /* and is asked for this long before the hold: RD_AHEAD_S, and as much again to spare */
#define XF_HOLD_WAIT_MS 4000 /* a hold waits this long for a second open that has nothing decoded, then leaves it */
#define XF_CUT_S 0.2         /* a tail whose second open falls behind under the next song fades out over this */
#define XF_LEAD_S 2.0        /* the hold starts this much early, for the length's whole seconds */
#define XF_STALE_NS 3000000000LL /* a tail cut short, or a mix, not taken on this soon is dropped */
#define XF_COLD_NS 30000000000LL /* the same for a tail its file's end left: a cold start */
#define XF_SEEK_S 1.5        /* a position jump this large while holding is a seek */
#define XF_SMART_LEAD_S 45.0 /* a smart fade's analysis starts this far from the song's end: a ring's longest hold
                              * (16.3 s at 44.1 kHz stereo) starts 34.7 s from it, leaving over 10 s; a longer one,
                              * of a tail read from the file, is cut to that when its plan comes too late for it */
#define XF_MAX_S 15          /* a smart fade's longest overlap */
#define XF_CEIL 0.99f        /* a smart mix's highest sample: under full scale */
#define XF_RELEASE_S 0.05    /* its limiter's recovery, a time constant */
struct xf_set { int mono, swap, mute; };
static struct {
    float *ring;                  /* NULL while nothing is held in memory */
    size_t bytes;                 /* its mapping's size */
    unsigned cap;                 /* its frames */
    unsigned head, len;           /* frames held from head; of a tail read from the file, those still to read */
    unsigned total, left;         /* the mix: its frames at the next song's rate, and those still to go */
    double pos, step;             /* its read position in the held frames, and held frames per frame out */
    int out_rate, mixer;          /* the mix's output rate, and the song that mixed last */
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
    /* The tail read from the file (rd set, no ring): the holder's second open, and the holder's stages as they stood
     * at the frame it has reached, since the frames it gives have not been through them. */
    struct tail *rd;
    peq_dsp eq;
    peq_preset preset;
    float rg;
    int unit, is_float;           /* a sample's bytes in the file's format, and whether it is float */
    struct xf_set set, told;      /* the settings at the frame reached, and those of the holder's newest block */
    int muted;                    /* the frame before the one reached played muted */
    uint64_t in, out;             /* frames the hold was given, and frames read back */
    int64_t next;                 /* the file's frame the holder's next block must begin at */
    struct xf_event {
        uint64_t at;              /* from this frame of the hold on */
        struct xf_set set;
        int eq;                   /* preset is a new EQ, taken as dsp_eq_changed takes it */
        peq_preset preset;
    } ev[XF_EVENTS];
    unsigned events;
    int eq_gen;                   /* the holder's eq_gen at its newest block */
    int said;                     /* a shortfall or an overflow was logged for this tail */
    uint8_t *file;                /* a read, in the file's format */
    size_t file_cap;
    float *win;                   /* the mix's window: held frames from win_lo, as float, after the stages */
    unsigned win_cap, win_lo, win_n;
    int cut;                      /* the second open fell behind in the mix: the tail fades out early, */
    unsigned cut_from, cut_at;    /* from this held frame to nothing at this one, the last one decoded */
} xf;

/* The ring's most, in MB. Q2PLAYER_XF_RING=MB: test/player/run.py's player that holds every tail in memory
 * (ring_only), in a ring of up to that size, which a tail read from the file is compared with. */
static unsigned ring_mb(void) {
    static unsigned mb;
    if (!mb) {
        const char *e = getenv("Q2PLAYER_XF_RING");
        mb = e && atoi(e) > 0 && atoi(e) <= 256 ? (unsigned)atoi(e) : 6;
    }
    return mb;
}

/* The tail is over, mixed or dropped: its memory goes back, and a second open is closed. */
static void tail_drop(void) {
    if (xf.ring) munmap(xf.ring, xf.bytes);
    tail_close(xf.rd);
    free(xf.file);
    free(xf.win);
    xf.ring = NULL, xf.rd = NULL, xf.file = NULL, xf.win = NULL;
    xf.file_cap = 0, xf.win_cap = xf.win_lo = xf.win_n = 0, xf.cut = 0;
    xf.len = xf.total = 0;
}

/* A ring for a hold of want frames, and a block more. */
static int ring_open(unsigned want, int nch) {
    unsigned most = XF_FLOATS / (unsigned)nch, cap = want + XF_SLACK / (unsigned)nch;
    if (cap > most) cap = most;
    size_t bytes = (size_t)cap * nch * sizeof(float);
    void *p = mmap(0, bytes, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    if (p == MAP_FAILED) return 0;
    xf.ring = p, xf.bytes = bytes, xf.cap = cap;
    return 1;
}

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

static void note(const char *what, unsigned long n) {
    char line[200];
    int k = snprintf(line, sizeof line, what, n);
    plog('I', line, k < (int)sizeof line ? k : (int)sizeof line - 1);
}

/* The song's plan made to fit a hold of room frames: its overlap cut, logged; 0, and the plain fade, when its trail
 * alone is longer. */
static int fit(struct dsp *s, unsigned room) {
    struct xf_plan *p = &s->plan;
    if (p->trail + s->rate / 10 > (int64_t)room) {
        say("plain, its trailing silence is longer than the hold");
        s->smart = SMART_PLAIN;
        return 0;
    }
    if (p->overlap + p->trail > (int64_t)room) {
        p->overlap = room - p->trail;
        say("overlap cut to what the hold has room for at this rate");
    }
    return 1;
}

/* The hold a smart fade wants, in frames at rate: the plan's overlap and trail, capped at XF_MAX_S and at room, the
 * hold's (the ring's, or XF_FILED_S while the tail may be read from the file); plain (the plain fade's) until the
 * analysis is asked for, while it is not wanted or failed, and when it came too late for its hold; 0 while it runs,
 * so no hold starts. A plan longer than ring, the ring's own room, that came too late for its hold is cut to the
 * ring's before it is given up, as it is when there is no second open. The analysis starts XF_SMART_LEAD_S from the
 * end, and gives way to the plain fade when not done by the plain hold's start. */
static unsigned smart_want(struct dsp *s, double pts, int length, int rate, unsigned room, unsigned ring, unsigned plain) {
    if (!s->url || s->smart == SMART_PLAIN) return plain;
    if (s->smart == SMART_NONE) {
        if (!(length > 0 && pts >= 0 && pts > length - XF_SMART_LEAD_S)) return plain;
        smart_begin(s->url, s->rg);
        s->smart = SMART_RUN;
    }
    if (s->smart == SMART_RUN) {
        int r = smart_poll(&s->plan);
        /* Q2PLAYER_XF_WAIT (tail.c): test/player/run.py's player waits for the analysis, however busy the machine */
        for (int i = 0; r == SMART_RUN && getenv("Q2PLAYER_XF_WAIT") && !getenv("Q2PLAYER_SMART_DELAY_MS") && i < 60000; ++i)
            usleep(1000), r = smart_poll(&s->plan);
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
        if (!fit(s, room)) return plain;
        double at = length - 2.0 * (double)(p->overlap + p->trail) / rate - XF_LEAD_S;
        if (at > 0 && pts > at && p->overlap + p->trail > (int64_t)ring) {
            if (!fit(s, ring)) return plain;
            at = length - 2.0 * (double)(p->overlap + p->trail) / rate - XF_LEAD_S;
        }
        if (at > 0 && pts > at) {
            say("plain, the analysis came too late for its hold");
            s->smart = SMART_PLAIN;
            return plain;
        }
        s->smart = SMART_PLAN;
    }
    return (unsigned)(s->plan.overlap + s->plan.trail);
}

/* ---- the tail from the file (docs/internals.md#the-tail-from-the-file). A hold delays the song by up to the fade's
 * length while the decoder runs ahead, and the mix then needs the song's last frames: both are the same frames a
 * little later, so instead of keeping them the chain reads them from the file a second time (tail.c). The second open
 * is asked for RD_LEAD_S before the hold is to begin, RD_AHEAD_S ahead of the song; once the song reaches it, each
 * block is compared with the same frames read there, so it is level with the song and known to give the song's own
 * samples. A hold that begins while it is level
 * takes it: the first decode still runs ahead to the file's end and through its own stages, which keeps the engine,
 * the position and the EQ memory handed to the next song as they are, but its frames are only counted and noted
 * (tail_note); what plays, and what the mix reads, is the second open's frames through a copy of the stages as they
 * stood at the hold's first frame, with each later change of a setting taken at the frame it came in at. Anything else
 * (no second open, not level in time, a scan, a seek) is the ring. */

static int ring_only(void) { return getenv("Q2PLAYER_XF_RING") != NULL; }

static void floats(float *out, const uint8_t *data, int n, int bytes, int is_float) {
    if (is_float) memcpy(out, data, (size_t)n * 4);
    else if (bytes == 2)
        for (int i = 0; i < n; ++i) out[i] = ((const int16_t *)data)[i] * (1.0f / 32768);
    else
        for (int i = 0; i < n; ++i) out[i] = (float)(((const int32_t *)data)[i] * (1.0 / 2147483648.0));
}

static int grow(struct dsp *s, int frames);

static void reader_off(struct dsp *s, const char *why) {
    tail_close(s->rd);
    s->rd = NULL, s->rd_state = RD_OFF;
    if (why) note(why, 0);
}

/* The second open's next frames are these of the song's (1), are not (0), or are not decoded yet (-1: it has fallen
 * behind the song, which does not wait for it): read into the output's buffer, which the block has not reached yet. */
static int same(struct dsp *s, const uint8_t *data, unsigned frames) {
    size_t unit = (size_t)s->nch * s->bytes;
    if (!grow(s, (int)frames) || tail_read(s->rd, s->out, (int)frames, 0) != (int)frames) return -1;
    return !memcmp(s->out, data, frames * unit);
}
static const char RD_BEHIND[] = "q2player: crossfade: the tail held in memory, the second open fell behind the song";

/* Whether the song may still get a second open: its blocks have a place in a file that is named. */
static int reader_may(const struct dsp *s) { return s->rd_state == RD_NONE && s->frame >= 0 && s->url && !ring_only(); }

/* The second open asked for, RD_AHEAD_S past the block in hand (crossfade does, RD_LEAD_S before the hold). */
static void reader_open(struct dsp *s, unsigned frames) {
    struct pcm_format f = {s->rate, s->nch, s->bytes, s->is_float, DSD_PCM};
    s->rd = tail_open(s->url, &f, s->frame + frames + (int64_t)(RD_AHEAD_S * s->rate));
    s->rd_state = s->rd ? RD_OPEN : RD_OFF;
}

/* Before a block's stages: the second open found level when the song reaches where it began, and given up when the
 * block has no place or is not the next. */
static void reader_before(struct dsp *s, int said, const uint8_t *data, unsigned frames) {
    s->raw = data, s->rd_ahead = 0;
    if (s->rd_state == RD_OFF || s->rd_state == RD_NONE) return;
    if (s->frame < 0 || !(said & XF_MS)) { /* a scan, or no fade any more: a fade set again holds its tail in memory */
        reader_off(s, NULL);
        return;
    }
    if (s->rd_state == RD_OPEN) {
        int r = tail_ready(s->rd);
        if (r < 0) reader_off(s, NULL); /* tail.c said why */
        if (r <= 0) return;
        int64_t at = tail_at(s->rd);
        if (at < s->frame) reader_off(s, "q2player: crossfade: the tail held in memory, the second open came after the song had passed it");
        else if (at < s->frame + frames) { /* the song has reached it: the rest of this block is the same frames */
            int r = same(s, data + (size_t)(at - s->frame) * s->nch * s->bytes, (unsigned)(s->frame + frames - at));
            if (r > 0) s->rd_state = RD_LEVEL, s->rd_ahead = 1;
            else reader_off(s, r ? RD_BEHIND : "q2player: crossfade: the tail held in memory, the second open is not at the song's frames");
        }
        return;
    }
    if (tail_at(s->rd) != s->frame) { /* a seek */
        reader_off(s, NULL);
        return;
    }
}

/* After a block that no hold took from the second open: the open reads the same frames, and stays level. */
static void reader_after(struct dsp *s, const uint8_t *data, unsigned frames) {
    int r = s->rd_state == RD_LEVEL && !s->rd_ahead ? same(s, data, frames) : 1;
    if (r <= 0) reader_off(s, r ? RD_BEHIND : "q2player: crossfade: the tail held in memory, the second decode differs from the first");
    s->frame = -1;
}

/* The holder's stages (chain's: ReplayGain, Swap L/R, the EQ, Mono Audio) on n frames read back from the file. */
static void stages(float *a, unsigned n) {
    int nch = xf.nch;
    if (xf.rg != 1)
        for (unsigned i = 0; i < n * nch; ++i) {
            float x = a[i] * xf.rg;
            a[i] = x > 1 ? 1 : x < -1 ? -1 : x;
        }
    if (xf.set.swap)
        for (float *p = a, *end = a + 2 * n; p < end; p += 2) {
            float left = p[0];
            p[0] = p[1], p[1] = left;
        }
    if (xf.set.mute != xf.muted && !(xf.muted = xf.set.mute)) peq_reset(&xf.eq, xf.rate, nch, &xf.preset);
    if (!xf.set.mute) peq_process(&xf.eq, a, n);
    if (xf.set.mono)
        for (float *p = a, *end = a + 2 * n; p < end; p += 2) p[0] = p[1] = (p[0] + p[1]) * 0.5f;
}

/* The next n frames of the tail from the file into dst, as float after the holder's stages, each change of a setting
 * taken at its frame. Only frames already decoded are taken (the callers see to it that they are, or play without
 * them); frames that are not there are silence, logged. */
static void filed(float *dst, unsigned n) {
    int nch = xf.nch;
    size_t unit = (size_t)nch * xf.unit;
    if ((size_t)n * unit > xf.file_cap) {
        uint8_t *b = realloc(xf.file, (size_t)n * unit);
        if (b) xf.file = b, xf.file_cap = (size_t)n * unit;
    }
    unsigned got = (size_t)n * unit <= xf.file_cap ? (unsigned)tail_read(xf.rd, xf.file, (int)n, 0) : 0;
    if (got < n && !xf.cut && !(xf.said & 1)) xf.said |= 1, note("q2player: crossfade: the file gave %lu frames fewer than the tail held", n - got);
    floats(dst, xf.file, (int)(got * nch), xf.unit, xf.is_float);
    memset(dst + (size_t)got * nch, 0, (size_t)(n - got) * nch * sizeof *dst);
    while (n) {
        while (xf.events && xf.ev[0].at <= xf.out) {
            if (xf.ev[0].eq && peq_update(&xf.eq, &xf.ev[0].preset)) xf.preset = xf.ev[0].preset;
            xf.set = xf.ev[0].set;
            memmove(xf.ev, xf.ev + 1, --xf.events * sizeof *xf.ev);
        }
        unsigned m = xf.events && xf.ev[0].at - xf.out < n ? (unsigned)(xf.ev[0].at - xf.out) : n;
        stages(dst, m);
        dst += (size_t)m * nch, n -= m, xf.out += m;
    }
}

/* A block given to a hold that reads its tail from the file: a setting that differs from the block before's is
 * remembered for the frame this block begins at. More than XF_EVENTS waiting: the newest takes the last one's frame. */
static void told(struct dsp *s) {
    struct xf_set now = {s->mono, s->swap, s->mute};
    int eq = s->eq_gen != xf.eq_gen;
    if (!eq && !memcmp(&now, &xf.told, sizeof now)) return;
    struct xf_event *e = &xf.ev[XF_EVENTS - 1];
    if (xf.events == XF_EVENTS) {
        if (!(xf.said & 2)) xf.said |= 2, note("q2player: crossfade: settings changed more than %lu times in one hold", XF_EVENTS);
    } else e = &xf.ev[xf.events++], e->at = xf.in, e->eq = 0;
    e->set = now;
    if (eq) e->eq = 1, e->preset = s->preset;
    xf.told = now, xf.eq_gen = s->eq_gen;
}

/* The held frames lo to hi of a tail read from the file, as float after the stages: frame lo's place. */
static const float *window(unsigned lo, unsigned hi) {
    int nch = xf.nch;
    unsigned have = xf.win_lo + xf.win_n;
    if (hi >= have) {
        unsigned drop = lo < have ? lo - xf.win_lo : xf.win_n, add = hi + 1 - have;
        if (drop) memmove(xf.win, xf.win + (size_t)drop * nch, (size_t)(xf.win_n - drop) * nch * sizeof *xf.win);
        xf.win_lo += drop, xf.win_n -= drop;
        if (xf.win_n + add > xf.win_cap) {
            float *w = realloc(xf.win, (size_t)(xf.win_n + add) * nch * sizeof *w);
            if (!w) return NULL;
            xf.win = w, xf.win_cap = xf.win_n + add;
        }
        filed(xf.win + (size_t)xf.win_n * nch, add);
        xf.win_n += add;
    }
    return xf.win + (size_t)(lo - xf.win_lo) * nch;
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
    unsigned slack = XF_SLACK / (unsigned)nch, ring = XF_FLOATS / (unsigned)nch - slack; /* a ring's longest hold */
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
            tail_drop();
        else if (xf.total && rate != xf.out_rate) {
            double step = (double)xf.rate / rate;
            xf.total = (unsigned)(xf.total * xf.step / step);
            xf.left = (unsigned)((xf.len - xf.pos) / step);
            xf.step = step, xf.out_rate = rate;
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
                xf.step = (double)xf.rate / rate, xf.pos = 0, xf.out_rate = rate;
                xf.total = xf.left = (unsigned)(xf.len / xf.step);
            }
            if (!xf.total) tail_drop();
        }
        if (xf.total && xf.skip) { /* the next song's leading silence */
            unsigned d = frames < xf.skip ? frames : xf.skip;
            memmove(a, a + (size_t)d * nch, (size_t)(frames - d) * nch * 4);
            frames -= d, xf.skip -= d;
            xf.last = now, xf.mixer = s->id;
            if (!frames) return 0;
        }
        if (xf.total) {
            int hn = xf.nch; /* the tail is in the holder's channels */
            unsigned hcap = xf.cap;
            unsigned n = frames < xf.left ? frames : xf.left;
            /* a tail read from the file: the held frames this block reaches, read now (a frame over for the rounding
             * of the place as it is stepped) */
            unsigned lo = (unsigned)xf.pos, hi = (unsigned)(xf.pos + (n ? n - 1 : 0) * xf.step) + 2;
            if (hi > xf.len - 1) hi = xf.len - 1;
            if (xf.rd && n && !xf.cut) {
                /* The frames this block reaches are decoded ahead (tail.c). When they, and XF_CUT_S more, are not
                 * (the card has not answered the second open for as long as it decodes ahead), the tail fades out
                 * over the frames there are, so it ends without a step and the next song plays on untouched. */
                unsigned got = xf.win_lo + xf.win_n, need = hi + 1 > got ? hi + 1 - got : 0, ramp = (unsigned)(XF_CUT_S * xf.rate);
                unsigned ahead = need + ramp < xf.len - got ? need + ramp : xf.len - got;
                int whole, have = tail_ahead(xf.rd, (int)ahead, 0, &whole);
                if ((unsigned)have < ahead && !whole) {
                    xf.cut = 1, xf.cut_at = got + (unsigned)have;
                    xf.cut_from = xf.cut_at > lo + ramp ? xf.cut_at - ramp : lo;
                    note("q2player: crossfade: the second open fell behind under the next song, the tail faded out %lu ms early",
                         (unsigned long)((xf.len - xf.cut_at) * 1000.0 / xf.rate));
                }
            }
            const float *win = xf.rd && n ? window(lo, hi) : NULL;
            if (xf.rd && !win) n = 0; /* no memory for the window: the tail is not mixed */
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
                if (xf.cut) fo = at >= xf.cut_at ? 0 : at > xf.cut_from ? fo * (float)(xf.cut_at - at) / (float)(xf.cut_at - xf.cut_from) : fo;
                float fi = !xf.smart ? (float)in : xf.t < xf.fade ? sinf((xf.t + 0.5f) / xf.fade * 1.5707963f) : 1;
                const float *h0, *h1;
                if (win) {
                    h0 = win + (size_t)((at < hi ? at : hi) - lo) * hn, h1 = win + (size_t)((next < hi ? next : hi) - lo) * hn;
                } else {
                    if ((at += xf.head) >= hcap) at -= hcap;
                    if ((next += xf.head) >= hcap) next -= hcap;
                    h0 = xf.ring + at * hn, h1 = xf.ring + next * hn;
                }
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
                tail_drop();
            }
            xf.last = now, xf.mixer = s->id;
            return frames;
        }
    }
    int length = mine ? xf.length : s->length;
    /* a tail read from the file has no ring to fit: this song's hold may be as long as its plan asks */
    int filed_tail = mine ? xf.rd != NULL : s->rd_state == RD_OPEN || s->rd_state == RD_LEVEL || reader_may(s);
    unsigned room = filed_tail ? XF_FILED_S * (unsigned)rate : ring, plain = (unsigned)ms * (unsigned)(rate / 10) / 100;
    if (plain > room) plain = room;
    int smart = (said & XF_SMART) && ms;
    unsigned want = smart ? smart_want(s, pts, length, rate, room, ring, plain) : plain;
    int ending = want && length > 0 && pts >= 0 && pts > length - 2.0 * want / rate - XF_LEAD_S;
    mine = xf.len && xf.owner == s->id;
    /* a seek: the held audio was left behind. A tail read from the file is also left at any block that is not the
     * file's next frames (a scan, a seek of any size), as the second open reads on from where the hold began. */
    if (mine && (__builtin_fabs(pts - xf.pts) > XF_SEEK_S || (xf.rd ? s->frame != xf.next : xf.len + frames > xf.cap)))
        tail_drop(), mine = 0;
    /* A hold once begun is finished, whatever demo says now: no fade any more only stops it growing, and the next
     * song mixes what is held. Chosen over playing it out: no block runs after the file's end to carry the rest. */
    if (mine && !ms) want = xf.len;
    if (mine && !xf.rd && want > xf.cap - slack) want = xf.cap - slack; /* the ring was mapped for the hold it began as */
    if (!mine) {
        if (reader_may(s) && want && length > 0 && pts >= 0 && pts > length - 2.0 * want / rate - XF_LEAD_S - RD_LEAD_S)
            reader_open(s, frames);
        if (!ending) return frames;
        tail_drop(); /* a hold that came to nothing (its only block one frame) left its ring or its second open */
        int level = s->rd_state == RD_LEVEL && !s->rd_ahead && s->frame >= 0;
        if (!level) {
            if (s->rd_state == RD_OPEN || s->rd_state == RD_LEVEL)
                reader_off(s, "q2player: crossfade: the tail held in memory, the second open was not level when the hold began");
            if (want > ring) { /* asked of the file: asked again of the ring, whose hold begins later */
                if (smart && s->smart == SMART_PLAN) fit(s, ring);
                if (plain > ring) plain = ring;
                want = smart ? smart_want(s, pts, length, rate, ring, ring, plain) : plain;
                if (!(want && pts > length - 2.0 * want / rate - XF_LEAD_S)) return frames;
            }
            if (frames > slack || !ring_open(want, nch)) return frames;
            note("q2player: crossfade: the tail held in memory, %lu KB", (unsigned long)(xf.bytes >> 10));
        }
        xf.head = 0, xf.owner = s->id, xf.rate = rate, xf.nch = nch, xf.length = length;
        xf.stops = said >> XF_STOPS, xf.ended = 0;
        xf.smart = s->smart == SMART_PLAN && want == (unsigned)(s->plan.overlap + s->plan.trail);
        if (xf.smart) xf.trail = (unsigned)s->plan.trail, xf.plan = s->plan;
        if (level) { /* the second open and the stages as they stood before this block */
            xf.rd = s->rd, s->rd = NULL, s->rd_state = RD_OFF;
            xf.eq = s->before, xf.preset = s->preset, xf.rg = s->rg, xf.unit = s->bytes, xf.is_float = s->is_float;
            xf.set = xf.told = (struct xf_set){s->mono, s->swap, s->mute};
            xf.muted = s->was_muted, xf.in = xf.out = 0, xf.events = 0, xf.eq_gen = s->eq_gen, xf.said = 0;
            xf.win_lo = xf.win_n = 0;
            note("q2player: crossfade: the tail read from the file a second time, no ring mapped", 0);
        }
    }
    unsigned k = (frames + 1) / 2;
    if (xf.rd) { /* the block is counted and noted; what plays is the file's frames from where the hold has got to */
        if (xf.len + frames > want && xf.len + frames - want > k) k = xf.len + frames - want;
        if (k > frames) k = frames;
        /* Those frames are decoded ahead (tail.c), so this is a look, not a read of the card. With none after
         * XF_HOLD_WAIT_MS the card has not answered the second open for that long and more: the hold is left, as at
         * a seek, this block plays as it came, and the frames between are not heard. */
        int whole;
        if (tail_ahead(xf.rd, (int)k, XF_HOLD_WAIT_MS, &whole) < (int)k && !whole) {
            note("q2player: crossfade: the second open stopped giving frames, %lu ms of the song left unplayed",
                 (unsigned long)(xf.len * 1000.0 / rate));
            tail_drop();
            return frames;
        }
        told(s);
        tail_note(xf.rd, s->raw, (int)frames);
        xf.in += frames, xf.next = s->frame + frames;
        xf.len += frames;
        filed(a, k);
    } else {
        ring_copy(a, (xf.head + xf.len) % xf.cap, frames, xf.cap, nch, 1);
        xf.len += frames;
        if (xf.len > want && xf.len - want > k) k = xf.len - want;
        if (k > frames) k = frames;
        ring_copy(a, xf.head, k, xf.cap, nch, 0);
        xf.head = (xf.head + k) % xf.cap;
    }
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
    s->rg = replaygain, s->length = length, s->frame = -1;
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
    if (s && !s->pass && peq_load_active(&p) && memcmp(&p, &s->preset, sizeof p) && peq_update(&s->dsp, &p)) s->preset = p, ++s->eq_gen;
}

int dsp_tail_held(void) { return xf.len != 0; }

void dsp_path(struct dsp *s, const char *url) {
    if (s && !s->url) s->url = strdup(url);
}

void dsp_frame(struct dsp *s, int64_t frame) {
    if (s) s->frame = frame;
}

void dsp_close(struct dsp *s, int ended) {
    if (!s) return;
    if (!s->pass) leave(s);
    if (xf.len && xf.owner == s->id) xf.ended = ended;
    if (s->smart == SMART_RUN) smart_cancel(NULL); /* the song is gone before its plan */
    tail_close(s->rd); /* a second open no hold took */
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
    floats(s->buf, data, frames * s->nch, s->bytes, s->is_float);
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
        tail_drop(), xf.release = 0; /* DSD or the like plays between: the tail has nothing to mix under */
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
    reader_before(s, said, data, n);
    if (s->rd_state == RD_LEVEL && !s->rd_ahead) s->before = s->dsp, s->was_muted = cs.muted; /* a hold may begin here */
    if (!(s->rg != 1 || eq || mono || swap || fade || cast || mute || mute != cs.muted) || !grow(s, frames)) {
        s->frame = -1;
        return frames; /* nothing to change: the block as it came */
    }
    s->mono = mono, s->swap = swap, s->mute = mute;
    to_float(s, data, frames);
    float *a = s->buf;
    if (s->rg != 1) /* hciplayer's replaygain filter, MPlayer's volume filter: the scale, then a hard clip */
        for (unsigned i = 0; i < n * nch; ++i) {
            float x = a[i] * s->rg;
            a[i] = x > 1 ? 1 : x < -1 ? -1 : x;
        }
    if (cast) cast_jump(cast, s, n, rate);
    if (swap) /* Swap L/R, before the EQ: its balance and Channel: L and R bands are the ear's, as stock's Balance is */
        for (float *p = a, *end = a + 2 * n; p < end; p += 2) {
            float left = p[0];
            p[0] = p[1], p[1] = left;
        }
    if (mute != cs.muted && !(cs.muted = mute)) /* the EQ's filters start again from rest */
        peq_reset(&s->dsp, rate, nch, &s->preset);
    if (!mute) peq_process(&s->dsp, a, n);
    if (mono) /* Mono Audio (patch/crossfade.c mono_sync), after the EQ's sides */
        for (float *p = a, *end = a + 2 * n; p < end; p += 2) p[0] = p[1] = (p[0] + p[1]) * 0.5f;
    n = crossfade(s, a, n, nch, rate, said);
    reader_after(s, data, (unsigned)frames);
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
