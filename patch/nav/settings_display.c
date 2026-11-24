static void setting_text(int i) {
    static const char *const home[] = { "Home: Split", "Home: Full" },
                             *const battery[] = { "Battery: Icon", "Battery: Percent",
                                                  "Battery: Icon + Percent",
                                                  "Battery: Percent Inside" },
                             *const light[] = { "Light Mode: Off", "Light Mode: On",
                                                "Light Mode: Auto" };
    int a = accent();
    const scheme_t *S = scheme();
    const char *const names[] = { S->label ? (const char *)0 : accent_names[a], home[st.home],
                                  battery[st.battery], light[st.light] };
    char text[64];
    if (i == 0 && S->label) {
        tk_snprintf(text, sizeof text, "Accent: %s", S->label);
        row_label(st.setting_label[0], text);
        return;
    }
    if (i >= SETTING_OWN) {
        const theme_key_t *K = &theme_keys[i - SETTING_OWN];
        if (theme()->set_text) { /* the label and the value's name, unsplit */
            list_value(st.setting_label[i], K->label, K->names[st.own[i - SETTING_OWN]]);
            return;
        }
        tk_snprintf(text, sizeof text, "%s: %s", K->label, K->names[st.own[i - SETTING_OWN]]);
        row_label(st.setting_label[i], text);
        return;
    }
    static const char *const toggles[] = { "Smart Awake", 0 /* the Clicker's, no row */,
                                           "Reduce Motion" };
    if (i >= SETTING_KEEPLIT) {
        list_toggle(st.setting_label[i], toggles[i - SETTING_KEEPLIT], *setting_values[i]);
        return;
    }
    row_label(st.setting_label[i], names[i]);
}

static int settings_list(void *w) {
    return w && !tk_strcmp(widget_get_type(w), "list_view") && !I(w, ROW_HEIGHT) &&
           I(w, LIST_DEFAULT_ITEM_HEIGHT) == geom_val(V_SET_ROW);
}

/* Cached pages behind Display must take the new inset before their return transition. The set's
 * set_top counts from the status row's foot: in a window, from its bar_foot. */
static void settings_layout(void *w) {
    if (!w) return;
    if (settings_list(w)) {
        void *p = P(w, W_PARENT);
        int y = theme()->set_top + (p && P(p, W_PARENT) == window_manager() ? bar_foot(p) : 0);
        if (I(w, W_Y) != y) widget_move_resize(w, I(w, W_X), y, I(w, W_W), I(w, W_H));
        return;
    }
    for (unsigned i = 0, n = widget_count_children(w); i < n; ++i)
        settings_layout(widget_get_child(w, i));
}

/* Centre or tap cycles the row's value and saves it. A new accent reaches the payload's drawing on
 * the next paint, and the theme's colors and images once every cached image is dropped and the
 * screen repaints; Home takes its new layout and Streaming label at once (never recreated), and an
 * open Now Playing its progress bar. */
static void setting_put(int *value, int v, const char *key) {
    if (*value != v) write_int_config(*value = v, "IPOD", key);
}
/* The theme's preset (its theme.xml's: OLED's dark, Blue, Full), as choosing the theme applied it
 * when there were others: theme_boot's once for a unit an older build left on another (THEME_OLED).
 * Light Mode's Auto and the battery are kept. Only a change is saved. */
static void theme_preset(const theme_t *T) {
    int buf[11];
    st.light_day = light_day(clock_now(buf));
    for (int k = 0; k < T->n_opts; ++k) {
        const option_t *o = &T->opts[k];
        int *v = option_slot(T, o->setting);
        if (o->preset >= 0 && *v != o->keep) setting_put(v, o->preset, option_key(T, o->setting));
    }
}

static int setting_click(void *ctx, void *event) {
    int i = (int)(long)ctx;
    const theme_t *T = theme();
    int *value = option_slot(T, i);
    const char *key = option_key(T, i);
    /* Graphite, ..., Plum, Green, then Ivory (a theme's mask skips what it does not offer) */
    static const int accent_next[] = { 1, 2, 3, 4, 5, 6, 8, 0, MINIMAL_ACCENT };
    _Static_assert(sizeof accent_next / sizeof *accent_next == ACCENT_N, "one per ACCENTS row");
    if (i) {
        const option_t *o = option_of(T, i);
        unsigned mask = o && o->values ? o->values : ~0u;
        do
            *value = (*value + 1) %
                     (i >= SETTING_OWN ? theme_keys[i - SETTING_OWN].n : setting_counts[i]);
        while (!(mask >> *value & 1));
    } else { /* Graphite, ..., Green, Ivory; a theme's own schemes after Green */
        const option_t *o = option_of(T, SETTING_ACCENT);
        unsigned mask = o && o->values ? o->values : (1u << ACCENT_N) - 1;
        do {
            int v = *value >= ACCENT_N ? *value + 1 : accent_next[*value];
            if (!v && T->n_schemes > ACCENT_N && *value < ACCENT_N) v = ACCENT_N;
            *value = v < T->n_schemes ? v : 0;
        } while (!(mask >> *value & 1));
    }
    write_int_config(*value, "IPOD", key);
    if (i == SETTING_LIGHT) { /* Auto applies from this click */
        int buf[11];
        st.light_day = light_day(clock_now(buf));
    }
    if (i == SETTING_BATTERY)
        widget_invalidate_force(*(void *const *)system_bar, (void *)0); /* bar_sync applies it */
    else if (i == SETTING_HOME)
        coverflow_home_layout();
    else if (i == SETTING_ACCENT || i == SETTING_LIGHT) { /* the switches from Smart Awake on and
                                                           * the theme's own options only need
                                                           * their row drawn */
        np_fill(0);
        image_manager_unload_all(image_manager());
        widget_invalidate_force(window_manager(), (void *)0);
    }
    setting_text(i);
    return 0;
}

/* The info page's Genre line: stock's centred label would clip a long value at both ends, and
 * stock's np_info rewrites it every 250 ms, which would reset an animation on it. So it stays
 * hidden as the value source, and a private hscroll_label shows the value, its text set only when
 * that changes. It scrolls MARQUEE_PX every MARQUEE_MS, in front and lit only: covered,
 * dark or off the page the chain ends, and np_info's next fill restarts it. Stock measures text_w
 * on the first paint, so a new label starts moving on a fill after that.
 * ponytail: not in np_animating, so once idle its 50 ms step waits for the loop's slower passes and
 * it runs a little slower than MARQUEE_MS; add it there if that looks bad, at the cost of the
 * loop's fast pace while it moves. */
#define GENRE_SCROLL "ringnav_genre_scroll"
#define MARQUEE_MS 50
#define MARQUEE_PX 2
static int genre_over(void) {
    void *s = st.genre_label, *about = np_about_page();
    if (!s || !about || !g_backlight_status ||
        window_manager_get_top_window(window_manager()) != about)
        return 0;
    return I(s, HSCROLL_TEXT_W) + 4 - I(s, W_W); /* stock's two 2 px margins, as marq_moved */
}
static int genre_step(const void *info) {
    (void)info;
    st.genre_timer = 0;
    int over = genre_over();
    if (over <= 0) return 0;
    int end = st.genre_off >= over;
    st.genre_off = end ? 0 : st.genre_off + MARQUEE_PX > over ? over : st.genre_off + MARQUEE_PX;
    hscroll_label_set_xoffset(st.genre_label, st.genre_off);
    st.genre_timer = timer_add(genre_step, (void *)0,
                               end || st.genre_off >= over ? MARQUEE_HOLD_MS : MARQUEE_MS);
    return 0;
}
static void genre_fill(void *win, void *label, const char *text) {
    void *s = widget_lookup(win, GENRE_SCROLL, 1);
    if (!s) {
        s = hscroll_label_create(P(label, W_PARENT), I(label, W_X), I(label, W_Y), I(label, W_W),
                                 I(label, W_H));
        if (!s) return;
        widget_set_name(s, GENRE_SCROLL);
        widget_use_style(s, "s_scrlabel_white16c");
        hscroll_label_set_only_focus(s, 1); /* no stock timer: genre_step moves it */
        widget_set_visible(label, 0, 0);
    }
    char old[1024 + 64 + 8]; /* ringnav_np_info's out */
    int same = !widget_get_text_utf8(s, old, sizeof old) && !strcmp(old, text);
    if (!same) {
        widget_set_text_utf8(s, text);
        st.genre_off = 0;
        hscroll_label_set_xoffset(s, 0);
    }
    if (win != np_about_page()) return;
    st.genre_label = s;
    if (!same) stop_timer(&st.genre_timer); /* the new text holds at its start */
    if (!st.genre_timer && genre_over() > 0)
        st.genre_timer = timer_add(genre_step, (void *)0, MARQUEE_HOLD_MS);
}
/* Now Playing's info page: stock (0x53bc78, from its one call, NP_INFO_CALL in tools/build.py)
 * writes "Label:value" lines and the folder alone. Each line gets ": ", a number its unit's space
 * ("35.92 MB", "16-bit", "950 kbps", "44.1 kHz"), the folder its Location label, and an unknown
 * value, en_US's first player_unknown ("Unknown Artist"), reads "Unknown". A page with
 * rows (#204's): stock's label keeps the name and its <name>_value the
 * value, in the info role's face, the value in the theme's grey (written through the flip, its own
 * inverse on the half's paper and ink), the genre with them, not genre_fill's marquee. OLED's
 * hidden details have them too, the source of About's grid (np_about.c np_about_fill, which this
 * fill ends with), their English names "Format", "Bit Rate" and "Size" for stock's "Encoding",
 * "Bitrate" and "File Size". */
int ringnav_np_info(void *win) {
    static const char *const names[] = { "label_album", "label_genre",  "label_size",
                                         "label_year",  "label_encode", "label_bits",
                                         "label_kbps",  "label_sample", "label_location" },
                             *const units[] = {
                                 "", "", " MB", "", "", "-bit", " kbps", " kHz", ""
                             };
    if (win && win == st.np_win && np_about_page())
        win = np_about_page(); /* About holds the labels */
    int result = np_info(win), rows = win && widget_lookup(win, "label_album_value", 1);
    /* OLED's details (tools/ipod.py np_info_values), named as About's grid names them */
    static const char *const stock_names[] = { "File Size", "Encoding", "Bitrate" },
                             *const oled_names[] = { "Size", "Format", "Bit Rate" };
    int oled = rows;
    char where[64] = "";
    getFormatString("player_location", where, sizeof where);
    const char *loc =
        *where && *where != '(' ? where : "Location"; /* a missing string reads "(key)" */
    /* a server song (catalogue.c): its genre and year are the catalogue's, its place the server */
    char genre[96], year[12];
    int y = 0, server = catalogue_np_info((const char *)g_play_id3_info, genre, &y);
    tk_snprintf(year, sizeof year, "%d", y);
    for (unsigned i = 0; i < sizeof names / sizeof *names; ++i) {
        void *label = win ? widget_lookup(win, names[i], 1) : (void *)0;
        char s[1024], out[1024 + sizeof where + 8];
        if (!label || widget_get_text_utf8(label, s, sizeof s) || !*s) continue;
        const char *name = s, *v = s, *colon = s;
        while (*colon && *colon != ':') ++colon;
        int n = (int)(colon - s);
        if (i == 8 && !tk_str_start_with(s, loc)) /* the bare folder */
            name = loc, n = (int)strlen(loc);
        else if (*colon)
            v = colon + 1;
        else
            continue;
        while (*v == ' ') ++v;
        if (server && i == 1 && *genre) v = genre;
        if (server && i == 3 && y > 0) v = year;
        if (server && i == 8) v = "Server";
        if (!strcmp(v, "Unknown Artist")) v = "Unknown";
        const char *unit = *units[i] ? units[i] + 1 : units[i];
        int len = (int)strlen(v), u = (int)strlen(unit);
        int split = u && len > u && !strcmp(v + len - u, unit) && v[len - u - 1] >= '0' &&
                    v[len - u - 1] <= '9';
        if (rows) {
            char key[24];
            tk_snprintf(key, sizeof key, "%s_value", names[i]);
            void *value = widget_lookup(win, key, 1);
            tk_snprintf(out, sizeof out, "%.*s", n, name);
            int k = i == 2 ? 0 : i == 4 ? 1 : i == 6 ? 2 : -1;
            if (oled && k >= 0 && !strcmp(out, stock_names[k]))
                tk_snprintf(out, sizeof out, "%s", oled_names[k]);
            if (strcmp(out, s)) widget_set_text_utf8(label, out);
            theme_font(label, ROLE_INFO, FONT_ROW);
            if (!value) continue;
            tk_snprintf(out, sizeof out, "%.*s%s", split ? len - u : len, v, split ? units[i] : "");
            if (widget_get_text_utf8(value, s, sizeof s) || strcmp(s, out))
                widget_set_text_utf8(value, out);
            theme_font(value, ROLE_INFO, FONT_ROW);
            int ink = (int)ui_ink(RGBA(theme()->np_info_value[ipod_light()]));
            if (widget_get_prop_int(value, "_ink", 0) != ink) {
                widget_set_prop_int(value, "_ink", ink);
                widget_set_prop_int(value, "style:normal:text_color", ink);
            }
            continue;
        }
        tk_snprintf(out, sizeof out, "%.*s: %.*s%s", n, name, split ? len - u : len, v,
                    split ? units[i] : "");
        if (i == 1) {
            genre_fill(win, label, out);
            continue;
        }
        if (strcmp(out, s)) widget_set_text_utf8(label, out);
    }
    np_about_fill(win);
    return result;
}

/* The Display sub-page the next display_page is to be (DISPLAY_*), 0 for Display itself. */
enum { DISPLAY_APPEARANCE = 1, DISPLAY_COVERFLOW, DISPLAY_CONTROLS };
static int keylock_open(void *ctx, void *event) {
    navigator_to("systemset/keylock_page");
    return 0;
}
static int display_sub __attribute__((section(".scratch")));
static int display_open(void *ctx, void *event) {
    display_sub = (int)(long)ctx;
    navigator_to("systemset/display_page");
    display_sub = 0;
    return 0;
}

static void setting_row(void *view, int i) {
    static const char *const icons[SETTING_OWN] = {
        [SETTING_ACCENT] = "system_display",       [SETTING_HOME] = "playset_covermode",
        [SETTING_BATTERY] = "system_powermanager", [SETTING_LIGHT] = "system_display",
        [SETTING_KEEPLIT] = "system_display",      [SETTING_CLICKER] = "system_keytone",
        [SETTING_REDUCEMOTION] = "system_display"
    };
    const char *icon = i < SETTING_OWN ? icons[i] : "system_display";
    st.setting_label[i] = list_row(view, icon, setting_click, (void *)(long)i);
    setting_text(i);
    if (i == SETTING_KEEPLIT) caption_row(st.setting_label[i], smart_awake_caption);
    if (i == SETTING_REDUCEMOTION) caption_row(st.setting_label[i], reduce_motion_caption);
}

/* systemset_display_page_init: stock builds its four rows (0x4c6b94: a s_listitem_black list_item
 * holding a 335x70 s_btn_listitem button with a 52px icon, a 24px label at x 72 and list_into).
 * Its fourth, Show Navigation Bar, goes: the themes own the bar and each list's box, so stock's
 * switch is held on (tools/build.py NAV_SHOWN). Its third, Lock Screen, goes too: Q2 Muse has no
 * lock screen (docs/internals.md#screen-off-and-wake). Its first, Brightness, is hidden for an
 * inline slider row in its place (inline_slider.c). Smart Awake and Night Shift (the brightness
 * icon, opening its slider page, crossfade.c) follow Brightness and Screen Off Time, then
 * Appearance and Coverflow, each opening stock's page again (display_open) with its stock rows
 * dropped and its own instead: the theme's options (OLED's: Light Mode, Accent, Home,
 * Battery, Song Artist, Song Length, Track Time); Artist View, Coverflow Sort and Refresh
 * Coverflow. Controls is System settings' row (ringnav_systemset) on the same page: the wheel, the
 * buttons and what the buttons do. The value rows show it in the label and have no chevron, since
 * they change in place. ponytail: stock's page opened over itself; its own rows' state is never
 * touched on a sub-page. */
int ringnav_display(void *win, void *ctx) {
    static const char *const titles[] = { 0, "Appearance", "Cover Flow", "Controls" },
                             *const sub_icons[] = { 0, "system_display", "playset_covermode",
                                                    "playset_folderjump" };
    static const char *const cf_icons[] = { "playset_covermode", "playset_covermode",
                                            "system_update", "playset_covermode" };
    static const char *const hold_icon[] = { "playset_folderjump" },
                             *const tone_icon[] = { "system_keytone" },
                             *const lock_icon[] = { "system_keylock" };
    int sub = display_sub, result = stock_display_trampoline(win, ctx);
    void *view = win ? widget_lookup(win, "scroll_view_display", 1) : (void *)0;
    if (!view) return result;
    if (!sub) {
        widget_destroy(widget_get_child(view, 3)); /* Show Navigation Bar */
        widget_destroy(widget_get_child(view, 2)); /* Lock Screen */
        /* Brightness, set in the list (inline_slider.c), first; stock's row, which opens its page,
         * stays hidden */
        widget_set_visible(widget_get_child(view, 0), 0, 0);
        void *bright = inline_slider_row(view, "display_backlight", IS_BRIGHT);
        widget_restack(P(P(bright, W_PARENT), W_PARENT), 0);
        setting_row(view, SETTING_KEEPLIT);
        night_row(list_row_into(view, "display_backlight", xfade_open, (void *)3));
        for (int i = DISPLAY_APPEARANCE; i <= DISPLAY_COVERFLOW; ++i)
            widget_set_text_utf8(list_row_into(view, sub_icons[i], display_open, (void *)(long)i),
                                 titles[i]);
        pod_rows(win, "scroll_view_display", POD_COVERMODE, 1, cf_icons); /* Now Playing's art */
        setting_row(view, SETTING_REDUCEMOTION); /* last (docs/oled-design.md#motion) */
        return result;
    }
    widget_destroy_children(view);
    void *nav = widget_lookup(win, "view_navbar", 1), *title = nav ? widget_get_child(nav, 0) : 0;
    if (title) widget_set_text_utf8(title, titles[sub]);
    widget_set_prop_int(win, SUB_PAGE, 0x10 + sub); /* apart from Library's pages */
    if (sub == DISPLAY_COVERFLOW) { /* what changes Cover Flow alone; the Library's are Library's */
        pod_rows(win, "scroll_view_display", POD_CF_SORT, 1, cf_icons);
        pod_rows(win, "scroll_view_display", POD_BIG_LIB, 1, cf_icons + 3);
        pod_rows(win, "scroll_view_display", POD_CF_POSITION, 1, cf_icons + 3);
        settings_gap(view);
        pod_rows(win, "scroll_view_display", POD_CF_REFRESH, 1, cf_icons + 2);
        return result;
    }
    if (sub == DISPLAY_APPEARANCE) {
        /* the theme's options in order */
        const theme_t *T = theme();
        for (int k = 0; k < T->n_opts; ++k) setting_row(view, T->opts[k].setting);
        return result;
    }
    wheel_row(list_row_into(view, "system_display", xfade_open, (void *)1));
    pod_rows(win, "scroll_view_display", POD_HOLD_HOME, 1, hold_icon);
    pod_rows(win, "scroll_view_display", POD_KEYTONE, 1, tone_icon); /* the Clicker */
    widget_set_tr_text(list_row_into(view, "system_keylock", keylock_open, 0), "system_keylock");
    pod_rows(win, "scroll_view_display", POD_WAKE, 1, lock_icon);
    return result;
}

/* Settings' rows on Home that are not stock's (tools/ipod.py HOME_SETS; stock binds General and
 * Playback): Wi-Fi's and Bluetooth's pages, which Wireless held, Car Remote, Display, Controls and
 * Battery, which General (stock's System page, ringnav_systemset) held. Here, as display_open is.
 */
static int set_open(void *ctx, void *event) {
    int i = (int)(long)ctx;
    if (i == 2) return carremote_open(0, event);
    if (i == 4) return display_open((void *)DISPLAY_CONTROLS, event);
    static const char *const pages[] = {
        "systemset/wifi_page",        "systemset/bluetooth_page", 0, "systemset/display_page", 0,
        "systemset/powermanager_page"
    };
    navigator_to(pages[i]);
    return 0;
}
void home_sets_bind(void *win) {
    static const char *const names[] = { "img_set_wifi",    "img_set_bt",       "img_set_car",
                                         "img_set_display", "img_set_controls", "img_set_power" };
    for (unsigned i = 0; i < sizeof names / sizeof *names; ++i)
        widget_on(widget_lookup(win, names[i], 1), EVT_CLICK, set_open, (void *)(long)i);
}

#define BOOT_TARGET "/mnt/data/boot-target"
static int boot_rockbox __attribute__((section(".scratch")));
static void boot_text(void *label) {
    row_value_text(label, boot_rockbox ? "Boot to: Rockbox" : "Boot to: Q2 Muse");
}
static int boot_click(void *label, void *event) {
    if (!rockbox_available()) {
        toast("Rockbox is not on the card");
        return 0;
    }
    const char *value = boot_rockbox ? "q2pod" : "rockbox";
    if (!blob_io(BOOT_TARGET, BOOT_TARGET ".tmp", (void *)value, strlen(value), 1)) {
        unlink(BOOT_TARGET ".tmp");
        toast("Could not save boot choice");
        return 0;
    }
    boot_rockbox = !boot_rockbox;
    boot_text(label);
    return 0;
}
static void boot_row(void *view) {
    if (!view || !rockbox_available()) return;
    char value[16];
    void *f = fopen(BOOT_TARGET, "rb");
    boot_rockbox = 1;
    if (f) {
        unsigned n = fread(value, 1, 5, f);
        int q2pod = n == 5 && !memcmp(value, "q2pod", 5);
        /* Shell command substitution strips all trailing newlines. */
        while (q2pod && (n = fread(value, 1, sizeof value, f)))
            for (unsigned i = 0; i < n; ++i)
                if (value[i] != '\n') q2pod = 0;
        boot_rockbox = !q2pod || ferror(f);
        fclose(f);
    }
    void *label = list_row(view, "system_powermanager", 0, 0);
    void *button = P(label, W_PARENT);
    widget_on(button, EVT_CLICK, boot_click, label);
    boot_text(label);
}
int ringnav_systemset(void *win, void *ctx) {
    int result = stock_systemset_trampoline(win, ctx);
    void *view = win ? widget_lookup(win, "scroll_view_sysset", 1) : (void *)0;
    if (view) {
        /* Stock's System page's rows and Date & Time, which V1.34 took off this list */
        static const char *const moved[][2] = { { "system_time", "systemset/datetime_page" },
                                                { "system_update", "systemset/fwupdate_page" },
                                                { "system_reset", "systemset/reset_page" },
                                                { "system_about", "systemset/about_page" } };
        widget_set_text_utf8(list_row_into(view, "system_update", shelf_open, 0), "App Store");
        for (unsigned i = 0; i < 4; ++i) {
            /* Software Update (sw_update.c) checks online and the card from one row */
            void *row = list_row_into(view, moved[i][0], i == 1 ? sw_update_open : to_page,
                                      (void *)moved[i][1]);
            if (i == 1)
                widget_set_text_utf8(row, "Software Update");
            else
                widget_set_tr_text(row, moved[i][0]);
        }
        /* Storage (storage.c): the card's use and a card app's Downloads */
        widget_set_text_utf8(list_row_into(view, "playset_folderjump", storage_open, 0), "Storage");
        /* USB (usb_ui.c): the remembered choice, and its picker */
        usb_ui_row(list_row_into(view, "playset_folderjump", usb_pick_open, 0));
        static const unsigned char hidden[] = { 1, 3, 4, 5, 6, 7, 8 };
        for (unsigned i = 0; i < sizeof hidden; ++i)
            widget_set_visible(widget_get_child(view, hidden[i]), 0, 0);
        /* Language: Q2 Muse's pages and labels are English only, so stock's other languages read
         * half-blank; hidden until they are translated (docs/internals.md, English). */
        widget_set_visible(widget_get_child(view, 0), 0, 0);
        void *nav = widget_lookup(win, "view_navbar", 1),
             *title = nav ? widget_get_child(nav, 0) : (void *)0;
        if (title) widget_set_text_utf8(title, "General");
    }
    /* reorder's own condition: the page as built, else no row is known to be Reset */
    void *reset = view && widget_count_children(view) == sizeof sysset_order
                      ? widget_get_child(view, SYSSET_RESET)
                      : (void *)0;
    reorder(view, sysset_order, sizeof sysset_order);
#if Q2POD_DEV
    /* Developer Options (adb_trust.c): Trusted Computers, the computers adb lets in */
    if (view) widget_set_text_utf8(list_row_into(view, "playset_folderjump", adb_dev_open, 0), "Developer Options");
#endif
    /* Rockbox (docs/boot.md#rockbox-from-home): the hand-over, while the card has it */
    if (view && rockbox_available())
        widget_set_text_utf8(list_row(view, "system_powermanager", rockbox_open, 0), "Rockbox");
    power_rows(view, reset); /* Restart, Shut Down and Reset Settings, last */
    return result;
}

