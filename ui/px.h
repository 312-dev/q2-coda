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
/* The same with a in 1/65536 and noise (0-255) added under the last bit kept, which is what
 * spreads a slow ramp's steps into grain too fine to see as bands. */
static inline uint32_t px_mix16(uint32_t dst, uint32_t rgb, unsigned a, unsigned noise) {
    unsigned k = 65536 - a, n = noise << 8;
    unsigned r = ((rgb >> 16 & 0xff) * a + (dst >> 16 & 0xff) * k + n) >> 16;
    unsigned g = ((rgb >> 8 & 0xff) * a + (dst >> 8 & 0xff) * k + n) >> 16;
    unsigned b = ((rgb & 0xff) * a + (dst & 0xff) * k + n) >> 16;
    return 0xff000000 | r << 16 | g << 8 | b;
}

/* How far (px) a point |u|, |v| from a box's centre is outside the box of half-size hw, hh with
 * corners of radius r: negative inside. */
static inline float px_box_dist(float au, float av, float hw, float hh, float r) {
    float qx = au - (hw - r), qy = av - (hh - r);
    if (qx > 0 && qy > 0) return __builtin_sqrtf(qx * qx + qy * qy) - r;
    return (qx > qy ? qx : qy) - r;
}

/* The eye's distance from the screen, px: what a lean's perspective is drawn for. */
#define PX_EYE 600.0f

#endif
