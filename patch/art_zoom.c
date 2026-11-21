/* The cover travels into Now Playing (docs/oled-design.md#motion): opened from a page that shows
 * the playing song's cover (Home's art pane, a dressed album page's header), the cover lifts off
 * that page and travels to Now Playing's art as one card, and Return carries it back, when the page
 * under is still the one it came from; over another it is a plain cross-fade. Opened anywhere
 * else, Now Playing opens at once as before. Under Reduce Motion both are a MO_FADE_MS cross-fade
 * (AWTK's fade animator).
 *
 * The card (AZ_HERO, approved from the np-home-transition mock-up on 2026-11-20): its box runs
 * between the two covers on a damped spring (az_spring, response 0.42 s, damping 0.86, as a table)
 * along an arc AZ_ARC px high, its corners from the cover's radius to the other's, and it shows
 * the cover aspect-filled at a zoom from 1.00 at Now Playing to Home's 1.10 at the pane, so it is
 * cropped, never squashed, as it changes shape. Home's card is drawn from the pane's own texture
 * (coverflow_home_cover) at the pane's pan, with the pane's vignette coming in over its last
 * 45%, so it lands on the pane as the pane is; a soft shadow (az_shadow, a pre-blurred edge
 * profile) lies under it in flight. Around it the rest of Now Playing fades over the first 55%
 * and draws back to 0.96, and Home's list fades and slides AZ_SLIDE px, on the same spring
 * AZ_LAG_MS behind. Each snapshot is drawn with its own cover cut out (az_holed), so the cover is
 * never on the screen twice. Return is the same run backwards.
 *
 * AZ_HERO 0 is the lite variant, the same box, crop and corners on a critically damped spring
 * along a straight line, with no shadow, no scaling of Now Playing and no slide of the list:
 * plain cross-fades, about the cost of the old square's run.
 *
 * art_zoom_load, as Now Playing's window loads (nav/home_carousel.c motion_load), finds the cover
 * on the window under it, the page it opens over (art_zoom_source), and if there is one gives the
 * window an open_anim_hint and a close_anim_hint naming AZ_TYPE, an animator registered with
 * stock's window animator factory on first use. It is stock's own window_animator_t
 * (window_animator_create) with a vtable of its own: stock's htranslate update (whose linear time,
 * time_percent, the run follows) and two draws over its snapshots: the page (prev_img) on the
 * ground, then Now Playing (curr_img), the shadow and the card, which writes the screen's buffer
 * itself (art_zoom_card).
 */
#include "navigation.h"

#ifndef AZ_HERO
#define AZ_HERO 1 /* 0: the lite variant (above) */
#endif
#define AZ_TYPE "art_zoom"
#define AZ_HERO_MS 420    /* the hero's run, each way */
#define AZ_LITE_MS 340    /* the lite variant's */
#define AZ_ARC 14         /* the hero's path, px above the straight line at its middle */
#define AZ_SLIDE 28       /* Home's list, px left of its place at the run's far end */
#define AZ_LAG_MS 40      /* the list behind the card */
#define AZ_SCALE 960      /* Now Playing drawn back to this, thousandths */
#define AZ_PANE_ZOOM 1100 /* the pane's cover (coverflow.c DRIFT_ZOOM_PCT), thousandths */
#define AZ_NP_R 6         /* Now Playing's art's corners (theme playing.art.radius) */
#define AZ_SHADOW 200     /* the shadow's darkest, 255ths, mid-flight */
#define AZ_SHADOW_DY 6    /* below the card */
#define AZ_BLUR 16        /* the shadow's reach past the card's edge (az_edge's half) */
#define AZ_INSET 3        /* Now Playing's art read this far in from its rounded corners */
/* window_animator_t: prev_win @0x14, prev_img @0x1c, curr_img @0x64, open @0xac, percent @0xb4,
 * canvas @0xb8, time_percent @0xbc (float, 0..1, linear); its vtable: type, desc, size, overlap,
 * init, update_percent, draw_prev_window, draw_curr_window, destroy (stock's htranslate's at
 * 0x93b234) */
#define WA_PREV_WIN 0x14
#define WA_PREV_IMG 0x1c
#define WA_CURR_IMG 0x64
#define WA_OPEN 0xac
#define WA_CANVAS 0xb8
#define WA_TIME 0xbc
#define WA_SIZE 0xc8

static struct {
    void *from;   /* the page Now Playing opened over with a cover; 0 none */
    rect_t cover; /* that cover on screen */
    int kind;     /* AZ_PANE, AZ_ALBUM */
    int registered;
    int force; /* test/fw: 1 the hero, 2 the lite variant, 0 AZ_HERO */
} az __attribute__((section(".scratch")));

/* Each spring's value at 32 even steps of its run, thousandths, the last 1000 (normalized by the
 * value there, so the run ends at rest): the hero's response 0.42 s, damping 0.86 over 420 ms,
 * the lite variant's response 0.36 s, critically damped, over 340 ms. */
static const short az_hero_spring[33] = {
    0,   17,  61,  123, 196, 274, 353, 430, 504, 572, 635, 691, 742, 786,  824,  857, 885,
    908, 928, 944, 957, 968, 976, 983, 988, 992, 995, 997, 998, 999, 1000, 1000, 1000
};
static const short az_lite_spring[33] = { 0,   15,  55,  110, 174, 242, 311, 379, 445, 506, 564,
                                          616, 664, 707, 746, 780, 811, 838, 862, 883, 901, 917,
                                          931, 943, 954, 963, 971, 978, 984, 989, 993, 997, 1000 };
/* The shadow's pre-blurred edge: a Gaussian's (sigma 7 px) share inside an edge d px away, d from
 * -AZ_BLUR to AZ_BLUR, in 255ths. */
static const unsigned char az_edge[2 * AZ_BLUR + 1] = { 0,   1,   3,   5,   8,   12,  17,  23,  30,
                                                        38,  48,  59,  71,  84,  98,  113, 128, 142,
                                                        157, 171, 184, 196, 207, 217, 225, 232, 238,
                                                        243, 247, 250, 252, 254, 255 };

int art_zoom_hero(void) { return az.force ? az.force == 1 : AZ_HERO; }
void art_zoom_force(int v) { az.force = v; } /* test/fw */

/* win's open_anim_hint or close_anim_hint, "" when it has none. Stock's window_base_get_prop
 * answers these itself with the window's own field, which is NULL until one is set, and
 * widget_get_prop_str passes that NULL on instead of its default: a window loaded new (Now Playing
 * over a list, the first time) has none. */
const char *window_hint(void *win, const char *name) {
    const char *hint = widget_get_prop_str(win, name, "");
    return hint ? hint : "";
}

/* w's box on screen */
static rect_t screen_rect(void *w) {
    rect_t r = { 0, 0, I(w, W_W), I(w, W_H) };
    for (void *p = w; p && p != window_manager(); p = P(p, W_PARENT))
        r.x += I(p, W_X), r.y += I(p, W_Y);
    return r;
}

/* The playing song's cover on top, the page Now Playing is opening over, into r: Home's whole art
 * pane (AZ_PANE: the cover of the row it opened from, Now Playing's, fills it cropped), or a
 * dressed album page's header (AZ_ALBUM); 0 for none. */
int art_zoom_source(void *top, rect_t *r) {
    void *art = coverflow_home_pane();
    if (top && art && window_of(art) == top && widget_get_visible(art)) {
        *r = screen_rect(art);
        return r->w > 0 && r->h > 0 ? AZ_PANE : 0;
    }
    return top && album_local_on(top) && hero_cover_box(r) ? AZ_ALBUM : 0;
}

/* x in thousandths through the stretch a..b, eased in and out (smoothstep), in thousandths. */
static int az_smooth(int a, int b, int x) {
    if (x <= a) return 0;
    if (x >= b) return 1000;
    int s = (x - a) * 1000 / (b - a); /* the payload has no 64-bit division: 32 bits throughout */
    return s * s / 1000 * (3000 - 2 * s) / 1000;
}

/* The spring at t thousandths of the run (before 0: 0, after 1000: 1000), thousandths. */
static int az_spring(int t, int hero) {
    const short *s = hero ? az_hero_spring : az_lite_spring;
    if (t <= 0) return 0;
    if (t >= 1000) return 1000;
    int i = t * 32 / 1000, f = t * 32 % 1000;
    return s[i] + (s[i + 1] - s[i]) * f / 1000;
}

/* sin(pi x) for x in thousandths (Bhaskara's), thousandths. */
static int az_sin(int x) {
    x = x < 0 ? 0 : x > 1000 ? 1000 : x;
    int p = x * (1000 - x); /* x(1-x) in millionths */
    return 16 * p / ((5000000 - 4 * p) / 1000);
}

static int az_lerp(int a, int b, int k) { return a + (b - a) * k / 1000; }

/* At t thousandths of the run's time (time_percent), opening (Home to Now Playing) or not, from
 * the page's cover (src, its corners src_r, its zoom src_z: Home's pane 0, 6, AZ_PANE_ZOOM) to
 * Now Playing's art (np): everything one frame draws (art_zoom_frame_t, navigation.h). */
void art_zoom_frame(int t, int open, int hero, const rect_t *src, int src_r, int src_z,
                    const rect_t *np, art_zoom_frame_t *f) {
    t = t < 0 ? 0 : t > 1000 ? 1000 : t;
    int ms = hero ? AZ_HERO_MS : AZ_LITE_MS, k = az_spring(t, hero),
        kc = k < 0      ? 0
             : k > 1000 ? 1000
                        : k;
    /* page: the page's end, 1000 at its cover, 0 at Now Playing's */
    int page = open ? 1000 - k : k, pc = open ? 1000 - kc : kc;
    const rect_t *a = open ? src : np, *b = open ? np : src;
    f->card.x = az_lerp(a->x, b->x, k), f->card.y = az_lerp(a->y, b->y, k);
    f->card.w = az_lerp(a->w, b->w, k), f->card.h = az_lerp(a->h, b->h, k);
    if (hero) f->card.y -= AZ_ARC * az_sin(kc) / 1000;
    f->radius = az_lerp(AZ_NP_R, src_r, pc);
    f->zoom = az_lerp(1000, src_z, page);
    if (f->zoom < 1000) f->zoom = 1000; /* past 1.00 the crop would leave the cover */
    f->pane = pc;
    /* the pane's vignette: in over the last 45% of the way to the pane, out over the first 45% of
     * the way from it */
    f->vig = open ? 1000 - az_smooth(0, 450, kc) : az_smooth(550, 1000, kc);
    f->np_a = open ? az_smooth(300, 950, t) : 1000 - az_smooth(0, 550, t);
    f->np_scale = !hero  ? 1000
                  : open ? az_lerp(AZ_SCALE, 1000, az_smooth(250, 1000, t))
                         : az_lerp(1000, AZ_SCALE, az_smooth(0, 1000, t));
    f->page_a = open ? 1000 - az_smooth(0, 450, t) : az_smooth(150, 700, t);
    f->page_dx = !hero  ? 0
                 : open ? -AZ_SLIDE * az_smooth(0, 1000, t) / 1000
                        : -AZ_SLIDE * (1000 - az_spring(t - AZ_LAG_MS * 1000 / ms, 1)) / 1000;
    int lift = t * 115 / 100;
    f->shadow = hero ? AZ_SHADOW * az_sin(lift > 1000 ? 1000 : lift) / 1000 : 0;
    /* each end's own pixels over the card: the page's cover as the page shows it (its pan, the
     * status row's glyphs over it), Now Playing's art as Now Playing shows it */
    f->src_a = open ? 1000 - az_smooth(0, 100, t) : az_smooth(800, 1000, t);
    f->np_art_a = open ? az_smooth(850, 1000, t) : 1000 - az_smooth(0, 100, t);
}

/* The texel window for a w x h card showing a tw x th texture aspect-filled at zoom (thousandths):
 * its top left u v and its step, texels a pixel, all in 1/65536 texel; centered, then moved toward
 * the pane's own window (pu pv, 1/256 texel, a pw x ph pane at AZ_PANE_ZOOM, a texel a pixel) by
 * pane thousandths, so the pane's end is the pane's window exactly. */
void art_zoom_window(int w, int h, int zoom, int tw, int th, int pane, int pu, int pv, int pw,
                     int ph, int *u, int *v, int *step) {
    /* texels a pixel, 1/65536: the axis the cover fills (the smaller texels-a-pixel), over the
     * zoom, so a pane-sized card at the pane's zoom steps a texel a pixel exactly. In float: the
     * payload has no 64-bit division, and these are once a frame. */
    int by_w = tw * h <= th * w; /* tw / w <= th / h */
    float den = (float)(by_w ? w : h) * (float)zoom;
    int s = den > 0 ? (int)((float)(by_w ? tw : th) * 65536000.0f / den + 0.5f) : 65536;
    /* the window's room to move, and its centered place */
    long long umax = (long long)tw * 65536 - (long long)w * s,
              vmax = (long long)th * 65536 - (long long)h * s;
    long long uu = umax >> 1, vv = vmax >> 1;
    /* the pane's own: a texel a pixel, its window where the pan has it, against its centered one */
    if (pw && pane) {
        int du = pu * 256 - (tw - pw) * 32768, dv = pv * 256 - (th - ph) * 32768; /* < 2^31 */
        uu += pane >= 1000 ? du : (int)((float)du * (float)pane / 1000.0f);
        vv += pane >= 1000 ? dv : (int)((float)dv * (float)pane / 1000.0f);
    }
    uu = uu < 0 ? 0 : uu > umax ? (umax < 0 ? 0 : umax) : uu;
    vv = vv < 0 ? 0 : vv > vmax ? (vmax < 0 ? 0 : vmax) : vv;
    *u = (int)uu, *v = (int)vv, *step = (int)s;
}

/* Integer square root (floor). */
static unsigned az_isqrt(unsigned n) {
    unsigned r = 0, b = 1u << 30;
    while (b > n) b >>= 2;
    for (; b; b >>= 2)
        if (n >= r + b)
            n -= r + b, r = (r >> 1) + b;
        else
            r >>= 1;
    return r;
}

/* Row y's corner inset of a w x h card with r px corners, in 1/256 px: 0 off the corners. */
static int az_inset(int y, int h, int r) {
    if (r <= 0) return 0;
    int dy = y < r ? r * 2 - (2 * y + 1) : y >= h - r ? 2 * y + 1 - (h - r) * 2 : 0; /* 2x */
    if (!dy) return 0;
    unsigned dx = az_isqrt((unsigned)(4 * r * r - dy * dy) * 65536u); /* 2x, 1/256 */
    return r * 256 - (int)(dx / 2);
}

/* Per pixel: inlined, as -Os would call them (coverflow.c's HOT) */
#define AZ_HOT static inline __attribute__((always_inline))
/* a to b by k 256ths, per channel, opaque */
AZ_HOT unsigned az_mix(unsigned a, unsigned b, unsigned k) {
    return 0xff000000u | (((a & 0xff00ffu) * (256 - k) + (b & 0xff00ffu) * k) >> 8 & 0xff00ffu) |
           (((a & 0xff00u) * (256 - k) + (b & 0xff00u) * k) >> 8 & 0xff00u);
}
/* c (BGRA) darkened to k 256ths */
AZ_HOT unsigned az_scale(unsigned c, unsigned k) {
    return (c & 0xff000000u) | ((c & 0xff00ffu) * k >> 8 & 0xff00ffu) |
           ((c & 0xff00u) * k >> 8 & 0xff00u);
}
/* The texture at at_u (1/65536 texel along row, fy the row's 256ths down to the next): bilinear,
 * red and blue swapped from RGBA when rgba, darkened to dim 256ths when under 256. */
AZ_HOT unsigned az_texel(const unsigned *row, int ts, unsigned at_u, unsigned fy, int rgba,
                         unsigned dim) {
    const unsigned *t = row + (at_u >> 16);
    unsigned fx = at_u >> 8 & 255,
             c = az_mix(az_mix(t[0], t[1], fx), az_mix(t[ts], t[ts + 1], fx), fy);
    if (rgba) c = (c & 0xff00ff00u) | (c >> 16 & 0xffu) | (c & 0xffu) << 16;
    return dim < 256 ? az_scale(c, dim) : c;
}
/* One stretch of a row, x from a to b, the loop specialized on rgba and dim */
#define AZ_SPAN(RGBA, DIM)                                                                         \
    for (int x = a; x < b; ++x, at_u += (unsigned)step)                                            \
        d[x] = az_texel(row, ts, at_u, fy, RGBA, DIM);

/* The card into fb (375 x 320, pitch words a row, BGRA8888): box r (on screen) with corners
 * radius px, each pixel the texture tex (texel 0, 0 at tex, ts words a row, a texel of border right
 * and below it; RGBA8888 when rgba, the pane's, else BGRA as a snapshot's) at u + x step,
 * v + y step (1/65536 texel), bilinear, darkened by the pane's vignette at vig thousandths
 * (coverflow_home_vig, for the card's own height). The corners' edge pixels are blended by their
 * cover. Only what is on the screen is written. */
void art_zoom_card(unsigned *fb, int pitch, const rect_t *r, int radius, const unsigned *tex,
                   int ts, int u, int v, int step, int vig, int rgba) {
    if (r->w <= 0 || r->h <= 0) return;
    int x0 = r->x < 0 ? -r->x : 0, x1 = r->x + r->w > 375 ? 375 - r->x : r->w;
    for (int y = r->y < 0 ? -r->y : 0; y < r->h && r->y + y < 320; ++y) {
        int in = az_inset(y, r->h, radius), full = (in + 255) >> 8, edge = in >> 8;
        unsigned dim = 256;
        if (vig) {
            unsigned k = coverflow_home_vig(y, r->h);
            dim = 256 - (256 - k) * (unsigned)vig / 1000;
        }
        unsigned at_v = (unsigned)v + (unsigned)y * (unsigned)step, fy = at_v >> 8 & 255;
        const unsigned *row = tex + (int)(at_v >> 16) * ts;
        unsigned *d = fb + (r->y + y) * pitch + r->x;
        /* the corners' part-covered pixels, each blended by its share */
        if (in & 255)
            for (int k = 0, x = edge; k < 2; ++k, x = r->w - 1 - edge)
                if (x >= x0 && x < x1)
                    d[x] = az_mix(d[x],
                                  az_texel(row, ts, (unsigned)u + (unsigned)x * (unsigned)step, fy,
                                           rgba, dim),
                                  256 - (unsigned)(in & 255));
        int a = full > x0 ? full : x0, b = r->w - full < x1 ? r->w - full : x1;
        unsigned at_u = (unsigned)u + (unsigned)a * (unsigned)step;
        if (rgba && dim < 256) {
            AZ_SPAN(1, dim)
        } else if (rgba) {
            AZ_SPAN(1, 256)
        } else if (dim < 256) {
            AZ_SPAN(0, dim)
        } else {
            AZ_SPAN(0, 256)
        }
    }
}

/* The shadow under box r, AZ_SHADOW_DY lower, at its darkest a 255ths: the screen darkened by the
 * product of az_edge's share across each axis, where the card will not cover it. A product of two
 * 255ths over 255 is taken as (p + (p >> 8) + 1) >> 8, with no division. */
void art_zoom_shadow(unsigned *fb, int pitch, const rect_t *r, unsigned a) {
    if (!a) return;
    int top = r->y + AZ_SHADOW_DY, bot = top + r->h, left = r->x, right = r->x + r->w;
    int lo = left - AZ_BLUR < 0 ? 0 : left - AZ_BLUR,
        hi = right + AZ_BLUR > 375 ? 375 : right + AZ_BLUR;
    unsigned char col[375]; /* each column's share across, 255ths */
    for (int x = lo; x < hi; ++x) {
        int dl = x - left, dr = right - 1 - x;
        unsigned p = (unsigned)az_edge[dl > AZ_BLUR ? 2 * AZ_BLUR : dl + AZ_BLUR] *
                     az_edge[dr > AZ_BLUR ? 2 * AZ_BLUR : dr + AZ_BLUR];
        col[x] = (unsigned char)((p + (p >> 8) + 1) >> 8);
    }
    for (int y = top - AZ_BLUR; y < bot + AZ_BLUR; ++y) {
        if (y < 0 || y >= 320) continue;
        int dt = y - top, db = bot - 1 - y;
        unsigned p = (unsigned)az_edge[dt > AZ_BLUR ? 2 * AZ_BLUR : dt + AZ_BLUR] *
                     az_edge[db > AZ_BLUR ? 2 * AZ_BLUR : db + AZ_BLUR],
                 py = (p + (p >> 8) + 1) >> 8, ka = py * a, k = (ka + (ka >> 8) + 1) >> 8;
        if (!k) continue;
        int under = y >= r->y && y < r->y + r->h; /* the card's rows: only its sides */
        unsigned *d = fb + y * pitch;
        for (int x = lo; x < hi; ++x) {
            if (under && x == left) {
                x = right - 1;
                continue;
            }
            unsigned s = col[x] * k >> 8;
            if (s) d[x] = az_scale(d[x], 256 - s);
        }
    }
}

/* A snapshot with a hole: img (full screen) drawn through the canvas at alpha (255ths), scaled
 * about the screen's middle by scale thousandths and moved dx, all but the rect hole (in the
 * snapshot), as up to four pieces around it, whose edges are scaled once each so they meet. */
static void az_holed(void *c, void *img, const rect_t *hole, unsigned alpha, int scale, int dx) {
    if (!alpha) return;
    int xs[4] = { 0, hole->x, hole->x + hole->w, 375 },
        ys[4] = { 0, hole->y, hole->y + hole->h, 320 };
    for (int i = 0; i < 4; ++i) {
        xs[i] = xs[i] < 0 ? 0 : xs[i] > 375 ? 375 : xs[i];
        ys[i] = ys[i] < 0 ? 0 : ys[i] > 320 ? 320 : ys[i];
    }
    int sx[4], sy[4];
    for (int i = 0; i < 4; ++i) /* scaled about 187.5, 160, rounded */
        sx[i] = ((2 * xs[i] - 375) * scale + 375000 + 1000) / 2000 + dx,
        sy[i] = ((2 * ys[i] - 320) * scale + 320000 + 1000) / 2000;
    canvas_set_global_alpha(c, alpha);
    for (int j = 0; j < 3; ++j)
        for (int i = 0; i < 3; ++i) {
            if (i == 1 && j == 1) continue; /* the hole */
            rect_t s = { xs[i], ys[j], xs[i + 1] - xs[i], ys[j + 1] - ys[j] },
                   d = { sx[i], sy[j], sx[i + 1] - sx[i], sy[j + 1] - sy[j] };
            if (s.w > 0 && s.h > 0 && d.w > 0 && d.h > 0) canvas_draw_image(c, img, &s, &d);
        }
    canvas_set_global_alpha(c, 255);
}

static int az_t(void *wa) {
    int t = (int)(*(float *)((char *)wa + WA_TIME) * 1000 + 0.5f);
    return t < 0 ? 0 : t > 1000 ? 1000 : t;
}
static int az_open(void *wa) { return *((unsigned char *)wa + WA_OPEN) != 0; }
static int az_flies(void *wa) { return az.from && P(wa, WA_PREV_WIN) == az.from && st.np_cover; }

/* Now Playing's art as it stands, smaller when paused (np_settle.c) */
static rect_t az_np_art(void) {
    int b[4];
    rect_t to = screen_rect(st.np_cover);
    np_settle_box(st.np_cover, b);
    to.x += b[0], to.y += b[1], to.w = b[2], to.h = b[3];
    return to;
}

static void az_frame(int t, int open, art_zoom_frame_t *f, rect_t *np) {
    *np = az_np_art();
    art_zoom_frame(t, open, art_zoom_hero(), &az.cover, az.kind == AZ_PANE ? 0 : AZ_NP_R,
                   az.kind == AZ_PANE ? AZ_PANE_ZOOM : 1000, np, f);
}

static int az_update(void *wa) { return htranslate_update(wa); }

/* The screen's buffer the canvas draws into (375 words a row, BGRA8888), else 0. */
static unsigned *az_fb(void *lcd) {
    if (I(lcd, LCD_FORMAT) != LCD_BGRA8888 || I(lcd, LCD_W) != 375 || I(lcd, LCD_H) != 320 ||
        I(lcd, LCD_LINE_LENGTH) != 375 * 4)
        return 0;
    return P(lcd, LCD_OFFLINE_FB);
}

/* A snapshot's rect s drawn at the card's box at alpha a (thousandths). */
static void az_over(void *c, void *img, const rect_t *s, const rect_t *card, int a) {
    if (a <= 0 || s->w <= 0 || s->h <= 0 || card->w <= 0 || card->h <= 0) return;
    canvas_set_global_alpha(c, (unsigned)(a * 255 / 1000));
    canvas_draw_image(c, img, s, card);
    canvas_set_global_alpha(c, 255);
}

/* The ground, then the page (img) at t thousandths of the run: from the page Now Playing came
 * from (flies), all but its cover, fading and sliding; over another, the whole page fading out
 * with Now Playing's run. */
static void az_draw_prev(void *c, void *img, int t, int open, int flies) {
    void *lcd = P(c, CANVAS_LCD);
    if (!lcd) return;
    unsigned fill = (unsigned)I(lcd, LCD_FILL_COLOR);
    stock_canvas_set_fill_color(c, 0xff000000u | (ipod_light() ? scheme()->paper : 0));
    canvas_fill_rect(c, 0, 0, I(lcd, LCD_W), I(lcd, LCD_H));
    stock_canvas_set_fill_color(c, fill);
    rect_t none = { 0, 0, 0, 0 };
    if (!flies) {
        int a = open ? 1000 - az_smooth(0, 500, t) : az_smooth(500, 1000, t);
        az_holed(c, img, &none, (unsigned)(a * 255 / 1000), 1000, 0);
        return;
    }
    art_zoom_frame_t f;
    rect_t np;
    az_frame(t, open, &f, &np);
    az_holed(c, img, &az.cover, (unsigned)(f.page_a * 255 / 1000), 1000, f.page_dx);
}

/* Now Playing (img) at t: from the page it came from, without its art, then the shadow and the
 * card, and each end's own cover over the card (prev the page's snapshot); else Now Playing
 * fading in or out whole. */
static void az_draw_curr(void *c, void *img, void *prev, int t, int open, int flies) {
    void *lcd = P(c, CANVAS_LCD);
    if (!lcd) return;
    rect_t none = { 0, 0, 0, 0 };
    if (!flies) {
        int a = open ? az_smooth(400, 1000, t) : 1000 - az_smooth(0, 600, t);
        az_holed(c, img, &none, (unsigned)(a * 255 / 1000), 1000, 0);
        return;
    }
    art_zoom_frame_t f;
    rect_t np;
    az_frame(t, open, &f, &np);
    az_holed(c, img, &np, (unsigned)(f.np_a * 255 / 1000), f.np_scale, 0);
    unsigned *fb = az_fb(lcd);
    if (fb && f.shadow) art_zoom_shadow(fb, 375, &f.card, (unsigned)f.shadow);
    /* the card: the pane's texture where Home's pane has one, else Now Playing's art itself */
    const unsigned *tex = 0;
    int tw, th, pu = 0, pv = 0, ts, u, v, step, drew = 0;
    if (fb && az.kind == AZ_PANE && coverflow_home_cover(&tex, &tw, &th, &pu, &pv)) {
        ts = tw + 3;
        art_zoom_window(f.card.w, f.card.h, f.zoom, tw, th, f.pane, pu, pv, az.cover.w, az.cover.h,
                        &u, &v, &step);
        art_zoom_card(fb, 375, &f.card, f.radius, tex + ts + 1, ts, u, v, step, f.vig, 1);
        drew = 1;
    } else if (fb && np.w > 2 * AZ_INSET + 1 && np.h > 2 * AZ_INSET + 1) {
        const unsigned *px = (const unsigned *)bitmap_lock_buffer_for_read(img);
        if (px) {
            ts = (int)(bitmap_get_line_length(img) / 4);
            tw = np.w - 2 * AZ_INSET - 1, th = np.h - 2 * AZ_INSET - 1; /* the last for bilinear */
            art_zoom_window(f.card.w, f.card.h, f.zoom, tw, th, 0, 0, 0, 0, 0, &u, &v, &step);
            art_zoom_card(fb, 375, &f.card, f.radius, px + (np.y + AZ_INSET) * ts + np.x + AZ_INSET,
                          ts, u, v, step, az.kind == AZ_PANE ? f.vig : 0, 0);
            bitmap_unlock_buffer(img);
            drew = 1;
        }
    }
    if (!drew) { /* no buffer to write: Now Playing's art scaled into the box, square */
        az_over(c, img, &np, &f.card, 1000);
        return;
    }
    /* each end's own pixels over the card, so the run starts and ends on them with no step */
    az_over(c, prev, &az.cover, &f.card, f.src_a);
    az_over(c, img, &np, &f.card, f.np_art_a);
}

static int az_prev(void *wa) {
    void *c = P(wa, WA_CANVAS);
    if (c) az_draw_prev(c, (char *)wa + WA_PREV_IMG, az_t(wa), az_open(wa), az_flies(wa));
    return 0;
}
static int az_curr(void *wa) {
    void *c = P(wa, WA_CANVAS);
    if (c)
        az_draw_curr(c, (char *)wa + WA_CURR_IMG, (char *)wa + WA_PREV_IMG, az_t(wa), az_open(wa),
                     az_flies(wa));
    return 0;
}

static const struct {
    const char *type, *desc;
    unsigned size;
    int overlap;
    int (*init)(void *), (*update)(void *), (*prev)(void *), (*curr)(void *);
    void (*destroy)(void *);
} az_vt = { AZ_TYPE, AZ_TYPE, WA_SIZE, 0, 0, az_update, az_prev, az_curr, 0 };

static void *az_create(int open, void *args) {
    (void)args;
    return window_animator_create(open, &az_vt);
}

/* Now Playing's window as it loads: from a cover, its open and close take AZ_TYPE (a fade under
 * Reduce Motion); else its open stays immediate and its close the theme's (motion_hint). */
void art_zoom_load(void *np_win) {
    void *wm = window_manager(), *top = (void *)0;
    for (int i = (int)widget_count_children(wm) - 1, below = 0; i >= 0 && !top; --i) {
        void *w = widget_get_child(wm, (unsigned)i); /* the window under Now Playing, which is in */
        if (w == np_win)                             /* the stack as it loads */
            below = 1;
        else if (below && !tk_strcmp(widget_get_type(w), "window"))
            top = w;
    }
    az.kind = art_zoom_source(top, &az.cover);
    az.from = az.kind ? top : (void *)0;
    if (!az.from) { /* a reused window's last open: immediate again */
        if (window_hint(np_win, "open_anim_hint")[0])
            widget_set_prop_str(np_win, "open_anim_hint", "");
        return;
    }
    int reduced = motion_reduced(), hero = art_zoom_hero();
    if (!reduced && !az.registered)
        az.registered =
            !window_animator_factory_register(window_animator_factory(), AZ_TYPE, az_create);
    const char *run = hero ? "art_zoom(duration=420)" : "art_zoom(duration=340)";
    widget_set_prop_str(np_win, "open_anim_hint", reduced ? "fade(duration=150)" : run);
    widget_set_prop_str(np_win, "close_anim_hint", reduced ? "fade(duration=150)" : run);
    _Static_assert(MO_FADE_MS == 150 && AZ_HERO_MS == 420 && AZ_LITE_MS == 340,
                   "the hints' durations");
}
