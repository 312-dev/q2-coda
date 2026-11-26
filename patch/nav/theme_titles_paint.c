/* OLED is the one theme, stored as THEME 3, the fourth of the four there were (docs/ipod.md#theme).
 * A unit an older build left on another (0 Minimal, 1 Classic, 2 Modern, or no key: Minimal)
 * takes OLED's preset once, as choosing OLED did, and keeps 3 from then on. */
#define THEME_OLED 3
static void theme_preset(const theme_t *T);
static void theme_boot(void) {
    if (config_value("IPOD", "THEME", 10, 0) != THEME_OLED) {
        theme_preset(theme());
        write_int_config(THEME_OLED, "IPOD", "THEME");
    }
}

/* The value of group (G_PITCH, G_BODY, ...) that tools/ipod.py baked into stock's immediates. */
int geom(int group) { return geom_words[group]; }

/* An A-B marker's x for point t (stock 0x53b9d8, from its three calls in the markers' placement:
 * AB_X_CALLS in tools/build.py), on the baked bar (the np_bar_x and np_bar_w immediates):
 * a bar laid out elsewhere takes it along, at the same share of it. 0, no point to mark, stays. */
int ringnav_ab_x(int t) {
    int x = ab_x(t), x0 = geom(G_NP_BAR_X), w0 = geom(G_NP_BAR_W);
    if (!st.np_slider || x < x0 || w0 <= 0) return x;
    return I(st.np_slider, W_X) + (x - x0) * I(st.np_slider, W_W) / w0;
}

/* An A-B marker's move (stock's two widget_move calls after its x, whose y is 250: AB_MOVE_CALLS in
 * tools/build.py): centered on the bar's box, where stock's y put it on the old bar's place,
 * which on OLED's page is under the screen's rounded corners. */
int ringnav_ab_move(void *w, int x, int y) {
    if (st.np_slider) y = I(st.np_slider, W_Y) + (I(st.np_slider, W_H) - I(w, W_H)) / 2;
    return widget_move(w, x, y);
}

/* ui_loader_load_widget_with_parent, every window's asset (window_open's); any opener comes
 * through here. */
extern void *stock_ui_load_trampoline(const char *name, void *parent);
/* key: STYLE_KEY bytes. */
const char *style_key(char *key, const char *state, const char *name) {
    tk_snprintf(key, STYLE_KEY, "style:%s:%s", state, name);
    return key;
}
/* The slider's value as a part of w px. */
int slider_part(void *s, int w) {
    int min = widget_get_prop_int(s, "min", 0), max = widget_get_prop_int(s, "max", 100);
    return max > min ? w * (widget_get_prop_int(s, "value", 0) - min) / (max - min) : 0;
}
/* A label's final ink, 0xAARRGGBB in its first n states, stored as what Light Mode's style hook
 * turns into it, so stored for this half. text_inks writes it again when the half changes. */
static const char *const ink_state[] = { "normal",
                                         "pressed",
                                         "over",
                                         "disable",
                                         "focused",
                                         "normal_of_active",
                                         "pressed_of_active",
                                         "over_of_active",
                                         "disable_of_active",
                                         "focused_of_active",
                                         "error",
                                         "empty",
                                         "empty_focus",
                                         "empty_over",
                                         "changed" };
void text_ink(void *w, unsigned color, int n) {
    char key[STYLE_KEY];
    unsigned ink = RGBA(flip_if(ipod_light(), color & 0xffffffu)) & 0xffffffu;
    ink |= color & 0xff000000u;
    widget_set_prop_int(w, "_final", (int)color);
    widget_set_prop_int(w, "_final_n", n);
    for (int i = 0; i < n; ++i)
        widget_set_prop_int(w, style_key(key, ink_state[i], "text_color"), (int)ink);
}
void text_inks(void *w) {
    int n = widget_get_prop_int(w, "_final_n", 0);
    if (n) text_ink(w, (unsigned)widget_get_prop_int(w, "_final", 0), n);
    for (int i = widget_count_children(w) - 1; i >= 0; --i) text_inks(widget_get_child(w, i));
}
/* A page's label: box, face and white, in either half (Light Mode's style hook flips it back). */
void slider_label(void *w, int x, int y, int ww, int h, int px, const char *face) {
    widget_move_resize(w, x, y, ww, h);
    widget_set_prop_str(w, "style:normal:font_name", face);
    widget_set_prop_int(w, "style:normal:font_size", px);
    widget_set_prop_str(w, "style:normal:text_align_h", "center");
    text_ink(w, 0xffffffffu, INK_LABEL);
}
/* Stock's window as it loads, before its init: the slider page's own form (slider_page.c). */
static void slider_open(void *win) {
    static const char *const sliders[] = { "slider_backlight", "slider_maxvol", "slider_balance",
                                           "slider_bootvol" }; /* [SLIDER_*] */
    void *s = (void *)0;
    unsigned kind = 0;
    for (; !s && kind < sizeof sliders / sizeof *sliders; ++kind)
        s = widget_lookup(win, sliders[kind], 1);
    if (!s) return;
    slider_knobless(s); /* in every theme: the page's slider is a plain one */
    if (theme()->set_switch) slider_page_open(win, s, kind - 1); /* OLED's one form */
}
/* A nul-ended string centred in r in face (0: the system font) at px, in a final 0xAARRGGBB, never
 * flipped. Text colour and alignment are restored, the font is not. */
void final_text(void *canvas, const char *face, const unsigned *s, const void *r, unsigned px,
                unsigned color) {
    unsigned n = wide_len(s), raw = st.raw_ink;
    st.raw_ink = 1;
    if (n)
        draw_in_face(canvas, face, s, n, r, px,
                     (RGBA(color & 0xffffffu) & 0xffffffu) | (color & 0xff000000u));
    st.raw_ink = raw;
}

/* USB Storage's page: the computer's eject (SCSI START STOP UNIT, LoEj) closes the
 * card's LUN in the kernel's f_mass_storage, and its configfs file then reads empty. Once it has
 * read open, empty is the eject: stock's line (scrlabel_info, which stock's timer rewrites "USB
 * Connected" every 500 ms, so a label of its style covers it) says OK to Disconnect. No file (the
 * gadget not built yet, or torn down) is no eject. The LUN stays closed until stock rebuilds the
 * gadget, so the poll ends there. ponytail: only a host that sends LoEj counts (macOS, Linux,
 * Explorer's Eject); Windows' Safely Remove Hardware may only stop the device, leaving the page on
 * Connected. The upgrade is telling that apart from a cable still in (the gadget's suspend state,
 * which configfs doesn't show). */
#define USB_LUN "/sys/kernel/config/usb_gadget/storage_demo/functions/mass_storage.msg/lun.0/file"
static int usb_poll(const void *info) {
    void *win = P(info, TIMER_CTX);
    char path[2];
    void *f = fopen(USB_LUN, "rb");
    int n = f ? (int)fread(path, 1, sizeof path, f) : -1;
    if (f) fclose(f);
    if (n > 0) st.usb_lun = 1;
    if (n || !st.usb_lun) return 8; /* RET_REPEAT */
    usb_ui_ejected(win);            /* the mode page says OK to Disconnect */
    return 7;                       /* RET_REMOVE */
}

void *ringnav_ui_load(const char *name, void *parent) {
    /* application_init's first window; accent() makes theme() final */
    if (name && !st.theme_booted && !tk_strcmp(name, "system_bar")) {
        st.theme_booted = 1;
        accent();
        theme_boot();
    }
    int np = name && !tk_strcmp(name, "playing_page");
    void *root = stock_ui_load_trampoline(name, parent);
    if (root && slider_page(root)) slider_open(root); /* no knob; slider_page.c's form */
    if (root) msg_open(root);                         /* the scan page (scan_page.c oled_scan) */
    if (root) usb_ui_mode_load(root);                 /* USB's page as a mode page (usb_ui.c) */
    if (root) bt_recv_load(root);                     /* the Bluetooth receiver's (bt_recv.c) */
    if (root && g_usbmode != 1 && !tk_strcmp(widget_get_prop_str(root, "name", ""), "usbmode_page"))
        st.usb_lun = 0, widget_add_timer(root, usb_poll, 500);
    if (root) album_local_load(root); /* OLED's album page on stock's (library_album.c) */
    if (root) motion_load(root, np);
    return root;
}

/* bigcover_page_init: stock's full-screen art (a hold on Now Playing's art opens it, np_taps.c, as
 * does Enlarged Album Art's countdown; any tap closes it)
 * shows the song name BC_MS, then bc_fade fades it out. Stock's own timer writes the next song's
 * name; ringnav_sleep shows it again when the track changes. Off the art page (lyrics, info)
 * there is no art to enlarge, so ringnav_sleep holds the countdown full there (#57).
 * Closing it after the open flashed it every 10 s (#153). */
int ringnav_bigcover(void *win, void *ctx) {
    int result = stock_bigcover_trampoline(win, ctx);
    if (!win) return result;
    st.bc[0] = widget_lookup(win, "scrlabel_title", 1);
    st.bc[1] = widget_lookup(win, "view_shade", 1);
    st.bc[2] = win;
    st.bc_key = 0, st.bc_shape = 1 << 16 | 1;
    bc_show();
    widget_on(win, EVT_DESTROY, bc_gone, win);
    return result;
}

/* Both native album constructors use 110px square covers, with playing overlays either
 * beside the cover or nested inside it. Keep their local origins together when padding the tile.
 * Every theme insets the cover, so the selection card frames it rather than hiding under it. */
static void album_children(void *w, int nested) {
    int pad = TILE_INSET + TILE_CLEARANCE, size = 110 - 2 * pad;
    for (unsigned i = 0; i < widget_count_children(w); ++i) {
        void *c = widget_get_child(w, i);
        int x = I(c, W_X), y = I(c, W_Y), cw = I(c, W_W), h = I(c, W_H);
        if ((cw == 110 || cw == size) && cw == h) {
            album_children(c, 1);
            x = y = nested ? 0 : pad;
            cw = h = size;
            if (!tk_strcmp(widget_get_type(c), "image") &&
                widget_get_prop_int(c, "draw_type", 0) != IMAGE_DRAW_SCALE_DOWN)
                image_set_draw_type(c, IMAGE_DRAW_SCALE_DOWN);
        } else if (!nested && !tk_strcmp(widget_get_type(c), "hscroll_label")) {
            if (x != pad) y -= TILE_INSET;
            x = pad;
            cw = size;
        } else
            continue;
        if (x != I(c, W_X) || y != I(c, W_Y) || cw != I(c, W_W) || h != I(c, W_H))
            widget_move_resize(c, x, y, cw, h);
    }
}

static void album_tile(void *w) {
    if (!w || I(w, W_W) != 110 || I(w, W_H) != 160 || tk_strcmp(widget_get_type(w), "button"))
        return;
    void *win = window_of(w);
    const char *name = win ? widget_get_prop_str(win, "name", "") : "";
    if (!tk_strcmp(name, "album_page") || !tk_strcmp(name, "artistinfo_page")) album_children(w, 0);
}

/* 1 if name is in names, NUL-separated and ended by an empty name. */
static int named_in(const char *names, const char *name) {
    for (const char *n = names; *n;) {
        if (!tk_strcmp(name, n)) return 1;
        while (*n++) {}
    }
    return 0;
}
/* 1 if an active control's white glyph turns CONFIRM_SURFACE on tone, 0xRRGGBB: perceived
 * brightness over GLYPH_LIGHT_MAX (Graphite's silver). */
static int bright_tone(unsigned tone) {
    return ((tone >> 16) * 299 + (tone >> 8 & 255) * 587 + (tone & 255) * 114) / 1000 >
           GLYPH_LIGHT_MAX;
}
/* The confirm pop-up's prompt (stock's own child, "Shut Down?"): CONFIRM_PROMPT_PX, centred in the
 * space above its rows, or above Delete File's "Also delete source file" (stock's check_button
 * msg_delcheckbutton at y 124, centred 20px), which keeps its stock place and style.
 * ponytail: by position, any other non-button child above CONFIRM_TOP. */
static void confirm_prompt(void *w, void *canvas) {
    if (tk_strcmp(widget_get_prop_str(w, "name", ""), "confirminfo_dialog")) return;
    if (feedback_confirm_dress(w)) return; /* OLED's dialog page */
    void *check = widget_lookup(w, "msg_delcheckbutton", 0);
    int bottom = check ? I(check, W_Y) : CONFIRM_TOP;
    for (unsigned i = 0, n = widget_count_children(w); i < n; ++i) {
        void *c = widget_get_child(w, i);
        if (c == check || !tk_strcmp(widget_get_type(c), "button") ||
            I(c, W_Y) + I(c, W_H) > CONFIRM_TOP ||
            (!I(c, W_X) && !I(c, W_Y) && I(c, W_W) == I(w, W_W)))
            continue;
        widget_move_resize(c, 0, 0, I(w, W_W), bottom);
        widget_set_prop_int(c, "style:normal:font_size", CONFIRM_PROMPT_PX);
        widget_set_prop_str(c, "style:normal:text_align_h", "center");
        widget_set_prop_str(c, "style:normal:text_align_v", "middle");
    }
}
/* A list row's touch target: its own (page rows), else a button over its top (the Library's,
 * Settings', the More menu's); pressed with its grey (ipod.json's pressed #333333) or none. */
static int pressed_row(void *b) {
    const char *s = widget_get_prop_str(b, "style", "");
    return !tk_strcmp(widget_get_prop_str(b, "state_for_style", ""), "pressed") &&
           (!tk_strcmp(s, "s_btn_listitem") || !tk_strcmp(s, "s_btn_forwardspeed") ||
            !tk_strcmp(s, "s_listitem_black"));
}
static void *touch_target(void *row) {
    void *b = widget_count_children(row) ? widget_get_child(row, 0) : (void *)0;
    return b && !tk_strcmp(widget_get_type(b), "button") && !I(b, W_Y) ? b : row;
}
/* A button under half its row's width is a tile (the album grid's, the playlist page's pair), as
 * paint_selection tells a tile from a row. */
static int tile_in(void *b, void *row) { return 2 * I(b, W_W) < I(row, W_W); }
static int press_repaint(const void *info) {
    st.press_timer = 0;
    void *top = window_manager_get_top_window(window_manager());
    if (top) widget_invalidate_force(top, (void *)0);
    return 0;
}

/* A touched list row's grey: the whole row (the Library's button leaves x 0-20, 355-375 and the
 * pitch's last 4 px), square. Drawn as the row's background; the button's own box is skipped (1).
 * The press invalidates the whole surface (ringnav_touch), its release only the button, so the
 * row's edges repaint once after it (press_repaint). A tile's grey is its selection card, inside
 * the tile. */
static int pressed_pill(void *w, void *canvas) {
    if (!w) return 0;
    if (!is_row(w)) {
        void *row = P(w, W_PARENT);
        if (!is_row(row) || !pressed_row(w)) return 0;
        if (!tile_in(w, row)) /* the row drew it: skip its button's box */
            return touch_target(row) == w && I(row, W_W) > 2 * SEL_INSET + I(row, W_H);
        rect_t card = { TILE_INSET, TILE_INSET, I(w, W_W) - 2 * TILE_INSET,
                        I(w, W_H) - 2 * TILE_INSET };
        fill_box(canvas, &card, PH_GREY, 0);
        return 1;
    }
    void *b = touch_target(w);
    /* childless: not a row but a page; a tile draws its own */
    if (!pressed_row(b) || tile_in(b, w) || !widget_count_children(w) ||
        I(w, W_W) <= 2 * SEL_INSET + I(w, W_H)) {
        if (w == st.press_row) {
            st.press_row = (void *)0;
            rearm(&st.press_timer, press_repaint, 0);
        }
        return 0;
    }
    st.press_row = w;
    rect_t pill = { 0, 0, I(w, W_W), I(w, W_H) };
    fill_box(canvas, &pill, PH_GREY, 0);
    return b == w; /* a page row's own (clear) background */
}

/* A payload page's caption, 48 px or page_title's: a child of win, or the first in one of its
 * children (Games' list comes after its sheet). The one on show, else the first: a hidden caption
 * still names the page, and Games writes a game's name into its own (games.c show). */
static void *page_caption(void *win) {
    void *first = 0;
    for (unsigned i = 0, n = widget_count_children(win); i < n; ++i) {
        void *c = widget_get_child(win, i);
        int shown = widget_get_visible(c);
        if (widget_count_children(c) && tk_strcmp(widget_get_type(c), "hscroll_label"))
            c = widget_get_child(c, 0);
        if (tk_strcmp(widget_get_type(c), "hscroll_label") ||
            (I(c, W_H) != 48 && tk_strcmp(widget_get_prop_str(c, "name", ""), PAGE_TITLE)))
            continue;
        if (shown) return c;
        if (!first) first = c;
    }
    return first;
}

/* The bar title for window win: Now Playing and EQ by name, Coverflow by its screen
 * (coverflow_bar_title); else 0, and *label is a stock page's (hidden) navbar title, else a payload
 * page's 48 px caption row (page_title). */
static const char *bar_title_of(void *win, void **label) {
    const char *name = widget_get_prop_str(win, "name", "");
    /* the status row shows the clock on Now Playing (tools/ipod.py status_row) */
    if (tk_str_start_with(name, "playing_page")) return "";
    /* the bar names the Library and its whole Songs list as Home does, not by stock's "Local
     * Songs" in both: allmusic_page's other lists (an album, a playlist, Recently Added) keep their
     * own (0xf001: all songs, az_index.c name_sorted's class) */
    if (!tk_strcmp(name, "localmusic_page"))
        return lr_editing(win) ? lr_edit_title() : "Library";
    if (album_local_on(win)) return ""; /* the design's album page: its header names it */
    if (!tk_strcmp(name, "allmusic_page") && I(g_class_type, 0) == 0xf001)
        return "Songs";
    if (!tk_strcmp(name, "coverflow_page")) { /* its screen's own, else its caption row's */
        const char *t = coverflow_bar_title();
        if (!t) *label = page_caption(win);
        return t;
    }
    if (!tk_strcmp(name, "equalizer_page")) return "EQ"; /* its 48 px row is the tabs' own label */
    if (!tk_strcmp(name, "spotify_page")) return "Spotify"; /* they hide their own name's label */
    if (!tk_strcmp(name, "radionp_page")) return "Internet Radio";
    if (usb_ui_title(name)) return usb_ui_title(name);   /* the USB mode pages (usb_ui.c) */
    if (bt_recv_title(name)) return bt_recv_title(name); /* Bluetooth receiver mode (bt_recv.c) */
    if (bt_detail_title(name)) return bt_detail_title(name);   /* a Bluetooth device's page */
    if (bt_stopped_title(name)) return bt_stopped_title(name); /* Bluetooth Stopped */
    if (guest_ui_title(name)) return guest_ui_title(name);     /* Guest Play's pages */
    if (sw_update_title(name)) return sw_update_title(name);   /* SD Card Update's (sw_update.c) */
    if (msg_title(name)) return msg_title(name); /* the library scan's (scan_page.c) */
    if (!tk_strcmp(name, "qrcode_page")) return addon_qr_title(win); /* the card app's name */
    if (!tk_strcmp(name, "search_dialog")) return "Search"; /* stock's toolbar has no label */
    if (!tk_strcmp(name, "statusbar_dialog")) return "";    /* Control Center: no name, as Home's */
    if (artist_local_on(win)) return ""; /* the design's artist page: the header names it */
    void *nav = widget_lookup(win, "view_navbar", 1);
    if (nav) {
        for (int i = 0, k = widget_count_children(nav); i < k && !*label; ++i) {
            void *c = widget_get_child(nav, i);
            const char *t = widget_get_type(c);
            if (!tk_strcmp(t, "hscroll_label") || !tk_strcmp(t, "label")) *label = c;
        }
    } else {
        *label = page_caption(win);
    }
    return 0;
}

/* Folder's title from stock's navbar name s (its storage, "SD", or a path, "SD:/Music"): Folders
 * at the card's root and the storage list above it (g_folder_layer 1 and 2), else the folder's
 * own name, the path's last, in place in s. */
static const char *folder_title(char *s) {
    if (g_folder_layer <= 2) return "Folders";
    int n = (int)strlen(s);
    while (n > 0 && s[n - 1] == '/') s[--n] = 0;
    char *last = s + n;
    while (last > s && last[-1] != '/' && last[-1] != ':') --last;
    return *last ? last : "Folders";
}

/* Either of st.title_win and st.title_lbl going (page_list rebuilds a caption under a live window)
 * drops both, so the next paint finds them again. */
int title_gone(void *w, void *event) {
    if (st.title_win) widget_off_by_func(st.title_win, EVT_DESTROY, title_gone, st.title_win);
    if (st.title_lbl) widget_off_by_func(st.title_lbl, EVT_DESTROY, title_gone, st.title_lbl);
    /* the Shuffle row keeps its selection under another page (Return lands on it): its own destroy
     * lets it go (shuffle_gone) */
    st.title_win = st.title_lbl = st.action_view = (void *)0;
    return 0;
}

/* The Shuffle row's action view going: its selection with it. */
static int shuffle_gone(void *row, void *event) {
    if (st.shuffle_on == row) st.shuffle_on = (void *)0;
    return 0;
}

#define SHUFFLE_STOP 1 /* st.shuffle_stop: the row's Shuffle */
#define SORT_STOP 2    /* ... its Sort bars */
#define SHUFFLE_ICON                                                                               \
    20 /* the Shuffle row's glyph (tools/art.py ROW_ICON_PX), at the text column                   \
        */
#define SHUFFLE_LABEL_DX 28 /* ... and its label this far after the text column */
#define SORT_PILL_W                                                                                \
    44 /* Sort's stop: its bars centred in a pill this wide, inside the bar inset                  \
        */
/* the Shuffle row's label and the ink last written to it (shuffle_paint) */
static void *shuffle_label __attribute__((section(".scratch")));
static int shuffle_ink __attribute__((section(".scratch")));

/* Songs' action bar as a plain row (a set's action_row, its view tools/ipod.py sizes). Stock
 * rebuilds its children on each refresh (0x4a9e78): the label first, then Play All's image and
 * maybe Sort's, each bound as it is made. They stay as hit areas: the label at the list's text x in
 * the row's face and size, Play All's image over the row's left without its icon, Sort's three bars
 * (tools/art.py) over the right. Their first child's height says they are done. */
static void action_row(void *view) {
    int h = geom_val(V_ACTION_ROW), n = widget_count_children(view);
    if (n < 2 || I(widget_get_child(view, 0), W_H) == h) return;
    void *label = widget_get_child(view, 0), *play = widget_get_child(view, 1);
    int shuffle = theme()->shuffle_row;
    /* a shuffle_row theme's label stands after its glyph (shuffle_paint) and reads Shuffle */
    widget_move_resize(label, geom_val(V_TEXT_X) + (shuffle ? SHUFFLE_LABEL_DX : 0), 0, 280, h);
    if (shuffle) {
        widget_set_text_utf8(label, "Shuffle");
        shuffle_label = (void *)0; /* a new label: shuffle_paint inks it */
    }
    theme_font(label, ROLE_TITLE, FONT_ROW);
    widget_use_style(play, "default");
    widget_move_resize(play, 0, 0, 300, h);
    if (n < 3) return; /* Recently Added and the like have no Sort */
    void *sort = widget_get_child(view, 2);
    /* Songs' order is Playback > Library > Sorting's (library_settings.c): the bars stay, hidden */
    widget_set_visible(sort, 0, 0);
    widget_use_style(sort, "default");
    image_base_set_image(sort, "sort_bars");
    image_set_draw_type(sort, IMAGE_DRAW_CENTER);
    /* a shuffle_row theme's: centred in the pill the wheel's SORT_STOP lights, on the right column
     */
    if (shuffle)
        widget_move_resize(sort, 375 - theme()->sel_inset - SORT_PILL_W, 0, SORT_PILL_W, h);
    else
        widget_move_resize(sort, 301, 0, 48, h);
}

/* A shuffle_row theme's action row (OLED; docs/oled-design.md#song-rows): Shuffle before the
 * songs, a stop the wheel reaches from the first song, and Sort's three bars a stop after it.
 * The selection's bar lights the stop shuffle_key put it on; the glyph is Accent Blue at rest and
 * white on the bar, as the label is. */
static void shuffle_paint(void *w, void *canvas) {
    const theme_t *T = theme();
    const scheme_t *S = scheme();
    int h = I(w, W_H), on = st.shuffle_on == w ? st.shuffle_stop : 0;
    void *label = widget_count_children(w) ? widget_get_child(w, 0) : (void *)0;
    if (on) {
        rect_t bar = { T->sel_inset, 0, 375 - 2 * T->sel_inset, h };
        if (on == SORT_STOP) bar.x = 375 - T->sel_inset - SORT_PILL_W, bar.w = SORT_PILL_W;
        gradient(canvas, bar, S->tones[0], S->tones[1], S->tones[4], T->sel_radius);
    }
    int ink = on == SHUFFLE_STOP ? (int)RGBA(0xffffff) : (int)RGBA(S->tones[0]);
    if (label &&
        (label != shuffle_label || ink != shuffle_ink)) { /* once per change: it repaints */
        shuffle_label = label;
        shuffle_ink = ink;
        widget_set_prop_int(label, "style:normal:text_color", ink);
    }
    paint_art(w, canvas, theme_art(on == SHUFFLE_STOP ? "shuffle_w" : "shuffle_a"),
              geom_val(V_TEXT_X), (h - SHUFFLE_ICON) / 2);
}

/* The bar's title (label_title, in the theme's system bar, hidden: the status row names no page)
 * names the top window win, Home by the list it shows; a dialog keeps the window's title. The
 * Search bar, the letter strip and Up Next's "From" read it. The title's source is found once per
 * top window; after that a paint compares only that label's text with the title's (none for a title
 * by name), and the title is written only when it changes. */
void bar_title_sync(void *win) {
    /* a page under the top window that writes its caption would flip the bar with each paint */
    if (feedback_toast(win)) win = feedback_under(win); /* OLED's toast leaves the page in sight */
    if (!st.bar_title || !win || win != feedback_top()) return;
    /* a menu sheet has no title: the bar names the page under it, which a dialog over the
     * modal may have written over (Delete Playlist's confirm, feedback_confirm) */
    if (modal_win(win) && !(win = feedback_under(win))) return;
    static const unsigned none = 0;
    char s[256], now[256]; /* stock writes nothing unless all of it fits */
    /* Home names the list it shows, and none of Home's own */
    const char *t = !home_menu(win)        ? (const char *)0
                    : lr_editing(win)      ? lr_edit_title()
                    : !coverflow_home_sub() ? ""
                                           : coverflow_home_title();
    if (t)
        ;
    else if (win != st.title_win) {
        title_gone(0, 0);
        st.title_win = win;
        widget_on(win, EVT_DESTROY, title_gone, win);
        /* Control Center (control_center.c) and the library scan (scan_page.c) are pages under
         * the bar */
        if (tk_strcmp(widget_get_type(win), "window") &&
            tk_strcmp(widget_get_prop_str(win, "name", ""), "statusbar_dialog") &&
            !msg_title(widget_get_prop_str(win, "name", "")))
            return;
        t = bar_title_of(win, &st.title_lbl);
        /* a page may have Songs' action bar, drawn as a row (the set's action_row) */
        st.action_view = widget_lookup(win, "view_navbar_allplay", 0);
        if (st.title_lbl) widget_on(st.title_lbl, EVT_DESTROY, title_gone, st.title_lbl);
    } else {
        if (!st.title_lbl) return;
        const unsigned *a = widget_get_text(st.title_lbl) ?: &none,
                       *b = widget_get_text(st.bar_title) ?: &none;
        while (*a && *a == *b) ++a, ++b;
        if (*a == *b) return;
    }
    if (!t) {
        *s = 0;
        if (st.title_lbl) widget_get_text_utf8(st.title_lbl, s, sizeof s);
        t = !tk_strcmp(s, "(null)") ? "" /* stock's "%s" of a NULL name: no title */
            : !tk_strcmp(widget_get_prop_str(win, "name", ""), "folder_page") ? folder_title(s)
                                                                              : s;
    }
    if (widget_get_text_utf8(st.bar_title, now, sizeof now) || tk_strcmp(now, t))
        widget_set_text_utf8(st.bar_title, t);
}

/* A page_title's caption was an empty page's message, and the bar holds captions: the message stays
 * the first line (OLED's is a note centred under the status row, feedback_empty), and the bar reads
 * name, the page's, from the hidden label. */
void page_empty(void *body, const char *name) {
    if (theme()->page_title_h) return;
    char message[64];
    void *caption = widget_get_child(body, 0);
    if (widget_get_text_utf8(caption, message, sizeof message)) return;
    if (!feedback_empty(body, 0, message, (const char *)0)) /* OLED's note, else a first line */
        widget_set_name(page_title(body, message, 0), "");
    widget_set_text_utf8(caption, name);
    widget_set_visible(caption, 0, 0);
    bar_title_sync(window_manager_get_top_window(window_manager()));
}

/* Whether the bar's title can hold label's text as the page's name: any, once the bar is found (the
 * status row shows no title, docs/oled-design.md#status-row). */
int bar_title_fits(void *label) {
    (void)label;
    return st.bar_title && widget_get_canvas(st.bar_title);
}

/* Stock paints a widget's background before its children, so the bar sits behind the rows.
 * The selection work for the surface happens here, once per frame, instead of in the border hook;
 * a BUTTONS dialog is itself top-level. Home's art is clipped to its panel until the border hook.
 * Other top-level widgets are the status bar, which gets its solid fill, and the windows. Painting
 * the top window or the bar keeps the bar's clock, Home's art and Now Playing's labels current;
 * Home's art is also checked when Home is painted under another window. */
int ringnav_paint_bg(void *w, void *canvas) {
    st.paint_w = w;
    table_clip_begin(w, canvas); /* a virtual table's rows inside its box (row_paint.c) */
    np_settle_begin(w, canvas); /* the art settled smaller: stock paints none of it */
    np_skip_begin(w, canvas);   /* a new song's lines: the old copied first (np_skip.c) */
    swipe_back_paint(w, canvas); /* the page under a swipe back, left of the page */
    confirm_prompt(w, canvas);
    feedback_toast_paint(w, canvas); /* the toast's pill */
    album_tile(w);
    int result = 0;
    if (kb_host_bg(w, canvas))
        ;
    else if (!pressed_pill(w, canvas))
        result = stock_paint_bg_trampoline(w, canvas);
    search_strip_bar_ground(w, canvas); /* a Wi-Fi box's dialog cleared under the bar */
    /* Now Playing's glow and rating page (np_rate.c), on their pages' ground */
    np_rate_bg(w, canvas);
    void *wm = window_manager(), *bar = *(void *const *)system_bar;
    feedback_confirm_bar(w, canvas);
    modal_paint(w, canvas);
    confirm_paint(w, canvas); /* the confirmation card (feedback.c) */
    paint_selection(w, canvas);
    pv_ink(w); /* a playlist's action row's label colour, before the label paints */
    grey_rows(w); /* first: paint_songs reserves the marks of the rows it greys */
    paint_songs(w, canvas);
    paint_modal_rows(w, canvas);
    confirm_rows_paint(w, canvas);
    feedback_switch_bar(w, canvas);
    feedback_scan_bar(w, canvas);
    empty_state_paint(w, canvas); /* a page's empty state (feedback.c) */
    marquee_fade_under(w, canvas); /* last: what stands behind a scrolling label's ends */
    coverflow_home_clip(w, canvas, 1);
    if (w && w == st.bar_slot) paint_battery(w, canvas);
    if (w && w == st.action_view && P(canvas, CANVAS_LCD) && theme()->shuffle_row) {
        unsigned fill = (unsigned)I(P(canvas, CANVAS_LCD), LCD_FILL_COLOR);
        shuffle_paint(w, canvas);
        stock_canvas_set_fill_color(canvas, fill);
    }
    if (!w || P(w, W_PARENT) != wm) return result;
    if (w == st.np_win) np_glyphs(w); /* before its buttons paint */
    if (P(canvas, CANVAS_LCD) && !tk_strcmp(widget_get_prop_str(w, "name", ""), "statusbar_dialog"))
        cc_paint(w, canvas);
    if (w == st.msg_win) msg_paint(w, canvas);
    if (w == bar && P(canvas, CANVAS_LCD)) {
        unsigned fill = (unsigned)I(P(canvas, CANVAS_LCD), LCD_FILL_COLOR);
        /* An overlay on a full-bleed page, which paints under it (tools/build.py FULL_BLEED,
         * docs/oled-design.md#full-bleed): no ground of its own but Split's ink ground on Home's
         * band. The page in sight is, under OLED's toast, the page it is over, so Home's band
         * stays its own. Under the glyphs, the top window's scrolled list's rows run on into the
         * band (row_paint.c list_reach_paint). */
        void *under = (void *)0;
        for (unsigned i = widget_count_children(wm); i-- && !under;) {
            void *c = widget_get_child(wm, i);
            if (c && !tk_strcmp(widget_get_type(c), "window")) under = c;
        }
        coverflow_home_bar(canvas, feedback_top(), ui_ink(RGBA(BAR_COLOR)));
        list_reach_paint(w, under, canvas); /* the page's scrolled list under the glyphs */
        status_slots_paint(w, canvas); /* the status row's glyphs but the battery (status_row.c) */
        st.paint_w = w;
        stock_canvas_set_fill_color(canvas, fill);
    }
    void *top = window_manager_get_top_window(wm);
    /* Any painted window, not only the top one: Home slides back in from a snapshot. */
    coverflow_home_art(w == bar ? top : w);
    if (w == st.bc[2]) bc_fit(w);
    if (bar && (w == bar || w == top)) {
        if (!st.clock_timer || (g_time24h_flag != 0) != st.clock_full) { /* not mid-paint */
            st.clock_full = g_time24h_flag != 0;
            rearm(&st.clock_timer, clock_due, 0);
        }
        bar_sync(bar);
        bar_title_sync(top);
        clock_place(feedback_top()); /* beside Split's art under a toast too */
        if (st.action_view) action_row(st.action_view);
        feedback_songs(top, st.action_view); /* OLED: an empty list's note, no Shuffle row */
        feedback_folder(top);                /* an empty folder level's note */
        np_sync(top);
        if (w == top) vol_paint(top, canvas);
        /* Boot may paint Home before the screen is usable, so nothing chose or drew its first
         * row. Once, when the list is first reachable, repaint it. */
        void *list = st.greeted ? (void *)0 : surface((void *)0, (void *)0);
        if (list) {
            st.greeted = 1;
            widget_invalidate_force(list, (void *)0);
        }
    }
    return result;
}

/* Live accent (docs/internals.md#accent). Colors are mapped as style_get_color returns them: text
 * (text_color, highlight_text_color) to the red tone, fills and borders to the light tone. The name
 * is only compared for a color that maps. */
/* Resolve ancestry during painting: rebinding a pooled row never leaves a color override behind. */
int painting_in(void *row) {
    for (void *w = st.paint_w; row && w; w = P(w, W_PARENT)) {
        if (w == row) return 1;
        if (w == st.sel_w) break;
    }
    return 0;
}
/* Whether row holds the selection painted now, or is inside it (catalogue.c's greyed rows). */
int ringnav_row_selected(void *row) {
    void *at = st.sel_w && st.sel_row >= 0 && st.sel_row < g_menu.n ? g_menu.at[st.sel_row] : 0;
    for (void *w = at; row && w && w != st.sel_w; w = P(w, W_PARENT))
        if (w == row) return 1;
    for (void *w = row; at && w && w != st.sel_w; w = P(w, W_PARENT))
        if (w == at) return 1;
    return 0;
}

static unsigned *style_color(unsigned *color, void *style, const char *name, unsigned fallback);
/* A row that can't play now (catalogue.c), a Quality choice the connected Bluetooth device lacks
 * (bt_ui.c), a hidden one and the playing song's title take their text colour last, over the
 * theme's. */
unsigned *ringnav_style_color(unsigned *color, void *style, const char *name, unsigned fallback) {
    style_color(color, style, name, fallback);
    if (!st.style_depth && st.paint_w && name && tk_str_end_with(name, "text_color")) {
        catalogue_greyed(st.paint_w, color);
        bt_quality_dim(st.paint_w, color); /* a codec the connected device lacks (bt_ui.c) */
        if (lr_edit_dim(st.paint_w)) /* a row hidden in Edit Library or Edit Home */
            *color = (*color & 0xff000000u) | (scheme()->muted & 0xffffff);
        if (np_tap_lit(st.paint_w)) /* Now Playing's artist or album held (np_taps.c) */
            *color = (*color & 0xff000000u) | (RGBA(np_tap_ink()) & 0xffffff);
        /* the playing song's title, off the selection (song_rows.c; oled-design.md#song-rows) */
        if (playing_title_is(st.paint_w) && !(st.sel_accent && painting_in(st.sel_accent)))
            *color = (*color & 0xff000000u) | (RGBA(scheme()->tones[0]) & 0xffffff);
    }
    return color;
}
static unsigned *style_color(unsigned *color, void *style, const char *name, unsigned fallback) {
    /* A mutable style (inline style:*) answers from its default style through style_get_color
     * again: only the outermost call maps, as Light Mode's flip is not idempotent. */
    ++st.style_depth;
    stock_color_trampoline(color, style, name, fallback);
    if (--st.style_depth) return color;
    /* Text inputs' selected text (an edit's T9 pending letter) is stock near-black on their light
     * blue selected_bg_color, which Light Mode keeps: it stays, not flipped to paper. */
    if (name && !tk_strcmp(name, "selected_text_color")) return color;
    const scheme_t *S = scheme();
    const theme_t *T = theme();
    if (T->sb_w && st.paint_w && name &&
        (tk_str_end_with(name, "fg_color") || tk_str_end_with(name, "bg_color")) &&
        !tk_strcmp(widget_get_type(st.paint_w), "scroll_bar_m")) {
        *color &= 0xffffff; /* paint_scrollbar draws it */
        return color;
    }
    if (st.paint_w && name && tk_str_end_with(name, "text_color")) {
        const char *label = widget_get_prop_str(st.paint_w, "name", "");
        if (!tk_strcmp(label, "label_del") || !tk_strcmp(label, "label_rename")) {
            int enabled = 1;
            for (void *w = P(st.paint_w, W_PARENT); w; w = P(w, W_PARENT))
                if (!widget_get_prop_bool(w, "enable", 1)) enabled = 0;
            *color = (*color & 0xff000000u) | (enabled ? 0xffffff : 0xaaaaaa);
        }
        /* the selection's row, or the Shuffle row's label while its Shuffle is lit (outside the
         * list: shuffle_paint) */
        if ((ipod_light() || S->sel_text) &&
            (painting_in(st.sel_accent) ||
             (shuffle_label && st.paint_w == shuffle_label && st.shuffle_on == st.action_view &&
              st.shuffle_stop == SHUFFLE_STOP))) {
            if (S->sel_text) *color = (*color & 0xff000000u) | (RGBA(S->sel_text) & 0xffffff);
            return color;
        }
        /* A set_switch theme's disabled row: stock's #777777 label in Text Tertiary (the
         * scheme's muted), as its chevron (chevron_muted) */
        if (T->set_switch && (*color & 0xffffff) == 0x777777)
            *color = (*color & 0xff000000u) | S->muted;
        /* stock's secondary text takes the scheme's quieter grey */
        if (S->muted && (*color & 0xffffff) == 0xaaaaaa) *color = (*color & 0xff000000u) | S->muted;
    }
    int a = accent();
    unsigned c = accent_map(*color, a, TONE_LIGHT);
    if (c != *color && name && tk_str_end_with(name, "text_color"))
        c = accent_map(*color, a, TONE_RED);
    *color = c;
    return color;
}

/* Backgrounds come as gradients: the whole stock leaf (null style or vtable: none), then the stops
 * of the caller's gradient_t (nr @8, 8-byte {color, offset} stops @0xc, at most 8). style_get_color
 * asks here first for every color, text included, so its own call stays unmapped and the color
 * hook picks the tone. */
void *ringnav_style_gradient(void *style, const char *name, void *out) {
    void *vt = style ? P(style, 0) : (void *)0;
    void *(*get)(void *, const char *, void *) =
        vt ? (void *(*)(void *, const char *, void *))P(vt, 0x18) : (void *)0;
    ++st.style_depth;
    void *g = get ? get(style, name, out) : (void *)0;
    int own = --st.style_depth || __builtin_return_address(0) == (void *)STYLE_COLOR_GRADIENT_RET;
    for (int i = 0; !own && g && g == out && i < I(g, 8) && i < 8; ++i)
        I(g, 0xc + 8 * i) = (int)accent_map((unsigned)I(g, 0xc + 8 * i), accent(), TONE_LIGHT);
    return g;
}

/* 1 if name is one of the settings rows' category icons (SETTINGS_ICON_NAMES in stock.h, from
 * ipod.json): their red is a category colour, like the purple and orange ones, not an accent. */
static int settings_icon(const char *name) { return named_in(SETTINGS_ICON_NAMES, name); }

/* Decoded theme images are mapped once, before the image manager caches them. Only a plain asset
 * name is the theme's: covers by path or URL (a '/' or ':') never are. An icons.mono theme (OLED)
 * also greys built-in navigation/control icons; category discs keep white glyphs, warning batteries
 * keep their colours. The confirm pop-up's discs (CONFIRM_IMAGE*) take the dark CONFIRM_SURFACE
 * under every accent, Crimson included, so their white glyphs stay legible. The quick settings'
 * active controls (DROPDOWN_IMAGE*, not the brightness suns) take the accent's red tone like
 * everything else red, but their white glyph turns CONFIRM_SURFACE on a tone brighter than
 * GLYPH_LIGHT_MAX (Graphite's silver), so an active disc stands apart from the grey inactive ones
 * and its glyph stays legible. Any other red under white (a switch's knob, a disc's glyph, a
 * BUTTON_IMAGE*'s label) is a surface and takes the light tone, which keeps the white legible; red
 * marks on their own keep the red tone. bitmap_t: w @0, h @4, format @0xe; the 32-bit formats 1-4
 * hold r, g, b at these byte offsets. */
/* Clamp straight alpha to the arc's coverage (corner_cover.c): reusing an already rounded bitmap
 * is harmless. */
static void round_corners(unsigned char *px, int w, int h, int stride, int alpha) {
    /* a theme's list.art.radius is on screen, at the set's art size: the bitmap's own scale */
    int side = w < h ? w : h, art = geom(G_ART), radius = theme()->art_radius;
    int r = radius && art ? (side * radius + art / 2) / art : side * NP_ART_RADIUS / 166;
    if (2 * r > side) r = side / 2;
    const unsigned char *cover = corner_cover(r);
    for (int y = 0; cover && y < r; ++y) {
        for (int x = 0; x < r && cover[y * r + x] < 255; ++x) {
            unsigned coverage = cover[y * r + x];
            for (int c = 0; c < 4; ++c) {
                unsigned char *a =
                    px + (c & 1 ? h - 1 - y : y) * stride + (c & 2 ? w - 1 - x : x) * 4 + alpha;
                if (*a > coverage) *a = (unsigned char)coverage;
            }
        }
    }
}

/* Zeroes every pixel of a decoded 32-bit bitmap (bitmap_t w @0, h @4), alpha included, row padding
 * kept, and clears its opaque flag (@0xc bit 0) as round_corners' callers do: it draws nothing. */
static void clear_bitmap(void *bitmap) {
    unsigned char *px = bitmap_lock_buffer_for_write(bitmap);
    if (!px) return;
    for (int y = 0; y < I(bitmap, 4); ++y)
        memset(px + y * bitmap_get_line_length(bitmap), 0, 4 * (unsigned)I(bitmap, 0));
    *(unsigned short *)((char *)bitmap + 0xc) &= ~1u;
    bitmap_unlock_buffer(bitmap);
}

/* Scales a decoded 32-bit bitmap (at most 64 px square; alpha at byte a of each pixel) into a to x
 * to square at its middle, each pixel the alpha-weighted mean of the pixels it covers (a straight
 * or a premultiplied colour alike), the rest cleared, and clears its opaque flag. The copy it reads
 * from is on the heap for the call (up to 16 KB, once per tile the image manager loads), not in the
 * scratch state; when that allocation fails the tile is left as it was. */
static void shrink_centred(void *bitmap, int to, int a) {
    int w = I(bitmap, 0), h = I(bitmap, 4), stride = bitmap_get_line_length(bitmap);
    int premul = *(unsigned short *)((char *)bitmap + 0xc) & BITMAP_PREMULTI;
    if (!(w <= 64 && h <= 64 && 0 < to && to < w && to < h)) return;
    unsigned char *src = calloc(4u * (unsigned)w, (unsigned)h);
    unsigned char *px = src ? bitmap_lock_buffer_for_write(bitmap) : (void *)0;
    if (!px) {
        free(src);
        return;
    }
    for (int y = 0; y < h; ++y) memcpy(src + 4 * w * y, px + stride * y, 4u * (unsigned)w);
    for (int y = 0; y < h; ++y)
        for (int x = 0; x < w; ++x) {
            unsigned sum[4] = { 0 }, n = 0;
            int sx = x - (w - to) / 2, sy = y - (h - to) / 2;
            if (sx >= 0 && sy >= 0 && sx < to && sy < to)
                for (int v = sy * h / to; v < (sy + 1) * h / to; ++v)
                    for (int u = sx * w / to; u < (sx + 1) * w / to; ++u, ++n)
                        for (int c = 0; c < 4; ++c) {
                            const unsigned char *p = src + 4 * (w * v + u);
                            sum[c] += c == a || premul ? p[c] : p[c] * p[a];
                        }
            unsigned char *d = px + stride * y + 4 * x;
            for (int c = 0; c < 4; ++c)
                d[c] = (unsigned char)(!n                 ? 0
                                       : c == a || premul ? sum[c] / n
                                       : sum[a]           ? sum[c] / sum[a]
                                                          : 0);
        }
    *(unsigned short *)((char *)bitmap + 0xc) &= ~1u;
    bitmap_unlock_buffer(bitmap);
    free(src);
}

int ringnav_image_add(void *manager, const char *name, void *bitmap) {
    static const unsigned char at[4][4] = BITMAP_RGBA_AT;
    unsigned format = bitmap ? *(unsigned short *)((char *)bitmap + 0xe) - 1u : 4,
             preset = accent();
    const theme_t *T = theme();
    const scheme_t *sc = scheme(); /* scheme preset (accent()) in the half Light Mode picks */
    unsigned char *data = (void *)0;
    /* Before the manager copies bitmap_t: cached flags must agree with the new alpha. These
     * paths are stock's album/artist thumbnails, Folder view's (.downpic/fd*, its cover
     * thread's) and a playlist's row art (playlist_art.c, pl_ and plc_, the _s.jpg ones: Cover
     * Flow insets its covers' own corners); playing covers are rounded after scaling. */
    if (format < 4 && name &&
        (tk_str_start_with(name, "file:///mnt/mmc/.sldp/") ||
         tk_str_start_with(name, "file:///mnt/mmc/.downpic/fd") ||
         (tk_str_start_with(name, "file:///mnt/mmc/.coverflow/pl") &&
          tk_str_end_with(name, "_s.jpg")))) {
        int w = I(bitmap, 0), h = I(bitmap, 4), stride = bitmap_get_line_length(bitmap);
        if (w > 0 && h > 0 && w <= 4096 && h <= 4096 && stride >= w * 4) {
            unsigned char *px = bitmap_lock_buffer_for_write(bitmap);
            if (px) {
                round_corners(px, w, h, stride, at[format][3]);
                *(unsigned short *)((char *)bitmap + 0xc) &= ~1u;
                bitmap_unlock_buffer(bitmap);
            }
        }
    }
    const char *s = name;
    int dark = s && tk_str_start_with(s, CONFIRM_IMAGE);
    int control = s && tk_str_start_with(s, DROPDOWN_IMAGE) && !tk_str_start_with(s, DROPDOWN_SUN);
    int paper = ipod_light(); /* Light Mode: tone_of's tones, flipped as drawn */
    unsigned tone = dark ? CONFIRM_SURFACE : flip_if(paper, tone_of(tones_of(preset), TONE_RED)),
             glyph = 0xffffff, light = flip_if(paper, tone_of(tones_of(preset), TONE_LIGHT));
    if (control && bright_tone(tone)) glyph = CONFIRM_SURFACE;
    while (s && *s && *s != '/' && *s != ':') ++s;
    /* art_final: the theme's own images (tools/art.py, named by its art prefix) are final */
    if (s && !*s && T->art_final && tk_str_start_with(name, T->art)) s = (void *)0;
    /* icons_badge_muted: the codec badges (img_flac, img_mp3, ...: red with the name cut out) take
     * the secondary text's grey. */
    if (s && !*s && tk_str_start_with(name, "img_") && tk_strcmp(name, "img_delete") &&
        tk_strcmp(name, "img_edit") && tk_strcmp(name, "img_morefolder") &&
        tk_strcmp(name, "img_path") && !dark && T->icons_badge_muted)
        tone = sc->muted;
    /* icons_mono: the settings icons' coloured discs all turn the scheme's chevron grey, like
     * stock's grey list discs. A pixel's saturation (max - min) against the disc's (the icon's most
     * saturated) says how far it blends towards the white glyph, so edges stay smooth; alpha
     * kept. */
    int icon = s && !*s &&
               (settings_icon(name) || tk_str_start_with(name, "local_") ||
                tk_str_start_with(name, "list_") || tk_str_start_with(name, "small_") ||
                tk_str_start_with(name, "player_"));
    int mono = s && !*s && T->icons_mono &&
               (tk_str_start_with(name, "navbar_") || tk_str_start_with(name, "home_") ||
                tk_str_start_with(name, "img_") || tk_str_start_with(name, "drop_") ||
                tk_str_start_with(name, "play_") || tk_str_start_with(name, "playlist_") ||
                tk_str_start_with(name, "stream_") || tk_str_start_with(name, "tidal_") ||
                tk_str_start_with(name, "btn_") || tk_str_start_with(name, "bt_") ||
                tk_str_start_with(name, "singer_") || tk_str_start_with(name, "song_") ||
                tk_str_start_with(name, "album_") || tk_str_start_with(name, "search") ||
                !tk_strcmp(name, "airplay") || !tk_strcmp(name, "dlna") ||
                !tk_strcmp(name, "wifitransport")) &&
               tk_strcmp(name, "play_defaultcover") && tk_strcmp(name, "playlist_default") &&
               !tk_str_start_with(name, "tidal_shan") && tk_strcmp(name, "tidal_user");
    /* Baidu's is a white square, the only list icon without a disc: cut to one, its edge a pixel
     * soft. An icons.mono theme greys it inverted below (a grey disc, the logo white), as its
     * neighbours. */
    int square = s && !*s && !tk_strcmp(name, "list_baidu");
    unsigned char *cut = square && format < 4 ? bitmap_lock_buffer_for_write(bitmap) : (void *)0;
    if (cut) {
        int w = I(bitmap, 0), h = I(bitmap, 4), r = (w < h ? w : h) - 2; /* radius x2 */
        for (int y = 0; y < h; ++y)
            for (int x = 0; x < w; ++x) {
                unsigned char *a = cut + y * bitmap_get_line_length(bitmap) + 4 * x + at[format][3];
                int dx = 2 * x + 1 - w, dy = 2 * y + 1 - h, d = dx * dx + dy * dy;
                int q = r - 2; /* sqrt(d), in the two-pixel band only */
                while (q < r + 2 && (q + 1) * (q + 1) <= d) ++q;
                *a = (unsigned char)(*a * (r + 2 - q) / 4);
            }
        bitmap_unlock_buffer(bitmap);
    }
    unsigned char *px =
        icon && format < 4 && T->icons_mono ? bitmap_lock_buffer_for_write(bitmap) : (void *)0;
    if (px) {
        const unsigned char *o = at[format];
        int disc = 0;
        for (int pass = 0; pass < 2; ++pass)
            for (int y = 0; y < I(bitmap, 4); ++y)
                for (unsigned char *p = px + y * bitmap_get_line_length(bitmap),
                                   *end = p + 4 * I(bitmap, 0);
                     p < end; p += 4) {
                    int r = p[o[0]], g = p[o[1]], b = p[o[2]];
                    int sat = (r > g ? (r > b ? r : b) : (g > b ? g : b)) -
                              (r < g ? (r < b ? r : b) : (g < b ? g : b));
                    if (!pass) {
                        if (sat > disc) disc = sat;
                        continue;
                    }
                    if (sat > disc) sat = disc;
                    if (icon && !disc) continue; /* already grey: keep the disc/glyph contrast */
                    int ink = disc ? (255 - (sc->chevron & 255)) * sat / disc : 0;
                    p[o[0]] = p[o[1]] = p[o[2]] =
                        (unsigned char)(!icon    ? sc->chevron & 255
                                        : square ? (sc->chevron & 255) + ink
                                                 : 255 - ink);
                }
        bitmap_unlock_buffer(bitmap);
    }
    int mapped = 0; /* the accent changed a pixel: its colours are final */
    if (format < 4 && s && !*s && !settings_icon(name) && (!T->icons_mono || !icon))
        data = bitmap_lock_buffer_for_write(bitmap);
    if (data) {
        const unsigned char *o = at[format];
        unsigned stride = bitmap_get_line_length(bitmap);
        /* Only an active control holds red; an inactive one keeps its white glyph. */
        int red = 0, white = tk_str_start_with(name, BUTTON_IMAGE);
        int scan = control ? glyph != 0xffffff : !dark && light != tone;
        for (int y = 0; scan && !(red && white) && y < I(bitmap, 4); ++y)
            for (unsigned char *p = data + y * stride, *end = p + 4 * I(bitmap, 0); p < end;
                 p += 4) {
                unsigned c = p[o[0]] | p[o[1]] << 8 | p[o[2]] << 16;
                if (red_map(c, tone, 0xffffff, 0) != c)
                    red = 1;
                else if ((c == 0xffffff || c == 0x7f7f7f) &&
                         p[o[3]] == 255) /* or a pressed disc's */
                    white = 1;
            }
        if (!red)
            glyph = 0xffffff;
        else if (!control && white)
            tone = light;
        for (int y = 0; y < I(bitmap, 4); ++y)
            for (unsigned char *p = data + y * stride, *end = p + 4 * I(bitmap, 0); p < end;
                 p += 4) {
                unsigned c0 = p[o[0]] | p[o[1]] << 8 | p[o[2]] << 16,
                         c = red_map(c0, tone, glyph, 0);
                mapped |= c != c0;
                if (mono) {
                    unsigned grey =
                        ((c & 255) * 299 + (c >> 8 & 255) * 587 + (c >> 16) * 114) / 1000;
                    c = grey * 0x010101;
                }
                p[o[0]] = c;
                p[o[1]] = c >> 8;
                p[o[2]] = c >> 16;
            }
        bitmap_unlock_buffer(bitmap);
    }
    /* icons.addon_mono: a card app's Streaming icon (addon.c, a file:// image) greys as
     * Streaming's own stream_ icons do (mono, above), so its row matches theirs. */
    unsigned char *app = format < 4 && T->icons_addon_mono && addon_is_icon(name) &&
                                 I(bitmap, 0) > 0 && I(bitmap, 0) <= 4096 && I(bitmap, 4) <= 4096 &&
                                 (int)bitmap_get_line_length(bitmap) >= 4 * I(bitmap, 0)
                             ? bitmap_lock_buffer_for_write(bitmap)
                             : (void *)0;
    if (app) {
        const unsigned char *o = at[format];
        for (int y = 0; y < I(bitmap, 4); ++y)
            for (unsigned char *p = app + y * bitmap_get_line_length(bitmap),
                               *end = p + 4 * I(bitmap, 0);
                 p < end; p += 4)
                p[o[0]] = p[o[1]] = p[o[2]] =
                    (unsigned char)((p[o[0]] * 299 + p[o[1]] * 587 + p[o[2]] * 114) / 1000);
        bitmap_unlock_buffer(bitmap);
    }
    /* stock's grey `>` on every row is emptied: paint_into draws each row's own, and a disabled
     * row's (list_intodown) in Text Tertiary */
    if (s && !*s && (!tk_strcmp(name, "list_into") || !tk_strcmp(name, "list_intodown")) &&
        format < 4) {
        clear_bitmap(bitmap);
        mapped = 1;
    }
    /* A set_switch theme: stock's switch art is emptied; paint_into draws the theme's switch,
     * Gain's value in place of its switch (paint_gain) and a Wi-Fi network's signal (wifi_mark) */
    if (T->set_switch && s && !*s && format < 4 &&
        (!tk_strcmp(name, "switch_on") || !tk_strcmp(name, "switch_off") ||
         !tk_strcmp(name, "gain_high") || !tk_strcmp(name, "gain_low") ||
         (name[0] == 'w' && wifi_sig(name)))) {
        clear_bitmap(bitmap);
        mapped = 1;
    }
    /* A set_text theme: stock's radio rings are emptied; paint_radio draws the
     * tick. */
    if (T->set_text && s && !*s && (!tk_strcmp(name, "select") || !tk_strcmp(name, "unselect")) &&
        format < 4) {
        clear_bitmap(bitmap);
        mapped = 1;
    }
    /* Light Mode: a theme image without colour that is mostly glyph (marks, chevrons, spinners)
     * flips as the colours do. One with a grey or dark disc or plate (LIGHT_SURFACE) keeps its
     * white glyph, an image the accent mapped is final, and switch_off keeps its white knob as
     * switch_on does. Quick Settings' inactive discs (DROPDOWN_IMAGE, unmapped) flip whole: a
     * light disc under a dark glyph, as the active ones stand out in the accent. Premultiplied
     * pixels (bitmap_t flags @0xc) flip their straight colour. Gain's switch keeps its white in a
     * theme's own light half, its thumb white as the other switches' are. */
    unsigned char *ink = paper && !mapped && format < 4 && s && !*s &&
                                 tk_strcmp(name, "switch_off") &&
                                 (T->light == T->schemes ||
                                  (tk_strcmp(name, "gain_low") && tk_strcmp(name, "gain_high")))
                             ? bitmap_lock_buffer_for_write(bitmap)
                             : (void *)0;
    if (ink) {
        const unsigned char *o = at[format];
        unsigned fp = sc->paper & 255, fi = sc->ink & 255;
        int w = I(bitmap, 0), h = I(bitmap, 4), stride = bitmap_get_line_length(bitmap);
        int premul = *(unsigned short *)((char *)bitmap + 0xc) & BITMAP_PREMULTI, solid = 0,
            hue = 0;
        for (int y = 0; !hue && y < h; ++y)
            for (unsigned char *p = ink + y * stride, *end = p + 4 * w; p < end; p += 4) {
                int a = p[o[3]], r = p[o[0]], g = p[o[1]], b = p[o[2]];
                if (a < 128) continue;
                int hi = r > g ? (r > b ? r : b) : (g > b ? g : b),
                    lo = r < g ? (r < b ? r : b) : (g < b ? g : b);
                if (premul) hi = hi * 255 / a, lo = lo * 255 / a;
                hue |= hi - lo > LIGHT_CHROMA;
                solid += (premul ? (r * 299 + g * 587 + b * 114) / a * 255 / 1000
                                 : (r * 299 + g * 587 + b * 114) / 1000) < LIGHT_GLYPH;
            }
        for (int y = 0; !hue && (control || solid * LIGHT_SURFACE < w * h) && y < h; ++y)
            for (unsigned char *p = ink + y * stride, *end = p + 4 * w; p < end; p += 4)
                for (int c = 0, a = p[o[3]]; c < 3 && a; ++c)
                    p[o[c]] = (unsigned char)(premul ? flip_v(p[o[c]] * 255u / a, fp, fi) * a / 255
                                                     : flip_v(p[o[c]], fp, fi));
        bitmap_unlock_buffer(bitmap);
    }
    /* A theme's art placeholder size (art.placeholder): stock's grey tiles, as tall as the row
     * (Folder view's folders and songs; list_song is Album info's row image too), drawn at that
     * size in its middle, so they have room above and below; a real cover (a file path) keeps the
     * row's size. */
    static const char *const tiles[] = { "list_folder", "list_song" };
    for (unsigned k = 0;
         T->art_placeholder && s && !*s && format < 4 && k < sizeof tiles / sizeof *tiles; ++k)
        if (!tk_strcmp(name, tiles[k])) shrink_centred(bitmap, T->art_placeholder, at[format][3]);
    return stock_image_trampoline(manager, name, bitmap);
}

