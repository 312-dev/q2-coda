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

/* Table t (r rows of r, the outermost row and column first) of a radius r corner: each pixel's
 * share of 16 x 16 sample points inside the circle, out of 255. A sample (i, j) of 16r across the
 * corner is inside when (32r - 2i - 1)^2 + (32r - 2j - 1)^2 <= (32r)^2 (tools/art.py corner_cover
 * samples the same points), so a row of samples has its first inside one where the integer root
 * of (32r)^2 - dy^2 says. That root only grows from one row of samples to the next, so it is
 * stepped up from the last, 32r steps in all for the corner (the payload links no libm, and a
 * first paint of rows with two radii keeps to its budget). The samples' first inside ones fall
 * further in down a row of pixels, so only the pixels between the first and last row's are counted,
 * from running sums; those before are clear and those after whole. */
static void corner_fill(unsigned char *t, int r) {
    /* in[j]: sample row j's samples outside the circle, from the outer edge, falling as j grows;
     * sum[k]: in[0] to in[k - 1] added up */
    int in[16], sum[17], s = 0, next = 1; /* next: (s + 1)^2 */
    for (int y = 0; y < r; ++y) {
        sum[0] = 0;
        for (int j = 0; j < 16; ++j) {
            int dy = 32 * r - 2 * (16 * y + j) - 1, v = 1024 * r * r - dy * dy;
            while (next <= v) next += 2 * s + 3, ++s; /* s = floor(sqrt(v)) */
            in[j] = s >= 32 * r - 1 ? 0 : (32 * r - 2 - s) / 2 + 1;
            sum[j + 1] = sum[j] + in[j];
        }
        unsigned char *row = t + y * r;
        int x = 0, clear = in[15] / 16, whole = (in[0] + 15) / 16;
        for (; x < clear && x < r; ++x) row[x] = 0;
        /* pixel x: rows j < lo have all 16 of its samples outside, rows from hi none, and the
         * rows between 16x + 16 - in[j] inside; both bounds only fall as x grows */
        for (int lo = 16, hi = 16; x < whole && x < r; ++x) {
            while (lo && in[lo - 1] < 16 * x + 16) --lo;
            while (hi && in[hi - 1] <= 16 * x) --hi;
            int n = 16 * (16 - hi) + (hi - lo) * (16 * x + 16) - (sum[hi] - sum[lo]);
            row[x] = (unsigned char)((n * 255 + 128) / 256);
        }
        for (; x < r; ++x) row[x] = 255;
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
