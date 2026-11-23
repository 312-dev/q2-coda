/* Other sources' sound for the car (docs/iphone-mode.md#other-sources-over-usb): Radio, Internet Radio and Videos play
 * through q2video, which while Car Audio takes the audio plays to ALSA's q2car device (tools/build.py CAR_FEED_CONF: a
 * plug at 44.1 kHz, 16-bit stereo, into a file PCM that writes raw frames to FEED_FILE, a FIFO). The player is the car
 * card's one owner: this thread reads the FIFO into a ring of FEED_RING frames, and the engine, while its own song is
 * stopped and the car's way is the output, writes the ring's frames to the car's PCM (engine.c feed_session), silence
 * when it runs dry, so the car's stream never stops and its rules (output.c) live in one place.
 *
 * The FIFO is made and held open here from the player's start, for reading and for writing, so a source's open never
 * waits for a reader and the read never sees an end. The ring is read from only as the car takes its frames, so the
 * source is paced by the car through the pipe; when nothing has taken a frame for FEED_IDLE_MS (the car not reading,
 * the player's own song playing), what comes is read and dropped at the source's own rate, so a source never blocks
 * for good and nothing grows. Between a source's write and the car: the FIFO's FEED_PIPE bytes, the ring's FEED_RING
 * frames and what the engine writes ahead in the car's PCM, FEED_LEAD_MS (engine.c), under 0.3 s in all, which a
 * video's picture leads by (q2video's clock is the q2car device's, which holds nothing). The reader sleeps in poll() until a source writes: it never wakes on its own.
 * Q2PLAYER_FEED moves the FIFO for the host tests. */
#include <errno.h>
#include <fcntl.h>
#include <poll.h>
#include <pthread.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>

#include "q2player.h"

#define FEED_FILE PEQ_ROOT "/tmp/q2car.feed"
#define FEED_RING (FEED_RATE / 10) /* frames: 100 ms */
#define FEED_PIPE 4096          /* the FIFO's bytes, 23 ms: what the source runs ahead by (F_SETPIPE_SZ) */
#define FEED_IDLE_MS 300
#define FRAME 4 /* 16-bit stereo */

static pthread_mutex_t mu = PTHREAD_MUTEX_INITIALIZER;
static uint8_t ring[FEED_RING * FRAME], part[FRAME];
static unsigned head, count, nparts; /* frames */
static int64_t pulled_at, heard_at;

static int64_t mono_ms(void) {
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return (int64_t)t.tv_sec * 1000 + t.tv_nsec / 1000000;
}

/* bytes from the FIFO into the ring, whole frames, a frame cut by a read kept for the next */
static void put(const uint8_t *b, unsigned n) {
    pthread_mutex_lock(&mu);
    int was_empty = !count;
    if (was_empty) pulled_at = mono_ms(); /* a start: FEED_IDLE_MS for the engine to begin taking it */
    while (n) {
        if (nparts || n < FRAME) {
            unsigned k = FRAME - nparts < n ? FRAME - nparts : n;
            memcpy(part + nparts, b, k), nparts += k, b += k, n -= k;
            if (nparts < FRAME) break;
            nparts = 0;
            if (count < FEED_RING) memcpy(ring + (head + count++) % FEED_RING * FRAME, part, FRAME);
            continue;
        }
        if (count < FEED_RING) memcpy(ring + (head + count++) % FEED_RING * FRAME, b, FRAME);
        b += FRAME, n -= FRAME;
    }
    heard_at = mono_ms();
    pthread_mutex_unlock(&mu);
    if (was_empty) { /* the engine waits on its own wake for a song or this */
        pthread_mutex_lock(&ctl.mu);
        pthread_cond_broadcast(&ctl.wake);
        pthread_mutex_unlock(&ctl.mu);
    }
}

static void *reader(void *arg) {
    int fd = (int)(intptr_t)arg;
    uint8_t b[4096];
    for (;;) {
        /* no timeout: the write end held here means no end of file, so the thread sleeps until a source writes */
        struct pollfd p = {fd, POLLIN, 0};
        if (poll(&p, 1, -1) <= 0) continue;
        pthread_mutex_lock(&mu);
        unsigned room = FEED_RING - count;
        int64_t idle = mono_ms() - pulled_at;
        pthread_mutex_unlock(&mu);
        if (room >= sizeof b / FRAME) {
            ssize_t r = read(fd, b, sizeof b);
            if (r > 0) put(b, (unsigned)r);
        } else if (idle >= FEED_IDLE_MS) {
            /* nobody takes the frames: dropped at the source's rate (each read waited out for as long as it plays),
             * so the source is neither held nor sped up */
            ssize_t r = read(fd, b, sizeof b);
            if (r > 0) {
                pthread_mutex_lock(&mu);
                heard_at = mono_ms();
                pthread_mutex_unlock(&mu);
                usleep((useconds_t)((int64_t)r / FRAME * 1000000 / FEED_RATE));
            }
        } else {
            usleep(5000); /* the ring is full and the car is taking it: wait for room */
        }
    }
    return NULL;
}

void feed_start(void) {
    const char *path = getenv("Q2PLAYER_FEED") ? getenv("Q2PLAYER_FEED") : FEED_FILE;
    struct stat st;
    if (!stat(path, &st) && !S_ISFIFO(st.st_mode)) unlink(path);
    if (mkfifo(path, 0666) && errno != EEXIST) return;
    chmod(path, 0666);
    int fd = open(path, O_RDONLY | O_NONBLOCK | O_CLOEXEC);
    if (fd < 0) return;
    if (open(path, O_WRONLY | O_NONBLOCK | O_CLOEXEC) < 0) { /* held, so the read never sees an end */
        close(fd);
        return;
    }
    fcntl(fd, F_SETPIPE_SZ, FEED_PIPE); /* the source this little ahead of the ring, for a video's lips */
    pthread_t t;
    if (pthread_create(&t, NULL, reader, (void *)(intptr_t)fd)) close(fd);
    else pthread_detach(t);
}

int feed_frames(void) {
    pthread_mutex_lock(&mu);
    int n = (int)count;
    pthread_mutex_unlock(&mu);
    return n;
}

int feed_take(uint8_t *out, int frames) {
    pthread_mutex_lock(&mu);
    int n = frames < (int)count ? frames : (int)count;
    for (int i = 0; i < n; ++i) memcpy(out + i * FRAME, ring + (head + i) % FEED_RING * FRAME, FRAME);
    head = (head + (unsigned)n) % FEED_RING, count -= (unsigned)n;
    pulled_at = mono_ms();
    pthread_mutex_unlock(&mu);
    return n;
}

void feed_pulling(void) {
    pthread_mutex_lock(&mu);
    pulled_at = mono_ms();
    pthread_mutex_unlock(&mu);
}

int64_t feed_heard_ms(void) {
    pthread_mutex_lock(&mu);
    int64_t t = heard_at;
    pthread_mutex_unlock(&mu);
    return t ? mono_ms() - t : -1;
}
