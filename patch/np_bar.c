/* Now Playing's bar: Classic's times and bar fitted to the track, the bar's title, the theme's
 * progress bar and its fill. */
#include "navigation.h"

static int bar_bold __attribute__((section(".scratch"))); /* the bar's title set bold (bar_title) */

/* Classic's bar's left end for a track sec long: the times' boxes hold "-9:59" under 10:00,
 * "-59:59" under an hour, else "-9:59:59" (NP_IPOD_TIME_W*, tools/ipod.py measures them), their
 * outer edges and the gap kept; the bar takes the rest, mirrored. */
static int ipod_bar_x(int sec) {
    return NP_IPOD_BAR_X - NP_IPOD_TIME_W1 +
           (sec < 600    ? NP_IPOD_TIME_W1
            : sec < 3600 ? NP_IPOD_TIME_W2
                         : NP_IPOD_TIME_W);
}

/* Classic's elapsed and remaining times and bar (with none, the radio's own) fitted to a track sec
 * long (ipod_bar_x); unknown (0 or less) leaves them, so the page keeps the build's "-9:59" boxes
 * and widest bar, or the last track's, whose times hold the 0:00 an unknown length shows. Each
 * keeps its y and height and repaints where it was and where it goes, so the caller is outside a
 * paint, which loses its invalidates (#116). 1 when any moved. */
int ipod_times(void *elapsed, void *remain, void *bar, int sec) {
    if (sec <= 0) return 0;
    int x = ipod_bar_x(sec), tw = x - (NP_IPOD_BAR_X - NP_IPOD_TIME_W1), bw = 375 - 2 * x,
        moved = 0;
    void *w[] = { elapsed, remain, bar ? bar : st.stream_bar };
    const int to[3][2] = { { x - NP_IPOD_TIME_GAP - tw, tw },
                           { x + bw + NP_IPOD_TIME_GAP, tw },
                           { x, bw } };
    for (unsigned i = 0; i < 3; ++i) {
        if (!w[i] || (I(w[i], W_X) == to[i][0] && I(w[i], W_W) == to[i][1])) continue;
        widget_invalidate_force(w[i], (void *)0);
        widget_move_resize(w[i], to[i][0], I(w[i], W_Y), to[i][1], I(w[i], W_H));
        widget_invalidate_force(w[i], (void *)0);
        moved = 1;
    }
    return moved;
}

/* The remaining time, once after a frame whose paint np_sync wrote it in: its invalidate there is
 * lost with the frame's dirty rects (#116). Minimal's and Modern's sit in the rect the slider's and
 * the elapsed time's repaint takes each second; only times beside the bar (np_beside) need it. */
int remain_due(const void *info) {
    (void)info;
    st.np_remain_timer = 0;
    if (st.np_remain) widget_invalidate_force(st.np_remain, (void *)0);
    return 0;
}

/* 1 when Now Playing's times sit on its bar's rows, beside it (Classic's iPod page; its Centred
 * page and the other themes' put them under the bar): outside the slider's repaint. */
int np_beside(void) {
    if (!st.np_remain || !st.np_slider || !st.np_elapsed) return 0;
    int bh = widget_get_prop_int(st.np_slider, "bar_size", 8);
    return I(st.np_elapsed, W_Y) < I(st.np_slider, W_Y) + (I(st.np_slider, W_H) + bh) / 2;
}

/* Classic's times and bar fitted to the playing track's length (the slider's max), off the paint
 * whose np_sync found them stale. */
int np_fit_due(const void *info) {
    (void)info;
    st.np_fit_timer = 0;
    if (np_beside())
        ipod_times(st.np_elapsed, st.np_remain, st.np_slider,
                   widget_get_prop_int(st.np_slider, "max", 0));
    return 0;
}

/* Now Playing's album and right-hand time: the remaining time (slider max less value,
 * stock's seconds, so a drag or scrub previews it), or the track's length under Track Time's Total
 * (np_time_total). Labels are written only when their source changes; 1 when the right-hand time
 * was. Classic's times beside the bar refit when the length's class changes: a
 * seek, a pause or a rate change keeps it. */
int np_sync(void *top) {
    if (!top || top != st.np_win) return 0;
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
        st.np_off = 0;
        stop_timer(&st.np_marquee); /* the next paint restarts it if the album overflows */
    }
    int max = widget_get_prop_int(st.np_slider, "max", 0),
        left = max - widget_get_prop_int(st.np_slider, "value", 0);
    if (max > 0 && !st.np_fit_timer && np_beside() && I(st.np_slider, W_X) != ipod_bar_x(max))
        st.np_fit_timer = timer_add(np_fit_due, (void *)0, 0); /* mid-paint */
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

/* Time in Title off: the bar's centre names the page in front, as an iPod's, in Modern's bold:
 * Now Playing or Cover Flow, else stock's title in the page's hidden navbar (scrlabel_title, or a
 * settings page's unnamed first child). Home, a page showing its navbar and an untitled page keep
 * the clock; a pop-up or keyboard leaves the bar as it is. This runs in a paint, which drops the
 * label's invalidate, so bar_repaint follows; the clock waits for its timer. */
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
        if (bar_bold) label_font(label, 0);
        font_weight_mark(label, 0); /* a CJK title's mark, under bar_bold 0 too */
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
/* The accent's light tone (Minimal's own accent: MINIMAL_FILL) fills the progress bar in every
 * state a touch passes through; a white fill (Minimal: a thicker line) marks the scrub. */
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
    /* Minimal's fill is near white already: its scrub thickens the line to Classic's. */
    const theme_t *T = theme();
    if (T->np_scrub != T->np_bar)
        widget_set_prop_int(st.np_slider, "bar_size", scrub ? T->np_scrub : T->np_bar);
    widget_invalidate_force(st.np_slider, (void *)0);
}

/* One part of Classic's bar, w wide at x, y: the stops' rows h high, then mirrored under them from
 * SCRUB_REFLECT_A/256 to the paper over SCRUB_REFLECT_ROWS. */
static void bar_part(void *canvas, const stops_t *s, int x, int y, int w, int h, unsigned paper) {
    paint_stops(canvas, x, y, w, h, s);
    for (int k = 0; k < SCRUB_REFLECT_ROWS; ++k) {
        int a = SCRUB_REFLECT_A * (SCRUB_REFLECT_ROWS - k) / SCRUB_REFLECT_ROWS;
        stock_canvas_set_fill_color(canvas, RGBA(mix(paper, stops_row(s, h - 1 - k, h), a, 256)));
        canvas_fill_rect(canvas, x, y + h + k, w, 1);
    }
}

/* Classic's bar (playing.scrubber.stops), an iPod classic's, bw wide and the theme's np_bar high at
 * x, y: the fill (the theme's own scheme's np_fill, another accent's band) over the first `on`
 * columns, the track's rows after them, a column at each end in the track's bottom line's colour
 * whatever the fill reaches, each mirrored under it. With mark, a scrub's cue on the gloss: a
 * diamond in the ink, NP_SCRUB_MARK each side of its middle row and column, at the fill's end, kept
 * inside the bar. 0 when the theme has none. The canvas's fill colour is left as it was. */
int ipod_bar(void *canvas, int x, int y, int bw, int on, int mark) {
    const theme_t *T = theme();
    const stops_t *track = &T->np_track[ipod_light()];
    if (track->n < 2) return 0;
    unsigned saved = (unsigned)I(P(canvas, CANVAS_LCD), LCD_FILL_COLOR), paper = scheme()->paper,
             line = track->rgb[track->n - 1];
    const stops_t side = { 2, { 0, 100 }, { line, line } };
    bar_part(canvas, T->np_fill.n && T->schemes[accent()].label ? &T->np_fill : &scheme()->sel, x,
             y, on, T->np_bar, paper);
    bar_part(canvas, track, x + on, y, bw - on, T->np_bar, paper);
    bar_part(canvas, &side, x, y, 1, T->np_bar, paper);
    bar_part(canvas, &side, x + bw - 1, y, 1, T->np_bar, paper);
    stock_canvas_set_fill_color(canvas, RGBA(scheme()->ink));
    int end = bw - 1 - NP_SCRUB_MARK, cx = on < NP_SCRUB_MARK ? NP_SCRUB_MARK : on,
        cy = y + (T->np_bar - 1) / 2;
    if (cx > end) cx = end;
    for (int j = -NP_SCRUB_MARK; mark && j <= NP_SCRUB_MARK; ++j) {
        int half = NP_SCRUB_MARK - (j < 0 ? -j : j);
        canvas_fill_rect(canvas, x + cx - half, cy + j, 2 * half + 1, 1);
    }
    stock_canvas_set_fill_color(canvas, saved);
    return 1;
}

/* Now Playing's bar between the player's whole seconds (docs/internals.md#now-playing-ipod): stock
 * sets the slider's value once a second, so the fill stepped a second's width at a time. While
 * playing, outside a scrub, it runs on from the value by the time since that changed, at most a
 * second, so it never goes back; bar_glide repaints the slider each time that moves it a pixel.
 * Classic's bar is ipod_bar's, drawn at that fill; the others' is stock's slider, whose fill is
 * drawn again to it over stock's, in np_fill's colour, bar_size high with np_theme's round ends. */
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

/* Classic's progress bar over stock's plain one, centred in the slider as stock's, the scrub's cue
 * on it while a scrub runs; the times sit beside it (tools/ipod.py np_ipod). A status row's set
 * (OLED's) marks a scrub with a NP_KNOB px white knob on the fill's end instead of a white fill. */
#define NP_KNOB 14
void paint_scrubber(void *w, void *canvas) {
    if (w && w == st.stream_bar && P(canvas, CANVAS_LCD))
        ipod_bar(canvas, 0, 0, I(w, W_W), I(w, W_W), 0);
    if (!w || w != st.np_slider || !P(canvas, CANVAS_LCD)) return;
    int bw = I(w, W_W), lo = widget_get_prop_int(w, "min", 0),
        n = widget_get_prop_int(w, "max", 100) - lo,
        v = clamp_step(0, n, widget_get_prop_int(w, "value", 0) - lo);
    int playing = !st.scrub && n > 0 && v < n && mclGetPlayStatus() == 2,
        fill = n > 0 ? (bw * v + bw * glide_ms(v, playing) / 1000) / n : 0;
    if (fill > bw) fill = bw;
    if (!ipod_bar(canvas, 0, (I(w, W_H) - theme()->np_bar) / 2, bw, fill, st.scrub) && n > 0 &&
        fill > bw * v / n) {
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

/* The rows painter's bar (the volume panel, a card app's progress): the track's rows, then the
 * fill's, the theme's own scheme's (Classic Blue) or another accent's band, as Now Playing's bar
 * (ipod_bar). */
void paint_rows_bar(void *canvas, int x, int y, int w, int h, int fill_w) {
    const theme_t *T = theme();
    const unsigned(*rows)[NP_ROWS] = T->np_rows + 2 * ipod_light();
    const stops_t *band = T->schemes[accent()].label ? (const stops_t *)0 : &scheme()->sel;
    unsigned fill = (unsigned)I(P(canvas, CANVAS_LCD), LCD_FILL_COLOR);
    for (int j = 0; j < h && j < NP_ROWS; ++j) {
        scrub_row(canvas, rows[0][j], x, y + j, w);
        if (fill_w > 0)
            scrub_row(canvas, band ? 0xff000000u | stops_row(band, j, h) : rows[1][j], x, y + j,
                      fill_w < w ? fill_w : w);
    }
    stock_canvas_set_fill_color(canvas, fill);
}
/* The Theme's progress bar over the asset's (Minimal's square 4px line): its np_bar, Modern's
 * NP_BAR_CLASSIC capsule or Classic's NP_BAR_IPOD (paint_scrubber draws it), and stock's play-state
 * glyph, which Minimal and Classic hide (playing.state.opacity). */
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
    static unsigned written = TRACK_COLOR;
    if (st.np_slider && (T->np_track_rgb != TRACK_COLOR || written != TRACK_COLOR)) {
        for (unsigned i = 0; i < sizeof track / sizeof *track; ++i)
            widget_set_prop_int(st.np_slider, track[i], (int)RGBA(T->np_track_rgb));
        written = T->np_track_rgb;
    }
    if (glyph) widget_set_opacity(glyph, T->np_state_op);
    /* Minimal: the album is paint_caps' capitals. */
    if (st.np_album) widget_set_opacity(st.np_album, T->np_album_op);
}

/* Shuffle and repeat as two controls (playing.modes.icons): shuffle on or off, and repeat Off, All
 * or One, from stock's one play mode (MCL_MODE: 0 order, 1 repeat one, 2 shuffle, 3 repeat all).
 * Stock has no shuffle under Repeat One, and its Shuffle never ends (it draws again once every
 * track has played), so Repeat One plays as 1 whatever shuffle says, and shuffle with repeat Off
 * or All both play as 2. What a mode can't say is kept here: shuffle under Repeat One
 * (np_shuf_one) and Off chosen under shuffle (np_rep_off; All otherwise), so a control turned
 * back finds the other as it was. NP_REPEAT_* are repeat's values. */
enum { NP_REPEAT_OFF, NP_REPEAT_ALL, NP_REPEAT_ONE };
static int np_shuffle_on(int m) { return m == 2 || (m == 1 && st.np_shuf_one); }
static int np_repeat_of(int m) {
    return m == 1                                 ? NP_REPEAT_ONE
           : m == 3 || (m == 2 && !st.np_rep_off) ? NP_REPEAT_ALL
                                                  : NP_REPEAT_OFF;
}
/* Shuffle (on) and repeat (rep) set: the mode they play as, through config_playmode as stock's
 * own play mode setting saves it, and so ringnav_mode's rules (Shuffle Albums or Folders kept
 * under stock Shuffle, other advanced play ended). Unchanged, nothing is sent: repeat between
 * Off and All under shuffle keeps whatever plays. */
void np_mode_set(int on, int rep) {
    if (rep != NP_REPEAT_ONE) st.np_rep_off = rep == NP_REPEAT_OFF;
    st.np_shuf_one = on;
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
/* A tap on the play mode's target under the theme's pair: its left half is shuffle's, its right
 * repeat's, instead of stock's step through the one mode. 1 when it took the tap. */
int np_mode_click(void *target, void *event) {
    if (!target || target != (st.np_win ? widget_lookup(st.np_win, "img_playmode", 1) : 0) ||
        !theme()->np_modes) /* the theme last: theme() reads the settings */
        return 0;
    int x = I(event, EVENT_X);
    for (void *p = target; p && p != window_manager(); p = P(p, W_PARENT)) x -= I(p, W_X);
    np_mode_step(x >= I(target, W_W) / 2, 1);
    return 1;
}

/* playing.modes.icons: the pair (docs/oled-design.md#now-playing), NP_MODES_APART apart about the
 * centre of the play mode's target (tools/ipod.py np_grid), which keeps stock's image under
 * opacity 0 and np_mode_click's halves. Each glyph is the accent's while on, else the half's grey
 * (tools/art.py mode_*); Repeat One has its own glyph. The one the Centre cycle's wheel sets
 * (np_stage) stands in a Text Primary ring, rounded as the selection bar. */
#define NP_MODES_APART 44
#define NP_RING_W 38 /* the ring round the glyph the Centre cycle's wheel sets, 2 px wide */
#define NP_RING_H 34
void np_modes(void *win, void *canvas) {
    void *mode = theme()->np_modes && P(canvas, CANVAS_LCD) ? widget_lookup(win, "img_playmode", 0)
                                                            : (void *)0;
    if (!mode) return;
    int m = *(volatile int *)MCL_MODE, cx = I(mode, W_X) + I(mode, W_W) / 2,
        cy = I(mode, W_Y) + I(mode, W_H) / 2, rep = np_repeat_of(m);
    const char *off = ipod_light() ? "_l" : "_d";
    char name[2][24];
    unsigned bitmap[64]; /* bitmap_t */
    tk_snprintf(name[0], sizeof *name, "mode_shuffle%s", np_shuffle_on(m) ? "_on" : off);
    tk_snprintf(name[1], sizeof *name, "mode_repeat%s",
                rep == NP_REPEAT_ONE   ? "1_on"
                : rep == NP_REPEAT_ALL ? "_on"
                                       : off);
    for (int i = 0; i < 2; ++i) {
        int x = cx + (2 * i - 1) * NP_MODES_APART / 2;
        if (st.np_stage == (unsigned)(NP_STAGE_SHUFFLE + i)) { /* the wheel's: a ring round it */
            rect_t ring = { x - NP_RING_W / 2, cy - NP_RING_H / 2, NP_RING_W, NP_RING_H },
                   in = { ring.x + 2, ring.y + 2, NP_RING_W - 4, NP_RING_H - 4 };
            unsigned saved = (unsigned)I(P(canvas, CANVAS_LCD), LCD_FILL_COLOR);
            fill_box(canvas, &ring, ui_ink(RGBA(0xffffff)), 8);
            fill_box(canvas, &in, ui_ink(RGBA(0)), 6);
            stock_canvas_set_fill_color(canvas, saved);
        }
        if (!widget_load_image(win, theme_art(name[i]), bitmap))
            canvas_draw_icon(canvas, bitmap, x, cy);
    }
}

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
