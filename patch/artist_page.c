/* An artist's page under a set with the inset modal (OLED's; docs/oled-design.md#artist-page): a
 * header (hero_open) of a monogram, the name, a line of counts and one of genre, then an action
 * pair (pair_open), two accent panels side by side, Shuffle and Radio, then drill-down rows (Top
 * Songs, Albums, Singles & EPs, Similar Artists, All Songs) with a count and a chevron, and the
 * cover rows of the pages they open (cover_row; a song's album after its title, cover_after; an
 * artist's monogram in the cover's place, cover_mono). addon.c builds the page from a card app's
 * reply (its artist_view), library_artist.c from the Library's songs on stock's artistinfo_page;
 * the header and the pair take plain strings, so an album's page can use them too.
 *
 * The header stands from screen y HERO_Y, which is above the window (the status row is V_BAR
 * high): its monogram's top is drawn again in the status bar's background paint (artist_cap),
 * clipped to the bar. The pair is a pair of stops above the list (pair_key), as Songs' Shuffle
 * row is (navigation.c shuffle_key): the page opens on Shuffle, a tick moves to Radio, the next
 * to the first row; a tick up from the first row comes back to Radio. A page with Shuffle alone
 * (a library artist's, library_artist.c) has one stop, the pair's width. While a stop is lit the
 * list's own bar is hidden (pair_holds). Centre or a tap on a panel presses its action. Each draws
 * in final colours, Light Mode's flip applied by ui_ink as for any payload text. */
#include "navigation.h"

#define HERO_Y 52    /* the header's top on screen: the monogram's */
#define HERO_MONO 64 /* the monogram's diameter, at the text column */
#define HERO_TEXT 102
#define HERO_NAME_Y 66 /* each line's centre on screen */
#define HERO_SUB_Y 88
#define HERO_META_Y 107
#define PAIR_Y 128 /* the pair's row; its panels from PAIR_Y + 2, PAIR_H high */
#define PAIR_H 28
#define PAIR_GAP 8
#define PAIR_R 8
#define PAIR_ICON 16 /* tools/art.py PAIR_ICON_PX */
#define PAIR_LABEL_DX 22
#define AR_LIST_Y 168  /* the drill-down rows' first, on screen */
#define AR_COUNT_DX 44 /* a drill-down row's count, right-aligned this far from the right edge */
#define AR_THUMB_R 4
#define AR_FILL 0x1c1c1e    /* a pair's panel at rest */
#define AR_MONO 0x2c2c2e    /* the monogram's disc, and a cover's placeholder */
#define AR_MONO_ON 0x3a3a3c /* an artist row's disc on the selection */
#define AR_SECOND 0xababab  /* Text Secondary */
#define AR_THIRD 0x858585   /* Text Tertiary */
/* AWTK's sizes for the design's px (Inter's line is 1.21 em: assets/themes/oled/theme.xml) */
#define PX_30 36
#define PX_20 24
#define PX_18 22
#define PX_17 21
#define PX_15 18
#define PX_14 17
#define PX_13 16

#define AR_NAME "_ar_name" /* the header's strings, kept on it */
#define AR_SUB "_ar_sub"
#define AR_META "_ar_meta"
#define AR_LEFT "_ar_left" /* a pair panel's label and icon stem */
#define AR_ICON "_ar_icon"
#define AR_COUNT "_ar_count" /* a drill-down row's count */
#define AR_RIGHT "_ar_right" /* a cover row's text at the right (a year, a length) */
#define AR_THUMB "_ar_thumb" /* its cover's size, its file in AR_COVER, "" for a placeholder */
#define AR_COVER "_ar_cover"
#define AR_AFTER "_ar_after" /* a song row's album, after its title */
#define AR_TITLE "_ar_title" /* a cover row's title, for its monogram and the album's place */
#define AR_PX "_ar_px"       /* a cover row's title's AWTK size */
#define AR_DISC "_ar_disc"   /* an artist row: its name's monogram in the cover's place */

static struct {
    void *head, *list, *panel[2];
    int ctx[2];        /* each panel's press's context */
    signed char *stop; /* the level's: 0 the list's row, 1 Shuffle, 2 Radio */
    int (*press)(void *, void *);
    unsigned cap; /* the bar asked to paint the monogram's top (cap_due), 0 not yet */
} ar __attribute__((section(".scratch")));

static int ar_gone(void *w, void *event) {
    (void)event;
    if (w == ar.head) memset(&ar, 0, sizeof ar);
    return 0;
}

/* s's UTF-8 as characters in out (at most n - 1, then 0); a malformed byte stands for itself. */
static unsigned ar_wide(const char *s, unsigned *out, unsigned n) {
    unsigned k = 0;
    const unsigned char *p = (const unsigned char *)s;
    while (p && *p && k + 1 < n) {
        unsigned c = *p++, more = c >= 0xf0 ? 3 : c >= 0xe0 ? 2 : c >= 0xc0 ? 1 : 0;
        if (more) c &= 0x3f >> more;
        for (; more && (*p & 0xc0) == 0x80; --more) c = c << 6 | (*p++ & 0x3f);
        out[k++] = c;
    }
    out[k] = 0;
    return k;
}

static int ar_measure(void *canvas, const char *face, int px, const unsigned *s, unsigned n) {
    canvas_set_font(canvas, face, (unsigned)px);
    return (int)(canvas_measure_text(canvas, s, n) + 0.5f);
}

/* s in Inter (medium: Inter Medium) at AWTK size px, in rgb (0xRRGGBB; final when raw), its row
 * centred on y, from x (align 2) or ending at x (align 3), at most w wide: an ellipsis ends a
 * longer one. A character Inter lacks takes the theme's fallback face. Returns its width. */
static int ar_text(void *canvas, const char *s, int medium, int px, unsigned rgb, int raw, int x,
                   int y, int w, int align) {
    unsigned buf[96], n = ar_wide(s, buf, sizeof buf / sizeof *buf);
    if (!n || w <= 0) return 0;
    int fpx = px;
    const char *face = theme_face(buf, (int)n, &fpx);
    if (medium && face && !tk_strcmp(face, "inter")) face = "inter-medium";
    int ink = ar_measure(canvas, face, fpx, buf, n);
    while (n > 1 && ink > w)
        buf[--n - 1] = 0x2026, buf[n] = 0, ink = ar_measure(canvas, face, fpx, buf, n);
    rect_t r = { align == 3 ? x - ink : x, y - fpx, ink + 1, 2 * fpx };
    unsigned text = (unsigned)I(P(canvas, CANVAS_LCD), LCD_TEXT_COLOR),
             was = ui_ink_raw((unsigned)raw);
    int align_v = I(canvas, CANVAS_ALIGN_V), align_h = I(canvas, CANVAS_ALIGN_H);
    canvas_set_text_color(canvas, RGBA(rgb));
    I(canvas, CANVAS_ALIGN_V) = 1;
    I(canvas, CANVAS_ALIGN_H) = 2;
    canvas_draw_text_in_rect(canvas, buf, n, &r);
    I(canvas, CANVAS_ALIGN_V) = align_v;
    I(canvas, CANVAS_ALIGN_H) = align_h;
    ui_ink_raw(was);
    stock_canvas_set_text_color(canvas, text);
    return ink;
}

/* A disc or rounded box over r in a final 0xRRGGBB, its corners R. */
static void ar_box(void *canvas, const rect_t *r, int R, unsigned rgb) {
    unsigned fill = (unsigned)I(P(canvas, CANVAS_LCD), LCD_FILL_COLOR);
    for (int i = 0; i < r->h; ++i) round_row(canvas, r, R, i, 0xff000000u | rgb);
    stock_canvas_set_fill_color(canvas, fill);
}

static unsigned ar_accent(void) { return scheme()->tones[0] & 0xffffffu; }

/* The square root of x > 0 (the payload links no libm): Newton's steps from above. */
static float ar_sqrt(float x) {
    float s = x > 1 ? x : 1;
    for (int k = 0; k < 12; ++k) s = (s + x / s) / 2;
    return s;
}

/* A disc d px across from (x, y) in a final 0xRRGGBB, each row's ends antialiased by how much of
 * their pixel the circle covers; the canvas's clip cuts it (the status bar's, for its top). */
static void ar_disc(void *canvas, int x, int y, int d, unsigned rgb) {
    unsigned fill = (unsigned)I(P(canvas, CANVAS_LCD), LCD_FILL_COLOR);
    float r = d / 2.0f;
    for (int i = 0; i < d; ++i) {
        float dy = r - i - 0.5f, half = ar_sqrt(r * r - dy * dy), left = r - half;
        int in = (int)left + 1, cover = (int)((in - left) * 255.0f);
        if (in < (int)r) {
            scrub_row(canvas, 0xff000000u | rgb, x + in, y + i, d - 2 * in);
            scrub_row(canvas, (unsigned)cover << 24 | rgb, x + in - 1, y + i, 1);
            scrub_row(canvas, (unsigned)cover << 24 | rgb, x + d - in, y + i, 1);
        }
    }
    stock_canvas_set_fill_color(canvas, fill);
}

/* A monogram: a disc d px across from (x, y) in rgb (final once flipped for Light Mode) and the
 * name's first letter centred on it in Inter Medium at AWTK size px, white. */
static void mono(void *canvas, const char *name, int x, int y, int d, int px, unsigned rgb) {
    unsigned c[2];
    char letter[8] = "";
    ar_disc(canvas, x, y, d, flip_if(ipod_light(), rgb));
    if (ar_wide(name, c, 2)) { /* the name's first character, upper case when ASCII */
        unsigned k = 0, ch = c[0] >= 'a' && c[0] <= 'z' ? c[0] - 32 : c[0];
        const unsigned char *p = (const unsigned char *)name;
        if (ch != c[0])
            letter[k++] = (char)ch;
        else
            do letter[k++] = (char)*p++;
            while (k < 4 && (*p & 0xc0) == 0x80);
        letter[k] = 0;
    }
    int fpx = px;
    unsigned buf[4];
    unsigned n = ar_wide(letter, buf, 4);
    const char *face = theme_face(buf, (int)n, &fpx);
    if (face && !tk_strcmp(face, "inter")) face = "inter-medium";
    int w = n ? ar_measure(canvas, face, fpx, buf, n) : 0;
    ar_text(canvas, letter, 1, px, 0xffffff, 0, x + (d - w) / 2, y + d / 2, d, 2);
}

/* The header's monogram, its top dy px down the canvas from the header's top on screen. */
static void hero_mono(void *canvas, const char *name, int dy) {
    mono(canvas, name, geom_val(V_TEXT_X), dy, HERO_MONO, PX_30, AR_MONO);
}

/* The header in body (the page's window, which starts V_BAR down the screen): the monogram of
 * name, name, sub under it and meta (none when empty) under that, kept as its props; its view
 * reaches down to the drill-down rows, so the pair's panels are its children. */
void *hero_open(void *body, const char *name, const char *sub, const char *meta) {
    int bar = geom_val(V_BAR);
    void *head =
        widget_factory_create_widget(widget_factory(), "view", body, 0, 0, 375, AR_LIST_Y - bar);
    widget_set_prop_str(head, AR_NAME, name ? name : "");
    widget_set_prop_str(head, AR_SUB, sub ? sub : "");
    widget_set_prop_str(head, AR_META, meta ? meta : "");
    memset(&ar, 0, sizeof ar);
    ar.head = head;
    widget_on(head, EVT_DESTROY, ar_gone, head);
    return head;
}

/* The header artist_paint draws, 0 when none: the last hero_open's while it stands. */
void *hero_now(void) { return ar.head; }

/* The status bar's background (navigation.c ringnav_paint_bg): the monogram's top, which stands
 * above the window, while the header's page is on top (or under an inset modal, which dims the bar
 * with it) and still: a sliding page leaves the bar as it is. */
void artist_cap(void *bar, void *canvas) {
    void *wm = window_manager(), *win = ar.head ? window_of(ar.head) : (void *)0,
         *top = window_manager_get_top_window(wm);
    if (!win || !P(canvas, CANVAS_LCD) || (win != top && !modal_win(top)) ||
        window_manager_is_animating(wm))
        return;
    hero_mono(canvas, widget_get_prop_str(ar.head, AR_NAME, ""), HERO_Y - I(bar, W_Y));
}
/* The bar painted again once the page has come to rest: nothing else repaints it then. Asked from
 * the header's first paint, where an invalidate would be lost, so from the next pass on, until the
 * window manager has no slide running. */
static int cap_due(const void *info) {
    (void)info;
    void *bar = *(void *const *)system_bar;
    if (!ar.head) return 0;
    if (window_manager_is_animating(window_manager())) return 8; /* RET_REPEAT */
    if (bar) widget_invalidate_force(bar, (void *)0);
    return 0;
}

/* The action pair under the header: label[k] after the theme's pair_<icon[k]> art, each panel a
 * tap target whose press (the page's row click) gets ctx[k]; stop is the level's lit stop, kept
 * by the page across its rebuilds. list: the rows under it, whose bar hides while a stop is lit.
 * With label[1] 0 the first is the only panel, the pair's whole width. */
void pair_open(void *head, void *list, const char *const label[2], const char *const icon[2],
               const int ctx[2], int (*press)(void *, void *), signed char *stop) {
    int x = geom_val(V_TEXT_X), n = label[1] ? 2 : 1, w = (375 - 2 * x - (n - 1) * PAIR_GAP) / n,
        y = PAIR_Y - geom_val(V_BAR);
    for (int k = 0; k < n; ++k) {
        void *p = button_create(head, x + k * (w + PAIR_GAP), y, w, PAIR_H + 4);
        widget_use_style(p, "s_btn_listitem"); /* a tap's click; pair_paint draws it */
        widget_set_prop_str(p, AR_LEFT, label[k]);
        widget_set_prop_str(p, AR_ICON, icon[k]);
        widget_on(p, EVT_CLICK, press, (void *)(long)ctx[k]);
        ar.panel[k] = p, ar.ctx[k] = ctx[k];
    }
    ar.list = list, ar.stop = stop, ar.press = press;
}

/* The wheel and Centre on the page (navigation.c ringnav, before the list takes them): 1 when the
 * pair took it. */
int pair_key(void *top, void *w, int dir) {
    if (!ar.stop || !ar.list || w != ar.list || window_of(ar.head) != top) return 0;
    signed char *s = ar.stop;
    int last = ar.panel[1] ? 2 : 1;
    if (!*s) {
        if (dir >= 0 || widget_get_prop_int(w, SEL, -1) > 0) return 0;
        *s = (signed char)last; /* up from the first row: Radio, or a lone Shuffle */
    } else if (dir > 0) {
        *s = *s < last ? *s + 1 : 0;
        /* down off the pair: the first row, whatever the list kept (a stock page's position
         * memory restores one under the pair) */
        if (!*s) ringnav_select(w, 0, widget_get_prop_int(w, COUNT, 0));
    } else if (dir < 0) {
        if (*s == 1) return 1; /* the first stop: nothing above */
        *s = 1;
    } else {
        ar.press((void *)(long)ar.ctx[*s - 1], (void *)0);
        return 1;
    }
    st.touch_mode = 0;
    widget_invalidate_force(ar.head, (void *)0);
    widget_invalidate_force(w, (void *)0);
    return 1;
}

/* Whether the list w's bar is hidden: a pair stop above it has the selection. */
int pair_holds(void *w) { return ar.stop && *ar.stop && w == ar.list; }

/* A pair panel: #1C1C1E, or the accent with its content white when its stop is lit; its glyph and
 * label centred together. */
static void pair_paint(void *p, void *canvas) {
    int k = p == ar.panel[1], on = ar.stop && *ar.stop == k + 1 && !st.touch_mode;
    unsigned accent = ar_accent();
    rect_t r = { 0, 2, I(p, W_W), PAIR_H };
    int raw = ui_ink_raw(1);
    ar_box(canvas, &r, PAIR_R, on ? accent : flip_if(ipod_light(), AR_FILL));
    ui_ink_raw((unsigned)raw);
    const char *label = widget_get_prop_str(p, AR_LEFT, "");
    char art[24];
    unsigned buf[32], n = ar_wide(label, buf, 32);
    int px = PX_17;
    const char *face = theme_face(buf, (int)n, &px);
    if (face && !tk_strcmp(face, "inter")) face = "inter-medium";
    int tw = n ? ar_measure(canvas, face, px, buf, n) : 0, x = (r.w - tw - PAIR_LABEL_DX) / 2;
    tk_snprintf(art, sizeof art, "pair_%s_%s", widget_get_prop_str(p, AR_ICON, ""), on ? "w" : "a");
    paint_art(p, canvas, theme_art(art), x, r.y + (PAIR_H - PAIR_ICON) / 2);
    ar_text(canvas, label, 1, PX_17, on ? 0xffffff : accent, 1, x + PAIR_LABEL_DX, r.y + PAIR_H / 2,
            r.w, 2);
}

/* The header: the monogram (its top above the window is artist_cap's) and the three lines. */
static void hero_paint(void *head, void *canvas) {
    int bar = geom_val(V_BAR), right = 375 - geom_val(V_RIGHT_X), w = right - HERO_TEXT;
    const char *meta = widget_get_prop_str(head, AR_META, "");
    hero_mono(canvas, widget_get_prop_str(head, AR_NAME, ""), HERO_Y - bar);
    ar_text(canvas, widget_get_prop_str(head, AR_NAME, ""), 1, PX_20, 0xffffff, 0, HERO_TEXT,
            HERO_NAME_Y - bar, w, 2);
    ar_text(canvas, widget_get_prop_str(head, AR_SUB, ""), 0, PX_15, AR_SECOND, 0, HERO_TEXT,
            HERO_SUB_Y - bar, w, 2);
    if (meta && *meta)
        ar_text(canvas, meta, 0, PX_13, AR_THIRD, 0, HERO_TEXT, HERO_META_Y - bar, w, 2);
    if (!ar.cap) ar.cap = timer_add(cap_due, (void *)0, 0) | 1;
}

/* Whether a row of a list is lit: the list's selection, shown, and no pair stop over it. */
static int ar_lit(void *item) {
    void *list = P(item, W_PARENT);
    return !st.touch_mode && !pair_holds(list) &&
           widget_get_prop_int(list, SEL, -1) == widget_index_of(item);
}

/* The drill-down rows' list (page_list's view, of n rows) moved under the pair, as many whole rows
 * in view as end by the set's list_fit_end; its rows' height. */
int drill_list(void *view, int n) {
    void *lv = P(view, W_PARENT);
    int h = page_h(48), room = list_fit_end() - AR_LIST_Y, rows = n * h;
    if (room > 0 && rows > room - room % h) rows = room - room % h;
    widget_move_resize(lv, I(lv, W_X), AR_LIST_Y - geom_val(V_BAR), I(lv, W_W), rows);
    widget_resize(view, I(view, W_W), rows);
    return h;
}

/* A drill-down row's count, right-aligned AR_COUNT_DX from the edge, before the chevron. */
void drill_count(void *item, const char *count) { widget_set_prop_str(item, AR_COUNT, count); }

/* A row of h px in view at index: a cover (cover, a file:// URL, or a placeholder when 0) thumb px
 * square at the text column, title in the theme's row face (px, AWTK's size) after it, and right
 * (none when empty) right-aligned on the right column in Text Tertiary. click gets ctx. */
void *cover_row(void *view, int index, int h, int thumb, int px, const char *title,
                const char *right, const char *cover, int (*click)(void *, void *), int ctx) {
    int x = geom_val(V_TEXT_X), tx = x + thumb + 10, end = 375 - geom_val(V_RIGHT_X);
    void *item = page_row_detail(view, ctx, title, 0, click);
    void *label = widget_get_child(item, 0);
    widget_move_resize(item, 0, index * h, 375, h);
    if (right && *right) end -= 50;
    widget_move_resize(label, tx - 2, 0, end - tx + 2, h);
    if (px) widget_set_prop_int(label, "style:normal:font_size", px);
    /* a long title rests on its ellipsis and scrolls only on the selection, as an album's tracks */
    widget_set_prop_int(label, "loop", 0);
    hscroll_label_set_only_focus(label, 1);
    hscroll_label_set_ellipses(label, 1);
    widget_set_prop_int(item, AR_PX, px ? px : PX_20);
    widget_set_prop_str(item, AR_TITLE, title);
    widget_set_prop_str(item, AR_RIGHT, right ? right : "");
    widget_set_prop_int(item, AR_THUMB, thumb);
    widget_set_prop_int(item, "_ar_row", ctx); /* addon.c addon_hold's */
    if (cover) {
        void *img = image_create(item, x, (h - thumb) / 2, thumb, thumb);
        image_set_draw_type(img, 4); /* scaled to fit */
        image_base_set_image(img, cover);
        widget_set_prop_str(item, AR_COVER, cover);
    }
    return item;
}

/* A song's cover row's album (none when empty), drawn after its title in Inter Regular 14, Text
 * Tertiary, where the title leaves 30 px or more of its column. */
void cover_after(void *item, const char *album) {
    if (album && *album) widget_set_prop_str(item, AR_AFTER, album);
}

/* An artist's cover row: a monogram of its title in the cover's place, and the chevron. */
void cover_mono(void *item) {
    widget_set_prop_int(item, AR_DISC, 1);
    row_into(item, I(item, W_H));
}

/* A cover row: the placeholder under no cover, the cover's corners rounded into what is behind
 * them (the selection's accent or the page's black), and its right-hand text. */
static void cover_paint(void *item, void *canvas) {
    int thumb = widget_get_prop_int(item, AR_THUMB, 0), h = I(item, W_H), on = ar_lit(item);
    const char *right = widget_get_prop_str(item, AR_RIGHT, ""),
               *cover = widget_get_prop_str(item, AR_COVER, "");
    rect_t r = { geom_val(V_TEXT_X), (h - thumb) / 2, thumb, thumb };
    unsigned fill = (unsigned)I(P(canvas, CANVAS_LCD), LCD_FILL_COLOR);
    const char *after = widget_get_prop_str(item, AR_AFTER, "");
    if (widget_get_prop_int(item, AR_DISC, 0)) {
        mono(canvas, widget_get_prop_str(item, AR_TITLE, ""), r.x, r.y, thumb, PX_15,
             on ? AR_MONO_ON : AR_MONO);
    } else if (!cover || !*cover) {
        ar_box(canvas, &r, AR_THUMB_R, flip_if(ipod_light(), AR_MONO));
    } else { /* the four corners outside the arcs, in the colour behind */
        unsigned behind = on ? ar_accent() : flip_if(ipod_light(), 0);
        for (int i = 0; i < AR_THUMB_R; ++i) {
            int n = round_inset(AR_THUMB_R, i, thumb);
            for (int b = 0; b < 2 && n; ++b) {
                int y = r.y + (b ? thumb - 1 - i : i);
                scrub_row(canvas, 0xff000000u | behind, r.x, y, n);
                scrub_row(canvas, 0xff000000u | behind, r.x + thumb - n, y, n);
            }
        }
    }
    stock_canvas_set_fill_color(canvas, fill);
    if (right && *right)
        ar_text(canvas, right, 0, PX_15, on ? 0xffffff : AR_THIRD, 0, 375 - geom_val(V_RIGHT_X),
                h / 2, 60, 3);
    if (after && *after) { /* the title's ink and two spaces, then what is left of its column */
        char title[160];
        unsigned buf[96], n;
        int x = r.x + thumb + 10, w = 375 - geom_val(V_RIGHT_X) - (right && *right ? 50 : 0) - x,
            px = widget_get_prop_int(item, AR_PX, PX_20);
        tk_snprintf(title, sizeof title, "%s  ", widget_get_prop_str(item, AR_TITLE, ""));
        n = ar_wide(title, buf, sizeof buf / sizeof *buf);
        const char *face = theme_face(buf, (int)n, &px);
        int tw = n ? ar_measure(canvas, face, px, buf, n) : 0;
        if (tw < w - 30)
            ar_text(canvas, after, 0, PX_14, on ? 0xffffff : AR_THIRD, 0, x + tw, h / 2, w - tw, 2);
    }
}

/* ringnav_paint's, after stock painted w (addon.c addon_paint, while its page shows one of these
 * layouts): the header, its pair, a drill-down row's count and a cover row's cover and text. */
void artist_paint(void *w, void *canvas) {
    if (!w || !P(canvas, CANVAS_LCD)) return;
    if (ar.head && w == ar.head) {
        hero_paint(w, canvas);
        return;
    }
    if (ar.head && (w == ar.panel[0] || w == ar.panel[1])) {
        pair_paint(w, canvas);
        return;
    }
    const char *count = widget_get_prop_str(w, AR_COUNT, (const char *)0);
    if (count && *count) {
        ar_text(canvas, count, 0, PX_15, ar_lit(w) ? 0xffffff : AR_THIRD, 0, 375 - AR_COUNT_DX,
                I(w, W_H) / 2, 60, 3);
        return;
    }
    if (widget_get_prop_int(w, AR_THUMB, 0)) cover_paint(w, canvas);
}
