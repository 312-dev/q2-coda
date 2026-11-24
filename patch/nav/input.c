/* Wake: Any Button (wake_key, below): with the screen off, each of the Q2's own keys lights it.
 * Wake: Double Press, the pocket guard: Return never reaches the UI dark, so a bump of Back leaves
 * the page alone; a lone centre press (the power key, which wakes it)
 * never reaches the UI, so a pocket bump stays dark; one within WAKE_MS after it passes, press and
 * release, so holding that second press still powers off; under Any Button a centre press passes at
 * once. Either way a passing press clears Standby's resume guard, so its release is not dropped,
 * and another centre press within WAKE_MS of it, or of the first UI loop pass with the screen lit,
 * is dropped with its release (wake_eat): turning the screen on (on_wm_keyup_fun) and that pass's
 * paint stall the loop about that long, so a blind second press is dispatched only after. A blind
 * double press only wakes, without clicking the selected row or turning the screen off again.
 * Repeated downs share their press's fate, including a dropped press's release. A passing hold
 * keeps stock's downstream long-press power-off. Under Double Press other keys pass: the media
 * keys work dark, and the wheel's ticks set nothing there (volume.c vol_wm_keyup). Returns 1 to
 * drop the event. */
static void woke(unsigned now) { /* a passing dark press: not Standby's resume key */
    *(volatile unsigned char *)BOOT_KEY_GUARD = 0;
    st.woke_at = now;
    st.dark |= 2; /* ringnav_sleep restarts woke_at once the screen is lit */
}
static int remote_match(void *e, int spend);
/* Wake: Any Button (WAKEDOUBLE 0, the default) lights a dark screen at the press of any of the Q2's
 * own keys (not a SyncLink or Car Remote key, posted by the loop, nor a headset's, which headset.c
 * takes past the window manager), as stock's Center release lights it (awake_screen:
 * g_backlight_status, enable_fb, its 50 ms settle, screen_action(1)), its screen-off and Standby
 * counts started over as a press's (reset_poweroptions_timer) and its wake window and lit pacing as
 * a Center wake's (woke). Play/Pause, Previous and Next go on and act, a hold of the skips seeking;
 * Return's press only wakes, its long press and release dropped with it (wake_return), so a hold
 * from dark does not open Home; a wheel turn's first tick only wakes, and the turn's ticks after it
 * are dropped while they come within WAKE_TURN_MS of each other (wake_turn_at), so neither the
 * volume nor a list moves on the turn that woke it. Center is left to stock's release, which lights
 * it as it always has. A key the Buttons Lock holds is left to keylock.c, whose card lights the
 * screen. */
#define WAKE_TURN_MS 300
static int wake_key(void *e, unsigned type, unsigned key, unsigned now) {
    if (key == KEY_RETURN && st.wake_return) { /* the waking press's long press and release */
        if (type == EVT_KEY_UP) st.wake_return = 0;
        return 1;
    }
    int wheel = key == KEY_PREV || key == KEY_NEXT;
    if (wheel && st.wake_turn_at && now - st.wake_turn_at < WAKE_TURN_MS) {
        st.wake_turn_at = now | 1; /* the turn goes on */
        return 1;
    }
    st.wake_turn_at = 0;
    if (g_backlight_status || type != EVT_KEY_DOWN) return 0;
    int media = key == KEY_PLAY || key == KEY_BACK_BTN || key == KEY_FWD_BTN;
    if ((!media && !wheel && key != KEY_RETURN) || remote_match(e, 0) || keylock_holds(key))
        return 0;
    awake_screen();
    reset_poweroptions_timer(1, 1, 1);
    woke(now);
    if (key == KEY_RETURN) st.wake_return = 1;
    if (wheel) st.wake_turn_at = now | 1;
    return !media;
}
static int wake_gate(void *e) {
    unsigned type = (unsigned)I(e, EVENT_TYPE), now = (unsigned)time_now_ms();
    if (type != EVT_KEY_DOWN && type != EVT_KEY_LONG && type != EVT_KEY_UP) return 0;
    unsigned key = (unsigned)I(e, EVENT_KEY);
    if (!st.wake_double && wake_key(e, type, key, now)) return 1;
    if (!g_backlight_status && key == KEY_RETURN) return 1;
    if (type == EVT_KEY_LONG) return 0; /* stock generates this downstream of the input hook */
    if (key != KEY_CENTER) return 0;
    /* Consume Standby's release guard once; our dropped press already drops its release. */
    if (type == EVT_KEY_DOWN && !g_backlight_status && *(volatile unsigned char *)BOOT_KEY_GUARD) {
        st.wake_down = 0;
        *(volatile unsigned char *)BOOT_KEY_GUARD = 0;
    }
    if (type == EVT_KEY_UP)
        st.wake_down = 0;
    else {
        if (st.wake_down) return st.wake_down == 2;
        st.wake_down = 1;
    }
    if (type == EVT_KEY_UP && st.wake_eat) {
        st.wake_eat = 0;
        return 1;
    }
    /* every press sets it, so one whose release never came (Standby while held) leaves none */
    if (type == EVT_KEY_DOWN && (st.wake_eat = now - st.woke_at < WAKE_MS)) {
        st.wake_down = 2;
        return 1;
    }
    if (!st.wake_double || g_backlight_status) { /* wake_pass is read by power_poll */
        if (!g_backlight_status && type == EVT_KEY_DOWN) woke(now);
        return st.wake_pass = 0;
    }
    if (type == EVT_KEY_UP) {
        int pass = st.wake_pass;
        st.wake_pass = 0;
        return !pass;
    }
    st.wake_pass = now - st.wake_at < WAKE_MS;
    st.wake_at = st.wake_pass ? 0 : now;
    if (st.wake_pass)
        woke(now);
    else
        st.wake_down = 2;
    return !st.wake_pass;
}

/* A loose wheel, or a thumb on its rim between two buttons, can close Centre with an edge button
 * (Return, Play/Pause, Previous, Next). A Centre press that starts while one is held, within
 * CHORD_MS of its press, is dropped whole (its repeats and release too), before the wake gate sees
 * it; the edge button keeps its own. Centre pressed first passes, but an edge press within
 * CHORD_MS of it ends Centre's hold timer (ringnav_hold_timer), so a rim press held near a corner
 * never reaches stock's Centre hold, the Shut Down dialog. Return's hold (Now Playing, Home) can
 * ghost a second press as it lets go: a Return press starting within RETURN_GHOST_MS of a hold's
 * release is dropped whole too. ponytail: CHORD_MS bounds a Return release that never came (Standby
 * while held); RETURN_GHOST_MS is the tuning knob, and only a ghost after the release is caught.
 * Returns 1 to drop the event. */
#define CHORD_MS 500
#define RETURN_GHOST_MS 135
static int chord_gate(void *e) {
    unsigned type = (unsigned)I(e, EVENT_TYPE), key = (unsigned)I(e, EVENT_KEY);
    unsigned now = (unsigned)time_now_ms();
    if (type != EVT_KEY_DOWN && type != EVT_KEY_LONG && type != EVT_KEY_UP) return 0;
    unsigned edge = key == KEY_RETURN     ? 1
                    : key == KEY_PLAY     ? 2
                    : key == KEY_BACK_BTN ? 4
                    : key == KEY_FWD_BTN  ? 8
                                          : 0;
    /* a repeat follows its press within 258 ms; a later down is a new press (a lost release) */
    if (edge && type == EVT_KEY_UP)
        st.edge_held &= ~edge;
    else if (edge &&
             (!(st.edge_held & edge) || (type == EVT_KEY_DOWN && now - st.edge_seen > 300))) {
        st.edge_held |= edge;
        st.edge_at = now;
        if (st.centre_at && (int)(now - st.centre_at) < CHORD_MS && st.centre_hold) {
            timer_remove(st.centre_hold); /* the rim's Centre: no hold */
            st.centre_hold = 0;
        }
    }
    if (edge && type != EVT_KEY_UP) st.edge_seen = now;
    if (key == KEY_RETURN) {
        /* a repeat follows its press within 258 ms; a later down is a new press */
        if (type == EVT_KEY_DOWN && !st.return_at &&
            (!st.return_eat || now - st.return_eat_at > 300))
            st.return_eat = st.return_up && (int)(now - st.return_up) < RETURN_GHOST_MS;
        if (st.return_eat) {
            st.return_eat_at = now;
            if (type == EVT_KEY_UP) st.return_eat = st.return_up = 0;
            return 1;
        }
        if (type == EVT_KEY_UP) {
            st.return_up = /* return_at may run a ms late, its | 1 */
                st.return_at && now - st.return_at + 1 >= KEY_HOLD_MS ? now | 1 : 0;
            st.return_at = 0;
        } else if (!st.return_at) {
            st.return_at = now | 1; /* never 0 while held */
            /* a hold's release can miss stock's filter as the windows close: its latch is stale */
            if (type == EVT_KEY_DOWN) *(volatile unsigned char *)RETURN_RELEASE_LATCH = 0;
        }
        return 0;
    }
    if (key != KEY_CENTER) return 0;
    /* a repeat follows its press within 258 ms; a later down is a new press (a lost release) */
    int fresh = type == EVT_KEY_DOWN && (!st.chord_eat || now - st.chord_at > 300);
    if (fresh) st.chord_eat = st.edge_held && now - st.edge_at < CHORD_MS ? 1 : -1;
    st.chord_at = now;
    int eat = st.chord_eat > 0;
    if (type == EVT_KEY_UP) st.chord_eat = st.centre_hold = 0;
    /* a passing press: its time, for an edge press after it and np_centre's hold (ringnav_keydown);
     * its release ends that */
    if (!eat)
        st.centre_at = type == EVT_KEY_UP ? 0 : st.centre_at && !fresh ? st.centre_at : now | 1;
    return eat;
}

/* main_loop_post_key_event: its one caller, on_wm_idle_status, posts SyncLink's (Eddict Player's)
 * play/pause, previous and next as a press and a release for the loop to dispatch later, stamped
 * with time_now_ms. Each post keeps its key and the time around it, so ringnav_input knows it. */
static struct {
    unsigned key, from, to;
} posted[4] __attribute__((section(".scratch")));
extern int stock_post_key_trampoline(void *, int, int);
int ringnav_post_key(void *loop, int pressed, int key) {
    static unsigned next __attribute__((section(".scratch")));
    unsigned from = time_now_ms();
    int ret = stock_post_key_trampoline(loop, pressed, key);
    next = (next + 1) % 4;
    posted[next].key = key & 255;
    posted[next].from = from;
    posted[next].to = time_now_ms();
    return ret;
}
static int remote_match(void *e, int spend) {
    unsigned key = (unsigned)I(e, EVENT_KEY), t = (unsigned)I(e, EVENT_TIME);
    for (int i = 0; i < 4; i++)
        if (posted[i].key == key && t - posted[i].from <= posted[i].to - posted[i].from) {
            if (spend) posted[i].key = 0;
            return 1;
        }
    return 0;
}
static int remote_key(void *e) { return remote_match(e, 1); }

/* window_manager_dispatch_input_event: while q2video plays (videos.c) no key or touch reaches the
 * UI; a key's release goes to the player instead. A card game (addon.c) takes every event but the
 * music's keys. Every event times ringnav_sleep's idle. */
int ringnav(void *ctx, void *event);
/* A wheel tick needs a ring frame with pressure within WHEEL_FRESH_MS (wheel_sample stamps it
 * before it posts), and no stop from Return, so a tick with no finger behind it goes no further,
 * whatever posted it. Its release follows its press, so AWTK never keeps a press with no release.
 * replay_wheel calls ringnav itself and never passes here. Signed: a frame can stamp after the
 * clock is read. ponytail: ticks queued behind a UI stalled past WHEEL_FRESH_MS after the last
 * frame go too; a flick's tail, raise the knob if it is ever missed. */
static int tick_gate(void *e) {
    unsigned type = (unsigned)I(e, EVENT_TYPE), key = (unsigned)I(e, EVENT_KEY);
    if ((type != EVT_KEY_DOWN && type != EVT_KEY_UP) || (key != KEY_PREV && key != KEY_NEXT))
        return 0;
    unsigned bit = key == KEY_NEXT ? 2 : 1;
    if (type == EVT_KEY_UP) {
        int drop = (st.tick_drop & bit) != 0;
        st.tick_drop &= ~bit;
        return drop;
    }
    int muted = wheel_mute;
    unsigned now = (unsigned)time_now_ms();
    if (!muted && (int)(now - wheel_frame_at) < WHEEL_FRESH_MS) {
        st.tick_drop &= ~bit;
        st.tick_at = now;
        return 0;
    }
    st.tick_drop |= bit;
    if (Q2POD_DEV && !muted) wheel_gate_note();
    return 1;
}
/* A wheel tick's step of the volume, as stock's dialog makes it (volume_dialog_on_keyup, and
 * vol_next's steps at the wheel's speed): within 0 and the volume limit, set and saved. */
static void vol_step(unsigned key) {
    int value = vol_next(key);
    if (value == g_volume) return;
    g_volume = value;
    device_set_volume(value, 1);
    write_int_config(value, "PLAYSET", "VOLUME");
}
int ringnav_input(void *wm, void *e) {
    if (e && tick_gate(e)) return 0;
    if (e && (chord_gate(e) || wake_gate(e))) return 0;
    if (e && keylock_gate(e, remote_match(e, 0))) return 0; /* keylock.c */
    if (e && charge_gate(e)) return 0;                      /* the plug-in screen (charging.c) */
    if (e && coverflow_wait_input(e)) return 0; /* Cover Flow opening: Return gives it up */
    if (load_over_input(e)) return 0; /* a page loading over this one (list_marks.c load_over) */
    st.last_input = time_now_ms();
    unsigned type = e ? (unsigned)I(e, EVENT_TYPE) : 0;
    unsigned key = e ? (unsigned)I(e, EVENT_KEY) : 0;
    if (video_on()) {
        if (e && I(e, EVENT_TYPE) == EVT_KEY_UP) video_key((unsigned)I(e, EVENT_KEY));
        return 0;
    }
    if (addon_game_input(e)) return 0;
    /* the selection bar's press (tilt.c): past the gates that drop a press with its release, and
     * the video and the card game that own every key */
    if (key == KEY_CENTER && (type == EVT_KEY_DOWN || type == EVT_KEY_UP))
        tilt_press(type == EVT_KEY_DOWN);
    if (e && seek_input(e)) return 0; /* a held Previous or Next's repeats: seek.c */
    if (type >= EVT_POINTER_FIRST && type <= EVT_POINTER_ABORT) { /* a finger (stale_heal) */
        st.touch_at = st.last_input;
        st.touch_type = (int)type;
        st.touch_x = I(e, EVENT_X);
        st.touch_y = I(e, EVENT_Y);
        /* a tap on a status row slot (status_row.c), upright: the panel's raw axes are portrait */
        status_touch(type, st.touch_y, 319 - st.touch_x);
    }
    if ((type == EVT_KEY_DOWN || type == EVT_KEY_UP || type == EVT_KEY_LONG) &&
        (key == KEY_PREV || key == KEY_NEXT || key == KEY_CENTER) && usb_prompt()) {
        reset_poweroptions_timer(1, 1, 1);
        if (type == EVT_KEY_UP) ringnav(0, e);
        return 0;
    }
    void *top = window_manager_get_top_window(wm);
    if (type == EVT_KEY_UP && (key == KEY_PREV || key == KEY_NEXT) && usable() &&
        window_manager_is_animating(wm) && top &&
        !tk_strcmp(widget_get_prop_str(top, "name", ""), "playing_page")) {
        drop_input();
        st.held = 0; /* Queued list ticks never become volume ticks. */
        reset_poweroptions_timer(1, 1, 1);
        vol_step(key);
        return 0;
    }
    /* No Quick Settings over a prompt. Stock's pull-down (on_wm_tsmove_fun) stays out of its own
     * Add and Delete prompts by their flags, but not out of Shut Down? (g_poweroff_state 1, it
     * tests for 2) or a prompt of ours. Return there was stock's, and took the windows away under
     * the prompt's modal loop: Shut Down? came back with dead buttons, g_poweroff_state still 1,
     * until a restart (#303). A touch that lands with a prompt in front starts below the pull's
     * band, whatever stock recorded for it. */
    if (type == EVT_POINTER_FIRST) {
        int ret = stock_input_trampoline(wm, e);
        void *front = window_manager_get_top_window(wm);
        if (front && !tk_strcmp(widget_get_prop_str(front, "name", ""), "confirminfo_dialog") &&
            I(g_pressdown_y, 0) < QS_PULL_Y)
            I(g_pressdown_y, 0) = QS_PULL_Y;
        return ret;
    }
    if ((type != EVT_KEY_DOWN && type != EVT_KEY_UP) || !remote_key(e))
        return stock_input_trampoline(wm, e);
    /* Buttons lock is the buttons': stock's dark lock filters would drop SyncLink's keys (#113) */
    unsigned char lock = g_keylock_flag;
    g_keylock_flag = 0;
    int ret = stock_input_trampoline(wm, e);
    g_keylock_flag = lock;
    return ret;
}

static unsigned long long glyph_ms __attribute__((section(".scratch"))); /* 0: no frame paints */
/* Stock's time_now_ms whole: 64 bits ($v0, $v1), where FUNCTIONS declares its low 32 */
static unsigned long long now_ms64(void) {
    return ((unsigned long long (*)(void))(void *)time_now_ms)();
}
/* window_manager_paint, a leaf (tools/build.py WM_PAINT_LEAF): the window manager's own paint
 * (vtable +0xc), except while q2video or a card game has the framebuffer (each poll repaints after
 * it) and while the screen is off, unless a window animates (AWTK steps it from the paint); the
 * first lit pass repaints the whole screen (ringnav_sleep; docs/internals.md Battery). While it
 * paints, glyphs are stamped with the frame's time (ringnav_glyph_stamp). */
int ringnav_wm_paint(void *wm) {
    if (wm && !g_backlight_status && !window_manager_is_animating(wm)) return 0; /* RET_OK */
    int away = video_on() || addon_game_on();
    void *vt = wm && !away ? P(wm, 0x94) : (void *)0;
    int (*paint)(void *) = vt ? (int (*)(void *))P(vt, 0xc) : (int (*)(void *))0;
    if (!paint) return away ? 0 : 0x10; /* RET_BAD_PARAMS, as stock */
    list_reach_due(); /* the status row's band, when the list under it moved (row_paint.c) */
    unsigned long long outer = glyph_ms; /* a paint inside a paint (keylock.c, charging.c) */
    glyph_ms = now_ms64();
    int ret = paint(wm);
    glyph_ms = outer;
    return ret;
}

/* glyph_cache_lookup's stamp on a hit (tools/build.py GLYPH_STAMP, in place of its time_now_ms):
 * the frame's time while a frame paints, else the time now. Stock stamps every glyph it finds,
 * each a clock_gettime system call on the Q2, and every row's text is laid out again whatever the
 * frame's dirty area: about 950 a Songs frame, 12% of demo's running time as the wheel scrolled.
 * The cache only orders its glyphs by the stamp, dropping the least recent when it is full, which
 * a frame's few milliseconds don't change (docs/internals.md#song-rows-cost). Here, beside the
 * paint that sets the frame's time. */
unsigned long long ringnav_glyph_stamp(void) { return glyph_ms ? glyph_ms : now_ms64(); }

/* buzzeer_switch (0x4fe3f8), every Key Tone click, stock's and ringnav()'s: the Clicker's route
 * (clicker.c, docs/internals.md#key-tone). The buzzer is the MCU's (system("cmd_mcu write_str
 * buzzer") when g_keytone_flag is set), the device's own speaker, so Speaker keeps it silent while
 * headphones (either jack) or a Bluetooth or USB output listen; Headphones sends the click to
 * q2player to mix into what plays, and has the buzzer only while nothing is mixed; Both has both,
 * the buzzer whatever listens. Off (Key Tone off) is silent. clicker_kind names the sound: a
 * wheel's, unless ringnav_keydown marked a button's.
 * The command waits out the buzzer and the MCU's park (tools/mcu_pwm_clock.c), so it runs on a
 * worker, not the UI thread; a buzzer click while one sounds is dropped, as the buzzer is busy
 * anyway. With the screen off a pass clicks once (tone_dark; ringnav_sleep).
 */
static volatile int tone_busy;
static void *tone_worker(void *on) {
    stock_buzzer_trampoline((int)(long)on);
    tone_busy = 0;
    return 0;
}
/* A buzzer click alone (clicker.c clicker_buzzes, nothing mixed), on the same worker and gates as
 * ringnav_buzzer: Radio's dial, whose wheel is read raw (radio_tuner.c fine_turn). */
int ringnav_piezo(void) {
    int listening = g_po_status || g_bal_status || mclGetOutputWay();
    if (!g_keytone_flag || tone_dark || !clicker_buzzes(listening) || tone_busy) return 0;
    unsigned long tid;
    tone_busy = 1;
    if (pthread_create(&tid, 0, tone_worker, (void *)1L)) {
        tone_busy = 0;
        return 0;
    }
    pthread_detach(tid);
    tone_dark = !g_backlight_status;
    return 1;
}
int ringnav_buzzer(int on) {
    int listening = g_po_status || g_bal_status || mclGetOutputWay();
    if (!g_keytone_flag || tone_dark) return 0;
    int route = clicker_route(listening);
    if (route & ROUTE_MIXED) tone_dark = !g_backlight_status;
    if (!(route & ROUTE_BUZZER) || tone_busy) return 0;
    unsigned long tid;
    tone_busy = 1;
    if (pthread_create(&tid, 0, tone_worker, (void *)(long)on)) {
        tone_busy = 0;
    } else {
        pthread_detach(tid);
        tone_dark = !g_backlight_status;
    }
    return 0;
}

/* on_wm_keydown_before_fun. Stock clicks (buzzeer_switch, which reads g_keytone_flag) on the press,
 * before the release moves anything. A wheel press on a page whose release ringnav() takes as row
 * navigation runs the whole stock body with the flag off for that one synchronous call, so its
 * gates and latches still run, and the release clicks when the selection changes. The test reads
 * the tree only: nothing is loaded, selected or restored. Cover Flow's slide menu, the pixel-scroll fallback,
 * scrub, the volume and every other key keep the stock click. */
int ringnav_keydown(void *ctx, void *event) {
    unsigned key = event ? (unsigned)I(event, EVENT_KEY) : 0;
    if (ple_key(key, 1)) return STOP;
    void *top = window_manager_get_top_window(window_manager());
    if (usable() && games_keydown(top, key)) return STOP; /* an engine game's press (games.c) */
    /* np_centre's hold, timed from the press: its repeats (every 50 ms while held) keep
     * chord_gate's centre_at, which ringnav_input set before this */
    if (key == KEY_CENTER)
        st.center_down_at = st.centre_at ? st.centre_at : (unsigned)time_now_ms() | 1;
    lr_edit_keydown(key); /* Center held on the Library or Home: its editor (library_edit.c) */
    tuner_keydown(top, key); /* ... on Radio's tuner: its stations list (radio_tuner.c) */
    search_strip_down(top, key); /* Return held on a letter strip: its card (search_strip.c) */
    cc_keydown(key);             /* ... on a Control Center stop: its page (control_center.c) */
    un_keydown(key);             /* ... on a queued song in Up Next: it lifts (upnext.c) */
    unsigned char tone = g_keytone_flag;
    void *row = (void *)0;
    entries_t s = { &row, 0, 1, 4096 };
    st.tone_key = 0;
    if ((key == KEY_PREV || key == KEY_NEXT) && usable() && allowed_top(top)) {
        void *w = surface_under(top, (void *)0, (void *)0, 1);
        unsigned n = w && kind(w) != 3 ? widget_count_children(w) : 0;
        for (unsigned i = 0; i < n && !s.n; ++i) collect(widget_get_child(w, i), &s, 1);
        if (s.n) st.tone_key = key;
    }
    if (st.tone_key) g_keytone_flag = 0;
    if (key != KEY_PREV && key != KEY_NEXT)
        clicker_kind = CLICK_KIND_BUTTON; /* stock's click is a button's */
    int result = stock_keydown_trampoline(ctx, event);
    clicker_kind = 0; /* a wheel's again */
    if (st.tone_key) g_keytone_flag = tone;
    /* Coverflow moves on release; stop the press before it navigates the focused list below. */
    if (!result && (key == KEY_PREV || key == KEY_NEXT) && top &&
        !tk_strcmp(widget_get_prop_str(top, "name", ""), "coverflow_page"))
        return STOP;
    return result;
}

/* Stock's setting editors, which have no list. Brightness, maximum and startup volume and balance
 * step with the wheel through their own +/- (limits and saving stay stock's), and Centre goes back;
 * on a page with a switch (Default Volume's img_bootswitch, its row selected) Centre turns it on or
 * off instead, as a tap on the row does, and Return goes back.
 * Quick Settings' brightness slider has no +/-: its value is set, firing stock's change callback.
 * Set date and time and the sleep timer edit one text_selector field at a time, outlined by
 * edit_paint: the wheel turns it, Centre moves to the next field and, after the last, to OK, which
 * Centre presses; a turn on OK goes back to the fields. A double Centre turns the screen off. */
static int slider_page(void *top) {
    const char *name = top ? widget_get_prop_str(top, "name", "") : "";
    return !tk_strcmp(name, "backlight_page") || !tk_strcmp(name, "maxvol_page") ||
           !tk_strcmp(name, "bootvol_page") || !tk_strcmp(name, "balance_page");
}

static int fields(void *top) {
    const char *name = top ? widget_get_prop_str(top, "name", "") : "";
    return !tk_strcmp(name, "manualtime_page") ? 5 : !tk_strcmp(name, "sleepshutdown_page") ? 2 : 0;
}

static void *field_widget(void *top, int f) {
    static const char *const names[] = { "tselector_year", "tselector_month", "tselector_day",
                                         "tselector_hour", "tselector_min",   "btn_enter" };
    int n = fields(top);
    return n ? widget_lookup(top, names[f == n ? 5 : n == 2 ? f + 3 : f], 1) : (void *)0;
}

/* A swipe between the date and time pages moves the field to that page's first. */
static int field_on_page(int f, int time) { return time != (f >= 3) ? (time ? 3 : 0) : f; }
/* The field the wheel edits. */
static int field_at(void *top) {
    int n = fields(top), f = clamp_step(widget_get_prop_int(top, "_edit_field", 0), n, 0);
    void *slide = n >= 5 && f < n ? widget_lookup(top, "slide_view", 1) : (void *)0;
    return slide ? field_on_page(f, widget_get_prop_int(slide, "value", 0) == 1) : f;
}

static void field_set(void *top, int f) {
    prop(top, "_edit_field", f);
    int n = fields(top);
    void *slide = n >= 5 && f < n ? widget_lookup(top, "slide_view", 1) : (void *)0;
    if (slide) widget_set_prop_int(slide, "value", f >= 3);
    widget_invalidate_force(top, (void *)0);
}

/* The side keys move between the fields and OK, where there's one: Next on, Previous back, stopping
 * at the ends. */
static int field_key(void *top, unsigned key) {
    int n = fields(top);
    if (!n || (key != KEY_FWD_BTN && key != KEY_BACK_BTN)) return 0;
    /* a pending Centre would step (or press OK) again: it's dropped, not run */
    stop_timer(&st.edit_press);
    field_set(top, clamp_step(field_at(top), n, key == KEY_FWD_BTN ? 1 : -1));
    return 1;
}

static int edit_single(const void *info) {
    st.edit_press = 0;
    void *wm = window_manager(), *top = window_manager_get_top_window(wm);
    if (!usable() || window_manager_is_animating(wm) || pointer_down()) return 0;
    *(volatile unsigned char *)KEY_LOCKOUT = 0; /* stock's wheel lockout after the centre key */
    int n = fields(top), f = field_at(top);
    void *hidden = n ? widget_lookup(top, "view_sleep", 1) : (void *)0;
    if (hidden && !widget_get_visible(hidden)) /* the switch's off: only its row is showing */
        switch_click(0, 0);
    else if (!n) {
        void *sw = slider_page(top) ? widget_lookup(top, "img_bootswitch", 1) : (void *)0;
        char click[0x30];
        if (sw && widget_get_visible(sw)) {
            stock_dispatch_trampoline(sw, pointer_event_init(click, EVT_CLICK, sw, 0, 0));
            widget_invalidate_force(top,
                                    (void *)0); /* the hidden switch's change repaints no row */
        } else if (slider_page(top))
            navigator_back();
    } else if (f < n)
        field_set(top, f + 1);
    else {
        void *ok = field_widget(top, n);
        char click[0x30];
        if (ok) time_click(ok, pointer_event_init(click, EVT_CLICK, ok, 0, 0));
    }
    return 0;
}

static int edit_key(void *top, unsigned key) {
    static const unsigned release[EVENT_KEY / 4 + 1] = { [EVENT_KEY / 4] = KEY_CENTER };
    int quick = top && !tk_strcmp(widget_get_prop_str(top, "name", ""), "statusbar_dialog");
    int n = fields(top);
    if (!quick && !n && !slider_page(top)) return 0;
    if (key == KEY_CENTER) {
        if (quick) return 0;
        int press = center_press(&st.edit_press, &st.edit_at, edit_single, 0);
        if (!press)
            press = center_press(&st.edit_press, &st.edit_at, edit_single,
                                 0); /* after an overdue one */
        if (press == 2 && g_backlight_status) on_wm_keyup_fun((void *)0, (void *)release);
        return 1;
    }
    if (key != KEY_PREV && key != KEY_NEXT) return 0;
    /* sliding open or shut (quick_stuck's states, or its slide timer): the wheel waits for it */
    if (quick && ((unsigned)(I(g_dropdown_menu_state, 0) - 1) < 2 ||
                  widget_get_prop_int(top, "timer_idex", -1) != -1))
        return 1;
    void *wm = window_manager();
    if (window_manager_is_animating(wm) || pointer_down()) return 1;
    int dir = key == KEY_NEXT ? 1 : -1;
    if (quick) {
        cc_wheel(top, dir); /* Control Center's stops, or a slider's level (control_center.c) */
    } else if (!n) {
        void *button = widget_lookup(top, dir > 0 ? "img_add" : "img_dec", 1),
             *bright = widget_lookup(top, "slider_backlight", 1);
        char click[0x30];
        if (bright)
            bright_wheel(bright, dir); /* Control Center's step, at the wheel's speed (levels.c) */
        else if (button && widget_get_prop_bool(button, "enable", 1))
            stock_dispatch_trampoline(button, pointer_event_init(click, EVT_CLICK, button, 0, 0));
    } else {
        int f = field_at(top);
        void *sel = field_widget(top, f);
        unsigned options = f < n && sel ? text_selector_count_options(sel) : 0;
        if (f == n)
            field_set(top, dir > 0 ? 0 : n - 1);
        else if (options && options <= 0x7fffffff) {
            int index = I(sel, SELECTOR_INDEX), next = clamp_step(index, (int)options - 1, dir);
            if (next != index) text_selector_set_selected_index(sel, (unsigned)next);
            field_set(top, f);
        }
    }
    return 1;
}

/* ringnav_paint, after the children: the field the wheel edits, framed in white; OK, when it is
 * next, as a selected row: an off-white card (MINIMAL_FILL) with its label dark. */
static void edit_paint(void *w, void *canvas) {
    void *top = window_manager_get_top_window(window_manager());
    int n = fields(top), f = n ? field_at(top) : 0;
    if (!w || !n || w != field_widget(top, f) || !P(canvas, CANVAS_LCD)) return;
    int ww = I(w, W_W), wh = I(w, W_H);
    if (ww < 5 || wh < 5) return;
    if (f == n) {
        unsigned fill = (unsigned)I(P(canvas, CANVAS_LCD), LCD_FILL_COLOR), k = 0;
        rect_t r = { 0, 0, ww, wh };
        const unsigned *text = widget_get_text(w);
        while (text && k < 16 && text[k]) ++k;
        fill_box(canvas, &r, RGBA(MINIMAL_FILL), SEL_RADIUS);
        draw_centred(canvas, text, k, &r, EDIT_OK_PX, RGBA(0x171717));
        stock_canvas_set_fill_color(canvas, fill);
        return;
    }
    unsigned old = (unsigned)I(P(canvas, CANVAS_LCD), LCD_STROKE_COLOR);
    rect_t r = { 0, 0, ww, wh };
    frame_box(canvas, &r, 0xffffffff, SEL_RADIUS);
    stock_canvas_set_stroke_color(canvas, old);
}

/* A shuffle_row theme's wheel and Centre on Songs' action row (shuffle_paint draws it): a tick up
 * from the first song puts the selection on Shuffle, another on Sort's bars when the list has
 * them; a tick down comes back. Centre on Shuffle plays the page's songs (p_deque_showlist, the
 * rows stock shows) as Shuffle Songs; on Sort it is a tap on the bars, stock's sort picker. 1 when
 * the key was the row's. */
static int shuffle_key(void *top, void *w, int dir) {
    void *row = st.action_view;
    /* another page on top (Now Playing, after Centre on Shuffle) keeps it for Return */
    if (!theme()->shuffle_row || !row || window_of(row) != top) return 0;
    if (!widget_get_visible(row) || widget_count_children(row) < 2) {
        st.shuffle_on = (void *)0;
        return 0;
    }
    int stops = widget_count_children(row) > 2 && widget_get_visible(widget_get_child(row, 2))
                    ? SORT_STOP
                    : SHUFFLE_STOP;
    if (st.shuffle_on != row || st.shuffle_list != w) {
        if (dir >= 0 || widget_get_prop_int(w, SEL, -1) != 0) return 0;
        st.shuffle_on = row;
        st.shuffle_list = w;
        st.shuffle_stop = SHUFFLE_STOP;
        widget_off_by_func(row, EVT_DESTROY, shuffle_gone, row);
        widget_on(row, EVT_DESTROY, shuffle_gone, row);
        stop_scroll(&g_menu);
        reveal(&g_menu, 0, 1); /* the row under it in view */
    } else if (dir) {
        int stop = st.shuffle_stop - dir; /* up the stops, down to the songs */
        if (stop > stops) { /* the last stop: above it only the search bar (search_bar.c) */
            search_bar_up_with(top, w, row);
            return 1;
        }
        if (stop) {
            st.shuffle_stop = stop;
        } else {
            select(&g_menu, 0); /* clears shuffle_on */
            reveal(&g_menu, 0, 1);
        }
    } else {
        cancel_center();
        if (st.shuffle_stop == SORT_STOP) {
            void *sort = widget_get_child(row, 2);
            char click[0x30];
            stock_dispatch_trampoline(sort, pointer_event_init(click, EVT_CLICK, sort, 0, 0));
        } else
            shuffle_page();
        return 1;
    }
    st.touch_mode = 0;
    widget_invalidate_force(row, (void *)0);
    widget_invalidate_force(w, (void *)0);
    return 1;
}

static int replay_wheel(const void *info);
int ringnav(void *ctx, void *event);
/* A wheel tick the page cannot take yet: counted, its event kept for replay. Replay's own tick,
 * held again, always counts back in (replay_wheel takes it off); live events never equal
 * st.held_event, so the cap still holds at intake. */
static int hold_wheel(void *ctx, void *event, int dir) {
    unsigned now = (unsigned)time_now_ms();
    if (!st.held) st.held_at = now;
    if (st.held * dir < HOLD_MAX || event == st.held_event) st.held += dir;
    st.held_ctx = ctx;
    if (event != st.held_event) memcpy(st.held_event, event, sizeof st.held_event);
    if (!st.held_timer) st.held_timer = timer_add(replay_wheel, 0, HOLD_POLL_MS);
    return STOP;
}

static int replay_wheel(const void *info) {
    void *wm = window_manager();
    if (!st.held || (unsigned)time_now_ms() - st.held_at > HOLD_MS || !usable()) {
        st.held = 0;
        st.held_timer = 0;
        return 0; /* anything but RET_REPEAT removes the timer */
    }
    if (window_manager_is_animating(wm) || pointer_down()) return 8;
    int dir = st.held > 0 ? 1 : -1;
    unsigned key = dir > 0 ? KEY_NEXT : KEY_PREV;
    st.held_event[EVENT_KEY / 4] = key;
    st.tone_key = key; /* clicks on its row change, as a live tick whose press was silenced */
    ringnav(st.held_ctx, st.held_event); /* held again: hold_wheel counts it back in */
    st.held -= dir;
    if (st.held) return 8; /* RET_REPEAT */
    st.held_timer = 0;
    return 0;
}

/* The keyboard is its own top window: take the edit from the input method's owner. Close it
 * before stock's Return or the page's own can destroy the edit under it, which crashes demo; the
 * next Return goes back. Stock's search dialogs and the playlist editor's search (search_edit)
 * keep their text. Every other T9 edit of ours (EDIT_NAME, edit_create) is a cancel: emptied
 * first, so the blur's EVT_VALUE_CHANGED reads as no entry and its page backs out of the edit. */
int search_key(unsigned key) {
    if (key != KEY_RETURN) return 0;
    void *im = input_method(), *edit = im ? P(im, IM_WIDGET) : (void *)0;
    const char *name = edit ? widget_get_prop_str(edit, "name", "") : "";
    int ours = !tk_strcmp(name, EDIT_NAME);
    if (!edit || (!ours && tk_strcmp(name, "search_edit"))) return 0;
    if (ours) widget_set_text_utf8(edit, "");
    widget_set_focused(edit, 0);
    input_method_request(im, (void *)0); /* searchbox dialogs do not close on blur */
    return 1;
}

int ringnav(void *ctx, void *event) {
    pull_cancel();
    /* The stock filter dereferences the event before returning. */
    if (!event) {
        drop_input();
        return 0;
    }
    unsigned key = (unsigned)I(event, EVENT_KEY);
    if (key == KEY_PREV || key == KEY_NEXT) wheel_tick_taken(); /* the bar's lean: its carry again */
    if (ple_key(key, 2)) return STOP;
    if (seek_release(key)) return STOP; /* a hold that scanned is no skip */
    /* This release's press was silenced by ringnav_keydown: a row change below clicks instead. */
    int owned = st.tone_key == key;
    st.tone_key = 0;
    if ((key == KEY_PREV || key == KEY_NEXT) &&
        *(volatile signed char *)KEY_LOCKOUT <= 8 - LOCKOUT_KEEP)
        *(volatile unsigned char *)KEY_LOCKOUT = 0;
    if (key == KEY_CENTER || key == KEY_RETURN) st.held = 0; /* a button ends a held turn */
    /* A tick the other way ends it too and is dropped with it, before or during its replay;
     * replay's own ticks go the held way. */
    if (st.held * (key == KEY_NEXT ? 1 : key == KEY_PREV ? -1 : 0) < 0) {
        st.held = 0;
        drop_input();
        return STOP;
    }
    void *front = window_manager_get_top_window(window_manager());
    if (usable() && field_key(front, key)) return STOP; /* before stock's track skip */
    /* The restart toast's msginfo_dialog stops Previous and Next itself (0x49b7c8), for its 2 s of
     * the 3 s: the same key goes on. ponytail: the toast stays up its time; another key is still
     * stopped. */
    if ((key == KEY_BACK_BTN || key == KEY_FWD_BTN) && restart_armed(key == KEY_FWD_BTN) && front &&
        !tk_strcmp(widget_get_type(front), "dialog")) {
        key == KEY_FWD_BTN ? next_quick_click() : prev_quick_click();
        return STOP;
    }
    /* A game's keys, before stock's lock filter, media keys and screen-off double Centre */
    if (usable() && games_key(window_manager_get_top_window(window_manager()), key)) return STOP;
    if (usable() && tuner_key(window_manager_get_top_window(window_manager()), key)) return STOP;
    if (usable() && radio_take_back(window_manager_get_top_window(window_manager()), key))
        return STOP;
    /* the row editors' keys, and the release of the Center hold that entered or left one */
    if (lr_edit_key(window_manager_get_top_window(window_manager()), key, owned)) return STOP;
    /* a grabbed inline slider's keys: the wheel sets it, Centre or Return sets it down */
    if (inline_slider_key(window_manager_get_top_window(window_manager()), key, owned)) return STOP;
    if (cc_key_release(key)) return STOP; /* the release of a Centre hold on Control Center */
    if (modal_closing()) return STOP; /* a menu sheet falling away (menu_modal.c) */
    if (un_key(window_manager_get_top_window(window_manager()), key, owned)) return STOP;
    if (usable() && search_strip_key(window_manager_get_top_window(window_manager()), key))
        return STOP;
    /* EQ's tab bar takes Previous and Next as Search's tabs do, before stock's track skip */
    if (usable() && (key == KEY_FWD_BTN || key == KEY_BACK_BTN) &&
        eq_key(window_manager_get_top_window(window_manager()), key))
        return STOP;
    if (usable() && add_songs_key(window_manager_get_top_window(window_manager()), key))
        return STOP;
    /* Return or Centre closes quick settings, as its swipe up does (dialog_quit), but on Control
     * Center (control_center.c cc_key) Centre acts on the stop and Return leaves a slider first;
     * stock leaves Return to its Home button and Centre to turning the screen off. A held Return's
     * release goes to the stock filter, which consumes its latch (the hold closed it itself,
     * compact_now_playing). */
    if ((key == KEY_CENTER ||
         (key == KEY_RETURN && !*(volatile unsigned char *)RETURN_RELEASE_LATCH)) &&
        usable()) {
        void *top = window_manager_get_top_window(window_manager());
        if (top && !tk_strcmp(widget_get_prop_str(top, "name", ""), "statusbar_dialog")) {
            if (cc_key(top, key)) return STOP;
            dialog_quit(top, 0);
            return STOP;
        }
        /* The library scan's dialog, once its scan has ended: stock's Return and Cancel only stop
         * a scan (0x4a2574) and do nothing until its timer (0x4a2bac) has counted SCAN_DONE to 3
         * and slept 2 s (1 s until V1.34), ~2.4 s, then close it itself; so a press there seemed
         * lost. The dialog's init clears SCAN_DONE (0x4a2de4). dialog_quit is safe twice, so the
         * timer's own close after ours does nothing. */
        if (top && *(volatile int *)SCAN_DONE &&
            !tk_strcmp(widget_get_prop_str(top, "name", ""), "updatemusic_dialog")) {
            dialog_quit(top, 0);
            return STOP;
        }
        /* the scan page (scan_page.c oled_scan) has Cancel on the selection bar: Centre presses
         * it, as a tap does, and stops the scan */
        void *cancel =
            key == KEY_CENTER && top &&
                    !tk_strcmp(widget_get_prop_str(top, "name", ""), "updatemusic_dialog")
                ? widget_lookup(top, "btn_no", 1)
                : (void *)0;
        if (cancel) {
            unsigned click[12];
            stock_dispatch_trampoline(cancel, pointer_event_init(click, EVT_CLICK, cancel, 0, 0));
            return STOP;
        }
    }
    if (usb_ui_key(window_manager_get_top_window(window_manager()), key))
        return STOP; /* USB's Stop */
    if (search_key(key)) return STOP;
    int result = usb_prompt() ? 0 : stock_keyup_trampoline(ctx, event);
    if (key == KEY_PLAY && play_hold_released()) return STOP;
    /* Media controls follow the active source, including when local Now Playing is visible. */
    if (!result && (media_focused() || media_owner() != 1) &&
        (key == KEY_PLAY || key == KEY_FWD_BTN || key == KEY_BACK_BTN)) {
        media_key(key);
        return STOP;
    }
    if (result) {
        cancel_center();
        if (key == KEY_PREV || key == KEY_NEXT) drop_wheel();
        /* Keep the home interval independent of rejected list navigation. */
        if (key == KEY_CENTER || !usable()) drop_spin();
        return result;
    }
    /* A volume dialog just opened is the window in front but not yet the window manager's key
     * target: that comes with its open event, on the pass's idle or after its animation. A tick
     * dispatched before then went past it to on_wm_keyup_fun, which opened one more dialog over
     * it; a dark pass takes several ticks, so a dark turn left a stack, each dialog closing 3 s
     * after the last and showing the volume it opened at (#303). The tick steps the volume here,
     * and the dialog's slider with it; dark, it goes on to on_wm_keyup_fun's hook, which drops it
     * (volume.c vol_wm_keyup). */
    void *vol = (key == KEY_PREV || key == KEY_NEXT) && g_backlight_status ? vol_top() : (void *)0;
    if (vol && P(window_manager(), W_KEY_TARGET) != vol) {
        vol_step(key);
        void *slider = widget_lookup(vol, "slider_vol", 1);
        if (slider) widget_set_prop_int(slider, "value", g_volume);
        return STOP;
    }
    if (key == KEY_RETURN && (st.scrub || st.np_stage)) { /* ends the scrub or the cycle's stage
                                                            without leaving the page */
        np_cancel();
        if (window_manager_get_top_window(window_manager()) == st.np_win) return STOP;
    }
    /* Full-screen lyrics: Return goes back to the column first, as a tap does */
    if (key == KEY_RETURN && st.np_full &&
        window_manager_get_top_window(window_manager()) == st.np_win) {
        np_full(0);
        return STOP;
    }
    /* Home's Settings list goes back to Home's list (coverflow_home_back). */
    if (key == KEY_RETURN && coverflow_home_back(window_manager_get_top_window(window_manager())))
        return STOP;
    /* Return on Home's main list selects its first row, Now Playing while something is loaded
     * (coverflow.c np_loaded); on Now Playing's row it opens Now Playing with the current queue
     * untouched. */
    if (key == KEY_RETURN && usable()) {
        void *wm = window_manager(), *top = window_manager_get_top_window(wm);
        void *w = home_menu(top) && !window_manager_is_animating(wm)
                      ? surface_under(top, (void *)0, (void *)0, 1)
                      : (void *)0;
        if (w && load(&g_menu, w, 1) && g_menu.n && g_menu.kind != 3) {
            st.touch_mode = 0;
            if (widget_get_prop_int(w, SEL, -1) != 0) {
                select(&g_menu, 0);
                reveal(&g_menu, 0, 1);
                widget_invalidate_force(w, (void *)0);
            } else if (!tk_strcmp(widget_get_prop_str(g_menu.at[0], "name", ""), "img_playing") &&
                       !guest_home_np()) /* the row reads Guest Play: the guest's (guest_ui.c) */
                compact_now_playing();
            return STOP;
        }
    }
    /* reopen_top: the list Return leaves opens at its top next time (a held Return's release is not
     * one) */
    if (key == KEY_RETURN && theme()->reopen_top &&
        !*(volatile unsigned char *)RETURN_RELEASE_LATCH && usable())
        forget_left();
    /* Back by button: the page behind shows its row. A sliding page is painted once more, into its
     * closing snapshot, inside stock's handling of this release; it keeps hiding its own row until
     * that is done. */
    if (key == KEY_RETURN && st.touch_mode) {
        rearm(&st.untouch_timer, untouch, 0);
        if (!st.untouch_timer) st.touch_mode = 0;
    }
    if (key != KEY_CENTER && key != KEY_PREV && key != KEY_NEXT) return result;
    if (key == KEY_CENTER)
        drop_spin();
    else
        cancel_center();
    if (st.center_timer && (unsigned)time_now_ms() - st.last_center >= DOUBLE_CLICK_MS) {
        unsigned timer = st.center_timer;
        confirm_center((void *)0);
        timer_remove(timer);
    }
    /* An overdue click may change power/lock state; inspect it after its callback. */
    if (!usable()) {
        drop_input();
        return result;
    }
    /* Match the stock power-key release exclusions, including release after long press. */
    if (key == KEY_CENTER &&
        (g_power_longkey || g_ingore_bootkey_flag || *(volatile unsigned char *)BOOT_KEY_GUARD)) {
        /* V1.36's confirm takes every Centre release itself (0x499b9c), so the one that ends the
         * hold that opened Shut Down? never reaches on_wm_keyup_fun, which cleared these: cleared
         * here, or each later Centre in the prompt is dropped as that release (#303). */
        void *held = window_manager_get_top_window(window_manager());
        if (held && !tk_strcmp(widget_get_prop_str(held, "name", ""), "confirminfo_dialog")) {
            g_power_longkey = g_ingore_bootkey_flag = 0;
            *(volatile unsigned char *)BOOT_KEY_GUARD = 0;
        }
        cancel_center();
        return result;
    }
    void *wm = window_manager(), *top = window_manager_get_top_window(wm);
    if (spot_key(top, key)) return STOP;     /* Spotify's page: Centre scrubs */
    if (np_about_key(top, key)) return STOP; /* OLED's About: the wheel scrolls it */
    if (sw_update_key(top, key)) return STOP; /* Software Update's button and the line under it */
    if (localsend_key(top, key)) return STOP;  /* LocalSend's transfer page: Center is Cancel */
    if (battery_key(top, key)) return STOP;   /* Battery History: the wheel scrubs, Centre exports */
    if (xfade_key(top, key))
        return STOP; /* Crossfade's page: Centre toggles, the wheel sets the length */
    if (eq_key(top, key))
        return STOP; /* EQ's Simple tab, or its tabs: the wheel moves or adjusts, Centre acts */
    if (edit_key(top, key)) {
        drop_input();
        return STOP;
    }

    /* Outside a scrub the volume's band over Now Playing leaves the keys the page's: Centre's
     * presses as on it, the wheel the volume's, and a pending press survives the ticks */
    int np = top == st.np_win || (!st.scrub && vol_band(wm));
    if (!np || window_manager_is_animating(wm) || pointer_down())
        np_cancel();
    else if (key != KEY_CENTER && (np_rate_key(key) || np_lyrics(key)))
        return STOP; /* the rating page's target (np_rate.c), or the lyrics */
    else if (key == KEY_CENTER || st.scrub || st.np_stage)
        return np_key(st.np_win, key);
    /* A keyboard (T9's, every text box's) on top takes Centre as a listed page does: a single press
     * does nothing, a double turns the screen off; stock turned it off on each one (#365). Its
     * wheel goes on to on_wm_keyup_fun, whose hook drops it (volume.c vol_wm_keyup). */
    int kb = key == KEY_CENTER && top && !tk_strcmp(widget_get_type(top), "keyboard");
    if (!allowed_top(top) && !usb_prompt()) {
        drop_input();
        if (!kb) return result;
    }
    if (search_bar_key(top, key)) return STOP; /* a music list's lit search bar (search_bar.c) */
    /* A double Centre turns the screen off on every listed page, also where the page has no row
     * selected or is still sliding in. A list's
     * selected row times its own double press (center_timer). */
    if (key == KEY_CENTER && !st.center_timer) {
        unsigned now = (unsigned)time_now_ms();
        if (top == st.dc_top && now - st.dc_at < DOUBLE_CLICK_MS) {
            static const unsigned release[EVENT_KEY / 4 + 1] = { [EVENT_KEY / 4] = KEY_CENTER };
            st.dc_top = (void *)0;
            drop_input();
            if (g_backlight_status) on_wm_keyup_fun((void *)0, (void *)release);
            return STOP;
        }
        st.dc_top = top;
        st.dc_at = now;
    }
    if (kb) return STOP;
    int dir = key == KEY_NEXT ? 1 : key == KEY_PREV ? -1 : 0;
    if (window_manager_is_animating(wm) || pointer_down()) {
        drop_input();
        return dir && window_manager_is_animating(wm) ? hold_wheel(ctx, event, dir) : STOP;
    }
    /* Songs over an empty library: stock hides the list and shows its note over Scan Music Now
     * (btn_upmusic), which takes no key. With no list under it, Centre would go on to
     * on_wm_keyup_fun, whose Centre turns the screen off; it presses the button, as a tap does
     * (docs/internals.md#center-button). */
    void *scan = !dir && top ? widget_lookup(top, "btn_upmusic", 1) : (void *)0;
    if (scan && widget_get_visible(scan) && widget_get_visible(P(scan, W_PARENT))) {
        unsigned click[12];
        stock_dispatch_trampoline(scan, pointer_event_init(click, EVT_CLICK, scan, 0, 0));
        return STOP;
    }
    coverflow_lost_touch();
    if (!dir) coverflow_wheel_land(); /* Centre opens the album the caption names */
    void *w = surface_under(top, (void *)0, (void *)0, dir != 0);
    if (!is_home(top, w) || w != st.home_surface) st.home_surface = (void *)0;
    if (!w || !load(&g_menu, w, 1)) {
        drop_input();
        return dir ? hold_wheel(ctx, event, dir) : result;
    }
    if (shuffle_key(top, w, dir)) return STOP; /* OLED's Shuffle row over the songs */
    if (pair_key(top, w, dir)) return STOP;    /* an artist's Shuffle and Radio (artist_page.c) */
    if (st.center_timer && !pending_matches(top, &g_menu)) cancel_center();
    int touch = widget_get_prop_int(w, TOUCH, 0);
    if (touch) {
        stop_scroll(&g_menu);
        prop(w, TOUCH, 0); /* Centre consumes the interrupted gesture too. */
    }
    int cur = reconcile(&g_menu, touch || !moving(&g_menu));
    unsigned now = (unsigned)time_now_ms();
    if (!dir) {
        if (st.center_timer && now - st.last_center < DOUBLE_CLICK_MS) {
            cancel_center();
            return result; /* Let the stock downstream short-press handler turn the screen off. */
        }
        cancel_center();
        if (cur < 0) return STOP;
        st.touch_mode = 0;
        select(&g_menu, g_menu.id[cur]);
        widget_invalidate_force(w, (void *)0);
        st.last_center = now;
        st.center_top = top;
        st.center_surface = w;
        st.center_scope = g_menu.scope;
        st.center_ctx = g_menu.ctx;
        st.center_rows = g_menu.rows;
        st.center_id = g_menu.id[cur];
        row_id_t r = row_id(g_menu.at[cur]);
        st.center_hash = r.one;
        st.center_hash2 = r.two;
        st.center_timer = timer_add(confirm_center, (void *)0, DOUBLE_CLICK_MS);
        st.center_token = st.center_timer;
        if (st.center_timer) {
            prop(top, CONFIRM, (int)st.center_token);
            prop(w, CONFIRM, (int)st.center_token);
            if (g_menu.kind != 2) prop(g_menu.at[cur], CONFIRM, (int)st.center_token);
        } else
            cancel_center(); /* Allocation failure consumes the press without a click. */
        return STOP;
    }
    st.touch_mode = 0;
    if (is_home(top, w)) {
        if (coverflow_wheel_on(w)) return STOP; /* a tick queued as the wheel went raw */
        /* the lists' momentum (wheel_accel.c), rows only: a length of 0, under WA_LETTER_ROWS, so
         * Cover Flow never jumps by letter */
        static wheel_accel_t cf_wa __attribute__((section(".scratch")));
        home_step(w, dir, now, wheel_accel(&cf_wa, w, dir, now, 0));
        tilt_poll(); /* the bar's lean as it steps: the tick's carry (playback.c wheel_part) */
        widget_invalidate_force(w, (void *)0);
        return STOP;
    }
    /* Every accepted tick times the run and takes its place in it, short lists included. */
    int list = g_menu.n; /* row lists: the gentler ramp */
    int step = ramp(top, g_menu.w, g_menu.scope, g_menu.ctx, dir, now,
                    list ? LIST_FIRST_MS : WHEEL_RAMP_MS, list ? LIST_RAMP_MS : WHEEL_RAMP_MS);
    if (g_menu.rows <= SHORT_LIST_MAX) {
        st.wheel_run = 0;
        step = 1;
    }
    wheel_sb_arm(g_menu.w); /* the theme's indicator (list_marks.c); stock's bar stays asleep */
    if (g_menu.kind == 3) {
        if (dir > 0)
            slide_menu_scroll_to_next(w);
        else
            slide_menu_scroll_to_prev(w);
    } else if (g_menu.n) {
        int id = widget_get_prop_int(w, SEL, -1);
        /* The letter only over a name-sorted list (az_index.c name_sorted). Tested here, as at
         * paint the top may already be the page a Return brings back. */
        if (step > 1 && name_sorted(widget_get_prop_str(top, "name", "")))
            rearm(&st.letter_timer, letter_expire, LETTER_MS);
        int next = clamp_step(id < 0 ? (cur >= 0 ? g_menu.id[cur] : 0) : id, g_menu.rows - 1,
                              id < 0 ? 0 : dir * step);
        /* a fast spin that goes on, on a long name-sorted table, moves a letter a tick (an iPod
         * classic's, docs/internals.md#wheel-acceleration); back to rows once it slows */
        static wheel_accel_t wa __attribute__((section(".scratch")));
        if (id >= 0 && g_menu.kind == 2 &&
            wheel_accel(&wa, g_menu.w, dir, now, g_menu.rows) == WA_LETTER) {
            int to = az_letter_step(g_menu.w, id, dir);
            if (to >= 0) next = to;
        }
        if (next == 0 || next == g_menu.rows - 1) drop_wheel();
        if (next == id) { /* a list ends at its ends: no wrap; the bar leans against the end
                             (tilt.c), but above a music list's first row stands its search bar */
            if (dir < 0 && !id && search_bar_up(top, g_menu.w)) return STOP;
            tilt_end(w, dir);
            tilt_poll(); /* held full from this paint on, never the bare carry */
            stop_scroll(&g_menu);
            widget_invalidate_force(w, (void *)0); /* a wheel wake still clears touch hiding */
            return STOP;
        }
        select(&g_menu, next);
        /* Stop momentum even when the selected row already fits the viewport. */
        reveal(&g_menu, next, 1);
        az_wheel(w, next); /* a fast spin's A-Z index (az_index.c) */
        /* Home's pane repaints in this frame, with the row. */
        if (home_menu(top) && index_of(&g_menu, next) >= 0)
            coverflow_home_row(w, g_menu.at[index_of(&g_menu, next)], next, 0);
        if (owned && id >= 0) buzzeer_switch(1); /* one click per row change, however far */
    } else {
        /* no row to select: the content's foot on the list end, where a drag stops too (list_fade's
         * padding), so text just past the end under the fade still comes up */
        int top = view_top(&g_menu), end = max_top(&g_menu);
        int next = clamp_step(top, end, dir * RING_STEP * step);
        if (next == 0 || next >= end) st.wheel_run = 0;
        wheel_offset(&g_menu, next);
    }
    tilt_poll(); /* the bar's lean as it steps: the tick's carry (playback.c wheel_part) */
    widget_invalidate_force(w, (void *)0);
    return STOP;
}
