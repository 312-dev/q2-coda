/* test/tuner.py's runs of patch/tuner_core.c with patch/fmrx.c, as q2video -t's writer runs them (video.c
 * tuner_writer): "name value" lines, which tuner.py checks. A stream plays a 1 kHz tone into the ring at the
 * real rate, stops for a dropout, and comes back; the writer takes TUNER_FRAMES a write as the ring allows,
 * the carrier dropped while it can't (FMRX_NO_CARRIER); the output's level is measured in 50 ms windows. Then a
 * stream that keeps ending: when each restart is due, and what a sounding stream does to the count. */
#include <math.h>
#include <stdio.h>
#include <string.h>

#include "../patch/fmrx.h"
#include "../patch/tuner_core.h"

#define FS 48000
#define SECS 10
static fmrx rx;
static float out[2 * FS * SECS];

static double db(double x) { return 20 * log10(x > 1e-12 ? x : 1e-12); }
static double rms(int from, int n) {
    double s = 0;
    for (int i = from; i < from + n; ++i) s += (double)out[2 * i] * out[2 * i];
    return sqrt(s / n);
}

int main(void) {
    /* the stream sends sound from 0 to 3 s, nothing from 3 to 6 s (the dropout), sound again from 6 s */
    const int drop_at = 3 * FS, back_at = 6 * FS;
    tuner_feed f = { 0, 0, 0 };
    fmrx_init(&rx, FS, 3);
    fmrx_params p = { 70, 0, 0.03f, 1, 1 }; /* a strong station, the needle on it */
    unsigned sent = 0, taken = 0;           /* frames the stream has sent, and the writer has played */
    static float in[2 * TUNER_FRAMES];
    int dry_at = -1, sounded_at = -1;
    for (int at = 0; at + TUNER_FRAMES <= FS * SECS; at += TUNER_FRAMES) {
        int t = at + TUNER_FRAMES; /* the stream is real time: what it has sent by the end of this write */
        sent = t < drop_at ? (unsigned)t : t < back_at ? (unsigned)drop_at : (unsigned)(drop_at + t - back_at);
        int prog = tuner_take(&f, sent - taken);
        if (!prog && f.stalled && dry_at < 0) dry_at = at;
        if (prog && dry_at >= 0 && at > back_at && sounded_at < 0) sounded_at = at;
        for (int i = 0; prog && i < TUNER_FRAMES; ++i) {
            float v = 0.25f * (float)sin(2 * M_PI * 1000.0 * (taken + i) / FS); /* -15 dBFS RMS */
            in[2 * i] = in[2 * i + 1] = v;
        }
        if (prog) taken += TUNER_FRAMES;
        fmrx_params q = p;
        if (!prog) q.level = FMRX_NO_CARRIER; /* the stream named, not sounding */
        fmrx_run(&rx, &q, prog ? in : 0, out + 2 * at, TUNER_FRAMES);
    }
    double lo = 0;
    for (int w = dry_at + FS / 10; w + FS / 20 <= back_at; w += FS / 20) { /* the dropout's 50 ms windows */
        double v = db(rms(w, FS / 20));
        if (lo == 0 || v < lo) lo = v;
    }
    printf("before_dbfs %.2f\n", db(rms(2 * FS, FS / 2)));
    printf("dry_ms %d\n", dry_at * 1000 / FS);
    printf("dropout_dbfs %.2f\n", db(rms(dry_at + FS / 10, back_at - dry_at - FS / 10)));
    printf("dropout_quietest_window_dbfs %.2f\n", lo);
    printf("sounds_again_ms %d\n", sounded_at * 1000 / FS);
    printf("after_dbfs %.2f\n", db(rms(sounded_at + FS / 2, FS / 2)));

    /* a stream that keeps ending: the wait before each restart, up to 12 of them (0: never) */
    tuner_feed d = { 0, 0, 0 };
    printf("waits_ms");
    for (int k = 0; k < 12; ++k) {
        tuner_ended(&d);
        unsigned wait = 0;
        for (unsigned ms = 0; ms <= 120000; ms += 10)
            if (tuner_retry_due(&d, ms)) {
                wait = ms;
                break;
            }
        printf(" %u", wait);
    }
    printf("\n");
    /* three ends, then the stream sounds: the count starts over */
    tuner_feed s = { 0, 0, 0 };
    tuner_ended(&s), tuner_ended(&s), tuner_ended(&s);
    tuner_take(&s, TUNER_PRIME);
    printf("tries_after_sound %d\n", s.tries);
    /* seven ends, then demo's nudge (the Wi-Fi link back): the next restart without the wait */
    tuner_feed n = { 0, 0, 0 };
    for (int k = 0; k < 7; ++k) tuner_ended(&n);
    tuner_nudge(&n);
    printf("wait_after_nudge_ms %u\n", tuner_wait_ms(&n));
    return 0;
}
