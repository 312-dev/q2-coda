int usable(void) {
    if (!g_backlight_status || g_lockscreen_pageflag || g_testmode_flag || g_guideflag ||
        g_poweroff_state > 1)
        return 0;
    void *top = window_manager_get_top_window(window_manager());
    int confirm = top && !tk_strcmp(widget_get_prop_str(top, "name", ""), "confirminfo_dialog");
    /* A held power key sets g_poweroff_state to 1 while its Confirm Shutdown prompt is up
     * (on_wm_keylong_fun); 2 is the power-off page itself. */
    if (g_poweroff_state) return confirm;
    if (g_usblink_status != 2) return 1;
    /* USB mode's exit asks whether to scan the card in a confirm dialog before it leaves the mode,
     * the cable pulled out or the mode page's Stop pressed with the computer still there: that
     * prompt takes the wheel and Centre either way, its first button selected as it opens. */
    return confirm;
}

static int usb_prompt(void) {
    void *top = window_manager_get_top_window(window_manager());
    return g_usblink_status == 2 && usable() && top &&
           !tk_strcmp(widget_get_prop_str(top, "name", ""), "confirminfo_dialog");
}

static int allowed_top(void *top) {
    return top && context_id(widget_get_prop_str(top, "name", (void *)0)) >= 0;
}
int pointer_down(void) { return window_manager_get_pointer_pressed(window_manager()); }

/* A click identifies its pane by ancestry. On the first wheel turn, an unowned pair starts
 * with the first pane in UI order; painting and touch never choose one implicitly. */
static void *surface_under(void *top, void *target, void **other, int wheel) {
    void *found[2] = { (void *)0, (void *)0 };
    int count = 0, aborted = 0, budget = 512;
    find_surface(top, found, &count, &aborted, 0, &budget);
    if (aborted || count == 0) return (void *)0;
    if (count == 1) return found[0];
    if (target) {
        for (int depth = 0; target && depth < 32; ++depth) {
            for (int i = 0; i < count; ++i)
                if (target == found[i]) {
                    if (other) *other = found[1 - i];
                    return found[i];
                }
            if (target == top) break;
            target = P(target, W_PARENT);
        }
        return (void *)0;
    }
    int first = widget_get_prop_int(found[0], SEL, -1) >= 0;
    int second = widget_get_prop_int(found[1], SEL, -1) >= 0;
    if (wheel && !first && !second) return found[0];
    return first == second ? (void *)0 : (first ? found[0] : found[1]);
}

static void *surface(void *target, void **other) {
    void *wm = window_manager(), *top = window_manager_get_top_window(wm);
    void *w = usable() && !window_manager_is_animating(wm) && allowed_top(top)
                  ? surface_under(top, target, other, 0)
                  : (void *)0;
    if (st.center_timer && (top != st.center_top || w != st.center_surface)) cancel_center();
    if (!is_home(top, w) || w != st.home_surface) st.home_surface = (void *)0;
    return w;
}

void prop(void *w, const char *name, int value) {
    if (widget_get_prop_int(w, name, -1) != value) widget_set_prop_int(w, name, value);
}

/* FNV-1a of the first two non-empty text properties in a small row subtree: the item's own name
 * and an optional subtitle, in pre-order. Each stays 0 while its text has not been seen. No stock
 * list row carries a stable id: emitter tags and pointer props are unused by the app rows (only
 * ROW_INDEX, which a re-sort rewrites), so these hashes are the identity available to restore. */
typedef struct {
    unsigned one, two;
    const unsigned *title; /* the first text itself, for the iPod fast-scroll letter */
} row_id_t;

/* The library browsing state both position memory and the queue menu key on. */
static unsigned local_hash(unsigned h) {
    h = hash_bytes(h, g_class_type, 4);
    h = hash_bytes(h, g_local_classinfo_save, 912);
    return hash_bytes(h, g_artist_type, 4);
}

static void row_hash_text(const unsigned *s, unsigned *h) {
    unsigned n = 0;
    while (s[n]) ++n;
    /* Stock wchar_t is UTF-32; hash all four bytes, zero bytes inside a character included. */
    *h = hash_bytes(FNV_SEED, (const unsigned char *)s, 4 * n);
    if (!*h) *h = 1; /* zero denotes missing text */
}

static void row_id_walk(void *w, int depth, int *budget, row_id_t *id) {
    if (!w || depth == 4 || id->two || --*budget < 0) return;
    const unsigned *s = widget_get_text(w);
    if (s && *s) {
        if (!id->one) {
            row_hash_text(s, &id->one);
            id->title = s;
        } else {
            row_hash_text(s, &id->two);
            return;
        }
    }
    unsigned n = widget_count_children(w);
    for (unsigned i = 0; i < n; ++i) row_id_walk(widget_get_child(w, i), depth + 1, budget, id);
}

static row_id_t row_id(void *w) {
    row_id_t id = { 0, 0, (void *)0 };
    int budget = 32;
    row_id_walk(w, 0, &budget, &id);
    return id;
}

/* The local list loaders use these browsing globals: folder_enter/back maintain g_folder_path;
 * load_localclass_list/load_album_detaillist use the class, saved query and artist/album modes.
 * Hash the bounded query object, including its flags, rather than a title or a freed pointer. */
static int context_now(unsigned *scope) {
    void *top = window_manager_get_top_window(window_manager());
    const char *name = top ? widget_get_prop_str(top, "name", (void *)0) : (void *)0;
    *scope = 0;
    if (!name) return -1;
    int ctx = context_id(name);
    if (ctx < 0) return -1;
    void *found[2];
    int count = 0, aborted = 0, budget = 512;
    find_surface(top, found, &count, &aborted, 0, &budget);
    if (contexts[ctx].kind == CTX_FOLDER) {
        unsigned n = 0;
        while (n < 1024 && g_folder_path[n]) ++n;
        if (!n || n == 1024) return -1;
        *scope = hash_bytes(FNV_SEED, g_folder_path, n);
    } else if (!tk_strcmp(name, "playlist_page")) {
        /* The list is the same whatever opening a row saves in the browsing state, so back keeps
         * the row; the Add to Playlist picker keeps its own. */
        *scope = (unsigned)widget_get_prop_int(top, PL_MODE, 0) + 1;
    } else if (contexts[ctx].kind == CTX_LOCAL) {
        unsigned h = local_hash(FNV_SEED);
        int cls = I(g_class_type, 0);
        /* Root collections ignore the saved detail query, which opening an artist overwrites. */
        if (!tk_strcmp(name, "localclass_page") && cls >= CLASS_ALBUMS && cls <= 0xf006) {
            h = hash_bytes(FNV_SEED, g_class_type, 4);
            h = hash_bytes(h, g_artist_type, 4);
        }
        *scope = hash_bytes(h, album_modetype, 4);
    } else if (contexts[ctx].kind == CTX_FIXED) {
        /* a stock page opened again as a sub-page of its own (Display's, Library's) keeps its own
         */
        *scope = 1 + (unsigned)widget_get_prop_int(top, SUB_PAGE, 0);
    } else if (!tk_strcmp(name, "coverflow_page")) {
        *scope = coverflow_scope(top);
        if (!*scope) return -1;
    } else {
        if (!tk_strcmp(name, "search_dialog") || !tk_strcmp(name, "tidal_search_dialog")) {
            void *edit = widget_lookup(top, "search_edit", 1);
            row_id_t query = row_id(edit);
            *scope = query.one;
        }
        /* Network/detail pages without an audited content key keep widget-owned selection, but
         * never import another page's row. */
        return -1;
    }
    if (!*scope) *scope = 1;
    return !aborted && count == 1 ? ctx : -1; /* no cross-pane position memory */
}

static int index_of(menu_t *m, int id);

/* Bounded recency order avoids a timestamp that could wrap during a long session. */
static int position(menu_t *m) {
    if (m->ctx >= 0)
        for (int i = 0; i < POS_MEM; ++i)
            if (st.pos[i].id && st.pos[i].ctx == m->ctx && st.pos[i].scope == m->scope) return i;
    return -1;
}

/* Remember where the user was. Widget props die with a recreated page; this survives it.
 * Non-virtual lists also remember the row's first two text values, so a reordered list restores
 * the same item and duplicate names can be told apart by their second line. */
static void select(menu_t *m, int id) {
    st.shuffle_on = (void *)0; /* a row takes the selection from the Shuffle row */
    if (st.center_timer && (m->w != st.center_surface || id != st.center_id)) cancel_center();
    if (st.reveal_surface == m->w) st.reveal_surface = (void *)0;
    unsigned hash = 0, hash2 = 0;
    if (id >= 0) {
        int ctx = m->ctx;
        if (m->kind == 1) {
            int i = index_of(m, id);
            if (i >= 0) {
                row_id_t r = row_id(m->at[i]);
                hash = r.one;
                hash2 = r.two;
            }
        }
        if (ctx >= 0) {
            int p = position(m);
            if (p < 0) p = POS_MEM - 1;
            for (; p > 0; --p) st.pos[p] = st.pos[p - 1];
            st.pos[0] = (position_t){ ctx, id + 1, m->scope, hash, hash2 };
        }
    }
    prop(m->w, SEL, id);
}

/* A reopen_top theme (OLED): Return out of a list drops its remembered
 * row, so choosing it again opens it at the top as an iPod's menus do; the page Return lands on
 * keeps its own (docs/internals.md#position-memory). Runs on Return's release before stock leaves
 * the page, so context_now still names the list being left. */
static void forget_left(void) {
    unsigned scope;
    int ctx = context_now(&scope);
    if (ctx < 0) return;
    for (int i = 0, j = 0; i < POS_MEM; ++i) {
        position_t p = st.pos[i];
        st.pos[i] = (position_t){ 0 };
        if (p.id && !(p.ctx == ctx && p.scope == scope)) st.pos[j++] = p;
    }
}

int load_rows(menu_t *m, void *w) {
    m->w = w;
    m->n = 0;
    m->kind = kind(w);
    m->height = I(w, W_H);
    if (!m->kind || m->height <= 0) return 0;
    m->row = m->kind == 2 ? I(w, ROW_HEIGHT) : 0;
    m->rows = m->kind == 2 ? I(w, TABLE_ROWS) : 0;
    if (m->kind == 2 && (m->row <= 0 || m->rows < 0 || m->rows > 0x7fffffff / m->row)) return 0;
    unsigned n = widget_count_children(w);
    if (m->kind == 1 && I(w, VIEW_CONTENT_H) < 0) return 0;
    if (m->kind == 1 || m->kind == 4) {
        entries_t s = { m->at, 0, MAX_ENTRIES, 4096 };
        for (unsigned i = 0; i < n && s.n < s.cap && s.budget >= 0; ++i)
            collect(widget_get_child(w, i), &s, 1);
        m->n = s.n;
        m->rows = s.n;
        for (int i = 0; i < m->n; ++i) m->id[i] = i;
    } else {
        if (m->kind == 3) m->rows = slide_count(w); /* Cover Flow's children stand for more */
        for (unsigned i = 0; i < n && m->n < MAX_ENTRIES; ++i) {
            void *r = widget_get_child(w, i), *e = (void *)0;
            entries_t s = { &e, 0, 1, 256 };
            collect(r, &s, 0);
            int id = m->kind == 2   ? I(r, ROW_INDEX)
                     : m->kind == 3 ? slide_child_index(w, (int)i)
                                    : (int)i;
            if (e && id >= 0 && id < m->rows) {
                m->at[m->n] = e;
                m->id[m->n++] = id;
            }
        }
    }
    return 1;
}

/* A row holding stock's "select" check image: a CHOICE picker's current option. */
static int checked(void *w, int depth, int *budget) {
    if (!w || depth == 16 || --*budget < 0 || !widget_get_visible(w)) return 0;
    if (!tk_strcmp(widget_get_prop_str(w, "image", ""), "select")) return 1;
    unsigned n = widget_count_children(w);
    for (unsigned i = 0; i < n; ++i)
        if (checked(widget_get_child(w, i), depth + 1, budget)) return 1;
    return 0;
}

static int checked_row(menu_t *m) {
    for (int i = 0; i < m->n; ++i) {
        int budget = 256;
        if (checked(m->at[i], 0, &budget)) return m->id[i];
    }
    return -1;
}

static int load(menu_t *m, void *w, int recall) {
    if (!load_rows(m, w)) return 0;
    m->ctx = context_now(&m->scope);
    int count = widget_get_prop_int(w, COUNT, -1);
    unsigned scope = (unsigned)widget_get_prop_int(w, SCOPE, 0);
    if (scope != m->scope) {
        count = -1;
        prop(w, SCOPE, (int)m->scope);
        if (st.reveal_surface == w) st.reveal_surface = (void *)0;
    }
    if (count != m->rows) {
        if (st.wheel_surface == w) drop_wheel();
        prop(w, SEL, -1);
        prop(w, COUNT, m->rows);
        /* A drag stops at the list's ends, as the wheel does: AWTK's default lets it run on past
         * them by half the view and springs back on release (docs/internals.md#list-ends). */
        if (m->kind == 1) widget_set_prop_int(w, "slide_limit_ratio", 0);
    }
    /* A recreated page has never seen this surface (COUNT unset) and lost its selection: put the
     * user back where they left off. A live page whose count changed resets the selection but
     * keeps its viewport, so it does not re-read the table. A non-virtual list prefers the
     * remembered first text, breaks ties by the second text and then by the remembered index,
     * and falls back to the index when no text matches. */
    if (m->kind != 3 && count < 0 && (recall || theme()->set_text)) {
        /* A picker opens on its current option rather than the row last chosen; a set_text
         * theme's also while a tap that opened it is still down, or a Centre takes its first
         * row. */
        void *top = window_manager_get_top_window(window_manager());
        int ctx = top ? context_id(widget_get_prop_str(top, "name", (void *)0)) : -1;
        int choice = ctx >= 0 && (contexts[ctx].flags & CHOICE) ? checked_row(m) : -1;
        int p = choice >= 0 || !recall ? -1 : position(m);
        int id = choice >= 0 ? choice : p < 0 ? -1 : st.pos[p].id - 1;
        unsigned hash = p < 0 ? 0 : st.pos[p].hash;
        unsigned hash2 = p < 0 ? 0 : st.pos[p].hash2;
        if (id >= 0 && m->kind == 1 && hash) {
            /* A secondary-text match outranks proximity; equal ranks keep the earlier row. */
            int best = -1, best_dist = 0, best_second = 0;
            for (int i = 0; i < m->n; ++i) {
                row_id_t r = row_id(m->at[i]);
                if (r.one != hash) continue;
                int dist = m->id[i] - id;
                if (dist < 0) dist = -dist;
                int second = hash2 && r.two == hash2;
                if (best < 0 || second > best_second ||
                    (second == best_second && dist < best_dist)) {
                    best = i;
                    best_dist = dist;
                    best_second = second;
                }
            }
            if (best >= 0) id = m->id[best];
        }
        if (id >= 0 && id < m->rows) {
            prop(w, SEL, id);
            /* A sliding page's first paint is its snapshot: arrive in place, not mid-glide. */
            reveal(m, id, slides(window_manager_get_top_window(window_manager())));
            st.reveal_surface = m->w;
            st.reveal_id = id;
            /* A synchronous table rebind changes the row pool; discard the pre-scroll snapshot. */
            if (m->kind == 2) return load_rows(m, w);
        }
    }
    return 1;
}

/* Live viewport offset. reveal glides are relative, so every delta is computed against the
 * position the widget is actually at, never an intended one. */
static int view_top(menu_t *m) {
    return m->kind == 2 ? I(m->w, TABLE_TOP) : m->kind == 1 ? I(m->w, SCROLL_Y) : 0;
}

/* The height a selected row scrolls in: the surface's, cut at the set's list end on the screen
 * (list_fit_end; W_Y summed up to the window manager), where the rows below draw on to the glass
 * but a selected one's text would not show. */
static int sel_height(menu_t *m) {
    int y = 0, end = list_fit_end(), rest = home_rest(m->w, (int *)0);
    /* Home's main list rests on its rows' height, whatever runs on below (home.c home_rest) */
    if (rest) return rest < m->height ? rest : m->height;
    /* a menu sheet's rows end on the list end, the screen's top theirs (menu_modal.c) */
    if (modal_rows_of(m->w)) return modal_sel_height(m->w, m->height);
    /* the confirmation card's answers stand whole inside the card, wherever it sits: cut at the
     * list end, which crosses the card's answers, the second answer would scroll the first under
     * the bar and the wheel could never light it (feedback.c confirm) */
    if (confirm_surface(m->w)) return m->height;
    if (m->kind == 3 || m->kind == 4) return m->height;
    void *wm = window_manager();
    for (void *p = m->w; p && p != wm; p = P(p, W_PARENT)) y += I(p, W_Y);
    end -= lr_edit_room(m->w); /* an editor's list ends higher, over its hint (library_edit.c) */
    return end - y < m->height ? end - y : m->height;
}

/* sel_height's cut for a row that grows in place (settings_pages.c cap_step, inline_slider.c
 * heights): view's height, cut at the set's list end on the screen. The view itself runs on to the
 * screen's foot under OLED, so a grown row kept inside its whole height stood in the glass's
 * bottom corners. row_paint.c list_grown_foot scrolls a grown row inside it. */
int list_shown_h(void *view) {
    int y = 0, h = I(view, W_H), end = list_fit_end() - lr_edit_room(view);
    void *wm = window_manager();
    for (void *p = view; p && p != wm; p = P(p, W_PARENT)) y += I(p, W_Y);
    return end - y < h ? end - y : h;
}

/* Largest viewport top that still shows content; the clamp bound for every glide. */
static int list_pad(void *w);
static void list_fade(void *w, void *canvas);
static int scroll_end(menu_t *m, int height) {
    int foot;
    if (m->kind == 2) return m->rows * m->row - height;
    if (m->kind == 1 && home_rest(m->w, &foot)) return foot - height; /* its shown rows' */
    return m->kind == 1 ? I(m->w, VIEW_CONTENT_H) - list_pad(m->w) - height : 0;
}
static int max_top(menu_t *m) { return scroll_end(m, sel_height(m)); }

static int speed_stop(void *e) {
    return !tk_strcmp(widget_get_type(e), "image") &&
           !tk_strcmp(widget_get_prop_str(P(e, W_PARENT), "style", ""), "s_btn_forwardspeed");
}
static rect_t bounds(menu_t *m, int i) {
    void *e = m->at[i];
    rect_t r = { 0, 0, I(e, W_W), I(e, W_H) };
    /* Speed images have 61px hit targets, but their visible buttons are only 24px high. */
    if (speed_stop(e) && r.h > 24) r.h = 24;
    for (void *p = e; p && p != m->w; p = P(p, W_PARENT)) {
        r.x += I(p, W_X);
        r.y += I(p, W_Y);
    }
    if (m->kind != 3) r.y -= view_top(m);
    if (m->kind == 1) r.x -= I(m->w, SCROLL_X);
    return r;
}

static int index_of(menu_t *m, int id) {
    for (int i = 0; i < m->n; ++i)
        if (m->id[i] == id) return i;
    return -1;
}

static int moving(menu_t *m) {
    return m->kind == 1   ? P(m->w, VIEW_ANIMATOR) != 0
           : m->kind == 2 ? P(m->w, TABLE_ANIMATOR) != 0
                          : 0;
}

static void stop_scroll(menu_t *m) {
    if (m->kind == 2)
        table_client_stop_animator_scroll(m->w);
    else if (m->kind == 1 && P(m->w, VIEW_ANIMATOR)) {
        /* Same pause/destroy/null sequence as stock table_client_stop_animator_scroll. */
        void *a = P(m->w, VIEW_ANIMATOR);
        widget_animator_pause(a);
        widget_animator_destroy(a);
        P(m->w, VIEW_ANIMATOR) = (void *)0;
    }
}

/* View w's scroll animation stopped, for a touch claimed sideways (addon.c swipe, as pull_event).
 */
void list_stop_scroll(void *w) {
    if (load_rows(&g_menu, w)) stop_scroll(&g_menu);
}

/* Wheel offsets are synchronous; table setters can replace the recycled row pool. Scroll end
 * loads the covers in view, as after a touch scroll. */
static void wheel_offset(menu_t *m, int top) {
    stop_scroll(m);
    if (m->kind == 2) {
        table_client_set_yoffset(m->w, top);
        load_rows(m, m->w);
    } else if (m->kind == 1) {
        scroll_view_set_offset(m->w, I(m->w, SCROLL_X), top);
        /* The immediate setter skips the on_scroll callback that touch scrolling runs. */
        int (*on_scroll)(void *, int, int) = P(m->w, VIEW_ON_SCROLL);
        if (on_scroll) on_scroll(m->w, I(m->w, SCROLL_X), I(m->w, SCROLL_Y));
        widget_dispatch_simple_event(m->w, EVT_SCROLL);
    } else
        return;
    widget_dispatch_simple_event(m->w, EVT_SCROLL_END);
}

/* A reopen_top theme's list that outlives its Return, hidden rather than closed (Home's Library,
 * Streaming and Settings lists, coverflow.c coverflow_home_back): position memory doesn't key
 * Home, so the row its view keeps (SEL) goes and the view scrolls back to its top, where it opens
 * next. list: a list_view, its scroll view the first child (coverflow.c home_fill's shape). */
void forget_rows(void *list) {
    void *view = widget_count_children(list) ? widget_get_child(list, 0) : (void *)0;
    menu_t m;
    if (!view) return;
    prop(view, SEL, -1);
    if (load_rows(&m, view)) wheel_offset(&m, 0);
}

/* A list holds whole rows (OLED's lists), so a view stops with a row's top at its own: want, or
 * where want would cut a row, the first row's top past it. A row of another pitch (Power's 12 px
 * gap before Restart) can put the end, the content less the view, inside a row, which would show
 * cut at the top; there the content grows to that row's top plus the view, as lyric_pad's does, so
 * stock's own clamp (a drag's end, a relayout's) keeps it until a relayout puts the height back. */
static int fit_top(menu_t *m, int want) {
    int top = view_top(m), best = -1, cut = 0;
    /* a menu sheet's rows pass under its top fade, so its last row ends on the list end
     * (menu_modal.c) */
    if (m->kind != 1 || modal_rows_of(m->w)) return want;
    for (int i = 0; i < m->n; ++i) {
        rect_t r = bounds(m, i);
        int y = r.y + top;
        cut |= y < want && want < y + r.h;
        if (y >= want && (best < 0 || y < best)) best = y;
    }
    if (!cut || best < 0) return want; /* a header above the rows stays */
    if (I(m->w, VIEW_CONTENT_H) < best + I(m->w, W_H))
        I(m->w, VIEW_CONTENT_H) = best + I(m->w, W_H);
    return best;
}

/* Least viewport move that reveals logical row id, whole.
 * Remembered-position restoration keeps its glide; wheel steps are immediate. */
void reveal(menu_t *m, int id, int immediate) {
    int top = view_top(m);
    int y, h;
    if (m->kind == 2) {
        y = id * m->row;
        h = m->row;
    } else {
        int i = index_of(m, id);
        if (i < 0) return;
        rect_t r = bounds(m, i);
        y = r.y + top;
        h = r.h;
    }
    /* A tall row cannot fit: show its title consistently instead of alternating edges. The list
     * holds whole rows and keeps them whole: no margin, so it scrolls a row at
     * a time. */
    int height = sel_height(m);
    int want = h > height || y < top ? y : y - top > height - h ? y - (height - h) : top;
    /* The first row brings in whatever heads the list above it (the EQ page's graph) when both
     * fit. Kind 1's ids are its row order. */
    if (m->kind == 1 && id == 0 && y + h <= height) want = 0;
    want = fit_top(m, clamp_step(want, max_top(m), 0));
    int glide = motion_ms(theme()->recall_ms, MO_SPATIAL);
    if (immediate || !glide) {
        wheel_offset(m, want);
        return;
    }
    if (want == top) return;
    /* Stock scroll views cannot retarget an animator whose old goal is a boundary. */
    stop_scroll(m);
    if (m->kind == 2) {
        table_client_scroll_to(m->w, want, glide);
    } else
        scroll_view_scroll_delta_to(m->w, 0, want - top, glide);
}

/* The A-Z index's jump (az_index.c): table w's row id selected and at the top of the list, as far
 * as the list's end allows, with the bar showing. */
void ringnav_jump(void *w, int id) {
    if (!load(&g_menu, w, 0) || g_menu.kind != 2 || id < 0 || id >= g_menu.rows) return;
    st.touch_mode = 0;
    select(&g_menu, id);
    wheel_offset(&g_menu, clamp_step(id * g_menu.row, max_top(&g_menu), 0));
    widget_invalidate_force(w, (void *)0);
}

/* A payload page's own row: establish its scope and viewport before paint can recall another. */
void ringnav_select(void *w, int id, int rows) {
    /* A list under another window (a card app's pages still arriving) keeps the props alone: its
     * load would take g_menu and the top list's glide and big letter from under the wheel. */
    void *win = window_of(w);
    int loaded =
        (!win || win == window_manager_get_top_window(window_manager())) && load(&g_menu, w, 0);
    prop(w, COUNT, rows);
    prop(w, SEL, id);
    if (loaded) reveal(&g_menu, id, 1);
}

/* Keep selection during native momentum and recall glides. Once settled, repair an offscreen
 * selection with the visible row nearest the viewport centre (ties go to the earlier row), so a
 * swipe never leaves the highlight pinned to the top edge. An interrupted recall glide is
 * retried once instead: the remembered row must not be silently replaced by a visible one. */
static int reconcile(menu_t *m, int settle) {
    int id = m->kind == 3 ? slide_index(m->w) : widget_get_prop_int(m->w, SEL, -1);
    int cur = index_of(m, id);
    if (m->kind == 3) return cur;
    /* Home shows its bar under touch, so once a drag settles its selected row stands whole where
     * the wheel would leave it (sel_height: Home's main list's rest), never under the status row
     * or in the fade past the rest (docs/oled-design.md#list-edges); elsewhere the bar is hidden
     * until the wheel, which reveals the row, so a row partly in sight keeps it. */
    int home = settle && home_menu(window_manager_get_top_window(window_manager())),
        shown = home ? sel_height(m) : m->height;
    if (cur >= 0) {
        rect_t r = bounds(m, cur);
        if (home ? r.y >= 0 && r.y + r.h <= shown : r.y < m->height && r.y + r.h > 0) {
            st.reveal_surface = (void *)0; /* the glide arrived, or the user brought it back */
            return cur;
        }
        if (!settle) return cur;
    } else if (id >= 0 && id < m->rows && !settle)
        return -1;
    if (st.reveal_surface == m->w && st.reveal_id == id) {
        st.reveal_surface = (void *)0;
        reveal(m, id, 0);
        if (m->kind == 2) return load_rows(m, m->w) ? index_of(m, id) : -1;
        return cur;
    }
    int best = -1, best_dist = 0, partial = -1, partial_dist = 0, had = id >= 0;
    for (int i = 0; i < m->n; ++i) {
        rect_t r = bounds(m, i);
        if (r.y >= m->height || r.y + r.h <= 0) continue;
        int dist = r.y + r.h / 2 - m->height / 2;
        if (dist < 0) dist = -dist;
        if (r.y >= 0 && r.y + r.h <= m->height) {
            if (best < 0 || (had && dist < best_dist)) {
                best = i;
                best_dist = dist;
            }
            if (!had) break; /* a fresh page starts on the first visible row */
        } else if (partial < 0 || (had && dist < partial_dist)) {
            partial = i;
            partial_dist = dist;
        }
    }
    cur = best >= 0 ? best : partial;
    int to = !had && cur >= 0 ? pl_first(m, cur) : cur, moved = to != cur;
    cur = to;
    st.reveal_surface = (void *)0;
    select(m, cur >= 0 ? m->id[cur] : -1);
    if (moved) reveal(m, m->id[cur], 1); /* a playlist just made may be below the view */
    return cur;
}

/* Resolve only live rows. Widget-owned tokens reject a recreated window/surface even when
 * the allocator reuses its address; text hashes reject a rebound item at the same index. */
#define CONFIRM "_ringnav_confirm"
static int pending_matches(void *top, menu_t *m) {
    int id = m->kind == 3 ? slide_index(m->w) : widget_get_prop_int(m->w, SEL, -1);
    int i = index_of(m, id);
    if (top != st.center_top || m->w != st.center_surface || m->scope != st.center_scope ||
        m->ctx != st.center_ctx || m->rows != st.center_rows || id != st.center_id || i < 0 ||
        (unsigned)widget_get_prop_int(top, CONFIRM, 0) != st.center_token ||
        (unsigned)widget_get_prop_int(m->w, CONFIRM, 0) != st.center_token)
        return 0;
    /* Ordinary rows must still be the armed widget, even with duplicate/missing text.
     * Virtual tables instead resolve the logical row across recycled pool widgets. */
    if (m->kind != 2 && (unsigned)widget_get_prop_int(m->at[i], CONFIRM, 0) != st.center_token)
        return 0;
    row_id_t r = row_id(m->at[i]);
    return r.one == st.center_hash && r.two == st.center_hash2;
}

static int confirm_center(const void *info) {
    void *w = surface((void *)0, (void *)0);
    if (!st.center_timer) return 0;
    /* Stock removes this one-shot after return; never repeat (RET_REPEAT=8). */
    st.center_timer = 0;
    void *top = window_manager_get_top_window(window_manager());
    int valid = w && !pointer_down() && !g_power_longkey && !g_ingore_bootkey_flag &&
                !*(volatile unsigned char *)BOOT_KEY_GUARD && load_rows(&g_menu, w);
    if (valid) {
        /* Validation must not recall/rebind a changed scope or consume its pending recall. */
        g_menu.ctx = context_now(&g_menu.scope);
        valid = pending_matches(top, &g_menu);
    }
    cancel_center(); /* Clear before any app callback can destroy or navigate the page. */
    if (valid) {
        /* Stock's wheel lockout after the centre key (up to 800 ms) has guarded the press; the
         * page it opens scrolls at once. */
        *(volatile unsigned char *)KEY_LOCKOUT = 0;
        void *target = g_menu.at[index_of(&g_menu, st.center_id)];
        char click[0x30];
        void *e = pointer_event_init(click, EVT_CLICK, target, 0, 0);
        if (!library_row_wait(target, e)) stock_dispatch_trampoline(target, e);
    }
    return 0;
}

#define PULL_BOUND "_pull_bound"
#define PULL_SUPPRESS "_pull_suppress"
#define PULL_PROMPT "_pull_prompt"

