/* iPod Now Playing's fourth page (docs/ipod.md#visualizer): the music as heard, read from
 * hciplayer's tap (peq.h), in one of four styles after Rockbox's FFT, Oscilloscope and VU Meter
 * plugins, and under OLED a fifth, Field, after iTunes' Magnetosphere; a tap moves to the next
 * style. */
#include "peq.h"
#include "offsets.inc"

#define VIS_N 1024           /* frames each frame analyses */
#define VIS_HALO 64          /* log-spaced bands, 40 Hz to 16 kHz: the Halo's bars, pairs the spectrum's */
#define VIS_X 16             /* tools/ipod.py NP_MARGIN */
#define VIS_W (375 - 2 * VIS_X) /* the widest page's drawing, its buffers' size */
#define PAGE_W (vz.w - 2 * VIS_X) /* this page's: the slide's width, 375 or the centred page's 225 */
#define VIS_SLIDE_H 186      /* tools/ipod.py NP_SLIDE_H, the page's height, from the lyrics' top */
#define VIS_H (vz.h ? vz.h : VIS_SLIDE_H) /* ... or less where the slide ends sooner (OLED's np_grid) */
#define VIS_ART 88           /* the Halo's centre */
#define TAU 6.2831853f
enum { SPECTRUM, SCOPE, METERS, HALO, FIELD, STYLES }; /* FIELD: OLED's only (styles()) */

static struct {
    float re[VIS_N], im[VIS_N], pcm[VIS_N][2], hann[VIS_N]; /* hann[VIS_N / 2] is 0 until filled */
    float level[VIS_HALO], peak[VIS_BARS], fall[VIS_BARS], wave[2][VIS_W], vu[2], spin, bass;
    unsigned held[VIS_BARS], lit[2], bin_rate;
    int w, h; /* the page's width and height (visualizer_attach; h 0: VIS_SLIDE_H) */
    int bin[VIS_HALO + 1]; /* each band's first FFT bin at bin_rate */
#ifndef PEQ_HOST
    void *win, *slide, *page, *art, *cover;
    vis_tap *tap;
    unsigned timer, last, named, heard; /* heard: the last tick with audio */
    unsigned retry; /* a paint missed while not settled looks again (look) */
    int ox, oy, page_x; /* the slide area in the window; the page's canvas x there as last painted */
    int style;
    float flow; /* Field's clock, seconds of music, moved on while it plays */
    void *glow[5][4]; /* Field's sprites (field_sprites), each band's at four strengths; kept for
                       * demo's life: 5 x 4 x 32 x 32 x 4 bytes, 80 KB */
    int chrome;              /* OLED's full screen (full()): the page's other parts' alpha, 0-255 */
    unsigned chrome_at, lift; /* chrome's last step, and the last time a thumb rested on the wheel */
    int in_bar;               /* bar_paint is painting the status row again: its own paint is stock's */
    int quiet; /* at rest (not playing, nothing heard), the style's name gone, as last ticked: the
                * track's name shows, and its change repaints; the last word (test/patch.py finds it so) */
#endif
} vz __attribute__((section(".scratch")));

/* In-place radix-2 FFT of n complex values, n a power of two. */
static void fft(float *re, float *im, int n) {
    for (int i = 1, j = 0; i < n; ++i) {
        int bit = n >> 1;
        for (; j & bit; bit >>= 1) j ^= bit;
        j ^= bit;
        if (i < j) {
            float t = re[i]; re[i] = re[j]; re[j] = t;
            t = im[i]; im[i] = im[j]; im[j] = t;
        }
    }
    for (int len = 2; len <= n; len <<= 1) {
        float wr = cosf(TAU / len), wi = -sinf(TAU / len);
        for (int i = 0; i < n; i += len) {
            float cr = 1, ci = 0;
            for (int k = i; k < i + len / 2; ++k) {
                float tr = re[k + len / 2] * cr - im[k + len / 2] * ci, ti = re[k + len / 2] * ci + im[k + len / 2] * cr;
                re[k + len / 2] = re[k] - tr; im[k + len / 2] = im[k] - ti;
                re[k] += tr; im[k] += ti;
                float t = cr * wr - ci * wi;
                ci = cr * wi + ci * wr;
                cr = t;
            }
        }
    }
}

static int band_bin(int b, unsigned rate) { return (int)(40 * pow(400, (double)b / VIS_HALO) * VIS_N / rate + 0.5); }

/* level[VIS_HALO]: each band's loudest bin of the Hann-windowed mono mix of VIS_N frames at rate, 0
 * at VIS_FLOOR_DB below a full-scale sine to 1 at it. */
void vis_bands(const float (*pcm)[2], unsigned rate, float *level) {
    if (!vz.hann[VIS_N / 2]) /* the window, once */
        for (int i = 0; i < VIS_N; ++i) vz.hann[i] = 0.25f - 0.25f * cosf(TAU * i / (VIS_N - 1)); /* with the mono mix's 1/2 */
    for (int i = 0; i < VIS_N; ++i) {
        vz.re[i] = (pcm[i][0] + pcm[i][1]) * vz.hann[i];
        vz.im[i] = 0;
    }
    fft(vz.re, vz.im, VIS_N);
    if (vz.bin_rate != rate) {
        for (int b = 0; b <= VIS_HALO; ++b) vz.bin[b] = band_bin(b, rate);
        vz.bin_rate = rate;
    }
    for (int b = 0; b < VIS_HALO; ++b) {
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

static float bar(int i) { return (vz.level[2 * i] + vz.level[2 * i + 1]) / 2; }

/* VU position 0 to 1 of a level in VU (dB): proportional to voltage, +3 at the end, as the meter's scale. */
static float vu_at(float vu) { return (float)pow(10, (vu - 3) / 20); }

void vis_reset(void) {
    /* An inactive style has no fresh history; start it at rest, including while paused. */
    memset(vz.level, 0, sizeof vz.level);
    memset(vz.peak, 0, sizeof vz.peak);
    memset(vz.wave, 0, sizeof vz.wave);
    memset(vz.vu, 0, sizeof vz.vu);
    memset(vz.lit, 0, sizeof vz.lit);
    vz.bass = 0;
}

/* Only the visible style consumes analysis work; scope and meters do not need an FFT. */
void vis_analyze(int style, unsigned rate, float dt, unsigned now) {
    float target[VIS_HALO] = { 0 };
    if (style == SPECTRUM || style == HALO || style == FIELD) {
        if (rate) vis_bands(vz.pcm, rate, target);
        for (int b = 0; b < VIS_HALO; ++b) {
            float fall = vz.level[b] - VIS_DECAY * dt;
            vz.level[b] = target[b] > vz.level[b] ? vz.level[b] + (target[b] - vz.level[b]) * 0.6f : target[b] > fall ? target[b] : fall;
        }
    }
    if (style == SPECTRUM) for (int i = 0; i < VIS_BARS; ++i) {
        if (bar(i) >= vz.peak[i]) {
            vz.peak[i] = bar(i);
            vz.fall[i] = 0;
            vz.held[i] = now + VIS_HOLD_MS;
        } else if ((int)(now - vz.held[i]) > 0) {
            vz.fall[i] += VIS_GRAVITY * dt;
            vz.peak[i] -= vz.fall[i] * dt;
            if (vz.peak[i] < bar(i)) vz.peak[i] = bar(i);
        }
    }
    if (style == SCOPE) {
        int at = 0;
        for (int i = 1; rate && i < VIS_N - VIS_W && !at; ++i)
            if (vz.pcm[i - 1][0] + vz.pcm[i - 1][1] < 0 && vz.pcm[i][0] + vz.pcm[i][1] >= 0) at = i;
        for (int ch = 0; ch < 2; ++ch)
            for (int i = 0; i < VIS_W; ++i) vz.wave[ch][i] = rate ? vz.pcm[at + i][ch] : vz.wave[ch][i] * 0.8f;
    }
    if (style == METERS) for (int ch = 0; ch < 2; ++ch) {
        float sum = 0, peak = 0;
        for (int i = 0; rate && i < VIS_N; ++i) {
            float v = vz.pcm[i][ch];
            sum += v * v;
            if (__builtin_fabsf(v) > peak) peak = __builtin_fabsf(v);
        }
        float to = sum > 0 ? vu_at((float)decibels(sum / VIS_N) - VIS_VU_REF_DB) : 0;
        vz.vu[ch] += ((to > 1 ? 1 : to) - vz.vu[ch]) * dt / (dt + 0.065f);
        if (peak >= (float)pow(10, VIS_LED_DB / 20.0)) vz.lit[ch] = now + VIS_HOLD_MS;
    }
    if (style == HALO) {
        float bass = (vz.level[0] + vz.level[2] + vz.level[4] + vz.level[6]) / 4;
        vz.bass += (bass - vz.bass) * 0.5f;
    }
}

#ifndef PEQ_HOST
#include "playback.h" /* wheel_held */
#include "stock.h"    /* system_bar */
void np_modes(void *win, void *canvas); /* np_bar.c: shuffle and repeat */
/* The accent's palette: its light tone, a dark shade of it and a bright tint, never washed to white. */
#define DARK(tone) mix(tone, 0, 55, 100)
#define BRIGHT(tone) mix(tone, 0xffffff, 30, 100)

/* The slide_view is on this page. */
static int on_page(void) {
    return vz.page && widget_get_prop_int(vz.slide, "value", 0) == (int)widget_count_children(vz.slide) - 1;
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
static int full(void) { return oled_pages() && vz.style == FIELD; } /* declared above */
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

/* The Halo's centre shows Now Playing's own art, in the same image, so stock's reload of the cover
 * reaches both; it shows only while the Halo draws round it. */
static void halo_art(int on) {
    if (!vz.art) return;
    on = on && vz.style == HALO;
    if (widget_get_visible(vz.art) != on) widget_set_visible(vz.art, on, 0);
    const char *src = vz.cover ? widget_get_prop_str(vz.cover, "image", "") : "";
    if (on && tk_strcmp(src, widget_get_prop_str(vz.art, "image", ""))) {
        image_set_draw_type(vz.art, widget_get_prop_int(vz.cover, "draw_type", IMAGE_DRAW_SCALE_DOWN));
        image_base_set_image(vz.art, src);
    }
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
    /* nothing written lately (DSD, or the filter is off), or a stamp read torn mid-write: one frame at rest */
    if (!rate || age < 0 || age > 1000000000LL) return 0;
    int lag = (int)(rate * (VIS_LATENCY_MS / 1000.0f - (int)age * 1e-9f));
    lag = lag < 0 ? 0 : lag > VIS_RING / 2 ? VIS_RING / 2 : lag; /* well clear of the next write */
    for (unsigned i = 0, at = seq - (unsigned)lag - VIS_N; i < VIS_N; ++i) {
        vz.pcm[i][0] = t->ring[(at + i) % VIS_RING][0];
        vz.pcm[i][1] = t->ring[(at + i) % VIS_RING][1];
    }
    return rate;
}

/* The timer repaints: audio in the last 1.5 s or a style name still fading. ringnav_sleep keeps
 * stock's pace while it does. */
int visualizer_live(void) {
    unsigned now = time_now_ms();
    return vz.timer && (now - vz.heard <= 1500 || now - vz.named <= 1500);
}

/* Each 1000 / VIS_FPS ms while showing: the levels rise fast and fall at VIS_DECAY a second, a cap
 * holds VIS_HOLD_MS over each spectrum bar's peak, then falls under VIS_GRAVITY; the scope starts on a
 * rising zero crossing so the wave stands still; the VU needles integrate over 300 ms. With nothing
 * playing every one comes to rest. */
static int tick(const void *unused) {
    if (!showing()) {
        halo_art(0);
        vz.timer = 0;
        return 7; /* RET_REMOVE */
    }
    /* The tap lives in hciplayer's PEQ filter, which boot leaves out of the chain when PEQ is off: the
     * first tick (outside the paint) while playing puts it in, in bypass, as the editor's switch does. */
    peq_chain();
    unsigned now = time_now_ms(), rate = capture();
    float dt = (now - vz.last) / 1000.0f;
    vz.last = now;
    if (dt > 0.1f) dt = 0.1f;
    if (rate || now - vz.heard <= 1500) vis_analyze(vz.style, rate, dt, now);
    /* 1.5 s after the audio stops everything has come to rest (a cap's hold and fall, the wave's fade)
     * and the style's name has faded: one repaint puts the track's name under it, then none until it
     * plays again. */
    if (rate) vz.heard = now;
    /* A finger resting on the page pauses nothing: under it the page drew only when something else
     * repainted, a frame a second (#303). A drag is the page off its place, which settled() sees. */
    int still = settled();
    halo_art(still);
    if (still && vz.quiet != (now - vz.heard > 1500 && now - vz.named > 1500 && mclGetPlayStatus() != 2)) {
        vz.quiet = !vz.quiet; /* at rest, the style's name gone: the track's name, or back */
        repaint();
    }
    if (still && full() && chrome_step(now)) repaint();
    if (!visualizer_live() || !still) return 8; /* RET_REPEAT */
    vz.spin += dt * 0.2f; /* radians a second */
    if (rate) vz.flow += dt;
    /* The slide_view: the page's own rect does not map to the screen inside it. */
    repaint();
    return 8; /* RET_REPEAT */
}

static void arm(void) {
    if (vz.timer || !showing()) return;
    vz.last = time_now_ms();
    if (vz.last - vz.heard > 1500) vis_reset();
    vz.chrome = 0, vz.chrome_at = vz.last, vz.lift = vz.last - CHROME_HOLD_MS; /* hidden until a thumb rests */
    vz.timer = timer_add(tick, 0, 1000 / VIS_FPS);
}

static void line(void *vg, float x0, float y0, float x1, float y1) {
    vgcanvas_begin_path(vg);
    vgcanvas_move_to(vg, x0, y0);
    vgcanvas_line_to(vg, x1, y1);
    vgcanvas_stroke(vg);
}

/* A rounded rectangle as a sub-path of the current path. Stock vgcanvas_rounded_rect begins a new
 * path itself, so in one path of 32 bars only the last survived; its arcs and lines do not. */
static void capsule(void *vg, float x, float y, float w, float h, float r) {
    if (r > h / 2) r = h / 2;
    vgcanvas_move_to(vg, x, y + r);
    vgcanvas_arc(vg, x + r, y + r, r, TAU / 2, TAU * 3 / 4, 0);
    vgcanvas_arc(vg, x + w - r, y + r, r, TAU * 3 / 4, TAU, 0);
    vgcanvas_arc(vg, x + w - r, y + h - r, r, 0, TAU / 4, 0);
    vgcanvas_arc(vg, x + r, y + h - r, r, TAU / 4, TAU / 2, 0);
    vgcanvas_close_path(vg);
}

static void disc(void *vg, float x, float y, float r, unsigned color) {
    vgcanvas_begin_path(vg);
    vgcanvas_arc(vg, x, y, r, 0, TAU, 0);
    vgcanvas_set_fill_color(vg, color);
    vgcanvas_fill(vg);
}

/* Rockbox FFT's log bars, Apple's finish: capsules in a gradient from the accent's dark shade to its
 * bright tint at full height, a cap on each peak, and a faint reflection under the baseline. */
#define BASE 142
#define TALL 126
static void spectrum(void *vg, unsigned tone) {
    float pitch = (float)PAGE_W / VIS_BARS, w = pitch * 0.66f;
    /* Three fills for all the bars: the bars (one gradient, in page coordinates, serves them all),
     * their reflections and their caps. A fill each costs a frame. */
    for (int k = 0; k < 3; ++k) {
        vgcanvas_begin_path(vg);
        for (int i = 0; i < VIS_BARS; ++i) {
            float x = VIS_X + i * pitch + (pitch - w) / 2, h = 3 + bar(i) * (TALL - 3);
            if (!k) capsule(vg, x, BASE - h, w, h, w / 2);
            else if (k == 1) capsule(vg, x, BASE + 4, w, h * 0.3f, w / 2);
            else capsule(vg, x, BASE - 3 - vz.peak[i] * (TALL - 3) - 4, w, 3, 1.5f);
        }
        if (!k) vgcanvas_set_fill_linear_gradient(vg, 0, BASE, 0, BASE - TALL, rgba(DARK(tone), 255), rgba(BRIGHT(tone), 255));
        else vgcanvas_set_fill_color(vg, rgba(k == 1 ? tone : BRIGHT(tone), k == 1 ? 0x24 : 255));
        vgcanvas_fill(vg);
    }
}

/* Rockbox's oscilloscope: the right channel a faint accent line, the left over it with a glow, a wide
 * faint stroke under a fine bright one; a point every 2 px, which the anti-aliasing hides. */
static void scope(void *vg, unsigned tone) {
    static const struct { float width; unsigned alpha; } pass[] = { { 1.5f, 0x50 }, { 5, 0x40 }, { 1.5f, 0xff } };
    for (int k = 0; k < 3; ++k) {
        const float *w = vz.wave[!k ? 1 : 0];
        vgcanvas_begin_path(vg);
        for (int x = 0; x < PAGE_W; x += 2) {
            float y = VIS_H / 2 + (k ? 0 : 3) - w[x] * (VIS_H / 2 - 16);
            if (x) vgcanvas_line_to(vg, VIS_X + x, y);
            else vgcanvas_move_to(vg, VIS_X, y);
        }
        vgcanvas_set_line_width(vg, pass[k].width);
        vgcanvas_set_stroke_color(vg, rgba(k == 2 ? BRIGHT(tone) : tone, pass[k].alpha));
        vgcanvas_stroke(vg);
    }
}

/* Rockbox's analog VU meters, left and right, in the accent's palette: an arc from -20 to +3 VU,
 * heavier and brighter past 0, ticks and labels, the needle on its pivot and a peak LED. */
static void meters(void *vg, void *canvas, unsigned tone, unsigned now) {
    static const signed char marks[] = { -20, -10, -7, -5, -3, -2, -1, 0, 1, 2, 3 };
    static const char *const labels[] = { "-20", "-10", "", "-5", "-3", "", "", "0", "", "", "+3" };
    /* 80 degrees of arc: the end labels of the two meters stay apart, and the face centres on the page. */
    const float r = 94.0f * PAGE_W / VIS_W, py = 150, spread = 1.4f; /* narrower, smaller */
    for (int ch = 0; ch < 2; ++ch) {
        float cx = VIS_X + (PAGE_W / 4.0f) * (1 + 2 * ch), zero = -TAU / 4 - spread / 2;
        vgcanvas_set_line_width(vg, 2);
        vgcanvas_begin_path(vg);
        vgcanvas_arc(vg, cx, py, r, zero, zero + spread * vu_at(0), 0);
        vgcanvas_set_stroke_color(vg, rgba(tone, 255));
        vgcanvas_stroke(vg);
        vgcanvas_set_line_width(vg, 3);
        vgcanvas_begin_path(vg);
        vgcanvas_arc(vg, cx, py, r, zero + spread * vu_at(0), zero + spread, 0);
        vgcanvas_set_stroke_color(vg, rgba(BRIGHT(tone), 255));
        vgcanvas_stroke(vg);
        vgcanvas_set_line_width(vg, 1.5f);
        for (int over = 0; over < 2; ++over) { /* the ticks up to 0 VU, then past it: a stroke each */
            vgcanvas_begin_path(vg);
            for (unsigned i = 0; i < sizeof marks; ++i) {
                float a = zero + spread * vu_at(marks[i]), c = cosf(a), s = sinf(a), out = labels[i][0] ? 8 : 5;
                if ((marks[i] > 0) != over) continue;
                vgcanvas_move_to(vg, cx + c * r, py + s * r);
                vgcanvas_line_to(vg, cx + c * (r + out), py + s * (r + out));
            }
            vgcanvas_set_stroke_color(vg, rgba(over ? BRIGHT(tone) : tone, 255));
            vgcanvas_stroke(vg);
        }
        for (unsigned i = 0; i < sizeof marks; ++i) { /* text after the strokes: it may reset the path */
            float a = zero + spread * vu_at(marks[i]);
            if (labels[i][0]) caption(canvas, labels[i], (int)(cx + cosf(a) * (r + 16)) - 14, (int)(py + sinf(a) * (r + 16)) - 8, 28, 16, 12, CF_GREY);
        }
        caption(canvas, ch ? "R" : "L", (int)cx - 48, (int)py - 20, 24, 20, 14, CF_GREY); /* clear of the needle */
        float a = zero + spread * vz.vu[ch];
        vgcanvas_set_line_width(vg, 2);
        vgcanvas_set_stroke_color(vg, rgba(BRIGHT(tone), 255));
        line(vg, cx, py, cx + cosf(a) * (r + 4), py + sinf(a) * (r + 4));
        disc(vg, cx, py, 7, rgba(tone, 255));
        disc(vg, cx, py, 5, rgba(DARK(tone), 255));
        disc(vg, cx + r * 0.62f, py - r * 0.62f - 8, 4, rgba((int)(vz.lit[ch] - now) > 0 ? BRIGHT(tone) : DARK(DARK(tone)), 255));
    }
}

/* Apple's radial spectrum: VIS_HALO bars, mirrored left and right, radiating from Now Playing's art
 * in a circle, turning slowly, with the bass pulsing the ring. */
static void halo(void *vg, unsigned tone) {
    float cx = vz.w / 2.0f, cy = VIS_H / 2.0f, art = VIS_ART / 2.0f, r0 = art + 7 + 5 * vz.bass;
    vgcanvas_set_line_width(vg, art * 0.42f + 2); /* round the art: black over its corners */
    vgcanvas_begin_path(vg);
    vgcanvas_arc(vg, cx, cy, art * 1.21f + 1, 0, TAU, 0);
    vgcanvas_set_stroke_color(vg, rgba(0, 255));
    vgcanvas_stroke(vg);
    vgcanvas_set_line_width(vg, 2);
    vgcanvas_begin_path(vg);
    vgcanvas_arc(vg, cx, cy, art + 3, 0, TAU, 0);
    vgcanvas_set_stroke_color(vg, rgba(tone, 0x50 + (unsigned)(0xaf * vz.bass)));
    vgcanvas_stroke(vg);
    vgcanvas_set_line_cap(vg, "round");
    vgcanvas_set_line_width(vg, 3);
    for (int level = 0; level < 6; ++level) { /* a stroke per shade, its bars all in one path */
        vgcanvas_begin_path(vg);
        for (int i = 0; i < VIS_HALO; ++i) {
            float v = bar(i < VIS_HALO / 2 ? i : VIS_HALO - 1 - i), a = vz.spin + TAU * i / VIS_HALO;
            if ((v >= 1 ? 5 : (int)(v * 6)) != level) continue;
            float c = cosf(a), s = sinf(a), len = 3 + v * (cy - r0 - 6);
            vgcanvas_move_to(vg, cx + c * r0, cy + s * r0);
            vgcanvas_line_to(vg, cx + c * (r0 + len), cy + s * (r0 + len));
        }
        vgcanvas_set_stroke_color(vg, rgba(mix(DARK(tone), BRIGHT(tone), 35 + 65 * (2 * level + 1) / 12, 100), 255));
        vgcanvas_stroke(vg);
    }
    vgcanvas_set_line_cap(vg, "butt");
}

/* Field (OLED's, after iTunes' Magnetosphere, SPEC-next 4): FIELD_DOTS soft glowing points in each
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
                          dy = (y + 0.5f - FIELD_PX / 2.0f) / (FIELD_PX / 2.0f), r2 = dx * dx + dy * dy;
                    /* a soft halo to the edge, a bright core in its middle sixth (no expf in demo) */
                    float q = 1 - r2, c = 1 - 9 * r2, halo = q * q * q, core = c > 0 ? c * c : 0,
                          a = strength * (0.5f * halo + core);
                    unsigned rgb = mix(field_rgb[b], 0xffffff, (int)(60 * core), 100);
                    unsigned alpha = r2 >= 1 ? 0 : a >= 1 ? 255 : (unsigned)(a * 255);
                    /* RGBA8888 in memory: r, g, b, a */
                    d[y * stride + x] = alpha << 24 | (rgb & 255) << 16 | (rgb & 0xff00) | rgb >> 16;
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
        for (int i = b * VIS_HALO / 5; i < (b + 1) * VIS_HALO / 5; ++i) level += vz.level[i];
        level /= VIS_HALO / 5;
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

/* The styles a tap steps through: Field only under OLED. */
static int styles(void) { return oled_pages() ? STYLES : FIELD; }

/* A paint missed while not settled: once settled, the page paints again. */
static int look(const void *unused) {
    if (on_page() && !settled()) return 8; /* RET_REPEAT: an overlay can outlast one retry. */
    vz.retry = 0;
    if (settled()) repaint();
    return 0; /* a one-shot */
}

/* The track, title · artist, or the title alone, trimmed to fit, at alpha over the slide area's foot
 * (the canvas at the slide area). */
static void song_line(void *canvas, unsigned alpha) {
    void *tl = widget_lookup(vz.win, "scrlabel_title", 1), *al = widget_lookup(vz.win, "scrlabel_artist", 1);
    const unsigned *t = tl ? widget_get_text(tl) : 0, *a = al ? widget_get_text(al) : 0;
    unsigned text[160], n = 0, title;
    while (t && *t && n < 100) text[n++] = *t++;
    title = n;
    if (n && a && *a) text[n++] = ' ', text[n++] = 0xb7, text[n++] = ' ';
    while (a && *a && n < sizeof text / sizeof *text) text[n++] = *a++;
    canvas_set_font(canvas, (void *)0, 14); /* draw_centred's */
    if (title && canvas_measure_text(canvas, text, n) > PAGE_W) n = title;
    while (n > 1 && canvas_measure_text(canvas, text, n) > PAGE_W) text[--n - 1] = 0x2026; /* … */
    int r[4] = { VIS_X, VIS_H - 22, PAGE_W, 20 };
    if (n) draw_centred(canvas, text, n, r, 14, rgba(0xaaaaaa, alpha));
}

/* OLED's full screen (full()) over the status row, which the window manager paints before Now
 * Playing, outside its window's clip: after the bar's own paint, black, Field's part there, then the
 * bar again at chrome's alpha over it. */
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
            for (void *p = c ? P(c, W_PARENT) : 0; p && p != vz.win; p = P(p, W_PARENT)) dx += I(p, W_X), dy += I(p, W_Y);
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

/* ringnav_paint, after stock, on Now Playing's window (after all its children): the current style
 * over the slide area, and for 1.5 s after a change the style's name, fading over its last half
 * second. Drawing on the window, clipped to the slide area, not on the page: the slide_view clips the
 * page it was given at run time to a sliver. Painting starts the timer. */
void visualizer_paint(void *w, void *canvas) {
    static const char *const names[] = { "Spectrum", "Oscilloscope", "VU Meters", "Halo", "Field" };
    if (!w) return;
    if (w == *(void *const *)system_bar) { /* painted before Now Playing, outside its window */
        if (!vz.in_bar && vz.win && visualizer_full()) bar_paint(w, canvas);
        return;
    }
    if (w == vz.page) { /* the page paints first, its canvas x 0 only when still */
        vz.page_x = I(canvas, CANVAS_X) - vz.ox;
        if (vz.page_x) halo_art(0); /* the art is the window's, painted after the page: off this frame */
        return;
    }
    if (w != vz.win || !P(canvas, CANVAS_LCD) || !on_page()) return;
    arm();
    if (!settled()) { /* under a closing window (the lock screen), sliding or dragged: paused, nothing
                       * else would paint it once that ends */
        if (!vz.retry) vz.retry = timer_add(look, 0, VIS_RETRY_MS);
        return;
    }
    unsigned now = time_now_ms();
    if (full()) {
        chrome_step(now);
        full_paint(canvas);
        unsigned shown = now - vz.named; /* the style's name, over all */
        if (vz.named && shown < 1500) {
            I(canvas, CANVAS_X) += vz.ox, I(canvas, CANVAS_Y) += vz.oy;
            caption(canvas, names[vz.style], 0, VIS_H - 22, vz.w, 20, 14,
                    rgba(0xaaaaaa, shown < 1000 ? 255 : 255 * (1500 - shown) / 500));
            I(canvas, CANVAS_X) -= vz.ox, I(canvas, CANVAS_Y) -= vz.oy;
        }
        return;
    }
    int x = vz.ox, y = vz.oy, old[4], clip[4];
    if (!clip_within(canvas, old, clip, I(canvas, CANVAS_X) + x, I(canvas, CANVAS_Y) + y, vz.w, VIS_H))
        return;
    canvas_set_clip_rect(canvas, clip);
    I(canvas, CANVAS_X) += x; /* the slide area's origin, for the vector drawing and the text alike */
    I(canvas, CANVAS_Y) += y;
    void *vg = canvas_get_vgcanvas(canvas);
    unsigned tone = accent_tone(2);
    if (vz.style == FIELD)
        field_paint(canvas, vz.w / 2.0f, VIS_H / 2.0f, 1.45f, 0.74f);
    else if (vg) {
        vgcanvas_save(vg);
        vgcanvas_translate(vg, (float)I(canvas, CANVAS_X), (float)I(canvas, CANVAS_Y));
        if (vz.style == SPECTRUM) spectrum(vg, tone);
        else if (vz.style == SCOPE) scope(vg, tone);
        else if (vz.style == METERS) meters(vg, canvas, tone, now);
        else halo(vg, tone);
        vgcanvas_restore(vg);
    }
    unsigned shown = now - vz.named;
    if (vz.named && shown < 1500)
        caption(canvas, names[vz.style], 0, VIS_H - 22, vz.w, 20, 14,
             rgba(0xaaaaaa, shown < 1000 ? 255 : 255 * (1500 - shown) / 500));
    else if (vz.quiet)
        song_line(canvas, 255);
    I(canvas, CANVAS_X) -= x;
    I(canvas, CANVAS_Y) -= y;
    canvas_set_clip_rect(canvas, old);
}

static int next_style(void *ctx, void *event) {
    vz.style = (vz.style + 1) % styles();
    vis_reset();
    vz.named = time_now_ms() | 1; /* never 0, which is none */
    write_int_config(vz.style, "IPOD", "VIS");
    repaint();
    return 0;
}

static int slid(void *ctx, void *event) {
    arm();
    return 0;
}

static int gone(void *win, void *event) {
    if (win == vz.win) {
        stop_timer(&vz.timer);
        stop_timer(&vz.retry);
        vz.win = vz.slide = vz.page = vz.art = vz.cover = 0;
    }
    return 0;
}

/* ringnav_playing: the page joins the slide_view's art, lyrics and info pages, and its dots. */
void visualizer_attach(void *win) {
    void *slide = widget_lookup(win, "slide_view", 1), *dots = widget_lookup(win, "slide_indicator1", 1);
    if (!slide) return;
    /* OLED opens on Field until a tap picks another; another theme never shows it */
    vz.style = config_value("IPOD", "VIS", STYLES, oled_pages() ? FIELD : 0);
    if (vz.style >= styles()) vz.style = 0;
    vz.win = win;
    vz.slide = slide;
    vz.cover = widget_lookup(win, "img_cover", 1);
    vz.w = I(slide, W_W) > 0 && I(slide, W_W) <= 375 ? I(slide, W_W) : 375;
    /* Classic's slide starts higher, its lyrics down it as far (tools/ipod.py NP_IPOD_LIFT): the
     * page starts where they do, so it stays where the others have it on screen */
    void *lrc = widget_lookup(slide, "view_lrc", 1);
    int top = lrc ? I(lrc, W_Y) : 0;
    vz.h = I(slide, W_H) - top < VIS_SLIDE_H ? I(slide, W_H) - top : VIS_SLIDE_H;
    vz.page = list_item_create(slide, 0, top, vz.w, VIS_H);
    widget_use_style(vz.page, "s_listitem_black");
    widget_on(vz.page, EVT_CLICK, next_style, 0);
    /* The art is the window's child, over the slide area's centre, for the same reason as the drawing. */
    vz.ox = 0, vz.oy = top;
    for (void *p = slide; p && p != win; p = P(p, W_PARENT)) vz.ox += I(p, W_X), vz.oy += I(p, W_Y);
    vz.art = image_create(win, vz.ox + (vz.w - VIS_ART) / 2, vz.oy + (VIS_H - VIS_ART) / 2, VIS_ART, VIS_ART);
    widget_set_sensitive(vz.art, 0);
    widget_set_visible(vz.art, 0, 0);
    if (dots) widget_set_prop_int(dots, "max", (int)widget_count_children(slide));
    widget_on(slide, EVT_VALUE_CHANGED, slid, 0);
    widget_on(win, EVT_DESTROY, gone, win);
}
#endif
