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
typedef struct {
    float gx, gy;   /* the gleam's centre from the box's, px */
    float rx, ry;   /* its radii */
    float strength; /* 0 to 1 of white at its centre; 0: none */
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
int text_warm(unsigned budget_us); /* make common glyphs ahead of need; 1 while more are left */
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
void ui_on_stack(void (*fn)(void));      /* called when a page has opened or closed */
void ui_on_hold(int key, void (*fn)(void)); /* a key held, on any page, is fn's and not the page's (Return: Control Center) */
void ui_watch(int fd, void (*fn)(void)); /* fn when fd is readable (a daemon's message); four at most */
extern const char *ui_backlight;         /* the folder of backlights whose state says the screen is dark */
/* One pass by hand, for the bench and a script: events, the step at the frame's time, the draw,
 * the frame. Returns whether a frame went out. */
int ui_frame(const input_t *in, int n);
struct ui_stats {
    unsigned frames, ticks, px, dropped, late;
    uint64_t draw_ns, show_ns, draw_max_ns; /* CPU time */
    uint64_t work_us, work_max_us;          /* wall time from a frame's start to its pan */
    unsigned over_budget;                   /* frames whose work took longer than a refresh, */
    unsigned cpu_over;                      /* and those whose CPU time did: the machine's other work left out */
    unsigned hist[8];                       /* work time: under 1, 2, 4, 8, 12, 16.7, 33 ms, more */
    unsigned streak, streak_max;            /* frames shown back to back, each a refresh after the last */
    unsigned deferred;                      /* glyphs a frame left for the next to make */
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
/* where the battery, Wi-Fi's state and its signal are read (a test's are its own files) */
extern const char *status_power, *status_wifi, *status_wireless, *status_told;
void status_told_changed(void); /* demo's file of the states only it knows changed (now.c's watch) */
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
    float level;           /* ROW_SLIDER: its fill, 0 to 1 */
} ui_row;
typedef struct ui_source {
    int (*count)(void *ctx);
    void (*row)(void *ctx, int i, ui_row *out); /* strings stay valid until the next call */
    void (*chosen)(void *ctx, int i);           /* Centre */
    void (*held)(void *ctx, int i);             /* Centre held; 0: nothing */
    void (*focus)(void *ctx, int i);            /* the selection came to row i; 0: nothing */
    /* A grabbed ROW_SLIDER row turned: steps up (+) or down (-), more a tick on a fast spin. The
     * source sets its level and calls list_rows_changed. */
    void (*adjust)(void *ctx, int i, int steps);
    void *ctx;
    const char *name; /* the page's name in a route; 0: not kept */
    int pinned; /* rows at the top that stay put while the rest scroll under them (Songs' Shuffle) */
    int row_h;  /* 32, or 52 for two-line rows; 0 is 32 */
    int letters; /* 1: sorted by title, so a fast spin jumps by letter */
    rect_t box; /* where the list lives; w 0 is the whole screen under the status row */
} ui_source;
typedef struct ui_list ui_list;
/* A picture a row was given (ui_row.art) may have changed or been freed: every list asks its rows
 * with art again before it draws them. Whoever owns the pictures calls it (ui/art.c). */
void list_pictures_changed(void);
void list_rows_changed(void); /* any row's words or marks may have changed: every row is asked again, a switch slides,
                                 and the rows are counted again */
void control_push(void);      /* Control Center (page_control.c) */
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
void display_push(void);         /* Settings > Display (page_display.c) */
void playback_push(void);        /* Settings > Playback (page_playback.c) */
void general_push(void);         /* Settings > General and its About (page_general.c) */
void about_push(void);           /* General > About (page_general.c) */
int settings_route(const char *name); /* a page of Settings' by its route name (page_settings.c); 0 none */
void wifi_push(void);            /* Settings > Wi-Fi (page_wifi.c) */
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
float list_scroll(const ui_list *l); /* px scrolled, for a test */
extern int list_motion; /* 0: every move is at its end at once (a bench, Reduce Motion) */
/* A page that is one list and nothing else. */
extern const ui_page list_page;
ui_list *list_page_list(void *st);
/* the list last stepped, as "sel=N scroll=PX bar=PX lean=PX press=S knob=SHARE grab=N": a script's trace */
int list_trace(char *out, size_t room);
int list_rows_dump(const char *path); /* the top list's rows as text, a line each (a script's rows FILE) */
int list_last_selected(void); /* that list's selection; -1 with none */

/* ---- lib.c: the library, read from stock's database and the server catalogue ---- */
int lib_open(const char *db, const char *catalogue, int limit);
int lib_songs(void);
const char *lib_title(int i);
const char *lib_artist(int i);
const int *lib_ids(void); /* each song's row id in its table, in the list's order */
const char *lib_album(int i);
int lib_track(int i);
enum { LIB_ARTIST, LIB_ALBUM, LIB_KINDS };
int lib_groups(int kind);                             /* how many artists, or albums */
const char *lib_group_name(int kind, int g);
const char *lib_group_artist(int g);                  /* an album's artist (its first song's) */
const int *lib_group_songs(int kind, int g, int *n);  /* the group's songs, as numbers for lib_title */
int lib_playlists(int *ids, char (*names)[64], int room); /* the playlists, On-The-Go (100) first while it has songs */
int lib_playlist_songs(int list, int *songs, int room);    /* a playlist's songs in its order, as numbers for lib_title */

/* ---- now.c: what plays ---- */
typedef struct {
    char title[160], artist[160], album[160];
    int playing, paused, pos_ms, len_s, volume, id;
    int shuffle, repeat, hearts; /* the modes (repeat: 0 off, 1 one, 2 all) and the song's hearts, -1 unknown */
    uint64_t read_us; /* when this was read: the position is that old */
} ui_now;
void now_open(void);              /* watch for changes; a change wakes the loop */
void now_read(void);
const ui_now *now_get(void);
unsigned now_seq(void);           /* moves when anything of it changed */
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

/* ---- player.c: the player, through demo while demo owns it ---- */
int player_play(const int *ids, int n, int at, int shuffle); /* the list, starting at its song `at`; 0 sent */
int player_send(const char *verb);                           /* toggle, next, previous, ...; 0 sent */
extern const char *player_queue, *player_list;               /* the FIFO and the list file (a test's are its own) */
int player_set(const char *name, int value); /* a setting of demo's, down q2ui's own pipe; 0 sent */
extern int player_ctl;                       /* that pipe's write end (--ctl FD), or -1 */

/* ---- pages ---- */
void songs_push(void);
void library_push(void);
int library_route(const char *name); /* open the Library's page of that route name; 1 when it was one */
extern const ui_page page_home, page_now;
/* Leave q2ui for a page of demo's it has not got (docs/ui-rewrite.md#cutover): demo opens the page
 * named, takes the screen, and starts q2ui again where it was when the page is left. */
void ui_demo_page(const char *name);
void settings_push(void);         /* Settings, its list (page_settings.c) */
extern const ui_page page_depth; /* the panel's colour depth test card */
void gallery_push(void);         /* every kind of row, as a list to try them on */
extern const ui_page page_card;  /* a card of layers the wheel pushes */

#endif
