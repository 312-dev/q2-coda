/* A s_listitem_black list_item in view with a 335x70 s_btn_listitem button; returns the button. */
static void *list_button(void *view) {
    void *item = list_item_create(view, 0, 0, 0, 0);
    widget_use_style(item, "s_listitem_black");
    void *button = button_create(item, 20, 0, 335, 70);
    widget_use_style(button, "s_btn_listitem");
    return button;
}

/* A stock settings-style row (0x4c6b94): a list_button with a 52px icon and a 24px label at x 72
 * (no icon: at x 10, as the Bluetooth page's rows), into adding stock's list_into trailing image
 * with the label narrowed clear of it, as the stock Local Music categories place it. Returns the
 * label. */
void *list_row_core(void *view, const char *icon, int (*click)(void *, void *), void *ctx, int w,
                    int into) {
    /* The Library constructor's words tools/ipod.py baked: lib_body and lib_inset. */
    int compact = !tk_strcmp(widget_get_prop_str(view, "name", ""), "scroll_view_localmusic");
    int h = compact ? *(const volatile unsigned short *)LIBRARY_ROW_BODY : 70;
    void *button = list_button(view);
    if (compact) widget_resize(button, 335, h);
    if (click) widget_on(button, EVT_CLICK, click, ctx);
    if (icon) { /* centred, as stock's rows (0x5356ac) */
        void *image = image_create(
            button, compact ? *(const volatile unsigned short *)LIBRARY_ROW_ICON_X : 10, 0,
            SET_STOCK_ICON, h);
        image_base_set_image(image, icon);
        image_set_draw_type(image, IMAGE_DRAW_CENTER);
    }
    void *label = hscroll_label_create(button, icon ? 72 : 10, 0, w, h);
    widget_use_style(label, "s_scrlabel_white24l");
    set_hscroll_label_attribute(label);
    if (into) image_base_set_image(image_create(button, 282, 0, 50, h), "list_into");
    return label;
}

void *list_row(void *view, const char *icon, int (*click)(void *, void *), void *ctx) {
    return list_row_core(view, icon, click, ctx, 260, 0);
}

/* A settings row that opens a page, its label clear of the chevron. */
void *list_row_into(void *view, const char *icon, int (*click)(void *, void *), void *ctx) {
    return list_row_core(view, icon, click, ctx, 202, 1);
}

/* Use the same trailing switch images and hit target as Shanling's settings rows: at screen x 302
 * whether the label sits in a settings button (x 20) or a full-width page row (page_row_detail,
 * peq_ui.c's). A set_text theme writes On or Off as the row's value instead, unless it is also
 * set_switch, whose switch paint_into draws over the emptied image. */
void list_toggle(void *label, const char *title, int on) {
    if (theme()->set_text && !theme()->set_switch) {
        list_value(label, title, on ? "On" : "Off");
        return;
    }
    void *button = P(label, W_PARENT);
    void *image = widget_lookup(button, "_setting_switch", 0);
    if (!image) {
        /* on the set's right column */
        int x = 375 - geom_val(V_RIGHT_X) - 50 - I(button, W_X);
        image = image_create(button, x, 0, 50, I(button, W_H));
        widget_set_name(image, "_setting_switch");
        image_set_draw_type(image, IMAGE_DRAW_CENTER);
        widget_move_resize(label, I(label, W_X), 0, x - I(label, W_X) - 8, I(button, W_H));
    }
    widget_set_text_utf8(label, title);
    image_base_set_image(image, on ? "switch_on" : "switch_off");
}

/* A set_text theme's settings rows: the title left and the value right-aligned in the
 * trailing column, the title ending short of it. Switches read On or Off as values, except on a
 * set_switch theme (list_toggle). */
/* The value's box ends on the set's right column, or VALUE_CHEVRON_GAP short of it on a
 * row that opens a page (its chevron is there), and the title and value split the row as a
 * label/value row does (kv_row); unmeasured (no canvas yet), the boxes stay as made. */
static void value_fit(void *label, void *text) {
    void *button = P(label, W_PARENT);
    int into =
        drill(button); /* a drill page's row, or one with stock's list_into (list_row_into) */
    for (unsigned i = 0, n = widget_count_children(button); !into && i < n; ++i)
        into =
            !tk_strcmp(widget_get_prop_str(widget_get_child(button, i), "image", ""), "list_into");
    int right = 374 - geom_val(V_RIGHT_X) - (into ? VALUE_CHEVRON_GAP : 0) - I(button, W_X);
    int w = I(text, W_W);
    if (I(text, W_X) + w != right) widget_move_resize(text, right - w, 0, w, I(text, W_H));
    int kw = kv_text_w(label), vw = kv_text_w(text);
    if (kw >= 0 && vw >= 0) kv_row(label, text, kw, vw, right);
}
void list_value(void *label, const char *title, const char *value) {
    void *button = P(label, W_PARENT);
    void *text = widget_lookup(button, "_setting_value", 0);
    if (!text) {
        /* its text's right end on the set's right column (hscroll_label keeps 2 px) */
        int x = 374 - geom_val(V_RIGHT_X) - 134 - I(button, W_X);
        text = hscroll_label_create(button, x, 0, 134, I(button, W_H));
        widget_set_name(text, "_setting_value");
        widget_use_style(text, "s_scrlabel_white20r");
        hscroll_label_set_only_focus(text, 1);
        hscroll_label_set_ellipses(text, 1);
        widget_move_resize(label, I(label, W_X), 0, x - 8 - I(label, W_X), I(button, W_H));
    }
    widget_set_text_utf8(label, title);
    widget_set_text_utf8(text, value);
    theme_font(text, ROLE_TITLE, FONT_ROW);
    /* a theme's value grey, final in each half (text_ink); the selection's sel_text still wins */
    unsigned ink = theme()->set_value_ink[ipod_light()];
    if (ink) text_ink(text, 0xff000000u | ink, INK_LABEL);
    value_fit(label, text);
}

/* Without stock's toolbars (tools/ipod.py patch_asset) the Sleep Timer's switch is a row: stock's
 * img_switch stays, hidden with its toolbar, for stock's callbacks to find by name, and the row's
 * click is its click, on the next loop pass. The page is list-less, so the row is over its fields,
 * tools/ipod.py moving them down. The row reads what the image shows. */
#define SWITCH_ROW "_switch_row"
static void switch_text(void *win, void *label) {
    void *sw = widget_lookup(win, "img_switch", 1);
    list_toggle(label, "Sleep Timer",
                sw && !tk_strcmp(widget_get_prop_str(sw, "image", ""), "switch_on"));
}
static int switch_due(const void *info) {
    (void)info;
    void *win = window_manager_get_top_window(window_manager());
    void *sw = win ? widget_lookup(win, "img_switch", 1) : (void *)0;
    char click[0x30];
    if (!sw) return 0;
    stock_dispatch_trampoline(sw, pointer_event_init(click, EVT_CLICK, sw, 0, 0));
    void *label = widget_lookup(win, SWITCH_ROW, 1);
    if (label) switch_text(win, label);
    return 0;
}
static int switch_click(void *ctx, void *event) {
    (void)ctx, (void)event;
    timer_add(switch_due, (void *)0, 0);
    return 0;
}
extern int stock_sleeptimer_trampoline(void *win, void *ctx);
int ringnav_sleeptimer(void *win, void *ctx) {
    int result = stock_sleeptimer_trampoline(win, ctx);
    if (win) {
        int h = geom_val(V_SET_ROW);
        void *button = button_create(win, 0, 0, 375, h);
        widget_use_style(button, "s_btn_listitem");
        widget_on(button, EVT_CLICK, switch_click, (void *)0);
        void *label = hscroll_label_create(button, geom_val(V_TEXT_X), 0, 200, h);
        widget_use_style(label, "s_scrlabel_white24l");
        set_hscroll_label_attribute(label);
        widget_set_name(label, SWITCH_ROW);
        switch_text(win, label);
        theme_font(label, ROLE_TITLE, FONT_ROW);
    }
    return result;
}

/* A settings row's "Title: value" text; a set_text theme shows it as list_value, its bare text
 * (Accent's name) under Accent. */
static void row_label(void *label, const char *text);
/* A feature's settings row's "Title: value" (Crossfade, Resume Songs, Library & Folders' rows): the
 * title alone with the value on the set's right column, as a key/value row (row_label), else, with
 * no ": " or under a theme without set_text, as written. */
void row_value_text(void *label, const char *text) {
    const char *v = text;
    while (*v && !(v[0] == ':' && v[1] == ' ')) ++v;
    if (*v && theme()->set_text)
        row_label(label, text);
    else
        widget_set_text_utf8(label, text);
}
static void row_label(void *label, const char *text) {
    if (!theme()->set_text) {
        widget_set_text_utf8(label, text);
        return;
    }
    const char *value = text;
    while (*value && !(value[0] == ':' && value[1] == ' ')) ++value;
    char title[40] = "Accent";
    unsigned n = (unsigned)(value - text);
    if (*value) {
        if (n >= sizeof title) n = sizeof title - 1;
        memcpy(title, text, n);
        title[n] = 0;
    }
    list_value(label, title, *value ? value + 2 : text);
}

/* item's place in list, comma separated, or 0; an empty item is never in it. Shared with
 * spotify.c. */
char *csv_find(char *list, const char *item) {
    unsigned n = strlen(item);
    for (char *p = list, *e; n && *p; p = e + !!*e) {
        for (e = p; *e && *e != ','; ++e) { /* demo imports no strchr */
        }
        if ((unsigned)(e - p) == n && !memcmp(p, item, n)) return p;
    }
    return 0;
}

/* item taken out of list, or added within size bytes: 1 added, 0 taken out (or empty, left
 * out), -1 no room. */
int csv_toggle(char *list, unsigned size, const char *item) {
    char *p = csv_find(list, item), *from;
    unsigned n = strlen(item), k = strlen(list);
    if (!n) return 0;
    if (p) {
        from = p + n + (p[n] == ',');
        if (!p[n] && p != list) --p; /* the last: its comma goes with it */
        while ((*p++ = *from++)) {}
        return 0;
    }
    if (k + !!k + n >= size) return -1;
    if (k) list[k++] = ',';
    memcpy(list + k, item, n + 1);
    return 1;
}

/* Buttons Lock, Off or On: On is stock's LOCKMODE 2, which with the screen off locks Return,
 * Play/Pause and the skip keys. The dark wheel does nothing (keylock.c wheel_lock_gate), so stock's
 * wheel modes (1, 3) lock nothing more; keylock.c keylock_boot makes an earlier one of these two.
 * KEY_LOCKOUT is the separate, temporary debounce after a button press. */
static const char *const lock_names[] = { "Off", "On" };
static int lock_choice(void) { return g_keylock_flag != 0; }
static void lock_set(int choice) {
    g_keylock_mode = 2;
    g_keylock_flag = choice != 0;
    write_int_config(g_keylock_mode, "SYSSET", "LOCKMODE");
    write_int_config(g_keylock_flag, "SYSSET", "LOCKFLAG");
}
static int lock_pick(void *ctx, void *event) {
    lock_set((int)(long)ctx);
    void *win = window_manager_get_top_window(window_manager());
    void *view = widget_lookup(win, "scroll_view_keylock", 1);
    int choice = lock_choice();
    for (unsigned i = 0; view && i < widget_count_children(view); ++i) {
        void *button = widget_get_child(widget_get_child(view, i), 0);
        image_base_set_image(widget_get_child(button, 0), (int)i == choice ? "select" : "unselect");
    }
    return 0;
}
int ringnav_keylock_rows(void *win) {
    if (picker_rows(win)) return 0; /* a settings picker over the same page (library_settings.c) */
    void *view = win ? widget_lookup(win, "scroll_view_keylock", 1) : 0;
    if (!view) return 0;
    widget_destroy_children(view);
    int choice = lock_choice();
    for (int i = 0; i < 2; ++i) {
        void *label =
            list_row(view, i == choice ? "select" : "unselect", lock_pick, (void *)(long)i);
        widget_set_text_utf8(label, lock_names[i]);
    }
    return 0;
}
static void quick_lock_text(void *win) {
    widget_set_text_utf8(widget_lookup(win, "label_lock", 1), lock_names[lock_choice()]);
    image_base_set_image(widget_lookup(win, "img_lock", 1),
                         g_keylock_flag ? "drop_keylockopen" : "drop_keylock");
}
int ringnav_quick_lock(void *win, void *event) {
    lock_set(!lock_choice());
    quick_lock_text(win);
    widget_invalidate_force(win, (void *)0); /* OLED's row paints the value: its label is hidden */
    return 0;
}
#define QUICK_HOLD "_quick_hold"
#define SLIDE_PX 10 /* the brightness slider's sideways move before it takes the touch */
static struct {
    void *win, *icon, *slider;
    unsigned timer;
    int x, y, sx, sy, slide; /* slide: the slider's touch, 0 undecided, 1 its own, -1 the swipe's */
} quick __attribute__((section(".scratch")));

static int quick_cancel(void *ctx, void *event) {
    stop_timer(&quick.timer);
    quick.win = quick.icon = quick.slider = 0;
    return 0;
}
static int quick_long(const void *unused) {
    quick.timer = 0;
    void *win = quick.win, *icon = quick.icon;
    if (!win || window_manager_get_top_window(window_manager()) != win || !pointer_down()) return 0;
    int page = widget_get_prop_int(icon, QUICK_HOLD, 0);
    /* Abort the image's press before closing, so its release cannot click the icon. */
    prop(icon, QUICK_HOLD, -page);
    unsigned abort_event[12];
    pointer_event_init(abort_event, EVT_POINTER_ABORT, win, 0, 0);
    widget_dispatch_event_to_target_recursive(win, abort_event);
    quick_cancel(0, 0);
    dialog_quit(win, 0);
    if (page >= QUICK_CC)
        return cc_hold(page - QUICK_CC), 0; /* Control Center's (control_center.c) */
    /* an open page is already the goal (control_center.c cc_page) */
    cc_page(page == 1   ? "systemset/wifi_page"
            : page == 2 ? "systemset/bluetooth_page"
                        : "playset/equalizer_page"); /* as Playback's EQ row (0x4bd114) */
    return 0;
}
static int quick_down(void *icon, void *event) {
    int page = widget_get_prop_int(icon, QUICK_HOLD, 0);
    if (!page) return 0; /* a button with no hold */
    prop(icon, QUICK_HOLD, page < 0 ? -page : page);
    quick.win = window_manager_get_top_window(window_manager());
    quick.icon = icon;
    quick.x = I(event, EVENT_X), quick.y = I(event, EVENT_Y);
    rearm(&quick.timer, quick_long, QUICK_HOLD_MS);
    return 0;
}
/* button held QUICK_HOLD_MS opens page (quick_long): 1 Wi-Fi's settings, 2 Bluetooth's, 3 the EQ
 * page, QUICK_CC and on Control Center's stop's (control_center.c cc_hold). */
void quick_hold(void *button, int page) {
    prop(button, QUICK_HOLD, page);
    widget_on(button, EVT_POINTER_DOWN, quick_down, button);
}
int ringnav_quick_settings(void *win, void *ctx) {
    int result = stock_quicksettings_trampoline(win, ctx);
    if (win) {
        quick_lock_text(win);
        /* held: 1 Wi-Fi's settings, 2 Bluetooth's, 3 the EQ page (Playback's) */
        static const char *const names[] = { "img_wifiswitch", "img_btswitch", "img_playset" };
        for (int i = 0; i < 3; ++i) {
            void *icon = widget_lookup(win, names[i], 1);
            if (icon) quick_hold(icon, i + 1);
        }
        cc_open(win); /* Control Center (control_center.c) */
        widget_on(win, EVT_DESTROY, quick_cancel, win);
        widget_on(win, EVT_WINDOW_CLOSE, quick_cancel, win);
        widget_on(win, EVT_WINDOW_BACKGROUND, quick_cancel, win);
    }
    return result;
}

/* Power's Charge limit and Wake (docs/internals.md#charge-limit, #wake), Q2POD CHARGELIMIT and
 * WAKEDOUBLE in config.ini, the Library's Artist View
 * (ARTISTCF, read by Go to Album: hold_menu.c qm_goto), Playback's Year order (YEAROLD, coverflow.c's year ranks) and Artists, which
 * is stock's own PLAYSET ARTISTTYPE (artist_type, the artist page's switch;
 * docs/internals.md#album-artists), and Bluetooth's Resume on connect (RESUME, so a missing key
 * holds playback; docs/internals.md#resume-on-connect), and Playback's Artist album order
 * (ARTISTAZ), and Display's Hold Return (HOLDRETURN, compact_now_playing;
 * docs/ipod.md#hold-return), and Power's Standby limit (STANDBYLIMIT, ringnav_standby;
 * docs/internals.md#standby-limit), and Bluetooth's Link Volume (BTVOLLINK,
 * bt_audio_ready; docs/internals.md#headset-controls-and-volume), and Power's Smart Wi-Fi
 * (SMARTWIFI, smart_poll; docs/internals.md#smart-wi-fi), and Display's Fast Navigation
 * (BIGLIBRARY, Cover Flow's letters; docs/internals.md#coverflow-thumb) and Playlist View
 * (PLAYLISTCF).
 */
/* Bluetooth's Link Volume as saved, -1 never chosen (bt_link): on, the Q2's volume is the device's
 * own (bt_audio_ready). */
static int bt_vol_link __attribute__((section(".scratch")));
/* Playback > Library's On-The-Go (OTG, playlist_pages.c): the Playlists row and the hold menu's Add
 * to On-The-Go; on unless the key says 0. */
static int otg_on __attribute__((section(".scratch")));
/* Date & Time's Automatic Time Zone (autozone.c): on unless the key says 0. */
static int autozone_on __attribute__((section(".scratch")));
#define STR_(x) #x
#define STR(x) STR_(x)
static int *const pod_values[] = { &st.charge_limit,
                                   &st.wake_double,
                                   &st.artist_cf,
                                   &st.year_old,
                                   0,
                                   &st.resume,
                                   &st.artist_az,
                                   &st.hold_home,
                                   &st.standby_limit,
                                   &bt_vol_link,
                                   0,
                                   0,
                                   &st.eq_output,
                                   0,
                                   0,
                                   &st.smart_wifi,
                                   0,
                                   0,
                                   &st.big_library,
                                   &st.playlist_cf,
                                   &st.scan_durations,
                                   &st.cf_position,
                                   &otg_on,
                                   &autozone_on };
_Static_assert(sizeof pod_values / sizeof *pod_values == POD_COUNT, "a pod_values per row");
static const char *const pod_keys[] = { "CHARGELIMIT",
                                        "WAKEDOUBLE",
                                        "ARTISTCF",
                                        "YEAROLD",
                                        0,
                                        "RESUME",
                                        "ARTISTAZ",
                                        "HOLDRETURN",
                                        "STANDBYLIMIT",
                                        "BTVOLLINK",
                                        0,
                                        0,
                                        "EQOUTPUT",
                                        0,
                                        0,
                                        "SMARTWIFI",
                                        0,
                                        0,
                                        "BIGLIBRARY",
                                        "PLAYLISTCF",
                                        "SCANDURATIONS",
                                        "CFPOSITION",
                                        "OTG",
                                        "AUTOZONE" };
_Static_assert(sizeof pod_keys / sizeof *pod_keys == POD_COUNT, "a pod_keys entry per row");
/* a row's choices: Artist View three, Standby limit four, every other row two */
static int pod_choices(unsigned i) { return i == POD_ARTIST_CF ? 3 : i == POD_STANDBY ? 4 : 2; }
void pod_settings(void) {
    if (st.pod_read) return;
    for (unsigned i = 0; i < sizeof pod_keys / sizeof *pod_keys; ++i)
        if (pod_keys[i]) /* Artist View defaults to Cover Flow, On-The-Go and Set Time Zone
                          * Automatically to on; Link Volume never chosen is -1 (bt_link) */
            *pod_values[i] =
                config_value("Q2POD", pod_keys[i], pod_choices(i),
                             i == POD_BT_LINK                                          ? -1
                             : i == POD_ARTIST_CF || i == POD_OTG || i == POD_AUTOZONE ? 1
                                                                                       : 0);
    st.pod_read = 1;
}
int ringnav_year_old(void) {
    pod_settings();
    return st.year_old;
}
int ringnav_artist_az(void) {
    pod_settings();
    return st.artist_az;
}
int ringnav_artist_view(void) {
    pod_settings();
    return st.artist_cf;
}
int ringnav_otg(void) {
    pod_settings();
    return otg_on;
}
int autozone_enabled(void) {
    pod_settings();
    return autozone_on;
}
/* A zone picked by hand (autozone.c autozone_config): the switch off, saved, and its row redrawn.
 */
void autozone_off(void) {
    pod_settings();
    autozone_on = 0;
    write_int_config(0, "Q2POD", "AUTOZONE");
    pod_text_again(POD_AUTOZONE);
}
int ringnav_playlist_coverflow(void) {
    pod_settings();
    return st.playlist_cf;
}
/* Fast Navigation: Cover Flow's letters and their half copies (coverflow_thumb.c, lettered,
 * coverflow_halves); off unless the key is 1. */
int ringnav_big_library(void) {
    pod_settings();
    return st.big_library;
}
int coverflow_position_enabled(void) {
    pod_settings();
    return st.cf_position;
}
int ringnav_eq_output(void) {
    pod_settings();
    return st.eq_output;
}
/* Advanced EQ's row. Turned off, what plays stays. */
void ringnav_eq_output_toggle(void) {
    pod_settings();
    st.eq_output = !st.eq_output;
    write_int_config(st.eq_output, "Q2POD", "EQOUTPUT");
    if (!st.eq_output) eq_output_forget();
}
int ringnav_bt_resume(void) {
    pod_settings();
    return st.resume;
}
/* Link Volume as it applies now: the choice saved, else on for the device connected now when its
 * Class is headphones or a headset (bt_kind's "headphones", from the battery run's read of it,
 * bt_battery.c) and off for a speaker, a car or a device not yet known, whose own amplifier a low
 * Q2 volume would turn down with it (docs/internals.md#headset-controls-and-volume). */
static int bt_link(void) {
    pod_settings();
    return bt_vol_link >= 0 ? bt_vol_link : !tk_strcmp(bt_kind(bt_headset_class()), "headphones");
}
/* read_artistype_value (a Library list's or artist page's init) and boot (playback.c
 * ringnav_bootmode): Artists as stock reads it, PLAYSET ARTISTTYPE, but Album Artist when no choice
 * is saved; stock writes the key only when Artists or the artist page's switch toggles. */
void ringnav_read_artistype(void) {
    I(artist_type, 0) = config_value("PLAYSET", "ARTISTTYPE", 2, 1);
}
/* checkBluetoothStatus' player_save_start(0) once a headset's A2DP comes up (tools/build.py
 * BT_CONNECT_START): player_save_stop stopped the local or Tidal queue, and stock restarts it
 * unpaused, so a paused queue plays, and a stopped one starts over whatever owns playback
 * (mclStartPlayer's hook yields Spotify and Internet Radio). Resume on connect off (the default):
 * a queue that was playing goes on, a paused one comes back paused, and nothing else starts. A
 * card game with the sound (addon.c) keeps it either way. */
int ringnav_bt_connect_start(int pause) {
    if (addon_game_audio()) return 0;
    if (ringnav_bt_resume()) return player_save_start(pause);
    int saved = media_owner() == 1 ? mclGetSavePlayStatus() : 0; /* 2 playing, 3 paused */
    return saved == 2 || saved == 3 ? player_save_start(saved == 3) : 0;
}
static void pod_text(int i) {
    static const char *const names[][4] = {
        { "Charge Limit: Off", "Charge Limit: " STR(CHARGE_STOP) "%" },
        { "Wake: Any Button", "Wake: Double Press" },
        { "Artist View: Grid", "Artist View: Cover Flow", "Artist View: List" },
        { "Years: Newest First", "Years: Oldest First" },
        { "Artists: Artist", "Artists: Album Artist" },
        { "Resume on Connect: Off", "Resume on Connect: On" },
        { "Artist's Albums: Year", "Artist's Albums: A–Z" },
        { "Hold Return: Recent", "Hold Return: Home" },
        { "Standby Limit: Off", "Standby Limit: " STR(STANDBY_LIMIT_1) "h",
          "Standby Limit: " STR(STANDBY_LIMIT_2) "h", "Standby Limit: " STR(STANDBY_LIMIT_3) "h" },
        { 0 }, /* Link Volume: a switch */
        { 0 }, /* Coverflow Sort's from coverflow.c */
        { "Refresh Cover Flow" },
        { 0 }, /* EQ per Output: a switch */
        { 0 }, /* Key Tone: the Clicker's (clicker.c) */
        { 0 }, /* In-Vehicle Mode: a switch */
        { 0 }, /* Smart Wi-Fi: a switch */
        { 0 }, /* Enlarged Album Art: a switch */
        { 0 }, /* Folder Album Cover: a switch */
        { 0 }, /* Fast Navigation: a switch */
        { "Playlist View: List", "Playlist View: Cover Flow" },
        { 0 }, /* Scan Song Durations: a switch */
        { 0 }, /* Position Bar: a switch */
        { 0 }, /* On-The-Go: a switch */
        { 0 }, /* Automatic Time Zone: a switch */
    };
    _Static_assert(sizeof names / sizeof *names == POD_COUNT, "a names entry per row");
    if (i == POD_KEYTONE) { /* the Clicker, Key Tone's row */
        row_value_text(st.pod_label[i], clicker_text()); /* "Clicker" left, the value right */
        return;
    }
    if (i == POD_CF_SORT) { /* key/value on a set with a right column */
        row_value_text(st.pod_label[i], coverflow_sort_name());
        return;
    }
    int v = i == POD_ARTISTS       ? I(artist_type, 0) == 1
            : i == POD_CARMODE     ? g_carmode != 0
            : i == POD_COVERMODE   ? g_bigcovermode == 1
            : i == POD_FOLDERCOVER ? g_foldercover_flag != 0
            : i == POD_BT_LINK     ? bt_link() /* never chosen: what applies now */
            : pod_values[i]        ? *pod_values[i]
                                   : 0;
    /* a set_switch theme's Charge Limit is a key/value row, Off or its level, as names[] has it */
    const char *toggle = i == POD_CHARGE && theme()->set_switch ? 0
                         : i == POD_CHARGE         ? "Charge Limit (" STR(CHARGE_STOP) "%)"
                         : i == POD_RESUME         ? "Resume on Connect"
                         : i == POD_BT_LINK        ? "Link Volume"
                         : i == POD_CARMODE        ? "In-Vehicle Mode"
                         : i == POD_SMART_WIFI     ? "Smart Wi-Fi"
                         : i == POD_COVERMODE      ? "Enlarged Album Art"
                         : i == POD_FOLDERCOVER    ? "Folder Album Cover"
                         : i == POD_BIG_LIB        ? "Fast Navigation"
                         : i == POD_SCAN_DURATIONS ? "Scan Song Durations"
                         : i == POD_CF_POSITION    ? "Position Bar"
                         : i == POD_OTG            ? "On-The-Go"
                         : i == POD_AUTOZONE       ? "Automatic Time Zone"
                                                   : 0;
    if (toggle)
        list_toggle(st.pod_label[i], toggle, v);
    else
        row_value_text(st.pod_label[i], names[i][v]);
    /* Stock's rows, so stock's translations */
    if (i == POD_CARMODE) widget_set_tr_text(st.pod_label[i], "system_carmode");
    if (i == POD_COVERMODE) widget_set_tr_text(st.pod_label[i], "playset_covermode");
    if (i == POD_FOLDERCOVER) widget_set_tr_text(st.pod_label[i], "playset_foldercover");
}
void pod_text_again(int i) {
    if (st.pod_label[i]) pod_text(i);
}
/* Centre or tap toggles and saves. Charge limit takes effect on the next UI loop pass
 * (power_poll), Smart Wi-Fi within SMART_POLL_MS; Artists on the next load of an artist list, as
 * the artist page's switch does; Fast Navigation's letters at the next spin, its half copies at
 * Cover Flow's next idle pass; Standby limit at the next Standby; Refresh Cover Flow at Cover
 * Flow's next open (coverflow.c), with a toast, as it changes nothing in view. Cover Flow's Sort
 * opens its picker. */
static int pod_click(void *ctx, void *event) {
    int i = (int)(long)ctx;
    pod_settings();
    if (i == POD_CF_SORT) {
        cf_sort_open(); /* its pick writes the row */
        return 0;
    } else if (i == POD_CF_REFRESH) {
        coverflow_refresh_art();
        toast("Cover Flow rebuilds artwork next time");
    } else if (i == POD_ARTISTS) {
        int v = I(artist_type, 0) != 1;
        I(artist_type, 0) = v;
        write_int_config(v, "PLAYSET", "ARTISTTYPE");
    } else if (i == POD_KEYTONE) {
        clicker_step(); /* the Clicker's next value; Off is Key Tone off */
    } else if (i == POD_CARMODE) {
        g_carmode = !g_carmode;
        write_int_config(g_carmode, "SYSSET", "CARMODE");
        g_carmode_state = 0;         /* as stock's row (0x4d3530) */
    } else if (i == POD_COVERMODE) { /* these two as stock's rows (0x4c0714) */
        g_bigcovermode = g_bigcovermode != 1;
        write_int_config(g_bigcovermode, "PLAYSET", "COVERMODE");
    } else if (i == POD_FOLDERCOVER) {
        g_foldercover_flag = !g_foldercover_flag;
        write_int_config(g_foldercover_flag, "PLAYSET", "FOLDERCOVER");
    } else {
        int *value = pod_values[i];
        *value = i == POD_BT_LINK ? !bt_link() : (*value + 1) % pod_choices(i);
        write_int_config(*value, "Q2POD", pod_keys[i]);
        if (i == POD_SCAN_DURATIONS && !*value) durations_cancel();
        if (i == POD_CF_POSITION) coverflow_position_reset();
        if (i == POD_AUTOZONE && *value) autozone_due(); /* looked up at once, link allowing */
        st.charge_at = 0;
        /* Link Volume: off, stock's gain back at once; on, bt_volume_poll's next pass
         * sends the Q2's level to the device. */
        if (i == POD_BT_LINK && mclGetOutputWay() == 1)
            headset_volume_reset(), device_set_volume(g_volume, 0);
    }
    pod_text(i);
    return 0;
}
void pod_rows(void *win, const char *view_name, int first, int n, const char *const *icons) {
    void *view = win ? widget_lookup(win, view_name, 1) : (void *)0;
    pod_settings();
    for (int i = 0; view && i < n; ++i) {
        st.pod_label[first + i] = (first + i == POD_CF_SORT ? list_row_into : list_row)(
            view, icons[i], pod_click, (void *)(long)(first + i));
        pod_text(first + i);
        caption_row(st.pod_label[first + i], pod_caption(first + i)); /* settings_pages.c */
    }
}

/* Row i (a switch) into view as its child at: Automatic Time Zone under the time zone
 * page's Automatic Time Set switch (navigation.c ringnav_timezone_row). */
void pod_row_at(void *view, int i, const char *icon, unsigned at) {
    pod_settings();
    st.pod_label[i] = list_row(view, icon, pod_click, (void *)(long)i);
    pod_text(i);
    caption_row(st.pod_label[i], pod_caption(i));
    unsigned n = widget_count_children(view);
    if (n && at < n) widget_restack(widget_get_child(view, n - 1), at);
}

/* systemset_powermanager_page_init and playset_playset_page_init: stock builds its rows, then
 * these follow in the same widgets and styles (list_row), with the value in the label. */
static void boot_row(void *view);
int ringnav_powermanager(void *win, void *ctx) {
    static const char *const icons[] = { "usb_chargeswitch", "system_powermanager",
                                         "system_carmode", "wifiset_wifi" };
    int result = stock_power_trampoline(win, ctx);
    pod_rows(win, "scroll_view_powermanager", POD_STANDBY, 1,
             icons + 1); /* beside stock's Standby */
    pod_rows(win, "scroll_view_powermanager", POD_CHARGE, 1, icons);
    void *view = win ? widget_lookup(win, "scroll_view_powermanager", 1) : (void *)0;
    usb_charge_row(win, view);
    pod_rows(win, "scroll_view_powermanager", POD_SMART_WIFI, 1, icons + 3);
    /* smart_wifi.c's SMART_IDLE_MS; a caption shows under the selected row (settings_pages.c) */
    if (view)
        caption_row(st.pod_label[POD_SMART_WIFI],
                    "Turns Wi-Fi off after 5 minutes when nothing needs it. "
                    "AirPlay and Car Remote keep it on.");
    pod_rows(win, "scroll_view_powermanager", POD_CARMODE, 1, icons + 2);
    if (view) /* battery.c's page */
        widget_set_text_utf8(list_row_into(view, "system_powermanager", battery_history, 0),
                             "Battery History");
    boot_row(view);
    return result;
}
/* The Bluetooth page's list (stock's local builder, btmenu_rows), rebuilt whole on every search and
 * link change: Resume on Connect and Link Volume, pod_rows' switches, with Bluetooth on, then
 * bt_ui.c's order and look (docs/oled-design.md#bluetooth). */
int ringnav_btrows(void *win, void *ctx) {
    static const char *const icons[] = { 0 };
    int result = stock_btrows_trampoline(win, ctx);
    void *view = win ? widget_lookup(win, "scroll_view_bluetooth", 1) : (void *)0;
    if (g_bluetoothflag) {
        pod_rows(win, "scroll_view_bluetooth", POD_RESUME, 1, icons);
        pod_rows(win, "scroll_view_bluetooth", POD_BT_LINK, 1, icons);
    }
    if (view) bt_page(view, g_bluetoothflag);
    return result;
}
/* A settings list's rows, stock's (in its table's order, each button named by its place there,
 * which its click looks up, so order is free) and then ours, restacked into order; any other count
 * is left as built. */
static void reorder(void *view, const unsigned char *order, unsigned n) {
    void *rows[32];
    if (!view || n > sizeof rows / sizeof *rows || widget_count_children(view) != n) return;
    for (unsigned i = 0; i < n; i++) rows[i] = widget_get_child(view, i);
    for (unsigned i = 0; i < n; i++) widget_restack(rows[order[i]], i);
}

/* EQ and the Library first, then everyday listening, what plays next, the Sleep Timer, level,
 * channels, then specialist output controls. Stock's rows 0-10 (ringnav_audioset drops its Volume
 * and Album Display and Folder skip, hides its Balance, 10, for the inline one, and Headphones
 * Required, 8, which is always on), then ours as built. */
static const unsigned char playset_order[] = {
    2 /* EQ */,
    21 /* Library */,
    3 /* Play Mode */,
    7 /* Gapless */,
    11 /* Smart Crossfade */,
    6 /* Memory play */,
    12 /* Resume Songs */,
    8 /* Headphones required, hidden */,
    15 /* Sleep Timer */,
    16 /* Volume */,
    17 /* Max Volume */,
    18 /* Default Volume */,
    19 /* its Level */,
    0 /* Gain */,
    9 /* ReplayGain */,
    20 /* Balance */,
    10 /* stock's Balance, hidden */,
    13 /* Mono Audio */,
    14 /* Swap L/R */,
    1 /* Filters */,
    4 /* DSD Output */,
    5 /* USB Audio */,
};
/* General, iOS's: About first, then device maintenance, Network's receivers and Reset. Stock's
 * Language, Wireless, Display, Power, Wheel Function, In-Vehicle, General and System stay hidden,
 * its page left whole: Settings on Home has Wi-Fi, Bluetooth, Display and Battery's rows itself
 * (home_sets_bind), the wheel is Q2 Muse's (tools/build.py WHEEL_MODE), Battery and Controls have
 * In-Vehicle's, Key tone's and Buttons lock's rows (pod_click, keylock_open), and the rows stock
 * moved to System are here, as they were (ringnav_systemset). */
#define SYSSET_RESET 12 /* Reset's index as built; power_rows moves it past Shut Down */
static const unsigned char sysset_order[] = {
    13 /* About */,
    11 /* Update */,
    14 /* Storage */,
    15 /* USB */,
    10 /* Date and time */,
    9 /* App Store */,
    2 /* Network */,
    12 /* Reset */,
    0 /* Language, hidden */,
    1 /* Wireless, hidden */,
    3 /* Display, hidden */,
    4 /* Power, hidden */,
    5 /* Wheel Function, hidden */,
    6 /* In-Vehicle, hidden */,
    7 /* General, hidden */,
    8 /* System, hidden */,
};

/* Folder Root (Q2POD FOLDERROOT): the card's top-level folder Home's Folder opens in, "" the card
 * itself; name holds 256. toolsReadConfig leaves name alone for a missing key and empty default.
 * ponytail: names over ROOT_MAX bytes or with an edge space are not offered: stock's config.ini
 * line (toolsWriteConfig's 257-byte sprintf, toolsReadConfig's 255-byte fgets, then trimmed) can't
 * keep them. */
#define ROOT_MAX 200
static void root_read(char *name) {
    name[0] = 0;
    toolsReadConfig("/mnt/data/config.ini", "Q2POD", "FOLDERROOT", name, "");
}
/* The card's next top-level folder after name, in name order (case aside, as FAT and exFAT hold
 * no two names differing only so; no dot-folders): the first after "", "" (the card) after the
 * last. d_type 0: as media_find. */
static void root_next(char *name) {
    char next[256] = "";
    void *dir = opendir("/mnt/mmc");
    for (struct dirent *e; dir && (e = readdir(dir));) {
        const char *d = e->d_name;
        unsigned n = strlen(d);
        if ((e->d_type != 4 && e->d_type) || d[0] == '.' || d[0] == ' ' || d[n - 1] == ' ' ||
            n > ROOT_MAX)
            continue;
        if (strcasecmp(d, name) > 0 && (!next[0] || strcasecmp(d, next) < 0))
            memcpy(next, d, n + 1);
    }
    if (dir) closedir(dir);
    memcpy(name, next, 256);
}
/* The saved root's folder into path (265 bytes), 0 when there is none to open. */
static int root_dir(char *path) {
    char name[256];
    unsigned info[36];
    root_read(name);
    tk_snprintf(path, 265, "/mnt/mmc/%s", name);
    return name[0] && name[0] != '.' && !strrchr(name, '/') && !__xstat(3, path, info) &&
           (info[5] & 0170000) == 0040000;
}
static void root_text(const char *name) {
    char t[280];
    tk_snprintf(t, sizeof t, "Folder Root: %s", name[0] ? name : "Card");
    row_value_text(st.root_label, t);
}
/* Centre or tap: the next root, saved; Folder opens there from its next visit. */
static int root_click(void *ctx, void *event) {
    char name[256];
    root_read(name);
    root_next(name);
    toolsWriteConfig("/mnt/data/config.ini", "Q2POD", "FOLDERROOT", name);
    root_text(name);
    return 0;
}
/* Playback > Library > Folders' Folder Root row (library_settings.c) */
void *folder_root_row(void *view) {
    char name[256];
    root_read(name);
    st.root_label = list_row(view, "playset_folderjump", root_click, 0);
    root_text(name);
    return st.root_label;
}
/* a 12 px space between rows, which the wheel passes (an empty label, as power_rows' gap) */
void settings_gap(void *view) { widget_set_text_utf8(label_create(view, 0, 0, 0, 12), ""); }

/* A row that opens the stock page ctx names, as stock's own rows do. */
static int to_page(void *ctx, void *event) {
    navigator_to((const char *)ctx);
    return 0;
}
/* Playback > Library and its pages (library_settings.c) open stock's Playback page again (as
 * Display's sub-pages do), playset_sub naming the page while its init runs, with the page's rows
 * instead of stock's: Folders keeps stock's Folder skip (12), the others none. Playback itself
 * drops stock's Volume and Album Display (11) and Folder skip; Album Display's rows are elsewhere,
 * Max Volume and Default Volume here, Enlarged Album Art on Display and Folder Album Cover on
 * Library > Folders, flipping stock's switches (pod_click). Max Volume, Balance and Crossfade's and
 * Default Volume's values are inline sliders, set in the list (inline_slider.c); stock's Balance
 * row stays, hidden. Playback's Volume row, before Max Volume, opens the slider page on the volume
 * (crossfade.c xfade_open 4). Each page builds only its own rows, so a row's kept label is its
 * page's. */
static int playset_sub __attribute__((section(".scratch")));
int library_open(void *ctx, void *event) {
    playset_sub = (int)(long)ctx;
    navigator_to("playset/playset_page");
    playset_sub = 0;
    return 0;
}
int ringnav_audioset(void *win, void *ctx) {
    int sub = playset_sub, result = stock_audioset_trampoline(win, ctx);
    void *view = win ? widget_lookup(win, "scroll_view_playset", 1) : (void *)0;
    if (!view) return result;
    for (int i = (int)widget_count_children(view); i-- > 0;)
        if (sub ? sub != LIB_FOLDERS || i != 12 : i >= 11)
            widget_destroy(widget_get_child(view, (unsigned)i));
    if (sub) {
        static const char *const titles[] = { 0, "Library", "Sorting", "Playlists", "Folders" };
        if (sub == LIB_FOLDERS) { /* Folder skip's caption: its button's first child stands in for
                                   * list_row's label */
            void *skip = widget_get_child(view, 0), *button = skip ? widget_get_child(skip, 0) : 0;
            caption_row(button ? widget_get_child(button, 0) : (void *)0, folder_skip_caption);
        }
        void *nav = widget_lookup(win, "view_navbar", 1),
             *title = nav ? widget_get_child(nav, 0) : (void *)0;
        if (title) widget_set_text_utf8(title, titles[sub]);
        widget_set_prop_int(win, SUB_PAGE, sub);
        library_page(win, view, sub);
        return result;
    }
    xfade_row(list_row(view, "playset_gapless", xfade_click, 0)); /* Smart Crossfade, crossfade.c */
    resume_row(list_row_into(view, "playset_memplay", xfade_open, (void *)2)); /* crossfade.c */
    mono_row(list_row(view, "playset_balance", mono_click, 0));
    swap_row(list_row(view, "playset_balance", swap_click, 0));
    /* stock's timer page, which Control Center's tile opens too (ringnav_sleeptimer) */
    widget_set_text_utf8(
        list_row_into(view, "system_powermanager", to_page, (void *)"systemset/sleepshutdown_page"),
        "Sleep Timer");
    volume_row(list_row_into(view, "playset_maxvol", xfade_open, (void *)4)); /* crossfade.c */
    inline_slider_row(view, "playset_maxvol", IS_MAXVOL);
    inline_pair(view, "playset_bootvol", IS_BOOTVOL); /* Default Volume and its Level */
    inline_slider_row(view, "playset_balance", IS_BALANCE);
    /* stock's Balance, which opens its page */
    widget_set_visible(widget_get_child(view, 10), 0, 0);
    /* Headphones Required, held on (output_rule.c) */
    widget_set_visible(widget_get_child(view, 8), 0, 0);
    widget_set_text_utf8(
        list_row_into(view, "playset_folderjump", library_open, (void *)LIB_LIBRARY), "Library");
    reorder(view, playset_order, sizeof playset_order);
    return result;
}

/* Nearest collected ancestor of a tap: a clickable child selects the row that owns it. */
static int selects(menu_t *m, void *target) {
    for (int depth = 0; target && depth < 32; ++depth) {
        for (int i = 0; i < m->n; ++i)
            if (m->at[i] == target) return i;
        if (target == m->w) break;
        target = P(target, W_PARENT);
    }
    return -1;
}

/* target's row takes the selection, painted: a tap's, or Theme's after its relayout. */
static void keep_selected(void *target) {
    void *other = (void *)0, *w = surface(target, &other);
    int i = w && load(&g_menu, w, 0) ? selects(&g_menu, target) : -1;
    if (i < 0) return;
    stop_scroll(&g_menu); /* an explicit tap replaces any pending recall glide */
    if (other) {
        prop(other, SEL, -1);
        widget_invalidate_force(other, (void *)0);
    }
    select(&g_menu, g_menu.id[i]);
    widget_invalidate_force(w, (void *)0);
}

