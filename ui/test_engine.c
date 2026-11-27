/* The toolkit's own checks, on this machine with no panel: built from ui/ less main.c by
 * `python3 tools/ui.py test` and run by test/ui.py. Each prints ok or FAIL and what it saw; the
 * exit code is the number that failed. */
#include <math.h>
#include <signal.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/wait.h>
#include <unistd.h>

#include "px.h"
#include "q2ui.h"

static int failed;
static void check(const char *what, int ok, const char *fmt, ...) {
    printf("%s %s", ok ? "ok  " : "FAIL", what);
    if (!ok) {
        va_list ap;
        printf(": ");
        va_start(ap, fmt);
        vprintf(fmt, ap);
        va_end(ap);
        ++failed;
    }
    printf("\n");
}
static uint32_t at(int x, int y) { return canvas[y * SCREEN_W + x] & 0xffffff; }
static void all(void) { clip_set((rect_t){ 0, 0, SCREEN_W, SCREEN_H }); }

/* a spring stepped once over a time lands where many small steps over it do, and a new target
 * mid-way keeps its speed */
static void springs(void) {
    for (int under = 0; under < 2; ++under) {
        float damp = under ? 0.7f : 1.0f;
        spring_t a = { 0, 0, 32 }, b = a;
        spring_step(&a, 0.1f, 0.2f, damp);
        for (int i = 0; i < 100; ++i) spring_step(&b, 0.001f, 0.2f, damp);
        check(under ? "an underdamped spring is the same in one step or a hundred"
                    : "a spring is the same in one step or a hundred",
              fabs(a.x - b.x) < 0.02 && fabs(a.v - b.v) < 1.0, "%f %f against %f %f", a.x, a.v, b.x, b.v);
    }
    spring_t s = { 0, 0, 32 };
    double last = 0;
    int mono = 1, frames = 0;
    while (spring_step(&s, 1 / 60.0f, 0.2f, 1) && frames < 200) mono &= s.x > last && s.x <= 32, last = s.x, ++frames;
    check("a critically damped spring neither turns back nor passes its target", mono && s.x == 32, "x %f", s.x);
    check("it is at rest in 8 to 24 frames of a 0.2 s response", frames >= 8 && frames <= 24, "%d frames", frames);
    spring_t r = { 0, 0, 32 };
    spring_step(&r, 0.05f, 0.2f, 1);
    double v = r.v, x = r.x;
    r.to = 64;
    spring_step(&r, 0.001f, 0.2f, 1);
    check("a new target keeps the place and the speed", fabs(r.x - x) < 0.5 && r.v > v * 0.9, "x %f to %f, v %f to %f", x, r.x, v, r.v);
    /* a long list: 9,000 rows down, a spring still comes to rest on its row */
    spring_t far = { 288000.0 - 20, 0, 288000.0 };
    int steps = 0;
    while (spring_step(&far, 1 / 60.0f, 0.2f, 1) && steps < 600) ++steps;
    check("a spring 288,000 px down a list comes to rest", steps < 60 && far.x == 288000.0, "%d frames, at %f", steps, far.x);
    spring_t u = { 0, 0, 10 };
    double peak = 0;
    for (int i = 0; i < 120; ++i) spring_step(&u, 1 / 60.0f, 0.28f, 0.55f), peak = u.x > peak ? u.x : peak;
    check("an underdamped spring passes its target once and comes to rest on it", peak > 10.2 && u.x == 10, "peak %f, end %f", peak, u.x);
}

/* an edge between pixels is half of each, on any background, and nothing darker or of another
 * hue appears along it */
static void edges(void) {
    all();
    fill((rect_t){ 0, 0, SCREEN_W, SCREEN_H }, 0x804020);
    fill_round((rectf_t){ 10.5f, 10.5f, 40, 20 }, 0, 0, 0xffffff, 1, 0);
    uint32_t e = at(10, 20), in = at(30, 20), out = at(8, 20);
    int half = abs((int)(e >> 16) - (0x80 + 0xff) / 2) <= 2 && abs((int)(e >> 8 & 0xff) - (0x40 + 0xff) / 2) <= 2 &&
               abs((int)(e & 0xff) - (0x20 + 0xff) / 2) <= 2;
    check("a box edge half over a pixel is half the box and half what was there", half && in == 0xffffff && out == 0x804020,
          "edge %06x inside %06x outside %06x", e, in, out);
    fill((rect_t){ 0, 0, SCREEN_W, SCREEN_H }, 0xd02020);
    fill_round((rectf_t){ 100.3f, 100.6f, 60, 30 }, 8, 0, 0x2160d8, 1, 0);
    int fringe = 0, partial = 0;
    for (int y = 98; y < 134; ++y)
        for (int x = 98; x < 164; ++x) {
            uint32_t p = at(x, y);
            int r = (int)(p >> 16), g = (int)(p >> 8 & 0xff), b = (int)(p & 0xff);
            /* every channel between the background's and the box's */
            fringe += r < 0x21 - 1 || r > 0xd0 + 1 || g < 0x20 - 1 || g > 0x60 + 1 || b < 0x20 - 1 || b > 0xd8 + 1;
            partial += p != 0xd02020 && p != 0x2160d8;
        }
    check("a rounded box on red has smoothed pixels and none outside the two colours' range", !fringe && partial > 60,
          "%d out of range, %d smoothed", fringe, partial);
    /* its area is its geometry's: 60 x 30 less the corners' squares plus their quarter discs */
    fill((rect_t){ 0, 0, SCREEN_W, SCREEN_H }, 0);
    fill_round((rectf_t){ 100.3f, 100.6f, 60, 30 }, 8, 0, 0xffffff, 1, 0);
    double sum = 0, want = 60 * 30 - (4 - 3.14159265) * 8 * 8;
    for (int y = 90; y < 140; ++y)
        for (int x = 90; x < 170; ++x) sum += (at(x, y) & 0xff) / 255.0;
    check("a rounded box at a sub-pixel place covers its own area", fabs(sum - want) < want * 0.01, "%.1f px for %.1f", sum, want);
    /* leaning, the edge away from the eye is the shorter */
    fill((rect_t){ 0, 0, SCREEN_W, SCREEN_H }, 0);
    fill_round((rectf_t){ 20, 200, 300, 40 }, 0, 0.2f, 0xffffff, 1, 0);
    double top = 0, bottom = 0;
    for (int x = 0; x < SCREEN_W; ++x) top += (at(x, 204) & 0xff) / 255.0, bottom += (at(x, 236) & 0xff) / 255.0;
    check("a box leaning back is narrower at its top than at its foot", bottom - top > 1.0 && bottom - top < 12, "%.2f and %.2f px", top, bottom);
}

/* coverage moved a fraction of a pixel is the same ink, spread over the neighbours */
static void glyphs(void) {
    static const uint8_t dot[9] = { 0, 255, 0, 255, 255, 255, 0, 255, 0 };
    double sums[3];
    static const float shift[3][2] = { { 0, 0 }, { 0.5f, 0 }, { 0.25f, 0.75f } };
    all();
    for (int k = 0; k < 3; ++k) {
        fill((rect_t){ 0, 0, 40, 40 }, 0);
        blit_a8(dot, 3, 3, 10 + shift[k][0], 10 + shift[k][1], 0xffffff, 256);
        sums[k] = 0;
        for (int y = 0; y < 40; ++y)
            for (int x = 0; x < 40; ++x) sums[k] += (at(x, y) & 0xff) / 255.0;
    }
    check("a glyph between pixels keeps its ink", fabs(sums[1] - sums[0]) < 0.1 && fabs(sums[2] - sums[0]) < 0.1,
          "%.3f, %.3f, %.3f", sums[0], sums[1], sums[2]);
    fill((rect_t){ 0, 0, 40, 40 }, 0);
    blit_a8(dot, 3, 3, 10.5f, 10, 0xffffff, 256);
    check("half a pixel right, the middle row's ends are half lit", abs((int)(at(10, 11) & 0xff) - 128) <= 3 && (at(11, 11) & 0xff) == 0xff && abs((int)(at(13, 11) & 0xff) - 128) <= 3,
          "%02x %02x %02x", at(10, 11) & 0xff, at(11, 11) & 0xff, at(13, 11) & 0xff);
    fill((rect_t){ 0, 0, 40, 40 }, 0);
    fade_set(0, 13, 0, 2);
    blit_a8(dot, 3, 3, 10, 10, 0xffffff, 256);
    fade_clear();
    check("a fade window dims a blit toward its end", (at(10, 11) & 0xff) == 0xff && (at(12, 11) & 0xff) < 0x20, "%02x %02x", at(10, 11) & 0xff, at(12, 11) & 0xff);
}

/* a slow ramp: 50 rows from a dim grey to nothing has more rows than levels, so cut off it shows
 * bands; dithered, a row's mean still follows the ramp and no two neighbours in a row are alike
 * for long */
static void ramps(void) {
    all();
    fill((rect_t){ 0, 0, SCREEN_W, SCREEN_H }, 0x202020);
    shade_rows(100, 200, 255, 0);
    double worst = 0;
    int mixed = 0;
    for (int y = 100; y < 200; ++y) {
        double mean = 0, want = 0x20 * (double)(199 - y) / 99;
        int lo = 255, hi = 0;
        for (int x = 0; x < SCREEN_W; ++x) {
            int v = (int)(at(x, y) & 0xff);
            mean += v, lo = v < lo ? v : lo, hi = v > hi ? v : hi;
        }
        mean /= SCREEN_W;
        if (fabs(mean - want) > worst) worst = fabs(mean - want);
        mixed += hi > lo;
    }
    check("a dithered fade's rows keep the ramp's mean to a third of a level", worst < 0.34, "worst %.3f", worst);
    check("most of its rows mix two levels", mixed > 60, "%d of 100", mixed);
    int count[256] = { 0 }, uniform = 1;
    for (int i = 0; i < 4096; ++i) ++count[dither64[i]];
    for (int i = 0; i < 256; ++i) uniform &= count[i] == 16;
    check("the noise table holds every value 16 times", uniform, "");
}

static void draw_marks(void *st, rect_t r) {
    (void)st;
    ui_mark(r);
}
static void draw_none(void *st, rect_t r) {
    (void)st, (void)r;
}
/* a page whose draw marks is stopped, not shown */
static void paint_guard(void) {
    fflush(stdout);
    pid_t pid = fork();
    if (!pid) {
        static const ui_page bad = { .name = "bad", .draw = draw_marks };
        fclose(stderr);
        ui_push(&bad, 0);
        ui_clock_fake(1000000);
        ui_frame(0, 0);
        _exit(0);
    }
    int status = 0;
    waitpid(pid, &status, 0);
    check("a draw that marks is aborted", WIFSIGNALED(status) && WTERMSIG(status) == SIGABRT, "status %x", status);
    static const ui_page good = { .name = "good", .draw = draw_none };
    ui_push(&good, 0);
    ui_clock_fake(1000000);
    check("a draw that only draws is shown", ui_frame(0, 0) == 1 && !ui_painting, "");
    ui_clock_fake(1016667);
    check("with nothing marked no frame goes out", ui_frame(0, 0) == 0, "");
}

/* the turn to a 6-bit panel: the bits the link drops are clear, black stays black, and a block's
 * mean is the 8-bit colour's */
static void narrow(const char *fb) {
    all();
    for (int y = 0; y < SCREEN_H; ++y)
        for (int x = 0; x < SCREEN_W; ++x) canvas[y * SCREEN_W + x] = 0xff000000 | (y < 64 ? 0 : 0x535353);
    dmg_all();
    fb_present();
    FILE *f = fopen(fb, "rb");
    static uint32_t page[320 * 375];
    fseek(f, (long)(fb_page() * 320 * 375 * 4), SEEK_SET);
    size_t got = fread(page, 4, 320 * 375, f);
    fclose(f);
    int low = 0, lit = 0;
    double mean = 0;
    unsigned n = 0;
    for (int y = 0; y < SCREEN_H; ++y)
        for (int x = 0; x < SCREEN_W; ++x) {
            uint32_t p = page[x * 320 + (319 - y)];
            low += (p & 0x030303) != 0;
            if (y < 64)
                lit += (p & 0xffffff) != 0;
            else
                mean += p & 0xff, ++n;
        }
    check("a 6-bit panel is sent no low bits", got == 320 * 375 && !low, "%d pixels with some", low);
    check("black stays black under the dither", !lit, "%d lit", lit);
    check("0x53 dithered to 6 bits is 0x53 on average", fabs(mean / n - 0x53) < 0.2, "%.3f", mean / n);
}

int main(int argc, char **argv) {
    char regular[300], medium[300];
    if (argc < 3) return fprintf(stderr, "usage: q2ui-test PANEL_FILE FONTS_DIR\n"), 2;
    if (fb_open(argv[1], 6)) return fprintf(stderr, "cannot use %s\n", argv[1]), 2;
    snprintf(regular, sizeof regular, "%s/inter.ttf", argv[2]);
    snprintf(medium, sizeof medium, "%s/inter-medium.ttf", argv[2]);
    if (text_open(regular, medium, 0)) return fprintf(stderr, "no font in %s\n", argv[2]), 2;
    fb_vsync = 0;
    springs();
    edges();
    glyphs();
    ramps();
    narrow(argv[1]);
    paint_guard();
    printf("q2ui-test: %s\n", failed ? "FAILED" : "all checks passed");
    return failed;
}
