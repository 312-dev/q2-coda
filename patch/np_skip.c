/* Now Playing's track change (docs/oled-design.md#motion): on Next, Previous, a song ending or a
 * headset's skip, the old art and lines slide NS_SHIFT px the way the queue moved and fade out over
 * NS_OUT_MS (cubic_in) while the new ones come in from NS_SHIFT px the other side over NS_IN_MS
 * (cubic_out) from NS_IN_AT on: NS_ALL_MS in all. The times and the bar change at once. Under
 * Reduce Motion the two cross-fade in place over MO_FADE_MS. A change mid-run starts again from
 * the frame on screen, so a run of skips never queues.
 *
 * The change is the lines' text (title, artist, album), as stock's timer writes it: the art the
 * page reloads (np_art_poll, stock's own reload) lands under the same run. ringnav_paint_bg, as the
 * page's window starts a frame (np_skip_begin), sees the new text before any of it is drawn: the
 * screen's buffer there still holds the last frame, so the art's box and the lines' box are copied
 * from it as the old. After the window's children (np_skip_end, from ringnav_paint) each box's new
 * pixels, as just drawn, are copied, and the box is made again from the page's ground, the new at
 * its shift and alpha and the old over it at its own, inside the canvas's clip. The direction is
 * the queue position's: back one is Previous, anything else Next. Only on the art's page (the
 * slide_view at 0), on the screen's own buffer (not a window's snapshot), with the screen lit.
 * ponytail: two copies of the band (375 x the art's height, BGRA) live while a run does, 2 x 220
 * KB.
 */
#include "navigation.h"
#include "ui_kit.h"

#define NS_SHIFT 16 /* px the old leaves by and the new comes from */
#define NS_OUT_MS 120
#define NS_IN_AT 60
#define NS_IN_MS 200
#define NS_ALL_MS (NS_IN_AT + NS_IN_MS)
#define NS_W 375
#define NS_H 320

static struct {
    void *win;           /* the page the key is of */
    unsigned key, pos;   /* its lines' text as last seen; the queue position then */
    unsigned at;         /* the run's start, time_now_ms; 0 none */
    int dir;             /* -1: the old leaves left (Next), 1: right (Previous) */
    int reduced;         /* the run is Reduce Motion's cross-fade */
    int y0, h;           /* the band: the rows both boxes lie in */
    int box[2][4];       /* the art's and the lines' boxes, x y w h on screen */
    unsigned *old, *now; /* the band's pixels: before the change, and this frame's */
} sk __attribute__((section(".scratch")));

/* The screen's own buffer under canvas (stock's 375 x 320 lcd_mem, BGRA8888), 0 for a window's
 * snapshot or another lcd. */
static unsigned *skip_fb(void *canvas) {
    void *lcd = canvas ? P(canvas, CANVAS_LCD) : (void *)0;
    if (!lcd || I(lcd, LCD_DRAW_MODE) != LCD_DRAW_NORMAL || I(lcd, LCD_FORMAT) != LCD_BGRA8888 ||
        I(lcd, LCD_W) != NS_W || I(lcd, LCD_H) != NS_H || I(lcd, LCD_LINE_LENGTH) != NS_W * 4)
        return (unsigned *)0;
    return P(lcd, LCD_OFFLINE_FB);
}

static unsigned text_key(unsigned h, void *w) {
    const unsigned *s = w ? widget_get_text(w) : (const unsigned *)0;
    unsigned n = 0;
    while (s && s[n]) ++n;
    return hash_bytes(h, (const unsigned char *)(s ? s : &n), s ? n * 4 : 0);
}

/* w's box on screen into b */
static void screen_box(void *w, int *b) {
    b[0] = b[1] = 0;
    for (void *p = w; p && p != window_manager(); p = P(p, W_PARENT))
        b[0] += I(p, W_X), b[1] += I(p, W_Y);
    b[2] = I(w, W_W), b[3] = I(w, W_H);
}

static void skip_free(void) {
    free(sk.old);
    free(sk.now);
    sk.old = sk.now = (unsigned *)0;
    sk.at = 0;
}

/* The boxes on screen: the art's, NS_SHIFT wider on its left (the margin) and as far right as the
 * lines start; the lines', from the title's top to the album's foot, NS_SHIFT wider on the left. */
static int skip_boxes(void) {
    void *t = st.np_title, *a = st.np_album ? st.np_album : st.np_artist;
    if (!st.np_cover || !t || !a) return 0;
    int c[4], tb[4], ab[4];
    screen_box(st.np_cover, c), screen_box(t, tb), screen_box(a, ab);
    int *art = sk.box[0], *lines = sk.box[1];
    art[0] = c[0] - NS_SHIFT < 0 ? 0 : c[0] - NS_SHIFT, art[1] = c[1];
    art[2] = tb[0] - art[0], art[3] = c[3];
    lines[0] = tb[0], lines[1] = tb[1], lines[2] = NS_W - tb[0], lines[3] = ab[1] + ab[3] - tb[1];
    sk.y0 = art[1] < lines[1] ? art[1] : lines[1];
    int end = art[1] + art[3] > lines[1] + lines[3] ? art[1] + art[3] : lines[1] + lines[3];
    if (sk.y0 < 0) sk.y0 = 0;
    if (end > NS_H) end = NS_H;
    sk.h = end - sk.y0;
    return art[2] > 0 && sk.h > 0;
}

/* ringnav_paint_bg, Now Playing's window starting a frame: new lines start a run, the old copied
 * from the screen as it stands. */
void np_skip_begin(void *w, void *canvas) {
    if (!w || w != st.np_win) return;
    unsigned key = text_key(text_key(text_key(FNV_SEED, st.np_title), st.np_artist), st.np_album);
    unsigned pos = 0, n = 0;
    queue_now(&pos, &n);
    int seen = sk.win == w && sk.key;
    if (sk.win != w) skip_free();
    if (seen && key == sk.key) return;
    int dir = pos + 1 == sk.pos ? 1 : -1;
    sk.win = w, sk.key = key, sk.pos = pos;
    unsigned *fb = skip_fb(canvas);
    if (!seen || !fb || !g_backlight_status ||
        (st.np_slide && widget_get_prop_int(st.np_slide, "value", 0)) || !skip_boxes()) {
        skip_free();
        return;
    }
    unsigned bytes = (unsigned)(NS_W * sk.h) * 4;
    free(sk.old), free(sk.now); /* a run under way: its band may have been another size */
    sk.old = calloc(1, bytes), sk.now = calloc(1, bytes);
    if (!sk.old || !sk.now) {
        skip_free();
        return;
    }
    memcpy(sk.old, fb + sk.y0 * NS_W, bytes);
    memcpy(sk.now, sk.old, bytes); /* until the window paints over it */
    sk.dir = dir;
    sk.reduced = motion_reduced();
    sk.at = (unsigned)time_now_ms() ?: 1;
    motion_frames(sk.reduced ? MO_FADE_MS : NS_ALL_MS);
}

/* At t ms into a run: the old's and the new's alpha (0..256) and shift (px, signed) */
void np_skip_at(int t, int reduced, int dir, int *a_old, int *x_old, int *a_new, int *x_new) {
    if (t < 0) t = 0;
    if (reduced) {
        int k = t >= MO_FADE_MS ? 256 : t * 256 / MO_FADE_MS;
        *a_old = 256 - k, *a_new = k, *x_old = *x_new = 0;
        return;
    }
    int o = motion_ease(t * 1000 / NS_OUT_MS, EASE_CUBIC_IN),
        i = t < NS_IN_AT ? 0 : motion_ease((t - NS_IN_AT) * 1000 / NS_IN_MS, EASE_CUBIC_OUT);
    *a_old = 256 - o * 256 / 1000, *x_old = dir * NS_SHIFT * o / 1000;
    *a_new = i * 256 / 1000, *x_new = -dir * NS_SHIFT * (1000 - i) / 1000;
}

static unsigned skip_mix(unsigned under, unsigned over, int a) {
    unsigned rb = under & 0xff00ff, g = under & 0xff00;
    rb += (((over & 0xff00ff) - rb) * (unsigned)a >> 8) & 0xff00ff;
    g += (((over & 0xff00) - g) * (unsigned)a >> 8) & 0xff00;
    return 0xff000000u | (rb & 0xff00ff) | (g & 0xff00);
}

/* One box's rows inside the clip c (x0 y0 x1 y1 on screen), from sk.now and sk.old into fb. */
static void skip_box(unsigned *fb, const int *b, const int *c, unsigned ground, int ao, int xo,
                     int an, int xn) {
    int x0 = b[0] > c[0] ? b[0] : c[0], x1 = b[0] + b[2] < c[2] ? b[0] + b[2] : c[2];
    int y0 = b[1] > c[1] ? b[1] : c[1], y1 = b[1] + b[3] < c[3] ? b[1] + b[3] : c[3];
    for (int y = y0; y < y1; ++y) {
        const unsigned *now = sk.now + (y - sk.y0) * NS_W, *old = sk.old + (y - sk.y0) * NS_W;
        for (int x = x0; x < x1; ++x) {
            int sn = x - xn, so = x - xo;
            unsigned p = skip_mix(ground, sn >= b[0] && sn < b[0] + b[2] ? now[sn] : ground, an);
            fb[y * NS_W + x] = skip_mix(p, so >= b[0] && so < b[0] + b[2] ? old[so] : ground, ao);
        }
    }
}

/* ringnav_paint, after Now Playing's window painted its children: the boxes made from the old and
 * the new at the run's place; the run's end lets the buffers go. */
void np_skip_end(void *w, void *canvas) {
    if (!sk.at || !w || w != sk.win) return;
    unsigned *fb = skip_fb(canvas);
    int t = (int)((unsigned)time_now_ms() - sk.at);
    if (!fb || t >= (sk.reduced ? MO_FADE_MS : NS_ALL_MS)) {
        skip_free();
        return;
    }
    int r[4], c[4];
    canvas_get_clip_rect(canvas, r);
    c[0] = r[0] < 0 ? 0 : r[0], c[1] = r[1] < sk.y0 ? sk.y0 : r[1];
    c[2] = r[0] + r[2] > NS_W ? NS_W : r[0] + r[2];
    c[3] = r[1] + r[3] > sk.y0 + sk.h ? sk.y0 + sk.h : r[1] + r[3];
    if (c[0] >= c[2] || c[1] >= c[3]) return;
    for (int y = c[1]; y < c[3]; ++y)
        memcpy(sk.now + (y - sk.y0) * NS_W + c[0], fb + y * NS_W + c[0],
               (unsigned)(c[2] - c[0]) * 4);
    int ao, xo, an, xn;
    np_skip_at(t, sk.reduced, sk.dir, &ao, &xo, &an, &xn);
    unsigned ground = 0xff000000u | (ipod_light() ? scheme()->paper : 0);
    for (int i = 0; i < 2; ++i) skip_box(fb, sk.box[i], c, ground, ao, xo, an, xn);
}
