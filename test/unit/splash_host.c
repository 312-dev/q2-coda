/* patch/splash.c on this machine (test_splash.py): its libc calls are replaced with a file held in
 * memory, a two-page 320x375 framebuffer and a clock that only usleep moves. splash_run plays a
 * file and hands back page 0. */
#include <string.h>

#define main splash_main
#define timespec host_timespec
#define open host_open
#define ioctl host_ioctl
#define usleep host_usleep
#define clock_gettime host_clock_gettime
#define lseek host_lseek
#define mmap host_mmap
#include "../../patch/splash.c"
#undef main
#undef open
#undef ioctl
#undef usleep
#undef clock_gettime
#undef lseek
#undef mmap

enum {
    FILE_FD = 3,
    FB_FD = 4,
    LINE = W * 4 + 16
}; /* a stride wider than the row, as a driver may have */
static const unsigned char *file_data;
static unsigned char fb_mem[2 * H * LINE];
static long file_size, pan_at = -1, waits;
static unsigned clock_us;

int host_open(const char *path, int flags, ...) {
    (void)flags;
    return !strcmp(path, "/dev/fb0") ? FB_FD : file_data ? FILE_FD : -1;
}
long host_lseek(int fd, long off, int whence) {
    (void)off, (void)whence;
    return fd == FILE_FD ? file_size : -1;
}
void *host_mmap(void *addr, unsigned len, int prot, int flags, int fd, long off) {
    (void)addr, (void)len, (void)prot, (void)flags, (void)off;
    return fd == FILE_FD ? (void *)(unsigned long)file_data : (void *)fb_mem;
}
int host_ioctl(int fd, unsigned request, ...) {
    __builtin_va_list ap;
    __builtin_va_start(ap, request);
    unsigned *out = __builtin_va_arg(ap, unsigned *);
    __builtin_va_end(ap);
    if (fd != FB_FD) return -1;
    if (request == FBIOGET_VSCREENINFO) {
        memset(out, 0, 40 * sizeof *out);
        out[0] = W, out[1] = H, out[2] = W, out[3] = 2 * H, out[6] = 32, out[8] = 16;
        out[5] = pan_at >= 0 && waits >= pan_at ? H : 0;
    } else if (request == FBIOGET_FSCREENINFO) {
        memset(out, 0, 17 * sizeof *out);
        out[5] = sizeof fb_mem, out[11] = LINE;
    }
    return 0;
}
int host_usleep(unsigned us) {
    clock_us += us, ++waits;
    return 0;
}
int host_clock_gettime(int id, struct host_timespec *t) {
    (void)id;
    t->tv_sec = clock_us / 1000000, t->tv_nsec = clock_us % 1000000 * 1000;
    return 0;
}

/* Plays the size bytes at file. pan_at is the frame before which the display is panned to page 1,
 * as demo does, or -1. page0 gets the 320x375 words drawn, *frames the frames waited for and *us
 * the clock at the end. Returns q2splash's exit status, or 3 if it wrote outside page 0's rows. */
int splash_run(const unsigned char *file, long size, long pan, unsigned *page0, long *frames,
               unsigned *us) {
    file_data = file, file_size = size, pan_at = pan, waits = 0, clock_us = 0;
    memset(fb_mem, 0x55, sizeof fb_mem);
    for (int y = 0; y < H; ++y) /* display_logo's black splash */
        for (int x = 0; x < W; ++x) *(unsigned *)(fb_mem + y * LINE + x * 4) = 0xff000000u;
    char name[] = "q2splash", path[] = "anim", *args[] = { name, path, 0 };
    int status = splash_main(2, args);
    for (int y = 0; y < H; ++y) memcpy(page0 + y * W, fb_mem + y * LINE, W * 4);
    for (int y = 0; y < H; ++y)
        for (int x = W * 4; x < LINE; ++x)
            if (fb_mem[y * LINE + x] != 0x55) status = 3;
    for (unsigned i = H * LINE; i < sizeof fb_mem; ++i)
        if (fb_mem[i] != 0x55) status = 3;
    *frames = waits, *us = clock_us;
    return status;
}
