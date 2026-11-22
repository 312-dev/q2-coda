static int untouch(const void *info) {
    st.untouch_timer = 0;
    st.touch_mode = 0;
    void *top = window_manager_get_top_window(window_manager());
    if (top) widget_invalidate_force(top, (void *)0);
    return 0;
}

static void hide_outline(void) {
    if (st.touch_mode && !st.untouch_timer) return; /* already hidden: each touch move comes here */
    void *top = window_manager_get_top_window(window_manager());
    /* The keyboard's keys and the lock window's swipe touch no row: the list shown after them
     * (a playlist just named, the page under the lock) keeps its bar. */
    if (g_lockscreen_pageflag || (top && !tk_strcmp(widget_get_type(top), "keyboard"))) return;
    stop_timer(&st.untouch_timer);
    st.touch_mode = 1;
    if (top) widget_invalidate_force(top, (void *)0);
}
/* A touch on the page after all: swipe_back.c's edge press that dragged up, down or left, or lifted
 * where it came down. */
void touch_hide(void) { hide_outline(); }

int ringnav_touch(void *ctx, void *event) {
    /* a finger on the A-Z index: its moves are az_index.c's (az_event), the rows' bar stays */
    if (az_held(event)) return stock_touch_trampoline(ctx, event);
    /* a finger dragging a Library or Home row: its moves are library_edit.c's (lr_event) */
    if (lr_edit_dragging()) return stock_touch_trampoline(ctx, event);
    /* settle_timer while paused on the scrubbed track: a paused seek still waiting for Play. */
    int hold =
        st.settle_timer && st.np_slider && np_track() == st.scrub_track && mclGetPlayStatus() == 3;
    if (hold && event) {
        int xy[2] = { I(event, EVENT_X), I(event, EVENT_Y) };
        widget_to_local(st.np_slider, xy);
        if (xy[0] >= 0 && xy[0] < I(st.np_slider, W_W) && xy[1] >= 0 &&
            xy[1] < I(st.np_slider, W_H))
            hold = 0;
    }
    if (!hold)       /* swiping pages keeps the paused seek's preview */
        np_cancel(); /* before the slider sees the touch, so a drag seeks the stock way */
    /* A swipe back's touch (swipe_back.c) is no touch on the rows: the bar rides with the page. Its
     * press hides it here as any does, and is given it back below once it is a candidate. */
    int was_touch = st.touch_mode, swiping = swipe_back_holds();
    if (!swiping) hide_outline();
    int result = stock_touch_trampoline(ctx, event);
    /* A tap is a fresh interaction: it cancels a pending screen-toggle pair and any spin. */
    drop_input();
    /* Stock move-before forwards to this down-before entry too. Only a real down starts
     * a new pull; the page's native before-children callbacks observe subsequent motion. */
    if (!event || I(event, EVENT_TYPE) == EVT_POINTER_DOWN) pull_begin(event);
    void *w = surface((void *)0, (void *)0);
    /* a press on an indexed list's right edge is the A-Z index's (az_index.c) */
    if (!result && w && event && I(event, EVENT_TYPE) == EVT_POINTER_DOWN && az_touch(w, event)) {
        st.touch_mode = 0;
        return result;
    }
    /* a press on a Library or Home row (OLED): its hold, in the editor its grip (library_edit.c) */
    if (!result && event && I(event, EVENT_TYPE) == EVT_POINTER_DOWN && lr_edit_touch(event))
        return result;
    /* a press near the left edge may be a swipe back (swipe_back.c); not where Return stays on
     * the page: full-screen lyrics go back to the column, the rating picker closes */
    if (!result)
        swipe_back_down(event, !st.rt_page && !(st.np_full && window_manager_get_top_window(
                                                                  window_manager()) == st.np_win));
    if (!swiping && !was_touch && swipe_back_holds()) st.touch_mode = 0;
    /* Pointer-down must not recall/rebind the row that native touch is about to hit. */
    if (!result && w && load_rows(&g_menu, w)) {
        stop_scroll(&g_menu);
        prop(w, TOUCH, 1);
        widget_invalidate_force(w, (void *)0);
    }
    return result; /* The very same touch continues through the stock tap/drag handlers. */
}

/* target's click, to stock. Set date and time's OK sets the clock (stock) and stays; it goes back
 * to the Time page instead, as Return does, after a tap (ringnav_dispatch) or Centre's click
 * (edit_single). */
static int time_click(void *target, void *event) {
    void *page = window_manager_get_top_window(window_manager());
    int ok = page && !tk_strcmp(widget_get_prop_str(page, "name", ""), "manualtime_page") &&
             !tk_strcmp(widget_get_prop_str(target, "name", ""), "btn_enter");
    int result = stock_dispatch_trampoline(target, event);
    if (ok && st.clock_timer) clock_show();
    if (ok && window_manager_get_top_window(window_manager()) == page) navigator_back();
    return result;
}

static void *np_more(void);

/* The Shuffle row's action (a shuffle_row theme): the songs the page lists, p_deque_showlist, as
 * Shuffle Songs plays them. The list must be the surface's, row for row, or nothing plays. */
static void shuffle_page(void) {
    void *list = P(p_deque_showlist, 0), *w = surface((void *)0, (void *)0);
    if (!list || !w || !load_rows(&g_menu, w) || (int)deque_size(list) != g_menu.rows ||
        !shuffle_play(list))
        toast("Queue unchanged");
}

/* A touch's press and whether it went more than TAP_PX from it either way before its lift (far);
 * lift: the last pointer event was its lift, so the click that follows is that touch's, not a
 * key's. AWTK clicks a button lifted inside it however far it was dragged there, and a list's
 * scroll view takes only drags up or down: a sideways drag across a row clicked it (Gain cycled,
 * a switch flipped). drag_click drops such a click on a list's row. */
static struct {
    int x0, y0, far, lift;
} tp __attribute__((section(".scratch")));

static void touch_track(void *event) {
    int type = I(event, EVENT_TYPE), x = I(event, EVENT_X), y = I(event, EVENT_Y);
    if (type == EVT_POINTER_DOWN - 1 || type == EVT_POINTER_DOWN) {
        tp.x0 = x, tp.y0 = y, tp.far = tp.lift = 0;
        return;
    }
    int up = type == EVT_POINTER_UP_BEFORE - 1 || type == EVT_POINTER_UP_BEFORE;
    if (up || type == EVT_POINTER_MOVE_BEFORE - 1 || type == EVT_POINTER_MOVE_BEFORE) {
        if ((unsigned)(x - tp.x0 + TAP_PX) > 2 * TAP_PX ||
            (unsigned)(y - tp.y0 + TAP_PX) > 2 * TAP_PX)
            tp.far = 1;
        if (up) tp.lift = 1;
    } else if (type >= EVT_KEY_DOWN && type <= EVT_KEY_UP_BEFORE)
        tp.lift = 0; /* a key's click from here on */
}

/* 1 for a click on a list's row (inside a scroll view) made by the lift of a touch that went
 * more than TAP_PX from its press: a drag, not a tap. Buttons in no list (a dialog's, a
 * keyboard's) keep stock's click. */
static int drag_click(void *target) {
    int dragged = tp.lift && tp.far;
    tp.lift = 0; /* one click a lift */
    for (void *w = dragged ? target : (void *)0; w; w = P(w, W_PARENT))
        if (!tk_strcmp(widget_get_type(w), "scroll_view")) return 1;
    return 0;
}

/* Observe actual clicks BEFORE app callbacks can navigate or destroy/rebind their widgets.
 * Do not turn pointer-down into selection: a swipe is not a tap. */
int ringnav_dispatch(void *target, void *event) {
    if (event) touch_track(event);
    /* a row's click from a drag's lift: no tap, so no row (a Settings switch or choice) changes */
    if (target && event && I(event, EVENT_TYPE) == EVT_CLICK && drag_click(target)) return STOP;
    if (np_tap_lift(target, event)) return STOP; /* a hold's lift under the art it opened */
    if (event && target != window_manager() &&
        ((I(event, EVENT_TYPE) >= EVT_KEY_DOWN && I(event, EVENT_TYPE) <= EVT_KEY_UP_BEFORE) ||
         I(event, EVENT_TYPE) == EVT_KEY_LONG) &&
        ple_key((unsigned)I(event, EVENT_KEY), 3))
        return STOP;
    if (event) {
        int type = I(event, EVENT_TYPE);
        if (quick.timer &&
            (type == EVT_POINTER_UP_BEFORE || type == EVT_POINTER_UP_BEFORE - 1 ||
             type == EVT_POINTER_ABORT || type == EVT_CLICK || type == EVT_KEY_DOWN_BEFORE ||
             ((type == EVT_POINTER_MOVE_BEFORE || type == EVT_POINTER_MOVE_BEFORE - 1) &&
              ((unsigned)(I(event, EVENT_X) - quick.x + 10) > 20 ||
               (unsigned)(I(event, EVENT_Y) - quick.y + 10) > 20))))
            quick_cancel(0, 0);
        if (target && type == EVT_CLICK && widget_get_prop_int(target, QUICK_HOLD, 0) < 0)
            return STOP;
        /* Quick Settings' swipe up to close may start on its brightness slider. The slider gets
         * its press only once the touch has moved SLIDE_PX more sideways than up or down, or on a
         * tap's release; a touch that moves up or down first, or while stock slides the pull-down
         * (g_dropdown_menu_state 1, 2), never reaches it, and one it already has is aborted. A
         * withheld event returns 0, not STOP, which would end its parents' handling too: the
         * window manager's pointer-down keeps the touch's start (g_pressdown_y) for the swipe. */
        void *qs = target;
        if (type == EVT_POINTER_DOWN - 1 && target &&
            !tk_strcmp(widget_get_prop_str(target, "name", ""), "slider_backlight"))
            while (qs && tk_strcmp(widget_get_prop_str(qs, "name", ""), "statusbar_dialog"))
                qs = P(qs, W_PARENT); /* Quick Settings', not Settings' Brightness page */
        else
            qs = 0;
        if (qs) {
            quick.slider = target, quick.slide = 0;
            quick.sx = I(event, EVENT_X), quick.sy = I(event, EVENT_Y);
            return 0;
        }
        int move = type == EVT_POINTER_MOVE_BEFORE - 1, up = type == EVT_POINTER_UP_BEFORE - 1;
        if (target && target == quick.slider && (move || up)) {
            int dx = I(event, EVENT_X) - quick.sx, dy = I(event, EVENT_Y) - quick.sy;
            dx = dx < 0 ? -dx : dx, dy = dy < 0 ? -dy : dy;
            unsigned ev[12];
            if ((unsigned)(I(g_dropdown_menu_state, 0) - 1) < 2) {
                if (quick.slide > 0)
                    stock_dispatch_trampoline(
                        target, pointer_event_init(ev, EVT_POINTER_ABORT, target, 0, 0));
                quick.slide = -1;
            } else if (!quick.slide && (up || (dx >= SLIDE_PX && dx > dy))) {
                quick.slide = 1;
                stock_dispatch_trampoline(target, pointer_event_init(ev, EVT_POINTER_DOWN - 1,
                                                                     target, quick.sx, quick.sy));
            } else if (!quick.slide && dy >= SLIDE_PX)
                quick.slide = -1;
            if (up) quick.slider = 0;
            if (quick.slide <= 0) return 0;
        } else if (up) /* a release the slider doesn't see, its press never given */
            quick.slider = 0;
    }
    /* Native key delivery can retain the covered window's focused widget: the Artists list under
     * an artist's page (wheel turns queued while it loads) or under its Coverflow. */
    if (target && event && I(event, EVENT_TYPE) >= EVT_KEY_DOWN &&
        I(event, EVENT_TYPE) <= EVT_KEY_UP_BEFORE &&
        (I(event, EVENT_KEY) == KEY_PREV || I(event, EVENT_KEY) == KEY_NEXT)) {
        void *wm = window_manager(), *top = window_manager_get_top_window(wm);
        const char *name = top ? widget_get_prop_str(top, "name", "") : "";
        if (target != wm && top &&
            (!tk_strcmp(name, "coverflow_page") || !tk_strcmp(name, "artistinfo_page"))) {
            void *w = target;
            while (w && w != top) w = P(w, W_PARENT);
            if (!w) return STOP;
        }
    }
    if (st.pull_page && (!event || !pull_live() || I(event, EVENT_TYPE) == EVT_KEY_DOWN_BEFORE))
        pull_cancel();
    if (target && event && I(event, EVENT_TYPE) == EVT_CLICK) {
        for (void *w = target; w; w = P(w, W_PARENT))
            if (widget_get_prop_int(w, PULL_SUPPRESS, 0)) return STOP;
        pull_cancel();
    }
    if (target && event && I(event, EVENT_TYPE) == EVT_CLICK) {
        if (lr_edit_click(target)) return STOP; /* the editor: a tap selects, opens nothing */
        hide_outline();
        cancel_center(); /* A native activation supersedes confirmation, even without touch. */
        drop_spin();
        keep_selected(
            target); /* a tap owns its live row: recall could scroll/rebind it before delivery */
        /* a tap on the Shuffle row: its action, not stock's Play All under it (shuffle_key); the
         * theme is asked only then, as theme() reads the accent's settings */
        if (st.action_view && widget_count_children(st.action_view) > 1 &&
            target == widget_get_child(st.action_view, 1) && theme()->shuffle_row) {
            shuffle_page();
            return STOP;
        }
        /* Now Playing's ⋯: an app's page instead of stock's while the app's queue plays */
        if (np_mode_click(target, event)) return STOP; /* shuffle or repeat, one of the two */
        /* the art's tap is np_tap's (its album; a hold its full screen), never stock's own */
        if (target == st.np_cover) return STOP;
        return target == np_more() && addon_more() ? STOP : time_click(target, event);
    }
    return stock_dispatch_trampoline(target, event);
}

/* Songs' bind found no artist on the record (SONG_UNKNOWN_CALL, tools/build.py). Stock would write
 * "Unknown Artist"; the set has no text-row artwork (text_art 0), so the two-line row says only what
 * the record says and the second line stays empty. */
void ringnav_song_unknown(const char *key, char *text, int size) { (void)key, (void)text, (void)size; }

/* The set has no text-row artwork (text_art 0): a row no taller than the pitch is one line: its leading children (artwork, the playing gif; stock's choice box stays) and the
 * text's second line hide, and the title, at the set's centred y, takes their width. Stock
 * re-showing one relayouts the row, which hides it again. */
static int one_line(void *row) { return I(row, W_H) <= geom(G_PITCH); }
/* A taller row whose leading image stock gave no width (text_art 0, as Songs') is
 * two lines of text: the same children hide, its second line stays. */
static int two_lines(void *row) {
    if (I(row, W_H) <= geom(G_PITCH)) return 0;
    for (unsigned i = 0; i < widget_count_children(row); ++i) {
        void *child = widget_get_child(row, i);
        if (!tk_str_start_with(widget_get_prop_str(child, "name", ""), "img_choice"))
            return !I(child, W_W);
    }
    return 0;
}

/* A row's text after its title, however deep stock nests it (view_artist): the meta role. A theme
 * with its own face there also takes it to stock's secondary grey, the theme's muted (a style's
 * white would read as black ink in Light Mode). */
void row_meta(void *w) {
    const theme_t *T = theme();
    if (tk_strcmp(widget_get_type(w), "hscroll_label")) {
        for (unsigned i = 0;
             (T->font[ROLE_META] || T->font_px[ROLE_META]) && i < widget_count_children(w); ++i)
            row_meta(widget_get_child(w, i));
        return;
    }
    int fresh =
        (T->font[ROLE_META] || T->font_px[ROLE_META]) && widget_get_prop_int(w, "_font", -1) < 0;
    theme_font(w, ROLE_META, FONT_ROW);
    if (fresh) widget_set_prop_int(w, "style:normal:text_color", (int)SPOT_GREY);
}

/* Only audited ordinary row constructors install this per-instance layouter. Stock still
 * positions every child, skips hidden controls, and owns clone/destruction and text overflow. */
static int compact_row_layout(void *layout, void *row) {
    void *text = (void *)0, *title = (void *)0;
    /* A toolbar-less set's playlist rows hold their children in their own places: stock's layout
     * has none */
    if (widget_get_prop_int(P(row, W_PARENT), PL_ROW, 0)) {
        pl_fit(row);
        return 0;
    }
    unsigned count = widget_count_children(row);
    const theme_t *T = theme();
    int tall = T->art_x && I(row, W_H) > geom(G_PITCH), plain = two_lines(row), choice = 0;
    /* Names differ (view_info, viewinfo_N, ...); all seven constructors put the title
     * first, either directly in the row or one level inside its text container. */
    for (unsigned i = 0; i < count && !text; ++i) {
        void *child = widget_get_child(row, i);
        if (!tk_strcmp(widget_get_type(child), "hscroll_label")) text = title = child;
        for (unsigned j = 0; j < widget_count_children(child) && !text; ++j) {
            void *label = widget_get_child(child, j);
            if (!tk_strcmp(widget_get_type(label), "hscroll_label")) {
                text = child;
                title = label;
            }
        }
    }
    if (text && (plain || one_line(row))) {
        for (unsigned i = 0; i < count; ++i) {
            void *child = widget_get_child(row, i);
            if (child == text) break;
            if (!tk_str_start_with(widget_get_prop_str(child, "name", ""), "img_choice"))
                widget_set_visible(child, 0, 0);
            else if (widget_get_visible(child))
                choice = 1;
        }
        for (unsigned j = 0; !plain && text != title && j < widget_count_children(text); ++j)
            if (widget_get_child(text, j) != title)
                widget_set_visible(widget_get_child(text, j), 0, 0);
    }
    if (text && widget_get_visible(text)) {
        int width = I(row, W_W) - 2 * B(layout, DEFAULT_LAYOUT_X_MARGIN);
        if (drill(row)) /* as if a stock img_into were the last child */
            width -=
                CHEVRON_W - B(layout, DEFAULT_LAYOUT_X_MARGIN) + B(layout, DEFAULT_LAYOUT_SPACING);
        for (unsigned i = 0; i < count; ++i) {
            void *child = widget_get_child(row, i);
            if (child != text && widget_get_visible(child))
                width -= I(child, W_W) + B(layout, DEFAULT_LAYOUT_SPACING);
        }
        /* A theme's art row: its text's box on the theme's columns, hscroll_label's margin each
         * side (the move's below, after stock's layout); a two-line row's from the set's text
         * inset, unless stock's choice box shows. */
        if (tall && !plain) width = T->art_text_end - T->art_text_x + 4;
        if (plain && !choice) {
            /* stock's chevron last, as Search's Artists: the text ends where an art row's does */
            void *last = widget_get_child(row, count - 1);
            int into = last != text && widget_get_visible(last) &&
                       !tk_strcmp(widget_get_prop_str(last, "image", ""), "list_into");
            width = (into ? T->art_text_end : 0) - geom_val(V_TEXT_X) + 2;
        }
        if (width < 0) width = 0;
        widget_resize(text, width, I(text, W_H));
        /* Stock's rows scroll a long title once as the page opens, every row at a time, its
         * glyphs smeared over the moving clip; focus-only (no row takes AWTK focus) keeps the
         * ellipsis, as the selected row shows it. */
        hscroll_label_set_only_focus(title, 1);
        theme_font(title, tall ? ROLE_TALL : ROLE_TITLE, FONT_ROW);
        if (title != text) {
            int available = width - I(title, W_X);
            widget_resize(title, available > 0 ? available : 0, I(title, W_H));
            for (unsigned j = 0, after = 0; j < widget_count_children(text); ++j) {
                void *label = widget_get_child(text, j);
                if (label == title)
                    after = 1;
                else if (after) {
                    row_meta(label);
                    if (tall) { /* stock's second line is 151 px: it takes the box, as the title */
                        widget_resize(label, available > 0 ? available : 0, I(label, W_H));
                        for (unsigned k = 0; k < widget_count_children(label); ++k)
                            widget_resize(widget_get_child(label, k), available > 0 ? available : 0,
                                          I(widget_get_child(label, k), W_H));
                    }
                }
            }
        }
    }
    int ret = ((int (*)(void *, void *))((const unsigned *)DEFAULT_LAYOUT_VTABLE)[2])(layout, row);
    if (tall && text && widget_get_visible(text)) { /* stock's choice box keeps stock's places */
        void *art = 0;
        for (unsigned i = 0; i < count && widget_get_child(row, i) != text; ++i) {
            void *child = widget_get_child(row, i);
            if (!widget_get_visible(child)) continue;
            if (tk_str_start_with(widget_get_prop_str(child, "name", ""), "img_choice")) {
                art = 0;
                break;
            }
            art = art ?: child;
            widget_move_resize(child, T->art_x - I(row, W_X), I(child, W_Y), I(child, W_W),
                               I(child, W_H));
        }
        if (art)
            widget_move_resize(text, T->art_text_x - 2 - I(row, W_X), I(text, W_Y), I(text, W_W),
                               I(text, W_H));
    }
    /* The set's text inset (text_x, on screen, clear of the glass; the row may sit
     * in from it): text leading the row moves there, its right edge kept. Text after art or stock's
     * choice box keeps its place. */
    int tx = geom_val(V_TEXT_X);
    if (tx && text && I(text, W_X) <= B(layout, DEFAULT_LAYOUT_X_MARGIN)) {
        tx = tx > I(row, W_X) ? tx - I(row, W_X) : 0;
        widget_move_resize(text, tx, I(text, W_Y),
                           plain && !choice ? I(text, W_W) : I(text, W_W) - (tx - I(text, W_X)),
                           I(text, W_H));
    }
    return ret;
}

int compact_set_row_layout(void *row, const char *params) {
    static unsigned vtable[8] __attribute__((section(".scratch")));
    int ret = widget_set_children_layout(row, params);
    if (ret || !row) return ret;
    void *layout = P(row, W_CHILDREN_LAYOUT);
    if (layout) {
        if (!vtable[0]) {
            memcpy(vtable, (const void *)DEFAULT_LAYOUT_VTABLE, sizeof(vtable));
            vtable[2] = (unsigned)compact_row_layout;
        }
        P(layout, CHILDREN_LAYOUT_VTABLE) = vtable;
    }
    return ret;
}

/* Stock's own table rows (ipod.json stock_row_layout_calls), which the set re-pitches. */
int stock_set_row_layout(void *row, const char *params) {
    return compact_set_row_layout(row, params);
}

/* ... whose titles are bound with no relayout (stock_row_text_calls), so the face that has the
 * text's characters is found here. Queue's label, alone in its button at x 10, goes to the set's
 * text inset. */
int stock_row_text(void *label, const char *text) {
    int ret = widget_set_text_utf8(label, text);
    theme_font(label, ROLE_TITLE, FONT_ROW);
    if (I(label, W_X) == 10) {
        int x = geom_val(V_TEXT_X) - SET_STOCK_X;
        widget_move_resize(label, x, 0, I(label, W_W) + 10 - x, I(label, W_H));
    }
    return ret;
}

/* ... whose style stock then picks, after the title on each bind: the playing row's red, which the
 * build turns white for every theme (ipod.json), so a play mark goes before its title: every face
 * a row takes has one (tools/build.py play_mark), so the title keeps the
 * face stock_row_text gave it. It keeps its colour, as the set re-pitches the rows; the style
 * hook takes it from there, as any stock red. Inline, as the row's font is, and written on each
 * bind, as the rows are reused. */
int stock_row_style(void *label, const char *style) {
    int ret = widget_use_style(label, style), playing = !tk_strcmp(style, "s_scrlabel_red24l");
    if (playing) {
        char text[512] = "\xe2\x96\xb6 "; /* U+25B6; stock binds at most this much title */
        widget_get_text_utf8(label, text + 4, sizeof text - 4);
        widget_set_text_utf8(label, text);
    }
    widget_set_prop_int(label, "style:normal:text_color", (int)RGBA(playing ? STOCK_RED : 0xffffff));
    return ret;
}

/* More's speed row: its four 42 x 61 buttons can't take the pitch, so it stays stock's height,
 * over the set's rows. Its icon goes and its labels take the row's fonts, each box holding its
 * font and ending before the first button. */
void stock_more_speed(void *win) {
    void *first = widget_lookup(win, "img_forward_0", 1);
    if (!first) return;
    void *button = P(first, W_PARENT), *row = P(button, W_PARENT);
    int x = geom_val(V_TEXT_X) - I(button, W_X), labels = 0;
    widget_resize(row, I(row, W_W), SET_STOCK_ROW);
    for (unsigned i = 0, n = widget_count_children(button); i < n; ++i) {
        void *c = widget_get_child(button, i);
        if (I(c, W_W) == SET_STOCK_ICON) widget_set_visible(c, 0, 0);
        if (tk_strcmp(widget_get_type(c), "hscroll_label")) continue;
        if (!labels++) {
            widget_move_resize(c, x, 8, I(first, W_X) - x - 2, 28);
            theme_font(c, ROLE_META,
                       FONT_ROW); /* the row's 27 px wouldn't fit before the buttons */
        } else {
            widget_move_resize(c, x, 43, I(c, W_W), 18);
            theme_font(c, ROLE_META, FONT_ROW);
        }
    }
}

/* Settings rows (docs/ipod.md#settings). One stock child in row coordinates: full-height children
 * fill the row and shorter ones keep their centre; the icon hides (the set's set_icon 0); text starts at the corner-safe column; children from SET_RIGHT_SIDE, and wide ones'
 * right edges, move with the trailing image by `shift`, never past the text margin. */
/* The settings rows' trailing images' right edge from the row's: the set's right column (a 50 px
 * switch's glyph fills its box). */
static int set_edge(void) { return geom_val(V_RIGHT_X); }
/* ... and their text's box's left edge: the set's text column less hscroll_label's 2 px margin, so
 * the text starts on it as the list's rows do (docs/oled-design.md Spacing). */
static int set_text_x(void) { return geom_val(V_TEXT_X) - 2; }
static void set_child(void *c, int row_w, int row_h, int shift) {
    int x = I(c, W_X), y = I(c, W_Y), w = I(c, W_W), h = I(c, W_H), right = x + w;
    const theme_t *T = theme();
    /* No settings icons: any image leading the row goes, whatever its width (a paired device's
     * 50 px one), and text after one starts at the text column. A payload picker's mark (Key
     * Lock's) stays, in the column of stock's own marks. */
    if (x < SET_RIGHT_SIDE && !tk_strcmp(widget_get_type(c), "image")) {
        const char *image = widget_get_prop_str(c, "image", "");
        if (!tk_strcmp(image, "select") || !tk_strcmp(image, "unselect"))
            widget_move_resize(c, row_w - set_edge() - 50, 0, 50, row_h);
        else
            widget_set_visible(c, 0, 0);
        return;
    }
    if (h >= SET_STOCK_BODY) {
        y = 0;
        h = row_h;
    } else
        y -= (SET_STOCK_BODY - row_h) / 2;
    if (w == SET_STOCK_ICON && !tk_strcmp(widget_get_type(c), "image")) {
        widget_set_visible(c, 0, 0);
        return;
    }
    int left = x >= SET_RIGHT_SIDE                ? SET_STOCK_X + x + shift
               : x > SET_STOCK_ICON / 2 + 10 /* after a stock icon at x 10 */
                   ? set_text_x() + x - (SET_STOCK_ICON + 20)
                   : set_text_x() + x - 10;
    if (left < set_text_x()) left = set_text_x();
    right = right >= SET_RIGHT_SIDE ? SET_STOCK_X + right + shift : right + left - x;
    if (right > row_w - SET_TEXT_X) right = row_w - SET_TEXT_X;
    widget_move_resize(c, left, y, right > left ? right - left : 0, h);
    if (tk_strcmp(widget_get_type(c), "hscroll_label")) return;
    theme_font(c, ROLE_TITLE, FONT_ROW);
    /* a long title rests on its ellipsis and scrolls when its row is selected (marq_moved): stock's
     * flags, which the wheel's mode leaves off (set_hscroll_label_attribute), scroll it once on
     * every row as the page opens and never again (a Wi-Fi network's, a Bluetooth device's) */
    hscroll_label_set_only_focus(c, 1);
    /* a set_switch theme: a stock row's text on its right side is its value (About's), in the
     * value grey as list_value's; the selection's text still wins */
    unsigned ink = T->set_switch ? T->set_value_ink[ipod_light()] : 0;
    if (ink && x >= SET_RIGHT_SIDE) text_ink(c, 0xff000000u | ink, INK_LABEL);
}

/* The row's stock settings button (SET_STOCK_X, SET_STOCK_W), or none: a row already mapped, or one
 * another builder made, which keeps its own height and geometry. */
static void *set_button(void *item) {
    if (tk_strcmp(widget_get_type(item), "list_item")) return (void *)0;
    for (unsigned i = 0; i < widget_count_children(item); ++i) {
        void *b = widget_get_child(item, i);
        if (I(b, W_X) == SET_STOCK_X && I(b, W_W) == SET_STOCK_W &&
            !tk_strcmp(widget_get_type(b), "button"))
            return widget_lookup(b, "img_forward_0", 0) ? (void *)0 : b; /* stock_more_speed */
    }
    return (void *)0;
}

/* The stock button spans the row; its children follow. Once mapped it no longer matches, so a later
 * layout leaves it alone; the builders give its children no self_layout, so nothing lays them out
 * again. */
static void set_row(void *item) {
    int row_w = I(item, W_W), row_h = I(item, W_H);
    void *b = set_button(item);
    if (b) {
        int trail = SET_STOCK_TRAIL; /* the rightmost trailing image sets the right column */
        unsigned n = widget_count_children(b);
        for (unsigned j = 0; j < n; ++j) {
            void *c = widget_get_child(b, j);
            if (I(c, W_W) == 50 && I(c, W_X) >= SET_RIGHT_SIDE && I(c, W_X) > trail &&
                !tk_strcmp(widget_get_type(c), "image"))
                trail = I(c, W_X);
        }
        widget_move_resize(b, 0, 0, row_w, row_h);
        for (unsigned j = 0; j < n; ++j)
            set_child(widget_get_child(b, j), row_w, row_h,
                      row_w - set_edge() - 50 - SET_STOCK_X - trail);
    }
}

/* A settings row's height: h, or two of them where it is too short for a stock row with a detail
 * line under its title (Bluetooth's Quality), which would run into the next row. */
static int set_row_h(void *button, int h) {
    if (2 * h > SET_STOCK_BODY) return h;
    for (unsigned i = 0, n = widget_count_children(button); i < n; ++i) {
        void *c = widget_get_child(button, i);
        if (I(c, W_Y) >= SET_STOCK_BODY / 2 && !tk_strcmp(widget_get_type(c), "hscroll_label"))
            return 2 * h;
    }
    return h;
}

/* Streaming's list: settings rows at the set's stream_row, matched
 * by name, at layout time only. Its list on Home's window matches too, but its buttons are Home's
 * by then (coverflow.c home_fill), so set_button leaves them alone. */
static int stream_list(void *view) {
    return view && !tk_strcmp(widget_get_prop_str(view, "name", ""), "scroll_view_streamsset");
}

/* The list_view layouter's vtable slot. A list whose asset default_item_height is the set's
 * settings row (only the iPod settings pages) gets settings rows: items stock made SET_STOCK_ROW
 * high take it before the stock layout stacks them and sizes the scroll view, then each row is
 * mapped. Settings code never moves, resizes or scrolls its rows afterwards
 * (docs/ipod.md#settings). A set whose pitch is its settings row (OLED's) gives every re-pitched
 * list an item_height (tools/ipod.py), which stock forces on each row: a grid's rows (the artist
 * page's albums, 160 px list_items of 110 px tiles) keep their own height, or each would be one
 * pitch high, and stock's list painter, which culls a row by its own box, would skip the tiles'
 * covers and names wherever a repaint misses that box, under the selection card it paints. */
int ipod_list_layout(void *layout, void *view) {
    void *list = view ? P(view, W_PARENT) : (void *)0;
    int settings = settings_list(list);
    int row = stream_list(view) || settings ? geom_val(V_SET_ROW) : 0;
    int wifi = settings && wifi_list(list), bt = settings && bt_list(list);
    if (settings) settings_layout(list);
    if (wifi) wifi_rows_pre(view);
    if (bt) bt_rows_pre(view); /* bt_ui.c */
    unsigned n = row ? widget_count_children(view) : 0;
    for (unsigned i = 0; i < n; ++i) {
        void *item = widget_get_child(view, i);
        void *button = I(item, W_H) == SET_STOCK_ROW ? set_button(item) : (void *)0;
        if (button) I(item, W_H) = wifi && wifi_connected_row(button) ? row : set_row_h(button, row);
    }
    int forced = list ? I(list, ROW_HEIGHT) : 0, grid = 0; /* list_view's item_height */
    for (unsigned i = 0, k = forced ? widget_count_children(view) : 0; i < k && !grid; ++i) {
        void *item = widget_get_child(view, i),
             *b = widget_count_children(item) ? widget_get_child(item, 0) : (void *)0;
        grid = b && !tk_strcmp(widget_get_type(b), "button") && tile_in(b, list);
    }
    if (grid) I(list, ROW_HEIGHT) = 0; /* this layout only: settings_list reads it */
    int ret = ((int (*)(void *, void *))LIST_VIEW_LAYOUT)(layout, view);
    if (grid) I(list, ROW_HEIGHT) = forced;
    list_relaid(view); /* row_paint.c: its end padding and scroll back before a frame draws it */
    /* Stock neither places nor sizes a hidden row (no keep_invisible): mapped at 0x0 its button
     * would stay empty once shown (Display's Accent), so it is mapped after its first layout. */
    for (unsigned i = 0; i < n; ++i) {
        void *item = widget_get_child(view, i);
        if (widget_get_visible(item)) set_row(item);
    }
    if (wifi) wifi_rows_post(view);
    if (bt) bt_rows_post(view);
    if (settings && about_list(list)) about_rows(view);
    if (settings && reset_list(list)) reset_rows(view); /* settings_pages.c */
    return ret;
}

/* Replaces home_page_init's boot resume, navigator_to_with_context("playing_page", ctx) with ctx
 * {queue, index, class, mode}. Car mode (mode 2, plays) keeps that call; otherwise this is the
 * paused start playing_page_init would make, without the page. Headphones Required no longer skips
 * the resume (ipod.py nops stock's test): a paused queue plays nothing, and car mode's start is
 * asked here, as the page would (it would refuse and restore nothing), and comes back paused. */
void ringnav_boot(const char *page, const int *ctx) {
    if (ctx[3] == 2 && !check_forbid_play_no_headset(0))
        navigator_to_with_context(page, ctx);
    else if (ctx[2] != 0xff)
        player_start((void *)ctx[0], ctx[1], ctx[2], 3);
}

