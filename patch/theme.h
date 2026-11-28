#ifndef Q2_THEME_H
#define Q2_THEME_H
/* The built-in theme's shape (docs/internals.md#themes); the table is theme_engine.c's themes[],
 * OLED's one row. */

enum { SCHEME_DERIVE, SCHEME_LIGHT, SCHEME_DARK }; /* scheme_t.mode */
/* theme_t.font[] index: list title, list second line, bar title, Now Playing title, a two-line
 * row's title, Home rows, small text (the QR pages, the Time Zone caption, page notes) */
enum { ROLE_TITLE, ROLE_META, ROLE_BAR, ROLE_NP, ROLE_TALL, ROLE_HOME, ROLE_INFO, ROLE_N };
typedef struct {
    const unsigned *tones; /* its ACCENTS row, accents[v], or an own scheme's tones=, <id>_tones[k]
                              (top, bottom, light, red, highlight) */
    unsigned char mode; /* SCHEME_DERIVE: Light Mode picks the half; LIGHT/DARK: that half always.
                           Both halves carry it; light_on() reads the dark row's */
    /* 0xRRGGBB; 0: role unused (ponytail: no black role): the grey a mono theme's list icons take
     * (ringnav_image_add), second lines' grey */
    unsigned chevron, muted;
    unsigned sel_text;   /* text on the selection, 0: the row's own */
    unsigned paper, ink; /* this half's, greys 0xRRGGBB: the light half's are the flip's ends
                            (LIGHT_PAPER and LIGHT_INK unless set), the dark half's 0x000000 and
                            0xffffff. A one-array theme's row holds the light half's: only
                            ipod_light()-gated code reads them there */
    const char *label;   /* a theme's own scheme's name ("Blue"); NULL: accent_names[v] */
} scheme_t;
typedef struct {
    unsigned char setting; /* SETTING_* (theme_engine.c): the bound row's click/text code, or
                              SETTING_OWN + k: the theme's own option theme_keys[k] */
    signed char def;       /* stored value not in values -> def; -1 none */
    signed char preset;    /* theme_preset writes it; -1 none */
    signed char keep;      /* a stored value theme_preset leaves (Light's Auto 2); -1 none */
    unsigned values;       /* bitmask of values the row takes; 0: all of setting_counts[setting] */
} option_t;
/* a theme's own IPOD key (tools/theme.py theme_keys), read after the payload's thirteen in table
 * order into st.own[] */
typedef struct {
    const char *key;           /* the IPOD key; a theme-owned accent key's too */
    const char *label;         /* the row's title; 0: an accent key (its row is Accent's) */
    const char *const *names;  /* [n] the values' labels */
    unsigned char n, def;      /* value count; the value a missing or unreadable key reads */
} theme_key_t;
typedef struct {
    /* coverflow.shelf (px, brightness of 256; tilt: degrees, horizon: % down the cover, third: the
     * third cover out stays, swing 1: a step's depth follows the turn and the near edge eases to
     * just past its rest place, 2: a step goes straight out to the first slot, lift: px nearer
     * mid-way, view_h: the frame's rows, art_persp: % of the
     * plane's own spacing of a cover's art, 0 even), coverflow.caption: offsets.inc's CF_* */
    short top, x1, xs, z1, zs, eye, bright1, bright2, reflect, reflect_top, stride, radius;
    short tilt, horizon, third, swing, lift, view_h, eye_turn, art_persp;
    /* coverflow.shelf, the thumb (docs/internals.md#coverflow-thumb; thumb 0: ticks and the step's
     * animator). Ticks are 200 / ticks_turn raw units at 50% Wheel Sensitivity, twice as many a
     * turn at 100%, and rates count those; far_left, stop_left, back, aim_snap, lead_*, row_from,
     * _full, _flat, _edge and fold_near are 1/100 album; enter_albums albums; others ms, ticks/s,
     * albums/s, px or %. jump_at, jump_ms and jump_pace are read only with Fast Navigation off
     * (1.7.6's letters; on, big_jump_at and big_jump_ms). The arch (Fast Navigation off; arch_span
     * 0: the row at speed instead): from arch_at to arch_full albums/s the covers within arch_span
     * (1/100 album) of the middle go back to the side covers' depth and turn by how far out they
     * are, forming over arch_in ms and relaxing over arch_out, and the covers
     * follow the wheel by one time constant, arch_tau ms, in place of track, snap and spin. */
    short thumb, ticks_turn, ticks_album, fast_at, fast_full, top_speed, track, snap, spin;
    short far_left, detent, hold_ms, stop_ms, stop_left, rate_up, rate_down, back, dead;
    short contact_ms, aim_snap, lead_from, lead_full, lead_ms, row_shift, row_from, row_full;
    short row_shrink, row_flat, row_out, row_edge, row_width, row_key;
    short jump_ms, jump_ticks, jump_at, jump_pace, big_jump_at, big_jump_ms, enter_albums, slide_ms;
    short fold_near;
    short arch_at, arch_full, arch_span, arch_in, arch_out, arch_tau;
    short name_px, artist_px, text_y, artist_y;
} cf_style_t;
enum {
    MOTION_PAGE,
    MOTION_PLAYING,
    MOTION_COVERFLOW,
    MOTION_INPLACE,
    MOTION_SWAP,
    MOTION_STEP,
    MOTION_STEP_FAST,
    MOTION_COVERFLOW_STEP, /* as each step, its fast kind right after it */
    MOTION_COVERFLOW_STEP_FAST,
    MOTION_N
};
typedef struct {
    const char
        *hint; /* window kinds: the anim_hint a load or open writes; 0 leaves the window as is */
    unsigned short ms;    /* widget kinds: the glide's duration; 0 is an immediate move */
    unsigned char easing; /* widget kinds: AWTK easing_type_t */
} motion_t;
typedef struct {
    /* a list left by Return forgets its row, so choosing it again opens it at the top
     * (navigation.c forget_left); Songs' action row reads Shuffle with the theme's own glyph and
     * the wheel selects it above the first song (navigation.c shuffle_key); in Split, the lists
     * Home's rows open (Settings, Library, Streaming) take the whole width with the art hidden, as
     * a page (coverflow.c sub_full) */
    unsigned char reopen_top, shuffle_row, home_lists_full;
    motion_t motion[MOTION_N];
    unsigned short recall_ms; /* a remembered row's glide; 0: shown at once */
    unsigned char chevron_home, set_text, icons_mono, icons_badge_muted, icons_addon_mono,
        art_final, np_album_op, np_state_op;     /* flags 0/1; *_op 0/255 */
    unsigned char font[ROLE_N], font_px[ROLE_N]; /* FONT_* (stock.h); px 0: the style's size */
    /* A list's scrollbar, a track and thumb shown while the wheel turns (list_marks.c): width (0:
     * stock's fading bar) and its place from the list's right; the screen rows its ends stay in;
     * the thumb's shortest length */
    short sb_w, sb_right, sb_top, sb_bottom, sb_min;
    unsigned sb_thumb[2]; /* final: [0] dark, [1] light */
    unsigned sb_track[2]; /* its track, final: [0] dark, [1] light */
    /* a row with art, on screen: the cover's x and its text's first and last columns; 0: stock's */
    short art_x, art_text_x, art_text_end;
    short art_radius; /* a row cover's corner radius on screen at the set's art; 0: NP_ART_RADIUS's
                         share of its size */
    short art_placeholder; /* stock's grey tiles (list_folder, list_song) this size, centred, 0:
                              as stock */
    unsigned char np_meta_font; /* Now Playing's artist and album: FONT_* at the asset's size, by
                                   its text (np_bar.c np_sync); 0: the style's */
    short home_top, home_full_row, home_text_x, home_label_end[2] /* [0] split, [1] full */,
        set_top, np_bar, np_scrub;
    /* the selection bar on a list row: its ends' radius, at most pill_radius's (so SEL_MAX_RADIUS
     * is always the pill), and its inset from each side of the row */
    unsigned char sel_radius, sel_inset;
    /* a scrolling hscroll_label's ends fade into what is behind it (list_marks.c marquee_fade);
     * Now Playing's art corners in px (paint_cover; 0: NP_ART_RADIUS's share of its size); its
     * shuffle and repeat drawn as the theme's own pair (np_bar.c np_modes; 0: stock's one icon),
     * and its heart and More the theme's glyphs too (np_glyphs; 0: stock's) */
    unsigned char marquee_fade, np_art_radius, np_modes;
    /* a set_text settings row's value (list_value), final: [0] dark, [1] light; 0: its style's
     * white */
    unsigned set_value_ink[2];
    unsigned np_track_rgb;     /* 0xRRGGBB: the progress bar's track, the volume's over it */
    unsigned np_info_value[2]; /* the info page's values, final: [0] dark, [1] light */
    int *accent_slot;
    const char *accent_key;  /* &st.accent,"ACCENT" | &st.own[k], an own key's */
    const scheme_t *schemes; /* [n_schemes], indexed by accent value: each scheme's dark half */
    const scheme_t *light; /* [n_schemes] each scheme's light half; == schemes when the theme states
                              no final value */
    unsigned char n_schemes; /* ACCENT_N, then the theme's own (OLED: 12) */
    const option_t *opts;    /* the Appearance rows, in order */
    unsigned char n_opts;
    /* settings.values.switch: an on/off settings row shows a switch (list_marks.c switch_mark), on
     * a set_text theme too, where it would read On or Off */
    unsigned char set_switch;
    unsigned undeclared; /* SETTING_* bits of the theme options it does not declare */
    cf_style_t cf;
    /* text entry the iPod's way: Search opens the letter strip (search_strip.c), the keyboard's
     * keys are walked by the wheel (keyboard.c) */
    unsigned char entry_ipod;
    /* page_list: a row, a row with a detail, the in-page title (0: the bar's) and the text's x; a
     * detail row's caption and detail boxes */
    short page_row, page_detail, page_title_h, page_x, page_cap_y, page_cap_h, page_det_y,
        page_det_h;
    const char *art; /* its own images' prefix (tools/art.py, its art attribute's id): "oled_" */
} theme_t;
const theme_t *theme(void);       /* accent(); return themes; (THEME_N is 1) */
const scheme_t *scheme(void);     /* theme()'s scheme accent(), in the half Light Mode picks */
int client_h(void);               /* the screen's rows under the theme's bar (theme_engine.c) */
int bar_foot(void *w);            /* the row in w where the status row ends (theme_engine.c) */
int page_room(void *w);           /* w's rows under the status row (theme_engine.c) */
/* Home's Split under the theme's set (its split_* fields, tools/ipod.py Geometry): SPLIT_ROW the
 * menu's width, SPLIT_ART the art pane's left edge, SPLIT_ART_UP the pane's rows above the window,
 * SPLIT_INSET the percent of the zoomed cover the pan keeps clear at each edge (coverflow.c
 * drift_axis) */
enum { SPLIT_ROW, SPLIT_ART, SPLIT_ART_UP, SPLIT_INSET };
int split_geom(int k);
int list_fit_end(void); /* theme_engine.c: the lists' last screen row (the grid's list_end) */
/* 0xRRGGBB to an opaque color_t, whose bytes are r, g, b, a. */
static inline unsigned RGBA(unsigned c) {
    return 0xff000000u | (c & 255) << 16 | (c & 0xff00) | c >> 16;
}
#endif
