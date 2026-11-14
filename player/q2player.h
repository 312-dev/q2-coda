/* q2player: the Q2's audio player, a replacement for stock's hciplayer that speaks its socket protocol
 * (docs/player.md) and decodes with FFmpeg's libraries. Architecture: docs/player-plan.md#architecture. */
#ifndef Q2PLAYER_H
#define Q2PLAYER_H
#include <pthread.h>
#include <stdint.h>

#define URL_MAX 1564 /* stock's command buffer, 0x61c */

/* set-songurl, set-nexturl and set-gapless, as demo sends them. */
struct song {
    char url[URL_MAX];
    int start;     /* ss=N: where to begin, in seconds (a CUE track, a resumed song) */
    int cache_kb;  /* stream cache for a URL; -1 on a preload */
    int cache_pct; /* how full the cache is before playing starts */
    int gapless;   /* demo's Gapless setting */
    int hw;        /* demo's hardware type, 17 on a Q2; absent from set-gapless */
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
    /* settings demo pushes; most only take effect in later phases */
    int outputway, volume, softvol, dsdmode, replaygain, speed, explay, mqa, eq_on;
    float eq[10];
    /* status */
    int state, has_info, position;
    struct info info;
    int uv[2], spectrum[12];
};
extern struct ctl ctl;

/* ipc.c */
int ipc_connect(const char *path);
void ipc_send(const char *text);
void *ipc_thread(void *);

/* source.c: one open file or stream, decoded to interleaved PCM in the format the output is opened with. */
struct pcm_format { int rate, channels, bytes; /* per sample: 2 (S16_LE) or 4 (S32_LE) */ };
struct source;
struct source *source_open(const struct song *song, int *error);
void source_close(struct source *);
const struct pcm_format *source_format(const struct source *);
void source_info(const struct source *, struct info *);
/* The next block: frames and where they are, 0 at the end, below 0 on a decode error that ends the file. */
int source_read(struct source *, const uint8_t **data);
int source_seek(struct source *, int seconds);
int64_t source_frames(const struct source *); /* frames handed out since the start or the last seek, plus its target */

/* output.c */
struct output;
struct output *output_open(const struct pcm_format *, int outputway, int hw);
int output_write(struct output *, const uint8_t *data, int frames);
int64_t output_delay(struct output *); /* frames written and not yet played */
void output_pause(struct output *, int on);
void output_drop(struct output *);     /* forget what is buffered: a seek, a skip, a stop */
void output_drain(struct output *);    /* play what is buffered to its end */
void output_close(struct output *);

/* engine.c */
void *engine_thread(void *);
#endif
