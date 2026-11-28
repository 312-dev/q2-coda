/* test/sysd.py's stand-in for stock's PWM driver (soc_pwm.ko, /dev/jz_pwm): LD_PRELOADed into q2sysd, it answers the
 * four ioctls Find My Vamp's jingle uses (patch/findmy_tune.h) on a file whose path ends in jz_pwm and writes each to
 * $Q2PWM_LOG ("release CH", "request PIN", "config HZ CH", "level CH DUTY") with the time in ms; REQUEST answers
 * channel 1. $Q2PWM_REFUSE=N refuses the Nth CONFIG, as the driver does while the MCU holds the channel.
 * It is also stock's MCU driver (/dev/mcu, as libhardware2.so's mcu_* call it): on a file whose path ends in /mcu it
 * writes "mcu mem OFFSET LEN" (a piece of the firmware), "mcu bootup", "mcu reset" and "mcu write TEXT LEN TIMEOUT"
 * (mcu_write_data_timeout) to $Q2PWM_LOG; $Q2MCU_DOWN makes each refuse with ENODEV, as the driver does before the
 * firmware is loaded, and $Q2MCU_MS holds each write that long. Every other ioctl is the real one. */
#define _GNU_SOURCE
#include <dlfcn.h>
#include <errno.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <time.h>
#include <unistd.h>

int ioctl(int fd, unsigned long req, ...) {
    va_list ap;
    va_start(ap, req);
    void *arg = va_arg(ap, void *);
    va_end(ap);
    char link[64], p[512];
    snprintf(link, sizeof link, "/proc/self/fd/%d", fd);
    ssize_t n = readlink(link, p, sizeof p - 1);
    if (n > 0 && (p[n] = 0, n >= 4) && !strcmp(p + n - 4, "/mcu")) {
        struct timespec t;
        clock_gettime(CLOCK_MONOTONIC, &t);
        long long ms = (long long)t.tv_sec * 1000 + t.tv_nsec / 1000000;
        if (getenv("Q2MCU_DOWN")) return errno = ENODEV, -1;
        FILE *f = fopen(getenv("Q2PWM_LOG"), "a");
        if (req == 0x80044d73UL) {
            const struct { unsigned off; const void *buf; unsigned len; } *m = arg;
            if (f) fprintf(f, "%lld mcu mem %u %u\n", ms, m->off, m->len);
        } else if (req == 0x20004d72UL) {
            if (f) fprintf(f, "%lld mcu bootup\n", ms);
        } else if (req == 0x20004d71UL) {
            if (f) fprintf(f, "%lld mcu reset\n", ms);
        } else if (req == 0x80044d76UL) {
            const struct { const char *buf; unsigned len, timeout; } *d = arg;
            if (f) fprintf(f, "%lld mcu write %.*s %u %u\n", ms, (int)strnlen(d->buf, d->len), d->buf, d->len, d->timeout);
            if (getenv("Q2MCU_MS")) usleep((useconds_t)atoi(getenv("Q2MCU_MS")) * 1000);
        } else if (f)
            fprintf(f, "%lld mcu unknown 0x%lx\n", ms, req);
        if (f) fclose(f);
        return 0;
    }
    if (n > 0 && (p[n] = 0, n >= 6) && !strcmp(p + n - 6, "jz_pwm")) {
        static int configs;
        struct timespec t;
        clock_gettime(CLOCK_MONOTONIC, &t);
        long long ms = (long long)t.tv_sec * 1000 + t.tv_nsec / 1000000;
        int r = 0;
        FILE *f = fopen(getenv("Q2PWM_LOG"), "a");
        if (req == 0x80045016UL) {
            if (f) fprintf(f, "%lld release %ld\n", ms, (long)arg);
        } else if (req == 0x8001500bUL) {
            if (f) fprintf(f, "%lld request %.12s\n", ms, (const char *)arg);
            r = 1;
        } else if (req == 0x80185021UL) {
            const unsigned *c = arg;
            const char *refuse = getenv("Q2PWM_REFUSE");
            r = refuse && ++configs == atoi(refuse) ? -1 : 0;
            if (!refuse) ++configs;
            if (f) fprintf(f, "%lld config %u %u%s\n", ms, c[3], c[5], r ? " refused" : "");
        } else if (req == 0xc004502cUL) {
            const unsigned *l = arg;
            if (f) fprintf(f, "%lld level %u %u\n", ms, l[0], l[1]);
        } else if (f)
            fprintf(f, "%lld unknown 0x%lx\n", ms, req);
        if (f) fclose(f);
        return r;
    }
    static int (*real)(int, unsigned long, ...);
    if (!real) real = (int (*)(int, unsigned long, ...))dlsym(RTLD_NEXT, "ioctl");
    return real(fd, req, arg);
}
