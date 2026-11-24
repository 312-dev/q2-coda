/* q2player: the Q2's audio player, a replacement for stock's hciplayer that speaks its socket protocol
 * (docs/player.md) and decodes with FFmpeg's libraries. Architecture: docs/player-plan.md#architecture. */
#ifndef Q2PLAYER_H
#define Q2PLAYER_H
#include <pthread.h>
#include <stdint.h>

#ifndef PEQ_ROOT /* player/Makefile passes it: "" on the Q2, a host test's own directory (".") */
#define PEQ_ROOT ""
#endif

#define URL_MAX 1564 /* stock's command buffer, 0x61c */

/* set-songurl, set-nexturl and set-gapless, as demo sends them. */
struct song {
    char url[URL_MAX];
    int start;     /* ss=N: where to begin, in seconds (a CUE track, a resumed song) */
    int cache_kb;  /* stream cache for a URL; -1 on a preload */
    int cache_pct; /* how full the cache is before playing starts */
    int gapless;   /* demo's Gapless setting */
    int hw;        /* demo's hardware type, 17 on a Q2; absent from set-gapless */
    int dsd;       /* how a DSD file goes out, DSD_*: set by the player for the output it will play on */
};

/* What media-info reports. */
struct info {
    int bitrate; /* bits a second */
    int rate;    /* Hz */
    int length;  /* whole seconds */
    int bits;
    char codec[8];
};

enum { IDLE, OPENING, PLAYING };          /* state: player-status answers "playing" only in PLAYING */
enum { ERR_NONE, ERR_OTHER, ERR_UNSUPPORTED, ERR_MISSING };

/* Shared between the command thread (ipc.c) and the playback thread (engine.c), under mu. */
struct ctl {
    pthread_mutex_t mu;
    pthread_cond_t wake;        /* the playback thread waits here when idle or paused */
    /* requests, cleared by the playback thread as it takes them */
    int play, next, stop, preload, close_preload, seek;
    struct song play_song, preload_song;
    int seek_to;
    int paused;
    int play_paused; /* a pause asked after play_song's command, before this thread took it: it opens paused */
    /* the stop waiting came before play_song's command, with a song open or opening (ipc.c set-songurl): it ends that
     * song and leaves play_song to be taken next, where a stop after the command forgets it too */
    int stop_first;
    /* settings demo pushes; speed and mqa are stored and read by nothing (stock reads speed, docs/player.md) */
    int outputway, volume, softvol, dsdmode, replaygain, speed, explay, mqa, eq_on;
    int scan; /* mcl-scan: a held Previous or Next heard at speed, in hundredths, below 0 backwards; 0 none */
    int volume_changed; /* an mcl-setvol since the playback thread last looked */
    float eq[10];
    int eq_changed; /* an mcl-seteq since the playback thread last looked: the EQ state on disk is new */
    int click;      /* an mcl-click to sound, CLICK_*: mixed into a song playing, else on its own (engine.c click_session) */
    int cut;        /* an mcl-stop or set-nexturl since the playback thread began opening a song: that open gives up */
    /* mcl-dropcar (or mcl-carrelease, or SIGUSR2: docs/player.md#releasing-the-car): the car's gadget is about to go,
     * so a car PCM held paused or between songs is closed at once; cleared as the playback thread takes it */
    int car_release;
    /* status */
    int state, has_info, position;
    struct info info;
    int uv[2], spectrum[12];
};
extern struct ctl ctl;

/* ipc.c */
int ipc_connect(const char *path);
void ipc_send(const char *text);
void ipc_push_open(const char *path);         /* the status push's socket, demo's at path */
void ipc_status_note(const struct ctl *c);    /* mu held: push when what the status queries answer has changed */
void ipc_event(const char *text);             /* an event demo did not ask for, sent and pushed */
void plog(char level, const char *msg, int n); /* main.c: a line in the unified log, source "player" */
void *ipc_thread(void *);

/* source.c: one open file or stream, decoded to interleaved PCM in the format the output is opened with. */
/* S16_LE (2 bytes), S32_LE (4) or FLOAT_LE (4, is_float); S24_3LE (3) as a device's. dsd: DSD's own bits, which
 * nothing may change: DoP frames in S24_3LE (3) or Native's DSD_U32_BE (4), dsd.c */
enum { DSD_PCM, DSD_DOP, DSD_NATIVE };
struct pcm_format { int rate, channels, bytes, is_float, dsd; };
struct source;
/* cancel, when not NULL, is read during the open: once it is nonzero a blocking read gives up and the open fails. */
struct source *source_open(const struct song *song, int *error, const int *cancel);
void source_keep(struct source *); /* it no longer reads the cancel flag it was opened with, which may now be freed */
void source_close(struct source *);
const struct pcm_format *source_format(const struct source *);
void source_info(const struct source *, struct info *);
/* The next block: frames and where they are, 0 at the end, below 0 on a decode error that ends the file. */
int source_read(struct source *, const uint8_t **data);
int source_seek(struct source *, int seconds);
int source_seek_frame(struct source *, int64_t frame); /* frame of the output's rate */
float source_replaygain(const struct source *, int mode); /* mcl-replaygain's scale for this file, 1 for none */
int64_t source_frames(const struct source *); /* frames handed out since the start or the last seek, plus its target */
struct stream *source_stream(const struct source *); /* NULL for a file on the card */

/* stream.c: a URL's bytes, through demo's cache, and QQ Music's decryption (qq.c), for libavformat to read */
struct stream;
struct AVIOContext;
int stream_is_url(const char *url);
/* NULL with *error on failure; cancel as source_open's */
struct stream *stream_open(const struct song *, const int *cancel, int *error);
void stream_keep(struct stream *); /* as source_keep */
struct AVIOContext *stream_avio(struct stream *);
void stream_close(struct stream *);
void stream_playing(struct stream *); /* the playing song's: its cache is what get_cache_buffer reports */
int stream_cache_fill(void);         /* get_cache_buffer's answer: the playing song's cache fill in percent, or 0 */
/* qq.c: QQ Music's songs, a URL with qm_songid=ID; NULL when it has none or it cannot be decrypted (read as it is) */
struct qq;
struct qq *qq_open(const char *url);
int qq_decrypt(struct qq *, int64_t offset, uint8_t *data, int n); /* in place, by the bytes' offset in the file */
void qq_close(struct qq *);

/* sacd.c: an SACD image's stereo area as a DSDIFF file for libavformat */
struct sacd;
int sacd_is_image(const char *path); /* by its name: .iso */
struct sacd *sacd_open(const char *path, int *error);
struct AVIOContext *sacd_avio(struct sacd *);
void sacd_close(struct sacd *);

/* grow.c: a card app's song still arriving in place, read with a wait at the end of its bytes */
struct grow;
int grow_arriving(const char *url); /* the app's .stream says url is still arriving */
/* the same, with the bytes on the card and the app's total (0 unknown) and length in ms (0 unknown) */
int grow_have(const char *url, int64_t *have, int64_t *total, int64_t *ms);
struct grow *grow_open(const char *url, int *error);
struct AVIOContext *grow_avio(struct grow *);
void grow_seeking(struct grow *, int on); /* on while a seek searches: only the bytes there, no wait at their end */
void grow_playing(struct grow *);         /* opened: a stall from here on waits for a refill (REFILL_S) */
void grow_close(struct grow *);

/* feed.c: other sources' sound for the car (docs/iphone-mode.md#other-sources-over-usb): a FIFO read into a ring */
#define FEED_RATE 44100
void feed_start(void);
int feed_frames(void);                   /* frames waiting */
int feed_take(uint8_t *out, int frames); /* up to frames of 16-bit stereo; how many */
void feed_pulling(void);                 /* the car takes it, the ring dry: keep the source paced */
int64_t feed_heard_ms(void);             /* since the last frames came, -1 never */

/* output.c */
struct output;
struct output *output_open(const struct pcm_format *, int outputway, int hw);
/* Whether an open output goes on with format f on outputway (a gapless change): Bluetooth takes the same channels
 * and width at any rate and resamples it; the car the same channels at any rate and width, its PCM opened again at
 * the car's next rate first (output_car_song); the DAC and a USB DAC any song that would go out in the format the
 * PCM is open at (a fixed-rate dongle: every PCM song, resampled), DSD the same format. 1 when it does, and f is then
 * what is written. */
int output_take(struct output *, const struct pcm_format *f, int outputway);
/* Software volume, mcl-setvol's L << 8 | R by stock's law (output.c); 0x6464, 100 each, is unity */
void output_volume(struct output *, int volume);
void volume_gains(int volume, float gain[2]); /* left, right */
int output_write(struct output *, const uint8_t *data, int frames);
/* output_write's answer when the car's way ended under a write: its file removed (the car stopped, the cable came out)
 * or its card gone (way 3, docs/iphone-mode.md#car-audio) */
#define OUTPUT_CAR_GONE (-2)
/* output_write's answer when a USB DAC (way 2) was unplugged under a write; output_gone is 1 after an output_open that
 * failed because it is not there; output_present says whether way's device is there to open (a USB DAC's card node),
 * for a wait for one to come back (engine.c output_back). Each is logged once until the device is opened again. */
#define OUTPUT_GONE (-3)
int output_gone(void);
int output_present(int outputway);
/* Car Audio's output, way 3 (Q2_CAR_USB: every image's player): 1 while the iPhone gadget's car wants USB audio, with its
 * ALSA device and rate when asked; output_car_gone forgets that until the car asks again. */
int output_car(char *device, unsigned cap, unsigned *rate);
void output_car_gone(void);
void output_car_watch(const volatile int *cut); /* the engine's cut flag: a stop or a skip ends a wait for the car */
/* The song about to start on way 3 is at rate: the next output_open or output_take there asks the car for its rate
 * (car_rate_for) and opens the PCM again at the rate the car then plays at (docs/iphone-mode.md#per-song-rate). keep:
 * a crossfade carries the last song's tail into it, and the car's rate stays as it is. */
void output_car_song(int rate, int keep);
int car_rate_for(int rate); /* 32000, 44100 or 48000 */
int64_t output_delay(struct output *); /* frames written and not yet played, at the rate written */
int64_t output_held(struct output *);  /* a paused song's frames taken back from the PCM and held, at the rate written */
void output_levels(struct output *, long *avail, long *delay); /* the device's own, in its frames */
void output_pause(struct output *, int on);
void output_drop(struct output *);     /* forget what is buffered: a seek, a skip, a stop */
void output_drain(struct output *);    /* play what is buffered to its end */
void output_close(struct output *);
enum { CLICK_WHEEL = 1, CLICK_BUTTON, CLICK_BEEP, CLICK_CHARGE }; /* mcl-click's wheel, button, beep and charge */
int output_click(struct output *, int kind); /* mixed into what is about to be heard */
/* A click with nothing playing (docs/internals.md#key-tone; engine.c click_session). output_hold: a paused song's
 * frames not yet heard taken back (kept, written again by output_pause's resume) and the PCM left empty, running. Then
 * output_idle_click for each click, output_idle_fill to keep silence ahead of what plays, and output_idle_end, which
 * drops that silence: a held PCM is paused again as far as the song is concerned. The click and the fill answer the
 * frames of silence they wrote. */
void output_hold(struct output *);
int output_idle_click(struct output *, int kind);
int output_idle_fill(struct output *);
void output_idle_end(struct output *);
/* the headphone DAC (output way 0; on the others only dac_song's mode) */
void dac_song(int outputway, int dsd);     /* at each song's start: DoP or PCM mode */
void dac_mute(int outputway);
void dac_unmute(int outputway, int wait);  /* wait: hold sound back until the mute line has let go */
int dac_stamp(int pressed);                /* a fresh press (1) or natural-start mark (0) from demo, each once */

/* dsp.c: one song's chain (ReplayGain, the EQs, Mono Audio, Swap L/R, crossfade, the taps). untouched: DSD, which
 * passes as it is. */
struct dsp;
struct dsp *dsp_open(const struct pcm_format *, int untouched, float replaygain, int length);
/* frames of data through it; returns the frames to write, at *out (fewer while crossfade holds a tail back). pts:
 * the song's seconds handed out up to the end of this block */
int dsp_run(struct dsp *, const uint8_t *data, int frames, double pts, const uint8_t **out);
void dsp_eq_changed(struct dsp *);
void dsp_close(struct dsp *, int ended); /* ended: the song played to its end, nobody cut it */
int dsp_tail_held(void); /* crossfade holds a song's tail for the next one to mix in */
void dsp_path(struct dsp *, const char *url); /* the song's file, for Smart Crossfade's analysis and plan */
void dsp_meter(const struct pcm_format *, int untouched, const uint8_t *data, int frames, int uv[2]);

/* smart.c: Smart Crossfade's analysis (docs/internals.md#smart-crossfade), on a thread of its own. smart_begin starts
 * it for the song at url (rg its ReplayGain scale) and the next song XFADE_NEXT names; smart_poll answers SMART_RUN
 * until it is done, then SMART_PLAN with the plan or SMART_PLAIN (the plain fade, the reason logged); smart_cancel
 * forgets it, with why logged unless NULL. */
enum { SMART_NONE, SMART_RUN, SMART_PLAN, SMART_PLAIN };
struct xf_plan {
    int64_t overlap; /* the song's frames mixed under the next, out_point to tail_end */
    int64_t trail;   /* its frames after tail_end, dropped */
    int64_t skip;    /* the next song's frames dropped at its start, at next_rate */
    int64_t fade;    /* its fade-in's frames, 0 for none */
    int next_rate;
    char next[URL_MAX]; /* the song the skip and the fade-in are for */
};
void smart_begin(const char *url, float rg);
int smart_poll(struct xf_plan *plan);
void smart_cancel(const char *why);

/* dsd.c: DSD packets made into DoP or Native frames */
enum { DSD_CHANNELS = 8 };
struct dsd_pack {
    int mode, channels;
    int left;                       /* bytes of each channel waiting for the next packet, below a frame's */
    uint8_t wait[DSD_CHANNELS][4];
    int64_t frame;                  /* frames made: DoP's marker goes by it */
    uint8_t *buf;
    size_t cap;
};
int dsd_unit(int mode); /* bytes of each channel a frame */
void dsd_reset(struct dsd_pack *);
/* bytes of packet data (planar: a block a channel; lsb_first: DSF's bit order), from skip bytes into each channel on:
 * the frames made, at *out; below 0 with no memory */
int dsd_pack(struct dsd_pack *, const uint8_t *data, int bytes, int planar, int lsb_first, int skip, const uint8_t **out);

#define SCAN_FILE PEQ_ROOT "/tmp/q2scan" /* there while the song playing can be scanned at speed (mcl-scan, patch/seek.c) */
/* stretch.c: scan's time-stretch, the song at 2 to 8 times its speed at its own pitch (docs/player.md#scan) */
struct stretch;
double stretch_top(int rate);                         /* the highest speed at a rate */
struct stretch *stretch_open(const struct pcm_format *); /* NULL for a format it does not take: DSD, or over 192 kHz */
void stretch_close(struct stretch *);
void stretch_reset(struct stretch *); /* a seek: forget what is held */
void stretch_speed(struct stretch *, double speed); /* 0: off, playing on from where the stretch had got to */
double stretch_rate(const struct stretch *);        /* the speed in force, 0 for none */
int64_t stretch_behind(const struct stretch *);     /* frames given that the output has not yet reached */
/* frames of data in (the song's format): the frames to write, at *out; data itself while passing through */
int stretch_run(struct stretch *, const uint8_t *data, int frames, const uint8_t **out);

/* cartrace.c: way 3's line a second in the unified log and its tap of the car's PCM (docs/iphone-mode.md#car-trace).
 * The playback thread's alone; output.c counts, engine.c times its reads and asks for the line. */
int64_t car_trace_now(void);                          /* CLOCK_MONOTONIC in microseconds */
void car_trace_open(int rate, const char *car_file);  /* a way-3 output opened at the device's rate */
void car_trace_write(int frames, int64_t us);         /* a write to the device and how long it took */
void car_trace_dry(int64_t frames);                   /* the PCM ran dry: the silence it put in */
void car_trace_back(int64_t frames);                  /* written frames taken back unheard */
void car_trace_underrun(void);                         /* dry while a song flowed: not a pause's, a seek's or the open's */
void car_trace_read(int64_t us);                      /* a source read and how long it took */
void car_trace_position(int position, int64_t played); /* the position told demo, the frames heard since its origin */
void car_tap(const uint8_t *data, int64_t bytes);     /* what is written to the device, for the tap */
void car_trace_tick(struct output *, int closing);    /* the line, once its second is up or as the output closes */

/* engine.c */
void *engine_thread(void *);
#endif
