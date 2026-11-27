/* Pixel arithmetic shared by draw.c and image.c. Colours are 0xRRGGBB; the canvas keeps 0xff above
 * them. Blends take the destination as it is, so an edge smoothed onto any background takes that
 * background's colour and no other (no assumed black, no fringe). */
#ifndef Q2UI_PX_H
#define Q2UI_PX_H

#include <stdint.h>

/* dst with a/256 of rgb over it */
static inline uint32_t px_mix(uint32_t dst, uint32_t rgb, unsigned a) {
    uint32_t rb = ((rgb & 0xff00ff) * a + (dst & 0xff00ff) * (256 - a)) >> 8 & 0xff00ff;
    uint32_t g = ((rgb & 0xff00) * a + (dst & 0xff00) * (256 - a)) >> 8 & 0xff00;
    return 0xff000000 | rb | g;
}
/* The panel's bits for red and blue, and for green (fb.c fb_depth): 8, or fewer on a narrower
 * panel, whose levels are then every 4th or 8th of the canvas's. */
extern unsigned px_bits_rb, px_bits_g;

/* A channel's value v, in 1/65536 of a level, brought to the nearest level below it that a panel
 * of `bits` has, with noise (0-255) of one such level's size added first. */
static inline unsigned px_level(unsigned v, unsigned noise, unsigned bits) {
    unsigned top = (0xffu << (8 - bits)) & 0xff, out = (v + (noise << (16 - bits))) >> (24 - bits) << (8 - bits);
    return out > top ? top : out;
}
/* The same blend with a in 1/65536 and noise (0-255) added under the last level kept, which is
 * what spreads a slow ramp's steps into grain too fine to see as bands. The last level kept is
 * the panel's, not the canvas's: on a 6-bit panel a ramp is dithered once, from these 16 bits
 * straight to the panel's 6, and leaves here already on the panel's levels, so nothing dithers it
 * again on its way out (docs/ui-rewrite.md#gradients). On an 8-bit panel it is the canvas's. */
static inline uint32_t px_mix16(uint32_t dst, uint32_t rgb, unsigned a, unsigned noise) {
    unsigned k = 65536 - a;
    unsigned r = px_level((rgb >> 16 & 0xff) * a + (dst >> 16 & 0xff) * k, noise, px_bits_rb);
    unsigned g = px_level((rgb >> 8 & 0xff) * a + (dst >> 8 & 0xff) * k, noise, px_bits_g);
    unsigned b = px_level((rgb & 0xff) * a + (dst & 0xff) * k, noise, px_bits_rb);
    return 0xff000000 | r << 16 | g << 8 | b;
}
/* A picture's pixel brought to the panel's levels with noise: a cover's own gradients are ramps
 * too. On an 8-bit panel it is the pixel. */
static inline uint32_t px_picture(uint32_t rgb, unsigned noise) {
    if (px_bits_rb == 8) return rgb & 0xffffff;
    return px_level((rgb >> 16 & 0xff) << 16, noise, px_bits_rb) << 16 | px_level((rgb >> 8 & 0xff) << 16, noise, px_bits_g) << 8 |
           px_level((rgb & 0xff) << 16, noise, px_bits_rb);
}

/* Whole-number square root, rounded down. */
static inline unsigned px_isqrt(unsigned n) {
    unsigned r = 0, bit = 1u << 30;
    while (bit > n) bit >>= 2;
    for (; bit; bit >>= 2)
        if (n >= r + bit)
            n -= r + bit, r = (r >> 1) + bit;
        else
            r >>= 1;
    return r;
}
/* How far a point |u|, |v| from a box's centre is outside the box of half-size hw, hh with corners
 * of radius r, all in 1/64 px: negative inside. Whole numbers, so an edge costs a frame the same
 * on a Q2 as it is measured to under an emulator that counts every float. */
#define PX_ONE 64
static inline int px_box_dist(int au, int av, int hw, int hh, int r) {
    int qx = au - (hw - r), qy = av - (hh - r);
    if (qx > 0 && qy > 0) return (int)px_isqrt((unsigned)(qx * qx + qy * qy)) - r;
    return (qx > qy ? qx : qy) - r;
}

/* The eye's distance from the screen, px: what a lean's perspective is drawn for. */
#define PX_EYE 600.0f

#endif
