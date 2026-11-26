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
/* bits: the panel link's bits a channel (8, 6 or 5); under 8 the turn dithers down to it */
int fb_open(const char *path, int bits);
void fb_close(void);
void dmg_add(rect_t r); /* r changed: turned and shown with the next frame */
int dmg_count(void);
void dmg_all(void); /* both pages are stale (the screen came back from another program) */
/* The frame's rects, and the last frame's, turned onto the hidden page; then the pan and the wait
 * for the panel's refresh. Returns the pixels turned. */
unsigned fb_present(void);
int fb_page(void);      /* the page on screen, 0 or 1 */
extern int fb_vsync;    /* 0: fb_present does not wait (the bench, a script's clock) */
extern int fb_bits;     /* the panel's bits a channel */
unsigned fb_refresh_us(void); /* the panel's refresh period, from its timings (16667 unknown) */
/* A second canvas a page is drawn into for a slide: canvas_swap makes it the one draw.c draws
 * into and returns the other. */
uint32_t *canvas_swap(uint32_t *other);

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
    float x, v, to;
} spring_t;
/* dt seconds on, exactly (no step size to tune); 1 while it still moves */
int spring_step(spring_t *s, float dt, float response, float damping);
static inline void spring_set(spring_t *s, float x) { s->x = s->to = x, s->v = 0; }
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
typedef struct {
    float gx, gy;   /* the gleam's centre from the box's, px */
    float rx, ry;   /* its radii */
    float strength; /* 0 to 1 of white at its centre; 0: none */
} gleam_t;
void fill_round(rectf_t r, float radius, float tilt, uint32_t rgb, float alpha, const gleam_t *g);
/* the soft shadow a raised box casts: rgb at alpha under r, gone blur px out; r's inside is left
 * for the box itself */
void shadow_round(rectf_t r, float radius, float blur, uint32_t rgb, float alpha);
/* a stroke with round ends, and a disc, smoothed */
void stroke_line(float x0, float y0, float x1, float y1, float width, uint32_t rgb, float alpha);
void fill_disc(float cx, float cy, float radius, uint32_t rgb, float alpha);
/* Rows y0..y1 of the clip scaled toward black: a0/255 of the picture left at y0, a1/255 at y1,
 * dithered so the ramp shows no bands. */
void shade_rows(int y0, int y1, int a0, int a1);
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
 * once; 0 when the folder has none. */
const image_t *icon(const char *name);
extern const char *icon_dir;
/* im over dst, scaled, at a sub-pixel place, bilinear; alpha 0-256; tint >= 0 inks its alpha in
 * that colour instead of its own (an icon). radius rounds dst's corners. tilt as fill_round's. */
void image_draw(const image_t *im, rectf_t dst, float radius, float tilt, unsigned alpha, long tint);

/* ---- text.c ---- */
enum { FACE_REGULAR, FACE_MEDIUM, FACE_FALLBACK, FACES };
int text_open(const char *regular, const char *medium, const char *fallback);
extern int text_whole; /* 1: advances rounded to whole pixels, as AWTK lays text out (a comparison) */
typedef struct {
    uint16_t n;    /* glyphs */
    uint16_t cut;  /* 1: ended in an ellipsis */
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
struct text_stats {
    unsigned glyphs, bytes, made, evictions;
};
extern struct text_stats text_stats;

/* ---- input.c ---- */
enum { IN_NONE, IN_TICK, IN_TUG, IN_KEY_DOWN, IN_KEY_UP, IN_KEY_HOLD };
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
} input_t;
int input_open(const char *dir);
int input_fds(int *fds, int max);
int input_read(input_t *out, int max); /* what has arrived, without waiting */
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
} ui_page;
void ui_push(const ui_page *p, void *arg);
void ui_pop(void); /* the top page closes; the last page's pop ends the program */
void ui_quit(int code);
void ui_mark(rect_t r);  /* r is to be drawn again and shown */
void ui_mark_all(void);
void ui_again(void);     /* something still moves: another frame after this one */
void ui_wake_ms(unsigned ms); /* nothing moves, but step again in ms (a marquee's hold, a fade's start) */
int ui_depth(void);
int ui_moving(void);      /* the last frame asked for another */
int ui_wake_pending(void); /* a wake is set */
extern int ui_reduce_motion;
extern int ui_painting; /* 1 while a frame is drawn: marks and state changes are refused */
int ui_run(void);       /* the loop, until ui_quit; the exit code */
void ui_on_rest(void (*fn)(void));       /* called once each time the loop comes to rest */
void ui_watch(int fd, void (*fn)(void)); /* fn when fd is readable (a daemon's message) */
/* One pass by hand, for the bench and a script: events, the step at the frame's time, the draw,
 * the frame. Returns whether a frame went out. */
int ui_frame(const input_t *in, int n);
struct ui_stats {
    unsigned frames, ticks, px, dropped, late;
    uint64_t draw_ns, show_ns, draw_max_ns; /* CPU time */
    uint64_t work_us, work_max_us;          /* wall time from a frame's start to its pan */
    unsigned over_budget;                   /* frames whose work took longer than a refresh */
    unsigned hist[8];                       /* work time: under 1, 2, 4, 8, 12, 16.7, 33 ms, more */
    unsigned streak, streak_max;            /* frames shown back to back, each a refresh after the last */
};
extern struct ui_stats ui_stats;
void ui_stats_reset(void);

/* ---- theme.c ---- */
#include "../patch/tokens.h"
extern int ui_light;         /* Light Mode */
uint32_t tok(int token);     /* the token's colour in the half in use */
#define RGB_ACCENT 0x2160d8
#define RGB_ACCENT_PRESSED 0x4080f0
/* A size in em px as the design states it (docs/oled-design.md#type) to the line height text.c
 * takes: Inter's line is 1.21 em. */
#define TYPE(px) ((int)((px) * 1.21f + 0.5f))

/* ---- status.c: the status row, an overlay every page draws last ---- */
void status_draw(rect_t r);
void status_step(void);
rect_t status_rect(void);
void status_clock_fixed(const char *hhmm);

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
};
typedef struct {
    const char *title;
    const char *detail;    /* inline after the title, smaller and muted (a song's artist) */
    const char *value;     /* at the right column (a setting's value, a length) */
    const char *sub;       /* the second line of a two-line row */
    const char *lead;      /* small text in the leading column (a track number) */
    const image_t *icon;   /* a 20 px glyph leading the row, inked as the text */
    const image_t *art;    /* leading art of a two-line row */
    unsigned flags;
} ui_row;
typedef struct ui_source {
    int (*count)(void *ctx);
    void (*row)(void *ctx, int i, ui_row *out); /* strings stay valid until the next call */
    void (*chosen)(void *ctx, int i);           /* Centre */
    void (*held)(void *ctx, int i);             /* Centre held; 0: nothing */
    void (*focus)(void *ctx, int i);            /* the selection came to row i; 0: nothing */
    void *ctx;
    int pinned; /* rows at the top that stay put while the rest scroll under them (Songs' Shuffle) */
    int row_h;  /* 32, or 52 for two-line rows; 0 is 32 */
    int letters; /* 1: sorted by title, so a fast spin jumps by letter */
    rect_t box; /* where the list lives; w 0 is the whole screen under the status row */
} ui_source;
typedef struct ui_list ui_list;
ui_list *list_new(const ui_source *src);
void list_free(ui_list *l);
void list_event(ui_list *l, const input_t *e);
void list_step(ui_list *l);
void list_draw(ui_list *l, rect_t r);
void list_changed(ui_list *l); /* the source's rows changed: lay out again, keep the selection in range */
void list_select(ui_list *l, int i, int animate);
int list_selected(const ui_list *l);
float list_scroll(const ui_list *l); /* px scrolled, for a test */
extern int list_motion; /* 0: every move is at its end at once (a bench, Reduce Motion) */
/* A page that is one list and nothing else. */
extern const ui_page list_page;
ui_list *list_page_list(void *st);
/* the list last stepped, as "sel=N scroll=PX bar=PX lean=PX press=S": a script's trace */
int list_trace(char *out, size_t room);

/* ---- lib.c: the library, read from stock's database and the server catalogue ---- */
int lib_open(const char *db, const char *catalogue, int limit);
int lib_songs(void);
const char *lib_title(int i);
const char *lib_artist(int i);

/* ---- pages ---- */
void songs_push(void);
extern const ui_page page_depth; /* the panel's colour depth test card */

#endif
