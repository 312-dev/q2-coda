/* The EQ for test/player/run.py, from patch/peq.c as the player builds it:
 *   peq_ref save STATE GAIN [PREAMP]    an eq_state playing a PEQ of one 1 kHz band of GAIN dB, preamp PREAMP
 *                                        (a set one, so it plays as saved) or Auto
 *   peq_ref filter STATE RATE IN OUT [FRAME STATE2]
 *                                        IN (raw S16 stereo) through STATE's EQ as player/dsp.c runs it, float from
 *                                        x / 32768 and back by lrintf of x * 32768, saturated; from FRAME on through
 *                                        STATE2's, by the engine's update ramp, as an mcl-seteq does. */
#include <assert.h>
#include "peq.h"

static void effective(const char *path, peq_preset *p) {
    eq_state s;
    assert(eq_load_from(path, &s));
    eq_effective(&s, p);
}

int main(int argc, char **argv) {
    if (argc >= 4 && !strcmp(argv[1], "save")) {
        eq_state s;
        eq_load_from("/nonexistent", &s); /* the defaults */
        s.mode = EQ_PEQ;
        peq_default(&s.peq);
        s.peq.bypass = 0, s.peq.count = 1;
        s.peq.bands[0] = (peq_band){1, 0, 1000, atof(argv[3]), 1};
        s.peq.automatic = argc > 4 ? 2 : 1;
        s.peq.preamp = argc > 4 ? atof(argv[4]) : 0;
        return !eq_save_to(argv[2], &s);
    }
    if (argc >= 6 && !strcmp(argv[1], "filter")) {
        peq_preset p, q;
        peq_dsp d;
        effective(argv[2], &p);
        int rate = atoi(argv[3]);
        long at = argc > 7 ? atol(argv[6]) : -1;
        if (at >= 0) effective(argv[7], &q);
        peq_reset(&d, rate, 2, &p);
        FILE *in = fopen(argv[4], "rb"), *out = fopen(argv[5], "wb");
        assert(in && out);
        int16_t s[2];
        float x[2];
        for (long n = 0; fread(s, sizeof s, 1, in) == 1; ++n) {
            if (n == at) assert(peq_update(&d, &q));
            for (int c = 0; c < 2; ++c) x[c] = s[c] * (1.0f / 32768);
            peq_process(&d, x, 1);
            for (int c = 0; c < 2; ++c) {
                float v = x[c] * 32768.0f;
                s[c] = v >= 32767.0f ? 32767 : v <= -32768.0f ? -32768 : (int16_t)lrintf(v);
            }
            fwrite(s, sizeof s, 1, out);
        }
        return fclose(out) || fclose(in);
    }
    fputs("usage: peq_ref save STATE GAIN [PREAMP] | filter STATE RATE IN OUT [FRAME STATE2]\n", stderr);
    return 2;
}
