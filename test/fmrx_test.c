/* test/fmrx.py's measurements of patch/fmrx.c, the Radio Tuner's FM receiver, made as the prototype's
 * were (static-model.md, "Measurements"): each prints "name value" lines that fmrx.py compares with that
 * table. With --bench SECONDS it runs the receiver on a station with programme for that long and prints
 * the CPU time per second of audio, for the Q2 (built for MIPS by fmrx.py --mips) as for this machine. */
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#include "../patch/fmrx.h"

#define FS 48000
static fmrx rx;

static double db(double x) { return 20 * log10(x > 1e-12 ? x : 1e-12); }

/* n frames at p from in (NULL silence) into out; returns frames */
static void run(fmrx_params *p, const float *in, float *out, int n) { fmrx_run(&rx, p, in, out, n); }

static double rms(const float *x, int n) { /* the left channel */
    double s = 0;
    for (int i = 0; i < n; ++i) s += (double)x[2 * i] * x[2 * i];
    return sqrt(s / n);
}

/* In-place radix-2 FFT of n (a power of two) complex values. */
static void fft(double *re, double *im, int n) {
    for (int i = 1, j = 0; i < n; ++i) {
        int bit = n >> 1;
        for (; j & bit; bit >>= 1) j ^= bit;
        j ^= bit;
        if (i < j) {
            double t = re[i]; re[i] = re[j]; re[j] = t;
            t = im[i]; im[i] = im[j]; im[j] = t;
        }
    }
    for (int len = 2; len <= n; len <<= 1) {
        double a = -2 * M_PI / len;
        for (int i = 0; i < n; i += len)
            for (int k = 0; k < len / 2; ++k) {
                double wr = cos(a * k), wi = sin(a * k);
                double xr = re[i + k + len / 2] * wr - im[i + k + len / 2] * wi;
                double xi = re[i + k + len / 2] * wi + im[i + k + len / 2] * wr;
                re[i + k + len / 2] = re[i + k] - xr, im[i + k + len / 2] = im[i + k] - xi;
                re[i + k] += xr, im[i + k] += xi;
            }
    }
}

#define NFFT 4096
/* The left channel's averaged power spectrum (Blackman-Harris), NFFT/2 bins of FS/NFFT Hz. */
static void spectrum(const float *x, int n, double *pw) {
    static double re[NFFT], im[NFFT];
    memset(pw, 0, sizeof(double) * NFFT / 2);
    int segs = 0;
    for (int at = 0; at + NFFT <= n; at += NFFT / 2, ++segs) {
        for (int i = 0; i < NFFT; ++i) {
            double c = 2 * M_PI * i / NFFT; /* 4-term Blackman-Harris: sidelobes 92 dB down */
            double w = 0.35875 - 0.48829 * cos(c) + 0.14128 * cos(2 * c) - 0.01168 * cos(3 * c);
            re[i] = x[2 * (at + i)] * w, im[i] = 0;
        }
        fft(re, im, NFFT);
        for (int i = 0; i < NFFT / 2; ++i) pw[i] += re[i] * re[i] + im[i] * im[i];
    }
    for (int i = 0; i < NFFT / 2; ++i) pw[i] /= segs;
}
/* the mean power over the bins within 3% of hz */
static double band(const double *pw, double hz) {
    int a = (int)(hz * 0.97 * NFFT / FS), b = (int)(hz * 1.03 * NFFT / FS) + 1;
    double s = 0;
    for (int i = a; i <= b; ++i) s += pw[i];
    return s / (b - a + 1);
}

static float *buf(int n) { return calloc(2 * (size_t)n, sizeof(float)); }

int main(int argc, char **argv) {
    fmrx_params p = { FMRX_NO_CARRIER, 0, 0, 1, 0 };
    int n = 60 * FS;
    float *out = buf(n), *in = buf(n);
    if (argc > 2 && !strcmp(argv[1], "--bench")) {
        double secs = atof(argv[2]);
        int m = (int)(secs * FS);
        float *a = buf(m), *b = buf(m);
        for (int i = 0; i < m; ++i) a[2 * i] = a[2 * i + 1] = 0.25f * (float)sin(2 * M_PI * 1000 * i / FS);
        fmrx_init(&rx, FS, 1);
        p.level = 60, p.fade = 0.2f;
        clock_t t0 = clock();
        for (int at = 0; at < m; at += 512) run(&p, a + 2 * at, b + 2 * at, m - at < 512 ? m - at : 512);
        double cpu = (double)(clock() - t0) / CLOCKS_PER_SEC;
        printf("bench_cpu_ms_per_audio_s %.2f\n", cpu * 1000 / secs);
        p.gain = 0; /* paused: no work */
        for (int i = 0; i < 20; ++i) run(&p, a, b, 512);
        t0 = clock();
        for (int at = 0; at < m; at += 512) run(&p, a + 2 * at, b + 2 * at, m - at < 512 ? m - at : 512);
        printf("bench_paused_ms_per_audio_s %.2f\n", (double)(clock() - t0) / CLOCKS_PER_SEC * 1000 / secs);
        return 0;
    }

    /* empty-channel hiss: level, its 250 ms windows, its spectrum, the largest step */
    fmrx_init(&rx, FS, 7);
    run(&p, 0, out, FS); /* settle */
    run(&p, 0, out, n);
    printf("hiss_dbfs %.2f\n", db(rms(out, n)));
    double w[240];
    for (int i = 0; i < 240; ++i) w[i] = db(rms(out + 2 * i * (FS / 4), FS / 4));
    for (int i = 1; i < 240; ++i)
        for (int j = i; j > 0 && w[j] < w[j - 1]; --j) { double t = w[j]; w[j] = w[j - 1]; w[j - 1] = t; }
    printf("hiss_windows_median %.2f\nhiss_windows_p10 %.2f\nhiss_windows_min %.2f\n", w[120], w[24], w[0]);
    double step = 0;
    for (int i = 1; i < n; ++i) step = fmax(step, fabs(out[2 * i] - out[2 * i - 2]));
    printf("hiss_max_step %.3f\n", step);
    static double pw[NFFT / 2];
    spectrum(out, n, pw);
    double ref = band(pw, 1000);
    static const int hz[] = { 500, 2122, 4000, 8000, 15000, 18000 };
    for (int i = 0; i < 6; ++i) printf("hiss_spectrum_%d %.2f\n", hz[i], 10 * log10(band(pw, hz[i]) / ref));

    /* output noise against station level, no programme */
    static const int lv[] = { 0, 4, 24, 28, 36, 44, 60, 80 };
    for (int i = 0; i < 8; ++i) {
        fmrx_init(&rx, FS, 11 + i);
        p.level = (float)lv[i], p.offset = 0, p.fade = 0;
        run(&p, 0, out, FS);
        run(&p, 0, out, 10 * FS);
        printf("noise_at_%d %.2f\n", lv[i], db(rms(out, 10 * FS)));
    }

    /* a 1 kHz tone at -12.1 dBFS, 80 dB station: its level and what is left without it */
    float amp = (float)(pow(10, -12.1 / 20) * sqrt(2));
    for (int i = 0; i < n; ++i) in[2 * i] = in[2 * i + 1] = amp * (float)sin(2 * M_PI * 1000.0 * i / FS);
    static const int offs[] = { 0, 100, 150, 175, 200 };
    for (int o = 0; o < 5; ++o) {
        fmrx_init(&rx, FS, 23 + o);
        p.level = offs[o] ? 78 : 80, p.offset = (float)offs[o], p.fade = 0;
        run(&p, in, out, FS);
        run(&p, in + 2 * FS, out, 10 * FS);
        spectrum(out, 10 * FS, pw);
        double total = 0, fund = 0, second = 0;
        for (int i = 1; i < NFFT / 2; ++i) total += pw[i];
        for (int i = -6; i <= 6; ++i) /* the window's main lobe is 4 bins each side */
            fund += pw[1000 * NFFT / FS + i], second += pw[2000 * NFFT / FS + i];
        double r = rms(out, 10 * FS);
        printf("tone_off%d_out %.2f\ntone_off%d_fund %.2f\ntone_off%d_second %.2f\ntone_off%d_residue %.2f\n",
               offs[o], db(r), offs[o], db(r * sqrt(fund / total)), offs[o], db(r * sqrt(second / total)), offs[o],
               db(r * sqrt(fmax(total - fund, 0) / total)));
    }

    /* a carrier appearing on an empty channel, then lost: 10 ms windows */
    fmrx_init(&rx, FS, 41);
    p.level = FMRX_NO_CARRIER, p.offset = 0;
    run(&p, 0, out, 2 * FS);
    p.level = 70;
    run(&p, 0, out, FS);
    printf("appear_20ms %.2f\nappear_60ms %.2f\nappear_300ms %.2f\n", db(rms(out + 2 * 960, 480)),
           db(rms(out + 2 * 2880, 480)), db(rms(out + 2 * 14400, 480)));
    p.level = FMRX_NO_CARRIER;
    run(&p, 0, out, FS);
    printf("lost_40ms %.2f\nlost_200ms %.2f\n", db(rms(out + 2 * 1920, 480)), db(rms(out + 2 * 9600, 480)));

    /* fringe: 44 dB with flutter 0.6, 24 dB with 0.5: 50 ms windows */
    static const float fringe[2][2] = { { 44, 0.6f }, { 24, 0.5f } };
    for (int k = 0; k < 2; ++k) {
        fmrx_init(&rx, FS, 51 + k);
        p.level = fringe[k][0], p.fade = fringe[k][1], p.offset = 0;
        run(&p, 0, out, FS);
        run(&p, 0, out, n);
        int nw = n / 2400;
        double *v = malloc(sizeof(double) * nw);
        for (int i = 0; i < nw; ++i) v[i] = db(rms(out + 2 * i * 2400, 2400));
        for (int i = 1; i < nw; ++i)
            for (int j = i; j > 0 && v[j] < v[j - 1]; --j) { double t = v[j]; v[j] = v[j - 1]; v[j - 1] = t; }
        printf("fringe%d_median %.2f\nfringe%d_p99 %.2f\n", (int)fringe[k][0], v[nw / 2], (int)fringe[k][0],
               v[nw * 99 / 100]);
        free(v);
    }

    /* pause: gain 0 fades out in 15 ms and then does no work */
    fmrx_init(&rx, FS, 61);
    p.level = 60, p.fade = 0, p.gain = 0;
    int busy = 1;
    for (int i = 0; i < 40; ++i) busy = fmrx_run(&rx, &p, 0, out, 512);
    printf("paused_busy %d\n", busy);
    return 0;
}
