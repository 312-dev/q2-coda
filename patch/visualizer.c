/* iPod Now Playing's last page (docs/ipod.md#visualizer): the music as heard, read from
 * hciplayer's tap (peq.h), as Field, after iTunes' Magnetosphere. Off for the battery while
 * NP_VISUALIZER (offsets.inc) is 0: navigation.c calls none of this, and the link drops it. */
#include "offsets.inc"
#include "peq.h"

#define VIS_N 1024                /* frames each frame analyses */
#define VIS_BANDS 64              /* log-spaced bands, 40 Hz to 16 kHz */
#define VIS_X 16                  /* tools/ipod.py NP_MARGIN */
#define PAGE_W (vz.w - 2 * VIS_X) /* the song line's room: the slide's width */
#define VIS_SLIDE_H 186 /* tools/ipod.py NP_SLIDE_H, the page's height, from the lyrics' top */
#define VIS_H                                                                                      \
    (vz.h ? vz.h : VIS_SLIDE_H) /* ... or less where the slide ends sooner (OLED's np_grid) */
#define TAU 6.2831853f

static struct {
    float re[VIS_N], im[VIS_N], pcm[VIS_N][2], hann[VIS_N]; /* hann[VIS_N / 2] is 0 until filled */
    float level[VIS_BANDS];
    unsigned bin_rate;
    int w, h;               /* the page's width and height (visualizer_attach; h 0: VIS_SLIDE_H) */
    int bin[VIS_BANDS + 1]; /* each band's first FFT bin at bin_rate */
#ifndef PEQ_HOST
    void *win, *slide, *page;
    vis_tap *tap;
    unsigned timer, last, heard; /* heard: the last tick with audio */
    unsigned retry;              /* a paint missed while not settled looks again (look) */
    int ox, oy,
        page_x;       /* the slide area in the window; the page's canvas x there as last painted */
    float flow;       /* Field's clock, seconds of music, moved on while it plays */
    void *glow[5][4]; /* Field's sprites (field_sprites), each band's at four strengths; kept for
                       * demo's life: 5 x 4 x 32 x 32 x 4 bytes, 80 KB */
    int chrome;       /* OLED's full screen (full()): the page's other parts' alpha, 0-255 */
    unsigned chrome_at,
        lift;   /* chrome's last step, and the last time a thumb rested on the wheel */
    int in_bar; /* bar_paint is painting the status row again: its own paint is stock's */
    int quiet;  /* at rest (not playing, nothing heard) as last ticked: the track's name shows, and
                 * its change repaints; the last word (test/patch.py finds it so) */
#endif
} vz __attribute__((section(".scratch")));

/* In-place radix-2 FFT of n complex values, n a power of two. */
static void fft(float *re, float *im, int n) {
    for (int i = 1, j = 0; i < n; ++i) {
        int bit = n >> 1;
        for (; j & bit; bit >>= 1) j ^= bit;
        j ^= bit;
        if (i < j) {
            float t = re[i];
            re[i] = re[j];
            re[j] = t;
            t = im[i];
            im[i] = im[j];
            im[j] = t;
        }
    }
    for (int len = 2; len <= n; len <<= 1) {
        float wr = cosf(TAU / len), wi = -sinf(TAU / len);
        for (int i = 0; i < n; i += len) {
            float cr = 1, ci = 0;
            for (int k = i; k < i + len / 2; ++k) {
                float tr = re[k + len / 2] * cr - im[k + len / 2] * ci,
                      ti = re[k + len / 2] * ci + im[k + len / 2] * cr;
                re[k + len / 2] = re[k] - tr;
                im[k + len / 2] = im[k] - ti;
                re[k] += tr;
                im[k] += ti;
                float t = cr * wr - ci * wi;
                ci = cr * wi + ci * wr;
                cr = t;
            }
        }
    }
}

static int band_bin(int b, unsigned rate) {
    return (int)(40 * pow(400, (double)b / VIS_BANDS) * VIS_N / rate + 0.5);
}

/* level[VIS_BANDS]: each band's loudest bin of the Hann-windowed mono mix of VIS_N frames at rate,
 * 0 at VIS_FLOOR_DB below a full-scale sine to 1 at it. */
void vis_bands(const float (*pcm)[2], unsigned rate, float *level) {
    if (!vz.hann[VIS_N / 2]) /* the window, once */
        for (int i = 0; i < VIS_N; ++i)
            vz.hann[i] = 0.25f - 0.25f * cosf(TAU * i / (VIS_N - 1)); /* with the mono mix's 1/2 */
    for (int i = 0; i < VIS_N; ++i) {
        vz.re[i] = (pcm[i][0] + pcm[i][1]) * vz.hann[i];
        vz.im[i] = 0;
    }
    fft(vz.re, vz.im, VIS_N);
    if (vz.bin_rate != rate) {
        for (int b = 0; b <= VIS_BANDS; ++b) vz.bin[b] = band_bin(b, rate);
        vz.bin_rate = rate;
    }
    for (int b = 0; b < VIS_BANDS; ++b) {
        int lo = vz.bin[b], hi = vz.bin[b + 1];
        float m = 0;
        for (int k = lo; k < (hi > lo ? hi : lo + 1) && k < VIS_N / 2; ++k) {
            float p = vz.re[k] * vz.re[k] + vz.im[k] * vz.im[k];
            if (p > m) m = p;
        }
        /* A full-scale sine peaks at N / 4 under the Hann window. */
        float v = m > 0 ? 1 + (float)decibels(m * 16 / ((float)VIS_N * VIS_N)) / VIS_FLOOR_DB : 0;
        level[b] = v < 0 ? 0 : v > 1 ? 1 : v;
    }
}

void vis_reset(void) {
    /* No fresh history: start at rest, including while paused. */
    memset(vz.level, 0, sizeof vz.level);
}

/* The bands' levels a tick on: each rises most of the way to its target at once and falls at
 * VIS_DECAY a second. */
void vis_analyze(unsigned rate, float dt) {
    float target[VIS_BANDS] = { 0 };
    if (rate) vis_bands(vz.pcm, rate, target);
    for (int b = 0; b < VIS_BANDS; ++b) {
        float fall = vz.level[b] - VIS_DECAY * dt;
        vz.level[b] = target[b] > vz.level[b] ? vz.level[b] + (target[b] - vz.level[b]) * 0.6f
                      : target[b] > fall      ? target[b]
                                              : fall;
    }
}

#ifndef PEQ_HOST
#include "playback.h"                   /* wheel_held */
#include "stock.h"                      /* system_bar */
void np_modes(void *win, void *canvas); /* np_bar.c: shuffle and repeat */

/* The slide_view is on this page. */
static int on_page(void) {
    return vz.page &&
           widget_get_prop_int(vz.slide, "value", 0) == (int)widget_count_children(vz.slide) - 1;
}

static int full(void);
/* Now Playing is on top, on this page, with the screen on; under OLED's full screen, also with the
 * wheel's volume over it (volume.c's band in the bar's place), so Field goes on under a turn. */
static int showing(void) {
    void *wm = window_manager(), *top = window_manager_get_top_window(wm);
    unsigned n = widget_count_children(wm);
    int vol = top && n > 1 && full() && widget_get_child(wm, n - 2) == vz.win &&
              !tk_strcmp(widget_get_prop_str(top, "name", ""), "volume_dialog");
    return on_page() && g_backlight_status && (top == vz.win || vol);
}

/* Showing and still: no window sliding, and the page last painted at the slide area's left edge, so
 * not dragged or mid-slide (value only changes once a slide ends); a tap leaves it there. */
static int settled(void) {
    return showing() && !window_manager_is_animating(window_manager()) && !vz.page_x;
}

/* OLED's Field fills the screen (docs/internals.md#visualizer-ipod): drawn over the whole of it,
 * the status row and the rest of the page included, which show over it, fading in over
 * CHROME_FADE_MS, only while a thumb rests on the wheel (wheel_held) and until CHROME_HOLD_MS after
 * it lifts, then fading out as fast. */
#define CHROME_FADE_MS 150
#define CHROME_HOLD_MS 1500
#define FULL_SX 1.55f /* Field's rings across the screen, from the page's 1.45 and 0.74 */
#define FULL_SY 1.25f
static int full(void) { return 1; } /* declared above */
int visualizer_full(void) { return full() && settled(); }
/* chrome a step on to now: 1 if it changed */
static int chrome_step(unsigned now) {
    int was = vz.chrome, step = (int)(now - vz.chrome_at) * 255 / CHROME_FADE_MS;
    vz.chrome_at = now;
    if (wheel_held) vz.lift = now;
    int up = wheel_held || now - vz.lift < CHROME_HOLD_MS;
    if (step < 1) step = 1;
    vz.chrome = up ? (was + step > 255 ? 255 : was + step) : (was - step < 0 ? 0 : was - step);
    return vz.chrome != was;
}
/* What a frame repaints: the slide area, or with full() the window and the status row over it. */
static void repaint(void) {
    widget_invalidate_force(full() ? vz.win : vz.slide, 0);
    void *bar = *(void *const *)system_bar;
    if (full() && bar) widget_invalidate_force(bar, 0);
}

/* The window and the status row whole, whatever the page shows. */
static void repaint_all(void) {
    void *bar = *(void *const *)system_bar;
    widget_invalidate_force(vz.win, 0);
    if (bar) widget_invalidate_force(bar, 0);
}

/* The tap, mapped read-only; demo makes the file whole under another name first (peq.h). */
static vis_tap *tap(void) {
    if (vz.tap) return vz.tap;
    int fd = open(VIS_FILE, 2); /* O_RDWR */
    if (fd < 0) {
        fd = open(VIS_FILE ".tmp", 0x302, 0644); /* O_RDWR | O_CREAT | O_TRUNC (MIPS) */
        if (fd < 0) return 0;
        if (ftruncate(fd, sizeof(vis_tap)) || rename(VIS_FILE ".tmp", VIS_FILE)) {
            close(fd);
            return 0;
        }
    }
    void *p = mmap(0, sizeof(vis_tap), 3, 1, fd, 0); /* PROT_READ | PROT_WRITE (want), MAP_SHARED */
    close(fd);
    return vz.tap = p == (void *)-1 ? 0 : p;
}

/* The VIS_N frames heard now, into vz.pcm: VIS_LATENCY_MS behind the tap's last write, moved on by
 * the time since it. Their rate, or 0 when nothing plays. */
static unsigned capture(void) {
    vis_tap *t = tap();
    if (!t || mclGetPlayStatus() != 2) return 0;
    ++t->want; /* keep the writer copying */
    unsigned seq = t->seq, rate = t->rate;
    long long age = now_ns() - t->stamp;
    /* nothing written lately (DSD, or the filter is off), or a stamp read torn mid-write: one frame
     * at rest */
    if (!rate || age < 0 || age > 1000000000LL) return 0;
    int lag = (int)(rate * (VIS_LATENCY_MS / 1000.0f - (int)age * 1e-9f));
    lag = lag < 0 ? 0 : lag > VIS_RING / 2 ? VIS_RING / 2 : lag; /* well clear of the next write */
    for (unsigned i = 0, at = seq - (unsigned)lag - VIS_N; i < VIS_N; ++i) {
        vz.pcm[i][0] = t->ring[(at + i) % VIS_RING][0];
        vz.pcm[i][1] = t->ring[(at + i) % VIS_RING][1];
    }
    return rate;
}

/* The timer repaints: audio in the last 1.5 s. ringnav_sleep keeps stock's pace while it does. */
int visualizer_live(void) { return vz.timer && time_now_ms() - vz.heard <= 1500; }

/* Each 1000 / VIS_FPS ms while showing: the levels rise fast and fall at VIS_DECAY a second. With
 * nothing playing they come to rest. */
static int tick(const void *unused) {
    if (!showing()) {
        vz.timer = 0;
        return 7; /* RET_REMOVE */
    }
    /* The tap lives in hciplayer's PEQ filter, which boot leaves out of the chain when PEQ is off:
     * the first tick (outside the paint) while playing puts it in, in bypass, as the editor's
     * switch does. */
    peq_chain();
    unsigned now = time_now_ms(), rate = capture();
    float dt = (now - vz.last) / 1000.0f;
    vz.last = now;
    if (dt > 0.1f) dt = 0.1f;
    if (rate || now - vz.heard <= 1500) vis_analyze(rate, dt);
    /* 1.5 s after the audio stops everything has come to rest: one repaint puts the track's name
     * under it, then none until it plays again. */
    if (rate) vz.heard = now;
    /* A finger resting on the page pauses nothing: under it the page drew only when something else
     * repainted, a frame a second (#303). A drag is the page off its place, which settled() sees.
     */
    int still = settled();
    if (still && vz.quiet != (now - vz.heard > 1500 && mclGetPlayStatus() != 2)) {
        vz.quiet = !vz.quiet; /* at rest: the track's name, or back */
        repaint();
    }
    if (still && full() && chrome_step(now)) repaint();
    if (!visualizer_live() || !still) return 8; /* RET_REPEAT */
    if (rate) vz.flow += dt;
    /* The slide_view: the page's own rect does not map to the screen inside it. */
    repaint();
    return 8; /* RET_REPEAT */
}

static void arm(void) {
    if (vz.timer || !showing()) return;
    vz.last = time_now_ms();
    if (vz.last - vz.heard > 1500) vis_reset();
    vz.chrome = 0, vz.chrome_at = vz.last,
    vz.lift = vz.last - CHROME_HOLD_MS; /* hidden until a thumb rests */
    vz.timer = timer_add(tick, 0, 1000 / VIS_FPS);
}

/* Field (after iTunes' Magnetosphere, OLED's SPEC-next 4): FIELD_DOTS soft glowing points in each
 * of five bands of the spectrum, flowing round the page's centre on rings that the band's level
 * swells and ripples, each band its own colour and way round. Every point is a sprite drawn as is
 * (canvas_draw_image, no scaling): a FIELD_PX glow made once per band and strength, so a frame
 * is 5 x FIELD_DOTS image blends of FIELD_PX squared pixels and no vector path. */
#define FIELD_DOTS 40
#define FIELD_PX 32
static const unsigned field_rgb[5] = { 0x3c78ff, 0x7850ff, 0x28c8ff, 0xff5ab4, 0x5affc8 };
static void field_sprites(void) {
    for (int b = 0; b < 5; ++b)
        for (int k = 0; k < 4; ++k) {
            if (vz.glow[b][k]) continue;
            void *bm = bitmap_create_ex(FIELD_PX, FIELD_PX, FIELD_PX * 4, 1 /* RGBA8888 */);
            unsigned *d = bm ? (unsigned *)bitmap_lock_buffer_for_write(bm) : 0;
            if (!d) {
                if (bm) bitmap_destroy(bm);
                return;
            }
            int stride = (int)(bitmap_get_line_length(bm) / 4);
            float strength = 0.35f + 0.65f * (float)k / 3;
            for (int y = 0; y < FIELD_PX; ++y)
                for (int x = 0; x < FIELD_PX; ++x) {
                    float dx = (x + 0.5f - FIELD_PX / 2.0f) / (FIELD_PX / 2.0f),
                          dy = (y + 0.5f - FIELD_PX / 2.0f) / (FIELD_PX / 2.0f),
                          r2 = dx * dx + dy * dy;
                    /* a soft halo to the edge, a bright core in its middle sixth (no expf in demo)
                     */
                    float q = 1 - r2, c = 1 - 9 * r2, halo = q * q * q, core = c > 0 ? c * c : 0,
                          a = strength * (0.5f * halo + core);
                    unsigned rgb = mix(field_rgb[b], 0xffffff, (int)(60 * core), 100);
                    unsigned alpha = r2 >= 1 ? 0 : a >= 1 ? 255 : (unsigned)(a * 255);
                    /* RGBA8888 in memory: r, g, b, a */
                    d[y * stride + x] =
                        alpha << 24 | (rgb & 255) << 16 | (rgb & 0xff00) | rgb >> 16;
                }
            bitmap_unlock_buffer(bm);
            vz.glow[b][k] = bm;
        }
}
static void field_paint(void *canvas, float cx, float cy, float sx, float sy) {
    field_sprites();
    float t0 = vz.flow;
    for (int b = 0; b < 5; ++b) {
        float level = 0;
        for (int i = b * VIS_BANDS / 5; i < (b + 1) * VIS_BANDS / 5; ++i) level += vz.level[i];
        level /= VIS_BANDS / 5;
        float swell = 0.25f + 0.75f * level, turn = (b & 1 ? -1 : 1) * (0.12f + 0.04f * b) * t0;
        int k = level > 0.6f ? 3 : level > 0.35f ? 2 : level > 0.12f ? 1 : 0;
        if (!vz.glow[b][k]) return;
        for (int i = 0; i < FIELD_DOTS; ++i) {
            float a = TAU * i / FIELD_DOTS + turn,
                  r = 34 + b * 13 + 24 * swell * sinf(3 * a + b + t0 * (0.5f + 0.1f * b)) +
                      5 * sinf(2.3f * i + b); /* each point a little off its ring */
            int x = (int)(cx + r * cosf(a) * sx) - FIELD_PX / 2,
                y = (int)(cy + r * sinf(a) * sy) - FIELD_PX / 2;
            int src[4] = { 0, 0, FIELD_PX, FIELD_PX }, dst[4] = { x, y, FIELD_PX, FIELD_PX };
            canvas_draw_image(canvas, vz.glow[b][k], src, dst);
        }
    }
}

/* A paint missed while not settled: once settled, the page paints again. */
static int look(const void *unused) {
    if (on_page() && !settled()) return 8; /* RET_REPEAT: an overlay can outlast one retry. */
    vz.retry = 0;
    if (settled()) repaint();
    return 0; /* a one-shot */
}

/* The track, title · artist, or the title alone, trimmed to fit, at alpha over the slide area's
 * foot (the canvas at the slide area). */
static void song_line(void *canvas, unsigned alpha) {
    void *tl = widget_lookup(vz.win, "scrlabel_title", 1),
         *al = widget_lookup(vz.win, "scrlabel_artist", 1);
    const unsigned *t = tl ? widget_get_text(tl) : 0, *a = al ? widget_get_text(al) : 0;
    unsigned text[160], n = 0, title;
    while (t && *t && n < 100) text[n++] = *t++;
    title = n;
    if (n && a && *a) text[n++] = ' ', text[n++] = 0xb7, text[n++] = ' ';
    while (a && *a && n < sizeof text / sizeof *text) text[n++] = *a++;
    int px = 14;
    canvas_set_font(canvas, theme_face(text, (int)n, &px), (unsigned)px); /* draw_centred's face */
    if (title && canvas_measure_text(canvas, text, n) > PAGE_W) n = title;
    while (n > 1 && canvas_measure_text(canvas, text, n) > PAGE_W) text[--n - 1] = 0x2026; /* … */
    int r[4] = { VIS_X, VIS_H - 22, PAGE_W, 20 };
    if (n) draw_centred(canvas, text, n, r, 14, rgba(0xaaaaaa, alpha));
}

/* OLED's full screen (full()) over the status row, which the window manager paints before Now
 * Playing, outside its window's clip: after the bar's own paint, black, Field's part there, then
 * the bar again at chrome's alpha over it. */
static void bar_paint(void *bar, void *canvas) {
    int bx = I(canvas, CANVAS_X), by = I(canvas, CANVAS_Y), old[4], clip[4];
    if (!clip_within(canvas, old, clip, bx, by, I(bar, W_W), I(bar, W_H))) return;
    canvas_set_clip_rect(canvas, clip);
    unsigned fill = (unsigned)I(P(canvas, CANVAS_LCD), LCD_FILL_COLOR);
    stock_canvas_set_fill_color(canvas, rgba(0, 255));
    canvas_fill_rect(canvas, 0, 0, I(bar, W_W), I(bar, W_H));
    stock_canvas_set_fill_color(canvas, fill);
    field_paint(canvas, 375 / 2.0f - I(bar, W_X), 320 / 2.0f - I(bar, W_Y), FULL_SX, FULL_SY);
    if (vz.chrome) {
        canvas_set_global_alpha(canvas, (unsigned)vz.chrome);
        I(canvas, CANVAS_X) = bx - I(bar, W_X), I(canvas, CANVAS_Y) = by - I(bar, W_Y);
        vz.in_bar = 1;
        widget_paint(bar, canvas);
        vz.in_bar = 0;
        I(canvas, CANVAS_X) = bx, I(canvas, CANVAS_Y) = by;
        canvas_set_global_alpha(canvas, 255);
    }
    canvas_set_clip_rect(canvas, old);
}

/* The rest of OLED's full screen, the canvas at the window: black, Field round the screen's centre,
 * then at chrome's alpha the progress bar, its times, the play modes, the page dots and the song's
 * line, as they sit, over it. */
static void full_paint(void *canvas) {
    int sx = I(canvas, CANVAS_X) - I(vz.win, W_X), sy = I(canvas, CANVAS_Y) - I(vz.win, W_Y),
        wx = I(canvas, CANVAS_X), wy = I(canvas, CANVAS_Y), old[4], clip[4];
    if (!clip_within(canvas, old, clip, sx, sy, 375, 320)) return;
    canvas_set_clip_rect(canvas, clip);
    I(canvas, CANVAS_X) = sx, I(canvas, CANVAS_Y) = sy;
    unsigned fill = (unsigned)I(P(canvas, CANVAS_LCD), LCD_FILL_COLOR);
    stock_canvas_set_fill_color(canvas, rgba(0, 255));
    canvas_fill_rect(canvas, 0, 0, 375, 320);
    stock_canvas_set_fill_color(canvas, fill);
    field_paint(canvas, 375 / 2.0f, 320 / 2.0f, FULL_SX, FULL_SY);
    if (vz.chrome) {
        canvas_set_global_alpha(canvas, (unsigned)vz.chrome);
        I(canvas, CANVAS_X) = wx, I(canvas, CANVAS_Y) = wy;
        static const char *const parts[] = { "slider_play", "label_playtime", "label_ipod_remain",
                                             "slide_indicator1" };
        for (unsigned i = 0; i < sizeof parts / sizeof *parts; ++i) {
            void *c = widget_lookup(vz.win, parts[i], 1);
            int dx = 0, dy = 0; /* its parent's place in the window */
            for (void *p = c ? P(c, W_PARENT) : 0; p && p != vz.win; p = P(p, W_PARENT))
                dx += I(p, W_X), dy += I(p, W_Y);
            if (!c) continue;
            I(canvas, CANVAS_X) = wx + dx, I(canvas, CANVAS_Y) = wy + dy;
            widget_paint(c, canvas);
        }
        I(canvas, CANVAS_X) = wx, I(canvas, CANVAS_Y) = wy;
        np_modes(vz.win, canvas);
        canvas_set_global_alpha(canvas, 255);
        I(canvas, CANVAS_X) = wx + vz.ox, I(canvas, CANVAS_Y) = wy + vz.oy;
        song_line(canvas, (unsigned)vz.chrome);
    }
    I(canvas, CANVAS_X) = wx, I(canvas, CANVAS_Y) = wy;
    canvas_set_clip_rect(canvas, old);
}

/* ringnav_paint, after stock, on Now Playing's window (after all its children): Field over the
 * slide area, and the track's name at its foot once at rest. Drawing on the window, clipped to the
 * slide area, not on the page: the slide_view clips the page it was given at run time to a sliver.
 * Painting starts the timer. */
void visualizer_paint(void *w, void *canvas) {
    if (!w) return;
    if (w == *(void *const *)system_bar) { /* painted before Now Playing, outside its window */
        if (!vz.in_bar && vz.win && visualizer_full()) bar_paint(w, canvas);
        return;
    }
    if (w == vz.page) { /* the page paints first, its canvas x 0 only when still */
        vz.page_x = I(canvas, CANVAS_X) - vz.ox;
        return;
    }
    if (w != vz.win || !P(canvas, CANVAS_LCD) || !on_page()) return;
    arm();
    if (!settled()) { /* under a closing window, sliding or dragged: paused, nothing else
                       * would paint it once that ends */
        if (!vz.retry) vz.retry = timer_add(look, 0, VIS_RETRY_MS);
        return;
    }
    if (full()) {
        chrome_step(time_now_ms());
        full_paint(canvas);
        return;
    }
    int x = vz.ox, y = vz.oy, old[4], clip[4];
    if (!clip_within(canvas, old, clip, I(canvas, CANVAS_X) + x, I(canvas, CANVAS_Y) + y, vz.w,
                     VIS_H))
        return;
    canvas_set_clip_rect(canvas, clip);
    I(canvas, CANVAS_X) += x; /* the slide area's origin */
    I(canvas, CANVAS_Y) += y;
    field_paint(canvas, vz.w / 2.0f, VIS_H / 2.0f, 1.45f, 0.74f);
    if (vz.quiet) song_line(canvas, 255);
    I(canvas, CANVAS_X) -= x;
    I(canvas, CANVAS_Y) -= y;
    canvas_set_clip_rect(canvas, old);
}

/* The slide_view's page changed. Under OLED's full screen, a slide off this page repaints the
 * window and the status row whole: Field covered all of it, and the slide repaints only the slide
 * area, so the rest (shuffle and repeat's row among it) kept Field's last frame. */
static int slid(void *ctx, void *event) {
    if (full() && vz.win && !on_page()) repaint_all();
    arm();
    return 0;
}

static int gone(void *win, void *event) {
    if (win == vz.win) {
        stop_timer(&vz.timer);
        stop_timer(&vz.retry);
        vz.win = vz.slide = vz.page = 0;
    }
    return 0;
}

/* ringnav_playing: the page joins the slide_view's art and lyrics pages, and its dots. */
void visualizer_attach(void *win) {
    void *slide = widget_lookup(win, "slide_view", 1),
         *dots = widget_lookup(win, "slide_indicator1", 1);
    if (!slide) return;
    vz.win = win;
    vz.slide = slide;
    vz.w = I(slide, W_W) > 0 && I(slide, W_W) <= 375 ? I(slide, W_W) : 375;
    /* the page starts where the slide's lyrics do */
    void *lrc = widget_lookup(slide, "view_lrc", 1);
    int top = lrc ? I(lrc, W_Y) : 0;
    vz.h = I(slide, W_H) - top < VIS_SLIDE_H ? I(slide, W_H) - top : VIS_SLIDE_H;
    vz.page = list_item_create(slide, 0, top, vz.w, VIS_H);
    widget_use_style(vz.page, "s_listitem_black");
    vz.ox = 0, vz.oy = top;
    for (void *p = slide; p && p != win; p = P(p, W_PARENT)) vz.ox += I(p, W_X), vz.oy += I(p, W_Y);
    if (dots) widget_set_prop_int(dots, "max", (int)widget_count_children(slide));
    widget_on(slide, EVT_VALUE_CHANGED, slid, 0);
    widget_on(win, EVT_DESTROY, gone, win);
}
#endif
