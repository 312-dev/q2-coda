/* q2ui: the menu UI as a program of Vamp's own (docs/ui-rewrite.md). Nothing here is stock's or
 * AWTK's.
 *
 * The frame (docs/ui-rewrite.md#the-frame): a 375 x 320 landscape canvas in memory, drawn only
 * inside the rects a change marked (ui_mark), then those rects turned onto the panel's hidden
 * page and panned to, one frame a refresh (fb.c). State changes first (events, then the motion's
 * step to the frame's time), then one draw of what changed: nothing is drawn from an input
 * handler, and nothing may mark or change state while a frame is drawn (ui_mark aborts there).
 *
 * The toolkit, bottom up: fb.c (the panel, damage, the dither to the panel's depth), draw.c
 * (shapes with smoothed edges at sub-pixel places, shadows, gradients, glyph and image blits),
 * anim.c (the frame clock, springs, curves), text.c (faces, glyphs, runs), image.c (PNG and JPEG),
 * input.c (the wheel, its tug, the keys), ui.c (pages, the stack, slides, the loop), list.c (the
 * list every menu is), status.c (the status row). Pages are page_*.c. */
#ifndef Q2UI_H
#define Q2UI_H

/* The toolkit's interface version (docs/ui-rewrite.md#the-toolkit): the first number changes when
 * a page written for the last one would no longer build or behave, the second when something is
 * added. */
#define Q2UI_API_MAJOR 0
#define Q2UI_API_MINOR 2

#include <stddef.h>
#include <stdint.h>

#define SCREEN_W 375
#define SCREEN_H 320

typedef struct {
    int x, y, w, h;
} rect_t;
typedef struct {
    float x, y, w, h;
} rectf_t;

/* ---- fb.c: the panel ---- */
extern uint32_t *canvas; /* SCREEN_W x SCREEN_H, 0xffRRGGBB; draw.c draws into it */
/* bits: the panel link's bits a channel (8, 6 or 5, or 565 for red 5, green 6, blue 5); under 8
 * the turn dithers down to it */
int fb_open(const char *path, int bits);
void fb_depth(int bits); /* the same, changed while it runs */
void fb_close(void);
void dmg_add(rect_t r); /* r changed: turned and shown with the next frame */
int dmg_count(void);
void dmg_all(void); /* both pages are stale (the screen came back from another program) */
/* The frame's rects, and the last frame's, turned onto the hidden page; then the pan and the wait
 * for the panel's refresh. Returns the pixels turned. */
unsigned fb_present(void);
int fb_page(void);      /* the page on screen, 0 or 1 */
extern int fb_vsync;    /* 0: fb_present does not wait (the bench, a script's clock) */
extern int fb_pan_blocks; /* a file's pan waits for the refresh, as the Q2's does (--pan-blocks, a host test) */
extern int fb_check_page; /* read the panel's shown page back before each turn (the stats' fb_overlaps) */
extern int fb_flush;      /* between the turn and the pan: 0 nothing, 1 msync, 2 the data cache written back (MIPS) */
extern int fb_overlap;    /* the last turn's page was the one the panel said it showed */
extern const char *fb_lcd_site; /* --lcd-site: where the display controller's registers are read (/dev/mem), or 0 */
extern int fb_scanned;   /* the last turn wrote into the page the display controller was reading (--lcd-site) */
extern int fb_bits;     /* the panel's bits a channel, as given */
unsigned fb_refresh_us(void); /* the panel's refresh period, from its timings (16667 unknown) */
/* A second canvas a page is drawn into for a slide: canvas_swap makes it the one draw.c draws
 * into and returns the other. */
uint32_t *canvas_swap(uint32_t *other);

/* ---- panel.c: the panel's colour mode ---- */
int panel_24bit(void);           /* tell the panel to take 24 bits a pixel; 1 when the command was sent */
extern const char *panel_mem;    /* what is mapped for the DSI host's registers (/dev/mem), */
extern unsigned panel_base;      /* and where in it they are (a test's file has them at 0) */
extern int panel_keep;           /* 1: the panel's mode is never touched */

/* ---- anim.c: the frame clock and what moves by it ---- */
/* The time every motion is stepped to, microseconds: read once a frame, so one frame shows one
 * instant. A script's clock is its own (ui_clock_fake). */
uint64_t ui_time(void);
void ui_clock_read(void);         /* the frame's time is now */
void ui_clock_fake(uint64_t us);  /* the frame's time is us, and the real clock is not read */
uint64_t mono_us(void);
uint64_t cpu_ns(void);
/* A damped spring: x goes to `to` with velocity v kept across retargets, so a new target mid-way
 * starts from where it is and how fast. response is the period in seconds (a critically damped
 * one is at rest in about that long); damping 1 is critical, under 1 overshoots. */
typedef struct {
    double x, v, to; /* double: a long list's scroll is hundreds of thousands of px, placed to 1/100 */
} spring_t;
/* dt seconds on, exactly (no step size to tune); 1 while it still moves */
int spring_step(spring_t *s, float dt, float response, float damping);
static inline void spring_set(spring_t *s, double x) { s->x = s->to = x, s->v = 0; }
static inline int spring_moving(const spring_t *s) { return s->x != s->to || s->v != 0; }
float ease_cubic_out(float t);
float ease_cubic_in(float t);
static inline float clampf(float v, float lo, float hi) { return v < lo ? lo : v > hi ? hi : v; }
static inline float lerpf(float a, float b, float t) { return a + (b - a) * t; }
uint32_t rgb_mix(uint32_t a, uint32_t b, float t); /* a at 0, b at 1 */

/* ---- draw.c: everything clips to the rect being drawn ---- */
void clip_set(rect_t r);
rect_t clip_get(void);
int rect_meet(rect_t a, rect_t b, rect_t *out);
rect_t rect_union(rect_t a, rect_t b);
rect_t rect_of(rectf_t r, int pad); /* the whole pixels r touches, pad more each side */
void fill(rect_t r, uint32_t rgb);
void fill_alpha(rect_t r, uint32_t rgb, unsigned alpha); /* alpha 0-255 */
/* A rounded box at a sub-pixel place, its edge smoothed by how much of each pixel it covers,
 * blended onto whatever is there. tilt (radians) turns it about its horizontal middle line with a
 * little perspective, the top edge away from the eye when positive, as a tvOS card leans. */
#include "cells.h"
/* the living row's cells for fill_round (list.c): the mask, the box's size, and its ink */
struct cells_paint {
    const cells_t *mask;
    const cells_ink_t *ink;
};
typedef struct {
    float gx, gy;   /* the gleam's centre from the box's, px */
    float rx, ry;   /* its radii */
    float strength; /* 0 to 1 of white at its centre; 0: none */
    const struct cells_paint *cells; /* 0: none; else the box's fill is the cells' ink (cells_px) */
} gleam_t;
#define RGB_GLEAM_ONLY 0xff000000u /* fill_round's rgb for the gleam alone, over what is there */
void fill_round(rectf_t r, float radius, float tilt, uint32_t rgb, float alpha, const gleam_t *g);
/* the soft shadow a raised box casts: rgb at alpha over r, gone blur px out from it; the box is
 * drawn after, over it */
void shadow_round(rectf_t r, float radius, float blur, uint32_t rgb, float alpha);
/* a stroke with round ends, and a disc, smoothed */
void stroke_line(float x0, float y0, float x1, float y1, float width, uint32_t rgb, float alpha);
void fill_disc(float cx, float cy, float radius, uint32_t rgb, float alpha);
/* Rows y0..y1 of the clip scaled toward black: a0/255 of the picture left at y0, a1/255 at y1,
 * dithered so the ramp shows no bands. */
void shade_rows(int y0, int y1, int a0, int a1);
void fade_rows(int y0, int y1, int a0, int a1);  /* the same toward the page's ground, not black: a list's ends */
/* the same across columns */
void shade_cols(int x0, int x1, int a0, int a1);
void shade_all(rect_t r, unsigned keep); /* keep/255 of the picture left */
/* A vertical ramp from top to bottom colour over r, dithered. */
void fill_ramp(rect_t r, uint32_t top, uint32_t bottom);
/* Coverage (0-255 a pixel) inked in rgb at a sub-pixel place; alpha 0-256 over all of it. A blit
 * fades to nothing toward the fade window's ends when one is set (fade_set), for a marquee. */
void blit_a8(const uint8_t *bits, int w, int h, float x, float y, uint32_t rgb, unsigned alpha);
void fade_set(int x0, int x1, int left, int right); /* widths of the fade at each end, 0 none */
void fade_clear(void);
extern const uint8_t dither64[64 * 64]; /* blue noise, each value 16 times (ui/bluenoise.inc) */

/* ---- image.c ---- */
typedef struct {
    int w, h;
    uint32_t *px; /* 0xAARRGGBB, colour not multiplied by alpha */
} image_t;
image_t *image_load(const char *path);                  /* PNG or JPEG; 0 when it cannot be read */
image_t *image_mem(const uint8_t *data, size_t n);      /* the same from memory */
image_t *image_fit(const image_t *src, int w, int h);   /* scaled to cover w x h, cropped, box filtered */
void image_free(image_t *im);
/* The theme's glyph `name` (tools/art.py's oled_<name>_w.png, white, in the icons folder), loaded
 * once; 0 when the folder has none. A name ending in a dot ("mode_shuffle_on.") is that file as it
 * is, oled_mode_shuffle_on.png, in its own ink. */
const image_t *icon(const char *name);
extern const char *icon_dir;
/* im over dst, scaled, at a sub-pixel place, bilinear; alpha 0-256; tint >= 0 inks its alpha in
 * that colour instead of its own (an icon). radius rounds dst's corners. tilt as fill_round's. */
void image_draw(const image_t *im, rectf_t dst, float radius, float tilt, unsigned alpha, long tint);
/* the same of the part `part` of im (its own pixels): a cover shown larger than its frame and moved in it */
void image_draw_part(const image_t *im, rectf_t part, rectf_t dst, float radius, float tilt, unsigned alpha, long tint);
image_t *image_new(int w, int h); /* clear, to be drawn into by hand */

/* ---- card.c: a card of layers at different depths (docs/ui-rewrite.md#cards) ---- */
typedef struct {
    const image_t *im; /* the layer's picture; 0: the flat colour rgb */
    uint32_t rgb;
    float depth;       /* px the layer moves at a full push: behind the card's face when negative */
    float zoom;        /* how much larger than its frame a picture is shown, so it has room to move (0: 1) */
    float inset;       /* px in from the card's edge, each side */
} ui_layer;
/* The card in r pushed by `push` (-1 up to 1 down, the lean's share): it leans as a tvOS card
 * does, casts a shadow that moves the other way, each layer shifts by its depth, and a glint
 * crosses its face. lift (0 to 1) raises it: larger, its shadow deeper. */
void card_draw(rectf_t r, float radius, const ui_layer *layers, int n, float push, float lift);
rect_t card_bounds(rectf_t r); /* everything a card in r can touch at any push and lift */

/* ---- text.c ---- */
enum { FACE_REGULAR, FACE_MEDIUM, FACE_FALLBACK, FACES };
int text_open(const char *regular, const char *medium, const char *fallback);
extern int text_whole; /* 1: advances rounded to whole pixels, as AWTK lays text out (a comparison) */
typedef struct {
    uint16_t n;    /* glyphs */
    uint8_t cut;     /* 1: ended in an ellipsis */
    uint8_t pending; /* 1: some glyphs were left for a later frame to make (text_budget_us) */
    uint8_t face, px;
    float w;       /* the ink's advance, px */
    uint32_t made; /* the glyph cache's generation the run was laid out in */
    struct run_glyph {
        const struct glyph *g;
        int16_t x;
    } at[96];
} run_t;
/* s (UTF-8) laid out at px (AWTK's size: the line, ascent less descent) in face, cut to max px
 * with an ellipsis when wider (max < 0: never). */
void run_make(run_t *r, int face, int px, const char *s, int max);
int run_stale(const run_t *r);
/* the run's pen at x, its line box (px high) from y, both sub-pixel; alpha 0-256 */
void run_draw(const run_t *r, float x, float y, uint32_t rgb, unsigned alpha);
float text_width(int face, int px, const char *s);
void text_frame(void); /* a frame is over: the cache's clock moves on */
extern int text_budget_us; /* what a frame may spend making new glyphs, wall time; 0: all it needs */
extern int text_deferred;  /* glyphs this frame left for the next */
extern unsigned text_made_frame, text_glyph_max_us; /* glyphs this frame made, and the slowest one's time */
int text_warm(unsigned budget_us); /* make common glyphs ahead of need; 1 while more are left */
struct text_stats {
    unsigned glyphs, bytes, made, evictions;
};
extern struct text_stats text_stats;

/* ---- input.c ---- */
/* IN_TOUCH_*: a finger on the screen (hyn_ts), at x, y on the upright screen */
enum { IN_NONE, IN_TICK, IN_TUG, IN_KEY_DOWN, IN_KEY_UP, IN_KEY_HOLD, IN_TOUCH_DOWN, IN_TOUCH_MOVE, IN_TOUCH_UP };
enum {
    KEY_UI_CENTRE = 116,
    KEY_UI_RETURN = 103,
    KEY_UI_PLAY = 108,
    KEY_UI_NEXT = 163,
    KEY_UI_PREV = 165
};
#define KEY_HOLD_MS 600
typedef struct {
    int type;
    int value;   /* IN_TICK: +1 down the list, -1 up; a key: its code */
    float share; /* IN_TUG: the share of the next tick turned, -1 to 1 (0: the finger left) */
    unsigned ms;
    int x, y; /* IN_TOUCH_*: where the finger is, 0 to SCREEN_W - 1 and SCREEN_H - 1 (the last place, lifting) */
    int speed; /* IN_TICK: the wheel's speed as it came, degrees a second (demo's wheel_speed) */
} input_t;
int input_open(const char *dir);
int input_fds(int *fds, int max);
int input_read(input_t *out, int max); /* what has arrived, without waiting */
int input_wheel_feed(int x, int touching, unsigned ms, input_t *out, int max); /* a script's wheel, read as the device's */
extern uint64_t input_at_us;            /* when the first event the last input_read took happened (mono_us) */
int wheel_steps(int speed); /* a level's steps for a tick at speed: demo's volume curve (patch/volume.c vol_accel) */
int input_wait_ms(void);               /* until a held key's hold is due; -1: no key is down */
void input_flush(void);                /* what arrived while another program had the keys is dropped */

/* ---- ui.c: pages, the stack, the loop ---- */
typedef struct ui_page {
    const char *name;
    void *(*open)(void *arg);                   /* build the state; arg is what the opener passed */
    void (*event)(void *st, const input_t *e);  /* change state, ui_mark what changed */
    void (*step)(void *st);                     /* the motion at ui_time(); ui_mark, ui_again */
    void (*draw)(void *st, rect_t r);           /* everything of the page inside r, from the state alone */
    void (*close)(void *st);
    void (*shown)(void *st);                    /* the page is on top again (a page above it closed) */
    /* Where the page is, for q2ui to come back to after demo has had the screen: its name (which
     * the route's reader knows how to open) into `name`, and its selection, -1 with none. 0: the
     * page is not kept in a route, nor any above it. */
    int (*where)(void *st, char *name, size_t room);
    void (*select)(void *st, int sel);          /* the selection put back, with no motion */
    /* a script's trace line for the page, "sel=N ..."; 0: the list last stepped's (list_trace) */
    int (*trace)(void *st, char *out, size_t room);
} ui_page;
/* The stack as "name:sel/name:sel", bottom first, as far as its pages say where they are. */
void ui_route(char *out, size_t room);
void ui_select_top(int sel);
void ui_push(const ui_page *p, void *arg);
void ui_pop(void); /* the top page closes; the last page's pop ends the program */
void ui_push_still(const ui_page *p, void *arg); /* with no slide: the page moves in itself (a sheet) */
void ui_pop_still(void);
uint32_t *ui_snapshot_screen(void); /* the top page and the status row as they are now, malloc'd; 0 with no memory */
uint32_t *ui_snapshot_under(void);  /* the same of the page under the top: what a sheet going away uncovers */
extern int ui_status_hidden;        /* 1: the loop draws no status row (a sheet's page behind has its own in it) */
#define UI_EXIT_ASK 3    /* its exit code with a page of demo's named (ui_demo_page) */
#define UI_EXIT_RELOAD 4 /* and to be started again at once: SIGHUP's, when it cannot start itself again */
extern void (*ui_restart)(void); /* SIGHUP's: start the program again in place; returns only if it could not */
void ui_quit(int code);
int ui_quitting(int *code); /* whether ui_quit was called, and with what */
void ui_mark(rect_t r);  /* r is to be drawn again and shown */
void ui_mark_all(void);
void ui_again(void);     /* something still moves: another frame after this one */
void ui_wake_ms(unsigned ms); /* nothing moves, but step again in ms (a marquee's hold, a fade's start) */
int ui_depth(void);
void *ui_top(void);        /* the top page's state */
const char *ui_top_name(void);
int ui_moving(void);      /* the last frame asked for another */
int ui_sliding(void);     /* a page is sliding on or off */
int ui_trace(char *out, size_t room); /* the top page's trace line (ui_page.trace) */
int ui_wake_pending(void); /* a wake is set */
extern int ui_reduce_motion;
extern int ui_painting; /* 1 while a frame is drawn: marks and state changes are refused */
int ui_run(void);       /* the loop, until ui_quit; the exit code */
void ui_on_rest(void (*fn)(void));       /* called once each time the loop comes to rest */
void ui_on_second(void (*fn)(void));     /* called at most once a second while the loop runs (frames or input) */
void ui_on_stack(void (*fn)(void));      /* called when a page has opened or closed */
void ui_on_hold(int key, void (*fn)(void)); /* a key held, on any page, is fn's and not the page's (Return: Control Center) */
void ui_on_pull(void (*fn)(const input_t *e)); /* a finger pulled down from the screen's top edge, on any page: the
                                                  move that crossed and the rest of that touch */
/* The key click (Key Tone): ui_click(0) for a wheel tick that moved something, (1) for a press. The toolkit clicks
 * every Centre and Return press itself; a page clicks its own ticks. ui_clicker sounds it (0: silent): q2ui's is
 * demo's click, down its pipe (ui/player.c player_click). */
void ui_click(int button);
extern void (*ui_clicker)(int button);
void ui_watch(int fd, void (*fn)(void)); /* fn when fd is readable (a daemon's message); sixteen at most */
void ui_unwatch(int fd);                 /* fd is no longer waited on (a connection that ended) */
extern const char *ui_backlight;         /* the folder of backlights whose state says the screen is dark */
int ui_dark(void);                       /* the screen is dark: Q2SYS/1's word, demo's dark=, else the backlight's */
/* One pass by hand, for the bench and a script: events, the step at the frame's time, the draw,
 * the frame. Returns whether a frame went out. */
int ui_frame(const input_t *in, int n);
struct ui_stats {
    unsigned frames, ticks, px, dropped, late;
    uint64_t draw_ns, show_ns, draw_max_ns; /* CPU time */
    uint64_t work_us, work_max_us;          /* wall time from a frame's start to its pan */
    unsigned over_budget;                   /* frames whose work, its pan left out, took longer than a refresh, */
    unsigned cpu_over;                      /* and those whose CPU time did: the machine's other work left out */
    unsigned hist[8];                       /* work time: under 1, 2, 4, 8, 12, 16.7, 33 ms, more */
    unsigned streak, streak_max;            /* frames shown back to back, each a refresh after the last */
    unsigned deferred;                      /* glyphs a frame left for the next to make */
    uint64_t pan_us, pan_max_us, wait_us;   /* the pan ioctl's time, inside work_us, and the wait for the refresh after it */
    unsigned gap_hist[5]; /* a motion's frames by the time since the one before: 1, 2, 3, 4 to 6 refreshes, more */
    unsigned gaps_paused; /* frames after a motion that had ended by its own steps (ui.c account): no gap counted */
    uint64_t gap_max_us;  /* the longest gap counted as refreshes missed, and the UI_SINCE_* flags of its wait */
    unsigned gap_max_flags;
    unsigned start_late; /* gaps of a refresh and a half or more in the first turns after the start: not a motion's */
    /* from a wheel's or a finger's input to the end of the pan of the first frame shown after it (its first refresh
     * on the panel): under 17, 33, 50, 67, 100 ms, more; how many, their sum and the longest */
    unsigned lat_hist[6], lat_n;
    uint64_t lat_us, lat_max_us;
    unsigned fb_checks, fb_overlaps; /* frames whose page was read back before the turn, and those it said is shown */
    /* --lcd-site: DC_LAY0_SITE readings (two a turn), those outside both pages, and the turns that wrote into the page
     * it was read from */
    unsigned lcd_site_reads, lcd_site_outside, lcd_site_into;
};
/* The frame log (--frame-log N): the last N frames shown, each its time since the one before, the scroll of the list
 * it drew (in 16ths of a pixel; INT32_MIN, none) and FRAME_* flags, for a device read of judder and tearing */
struct ui_frame_rec {
    uint32_t dt_us;
    int32_t scroll16;
    uint32_t flags;
    uint32_t work_us;      /* from the frame's start to the end of its pan (ui.c account) */
    uint32_t glyphs;       /* glyphs made for it (text.c) */
    uint32_t glyph_max_us; /* the slowest of them */
    uint32_t faults;       /* major page faults since the frame before (a mapped font's pages read from flash) */
    uint32_t pan_us;       /* its pan ioctl's time, the refresh wait included: work_us less this is the frame's own */
    uint32_t loop_us;      /* the loop's own work since the frame before: the watches' readers and the second's hook */
};
#define FRAME_MOVING 1  /* the frame asked for the next */
#define FRAME_OVERLAP 2 /* the panel said the page this frame was turned onto was the one shown */
#define FRAME_DEFER 4   /* glyphs were left for the next frame */
#define FRAME_PAGE1 8   /* turned onto page 1 (else 0) */
#define FRAME_INPUT 16  /* input came in for it */
#define FRAME_SCANNED 32 /* its turn wrote into the page the display controller was reading (--lcd-site) */
#define FRAME_SECOND 64  /* the once-a-second hook (ui_on_second: the stats file) ran since the frame before */
extern struct ui_frame_rec *ui_frame_log;
extern unsigned ui_frame_log_n, ui_frame_log_at; /* the log's length and the next slot, oldest first from there */
extern double list_drawn_scroll;                 /* list.c: the scroll of the list drawn last (list_drawn: one was) */
extern int list_drawn;
#define UI_SINCE_REST 1   /* the loop waited at rest (nothing due, no motion asked for) */
#define UI_SINCE_EMPTY 2  /* a step drew nothing */
#define UI_SINCE_DEFER 4  /* the frame before left glyphs to be made */
#define UI_SINCE_SLIDE 8  /* the frame before was a slide's */
#define UI_SINCE_INPUT 16 /* input came in */
extern struct ui_stats ui_stats;
void ui_stats_reset(void);

/* ---- theme.c ---- */
#include "../patch/tokens.h"
extern int ui_light;         /* Light Mode */
uint32_t tok(int token);     /* the token's colour in the half in use */
/* the accent chosen: { selection, pressed, text on black, text on white, caption on it } */
extern const uint32_t *ui_accent;
int accent_set_value(int v); /* [IPOD] UI_ACCENT_KEY's value; 1 when the accent changed */
#define RGB_ACCENT (ui_accent[0])
#define RGB_ACCENT_PRESSED (ui_accent[1])
/* A size in em px as the design states it (docs/oled-design.md#type) to the line height text.c
 * takes: Inter's line is 1.21 em. */
#define TYPE(px) ((int)((px) * 1.21f + 0.5f))

/* ---- notice.c: Q2UI/1, toasts and questions from other programs (docs/ui-rewrite.md#notices) ---- */
extern const char *notice_socket; /* where it is served (/run/vamp/ui.sock; "" none) */
extern const char *notice_apps;   /* q2pkgd's apps file, "<id> <uid> <permissions>" a line: who asks */
extern const char *notice_card;   /* the card the apps' folders (.<id>/q2app) are on */
extern void (*notice_said)(const char *line); /* a script's own lines' answers (notice_line) */
extern void (*notice_wake)(void);             /* light the dark screen for a question */
extern void (*notice_changed)(void);          /* a card or a toast came or went (the stats file written again) */
int notice_open(void);
void notice_close(void);
void notice_line(const char *line); /* a command as a connection's, its answers to notice_said (a script) */
int notice_event(const input_t *e); /* 1: the card took it */
void notice_step(void);
void notice_draw(rect_t r);         /* over the page and the status row */
void notice_toast(const char *text); /* q2ui's own toast, queued as a connection's */
int notice_ask_local(const char *title, const char *body, const char *const *choices, int n, int initial, int danger,
                     void (*done)(int n)); /* q2ui's own question: done told the choice from 1, 0 none; -1 full */
int notice_up(void);                /* a question is on the card */
int notice_trace(char *out, size_t room); /* "notice card=ID lit=N level=X toast=on|leaving|- waiting=N"; 0 with none up */
void notice_stats(char *out, size_t room); /* the stats file's "card", "card_from", "toast", "toasts_waiting" */
/* ---- status.c: the status row, an overlay drawn over every page ---- */
void status_draw(rect_t r);
#define HOME_PANE_X 196   /* Home's art from here to the right edge, from the top (page_home.c) */
extern int home_split;      /* Home is laid out Split, its art pane beside the list (page_home.c) */
extern const char *home_order_file; /* demo's Edit Home order (--home-order) */
int status_over_ink(void); /* the ink Home's glyphs over the art took: 1 white, 2 black, 0 none yet */
void status_step(void);
rect_t status_rect(void);
void status_clock_fixed(const char *hhmm);
void status_clock_at(long epoch); /* a script's clock: the time at epoch, in demo's zone and form */
long status_time(void);           /* the time the clock reads */
/* where the battery, Wi-Fi's state and its signal are read (a test's are its own files) */
extern const char *status_power, *status_wifi, *status_wireless, *status_told;
void status_told_changed(void); /* demo's file of the states only it knows changed (now.c's watch) */
const char *status_told_text(void); /* those states' text, the file's or on Vamp OS the services' */
int status_told_value(const char *key, char *out, size_t room); /* demo's word on key; 0 when it said nothing */
int status_wifi_bars(void); /* the link as the row draws it: 0 not joined, 1 to 3 its fan's lit parts, 4 joined with no internet */

/* ---- list.c: the list every menu is ---- */
enum {
    ROW_CHEVRON = 1 << 0, /* opens a page */
    ROW_SWITCH = 1 << 1,  /* an on/off setting; ROW_ON its state */
    ROW_ON = 1 << 2,
    ROW_GREY = 1 << 3,    /* disabled: tertiary ink, and the wheel passes over it */
    ROW_PLAYING = 1 << 4, /* the playing song: accent title, level bars at the right */
    ROW_ACCENT = 1 << 5,  /* an action row in the accent (Shuffle) */
    ROW_CHECK = 1 << 6,   /* a picker's current choice */
    ROW_HEADER = 1 << 7,  /* a section header: small tertiary text, never selected */
    ROW_SLIDER = 1 << 8,  /* a level set in its row (docs/oled-design.md#inline-sliders): level, value */
    /* a row editor's (docs/oled-design.md#edit-library): */
    ROW_DIM = 1 << 9,     /* its words in Text Tertiary, still selectable (a hidden row) */
    ROW_CIRCLE = 1 << 10, /* a check circle leading it, filled with a check with ROW_ON, else a ring */
    ROW_LOCK = 1 << 11,   /* ... a locked one, a padlock in it: always shown */
    ROW_GRIP = 1 << 12,   /* a grip in the chevron's place */
    ROW_SOFT = 1 << 13,   /* its marks in Text Secondary, not Primary (a menu's symbols) */
};
typedef struct {
    const char *title;
    const char *detail;    /* inline after the title, smaller and muted (a song's artist) */
    const char *value;     /* at the right column (a setting's value, a playlist song's artist) */
    const char *length;    /* a song's length, small at the right column, the playing song's bars before it (Song Length) */
    const char *sub;       /* the second line of a two-line row */
    const char *caption;   /* said under the title only while the row is selected, the row growing to hold it */
    const char *lead;      /* small text in the leading column (a track number) */
    const image_t *icon;   /* a 20 px glyph leading the row, inked as the text */
    const image_t *art;    /* leading art of a two-line row */
    unsigned flags;
    float level;           /* ROW_SLIDER: its fill, 0 to 1 */
    /* 20 px glyphs at the right column, from it leftwards (a network's signal, then its lock), by the
     * theme's dark-half names ("wifi_3", "lock": oled_<name>_d.png), inked as a value is */
    const char *marks[3];
} ui_row;
typedef struct ui_source {
    int (*count)(void *ctx);
    void (*row)(void *ctx, int i, ui_row *out); /* strings stay valid until the next call */
    void (*chosen)(void *ctx, int i);           /* Centre */
    void (*held)(void *ctx, int i);             /* Centre held; 0: nothing */
    void (*focus)(void *ctx, int i);            /* the selection came to row i; 0: nothing */
    /* A grabbed ROW_SLIDER row turned: steps up (+, clockwise) or down (-), more a tick on a fast spin. The
     * source sets its level and calls list_rows_changed. */
    void (*adjust)(void *ctx, int i, int steps);
    /* Row i's song, its id as demo's now file names it (ui_now.id), else 0; given, the playing song's row is
     * marked (ROW_PLAYING) and Centre on it opens Now Playing, the song playing on */
    int (*song)(void *ctx, int i);
    void *ctx;
    const char *name; /* the page's name in a route; 0: not kept */
    int pinned; /* rows at the top that stay put while the rest scroll under them (Songs' Shuffle) */
    int row_h;  /* 32, or 52 for two-line rows; 0 is 32 */
    int letters; /* 1: sorted by title, so a fast spin jumps by letter */
    rect_t box; /* where the list lives; w 0 is the whole screen under the status row */
    int fade_to; /* where the rows past the list's end have faded into the ground; 0: the screen's foot's (y 311).
                    Given, a list that starts lower than a list page's runs on to the screen's foot (Home's eighth row) */
    int bare;    /* 1: no ground and no fades of its own, over what the page has drawn (a menu sheet's material) */
} ui_source;
typedef struct ui_list ui_list;
/* A picture a row was given (ui_row.art) may have changed or been freed: every list asks its rows
 * with art again before it draws them. Whoever owns the pictures calls it (ui/art.c). */
void list_pictures_changed(void);
void list_rows_changed(void); /* any row's words or marks may have changed: every row is asked again, a switch slides,
                                 and the rows are counted again */
void control_push(void);      /* Control Center (page_control.c), dropped from the top */
void control_pull(const input_t *e); /* ... or pulled down by a finger (ui_on_pull's) */
extern const ui_page page_control;
/* ---- config.c: the settings q2ui reads from demo's config.ini ---- */
extern const char *config_file;
extern const char *library_order_file; /* demo's Edit Library order (ui/page_library.c) */
extern int config_light_forced, config_motion_forced; /* --light, --reduce-motion */
int config_int(const char *section, const char *key, int def);
void config_open(void);      /* read it, and again whenever it is written */
void config_read(void);
void config_light_now(void); /* Light Mode's Auto at this hour */
int config_light_setting(void);  /* [IPOD] LIGHT as the file has it: 0 Off, 1 On, 2 Auto */
int config_motion_setting(void); /* [IPOD] REDUCEMOTION: 1 on */
int config_night_setting(void);  /* [Q2POD] NIGHTSHIFT: 0 Off, 1 On, 2 Auto (from 19:00 to 7:00) */
int config_night_kelvin(void);   /* [Q2POD] NIGHTK, its temperature */
/* the song rows' options (docs/oled-design.md#song-rows), as patch/navigation.h numbers them */
enum { SONG_ARTIST_INLINE, SONG_ARTIST_SELECTED, SONG_ARTIST_RIGHT, SONG_ARTIST_OFF };
enum { SONG_LENGTH_ALBUMS, SONG_LENGTH_ALL, SONG_LENGTH_OFF };
int config_song_artist(void); /* [IPOD] SONGARTIST */
int config_song_length(void); /* [IPOD] SONGLENGTH */
void fb_warm(int kelvin);        /* Night Shift's warming of every pixel turned to the panel; 0 off (fb.c) */
void display_push(void);         /* Settings > Display (page_display.c) */
void playback_push(void);        /* Settings > Playback (page_playback.c) */
void general_push(void);         /* Settings > General and its About (page_general.c) */
void about_push(void);           /* General > About (page_general.c) */
int settings_route(const char *name); /* a page of Settings' by its route name (page_settings.c); 0 none */
void wifi_push(void);            /* Settings > Wi-Fi (page_wifi.c) */
extern const char *wifi_networks_file; /* demo's list of the networks for it (--networks) */
void wifi_networks_changed(void);      /* ... which has changed (now.c's watch) */
extern const char *bt_devices_file;    /* demo's list of the Bluetooth devices (--bt-devices) */
void bt_devices_changed(void);
void bluetooth_push(void);       /* Settings > Bluetooth (page_bluetooth.c) */
extern const char *general_card; /* About's card: --card */
ui_list *list_new(const ui_source *src);
void list_free(ui_list *l);
void list_event(ui_list *l, const input_t *e);
void list_step(ui_list *l);
void list_draw(ui_list *l, rect_t r);
void list_changed(ui_list *l); /* the source's rows changed: lay out again, keep the selection in range */
void list_reload(ui_list *l);  /* rows changed in place (a switch, a value): read again, the switch slides */
void list_select(ui_list *l, int i, int animate);
int list_selected(const ui_list *l);
int list_grabbed(const ui_list *l); /* the ROW_SLIDER row Centre grabbed, the wheel's until set down; -1 none */
int list_set_down(ui_list *l);      /* the grabbed row set down: 1 when one was */
/* A row editor's: the selected row lifted onto a raised card (its words in Inter Medium, in Text Primary), or put
 * down; and the source's row `from` moved to `to`, the selection going with it and the rows it passed sliding aside
 * (docs/oled-design.md#edit-library) */
void list_lift(ui_list *l, int on);
int list_lifted(const ui_list *l);
void list_moved_row(ui_list *l, int from, int to);
float list_scroll(const ui_list *l); /* px scrolled, for a test */
extern int list_motion; /* 0: every move is at its end at once (a bench, Reduce Motion) */
/* 1 (--wheel-scroll follow, the default): while the wheel turns, the rows go on between ticks at the finger's speed;
 * 0 (tick): they glide a tick's rows on at each tick (list.c follow) */
extern int list_wheel_follow;
extern int list_living, list_living_fps; /* the living row: drawn; its frames a second at rest (list.c) */
/* A page that is one list and nothing else. */
extern const ui_page list_page;
ui_list *list_page_list(void *st);
/* the list last stepped, as "sel=N scroll=PX bar=PX lean=PX press=S knob=SHARE grab=N az=L azin=PX cap=PX slide=PX":
 * a script's trace (az: the A-Z index's lit letter, 0 '#', 1 to 26, -1 hidden; cap: how far the selected row has grown to
 * its caption; slide: how far the rows a moved row passed are still from their places, together) */
int list_trace(char *out, size_t room);
int list_rows_dump(const char *path); /* the top list's rows as text, a line each (a script's rows FILE) */
/* ... and those in sight as laid out, a line each: the row, then where its title, detail and length begin and end,
 * where the playing song's bars end (0 for a part it has not) and the length shown, tab-separated (a script's
 * layout FILE) */
int list_layout_dump(const char *path);
int list_last_selected(void); /* that list's selection; -1 with none */

/* ---- lib.c: the library, read from stock's database and the server catalogue ---- */
int lib_open(const char *db, const char *catalogue, int limit);
int lib_songs(void);
const char *lib_title(int i);
const char *lib_artist(int i);
const int *lib_ids(void); /* each song's row id in its table, in the list's order */
const char *lib_album(int i);
int lib_track(int i);
/* a song's length in seconds (a CUE track's, a server row's, or its file's in demo's durations cache), -1 unknown;
 * and as Now Playing writes it ("3:45", "1:02:05"), empty when unknown */
int lib_seconds(int i);
const int *lib_recent(void); /* every song as numbers for lib_title, the newest added first (Recently Added) */
const char *lib_length(int i, char *out, size_t room);
extern const char *lib_durations_file; /* demo's durations cache (--durations) */
enum { LIB_ARTIST, LIB_ALBUM, LIB_GENRE, LIB_KINDS };
int lib_groups(int kind);                             /* how many artists, albums or genres */
const char *lib_group_name(int kind, int g);
const char *lib_group_artist(int g);                  /* an album's artist (its first song's) */
const int *lib_group_songs(int kind, int g, int *n);  /* the group's songs, as numbers for lib_title */
int lib_group_of(int kind, int song);                 /* the group a song is in, -1 with none */
int lib_playlists(int *ids, char (*names)[64], int room); /* the playlists, On-The-Go (100) first while it has songs */
int lib_playlist_songs(int list, int *songs, int room);    /* a playlist's songs in its order, as numbers for lib_title */

/* ---- now.c: what plays ---- */
typedef struct {
    char title[160], artist[160], album[160];
    int playing, paused, pos_ms, len_s, volume, id;
    int shuffle, repeat, hearts; /* the modes (repeat: 0 off, 1 one, 2 all) and the song's stars, -1 unknown */
    int liked;                   /* its like, the heart: 1, 0, -1 none here (a stream) or unknown */
    int radio;                   /* a radio plays (the song's, or the Q2's own): shuffle and repeat are its */
    uint64_t read_us; /* when this was read: the position is that old */
} ui_now;
void now_open(void);              /* watch for changes; a change wakes the loop */
void now_read(void);
const ui_now *now_get(void);
unsigned now_seq(void);           /* moves when anything of it changed */
int now_pos_ms(void);              /* the playing position, ms, gone on by the clock since it was read */
int now_watched(void);            /* whether changes are being watched for (the stats file says) */
const image_t *now_cover(void);   /* the playing song's cover, 0 with none */
/* ---- art.c: album covers for rows, Cover Flow's thumbnails on the card ---- */
#define ART_TRIES 4 /* the artists a row's album is looked for under */
unsigned art_key(const char *artist, const char *album); /* patch/coverflow.c tags_key */
/* the cover under the first of keys there (keys[0] names the ask), else the placeholder; one not
 * read yet is asked for, and the screen drawn again when it comes */
const image_t *art_cover(const unsigned *keys, int n);
extern const char *art_dir;
extern int art_sync; /* read in place, not by the worker: a script's, so its pictures repeat */
unsigned now_cover_seq(void);
extern const char *now_file, *now_cover_file;

/* ---- api.c: Vamp API 1's services, on Vamp OS (--api auto|demo) ---- */
extern const char *api_dir, *api_choice; /* the services' folder (/run/vamp); auto, demo or vamp */
extern int api_vamp;                     /* 1: the services are spoken to, 0: demo's pipe and files */
void api_open(void);
int api_field(const char *line, const char *key, char *out, size_t room); /* key=VALUE, percent-decoded; 0 absent */
int api_play_has(const char *word);                   /* Q2PLAY/1's caps name the word */
int api_play_send(const char *verb);                  /* a verb to Q2PLAY/1; 0 sent */
int api_play_list(const char *head, const int *ids, int n); /* play or queue and its N id lines; 0 sent */
int api_now_overlay(ui_now *n);                       /* Q2PLAY/1's now over the file's; 1 when it said one */
int api_play_live(void);                              /* connected to Q2PLAY/1 */
void api_wait(unsigned ms);                           /* a script's wait for every answer, up to ms */
int api_screen_dark(void);            /* Q2SYS/1's screen: 1 dark, 0 lit, -1 no word (off Vamp OS) */
int api_sys_send(const char *verb);   /* a command to Q2SYS/1 (`screen on`); 0 sent */
int api_screen(int on);               /* Q2SYS/1 `screen on` or `screen off`; 0 sent, -1 off Vamp OS */
int api_told(char *out, size_t room); /* the status row's states from Q2SYS/1, Q2BT/1 and Q2NET/1 as /tmp/q2ui.status has them; 0 off Vamp OS */

/* ---- lyrics.c: a song's lyrics, Q2LIB/1's `lyrics ID` ---- */
enum { LYR_NONE, LYR_ASKING, LYR_PLAIN, LYR_LINE, LYR_WORD };
#define LYRICS_ANSWER_MAX (512u * 1024u) /* q2libd reads 64 KB of text at most; percent-encoded, three times that */
typedef struct {
    int ms;      /* the word's start */
    unsigned at; /* its first byte in the line's text */
} lyr_mark;
typedef struct {
    int at;              /* the line's start, ms; -1 untimed */
    unsigned text, len;  /* its text in lyrics_t.text, well-formed UTF-8 */
    unsigned mark, marks; /* its word marks in lyrics_t.mark */
} lyr_line;
typedef struct {
    int kind, id, n, marks;
    lyr_line *line;
    lyr_mark *mark;
    char *text;
    size_t text_len;
} lyrics_t;
const lyrics_t *lyrics_for(int id); /* song id's lyrics, asked when another song's are held; LYR_ASKING until they come */
unsigned lyrics_seq(void);          /* moves when what lyrics_for holds changes */
const lyrics_t *lyrics_held(void);  /* what lyrics_for holds, asking for nothing */
void lyrics_wait(unsigned ms);      /* a script's wait for the answer asked for, up to ms */
int lyrics_line_at(const lyrics_t *l, int ms);

/* ---- player.c: the player, through demo while demo owns it ---- */
int player_play(const int *ids, int n, int at, int shuffle); /* the list, starting at its song `at`; 0 sent */
int player_send(const char *verb);                           /* toggle, next, previous, ...; 0 sent */
int player_enqueue(const int *ids, int n, int next);           /* the songs added as Play Next (1) or Play Last (0); 0 sent */
extern const char *player_queue, *player_list;               /* the FIFO and the list file (a test's are its own) */
int player_set(const char *name, int value); /* a setting of demo's, down q2ui's own pipe; 0 sent */
void player_click(int button);               /* demo's key click for a tick (0) or a press (1), down the same pipe */
int player_line(const char *verb, const char *arg); /* "scan", "join" a network, "btscan", "btlink" a MAC...: the same pipe; 0 sent */
extern int player_ctl;                       /* that pipe's write end (--ctl FD), or -1 */

/* ---- page_edit.c: a list's own order and choice of rows (demo's Edit Library file), and its editor ---- */
#define ORDER_MAX 64 /* the file's bytes, its letters and marks and a NUL: patch/library_edit.c LR_MAX */
enum { EDIT_SHOWN, EDIT_HIDDEN, EDIT_LOCKED }; /* a row's own rule until the user shows or hides it */
typedef struct {
    char letter; /* its letter in the file, by its place in the list's default order */
    const char *title;
    int rule;
} edit_row;
/* The order in the file saved (its bytes, NUL-ended) against the default def: out the rows' letters (ORDER_MAX), marks
 * each row's '+', '-' or 0 by its place in def (ORDER_MAX; may be 0); returns out's length. order_read reads file. */
int order_resolve(const char *def, const char *saved, char *out, char *marks);
int order_read(const char *file, const char *def, char *out, char *marks);
int order_shows(int mark, int rule); /* whether a row with that mark and rule shows */
/* The editor over rows, those of def's a page has: its order saved to file as it changes; done is called as it closes */
void edit_push(const char *def, const char *file, const edit_row *rows, int n, void (*done)(void));

/* ---- pages ---- */
void songs_push(void);
void song_menu(int song); /* a song's hold menu (page_library.c), song a number for lib_title */
/* ---- page_menu.c: a menu sheet over the page (docs/oled-design.md#menus) ---- */
typedef struct {
    const char *title;
    const char *icon; /* the theme's menu symbol (tools/art.py MENU_ICONS) as icon() names it: "menu_queue" */
} menu_row;
typedef struct {
    const char *name, *line; /* the header: the item's name, and the line under it (0: no header) */
    const image_t *cover;    /* its cover, 0 for the plain tile */
    const menu_row *rows;
    int n;
    void (*picked)(void *ctx, int i); /* Centre on row i, once the sheet has gone and the page is on top again */
    void *ctx;
} menu_t;
void menu_push(const menu_t *m); /* the strings and rows must outlive the sheet */
void library_push(void);
int library_route(const char *name); /* open the Library's page of that route name; 1 when it was one */
extern const ui_page page_home, page_now, page_lyrics;
/* Leave q2ui for a page of demo's it has not got (docs/ui-rewrite.md#cutover): demo opens the page
 * named, takes the screen, and starts q2ui again where it was when the page is left. */
void ui_demo_page(const char *name);
void settings_push(void);         /* Settings, its list (page_settings.c) */
extern const ui_page page_depth; /* the panel's colour depth test card */
void gallery_push(void);         /* every kind of row, as a list to try them on */
extern const ui_page page_card;  /* a card of layers the wheel pushes */

#endif
