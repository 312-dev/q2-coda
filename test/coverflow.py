#!/usr/bin/env python3
"""Run patch/coverflow.c's art cache on the host: the library query, the modal art build on a real
pthread, the stock art calls and their locks. Built 32-bit (-m32, like test/peq.py's player) so the
stSongInfo pointer offsets hold. The stock calls are stubbed; UI widgets are plain records."""
import ast
import json
import pathlib
import re
import sqlite3
import subprocess
import tempfile
import sys; sys.path.insert(0, sys.path[0] + '/../tools')  # tools/ first
from build import FUNCTIONS, ROOT, font_indices
from peq import LIBC

PROTOTYPES = {**FUNCTIONS, **LIBC}  # the stock calls the host shims stand in for
# The tick cases' shelf, 1.8.0's: side covers at coverflow.c's first sine table's 60 degrees (CF_ANGLE)
SHELF_60 = {'cf.x1': 112, 'cf.xs': 48, 'cf.z1': 90, 'cf.zs': 20, 'cf.eye': 300, 'cf.tilt': 60, 'cf.horizon': 50, 'cf.swing': 0}
# The thumb cases' page: square covers lower in a taller view, at full brightness, the reflection from CF_REFLECT_TOP,
# the captions larger (1.8.0's, as the cases were tuned on)
THUMB_PAGE = {'cf.radius': 0, 'cf.top': 32, 'cf.bright1': 256, 'cf.bright2': 256, 'cf.reflect_top': 90, 'cf.view_h': 242,
              'cf.name_px': 27, 'cf.artist_px': 27, 'cf.text_y': 220, 'cf.artist_y': 26}
# The thumb cases' shelf and follower, 1.8.0's: the door's swing, the eye drawn back mid-turn, no arch
SHELF_180 = {'cf.eye': 94, 'cf.tilt': 89, 'cf.x1': 116, 'cf.xs': 34, 'cf.z1': 77, 'cf.zs': 1, 'cf.horizon': 63, 'cf.swing': 1,
             'cf.lift': 20, 'cf.eye_turn': 150, 'cf.art_persp': 13, 'cf.arch_span': 0}

# coverflow.c, coverflow_thumb.c, coverflow_letters.c, coverflow_loader.c, playlist_art.c, wheel_accel.c and hash.c as one unit, so the shim's
# stubs are defined once
CF_SOURCES = '#include "coverflow.c"\n#include "coverflow_thumb.c"\n#include "coverflow_letters.c"\n#include "coverflow_loader.c"\n#include "playlist_art.c"\n#include "coverflow_position.c"\n#include "corner_cover.c"\n#include "wheel_accel.c"\n#include "hash.c"\n'
SHIM_H = r"""
extern char system_bar[];
""" + ''.join(line + '\n' for line in font_indices()) + r"""
#include <dirent.h>
#include <errno.h>
#include <pthread.h>
#include <sys/stat.h>
#include <sys/time.h>
#include <sys/wait.h>
typedef int (*handler)(void *, void *);
extern pthread_mutex_t shim_parse, shim_play;
int mclGetPlayStatus(void); /* a macro over stock's address on the Q2 */
extern void *shim_dir;
#define parse_cover_mutex ((const unsigned char *)&shim_parse)
#define g_playcover_mutex ((const unsigned char *)&shim_play)
#define tools_pdeq_directory ((const unsigned char *)&shim_dir)
static unsigned char shim_artist_type[4] __attribute__((unused)); /* Artists: Artist */
#define artist_type shim_artist_type
#define pthread_mutex_lock shim_lock
#define pthread_mutex_unlock shim_unlock
int shim_join(pthread_t, void **);
#define pthread_join shim_join
#define statfs shim_statfs
#define __xstat shim_xstat /* o32's stat: st_size word 12, st_mtime word 16 */
int shim_access(const char *, int);
#define access shim_access /* counted: the UI thread's own (pool_library) */
#define tk_snprintf snprintf
extern void *shim_queue;
extern unsigned char shim_covertype;
extern char shim_lastcover[1024], shim_id3[2716];
#define mcl_pdeqplaylist ((const unsigned char *)&shim_queue)
#define g_lastcover_url ((const unsigned char *)shim_lastcover)
#define g_play_id3_info ((const unsigned char *)shim_id3)
#define g_playcover_type shim_covertype
extern unsigned char shim_memory[0x800];
#define g_memory_info shim_memory
#define g_backlight_status 0 /* dark: Home's cover never pans here */
#define g_lockscreen_pageflag 0
int shim_lock(void *), shim_unlock(void *), shim_statfs(const char *, void *);
int shim_xstat(int, const char *, void *);
int tk_str_start_with(const char *, const char *);
const unsigned *widget_get_text(void *);
int canvas_set_font(void *, const char *, unsigned);
float canvas_measure_text(void *, const unsigned *, unsigned);
""" + ''.join(f'{r} {n}({a});\n' for n in """
canvas_fill_rect getAllAlbum getMusicByAlbum toolsQueryDbTable toolsUpdateDbTable toolsGetUserStop album_row toolsThumbSpecCover toolsGetAlbumCover toolsGetExternCover _create_deque deque_init_copy deque_clear
deque_init _deque_push_back deque_pop_back
deque_assign deque_destroy deque_size deque_at window_create widget_factory
widget_factory_create_widget image_create hscroll_label_create set_hscroll_label_attribute hscroll_label_set_only_focus hscroll_label_set_ellipses hscroll_label_stop
slide_menu_set_value slide_menu_item_width list_view_create scroll_view_create list_item_create image_set_draw_type
image_base_set_image widget_load_image widget_unload_image widget_set_name widget_use_style
widget_set_text_utf8 widget_get_text_utf8 widget_add_timer widget_restack widget_animator_prop_create widget_animator_prop_set_params widget_animator_start widget_set_visible widget_get_prop_int widget_set_prop_int
widget_get_prop_str widget_on widget_destroy_children widget_invalidate_force
widget_count_children widget_get_child widget_lookup widget_move_resize widget_resize widget_get_canvas widget_get_visible
canvas_get_clip_rect canvas_set_clip_rect widget_set_sensitive widget_get_type tk_strcmp widget_animator_destroy
timer_add timer_remove navigator_back_to_home navigator_back navigator_window_is_exist window_manager_close_window_force navigator_to_with_context check_forbid_play_no_headset bitmap_create_ex bitmap_destroy bitmap_unlock_buffer
bitmap_lock_buffer_for_read bitmap_lock_buffer_for_write bitmap_get_line_length
canvas_draw_image slide_menu_set_spacer window_manager window_manager_get_top_window window_manager_is_animating time_now_ms
widget_to_local widget_animator_pause window_manager_get_pointer_pressed image_manager image_manager_unload_bitmap_by_name image_manager_unload_all
on_home_music_onclick on_home_stream_onclick widget_layout widget_destroy widget_paint canvas_translate widget_set_prop_str
canvas_offline_create canvas_offline_begin_draw canvas_offline_end_draw canvas_offline_destroy
sqlite3_open sqlite3_exec sqlite3_close sqlite3_progress_handler reset_poweroptions_timer getMusicByPlayList getAllPlayList
""".split() for r, a in [PROTOTYPES[n]]) + r"""
int stock_canvas_set_fill_color(void *, unsigned);
void *shim_calloc(size_t, size_t);
#define calloc shim_calloc
#include <stdio.h>
extern void shim_read(size_t) __attribute__((weak)); /* the tests' clock: half copies take half_ms */
static inline size_t shim_fread(void *p, size_t s, size_t n, FILE *f) {
    if (shim_read) shim_read(s * n);
    return (fread)(p, s, n, f);
}
#define fread(p, s, n, f) shim_fread(p, s, n, f)
#include <semaphore.h>
#include <string.h>
/* The tests' loader: run on a thread of its own, or step by step at each wake-up (shim_wake), and
   a half copy's install takes half_ms on the fake clock (shim_install). */
extern int shim_thread(pthread_t *, void *(*)(void *), void *) __attribute__((weak));
extern int shim_wake(sem_t *) __attribute__((weak));
extern void shim_install(void) __attribute__((weak));
static inline int shim_create(pthread_t *t, const pthread_attr_t *a, void *(*f)(void *), void *x) {
    return shim_thread ? shim_thread(t, f, x) : (pthread_create)(t, a, f, x);
}
static inline int shim_post(sem_t *s) { return shim_wake ? shim_wake(s) : (sem_post)(s); }
/* ponytail: any 25.6 KB memcpy counts as a half copy's install; none other in coverflow.c yet */
__attribute__((weak, noinline)) void *shim_memcpy(void *d, const void *s, size_t n) {
    if (shim_install && n == 80 * 80 * 4) shim_install();
    return (memcpy)(d, s, n);
}
#define pthread_create(t, a, f, x) shim_create(t, a, f, x)
#define sem_post(s) shim_post(s)
#define memcpy(d, s, n) shim_memcpy(d, s, n)
"""

TEST = r"""
#include <assert.h>
#include <math.h>
#include <stdarg.h>
#include <sys/mman.h>
#include "peq.h"
#include "offsets.inc"
#include <stdint.h>
#undef pthread_mutex_lock /* the stubs below record, then take the real lock */
#undef pthread_mutex_unlock
#undef pthread_join /* the payload's joins say so (shim_join); the tests' own are libc's */
#undef access /* the payload's are counted (shim_access); the tests' own are libc's */
#undef pthread_create
#undef sem_post
#undef memcpy
int coverflow_home(void *, void *), coverflow_player_cover(const char *, const char *, int, int);
const char *track_name(char *, unsigned, void *);

/* Cache boundary: the duration service has its own tests, test/fw/test_durations.py. */
int duration_seconds(void *song) { return -1; }
void durations_scanned(void) {}
const char *duration_text(char *text, unsigned size, void *song) {
    if (size) text[0] = 0;
    return text;
}

/* Server boundary: catalogue.c with no server rows, a card library under Local; Cover Flow's server
 * rows and art are test/fw/test_catalogue.py's, against the real catalogue.c. */
int catalogue_has(void) { return 0; }
int catalogue_path(const char *path) { return 0; }
int catalogue_missing(const char *path) { return access(path, 0); } /* a card row: its file gone */
int catalogue_album_unavailable(void *r) { return 0; }
int catalogue_cover(const char *path, char *out, unsigned size) { return 0; }
void catalogue_grey(void *w, int on) {}
int catalogue_row_ink(void *row, unsigned *ink) { return 0; }
void catalogue_mark_paint(void *canvas, int x, int y, unsigned ink) {}

/* Widgets: raw memory first, so the payload's field reads (SLIDE_INDEX) land in it. */
typedef struct { char raw[0x100]; int parent, visible, numb, kids[1024], nkids, bg, fg, right; char type[32], text[160], image[600];
                 handler click; void *ctx; } widget;
static widget w[8192];
static int nw;
static widget *W(void *p) { return (widget *)p; }
static void *make(void *parent, const char *type) {
    assert(nw < 8189); /* w[8190] and w[8191] are the page's handlers */
    widget *x = &w[++nw];
    memset(x, 0, sizeof(*x));
    x->visible = 1;
    snprintf(x->type, sizeof(x->type), "%s", type);
    if (parent) { assert(W(parent)->nkids < 1024); /* the harness's cap: a pool's covers stay far under it */
        x->parent = (int)(W(parent) - w); W(parent)->kids[W(parent)->nkids++] = nw; *(void **)(x->raw + W_PARENT) = parent; }
    return x;
}
static void *placed(void *x, int left, int top, int ww, int h) {
    int *r = (int *)W(x)->raw; /* W_X, W_Y, W_W, W_H */
    r[0] = left; r[1] = top; r[2] = ww; r[3] = h;
    return x;
}
static int window_fail; /* no memory for a page */
/* a page's window: the whole screen (tools/build.py FULL_BLEED) */
void *window_create(void *p, int x, int y, int ww, int h) { return window_fail ? 0 : placed(make(0, "window"), 0, 0, 375, 320); }
void *widget_factory(void) { return (void *)1; }
void *widget_factory_create_widget(void *f, const char *t, void *p, int x, int y, int ww, int h) { return placed(make(p, t), x, y, ww, h); }
#define CREATE(name, type) void *name(void *p, int x, int y, int ww, int h) { return placed(make(p, type), x, y, ww, h); }
CREATE(image_create, "image") CREATE(list_view_create, "list_view")
CREATE(hscroll_label_create, "hscroll_label")
CREATE(scroll_view_create, "scroll_view") CREATE(list_item_create, "list_item")
int image_set_draw_type(void *x, int t) { return 0; }
int image_base_set_image(void *x, const char *s) { snprintf(W(x)->image, 600, "%s", s); return 0; }
/* A zero-length "no art" marker does not decode. Every successful load is unloaded again and
   decodes to art_w x art_h (bitmap_t w @0, h @4). */
static int loads, unloads, theme_placeholder, cover_fail, clear_cover;
static unsigned art_w = 160, art_h = 160;
/* Decoded art (bitmap_t w @0, h @4, line length @8, format @0xe, and here the pixels @0x14):
   RGBA8888, a pattern whose colour comes from the file's path, so every album looks different. */
static unsigned char pixels[16][160 * 160 * 4];
static int next_pixels, reads;
static void pattern(unsigned char *px, unsigned seed) {
    static const unsigned char hue[8][3] = { { 214, 68, 58 }, { 58, 140, 214 }, { 236, 180, 50 }, { 90, 176, 96 },
                                             { 150, 90, 200 }, { 230, 120, 170 }, { 60, 190, 190 }, { 200, 200, 200 } };
    const unsigned char *c = hue[seed % 8];
    for (int y = 0; y < 160; ++y)
        for (int x = 0; x < 160; ++x) {
            int dx = x - 80, dy = y - 80, ring = dx * dx + dy * dy, k = 120 + (x + y) * 135 / 318;
            if (ring < 30 * 30 && ring > 22 * 22) k = 40; /* a ring, off-centre text-like bars */
            if (y > 118 && y < 128 && x > 20 && x < 20 + (int)(seed * 37 % 100)) k = 255;
            if (x < 4 || y < 4 || x > 155 || y > 155) k = 255 - k / 2;
            for (int i = 0; i < 3; ++i) px[4 * (y * 160 + x) + i] = c[i] * k / 255;
            px[4 * (y * 160 + x) + 3] = 255;
        }
}
static unsigned clock_ms; /* the fake clock, time_now_ms's */
static int decode_ms; /* a cache file's decode, on that clock */
int widget_load_image(void *x, const char *url, void *b) {
    struct stat s;
    int themed = theme_placeholder && strncmp(url, "file://", 7);
    int failed = cover_fail || (!themed && (strncmp(url, "file://", 7) || stat(url + 7, &s) || !s.st_size));
    loads += !failed && !themed;
    if (!failed && !themed) clock_ms += decode_ms;
    if (!failed) {
        unsigned *bm = b, seed = 0;
        for (const char *c = strrchr(url, '/') ? strrchr(url, '/') : url; *c; ++c) seed = seed * 31 + (unsigned char)*c; /* the key, not the scratch path */
        unsigned char *px = pixels[next_pixels++ % 16];
        pattern(px, seed % 1000);
        if (themed || clear_cover) memset(px, 0, sizeof pixels[0]); /* transparent theme placeholder */
        bm[0] = art_w, bm[1] = art_h, bm[2] = art_w * 4;
        ((unsigned short *)b)[7] = 1; /* RGBA8888 */
        *(unsigned char **)((char *)b + 0x14) = px;
    }
    return failed;
}
const unsigned char *bitmap_lock_buffer_for_read(void *b) { ++reads; return *(unsigned char **)((char *)b + 0x14); }
int widget_unload_image(void *x, void *b) { ++unloads; return 0; }
static char dropped[600]; /* the last cached bitmap dropped by name */
void *image_manager(void) { return (void *)8; }
int image_manager_unload_bitmap_by_name(void *m, const char *name) { snprintf(dropped, sizeof dropped, "%s", name); return 0; }
static int drops; /* image_manager_unload_all calls */
int image_manager_unload_all(void *m) { ++drops; return 0; }
void set_hscroll_label_attribute(void *x) { assert(!strcmp(W(x)->type, "hscroll_label")); }
int hscroll_label_set_only_focus(void *x, int on) { (void)x, (void)on; return 0; }
int hscroll_label_set_ellipses(void *x, int on) { (void)x, (void)on; return 0; }
static int scroll_stops[8192]; /* each label's hscroll_label_stop calls: its marquee back to the start */
int hscroll_label_stop(void *x) { return ++scroll_stops[W(x) - w], 0; }
int slide_menu_set_value(void *x, int value) { *(int *)(W(x)->raw + SLIDE_INDEX) = value; return 0; }
int widget_set_name(void *x, const char *s) { snprintf(W(x)->type, 32, "%s", s); return 0; }
void page_empty(void *body, const char *name) { (void)body; (void)name; } /* navigation.c: page_list_or only */
int widget_use_style(void *x, const char *s) { return 0; }
int widget_set_text_utf8(void *x, const char *s) { if (s) snprintf(W(x)->text, 160, "%s", s); return 0; } /* stock: NULL keeps the text */
int widget_get_text_utf8(void *x, char *s, unsigned n) { return snprintf(s, n, "%s", W(x)->text) >= (int)n; } /* stock: nonzero when it doesn't fit */
unsigned widget_add_timer(void *x, int (*f)(const void *), unsigned ms) { (void)x, (void)f, (void)ms; return 1; } /* the empty page's poll: not run here */
int widget_restack(void *x, unsigned i) { return 0; }
void *widget_animator_prop_create(void *x, unsigned d, unsigned l, int e, const char *p) { return 0; }
int widget_animator_prop_set_params(void *a, double f, double t) { return 0; }
int widget_animator_start(void *a) { return 0; }
int widget_set_visible(void *x, int v, int r) { W(x)->visible = v; return 0; }
int widget_get_prop_int(void *x, const char *k, int d) { return d; }
int widget_set_prop_int(void *x, const char *k, int v) {
    if (!strcmp(k, "style:normal:bg_color")) W(x)->bg = v;
    if (!strcmp(k, "style:normal:text_color")) W(x)->fg = v;
    return 0;
}
/* widget_set_name writes the type's place, so a window's name reads back from there */
const char *widget_get_prop_str(void *x, const char *k, const char *d) {
    return !strcmp(k, "image") ? W(x)->image : !strcmp(k, "name") ? W(x)->type : d;
}
static handler touch_fn, changed_fn; /* the covers' touch and the slide_menu's change */
static handler foreground_fn; /* the page's, as a window over it closes */
unsigned widget_on(void *x, unsigned type, handler f, void *ctx) {
    if (!x) return 0;
    if (type >= EVT_POINTER_DOWN && type <= EVT_POINTER_ABORT) touch_fn = f;
    if (type == EVT_VALUE_CHANGED) changed_fn = f;
    if (type == EVT_WINDOW_FOREGROUND) foreground_fn = f;
    if (type == EVT_CLICK || type == EVT_KEY_UP || type == EVT_DESTROY) {
        widget *h = type == EVT_CLICK ? W(x) : &w[0] + (type == EVT_KEY_UP ? 8190 : 8191); /* page handlers */
        h->click = f; h->ctx = ctx;
    }
    return 1;
}
int widget_destroy_children(void *x) { W(x)->nkids = 0; return 0; }
static int wm_repaints; /* whole-screen invalidations, from outside a paint */
void *window_manager(void) { return &wm_repaints; }
char system_bar[sizeof(void *)];  /* no bar: the window manager's first child is whatever the test put there */
static void *top_window;
static int animating;
void *window_manager_get_top_window(void *wm) { return top_window; }
int window_manager_is_animating(void *wm) { return animating; }
static unsigned clock_ms, pressed = 1, paused;
unsigned time_now_ms(void) { return clock_ms; }
int window_manager_get_pointer_pressed(void *wm) { return pressed; }
int widget_animator_pause(void *a) { ++paused; return 0; }
static int animators_gone;
int widget_animator_destroy(void *a) { ++animators_gone; return 0; }
int widget_to_local(void *x, void *p) { int *r = (int *)W(x)->raw, *q = p; q[0] -= r[0], q[1] -= r[1]; return 0; }
int slide_count(void *w); /* coverflow.c's: the albums and cards the covers stand for */
int slide_child_index(void *w, int j);
void *slide_child(void *w, int i);
/* A click on the child standing for album i, centred there as Centre clicks it (the pool's children
   stand for the albums round SLIDE_INDEX); the index goes back after. */
static void click_album(widget *s, int i) {
    int *index = (int *)(s->raw + SLIDE_INDEX), was = *index;
    *index = i;
    widget *k = slide_child(s, i);
    if (k) k->click(k->ctx, 0);
    *index = was;
}
/* navigation.c's: the glide asked for, the animator standing in as running until glide_end. */
static int glides, glide_goal, glide_ms, glide_easing;
void motion_open(void *p, const char *n) {}
int carousel_glide(void *x, int goal, unsigned ms, int easing) {
    if (!ms) { /* the immediate move, as navigation.c's: the index moves, the offset is 0 */
        int n = slide_count(x), stride = slide_menu_item_width(x) + *(int *)(W(x)->raw + SLIDE_SPACER);
        int id = (*(int *)(W(x)->raw + SLIDE_INDEX) - goal / stride) % n;
        *(void **)(W(x)->raw + SLIDE_ANIMATOR) = 0;
        *(int *)(W(x)->raw + SLIDE_OFFSET) = 0;
        slide_menu_set_value(x, id < 0 ? id + n : id);
        return 0;
    }
    *(void **)(W(x)->raw + SLIDE_ANIMATOR) = (void *)1;
    ++glides, glide_goal = goal, glide_ms = (int)ms, glide_easing = easing;
    return 1;
}
void search_yield(void) {} /* search_strip.c's: no Search here to wait for */
static void *invalidated; /* the last widget invalidated */
int widget_invalidate_force(void *x, void *y) { wm_repaints += x == &wm_repaints; invalidated = x; return 0; }
static void *wm_stack[4]; /* the window manager's windows, bottom first: none unless a test stacks them */
static int wm_n;
unsigned widget_count_children(void *x) { return x == &wm_repaints ? (unsigned)wm_n : (unsigned)W(x)->nkids; }
void *widget_get_child(void *x, unsigned i) { return x == &wm_repaints ? wm_stack[i] : &w[W(x)->kids[i]]; }
static void *home_art, *home_list; /* iPod Home's art and list; none in the slide tests */
void *widget_lookup(void *x, const char *n, int r) {
    return !x ? 0 : !strcmp(n, "img_cover_flow") ? x : !strcmp(n, "img_homeart") ? home_art :
           !strcmp(n, "list_view_home") ? home_list : 0;
}
static int home_mode, alt, light; /* home_mode: HOME, 0 Split, 1 Full; alt: shim_themes' index */
int ipod_home(void) { return home_mode; }
int home_lib_on(void) { return 1; }
int row_preview_on(void) { return 1; }
int slide_pick(char *url, int take) { return 0; } /* slideshow.c: no library cover */
int mclGetPlayStatus(void) { return 2; } /* playing: Split shows the playing cover */
int media_status(void) { return 2; } /* playing too: Home lists Now Playing's row */
void library_rows(void *list) {}
void stream_rows(void *list) {}
int tk_str_start_with(const char *s, const char *p) { return !strncmp(s, p, strlen(p)); }
const unsigned *widget_get_text(void *w) { return 0; }
int canvas_set_font(void *c, const char *f, unsigned px) { return 0; }
float canvas_measure_text(void *c, const unsigned *s, unsigned n) { return 0; }
const char *theme_face(const unsigned *s, int n, int *px) { return 0; }
int coverflow_position_enabled(void) { return 0; }
/* home.c's list pitch and fades, and Home's Shuffle Songs and Search rows (library_lists.c,
 * search_strip.c): none of these tests' */
void home_pad(void *view) {}
void home_np_keep(int d) {} /* home.c's selection as Now Playing's row comes or goes */
void home_overflow(void *list, int bottom) {}
void home_in_place(void *list) {}
void home_fades(void *canvas, void *list, int list_y, int cols, int split, int shift) {}
/* page_parallax.c: no window slides in these tests */
void *parallax_slide(float *percent) { return 0; }
int parallax_under_x(int w, float percent) { return 0; }
unsigned parallax_dim(float percent) { return 0; }
int shuffle_songs(void *ctx, void *event) { return 0; }
/* Home's first row, edit mode and folders' rule (home_np.c, library_edit.c, home.c, navigation.c): Home as stock has it, none of it edited or playing here */
void home_np_bind(void *win) {}
void home_np_sync(void) {}
void home_sets_bind(void *win) {}
void lr_home_tag(void *view) {}
void lr_home_apply(void *view, int np) {}
void lr_edit_labels(void *view, int on) {}
int lr_home_editing(void) { return 0; }
int home_folders(void) { return 0; }
/* ui_kit.c's motion: reduced, and a theme row with no duration, so a level changes at once */
int motion_reduced(void) { return 0; }
const motion_t *motion_row(int kind) { static const motion_t still; return &still; }
/* feedback.c's empty-page mark, cf_empty.c's page (none: the page is plain here) and catalogue.c's playlist covers (none) */
int empty_shown(void *page) { return 0; }
int cf_empty(void *page, void *body) { return 0; }
void catalogue_playlist_covers(void) {}
int catalogue_playlist_cover(int list, char *url) { return 0; }
int library_search_open(void *ctx, void *event) { return 0; }
int games_open(void *ctx, void *event) { return 0; } /* games.c: Home's Games row */
int tuner_open(void *ctx, void *event) { return 0; } /* radio_tuner.c: Home's Radio row */
/* radio_tuner.c and radio_pane.c: Radio never the owner here, so Home's pane is always the covers' */
int tuner_state(void) { return 0; }
int tuner_dial(void) { return 0; }
int rp_frame(unsigned *d, int pitch, int w, int h, int top, unsigned now, rp_words_t *words) { return 0; }
void rp_words(void *w, void *canvas, const rp_words_t *k) {}
void rp_enter(const unsigned *cover, int w, int h) {}
void rp_free(void) {}
int rp_moving(void) { return 0; }
void storage_scanned(void) {}                        /* storage.c: a whole scan's end, saved */
/* library_artist.c's own artist page: never up in these tests, so the page under is closed as before */
int artist_local_on(void *win) { return 0; }
int on_home_music_onclick(void *ctx, void *e) { return 0; }
int on_home_stream_onclick(void *ctx, void *e) { return 0; }
int widget_layout(void *w) { return 0; }
int widget_paint(void *w, void *c) { return 0; }
int widget_set_prop_str(void *x, const char *k, const char *v) {
    if (!strcmp(k, "style:normal:text_align_h")) W(x)->right = !strcmp(v, "right");
    return 0;
}
int widget_destroy(void *w) { return 0; }
int canvas_translate(void *c, int x, int y) { return 0; }
/* Stock's off-screen canvas, when offline_ok: the screen's size and BGRA8888 format, its pixels offline[] */
static int offline_ok;
static unsigned offline[320 * 375];
static char offline_lcd[0x200], offline_canvas[0x100];
void *canvas_offline_create(unsigned ww, unsigned h, unsigned f) {
    if (!offline_ok) return 0;
    assert(ww == 375 && h == 320 && f == LCD_BGRA8888);
    *(void **)(offline_canvas + CANVAS_LCD) = offline_lcd;
    *(int *)(offline_lcd + LCD_W) = 375, *(int *)(offline_lcd + LCD_H) = 320;
    *(int *)(offline_lcd + LCD_LINE_LENGTH) = 375 * 4, *(int *)(offline_lcd + LCD_FORMAT) = LCD_BGRA8888;
    *(void **)(offline_lcd + LCD_OFFLINE_FB) = offline;
    return offline_canvas;
}
int canvas_offline_begin_draw(void *c) { return 0; }
int canvas_offline_end_draw(void *c) { return 0; }
int canvas_offline_destroy(void *c) { return 0; }
unsigned unflip(unsigned v) { return v; }
unsigned mix(unsigned a, unsigned b, int j, int n) { return a; }
void bar_title_sync(void *win) {}
void forget_rows(void *list) { (void)list; }
int title_gone(void *w, void *event) { (void)w, (void)event; return 0; }
int bar_title_fits(void *label) { return 1; }
void list_value(void *l, const char *t, const char *v) { (void)l, (void)t, (void)v; }
/* shim_themes[0]: OLED's rows (main() checks them against its theme.xml), on CF_STYLE0, OLED's .cf macros from
 * tools/theme.py ROLE_MACROS on the tick cases' shelf, thumb off; [1] the same rows on CF_STYLE1, the thumb cases'
 * fixture (main()), with its own white paper */
static theme_t shim_themes[2] = {
    { .page_row = 32, .page_detail = 52, .page_title_h = 0, .page_x = 24, .page_cap_y = 4, .page_cap_h = 24, .page_det_y = 31, .page_det_h = 20, .home_full_row = 375, .home_text_x = 24,
      .home_label_end = { 44, 41 }, .cf = CF_STYLE0 },
    { .page_row = 32, .page_detail = 52, .page_title_h = 0, .page_x = 24, .page_cap_y = 4, .page_cap_h = 24, .page_det_y = 31, .page_det_h = 20, .home_full_row = 375, .home_text_x = 24,
      .home_label_end = { 44, 41 }, .cf = CF_STYLE1 } };
static short stub_bar_h = 30;
const theme_t *theme(void) { return &shim_themes[alt]; }
const scheme_t *scheme(void) { /* light is only asked of it in its light half */
    static scheme_t s;
    s.paper = alt == 1 ? 0xffffffu : LIGHT_PAPER * 0x010101u; /* the thumb fixture's own: white */
    return &s;
}
int client_h(void) { return 320 - stub_bar_h; }
/* theme_engine.c's, on the stub bar: a page's window is the whole screen, its content from the bar's foot */
int bar_foot(void *w) { return stub_bar_h - (w ? ((int *)W(w)->raw)[1] : 0); }
int page_room(void *w) { return w ? ((int *)W(w)->raw)[3] - bar_foot(w) : client_h(); }
int split_geom(int k) { return k == SPLIT_ROW || k == SPLIT_ART ? HOME_SPLIT_W : 0; } /* stock's grid's (STOCK_GRID) */
int list_fit_end(void) { return 0; } /* unread by these sources */
int theme_font(void *w, int role, int was) { return was; } /* the font only: no geometry */
/* Each fill in the colour last set, the centred text's. */
static int fills;
static struct { int x, y, w, h; unsigned color; } fill[1024];
static unsigned fill_color, centred_color;
static char centred_text[32];
static int centred_box[4];
static unsigned centred_px;
int stock_canvas_set_fill_color(void *c, unsigned x) { fill_color = x; return 0; }
int canvas_fill_rect(void *c, int x, int y, int ww, int h) {
    assert(fills < 1024);
    fill[fills].x = x; fill[fills].y = y; fill[fills].w = ww; fill[fills].h = h; fill[fills++].color = fill_color;
    return 0;
}
void draw_centred(void *c, const unsigned *s, unsigned n, const void *r, unsigned px, unsigned color) {
    unsigned i = 0;
    for (; i < n && i + 1 < sizeof centred_text; ++i) centred_text[i] = (char)s[i];
    centred_text[i] = 0, centred_color = color;
    memcpy(centred_box, r, sizeof centred_box), centred_px = px;
}
int ipod_light(void) { return light; }
unsigned tok(int t) { /* ui_kit.c's: the token in the mode shown */
    static const unsigned tokens[][2] = UI_TOKENS;
    return tokens[t][light ? 1 : 0];
}
int ipod_home_rockbox(void) { return 0; }
int widget_move_resize(void *x, int left, int top, int ww, int h) {
    int *r = (int *)W(x)->raw; /* W_X, W_Y, W_W, W_H */
    r[0] = left; r[1] = top; r[2] = ww; r[3] = h;
    return 0;
}
int widget_resize(void *x, int ww, int h) { return widget_move_resize(x, ((int *)W(x)->raw)[0], ((int *)W(x)->raw)[1], ww, h); }
void *widget_get_canvas(void *x) { return 0; } /* no canvas: the panel keeps its reserve */
static int insensitive; /* the widget last made insensitive */
int widget_set_sensitive(void *x, int v) { W(x)->numb = !v; if (!v) insensitive = W(x) - w; return 0; }
static int clip_rect[4] = { 0, 0, 375, 320 }; /* the canvas clip, screen x, y, w, h */
int canvas_get_clip_rect(void *c, void *r) { memcpy(r, clip_rect, sizeof clip_rect); return 0; }
int canvas_set_clip_rect(void *c, const void *r) { memcpy(clip_rect, r, sizeof clip_rect); return 0; }
const char *widget_get_type(void *x) { return W(x)->type; }
int widget_get_visible(void *x) { return W(x)->visible; }
int tk_strcmp(const char *a, const char *b) { return strcmp(a ? a : "", b ? b : ""); }
int navigator_back_to_home(void) { return 0; }
static int backs, force_closes, played_cls = -1, played_idx = -1;
unsigned playback_loads; /* playback.c's: ringnav_load's count of queue loads */
static void *force_closed; /* the last window closed unanimated */
int navigator_back(void) { return ++backs, 0; }
static int usb_page; /* USB Storage's page open */
int navigator_window_is_exist(const char *name) { return usb_page && !strcmp(name, "usbmode_page"); }
int window_manager_close_window_force(void *m, void *x) { force_closed = x; return ++force_closes, 0; }
int check_forbid_play_no_headset(int quiet) { return 0; } /* an output there: the tests play */
/* play_folder's: the page, its context and the queue as it stood */
static char nav_to[32];
static struct { void *dq; int idx, cls, mode; } nav_ctx;
static void *nav_queue[64];
static unsigned nav_n, navs;
int navigator_to_with_context(const char *n, const void *c) {
    snprintf(nav_to, sizeof nav_to, "%s", n);
    memcpy(&nav_ctx, c, sizeof nav_ctx);
    if (!strcmp(n, "playing_page")) played_idx = nav_ctx.idx, played_cls = nav_ctx.cls, ++playback_loads; /* stock's load */
    nav_n = *(const unsigned *)nav_ctx.dq; /* the queue's deque, as below */
    memcpy(nav_queue, (const char *)nav_ctx.dq + sizeof(unsigned), sizeof nav_queue);
    return ++navs, 0;
}
/* slide_menu: square items as high as the menu, plus the spacer. */
int slide_menu_item_width(void *x) { return *(int *)(W(x)->raw + W_H); }
int slide_menu_set_spacer(void *x, int v) { *(int *)(W(x)->raw + SLIDE_SPACER) = v; return 0; }
/* A BGRA8888 pixel, the screen's, in the textures' order (RGBA8888, red low), and back. */
static unsigned texel(unsigned p) { return (p & 0xff00ff00u) | (p & 255u) << 16 | (p >> 16 & 255u); }
/* The frame bitmap (bitmap_t as above) and the canvas: canvas_draw_image keeps what it drew, in the
   textures' order. */
static unsigned drawn_format; /* the last frame drawn's: 3, the screen's BGRA8888, is copied */
static int frames, frame_fail, tex_fail, locks, draws, drawn[4];
static unsigned shown[CF_VIEW_H * CF_VIEW_W];
#undef calloc /* the payload's calloc is this shim; the tests' own is libc's */
void *calloc(size_t, size_t);
static int calloc_fail, more_fail, more_asks; /* no memory at all; none for fx.more's slots alone */
static int tex_slots; /* fx_open's last textures: fx_base's and the placeholder */
static unsigned mem_kb = ~0u, mem_asks; /* /proc/meminfo's MemAvailable (chapters.c's), and its reads */
unsigned mem_available_kb(void) { return ++mem_asks, mem_kb; }
void *shim_calloc(size_t n, size_t size) {
    int more = size == 160 * 160 * 4 && n == 64 - 15; /* fx_window's: FX_POOL past a thumb theme's fx_base */
    more_asks += more;
    if (size == 160 * 160 * 4 && !more) tex_slots = (int)n;
    return calloc_fail || (tex_fail && size == 160 * 160 * 4) || (more_fail && more) ? 0 : calloc(n, size);
}
static int half_ms; /* a half copy's read, on the fake clock */
static int card_us; /* a half copy's read, slept by the loader on a real thread (card()) */
static void card(int us) { __atomic_store_n(&card_us, us, __ATOMIC_SEQ_CST); }
void shim_read(size_t bytes) {
    int us = __atomic_load_n(&card_us, __ATOMIC_SEQ_CST);
    if (bytes != 80 * 80 * 4) return;
    if (us) usleep(us);
    else clock_ms += half_ms;
}
/* The payload's o32 syscalls (cf_syscall), logged: gettid answers 4242; idle_fails refuses the idle
   I/O class. sys_main: one came from the tests' own thread. */
static int sys_log[16][4], sys_n, sys_main, idle_fails;
static pthread_t main_thread;
int cf_syscall(int nr, int a, int b, int c) {
    int *l = sys_log[sys_n++ & 15];
    sys_main |= pthread_equal(pthread_self(), main_thread);
    l[0] = nr, l[1] = a, l[2] = b, l[3] = c;
    return nr == 4222 ? 4242 : nr == 4314 && c == 3 << 13 && idle_fails ? -1 : 0;
}
/* loop_wake.c's: a half read ends the UI loop's idle; counted, as there is no loop here. */
static int loop_wakes;
void loop_wake(void) { ++loop_wakes; }
#define SYS_IS(i, nr, a, b, c) (sys_log[i][0] == (nr) && sys_log[i][1] == (a) && sys_log[i][2] == (b) && sys_log[i][3] == (c))
/* The loader: by default each wake-up reads every copy asked for, its reads off the UI thread's
   clock, as on a thread of its own on a card at once; ld_hold leaves them asked for (cf_ld_step
   reads one); ld_threads runs it on a real thread, card_us each read. */
static int ld_threads, ld_hold, ld_live, ld_started;
void *(*cf_loader(void))(void *);
int cf_ld_step(void), cf_ld(int a);
static void *(*ld_body)(void *);
static void *idle_body(void *x) { return x; }
static void *loader_body(void *x) { __atomic_fetch_add(&ld_live, 1, __ATOMIC_SEQ_CST); ld_body(x); __atomic_fetch_sub(&ld_live, 1, __ATOMIC_SEQ_CST); return 0; }
int shim_thread(pthread_t *t, void *(*f)(void *), void *x) {
    if (f == cf_loader()) ++ld_started, ld_body = f, f = ld_threads ? loader_body : idle_body;
    return pthread_create(t, 0, f, x);
}
int shim_wake(sem_t *s) {
    if (ld_threads) return sem_post(s);
    unsigned c = clock_ms;
    while (!ld_hold && cf_ld_step()) {
    }
    clock_ms = c;
    return 0;
}
void shim_install(void) { clock_ms += half_ms; }
void *bitmap_create_ex(unsigned ww, unsigned h, unsigned line, unsigned format) {
    if (frame_fail) return 0;
    unsigned *b = calloc(1, 0x48);
    b[0] = ww, b[1] = h, b[2] = line, ((unsigned short *)b)[7] = format;
    *(unsigned char **)((char *)b + 0x14) = calloc(h, line);
    ++frames;
    return b;
}
int bitmap_destroy(void *b) { free(*(void **)((char *)b + 0x14)); free(b); --frames; return 0; }
unsigned char *bitmap_lock_buffer_for_write(void *b) { ++locks; return *(unsigned char **)((char *)b + 0x14); }
int bitmap_unlock_buffer(void *b) { return 0; }
unsigned bitmap_get_line_length(void *b) { return ((unsigned *)b)[2]; }
static int moves_ok, moves, moved[2][2][4]; /* moves_ok: a draw elsewhere than its source, src and dst kept */
int canvas_draw_image(void *c, void *b, const void *src, const void *dst) {
    const int *r = dst, *q = src;
    if (moves_ok && memcmp(r, q, 16)) {
        assert(moves < 2 && ((unsigned short *)b)[6] & 1);
        memcpy(moved[moves][0], q, 16), memcpy(moved[moves++][1], r, 16);
        return 0;
    }
    ++draws;
    assert(!memcmp(r, q, 16) && ((unsigned short *)b)[6] & 1); /* 1:1, and marked opaque */
    memcpy(drawn, r, sizeof drawn);
    unsigned fw = ((unsigned *)b)[0] < CF_VIEW_W ? ((unsigned *)b)[0] : CF_VIEW_W, fh = ((unsigned *)b)[1] < CF_VIEW_H ? ((unsigned *)b)[1] : CF_VIEW_H;
    for (unsigned y = 0; y < fh; ++y) memcpy(shown + y * CF_VIEW_W, *(unsigned char **)((char *)b + 0x14) + y * ((unsigned *)b)[2], fw * 4);
    if ((drawn_format = ((unsigned short *)b)[7]) == 3)
        for (unsigned i = 0; i < fh * CF_VIEW_W; ++i) shown[i] = texel(shown[i]);
    return 0;
}
int stock_home_trampoline(void *win, void *ctx) { return 0; }
/* The songtable writers' stock bodies; during_write runs inside one, as an open mid-scan would. */
static void (*during_write)(void);
static int stock_write(void) { if (during_write) during_write(); return 0; }
int stock_scan_all_trampoline(void *a, void *b) { return stock_write(); }
int stock_scan_folder_trampoline(void *a, void *b) { return stock_write(); }
int stock_delete_song_trampoline(void *a, void *b) { return stock_write(); }
void library_scope_scan(void) {} /* the marker walk: test/library_scope.py */
int library_scope_hidden(const char *path) { (void)path; return 0; } /* no markers here */
int coverflow_scan_all(void *, void *);
static void rescan(void) { coverflow_scan_all(0, 0); } /* the library changed, as only a scan changes it */

static int (*timer_fn)(const void *);
unsigned timer_add(int (*f)(const void *), void *ctx, unsigned ms) { timer_fn = f; return 1; }
int timer_remove(unsigned id) { timer_fn = 0; return 0; }
void stop_timer(unsigned *t) { if (*t) timer_remove(*t); *t = 0; } /* navigation.c's */
void rearm(unsigned *t, int (*f)(const void *), unsigned ms) { stop_timer(t); *t = timer_add(f, 0, ms); }
static void run(void) { while (timer_fn) { int (*f)(const void *) = timer_fn; timer_fn = 0; usleep(1000); f(0); } }

/* libcstl: a deque of record pointers; the query stubs fill the staging deque. */
typedef struct { unsigned n; void *at[8192]; } deque; /* pool_library's 6,000 albums fit */
void *shim_dir;
void *_create_deque(const char *t) { return calloc(1, sizeof(deque)); }
void deque_init_copy(void *d, const void *s) { *(deque *)d = *(const deque *)s; }
void deque_assign(void *d, const void *s) { *(deque *)d = *(const deque *)s; }
void deque_clear(void *d) { ((deque *)d)->n = 0; }
void deque_init(void *d) { ((deque *)d)->n = 0; }
void _deque_push_back(void *d, ...) {
    va_list a;
    va_start(a, d);
    ((deque *)d)->at[((deque *)d)->n++] = va_arg(a, void *);
    va_end(a);
}
void deque_destroy(void *d) { free(d); }
void deque_pop_back(void *d) { --((deque *)d)->n; }
unsigned deque_size(const void *d) { return ((const deque *)d)->n; }
void *deque_at(const void *d, unsigned i) { return i < ((const deque *)d)->n ? ((const deque *)d)->at[i] : 0; }

static char records[8192][0x60], names[8192][64], paths[8192][600];
static int albums, queries;
static void album(const char *name, const char *art) {
    char *r = records[albums], dir[500];
    snprintf(names[albums], 64, "%s", name);
    snprintf(dir, sizeof(dir), PEQ_ROOT "/music/%s", name);
    mkdir(dir, 0755);
    snprintf(paths[albums], 600, "%s/01.flac", dir);
    FILE *f = fopen(paths[albums], "w"); fclose(f);
    if (art) { snprintf(dir + strlen(dir), 100, "/%s", art); f = fopen(dir, "w"); fclose(f); }
    memset(r, 0, 0x60);
    *(int *)(r + REC_ID) = albums + 1;
    *(char **)(r + REC_ALBUM) = names[albums];
    *(char **)(r + REC_ARTIST) = "Artist";
    *(char **)(r + REC_PATH) = paths[albums++];
}
/* Stock groups albums by name alone, and appends an "Unknown Album" row (id -1) whenever any album
   exists; unknown_songs is what its press, getMusicByAlbum(NULL), finds. */
static int unknown_row, unknown_songs;
static char unknown_record[0x60], untagged_song[0x60], untagged_more[0x60];
static int untagged_two; /* getMusicByAlbum(NULL) finds untagged_more too */
static unsigned all_ms; /* what each getAllAlbum takes on the fake clock */
/* all_hold: getAllAlbum runs on until the test lets it go (all_hold 0) or the open gives up, which its progress
   handler sees (coverflow_query_stop, as SQLite calls it between VM steps), for at most 3 s; all_running while it
   runs, all_stopped once the handler stopped it (slow_open) */
static volatile int all_hold, all_running, all_stopped;
static int all_queries;
/* weak: a build without it (before 2026-11-19) links, and its open is seen to hold the press */
extern int coverflow_query_stop(void *) __attribute__((weak));
static int query_stopped(void) { return coverflow_query_stop && coverflow_query_stop(0); }
int getAllAlbum(void) {
    deque *d = shim_dir;
    ++queries, ++all_queries;
    clock_ms += all_ms;
    all_running = 1;
    for (int i = 0; all_hold && i < 3000; ++i) {
        if (query_stopped()) {
            all_stopped = 1;
            break;
        }
        usleep(1000);
    }
    all_running = 0;
    d->n = 0;
    for (int i = 0, j; i < albums; ++i) {
        for (j = 0; j < i && strcasecmp(names[j], names[i]); ++j) {}
        if (j == i) d->at[d->n++] = records[i];
    }
    if (unknown_row && albums) {
        *(int *)(unknown_record + REC_ID) = -1;
        *(const char **)(unknown_record + REC_ALBUM) = "Unknown Album";
        *(const char **)(unknown_record + REC_ARTIST) = "Unknown Artist";
        d->at[d->n++] = unknown_record;
    }
    return albums;
}
static int music_only;
extern void *staged(int (*query)(void *), void *arg, int *count);
void *staged_music(int (*query)(void *), void *arg, int *count) {
    music_only = 1;
    void *out = staged(query, arg, count);
    music_only = 0;
    return out;
}
static int spoken_path(const char *path) {
    return !strncasecmp(path, "/mnt/mmc/audiobooks/", 20) ||
           !strncasecmp(path, "/mnt/mmc/podcasts/", 18) ||
           (strlen(path) >= 4 && !strcasecmp(path + strlen(path) - 4, ".m4b"));
}
/* navigation.c's: a playlist's displayed copy, its spoken songs left out */
int music_records(void *list, int discovery) {
    deque *d = list;
    unsigned k = 0;
    for (unsigned i = 0; i < d->n; ++i) {
        const char *path = *(const char **)((char *)d->at[i] + REC_PATH);
        if (discovery || !path || !spoken_path(path)) d->at[k++] = d->at[i];
    }
    return (int)(d->n = k);
}
int spoken(const char *path) { return path && spoken_path(path); } /* navigation.c's */
static int unknown_queries;
int getMusicByAlbum(const char *a) {
    deque *d = shim_dir;
    d->n = 0;
    if (!a) {
        ++unknown_queries;
        if (unknown_songs) d->at[d->n++] = untagged_song;
        if (untagged_two) d->at[d->n++] = untagged_more;
    } else
        for (int i = 0; i < albums; ++i)
            if (!strcasecmp(names[i], a) && (!music_only || !spoken_path(paths[i]))) d->at[d->n++] = records[i]; /* each album's one song: its record */
    return (int)d->n;
}

/* The albums told apart (a record each), and Sort's ranking queries over that grouping in another
   order, here the test's own order by name; a row may differ in case from the stock one, as the
   grouping allows. */
static const char *ranked[16], *rank_sql;
static unsigned rank_ms; /* what a ranking takes on the fake clock */
static volatile unsigned rank_hold; /* a ranking runs on until the test lets it go (slow_sort) */
static pthread_t rank_thread;       /* the thread the last ranking ran on */
static char rank_rows[16][0x60];
static int rank_year[16]; /* the Year query's year column, each ranked row's (split_row's REC_YEAR) */
/* Recently Added's time column, each ranked row's newest time_create; unset, each row a second older than the
   last */
static const char *rank_when[16];
/* navigation.c's Year order: newest first (0) or oldest. */
static int year_old;
int ringnav_year_old(void) { return year_old; }
/* navigation.c's Fast Navigation: letters, years and half copies; main turns it on for their tests */
static int big_library;
int ringnav_big_library(void) { return big_library; }
int ringnav_artist_az(void) { return 0; }
/* Stock's row maker for a ranking query (split_row's first four columns): the album named by v[1]
   as a row of its own, added to the staged deque. */
static int rank_n;
int album_row(void *a, int n, char **v, char **c) {
    assert(n == 4 && rank_n < 16);
    char *r = rank_rows[rank_n++];
    memset(r, 0, 0x60);
    for (int j = 0; j < albums; ++j)
        if (!strcasecmp(names[j], v[1])) memcpy(r, records[j], 0x60);
    *(const char **)(r + REC_ALBUM) = v[1]; /* ranked[]'s, which outlives the row */
    ((deque *)shim_dir)->at[((deque *)shim_dir)->n++] = r;
    return 0;
}
int toolsUpdateDbTable(const char *db, const char *sql) { return 1; } /* only a drop-flag scan writes */
int toolsGetUserStop(void) { return 0; }
int msg_counting(void) { return 0; }
int toolsQueryDbTable(const char *db, const char *sql, void *row, int sort) {
    assert(!strcmp(db, "/mnt/data/database.db") && !sort && strstr(sql, "group by album COLLATE NOCASE,ifnull(albumsonger"));
    deque *d = shim_dir;
    d->n = 0;
    if (!strstr(sql, " order by ")) {
        int music = strstr(sql, "fileurl not like") != 0;
        for (int i = 0; i < albums; ++i) {
            const char *path = *(const char **)(records[i] + REC_PATH);
            if (music && (!strncasecmp(path, "/mnt/mmc/audiobooks/", 20) ||
                          !strncasecmp(path, "/mnt/mmc/podcasts/", 18) ||
                          (strlen(path) >= 4 && !strcasecmp(path + strlen(path) - 4, ".m4b")))) continue;
            d->at[d->n++] = records[i];
        }
        if (music && unknown_songs) d->at[d->n++] = unknown_record;
        return (int)d->n;
    }
    assert(strstr(sql, "time_create") || strstr(sql, "year"));
    rank_sql = sql, rank_thread = pthread_self();
    clock_ms += rank_ms;
    for (int i = 0; rank_hold && i < 3000 && !query_stopped(); ++i)
        usleep(1000); /* held on its thread until the test lets it go, or the open gives up */
    /* each row's columns as text, as SQLite hands them: id, album, songer, the art track,
       albumsonger, and YEAR_SQL's year */
    int added = strstr(sql, "max(time_create) from") != 0, cols = added || strstr(sql, "else 0 end) from") ? 6 : 5;
    rank_n = 0;
    for (int i = 0; ranked[i]; ++i) {
        char id[12], year[12], when[12];
        snprintf(id, sizeof id, "%d", i + 1), snprintf(year, sizeof year, "%d", rank_year[i]);
        snprintf(when, sizeof when, "%s", rank_when[i] ? rank_when[i] : "");
        if (!rank_when[i]) snprintf(when, sizeof when, "%d", 1700000000 - i);
        char *v[6] = { id, (char *)ranked[i], "Artist", "/music/x/01.flac", 0, added ? when : year };
        ((int (*)(void *, int, char **, char **))row)(0, cols, v, 0);
    }
    ++queries;
    return (int)d->n;
}
/* library_lists.c's and navigation.c's: Most Played's counts, per stock record here, and the
 * library lists' article. */
static unsigned played[1024];
/* playback.c's generator, falling: Random shows the stock order reversed. */
static unsigned dice = 1000;
unsigned rand_below(unsigned n) { return dice-- % n; }
void album_plays(int (*album_of)(void *), unsigned *sum) {
    for (int i = 0; i < albums; ++i) {
        int a = played[i] ? album_of(records[i]) : -1;
        if (a >= 0) sum[a] += played[i];
    }
}
void ringnav_sort_key(char *s) {
    if (!strncasecmp(s, "the ", 4)) memmove(s, s + 4, strlen(s + 4) + 1);
}

/* The stock art calls: each writes its source into dst, and checks the locks it runs under. */
pthread_mutex_t shim_parse = PTHREAD_MUTEX_INITIALIZER, shim_play = PTHREAD_MUTEX_INITIALIZER;
static int held[2], order[64], norder, calls, embedded_fails, block_at = -1, blocked;
static char made[64][600];
int shim_lock(void *m) {
    order[norder++ % 64] = m == &shim_parse ? 1 : 2;
    assert(!held[m == &shim_play]);
    held[m == &shim_play] = 1;
    return pthread_mutex_lock(m);
}
int shim_unlock(void *m) { held[m == &shim_play] = 0; return pthread_mutex_unlock(m); }
static int write_art(const char *dst, const char *what) {
    snprintf(made[calls++ % 64], 600, "%s", what);
    if (calls - 1 == block_at) { blocked = 1; while (blocked) usleep(1000); }
    FILE *f = fopen(dst, "w"); /* none once the card is gone */
    if (f) fputs(what, f);
    return f && !fclose(f);
}
/* Cover Flow's .jpg.tmp; playlist art's .tmp.jpg, its 52 px small one too. */
static int tmp_dst(const char *dst) { return strstr(dst, ".jpg.tmp") || strstr(dst, ".tmp.jpg"); }
int toolsThumbSpecCover(const char *src, const char *dst, int ww, int h) {
    assert((ww == 160 || ww == 52) && h == ww && held[0] && !held[1] && tmp_dst(dst));
    /* a playlist's own picture, any file the user drops in, is decoded by ffmpeg alone, out of demo */
    assert(!strstr(src, "_explaylist_data"));
    if (strstr(src, ".webp") || strstr(src, "Bad")) return 0; /* stock decodes only JPEG and PNG */
    return write_art(dst, strrchr(src, '/') + 1);
}
static int album_asks; /* each toolsGetAlbumCover, found or not */
int toolsGetAlbumCover(const char *src, const char *dst, int ww, int h) {
    assert(ww == 160 && h == 160 && held[0] && held[1] && tmp_dst(dst));
    ++album_asks;
    return embedded_fails ? 0 : write_art(dst, "embedded");
}
static int extern_found;
int toolsGetExternCover(const char *src, const char *dst, int ww, int h) {
    assert(ww == 320 && h == 320 && !held[0] && !held[1]);
    return extern_found ? write_art(dst, "extern") : 0;
}
static unsigned free_blocks = 1 << 20, card_blocks = 1 << 21, statfs_calls; /* 4 GB free of 8 GB */
/* The art cache's room check, and a full scan's room check on /mnt/data before its indexes: both
   see the same card here. */
int shim_statfs(const char *p, void *out) {
    assert(!strcmp(p, PEQ_ROOT "/mnt/mmc/.coverflow") || !strcmp(p, "/mnt/data"));
    ++statfs_calls;
    unsigned *s = out;
    s[1] = 4096, s[3] = card_blocks, s[7] = free_blocks; /* MIPS o32 statfs: f_bsize, f_blocks, f_bavail */
    return 0;
}

void *shim_queue;
unsigned char shim_memory[0x800]; /* g_memory_info, which album_memory fills */
unsigned char shim_covertype;
char shim_lastcover[1024], shim_id3[2716];
static widget *page;
/* A payload join has begun: worker_stop sets the cancel, then joins the art worker. */
static volatile int joining;
int shim_join(pthread_t t, void **result) { joining = 1; return pthread_join(t, result); }
/* access() calls, the UI thread's (main's) and every thread's */
static pthread_t main_thread;
static volatile int ui_access, all_access;
int shim_access(const char *path, int mode) {
    __sync_fetch_and_add(&all_access, 1);
    if (pthread_equal(pthread_self(), main_thread)) ++ui_access;
    return access(path, mode);
}
/* The album in hand finishes once the cancel is set, however late the main thread gets to stop(); the caller clears
   joining before it starts this. */
static void *release(void *unused) { while (!joining) usleep(1000); blocked = 0; return 0; }
static void open_page(void) {
    widget home;
    memset(&home, 0, sizeof(home));
    coverflow_home(&home, 0);
    page = &w[nw + 1];
    home.click(home.ctx, 0);
    run();
}
static void key(int k) { int e[16] = {0}; e[EVENT_KEY / 4] = k; w[8190].click(0, e); run(); }
static void close_page(void) { w[8191].click(w[8191].ctx, 0); }
static void mid_write(void) { open_page(); close_page(); }
unsigned fnv(unsigned h, const unsigned char *s);
static int last_album(int i) { /* the card holds album i's key as the one to reopen on */
    unsigned key = 0;
    FILE *f = fopen(PEQ_ROOT "/mnt/mmc/.coverflow/album", "rb");
    int ok = f && fread(&key, sizeof(key), 1, f) == 1;
    if (f) fclose(f);
    return ok && key == fnv(fnv(FNV_SEED, (const unsigned char *)"Artist"), (const unsigned char *)names[i]);
}
static widget *slide(void) {
    for (int i = nw; i > page - w; --i) if (!strcmp(w[i].type, "slide_menu")) return &w[i];
    return 0;
}
static const char *title(void) { /* the page's caption: its title row's, or the label the bar reads (PAGE_TITLE, its type here) */
    for (int i = nw; i > page - w; --i)
        if ((!strcmp(w[i].type, "hscroll_label") || !strcmp(w[i].type, PAGE_TITLE)) && w[w[i].parent].parent == page - w) return w[i].text;
    return "";
}
/* The cache file of Artist's album_name. */
static char *art_file(char *path, const char *album_name) {
    unsigned h = 2166136261u;
    for (const unsigned char *s = (const unsigned char *)"Artist"; *s; ++s) h = (h ^ *s) * 16777619u;
    h *= 16777619u;
    for (const unsigned char *s = (const unsigned char *)album_name; *s; ++s) h = (h ^ *s) * 16777619u;
    h *= 16777619u;
    snprintf(path, 600, PEQ_ROOT "/mnt/mmc/.coverflow/%08x.jpg", h);
    return path;
}
static long size(const char *album_name) {
    char path[600];
    for (int i = 0; i < albums; ++i) {
        if (strcmp(names[i], album_name)) continue;
        art_file(path, names[i]);
        struct stat s;
        return stat(path, &s) ? -1 : s.st_size;
    }
    return -2;
}
static int tmp_files(void) {
    int n = 0;
    DIR *d = opendir(PEQ_ROOT "/mnt/mmc/.coverflow");
    for (struct dirent *e; d && (e = readdir(d));) n += strstr(e->d_name, ".tmp") != 0;
    if (d) closedir(d);
    return n;
}


/* ---- Depth: the renderer on its own, then on the page. ---- */
void coverflow_render(unsigned *, int, int, const unsigned *const[7]);
void coverflow_paint(void *, void *);
/* The renderers draw the screen's BGRA8888 (opaque_frame); the tests hand them and read their frames
   in the textures' order, RGBA8888 with red low: R and B swapped back in each row the render filled
   (all opaque; the guards and any row below left). bgra_frames checks the frames as drawn. */
static void (*const bgra_render)(unsigned *, int, int, const unsigned *const[7]) = coverflow_render;
static void texel_render(unsigned *d, int pitch, int frac, const unsigned *const ring[7]) {
    bgra_render(d, pitch, frac, ring);
    for (unsigned *row = d;; row += pitch) {
        int x = 0;
        while (x < CF_VIEW_W && row[x] >> 24 == 255) ++x;
        if (x < CF_VIEW_W) break;
        for (x = 0; x < CF_VIEW_W; ++x) row[x] = texel(row[x]);
    }
}
#define coverflow_render texel_render
#define ONE 65536
#define PITCH (CF_VIEW_W + 5) /* guard columns, and guard rows below, to catch clipping errors */
static unsigned frame[(CF_VIEW_H + 3) * PITCH], flat[7][160 * 160], art[160 * 160];
static const unsigned colours[7] = { 0xff0000ffu, 0xff00ff00u, 0xffff0000u, 0xff00ffffu, 0xffffff00u, 0xffff00ffu, 0xff0080ffu };
/* A thumb theme's ring, wide's fifteen (cover j - 7 at wide[j]): the seven flat colours round and
   round, as a small library repeats; the seven's ring. */
static const unsigned **flat_ring(const unsigned **wide) {
    for (int j = 0; j < 15; ++j) wide[j] = flat[(j + 3) % 7];
    return wide + 4;
}
static void render(int frac, const unsigned *const ring[7]) {
    for (unsigned i = 0; i < sizeof(frame) / 4; ++i) frame[i] = 0x12345678u;
    coverflow_render(frame, PITCH, frac, ring);
    for (int y = 0; y < CF_VIEW_H + 3; ++y)
        for (int x = 0; x < PITCH; ++x)
            assert((y < CF_VIEW_H && x < CF_VIEW_W) == (frame[y * PITCH + x] != 0x12345678u)); /* all of it, and only it */
}
static unsigned px(int x, int y) { return frame[y * PITCH + x]; }
static int ch(unsigned p, int i) { return (int)(p >> 8 * i & 255); }
/* The slot whose flat colour p is a shade of (by channel proportions), or -1. */
static int slot_of(unsigned p) {
    int m = ch(p, 0) > ch(p, 1) ? ch(p, 0) : ch(p, 1);
    m = m > ch(p, 2) ? m : ch(p, 2);
    for (int j = 0; m >= 24 && j < 7; ++j) {
        int ok = 1;
        for (int i = 0; i < 3; ++i) { int want = ch(colours[j], i) * m / 255; ok &= abs(ch(p, i) - want) <= 3 + m / 40; }
        if (ok) return j;
    }
    return -1;
}
static int differ(const unsigned *a, const unsigned *b, int tolerance) {
    int n = 0;
    for (int y = 0; y < CF_VIEW_H; ++y)
        for (int x = 0; x < CF_VIEW_W; ++x)
            for (int i = 0; i < 3; ++i) if (abs(ch(a[y * PITCH + x], i) - ch(b[y * PITCH + x], i)) > tolerance) { ++n; break; }
    return n;
}
static void capture(const char *name) {
    const char *dir = getenv("CF_CAPTURES");
    if (!dir) return;
    char path[600];
    snprintf(path, sizeof(path), "%s/%s.rgba", dir, name);
    FILE *f = fopen(path, "wb");
    fwrite(shown, 4, CF_VIEW_H * CF_VIEW_W, f);
    fclose(f);
}

static void renderer(void) {
    const unsigned *ring[7], *mirror[7];
    for (int j = 0; j < 7; ++j) {
        for (int i = 0; i < 160 * 160; ++i) flat[j][i] = colours[j];
        ring[j] = flat[j];
    }
    for (int i = 0; i < 160 * 160; ++i) art[i] = 0xff000000u | (unsigned)(i * 2654435761u >> 8 & 0xffffff);
    /* At rest the selected cover is the texture itself, 160 px square at CF_TOP, centred, full
       brightness; the neighbours at CF_BRIGHT1, then CF_BRIGHT2, the third out too (CF_THIRD). */
    const unsigned *rest[7] = { flat[0], flat[1], flat[2], art, flat[4], flat[5], flat[6] };
    render(0, rest);
    assert(px(CF_CX - 80 + 1, CF_TOP) != art[1]); /* art[0] is black, as the corner's background */
    for (int y = 0; y < 160; ++y)
        for (int x = 0; x < 160; ++x)
            if ((x >= 12 && x < 148) || (y >= 12 && y < 148))
                assert(px(CF_CX - 80 + x, CF_TOP + y) == art[y * 160 + x]);
    assert(px(CF_CX - 81, CF_TOP + 80) != art[80 * 160] && px(CF_CX, CF_TOP - 1) == 0xff000000u);
    /* Each neighbour's visible run on the horizon, sampled in its middle: its colour at its
       brightness, three a side, each run a real slice of cover. */
    for (int j = -3; j <= 3; ++j) {
        if (!j) continue;
        int first = -1, last = -1;
        for (int x = 0; x < CF_VIEW_W; ++x)
            if (slot_of(px(x, CF_TOP + 80)) == j + 3) last = x, first = first < 0 ? x : first;
        assert(last - first >= (abs(j) == 1 ? 40 : abs(j) == 2 ? 18 : 8));
        unsigned p = px((first + last) / 2, CF_TOP + 80), want = colours[j + 3];
        int b = abs(j) == 1 ? CF_BRIGHT1 : CF_BRIGHT2;
        for (int i = 0; i < 3; ++i) assert(abs(ch(p, i) - ch(want, i) * b / 256) <= 1);
    }
    static unsigned without[(CF_VIEW_H + 3) * PITCH];
    memcpy(without, frame, sizeof(frame));
    const unsigned *inner[7] = { 0, flat[1], flat[2], art, flat[4], flat[5], 0 };
    render(0, inner);
    assert(memcmp(without, frame, sizeof(frame))); /* the third out stays at rest, as in the row */
    /* Everything, reflections included, fits the frame with room to spare. */
    for (int x = 0; x < CF_VIEW_W; ++x) assert(!(px(x, 0) & 0xffffff) && !(px(x, CF_VIEW_H - 1) & 0xffffff));
    unsigned centre = px(CF_CX, CF_TOP + 80); /* Light Mode: the same covers over paper */
    light = 1;
    render(0, inner);
    assert(px(0, 0) == (0xff000000u | LIGHT_PAPER * 0x010101u) && px(CF_CX, CF_TOP + 80) == centre);
    light = 0;
    render(0, inner);
    /* The reflection: CF_REFLECT rows under the cover, from CF_REFLECT_TOP of its brightness down
       to black, never brighter going down; nothing after it. */
    render(0, ring);
    int prev = 256, rows = 0;
    for (int y = CF_TOP + 160; y < CF_VIEW_H; ++y) {
        int v = ch(px(CF_CX, y), 0); /* slot 3 is 0xff00ffff: red and green */
        assert(v <= prev);
        if (y == CF_TOP + 160) assert(abs(v - 255 * CF_REFLECT_TOP / 256) <= 3);
        rows += v > 0, prev = v;
    }
    assert(rows >= CF_REFLECT - 1 && rows <= CF_REFLECT);
    /* No seam where a body ends mid-row: down any column from a cover's middle, brightness never
       dips between the body, the row they share and the reflection. Square covers: mid-turn the
       centre column can fall on a rounded corner, whose reflection mirrors the arc and so does
       brighten for a few rows below the edge (corner_column). */
    const unsigned *alone[7] = { 0, 0, 0, flat[3], 0, 0, 0 };
    render(0, alone);
    assert(px(CF_CX, CF_TOP + 80) == colours[3]);
    assert(px(CF_CX - 80, CF_TOP) == 0xff000000u);
    assert(px(CF_CX - 80, CF_TOP + 160) == 0xff000000u);
    theme_t *t = (theme_t *)theme();
    int radius = t->cf.radius;
    close_page(); t->cf.radius = 0; open_page();
    for (int frac = -ONE / 2; frac < ONE / 2; frac += ONE / 16 + 77) {
        render(frac, alone);
        for (int x = 0; x < CF_VIEW_W; ++x) {
            if (slot_of(px(x, CF_TOP + 80)) != 3) continue;
            if (x != CF_CX) continue;
            int body = 256, dipped = 0;
            for (int y = CF_TOP + 80; y < CF_VIEW_H; ++y) {
                int v = ch(px(x, y), 0);
                dipped |= v < body && v < ch(px(x, y + 1 < CF_VIEW_H ? y + 1 : y), 0) - 2;
                body = v;
            }
            assert(!dipped);
        }
    }
    close_page(); t->cf.radius = radius; open_page();
    /* Symmetry: a mirror-image ring renders the mirror image, at rest and mid-turn either way. */
    for (int j = 0; j < 7; ++j) mirror[j] = flat[j < 3 ? j : 6 - j];
    render(0, mirror);
    for (int y = 0; y < CF_VIEW_H; ++y)
        for (int x = 0; x < 2 * CF_CX; ++x) assert(px(x, y) == px(2 * CF_CX - 1 - x, y));
    static unsigned left[(CF_VIEW_H + 3) * PITCH];
    const unsigned *swapped[7];
    for (int j = 0; j < 7; ++j) swapped[j] = ring[6 - j];
    for (int frac = ONE / 8; frac < ONE / 2; frac += ONE / 8) {
        render(-frac, swapped);
        memcpy(left, frame, sizeof(frame));
        render(frac, ring);
        for (int y = 0; y < CF_VIEW_H; ++y)
            for (int x = 0; x < 2 * CF_CX; ++x) assert(px(x, y) == left[y * PITCH + 2 * CF_CX - 1 - x]);
    }
    /* Continuity: covers move, turn and fade from one continuous position, so a small step changes
       only a little, including where the centre changes hands (frac -1/2 after c, +1/2 before). */
    static unsigned a[(CF_VIEW_H + 3) * PITCH];
    const unsigned *next[7];
    for (int j = 0; j < 7; ++j) next[j] = ring[(j + 1) % 7];
    render(ONE / 2 - 1, ring);
    memcpy(a, frame, sizeof(frame));
    render(-ONE / 2, next);
    assert(differ(a, frame, 6) < 200);
    int worst = 0;
    for (int f = -ONE / 2; f + ONE / 256 < ONE / 2; f += ONE / 256) {
        render(f, ring);
        memcpy(a, frame, sizeof(frame));
        render(f + ONE / 256, ring);
        int d = differ(a, frame, 24);
        worst = d > worst ? d : worst;
    }
    assert(worst < CF_VIEW_W * CF_VIEW_H / 50);
}

int coverflow_hit(int, int, int);
void coverflow_lost_touch(void);
static unsigned batch; /* the dispatch time of moves queued together, else 0 */
static int touch_at(int type, int x, int y, unsigned t) { /* t: when the event was posted */
    int e[16] = {0};
    e[0] = type, e[EVENT_TIME / 4] = t, e[EVENT_X / 4] = x, e[EVENT_Y / 4] = y;
    clock_ms = batch ? batch : t;
    return touch_fn(0, e);
}
/* The glide ends as stock completion would: the index takes the offset, which goes back to 0. */
static void glide_end(widget *s) {
    char *raw = s->raw;
    int n = slide_count(s), i = (*(int *)(raw + SLIDE_INDEX) - glide_goal / CF_STRIDE) % n;
    *(int *)(raw + SLIDE_INDEX) = i < 0 ? i + n : i;
    *(int *)(raw + SLIDE_OFFSET) = 0;
    *(void **)(raw + SLIDE_ANIMATOR) = 0;
    changed_fn(0, 0);
}
static int offset(widget *s) { return *(int *)(s->raw + SLIDE_OFFSET); }
static const char *album_caption(widget *covers_view) { /* the first label: the album's */
    for (int i = 0; i < covers_view->nkids; ++i)
        if (!strcmp(w[covers_view->kids[i]].type, "hscroll_label")) return w[covers_view->kids[i]].text;
    return "";
}

/* Touch: the hit test agrees with the drawn covers; taps, drags, flicks, a vertical move, a catch
   and a release never seen. */
static void touches(widget *s, widget *covers_view) {
    const unsigned *ring[7];
    for (int j = 0; j < 7; ++j) ring[j] = flat[j];
    for (int frac = -ONE / 2; frac < ONE / 2; frac += ONE / 8) {
        render(frac, ring);
        for (int y = 1; y < CF_TOP + 160 - 1; ++y) /* the bodies, above the reflections */
            for (int x = 1; x < CF_VIEW_W - 1; ++x) {
                int j = coverflow_hit(frac, x, y), same = 1;
                for (int d = 0; d < 4; ++d) same &= coverflow_hit(frac, x + (d & 1 ? d - 2 : 0), y + (d & 1 ? 0 : d - 1)) == j;
                if (same && j != 99) assert(slot_of(px(x, y)) == j + 3); /* away from the edges */
                if (same && j == 99 && y < CF_TOP + 80) /* black, or a cover too faint to tap; reflections below */
                    for (int i = 0; i < 3; ++i) assert(ch(px(x, y), i) < 64);
            }
    }
    char *raw = s->raw;
    int n = slide_count(s), side = CF_CX + CF_X1 * CF_EYE / (CF_EYE + CF_Z1), mid = CF_TOP + 80;
    *(int *)(raw + SLIDE_INDEX) = 3, *(int *)(raw + SLIDE_OFFSET) = 0;
    /* A tap on the right-hand cover glides it to the centre, as the wheel's step; the left-hand one,
       back. A tap on the background does nothing. */
    touch_at(EVT_POINTER_DOWN, side, mid, 0); touch_at(EVT_POINTER_UP_BEFORE, side, mid, 80);
    assert(glides == 1 && glide_goal == -CF_STRIDE && glide_easing == SLIDE_EASING && glide_ms == CF_TAP_MS);
    glide_end(s); assert(*(int *)(raw + SLIDE_INDEX) == 4 && !strcmp(album_caption(covers_view), names[4]));
    touch_at(EVT_POINTER_DOWN, 2 * CF_CX - side, mid, 0); touch_at(EVT_POINTER_UP_BEFORE, 2 * CF_CX - side, mid, 80);
    assert(glides == 2 && glide_goal == CF_STRIDE && paused == 0);
    glide_end(s);
    touch_at(EVT_POINTER_DOWN, 5, 5, 0); touch_at(EVT_POINTER_UP_BEFORE, 5, 5, 80);
    assert(glides == 2 && !timer_fn);
    /* A drag follows the finger, CF_DRAG_PX an album, from where it passed the slop. A flick right glides on, decelerating, to a cover further left in the list. */
    touch_at(EVT_POINTER_DOWN, 100, 100, 0);
    touch_at(EVT_POINTER_MOVE_BEFORE, 100 + CF_SLOP - 1, 100, 10); assert(offset(s) == 0);
    touch_at(EVT_POINTER_MOVE_BEFORE, 110, 102, 20); assert(offset(s) == 0);
    touch_at(EVT_POINTER_MOVE_BEFORE, 110 + CF_DRAG_PX / 2, 102, 40); assert(offset(s) == CF_STRIDE / 2);
    touch_at(EVT_POINTER_MOVE_BEFORE, 110 + CF_DRAG_PX, 102, 60); assert(offset(s) == CF_STRIDE);
    touch_at(EVT_POINTER_UP_BEFORE, 110 + CF_DRAG_PX + 10, 102, 64);
    assert(glides == 3 && glide_easing == EASE_CUBIC_OUT && glide_goal % CF_STRIDE == 0 && glide_goal >= 3 * CF_STRIDE);
    assert(glide_ms > CF_SETTLE_MS && glide_ms <= CF_GLIDE_MAX);
    int albums = glide_goal / CF_STRIDE;
    glide_end(s); assert(*(int *)(raw + SLIDE_INDEX) == ((3 - albums) % n + n) % n);
    /* A drag that rests before it lifts settles on the nearest cover; a third of the way it goes back. */
    *(int *)(raw + SLIDE_INDEX) = 3;
    touch_at(EVT_POINTER_DOWN, 200, 100, 0);
    touch_at(EVT_POINTER_MOVE_BEFORE, 200 - 20, 100, 20);
    touch_at(EVT_POINTER_MOVE_BEFORE, 200 - 20 - CF_DRAG_PX * 2 / 3, 100, 60);
    touch_at(EVT_POINTER_UP_BEFORE, 200 - 20 - CF_DRAG_PX * 2 / 3, 100, 60 + CF_STILL_MS + 40);
    assert(glides == 4 && glide_goal == -CF_STRIDE && glide_ms == CF_SETTLE_MS);
    glide_end(s);
    touch_at(EVT_POINTER_DOWN, 200, 100, 0);
    touch_at(EVT_POINTER_MOVE_BEFORE, 200 - 20, 100, 20);
    touch_at(EVT_POINTER_MOVE_BEFORE, 200 - 20 - CF_DRAG_PX / 3, 100, 60);
    touch_at(EVT_POINTER_UP_BEFORE, 200 - 20 - CF_DRAG_PX / 3, 100, 200);
    assert(glides == 5 && glide_goal == 0);
    glide_end(s); assert(*(int *)(raw + SLIDE_INDEX) == 4);
    /* A drag back to where it began lifts at rest: no glide, and the still frame is asked for (the
       moving one drew the edges' covers). */
    touch_at(EVT_POINTER_DOWN, 200, 100, 0);
    touch_at(EVT_POINTER_MOVE_BEFORE, 200 - 20, 100, 20);
    touch_at(EVT_POINTER_MOVE_BEFORE, 200 - 50, 100, 40); assert(offset(s) != 0);
    touch_at(EVT_POINTER_MOVE_BEFORE, 200 - 20, 100, 60); assert(offset(s) == 0);
    invalidated = 0;
    touch_at(EVT_POINTER_UP_BEFORE, 200 - 20, 100, 60 + CF_STILL_MS + 40);
    assert(glides == 5 && invalidated == s);
    /* A slow flick still moves on at least to the next cover, never back against itself. */
    touch_at(EVT_POINTER_DOWN, 200, 100, 0);
    touch_at(EVT_POINTER_MOVE_BEFORE, 200 - 20, 100, 100);
    touch_at(EVT_POINTER_MOVE_BEFORE, 200 - 33, 100, 150); /* 260 px/s */
    touch_at(EVT_POINTER_UP_BEFORE, 200 - 33, 100, 155);
    assert(glides == 6 && glide_goal == -CF_STRIDE);
    glide_end(s); assert(*(int *)(raw + SLIDE_INDEX) == 5);
    /* Up or down is not the covers': no move, no tap. */
    touch_at(EVT_POINTER_DOWN, 200, 100, 0);
    touch_at(EVT_POINTER_MOVE_BEFORE, 203, 130, 20); touch_at(EVT_POINTER_MOVE_BEFORE, 260, 160, 40);
    touch_at(EVT_POINTER_UP_BEFORE, 260, 160, 60);
    assert(glides == 6 && offset(s) == 0 && !timer_fn);
    /* A touch while they glide stops them where they are; lifting settles, and opens nothing. */
    touch_at(EVT_POINTER_DOWN, side, mid, 0); touch_at(EVT_POINTER_UP_BEFORE, side, mid, 50);
    assert(glides == 7);
    *(int *)(raw + SLIDE_OFFSET) = -CF_STRIDE / 3;
    touch_at(EVT_POINTER_DOWN, CF_CX, mid, 100); assert(paused == 1);
    touch_at(EVT_POINTER_UP_BEFORE, CF_CX, mid, 150);
    assert(glides == 8 && glide_goal == 0 && glide_ms == CF_SETTLE_MS && !timer_fn);
    glide_end(s);
    /* A release that never arrived: the next key lets go, and so does the next touch. */
    touch_at(EVT_POINTER_DOWN, 200, 100, 0); touch_at(EVT_POINTER_MOVE_BEFORE, 180, 100, 20);
    touch_at(EVT_POINTER_MOVE_BEFORE, 130, 100, 40);
    coverflow_lost_touch(); assert(glides == 9);
    glide_end(s);
    /* A flick whose moves and release were queued behind a frame, dispatched at once, is timed by
       when they were posted. */
    batch = 500;
    touch_at(EVT_POINTER_DOWN, 200, 100, 0); touch_at(EVT_POINTER_MOVE_BEFORE, 180, 100, 20);
    touch_at(EVT_POINTER_MOVE_BEFORE, 140, 100, 40); touch_at(EVT_POINTER_UP_BEFORE, 140, 100, 45);
    batch = 0;
    assert(glides == 10 && glide_easing == EASE_CUBIC_OUT && glide_goal <= -2 * CF_STRIDE && glide_ms > CF_SETTLE_MS);
    glide_end(s);
    /* The centre cover opens its album. */
    touch_at(EVT_POINTER_DOWN, CF_CX, mid, 0); touch_at(EVT_POINTER_UP_BEFORE, CF_CX, mid, 80);
    assert(glides == 10 && timer_fn);
    int album = *(int *)(raw + SLIDE_INDEX);
    run(); assert(!strcmp(title(), names[album]) && !covers_view->visible);
    key(KEY_RETURN); assert(covers_view->visible);
}

/* A theme's corner radius takes effect at the next page open (cf_restyle refills cf_inset). */
static void restyle(void) {
    const unsigned *rest[7] = { flat[0], flat[1], flat[2], art, flat[4], flat[5], flat[6] };
    theme_t *t = (theme_t *)theme();
    close_page(); t->cf.radius = 0; open_page();
    render(0, rest);
    for (int y = 0; y < 160; ++y)
        for (int x = 0; x < 160; ++x) assert(px(CF_CX - 80 + x, CF_TOP + y) == art[y * 160 + x]); /* square */
    close_page(); t->cf.radius = NP_ART_RADIUS; open_page();
    render(0, rest);
    assert(px(CF_CX - 80 + 159, CF_TOP) != art[159]); /* rounded again */
    close_page();
}

/* The tick shelf's frames (shim_themes[0]), dark and light, at rest and mid-turn: the same bytes as ever. */
static void pinned(void) {
    const unsigned *ring[7];
    for (int j = 0; j < 7; ++j) ring[j] = j == 3 ? art : flat[j];
    unsigned hash = 2166136261u;
    for (light = 0; light < 2; ++light)
        for (int frac = -ONE / 2; frac < ONE / 2; frac += ONE / 5) {
            render(frac, ring);
            for (unsigned i = 0; i < sizeof(frame) / 4; ++i) hash = (hash ^ frame[i]) * 16777619u;
        }
    light = 0;
    assert(hash == PINNED_FRAMES);
}

/* The door's shelf (the thumb fixture, SHELF_180): side covers facing the centre, their edges meeting there, on a horizon 63%
   down the cover, each flat colour exact out to the third, reflections under the slanted feet. */
static void door_shelf(void) {
    static unsigned f[300 * PITCH], g[300 * PITCH];
    const cf_style_t *c = &shim_themes[1].cf;
    const unsigned *wide[15], **ring = flat_ring(wide);
    alt = 1, frame_fail = 0;
    open_page();
    widget *s = slide();
    int *geo = (int *)s->raw, H = c->view_h, canvas[16] = { 0 }, top = c->top, hz = top + 160 * c->horizon / 100;
    /* The page: one frame as tall as the theme says, its stride kept, drawn 1:1; the captions in their boxes. */
    assert(frames == 1 && geo[3] == H && slide_menu_item_width(s) + *(int *)(s->raw + SLIDE_SPACER) == c->stride);
    *(int *)(s->raw + SLIDE_INDEX) = 3; *(int *)(s->raw + SLIDE_OFFSET) = 0;
    coverflow_paint(s, canvas);
    assert(drawn[2] == CF_VIEW_W && drawn[3] == H && drawn_format == 3); /* the screen's, so stock copies it */
#define F(x, y) f[(y) * PITCH + (x)]
    for (unsigned i = 0; i < sizeof(f) / 4; ++i) f[i] = 0x12345678u;
    coverflow_render(f, PITCH, 0, ring);
    for (int y = 0; y < 300; ++y)
        for (int x = 0; x < PITCH; ++x) assert((y < H && x < CF_VIEW_W) == (F(x, y) != 0x12345678u));
    /* The outer edge, its height and where it sits on the horizon, a cover at a time; no shading. */
    static const int out[3] = { 121, 154, 188 }, tall[2] = { 166, 164 };
    int prev = 0, h1 = 0;
    for (int j = 1; j <= 3; ++j) {
        int edge = -1, rows = 0, first = -1;
        for (int x = CF_CX; x < CF_VIEW_W; ++x)
            if (F(x, hz) == colours[3 + j]) edge = x;
        assert(abs(edge + 1 - CF_CX - out[j - 1]) <= 1);
        if (j > 1 && j < 3) assert(abs(edge + 1 - CF_CX - prev - 33) <= 1);
        prev = edge + 1 - CF_CX;
        for (int y = 0; y < H; ++y) /* most of the row: the reflection stays under that */
            if (ch(F(edge, y), 0) + ch(F(edge, y), 1) + ch(F(edge, y), 2) >= 256) first = first < 0 ? y : first, ++rows;
        if (j < 3) assert(abs(rows - tall[j - 1]) <= 2);
        if (j == 1) h1 = rows;
        assert(abs((hz - first) * 100 / rows - 63) <= 2);
        for (int x = 0; x < CF_VIEW_W; ++x) assert(F(x, H - 1) == 0xff000000u); /* no reflection cut */
    }
    assert(h1);
    /* Top and bottom edges of the first cover meet within 3 px of the centre column. */
    int xs[2] = { CF_CX + 84, CF_CX + 118 }, hs[2] = { 0, 0 };
    for (int i = 0; i < 2; ++i)
        for (int y = 0; y < H; ++y) hs[i] += F(xs[i], y) == colours[4];
    assert(hs[1] > hs[0]);
    assert(abs((xs[0] - CF_CX) - hs[0] * (xs[1] - xs[0]) / (hs[1] - hs[0])) <= 3);
    /* The centre cover is square at 58 to 218 on the screen under the 26 px bar. */
    assert(top + 26 == 58 && F(CF_CX, top - 1) == 0xff000000u && F(CF_CX, top) == colours[3] && F(CF_CX, top + 159) == colours[3]);
    /* Its reflection: 46 rows, from 35% of the cover, never brighter going down, all in the frame. */
    int rows = 0, last = 256;
    for (int y = top + 160; y < H; ++y) {
        int v = ch(F(CF_CX, y), 0);
        assert(v <= last);
        if (y == top + 160) assert(abs(v - 255 * 35 / 100) <= 3);
        rows += v > 0, last = v;
    }
    assert(rows >= 45 && rows <= 46);
    /* The side covers' reflections start at their own feet, lower towards the outer edge. */
    int foot[2];
    for (int i = 0; i < 2; ++i) {
        foot[i] = 0;
        for (int y = hz; y < H; ++y) foot[i] = F(xs[i], y) == colours[4] ? y + 1 : foot[i];
        assert(slot_of(F(xs[i], foot[i] + 1)) == 4 && F(xs[i], foot[i] + 1) != colours[4]);
    }
    assert(foot[1] > foot[0]);
    /* The hit test follows the horizon; the third cover is a target. */
    assert(coverflow_hit(0, CF_CX, top - 1) == 99 && coverflow_hit(0, CF_CX, top) == 0);
    assert(coverflow_hit(0, CF_CX, top + 159) == 0 && coverflow_hit(0, CF_CX, top + 160) == 99);
    assert(coverflow_hit(0, CF_CX + 100, hz) == 1 && coverflow_hit(0, CF_CX + 140, hz) == 2 && coverflow_hit(0, 373, hz) == 3);
    assert(coverflow_hit(0, 2 * CF_CX - 1 - 373, hz) == -3);
    /* Mid-turn every step changes only a little, including at the fourth cover's edge of reach and
       where the centre changes hands. */
    const unsigned *next_wide[15], **next = next_wide + 4;
    for (int j = -4; j <= 10; ++j) next[j] = ring[j + 1 <= 10 ? j + 1 : j - 6];
    int worst = 0;
    for (int fr = -ONE / 2; fr + ONE / 256 < ONE / 2; fr += ONE / 256) {
        coverflow_render(f, PITCH, fr, ring);
        coverflow_render(g, PITCH, fr + ONE / 256, ring);
        int d = differ(f, g, 24);
        worst = d > worst ? d : worst;
    }
    assert(worst < CF_VIEW_W * CF_VIEW_H / 50);
    coverflow_render(f, PITCH, ONE / 2 - 1, ring);
    coverflow_render(g, PITCH, -ONE / 2, next);
    assert(differ(f, g, 6) < 200);
    /* The light half: the shelf is the scheme's paper, and the reflection fades into it. */
    light = 1;
    coverflow_render(f, PITCH, 0, ring);
    assert(F(0, 0) == 0xffffffffu && F(CF_CX, top) == colours[3]);
    assert(abs(ch(F(CF_CX, top + 160), 2) - 255 * 65 / 100) <= 3 && F(CF_CX, top + 160 + 46) == 0xffffffffu);
    light = 0;
    close_page();
    alt = 0;
    open_page(); /* shim_themes[0]'s values again */
    close_page();
#undef F
}

/* The door's step (coverflow.shelf.swing 1): at rest, the bytes of its path switched off; mid-way the near edge swells
   to about 173 px (165 at rest) and eases out to 3 px past its rest place, where switched off it reaches about 198 and 48 px. */
static void cf_near_edge(const unsigned *f, int H, int hz, int j, int *reach, int *rows) { /* cover j, on the right */
    int edge = -1, n = 0;
    for (int x = CF_CX; x < CF_VIEW_W; ++x) edge = f[hz * PITCH + x] == colours[j] ? x : edge;
    assert(edge >= CF_CX);
    for (int y = 0; y < H; ++y) { /* most of the row: the reflection stays under that */
        unsigned p = f[y * PITCH + edge];
        n += ch(p, 0) + ch(p, 1) + ch(p, 2) >= 256;
    }
    *reach = edge + 1 - CF_CX, *rows = n;
}
static void cf_step_peak(unsigned *f, int H, int hz, const unsigned *const *ring, int *reach, int *tallest) {
    *reach = *tallest = 0;
    for (int fr = -ONE / 2; fr < ONE / 2; fr += ONE / 128) { /* the cover turning on the right: the centre's, then the next */
        int x, rows;
        coverflow_render(f, PITCH, fr, ring);
        cf_near_edge(f, H, hz, fr < 0 ? 3 : 4, &x, &rows);
        *reach = x > *reach ? x : *reach, *tallest = rows > *tallest ? rows : *tallest;
    }
}
/* The largest change of an edge's speed from one frame to the next (1/16 px), through a follow of 81 ms from rest at
   60 fps: t falling from 1 for the cover moving in, rising from 0 for the one moving out. */
int cf_path_x(int t, int u);
static int cf_step_jerk(int moving_in, int u, int first) { /* from frame first */
    enum { FRAMES = 33 };
    int x[FRAMES + 1], worst = 0;
    for (int i = 0; i <= FRAMES; ++i) {
        double s = i * (1000 / 60.0) / 81;
        int eased = i == FRAMES ? ONE : (int)(ONE * (1 - (1 + s) * exp(-s)));
        x[i] = cf_path_x(moving_in ? ONE - eased : eased, u);
    }
    for (int i = first; i < FRAMES; ++i) {
        int jerk = abs(x[i + 1] - 2 * x[i] + x[i - 1]);
        worst = jerk > worst ? jerk : worst;
    }
    return worst;
}
static void door_step(void) {
    static unsigned f[300 * PITCH], off[300 * PITCH];
    cf_style_t *c = &shim_themes[1].cf;
    const unsigned *wide[15], **ring = flat_ring(wide);
    int H = c->view_h, hz = c->top + 160 * c->horizon / 100, rest, rows, reach, tallest;
    assert(c->swing == 1 && c->lift == 20);
    alt = 1, frame_fail = 0;
    c->swing = 0;
    open_page();
    for (unsigned i = 0; i < sizeof(off) / 4; ++i) off[i] = f[i] = 0;
    coverflow_render(off, PITCH, 0, ring);
    cf_step_peak(f, H, hz, ring, &reach, &tallest);
    assert(reach > 160 && tallest > 190); /* what the swing takes away */
    close_page();
    c->swing = 1;
    open_page();
    for (unsigned i = 0; i < sizeof(f) / 4; ++i) f[i] = 0;
    coverflow_render(f, PITCH, 0, ring);
    assert(!memcmp(f, off, sizeof(f)));
    cf_near_edge(f, H, hz, 4, &rest, &rows);
    cf_step_peak(f, H, hz, ring, &reach, &tallest);
    assert(reach > rest && reach <= rest + 3);
    assert(abs(tallest - 173) <= 1 && abs(rows - 165) <= 1);
    /* no corner in the path: about 3 px a frame moving in, 6 at most moving out, where the near edge's quick start
       settles without a stop and the far edge's start is steep on any path through this short an eye. The landing,
       the last 300 ms: a px or two moving in, under a px moving out, with no dead stop. */
    enum { NEAR = 16 * 80, FAR = -NEAR };
    assert(cf_step_jerk(1, FAR, 1) <= 56 && cf_step_jerk(1, NEAR, 1) <= 28);
    assert(cf_step_jerk(0, NEAR, 1) <= 78 && cf_step_jerk(0, FAR, 1) <= 96);
    assert(cf_step_jerk(1, FAR, 15) <= 34 && cf_step_jerk(1, NEAR, 15) <= 28);
    assert(cf_step_jerk(0, NEAR, 15) <= 4 && cf_step_jerk(0, FAR, 15) <= 12);
    close_page();
    alt = 0;
    open_page(); /* shim_themes[0]'s values again */
    close_page();
}

/* A cover's far edge under the widened eye (coverflow.shelf.eye_turn): the near edge's path and height stay, the far edge's
   is the model's, and every rest pose is the bytes it was. */
static void door_eye(void) {
    static unsigned f[300 * PITCH], off[300 * PITCH];
    cf_style_t *c = &shim_themes[1].cf;
    const unsigned *wide[15], **ring = flat_ring(wide);
    enum { NEAR = 16 * 80, FAR = -NEAR };
    assert(c->eye_turn == 150);
    alt = 1, frame_fail = 0;
    c->eye_turn = 0;
    open_page();
    coverflow_render(off, PITCH, 0, ring);
    int near0[5], slot0[3][2];
    for (int i = 1; i < 5; ++i) near0[i] = cf_path_x(i * ONE / 5, NEAR);
    for (int i = 0; i < 3; ++i) slot0[i][0] = cf_path_x((i + 1) * ONE, NEAR), slot0[i][1] = cf_path_x((i + 1) * ONE, FAR);
    close_page();
    c->eye_turn = 150;
    open_page();
    coverflow_render(f, PITCH, 0, ring);
    assert(!memcmp(f, off, sizeof(f)));
    for (int i = 0; i < 3; ++i) /* the slots: both edges, as with no widening */
        assert(cf_path_x((i + 1) * ONE, NEAR) == slot0[i][0] && cf_path_x((i + 1) * ONE, FAR) == slot0[i][1]);
    const double T = 89 * M_PI / 180, s1 = sin(T), c1 = cos(T), E0 = 94, HW = 80, Z1 = 77;
    const double PK = (116 + HW * c1) * E0 / (E0 + Z1 - HW * s1) + 3;
    for (int i = 1; i < 5; ++i) {
        double u = i / 5.0, s = sin(T * u), co = cos(T * u), z0 = Z1 * s / s1 - 20 * u * (1 - u), d0 = E0 + z0 - HW * s;
        double O = u < .45 ? PK - (PK - 80) * (.45 - u) * (.45 - u) / (.45 * .45) : PK - 3 * (u - .45) * (u - .45) / (.55 * .55);
        double E = E0 + 150 * (1 - u * u * u * u), xc = O * d0 / E0 - HW * co, zc = E * d0 / E0 - E + HW * s;
        double far = (xc - HW * co) * E / (E + zc + HW * s), near = (xc + HW * co) * E / (E + zc - HW * s);
        assert(fabs(cf_path_x(i * ONE / 5, FAR) / 16.0 - far) < .5);
        assert(fabs(cf_path_x(i * ONE / 5, NEAR) / 16.0 - near) < .5);
        assert(abs(cf_path_x(i * ONE / 5, NEAR) - near0[i]) <= 2);
    }
    /* A tap mid-turn lands on the cover drawn there, under the wider eye. Down to the horizon: every cover's body,
       above any reflection. */
    int hz = c->top + 160 * c->horizon / 100, hits = 0;
    for (int frac = -ONE / 2; frac < ONE / 2; frac += ONE / 8) {
        coverflow_render(f, PITCH, frac, ring);
        for (int y = 1; y <= hz; ++y)
            for (int x = 1; x < CF_VIEW_W - 1; ++x) {
                int j = coverflow_hit(frac, x, y), same = 1;
                for (int d = 0; d < 4; ++d) same &= coverflow_hit(frac, x + (d & 1 ? d - 2 : 0), y + (d & 1 ? 0 : d - 1)) == j;
                if (!same || j == 99) continue;
                assert(slot_of(f[y * PITCH + x]) == (j + 10) % 7); /* the ring's colours go round */
                ++hits;
            }
    }
    assert(hits > 100000);
    close_page();
    alt = 0;
    open_page();
    close_page();
}

/* The door's step as a follow: the shares stock's animator gets, as the whole px it writes, against the critically damped
   model, from rest and through ticks that move the goal mid-step, with its speed carried across each. */
float coverflow_follow(float);
int coverflow_follow_aim(int, int, int);
static double follow_x, follow_v; /* the model: px, px/ms */
static void follow_step(double goal, double ms, double tau) {
    double w = 1 / tau, y = follow_x - goal, B = follow_v + w * y, e = exp(-w * ms);
    follow_x = goal + (y + B * ms) * e, follow_v = (B - w * (y + B * ms)) * e;
}
static void door_follow(void) {
    enum { TAU = 81, STRIDE = 110 };
    /* from rest: the share is the model's, starts at no speed, stays inside the goal and ends on it */
    int T = coverflow_follow_aim(-STRIDE, TAU, -1);
    assert(T >= 550 && T <= 650);
    assert(abs((int)(-STRIDE * coverflow_follow((float)(T - 1) / T)) + STRIDE) <= 1); /* the last move: a whole px */
    follow_x = 0, follow_v = 0;
    double last = 0;
    for (int t = 0; t < T; t += 16) {
        double m = (double)t / TAU, want = 1 - (1 + m) * exp(-m), got = coverflow_follow((float)t / T);
        assert(fabs(got - want) < .002 && got >= last && got < 1);
        last = got;
    }
    assert(coverflow_follow(1) == 1);
    /* ticks at these ms after the one before: the whole px against the model, the speed across each */
    static const int gaps[] = { 75, 120, 40, 200, 60 };
    int from = 0, goal = -STRIDE, elapsed = -1;
    follow_x = 0, follow_v = 0;
    T = coverflow_follow_aim(goal - from, TAU, elapsed);
    for (unsigned i = 0; i < sizeof gaps / sizeof *gaps; ++i) {
        double before = 0;
        for (int t = 16; t <= gaps[i]; t += 16) {
            follow_step(goal, 16, TAU);
            int px = (int)(from + (goal - from) * (double)coverflow_follow((float)t / T));
            assert(abs(px - (int)follow_x) <= 1);
            before = (goal - from) * ((double)coverflow_follow((float)t / T) - coverflow_follow((float)(t - 1) / T));
        }
        int at = gaps[i] / 16 * 16;
        from = (int)(from + (goal - from) * (double)coverflow_follow((float)at / T));
        follow_x = from;
        goal -= STRIDE;
        T = coverflow_follow_aim(goal - from, TAU, at);
        double after = (goal - from) * ((double)coverflow_follow(1.0f / T) - coverflow_follow(0));
        assert(fabs(after - before) < .08); /* px a ms: nothing a frame could show */
    }
    /* A reversal carries on a moment, then comes back, with the same speed at the turn. */
    double before = (goal - from) * ((double)coverflow_follow(101.0f / T) - coverflow_follow(100.0f / T));
    assert(before < 0);
    from = (int)(from + (goal - from) * (double)coverflow_follow(100.0f / T)), goal = from + 60;
    T = coverflow_follow_aim(goal - from, TAU, 100);
    assert(fabs((goal - from) * ((double)coverflow_follow(1.0f / T) - coverflow_follow(0)) - before) < .08);
    assert(coverflow_follow(4.0f / T) < 0 && coverflow_follow(1) == 1);
    /* A goal right ahead of a fast cover never overshoots, and a goal where it stands is no division by nothing. */
    T = coverflow_follow_aim(-STRIDE * 8, TAU, -1);
    T = coverflow_follow_aim(-3, TAU, 150);
    for (int t = 0; t < T; t += 8) assert(coverflow_follow((float)t / T) < 1);
    T = coverflow_follow_aim(0, TAU, 100);
    assert(T > 0 && fabs(coverflow_follow(.5f)) < 2);
}

/* The frames as drawn: the screen's BGRA8888, opaque, red at bit 16 (texel_render's, R and B
   swapped); at rest the centre cover's middle texel, swapped. */
static void bgra_frames(void) {
    static unsigned raw[(CF_VIEW_H + 3) * PITCH];
    const unsigned *ring[7];
    for (int j = 0; j < 7; ++j) ring[j] = j == 3 ? art : flat[j];
    for (light = 0; light < 2; ++light) {
        bgra_render(raw, PITCH, ONE / 5, ring);
        render(ONE / 5, ring);
        for (int y = 0; y < CF_VIEW_H; ++y)
            for (int x = 0; x < CF_VIEW_W; ++x) assert(raw[y * PITCH + x] == texel(px(x, y)) && raw[y * PITCH + x] >> 24 == 255);
    }
    light = 0;
    bgra_render(raw, PITCH, 0, ring);
    unsigned a = art[80 * 160 + 80];
    assert(raw[(CF_TOP + 80) * PITCH + CF_CX] == (0xff000000u | (a & 0xff00u) | (a & 255u) << 16 | (a >> 16 & 255u)));
}

/* column() as it was before its fast path (1.7.5): column_fuzz's reference, on a frame and solid
   span of its own, c and paper as the open page has them. */
#define ART_SIZE 160
static unsigned ref_shade(unsigned p, unsigned b) {
    return ((p & 0xff00ffu) * b >> 8 & 0xff00ffu) | ((p & 0xff00u) * b >> 8 & 0xff00u);
}
static void ref_put(unsigned *px, unsigned colour, unsigned cov) {
    unsigned a = *px >> 24, have = a + (a >> 7), w = cov * (256 - have) >> 8;
    if (!a && cov == 256) {
        *px = colour | 0xff000000u;
        return;
    }
    if (!w) return;
    have += w;
    *px = ((*px & 0xffffffu) + ref_shade(colour, w)) | (have > 255 ? 255u : have) << 24;
}
/* Each row's coverage of a column by the plain rule: the share of the row the cover spans, times, in a corner's
   columns, the corner pixel's coverage (corner_cover.c) at the row's texel. */
static void ref_column(const cf_style_t *c, unsigned paper, unsigned *d0, int pitch, short *solid, int col, const unsigned *tex, int h, int bright, int k) {
    if (h < 16) return;
    unsigned *d = d0 + col;
    int R = ART_SIZE * c->radius / 166;
    const unsigned char *cover = corner_cover(R);
    int kc = k < 0 || !cover ? -1 : k < R ? k : k >= ART_SIZE - R ? ART_SIZE - 1 - k : -1;
    int top = 256 * c->top + 40960 * c->horizon / 100 - h * c->horizon / 100, bottom = top + h, step = (ART_SIZE << 20) / (h >> 4);
    int solid_top = solid[0], solid_bottom = solid[1], part = 0;
    int clear = 0;
    while (kc >= 0 && part < R && cover[part * R + kc] < 255) clear += !cover[part * R + kc], ++part;
    int out = h * clear / ART_SIZE; /* the rows wholly clear at either end are not visited */
    int y = (top + out) >> 8, end = (bottom - out + 255) >> 8, last = end < c->view_h ? end : c->view_h;
    int v = (((y << 8) + 128 - top) >> 4) * step >> 4;
    if (y < 0) v -= y * step, y = 0;
    unsigned *px = d + y * pitch, wash = ref_shade(paper, 256u - (unsigned)bright);
    for (; y < last; ++y, v += step, px += pitch) {
        if (y >= solid_top && y < solid_bottom) continue;
        int row = v < 0 ? 0 : v >> 16 >= ART_SIZE ? ART_SIZE - 1 : v >> 16, cr = row < ART_SIZE / 2 ? row : ART_SIZE - 1 - row;
        int from = y << 8 > top ? y << 8 : top, to = (y + 1) << 8 < bottom ? (y + 1) << 8 : bottom;
        unsigned cov = (unsigned)(to - from);
        if (cr < part) cov = cov * (cover[cr * R + kc] + (cover[cr * R + kc] >> 7)) >> 8;
        if (cov) ref_put(px, bright >= 256 ? tex[row * ART_SIZE] : ref_shade(tex[row * ART_SIZE], (unsigned)bright) + wash, cov);
    }
    int reflect = h * c->reflect / ART_SIZE, stop = (bottom + reflect + 255) >> 8;
    int fade = (256 << 16) / reflect;
    unsigned dim = (unsigned)bright * c->reflect_top >> 8;
    wash = ref_shade(paper, 256u - dim);
    if (stop > c->view_h) stop = c->view_h;
    for (y = (bottom + out) >> 8, px = d + y * pitch; y < stop; ++y, px += pitch) {
        int from = y << 8 > bottom ? y << 8 : bottom, dist = ((from + ((y + 1) << 8)) >> 1) - bottom;
        if (dist >= reflect) break;
        if (y < 0 || (y >= solid_top && y < solid_bottom)) continue;
        int mirrored = dist * step >> 8 >> 16;
        int row = ART_SIZE - 1 - (mirrored >= ART_SIZE ? ART_SIZE - 1 : mirrored);
        unsigned cov = (unsigned)(((y + 1) << 8) - from) * (256u - (unsigned)(dist * fade >> 16)) >> 8;
        if (mirrored < part) cov = cov * (cover[mirrored * R + kc] + (cover[mirrored * R + kc] >> 7)) >> 8;
        ref_put(px, ref_shade(tex[row * ART_SIZE], dim) + wash, cov);
    }
    int ins = h * part / ART_SIZE;
    int s0 = (top + ins + 255) >> 8, s1 = (bottom - ins) >> 8;
    s0 = s0 < 0 ? 0 : s0, s1 = s1 > c->view_h ? c->view_h : s1;
    if (s1 <= s0) return;
    if (solid_bottom > solid_top && s0 <= solid_bottom && s1 >= solid_top) {
        s0 = s0 < solid_top ? s0 : solid_top;
        s1 = s1 > solid_bottom ? s1 : solid_bottom;
    } else if (s1 - s0 <= solid_bottom - solid_top)
        return;
    solid[0] = (short)s0, solid[1] = (short)s1;
}
void cf_column(unsigned *, int, short *, int, const unsigned *, int, int, int);
/* column() byte for byte its reference, over many random columns in both shim themes, dark and
   light: any height to three times the rest's, brightness (whole half the time), the texel across
   (a corner's column a third of the time), texture column, solid span, and what lies under it
   (empty, opaque or part covered). */
static void column_fuzz(void) {
    enum { ROWS = 300, N = 40000 };
    static unsigned a[ROWS * PITCH], b[ROWS * PITCH], tex[160 * 160];
    unsigned r = 12345;
#define RND() (r = r * 1103515245u + 12345u, r >> 8)
    for (int i = 0; i < 160 * 160; ++i) tex[i] = RND() << 8 ^ RND();
    for (alt = 0; alt < 2; ++alt) {
        open_page();
        const cf_style_t *c = &theme()->cf;
        for (light = 0; light < 2; ++light)
            for (int n = 0; n < N; ++n) {
                int col = (int)(RND() % CF_VIEW_W), h = (int)(RND() % (3 * 40960)), bright = RND() & 1 ? 256 : (int)(RND() % 256) + 1;
                int k = RND() % 3 ? -1 : (int)(RND() % 160); /* the shim themes' radius makes some corner columns */
                const unsigned *t = tex + RND() % 160;
                short sa[2] = { 0, 0 }, sb[2];
                if (RND() % 3) {
                    int s0 = (int)(RND() % (c->view_h + 1)), s1 = (int)(RND() % (c->view_h + 1));
                    sa[0] = (short)(s0 < s1 ? s0 : s1), sa[1] = (short)(s0 < s1 ? s1 : s0);
                }
                sb[0] = sa[0], sb[1] = sa[1];
                for (int y = 0; y < ROWS; ++y) {
                    unsigned k = RND() % 4, p = RND() << 8 ^ RND();
                    a[y * PITCH + col] = b[y * PITCH + col] = k == 0 ? 0 : k == 1 ? p | 0xff000000u : p;
                }
                ref_column(c, light ? scheme()->paper : 0, a, PITCH, sa, col, t, h, bright, k);
                cf_column(b, PITCH, sb, col, t, h, bright, k);
                assert(sa[0] == sb[0] && sa[1] == sb[1]);
                for (int y = 0; y < ROWS; ++y) assert(a[y * PITCH + col] == b[y * PITCH + col]);
            }
        close_page();
    }
#undef RND
    alt = 0, light = 0;
}
#undef ART_SIZE

static void depth(void) {
    renderer();
    pinned();
    bgra_frames();
    column_fuzz();
    /* On the page: the slide_menu spans the frame, one album per CF_STRIDE px, with empty children
       under the drawn covers; the captions sit under the covers, over the reflections' tail. */
    frame_fail = 0;
    open_page();
    widget *s = slide();
    char *raw = s->raw;
    int *geo = (int *)raw;
    assert(frames == 1 && geo[0] == 0 && geo[1] == 0 && geo[2] == CF_VIEW_W && geo[3] == CF_VIEW_H);
    assert(s == &w[insensitive]); /* the covers' view takes the touch, never stock's slide_menu */
    assert(slide_menu_item_width(s) + *(int *)(raw + SLIDE_SPACER) == CF_STRIDE);
    for (int i = 0; i < slide_count(s); ++i) assert(!w[s->kids[i]].image[0]);
    widget *covers_view = &w[s->parent];
    int labels = 0;
    for (int i = 0; i < covers_view->nkids; ++i) {
        widget *l = &w[covers_view->kids[i]];
        if (strcmp(l->type, "hscroll_label")) continue;
        int *g = (int *)l->raw;
        const cf_style_t *c = &theme()->cf; /* the album's caption at text_y, the artist's artist_y under it */
        assert(g[0] == CF_EDGE && g[2] == 375 - 2 * CF_EDGE && g[1] == c->text_y + labels * c->artist_y && g[1] >= CF_TOP + 160);
        /* the artist in the design's Artist role, Text Secondary: never stock's #AAAAAA, which the theme paints Tertiary */
        if (labels == 1) assert((unsigned)l->fg == (0xff000000u | RGB_SECONDARY));
        ++labels;
    }
    assert(labels == 2);
    /* Paint: the seven covers around the position decoded (each load released at once; albums 2
       and 3, Embedded and None, have only the empty marker and show the placeholder), one render,
       drawn 1:1 at the slide_menu's origin. A repaint in place only draws again. */
    int canvas[16] = { 0 };
    *(int *)(raw + SLIDE_INDEX) = 3; *(int *)(raw + SLIDE_OFFSET) = 0;
    loads = unloads = 0;
    coverflow_paint(s, canvas);
    assert(draws == 1 && locks == 1 && drawn[0] == 0 && drawn[1] == 0 && drawn[2] == CF_VIEW_W && drawn[3] == CF_VIEW_H);
    assert(loads == 5 && unloads == 5 && reads == 5);
    coverflow_paint(s, canvas);
    coverflow_paint(covers_view, canvas); /* other widgets: nothing */
    assert(draws == 2 && locks == 1 && loads == 5);
    capture("rest");
    /* Turning a quarter, a half and three quarters towards the next album draws from the live
       offset without loading; crossing into the next album loads just the one entering reach. */
    static const char *turns[] = { "quarter", "half", "three-quarter" };
    for (int q = 1; q < 4; ++q) {
        *(int *)(raw + SLIDE_OFFSET) = -CF_STRIDE * q / 4;
        coverflow_paint(s, canvas);
        assert(locks == 1 + q && loads == 5 + (q >= 2)); /* past half, album 7 comes into reach */
        capture(turns[q - 1]);
    }
    *(int *)(raw + SLIDE_OFFSET) = 0; *(int *)(raw + SLIDE_INDEX) = 4;
    coverflow_paint(s, canvas);
    assert(loads == 6 && unloads == 6);
    /* The wheel's centre press clicks the centre child, which opens its tracks. */
    *(int *)(raw + SLIDE_INDEX) = 6;
    int (*waiting)(const void *) = timer_fn;
    click_album(s, 6);
    assert(timer_fn != waiting);
    run();
    assert(!strcmp(title(), names[6]) && !covers_view->visible && last_album(6)); /* kept for a reboot */
    assert(covers_view->numb); /* stock hit-tests hidden widgets: the list below takes the touch */
    key(KEY_RETURN); /* back to the same album, still drawn */
    assert(covers_view->visible && !covers_view->numb && *(int *)(raw + SLIDE_INDEX) == 6);
    coverflow_paint(s, canvas);
    touches(s, covers_view);
    /* Auto Light Mode refreshes a stationary frame and its composited placeholder in RAM. */
    *(int *)(raw + SLIDE_INDEX) = 3;
    theme_placeholder = 1;
    for (int mode = 1; mode >= 0; --mode) {
        light = mode;
        int before = locks;
        coverflow_paint(s, canvas);
        unsigned paper = 0xff000000u | (mode ? LIGHT_PAPER * 0x010101u : 0);
        assert(shown[0] == paper && shown[(CF_TOP + 80) * CF_VIEW_W + CF_CX] == paper);
        assert(locks == before + 1 && frames == 1);
        before = locks;
        int decoded = loads;
        coverflow_paint(s, canvas);
        assert(locks == before && loads == decoded); /* no work on the next unchanged paint */
    }
    theme_placeholder = 0;
    /* Refresh and close release the frame and every texture. */
    close_page();
    assert(frames == 0 && loads == unloads);
    {   /* Reopened, the covers centre the queue's song's album (#350), not the one last centred; no
           queue, a song with no album tag (a folder play's) or one whose album the list lacks (a
           same-named album in another folder) leaves the last one. A compilation's track, with an artist of its own, still finds its album. */
        void *pos = mmap((void *)(MCL_POS & ~4095), 4096, PROT_READ | PROT_WRITE,
                         MAP_PRIVATE | MAP_ANONYMOUS | MAP_FIXED_NOREPLACE, -1, 0);
        assert(pos != MAP_FAILED);
        deque playing = {0};
        char untagged[sizeof records[0]] = {0}, elsewhere[sizeof records[0]], guest[sizeof records[0]];
        memcpy(elsewhere, records[1], sizeof elsewhere);
        *(char **)(elsewhere + REC_PATH) = "/mnt/mmc/audiobooks/Folder/01.m4b";
        memcpy(guest, records[2], sizeof guest);
        *(char **)(guest + REC_ARTIST) = "A guest";
        int at[5];
        void *songs[5] = { 0, untagged, elsewhere, guest, records[1] };
        for (int k = 0; k < 5; ++k) {
            playing.n = 1; playing.at[0] = songs[k];
            shim_queue = songs[k] ? &playing : 0;
            open_page();
            at[k] = *(int *)(slide()->raw + SLIDE_INDEX);
            close_page();
        }
        assert(at[0] == 6 && at[1] == 6 && at[2] == 6 && at[3] == 2 && at[4] == 1);
        shim_queue = 0;
        munmap(pos, 4096);
    }
    /* Small libraries: two albums wrap round the ring (no Sort or Refresh card: Display settings' rows
       now), each album decoded once however often it repeats; missing art shows the placeholder. */
    int keep = albums;
    albums = 2; rescan();
    open_page();
    s = slide(); raw = s->raw;
    loads = unloads = 0;
    coverflow_paint(s, canvas);
    assert(slide_count(s) == 2 && loads <= 2 && loads == unloads);
    render(0, (const unsigned *const[7]){ 0 }); /* nothing to draw: all black */
    for (int i = 0; i < CF_VIEW_W; ++i) assert(px(i, CF_TOP + 80) == 0xff000000u);
    close_page();
    albums = 1; rescan();
    open_page(); s = slide();
    coverflow_paint(s, canvas);
    assert(slide_count(s) == 1);
    capture("one-album");
    close_page();
    albums = keep; rescan();
    /* Allocation failure: the flat covers, as before. */
    tex_fail = 1;
    open_page(); s = slide();
    int flat_images = 0;
    for (int i = 0; i < slide_count(s); ++i) flat_images += !strncmp(w[s->kids[i]].image, "file://", 7);
    assert(frames == 0 && ((int *)s->raw)[1] == 24 && ((int *)s->raw)[3] == 160 && flat_images > 0);
    for (int i = 0; i < slide_count(s); ++i) assert(w[s->kids[i]].image[0]); /* art or the placeholder, as before */
    int before = draws;
    coverflow_paint(s, canvas);
    assert(draws == before);
    close_page();
    assert(loads == unloads);
    tex_fail = 0;
}

/* Library settings' Cover Flow Sort picker (coverflow_sort_set): each press takes the next order, saved and
   taken at the next open; ties keep the stock order. */
static const char *album_at(int i) {
    static char got[160];
    widget *s = slide();
    click_album(s, i);
    run();
    snprintf(got, sizeof got, "%s", title());
    key(KEY_RETURN);
    return got;
}
static void order_is(const char *const *want) {
    for (int i = 0; want[i]; ++i) assert(!strcmp(album_at(i), want[i]));
}
static int saved_sort(void) {
    int v = -1;
    FILE *f = fopen(PEQ_ROOT "/mnt/data/ringnav-coversort", "rb");
    if (f) assert(fread(&v, sizeof v, 1, f) == 1), fclose(f);
    return v;
}
extern void coverflow_sort_set(int), coverflow_refresh_art(void);
extern int coverflow_sort_now(void), coverflow_sort_count(void);
static void press_sort(void) { /* the picker's next choice, as library_settings.c's rows take it */
    coverflow_sort_set((coverflow_sort_now() + 1) % coverflow_sort_count());
    close_page();
    open_page();
}
extern int coverflow_jump(void *, int, int, unsigned *);

static void sorting(void) {
    mkdir(PEQ_ROOT "/mnt/data", 0755);
    /* Cover Folder Embedded None New A B C Tight; B is The Beatles', Tight Abba's. */
    assert(albums == 9 && !strcmp(names[8], "Tight"));
    *(const char **)(records[6] + REC_ARTIST) = "The Beatles";
    *(const char **)(records[8] + REC_ARTIST) = "Abba";
    rescan();
    open_page();
    widget *s = slide();
    assert(slide_count(s) == albums);
    order_is((const char *const[]){ "Cover", "Folder", "Embedded", "None", "New", "A", "B", "C", "Tight", 0 });
    int q = queries;
    /* Artist, articles aside (The Beatles under B), then year (the query: None, Folder), then stock. */
    ranked[0] = "none", ranked[1] = "Folder", ranked[2] = 0;
    press_sort();
    assert(saved_sort() == 1 && queries == q + 1); /* only the ranking is queried */
    order_is((const char *const[]){ "Tight", "None", "Folder", "Cover", "Embedded", "New", "A", "C", "B", 0 });
    unsigned letter;
    assert(coverflow_jump(slide(), 0, 1, &letter) == 8 && letter == 'B');
    assert(coverflow_jump(slide(), 1, 1, &letter) == 7 && letter == 'B');
    assert(coverflow_jump(slide(), 8, -1, &letter) == 8 && letter == 'A');
    /* Year: the query's order (newest first, undated last), the rest after. */
    ranked[0] = "New", ranked[1] = "a", ranked[2] = "Cover", ranked[3] = 0;
    rank_year[0] = 2001, rank_year[1] = 1994, rank_year[2] = 70000; /* past 9999: undated */
    press_sort();
    assert(saved_sort() == 6);
    order_is((const char *const[]){ "New", "A", "Cover", "Folder", "Embedded", "None", "B", "C", "Tight", 0 });
    /* its groups are years, the query's year column; the albums it missed are undated, last */
    assert(coverflow_jump(slide(), 0, 1, &letter) == 1 && letter == (CF_YEAR | 1994));
    assert(coverflow_jump(slide(), 1, 1, &letter) == 1 && letter == CF_YEAR);
    assert(coverflow_jump(slide(), 2, -1, &letter) == 1 && letter == (CF_YEAR | 1994));
    assert(coverflow_jump(slide(), 2, 1, &letter) == albums - 2 && !letter);
    rank_year[0] = rank_year[1] = rank_year[2] = 0;
    assert(strstr(rank_sql, "else 0 end) desc"));
    /* Year order: Oldest first: the next open ranks again, ascending (the query's order still). */
    close_page();
    year_old = 1, q = queries;
    open_page();
    assert(queries == q + 1 && strstr(rank_sql, "else 0 end),album") && !strstr(rank_sql, "desc"));
    order_is((const char *const[]){ "New", "A", "Cover", 0 });
    q = queries;
    close_page();
    open_page();
    assert(queries == q); /* unchanged: kept */
    year_old = 0;
    /* An artist page's albums (coverflow_by_year), in place: the query's order, the rest after. */
    extern void coverflow_by_year(void *);
    void *mine = _create_deque("stSongInfo");
    deque_init(mine);
    for (int i = 5; i < 8; ++i) _deque_push_back(mine, records[i]); /* A B C */
    ranked[0] = "C", ranked[1] = "New", ranked[2] = "a", ranked[3] = 0;
    coverflow_by_year(mine);
    assert(deque_size(mine) == 3 && deque_at(mine, 0) == records[7] && deque_at(mine, 1) == records[5] &&
           deque_at(mine, 2) == records[6]);
    deque_destroy(mine);
    /* Recently Added: the query's order (a name in another case still matches), the rest after. */
    ranked[0] = "C", ranked[1] = "a", ranked[2] = "New", ranked[3] = "COVER", ranked[4] = "Gone", ranked[5] = 0;
    press_sort();
    order_is((const char *const[]){ "C", "A", "New", "Cover", "Folder", "Embedded", "None", "B", "Tight", 0 });
    assert(coverflow_jump(slide(), 0, 1, &letter) == 1 && !letter);
    /* Most Played: the album's listens, most first, ties and unplayed in stock order. */
    played[4] = 5, played[1] = 2, played[7] = 2;
    q = queries;
    press_sort();
    assert(saved_sort() == 3 && queries == q);
    order_is((const char *const[]){ "New", "Folder", "C", "Cover", "Embedded", "None", "A", "B", "Tight", 0 });
    assert(coverflow_jump(slide(), 0, 1, &letter) == 1 && !letter);
    /* The order outlives the page; Album again is the stock order. */
    close_page();
    open_page();
    order_is((const char *const[]){ "New", "Folder", "C", 0 });
    /* Random: the generator's order, nothing queried. */
    q = queries;
    press_sort();
    assert(saved_sort() == 4 && queries == q);
    order_is((const char *const[]){ "Tight", "C", "B", "A", "New", "None", "Embedded", "Folder", "Cover", 0 });
    assert(coverflow_jump(slide(), 0, 1, &letter) == 1 && !letter);
    press_sort();
    assert(saved_sort() == 0);
    order_is((const char *const[]){ "Cover", "Folder", "Embedded", "None", "New", "A", "B", "C", "Tight", 0 });
    close_page();
    assert(loads == unloads);
    *(const char **)(records[6] + REC_ARTIST) = "Artist";
    *(const char **)(records[8] + REC_ARTIST) = "Artist";
    memset(played, 0, sizeof played);
    rescan();
}

/* Recently Added with every album's newest song at one time_create (a card's albums copied in one go): SQLite
   hands the ties in the Album order, which showed as if the Sort had not applied. They go newest file first, by
   the art track's modification time (each album's row is its art track, album()'s file), then by its path; an
   album an hour newer still goes first, and a file that is gone counts as oldest. */
static int album_no(const char *name) {
    int i = 0;
    while (i < albums && strcmp(names[i], name)) ++i;
    assert(i < albums);
    return i;
}
static void added_ties(void) {
    static const char *const tied[] = { "A", "B", "C", "Cover", "Tight" };
    static const long mtimes[] = { 100, 200, 300, 0, 200 };
    char gone[620];
    for (int i = 0; i < 5; ++i) {
        const char *path = paths[album_no(tied[i])];
        struct timeval tv[2] = { { mtimes[i], 0 }, { mtimes[i], 0 } };
        if (mtimes[i]) assert(!utimes(path, tv));
        else snprintf(gone, sizeof gone, "%s.away", path), assert(!rename(path, gone));
    }
    /* the query's order: New an hour newer, then the tie as SQLite gives it, by album */
    ranked[0] = "New", ranked[1] = "A", ranked[2] = "B", ranked[3] = "C", ranked[4] = "Cover", ranked[5] = "Tight";
    ranked[6] = 0;
    rank_when[0] = "1700003600";
    for (int i = 1; i < 6; ++i) rank_when[i] = "1700000000";
    open_page();
    while (saved_sort() != 2) press_sort(); /* Recently Added */
    assert(strstr(rank_sql, "max(time_create) desc"));
    /* C newest (300), then B and Tight at 200 by path, A (100), Cover's file gone */
    order_is((const char *const[]){ "New", "C", "B", "Tight", "A", "Cover", 0 });
    while (saved_sort() != 0) press_sort();
    close_page();
    assert(!rename(gone, paths[album_no("Cover")]));
    ranked[0] = 0;
    memset(rank_when, 0, sizeof rank_when);
}

/* The Unknown Album card shows only when its press would find a song: getAllAlbum appends it even
   when every song carries an album tag. It stays last whatever the Sort. */
static void unknown_card(void) {
    unknown_row = 1;
    rescan();
    open_page();
    assert(slide_count(slide()) == albums && unknown_queries == 0);
    close_page();
    unknown_songs = 1;
    rescan();
    open_page();
    widget *s = slide();
    assert(slide_count(s) == albums + 1 && !strcmp(album_at(albums), "Unknown Album"));
    ranked[0] = "Unknown Album", ranked[1] = "None", ranked[2] = 0; /* Artist, then year */
    press_sort();
    assert(!strcmp(album_at(albums), "Unknown Album") && !strcmp(album_at(0), "None"));
    ranked[0] = 0;
    for (int i = 0; i < 4; ++i) press_sort(); /* to Random */
    assert(!strcmp(album_at(albums), "Unknown Album"));
    press_sort(); /* back to Album */
    close_page();
    assert(loads == unloads);
    unknown_row = unknown_songs = 0;
    rescan();
}

/* A cold open that takes a while (docs/internals.md#coverflow): once it has taken half of 150 ms, the press
   returns with the albums' query still running on its own thread, and Home stays live with the spinner on its row:
   the wheel's ticks go to Home, a timer collects the steps and the page opens after the last. Return's release
   gives the open up, and so does any other key or a touch, which then goes on to Home: the query's statement is
   stopped (its progress handler, coverflow_query_stop) and its thread joined before the input returns, and nothing
   is kept, so the next open queries again. A quick one opens as it is pressed, the spinner never shown. */
static void slow_open(void) {
    extern int coverflow_spinner(void), coverflow_wait_input(void *);
    int down[16] = {0}, back[16] = {0}, back_down[16] = {0}, touch[16] = {0}, tick[16] = {0};
    down[EVENT_TYPE / 4] = EVT_KEY_DOWN, down[EVENT_KEY / 4] = KEY_CENTER;
    back[EVENT_TYPE / 4] = EVT_KEY_UP, back[EVENT_KEY / 4] = KEY_RETURN;
    back_down[EVENT_TYPE / 4] = EVT_KEY_DOWN, back_down[EVENT_KEY / 4] = KEY_RETURN;
    touch[EVENT_TYPE / 4] = EVT_POINTER_DOWN;
    tick[EVENT_TYPE / 4] = EVT_KEY_DOWN, tick[EVENT_KEY / 4] = KEY_NEXT;
    widget home;
    memset(&home, 0, sizeof(home));
    coverflow_home(&home, 0);
    int q = all_queries;
    rescan();
    all_ms = 70; /* under half: the open waits for it, as pressed */
    open_page();
    assert(all_queries == q + 1 && slide() && !coverflow_spinner());
    close_page();
    /* a slow one: the press returns while its query runs */
    rescan();
    all_ms = 80, all_hold = 1, all_stopped = 0;
    page = &w[nw + 1];
    home.click(home.ctx, 0);
    assert(all_queries == q + 2 && all_running && timer_fn && !slide() && coverflow_spinner());
    assert(!coverflow_wait_input(tick)); /* the wheel moves Home */
    home.click(home.ctx, 0); /* a second press opens nothing more */
    assert(all_queries == q + 2 && all_running);
    all_hold = 0; /* the query ends: the steps go on, the page opens */
    run();
    assert(slide() && !coverflow_spinner() && !all_stopped && !coverflow_wait_input(down));
    assert(slide_count(slide()) == albums);
    close_page();
    /* Return gives it up: the statement stopped, the thread joined, before the release returns */
    rescan();
    all_hold = 1, all_stopped = 0;
    page = &w[nw + 1];
    home.click(home.ctx, 0);
    assert(all_running && timer_fn && coverflow_spinner());
    assert(coverflow_wait_input(back_down) && all_running); /* Return's press goes nowhere */
    assert(coverflow_wait_input(back)); /* its release gives it up */
    assert(!all_running && all_stopped && !timer_fn && !coverflow_spinner());
    run();
    assert(!slide());
    /* any other key, or a touch, gives it up as well, and goes on to Home */
    int *gone[] = { down, touch };
    for (unsigned k = 0; k < 2; ++k) {
        all_hold = 1, all_stopped = 0;
        page = &w[nw + 1];
        home.click(home.ctx, 0);
        assert(all_running && coverflow_spinner());
        assert(!coverflow_wait_input(gone[k]));
        assert(!all_running && all_stopped && !timer_fn && !coverflow_spinner());
        run();
        assert(!slide());
    }
    all_hold = 0, all_ms = 0;
    open_page(); /* the given-up queries are not kept: this one queries again, in no time: no spinner */
    assert(all_queries == q + 6 && slide() && slide_count(slide()) == albums && !coverflow_spinner());
    close_page();
}

/* A Sort that ranks (Recently Added's query; docs/internals.md#coverflow) runs on the open's thread too, as the
   albums' query does, with Home live and its spinner up: on a cold open after the albums' steps, and on an open that
   keeps the albums but has a new Sort to apply, which waits on its ranking as a cold open waits on its query. The
   page opens in the new order after it. */
static void slow_sort(void) {
    extern int coverflow_spinner(void);
    pthread_t ui = pthread_self();
    widget home;
    memset(&home, 0, sizeof(home));
    coverflow_home(&home, 0);
    ranked[0] = "C", ranked[1] = "a", ranked[2] = 0;
    rescan();
    coverflow_sort_set(3); /* Recently Added */
    all_ms = 0, rank_ms = 80, rank_hold = 1;
    int q = all_queries;
    page = &w[nw + 1];
    home.click(home.ctx, 0); /* cold: the albums' steps, then the ranking past half of 150 ms */
    assert(all_queries == q + 1 && !pthread_equal(rank_thread, ui));
    assert(timer_fn && coverflow_spinner() && !slide()); /* the press returned while it ranks */
    rank_hold = 0;
    run();
    assert(slide() && !coverflow_spinner() && saved_sort() == 2);
    order_is((const char *const[]){ "C", "A", 0 });
    close_page();
    coverflow_sort_set(1); /* Artist: the albums kept, only its ranking to wait on */
    ranked[0] = "B", ranked[1] = 0;
    rank_thread = ui, rank_hold = 1;
    page = &w[nw + 1];
    home.click(home.ctx, 0);
    assert(all_queries == q + 1 && !pthread_equal(rank_thread, ui) && timer_fn && coverflow_spinner());
    rank_hold = 0;
    run();
    assert(slide() && !coverflow_spinner() && saved_sort() == 1);
    close_page();
    coverflow_sort_set(0); /* Album: nothing to rank, the page at once */
    open_page();
    assert(slide() && !coverflow_spinner() && saved_sort() == 0);
    close_page();
    rank_ms = rank_hold = 0, ranked[0] = 0;
}

/* The captions' marquee starts over with each new album (docs/oled-design.md#motion): a long name
   scrolled part-way is stopped, so the next album's starts at its start after the hold, not at the
   last one's offset; a text named again (the same album, or the same artist) leaves its scroll alone. */
static void caption_marquee(void) {
    open_page();
    widget *s = slide(), *covers_view = &w[s->parent];
    int labels[2], k = 0;
    for (int i = 0; i < covers_view->nkids && k < 2; ++i)
        if (!strcmp(w[covers_view->kids[i]].type, "hscroll_label")) labels[k++] = covers_view->kids[i];
    assert(k == 2);
    int name = scroll_stops[labels[0]], artist = scroll_stops[labels[1]];
    *(int *)(s->raw + SLIDE_INDEX) = 2;
    changed_fn(0, 0);
    assert(!strcmp(album_caption(covers_view), names[2]));
    assert(scroll_stops[labels[0]] == name + 1);
    assert(scroll_stops[labels[1]] == artist); /* every album's artist is "Artist": left scrolling */
    changed_fn(0, 0); /* a commit on the same album: the same text, its scroll left alone */
    assert(scroll_stops[labels[0]] == name + 1);
    *(int *)(s->raw + SLIDE_INDEX) = 3;
    changed_fn(0, 0);
    assert(!strcmp(album_caption(covers_view), names[3]) && scroll_stops[labels[0]] == name + 2);
    *(const char **)(records[4] + REC_ARTIST) = "Another";
    *(int *)(s->raw + SLIDE_INDEX) = 4;
    changed_fn(0, 0);
    assert(scroll_stops[labels[0]] == name + 3 && scroll_stops[labels[1]] == artist + 1);
    *(const char **)(records[4] + REC_ARTIST) = "Artist";
    close_page();
}

/* Baroness' and Stone Temple Pilots' "Purple": getAllAlbum's one row for the name becomes two cards,
   each with its own art and only its own track. */
static void same_name(void) {
    extern void *coverflow_tracks(void *);
    static const char *const who[2] = { "Baroness", "Stone Temple Pilots" };
    album(who[0], "cover.jpg"), album(who[1], "cover.jpg"); /* their folders */
    for (int i = 0; i < 2; ++i) {
        snprintf(names[albums - 2 + i], 64, "Purple");
        *(const char **)(records[albums - 2 + i] + REC_ARTIST) = who[i];
        *(const char **)(records[albums - 2 + i] + REC_ALBUM_ARTIST) = who[i];
    }
    rescan();
    free_blocks = 1 << 20;
    open_page();
    widget *s = slide();
    assert(slide_count(s) == albums);
    for (int i = albums - 2; i < albums; ++i) {
        char art[600];
        snprintf(art, sizeof art, PEQ_ROOT "/mnt/mmc/.coverflow/%08x.jpg",
                 fnv(fnv(FNV_SEED, (const unsigned char *)who[i - albums + 2]), (const unsigned char *)"Purple"));
        assert(!access(art, 0));
        click_album(s, i);
        run();
        void *t = coverflow_tracks(page);
        assert(!strcmp(title(), "Purple") && deque_size(t) == 1 && deque_at(t, 0) == records[i]);
        key(KEY_RETURN);
    }
    close_page();
    albums -= 2;
    rescan();
}
/* Spoken-audio folders and M4B files never make covers, including artist and rated views. */
static int five_hearts(void *r) { return 5; }
static void audiobooks(void) {
    extern int coverflow_rated(int (*hearts)(void *));
    int music = albums;
    static const char *const books[] = {
        "/mnt/mmc/Audiobooks/Novel/01.mp3", "/mnt/mmc/aUdIoBoOkS/02.flac",
        "/mnt/mmc/Music/Novel.M4B", "/mnt/mmc/Podcasts/Show/01.mp3",
        "/mnt/mmc/pOdCaStS/02.mp3"
    };
    for (unsigned i = 0; i < sizeof books / sizeof *books; ++i) {
        album("Novel", 0);
        snprintf(paths[albums - 1], 600, "%s", books[i]);
    }
    /* Music, a book and a podcast share album and album-artist tags. */
    memmove(names[music], names[0], sizeof names[0]);
    *(const char **)(records[0] + REC_ALBUM_ARTIST) = "Shared";
    *(const char **)(records[music] + REC_ALBUM_ARTIST) = "Shared";
    memmove(names[music + 3], names[0], sizeof names[0]);
    *(const char **)(records[music + 3] + REC_ALBUM_ARTIST) = "Shared";
    extern void *coverflow_album_tracks(void *);
    deque *tracks = coverflow_album_tracks(records[0]);
    assert(tracks->n == 1 && tracks->at[0] == records[0]);
    free(tracks);

    rescan();
    open_page();
    assert(slide_count(slide()) == music);
    close_page();
    assert(coverflow_rated(five_hearts));
    run();
    assert(slide_count(slide()) == music + 1);
    close_page();
    *(const char **)(records[0] + REC_ALBUM_ARTIST) = 0;
    albums = music;
    /* An audiobook-only library renders the empty state. */
    char saved[64][600];
    for (int i = 0; i < albums; ++i) {
        strcpy(saved[i], paths[i]);
        snprintf(paths[i], 600, "/mnt/mmc/Audiobooks/%d.mp3", i);
    }
    rescan();
    open_page();
    assert(!slide());
    close_page();
    for (int i = 0; i < albums; ++i) strcpy(paths[i], saved[i]);
    rescan();
}

static void embedded_first(void) {
    /* Embedded art comes before a folder's cover.jpg, and a folder shared with another album gives
       its albums no cover.jpg: with embedded art failing, they keep the placeholder. */
    embedded_fails = 0;
    album("Both", "cover.jpg");
    album("Embedded2", 0);
    rescan();
    open_page();
    assert(size("Both") == 8 && size("Embedded2") == 8 && !tmp_files());
    close_page();
    album("Shared", "cover.jpg");
    album("Mate", 0);
    snprintf(paths[albums - 1], 600, PEQ_ROOT "/music/Shared/02.flac");
    fclose(fopen(paths[albums - 1], "w"));
    embedded_fails = 1;
    rescan();
    open_page();
    assert(size("Shared") == 0 && size("Mate") == 0 && !tmp_files());
    close_page();
}

/* Without album artists: an album's disc folders (CD1, Disc 2) are one card with both tracks,
   and a same-named album in another folder is its own. */
static void disc_folders(void) {
    extern void *coverflow_tracks(void *);
    static const char *const dirs[3] = { "Purple/CD1", "Purple/Disc 2", "Other Purple" }, *const who[3] = { "Baroness", "Baroness", "STP" };
    mkdir(PEQ_ROOT "/music/Purple", 0755);
    for (int i = 0; i < 3; ++i) {
        album(dirs[i], 0);
        snprintf(names[albums - 1], 64, "Purple");
        *(const char **)(records[albums - 1] + REC_ARTIST) = who[i];
    }
    rescan();
    open_page();
    widget *s = slide();
    assert(slide_count(s) == albums - 1);
    for (int i = 0; i < 2; ++i) {
        click_album(s, albums - 3 + i);
        run();
        void *t = coverflow_tracks(page);
        assert(deque_size(t) == (unsigned)(2 - i) && deque_at(t, 0) == records[albums - 3 + 2 * i]);
        if (!i) assert(deque_at(t, 1) == records[albums - 2]);
        key(KEY_RETURN);
    }
    close_page();
    albums -= 3;
    rescan();
}

static void ffmpeg_art(void) {
    /* What stock cannot decode goes to ffmpeg (FFMPEG_STUB): a folder's cover.webp, and the
       embedded picture of an album whose track only ffmpeg reads. */
    int before = calls;
    embedded_fails = 1;
    album("Webp", "cover.webp");
    album("Ffok", 0);
    rescan();
    open_page();
    assert(calls == before && size("Webp") == 6 && size("Ffok") == 6 && !tmp_files());
    close_page();
    /* An ffmpeg that never reached its own exit (killed: out of memory, #169) leaves no marker, so
       the album is tried again at the next open, not shown artless until Refresh Cover Flow. */
    album("Fkill", 0);
    rescan();
    for (int i = 0; i < 2; ++i) {
        open_page();
        assert(size("Fkill") == -1 && !tmp_files());
        close_page();
    }
}

static void player_cover(void) {
    /* Now Playing's folder art (coverflow_player_cover): stock's, else ffmpeg alone on a folder image
       (stock already refused it), then on the embedded picture. No stock thumbnailer: its parse lock
       would nest under the player's. */
    const char *dst = PEQ_ROOT "/externpic.jpg";
    char got[16];
    struct { const char *album, *want; int found; } cases[] = {
        { "Webp", "ffmpeg", 0 }, { "Ffok", "ffmpeg", 0 }, { "Cover", 0, 0 }, { "Cover", "extern", 1 } };
    for (unsigned i = 0; i < sizeof(cases) / sizeof(*cases); ++i) {
        char track[600];
        snprintf(track, sizeof track, PEQ_ROOT "/music/%s/01.flac", cases[i].album);
        unlink(dst);
        extern_found = cases[i].found;
        int before = calls, ok = coverflow_player_cover(track, dst, 320, 320);
        FILE *f = fopen(dst, "r");
        size_t n = f ? fread(got, 1, sizeof(got) - 1, f) : 0;
        if (f) fclose(f);
        got[n] = 0;
        assert(ok == !!cases[i].want && calls == before + cases[i].found);
        assert(cases[i].want ? !strcmp(got, cases[i].want) : !f);
    }
    extern_found = 0;
    /* Once the player has parsed the track, an album Coverflow marked empty skips ffmpeg; a
       missing marker, or a real cover, still goes to it. */
    char track[600], marker[600];
    snprintf(track, sizeof track, PEQ_ROOT "/music/Ffok/01.flac");
    snprintf(shim_id3, sizeof(shim_id3), "%s", track);
    snprintf(shim_id3 + ID3_ALBUM, 256, "%s", "Ffok");
    snprintf(shim_id3 + ID3_ARTIST, 256, "%s", "Artist");
    art_file(marker, "Ffok");
    for (int i = 0; i < 3; ++i) {
        FILE *f = i == 1 ? 0 : fopen(marker, "w");
        if (f) fprintf(f, "%s", i ? "ffmpeg" : ""), fclose(f);
        if (i == 1) unlink(marker);
        unlink(dst);
        int ok = coverflow_player_cover(track, dst, 320, 320);
        assert(ok == !!i && access(dst, 0) == (i ? 0 : -1));
    }
    /* The marker skips only the embedded probe: with it, Webp's cover.webp still shows (a shared
       folder, or a disc Coverflow did not look at), and a track the player has not parsed probes. */
    FILE *f = fopen(art_file(marker, "Webp"), "w");
    fclose(f);
    snprintf(track, sizeof track, PEQ_ROOT "/music/Webp/01.flac");
    snprintf(shim_id3, sizeof(shim_id3), "%s", track);
    snprintf(shim_id3 + ID3_ALBUM, 256, "%s", "Webp");
    unlink(dst);
    assert(coverflow_player_cover(track, dst, 320, 320) && !access(dst, 0));
    f = fopen(marker, "w"); /* Webp's art back, as ffmpeg_art left it */
    fputs("ffmpeg", f), fclose(f);
    f = fopen(art_file(marker, "Ffok"), "w");
    fclose(f);
    snprintf(track, sizeof track, PEQ_ROOT "/music/Ffok/01.flac");
    unlink(dst);
    assert(coverflow_player_cover(track, dst, 320, 320) && !access(dst, 0));
    fputs("ffmpeg", f = fopen(marker, "w")), fclose(f);
    memset(shim_id3, 0, sizeof(shim_id3));
}

/* ---- The thumb (the fixture's door shelf here): the follower's numbers, as tuned. ---- */
volatile int wheel_raw, wheel_travel, wheel_held;
static int test_wheel_pct = 50;
int wheel_pct(void) { return test_wheel_pct; }
int coverflow_wheel_live(void), coverflow_wheel_poll(int usable, int gated), coverflow_wheel_on(void *);
void coverflow_wheel_land(void), cf_thumb(float *out), cf_thumb_lead(float lead);
enum { T_POS, T_VEL, T_GOAL, T_LEAD, T_RATE, T_ON, T_JUMP, T_LEAN, T_FOLD, T_ENTER, T_FOLD_OFF, T_FOLD_VEL, T_ARCH, T_REBASED };
static float th[14];
static int th_pass(int travel, int ms) {
    clock_ms += ms;
    wheel_travel += travel;
    int r = coverflow_wheel_poll(1, 0);
    cf_thumb(th);
    return r;
}
/* The wheel turned at ticks a second going forward (x falls), 17 ms a pass. */
static void th_spin(float ticks, int ms) {
    static float carry;
    for (int t = 0; t < ms; t += 17) {
        carry += ticks * 200 / 36 * 17 / 1000;
        int u = (int)carry;
        carry -= u;
        th_pass(-u, 17);
    }
}
static void th_down(void) { wheel_held = 1; th_pass(0, 50); th_pass(0, 50); }
static void th_rest(void) {
    wheel_held = 0;
    for (int i = 0; i < 400 && (i < 8 || th[T_ON]); ++i) th_pass(0, 17);
    assert(!th[T_ON]);
}
/* The wheel's momentum on the covers is the lists' (wheel_accel.c, docs/internals.md#coverflow-thumb): the thumb's
   travel, Wheel Sensitivity-scaled into ticks (36 a turn at 50%), moves the goal one album a tick turned slowly or
   newly, more a tick as a sustained spin goes on (2 from 300 ms, one more each 200 ms, at most 8), back to one when a
   pause past 140 ms or a turn back starts a new run; never a letter, however long and fast the spin, and the covers
   follow through every album between, never past the goal. th_tick: one tick forward, gap ms after the last. */
static int tick_acc; /* the thumb's own count (coverflow_thumb.c th_turn): units x 36 x 50 a tick at 10000 */
static void th_tick(int gap) {
    int u = (10000 - tick_acc + 1799) / 1800; /* the units that complete the next tick */
    tick_acc += u * 1800 - 10000;
    for (int t = 17; t < gap; t += 17) th_pass(0, 17);
    th_pass(-u, gap % 17 ? gap % 17 : 17);
}
static void thumb_momentum(void) {
    alt = 1, frame_fail = 0, pressed = 0;
    open_page();
    widget *s = slide();
    int n = slide_count(s), *index = (int *)(s->raw + SLIDE_INDEX);
    top_window = page;
    th_pass(0, 17);
    th_down();
    th_pass(-1, 17), tick_acc = 0; /* the dead band's unit of shake taken up */
    /* slow: an album a tick, landing on each */
    float g = th[T_GOAL];
    for (int i = 1; i <= 3; ++i) {
        th_tick(250);
        assert(fabsf(th[T_GOAL] - (g + i)) < .001f);
    }
    /* a sustained fast spin, a tick each 30 ms for 2.4 s: 1 a tick for 300 ms, then more, up to 8, never a letter's
       jump; the covers never pass the goal and never go back */
    g = th[T_GOAL] + th[T_REBASED]; /* albums from where the thumb took the row, across rebases */
    int steps[80], k = 0, most = 0;
    float last_pos = th[T_POS] + th[T_REBASED];
    for (int t = 0; t < 2400; t += 30) {
        th_tick(30);
        int step = (int)lroundf(th[T_GOAL] + th[T_REBASED] - g);
        g = th[T_GOAL] + th[T_REBASED];
        steps[k++] = step;
        most = step > most ? step : most;
        assert(step >= 1 && step <= 8);
        assert(th[T_POS] + th[T_REBASED] >= last_pos - .001f && th[T_POS] <= th[T_GOAL] + .001f);
        last_pos = th[T_POS] + th[T_REBASED];
    }
    assert(steps[0] == 1 && steps[8] == 1 && most == 8 && steps[k - 1] == 8);
    for (int i = 1; i < k; ++i) assert(steps[i] >= steps[i - 1]); /* it only rises while the spin goes on */
    /* slowing within the run keeps its step; a pause past 140 ms starts over at one */
    th_tick(200);
    assert(fabsf(th[T_GOAL] + th[T_REBASED] - g - 1) < .001f);
    /* a crawl, a unit a pass with the row at rest between: its part-ticks add up, an album each whole tick */
    for (int i = 0; i < 80 && th[T_ON]; ++i) th_pass(0, 17); /* the row lands */
    int at = *index, whole = (tick_acc + 12 * 1800) / 10000;
    for (int i = 0; i < 12; ++i) {
        th_pass(-1, 300);
        for (int j = 0; j < 80 && th[T_ON]; ++j) th_pass(0, 17);
    }
    assert(!th[T_ON] && whole >= 2 && *index == (at + whole) % n);
    th_rest();
    assert(!th[T_ON] && *index >= 0 && *index < n);
    close_page();
}
/* One cover alone in the frame, a albums from frac 0's centre, at a lead. */
int cf_frame_window(int frac);
static void row_alone(unsigned *f, float lead, int a, const unsigned *art) {
    const unsigned *one[15] = { 0 };
    cf_thumb_lead(lead);
    int sh = cf_frame_window(0); /* the ring goes round the covers the row shows */
    assert(abs(a - sh) <= 7);
    one[a - sh + 7] = art;
    coverflow_render(f, PITCH, 0, one + 4);
}
static int col_top(const unsigned *f, int x) {
    for (int y = 0; y < CF_VIEW_H; ++y)
        if (slot_of(f[y * PITCH + x]) >= 0) return y;
    return -1;
}

/* ---- Going by letters the row is held as the entry left it, the fast row going on: in each built-in
   theme, spinning either way, its frame is the entry's last, cover for cover; every cover on the
   screen is as wide, as far from the next and as keystoned (far:near) as the formed row at speed's
   covers away from its facing point, all leaning against the spin, and stays so through an album's
   slide (none turns towards the eye as the albums pass the facing point off the leading edge); the
   covers reach both edges of the screen; half an album on from one cover is half an album back from
   the next, the same frame; leaning left is leaning right in a mirror. ---- */
void cf_thumb_lean(int lean);
float cf_thumb_hold(int lean, float lead, int frac);
int cf_frame_window(int frac);
int cf_enter_quads(int frac, float lead, float enter, int lean, int *centre, int (*out)[6]);
void cf_thumb_enter(int lean, float lead, float enter), cf_thumb_speed(float vel, float lead_v);
int cf_frame_quads(int frac, int (*out)[6]);
static int quad_cols(const int *q) { /* whether draw_quad draws any column of q: its middle in [xl, xr) */
    int n = 0;
    for (int x = 0; x < CF_VIEW_W && q[5] > 0; ++x) n += (2 * x + 1 - 2 * CF_CX) * 8 >= q[1] && (2 * x + 1 - 2 * CF_CX) * 8 < q[2];
    return n;
}
static void held_row(void) {
    static unsigned f[300 * PITCH], g[300 * PITCH], art[43][160 * 160];
    static const cf_style_t shipped[CF_THEMES] = CF_SHIPPED_ALL; /* every built-in theme's, in index order */
    const cf_style_t fixture = shim_themes[1].cf;
    #define ART(a) (abs(a) <= 21 ? art[(a) + 21] : 0) /* album a */
    for (int i = 0; i < 43; ++i)
        for (int k = 0; k < 160 * 160; ++k) art[i][k] = 0xff000000u | ((unsigned)(i * 160 * 160 + k) * 2654435761u >> 8 & 0xffffff);
    alt = 1;
    for (int ti = 0; ti < CF_THEMES; ++ti) {
        shim_themes[1].cf = shipped[ti];
        open_page();
        const cf_style_t *cs = &shim_themes[1].cf;
        int mirror[2][43][6] = { { { 0 } } };
        for (int dir = -1; dir <= 1; dir += 2) {
            int lean = -dir, frac = dir * 13107, q[15][6], lq[15][6], centre;
            float lead = dir * 3.2f; /* formed: the row at speed's covers away from it all alike */
            cf_thumb_speed(dir * .0139f, dir * .0094f);
            float x0 = cf_thumb_hold(lean, lead, frac);
            int r0 = (int)floorf(x0 + .5f), f0 = (int)((x0 - (float)r0) * ONE);
            /* the entry's last frame (its albums from frac's centre) is the held row's first */
            int n = cf_enter_quads(frac, lead, 0, lean, &centre, q), on = 0;
            cf_thumb_lean(lean);
            int ln = cf_frame_quads(f0, lq);
            for (int i = 0; i < n; ++i) {
                if (!quad_cols(q[i])) continue;
                int j = 0;
                while (j < ln && lq[j][0] + r0 != q[i][0]) ++j;
                assert(j < ln);
                for (int e = 1; e < 6; ++e) assert(abs(lq[j][e] - q[i][e]) <= 1);
                ++on;
            }
            assert(on >= 11);
            /* the formed row at speed: a cover row_flat and row_edge or more from its facing point */
            float turned = (cs->row_flat > cs->row_edge ? cs->row_flat : cs->row_edge) / 100.f;
            int rq[15][6], rn, ref = -1, next = -1;
            cf_thumb_enter(0, lead, 0);
            rn = cf_frame_quads(frac, rq);
            for (int i = 0; i < rn; ++i) {
                float t = ((float)rq[i][0] - ((float)frac / ONE + lead)) * dir;
                if (t <= -turned - 1 && quad_cols(rq[i])) {
                    for (int j = 0; j < rn; ++j) next = rq[j][0] == rq[i][0] - dir ? j : next;
                    ref = i;
                    break;
                }
            }
            assert(ref >= 0 && next >= 0);
            int width = rq[ref][2] - rq[ref][1], gap = (rq[ref][1] - rq[next][1]) * dir;
            float key = (float)(lean > 0 ? rq[ref][3] : rq[ref][4]) / (float)(lean > 0 ? rq[ref][4] : rq[ref][3]);
            assert(key < .99f && gap > 0);
            /* through an album's slide, every cover on the screen so, leaning against the spin */
            cf_thumb_lean(lean);
            for (int ph = -ONE / 2; ph < ONE / 2; ph += ONE / 16) {
                int fq[15][6], fn = cf_frame_quads(ph, fq), lo = 1 << 30, hi = -(1 << 30), on = 0;
                for (int i = 0; i < fn; ++i) {
                    if (!quad_cols(fq[i])) continue;
                    ++on, lo = fq[i][1] < lo ? fq[i][1] : lo, hi = fq[i][2] > hi ? fq[i][2] : hi;
                    float k = (float)(lean > 0 ? fq[i][3] : fq[i][4]) / (float)(lean > 0 ? fq[i][4] : fq[i][3]);
                    if (abs(fq[i][2] - fq[i][1] - width) > 2 || fabsf(k - key) > .005f || (fq[i][4] - fq[i][3]) * lean <= 0) {
                        fprintf(stderr, "held_row: theme %d dir %d at %d: album %d %d wide, far:near %.3f (the row's %d, %.3f)\n",
                                ti, dir, ph, fq[i][0], fq[i][2] - fq[i][1], k, width, key);
                        assert(0);
                    }
                    for (int j = 0; j < fn; ++j) /* the next one the way the covers came from */
                        if (fq[j][0] == fq[i][0] - dir && quad_cols(fq[j])) assert(abs((fq[i][1] - fq[j][1]) * dir - gap) <= 2);
                }
                /* the screen, edge to edge: a cover within a spacing of each */
                assert(on >= 11 && lo < (1 - 2 * CF_CX) * 8 + gap && hi > (2 * CF_VIEW_W - 1 - 2 * CF_CX) * 8 - gap);
            }
            /* half an album on from one cover is half an album back from the next */
            for (int o = -3; o <= 3; ++o) {
                const unsigned *one[15] = { 0 }, *nx[15] = { 0 };
                int a = cf_frame_window(ONE / 2), b = cf_frame_window(-ONE / 2);
                if (abs(o - a) > 7 || abs(o - 1 - b) > 7) continue;
                one[o - a + 7] = nx[o - 1 - b + 7] = art[o + 21];
                coverflow_render(f, PITCH, ONE / 2, one + 4);
                coverflow_render(g, PITCH, -ONE / 2, nx + 4);
                assert(!memcmp(f, g, sizeof f));
            }
            /* for the mirror: each cover at frac 0 */
            int mq[15][6], mn = cf_frame_quads(0, mq);
            for (int i = 0; i < mn; ++i)
                if (abs(mq[i][0]) <= 21) memcpy(mirror[dir > 0][mq[i][0] + 21], mq[i], sizeof mq[i]);
        }
        /* Leaning left is leaning right in a mirror: cover -a is cover a's, mirrored. */
        for (int a = -21; a <= 21; ++a) {
            const int *l = mirror[0][a + 21], *r = mirror[1][21 - a];
            if (!l[5] || !r[5] || !quad_cols(l)) continue;
            assert(abs(l[1] + r[2]) <= 1 && abs(l[2] + r[1]) <= 1 && abs(l[3] - r[4]) <= 1 && abs(l[4] - r[3]) <= 1 && l[5] == r[5]);
        }
        /* The page's frame follows the mode, the position unchanged: letters, then the shelf again. */
        static unsigned before[CF_VIEW_H * CF_VIEW_W];
        int canvas[16] = { 0 };
        frame_fail = 0, top_window = page;
        cf_thumb_lean(0);
        coverflow_paint(slide(), canvas);
        memcpy(before, shown, sizeof shown);
        cf_thumb_lean(1);
        coverflow_paint(slide(), canvas);
        assert(memcmp(before, shown, sizeof shown));
        cf_thumb_lean(0);
        coverflow_paint(slide(), canvas);
        assert(!memcmp(before, shown, sizeof shown));
        close_page();
        top_window = 0;
    }
    #undef ART
    shim_themes[1].cf = fixture;
    alt = 0;
}

/* ---- The lift folds the held row back out onto the shelf: every cover glides from the pose that
   draws it in the held row to its rest pose, and the side that must turn its lean over swings out on
   an arc and turns while out. Seen from above (each cover a segment 160 px wide), no two covers ever
   cross or come within 4% of a cover's width, in each built-in theme, leaning either way. ---- */
int cf_fold_pose(int o, int t16, int lean, int *xc, int *zc, int *c, int *s, int *eye);
int cf_fold_order(int t16, int lean, signed char *order, int (*out)[6]);
static void hold_formed(int lean) { /* the row held as an entry from the formed row at speed leaves it */
    cf_thumb_speed(-lean * .0139f, -lean * .0094f);
    cf_thumb_hold(lean, -lean * 3.2f, 0);
}
void cf_thumb_fold(int lean, unsigned at);
int cf_path_x(int t, int u);
static int orient(const double *p, const double *q, const double *r) {
    double v = (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0]);
    return (v > 0) - (v < 0);
}
static int crossing(const double *a, const double *b) { /* touching counts */
    int d1 = orient(b, b + 2, a), d2 = orient(b, b + 2, a + 2), d3 = orient(a, a + 2, b), d4 = orient(a, a + 2, b + 2);
    return d1 * d2 <= 0 && d3 * d4 <= 0 &&
           !(!d1 && !d2 && !d3 && !d4 && (fmax(a[0], a[2]) < fmin(b[0], b[2]) || fmax(b[0], b[2]) < fmin(a[0], a[2])));
}
static double to_seg(const double *p, const double *s) {
    double dx = s[2] - s[0], dy = s[3] - s[1], l = dx * dx + dy * dy, u = l ? ((p[0] - s[0]) * dx + (p[1] - s[1]) * dy) / l : 0;
    u = u < 0 ? 0 : u > 1 ? 1 : u;
    return hypot(p[0] - s[0] - u * dx, p[1] - s[1] - u * dy);
}
static double seg_gap(const double *a, const double *b) {
    if (crossing(a, b)) return 0;
    return fmin(fmin(to_seg(a, b), to_seg(a + 2, b)), fmin(to_seg(b, a), to_seg(b + 2, a)));
}
static int fold_x(int xc, int zc, int c, int s, int eye, int u) { /* project()'s own sums */
    return (xc + (u * c >> 16)) * eye / (eye + zc - (u * s >> 16));
}
static void fold_clearance(void) {
    static const cf_style_t shipped[CF_THEMES] = CF_SHIPPED_ALL; /* every built-in theme's, in index order */
    const cf_style_t fixture = shim_themes[1].cf;
    alt = 1;
    for (int ti = 0; ti < CF_THEMES; ++ti) {
        shim_themes[1].cf = shipped[ti];
        const cf_style_t *cs = &shim_themes[1].cf;
        for (int lean = -1; lean <= 1; lean += 2) {
            hold_formed(lean);
            for (int k = 0; k <= 100; ++k) {
                int t16 = k * ONE / 100, n = 0, at[15];
                double seg[15][4];
                for (int o = -7; o <= 7; ++o) {
                    int xc, zc, c, s, eye;
                    if (cf_fold_pose(o, t16, lean, &xc, &zc, &c, &s, &eye) <= 0) continue;
                    double hc = 1280. * c / ONE, hs = 1280. * s / ONE;
                    seg[n][0] = xc - hc, seg[n][1] = zc + hs, seg[n][2] = xc + hc, seg[n][3] = zc - hs;
                    at[n++] = o;
                }
                for (int i = 0; i < n; ++i)
                    for (int j = i + 1; j < n; ++j) {
                        double gap = seg_gap(seg[i], seg[j]);
                        if (gap < 2 * 1280 * 4 / 100.) {
                            fprintf(stderr, "fold_clearance: theme %d lean %d at %d/100: covers %d and %d %.2f px apart\n",
                                    ti, lean, k, at[i], at[j], gap / 16);
                            assert(0);
                        }
                    }
            }
            /* The turning side turns only between FOLD_FROM and FOLD_TO % of the fold. */
            for (int o = -7; o <= 7; ++o) {
                if (o * lean >= 0) continue;
                int xc, zc, c, s, eye, c0, s0, c1, s1;
                cf_fold_pose(o, 0, lean, &xc, &zc, &c0, &s0, &eye);
                cf_fold_pose(o, ONE, lean, &xc, &zc, &c1, &s1, &eye);
                cf_fold_pose(o, FOLD_FROM * ONE / 100, lean, &xc, &zc, &c, &s, &eye);
                assert(c == c0 && s == s0);
                cf_fold_pose(o, (FOLD_TO * ONE + 99) / 100, lean, &xc, &zc, &c, &s, &eye);
                assert(c == c1 && s == s1);
            }
            /* It starts as the held row, each cover the pose that draws its quad there, to a 1/16 px
               (heights to 1/128), and ends as the shelf at rest. The top view above takes every cover
               as seen from one eye; the held row's covers on the screen are seen from eyes within 20%
               of each other (9% at the shipped numbers, 18% at the door's: each cover's own turn sets its
               eye), and fold_frames checks the screen itself, every column's cover in front the
               tallest there. */
            int lq[15][6], ln, eye_lo = 1 << 30, eye_hi = 0;
            cf_thumb_lean(lean);
            ln = cf_frame_quads(0, lq);
            cf_thumb_lean(0);
            for (int o = -7; o <= 7; ++o) {
                int xc, zc, c, s, eye, n = abs(o), side = o < 0 ? -1 : 1, j = 0;
                int bright = cf_fold_pose(o, 0, lean, &xc, &zc, &c, &s, &eye);
                while (j < ln && lq[j][0] != o) ++j;
                if (j < ln && quad_cols(lq[j])) {
                    eye_lo = eye < eye_lo ? eye : eye_lo, eye_hi = eye > eye_hi ? eye : eye_hi;
                    double e = eye, dl = e + zc + 1280. * s / ONE, dr = e + zc - 1280. * s / ONE;
                    double xl = (xc - 1280. * c / ONE) * e / dl, xr = (xc + 1280. * c / ONE) * e / dr;
                    if (!(fabs(xl - lq[j][1]) <= 1.5 && fabs(xr - lq[j][2]) <= 1.5 && fabs(40960. * e / dl - lq[j][3]) <= 2 &&
                          fabs(40960. * e / dr - lq[j][4]) <= 2 && bright == lq[j][5])) {
                        fprintf(stderr, "fold_clearance: theme %d lean %d cover %d: %.1f %.1f %.1f %.1f, the held row's %d %d %d %d\n",
                                ti, lean, o, xl, xr, 40960. * e / dl, 40960. * e / dr, lq[j][1], lq[j][2], lq[j][3], lq[j][4]);
                        assert(0);
                    }
                }
                if (o == 7) assert(eye_hi > 0 && eye_hi * 100 <= eye_lo * 120);
                cf_fold_pose(o, ONE, lean, &xc, &zc, &c, &s, &eye);
                if (n) assert(xc == side * 16 * (cs->x1 + cs->xs * (n - 1)) && zc == 16 * (cs->z1 + cs->zs * (n - 1)));
                for (int u = -1280; u <= 1280; u += 1280) /* the pose seen as pose()'s, mirrored on the left */
                    assert(fold_x(side * xc, zc, c, side * s, eye, u) == cf_path_x(n * ONE, u));
            }
        }
    }
    shim_themes[1].cf = fixture;
    alt = 0;
}

/* Its frames: per column the nearest cover, in fold_order's order, which is the nearest there; it
   starts on the held row's frame (each cover's quad to a 1/16 px, and the same pixels drawn but for
   which texel a column takes) and ends on the shelf's at rest; an empty side (the landing album
   first or last of an artist's) draws nothing; and the page draws every frame of it anew. */
void cf_shelf_still(int still);
static void fold_frames(void) {
    static unsigned f[300 * PITCH], g[300 * PITCH], one_f[300 * PITCH], art[15][160 * 160], line[15][CF_VIEW_W];
    static const cf_style_t shipped[CF_THEMES] = CF_SHIPPED_ALL; /* every built-in theme's, in index order */
    const cf_style_t fixture = shim_themes[1].cf;
    const unsigned *ring[15];
    for (int i = 0; i < 15; ++i) {
        ring[i] = art[i];
        for (int k = 0; k < 160 * 160; ++k) art[i][k] = 0xff000000u | ((unsigned)(i * 160 * 160 + k) * 2654435761u >> 8 & 0xffffff);
    }
    alt = 1;
    for (int ti = 0; ti < CF_THEMES; ++ti) {
        shim_themes[1].cf = shipped[ti];
        open_page();
        const cf_style_t *cs = &shim_themes[1].cf;
        int H = cs->view_h, hz = cs->top + 160 * cs->horizon / 100;
        for (int lean = -1; lean <= 1; lean += 2) {
            hold_formed(lean);
            for (int empty = 0; empty <= 2; ++empty) /* none, the left side, the right side */
                for (int k = 0, steps = empty ? 8 : 50; k <= steps; ++k) { /* whole */
                    int ms = k * CF_FOLD_MS / steps, t16 = ms * ONE / CF_FOLD_MS;
                    const unsigned *have[15];
                    for (int o = -7; o <= 7; ++o) have[o + 7] = (empty == 1 && o < 0) || (empty == 2 && o > 0) ? 0 : ring[o + 7];
                    cf_thumb_fold(lean, clock_ms - ms);
                    signed char order[15];
                    int n = cf_fold_order(t16, lean, order, 0);
                    for (int o = -7; o <= 7; ++o) {
                        const unsigned *one[15] = { 0 };
                        one[o + 7] = have[o + 7];
                        coverflow_render(one_f, PITCH, 0, one + 4);
                        for (int x = 0; x < CF_VIEW_W; ++x) line[o + 7][x] = one_f[hz * PITCH + x];
                    }
                    coverflow_render(f, PITCH, 0, have + 4);
                    for (int x = 0; x < CF_VIEW_W; ++x) {
                        int want = -99;
                        for (int i = 0; i < n && want == -99; ++i)
                            if (line[order[i] + 7][x] != 0xff000000u) want = order[i];
                        assert(f[hz * PITCH + x] == (want == -99 ? 0xff000000u : line[want + 7][x]));
                        if (want == -99) continue;
                        /* no cover showing at x is nearer there than the one in front: taller,
                           each seen from its own eye */
                        double dx = (2 * x + 1 - 2 * CF_CX) * 8, tallest = 0, front = 0;
                        for (int o = -7; o <= 7; ++o) {
                            if (line[o + 7][x] == 0xff000000u) continue;
                            int xc, zc, c, s, eye;
                            cf_fold_pose(o, t16, lean, &xc, &zc, &c, &s, &eye);
                            double cc = (double)c / ONE, ss = (double)s / ONE;
                            double u = (dx * (eye + zc) - (double)xc * eye) / (cc * eye + dx * ss), h = 160. * eye / (eye + zc - u * ss);
                            tallest = h > tallest ? h : tallest;
                            front = o == want ? h : front;
                        }
                        assert(front >= tallest - 1);
                    }
                }
            /* the held row's frame at the start, the shelf's at rest at the end */
            signed char order[15];
            int fq[15][6], lq[15][6], fn = cf_fold_order(0, lean, order, fq), ln, sh, off = 0;
            const unsigned *lring[15] = { 0 };
            cf_thumb_fold(lean, clock_ms);
            coverflow_render(f, PITCH, 0, ring + 4);
            cf_thumb_lean(lean);
            ln = cf_frame_quads(0, lq), sh = cf_frame_window(0);
            for (int j = 0; j < 15; ++j) lring[j] = j + sh >= 0 && j + sh < 15 ? ring[j + sh] : 0;
            coverflow_render(g, PITCH, 0, lring + 4);
            for (int i = 0; i < ln; ++i) {
                int j = 0;
                if (!quad_cols(lq[i])) continue;
                while (j < fn && fq[j][0] != lq[i][0]) ++j;
                assert(j < fn);
                for (int e = 1; e < 6; ++e) assert(abs(fq[j][e] - lq[i][e]) <= 2);
            }
            for (int y = 0; y < H; ++y)
                for (int x = 0; x < CF_VIEW_W; ++x) off += (f[y * PITCH + x] == 0xff000000u) != (g[y * PITCH + x] == 0xff000000u);
            assert(off < H);
            /* the fold's last frame hands over to the still shelf it lands on, with no pop: the
               covers past the third out are gone by then */
            cf_thumb_fold(lean, clock_ms - CF_FOLD_MS);
            coverflow_render(f, PITCH, 0, ring + 4);
            cf_thumb_fold(0, 0);
            cf_shelf_still(1);
            coverflow_render(g, PITCH, 0, ring + 4);
            cf_shelf_still(0);
            int jump = 0;
            for (int i = 0; i < H * PITCH; ++i)
                for (int b = 0; b < 24; b += 8) jump = abs(ch(f[i], b / 8) - ch(g[i], b / 8)) > jump ? abs(ch(f[i], b / 8) - ch(g[i], b / 8)) : jump;
            if (jump > 2) fprintf(stderr, "fold_frames: theme %d lean %d: the landing jumps %d levels\n", ti, lean, jump);
            assert(jump <= 2);
        }
        /* The page: a held row leaning the other way is another frame, and the fold's frames move
           with the clock, the position unchanged. */
        static unsigned before[CF_VIEW_H * CF_VIEW_W];
        int canvas[16] = { 0 };
        frame_fail = 0, top_window = page;
        hold_formed(1), cf_thumb_lean(1);
        for (int i = 0; i < 30; ++i) coverflow_paint(slide(), canvas); /* every texture loaded */
        memcpy(before, shown, sizeof shown);
        hold_formed(-1), cf_thumb_lean(-1);
        coverflow_paint(slide(), canvas);
        assert(memcmp(before, shown, sizeof shown));
        cf_thumb_fold(1, clock_ms);
        coverflow_paint(slide(), canvas);
        memcpy(before, shown, sizeof shown);
        clock_ms += CF_FOLD_MS / 2;
        coverflow_paint(slide(), canvas);
        assert(memcmp(before, shown, sizeof shown));
        cf_thumb_fold(0, 0);
        close_page();
        top_window = 0;
    }
    shim_themes[1].cf = fixture;
    alt = 0;
}

/* ---- Into letters the facing point overtakes: from the row at speed as it was, the row goes on and
   the facing point runs on ahead of it, the way the spin goes, through the covers ahead, each turning
   as it is passed, till it is so far off the screen's leading edge that no cover on the screen is
   within row_flat or row_edge of it; the covers it has passed lean the way the covers behind a
   facing cover do. Every
   frame of it, in each built-in theme, spinning either way: per column the cover in front is the one
   painted first there and the tallest there (no two pass through each other); every cover behind the
   facing point is turned the lean's way and none ahead of it is; the facing point's x on the screen
   only goes on towards the leading edge, and off it; a cover leaves or joins the window only off the
   screen; it starts on the row's frame and ends on the held row's, every cover leaning, against the
   spin. The facing point sets off at its own speed in the row, and every cover within row_flat and
   row_edge of it is off the screen as it stops running. ---- */
void cf_enter_quad(int frac, float lead, float enter, int lean, int a, int *out);
float cf_enter_face(int frac, float lead, float enter, int lean, int *x);
int coverflow_letter_next(void *w, int from, int dir, int n, unsigned *letter);
static int turned_on(int frac, float lead, float enter, int lean, float at) { /* a cover within the row's turn on the screen */
    const cf_style_t *cs = &shim_themes[1].cf;
    float turned = (cs->row_flat > cs->row_edge ? cs->row_flat : cs->row_edge) / 100.f;
    int out[6], on = 0;
    for (int a = (int)floorf(at - turned); a <= at + turned; ++a)
        if (fabsf(a - at) < turned) cf_enter_quad(frac, lead, enter, lean, a, out), on |= quad_cols(out) > 0;
    return on;
}
static void enter_frames(void) {
    static unsigned f[300 * PITCH], g[300 * PITCH], one_f[300 * PITCH], last_f[300 * PITCH], art[43][160 * 160], line[15][CF_VIEW_W];
    static const cf_style_t shipped[CF_THEMES] = CF_SHIPPED_ALL; /* every built-in theme's, in index order */
    const cf_style_t fixture = shim_themes[1].cf;
    #define ART(a) (abs(a) <= 21 ? art[(a) + 21] : 0) /* album a from frac's centre */
    for (int i = 0; i < 43; ++i)
        for (int k = 0; k < 160 * 160; ++k) art[i][k] = 0xff000000u | ((unsigned)(i * 160 * 160 + k) * 2654435761u >> 8 & 0xffffff);
    alt = 1;
    for (int ti = 0; ti < CF_THEMES; ++ti) {
        shim_themes[1].cf = shipped[ti];
        open_page(); /* cf_restyle's */
        const cf_style_t *cs = &shim_themes[1].cf;
        int H = cs->view_h, hz = cs->top + 160 * cs->horizon / 100;
        for (int dir = -1; dir <= 1; dir += 2)
            for (int start = 0; start < 2; ++start) { /* at the cap's lead, and a short one */
                int lean = -dir; /* the covers behind the facing cover's turn, against the spin */
                float lead = dir * (start ? .7f : 2.6f);
                float travel = cs->enter_albums;
                int frac = dir * (start ? -19661 : 13107), steps = 100; /* the run ends at a step: k 99 */
                int last_n = 0, last[15][6], face_x = 0;
                float prev_enter = 0, face = 0, ahead = 0, first = 0;
                int last_centre = 0;
                /* the row's speed and the facing point's own as letters began, albums a ms */
                float vel = dir * (start ? .012f : .0139f), lead_v = dir * (start ? .003f : .0094f);
                float v = fabsf(vel) > CF_ENTER_SPEED / 1000.f ? fabsf(vel) : CF_ENTER_SPEED / 1000.f;
                int black0 = 0, black1 = 0, worst = 0, worst_k = 0;
                cf_thumb_speed(vel, lead_v);
                for (int k = 0; k <= steps; ++k) {
                    float enter = k < steps ? travel * (steps - k) / steps : 1e-5f;
                    int q[15][6], centre, n = cf_enter_quads(frac, lead, enter, lean, &centre, q), x;
                    float at = cf_enter_face(frac, lead, enter, lean, &x);
                    const unsigned *have[15];
                    for (int j = -7; j <= 7; ++j) have[j + 7] = ART(centre + j);
                    cf_thumb_enter(lean, lead, enter);
                    for (int i = 0; i < n; ++i) assert(q[i][0] >= centre - 7 && q[i][0] <= centre + 7);
                    /* the facing point runs on, the way the spin goes, on the screen and through the row */
                    if (!k)
                        assert(fabsf(at - ((float)frac / ONE + lead)) < 1e-4f);
                    else if (!((at - face) * dir >= 0 && (x - face_x) * dir >= 0)) {
                        fprintf(stderr, "enter_frames: theme %d dir %d start %d at %d/%d: the facing point at %.3f, x %d, from %.3f, x %d\n", ti, dir, start, k, steps, at, x, face, face_x);
                        assert(0);
                    }
                    /* faster and faster while it runs (ENTER_RUN % of the entry), then with the row */
                    /* (the share gone is to 1/65536, of enter_albums: the row's step jitters by that) */
                    if (k >= 2 && k * 100 <= steps * ENTER_RUN) assert((at - face) * dir >= ahead - 2.f * cs->enter_albums / ONE);
                    if (k == 1) first = (at - face) * dir;
                    /* from its speed in the row as letters began, the row's and the lead's own */
                    if (k == 1) assert(first >= (fabsf(vel) + fabsf(lead_v)) * travel / steps / v * .99f);
                    /* the cube on top of its own speed: at the run's end over 1.5x its first step
                       (the lead's own speed covers most of a run as long as the entry, so it is as
                       little as 1.9x, the door's; a run with no cube left would end at 1x, and one
                       ending slower than it came stalls the facing cover going off) */
                    if (k * 100 == steps * ENTER_RUN) assert((at - face) * dir > 1.5f * first);
                    /* as it stops running, every cover within the row's turn off the screen, the
                       facing cover too, from the row at any phase, and for an entry of 3 albums too
                       (where it stops between albums otherwise) */
                    for (int ph = -ONE / 2, e = cs->enter_albums; k * 100 == steps * ENTER_RUN && ph < ONE / 2; ph += ONE / 16)
                        for (int albums3 = 0; albums3 < 2; ++albums3) {
                            int out[6], xx;
                            float stop = (albums3 ? 3 : e) * (100 - ENTER_RUN) / 100.f, fa;
                            shim_themes[1].cf.enter_albums = albums3 ? 3 : e;
                            fa = cf_enter_face(ph, lead, stop, lean, &xx);
                            cf_enter_quad(ph, lead, stop, lean, (int)floorf(fa + .5f), out);
                            int on = turned_on(ph, lead, stop, lean, fa);
                            shim_themes[1].cf.enter_albums = e;
                            cf_enter_face(ph, lead, stop, lean, &xx); /* cf_restyle's, the theme's again */
                            if (quad_cols(out) || on) {
                                fprintf(stderr, "enter_frames: theme %d dir %d start %d frac %d (%d albums): the facing cover at %d..%d as the run ends\n",
                                        ti, dir, start, ph, albums3 ? 3 : e, out[1], out[2]);
                                assert(0);
                            }
                        }
                    ahead = (at - face) * dir, face = at, face_x = x;
                    /* per column, every fourth step: the first painted, and the tallest there */
                    for (int i = 0; !(k & 3) && i < n; ++i) {
                        const unsigned *one[15] = { 0 };
                        one[q[i][0] - centre + 7] = have[q[i][0] - centre + 7];
                        coverflow_render(one_f, PITCH, frac, one + 4);
                        for (int x = 0; x < CF_VIEW_W; ++x) line[i][x] = one_f[hz * PITCH + x];
                    }
                    coverflow_render(f, PITCH, frac, have + 4);
                    int black = 0;
                    for (int x = 0; x < CF_VIEW_W; ++x) black += f[hz * PITCH + x] == 0xff000000u;
                    worst = black > worst ? black : worst, worst_k = black == worst ? k : worst_k;
                    if (!k) black0 = black;
                    for (int x = 0; !(k & 3) && x < CF_VIEW_W; ++x) {
                        int want = -1, mx = (2 * x + 1 - 2 * CF_CX) * 8; /* draw_quad's column middle */
                        #define DRAWS(i) (q[i][5] > 0 && mx >= q[i][1] && mx < q[i][2])
                        for (int i = 0; i < n && want < 0; ++i)
                            if (DRAWS(i)) want = i;
                        assert(f[hz * PITCH + x] == (want < 0 ? 0xff000000u : line[want][x]));
                        if (want < 0) continue;
                        double dx = mx, tallest = 0, front = 0;
                        for (int i = 0; i < n; ++i) {
                            if (!DRAWS(i)) continue;
                            double h = q[i][3] + (double)(q[i][4] - q[i][3]) * (dx - q[i][1]) / (q[i][2] - q[i][1]);
                            tallest = h > tallest ? h : tallest;
                            front = i == want ? h : front;
                        }
                        if (front < tallest - 256) {
                            fprintf(stderr, "enter_frames: theme %d dir %d start %d at %d/%d: column %d shows %d, %.1f px under %.1f\n",
                                    ti, dir, start, k, steps, x, q[want][0], front / 256, tallest / 256);
                            assert(0);
                        }
                        #undef DRAWS
                    }
                    /* behind the facing point, turned the lean's way (the near edge, the taller, on
                       the lean's side); ahead of it, not */
                    for (int i = 0; i < n; ++i) {
                        int a = q[i][0], d = q[i][4] - q[i][3];
                        float u = ((float)a - at) * dir;
                        if (!quad_cols(q[i]) || (u > -1e-3f && u < 1e-3f)) continue;
                        if (u < 0 ? d * lean < 0 : d * lean > 0) {
                            fprintf(stderr, "enter_frames: theme %d dir %d start %d at %d/%d: album %d, %.2f from the facing point, edges %d and %d\n",
                                    ti, dir, start, k, steps, a, u, q[i][3], q[i][4]);
                            assert(0);
                        }
                    }
                    /* the window drops a cover only once it shows nothing, and gains one only while
                       it still does: off the screen, or behind nearer covers in the frame with it */
                    for (int pass = 0; k && pass < 2; ++pass) {
                        int (*from)[6] = pass ? q : last, (*to)[6] = pass ? last : q, nf = pass ? n : last_n, nt = pass ? last_n : n;
                        int c0 = pass ? centre : last_centre;
                        for (int i = 0; i < nf; ++i) {
                            int kept = 0;
                            for (int j = 0; j < nt; ++j) kept |= to[j][0] == from[i][0];
                            if (kept) continue;
                            int out[6];
                            cf_enter_quad(frac, lead, pass ? prev_enter : enter, lean, from[i][0], out);
                            if (!quad_cols(out)) continue; /* off the screen in the frame without it */
                            const unsigned *less[15]; /* or adding nothing to the frame with it */
                            for (int j = -7; j <= 7; ++j) less[j + 7] = c0 + j == from[i][0] ? 0 : ART(c0 + j);
                            cf_thumb_enter(lean, lead, pass ? enter : prev_enter);
                            coverflow_render(g, PITCH, frac, less + 4);
                            if (memcmp(pass ? f : last_f, g, H * PITCH * sizeof *f)) {
                                fprintf(stderr, "enter_frames: theme %d dir %d start %d at %d/%d: album %d %s the window showing\n",
                                        ti, dir, start, k, steps, from[i][0], pass ? "joins" : "leaves");
                                assert(0);
                            }
                        }
                    }
                    cf_thumb_enter(lean, lead, enter);
                    memcpy(last, q, sizeof q), last_n = n, last_centre = centre, prev_enter = enter;
                    memcpy(last_f, f, sizeof last_f);
                    if (k == 0) { /* the row at speed's frame, as it was */
                        const unsigned *row[15];
                        cf_thumb_enter(0, lead, 0);
                        for (int j = -7, sh = cf_frame_window(frac); j <= 7; ++j) row[j + 7] = ART(sh + j);
                        coverflow_render(g, PITCH, frac, row + 4);
                        assert(!memcmp(f, g, H * PITCH * sizeof *f));
                        /* and its black as the facing point goes through an album, as the overtake's
                           does, and as the row formed does (the run forms it) */
                        for (int formed = 0; formed < 2; ++formed)
                            for (int ph = -ONE / 2; ph < ONE / 2; ph += ONE / 16) {
                                int b = 0;
                                float l = formed ? dir * 3.2f : lead;
                                cf_thumb_enter(0, l, 0);
                                for (int j = -7, sh = cf_frame_window(ph); j <= 7; ++j) row[j + 7] = ART(sh + j);
                                coverflow_render(g, PITCH, ph, row + 4);
                                for (int x = 0; x < CF_VIEW_W; ++x) b += g[hz * PITCH + x] == 0xff000000u;
                                black0 = b > black0 ? b : black0;
                            }
                    }
                }
                /* the facing cover wholly off the screen's leading edge at the end, and every cover
                   within the row's turn, every cover on it leaning, and the held row's frame there */
                int out[6];
                cf_enter_quad(frac, lead, 1e-5f, lean, (int)floorf(face + .5f), out);
                assert(dir > 0 ? out[1] > (2 * CF_VIEW_W - 1 - 2 * CF_CX) * 8 : out[2] <= (1 - 2 * CF_CX) * 8);
                assert(!turned_on(frac, lead, 1e-5f, lean, face));
                for (int i = 0; i < last_n; ++i)
                    if (quad_cols(last[i]) && !((last[i][4] - last[i][3]) * lean > 0 && (last[i][0] - face) * dir <= -1 + 1e-3f)) {
                        fprintf(stderr, "enter_frames: theme %d dir %d start %d: album %d on the screen at the end, %d..%d, edges %d %d, facing %.2f\n",
                                ti, dir, start, last[i][0], last[i][1], last[i][2], last[i][3], last[i][4], face);
                        assert(0);
                    }
                /* the held row exactly: each cover on the screen where the held row has it, to a
                   1/16 px, its album the same, and the same pixels drawn */
                int lq[15][6], ln, off = 0;
                float x0 = cf_thumb_hold(lean, lead, frac);
                int r0 = (int)floorf(x0 + .5f), f0 = (int)((x0 - (float)r0) * ONE), sh;
                const unsigned *held_ring[15];
                cf_thumb_enter(lean, 0, 0); /* in: the held row */
                ln = cf_frame_quads(f0, lq), sh = cf_frame_window(f0);
                for (int j = -7; j <= 7; ++j) held_ring[j + 7] = ART(r0 + sh + j);
                coverflow_render(g, PITCH, f0, held_ring + 4);
                for (int x = 0; x < CF_VIEW_W; ++x) black1 += g[hz * PITCH + x] == 0xff000000u;
                for (int i = 0; i < last_n; ++i) {
                    int j = 0;
                    if (!quad_cols(last[i])) continue;
                    while (j < ln && lq[j][0] + r0 != last[i][0]) ++j;
                    assert(j < ln && lq[j][5] == last[i][5]);
                    for (int e = 1; e < 5; ++e) assert(abs(lq[j][e] - last[i][e]) <= 1);
                }
                for (int y = 0; y < H; ++y)
                    for (int x = 0; x < CF_VIEW_W; ++x) off += (f[y * PITCH + x] == 0xff000000u) != (g[y * PITCH + x] == 0xff000000u);
                assert(off < H);
                /* no hole opens on the way: never more of the horizon black than the row at speed,
                   as it was or formed, its facing point anywhere through an album, or the held row
                   shows, and 2% of it */
                if (worst > (black0 > black1 ? black0 : black1) + CF_VIEW_W / 50) {
                    fprintf(stderr, "enter_frames: theme %d dir %d start %d at %d/%d: %d px of the horizon black, the row's %d, the held row's %d\n",
                            ti, dir, start, worst_k, steps, worst, black0, black1);
                    assert(0);
                }
            }
        cf_thumb_lean(0);
        close_page();
    }
    #undef ART
    shim_themes[1].cf = fixture;
    alt = 0;
}

/* ---- At rest, nothing moving (a whole album at the middle), each built-in theme's frame is the bytes
   it has always been, dark and light: only frames while the covers move draw the cover coming in at
   the edge. ---- */
void cf_shelf_still(int still);
static void rest_pinned(void) {
    static const cf_style_t shipped[CF_THEMES] = CF_SHIPPED_ALL; /* every built-in theme's, in index order */
    static const unsigned pinned[CF_THEMES] = { 0xa723fc9cu };
    static unsigned f[300 * PITCH], art[15][160 * 160];
    const cf_style_t ticks = shim_themes[0].cf;
    for (int i = 0; i < 15; ++i)
        for (int k = 0; k < 160 * 160; ++k) art[i][k] = 0xff000000u | ((unsigned)(i * 160 * 160 + k) * 2654435761u >> 8 & 0xffffff);
    const unsigned *ring[15];
    for (int j = 0; j < 15; ++j) ring[j] = art[j];
    alt = 0; /* OLED's rows and paper */
    cf_shelf_still(1); /* nothing moving, as coverflow_paint has it */
    for (int ti = 0; ti < CF_THEMES; ++ti) {
        shim_themes[0].cf = shipped[ti];
        open_page();
        unsigned hash = 2166136261u;
        for (light = 0; light < 2; ++light) {
            for (unsigned i = 0; i < sizeof f / 4; ++i) f[i] = 0;
            coverflow_render(f, PITCH, 0, ring + 4);
            for (int i = 0; i < shipped[ti].view_h * PITCH; ++i) hash = (hash ^ f[i]) * 16777619u;
            /* a theme whose reflection starts at 0 brightness (OLED's): under the centre cover each row is one colour,
               the page's, across the cover's width */
            for (int y = shipped[ti].top + 161; !shipped[ti].reflect_top && y < shipped[ti].view_h; ++y)
                for (int x = CF_CX - 70; x < CF_CX + 70; ++x) assert(f[y * PITCH + x] == f[y * PITCH + CF_CX - 70]);
        }
        if (hash != pinned[ti]) fprintf(stderr, "rest_pinned: theme %d 0x%08xu\n", ti, hash);
        assert(hash == pinned[ti]);
        if (!ti) { /* the page: a glide's frame on a whole album is a moving one (the fourth sliver
                      drawn), and once it stops the still frame is drawn again */
            static unsigned before[CF_VIEW_H * CF_VIEW_W];
            static int fake_anim[64];
            int canvas[16] = { 0 };
            widget *sl = slide();
            frame_fail = 0, top_window = page;
            for (int i = 0; i < 30; ++i) coverflow_paint(sl, canvas); /* every texture loaded */
            memcpy(before, shown, sizeof shown);
            *(void **)(sl->raw + SLIDE_ANIMATOR) = fake_anim;
            coverflow_paint(sl, canvas);
            assert(memcmp(before, shown, sizeof shown));
            *(void **)(sl->raw + SLIDE_ANIMATOR) = 0;
            coverflow_paint(sl, canvas);
            assert(!memcmp(before, shown, sizeof shown));
            top_window = 0;
            cf_shelf_still(1); /* the page's own render leaves it moving */
        }
        close_page();
    }
    light = 0;
    cf_shelf_still(0);
    shim_themes[0].cf = ticks;
}

/* ---- A cover comes onto the screen already drawn: in each built-in theme, dark and light, at rest, in the row at speed (slow and fast, either way) and in the held row (at
   the theme's row_out, and at the smallest tools/theme.py takes, CF_WIDE covers on the screen),
   every cover any of which is on the screen is at its own brightness (bright2 or more: no fading at
   the screen's edge), and as the covers slide an eighth of an album a frame (a quarter in Light Mode), the window drops a
   cover only once it shows nothing in the frame, and gains one only while it still shows nothing:
   none appears or disappears while any of it shows, but in Light Mode, where one the window drops
   at the screen's edge can still move the paper there by up to 2 levels (the dark frame's black
   shows nothing of it). (Into letters, enter_frames checks the same as the entry runs.) ---- */
int cf_pose_bright(int t);
void cf_frame_quad(int frac, int a, int *out);
static unsigned edge_art[64][160 * 160];
/* The frame at frac with the window's middle at album mid, each album its own art, less album gone */
static void edge_frame(unsigned *f, int frac, int mid, int gone) {
    const unsigned *ring[15];
    for (int j = -7; j <= 7; ++j) ring[j + 7] = mid + j == gone ? 0 : edge_art[((mid + j) % 64 + 64) % 64];
    coverflow_render(f, PITCH, frac, ring + 4);
}
static int edge_levels(const unsigned *a, const unsigned *b, int h) { /* the most h rows differ by, a channel */
    int m = 0;
    for (int i = 0; i < h * PITCH; ++i)
        for (int k = 0, d; k < 24; k += 8) d = abs((int)(a[i] >> k & 255) - (int)(b[i] >> k & 255)), m = d > m ? d : m;
    return m;
}
static void edge_drawn(void) {
    static unsigned f[300 * PITCH], last[300 * PITCH], g[300 * PITCH];
    static const cf_style_t shipped[CF_THEMES] = CF_SHIPPED_ALL; /* every built-in theme's, in index order */
    static const float leads[] = { 0, .05f, .3f, 1, 2, 3.2f, 5 };
    static const int row_out_min[CF_THEMES] = CF_ROW_OUT_MIN; /* tools/theme.py's, each theme's */
    const cf_style_t fixture = shim_themes[1].cf;
    for (int i = 0; i < 64; ++i)
        for (int k = 0; k < 160 * 160; ++k) edge_art[i][k] = 0xff000000u | ((unsigned)(i * 160 * 160 + k) * 2654435761u >> 8 & 0xffffff);
    alt = 1;
    for (int ti = 0; ti < CF_THEMES; ++ti) {
        shim_themes[1].cf = shipped[ti];
        open_page(); /* cf_restyle's */
        const cf_style_t *cs = &shim_themes[1].cf;
        for (int t = 2 * ONE; t <= 15 * ONE / 2; t += ONE / 16) assert(cf_pose_bright(t) == cs->bright2);
        for (int v = 0; v < 2 * 9; ++v) /* each c dark, then light */
            for (int dir = -1, c = v % 9; dir <= 1; dir += 2) { /* c 0: at rest; 1-6: the row at speed at leads[c]; 7, 8: held, 8 at row_out_min */
                light = v >= 9;
                shim_themes[1].cf.row_out = c == 8 ? row_out_min[ti] : shipped[ti].row_out;
                if (c < 7) cf_thumb_enter(0, 0, 0), cf_thumb_lead(c ? dir * leads[c] : -1);
                else hold_formed(-dir), cf_thumb_enter(-dir, 0, 0);
                /* half an album on from one cover is half an album back from the next: no cover cut
                   where the window or the shelf's reach ends */
                edge_frame(f, ONE / 2, cf_frame_window(ONE / 2), 1 << 30);
                edge_frame(g, -ONE / 2, 1 + cf_frame_window(-ONE / 2), 1 << 30);
                assert(!memcmp(f, g, cs->view_h * PITCH * sizeof *g));
                int last_at = 0, last_mid = 0, last_frac = 0;
                for (int s = 0; s <= 48; s += light ? 4 : 2) { /* three albums the spin's way */
                    float p = (float)(dir * s) / 16;
                    int at = (int)floorf(p + .5f), frac = (int)((p - (float)at) * ONE), q[15][6], n = cf_frame_quads(frac, q);
                    int mid = at + cf_frame_window(frac);
                    for (int i = 0; c && i < n; ++i)
                        if (quad_cols(q[i]) && q[i][5] < cs->bright2) {
                            fprintf(stderr, "edge_drawn: theme %d light %d case %d dir %d at %d: album %d on the screen at %d\n",
                                    ti, light, c, dir, s, q[i][0], q[i][5]);
                            assert(0);
                        }
                    edge_frame(f, frac, mid, 1 << 30);
                    /* a cover joining or leaving the window: off the screen in the frame without it,
                       or adding nothing to the frame with it (behind nearer covers) */
                    for (int pass = 0; s && pass < 2; ++pass)
                        for (int a = (pass ? mid : last_mid) - 7; a <= (pass ? mid : last_mid) + 7; ++a) {
                            int out[6];
                            if (abs(a - (pass ? last_mid : mid)) <= 7) continue;
                            cf_frame_quad(pass ? last_frac : frac, a - (pass ? last_at : at), out);
                            if (!quad_cols(out)) continue;
                            edge_frame(g, pass ? frac : last_frac, pass ? mid : last_mid, a);
                            if (edge_levels(pass ? f : last, g, cs->view_h) > (light ? 2 : 0)) { /* the frame's rows */
                                fprintf(stderr, "edge_drawn: theme %d light %d case %d dir %d at %d: album %d %s the window showing\n",
                                        ti, light, c, dir, s, a, pass ? "joins" : "leaves");
                                assert(0);
                            }
                        }
                    memcpy(last, f, sizeof last), last_at = at, last_mid = mid, last_frac = frac;
                }
            }
        light = 0, shim_themes[1].cf.row_out = shipped[ti].row_out;
        /* The shelf moving, a tenth to nine tenths (odd) of a touch glide's or a step's album either way:
           both edges' covers whole, each side shows as many covers in their bodies (reflections out
           of the frame, reflect_top 0; rounded corners) as the other, give or take the one the
           geometry adds; and it draws its whole ring (shelf_draws): every cover any of which is on
           the screen changes the frame. */
        shim_themes[1].cf.reflect_top = 0;
        cf_thumb_enter(0, 0, 0), cf_thumb_lead(-1);
        for (int dir = -1; dir <= 1; dir += 2)
            for (int s = 1; s <= 9; s += 2) {
                float p = (float)(dir * s) / 10;
                int at = (int)floorf(p + .5f), frac = (int)((p - (float)at) * ONE), side[2] = { 0, 0 };
                edge_frame(f, frac, at, 1 << 30);
                int q[15][6];
                for (int a = at - 7; a <= at + 7; ++a) cf_frame_quad(frac, a - at, q[a - at + 7]);
                for (int a = at - 7; a <= at + 7; ++a) {
                    int shows, bare = 0; /* its columns on the screen that no nearer cover's quad reaches */
                    edge_frame(g, frac, at, a);
                    side[(float)a > p] += shows = memcmp(f, g, cs->view_h * PITCH * sizeof *g) != 0;
                    for (int x = 0, m; x < CF_VIEW_W; ++x) {
                        int in = (m = (2 * x + 1 - 2 * CF_CX) * 8) >= q[a - at + 7][1] && m < q[a - at + 7][2];
                        for (int b = at - 7; in && b <= at + 7; ++b)
                            in = b == a || fabsf(b - p) > fabsf(a - p) || m < q[b - at + 7][1] || m >= q[b - at + 7][2];
                        bare += in;
                    }
                    if (bare && !shows) {
                        fprintf(stderr, "edge_drawn: theme %d dir %d at %d/10: album %d not drawn, %d columns bare\n", ti, dir, s, a, bare);
                        assert(0);
                    }
                }
                if (abs(side[0] - side[1]) > 1 || side[0] < 3 || side[1] < 3) {
                    fprintf(stderr, "edge_drawn: theme %d dir %d at %d/10: %d covers show left, %d right\n", ti, dir, s, side[0], side[1]);
                    assert(0);
                }
            }
        shim_themes[1].cf = shipped[ti];
        cf_thumb_enter(0, 0, 0), cf_thumb_lead(-1), cf_thumb_lean(0);
        edge_frame(f, 0, 0, 1 << 30); /* cf_corners' table at the theme's radius again */
        close_page();
    }
    shim_themes[1].cf = fixture;
    alt = 0;
}

/* ---- The thumb on the shipped numbers (OLED's theme.xml coverflow.shelf): the cap at every
   sensitivity, the letters after the dwell and back, and a long spin's landing. ---- */
static int initial_of(int i) { char c = names[i][0]; return c >= 'a' && c <= 'z' ? c - 32 : c; }
/* Going by letters, where the row is going: the goal's album, a letter's first; the row's place,
   unwrapped from last; the row there, the thumb still. */
#define TARGET() ((((*index + (int)lroundf(th[T_GOAL])) % n) + n) % n)
#define FIRST(a) ((a) == 0 || initial_of(a) != initial_of((a) - 1))
static float th_at(int *index, int n, float *last) { /* the row's place, unwrapped from last */
    float d = (float)*index + th[T_POS] - *last;
    return *last += d - (float)n * floorf(d / (float)n + .5f);
}
static void th_arrive(void) { /* the thumb still, till the row is there */
    for (int i = 0; i < 300 && th[T_POS] != th[T_GOAL]; ++i) th_pass(0, 17);
    assert(th[T_POS] == th[T_GOAL]);
}

/* ---- Fast Navigation off: 1.7.6's letters, as 1.7.6's own tests had them (the shipped
   numbers): after the dwell the covers flow on and each letter is named on the moving row,
   jump_pace apart, shown for LETTER_MS; slowing, turning back or a lift ends them. No held row, no
   entry or fold, no letters' slots or loader. ---- */
int cf_fx_letters(void);
static int first_of_letter(int i) { return i >= 0 && (!i || initial_of(i) != initial_of(i - 1)); }
/* The first album of the letter after (dir 1) or before i's, round the ends. */
static int next_letter(int i, int n, int dir) {
    int j = i;
    do j = (j + dir + n) % n; while (initial_of(j) == initial_of(i));
    while (dir < 0 && !first_of_letter(j)) --j;
    return j;
}
static int named_album(widget *covers_view, int n) {
    int i = 0;
    while (i < n && strcmp(album_caption(covers_view), names[i])) ++i;
    return i < n ? i : -1;
}

/* ---- The arch (#329's), as shipped, Fast Navigation off: at speed the covers within arch_span of
   the middle are at the side covers' depth, none nearer the eye, each turned more the farther out;
   their places across and everything arch_span out are the shelf's; unformed, the shelf's own pose.
   The follower: no arch at a turn a second or from a step of one album, formed at two turns a
   second, gone by the landing, and none with Fast Navigation on, which keeps the row at speed. ---- */
void cf_thumb_arch(float arch), cf_arch_pose(int t, float arch, int *out);

/* ---- Pages, the scoped pages' cards, Go to Album, the build's Cancel row. ---- */
extern int coverflow_rated(int (*)(void *)), coverflow_goto_album(void *), coverflow_shows_album(void *);
extern void *coverflow_tracks(void *);
extern void coverflow_by_year(void *);
static int under(const widget *x, const widget *a) {
    for (; x->parent; x = &w[x->parent])
        if (&w[x->parent] == a) return 1;
    return 0;
}
static widget *label_in(widget *v, int k) { /* v's k-th label of its own */
    for (int i = 0; i < v->nkids; ++i)
        if (!strcmp(w[v->kids[i]].type, "hscroll_label") && !k--) return &w[v->kids[i]];
    return 0;
}
static widget *row_under(widget *a, int k) { /* the k-th list_item under a */
    for (int i = (int)(a - w) + 1; i <= nw; ++i)
        if (!strcmp(w[i].type, "list_item") && under(&w[i], a) && !k--) return &w[i];
    return 0;
}

static int no_hearts(void *r) { return 0; }
static int rated_sort(void) {
    int v = -1;
    FILE *f = fopen(PEQ_ROOT "/mnt/data/ringnav-ratedsort", "rb");
    if (f) assert(fread(&v, sizeof v, 1, f) == 1), fclose(f);
    return v;
}
/* The rated albums' Sort card steps their own order, saved apart from the library's, and stays on the card. Go to
   Album's page is the album's tracks alone. */
static void scoped_pages(void) {
    rescan();
    assert(!coverflow_rated(no_hearts)); /* nothing rated: no page */
    page = &w[nw + 1];
    assert(coverflow_rated(five_hearts));
    run();
    widget *s = slide();
    int n = slide_count(s) - 1, lib = saved_sort();
    unlink(PEQ_ROOT "/mnt/data/ringnav-ratedsort");
    static const char *const steps[] = { "Sort: Album", "Sort: Artist" };
    for (int k = 0; k < 2; ++k) {
        click_album(s, n);
        run();
        s = slide();
        assert(rated_sort() == k && saved_sort() == lib && *(int *)(s->raw + SLIDE_INDEX) == n);
        assert(!strcmp(album_caption(&w[s->parent]), steps[k]));
    }
    close_page();
    page = &w[nw + 1];
    assert(coverflow_goto_album(records[0]));
    assert(!strcmp(title(), names[0]) && !slide() && coverflow_shows_album(records[0]) && !coverflow_shows_album(records[1]));
    backs = 0;
    key(KEY_RETURN);
    assert(backs == 1); /* no covers under it: Return closes the page */
    close_page();
    assert(!coverflow_shows_album(records[0]));
    static char gone[0x60];
    *(const char **)(gone + REC_ALBUM) = "Nowhere", *(const char **)(gone + REC_PATH) = "/x";
    assert(!coverflow_goto_album(gone)); /* no tracks: nothing opens */
    window_fail = 1;
    assert(!coverflow_goto_album(records[0]) && !coverflow_rated(five_hearts)); /* no page to open */
    window_fail = 0;
    /* An artist page's albums with no memory to sort them keep their order. */
    void *mine = _create_deque(0);
    _deque_push_back(mine, records[5]), _deque_push_back(mine, records[7]);
    ranked[0] = "C", ranked[1] = 0;
    calloc_fail = 1;
    coverflow_by_year(mine);
    calloc_fail = 0;
    assert(deque_at(mine, 0) == records[5] && deque_at(mine, 1) == records[7]);
    coverflow_by_year(mine);
    assert(deque_at(mine, 0) == records[7] && deque_at(mine, 1) == records[5]);
    ranked[0] = 0;
    free(mine);
}

/* The build's Cancel row, as Return: the album in hand finishes, the rest wait for the next open. */
static void cancel_build(void) {
    album("Hold", "cover.jpg"), album("Back", "cover.jpg");
    rescan();
    block_at = calls;
    widget home;
    memset(&home, 0, sizeof(home));
    coverflow_home(&home, 0);
    page = &w[nw + 1];
    home.click(home.ctx, 0);
    while (!blocked) usleep(1000);
    widget *row = row_under(page, 0);
    assert(!strncmp(title(), "Preparing artwork", 17) && row && !strcmp(label_in(row, 0)->text, "Cancel"));
    row->click(row->ctx, 0);
    pthread_t t;
    joining = 0;
    pthread_create(&t, 0, release, 0);
    run();
    pthread_join(t, 0);
    assert(size("Hold") == 9 && size("Back") == -1 && !tmp_files() && slide() && slide()->visible);
    close_page();
    block_at = -1;
    open_page();
    close_page();
    assert(size("Back") == 9);
    albums -= 2;
    rescan();
}


/* ---- Each step in letters slides the held row there, through the albums between, on a follower
   slide_ms long: on at once from where it is, never back, never past the letter's first album, the
   caption on the album nearest the middle as it passes, the letter and the one click at the step. A
   step before it arrives goes on from there without stopping; round the seam it goes forward through
   it; a lift lets it arrive, then folds. ---- */
int cf_cover_x(int o, int frac);
static int target_x(int *index, int n, int to) { /* the letter's cover's middle on the screen, 1/16 px */
    int q = (int)floorf(th[T_POS] + .5f), c = ((*index + q) % n + n) % n, o = ((to - c) % n + n + n / 2) % n - n / 2;
    return cf_cover_x(th[T_FOLD] ? 0 : o, (int)((th[T_POS] - (float)q) * ONE));
}










/* ---- Half copies for letters (docs/internals.md#coverflow-depth): made once from a decoded cover,
   or by idle passes at rest from the cache, beside its file, whole or not at all; read with no decode
   and spread two by two. Going by letters each frame loads while it has spent under FX_LETTERS_MS:
   first the covers on the screen, then those ahead of the row towards the letter it goes
   to, that letter's, then the next and the previous letters', each window as far from its letter's
   first album as the screen's is from the album at the middle, and full art over the screen's half
   copies once the row is there. Four letters of 40 albums make the windows apart. ---- */
int cf_fx_has(int a), cf_fx_letters(void);
unsigned cf_album_key(int a), album_key(void *r);
void coverflow_halves(void);
int coverflow_letter_next(void *w, int from, int dir, int n, unsigned *letter);
static void cache_file(unsigned key, const char *suffix, char *path) {
    snprintf(path, 600, PEQ_ROOT "/mnt/mmc/.coverflow/%08x.jpg%s", key, suffix);
}
static long size_of(const char *path) { struct stat st; return stat(path, &st) ? -1 : (long)st.st_size; }
static int halves(int remove) { /* the half copies on the card; removed, if asked */
    int k = 0;
    DIR *d = opendir(PEQ_ROOT "/mnt/mmc/.coverflow");
    for (struct dirent *e; d && (e = readdir(d));) {
        size_t l = strlen(e->d_name);
        if (l < 5 || strcmp(e->d_name + l - 5, ".half")) continue;
        char path[600];
        snprintf(path, sizeof path, PEQ_ROOT "/mnt/mmc/.coverflow/%s", e->d_name);
        if (remove) unlink(path);
        ++k;
    }
    if (d) closedir(d);
    return k;
}
static void cover_tex(int a, unsigned *tex) { /* album a's cover as decode() makes it of the shim's */
    static unsigned char px[160 * 160 * 4];
    char path[600];
    unsigned seed = 0;
    cache_file(cf_album_key(a), "", path);
    for (const char *c = strrchr(path, '/'); *c; ++c) seed = seed * 31 + (unsigned char)*c;
    pattern(px, seed % 1000);
    for (int i = 0; i < 160 * 160; ++i) tex[i] = 0xff000000u | px[4 * i] | px[4 * i + 1] << 8 | px[4 * i + 2] << 16;
}
static int read_half(int a, unsigned *half) { /* album a's half copy, 80 x 80 */
    char path[600];
    cache_file(cf_album_key(a), ".half", path);
    FILE *f = fopen(path, "rb");
    int ok = f && fread(half, 4, 80 * 80, f) == 80 * 80;
    if (f) fclose(f);
    return ok;
}

/* ---- The loader: going by letters the half copies are read on a thread of its own, the nearest
   first, at most LD_SLOTS in hand; the UI thread only spreads one that is there into its texture,
   within FX_LETTERS_MS, and never waits on the card. A copy read for a page since closed is
   dropped; closing the page stops the thread after the read in hand,
   and nothing runs while the page is closed. Runs on half_art's library. ---- */
static double wall_ms(void) {
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return t.tv_sec * 1e3 + t.tv_nsec / 1e6;
}
#define FILL_FRAMES 2 /* the frames after a letter step with a cover drawn waiting, at most */


/* ---- Playlist art (playlist_art.c): the playlist table's rows, as its own SQLite query reads them. ---- */
#include "playlist_art.h"
unsigned tags_key(const char *artist, const char *album);
static struct { int cd, id; const char *url, *album, *artist, *aa; } pl_rows[160];
static char pl_urls[160][600];
static int pl_n, pl_opens, pl_busy, pl_scans, pl_ui_opens, pl_awake;
int reset_poweroptions_timer(int standby, int power_off, int screen) {
    assert(standby && power_off && !screen); /* the screen still times out */
    return ++pl_awake;
}
static pthread_t pl_ui; /* the UI thread, which never opens the database for playlist art */
static int ui_fopens; /* fopens on the UI thread (each file_size is one) */
FILE *__real_fopen(const char *, const char *);
FILE *__wrap_fopen(const char *path, const char *mode) {
    ui_fopens += pthread_equal(pthread_self(), pl_ui);
    return __real_fopen(path, mode);
}
int sqlite3_open(const char *path, void **db) {
    assert(!strcmp(path, "/mnt/data/database.db"));
    *db = (void *)1, ++pl_opens, pl_ui_opens += pthread_equal(pthread_self(), pl_ui);
    return 0;
}
int sqlite3_close(void *db) { return 0; }
/* The messy library, test/fw/messy_playlists.py's shape made here in a loop: cd 1 20,000 untagged
   singles, each in its own folder; cd 2 5,000 songs of 500 albums, 4 KB album tags and 800-byte
   fileurls with non-UTF-8 bytes; cd 3 300 songs, 280 gone from the card, 20 in folders with a
   cover.jpg; cd 0 (Q2 Muse's first playlist) and cd 4 to 100 300 songs each; 50 junk rows (cd_num NULL, -1, 999; fileurl NULL or empty).
   Their ids shuffled across the playlists: id 101 + k is row messy_at[k]. */
enum { MESSY_ROWS = 20000 + 5000 + 300 + 98 * 300 + 50, MESSY_LIVE = 20 };
static int messy, messy_at[MESSY_ROWS], messy_ui_execs, messy_reads; /* reads: a playlist's own */
static int messy_cd(int r, int *null) { /* row r's cd_num; *null: none */
    static const int junk[5] = { 0, -1, 999, 5, 5 };
    *null = r >= MESSY_ROWS - 50 && (r - (MESSY_ROWS - 50)) % 5 == 0;
    return r < 20000 ? 1 : r < 25000 ? 2 : r < 25300 ? 3 : r < MESSY_ROWS - 50 ? (4 + (r - 25300) / 300) % 101 : junk[(r - (MESSY_ROWS - 50)) % 5];
}
/* row r's fileurl, album, songer, albumsonger (in static buffers) */
static void messy_row(int r, char **v) {
    static char url[900], album[4200], tag[4097];
    if (!tag[0]) memset(tag, 'T', 4096);
    v[1] = url, v[2] = album, v[3] = "Artist", v[4] = "";
    if (r < 20000) {
        snprintf(url, sizeof url, PEQ_ROOT "/mnt/mmc/S/%d/t.flac", r);
        v[2] = v[3] = v[4] = 0;
    } else if (r < 25000) {
        int n = snprintf(url, sizeof url, PEQ_ROOT "/mnt/mmc/B/%d/", (r - 20000) % 500);
        while (n < 780) url[n++] = '\xff', url[n++] = '\xfe';
        snprintf(url + n, sizeof url - n, "%d.flac", r);
        snprintf(album, sizeof album, "%s%d", tag, (r - 20000) % 500);
    } else if (r < 25300) {
        snprintf(url, sizeof url, PEQ_ROOT "/mnt/mmc/%s/%d/t.flac", r - 25000 < MESSY_LIVE ? "L" : "Gone", r - 25000);
        snprintf(album, sizeof album, "Album %d", r - 25000);
    } else if (r < MESSY_ROWS - 50) {
        int cd = (4 + (r - 25300) / 300) % 101;
        snprintf(url, sizeof url, PEQ_ROOT "/mnt/mmc/P/%d/%d.flac", cd, (r - 25300) % 300);
        snprintf(album, sizeof album, "Album %d.%d", cd, (r - 25300) % 7);
    } else {
        int j = (r - (MESSY_ROWS - 50)) % 5;
        snprintf(url, sizeof url, j == 4 ? "" : PEQ_ROOT "/mnt/mmc/j.flac");
        v[1] = j == 3 ? 0 : url, v[2] = v[3] = v[4] = 0;
    }
}
static void messy_on(void) {
    unsigned seed = 2026;
    for (int k = 0; k < MESSY_ROWS; ++k) messy_at[k] = k;
    for (int k = MESSY_ROWS; k > 1; --k) {
        seed = seed * 1664525u + 1013904223u;
        int j = (int)((seed >> 8) % (unsigned)k), t = messy_at[k - 1];
        messy_at[k - 1] = messy_at[j], messy_at[j] = t;
    }
    messy = 1;
}
/* the messy table's first 1024 rows past id lo through row, every playlist's: (id, cd_num, fileurl)
   for the refresh, else (id, fileurl, album, songer, albumsonger, cd_num); the rows given */
static int messy_exec(int songs, unsigned lo, int (*row)(void *, int, char **, char **), void *ctx, int *rows) {
    char cds[16], id[16], *v[6];
    if (pthread_equal(pthread_self(), pl_ui)) ++messy_ui_execs;
    messy_reads += songs && lo == 100;
    for (int k = (int)lo - 100, null; k < MESSY_ROWS && *rows < 1024; ++k) {
        int c = messy_cd(messy_at[k], &null);
        snprintf(cds, sizeof cds, "%d", c), snprintf(id, sizeof id, "%d", 101 + k);
        messy_row(messy_at[k], v);
        v[0] = id, v[5] = null ? 0 : cds, ++*rows;
        char *three[3] = { id, v[5], v[1] };
        if (songs ? row(ctx, 6, v, 0) : row(ctx, 3, three, 0)) return 4; /* SQLITE_ABORT */
    }
    return 0;
}
/* The most rows a statement gave (the reads go in pages of rows, a statement each). */
static int pl_page_most;
static int pl_exec(void *db, const char *sql, int (*row)(void *, int, char **, char **), void *ctx, int *rows) {
    unsigned lo;
    char want[200], id[16];
    if (sscanf(sql, "select id,cd_num,fileurl from playlist where id>%u", &lo) == 1) {
        snprintf(want, sizeof want, "select id,cd_num,fileurl from playlist where id>%u order by id limit 1024", lo);
        assert(!strcmp(sql, want) && lo >= 100);
        if (pl_busy) return 5; /* SQLITE_BUSY: stock writes the database */
        pl_scans += lo == 100;
        if (messy) return messy_exec(0, lo, row, ctx, rows);
        for (int last = (int)lo, next; *rows < 1024; last = pl_rows[next].id) { /* every playlist's songs, in id order */
            next = -1;
            for (int i = 0; i < pl_n; ++i)
                if (pl_rows[i].id > last && (next < 0 || pl_rows[i].id < pl_rows[next].id)) next = i;
            if (next < 0) return 0;
            char cds[16];
            snprintf(cds, sizeof cds, "%d", pl_rows[next].cd), snprintf(id, sizeof id, "%d", pl_rows[next].id);
            char *v[3] = { id, cds, (char *)pl_rows[next].url };
            ++*rows;
            if (row(ctx, 3, v, 0)) return 4;
        }
        return 0;
    }
    /* a playlist's own read: every playlist's rows, a page at a time, song_row keeping its own */
    assert(sscanf(sql, "select id,fileurl,album,songer,albumsonger,cd_num from playlist where id>%u", &lo) == 1);
    snprintf(want, sizeof want, "select id,fileurl,album,songer,albumsonger,cd_num from playlist where id>%u order by id limit 1024", lo);
    assert(!strcmp(sql, want) && lo >= 100);
    if (messy) return messy_exec(1, lo, row, ctx, rows);
    for (int i = 0; i < pl_n && *rows < 1024; ++i) { /* kept in id order */
        if (pl_rows[i].id <= (int)lo) continue;
        char cds[16];
        snprintf(id, sizeof id, "%d", pl_rows[i].id), snprintf(cds, sizeof cds, "%d", pl_rows[i].cd);
        char *v[6] = { id, (char *)pl_rows[i].url, (char *)pl_rows[i].album, (char *)pl_rows[i].artist, (char *)pl_rows[i].aa, cds };
        ++*rows;
        if (row(ctx, 6, v, 0)) return 4; /* SQLITE_ABORT */
    }
    return 0;
}
void sqlite3_progress_handler(void *db, int ops, int (*f)(void *), void *ctx) { (void)db, (void)ops, (void)f, (void)ctx; }
int sqlite3_exec(void *db, const char *sql, int (*row)(void *, int, char **, char **), void *ctx, char **err) {
    int rows = 0, r;
    if (!strcmp(sql, "PRAGMA cache_size=64")) return 0;
    r = pl_exec(db, sql, row, ctx, &rows);
    pl_page_most = rows > pl_page_most ? rows : pl_page_most;
    return r;
}
int shim_xstat(int ver, const char *path, void *out) {
    struct stat s;
    unsigned *o = out;
    if (stat(path, &s)) return -1;
    memset(o, 0, 36 * 4);
    o[5] = s.st_mode, o[12] = (unsigned)s.st_size, o[16] = (unsigned)s.st_mtime;
    return 0;
}
/* A song of album in playlist cd, its file in music/<album>/. */
static void pl_song(int cd, int id, const char *album) {
    char dir[500];
    snprintf(dir, sizeof dir, PEQ_ROOT "/music/%s", album);
    mkdir(dir, 0755);
    snprintf(pl_urls[pl_n], 600, "%s/%d.flac", dir, id);
    fclose(fopen(pl_urls[pl_n], "w"));
    pl_rows[pl_n].cd = cd, pl_rows[pl_n].id = id, pl_rows[pl_n].url = pl_urls[pl_n];
    pl_rows[pl_n].album = album, pl_rows[pl_n].artist = "Artist", pl_rows[pl_n++].aa = "";
}
/* The art file a playlist's songs name now: FNV-1a over each song's fileurl, in id order. */
static char *pl_file(char *path, const char *kind, int cd, const char *suffix) {
    unsigned h = FNV_SEED;
    for (int i = 0; i < pl_n; ++i)
        if (pl_rows[i].cd == cd) h = fnv(h, (const unsigned char *)pl_rows[i].url);
    snprintf(path, 600, PEQ_ROOT "/mnt/mmc/.coverflow/%s_%d_%08x%s", kind, cd, h, suffix);
    return path;
}
static int pl_ready[16], pl_readies;
static void on_ready(int cd) { pl_ready[pl_readies++ % 16] = cd; }
static int pl_heard[100];
static void heard(int cd) { ++pl_heard[cd]; }
/* The last 2x2's ffmpeg: its four inputs and its filter graph. */
static char ff_in[4][600], ff_graph[600];
static int tile_args(void) {
    char line[600], prev[600] = "";
    int n = 0;
    FILE *f = fopen(ART_FFMPEG ".args", "r");
    while (f && fgets(line, sizeof line, f)) {
        line[strcspn(line, "\n")] = 0;
        if (!strcmp(prev, "-i") && n < 4) snprintf(ff_in[n++], 600, "%s", line);
        if (!strcmp(prev, "-filter_complex")) snprintf(ff_graph, sizeof ff_graph, "%s", line);
        snprintf(prev, sizeof prev, "%s", line);
    }
    if (f) fclose(f);
    unlink(ART_FFMPEG ".args");
    return n;
}
static int content(const char *path, const char *want) {
    char got[600] = "";
    FILE *f = fopen(path, "r");
    size_t n = f ? fread(got, 1, sizeof got - 1, f) : 0;
    if (f) fclose(f);
    got[n] = 0;
    return f && !strcmp(got, want);
}
static void wait_blocked(void) { while (!blocked) usleep(1000); }
/* A playlist's own picture at path, w x h by its JPEG frame header (SOF0), which the ffmpeg stub
   decodes when it holds a "picture" line. */
static void pl_picture(const char *path, int w, int h, int decodes) {
    const unsigned char b[24] = { 0xff, 0xd8, 0xff, 0xc0, 0, 17, 8, h >> 8, h & 255, w >> 8, w & 255, 3 };
    FILE *f = fopen(path, "wb");
    fwrite(b, 1, sizeof b, f), fputs(decodes ? "\npicture\n" : "\n", f), fclose(f);
}
/* playlists.c's refresh, as a Playlists rebuild asks for it, and the worker's read landed (its
   playlists not told: the tests count the builds' readies). */
static void pl_refresh(void) {
    void (*ready)(int) = playlist_art_ready;
    playlist_art_ready = 0;
    playlist_art_refresh();
    run();
    playlist_art_ready = ready;
}
static void playlist_art_files(void) {
    /* each cover filled to 80x80 and cut square: a non-square one is not squashed */
#define FILL(i, o) "[" #i "]scale=80:80:force_original_aspect_ratio=increase,crop=80:80[" #o "];"
    static const char graph[] = FILL(0, a) FILL(1, b) FILL(2, c) FILL(3, d)
        "[a][b]hstack[t];[c][d]hstack[u];[t][u]vstack";
#undef FILL
    char url[700], path[600], old[600], cached[3][600];
    const char *three[] = { "PL A", "PL B", "PL C" };
    playlist_art_ready = on_ready;
    int fails = embedded_fails; /* the albums' tests after these build from cover.jpg */
    embedded_fails = 0, block_at = -1, free_blocks = 1 << 20, calls = 0;
    mkdir(PEQ_ROOT "/mnt/mmc/.coverflow", 0755);
    mkdir(PEQ_ROOT "/mnt/mmc/_explaylist_data", 0755);

    /* Three albums whose Cover Flow art is cached: nothing built but the 2x2, a b / c a of those
       files by one ffmpeg (hstack and vstack: 4.2 has no xstack), named for the songs. */
    pl_song(7, 101, "PL A"), pl_song(7, 102, "PL B"), pl_song(7, 103, "PL B"), pl_song(7, 104, "PL C");
    for (int i = 0; i < 3; ++i) {
        snprintf(cached[i], 600, PEQ_ROOT "/mnt/mmc/.coverflow/%08x.jpg", tags_key("Artist", three[i]));
        FILE *f = fopen(cached[i], "w");
        fputs(three[i], f), fclose(f);
    }
    /* The UI thread never reads the table: the refresh asks the worker. Before its first read
       lands an ask answers none and queues nothing; a read the database refuses (busy) publishes
       nothing and is not tried again per ask; the next rebuild's read fills the table, and the
       playlists it brings are told, so their rows ask again. */
    pl_ui = pthread_self();
    pl_busy = 1;
    playlist_art_refresh();
    assert(timer_fn && !pl_ui_opens);
    run();
    pl_busy = 0;
    int opens = pl_opens;
    for (int i = 0; i < 20; ++i) assert(!playlist_art(7, "Seven", i & 1, url, sizeof url) && !timer_fn);
    assert(pl_opens == opens && !pl_ui_opens && !playlist_songs(7) && !pl_readies);
    playlist_art_refresh();
    run();
    assert(pl_opens == opens + 1 && !pl_ui_opens && playlist_songs(7) == 4 && pl_readies == 1 && pl_ready[0] == 7);
    pl_readies = 0;
    assert(!playlist_art(7, "Seven", 0, url, sizeof url) && timer_fn);
    run();
    assert(pl_readies == 1 && pl_ready[0] == 7);
    assert(tile_args() == 4 && !strcmp(ff_graph, graph) && !strcmp(ff_in[3], ff_in[0]));
    for (int i = 0; i < 3; ++i) {
        int hit = 0;
        for (int k = 0; k < 3; ++k) hit += !strcmp(ff_in[i], cached[k]);
        assert(hit == 1 && strcmp(ff_in[i], ff_in[(i + 1) % 3]));
    }
    assert(content(pl_file(path, "pl", 7, ".jpg"), "tile") && !tmp_files());
    assert(calls == 1 && strstr(made[0], ".tmp.jpg")); /* only the small one, from the 2x2 */
    assert(playlist_art(7, "Seven", 0, url, sizeof url) && !strncmp(url, "file://", 7) && !strcmp(url + 7, path));
    assert(playlist_art(7, "Seven", 1, url, sizeof url) && !strcmp(url + 7, pl_file(path, "pl", 7, "_s.jpg")) && !access(path, 0));
    assert(!timer_fn && pl_readies == 1); /* ready: nothing queued */
    /* Asks read the hashes refresh took: no SQLite on the UI thread, the same answers. */
    opens = pl_opens;
    for (int i = 0; i < 20; ++i)
        assert(playlist_art(7, "Seven", i & 1, url, sizeof url) && !strcmp(url + 7, pl_file(path, "pl", 7, i & 1 ? "_s.jpg" : ".jpg")));
    assert(pl_opens == opens && !timer_fn);
    /* A refresh the database refuses (busy) keeps the hashes it had. */
    pl_busy = 1, pl_refresh(), pl_busy = 0;
    assert(playlist_art(7, "Seven", 0, url, sizeof url) && !strcmp(url + 7, pl_file(path, "pl", 7, ".jpg")));

    /* Its own picture, _explaylist_data/<name>.jpg, wins over the 2x2. */
    FILE *f;
    pl_picture(PEQ_ROOT "/mnt/mmc/_explaylist_data/Seven.jpg", 640, 640, 1);
    assert(!playlist_art(7, "Seven", 0, url, sizeof url));
    run();
    assert(pl_readies == 2 && pl_ready[1] == 7);
    assert(playlist_art(7, "Seven", 0, url, sizeof url) && strstr(url, "/.coverflow/plc_7_") && content(url + 7, "Seven.jpg"));
    assert(playlist_art(7, "Seven", 1, url, sizeof url) && strstr(url, "_s.jpg") && !access(url + 7, 0));
    /* A 63-byte name with characters FAT refuses: its picture under the escaped name. */
    char name[64];
    snprintf(name, sizeof name, "AC/DC: Live?%051d", 0);
    assert(strlen(name) == 63);
    snprintf(path, sizeof path, PEQ_ROOT "/mnt/mmc/_explaylist_data/AC_DC_ Live_%051d.png", 0);
    pl_picture(path, 640, 640, 1);
    assert(!playlist_art(7, name, 0, url, sizeof url));
    run();
    assert(playlist_art(7, name, 0, url, sizeof url) && content(url + 7, strrchr(path, '/') + 1));

    /* A picture whose size its header doesn't tell: marked, and the 2x2 instead, from then on. */
    pl_song(8, 105, "PL A"), pl_song(8, 106, "PL C");
    pl_refresh();
    fputs("junk", f = fopen(PEQ_ROOT "/mnt/mmc/_explaylist_data/Bad one.jpg", "w")), fclose(f);
    assert(!playlist_art(8, "Bad one", 0, url, sizeof url));
    run();
    assert(tile_args() == 4 && !strcmp(ff_in[0], ff_in[3]) && !strcmp(ff_in[1], ff_in[2]) && strcmp(ff_in[0], ff_in[1]));
    assert(playlist_art(8, "Bad one", 0, url, sizeof url) && !strcmp(url + 7, pl_file(path, "pl", 8, ".jpg")));
    assert(!timer_fn && !tmp_files());
    /* A read that lands tells only the playlists whose songs it changed: 8's, not 7's. */
    int told = pl_readies;
    pl_song(8, 150, "PL B");
    playlist_art_refresh();
    run();
    assert(pl_readies == told + 1 && pl_ready[told % 16] == 8 && playlist_songs(8) == 3 && playlist_songs(7) == 4);
    assert(!playlist_art(8, "Bad one", 0, url, sizeof url));
    run();
    assert(playlist_art(8, "Bad one", 0, url, sizeof url) && !strcmp(url + 7, pl_file(path, "pl", 8, ".jpg")));

    /* Songs changed mid-build: the finished art keeps the old songs' name, which is never shown for
       the new ones; the next ask builds theirs. The covers are built here (none cached). */
    pl_song(9, 107, "PL D"), pl_song(9, 108, "PL E");
    pl_refresh();
    pl_file(old, "pl", 9, ".jpg");
    calls = 0, block_at = 0;
    assert(!playlist_art(9, "Nine", 0, url, sizeof url));
    wait_blocked();
    pl_song(9, 109, "PL F");
    playlist_art_refresh(); /* the list rebuilt meanwhile: the old songs' build never names its art */
    blocked = 0, block_at = -1;
    run();
    assert(content(old, "tile") && access(pl_file(path, "pl", 9, ".jpg"), 0) && !tmp_files());
    assert(tile_args() == 4 && strstr(ff_in[0], ".tmp.jpg") && !strcmp(ff_in[0], ff_in[3]) && !strcmp(ff_in[1], ff_in[2]));
    assert(!playlist_art(9, "Nine", 0, url, sizeof url));
    run();
    assert(playlist_art(9, "Nine", 0, url, sizeof url) && !strcmp(url + 7, path) && content(path, "tile"));

    /* A finished build hands the UI the songs it read, their hash and count, with no read of the
       table on the UI thread: songs added after the last refresh show their art and count. */
    pl_song(11, 115, "PL A"), pl_song(11, 116, "PL C");
    pl_refresh();
    pl_song(11, 117, "PL B");
    int scans = pl_scans;
    assert(!playlist_art(11, "Eleven", 0, url, sizeof url));
    run();
    assert(pl_scans == scans && playlist_songs(11) == 3);
    assert(playlist_art(11, "Eleven", 0, url, sizeof url) && !strcmp(url + 7, pl_file(path, "pl", 11, ".jpg")));

    /* The card full, or gone mid-build: nothing named, the worker ends, the next ask builds. */
    pl_song(10, 110, "PL G"), pl_song(10, 111, "PL H");
    pl_refresh();
    int readies = pl_readies;
    free_blocks = 0;
    assert(!playlist_art(10, "Ten", 0, url, sizeof url));
    run();
    free_blocks = 1 << 20;
    assert(access(pl_file(path, "pl", 10, ".jpg"), 0) && pl_readies == readies && !tmp_files());
    calls = 0, block_at = 0;
    assert(!playlist_art(10, "Ten", 0, url, sizeof url));
    wait_blocked();
    assert(!rename(PEQ_ROOT "/mnt/mmc", PEQ_ROOT "/mnt/gone"));
    blocked = 0, block_at = -1;
    run();
    assert(calls == 1 && pl_readies == readies); /* stopped at the card: no second album, no small one */
    assert(!rename(PEQ_ROOT "/mnt/gone", PEQ_ROOT "/mnt/mmc"));
    /* the card pulled in album_art leaves its note, which would strike that album, as Cover Flow's */
    assert(!unlink(PEQ_ROOT "/mnt/mmc/.coverflow/pl-building"));
    assert(access(path, 0) && !tmp_files());
    assert(!playlist_art(10, "Ten", 0, url, sizeof url));
    run();
    assert(playlist_art(10, "Ten", 0, url, sizeof url) && content(path, "tile"));
    /* A refresh after a song change: the new songs' hash, its art still to build. */
    pl_song(10, 112, "PL I");
    assert(playlist_art(10, "Ten", 0, url, sizeof url) && !strcmp(url + 7, path)); /* until a refresh */
    pl_refresh();
    assert(!playlist_art(10, "Ten", 0, url, sizeof url));
    run();
    assert(playlist_art(10, "Ten", 0, url, sizeof url) && !strcmp(url + 7, pl_file(path, "pl", 10, ".jpg")));
    /* Stopped (the page closed) mid-build: back after the one cover being built, nothing named,
       no marker. While it builds, each tick holds off Standby and auto power-off, so the card is
       not unmounted under it. */
    pl_song(12, 113, "PL J"), pl_song(12, 114, "PL K");
    pl_refresh();
    calls = 0, block_at = 0;
    assert(!playlist_art(12, "Twelve", 0, url, sizeof url));
    wait_blocked();
    int awake = pl_awake, (*tick)(const void *) = timer_fn;
    timer_fn = 0, tick(0);
    assert(pl_awake == awake + 1 && timer_fn);
    pthread_t releaser;
    pthread_create(&releaser, 0, release, 0);
    readies = pl_readies;
    playlist_art_stop();
    pthread_join(releaser, 0);
    block_at = -1;
    assert(calls == 1 && pl_readies == readies && !timer_fn);
    assert(access(pl_file(path, "pl", 12, ".jpg"), 0) && !tmp_files());
    playlist_art_stop();

    /* Albums Cover Flow knows have no art (its empty marker) are not tried again, though their
       songs would give some now: twenty of them are no asks, and the playlist is marked. */
    static char albums[20][16];
    for (int i = 0; i < 20; ++i) {
        snprintf(albums[i], 16, "PL R%02d", i);
        pl_song(13, 120 + i, albums[i]);
        snprintf(path, sizeof path, PEQ_ROOT "/mnt/mmc/.coverflow/%08x.jpg", tags_key("Artist", albums[i]));
        fclose(fopen(path, "w"));
    }
    pl_refresh();
    int asks = album_asks;
    assert(!playlist_art(13, "Thirteen", 1, url, sizeof url));
    run();
    assert(album_asks == asks && content(pl_file(path, "pl", 13, ".jpg"), "") && !tmp_files());
    assert(!playlist_art(13, "Thirteen", 1, url, sizeof url) && !timer_fn); /* marked: none, not queued */

    /* A 2x2 whose ffmpeg fails, run to its own exit: marked as none, not built again at every ask. */
    pl_song(66, 140, "PL A"), pl_song(66, 141, "PL C");
    pl_refresh();
    assert(!playlist_art(66, "Sixty-six", 1, url, sizeof url));
    run();
    assert(tile_args() == 4 && content(pl_file(path, "pl", 66, ".jpg"), "") && !tmp_files());
    assert(!playlist_art(66, "Sixty-six", 1, url, sizeof url) && !timer_fn);

    /* A hundred playlists listed at once: every ask answers at once, placeholders
       while one worker builds a job at a time (the first holds its first cover meanwhile, and no
       other cover starts); each playlist queued once however often it is asked (twice each here:
       128 jobs would hold only 64 playlists), and each fills in. */
    pl_n = 0;
    playlist_art_ready = heard;
    for (int cd = 0; cd < 100; ++cd) pl_song(cd, 1000 + cd, "PL Many");
    pl_refresh();
    calls = 0, block_at = 0;
    for (int cd = 0; cd < 100; ++cd)
        for (int twice = 0; twice < 2; ++twice) {
            snprintf(name, sizeof name, "Many %d", cd);
            assert(!playlist_art(cd, name, 1, url, sizeof url));
        }
    wait_blocked();
    usleep(20000);
    assert(calls == 1 && timer_fn);
    for (int cd = 0; cd < 100; ++cd) assert(!pl_heard[cd]);
    blocked = 0, block_at = -1;
    run();
    for (int cd = 0; cd < 100; ++cd) {
        snprintf(name, sizeof name, "Many %d", cd);
        assert(pl_heard[cd] == 1 && playlist_art(cd, name, 1, url, sizeof url) &&
               !strcmp(url + 7, pl_file(path, "pl", cd, "_s.jpg")));
    }
    assert(!timer_fn && !tmp_files());
    playlist_art_ready = 0;
    embedded_fails = fails;
}

/* The 2x2's album builds under a synced note of their own (pl-building), so an album whose cover
   kills or hangs demo is struck at the worker's next start, not tried again at every open. */
#define PL_NOTE PEQ_ROOT "/mnt/mmc/.coverflow/pl-building"
char *art_path(char *out, unsigned key, const char *suffix);
void coverflow_clean_end(void);
static void pl_write_note(unsigned key, const char *track) {
    FILE *f = fopen(PL_NOTE, "wb");
    fwrite(&key, 4, 1, f), fputs(track, f), fclose(f);
}
static void playlist_art_crash(void) {
    char url[700], path[600], note[700], log[4096];
    int fails = embedded_fails, n;
    embedded_fails = 0;
    pl_n = 0;
    pl_song(20, 2001, "PL Crash");
    unsigned key = tags_key("Artist", "PL Crash");
    pl_refresh();
    calls = 0, block_at = 0;
    assert(!playlist_art(20, "Twenty", 0, url, sizeof url));
    wait_blocked(); /* in album_art: the note holds the album's key and track */
    FILE *f = fopen(PL_NOTE, "rb");
    n = f ? (int)fread(note, 1, sizeof note, f) : 0;
    if (f) fclose(f);
    assert(n == 4 + (int)strlen(pl_urls[0]) && !memcmp(note, &key, 4) && !memcmp(note + 4, pl_urls[0], n - 4));
    blocked = 0, block_at = -1;
    run();
    assert(access(PL_NOTE, 0) && !access(pl_file(path, "pl", 20, ".jpg"), 0) && !tmp_files());

    /* A note a build left (demo died in it): the next start strikes its album, Cover Flow's empty
       marker and a crash-log line, and never asks for its art. */
    pl_song(21, 2002, "PL Struck");
    pl_refresh();
    pl_write_note(tags_key("Artist", "PL Struck"), pl_urls[1]);
    unlink(PEQ_ROOT "/mnt/mmc/q2-crash.log");
    int asks = album_asks;
    assert(!playlist_art(21, "Twenty-one", 0, url, sizeof url));
    run();
    snprintf(log, sizeof log, "== cover flow skipped\n%s\n", pl_urls[1]);
    assert(album_asks == asks && access(PL_NOTE, 0) && content(PEQ_ROOT "/mnt/mmc/q2-crash.log", log));
    assert(content(art_path(path, tags_key("Artist", "PL Struck"), ""), ""));
    assert(content(pl_file(path, "pl", 21, ".jpg"), "")); /* its one album known to have none */

    /* A note whose album's art is there leaves the art alone. */
    art_path(path, key, "");
    fputs("art", f = fopen(path, "w")), fclose(f);
    pl_write_note(key, pl_urls[0]);
    pl_song(22, 2003, "PL Crash");
    pl_refresh();
    assert(!playlist_art(22, "Twenty-two", 0, url, sizeof url));
    run();
    assert(content(path, "art") && access(PL_NOTE, 0));

    /* A clean end (power-off) mid-build stops the worker before its next cover: no note of the next
       album's outlives demo for the next start to strike, the one in hand's goes, nothing is named. */
    pl_song(23, 2004, "PL End A"), pl_song(23, 2005, "PL End B");
    pl_refresh();
    calls = 0, block_at = 0, asks = album_asks;
    assert(!playlist_art(23, "Twenty-three", 0, url, sizeof url));
    wait_blocked(); /* in the first album's album_art, under its note */
    coverflow_clean_end();
    assert(access(PL_NOTE, 0));
    blocked = 0, block_at = -1;
    run();
    assert(album_asks == asks + 1 && access(PL_NOTE, 0) && !tmp_files());
    assert(access(pl_file(path, "pl", 23, ".jpg"), 0));

    /* A clean end with no build running is no album's fault either: the note goes. */
    pl_write_note(key, pl_urls[0]);
    coverflow_clean_end();
    assert(access(PL_NOTE, 0) && !pl_ui_opens);
    playlist_art_stop(); /* its cancel dropped, for the tests after */
    embedded_fails = fails;
}

/* Each ffmpeg run so far (the stub counts them). */
static int ff_runs(void) {
    int n = 0;
    FILE *f = fopen(ART_FFMPEG ".runs", "r");
    for (int c; f && (c = fgetc(f)) != EOF;) n += c == '\n';
    if (f) fclose(f);
    return n;
}
/* playlist cd's files of kind, as a glob's count, and whether the last one is empty */
static int pl_files(const char *kind, int cd, int *empty) {
    char want[32], path[600];
    int n = 0;
    snprintf(want, sizeof want, "%s_%d_", kind, cd);
    DIR *d = opendir(PEQ_ROOT "/mnt/mmc/.coverflow");
    for (struct dirent *e; d && (e = readdir(d));) {
        if (strncmp(e->d_name, want, strlen(want)) || strstr(e->d_name, "_s.jpg")) continue;
        snprintf(path, sizeof path, PEQ_ROOT "/mnt/mmc/.coverflow/%s", e->d_name);
        ++n, *empty = content(path, "");
    }
    if (d) closedir(d);
    return n;
}
/* The user's own picture is decoded up to 12 MP (PL_PICTURE_PX) by its header: 4080 x 3072 (a Pixel's
   12 MP photo) runs ffmpeg, 4081 x 3072 is marked at once with no run and its 2x2 shows. One ffmpeg won't decode is
   marked too; one whose ffmpeg is killed (Big.png: memory short just then) is tried again at the
   next ask, never marked. */
static void playlist_art_kills(void) {
    char url[700], path[600];
    int empty = 0, runs, fails = embedded_fails;
    embedded_fails = 0;
    pl_n = 0;
    pl_song(120, 2101, "PL A"); /* its Cover Flow art cached: a 2x2 costs no run */
    pl_song(121, 2102, "PL A"), pl_song(122, 2103, "PL A"), pl_song(123, 2104, "PL A");
    pl_refresh();
    static const struct { int cd, w, h, decodes, runs, shown; const char *name; } cap[] = {
        { 121, 4080, 3072, 1, 1, 1, "Edge" }, { 122, 4081, 3072, 1, 0, 0, "Over" }, { 123, 3000, 4000, 0, 1, 0, "Junk" } };
    for (int i = 0; i < 3; ++i) {
        snprintf(path, sizeof path, PEQ_ROOT "/mnt/mmc/_explaylist_data/%s.jpg", cap[i].name);
        pl_picture(path, cap[i].w, cap[i].h, cap[i].decodes);
        runs = ff_runs();
        assert(!playlist_art(cap[i].cd, cap[i].name, 0, url, sizeof url));
        run();
        assert(ff_runs() == runs + cap[i].runs && pl_files("plc", cap[i].cd, &empty) == 1 && empty == !cap[i].shown);
        assert(playlist_art(cap[i].cd, cap[i].name, 0, url, sizeof url) && strstr(url, cap[i].shown ? "/plc_" : "/pl_"));
        unlink(path);
    }
    pl_picture(PEQ_ROOT "/mnt/mmc/_explaylist_data/Big.png", 4000, 3000, 1);
    for (int i = 0; i < 2; ++i) {
        runs = ff_runs();
        assert(!playlist_art(120, "Big", 0, url, sizeof url));
        run();
        assert(ff_runs() == runs + 1 && !pl_files("plc", 120, &empty) && !pl_files("pl", 120, &empty));
    }
    assert(!tmp_files());
    unlink(PEQ_ROOT "/mnt/mmc/_explaylist_data/Big.png");
    embedded_fails = fails;
}

/* The 2x2 is the first four albums with art in song id order, one Cover Flow found none for passed
   over; songs added after those four leave its covers as they were, in a file of the new songs'. */
static void playlist_art_order(void) {
    static const char *const names[] = { "PO A", "PO None", "PO B", "PO C", "PO D", "PO E", "PO F" };
    char url[700], path[600];
    int fails = embedded_fails;
    embedded_fails = 0;
    pl_n = 0;
    for (int i = 0; i < 7; ++i) {
        FILE *f = fopen(art_path(path, tags_key("Artist", names[i]), ""), "w");
        if (i != 1) fputs(names[i], f);
        fclose(f);
    }
    for (int i = 0; i < 6; ++i) pl_song(32, 3200 + i, names[i]);
    pl_refresh();
    for (int pass = 0; pass < 2; ++pass) {
        assert(!playlist_art(32, "Order", 0, url, sizeof url));
        run();
        assert(tile_args() == 4);
        for (int i = 0; i < 4; ++i) assert(!strcmp(ff_in[i], art_path(path, tags_key("Artist", names[i ? i + 1 : 0]), "")));
        assert(playlist_art(32, "Order", 0, url, sizeof url) && !strcmp(url + 7, pl_file(path, "pl", 32, ".jpg")));
        pl_song(32, 3210, names[6]), pl_song(32, 3211, names[3]); /* added after the four */
        pl_refresh();
    }
    embedded_fails = fails;
}

/* The messy library's (messy_on) playlist art: every bound holds, counted, never timed. */
static int runs_of(const char *what) { /* ffmpeg runs whose input names what */
    char line[1000];
    int n = 0;
    FILE *f = fopen(ART_FFMPEG ".runs", "r");
    while (f && fgets(line, sizeof line, f)) n += !!strstr(line, what);
    if (f) fclose(f);
    return n;
}
static const char *messy_name(int cd, const char *l255) { /* the containers' names */
    return cd == 1 ? "Mix" : cd == 2 ? "Road Trip" : cd == 3 ? "\xc3\x9c" "n\xc3\xaf" "c\xc3\xb8" "d\xc3\xa9/"
         : cd == 4 ? l255 : cd == 5 ? "Big" : "Playlist";
}
static void playlist_art_messy(void) {
    static const unsigned char png[24] = { 0x89, 'P', 'N', 'G', '\r', '\n', 0x1a, '\n', 0, 0, 0, 13,
                                           'I', 'H', 'D', 'R', 0, 0, 0x1f, 0x40, 0, 0, 0x1f, 0x40 };
    static const unsigned char sof2[32] = { 0xff, 0xd8, 0xff, 0xc2, 0, 17, 8, 0x0b, 0xb8, 0x0f, 0xa0, 3 };
    char url[700], path[1100], name[300];
    int fails = embedded_fails, empty = 0;
    embedded_fails = 1, free_blocks = 1 << 20; /* no embedded art: a build reaches its folder's cover, then ffmpeg */
    playlist_art_stop(); /* the covers' last worker: the shim's one timer slot lost its poll */
    messy_on();
    /* The card: cd 3's twenty live songs, each beside a cover.jpg; Big.png (8000 x 8000) for cd 5,
       Road Trip.jpg (progressive, 4000 x 3000) for cd 2; ten of cd 2's albums marked by Cover Flow as
       having none, and a hundred with art. No playlist's art yet. */
    for (int i = 0; i < MESSY_LIVE; ++i) {
        snprintf(path, sizeof path, PEQ_ROOT "/mnt/mmc/L/%d", i), mkdir(PEQ_ROOT "/mnt/mmc/L", 0755), mkdir(path, 0755);
        snprintf(path, sizeof path, PEQ_ROOT "/mnt/mmc/L/%d/t.flac", i), fclose(fopen(path, "w"));
        snprintf(path, sizeof path, PEQ_ROOT "/mnt/mmc/L/%d/cover.jpg", i), fclose(fopen(path, "w"));
    }
    FILE *f = fopen(PEQ_ROOT "/mnt/mmc/_explaylist_data/Big.png", "wb");
    fwrite(png, 1, sizeof png, f), fclose(f);
    f = fopen(PEQ_ROOT "/mnt/mmc/_explaylist_data/Road Trip.jpg", "wb");
    fwrite(sof2, 1, sizeof sof2, f), fclose(f);
    static char tag[4200];
    memset(tag, 'T', 4096);
    for (int i = 0; i < 500; i += i == 9 ? 391 : 1) { /* albums 0-9 marked none, 400-499 with art */
        snprintf(tag + 4096, 16, "%d", i);
        art_path(path, tags_key("Artist", tag), "");
        f = fopen(path, "w");
        if (i >= 400) fputs("art", f);
        fclose(f);
    }
    DIR *d = opendir(PEQ_ROOT "/mnt/mmc/.coverflow");
    for (struct dirent *e; d && (e = readdir(d));)
        if (!strncmp(e->d_name, "pl", 2) && strchr(e->d_name, '_'))
            snprintf(path, sizeof path, PEQ_ROOT "/mnt/mmc/.coverflow/%s", e->d_name), unlink(path);
    if (d) closedir(d);
    /* A Playlists rebuild of 101 rows: the refresh asks the worker, each row asks its art. No SQLite
       on the UI thread, at most two files opened per row (file_size), whatever the table holds. */
    int opens = pl_ui_opens, execs = messy_ui_execs, fopens = ui_fopens, runs = ff_runs();
    int gone0 = runs_of("/Gone/"), big0 = runs_of("Big.png"), road0 = runs_of("Road Trip.jpg");
    struct timespec t0, t1;
    playlist_art_ready = 0;
    playlist_art_refresh();
    clock_gettime(CLOCK_MONOTONIC, &t0);
    memset(name, 'L', 255), name[255] = 0; /* cd 4's: 255 characters */
    for (int cd = 0; cd <= 100; ++cd) playlist_art(cd, messy_name(cd, name), 1, url, sizeof url);
    clock_gettime(CLOCK_MONOTONIC, &t1);
    assert(pl_ui_opens == opens && messy_ui_execs == execs && ui_fopens - fopens <= 2 * 101);
    printf("playlist art, messy library: a 101-row rebuild asks %.3f ms a row on the UI thread (%d files opened)\n",
           ((t1.tv_sec - t0.tv_sec) * 1e3 + (t1.tv_nsec - t0.tv_nsec) / 1e6) / 101, ui_fopens - fopens);
    fflush(stdout);
    run(); /* the worker's read, then the first build: 101 jobs */
    assert(pl_page_most == 1024); /* a page of rows a statement: a writer waits for one, never the table */
    assert(playlist_songs(1) == 20000 && playlist_songs(2) == 5000 && playlist_songs(3) == 300 && playlist_songs(5) == 320);
    assert(playlist_songs(0) == 300); /* Q2 Muse's first playlist is cd 0 */
    assert(!playlist_songs(-1) && !playlist_songs(999)); /* junk: no playlist's */
    int forks = ff_runs() - runs;
    printf("playlist art, messy library: %d rows; first build of 101 playlists forked %d ffmpeg runs, %d on files gone\n",
           MESSY_ROWS, forks, runs_of("/Gone/") - gone0);
    fflush(stdout);
    assert(runs_of("/Gone/") == gone0 && runs_of("/mnt/mmc/S/") + runs_of("/mnt/mmc/P/") + runs_of("/mnt/mmc/B/") == 0);
    /* Road Trip's picture (12 MP: Big.png's 64 MP is never run), cd 2's 2x2 (its albums with art)
       and cd 3's (its live songs' covers) */
    assert(runs_of("Big.png") == big0 && runs_of("Road Trip.jpg") == road0 + 1 && forks == 3);
    assert(pl_files("pl", 3, &empty) == 1 && !empty); /* cd 3's twenty live songs: their 2x2, not a marker */
    assert(pl_files("pl", 1, &empty) == 1 && empty);  /* 20,000 albums, every file gone: marked, no run */
    assert(pl_files("pl", 2, &empty) == 1 && !empty && pl_files("plc", 2, &empty) == 1 && empty); /* the picture won't decode */
    assert(pl_files("plc", 5, &empty) == 1 && empty && pl_files("pl", 5, &empty) == 1 && empty); /* over 12 MP: marked */
    assert(!pl_ui_opens && !messy_ui_execs && !tmp_files() && access(PL_NOTE, 0));
    /* The page shown again: every row asks, the art there now answers at once, nothing built again. */
    runs = ff_runs(), fopens = ui_fopens;
    int reads = messy_reads;
    playlist_art_refresh();
    for (int cd = 0; cd <= 100; ++cd) playlist_art(cd, messy_name(cd, name), 1, url, sizeof url);
    assert(ui_fopens - fopens <= 2 * 101);
    run();
    assert(messy_reads == reads && playlist_songs(0) == 300); /* cd 0 kept by the refresh: no build */
    assert(ff_runs() == runs && runs_of("Big.png") == big0);
    assert(!pl_ui_opens && !messy_ui_execs && !tmp_files());
    /* Return mid-build waits for the cover in hand at most: none after it, nothing named or marked. */
    d = opendir(PEQ_ROOT "/mnt/mmc/.coverflow");
    for (struct dirent *e; d && (e = readdir(d));)
        if (!strncmp(e->d_name, "pl_3_", 5)) snprintf(path, sizeof path, PEQ_ROOT "/mnt/mmc/.coverflow/%s", e->d_name), unlink(path);
    if (d) closedir(d);
    calls = 0, block_at = 0, joining = 0;
    assert(!playlist_art(3, "Gone", 1, url, sizeof url));
    wait_blocked();
    pthread_t releaser;
    pthread_create(&releaser, 0, release, 0);
    playlist_art_stop();
    pthread_join(releaser, 0);
    block_at = -1;
    assert(calls == 1 && !pl_files("pl", 3, &empty) && !tmp_files() && access(PL_NOTE, 0));
    messy = 0;
    unlink(PEQ_ROOT "/mnt/mmc/_explaylist_data/Big.png"), unlink(PEQ_ROOT "/mnt/mmc/_explaylist_data/Road Trip.jpg");
    embedded_fails = fails;
}

/* Songs whose files are gone (deleted over USB, a bad m3u line: stock's rescan leaves playlist rows)
   cost no ffmpeg run and none of the 16 tries: forty of them ahead of the one album still there
   still give its art. */
static void playlist_art_gone(void) {
    char url[700], dir[600];
    int empty = 1, fails = embedded_fails, runs = ff_runs();
    embedded_fails = 1; /* no embedded art: each album's build reaches ffmpeg */
    pl_n = 0;
    for (int i = 0; i < 40; ++i) {
        snprintf(pl_urls[pl_n], 600, PEQ_ROOT "/music/Gone %d/1.flac", i);
        pl_rows[pl_n].cd = 121, pl_rows[pl_n].id = 2200 + i, pl_rows[pl_n].url = pl_urls[pl_n];
        pl_rows[pl_n].album = "Gone", pl_rows[pl_n].artist = pl_urls[pl_n], pl_rows[pl_n++].aa = "";
    }
    pl_song(121, 2300, "PL Live");
    snprintf(dir, sizeof dir, PEQ_ROOT "/music/PL Live/cover.jpg");
    fclose(fopen(dir, "w"));
    pl_refresh();
    assert(!playlist_art(121, "Gone", 0, url, sizeof url));
    run();
    assert(ff_runs() == runs && pl_files("pl", 121, &empty) == 1 && !empty && !tmp_files());
    embedded_fails = fails;
}

/* Refresh Cover Flow with Playlists open under Settings, mid-build: the build stops first (a join),
   so the 2x2 whose cached cover Refresh deletes is not tiled from a missing file and marked as
   none: no pl_ file, empty or not, and the next ask builds it. */
static void *release_soon(void *unused) { /* at the join, or after 200 ms when none comes */
    for (int i = 0; i < 200 && !joining; ++i) usleep(1000);
    blocked = 0;
    return 0;
}
static void playlist_art_refresh_cf(void) {
    extern void coverflow_refresh_art(void);
    char url[700], path[600];
    int empty = 0, fails = embedded_fails;
    embedded_fails = 0;
    pl_n = 0;
    pl_song(24, 2401, "PL Kept"), pl_song(24, 2402, "PL Built");
    FILE *f = fopen(art_path(path, tags_key("Artist", "PL Kept"), ""), "w");
    fputs("art", f), fclose(f);
    pl_refresh();
    calls = 0, block_at = 0, joining = 0;
    assert(!playlist_art(24, "Twenty-four", 0, url, sizeof url));
    wait_blocked(); /* "PL Built"'s album_art, "PL Kept"'s cached cover already picked */
    pthread_t releaser;
    pthread_create(&releaser, 0, release_soon, 0);
    coverflow_refresh_art();
    pthread_join(releaser, 0);
    block_at = -1;
    run();
    assert(!pl_files("pl", 24, &empty) && !tmp_files() && !timer_fn);
    assert(!playlist_art(24, "Twenty-four", 0, url, sizeof url));
    run();
    assert(pl_files("pl", 24, &empty) == 1 && !empty);
    embedded_fails = fails;
}

/* ---- Playlists as Cover Flow (coverflow_playlists, which playlists.c opens over the veiled
   Playlists page): the playlists as covers, no cards, their art playlist_art.c's. ---- */
int coverflow_playlists(void *lists), lettered(void *w), coverflow_bounded(void *w), coverflow_playlist_queue(void);
void *coverflow_album(void *page), *coverflow_tracks(void *page), *coverflow_playlist(void *page);
void *coverflow_playlists_close(void);
unsigned coverflow_scope(void *page);
static void *list_win;
static int list_cd = -1;
void playlist_open(void *win, int cd_num) { list_win = win, list_cd = cd_num; } /* playlists.c's */
static int pl_staged; /* each getMusicByPlayList */
int getMusicByPlayList(int cd) { /* a playlist's songs: 40's are records 0 and 1, 41's record 2, 42's 3 to 9 */
    deque *d = shim_dir;
    d->n = 0, ++pl_staged;
    if (messy) { /* the messy library's cd 3: 300 songs (cd 1's 20,000 would overflow the deque) */
        for (int i = 0; cd == 3 && i < 300; ++i) d->at[d->n++] = records[i % albums];
        return (int)d->n;
    }
    for (int i = cd == 41 ? 2 : cd == 42 ? 3 : 0; i < (cd == 40 ? 2 : cd == 41 ? 3 : cd == 42 ? 10 : 0); ++i) d->at[d->n++] = records[i];
    return (int)d->n;
}
static const char *second_caption(widget *covers_view) { /* the second label: the album's artist */
    for (int i = 0, k = 0; i < covers_view->nkids; ++i)
        if (!strcmp(w[covers_view->kids[i]].type, "hscroll_label") && k++) return w[covers_view->kids[i]].text;
    return "";
}
static unsigned saved_key(void) { /* the album key kept on the card to reopen on */
    unsigned key = 0;
    FILE *f = fopen(PEQ_ROOT "/mnt/mmc/.coverflow/album", "rb");
    if (f) assert(fread(&key, sizeof key, 1, f) == 1), fclose(f);
    return key;
}
static int rows_heard; /* the rows' own playlist_art_ready, under the covers' */
static void rows_hear(int cd_num) { rows_heard = cd_num; }
static void playlist_covers(void) {
    static char lists[2][0x60];
    static const cf_style_t shipped = CF_SHIPPED;
    const cf_style_t fixture = shim_themes[1].cf;
    char url[600];
    unsigned kept = saved_key(), was[4] = { alt, frame_fail, pressed, embedded_fails };
    embedded_fails = 0; /* the playlists' songs' art is their embedded art */
    pl_n = 0;
    pl_song(40, 2001, "PL CF A"), pl_song(40, 2002, "PL CF A"), pl_song(41, 2003, "PL CF B");
    deque *d = _create_deque(0);
    for (int i = 0; i < 2; ++i) {
        memset(lists[i], 0, 0x60);
        *(int *)(lists[i] + REC_ID) = *(int *)(lists[i] + REC_DISC) = 40 + i;
        *(const char **)(lists[i] + REC_TITLE) = i ? "List 1" : "List 0";
        d->at[d->n++] = lists[i];
    }
    /* Flat: two covers, no card; the name over its count; placeholders while the art builds, then
       the art, the page still open. */
    alt = 0, frame_fail = 1, pressed = 0;
    page = &w[nw + 1];
    playlist_art_refresh(); /* playlists.c's, as it opens them: the worker reads the songs */
    playlist_art_ready = rows_hear, rows_heard = 0; /* the rows', which the covers take over */
    assert(coverflow_playlists(d) && d->n == 2); /* the caller's list stays the caller's */
    widget *s = slide(), *view = &w[s->parent];
    assert(s && slide_count(s) == 2 && !lettered(s) && coverflow_bounded(s)); /* two covers, not A B A B A */
    assert(!strcmp(album_caption(view), "List 0") && !strcmp(second_caption(view), "0 songs")); /* not read yet */
    assert(!strcmp(w[s->kids[0]].image, PLACEHOLDER) && !strcmp(w[s->kids[1]].image, PLACEHOLDER) && timer_fn);
    run();
    assert(!strcmp(second_caption(view), "2 songs")); /* the read landed: the count named again */
    assert(!rows_heard); /* the hidden rows ask again as their page comes to the front */
    for (int i = 0; i < 2; ++i)
        assert(playlist_art(40 + i, "", 0, url, sizeof url) && !strcmp(w[s->kids[i]].image, url) && !strstr(url, "_s.jpg"));
    slide_menu_set_value(s, 1);
    changed_fn(0, 0);
    assert(!strcmp(album_caption(view), "List 1") && !strcmp(second_caption(view), "1 song"));
    /* a playlist with no name: an empty caption, not the one before */
    *(const char **)(lists[0] + REC_TITLE) = 0;
    slide_menu_set_value(s, 0);
    changed_fn(0, 0);
    assert(!strcmp(album_caption(view), "") && !strcmp(second_caption(view), "2 songs"));
    *(const char **)(lists[0] + REC_TITLE) = "List 0";
    slide_menu_set_value(s, 1);
    changed_fn(0, 0);
    /* No album hold or rating on a playlist; its hold is the playlist's, the record at the centre. */
    assert(!coverflow_album(page) && !coverflow_playlist(0));
    assert(coverflow_playlist(page) && *(int *)((char *)coverflow_playlist(page) + REC_DISC) == 41);
    /* Centre: the playlist opens from its row on the page under the covers, by its
       cd_num, and the covers stay, for Return from its list to come back to. */
    widget *under = make(0, "playlist_page");
    wm_stack[0] = under, wm_stack[1] = page, wm_n = 2, force_closes = 0;
    click_album(s, 1);
    run();
    assert(!force_closes && list_win == under && list_cd == 41);
    assert(slide() == s && s->visible && !strcmp(album_caption(view), "List 1"));
    /* The hold's New Playlist: the covers close, unanimated, for the Playlists
       page under them; with no covers up, nothing. */
    force_closes = 0;
    assert(coverflow_playlists_close() == under && force_closes == 1 && force_closed == page);
    close_page();
    assert(!playlist_art_ready); /* the rows take it again as their page is rebuilt */
    assert(!coverflow_playlists_close() && force_closes == 1 && !coverflow_playlist(page));
    page = &w[nw + 1];
    wm_stack[1] = page;
    assert(coverflow_playlists(d));
    /* Return on the covers: the Playlists page under them closes first, then the covers. */
    force_closes = 0, backs = 0;
    key(KEY_RETURN);
    assert(force_closes == 1 && force_closed == under && backs == 1);
    close_page();
    wm_n = 0;
    /* Depth: a cover whose art is still building shows the placeholder, and its art once made,
       decoded then, the page still open; a fast spin goes by covers, never letters. */
    pl_song(41, 2004, "PL CF C"); /* 41's songs changed: its art is built again */
    pl_refresh();
    alt = 1, frame_fail = 0;
    shim_themes[1].cf = shipped;
    page = &w[nw + 1];
    assert(coverflow_playlists(d));
    s = slide();
    top_window = page;
    int canvas[16] = { 0 }, before = loads;
    coverflow_paint(s, canvas);
    assert(loads == before + 1 && timer_fn); /* 40's art; 41's the placeholder */
    /* the builds stopped under the covers (USB Storage, Refresh Cover Flow): 41 stays on the
       placeholder, then is asked again as the covers come back to the front */
    playlist_art_stop();
    run();
    coverflow_paint(s, canvas);
    assert(loads == before + 1 && !timer_fn);
    foreground_fn(page, 0);
    run();
    coverflow_paint(s, canvas);
    run();
    coverflow_paint(s, canvas);
    assert(loads == before + 2); /* 41's, as it was made */
    th_pass(0, 17);
    th_down();
    for (int t = 0; t < 2500; t += 17) th_spin(150, 17);
    th_rest();
    /* The playlists stop at their ends (coverflow_bounded): the first push past kicks the covers out
       by a fifth of a stride, once, they spring back, and the thumb never wraps to the other end. */
    int *index = (int *)(s->raw + SLIDE_INDEX), n = slide_count(s);
    for (int dir = 1; dir >= -1; dir -= 2) {
        int end = dir > 0 ? n - 1 : 0;
        float past = 0;
        wheel_held = 1, wheel_travel = 0;
        th_pass(0, 50); th_pass(0, 50);
        for (int i = 0; i < 300; ++i) {
            th_pass(-dir * 3, 17);
            float e = (float)(end - *index);
            past = (th[T_POS] - e) * (float)dir > past ? (th[T_POS] - e) * (float)dir : past;
            assert(th[T_ON] ? th[T_GOAL] * (float)dir <= e * (float)dir : 1);
        }
        assert(past > .1f && past <= .21f);
        th_rest();
        assert(*index == end && !*(int *)(s->raw + SLIDE_OFFSET));
    }
    wheel_held = 0;
    close_page();
    top_window = 0;
    th_pass(0, 17);
    shim_themes[1].cf = fixture;
    assert(saved_key() == kept); /* a playlist is never the album Cover Flow reopens on */
    free(d);
    playlist_art_ready = 0;
    alt = was[0], frame_fail = was[1], pressed = was[2], embedded_fails = was[3];
}

/* More's Rename or Delete over the covers (stock's list, getAllPlayList, read again as they come
   back to the front): a deleted playlist's cover goes, a renamed one's caption follows, and the
   centre stays on its playlist; with none left the covers close, for the list under them. Nothing
   changed: the covers stay as they are. */
static char *pl_heads[4];
static int pl_nheads;
int getAllPlayList(void) {
    deque *d = shim_dir;
    d->n = 0;
    for (int i = 0; i < pl_nheads; ++i) d->at[d->n++] = pl_heads[i];
    return pl_nheads;
}
static void playlist_covers_back(void) {
    static char lists[3][0x60], renamed[0x60];
    unsigned was[2] = { alt, frame_fail };
    deque *d = _create_deque(0);
    for (int i = 0; i < 3; ++i) {
        memset(lists[i], 0, 0x60);
        *(int *)(lists[i] + REC_ID) = *(int *)(lists[i] + REC_DISC) = 50 + i;
        *(const char **)(lists[i] + REC_TITLE) = i == 0 ? "Back 0" : i == 1 ? "Back 1" : "Back 2";
        d->at[d->n++] = pl_heads[i] = lists[i];
    }
    pl_nheads = 3;
    alt = 0, frame_fail = 1;
    page = &w[nw + 1];
    foreground_fn = 0;
    assert(coverflow_playlists(d) && foreground_fn);
    widget *s = slide(), *view = &w[s->parent];
    slide_menu_set_value(s, 1);
    changed_fn(0, 0);
    foreground_fn(page, 0);
    run();
    assert(slide() == s && slide_count(s) == 3 && !strcmp(album_caption(view), "Back 1"));
    /* A song added to Back 1 from its list (Edit Playlist, Add to Playlist): its count follows as
       the covers come back, the art's worker reading the songs again. */
    assert(strcmp(second_caption(view), "1 song"));
    pl_song(51, 2101, "PL Back");
    playlist_art_stop(); /* the shim's one timer slot lost the running worker's poll */
    foreground_fn(page, 0);
    run();
    assert(slide() == s && playlist_songs(51) == 1 && !strcmp(second_caption(view), "1 song"));
    /* A failed read of the headers keeps the covers. */
    pl_nheads = -1, force_closes = 0;
    foreground_fn(page, 0);
    run();
    assert(!force_closes && slide() == s && slide_count(s) == 3 && !strcmp(album_caption(view), "Back 1"));
    pl_nheads = 3;
    /* A Centre queued as the covers come back after an edit (Back 2 deleted) wins, on its own
       playlist; the covers are read again on its way back. */
    widget *list_page = make(0, "playlist_page");
    wm_stack[0] = list_page, wm_stack[1] = page, wm_n = 2;
    list_win = 0, list_cd = -1, pl_nheads = 2;
    click_album(s, 1);
    int (*centre)(const void *) = timer_fn;
    foreground_fn(page, 0); /* the shim's one timer slot: the read's timer runs first */
    run();
    assert(slide() == s && slide_count(s) == 3);
    centre(0);
    assert(list_win == list_page && list_cd == 51);
    pl_nheads = 3, wm_n = 0;
    memcpy(renamed, lists[1], 0x60);
    *(const char **)(renamed + REC_TITLE) = "Renamed";
    pl_heads[0] = renamed, pl_heads[1] = lists[2], pl_nheads = 2; /* Back 0 deleted, Back 1 renamed */
    foreground_fn(page, 0);
    run();
    s = slide(), view = &w[s->parent];
    assert(slide_count(s) == 2 && *(int *)(s->raw + SLIDE_INDEX) == 0 && !strcmp(album_caption(view), "Renamed"));
    assert(*(int *)((char *)coverflow_playlist(page) + REC_DISC) == 51);
    pl_heads[0] = lists[2], pl_nheads = 1; /* the centre's own deleted: the cover at its place */
    foreground_fn(page, 0);
    run();
    s = slide(), view = &w[s->parent];
    assert(slide_count(s) == 1 && !strcmp(album_caption(view), "Back 2"));
    widget *under = make(0, "playlist_page");
    wm_stack[0] = under, wm_stack[1] = page, wm_n = 2, force_closes = 0;
    pl_nheads = 0;
    foreground_fn(page, 0);
    run();
    assert(force_closes == 1 && force_closed == page);
    close_page();
    wm_n = 0;
    free(d);
    playlist_art_ready = 0;
    alt = was[0], frame_fail = was[1];
}

/* ---- A track's play and its resume after a reboot. ---- */
extern int album_only(void *, void *, int);
static int open_card(const char *name) { /* the card whose tracks list is titled name, left open */
    widget *s = slide();
    for (int i = 0; s && i < slide_count(s); ++i) {
        click_album(s, i);
        run();
        if (!strcmp(title(), name)) return 1;
        key(KEY_RETURN);
    }
    return 0;
}
static widget *row_playing(int k) { /* the newest row under the page that plays track k */
    for (int i = nw; i > page - w; --i)
        if (!strcmp(w[i].type, "list_item") && under(&w[i], page) && w[i].click && w[i].ctx == (void *)(long)k) return &w[i];
    return 0;
}
/* Stock's resume after a reboot (memeory_startplayer, then playback.c's stock_memory): getMusicByAlbum of the saved
   name, or of NULL with the Unknown flag, the saved track found by its path, then album_only to its album. */
static deque *resumed(const char *path, int *at) {
    getMusicByAlbum(shim_memory[0x60d] ? 0 : (const char *)shim_memory + 0x611);
    deque *d = calloc(1, sizeof *d);
    *d = *(deque *)shim_dir;
    int k = -1;
    for (unsigned i = 0; i < d->n; ++i)
        if (!strcmp(*(char **)((char *)d->at[i] + REC_PATH), path)) k = (int)i;
    assert(k >= 0);
    *at = album_only(d, d->at[k], k);
    return d;
}
static int has(const deque *d, const void *r) {
    for (unsigned i = 0; i < d->n; ++i)
        if (d->at[i] == r) return 1;
    return 0;
}
/* The Unknown Album card (x.flac and y.flac, untagged, in two folders) resumes whole, at the song
   that was playing. */
static void unknown_resume(void) {
    char *loose[2] = { untagged_song, untagged_more };
    static char where[2][600];
    for (int i = 0; i < 2; ++i) {
        char d[500];
        snprintf(d, sizeof d, PEQ_ROOT "/music/Loose %c", 'A' + i);
        mkdir(d, 0755);
        snprintf(where[i], 600, "%s/%c.flac", d, 'x' + i);
        fclose(fopen(where[i], "w"));
        memset(loose[i], 0, 0x60);
        *(int *)(loose[i] + REC_ID) = 60 + i;
        *(char **)(loose[i] + REC_PATH) = where[i];
    }
    unknown_row = unknown_songs = untagged_two = 1;
    rescan();
    open_page();
    assert(open_card("Unknown Album"));
    deque *t = coverflow_tracks(page);
    assert(t->n == 2 && t->at[0] == untagged_song && t->at[1] == untagged_more);
    memset(shim_memory, 0, sizeof shim_memory);
    widget *row = row_playing(1);
    assert(row);
    row->click(row->ctx, 0);
    assert(shim_memory[0x60d] == 1 && nav_ctx.cls == 0xff10 && nav_ctx.idx == 1);
    key(KEY_RETURN);
    close_page();
    int at;
    deque *q = resumed(where[1], &at);
    assert(q->n == 2 && has(q, untagged_song) && has(q, untagged_more) && q->at[at] == untagged_more);
    free(q);
    unknown_row = unknown_songs = untagged_two = 0;
    memset(untagged_song, 0, 0x60), memset(untagged_more, 0, 0x60);
    rescan();
}

/* ---- 6,000 albums through the pool: the slide_menu holds CF_POOL children whatever the library (one per album would
   pass the harness's 1,024-child cap), each standing for one album round the centre; the ends wrap; Centre opens the
   far album; the captions follow the index; and an open of the same albums once their art is known asks no file per
   album on the UI thread: a thread looks behind the covers, and an album it finds without art makes the next open a
   cold one, whose look runs on the open's own thread, Home up, and builds what it finds. ---- */
#define CF_POOL 8 /* coverflow.h's: the slide_menu's children with depth */
static void pool_library(void) {
    enum { BIG = 6000 };
    static char nm[BIG][32], keep_rec[64][0x60], keep_nm[64][64], keep_pa[64][600];
    char path[600];
    int keep = albums;
    unsigned room = free_blocks;
    free_blocks = 1 << 20; /* room for a build */
    assert(keep <= 64);
    memcpy(keep_rec, records, sizeof keep_rec), memcpy(keep_nm, names, sizeof keep_nm), memcpy(keep_pa, paths, sizeof keep_pa);
    albums = 0;
    for (int i = 0; i < BIG; ++i) {
        snprintf(nm[i], 32, "Pool %04d", i), album(nm[i], 0);
        fclose(fopen(art_file(path, nm[i]), "w")); /* its art looked for already: "none", no build */
    }
    for (int i = 4318; i <= 4324; ++i) { /* art round the album the covers reopen on */
        FILE *f = fopen(art_file(path, nm[i]), "w");
        fputs("art", f), fclose(f);
    }
    rescan();
    int made = nw, built = calls;
    open_page();
    widget *s = slide(), *covers_view = s ? &w[s->parent] : 0;
    assert(s && slide_count(s) == BIG && s->nkids == CF_POOL && calls == built);
    assert(nw - made < 64); /* the page, the covers' view, the pool and the captions: no widget per album */
    int *index = (int *)(s->raw + SLIDE_INDEX);
    /* Each child stands for one album round the centre, the window wrapping at the ends. */
    const int at[] = { 0, 1, 2999, BIG - 2, BIG - 1 };
    for (unsigned t = 0; t < sizeof at / sizeof *at; ++t) {
        *index = at[t];
        int seen[CF_POOL] = { 0 };
        for (int j = 0; j < s->nkids; ++j) {
            int a = slide_child_index(s, j), d = ((a - at[t]) % BIG + BIG) % BIG;
            d = d > BIG / 2 ? d - BIG : d;
            assert(d >= -(CF_POOL - 1) / 2 && d <= CF_POOL / 2 && slide_child(s, a) == &w[s->kids[j]]);
            int *k = &seen[d + (CF_POOL - 1) / 2];
            assert(!*k);
            *k = 1;
        }
    }
    /* The captions name the index's album; a step past the last wraps to the first. */
    slide_menu_set_value(s, BIG - 1), changed_fn(0, 0); /* the value-changed event stock sends */
    assert(!strcmp(album_caption(covers_view), nm[BIG - 1]));
    carousel_glide(s, -CF_STRIDE, 0, 0), changed_fn(0, 0);
    assert(*index == 0 && !strcmp(album_caption(covers_view), nm[0]));
    carousel_glide(s, CF_STRIDE, 0, 0), changed_fn(0, 0);
    assert(*index == BIG - 1);
    /* Centre on an album far into the library: its tracks. */
    click_album(s, 4321);
    run();
    assert(!strcmp(title(), nm[4321]));
    key(KEY_RETURN);
    slide_menu_set_value(s, 4321), changed_fn(0, 0); /* the one kept */
    close_page();
    /* Warm: the same albums, their art known. The covers open on the saved album with no file asked for per album on
       the UI thread; the look over every album's art runs behind them. */
    int ui = ui_access, all = all_access;
    made = nw;
    open_page();
    s = slide(), index = (int *)(s->raw + SLIDE_INDEX);
    assert(s && *index == 4321 && s->nkids == CF_POOL && nw - made < 64);
    assert(ui_access - ui < 16);
    /* Its first frame decodes the centre cover alone, the rest within the frame's budget in the frames after; once all
       are in, a step at rest decodes the one entering at once, as ever. */
    int canvas[16] = { 0 }, first;
    loads = unloads = 0, decode_ms = 6;
    coverflow_paint(s, canvas);
    first = loads;
    for (int k = 0; k < 8 && loads < 7; ++k) coverflow_paint(s, canvas);
    assert(first == 1 && loads == 7);
    slide_menu_set_value(s, 4322), changed_fn(0, 0);
    coverflow_paint(s, canvas);
    assert(loads == 7); /* album 4325 has no art: the placeholder, nothing decoded */
    slide_menu_set_value(s, 4321), changed_fn(0, 0);
    decode_ms = 0;
    for (int i = 0; i < 5000 && all_access - all < BIG; ++i) usleep(1000); /* the look's thread */
    assert(all_access - all >= BIG && ui_access - ui < 16);
    close_page(); /* joins it */
    /* An album whose art went is found behind the covers, and built at the next open. */
    unlink(art_file(path, nm[17]));
    all = all_access;
    open_page();
    assert(size(nm[17]) == -1); /* not this time */
    for (int i = 0, was = -1; i < 100 && all_access != was; ++i) was = all_access, usleep(50000); /* the look ends */
    close_page();
    ui = ui_access, all = all_access;
    open_page();
    assert(ui_access - ui < 16 && all_access - all >= 18); /* its look on the open's thread, to album 17 at least */
    assert(size(nm[17]) >= 0); /* built: its art, or the "none" marker */
    close_page();
    memcpy(records, keep_rec, sizeof keep_rec), memcpy(names, keep_nm, sizeof keep_nm), memcpy(paths, keep_pa, sizeof keep_pa);
    albums = keep;
    free_blocks = room;
    rescan();
}

int main(void) {
    main_thread = pthread_self();
    big_library = 1; /* every test of letters, years and half copies; off: unlettered_sorts, half_art */
    /* Titles come from tags, unchanged; missing tags retain filename/CUE fallbacks. */
    char track[0x60] = {0}, name[512];
    *(const char **)(track + REC_NAME) = "01 - File.flac";
    *(const char **)(track + REC_PATH) = "/music/01 - File.flac";
    *(const char **)(track + REC_TITLE) = "Tagged title.flac";
    assert(!strcmp(track_name(name, sizeof name, track), "Tagged title.flac"));
    *(const char **)(track + REC_TITLE) = "";
    assert(!strcmp(track_name(name, sizeof name, track), "01 - File"));
    *(const char **)(track + REC_TITLE) = 0;
    assert(!strcmp(track_name(name, sizeof name, track), "01 - File"));
    *(const char **)(track + REC_NAME) = "CUE title";
    assert(!strcmp(track_name(name, sizeof name, track), "CUE title"));
    /* The stock scan flags are raw addresses in the device ABI; map them here. */
    assert(mmap((void *)(SCAN_THREAD & ~4095), 4096, PROT_READ | PROT_WRITE,
                MAP_PRIVATE | MAP_ANONYMOUS | MAP_FIXED_NOREPLACE, -1, 0) != MAP_FAILED);
    deque staging = {0};
    shim_dir = &staging;
    frame_fail = 1; /* the flat fallback first: stock images on the slide_menu */
    mkdir(PEQ_ROOT, 0755); mkdir(PEQ_ROOT "/mnt", 0755); mkdir(PEQ_ROOT "/mnt/mmc", 0755); mkdir(PEQ_ROOT "/music", 0755);

    /* An empty library, or one being scanned, shows the message and builds nothing. */
    open_page();
    assert(!strcmp(title(), "Update Local Music first") && !slide() && queries == 1);
    /* Every list (message, progress, tracks) comes from list(): iPod rows are transparent, so its
       list_view paints black itself rather than the theme's light card. */
    int lists = 0;
    for (int i = nw; i > page - w; --i) if (!strcmp(w[i].type, "list_view")) { assert((unsigned)w[i].bg == 0xff000000u); ++lists; }
    assert(lists == 1);
    key(KEY_RETURN); close_page();
    album("Cover", "cover.jpg");
    *(volatile int *)SCAN_THREAD = 1;
    open_page();
    assert(!strcmp(title(), "Update Local Music first") && queries == 1 && !calls);
    close_page();
    *(volatile int *)SCAN_DONE = 1;

    /* With embedded art failing, cover.jpg beats folder.jpg; with no art an empty marker makes the
       placeholder show. Folder art takes only the parse lock. */
    char both[600];
    snprintf(both, sizeof(both), PEQ_ROOT "/music/Cover/folder.jpg");
    fclose(fopen(both, "w"));
    album("Folder", "folder.jpg");
    album("Embedded", 0);
    embedded_fails = 1;
    rescan();
    open_page();
    assert(calls == 2 && !strcmp(made[0], "cover.jpg") && !strcmp(made[1], "folder.jpg"));
    assert(size("Cover") == 9 && size("Folder") == 10 && size("Embedded") == 0 && !tmp_files());
    widget *s = slide();
    assert(s && slide_count(s) == 3 && !strncmp(w[s->kids[0]].image, "file://", 7));
    close_page();
    album("None", 0);
    rescan();
    open_page();
    assert(calls == 2 && size("None") == 0 && !tmp_files());
    s = slide(); /* Cover and Folder have art; Embedded and None the placeholder; nothing after them */
    int with_art = 0, bare = 0;
    for (int i = 0; i < 4; ++i) {
        if (!strncmp(w[s->kids[i]].image, "file://", 7)) ++with_art;
        else if (!strcmp(w[s->kids[i]].image, PLACEHOLDER)) ++bare;
    }
    assert(with_art == 2 && bare == 2 && slide_count(s) == 4);
    close_page();

    /* A later open builds only the albums with no cache file; the marker is not retried. */
    album("New", "cover.jpg");
    rescan();
    open_page();
    assert(calls == 3 && !strcmp(made[2], "cover.jpg") && size("New") == 9);
    close_page();
    /* Only a cold open (the albums queried, or art to build) touches /mnt/data/q2-cf-trace, and
       removes it when it ends; a warm one leaves even a stale one alone. */
    mkdir(PEQ_ROOT "/mnt/data", 0755);
    fclose(fopen(PEQ_ROOT "/mnt/data/q2-cf-trace", "w"));
    open_page();
    assert(calls == 3 && !access(PEQ_ROOT "/mnt/data/q2-cf-trace", 0));
    close_page();
    rescan();
    open_page();
    assert(calls == 3 && access(PEQ_ROOT "/mnt/data/q2-cf-trace", 0));
    close_page();

    /* Cancel mid-build (Return on the progress screen) keeps the finished thumbnails, leaves no
       .tmp, and shows the covers; the next open resumes with the rest. */
    album("A", "cover.jpg"); album("B", "cover.jpg"); album("C", "cover.jpg");
    rescan();
    block_at = calls + 1;
    widget home;
    memset(&home, 0, sizeof(home));
    coverflow_home(&home, 0);
    page = &w[nw + 1];
    home.click(home.ctx, 0);
    while (!blocked) usleep(1000);
    assert(!strncmp(title(), "Preparing artwork", 17));
    int e[16] = {0}; e[EVENT_KEY / 4] = KEY_RETURN;
    w[8190].click(0, e);
    pthread_t t;
    joining = 0;
    pthread_create(&t, 0, release, 0); /* finish the album in hand once the cancel is set */
    run();
    pthread_join(t, 0);
    assert(size("A") == 9 && size("B") == 9 && size("C") == -1 && !tmp_files() && slide() && slide()->visible);
    close_page();
    block_at = -1;
    open_page();
    assert(size("C") == 9 && !strcmp(made[calls - 1], "cover.jpg"));

    /* Display settings' Refresh Cover Flow (coverflow_refresh_art) clears the cache and, at the next
       open, queries the albums again and rebuilds every one. Stock's Library covers go too, with
       AWTK's cached bitmaps, as the same names are written again. */
    int before = calls, q = queries, d = drops;
    close_page();
    mkdir(PEQ_ROOT "/mnt/mmc/.sldp", 0755);
    fclose(fopen(PEQ_ROOT "/mnt/mmc/.sldp/x0.jpg", "w"));
    coverflow_refresh_art();
    assert(size("Cover") == -1 && size("None") == -1);
    assert(access(PEQ_ROOT "/mnt/mmc/.sldp/x0.jpg", F_OK) && drops == d + 1);
    open_page();
    run();
    assert(calls == before + albums - 2 && size("None") == 0 && size("Embedded") == 0 && size("Cover") == 9);
    assert(queries == q + 1); /* Refresh always queries again */
    close_page();

    /* The album list outlives the page: reopening queries nothing until a songtable writer runs,
       and a list queried while one runs is not kept. */
    q = queries;
    open_page(); assert(queries == q && slide()); close_page();
    rescan(); /* each writer's hook is checked on the MIPS build (test/patch.py) */
    open_page(); assert(queries == ++q && slide()); close_page();
    open_page(); assert(queries == q); close_page();
    during_write = mid_write; rescan(); during_write = 0;
    assert(queries == ++q);
    open_page(); assert(queries == ++q); close_page();

    /* Low free space on the card skips the build: placeholders, no thread. */
    album("Tight", "cover.jpg");
    rescan();
    free_blocks = 4000; /* under 16 MB of 4 KB blocks */
    before = calls;
    open_page();
    assert(calls == before && size("Tight") == -1 && slide() && !strcmp(w[slide()->kids[albums - 1]].image, PLACEHOLDER));
    close_page();
    assert(loads == unloads);
    depth();
    restyle();
    door_shelf();
    door_step();
    door_eye();
    door_follow();
    rest_pinned();
    thumb_momentum();
    pool_library();
    held_row();
    fold_clearance();
    fold_frames();
    enter_frames();
    edge_drawn();
    sorting();
    open_page();
    while (saved_sort() != 0) press_sort();
    close_page();
    unknown_card();
    same_name();
    slow_open();
    slow_sort();
    added_ties();
    caption_marquee();
    disc_folders();
    audiobooks();
    ffmpeg_art();
    player_cover();
    scoped_pages();
    cancel_build();
    unknown_resume();
    playlist_art_files();
    playlist_art_crash();
    playlist_art_kills();
    playlist_art_order();
    playlist_art_gone();
    playlist_covers();
    playlist_covers_back();
    playlist_art_messy();
    /* Home: the player's cover for its type, once the player has parsed the current track
       (g_lastcover_url is its path), else the track album's Coverflow thumbnail, else the
       placeholder; reloaded only when the track or the usable cover changes, and only while Home
       is the painted top window. Split shows the art itself in the right panel. */
    extern void coverflow_home_art(void *);
    /* The pan's texture is sharpened once, [-1,6,-1]/4 a side (home_sharpen), against its cubic
       B-spline's softness: rounded and clamped a channel, the ends their own neighbours; flat
       colour stays. */
    extern void home_sharpen(unsigned *, int, int);
    unsigned line[5] = { 0xff000000u, 0xffffffffu, 0xff000000u, 0xff515151u, 0xff204080u };
    home_sharpen(line, 5, 1);
    assert(line[0] == 0xff000000u && line[1] == 0xffffffffu && line[2] == 0xff000000u &&
           line[3] == 0xff726a5au && line[4] == 0xff143c8cu);
    unsigned flat[3] = { 0xff123456u, 0xff123456u, 0xff123456u };
    home_sharpen(flat, 3, 1); assert(flat[0] == 0xff123456u && flat[1] == 0xff123456u && flat[2] == 0xff123456u);
    assert(mmap((void *)(MCL_POS & ~4095), 4096, PROT_READ | PROT_WRITE,
                MAP_PRIVATE | MAP_ANONYMOUS | MAP_FIXED_NOREPLACE, -1, 0) != MAP_FAILED);
    deque queue = {0};
    shim_queue = &queue;
    home_art = make(0, "image");
    frame_fail = 1; /* no pan frame: covers show still (as a 16-bit or too long one), the image named */
    int *geo = (int *)W(home_art)->raw, asset[4] = { 0, 0, 375, 290 }, /* W_X, W_Y, W_W, W_H */
        split[4] = { HOME_SPLIT_W, 30, 375 - HOME_SPLIT_W, 290 }; /* from the 30 px bar's foot: Home's window is the screen */
    memcpy(geo, asset, sizeof asset);
    widget *win = make(0, "window");
    const char *art = W(home_art)->image, *player = "file://" PEQ_ROOT "/tmp/coverpic.jpg";
    mkdir(PEQ_ROOT "/tmp", 0755);
    FILE *f = fopen(PEQ_ROOT "/tmp/coverpic.jpg", "w"); fputs("jpg", f); fclose(f);
    coverflow_home(win, 0);
    assert(&w[insensitive] == W(home_art)); /* taps under a fitted cover still find the list */
    coverflow_home_art(page);
    assert(!*art);
    coverflow_home_art(win);
    assert(!strcmp(art, "default_album_home") && !memcmp(geo, split, sizeof split)); /* no size: the panel */
    queue.n = 2; queue.at[0] = records[0]; queue.at[1] = records[3]; /* "Cover" is cached, "None" is not */
    *(volatile int *)MCL_POS = 0;
    shim_covertype = 1;
    coverflow_home_art(win); /* the cover is still the previous track's */
    assert(strstr(art, "/mnt/mmc/.coverflow/") && size("Cover") > 0);
    snprintf(shim_lastcover, sizeof(shim_lastcover), "%s", paths[0]);
    timer_fn = 0;
    coverflow_home_art(win);
    assert(!strcmp(art, player));
    /* the player reuses the file for each track: its cached bitmap is dropped, not the last track's shown */
    assert(!strcmp(dropped, player));
    /* a paint drops its own invalidations: the whole screen repaints after it */
    assert(!wm_repaints && timer_fn && (timer_fn(0), wm_repaints == 1));
    /* Square, portrait and landscape still covers keep their proportions, just cover the panel and
       centre on it. */
    static const int covers[][4] = { { 160, 160, 290, 290 }, { 206, 290, 206, 290 },
                                     { 300, 200, 435, 290 }, { 100, 400, 188, 752 } };
    for (unsigned i = 0; i < 4; ++i) {
        art_w = covers[i][0], art_h = covers[i][1];
        *(volatile int *)MCL_POS = 1; coverflow_home_art(win); *(volatile int *)MCL_POS = 0; coverflow_home_art(win);
        int want[4] = { HOME_SPLIT_W + (375 - HOME_SPLIT_W - covers[i][2]) / 2, 30 + (290 - covers[i][3]) / 2,
                        covers[i][2], covers[i][3] };
        for (int k = 0; k < 4; ++k) assert(geo[k] == want[k]);
    }
    art_w = art_h = 160;
    /* The art's paint is clipped to the panel on screen (canvas origin at the art, window at y 0)
       from the background hook to the border hook; other widgets keep the clip. */
    extern void coverflow_home_clip(void *, void *, int);
    static char lcd[0x100]; /* the canvas's LCD */
    int canvas[32] = { geo[0], geo[1] }, full[4] = { 0, 0, 375, 320 }, want[4] = { HOME_SPLIT_W, 30, 375 - HOME_SPLIT_W, 290 };
    *(void **)((char *)canvas + CANVAS_LCD) = lcd;
    coverflow_home_clip(home_art, canvas, 1);
    assert(!memcmp(clip_rect, want, sizeof want));
    coverflow_home_clip(win, canvas, 0);
    assert(!memcmp(clip_rect, want, sizeof want));
    coverflow_home_clip(home_art, canvas, 0);
    assert(!memcmp(clip_rect, full, sizeof full));
    coverflow_home_clip(win, canvas, 1);
    assert(!memcmp(clip_rect, full, sizeof full));
    before = loads;
    coverflow_home_art(win);
    assert(loads == before);
    shim_covertype = 2; /* a folder image the player has not written: the cached thumbnail */
    coverflow_home_art(win);
    assert(strstr(art, "/mnt/mmc/.coverflow/"));
    *(volatile int *)MCL_POS = 1; shim_covertype = 1; /* next track, the old cover still in place */
    coverflow_home_art(win);
    assert(!strcmp(art, "default_album_home"));
    snprintf(shim_lastcover, sizeof(shim_lastcover), "%s", paths[3]);
    coverflow_home_art(win);
    assert(!strcmp(art, player) && loads == unloads);
    /* Issue #7: the next folder stock queues after a folder play carries no tags; with no player
       cover the art is the placeholder until the player's parsed tags name the cached album. */
    static char next[0x60];
    *(const char **)(next + REC_PATH) = "/mnt/mmc/Next/01.flac";
    queue.n = 3; queue.at[2] = next;
    *(volatile int *)MCL_POS = 2; shim_covertype = 3;
    snprintf(shim_lastcover, sizeof(shim_lastcover), "%s", "/mnt/mmc/Next/01.flac");
    coverflow_home_art(win);
    assert(!strcmp(art, "default_album_home"));
    snprintf(shim_id3, sizeof(shim_id3), "%s", "/mnt/mmc/Next/01.flac");
    snprintf(shim_id3 + ID3_ALBUM, 256, "%s", "Cover");
    snprintf(shim_id3 + ID3_ARTIST, 256, "%s", "Artist");
    coverflow_home_art(win);
    assert(strstr(art, "/mnt/mmc/.coverflow/") && loads == unloads);
    memset(shim_id3, 0, sizeof(shim_id3));
    queue.n = 2; *(volatile int *)MCL_POS = 1; shim_covertype = 1;
    snprintf(shim_lastcover, sizeof(shim_lastcover), "%s", paths[3]);
    coverflow_home_art(win);
    assert(!strcmp(art, player) && loads == unloads);
    /* Home layout from the asset's full-width list: Split narrows the list, its rows and their tap
       targets to HOME_SPLIT_W and shows the art; Full widens the list to the screen and its rows to the
       theme's home_full_row, and hides the art. Labels start at the theme's home_text_x and end
       home_label_end (Split's, Full's) short of the row. */
    extern void coverflow_home_layout(void);
    home_list = make(0, "list_view");
    widget *sv = make(home_list, "scroll_view"), *row = make(sv, "view"), *label = make(row, "hscroll_label"),
           *tap = make(row, "image"), *all[] = { home_list, sv, row, tap };
    for (int i = 0; i < 4; ++i) *(int *)(all[i]->raw + W_W) = 375;
    const theme_t *T = &shim_themes[0];
    home_mode = 1;
    coverflow_home(win, 0);
    for (int i = 0; i < 4; ++i) assert(*(int *)(all[i]->raw + W_W) == (i < 2 ? 375 : T->home_full_row));
    assert(*(int *)(label->raw + W_X) == T->home_text_x &&
           *(int *)(label->raw + W_W) == T->home_full_row - T->home_text_x - T->home_label_end[1] && !W(home_art)->visible);
    before = loads;
    coverflow_home_art(win); /* hidden: nothing loads */
    assert(loads == before);
    home_mode = 0;
    coverflow_home_layout();
    for (int i = 0; i < 4; ++i) assert(*(int *)(all[i]->raw + W_W) == HOME_SPLIT_W);
    assert(*(int *)(label->raw + W_W) == HOME_SPLIT_W - T->home_text_x - T->home_label_end[0] && W(home_art)->visible);
    coverflow_home_art(win);
    assert(loads == before + 2 && !strcmp(art, player)); /* slide_load tries it, then the still fill */
    home_mode = 1; /* Full again, then back: Split's art is the panel again, nothing left loaded */
    coverflow_home_layout();
    assert(!W(home_art)->visible);
    home_mode = 0;
    coverflow_home_layout();
    assert(*(int *)(label->raw + W_X) == T->home_text_x && !memcmp(geo, split, sizeof split) && loads == unloads);
    /* A 26 px bar: the art panel takes the 294 px client height (client_h). */
    stub_bar_h = 26;
    coverflow_home_layout();
    static const int tall[4] = { HOME_SPLIT_W, 26, 375 - HOME_SPLIT_W, 294 };
    assert(!memcmp(geo, tall, sizeof tall));
    stub_bar_h = 30;
    coverflow_home_layout();
    assert(!memcmp(geo, split, sizeof split));
    /* A still cover: the clip paints nothing over it. */
    fills = 0; coverflow_home_clip(home_art, canvas, 0);
    assert(!fills && !memcmp(geo, split, sizeof split));
    /* A cover that pans: the art's image is empty and fills the panel, the pan's frame drawn over it. */
    frame_fail = 0;
    *(volatile int *)MCL_POS = 0;
    coverflow_home_art(win);
    assert(!*art && !memcmp(geo, split, sizeof split) && loads == unloads);
    int drew = draws;
    coverflow_home_clip(home_art, canvas, 0);
    assert(draws == drew + 1);
    /* A transparent cover pans over the half's paper: Light Mode's (the scheme's), else black, dimmed in the vignette's rows. */
    clear_cover = 1;
    static const struct { int light; unsigned paper; } papers[] = {
        { 1, 0xff000000u | LIGHT_PAPER * 0x010101u }, { 0, 0xff000000u } };
    for (int i = 0; i < 2; ++i) {
        light = papers[i].light;
        coverflow_home_layout();
        coverflow_home_art(win);
        coverflow_home_clip(home_art, canvas, 0);
        /* the panel's top rows are under the vignette (vig_k): darker than the paper, the black one's unchanged; below it, the paper */
        assert(shown[0] <= papers[i].paper && (i || (shown[0] & 255) < (papers[i].paper & 255)) && shown[100 * CF_VIEW_W + 90] == papers[i].paper);
    }
    clear_cover = 0;

    embedded_first();
    playlist_art_refresh_cf(); /* last: Refresh empties the cache the tests above share */
    return 0;
}
"""


# Stands in for stock ffmpeg: "ffmpeg" in its output (the last argument) for a .webp source or a
# track under Ffok/, else a failure; a playlist's 2x2 (-filter_complex) writes "tile" and leaves its
# arguments, a line each, in ffmpeg.args, but playlist 66's fails, an exit of its own.
FFMPEG_STUB = r"""#!/bin/sh
for a; do [ "$prev" = -i ] && { src=$a; [ -e "$a" ] || gone=1; }; prev=$a; done
printf '%s\n' "$src" >> "$0.runs"
case " $* " in *" -filter_complex "*) printf '%s\n' "$@" > "$0.args"; case $a in */pl_66_*) exit 1;; esac; [ -z "$gone" ] || exit 1; printf tile > "$a"; exit 0;; esac
case $src in *.webp|*/Ffok/*) printf ffmpeg > "$a";; */Fkill/*|*/Big.png) kill -9 $$;;
  */_explaylist_data/*) grep -q '^picture$' "$src" && printf '%s' "${src##*/}" > "$a" || exit 1;; *) exit 1;; esac
"""


def main():
    import argparse, os
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--captures', type=pathlib.Path,
                    help='write the renderer\'s frames (rest, quarter, half, three-quarter turns, one album) as PNGs here')
    a = ap.parse_args()
    # shim_themes[0] stands in for the generated themes[]' one row, OLED's: its .home_* and page rows
    # must match the resolved theme.xml (0, unset in the shim, left out).
    from theme import ROLE_MACROS, builtins, validate, ThemeError
    oled = builtins()[0]
    shim = re.search(r'shim_themes\[2\] = \{(.*?)\} \};', TEST, re.S)[1].split('},\n')
    tight = re.sub(r'\s', '', shim[0])
    for field in ('home_top', 'home_full_row', 'home_text_x'):
        assert re.search(rf'\.{field}=(\w+)', tight) is None if not oled[field] else int(re.search(rf'\.{field}=(\w+)', tight)[1], 0) == oled[field], f'shim_themes drifted from OLED: {field}'
    assert f"home_label_end={{{oled['home_label_end[0]']},{oled['home_label_end[1]']}}}" in tight, 'shim_themes drifted from OLED: home_label_end'
    page = [tuple(map(int, re.search(r'\.page_row = (\d+), \.page_detail = (\d+), \.page_title_h = (\d+), \.page_x = (\d+), \.page_cap_y = (-?\d+), \.page_cap_h = (\d+), \.page_det_y = (\d+), \.page_det_h = (\d+)', t).groups())) for t in shim]
    assert page == [tuple(oled[f] for f in ('page_row', 'page_detail', 'page_title_h', 'page_x', 'page_cap_y', 'page_cap_h', 'page_det_y', 'page_det_h'))] * len(page), 'shim_themes page rows drifted from OLED'
    # Each fixture's .cf, as designated initialisers, so neither a CF_ROLES change nor a field reorder drifts.
    # CF_STYLE0: OLED's macros and captions on the tick cases' shelf, its reflection from CF_REFLECT_TOP, thumb off.
    captions = {f: oled[f] for f in ('cf.name_px', 'cf.artist_px', 'cf.text_y', 'cf.artist_y', 'cf.art_persp')}
    cf_style = [f'-DCF_STYLE0={{ ' + ', '.join(f'.{f[3:]} = {v}' for f, v in {**ROLE_MACROS['oled'], 'cf.reflect_top': 'CF_REFLECT_TOP', **captions, **SHELF_60}.items() if f.startswith('cf.')) + ' }']
    cf_style += [f'-DCF_{f[3:].upper()}={v}' for f, v in SHELF_60.items() if f not in ('cf.tilt', 'cf.swing')]  # CF_X1 ...: the fixture's own
    # CF_STYLE1: the thumb cases' fixture, OLED's numbers on its own page (THUMB_PAGE) and the door's shelf
    # (SHELF_180). The wheel's numbers are retuned on the device: these cases pin the model, with its own fixed ones
    # (letters off: thumb_shipped has them on the shipped numbers).
    fixture = {**oled, **THUMB_PAGE, **SHELF_180, 'cf.ticks_album': 13, 'cf.fast_at': 25, 'cf.fast_full': 120, 'cf.jump_ticks': 0}
    cf_style.append('-DCF_STYLE1={ ' + ', '.join(f'.{f[3:]} = {v}' for f, v in fixture.items() if f.startswith('cf.')) + ' }')
    cf_style.append('-DCF_SHIPPED={ ' + ', '.join(f'.{f[3:]} = {v}' for f, v in oled.items() if f.startswith('cf.')) + ' }')
    # every built-in theme's .cf, for the checks that run each shipped look in turn
    cf_style.append(f'-DCF_THEMES={len(builtins())}')
    cf_style.append('-DCF_SHIPPED_ALL={ ' + ', '.join('{ ' + ', '.join(f'.{f[3:]} = {v}' for f, v in t.items() if f.startswith('cf.')) + ' }' for t in builtins()) + ' }')
    cf_style.append('-DPINNED_FRAMES=0x3cbea4d0u')
    # The built-in numbers the checks use, as coverflow.h and coverflow.c have them
    cf_style += [f'-D{k}={v}' for k, v in re.findall(r'^#define (CF_FOLD_MS|CF_ENTER_SPEED|FOLD_FROM|FOLD_TO|ENTER_RUN|LD_SLOTS|FX_ENTER_MS|FX_LETTERS_MS|FX_SLACK_MS|FX_LATE_MS|HALF_GAP_MS) (\d+)',
                                                 ''.join((ROOT/'patch'/f).read_text() for f in ('coverflow.h', 'coverflow.c', 'coverflow_letters.c', 'coverflow_loader.c')), re.M)]
    def accepts(t):
        try:
            validate(t, 't.xml')
        except ThemeError:
            return False
        return True
    # edge_drawn's held row at the smallest row_out tools/theme.py takes, each built-in theme's
    cf_style.append('-DCF_ROW_OUT_MIN={ ' + ', '.join(str(next(r for r in range(101) if accepts({**t, 'cf.row_out': r}))) for t in builtins()) + ' }')
    source = ((ROOT/'patch/offsets.inc').read_text() + (ROOT/'patch/coverflow.c').read_text()).replace('\\\n', '')
    def macro(name):
        line = re.search(r'^#define ' + name + r' (.*)$', source, re.M)[1]
        return ''.join(ast.literal_eval(x) for x in re.findall(r'"(?:[^"\\]|\\.)*"', line))
    # Marker expansion is executed against real SQLite by test/library_scope.py and the MIPS
    # wrapper tests. These base predicates also apply when no directories have markers.
    music_where = macro('MUSIC_WHERE') + '1'
    sql = macro('MUSIC_ALBUM_SQL').replace(',,', ',' + macro('ALBUM_TRACK') + ',') + music_where + macro('ALBUM_GROUP')
    with sqlite3.connect(':memory:') as db:
        db.execute('create table songtable(id,album,songer,fileurl,albumsonger,track,cd_num)')
        paths = ['/mnt/mmc/Audiobooks/Book/01.mp3', '/mnt/mmc/aUdIoBoOkS/02.flac',
                 '/mnt/mmc/Music/book.M4B', '/mnt/mmc/Music/song.flac',
                 '/mnt/mmc/Audiobooks Extra/song.mp3', '/mnt/mmc/Music/book.m4b.mp3',
                 '/mnt/mmc/Podcasts/Show/episode.mp3', '/mnt/mmc/pOdCaStS/untagged.mp3',
                 '/mnt/mmc/Podcasts Extra/song.mp3', '/mnt/mmc/Music/Podcasts/song.mp3']
        db.executemany('insert into songtable values (?,"Shared","Artist",?,"Artist",1,1)', enumerate(paths))
        assert {r[0] for r in db.execute('select id from songtable where '+music_where)} == {3,4,5,8,9}
        rows = db.execute(sql).fetchall()
        assert len(rows) == 1 and rows[0][3] in paths[3:]
        db.execute('delete from songtable where id >= 3')
        assert not db.execute(sql).fetchall()
        db.execute("insert into songtable(id,album,songer,fileurl,albumsonger) values (10,NULL,'Artist','/mnt/mmc/Music/untagged.mp3',NULL)")
        assert db.execute(sql).fetchone()[:2] == (-1, 'Unknown Album')
        db.execute("update songtable set fileurl='/mnt/mmc/Audiobooks/untagged.mp3' where id=10")
        assert not db.execute(sql).fetchall()
        db.execute("update songtable set fileurl='/mnt/mmc/PODCASTS/untagged.mp3' where id=10")
        assert not db.execute(sql).fetchall()
        for path in ('/mnt/mmc/Discovery/weekly/01.flac', '/mnt/mmc/dIsCoVeRy/01.flac'):
            db.execute('update songtable set fileurl=? where id=10', (path,))
            assert db.execute(sql).fetchone()[:2] == (-1, 'Unknown Album')
        for path in ('/mnt/mmc/Discovery Extra/01.flac', '/mnt/mmc/Music/Discovery/01.flac'):
            db.execute('update songtable set fileurl=? where id=10', (path,))
            assert db.execute(sql).fetchone()[:2] == (-1, 'Unknown Album')
        # An album's art track (ALBUM_TRACK): its lowest disc, then track, then path, whatever order its
        # rows went in (the order the files were copied); no track number counts as 0.
        db.execute('delete from songtable')
        album = '/mnt/mmc/Music/Album/'
        for i, (name, track, disc) in enumerate((('d2t1.flac', 1, 2), ('d1t3.flac', 3, 1), ('z.flac', 2, 1), ('a.flac', 2, 1))):
            db.execute('insert into songtable(id,album,songer,fileurl,albumsonger,track,cd_num) values (?,"Album","Artist",?,"Artist",?,?)', (20 + i, album + name, track, disc))
        art = lambda: db.execute(sql).fetchone()[3]
        assert art() == album + 'a.flac'
        db.execute("update songtable set track=1 where id=22")
        assert art() == album + 'z.flac'
        db.execute("update songtable set track=NULL where id=21")
        assert art() == album + 'd1t3.flac'
        # A number outside 0-9999 (a date as the track, or -1) takes its bound: the path stays whole.
        db.execute("update songtable set track=-1 where id=21")
        assert art() == album + 'd1t3.flac'
        db.execute("update songtable set track=20231015 where id=21")
        assert art() == album + 'z.flac'
        db.execute("update songtable set cd_num=-1 where id=20")
        assert art() == album + 'd2t1.flac'

    with tempfile.TemporaryDirectory(prefix='q2-coverflow-check-') as directory:
        tmp = pathlib.Path(directory)
        (tmp/'shim.h').write_text(SHIM_H)
        (tmp/'coverflow_all.c').write_text(CF_SOURCES)
        (tmp/'test.c').write_text(TEST)
        (tmp/'ffmpeg').write_text(FFMPEG_STUB)
        (tmp/'ffmpeg').chmod(0o755)
        builds = {'coverflow_test': ()}  # each build's own flags; its card is <name>-root
        def cc(name, src):  # each source on its own, so they compile at once
            return subprocess.Popen(['cc', '-m32', '-pthread', '-DPEQ_HOST', f'-DPEQ_ROOT="{tmp}/{name}-root"',
                        f'-DART_FFMPEG="{tmp}/ffmpeg"', '-D_GNU_SOURCE', '-O1', '-Wall', '-Wextra', '-Werror', '-Wno-unused-result', '-Wno-unused-parameter', '-Wno-unused-function',
                        *builds[name], *cf_style, '-I', str(ROOT/'patch'), '-include', str(tmp/'shim.h'), '-c', str(tmp/src), '-o', str(tmp/f'{name}-{src}.o')])
        objects = [cc(name, src) for name in builds for src in ('coverflow_all.c', 'test.c')]
        assert all(o.wait() == 0 for o in objects), 'the host tests did not build'
        for name in builds:
            subprocess.run(['cc', '-m32', '-pthread', str(tmp/f'{name}-coverflow_all.c.o'), str(tmp/f'{name}-test.c.o'), '-lm', '-Wl,--wrap=fopen', '-o', str(tmp/name)], check=True)
        binary = tmp/'coverflow_test'
        env = dict(os.environ)
        spins = json.loads((ROOT/'test/fixtures/coverflow_spins.json').read_text())  # real_spins' input
        (tmp/'spins').write_text(spins['library'] + '\n' + ''.join(
            f"{k} {r['start']} {r['lands']} {len(r['passes'])}\n" + ''.join(f'{m} {t} {h}\n' for m, t, h in r['passes'])
            for k, r in spins['runs'].items()))
        env['CF_SPINS'] = str(tmp/'spins')
        if a.captures:  # capture the reflection intensity
            (tmp/'frames').mkdir()
            env['CF_CAPTURES'] = str(tmp/'frames')
        subprocess.run([str(binary)], check=True, env=env)
        if a.captures:
            a.captures.mkdir(parents=True, exist_ok=True)
            from ipod import inc, imagemagick
            size = f"{inc('CF_VIEW_W')}x{inc('CF_VIEW_H')}"
            for raw in sorted((tmp/'frames').glob('*.rgba')):  # RGBA8888 rows, the frame's byte order
                (a.captures/(raw.stem + '.png')).write_bytes(imagemagick(
                    '-size', size, '-depth', '8', 'rgba:-', '-alpha', 'off', 'png:-', data=raw.read_bytes()))
    print('Coverflow: art order, ffmpeg fallback, locks, markers, resume, cancel, Refresh, album list reuse, low space and empty library passed;'
          ' depth renderer (exact centre, clipping, symmetry, reflection, continuity),'
          ' its texture window, centre click, small libraries and flat fallback passed;'
          ' Sort by artist, year, recently added, most played and random, kept across opens, passed;'
          ' Home art sources, fit, clip, cache and Split/Full layout passed;'
          ' playlist art (own picture, 2x2, bad picture, songs changed mid-build, card full or gone) passed; playlists as covers (captions, art filling in, Centre, Return) passed;'
          ' the scoped pages\' cards, Go to Album, the Cancel row and the Unknown Album card\'s resume after a restart passed.')


if __name__ == '__main__':
    main()
