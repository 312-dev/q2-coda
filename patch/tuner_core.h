/* Radio's receiver (q2video -t, video.c tuner): when a stream's sound is the programme and when its
 * carrier is dropped, and when a stream that ended is started again
 * (docs/internals.md#radio-static). The decisions alone, with no clock, files or threads, so
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

#endif
