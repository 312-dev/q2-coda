/* q2splash FILE: plays the boot animation FILE (tools/splash.py's format) on /dev/fb0's page 0,
 * where display_logo left the splash, and leaves its last frame there
 * (docs/boot.md#boot-animation). S11logo_display_shell runs it in place of the still splash. It
 * stops as soon as the display is panned away from page 0, which is demo showing its first frame,
 * and S90play kills it before Rockbox. Exits 0 once it has drawn a frame, 1 when it could not
 * start, so the hook can fall back to the still logo. No MIPS sysroot: glibc 2.28's declarations.
 */
struct timespec {
    long tv_sec, tv_nsec;
};
int open(const char *, int, ...), ioctl(int, unsigned, ...), usleep(unsigned);
int clock_gettime(int, struct timespec *);
long lseek(int, long, int);
void *mmap(void *, unsigned, int, int, int, long);

#define W 320 /* /dev/fb0: 320 x 375, 32 bpp, red at bit 16, two pages (docs/boot.md#framebuffer)  \
               */
#define H 375
#define O_RDWR 2
#define O_CLOEXEC 0x80000
#define FBIOGET_VSCREENINFO 0x4600
#define FBIOGET_FSCREENINFO 0x4602
#define FBIOPAN_DISPLAY 0x4606
#define CLOCK_MONOTONIC 1

static unsigned u16(const unsigned char *p) { return p[0] | p[1] << 8; }

/* Microseconds, wrapping every 71 minutes: only differences are used. 32-bit, as there is no libgcc
 * here for a 64-bit division. */
static unsigned now_us(void) {
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return (unsigned)t.tv_sec * 1000000u + (unsigned)t.tv_nsec / 1000;
}

/* One varint at *at, below end; -1 past the end or over 28 bits. */
static long varint(const unsigned char **at, const unsigned char *end) {
    unsigned long v = 0;
    for (int shift = 0; shift < 28 && *at < end; shift += 7) {
        unsigned b = *(*at)++;
        v |= (b & 0x7f) << shift;
        if (b < 0x80) return (long)v;
    }
    return -1;
}

int main(int argc, char **argv) {
    if (argc < 2) return 1;
    int in = open(argv[1], O_CLOEXEC), fb = open("/dev/fb0", O_RDWR | O_CLOEXEC);
    long size = in < 0 ? -1 : lseek(in, 0, 2);
    unsigned var[40], fix[17];
    if (size < 12 || fb < 0 || ioctl(fb, FBIOGET_VSCREENINFO, var) ||
        ioctl(fb, FBIOGET_FSCREENINFO, fix))
        return 1;
    unsigned line = fix[11];
    /* anything but the Q2's xres, yres, bpp and red offset, or a file for another screen: no
     * drawing */
    if (var[0] != W || var[1] != H || var[6] != 32 || var[8] != 16 || W * 4 > line ||
        H * line > fix[5])
        return 1;
    const unsigned char *file = mmap(0, (unsigned)size, 1, 2, in, 0); /* PROT_READ, MAP_PRIVATE */
    unsigned char *mem = mmap(0, fix[5], 3, 1, fb, 0); /* read and write, MAP_SHARED */
    if (file == (void *)-1 || mem == (void *)-1) return 1;
    if (file[0] != 'Q' || file[1] != '2' || file[2] != 'A' || file[3] != 'N') return 1;
    unsigned fps = u16(file + 4), frames = u16(file + 6);
    if (!fps || u16(file + 8) != W || u16(file + 10) != H) return 1;

    /* 16 grey levels to the splash's warm off-white, #F2F2F0 at 15 */
    unsigned lut[16];
    for (unsigned v = 0; v < 16; ++v)
        lut[v] = 0xff000000u | (v * 0xf2 + 7) / 15 << 16 | (v * 0xf2 + 7) / 15 << 8 |
                 (v * 0xf0 + 7) / 15;

    const unsigned char *at = file + 12, *end = file + size;
    unsigned start = now_us();
    int drawn = 0;
    for (unsigned f = 0; f < frames; ++f) {
        if (end - at < 4) break;
        unsigned n = at[0] | at[1] << 8 | at[2] << 16 | (unsigned)at[3] << 24;
        at += 4;
        if (n > (unsigned)(end - at)) break;
        const unsigned char *stop = at + n;
        /* demo pans to the page it has drawn: from then on the screen is its own */
        if (ioctl(fb, FBIOGET_VSCREENINFO, var) || var[5]) break;
        unsigned px = 0;
        while (at < stop) {
            long skip = varint(&at, stop), run = varint(&at, stop);
            if (skip < 0 || run < 0 || px + skip + run > W * H || (run + 1) / 2 > stop - at)
                return !drawn;
            px += skip;
            for (long k = 0; k < run; ++k, ++px)
                *(unsigned *)(mem + px / W * line + px % W * 4) =
                    lut[at[k / 2] >> 4 * (k & 1) & 15];
            at += (run + 1) / 2;
        }
        drawn = 1;
        unsigned due = (f + 1) / fps * 1000000u + (f + 1) % fps * 1000000u / fps;
        int wait = (int)(start + due - now_us());
        if (wait > 0)
            usleep((unsigned)wait); /* a late frame is not waited for: the next catches up */
    }
    return !drawn;
}
