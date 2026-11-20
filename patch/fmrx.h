/* The Radio Tuner's FM receiver (docs/internals.md#radio-tuner-static): a station's programme as an
 * FM receiver would hear it at a given carrier-to-noise ratio and tuning offset, with the hiss, the
 * soft mute, the stereo blend, multipath flutter and the next channel's splatter of a real one. The
 * reference is the approved prototype's fmRxWorklet (its spec: static-model.md, 2026-11-20); this
 * is the same model with cheaper parts for the Q2's MIPS core: table sines, a table Gaussian, a
 * polynomial arctangent. q2video's tuner (-t, video.c) runs it between ffmpeg's sound and ALSA;
 * test/fmrx.py holds it to the prototype's measured levels and spectra. No libc beyond what both
 * builds pass in: fmrx_init takes the few transcendental results it needs as tables it fills
 * itself. */
#ifndef FMRX_H
#define FMRX_H

#define FMRX_BLOCK 128           /* frames a control step covers, the worklet's render quantum */
#define FMRX_NO_CARRIER (-40.0f) /* level: nothing on this channel, dB */
#define FMRX_TABLE 4096          /* sine and Gaussian table entries */

/* What demo sets, as the worklet's parameters: level, the station's carrier to noise in dB
 * (FMRX_NO_CARRIER for none); offset, the dial less the station, kHz; fade, the multipath depth
 * rho; gain, the output (0 paused, which stops the computation once it has faded); station, which
 * carrier level and offset describe (a change fades the old one out before the new is taken up). */
typedef struct {
    float level, offset, fade, gain;
    int station;
} fmrx_params;

typedef struct {
    float re, im, a[2], b[2], hz;
} fmrx_wander;

typedef struct {
    float b0, b1, b2, a1, a2, z[4];
} fmrx_biquad;

typedef struct {
    float fs, a_de, hp_r, dpsi;
    float lv, off, g, amp, mute, blend, dc, sn, gh;
    float phi, psi_re, psi_im, pre, pim, mz, de, hx, hy, sde;
    int id, swap, idle;
    unsigned rng;
    fmrx_wander flutter, drift;
    fmrx_biquad m[2], s[2];
    float sine[FMRX_TABLE + 1], gauss[FMRX_TABLE], ifamp[1026];
} fmrx;

/* fs: the sample rate (48000); seed: the noise's (any but 0). */
void fmrx_init(fmrx *r, float fs, unsigned seed);
/* n frames (any count; the control steps every FMRX_BLOCK of them) of interleaved stereo programme
 * in, -1..1 (NULL: silence, a carrier with no programme), to interleaved stereo out. Returns 0 when
 * paused and faded out: out is silence and no work was done. */
int fmrx_run(fmrx *r, const fmrx_params *p, const float *in, float *out, int n);

#endif
