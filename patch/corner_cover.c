/* Anti-aliased rounded corners (docs/internals.md#rounded-corners): one coverage table per radius,
 * worked out the first time a radius is drawn and kept, which every rounded thing the payload
 * paints itself reads: list rows and pills (nav/row_paint.c round_row, gradient), the corners
 * rounded into art over a known colour (corner_mask: Control Center, Up Next, artist pages, Now
 * Playing), a bitmap's own alpha (theme_titles_paint.c round_corners) and Cover Flow's covers
 * (coverflow.c column). tools/art.py corner_cover is the same table in Python, which the tests
 * hold this to. */
#include "peq.h"

/* The largest radius kept; a larger one is drawn at this. Cover Flow's 160-texel art at a
 * coverflow.shelf.radius of 166 would need 160. */
#define CORNER_MAX 160

/* floor(sqrt(v)), v >= 0: bit by bit, as the payload links no libm. */
static unsigned isqrt(unsigned v) {
    unsigned r = 0, bit = 1u << 30;
    while (bit > v) bit >>= 2;
    for (; bit; bit >>= 2)
        if (v >= r + bit)
            v -= r + bit, r = (r >> 1) + bit;
        else
            r >>= 1;
    return r;
}

/* Table t (r rows of r, the outermost row and column first) of a radius r corner: each pixel's
 * share of 16 x 16 sample points inside the circle, out of 255. A sample (i, j) of 16r across the
 * corner is inside when (32r - 2i - 1)^2 + (32r - 2j - 1)^2 <= (32r)^2 (tools/art.py corner_cover
 * samples the same points), so a row of samples has its first inside one where the integer root
 * of (32r)^2 - dy^2 says, one root a row of samples. */
static void corner_fill(unsigned char *t, int r) {
    int in[16]; /* each sample row's samples outside the circle, from the outer edge */
    for (int y = 0; y < r; ++y) {
        for (int j = 0; j < 16; ++j) {
            int dy = 32 * r - 2 * (16 * y + j) - 1;
            int s = (int)isqrt((unsigned)(1024 * r * r - dy * dy));
            in[j] = s >= 32 * r - 1 ? 0 : (32 * r - 2 - s) / 2 + 1;
        }
        for (int x = 0; x < r; ++x) {
            int n = 0;
            for (int j = 0; j < 16; ++j) {
                int o = in[j] - 16 * x; /* this pixel's samples outside, 0 to 16 */
                n += 16 - (o < 0 ? 0 : o > 16 ? 16 : o);
            }
            t[y * r + x] = (unsigned char)((n * 255 + 128) / 256);
        }
    }
}

/* The coverage table of a radius r corner (see corner_fill), or 0 for r < 1; past CORNER_MAX,
 * CORNER_MAX's. Each radius is filled once, on the heap, and kept: a screen uses a handful. The
 * pointer is stored only after its table is whole, so a reader on another thread (Cover Flow's
 * renderer) sees a whole table or none and fills its own, at worst leaking that one.
 * ponytail: a heap failure gives 0, which every caller takes as a square corner. */
const unsigned char *corner_cover(int r) {
    static const unsigned char *made[CORNER_MAX + 1] __attribute__((section(".scratch")));
    if (r < 1) return (const unsigned char *)0;
    if (r > CORNER_MAX) r = CORNER_MAX;
    const unsigned char *t = made[r];
    if (t) return t;
    unsigned char *fresh = calloc((unsigned)(r * r), 1);
    if (!fresh) return (const unsigned char *)0;
    corner_fill(fresh, r);
    __atomic_store_n(&made[r], fresh, __ATOMIC_RELEASE);
    return fresh;
}
