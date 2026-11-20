/* The Radio Tuner's FM receiver model (fmrx.h, docs/internals.md#radio-tuner-static), per sample:
 * the transmitter's pre-emphasis and limiter, the IF filter's amplitude at the instantaneous
 * frequency, the carrier's phasor plus complex noise plus a wandering co-channel ghost at 19 kHz,
 * the discriminator and its S-curve, de-emphasis, a 30 Hz DC block, the 15 kHz lowpass, the stereo
 * difference with its own hiss and blend, the soft mute and the output gain. Block rate: the
 * smoothed level, offset, gain, mute and blend, multipath flutter, the standing offset. The
 * prototype's fmRxWorklet is the reference, line for line; what differs is how the per-sample
 * transcendental functions are had: sines from a table with linear interpolation (error under
 * 3e-7), Gaussian noise from an inverse-CDF table, the arctangent from a polynomial (error under
 * 1e-5 rad), tanh from a rational function where the S-curve flattens. */
#include "fmrx.h"

double exp(double), pow(double, double), log(double), log10(double), sqrt(double), sin(double),
    cos(double);

#define PI 3.14159265358979f
#define DEV 75.0f              /* kHz of deviation for full-scale programme */
#define RPK (0.16f * PI / DEV) /* phase step a sample that stands for 1 kHz */
#define MUTE_DB 24.0f          /* soft mute: this much at MUTE_LO dB or below, none at MUTE_HI */
#define MUTE_LO 4.0f
#define MUTE_HI 26.0f
#define S_NOISE 10.8f    /* the difference channel's hiss, over the carrier's amplitude */
#define BLEND_FULL 66.0f /* dB at which the blend reaches full stereo */
#define GHOST 0.8f       /* the far co-channel carrier's mean amplitude, under the noise */
#define LP_HZ 15000.0f

static unsigned rnd(fmrx *r) {
    unsigned x = r->rng;
    x ^= x << 13, x ^= x >> 17, x ^= x << 5;
    return r->rng = x;
}
static float uniform(fmrx *r) { return (rnd(r) >> 8) * (1.0f / 16777216.0f); }
static float gauss(fmrx *r) { return r->gauss[rnd(r) >> 20]; } /* unit variance */

/* sin(x) for any x, from the table */
static float tsin(const fmrx *r, float x) {
    float t = x * (FMRX_TABLE / (2 * PI));
    int i = (int)t;
    if (t < 0) --i;
    float f = t - (float)i;
    i &= FMRX_TABLE - 1;
    return r->sine[i] + (r->sine[i + 1] - r->sine[i]) * f;
}
static float tcos(const fmrx *r, float x) { return tsin(r, x + PI / 2); }

/* atan2(y, x), within 1e-5 rad */
static float fatan2(float y, float x) {
    float ax = x < 0 ? -x : x, ay = y < 0 ? -y : y;
    if (ax == 0 && ay == 0) return 0;
    float a = ax > ay ? ay / ax : ax / ay, s = a * a;
    float t =
        a * (0.99997726f +
             s * (-0.33262347f +
                  s * (0.19354346f + s * (-0.11643287f + s * (0.05265332f + s * -0.01172120f)))));
    if (ay > ax) t = PI / 2 - t;
    if (x < 0) t = PI - t;
    return y < 0 ? -t : t;
}

/* tanh(x) for x >= 0, within 2.5e-3: where the S-curve flattens, no finer reading matters */
static float ftanh(float x) {
    if (x > 4.97f) return 1;
    float s = x * x;
    return x * (135135 + s * (17325 + s * (378 + s))) /
           (135135 + s * (62370 + s * (3150 + 28 * s)));
}

/* The discriminator's S-curve: linear to 150 kHz, flattening out at 200. */
static float s_curve(float f) {
    return f > 150    ? 150 + 50 * ftanh((f - 150) / 50)
           : f < -150 ? -150 - 50 * ftanh((-f - 150) / 50)
                      : f;
}

/* The IF filter's amplitude at f kHz from the tuned centre: flat across the channel, 3 dB down at
 * 97 kHz, 82 dB down on the next channel. */
static float if_amp(const fmrx *r, float f) {
    float x = (f < 0 ? -f : f) * 2;
    if (x > 1024) x = 1024;
    int i = (int)x;
    return r->ifamp[i] + (r->ifamp[i + 1] - r->ifamp[i]) * (x - (float)i);
}

/* 1 - e^(-n / (fs tau)): a one-pole's step over n samples */
static float k(const fmrx *r, int n, float tau) { return 1 - (float)exp(-n / (r->fs * tau)); }

/* A slowly wandering complex Gaussian of unit power: two one-pole lowpasses at hz, stepped a block.
 */
static void wander(fmrx *r, fmrx_wander *w, int n) {
    float al = 1 - (float)exp(-2 * PI * w->hz * n / r->fs), be = 1 - al;
    float norm = 1 / (float)sqrt(al * al * al * al * (1 + be * be) /
                                 ((1 - be * be) * (1 - be * be) * (1 - be * be)));
    for (int i = 0; i < 2; ++i) {
        float x = gauss(r) * 0.70710678f; /* variance 1/2 */
        w->a[i] += al * (x - w->a[i]);
        w->b[i] += al * (w->a[i] - w->b[i]);
    }
    w->re = w->b[0] * norm, w->im = w->b[1] * norm;
}

/* An RBJ lowpass biquad at fc, quality q. */
static void lowpass(fmrx *r, fmrx_biquad *b, float q) {
    float fc = LP_HZ < 0.45f * r->fs ? LP_HZ : 0.45f * r->fs;
    float w = 2 * PI * fc / r->fs, al = (float)sin(w) / (2 * q), c = (float)cos(w), a0 = 1 + al;
    b->b0 = (1 - c) / 2 / a0, b->b1 = (1 - c) / a0, b->b2 = (1 - c) / 2 / a0;
    b->a1 = -2 * c / a0, b->a2 = (1 - al) / a0;
    b->z[0] = b->z[1] = b->z[2] = b->z[3] = 0;
}

/* the 15 kHz lowpass: fourth-order Butterworth as two biquads */
static float lp4(fmrx_biquad *s, float x) {
    for (int j = 0; j < 2; ++j) {
        fmrx_biquad *c = &s[j];
        float y = c->b0 * x + c->b1 * c->z[0] + c->b2 * c->z[1] - c->a1 * c->z[2] - c->a2 * c->z[3];
        c->z[1] = c->z[0], c->z[0] = x, c->z[3] = c->z[2], c->z[2] = y, x = y;
    }
    return x;
}

/* The standard normal's quantile at p (Acklam's rational approximation, relative error 1.2e-9). */
static double quantile(double p) {
    static const double a[] = { -3.969683028665376e+01, 2.209460984245205e+02,
                                -2.759285104469687e+02, 1.383577518672690e+02,
                                -3.066479806614716e+01, 2.506628277459239e+00 };
    static const double b[] = { -5.447609879822406e+01, 1.615858368580409e+02,
                                -1.556989798598866e+02, 6.680131188771972e+01,
                                -1.328068155288572e+01 };
    static const double c[] = { -7.784894002430293e-03, -3.223964580411365e-01,
                                -2.400758277161838e+00, -2.549732539343734e+00,
                                4.374664141464968e+00,  2.938163982698783e+00 };
    static const double d[] = { 7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
                                3.754408661907416e+00 };
    if (p < 0.02425) {
        double q = sqrt(-2 * log(p));
        return (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) /
               ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1);
    }
    if (p > 1 - 0.02425) return -quantile(1 - p);
    double q = p - 0.5, s = q * q;
    return (((((a[0] * s + a[1]) * s + a[2]) * s + a[3]) * s + a[4]) * s + a[5]) * q /
           (((((b[0] * s + b[1]) * s + b[2]) * s + b[3]) * s + b[4]) * s + 1);
}

void fmrx_init(fmrx *r, float fs, unsigned seed) {
    char *p = (char *)r;
    for (unsigned i = 0; i < sizeof *r; ++i) p[i] = 0;
    r->fs = fs;
    r->rng = seed ? seed : 1;
    r->a_de = (float)exp(-1 / (fs * 75e-6));
    r->hp_r = 1 - 2 * PI * 30 / fs;
    r->dpsi = 2 * PI * 19000 / fs;
    r->lv = FMRX_NO_CARRIER;
    r->mute = (float)pow(10, -MUTE_DB / 20);
    r->pre = 1, r->psi_re = 1;
    r->flutter.hz = 3, r->drift.hz = 0.25f;
    for (int i = 0; i <= FMRX_TABLE; ++i) r->sine[i] = (float)sin(2 * PI * i / FMRX_TABLE);
    double sum = 0;
    for (int i = 0; i < FMRX_TABLE; ++i)
        r->gauss[i] = (float)quantile((i + 0.5) / FMRX_TABLE), sum += r->gauss[i] * r->gauss[i];
    float scale = (float)sqrt(FMRX_TABLE / sum); /* the table's own variance made exactly 1 */
    for (int i = 0; i < FMRX_TABLE; ++i) r->gauss[i] *= scale;
    for (int i = 0; i < 1026; ++i) r->ifamp[i] = (float)(1 / sqrt(1 + pow(i * 0.5 / 97, 26)));
    lowpass(r, &r->m[0], 0.5412f), lowpass(r, &r->m[1], 1.3066f);
    lowpass(r, &r->s[0], 0.5412f), lowpass(r, &r->s[1], 1.3066f);
}

/* One control step over n frames (n <= FMRX_BLOCK). */
static int block(fmrx *r, const fmrx_params *p, const float *in, float *out, int n) {
    float gT = p->gain, g0 = r->g;
    r->g += (gT - g0) * k(r, n, 0.015f);
    if (gT == 0 && r->g < 1e-4f) { /* paused: silence, and no work */
        r->g = 0;
        if (g0 == 0) {
            for (int i = 0; i < 2 * n; ++i) out[i] = 0;
            return 0;
        }
    }
    /* a change of station is two carriers: the old fades out where it was before the new one's
     * level and offset are taken up, so one station's sound never slides across the dial */
    if (p->station != r->id) r->id = p->station, r->swap = 1;
    float offT = p->offset > 250 ? 250 : p->offset < -250 ? -250 : p->offset;
    if (r->swap && r->lv < -10) r->swap = 0, r->off = offT;
    float lvT = r->swap ? FMRX_NO_CARRIER : p->level > FMRX_NO_CARRIER ? p->level : FMRX_NO_CARRIER;
    r->lv += (lvT - r->lv) * k(r, n, lvT > r->lv ? 0.07f : 0.03f);
    float off0 = r->off;
    if (!r->swap) r->off += (offT - off0) * k(r, n, 0.01f);
    wander(r, &r->flutter, n), wander(r, &r->drift, n);
    float rho = p->fade, fre = 1 + rho * r->flutter.re, fim = rho * r->flutter.im;
    float fl = (float)sqrt(fre * fre + fim * fim);
    float amp0 = r->amp, amp1 = r->lv <= FMRX_NO_CARRIER + 1 ? 0 : (float)pow(10, r->lv / 20) * fl;
    float gh0 = r->gh,
          gh1 = GHOST * (float)sqrt(r->drift.re * r->drift.re + r->drift.im * r->drift.im);
    float ac = amp1 * if_amp(r, r->off), cnr = 20 * (float)log10(ac > 1e-3f ? ac : 1e-3f);
    float captured = 1 - (float)exp(-ac * ac); /* FM's capture of a weaker signal */
    float x = (MUTE_HI - cnr) / (MUTE_HI - MUTE_LO);
    float muteT = (float)pow(10, -MUTE_DB / 20 * (x < 0 ? 0 : x > 1 ? 1 : x));
    float mute0 = r->mute;
    r->mute = muteT < mute0 ? muteT : mute0 + (muteT - mute0) * k(r, n, 0.04f);
    float blendT = (float)pow(10, (cnr - BLEND_FULL) / 20);
    if (blendT > 1) blendT = 1;
    float blend0 = r->blend;
    r->blend = blendT < blend0 ? blendT : blend0 + (blendT - blend0) * k(r, n, 0.1f);
    float dc0 = r->dc, dc1 = s_curve(r->off) / DEV * captured; /* the standing output off centre */
    float sn0 = r->sn, sn1 = blendT * S_NOISE / (ac > 1e-3f ? ac : 1e-3f);
    r->amp = amp1, r->gh = gh1, r->dc = dc1, r->sn = sn1;
    float phi = r->phi, pre = r->pre, pim = r->pim, mz = r->mz, de = r->de, hx = r->hx, hy = r->hy;
    float sde = r->sde, a_de = r->a_de, pr = r->psi_re, pi = r->psi_im;
    float cr = tcos(r, r->dpsi), ci = tsin(r, r->dpsi);
    for (int i = 0; i < n; ++i) {
        float t = (float)(i + 1) / (float)n, l = in ? in[2 * i] : 0, rr = in ? in[2 * i + 1] : 0;
        float m = (l + rr) * 0.5f;
        float pe =
            (m - a_de * mz) / (1 - a_de); /* pre-emphasis, limited to three times full deviation */
        mz = m;
        if (pe > 2)
            pe = 2 + ftanh(pe - 2);
        else if (pe < -2)
            pe = -2 - ftanh(-pe - 2);
        float f =
            off0 + (r->off - off0) * t + DEV * pe; /* instantaneous frequency from the centre */
        float a = (amp0 + (amp1 - amp0) * t) * if_amp(r, f);
        phi += f * RPK;
        if (phi > PI)
            phi -= 2 * PI;
        else if (phi < -PI)
            phi += 2 * PI;
        float nr = pr * cr - pi * ci; /* the ghost's 19 kHz phasor */
        pi = pr * ci + pi * cr, pr = nr;
        float gh = gh0 + (gh1 - gh0) * t;
        float re = a * tcos(r, phi) + gh * pr + gauss(r) * 0.70710678f;
        float im = a * tsin(r, phi) + gh * pi + gauss(r) * 0.70710678f;
        float d = fatan2(im * pre - re * pim,
                         re * pre + im * pim); /* the phase step: the discriminator */
        pre = re, pim = im;
        float y = s_curve(d / RPK) / DEV - (dc0 + (dc1 - dc0) * t);
        de += (1 - a_de) * (y - de);      /* de-emphasis */
        float h = de - hx + r->hp_r * hy; /* 30 Hz DC block */
        hx = de, hy = h;
        float M = lp4(r->m, h);
        float bl = blend0 + (r->blend - blend0) * t, sd = 0;
        if (bl > 1e-4f || sn0 > 1e-6f) { /* the difference channel, with its own larger hiss */
            float w = (uniform(r) + uniform(r) + uniform(r) - 1.5f) * 2 * (sn0 + (sn1 - sn0) * t);
            sde += (1 - a_de) * (w - sde);
            sd = lp4(r->s, bl * (l - rr) * 0.5f + sde);
        }
        float g = (g0 + (r->g - g0) * t) * (mute0 + (r->mute - mute0) * t);
        out[2 * i] = (M + sd) * g, out[2 * i + 1] = (M - sd) * g;
    }
    float mag = (float)sqrt(pr * pr + pi * pi); /* the rotation's rounding, taken out a block */
    r->psi_re = pr / mag, r->psi_im = pi / mag;
    r->phi = phi, r->pre = pre, r->pim = pim, r->mz = mz, r->de = de, r->hx = hx, r->hy = hy,
    r->sde = sde;
    return 1;
}

int fmrx_run(fmrx *r, const fmrx_params *p, const float *in, float *out, int n) {
    int busy = 0;
    for (int at = 0; at < n; at += FMRX_BLOCK) {
        int m = n - at < FMRX_BLOCK ? n - at : FMRX_BLOCK;
        busy |= block(r, p, in ? in + 2 * at : 0, out + 2 * at, m);
    }
    return busy;
}
