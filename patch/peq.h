#ifndef Q2_PEQ_H
#define Q2_PEQ_H

#ifdef PEQ_HOST
#ifndef _GNU_SOURCE
#define _GNU_SOURCE /* mmap64 */
#endif
#include <fcntl.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <time.h>
#include <unistd.h>
#define child_exit _exit
int card_mounted(void); /* the host checks' shim */
static inline void child_oom_first(void) {
    int fd = open("/proc/self/oom_score_adj", O_WRONLY);
    if (fd >= 0) write(fd, "1000", 4), close(fd);
}
static inline void crash_note(const char *path, const char *file, const char *log, unsigned every,
                              unsigned *n) {
    (void)path, (void)file, (void)log, (void)every, (void)n; /* the device's crash log only */
}
#else
#include "peq_platform.h"
#define PEQ_ROOT "" /* host tests pass a scratch directory */
void child_exit(int status) __attribute__((noreturn));
void child_oom_first(
    void); /* between fork and exec: the OOM killer takes this child, not demo (#169) */
void ringnav_clean_end(
    void); /* a clean end begins: the watchdog stops, the state record goes (system.c) */
void reboot_record(void *caller); /* a Bluetooth abnormal reboot's q2-crash.log (system.c) */
int card_mounted(void);           /* /proc/mounts has the card (system.c) */
/* The crash log's record of a long job's current file and demo's memory (tags.c) */
void crash_note(const char *path, const char *file, const char *log, unsigned every, unsigned *n);
#ifdef canvas_set_fill_color
/* Light Mode: every colour the payload draws passes ui_ink (theme_engine.c) on its way to these
 * stock setters, as stock's own colours pass the style and image hooks. stock_* sets a colour read
 * back from the LCD, which has passed already. */
unsigned ui_ink(unsigned rgba);
unsigned ui_ink_raw(unsigned raw);
static inline int stock_canvas_set_fill_color(void *c, unsigned x) {
    return canvas_set_fill_color(c, x);
}
static inline int stock_canvas_set_text_color(void *c, unsigned x) {
    return canvas_set_text_color(c, x);
}
static inline int stock_canvas_set_stroke_color(void *c, unsigned x) {
    return canvas_set_stroke_color(c, x);
}
static inline int ink_canvas_fill_rounded_rect(void *c, const void *r, const void *g,
                                               const void *color, unsigned radius) {
    unsigned v = ui_ink(*(const unsigned *)color);
    return canvas_fill_rounded_rect(c, r, g, &v, radius);
}
static inline int ink_canvas_stroke_rounded_rect(void *c, const void *r, const void *g,
                                                 const void *color, unsigned radius, unsigned w) {
    unsigned v = ui_ink(*(const unsigned *)color);
    return canvas_stroke_rounded_rect(c, r, g, &v, radius, w);
}
static inline int ink_vgcanvas_set_fill_color(void *vg, unsigned x) {
    return vgcanvas_set_fill_color(vg, ui_ink(x));
}
static inline int ink_vgcanvas_set_stroke_color(void *vg, unsigned x) {
    return vgcanvas_set_stroke_color(vg, ui_ink(x));
}
static inline int ink_vgcanvas_set_fill_linear_gradient(void *vg, float sx, float sy, float ex,
                                                        float ey, unsigned from, unsigned to) {
    return vgcanvas_set_fill_linear_gradient(vg, sx, sy, ex, ey, ui_ink(from), ui_ink(to));
}
#undef canvas_set_fill_color
#undef canvas_set_text_color
#undef canvas_set_stroke_color
#undef canvas_fill_rounded_rect
#undef canvas_stroke_rounded_rect
#undef vgcanvas_set_fill_color
#undef vgcanvas_set_stroke_color
#undef vgcanvas_set_fill_linear_gradient
#define canvas_set_fill_color(c, x) stock_canvas_set_fill_color(c, ui_ink(x))
#define canvas_set_text_color(c, x) stock_canvas_set_text_color(c, ui_ink(x))
#define canvas_set_stroke_color(c, x) stock_canvas_set_stroke_color(c, ui_ink(x))
#define canvas_fill_rounded_rect ink_canvas_fill_rounded_rect
#define canvas_stroke_rounded_rect ink_canvas_stroke_rounded_rect
#define vgcanvas_set_fill_color ink_vgcanvas_set_fill_color
#define vgcanvas_set_stroke_color ink_vgcanvas_set_stroke_color
#define vgcanvas_set_fill_linear_gradient ink_vgcanvas_set_fill_linear_gradient
#endif
#endif
#include "peq_types.h"
#include "theme.h"
#include "ui_kit.h"

#define PEQ_CHANNELS 8
#define PEQ_FILE_LIMIT 16384
#define PEQ_LINE_LIMIT 512
#define PEQ_ACTIVE PEQ_ROOT "/mnt/data/peq-active"
#define PEQ_SAVED                                                                                  \
    PEQ_ROOT "/mnt/data/peq-presets"      /* earlier firmware's binary presets: written out to     \
                                             PEQ_FOLDER once, kept */
#define PEQ_FOLDER PEQ_ROOT "/mnt/mmc/EQ" /* the presets, Equalizer APO text (peq_export) */
#define EQ_OUTPUTS                                                                                 \
    PEQ_ROOT "/mnt/data/peq-outputs" /* EQ per Output: an eq_state per output, and owner */
#define EQ_DEVICES 32                /* Bluetooth devices remembered at once */

/* Why peq_parse failed, at line; or, when it loads, what it left out: skipped lines, the first at
 * line, reason as a failure would give it, and what a word or two naming it for a message. */
typedef struct {
    unsigned line;
    const char *reason;
    unsigned skipped;
    char what[24];
} peq_error;
typedef struct {
    double b0, b1, b2, a1, a2;
} peq_coeff;
typedef struct {
    peq_coeff c[PEQ_BANDS];
    double z[PEQ_CHANNELS][PEQ_BANDS][2], gain[PEQ_CHANNELS];
    int bypass, used, only[PEQ_BANDS]; /* used: bands past it are identity with zero memory; only: 0
                                          every channel, else channel only-1 */
} peq_engine;
typedef struct {
    peq_engine current, next, pending;
    int rate, channels, ramp, ramp_length, waiting;
} peq_dsp;

void peq_default(peq_preset *p);
int peq_valid(const peq_preset *p);
int peq_parse(const char *text, unsigned size, peq_preset *out, peq_error *error);
int peq_number(const char *s, double *out);
int peq_import_file(const char *path, peq_preset *out, peq_error *error);
int peq_load(const char *path, peq_preset *out);
int peq_load_active(peq_preset *p); /* the effective preset (eq_effective); 0 if none was read */
int peq_save(const char *path, const peq_preset *p, int replace);
/* p as Equalizer APO text that peq_parse reads back as p, its Auto in a "# q2:" comment: the
 * length, or 0 if p is invalid or out holds less. peq_export_file is peq_save's, as text. */
unsigned peq_export(const peq_preset *p, char *out, unsigned size);
int peq_export_file(const char *path, const peq_preset *p, int replace);
void eq3_bands(peq_preset *p, const double gain[3]);
int eq_load(eq_state *s); /* 0: no readable state, s holds the defaults */
int eq_save(const eq_state *s);
int eq_load_from(const char *path, eq_state *s), eq_save_to(const char *path, const eq_state *s);
int peq_save_file(const char *path, const char *magic, const void *data, unsigned size);
void eq_effective(const eq_state *s, peq_preset *out);
int peq_compile(const peq_preset *p, int rate, peq_engine *out); /* the saved preamp, Auto or not */
void peq_response(const peq_engine *e, int rate, double f, double *l, double *r);
/* Auto's preamp at rate, dB: what the player plays */
double peq_auto(const peq_preset *p, int rate);
void peq_reset(peq_dsp *d, int rate, int channels, const peq_preset *p);
int peq_update(peq_dsp *d, const peq_preset *p);
void peq_process(peq_dsp *d, float *audio, unsigned frames);

long long now_ns(void);
unsigned long long now_ms(void);
/* Crossfade (docs/internals.md#crossfade): demo writes the next track change's fade, an int: its
 * length in ms (XF_MS, 0 for none, the fixed fade a smart one falls back to), XF_SMART to have the
 * player choose the overlap from both songs' loudness, and demo's count of stops above (XF_STOPS).
 * The player maps the file read-only and holds the outgoing track's tail for it. XFADE_NEXT names
 * the two songs of that change, a line each, the playing one's path first, so the player can open
 * the next one ahead (made whole under .tmp and renamed; empty when the next is not known). */
#define XFADE_FILE PEQ_ROOT "/tmp/q2xfade"
#define XFADE_NEXT PEQ_ROOT "/tmp/q2xfade.next"
#define XF_MS 0x7fff
#define XF_SMART 0x8000
#define XF_STOPS 16
/* Mono Audio: demo writes an int, 1 to mix both channels into each; hciplayer's filter and q2video
 * map it read-only (video.c keeps its own copy of the path). */
#define MONO_FILE PEQ_ROOT "/tmp/q2mono"
#define SWAP_FILE PEQ_ROOT "/tmp/q2swap"
/* AirPlay (docs/internals.md#airplay-sender): the q2-airplay card app makes CAST_FILE, a cast_tap,
 * whole under another name and renames it, and keeps it. While the app reads (want, its CAST_CLOCK,
 * less than CAST_STALE old) hciplayer's filter puts what plays into ring[seq % CAST_RING] at
 * CAST_RATE, 16-bit, then sets id, pts_ms and stamp, then seq; mute set, it also skips the EQ and
 * silences the Q2's own output. A seek or a skip (not a song running into the next) sets jump_seq,
 * then bumps jumps. demo writes its part each pass and the playing song's tags to CAST_TRACK; it
 * reads the app's state and the speaker's place in the song, for the lyrics. The app (q2-airplay's
 * tap.go) mirrors this layout. */
#define CAST_FILE PEQ_ROOT "/tmp/q2cast"
#define CAST_TRACK PEQ_ROOT "/tmp/q2cast.track"
#define CAST_MAGIC 0x31544351u /* "QCT1" */
#define CAST_RATE 44100
#define CAST_RING 262144 /* a power of two: 5.9 s at CAST_RATE */
#define CAST_CLOCK(ns)                                                                             \
    ((unsigned)((unsigned long long)(ns) >> 20)) /* 1.048576 ms ticks: no division */
#define CAST_STALE 1430                          /* 1.5 s of ticks: demo lets go first */
#define CAST_STALE_PLAYER                                                                          \
    1900 /* 2 s: then the filter, its output 500 ms behind in ALSA's buffer                        \
          */
enum { CAST_OFF, CAST_CONNECTING, CAST_ON };
typedef struct {
    unsigned magic, size;
    /* the app's */
    unsigned want; /* CAST_CLOCK while it reads */
    int mute;      /* the Q2's own output silent, its EQ skipped */
    int state;     /* CAST_*, for demo */
    int pos_ms; /* the speaker's place in the song of filter instance pos_id, at pos_at (CAST_CLOCK)
                 */
    unsigned pos_id, pos_at;
    int pos_run; /* 1 while it plays on from there */
    char speaker[64];
    /* the filter's */
    unsigned seq; /* frames written */
    unsigned id;  /* the filter instance (one a song) of the frame before seq */
    int pts_ms;   /* that frame's place in its song */
    unsigned jumps, jump_seq;
    int dsd;         /* the song plays through untouched (DSD, DoP): nothing to send */
    int spk;         /* the app's: the speaker's volume (0-100), as it said or the Q2 last set it */
    long long stamp; /* CLOCK_MONOTONIC ns of the last write */
    /* demo's */
    unsigned beat;      /* bumped each pass */
    int status;         /* mclGetPlayStatus: 1 stopped, 2 playing, 3 paused */
    int volume, maxvol; /* volume: the speaker's (0-100) while one plays, else -1 (also the filter's
                         * mute until demo has the earphones' level back) */
    unsigned track;     /* CAST_TRACK's writes */
    unsigned spk_seq;   /* the app's: bumped with each spk (0: an app that never says) */
    short ring[CAST_RING][2];
} cast_tap;
_Static_assert(__builtin_offsetof(cast_tap, stamp) == 128 &&
                   __builtin_offsetof(cast_tap, ring) == 160,
               "q2-airplay's cast_tap layout");
/* The wired outputs' wait (docs/internals.md#wired-start): demo writes now_ms, never 0, at a press
 * that starts a track or seeks, and 0 at a natural change; hciplayer reads it at init's end and
 * seek's unmute. */
#define PRESS_FILE PEQ_ROOT "/tmp/q2press"
extern int
    press_natural; /* crossfade.c: inside a track change nobody pressed for, nothing stamps */
void press_stamp(void), press_forget(void);
/* librespot (spotify.c) and its restart loop, whose pid its launcher left, stopped: Spotify's idle
 * stop and Home's Rockbox row, which lets ALSA go too. A q2video sink ends with librespot's pipe.
 */
#define SPOT_OUT "/tmp/q2-librespot.out" /* the output for the card's aplay.sh, none on the DAC */
#define SPOT_DOWNLOAD                                                                              \
    "/tmp/q2-librespot.download" /* backend download progress, independent of playback */
#define SPOT_KILL                                                                                  \
    "kill -9 $(cat /tmp/q2-librespot); killall -9 librespot aplay; "                               \
    "rm -f /tmp/q2-librespot /tmp/q2-librespot.state " SPOT_OUT " " SPOT_DOWNLOAD
double decibels(double power); /* 10 log10 */

/* Audited against hciplayer 9c3f8c6d… and MPlayer 1.3.0 libaf/af.h: the filter peq_player.c opens.
 */
typedef struct {
    void *audio;
    int len, rate, nch, format, bps;
} af_data;
typedef struct af_instance {
    const void *info;
    int (*control)(struct af_instance *, int, void *);
    void (*uninit)(struct af_instance *);
    af_data *(*play)(struct af_instance *, af_data *);
    void *setup;
    af_data *data;
    struct af_instance *next, *prev;
    double delay, mul;
} af_instance;
/* id: one per filter instance, for crossfade and the carried filter memory; fresh: negotiated, not
 * yet played; ending: its last block was filtered near the track's end */
typedef struct {
    peq_dsp dsp;
    peq_preset preset;
    int id, fresh, ending;
} player_state;

/* The payload's shared helpers: coverflow.c's pages, hashes and worker, navigation.c's. */
void *text(void *parent, int x, int y, int w, int h);
/* feedback.c: OLED's empty and offline note (docs/oled-design.md#empty-and-offline); 0 elsewhere */
int feedback_empty(void *parent, int free, const char *message, const char *hint);
int feedback_note(void *line, void *hint, int free);
int feedback_below(void *w); /* the screen y under w */
void *page_open(const char *name, int (*closed)(void *, void *), int (*keyup)(void *, void *));
void motion_open(void *page,
                 const char *name); /* navigation.c: page_open's page takes the theme's hint */
void *page_title(void *body, const char *caption, int n);
void page_empty(void *body, const char *name);
#define PAGE_TITLE "_page_title" /* the caption the bar reads (navigation.c page_caption) */
#define PAGE_TALL "_page_tall"   /* a two-line row, its separators' (paint_separators) */
int page_h(int h);
int page_x(void);
void page_retitle(void *page, void *title, const char *caption);
void *page_list(void *page, void *body, void **title, const char *caption, int n, int item_h);
void *rows_view(void *parent, int y, int w, int h, int n, int item_h);
void *page_list_or(void *page, void *body, const char *name, const char *none, int n, int item_h);
/* feedback.c: the empty state (docs/oled-design.md#empty-and-offline), an outline glyph
 * (tools/art.py EMPTY_GLYPHS; none in the compact form, under rows), a title, a hint (lines split at
 * '\n') and at most one button; a status page's (a card app's Sync, docs/oled-design.md#sync) also a
 * progress bar under the title (bar: its permille + 1, 0 for none) and a footer line (foot) */
typedef struct {
    const char *glyph, *title, *hint, *row, *foot;
    int bar;
} empty_t;
int empty_state(void *page, int free_top, const empty_t *e, int (*on_row)(void *ctx, void *event));
int empty_row_y(int free_top);
int empty_shown(void *page);
int empty_button(void *list);
void *empty_list(void *page, void *body, const char *name, const empty_t *e,
                 int (*on_row)(void *ctx, void *event));
void *empty_open(const char *window, const char *name, const empty_t *e,
                 int (*on_row)(void *ctx, void *event));
int page_push(void *view, int dir);
void *page_row_detail(void *view, int index, const char *caption, const char *detail,
                      int (*click)(void *, void *));
void *page_row_detail_into(void *view, int index, const char *caption, const char *detail,
                           int (*click)(void *, void *));
void page_row_into(void *view, int index, const char *caption, int (*click)(void *, void *));
void row_into(void *item, int h);
/* artist_page.c: an artist's page under the menu sheet's set (docs/oled-design.md#artist-page) */
void *hero_open(void *body, const char *name, const char *sub, const char *meta);
void *hero_now(void);
void pair_open(void *head, void *list, const char *const label[2], const char *const icon[2],
               const int ctx[2], int (*press)(void *, void *), signed char *stop);
int drill_list(void *view, int n);
void drill_count(void *item, const char *count);
void *window_of(void *w); /* navigation.c */
void *cover_row(void *view, int index, int h, int thumb, int px, const char *title,
                const char *right, const char *cover, int (*click)(void *, void *), int ctx);
void cover_after(void *item, const char *album);
void cover_mono(void *item);
void artist_paint(void *w, void *canvas);
/* and an album's page (docs/oled-design.md#album-page): its cover, lines and tracks */
void hero_cover(void *head, const char *url);
void hero_text(void *head, const char *name, const char *sub, const char *meta);
void *track_row(void *view, int index, int num, const char *title, const char *len,
                int (*click)(void *, void *), int ctx);
#define AR_SONG_PX 22 /* cover_row's px for a song: the design's 18 px in AWTK's size */
int geom_val(int v) __attribute__((pure));
/* menu_modal.c */
int modal_actions(const char *const *names, const char *const *icons, int n, void (*pick)(int i));
int modal_pick(void *under, const char *const *names, int n, int current, void (*pick)(int i));
void modal_head(const char *title, const char *sub, void *r); /* the next sheet's header */
void list_toggle(void *label, const char *title, int on); /* navigation.c: a row's switch */
void caption_row(void *label, const char *text); /* settings_pages.c: a row's caption */
/* navigation.c: a set_text row's value */
void list_value(void *label, const char *title, const char *value);
char *csv_find(char *list, const char *item); /* navigation.c: comma lists */
int csv_toggle(char *list, unsigned size, const char *item);
int text_differs(void *w, const char *s); /* coverflow.c */
long file_size(const char *path);                   /* coverflow.c: -1 when it can't be opened */
int picture_size(const char *path, int *w, int *h); /* coverflow.c */
void *read_all(const char *path, unsigned max, unsigned *size); /* scrobble.c */
int visual(void *s, int n, int *c, int *frac);
int thumb(const char *src, const char *dst, int w, int h);
extern volatile unsigned ff_kills; /* coverflow.c: ffmpeg runs killed or never started */
int card_space(const char *dir);
int card_room(const char *dir, unsigned mb, unsigned pct);
unsigned hash_bytes(unsigned h, const unsigned char *s, unsigned n); /* hash.c: FNV-1a */
unsigned fnv(unsigned h, const unsigned char *s);
void stop_timer(unsigned *timer), rearm(unsigned *timer, int (*fn)(const void *), unsigned ms);
int return_key(void *event, unsigned *timer,
               int (*leave)(const void *)); /* a page's keyup: Return */
int center_press(unsigned *timer, unsigned *at, int (*single)(const void *), int scrubbing);
int config_value(const char *section, const char *key, int n,
                 int def); /* theme_engine.c: a config.ini decimal */
int image_show(void *img, const char *url, unsigned *size);
int album_cmp(void *a, void *b);
int spoken(const char *path); /* navigation.c: under Podcasts or Audiobooks, or an M4B */
void peq_attach(void);        /* peq_ui.c */
/* EQ per Output (docs/internals.md#eq-per-output): peq_ui.c swaps the EQ when the output named key
 * ("3.5mm", "4.4mm", "usb", a Bluetooth MAC) takes over; forgetting whose it is ends the following.
 */
void eq_output(const char *key), eq_output_forget(void);
int ringnav_eq_output(void);         /* navigation.c: the setting */
void ringnav_eq_output_toggle(void); /* and its row's click */
void peq_boot(void); /* clear a previous session's Simple EQ comparison before restoring playback */
extern int peq_chained; /* peq_ui.c: peq_stock_eq ran this boot */
void peq_chain(void);   /* crossfade.c: peq_attach once a connect (ringnav_mode), while playing */
int swap_on(void);
int mono_on(void);   /* crossfade.c: Mono Audio's setting */
#define NS_WARM 2000 /* nightshift.c: Night Shift's temperatures, K */
#define NS_COOL 5000
#define NS_STEP 100
#define NS_DEFAULT 3400
int night_on(void), night_kelvin(void); /* night_on: 0 Off, 1 On, 2 Auto */
void eq_toggle(void); /* peq_ui.c: EQ off, or on as PEQ; Control Center's EQ circle */
void night_set(int on, int kelvin), night_save(void), night_paint(void *canvas), night_clock(int dark);
void splash_poll(void); /* splash_hold.c: the boot animation's hold, every UI loop pass */
int clock_night(void); /* theme_engine.c: Night Shift's Auto hours now */
int worker_stop(unsigned long thread, int *running, volatile int *cancel, unsigned *timer);
void ringnav_select(void *w, int id, int rows);
int library_scanning(void); /* coverflow.c: a library scan's thread runs */
int library_quiet(void);    /* addon_library.c: a library scan may start now */
int library_counting(void); /* coverflow.c: a song count reads songtable */
/* coverflow.c: the library's generation, one up each rewrite (Most Played's too); an album row's
 * tracks, its id and the length of a path's album folder */
extern volatile unsigned library_gen;
/* durations.c: cache-only reads for Cover Flow; no file probes on the UI thread. */
int duration_seconds(void *song);
const char *duration_text(char *text, unsigned size, void *song);
void durations_scanned(void); /* a library scan's end, on its thread */
int thread_lower(int idle);   /* coverflow_loader.c: a worker's nice 19 and its I/O class */
/* coverflow.c: the o32 statx syscall, as demo imports none */
int ringnav_statx(int dirfd, const char *path, int flags, unsigned mask, void *buf);
void *coverflow_album_tracks(void *r);
unsigned album_id(void *r), album_dir(const char *p, int disc);
/* nav/home_carousel.c: Cover Flow's glide */
int carousel_glide(void *w, int goal, unsigned ms, int easing);
int ringnav_big_library(void); /* Display's Fast Navigation: letters, years and half copies */
/* A letter with this bit is the Year sort's year instead, in its low bits, 0 undated. */
#define CF_YEAR 0x80000000u
int blob_io(const char *path, const char *tmp, void *buf, unsigned size, int write);
#define BLOB_IO(file, buf, write) blob_io(file, file ".tmp", &(buf), sizeof(buf), write)
int clip_within(void *canvas, int *old, int *clip, int x, int y, int w, int h);
void play_folder(void *dq, int idx, int cls);
int app_urls_play(const char *path); /* tidal.c: a card app's Q2URLS/1 queue, played */
int app_lyrics(const char *id, char *out, unsigned size); /* tidal.c: an app track's .lrc path */
int app_fav(void);         /* tidal.c: Now Playing's heart on an app's streamed song, -1 not one */
void app_fav_load(void);   /* tidal.c: a queue loading clears an app song's heart */
int app_urls_at(void);     /* tidal.c: the song playing's n in a now=1 URL queue playing; -1 none */
int directory(void *path); /* playback.c: toolsLoadDirectory, for staged */
#define TIDAL_DIR "/mnt/mmc/.tidal" /* tidal.c's cache, Tidal Downloads' list */
void tidal_clear(void);
#define SPOT_DIR "/mnt/mmc/.spotify" /* librespot and its launcher, spotify.c's */
/* addon.c: card apps, each a card folder .<id> holding its manifest q2app, its
 * launcher run, its binary q2-<id> and its icons. addon_scan finds them again (how many slots: a
 * slot's index stays its app's), addon_name, addon_id and addon_icon (an image name: file://… or
 * the shared stream_app) are one's while it is on the card (else 0), addon_open its Streaming row's
 * click (ctx: the slot). addon_forget and addon_quit: the Apps page replaces or removes the files
 * of the app in folder dir (".<id>"): asked to end and waited for on the Apps page's worker, then,
 * once replaced or removed, forgotten on the UI thread. */
#define APPS_MAX 8 /* card apps on the card at once */
int addon_scan(void), addon_open(void *ctx, void *event), addon_hold(void *top);
int addon_row_hold(void *top); /* Centre or a finger held on a card app's row: its own menu */
/* library_pages.c: songs into the queue playing as the hold menu's Play Next (next 1) or Play Last
 * (2); 0 when the queue takes none */
int queue_add(void *add, int next);
void child_quit(int pid, unsigned quit_ms); /* radio.c: a child asked to quit, reaped or killed */
int addon_games_last(void); /* the card's games as the last addon_scan found them */
/* The games games.c's chooser ships, the one place to turn each on (docs/internals.md#games): 1
 * lists it and builds it into the payload, 0 leaves it out of both, its source kept in games.c
 * (test/fw builds every one, GAMES_ALL, so its tests keep running). Sudoku, Vortex, Solitaire
 * and Snake wait while the games in progress are finished (the owner, 2026-11-22); Bricks is the
 * engine's (engine.c eng_games[]), always listed. */
#define GAME_SUDOKU 0
#define GAME_VORTEX 0
#define GAME_SOLITAIRE 0
#define GAME_SNAKE 0
#define GAMES_BUILTIN (GAME_SUDOKU + GAME_VORTEX + GAME_SOLITAIRE + GAME_SNAKE) /* chooser rows */
const char *addon_name(int a), *addon_id(int a), *addon_icon(int a);
int card_path(const char *path); /* on the card, no ".." segment: what an app or server may name */
/* stream_state.c: Download Ahead and a card app's .stream
 * (docs/server-library.md#download-ahead-and-the-streaming-state) */
enum { SL_NONE, SL_WAIT, SL_GET, SL_STALL, SL_DONE, SL_FAIL };
typedef struct {
    int state;               /* SL_* */
    unsigned have, total, ms; /* bytes on the card, of the whole (0 unknown), length (0 unknown) */
} sline_t;
int ahead_n(void), ahead_step(void);
void ahead_start(const char *id, unsigned seq);
const char *ahead_caps(void);
int stream_line(const char *id, const char *path, sline_t *l);
int stream_startable(const char *id, const char *path);
int stream_app_of(const char *path, char *id, unsigned size);
int stream_player_waits(const char *path);
/* How sure the length of path is: 0 the player's or the library's stands (not a song of an app's
 * stream cache, no .stream line, or its line is done); 1 the server's own length is known, *ms
 * (the line's, else the catalogue's); 2 the song is still arriving and no length is known. */
int stream_length(const char *path, unsigned *ms);
/* The last second of path a seek may go to while it arrives: what is on the card of it, at most the
 * player's length total (seconds); -1 when it is not arriving (stream_length 0). Not called: a seek
 * past the bytes there are waits for them in q2player (docs/internals.md#streaming-configuration). */
int stream_reach(const char *path, int total);
void ahead_poll(const char *id, const char *seq, void *queue);
int addon_is_icon(const char *name); /* a card app's Streaming icon, by its file:// name */
void addon_poll(void), addon_stop(void), addon_paint(void *w, void *canvas), addon_card_away(void);
void radio_paint(void *w, void *canvas); /* radio.c: its loading overlay */
const char *addon_qr_title(void *win); /* addon.c: the QR page's app name, 0 for another window */
void addon_forget(const char *dir), addon_quit(const char *dir);
/* addon.c: a catalogue app started for a catplay (0 running, 1 started, -1 can't), and whether a
 * started one reads it yet */
int addon_wake(const char *id), addon_ready(const char *id);
/* addon.c: what a running app's state file says it syncs (its sync= key,
 * docs/internals.md#card-app-sync): 0 nothing, 1 checking, 2 downloading with *permille (unless 0)
 * how far */
int addon_sync(int *permille);
int addon_offline(void); /* a running app's server out of reach (status_row.c) */
int addon_offline_ok(const char *id), addon_offline_ask(const char *id, int add, const char *key);
/* addon.c's card games (q2app game=1), Games' rows after its own: how many (a scan), one's name,
 * its start over the screen (1 when it runs), whether one runs, and ringnav_input's events for it
 */
int addon_games(void), addon_game_start(int k), addon_game_on(void);
int addon_game_audio(
    void); /* a card game with audio=1 runs: the sound and the media keys are its */
const char *addon_game_name(int k);
/* games.c: the theme's pop-up for a card game (Q2_POPUP's value), 7 + 6 * 7 + 64 * 7 + 1 bytes at
 * most */
#define POPUP_ENV 512
void popup_env(char *out, unsigned size);
int addon_game_input(void *e), addon_is_game(const char *id);
void ringnav_wdt_long(void);   /* system.c: the watchdog's long count for a long pass */
int ringnav_smart_awake(void); /* navigation.c: Smart Awake (IPOD KEEPLIT) is on */
void library_poll(void); /* addon_library.c: an app's library update request, once per addon_poll */
/* spotify.c's Q2SPOT/1 framing (docs/librespot.md), which the card apps speak too */
int spot_escape(const char *s, char *out, unsigned size);
int spot_reply(char *buf, unsigned len, char **f, int max);
void spot_shut(int fd);
/* library_lists.c: a card folder's songs played from the card (a card app's local row): a
 * folder opens its list, a file plays its folder from it; album, in track order (by name), else
 * newest first. */
int songs_open(const char *path, const char *title, int album);
void *app_album_songs(const char *dir); /* library_lists.c: a folder's songs by name, for addon.c */
int addon_goto(const char *path, const char *title, int album); /* Go to on a card app's song */
int addon_goes(const char *path);      /* addon.c: Go to on path opens a page */
int addon_scrobbles(const char *path); /* addon.c: not when path's app says scrobble=0 */
int addon_more(void); /* addon.c: Now Playing's ⋯ opens the playing queue's app's page */
int addon_radio(void); /* addon.c: the playing queue's app plays a radio */
/* addon.c: a song's radio asked of its app (song_radio.c); the answer to request seq, -1 none yet */
int addon_radio_start(const char *path, unsigned seq), addon_radio_stop(unsigned seq);
int addon_radio_answer(unsigned seq);
const char *addon_radio_missing(const char *path);
/* addon.c: a song's lyrics asked of its app (lyrics.c); the answer to request seq, -1 none yet */
int addon_lyrics_ask(const char *path, unsigned seq);
int addon_lyrics_answer(unsigned seq, const char *path);
/* addon.c: a restored stream's songs not on the card asked of their app (playback.c
 * ringnav_memory), the restored queue loaded again (ringnav_load), and Up Next's pending songs as
 * paths (save_tail) */
int addon_resume_ask(const char *const *paths, const unsigned char *in, unsigned n, unsigned at,
                     int sec, int playing, unsigned seq);
void addon_resume_follow(void);
char *addon_pending_paths(unsigned max, unsigned *n, unsigned *bytes);
void addon_cover_wake(const char *id); /* addon.c: a card app with covers=1 started for the car's cover */
void addon_open_id(const char *id); /* a card app's page by its id, hidden or not */
/* ... at op, its one argument arg ("" for none), titled title (0: the app's name) */
int addon_open_at(const char *id, const char *op, const char *arg, const char *title);
/* airplay.c: the q2-airplay app's side (docs/internals.md#airplay-sender) */
void airplay_poll(void), airplay_more(void *view);
int airplay_on(void), airplay_lyrics(int *ms);
/* addon.c: a heart's press, appended to the app's fav.log for it to send (docs/librespot.md);
 * addon_fav_path's for a song in a card folder an app's q2app names as favdir, else nothing to do.
 * 0 when the card didn't take it. */
int addon_fav(const char *id, const char *kind, const char *key, int on);
int addon_fav_path(const char *path, int on);
/* addon.c: which copy of a card app runs, the card's or the firmware's (docs/internals.md#bundled-apps) */
int addon_dir(const char *id, char *dir, unsigned n), addon_version_cmp(const char *x, const char *y);
/* addon.c: whether the firmware carries app id as its own alone, never the App Store's (netupdate.c) */
int addon_builtin(const char *id);
void addon_q2app(const char *id, char *path, unsigned n);
/* each card app's q2app read, until each answers nonzero (addon.c) */
int addon_q2apps(int (*each)(const char *id, const char *raw, void *ctx), void *ctx);
/* catalogue.c: the server library (docs/internals.md#server-library), a signed-in catalogue app's
 * songs in the Library. catalogue_has: any are in it now; catalogue_path: a server row's path; catalogue_unavailable: a record that can't play now;
 * catalogue_album_unavailable: an album list's record none of whose songs can play now, and
 * catalogue_artist_unavailable an artist's;
 * catalogue_union: a Library query reading servertable too (0: as it is; the caller frees it);
 * catalogue_load: ringnav_load's queue (0 as it is, 1 *own instead, 2 nothing loaded). */
int catalogue_has(void), catalogue_path(const char *path), catalogue_unavailable(void *r);
/* catalogue.c: the server row (its fileurl) record r's Stream Cache copy plays, or 0 */
const char *catalogue_row_path(void *r);
int catalogue_album_unavailable(void *r), catalogue_artist_unavailable(void *r);
int catalogue_seconds(const char *path), catalogue_copy_seconds(const char *path), catalogue_load(void *q, int *at, void **own);
int catalogue_access(const char *path, int mode); /* stock's play check, tools/build.py */
int catalogue_missing(const char *path);          /* the payload's own lists' check before a play */
unsigned catalogue_song_key(void *r, unsigned key); /* plays and hearts: a copy is its row */
int catalogue_row_fav(void *r); /* a copy's My Fav by its row: 1, 0, -1 for no copy */
void catalogue_row_unfav(void *r), catalogue_fav_changed(void);
unsigned catalogue_fav_gen(void);
char *catalogue_union(const char *sql), *catalogue_search(const char *sql);
const char *catalogue_rows(void); /* library_query's union, "" without one */
int catalogue_filling(void); /* a signed-in app's rows on their way, none shown yet */
void catalogue_attach(void **slot), catalogue_attach_db(void *db), catalogue_poll(void);
void catalogue_card_away(void);
int catalogue_card_release(void), catalogue_away(void); /* card_release.c's: the card let go of, */
void catalogue_card_back(void);                          /* and back */
void catalogue_rated(void *r, int stars), catalogue_liked(void *r, int on);
int catalogue_signed(char *id, char *name, unsigned size); /* a catalogue app signed in */
/* catalogue.c: a Library item's server identity for Sync Offline (offline.c), by OFF_* kind */
enum { OFF_SONG = 1, OFF_ALBUM, OFF_ARTIST, OFF_PLAYLIST };
int catalogue_offline_key(int kind, void *r, int list, char *app, char *key, unsigned n);
void offline_np_row(void *win, void *view); /* offline.c: Now Playing's ⋯ row */
/* cf_empty.c: Cover Flow's page with no albums, by what is missing; 0 under a theme without it */
int cf_empty(void *page, void *body);
/* library_settings.c: the server library's state for card apps (/tmp/q2-addon.library): always
 * "server" */
void library_source_file(void);
/* a server playlist changed on the Q2: its songs (PLS_SET), its name, or its deletion, to its app
 */
enum { PLS_SET, PLS_NAME, PLS_DELETE };
void catalogue_playlist_sync(int list, int what, const char *name);
void catalogue_playlist_covers(void);              /* read once per Playlists list built */
int catalogue_playlist_cover(int list, char *url); /* a server playlist's cover, url 600 bytes */
void catalogue_bind(void *row, unsigned index), catalogue_mark(void *row, void *r, int tab);
void catalogue_forget(void *row), catalogue_grey(void *w, int on);
int catalogue_greyed(void *w, unsigned *color), catalogue_row_ink(void *row, unsigned *ink);
int catalogue_greys(void), catalogue_row_greyed(void *row);
#define CAT_MARK_W 20 /* the offline mark's ink, catalogue_mark_paint centres it on its x */
void catalogue_mark_paint(void *canvas, int x, int y, unsigned ink);
void catalogue_row_mark(void *w, void *canvas, void *row, int x, int y);
void catalogue_fit(void *entry, void *surface, int limit);
int catalogue_np_info(const char *path, char *genre, int *year);
int catalogue_cover(const char *path, char *out, unsigned size);
int catalogue_cover_name(const char *path, char *out, unsigned size);
int catalogue_cover_large(const char *path, char *out, unsigned size);
/* catalogue.c: a Stream Cache copy's record takes its server row's title, artist and album (1) */
int catalogue_copy_tags(void *r);
/* catalogue.c: whether two paths play one song (song_radio.c) */
int catalogue_same_song(const char *a, const char *b);
/* catalogue.c: Now Playing shows r, not yet the player's, at its place at of n (addon.c) */
void catalogue_np_show(void *r, unsigned at, unsigned n);
/* tags.c: a card app's song's record named before it plays, from its server row or its file */
void card_record_tags(void *r);
/* Now Playing while a pressed server song loads: the record it shows, its title and artist, its art
 */
void *catalogue_np_now(unsigned *at, unsigned *n);
void catalogue_np_text(void *win);
const char *catalogue_np_cover(void);
extern unsigned id3_parses;          /* tags.c: the player's fresh parses (ringnav_id3_path) */
int ringnav_row_selected(void *row); /* navigation.c: row holds the painted selection */
void *ringnav_search_rows(void *win, int *tab); /* navigation.c: Search's results, while shown */
extern unsigned auto_nexts;    /* playback.c ringnav_next: tracks that ended by themselves */
int stream_cache(int spotify); /* stream_config.c: Streaming's Configuration, a cap of 512 << it MB */
void album_memory(void *r);
void draw_centred(void *canvas, const unsigned *s, unsigned n, const void *r, unsigned px,
                  unsigned color);
unsigned utf8_char(const unsigned char *t, unsigned n,
                   unsigned *i); /* lyrics.c: UTF-8, else Latin-1 */
int utf8_text(const char *s);    /* xiph.c: valid UTF-8, a 4-byte character too */
/* Lyrics as lyrics.c's loader reads them: each line's start (ms, LRC_FAR unsynced), the order it
 * came in, its UTF-8 text and its word stamps (A2's <mm:ss.xx>), each the ms a word starts and its
 * byte offset in the text. */
#define LRC_FAR 0x7fffffff
typedef struct {
    int ms;
    unsigned at;
} lrc_mark;
typedef struct {
    int time, at;
    const char *text;
    unsigned mark, marks;
} lrc_line;
/* lyrics.c, iPod: Now Playing's lyrics page, three large lines lit word by word (docs/internals.md#
 * word-timed-lyrics). lyrics_publish takes each load's lines; lyrics_rows turns stock's rows of lrc
 * into the page's (whether it is on, as lyrics_on asks), and lyrics_fit lays them out again at
 * lrc's new width; lyrics_now is the playing position in ms,
 * lyrics_sec its whole second (off the page), lyrics_line the playing line there (-1 before the
 * first), repainting it while page shows; lyrics_timed whether any line has a time, lyrics_row_ms
 * the time a tap on a row seeks to (-1: none); lyrics_paint draws a row's line, lyrics_fade OLED's
 * faded edges over the view. */
void lyrics_publish(const lrc_line *l, unsigned n, const lrc_mark *m);
void lyrics_ask_poll(int front); /* a server song's lyrics asked of its app (addon_lyrics_ask) */
int ringnav_lyric(void);
int lyrics_rows(void *lrc), lyrics_fit(void *lrc), lyrics_on(void *lrc), lyrics_now(void),
    lyrics_row_h(void),
    lyrics_row_h(void),
    lyrics_sec(void), lyrics_line(void *lrc, int ms, int page), lyrics_timed(void *lrc),
    lyrics_row_ms(void *lrc, int i);
void lyrics_paint(void *row, void *canvas), lyrics_fade(void *lrc, void *canvas), lyrics_gone(void);
/* np_rate.c: rows y to y + n of w's from Now Playing's glow at alpha a; 0 when there is none */
int np_glow_rows(void *w, void *canvas, int y, int n, unsigned a);
unsigned accent_tone(int tone); /* iPod: the Accent's ACCENTS column  */
unsigned rgba(unsigned rgb, unsigned alpha);
/* const and pure below: each one's result rests on its arguments (and pure's, memory) alone, so
 * clang can keep one call for several (theme_engine.c and navigation.c define these). */
unsigned mix(unsigned from, unsigned to, int j, int n) __attribute__((const));
void caption(void *canvas, const char *s, int x, int y, int w, int h, unsigned px, unsigned color);
void peq_paint(void *w, void *canvas);
/* spotify.c's, shared with radio.c */
unsigned long long now_ms(void); /* CLOCK_MONOTONIC */
void field(const char *raw, const char *key, char *out, unsigned n);
unsigned long long number(const char *raw, const char *key);
unsigned state_read(const char *path, char *raw, unsigned size);
struct unix_addr { /* sockaddr_un */
    unsigned short family;
    char path[108];
};
void dgram_send(int *sock, const char *path, const char *c, unsigned n);
int hex(char c); /* a hex digit's value, -1 for any other character; headset.c's too */
void *label(void *parent, int x, int y, int w, int h, const char *style, int px, unsigned color);
void spot_yield(void), spot_stop(void), radio_stop(void);
void headset_volume_reset(void);
int vol_next(unsigned key); /* volume.c: the volume a wheel tick moves to, at the wheel's speed */
/* bt_vol_limit.c: a Bluetooth device's Volume Limit and Volume Curve
 * (docs/internals.md#headset-controls-and-volume); vol_cap is the volume's top now, Max Volume and
 * over Bluetooth the device's limit */
int vol_cap(void), btlim_get(const char *mac), btlim_departing(int v), btlim_entering(int v);
int btlim_edit_get(void);
void btlim_poll(void), btlim_saved(void), btlim_set(const char *mac, int v);
void btlim_forget(const char *mac), btlim_text(int v, char *s, unsigned n);
void btlim_edit(const char *mac), btlim_edit_take(int v), btlim_edit_save(void);
void btlim_edit_row(void *label);
/* ... and its Volume Curve, Link Volume's mapping onto the headset's 0 to 127 (playback_power.c
 * bt_vol_avrcp); vol_gentle is the Bluetooth output's device's, 1 for Gentle */
enum { BTLIM_EVEN, BTLIM_GENTLE_CURVE };
#define BTLIM_CURVE_ROW "label_btcurve" /* the device page's Volume Curve value (bt_detail.c) */
int btlim_curve(const char *mac), vol_gentle(void), btlim_edit_curve(void);
void btlim_curve_set(const char *mac, int c), btlim_edit_curve_take(int c);
const char *btlim_curve_name(int c);
void bt_curve_open(void); /* library_settings.c: the Volume Curve picker for btlim_edit's device */
int wheel_level(int value, int lo, int hi, int full, int dir); /* levels.c: a wheel tick's level */
void headset_poll(void), headset_eq_poll(void), headset_manual(void);
/* loop_wake.c: any thread ends the UI loop's idle (docs/internals.md#the-ui-loops-wait); the touch
 * thread's per-sample form only while it idles */
void loop_wake(void), loop_wake_touch(void);
/* guest.c: Guest Play's side in demo (docs/internals.md#guest-play) */
int guest_send(const char *line), guest_active(void), guest_key(int code);
unsigned guest_seq(void);
void guest_field(const char *key, char *out, unsigned n), guest_poll(void), guest_bt_on(void);
int guest_net(const char *kind, const char *id, const char *name);
void spot_send_play(int playing);
void q2eq_poll(void); /* peq_ui.c, --dev builds only: a q2eq request (q2eq.h) */
void q2update_poll(void); /* sw_update.c, --dev builds only: a q2update request (docs/adb.md#installing-an-update-over-adb) */
int media_owner(void), media_status(void), media_playing(void), media_focused(void);
int usb_otg_forbid_resume(void); /* usb_otg.c: the gate for a Play of the song there, kept while USB audio connects */
void media_set_playing(int playing), media_key(unsigned key);
void video_key(unsigned key);
int radio_media(unsigned key), spot_media(unsigned key);
int spot_np_track(void *win); /* spotify.c: win is Spotify's Now Playing with a track on it */
int headset_gain(void);
int spot_status(void), radio_status(void), video_status(void);
unsigned spot_session(void), radio_session(void), video_session(void);
int radio_now(char *station, char *title, unsigned cap);
int spot_now(char *title, char *artist, char *album, unsigned cap, unsigned *pos, unsigned *len);
int now_time(int *sec, int *total); /* now_source.c: the Library's second, length and play state */
/* now_source.c: the Library's song held paused where it was while Radio, Internet Radio, a video or a
 * sound game has the output, given back paused, played on, or forgotten for a start of its own */
void now_hold(void), now_drop(void), now_take_back(void), now_starting(void);
int now_release(void); /* 1 when a held song was given back */
int play_hold_released(void); /* navigation.c: Play/Pause's release ends a hold that fired */
int radio_take_back(void *top, unsigned key); /* radio.c: Play on the Library's Now Playing */
void spot_set_playing(int playing), radio_set_playing(int playing), video_set_playing(int playing);
int video_start(const char *file, int radio); /* videos.c */
/* radio_tuner.c: Radio's receiver, 0 none, 1 playing, 2 paused (its page shut or not); played or
 * paused, or a media key, from a headset, the car or Car Remote (headset.c); ended for the Library
 * (tuner_stop: any start of the Library's player, ringnav_start_player; tuner_yield, a pick,
 * playback.c ringnav_load, its held song dropped too); its page (tuner_open) */
int tuner_state(void), tuner_open(void *ctx, void *event);
int tuner_dial(void); /* ...the dial's frequency, 10 kHz, 0 with no receiver */
/* radio_pane.c: Home's art pane while Radio is Now Playing (coverflow.c): a frame of it into an
 * RGBA8888 pane-sized buffer (rp_frame; 0 out of memory), the tower and the frequency over it
 * (rp_words, where rp_frame put them), the way in over the cover it replaces (rp_enter), whether it
 * moves (rp_moving), its layers freed (rp_free) */
#define RADIO_TOWER_PX 39 /* tools/art.py RADIO_TOWER_PX */
typedef struct {
    int off, badge_a, badge_dy, freq_a, freq_dy, cx, cy;
} rp_words_t;
int rp_frame(unsigned *d, int pitch, int w, int h, int top, unsigned now, rp_words_t *words);
void rp_words(void *w, void *canvas, const rp_words_t *k);
void rp_enter(const unsigned *cover, int w, int h), rp_free(void);
int rp_moving(void);
void tuner_set_playing(int playing), tuner_media(unsigned key), tuner_stop(void), tuner_yield(void);
/* output_fail.c: a song's output that failed said once, and a run of songs that will not play ended
 * (docs/internals.md#output-failure) */
void output_fail_poll(void), output_fail_skipped(void), output_fail_start(void);
int output_fail_skip(void);
/* peq_ui.c's T9 edit, named EDIT_NAME: navigation.c's search_key cancels it on Return while its
 * keyboard is up. */
#define EDIT_NAME "q2_edit"
void *edit_create(void *item, const char *input_type, int y, int h);
/* The letter strip's return key's mark (search_strip.c ss_ret_kind), by what submitting the text
 * does: an edit's STRIP_RETURN int prop, or search_strip_return for search_strip_text. STRIP_AUTO
 * (unset) leaves it to the strip: a magnifier on its query line, else the arrow. */
#define STRIP_RETURN "_strip_return"
enum { STRIP_AUTO, STRIP_GO, STRIP_SEARCH, STRIP_SAVE };
const char *output_device(char *vol); /* videos.c */
void dac_take(void);                  /* videos.c */
/* navigation.c: the theme's font for role on w, or stock's; canvas text's face, 0: the system
 * font */
int theme_font(void *w, int role, int was);
int face_font(void *w, int face, int px, int was); /* ... a FONT_* face at px (0: the style's) */
#define FONT_ROW -2             /* was: cache the answer in w's own "_font" prop */
void marq_restart(void *row);   /* navigation.c: its title changed, its marquee re-armed */
void list_stop_scroll(void *w); /* navigation.c: its scroll animation stopped */
const char *theme_face(const unsigned *s, int n, int *px); /* *px, unless 0: the px to draw it at */
void bar_title_sync(void *win); /* navigation.c: the bar's title names win */
void forget_rows(void *list);   /* navigation.c: a hidden list opens at its top next time */
/* navigation.c's painters; colours are final. */
void paint_art(void *w, void *canvas, const char *name, int x, int y);
void final_text(void *canvas, const char *face, const unsigned *s, const void *r, unsigned px,
                unsigned color);
int bar_status(void);
void slider_label(void *w, int x, int y, int ww, int h, int px, const char *face);
void text_ink(void *w, unsigned color, int n); /* a label's final 0xAARRGGBB, in either half */
/* navigation.c: a feature settings row's "Title: value", on the set's right column where it has one
 */
void row_value_text(void *label, const char *text);
void slider_page_dirty(
    void *page); /* slider_page.c: its switch row, after the hidden switch changes */
void stock_more_speed(void *win);
int xfade_page(void *top);                       /* crossfade.c */
int xfade_volume_shown(void *top);               /* crossfade.c */
int xfade_open(void *ctx, void *event);          /* crossfade.c: its slider page, by row */
int night_page(void *top);                       /* crossfade.c */
void xfade_ends(char *lo, char *hi, unsigned n); /* crossfade.c: its page's range ends */
/* slider_page.c: EQ's Simple tab as a set_switch theme draws it (docs/oled-design.md Progress and
 * sliders): rows under the tabs, then the bands' vertical sliders, in the page's coordinates */
#define EQ_TABS_H 48 /* the tab bar (peq_ui.c bar) */
#define EQ_ROWS_Y EQ_TABS_H
#define EQ_ROW 32          /* the switch, Presets and Reset */
#define EQ_SLIDERS_TOP 150 /* a band's travel, +max dB to -max */
#define EQ_SLIDERS_BOTTOM 186
#define EQ_NAME_Y 200 /* the band's name's and gain's centres */
#define EQ_GAIN_Y 216
typedef struct {
    const char *preset;       /* Presets' value: the PEQ preset playing, else 0 */
    int on, focus, adjusting; /* focus: 0 the tabs, 1 the switch, 2 Presets, 3 Reset, 4.. bands */
    double gain[3], max;
} eq_rows_t;
int eq_rows(void);
int eq_list_end(void); /* the page's rows end here (the set's list_end) */
int list_fit_end(void); /* theme_engine.c: the lists' end on the screen (the grid's list_end) */
void eq_rows_paint(void *w, void *canvas, const eq_rows_t *e);
void eq_tabs_paint(void *w, void *canvas, int simple, int focus);
/* slider_page.c: an iOS segmented control of n segments, w by h from x, y; open is lit */
void segments_paint(void *canvas, const char *const *names, int n, int open, int focus, int x,
                    int y, int w, int h);
int title_gone(void *w, void *event); /* ... and looks for its source again at the next sync */
int bar_title_fits(void *label);      /* label's text fits the bar's title box */
int ipod_light(void);                 /* theme_engine.c: Light Mode */
unsigned unflip(unsigned v);          /* theme_engine.c: the grey Light Mode's flip turns into v */
/* corner_cover.c: a radius r corner's anti-aliased coverage, r rows of r from the outermost, out
 * of 255; 0 for r < 1 (docs/internals.md#rounded-corners) */
const unsigned char *corner_cover(int r);
#define SPOT_GREY 0xffaaaaaau
void radio_search(void);      /* radio.c */
void toast(const char *text); /* navigation.c */
void toast_later(const char *glyph, const char *text); /* feedback.c */
int wifi_level(void); /* status_row.c: Wi-Fi's bars, 0 not connected */
/* smart_wifi.c: its radio woken, 1 while its link comes up; its radio off; off or coming
 * back; an action held for that link (fn(ctx, 0) once it is up, if page, or any page when 0,
 * is still in use) */
int smart_wifi_wait(void), smart_wifi_off(void), smart_wifi_back(void);
int smart_hold(int (*fn)(void *, void *), void *ctx, const char *page);
int need_link(int (*fn)(void *, void *), void *ctx); /* smart_hold, else "Connect to Wi-Fi first" */
/* the work smart_wifi.c keeps the radio on for: spotify.c's, scrobble.c's, tidal.c's, addon.c's,
 * netupdate.c's and onlinematch.c's */
int spot_running(void), scrobble_busy(void), tidal_busy(void), addon_running(void),
    netupdate_busy(void), album_lyrics_busy(void);
/* netupdate.c: Software Update's online half (sw_update.c draws its page): the check's answers,
 * the download's states (netupdate_poll's); sw_update_open opens the page (General's row) */
enum { NET_NEWER = 1, NET_LATEST, NET_FAILED };
enum {
    NU_IDLE,
    NU_RUNNING,
    NU_DONE,
    NU_LATEST,
    NU_NET,
    NU_MISSING,
    NU_CARD,
    NU_CANCELLED,
    NU_UNSIGNED,
    NU_FORGED
};
int netupdate_check(char *tag, unsigned n, unsigned *kb);
int netupdate_start(void), netupdate_poll(int *percent, int *verifying);
void netupdate_cancel(void);
const char *netupdate_said(int state);
int sw_update_open(void *ctx, void *event);
/* coverflow.c: Display > Cover Flow's Sort, its value and its picker's choices */
const char *coverflow_sort_name(void), *coverflow_sort_at(int i);
int coverflow_sort_count(void), coverflow_sort_now(void);
void coverflow_sort_set(int i), coverflow_refresh_art(void);
void coverflow_clean_end(void); /* coverflow.c: a clean end mid-build strikes no album */
/* Playlists as Cover Flow: coverflow.c's covers of lists (p_deque_playlist), 0 when it has none,
 * its centred playlist (a cover's hold) and its close of the covers; playlists.c's open of playlist
 * cd_num from its row on the Playlists page win under them, its index in the live list, and New
 * Playlist on that page. */
int coverflow_playlists(void *lists);
int coverflow_playlist_queue(void); /* the queue a playlist's cover played, no load since */
void *coverflow_playlist(void *page), *coverflow_playlists_close(void);
void playlist_open(void *win, int cd_num);
unsigned playlist_index(int cd_num);
int rockbox_available(void);   /* theme_engine.c: a regular Rockbox binary on the card */
int rockbox_open(void *ctx, void *event); /* playback_power.c: General's Rockbox row, the hand-over */
int page_keyup(void *ctx, void *event); /* navigation.c: Return leaves a payload page */
int ringnav_localclass(int cls);        /* navigation.c: load_localclass_list's hook */
int shelf_open(void *ctx, void *event); /* netupdate.c: System settings' Apps page */
const char *shelf_busy(void);           /* netupdate.c: the app folder a job replaces */
const char *shelf_moving(void);         /* netupdate.c: the app folder deleted or moved into */
int shelf_erase(int start);             /* netupdate.c: Spotify's erase, started or running */
void stream_erased(void);               /* stream_config.c: Configuration follows a finished erase */
void album_lyrics_stop(void); /* onlinematch.c: Get Album Lyrics before USB storage */
void coverflow_art_stop(void); /* coverflow.c: Cover Flow's art build, before the card goes */
/* release_sig.c (docs/internals.md#release-signatures): the release key's checks */
enum { SIG_OK, SIG_BAD, SIG_IO, SIG_CANCELLED };
void sig_hex(const unsigned char *in, unsigned n, char *out);
int sig_unhex(const char *s, unsigned char *out, unsigned n);
int sig_hash_file(const char *path, int sha512, unsigned char *out, volatile int *cancel);
unsigned sig_message(char *out, unsigned n, const char *kind, const char *tag,
                     const unsigned char digest[64]);
int sig_check(const char *message, unsigned n, const unsigned char sig[64]);
int sig_update(const char *path, const char *tag, const unsigned char sig[64],
               volatile int *cancel);
unsigned sig_list(const char *text, unsigned n);

/* 2038 (docs/internals.md#2038). glibc 2.28's time() truncates the kernel's 64-bit seconds to 32
 * bits, so read unsigned it is right until 2106; -1 still means it failed. */
static inline long long wall_time(void) {
    long t = time((void *)0);
    return t == -1 ? -1 : (long long)(unsigned long)t;
}
/* A wall time before this (2020-09-13) is a clock never set: scrobbling's test, Battery History's.
 */
#define CLOCK_SET 1600000000u

#endif
