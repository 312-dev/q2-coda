/* The output tap (docs/usb-audio.md#the-tap): what the player hands the device, streamed to a reader of TAP_FIFO,
 * which `vamp audio` runs on the Q2 over adb (`cat`), so the laptop hears, bit for bit, what the DAC is given; and
 * the same stream to the USB audio sink (uac.c), which plays it into the gadget's sound card.
 *
 * Only where TAP_GATE is there (q2adb-gadget, which --dev builds alone have) does pcm_tap_start make the consumers'
 * threads; elsewhere every call below returns at its first test. Each consumer has a ring of PCM_TAP_RING bytes and says
 * when it wants frames (active): the FIFO's thread once a reader has the FIFO open (an open for writing without
 * blocking is refused, ENXIO, while nobody reads; looked at every TAP_LOOK_MS), the sink while the gadget's card is
 * open. The playback thread copies each write into each active ring, no system call and no lock (one producer, one
 * consumer, head and tail as in cartrace.c's tap). A consumer that does not keep up fills its ring: what does not fit
 * is dropped and counted, never waited for. A reader gone (EPIPE) closes the FIFO, and the thread looks again.
 *
 * The stream is records, each an 8-byte header, a tag and the payload's length (little-endian, 32 bits), then the
 * payload:
 *   Q2TF  the format of the data after it: rate (u32), channels (u16), bytes a sample (u8), kind (u8: 0 integer,
 *         1 float, 2 DoP, 3 native DSD). First in every session, and again at each change.
 *   Q2TD  frames, exactly as written to the device.
 *   Q2TB  frames (u32) taken back unheard: a rewind (a click mixed in, output_click) or a drop of what the device
 *         held; the frames after it replace them.
 *   Q2TL  frames (u32) dropped because the ring was full, at this point in the stream.
 * A reader that applies each Q2TB to what it holds back (tools/q2tap.py Tap) has the device's own stream. */
#include "q2player.h"
#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#define TAP_GATE PEQ_ROOT "/usr/bin/q2adb-gadget"
#define TAP_FIFO PEQ_ROOT "/tmp/q2tap.pcm"
enum { TAP_LOOK_MS = 500, TAP_IDLE_MS = 10 };

static struct tap_ring fifo_ring, uac_ring;

static size_t room(struct tap_ring *r) { return PCM_TAP_RING - (r->head - __atomic_load_n(&r->tail, __ATOMIC_ACQUIRE)); }

/* n bytes at ring position *at, which moves on; head is left alone until the whole record is in */
static void put_bytes(struct tap_ring *r, size_t *at, const void *p, size_t n) {
    size_t i = *at % PCM_TAP_RING, m = n < PCM_TAP_RING - i ? n : PCM_TAP_RING - i;
    memcpy(r->buf + i, p, m);
    memcpy(r->buf, (const uint8_t *)p + m, n - m);
    *at += n;
}

/* One record, whole or not at all; committed by moving head once its bytes are in. */
static int record(struct tap_ring *r, const char tag[4], const void *a, size_t an) {
    if (room(r) < PCM_TAP_HEAD + an) return -1;
    uint32_t len = (uint32_t)an;
    size_t at = r->head;
    put_bytes(r, &at, tag, 4), put_bytes(r, &at, &len, 4), put_bytes(r, &at, a, an);
    __atomic_store_n(&r->head, at, __ATOMIC_RELEASE);
    return 0;
}

/* What must go before more frames: a new session's or a changed format, then any count still owed. */
static int owed(struct tap_ring *r, const struct pcm_format *f) {
    int s = __atomic_load_n(&r->session, __ATOMIC_ACQUIRE);
    if (s != r->seen) r->seen = s, r->lost = r->back = 0, r->sent.rate = 0;
    const struct pcm_format *t = &r->sent;
    if (f && (t->rate != f->rate || t->channels != f->channels || t->bytes != f->bytes || t->is_float != f->is_float ||
              t->dsd != f->dsd)) {
        uint8_t p[8];
        uint32_t rate = (uint32_t)f->rate;
        uint16_t ch = (uint16_t)f->channels;
        memcpy(p, &rate, 4), memcpy(p + 4, &ch, 2);
        p[6] = (uint8_t)f->bytes, p[7] = (uint8_t)(f->dsd == DSD_DOP ? 2 : f->dsd ? 3 : f->is_float ? 1 : 0);
        if (record(r, "Q2TF", p, 8)) return -1;
        r->sent = *f;
    }
    if (r->back && !record(r, "Q2TB", &r->back, 4)) r->back = 0;
    if (r->lost && !record(r, "Q2TL", &r->lost, 4)) r->lost = 0;
    return r->back || r->lost ? -1 : 0;
}

static int wants(struct tap_ring *r) { return r->buf && __atomic_load_n(&r->active, __ATOMIC_ACQUIRE); }

static void put(struct tap_ring *r, const struct pcm_format *f, const uint8_t *data, int frames) {
    if (!wants(r)) return;
    if (owed(r, f) || record(r, "Q2TD", data, (size_t)frames * f->channels * f->bytes)) r->lost += (uint32_t)frames;
}

static void back(struct tap_ring *r, int64_t frames) {
    if (!wants(r) || r->seen != __atomic_load_n(&r->session, __ATOMIC_ACQUIRE)) return; /* nothing of this session yet */
    /* frames lost since the last data are in the stream after it, so a take-back first spends them */
    if (r->lost >= frames) { r->lost -= (uint32_t)frames; return; }
    frames -= r->lost, r->lost = 0;
    r->back += (uint32_t)frames;
    owed(r, NULL);
}

void pcm_tap_put(const struct pcm_format *f, const uint8_t *data, int frames) {
    if (frames <= 0) return;
    put(&fifo_ring, f, data, frames);
    put(&uac_ring, f, data, frames);
}

void pcm_tap_back(int64_t frames) {
    if (frames <= 0) return;
    back(&fifo_ring, frames);
    back(&uac_ring, frames);
}

/* A consumer's start of a session: what an earlier one left unsent skipped, the new session told to the playback
 * thread, which begins it with a format. */
void tap_ring_open(struct tap_ring *r) {
    __atomic_store_n(&r->tail, __atomic_load_n(&r->head, __ATOMIC_ACQUIRE), __ATOMIC_RELEASE);
    __atomic_store_n(&r->session, r->session + 1, __ATOMIC_RELEASE);
    __atomic_store_n(&r->active, 1, __ATOMIC_RELEASE);
}

void tap_ring_close(struct tap_ring *r) { __atomic_store_n(&r->active, 0, __ATOMIC_RELEASE); }

static void *fifo_thread(void *arg) {
    struct tap_ring *r = arg;
    for (;;) {
        int fd = open(TAP_FIFO, O_WRONLY | O_NONBLOCK | O_CLOEXEC);
        if (fd < 0) { usleep(TAP_LOOK_MS * 1000); continue; }
        fcntl(fd, F_SETFL, fcntl(fd, F_GETFL) & ~O_NONBLOCK); /* this thread may wait on the reader; nothing else does */
        tap_ring_open(r);
        for (;;) {
            size_t h = __atomic_load_n(&r->head, __ATOMIC_ACQUIRE), t = r->tail;
            if (t == h) { usleep(TAP_IDLE_MS * 1000); continue; }
            size_t at = t % PCM_TAP_RING, n = h - t;
            if (n > PCM_TAP_RING - at) n = PCM_TAP_RING - at;
            ssize_t w = write(fd, r->buf + at, n);
            if (w < 0 && errno == EINTR) continue;
            if (w <= 0) break; /* the reader gone */
            __atomic_store_n(&r->tail, t + (size_t)w, __ATOMIC_RELEASE);
        }
        tap_ring_close(r);
        close(fd);
    }
    return NULL;
}

static void thread(void *(*fn)(void *), struct tap_ring *r) {
    if (!(r->buf = malloc(PCM_TAP_RING))) return;
    r->seen = -1;
    pthread_t th;
    pthread_attr_t a;
    pthread_attr_init(&a);
    pthread_attr_setdetachstate(&a, PTHREAD_CREATE_DETACHED);
    if (pthread_create(&th, &a, fn, r)) free(r->buf), r->buf = NULL;
    pthread_attr_destroy(&a);
}

void pcm_tap_start(void) {
    if (access(TAP_GATE, F_OK)) return;
    thread(fifo_thread, &fifo_ring);
    thread(uac_thread, &uac_ring);
}
