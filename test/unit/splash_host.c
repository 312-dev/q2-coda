/* patch/splash.c on this machine (test_splash.py): its libc calls are replaced with a file held in
 * memory, a two-page 320x375 framebuffer, a clock that only usleep moves and a /tmp holding only
 * its marker. splash_run plays a file and hands back page 0. */
#include <string.h>

#define main splash_main
#define timespec host_timespec
#define open host_open
#define ioctl host_ioctl
#define usleep host_usleep
#define clock_gettime host_clock_gettime
#define lseek host_lseek
#define mmap host_mmap
#define fcntl host_fcntl
#define rename host_rename
#define unlink host_unlink
#define access host_access
#include "../../patch/splash.c"
#undef main
#undef open
#undef ioctl
#undef usleep
#undef clock_gettime
#undef lseek
#undef mmap
#undef fcntl
#undef rename
#undef unlink
#undef access

enum {
    FILE_FD = 3,
    FB_FD = 4,
    MARK_FD = 5,
    LINE = W * 4 + 16
}; /* a stride wider than the row, as a driver may have */
static const unsigned char *file_data;
static unsigned char fb_mem[2 * H * LINE];
static long file_size, gone_at = -1, waits;
static unsigned clock_us;
/* the marker: fail, what of it to refuse (1 its open, 2 its lock, 3 its rename); made, opened as
 * MARK ".new" with O_CREAT; locked, a write lock on it; at, 1 while it is MARK; drawn_bare, frames
 * finished without both; removed, its unlinks while MARK */
static int fail, made, locked, at, drawn_bare, removed;

int host_open(const char *path, int flags, ...) {
    if (!strcmp(path, MARK ".new")) {
        made = flags == (O_RDWR | O_CREAT | O_CLOEXEC);
        return fail == 1 ? -1 : MARK_FD;
    }
    return !strcmp(path, "/dev/fb0") ? FB_FD : file_data ? FILE_FD : -1;
}
int host_fcntl(int fd, int cmd, ...) {
    __builtin_va_list ap;
    __builtin_va_start(ap, cmd);
    short *lock = __builtin_va_arg(ap, short *);
    __builtin_va_end(ap);
    for (int i = 1; i < 32; ++i)
        if (lock[i]) return -1; /* the whole file, from its start */
    if (fd != MARK_FD || cmd != F_SETLK || lock[0] != 1 || fail == 2) return -1;
    locked = 1;
    return 0;
}
int host_rename(const char *from, const char *to) {
    if (fail == 3 || !made || strcmp(from, MARK ".new") || strcmp(to, MARK)) return -1;
    at = 1;
    return 0;
}
int host_unlink(const char *path) {
    if (strcmp(path, MARK) || !at) return -1;
    at = 0, ++removed;
    return 0;
}
int host_access(const char *path, int mode) { /* demo removes MARK before frame gone_at */
    if (gone_at >= 0 && waits >= gone_at) at = 0;
    return !strcmp(path, MARK) && !mode && at ? 0 : -1;
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
    } else if (request == FBIOGET_FSCREENINFO) {
        memset(out, 0, 17 * sizeof *out);
        out[5] = sizeof fb_mem, out[11] = LINE;
    }
    return 0;
}
int host_usleep(unsigned us) {
    drawn_bare += !locked || !at;
    clock_us += us, ++waits;
    return 0;
}
int host_clock_gettime(int id, struct host_timespec *t) {
    (void)id;
    t->tv_sec = clock_us / 1000000, t->tv_nsec = clock_us % 1000000 * 1000;
    return 0;
}

/* Plays the size bytes at file. gone is the frame before which demo removes the marker, or -1;
 * refuse as fail above. page0 gets the 320x375 words drawn, *frames the frames waited for, *us
 * the clock at the end and mark {frames drawn without the marker locked, its unlinks, whether it
 * is left}. Returns q2splash's exit status, or 3 if it wrote outside page 0's rows. */
int splash_run(const unsigned char *file, long size, long gone, int refuse, unsigned *page0,
               long *frames, unsigned *us, int *mark) {
    file_data = file, file_size = size, gone_at = gone, waits = 0, clock_us = 0;
    fail = refuse, made = locked = at = drawn_bare = removed = 0;
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
    mark[0] = drawn_bare, mark[1] = removed, mark[2] = at;
    return status;
}
