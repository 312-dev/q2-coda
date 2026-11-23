/* The car's trace (docs/iphone-mode.md#car-trace): what way 3 hands the iPhone gadget's PCM, for a drive's log.
 *
 * While a way-3 output is open, the playback thread looks at each second's counts after a write. A second with
 * something wrong in it (the PCM ran dry, a write over SLOW_MS, an underrun) is a line of its own in the unified log
 * (plog); the quiet seconds before it are one line, as are the quiet seconds of each QUIET_MS and those left as the
 * output closes, so the lines' windows follow one another and their frames add up to what the car was handed. A
 * drive's log holds a line a minute while all is well, not one a second (the unified log's 1 MB rolled in about 70
 * minutes of driving, 2026-11-22):
 *
 *   car 1003ms: frames=44100 writes=43 wmax=23ms slow=0 dry=0/0ms back=0/0ms avail=5512 delay=16538 rmax=4ms cpu=37ms tap=176400/0 under=0
 *
 * the window's length; frames handed to the device and the writes that took them; the longest write (the wait for
 * room included) and how many took over SLOW_MS; the times the PCM ran dry (output.c sink: ALSA played silence and
 * the write moved past it) and the silence that put in, in ms of audio; the times written frames were taken back
 * unheard (output_drop's and output_close's rewind on way 3) and how much; the PCM's avail and delay as the line is
 * made; the slowest source read in the window (the decode, or a wait for a file's bytes); q2player's own CPU time in
 * the window, every thread's; the tap's bytes so far this session and bytes it could not keep; and the times, since
 * q2player started, the PCM ran dry while a song flowed into it (output.c sink: not after a pause, a seek or the
 * open), also written to CAR_UNDER as the line is made, for q2-iap's health check (patch/q2_iap.c health_poll).
 *
 * The tap, for an investigation and off unless TAP_ON exists: the first TAP_S seconds of each car session's PCM,
 * exactly as written to the device (S16, the car's rate, stereo), go to TAP_FILE. A session is one car file (output.c car_path) as the probe wrote it:
 * a new file (another inode or mtime) starts the tap again, the last two kept as TAP_FILE.1 and .2. The playback thread only copies into
 * a ring (TAP_RING bytes, about 3 s at 44.1 kHz); a thread of the session's own writes the ring to the card every
 * TAP_POLL_MS, so a slow card never holds a write to the car. What does not fit in the ring is counted (tap's second
 * number) and left out of the file, never waited for. All of this runs only on way 3, which every image's player has
 * (Q2_CAR_USB); Q2PLAYER_CAR_TAP_S stands for TAP_S in a host test, Q2PLAYER_CAR_QUIET_MS for QUIET_MS and
 * Q2PLAYER_CAR_UNDER for CAR_UNDER. */
#include "q2player.h"
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>

#define TAP_FILE PEQ_ROOT "/mnt/mmc/.q2muse/logs/car-pcm.raw"
#define TAP_ON PEQ_ROOT "/mnt/mmc/.q2muse/logs/car-pcm.on"
#define CAR_UNDER PEQ_ROOT "/tmp/q2-iap.under"
enum { TAP_S = 120, TAP_RING = 512 * 1024, TAP_POLL_MS = 100, SLOW_MS = 50, LINE_MS = 1000, QUIET_MS = 60000 };

static int64_t clock_us(clockid_t id) {
    struct timespec t;
    clock_gettime(id, &t);
    return t.tv_sec * 1000000LL + t.tv_nsec / 1000;
}

int64_t car_trace_now(void) { return clock_us(CLOCK_MONOTONIC); }

/* ---- a window's counts, the playback thread's alone: w the second in hand, q the quiet seconds since the last line */
struct window {
    int64_t frames, wmax_us, dry_frames, back_frames, rmax_us;
    int writes, slow, dry, back;
    int64_t at, cpu_at; /* when the window began, and q2player's CPU time then */
};
static struct window w, q;
static int rate = 44100; /* the device's */

/* ---- the tap: a ring of TAP_RING bytes, head and tail counting bytes put and taken since the session began */
struct tap {
    uint8_t *ring;
    size_t head, tail; /* head the playback thread's, tail the writer's */
    int fd, done;      /* done: nothing more will be put; the writer empties the ring, closes and frees */
};
static struct tap *tap;      /* the session's, while it still takes bytes */
static int64_t tap_left;     /* bytes it still takes */
static int64_t tap_put, tap_lost;
static struct stat session;  /* the car file of the session tapped */
static int tapped;

static void *tap_writer(void *arg) {
    struct tap *t = arg;
    for (;;) {
        int done = __atomic_load_n(&t->done, __ATOMIC_ACQUIRE);
        size_t head = __atomic_load_n(&t->head, __ATOMIC_ACQUIRE);
        while (t->tail < head) {
            size_t at = t->tail % TAP_RING, n = head - t->tail;
            if (n > TAP_RING - at) n = TAP_RING - at;
            ssize_t r = write(t->fd, t->ring + at, n);
            /* a card that will not take it (full, gone): the bytes are dropped rather than kept for ever */
            __atomic_store_n(&t->tail, t->tail + (r > 0 ? (size_t)r : n), __ATOMIC_RELEASE);
        }
        if (done) break;
        usleep(TAP_POLL_MS * 1000);
    }
    close(t->fd);
    free(t->ring);
    free(t);
    return NULL;
}

static void tap_end(void) {
    if (tap) __atomic_store_n(&tap->done, 1, __ATOMIC_RELEASE);
    tap = NULL, tap_left = 0;
}

/* A new session's tap: the old file unlinked first, so a writer still emptying an older ring writes to that. */
static void tap_start(int device_rate) {
    const char *secs = getenv("Q2PLAYER_CAR_TAP_S");
    int s = secs && atoi(secs) > 0 ? atoi(secs) : TAP_S;
    tap_end();
    tap_put = tap_lost = 0;
    if (access(TAP_ON, F_OK)) return;
    struct tap *t = calloc(1, sizeof *t);
    if (!t || !(t->ring = malloc(TAP_RING))) { free(t); return; }
    /* the last three sessions' taps: car-pcm.raw, then .1 and .2 (a drive's first session was lost under its second,
     * 2026-11-20) */
    rename(TAP_FILE ".1", TAP_FILE ".2");
    rename(TAP_FILE, TAP_FILE ".1");
    if ((t->fd = open(TAP_FILE, O_WRONLY | O_CREAT | O_EXCL | O_CLOEXEC, 0644)) < 0) {
        free(t->ring), free(t);
        return;
    }
    pthread_attr_t a;
    pthread_t th;
    pthread_attr_init(&a);
    pthread_attr_setdetachstate(&a, PTHREAD_CREATE_DETACHED);
    int r = pthread_create(&th, &a, tap_writer, t);
    pthread_attr_destroy(&a);
    if (r) { close(t->fd), free(t->ring), free(t); return; }
    tap = t, tap_left = (int64_t)s * device_rate * 4; /* S16 stereo */
}

void car_tap(const uint8_t *data, int64_t bytes) {
    if (!tap) return;
    if (bytes > tap_left) bytes = tap_left;
    size_t head = tap->head, room = TAP_RING - (head - __atomic_load_n(&tap->tail, __ATOMIC_ACQUIRE));
    tap_left -= bytes;
    if ((size_t)bytes > room) tap_lost += bytes; /* the writer is behind: left out, never waited for */
    else {
        size_t at = head % TAP_RING, m = (size_t)bytes < TAP_RING - at ? (size_t)bytes : TAP_RING - at;
        memcpy(tap->ring + at, data, m);
        memcpy(tap->ring, data + m, (size_t)bytes - m);
        __atomic_store_n(&tap->head, head + (size_t)bytes, __ATOMIC_RELEASE);
        tap_put += bytes;
    }
    if (!tap_left) tap_end();
}

void car_trace_open(int device_rate, const char *car_file) {
    struct stat st;
    int known = car_file && !stat(car_file, &st);
    memset(&w, 0, sizeof w);
    rate = device_rate > 0 ? device_rate : 44100;
    w.at = car_trace_now(), w.cpu_at = clock_us(CLOCK_PROCESS_CPUTIME_ID);
    q = w;
    /* the same car file as the session tapped: its tap goes on (a song after a stop, a click's PCM) */
    if (known && tapped && st.st_ino == session.st_ino && st.st_mtim.tv_sec == session.st_mtim.tv_sec &&
        st.st_mtim.tv_nsec == session.st_mtim.tv_nsec)
        return;
    if (known) session = st;
    tapped = known;
    tap_start(rate);
}

void car_trace_write(int frames, int64_t us) {
    w.frames += frames, w.writes++;
    if (us > w.wmax_us) w.wmax_us = us;
    if (us > SLOW_MS * 1000) w.slow++;
}

void car_trace_dry(int64_t frames) { w.dry++, w.dry_frames += frames; }
void car_trace_back(int64_t frames) { w.back++, w.back_frames += frames; }
static unsigned underruns, underruns_told;
void car_trace_underrun(void) { underruns++; }
void car_trace_read(int64_t us) {
    if (us > w.rmax_us) w.rmax_us = us;
}

/* The song's position as demo is told it, and the frames heard since the song's start or its last seek or move
 * (engine.c play_through: until they pass 0 the position stays where it was), as the second's line ends */
static int heard_s;
static long long heard_frames;
void car_trace_position(int position, int64_t played) { heard_s = position, heard_frames = (long long)played; }

static int quiet_ms(void) {
    const char *ms = getenv("Q2PLAYER_CAR_QUIET_MS");
    return ms && atoi(ms) > 0 ? atoi(ms) : QUIET_MS;
}

static int window_used(const struct window *x) { return x->frames || x->dry || x->back; }

/* x's line, the window ending at end with q2player's CPU time cpu then */
static void window_line(struct output *o, const struct window *x, int64_t end, int64_t cpu) {
    long avail = 0, delay = 0;
    output_levels(o, &avail, &delay);
    char line[256];
    int n = snprintf(line, sizeof line,
                     "car %lldms: frames=%lld writes=%d wmax=%lldms slow=%d dry=%d/%lldms back=%d/%lldms avail=%ld "
                     "delay=%ld rmax=%lldms cpu=%lldms tap=%lld/%lld under=%u pos=%ds played=%lld",
                     (long long)((end - x->at) / 1000), (long long)x->frames, x->writes, (long long)(x->wmax_us / 1000),
                     x->slow, x->dry, (long long)(x->dry_frames * 1000 / rate), x->back,
                     (long long)(x->back_frames * 1000 / rate), avail, delay, (long long)(x->rmax_us / 1000),
                     (long long)((cpu - x->cpu_at) / 1000), (long long)tap_put, (long long)tap_lost, underruns, heard_s,
                     heard_frames);
    plog('I', line, n < (int)sizeof line ? n : (int)sizeof line - 1);
}

/* The underrun count for q2-iap's health check, written aside and renamed: it reads the file once a second, and a
 * file truncated in place read as empty, 0, which took the count back. */
static void under_write(void) {
    const char *path = getenv("Q2PLAYER_CAR_UNDER") ? getenv("Q2PLAYER_CAR_UNDER") : CAR_UNDER;
    char tmp[256];
    snprintf(tmp, sizeof tmp, "%s.tmp", path);
    FILE *f = fopen(tmp, "w");
    if (!f) return;
    int ok = fprintf(f, "%u\n", underruns) > 0;
    if (fclose(f) || !ok || rename(tmp, path)) {
        unlink(tmp);
        return;
    }
    underruns_told = underruns;
}

void car_trace_tick(struct output *o, int closing) {
    int64_t now = car_trace_now();
    if (!closing && now - w.at < LINE_MS * 1000) return;
    int64_t cpu = clock_us(CLOCK_PROCESS_CPUTIME_ID);
    int wrong = w.dry || w.slow || underruns != underruns_told;
    if (underruns != underruns_told) under_write();
    if (wrong) { /* the quiet seconds before it, then the second itself */
        if (window_used(&q)) window_line(o, &q, w.at, w.cpu_at);
        if (window_used(&w)) window_line(o, &w, now, cpu);
        memset(&q, 0, sizeof q);
        q.at = now, q.cpu_at = cpu;
    } else {
        q.frames += w.frames, q.writes += w.writes, q.slow += w.slow, q.dry += w.dry, q.back += w.back;
        q.dry_frames += w.dry_frames, q.back_frames += w.back_frames;
        if (w.wmax_us > q.wmax_us) q.wmax_us = w.wmax_us;
        if (w.rmax_us > q.rmax_us) q.rmax_us = w.rmax_us;
        if (window_used(&q) && (closing || now - q.at >= (int64_t)quiet_ms() * 1000)) {
            window_line(o, &q, now, cpu);
            memset(&q, 0, sizeof q);
            q.at = now, q.cpu_at = cpu;
        }
    }
    memset(&w, 0, sizeof w);
    w.at = now, w.cpu_at = cpu;
}
