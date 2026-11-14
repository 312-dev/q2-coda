/* The part of alsa-lib's public API output.c calls, declared here because the Q2's root filesystem has
 * libasound.so.2 (1.1.6) and no headers. Values are alsa-lib's <alsa/pcm.h>. */
#ifndef ALSA_MIN_H
#define ALSA_MIN_H
typedef struct _snd_pcm snd_pcm_t;
typedef struct _snd_pcm_hw_params snd_pcm_hw_params_t;
typedef struct _snd_pcm_sw_params snd_pcm_sw_params_t;
typedef unsigned long snd_pcm_uframes_t;
typedef long snd_pcm_sframes_t;
enum { SND_PCM_STREAM_PLAYBACK = 0, SND_PCM_ACCESS_RW_INTERLEAVED = 3, SND_PCM_FORMAT_S16_LE = 2, SND_PCM_FORMAT_S32_LE = 10,
       SND_PCM_FORMAT_FLOAT_LE = 14 };
int snd_pcm_open(snd_pcm_t **, const char *, int stream, int mode);
int snd_pcm_close(snd_pcm_t *);
int snd_pcm_hw_params_malloc(snd_pcm_hw_params_t **);
void snd_pcm_hw_params_free(snd_pcm_hw_params_t *);
int snd_pcm_hw_params_any(snd_pcm_t *, snd_pcm_hw_params_t *);
int snd_pcm_hw_params_set_access(snd_pcm_t *, snd_pcm_hw_params_t *, int);
int snd_pcm_hw_params_set_format(snd_pcm_t *, snd_pcm_hw_params_t *, int);
int snd_pcm_hw_params_set_channels(snd_pcm_t *, snd_pcm_hw_params_t *, unsigned);
int snd_pcm_hw_params_set_rate_resample(snd_pcm_t *, snd_pcm_hw_params_t *, unsigned);
int snd_pcm_hw_params_set_rate_near(snd_pcm_t *, snd_pcm_hw_params_t *, unsigned *, int *);
int snd_pcm_hw_params_set_buffer_time_near(snd_pcm_t *, snd_pcm_hw_params_t *, unsigned *, int *);
int snd_pcm_hw_params_set_periods_near(snd_pcm_t *, snd_pcm_hw_params_t *, unsigned *, int *);
int snd_pcm_hw_params(snd_pcm_t *, snd_pcm_hw_params_t *);
int snd_pcm_prepare(snd_pcm_t *);
snd_pcm_sframes_t snd_pcm_writei(snd_pcm_t *, const void *, snd_pcm_uframes_t);
int snd_pcm_delay(snd_pcm_t *, snd_pcm_sframes_t *);
snd_pcm_sframes_t snd_pcm_rewindable(snd_pcm_t *);
snd_pcm_sframes_t snd_pcm_rewind(snd_pcm_t *, snd_pcm_uframes_t);
int snd_pcm_pause(snd_pcm_t *, int);
int snd_pcm_drop(snd_pcm_t *);
int snd_pcm_drain(snd_pcm_t *);
const char *snd_strerror(int);
#endif
