/* scroll_lrc's rows are 20 px, or 25 a line when wrapped (0x53b838), and its 178 px are no whole
 * number of them, so the view's end (content height - view height), where stock's follow and the
 * wheel stop, cut the top row. The content grows to the first row top at or past that end plus the
 * view: under a row of blank space below the last. A relayout (scroll_view's, 0x604638) puts the
 * height back and clamps the offset; the next follow, tick or glide step pads it again. ponytail:
 * the wheel and a drag stop anywhere, so a row can be cut at the top until the page follows. iPod's
 * lyrics page (lyrics.c) ends with its last line in the middle. */
/* The offset that centres row i of lrc in its view. */
static int lyric_centre(void *lrc, int i) {
    void *row = widget_get_child(lrc, i);
    return I(row, W_Y) + (I(row, W_H) - I(lrc, W_H)) / 2;
}
static void lyric_pad(void *lrc) {
    if (lyrics_on(lrc)) {
        I(lrc, VIEW_CONTENT_H) = lyric_centre(lrc, widget_count_children(lrc) - 1) + I(lrc, W_H);
        return;
    }
    int h = I(lrc, W_H), end = I(lrc, VIEW_CONTENT_H) - h, top = 1 << 30;
    for (int i = widget_count_children(lrc); i-- > 0;) {
        int t = I(widget_get_child(lrc, i), W_Y);
        if (t >= end && t < top) top = t;
    }
    if (end > 0 && top < 1 << 30) I(lrc, VIEW_CONTENT_H) = top + h;
}

static unsigned np_track(void);

/* The lyrics page in front: Now Playing on top, lit, not sliding and untouched, on the lyrics. */
static int lyrics_shown(void) {
    void *wm = window_manager();
    return st.np_lrc && st.np_slide && usable() && window_manager_get_top_window(wm) == st.np_win &&
           !window_manager_is_animating(wm) && !pointer_down() &&
           widget_get_prop_int(st.np_slide, "value", 0) == 1;
}

/* Stock's scroll animator on the lyrics stops, a drag's fling among them (stock
 * table_client_stop_animator_scroll's sequence, as stop_scroll). */
static void lyric_still(void *lrc) {
    void *a = P(lrc, VIEW_ANIMATOR);
    if (!a) return;
    widget_animator_pause(a);
    widget_animator_destroy(a);
    P(lrc, VIEW_ANIMATOR) = (void *)0;
}

/* Another song: its lyrics at the top, nothing held, gliding or waiting for a tap's seek, and
 * stock's animator, which would carry them on to where the last song's follow sent them, stopped.
 */
static void lyric_top(void *lrc) {
    st.lyric_held = st.lyric_spun = st.lyric_glide = st.lyric_to = st.lyric_push = 0;
    st.lyric_seek = 0;
    lyric_still(lrc);
    scroll_view_set_offset(lrc, I(lrc, SCROLL_X), 0);
}

/* The lyrics glide to `to` (1/16 px) within their content; lyric_step moves them, every UI pass. */
static void lyric_glide(void *lrc, int to) {
    lyric_still(lrc);
    st.lyric_to = clamp_step(to, (I(lrc, VIEW_CONTENT_H) - I(lrc, W_H)) * 16, 0);
    if (!st.lyric_glide) st.lyric_at = (unsigned)time_now_ms();
    st.lyric_glide = 1;
}

/* The Now Playing timer's follow (LYRIC_FOLLOW in tools/build.py): stock scrolls to the row three
 * lines above the current one, which past the end clamps to it. iPod: lyrics the wheel or a finger
 * moved stay (lyric_held) until Now Playing goes, another song starts or, timed, they rest
 * LYRIC_RESUME_MS (lyric_poll), and a finger on the
 * screen keeps them too; on the page, the follow glides. The lyrics page follows its own playing
 * line (lyric_follow). */
int ringnav_lyric_follow(void *lrc, int x, int y, int ms) {
    lyric_pad(lrc);
    if (lrc == st.np_lrc) {
        if (st.lyric_held || st.lyric_spun == 2 || lyrics_on(lrc) || pointer_down()) return 0;
        if (lyrics_shown()) {
            lyric_glide(lrc, y * 16);
            return 0;
        }
        st.lyric_glide = 0;
    }
    return scroll_view_scroll_to(lrc, x, y, ms);
}

/* The page's refresh rebuilds scroll_lrc's rows (0x53b67c, LYRIC_ROWS in tools/build.py) whenever
 * g_forcerefresh_flag is set, as Play/Pause (playpause_quick_click) and the page's return to the
 * front do, and the rebuild ends at offset 0: the lyrics flicked to the top until the next follow.
 * The offset stays; the follow moves it if the line did. iPod: rows for another song than the last
 * rows' start at the top (lyric_top), whatever moved the old ones since the song changed, and they
 * become the lyrics page's (lyrics_rows). */
int ringnav_lyric_rows(void *win) {
    void *lrc = widget_lookup(win, "scroll_lrc", 1);
    int y = lrc ? I(lrc, SCROLL_Y) : 0, ret = ((int (*)(void *))0x53b67c)(win);
    if (lrc && lrc == st.np_lrc && lyrics_rows(lrc)) lyric_pad(lrc);
    unsigned song = lrc && lrc == st.np_lrc ? np_track() : st.lyric_rows_song;
    if (song != st.lyric_rows_song) {
        st.lyric_rows_song = st.lyric_song = song;
        lyric_top(lrc);
        return ret;
    }
    if (y) scroll_view_set_offset(lrc, I(lrc, SCROLL_X), y);
    return ret;
}

/* The playing track: its queue position and path. */
static unsigned np_track(void) {
    unsigned at, n;
    void *r = queue_now(&at, &n);
    return r ? fnv(hash_bytes(FNV_SEED, (const unsigned char *)&at, sizeof at), P(r, REC_PATH)) : 0;
}

/* Stock's seek resumes playback; a paused seek must wait for Play instead. */
static int seek_position(int to) {
    unsigned at, n;
    void *r = mclGetPlayStatus() == 3 ? queue_now(&at, &n) : 0;
    if (r) {
        int sec = -1, total;
        player_playtime_and_length(&sec, &total);
        mclSetSeek(to + I(r, REC_CUE_START));
        return sec + 1;
    }
    player_seek_time(to);
    return 0;
}

/* The status bar's centred label follows stock's 24-hour setting (18:14 or 6:14 PM), without
 * seconds, or --:-- when the time cannot be read. clock_show reads the time once a minute, from a
 * timer due at the next minute, writes the label when the minute shown changes and applies Auto
 * Light Mode's and Night Shift's hour; nothing reads the time in between. It runs at once for a new
 * zone (ringnav_timezone), a time set by hand (time_click) and a wake (ringnav_sleep), and on the
 * pass after the bar first paints or paints with a new 24-hour setting (ringnav_paint_bg).
 * ponytail: a network time sync while lit shows at the next minute. */
static void clock_show(void);
int clock_due(const void *info) {
    (void)info;
    st.clock_timer = 0;
    clock_show();
    return 0;
}
static void clock_show(void) {
    stop_timer(&st.clock_timer);
    int buf[11];
    const int *tm = clock_now(buf);
    int ok = tm && tm[0] >= 0 && tm[0] < 61 && tm[1] >= 0 && tm[1] < 60 && tm[2] >= 0 && tm[2] < 24;
    int full = st.clock_full = g_time24h_flag != 0;
    accent(); /* reads Light Mode */
    light_apply(ok ? tm : (void *)0);
    night_clock(ok && !light_day(tm));
    st.clock_timer = timer_add(clock_due, (void *)0, ok ? (60u - (unsigned)tm[0]) * 1000u : 60000u);
    unsigned key = ok ? ((unsigned)(tm[2] * 60 + tm[1]) + 1) | (unsigned)full << 11 |
                            (unsigned)!!st.bar_title << 12 /* it changes the 12-hour form below */
                      : ~0u;                               /* 0 before the first */
    void *bar = *(void *const *)system_bar;
    void *label = key == st.clock_key || !bar ? (void *)0 : widget_lookup(bar, "label_clock", 1);
    if (!label) return;
    st.clock_key = key;
    char s[16] = "--:--";
    if (ok && full)
        tk_snprintf(s, sizeof s, "%02d:%02d", tm[2], tm[1]);
    else if (ok && st.bar_title) /* beside the bar's title: AM or PM would reach it */
        tk_snprintf(s, sizeof s, "%d:%02d", (tm[2] + 11) % 12 + 1, tm[1]);
    else if (ok)
        tk_snprintf(s, sizeof s, "%d:%02d %s", (tm[2] + 11) % 12 + 1, tm[1],
                    tm[2] < 12 ? "AM" : "PM");
    widget_set_text_utf8(label, s);
    /* the status row's clock: the title's face, in its place */
    theme_font(label, ROLE_BAR, FONT_ROW);
}
static void clock_zone(void) {
    if (st.clock_timer) clock_show(); /* the bar runs; boot's call comes before it */
}

/* systembar_showface sets img_bt's image each second: a codec badge (BT_CODECS) while stock shows
 * the codec, else bar_bt or BT_GLYPH. A new badge shows CODEC_MS, then codec_fade fades it out and
 * BT_GLYPH in, and bar_sync keeps putting BT_GLYPH back over stock's badge before img_bt paints. */
static const char *const codecs[] = BT_CODECS;

static int codec_fade(const void *info) {
    int n = ++st.codec_step, d = CODEC_STEPS - n;
    if (n == 1)
        st.codec_timer = timer_add(codec_fade, (void *)0, CODEC_STEP_MS); /* this one ends */
    if (n == CODEC_STEPS) image_base_set_image(st.bar_bt, BT_GLYPH);
    widget_set_opacity(st.bar_bt, 255u * (unsigned)(d < 0 ? -d : d) / CODEC_STEPS);
    if (n < 2 * CODEC_STEPS) return n == 1 ? 0 : 8; /* RET_REPEAT */
    st.codec_timer = 0;
    return 0;
}

/* The percentage's width at BATT_PX: digits are tabular, each measured up to a whole pixel, so the
 * slot holds the ink; BATT_PCT_W until the first paint measures. */
static int pct_width(unsigned level) {
    if (!st.pct_digit) return BATT_PCT_W;
    return (level >= 100 ? 3 : level >= 10 ? 2 : 1) * st.pct_digit + st.pct_sign;
}

/* What the playing source reports (now_source.c now_status), as mclGetPlayStatus says it: 2
 * playing, 3 paused, 1 neither. */
int bar_status(void) {
    int s = now_status();
    return s == 1 ? 2 : s == 2 ? 3 : 1;
}
/* img_state follows whichever player plays, stock's or Spotify's, radio's or a video's, and
 * label_idle shows when neither it nor label_eq does. Returns whether a state image changed. */
static int bar_state_sync(void *bar) {
    int changed = 0;
    if (!st.bar_bt && bar) {
        st.bar_bt = widget_lookup(bar, "img_bt", 1);
        st.bar_wifi = widget_lookup(bar, "img_wifi", 1);
        st.bar_pct = widget_lookup(bar, "label_battery", 1);
        st.bar_slot = widget_lookup(bar, "view_battery", 1);
        st.bar_icon = widget_lookup(bar, "img_battery", 1);
        st.bar_idle = widget_lookup(bar, "label_idle", 1);
        st.bar_state = widget_lookup(bar, "img_state", 1);
        st.bar_eq = widget_lookup(bar, "label_eq", 1);
        st.bar_title = widget_lookup(bar, "label_title", 1);
    }
    int on = 0;
    if (st.bar_state) {
        int status = bar_status();
        on = status == 2 || status == 3;
        if (on != !!widget_get_visible(st.bar_state)) {
            widget_set_visible(st.bar_state, on, 0);
            changed = 1;
        }
        if (on) {
            /* the play state is the theme's own art (tools/art.py), by state and half */
            char icon[32];
            tk_snprintf(icon, sizeof icon, "%s%s_%c", theme()->art, status == 2 ? "play" : "pause",
                        ipod_light() ? 'l' : 'd');
            if (tk_strcmp(widget_get_prop_str(st.bar_state, "image", ""), icon)) {
                image_base_set_image(st.bar_state, icon);
                changed = 1;
            }
        }
    }
    changed |= status_row_sync(); /* status_row.c: its slots, the padlock among them */
    if (st.bar_idle && st.bar_state && st.bar_eq)
        widget_set_visible(st.bar_idle,
                           !widget_get_visible(st.bar_state) && !widget_get_visible(st.bar_eq), 0);
    return changed;
}

/* An invalidate made while another window paints is dropped with that frame's dirty rects, and
 * img_state keeps its dirty flag, so stock's own invalidates skip it until something else repaints
 * the bar: Now Playing, painting every 250 ms, sees a Play/Pause first and left a stale icon
 * (#116). Repaint the bar once, after the frame. */
int bar_repaint(const void *info) {
    (void)info;
    st.bar_timer = 0;
    widget_invalidate_force(*(void *const *)system_bar, (void *)0);
    return 0;
}

/* The status bar's codec badge, then the Battery setting (docs/ipod.md#status-bar-and-clock):
 * view_battery paints the charge-level icon, the percentage, both, or the percentage inside, sized
 * to its ink so every gap is STATUS_GAP, and the icon wherever the group's ink would reach more
 * than batt_room allows. widget_set_visible does nothing for an unchanged state and relayouts the
 * view for a new one. */
static int slider_page(void *top);
/* A status row's clock (tools/ipod.py status_row; docs/oled-design.md#status-row): on a full-width
 * row, titled or not, centred on the screen (x 187.5), the title's box ending CLOCK_CLEAR short of
 * the widest clock, "00:00", centred, so its ellipsis does; the glyph slots left of the battery get
 * the room from CLOCK_BESIDE past that widest clock's text (status_row.c status_strip_room), and
 * scroll there when they run past it, so the clock never moves. On Home's Split, with its art
 * beside the list, the clock goes over the list at the row's left, its text starting
 * STATUS_ROW_INSET in, as the battery ends that far from the right; the slots keep the row's right
 * with no room set, and the title's box (empty there) ends CLOCK_CLEAR short of a widest clock
 * beside them. Where the art runs up under the row the clock is in Text Primary, as it was over the
 * art. The box is sized to the text as measured in the bar's face, with
 * hscroll_label's 2 px margins, so the label's own alignment plays no part. Runs in the bar's
 * background paint, before the clock draws; a move repaints the bar after. */
#define CLOCK_BESIDE 7 /* the text's box: 8 px of ground between the digits' ink and the slot */
#define CLOCK_CLEAR 12
static int clock_width(void *canvas, const unsigned *s) {
    int n = (int)wide_len(s), px = theme()->font_px[ROLE_BAR];
    canvas_set_font(canvas, theme_face(s, n, &px), px);
    return (int)(canvas_measure_text(canvas, s, (unsigned)n) + 0.5f);
}
/* The widest clock, "00:00", as measured once in the bar's face (it holds until a restart); 0
 * before the title has a canvas to measure on. */
static int clock_widest(void) {
    static const unsigned zeros[] = { '0', '0', ':', '0', '0', 0 };
    static int widest;
    void *canvas = st.bar_title ? widget_get_canvas(st.bar_title) : (void *)0;
    if (!widest && canvas) widest = clock_width(canvas, zeros);
    return widest;
}
/* The left edge of the strip's room on a full-width row: CLOCK_BESIDE past the widest clock's
 * text, centred; 0 before the widest is measured. */
static int strip_left(int widest) {
    int ww = widest + 4; /* the box: hscroll_label's 2 px margins */
    return widest ? (375 - ww + 1) / 2 + ww - 2 + CLOCK_BESIDE : 0;
}
static void clock_place(void *bar, void *top) {
    static void *clock;
    static int ink, set_color; /* set_color: the colour written last, compared in place of
                                * a read-back of the style's current state */
    void *canvas = st.bar_title ? widget_get_canvas(st.bar_title) : (void *)0;
    if (!st.bar_slot || !canvas) return;
    if (!clock) {
        if (!(clock = widget_lookup(bar, "label_clock", 1))) return;
        ink = style_get_int(P(clock, W_ASTYLE), "text_color", (int)RGBA(RGB_SECONDARY));
    }
    int widest = clock_widest();
    const unsigned *text = widget_get_text(clock);
    int split = coverflow_home_split(top);
    status_strip_room(split ? 0 : strip_left(widest));
    int edge = status_slots_left() - CLOCK_BESIDE,
        w = (text && *text ? clock_width(canvas, text) : widest) + 4,
        x = split ? STATUS_ROW_INSET - 2 : (375 - w + 1) / 2,
        color = split == 2 ? (int)RGBA(0xffffff) : ink,
        title_w = (split ? edge - widest : (375 - widest - 4 + 1) / 2 + 2) - CLOCK_CLEAR + 2 -
                  I(st.bar_title, W_X);
    if (I(st.bar_title, W_W) != title_w)
        widget_resize(st.bar_title, title_w, I(st.bar_title, W_H));
    else if (I(clock, W_X) == x && I(clock, W_W) == w && (set_color ? set_color : ink) == color)
        return;
    widget_move_resize(clock, x, I(clock, W_Y), w, I(clock, W_H));
    /* each state status_row styles: the label paints in whichever it is in */
    widget_set_prop_int(clock, "style:normal:text_color", color);
    widget_set_prop_int(clock, "style:focused:text_color", color);
    widget_set_prop_int(clock, "style:disable:text_color", color);
    set_color = color;
    if (!st.bar_timer) st.bar_timer = timer_add(bar_repaint, (void *)0, 0);
}

/* Charging, as the bar says it: stock's icon is bar_charge whenever a cable is in, the charge
 * enable held off or not (usb_charge.c's guard, Charge Limit), so a held one is not charging. */
static int bar_charging(void) {
    const char *icon = st.bar_icon ? widget_get_prop_str(st.bar_icon, "image", "") : "";
    return icon && !tk_strcmp(icon, "bar_charge") && !usb_charge_held();
}
/* The battery view_battery shows, as a BATT_KEY_* key: the level from label_battery's "88%" (stock
 * zeroes progress_battery while charging), then what the cable does: too weak to charge
 * (charge_weak), full (g_power_chargestate 2) or held at Charge Limit, else charging
 * (bar_charging); and low (bar_lowcharge, or under 15% on a cable that charges nothing). The cable
 * is g_usbdet_value, stock's own read of it: stock's icon cannot say it, as systembar_showface
 * picks bar_charge for a cable only while the charger is not Full (0x541e2c) and the plain icon
 * over a USB DAC link with its charging Off, so at Full the cable read as out and the battery was
 * drawn as on battery. A --dev build's STATUS_FAKE "charge=1" is charging, "charge=2" a cable held
 * full. Reads stock's widgets and bytes, no I/O. */
static unsigned battery_now(void) {
    const char *icon = widget_get_prop_str(st.bar_icon, "image", "");
    const unsigned *t = widget_get_text(st.bar_pct);
    int fake = status_fake('c'), level = 0, charging = bar_charging() || fake == 1,
        cable = g_usbdet_value == 1 || (icon && !tk_strcmp(icon, "bar_charge")) || fake > 0;
    while (t && *t >= '0' && *t <= '9') level = level * 10 + (int)(*t++ - '0');
    int held = cable && !charging, weak = charging && charge_weak();
    int full =
        cable && !weak && (fake == 2 || I(g_power_chargestate, 0) == 2 || (held && st.charge_held));
    unsigned key = (unsigned)(level > 100 ? 100 : level) << 4;
    if (weak)
        key |= BATT_KEY_WEAK;
    else if (full)
        key |= BATT_KEY_PLUGGED;
    else if (charging)
        key |= BATT_KEY_CHARGING;
    return key | (unsigned)((icon && !tk_strcmp(icon, "bar_lowcharge")) || (held && level < 15));
}
/* The room the battery and the slots left of it have on OLED's status row: from the battery's end
 * (STATUS_ROW_INSET in from the right edge) to the strip's room's edge past the centred clock
 * (strip_left), so the percentage gives way to the icon before the glyphs would have to scroll.
 * Before the clock's widest is measured (no paint yet) there is no limit. */
static int batt_room(void) {
    int widest = clock_widest();
    return widest ? 375 - STATUS_ROW_INSET - strip_left(widest) : 375;
}
static void bar_sync(void *bar) {
    if (bar_state_sync(bar) && !st.bar_timer) st.bar_timer = timer_add(bar_repaint, (void *)0, 0);
    if (!st.bar_bt || !st.bar_wifi || !st.bar_pct || !st.bar_slot || !st.bar_icon) return;
    int shown = widget_get_visible(st.bar_bt), c = 0;
    const char *image = shown ? widget_get_prop_str(st.bar_bt, "image", "") : "";
    for (int i = 0; image && i < (int)(sizeof codecs / sizeof *codecs); ++i)
        if (!tk_strcmp(image, codecs[i])) c = i + 1;
    /* A new badge starts over; BT_GLYPH with a badge is the faded badge itself. */
    if (c != st.codec && (c || !image || tk_strcmp(image, BT_GLYPH))) {
        stop_timer(&st.codec_timer);
        st.codec = c;
        st.codec_step = 0;
        widget_set_opacity(st.bar_bt, 255);
        if (c) st.codec_timer = timer_add(codec_fade, (void *)0, CODEC_MS);
    } else if (c && st.codec_step >= CODEC_STEPS)
        image_base_set_image(st.bar_bt, BT_GLYPH);
    accent(); /* reads the settings */
    coverflow_home_np(); /* Now Playing's row, there while something is loaded */
    /* the slim battery (list_marks.c): the set's body */
    int body = geom_val(V_BATT_W), inside = BATT_INSIDE_W;
    int mode = st.battery;
    if (mode) {
        int reach = mode == 1   ? BATT_PCT_W
                    : mode == 3 ? inside + BATT_NUB_W
                                : body + BATT_NUB_W + BATT_GAP + BATT_PCT_W;
        if (reach + status_slots_reach() > batt_room()) mode = 0;
    }
    widget_set_visible(st.bar_icon, 0, 0);
    widget_set_visible(st.bar_pct, 0, 0);
    widget_set_visible(st.bar_slot, 1, 0);

    unsigned key = battery_now();
    int pct = pct_width(BATT_KEY_LEVEL(key));
    int width = mode == 1
                    ? pct
                    : (mode == 3 ? inside : body) + BATT_NUB_W + (mode == 2 ? BATT_GAP + pct : 0);
    int resized = I(st.bar_slot, W_W) != width;
    widget_resize(st.bar_slot, width, geom_val(V_BAR));
    /* Resize only relayouts the slot's own children; the right-aligned row must
     * position it again even when Icon + Percent -> Icon keeps it visible. */
    if (resized) widget_layout_children(P(st.bar_slot, W_PARENT));
    if (key == st.batt_key && mode == st.batt_mode && !resized) return;
    st.batt_mode = mode;
    st.batt_key = key;
    /* bar_sync runs mid-paint: an invalidate now is lost, and this frame painted the slot only
     * where stock dirtied the bar (label_battery's new digit), so charging's colour reached one
     * digit until a wake. The whole bar again after this frame. */
    if (!st.bar_timer) st.bar_timer = timer_add(bar_repaint, (void *)0, 0);
}

/* systembar_showface, each second: stock hides img_state whenever its own player is stopped, so
 * with Spotify, radio or a video playing it went off here and back on at the next paint, two
 * relayouts a second and a frame with EQ drawn in the play icon's place. Correct it before paint,
 * dark or not, so a wake never shows stock's hidden icon; the badge waits for paint. Stock writes
 * the battery into label_battery and img_battery, which bar_sync hides, so their writes repaint
 * nothing and a new level showed only when something else painted the bar: a new level or charge
 * repaints the bar here, outside a paint, while lit (a wake paints it all). */
int ringnav_showface(void *bar) {
    int result = stock_showface_trampoline(bar);
    bar_state_sync(bar); /* no I/O, and a relayout waits for a paint */
    if (st.bar_pct && st.bar_icon && g_backlight_status) {
        unsigned key = battery_now();
        if (key != st.batt_key) {
            st.batt_key = key; /* what this repaint shows: bar_sync has nothing more to repaint */
            widget_invalidate_force(bar, (void *)0);
        }
    }
    return result;
}

/* A battery body (or the percentage alone) bh rows high: its top in the slot w. Centred on the
 * row's height, not on w's, which is the bar's. The slim body rises BATT_BODY_RISE, so its 12 rows
 * (16 to 27) hold the clock's digits' 11 (17 to 27) and stand a row past their top, as iOS's
 * battery does; beside the percentage it rises BATT_PCT_DY, to the digits' ink. */
#define BATT_BODY_RISE 1
static int batt_top(void *w, int bh) {
    int rise = st.batt_mode == 2 ? BATT_PCT_DY : st.batt_mode != 1 ? BATT_BODY_RISE : 0;
    return (I(P(w, W_PARENT), W_H) + 1 - bh) / 2 - rise;
}
/* Mode 1 is the percentage alone; every other the slim battery (list_marks.c), the set's body from
 * column 0. Restore canvas state. */
static void paint_battery(void *w, void *canvas) {
    void *lcd = P(canvas, CANVAS_LCD);
    if (!lcd) return;
    unsigned fill = (unsigned)I(lcd, LCD_FILL_COLOR);
    unsigned key = st.batt_key, level = BATT_KEY_LEVEL(key), s[4], n = put_num(s, level);
    int inside = st.batt_mode == 3;
    if (!inside) s[n++] = '%';
    unsigned color = RGBA(key & BATT_KEY_WEAK                            ? BATT_WEAK_RGB
                          : key & (BATT_KEY_CHARGING | BATT_KEY_PLUGGED) ? BATT_CHARGE_RGB
                          : level < BATT_LOW_PCT                         ? BATT_LOW_RGB
                                                                         : 0xffffff);
    /* whole pixels up, so the slot is never narrower than the ink */
    if (!inside && !st.pct_digit) {
        unsigned c = '0';
        int px = BATT_PX;
        canvas_set_font(canvas, theme_face(&c, 1, &px), (unsigned)px);
        st.pct_digit = (int)canvas_measure_text(canvas, &c, 1) + 1;
        c = '%';
        st.pct_sign = (int)canvas_measure_text(canvas, &c, 1) + 1;
    }
    if (st.batt_mode == 1) { /* one line, never wrapped: centred in the slot, on the row's centre */
        int px = BATT_PX;
        const char *face = theme_face(s, (int)n, &px);
        unsigned text = (unsigned)I(lcd, LCD_TEXT_COLOR);
        canvas_set_font(canvas, face, (unsigned)px);
        canvas_set_text_color(canvas, color);
        int tw = (int)canvas_measure_text(canvas, s, n);
        canvas_draw_text(canvas, s, n, (I(w, W_W) - tw) / 2, batt_top(w, px));
        stock_canvas_set_text_color(canvas, text);
        return;
    }
    int bh = geom_val(V_BATT_H);
    battery_slim(w, canvas, 0, batt_top(w, bh), geom_val(V_BATT_W), bh, key);
    stock_canvas_set_fill_color(canvas, fill);
}

/* Seconds as stock writes label_playtime, after a minus when negative is 1. */
void clock_text(void *label, int negative, int t) {
    char s[16] = "-";
    toolsTimeItoa(s + negative, t);
    widget_set_text_utf8(label, s);
}

/* The faces, by FONT_* (stock.h, from tools/build.py FACES): each one's name, its fallback for a
 * character it lacks and that fallback's px (% of its own), and its cmap's runs, FONT_RANGES from
 * face_at[face] to face_at[face + 1]; stock's default.ttf has none listed, and every character. */
static const char *const font_faces[FONT_N] = FONT_NAMES;
static const unsigned char face_fallback[FONT_N] = FONT_FALLBACK,
                           face_pct[FONT_N] = FONT_FALLBACK_PCT;
static const unsigned short face_ranges[] = FONT_RANGES, face_at[FONT_N + 1] = FONT_RANGE_AT;

/* 1 iff every character of c (n < 0: up to the NUL; NULL has none) is in face's cmap. */
static int face_has(int face, const unsigned *c, int n) {
    const unsigned short *r = face_ranges + face_at[face];
    unsigned k = face_at[face + 1] - face_at[face];
    if (!k) return 1;
    if (!c) return 0;
    for (; n < 0 ? *c : n; ++c, --n) {
        unsigned i = 0;
        while (i < k && (*c < r[i] || *c > r[i + 1])) i += 2;
        if (i >= k) return 0;
    }
    return 1;
}

/* The theme's font for role on w: its face when that face has every character of w's text, else its
 * fallback's (face_fallback: Inter's is stock's) that does, and so on down to stock's, which has
 * CJK; and its px, if the theme sets one, scaled by each fallback taken (face_pct: stock's standing
 * in for Inter at 98%). Written in every state the labels style, only when the
 * answer (face | px << FACE_BITS) differs from was; returns it. A code-built row passes FONT_ROW:
 * the answer lives in its own "_font" prop with the role above it (row_faces reads it), and a role
 * the theme leaves to the style writes nothing. */
#define FACE_BITS 4
_Static_assert(FONT_N <= 1 << FACE_BITS, "theme_font's answer holds the face in FACE_BITS");
static int fallback_px(int px, int face) { return (px * face_pct[face] + 50) / 100; }
static int role_font(void *w, int face, int px, int role, int was) {
    static const char *const names[] = { "style:normal:font_name", "style:focused:font_name",
                                         "style:disable:font_name" },
                             *const sizes[] = { "style:normal:font_size", "style:focused:font_size",
                                                "style:disable:font_size" };
    int row = was == FONT_ROW;
    if (row) {
        if (!face && !px) return 0;
        was = widget_get_prop_int(w, "_font", -1);
    }
    const unsigned *text = face ? widget_get_text(w) : (void *)0, none[1] = { 0 };
    if (face && !text) text = none; /* no text yet: keeps the face, as bold_label did */
    for (; face && !face_has(face, text, -1); face = face_fallback[face])
        px = fallback_px(px, face);
    int answer = face | px << FACE_BITS, kept = row ? answer | role << 16 : answer;
    if (!w || kept == was) return answer;
    for (unsigned i = 0; i < sizeof names / sizeof *names; ++i) {
        widget_set_prop_str(w, names[i], font_faces[face]);
        if (px) widget_set_prop_int(w, sizes[i], px);
    }
    if (row) widget_set_prop_int(w, "_font", kept);
    return answer;
}
int theme_font(void *w, int role, int was) {
    const theme_t *T = theme();
    return role_font(w, w ? T->font[role] : FONT_DEFAULT, T->font_px[role], role, was);
}
/* theme_font for a face named outright, at px (0: the style's size); never a row's FONT_ROW. */
int face_font(void *w, int face, int px, int was) {
    return w ? role_font(w, face, px, 0, was) : was;
}

/* Each label under w that theme_font gave a face finds it again for its text, in its own role. */
static void row_faces(void *w) {
    int kept = widget_get_prop_int(w, "_font", -1);
    if (kept >= 0) theme_font(w, kept >> 16, FONT_ROW);
    for (unsigned i = 0, n = widget_count_children(w); i < n; ++i)
        row_faces(widget_get_child(w, i));
}

/* A virtual table's bind of a pooled row (table_client's load loop, TABLE_LOAD_CALL): stock's
 * on_load_data writes the row's text with no relayout, so the faces its layout picked would stay
 * with the last text: a CJK name in a Latin-only face drew blank (#217). */
int ringnav_table_load(void *ctx, unsigned index, void *row) {
    int ret =
        ((int (*)(void *, unsigned, void *))P(P(row, W_PARENT), TABLE_LOAD_DATA))(ctx, index, row);
    row_faces(row);
    catalogue_bind(row, index); /* greyed when it is a server row that can't play now */
    return ret;
}

/* A drag on a virtual table (TABLE_DRAG_CALL, table_client's pointer move): stock's
 * table_client_set_yoffset with the finger's offset held from the first row's top to the wheel's
 * end (navigation.c scroll_end), the rows less the client's shown height (list_shown_h: its box
 * cut at the set's list end, y 282), so a drag stops with the last row's foot on the list end, not
 * on the box's foot at the screen's, in the glass's corners, and never runs on to spring back
 * (docs/internals.md#list-ends). */
int ringnav_table_drag(void *w, int y) {
    int end = I(w, TABLE_ROWS) * I(w, ROW_HEIGHT) - list_shown_h(w);
    if (y > end) y = end;
    return table_client_set_yoffset(w, y < 0 ? 0 : y);
}

/* A virtual table's release (TABLE_SCROLL_CALLS, table_client's pointer up: the fling's target, or
 * where an over-drag settles): stock's table_client_scroll_to clamps its target to the rows less
 * the client's height, the box's foot at the screen's, which a drag held to the list end
 * (ringnav_table_drag) would be sent back down to. It runs with the client's height cut to the
 * shown one, so the fling ends with the last row's foot on the list end too. */
int ringnav_table_scroll(void *w, int y, int ms) {
    int h = I(w, W_H), shown = list_shown_h(w);
    I(w, W_H) = shown;
    int ret = table_client_scroll_to(w, y, ms);
    I(w, W_H) = h;
    return ret;
}

/* Canvas text in the theme's list title face where it has the characters, else in its fallbacks as
 * theme_font has them, at their px (draw_centred); stock's is the system font, NULL. A theme
 * without a face of its own draws the system font. */
const char *theme_face(const unsigned *s, int n, int *px) {
    int face = theme()->font[ROLE_TITLE];
    for (; face && !face_has(face, s, n); face = face_fallback[face])
        if (px) *px = fallback_px(*px, face);
    return face ? font_faces[face] : (const char *)0;
}
