/* Where PCM goes. On the Q2 that is ALSA, opened as stock opens it (docs/player.md#output): the device by output
 * way and hardware type, ALSA's resampling off, a 500 ms buffer in 16 periods, and stock's software parameters
 * (alsa_sw: a dry PCM plays on through silence, and sink moves past what it missed). The DAC and a USB DAC are asked
 * what they take (choose, docs/player.md#usb-dacs): the song's own rate, width and channels when the device takes
 * them, bit for bit, else the nearest it takes, the song resampled and converted here; a USB DAC unplugged is said
 * once (say_gone) and waited for by the engine. Bluetooth is asked for 48 kHz, as stock asks it, and plays at the
 * rate bluealsa gives (its transport's), the song resampled to it here, and opened again when bluealsa closes it
 * under a stream (a codec switch: bt_reopen); a device that refuses the song's width is given another (stock's
 * fallback, and S24_3LE); Bluetooth's and, when demo says so, a USB DAC's volume is done here in software, by
 * stock's law. With Q2PLAYER_PCM set to a directory it is a file a test reads back: one per open,
 * NNNN_RATE_CH_FORMAT[_WAY].raw (s16, s24, s32, f32, dop or dsd; _bt or _usb off the DAC), written as fast as it
 * would play, or Q2PLAYER_SPEED times as fast (a test's clock runs that much faster too). There Q2PLAYER_BT_RATE
 * stands for the transport's rate (48000 unset), Q2PLAYER_FORMATS, a list such as "s16,s24", for the only formats a
 * device takes, Q2PLAYER_RATES and Q2PLAYER_CHANNELS for the only rates and channels the DAC or a USB DAC takes, and
 * Q2PLAYER_GONE for a file that is there while a USB DAC is unplugged (sink_open). */
#include "q2player.h"
#include <errno.h>
#include <math.h>
#include <dirent.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <time.h>
#include <unistd.h>
#include <libavutil/channel_layout.h>
#include <libavutil/opt.h>
#include <libswresample/swresample.h>
#ifdef HAVE_ALSA
#include "alsa_min.h"
#endif

enum { BUFFER_US = 500000, PERIODS = 16 };
enum { BT_ASK = 48000 }; /* what stock asks bluealsa for, whatever the file (reinit_audio_chain, 0x42ecc0) */
/* A Bluetooth open's wait for a busy PCM (alsa_open_bt), BT_BUSY_MS unless output_busy_wait set it (a click's, shorter:
 * engine.c CLICK_BUSY_MS) */
enum { BT_BUSY_MS = 1500, BT_BUSY_STEP_MS = 50 };
static int busy_ms = BT_BUSY_MS;
void output_busy_wait(int ms) { busy_ms = ms < 0 ? BT_BUSY_MS : ms; }

/* What a device takes, asked on the DAC's and a USB DAC's ways (choose): bit i of rates is RATES[i] (at the open's
 * format and channels), own the song's rate when the device takes it and it is none of RATES; fmts is CAP_* bits,
 * chans bit c for c channels; any_rate a device that takes whatever rate it is asked (the file sink without
 * Q2PLAYER_RATES). */
struct caps {
    unsigned rates;
    int own, fmts, chans, any_rate;
};

struct output {
    struct pcm_format in; /* what is written: the song's */
    struct pcm_format f;  /* what the device takes */
    int way;
    int neg;              /* ways 0 and 2: f was chosen from what the device takes (caps), the song converted to it */
    struct caps caps;
    int dither;           /* TPDF dither where the song is made 16-bit (from_float) */
    unsigned noise;       /* its LCG */
#ifdef HAVE_ALSA
    snd_pcm_t *pcm;
    snd_pcm_uframes_t ring, period; /* the buffer and a period, in frames, as hw_params set them */
#endif
    /* software volume (output_volume), and Bluetooth's resampler from the song's rate to the device's */
    float gain[2];
    int gained, vol16[2];
    SwrContext *swr;
    float *fbuf, *rbuf;
    int16_t *sbuf;
    uint8_t *dbuf;
    int fcap, rcap, dcap, scap;
    /* what was written last, for a click mixed in by rewinding (output_click): a ring of hist_cap frames, its
     * newest frame before hist_at, hist_n of it valid; and the click playing, as frames still to mix */
    uint8_t *hist, *tmp;
    int hist_cap, hist_at, hist_n, tmp_cap;
    const float *clk, *duck;
    int clk_len, clk_at;
    /* a click with nothing playing (output_hold, output_idle_*): a paused song's frames not yet heard, held_n of them
     * in the device's format; holding while they are kept; idle while silence is being written for clicks */
    uint8_t *held, *zbuf;
    int held_n, holding, idle, zcap;
    FILE *file;
    int paused;
    int dry_ok; /* way 3: a dry PCM at the next write is a pause's, a seek's or the open's, not an underrun */
    /* way 3, for a slow write's line (sink): this write's last snd_pcm_wait and snd_pcm_writei answers, errno after
     * the writei, and the microseconds spent in writei */
    int car_wait_r, car_errno;
    long car_write_r;
    int64_t car_write_us;
    double speed;         /* file sink: how many seconds of sound pass in one */
    int64_t written;      /* frames, file sink */
    struct timespec t0;   /* when the file sink's clock started, moved on by each pause */
    struct timespec paused_at;
};

static double between(const struct timespec *t, const struct timespec *now) {
    return (now->tv_sec - t->tv_sec) + (now->tv_nsec - t->tv_nsec) / 1e9;
}

static double since(const struct timespec *t) {
    struct timespec now;
    clock_gettime(CLOCK_MONOTONIC, &now);
    return between(t, &now);
}

static int same_format(const struct pcm_format *a, const struct pcm_format *b) {
    return a->rate == b->rate && a->channels == b->channels && a->bytes == b->bytes && a->is_float == b->is_float && a->dsd == b->dsd;
}

/* buf holding at least need elements of unit bytes; cap counts elements */
static int grow(void **buf, int *cap, int need, int unit) {
    if (need <= *cap) return 0;
    void *b = realloc(*buf, (size_t)need * unit);
    if (!b) return -1;
    *buf = b, *cap = need;
    return 0;
}

/* The formats a device is offered for f, in turn: f's own, then stock's fallback ("Format %s is not supported by
 * hardware, trying default", 0x439060: S16_LE, then S32_LE), with S32_LE and S24_3LE first for a song wider than 16
 * bits so that a USB DAC without S32_LE keeps 24 of them. DSD has its one format. Each is {bytes, is_float}. */
static int candidates(const struct pcm_format *f, int out[4][2]) {
    int n = 0, wide = f->is_float || f->bytes > 2;
    out[n][0] = f->bytes, out[n++][1] = f->is_float;
    if (f->dsd) return n;
    static const int order_wide[3][2] = {{4, 0}, {3, 0}, {2, 0}}, order_16[3][2] = {{2, 0}, {4, 0}, {3, 0}};
    for (int i = 0; i < 3; ++i) {
        const int *c = wide ? order_wide[i] : order_16[i];
        if (c[0] == f->bytes && c[1] == f->is_float) continue;
        out[n][0] = c[0], out[n++][1] = c[1];
    }
    return n;
}

/* ---- what the DAC and a USB DAC take, and what a song is made for them (docs/player.md#usb-dacs) ----
 *
 * The device is asked what it takes; the song goes out as it is when the device takes its rate, width and channels
 * (bit for bit), and otherwise converted, the PCM opened at the nearest the device takes: its width as candidates()
 * orders them, its channels (else stereo, else the fewest), and its rate by pick_rate. A cheap USB-C dongle takes
 * 48 kHz S16_LE stereo and nothing else (the AB13X of 2026-11-22), and was refused before. */
static const int RATES[] = {8000, 11025, 16000, 22050, 32000, 44100, 48000, 64000, 88200, 96000, 176400, 192000, 352800, 384000, 705600, 768000};
enum { NRATES = sizeof RATES / sizeof RATES[0], MAX_CH = 8 };
enum { CAP_S16 = 1, CAP_S24 = 2, CAP_S32 = 4, CAP_F32 = 8 };
static int cap_fmt(int bytes, int is_float) { return is_float ? CAP_F32 : bytes == 2 ? CAP_S16 : bytes == 3 ? CAP_S24 : CAP_S32; }
static const char *fmt_name(int bytes, int is_float) {
    return is_float ? "FLOAT_LE" : bytes == 2 ? "S16_LE" : bytes == 3 ? "S24_3LE" : "S32_LE";
}

/* The rate for a song at want: its own when the device takes it; else the lowest whole multiple of it the device
 * takes (88.2 kHz for 44.1); else the lowest rate above it (48 kHz for 44.1); else the highest whole divisor of it (48
 * kHz for 192); else the highest the device takes. Each keeps all of the song's bandwidth the device can carry. */
static int pick_rate(int want, const struct caps *c) {
    if (c->any_rate || want == c->own) return want;
    for (int i = 0; i < NRATES; ++i)
        if (RATES[i] == want && c->rates >> i & 1) return want;
    for (int i = 0; i < NRATES; ++i)
        if (c->rates >> i & 1 && RATES[i] > want && RATES[i] % want == 0) return RATES[i];
    for (int i = 0; i < NRATES; ++i)
        if (c->rates >> i & 1 && RATES[i] > want) return RATES[i];
    for (int i = NRATES - 1; i >= 0; --i)
        if (c->rates >> i & 1 && want % RATES[i] == 0) return RATES[i];
    for (int i = NRATES - 1; i >= 0; --i)
        if (c->rates >> i & 1) return RATES[i];
    return want; /* it named none: its own answer to the song's rate (set_rate_near) is taken */
}

static int pick_channels(int want, int chans) {
    if (want > MAX_CH || chans >> want & 1) return want;
    if (chans >> 2 & 1) return 2;
    for (int c = 1; c <= MAX_CH; ++c)
        if (chans >> c & 1) return c;
    return 0;
}

/* What f goes out as on a device that takes c: bytes 0 when the device takes none of f's widths. */
static struct pcm_format choose(const struct caps *c, const struct pcm_format *f) {
    struct pcm_format d = *f;
    int w[4][2], n = candidates(f, w), i = 0;
    while (i < n && !(c->fmts & cap_fmt(w[i][0], w[i][1]))) ++i;
    d.bytes = i < n ? w[i][0] : 0, d.is_float = i < n ? w[i][1] : 0;
    d.channels = pick_channels(f->channels, c->chans);
    d.rate = pick_rate(f->rate, c);
    return d;
}

/* What the device offers, for the log: "48000 Hz; S16_LE; 2 ch" */
static void offer_text(const struct caps *c, char *b, int cap) {
    int n = 0;
    for (int i = 0; i < NRATES && n < cap; ++i)
        if (c->rates >> i & 1) n += snprintf(b + n, (size_t)(cap - n), "%s%d", n ? "," : "", RATES[i]);
    if (c->own && n < cap) n += snprintf(b + n, (size_t)(cap - n), "%s%d", n ? "," : "", c->own);
    if (c->any_rate && n < cap) n += snprintf(b + n, (size_t)(cap - n), "any");
    static const int fmts[4][2] = {{2, 0}, {3, 0}, {4, 0}, {4, 1}};
    for (int i = 0, first = 1; i < 4 && n < cap; ++i)
        if (c->fmts & cap_fmt(fmts[i][0], fmts[i][1]))
            n += snprintf(b + n, (size_t)(cap - n), "%s%s", first ? " Hz; " : ",", fmt_name(fmts[i][0], fmts[i][1])), first = 0;
    for (int ch = 1, first = 1; ch <= MAX_CH && n < cap; ++ch)
        if (c->chans >> ch & 1) n += snprintf(b + n, (size_t)(cap - n), "%s%d", first ? "; " : ",", ch), first = 0;
    if (n < cap) snprintf(b + n, (size_t)(cap - n), " ch");
}

/* The log's word on a device, once each while it stays attached (a device that goes away, or another that offers
 * otherwise, starts again): what it offers, beside each kind of song made another format for it, or refused. */
static struct {
    char offer[200];
    int seen[8][2]; /* {song rate, channels << 16 | width}, the conversions logged */
    int n, gone;    /* gone: logged as gone (output_gone), until an open finds it again */
} said;

static void say_device(const char *name, const struct caps *c, const struct pcm_format *song, const struct pcm_format *dev) {
    char offer[200];
    offer_text(c, offer, sizeof offer);
    if (strcmp(offer, said.offer)) snprintf(said.offer, sizeof said.offer, "%s", offer), said.n = 0;
    int key = song->channels << 16 | song->bytes << 1 | song->is_float;
    for (int i = 0; i < said.n; ++i)
        if (said.seen[i][0] == song->rate && said.seen[i][1] == key) return;
    if (said.n < 8) said.seen[said.n][0] = song->rate, said.seen[said.n++][1] = key;
    if (!dev->bytes || !dev->channels)
        fprintf(stderr, "q2player: %s offers %s; refused %d Hz %s %d ch\n", name, offer, song->rate,
                fmt_name(song->bytes, song->is_float), song->channels);
    else
        fprintf(stderr, "q2player: %s offers %s; %d Hz %s %d ch played as %d Hz %s %d ch\n", name, offer, song->rate,
                fmt_name(song->bytes, song->is_float), song->channels, dev->rate, fmt_name(dev->bytes, dev->is_float),
                dev->channels);
}

/* The sound cards there, from /proc/asound/cards (Q2PLAYER_CARDS in a host test), as `0 audiocodec, 2 AB13X`: a USB
 * DAC that came back as another card than 1 is not hw:1,0. */
static void cards_text(char *out, size_t size) {
    const char *path = getenv("Q2PLAYER_CARDS") ? getenv("Q2PLAYER_CARDS") : "/proc/asound/cards";
    FILE *f = fopen(path, "r");
    char line[160], id[40];
    size_t n = 0;
    int card;
    out[0] = 0;
    if (!f) { snprintf(out, size, "none (%s)", strerror(errno)); return; }
    while (fgets(line, sizeof line, f) && n < size)
        if (sscanf(line, " %d [%39[^] ]", &card, id) == 2) n += (size_t)snprintf(out + n, size - n, "%s%d %s", n ? ", " : "", card, id);
    fclose(f);
    if (!out[0]) snprintf(out, size, "none");
}

/* A USB DAC not there to open, or gone under a write: said once until it is opened again, with the call that found it
 * so and its answer, and the sound cards there then. The drive of 2026-11-23 had `hw:1,0 is gone` about 5 s before
 * the earbuds left the bus, and the line could not say which call, which error, or where the card was. */
static int open_gone;
static void say_gone(const char *name, const char *call, int err) {
    char cards[120];
    open_gone = 1;
    if (said.gone) return;
    said.gone = 1, said.offer[0] = 0, said.n = 0;
    cards_text(cards, sizeof cards);
    fprintf(stderr, "q2player: %s is gone: %s %d (%s); sound cards: %s\n", name, call, err, strerror(-err), cards);
}

int output_gone(void) { return open_gone; }

/* ---- route 1's car output, way 3 (docs/iphone-mode.md#car-audio) ---- */

/* The iPhone gadget's probe writes CAR_FILE when the car asks for USB audio (StartUSBDeviceModeAudio) and removes it
 * when the car stops, the link drops or the cable comes out: the gadget's ALSA device on its first line, the rate the
 * car was told on its second (the PCM's rate only with a one-rate module: car_open_rate). While it is there, way 3 takes the place of the DAC's and Bluetooth's. Every image's
 * player has it (Q2_CAR_USB, tools/build.py); the host's default build has not, so test/player's other suites never
 * meet a car. */
#define CAR_FILE PEQ_ROOT "/tmp/q2-iap.audio"
#define CAR_RATE 44100
#define CAR_WAIT_MS 1000 /* a close's silence waits at most this long for room (output_close) */
#define CAR_LOOK_MS 250  /* a write waiting for the car looks at the car file this often (sink) */

static const char *car_path(void) { return getenv("Q2PLAYER_CAR") ? getenv("Q2PLAYER_CAR") : CAR_FILE; }

int output_car(char *dev, unsigned cap, unsigned *rate) {
#ifdef Q2_CAR_USB
    const char *path = car_path();
    char b[160] = "";
    FILE *f = fopen(path, "r");
    if (!f) return 0;
    size_t n = fread(b, 1, sizeof b - 1, f);
    fclose(f);
    b[n] = 0;
    char *nl = strchr(b, '\n');
    if (nl) *nl = 0;
    if (dev && cap) snprintf(dev, cap, "%s", b[0] ? b : "null");
    if (rate) *rate = nl && atoi(nl + 1) > 0 ? (unsigned)atoi(nl + 1) : CAR_RATE;
    return 1;
#else
    (void)dev, (void)cap, (void)rate;
    return 0;
#endif
}

static const volatile int *car_cut;
void output_car_watch(const volatile int *cut) { car_cut = cut; }

void output_car_gone(void) {
#ifdef Q2_CAR_USB
    unlink(car_path());
#endif
}

/* ---- the car's rate, song by song (docs/iphone-mode.md#per-song-rate) ----
 *
 * The gadget's module (tools/uac1_module, q2-uac1-playback-rates.patch) offers 32, 44.1 and 48 kHz and plays at the
 * one the car last set (its SET_CUR); P_RATE_FILE is that rate. The player is the one that knows the next song's rate,
 * so it writes the rate it wants to CAR_RATE_FILE, q2-iap tells the car (DA01, patch/q2_iap.c rate_poll), and the
 * car sets it. The PCM is only ever opened at P_RATE_FILE's rate: the car's packets are sized by it, and a PCM at
 * another would play off speed. A song at a rate the car is not at is resampled to the car's. A module with no
 * P_RATE_FILE (one rate) is left at the car file's rate, and nothing is asked. */
#define CAR_RATE_FILE PEQ_ROOT "/tmp/q2-iap.rate"
#define P_RATE_FILE "/sys/module/usb_f_uac1/parameters/p_rate"
/* how long a rate change waits for the car to follow; how often a song playing looks at P_RATE_FILE; the silence
 * written after the last song's tail before the PCM closes, so the car hears silence up to the close */
enum { CAR_FOLLOW_MS = 500, CAR_CHECK_MS = 50, CAR_PAD_MS = 60, CAR_DRAIN_MS = 600 };

/* The car's rate for a song at rate: 32, 44.1 and 48 kHz as they are; above 48 kHz the family's, 48 kHz for 96 and
 * 192 kHz and 44.1 kHz for 88.2 and 176.4 kHz, so the resampler's ratio is a whole number; anything else 44.1 kHz. */
int car_rate_for(int rate) {
    if (rate == 32000 || rate == 44100 || rate == 48000) return rate;
    return rate > 48000 && rate % 48000 == 0 ? 48000 : 44100;
}

/* P_RATE_FILE (Q2PLAYER_P_RATE in a host test), or 0 with no such file */
static int car_p_rate(void) {
    const char *path = getenv("Q2PLAYER_P_RATE") ? getenv("Q2PLAYER_P_RATE") : P_RATE_FILE;
    char b[16];
    int fd = open(path, O_RDONLY | O_CLOEXEC), n = fd < 0 ? -1 : (int)read(fd, b, sizeof b - 1);
    if (fd >= 0) close(fd);
    if (n <= 0) return 0;
    b[n] = 0;
    return atoi(b) > 0 ? atoi(b) : 0;
}

/* The rate wanted, for q2-iap: written whole (a rename), and only when it changes. Q2PLAYER_CAR_RATE in a host test. */
static int car_asked;

static void car_ask(int rate) {
    if (rate == car_asked) return;
    const char *path = getenv("Q2PLAYER_CAR_RATE") ? getenv("Q2PLAYER_CAR_RATE") : CAR_RATE_FILE;
    char tmp[1100];
    snprintf(tmp, sizeof tmp, "%s.new", path);
    FILE *f = fopen(tmp, "w");
    if (!f) return;
    fprintf(f, "%d\n", rate);
    if (fclose(f) || rename(tmp, path)) { unlink(tmp); return; }
    car_asked = rate;
}

/* A rate the car was asked for and did not set, or set another over: not waited for again (each song of an album
 * would wait) until another is asked for. */
static int car_refused;

/* Asks for want, then waits up to CAR_FOLLOW_MS for the car to set it: the rate the car plays at after, and in
 * *waited_ms how long that took. */
static int car_follow(int want, int *waited_ms) {
    int64_t t = car_trace_now();
    car_ask(want);
    int r = car_p_rate();
    while (r != want && car_trace_now() - t < CAR_FOLLOW_MS * 1000LL) usleep(2000), r = car_p_rate();
    *waited_ms = (int)((car_trace_now() - t) / 1000);
    car_refused = r == want ? 0 : want;
    return r;
}

/* The one-off line for each change of the car's rate, beside the trace's lines (cartrace.c) */
static void car_rate_line(const char *what, int from, int to, int waited_ms, int gap_ms) {
    char line[160];
    int n = gap_ms < 0 ? snprintf(line, sizeof line, "car rate %d->%d %s: wait=%dms", from, to, what, waited_ms)
                       : snprintf(line, sizeof line, "car rate %d->%d %s: wait=%dms gap=%dms", from, to, what, waited_ms, gap_ms);
    plog('I', line, n < (int)sizeof line ? n : (int)sizeof line - 1);
}

/* engine.c's word for the song about to start on way 3 (output_car_song): its rate, and whether a crossfade carries
 * the last song's tail into it. Taken by the next output_open or output_take on way 3; a click's PCM has none. */
static int car_song_rate, car_song_keep;

void output_car_song(int rate, int keep) { car_song_rate = rate, car_song_keep = keep; }

/* The headset connected last, whatever /mnt/data/asound.conf names (docs/player.md#output, "Which headset"):
 * bluealsa's plugin takes 00:00:00:00:00:00 as the PCM with the highest Sequence, the device that connected most
 * recently, so the car's PCM is found from bluealsa's SetConfiguration on, before demo rewrites the file or when it
 * never does (SaveBlueAlsa runs only on its own link thread's setup). */
#define BT_DEVICE "plug:'bluealsa:DEV=00:00:00:00:00:00,PROFILE=a2dp'"

/* Stock's ao_alsa init (hciplayer 0x438f64-0x4391f8): Bluetooth through bluealsa, a USB DAC as card 1 on a Q2
 * (hardware type 17; captured), and the local DAC as "default"; types 14 and 15 are other Shanling players'
 * "plughw:1,0". "default" is a plug over the DAC, which is what takes a float decoder's FLOAT_LE. */
static const char *device(int outputway, int hw) {
    const char *forced = getenv("Q2PLAYER_DEVICE");
    static char car[96];
    if (forced) return forced;
    if (outputway == 3) return output_car(car, sizeof car, NULL) ? car : "null";
    if (outputway == 1) return BT_DEVICE;
    if (outputway == 2) return "hw:1,0";
    if (hw == 14 || hw == 15) return "plughw:1,0";
    if (hw == 7 || hw == 11) return "hw:1,0";
    return "default";
}

/* Whether way's device is there to open: a USB DAC's card node (hw:1,0 is /dev/snd/pcmC1D0p), so a wait for one
 * that went (engine.c output_back) does not open it, and alsa-lib log its absence, at each look; the file sink's
 * while Q2PLAYER_GONE's file is not there. Every other way is taken as there. */
int output_present(int outputway) {
    const char *gone = getenv("Q2PLAYER_GONE");
    if (outputway != 2) return 1;
    if (getenv("Q2PLAYER_PCM")) return !gone || access(gone, 0);
    return getenv("Q2PLAYER_DEVICE") || !access("/dev/snd/pcmC1D0p", 0);
}

#ifdef HAVE_ALSA
static int alsa_format(const struct pcm_format *f) {
    if (f->dsd == DSD_DOP) return SND_PCM_FORMAT_S24_3LE;
    if (f->dsd == DSD_NATIVE) return SND_PCM_FORMAT_DSD_U32_BE;
    return f->is_float ? SND_PCM_FORMAT_FLOAT_LE : f->bytes == 2 ? SND_PCM_FORMAT_S16_LE : f->bytes == 3 ? SND_PCM_FORMAT_S24_3LE : SND_PCM_FORMAT_S32_LE;
}

/* Stock's software parameters (ao_alsa's init, 0x438d64-0x438dc4, the same on every output way): the stream starts
 * once a period is written, and stop_threshold and silence_size are the boundary, so a PCM that runs dry is never
 * stopped: the hardware plays the silence ALSA fills the ring with and waits, where at ALSA's defaults (start at the
 * first frame, stop at a buffer's worth of underrun) it would stop and start the stream again (docs/player.md#output).
 * What is written after such an underrun lands behind hw_ptr, so sink() moves past it first. avail_min (a period)
 * and silence_threshold (0) are ALSA's defaults, which stock leaves too. */
static int alsa_sw(struct output *o) {
    snd_pcm_sw_params_t *s = NULL;
    snd_pcm_uframes_t boundary = 0;
    int r;
    if ((r = snd_pcm_get_params(o->pcm, &o->ring, &o->period)) < 0 || (r = snd_pcm_sw_params_malloc(&s)) < 0) return r;
    if ((r = snd_pcm_sw_params_current(o->pcm, s)) >= 0 && (r = snd_pcm_sw_params_get_boundary(s, &boundary)) >= 0 &&
        (r = snd_pcm_sw_params_set_start_threshold(o->pcm, s, o->period)) >= 0 &&
        (r = snd_pcm_sw_params_set_stop_threshold(o->pcm, s, boundary)) >= 0 &&
        (r = snd_pcm_sw_params_set_silence_size(o->pcm, s, boundary)) >= 0)
        r = snd_pcm_sw_params(o->pcm, s);
    snd_pcm_sw_params_free(s);
    return r;
}

/* Ways 0 and 2: what the device takes, asked of its hardware parameters (struct caps), and the song's format chosen
 * from it (choose); p is left with the format and channels set and the rate to be. */
static int alsa_negotiate(struct output *o, snd_pcm_hw_params_t *p, const char *name) {
    static const int fmts[4][2] = {{2, 0}, {3, 0}, {4, 0}, {4, 1}};
    struct caps *c = &o->caps;
    struct pcm_format t = o->f, d;
    memset(c, 0, sizeof *c);
    for (int i = 0; i < 4; ++i) {
        t.bytes = fmts[i][0], t.is_float = fmts[i][1];
        if (snd_pcm_hw_params_test_format(o->pcm, p, alsa_format(&t)) >= 0) c->fmts |= cap_fmt(t.bytes, t.is_float);
    }
    for (int ch = 1; ch <= MAX_CH; ++ch)
        if (snd_pcm_hw_params_test_channels(o->pcm, p, (unsigned)ch) >= 0) c->chans |= 1 << ch;
    c->any_rate = 1; /* the rates are asked once the format and channels are set */
    d = choose(c, &o->in);
    int r;
    if (!d.bytes || !d.channels || (r = snd_pcm_hw_params_set_format(o->pcm, p, alsa_format(&d))) < 0 ||
        (r = snd_pcm_hw_params_set_channels(o->pcm, p, (unsigned)d.channels)) < 0 ||
        (r = snd_pcm_hw_params_set_rate_resample(o->pcm, p, 0)) < 0) {
        say_device(name, c, &o->in, &d);
        return -EINVAL;
    }
    c->any_rate = 0;
    for (int i = 0; i < NRATES; ++i)
        if (snd_pcm_hw_params_test_rate(o->pcm, p, (unsigned)RATES[i], 0) >= 0) c->rates |= 1u << i;
    if (snd_pcm_hw_params_test_rate(o->pcm, p, (unsigned)o->in.rate, 0) >= 0) c->own = o->in.rate;
    for (int i = 0; i < NRATES; ++i)
        if (RATES[i] == c->own) c->own = 0; /* one of RATES: its bit says it */
    o->f = choose(c, &o->in);
    o->neg = 1;
    return 0;
}

static int alsa_open(struct output *o, int outputway, int hw) {
    snd_pcm_hw_params_t *p = NULL;
    const char *name = device(outputway, hw);
    /* alsa-lib reads its configuration again only when alsa.conf itself changes, not /mnt/data/asound.conf, which
     * its hook loads and demo rewrites at each link (SaveBlueAlsa), so the rest of bluealsa's defaults there would
     * stay those of the player's first open. Stock's ao_alsa frees the configuration before it opens (hciplayer
     * 0x438ffc); so does every Bluetooth open here, the old PCM already closed. */
    if (outputway == 1) snd_config_update_free_global();
    int r = snd_pcm_open(&o->pcm, name, SND_PCM_STREAM_PLAYBACK, 0), c[4][2], n = candidates(&o->f, c), i = 0;
    if (r < 0) {
        if (outputway == 2 && (r == -ENOENT || r == -ENODEV)) say_gone(name, "snd_pcm_open", r); /* unplugged */
        else if (r != -EBUSY) fprintf(stderr, "q2player: %s: %s\n", name, snd_strerror(r));
        return r == -EBUSY ? -EBUSY : -1;
    }
    if (outputway == 2) said.gone = 0;
    /* the DAC and a USB DAC are asked what they take and the song converted to it; DSD's bits go as they are, and
     * Bluetooth and the car are opened at the rate they play at (way 3's o->f.rate is the car's: car_open_rate) */
    int neg = (outputway == 0 || outputway == 2) && !o->f.dsd;
    unsigned want = outputway == 1 ? BT_ASK : (unsigned)o->f.rate, rate, buffer = BUFFER_US, periods = PERIODS;
    if ((r = snd_pcm_hw_params_malloc(&p)) < 0 || (r = snd_pcm_hw_params_any(o->pcm, p)) < 0 ||
        (r = snd_pcm_hw_params_set_access(o->pcm, p, SND_PCM_ACCESS_RW_INTERLEAVED)) < 0)
        goto done;
    if (neg) {
        if ((r = alsa_negotiate(o, p, name)) < 0) goto done;
        want = (unsigned)o->f.rate;
    } else {
        for (; i < n; ++i) {
            struct pcm_format t = o->f;
            t.bytes = c[i][0], t.is_float = c[i][1];
            if (snd_pcm_hw_params_test_format(o->pcm, p, alsa_format(&t)) >= 0) break;
            fprintf(stderr, "q2player: %s takes no format %d\n", name, alsa_format(&t));
        }
        if (i == n) { r = -EINVAL; goto done; }
        o->f.bytes = c[i][0], o->f.is_float = c[i][1];
        if ((r = snd_pcm_hw_params_set_format(o->pcm, p, alsa_format(&o->f))) < 0 ||
            (r = snd_pcm_hw_params_set_channels(o->pcm, p, o->f.channels)) < 0 ||
            (r = snd_pcm_hw_params_set_rate_resample(o->pcm, p, 0)) < 0)
            goto done;
    }
    rate = want;
    if ((r = snd_pcm_hw_params_set_rate_near(o->pcm, p, &rate, NULL)) < 0 ||
        (r = snd_pcm_hw_params_set_buffer_time_near(o->pcm, p, &buffer, NULL)) < 0 ||
        (r = snd_pcm_hw_params_set_periods_near(o->pcm, p, &periods, NULL)) < 0 ||
        (r = snd_pcm_hw_params(o->pcm, p)) < 0 || (r = alsa_sw(o)) < 0 || (r = snd_pcm_prepare(o->pcm)) < 0)
        ;
done:
    if (r < 0 && r != -EBUSY) /* busy: alsa_open_bt says it once, after its wait */
        fprintf(stderr, "q2player: %s %d Hz %d ch %d bytes: %s\n", name, o->f.rate, o->f.channels, o->f.bytes, snd_strerror(r));
    if (p) snd_pcm_hw_params_free(p);
    /* DSD's frames at another rate would play as noise, and the car's PCM is only ever at the car's rate; Bluetooth
     * and a negotiated device are resampled to the rate they give */
    if (r >= 0 && outputway != 1 && !neg && rate != want) {
        fprintf(stderr, "q2player: %s gave %u Hz for %u\n", name, rate, want);
        r = -1;
    }
    if (r < 0) { snd_pcm_close(o->pcm); o->pcm = NULL; return r == -EBUSY ? -EBUSY : -1; }
    o->f.rate = (int)rate;
    if (neg && !same_format(&o->f, &o->in)) say_device(name, &o->caps, &o->in, &o->f);
    return 0;
}

/* bluealsa's PCM takes one client, claimed at hw_params, and is busy (-EBUSY) while another holds it or until
 * bluealsa has seen the last one's close, which comes a moment after snd_pcm_close: a click session's PCM closed and
 * another opened at once met it on a Q2. On Bluetooth the open is tried again BT_BUSY_STEP_MS apart for up to
 * BT_BUSY_MS (docs/internals.md#bluetooth-pause), the play loop held meanwhile, and the wait is said once: opened
 * after it, or still busy, as another client (an AirPlay receiver, q2video's sources) holds the PCM. */
/* Who holds card's first playback PCM, for the busy line: its owner_pid from the kernel's status of the substream and
 * that process's command line, as `pid 812 (/usr/bin/q2video -t plughw:0,0)` (docs/internals.md#output-failure);
 * Q2PLAYER_PROC stands for /proc in a host test. Empty when the status names no owner. */
static void pcm_holder(int card, char *out, size_t size) {
    const char *proc = getenv("Q2PLAYER_PROC") ? getenv("Q2PLAYER_PROC") : "/proc";
    char path[96], line[96], cmd[64] = "";
    int pid = 0;
    out[0] = 0;
    snprintf(path, sizeof path, "%s/asound/card%d/pcm0p/sub0/status", proc, card);
    FILE *f = fopen(path, "r");
    while (f && !pid && fgets(line, sizeof line, f)) sscanf(line, "owner_pid : %d", &pid);
    if (f) fclose(f);
    if (pid <= 0) return;
    snprintf(path, sizeof path, "%s/%d/cmdline", proc, pid);
    int fd = open(path, O_RDONLY | O_CLOEXEC), n = fd >= 0 ? (int)read(fd, cmd, sizeof cmd - 1) : 0;
    if (fd >= 0) close(fd);
    for (int i = 0; i < n - 1; ++i)
        if (!cmd[i]) cmd[i] = ' '; /* its arguments, NUL apart */
    cmd[n > 0 ? n : 0] = 0;
    snprintf(out, size, ", held by pid %d (%s)", pid, cmd[0] ? cmd : "gone");
}

/* bluealsa's PCM has no kernel status to name its client, and bluealsa says nothing of it to another: so the busy line
 * names the processes running that open it (q2video's sources, the AirPlay receiver, librespot, aplay), from
 * /proc/PID/cmdline, as `, running: pid 812 (/usr/bin/q2video -t bluealsa)`; empty when none runs. */
static void bt_clients(char *out, size_t size) {
    static const char *const names[] = { "q2video", "shairport", "librespot", "aplay" };
    const char *proc = getenv("Q2PLAYER_PROC") ? getenv("Q2PLAYER_PROC") : "/proc";
    size_t at = 0;
    out[0] = 0;
    DIR *d = opendir(proc);
    for (struct dirent *e; d && (e = readdir(d));) {
        int pid = atoi(e->d_name);
        if (pid <= 0 || pid == getpid()) continue;
        char path[96], cmd[64];
        snprintf(path, sizeof path, "%s/%d/cmdline", proc, pid);
        int fd = open(path, O_RDONLY | O_CLOEXEC), n = fd >= 0 ? (int)read(fd, cmd, sizeof cmd - 1) : 0;
        if (fd >= 0) close(fd);
        if (n <= 0) continue;
        for (int i = 0; i < n - 1; ++i)
            if (!cmd[i]) cmd[i] = ' ';
        cmd[n] = 0;
        int known = 0;
        for (unsigned k = 0; k < sizeof names / sizeof *names; ++k) known |= strstr(cmd, names[k]) != NULL;
        if (known && at + 1 < size)
            at += (size_t)snprintf(out + at, size - at, "%s pid %d (%s)", at ? "," : ", running:", pid, cmd);
        if (at >= size) at = size - 1;
    }
    if (d) closedir(d);
}

static int alsa_open_bt(struct output *o, int outputway, int hw) {
    struct pcm_format f = o->f;
    int r, waited = 0;
    while ((r = alsa_open(o, outputway, hw)) == -EBUSY && outputway == 1 && waited < busy_ms)
        usleep(BT_BUSY_STEP_MS * 1000), waited += BT_BUSY_STEP_MS, o->f = f;
    if (r == -EBUSY && outputway == 1) {
        char clients[160];
        bt_clients(clients, sizeof clients);
        fprintf(stderr, "q2player: bluealsa's PCM still busy after %d ms: another client holds it%s\n", waited, clients);
    } else if (r == -EBUSY) {
        char holder[112];
        pcm_holder(strstr(device(outputway, hw), ":1,") ? 1 : 0, holder, sizeof holder); /* hw:1,0 is card 1's */
        fprintf(stderr, "q2player: %s: %s%s\n", device(outputway, hw), snd_strerror(r), holder);
    }
    else if (waited && r == 0)
        fprintf(stderr, "q2player: bluealsa's PCM was busy; it opened after %d ms\n", waited);
    return r < 0 ? -1 : 0;
}
#endif


/* ---- the headphone DAC's controls, /dev/shanling_dac (codec_cs43131.ko), called as stock's player calls them
 * (docs/player.md#output; the order is the emulator shim's log of stock's): each song sets the mode (dac_song), and
 * on output way 0 alone the line is muted before the PCM opens, closes, pauses or drops for a seek, and unmuted
 * after. The unmute lets the line go 90-100 ms later and the volume ramps back, so where a
 * person is waiting for sound (a press, a seek, a resume) the first frame follows DAC_RELEASE_MS after it, as in
 * Vamp's player (docs/internals.md#wired-start). */
enum { DAC_MODE = 0xc0044d1b, DAC_INIT = 0xc0044d0a, DAC_MUTE = 0xc0044d1f };
enum { DAC_RELEASE_MS = 130, DAC_PRESS_MS = 5000 };
static int dac_fd = -2, dac_muted;

static void dac_ioctl(unsigned long request, int value) {
    if (dac_fd == -2) dac_fd = open("/dev/shanling_dac", O_RDWR | O_CLOEXEC); /* kept open, as stock's is */
    if (dac_fd >= 0) ioctl(dac_fd, request, &value);
}

/* Stock sends the mode at each song on every output, 1 for DoP and 0 otherwise (Native too, captured), and the init
 * request only on the DAC's. */
void dac_song(int outputway, int dsd) {
    dac_ioctl(DAC_MODE, dsd == DSD_DOP);
    if (!outputway) dac_ioctl(DAC_INIT, 0);
}

void dac_mute(int outputway) {
    if (outputway) return;
    dac_ioctl(DAC_MUTE, 1);
    dac_muted = 1;
}

/* Unmutes what dac_mute muted, once; with wait, sound is held back until the line is open. */
void dac_unmute(int outputway, int wait) {
    if (outputway || !dac_muted) return;
    dac_ioctl(DAC_MUTE, 0);
    dac_muted = 0;
    if (wait) usleep(DAC_RELEASE_MS * 1000);
}

/* demo's word in /tmp/q2press (patch/playback.c's press_stamp): CLOCK_MONOTONIC in ms, odd for a person's press, even
 * for a natural start demo did not mute for. It counts when younger than DAC_PRESS_MS, and each word once. */
int dac_stamp(int pressed) {
    static int seen;
    int at = 0, fd = open(PEQ_ROOT "/tmp/q2press", O_RDONLY | O_CLOEXEC);
    if (fd < 0) return 0;
    if (read(fd, &at, sizeof at) != sizeof at) at = 0;
    close(fd);
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    unsigned now = (unsigned)(t.tv_sec * 1000ULL + t.tv_nsec / 1000000);
    if (!at || (at & 1) != pressed || at == seen || now - (unsigned)at >= DAC_PRESS_MS) return 0;
    seen = at;
    return 1;
}

static int frame_bytes(const struct output *o) { return o->f.channels * o->f.bytes; }

/* The history is the buffer's length: the most a click can rewind. With no memory for it, clicks come late. */
static struct output *history(struct output *o) {
    o->hist_cap = (int)((int64_t)o->f.rate * BUFFER_US / 1000000);
    if (!(o->hist = malloc((size_t)o->hist_cap * frame_bytes(o)))) o->hist_cap = 0;
    return o;
}

static void remember(struct output *o, const uint8_t *data, int frames) {
    int fb = frame_bytes(o);
    if (!o->hist_cap) return;
    if (frames > o->hist_cap) data += (size_t)(frames - o->hist_cap) * fb, frames = o->hist_cap;
    int m = frames < o->hist_cap - o->hist_at ? frames : o->hist_cap - o->hist_at;
    memcpy(o->hist + (size_t)o->hist_at * fb, data, (size_t)m * fb);
    memcpy(o->hist, data + (size_t)m * fb, (size_t)(frames - m) * fb);
    o->hist_at = (o->hist_at + frames) % o->hist_cap;
    o->hist_n = o->hist_n + frames < o->hist_cap ? o->hist_n + frames : o->hist_cap;
}

/* The file sink as a device: Q2PLAYER_FORMATS, when set, is all it takes, and on Bluetooth it plays at
 * Q2PLAYER_BT_RATE, as bluealsa at its transport's. On ways 0 and 2 it is asked as a device is (choose):
 * Q2PLAYER_RATES and Q2PLAYER_CHANNELS, lists such as "48000" and "2", are all it takes of those, and a USB DAC is
 * unplugged while Q2PLAYER_GONE's file is there (output_present). */
static const char *sink_name(const struct pcm_format *f) {
    return f->dsd == DSD_DOP ? "dop" : f->dsd ? "dsd" : f->is_float ? "f32" : f->bytes == 2 ? "s16" : f->bytes == 3 ? "s24" : "s32";
}

static void sink_caps(struct caps *c) {
    const char *only = getenv("Q2PLAYER_FORMATS"), *rates = getenv("Q2PLAYER_RATES"), *chans = getenv("Q2PLAYER_CHANNELS");
    static const char *names[4] = {"s16", "s24", "s32", "f32"};
    memset(c, 0, sizeof *c);
    for (int i = 0; i < 4; ++i)
        if (!only || strstr(only, names[i])) c->fmts |= 1 << i;
    c->any_rate = !rates;
    for (const char *r = rates; r && *r; r += strcspn(r, ",") + (r[strcspn(r, ",")] == ','))
        for (int i = 0; i < NRATES; ++i)
            if (RATES[i] == atoi(r)) c->rates |= 1u << i;
    for (int ch = 1; ch <= MAX_CH; ++ch) {
        char b[4];
        snprintf(b, sizeof b, "%d", ch);
        if (!chans || strstr(chans, b)) c->chans |= 1 << ch;
    }
}

static int sink_open(struct output *o, const char *dir, int outputway) {
    static int opens;
    const char *only = getenv("Q2PLAYER_FORMATS"), *bt = getenv("Q2PLAYER_BT_RATE"), *name = device(outputway, 0);
    if (!output_present(outputway)) { say_gone(name, "the card's node", -ENOENT); return -1; }
    if (outputway == 2) said.gone = 0;
    if ((outputway == 0 || outputway == 2) && !o->f.dsd) {
        sink_caps(&o->caps);
        o->f = choose(&o->caps, &o->in);
        o->neg = 1;
        if (!o->f.bytes || !o->f.channels) { say_device(name, &o->caps, &o->in, &o->f); return -1; }
        if (!same_format(&o->f, &o->in)) say_device(name, &o->caps, &o->in, &o->f);
    } else {
        int c[4][2], n = candidates(&o->f, c), i = 0;
        for (; only && i < n; ++i) {
            struct pcm_format t = o->f;
            t.bytes = c[i][0], t.is_float = c[i][1];
            if (strstr(only, sink_name(&t))) break;
        }
        if (i == n) return -1;
        o->f.bytes = c[i][0], o->f.is_float = c[i][1];
        if (outputway == 1) o->f.rate = bt && atoi(bt) > 0 ? atoi(bt) : BT_ASK; /* way 3's is the car's already */
    }
    char path[1024];
    snprintf(path, sizeof path, "%s/%04d_%d_%d_%s%s.raw", dir, opens++, o->f.rate, o->f.channels, sink_name(&o->f),
             outputway == 1 ? "_bt" : outputway == 2 ? "_usb" : outputway == 3 ? "_car" : "");
    if (!(o->file = fopen(path, "wb"))) { perror(path); return -1; }
    clock_gettime(CLOCK_MONOTONIC, &o->t0);
    return 0;
}

/* The resampler from the song's rate to the device's, on float, which also makes its channels the device's. On
 * Bluetooth and the car with stock's settings: MPlayer's lavcresample, which stock appends for a file at another rate
 * (af_lavcresample: filter length 16, phase shift 10, not linear, cutoff max(1 - 6.5 / (16 + 8), 0.8));
 * docs/internals.md#bluetooth-resampler. For the DAC and a USB DAC with libswresample's own defaults, USB_FILTER taps
 * and a cutoff of USB_CUTOFF, flat to 21.4 kHz at 44.1 (docs/player.md#usb-dacs: about 0.13% of this machine's core
 * for 44.1 to 48 kHz, FFmpeg's C alone). A mono song goes to both channels of a stereo device at its own level, as
 * the DAC's plug duplicates it; more channels than the device has are mixed down by libswresample. */
#define USB_FILTER 32
#define USB_CUTOFF 0.97
static int resampler(struct output *o) {
    swr_free(&o->swr);
    if (o->in.rate == o->f.rate && o->in.channels == o->f.channels) return 0;
    int hq = o->way == 0 || o->way == 2;
    AVChannelLayout li, lo;
    av_channel_layout_default(&li, o->in.channels);
    av_channel_layout_default(&lo, o->f.channels);
    if (swr_alloc_set_opts2(&o->swr, &lo, AV_SAMPLE_FMT_FLT, o->f.rate, &li, AV_SAMPLE_FMT_FLT, o->in.rate, 0, NULL) < 0) return -1;
    av_opt_set_int(o->swr, "filter_size", hq ? USB_FILTER : 16, 0);
    av_opt_set_int(o->swr, "phase_shift", 10, 0);
    av_opt_set_int(o->swr, "linear_interp", 0, 0);
    av_opt_set_double(o->swr, "cutoff", hq ? USB_CUTOFF : 0.8, 0);
    static const double mono[2] = {1, 1};
    if (o->in.channels == 1 && o->f.channels == 2) swr_set_matrix(o->swr, mono, 1);
    if (swr_init(o->swr) < 0) { swr_free(&o->swr); return -1; }
    return 0;
}

/* The rate a way-3 PCM opens at: with a song's word (output_car_song), the car is asked for its rate and followed
 * for CAR_FOLLOW_MS; a click's PCM takes the car's rate as it is; a one-rate module, the car file's. */
static int car_open_rate(void) {
    unsigned line = CAR_RATE;
    output_car(NULL, 0, &line);
    int now = car_p_rate(), want = car_song_rate ? car_rate_for(car_song_rate) : 0, waited = 0;
    car_song_rate = 0;
    if (!now) return (int)line;
    if (want == now) car_ask(want); /* q2-iap's DA01 stays the song's */
    if (!want || want == now || want == car_refused || car_song_keep) return now;
    int r = car_follow(want, &waited);
    car_rate_line(r == want ? "at the open" : "not followed at the open, resampled", now, want, waited, -1);
    return r ? r : now;
}

struct output *output_open(const struct pcm_format *f, int outputway, int hw) {
    struct output *o = calloc(1, sizeof *o);
    const char *dir = getenv("Q2PLAYER_PCM");
    if (!o) return NULL;
    o->dry_ok = 1;
    o->in = o->f = *f, o->way = outputway;
    o->dither = outputway == 0 || outputway == 2, o->noise = 1;
    o->gain[0] = o->gain[1] = 1;
    o->speed = getenv("Q2PLAYER_SPEED") ? atof(getenv("Q2PLAYER_SPEED")) : 1;
    if (o->speed <= 0) o->speed = 1;
    int r = -1;
    open_gone = 0;
    if (outputway == 3) o->f.rate = car_open_rate();
    if (dir) r = sink_open(o, dir, outputway);
#ifdef HAVE_ALSA
    else r = alsa_open_bt(o, outputway, hw);
#else
    else (void)hw, fprintf(stderr, "q2player: built without ALSA; set Q2PLAYER_PCM\n");
#endif
    if (r == 0 && resampler(o) == 0) {
        if (outputway == 3) car_trace_open(o->f.rate, car_path()); /* docs/iphone-mode.md#car-trace */
        return history(o);
    }
    o->way = -1; /* nothing was opened: no trace line at its close */
    output_close(o);
    return NULL;
}

/* The device's frames written and not yet played. */
static int64_t dev_delay(struct output *o) {
#ifdef HAVE_ALSA
    if (o->pcm) {
        snd_pcm_sframes_t d = 0;
        return snd_pcm_delay(o->pcm, &d) < 0 || d < 0 ? 0 : d;
    }
#endif
    int64_t played = (int64_t)((o->paused ? between(&o->t0, &o->paused_at) : since(&o->t0)) * o->speed * o->f.rate);
    return o->written > played ? o->written - played : 0;
}

void output_levels(struct output *o, long *avail, long *delay) {
#ifdef HAVE_ALSA
    if (o->pcm) {
        snd_pcm_sframes_t d = 0;
        *avail = (long)snd_pcm_avail(o->pcm);
        *delay = snd_pcm_delay(o->pcm, &d) < 0 ? -1 : (long)d;
        return;
    }
#endif
    *delay = (long)dev_delay(o);
    *avail = (long)((int64_t)o->f.rate * BUFFER_US / 1000000) - *delay; /* the file sink's buffer is BUFFER_US */
}

/* A paused song's frames held (hold: a click, or a pause the device refused), at the rate written: written, not heard,
 * and not in the PCM. */
int64_t output_held(struct output *o) {
    int64_t n = o && o->holding ? o->held_n : 0;
    return o && o->in.rate != o->f.rate ? n * o->in.rate / o->f.rate : n;
}

int64_t output_delay(struct output *o) {
    int64_t d = dev_delay(o);
    if (o->in.rate != o->f.rate) d = d * o->in.rate / o->f.rate;
    return d + (o->swr ? swr_get_delay(o->swr, o->in.rate) : 0);
}

/* ---- software volume: stock's mixer_setvolume (0x4287a4) as mcl-setvol reaches it (0x42c858), the bytes L and R of
 * L << 8 | R, as floats. Either at 100 takes the volume filter out: unity. Otherwise its vendor's law: from 50 up a
 * level is 0.1 + 0.018 per step past 50, so 255 is 3.79 (+11.6 dB); below 50 it is 0.002 a step (soft_vol_max 20
 * times 1e-4). Which of the two is chosen by L alone, so R's own law goes with it; a level below 0 (R well below L) is
 * taken here as silence, where stock's dB of it is not a number. MPlayer's volume filter then scales a 16-bit song in
 * integers, (x * (int)(255 * level)) >> 8 saturated (so 50 is 25/256, 0.0977: captured), and anything wider in float,
 * x * level clipped at full scale. */
void volume_gains(int volume, float gain[2]) {
    int l = volume >> 8 & 255, r = volume & 255;
    double g[2] = {l * 0.002, r * 0.002};
    if (l == 100 || r == 100) g[0] = g[1] = 1;
    else if (l >= 50) g[0] = 0.1 + 0.018 * (l - 50), g[1] = 0.1 + 0.018 * (r - 50);
    for (int i = 0; i < 2; ++i) gain[i] = g[i] > 0 ? (float)g[i] : 0;
}

void output_volume(struct output *o, int volume) {
    if (!o || o->f.dsd) return; /* DSD's bits go out as they are */
    volume_gains(volume, o->gain);
    o->gained = o->gain[0] != 1 || o->gain[1] != 1;
    for (int i = 0; i < 2; ++i) o->vol16[i] = (int)(255.0 * o->gain[i]);
}

static int car_switch(struct output *o, int want);

int output_take(struct output *o, const struct pcm_format *f, int outputway) {
    if (!o || outputway != o->way) return 0;
    /* The DAC and a USB DAC: the PCM stays open when the song goes out as the one before did (choose): the same
     * format at another rate into a device that takes one rate, its resampler made again, so a fixed-rate dongle
     * plays a preloaded change of rate without a break; a song the device takes as it is opens it at that. */
    if (o->neg && !f->dsd && !o->in.dsd) {
        struct pcm_format d = choose(&o->caps, f);
        if (d.rate != o->f.rate || d.channels != o->f.channels || d.bytes != o->f.bytes || d.is_float != o->f.is_float) return 0;
        if (f->rate != o->in.rate || f->channels != o->in.channels) {
            if (o->swr) { /* the old rate's resampler: what it holds back is played, then a fresh one */
                int n = swr_get_out_samples(o->swr, 0);
                if (n > 0 && !grow((void **)&o->rbuf, &o->rcap, n * o->f.channels, sizeof(float))) {
                    n = swr_convert(o->swr, (uint8_t **)&o->rbuf, n, NULL, 0);
                    if (n > 0) output_write(o, NULL, n);
                }
            }
            o->in = *f;
            if (resampler(o) < 0) return 0;
        }
        o->in = *f;
        return 1;
    }
    /* Bluetooth and the car play at their own rate: a song at another goes through the resampler, and the PCM, and
     * with it the car's stream, stays open across it; on way 3 a song for another of the car's rates moves the car
     * there first (car_switch) */
    if ((outputway != 1 && outputway != 3) || f->dsd || o->in.dsd) return same_format(f, &o->in);
    if (f->channels != o->in.channels) return 0;
    /* stock asks the song's width of Bluetooth; the car's device is 16-bit whatever the song, and converted to */
    if (outputway == 1 && (f->bytes != o->in.bytes || f->is_float != o->in.is_float)) return 0;
    /* way 3: a song for another of the car's rates, unless a crossfade carries the last one's tail into it */
    int want = outputway == 3 && car_song_rate && !car_song_keep && car_p_rate() ? car_rate_for(car_song_rate) : 0;
    car_song_rate = 0;
    if (want && want == o->f.rate) car_ask(want); /* q2-iap's DA01 stays the song's; a change asks after the drain */
    if (want == car_refused || want == o->f.rate) want = 0;
    if (f->rate != o->in.rate || (want && want != o->f.rate)) {
        /* the old rate's resampler: what it holds back is played, then a fresh one */
        if (o->swr) {
            int n = swr_get_out_samples(o->swr, 0);
            if (n > 0 && !grow((void **)&o->rbuf, &o->rcap, n * o->f.channels, sizeof(float))) {
                n = swr_convert(o->swr, (uint8_t **)&o->rbuf, n, NULL, 0);
                if (n > 0) output_write(o, NULL, n);
            }
        }
        if (want && want != o->f.rate && car_switch(o, want) < 0) return 0;
        o->in = *f;
        if (resampler(o) < 0) return 0;
    }
    o->in = *f;
    return 1;
}

/* frames of the song's, in o->in's format, as interleaved float in o->fbuf, times the volume, clipped at full scale */
static int to_float(struct output *o, const uint8_t *data, int frames) {
    int ch = o->in.channels, n = frames * ch;
    if (grow((void **)&o->fbuf, &o->fcap, n, sizeof(float))) return -1;
    float *x = o->fbuf;
    if (o->in.is_float) memcpy(x, data, (size_t)n * 4);
    else if (o->in.bytes == 2) for (int i = 0; i < n; ++i) x[i] = ((const int16_t *)data)[i] * (1.0f / 32768);
    else for (int i = 0; i < n; ++i) x[i] = (float)(((const int32_t *)data)[i] * (1.0 / 2147483648.0));
    if (o->gained && !(o->in.bytes == 2 && !o->in.is_float)) /* 16 bits were scaled already, in integers */
        for (int i = 0; i < n; ++i) {
            float v = x[i] * o->gain[i % ch < 2 ? i % ch : 0];
            x[i] = v > 1 ? 1 : v < -1 ? -1 : v;
        }
    return 0;
}

/* frames of float at x into o->dbuf in the device's format: rounded to nearest and saturated, as dsp.c's from_float
 * (hciplayer's converters), with a TPDF dither where a wider or resampled song is made 16-bit for the DAC or a USB
 * DAC (Bluetooth and the car round, as stock's lavcresample and af_format did) */
static int from_float(struct output *o, const float *x, int frames) {
    int n = frames * o->f.channels;
    if (grow((void **)&o->dbuf, &o->dcap, n, 4)) return -1;
    if (o->f.is_float) memcpy(o->dbuf, x, (size_t)n * 4);
    else if (o->f.bytes == 2)
        for (int i = 0; i < n; ++i) {
            float v = x[i] * 32768.0f;
            if (o->dither && v != (float)(int)v) { /* not a 16-bit sample already: TPDF dither of a step, as dsp.c's */
                o->noise = o->noise * 1664525u + 1013904223u;
                v += (float)((int)(o->noise >> 16 & 255) - (int)(o->noise >> 24)) * (1.0f / 256);
            }
            ((int16_t *)o->dbuf)[i] = v >= 32767.0f ? 32767 : v <= -32768.0f ? -32768 : (int16_t)lrintf(v);
        }
    else
        for (int i = 0; i < n; ++i) {
            double v = x[i] * 2147483648.0;
            int32_t w = v >= 2147483647.0 ? 2147483647 : v <= -2147483648.0 ? INT32_MIN : (int32_t)llrint(v);
            if (o->f.bytes == 4) ((int32_t *)o->dbuf)[i] = w;
            else { /* S24_3LE: the top three bytes, rounded */
                int64_t t = ((int64_t)w + 128) >> 8;
                if (t > 8388607) t = 8388607;
                uint8_t *d = o->dbuf + 3 * i;
                d[0] = (uint8_t)t, d[1] = (uint8_t)(t >> 8), d[2] = (uint8_t)(t >> 16);
            }
        }
    return 0;
}

/* frames in o->in's format made the device's: as they are when nothing differs, which is the bit-exact path */
static int convert(struct output *o, const uint8_t *data, int frames, const uint8_t **out) {
    if (!data) { /* the resampler's tail, already in o->rbuf */
        if (from_float(o, o->rbuf, frames)) return -1;
        *out = o->dbuf;
        return frames;
    }
    if (o->gained && o->in.bytes == 2 && !o->in.is_float) { /* af_volume's integer path */
        int ch = o->in.channels, n = frames * ch;
        if (grow((void **)&o->sbuf, &o->scap, n, 2)) return -1;
        for (int i = 0; i < n; ++i) {
            int v = ((const int16_t *)data)[i] * o->vol16[i % ch < 2 ? i % ch : 0] >> 8;
            o->sbuf[i] = (int16_t)(v > 32767 ? 32767 : v < -32768 ? -32768 : v);
        }
        data = (const uint8_t *)o->sbuf;
    }
    if (!o->swr && o->in.bytes == o->f.bytes && o->in.is_float == o->f.is_float && (!o->gained || o->in.bytes == 2)) {
        *out = data;
        return frames;
    }
    if (to_float(o, data, frames)) return -1;
    const float *x = o->fbuf;
    if (o->swr) {
        int room = swr_get_out_samples(o->swr, frames);
        if (grow((void **)&o->rbuf, &o->rcap, room * o->f.channels, sizeof(float))) return -1;
        frames = swr_convert(o->swr, (uint8_t **)&o->rbuf, room, (const uint8_t **)&o->fbuf, frames);
        if (frames < 0) return -1;
        x = o->rbuf;
    }
    if (from_float(o, x, frames)) return -1;
    *out = o->dbuf;
    return frames;
}

#ifdef HAVE_ALSA
/* bluealsa's PCM gone from under a stream. A codec switch (SelectCodec, as the link monitor's step-down sends:
 * docs/internals.md#bluetooth-link-monitor) has BlueZ clear the old transport, which closes the PCM's FIFO, so the
 * plugin answers -ENODEV, and set up a new one that bluealsa leaves idle, no encoder running, until a client opens
 * its PCM. So it is opened again, as at a song's start, and the write goes on into it: BT_REOPEN_TRIES looks
 * BT_REOPEN_WAIT_MS apart while the new PCM is not there yet. A headset that went away leaves none, and the song
 * fails as before, after the wait. The new transport's rate may differ (SBC's), so the resampler and the history
 * are made again, and a click playing in the old PCM is let go; 1 when the device's width changed too, as the rest
 * of the block in hand is in the old one. ponytail: the wait holds the play loop, so a stop or pause sent in it is
 * taken after it, at most BT_REOPEN_TRIES * BT_REOPEN_WAIT_MS later. */
enum { BT_REOPEN_TRIES = 50, BT_REOPEN_WAIT_MS = 100 };

static int bt_reopen(struct output *o) {
    struct pcm_format was = o->f;
    snd_pcm_close(o->pcm);
    o->pcm = NULL;
    o->clk_at = o->clk_len;
    for (int i = 0; i < BT_REOPEN_TRIES; ++i) {
        if (i) usleep(BT_REOPEN_WAIT_MS * 1000);
        o->f = o->in;
        if (alsa_open(o, o->way, 0) < 0) continue;
        free(o->hist);
        o->hist = NULL, o->hist_at = o->hist_n = 0;
        history(o);
        if (resampler(o) < 0) {
            snd_pcm_close(o->pcm);
            o->pcm = NULL;
            return -1;
        }
        fprintf(stderr, "q2player: bluealsa's PCM came back at %d Hz after %d looks\n", o->f.rate, i + 1);
        return o->f.bytes != was.bytes || o->f.is_float != was.is_float;
    }
    o->f = was;
    fprintf(stderr, "q2player: bluealsa's PCM did not come back\n");
    return -1;
}
#endif

static int sink_to(struct output *o, const uint8_t *data, int frames) {
#ifdef HAVE_ALSA
    if (o->pcm) {
        /* Off Bluetooth a PCM that ran dry plays on (alsa_sw): hw_ptr is past appl_ptr, and frames written there would
         * be lost until appl_ptr caught up, heard as the song jumping ahead. So appl_ptr moves to a period past hw_ptr,
         * over the silence ALSA left there: a gap, and the song carries on where it stopped (as Vamp's player does,
         * patch/peq_player.c ao_write). What the history holds leads up to the gap, so a click no longer rewinds into
         * it. Bluetooth's plugin stops at its own underrun (docs/internals.md#bluetooth-resampler): EPIPE below. */
        snd_pcm_sframes_t avail = o->way != 1 ? snd_pcm_avail(o->pcm) : 0;
        if (avail > (snd_pcm_sframes_t)o->ring) {
            /* on way 3 the car heard silence for what hw_ptr ran past, and hears the period skipped over now */
            if (o->way == 3) car_trace_dry((int64_t)avail - (int64_t)o->ring + (int64_t)o->period);
            snd_pcm_forward(o->pcm, (snd_pcm_uframes_t)avail - o->ring + o->period);
            o->hist_n = 0;
            if (o->way == 3 && !o->dry_ok) car_trace_underrun(); /* Car Audio's health (cartrace.c) */
        }
        o->dry_ok = 0;
        while (frames > 0) {
            /* The car's stream moves only while the car reads it, and a car begins to read some time after it asks
             * for audio (the BMW 2011 ms after, 2026-11-20: the one-second limit this had took that for the car gone,
             * and the song went to Bluetooth for good). So a write waits for room as long as the car file says the
             * car wants audio, looking at it every CAR_LOOK_MS: q2-iap removes it when the car stops (it changed
             * source) and q2car.sh when the cable comes out, and that ends the car's way. A stop or a skip (the
             * engine's cut, output_car_watch) ends the wait, the frames dropped, as the stop drops them. ponytail:
             * a pause or a seek waits until the car reads; the BMW reads from about 2 s after it asks. */
            snd_pcm_uframes_t ask = (snd_pcm_uframes_t)frames;
            if (o->way == 3) {
                while ((o->car_wait_r = snd_pcm_wait(o->pcm, CAR_LOOK_MS)) == 0) {
                    if (!output_car(NULL, 0, NULL)) return OUTPUT_CAR_GONE;
                    if (car_cut && *car_cut) return 0;
                }
                /* No more than the ring has room for: a blocking write of more writes the room and then waits in the
                 * kernel for the rest, which a car that stopped reading never makes, until 5.10's timeout (pcm_lib.c
                 * wait_for_avail, 10 s), deaf to a stop or a skip (run.py test_car_write_slow). The rest goes after
                 * the next wait, which looks at the car file and the cut. Room of 0 or an error: the write as
                 * asked, which answers the error. */
                snd_pcm_sframes_t room = snd_pcm_avail(o->pcm);
                if (room > 0 && room < frames) ask = (snd_pcm_uframes_t)room;
            }
            int64_t wt = o->way == 3 ? car_trace_now() : 0;
            errno = 0; /* a slow write's line says the writei's own */
            snd_pcm_sframes_t n = snd_pcm_writei(o->pcm, data, ask);
            if (o->way == 3) o->car_write_r = n, o->car_errno = errno, o->car_write_us += car_trace_now() - wt;
            if (n == -EINTR || n == -EAGAIN) continue;
            if (n == -EPIPE || n == -ESTRPIPE) { /* the buffer ran dry: start again here */
                if (o->way == 3) car_trace_dry(0);
                n = snd_pcm_prepare(o->pcm);
            }
            if (n == -ENODEV && o->way == 1) { /* bluealsa's PCM went away: bt_reopen */
                int r = bt_reopen(o);
                if (r) return r < 0 ? -1 : 0;
                continue;
            }
            if (n == -ENODEV && o->way == 3) return OUTPUT_CAR_GONE; /* the gadget, and its card, went */
            if (n == -ENODEV && o->way == 2) { say_gone(device(2, 0), "snd_pcm_writei", (int)n); return OUTPUT_GONE; } /* unplugged */
            if (n < 0) { /* the song's failure (engine.c FAILED); on a USB DAC with the call and its answer */
                if (o->way == 2) fprintf(stderr, "q2player: %s: snd_pcm_writei %ld (%s)\n", device(2, 0), (long)n, strerror((int)-n));
                return -1;
            }
            if (n == 0) continue;
            data += n * o->f.channels * o->f.bytes, frames -= n;
        }
        return 0;
    }
#endif
    if (!o->file) return -1; /* a Bluetooth PCM that did not come back (bt_reopen) */
    if (!output_present(o->way)) { say_gone(device(o->way, 0), "the card's node", -ENOENT); return OUTPUT_GONE; } /* a USB DAC unplugged */
    if (fwrite(data, (size_t)o->f.channels * o->f.bytes, frames, o->file) != (size_t)frames) return -1;
    int64_t due = (int64_t)(since(&o->t0) * o->speed * o->f.rate);
    if (due > o->written) { /* it ran dry: the clock starts again here */
        if (o->way == 3 && o->written) {
            car_trace_dry(due - o->written); /* the silence a device would have played */
            if (!o->dry_ok) car_trace_underrun();
        }
        clock_gettime(CLOCK_MONOTONIC, &o->t0);
        o->written = 0;
    }
    o->dry_ok = 0;
    o->written += frames;
    int64_t ahead = dev_delay(o) - (int64_t)o->f.rate * BUFFER_US / 1000000;
    if (ahead > 0) usleep((useconds_t)(ahead * 1000000 / o->f.rate / o->speed));
    return 0;
}

/* A write to the car's PCM that took CAR_SLOW_US or more, one line for it: where the time went, in snd_pcm_wait (the
 * car not reading, CAR_LOOK_MS at a time) or in snd_pcm_writei (the kernel's own wait), their last answers, errno
 * after the writei, and the PCM's state and room after it. The drive of 2026-11-23 had one write of 10.8 s across a
 * pause, a stop and a restart, and its trace line could not say which. */
enum { CAR_SLOW_US = 1000000 };
static const char *const PCM_STATES[] = {"OPEN", "SETUP", "PREPARED", "RUNNING", "XRUN", "DRAINING", "PAUSED", "SUSPENDED", "DISCONNECTED"};
static void car_slow(struct output *o, int frames, int64_t us) {
    char line[200];
    int st = -1;
    long avail = 0;
#ifdef HAVE_ALSA
    if (o->pcm) st = snd_pcm_state(o->pcm), avail = (long)snd_pcm_avail(o->pcm);
#endif
    int n = snprintf(line, sizeof line,
                     "q2player: a car write took %lld ms: %d frames, %lld ms waiting (snd_pcm_wait %d), %lld ms writing "
                     "(snd_pcm_writei %ld, errno %d), %s, avail %ld",
                     (long long)(us / 1000), frames, (long long)((us - o->car_write_us) / 1000), o->car_wait_r,
                     (long long)(o->car_write_us / 1000), o->car_write_r, o->car_errno,
                     st >= 0 && st < 9 ? PCM_STATES[st] : "no PCM", avail);
    plog('W', line, n < (int)sizeof line ? n : (int)sizeof line - 1);
}

/* Every write to the device, copied to the output tap (tap.c) on every way. On way 3 it is also timed and tapped for
 * the car's trace (docs/iphone-mode.md#car-trace). */
static int sink(struct output *o, const uint8_t *data, int frames) {
    pcm_tap_put(&o->f, data, frames);
    if (o->way != 3) return sink_to(o, data, frames);
    car_tap(data, (int64_t)frames * frame_bytes(o));
    int64_t t = car_trace_now();
    o->car_wait_r = o->car_errno = 0, o->car_write_r = 0, o->car_write_us = 0;
    int r = sink_to(o, data, frames);
    int64_t us = car_trace_now() - t;
    car_trace_write(frames, us);
    if (us >= CAR_SLOW_US) car_slow(o, frames, us);
    return r;
}

/* ---- the Clicker's click in the headphones (docs/internals.md#key-tone): the payload sends {mcl-click\KIND} while
 * a song plays, and the click is mixed into what is about to be heard. The PCM holds up to half a second, so the
 * output rewinds to CLICK_LEAD_MS past what is playing, mixes the click into its own copy of those frames and writes
 * them again; where nothing can be rewound (no backlog, a device that cannot rewind) the click starts with the next
 * frames written. The mix makes room for the click: y = x * (1 - e) + e * s, with e the click's envelope (at most
 * its level) and |s| <= 1, so a sample within full scale stays within it and the conversion never clips. The level
 * is its own, CLICK_DB of the PCM's full scale whatever the volume: software volume (Bluetooth with Link Volume off,
 * a USB DAC with mcl-softvolflag 1) scales the music before the mix and never the click. What comes after the PCM
 * still scales both: the headphone DAC's hardware volume, a USB DAC's own, a headset's own (its AVRCP absolute
 * volume, which Link Volume sets from the Q2's). With nothing playing the click is played in silence of its own
 * (output_idle_*, engine.c click_session). */
#define CLICK_LEAD_MS 20 /* past the PCM's place, for the rewind and rewrite to land before it gets there */
#define CLICK_DB (-20.0) /* the click's peak, dB of full scale: tune by ear */
#define CLICK_ATTACK_MS 0.25 /* the envelope's rise and fall, so the music's dip under it has no step either */
#define CLICK_TAPER_MS 0.5
/* Rockbox's iPod piezo (firmware/target/arm/ipod/piezo.c, Hz = 91225 / period): a wheel click about 1,000 Hz for
 * 4 ms, a button's about 1,820 Hz for 3 ms, an alert's about 2,280 Hz for 200 ms. Each is a pulse exciting a small
 * resonator, a ringing piezo: a sine at its pitch dying away with time constant decay_ms. */
static const struct { double hz, ms, decay_ms; } SOUNDS[] = {
    [CLICK_WHEEL] = {1000, 4, 1.2}, [CLICK_BUTTON] = {1820, 3, 0.9}, [CLICK_BEEP] = {2280, 200, 60}};

/* The plug-in chirp (patch/charging.c, design/mockups/charge_chimes2.py's "8-click-up-chirp"): a CHIRP_TICK_MS
 * tick of one-pole low-passed noise (a seeded LCG's, so every chirp is the same), then CHIRP_LO_HZ and CHIRP_HI_HZ
 * blips with a 2 ms rise, their second harmonic at 0.15, at CHIRP_LO_MS and CHIRP_HI_MS; its peak CHIRP_DB. */
#define CHIRP_DB (-12.0)
#define CHIRP_MS 340
#define CHIRP_TICK_MS 3.0
#define CHIRP_TICK_SMOOTH 0.7 /* the low-pass's share of each new noise sample: near 1 a crisp tick */
#define CHIRP_TICK_AMP 0.7
#define CHIRP_LO_HZ 880.0
#define CHIRP_LO_MS 30.0
#define CHIRP_LO_LEN_MS 180.0
#define CHIRP_LO_DECAY_MS 50.0
#define CHIRP_HI_HZ 1320.0
#define CHIRP_HI_MS 90.0
#define CHIRP_HI_LEN_MS 250.0
#define CHIRP_HI_DECAY_MS 70.0
#define CHIRP_BLIP_AMP 0.6
#define CHIRP_RISE_MS 2.0

/* The chirp's raw sample i at rate and the most it could be (its components' envelopes), before the level. tick is
 * the tick's low-pass state, seed its noise's. */
static double chirp_at(int i, int rate, double *tick, uint32_t *seed, double *bound) {
    double t = (double)i / rate, y = 0, e = 0;
    if (t < CHIRP_TICK_MS / 1000) {
        *seed = *seed * 1664525u + 1013904223u; /* uniform noise in -1..1 */
        double noise = (double)(*seed >> 8) / (1 << 23) - 1;
        *tick = i ? CHIRP_TICK_SMOOTH * noise + (1 - CHIRP_TICK_SMOOTH) * *tick : 0;
        double env = CHIRP_TICK_AMP * exp(-t * 4000 / CHIRP_TICK_MS);
        y += env * *tick, e += env;
    }
    static const double blips[2][4] = {{CHIRP_LO_HZ, CHIRP_LO_MS, CHIRP_LO_LEN_MS, CHIRP_LO_DECAY_MS},
                                       {CHIRP_HI_HZ, CHIRP_HI_MS, CHIRP_HI_LEN_MS, CHIRP_HI_DECAY_MS}};
    for (int b = 0; b < 2; ++b) {
        double u = t - blips[b][1] / 1000;
        if (u < 0 || u >= blips[b][2] / 1000) continue;
        double env = CHIRP_BLIP_AMP * fmin(u * 1000 / CHIRP_RISE_MS, 1) * exp(-u * 1000 / blips[b][3]),
               ph = 2 * M_PI * blips[b][0] * u;
        y += env * (sin(ph) + 0.15 * sin(2 * ph)), e += env * 1.15;
    }
    *bound = e;
    return y;
}

/* The sound kind at rate, as samples (clk) and its envelope (duck), made at the first use of each. */
static int click_table(int kind, int rate, const float **clk, const float **duck) {
    static struct { int rate, len; float *s, *e; } made[CLICK_CHARGE];
    if (kind < CLICK_WHEEL || kind > CLICK_CHARGE) return 0;
    __typeof__(made[0]) *m = &made[kind - CLICK_WHEEL];
    if (m->rate != rate) {
        int len = (int)((kind == CLICK_CHARGE ? CHIRP_MS : SOUNDS[kind].ms) * rate / 1000);
        float *s = realloc(m->s, (size_t)len * sizeof *s), *e = realloc(m->e, (size_t)len * sizeof *e);
        if (s) m->s = s;
        if (e) m->e = e;
        if (!s || !e || len <= 0) return m->rate = 0, 0;
        if (kind == CLICK_CHARGE) { /* normalized to its peak: |s| <= e, so the mix's room holds */
            double tick = 0, peak = 0, bound, level = pow(10, CHIRP_DB / 20);
            uint32_t seed = 7;
            for (int i = 0; i < len; ++i) {
                double y = chirp_at(i, rate, &tick, &seed, &bound);
                s[i] = (float)y, e[i] = (float)bound, peak = fmax(peak, fabs(y));
            }
            for (int i = 0; i < len; ++i)
                s[i] = (float)(level * s[i] / peak), e[i] = (float)(level * fmin(e[i] / peak, 1));
        } else {
            double level = pow(10, CLICK_DB / 20), rise = CLICK_ATTACK_MS * rate / 1000, fall = CLICK_TAPER_MS * rate / 1000;
            for (int i = 0; i < len; ++i) {
                double t = (double)i / rate, env = level * exp(-t * 1000 / SOUNDS[kind].decay_ms);
                if (i < rise) env *= 0.5 - 0.5 * cos(M_PI * i / rise);
                if (len - i < fall) env *= 0.5 - 0.5 * cos(M_PI * (len - i) / fall);
                e[i] = (float)env, s[i] = (float)(env * sin(2 * M_PI * SOUNDS[kind].hz * t));
            }
        }
        m->rate = rate, m->len = len;
    }
    *clk = m->s, *duck = m->e;
    return m->len;
}

/* The click, from where it is, into frames of data in place, in the output's format: x * (1 - e) + c, the music x
 * already through the software volume, the click c at its own level. */
static void click_mix(struct output *o, uint8_t *data, int frames) {
    int ch = o->f.channels;
    for (int i = 0; i < frames && o->clk_at < o->clk_len; ++i, ++o->clk_at) {
        float g = 1 - o->duck[o->clk_at], c = o->clk[o->clk_at];
        for (int k = 0; k < ch; ++k) {
            int j = i * ch + k;
            float cg = c;
            if (o->f.is_float) ((float *)data)[j] = ((float *)data)[j] * g + cg;
            else if (o->f.bytes == 2) {
                float v = (((int16_t *)data)[j] * (1.0f / 32768) * g + cg) * 32768;
                ((int16_t *)data)[j] = v >= 32767 ? 32767 : v <= -32768 ? -32768 : (int16_t)lrintf(v);
            } else if (o->f.bytes == 3) {
                uint8_t *d = data + 3 * j;
                int32_t x = (int32_t)((uint32_t)d[0] << 8 | (uint32_t)d[1] << 16 | (uint32_t)d[2] << 24) >> 8;
                double v = (x * (1.0 / 8388608.0) * g + cg) * 8388608.0;
                int32_t w = v >= 8388607.0 ? 8388607 : v <= -8388608.0 ? -8388608 : (int32_t)llrint(v);
                d[0] = (uint8_t)w, d[1] = (uint8_t)(w >> 8), d[2] = (uint8_t)(w >> 16);
            } else {
                double v = (((int32_t *)data)[j] * (1.0 / 2147483648.0) * g + cg) * 2147483648.0;
                ((int32_t *)data)[j] = v >= 2147483647.0 ? 2147483647 : v <= -2147483648.0 ? INT32_MIN : (int32_t)llrint(v);
            }
        }
    }
}

static uint8_t *scratch(struct output *o, int frames) {
    if (frames > o->tmp_cap) {
        uint8_t *t = realloc(o->tmp, (size_t)frames * frame_bytes(o));
        if (!t) return NULL;
        o->tmp = t, o->tmp_cap = frames;
    }
    return o->tmp;
}

/* frames of data, the song's (NULL: as many of the resampler's tail, in o->rbuf), out through the volume and the
 * resampler to the device, with a click that is sounding mixed in. */
/* ---- way 3 at another of the car's rates (docs/iphone-mode.md#per-song-rate) ---- */

/* The way-3 PCM opened again at rate, its old one closed: what it held is gone, and the trace's window ends with it.
 * The caller makes the resampler for the new rate. */
static int car_reopen(struct output *o, int rate) {
    car_trace_tick(o, 1);
    o->f.rate = rate;
#ifdef HAVE_ALSA
    if (o->pcm) {
        snd_pcm_close(o->pcm);
        o->pcm = NULL;
        if (alsa_open(o, 3, 0) < 0) return -1;
    } else
#endif
    {
        const char *dir = getenv("Q2PLAYER_PCM");
        if (o->file) fclose(o->file), o->file = NULL;
        o->written = 0;
        if (!dir || sink_open(o, dir, 3) < 0) return -1;
    }
    free(o->hist);
    o->hist = NULL, o->hist_at = o->hist_n = 0, o->clk_at = o->clk_len;
    history(o);
    o->dry_ok = 1; /* the new PCM starting is not an underrun */
    car_trace_open(o->f.rate, car_path());
    return 0;
}

/* The last song's tail played out, then CAR_PAD_MS of silence, so the car hears silence while the rate changes up
 * to the close; the wait bounded, as output_close's */
static void car_quiet(struct output *o) {
    int pad = o->f.rate * CAR_PAD_MS / 1000;
    void *z = calloc((size_t)pad, (size_t)frame_bytes(o));
    if (z) remember(o, z, pad), sink(o, z, pad), free(z);
    int64_t d = dev_delay(o) - pad;
    if (d > 0) {
        long us = (long)(d * 1000000 / o->f.rate / o->speed);
        usleep((useconds_t)(us < CAR_DRAIN_MS * 1000 ? us : CAR_DRAIN_MS * 1000));
    }
}

/* At a song's start, the car moved to want: the tail drained to silence, want asked for and waited for, and the PCM
 * opened again at the car's rate. A car that does not follow keeps its rate and the song is resampled to it. */
static int car_switch(struct output *o, int want) {
    int from = o->f.rate, waited;
    car_quiet(o);
    int64_t t = car_trace_now();
    int r = car_follow(want, &waited);
    if (!r || r == from) {
        car_rate_line("not followed, resampled", from, want, waited, -1);
        return 0;
    }
    if (car_reopen(o, r) < 0) return -1;
    car_rate_line(r == want ? "followed" : "set by the car", from, r, waited,
                  (int)((car_trace_now() - t) / 1000) + CAR_PAD_MS);
    return 0;
}

/* The player is the car's rate's source of truth, but the car may set one by itself (late, after car_switch gave
 * up, or unasked): every CAR_CHECK_MS a song playing on way 3 looks at P_RATE_FILE, and a PCM at another rate
 * than the car's is taken back unheard (off speed) and opened again at the car's, the song resampled to it. */
static int car_check(struct output *o) {
    static int64_t at;
    int64_t now = car_trace_now();
    if (now - at < CAR_CHECK_MS * 1000LL) return 0;
    at = now;
    int r = car_p_rate();
    if (!r || r == o->f.rate) return 0;
    int from = o->f.rate;
    /* the rate last asked for is the car's choice no longer: not waited for again (q2-iap sends it only when it
     * changes, so the car is not told it again) until the player asks for another */
    car_refused = car_asked;
#ifdef HAVE_ALSA
    if (o->pcm) {
        snd_pcm_sframes_t can = snd_pcm_rewindable(o->pcm);
        if (can > 0 && snd_pcm_rewind(o->pcm, (snd_pcm_uframes_t)can) > 0) car_trace_back(can), pcm_tap_back(can);
    }
#endif
    if (car_reopen(o, r) < 0 || resampler(o) < 0) return -1;
    car_rate_line("set by the car, reopened", from, r, 0, -1);
    return 0;
}

int output_write(struct output *o, const uint8_t *data, int frames) {
    uint8_t *t;
    if (o->way == 3 && data && car_check(o) < 0) return -1;
    if ((frames = convert(o, data, frames, &data)) <= 0) return frames;
    if (o->clk_at < o->clk_len && (t = scratch(o, frames))) { /* a click still sounding goes on in these frames */
        memcpy(t, data, (size_t)frames * frame_bytes(o));
        click_mix(o, t, frames);
        data = t;
    }
    remember(o, data, frames);
    return sink(o, data, frames);
}

/* Frames back from the newest that can be written again, at most want. */
static int rewind_by(struct output *o, int want) {
#ifdef HAVE_ALSA
    if (o->pcm) {
        snd_pcm_sframes_t can = snd_pcm_rewindable(o->pcm);
        if (can < want) want = can > 0 ? (int)can : 0;
        snd_pcm_sframes_t got = want ? snd_pcm_rewind(o->pcm, (snd_pcm_uframes_t)want) : 0;
        pcm_tap_back(got);
        return got > 0 ? (int)got : 0;
    }
#endif
    fflush(o->file);
    if (fseeko(o->file, -(off_t)want * frame_bytes(o), SEEK_END)) return 0;
    /* the file ends where the next write goes, as a PCM's frames past appl_ptr are not there to be played: a held
     * song's frames (output_hold) taken back are gone from it until written again */
    if (ftruncate(fileno(o->file), ftello(o->file))) return 0;
    o->written -= want;
    pcm_tap_back(want);
    return want;
}

int output_click(struct output *o, int kind) {
    if (!o || o->f.dsd || !(o->clk_len = click_table(kind, o->f.rate, &o->clk, &o->duck))) return -1; /* not into DSD's bits */
    o->clk_at = 0;
    int want = (int)(dev_delay(o) - (int64_t)o->f.rate * CLICK_LEAD_MS / 1000), fb = frame_bytes(o);
    if (want > o->hist_n) want = o->hist_n;
    if (want <= 0 || !scratch(o, want)) return 0; /* the click starts with the next frames written */
    int back = rewind_by(o, want);
    if (!back) return 0;
    int from = (o->hist_at - back + o->hist_cap) % o->hist_cap, m = back < o->hist_cap - from ? back : o->hist_cap - from;
    memcpy(o->tmp, o->hist + (size_t)from * fb, (size_t)m * fb);
    memcpy(o->tmp + (size_t)m * fb, o->hist, (size_t)(back - m) * fb);
    click_mix(o, o->tmp, back);
    o->hist_at = from, o->hist_n -= back; /* written again, so remembered again */
    remember(o, o->tmp, back);
    return sink(o, o->tmp, back);
}

/* ---- a click with nothing playing (docs/internals.md#key-tone): engine.c's click_session opens the output for it (a
 * song stopped) or takes the paused song's (output_hold), then keeps IDLE_AHEAD_MS of silence written ahead of what
 * plays, each click mixed into that silence as into a song (output_click's rewind) or starting with the next of it,
 * until the session ends (output_idle_end) */
#define IDLE_AHEAD_MS 80 /* more than a period (31 ms asked; the DAC's driver caps one at 4096 bytes, 19 ms at
                          * 44.1 kHz 16-bit, docs/player.md), so the PCM starts, and room for engine.c's 30 ms ticks */

/* The frames written and not yet heard, the newest n of the history (d of them were unheard), into o->held; the PCM
 * emptied, running. */
static void hold(struct output *o, int64_t d) {
    if (!o || o->holding) return;
    int fb = frame_bytes(o);
    int n = d < o->hist_n ? (int)d : o->hist_n;
    if (n > 0 && (o->held = malloc((size_t)n * fb))) { /* no memory: the resume goes on from after them, a jump */
        int from = (o->hist_at - n + o->hist_cap) % o->hist_cap, m = n < o->hist_cap - from ? n : o->hist_cap - from;
        memcpy(o->held, o->hist + (size_t)from * fb, (size_t)m * fb);
        memcpy(o->held + (size_t)m * fb, o->hist, (size_t)(n - m) * fb);
        o->held_n = n;
    }
    o->hist_n = 0, o->clk_at = o->clk_len, o->holding = 1, o->paused = 0;
#ifdef HAVE_ALSA
    if (o->pcm) {
        pcm_tap_back(d);              /* the frames not yet heard, written again at the resume */
        snd_pcm_drop(o->pcm); /* out of its pause too */
        snd_pcm_prepare(o->pcm);
        return;
    }
#endif
    rewind_by(o, (int)d); /* the file sink: those frames are written again at the resume */
    clock_gettime(CLOCK_MONOTONIC, &o->t0);
    o->written = 0;
}
void output_hold(struct output *o) {
    if (o) hold(o, dev_delay(o));
}

/* What is ahead of what plays in the PCM's own ring, in the song's frames. Not snd_pcm_delay: bluealsa's plugin adds
 * the headset's own delay to it (its Delay, 280 ms on the Pixel Buds; libasound_module_pcm_bluealsa.so 0x5b64)
 * whether the PCM has started or not, more than IDLE_AHEAD_MS, so a fill measured by it wrote nothing on Bluetooth
 * and the click was never heard. */
static int64_t idle_ahead(struct output *o) {
#ifdef HAVE_ALSA
    if (o->pcm) {
        snd_pcm_sframes_t room = snd_pcm_avail(o->pcm);
        int64_t d = room < 0 ? 0 : room >= (snd_pcm_sframes_t)o->ring ? 0 : (int64_t)o->ring - room;
        if (o->in.rate != o->f.rate) d = d * o->in.rate / o->f.rate;
        return d + (o->swr ? swr_get_delay(o->swr, o->in.rate) : 0);
    }
#endif
    return output_delay(o);
}

int output_idle_fill(struct output *o) {
    if (!o || o->f.dsd) return 0;
    int fb = o->in.channels * o->in.bytes, n = (int)((int64_t)o->in.rate * IDLE_AHEAD_MS / 1000 - idle_ahead(o));
    if (n <= 0 || grow((void **)&o->zbuf, &o->zcap, n * fb, 1)) return 0;
    memset(o->zbuf, 0, (size_t)n * fb); /* silence in the song's format: zero is silence in each, float's too */
    o->idle = 1;
    return output_write(o, o->zbuf, n) < 0 ? 0 : n;
}

int output_idle_click(struct output *o, int kind) {
    if (!o || o->f.dsd) return 0;
    o->idle = 1;
    output_click(o, kind);
    return output_idle_fill(o);
}

/* The silence not yet heard dropped; a held song's PCM left as paused, empty, for output_pause's resume. */
void output_idle_end(struct output *o) {
    if (!o || !o->idle) return;
    o->idle = 0, o->clk_at = o->clk_len, o->hist_n = 0;
#ifdef HAVE_ALSA
    if (o->pcm) {
        pcm_tap_back(dev_delay(o)); /* the silence not yet heard */
        snd_pcm_drop(o->pcm);
        snd_pcm_prepare(o->pcm);
        o->paused = o->holding;
        return;
    }
#endif
    rewind_by(o, (int)dev_delay(o));
    clock_gettime(CLOCK_MONOTONIC, &o->t0);
    o->written = 0, o->paused = o->holding;
    o->paused_at = o->t0;
}

void output_pause(struct output *o, int on) {
    if (!on && o->holding) { /* the resume of a song held for a click: its frames not yet heard, then on as before */
        output_idle_end(o);
        o->holding = 0, o->paused = 0;
        clock_gettime(CLOCK_MONOTONIC, &o->t0);
        o->written = 0;
        if (o->held_n) remember(o, o->held, o->held_n), sink(o, o->held, o->held_n);
        free(o->held);
        o->held = NULL, o->held_n = 0;
        return;
    }
    o->dry_ok = 1;
    if (o->paused == on) return;
    o->paused = on;
#ifdef HAVE_ALSA
    if (o->pcm) {
        /* A device that cannot pause (docs/player.md#pause): the car's gadget advertises a pause its trigger refuses
         * (u_audio.c uac_pcm_trigger has no pause case: -EINVAL, its read position put back to the ring's start, so
         * the car heard the ring again, out of order). Its frames not yet heard are held, as for a click (hold, with
         * the delay read before the refused pause moved it), and the PCM stopped, which the gadget then sends as
         * silence, as an iPhone's stream runs on silent through a pause; the resume writes them again (above). */
        int64_t d = on ? dev_delay(o) : 0;
        int r = snd_pcm_pause(o->pcm, on);
        if (r < 0 && on) {
            fprintf(stderr, "q2player: the device cannot pause (%s): %lld frames held, the PCM stopped\n", snd_strerror(r),
                    (long long)d);
            hold(o, d);
            o->paused = 1;
        } else if (r < 0) { /* a resume refused: the PCM was never paused, and the next write goes on as ever */
            fprintf(stderr, "q2player: the device refused the resume (%s)\n", snd_strerror(r));
        }
        return;
    }
#endif
    if (on) clock_gettime(CLOCK_MONOTONIC, &o->paused_at);
    else {
        double gap = since(&o->paused_at);
        o->t0.tv_sec += (time_t)gap;
        o->t0.tv_nsec += (long)((gap - (time_t)gap) * 1e9);
        if (o->t0.tv_nsec >= 1000000000) o->t0.tv_sec++, o->t0.tv_nsec -= 1000000000;
    }
}

void output_drop(struct output *o) {
    o->dry_ok = 1;
    o->hist_n = 0, o->clk_at = o->clk_len; /* what was written is gone, and a click with it */
    free(o->held); /* and a paused song's frames held for a click (output_hold) */
    o->held = NULL, o->held_n = o->holding = o->idle = 0;
    resampler(o);                          /* and what the resampler held back of it */
#ifdef HAVE_ALSA
    if (o->pcm && o->way == 3) {
        /* Taken back rather than dropped, the stream runs on and ALSA's silence fills the room (alsa_sw), as an
         * iPhone's runs on: no stop and start for the car. A stop is silent too: the gadget's STOP zeroes its packet
         * buffers, which its completion then sends again and again (u_audio.c uac_pcm_trigger, read 2026-11-22) */
        snd_pcm_sframes_t can = snd_pcm_rewindable(o->pcm);
        if (can > 0 && snd_pcm_rewind(o->pcm, (snd_pcm_uframes_t)can) > 0) car_trace_back(can), pcm_tap_back(can);
        o->paused = 0;
        return;
    }
    if (o->pcm) {
        pcm_tap_back(dev_delay(o)); /* what the device held, never heard */
        snd_pcm_drop(o->pcm);
        snd_pcm_prepare(o->pcm);
        o->paused = 0;
        return;
    }
#endif
    fflush(o->file);
    clock_gettime(CLOCK_MONOTONIC, &o->t0);
    o->written = 0, o->paused = 0;
}

void output_drain(struct output *o) {
    int n = o->swr ? swr_get_out_samples(o->swr, 0) : 0; /* the resampler's tail is the song's too */
    if (n > 0 && !grow((void **)&o->rbuf, &o->rcap, n * o->f.channels, sizeof(float)) &&
        (n = swr_convert(o->swr, (uint8_t **)&o->rbuf, n, NULL, 0)) > 0)
        output_write(o, NULL, n);
#ifdef HAVE_ALSA
    if (o->pcm) { snd_pcm_drain(o->pcm); return; }
#endif
    fflush(o->file);
    int64_t d = dev_delay(o);
    if (d > 0) usleep((useconds_t)(d * 1000000 / o->f.rate / o->speed));
}

void output_close(struct output *o) {
    if (!o) return;
#ifdef HAVE_ALSA
    if (o->pcm && o->way == 3) {
        /* the last packets silence, played out before the close (the gadget's STOP then zeroes its packets) */
        snd_pcm_sframes_t can = snd_pcm_rewindable(o->pcm);
        if (can > 0 && snd_pcm_rewind(o->pcm, (snd_pcm_uframes_t)can) > 0) car_trace_back(can), pcm_tap_back(can);
        size_t bytes = (size_t)o->period * 2 * o->f.channels * o->f.bytes;
        void *z = bytes ? calloc(1, bytes) : NULL;
        if (z && snd_pcm_wait(o->pcm, CAR_WAIT_MS) > 0 && snd_pcm_writei(o->pcm, z, o->period * 2) > 0)
            pcm_tap_put(&o->f, z, (int)o->period * 2);
        free(z);
        /* played out with a bound, not a drain: a car that stops reading meanwhile would hold a drain for ever */
        snd_pcm_sframes_t d = 0;
        if (!snd_pcm_delay(o->pcm, &d) && d > 0 && o->f.rate > 0) {
            long us = (long)((int64_t)d * 1000000 / o->f.rate);
            usleep((useconds_t)(us < 600000 ? us : 600000));
        }
    }
#endif
    if (o->way == 3) car_trace_tick(o, 1); /* the last window's line (docs/iphone-mode.md#car-trace) */
#ifdef HAVE_ALSA
    if (o->pcm) snd_pcm_close(o->pcm);
#endif
    if (o->file) fclose(o->file);
    swr_free(&o->swr);
    free(o->fbuf), free(o->rbuf), free(o->dbuf), free(o->sbuf);
    free(o->hist);
    free(o->tmp);
    free(o->held), free(o->zbuf);
    free(o);
}
