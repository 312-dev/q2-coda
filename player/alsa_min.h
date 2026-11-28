/* The part of alsa-lib's public API output.c and uac.c call, declared here because the Q2's root filesystem has
 * libasound.so.2 (1.1.6) and no headers. Values are alsa-lib's <alsa/pcm.h>. */
#ifndef ALSA_MIN_H
#define ALSA_MIN_H
typedef struct _snd_pcm snd_pcm_t;
typedef struct _snd_pcm_hw_params snd_pcm_hw_params_t;
typedef struct _snd_pcm_sw_params snd_pcm_sw_params_t;
typedef unsigned long snd_pcm_uframes_t;
typedef long snd_pcm_sframes_t;
enum { SND_PCM_STREAM_PLAYBACK = 0, SND_PCM_ACCESS_RW_INTERLEAVED = 3, SND_PCM_FORMAT_S16_LE = 2, SND_PCM_FORMAT_S32_LE = 10,
       SND_PCM_FORMAT_FLOAT_LE = 14, SND_PCM_FORMAT_S24_3LE = 32, SND_PCM_FORMAT_DSD_U32_BE = 52 };
int snd_config_update_free_global(void);
int snd_pcm_open(snd_pcm_t **, const char *, int stream, int mode);
int snd_pcm_close(snd_pcm_t *);
int snd_pcm_hw_params_malloc(snd_pcm_hw_params_t **);
void snd_pcm_hw_params_free(snd_pcm_hw_params_t *);
int snd_pcm_hw_params_any(snd_pcm_t *, snd_pcm_hw_params_t *);
int snd_pcm_hw_params_set_access(snd_pcm_t *, snd_pcm_hw_params_t *, int);
int snd_pcm_hw_params_test_format(snd_pcm_t *, snd_pcm_hw_params_t *, int);
int snd_pcm_hw_params_set_format(snd_pcm_t *, snd_pcm_hw_params_t *, int);
int snd_pcm_hw_params_set_channels(snd_pcm_t *, snd_pcm_hw_params_t *, unsigned);
int snd_pcm_hw_params_test_channels(snd_pcm_t *, snd_pcm_hw_params_t *, unsigned);
int snd_pcm_hw_params_test_rate(snd_pcm_t *, snd_pcm_hw_params_t *, unsigned, int);
int snd_pcm_hw_params_set_rate_resample(snd_pcm_t *, snd_pcm_hw_params_t *, unsigned);
int snd_pcm_hw_params_set_rate_near(snd_pcm_t *, snd_pcm_hw_params_t *, unsigned *, int *);
int snd_pcm_hw_params_set_buffer_time_near(snd_pcm_t *, snd_pcm_hw_params_t *, unsigned *, int *);
int snd_pcm_hw_params_set_periods_near(snd_pcm_t *, snd_pcm_hw_params_t *, unsigned *, int *);
int snd_pcm_hw_params(snd_pcm_t *, snd_pcm_hw_params_t *);
int snd_pcm_get_params(snd_pcm_t *, snd_pcm_uframes_t *buffer_size, snd_pcm_uframes_t *period_size);
int snd_pcm_sw_params_malloc(snd_pcm_sw_params_t **);
void snd_pcm_sw_params_free(snd_pcm_sw_params_t *);
int snd_pcm_sw_params_current(snd_pcm_t *, snd_pcm_sw_params_t *);
int snd_pcm_sw_params_get_boundary(const snd_pcm_sw_params_t *, snd_pcm_uframes_t *);
int snd_pcm_sw_params_set_start_threshold(snd_pcm_t *, snd_pcm_sw_params_t *, snd_pcm_uframes_t);
int snd_pcm_sw_params_set_stop_threshold(snd_pcm_t *, snd_pcm_sw_params_t *, snd_pcm_uframes_t);
int snd_pcm_sw_params_set_silence_size(snd_pcm_t *, snd_pcm_sw_params_t *, snd_pcm_uframes_t);
int snd_pcm_sw_params(snd_pcm_t *, snd_pcm_sw_params_t *);
int snd_pcm_prepare(snd_pcm_t *);
snd_pcm_sframes_t snd_pcm_writei(snd_pcm_t *, const void *, snd_pcm_uframes_t);
int snd_pcm_delay(snd_pcm_t *, snd_pcm_sframes_t *);
snd_pcm_sframes_t snd_pcm_avail(snd_pcm_t *);
snd_pcm_sframes_t snd_pcm_forward(snd_pcm_t *, snd_pcm_uframes_t);
snd_pcm_sframes_t snd_pcm_rewindable(snd_pcm_t *);
int snd_pcm_wait(snd_pcm_t *, int);
int snd_pcm_state(snd_pcm_t *); /* snd_pcm_state_t: 0 OPEN, 1 SETUP, 2 PREPARED, 3 RUNNING, 4 XRUN, ... 8 DISCONNECTED */
snd_pcm_sframes_t snd_pcm_rewind(snd_pcm_t *, snd_pcm_uframes_t);
int snd_pcm_pause(snd_pcm_t *, int);
int snd_pcm_drop(snd_pcm_t *);
int snd_pcm_drain(snd_pcm_t *);
const char *snd_strerror(int);
/* controls (uac.c reads the gadget card's Playback Rate); SND_CTL_ELEM_IFACE_PCM is alsa-lib's <alsa/control.h> */
typedef struct _snd_ctl snd_ctl_t;
typedef struct _snd_ctl_elem_value snd_ctl_elem_value_t;
enum { SND_CTL_ELEM_IFACE_PCM = 3 };
int snd_ctl_open(snd_ctl_t **, const char *, int mode);
int snd_ctl_close(snd_ctl_t *);
int snd_ctl_elem_value_malloc(snd_ctl_elem_value_t **);
void snd_ctl_elem_value_free(snd_ctl_elem_value_t *);
void snd_ctl_elem_value_set_interface(snd_ctl_elem_value_t *, int);
void snd_ctl_elem_value_set_name(snd_ctl_elem_value_t *, const char *);
int snd_ctl_elem_read(snd_ctl_t *, snd_ctl_elem_value_t *);
long snd_ctl_elem_value_get_integer(const snd_ctl_elem_value_t *, unsigned int);
#endif
