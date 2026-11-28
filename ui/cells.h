/* Blood cells under the selected row's sheen: the particle system and its ink, in whole numbers.
 *
 * Two halves, both free of floats, division-light and allocation-free:
 *
 *   cells_step()       rasterises every cell at a time into an A8 mask the size of the pill's box
 *                      (`keep`: 255 where the platter shows whole, less under a cell), and marks
 * which 16 px blocks of each row hold a cell at all (`dirty`). cells_shade_row()  turns one row of
 * the pill into pixels: platter, darkened toward the venous colour by the mask, the sheen's white
 * over that, dithered once to the panel's levels. cells_px() is the same for one pixel, for a
 * caller with its own loop (q2ui's fill_round has one: ui/draw.c).
 *
 * The field is a function of the time alone and repeats every CELLS_LOOP_US (6 s, 7 heartbeats),
 * so nothing is stepped and a frame may be skipped or drawn twice. Pixels are q2ui's: 0xffRRGGBB.
 *
 * Fitting q2ui (ui/list.c settle, ui/draw.c fill_round), a frame of the selected row:
 *   1. cells_step(&cells, ui_time()) for the platter's box, before the platter is filled.
 *   2. In fill_round's middle run (whole coverage), where it has `white` and `noise[x & 63]` for a
 *      pixel: row[x] = cells_px(&ink, keep[x - box.x], white, noise[x & 63]), keep being the mask's
 *      row for this scanline; or collect the run's `white` and call cells_shade_row once. At the
 *      rounded ends, px_mix the same pixel in by the coverage, as the platter's colour is today.
 *   3. The row's text over it, unchanged (row_paint): a cell only ever darkens the platter, so the
 *      text's contrast never falls.
 *   4. ui_mark the platter's box and ui_wake_ms(16) while the list is lit, at rest and not under
 *      Reduce Motion (then: one cells_step at a fixed time, or none).
 * At rest, cells_hold_row once and cells_draw_row a frame do step 2 for a fifth of the
 * instructions. cells_ink_init wants venous no lighter than the platter in any channel.
 */
#ifndef VAMP_CELLS_H
#define VAMP_CELLS_H

#include <stdint.h>

#define CELLS_MAX_W 384 /* the widest pill: a row across the 375 px screen */
#define CELLS_MAX_H 64
#define CELLS_BLOCK 4 /* a dirty block is 1 << CELLS_BLOCK px wide */

typedef struct {
    int w, h, stride;
    uint32_t dirty[CELLS_MAX_H];             /* row y, bit b: block b holds a cell */
    uint8_t keep[CELLS_MAX_H * CELLS_MAX_W]; /* row y at keep + y * stride */
} cells_t;

/* A pill's box of w x h px (to CELLS_MAX_W x CELLS_MAX_H). */
void cells_init(cells_t *c, int w, int h);
/* The mask at t_us (any clock in microseconds: only its place in the 6 s loop counts). */
void cells_step(cells_t *c, uint64_t t_us);
/* The same from nine samples a pixel, as the reference renderer takes them: what cells_step is
 * checked against, four times its cost. */
void cells_step_ss9(cells_t *c, uint64_t t_us);

typedef struct {
    uint32_t ramp[256][4]; /* by keep: red, green, blue as 8.16 where no sheen lies, and a spare */
    uint32_t flat;         /* the platter itself, 0xffRRGGBB */
    int32_t p[3], dp[3]; /* the platter's channels, and how far each is from the venous colour's */
    uint32_t gain;       /* strength / 255, Q28 */
    uint32_t gain_catch; /* gain times the sheen's catch, Q25: a cell under white w is 1 + catch * w
                            stronger */
    uint32_t white; /* the sheen's white, 8.16 (q2ui mixes toward 255; the reference renderer toward
                       252) */
    unsigned bits_rb, bits_g;
} cells_ink_t;

/* platter and venous are 0xRRGGBB; strength_q16 is how far a near cell's rim goes toward venous
 * (0.20: 13107); catch_q16 the sheen's catch (2.0: 131072); bits the panel's for red and blue, and
 * for green (q2ui's px_bits_rb, px_bits_g). */
void cells_ink_init(cells_ink_t *k, uint32_t platter, uint32_t venous, unsigned strength_q16,
                    unsigned catch_q16, unsigned bits_rb, unsigned bits_g);

/* A channel's value v (8.16) to the panel's level with noise (0-255) of one level's size under it:
 * q2ui's px_level (ui/px.h). */
static inline unsigned cells_level(unsigned v, unsigned noise, unsigned bits) {
    unsigned top = (0xffu << (8 - bits)) & 0xff,
             out = (v + (noise << (16 - bits))) >> (24 - bits) << (8 - bits);
    return out > top ? top : out;
}

/* A pixel's three channels as 8.16, before the dither: the platter pulled toward venous by the mask
 * (keep, 255: no cell), more under the sheen, with the sheen's white (0 to 65535 of 65536) over it.
 * Every product fits 32 bits, or is one multiply's high word. */
static inline void cells_sums(const cells_ink_t *k, unsigned keep, unsigned white, uint32_t v[3]) {
    uint32_t a = ((255 - keep) * (k->gain + ((k->gain_catch * white) >> 13))) >>
                 8; /* how far toward venous, Q20 */
    for (int i = 0; i < 3; ++i) {
        uint32_t acc = (uint32_t)((k->p[i] << 16) - ((k->dp[i] * (int32_t)a) >> 4));
        uint32_t up = k->white > acc ? k->white - acc : 0;
        v[i] = acc + (uint32_t)(((uint64_t)(up << 8) * (white << 8)) >> 32);
    }
}

/* One pixel: keep from the mask, white the sheen's share there (q2ui's gleam, 0: none), noise the
 * dither table's value for the pixel. Where no sheen lies the sums are read from the ramp. */
static inline uint32_t cells_px(const cells_ink_t *k, unsigned keep, unsigned white,
                                unsigned noise) {
    uint32_t sums[3];
    const uint32_t *v = k->ramp[keep];
    if (white) cells_sums(k, keep, white, sums), v = sums;
    return 0xff000000 | cells_level(v[0], noise, k->bits_rb) << 16 |
           cells_level(v[1], noise, k->bits_g) << 8 | cells_level(v[2], noise, k->bits_rb);
}

/* n pixels of a row of the pill into dst. keep is the mask's row and dirty its row's bits (pixel i
 * of the run is the mask's column i); white the sheen's share for each pixel, or 0 for none, with
 * [g0, g1) the columns where it is not zero; noise the dither table's row (64 values) and sx the
 * screen's x of dst[0]. Blocks with no cell and no sheen are filled flat. */
void cells_shade_row(const cells_ink_t *k, uint32_t *dst, const uint8_t *keep, uint32_t dirty,
                     const uint16_t *white, int g0, int g1, const uint8_t *noise, int sx, int n);
/* The same with every pixel taken the long way (no blocks, no ramp): what the blocks and the ramp
 * are measured against. */
void cells_shade_row_plain(const cells_ink_t *k, uint32_t *dst, const uint8_t *keep,
                           const uint16_t *white, const uint8_t *noise, int sx, int n);

/* ---- the pill held between frames ----
 * While the platter and its sheen stand still (the list at rest), everything about a pixel but the
 * cells is the same every frame. cells_hold_row works that out once for a row: the pixel with no
 * cell on it (clean, 4 bytes), and what a cell there needs (8 bytes). cells_draw_row then inks a
 * frame from them: a copy where no cell lies, one multiply a channel where one does. Made for a
 * 6-bit panel; a held value is 8.8 with its noise in it, so a pixel under a cell can land a level
 * off cells_shade_row's about once in 2,000. Hold again whenever the platter's colour, its box or
 * the sheen changes (a lean, a press); while they move, cells_shade_row draws the frame. */
typedef struct {
    uint16_t b[3]; /* platter and sheen, 8.8, the pixel's dither noise added */
    uint16_t pull; /* how far a whole cell pulls it toward venous, Q25 */
} cells_held_t;
/* Fills row and clean for n pixels (white, noise, sx as cells_shade_row). Returns the blocks the
 * sheen lies in. */
uint32_t cells_hold_row(const cells_ink_t *k, cells_held_t *row, uint32_t *clean,
                        const uint16_t *white, const uint8_t *noise, int sx, int n);
void cells_draw_row(const cells_ink_t *k, uint32_t *restrict dst, const cells_held_t *restrict row,
                    const uint32_t *restrict clean, const uint8_t *restrict keep, uint32_t dirty,
                    int n);

#endif
