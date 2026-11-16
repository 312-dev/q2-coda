/* Now Playing's bar: the bar's title, the theme's progress bar and its fill. */
#include "navigation.h"

static int bar_bold __attribute__((section(".scratch"))); /* the bar's title set bold (bar_title) */
static int row_frame(const void *info);
static struct {
    int on;            /* radio as last painted (np_modes) */
    int from, to;      /* the pill's width at the grow's start and its end */
    unsigned at;       /* the grow's start, time_now_ms */
    unsigned timer;    /* the grow's next frame */
    int pressed;       /* the button a tap last pressed, + 1 (np_mode_click) */
    unsigned press_at; /* its time_now_ms: its square fades out over NP_PRESS_MS */
} rad __attribute__((section(".scratch")));

/* Now Playing's album and right-hand time: the remaining time (slider max less value,
 * stock's seconds, so a drag or scrub previews it), or the track's length under Track Time's Total
 * (np_time_total). Labels are written only when their source changes; 1 when the right-hand time
 * was. */
int np_sync(void *top) {
    if (!top || top != st.np_win) return 0;
    if (addon_radio() != rad.on) row_frame(0); /* radio came or went: the row grows or shrinks */
    st.np_bold = theme_font(st.np_title, ROLE_NP, st.np_bold); /* the title, as it changes */
    const theme_t *T = theme();
    if (T->np_meta_font) { /* the artist and album in the theme's face, as their text allows */
        static void *const *const meta[] = { &st.np_artist, &st.np_album };
        for (unsigned i = 0; i < 2; ++i)
            st.np_meta[i] = face_font(*meta[i], T->np_meta_font, 0, st.np_meta[i]);
    }
    unsigned at, n;
    /* a pressed server song while it loads (catalogue.c), else the playing one */
    void *pressed = catalogue_np_now(&at, &n), *r = pressed ? pressed : queue_now(&at, &n);
    const char *album = pressed ? P(pressed, REC_ALBUM) : now_tag(r, REC_ALBUM);
    if (pressed) catalogue_np_text(top);
    unsigned pos[2] = { at, n }; /* a new song of the same album restarts its marquee too */
    unsigned h = hash_bytes(fnv(FNV_SEED, (const unsigned char *)album), (const unsigned char *)pos,
                            sizeof pos);
    if (h != st.np_hash) {
        st.np_hash = h;
        widget_set_text_utf8(st.np_album, album ? album : "");
    }
    int max = widget_get_prop_int(st.np_slider, "max", 0),
        left = max - widget_get_prop_int(st.np_slider, "value", 0);
    if (left < 0) left = 0;
    int total = np_time_total(), shown = total ? max : left, key = shown << 1 | total;
    if (key == st.np_left) return 0;
    st.np_left = key;
    clock_text(st.np_remain, !total, shown);
    return 1;
}

/* 1 when the screen's (x, y) is inside w (np_taps.c's hit test too) */
int np_hit(void *w, int x, int y) {
    for (void *p = w; p && p != window_manager(); p = P(p, W_PARENT))
        x -= I(p, W_X), y -= I(p, W_Y);
    return (unsigned)x < (unsigned)I(w, W_W) && (unsigned)y < (unsigned)I(w, W_H);
}

/* A tap on the right-hand time (playing_page's label_ipod_remain): a touch that came down on it and
 * lifts on it switches Track Time. The page takes the touch, as the lyrics' column
 * (slide_view_view) lies over the label and gets it; the time is outside the slider's target, so
 * it never seeks. Returns 0, so the window manager keeps the touch as it would. */
int np_time_tap(void *ctx, void *event) {
    (void)ctx;
    int type = I(event, EVENT_TYPE),
        on = st.np_remain && np_hit(st.np_remain, I(event, EVENT_X), I(event, EVENT_Y));
    if (type == EVT_POINTER_DOWN) {
        st.np_time_down = on;
        return 0;
    }
    int tap = type == EVT_POINTER_UP_BEFORE && st.np_time_down && on;
    st.np_time_down = 0;
    if (tap && np_time_toggle()) {
        st.np_left = -1;
        np_sync(st.np_win);
    }
    return 0;
}

/* Time in Title off: the bar's centre names the page in front, as an iPod's, in the bar role's
 * face: Now Playing or Cover Flow, else stock's title in the page's hidden navbar (scrlabel_title,
 * or a settings page's unnamed first child). Home, a page showing its navbar and an untitled page
 * keep the clock; a pop-up or keyboard leaves the bar as it is. This runs in a paint, which drops
 * the label's invalidate, so bar_repaint follows; the clock waits for its timer. */
void bar_title(void *bar, void *top) {
    static const unsigned
        now_playing[] = { 'N', 'o', 'w', ' ', 'P', 'l', 'a', 'y', 'i', 'n', 'g', 0 },
        cover_flow[] = { 'C', 'o', 'v', 'e', 'r', ' ', 'F', 'l', 'o', 'w', 0 }, none = 0;
    if (st.bar_title) { /* this bar names the page in its own label: off, the clock makes way */
        const unsigned *own = widget_get_text(st.bar_title);
        /* a status row (tools/ipod.py status_row): the title where the page has one, the clock
         * either way */
        int titled = (!st.time_title || geom_val(V_STATUS_Y)) && own && *own;
        void *clock = titled != st.bar_titled ? widget_lookup(bar, "label_clock", 1) : (void *)0;
        if (!clock) return;
        st.bar_titled = titled;
        st.clock_key = 0;
        /* a status row keeps the clock beside a title (navigation.c clock_place places both) */
        if (titled && !geom_val(V_STATUS_Y))
            widget_set_text(clock, &none);
        else
            rearm(&st.clock_timer, clock_due, 0); /* clock_show writes it again */
        /* the title takes the clock's room while it makes way, up to EQ's box, parked where the
         * right group's room starts (tools/ipod.py) */
        static int title_w;
        if (!title_w) title_w = I(st.bar_title, W_W);
        void *eq = st.bar_eq ? P(st.bar_eq, W_PARENT) : (void *)0;
        int wide = eq ? I(eq, W_X) + I(st.bar_eq, W_X) - I(st.bar_title, W_X) : title_w;
        if (!geom_val(V_STATUS_Y)) /* a status row's: clock_place */
            widget_resize(st.bar_title, titled && wide > title_w ? wide : title_w,
                          I(st.bar_title, W_H));
        if (!st.bar_timer) st.bar_timer = timer_add(bar_repaint, (void *)0, 0);
        return;
    }
    if (st.time_title && !st.bar_titled) return; /* the clock, every paint */
    if (!top || tk_strcmp(widget_get_type(top), "window")) return;
    void *label = widget_lookup(bar, "label_clock", 1);
    if (!label) return;
    const unsigned *title = (void *)0;
    if (!st.time_title) {
        void *nav = widget_lookup(top, "view_navbar", 0), *t = (void *)0;
        if (nav && !widget_get_visible(nav)) {
            t = widget_lookup(nav, "scrlabel_title", 0);
            if (!t && widget_count_children(nav)) t = widget_get_child(nav, 0);
            if (t && tk_strcmp(widget_get_type(t), "hscroll_label")) t = (void *)0;
        }
        title = top == st.np_win                                                     ? now_playing
                : !tk_strcmp(widget_get_prop_str(top, "name", ""), "coverflow_page") ? cover_flow
                : t ? widget_get_text(t)
                    : (void *)0;
        if (title && !*title) title = (void *)0;
        static const unsigned null_name[] = { '(', 'n', 'u', 'l', 'l', ')', 0 };
        const unsigned *a = title, *b = null_name; /* stock's "%s" of a NULL name: no title */
        while (a && *a && *a == *b) ++a, ++b;
        if (a && !*a && !*b) title = (void *)0;
    }
    if (!title) {
        if (!st.bar_titled) return;
        st.bar_titled = 0;
        st.clock_key = 0; /* clock_show writes it again */
        if (bar_bold) label_font(label);
        bar_bold = 0;
        rearm(&st.clock_timer, clock_due, 0);
        return;
    }
    const unsigned *had = widget_get_text(label), *want = title;
    while (had && *had && *had == *want) ++had, ++want;
    int changed = !st.bar_titled || !had || *had != *want;
    if (changed) widget_set_text(label, title);
    st.bar_titled = 1;
    int bold =
        theme_font(label, ROLE_NP, bar_bold); /* a new Theme can change it under the same title */
    if ((changed || bold != bar_bold) && !st.bar_timer)
        st.bar_timer = timer_add(bar_repaint, (void *)0, 0);
    bar_bold = bold;
}

/* No knob in any state on a plain slider (tools/ipod.py plain_slider): Now Playing's, Quick
 * Settings' brightness and the settings slider pages'. The asset's style name has no theme entry,
 * and stock then reads what the inline style leaves out from its default slider style (0x6b48cc),
 * whose icon is the knob: rounded_n, rounded_o focused or over, rounded_p pressed. With one, stock
 * draws it, sizes the dragger to it and fills and seeks short of the ends by half of it. An icon
 * that's no string reads as none (value_str). An empty one in the asset would too, as on stock's
 * own brightness bar, but stock then looks the image up at every paint. */
void slider_knobless(void *slider) {
    static const char *const knob[] = { "style:normal:icon", "style:pressed:icon",
                                        "style:over:icon", "style:focused:icon",
                                        "style:disable:icon" };
    for (unsigned i = 0; i < sizeof knob / sizeof *knob; ++i)
        widget_set_prop_int(slider, knob[i], 0);
}

/* Scrub (docs/internals.md#scrub-ipod) follows stock's own key seek (0x53e000): the page's 250 ms
 * timer stops, the slider and label_playtime show the target, a playing track follows it (seek.c
 * seek_scrub) and a paused one seeks there once on exit, in track seconds plus a CUE track's
 * start, and the timer restarts. */
/* The accent's light tone fills the progress bar in every state a touch passes through; a white
 * fill, or the theme's scrub_height line, marks the scrub. */
void slider_fill(void *slider, unsigned color) {
    static const char *const fill[] = { "style:normal:fg_color", "style:pressed:fg_color",
                                        "style:over:fg_color", "style:focused:fg_color" };
    for (unsigned i = 0; i < sizeof fill / sizeof *fill; ++i)
        widget_set_prop_int(slider, fill[i], (int)color);
    slider_knobless(slider);
}
void np_fill(int scrub) {
    if (!st.np_slider) return;
    /* a status row's set (OLED's) keeps the accent and marks the scrub with paint_scrubber's knob
     */
    slider_fill(st.np_slider,
                scrub && !geom_val(V_STATUS_Y) ? 0xffffffff : RGBA(accent_tone(TONE_LIGHT)));
    /* a theme whose fill is near white already marks the scrub with a thicker line */
    const theme_t *T = theme();
    if (T->np_scrub != T->np_bar)
        widget_set_prop_int(st.np_slider, "bar_size", scrub ? T->np_scrub : T->np_bar);
    widget_invalidate_force(st.np_slider, (void *)0);
}

/* Now Playing's bar between the player's whole seconds (docs/internals.md#now-playing-ipod): stock
 * sets the slider's value once a second, so the fill stepped a second's width at a time. While
 * playing, outside a scrub, it runs on from the value by the time since that changed, at most a
 * second, so it never goes back; bar_glide repaints the slider each time that moves it a pixel.
 * Stock's slider's fill is drawn again to it over stock's, in np_fill's colour, bar_size high with
 * np_theme's round ends. */
#define GLIDE_MIN_MS 33 /* the fastest repaint, about 30 a second */
/* ms until the bar's next pixel, or max when it wants none that soon (ringnav_sleep) */
unsigned np_glide_wait(unsigned max) {
    if (!st.np_glide_timer) return max;
    unsigned left = st.np_glide_due - (unsigned)time_now_ms();
    return (int)left < 0 ? 0 : left < max ? left : max;
}
static int bar_glide(const void *info) {
    (void)info;
    st.np_glide_timer = 0;
    if (st.np_slider) widget_invalidate_force(st.np_slider, (void *)0);
    return 0; /* one shot: the paint arms the next */
}
/* ms the fill has run on past the slider's value v, which is the playing track's */
static int glide_ms(int v, int playing) {
    unsigned now = (unsigned)time_now_ms();
    if (v != st.np_glide_v || !playing || !st.np_glide_play)
        st.np_glide_v = v, st.np_glide_at = now;
    st.np_glide_play = playing;
    unsigned ms = now - st.np_glide_at;
    return ms > 1000 ? 1000 : (int)ms;
}

/* The progress bar's glide over stock's, as above. A status row's set (OLED's) marks a scrub with a
 * NP_KNOB px white knob on the fill's end instead of a white fill. */
#define NP_KNOB 14
void paint_scrubber(void *w, void *canvas) {
    if (!w || w != st.np_slider || !P(canvas, CANVAS_LCD)) return;
    int bw = I(w, W_W), lo = widget_get_prop_int(w, "min", 0),
        n = widget_get_prop_int(w, "max", 100) - lo,
        v = clamp_step(0, n, widget_get_prop_int(w, "value", 0) - lo);
    int playing = !st.scrub && n > 0 && v < n && mclGetPlayStatus() == 2,
        fill = n > 0 ? (bw * v + bw * glide_ms(v, playing) / 1000) / n : 0;
    if (fill > bw) fill = bw;
    if (n > 0 && fill > bw * v / n) {
        int h = widget_get_prop_int(w, "bar_size", theme()->np_bar);
        rect_t r = { 0, (I(w, W_H) - h) / 2, fill, h };
        unsigned saved = (unsigned)I(P(canvas, CANVAS_LCD), LCD_FILL_COLOR);
        fill_box(canvas, &r, RGBA(accent_tone(TONE_LIGHT)), (unsigned)h / 2);
        stock_canvas_set_fill_color(canvas, saved);
    }
    if (st.scrub && n > 0 && geom_val(V_STATUS_Y)) { /* the scrub's knob, as a slider's (np_fill) */
        int x = fill < NP_KNOB / 2        ? NP_KNOB / 2
                : fill > bw - NP_KNOB / 2 ? bw - NP_KNOB / 2
                                          : fill;
        rect_t knob = { x - NP_KNOB / 2, (I(w, W_H) - NP_KNOB) / 2, NP_KNOB, NP_KNOB };
        unsigned saved = (unsigned)I(P(canvas, CANVAS_LCD), LCD_FILL_COLOR);
        fill_box(canvas, &knob, ui_ink(RGBA(0xffffff)), NP_KNOB / 2);
        stock_canvas_set_fill_color(canvas, saved);
    }
    if (playing && !st.np_glide_timer) {
        int per_px = (n * 1000 + bw - 1) / (bw > 0 ? bw : 1); /* rounded up: a pixel a frame */
        unsigned ms = per_px < GLIDE_MIN_MS ? GLIDE_MIN_MS : per_px > 1000 ? 1000 : per_px;
        st.np_glide_due = (unsigned)time_now_ms() + ms;
        st.np_glide_timer = timer_add(bar_glide, (void *)0, ms);
    }
}

/* The theme's progress bar over the asset's square 4px line: its np_bar (playing.scrubber.height),
 * ends round, and stock's play-state glyph at the theme's playing.state.opacity. */
void np_theme(void) {
    static const char *const radius[] = { "style:normal:round_radius", "style:pressed:round_radius",
                                          "style:over:round_radius", "style:disable:round_radius",
                                          "style:focused:round_radius" };
    void *glyph = st.np_win ? widget_lookup(st.np_win, "img_playstate", 1) : (void *)0;
    const theme_t *T = theme();
    int h = T->np_bar;
    for (unsigned i = 0; st.np_slider && i < sizeof radius / sizeof *radius; ++i)
        widget_set_prop_int(st.np_slider, radius[i], h / 2);
    if (st.np_slider) widget_set_prop_int(st.np_slider, "bar_size", h);
    /* The track: the asset's is TRACK_COLOR. A theme's own (playing.scrubber.track) is written,
     * and TRACK_COLOR written back after one, so a theme on the asset's colour writes nothing. */
    static const char *const track[] = { "style:normal:bg_color", "style:pressed:bg_color",
                                         "style:over:bg_color", "style:disable:bg_color",
                                         "style:focused:bg_color" };
    static unsigned written
        __attribute__((section(".scratch"))); /* the last written ^ TRACK_COLOR */
    if (st.np_slider && (T->np_track_rgb != TRACK_COLOR || written)) {
        for (unsigned i = 0; i < sizeof track / sizeof *track; ++i)
            widget_set_prop_int(st.np_slider, track[i], (int)RGBA(T->np_track_rgb));
        written = T->np_track_rgb ^ TRACK_COLOR;
    }
    if (glyph) widget_set_opacity(glyph, T->np_state_op);
    if (st.np_album) widget_set_opacity(st.np_album, T->np_album_op);
}

/* Shuffle and repeat as two controls (playing.modes.icons): shuffle on or off, and repeat Off, All
 * or One, from stock's one play mode (MCL_MODE: 0 order, 1 repeat one, 2 shuffle, 3 repeat all).
 * Stock has no shuffle under Repeat One, and its Shuffle never ends (it draws again once every
 * track has played), so Repeat One plays as 1 whatever shuffle says, and shuffle with repeat Off
 * or All both play as 2. What a mode can't say is kept here: shuffle under Repeat One
 * (np_shuf_one) and Off chosen under shuffle (np_rep_off; All otherwise), so a control turned
 * back finds the other as it was. Both are saved as [IPOD] NPSHUFONE and NPREPOFF, as stock saves
 * its mode (PLAYMODE), so a restart draws the pair as it was; np_modes_read reads them back on
 * first use. NP_REPEAT_* are repeat's values. */
enum { NP_REPEAT_OFF, NP_REPEAT_ALL, NP_REPEAT_ONE };
static void np_modes_read(void) {
    if (st.np_modes_read) return;
    st.np_modes_read = 1;
    st.np_shuf_one = (unsigned char)config_value("IPOD", "NPSHUFONE", 2, 0);
    st.np_rep_off = (unsigned char)config_value("IPOD", "NPREPOFF", 2, 0);
}
static int np_shuffle_on(int m) {
    np_modes_read();
    return m == 2 || (m == 1 && st.np_shuf_one);
}
static int np_repeat_of(int m) {
    np_modes_read();
    return m == 1                                 ? NP_REPEAT_ONE
           : m == 3 || (m == 2 && !st.np_rep_off) ? NP_REPEAT_ALL
                                                  : NP_REPEAT_OFF;
}
/* Shuffle (on) and repeat (rep) set: the mode they play as, through config_playmode as stock's
 * own play mode setting saves it, and so ringnav_mode's rules (Shuffle Albums or Folders kept
 * under stock Shuffle, other advanced play ended). Unchanged, nothing is sent: repeat between
 * Off and All under shuffle keeps whatever plays. A kept value is written only when it moves. */
void np_mode_set(int on, int rep) {
    np_modes_read();
    if (rep != NP_REPEAT_ONE && st.np_rep_off != (rep == NP_REPEAT_OFF)) {
        st.np_rep_off = rep == NP_REPEAT_OFF;
        write_int_config(st.np_rep_off, "IPOD", "NPREPOFF");
    }
    if (st.np_shuf_one != !!on) {
        st.np_shuf_one = !!on;
        write_int_config(st.np_shuf_one, "IPOD", "NPSHUFONE");
    }
    int to = rep == NP_REPEAT_ONE ? 1 : on ? 2 : rep == NP_REPEAT_ALL ? 3 : 0;
    if (to != *(volatile int *)MCL_MODE) config_playmode(to, 1);
    void *mode = st.np_win ? widget_lookup(st.np_win, "img_playmode", 1) : (void *)0;
    if (mode) widget_invalidate_force(mode, (void *)0);
}
/* A step of one control: shuffle (repeat 0) on or off, or repeat Off, All, One (dir 1) or back. */
void np_mode_step(int repeat, int dir) {
    int m = *(volatile int *)MCL_MODE, on = np_shuffle_on(m), rep = np_repeat_of(m);
    np_mode_set(repeat ? on : !on, repeat ? (rep + (dir < 0 ? 2 : 1)) % 3 : rep);
}
/* Now Playing's row (docs/oled-design.md#now-playing): shuffle, radio, repeat and Up Next across
 * the play mode's target (tools/ipod.py np_grid), which keeps stock's image under opacity 0. The
 * outer buttons' edges stay at the target's ends and the three gaps between the buttons are even.
 * Shuffle and repeat are their glyph in Text Secondary, or the accent with an NP_DOT dot NP_DOT_DY
 * under its centre while on; Repeat One has its own glyph. Radio is the antenna in Text Secondary,
 * or while the playing app plays a radio (addon.c addon_radio) an NP_PILL wide accent pill holding
 * the antenna and "Radio": turning on, the pill grows from the glyph's width over NP_GROW_MS and
 * the gaps shrink with it, the word fading in over the last part (back the same way off). While
 * radio is on, shuffle and repeat are NP_DIM and ignore taps and the Centre cycle: the app picks
 * the order. The one the Centre cycle's wheel sets (np_stage) stands in a Text Primary outline,
 * and a tapped one on a #2C2C2E square that fades out over NP_PRESS_MS
 * (docs/oled-design.md#now-playing, approved as 22 v4 on 2026-11-16): each fits what is drawn, the
 * glyph's ink and, while it shows, the on dot, padded NP_FOCUS_PAD to the outline's inner edge or
 * NP_PRESS_PAD to the square's. */
#define NP_BTN 22  /* a glyph, and a button's width in the row */
#define NP_PILL 92 /* the radio's pill, on */
#define NP_PILL_H 34
#define NP_GROW_MS 200
#define NP_WORD_FROM 60 /* the word shows past this percent of the grow, fading in */
#define NP_DOT 4
#define NP_DOT_DY 15 /* the on dot's centre below the glyph's: 2 px under its 22 px */
#define NP_RADIO_PX                                                                                \
    TYPE(16)              /* "Radio": Inter Medium, the design's 16 px (docs/oled-design.md#type) */
#define NP_RADIO_GLYPH 24 /* the antenna's centre this far left of the pill's, the word 10 px */
#define NP_FOCUS_PAD 6    /* the ink to the focus outline's inner edge; the outline 2 px wide */
#define NP_PRESS_PAD 8    /* the ink to the pressed square's edge */
#define NP_FRAME_R 9      /* both boxes' corners */
#define NP_PRESS_MS 250   /* a tapped button's square fades out over this */
#define NP_PRESS_INK RGB_RAISED_2
#define NP_INK_ALPHA 64 /* a glyph's pixel this opaque or more is its ink */
enum { NB_SHUFFLE, NB_RADIO, NB_REPEAT, NB_QUEUE, NB_N };

static void *np_row_widget(void) {
    return st.np_win ? widget_lookup(st.np_win, "img_playmode", 1) : (void *)0;
}
static int row_frame(const void *info) {
    (void)info;
    rad.timer = 0;
    void *mode = np_row_widget();
    if (mode) widget_invalidate_force(mode, (void *)0);
    return 0;
}
/* The pill's width now, eased 1 - (1 - t)^2 from where it was when radio last changed; 0..100 in
 * *word, the word's share of its ink. A grow under way asks for its next frame. */
static int pill_w(int *word) {
    int on = addon_radio();
    unsigned now = (unsigned)time_now_ms();
    if (!rad.to) rad.from = rad.to = on ? NP_PILL : NP_BTN, rad.on = on; /* first paint: no grow */
    int t = (int)(now - rad.at) * 100 / NP_GROW_MS;
    if (t > 100) t = 100;
    int e = 100 - (100 - t) * (100 - t) / 100, w = rad.from + (rad.to - rad.from) * e / 100;
    if (on != rad.on) /* from where it is now, towards the other end */
        rad.on = on, rad.from = w, rad.to = on ? NP_PILL : NP_BTN, rad.at = now;
    if (w != rad.to && !rad.timer) rad.timer = timer_add(row_frame, (void *)0, 16);
    int grown = (w - NP_BTN) * 100 / (NP_PILL - NP_BTN);
    *word = grown <= NP_WORD_FROM ? 0 : (grown - NP_WORD_FROM) * 100 / (100 - NP_WORD_FROM);
    return w;
}
/* The buttons' centres for a pill w wide, across the target from x0, row wide */
static void row_x(int x0, int row, int w, int cx[NB_N]) {
    int widths[NB_N] = { NP_BTN, w, NP_BTN, NP_BTN }, sum = 3 * NP_BTN + w, left = 0;
    for (int i = 0; i < NB_N; ++i) {
        cx[i] = x0 + left + widths[i] / 2 + i * (row - sum) / 3;
        left += widths[i];
    }
}
/* a over b, share percent of a, each 0xRRGGBB */
static unsigned blend(unsigned a, unsigned b, int share) {
    unsigned out = 0;
    for (int s = 0; s < 24; s += 8)
        out |=
            ((((a >> s) & 0xff) * (unsigned)share + ((b >> s) & 0xff) * (unsigned)(100 - share)) /
             100)
            << s;
    return out;
}
static void glyph(void *win, void *canvas, const char *name, int x, int y) {
    unsigned bitmap[64]; /* bitmap_t */
    if (!widget_load_image(win, theme_art(name), bitmap)) canvas_draw_icon(canvas, bitmap, x, y);
}
/* The ink of bitmap as canvas_draw_icon draws it centred on x, y: the bounds of its pixels at
 * NP_INK_ALPHA or more, into *ink; else the whole image (a format it can't read, or no ink). */
static void glyph_ink(unsigned *bitmap, int x, int y, rect_t *ink) {
    static const unsigned char at[4][4] = BITMAP_RGBA_AT;
    int w = (int)bitmap[0], h = (int)bitmap[1], x0 = w, y0 = h, x1 = -1, y1 = -1;
    unsigned format = ((unsigned short *)bitmap)[7] - 1u;
    const unsigned char *data = format < 4 && w > 0 && h > 0 && w <= 64 && h <= 64
                                    ? bitmap_lock_buffer_for_read(bitmap)
                                    : 0;
    if (data) {
        unsigned stride = bitmap_get_line_length(bitmap), a = at[format][3];
        for (int py = 0; py < h; ++py)
            for (int px = 0; px < w; ++px)
                if (data[(unsigned)py * stride + (unsigned)px * 4 + a] >= NP_INK_ALPHA) {
                    if (px < x0) x0 = px;
                    if (px > x1) x1 = px;
                    if (py < y0) y0 = py;
                    if (py > y1) y1 = py;
                }
        bitmap_unlock_buffer(bitmap);
    }
    if (x1 < 0) x0 = y0 = 0, x1 = w - 1, y1 = h - 1;
    *ink = (rect_t){ x - w / 2 + x0, y - h / 2 + y0, x1 - x0 + 1, y1 - y0 + 1 };
}
/* A row button's glyph centred on x, y, with the on dot under it (dot) and its frame behind:
 * focus, the wheel's outline; press, 1..100, the tapped square's share of its ink, fading. */
static void glyph_framed(void *win, void *canvas, const char *name, int x, int y, int dot,
                         int focus, int press) {
    unsigned bitmap[64]; /* bitmap_t */
    if (widget_load_image(win, theme_art(name), bitmap)) return;
    rect_t ink, d = { x - NP_DOT / 2, y + NP_DOT_DY - NP_DOT / 2, NP_DOT, NP_DOT };
    glyph_ink(bitmap, x, y, &ink);
    if (dot) { /* the box takes the dot in */
        int bottom = d.y + d.h > ink.y + ink.h ? d.y + d.h : ink.y + ink.h;
        ink.h = bottom - ink.y;
    }
    if (press) {
        int p = NP_PRESS_PAD;
        rect_t sq = { ink.x - p, ink.y - p, ink.w + 2 * p, ink.h + 2 * p };
        fill_box(canvas, &sq, RGBA(blend(NP_PRESS_INK, 0, press)), NP_FRAME_R);
    }
    if (focus) { /* 2 px: the ink to its inner edge NP_FOCUS_PAD */
        int p = NP_FOCUS_PAD + 2;
        rect_t ring = { ink.x - p, ink.y - p, ink.w + 2 * p, ink.h + 2 * p },
               in = { ring.x + 2, ring.y + 2, ring.w - 4, ring.h - 4 };
        fill_box(canvas, &ring, ui_ink(RGBA(0xffffff)), NP_FRAME_R);
        fill_box(canvas, &in, ui_ink(RGBA(0)), NP_FRAME_R - 2);
    }
    canvas_draw_icon(canvas, bitmap, x, y);
    if (dot) fill_box(canvas, &d, RGBA(accent_tone(TONE_LIGHT)), NP_DOT / 2);
}
/* The tapped button's square's share now, 100 at the tap down to 0 NP_PRESS_MS on, for button k;
 * a fade under way asks for its next frame. */
static int press_share(int k) {
    if (rad.pressed != k + 1) return 0;
    unsigned ms = (unsigned)time_now_ms() - rad.press_at;
    if (ms >= NP_PRESS_MS) return 0;
    if (!rad.timer) rad.timer = timer_add(row_frame, (void *)0, 16);
    return 100 - (int)(ms * 100 / NP_PRESS_MS);
}

/* A tap on the row (the play mode's target): the button nearest it. Shuffle and repeat step as
 * the Centre cycle's wheel does, unless radio is on; radio opens the playing app's page (addon.c
 * addon_more), where a radio starts and stops; Up Next opens the queue (navigation.c np_queue).
 * 1 when it took the tap, stock's step through the one mode never. */
static int row_radio(const void *info) {
    (void)info;
    addon_more();
    return 0;
}
int np_mode_click(void *target, void *event) {
    if (!target || target != np_row_widget() || !theme()->np_modes) /* the theme last */
        return 0;
    int x = I(event, EVENT_X), word, cx[NB_N], k = 0;
    for (void *p = target; p && p != window_manager(); p = P(p, W_PARENT)) x -= I(p, W_X);
    row_x(0, I(target, W_W), pill_w(&word), cx);
    for (int i = 1; i < NB_N; ++i) /* past the midpoint between two centres: the next */
        if (x > (cx[i - 1] + cx[i]) / 2) k = i;
    if (k != NB_RADIO || !rad.on) /* the pill is its own press */
        rad.pressed = k + 1, rad.press_at = (unsigned)time_now_ms(), row_frame(0);
    if (k == NB_QUEUE)
        timer_add(np_queue, (void *)0, 0);
    else if (k == NB_RADIO)
        timer_add(row_radio, (void *)0, 0);
    else if (!rad.on)
        np_mode_step(k == NB_REPEAT, 1);
    return 1;
}

void np_modes(void *win, void *canvas) {
    void *mode = theme()->np_modes && P(canvas, CANVAS_LCD) ? widget_lookup(win, "img_playmode", 0)
                                                            : (void *)0;
    if (!mode) return;
    int m = *(volatile int *)MCL_MODE, cy = I(mode, W_Y) + I(mode, W_H) / 2, rep = np_repeat_of(m),
        word, w = pill_w(&word), cx[NB_N];
    row_x(I(mode, W_X), I(mode, W_W), w, cx);
    const char *off = ipod_light() ? "_l" : "_d", *dim = ipod_light() ? "_xl" : "_xd";
    unsigned saved = (unsigned)I(P(canvas, CANVAS_LCD), LCD_FILL_COLOR),
             accent = accent_tone(TONE_LIGHT);
    char name[24];
    int lit[2] = { np_shuffle_on(m), rep != NP_REPEAT_OFF };
    for (int i = 0; i < 2; ++i) {
        int k = i ? NB_REPEAT : NB_SHUFFLE, x = cx[k];
        tk_snprintf(name, sizeof name, "mode_%s%s", i ? "repeat" : "shuffle",
                    rad.on                      ? dim
                    : i && rep == NP_REPEAT_ONE ? "1_on"
                    : lit[i]                    ? "_on"
                                                : off);
        /* on: the accent's dot under it; the wheel's stage: the outline round it */
        glyph_framed(win, canvas, name, x, cy, lit[i] && !rad.on,
                     st.np_stage == (unsigned)(NP_STAGE_SHUFFLE + i), rad.on ? 0 : press_share(k));
    }
    if (w > NP_BTN) { /* the pill, the antenna and, grown past NP_WORD_FROM, the word */
        rect_t pill = { cx[NB_RADIO] - w / 2, cy - NP_PILL_H / 2, w, NP_PILL_H };
        fill_box(canvas, &pill, RGBA(accent), NP_PILL_H / 2);
        glyph(win, canvas, "mode_radio_w", word ? cx[NB_RADIO] - NP_RADIO_GLYPH : cx[NB_RADIO], cy);
        if (word)
            ui_text(canvas, "Radio", 1, NP_RADIO_PX, blend(0xffffff, accent, word), 1,
                    cx[NB_RADIO] - 10, cy, w, 2);
    } else
        glyph_framed(win, canvas, ipod_light() ? "mode_radio_l" : "mode_radio_d", cx[NB_RADIO], cy,
                     0, 0, press_share(NB_RADIO));
    glyph_framed(win, canvas, ipod_light() ? "mode_queue_l" : "mode_queue_d", cx[NB_QUEUE], cy, 0,
                 0, press_share(NB_QUEUE));
    stock_canvas_set_fill_color(canvas, saved);
}

/* 1 while radio is on: shuffle and repeat are the app's (np_mode_click, navigation.c np_centre) */
int np_radio_on(void) { return rad.on; }

/* playing.modes.icons: the theme's own heart and More as well (tools/art.py np_fav, np_unfav_*,
 * np_more_*, np_moredown_*). Stock's update and click, and rate_fav_click, keep setting the heart
 * to play_fav or play_unfav, so each paint of the window (ringnav_paint_bg, before the buttons
 * paint) turns either into the theme's, and an empty heart or More drawn for the other half into
 * this one's. An app's heart (tidal_fav_*) stays stock's. ringnav_playing binds the two buttons. */
static void *glyph_fav __attribute__((section(".scratch")));
static void *glyph_more __attribute__((section(".scratch")));
static int glyph_half __attribute__((section(".scratch"))); /* More's: 0 none, else 'd' or 'l' */
void np_glyphs_bind(void *win) {
    glyph_fav = widget_lookup(win, "img_fav", 1);
    glyph_more = widget_lookup(win, "img_more", 1);
    glyph_half = 0;
}
void np_glyphs(void *win) {
    if (win != st.np_win || !theme()->np_modes) return;
    static const char *const state[] = { "style:normal:bg_image", "style:pressed:bg_image",
                                         "style:selected:bg_image", "style:disable:bg_image",
                                         "style:focused:bg_image" };
    int half = ipod_light() ? 'l' : 'd';
    char name[40], own[24];
    const char *now = glyph_fav ? widget_get_prop_str(glyph_fav, "image", "") : "";
    tk_snprintf(own, sizeof own, "%s", theme_art("np_"));
    unsigned n = (unsigned)strlen(own);
    int mine = !strncmp(now, own, n),
        on = !tk_strcmp(now, "play_fav") || (mine && !tk_strcmp(now + n, "fav")),
        off = !tk_strcmp(now, "play_unfav") || (mine && !strncmp(now + n, "unfav_", 6));
    tk_snprintf(name, sizeof name, on ? "%sfav" : "%sunfav_%c", own, half);
    if ((on || off) && tk_strcmp(now, name)) {
        image_base_set_image(glyph_fav, name);
        widget_invalidate_force(glyph_fav, (void *)0);
    }
    if (!glyph_more || glyph_half == half) return;
    glyph_half = half;
    for (unsigned i = 0; i < sizeof state / sizeof *state; ++i) {
        tk_snprintf(name, sizeof name, i == 1 ? "%smoredown_%c" : "%smore_%c", own, half);
        widget_set_prop_str(glyph_more, state[i], name);
    }
    widget_invalidate_force(glyph_more, (void *)0);
}
