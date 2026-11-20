/* Radio's receiver (q2video -t, video.c tuner): when a stream's sound is the programme and when its
 * carrier is dropped, when a stream that ended is started again (docs/internals.md#radio-static),
 * and the dial's click. The decisions alone, with no clock, files or threads, so
 * test/tuner.py runs them on this machine beside fmrx.c. */
#ifndef TUNER_CORE_H
#define TUNER_CORE_H

#define TUNER_PRIME                                                                                \
    12000 /* frames (250 ms at 48 kHz) in before the carrier comes up, and again after a stall */
#define TUNER_FRAMES 512 /* frames a write */

/* primed: the stream's sound is the programme; stalled: it was, and its ring ran dry; tries: the
 * stream's ends since it last sounded (or since demo's nudge). */
typedef struct {
    int primed, stalled, tries;
} tuner_feed;

/* have frames in the ring before a write: 1 when the write plays TUNER_FRAMES of them, 0 when it
 * plays none and the carrier is dropped (a stream named but not sounding). */
int tuner_take(tuner_feed *f, unsigned have);
/* The stream's ffmpeg ended (the network gone, the server closed it). */
void tuner_ended(tuner_feed *f);
/* demo's word that the network is back (the Wi-Fi link up again): the next restart without the
 * wait. */
void tuner_nudge(tuner_feed *f);
/* The wait after an end before the stream is started again: 1 s, 2, 4, 8, then 15 s for as long as
 * it fails. */
unsigned tuner_wait_ms(const tuner_feed *f);
/* since_ms after that end: 1 when the stream should be started again now. */
int tuner_retry_due(const tuner_feed *f, unsigned since_ms);

/* The dial's click (the approved prototype's click()): a 4 ms noise burst decaying over a fifth of
 * it, through a highpass at 2500 Hz, or 900 Hz for a louder one (over 10 tenths: landing on a
 * station), so a detent sounds duller; its peak gain 0.18 a unit of loudness. */
typedef struct {
    int left, n;
    unsigned rng;
    float gain, b0, b1, b2, a1, a2, x1, x2, y1, y2;
} tuner_click;

/* A click at loudness tenths/10 (demo's k datagram: 6 a held detent, 10 a step, 14 a station) at
 * sample rate fs, starting over any still sounding. */
void tuner_click_start(tuner_click *c, int tenths, float fs);
/* The click's next frames added into interleaved stereo out, both channels alike; nothing once it
 * has ended. */
void tuner_click_mix(tuner_click *c, float *out, int frames);

#endif
