/* Paused, the seek waits for Play (scrub_end) and the player's second stands still until then,
 * and the page's timer would put the bar back on it. The bar keeps the
 * target until the player reports it, the track changes or stops, or SETTLE_PLAY_MS of playing
 * pass without it; then the page's timer comes back. */
#define SETTLE_MS 250
#define SETTLE_PLAY_MS 2000
static int settle_poll(const void *info) {
    int sec, total, status = now_time(&sec, &total); /* now_source.c */
    if (status == 2) st.settle_played += SETTLE_MS;
    if (st.np_win && np_track() == st.scrub_track &&
        (sec < st.scrub_to - 1 || sec > st.scrub_to + 1) &&
        (status == 3 || (status == 2 && st.settle_played < SETTLE_PLAY_MS)))
        return 8; /* RET_REPEAT: paused, or not there yet */
    st.settle_timer = 0;
    if (st.np_win) playing_timer_start(st.np_win);
    return 0;
}

/* Commits a target the wheel moved, unless the playing track changed since the scrub began: a
 * playing track's last seek if the player isn't there yet (seek_scrub_end), a paused one's only
 * seek. The fill comes back, and the page's timer once the player is there (settle_poll), unless
 * the page is gone. */
static void scrub_end(void) {
    if (!st.scrub) return;
    st.scrub = 0;
    stop_timer(&st.scrub_timer);
    int seek = st.scrub_moved && np_track() == st.scrub_track;
    /* playing, the player followed the target (seek_scrub) and goes to it now if it isn't there */
    if (!seek_scrub_end(st.scrub_to, seek) && seek) st.held_sec = seek_position(st.scrub_to);
    st.scrub_moved = 0;
    if (!st.np_win) return;
    np_fill(0);
    st.settle_played = 0;
    if (seek)
        rearm(&st.settle_timer, settle_poll, SETTLE_MS);
    else
        playing_timer_start(st.np_win);
}

static int scrub_expire(const void *info) {
    st.scrub_timer = 0;
    scrub_end();
    return 0;
}

/* The lyrics: stock's 250 ms timer scrolls scroll_lrc to the current line whenever it is elsewhere.
 * Once the wheel or a finger moves them they stay (ringnav_lyric_follow), while the timer keeps the
 * bar and the highlight going, until Now Playing goes or another song starts, a swipe to another
 * page and back included. A scrub under way ends first. */
static void lyric_hold(void) {
    scrub_end();
    st.lyric_held = 1;
}

/* The glide's step, every UI pass: a dt / (dt + LYRIC_EASE_MS) share of the way left, at least a
 * pixel, so it eases in whatever the loop's pace, and quick wheel travel adds up. */
#define LYRIC_EASE_MS 50
static void lyric_step(void *lrc) {
    unsigned now = (unsigned)time_now_ms();
    int dt = (int)(now - st.lyric_at);
    st.lyric_at = now;
    lyric_pad(lrc);
    int y = I(lrc, SCROLL_Y),
        to = (clamp_step(st.lyric_to, (I(lrc, VIEW_CONTENT_H) - I(lrc, W_H)) * 16, 0) + 8) >> 4;
    if (to == y) {
        st.lyric_glide = 0;
        return;
    }
    if (dt > LYRIC_EASE_MS) dt = LYRIC_EASE_MS; /* a late pass: half the way, no leap */
    int step = (to - y) * dt / (dt + LYRIC_EASE_MS);
    scroll_view_set_offset(lrc, I(lrc, SCROLL_X), y + (step ? step : to > y ? 1 : -1));
}

/* The wheel let go: the glide ends on the row top nearest its target, so no row is cut at the top.
 * The end is one (lyric_pad). */
static void lyric_snap(void *lrc) {
    lyric_pad(lrc);
    int at = st.lyric_glide ? st.lyric_to : I(lrc, SCROLL_Y) * 16, to = 0;
    for (int i = 0, n = widget_count_children(lrc); i < n; ++i) {
        int top = I(widget_get_child(lrc, i), W_Y) * 16;
        if ((top > at ? top - at : at - top) < (to > at ? to - at : at - to)) to = top;
    }
    lyric_glide(lrc, to);
}

/* Every UI loop pass (ringnav_sleep). On the lyrics page in front, while the track has lyrics, the
 * wheel posts no ticks (wheel_raw) and its raw travel, 200 units a turn, glides them a pixel at a
 * time: a tick's worth of travel at the Wheel Sensitivity moves them LYRIC_ROW_PX, stock's row, as
 * far as a tick did. No key event comes, so the travel times the loop's idle and the screen's
 * timeout itself. While a finger stays on the wheel they stay where it put them; let go, they glide
 * to the nearest row top (lyric_snap). A finger that came down on the lyrics and dragged them holds
 * them as the wheel does; stock's scroll animation stops as it lands, so only the finger moves
 * them, and a swipe that brings the page in holds nothing. A swipe to another page and back keeps
 * the hold; leaving Now Playing drops it (np_gone). Another song starts at the top, its hold
 * gone. The lyrics page's rows are its lines, a tick's worth a one-row line's height
 * (lyrics_row_h), so a tick lands on a row top. */
#define LYRIC_ROW_PX 20

/* Now Playing's lyrics are the lyrics page's (lyrics.c): the rows full width after a blank
 * LYRIC_SLOT. Stock's init built the rows before ringnav_playing knew the page; they become the
 * page's, or, with none or no memory for their table, stay stock's. */
static void lyric_init(void) {
    void *lrc = st.np_lrc;
    if (lrc && lyrics_rows(lrc)) lyric_pad(lrc);
}

/* The lyrics page, every pass in front, else every LYRIC_FOLLOW_MS: its playing line centred,
 * gliding there while the page shows, else jumping, unless the wheel or a finger holds them or a
 * finger is on the screen. Its rows repaint while it is in front (front) and lit. */
#define LYRIC_FOLLOW_MS 250
static void lyric_follow(void *lrc, int ms, int front, int shown) {
    int line = lyrics_line(lrc, ms, front), to = lyric_centre(lrc, line > 0 ? line : 0);
    if (to < 0) to = 0;
    if (st.lyric_held || st.lyric_spun == 2 || pointer_down())
        return; /* lyric_spun 2: a finger on the wheel holds the nudge */
    lyric_pad(lrc);
    if (shown) {
        if (to * 16 != st.lyric_to || (!st.lyric_glide && I(lrc, SCROLL_Y) != to))
            lyric_glide(lrc, to * 16);
    } else if (I(lrc, SCROLL_Y) != to) {
        lyric_still(lrc);
        scroll_view_set_offset(lrc, I(lrc, SCROLL_X), to);
    }
}

/* Smart Awake (#57): Now Playing lit in front, a song playing, on its lyrics page (not the rating
 * page), or its full-screen art or ⋯'s About (np_about.c) in front, holds the screen on as the
 * lyrics wheel does; paused, it times out as usual. A guest's Now Playing in front with the guest's
 * song playing does too (guest_ui.c guest_ui_keeps_lit). */
static void keep_lit_poll(void) {
    unsigned now = (unsigned)time_now_ms();
    if (now - st.keep_lit_at < 1000) return;
    void *top = window_manager_get_top_window(window_manager());
    int guest = guest_ui_keeps_lit(top);
    if (!st.np_slide && !guest) return;
    st.keep_lit_at = now;
    accent(); /* reads the settings */
    if (st.keep_lit && g_backlight_status &&
        (guest || (mclGetPlayStatus() == 2 &&
                   ((top == st.np_win && widget_get_prop_int(st.np_slide, "value", 0) == 1) ||
                    (top && (top == st.bc[2] || top == np_about_page()))))))
        reset_poweroptions_timer(1, 1, 1);
}

/* Smart Awake's setting (accent's, kept current by its switch): addon.c holds a card game open
 * and lit under it */
int ringnav_smart_awake(void) {
    accent();
    return st.keep_lit;
}

/* Full-screen lyrics (np_lyric_touch): the column (slide_view_view) goes over the page's other
 * widgets, black and the window's width, its slide_view down to the page dots, and the lines wrap
 * at the new width (lyrics_fit), back on the playing line; off, its place, index and slide_view's
 * height before (np_was) again. The column, between the top row and the bar, takes the whole
 * window: its dots NP_FULL_FOOT above the foot and its view_lrc, whose self_layout fixes its
 * place, from the top down to NP_FULL_FOOT above them; off, both self_layouts are their old place
 * again. A column without its dots or view_lrc keeps its y and height. A page without a
 * slide_view has none of this. */
#define NP_FULL_FOOT 20
static void np_full(int on) {
    if (!st.np_slide || !on == !st.np_full) return; /* without stock's slide_view: no column */
    void *col = P(st.np_slide, W_PARENT), *dots = widget_lookup(col, "slide_indicator1", 1),
         *view = P(st.np_lrc, W_PARENT);
    /* off, the layout it started in */
    int *was = st.np_was, ipod = on ? dots && view : st.np_full == 2;
    if (on) {
        int now[] = { I(col, W_X), I(col, W_Y),          I(col, W_W),
                      I(col, W_H), widget_index_of(col), I(st.np_slide, W_H) };
        memcpy(was, now, sizeof now);
        if (ipod) was[6] = I(view, W_Y), was[7] = I(view, W_H), was[8] = I(dots, W_Y);
    }
    st.np_full = on ? 1 + ipod : 0;
    widget_restack(col, on ? widget_count_children(st.np_win) : was[4]);
    widget_set_prop_str(col, "style:normal:bg_color", on ? "#000000" : "#00000000");
    int h = I(st.np_win, W_H), foot = on ? h - NP_FULL_FOOT : was[8];
    if (!on)
        widget_move_resize(col, was[0], was[1], was[2], was[3]);
    else
        widget_move_resize(col, 0, ipod ? 0 : was[1], I(st.np_win, W_W), ipod ? h : was[3]);
    if (ipod) {
        char y[12], vy[12], vh[12];
        tk_snprintf(y, sizeof y, "%d", foot);
        tk_snprintf(vy, sizeof vy, "%d", on ? 0 : was[6]);
        tk_snprintf(vh, sizeof vh, "%d", on ? -NP_FULL_FOOT : was[7]);
        widget_set_self_layout_params(dots, "0", y, "100%", "10");
        widget_set_self_layout_params(view, "0", vy, "100%", vh);
    }
    widget_resize(st.np_slide, I(col, W_W), on && dots ? (ipod ? foot : I(dots, W_Y)) : was[5]);
    widget_layout(col);
    if (lyrics_fit(st.np_lrc)) lyric_pad(st.np_lrc);
    /* the follow takes them; the offset the layout left is no drag of the touch (lyric_poll) */
    st.lyric_held = 0, st.lyric_drag_y = I(st.np_lrc, SCROLL_Y);
}

/* np_rate.c: the column full screen for the rating page, or back */
void np_full_set(int on) { np_full(on); }

static void lyric_poll(void) {
    void *lrc = st.np_lrc, *wm = window_manager();
    unsigned song = lrc ? np_track() : st.lyric_song;
    if (song != st.lyric_song) {
        st.lyric_song = song;
        lyric_top(lrc);
    }
    int page = lrc && st.np_slide && widget_get_prop_int(st.np_slide, "value", 0) == 1;
    np_rate_poll(); /* the rating page's full screen, on it and off it (np_rate.c) */
    if (st.np_full && !page && !np_rate_on())
        np_full(0); /* full-screen lyrics end off their page */
    int touch = lrc && window_manager_get_top_window(wm) == st.np_win && pointer_down();
    int shown = page && lyrics_shown(),
        on = shown && widget_count_children(lrc) && mclGetLyricSize() > 0;
    if (touch && !st.lyric_touch) {
        st.lyric_touch = page ? 1 : 2;
        st.lyric_drag_y = page ? I(lrc, SCROLL_Y) : 0;
        if (page) lyric_still(lrc);
    } else if (!touch && st.lyric_touch) {
        if (st.lyric_touch == 1 && page && I(lrc, SCROLL_Y) != st.lyric_drag_y) lyric_hold();
        st.lyric_touch = 0;
    }
    if (on != st.lyric_raw) {
        __sync_lock_test_and_set(&wheel_travel, 0);
        st.lyric_raw = on;
        if (on || wheel_raw == WHEEL_RAW) wheel_raw = on; /* never the thumb's, handed over */
    }
    int travel = on ? __sync_lock_test_and_set(&wheel_travel, 0) : 0;
    /* Following, the wheel meets the resistance of a list's end: travel short of
     * LYRIC_PUSH_DETENTS within LYRIC_PUSH_MS only nudges the lyrics BUMP_PX from the follow's
     * place, and they spring back when it is let go, still following; a stray touch keeps the
     * follow. More takes over. */
    if (travel && !st.lyric_held) {
        unsigned at = (unsigned)time_now_ms();
        if (!st.lyric_push || at - st.lyric_push_at > LYRIC_PUSH_MS) {
            lyric_pad(lrc);
            st.lyric_push = 0; /* a nudge still held keeps its home: no walking off tick by tick */
            if (st.lyric_spun != 2)
                st.lyric_home = st.lyric_glide ? st.lyric_to : I(lrc, SCROLL_Y) * 16;
        }
        st.lyric_push_at = at;
        st.lyric_push += travel;
        /* the clicks Wheel Sensitivity makes: fewer raw units each as it rises */
        int push = LYRIC_PUSH_DETENTS * WHEEL_TICK_UNITS * 50 / wheel_pct();
        if (st.lyric_push > -push && st.lyric_push < push) {
            st.last_input = at;
            reset_poweroptions_timer(1, 1, 1);
            /* x rises anticlockwise: back up the lyrics */
            lyric_glide(lrc,
                        st.lyric_home - ((st.lyric_push > 0) - (st.lyric_push < 0)) * BUMP_PX * 16);
            st.lyric_spun = 2;
            travel = 0;
        }
    }
    if (travel) {
        st.last_input = time_now_ms();
        reset_poweroptions_timer(1, 1, 1);
        lyric_hold();
        st.lyric_push = 0;
        lyric_pad(lrc);
        int y = I(lrc, SCROLL_Y),
            from = st.lyric_glide || (st.lyric_to + 8) >> 4 == y ? st.lyric_to : y * 16;
        /* x rises anticlockwise: back up the lyrics */
        int row = lyrics_on(lrc) ? lyrics_row_h() : LYRIC_ROW_PX;
        lyric_glide(lrc, from - travel * row * 16 * wheel_pct() / (WHEEL_TICK_UNITS * 50));
        st.lyric_spun = 1;
    }
    if (!page)
        st.lyric_spun = st.lyric_push = 0;
    else if (st.lyric_spun == 2 && !wheel_held) {
        /* a nudge springs back, shown or not, unless a tick took them meanwhile; the follow moves
         * them on if the line changed. The next touch starts its push afresh. */
        if (shown && !st.lyric_held) lyric_glide(lrc, st.lyric_home);
        st.lyric_spun = st.lyric_push = 0;
    } else if (shown && st.lyric_spun && !wheel_held) {
        st.lyric_spun = 0;
        lyric_snap(lrc);
    }
    /* The page in front and usable (lit): the clock and the follow every pass (it narrows on each
     * second's change, lyrics_now). Else the follow, which only jumps, at stock's timer's pace on
     * whole seconds (lyrics_sec), so a dark or covered page reads the clock only that often. */
    int front = page && usable() && window_manager_get_top_window(wm) == st.np_win;
    lyrics_ask_poll(front); /* none of the song's own: its app's server's, when it has some */
    unsigned now = (unsigned)time_now_ms();
    if (lyrics_on(lrc) && (front || now - st.lyric_follow_at >= LYRIC_FOLLOW_MS)) {
        st.lyric_follow_at = now;
        lyric_follow(lrc, front ? lyrics_now() : lyrics_sec(), front, shown);
    }
    if (!shown)
        st.lyric_glide = 0;
    else if (st.lyric_glide)
        lyric_step(lrc);
}

/* A tick on the lyrics page while the track has lyrics, which comes only before lyric_poll takes
 * the wheel raw, glides them to the next or previous row's top, ahead of scrub and the volume. */
static int np_lyrics(unsigned key) {
    void *lrc = st.np_lrc;
    if (!st.np_slide || !lrc || widget_get_prop_int(st.np_slide, "value", 0) != 1 ||
        !widget_count_children(lrc) || mclGetLyricSize() <= 0)
        return 0;
    lyric_hold();
    lyric_pad(lrc);
    int y = st.lyric_glide ? (st.lyric_to + 8) >> 4 : I(lrc, SCROLL_Y),
        max = I(lrc, VIEW_CONTENT_H) - I(lrc, W_H), to = key == KEY_NEXT ? max : 0;
    for (int i = 0, n = widget_count_children(lrc); i < n; ++i) {
        int top = I(widget_get_child(lrc, i), W_Y);
        if (key == KEY_NEXT ? top > y && top < to : top < y && top > to) to = top;
    }
    lyric_glide(lrc, clamp_step(to, max, 0) * 16);
    return 1;
}

static void np_stage(unsigned stage);
static void np_cancel(void) {
    unsigned settle = st.settle_timer; /* an earlier scrub's wait; the one scrub_end arms stays */
    np_stage(NP_STAGE_REST);
    scrub_end();
    if (!settle) return;
    stop_timer(&st.settle_timer);
    if (st.np_win) playing_timer_start(st.np_win);
}

/* A hold of Previous or Next scans the track (seek.c): phase 0 as it starts, which ends a scrub
 * under way and stops the bar's own timer as a scrub does; 1 at each jump, the bar and times on the
 * target; 2 at the release or an end, where a paused track gets its one seek and stays paused
 * (seek_position) and the bar holds the target until the player has it, as after a scrub
 * (settle_poll). */
void np_seek_bar(int phase, int to, int paused) {
    if (phase == 0) {
        scrub_end();
        stop_timer(&st.settle_timer);
    }
    if (phase == 2) {
        st.scrub_to = to;
        st.scrub_track = np_track();
        if (paused) st.held_sec = seek_position(to);
        st.settle_played = 0;
        if (st.np_win) rearm(&st.settle_timer, settle_poll, SETTLE_MS);
        return;
    }
    if (!st.np_win || !st.np_slider) return;
    playing_timer_clear(st.np_win);
    if (phase != 1) return;
    widget_set_prop_int(st.np_slider, "value", to);
    clock_text(st.np_elapsed, 0, to);
    np_sync(st.np_win);
}

/* A short hold of Centre, never while scrubbing (np_centre): it replays the release to stock
 * on_wm_keyup_fun, which turns the screen off (it reads only the event's key). */
static void np_single(void) {
    static const unsigned release[EVENT_KEY / 4 + 1] = { [EVENT_KEY / 4] = KEY_CENTER };
    if (g_backlight_status) on_wm_keyup_fun((void *)0, (void *)release);
}

/* The top window if it is stock's volume dialog (vol_paint), else 0. */
static void *vol_top(void) {
    void *top = window_manager_get_top_window(window_manager());
    if (!top || tk_strcmp(widget_get_prop_str(top, "name", ""), "volume_dialog")) return (void *)0;
    return top;
}

/* The volume dialog on top with Now Playing directly under it (where vol_paint draws its band,
 * given the slider and times), else 0. */
static void *vol_band(void *wm) {
    void *vol = st.np_win ? vol_top() : (void *)0;
    unsigned n = vol ? widget_count_children(wm) : 0;
    return n > 1 && widget_get_child(wm, n - 2) == st.np_win ? vol : (void *)0;
}

/* Scrub starts from the bar's second; the wheel moves the target until scrub_end. A volume
 * band over the page closes first, as a tap beside its bar does, so the wheel reaches the scrub
 * and the bar shows. The close runs the page's foreground handler, which sets the bar to the
 * player's second and restarts stock's timer: the target is read, and the timer cleared, after
 * it. */
static void scrub_start(void) {
    if (st.scrub || !st.np_slider) return;
    void *vol = vol_band(window_manager());
    if (vol) window_close(vol);
    st.scrub = 1;
    st.scrub_moved = 0;
    st.scrub_to = widget_get_prop_int(st.np_slider, "value", 0);
    st.scrub_track = np_track();
    stop_timer(&st.settle_timer);
    playing_timer_clear(st.np_win);
    np_fill(1);
    rearm(&st.scrub_timer, scrub_expire, SCRUB_MS);
}

/* The Centre cycle's ring on shuffle, repeat or the heart (NP_STAGE_*), or rest: np_modes draws
 * it, and NP_STAGE_MS without a key ends it. */
#define NP_STAGE_MS SCRUB_MS
static int np_stage_end(const void *info) {
    st.np_stage_timer = 0;
    np_stage(NP_STAGE_REST);
    return 0;
}
static void np_stage(unsigned stage) {
    if (stage != NP_STAGE_REST)
        rearm(&st.np_stage_timer, np_stage_end, NP_STAGE_MS);
    else
        stop_timer(&st.np_stage_timer);
    if (st.np_stage == stage) return;
    st.np_stage = stage;
    void *mode = st.np_win ? widget_lookup(st.np_win, "img_playmode", 1) : (void *)0,
         *heart = np_heart();
    if (mode) widget_invalidate_force(mode, (void *)0);
    if (heart) widget_invalidate_force(heart, (void *)0);
}
/* 1 when the ring can stand on stage: shuffle and repeat under a theme with the pair and no radio
 * (whose order is the app's), the heart under such a theme while it shows (np_bar.c np_heart). */
static int np_stop_ok(unsigned stage) {
    if (!theme()->np_modes) return 0;
    if (stage == NP_STAGE_HEART) return np_heart() != 0;
    return (stage == NP_STAGE_SHUFFLE || stage == NP_STAGE_REPEAT) && !np_radio_on();
}
/* The stop dir (1 or -1) from stage that the ring can stand on, stage itself at an end; from
 * NP_STAGE_REST the first, or NP_STAGE_REST when there is none. */
static unsigned np_stop_next(unsigned stage, int dir) {
    for (int s = (int)stage + dir; s >= NP_STAGE_SHUFFLE && s <= NP_STAGE_HEART; s += dir)
        if (np_stop_ok((unsigned)s)) return (unsigned)s;
    return stage;
}

/* Now Playing's Centre, after an iPod classic's: at rest a press starts scrubbing; the scrub's
 * own press ends it (scrub_end seeks) and puts a focus ring on the first of shuffle, repeat and
 * the heart. The wheel moves the ring, never changing a control; Centre works the ringed one as a
 * tap does, the ring staying: shuffle on or off, repeat a step Off, All, One, or the heart's like
 * on or off (its click, ratings.c rate_fav_click). The song's stars are the rating page's alone.
 * Under a theme without the pair there is no ring and the scrub's press ends the cycle. A press
 * held NP_HOLD_MS or longer, short of stock's long press (Shut Down), turns the screen off
 * instead, as stock's release does. So does a double press, as on every other page: the first
 * press scrubs at once, and a second within DOUBLE_CLICK_MS of the release that started that
 * scrub, the wheel not turned between (np_key), undoes it unseeked (np_cancel: the target has not
 * moved, so scrub_end seeks nothing) and turns the screen off, where it would end the scrub and
 * ring shuffle. */
#define NP_HOLD_MS 400
static struct {
    unsigned at; /* that release's time */
    int armed;   /* ... and the next press may be its double */
} np_dc __attribute__((section(".scratch")));
static int np_centre(unsigned now) {
    int held = st.center_down_at && now - st.center_down_at >= NP_HOLD_MS;
    int dbl = np_dc.armed && now - np_dc.at < DOUBLE_CLICK_MS;
    st.center_down_at = 0; /* this press's: a release with no press stamped is a press */
    np_dc.armed = 0;
    if ((held && !st.scrub) || (dbl && st.scrub)) {
        np_cancel();
        np_single();
        return STOP;
    }
    if (np_rate_centre()) return STOP; /* the rating page's target (np_rate.c) */
    if (!st.scrub && st.np_stage == NP_STAGE_REST && np_stream_error()) {
        np_stream_retry(); /* Retry, the selected one (np_stream.c) */
        return STOP;
    }
    unsigned stage = st.np_stage;
    if (st.scrub) {
        scrub_end();
        np_stage(np_stop_next(NP_STAGE_REST, 1));
    } else if (stage == NP_STAGE_REST) {
        scrub_start();
        if (st.scrub) np_dc.at = now, np_dc.armed = 1; /* a second press soon is a double */
    }
    else if (!np_stop_ok(stage)) {
        np_stage(NP_STAGE_REST);
    } else if (stage == NP_STAGE_HEART) {
        void *heart = np_heart();
        char click[0x30];
        stock_dispatch_trampoline(heart, pointer_event_init(click, EVT_CLICK, heart, 0, 0));
        np_stage(stage); /* its timeout from this press */
    } else {
        np_mode_step(stage == NP_STAGE_REPEAT, 1);
        np_stage(stage); /* its timeout from this press */
    }
    return STOP;
}

/* Centre (np_centre) and, while scrubbing or in the Centre cycle, the wheel on the top Now Playing
 * page. While scrubbing the wheel moves the target SCRUB_STEP seconds times the
 * WHEEL_RAMP_MS ramp, within the track; a playing track follows it, at most a seek every
 * SCRUB_SEEK_MS (seek.c seek_scrub), a paused one only at the end. In the cycle a tick moves the
 * ring to the next or previous stop, staying at the ends. Neither the volume nor its dialog sees
 * the wheel. */
static int np_key(void *top, unsigned key) {
    unsigned now = (unsigned)time_now_ms();
    if (key == KEY_CENTER) { return np_centre(now); }
    np_dc.armed = 0; /* a turn between two presses: the second is the scrub's own, not a double */
    if (st.np_stage != NP_STAGE_REST && !st.scrub) {
        np_stage(np_stop_next(st.np_stage, key == KEY_NEXT ? 1 : -1)); /* its timeout from here */
        return STOP;
    }
    if (np_track() != st.scrub_track) { /* a new track: scrub_end drops the last one's target */
        scrub_end();
        return STOP;
    }
    int dir = key == KEY_NEXT ? 1 : -1;
    /* the step grows while the wheel spins: every tick counts, on the ramp lists had before */
    int step = SCRUB_STEP * ramp(top, st.np_slider, 0, -1, dir, now, WHEEL_RAMP_MS, WHEEL_RAMP_MS);
    int to = clamp_step(st.scrub_to, widget_get_prop_int(st.np_slider, "max", 0), dir * step);
    if (to != st.scrub_to) st.scrub_moved = 1;
    st.scrub_to = to;
    if (st.scrub_moved) seek_scrub(to); /* heard as it moves while the track plays */
    widget_set_prop_int(st.np_slider, "value", to);
    clock_text(st.np_elapsed, 0, to);
    np_sync(top);
    rearm(&st.scrub_timer, scrub_expire, SCRUB_MS);
    return STOP;
}

static int np_gone(void *win, void *event) {
    if (win == st.np_win) {
        np_about_np_gone(win);
        st.np_win = (void *)0;
        st.np_hash = 0;
        st.np_album = st.np_remain = st.np_slider = st.np_elapsed = st.np_cover = st.np_slide =
            st.np_lrc = st.np_title = st.np_artist = (void *)0;
        st.scrub_moved = 0; /* the page is going: no seek */
        st.np_full = st.lyric_held = 0;
        np_tap_reset();
        stop_timer(&st.genre_timer);
        st.genre_label = (void *)0;
        np_cancel();
        lyrics_gone(); /* the next page's rows may take its rows' place */
        np_rate_gone();
    }
    return 0;
}

/* Now Playing's art (either page) follows the player's cover. Stock reloads img_cover only on
 * one-shot flags its 250 ms timer finds set: a song change, a forced refresh, the cover thread's
 * finish. Enlarged Art's timer clears all three while it is open (0x52dac0), so a change it took
 * left the page on the last track's art. Each lit pass, the playing track and the player's file it
 * can use (once g_lastcover_url is this track's: 1 embedded, 2 folder image, 4 downloaded, 3 none:
 * stock's placeholder) are compared with the last, with that file's size and time
 * (now_cover_written: a card app's URL queue's songs share both); a change reloads the art as stock
 * does, the cached bitmap dropped first (the player writes every track's cover to the same file).
 * Anything else (parsing, Tidal) keeps stock's image.
 * ponytail: one more decode per track change while the page is up, beside stock's own reload. */
static unsigned np_art(const char **file) {
    unsigned at, n;
    if ((*file = catalogue_np_cover())) /* a pressed server song's, while it loads: whenever */
        return tk_strcmp(widget_get_prop_str(st.np_cover, "image", ""), *file) /* not shown */
                   ? ~st.np_art
                   : fnv(FNV_SEED, (const unsigned char *)*file);
    void *r = queue_now(&at, &n);
    unsigned char type = now_cover(r, file);
    unsigned written = now_cover_written(*file);
    if (type == 3) *file = "play_defaultcover"; /* none: stock's placeholder */
    return *file ? fnv(hash_bytes(FNV_SEED, &type, 1), (const unsigned char *)P(r, REC_PATH)) ^
                       written
                 : 0;
}
static void np_art_poll(void) {
    const char *file;
    if (!st.np_cover || !g_backlight_status) return;
    unsigned key = np_art(&file);
    if (key == st.np_art) return;
    st.np_art = key;
    if (!file) return;
    image_manager_unload_bitmap_by_name(image_manager(), file);
    image_base_set_image(st.np_cover, file);
    widget_invalidate_force(st.np_cover, (void *)0);
}

/* Now Playing's lyrics page (ringnav_playing): a touch that lifts within TAP_PX of where it came
 * down, so neither a swipe nor a drag of the lines, switches them to full screen and back
 * (np_full), once there are lyrics to show. The slide_view or scroll_lrc taking the touch (its
 * abort) ends full screen at once, so the pages slide in their column. Returns 0: a STOP would
 * keep the slide_view's grab (rate_fav_click). */
static int np_lyric_touch(void *ctx, void *event) {
    int type = I(event, EVENT_TYPE), x = I(event, EVENT_X), y = I(event, EVENT_Y);
    int tap = (unsigned)(x - st.np_tap_x + TAP_PX) <= 2 * TAP_PX &&
              (unsigned)(y - st.np_tap_y + TAP_PX) <= 2 * TAP_PX;
    if (type == EVT_POINTER_DOWN) {
        st.np_tap_x = x, st.np_tap_y = y;
        return 0;
    }
    st.np_tap_x = 1 << 20; /* used or taken for a swipe: a later lift is no tap */
    if (type == EVT_POINTER_ABORT) {
        if (st.np_full) np_full(0);
    } else if (tap && (st.np_full || lyrics_on(st.np_lrc)))
        np_full(!st.np_full);
    return 0;
}

/* Now Playing is one page. Stock's navigator opens a playing_page over whatever is in front, so a
 * song played from a page Now Playing opened (its artist or album tap, the ⋯ page's Look up, then
 * Shuffle or a song there) left the first one under it: covered, its 250 ms timer stopped, it kept
 * the song it last showed, and Return came back to it before Home (the emulator, 2026-11-17: Song
 * 1 at 1:01 while Song 2 played). Every older one closes as win opens; the pages between stay.
 * coverflow.c play_folder also closes one in front before it opens, so that open slides from the
 * page under it. */
static void np_only(void *win) {
    void *wm = window_manager();
    for (int i = (int)widget_count_children(wm) - 1; i >= 0; --i) {
        void *w = widget_get_child(wm, i);
        if (w && w != win && !tk_strcmp(widget_get_prop_str(w, "name", ""), "playing_page"))
            window_manager_close_window_force(wm, w);
    }
}

/* playing_page_init: stock builds the page and starts its 250 ms timer, then the iPod labels bind.
 * Headphones required with none to play to: output_rule.c ringnav_nav_to refuses the start before
 * the navigator makes the page. Should one get here all the same, stock toasts, closes the window
 * it was given and returns STOP before it builds anything (0x53e4b4), so nothing binds. */
int ringnav_playing(void *win, void *ctx) {
    if (st.np_full)
        np_full(0); /* another Now Playing: the last one's lyrics back in their column */
    /* and their place: np_gone leaves a page this one replaces alone */
    st.lyric_held = 0;
    int result = stock_playing_trampoline(win, ctx);
    if (!win || result == STOP) return result;
    np_only(win); /* before st.np_win is win: an older page's np_gone still clears its own */
    rate_bind(win);
    np_glyphs_bind(win);
    /* np_gone leaves a replaced page's state alone: its Genre line is that page's. */
    stop_timer(&st.genre_timer);
    st.genre_label = (void *)0;
    st.np_win = win;
    st.np_album = widget_lookup(win, "label_ipod_album", 1);
    st.np_slider = widget_lookup(win, "slider_play", 1);
    st.np_remain = widget_lookup(win, "label_ipod_remain", 1);
    st.np_elapsed = widget_lookup(win, "label_playtime", 1);
    st.np_cover = widget_lookup(win, "img_cover", 1);
    st.np_title = widget_lookup(win, "scrlabel_title", 1);
    st.np_artist = widget_lookup(win, "scrlabel_artist", 1);
    st.np_bold = st.np_meta[0] = st.np_meta[1] = -1;
    st.np_slide = widget_lookup(win, "slide_view", 1);
    st.np_lrc = widget_lookup(win, "scroll_lrc", 1);
    st.lyric_song = st.lyric_rows_song = np_track(); /* no song change at the start */
    lyric_init();                                    /* the rows stock's init built */
    np_rate_bind(win);                               /* after np_slide and np_lrc */
    np_marq_reset();
    void *lyric = widget_lookup(win, "view_lyric", 1);
    static const unsigned touch[] = { EVT_POINTER_DOWN, EVT_POINTER_UP_BEFORE, EVT_POINTER_ABORT };
    for (unsigned i = 0; lyric && i < sizeof touch / sizeof *touch; ++i)
        widget_on(lyric, touch[i], np_lyric_touch, (void *)0);
    for (unsigned i = 0; i < sizeof touch / sizeof *touch; ++i)
        widget_on(win, touch[i], np_time_tap, (void *)0); /* Track Time */
    static const unsigned taps[] = { EVT_POINTER_DOWN, EVT_POINTER_MOVE_BEFORE,
                                     EVT_POINTER_UP_BEFORE, EVT_POINTER_ABORT };
    for (unsigned i = 0; i < sizeof taps / sizeof *taps; ++i)
        widget_on(win, taps[i], np_tap, (void *)0); /* the art, artist and album (np_taps.c) */
    np_tap_reset();
    st.np_time_down = 0;
    st.np_hash = 0;
    const char *file;
    st.np_art = np_art(&file); /* stock's init loaded it */
    st.np_left = -1;
    np_fill(0);
    np_theme();
    widget_on(win, EVT_DESTROY, np_gone, win);
    int sec, total;
    now_time(&sec, &total); /* now_source.c */
    /* Stock's first update sets the value before the max, so the asset's max clamps it and the
     * bar sits short until the next tick: set them again, max first. */
    if (st.np_slider && total > 0) {
        widget_set_prop_int(st.np_slider, "max", total);
        widget_set_prop_int(st.np_slider, "value", sec);
    }
    /* A paused seek still waiting for Play, the player's second unmoved: the bar shows its target
     * again, held as scrub_end holds it (settle_poll). Resumed or another track drops it. */
    if (st.held_sec && st.held_sec == sec + 1 && np_track() == st.scrub_track &&
        mclGetPlayStatus() == 3 && st.np_slider) {
        playing_timer_clear(win);
        widget_set_prop_int(st.np_slider, "value", st.scrub_to);
        clock_text(st.np_elapsed, 0, st.scrub_to);
        st.settle_played = 0;
        rearm(&st.settle_timer, settle_poll, SETTLE_MS);
    } else
        st.held_sec = 0;
    np_sync(win);
    return result;
}

/* The song name and its shade fade out together, BC_STEPS steps BC_STEP_MS apart. */
static int bc_fade(const void *info) {
    int n = ++st.bc_step;
    if (n == 1) st.bc_timer = timer_add(bc_fade, (void *)0, BC_STEP_MS); /* this one ends */
    for (int i = 0; i < 2; ++i)
        if (st.bc[i]) widget_set_opacity(st.bc[i], 255u * (unsigned)(BC_STEPS - n) / BC_STEPS);
    if (n < BC_STEPS) return n == 1 ? 0 : 8; /* RET_REPEAT */
    st.bc_timer = 0;
    return 0;
}

static int bc_gone(void *win, void *event) {
    stop_timer(&st.bc_timer);
    stop_timer(&st.bc_repaint);
    st.bc[0] = st.bc[1] = st.bc[2] = (void *)0;
    return 0;
}

/* Shows the song name and its shade again for BC_MS, then fades them out. */
static void bc_show(void) {
    stop_timer(&st.bc_timer);
    for (int i = 0; i < 2; ++i)
        if (st.bc[i]) widget_set_opacity(st.bc[i], 255);
    st.bc_step = 0;
    st.bc_track = np_track();
    st.bc_timer = timer_add(bc_fade, (void *)0, BC_MS);
}

static int bc_repaint(const void *info) {
    st.bc_repaint = 0;
    if (st.bc[2]) widget_invalidate_force(st.bc[2], (void *)0);
    return 0; /* RET_REMOVE */
}

/* The full-screen art takes its cover's shape: the largest w:h box centred on the window whose
 * corners clear the glass by GLASS_SLACK, as tools/ipod.py sizes the square BC_ART, with the shade
 * and song name on its foot. Checked as the window paints; the image is decoded for its shape only
 * when the track, stock's cover of it or the image changes, and the box is sized again when that
 * shape or the box (a layout back to the square) changes. Stock names the player's one file per
 * kind by g_playcover_type (0x52d828), so one counts only once it is the type's and g_lastcover_url
 * is this track's, as Home's art; until then the art keeps the shape of g_lastcover_url's cover, or
 * the square once that changes. The file is decoded past the image manager's cached copy, which may
 * be the last track's; the file's size and time count too (np_art). Stock's
 * default_bigcover placeholder is the square. */
static void bc_fit(void *win) {
    void *img = widget_lookup(win, "img_bigcover", 1), *shade = st.bc[1], *title = st.bc[0];
    const char *name = img ? widget_get_prop_str(img, "image", "") : "";
    unsigned at, n;
    const char *cover;
    unsigned type = now_cover(queue_now(&at, &n), &cover);
    unsigned box = img ? (unsigned)(I(img, W_W) << 16 | I(img, W_H)) : 0;
    unsigned key = fnv(np_track() ^ type, (const unsigned char *)name) ^ now_cover_written(cover);
    if (!img || !shade || !title || (key == st.bc_key && box == st.bc_box)) return;
    int player = tk_str_end_with(name, "/tmp/coverpic.jpg") ||
                 tk_str_end_with(name, "/tmp/externpic.jpg") ||
                 tk_str_end_with(name, "/tmp/album_tidal.jpg");
    const char *own = type == 1                ? "/tmp/coverpic.jpg"
                      : type == 2 || type == 4 ? "/tmp/externpic.jpg"
                                               : "";
    unsigned bitmap[64], url = fnv(0, (const unsigned char *)g_lastcover_url);
    if (key != st.bc_key && player && !(*own && tk_str_end_with(name, own))) {
        if (url != st.bc_url) st.bc_shape = 1 << 16 | 1;
    } else if (key != st.bc_key) {
        st.bc_shape = 1 << 16 | 1,
        st.bc_url = url; /* the square when it won't load or is past 4:1 */
        if (tk_strcmp(name, "default_bigcover")) {
            image_manager_unload_bitmap_by_name(image_manager(), name);
            if (!widget_load_image(img, name, bitmap)) {
                if (bitmap[0] && bitmap[1] && bitmap[0] < 1 << 16 && bitmap[1] < 1 << 16 &&
                    bitmap[0] <= 4 * bitmap[1] && bitmap[1] <= 4 * bitmap[0])
                    st.bc_shape = bitmap[0] << 16 | bitmap[1];
                widget_unload_image(img, bitmap);
            }
        }
    }
    st.bc_key = key, st.bc_box = box;
    unsigned w = st.bc_shape >> 16, h = st.bc_shape & 0xffff;
    for (int s = 374; s > 2; s -= 2) {
        int bw = w >= h ? s : (int)(s * w / h), bh = w >= h ? (int)(s * h / w) : s;
        int x = (375 - bw) / 2, y = (320 - bh) / 2, dx = GLASS_R + GLASS_SLACK - x,
            dy = GLASS_R - y;
        if (bh > 320 || (dx > 0 && dy > 0 && dx * dx + dy * dy > GLASS_R * GLASS_R)) continue;
        if (I(img, W_X) == x && I(img, W_Y) == y && I(img, W_W) == bw && I(img, W_H) == bh) return;
        int sh = I(shade, W_H), foot = y + bh - sh;
        widget_move_resize(title, x + BC_TITLE_PAD, foot + I(title, W_Y) - I(shade, W_Y),
                           bw - 2 * BC_TITLE_PAD, I(title, W_H));
        widget_move_resize(shade, x, foot, bw, sh);
        widget_move_resize(img, x, y, bw, bh);
        st.bc_box = (unsigned)(bw << 16 | bh);
        rearm(&st.bc_repaint, bc_repaint, 0); /* AWTK drops what a paint invalidates */
        return;
    }
}
