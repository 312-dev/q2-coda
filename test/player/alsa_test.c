/* player/output.c built with HAVE_ALSA against this file's libasound, which records what it is asked: the software
 * parameters an open sets and what a write does on a PCM that ran dry. run.py's test_alsa_sw_params reads one line
 * per case. The model's buffer and period are what the Q2's DAC gives at 44.1 kHz (22050 frames in 16 periods of
 * 1378, docs/internals.md#bluetooth-resampler) and its boundary is alsa-lib's for that buffer on a 32-bit long. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "alsa_min.h"
#include "q2player.h"

enum { RING = 22050, PERIOD = 1378 };
#define BOUNDARY 0x56220000ul /* RING doubled while it stays under LONG_MAX - RING on MIPS o32 */

struct _snd_pcm { int dummy; };
struct _snd_pcm_hw_params { int dummy; };
struct _snd_pcm_sw_params { snd_pcm_uframes_t start, stop, silence, boundary; };

static struct _snd_pcm the_pcm;
static struct _snd_pcm_sw_params applied; /* what snd_pcm_sw_params was given */
static int sw_calls, avail_calls, writes;
static long avail_now; /* what snd_pcm_avail answers */
static long forwarded = -1;
static unsigned rate = 44100;

int snd_pcm_open(snd_pcm_t **p, const char *name, int stream, int mode) { *p = &the_pcm; return 0; }
int snd_pcm_close(snd_pcm_t *p) { return 0; }
int snd_pcm_hw_params_malloc(snd_pcm_hw_params_t **p) { *p = calloc(1, sizeof **p); return *p ? 0 : -12; }
void snd_pcm_hw_params_free(snd_pcm_hw_params_t *p) { free(p); }
int snd_pcm_hw_params_any(snd_pcm_t *p, snd_pcm_hw_params_t *h) { return 0; }
int snd_pcm_hw_params_set_access(snd_pcm_t *p, snd_pcm_hw_params_t *h, int a) { return 0; }
int snd_pcm_hw_params_test_format(snd_pcm_t *p, snd_pcm_hw_params_t *h, int f) { return 0; }
int snd_pcm_hw_params_set_format(snd_pcm_t *p, snd_pcm_hw_params_t *h, int f) { return 0; }
int snd_pcm_hw_params_set_channels(snd_pcm_t *p, snd_pcm_hw_params_t *h, unsigned c) { return 0; }
int snd_pcm_hw_params_set_rate_resample(snd_pcm_t *p, snd_pcm_hw_params_t *h, unsigned v) { return 0; }
int snd_pcm_hw_params_set_rate_near(snd_pcm_t *p, snd_pcm_hw_params_t *h, unsigned *v, int *d) { *v = rate; return 0; }
int snd_pcm_hw_params_set_buffer_time_near(snd_pcm_t *p, snd_pcm_hw_params_t *h, unsigned *v, int *d) { return 0; }
int snd_pcm_hw_params_set_periods_near(snd_pcm_t *p, snd_pcm_hw_params_t *h, unsigned *v, int *d) { return 0; }
int snd_pcm_hw_params(snd_pcm_t *p, snd_pcm_hw_params_t *h) { return 0; }
int snd_pcm_get_params(snd_pcm_t *p, snd_pcm_uframes_t *b, snd_pcm_uframes_t *s) { *b = RING, *s = PERIOD; return 0; }
/* alsa-lib's defaults after hw_params (pcm.c, _snd_pcm_hw_params_internal) */
int snd_pcm_sw_params_malloc(snd_pcm_sw_params_t **p) { *p = calloc(1, sizeof **p); return *p ? 0 : -12; }
void snd_pcm_sw_params_free(snd_pcm_sw_params_t *p) { free(p); }
int snd_pcm_sw_params_current(snd_pcm_t *p, snd_pcm_sw_params_t *s) {
    s->start = 1, s->stop = RING, s->silence = 0, s->boundary = BOUNDARY;
    return 0;
}
int snd_pcm_sw_params_get_boundary(const snd_pcm_sw_params_t *s, snd_pcm_uframes_t *v) { *v = s->boundary; return 0; }
int snd_pcm_sw_params_set_start_threshold(snd_pcm_t *p, snd_pcm_sw_params_t *s, snd_pcm_uframes_t v) { s->start = v; return 0; }
int snd_pcm_sw_params_set_stop_threshold(snd_pcm_t *p, snd_pcm_sw_params_t *s, snd_pcm_uframes_t v) { s->stop = v; return 0; }
int snd_pcm_sw_params_set_silence_size(snd_pcm_t *p, snd_pcm_sw_params_t *s, snd_pcm_uframes_t v) { s->silence = v; return 0; }
int snd_pcm_sw_params(snd_pcm_t *p, snd_pcm_sw_params_t *s) { applied = *s, sw_calls++; return 0; }
int snd_pcm_prepare(snd_pcm_t *p) { return 0; }
snd_pcm_sframes_t snd_pcm_writei(snd_pcm_t *p, const void *b, snd_pcm_uframes_t n) { writes++; return (snd_pcm_sframes_t)n; }
int snd_pcm_delay(snd_pcm_t *p, snd_pcm_sframes_t *d) { *d = 0; return 0; }
snd_pcm_sframes_t snd_pcm_avail(snd_pcm_t *p) { avail_calls++; return avail_now; }
snd_pcm_sframes_t snd_pcm_forward(snd_pcm_t *p, snd_pcm_uframes_t n) { forwarded = (long)n; return (snd_pcm_sframes_t)n; }
snd_pcm_sframes_t snd_pcm_rewindable(snd_pcm_t *p) { return 0; }
snd_pcm_sframes_t snd_pcm_rewind(snd_pcm_t *p, snd_pcm_uframes_t n) { return 0; }
int snd_pcm_pause(snd_pcm_t *p, int on) { return 0; }
int snd_pcm_drop(snd_pcm_t *p) { return 0; }
int snd_pcm_drain(snd_pcm_t *p) { return 0; }
const char *snd_strerror(int e) { return "error"; }

/* One write of 100 frames with avail at the given value on output way `way`: forwarded (or -1), avail reads, writes. */
static void write_case(const char *name, int way, long avail) {
    struct pcm_format f = {0};
    f.rate = 44100, f.channels = 2, f.bytes = 2;
    static int16_t silence[200];
    struct output *o = output_open(&f, way, 17);
    if (!o) { printf("%s open-failed\n", name); return; }
    avail_now = avail, forwarded = -1, avail_calls = writes = 0;
    int r = output_write(o, (const uint8_t *)silence, 100);
    printf("%s r %d forward %ld avail_reads %d writes %d\n", name, r, forwarded, avail_calls, writes);
    output_close(o);
}

int main(void) {
    unsetenv("Q2PLAYER_PCM");
    setenv("Q2PLAYER_DEVICE", "test", 1);
    for (int way = 0; way < 3; ++way) {
        struct pcm_format f = {0};
        f.rate = 44100, f.channels = 2, f.bytes = 2;
        rate = way == 1 ? 48000 : 44100;
        sw_calls = 0, memset(&applied, 0, sizeof applied);
        struct output *o = output_open(&f, way, 17);
        printf("open way %d calls %d start %lu stop %lu silence %lu boundary %lu\n", way, sw_calls, applied.start, applied.stop,
               applied.silence, applied.boundary);
        output_close(o);
    }
    rate = 44100;
    write_case("full", 0, 0);                 /* nothing free: no forward */
    write_case("ring", 0, RING);              /* ran exactly dry: hw_ptr at appl_ptr, nothing behind */
    write_case("dry", 0, RING + 5000);        /* hw_ptr 5000 past appl_ptr */
    write_case("dry-usb", 2, RING + 44100);   /* a second past, on a USB DAC */
    rate = 48000;
    write_case("dry-bt", 1, RING + 5000);     /* Bluetooth: not looked at */
    return 0;
}
