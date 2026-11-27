/* test/dacloop.py's simulation of USB DAC mode's fill level (dacloop/pitch.h, docs/dacloop.md#holding-the-fill-level):
 * a computer whose clock is off by DRIFT ppm sends at the rate the Q2's feedback asks for, scaled by the pitch control;
 * the DAC takes exactly RATE frames a second; once a second the fill is measured with up to half a read (CHUNK/2
 * frames) of noise either way, and pitch_step sets the control. Prints the fill's error in milliseconds: its least and
 * most over the run, and its mean and most over the last third; and the control's mean over the last third.
 *
 *     dacloop_pitch RATE DRIFT_PPM SECONDS [LO HI [START_MS]]
 *
 * LO, HI: the control's range, else 999000 to 1001000; START_MS: the fill's error at the start, else 0. */
#include <stdio.h>
#include <stdlib.h>

#include "pitch.h"

#define CHUNK 1024 /* q2dacloop's read */

int main(int argc, char **argv) {
    if (argc < 4 || argc == 5 || argc > 7) return fprintf(stderr, "usage: dacloop_pitch RATE DRIFT_PPM SECONDS [LO HI [START_MS]]\n"), 2;
    double rate = atof(argv[1]), drift = atof(argv[2]), lo_err = 1e9, hi_err = -1e9, tail_sum = 0, tail_max = 0, tail_pitch = 0;
    int seconds = atoi(argv[3]), tail = 0;
    long lo = argc >= 6 ? atol(argv[4]) : 999000, hi = argc >= 6 ? atol(argv[5]) : 1001000;
    struct pitch p = {0};
    int value = PITCH_NOMINAL;
    unsigned seed = 12345;
    double fill = argc == 7 ? atof(argv[6]) * rate / 1000 : 0; /* frames above the target */
    for (int t = 1; t <= seconds; ++t) {
        fill += rate * (1 + drift * 1e-6) * (value / 1e6) - rate;
        seed = seed * 1103515245u + 12345u;
        double noise = ((double)(seed >> 8 & 0xffff) / 65535.0 - 0.5) * CHUNK;
        value = pitch_step(&p, (fill + noise) / rate, 1.0, lo, hi);
        double ms = fill / rate * 1000;
        if (ms < lo_err) lo_err = ms;
        if (ms > hi_err) hi_err = ms;
        if (t > seconds * 2 / 3) tail_sum += ms, tail_pitch += value, ++tail, tail_max = ms > tail_max ? ms : -ms > tail_max ? -ms : tail_max;
    }
    printf("min %.3f max %.3f tail_mean %.3f tail_max %.3f pitch %.1f\n", lo_err, hi_err, tail_sum / tail, tail_max, tail_pitch / tail);
    return 0;
}
