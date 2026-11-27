/* The panel's colour depth, by eye (docs/ui-rewrite.md#gradients): the framebuffer is 8 bits a
 * channel, but the link to the glass may carry 6 or 5, and nothing the kernel reports says which.
 * Five ramps from black to white and five to the accent, each drawn as a link of that depth would
 * show it: 8 bits; 6 bits cut off; 6 bits dithered; 5 bits cut off; 5 bits dithered. On the panel,
 * the first cut-off ramp that looks no worse than the 8-bit one above it is the link's depth, and
 * --panel-bits is then set to it so every frame is dithered down to what the glass can show.
 * While the card is open nothing is dithered on the way to the panel, whatever --panel-bits says,
 * or the card would be the thing it measures. Return leaves. */
#include <stdio.h>

#include "px.h"
#include "q2ui.h"

#define RAMP_X 62
#define RAMP_W 250
#define RAMP_H 22

static const struct {
    int bits, dither;
    const char *name;
} ramps[] = { { 8, 0, "8" }, { 6, 0, "6" }, { 6, 1, "6 d" }, { 5, 0, "5" }, { 5, 1, "5 d" } };

static void ramp(int y, uint32_t to, int bits, int dither) {
    rect_t c, r = { RAMP_X, y, RAMP_W, RAMP_H };
    if (!rect_meet(r, clip_get(), &c)) return;
    unsigned drop = (unsigned)(8 - bits), keep = (0xffu << drop) & 0xff;
    for (int j = c.y; j < c.y + c.h; ++j)
        for (int i = c.x; i < c.x + c.w; ++i) {
            /* the ramp at 16 bits, brought to 8 with noise as every ramp here is, then to the
             * link's bits with or without it */
            unsigned noise = dither64[(j & 63) * 64 + (i & 63)];
            uint32_t p = px_mix16(0, to, (unsigned)((i - RAMP_X) * 65535 / (RAMP_W - 1)), bits == 8 ? noise : 128);
            if (bits < 8) {
                unsigned add = dither ? noise >> bits : 0, out = 0;
                for (unsigned shift = 0; shift < 24; shift += 8) {
                    unsigned v = (p >> shift & 0xff) + add;
                    out |= ((v > 255 ? 255 : v) & keep) << shift;
                }
                p = out;
            }
            canvas[j * SCREEN_W + i] = 0xff000000 | p;
        }
}

static void draw(void *st, rect_t r) {
    (void)st;
    fill(r, 0x000000);
    for (int k = 0; k < 10; ++k) {
        int y = 60 + k * (RAMP_H + 2) + (k >= 5 ? 6 : 0);
        run_t label;
        ramp(y, k < 5 ? 0xffffff : RGB_ACCENT, ramps[k % 5].bits, ramps[k % 5].dither);
        run_make(&label, FACE_REGULAR, TYPE(13), ramps[k % 5].name, -1);
        run_draw(&label, 30, (float)y + 3, tok(TOK_TERTIARY), 256);
    }
}
static void event(void *st, const input_t *e) {
    (void)st;
    if (e->type == IN_KEY_UP && e->value == KEY_UI_RETURN) ui_pop();
}
static int bits_before;
static void *open(void *arg) {
    (void)arg;
    bits_before = fb_bits;
    fb_depth(8);
    return 0;
}
static void close(void *st) {
    (void)st;
    fb_depth(bits_before);
    ui_mark_all();
}
const ui_page page_depth = { .name = "depth", .open = open, .event = event, .draw = draw, .close = close };
