/* Stock V1.36 layout and ABI constants shared by patch/navigation.c and test/patch.py.
 * The test harness parses this file, so a field change cannot leave the mocks behind.
 * Fields marked "same slot" are class-specific views of one offset. */
#define I(p, o) (*(int *)((char *)(p) + (o)))
#define P(p, o) (*(void **)((char *)(p) + (o)))
#define FNV_SEED 2166136261u /* FNV-1a offset basis */
#define W_X 0x00
#define W_Y 0x04
#define W_W 0x08
#define W_H 0x0c
#define W_FOCUS 0x24            /* stock focused flag byte (tests) */
#define W_PARENT 0x48
#define W_KEY_TARGET 0x58 /* the child its keys go to (widget_on_keyup @0x6760d0): a window manager's is the
                             window in front, from its open event on (0x6817c0, 0x67fba0) */
#define IM_WIDGET 0x04 /* input_method_t's editing target, input_method_request @0x658cc8 */
#define IM_KEYBOARD                                                                                \
    0x0c /* the default input method's open keyboard window, 0 once it closes (@0x69f19c) */
#define W_EMITTER 0x60
#define W_ASTYLE 0x64 /* widget_update_style reads it at 0x6717a0; 0x74 is the vtable */
#define W_CHILDREN_LAYOUT 0x68
#define CHILDREN_LAYOUT_VTABLE 0x10
#define DEFAULT_LAYOUT_X_MARGIN 0x18
#define DEFAULT_LAYOUT_SPACING 0x1a
#define DEFAULT_LAYOUT_VTABLE 0x93a6b4
#define EMIT_TYPE 0x08
#define EMIT_PENDING_REMOVE 0x15
#define EMIT_NEXT 0x20
#define ROW_HEIGHT 0x78         /* table_client row height */
#define ROW_INDEX 0x78          /* table_row logical index (same slot) */
#define SLIDE_INDEX 0x78        /* slide_menu current index (same slot) */
#define TABLE_ROWS 0x7c         /* table_client logical row count */
#define SELECTOR_INDEX 0x7c     /* text_selector selected option (same slot) */
#define VIEW_CONTENT_H 0x7c     /* scroll_view content height (same slot) */
#define SCROLL_X 0x80           /* scroll_view horizontal offset */
#define TABLE_TOP 0x80          /* table_client viewport top (same slot) */
#define TABLE_LOAD_DATA 0xe4    /* table_client on_load_data, its ctx at 0xe0 (table_client_set_on_load_data) */
#define SCROLL_Y 0x84           /* scroll_view viewport top */
#define VIEW_HORIZONTAL 0x90    /* scroll_view xslidable */
#define VIEW_VERTICAL 0x91      /* scroll_view yslidable */
#define VIEW_SNAP 0x92          /* scroll_view snap_to_page */
#define TABLE_ANIMATOR 0xd0
#define VIEW_ANIMATOR 0xe8
#define EVENT_TYPE 0x00
#define EVENT_KEY 0x18
#define EVENT_TIME 0x08 /* low word of the uint64 time_now_ms stamp */
#define EVENT_TARGET 0x10
#define EVENT_X 0x18           /* pointer_event_init @0x653808 */
#define EVENT_Y 0x1c
#define EVT_POINTER_FIRST 0xff /* the pointer events the wm dispatches: down 0xff, move 0x101, up 0x103 */
#define EVT_POINTER_DOWN 0x100 /* wm registers on_wm_tsdown_before_fun for this type @0x534e08 */
#define EVT_POINTER_MOVE_BEFORE 0x102 /* wm move-before forwards this type to the down hook @0x534dec */
#define EVT_POINTER_UP_BEFORE 0x104
#define EVT_POINTER_ABORT 0x107
#define EVT_DESTROY 0x0c
#define EVT_KEY_DOWN 0x110        /* widget_on_keydown @0x675d34 (0x111 is its long press) */
#define EVT_KEY_DOWN_BEFORE 0x112 /* wm keydown-before dispatch @0x534d84 */
#define EVT_KEY_UP 0x114          /* wm keyup dispatch @0x534dd0 */
#define EVT_KEY_UP_BEFORE 0x115   /* wm keyup-before dispatch @0x534d98 */
#define EVT_WINDOW_BACKGROUND 0x12a
#define EVT_WINDOW_FOREGROUND 0x12b
#define EVT_WINDOW_CLOSE 0x12c
#define PULL_SEARCH_PX 48       /* device tuning: release distance from the initial touch */
#define PULL_CLAIM_PX 8
#define QUICK_EDGE_PX 31        /* stock on_wm_tsmove_fun @0x4f2b28 */
#define EVT_CLICK 0x10c
#define EVT_FOCUS 0x10e /* widget_set_focused_internal @0x673364 */
#define EVT_BLUR 0x10f  /* widget_set_focused_internal @0x6733b0 */
#define EVT_IM_ACTION 0x13a /* the keyboard's OK, input_method_dispatch_action @0x659390 */
#define EVT_SCROLL 0x14e /* stock scroll_view on_scroll @0x6028d0; table setter emits this too */
#define EVT_SCROLL_END 0x14f /* stock lists queue their visible covers on this (demo's album page registers @0x4a5d7c) */
#define VIEW_ON_SCROLL 0x100 /* scroll_view callback installed by list_view, called by touch @0x6028ec */
#define CANVAS_X 0x00
#define CANVAS_Y 0x04
#define CANVAS_LCD 0x38
#define LCD_FILL_COLOR 0xbc
#define LCD_STROKE_COLOR 0xc0
#define LCD_TEXT_COLOR 0xb8     /* lcd_set_text_color @0x65aa34 */
/* Night Shift (nightshift.c): the screen's lcd_mem, BGRA8888 (lcd_mem create @0x6a5870) */
#define LCD_END_FRAME 0x7c   /* lcd_end_frame @0x65aec8 calls it */
/* The boot animation's hold (splash_hold.c): lcd_mem's end_frame calls one of them, and
 * lcd_linux_fb_create @0x63a57c sets both to its copy to the hidden page and pan @0x6398d8 */
#define LCD_SWAP 0x60  /* lcd_swap @0x65af44 */
#define LCD_FLUSH 0x74 /* lcd_flush @0x65af70 */
#define LCD_W 0xb0
#define LCD_H 0xb4
#define LCD_DRAW_MODE 0xcc   /* lcd_begin_frame @0x65a818 */
#define LCD_DRAW_NORMAL 0    /* 1 a window animation's */
#define LCD_DIRTY 0xf8       /* the frame's rect, x y w h; lcd_begin_frame sets it */
#define LCD_OFFLINE_FB 0x110 /* the buffer drawn into */
#define LCD_LINE_LENGTH 0x124
#define LCD_FORMAT 0x128
#define LCD_BGRA8888 3
#define CANVAS_ALIGN_V 0x30     /* canvas_set_text_align @0x64770c; middle = 1 */
#define CANVAS_ALIGN_H 0x34     /* center = 1 */
#define FILL_RGB 0x2b2b2b /* the stock dark surface, under the letter and volume overlays */
/* iPod accents, in the order of the Accent setting (IPOD/ACCENT in config.ini; 0 is the default):
 * { selection top, bottom, light tone, red tone, highlight }, 0xRRGGBB. Equal stops keep
 * selection flat on the Q2 panel; retain the fine top highlight. White text holds
 * 4.5:1 on every top. The light tone, the top lightened 12% toward white (Graphite keeps #6E6E6E),
 * is the Now Playing progress fill (3:1 or more on TRACK_COLOR). The highlight is the bar's one-pixel
 * top edge: the light tone, except Graphite's restrained #555555 over its flat bar. The red tone is what stock red #FF1448 becomes (switches, ticks, red text and
 * buttons): Graphite's is silver, so a lit switch reads against the grey off state; the others
 * reuse the light tone, 3:1 or more under white.
 * Crimson keeps every stock color. */
#define ACCENTS { 0x424242, 0x424242, 0x6e6e6e, 0xd8d8d8, 0x555555 }, /* Graphite */ \
                { 0xe8123f, 0xe8123f, 0xeb2f56, 0xff1448, 0xeb2f56 }, /* Crimson */ \
                { 0x13838d, 0x13838d, 0x30929b, 0x30929b, 0x30929b }, /* Tidal */ \
                { 0x8c732c, 0x8c732c, 0x9a8446, 0x9a8446, 0x9a8446 }, /* Champagne */ \
                { 0x2a6ad8, 0x2a6ad8, 0x447cdd, 0x447cdd, 0x447cdd }, /* Cobalt, the iPod's blue */ \
                { 0xc45400, 0xc45400, 0xcb681e, 0xcb681e, 0xcb681e }, /* Tangerine, the orange Q2 */ \
                { 0x903868, 0x903868, 0x9d4f7a, 0x9d4f7a, 0x9d4f7a }, /* Plum, the purple Q2 */ \
                { 0x424242, 0x424242, 0x6e6e6e, 0xd8d8d8, 0x555555 }, /* Ivory: Graphite, MINIMAL_FILL fills */ \
                { 0x647f34, 0x647f34, 0x778e4c, 0x778e4c, 0x778e4c }  /* Green, the green Q2 (#119: the shell's sage, darkened for white text); after Ivory so saved values keep */
#define CRIMSON 1
#define MINIMAL_ACCENT 7 /* Ivory, last in the Accent picker's order (setting_click accent_next) */
/* The confirm pop-up's stock red discs (confirm_ok, confirm_cancel and their pressed images) take
 * this dark surface under every accent, Crimson included, through the same red-blend mapping: the
 * OK disc becomes CONFIRM_SURFACE, Cancel's lighter tint a mid grey, and the near-white glyphs stay
 * near white (14.2:1 and 5.6:1). The wheel's focus is the accent tile behind the disc, framed white. */
#define CONFIRM_SURFACE 0x2b2b2b
#define CONFIRM_IMAGE "confirm_"
/* Quick settings' active controls (drop_wifiopen, drop_btopen, drop_highgain, ...) are the same
 * red discs with white glyphs, so they take CONFIRM_SURFACE too. Their inactive discs stay stock
 * #444444 and disabled ones keep their grey glyphs. The brightness suns (DROPDOWN_SUN*) sit on
 * black, not a disc, so they keep the accent's red tone. */
#define DROPDOWN_IMAGE "drop_"
/* Stock's red button backgrounds (btn_enter, btn_red and their pressed images) sit under a white
 * label that is not in the image, so they take the light tone like an image with white on its red. */
#define BUTTON_IMAGE "btn_"
#define DROPDOWN_SUN "drop_ligh"
/* An active control's white glyph turns CONFIRM_SURFACE on an accent tone brighter than this
 * (perceived 0-255, 0.299 R + 0.587 G + 0.114 B): Graphite's silver; the others keep white. */
#define GLYPH_LIGHT_MAX 160
/* bitmap_t (docs/internals.md#accent): format u16 @0xe, 1 RGBA, 2 ABGR, 3 BGRA, 4 ARGB, 4 bytes a pixel
 * by bitmap_get_bpp_of_format; per format, the byte offsets of r, g, b and a. */
#define BITMAP_RGBA_AT { { 0, 1, 2, 3 }, { 3, 2, 1, 0 }, { 2, 1, 0, 3 }, { 1, 2, 3, 0 } }
#define BITMAP_PREMULTI 16 /* AWTK's BITMAP_FLAG_PREMULTI_ALPHA in bitmap_t's u16 flags @0xc */
#define STYLE_COLOR_GRADIENT_RET 0x65d560 /* style_get_color's bal style_get_gradient (0x65d558) returns here */
#define STOCK_RED 0xff1448
#define RED_TOLERANCE 8 /* per channel, 0-255: how far a color may sit from a red/neutral blend */
/* iPod black status chrome, in every theme; TRACK_COLOR is Now Playing's progress track (a theme may
 * state its own, playing.scrubber.track) and the track of the sliders drawn in its look. */
#define BAR_COLOR 0x000000
#define TRACK_COLOR 0x1c1c1c
/* iPod: the confirm pop-up's Cancel row (tools/ipod.py confirm_dialog); its prompt is centred above
 * it at CONFIRM_PROMPT_PX (confirm_prompt). */
#define CONFIRM_TOP 208
#define CONFIRM_PROMPT_PX 24
#define NP_TIMES_PX 14 /* Now Playing's times, and the volume text in their place */
/* Over a status row's set's (OLED's) Now Playing the volume has no text: the bar stays on the
 * progress bar's rows, VOL_SPK_INSET in from each end, with a VOL_SPK_PX speaker (tools/art.py
 * vol_lo_*, vol_hi_*) centred VOL_SPK_X in from each end of the progress bar, on its centre line. */
#define VOL_SPK_INSET 24
#define VOL_SPK_X 8
#define VOL_SPK_PX 16
#define NP_ART_RADIUS 12 /* Now Playing's art corners (paint_cover), as stock's play_defaultcover scaled to the art */
/* A radius-r corner's arc at row y (0 the outermost): how far in it starts, in 1/16 pixels. */
static inline int arc_out(int r, int y) {
    int d = 2 * (r - y) - 1, v = (4 * r * r - d * d) * 64, s = 0;
    while ((s + 1) * (s + 1) <= v) ++s;
    return 16 * r - s;
}
#define PLACEHOLDER "default_album_flow" /* Cover Flow's and Spotify's missing art */
#define VOL_POLL_MS 16 /* how often the volume indicator checks for a change, and eases its bar */
/* Stock's volume dialog's close count, a byte: its timer (on_volume_tsup_fun's, 0x4a3248) adds 1 a
 * tick and closes the dialog at 31; dialog_volume_dialog_init and volume_dialog_on_keyup zero it.
 * volume.c vol_show zeroes it too, so a headset's change keeps the dialog up as a wheel tick does. */
#define VOL_DIALOG_COUNT 0xa3b0d0
/* A level bar's ease, the volume's and the brightness's (levels.c level_ease): a first-order
 * follow with this time constant, so 95% of a step in 3 x VOL_EASE_MS. A choice, not a
 * measurement: no source times an iPod's bar. */
#define VOL_EASE_MS 60
/* The wheel's volume acceleration (volume.c vol_accel): (speed / VOL_ACCEL_START)^4 steps a tick,
 * speed in degrees a second, Rockbox's iPod curve; at most VOL_ACCEL_MAX (reached from 475 deg/s),
 * the speed taken as at most VOL_ACCEL_TOP so the power fits 32 bits. VOL_ACCEL_MAX is ours: a
 * tick is 50 degrees at 50% Wheel Sensitivity, so a fast turn moves the volume about 70. */
#define VOL_ACCEL_START 270
#define VOL_ACCEL_MAX 10
#define VOL_ACCEL_TOP 1000
/* The volume panel off Now Playing (vol_paint): exactly over Quick Settings' brightness row
 * (tools/ipod.py checks it), its rounded corners clear of the 80px glass corners. */
#define VOL_PANEL_X 26
#define VOL_PANEL_Y 256
#define VOL_PANEL_H 48
#define VOL_PANEL_PAD 20
#define VOL_PANEL_BAR 10
#define VOL_PANEL_TEXT 52
#define VOL_PANEL_PX 20 /* the panel's number */
#define VOL_PANEL_TRACK 0x5a5a5a
/* The centred 16px clock and battery clear the rounded glass; widths are checked with the stock font. */
#define CLOCK_EDGE 57
#define STATUS_GAP 4
#define BATT_PCT_W 42
#define BATT_PCT_PX 16
#define BATT_BODY_W 16
#define BATT_BODY_H 11
#define BATT_INSIDE_W 28
#define BATT_NUB_W 2
#define BATT_PX 16
#define BATT_GAP 2
/* Icon + Percent: BATT_PX digits' ink (rows 8-20 of the 30 px bar, line box centred) sits a row
 * above the centred BATT_BODY_H icon (rows 10-20), so the icon rises BATT_PCT_DY to share its centre. */
#define BATT_PCT_DY 1
#define BATT_CHARGE_RGB 0x34c759
#define BATT_LOW_RGB 0xff453a /* under BATT_LOW_PCT (navigation.h), the slim battery's red */
#define BATT_WEAK_RGB 0xff9f0a /* a charger too weak to charge (charging.c charge_weak) */
/* Plain Bluetooth ink and codec reaches from the right edge of their stock canvases. */
#define BT_REACH 10
#define BT_CODECS { "bar_sbc", "bar_aac", "bar_aptx", "bar_aptxhd", "bar_ldac" }
#define BT_GLYPH "bar_btcon" /* the white Bluetooth glyph; stock's bar_bt is the grey one */
/* A new codec badge shows CODEC_MS, then fades out and the glyph in, CODEC_STEPS steps of
 * CODEC_STEP_MS each way. */
#define CODEC_MS 1000
#define CODEC_STEP_MS 25
#define CODEC_STEPS 6
/* iPod: the full-screen art (stock's bigcover_page) shows the song name BC_MS, then it and its shade
 * fade out in BC_STEPS steps, BC_STEP_MS apart. The art takes the cover's shape (bc_fit). */
#define BC_MS 3000
#define QS_PULL_Y 31 /* stock's pull-down (on_wm_tsmove_fun, 0x4f2a4c) takes a touch that began above here */
#define QS_HOLD_MS 1000 /* Return's hold over Quick Settings acts once it has closed, within this (quick_poll) */
/* No symbol: Enlarged Album Art's countdown. Now Playing's timer (0x53d810) counts it down while a
 * song plays there and opens bigcover_page at 0, then sets it back to BC_IDLE_FULL (10 s). */
#define BC_IDLE 0x9b3350
#define BC_IDLE_FULL 40
#define BC_TITLE_PAD 12 /* the song name's inset from the art's sides */
/* The screen glass's rounded corners and how far content keeps clear of them (tools/ipod.py). */
#define GLASS_R 80
#define GLASS_SLACK 4
/* A status row's ends (a set's status_y, OLED's), this far in from each side of the screen: the
 * title's text starts here and the battery's nub ends here, under the glass's top curve and clear
 * of it (tools/ipod.py status_row checks), with the list's own columns further out below it. */
#define STATUS_ROW_INSET 62
#define BC_STEP_MS 50
#define BC_STEPS 8
/* iPod drill rows: the stock list_into image sits where stock rows put their own img_into, its left
 * edge CHEVRON_W from the row's right edge (8 px margin + 50 px image), so every `>` lines up. */
#define CHEVRON_W 58
/* iPod Home: Split gives the menu half the screen (HOME_SPLIT_W, the geometry sets' split_row and split_art
 * but OLED's, tools/ipod.py), the art the rest right of it. Labels end HOME_LABEL_END from the row's edge, where the
 * chevron's (and Cover Flow's spinner's) 16 px box starts, 5 px left of OLED's 28 px right column. The selection
 * dot (HOME_DOT px) sits at HOME_DOT_X, clear of the bottom row's corner, HOME_DOT_GAP before the
 * labels (HOME_TEXT_X in tools/ipod.py). */
#define HOME_LABEL_END 41
#define HOME_DOT_X 25
#define HOME_DOT 6
#define HOME_DOT_GAP 8
/* OLED's chevrons are MINIMAL_CHEVRON grey under every accent; the edit pages' OK card is
 * MINIMAL_FILL (edit_paint). */
#define HOME_SPLIT_W 187
#define LOCALMUSIC_BG 0x534fa0 /* the Library page's EVT_WINDOW_BACKGROUND: stops its play gif's timer */
#define STREAM_BG 0x540d30 /* Streaming's, the same */
/* Split's cover pans (docs/ipod.md#home): zoomed DRIFT_ZOOM_PCT, each axis an eased ping-pong of
 * its own period across the zoom's margin (about 1.5 px/s on average on a square cover, as
 * iPod.js's; coverflow.c drift_at), drawn
 * at sub-pixel positions every DRIFT_TICK_MS, a step at most DRIFT_MAX_STEP_MS. A cover whose zoomed
 * fit passes DRIFT_TEXELS (four panels) stays still. Tuned by eye; 2 x DRIFT_X_MS x DRIFT_Y_MS must
 * fit in 32 bits. */
#define DRIFT_ZOOM_PCT 110
#define DRIFT_X_MS 20000
#define DRIFT_Y_MS 26000
#define DRIFT_TICK_MS 200
#define DRIFT_MAX_STEP_MS 500 /* 2.5 ticks: a late tick keeps its time */
#define DRIFT_TEXELS 240640 /* four panels: 4 x 188 x 320, a pane the screen's height */
/* Split's slideshow of library covers (coverflow.c home_drift, slideshow.c): a cover holds
 * SLIDE_HOLD_MS between SLIDE_FADE_MS cross-fades, drawn every SLIDE_TICK_MS while one fades; the
 * next is picked SLIDE_AHEAD_MS before it is due. Each slide drifts at most KB_DRIFT px of its
 * zoomed cover while it zooms between 1 and KB_NEAR (1/65536: 0.9, the window 0.9 of the panel's
 * texels) over its whole life, in or out. A pick reads SLIDE_ART_PX square art (the tallest pane, 320,
 * zoomed DRIFT_ZOOM_PCT); SLIDE_TRIES songs a pick, SLIDE_RECENT albums passed over, SLIDE_RETRY_MS
 * after a pick that found none. */
#define SLIDE_HOLD_MS 6000
#define SLIDE_FADE_MS 1500
#define SLIDE_TICK_MS 100
#define SLIDE_AHEAD_MS 3000
#define SLIDE_ART_PX 352
#define SLIDE_TRIES 8
#define SLIDE_RECENT 6
#define SLIDE_RETRY_MS 30000
#define KB_NEAR 58982
#define KB_DRIFT 24
#define HOME_TOP 8 /* Home's list starts this far below the status bar */
/* Home's main list rests on this many rows (coverflow.c home_overflow): more scroll, and the last
 * stops where the seventh row's foot is. */
#define HOME_REST 7
#define MINIMAL_FILL 0xeeeeec
#define MINIMAL_CHEVRON 0x777777
/* iPod Light Mode (IPOD/LIGHT 1, docs/ipod.md#light-mode): every neutral colour (channels within
 * LIGHT_SPREAD) flips per channel, black to LIGHT_PAPER and white to LIGHT_INK; hues are kept.
 * Accents keep their colours, and ink on a coloured selection stays white. A theme image without
 * colour flips too when it is mostly glyph: under LIGHT_SURFACE (1/n of its area) opaque and darker
 * than LIGHT_GLYPH, so a grey or dark disc keeps its white glyph. */
#define LIGHT_PAPER 0xf4
#define LIGHT_INK 0x16
#define LIGHT_SPREAD 16
#define LIGHT_CHROMA 40
#define LIGHT_GLYPH 0xa0
#define LIGHT_SURFACE 4
/* list.selection.inset's default, SEL_INSET px in from each side of the row; SEL_RADIUS rounds the
 * edit pages' field frame and OK card (edit_paint). */
#define SEL_INSET 6
#define SEL_RADIUS 6
/* list.selection.radius's top and default: half the screen's height, more than half any row, so the
 * bar's ends are always the pill (pill_radius) */
#define SEL_MAX_RADIUS 160
#define TILE_INSET 2 /* album cards stay inside their tile on all four sides */
#define TILE_CLEARANCE 4 /* artwork and labels sit this far inside the card */
#define EDIT_OK_PX 18 /* the date, time and sleep timer pages' OK label, on that card */
#define PAGE_SLIDE_MS 220 /* a page's push; its pop is page_parallax.c's PAGE_POP_MS */
#define GLIDE_MS 300 /* a remembered row's recall glide */
#define HOME_SLIDE_MS 200 /* Cover Flow's wheel step */
#define HOME_FAST_SLIDE_MS 120 /* ... and within HOME_FAST_WINDOW_MS of the last */
#define NP_ARTIST_RGB 0xcccccc
/* Now Playing's text (local, Spotify and Radio): the title over the artist and album, the album
 * lighter than the other grey text so it reads at a glance. */
#define NP_TITLE_PX 22
#define NP_META_PX 18
#define NP_ALBUM_RGB 0xbbbbbb
/* A line too long to fit holds MARQUEE_HOLD_MS at its start and its end: the info page's genre
 * (navigation.c genre_step) and every stock hscroll_label's delay and lull (ringnav_hscroll_start,
 * ringnav_hscroll_lull). */
#define MARQUEE_HOLD_MS 1500
#define MARQUEE_PASSES 3        /* the selected row's title: navigation.c marq_moved */
#define HSCROLL_ONLY_FOCUS 0x78 /* hscroll_label_t, a char: hscroll_label_set_only_focus */
#define HSCROLL_ONLY_PARENT_FOCUS 0x79 /* a char: hscroll_label_set_only_parent_focus */
#define HSCROLL_LOOP 0x7a       /* a char: a pass ends on its lull and starts again (0x5fb86c) */
#define HSCROLL_YOYO 0x7b       /* a char: it scrolls back instead of snapping (0x5fb888) */
#define HSCROLL_ELLIPSES 0x7c   /* a char: stopped, it ends in an ellipsis (hscroll_label_set_ellipses) */
#define HSCROLL_DELAY 0x88      /* ms its start waits before the first step (hscroll_label_start) */
#define HSCROLL_SPEED 0x8c      /* a float, px per ms; 0 runs a pass in its duration (0x5fb394) */
#define HSCROLL_XOFFSET 0x90    /* how far its text has scrolled, px: hscroll_label_set_xoffset */
#define HSCROLL_TEXT_W 0x94     /* its measured text width: hscroll_label_get_range */
#define HSCROLL_LOOP_GAP 0x9c   /* loop_interval_distance: a copy this far after the text, -1 none */
#define HSCROLL_TIMER 0xa4      /* its running timer's id; 0 stopped (stock paints the ellipsis) */
/* Every pass's fastest pace, 0.05f px per ms: stock Now Playing's title and artist (0x53d0e8); its
 * set_hscroll_label_attribute sets the same since V1.36 (0.1 before), some pages 0.2. As float bits,
 * which order as their values for any positive float. */
#define HSCROLL_PACE 0x3d4ccccd
#define TIMER_CTX 0x20          /* timer_info_t.ctx: stock's hscroll lull reads its label there */
#define IDLE_CTX 0x20           /* idle_info_t.ctx: dialog_modal's close reads its dialog there */
/* iPod Now Playing's lyrics (lyrics.c, docs/ipod.md#lyrics): a line of one row is LYRIC_SLOT high,
 * its text and the gap to the next, a longer one more; the first after a blank LYRIC_SLOT
 * (tools/ipod.py's list layout) so it can sit in the middle as the playing line does. The sung
 * part LYRIC_LIT. */
#define LYRIC_SLOT 59
#define LYRIC_LIT 0xffffff
/* Spotify's and Internet Radio's Now Playing layout (tools/ipod.py NP_*, which checks they match):
 * the art and the text 16px from the sides, 12 apart, the bar and times clear of the glass's
 * corners. */
#define SPOT_ART_X 16
/* iPod: Now Playing's art top, clear of the page dots (tools/ipod.py NP_ART_Y) */
#define NP_ART_TOP 42
#define SPOT_ART_Y NP_ART_TOP
#define SPOT_ART_PX 166
/* iPod: the 50px heart/play-mode frame sits this far below the art's bottom edge, so the centred
 * 28px glyph (4px clear at its foot) ends level with the art (tools/ipod.py NP_ICON_PAD) */
#define NP_ICON_PAD 15
#define SPOT_TEXT_X 194
#define SPOT_TEXT_W 165
#define SPOT_BAR_X 21
#define SPOT_BAR_Y 251
#define SPOT_BAR_W 333
#define SPOT_BAR_H 8 /* the bar's view; the theme's np_bar line is painted at its top */
#define SPOT_TIMES_Y 265
#define SPOT_TIME_X 46
/* iPod settings rows (docs/ipod.md#settings): the list starts SET_TOP below the status bar and
 * holds SET_ROWS complete SET_ROW rows. Stock builds each row as a list_item holding a
 * SET_STOCK_W x SET_STOCK_BODY button at x SET_STOCK_X with a SET_STOCK_ICON icon, a label and a
 * 50px trailing image (chevron, tick or switch); items made SET_STOCK_ROW high are normalised to
 * SET_ROW. The icon shrinks to SET_ICON at SET_ICON_X, the text starts SET_GAP after it (or at
 * SET_TEXT_X without one) and trailing images end SET_EDGE from the right edge; test/build.py
 * and test/patch.py check them against the rounded glass in the first and last visible rows. */
#define SET_ROW 68
#define SET_TOP 8
#define SET_ROWS 4
#define SET_ICON 40
#define SET_ICON_X 28
#define SET_GAP 12
#define SET_TEXT_X 20
#define SET_EDGE 24
#define SET_STOCK_ROW 78
#define SET_STOCK_BODY 70
#define SET_STOCK_X 20
#define SET_STOCK_W 335
#define SET_STOCK_ICON 52
#define SET_STOCK_TRAIL 276   /* the usual trailing image x in the stock button */
#define SET_RIGHT_SIDE 150    /* stock children starting here keep to the right edge */
#define LIST_DEFAULT_ITEM_HEIGHT 0x7c  /* list_view default_item_height */
#define LIST_VIEW_LAYOUT 0x5fcd44      /* children_layouter_list_view's layout */
#define LIST_VIEW_LAYOUT_SLOT 0x93a4c4 /* its vtable slot (vtable 0x93a4bc + 8) */
#define LIBRARY_ROW_BODY 0x535540      /* localmusic_page_init's row body immediate (ipod.json) */
#define LIBRARY_ROW_ICON_X 0x535678    /* its icon x (art_inset) */
#define IMAGE_DRAW_SCALE_DOWN 5        /* "scale_down" in the stock draw type table @0x93ae54 */
#define IMAGE_DRAW_CENTER 1            /* "center", the same table */
/* iPod fast-scroll letter: the selected row's first character in white, LETTER_PX high, on a
 * LETTER_BOX square of the stock dark surface at LETTER_ALPHA, centred over the list. */
#define LETTER_BOX 80
#define LETTER_RADIUS 12
#define LETTER_ALPHA 0xd9
#define LETTER_PX 60
#define LETTER_MS 400
/* iPod Now Playing scrub: SCRUB_STEP seconds per detent times the list ramp, previewed until
 * centre or Return commits it, or SCRUB_MS without input commits it and puts the wheel back on
 * volume. */
#define SCRUB_STEP 5
#define DOUBLE_CLICK_MS 250 /* a second centre press within this is a double press */
#define WAKE_MS 500 /* Wake: Double press, the screen-off double press; looser, done blind in a hand */
#define SCRUB_MS 3000
/* With the screen off the UI loop idles this long a turn instead of stock's 8 ms, about 10 turns a
 * second; a screen-off key (volume, play/pause, wake) ends the idle (loop_wake.c). */
#define SCREEN_OFF_SLEEP_MS 100
/* Stock's player status sync (5 a second) and player scheduling (ringnav_mainscheduling, 10) run at
 * least this far apart while ringnav_sleep finds the Q2 dark and nothing needs the position soon
 * (dark_slow: ringnav_dark_usleep); DARK_POLLS in tools/build.py. Lit, with q2player's push
 * (player_push.c), it is the slow check between pushes. */
#define DARK_POLL_US 1000000
/* Dark and playing, the player polls keep stock's pace from this many seconds before the track's
 * end (as the status cache has it): mclAutoChange's Gapless preload (under 6 s), plus a slow poll's
 * age and the status thread's last long sleep. */
#define DARK_END_LEAD_S 10
/* Screen off, nothing playing (media_playing) and the DAC already off (check_dacoff_state, about 31
 * s after playback stops; g_dacoff_time -1): the turn idles DARK_IDLE_SLEEP_MS, about 4 turns a
 * second (longer still: LOOP_DARK_MAX_MS), so a Q2 left dark without Standby stops waking the CPU
 * for nothing. A key ends the idle; Wake's second press is timed at the screen-off pace. */
#define DARK_IDLE_SLEEP_MS 250
/* A wheel turn with the screen off idles neither for this long after a tick (ringnav_sleep). A
 * calibration knob: a turn slower than a tick in this long waits out a dark pass for each. */
#define DARK_TURN_MS 500
/* Without BlueZ's volume signal (below), Bluetooth absolute volume is read back this often with the
 * screen off and no volume limit to hold (BT_VOLUME_LIMIT_MS with one), and 400 ms lit, so a
 * headset's own volume keys show on the Q2 that late; after the headset refused it, this often
 * whatever the screen or the signal (bt_volume_poll). */
#define BT_VOLUME_SLOW_MS 2000
#define BT_VOLUME_LIMIT_MS 1000
/* While BlueZ's volume signal is subscribed (bt_vol_event.c), a signal reads the level on the next
 * UI pass, at most one read every BT_VOLUME_EVENT_GAP_MS (a burst, the wheel's own writes echoed,
 * coalesces at the timed pace above), and with no signal it is still read every
 * BT_VOLUME_FALLBACK_MS, lit or dark, for a stack or headset that sends none. */
#define BT_VOLUME_EVENT_GAP_MS 400
#define BT_VOLUME_FALLBACK_MS 5000
/* Screen on, once IDLE_MS pass without input or a window animation, each turn idles at least
 * IDLE_SLEEP_MS (input ends it at once). */
#define IDLE_MS 2000
#define IDLE_SLEEP_MS 25
/* Past those idles the loop waits longer when nothing needs it sooner (loop_wake.c loop_span): lit,
 * until the next AWTK timer is due, at most LOOP_LIT_MAX_MS; dark and slow (dark_slow), at most
 * LOOP_DARK_MAX_MS, stock's timers riding the pass as they ride the dark idle. A timer of
 * LOOP_ANIM_MS or less (a frame) keeps the idle above. Input and a worker's result end the wait. */
#define LOOP_LIT_MAX_MS 250
#define LOOP_DARK_MAX_MS 1000
#define LOOP_ANIM_MS 50
/* Charge limit: the charger stops at CHARGE_STOP percent and starts again at CHARGE_RESUME,
 * checked every CHARGE_POLL_MS. */
#define CHARGE_STOP 80
#define CHARGE_RESUME 75
#define CHARGE_POLL_MS 5000
/* Without card_watch.c's events, the card's Rockbox and Groovebox binaries, and Home's Folders rule,
 * are looked at again at most this often, sooner on a library scan's end (theme_engine.c card_bins,
 * home.c home_folders). */
#define CARD_RECHECK_MS 5000
/* Hardware watchdog (docs/internals.md#watchdog): the UI loop feeds it every pass, so a loop or a
 * kernel stalled WDT_MS reboots. Well above any ordinary pass (a 65k-song list, a cover decode:
 * seconds). The known long ones on the UI thread, an import's tag reads and a folder add's directory
 * loads (a minute or more on a big card), get WDT_LONG_MS (ringnav_wdt_long), well under the
 * driver's limit of about 23 minutes. */
#define WDT_MS 60000
#define WDT_LONG_MS 900000
/* Stall record (docs/internals.md#watchdog): its thread wakes every STALL_POLL_MS and writes once
 * the UI loop has not passed for STALL_MS, and again at STALL_AGAIN_MS, both a wake and more
 * before WDT_MS. */
#define STALL_POLL_MS 5000
#define STALL_MS 20000
#define STALL_AGAIN_MS 45000
/* Standby limit (docs/internals.md#standby-limit): its three choices after Off, in hours of one
 * Standby, after which the Q2 powers off. */
#define STANDBY_LIMIT_1 1
#define STANDBY_LIMIT_2 8
#define STANDBY_LIMIT_3 24
/* check_cardmount_status's request for on_wm_timer_dialog's TF card toast, and how long after a
 * Standby resume ringnav_cardmount drops it (docs/internals.md#standby-limit). */
#define CARD_TOAST 0xa4bc07
#define CARD_QUIET_MS 15000
/* Smart Wi-Fi (docs/internals.md#smart-wi-fi), checked every SMART_POLL_MS: the radio goes off after
 * SMART_IDLE_MS with nothing needing the network (a new link counts as a use), and whatever wakes it
 * waits up to SMART_CONNECT_MS for the link before going on without it. */
#define SMART_POLL_MS 1000
#define SMART_IDLE_MS 300000
#define SMART_CONNECT_MS 15000
/* --dev builds only (smart_wifi.c remote_poll): q2remote (docs/remote.md) reaches the Q2 over Wi-Fi
 * alone, and nothing it does counts as a use otherwise, so Smart Wi-Fi would end a developer's
 * session SMART_IDLE_MS after the last other use, and with the radio off nothing can connect. Its
 * requests need the network for REMOTE_HOLD_MS after the last one, long enough for a build and an
 * install between two; and demo's first REMOTE_BOOT_MS do, so a Q2 just updated or restarted can be
 * reached before anything else wakes its radio. */
#define REMOTE_HOLD_MS 1800000
#define REMOTE_BOOT_MS 1800000
#define NET_TYPE 0xa500d4 /* no symbol: initNetworkModule's type, 2 Wi-Fi; networkStart and networkStop act only then */
/* The Wi-Fi signal (ringnav_wifi_signal) is read from the driver again at most every
 * WIFI_SIGNAL_MS; its two pollers each ask about once a second. */
#define WIFI_SIGNAL_MS 1000
/* The battery's level, voltage and charge status (get_battery_capacity, from the status bar's
 * one-second timer) are read at most every BATT_DARK_MS with the screen off, on battery, at a level
 * of BATT_DARK_FLOOR or more (ringnav_batt_level); stock's second otherwise. */
#define BATT_DARK_MS 30000
#define BATT_DARK_FLOOR 20
/* Resume (docs/internals.md#resume): tracks of Q2POD RESUMEMIN minutes or longer (crossfade.c
 * resume_min) keep their place in the RESUME_SLOTS most recent; a place within RESUME_EDGE_S of
 * either end is forgotten. Saved on a track change, a pause and every RESUME_SAVE_S of play;
 * restored if the track has played less than RESUME_START_S when first seen playing. */
#define RESUME_SLOTS 512
#define RESUME_EDGE_S 30
#define RESUME_SAVE_S 60
#define RESUME_START_S 5
/* Play counts (docs/internals.md#play-counts): a track over LISTEN_MIN_S counts once half of it,
 * or LISTEN_MAX_S, has been heard; PLAYS_SLOTS counts are kept, the lowest replaced when full, and
 * Most Played lists the top PLAYS_TOP. */
#define LISTEN_MIN_S 30
#define LISTEN_MAX_S 240
#define PLAYS_SLOTS 512
#define PLAYS_TOP 100
/* Ratings (docs/internals.md#ratings): songs keep 1-5 stars, apart from My Fav, and albums their
 * hearts, each in a hashed table of 4,096 words that doubles as it fills, up to RATE_SLOTS words:
 * 12,285 ratings of each, room for a library of 9,199 songs (the owner's, a server's) and more. */
#define RATE_SLOTS 16384
/* A Quick Settings or Control Center button held this long, by a finger (navigation.c quick_long)
 * or Centre (control_center.c cc_keydown), opens its page: the house hold, Edit Library's and Up
 * Next's (LR_HOLD_MS, UN_HOLD_MS). */
#define QUICK_HOLD_MS 550
/* A finger on Control Center's Volume slider sets the volume at most this often (control_center.c
 * cc_drag): each set is the DAC's ioctl, and with Link Volume a D-Bus get and set of the headset's
 * level, on the UI thread. About a frame; the release sets where the finger stopped at once. */
#define CC_VOL_WRITE_MS 30
/* Long Songs (docs/internals.md#long-songs): songs longer than this. */
#define LONG_SONG_S 600
/* Following lyrics' nudge against the wheel (lyric_poll), in px. */
#define BUMP_PX 6
/* Following lyrics resist the wheel as a list's end does (lyric_poll): travel short of
 * LYRIC_PUSH_DETENTS within LYRIC_PUSH_MS of the last only nudges them BUMP_PX.
 * ponytail: fixed feel, tune these two if a stray touch still takes over or a deliberate one
 * feels stuck. */
#define LYRIC_PUSH_DETENTS 2
#define LYRIC_PUSH_MS 1000
#define BAR_VALUE 0x7c /* scroll_bar native value, maintained by list/table scroll callbacks */
#define BAR_MAX 0x78 /* scroll_bar_set_params: the whole content, at least the bar's own h */
#define KEY_CENTER 218
#define KEY_PREV 172
#define KEY_NEXT 173
#define KEY_PLAY 171
#define KEY_RETURN 170 /* on_common_keyup @0x4f2394 */
#define KEY_BACK_BTN 222 /* the previous and next buttons: on_wm_keyup_fun, prev_quick_click */
#define KEY_FWD_BTN 223
#define KEY_LOCKOUT 0xa4bbf8 /* stock: a centre key sets 8, on_wm_timer_setup_state counts it down per 100 ms */
/* Stock drops wheel ticks while KEY_LOCKOUT is set, 800 ms after Centre, so a press that brushes
 * the wheel cannot scroll; only its first LOCKOUT_KEEP x 100 ms are kept (ringnav). */
#define LOCKOUT_KEEP 2
/* Wheel ticks during a page's slide or before its list loads wait up to HOLD_MS, at most HOLD_MAX
 * of them, then play into the page one per HOLD_POLL_MS, so a turn begun on Back or Select carries
 * on instead of being lost. */
#define HOLD_MS 1200
#define HOLD_MAX 8
#define HOLD_POLL_MS 16
/* The wheel (playback.c wheel_sample): knob units per tick at 50% Wheel sensitivity (a seventh of
 * a turn), 200 a turn, and stock's poster encoderknob_thread_run+0x150, which dispatches the
 * thread info's key event. */
#define WHEEL_TICK_UNITS 28
/* The wheel's speed (playback.c wheel_speed_sample), Rockbox's iPod driver's: timed a click of
 * WHEEL_SPEED_CLICK degrees at a time (its 4 of 96 positions), averaged (15 v + new) / 16, reset by
 * WHEEL_SPEED_IDLE_MS without travel (its WHEEL_FAST_OFF_TIMEOUT, 250 ms). */
#define WHEEL_SPEED_CLICK 15
#define WHEEL_SPEED_IDLE_MS 250
#define WHEEL_SPEED_TOP 4000 /* degrees a second: faster samples count as this */
#define WHEEL_POST 0x6386d0
#define WHEEL_INFO_EVENT 0x20 /* event type: 0x110 key down, 0x114 key up */
#define WHEEL_INFO_KEY 0x38   /* the event's key (EVENT_KEY) */
/* The poster's target is the info's main loop, whose input queue (stock event_queue_t: u16 r, w,
 * full, capacity) holds 20 events and drops its oldest when full. A tick is two events, so a fast
 * turn while the UI is busy evicted a touch's release and left the pointer pressed, which stops the
 * wheel until the next tap. wheel_post drops ticks that would leave under WHEEL_QUEUE_FREE slots.
 */
#define WHEEL_INFO_LOOP 0x10
#define LOOP_QUEUE 0x38
#define LOOP_POST 0x18 /* main_loop_t's queue_event slot (playback.c ringnav_queue_event) */
#define WHEEL_QUEUE_FREE 8
/* The UI loop's wait (loop_wake.c): stock AWTK's timer_manager_t keeps its timers in an slist at
 * TM_LIST (node: next at 0, data at SLIST_DATA); a timer_info_t has its ctx at TIMER_CTX, its start
 * (u64 ms) at TIMER_START, its period at TIMER_DURATION and a suspended byte at TIMER_SUSPEND
 * (timer_info_create, timer_manager_next_time). idle_manager_t's slist is at 0. The widget animator
 * manager's first animator is at AM_FIRST (widget_animator_manager_add), and native_window_t's
 * dirty rect count at NW_DIRTY (native_window_invalidate). */
#define TM_LIST 0x14
#define SLIST_DATA 4
#define TIMER_CTX 0x20
#define TIMER_START 0x38
#define TIMER_DURATION 0x40
#define TIMER_SUSPEND 0x4c
#define AM_FIRST 0x10
#define NW_DIRTY 0x38
/* main_loop_simple's pressed byte: the touch thread sets it as it posts a down and clears it as it
 * posts the release (input_dispatch_to_main_loop), whether or not the queue kept them. */
#define LOOP_PRESSED 0x44
/* A pointer press the window manager keeps this long with no pointer event is no finger
 * (navigation.c stale_heal): a release it never got would otherwise hold the wheel and Centre. */
#define STALE_TOUCH_MS 5000
/* An empty Library list's page (navigation.c empty_class) closes once the window manager marks it
 * opened ("stage" WINDOW_STAGE_OPENED, after the open animation): polled every EMPTY_POLL_MS.
 * The close path reads the same property @0x69d218 (3 or 5, suspended). */
#define WINDOW_STAGE_OPENED 3
#define EMPTY_POLL_MS 50
/* A tick needs a ring frame with pressure this recent (tick_gate, navigation.c), and the same gap in
 * frames ends Return's stop. Return's stop (ringnav_queue_event, playback.c): a second Return with
 * WHEEL_STOP_TICKS ticks since the first, the last within WHEEL_ESCAPE_TICK_MS, and the first within
 * WHEEL_ARM_MS. Calibration knobs. */
#define WHEEL_FRESH_MS 1000
#define WHEEL_ARM_MS 2000
#define WHEEL_ESCAPE_TICK_MS 300
#define WHEEL_STOP_TICKS 3
/* A wheel tick posted and not taken by ringnav in this long no longer holds the bar's lean full
 * (playback.c wheel_part): the UI takes one within a frame or two. */
#define WHEEL_TAKE_MS 250
#define BOOT_KEY_GUARD 0xa4bbf9 /* no symbol: set after a Standby resume, on_wm_keyup_fun drops the next centre release */
#define NP_KEY_HELD 0xa4e880 /* Now Playing's long press of 222/223 sets it (0x53b4d0), its release clears it */
#define NP_KEY_MODE 0xa4e881 /* 1 once a key-down stepped the slider with NP_KEY_HELD set (0x53e000) */
/* stock: on_wm_keydown_before_fun clicks (buzzeer_switch) only while it is clear and sets it after;
 * on_wm_keyup_before_fun (ringnav's hook) clears it. A key ringnav takes before stock's key-up body
 * clears it itself, or every later press is silent (docs/internals.md#ipod-row-clicks). */
#define KEY_TONE_LATCH 0xa4bc00
/* no symbol: what stock's editwifi_dialog types, its init's ctx (sw at 0x49a698): nonzero Add Network's password
 * (hint keyboard_password, 0x49a6b4), 0 its network name; kbwifiadd's two rows open it with 1 and 0 (0x49ae08, 0x49abb8) */
#define EDITWIFI_PASSWORD 0xa3b070
#define RETURN_RELEASE_LATCH 0xa4bbfa /* stock long Return arms it; the key-up filter consumes it */
#define NAME_SORT_FLAG 0xa5011d /* no symbol: toolsQueryDbTable's name sort uses comparator 0x5cd188 when set, the pinyin 0x5cc870 when clear */

/* slide_menu_scroll_to @0x606290 and completion @0x6064e4; animator
 * start @0x679060, time_elapse @0x679684, scroll_set_params @0x6956d4. */
#define SLIDE_SPACER 0x84
#define SLIDE_OFFSET 0x94
#define SLIDE_ANIMATOR 0xc8
#define ANIM_ELAPSED 0x10
#define ANIM_START_TIME 0x18
#define ANIM_DURATION 0x20
#define ANIM_X_TO 0x60
#define SLIDE_EASING 8          /* stock scroll_to easing */
#define ANIM_EASING 0x38 /* widget_animator_init (0x679464) stores easing_get(type) here */
#define EASE_LINEAR 0    /* AWTK easing_type_t, as ui_kit.c motion_ease reads them */
#define EASE_QUAD_IN 1
#define EASE_QUAD_OUT 2
#define EASE_CUBIC_IN 4
#define EASE_CUBIC_OUT 5 /* AWTK easing_type_t: starts at 3x the mean speed, then decelerates */
#define EASE_FOLLOW 24 /* past AWTK's 24: coverflow.c's follow, never handed to easing_get */
#define EVT_ANIM_END 0x125

/* Play/Pause hold queue menu (docs/internals.md). AWTK input_device_status lives at
 * wm+0x98 (window_manager_get_input_device_status @0x681ce4): 16 key records of 0x18 bytes from
 * +0x30, each {key, long fired, u64 press time @8, aborted @0x10}. */
#define EVT_KEY_LONG 0x111
#define INPUT_KEYS 0x30
#define INPUT_KEY_SIZE 0x18
#define INPUT_KEY_COUNT 16
#define INPUT_KEY_TIME 0x08
/* stSongInfo rows of p_deque_showlist and the mcl queue. */
#define REC_ID 0x00             /* -1/-2: unknown album/artist */
#define REC_TIDAL_ID 0x04       /* a Tidal queue row's song id (get_tidal_trackurl 0x483fdc) */
#define REC_NAME 0x08           /* the file name; REC_TITLE is the tag's, empty when untagged */
#define REC_PATH 0x0c
#define REC_TITLE 0x10
#define REC_ALBUM 0x14
#define REC_ARTIST 0x18
#define REC_STYLE 0x1c          /* the genre: a Search Genres row's (its callback 0x506d60) */
#define REC_ALBUM_ARTIST 0x24   /* albumsonger; empty or null in folder-queue records */
#define REC_COVER 0x28          /* an online row's cover, a Tidal row's a jpg in /tmp/tidal (player_get_id3info 0x51f6c0) */
#define REC_TYPE 0x34           /* 8 song, 4 directory */
#define REC_TRACK 0x3c          /* the tag's track and disc numbers, 0 when untagged */
#define REC_DISC 0x40
#define REC_CUE_START 0x48      /* a CUE track's start and end seconds, 0 otherwise */
#define REC_CUE_END 0x4c
#define REC_YEAR REC_CUE_END /* a Year ranking row's year (coverflow.c), matched, never played */
/* A row's size, as platformTypeRegister registers stSongInfo (0x5c6be0). Its copy (0x5c65ac) gives
 * each of the 11 strings at 0x04-0x2c its own buffer, so a row owns them and nothing points into it. */
#define REC_SIZE 0x58
/* g_play_id3_info, as player_get_id3info (0x51f3a4) parses the playing file: its path, then tags. */
#define ID3_ALBUM 0x404
#define ID3_ARTIST 0x504
#define ID3_TITLE 0x684
#define MUSIC_INFO_ALBUM_ARTIST 0x580 /* toolsGetMusicInfo's info (ID3_ALBUM's base): abmartist, the scan's albumsonger */
#define ID3_ALBUM_ARTIST (ID3_ALBUM + MUSIC_INFO_ALBUM_ARTIST) /* a CUE track's, from its file (ringnav_id3_path) */
#define CLASS_ALBUMS 0xf003     /* all albums: rows open getMusicByAlbum (load_localclass_list 0xff10) */
/* localmusic/playlist_page's add mode (g_play_info + 0x7894): non-zero while it is Add to Playlist;
 * 0 when browsing the Playlists list, whose rows' trailing dots open playlistsmore_page. */
#define PLAYLIST_ADD_MODE 0xa3b7d4
/* The artist page's navbar title (g_play_info + 0x7680, 512 bytes): localmusic_artistinfo_page_init
 * copies g_local_classinfo_save + 0x10d into it, which load_artistinfo_list wrote as "%s" of the
 * record's REC_ARTIST: "(null)" for an artist with no name. Each tab's navbar build (0x4ad020)
 * reads it after load_localartist_list. */
#define ARTIST_TITLE 0xa3b5b4
/* g_allselect_flag: playlist_page's Select All (0x4b46a4) toggles on it; a row's mark sets it to
 * whether every row is now marked (0x4b4950). */
#define G_ALLSELECT_FLAG 0xa4ca30
/* Marked folders stay indexed for playlists; the wrappers expand this before grouping. */
#include "library_scope.h"
#define MUSIC_WHERE "fileurl not like '/mnt/mmc/audiobooks/%' and fileurl not like '/mnt/mmc/podcasts/%' and fileurl not like '%.m4b' and " LIBRARY_SCOPE_TOKEN
/* Tags may store a full date (20220510 or 2022-05-10); compare the year before aggregating. */
#define TRACK_YEAR "case when cast(year as text) glob '[1-9][0-9][0-9][0-9]*' then cast(substr(year,1,4) as integer) else 0 end"
#define ALBUM_YEAR "max(" TRACK_YEAR ")"
/* One row per album, as album_cmp tells them apart (coverflow.c; library_lists.c's Era). */
/* An album group's art track: its lowest disc then track then path, as Coverflow lists it. A bare
 * fileurl would be whichever row SQLite saw last, so an album whose tracks carry different covers
 * showed a random one. Each number is clamped to 0-9999 so its prefix stays four digits (a date as
 * the track, or -1, would cut the path). */
#define ALBUM_TRACK \
    "substr(min(substr(10000+min(max(ifnull(cd_num,0),0),9999),2)||" \
    "substr(10000+min(max(ifnull(track,0),0),9999),2)||fileurl),9)"
#define ALBUM_GROUP " group by album COLLATE NOCASE," \
    "ifnull(albumsonger,'') COLLATE NOCASE,case ifnull(albumsonger,'') when '' then rtrim(fileurl,replace(fileurl,'/','')) end"
#define CLASS_ARTIST_ALBUMS 0xff01 /* an artist's albums (load_localclass_list 0xff11) */
#define ALBUMINFO_ROW 0xa3b0f0  /* the albums row localmusic_albuminfo_page_init opened (0x4a9b10) */
/* Static mcl queue state (mclLoadPlayList @0x5bcfc8, mclNextSong @0x5c063c). */
#define MCL_POOL 0xa4ff88       /* deque_t * of int: unplayed shuffle indices */
/* The playing track's lyrics (mclGetLyricSize @0x5bd314): a deque_t * of stLyricInfo { int time;
 * char *text; }, whose copy duplicates the text, and the mutex every reader and the loader hold. */
#define MCL_LYRICS 0xa4ff84
#define MCL_LYRIC_LOCK 0xa4ff90
/* demo's GOT entry for taglib_file_new, libtag_c's own address once a call has bound it, and the
 * pinned libtag_c.so.2.0.1's (build.py TAG_C_SHA) offsets of it and the property calls demo lacks. */
#define TAGLIB_NEW_GOT 0xa3a510
#define TAGLIB_FILE_NEW 0x98e0
#define TAGLIB_PROPERTY_GET 0xb4dc
#define TAGLIB_PROPERTY_FREE 0xb700
/* Now Playing's ⋯ page (playermore_page_init @0x539f60): its 8 rows' kinds by position, which the
 * row click (0x539dbc) reads at the button name's atoi, and Download Cover & Lyrics' kind
 * (get_playmore_showinfo @0x50be68: player_onlinesearch). */
#define PLAYMORE_ROWS 0x786a70
#define PLAYMORE_COUNT 8
#define PLAYMORE_SEARCH 4
#define PLAYMORE_DELETE 7
#define MCL_LASTPOS 0xa50068    /* shuffle "previous" index, -1 none */
#define MCL_PREPOS 0xa5006c
#define MCL_PRELOAD 0xa50070    /* 1 preloaded, 0 none, -1 closed for this track */
#define MCL_START_SEC 0xa50064  /* the next start's place, seconds of the file (mclSetStartSeekTime), -1 none; mclStartPlayer reads and clears it */
#define MCL_TYPE 0xa50078       /* queue classType: 1 folder, 0xfxxx local */
#define MCL_POS 0xa5007c
#define MCL_MODE 0xa50080       /* 0 order, 1 repeat one, 2 shuffle, 3 repeat all */
#define MCL_JUMPFOLDER 0xa5002b /* byte: Folder Skip, read only for class 1 queues */
#define MCL_GAPLESS 0xa5002c    /* byte: stock's Gapless setting (mclGetGaplessMode); mclAutoChange preloads only with it */
#define MCL_FD 0xa5008c         /* player socket, -1 none */
/* g_repeatinfo, A-B Repeat's state: byte on, then words A and B (seconds) and 2 once both are set;
 * check_abrepeatmode (0x51d3d0) seeks back to A each idle pass that finds the position past B. */
#define REPEAT_INFO 0xa551fc
#define REPEAT_AB_STATE 0xc
#define INITCONFIG_MODE_RET 0x51e0d0 /* player_initconfig's mclSetPlayMode call (jalr 0x51e0c8) returns here */
/* mclUsbAudioSetVol (0x5bec64): the USB DAC's volume control, -1 before the first set, -2 when it
 * has none and hciplayer's soft volume (mcl-softvolflag) applies instead (0x5bab48 opens hw:1). */
#define USB_MIXER 0xa50044
/* Stock sortselect_dialog callbacks the menu replaces: both set g_sort_changeflag, which makes
 * allmusic reload and scroll to the top on return. */
#define SORTSELECT_KEYUP 0x4a0b54
#define SORTSELECT_CLOSE 0x4a0810

/* Coverflow (docs/internals.md). slide_menu dispatches EVT_VALUE_CHANGED after its
 * index moves (0x60609c). The stock scan thread handle (dialog_updatemusic_dialog_init) and its
 * done flag (wait_scan sets it) have no symbols: a scan runs while the handle is set and the
 * flag is clear. */
#define EVT_VALUE_CHANGED 0x0e
#define EVT_VALUE_CHANGING 0x0f
/* playset_bootvol_page_init (0x4b7968) binds these, each with the window as context; Crossfade
 * re-dresses the page (crossfade.c) and unbinds them first. */
#define BOOTVOL_SWITCH 0x4b72e8   /* on_bootvol_switch_onclick: img_bootswitch's click */
#define BOOTVOL_DEC 0x4b7434      /* img_dec's click */
#define BOOTVOL_ADD 0x4b73b8      /* img_add's click */
#define BOOTVOL_CHANGING 0x4b7704 /* slider_bootvol's EVT_VALUE_CHANGING */
#define BOOTVOL_CHANGED 0x4b7110  /* slider_bootvol's EVT_VALUE_CHANGED */
#define SCAN_THREAD 0xa3b0c8
/* byte, 1 for the whole library's scan, 0 for a folder's (the same init): our reading of stock's
 * code, not checked on a device */
#define SCAN_WHOLE 0xa3b0c0
#define SCAN_DONE 0xa3b0c4
/* Coverflow text: every label, captions included, sits CF_EDGE from the sides, where the lowest
 * track row's text clears the bottom corners. Album names and artists scroll
 * within that. Under the covers (CF_TEXT_Y), the album name is CF_NAME_PX white and the artist
 * CF_ARTIST_PX in Text Secondary right under it. CF_GREY is stock's secondary text (the
 * Equalizer's captions). */
#define CF_EDGE 36
#define CF_NAME_PX 24
#define CF_NAME_H 32
#define CF_ARTIST_PX 20
#define CF_ARTIST_H 28
#define CF_GREY 0xffaaaaaa /* color_t #AAAAAA */
/* Coverflow depth (docs/internals.md#coverflow-depth): the covers are drawn in software into one
 * CF_VIEW_W x CF_VIEW_H frame, the top of the page. The selected cover is ART_SIZE (160)
 * square with its top CF_TOP from the frame's; where the side covers stand and how far they turn
 * is the theme's (coverflow.shelf in OLED's theme.xml, after #329's Parameter Lab). Brightness (of
 * 256) is 256 at the centre, CF_BRIGHT1
 * one cover out and CF_BRIGHT2 two and three out, reaching 0 half a step past the third, as the row
 * draws it while the wheel holds the covers. Each cover's reflection is CF_REFLECT px high at native
 * size, from CF_REFLECT_TOP (of 256) of its brightness down to black, so the frame runs
 * to its end, under the caption. The wheel moves one album every CF_STRIDE px. */
#define CF_VIEW_W 375
#define CF_VIEW_H 220 /* CF_TOP + 160 + CF_REFLECT, and a row to spare */
#define CF_CX 187 /* the centre line lies between screen columns 186 and 187 */
#define CF_TOP 13
#define CF_BRIGHT1 192
#define CF_BRIGHT2 128
#define CF_REFLECT 46
#define CF_REFLECT_TOP 90
#define CF_STRIDE 110
#define CF_BOUNCE 5 /* an artist's last album: a step past kicks it 1/5 of a stride, then back */
#define CF_ANGLE 60 /* the tilt coverflow.c's first sine table turns to, degrees */
#define CF_THIRD 1    /* 1: the third out stays, gone half a step past it */
#define CF_TEXT_Y 212 /* the album caption's top, under the frame; the artist follows */
/* Touch on the covers (coverflow.c, docs/internals.md#coverflow-touch) */
#define CF_DRAG_PX 90      /* finger travel per album: about the centre cover's own on screen */
#define CF_SLOP 8          /* px a touch moves before it is a drag, not a tap */
#define CF_PROJECT_MS 400  /* a flick's reach: the finger's speed carried on for this long */
#define CF_FLICK 250       /* px/s: a release this fast moves on at least to the next cover */
#define CF_STILL_MS 60     /* a finger resting this long before it lifts throws nothing */
#define CF_VMAX 6000       /* px/s, the fastest flick counted: about 27 albums' glide */
#define CF_GLIDE_MAX 1400  /* ms, the longest glide */
#define CF_SETTLE_MS 280   /* a release with no flick settles on the nearest cover */
#define CF_TAP_MS 320      /* a tapped side cover's glide to the centre */
#define PH_GREY 0xff333333 /* color_t #333333: a placeholder's grey (navigation.c) */
/* PEQ editor (patch/peq_ui.c): on the main and band screens a PEQ_GRAPH_H view heads the Advanced
 * list and holds the response from PEQ_GRAPH_X to 375 - PEQ_GRAPH_X (20 Hz to 20 kHz, log),
 * +-PEQ_GRAPH_DB at its plot's top and bottom (rows PEQ_PLOT_TOP to PEQ_PLOT_BOTTOM), frequency
 * labels under it. */
#define PEQ_GRAPH_H 96
#define PEQ_GRAPH_X 16
#define PEQ_GRAPH_DB 15
#define PEQ_PLOT_TOP 6
#define PEQ_PLOT_BOTTOM 78
