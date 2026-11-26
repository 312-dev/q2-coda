/* A card of layers (docs/ui-rewrite.md#cards): the depth a tvOS poster has. The card leans about
 * its horizontal middle as it is pushed, and each layer is drawn moved by its own depth times
 * the push, so a layer behind the face lags the card and one in front leads it; the shadow under
 * it goes the other way and a glint crosses the face. The wheel has one axis, so a push is up or
 * down. A picture layer is shown a little larger than its frame (zoom) and moved inside it, so
 * no edge of it ever shows. */
#include <math.h>

#include "q2ui.h"

#define CARD_TILT 0.16f /* radians at a full push */
#define CARD_GROW 0.06f /* how much larger a lifted card is */
#define CARD_SHADOW 14

rect_t card_bounds(rectf_t r) {
    float grow = r.w * CARD_GROW;
    return rect_of((rectf_t){ r.x - grow, r.y - grow, r.w + 2 * grow, r.h + 2 * grow }, CARD_SHADOW + 6);
}

void card_draw(rectf_t r, float radius, const ui_layer *layers, int n, float push, float lift) {
    push = clampf(push, -1, 1);
    float scale = 1 + CARD_GROW * clampf(lift, 0, 1), w = r.w * scale, h = r.h * scale;
    float cx = r.x + r.w / 2, cy = r.y + r.h / 2 + push * 2, tilt = -push * CARD_TILT;
    rectf_t face = { cx - w / 2, cy - h / 2, w, h };
    shadow_round((rectf_t){ face.x + 4, face.y + 6 - push * 4, face.w - 8, face.h - 4 }, radius, CARD_SHADOW, 0x000000,
                 0.35f + 0.25f * lift);
    for (int i = 0; i < n; ++i) {
        const ui_layer *l = &layers[i];
        float in = l->inset * scale, dy = l->depth * push;
        rectf_t box = { face.x + in, face.y + in, face.w - 2 * in, face.h - 2 * in };
        if (!l->im) {
            box.y += dy;
            fill_round(box, radius > in ? radius - in : 0, tilt, l->rgb, 1, 0);
            continue;
        }
        if (l->inset > 0) { /* a layer smaller than the card moves whole */
            box.y += dy;
            image_draw(l->im, box, 0, tilt, 256, -1);
            continue;
        }
        /* the card's own picture fills the face and moves inside it */
        float zoom = l->zoom > 1 ? l->zoom : 1, pw = (float)l->im->w / zoom, ph = (float)l->im->h / zoom;
        float room = ((float)l->im->h - ph) / 2, shift = clampf(-dy * (float)l->im->h / h, -room, room);
        image_draw_part(l->im, (rectf_t){ ((float)l->im->w - pw) / 2, room + shift, pw, ph }, box, radius, tilt, 256, -1);
    }
    gleam_t glint = { 0, -push * h * 0.55f, w * 0.7f, h * 0.6f, 0.22f * fabsf(push) };
    if (glint.strength > 0) fill_round(face, radius, tilt, RGB_GLEAM_ONLY, 1, &glint);
}
