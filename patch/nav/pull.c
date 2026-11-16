void pull_cancel(void) {
    void *page = st.pull_page;
    st.pull_page = st.pull_surface = (void *)0;
    st.pull_claimed = 0;
    /* Stock widget_set_visible rejects a NULL widget; an absent prompt needs no local guard. */
    if (page) widget_set_visible(widget_lookup(page, PULL_PROMPT, 1), 0, 0);
}

static int pull_live(void) {
    unsigned scope;
    if (!st.pull_page) return 0;
    void *wm = window_manager();
    if (!usable() || window_manager_is_animating(wm) ||
        window_manager_get_top_window(wm) != st.pull_page)
        return 0;
    context_now(&scope);
    return scope == st.pull_scope;
}

/* A white-on-black line across page, floating, that touches and keys pass through; 0 if it can't be
 * made. */
static void *band_create(void *page, const char *name, int y, int h) {
    void *band = label_create(page, 0, y, I(page, W_W), h);
    if (band) {
        widget_set_name(band, name);
        widget_use_style(band, "s_label_white18c");
        widget_set_enable(band, 0);
        widget_set_sensitive(band, 0);
        prop(band, "floating", 1);
        prop(band, "style:disable:bg_color", (int)0xff000000u);
        prop(band, "style:disable:text_color", (int)0xffffffffu);
    }
    return band;
}

static int pull_event(void *page, void *event) {
    if (page != st.pull_page) return 0;
    unsigned type = I(event, EVENT_TYPE);
    if (type == EVT_DESTROY || type == EVT_WINDOW_BACKGROUND || type == EVT_WINDOW_CLOSE ||
        type == EVT_POINTER_ABORT || !pull_live()) {
        pull_cancel();
        return 0;
    }
    int dx = I(event, EVENT_X) - st.pull_x;
    int dy = I(event, EVENT_Y) - st.pull_y;
    if (dx < 0) dx = -dx;
    int release = type == EVT_POINTER_UP_BEFORE;
    if (!st.pull_claimed) {
        if (release || (dx >= PULL_CLAIM_PX && dx >= dy) || dy <= -PULL_CLAIM_PX) {
            pull_cancel();
            return 0;
        }
        if (dy < PULL_CLAIM_PX) return 0;
        st.pull_claimed = 1;
        prop(st.pull_surface, PULL_SUPPRESS, 1);
        /* Abort the native pressed targets before swallowing move/up; native widgets clear
         * their pressed state and grabs. A canceled search must not turn into a row click. */
        unsigned abort_event[12];
        pointer_event_init(abort_event, EVT_POINTER_ABORT, page, st.pull_x, st.pull_y);
        widget_dispatch_event_to_target_recursive(page, abort_event);
        if (load_rows(&g_menu, st.pull_surface)) stop_scroll(&g_menu);
    }
    int ready = dy >= PULL_SEARCH_PX && dy > dx;
    if (release) {
        pull_cancel(); /* clear before stock search can navigate or destroy anything */
        if (ready) {
            if (!tk_strcmp(widget_get_prop_str(page, "name", ""), "radio_page"))
                radio_search();
            else
                library_search_open(page, event);
        }
        return STOP;
    }
    void *prompt = widget_lookup(page, PULL_PROMPT, 1);
    if (!prompt) prompt = band_create(page, PULL_PROMPT, 0, 44);
    if (prompt) {
        widget_set_text_utf8(prompt, ready ? "Release to search" : "Pull to search");
        widget_set_visible(prompt, dy > 0, 0);
    }
    return STOP;
}

static void pull_begin(void *event) {
    pull_cancel();
    if (!event || I(event, EVENT_Y) < QUICK_EDGE_PX) return;
    void *page = window_manager_get_top_window(window_manager());
    if (!page) return;
    /* Local Songs' is the Library's search (library_search_open); Internet Radio's is its own. A
     * set with home_lib shows the Library on Home's window, in Split, whose own lists take no pull.
     */
    const char *name = widget_get_prop_str(page, "name", "");
    int home = !tk_strcmp(name, "home_page");
    if (!home && tk_strcmp(name, "radio_page") && tk_strcmp(name, "localmusic_page")) return;
    void *w = surface((void *)0, (void *)0);
    if (!w) return;
    if (home && tk_strcmp(widget_get_prop_str(w, "name", ""), "scroll_view_localmusic")) return;
    prop(w, PULL_SUPPRESS, 0);
    int xy[2] = { I(event, EVENT_X), I(event, EVENT_Y) };
    widget_to_local(w, xy);
    if (xy[0] < 0 || xy[1] < 0 || xy[0] >= I(w, W_W) || xy[1] >= I(w, W_H) ||
        I(w, kind(w) == 2 ? TABLE_TOP : SCROLL_Y) > 0)
        return;
    if (!widget_get_prop_int(page, PULL_BOUND, 0)) {
        const unsigned types[] = {
            EVT_DESTROY,       EVT_WINDOW_BACKGROUND,   EVT_WINDOW_CLOSE,
            EVT_POINTER_ABORT, EVT_POINTER_MOVE_BEFORE, EVT_POINTER_UP_BEFORE
        };
        for (unsigned i = 0; i < sizeof types / sizeof *types; ++i)
            if (!widget_on(page, types[i], pull_event, page)) {
                /* Retry must not duplicate the callbacks already installed. */
                while (i) widget_off_by_func(page, types[--i], pull_event, page);
                return;
            }
        prop(page, PULL_BOUND, 1);
    }
    if (!widget_get_prop_int(w, PULL_BOUND, 0)) {
        if (!widget_on(w, EVT_DESTROY, pull_event, page)) return;
        prop(w, PULL_BOUND, 1);
    }
    st.pull_page = page;
    st.pull_surface = w;
    st.pull_x = I(event, EVENT_X);
    st.pull_y = I(event, EVENT_Y);
    context_now(&st.pull_scope);
}

