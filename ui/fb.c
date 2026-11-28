/* The panel (docs/boot.md#framebuffer, docs/ui-rewrite.md#the-frame). /dev/fb0 is 320 x 375,
 * portrait, 32 bits a pixel, two pages one above the other; the UI is landscape, 375 x 320, so a
 * canvas pixel (x, y) is the page's (319 - y, x). A frame turns only its changed rects onto the
 * page that is not shown, with the last frame's (that page is two frames old), pans to it and
 * waits for the refresh: at most one frame a refresh, and none while nothing changes.
 * The turn takes BAND canvas rows together, so each run of stores fills one cache line of a page
 * row: patch/fb_rotate.c measured that at 0.9 ms a whole frame on a Q2, 3.3 ms a pixel at a time.
 *
 * The canvas is 8 bits a channel whatever the panel shows. When the panel shows fewer (fb_bits 6 or
 * 5, or 565 for five bits of red and blue and six of green), the turn brings each pixel to the
 * nearest level the panel has, and adds no noise doing it: a flat colour comes out one colour,
 * solid, where noise here made every surface off the panel's levels a static grain of the two
 * beside it. What is a ramp (a fade, a shadow, a gleam, a cover's own shading) was dithered when
 * it was drawn, from 16 bits straight to the panel's levels (px.h px_mix16, px_picture), and
 * passes through here unchanged (docs/ui-rewrite.md#gradients).
 */
#include <fcntl.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#ifdef __mips__
#include <sys/cachectl.h>
#endif
#include <time.h>
#include <unistd.h>

#include <linux/fb.h>

#include "../patch/night_scale.h"
#include "q2ui.h"

#define PAGE_W SCREEN_H
#define PAGE_H SCREEN_W
#define PAGES 2
#define BAND 8
#define RECTS 8
#ifndef FBIO_WAITFORVSYNC
#define FBIO_WAITFORVSYNC _IOW('F', 0x20, uint32_t)
#endif

uint32_t *canvas;
int fb_vsync = 1, fb_bits = 8;
static int fd = -1, shown, can_wait = 1;
static unsigned refresh_us = 16667;
static uint64_t paced_ns;
uint64_t fb_panned_us; /* when the last frame was panned to: its work ended there (ui.c) */
/* the last frame's pan ioctl and its wait for the refresh after it, apart (ui.c account: the stats' pan_us and
 * vsync_wait_us, to tell a pan that waits for the panel from work that runs long) */
uint64_t fb_pan_us, fb_wait_us;
int fb_pan_blocks, fb_check_page, fb_flush, fb_overlap;
static void sleep_to_refresh(void) {
    /* to the next multiple of the period on the clock: where a file standing in for the panel has its refresh */
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    uint64_t ns = (uint64_t)t.tv_sec * 1000000000u + (uint64_t)t.tv_nsec, period = (uint64_t)refresh_us * 1000u;
    paced_ns = (ns / period + 1) * period;
    t.tv_sec = (time_t)(paced_ns / 1000000000u), t.tv_nsec = (long)(paced_ns % 1000000000u);
    clock_nanosleep(CLOCK_MONOTONIC, TIMER_ABSTIME, &t, 0);
}
static uint32_t *pages;
static struct fb_var_screeninfo var;
static struct damage {
    int n;
    rect_t r[RECTS];
} now, last;

static void add(struct damage *d, rect_t r) {
    rect_t screen = { 0, 0, SCREEN_W, SCREEN_H };
    if (!rect_meet(r, screen, &r)) return;
    /* a rect inside one already there adds nothing; past RECTS the last one grows to hold it */
    for (int i = 0; i < d->n; ++i) {
        rect_t *o = &d->r[i];
        if (r.x >= o->x && r.y >= o->y && r.x + r.w <= o->x + o->w && r.y + r.h <= o->y + o->h)
            return;
    }
    if (d->n < RECTS) {
        d->r[d->n++] = r;
        return;
    }
    rect_t *o = &d->r[RECTS - 1];
    int x1 = o->x + o->w > r.x + r.w ? o->x + o->w : r.x + r.w;
    int y1 = o->y + o->h > r.y + r.h ? o->y + o->h : r.y + r.h;
    o->x = o->x < r.x ? o->x : r.x, o->y = o->y < r.y ? o->y : r.y;
    o->w = x1 - o->x, o->h = y1 - o->y;
}
void dmg_add(rect_t r) { add(&now, r); }
int dmg_count(void) { return now.n; }
void dmg_all(void) {
    now.n = last.n = 0;
    add(&now, (rect_t){ 0, 0, SCREEN_W, SCREEN_H });
    add(&last, (rect_t){ 0, 0, SCREEN_W, SCREEN_H });
}
int fb_page(void) { return shown; }
unsigned fb_refresh_us(void) { return refresh_us; }
uint32_t *canvas_swap(uint32_t *other) {
    uint32_t *was = canvas;
    canvas = other;
    return was;
}

int fb_open(const char *path, int bits) {
    fb_depth(bits);
    canvas = calloc(SCREEN_W * SCREEN_H, sizeof *canvas);
    fd = open(path, O_RDWR);
    if (!canvas || fd < 0) return -1;
    /* a plain file standing in for the panel (a host run) answers no ioctl: its size is the two
     * pages' */
    if (ioctl(fd, FBIOGET_VSCREENINFO, &var) < 0) memset(&var, 0, sizeof var);
    if (var.xres && (var.xres != PAGE_W || var.yres != PAGE_H || var.bits_per_pixel != 32))
        return -1;
    pages = mmap(0, PAGE_W * PAGE_H * PAGES * 4, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
    if (pages == MAP_FAILED) return -1;
    shown = var.yoffset >= PAGE_H;
    /* the refresh, from the mode's own timings: the pixel clock's period (ps) times the pixels of
     * a whole frame, blanking included */
    uint64_t total = (uint64_t)(var.xres + var.left_margin + var.right_margin + var.hsync_len) *
                     (var.yres + var.upper_margin + var.lower_margin + var.vsync_len);
    if (var.pixclock && total) {
        uint64_t us = (uint64_t)var.pixclock * total / 1000000u;
        if (us >= 5000 && us <= 50000) refresh_us = (unsigned)us;
    }
    /* both pages are someone else's picture: the first two frames each turn the whole screen */
    add(&now, (rect_t){ 0, 0, SCREEN_W, SCREEN_H });
    return 0;
}
void fb_close(void) {
    if (pages) munmap(pages, PAGE_W * PAGE_H * PAGES * 4);
    if (fd >= 0) close(fd);
    free(canvas);
    pages = 0, canvas = 0, fd = -1;
}

/* c with noise n (0-255) scaled to one step of the panel's depth added to each channel, held at
 * white, and the bits the link drops cleared */
unsigned px_bits_rb = 8, px_bits_g = 8; /* red and blue's bits, and green's: a 565 panel gives green one more */
/* What narrow() adds and keeps for the panel's depth, worked out once a turn rather than a pixel
 * (px_bits_* are globals the page's stores could alias, so the compiler cannot hoist them). */
struct levels {
    uint32_t half_rb, half_g, keep_rb, keep_g;
};
static struct levels levels(void) {
    uint32_t keep_rb = (0xffu << (8 - px_bits_rb)) & 0xff, keep_g = (0xffu << (8 - px_bits_g)) & 0xff;
    return (struct levels){ (0x80u >> px_bits_rb) * 0x10001u, (0x80u >> px_bits_g) << 8, keep_rb * 0x10001u, keep_g << 8 };
}
/* c at the nearest level the panel has for each channel: half a step added, held at white, the bits
 * the panel drops cleared. No noise: see the note at the top. */
static inline uint32_t narrow(uint32_t c, struct levels l) {
    uint32_t rb = (c & 0xff00ff) + l.half_rb, g = (c & 0xff00) + l.half_g;
    uint32_t orb = rb & 0x1000100u, og = g & 0x10000u;
    rb = (rb | (orb - (orb >> 8))) & l.keep_rb;
    g = (g | (og - (og >> 8))) & l.keep_g;
    return 0xff000000 | rb | g;
}
/* Night Shift (docs/internals.md#night-shift, demo's patch/nightshift.c): while on, each pixel turned to the panel
 * keeps its red and has green and blue scaled to the temperature's (night_scale.h), then is narrowed as any is */
static unsigned char warm_g[256], warm_b[256];
static int warming;
void fb_warm(int kelvin) {
    static const unsigned char green[] = NS_GREEN, blue[] = NS_BLUE;
    int on = kelvin >= NS_WARM && kelvin <= NS_COOL;
    if (on) {
        unsigned i = (unsigned)(kelvin - NS_WARM) / NS_STEP;
        for (unsigned v = 0; v < 256; ++v)
            warm_g[v] = (unsigned char)((v * green[i] + 127) / 255), warm_b[v] = (unsigned char)((v * blue[i] + 127) / 255);
    }
    if (on || warming) add(&now, (rect_t){ 0, 0, SCREEN_W, SCREEN_H }), add(&last, (rect_t){ 0, 0, SCREEN_W, SCREEN_H });
    warming = on;
}
static inline uint32_t warm(uint32_t c) { return (c & 0xff0000) | (uint32_t)warm_g[c >> 8 & 255] << 8 | warm_b[c & 255]; }
void fb_depth(int bits) {
    fb_bits = bits == 5 || bits == 6 || bits == 565 ? bits : 8;
    px_bits_rb = fb_bits == 565 ? 5 : (unsigned)fb_bits, px_bits_g = fb_bits == 565 ? 6 : (unsigned)fb_bits;
}
/* What the turn does to each pixel: COPY on an 8-bit panel, NARROW to fewer bits, WARM then NARROW
 * under Night Shift (which an 8-bit panel narrows too: to its own 8 bits, a no-op on the levels). */
enum { COPY, NARROW, WARM };
static inline __attribute__((always_inline)) uint32_t turned(uint32_t c, int how, struct levels l) {
    return how == COPY ? c : how == NARROW ? narrow(c, l) : narrow(warm(c), l);
}
/* r of the canvas onto page: canvas (x, y) to page (319 - y, x), BAND rows at a time. Inlined once
 * for each `how`, so the pixel loop carries no test. */
static inline __attribute__((always_inline)) unsigned turn_as(uint32_t *page, rect_t r, int how) {
    struct levels l = levels();
    int y = r.y;
    for (; y + BAND <= r.y + r.h; y += BAND) {
        const uint32_t *src = canvas + y * SCREEN_W + r.x;
        uint32_t *dst = page + r.x * PAGE_W + (PAGE_W - BAND - y);
        for (int i = 0; i < r.w; ++i, ++src, dst += PAGE_W)
            for (int k = 0; k < BAND; ++k) dst[k] = turned(src[(BAND - 1 - k) * SCREEN_W], how, l);
    }
    for (; y < r.y + r.h; ++y) {
        const uint32_t *src = canvas + y * SCREEN_W + r.x;
        uint32_t *dst = page + r.x * PAGE_W + (PAGE_W - 1 - y);
        for (int i = 0; i < r.w; ++i, dst += PAGE_W) *dst = turned(src[i], how, l);
    }
    return (unsigned)(r.w * r.h);
}
static unsigned turn(uint32_t *page, rect_t r) {
    if (warming) return turn_as(page, r, WARM);
    return fb_bits == 8 ? turn_as(page, r, COPY) : turn_as(page, r, NARROW);
}

unsigned fb_present(void) {
    if (!now.n) return 0;
    int hidden = !shown;
    uint32_t *page = pages + hidden * PAGE_W * PAGE_H;
    unsigned px = 0;
    struct damage both = now;
    for (int i = 0; i < last.n; ++i) add(&both, last.r[i]);
    /* the page about to be turned onto must not be the one the panel scans out (a frame drawn into
     * it would tear): the panel's word for which it shows, read back first (--frame-log) */
    fb_overlap = 0;
    struct fb_var_screeninfo seen;
    if (fb_check_page && var.xres && ioctl(fd, FBIOGET_VSCREENINFO, &seen) == 0) {
        ++ui_stats.fb_checks;
        if ((seen.yoffset >= PAGE_H) == hidden) fb_overlap = 1, ++ui_stats.fb_overlaps;
    }
    for (int i = 0; i < both.n; ++i) px += turn(page, both.r[i]);
    /* --fb-flush: the page's stores out of the CPU's cache before the pan, for a read of whether
     * any are late to the panel (a mapping the kernel left cached) */
    if (fb_flush == 1) msync(page, PAGE_W * PAGE_H * 4, MS_SYNC);
#ifdef __mips__
    if (fb_flush == 2) cacheflush(page, PAGE_W * PAGE_H * 4, DCACHE);
#endif
    var.yoffset = (unsigned)(hidden * PAGE_H);
    uint64_t pan_at = mono_us();
    ioctl(fd, FBIOPAN_DISPLAY, &var);
    /* the Q2's pan returns at the refresh that shows it (a device read, 2026-11-28: 11.9 ms of each
     * 16.7), and its FBIO_WAITFORVSYNC then at once: --pan-blocks makes a file's pan the same */
    int blocked = fb_pan_blocks && fb_vsync && !var.xres;
    if (blocked) sleep_to_refresh();
    fb_panned_us = mono_us();
    fb_pan_us = fb_panned_us - pan_at, fb_wait_us = 0;
    if (fb_vsync && !blocked) {
        uint32_t crtc = 0;
        if (can_wait && ioctl(fd, FBIO_WAITFORVSYNC, &crtc) < 0) can_wait = 0;
        /* a file standing in for the panel has no refresh: the clock's stands in for it, so a host
         * run is paced as a panel paces it */
        if (!can_wait) sleep_to_refresh();
    }
    if (fb_vsync) fb_wait_us = mono_us() - fb_panned_us;
    shown = hidden;
    last = now;
    now.n = 0;
    return px;
}
