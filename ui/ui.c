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

static void (*rest_hook)(void), (*stack_hook)(void);
void ui_on_rest(void (*fn)(void)) { rest_hook = fn; }
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
int ui_wake_pending(void) { return wake_at != 0; }
void ui_quit(int code) { quit = 1, quit_code = code; }
int ui_quitting(int *code) {
    if (code) *code = quit_code;
    return quit;
}
void ui_stats_reset(void) { memset(&ui_stats, 0, sizeof ui_stats); }

/* The top page as it is now, whole, into a canvas of its own (no status row: that stays put over
 * a slide). */
static uint32_t *snapshot(void) {
    uint32_t *buf = malloc(SCREEN_W * SCREEN_H * sizeof *buf);
    rect_t all = { 0, 0, SCREEN_W, SCREEN_H };
    if (!buf) return 0;
    uint32_t *screen = canvas_swap(buf);
    ui_painting = 1;
    clip_set(all);
    stack[depth - 1].page->draw(stack[depth - 1].st, all);
    ui_painting = 0;
    canvas_swap(screen);
    return buf;
}
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

void ui_push(const ui_page *p, void *arg) {
    if (depth == STACK) return;
    uint32_t *from = depth && list_motion && !ui_reduce_motion ? snapshot() : 0;
    stack[depth].page = p, stack[depth].st = p->open ? p->open(arg) : 0;
    ++depth;
    ui_mark_all();
    if (from) slide_start(1, from);
    if (stack_hook) stack_hook();
}
void ui_pop(void) {
    if (!depth) return;
    uint32_t *from = depth > 1 && list_motion && !ui_reduce_motion ? snapshot() : 0;
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
    if (work > refresh) ++s->over_budget;
    if (s->frames > 2 && (draw_ns + show_ns) / 1000u > refresh) ++s->cpu_over;
    /* a frame of a motion follows the last by one refresh; one that took a refresh and a half or
     * more missed that many refreshes */
    if (last_moving && last_shown_us && fb_vsync) {
        uint64_t gap = mono_us() - last_shown_us;
        if (gap >= refresh * 3 / 2) {
            s->dropped += (unsigned)((gap + refresh / 2) / refresh) - 1, ++s->late;
            s->streak = 0;
        } else if (++s->streak > s->streak_max)
            s->streak_max = s->streak;
    }
    last_shown_us = mono_us(), last_moving = moving;
}

int ui_frame(const input_t *in, int n) {
    uint64_t start = mono_us();
    ui_clock_read();
    again = 0;
    if (wake_at && ui_time() >= wake_at) wake_at = 0;
    for (int i = 0; i < n && depth && !quit; ++i) {
        if (in[i].type == IN_TICK) ++ui_stats.ticks;
        if (stack[depth - 1].page->event) stack[depth - 1].page->event(stack[depth - 1].st, &in[i]);
    }
    if (!depth || quit) return 0;
    status_step();
    if (stack[depth - 1].page->step) stack[depth - 1].page->step(stack[depth - 1].st);
    if (slide.dir) {
        float ms = (float)(ui_time() - slide.t0) / 1000.0f + (float)fb_refresh_us() / 1000.0f;
        slide.at = ease_cubic_out(ms / (slide.dir > 0 ? PUSH_MS : POP_MS));
        if (slide.at >= 1)
            slide_end();
        else
            ui_mark_all(), again = 1;
    }
    if (!marks.n && !dmg_count()) return 0;
    uint64_t t0 = cpu_ns();
    /* a frame that follows another by a refresh is a motion's: it may not stop to make every new
     * glyph. A page's first frame, from rest, makes all it shows. */
    text_budget_us = last_moving ? GLYPH_BUDGET_US : 0;
    ui_painting = 1;
    if (slide.dir) {
        rect_t all = { 0, 0, SCREEN_W, SCREEN_H };
        clip_set(all);
        slide_draw();
        status_draw(all);
    } else
        for (int i = 0; i < marks.n; ++i) {
            clip_set(marks.r[i]);
            stack[depth - 1].page->draw(stack[depth - 1].st, marks.r[i]);
            clip_set(marks.r[i]);
            status_draw(marks.r[i]);
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


/* Whether the screen is dark: demo turns the backlight off after its idle time and on at the
 * next press, which is then a wake and nothing else (docs/internals.md#screen-off-and-wake). q2ui
 * reads the same keys, so it drops what arrives while the light is off. No backlight to read (a
 * host run, the emulator) is a lit screen. */
const char *ui_backlight = "/sys/class/backlight";
static int dark(void) {
    static char power[160], level[160];
    static int found = -1;
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

#define WATCHES 4
static int watches;
static struct {
    int fd;
    void (*fn)(void);
} watch[WATCHES];
void ui_watch(int fd, void (*fn)(void)) {
    if (watches < WATCHES) watch[watches].fd = fd, watch[watches++].fn = fn;
}

/* SIGHUP: q2ui starts itself again in place (ui_restart, set by main.c: the same program file run
 * anew under the same pid), so options changed in /mnt/data/q2ui.args, or a new q2ui put where the
 * old one was, take effect with no restart of the Q2 and without demo's gate seeing anything end.
 * If that cannot be done it ends with UI_EXIT_RELOAD, on which a gate that knows the code starts
 * it again (patch/q2ui_gate.c). The signal is held back everywhere but in the loop's wait
 * (ppoll), so it always ends a wait and is seen at once: let in anywhere else, one that came
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
            rested = 1;
            wait = !warm ? (int)(60 - time(0) % 60) * 1000 : poll(fds, (nfds_t)n, 0) > 0 ? 0 : (warm = text_warm(WARM_US), 0);
        } else
            rested = 0;
        struct timespec ts = { wait / 1000, (long)(wait % 1000) * 1000000L };
        ppoll(fds, (nfds_t)n, wait >= 0 ? &ts : 0, &open_mask);
        for (int k = 0; k < watches; ++k)
            if (fds[nfds + k].revents) watch[k].fn();
        input_t in[64];
        int got = input_read(in, 64);
        /* the press that wakes the screen is only that, its release included */
        static uint64_t quiet_until, last_input;
        if (got && dark()) quiet_until = mono_us() + 400000;
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
    }
    while (depth) {
        --depth;
        if (stack[depth].page->close) stack[depth].page->close(stack[depth].st);
    }
    if (slide.dir) slide_end();
    return quit_code;
}
