#ifndef Q2_NAVIGATION_H
#define Q2_NAVIGATION_H
/* navigation.c's shared state, types and macros, and the functions each file of tools/build.py's
 * NAVIGATION calls in another. */
#include "chapters.h"
#include "offsets.inc"
#include "peq.h" /* libc/libcstl imports (deque_*, send) and the shared helpers */
#include "wheel_accel.h" /* the wheel's momentum on a long list */
#include "playback.h"
#include "stock.h"
#include "themes.h" /* tools/theme.py: the themes' enums and macros; theme_engine.c has the tables */
#include "xiph.h"
#undef select /* navigation.c's UI selection helper; headset.c uses libc select */

#define STOP 11
#define MAX_ENTRIES 512
#define POS_MEM 64
/* Centre or a finger held this long: Edit Library's editor (library_edit.c), Radio's stations list
 * (radio_tuner.c); either removes stock's own Centre hold (Shut Down? at 1000 ms, st.centre_hold) */
#define LR_HOLD_MS 550
#define B(p, o) (*(unsigned char *)((char *)(p) + (o)))

typedef struct {
    int ctx, id; /* logical row + 1; 0 = unused */
    unsigned scope, hash, hash2;
} position_t;

/* The settings rows pod_rows builds (pod_values and the Q2POD keys below). */
enum {
    POD_CHARGE,
    POD_WAKE,
    POD_ARTIST_CF,
    POD_YEAR_OLD,
    POD_ARTISTS,
    POD_RESUME,
    POD_ARTIST_AZ,
    POD_HOLD_HOME,
    POD_STANDBY,
    POD_BT_LINK,
    POD_CF_SORT,    /* Display's Coverflow Sort: coverflow.c's SORT_FILE, not config.ini */
    POD_CF_REFRESH, /* and Refresh Coverflow, an action */
    POD_EQ_OUTPUT,  /* EQ per Output: the value and key; its row is Advanced EQ's (peq_ui.c) */
    POD_KEYTONE, /* Key Tone and In-Vehicle Mode: stock's bytes and SYSSET keys, as its rows did */
    POD_CARMODE,
    POD_SMART_WIFI,
    POD_COVERMODE, /* Enlarged Album Art and Folder Album Cover: stock's bytes and PLAYSET keys, as
                      its rows do */
    POD_FOLDERCOVER,
    POD_BIG_LIB, /* Display's Fast Navigation: Cover Flow's letters (ringnav_big_library) */
    POD_PL_CF,   /* Display's Playlist View: List or Cover Flow, as Artist View */
    POD_SCAN_DURATIONS,
    POD_CF_POSITION,
    POD_OTG,      /* Library's On-The-Go (playlist_pages.c), on unless chosen */
    POD_AUTOZONE, /* Automatic Time Zone (autozone.c), on unless chosen */
    POD_COUNT
};

/* Writable state lives in the zero-filled page the builder maps past the payload text.
 * center_timer defers confirmation; wheel_* scale repeated fast detents; pos_* remember
 * the selected row per audited context across page recreation; reveal_* pin the recall glide so
 * an interruption cannot silently rewrite that memory. */
typedef struct {
    unsigned last_center; /* release time of the pending selection press */
    unsigned center_timer, center_token, center_scope, center_hash, center_hash2;
    unsigned edit_press, edit_at; /* a slider or time editor's pending Centre (edit_key) */
    int center_id, center_ctx, center_rows;
    unsigned last_home;
    int home_dir;
    void *home_surface;   /* non-null also marks a step accepted at time zero */
    void *center_top;     /* top window of that press */
    void *center_surface; /* navigation surface of that press */
    unsigned last_wheel;  /* time of the previous wheel detent */
    int wheel_dir;        /* direction of that detent */
    unsigned wheel_run;   /* continuous same-direction spin ms + 1; capped at full speed */
    int touch_mode;       /* session-wide drawing preference, independent of selection */
    void *wheel_top, *wheel_surface;
    unsigned wheel_scope;
    int wheel_ctx;
    /* iPod: a Return by button ends touch mode once stock has handled it */
    unsigned untouch_timer;
    /* iPod: the row pressed_pill last drew grey, and the repaint once it is released */
    void *press_row;
    unsigned press_timer;
    /* wheel ticks held while a page slides or loads (hold_wheel), replayed into it */
    int held;
    unsigned held_at, held_timer;
    void *held_ctx;
    unsigned held_event[12];
    position_t pos[POS_MEM]; /* most recently selected first; keyed by context and scope */
    void *reveal_surface;    /* surface of the interrupted recall glide, 0 when none */
    int reveal_id;           /* logical row that glide was bringing into view */
    /* Resume: the saved places, file seconds by path hash, most recent first; the track polled
     * (rs_key, listen_key's, 0 none) and whether it is long, its last seconds, the seconds last
     * saved and how many polls they stood still; rs_pending is the place to restore once it plays,
     * rs_settle a track change one poll old. rs_file is its path's hash, rs_cue and rs_end its CUE
     * start and end, rs_total its seconds; rs_go is the seconds into rs_go_key's chapter a Resume
     * row asked for at rs_go_at. */
    struct {
        unsigned key;
        int sec;
    } spots[RESUME_SLOTS];
    unsigned spots_read, rs_at, rs_key, rs_file, rs_go_key, rs_go_at;
    int rs_long, rs_settle, rs_sec, rs_saved, rs_still, rs_pending, rs_cue, rs_end, rs_total, rs_go,
        rs_stopped;
    int rs_spoken; /* under Podcasts or Audiobooks: resumed at any length, never counted */
    /* Play counts by path hash (plays_read once loaded); the playing track's last second, the
     * seconds of it heard, whether this play has counted and been scrobbled, and .scrobble.ini's
     * PERCENT (1-100, else 0) as it read when the play began. */
    struct {
        unsigned key, n;
    } plays[PLAYS_SLOTS];
    unsigned plays_read, ls_key;
    int ls_sec, ls_heard, ls_done, ls_sent, ls_pct;
    /* Most Played's page and its tracks: the last ranking plus the songs counted since, kept across
     * opens while the library is at library_lists.c's mp_gen; the hold menu reads them too
     * (page_tracks). */
    void *mp_page, *mp_list;
    void *ch_page;
    chapters *ch;
    unsigned ch_timer, ch_track, ch_key;
    int ch_cue, ch_end; /* the Chapters track's CUE start and end, file seconds */
    /* Streaming's Configuration (stream_config.c): its page, the provider page it opened and whose
     * (sc_of: 0 TIDAL, 1 Spotify), the TIDAL and Spotify rows' labels, Download Ahead's label, the
     * provider page's Cache and Erase Spotify Data labels; each 0 while its page is closed */
    void *sc_page, *sc_sub, *sc_prov[2], *sc_ahead, *sc_cache, *sc_erase;
    int sc_of;
/* Streaming's services (svc): five fixed, and at most APPS_MAX card apps. */
#define SVC_FIXED 5
#define SVC_MAX (SVC_FIXED + APPS_MAX)
#define SVC_HIDE 260 /* STREAMHIDE, "key,key" */
#define SVC_PROP "ringnav_svc"
    void *sc_check[SVC_MAX]; /* the Configuration pages' switch labels, by service, while shown */
    int svc_n;               /* the card apps Streaming listed at its last build */
    /* Ratings (ratings.c keeps the rest): Now Playing's window; the page a Ratings list or Long
     * Songs opened, with its tracks (page_tracks). */
    void *rate_win;
    int empty_kind; /* the empty Library list empty_class closes and opens the empty state for */
    void *rt_page, *rt_list;
    int dark; /* 1: the backlight was off at the last UI loop pass; 2: a centre press woke it */
    /* Power's Charge limit (read once from config.ini's Q2POD), the value labels of that row
     * and Artists, and the poll: charging is held off at the limit;
     * last_input times the screen-on idle. year_old: Year order, oldest first. resume:
     * Bluetooth's Resume on connect on. hold_home: Hold Return goes Home. standby_limit: Standby
     * limit's choice.
     * smart_wifi: Power's Smart Wi-Fi on (smart_poll). big_library: Display's Fast Navigation
     * on. playlist_cf: Playlists open as Cover Flow. */
    int pod_read, charge_limit, wake_double, wake_pass, wake_eat,
        wake_down, /* 1: the held Centre press passes; 2: it is dropped */
        charge_held, artist_cf, year_old, resume, artist_az, hold_home, standby_limit, eq_output,
        smart_wifi, big_library, playlist_cf, scan_durations, cf_position;
    int wake_return;       /* ... a waking Return press, its long press and release to come */
    void *pod_label[POD_COUNT];
    char art_cf_hint[64]; /* the page cf_veil hides: its anim_hint */
    unsigned wake_at; /* Wake: Double press, the screen-off centre press a second one must follow */
    unsigned woke_at; /* the centre press that woke the screen, then its first lit loop pass */
    unsigned return_at;          /* chord_gate: Return's press while held, else 0 */
    int chord_eat;               /* 1: the held Centre press is dropped; -1: it passes */
    unsigned chord_at;           /* the last Centre event chord_gate saw */
    unsigned return_up;          /* chord_gate: a hold's release, else 0 */
    unsigned edge_held, edge_at; /* chord_gate: edge buttons held (bits), the last one's press */
    unsigned edge_seen;          /* chord_gate: an edge button's last press or repeat */
    unsigned centre_at, centre_hold; /* a passing Centre press, and its hold timer */
    int return_eat;                  /* 1: a ghost Return press is dropped */
    unsigned return_eat_at;
    void *np_origin; /* the page Hold Return opened Now Playing over */
    void *dc_top;    /* the page of the last centre press no list click took, and its time */
    unsigned dc_at;
    unsigned charge_at, last_input;
    unsigned touch_at; /* the last pointer event's time, type and place (stale_heal, stale_note) */
    int touch_type, touch_x, touch_y, stale_notes;
    int toasts; /* toast_for's calls under way, a swapped one's too, for the stall record (stall_write) */
    void *pull_page, *pull_surface;
    int pull_x, pull_y, pull_claimed;
    unsigned pull_scope;
    void *sel_w; /* the surface whose selection was last drawn */
    int sel_row, sel_y, sel_h;
    void *paint_w;        /* paint-local color mapping, never stored on recycled widgets */
    unsigned clock_key;   /* minute + 1 and format bits; 0 before the first, ~0 for --:-- */
    unsigned clock_timer; /* clock_due, at the next minute; 0 until the bar first paints */
    int clock_full;       /* g_time24h_flag as clock_show last read it */
    /* The status bar's Bluetooth, Wi-Fi and battery widgets, looked up once (the bar is never
     * destroyed); the codec badge shown (index + 1 in BT_CODECS, 0 none), its fade step (0 showing,
     * CODEC_STEPS the glyph fading in, 2 * CODEC_STEPS done) and timer; the battery slot's last
     * level, charge and low state. The bar also has a hidden title, the page's name
     * (bar_title_sync), from the top window title_win and, when a label holds it, title_lbl (both
     * found once per window). */
    void *bar_bt, *bar_wifi, *bar_pct, *bar_slot, *bar_icon, *bar_idle, *bar_state, *bar_eq,
        *bar_title, *title_win, *title_lbl,
        *action_view, /* Songs' action bar on title_win (the set's action_row) */
        /* a shuffle_row theme: the action bar the wheel's selection is on (else 0), the list under
         * it, and which of its stops: SHUFFLE_STOP or SORT_STOP (navigation.c shuffle_key) */
        *shuffle_on, *shuffle_list;
    int shuffle_stop;
    /* The row action's confirm on show (confirm_over): its second cell's verb, and its window */
    const char *confirm_verb;
    void *confirm_win;
    int codec, codec_step, batt_mode;
    unsigned codec_timer, batt_key, bar_timer;
    int pct_digit, pct_sign; /* BATT_PX advances of '0' and '%', up a pixel, at the first paint */
    unsigned letter_timer;   /* the fast-scroll letter shows while this runs */
    /* Now Playing's window and payload-filled widgets, and the sources they last showed. */
    void *np_win, *np_album, *np_slider, *np_remain, *np_elapsed, *np_cover, *np_title, *np_artist;
    int np_meta[2];          /* face_font's answers for np_artist and np_album, -1 none yet */
    int np_bold;             /* the title's font: theme_font's face | px << 2, -1 not set yet */
    void *np_slide, *np_lrc; /* the art and lyrics pages and the lyric lines' scroll_view */
    /* Now Playing's full-screen lyrics (np_full) and where the last touch on them came down
     * (np_lyric_touch) */
    int np_full, np_tap_x, np_tap_y;
    int np_time_down; /* np_time_tap: a touch came down on the right-hand time */
    /* np_bar.c's shuffle and repeat: shuffle kept under Repeat One, repeat Off chosen under shuffle
     * (saved as NPSHUFONE and NPREPOFF), and whether np_modes_read has read them */
    unsigned char np_shuf_one, np_rep_off, np_modes_read;
    int np_was[9]; /* the column before full screen (np_full) */
    /* The full-screen art's song name, its shade and its window, the fade's step and timer
     * (ringnav_bigcover) */
    void *bc[3];
    int bc_step;
    unsigned bc_timer, bc_track, bc_repaint;
    unsigned bc_key, bc_box, bc_shape,
        bc_url; /* the track, cover and image bc_fit sized for, the box
                 * it left, the image's w << 16 | h and its track */
    /* The scan's dialog (msg_open, scan_page.c). usb_lun: USB Storage's card has read open
     * (usb_poll). */
    void *msg_win;
    int usb_lun;
    unsigned np_hash; /* of the album text, position and queue length shown, 0 to refresh */
    unsigned np_art;  /* the track and player cover img_cover last took (np_art_poll) */
    int np_left;
    /* np_bar.c paint_scrubber's glide: the slider value it runs on from, since when, whether it was
     * playing, and its repaint */
    int np_glide_v, np_glide_play;
    unsigned np_glide_at, np_glide_timer, np_glide_due;
    /* The info page's Genre line: its label, np_info's copy (genre_fill), and px scrolled. */
    void *genre_label;
    int genre_off;
    unsigned genre_timer;
    /* The selected row's marquee (marq_moved). */
    void *marq_row, *marq_label;
    unsigned marq_timer;
    int marq_id, marq_passes;
    /* Scrub: scrub_to is the target second, scrub_moved set once the wheel changed it. */
    unsigned scrub_timer, scrub_track, settle_timer;
    /* Now Playing's Centre cycle (np_centre): its stage (NP_STAGE_*), the stage's timeout, and when
     * Centre's press went down (ringnav_keydown; its repeats don't move it), which tells a short
     * hold from a press */
    unsigned np_stage, np_stage_timer, center_down_at;
    int scrub, scrub_to, scrub_moved, settle_played;
    int held_sec; /* a paused seek to scrub_to on scrub_track: the player's second then, +1; 0 none
                   */
    /* The lyrics (lyric_poll), of the song lyric_song, their rows built for lyric_rows_song:
     * lyric_raw while their page holds the wheel's raw travel; lyric_held once the wheel or a drag
     * moved them, lyric_held_at the last wheel or touch on them since (the hold's LYRIC_RESUME_MS
     * runs from it); lyric_spun once the wheel moved them since it was let go (2: only nudged them,
     * still following); lyric_touch while a
     * finger is on Now Playing, 1 if it came down on the lyrics, lyric_drag_y the offset then; the
     * glide's target lyric_to (1/16 px), its last step lyric_at, and lyric_glide while it moves;
     * lyric_follow_at the last follow. */
    int lyric_raw, lyric_held, lyric_spun, lyric_touch, lyric_drag_y, lyric_glide, lyric_to;
    int lyric_push, lyric_home; /* travel short of LYRIC_PUSH_DETENTS, and where it nudged from */
    unsigned lyric_push_at;
    unsigned lyric_at, lyric_follow_at, lyric_song, lyric_rows_song, lyric_held_at;
    int lyric_seek; /* a tap's seek to a line (np_lyric_line): its start + 1, ms; 0 none */
    unsigned lyric_seek_at; /* when it was sent, or last seen paused (lyric_seek_ms) */
    /* The Display settings, read from config.ini on first use, and the display page's value labels.
     */
    int settings_read, accent, home, battery, light,
        light_day,       /* light_day: Auto's answer for now (LIGHT=2) */
        keep_lit,        /* Smart Awake (#57, keep_lit_poll) */
        reduce_motion,   /* Reduce Motion, IPOD REDUCEMOTION (ui_kit.c motion_reduced) */
        clicker,         /* the Clicker's output (clicker.c), IPOD CLICKER */
        theme_booted,    /* theme_boot ran (ringnav_ui_load) */
        raw_ink;         /* a final colour's draw: ui_ink leaves it alone */
    int own[THEME_KEYS]; /* the theme's own keys' values (theme_keys[], tools/theme.py), read after
                            the payload's */
    unsigned keep_lit_at;
    void *sel_accent; /* the row on a coloured selection, for Light Mode's ink */
    int style_depth;  /* nested style hooks (a mutable style's fallback) map nothing */
    void *setting_label[7 + THEME_KEYS]; /* SETTING_OWN + THEME_KEYS */
    unsigned tone_key;  /* the wheel key whose press ringnav_keydown silenced, 0 when none */
    unsigned tick_drop; /* tick_gate: the wheel keys whose press it dropped, bits */
    unsigned tick_at;   /* tick_gate: the last tick it passed (ringnav_sleep's dark turn) */
    int greeted;        /* the first reachable list got its boot repaint */
    /* Queue menu: the hold's AWTK press time marks its release; the target is a track/list row
     * checked by count, record and browsing-state hashes; qm_cls is the class its list holds;
     * qm_forced is a shuffle Play next. */
    unsigned long long qm_press;
    unsigned qs_hold; /* Return's hold closed Quick Settings then, 0 once acted on (quick_poll) */
    unsigned qm_timer, qm_cls, qm_idx, qm_rows, qm_hash, qm_browse, qm_forced, qm_forced_hash;
    int qm_kind, qm_action, qm_choice;
    char qm_name[96]; /* a playlist cover's name, the covers' own: the live list may be older */
    void *qm_dialog, *qm_queue, *qm_playlist;
    unsigned qm_queue_hash;
    unsigned char qm_classinfo[912]; /* g_local_classinfo_save before a Go to */
    /* Podcasts/Audiobooks: folder_page opens at media_root once media_open is set (else at the
     * card, /mnt/mmc, with a card and no drive; otherwise the storage selector stays); while the
     * page is there, Back at that root leaves it. */
    int media_open;
    char media_root[32];
    /* Go to Folder: once set, folder_page opens at go_folder at g_folder_layer go_layer. */
    int go_layer;
    char go_folder[1024]; /* g_folder_path's size */
    void *root_label;     /* Playback settings' Folder Root row */
    void *album;     /* what an Albums row or Go to album opened (album_open), a one-record deque */
    char **art_keys; /* an artist's albums tab's art keys by row, 0 for a name's own
                        (ringnav_artist_list) */
    unsigned *art_ids; /* and its rows' album_id, 0 for an album not found */
    unsigned art_n;
} scratch_t;

typedef struct {
    int x, y, w, h;
} rect_t;
typedef struct {
    void *w, *at[MAX_ENTRIES];
    int id[MAX_ENTRIES], n, kind, rows, row, height, ctx;
    unsigned scope;
} menu_t;

extern scratch_t st;
extern menu_t g_menu; /* the one row list load() fills */

/* Outside NAVIGATION: stock's trampolines (tools/build.py) and the other payload files'. */
extern const char *track_name(char *buf, unsigned size, void *t);
extern void *staged(int (*query)(void *), void *arg, int *count);
extern int spot_media(unsigned key), spot_key(void *top, unsigned key),
    spot_open(void *ctx, void *event), spot_hold(void *top);
extern int radio_media(unsigned key), radio_open(void *ctx, void *event);
extern int games_key(void *top, unsigned key), games_open(void *ctx, void *event);
/* now_source.c: Now Playing for every reader (docs/internals.md#now-playing-source). source: who
 * owns playback; state 0 stopped, 1 playing, 2 paused (AVRCP's and iAP2's order); pos_ms and len_ms
 * -1 for none (a radio has neither); rec the Library's record (queue or pressed), 0 for another
 * owner; station a radio's name, else its frequency. */
enum { NOW_NONE, NOW_LIBRARY, NOW_RADIO, NOW_NETRADIO, NOW_SPOTIFY, NOW_VIDEO };
typedef struct {
    int source, state, pos_ms, len_ms, id, pressed;
    unsigned qpos, qlen;
    void *rec;
    char title[160], artist[160], album[160], station[64], path[400];
} now_info;
void now_get(now_info *n), now_poll(void);
int now_owner(void), now_status(void), now_scrobbles(void), now_time(int *sec, int *total);
/* stock's Now Playing update reads through these (tools/build.py NP_TIME_CALLS) */
int now_page_status(void), now_page_time(int *sec, int *total);
void *now_song(unsigned *at, unsigned *n, int *pressed);
void bt_cover_hold(int held); /* bt_cover.c */

/* radio_tuner.c: Home's Radio (docs/internals.md#radio-home) */
int tuner_key(void *top, unsigned key);
void tuner_keydown(void *top, unsigned key); /* ringnav_keydown: Centre's hold on Radio's tuner */
void tuner_paint(void *w, void *canvas), tuner_poll(void);
void tuner_title(const char *in, char *out, unsigned cap);
void tuner_song(const char *in, char *artist, char *song, unsigned cap);
int tuner_now(char *station, char *freq, char *title, unsigned cap), tuner_state(void);
extern int games_keydown(void *top, unsigned key);
/* search_strip.c: Search, the strip under a list.entry.ipod theme, else stock's dialog */
int library_search_open(void *ctx, void *event), search_strip_key(void *top, unsigned key),
    search_strip_hold(void *top), search_strip_owns(void *top), search_strip_open(void *top),
    search_strip_close(void *top);
void search_strip_paint(void *w, void *canvas), search_strip_down(void *top, unsigned key);
void search_strip_bar_ground(void *w, void *canvas);
/* keyboard.c: the input method's keyboard window, a bare one the letter strip takes */
void *kb_host_open(const char *name);
int kb_open_hint(void *kb, const char *name, const char *value);
int kb_host_bg(void *w, void *canvas);
int kb_host_border(void *w);
extern int stock_keyup_trampoline(void *, void *), stock_touch_trampoline(void *, void *),
    stock_output_trampoline(int), stock_write_config_trampoline(int, const char *, const char *),
    stock_paint_trampoline(void *, void *), stock_dispatch_trampoline(void *, void *),
    stock_keylong_trampoline(void *, void *), stock_paint_bg_trampoline(void *, void *),
    stock_vol_keyup_trampoline(void *, void *), stock_wm_keyup_trampoline(void *, void *),
    stock_showface_trampoline(void *), stock_playing_trampoline(void *, void *),
    stock_display_trampoline(void *, void *), stock_bigcover_trampoline(void *, void *),
    stock_localmusic_trampoline(void *, void *), stock_keydown_trampoline(void *, void *),
    stock_btvol_trampoline(int, int), stock_sleep_trampoline(void *),
    stock_color_trampoline(void *, void *, const char *, unsigned),
    stock_image_trampoline(void *, const char *, void *), stock_about_trampoline(void *, void *),
    stock_folder_trampoline(void *, void *), stock_folder_back_trampoline(void *, void *),
    stock_folder_top_trampoline(void), stock_folder_click_trampoline(void *, void *),
    stock_input_trampoline(void *, void *), stock_buzzer_trampoline(int),
    stock_localclass_trampoline(int), stock_detail_trampoline(int, void *),
    stock_artist_trampoline(int), stock_power_trampoline(void *, void *),
    stock_systemset_trampoline(void *, void *), stock_audioset_trampoline(void *, void *),
    stock_btrows_trampoline(void *, void *), stock_playlist_trampoline(void *),
    stock_stream_trampoline(void *, void *), stock_start_player_trampoline(void),
    stock_standby_trampoline(void), stock_device_standby_trampoline(void), stock_cardmount_trampoline(void),
    stock_usbmode_trampoline(void *, void *, void *, void *),
    stock_hscroll_start_trampoline(void *), stock_wifi_page_trampoline(void *, void *),
    stock_stream_row_trampoline(void *, void *), stock_netservice_row_trampoline(void *, void *),
    stock_online_match_trampoline(void *), stock_wifi_switch_row_trampoline(void *, void *),
    stock_wifi_network_row_trampoline(void *, void *), stock_wifi_rows_trampoline(void *),
    stock_wifi_checkstatus_trampoline(void);
extern int scrobble_ready(void), scrobble_start(void), scrobble_poll(int *sent),
    scrobble_flag(const char *key), scrobble_value(const char *key);
extern const char *now_tag(void *r, int field);
extern void *queue_now(unsigned *pos, unsigned *n);
/* album_cover.c: an album's cover from its songs, as a file:// URL, and the fetches behind it
 * (docs/internals.md#album-covers): album_cover_gen moves as one lands, album_cover_busy until */
int album_cover_url(void *const *songs, unsigned n, char *url, unsigned size);
unsigned album_cover_gen(void);
int album_cover_busy(void);
void now_cover_forget(void); /* coverflow.c: the player's cover files, from AWTK's cache */
/* addon.c: a card app's playlist session's songs not yet queued, Up Next's pending rows (records,
 * freed by addon_pending_free; 0 none), what they were taken from, and one of them tapped */
void **addon_pending(unsigned max, unsigned *n);
void addon_pending_free(void **rows, unsigned n);
void **addon_pending_one(const char *path); /* a pending row's song, its own record */
unsigned addon_pending_sig(void);
int addon_pending_play(const char *path);

/* navigation.c */
void pod_settings(void);
#define COUNT "_ringnav_count"
#define SEL "_ringnav_index"
enum { SLIDER_LIGHT, SLIDER_MAX, SLIDER_BALANCE, SLIDER_BOOT }; /* slider_page_open's kind */
#define ROUND_RIGHT 2
#define ROUND_LEFT 1
#define STYLE_KEY 48
#define MCL(a) (*(volatile int *)(a))
int clamp_step(int offset, int maximum, int delta);
void fill_box(void *canvas, rect_t *r, unsigned color, unsigned radius);
unsigned put_num(unsigned *s, unsigned v);
int clock_due(const void *info);
int bar_repaint(const void *info);
void clock_text(void *label, int negative, int t);
void *list_row(void *view, const char *icon, int (*click)(void *, void *), void *ctx);
/* offline.c: Sync Offline's row for a Library item (OFF_* kind, peq.h), its symbol and its press;
 * Now Playing's ⋯ row is peq.h's offline_np_row */
const char *offline_row(int kind, void *r, int list), *offline_icon(void);
void offline_press(void);
int offline_state(const char *app, const char *key);
void *list_row_core(void *view, const char *icon, int (*click)(void *, void *), void *ctx, int w,
                    int into);
int media_find(int kind, char *path);
void prop(void *w, const char *name, int value);
char *svc_find(char *hide, int i);
void svc_hidden(char *s);
int media_click(void *ctx, void *event);
int stream_config(void *ctx, void *event);
int painting_in(void *row);
int round_inset(int R, int i, int h);
void round_ring(void *canvas, const rect_t *r, int R, unsigned c);
void round_row(void *canvas, const rect_t *r, int R, int i, unsigned c);
/* nav/row_paint.c: rounds what is drawn in r into behind (0xRRGGBB, final), anti-aliased */
void corner_mask(void *canvas, const rect_t *r, int R, unsigned behind);
int slider_part(void *s, int w);
const char *style_key(char *key, const char *state, const char *name);
unsigned wide_len(const unsigned *s);
void *window_of(void *w);
/* feedback.c: OLED's feedback forms: toast, confirm, empty songs page, shuffle confirmation
 * (docs/oled-design.md#dialogs-and-toasts) */
int feedback_toast(void *top);
void *feedback_under(void *toast);
void *feedback_top(void);
void feedback_toast_paint(void *w, void *canvas);
void feedback_toast_leave(void *w, void *canvas); /* the toast on its way out */
int feedback_confirm_dress(void *w);
void feedback_confirm_foot(const char *foot);
/* the empty state's painters (feedback.c, docs/oled-design.md#empty-and-offline): its view's, and a
 * painted page's stack at top (a y in w) */
void empty_state_paint(void *w, void *canvas);
void empty_paint(void *w, void *canvas, const empty_t *e, int top);
int empty_top(int free_top, int free_end, int row), empty_compact(int free_top);
int empty_card(empty_t *e);
void feedback_confirm_bar(void *w, void *canvas);
void feedback_songs(void *top, void *row);
void feedback_folder(void *top);
int feedback_offline(void);
void feedback_switch_bar(void *w, void *canvas);
void feedback_scan_bar(void *w, void *canvas);
void feedback_shuffled(const char *what, int songs);
int confirm_surface(void *w); /* w holds the card's answers */
/* pair_prompt.c: one pairing presentation, Car Remote's, Guest Play's and a Bluetooth device's
 * (docs/oled-design.md#pairing) */
void *pair_prompt(const char *phone, const char *code, const char *body, const char *const *rows,
                  int n_rows, int back, int timeout_ms, int timeout_row,
                  void (*answer)(void *ctx, int row, int toggle_on), void *ctx);
void confirm_paint(void *w, void *canvas), confirm_rows_paint(void *w, void *canvas);
void confirm_bar_dim(void *w, void *canvas);
void cf_veil(void *win, int on);              /* win unseen (1) or seen again under Coverflow */
int ringnav_otg(void);                        /* Library's On-The-Go switch */
int ringnav_playlist_coverflow(void);         /* Display's Playlist View is Cover Flow */
int music_records(void *list, int discovery); /* list's songs less spoken (0) or hidden (1) ones */
int search_key(unsigned key);
int geom(int group);
int load_rows(menu_t *m, void *w);
void *qm_record(void);
void *qm_collect(void *r);
void *rec_find(void *songs, const unsigned *slot, unsigned mask, void *r);
unsigned *rec_index(void *songs, unsigned *mask);
void reveal(menu_t *m, int id, int immediate);
void row_meta(void *w);
void *staged_music(int (*query)(void *), void *arg, int *count);
int usable(void);
#define LIBRARY_MUSIC 1 /* all_songs' music: spoken tracks left out, the server's songs beside */
#define LIBRARY_ALL 2   /* every card song, and the server's */
int all_songs(void *music);
long clock_back(long long wall);
void text_inks(void *w);
unsigned listen_key(void *r) __attribute__((pure));
void plays_load(void);
int songs_unsorted(void *music);
void drop_input(void);
int pointer_down(void);
unsigned qm_album_id(void *r);
char *scrobble_tag(char *o, char *end, const char *s);

/* coverflow_position.c */
int coverflow_position_enabled(void); /* navigation.c: persisted setting */
void coverflow_position_reset(void);

/* list_marks.c */
const char *theme_art(const char *name);
void chevron_icon(void *w, void *canvas, int right, int y, int selected);
/* a value beside a chevron ends this short of the right column: the chevron's 15 px box and 6 px
 * clear of it (navigation.c value_fit, slider_page.c eq_rows_paint) */
#define VALUE_CHEVRON_GAP 21
void chevron_muted(void *w, void *canvas, int right, int y);
void spinner_icon(void *w, void *canvas, int right, int y);
int spinner_frame(void);
void spinner_painted(void *w, const rect_t *r);
int coverflow_spinner(void), coverflow_wait_input(void *e);

/* slider_page.c: a set_switch theme's slider page, laid out as stock's window loads and painted
 * after its children */
void slider_page_open(void *win, void *s, unsigned kind);
void slider_page_paint(void *w, void *canvas);
extern void *slider_page_win;       /* the page slider_page_paint draws */
void slider_page_dirty(void *page); /* its switch row, after a change of the hidden switch */

/* wifi_rows.c: a set_switch theme's Wi-Fi page, from ipod_list_layout and paint_into */
int wifi_list(void *list);
int wifi_sig(const char *image); /* stock's signal image's level, 1-4, else 0 */
void wifi_rows_pre(void *view);
int wifi_connected_row(void *b); /* the row button b has stock's state line */
void wifi_rows_post(void *view);
void wifi_mark(void *w, void *canvas, int level, int selected);
void wifi_busy_show(void *win); /* "Searching..." on the section's line while a scan runs */
/* the network first under Networks (wifi_flows.c's): the selection kept through a rebuild, a press
 * followed, a failed join marked, a join ended */
void wifi_rows_keep(void *view), wifi_rows_follow(const char *ssid), wifi_rows_end(void),
    wifi_rows_scanned(void);
void wifi_rows_failed(const char *ssid, int wrong);
void *wifi_tap_button(void *view, int id); /* the row button of the list's tap target id */
/* wifi_flows.c: Settings > Wi-Fi's flows past stock's */
int ringnav_wifi_rows(void *win), wifi_flows_hold(void *top), wifi_flows_cancel_join(void);

/* synclink.c: SyncLink off unless the user turns it on (from ringnav_usb_config) */
void synclink_default(void);

/* settings_pages.c: a set_switch theme's About page as an inert table, from ipod_list_layout; a
 * settings row's caption, shown under it while it is selected (paint_selection), and the rows'
 * texts */
int about_list(void *list);
void about_rows(void *view);
int reset_list(void *list);
void reset_rows(void *view); /* settings_pages.c: Reset Settings' rows */
int kv_text_w(void *label);  /* an hscroll_label's text width in its row's face; -1 unmeasured */
void kv_row(void *key, void *val, int kw, int vw, int right); /* settings_pages.c: kv_fit's split */
void diag_about_row(void *view); /* diagnostics.c, --dev builds' Save Diagnostics */
int diag_row(void *item);
void caption_row(void *label, const char *text);
const char *pod_caption(int i);
extern const char *const smart_awake_caption, *const folder_skip_caption,
                                                  *const reduce_motion_caption;
void caption_prune(void *view); /* drops the captions of rows a rebuild removed from view */
void caption_sel(void *view, void *item);
/* nav/lists.c: how much of scroll view `view` a row may stand in, its height cut at the set's list
 * end on the screen; a row that grows in place scrolls its foot inside it (settings_pages.c
 * cap_step, inline_slider.c heights) */
int list_shown_h(void *view);
void list_grown_foot(void *view, void *row, int step); /* nav/row_paint.c: and scrolls it there */
void caption_paint(void *view, void *item, void *canvas, const rect_t *bar);
/* inline_slider.c: Settings' one-value rows that change in place
 * (docs/oled-design.md#inline-sliders) */
enum { IS_MAXVOL, IS_BALANCE, IS_BRIGHT, IS_BOOTVOL, IS_KINDS };
void *inline_slider_row(void *view, const char *icon, int kind);
void inline_pair(void *view, const char *icon, int kind);
void inline_slider_sel(void *view, void *item), inline_slider_paint(void *w, void *canvas);
int inline_slider_key(void *top, unsigned key, int owned), inline_value(int kind);
void inline_value_text(int kind, int v, char *out, unsigned n);
/* control_center.c: Quick Settings drawn as Control Center (ringnav_quick_settings, its paint and
 * keys) */
void cc_open(void *win), cc_paint(void *win, void *canvas), cc_wheel(void *win, int dir);
int cc_key(void *win, unsigned key), cc_key_release(unsigned key);
void cc_keydown(unsigned key), cc_hold(int stop);
/* A settings page by its folder and name, unless that window is open already (control_center.c). */
void cc_page(const char *path);
/* navigation.c: a button's touch held QUICK_HOLD_MS opens page; Control Center's from QUICK_CC */
#define QUICK_CC 16
void quick_hold(void *button, int page);
/* tilt.c: the selection bar leans toward the wheel's next tick (paint_selection, ringnav_sleep),
 * and sinks and springs back under Centre (tilt_press, from ringnav_input; tilt_tone its blue) */
void tilt_poll(void);
void tilt_bar(void *w, void *canvas, rect_t *bar, unsigned accent, int radius);
void tilt_gleam(void *canvas, const rect_t *bar, int radius);
int tilt_clip(void *canvas, int *old, const int *frame);
void tilt_end(void *w, int dir);
void tilt_press(int down);
unsigned tilt_tone(unsigned c);
/* swipe_back.c: the left edge's swipe back (docs/internals.md#swipe-back) */
void swipe_back_down(void *event, int allowed);
int swipe_back_holds(void); /* a touch is a swipe back's candidate or its own */
void touch_hide(void);      /* nav/touch_dispatch_layout.c: a touch on the rows after all */
void swipe_back_poll(void);
void swipe_back_paint(void *w, void *canvas);
int page_ox(void *canvas, void *w);
void *window_under(void *wm, void *top); /* the normal window under top, 0 none */
void *under_snap(void *under); /* a window painted once into a whole-screen bitmap, 0 none */
/* art_zoom.c: the cover travels between the page it opened from and Now Playing's art as one card
 * (docs/oled-design.md#motion) */
enum { AZ_PANE = 1, AZ_ALBUM }; /* the page's cover: Home's art pane, an album page's header */
typedef struct {
    rect_t card;         /* the card's box on screen */
    int radius;          /* its corners, px */
    int zoom;            /* its cover's zoom over aspect-fill, thousandths */
    int pane;            /* how far along toward the page's cover, thousandths (the pane's pan) */
    int vig;             /* the pane's vignette on the card, thousandths */
    int np_a;            /* Now Playing (its art cut out), thousandths */
    int np_scale;        /* and its scale about the screen's middle, thousandths */
    int page_a;          /* the page (its cover cut out), thousandths */
    int page_dx;         /* and its x, px */
    int shadow;          /* the card's shadow at its darkest, 255ths */
    int src_a, np_art_a; /* each end's own cover over the card, thousandths */
} art_zoom_frame_t;
void art_zoom_load(void *np_win);
const char *window_hint(void *win, const char *name);
int art_zoom_source(void *top, rect_t *r);
int art_zoom_hero(void);
void art_zoom_frame(int t, int open, int hero, const rect_t *src, int src_r, int src_z,
                    const rect_t *np, art_zoom_frame_t *f);
void art_zoom_window(int w, int h, int zoom, int tw, int th, int pane, int pu, int pv, int pw,
                     int ph, int *u, int *v, int *step);
void art_zoom_card(unsigned *fb, int pitch, const rect_t *r, int radius, const unsigned *tex,
                   int ts, int u, int v, int step, int vig, int rgba);
void art_zoom_shadow(unsigned *fb, int pitch, const rect_t *r, unsigned a);
void *coverflow_home_pane(void);
/* coverflow.c: the pane's playing cover as a texture, and its vignette, for art_zoom.c's card */
int coverflow_home_cover(const unsigned **tex, int *tw, int *th, int *u, int *v);
unsigned coverflow_home_vig(int y, int h);
int hero_cover_box(rect_t *r);
/* page_parallax.c: a page's push and pop, the page under drifting and dimming */
int parallax_under_x(int w, float percent);
unsigned parallax_dim(float percent);
void *parallax_slide(float *percent);
/* np_settle.c: Now Playing's art settles smaller on pause (docs/oled-design.md#motion) */
void np_settle_poll(void);
int np_settle_scale(void);
int np_settle_box(void *w, int *box);
void np_settle_begin(void *w, void *canvas);
void np_settle_paint(void *w, void *canvas);
/* np_skip.c: Now Playing's track change slides and fades the art and lines */
void np_skip_begin(void *w, void *canvas), np_skip_end(void *w, void *canvas);
void np_skip_at(int t, int reduced, int dir, int *a_old, int *x_old, int *a_new, int *x_new);
/* list_marks.c: a scrolling hscroll_label's ends faded into what is behind it (list.marquee.fade)
 */
void marquee_fade(void *w, void *canvas), marquee_fade_under(void *w, void *canvas);
/* ... and any text a painter draws itself into the columns x0 to x1, rows y to y + h (the canvas's
 * coordinates): its ends (1 left, 2 right) copied before it, faded after it; 1 when armed */
int text_fade_under(void *canvas, int x0, int x1, int y, int h, int ends);
int text_fade_under_n(void *canvas, int x0, int x1, int y, int h, int ends, int px);
void text_fade(void *canvas);
void sel_bar_flat(void *canvas, int x, int y, int w, int h); /* list_marks.c: the flat bar */
/* np_marquee.c: Now Playing's lines as one marquee under a fading theme */
int np_marq_owns(void *w);
void np_marq_kick(void *w), np_marq_reset(void);
unsigned np_marq_wait(unsigned max);
unsigned np_glide_wait(unsigned max); /* np_bar.c: the progress bar's next pixel */
/* np_bar.c: Now Playing's shuffle and repeat as the theme's own pair (playing.modes.icons) */
void np_modes(void *win, void *canvas);
void np_stream_paint(void *w, void *canvas); /* np_stream.c: a server song loading */
int np_stream_loading(unsigned *have, unsigned *total, unsigned *ms); /* and for the car remote */
/* np_stream.c: how sure the playing song's length is (stream_length in peq.h): 0 the player's
 * stands, 1 *ms is the server's, 2 unknown (shown as --:--) */
int np_stream_length(unsigned *ms);
int np_stream_error(void); /* np_stream.c: a server song stuck with its server down */
void np_stream_cta_paint(void *canvas, int cy), np_stream_retry(void), np_stream_cta_click(int x);
/* np_bar.c: ... and its own heart and More over stock's (playing.modes.icons) */
void np_glyphs_bind(void *win), np_glyphs(void *win);
void wheel_sb_arm(void *list);
unsigned wheel_sb_alpha(void *list);
/* The status battery's key (navigation.c battery_now): the level from bit 4, then what the cable
 * does. CHARGING: green, the bolt inside; PLUGGED: on a cable, full or held at Charge Limit, green
 * with the plug inside; WEAK: the charger too weak (charging.c charge_weak), orange, the warning inside;
 * LOW: stock's low icon (the painters' red is the level under BATT_LOW_PCT). */
#define BATT_KEY_LOW 1u
#define BATT_KEY_CHARGING 2u
#define BATT_KEY_PLUGGED 4u
#define BATT_KEY_WEAK 8u
#define BATT_KEY_LEVEL(key) ((key) >> 4)
#define BATT_LOW_PCT 20 /* under it the fill is red and a tap says Low battery */
void round_path(void *vg, float x, float y, float w, float h, float r); /* list_marks.c */
void battery_slim(void *w, void *canvas, int x, int y, int bw, int bh, unsigned key);
/* charging.c: 1 while the charger cannot keep up (no signal for it yet: always 0); a tap on the
 * status row's battery (status_row.c status_touch); usb_ui_poll's toasts */
int charge_weak(void);
void charge_tap(void);
void charging_poll(void);
/* status_row.c: the status row's glyph slots, right to left (docs/oled-design.md#status-row): the
 * strip's room left of the battery (status_strip_room: its left edge, 0 for none, from
 * clock_place), their left edge (status_slots_left: the volume pill ends short of it), paint
 * (ringnav_paint_bg), the slot under a touch, the sync bar_state_sync runs (1: repaint the bar),
 * ringnav_input's touches; the headset's battery, 0-100 or -1 */
enum {
    SLOT_BATTERY,
    SLOT_PLAY,
    SLOT_WIFI,
    SLOT_SYNC,
    SLOT_WIRED,
    SLOT_HEADSET,
    SLOT_LOCK,
    SLOT_OFFLINE,
    SLOT_N
};
void status_strip_room(int left);
int status_slots_left(void);
int status_slots_reach(void);
void status_slots_paint(void *bar, void *canvas);
int status_slot_at(int x, int y);
int status_row_sync(void);
void status_touch(unsigned type, int x, int y);
/* vol_pill.c: the volume pill in the clock's place (docs/internals.md#volume-pill): a change to
 * show (vol_show), whether it is up, its paint (status_slots_paint) */
void vol_pill_show(void);
int vol_pill_up(void);
void status_pill_show(const char *glyph, const char *text); /* a message in its place (adb's allowed) */
void vol_pill_paint(void *bar, void *canvas);
#if Q2POD_DEV
void vol_pill_fake(void); /* --dev: STATUS_FAKE's volume=N, as a headset's */
#endif
/* ... and the Wi-Fi link as it reads it: stock's bars 1 to 4 while joined, else 0; joined with no
 * link up (Control Center's Wi-Fi circle, control_center.c cc_wifi) */
int wifi_level(void), wifi_offline(void);
/* --dev builds: the emulator's stand-in for Wi-Fi, a headset, a charger; -1 for none */
#if Q2POD_DEV
int status_fake(char key);
#else
#define status_fake(key) (-1)
#endif
/* charging.c: the cable in (usb_ui_status), Charge Limit reached (system.c charge_poll), the
 * plug-in screen's input gate (ringnav_input; 1 drops the event) and paint (ringnav_paint) */
void charge_plugged(void), charge_limited(void), usb_charger_in(void);
int charge_gate(void *e);
void charge_paint(void *w, void *canvas);
/* list_marks.c: an on/off setting's switch, ending on canvas column right, centred on y
 * (settings.values.switch) */
void switch_mark(void *canvas, int right, int y, int on, int selected);
/* list_marks.c: the row editors' marks (docs/oled-design.md#edit-library): a grip centered on cx,
 * cy scaled by pct, a lifted row's raised card, and a row's check circle in state CIRCLE_* */
enum { CIRCLE_ON, CIRCLE_OFF, CIRCLE_LOCKED };
void grip_mark(void *canvas, int cx, int cy, unsigned rgb, int pct);
void lift_card_paint(void *canvas, const rect_t *card);
void check_circle(void *w, void *canvas, int cx, int cy, int state, int selected);

/* song_rows.c: SONGS_INLINE's rows. Song Artist's and Song Length's values (theme_engine.c
 * song_artist, song_length), a list's kind (song_list) and where an artist stands (song_fit). */
enum { SONG_ARTIST_INLINE, SONG_ARTIST_SELECTED, SONG_ARTIST_RIGHT, SONG_ARTIST_OFF };
enum { SONG_LENGTH_ALBUMS, SONG_LENGTH_ALL, SONG_LENGTH_OFF };
enum { SONGS_MIXED = 1, SONGS_ARTIST, SONGS_ALBUM, SONGS_FOLDER };
enum { SONG_INLINE = 1, SONG_RIGHT };
int song_artist(void), song_length(void);
/* theme_engine.c: Track Time's value, and its switch on a tap (np_bar.c np_time_tap) */
int np_time_total(void), np_time_toggle(void);
int np_time_tap(void *ctx, void *event);
/* np_bar.c: 1 when the screen's (x, y) is inside w */
int np_hit(void *w, int x, int y);
/* A touch that lifts within TAP_PX of where it came down is a tap, not a swipe or a drag */
#define TAP_PX 10 /* AWTK's drag threshold */
/* np_taps.c: Now Playing's art, artist and album taps (np_tap on the window), the one held lit
 * (np_tap_lit: its text in the accent, np_tap_dim: the art dimmed); navigation.c np_goto is where
 * they go, with go 0 only whether they would */
int np_tap(void *ctx, void *event);
int np_tap_lit(void *w);
unsigned np_tap_ink(void);
void np_tap_dim(void *w, void *canvas);
void np_tap_reset(void);
int np_tap_lift(void *target, void *event);
int np_goto(int album, int go);
/* np_about.c: ⋯'s About, the track's details as a page; under OLED its info grid, painted
 * (np_about_paint) from the window's top, the wheel scrolling it (np_about_key), filled from the
 * details' labels (np_about_fill) */
int np_about_open(void *ctx, void *event);
void *np_about_page(void);
void np_about_np_gone(void *win);
void np_about_fill(void *win);
void np_about_paint(void *w, void *canvas);
int np_about_key(void *top, unsigned key);
int ringnav_np_info(void *win); /* navigation.c: Now Playing's details, filled */
/* np_bar.c: Now Playing's shuffle and repeat as two controls */
void np_mode_set(int on, int rep), np_mode_step(int repeat, int dir);
int np_mode_shuffle(void),
    np_mode_repeat(void); /* repeat: 0 Off, 1 All, 2 One (np_bar.c NP_REPEAT_*) */
/* Now Playing's Centre cycle: at rest (the wheel is the volume's), the scrubber, then a focus ring
 * the wheel moves across shuffle, repeat and the heart (nav/now_playing.c np_centre; np_bar.c
 * np_modes draws it) */
enum { NP_STAGE_REST, NP_STAGE_SHUFFLE, NP_STAGE_REPEAT, NP_STAGE_HEART };
void *np_heart(void); /* np_bar.c: the heart, while it shows on the art page, else 0 */
/* np_rate.c: Now Playing's rating page (the slide_view's after the lyrics) and the art's glow */
void np_rate_bind(void *win), np_rate_gone(void), np_rate_poll(void);
void np_rate_bg(void *w, void *canvas); /* ringnav_paint_bg: the glow, and the rating page */
int np_rate_on(void);                   /* the slide_view on the rating page */
int np_rate_key(unsigned key);          /* a wheel tick on the page: 1 taken */
int np_rate_centre(void);               /* Centre on the page: 1 taken */
unsigned np_rate_builds(void);          /* glows made (tests) */
void np_full_set(int on);               /* now_playing.c np_full: the column full screen, or back */
int np_mode_click(void *target, void *event);
void np_corner_tap(int x0, int y0, int x, int y); /* np_bar.c: np_tap's tap past the row */
int np_radio_on(void);            /* np_bar.c: radio on, shuffle and repeat the app's */
int np_queue(const void *unused); /* navigation.c: Up Next, from Now Playing's row or hold menu */
int song_list(void *w);
int song_fit(int place, int room, int title, int gap, int artist, int *shown);
/* A row's text and what stands at its right (a length, the offline mark): this much clear of each
 * other's ink; HSCROLL_MARGIN is hscroll_label's own, each side of its text (a row's text box
 * starts on the inset) */
#define SONG_COL_GAP 12
#define HSCROLL_MARGIN 2
/* edge: the left of the offline mark where the row carries it (navigation.c offline_x), else 0 */
void song_row(void *w, void *canvas, void *row, int id, rect_t r, int selected, int edge);
/* song_rows.c: the playing song's row (docs/oled-design.md#song-rows), the song known by
 * song_playing (playback.h). playing_title: label shown as its title (the accent, through
 * ringnav_style_color's playing_title_is), 1 when that changed. playing_bars: the four bars ending
 * before right, centred on cy, returning their left. playing_fit: the bars' right edge on a row
 * whose right column ends at column, beside a length right_w wide (B, *keep) or in its place (A).
 * playing_mark: a payload list's song row's record, for playing_row (its bars and title after it
 * paints, and the room they take, which the row's own right-hand text moves left by;
 * playing_rows_paint calls it for a row with no painter of its own). */
#define PLAY_BARS_W 16
unsigned song_identity(void *r);
int playing_title(void *label, int on), playing_title_is(void *label);
int playing_bars(void *canvas, int right, int cy, int selected);
int playing_fit(int column, int right_w, int left, int *keep);
void playing_mark(void *item, void *r, int own);
int playing_row(void *item, void *canvas, int column, int reserve, int selected);
void playing_rows_paint(void *w, void *canvas);
void paint_art(void *w, void *canvas, const char *name, int x, int y); /* navigation.c */

/* np_bar.c */
int np_sync(void *top);
void slider_fill(void *slider, unsigned color);
void slider_knobless(void *slider);
void np_theme(void);
void paint_scrubber(void *w, void *canvas);
/* One row of final colour, its alpha the row's. Inline: the round boxes' edges call it a pixel at a
 * time, and an exported call passes w on the stack. */
static inline void scrub_row(void *canvas, unsigned c, int x, int y, int w) {
    stock_canvas_set_fill_color(canvas, (RGBA(c & 0xffffffu) & 0xffffffu) | (c & 0xff000000u));
    canvas_fill_rect(canvas, x, y, w, 1);
}
void np_fill(int scrub);

/* seek.c: a held Previous or Next scans the track; np_seek_bar is navigation.c's */
int seek_input(void *e), seek_release(unsigned key), seek_held(unsigned key), seek_tick(void);
void seek_car(int dir); /* the car's Next (1) or Previous (-1) held, 0 its release */
int seek_step(unsigned held_ms), seek_target(int to, int dir, int step, int total);
int seek_plan(int dir, int paused); /* a row of seek.c's seek_phases */
void np_seek_bar(int phase, int to, int paused);
void seek_scrub(int to);
int seek_scrub_end(int to, int seek);
int video_on(void);
/* keylock.c: Buttons Lock's card (docs/internals.md#buttons-lock); its padlock is a status_row.c
 * slot */
int keylock_gate(void *e, int remote);
int wheel_lock_gate(void *e); /* keylock.c: a wheel tick while locked (the screen off) */
int keylock_holds(unsigned key); /* keylock.c: the Buttons Lock holds key back now */
void keylock_paint(void *w, void *canvas);
/* feedback.c: an overlay card's scrim over the top window and the status bar; 1 on the window */
int overlay_scrim(void *w, void *canvas, unsigned argb, int *dy);
void search_strip_overlay(void *w, void *canvas); /* search_strip.c: Return held's card */
int ringnav_wm_paint(void *wm);                   /* window_manager_paint's leaf */
/* nav/input.c: glyph_cache_lookup's stamp (tools/build.py GLYPH_STAMP) */
unsigned long long ringnav_glyph_stamp(void);

/* theme_engine.c */
#define ACCENT_N SETTING_N_ACCENT /* the ACCENTS rows: theme_engine.c's accents[] has as many */
enum {
    SETTING_ACCENT,
    SETTING_HOME,
    SETTING_BATTERY,
    SETTING_LIGHT,
    SETTING_KEEPLIT,
    SETTING_CLICKER, /* the Clicker's output: stored here, its row Controls' Key Tone row
                        (clicker.c) */
    SETTING_REDUCEMOTION, /* Reduce Motion (ui_kit.c motion_reduced), Display's last row */
    SETTING_OWN
};
#define INK_LABEL 1
#define INK_BUTTON 5
enum { PODCASTS = 1, AUDIOBOOKS, VIDEOS };
enum { TONE_LIGHT = 2, TONE_RED = 3 }; /* columns of ACCENTS */
int geom_val(int v) __attribute__((pure));
extern const char *const MEDIA[];
unsigned flip_if(int on, unsigned c) __attribute__((pure));
int accent(void);
unsigned accent_map(unsigned c, int preset, int tone);
const int *clock_now(int *buf);
unsigned flip(unsigned c) __attribute__((pure));
unsigned flip_v(unsigned v, unsigned paper, unsigned ink);
int ipod_home(void);
void light_apply(const int *tm);
int light_day(const int *tm);
int neutral(unsigned c);
const char *option_key(const theme_t *T, int setting);
const option_t *option_of(const theme_t *T, int setting);
int *option_slot(const theme_t *T, int setting);
unsigned red_map(unsigned c, unsigned tone, unsigned glyph, int light);
int setting(int i);
unsigned tone_of(const unsigned *a, int tone);
extern const char *const accent_names[];
extern const int setting_counts[];
extern int *const setting_values[];
static inline const unsigned *tones_of(int v) { return theme()->schemes[v].tones; }
static inline const unsigned *tones(void) { return tones_of(accent()); }

/* scrobble_ui.c */
void scrobble_listened(void);
void scrobble_auto(void);
int upload_scrobbles(void *ctx, void *event);

/* volume.c */
void vol_paint(void *top, void *canvas);
int vol_keyup(void *dialog, void *e); /* volume_dialog_on_keyup's hook: the wheel's steps */
/* on_wm_keyup_fun's hook: the wheel's volume, playback screens' alone */
int vol_wm_keyup(void *ctx, void *e);
int vol_accel(void);     /* volume.c: a wheel tick's steps at its speed */
void vol_show(int from); /* volume.c: a change not the wheel's, on screen */
int vol_page(void *page);  /* volume.c: page's wheel is the volume (Now Playing and the rest) */
/* levels.c: the wheel's step and the bar's ease, shared by the volume and the brightness. shown:
 * the bar's level in 1/256 steps, moving towards the level while easing, from at. */
typedef struct {
    int shown, easing;
    unsigned at;
} level_ease_t;
extern level_ease_t vol_bar, bright_bar; /* volume.c, levels.c */
int wheel_level(int value, int lo, int hi, int full, int dir), bright_wheel(void *slider, int dir);
void level_ease_from(level_ease_t *e, int from), level_follow(void *win);
int level_ease(level_ease_t *e, int level), level_shown(const level_ease_t *e, int level);

/* smart_wifi.c */
void smart_wake(void); /* Smart Wi-Fi's radio back now, if it had it off */
int smart_config(const char *section, const char *key);
void smart_poll(void);
void *page_in_use(void); /* the top window past toasts and the volume dialog */

/* localsend_ui.c: Settings > Network > LocalSend, the share sheet, the Accept card (docs/localsend.md) */
int localsend_open(void *ctx, void *event), localsend_running(void), localsend_key(void *top, unsigned key);
void localsend_share(const char *path, const char *title), localsend_poll(void), localsend_paint(void *w, void *canvas);
int ringnav_wifi_transfer(int on), stock_wifi_transfer_trampoline(int on); /* stock's server kept off */

/* battery.c */
void battery_tick(int level, int cable);
unsigned battery_log(char *out, unsigned size);
int battery_log_write(const char *path); /* the log's text to path, whole: its bytes, or -1 */
int battery_history(void *ctx, void *event);
void battery_paint(void *w, void *canvas);
int battery_key(void *top, unsigned key); /* the wheel along the readings, Centre shares the log */

/* system.c */
void power_poll(void);
void heatguard_poll(void);  /* heatguard.c: q2guard's cards, from power_poll */
/* ulog.c: a line in the unified log through demo's stderr (docs/internals.md#unified-log): level E, W or
 * I, src ui, player, usb, bt or wifi; and a clean end's snapshot, waited for (power-off, restart, update) */
void ulog(char level, const char *src, const char *msg);
void ulog_end(const char *why);
void ulog_flush(void); /* q2guard's logs on the card now, as they are (LocalSend's Send Logs) */
/* ui_watch.c: the UI pass watchdog (docs/internals.md#ui-pass-watchdog), from ringnav_sleep; a
 * step a long pass may be in, named by its caller (a string constant); each poll's start, named */
void ui_watch_enter(void);
void ui_watch_polled(void);
void ui_watch_woke(void);
void ui_watch_step(const char *step);
void ui_watch_poll(const char *name);
void cover_wake_poll(void); /* cover_wake.c: the album cover thread woken, every UI loop pass */

/* power_rows.c */
void power_rows(void *view, void *reset); /* General's last rows; reset: the Reset Settings row, moved last */

/* usb_charge.c: wrappers for charging, configuration loading and USB gadget dispatch. */
int ringnav_usb_charge(int on), usb_charge_held(void);
void usb_charge_row(void *win, void *view); /* Power's USB DAC Charging */
int ringnav_usb_config(void), ringnav_usb_detect(void), ringnav_usb_status(void);
int stock_usb_config_trampoline(void), stock_usb_detect_trampoline(void);
int stock_usb_status_trampoline(void);

/* usb_otg.c: check_otg_status's hook, OTG host power only with no VBUS on the port */
int ringnav_otg_status(void), stock_otg_status_trampoline(void);
int ringnav_gpio_value(const char *name), stock_gpio_value_trampoline(const char *name);
/* usb_volume.c: mclUsbAudioSetVol's hook, a USB DAC's volume in decibels */
int ringnav_usb_setvol(int v, int right, const char *card), stock_usb_setvol_trampoline(int v, int right, const char *card);
/* usb_ui.c: a host has configured the gadget (system.c holds Standby on it) */
int usb_host(void);
void otg_hold(void); /* the PHY's ID pull-up on, the controller's role held at device */
int usb_otg_own_vbus(void); /* usb_otg.c: the Q2's own bus power on, or still dying away */
int usb_otg_cable(int vbus); /* usb_otg.c: PD17's VBUS less the Q2's own dying away */
void usb_otg_poll(void);    /* usb_otg.c: a Play asked while USB audio connected */
void usb_keys_poll(void);   /* usb_keys.c: a USB-C headset's buttons */
int stock_forbid_play_trampoline(int quiet);
/* standby_usb.c: Standby and the USB link (docs/internals.md#standby-and-the-usb-link) */
void standby_usb_suspend(void), standby_usb_poll(void);
/* card_release.c: the card let go of before Standby, power-off and restart (docs/internals.md#the-card-across-standby) */
void card_back(void);
int card_mounts(void), card_holders(char *out, unsigned cap), card_kept(void);

/* usb_record.c: the USB record on the card, a line per cable edge and OTG power change */
void usb_record(const char *what), usb_record_edge(int in), usb_record_wake(void), usb_record_poll(void);
void usb_record_pins(int hostdev); /* the pins alone, usb_otg.c's trace */
int ringnav_platform_init(void), stock_platform_init_trampoline(void);

/* autozone.c: Automatic Time Zone, the zone from the network's location */
int ringnav_timezone(void); /* navigation.c: g_time_zone's TZ set, the clock redrawn */
void autozone_poll(void), autozone_due(void), autozone_off(void),
    autozone_config(const char *section, const char *key);
int autozone_busy(void), autozone_match(const char *zone), autozone_enabled(void);
void pod_row_at(void *view, int i, const char *icon, unsigned at);

/* config_ini.c: toolsWriteConfig's hook, and the settled keys write_int_config's hook holds */
int ringnav_ini_write(const char *path, const char *section, const char *key, const char *value),
    stock_ini_write_trampoline(const char *path, const char *section, const char *key,
                               const char *value);
#define CONFIG_SETTLE_MS 3000 /* a held key is written this long after its last change */
int config_hold(int value, const char *section, const char *key);
void config_settle(int now_too);

/* library_lists.c */
int era_menu(void *ctx, void *event);
void era_rows_finish(void *page); /* a decade's page's rows all made now (search_bar.c) */
int era_sql(const char *sql, int (*row)(void *, int, char **, char **), void *ctx);
int long_songs(void *ctx, void *event);
/* durations.c: UI-thread API (duration_seconds and duration_text in peq.h); the worker alone
 * probes files, the UI publishes after join. */
int durations_start(void *songs), durations_busy(int *done, int *total);
void durations_cancel(void), durations_poll(void), durations_shutdown(int bounded);
void mp_counted(void *r, unsigned key, unsigned evicted);
int most_played(void *ctx, void *event);
int shuffle_menu(void *ctx, void *event);
/* library_lists.c: Shuffle Songs a step a UI pass, for a caller that shows a loading indicator */
void shuffle_songs_start(void), shuffle_songs_cancel(void);
int shuffle_songs_poll(void);
int shuffle_play(void *all);
int tidal_downloads(void *ctx, void *event);

/* ratings.c */
void rate_stars_paint(void *w, void *canvas);  /* ratings.c: OLED's Ratings rows' stars */
void stream_mark_paint(void *w, void *canvas); /* home.c: OLED's Streaming rows' marks */
extern void *stream_mark_views[2];             /* home.c: the lists whose rows it marks */
/* home.c: Home's main list's rest height and its rows' foot (0: another view); coverflow.c: its
 * scroll view */
int home_rest(void *view, int *foot);
void list_fades(void *canvas, int top, int bottom, int end, int foot, int above, int below, int s0,
                int s1);
void list_top_fade(void *canvas, int top); /* home.c: a list's rows above its top, faded */
void home_fades(void *canvas, void *list, int list_y, int cols, int split, int shift); /* home.c */
/* A list whose box starts at most this far down the screen runs on under the status row to the
 * screen's top edge once scrolled (row_paint.c list_reach, docs/oled-design.md#list-edges) */
#define REACH_TOP 90
void list_reach_paint(void *bar, void *win, void *canvas); /* row_paint.c: the status row's band */
void list_reach_due(void); /* row_paint.c: before a frame, the band again if its list moved */
void *coverflow_home_view(void);
int rate_keep(int album, unsigned key, int n);
void rate_hold(void);  /* rate_keep's songs held in the table, until */
int rate_release(void); /* written once */
int rate_kept(int album, unsigned key);
unsigned rate_song_key(void *r); /* a server song's offline copy: its row's */
int rate_fav(void *r);           /* My Fav, a server song's offline copy by its row */
int rate_now_set(int n);         /* n stars for the playing track; 1 kept */
int rate_now(void);              /* the playing track's 0-5 stars, -1 when it can't be rated */
int rate_saved(int k);
void rate_album_favs(void *album, int on);
int fav_told(void *r, int on); /* ratings: a My Fav change told to the song's card app */
void rate_bind(void *win); /* ratings: Now Playing's heart */
int rated_albums(void *ctx, void *event);
int rt_closed(void *ctx, void *event);
int ratings_menu(void *ctx, void *event);
void ratings_copy_due(void);
void ratings_copy_poll(void);
int ratings_card_release(void); /* card_release.c: a pass under way ended, 1 */
int rt_play(void *ctx, void *event);

/* scan_page.c */
void msg_open(void *win);
void msg_paint(void *w, void *canvas);
const char *msg_title(const char *name); /* its hidden bar title, else 0 */

/* playlists.c */
/* The Playlists rows (pl_dress): a page's add mode (ringnav_playlist), the button of a row that
 * isn't a playlist, its list item's action + 1, a playlist row's item and whether the rows pl_rows
 * last showed were select mode's. */
#define PL_MODE "_pl_mode"
#define PL_ACTION "_pl_action"
#define PL_ACT "_pl_act"
#define PL_ROW "_pl_row"
#define PL_MARKING "_pl_marks"
#define PL_ART "_pl_art"
#define PL_TILE "_pl_tile" /* OLED: the art image paints a tile until a picture (pl_tile_paint) */
void qm_playlist_bind(void *w, int depth, int (*pick)(void *, void *));
void qm_select(void);
int qm_playlist_gone(void *ctx, void *event);
int qm_playlist_pick(void *ctx, void *event);
int ple_key(unsigned key, int phase);
int ple_listing(void);
int pl_first(menu_t *m, int cur);
void pl_tile_paint(void *w, void *canvas);
/* On-The-Go's songs are playlist entries with this cd_num and no header (headers are 0 to 99):
 * stock's adds, reads and Now Playing's Delete take it as any playlist's
 * (docs/internals.md#on-the-go) */
#define ON_THE_GO 100
int playlist_add(void *r, int dest);
int playlist_remove(int dest, void *r);
void playlist_synced(int dest);
int playlist_rename(int id, const char *name);
/* a playlist id, -2 for a name taken, -1 when it fails */
int ringnav_playlist_create(void *list, const char *name);
int playlist_delete(int id);
int otg_clear(void);
int otg_save(char *name, unsigned size);
int playlist_confirm(const char *question, const char *verb);
void playlist_pick_open(void *r);
void playlist_rename_open(int id, const char *name); /* search_strip.c: the letter strip */
void playlist_new_open(void); /* search_strip.c: New Playlist on the strip, its field off */
/* bt_name.c: the Q2's Bluetooth name (docs/internals.md#bluetooth-name) and Settings > Bluetooth's
 * Name row */
const char *bt_name(char *out, unsigned n);
int bt_name_valid(const char *s);
void bt_name_apply(void), bt_name_row(void *view);
int bt_own_picture(const char *path); /* bt_cover.c: a song file holds its own picture */
/* bt_ui.c: Settings > Bluetooth's order, rows and marks (docs/oled-design.md#bluetooth) */
void bt_page(void *view, int on), bt_rows_pre(void *view), bt_rows_post(void *view);
int bt_list(void *list), bt_mac_ok(const char *s), bt_dev_find(const char *mac);
int bt_quality_dim(void *w, unsigned *color); /* a Quality choice the device lacks: its text */
#define BT_MARK_CHECK 6 /* bt_mark's kind: a picker's check on the right column */
void *bt_mark(void *parent, const char *name, int kind, int x, int y, int w, int h);
/* bt_pair.c: a device pairing by numeric comparison (docs/internals.md#bluetooth-pairing-card) */
void bt_pair_register(void *stock, void *unused), bt_pair_poll(void);
int bt_pair_prompt(int type, const char *passkey, void *unused);
/* guest_ui.c: Guest Play's screens (docs/oled-design.md#guest-play) */
void guest_ui_poll(void), guest_ui_paint(void *w, void *canvas), guest_np_open(void);
int guest_ui_state(void), guest_ui_on(void), guest_settings_open(void *ctx, void *event),
    guest_home_np(void);
/* home_np.c: Home's first row led by the play state (docs/ipod.md#home) */
void home_np_row(void *row, int editing), home_np_bind(void *win),
    home_np_paint(void *w, void *canvas);
int home_np_lead(void);
void home_np_sync(void);
const char *guest_ui_value(void), *guest_ui_title(const char *name);
int guest_ui_keeps_lit(void *top);
void guest_short(const char *name, char *out, unsigned n);
void bt_ui_paint(void *w, void *canvas), bt_ui_poll(void);
int ringnav_bt_resume(void); /* navigation.c: Resume on Connect */
const char *bt_codec(void), *bt_kind(unsigned cls), *bt_dev_mac(int i), *bt_dev_name(int i);
unsigned bt_class(const char *mac);
enum { BT_ROW_FOUND, BT_ROW_PAIRED, BT_ROW_CONNECTING, BT_ROW_CONNECTED };
int bt_dev_state(int i), bt_stock_click(int dev, int x), bt_ui_hold(void *top);
int ringnav_btrows(void *win, void *ctx); /* nav/rows_settings.c: stock's btmenu_rows, then the page's order */
/* bt_trace.c: stock's Bluetooth connection state in the unified log
 * (docs/internals.md#bluetooth-connection-trace); whether the kernel holds an ACL link to a MAC */
void bt_trace_poll(void), bt_trace_why(const char *why, const char *mac), bt_trace_page(void);
void bt_trace_shown(const char *mac);
void headset_bt_usb(void); /* headset.c: a cable in, one connect to the saved device */
int bt_acl_up(const char *mac), bt_acl_listed(const unsigned char *req, int max, const char *mac);
void bt_dev_rename(int i, const char *name);
/* bt_detail.c: a Bluetooth device's page */
void bt_detail_open(int dev), bt_detail_poll(void);
const char *bt_detail_title(const char *name);
int bt_codec_line(char *s, unsigned n);
unsigned bt_kbps(const char *codec, unsigned hz);
/* bt_stopped.c: Bluetooth Stopped, the dead radio's restart asked first */
int bt_stopped_hold(void *caller), bt_stopped_toast(const char *name, void *ctx);
void bt_stopped_paint(void *w, void *canvas);
const char *bt_stopped_title(const char *name);
/* search_strip.c: a text box on the strip, on its keyboard's window */
int search_strip_box(void *kb, void *edit, const char *title);
/* search_strip.c: the search bar's pill (docs/oled-design.md#search-bar), and a list page's own search
 * over its rows (search_bar.c); search_bar.c: the bar above a list page's first row */
#define SEARCH_PILL_X 16
#define SEARCH_PILL_H 36
#define SEARCH_PILL_R 10
void search_pill_paint(void *canvas, int cy, int lit, const char *words);
int search_strip_scoped(void *page, void *surface, const char *title);
int search_bar_up(void *top, void *w), search_bar_up_with(void *top, void *w, void *also),
    search_bar_key(void *top, unsigned key);
void search_bar_off(void), search_bar_paint(void *w, void *canvas);
/* ... or on a page of its own, for a text the payload keeps itself: titled title, text prefilled,
 * at most max characters; Next at the text's end calls done, whose 0 closes the page and anything
 * else is a toast saying why not, the text kept to edit */
void search_strip_text(const char *title, const char *text, int max,
                       const char *(*done)(const char *));
void search_strip_note(const char *note);   /* ... the next one's note: who sees the text */
void search_strip_secret(void);             /* ... the next one's text a password, as dots */
void search_strip_label(const char *label); /* ... the next one's row's label: typed as a cell */
void search_strip_return(int kind);         /* ... the next one's return mark, STRIP_* (peq.h) */
/* playlist_pages.c: a playlist's songs and On-The-Go under OLED (docs/oled-design.md#playlists) */
void playlist_page_open(int dest, const char *name);
int playlist_query(void *dest); /* dest's songs in its own order, for staged */
/* add_songs.c: Add Songs (docs/oled-design.md#add-songs) */
void add_songs(int dest, const char *name);
int add_songs_key(void *top, unsigned key);
void add_songs_paint(void *w, void *canvas);
int add_songs_mark(void *r);
void add_songs_toggle(void *r);
void add_songs_search(void *songs); /* search_strip.c: the strip over Add Songs' songs, picking */
void search_yield(void);    /* search_strip.c: Search's load, if it runs, ended (staged_into) */
void coverflow_yield(void); /* coverflow.c: a Cover Flow open's step given up (open_yield) */
int playlist_page_hold(void *top);
int playlist_page_song_hold(void *top);
/* hold_menu.c: a song row's hold menu from a held Centre or finger (centre_menu.c) */
int qm_centre_hold(void *top);
int qm_touch_hold(void *top, int x, int y);
/* search_strip.c: a song result held, by a key (the selected one) or a finger at (x, y) */
int search_strip_song_hold(void *top);
int search_strip_on_row(void *top); /* the selection on a result */
int search_strip_touch_hold(void *top, int x, int y);
/* upnext.c: the same over Up Next's queued song (centre: Playing Next's rows keep their lift) */
int un_menu_hold(void *top, int centre);
/* centre_menu.c: Centre's press and release, a finger's raw pointer events (input.c) */
void cm_keydown(unsigned key);
int cm_key_release(unsigned key);
void cm_touch(unsigned type, int x, int y);
int qm_song_hold(void *page, void *list, unsigned at, const char *extra, void (*pick)(void));
int qm_far_hold(void *page, void *list, void (*play)(void));
void qm_held_move(void (*move)(void)); /* the held row's Move (hold_menu.c) */
int upnext_instead(const char *name);  /* stock's queue page asked for: Up Next instead (upnext.c) */
int qm_row_under(void *top); /* a row under the selection on top, menu or not (hold_menu.c) */
int otg_count(void);
void pv_paint(void *w, void *canvas);
int playlists_transfer(void *ctx, void *event); /* Settings: Import (ctx 0) or Export Playlists */
void pl_fit(void *button);

/* clicker.c: the Clicker (docs/internals.md#key-tone), the Key Tone row's value and click, and a
 * click's route */
enum { CLICK_KIND_WHEEL = 1, CLICK_KIND_BUTTON, CLICK_KIND_BEEP }; /* q2player's mcl-click sounds */
int clicker_chirp(void); /* clicker.c: the plug-in chirp mixed into what plays; 1 if sent */
enum { ROUTE_BUZZER = 1, ROUTE_MIXED = 2 };
extern int clicker_kind; /* CLICK_KIND_*, 0 the wheel's (zero at start, as .scratch is) */
const char *clicker_text(void);
void clicker_step(void);
int clicker_route(int listening), clicker_buzzes(int listening);
/* input.c: one buzzer click alone, as a wheel's would be, for a wheel read raw (radio_tuner.c) */
int ringnav_piezo(void);
/* menu_modal.c: the menu sheet (docs/oled-design.md#menus) */
enum { MM_ACTION, MM_SETTING, MM_CHOICE, MM_SWITCH }; /* modal_row's how */
int modal_row_w(void);
void *modal_gap(void *view);
void modal_row(void *row, void *label, const char *icon, const char *value, int how);
void modal_frame(void *win, void *list);
void modal_paint(void *w, void *canvas);
void modal_after(void *w, void *canvas);
void modal_head(const char *title, const char *sub, void *r);
/* coverflow.c: r's cover as Home finds it, a file:// URL in url; 0, url empty, for none */
int song_cover_url(void *r, char *url, unsigned size);
void modal_red(void *row);
int modal_is_red(void *row);
int modal_sel_height(void *view, int h);
/* a menu's destructive row: its words and bar (the confirm card's red) */
#define MODAL_RED 0xff3b30
int modal_rows_of(void *w);
int modal_win(void *w);
void modal_row_paint(void *w, void *canvas, void *row, rect_t r, int selected);
int modal_pick(void *under, const char *const *names, int n, int current, void (*pick)(int i));
void modal_more(void *win);
void modal_playlist_more(void *win);
void *modal_sheet(int (*back)(void *dialog, void *event), int (*gone)(void *dialog, void *event));
void modal_head_bars(void);
void modal_close(void *win);
int modal_closing(void);
void modal_corners(void *canvas, const rect_t *r, int R);
/* library_album.c: a Library album's page under the menu sheet's set (albuminfo_page,
 * playerjumpinfo_page) */
void album_local_load(void *win);
int album_local_on(void *win);
void album_local_paint(void *w, void *canvas);
/* artist_page.c: an artist's page's pair of stops */
int pair_key(void *top, void *w, int dir);
int pair_holds(void *w);
/* library_artist.c: a Library artist's page under the menu sheet's set (artistinfo_page) */
int artist_local(void);
int artist_local_on(void *win);
void artist_local_paint(void *w, void *canvas);
int artist_songs(void *unused);  /* navigation.c: the artist page's songs query */
void *plays_ranked(void *songs); /* library_lists.c */
/* song_sort.c: Songs' order, Playback > Library > Sorting's Songs */
enum { SORT_TITLE, SORT_ARTIST, SORT_ALBUM, SORT_ADDED, SORT_N };
int song_sort(void);
int song_down(void);
void song_sort_set(int sort, int down);
int song_sort_commit(void);
int song_sort_own(void);
void song_order(void *list);
void rec_permute(void *list, unsigned *at);
/* name_order.c: a sorted query's records in stock's order, kept across loads (library_query) */
void name_order(const char *sql, int sort, void *list);
#define NO_SONGS 1 /* the Library lists name_order_list names, their kept orders loaded ahead */
#define NO_ALBUMS 2
#define NO_ARTISTS 3
void name_order_list(unsigned cls); /* the Library list the next loads are for, 0 for none */
void name_order_poll(void);         /* every UI loop pass: a list due loaded and sorted ahead */
void library_warm(unsigned cls, void *into); /* nav/library_pages.c: list cls's load, as its open */
int library_row_wait(void *target, void *event); /* nav/library_pages.c: Songs and kin pressed early */
int coverflow_opening(void);                 /* coverflow.c: an open's step runs */
void cstl_sort(void *list, void *cmp);
extern unsigned long ui_thread; /* playback.c: the UI thread, from its first pass */

/* coda_rc_shim.c: Car Remote (docs/internals.md#car-remote) */
void coda_rc_poll(void), coda_rc_toggle(void);
int coda_rc_enabled(void);
int coda_rc_wants_wifi(void); /* Smart Wi-Fi: Car Remote needs the radio (coda_rc_shim.c) */
int coda_rc_online(void);     /* a phone has an authenticated Car Remote session */
void coda_rc_note(const char *text); /* a toast, for the car (NowPlaying.notice) */
/* carremote_page.c: Settings > Car Remote, its phone page and Pair a Phone */
int carremote_open(void *ctx, void *event);
const char *day_or_date(char *out, unsigned n, unsigned when); /* Today, Yesterday, Nov 12 */
const char *when_said(char *out, unsigned n, unsigned when); /* today, 18:11; yesterday, ...; Nov 12 */
void carremote_poll(void), carremote_paired(const char *name),
    carremote_paint(void *w, void *canvas);
/* song_radio.c: Now Playing's Radio (docs/internals.md#radio) */
int song_radio_on(void), song_radio_tap(const void *unused), song_radio_car(int id, int stop_it);
void song_radio_poll(void);
/* storage.c: Settings > Storage and its Downloads (docs/internals.md#storage) */
int storage_open(void *ctx, void *event), storage_hold(void *top);
void storage_paint(void *w, void *canvas);
/* usb_ui.c: the USB pages and the USB picker (docs/internals.md#usb-pages) */
void usb_ui_status(void), usb_ui_poll(void), usb_ui_mode_load(void *win), usb_ui_ejected(void *win);
void usb_ui_paint(void *w, void *canvas), usb_ui_row(void *label);
const char *usb_ui_title(const char *name), *usb_ui_value(void);
int usb_ui_key(void *top, unsigned key), usb_ui_cable(void), usb_pick_open(void *ctx, void *event);
/* usb_car.c: Car Audio, the USB setting's Automatic (docs/iphone-mode.md#car-audio-in-the-image); usb_car_poll's
 * answers to usb_ui.c */
enum { USB_CAR_NONE, USB_CAR_STORAGE, USB_CAR_CHARGER };
void usb_car_start(void), usb_car_stop(void);
int usb_car_poll(void), usb_car_on(void), usb_car_output(void), usb_car_busy(void), usb_car_stop_now(void);
const char *usb_car_name(void);
/* output_rule.c: no playback without an output (docs/internals.md#no-playback-without-an-output) */
void output_rule_config(void);
int ringnav_valid_output(void), stock_valid_output_trampoline(void);
int ringnav_nav_to(const char *name, const int *ctx),
    stock_nav_to_trampoline(const char *, const int *);
#if Q2POD_DEV
/* adb_trust.c (docs/adb.md#authorization): adbd's question on the card, its answer back through
 * usb_ui.c; Settings > General > Developer Options and Trusted Computers */
void usb_ui_auth_answer(const char *a);
int adb_prompt_open(const char *line), adb_dev_open(void *ctx, void *event);
void adb_prompt_close(void), adb_trust_paint(void *w, void *canvas);
#endif
/* usb_event.c: the USB cable's edges (PD17) from the kernel's GPIO character device, taken by usb_ui_poll */
void usb_event_start(void);
int usb_event_take(unsigned long long *edge_ns), usb_event_live(void), usb_event_wait(int fd);
unsigned long long usb_event_now_ns(void);
/* bt_battery.c: a connected headset's battery, 0 to 100, -1 for none
 * (docs/internals.md#headset-battery) */
int bt_headset_battery(void);
/* A shell command run off the UI thread (bt_ui.c bt_shell): one at a time per job, done counted
 * once it has ended, so its file is read once per answer. */
typedef struct {
    int running;   /* a run of it has started and not ended */
    unsigned done; /* runs ended */
} bt_job;
int bt_shell(bt_job *job, const char *cmd);
unsigned bt_job_done(bt_job *job);
unsigned bt_headset_rate(void), bt_headset_class(void);
const char *bt_headset_name(void);
/* loop_wake.c: the UI loop's idle as a poll() on an eventfd that loop_wake (peq.h, any thread)
 * ends; loop_span, how long an idle may run past its fixed length. */
void loop_wait(unsigned ms);
unsigned loop_span(unsigned base, unsigned most, int timers);
/* bt_vol_event.c: BlueZ's PropertiesChanged on MediaTransport1, counted by a thread, taken by
 * bt_volume_poll (docs/internals.md#headset-controls-and-volume) */
void bt_vol_event_start(void);
int bt_vol_event_take(void), bt_vol_event_live(void), bt_vol_event_connect(void),
    bt_vol_event_wait(void);
/* card_watch.c: what the card holds, as a generation that moves on each change; 0 without the watch
 * (docs/internals.md#card-watch) */
unsigned card_watch_gen(void), card_watch_seen(void);
int card_mounted_now(void); /* system.c: /proc/mounts read now; card_mounted follows the watch */
/* player_push.c: q2player's status push, waited on by the player polls (docs/internals.md#player-status-push) */
int player_push_wait(unsigned us), player_push_live(void);
unsigned player_push_seq(void);
/* bt_recv.c: Bluetooth receiver mode as a mode page (docs/internals.md#bluetooth-receiver) */
void bt_recv_load(void *win), bt_recv_paint(void *w, void *canvas);
const char *bt_recv_title(const char *name), *bt_recv_codec(void *win);
/* sw_update.c */
void sw_update_paint(void *w, void *canvas), sw_update_restarting(void *win);
const char *sw_update_title(const char *name);
int sw_update_key(void *top, unsigned key);           /* its page's wheel and Centre */
void toast_icon(const char *glyph, const char *text); /* feedback.c */
/* feedback.c: one toast at a time; a toast of text for ms (glyph a theme image's whole name, or 0); the nav_to hook's
 * swap of a toast asked for while one is up; the next toast's number, and that toast taken down if still up */
void toast_for(const char *glyph, const char *text, int ms), toast_end(unsigned n);
int toast_swap(const char *name, const int *info);
unsigned toast_next(void);
/* findmy.c: Find My Vamp's jingle (docs/internals.md#find-my-vamp); nav/input.c's buzzer worker busy */
int findmy_start(void), findmy_on(void), findmy_gate(void *e);
void findmy_stop(void);
extern volatile int tone_busy;
void toast_stay(void *canvas, int top, const char *text); /* feedback.c: a toast a page paints while it holds */
/* feedback.c: a toast with glyph (a theme image without its prefix) by the next loop pass */
void toast_later(const char *glyph, const char *text), toast_later_poll(void);
int tsv_split(char *line, char **f, int max); /* catalogue.c */

/* az_index.c: the A-Z index on a long Library list in name order (docs/oled-design.md#a-z-index):
 * a list this long or shorter has none; a touch this near the list's right edge is the index's;
 * a tick this fast (degrees a second, wheel_speed) shows it, the speed at which Rockbox's list
 * acceleration, (speed / 270)^4, first moves 2 rows a click (az_index.c) */
#define AZ_MIN_ROWS 50
#define AZ_EDGE_PX 28
#define AZ_SPIN_DPS 300
int name_sorted(const char *page); /* the top page's list is a name-sorted Library list */
void az_mark(void *w, int letter, int held, int bubble);
void az_wheel(void *w, int id), az_paint(void *w, void *canvas);
int az_letter_step(void *w, int id, int dir); /* a letter's jump on the wheel (wheel_accel.c) */
int az_touch(void *w, void *event), az_held(void *event), az_on(void *w), az_right(void *w),
    az_bar_in(void *w);
int az_initial(const char *s), az_target(const int *first, int letter), az_letter_at(int y),
    az_column(signed char *out);
/* library_edit.c: the Library's and Home's row order and visibility, and OLED's Edit Library and
 * Edit Home (docs/oled-design.md#edit-library) */
#define LR_ROWS 32 /* a list's rows at most, shown or hidden: lr_default's letters */
extern const char lr_default[], lr_home[];
int lr_at(int c), lr_resolve(const char *saved, char *out),
    lr_resolve_in(const char *def, const char *saved, char *out, char *vis),
    lr_shows(int mark, int rule);
void lr_merge(char *full, const char *shown), lr_put(void **rows, int c, void *row),
    lr_apply(void **rows), lr_auto_hide(void *row), lr_retire(void *row);
void lr_home_tag(void *view), lr_home_apply(void *view, int np), lr_edit_labels(void *view, int on);
void lr_edit_keydown(unsigned key), lr_edit_paint(void *w, void *canvas);
int lr_edit_key(void *top, unsigned key, int owned), lr_edit_touch(void *event),
    lr_edit_dragging(void), lr_edit_click(void *target), lr_edit_row(void *w),
    lr_editing(void *page), lr_edit_list(void *view), lr_edit_dim(void *w), lr_home_editing(void),
    lr_edit_room(void *view), lr_edit_foot(void *view);
const char *lr_edit_title(void);
/* home.c: Home's Folders rule and its list's rows coming and going; coverflow.c: Home's list */
int home_folders(void);
void home_keep(int d, int k);
void *coverflow_home_edit_view(void *top);
void coverflow_home_layout(void);
/* library_settings.c: Playback > Library and its pages (docs/ipod.md#settings) */
enum { LIB_LIBRARY = 1, LIB_SORTING, LIB_PLAYLISTS, LIB_FOLDERS };
void library_page(void *win, void *view, int page);
int picker_rows(void *win); /* a picker's rows on stock's Buttons Lock page */
void cf_sort_open(void);
/* navigation.c */
int library_open(void *ctx, void *event); /* Library's page ctx (LIB_*) */
void *list_row_into(void *view, const char *icon, int (*click)(void *, void *), void *ctx);
void pod_rows(void *win, const char *view_name, int first, int n, const char *const *icons);
void pod_text_again(int i);        /* a settings row's text, its value changed elsewhere */
void *folder_root_row(void *view); /* Folder Root's row, its label */
void settings_gap(void *view);     /* a 12 px space between a settings page's rows */
void pull_cancel(void); /* a pull to search let go: Edit Library's drag takes the touch */
void ringnav_jump(void *w, int id); /* table w's row id selected, at the list's top */
void ringnav_sort_key(char *s);     /* a name's sort key: trimmed, its article dropped */

#endif
