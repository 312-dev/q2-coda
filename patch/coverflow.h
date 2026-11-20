#ifndef Q2_COVERFLOW_H
#define Q2_COVERFLOW_H
/* What coverflow.c shares with coverflow_thumb.c and coverflow_speed.c: the page's state, the
 * depth renderer's and the flip's, which the thumb reads, the thumb's follower, which the renderer
 * reads, and the poses and columns the arch and the row at speed draw with. */
#include "offsets.inc"
#include "peq.h"
#ifndef PEQ_HOST
#include "stock.h"
#endif
#include "chapters.h"
#include "library_scope.h"
#include "playback.h"
#include "wheel_accel.h"
#include "xiph.h"

enum { RATED_ALBUMS = 1, ALBUM_TRACKS, PLAYLISTS }; /* cf.scoped */
/* after the albums: the rated albums' Sort; none after the library's, whose Sort and Refresh are
 * Display settings' Coverflow Sort and Refresh Coverflow (coverflow_sort_set,
 * coverflow_refresh_art), nor after the playlists */
#define CARDS (cf.scoped && cf.scoped != PLAYLISTS)
#define ABS(x) ((x) < 0 ? -(x) : (x))

typedef struct {
    char *track; /* the album's first track, copied so the thread never reads stock deques */
    unsigned key;
    int shared; /* another album has a track in this folder, so its cover.jpg may be theirs */
} job_t;
typedef struct {
    void *page, *body, *covers, *slide, *name, *artist, *title;
    void *albums, *tracks; /* our copies of the stock query results; albums in the Sort's order */
    void *stock;           /* the albums in stock order, which albums is, or is sorted from */
    int sort, sort_read;   /* the Sort card's order (SORT_*), from SORT_FILE once */
    int year_old, artists; /* the Year order and Artists settings albums was sorted in */
    int on_sort;           /* the next load centres the Sort card */
    int resort; /* Display settings changed the library's order: the next open reads it */
    job_t *jobs;
    unsigned long thread;
    unsigned timer, title_timer; /* title_timer: the captions follow the centre cover mid-move */
    int titled;                  /* the album the captions name, + 1 */
    int offline;                 /* that album can't play now: its captions greyed (caption_mark) */
    unsigned saved_album, albums_gen; /* albums_gen: library_gen when albums was queried */
    int screen, album, running;
    volatile int done, total, cancel;
    /* Scoped (RATED_ALBUMS, ALBUM_TRACKS or PLAYLISTS, else 0): the rated albums
     * (coverflow_rated), stock holding them in the library's order and sort their own order, one
     * album's tracks alone (coverflow_goto_album), or the playlists, albums holding their
     * containers (coverflow_playlists); all_albums, all_stock and all_sort the library's, back on
     * close. No saved album; hearts gives a rated album's. */
    int scoped, all_sort;
    void *all_albums, *all_stock;
    int (*hearts)(void *);
    int count; /* the covers' albums and cards, SLIDE_INDEX's range (slide_count) */
} cf_page_t;
extern cf_page_t cf;

/* On the card, beside stock's own cover cache (/mnt/mmc/.sldp); playlist_art.c's files too. */
#define ART_DIR PEQ_ROOT "/mnt/mmc/.coverflow"
#define PL_BUILDING ART_DIR "/pl-building" /* playlist_art.c's art_note */
#define ART_SIZE 160                       /* a texture's side, texels */
#define CF_TEXELS (ART_SIZE * ART_SIZE)
#define CF_ONE 65536
/* the slide_menu's children with depth: empty, they stand for every album (slide_child_index), as
 * many as stock lays out round the centre, Centre's click on the centre one */
#define CF_POOL 8
#define CF_REACH 3 /* ring slots either side of the centre that can show (fading in) or preload */
#define CF_EYE16 (16 * cfs.eye) /* the eye's distance, 1/16 px */
#define CF_HALF16 (16 * ART_SIZE / 2)
#ifndef ART_FFMPEG
#define ART_FFMPEG "/usr/bin/ffmpeg"
#endif
#define CF_WIDE_REACH 7 /* CF_REACH under a thumb theme, for the row at speed */
#define CF_WIDE (2 * CF_WIDE_REACH + 1)
#define FX_POOL CF_WIDE /* the texture pool's slots at most: fx_base()'s under a thumb theme */
extern cf_style_t cfs;  /* the active theme's Cover Flow values, copied at page open (cf_restyle) */
/* A right-hand cover t (16.16, >= 0) from the visual position: centre xc and depth zc (1/16 px),
 * rotation c, s (16.16), brightness (of 256) and the eye it is seen from (1/16 px). Left covers
 * mirror it. */
typedef struct {
    int xc, zc, c, s, bright, eye;
} pose_t;

/* The frame being drawn, nearest cover first: each pixel's alpha byte holds the coverage so far
 * (255 opaque), and a layer only fills what is left, which composites as drawing back to front
 * would. Per column, rows solid_top to solid_bottom are known opaque and skipped outright, so the
 * hidden parts of side covers cost nothing. */
typedef struct {
    unsigned *d;
    int pitch;
    short solid_top[CF_VIEW_W], solid_bottom[CF_VIEW_W];
} shelf_t;

/* The depth renderer's state: the frame, the textures of the covers around the visual position
 * and the placeholder's. No frame: the flat fallback (stock images on the slide_menu). */
typedef struct {
    void *frame;                     /* bitmap_t *, CF_VIEW_W x cfs.view_h BGRA8888 */
    unsigned *tex;                   /* fx_base()'s slots, then the placeholder */
    int album[FX_POOL];              /* each slot's album + 1; 0 is empty */
    const unsigned *art[FX_POOL];    /* its texture: the slot's own, or the placeholder */
    int c, frac, drawn, light, lead; /* the position and Light Mode the frame holds, once drawn */
    int arch;                        /* and how far its arch had formed (16.16) */
    int todo;                        /* a cover wanted still waits for its load (FX_DECODES) */
} cf_depth_t;
extern cf_depth_t fx;

/* The covers held by the thumb (docs/internals.md#coverflow-thumb): one follower per UI pass, in
 * albums from SLIDE_INDEX, its speeds a ms. on: it owns the offset, until it commits. */
typedef struct {
    float pos, vel, goal, lead, lead_v, rate, tau, ticks, seen, seen_lead;
    unsigned at, tick_at, edge_at;
    int raw, on, finger, held, dir, dead, early, clicked, bounced;
    int tick_acc; /* travel past the last whole tick, units x ticks_turn x Wheel Sensitivity
                     (th_turn) */
    int rebased; /* albums th_rebase has moved SLIDE_INDEX on by, in all (test/coverflow.py's count)
                  */
    float arch, seen_arch; /* how far the arch has formed, 0 to 1 (th_arch) */
    wheel_accel_t wa; /* the ticks' momentum, as the lists' (wheel_accel.c) */
} cf_thumb_t;
extern cf_thumb_t th;

/* coverflow.c */
unsigned album_key(void *r);
unsigned tags_key(const char *artist, const char *album);
unsigned album_ident(const char *album, const char *album_artist, const char *path);
int album_art(const char *track, const char *dst, int shared);
void art_note(const char *note, unsigned key, const char *track);
void art_strike(const char *note);
int ff_wait(int pid, const char *tmp, const char *dst, int *exited);
int ff_thumb(const char *src, const char *dst, int w, int h);
char *art_path(char *out, unsigned key, const char *suffix);
int cf_decode(const char *url, unsigned *out, int unload);
/* the texture pool (fx_window): the shelf's slots, then the placeholder */
int fx_base(void);
#define FX_PLACEHOLDER (fx.tex + fx_base() * CF_TEXELS)
unsigned *fx_buf(int i);
/* the depth renderer, which the arch and the row draw with (coverflow_speed.c) */
extern const int cf_cos90[17];
void cf_restyle(void);
void turn(int a, int other, pose_t *p);
void pose(int t, pose_t *p);
int project(const pose_t *p, int u);
int unproject(const pose_t *p, int dx, int *u, int *h);
void draw_posed(shelf_t *f, const pose_t *p, int side, const unsigned *tex);
void draw_quad(shelf_t *f, int xl, int xr, int hl, int hr, const unsigned *tex, int bright);
int cf_floor(float x);
int cf_round(float x);
float cf_smooth(float x);
int coverflow_bounded(void *w);
int covers_live(void);
float ff_decay(float s);
int stride_of(void *s);
int slide_count(void *w);
void *slide_child(void *w, int i);
int slide_child_index(void *w, int j);
void titles(unsigned c);

/* coverflow_speed.c */
int coverflow_window(int frac);
void draw_row(shelf_t *f, int frac, const unsigned *const *ring);
void arch_pose(int t, pose_t *p);

/* Optional browsing position. */
int coverflow_position_enabled(void);
void coverflow_position_paint(void *w, void *canvas);
void coverflow_position_reset(void);
#endif
