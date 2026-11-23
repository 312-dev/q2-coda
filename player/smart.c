/* Smart Crossfade's analysis (docs/internals.md#smart-crossfade): where a song's ending is worth overlapping and how
 * the next one begins, from both songs' loudness, on a thread of its own so the playback thread never waits on it.
 *
 * The playing song is opened a second time and its last SR.tail_s seconds decoded to a loudness envelope: the RMS of
 * SR.window_ms windows, over every channel, in dB of full scale, after its ReplayGain scale and hard clip as the chain
 * applies them (dsp.c chain). Its windows are counted back from the file's last frame, so the plan is in frames from
 * the song's end, which the hold in dsp.c also counts from: an inexact seek moves nothing. The tail is decoded twice,
 * once to find that last frame and once to measure, as the last frame is only known at the end and the envelope is
 * cheaper to make again than the frames are to keep (15 MB at 192 kHz). The next song, from XFADE_NEXT, is decoded
 * from its start for SR.head_s seconds, its windows counted from its first frame.
 *
 * Rules (SR, one table to tune them; body is a song's median window over what was decoded):
 *   tail_end   the end of the last window above max(SR.floor_db, body - SR.tail_below); after it, dropped
 *   out_point  the start of the final run of windows below body - SR.quiet_below that ends at tail_end, when that run
 *              is SR.run_min_ms to SR.run_max_ms long; else tail_end - SR.fallback_ms
 *   skip       the next song's leading windows below SR.floor_db, at most SR.skip_max_ms
 *   fade       the next song's fade-in: none when its start (its loudest window in SR.start_ms after the skip) is
 *              within SR.hard_within of its body, a hard start; else SR.fade_min_ms, more for a quieter start, at most
 *              SR.fade_max_ms (SR.fade_span dB further down)
 * Anything that cannot be measured is SMART_PLAIN, with one log line saying why: either song a stream or still arriving
 * short of what is needed, the next one missing or unnamed, a decode that fails. Deterministic: the same files give
 * the same plan, every time. */
#include "q2player.h"
#include "peq.h"
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>
#include <sys/resource.h>
#include <sys/syscall.h>

static const struct smart_rules {
    int window_ms, tail_s, head_s;
    double floor_db, tail_below, quiet_below, hard_within, fade_span;
    int run_min_ms, run_max_ms, fallback_ms, skip_max_ms, start_ms, fade_min_ms, fade_max_ms;
} SR = {
    .window_ms = 50, .tail_s = 20, .head_s = 15,
    .floor_db = -60, .tail_below = 45, .quiet_below = 12, .hard_within = 6, .fade_span = 14,
    .run_min_ms = 1000, .run_max_ms = 15000, .fallback_ms = 5000, .skip_max_ms = 5000, .start_ms = 200,
    .fade_min_ms = 300, .fade_max_ms = 1000,
};
#define SILENT_DB (-200.0) /* a window of digital silence */
#define SMART_NICE 10     /* the analysis thread's nice: the playback thread before it */
#define MAX_WINDOWS 512    /* the most of either span: SR.tail_s or SR.head_s at SR.window_ms */

struct job {
    char cur[URL_MAX], next[URL_MAX];
    float rg;
    int gen;
};
static pthread_mutex_t mu = PTHREAD_MUTEX_INITIALIZER;
static int gen, state;
static struct xf_plan result;

static void say(const char *fmt, const char *a, const char *b) {
    char line[256];
    int n = snprintf(line, sizeof line, fmt, a, b);
    plog('I', line, n < (int)sizeof line ? n : (int)sizeof line - 1);
}

static int stale(const struct job *j) { return __atomic_load_n(&gen, __ATOMIC_RELAXED) != j->gen; }

/* A sample of the source's format as float, scaled and clipped as the chain's ReplayGain stage does. */
static float sample(const uint8_t *data, const struct pcm_format *f, size_t i, float rg) {
    float x = f->is_float ? ((const float *)data)[i]
              : f->bytes == 2 ? ((const int16_t *)data)[i] * (1.0f / 32768)
                              : (float)(((const int32_t *)data)[i] * (1.0 / 2147483648.0));
    if (rg != 1) {
        x *= rg;
        x = x > 1 ? 1 : x < -1 ? -1 : x;
    }
    return x;
}

static double db(double power, int64_t samples) {
    return power > 0 && samples > 0 ? 10 * log10(power / (double)samples) : SILENT_DB;
}

/* The playing song's last windows, newest first into env (at most MAX_WINDOWS), counted back from its last frame;
 * their count, below 0 when it would not open, seek or decode. */
static int tail_envelope(const struct job *j, double *env, int *rate) {
    struct song s = {0};
    int error, n = -1;
    snprintf(s.url, sizeof s.url, "%s", j->cur);
    struct source *src = source_open(&s, &error, NULL);
    if (!src) return -1;
    const struct pcm_format *f = source_format(src);
    struct info info;
    source_info(src, &info);
    int64_t w = (int64_t)f->rate * SR.window_ms / 1000, want = (int64_t)SR.tail_s * 1000 / SR.window_ms;
    int from = info.length - SR.tail_s - 2;
    if (from < 0) from = 0;
    *rate = f->rate;
    if (f->dsd || w <= 0 || source_seek(src, from) < 0) goto done;
    int64_t start = source_frames(src), end = start;
    const uint8_t *data;
    int k;
    while ((k = source_read(src, &data)) > 0 && !stale(j)) end = source_frames(src); /* the last frame */
    if (k < 0 || stale(j) || source_seek(src, from) < 0) goto done;
    int64_t span = (end - start) / w;
    if (span > want) span = want;
    if (span > MAX_WINDOWS) span = MAX_WINDOWS;
    double power[MAX_WINDOWS] = {0};
    int64_t first = end - span * w; /* the oldest window's first frame */
    while ((k = source_read(src, &data)) > 0 && !stale(j)) {
        int64_t at = source_frames(src) - k; /* this block's first frame */
        for (int i = 0; i < k; ++i) {
            if (at + i < first || at + i >= end) continue;
            int64_t back = (end - 1 - (at + i)) / w; /* 0: the newest window */
            double sum = 0;
            for (int c = 0; c < f->channels; ++c) {
                float x = sample(data, f, (size_t)i * f->channels + c, j->rg);
                sum += (double)x * x;
            }
            power[back] += sum;
        }
    }
    if (k < 0 || stale(j)) goto done;
    for (int64_t i = 0; i < span; ++i) env[i] = db(power[i], w * f->channels);
    n = (int)span;
done:
    source_close(src);
    return n;
}

/* The next song's first windows, oldest first into env; their count, below 0 as tail_envelope's. */
static int head_envelope(const struct job *j, double *env, int *rate) {
    struct song s = {0};
    int error, n = -1;
    snprintf(s.url, sizeof s.url, "%s", j->next);
    struct source *src = source_open(&s, &error, NULL);
    if (!src) return -1;
    const struct pcm_format *f = source_format(src);
    int64_t w = (int64_t)f->rate * SR.window_ms / 1000, want = (int64_t)SR.head_s * 1000 / SR.window_ms;
    pthread_mutex_lock(&ctl.mu);
    int mode = ctl.replaygain;
    pthread_mutex_unlock(&ctl.mu);
    float rg = source_replaygain(src, mode);
    double power[MAX_WINDOWS] = {0};
    int64_t got = 0;
    const uint8_t *data;
    int k = 0;
    *rate = f->rate;
    if (f->dsd || w <= 0 || want > MAX_WINDOWS) goto done;
    while (got < want * w && (k = source_read(src, &data)) > 0 && !stale(j)) {
        for (int i = 0; i < k && got < want * w; ++i, ++got) {
            double sum = 0;
            for (int c = 0; c < f->channels; ++c) {
                float x = sample(data, f, (size_t)i * f->channels + c, rg);
                sum += (double)x * x;
            }
            power[got / w] += sum;
        }
    }
    if (k < 0 || stale(j)) goto done;
    n = (int)(got / w);
    for (int i = 0; i < n; ++i) env[i] = db(power[i], w * f->channels);
done:
    source_close(src);
    return n;
}

static int by_value(const void *a, const void *b) {
    double x = *(const double *)a, y = *(const double *)b;
    return x < y ? -1 : x > y;
}
/* The lower median of n windows */
static double body(const double *env, int n) {
    double v[MAX_WINDOWS];
    memcpy(v, env, sizeof *v * (size_t)n);
    qsort(v, (size_t)n, sizeof *v, by_value);
    return v[(n - 1) / 2];
}

/* Whether path is a whole file on the card, or arriving with enough of its start there; why not, into why. */
static int on_card(const char *path, int whole, const char **why) {
    int64_t have, total, ms;
    if (!path[0]) return *why = "not known", 0;
    if (stream_is_url(path)) return *why = "a stream", 0;
    if (access(path, R_OK)) return *why = "not on the card", 0;
    if (!grow_have(path, &have, &total, &ms)) return 1;
    if (whole || total <= 0 || ms <= 0) return *why = "still downloading", 0;
    /* its first SR.head_s seconds and a second more, at its average rate, and what comes before the audio */
    int64_t need = total * (SR.head_s + 1) * 1000 / ms + 65536;
    if (have < need && have < total) return *why = "still downloading", 0;
    return 1;
}

/* a ends with b or b with a: the same file, named from another root */
static int same_song(const char *a, const char *b) {
    size_t m = strlen(a), n = strlen(b);
    return m && n && (m >= n ? !strcmp(a + m - n, b) : !strcmp(b + n - m, a));
}

/* The plan for j into p; 1, or 0 with why (n bytes). */
static int plan(const struct job *j, struct xf_plan *p, char *why, size_t n) {
    double tail[MAX_WINDOWS], head[MAX_WINDOWS];
    const char *where;
    int ra, rb;
    if (!on_card(j->cur, 1, &where)) return snprintf(why, n, "the playing song is %s", where), 0;
    if (!on_card(j->next, 0, &where)) return snprintf(why, n, "the next song is %s", where), 0;
    int nt = tail_envelope(j, tail, &ra);
    if (nt <= 0) return snprintf(why, n, "the playing song's tail would not decode"), 0;
    int nh = head_envelope(j, head, &rb);
    if (nh <= 0) return snprintf(why, n, "the next song would not decode"), 0;
    int64_t wa = (int64_t)ra * SR.window_ms / 1000, wb = (int64_t)rb * SR.window_ms / 1000;
    double ba = body(tail, nt), bb = body(head, nh);
    double loud = ba - SR.tail_below > SR.floor_db ? ba - SR.tail_below : SR.floor_db;
    int te = 0; /* tail_end, in windows back from the end */
    while (te < nt && tail[te] <= loud) ++te;
    if (te == nt) return snprintf(why, n, "no sound in the playing song's last seconds"), 0;
    int run = te;
    while (run < nt && tail[run] < ba - SR.quiet_below) ++run;
    int64_t quiet = (int64_t)(run - te) * SR.window_ms;
    p->trail = te * wa;
    p->overlap = quiet >= SR.run_min_ms && quiet <= SR.run_max_ms && run < nt ? (run - te) * wa
                                                                               : (int64_t)ra * SR.fallback_ms / 1000;
    int skip = 0;
    while (skip < nh && skip * SR.window_ms < SR.skip_max_ms && head[skip] < SR.floor_db) ++skip;
    double start = SILENT_DB;
    for (int i = skip; i < nh && (i - skip) * SR.window_ms < SR.start_ms; ++i)
        if (head[i] > start) start = head[i];
    double gap = bb - SR.hard_within - start; /* dB below a hard start */
    int fade_ms = 0;
    if (gap > 0) {
        double more = gap < SR.fade_span ? gap / SR.fade_span : 1;
        fade_ms = SR.fade_min_ms + (int)((SR.fade_max_ms - SR.fade_min_ms) * more);
    }
    p->skip = skip * wb;
    p->fade = (int64_t)rb * fade_ms / 1000;
    p->next_rate = rb;
    snprintf(p->next, sizeof p->next, "%s", j->next);
    char line[256];
    int k = snprintf(line, sizeof line,
                     "q2player: smart crossfade: body %.1f dB, overlap %.2f s from %.2f s before the end, %.2f s of tail "
                     "dropped; next body %.1f dB, %.2f s skipped, start %.1f dB, fade-in %d ms",
                     ba, (double)p->overlap / ra, (double)(p->overlap + p->trail) / ra, (double)p->trail / ra, bb,
                     (double)p->skip / rb, start, fade_ms);
    plog('I', line, k < (int)sizeof line ? k : (int)sizeof line - 1);
    return 1;
}

static void *work(void *arg) {
    struct job *j = arg;
    /* below the playback thread: on a busy Q2 the analysis waits, the song does not (docs/internals.md#smart-crossfade) */
    if (setpriority(PRIO_PROCESS, (id_t)syscall(SYS_gettid), SMART_NICE)) { /* the analysis runs at the player's own */
    }
    struct xf_plan p;
    char why[160] = "";
    struct timespec t0, t1;
    clock_gettime(CLOCK_THREAD_CPUTIME_ID, &t0);
    /* Q2PLAYER_SMART_DELAY_MS: test/player/run.py's slow analysis, to see the plain fade take over */
    for (long wait = getenv("Q2PLAYER_SMART_DELAY_MS") ? atol(getenv("Q2PLAYER_SMART_DELAY_MS")) : 0; wait > 0 && !stale(j);
         wait -= 10)
        usleep(10000);
    /* XFADE_NEXT: the playing song's path, then the next one's */
    char *text = calloc(1, 2 * URL_MAX + 4);
    FILE *f = fopen(XFADE_NEXT, "rb");
    size_t n = f && text ? fread(text, 1, 2 * URL_MAX + 2, f) : 0;
    if (f) fclose(f);
    char *nl = text ? memchr(text, '\n', n) : NULL;
    if (nl) {
        *nl = 0;
        snprintf(j->next, sizeof j->next, "%.*s", (int)strcspn(nl + 1, "\n"), nl + 1);
    }
    int ok = 0;
    if (!nl) snprintf(why, sizeof why, "no next song named");
    else if (!same_song(text, j->cur)) snprintf(why, sizeof why, "the next song named follows another");
    else ok = plan(j, &p, why, sizeof why);
    free(text);
    clock_gettime(CLOCK_THREAD_CPUTIME_ID, &t1);
    pthread_mutex_lock(&mu);
    if (!stale(j)) {
        if (ok) result = p;
        state = ok ? SMART_PLAN : SMART_PLAIN;
        if (!ok) say("q2player: smart crossfade: plain, %s%s", why, "");
        char line[96];
        int k = snprintf(line, sizeof line, "q2player: smart crossfade: analysis took %ld ms of CPU",
                         (long)((t1.tv_sec - t0.tv_sec) * 1000 + (t1.tv_nsec - t0.tv_nsec) / 1000000));
        plog('I', line, k);
    }
    pthread_mutex_unlock(&mu);
    free(j);
    return NULL;
}

void smart_begin(const char *url, float rg) {
    struct job *j = calloc(1, sizeof *j);
    pthread_attr_t a;
    pthread_t t;
    if (!j) return;
    pthread_mutex_lock(&mu);
    j->gen = __atomic_add_fetch(&gen, 1, __ATOMIC_RELAXED);
    state = SMART_RUN;
    pthread_mutex_unlock(&mu);
    snprintf(j->cur, sizeof j->cur, "%s", url);
    j->rg = rg;
    pthread_attr_init(&a);
    pthread_attr_setdetachstate(&a, PTHREAD_CREATE_DETACHED);
    if (pthread_create(&t, &a, work, j)) {
        free(j);
        smart_cancel("no thread for the analysis");
        pthread_mutex_lock(&mu);
        state = SMART_PLAIN;
        pthread_mutex_unlock(&mu);
    }
    pthread_attr_destroy(&a);
}

int smart_poll(struct xf_plan *p) {
    pthread_mutex_lock(&mu);
    int s = state;
    if (s == SMART_PLAN) *p = result;
    pthread_mutex_unlock(&mu);
    return s;
}

void smart_cancel(const char *why) {
    pthread_mutex_lock(&mu);
    __atomic_add_fetch(&gen, 1, __ATOMIC_RELAXED);
    state = SMART_NONE;
    pthread_mutex_unlock(&mu);
    if (why) say("q2player: smart crossfade: plain, %s%s", why, "");
}
