/* The frame's turn onto the panel (docs/internals.md#frame-rotation). demo draws a 375 x 320
 * landscape frame and the panel is 320 x 375, so every frame shown is turned a quarter onto the
 * framebuffer's hidden page: stock's swap (lcd_linux_fb_create's copy and pan, @0x6398d8) passes
 * each of the frame's dirty rects to image_rotate, which for BGRA8888 is rotate_bgra8888_image.
 * Stock's turn calls memcpy for every pixel, 4 bytes at a time, and was about 30% of demo's busy
 * time on the Q2 while a list scrolled. This is the same turn, pixel for pixel and in the same
 * order, with each pixel a word load and store: the same bytes land in the same places. Any case
 * stock's turn is not plainly a quarter or half turn of 4-byte pixels (another angle, a negative
 * size, a bitmap of another depth) goes to stock's own, so what it does there is unchanged. */
#include "offsets.inc"
#include "peq.h"
#include "stock.h"

int stock_rotate_bgra8888_trampoline(void *dst, void *src, const int *r, int o);

typedef struct {
    unsigned v;
} __attribute__((packed, may_alias)) px_t; /* one pixel at any address */

#define RET_BAD_PARAMS 0x10

/* rotate_bgra8888_image's hook, (dst, src, rect, orientation): stock's arguments and result. For
 * a turn of o degrees anticlockwise, source pixel (x, y) of src (w x h) lands at
 * 90:  (y, w - 1 - x)
 * 180: (w - 1 - x, h - 1 - y)
 * 270: (h - 1 - y, x)
 * in dst, each row of the rect copied in stock's order (its static helper @0x6e0f30). */
int fast_rotate_bgra8888(void *dst, void *src, const int *r, int o) {
    int x = r[0], y = r[1], rw = r[2], rh = r[3];
    if ((o != 90 && o != 180 && o != 270) || rw < 0 || rh < 0 || bitmap_get_bpp(dst) != 4 ||
        bitmap_get_bpp(src) != 4)
        return stock_rotate_bgra8888_trampoline(dst, src, r, o);
    unsigned w = bitmap_get_physical_width(src), h = bitmap_get_physical_height(src);
    unsigned dx, dy; /* where the rect's first source row starts in dst, as stock reckons it */
    if (o == 90)
        dx = (unsigned)y, dy = w - (unsigned)(x + rw);
    else if (o == 180)
        dx = w - (unsigned)x - 1, dy = h - (unsigned)y - 1;
    else
        dx = h - (unsigned)y - 1, dy = (unsigned)x;
    unsigned dll = bitmap_get_physical_line_length(dst), sll = bitmap_get_physical_line_length(src);
    unsigned char *d = bitmap_lock_buffer_for_write(dst);
    const unsigned char *s = bitmap_lock_buffer_for_read(src);
    if (!s || !d) return RET_BAD_PARAMS; /* stock's, which unlocks neither */
    unsigned char *dp = d + (dx * 4 + dy * dll);
    const unsigned char *sp = s + ((unsigned)x * 4 + (unsigned)y * sll);
    int aligned = !(((unsigned)dp | (unsigned)sp | dll | sll) & 3);
    /* Each source row is read along (src_step) and laid down by step: at 90 right to left and
     * down a column of dst, at 180 left along a row of dst, at 270 down a column. across moves
     * dst's start from one source row to the next. */
    if (o == 90) sp += rw * 4 - 4;
    int src_step = o == 90 ? -4 : 4;
    int step = o == 180 ? -4 : (int)dll;
    int across = o == 90 ? 4 : o == 180 ? -(int)dll : -4;
    for (int row = 0; row < rh; ++row, dp += across, sp += sll) {
        unsigned char *q = dp;
        const unsigned char *p = sp;
        if (aligned)
            for (int i = 0; i < rw; ++i, q += step, p += src_step)
                *(unsigned *)q = *(const unsigned *)p;
        else
            for (int i = 0; i < rw; ++i, q += step, p += src_step)
                ((px_t *)q)->v = ((const px_t *)p)->v;
    }
    bitmap_unlock_buffer(dst);
    bitmap_unlock_buffer(src);
    return 0; /* RET_OK */
}
