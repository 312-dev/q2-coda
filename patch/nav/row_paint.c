/* A pill's end radius on a list row (h tall, its card inset px from the row's left): h / 2, less
 * where the round end would cut across a leading cover's square corner (search results' art, which
 * sits closer to the edge than the Library's), so the cover stays inside the card.
 * ponytail: only the row's direct, non-centred image children on the left; nested or right-hand
 * art is not checked. */
static int pill_radius(void *row, int h, int inset) {
    int r = h / 2;
    for (unsigned i = 0; row && i < widget_count_children(row); ++i) {
        void *c = widget_get_child(row, i);
        /* icons: discs; hidden: Folder's multi-select check, full height at its left */
        if (tk_strcmp(widget_get_type(c), "image") || !widget_get_visible(c) ||
            widget_get_prop_int(c, "draw_type", 0) == IMAGE_DRAW_CENTER)
            continue;
        int x = I(c, W_X) - inset, y = I(c, W_Y), ch = I(c, W_H);
        if (I(c, W_W) <= 0 || ch <= 0 || x < 0 || x >= r) continue;
        if (h - y - ch < y) y = h - y - ch; /* the nearer of its top and bottom corners */
        while (r > 2 && y >= 0 && y < r && (r - x) * (r - x) + (r - y) * (r - y) > r * r) --r;
    }
    return r;
}

/* A vertical gradient in one-pixel bands, then a one-pixel top highlight; equal ends give a solid
 * fill. r.h is at least 2. A pill (radius, at most r.h / 2) rounds the bands at either end to that
 * radius's corners, each corner pixel the band's colour at its coverage (corner_cover.c); 0 is
 * square. Plain fills keep this off the stock gradient_t ABI, which is not audited. */
static void gradient(void *canvas, rect_t r, unsigned top, unsigned bottom, unsigned hi,
                     int radius) {
    const unsigned char *cover = radius > 0 ? corner_cover(radius) : (const unsigned char *)0;
    for (int j = 0; j < r.h; ++j) {
        int row = j < radius ? j : j >= r.h - radius ? r.h - 1 - j : -1, in = 0;
        const unsigned char *c =
            cover && row >= 0 ? cover + row * radius : (const unsigned char *)0;
        while (c && in < radius && c[in] < 255)
            ++in; /* the part and clear pixels in from each end */
        unsigned color = RGBA(j ? mix(top, bottom, j, r.h - 1) : hi);
        canvas_set_fill_color(canvas, color);
        canvas_fill_rect(canvas, r.x + in, r.y + j, r.w - 2 * in, 1);
        for (int k = 0; k < in; ++k) {
            if (!c[k]) continue;
            canvas_set_fill_color(canvas, (color & 0xffffff) | (unsigned)c[k] << 24);
            canvas_fill_rect(canvas, r.x + k, r.y + j, 1, 1);
            canvas_fill_rect(canvas, r.x + r.w - 1 - k, r.y + j, 1, 1);
        }
    }
}

/* How far a rounded box's row i starts in from its side: a hard corner. */
int round_inset(int R, int i, int h) {
    return i < R ? arc_out(R, i) >> 4 : i >= h - R ? arc_out(R, h - 1 - i) >> 4 : 0;
}
/* R's corner pixels' coverage out of 255 (corner_cover.c), at any radius; only a failed heap
 * gives none, and a hard corner. */
static const unsigned char *cover_of(int R) { return corner_cover(R); }
/* The corner's row r's row i falls in, -1 between the corners. */
static int corner_row(const rect_t *r, int R, int i) {
    return i < R ? i : i >= r->h - R ? r->h - 1 - i : -1;
}
/* Row i of r, its corners rounded by R and antialiased, in a final 0xAARRGGBB: each corner pixel
 * the colour at its coverage of the colour's alpha. Only the ends named are rounded (ROUND_LEFT,
 * ROUND_RIGHT); the other side is square. */
static void round_row_ends(void *canvas, const rect_t *r, int R, int i, unsigned c, int ends) {
    const unsigned char *cover = cover_of(R);
    int row = corner_row(r, R, i), n = cover ? 0 : round_inset(R, i, r->h);
    for (; cover && row >= 0 && n < R && cover[row * R + n] < 255; ++n) {
        unsigned a = cover[row * R + n] * (c >> 24) / 255;
        if (!a) continue;
        if (ends & ROUND_LEFT) scrub_row(canvas, a << 24 | (c & 0xffffff), r->x + n, r->y + i, 1);
        if (ends & ROUND_RIGHT)
            scrub_row(canvas, a << 24 | (c & 0xffffff), r->x + r->w - 1 - n, r->y + i, 1);
    }
    int l = ends & ROUND_LEFT ? n : 0, rr = ends & ROUND_RIGHT ? n : 0;
    if (r->w > l + rr) scrub_row(canvas, c, r->x + l, r->y + i, r->w - l - rr);
}
void round_row(void *canvas, const rect_t *r, int R, int i, unsigned c) {
    round_row_ends(canvas, r, R, i, c, ROUND_LEFT | ROUND_RIGHT);
}
/* A ring a pixel wide just inside r: what R's corners cover and R - 1's, a pixel in, don't. With
 * either table missing (a failed heap), hard corners. */
void round_ring(void *canvas, const rect_t *r, int R, unsigned c) {
    const unsigned char *out = cover_of(R), *in = cover_of(R - 1);
    for (int i = 0; i < r->h; ++i) {
        int row = corner_row(r, R, i);
        if (!i || i == r->h - 1) {
            round_row(canvas, r, R, i, c);
        } else if (row < 0 || !out || !in) {
            int n = round_inset(R, i, r->h);
            scrub_row(canvas, c, r->x + n, r->y + i, 1);
            scrub_row(canvas, c, r->x + r->w - 1 - n, r->y + i, 1);
        } else {
            for (int n = 0; n < R; ++n) {
                int a = out[row * R + n] - (n ? in[(row - 1) * (R - 1) + n - 1] : 0);
                if (a <= 0) continue;
                unsigned ink = (unsigned)a * (c >> 24) / 255 << 24 | (c & 0xffffff);
                scrub_row(canvas, ink, r->x + n, r->y + i, 1);
                scrub_row(canvas, ink, r->x + r->w - 1 - n, r->y + i, 1);
            }
        }
    }
}
/* What lies outside r's corners, radius R, painted over in behind (a final 0xRRGGBB), each pixel
 * at the share of it the arc leaves: rounds what is already drawn in r, a square cover, into what
 * stands behind it, anti-aliased (docs/internals.md#rounded-corners). Each corner row is one fill
 * of the pixels wholly outside, then one fill a part pixel. */
void corner_mask(void *canvas, const rect_t *r, int R, unsigned behind) {
    behind &= 0xffffffu;
    if (2 * R > r->w) R = r->w / 2;
    if (2 * R > r->h) R = r->h / 2;
    const unsigned char *cover = cover_of(R);
    for (int i = 0; cover && i < R; ++i) {
        const unsigned char *row = cover + i * R;
        int n = 0;
        while (n < R && !row[n]) ++n;
        for (int c = 0; c < 4; ++c) {
            int y = r->y + (c & 1 ? r->h - 1 - i : i), right = c & 2;
            if (n) scrub_row(canvas, 0xff000000u | behind, right ? r->x + r->w - n : r->x, y, n);
            for (int k = n; k < R && row[k] < 255; ++k)
                scrub_row(canvas, (unsigned)(255 - row[k]) << 24 | behind,
                          right ? r->x + r->w - 1 - k : r->x + k, y, 1);
        }
    }
}
/* A theme image at x, y at its size. */
void paint_art(void *w, void *canvas, const char *name, int x, int y) {
    unsigned bitmap[64]; /* bitmap_t: width @0, height @4 */
    if (widget_load_image(w, name, bitmap)) return;
    rect_t src = { 0, 0, (int)bitmap[0], (int)bitmap[1] }, dst = { x, y, src.w, src.h };
    canvas_draw_image(canvas, bitmap, &src, &dst);
}

/* Narrow the canvas clip to the surface's viewport, keeping the old clip; false when none shows. */
static int clip_surface(void *canvas, menu_t *m, rect_t *old) {
    int clip[4];
    if (!clip_within(canvas, &old->x, clip, I(canvas, CANVAS_X), I(canvas, CANVAS_Y), I(m->w, W_W),
                     m->height))
        return 0;
    canvas_set_clip_rect(canvas, clip);
    return 1;
}

/* Library sorting ignores a leading "The ", "A " or "An " (Apple's rule) when a name follows it:
 * the length to skip, else 0. */
static unsigned article(const char *s) {
    for (const char *a = "the a an "; *a;) {
        unsigned n = 0;
        while (a[n] != ' ' && (s[n] | 32) == a[n]) ++n;
        if (a[n] == ' ' && s[n] == ' ' && (unsigned char)s[n + 1] > ' ') return n + 1;
        while (*a++ != ' ') {}
    }
    return 0;
}

/* Text centred in r in the default font at px, in color; text color and alignment are restored,
 * the font is not (stock sets it before its own text). Shared with peq_ui.c. */
static void draw_in_face(void *canvas, const char *face, const unsigned *s, unsigned n,
                         const void *r, unsigned px, unsigned color) {
    unsigned text = (unsigned)I(P(canvas, CANVAS_LCD), LCD_TEXT_COLOR);
    int align_v = I(canvas, CANVAS_ALIGN_V), align_h = I(canvas, CANVAS_ALIGN_H);
    canvas_set_font(canvas, face, px);
    canvas_set_text_color(canvas, color);
    I(canvas, CANVAS_ALIGN_V) = I(canvas, CANVAS_ALIGN_H) = 1;
    canvas_draw_text_in_rect(canvas, s, n, r);
    I(canvas, CANVAS_ALIGN_V) = align_v;
    I(canvas, CANVAS_ALIGN_H) = align_h;
    stock_canvas_set_text_color(canvas, text);
}
void draw_centred(void *canvas, const unsigned *s, unsigned n, const void *r, unsigned px,
                  unsigned color) {
    int fpx = (int)px;
    /* the theme's face, or the system default font */
    const char *face = theme_face(s, (int)n, &fpx);
    draw_in_face(canvas, face, s, n, r, (unsigned)fpx, color);
}
/* A rounded box in color (a color_t). Where the canvas declines to round it (no vgcanvas, or a
 * radius under 3, which stock zeroes and then fails), its rows with corner_cover.c's anti-aliased
 * corners from a radius of 2 (round_row, in the colour as Light Mode inks it); a radius of 0 or 1,
 * or one too large for the box, is square. */
void fill_box(void *canvas, rect_t *r, unsigned color, unsigned radius) {
    if (!canvas_fill_rounded_rect(canvas, r, (void *)0, &color, radius)) return;
    if (radius >= 2 && 2 * (int)radius <= r->w && 2 * (int)radius <= r->h) {
        unsigned c = ui_ink(color);
        c = (c & 0xff000000u) | (c & 255) << 16 | (c & 0xff00u) | (c >> 16 & 255);
        for (int i = 0; i < r->h; ++i) round_row(canvas, r, (int)radius, i, c);
        canvas_set_fill_color(canvas, color); /* left set, as the square box leaves it */
        return;
    }
    canvas_set_fill_color(canvas, color);
    canvas_fill_rect(canvas, r->x, r->y, r->w, r->h);
}

/* A two-pixel frame in color, two concentric one-pixel rounded strokes, or a square one when the
 * box is too small to round or the canvas declines to (no vgcanvas). Leaves the stroke color set.
 */
static void frame_box(void *canvas, const rect_t *r, unsigned color, unsigned radius) {
    rect_t in = { r->x + 1, r->y + 1, r->w - 2, r->h - 2 };
    if (r->w > 2 * (int)radius && r->h > 2 * (int)radius &&
        !canvas_stroke_rounded_rect(canvas, r, (void *)0, &color, radius, 1) &&
        !canvas_stroke_rounded_rect(canvas, &in, (void *)0, &color, radius - 1, 1))
        return;
    canvas_set_stroke_color(canvas, color);
    canvas_stroke_rect(canvas, r->x, r->y, r->w, r->h);
    canvas_stroke_rect(canvas, in.x, in.y, in.w, in.h);
}

/* Stock names a playlist row's trailing More dots "img_more%d". */
static void *more_image(void *w) {
    for (unsigned i = 0, n = w ? widget_count_children(w) : 0; i < n; ++i) {
        void *c = widget_get_child(w, i);
        if (widget_get_visible(c) &&
            tk_str_start_with(widget_get_prop_str(c, "name", ""), "img_more"))
            return c;
    }
    return (void *)0;
}

/* A widget in a DRILL window (contexts.inc). Its own window decides, not the top one, so a window
 * painted during a transition keeps its own rows. */
static int drill(void *w) {
    /* stock's More hit test owns the trailing area */
    if (more_image(w)) return 0;
    if (!tk_strcmp(widget_get_prop_str(w, "name", ""), PL_ACTION)) return 0;
    w = window_of(w);
    /* a row that acts in place opens nothing: the Add to Playlist picker's add and close */
    if (w && widget_get_prop_int(w, PL_MODE, 0) &&
        !tk_strcmp(widget_get_prop_str(w, "name", ""), "playlist_page"))
        return 0;
    int ctx = w ? context_id(widget_get_prop_str(w, "name", (void *)0)) : -1;
    return ctx >= 0 && (contexts[ctx].flags & DRILL);
}

/* Home's Cover Flow row: the image its click opens Cover Flow from, or the row holding it. */
static int cover_flow_row(void *row) {
    return !tk_strcmp(widget_get_prop_str(row, "name", ""), "img_cover_flow") ||
           widget_lookup(row, "img_cover_flow", 1);
}

/* The iPod `>` of each drill row, where stock rows place img_into, as stock hides its own
 * list_into: not in multi-select, and not on grid tiles. The theme's own on each row, on the set's
 * right column (list_marks.c), Home's list's too under chevron_home, and on Home's Cover Flow row, selected, the spinner while Cover Flow opens
 * (coverflow.c coverflow_spinner). */
static void paint_chevrons(void *w, void *canvas) {
    rect_t old;
    void *win = window_of(w);
    int home = home_menu(win);
    /* a picker's tap adds and closes: no `>` */
    if (g_navbar_status || !kind(w) || !P(canvas, CANVAS_LCD) || !drill(w) ||
        (home && (!theme()->chevron_home || kind(w) == 3)) || lr_edit_list(w) ||
        !load_rows(&g_menu, w) || !clip_surface(canvas, &g_menu, &old))
        return;
    for (int i = 0; i < g_menu.n; ++i) {
        rect_t r = bounds(&g_menu, i);
        /* the rows in sight only, as the clip draws no others */
        if (r.y + r.h <= 0 || r.y >= g_menu.height || 2 * r.w < I(w, W_W) || !drill(g_menu.at[i]))
            continue;
        /* the theme's own, on the right column */
        int sel = st.sel_w == w && i == st.sel_row,
            spin = home && sel && cover_flow_row(g_menu.at[i]) ? coverflow_spinner() : 0;
        if (spin)
            spinner_icon(w, canvas, I(w, W_W) - az_right(w), r.y + r.h / 2);
        else
            chevron_icon(w, canvas, I(w, W_W) - az_right(w), r.y + r.h / 2, sel);
    }
    canvas_set_clip_rect(canvas, &old);
}

/* A song list's rows, from the list's background, so before the rows paint:
 * song_rows.c fits each title's box and draws what stands beside it, a greyed row's clear of its
 * offline mark. Here, as the rows and their bounds are this file's. */
static int offline_x(void *w, int i, const rect_t *r, int *chev);
static void paint_songs(void *w, void *canvas) {
    if (!w || !kind(w) || !P(canvas, CANVAS_LCD) || !song_list(w) || !load_rows(&g_menu, w)) return;
    int chev = -1; /* offline_x's, asked once */
    for (int i = 0; i < g_menu.n; ++i) {
        rect_t r = bounds(&g_menu, i);
        if (r.y + r.h > 0 && r.y < I(w, W_H))
            song_row(w, canvas, g_menu.at[i], g_menu.id[i], r, st.sel_w == w && i == st.sel_row,
                     catalogue_row_greyed(g_menu.at[i])
                         ? offline_x(w, i, &r, &chev) - CAT_MARK_W / 2
                         : 0);
    }
}

/* Rows that can't play now (catalogue.c) carry the offline mark at their right: in the chevron's
 * column, or just left of a chevron the row shows. Its centre on row i of g_menu, surface
 * coordinates. */
static int into_image(void *w, int depth) {
    for (unsigned k = 0, n = widget_count_children(w); k < n; ++k) {
        void *c = widget_get_child(w, k);
        if (!widget_get_visible(c)) continue;
        if (!tk_strcmp(widget_get_prop_str(c, "image", ""), "list_into") ||
            (depth > 1 && into_image(c, depth - 1)))
            return 1;
    }
    return 0;
}
/* *chev is drill(w), the list's own answer, -1 until this asks it: drill walks every row of the
 * list (more_image), so a painter asks it once for all its rows, not once a row
 * (docs/inefficiencies.md#37-a-lists-offline-marks-asked-the-whole-list-once-a-row-resolved). */
static int offline_x(void *w, int i, const rect_t *r, int *chev) {
    int sel = st.sel_w == w && i == st.sel_row;
    if (*chev < 0) *chev = drill(w);
    /* the payload's chevrons (paint_chevrons), or stock's list_into, which shows on the selection
     */
    int chevron = *chev || (sel && into_image(P(g_menu.at[i], W_PARENT), 2));
    return r->x + r->w - CHEVRON_W + 25 - (chevron ? 28 : 0);
}
/* Before a list's rows paint: an album's songs (albuminfo_page; playerjumpinfo_page, Go to
 * Album's; Cover Flow's tracks, where Go to Album goes with Artist View or Cover Flow open), Most
 * Played's and the Ratings lists' songs (page_tracks) and Search's results are a scroll_view's
 * rows, one per record in order (the showlist, the page's tracks or Search's results), not a
 * table's, so each that can't play now is greyed here, as ringnav_table_load greys a table's.
 * Then each greyed row in sight has its labels end short of its mark (catalogue_fit), their text
 * SONG_COL_GAP clear of its ink as a length is (song_rows.c), and any it narrowed before take
 * their width back as they come into sight. */
/* Search's results while they show: p_deque_searchshowlist, which each of its tab_button_group's
 * tabs (Songs, Artists, Albums, Genres) fills with its rows, the Songs tab's the list
 * search_track_row_on_click plays from; *tab the lit one's index. 0 for none (its history). */
static void *page_tracks(void *page);
void *ringnav_search_rows(void *win, int *tab) {
    void *tabs = win && !tk_strcmp(widget_get_prop_str(win, "name", ""), "search_dialog")
                     ? widget_lookup(win, "tab_button_group", 1)
                     : (void *)0;
    for (int i = 0, n = tabs && widget_get_visible(tabs) ? widget_count_children(tabs) : 0; i < n;
         ++i)
        if (widget_get_prop_int(widget_get_child(tabs, i), "value", 0))
            return *tab = i, P(p_deque_searchshowlist, 0);
    return (void *)0;
}
static void grey_rows(void *w) {
    if ((!catalogue_has() && !catalogue_greys()) || !kind(w) || !load_rows(&g_menu, w)) return;
    void *win = window_of(w), *list = (void *)0;
    int tab = 0; /* Search's lit tab: 0 Songs */
    const char *page = widget_get_prop_str(win, "name", "");
    if (g_menu.kind == 1 && catalogue_has())
        list = !tk_strcmp(page, "albuminfo_page") || !tk_strcmp(page, "playerjumpinfo_page")
                   ? P(p_deque_showlist, 0)
                   : page_tracks(win);
    if (!list && g_menu.kind == 1 && catalogue_has()) list = ringnav_search_rows(win, &tab);
    if (list && deque_size(list) != (unsigned)g_menu.rows) list = (void *)0;
    int chev = -1; /* offline_x's, asked once */
    for (int i = 0; i < g_menu.n; ++i) {
        rect_t r = bounds(&g_menu, i);
        /* the rows in sight only: a long list's would fill catalogue.c's CAT_GREY_ROWS, and a row
         * out of sight is marked and fitted in the paint that brings it in, before it paints */
        int shown = r.y + r.h > 0 && r.y < g_menu.height;
        if (list && shown)
            catalogue_mark(g_menu.at[i], deque_at(list, (unsigned)g_menu.id[i]), tab);
        else if (list)
            catalogue_forget(g_menu.at[i]);
        if (shown && 2 * r.w >= I(w, W_W))
            catalogue_fit(g_menu.at[i], w,
                          offline_x(w, i, &r, &chev) - CAT_MARK_W / 2 - SONG_COL_GAP +
                              HSCROLL_MARGIN + (g_menu.kind == 1 ? I(w, SCROLL_X) : 0));
    }
}

/* After the rows: each greyed row's mark in sight, in its text's colour as last painted, and
 * under OLED each marked song row's (catalogue.c catalogue_row_mark). */
static void paint_offline(void *w, void *canvas) {
    rect_t old;
    if (!catalogue_greys() || !kind(w) || !P(canvas, CANVAS_LCD) || !load_rows(&g_menu, w) ||
        !clip_surface(canvas, &g_menu, &old))
        return;
    unsigned fill = (unsigned)I(P(canvas, CANVAS_LCD), LCD_FILL_COLOR);
    int chev = -1; /* offline_x's, asked once */
    for (int i = 0; i < g_menu.n; ++i) {
        rect_t r = bounds(&g_menu, i);
        /* a greyed or marked row in sight: no other carries a mark */
        if (r.y + r.h > 0 && r.y < g_menu.height && 2 * r.w >= I(w, W_W) &&
            catalogue_row_greyed(g_menu.at[i]))
            catalogue_row_mark(w, canvas, g_menu.at[i], offline_x(w, i, &r, &chev), r.y + r.h / 2);
    }
    stock_canvas_set_fill_color(canvas, fill);
    canvas_set_clip_rect(canvas, &old);
}

/* An open menu sheet's rows' symbols and values (menu_modal.c), over its selection bar. */
static void paint_modal_rows(void *w, void *canvas) {
    rect_t old;
    if (!modal_rows_of(w) || !P(canvas, CANVAS_LCD) || !load_rows(&g_menu, w) ||
        !clip_surface(canvas, &g_menu, &old)) /* a row half out of the rows' box: its part in it */
        return;
    for (int i = 0; i < g_menu.n; ++i) {
        rect_t r = bounds(&g_menu, i);
        if (r.y + r.h > 0 && r.y < I(w, W_H))
            modal_row_paint(w, canvas, g_menu.at[i], r, st.sel_w == w && i == st.sel_row);
    }
    canvas_set_clip_rect(canvas, &old);
}

/* Writes v (under 1000) in decimal to s; returns the digits written. */
unsigned put_num(unsigned *s, unsigned v) {
    unsigned n = 0;
    if (v >= 100) s[n++] = '0' + v / 100 % 10;
    if (v >= 10) s[n++] = '0' + v / 10 % 10;
    s[n++] = '0' + v % 10;
    return n;
}

static int letter_expire(const void *info) {
    st.letter_timer = 0;
    void *w = surface((void *)0, (void *)0);
    if (w && w == st.wheel_surface) widget_invalidate_force(w, (void *)0);
    return 0;
}

/* The fast-scroll letter's box and its n characters. */
static void draw_letter(void *canvas, const unsigned *c, unsigned n, rect_t *box) {
    unsigned raw = st.raw_ink;
    st.raw_ink = 0;
    /* unsigned: 0xd9 << 24 is past INT_MAX, undefined for an int; the Q2's clang wrapped it */
    fill_box(canvas, box, ((unsigned)LETTER_ALPHA << 24) | FILL_RGB, LETTER_RADIUS);
    draw_centred(canvas, c, n, box, LETTER_PX, 0xffffffff);
    st.raw_ink = raw;
}

/* iPod fast scroll: while the wheel ramp moves more than one row per step, the selected row's
 * first character sits in a dark translucent square over the list until LETTER_MS after the last
 * such step. A virtual table resolves the logical row in its recycled pool; an offscreen or
 * textless row shows nothing. The fill color and clip are restored. */
static void paint_letter(void *w, void *canvas) {
    rect_t old;
    if (!st.letter_timer || w != st.wheel_surface || st.wheel_run <= LIST_FIRST_MS ||
        st.touch_mode || az_on(w) || !P(canvas, CANVAS_LCD) || !load_rows(&g_menu, w) ||
        /* Only a name-sorted list arms it (the wheel handler, az_index.c name_sorted); Cover Flow is
         * one until an album is open, whose tracks are in album order (disc and track). */
        coverflow_tracks(window_of(w)))
        return;
    int i = index_of(&g_menu, widget_get_prop_int(w, SEL, -1));
    const unsigned *s = i < 0 ? (void *)0 : row_id(g_menu.at[i]).title;
    while (s && *s == ' ') ++s;
    if (!s || !*s || !clip_surface(canvas, &g_menu, &old)) return;
    if (g_menu.ctx >= 0 && contexts[g_menu.ctx].kind == CTX_LOCAL) { /* the letter it sorts under */
        char head[8];
        unsigned k = 0;
        for (; k < 7 && s[k]; ++k) head[k] = (char)(s[k] < 128 ? s[k] : 127);
        head[k] = 0;
        s += article(head);
    }
    unsigned c = *s >= 'a' && *s <= 'z' ? *s - 32 : *s;
    unsigned fill = (unsigned)I(P(canvas, CANVAS_LCD), LCD_FILL_COLOR);
    rect_t box = { (I(w, W_W) - LETTER_BOX) / 2, (g_menu.height - LETTER_BOX) / 2, LETTER_BOX,
                   LETTER_BOX };
    draw_letter(canvas, &c, 1, &box);
    stock_canvas_set_fill_color(canvas, fill);
    canvas_set_clip_rect(canvas, &old);
}

/* Playing art is rounded after scaling, so opposite corners share the same pixel grid.
 * NP_ART_RADIUS at 166px (or the theme's playing.art.radius, in px at any size), painted over it in
 * the page's black: each corner row outside the arc, then its edge pixel at the alpha of its
 * uncovered part (1/16 px). Now Playing's art settled smaller on pause (np_settle.c) is drawn here
 * first, and its corners round its smaller box. */
static void paint_cover(void *w, void *canvas) {
    if (!w || !P(canvas, CANVAS_LCD)) return;
    np_settle_paint(w, canvas);                  /* settled smaller on pause (np_settle.c) */
    if (w == st.np_cover) np_tap_dim(w, canvas); /* held: under the corners */
    int x = 0, y0 = 0, box[4], ww, h;
    if (w == st.np_cover) np_settle_box(w, box), x = box[0], y0 = box[1]; /* settled: smaller */
    if (w == st.bc[2]) {
        w = widget_lookup(w, "img_bigcover", 1);
        if (!w) return;
        x = I(w, W_X), y0 = I(w, W_Y); /* after the title's shade, in the page's canvas */
    } else if (w != st.np_cover && w != spot_art())
        return;
    unsigned fill = (unsigned)I(P(canvas, CANVAS_LCD), LCD_FILL_COLOR);
    int own = theme()->np_art_radius;
    ww = I(w, W_W), h = I(w, W_H);
    if (w == st.np_cover) own = own * box[2] / ww, ww = box[2], h = box[3];
    int r = own ? own : (ww < h ? ww : h) * NP_ART_RADIUS / 166;
    unsigned page = ui_ink(RGBA(0)); /* the page's black as drawn, back to 0xRRGGBB */
    rect_t art = { x, y0, ww, h };
    corner_mask(canvas, &art, r, (page & 255) << 16 | (page & 0xff00) | (page >> 16 & 255));
    stock_canvas_set_fill_color(canvas, fill);
}

/* The selected row's long title scrolls, as an iPod's does, MARQUEE_PASSES times as soon as the
 * selection settles, through stock's scroller with every label's holds and pace
 * (ringnav_hscroll_start); every other row keeps its ellipsis. Only a title stock would scroll on a
 * focus no row has under ringnav (only_focus, or only_parent_focus: set_hscroll_label_attribute's,
 * stock's Wi-Fi networks): stock's timer stops such a label on its first tick unless it or its
 * parent is focused (0x5fb7ac, 0x5fb7f0), so both flags are off while this one runs and back after.
 * It loops, so each pass ends on its lull (ringnav_hscroll_lull), which counts it and stops it
 * after the last, or once the row isn't the selected one on a lit, usable screen: a dark screen or
 * a covered page costs at most the rest of one pass. A row is its widget and its id, as a table's
 * pool can hold the selection in one widget (paint_selection; a paint would drop
 * hscroll_label_stop's invalidate, so marq_moved stops it from a timer). */
static int marq_gone(void *ctx, void *event) {
    (void)ctx, (void)event;
    st.marq_label = (void *)0;
    return 0;
}
/* The marquee's label's focus flags as it found them: only_focus, only_parent_focus. */
static unsigned char marq_focus[2] __attribute__((section(".scratch")));
static void marq_stop(void) {
    void *label = st.marq_label;
    if (!label) return;
    st.marq_label = (void *)0;
    widget_off_by_func(label, EVT_DESTROY, marq_gone, (void *)0);
    hscroll_label_stop(label);
    hscroll_label_set_only_focus(label, marq_focus[0]);
    B(label, HSCROLL_ONLY_PARENT_FOCUS) = marq_focus[1];
}
/* stock's two margins (2 px each by default, 0x5fb23c) count towards its ellipsis */
static int scroller_over(void *label) { return I(label, HSCROLL_TEXT_W) + 4 - I(label, W_W); }
/* The row's first visible hscroll_label that is cut off, in order (its title, then a key/value
 * row's value: any value cut off on the selected row scrolls, docs/oled-design.md#motion); with
 * none cut off, its first. */
static void *row_scroller(void *w, int depth, int cut) {
    unsigned n = widget_count_children(w);
    for (unsigned i = 0; i < n; ++i) {
        void *c = widget_get_child(w, i);
        if (!widget_get_visible(c)) continue;
        if (!tk_strcmp(widget_get_type(c), "hscroll_label") && (!cut || scroller_over(c) > 0))
            return c;
        void *in = depth ? row_scroller(c, depth - 1, cut) : (void *)0;
        if (in) return in;
    }
    return (void *)0;
}
/* The marquee's row while it is the selected one on a usable screen; else 0. */
static void *marq_row(void) {
    void *w = usable() ? surface((void *)0, (void *)0) : (void *)0;
    int i = st.sel_row;
    void *row = w && w == st.sel_w && load_rows(&g_menu, w) && i >= 0 && i < g_menu.n &&
                        g_menu.id[i] == st.marq_id
                    ? g_menu.at[i]
                    : (void *)0;
    return row == st.marq_row ? row : (void *)0;
}
/* The row's title was rewritten in place (addon.c relabel): if it is the marquee's, the next paint
 * re-arms it for the new text's width. */
void marq_restart(void *row) {
    if (row == st.marq_row) st.marq_row = (void *)0;
}
static int marq_moved(const void *info) {
    (void)info;
    st.marq_timer = 0;
    marq_stop();
    st.marq_passes = 0;
    void *row = marq_row();
    if (!row) {
        st.marq_row = (void *)0; /* the next paint with a selection starts over */
        return 0;
    }
    void *label = row_scroller(row, 2, 1) ?: row_scroller(row, 2, 0);
    int over = label ? scroller_over(label) : 0;
    /* a label with neither flag scrolls by itself; one waiting on a focus no row has under ringnav
     * (only_focus, or set_hscroll_label_attribute's only_parent_focus: stock's Wi-Fi networks and
     * settings rows) scrolls here, its flags off while it runs */
    if (over <= 0 || (!B(label, HSCROLL_ONLY_FOCUS) && !B(label, HSCROLL_ONLY_PARENT_FOCUS)))
        return 0; /* until it moves */
    B(label, HSCROLL_LOOP) = 1;
    st.marq_label = label;
    marq_focus[0] = B(label, HSCROLL_ONLY_FOCUS),
    marq_focus[1] = B(label, HSCROLL_ONLY_PARENT_FOCUS);
    widget_on(label, EVT_DESTROY, marq_gone, (void *)0);
    B(label, HSCROLL_ONLY_PARENT_FOCUS) = 0;
    hscroll_label_set_only_focus(label, 0);
    hscroll_label_start(label);
    return 0;
}

/* Every hscroll_label's holds and pace. Stock's start (hscroll_label_start, which its focus, its
 * paint on a new text, its lull and the payload all call) waits the label's delay, 0 unless set,
 * before the first step, and a looping label ends a pass on its lull (2000 or 3000 ms) with its end
 * in view, then starts again: most labels raced off their start at once and parked on their end.
 * Every start now waits MARQUEE_HOLD_MS, showing where the text is (its start, or a yoyo label's
 * end), and runs at HSCROLL_PACE at most (or at it, for a label with no speed, which would cover
 * any length in its duration). Stock's start does nothing for a running label. */
int ringnav_hscroll_start(void *w) {
    if (np_marq_owns(w)) { /* Now Playing's lines move together (np_marquee.c), from rest */
        np_marq_reset();
        return 0;
    }
    unsigned speed = (unsigned)I(w, HSCROLL_SPEED);
    I(w, HSCROLL_DELAY) = MARQUEE_HOLD_MS;
    if (!speed || speed > HSCROLL_PACE) I(w, HSCROLL_SPEED) = HSCROLL_PACE;
    return stock_hscroll_start_trampoline(w);
}
/* Every hscroll_label's step (hscroll_label_on_timer_tick, HOOKS in tools/build.py). Stock adds the
 * timer's 16 ms period to the pass's elapsed time on each tick, but a timer runs at most once a UI
 * loop pass, so the text moved 16 ms' worth a pass however long the pass took: once the loop idled
 * (2 s after input), an emulator's 45 ms passes ran a list row's title at 18 px a second, a Q2's at
 * about 5. The tick counts the time since the last one instead (its now less its start, which the
 * timer manager sets to each run's time), so a pass moves the text by the clock and its pace is
 * its speed whatever the loop's. A paused label's tick moves nothing, as stock's. */
int ringnav_hscroll_tick(void *info) {
    void *w = P(info, TIMER_CTX);
    if (w && !B(w, HSCROLL_PAUSED))
        I(w, HSCROLL_ELAPSED) +=
            I(info, TIMER_NOW) - I(info, TIMER_START) - I(info, TIMER_DURATION);
    return stock_hscroll_tick_trampoline(info);
}
/* Stock's Albums rows since V1.36 (ALBUM_LABELS in tools/build.py): the list row's artist line and
 * a grid cell's title take stock's attributes here, then the focus flag V1.34 gave them, which
 * stock now sets in Menu Mode alone. No row's parent has the focus under ringnav, so they stay
 * still. */
void ringnav_album_label(void *label) {
    set_hscroll_label_attribute(label);
    hscroll_label_set_only_parent_focus(label, 1);
}
/* The end-of-pass widget_add_timer comes here (HSCROLL_LULL in tools/build.py): the lull holds the
 * end MARQUEE_HOLD_MS, then puts the start back before stock's callback restarts the label, so its
 * delay holds there. A looping yoyo label scrolls back from its end, which that delay holds: its
 * lull is 0. A label that doesn't loop comes here too (HSCROLL_ONCE): its lull's end stops it, back
 * on its start, as stock's lull callback ends (the timer id cleared, so hscroll_label_stop leaves
 * this firing timer to RET_REMOVE). An only_focus label stock won't restart (unfocused) rests on
 * its start, not its end. */
static int (*hscroll_lulled)(const void *);
static int hscroll_lull_end(const void *info) {
    void *w = P(info, TIMER_CTX);
    if (!B(w, HSCROLL_LOOP)) {
        I(w, HSCROLL_TIMER) = 0;
        hscroll_label_stop(w);
        return 7; /* RET_REMOVE */
    }
    if (!B(w, HSCROLL_YOYO)) hscroll_label_set_xoffset(w, 0);
    int ret = hscroll_lulled(info);
    if (w == st.marq_label && !marq_row())
        st.marq_row = (void *)0, marq_stop(); /* the next paint with a selection starts over */
    else if (w == st.marq_label && ++st.marq_passes >= MARQUEE_PASSES)
        marq_stop(); /* passes spent: nothing until the selection moves */
    return ret;
}
unsigned ringnav_hscroll_lull(void *w, int (*lulled)(const void *), unsigned ms) {
    (void)ms;
    hscroll_lulled = lulled;
    return widget_add_timer(w, hscroll_lull_end,
                            B(w, HSCROLL_YOYO) && B(w, HSCROLL_LOOP) ? 0 : MARQUEE_HOLD_MS);
}

/* A label at rest on its ellipsis (tools/build.py HSCROLL_ELLIPSIS: hscroll_label's paint calls
 * this for stock's widget_draw_text_in_rect, here with the other hscroll_label hooks). Stock keeps
 * the characters that fit before its "...", each measured alone, while dots + kept + the next is
 * under the box's width, so on its own a cut just after a word reads "(2017 ...", a gap before the
 * dots. The box handed on is narrowed to end the cut on the last character that isn't a space: one
 * px past dots + those, so they still fit and the space after them (wider than a px) does not. */
int ringnav_ellipsis_text(void *w, void *canvas, const unsigned *s, unsigned n, const rect_t *r,
                          int ellipses) {
    static const unsigned dots[] = { '.', '.', '.' };
    rect_t cut;
    if (ellipses && s && r && n && canvas_measure_text(canvas, s, n) > (float)r->w) {
        float dw = canvas_measure_text(canvas, dots, 3), kept = 0, ink = 0;
        unsigned i = 0, end = 0;
        for (; i < n; ++i) {
            float cw = canvas_measure_text(canvas, s + i, 1);
            if ((float)r->w <= dw + ink + cw) break;
            ink += cw;
            if (s[i] != ' ') end = i + 1, kept = ink;
        }
        if (end && end < i) {
            cut = *r;
            cut.w = (int)(dw + kept) + 1;
            r = &cut;
        }
    }
    return widget_draw_text_in_rect(w, canvas, s, n, r, ellipses);
}

unsigned wide_len(const unsigned *s) {
    unsigned n = 0;
    while (s && s[n]) ++n;
    return n;
}

static int reach_painting; /* list_reach_paint is painting a list's rows under the status row */
/* Home's selection moved in a paint: the next frame repaints Home's page and the status row's band
 * (paint_selection, list_reach_due). */
static int sel_repaint __attribute__((section(".scratch")));

/* Load and settle the painted surface's selection, then draw it behind the rows: the accent's bar,
 * a grid tile's card, a dot on Home's tiles; nothing behind a picture tile. */
static void paint_selection(void *w, void *canvas) {
    if (kind(w)) st.sel_w = st.sel_accent = (void *)0;
    if (!w || !canvas || !kind(w)) return;
    void *top = window_manager_get_top_window(window_manager());
    int i, shown = !st.touch_mode;
    void *own = window_of(w);
    int under = own && own != top && modal_win(top); /* the row a menu sheet acts on */
    if (own && own != top && (slides(top) || feedback_toast(top) || under)) {
        /* The page under a sliding one, painted into the animator's snapshot, or under OLED's
         * toast, which leaves the page in sight: draw the row it holds. Loading, recall and
         * settling read the top page's state, so none of it runs. A pending untouch is a Return by
         * button, which shows the row of the page behind. Under a menu sheet the row shows, dimmed with the page: it names what the
         * menu acts on. */
        if (!usable() || !load_rows(&g_menu, w) || g_menu.kind == 3) return;
        i = index_of(&g_menu, widget_get_prop_int(w, SEL, -1));
        top = own;
        if (st.untouch_timer || under) shown = 1;
    } else {
        if (surface((void *)0, (void *)0) != w) return;
        if (!load(&g_menu, w, !pointer_down())) {
            cancel_center();
            return;
        }
        if (st.center_timer && !pending_matches(top, &g_menu)) cancel_center();
        if (g_menu.kind == 3) return; /* Home shows its selected card. */
        if (eq_tabs_hold(w)) return;  /* the wheel is on EQ's tabs, above the list: no row */
        int was = widget_get_prop_int(w, SEL, -1);
        /* the band's paint (list_reach_paint) moves the view up under the status row: it draws
         * the selection there is, as a row in sight by the moved view need not be one in the
         * list's */
        i = reach_painting ? index_of(&g_menu, was)
                           : reconcile(&g_menu, !moving(&g_menu) && !pointer_down());
        /* Home's bar shows under touch, so a selection a drag's end moved off a row has a bar
         * drawn there still, perhaps on a row under the status row: this paint's clip may hold
         * neither (Split's art pans beside the list), and one asked for now goes with this frame.
         * The next frame repaints the page and the band (list_reach_due). */
        if (home_menu(top) && widget_get_prop_int(w, SEL, -1) != was) sel_repaint = 1;
    }
    /* Touch hides the selection until the wheel or a button, except on Home (iPod's list) and a
     * pop-up's buttons, which open on their first (Cancel). */
    int home = home_menu(top);
    if (home && i >= 0) coverflow_home_row(w, g_menu.at[i], g_menu.id[i], 1);
    /* a captioned settings row grows while selected (settings_pages.c), a grabbed inline slider's
     * while grabbed (inline_slider.c) */
    caption_sel(w, i < 0 || (!shown && !home) ? (void *)0 : P(g_menu.at[i], W_PARENT));
    inline_slider_sel(w, i < 0 || (!shown && !home) ? (void *)0 : P(g_menu.at[i], W_PARENT));
    if (i < 0 || (!shown && !home && g_menu.kind != 4)) return;
    if (st.shuffle_on && st.shuffle_on == st.action_view && st.shuffle_list == w)
        return;          /* the selection is on the Shuffle row above (shuffle_paint) */
    /* an empty state's button rings itself (feedback.c), no bar; so does a row standing for one in a page's own
     * list (carremote_page.c's Pair a Phone) */
    if (empty_button(w) || widget_get_prop_int(g_menu.at[i], "_rings_itself", 0) ||
        widget_get_prop_int(P(g_menu.at[i], W_PARENT), "_rings_itself", 0)) {
        st.sel_w = w;
        st.sel_row = i;
        return;
    }
    if (pair_holds(w)) { /* ... or on an artist's or album's pair (artist_page.c) */
        /* the row a fast spin passed through keeps no marquee: its timer finds no row */
        marq_stop();
        st.marq_row = (void *)0;
        return;
    }
    rect_t r = bounds(&g_menu, i), old;
    if (r.w < 5 || r.h < 5 || !P(canvas, CANVAS_LCD)) return;
    void *lcd = P(canvas, CANVAS_LCD);
    unsigned fill_color = (unsigned)I(lcd, LCD_FILL_COLOR);
    unsigned stroke_color = (unsigned)I(lcd, LCD_STROKE_COLOR);
    if (!clip_surface(canvas, &g_menu, &old)) return;
    /* A list row gets the full surface width; a grid tile keeps its own rect. */
    int tile = 2 * r.w < I(g_menu.w, W_W);
    /* a menu sheet's rows keep their own box, the confirmation card's the card's */
    if (!tile && !modal_rows_of(g_menu.w) && !confirm_surface(g_menu.w)) {
        r.x = 0;
        r.w = I(g_menu.w, W_W);
        /* The Library's row is a button over the top BODY of its pitch: the bar takes the whole
         * row, or the last row's leaves a strip at the screen's bottom. */
        void *row = P(g_menu.at[i], W_PARENT);
        if (!I(g_menu.at[i], W_Y) && is_row(row) && I(row, W_H) > r.h) r.h = I(row, W_H);
    }
    /* The accent's bar, under the rows' own ink */
    const scheme_t *S = scheme();
    /* Light Mode: ink on a coloured bar stays white (ringnav_style_color) */
    st.sel_accent =
        !neutral(S->tones[0]) ? (home ? P(g_menu.at[i], W_PARENT) : g_menu.at[i]) : (void *)0;
    if (!tile || g_menu.kind == 4) {
        /* a list row's is the theme's sel_inset in from each side, its ends sel_radius at most
         * pill_radius's */
        const theme_t *T = theme();
        rect_t bar = r;
        int in = tile ? 0 : T->sel_inset, radius = tile ? 0 : pill_radius(g_menu.at[i], r.h, in);
        if (radius > T->sel_radius) radius = T->sel_radius;
        bar.x += in, bar.w -= 2 * in + (tile ? 0 : az_bar_in(g_menu.w)); /* az_index.c */
        int surface_clip[4], leaning = !tile && tilt_clip(canvas, surface_clip, &old.x);
        if (!tile) tilt_bar(w, canvas, &bar, S->tones[0], radius); /* its press, or its lean */
        if (modal_is_red(g_menu.at[i])) /* a menu's destructive row (menu_modal.c modal_red) */
            fill_box(canvas, &bar, RGBA(MODAL_RED), (unsigned)radius);
        else
            gradient(canvas, bar, tilt_tone(S->tones[0]), tilt_tone(S->tones[1]),
                     tilt_tone(S->tones[4]), radius); /* brighter while pressed (tilt.c) */
        if (!tile) tilt_gleam(canvas, &bar, radius);
        if (leaning) canvas_set_clip_rect(canvas, surface_clip); /* the rows' own clip again */
        if (!tile) caption_paint(w, P(g_menu.at[i], W_PARENT), canvas, &bar);
    } else if (theme()->set_switch && !tk_strcmp(widget_get_type(g_menu.at[i]), "image")) {
        /* a picture tile (Speed's buttons) is its own lit state: no card behind it */
    } else if (home) {
        rect_t dot = { HOME_DOT_X, r.y + (r.h - HOME_DOT) / 2, HOME_DOT, HOME_DOT };
        fill_box(canvas, &dot, 0xffffffff, HOME_DOT / 2);
    } else { /* a grid tile's card, inset in the tile */
        rect_t card = { r.x + TILE_INSET, r.y + TILE_INSET, r.w - 2 * TILE_INSET,
                        r.h - 2 * TILE_INSET };
        fill_box(canvas, &card, RGBA(S->tones[0]), 0);
    }
    st.sel_w = w;
    st.sel_row = i;
    st.sel_y = r.y + r.h / 2, st.sel_h = r.h;
    if (g_menu.at[i] != st.marq_row || g_menu.id[i] != st.marq_id) {
        st.marq_row = g_menu.at[i];
        st.marq_id = g_menu.id[i];
        rearm(&st.marq_timer, marq_moved, 0);
    }
    /* a pop-up button's tile: framed white on any accent. A menu sheet's row is no tile
     * (menu_modal.c), nor is the confirmation card's answer (feedback.c). */
    if (g_menu.kind == 4 && r.w < I(g_menu.w, W_W) && !modal_rows_of(g_menu.w) &&
        !confirm_surface(g_menu.w))
        frame_box(canvas, &r, 0xffffffff, SEL_RADIUS);
    /* Save/restore explicitly: this firmware's canvas_save/restore cover neither clip nor
     * either color, and the global alpha is deliberately never touched. */
    stock_canvas_set_fill_color(canvas, fill_color);
    stock_canvas_set_stroke_color(canvas, stroke_color);
    canvas_set_clip_rect(canvas, &old);
}

static void edit_paint(void *w, void *canvas);

/* Stock's radio images (select, unselect) are emptied (ringnav_image_add): a checked row gets the
 * theme's check, and a playlist's quiet More its dots.
 */
static void paint_radio(void *w, void *canvas) {
    if (!w || !P(canvas, CANVAS_LCD)) return;
    const scheme_t *S = scheme();
    const char *image = widget_get_prop_str(w, "image", "");
    unsigned fill = (unsigned)I(P(canvas, CANVAS_LCD), LCD_FILL_COLOR);
    int x = I(w, W_W) / 2 - 6, y = I(w, W_H) / 2 - 4;
    /* A playlist's quiet More: stock's dots, emptied as a ring (pl_row), in the second line's
     * grey: 4 px dots, 7 px apart. The band's chevron takes their place (pl_fit). */
    if (!tk_strcmp(image, "unselect") &&
        tk_str_start_with(widget_get_prop_str(w, "name", ""), "img_more")) {
        for (int i = 0; i < 3 && !painting_in(st.sel_accent); ++i) {
            rect_t dot = { x - 3 + i * 7, y + 2, 4, 4 };
            fill_box(canvas, &dot, ui_ink(RGBA(S->muted)), 2);
        }
        stock_canvas_set_fill_color(canvas, fill);
        return;
    }
    if (tk_strcmp(image, "select")) return;
    /* settings.values.switch: the theme's 12 px check ending on the image's right end, the right
     * column (docs/oled-design.md Pickers), the accent's, white on the selection */
    unsigned bitmap[64]; /* bitmap_t: width @0 */
    if (!widget_load_image(w, theme_art(painting_in(st.sel_accent) ? "check_w" : "check_a"),
                           bitmap))
        canvas_draw_icon(canvas, bitmap, I(w, W_W) - (int)bitmap[0] / 2, I(w, W_H) / 2);
}

/* A set_switch theme's Gain row (docs/oled-design.md List row): stock's gain_high or gain_low
 * switch, which the image hook empties, read as a key/value row's value, High or Low, in the
 * theme's value grey (set_value_ink), white on the selection, its text ending on the image's right
 * end, which set_row puts on the right column as a value's. */
#define GAIN_VALUE_END                                                                             \
    1 /* the text's right end this far inside the image's, as a value label's                      \
       */
static void paint_gain(void *w, void *canvas, int high, int selected) {
    static const unsigned hi[] = { 'H', 'i', 'g', 'h' }, lo[] = { 'L', 'o', 'w' };
    const unsigned *s = high ? hi : lo;
    unsigned n = high ? 4 : 3, ink = theme()->set_value_ink[ipod_light()];
    int px = theme()->font_px[ROLE_TITLE];
    const char *face = theme_face(s, (int)n, &px);
    canvas_set_font(canvas, face, (unsigned)px);
    int wide = (int)(canvas_measure_text(canvas, s, n) + 0.5f) + 2;
    rect_t r = { I(w, W_W) - GAIN_VALUE_END - wide, 0, wide, I(w, W_H) };
    unsigned raw = st.raw_ink;
    st.raw_ink = 1; /* final in each half, as the value labels' text_ink */
    draw_in_face(canvas, face, s, n, &r, (unsigned)px,
                 selected || !ink ? 0xffffffffu : 0xff000000u | ink);
    st.raw_ink = raw;
}

/* Stock's own marks on a row, its image emptied by the image hook: the theme's chevron on the set's
 * right column for list_into (each row's own), the muted one for list_intodown, and on a
 * set_switch theme a switch, Gain's value or a Wi-Fi network's signal. */
static void paint_into(void *w, void *canvas) {
    if (!w) return;
    const char *image = P(canvas, CANVAS_LCD) ? widget_get_prop_str(w, "image", "") : "";
    if (!image) image = ""; /* a widget whose image isn't a string */
    /* a set_switch theme's switch, whose art the image hook empties: on the box's right end
     * (the set's right column on a settings row) */
    int sw = !theme()->set_switch || image[0] != 's' ? -1
             : !tk_strcmp(image, "switch_on")        ? 1
             : !tk_strcmp(image, "switch_off")       ? 0
                                                     : -1;
    int gain = sw >= 0 || !theme()->set_switch || image[0] != 'g' ? -1
               : !tk_strcmp(image, "gain_high")                   ? 1
               : !tk_strcmp(image, "gain_low")                    ? 0
                                                                  : -1;
    int sig = sw < 0 && gain < 0 && theme()->set_switch && image[0] == 'w' ? wifi_sig(image) : 0;
    if (sig) {
        wifi_mark(w, canvas, sig, st.sel_accent && painting_in(st.sel_accent));
        return;
    }
    if (sw >= 0 && widget_get_visible(w))
        switch_mark(canvas, I(w, W_W), I(w, W_H) / 2, sw,
                    st.sel_accent && painting_in(st.sel_accent));
    else if (gain >= 0 && widget_get_visible(w))
        paint_gain(w, canvas, gain, st.sel_accent && painting_in(st.sel_accent));
    else if (!tk_strcmp(image, "list_into") && !lr_edit_row(w)) /* Edit Library: its grip */
        chevron_icon(w, canvas, 375 - geom_val(V_RIGHT_X) - page_ox(canvas, w), I(w, W_H) / 2,
                     st.sel_accent && painting_in(st.sel_accent));
    else if (!tk_strcmp(image, "list_intodown"))
        chevron_muted(w, canvas, 375 - geom_val(V_RIGHT_X) - page_ox(canvas, w), I(w, W_H) / 2);
}

/* The theme's scrollbar (theme_t.sb_*), a track and thumb while the wheel turns (list_marks.c):
 * stock's mobile bar fades, so it is drawn from the list's border paint, after its children, in
 * final colours. list_bar is the scroll_bar_m
 * of a list that scrolls: stock's max is at least the bar's own h, which can pass a short list's,
 * so the content is read off its sibling. A table's virtual height also stands in for that max
 * (*table): stock sets its bar only from its client's scroll, so before the first scroll the max is
 * none, or an asset's 1000. ponytail: a type compare per list paint under the theme only; cache the
 * bar per list if paints get heavy. */
static void *list_bar(void *w, int *table) {
    const char *type = widget_get_type(w);
    if (tk_strcmp(type, "list_view") && tk_strcmp(type, "table_view")) return (void *)0;
    void *bar = (void *)0;
    int scrolls = 0;
    for (unsigned i = 0, n = widget_count_children(w); i < n; ++i) {
        void *c = widget_get_child(w, i);
        const char *t = widget_get_type(c);
        if (!tk_strcmp(t, "scroll_bar_m"))
            bar = c;
        else if (!tk_strcmp(t, "scroll_view"))
            scrolls |= I(c, VIEW_CONTENT_H) > I(c, W_H);
        else if (!tk_strcmp(t, "table_client"))
            scrolls |= (*table = table_client_get_virtual_h(c)) > I(c, W_H);
    }
    return scrolls ? bar : (void *)0;
}
static void paint_scrollbar(void *canvas, void *bar, int table) {
    const theme_t *T = theme();
    void *list = P(bar, W_PARENT), *lcd = P(canvas, CANVAS_LCD);
    int max = table ? table : I(bar, BAR_MAX), value = I(bar, BAR_VALUE);
    int y0 = I(canvas, CANVAS_Y), h = I(list, W_H);
    unsigned a = wheel_sb_alpha(list);
    int track = T->sb_bottom - T->sb_top;
    if (!a || !lcd || max <= 0) return;
    value = value < 0 ? 0 : value > max ? max : value;
    while (max >> 22) max >>= 1, value >>= 1;
    int at = track * value / max, len = track * h / max, right = T->sb_right;
    if (len > track - at) len = track - at;
    rect_t t = { I(list, W_W) - right - T->sb_w, T->sb_top - y0, T->sb_w, track },
           thumb = { t.x, t.y + at, t.w, len };
    unsigned fill = (unsigned)I(lcd, LCD_FILL_COLOR), L = ipod_light();
    for (int i = 0; i < t.h; ++i) round_row(canvas, &t, T->sb_w / 2, i, a << 24 | T->sb_track[L]);
    for (int i = 0; i < thumb.h; ++i)
        round_row(canvas, &thumb, T->sb_w / 2, i, a << 24 | T->sb_thumb[L]);
    stock_canvas_set_fill_color(canvas, fill);
}

/* A virtual table (Songs' and the other Library tables) draws the row its scroll cuts at its top
 * whole, above its box: under Songs' Shuffle row, and twice under the status row with the band's
 * (list_reach_paint). Its paint keeps to its box (from ringnav_paint_bg to ringnav_paint). */
static void *table_clip_w;
static int table_clip_old[4];
static void table_clip_begin(void *w, void *canvas) {
    int clip[4];
    if (table_clip_w || reach_painting || !w || kind(w) != 2) return;
    clip_within(canvas, table_clip_old, clip, I(canvas, CANVAS_X), I(canvas, CANVAS_Y), I(w, W_W),
                I(w, W_H));
    canvas_set_clip_rect(canvas, clip);
    table_clip_w = w;
}
static void table_clip_end(void *w, void *canvas) {
    if (!w || w != table_clip_w) return;
    canvas_set_clip_rect(canvas, table_clip_old);
    table_clip_w = (void *)0;
}

/* Stock paints children first and calls this with the surface's canvas origin restored. */
int ringnav_paint(void *w, void *canvas) {
    if (st.pull_page && !pull_live()) pull_cancel();
    int result = kb_host_border(w) ? 0 : stock_paint_trampoline(w, canvas); /* keyboard.c */
    table_clip_end(w, canvas);
    night_paint(canvas);
    coverflow_paint(w, canvas);
    keylock_paint(w, canvas);
    feedback_toast_leave(w, canvas);
    search_strip_overlay(w, canvas);
    search_bar_paint(w, canvas); /* a music list's lit search bar */
    confirm_bar_dim(w, canvas);
    edit_paint(w, canvas);
    spot_paint(w, canvas);
    if (theme()->sb_w && !reach_painting) {
        int table = 0;
        void *bar = w ? list_bar(w, &table) : (void *)0;
        if (bar) paint_scrollbar(canvas, bar, table);
    }
    addon_paint(w, canvas);
    radio_paint(w, canvas); /* Internet Radio's loading overlay */
    load_over_paint(w, canvas); /* a page loading over another module's window (list_marks.c) */
    artist_local_paint(w, canvas);
    np_about_paint(w, canvas);
    peq_paint(w, canvas);
    battery_paint(w, canvas);
    album_local_paint(w, canvas);
    games_paint(w, canvas);
    tuner_paint(w, canvas);
    search_strip_paint(w, canvas);
    add_songs_paint(w, canvas);
    inline_slider_paint(w, canvas); /* Settings' inline sliders (inline_slider.c) */
    /* Even a page with no navigable pane must end pending input when it is painted. */
    if (st.center_timer || st.home_surface) {
        void *wm = window_manager(), *top = window_manager_get_top_window(wm);
        if (!usable() || window_manager_is_animating(wm) || top != st.center_top) cancel_center();
        if (!carousel_page(top)) st.home_surface = (void *)0;
    }
    paint_chevrons(w, canvas);
    paint_offline(w, canvas);
    paint_radio(w, canvas);
    paint_into(w, canvas);
    if (st.rt_page) rate_stars_paint(w, canvas);
    if (w && P(w, W_PARENT) &&
        (P(w, W_PARENT) == stream_mark_views[0] || P(w, W_PARENT) == stream_mark_views[1]))
        stream_mark_paint(w, canvas);
    paint_letter(w, canvas);
    az_paint(w, canvas);
    pl_tile_paint(w, canvas);
    pv_paint(w, canvas);
    playing_rows_paint(w, canvas); /* a payload list's playing song (song_rows.c) */
    lr_edit_paint(w, canvas); /* OLED's Edit Library or Edit Home over its page (library_edit.c) */
    upnext_paint(w, canvas);  /* Up Next's rows (upnext.c) */
    paint_cover(w, canvas);
    paint_scrubber(w, canvas);
    if (w && st.np_lrc) { /* one test on every other page's paints (the Cover Flow paint budget) */
        if (P(w, W_PARENT) == st.np_lrc)
            lyrics_paint(w, canvas);
        else if (w == st.np_lrc)
            lyrics_fade(w, canvas);
    }
    if (theme()->marquee_fade) marquee_fade(w, canvas);
    if (w && w == slider_page_win) slider_page_paint(w, canvas);
    storage_paint(w, canvas);
    usb_ui_paint(w, canvas);
#if Q2POD_DEV
    adb_trust_paint(w, canvas); /* Trusted Computers' captions (adb_trust.c) */
#endif
    carremote_paint(w, canvas); /* Settings > Car Remote's pages (carremote_page.c) */
    localsend_paint(w, canvas); /* Settings > Network > LocalSend's pages (localsend_ui.c) */
    bt_recv_paint(w, canvas);    /* Bluetooth receiver mode (bt_recv.c) */
    bt_ui_paint(w, canvas);      /* Settings > Bluetooth's marks (bt_ui.c) */
    bt_stopped_paint(w, canvas); /* Bluetooth Stopped (bt_stopped.c) */
    guest_ui_paint(w, canvas);   /* Guest Play's pages (guest_ui.c) */
    home_np_paint(w, canvas);    /* Home's first row's glyph (home_np.c) */
    sw_update_paint(w, canvas);  /* SD Card Update's pages (sw_update.c) */
    np_skip_end(w, canvas); /* a track change's old and new art and lines (np_skip.c) */
    /* the state row, and a server song loading's spinner in the times row (np_stream.c), but under
     * the rating page at rest full screen (np_rate.c), whose column covers their rows */
    if (!w || w != st.np_win || !st.np_full || !np_rate_on()) {
        if (w && w == st.np_win) np_modes(w, canvas);
        np_stream_paint(w, canvas);
    }
    coverflow_home_clip(w, canvas, 0);
    modal_after(w, canvas); /* a menu sheet's header and fades (menu_modal.c) */
    list_fade(w, canvas);
    charge_paint(w, canvas); /* the plug-in screen (charging.c): last, over the page's own paints */
    st.paint_w = w ? P(w, W_PARENT) : (void *)0;
    return result;
}

/* A scrolling list whose box runs on below the set's list end (y 282): its
 * rows past it peek through a fade into the page's ground, and once scrolled its top rows fade
 * under the status row, as Home (Full)'s do (home.c list_fades; docs/oled-design.md#corners). In
 * Edit Library its end is a row higher and its rows fade into the hint's band (library_edit.c
 * lr_edit_room, lr_edit_foot). Scrolled to its end by the wheel its last row's foot is on the list end (scroll_end, sel_height):
 * so a drag stops there too, its content runs on by the box's rows past the end (LIST_PAD, set
 * again at each paint, as a relayout puts stock's height back; LIST_PADDED the padded height, so a
 * reset shows; LIST_TOP the scroll the paint saw, plus 1, which a reset's clamp gets back). Home
 * has its own (home_fades, home_pad), a menu sheet's rows fade into its material (menu_modal.c
 * modal_after). */
#define LIST_PAD "_list_pad"
#define LIST_PADDED "_list_padded"
#define LIST_TOP "_list_top"
static int list_pad(void *w) {
    return widget_get_prop_int(w, LIST_PADDED, -1) == I(w, VIEW_CONTENT_H)
               ? widget_get_prop_int(w, LIST_PAD, 0)
               : 0;
}
/* w's content padded by pad past the rows' foot. A relayout (a page back on top, a list rebuilt
 * whole, as Settings > Bluetooth's on each scan result) puts stock's height back, unpadded, and
 * AWTK clamps the scroll to it, which put a list scrolled to its end down until its last row sat on
 * the screen's foot: the scroll the last paint saw goes back, held to the wheel's end (a caption that
 * closes took the content's foot a row's part up since, as Settings > Battery's last row after
 * In-Vehicle Mode: its scroll, made for the grown list, lay past the new end). */
static void list_pad_to(void *w, int pad) {
    int content = I(w, VIEW_CONTENT_H) - list_pad(w), h = I(w, W_H), off = I(w, SCROLL_Y),
        kept = widget_get_prop_int(w, LIST_TOP, 0) - 1;
    int relaid = widget_get_prop_int(w, LIST_PADDED, -1) != I(w, VIEW_CONTENT_H);
    widget_set_prop_int(w, LIST_PAD, pad);
    I(w, VIEW_CONTENT_H) = content + pad;
    widget_set_prop_int(w, LIST_PADDED, content + pad);
    int last = content + pad - h; /* the wheel's end: the content's foot on the list end */
    if (relaid && kept > off && last > off)
        scroll_view_set_offset(w, I(w, SCROLL_X), kept < last ? kept : last);
}
/* After stock's list layout (ipod_list_layout): a list list_fade padded gets its pad and its scroll
 * back at once, before a frame draws it. Left to list_fade, at the end of the next paint, that
 * frame drew the rows at the clamp, the pad lower, and the scroll then moved under it: a later
 * repaint of part of the screen at that scroll left both (Settings > Bluetooth's headers twice
 * under its scans), and a page back on top, whose slide-in skips the fades, flicked down and up. */
static void list_relaid(void *w) {
    if (w && widget_get_prop_int(w, LIST_PADDED, -1) >= 0)
        list_pad_to(w, widget_get_prop_int(w, LIST_PAD, 0));
}
/* A row of scroll view `view` grown in place (settings_pages.c cap_step, inline_slider.c heights),
 * after the list's relayout: the view scrolls by whole `step` rows until the row's foot is on or
 * above the list end (list_shown_h), and no further than the wheel's end, the content's foot on the
 * list end. The relayout puts stock's height back, the rows' foot, and clamps the scroll to it, so
 * the content is padded first by the box's rows past the end (list_pad_to, as list_fade pads it),
 * and the scroll is left in LIST_TOP, which list_relaid puts back at the next relayout (seen on the
 * emulator, 2026-11-18: each step's relayout took Reduce Motion's scroll back to the rows' foot). */
void list_grown_foot(void *view, void *row, int step) {
    int shown = list_shown_h(view), h = I(view, W_H),
        over = I(row, W_Y) + I(row, W_H) - I(view, SCROLL_Y) - shown;
    if (over <= 0 || step <= 0) return;
    int top = I(view, SCROLL_Y) + (over + step - 1) / step * step;
    if (shown < h) {
        list_pad_to(view, h - shown);
        if (top > I(view, VIEW_CONTENT_H) - h) top = I(view, VIEW_CONTENT_H) - h;
        widget_set_prop_int(view, LIST_TOP, top + 1);
    }
    scroll_view_set_offset(view, I(view, SCROLL_X), top);
}
static void list_fade(void *w, void *canvas) {
    int k = w && P(canvas, CANVAS_LCD) ? kind(w) : 0;
    if ((k != 1 && k != 2) || reach_painting || home_rest(w, (int *)0) || modal_rows_of(w) ||
        window_manager_is_animating(window_manager()))
        return;
    int y = 0;
    void *wm = window_manager();
    for (void *p = w; p && p != wm; p = P(p, W_PARENT)) y += I(p, W_Y);
    int end = list_fit_end() - lr_edit_room(w), bottom = y + I(w, W_H);
    if (bottom <= end) return; /* a box cut above the end */
    int off = k == 2 ? I(w, TABLE_TOP) : I(w, SCROLL_Y),
        content = k == 2 ? I(w, TABLE_ROWS) * I(w, ROW_HEIGHT) : I(w, VIEW_CONTENT_H) - list_pad(w);
    if (k == 1) { /* a drag's end at the wheel's */
        list_pad_to(w, bottom - end);
        off = I(w, SCROLL_Y);
        widget_set_prop_int(w, LIST_TOP, off + 1);
    }
    int s0 = 1, s1 = 0; /* the selected row, on the screen */
    if (st.sel_w == w) s0 = y + st.sel_y - st.sel_h / 2, s1 = s0 + st.sel_h;
    list_fades(canvas, y, bottom, end, lr_edit_foot(w), off > 0, y + content - off > end, s0, s1);
}


/* Lists under the status row (docs/oled-design.md#list-edges). A list whose box starts at most
 * REACH_TOP down the screen keeps its box, so taps, the selection and its scroll range are as
 * they were and a press on the status row stays the row's; once scrolled, the status row's paint
 * draws the list's rows above its top into the row's band, the list's own paint shifted up by as
 * much (list_reach_paint), fading to the ground at the screen's top edge under the glyphs
 * (home.c list_top_fade; Home's Split, home_fades). A menu sheet's rows stay in the sheet (menu_modal.c). The
 * band repaints when the list's scroll or rows change (list_reach_due), as the list's own
 * invalidate stays inside its box. ponytail: a row in the band that changes on its own (a playing
 * row's level bars) shows its change at the next scroll. */
static struct {
    void *view; /* the list the band showed at the last paint, 0 none */
    int off, rows;
} reach;

/* win's list that runs under the status row: the first visible vertical list in UI order, outside
 * any other scrolling view, with its screen column in *x and row in *y. */
static void *reach_walk(void *w, int x, int y, int depth, int *budget, int *ox, int *oy) {
    if (!w || !widget_get_visible(w) || depth > 8 || --*budget < 0) return (void *)0;
    x += I(w, W_X), y += I(w, W_Y);
    int k = kind(w);
    if (k == 1 || k == 2) {
        *ox = x, *oy = y;
        return w;
    }
    const char *t = widget_get_type(w);
    if (!tk_strcmp(t, "scroll_view") || !tk_strcmp(t, "slide_view")) return (void *)0;
    for (unsigned i = 0, n = widget_count_children(w); i < n; ++i) {
        void *found = reach_walk(widget_get_child(w, i), x, y, depth + 1, budget, ox, oy);
        if (found) return found;
    }
    return (void *)0;
}
/* The list of window win that shows rows under the status row now: scrolled, its box's top on the
 * screen (*y) below the screen's top and at most REACH_TOP; *x its column, *off its scroll. */
static void *reach_find(void *win, int *x, int *y, int *off) {
    int budget = 96;
    void *v = win ? reach_walk(win, 0, 0, 0, &budget, x, y) : (void *)0;
    if (!v || modal_rows_of(v) || *y <= 0 || *y > REACH_TOP) return (void *)0;
    /* Songs' action row (its Shuffle) stands between the band and the list: rows painted above
     * the list would run on above the row, which then floats over the scrolled list (the user's,
     * 2026-11-19). The list keeps its rows in its box there, under the fixed row. */
    void *row = st.action_view, *p = row;
    for (int k = 0; p && p != win && k < 8; ++k) p = P(p, W_PARENT);
    if (p == win && row && widget_get_visible(row) && I(row, W_H) > 0) return (void *)0;
    *off = kind(v) == 2 ? I(v, TABLE_TOP) : I(v, SCROLL_Y);
    return *off > 0 ? v : (void *)0;
}
static int reach_rows(void *v) {
    return kind(v) == 2 ? I(v, TABLE_ROWS) : I(v, VIEW_CONTENT_H);
}
/* The page in front: the wm's topmost window (a dialog over it paints after the status row) */
static void *reach_page(void) {
    void *wm = window_manager();
    for (unsigned i = widget_count_children(wm); i--;) {
        void *c = widget_get_child(wm, i);
        if (c && !tk_strcmp(widget_get_type(c), "window")) return c;
    }
    return (void *)0;
}
static int reach_still(void) {
    float percent = 0;
    return !window_manager_is_animating(window_manager()) && !parallax_slide(&percent);
}

/* From the status row's background paint (bar, canvas at the screen's origin), under its glyphs:
 * page win's scrolled list's rows above its top, in the band, faded to the screen's top edge. */
void list_reach_paint(void *bar, void *win, void *canvas) {
    int x, y, off, old[4], clip[4];
    void *v = reach_still() && P(canvas, CANVAS_LCD) ? reach_find(win, &x, &y, &off) : (void *)0;
    reach.view = (void *)0;
    if (!v) return;
    reach.view = v, reach.off = off, reach.rows = reach_rows(v);
    int d = y < off ? y : off, foot = I(bar, W_H) < y ? I(bar, W_H) : y; /* no row above the first */
    if (!clip_within(canvas, old, clip, x, y - d, I(v, W_W), foot - (y - d))) return;
    int table = kind(v) == 2;
    /* the selection's record, which the shifted paint would move (paint_selection) */
    void *sw = st.sel_w, *sa = st.sel_accent;
    int sr = st.sel_row, sy = st.sel_y, sh = st.sel_h;
    canvas_set_clip_rect(canvas, clip);
    int px = x - I(v, W_X), py = y - I(v, W_Y); /* the list's parent on the screen */
    canvas_translate(canvas, px, py);
    reach_painting = 1;
    I(v, W_Y) -= d, I(v, W_H) += d;
    if (table) I(v, TABLE_TOP) -= d;
    else I(v, SCROLL_Y) -= d;
    widget_paint(v, canvas);
    I(v, W_Y) += d, I(v, W_H) -= d;
    if (table) I(v, TABLE_TOP) += d;
    else I(v, SCROLL_Y) += d;
    reach_painting = 0;
    canvas_translate(canvas, -px, -py);
    st.sel_w = sw, st.sel_accent = sa, st.sel_row = sr, st.sel_y = sy, st.sel_h = sh;
    if (v == coverflow_home_view() && coverflow_home_split(win)) /* Split's follow the glass */
        home_fades(canvas, P(v, W_PARENT), y, x + I(v, W_W), 1, 0);
    else
        list_top_fade(canvas, y);
    canvas_set_clip_rect(canvas, old);
}

/* Before a frame (input.c ringnav_wm_paint): the status row's band again when the page's list
 * under it has moved or changed since the band last showed it, or no longer shows rows there; and
 * the page in front and the band whole once a paint moved Home's selection (paint_selection). */
void list_reach_due(void) {
    void *bar = *(void *const *)system_bar;
    int x, y, off;
    if (sel_repaint) {
        void *page = reach_page();
        sel_repaint = 0;
        if (page) widget_invalidate_force(page, (void *)0);
        if (bar) widget_invalidate_force(bar, (void *)0);
    }
    if (!bar || !reach_still()) return;
    void *v = reach_find(reach_page(), &x, &y, &off);
    if (v == reach.view && (!v || (off == reach.off && reach_rows(v) == reach.rows))) return;
    int band[4] = { 0, 0, I(bar, W_W), I(bar, W_H) };
    reach.view = v, reach.off = v ? off : 0, reach.rows = v ? reach_rows(v) : 0;
    widget_invalidate_force(bar, band);
}
