/* q2ui's loop (docs/ui-rewrite.md#the-loop): wait in poll for the wheel, a key or the next thing
 * the motion or the clock is due, change the state, draw the rects that changed, show the frame.
 * With nothing due it sleeps until input: no timer runs at rest but the clock's minute.
 *
 *     q2ui [--db FILE] [--catalogue FILE] [--fb FILE] [--input DIR] [--fonts DIR] [--limit N]
 *          [--motion 0|1|2] [--copy] [--stats FILE] [--bench]
 *
 * --catalogue is the server library's (default /mnt/mmc/.q2pod/catalogue.db; "" for none): its
 * songs are listed with the card's, as the Library lists them.
 *
 * Options also come from /mnt/data/q2ui.args (one line), as S90play starts it with none. Return
 * ends it, and S90play then starts demo (tools/build.py UI_SPIKE_HOOK). --bench draws and turns
 * frames without the panel's wait and writes their CPU time to the stats file. */
#include <poll.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

#include "q2ui.h"

static struct {
    const char *db, *catalogue, *fb, *input, *fonts, *stats;
    int limit, bench;
} opt = { "/mnt/data/database.db",
          "/mnt/mmc/.q2pod/catalogue.db",
          "/dev/fb0",
          "/dev/input",
          "/release/assets/default/raw/fonts",
          "/mnt/data/q2ui-stats.json",
          0,
          0 };
static struct {
    unsigned frames, ticks, px, open_ms, first_ms, rows;
    uint64_t draw_ns, show_ns, draw_max_ns;
} stat;
static char bench_json[2048];

unsigned now_ms(void) {
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return (unsigned)(t.tv_sec * 1000 + t.tv_nsec / 1000000);
}
uint64_t cpu_ns(void) {
    struct timespec t;
    clock_gettime(CLOCK_PROCESS_CPUTIME_ID, &t);
    return (uint64_t)t.tv_sec * 1000000000u + (uint64_t)t.tv_nsec;
}

static void parse(int argc, char **argv) {
    for (int i = 0; i < argc; ++i) {
        const char *a = argv[i], *v = i + 1 < argc ? argv[i + 1] : "";
        if (!strcmp(a, "--db"))
            opt.db = v, ++i;
        else if (!strcmp(a, "--catalogue"))
            opt.catalogue = v, ++i;
        else if (!strcmp(a, "--fb"))
            opt.fb = v, ++i;
        else if (!strcmp(a, "--input"))
            opt.input = v, ++i;
        else if (!strcmp(a, "--fonts"))
            opt.fonts = v, ++i;
        else if (!strcmp(a, "--stats"))
            opt.stats = v, ++i;
        else if (!strcmp(a, "--limit"))
            opt.limit = atoi(v), ++i;
        else if (!strcmp(a, "--motion"))
            list_motion = atoi(v), ++i;
        else if (!strcmp(a, "--copy"))
            list_copy_scroll = 1;
        else if (!strcmp(a, "--bench"))
            opt.bench = 1;
    }
}
/* /mnt/data/q2ui.args: the same options, kept for the process's life */
static void parse_file(const char *path) {
    static char line[512];
    static char *args[32];
    FILE *f = fopen(path, "r");
    int n = 0;
    if (!f) return;
    if (fgets(line, sizeof line, f))
        for (char *t = strtok(line, " \t\r\n"); t && n < 32; t = strtok(0, " \t\r\n"))
            args[n++] = t;
    fclose(f);
    parse(n, args);
}

static void write_stats(void) {
    FILE *f = fopen(opt.stats, "w");
    if (!f) return;
    fprintf(
        f,
        "{\"rows\": %u, \"open_ms\": %u, \"first_frame_ms\": %u, \"frames\": %u, \"ticks\": %u,\n"
        " \"draw_us_per_frame\": %.1f, \"show_us_per_frame\": %.1f, \"draw_us_max\": %.1f, "
        "\"px_turned_per_frame\": %u,\n"
        " \"glyphs\": %u, \"glyph_bytes\": %u, \"glyphs_made\": %u, \"glyph_evictions\": %u, "
        "\"motion\": %d, \"copy\": %d, \"page\": %d%s%s}\n",
        stat.rows, stat.open_ms, stat.first_ms, stat.frames, stat.ticks,
        stat.frames ? (double)stat.draw_ns / stat.frames / 1e3 : 0,
        stat.frames ? (double)stat.show_ns / stat.frames / 1e3 : 0, (double)stat.draw_max_ns / 1e3,
        stat.frames ? stat.px / stat.frames : 0, text_stats.glyphs, text_stats.bytes,
        text_stats.made, text_stats.evictions, list_motion, list_copy_scroll, fb_page(),
        *bench_json ? ",\n \"bench\": " : "", bench_json);
    fclose(f);
}

/* Draw what the state marked and show it. Returns whether a frame went out. */
static int frame(void) {
    rect_t r[8];
    int n = list_dirty(r, 8);
    if (!n && !dmg_count()) return 0;
    uint64_t t0 = cpu_ns();
    for (int i = 0; i < n; ++i) list_draw(r[i]);
    uint64_t t1 = cpu_ns();
    stat.px += fb_present();
    uint64_t t2 = cpu_ns();
    text_frame();
    ++stat.frames;
    stat.draw_ns += t1 - t0, stat.show_ns += t2 - t1;
    if (t1 - t0 > stat.draw_max_ns && stat.frames > 2) stat.draw_max_ns = t1 - t0;
    return 1;
}

static void clock_now(void) {
    char s[8];
    time_t t = time(0);
    struct tm *tm = localtime(&t);
    snprintf(s, sizeof s, "%02d:%02d", tm->tm_hour, tm->tm_min);
    list_clock(s);
}

/* One case of the bench: `ticks` single ticks, a second apart on the motion's clock so none
 * accelerates, each drawn and turned; the CPU time of a pass and its parts, appended to the
 * bench's JSON under name (not when name is 0: a warm-up). */
static void bench_case(const char *name, int ticks, int dir, unsigned *clock) {
    uint64_t d0 = stat.draw_ns, s0 = stat.show_ns, t0 = cpu_ns();
    unsigned f0 = stat.frames, p0 = stat.px;
    for (int i = 0; i < ticks; ++i) {
        *clock += 1000;
        list_tick(dir, *clock);
        frame();
    }
    unsigned frames = stat.frames - f0;
    size_t at = strlen(bench_json);
    if (name && frames)
        snprintf(bench_json + at, sizeof bench_json - at,
                 "%s\"%s\": {\"passes\": %u, \"us_per_pass\": %.1f, \"draw_us\": %.1f, "
                 "\"turn_us\": %.1f, \"px_turned\": %u}",
                 at > 1 ? ", " : "", name, frames, (double)(cpu_ns() - t0) / frames / 1e3,
                 (double)(stat.draw_ns - d0) / frames / 1e3,
                 (double)(stat.show_ns - s0) / frames / 1e3, (stat.px - p0) / frames);
}
static void bench(int rows) {
    unsigned clock = 1000;
    int motion = list_motion, steps = rows > 420 ? 200 : rows / 3;
    fb_vsync = 0, list_motion = LIST_MOTION_OFF;
    strcpy(bench_json, "{");
    frame(), frame(); /* both pages hold the page now */
    /* the selection walks down the first page and back: two rows a pass, no scroll */
    for (int k = 0; k < 20; ++k) bench_case(0, 6, 1, &clock), bench_case(0, 6, -1, &clock);
    bench_case("move", 6, 1, &clock);
    /* then on down the list: every tick scrolls a row, drawn whole, then by copying */
    list_copy_scroll = 0;
    bench_case("scroll_redraw", steps, 1, &clock);
    list_copy_scroll = 1;
    bench_case("scroll_copy", steps, 1, &clock);
    list_copy_scroll = 0;
    /* and the whole screen, as a page's first frame is */
    uint64_t t0 = cpu_ns();
    for (int k = 0; k < 50; ++k) list_open_again(), frame();
    size_t at = strlen(bench_json);
    snprintf(bench_json + at, sizeof bench_json - at, ", \"full_us_per_pass\": %.1f}",
             (double)(cpu_ns() - t0) / 50 / 1e3);
    list_motion = motion;
}

int main(int argc, char **argv) {
    static source_t songs;
    char regular[256], medium[256], fallback[256];
    unsigned t0 = now_ms();
    parse_file("/mnt/data/q2ui.args");
    parse(argc - 1, argv + 1);
    snprintf(regular, sizeof regular, "%s/inter.ttf", opt.fonts);
    snprintf(medium, sizeof medium, "%s/inter-medium.ttf", opt.fonts);
    snprintf(fallback, sizeof fallback, "%s/default.ttf", opt.fonts);
    if (text_open(regular, medium, fallback))
        return fprintf(stderr, "q2ui: no font in %s\n", opt.fonts), 2;
    if (songs_open(opt.db, *opt.catalogue ? opt.catalogue : 0, &songs, opt.limit))
        return fprintf(stderr, "q2ui: cannot read %s\n", opt.db), 2;
    if (fb_open(opt.fb)) return fprintf(stderr, "q2ui: cannot use %s\n", opt.fb), 2;
    input_open(opt.input);
    stat.rows = (unsigned)songs.n, stat.open_ms = now_ms() - t0;
    list_open(&songs);
    clock_now();
    frame();
    stat.first_ms = now_ms() - t0;
    if (opt.bench) {
        bench(songs.n);
        write_stats();
        fb_close();
        return 0;
    }
    struct pollfd fds[4];
    int keys[2], nfds = input_fds(keys, 2), quit = 0, dirty_stats = 1, showed = 0;
    for (int i = 0; i < nfds; ++i) fds[i] = (struct pollfd){ keys[i], POLLIN, 0 };
    while (!quit) {
        unsigned now = now_ms();
        int wait = list_wait_ms(now);
        if (wait < 0) {
            /* at rest: the stats once, then nothing until input or the clock's next minute */
            if (dirty_stats) write_stats(), dirty_stats = 0;
            wait = (int)(60 - time(0) % 60) * 1000;
        }
        /* a frame just shown waited for the panel: the motion's next step is due now */
        poll(fds, (nfds_t)nfds, showed && wait <= 16 ? 0 : wait);
        input_t in[64];
        int n = input_read(in, 64);
        for (int i = 0; i < n; ++i) {
            if (in[i].type == IN_TICK) list_tick(in[i].value, in[i].ms), ++stat.ticks;
            if (in[i].type == IN_KEY_UP && in[i].value == KEY_UI_RETURN) quit = 1;
        }
        list_step(now_ms());
        clock_now();
        showed = frame();
        dirty_stats |= showed;
    }
    write_stats();
    fb_close();
    return 0;
}
