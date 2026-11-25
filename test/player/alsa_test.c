/* player/output.c built with HAVE_ALSA against this file's libasound, which records what it is asked: the software
 * parameters an open sets, what a write does on a PCM that ran dry and on one bluealsa took away, and which opens
 * read alsa-lib's configuration afresh. run.py's
 * test_alsa_sw_params reads one line per case. The model's buffer and period are what the Q2's DAC gives at 44.1 kHz (22050 frames in 16 periods of
 * 1378, docs/internals.md#bluetooth-resampler) and its boundary is alsa-lib's for that buffer on a 32-bit long. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <math.h>
#include <time.h>
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

/* bluealsa's PCM going away under a stream (a SelectCodec: docs/internals.md#bluetooth-link-monitor): the next
 * write_fails writes answer gone_error, the plugin's -ENODEV (or -EPIPE, with prepare then -ENODEV), the next
 * open_fails opens find no PCM, and a reopen is given reopen_rate. */
static int opens, open_fails, open_err = -19, write_fails, gone_error, prepare_fails, sleeps; /* open_err: what a failed open answers */
static unsigned reopen_rate;
static long last_written = -1;
static int lr_far, peak; /* the last write's largest difference between left and right, and its peak (16-bit stereo) */
/* snd_config_update_free_global: the next open reads alsa-lib's configuration afresh (fresh counts those opens),
 * so a /mnt/data/asound.conf demo rewrote since the last one names the device (docs/player.md#output). */
static int freed, fresh;
static char opened[128]; /* the device the last open named */
/* A USB DAC of its own kind (run.py test_alsa_usb): the rates it takes (none listed: `rate` alone, as above), and
 * whether it takes S16_LE and stereo alone, as a cheap USB-C dongle does (the AB13X of 2026-11-22: 48 kHz S16 stereo). */
static unsigned dev_rates[8];
static int dev_nrates, dev_s16, dev_stereo, set_rate_asked;
int snd_config_update_free_global(void) { freed = 1; return 0; }
int usleep(useconds_t us) { sleeps++; return 0; }
/* main.c's, for cartrace.c and output.c: on stderr, which run.py's test_car_write_slow reads */
void plog(char level, const char *msg, int n) { fprintf(stderr, "plog %c %.*s\n", level, n, msg); }
static void nap(int ms) { struct timespec t = {ms / 1000, ms % 1000 * 1000000L}; nanosleep(&t, NULL); } /* real time */
int snd_pcm_open(snd_pcm_t **p, const char *name, int stream, int mode) {
    fresh += freed, freed = 0;
    snprintf(opened, sizeof opened, "%s", name);
    if (opens++ && reopen_rate) rate = reopen_rate;
    if (open_fails && open_fails--) return open_err;
    *p = &the_pcm;
    return 0;
}
int snd_pcm_close(snd_pcm_t *p) { return 0; }
int snd_pcm_hw_params_malloc(snd_pcm_hw_params_t **p) { *p = calloc(1, sizeof **p); return *p ? 0 : -12; }
void snd_pcm_hw_params_free(snd_pcm_hw_params_t *p) { free(p); }
int snd_pcm_hw_params_any(snd_pcm_t *p, snd_pcm_hw_params_t *h) { return 0; }
int snd_pcm_hw_params_set_access(snd_pcm_t *p, snd_pcm_hw_params_t *h, int a) { return 0; }
int snd_pcm_hw_params_test_format(snd_pcm_t *p, snd_pcm_hw_params_t *h, int f) { return dev_s16 && f != SND_PCM_FORMAT_S16_LE ? -22 : 0; }
int snd_pcm_hw_params_set_format(snd_pcm_t *p, snd_pcm_hw_params_t *h, int f) { return snd_pcm_hw_params_test_format(p, h, f); }
int snd_pcm_hw_params_test_channels(snd_pcm_t *p, snd_pcm_hw_params_t *h, unsigned c) { return dev_stereo && c != 2 ? -22 : 0; }
int snd_pcm_hw_params_set_channels(snd_pcm_t *p, snd_pcm_hw_params_t *h, unsigned c) { return snd_pcm_hw_params_test_channels(p, h, c); }
int snd_pcm_hw_params_test_rate(snd_pcm_t *p, snd_pcm_hw_params_t *h, unsigned v, int d) {
    if (!dev_nrates) return v == rate ? 0 : -22;
    for (int i = 0; i < dev_nrates; ++i)
        if (dev_rates[i] == v) return 0;
    return -22;
}
int snd_pcm_hw_params_set_rate_resample(snd_pcm_t *p, snd_pcm_hw_params_t *h, unsigned v) { return 0; }
int snd_pcm_hw_params_set_rate_near(snd_pcm_t *p, snd_pcm_hw_params_t *h, unsigned *v, int *d) {
    set_rate_asked = (int)*v;
    if (!dev_nrates) { *v = rate; return 0; }
    unsigned best = dev_rates[0];
    for (int i = 1; i < dev_nrates; ++i)
        if (labs((long)dev_rates[i] - (long)*v) < labs((long)best - (long)*v)) best = dev_rates[i];
    *v = best;
    return 0;
}
int snd_pcm_hw_params_set_buffer_time_near(snd_pcm_t *p, snd_pcm_hw_params_t *h, unsigned *v, int *d) { return 0; }
int snd_pcm_hw_params_set_periods_near(snd_pcm_t *p, snd_pcm_hw_params_t *h, unsigned *v, int *d) { return 0; }
/* busy_hw: bluealsa's plugin claims its PCM at hw_params, and answers -EBUSY while another client holds it, or
 * while the server has not yet seen the last client's close (run.py test_bt_open_waits_out_a_busy_pcm) */
static int busy_hw, hw_tries;
int snd_pcm_hw_params(snd_pcm_t *p, snd_pcm_hw_params_t *h) {
    hw_tries++;
    return busy_hw && busy_hw-- ? -16 : 0;
}
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
static int prepares, drops, pauses, pause_err; /* pause_err: what snd_pcm_pause answers (the car's gadget: -EINVAL) */
static long delay_now, first_written;        /* what snd_pcm_delay answers; the last write's first sample */
/* A ring that counts (run.py test_click_idle_alsa): what is written stays in it (no time passes), snd_pcm_delay is
 * that plus link_frames, which bluealsa's plugin adds for the headset's own delay whether the PCM has started or not
 * (libasound_module_pcm_bluealsa.so 0x5b64: its Delay, 2800 tenths of a ms on the Pixel Buds), and snd_pcm_avail the
 * ring's room; loudest is the largest sample written while it counts. */
static int counting;
static long queued, link_frames, loudest;
int snd_pcm_prepare(snd_pcm_t *p) { prepares++; return prepare_fails && prepare_fails-- ? -19 : 0; }
static int last_zero; /* the last write all silence */
/* A car that stopped reading (StopUSBDeviceModeAudio, 2026-11-23) with room left in its ring for `room` frames (stall
 * set): the kernel's blocking write of more than that writes the room and then waits for more, which never comes,
 * until its timeout (5.10's pcm_lib.c wait_for_avail: 10 s), when it answers the frames it wrote. Here the wait is
 * block_ms of real time, and counted in blocked. write_nap_ms: every write takes that long, a slow device. */
static int stall, room, blocked, block_ms, write_nap_ms, wait_nap_ms;
snd_pcm_sframes_t snd_pcm_writei(snd_pcm_t *p, const void *b, snd_pcm_uframes_t n) {
    writes++;
    if (write_nap_ms) nap(write_nap_ms);
    if (stall && (long)n > room) {
        blocked++, nap(block_ms);
        n = (snd_pcm_uframes_t)room;
        if (!n) return -5; /* -EIO: nothing written before the timeout */
    }
    if (stall) room -= (int)n;
    if (write_fails && write_fails--) return gone_error;
    last_written = (long)n, first_written = ((const int16_t *)b)[0];
    lr_far = peak = 0;
    for (snd_pcm_uframes_t i = 0; i < n; ++i) {
        int l = ((const int16_t *)b)[2 * i], r = ((const int16_t *)b)[2 * i + 1];
        if (abs(l - r) > lr_far) lr_far = abs(l - r);
        if (abs(l) > peak) peak = abs(l);
    }
    last_zero = 1;
    for (snd_pcm_uframes_t i = 0; i < n * 2; ++i) last_zero &= !((const int16_t *)b)[i];
    if (counting) queued += (long)n, loudest = peak > loudest ? peak : loudest;
    return (snd_pcm_sframes_t)n;
}
int snd_pcm_delay(snd_pcm_t *p, snd_pcm_sframes_t *d) { *d = counting ? queued + link_frames : delay_now; return 0; }
snd_pcm_sframes_t snd_pcm_avail(snd_pcm_t *p) { avail_calls++; return counting ? RING - queued : stall ? room : avail_now; }
int snd_pcm_state(snd_pcm_t *p) { return 3; } /* SND_PCM_STATE_RUNNING */
snd_pcm_sframes_t snd_pcm_forward(snd_pcm_t *p, snd_pcm_uframes_t n) { forwarded = (long)n; return (snd_pcm_sframes_t)n; }
snd_pcm_sframes_t snd_pcm_rewindable(snd_pcm_t *p) { return 0; }
snd_pcm_sframes_t snd_pcm_rewind(snd_pcm_t *p, snd_pcm_uframes_t n) { return 0; }
/* The car way (way 3) waits on it. Room at once, unless wait_busy waits are to find none first (a car that has asked
 * for audio and not begun to read: the BMW began 2011 ms after it asked, 2026-11-20); at wait_unlink the car file is
 * removed (q2-iap: the car stopped, the cable came out), at wait_cut the engine's cut flag is set (a stop or a skip). */
static int wait_busy, waits, wait_unlink = -1, wait_cut = -1, waited_ms;
static volatile int cut;
static const char *car_file;
int snd_pcm_wait(snd_pcm_t *p, int ms) {
    ++waits, waited_ms += ms;
    if (wait_nap_ms) nap(wait_nap_ms);
    if (waits == wait_unlink) unlink(car_file);
    if (waits == wait_cut) cut = 1;
    return stall ? room > 0 : waits > wait_busy;
}
int snd_pcm_pause(snd_pcm_t *p, int on) { pauses++; return pause_err; }
int snd_pcm_drop(snd_pcm_t *p) { drops++, queued = 0; return 0; }
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

/* One write of 100 frames of a 44.1 kHz song on output way `way` whose PCM goes away (the first write answers err,
 * and prepare `prep` times -ENODEV), with `absent` opens finding no PCM after it and a reopen at reopen_hz, then a
 * second write: both results, the opens, the writes, the waits, and the frames the last write handed the device. */
static void gone_case(const char *name, int way, int err, int prep, int absent, unsigned reopen_hz) {
    struct pcm_format f = {0};
    f.rate = 44100, f.channels = 2, f.bytes = 2;
    static int16_t silence[200];
    rate = way == 1 ? 48000 : 44100;
    opens = sleeps = writes = 0, last_written = -1;
    struct output *o = output_open(&f, way, 17);
    if (!o) { printf("%s open-failed\n", name); return; }
    write_fails = 1, gone_error = err, prepare_fails = prep, open_fails = absent, reopen_rate = reopen_hz;
    int r = output_write(o, (const uint8_t *)silence, 100), r2 = output_write(o, (const uint8_t *)silence, 100);
    printf("%s r %d %d opens %d writes %d sleeps %d last %ld\n", name, r, r2, opens, writes, sleeps, last_written);
    output_close(o);
    write_fails = prepare_fails = open_fails = 0, reopen_rate = 0;
}

/* One write of 100 frames on way 3 to a car that finds no room for `busy` waits: its result, the waits and their ms,
 * the writes, and whether the car file is still there. */
static void car_case(const char *name, int busy, int unlink_at, int cut_at) {
    struct pcm_format f = {0};
    f.rate = 44100, f.channels = 2, f.bytes = 2;
    static int16_t silence[200];
    FILE *c = fopen(car_file, "w");
    if (c) fputs("hw:CARD=UAC1Gadget,0\n44100\n", c), fclose(c);
    rate = 44100;
    struct output *o = output_open(&f, 3, 17);
    if (!o) { printf("%s open-failed\n", name); return; }
    output_car_watch(&cut);
    waits = waited_ms = writes = 0, cut = 0, wait_busy = busy, wait_unlink = unlink_at, wait_cut = cut_at, avail_now = 0;
    int r = output_write(o, (const uint8_t *)silence, 100);
    printf("%s r %d waits %d ms %d writes %d file %d\n", name, r, waits, waited_ms, writes, access(car_file, 0) == 0);
    wait_busy = 0, wait_unlink = wait_cut = -1;
    output_close(o);
    output_car_watch(0);
}

/* One write of 100 frames on way 3, timed in real time: to a car that stopped reading with room for `left` frames
 * (stall) and a stop at the first wait; or with every wait or every write taking nap_ms (slow: 'w' or 'r'). Its
 * result, the kernel waits the write met (blocked), the writes, the ms it took. */
static void car_slow_case(const char *name, int left, char slow, int nap_ms, int busy) {
    struct pcm_format f = {0};
    f.rate = 44100, f.channels = 2, f.bytes = 2;
    static int16_t silence[200];
    FILE *c = fopen(car_file, "w");
    if (c) fputs("hw:CARD=UAC1Gadget,0\n44100\n", c), fclose(c);
    rate = 44100;
    struct output *o = output_open(&f, 3, 17);
    if (!o) { printf("%s open-failed\n", name); return; }
    output_car_watch(&cut);
    waits = writes = blocked = 0, cut = 0, avail_now = RING / 2, wait_busy = busy, wait_unlink = -1;
    stall = left >= 0, room = left, block_ms = 1200, wait_cut = stall ? 1 : -1;
    wait_nap_ms = slow == 'w' ? nap_ms : 0, write_nap_ms = slow == 'r' ? nap_ms : 0;
    int64_t t = car_trace_now();
    int r = output_write(o, (const uint8_t *)silence, 100);
    printf("%s r %d blocked %d writes %d ms %lld\n", name, r, blocked, writes, (long long)((car_trace_now() - t) / 1000));
    stall = wait_nap_ms = write_nap_ms = wait_busy = 0, wait_cut = -1, avail_now = 0;
    output_close(o);
    output_car_watch(0);
}

/* The 48 kHz-only dongle (usb_case's) on way 2 whose first `fails` writes answer err, then two writes of 100
 * frames; or, with err 0, an open that answers open_e. The results (the open's 1 or 0, the writes'), the writes and
 * prepares: what run.py's test_usb_errors reads beside the stderr lines output.c says. */
static void usb_err_case(const char *name, int err, int fails, int open_e) {
    struct pcm_format f = {0};
    f.rate = 44100, f.channels = 2, f.bytes = 2;
    static int16_t silence[200];
    dev_rates[0] = 48000, dev_nrates = 1, dev_s16 = dev_stereo = 1;
    writes = prepares = 0, avail_now = 0, open_fails = open_e ? 1 : 0, open_err = open_e;
    fprintf(stderr, "case %s\n", name);
    struct output *o = output_open(&f, 2, 17);
    write_fails = fails, gone_error = err;
    int r1 = o ? output_write(o, (const uint8_t *)silence, 100) : 0, r2 = o ? output_write(o, (const uint8_t *)silence, 100) : 0;
    printf("%s open %d r %d %d writes %d prepares %d\n", name, o != NULL, r1, r2, writes, prepares);
    if (o) output_close(o);
    write_fails = 0, open_fails = 0, open_err = -19, dev_nrates = dev_s16 = dev_stereo = 0;
}

/* A pause and its resume on way 3 with the device answering err to snd_pcm_pause (the car's gadget advertises a pause
 * its trigger cannot do: -EINVAL), 60 of the 100 frames written not yet heard: the pauses asked, the drops and
 * prepares, then at the resume the frames written again and the first of them (the tone's frame 40 is 1040). */
static void pause_case(const char *name, int err) {
    struct pcm_format f = {0};
    f.rate = 44100, f.channels = 2, f.bytes = 2;
    static int16_t tone[200];
    for (int i = 0; i < 100; ++i) tone[2 * i] = tone[2 * i + 1] = (int16_t)(1000 + i);
    FILE *c = fopen(car_file, "w");
    if (c) fputs("hw:CARD=UAC1Gadget,0\n44100\n", c), fclose(c);
    rate = 44100, wait_busy = 0, waits = 0, avail_now = 0;
    struct output *o = output_open(&f, 3, 17);
    if (!o) { printf("%s open-failed\n", name); return; }
    output_write(o, (const uint8_t *)tone, 100);
    pauses = drops = prepares = 0, pause_err = err, delay_now = 60;
    output_pause(o, 1);
    int paused_drops = drops, paused_prepares = prepares;
    delay_now = 0, last_written = -1, first_written = 0, writes = 0;
    output_pause(o, 0);
    printf("%s pauses %d drops %d prepares %d resume writes %d last %ld first %ld\n", name, pauses, paused_drops,
           paused_prepares, writes, last_written, first_written);
    pause_err = 0, delay_now = 0;
    output_close(o);
}

/* A USB DAC that takes 48 kHz S16_LE stereo alone (dev_*), and a song of rate, channels and width on way 2: the
 * rate it asked set_rate_near for, the writes, the frames two writes of 4410 handed the device, and the last write's
 * largest difference between its left and right and its peak: the tone (-12 dBFS, 8192) is the same in both, a mono
 * song's too once made stereo, at its own level, so the difference is the dither's, a step or two. */
static void usb_case(const char *name, int song_rate, int ch, int bytes) {
    struct pcm_format f = {0};
    f.rate = song_rate, f.channels = ch, f.bytes = bytes;
    static int32_t tone[4410 * 2];
    for (int i = 0; i < 4410 * ch; ++i) {
        double x = 0.25 * sin(2 * M_PI * 1000 * (i / ch) / song_rate);
        if (bytes == 2) ((int16_t *)tone)[i] = (int16_t)(x * 32767);
        else tone[i] = (int32_t)(x * 2147483647.0);
    }
    dev_rates[0] = 48000, dev_nrates = 1, dev_s16 = dev_stereo = 1;
    writes = 0, last_written = -1, avail_now = 0, set_rate_asked = 0;
    struct output *o = output_open(&f, 2, 17);
    if (!o) {
        printf("%s open-failed\n", name);
    } else {
        long sum = 0;
        for (int i = 0; i < 2; ++i) output_write(o, (const uint8_t *)tone, 4410), sum += last_written > 0 ? last_written : 0;
        printf("%s asked %d writes %d frames %ld lr %d peak %d\n", name, set_rate_asked, writes, sum, lr_far, peak);
        output_close(o);
    }
    dev_nrates = dev_s16 = dev_stereo = 0;
}

/* A click with nothing playing on `way` (engine.c click_session): a PCM opened for it at 48 kHz, or a paused song's
 * taken (output_hold, after 2000 frames of it written; paused 2: opened for the click with nothing written, as a
 * song paused on Bluetooth has its PCM opened again after BT_PAUSE_MS closed it), with link frames of delay after the
 * ring; the click and ten of the session's fills, no time passing. The frames in the ring and the loudest sample:
 * IDLE_AHEAD_MS of silence (3840 frames at 48 kHz) with the click in it. */
static void idle_case(const char *name, int way, long link, int paused) {
    struct pcm_format f = {0};
    f.rate = 48000, f.channels = 2, f.bytes = 2;
    static int16_t song[4000];
    for (int i = 0; i < 4000; ++i) song[i] = 0;
    rate = 48000, avail_now = 0;
    struct output *o = output_open(&f, way, 17);
    if (!o) { printf("%s open-failed\n", name); return; }
    counting = 1, queued = loudest = 0, link_frames = link;
    if (paused == 1) output_write(o, (const uint8_t *)song, 2000), output_pause(o, 1);
    if (paused) output_hold(o);
    output_idle_click(o, CLICK_WHEEL);
    for (int i = 0; i < 10; ++i) output_idle_fill(o);
    printf("%s queued %ld loudest %ld\n", name, queued, loudest);
    output_idle_end(o);
    counting = 0, queued = link_frames = 0;
    output_close(o);
}

int main(int argc, char **argv) {
    unsetenv("Q2PLAYER_PCM");
    setenv("Q2PLAYER_DEVICE", "test", 1);
    car_file = getenv("Q2PLAYER_CAR");
    if (argc > 1 && !strcmp(argv[1], "usb-errors")) { /* run.py test_usb_errors */
        usb_err_case("usb-xrun", -32, 1, 0);      /* an underrun on the first write: prepared and written again */
        usb_err_case("usb-suspend", -86, 1, 0);   /* -ESTRPIPE, the same */
        usb_err_case("usb-eio", -5, 1, 0);        /* -EIO: the song's failure, not the device gone */
        usb_err_case("usb-gone-open", 0, 0, -2);  /* the open finds no card: gone */
        usb_err_case("usb-gone-write", -19, 2, 0); /* opened again, then -ENODEV under two writes: gone, said once */
        return 0;
    }
    if (argc > 1 && !strcmp(argv[1], "busy")) { /* run.py test_bt_open_waits_out_a_busy_pcm: usleep counted */
        struct pcm_format f = {0};
        f.rate = 48000, f.channels = 2, f.bytes = 2;
        for (int n = 0; n < 3; ++n) {
            static const int busy[] = { 0, 6, 1000 }; /* free; busy for 6 tries; held for good */
            busy_hw = busy[n], hw_tries = 0;
            sleeps = 0;
            struct output *o = output_open(&f, 1, 0);
            printf("busy%d %s tries %d sleeps %d\n", busy[n], o ? "opened" : "failed", hw_tries, sleeps);
            if (o) output_close(o);
        }
        output_busy_wait(200); /* a click's open (engine.c CLICK_BUSY_MS): another source holding the PCM */
        busy_hw = 1000, hw_tries = 0, sleeps = 0;
        struct output *o = output_open(&f, 1, 0);
        printf("click %s tries %d sleeps %d\n", o ? "opened" : "failed", hw_tries, sleeps);
        if (o) output_close(o);
        output_busy_wait(-1);
        busy_hw = 0;
        return 0;
    }
    if (argc > 1 && !strcmp(argv[1], "idle")) { /* run.py test_click_idle_alsa */
        idle_case("bt-stopped", 1, 13440, 0);  /* the Pixel Buds: 280 ms of Delay at 48 kHz */
        idle_case("bt-paused", 1, 13440, 1);
        idle_case("bt-reopened", 1, 13440, 2); /* a paused song's PCM opened again for the click */
        idle_case("dac-stopped", 0, 0, 0);     /* the headphone DAC: nothing after the ring */
        idle_case("dac-paused", 0, 0, 1);
        return 0;
    }
    if (argc > 1 && !strcmp(argv[1], "car-slow") && car_file) { /* run.py test_car_write_slow, seconds of real time */
        car_slow_case("car-stall", 50, 0, 0, 0);     /* the car stopped, room for 50 frames, a stop at the first wait */
        car_slow_case("car-slow-wait", -1, 'w', 250, 5); /* five empty looks, then the one with room: 250 ms each */
        car_slow_case("car-slow-write", -1, 'r', 1100, 0); /* one write of 1.1 s */
        car_slow_case("car-quick", -1, 0, 0, 0);     /* none: no line */
        return 0;
    }
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
    /* way 3: a car that asked for audio and reads it only 2 s later is waited for, not left (car run 2026-11-20); the
     * car file gone while waiting ends the car's way; a stop or a skip ends the wait with the frames dropped */
    car_file = getenv("Q2PLAYER_CAR");
    if (car_file) {
        car_case("car-late", 8, -1, -1);
        car_case("car-late3", 12, -1, -1);           /* 3009 ms in the BMW, the second drive (2026-11-20) */
        car_case("car-left", 1000, 3, -1);
        car_case("car-cut", 1000, -1, 3);
        pause_case("car-pause", 0);          /* a device that pauses: nothing more */
        pause_case("car-pause-fails", -22);  /* the gadget's -EINVAL: held, stopped, written again at the resume */
        /* way 3's close, whatever played (a song, or another source through feed.c: engine.c feed_session): its last
         * write is silence, so the car hears silence up to the stop */
        struct pcm_format f = {0};
        f.rate = FEED_RATE, f.channels = 2, f.bytes = 2;
        static int16_t tone[200];
        for (int i = 0; i < 200; ++i) tone[i] = (int16_t)(1000 + i);
        FILE *c = fopen(car_file, "w");
        if (c) fputs("hw:CARD=UAC1Gadget,0\n44100\n", c), fclose(c);
        wait_busy = 0, waits = 0;
        struct output *o = output_open(&f, 3, 17);
        if (o) {
            output_write(o, (const uint8_t *)tone, 100);
            output_close(o);
            printf("car-close last %ld zero %d\n", last_written, last_zero);
        }
    }
    avail_now = 0;
    gone_case("bt-gone", 1, -19, 0, 3, 0);         /* SelectCodec: the new PCM is there after three looks */
    gone_case("bt-gone-xrun", 1, -32, 1, 0, 0);    /* seen first as an underrun, the prepare then -ENODEV */
    gone_case("bt-gone-44k", 1, -19, 0, 0, 44100); /* the new transport at 44.1 kHz: the song unresampled */
    gone_case("bt-lost", 1, -19, 0, 1000, 0);      /* the headset gone: no PCM comes back */
    gone_case("dac-gone", 0, -19, 0, 0, 0);        /* off Bluetooth an error ends the song, as before */
    gone_case("usb-gone", 2, -19, 0, 0, 0);        /* a USB DAC unplugged under a write */
    rate = 44100;
    usb_case("usb48", 44100, 2, 2);   /* a 44.1 kHz song into a 48 kHz-only dongle (2026-11-22): resampled, not refused */
    usb_case("usb48-96", 96000, 2, 4); /* 24 bits at 96 kHz into it: 48 kHz, 16 bits */
    usb_case("usb48-mono", 44100, 1, 2); /* a mono song into a stereo-only device */
    for (int way = 0; way < 3; ++way) { /* two opens in a row: which read the configuration afresh */
        struct pcm_format f = {0};
        f.rate = 44100, f.channels = 2, f.bytes = 2;
        rate = way == 1 ? 48000 : 44100;
        opens = fresh = freed = 0;
        for (int i = 0; i < 2; ++i) output_close(output_open(&f, way, 17));
        printf("reread way %d opens %d fresh %d\n", way, opens, fresh);
    }
    /* the device each way opens, as the Q2 runs it (no Q2PLAYER_DEVICE): Bluetooth's names no address, so the
     * headset connected last is found whether or not demo has rewritten asound.conf yet */
    unsetenv("Q2PLAYER_DEVICE");
    for (int way = 0; way < 3; ++way) {
        struct pcm_format f = {0};
        f.rate = 44100, f.channels = 2, f.bytes = 2;
        rate = way == 1 ? 48000 : 44100;
        output_close(output_open(&f, way, 17));
        printf("device way %d %s\n", way, opened);
    }
    return 0;
}
