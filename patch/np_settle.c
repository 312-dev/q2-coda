/* Now Playing's art settles on pause (docs/oled-design.md#motion), as Apple Music's does: paused,
 * the art shrinks about its center to NS_PAUSED thousandths of its size over NS_PAUSE_MS; played
 * again, it grows back over NS_PLAY_MS; both cubic_out, no overshoot. The play/pause glyph stays
 * the cue, so under Reduce Motion the art keeps its size (ovm_scale). A turn mid-run starts from
 * where the art is (ovm_t), so Play, Pause, Play in quick succession never jumps.
 *
 * np_settle_poll, each UI loop pass, follows the player's state while a Now Playing page is up; a
 * page that opens on a paused song shows the art small at once. Stock's image (img_cover, draw
 * type scale_auto, its own black box behind it) paints whole at its box, so while the art is
 * smaller ringnav_paint_bg empties the canvas's clip before stock paints it (np_settle_begin), and
 * paint_cover puts the clip back and draws the cover itself into the smaller box
 * (np_settle_paint), where the dim of a held art and the rounded corners follow it (np_settle_box).
 * The ring it leaves is the page behind.
 * ponytail: every frame of a paused page scales the cover again (about 1 ms at 132 px); a bitmap
 * kept at the paused size would make a paused repaint a copy.
 */
#include "navigation.h"
#include "ui_kit.h"

#define NS_PAUSED 880   /* the paused art's size, thousandths */
#define NS_PAUSE_MS 280 /* shrinking */
#define NS_PLAY_MS 320  /* growing back */
#define NS_PAUSE 3      /* mclGetPlayStatus: 1 stopped, 2 playing, 3 paused */

static struct {
    ovm_t o;     /* in: playing, the art whole; out: paused */
    void *cover; /* the img_cover followed; another starts at rest */
    int paused;  /* as last seen */
    int clipped; /* np_settle_begin emptied the clip; clip is the one it found */
    int clip[4];
} ns __attribute__((section(".scratch")));

/* Each UI loop pass: a pause or a play while Now Playing is up starts the art on its way. */
void np_settle_poll(void) {
    void *w = st.np_cover;
    if (!w) {
        ns.cover = (void *)0;
        return;
    }
    int paused = mclGetPlayStatus() == NS_PAUSE;
    if (w == ns.cover && paused == ns.paused) return;
    int ms = w == ns.cover ? paused ? NS_PAUSE_MS : NS_PLAY_MS : 0; /* a new page: at rest */
    ns.cover = w;
    ns.paused = paused;
    if (paused)
        ovm_exit(&ns.o, ms, EASE_CUBIC_OUT);
    else
        ovm_enter(&ns.o, ms, EASE_CUBIC_OUT);
}

/* The art's size now, thousandths: 1000 whole */
int np_settle_scale(void) {
    return ns.cover && ns.cover == st.np_cover ? ovm_scale(&ns.o, NS_PAUSED) : 1000;
}

/* 1 with box (x y w h in w's coordinates) the art's place when w is Now Playing's art and smaller
 * than its box, about the box's center; 0 with box w's own box. */
int np_settle_box(void *w, int *box) {
    int s = w && w == st.np_cover ? np_settle_scale() : 1000, bw = I(w, W_W), bh = I(w, W_H);
    box[2] = bw * s / 1000, box[3] = bh * s / 1000;
    box[0] = (bw - box[2]) / 2, box[1] = (bh - box[3]) / 2;
    return s < 1000;
}

/* ringnav_paint_bg, before stock paints w: Now Playing's art smaller than its box paints nothing
 * of stock's (its black box, its image). */
void np_settle_begin(void *w, void *canvas) {
    int box[4];
    if (!w || w != st.np_cover || ns.clipped || !np_settle_box(w, box)) return;
    canvas_get_clip_rect(canvas, ns.clip);
    int none[4] = { ns.clip[0], ns.clip[1], 0, 0 };
    canvas_set_clip_rect(canvas, none);
    ns.clipped = 1;
}

/* paint_cover, after stock painted w: the clip back, and the cover drawn into the art's smaller
 * box as stock's scale_auto draws it into the whole one (the whole image, its shape kept, centered).
 */
void np_settle_paint(void *w, void *canvas) {
    if (!ns.clipped || w != st.np_cover) return;
    canvas_set_clip_rect(canvas, ns.clip);
    ns.clipped = 0;
    int box[4];
    unsigned bitmap[64]; /* bitmap_t: width @0, height @4 */
    np_settle_box(w, box);
    const char *image = widget_get_prop_str(w, "image", "");
    if (!*image || widget_load_image(w, image, bitmap) || !bitmap[0] || !bitmap[1]) return;
    int bw = (int)bitmap[0], bh = (int)bitmap[1];
    rect_t src = { 0, 0, bw, bh }, dst = { box[0], box[1], box[2], box[3] };
    if (bw * box[3] > bh * box[2]) /* wider than the box: as wide, centered down it */
        dst.h = bh * box[2] / bw, dst.y += (box[3] - dst.h) / 2;
    else
        dst.w = bw * box[3] / bh, dst.x += (box[2] - dst.w) / 2;
    canvas_draw_image(canvas, bitmap, &src, &dst);
}
