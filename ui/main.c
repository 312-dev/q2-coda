/* q2ui's start: options, the library, the panel, the first page, then the loop (ui.c).
 *
 *     q2ui [--db FILE] [--catalogue FILE] [--fb FILE] [--input DIR] [--fonts DIR] [--icons DIR]
 *          [--limit N] [--motion 0|1] [--reduce-motion] [--light] [--panel-bits 8|6|5|565]
 *          [--panel-mode 24|keep] [--panel-mem FILE]
 *          [--text awtk] [--page home|songs|depth|gallery|card] [--stats FILE] [--bench] [--script FILE]
 *          [--trace FILE] [--clock HH:MM] [--state FILE] [--cmdq FIFO] [--playlist FILE]
 *          [--backlight DIR] [--ask FILE] [--now FILE] [--cover FILE]
 *
 * --catalogue is the server library's (default /mnt/mmc/.q2pod/catalogue.db; "" for none): its
 * songs are listed with the card's, as the Library lists them. --panel-bits is the depth of the
 * link to the glass, which every frame is dithered down to (docs/ui-rewrite.md#gradients);
 * --panel-mode keep leaves the panel in the 16-bit mode stock set (ui/panel.c puts it in 24
 * otherwise), and --panel-mem is a file standing in for the registers, for a test. kill -HUP
 * ends q2ui with a code on which demo starts it again, so this file's options can be changed
 * with no restart of the Q2.
 * --page depth is the card that finds it by eye; gallery and card are the toolkit's examples
 * (ui/page_gallery.c). --text awtk lays text out on whole pixels, as
 * demo does, for comparing screens.
 *
 * Options also come from /mnt/data/q2ui.args (one line), as demo starts it with none
 * (patch/q2ui_gate.c). Return on the first page ends it, and demo has the screen again. demo also
 * ends it, by a signal, when a window of its own must be seen, and starts it again after: so the
 * pages it had open and each one's row are kept in --state (a file in /tmp: gone at a restart)
 * each time it comes to rest, and a start within STATE_FRESH_S of that opens them again. --cmdq and --playlist are
 * the player's queue and list file (ui/player.c), --now and --cover what plays and its cover
 * (ui/now.c), --ask the file a page of demo's is asked for in (ui_demo_page), --backlight the
 * folder whose state says the screen is dark; a test names its own.
 *
 * --bench draws and turns frames without the panel's wait and writes their CPU time to the stats
 * file. --script runs a file of input on a clock of its own, a frame every 1/60 s whatever the
 * machine's speed, for tests and recordings (docs/ui-rewrite.md#scripts); --trace writes a line
 * a frame of the list's motion while it runs, and --clock fixes the status row's clock so two
 * runs give the same picture. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#include "q2ui.h"

#define FRAME_US 16667u

static struct {
    const char *db, *catalogue, *fb, *input, *fonts, *stats, *script, *trace, *page, *state, *ask;
    int limit, bench, bits;
} opt = { "/mnt/data/database.db",
          "/mnt/mmc/.q2pod/catalogue.db",
          "/dev/fb0",
          "/dev/input",
          "/release/assets/default/raw/fonts",
          "/mnt/data/q2ui-stats.json",
          0,
          0,
          "home",
          "/tmp/q2ui.state",
          "/tmp/q2ui.open",
          0,
          0,
          6 }; /* the Q2's panel shows 6 bits a channel or fewer (docs/ui-rewrite.md#gradients) */
static unsigned open_ms, first_ms;
static char bench_json[2048];

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
        else if (!strcmp(a, "--icons"))
            icon_dir = v, ++i;
        else if (!strcmp(a, "--stats"))
            opt.stats = v, ++i;
        else if (!strcmp(a, "--script"))
            opt.script = v, ++i;
        else if (!strcmp(a, "--trace"))
            opt.trace = v, ++i;
        else if (!strcmp(a, "--page"))
            opt.page = v, ++i;
        else if (!strcmp(a, "--state"))
            opt.state = v, ++i;
        else if (!strcmp(a, "--ask"))
            opt.ask = v, ++i;
        else if (!strcmp(a, "--now"))
            now_file = v, ++i;
        else if (!strcmp(a, "--cover"))
            now_cover_file = v, ++i;
        else if (!strcmp(a, "--cmdq"))
            player_queue = v, ++i;
        else if (!strcmp(a, "--playlist"))
            player_list = v, ++i;
        else if (!strcmp(a, "--backlight"))
            ui_backlight = v, ++i;
        else if (!strcmp(a, "--limit"))
            opt.limit = atoi(v), ++i;
        else if (!strcmp(a, "--motion"))
            list_motion = atoi(v) != 0, ++i;
        else if (!strcmp(a, "--panel-bits"))
            opt.bits = atoi(v), ++i;
        else if (!strcmp(a, "--panel-mode"))
            panel_keep = !strcmp(v, "keep"), ++i;
        else if (!strcmp(a, "--panel-mem"))
            panel_mem = v, panel_base = 0, ++i;
        else if (!strcmp(a, "--text"))
            text_whole = !strcmp(v, "awtk"), ++i;
        else if (!strcmp(a, "--clock"))
            status_clock_fixed(v), ++i;
        else if (!strcmp(a, "--reduce-motion"))
            ui_reduce_motion = 1;
        else if (!strcmp(a, "--light"))
            ui_light = 1;
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
    const struct ui_stats *s = &ui_stats;
    if (!f) return;
    double frames = s->frames ? (double)s->frames : 1;
    fprintf(f,
            "{\"rows\": %d, \"open_ms\": %u, \"first_frame_ms\": %u, \"frames\": %u, \"ticks\": %u,\n"
            " \"draw_us_per_frame\": %.1f, \"show_us_per_frame\": %.1f, \"draw_us_max\": %.1f, "
            "\"px_turned_per_frame\": %u,\n"
            " \"work_us_per_frame\": %.1f, \"work_us_max\": %llu, \"over_budget\": %u, \"cpu_over_budget\": %u, \"dropped\": %u, "
            "\"late\": %u, \"streak_max\": %u, \"refresh_us\": %u, \"glyphs_deferred\": %u,\n"
            " \"work_hist_ms\": {\"lt1\": %u, \"lt2\": %u, \"lt4\": %u, \"lt8\": %u, \"lt12\": %u, "
            "\"lt16_7\": %u, \"lt33\": %u, \"more\": %u},\n"
            " \"glyphs\": %u, \"glyph_bytes\": %u, \"glyphs_made\": %u, \"glyph_evictions\": %u, "
            "\"motion\": %d, \"panel_bits\": %d, \"page\": %d,\n"
            " \"now_seq\": %u, \"now_watched\": %d, \"playing\": %d, \"paused\": %d, \"cover\": %d, \"top\": \"%s\"%s%s}\n",
            lib_songs(), open_ms, first_ms, s->frames, s->ticks, (double)s->draw_ns / frames / 1e3,
            (double)s->show_ns / frames / 1e3, (double)s->draw_max_ns / 1e3, (unsigned)(s->px / frames),
            (double)s->work_us / frames, (unsigned long long)s->work_max_us, s->over_budget, s->cpu_over, s->dropped,
            s->late, s->streak_max, fb_refresh_us(), s->deferred, s->hist[0], s->hist[1], s->hist[2], s->hist[3],
            s->hist[4], s->hist[5], s->hist[6], s->hist[7], text_stats.glyphs, text_stats.bytes,
            text_stats.made, text_stats.evictions, list_motion, fb_bits, fb_page(), now_seq(), now_watched(),
            now_get()->playing, now_get()->paused, now_cover() != 0, ui_top_name(),
            *bench_json ? ",\n \"bench\": " : "", bench_json);
    fclose(f);
}

/* Where a q2ui before this one rested, when that was a moment ago: its pages and each one's row
 * (ui_route), opened again one over another with no motion. */
#define STATE_FRESH_S 600
static void state_save(void) {
    char route[400];
    ui_route(route, sizeof route);
    FILE *f = *route ? fopen(opt.state, "w") : 0;
    if (!f) return;
    fprintf(f, "%ld %s\n", (long)time(0), route);
    fclose(f);
}
static void state_restore(void) {
    char route[400] = "";
    long at = 0;
    FILE *f = fopen(opt.state, "r");
    if (!f) return;
    int n = fscanf(f, "%ld %399s", &at, route);
    fclose(f);
    if (n != 2 || time(0) - at >= STATE_FRESH_S) return;
    int motion = list_motion, first = 1;
    list_motion = 0;
    for (char *seg = strtok(route, "/"); seg; seg = strtok(0, "/"), first = 0) {
        char *colon = strchr(seg, ':');
        if (!colon) break;
        *colon = 0;
        if (first) { /* the first page is open already: the same one, or the route is another start's */
            if (strcmp(seg, opt.page)) break;
        } else if (!strcmp(seg, "now"))
            ui_push(&page_now, 0);
        else if (!library_route(seg))
            break;
        ui_select_top(atoi(colon + 1));
    }
    list_motion = motion;
}

void ui_demo_page(const char *name) {
    FILE *f = fopen(opt.ask, "w");
    if (!f) return;
    fprintf(f, "%s\n", name);
    fclose(f);
    state_save();
    ui_quit(UI_EXIT_ASK); /* demo reads the name on this exit code (patch/q2ui_gate.c) */
}
static void at_rest(void) {
    write_stats();
    state_save();
}

static void first_page(void) {
    if (!strcmp(opt.page, "home"))
        ui_push(&page_home, 0);
    else if (!strcmp(opt.page, "depth"))
        ui_push(&page_depth, 0);
    else if (!strcmp(opt.page, "gallery"))
        gallery_push();
    else if (!strcmp(opt.page, "card"))
        ui_push(&page_card, 0);
    else
        songs_push();
}

/* ---- the bench: CPU time a frame, on a clock of its own ---- */
static uint64_t fake_us = 1000000;
static void bench_note(const char *name, unsigned frames, uint64_t cpu, uint64_t draw, uint64_t show,
                       uint64_t worst, unsigned px) {
    size_t at = strlen(bench_json);
    if (!frames) return;
    snprintf(bench_json + at, sizeof bench_json - at,
             "%s\"%s\": {\"passes\": %u, \"us_per_pass\": %.1f, \"draw_us\": %.1f, \"turn_us\": %.1f, "
             "\"worst_us\": %.1f, \"px_turned\": %u}",
             at > 1 ? ", " : "", name, frames, (double)cpu / frames / 1e3, (double)draw / frames / 1e3,
             (double)show / frames / 1e3, (double)worst / 1e3, px / frames);
}
/* `ticks` ticks, `gap_us` apart, each followed by frames a refresh apart until the next is due;
 * with settle, frames go on after the last until nothing moves. */
static void bench_case(const char *name, int ticks, int dir, unsigned gap_us, int settle) {
    struct ui_stats before = ui_stats;
    uint64_t t0 = cpu_ns(), worst = 0;
    for (int i = 0; i < ticks; ++i) {
        input_t in = { IN_TICK, dir, 0, (unsigned)(fake_us / 1000u) };
        uint64_t until = fake_us + gap_us;
        for (int first = 1; fake_us < until || (settle && i == ticks - 1 && ui_moving()); first = 0) {
            uint64_t f0 = cpu_ns();
            ui_clock_fake(fake_us);
            ui_frame(&in, first);
            if (cpu_ns() - f0 > worst) worst = cpu_ns() - f0;
            fake_us += FRAME_US;
            if (!list_motion) break;
        }
        if (fake_us < until) fake_us = until;
    }
    if (name)
        bench_note(name, ui_stats.frames - before.frames, cpu_ns() - t0, ui_stats.draw_ns - before.draw_ns,
                   ui_stats.show_ns - before.show_ns, worst, ui_stats.px - before.px);
}
static void bench(void) {
    int rows = lib_songs(), steps = rows > 420 ? 200 : rows / 3, motion = list_motion;
    fb_vsync = 0;
    strcpy(bench_json, "{");
    /* with no motion, as phase 1 measured them: a tick on the page (two rows), a tick that
     * scrolls (the list), the whole screen */
    list_motion = 0;
    for (int k = 0; k < 20; ++k) bench_case(0, 5, 1, 1000000, 0), bench_case(0, 5, -1, 1000000, 0);
    bench_case("move", 5, 1, 1000000, 0);
    bench_case("scroll", steps, 1, 1000000, 0);
    uint64_t t0 = cpu_ns();
    for (int k = 0; k < 50; ++k) ui_mark_all(), ui_clock_fake(fake_us += FRAME_US), ui_frame(0, 0);
    size_t at = strlen(bench_json);
    snprintf(bench_json + at, sizeof bench_json - at, ", \"full_us_per_pass\": %.1f", (double)(cpu_ns() - t0) / 50 / 1e3);
    /* and as they run: every frame of a slow tick's glide, and of a fast spin */
    list_motion = 1;
    bench_case(0, 4, -1, 400000, 1);
    bench_case("glide_slow", 20, 1, 400000, 1);
    bench_case("glide_fast", 100, 1, 27000, 1);
    bench_case("glide_back", 100, -1, 27000, 1);
    strcat(bench_json, "}");
    list_motion = motion;
}

/* ---- a script: input on a clock of its own (docs/ui-rewrite.md#scripts) ---- */
static FILE *trace;
static char record_dir[256];
static unsigned recorded;
static void ppm(const char *path) {
    FILE *f = fopen(path, "wb");
    if (!f) return;
    fprintf(f, "P6\n%d %d\n255\n", SCREEN_W, SCREEN_H);
    for (int i = 0; i < SCREEN_W * SCREEN_H; ++i) {
        uint8_t rgb[3] = { (uint8_t)(canvas[i] >> 16), (uint8_t)(canvas[i] >> 8), (uint8_t)canvas[i] };
        fwrite(rgb, 1, 3, f);
    }
    fclose(f);
}
static void script_frame(const input_t *in, int n) {
    fake_us += FRAME_US;
    ui_clock_fake(fake_us);
    int shown = ui_frame(in, n);
    if (trace) {
        char line[160];
        list_trace(line, sizeof line);
        fprintf(trace, "t=%llu shown=%d %s\n", (unsigned long long)fake_us, shown, line);
    }
    if (shown && *record_dir) {
        char path[320];
        snprintf(path, sizeof path, "%s/f%05u.ppm", record_dir, recorded++);
        ppm(path);
    }
}
static int key_code(const char *name) {
    return !strcmp(name, "centre")   ? KEY_UI_CENTRE
           : !strcmp(name, "return") ? KEY_UI_RETURN
           : !strcmp(name, "play")   ? KEY_UI_PLAY
           : !strcmp(name, "next")   ? KEY_UI_NEXT
           : !strcmp(name, "prev")   ? KEY_UI_PREV
                                     : atoi(name);
}
/* Lines: tick N | tug SHARE | key NAME | down NAME | up NAME | hold NAME | run MS | rest |
 * shot FILE | record DIR | record off | page songs|depth. Each input goes in with the next frame. */
static int script(const char *path) {
    FILE *f = fopen(path, "r");
    char line[400], arg[320];
    if (!f) return 2;
    fb_vsync = 0;
    if (opt.trace) trace = fopen(opt.trace, "w");
    ui_clock_fake(fake_us);
    first_page();
    script_frame(0, 0);
    while (fgets(line, sizeof line, f)) {
        unsigned ms = (unsigned)(fake_us / 1000u);
        *arg = 0;
        if (sscanf(line, "tick %319s", arg) == 1) {
            int n = atoi(arg);
            for (int i = 0; i < abs(n); ++i) {
                input_t in = { IN_TICK, n > 0 ? 1 : -1, 0, (unsigned)(fake_us / 1000u) };
                script_frame(&in, 1);
            }
        } else if (sscanf(line, "tug %319s", arg) == 1) {
            input_t in = { IN_TUG, 0, (float)atof(arg), ms };
            script_frame(&in, 1);
        } else if (sscanf(line, "key %319s", arg) == 1) {
            input_t in[2] = { { IN_KEY_DOWN, key_code(arg), 0, ms }, { IN_KEY_UP, key_code(arg), 0, ms } };
            script_frame(&in[0], 1);
            script_frame(&in[1], 1);
        } else if (sscanf(line, "down %319s", arg) == 1 || sscanf(line, "up %319s", arg) == 1 ||
                   sscanf(line, "hold %319s", arg) == 1) {
            input_t in = { *line == 'd' ? IN_KEY_DOWN : *line == 'u' ? IN_KEY_UP : IN_KEY_HOLD, key_code(arg), 0, ms };
            script_frame(&in, 1);
        } else if (sscanf(line, "run %319s", arg) == 1) {
            for (uint64_t until = fake_us + (uint64_t)atoi(arg) * 1000u; fake_us < until;) script_frame(0, 0);
        } else if (!strncmp(line, "rest", 4)) {
            for (int k = 0; k < 900 && (ui_moving() || ui_wake_pending()); ++k) script_frame(0, 0);
        } else if (sscanf(line, "shot %319s", arg) == 1) {
            ppm(arg);
        } else if (sscanf(line, "record %255s", arg) == 1) {
            if (!strcmp(arg, "off"))
                *record_dir = 0;
            else
                strcpy(record_dir, arg);
        }
        if (!ui_depth() || ui_quitting(0)) break;
    }
    fclose(f);
    if (trace) fclose(trace);
    write_stats();
    int code = 0;
    ui_quitting(&code);
    return code; /* a page that ended the program ends the script, with its code */
}

int main(int argc, char **argv) {
    char regular[256], medium[256], fallback[256];
    uint64_t t0 = mono_us();
    parse_file("/mnt/data/q2ui.args");
    parse(argc - 1, argv + 1);
    snprintf(regular, sizeof regular, "%s/inter.ttf", opt.fonts);
    snprintf(medium, sizeof medium, "%s/inter-medium.ttf", opt.fonts);
    snprintf(fallback, sizeof fallback, "%s/default.ttf", opt.fonts);
    if (text_open(regular, medium, fallback))
        return fprintf(stderr, "q2ui: no font in %s\n", opt.fonts), 2;
    if (lib_open(opt.db, *opt.catalogue ? opt.catalogue : 0, opt.limit))
        return fprintf(stderr, "q2ui: cannot read %s\n", opt.db), 2;
    if (fb_open(opt.fb, opt.bits)) return fprintf(stderr, "q2ui: cannot use %s\n", opt.fb), 2;
    panel_24bit();
    open_ms = (unsigned)((mono_us() - t0) / 1000u);
    now_open();
    if (opt.script) {
        int code = script(opt.script);
        fb_close();
        return code;
    }
    if (opt.bench) {
        ui_clock_fake(fake_us);
        first_page();
        ui_frame(0, 0);
        ui_mark_all(), ui_clock_fake(fake_us += FRAME_US), ui_frame(0, 0); /* both pages hold the page now */
        bench();
        write_stats();
        fb_close();
        return 0;
    }
    input_open(opt.input);
    input_flush(); /* nothing pressed before it had the screen is its to act on (a FIFO standing in for a device keeps such) */
    first_page();
    state_restore();
    ui_frame(0, 0);
    first_ms = (unsigned)((mono_us() - t0) / 1000u);
    ui_on_rest(at_rest);
    ui_on_stack(state_save); /* a page that never rests (Now Playing while a song plays) is still come back to */
    int code = ui_run();
    write_stats();
    fb_close();
    return code;
}
