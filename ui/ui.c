/* Pages, their stack, and the loop (docs/ui-rewrite.md#the-loop).
 *
 * A frame is always these four, in this order: what arrived is read and changes state; whatever
 * moves is stepped to the frame's time; the rects that marked are drawn from the state, once; the
 * frame is shown, which waits for the panel. While a frame is drawn nothing may mark or change
 * state (ui_painting): a draw is a function of the state alone, so one frame never shows two
 * instants. At rest the loop is asleep in poll: nothing runs but the clock's minute.
 *
 * Pacing: a frame that was shown waited for the panel's refresh, so while something moves the
 * loop runs once a refresh and steps the motion by the clock. A frame whose work ran past the
 * refresh, or that reached the panel a refresh late, is counted (ui_stats): the motion still
 * lands where the clock says, a step coarser.
 *
 * A page opening or closing slides (docs/oled-design.md#motion): both pages are drawn once, each
 * into a canvas of its own, and a frame of the slide is those two pictures placed, the status row
 * over them. */
#include <dirent.h>
#include <poll.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/resource.h>
#include <time.h>

#include "q2ui.h"

#define STACK 16
#define MARKS 8
#define PUSH_MS 220
#define POP_MS 200
#define DRIFT 0.30f /* how far the page under drifts, of the screen's width */
#define DARKEN 64   /* and how far it darkens, of 255 */
#define GLYPH_BUDGET_US 3000 /* what a frame of a motion may spend making glyphs (text.c) */
#define WARM_US 4000         /* and a pass of the loop at rest, making them ahead */
#define WAKE_PAUSE_US 3000000u /* no input for this long: the screen may have slept and woken since */

int ui_reduce_motion, ui_painting;
struct ui_stats ui_stats;

static struct {
    const ui_page *page;
    void *st;
} stack[STACK];
static int depth, quit, quit_code, again;
static uint64_t wake_at, last_shown_us;
static int last_moving;
static unsigned since_shown; /* UI_SINCE_*: what the loop did since the last frame shown (the stats' gaps) */
static uint64_t lat_from; /* when the wheel's or a finger's input the next frame shows came (input_at_us) */
struct ui_frame_rec *ui_frame_log;
unsigned ui_frame_log_n, ui_frame_log_at;
static struct {
    int n;
    rect_t r[MARKS];
} marks;
static struct {
    int dir; /* 1: a page opens over `from`; -1: `from` closes off `to`; 0: no slide */
    uint64_t t0;
    uint32_t *from, *to;
    float at; /* 0 to 1 */
} slide;
extern uint64_t fb_panned_us; /* fb.c: when the last frame's pan was made */
extern uint64_t fb_pan_us, fb_wait_us; /* fb.c: the last pan ioctl's time and the refresh wait after it */

static void (*rest_hook)(void), (*stack_hook)(void);
void ui_on_rest(void (*fn)(void)) { rest_hook = fn; }
static void (*second_hook)(void);
void ui_on_second(void (*fn)(void)) { second_hook = fn; }
/* the loop's own work since the last frame shown (the frame log's loop_us), and whether the second's hook was in it */
static uint64_t loop_us;
static int loop_second;
static int hold_key;
static void (*hold_fn)(void);
void ui_on_hold(int key, void (*fn)(void)) { hold_key = key, hold_fn = fn; }
/* A finger that lands above PULL_Y and goes PULL_PX down: pull_fn, on every page (stock's pull-down,
 * on_wm_tsmove_fun: g_pressdown_y under 31, 20 px down), given the move that crossed and every event
 * of that touch after it, its lift too, so what it opens can follow the finger; no page sees them. The page that
 * had the touch until then is given an IN_TOUCH_UP of value 1: the touch is over for it, and it is no tap. */
#define PULL_Y 31
#define PULL_PX 20
static void (*pull_fn)(const input_t *e);
static struct {
    int y0, pulled;
} pull;
void ui_on_pull(void (*fn)(const input_t *e)) { pull_fn = fn; }
static int pulled(const input_t *e) {
    if (e->type == IN_TOUCH_DOWN) pull.y0 = e->y, pull.pulled = 0;
    if (e->type < IN_TOUCH_DOWN) return 0;
    if (!pull.pulled && !(e->type == IN_TOUCH_MOVE && pull_fn && pull.y0 < PULL_Y && e->y - pull.y0 >= PULL_PX)) return 0;
    if (!pull.pulled && depth && stack[depth - 1].page->event) { /* the page had the touch until now: it ends, no tap */
        input_t gone = { IN_TOUCH_UP, 1, 0, e->ms, e->x, e->y };
        stack[depth - 1].page->event(stack[depth - 1].st, &gone);
    }
    pull.pulled = e->type != IN_TOUCH_UP;
    pull_fn(e);
    return 1;
}
void (*ui_clicker)(int button);
void ui_click(int button) {
    if (ui_clicker) ui_clicker(button);
}
void ui_on_stack(void (*fn)(void)) { stack_hook = fn; }

void ui_mark(rect_t r) {
    if (ui_painting) {
        fprintf(stderr, "q2ui: ui_mark while a frame is drawn\n");
        abort();
    }
    rect_t screen = { 0, 0, SCREEN_W, SCREEN_H };
    if (!rect_meet(r, screen, &r)) return;
    for (int i = 0; i < marks.n; ++i) {
        rect_t *o = &marks.r[i];
        if (r.x >= o->x && r.y >= o->y && r.x + r.w <= o->x + o->w && r.y + r.h <= o->y + o->h)
            return;
    }
    if (marks.n < MARKS)
        marks.r[marks.n++] = r;
    else
        marks.r[MARKS - 1] = rect_union(marks.r[MARKS - 1], r);
}
void ui_mark_all(void) {
    marks.n = 0;
    ui_mark((rect_t){ 0, 0, SCREEN_W, SCREEN_H });
}
void ui_again(void) { again = 1; }
void ui_wake_ms(unsigned ms) {
    uint64_t at = ui_time() + (uint64_t)ms * 1000u;
    if (!wake_at || at < wake_at) wake_at = at;
}
int ui_depth(void) { return depth; }
void *ui_top(void) { return depth ? stack[depth - 1].st : 0; }
const char *ui_top_name(void) { return depth ? stack[depth - 1].page->name : ""; }
void ui_route(char *out, size_t room) {
    size_t at = 0;
    *out = 0;
    for (int i = 0; i < depth && stack[i].page->where; ++i) {
        char name[48] = "";
        int sel = stack[i].page->where(stack[i].st, name, sizeof name);
        if (!*name) break;
        at += (size_t)snprintf(out + at, room - at, "%s%s:%d", i ? "/" : "", name, sel);
        if (at >= room) break;
    }
}
void ui_select_top(int sel) {
    if (depth && stack[depth - 1].page->select && sel >= 0) stack[depth - 1].page->select(stack[depth - 1].st, sel);
}
int ui_moving(void) { return again; }
int ui_sliding(void) { return slide.dir != 0; }
int ui_trace(char *out, size_t room) {
    if (depth && stack[depth - 1].page->trace) return stack[depth - 1].page->trace(stack[depth - 1].st, out, room);
    return list_trace(out, room);
}
int ui_wake_pending(void) { return wake_at != 0; }
void ui_quit(int code) { quit = 1, quit_code = code; }
int ui_quitting(int *code) {
    if (code) *code = quit_code;
    return quit;
}
void ui_stats_reset(void) { memset(&ui_stats, 0, sizeof ui_stats); }

/* The page `down` under the top (0 the top) as it is now, whole, into a canvas of its own (no
 * status row: that stays put over a slide). */
static uint32_t *snapshot_at(int down) {
    uint32_t *buf = malloc(SCREEN_W * SCREEN_H * sizeof *buf);
    rect_t all = { 0, 0, SCREEN_W, SCREEN_H };
    if (!buf) return 0;
    uint32_t *screen = canvas_swap(buf);
    ui_painting = 1;
    clip_set(all);
    stack[depth - 1 - down].page->draw(stack[depth - 1 - down].st, all);
    ui_painting = 0;
    canvas_swap(screen);
    return buf;
}
static uint32_t *snapshot(void) { return snapshot_at(0); }
static void slide_end(void) {
    free(slide.from), free(slide.to);
    slide.from = slide.to = 0, slide.dir = 0;
    ui_mark_all();
}
static void slide_start(int dir, uint32_t *from) {
    if (slide.dir) slide_end();
    slide.from = from, slide.to = snapshot();
    if (!slide.from || !slide.to) {
        slide_end();
        return;
    }
    slide.dir = dir, slide.t0 = ui_time(), slide.at = 0;
    again = 1;
}

/* The screen as it is now, whole: the top page and the status row over it (a sheet's page behind); 0 with no memory.
 * ui_snapshot_under is the same with the page under the top (what a sheet going away uncovers). */
static uint32_t *screen_at(int down) {
    uint32_t *buf = depth > down ? snapshot_at(down) : 0;
    if (!buf) return 0;
    rect_t all = { 0, 0, SCREEN_W, SCREEN_H };
    uint32_t *screen = canvas_swap(buf);
    ui_painting = 1;
    clip_set(all);
    status_draw(all);
    ui_painting = 0;
    canvas_swap(screen);
    return buf;
}
uint32_t *ui_snapshot_screen(void) { return screen_at(0); }
uint32_t *ui_snapshot_under(void) { return screen_at(1); }
int ui_status_hidden;
static int still; /* the next push or pop has no slide: the page moves itself (a sheet) */
void ui_push_still(const ui_page *p, void *arg) {
    still = 1;
    ui_push(p, arg);
    still = 0;
}
void ui_pop_still(void) {
    still = 1;
    ui_pop();
    still = 0;
}

void ui_push(const ui_page *p, void *arg) {
    if (depth == STACK) return;
    uint32_t *from = depth && list_motion && !ui_reduce_motion && !still ? snapshot() : 0;
    stack[depth].page = p, stack[depth].st = p->open ? p->open(arg) : 0;
    ++depth;
    ui_mark_all();
    if (from) slide_start(1, from);
    if (stack_hook) stack_hook();
}
void ui_pop(void) {
    if (!depth) return;
    uint32_t *from = depth > 1 && list_motion && !ui_reduce_motion && !still ? snapshot() : 0;
    --depth;
    if (stack[depth].page->close) stack[depth].page->close(stack[depth].st);
    if (!depth) {
        free(from);
        ui_quit(0);
        return;
    }
    if (stack[depth - 1].page->shown) stack[depth - 1].page->shown(stack[depth - 1].st);
    ui_mark_all();
    if (from) slide_start(-1, from);
    if (stack_hook) stack_hook();
}

/* One frame of a slide: the page under at its drift, darkened, and the page over it from its
 * left edge on. Whole pixels: a slide crosses the screen in a fifth of a second. */
static void slide_draw(void) {
    float p = slide.at;
    const uint32_t *over = slide.dir > 0 ? slide.to : slide.from,
                   *under = slide.dir > 0 ? slide.from : slide.to;
    float open = slide.dir > 0 ? p : 1 - p; /* how far the page over is in */
    int edge = (int)((1 - open) * SCREEN_W + 0.5f), drift = (int)(open * DRIFT * SCREEN_W + 0.5f);
    unsigned keep = 255 - (unsigned)(open * DARKEN);
    keep += keep >> 7;
    for (int y = 0; y < SCREEN_H; ++y) {
        uint32_t *row = canvas + y * SCREEN_W;
        const uint32_t *u = under + y * SCREEN_W + drift;
        for (int x = 0; x < edge; ++x) {
            uint32_t c = x + drift < SCREEN_W ? u[x] : 0;
            uint32_t rb = ((c & 0xff00ff) * keep) >> 8 & 0xff00ff, g = ((c & 0xff00) * keep) >> 8 & 0xff00;
            row[x] = 0xff000000 | rb | g;
        }
        memcpy(row + edge, over + y * SCREEN_W, (size_t)(SCREEN_W - edge) * sizeof *row);
    }
}

static void account(uint64_t start_us, uint64_t draw_ns, uint64_t show_ns, unsigned px, int moving) {
    struct ui_stats *s = &ui_stats;
    uint64_t work = fb_panned_us > start_us ? fb_panned_us - start_us : 0, refresh = fb_refresh_us();
    static const unsigned edges[] = { 1000, 2000, 4000, 8000, 12000, 16700, 33000 };
    unsigned bin = 0;
    while (bin < 7 && work >= edges[bin]) ++bin;
    ++s->frames, ++s->hist[bin];
    s->px += px, s->draw_ns += draw_ns, s->show_ns += show_ns, s->work_us += work;
    if (draw_ns > s->draw_max_ns && s->frames > 2) s->draw_max_ns = draw_ns;
    if (work > s->work_max_us && s->frames > 2) s->work_max_us = work;
    /* the pan is left out: the Q2's returns at the refresh that shows the frame, a wait and not work */
    if (work - (fb_pan_us < work ? fb_pan_us : work) > refresh) ++s->over_budget;
    s->pan_us += fb_pan_us, s->wait_us += fb_wait_us;
    if (fb_pan_us > s->pan_max_us && s->frames > 2) s->pan_max_us = fb_pan_us;
    if (s->frames > 2 && (draw_ns + show_ns) / 1000u > refresh) ++s->cpu_over;
    /* a frame of a motion follows the last by one refresh; one that took a refresh and a half or
     * more missed that many refreshes. A motion whose next step drew nothing, or after which the
     * loop waited at rest, had ended there by its own steps: the frame after is a new motion's, its
     * gap a pause (gaps_paused), not refreshes missed */
    if (last_moving && last_shown_us && fb_vsync) {
        uint64_t gap = mono_us() - last_shown_us, k = (gap + refresh / 2) / refresh;
        if (since_shown & (UI_SINCE_REST | UI_SINCE_EMPTY))
            ++s->gaps_paused;
        else {
            ++s->gap_hist[k <= 1 ? 0 : k == 2 ? 1 : k == 3 ? 2 : k <= 6 ? 3 : 4];
            if (gap >= refresh * 3 / 2 && s->frames <= 2) /* the first turns: start-up, not the motion's pace */
                ++s->start_late;
            else if (gap >= refresh * 3 / 2) {
                s->dropped += (unsigned)k - 1, ++s->late;
                s->streak = 0;
                if (gap > s->gap_max_us) s->gap_max_us = gap, s->gap_max_flags = since_shown;
            } else if (++s->streak > s->streak_max)
                s->streak_max = s->streak;
        }
    }
    if (lat_from) {
        uint64_t lat = fb_panned_us > lat_from ? fb_panned_us - lat_from : 0;
        static const unsigned lat_edges[] = { 16700, 33300, 50000, 66700, 100000 };
        unsigned b = 0;
        while (b < 5 && lat >= lat_edges[b]) ++b;
        ++s->lat_hist[b], ++s->lat_n, s->lat_us += lat;
        if (lat > s->lat_max_us) s->lat_max_us = lat;
        lat_from = 0;
    }
    if (ui_frame_log_n) {
        struct ui_frame_rec *f = &ui_frame_log[ui_frame_log_at];
        ui_frame_log_at = (ui_frame_log_at + 1) % ui_frame_log_n;
        uint64_t dt = last_shown_us ? mono_us() - last_shown_us : 0;
        f->dt_us = dt > 0xffffffffu ? 0xffffffffu : (uint32_t)dt;
        f->scroll16 = list_drawn ? (int32_t)(list_drawn_scroll * 16) : INT32_MIN;
        f->flags = (moving ? FRAME_MOVING : 0) | (fb_overlap ? FRAME_OVERLAP : 0) | (text_deferred ? FRAME_DEFER : 0) |
                   (fb_page() ? FRAME_PAGE1 : 0) | (since_shown & UI_SINCE_INPUT ? FRAME_INPUT : 0) |
                   (fb_scanned ? FRAME_SCANNED : 0) | (loop_second ? FRAME_SECOND : 0);
        f->loop_us = loop_us > 0xffffffffu ? 0xffffffffu : (uint32_t)loop_us;
        f->work_us = work > 0xffffffffu ? 0xffffffffu : (uint32_t)work;
        f->pan_us = fb_pan_us > work ? f->work_us : (uint32_t)fb_pan_us;
        f->glyphs = text_made_frame, f->glyph_max_us = text_glyph_max_us;
        struct rusage ru;
        static long faults_before;
        if (!getrusage(RUSAGE_SELF, &ru)) f->faults = (uint32_t)(ru.ru_majflt - faults_before), faults_before = ru.ru_majflt;
    }
    list_drawn = 0, loop_us = 0, loop_second = 0;
    last_shown_us = mono_us(), last_moving = moving;
    since_shown = (text_deferred ? UI_SINCE_DEFER : 0) | (slide.dir ? UI_SINCE_SLIDE : 0);
}

int ui_frame(const input_t *in, int n) {
    uint64_t start = mono_us();
    ui_clock_read();
    again = 0;
    if (wake_at && ui_time() >= wake_at) wake_at = 0;
    for (int i = 0; i < n && depth && !quit; ++i) {
        if (in[i].type == IN_TICK) ++ui_stats.ticks;
        /* every press clicks as it goes down, as stock's keys do (on_wm_keydown) */
        if (in[i].type == IN_KEY_DOWN && (in[i].value == KEY_UI_CENTRE || in[i].value == KEY_UI_RETURN)) ui_click(1);
        if (notice_event(&in[i])) /* a question on the card takes every input while it asks (notice.c) */
            continue;
        if (pulled(&in[i])) continue;
        if (in[i].type == IN_KEY_HOLD && hold_fn && in[i].value == hold_key)
            hold_fn();
        else if (stack[depth - 1].page->event)
            stack[depth - 1].page->event(stack[depth - 1].st, &in[i]);
    }
    if (!depth || quit) return 0;
    status_step();
    notice_step();
    if (stack[depth - 1].page->step) stack[depth - 1].page->step(stack[depth - 1].st);
    if (slide.dir) {
        float ms = (float)(ui_time() - slide.t0) / 1000.0f + (float)fb_refresh_us() / 1000.0f;
        slide.at = ease_cubic_out(ms / (slide.dir > 0 ? PUSH_MS : POP_MS));
        if (slide.at >= 1)
            slide_end();
        else
            ui_mark_all(), again = 1;
    }
    if (n) since_shown |= UI_SINCE_INPUT;
    for (int i = 0; i < n && !lat_from; ++i)
        if (in[i].type == IN_TICK || in[i].type == IN_TUG || in[i].type == IN_TOUCH_DOWN || in[i].type == IN_TOUCH_MOVE)
            lat_from = input_at_us ? input_at_us : start;
    if (!marks.n && !dmg_count()) {
        since_shown |= UI_SINCE_EMPTY;
        lat_from = 0; /* the input moved nothing: no frame shows it */
        return 0;
    }
    uint64_t t0 = cpu_ns();
    /* a frame that follows another by a refresh is a motion's: it may not stop to make every new
     * glyph. A page's first frame, from rest, makes all it shows. */
    text_budget_us = last_moving ? GLYPH_BUDGET_US : 0;
    ui_painting = 1;
    if (slide.dir) {
        rect_t all = { 0, 0, SCREEN_W, SCREEN_H };
        clip_set(all);
        slide_draw();
        if (!ui_status_hidden) status_draw(all);
        notice_draw(all);
    } else
        for (int i = 0; i < marks.n; ++i) {
            clip_set(marks.r[i]);
            stack[depth - 1].page->draw(stack[depth - 1].st, marks.r[i]);
            clip_set(marks.r[i]);
            if (!ui_status_hidden) status_draw(marks.r[i]);
            notice_draw(marks.r[i]); /* a toast and the card over the page and its status row */
        }
    ui_painting = 0;
    for (int i = 0; i < marks.n; ++i) dmg_add(marks.r[i]);
    marks.n = 0;
    uint64_t t1 = cpu_ns();
    unsigned px = fb_present();
    if (text_deferred) /* the text left out is drawn with the next frame */
        ui_stats.deferred += (unsigned)text_deferred, ui_mark_all(), again = 1;
    account(start, t1 - t0, cpu_ns() - t1, px, again);
    text_frame();
    return 1;
}


/* Whether the screen is dark: demo, or q2sysd on Vamp OS, turns the light off after its idle time and
 * on at the next press, which is then a wake and nothing else (docs/internals.md#screen-off-and-wake).
 * q2ui reads the same keys, so it drops what arrives while the light is off. The Q2 has no
 * backlight class device (its light is stock's PWM on /dev/jz_pwm), so the word is the light's
 * owner's: Q2SYS/1's screen events on Vamp OS (ui/api.c), demo's dark= in its status file
 * (patch/q2ui_gate.c tell). Where neither says, a backlight device's bl_power and brightness; none
 * of the three (a host run, the emulator) is a lit screen. */
const char *ui_backlight = "/sys/class/backlight";
int ui_dark(void) {
    static char power[160], level[160];
    static int found = -1;
    int said = api_screen_dark();
    if (said >= 0) return said;
    char word[8];
    if (status_told_value("dark", word, sizeof word)) return atoi(word) != 0;
    if (found < 0) {
        found = 0;
        char cmd[200];
        snprintf(cmd, sizeof cmd, "%s", ui_backlight);
        DIR *d = opendir(cmd);
        for (struct dirent *e; d && (e = readdir(d));)
            if (e->d_name[0] != '.' && !found) {
                snprintf(power, sizeof power, "%s/%.60s/bl_power", ui_backlight, e->d_name);
                snprintf(level, sizeof level, "%s/%.60s/brightness", ui_backlight, e->d_name);
                found = 1;
            }
        if (d) closedir(d);
    }
    if (!found) return 0;
    char text[16] = "";
    FILE *f = fopen(power, "r");
    int off = f && fgets(text, sizeof text, f) && atoi(text) != 0;
    if (f) fclose(f);
    if (off) return 1;
    f = fopen(level, "r");
    off = f && fgets(text, sizeof text, f) && atoi(text) == 0;
    if (f) fclose(f);
    return off;
}

/* a slot given up (ui_unwatch) keeps fd -1, which poll passes over, until ui_watch takes it again: the loop's
 * slots never move under it while it calls them */
#define WATCHES 16
static int watches;
static struct {
    int fd;
    void (*fn)(void);
} watch[WATCHES];
void ui_watch(int fd, void (*fn)(void)) {
    for (int k = 0; k < watches; ++k)
        if (watch[k].fd < 0) {
            watch[k].fd = fd, watch[k].fn = fn;
            return;
        }
    if (watches < WATCHES) watch[watches].fd = fd, watch[watches++].fn = fn;
}
void ui_unwatch(int fd) {
    for (int k = 0; k < watches; ++k)
        if (watch[k].fd == fd) watch[k].fd = -1;
}

/* SIGHUP: q2ui starts itself again in place (ui_restart, set by main.c: the same program file run
 * anew under the same pid), so options changed in /mnt/data/q2ui.args, or a new q2ui put where the
 * old one was, take effect with no restart of the Q2 and without demo's gate seeing anything end.
 * If that cannot be done it ends with UI_EXIT_RELOAD, on which a gate that knows the code starts
 * it again (patch/q2ui_gate.c). The signal is held back everywhere but in the loop's wait
 * (ppoll), from main's first line (main.c) on, so one sent while q2ui starts waits for the loop; it always ends a wait and is seen at once: let in anywhere else, one that came
 * between the loop's look at it and its next wait was seen only when that wait ended, at rest up
 * to a minute later. */
void (*ui_restart)(void);
static volatile sig_atomic_t reload;
static void on_hup(int sig) {
    (void)sig;
    reload = 1;
}

int ui_run(void) {
    struct pollfd fds[3 + WATCHES];
    sigset_t hup, open_mask;
    sigemptyset(&hup), sigaddset(&hup, SIGHUP);
    signal(SIGHUP, on_hup);
    sigprocmask(SIG_BLOCK, &hup, &open_mask); /* open_mask: as it was, SIGHUP let in */
    sigdelset(&open_mask, SIGHUP);
    int keys[3], nfds = input_fds(keys, 3), showed = 0, rested = 0, warm = 1;
    for (int i = 0; i < nfds; ++i) fds[i] = (struct pollfd){ keys[i], POLLIN, 0 };
    while (!quit) {
        int n = nfds;
        for (int k = 0; k < watches; ++k) fds[n++] = (struct pollfd){ watch[k].fd, POLLIN, 0 };
        int wait = -1, hold = input_wait_ms();
        if (again)
            /* a frame just shown waited for the panel, so the next step is due now; a step that
             * drew nothing waits a refresh itself */
            wait = showed ? 0 : (int)(fb_refresh_us() / 1000u);
        else if (wake_at) {
            uint64_t now = mono_us();
            wait = wake_at > now ? (int)((wake_at - now + 999) / 1000u) : 0;
        }
        if (hold >= 0 && (wait < 0 || hold < wait)) wait = hold;
        if (wait < 0) {
            /* at rest: nothing until input or the clock's next minute, once the common glyphs
             * are made, a slice at a time so input is never kept waiting */
            if (!rested && rest_hook) rest_hook();
            rested = 1, since_shown |= UI_SINCE_REST;
            wait = !warm ? (int)(60 - time(0) % 60) * 1000 : poll(fds, (nfds_t)n, 0) > 0 ? 0 : (warm = text_warm(WARM_US), 0);
        } else
            rested = 0;
        struct timespec ts = { wait / 1000, (long)(wait % 1000) * 1000000L };
        ppoll(fds, (nfds_t)n, wait >= 0 ? &ts : 0, &open_mask);
        /* dark as the wait ended, before the watches read a word of the screen lit: the press that
         * lit it (demo's wake, at the same key) may be read in the same pass as that word */
        int dark = ui_dark();
        for (int k = 0; k < watches; ++k)
            if (fds[nfds + k].revents && fds[nfds + k].fd == watch[k].fd && watch[k].fd >= 0) {
                uint64_t t0 = mono_us();
                watch[k].fn();
                loop_us += mono_us() - t0;
            }
        input_t in[64];
        int got = input_read(in, 64);
        /* the press that wakes the screen is only that, its release included. demo lights it at a
         * key itself (patch/nav/input.c); q2sysd only at the power key, so on Vamp OS q2ui asks for
         * it at any other key's press (not the wheel's tick nor a touch, as demo's wake_key) */
        static uint64_t quiet_until, last_input;
        if (got && (dark || ui_dark())) {
            quiet_until = mono_us() + 400000;
            for (int i = 0; i < got && api_vamp; ++i)
                if (in[i].type == IN_KEY_DOWN && in[i].value != 116) { /* KEY_POWER: q2sysd's */
                    api_sys_send("screen on");
                    break;
                }
        }
        if (got && mono_us() < quiet_until) got = 0;
        /* a panel set up again on waking is back at 16 bits a pixel (panel.c): told again when the
         * screen was dark, and at the first input after a pause long enough for it to have been */
        if (got && (quiet_until > last_input || mono_us() - last_input > WAKE_PAUSE_US)) panel_24bit();
        if (got) last_input = mono_us();
        if (reload) {
            sigprocmask(SIG_SETMASK, &open_mask, 0); /* the program run next starts with it let in */
            if (ui_restart) ui_restart(); /* does not come back when it works */
            ui_quit(UI_EXIT_RELOAD);
        }
        showed = ui_frame(in, got);
        if (showed) rested = 0;
        static uint64_t second_at; /* while it runs, once a second (the stats file, read live on a Q2) */
        if (second_hook && mono_us() - second_at >= 1000000u) {
            second_at = mono_us(), second_hook();
            loop_us += mono_us() - second_at, loop_second = 1;
        }
    }
    while (depth) {
        --depth;
        if (stack[depth].page->close) stack[depth].page->close(stack[depth].st);
    }
    if (slide.dir) slide_end();
    return quit_code;
}
