/* The panel (docs/boot.md#framebuffer, docs/ui-rewrite.md#the-frame). /dev/fb0 is 320 x 375,
 * portrait, 32 bits a pixel, two pages one above the other; the UI is landscape, 375 x 320, so a
 * canvas pixel (x, y) is the page's (319 - y, x). A frame turns only its changed rects onto the
 * page that is not shown, with the last frame's (that page is two frames old), pans to it and
 * waits for the refresh: at most one frame a refresh, and none while nothing changes.
 * The turn takes BAND canvas rows together, so each run of stores fills one cache line of a page
 * row: patch/fb_rotate.c measured that at 0.9 ms a whole frame on a Q2, 3.3 ms a pixel at a time.
 */
#include <fcntl.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <unistd.h>

#include <linux/fb.h>

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
int fb_vsync = 1;
static int fd = -1, shown;
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
rect_t dmg_at(int i) { return now.r[i]; }
void dmg_clear(void) { now.n = 0; }
int fb_page(void) { return shown; }

int fb_open(const char *path) {
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

/* r of the canvas onto page: canvas (x, y) to page (319 - y, x), BAND rows at a time */
static unsigned turn(uint32_t *page, rect_t r) {
    int y = r.y;
    for (; y + BAND <= r.y + r.h; y += BAND) {
        const uint32_t *src = canvas + y * SCREEN_W + r.x;
        uint32_t *dst = page + r.x * PAGE_W + (PAGE_W - BAND - y);
        for (int i = 0; i < r.w; ++i, ++src, dst += PAGE_W)
            for (int k = 0; k < BAND; ++k) dst[k] = src[(BAND - 1 - k) * SCREEN_W];
    }
    for (; y < r.y + r.h; ++y) {
        const uint32_t *src = canvas + y * SCREEN_W + r.x;
        uint32_t *dst = page + r.x * PAGE_W + (PAGE_W - 1 - y);
        for (int i = 0; i < r.w; ++i, dst += PAGE_W) *dst = src[i];
    }
    return (unsigned)(r.w * r.h);
}

unsigned fb_present(void) {
    if (!now.n) return 0;
    int hidden = !shown;
    uint32_t *page = pages + hidden * PAGE_W * PAGE_H;
    unsigned px = 0;
    struct damage both = now;
    for (int i = 0; i < last.n; ++i) add(&both, last.r[i]);
    for (int i = 0; i < both.n; ++i) px += turn(page, both.r[i]);
    var.yoffset = (unsigned)(hidden * PAGE_H);
    ioctl(fd, FBIOPAN_DISPLAY, &var);
    if (fb_vsync) {
        uint32_t crtc = 0;
        ioctl(fd, FBIO_WAITFORVSYNC, &crtc);
    }
    shown = hidden;
    last = now;
    now.n = 0;
    return px;
}
