/* q2ui: the menu UI as a program of Vamp's own (docs/ui-rewrite.md). This is the spike: one list,
 * Songs, drawn on /dev/fb0 and moved by the wheel. Nothing here is stock's or AWTK's.
 *
 * The frame: a 375 x 320 landscape canvas in memory, drawn only inside the rects a change marked
 * (dmg_add), then those rects turned onto the panel's hidden page and panned to, one frame a
 * refresh (fb.c). State changes first, then one draw of what changed: nothing is drawn from an
 * input handler. */
#ifndef Q2UI_H
#define Q2UI_H

#include <stdint.h>

#define SCREEN_W 375
#define SCREEN_H 320

typedef struct {
    int x, y, w, h;
} rect_t;

/* fb.c: the panel */
extern uint32_t *canvas; /* SCREEN_W x SCREEN_H, 0xffRRGGBB */
int fb_open(const char *path);
void fb_close(void);
void dmg_add(rect_t r); /* r changed: drawn and shown with the next frame */
int dmg_count(void);
rect_t dmg_at(int i);
void dmg_clear(void);
/* The frame's rects, and the last frame's, turned onto the hidden page; then the pan and the wait
 * for the panel's refresh. Returns the pixels turned. */
unsigned fb_present(void);
int fb_page(void);   /* the page on screen, 0 or 1 */
extern int fb_vsync; /* 0: fb_present does not wait (the bench) */

/* draw.c: everything clips to the rect being drawn */
void clip_set(rect_t r);
rect_t clip_get(void);
int rect_meet(rect_t a, rect_t b, rect_t *out);
void fill(rect_t r, uint32_t rgb);
void fill_round(rect_t r, int radius, uint32_t rgb);
void fill_alpha(rect_t r, uint32_t rgb, unsigned alpha); /* alpha 0-255 */
void shade_rows(int y0, int y1, int a0, int a1); /* rows y0..y1 scaled from a0/255 to a1/255 */
void blit_a8(const uint8_t *bits, int w, int h, int x, int y, uint32_t rgb);

/* text.c */
enum { FACE_REGULAR, FACE_MEDIUM, FACE_FALLBACK, FACES };
int text_open(const char *regular, const char *medium, const char *fallback);
typedef struct {
    uint16_t n;    /* glyphs */
    int16_t w;     /* the ink's advance, px */
    uint32_t made; /* the glyph cache's generation the run was laid out in */
    struct run_glyph {
        const struct glyph *g;
        int16_t x;
    } at[80];
} run_t;
/* s (UTF-8) laid out at px in face, cut to max px with an ellipsis when wider (max < 0: never) */
void run_make(run_t *r, int face, int px, const char *s, int max);
int run_stale(const run_t *r);
/* the run's pen at x, its line box (AWTK's: px high) from y */
void run_draw(const run_t *r, int x, int y, uint32_t rgb);
void text_frame(void); /* a frame is over: the cache's clock moves on */
struct text_stats {
    unsigned glyphs, bytes, made, evictions;
};
extern struct text_stats text_stats;

/* input.c */
enum { IN_NONE, IN_TICK, IN_KEY_DOWN, IN_KEY_UP };
enum {
    KEY_UI_CENTRE = 116,
    KEY_UI_RETURN = 103,
    KEY_UI_PLAY = 108,
    KEY_UI_NEXT = 163,
    KEY_UI_PREV = 165
};
typedef struct {
    int type, value; /* IN_TICK: +1 down the list, -1 up; a key: its code */
    unsigned ms;
} input_t;
int input_open(const char *dir);
int input_fds(int *fds, int max);
int input_read(input_t *out, int max); /* what has arrived, without waiting */

/* songs.c: the library's songs, read once */
typedef struct {
    int n;
    const char *(*title)(int i);
    const char *(*artist)(int i);
} source_t;
int songs_open(const char *db, const char *catalogue, source_t *out, int limit);

/* list.c */
void list_open(const source_t *src);
void list_open_again(void); /* everything marked, as a page just opened */
void list_tick(int dir, unsigned ms);
void list_step(unsigned ms); /* the motion's state at ms; marks what it changes */
void list_draw(rect_t r);    /* everything of the page inside r */
void list_clock(const char *hhmm);
int list_dirty(rect_t *out, int max); /* the rects to draw now; taking them empties the list */
int list_wait_ms(unsigned ms);
extern int list_copy_scroll; /* 1: a scroll moves the drawn rows and draws only what is new */
enum { LIST_MOTION_OFF, LIST_MOTION_SPRING, LIST_MOTION_EVERY };
extern int list_motion;
int list_selected(void);

unsigned now_ms(void);
uint64_t cpu_ns(void);

#endif
