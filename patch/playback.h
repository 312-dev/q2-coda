#ifndef Q2_PLAYBACK_H
#define Q2_PLAYBACK_H
int playback_option(int kind); /* 0 shuffle, 1 repeat, 2 grouping */
int playback_active(void);     /* the extended play runs: playback_option holds */
unsigned rand_below(unsigned n);
int playback_set(int kind, int value);
int playback_groups(void *all, int folder);
int playback_unshuffle(void);   /* the stock mode Shuffle Songs replaces */
int playback_shuffle(int mode); /* the queue as Shuffle Songs, Repeat All; the next restores mode */
int playback_listable(void); /* View Queue: a shuffle's order is up (stock Shuffle taken first) */
int playback_group_skip(int forward);
int playback_back(void); /* the track stock Shuffle's Previous left, once; else -1 */
int playback_peek(unsigned at); /* stock Shuffle's next drawn ahead from at, once; else -1 */
int shuffle_forced(void);       /* navigation.c: a Play next stock Shuffle goes to next, or -1 */
unsigned playback_upcoming(unsigned *out, unsigned max); /* Up Next: queue indices, in play order */
unsigned playback_played(unsigned *out, unsigned max); /* a shuffle's played songs, oldest first */
/* Play Next (next 1), Play Last (2), an app's Add (0): the tracks land at pos+1, at the end of
 * Playing Next or at the end; returns that index */
unsigned playback_queue_insert(void *queue, void *add, int next);
unsigned playback_next_rows(unsigned *out, unsigned max); /* Up Next's Playing Next, in order */
int playback_next_move(unsigned from, unsigned to); /* Playing Next's entry from goes to place to */
int playback_next_clear(void); /* Up Next's Clear: Playing Next's tracks leave the queue */
/* A radio of the song playing: the queue becomes that song then add's, with no load (1) */
int playback_queue_rest(void *add);
/* song_rows.c: r is the song playing or paused, by its path and CUE track (a server row by its
 * fileurl); nothing is while stopped (docs/oled-design.md#song-rows) */
int song_playing(void *r);
/* song_radio.c: a playlist session's first entry is the song playing, its app's radio of it (1) */
int song_radio_keeps(const char *first);
void playback_insert(unsigned at, unsigned n, int next); /* the advanced part alone */
int playback_move(unsigned from); /* Up Next's hold: from plays next; its new index, else -1 */
void playback_poll(void);
void playback_speed_start(void);
void playback_track_speed(void); /* a track's start: the folder's speed, to the decoder */
int playback_start_fits(void);   /* 0: the track at MCL_POS is too long to open */
int playback_start_refused(void); /* 1: player_start's replay after a load of nothing */
int playback_video(void *record); /* a video file, kept out of audio queues */
void xfade_poll(int next, int shuffled); /* crossfade.c */
void mono_sync(void);      /* crossfade.c */
int playback_resumed(void *r);
void playback_save(void);
void playback_clear(void);
/* mclLoadPlayList's loads so far: a queue loaded later is another one (addon.c stream_queue) */
extern unsigned playback_loads;
/* The user's moves to another song in the queue playing so far (Next, Previous, a group skip, an Up
 * Next or car remote pick; playback_pick): one since a card app's .play means the song it waits for
 * is no longer wanted (addon.c stream_start), and a Next waiting for a song is dropped
 * (addon_next_waits) */
extern unsigned playback_picks;
void playback_pick(void);
/* addon.c: a user's Next on the last song queued of a card app's playlist whose next song is still
 * downloading moves Now Playing to that song at once (1), and it plays once it can */
int addon_next_waits(void);
#define WHEEL_PCT_MIN 10 /* the slowest: 140 units a tick, 0.7 of a turn */
int wheel_pct(void); /* Wheel sensitivity, WHEEL_PCT_MIN-200%, 50 unless set */
void wheel_set_pct(int n);
/* iPod: while wheel_raw is set the wheel posts no ticks and adds its raw travel to wheel_travel,
 * 200 units a turn, rising anticlockwise (games.c's Vortex, Now Playing's lyrics, 2: Cover Flow's
 * thumb, whose travel still counts towards Return's stop and which reads wheel_mute); wheel_held is
 * set while a finger is on the wheel */
extern volatile int wheel_raw, wheel_travel, wheel_held, wheel_mute;
enum { WHEEL_TICKS, WHEEL_RAW, WHEEL_THUMB }; /* wheel_raw's values: off, 1, 2 */
extern volatile unsigned wheel_frame_at; /* the wheel's last frame with pressure */
extern volatile int wheel_speed; /* degrees a second as the last tick was posted (wheel_sample) */
enum { THUMB_MOVED = 1, THUMB_TURNED = 2, THUMB_CLICK = 4 }; /* coverflow_wheel_poll's answer */
int wheel_part(void); /* the way to the next tick, 256ths, + toward Next (tilt.c) */
void wheel_tick_taken(void); /* ringnav: a posted tick reached the UI (wheel_part) */
void wheel_gate_note(void); /* --dev: tick_gate dropped a tick for want of a frame */
int playback_keeps(void *q, int at), restart_armed(int key);
void playback_np(void), playback_np_open(void);
#endif
