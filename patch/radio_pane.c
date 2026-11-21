/* Home's art pane while Radio is Now Playing (coverflow.c SHOW_RADIO;
 * docs/oled-design.md#homes-art-pane-while-radio-plays):
 * K1, approved 2026-11-20 (~/q2-emu/design/radio-tuner/radio-now-playing/pane-v6: mock.py has every
 * number, sheet.png the still, K1-rings.mp4 the motion). A dark red ground deepening to the bottom,
 * a glow behind a glossy red badge holding the broadcast tower, broadcast rings leaving the badge
 * one every RING_EVERY_MS while Radio plays (still while it is paused, or under Reduce Motion), and
 * the frequency alone under it over a soft dark vignette. The pane comes in as approved with it
 * ("Apple-like slide", pane-v3/anim.py): the cover recedes while this slides in from the right on a
 * damped spring, the glow blooms after it, and the badge then the frequency rise and fade in.
 *
 * Everything but the tower and the words is drawn here, in software, into coverflow's frame (an
 * RGBA8888 bitmap the pane's size): the ground, the glow, the badge and the vignette are worked out
 * once when the pane is first shown and kept until it goes (rp_free); a frame is then a pass over
 * the pane's pixels, the rings a table of alpha by distance from the badge's centre made each
 * frame (ring_table), so the motion costs a lookup a pixel. */
#include "navigation.h"

#define CX_OFF 0      /* the badge's centre: the pane's middle across ... */
#define CY_SCREEN 138 /* ... and screen row 138, so the badge and the frequency are centred */
#define BADGE 64      /* the badge's diameter */
#define FREQ_DY 70    /* the frequency's baseline below the badge's centre: 32 + 16 + its 22 cap */
#define GLOW_R                                                                                     \
    150 /* the glow: t = clamp(1 - r / GLOW_R)^2.2 * 0.45 of GLOW_RGB over the ground              \
         */
#define RING_EVERY_MS 2400 /* a ring leaves the badge this often; RINGS travel at once */
#define RINGS 4
#define RING_R0 40  /* a ring's radius from its start ... */
#define RING_R1 170 /* ... to its end */
#define BLOOM 5     /* the rings' bloom, a Gaussian this many px (mock.py's GaussianBlur(5)) */
#define VIG_W 62    /* the vignette: an ellipse VIG_W either side of the centre, ... */
#define VIG_UP 34   /* ... from VIG_UP above the baseline ... */
#define VIG_DOWN 12 /* ... to VIG_DOWN below it, black at VIG_A, blurred VIG_BLUR px */
#define VIG_A 210
#define VIG_BLUR 12
#define STILL_PHASE 0.35f /* the rings where they stand still under Reduce Motion (the sheet's) */
/* The way in (pane-v3/anim.py): the cover's recede over IN_COVER_MS, the spring, the glow from
 * IN_GLOW_AT over IN_GLOW_MS, the badge from IN_BADGE_AT and the frequency from IN_FREQ_AT, each
 * over IN_RISE_MS, rising IN_RISE px; all done by IN_MS. */
#define IN_COVER_MS 350
#define IN_GLOW_AT 180
#define IN_GLOW_MS 500
#define IN_BADGE_AT 300
#define IN_FREQ_AT 380
#define IN_RISE_MS 300
#define IN_RISE 8
#define IN_MS 1200
#define IN_SHIFT 18 /* the cover drifts left this far as it recedes to 0.92 and dims to 180/255 */

static const unsigned char GROUND[3] = { 20, 3, 7 }, GLOW_RGB[3] = { 88, 7, 22 },
                           EDGE[3] = { 30, 7, 11 }, RING[3] = { 205, 38, 66 },
                           BADGE_TOP[3] = { 214, 46, 76 }, BADGE_BOT[3] = { 122, 10, 32 };

static struct {
    int w, h, top, cx, cy;    /* the pane, its top on the screen, the badge's centre in it */
    int vx0, vy0, vx1, vy1;   /* the box the vignette reaches */
    unsigned *ground;         /* the ground without the glow, 0x00BBGGRR */
    unsigned *lit;            /* ... and with it, as a frame draws it once the way in is over */
    unsigned char *glow;      /* the glow's t, 255 at 0.45 */
    unsigned short *d4;       /* each pixel's distance from the badge's centre, quarter px */
    unsigned char *vig;       /* the vignette's alpha */
    unsigned *badge;          /* BADGE x BADGE, alpha in the top byte */
    unsigned *cover;          /* the cover the pane showed, while it recedes */
    unsigned in_at;           /* the way in's start (0: none) */
    unsigned phase_at;        /* time_now_ms at ring phase 0, while playing */
    float phase;              /* the rings' phase where they stand (paused, still) */
    int playing;              /* the last frame's: playing rings move */
    unsigned char ring[1600]; /* this frame's ring alpha by d4 */
} rp __attribute__((section(".scratch")));

static unsigned isqrt(unsigned v) {
    unsigned r = 0, b = 1u << 30;
    while (b > v) b >>= 2;
    for (; b; b >>= 2)
        if (v >= r + b)
            v -= r + b, r = (r >> 1) + b;
        else
            r >>= 1;
    return r;
}
static float clampf(float v) { return v < 0 ? 0 : v > 1 ? 1 : v; }
static float ease(float t) { /* cubic out, as the mock's */
    t = 1 - clampf(t);
    return 1 - t * t * t;
}
static unsigned pack(float r, float g, float b) {
    int R = (int)(r + 0.5f), G = (int)(g + 0.5f), B = (int)(b + 0.5f);
    R = R < 0     ? 0
        : R > 255 ? 255
                  : R,
    G = G < 0     ? 0
        : G > 255 ? 255
                  : G,
    B = B < 0     ? 0
        : B > 255 ? 255
                  : B;
    return (unsigned)R | (unsigned)G << 8 | (unsigned)B << 16;
}
/* a over b by alpha a (0..256), both 0x00BBGGRR */
static unsigned rp_mix(unsigned b, unsigned c, unsigned a) {
    unsigned rb = ((b & 0xff00ff) * (256 - a) + (c & 0xff00ff) * a) >> 8 & 0xff00ff,
             g = ((b & 0xff00) * (256 - a) + (c & 0xff00) * a) >> 8 & 0xff00;
    return rb | g;
}

static void layers_free(void) {
    free(rp.ground), free(rp.lit), free(rp.glow), free(rp.d4), free(rp.vig), free(rp.badge);
    rp.ground = 0, rp.lit = 0, rp.glow = 0, rp.d4 = 0, rp.vig = 0, rp.badge = 0;
    rp.w = 0;
}
/* The pane gone (the Library's covers back): its layers and any way in freed. */
void rp_free(void) {
    layers_free();
    free(rp.cover), rp.cover = 0, rp.in_at = 0;
}

/* The still layers for a w x h pane with its top at screen row top: 0 when out of memory. */
static int layers(int w, int h, int top) {
    if (rp.ground && rp.w == w && rp.h == h && rp.top == top) return 1;
    layers_free();
    unsigned n = (unsigned)(w * h);
    rp.ground = calloc(n, 4), rp.lit = calloc(n, 4), rp.glow = calloc(n, 1), rp.d4 = calloc(n, 2),
    rp.vig = calloc(n, 1), rp.badge = calloc(BADGE * BADGE, 4);
    float *row = calloc((unsigned)(w > h ? w : h), 4);
    if (!rp.ground || !rp.lit || !rp.glow || !rp.d4 || !rp.vig || !rp.badge || !row) {
        free(row);
        layers_free();
        return 0;
    }
    rp.w = w, rp.h = h, rp.top = top, rp.cx = w / 2 + CX_OFF, rp.cy = CY_SCREEN - top;
    for (int y = 0; y < h; ++y) {
        float deep = 1 - 0.35f * (float)y / (float)h;
        for (int x = 0; x < w; ++x) {
            int i = y * w + x, dx = x - rp.cx, dy = y - rp.cy;
            unsigned d4 = isqrt((unsigned)(dx * dx + dy * dy) * 16);
            rp.d4[i] = (unsigned short)(d4 < sizeof rp.ring ? d4 : sizeof rp.ring - 1);
            float t = clampf(1 - (float)d4 / 4 / GLOW_R);
            rp.glow[i] = (unsigned char)(255 * pow(t, 2.2) + 0.5);
            float e = x ? 0 : 1; /* the faint lighter line down the pane's left edge */
            rp.ground[i] = pack(GROUND[0] * deep + EDGE[0] * e, GROUND[1] * deep + EDGE[1] * e,
                                GROUND[2] * deep + EDGE[2] * e);
            float gl = rp.glow[i] * 0.45f / 255;
            rp.lit[i] = pack(GROUND[0] * deep + EDGE[0] * e + GLOW_RGB[0] * gl,
                             GROUND[1] * deep + EDGE[1] * e + GLOW_RGB[1] * gl,
                             GROUND[2] * deep + EDGE[2] * e + GLOW_RGB[2] * gl);
        }
    }
    /* the badge: a vertical gradient, a soft gloss high on it, its edge a pixel's coverage */
    for (int y = 0; y < BADGE; ++y)
        for (int x = 0; x < BADGE; ++x) {
            float c = BADGE / 2.0f, px = x + 0.5f - c, py = y + 0.5f - c, g = (y + 0.5f) / BADGE;
            float r = (float)isqrt((unsigned)((px * px + py * py) * 256)) / 16;
            float gy = (y + 0.5f - c * 0.55f) * 1.6f;
            float gl =
                clampf(1 - (float)isqrt((unsigned)((px * px + gy * gy) * 256)) / 16 / (c * 0.9f));
            gl = gl * gl * 70;
            float a = clampf(c - r + 0.5f);
            unsigned rgb = pack(BADGE_TOP[0] * (1 - g) + BADGE_BOT[0] * g + gl,
                                BADGE_TOP[1] * (1 - g) + BADGE_BOT[1] * g + gl,
                                BADGE_TOP[2] * (1 - g) + BADGE_BOT[2] * g + gl);
            rp.badge[y * BADGE + x] = rgb | (unsigned)(a * 255 + 0.5f) << 24;
        }
    /* the vignette: the ellipse, then a separable Gaussian of VIG_BLUR px over the box it reaches
     */
    int base = rp.cy + FREQ_DY, x0 = rp.cx - VIG_W - 3 * VIG_BLUR,
        x1 = rp.cx + VIG_W + 3 * VIG_BLUR, y0 = base - VIG_UP - 3 * VIG_BLUR,
        y1 = base + VIG_DOWN + 3 * VIG_BLUR;
    x0 = x0 < 0 ? 0 : x0, y0 = y0 < 0 ? 0 : y0, x1 = x1 > w ? w : x1, y1 = y1 > h ? h : y1;
    int bw = x1 - x0, bh = y1 - y0;
    rp.vx0 = x0, rp.vy0 = y0, rp.vx1 = x1, rp.vy1 = y1;
    float *box = bw > 0 && bh > 0 ? calloc((unsigned)(bw * bh), 4) : 0, k[3 * VIG_BLUR + 1], ks = 0;
    if (box) {
        for (int j = 0; j <= 3 * VIG_BLUR; ++j)
            k[j] = (float)pow(2.718281828, -(double)(j * j) / (2.0 * VIG_BLUR * VIG_BLUR)),
            ks += j ? 2 * k[j] : k[j];
        float ex = VIG_W, ey = (VIG_UP + VIG_DOWN) / 2.0f, ecy = base - VIG_UP + ey;
        for (int y = 0; y < bh; ++y)
            for (int x = 0; x < bw; ++x) {
                float u = (x0 + x + 0.5f - rp.cx) / ex, v = (y0 + y + 0.5f - ecy) / ey;
                box[y * bw + x] = u * u + v * v <= 1 ? VIG_A : 0;
            }
        for (int pass = 0; pass < 2; ++pass) { /* across, then down */
            int len = pass ? bh : bw, lines = pass ? bw : bh;
            for (int l = 0; l < lines; ++l) {
                for (int i = 0; i < len; ++i) row[i] = box[pass ? i * bw + l : l * bw + i];
                for (int i = 0; i < len; ++i) {
                    float s = 0;
                    for (int j = -3 * VIG_BLUR; j <= 3 * VIG_BLUR; ++j) {
                        int at = i + j;
                        if (at >= 0 && at < len) s += row[at] * k[j < 0 ? -j : j];
                    }
                    box[pass ? i * bw + l : l * bw + i] = s / ks;
                }
            }
        }
        for (int y = 0; y < bh; ++y)
            for (int x = 0; x < bw; ++x)
                rp.vig[(y0 + y) * w + x0 + x] = (unsigned char)(box[y * bw + x] + 0.5f);
        free(box);
    }
    free(row);
    return 1;
}

/* The rings at phase (0..1: how far the first has gone of its quarter), each fading as it grows,
 * as alpha by quarter-px distance: a ring's own pixel coverage over its Gaussian bloom. */
static void ring_table(float phase, float bloom) {
    float r[RINGS], al[RINGS], wd[RINGS];
    for (int k = 0; k < RINGS; ++k) {
        float u = (k + phase) / RINGS;
        r[k] = RING_R0 + u * (RING_R1 - RING_R0);
        al[k] = 0.55f * (float)pow(1 - u, 1.6) * bloom;
        wd[k] = 1.2f + 1.6f * u;
    }
    for (unsigned d = 0; d < sizeof rp.ring; ++d) {
        float dist = d / 4.0f, keep = 1;
        for (int k = 0; k < RINGS; ++k) {
            float x = dist - r[k];
            x = x < 0 ? -x : x;
            if (x > 4 * BLOOM) continue;
            float core = clampf(wd[k] / 2 + 0.5f - x) * al[k];
            float glow =
                al[k] * wd[k] * 0.0798f * (float)pow(2.718281828, -x * x / (2.0f * BLOOM * BLOOM));
            keep *= (1 - core) * (1 - clampf(glow));
        }
        rp.ring[d] = (unsigned char)((1 - keep) * 256 > 255 ? 255 : (1 - keep) * 256);
    }
}

/* The way in's progress at now: 1 once over, or with none */
static float in_t(unsigned now) { return rp.in_at ? (float)(now - rp.in_at) : (float)IN_MS; }

/* The pane changed to Radio's with cover showing (coverflow's frame, w x h): it slides in over it.
 * Under Reduce Motion it is there at once. */
void rp_enter(const unsigned *cover, int w, int h) {
    free(rp.cover), rp.cover = 0, rp.in_at = 0;
    if (motion_reduced() || !cover) return;
    rp.cover = calloc((unsigned)(w * h), 4);
    if (!rp.cover) return;
    memcpy(rp.cover, cover, (unsigned)(w * h) * 4);
    rp.in_at = (unsigned)time_now_ms() | 1;
}

/* 1 while the pane moves: the way in, or rings leaving while Radio plays (not under Reduce Motion)
 */
int rp_moving(void) {
    return in_t((unsigned)time_now_ms()) < IN_MS || (tuner_state() == 1 && !motion_reduced());
}

/* The badge over d at (bx, by), alpha a (0..256) */
static void badge_at(unsigned *d, int pitch, int bx, int by, unsigned a) {
    for (int y = 0; y < BADGE; ++y) {
        int py = by + y;
        if (py < 0 || py >= rp.h) continue;
        for (int x = 0; x < BADGE; ++x) {
            int px = bx + x;
            if (px < 0 || px >= rp.w) continue;
            unsigned b = rp.badge[y * BADGE + x], ba = (b >> 24) * a >> 8;
            if (ba)
                d[py * pitch + px] =
                    rp_mix(d[py * pitch + px], b & 0xffffff, ba + (ba > 127)) | 0xff000000u;
        }
    }
}

/* One frame of the pane into d (pitch pixels a row, RGBA8888, the pane w x h with its top at screen
 * row top), at now; 0 when the layers can't be had. *off, *badge_a, *badge_dy, *freq_a, *freq_dy:
 * where rp_words draws the tower and the frequency this frame. */
int rp_frame(unsigned *d, int pitch, int w, int h, int top, unsigned now, rp_words_t *words) {
    if (!layers(w, h, top)) return 0;
    float t = in_t(now), s = 1, g = 1, c = 1;
    if (t < IN_MS) { /* the damped spring 0 -> 1 (w 14, z 0.62) */
        float ts = t / 1000;
        s = 1 - (float)pow(2.718281828, -0.62 * 14 * ts) * (float)cos(14 * 0.7846 * ts);
        g = ease((t - IN_GLOW_AT) / IN_GLOW_MS), c = ease(t / IN_COVER_MS);
    } else if (rp.cover)
        free(rp.cover), rp.cover = 0, rp.in_at = 0;
    int playing = tuner_state() == 1 && !motion_reduced();
    if (playing && !rp.playing) /* moving again from where they stood */
        rp.phase_at = now - (unsigned)(rp.phase * RING_EVERY_MS);
    if (playing)
        rp.phase = (float)((now - rp.phase_at) % RING_EVERY_MS) / RING_EVERY_MS;
    else if (motion_reduced())
        rp.phase = STILL_PHASE;
    rp.playing = playing;
    ring_table(rp.phase, g);
    int off = (int)(w * (1 - s)); /* the pane's left edge as it slides in */
    float sc = 1 - 0.08f * c;
    unsigned dim = (unsigned)(256 - 180 * c);
    for (int y = 0; y < h; ++y) {
        unsigned *o = d + y * pitch;
        for (int x = 0; x < w; ++x) {
            int rx = x - off;
            if (rx < 0 || rx >= w) { /* the cover, receding behind */
                unsigned v = 0;
                if (rp.cover) {
                    int sx = (int)((x - (w - w * sc) / 2 + IN_SHIFT * c) / sc),
                        sy = (int)((y - (h - h * sc) / 2) / sc);
                    if (sx >= 0 && sx < w && sy >= 0 && sy < h)
                        v = rp_mix(0, rp.cover[sy * w + sx], dim);
                }
                o[x] = v | 0xff000000u;
                continue;
            }
            int i = y * w + rx;
            unsigned v = rp.lit[i]; /* the ground and its glow, whole once the glow has bloomed */
            if (g < 1) {
                unsigned gl = (unsigned)(rp.glow[i] * g * 0.45f);
                v = rp.ground[i];
                v = pack((float)(v & 255) + GLOW_RGB[0] * gl / 255.0f,
                         (float)(v >> 8 & 255) + GLOW_RGB[1] * gl / 255.0f,
                         (float)(v >> 16 & 255) + GLOW_RGB[2] * gl / 255.0f);
            }
            unsigned ra = rp.ring[rp.d4[i]];
            if (ra) v = rp_mix(v, (unsigned)RING[0] | RING[1] << 8 | (unsigned)RING[2] << 16, ra);
            o[x] = v | 0xff000000u;
        }
    }
    float ba = ease((t - IN_BADGE_AT) / IN_RISE_MS), fa = ease((t - IN_FREQ_AT) / IN_RISE_MS);
    int bdy = (int)(IN_RISE * (1 - ba) + 0.5f);
    badge_at(d, pitch, off + rp.cx - BADGE / 2, rp.cy - BADGE / 2 + bdy, (unsigned)(256 * ba));
    unsigned va = (unsigned)(256 * fa);
    for (int y = rp.vy0; y < rp.vy1; ++y) /* the box the vignette reaches */
        for (int x = rp.vx0 + off; x < rp.vx1 + off; ++x) {
            int rx = x - off;
            if (x < 0 || x >= w) continue;
            unsigned a = rp.vig[y * w + rx] * va >> 8;
            if (a) d[y * pitch + x] = rp_mix(d[y * pitch + x], 0, a) | 0xff000000u;
        }
    words->off = off, words->badge_a = (int)(255 * ba), words->badge_dy = bdy;
    words->freq_a = (int)(255 * fa), words->freq_dy = (int)(IN_RISE * (1 - fa) + 0.5f);
    words->cx = rp.cx, words->cy = rp.cy;
    return 1;
}

/* Over the frame, in the pane's own coordinates: the tower white in the badge, and the frequency,
 * "93.1" in Inter Medium 30 and "FM" in 15 Text Secondary 3 px after it, the pair centred, its
 * baseline FREQ_DY below the badge's centre; each at the way in's alpha and rise. */
void rp_words(void *w, void *canvas, const rp_words_t *k) {
    int f = tuner_dial(), tenth = (f + 5) / 10;
    if (k->badge_a) {
        canvas_set_global_alpha(canvas, (unsigned)k->badge_a);
        paint_art(w, canvas, theme_art("radio_tower"), k->off + k->cx - RADIO_TOWER_PX / 2,
                  k->cy - RADIO_TOWER_PX / 2 + k->badge_dy);
    }
    if (f && k->freq_a) {
        char num[8];
        tk_snprintf(num, sizeof num, "%d.%d", tenth / 10, tenth % 10);
        int nw = ui_text_w(canvas, num, 1, TYPE(30)), fw = ui_text_w(canvas, "FM", 1, TYPE(15)),
            x0 = k->off + k->cx - (nw + 3 + fw) / 2, base = k->cy + FREQ_DY + k->freq_dy;
        canvas_set_global_alpha(canvas, (unsigned)k->freq_a);
        /* placed by the baseline, as radio_tuner.c rt_text_base: Inter's line centre 0.3635 em up
         */
        ui_text(canvas, num, 1, TYPE(30), RGB_PRIMARY, 0, x0, base - (30 * 3635 + 5000) / 10000,
                nw + 8, 2);
        ui_text(canvas, "FM", 1, TYPE(15), RGB_SECONDARY, 0, x0 + nw + 3,
                base - (15 * 3635 + 5000) / 10000, fw + 8, 2);
    }
    canvas_set_global_alpha(canvas, 0xff);
}
