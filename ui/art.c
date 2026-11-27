/* Album covers for the rows of a list: the thumbnails Cover Flow keeps on the card
 * (/mnt/mmc/.coverflow/<key>.jpg, 160 px JPEGs, patch/coverflow.c art_path), found by the same key,
 * tags_key(artist, album): FNV-1a over the artist, then the album, each closed by a multiply so
 * "ab" + "c" and "a" + "bc" differ. Cover Flow names an album by the artist of one of its songs,
 * so a row asks with each of its songs' artists in turn (ART_TRIES) and takes the first file there.
 * Until Cover Flow has made it, and for an album with none, a row shows the placeholder, a plain
 * card in Raised 2 (docs/oled-design.md).
 *
 * A JPEG is read and scaled off the loop, by one worker thread that touches files and its own
 * queue alone: a list asks for the covers it draws (art_cover), newest first so the rows in sight
 * win during a spin, and the loop is woken through a pipe (ui_watch) when one is ready, which
 * redraws the screen and has the lists ask their rows again (list_pictures_changed), as it does
 * when a cover a row may hold is let go. A script (main.c --script) reads them in place instead, so a picture it
 * saves is the same on every run. Kept: the last ART_KEPT covers asked for, at twice the row's
 * 40 px for a lean's room.
 *
 * An album found with none is not asked again until Cover Flow's folder changes: demo makes the
 * thumbnails after a scan, so on a fresh card the rows are drawn before them. While one is known to
 * have none the idle worker looks at the folder's modification time every ART_LOOK_S, and when it
 * has moved, every album with none is asked again (the rows drawn again ask). ponytail: on FAT, whose
 * times are 2 s apart, a write within the window of the read that found none is seen at the next one. */
#include <fcntl.h>
#include <pthread.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>

#include "q2ui.h"

#define ART_KEPT 48
#define ART_QUEUE 12 /* asked for and not yet read; past it the oldest ask is dropped, asked again when seen */
#define ART_SIDE 80
#define PLACEHOLDER 0x2c2c2e /* Raised 2 */
#define ART_LOOK_S 2

const char *art_dir = "/mnt/mmc/.coverflow";
int art_sync;

enum { EMPTY, ASKED, HAVE, NONE };
static struct {
    unsigned key, keys[ART_TRIES];
    int n, state;
    unsigned used; /* the ask's turn: the least recent goes when the room is needed */
    image_t *im;
} kept[ART_KEPT];
static unsigned turn;
static pthread_mutex_t lock = PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t asked = PTHREAD_COND_INITIALIZER;
static int queue[ART_QUEUE], queued; /* kept[] slots, the newest last */
static int done[ART_KEPT], ndone;    /* read by the worker, for the loop to take */
static int wake[2] = { -1, -1 }, started;
static int recheck;   /* the folder moved: the loop has the rows ask again */
static struct timespec seen_at; /* the folder's time when a slot was last found to have none */

static int any_none(void) { /* under the lock */
    for (int i = 0; i < ART_KEPT; ++i)
        if (kept[i].state == NONE) return 1;
    return 0;
}
static struct timespec folder_time(void) {
    struct stat st;
    struct timespec none = { 0, 0 };
    if (stat(art_dir, &st)) return none;
#ifdef __APPLE__
    return st.st_mtimespec;
#else
    return st.st_mtim;
#endif
}

static unsigned fnv(unsigned h, const char *s) {
    for (; s && *s; ++s) h = (h ^ (unsigned char)*s) * 16777619u;
    return h * 16777619u;
}
unsigned art_key(const char *artist, const char *album) { return fnv(fnv(2166136261u, artist), album); }

/* The first of keys whose file is there, read and scaled; 0 for none. */
static image_t *read_cover(const unsigned *keys, int n) {
    char path[300];
    for (int k = 0; k < n; ++k) {
        snprintf(path, sizeof path, "%s/%08x.jpg", art_dir, keys[k]);
        if (access(path, R_OK)) continue;
        image_t *full = image_load(path);
        image_t *fit = full ? image_fit(full, ART_SIDE, ART_SIDE) : 0;
        image_free(full);
        if (fit) return fit;
    }
    return 0;
}

static void *worker(void *arg) {
    (void)arg;
    for (;;) {
        pthread_mutex_lock(&lock);
        while (!queued) {
            if (!any_none()) {
                pthread_cond_wait(&asked, &lock);
                continue;
            }
            /* albums with none: the folder looked at every ART_LOOK_S while nothing is asked */
            struct timespec until;
            clock_gettime(CLOCK_REALTIME, &until);
            until.tv_sec += ART_LOOK_S;
            if (pthread_cond_timedwait(&asked, &lock, &until) == 0 || queued) continue;
            struct timespec t = folder_time();
            if (t.tv_sec == seen_at.tv_sec && t.tv_nsec == seen_at.tv_nsec) continue;
            for (int i = 0; i < ART_KEPT; ++i)
                if (kept[i].state == NONE) kept[i].state = EMPTY;
            recheck = 1;
            char c = 1;
            if (write(wake[1], &c, 1) < 0) {}
        }
        int slot = queue[--queued];
        unsigned keys[ART_TRIES], key = kept[slot].key;
        int n = kept[slot].n;
        memcpy(keys, kept[slot].keys, sizeof keys);
        pthread_mutex_unlock(&lock);
        struct timespec t = folder_time(); /* before the read: a write after it moves the time on */
        image_t *im = read_cover(keys, n);
        pthread_mutex_lock(&lock);
        if (kept[slot].key == key && kept[slot].state == ASKED) { /* still the same ask */
            kept[slot].im = im, kept[slot].state = im ? HAVE : NONE;
            if (!im) seen_at = t;
            im = 0;
            done[ndone++] = slot;
        }
        pthread_mutex_unlock(&lock);
        image_free(im);
        char c = 1;
        if (write(wake[1], &c, 1) < 0) {} /* the pipe full: the loop is to read it anyway */
    }
    return 0;
}

/* the worker's covers are in: the screen again, as they may be anywhere on it */
static void arrived(void) {
    char buf[64];
    while (read(wake[0], buf, sizeof buf) > 0) {}
    pthread_mutex_lock(&lock);
    int any = ndone || recheck;
    ndone = recheck = 0;
    pthread_mutex_unlock(&lock);
    if (any) list_pictures_changed(), ui_mark_all();
}

static const image_t *placeholder(void) {
    static image_t *im;
    if (!im) {
        im = image_new(ART_SIDE, ART_SIDE);
        for (int i = 0; i < ART_SIDE * ART_SIDE; ++i) im->px[i] = 0xff000000u | PLACEHOLDER;
    }
    return im;
}

/* The worker and its pipe, once; with neither, covers are read in place. */
static void start(void) {
    started = 1;
    pthread_t t;
    if (pipe(wake)) {
        art_sync = 1;
        return;
    }
    for (int k = 0; k < 2; ++k) fcntl(wake[k], F_SETFL, O_NONBLOCK), fcntl(wake[k], F_SETFD, FD_CLOEXEC);
    if (pthread_create(&t, 0, worker, 0)) {
        art_sync = 1;
        return;
    }
    pthread_detach(t);
    ui_watch(wake[0], arrived);
}

const image_t *art_cover(const unsigned *keys, int n) {
    if (n <= 0) return placeholder();
    n = n > ART_TRIES ? ART_TRIES : n;
    if (!started && !art_sync) start();
    pthread_mutex_lock(&lock);
    int at = -1, oldest = 0;
    for (int i = 0; i < ART_KEPT; ++i) {
        if (kept[i].state != EMPTY && kept[i].key == keys[0]) at = i;
        if (kept[i].used < kept[oldest].used) oldest = i;
    }
    if (at >= 0) { /* read, being read, or known to have none */
        kept[at].used = ++turn;
        const image_t *im = kept[at].state == HAVE ? kept[at].im : 0;
        pthread_mutex_unlock(&lock);
        return im ? im : placeholder();
    }
    /* a new ask in the least recent slot; one the worker has in hand finds its slot changed and
     * drops what it read */
    at = oldest;
    for (int q = 0; q < queued; ++q)
        if (queue[q] == at) memmove(queue + q, queue + q + 1, (size_t)(--queued - q) * sizeof *queue);
    if (kept[at].im) image_free(kept[at].im), list_pictures_changed(); /* a row may still hold it */
    kept[at].im = 0, kept[at].key = keys[0], kept[at].n = n, kept[at].used = ++turn, kept[at].state = ASKED;
    memcpy(kept[at].keys, keys, (size_t)n * sizeof *keys);
    if (art_sync) {
        image_t *im = read_cover(keys, n);
        kept[at].im = im, kept[at].state = im ? HAVE : NONE;
        pthread_mutex_unlock(&lock);
        return im ? im : placeholder();
    }
    if (queued == ART_QUEUE) { /* the oldest ask dropped: asked again when its row is drawn */
        kept[queue[0]].state = EMPTY;
        memmove(queue, queue + 1, (size_t)--queued * sizeof *queue);
    }
    queue[queued++] = at;
    pthread_cond_signal(&asked);
    pthread_mutex_unlock(&lock);
    return placeholder();
}
