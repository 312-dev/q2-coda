/* An artist's page (docs/oled-design.md#artist-page): a
 * header (hero_open) of a monogram, the name, a line of counts and one of genre, then an action
 * pair (pair_open), two accent panels side by side, Shuffle and Radio, then drill-down rows (Top
 * Songs, Albums, Singles & EPs, Similar Artists, All Songs) with a count and a chevron, and the
 * cover rows of the pages they open (cover_row; a song's album after its title, cover_after; an
 * artist's monogram in the cover's place, cover_mono). addon.c builds the page from a card app's
 * reply (its artist_view), library_artist.c from the Library's songs on stock's artistinfo_page;
 * the header and the pair take plain strings, so an album's page uses them too
 * (docs/oled-design.md#album-page): its header shows the album's cover (hero_cover) in the
 * monogram's place, its pair is Play and Shuffle, and its rows are the album's tracks
 * (track_row), from a card app's reply (addon.c album_view) or on stock's album pages
 * (library_album.c).
 *
 * The header stands from screen y HERO_Y, above the status row's foot (V_BAR), where its view
 * starts: its monogram's top paints over the full-bleed window's rows under the status row. The pair is a pair of stops above the list (pair_key), as Songs' Shuffle
 * row is (navigation.c shuffle_key): the page opens on Shuffle, a tick moves to Radio, the next
 * to the first row; a tick up from the first row comes back to Radio. A page with Shuffle alone
 * (a library artist's, library_artist.c) has one stop, the pair's width. While a stop is lit the
 * list's own bar is hidden (pair_holds). Centre or a tap on a panel presses its action. Each draws
 * in final colours, Light Mode's flip applied by ui_ink as for any payload text. */
#include "navigation.h"

#define HERO_Y 52    /* the header's top on screen: the monogram's */
#define HERO_MONO 64 /* the monogram's diameter, at the text column; an album's cover's side */
#define HERO_ART_R 6 /* an album's cover's corners */
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
#define TRACK_NUM_X 38      /* a track's number, right-aligned to this on screen */
#define TRACK_TITLE_X 50    /* and its title's first column */
#define AR_FILL RGB_RAISED  /* a pair's panel at rest */
#define AR_MONO RGB_RAISED_2 /* the monogram's disc, and a cover's placeholder */
#define AR_MONO_ON RGB_OFF   /* an artist row's disc on the selection */

#define AR_NAME "_ar_name" /* the header's strings, kept on it */
#define AR_SUB "_ar_sub"
#define AR_META "_ar_meta"
#define AR_LEFT "_ar_left" /* a pair panel's label and icon stem */
#define AR_ICON "_ar_icon"
#define AR_COUNT "_ar_count" /* a drill-down row's count */
#define AR_RIGHT "_ar_right" /* a cover row's text at the right (a year, a length) */
#define AR_THUMB "_ar_thumb" /* its cover's size, its file in AR_COVER, "" for a placeholder */
#define AR_COVER "_ar_cover"
#define AR_AFTER "_ar_after"   /* a song row's album, after its title */
#define AR_TITLE "_ar_title"   /* a cover row's title, for its monogram and the album's place */
#define AR_PX "_ar_px"         /* a cover row's title's AWTK size */
#define AR_DISC "_ar_disc"     /* an artist row: its name's monogram in the cover's place */
#define AR_SQUARE "_ar_square" /* the header's art is an album's cover: its file in AR_COVER */
#define AR_NUM "_ar_num"       /* a track row's number, "" for none */

static struct {
    void *head, *list, *panel[2];
    int ctx[2];        /* each panel's press's context */
    signed char *stop; /* the level's: 0 the list's row, 1 Shuffle, 2 Radio */
    int (*press)(void *, void *);
} ar __attribute__((section(".scratch")));

static int ar_gone(void *w, void *event) {
    (void)event;
    if (w != ar.head) return 0;
    const char *cover = widget_get_prop_str(w, AR_COVER, "");
    if (cover && *cover) image_manager_unload_bitmap_by_name(image_manager(), cover);
    memset(&ar, 0, sizeof ar);
    return 0;
}

/* A disc or rounded box over r in a final 0xRRGGBB, its corners R. */
static void ar_box(void *canvas, const rect_t *r, int R, unsigned rgb) {
    unsigned fill = (unsigned)I(P(canvas, CANVAS_LCD), LCD_FILL_COLOR);
    for (int i = 0; i < r->h; ++i) round_row(canvas, r, R, i, 0xff000000u | rgb);
    stock_canvas_set_fill_color(canvas, fill);
}

/* A square image's corners over r rounded to R, anti-aliased into behind, the final 0xRRGGBB of
 * what stands behind it (nav/row_paint.c corner_mask). Up Next's and Control Center's covers too.
 */
void ar_corners(void *canvas, const rect_t *r, int R, unsigned behind) {
    corner_mask(canvas, r, R, behind);
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
    if (ui_wide(name, c, 2)) { /* the name's first character, upper case when ASCII */
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
    unsigned n = ui_wide(letter, buf, 4);
    const char *face = theme_face(buf, (int)n, &fpx);
    if (face && !tk_strcmp(face, "inter")) face = "inter-medium";
    int w = n ? ui_measure_w(canvas, face, fpx, buf, n) : 0;
    ui_text(canvas, letter, 1, px, 0xffffff, 0, x + (d - w) / 2, y + d / 2, d, 2);
}

/* An album's cover in the header, on screen (art_zoom.c: Now Playing grows from it). */
int hero_cover_box(rect_t *r) {
    r->x = geom_val(V_TEXT_X), r->y = HERO_Y, r->w = r->h = HERO_MONO;
    return 1;
}

/* The header's art, its top dy px down the canvas from the header's top on screen: the name's
 * monogram, or an album's cover (hero_cover) cropped square to HERO_MONO px with HERO_ART_R
 * corners, the #2C2C2E placeholder while it has none. */
static void hero_art(void *canvas, void *head, int dy) {
    unsigned bitmap[64]; /* bitmap_t: width @0, height @4 */
    rect_t r = { geom_val(V_TEXT_X), dy, HERO_MONO, HERO_MONO };
    const char *cover = widget_get_prop_str(head, AR_COVER, "");
    if (!widget_get_prop_int(head, AR_SQUARE, 0)) {
        mono(canvas, widget_get_prop_str(head, AR_NAME, ""), r.x, dy, HERO_MONO, TYPE(22), AR_MONO);
    } else if (!cover || !*cover || widget_load_image(head, cover, bitmap) || !bitmap[0] ||
               !bitmap[1]) {
        ar_box(canvas, &r, HERO_ART_R, flip_if(ipod_light(), AR_MONO));
    } else { /* the middle square of a cover that isn't one */
        int w = (int)bitmap[0], h = (int)bitmap[1], side = w < h ? w : h;
        rect_t src = { (w - side) / 2, (h - side) / 2, side, side };
        unsigned fill = (unsigned)I(P(canvas, CANVAS_LCD), LCD_FILL_COLOR);
        canvas_draw_image(canvas, bitmap, &src, &r);
        ar_corners(canvas, &r, HERO_ART_R, flip_if(ipod_light(), 0));
        stock_canvas_set_fill_color(canvas, fill);
    }
}

/* w's top on the screen. */
static int ar_screen_y(void *w) {
    int y = 0;
    for (void *wm = window_manager(); w && w != wm; w = P(w, W_PARENT)) y += I(w, W_Y);
    return y;
}

/* The header in body (the page's window or a view in it), from the status row's foot, V_BAR down
 * the screen: the monogram of name, name, sub under it and meta (none when empty) under that, kept
 * as its props; its view reaches down to the drill-down rows, so the pair's panels are its
 * children. hero_cover makes it an album's, hero_text writes its lines again. */
void *hero_open(void *body, const char *name, const char *sub, const char *meta) {
    int bar = geom_val(V_BAR);
    void *head = widget_factory_create_widget(widget_factory(), "view", body, 0,
                                              bar - ar_screen_y(body), 375, AR_LIST_Y - bar);
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

/* The header head shows an album's cover, url (a file:// URL; 0 or "" the placeholder), in the
 * monogram's place. */
void hero_cover(void *head, const char *url) {
    widget_set_prop_int(head, AR_SQUARE, 1);
    widget_set_prop_str(head, AR_COVER, url ? url : "");
    widget_invalidate_force(head, (void *)0);
}

/* The header head's lines, name (0 keeps its own), sub and meta, written again when one changed. */
void hero_text(void *head, const char *name, const char *sub, const char *meta) {
    if (!name) name = widget_get_prop_str(head, AR_NAME, "");
    if (!tk_strcmp(widget_get_prop_str(head, AR_NAME, ""), name) &&
        !tk_strcmp(widget_get_prop_str(head, AR_SUB, ""), sub) &&
        !tk_strcmp(widget_get_prop_str(head, AR_META, ""), meta))
        return;
    widget_set_prop_str(head, AR_NAME, name);
    widget_set_prop_str(head, AR_SUB, sub);
    widget_set_prop_str(head, AR_META, meta);
    widget_invalidate_force(head, (void *)0);
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
    unsigned buf[32], n = ui_wide(label, buf, 32);
    int px = TYPE(17);
    const char *face = theme_face(buf, (int)n, &px);
    if (face && !tk_strcmp(face, "inter")) face = "inter-medium";
    int tw = n ? ui_measure_w(canvas, face, px, buf, n) : 0, x = (r.w - tw - PAIR_LABEL_DX) / 2;
    tk_snprintf(art, sizeof art, "pair_%s_%s", widget_get_prop_str(p, AR_ICON, ""), on ? "w" : "a");
    paint_art(p, canvas, theme_art(art), x, r.y + (PAIR_H - PAIR_ICON) / 2);
    ui_text(canvas, label, 1, TYPE(17), on ? 0xffffff : accent, 1, x + PAIR_LABEL_DX,
            r.y + PAIR_H / 2, r.w, 2);
}

/* The header: the monogram or cover (its top above the view's, under the status row) and the
 * three lines. */
static void hero_paint(void *head, void *canvas) {
    int bar = geom_val(V_BAR), right = 375 - geom_val(V_RIGHT_X), w = right - HERO_TEXT;
    const char *meta = widget_get_prop_str(head, AR_META, "");
    hero_art(canvas, head, HERO_Y - bar);
    ui_text(canvas, widget_get_prop_str(head, AR_NAME, ""), 1, TYPE(20), 0xffffff, 0, HERO_TEXT,
            HERO_NAME_Y - bar, w, 2);
    ui_text(canvas, widget_get_prop_str(head, AR_SUB, ""), 0, TYPE(15), RGB_SECONDARY, 0, HERO_TEXT,
            HERO_SUB_Y - bar, w, 2);
    if (meta && *meta)
        ui_text(canvas, meta, 0, TYPE(13), RGB_TERTIARY, 0, HERO_TEXT, HERO_META_Y - bar, w, 2);
}

/* Whether a row of a list is lit: the list's selection, shown, and no pair stop over it. */
static int ar_lit(void *item) {
    void *list = P(item, W_PARENT);
    return !st.touch_mode && !pair_holds(list) &&
           widget_get_prop_int(list, SEL, -1) == widget_index_of(item);
}

/* The drill-down rows' list (page_list's view, of n rows) moved under the pair, down to the
 * window's foot at most, its rows past the list end fading (navigation.c list_fade); its rows'
 * height. */
int drill_list(void *view, int n) {
    void *lv = P(view, W_PARENT);
    int h = page_h(48), room = 320 - AR_LIST_Y, rows = n * h;
    if (rows > room) rows = room;
    widget_move_resize(lv, I(lv, W_X), AR_LIST_Y - ar_screen_y(P(lv, W_PARENT)), I(lv, W_W), rows);
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
    widget_set_prop_int(item, AR_PX, px ? px : TYPE(20));
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
        mono(canvas, widget_get_prop_str(item, AR_TITLE, ""), r.x, r.y, thumb, TYPE(15),
             on ? AR_MONO_ON : AR_MONO);
    } else if (!cover || !*cover) {
        ar_box(canvas, &r, AR_THUMB_R, flip_if(ipod_light(), AR_MONO));
    } else { /* the four corners outside the arcs, in the colour behind */
        ar_corners(canvas, &r, AR_THUMB_R, on ? ar_accent() : flip_if(ipod_light(), 0));
    }
    stock_canvas_set_fill_color(canvas, fill);
    /* the playing song's (song_rows.c): its bars on the right column, its length beside them */
    int col = 375 - geom_val(V_RIGHT_X), has = right && *right,
        room = playing_row(item, canvas, col - 1, has ? 50 : 0, on);
    if (has)
        ui_text(canvas, right, 0, TYPE(15), on ? 0xffffff : RGB_TERTIARY, 0, col - room, h / 2, 60,
                3);
    if (after && *after) { /* the title's ink and two spaces, then what is left of its column */
        char title[160];
        unsigned buf[96], n;
        int x = r.x + thumb + 10, w = col - room - (has ? 50 : 0) - x,
            px = widget_get_prop_int(item, AR_PX, TYPE(20));
        tk_snprintf(title, sizeof title, "%s  ", widget_get_prop_str(item, AR_TITLE, ""));
        n = ui_wide(title, buf, sizeof buf / sizeof *buf);
        const char *face = theme_face(buf, (int)n, &px);
        int tw = n ? ui_measure_w(canvas, face, px, buf, n) : 0;
        if (tw < w - 30)
            ui_text(canvas, after, 0, TYPE(15), on ? 0xffffff : RGB_TERTIARY, 0, x + tw, h / 2,
                    w - tw, 2);
    }
}

/* An album's track at index of view (a page_list's, its rows the theme's page_h(48), 32 px under
 * OLED): its number num (none when 0) right-aligned on TRACK_NUM_X in Inter Regular 15 Text
 * Tertiary, title in the theme's row face from TRACK_TITLE_X, resting on its ellipsis, and len
 * (none when empty) right-aligned on the right column as a cover row's. click gets ctx. */
void *track_row(void *view, int index, int num, const char *title, const char *len,
                int (*click)(void *, void *), int ctx) {
    int h = page_h(48), end = 375 - geom_val(V_RIGHT_X) - (len && *len ? 50 : 0);
    char n[12] = "";
    void *item = page_row_detail(view, ctx, title, 0, click);
    void *label = widget_get_child(item, 0);
    widget_move_resize(item, 0, index * h, 375, h);
    widget_move_resize(label, TRACK_TITLE_X - 2, 0, end - TRACK_TITLE_X + 2, h);
    widget_set_prop_int(label, "loop", 0); /* it scrolls on the selection alone */
    hscroll_label_set_only_focus(label, 1);
    hscroll_label_set_ellipses(label, 1);
    if (num > 0) tk_snprintf(n, sizeof n, "%d", num);
    widget_set_prop_str(item, AR_NUM, n);
    widget_set_prop_str(item, AR_RIGHT, len ? len : "");
    widget_set_prop_int(item, "_ar_row", ctx); /* addon.c addon_hold's */
    return item;
}

/* ringnav_paint's, after stock painted w (addon.c addon_paint, while its page shows one of these
 * layouts, and library_album.c on stock's album pages): the header, its pair, a drill-down row's
 * count, a track's number and length and a cover row's cover and text. */
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
    const char *num = widget_get_prop_str(w, AR_NUM, (const char *)0);
    if (num) { /* a track: its number, and its length on the right column */
        const char *len = widget_get_prop_str(w, AR_RIGHT, "");
        unsigned ink = ar_lit(w) ? 0xffffff : RGB_TERTIARY;
        if (*num) ui_text(canvas, num, 0, TYPE(15), ink, 0, TRACK_NUM_X, I(w, W_H) / 2, 40, 3);
        if (len && *len)
            ui_text(canvas, len, 0, TYPE(15), ink, 0, 375 - geom_val(V_RIGHT_X), I(w, W_H) / 2, 60,
                    3);
        return;
    }
    const char *count = widget_get_prop_str(w, AR_COUNT, (const char *)0);
    if (count && *count) {
        ui_text(canvas, count, 0, TYPE(15), ar_lit(w) ? 0xffffff : RGB_TERTIARY, 0,
                375 - AR_COUNT_DX, I(w, W_H) / 2, 60, 3);
        return;
    }
    if (widget_get_prop_int(w, AR_THUMB, 0)) cover_paint(w, canvas);
}
