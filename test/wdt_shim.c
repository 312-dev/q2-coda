/* test/sysd.py's stand-in for the X2000's watchdog driver (soc_watchdog.ko, /dev/jz_watchdog): LD_PRELOADed into
 * q2sysd, it answers the driver's three ioctls (docs/internals.md#watchdog) on a file whose path ends in jz_watchdog,
 * and writes each to $Q2WDT_LOG ("start MS", "feed", "stop") with the time in ms. Every other ioctl is the real one. */
#define _GNU_SOURCE
#include <dlfcn.h>
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
    if (n > 0 && (p[n] = 0, n >= 11) && !strcmp(p + n - 11, "jz_watchdog")) {
        struct timespec t;
        clock_gettime(CLOCK_MONOTONIC, &t);
        FILE *f = fopen(getenv("Q2WDT_LOG"), "a");
        if (f) {
            long long ms = (long long)t.tv_sec * 1000 + t.tv_nsec / 1000000;
            if (req == 0x8004570dUL) fprintf(f, "%lld start %d\n", ms, *(int *)arg);
            else if (req == 0x2000570fUL) fprintf(f, "%lld feed\n", ms);
            else if (req == 0x2000570eUL) fprintf(f, "%lld stop\n", ms);
            else fprintf(f, "%lld unknown 0x%lx\n", ms, req);
            fclose(f);
        }
        return 0;
    }
    static int (*real)(int, unsigned long, ...);
    if (!real) real = (int (*)(int, unsigned long, ...))dlsym(RTLD_NEXT, "ioctl");
    return real(fd, req, arg);
}
