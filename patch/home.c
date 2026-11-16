/* Home's Library and Streaming: their pages and the lists Home's Split shows; and its main list's
 * overflow, the rows past the seven it rests on, and its selection as Now Playing's row comes and
 * goes. */
#include "engine.h"
#include "navigation.h"

/* A Library row titled text; into: one that opens another list, with stock's own list_into
 * trailing image as the stock Local Music categories place it. Returns the row's list_item. */
static void *library_row(void *view, const char *icon, int (*click)(void *, void *), void *ctx,
                         const char *text, int into) {
    void *label = list_row_core(view, icon, click, ctx, into ? 210 : 260, into);
    widget_set_text_utf8(label, text);
    return P(P(label, W_PARENT), W_PARENT);
}

/* localmusic_page_init: stock's 11 category rows (0x535508), by index (get_localmusic_showinfo
 * 0x50bf30): Update Local Music, All Songs, Album, Artist, Genre, Hi-Res, My Fav, Frequent,
 * Recent, Recently Added, Playlist. */
enum {
    L_UPDATE,
    L_SONGS,
    L_ALBUMS,
    L_ARTISTS,
    L_GENRES,
    L_HIRES,
    L_FAV,
    L_FREQUENT,
    L_RECENT,
    L_ADDED,
    L_PLAYLISTS,
    L_STOCK
};

/* Every Library row, stock's and the added ones: its label takes the list titles' role. In a set
 * without Library icons (lib_art 0, as OLED's) it also drops its icon and starts its label at the
 * set's text inset on screen, its right edge kept. */
static void library_look(void *view) {
    const theme_t *T = theme();
    int bare = !geom_val(V_LIB_ART);
    if (!bare && !T->font[ROLE_TITLE] && !T->font_px[ROLE_TITLE]) return;
    for (unsigned i = 0, n = widget_count_children(view); i < n; ++i) {
        void *row = widget_get_child(view, i);
        void *button = widget_count_children(row) ? widget_get_child(row, 0) : (void *)0;
        for (unsigned j = 0, k = button ? widget_count_children(button) : 0; j < k; ++j) {
            void *c = widget_get_child(button, j);
            if (tk_strcmp(widget_get_type(c), "hscroll_label")) {
                if (bare) widget_set_visible(c, 0, 0);
                continue;
            }
            theme_font(c, ROLE_TITLE, FONT_ROW);
            if (bare) {
                int x = geom_val(V_TEXT_X) - I(button, W_X);
                widget_move_resize(c, x, I(c, W_Y), I(c, W_W) + I(c, W_X) - x, I(c, W_H));
            }
            break;
        }
    }
}

/* 1 when the Library leads with Search, in a set without Library icons (lib_art 0): the pull-down's
 * own stock search (0x5350d4, which ignores its arguments) shown, as Home's Split list hides the
 * pull. Home's pane counts Shuffle's row from it (coverflow.c coverflow_home_row). */
int library_search(void) { return !geom_val(V_LIB_ART); }

/* The Library: Search (library_search), one Shuffle row opening its menu, browsing, the user's own
 * lists, listening history, the card's other media, then upkeep, each row tagged with its letter
 * and put in the saved order (library_edit.c lr_default holds the default one). The added rows'
 * buttons have no name, so stock's row click (atoi of the name, 0x53510c) never sees them. */
int ringnav_localmusic(void *win, void *ctx) {
    int result = stock_localmusic_trampoline(win, ctx);
    void *view = win ? widget_lookup(win, "scroll_view_localmusic", 1) : (void *)0;
    if (!view || widget_count_children(view) != L_STOCK) return result;
    void *stock[L_STOCK], *rows[LR_ROWS] = { 0 };
    for (int i = 0; i < L_STOCK; i++) stock[i] = widget_get_child(view, i);
    if (library_search())
        lr_put(rows, 'S', library_row(view, (void *)0, library_search_open, 0, "Search", 1));
    lr_put(rows, 'h', library_row(view, "local_shuffle", shuffle_menu, 0, "Shuffle", 1));
    /* stock's rows: each one's letter, then its index */
    static const char stocks[] = { 'a', L_ARTISTS,   'l', L_ALBUMS, 's', L_SONGS,   'g', L_GENRES,
                                   'p', L_PLAYLISTS, 'n', L_ADDED,  'c', L_RECENT,  'i', L_HIRES,
                                   'u', L_UPDATE,    'f', L_FAV,    'q', L_FREQUENT };
    for (unsigned i = 0; i < sizeof stocks; i += 2)
        lr_put(rows, stocks[i], stock[(int)stocks[i + 1]]);
    /* Era: browsing by a tag, as Genre */
    lr_put(rows, 'e', library_row(view, "local_era", era_menu, 0, "Era", 1));
    /* My Fav is Ratings' Loved, its row kept hidden */
    lr_put(rows, 'r', library_row(view, "local_fav", ratings_menu, 0, "Ratings", 1));
    widget_set_visible(stock[L_FAV], 0, 0);
    lr_put(rows, 'm', library_row(view, "local_frequentplay", most_played, 0, "Most Played", 1));
    widget_set_visible(stock[L_FREQUENT], 0, 0); /* Most Played is its list, from every listen */
    lr_put(rows, 'L', library_row(view, "local_longsongs", long_songs, 0, "Long Songs", 1));
    /* Podcasts, Audiobooks, Photos, Books and Videos, each only with its folder; then iPod's Games.
     */
    static const char *const icons[] = { "local_podcasts", "local_audiobooks", "local_photos",
                                         "local_books", "local_videos" };
    char path[sizeof st.media_root];
    for (int k = 0; k < VIDEOS; k++)
        if (media_find(k + 1, path))
            lr_put(rows, "PAoBv"[k],
                   library_row(view, icons[k], media_click, (void *)(long)(k + 1), MEDIA[k], 1));
    lr_put(rows, 'G', library_row(view, "local_games", games_open, 0, "Games", 1));
    if (scrobble_ready())
        lr_put(rows, 'U',
               library_row(view, "local_scrobble", upload_scrobbles, 0, "Upload Scrobbles", 0));
    lr_apply(rows);
    library_look(view);
    return result;
}

/* Home's Library list (coverflow.c home_library), filled each time it opens, as the page is: the
 * same rows in the same order, whatever card, user or app adds. Stock's constructor takes a window
 * and finds the rows' view by name, so list, which holds one of that name, stands in. What else it
 * sets on the window goes again, list being none: the play gif's timer and its handlers. */
void library_rows(void *list) {
    ringnav_localmusic(list, (void *)0);
    ((int (*)(void *, void *))LOCALMUSIC_BG)(list, (void *)0);
    widget_off_by_ctx(list, list);
}

/* Streaming's AirPlay row: stock's own receiver (Shairport 1.1.1, AirPlay 1), whose switch is on
 * System settings' Network service page; that page, one press from Streaming. */
static int airplay_open(void *ctx, void *event) {
    navigator_to("systemset/netservice_page");
    return 0;
}

/* Whether w, or a widget in it, shows the image name. */
static int shows_image(void *w, const char *name) {
    if (!tk_strcmp(widget_get_prop_str(w, "image", ""), name)) return 1;
    for (unsigned i = 0, n = widget_count_children(w); i < n; ++i)
        if (shows_image(widget_get_child(w, i), name)) return 1;
    return 0;
}

/* A Streaming row tagged as service i (svc), hidden when STREAMHIDE holds it. */
static void svc_row(void *row, int i, char *hide) {
    prop(row, SVC_PROP, i + 1);
    if (svc_find(hide, i)) widget_set_visible(row, 0, 0);
}

/* OLED's Streaming marks (docs/oled-design.md): a service row's logo on its disc
 * gives way to the theme's 20 px monochrome mark (tools/art.py STREAM_MARKS), drawn with the row
 * by stream_mark_paint in Text Secondary, white on the selection, centred on x 34 as Folder view's
 * glyph, the row's words from x 52. A card app with a mark of its own (stream_apps, by its id)
 * takes it; any other card app with its own icon (addon.c, a file:// image) shows that icon in the
 * mark's 20 px box, as the image hook leaves it (OLED's icons.addon_mono greys it); one without
 * shows stream_app's mark. The settings layout hides every leading image on OLED's rows (set_child:
 * stream_row and set_row are both 32 px and set_icon is 0), so the row's own image never shows. */
static const char *const stream_logos[][2] = {
    { "list_tidal", "tidal" },   { "stream_spotify", "spotify" }, { "stream_app", "app" },
    { "stream_radio", "radio" }, { "stream_airplay", "airplay" }, { "stream_config", "config" }
};
/* card apps by their id (addon_id), each with its mark (oled_stream_<mark>_*) */
static const char *const stream_apps[][2] = { { "subsonic", "subsonic" } };
#define SVC_LOGOS (int)(sizeof stream_logos / sizeof *stream_logos)
#define SVC_APPS (int)(sizeof stream_apps / sizeof *stream_apps)
#define SVC_MARK_OWN (SVC_LOGOS + SVC_APPS + 1) /* a card app's own icon */
#define SVC_MARK_PROP "_stream_mark"
#define SVC_APP_PROP "_stream_app" /* a card app's row: its addon_* index + 1 */
#define SVC_MARK_PX 20
#define SVC_MARK_X 24
#define SVC_MARK_TEXT_X 52
/* the lists stream_marks tagged, the page's and Home's: ringnav_paint calls stream_mark_paint only
 * for their rows (a stale one is only compared) */
void *stream_mark_views[2] __attribute__((section(".scratch")));
static void stream_marks(void *view) {
    if (view != stream_mark_views[0] && view != stream_mark_views[1])
        stream_mark_views[1] = stream_mark_views[0], stream_mark_views[0] = view;
    for (unsigned i = 0, n = widget_count_children(view); i < n; ++i) {
        void *row = widget_get_child(view, i),
             *button = widget_count_children(row) ? widget_get_child(row, 0) : (void *)0;
        int app = widget_get_prop_int(row, SVC_APP_PROP, 0);
        for (unsigned j = 0, k = button ? widget_count_children(button) : 0; j < k; ++j) {
            void *c = widget_get_child(button, j);
            const char *image = widget_get_prop_str(c, "image", "");
            for (int m = 0; m < SVC_LOGOS; ++m)
                if (!tk_strcmp(image, stream_logos[m][0])) {
                    widget_set_visible(c, 0, 0);
                    prop(row, SVC_MARK_PROP, m + 1);
                }
            if (app && addon_is_icon(image)) {
                widget_set_visible(c, 0, 0);
                prop(row, SVC_MARK_PROP, SVC_MARK_OWN);
            }
        }
        const char *id = app ? addon_id(app - 1) : (const char *)0;
        for (int m = 0; id && m < SVC_APPS; ++m)
            if (!tk_strcmp(id, stream_apps[m][0])) prop(row, SVC_MARK_PROP, SVC_LOGOS + m + 1);
    }
}
/* A card app's own icon (its row's hidden file:// image) in the mark's box at x, y: scaled to fit,
 * centred. */
static void stream_own_icon(void *w, void *button, void *canvas, int x, int y) {
    for (unsigned j = 0, k = button ? widget_count_children(button) : 0; j < k; ++j) {
        const char *image = widget_get_prop_str(widget_get_child(button, j), "image", "");
        unsigned bitmap[64]; /* bitmap_t: width @0, height @4 */
        if (!addon_is_icon(image) || widget_load_image(w, image, bitmap)) continue;
        int bw = (int)bitmap[0], bh = (int)bitmap[1], big = bw > bh ? bw : bh;
        if (big <= 0) return;
        int dw = bw * SVC_MARK_PX / big, dh = bh * SVC_MARK_PX / big;
        rect_t src = { 0, 0, bw, bh },
               dst = { x + (SVC_MARK_PX - dw) / 2, y + (SVC_MARK_PX - dh) / 2, dw, dh };
        canvas_draw_image(canvas, bitmap, &src, &dst);
        return;
    }
}
void stream_mark_paint(void *w, void *canvas) {
    if (!w) return;
    int m = widget_get_prop_int(w, SVC_MARK_PROP, 0);
    if (m < 1 || m > SVC_MARK_OWN) return;
    void *button = widget_count_children(w) ? widget_get_child(w, 0) : (void *)0;
    int cx = I(canvas, CANVAS_X), /* the row's x on screen */
        x = SVC_MARK_X - cx, y = (I(w, W_H) - SVC_MARK_PX) / 2;
    if (m == SVC_MARK_OWN)
        stream_own_icon(w, button, canvas, x, y);
    else {
        /* selected: the row, or (a settings row's) its button */
        int sel = st.sel_accent && (painting_in(st.sel_accent) || P(st.sel_accent, W_PARENT) == w);
        char name[24];
        tk_snprintf(name, sizeof name, "stream_%s_%s",
                    m > SVC_LOGOS ? stream_apps[m - 1 - SVC_LOGOS][1] : stream_logos[m - 1][1],
                    sel            ? "w"
                    : ipod_light() ? "l"
                                   : "d");
        paint_art(w, canvas, theme_art(name), x, y);
    }
    /* the words after the mark: here, as the settings layout (set_row) and Home's (library_look)
     * place them later than stream_marks; a move repaints once */
    for (unsigned j = 0, k = button ? widget_count_children(button) : 0; j < k; ++j) {
        void *c = widget_get_child(button, j);
        int tx = SVC_MARK_TEXT_X - cx - I(button, W_X);
        if (I(c, W_X) != tx && !tk_strcmp(widget_get_type(c), "hscroll_label"))
            widget_move_resize(c, tx, I(c, W_Y), I(c, W_W) + I(c, W_X) - tx, I(c, W_H));
    }
}

/* stream_page_init: stock's rows but Baidu Netdisk (a Chinese cloud drive, signed in to from
 * Baidu's app: its row, the one with list_baidu's icon, removed), then Spotify (spotify.c), which
 * opens its browser, the card apps on the card (addon.c: Deezer, Soulseek, File Browser… as System
 * settings' Apps or the user put them there), Tidal Downloads, Internet Radio (radio.c), AirPlay
 * and Configuration. Their buttons have no name, so stock's row click never sees them. Smart
 * Wi-Fi's radio comes back first. */
int ringnav_stream(void *win, void *ctx) {
    smart_wake();
    int result = stock_stream_trampoline(win, ctx);
    void *view = win ? widget_lookup(win, "scroll_view_streamsset", 1) : (void *)0;
    for (int i = view ? (int)widget_count_children(view) : 0; i-- > 0;)
        if (shows_image(widget_get_child(view, (unsigned)i), "list_baidu"))
            widget_destroy(widget_get_child(view, (unsigned)i));
    if (view) {
        char hide[SVC_HIDE];
        int i = 1;
        st.svc_n = addon_scan(); /* at most APPS_MAX */
        svc_hidden(hide);
        for (unsigned c = 0, stock = widget_count_children(view); c < stock; ++c)
            if (shows_image(widget_get_child(view, c), "list_tidal"))
                svc_row(widget_get_child(view, c), 0, hide);
        svc_row(library_row(view, "stream_spotify", spot_open, 0, "Spotify", 1), i++, hide);
        for (int k = 0; k < st.svc_n; ++k) {
            void *row =
                library_row(view, addon_icon(k), addon_open, (void *)(long)k, addon_name(k), 1);
            prop(row, SVC_APP_PROP, k + 1); /* stream_marks: which app's */
            svc_row(row, i++, hide);
        }
        svc_row(library_row(view, "list_tidal", tidal_downloads, 0, "Tidal Downloads", 1), i++,
                hide);
        svc_row(library_row(view, "stream_radio", radio_open, 0, "Internet Radio", 1), i++, hide);
        svc_row(library_row(view, "stream_airplay", airplay_open, 0, "AirPlay", 1), i, hide);
        library_row(view, "stream_config", stream_config, 0, "Configuration", 1);
        stream_marks(view);
    }
    return result;
}

/* Home's Streaming list (coverflow.c home_streaming), filled each time it opens, as the page is. As
 * library_rows: stock's constructor takes a window and finds the rows' view by name, so list, which
 * holds one of that name, stands in; it finds no view_navbar there. What it sets on a window goes
 * again: its play gif's timer and its handlers. */
void stream_rows(void *list) {
    ringnav_stream(list, (void *)0);
    ((int (*)(void *, void *))STREAM_BG)(list, (void *)0);
    widget_off_by_ctx(list, list);
    library_look(widget_lookup(list, "scroll_view_streamsset", 1));
}

/* Home's overflow (docs/ipod.md#home): its main list holds more rows than it rests on, HOME_REST
 * of them from its top, as many as fit every set's window; the rest scroll, and scrolled to its end
 * the last row stops where the seventh's foot is. The list's box is those rows, which clips the
 * rest; under OLED's pages it runs on to the screen's bottom instead, and the rows there and, in
 * Split, the row above it (coverflow.c coverflow_home_strip) show through fades into the ground,
 * measured as docs/oled-design.md's Home rows say. */

/* The foot of view's last shown row: stock's list_view layout keeps a hidden row's slot
 * (Rockbox's, restacked last), so its content can end a row past it. */
static int shown_foot(void *view) {
    int foot = 0;
    for (unsigned i = 0, n = widget_count_children(view); i < n; ++i) {
        void *row = widget_get_child(view, i);
        if (widget_get_visible(row) && I(row, W_Y) + I(row, W_H) > foot)
            foot = I(row, W_Y) + I(row, W_H);
    }
    return foot;
}

/* For Home's main list's scroll view: the height its rows rest on, and in *foot their content's
 * end (shown_foot); 0 for any other view. navigation.c's sel_height and scroll_end take them. */
int home_rest(void *view, int *foot) {
    if (!view || view != coverflow_home_view() || !widget_count_children(view)) return 0;
    if (foot) *foot = shown_foot(view);
    return HOME_REST * I(widget_get_child(view, 0), W_H);
}

/* The scroll view's content as AWTK's touch scroll clamps it (to the content less the view): the
 * shown rows, and under them the box's rows past the rest, so a drag stops where the wheel does. A
 * relayout puts stock's back, so the list's paint sets it again (coverflow_home_clip). That
 * relayout (Home coming back on top) also clamps the scroll to stock's content, which moved a list
 * scrolled to its end down until its last row sat on the screen's foot. Found with stock's content
 * (the rows' foot, no padding), the scroll goes back to where the last paint saw the view end
 * (HOME_END, its foot in the content, plus 1): the paint moves and stretches the view by turns
 * (its top past the window's in Split's band), which its foot in the content does not see. */
#define HOME_END "_home_end"
void home_pad(void *view) {
    int foot, rest = home_rest(view, &foot);
    if (!rest) return;
    int h = I(view, W_H), content = foot + h - (rest < h ? rest : h), y = I(view, SCROLL_Y),
        kept = widget_get_prop_int(view, HOME_END, 0) - 1 - h;
    int relaid = I(view, VIEW_CONTENT_H) == foot; /* stock's own content: the rows' foot */
    I(view, VIEW_CONTENT_H) = content;
    if (relaid && kept > y && kept <= content - h)
        scroll_view_set_offset(view, I(view, SCROLL_X), y = kept);
    if (kept != y) prop(view, HOME_END, y + h + 1);
}

/* coverflow.c coverflow_home_layout, once list (Home's main list) is placed: its box to bottom (the
 * window's foot, in its parent), its scroll kept. */
void home_overflow(void *list, int bottom) {
    void *view = widget_count_children(list) ? widget_get_child(list, 0) : (void *)0;
    int foot, rest = home_rest(view, &foot), y = view ? I(view, SCROLL_Y) : 0;
    if (!rest) return;
    int h = bottom - I(list, W_Y);
    /* both by hand, as coverflow.c home_width sizes them: a resize lays out neither again */
    if (I(list, W_H) != h) widget_move_resize(list, I(list, W_X), I(list, W_Y), I(list, W_W), h);
    if (I(view, W_H) != h) widget_move_resize(view, I(view, W_X), I(view, W_Y), I(view, W_W), h);
    home_pad(view);
    if (y > foot - rest) y = foot - rest;
    scroll_view_set_offset(view, I(view, SCROLL_X), y < 0 ? 0 : y);
}

/* coverflow.c coverflow_home_layout, as Now Playing's row comes (d 1) or goes (-1) at the main
 * list's top, before the list is laid out again: the same row stays selected, so load sees no
 * change of count (which would start the selection over), and the rows in view stay, but for the
 * selected one's coming in sight as the row comes above it. On Now Playing as it goes, the row
 * after it, the Library. */
void home_np_keep(int d) {
    void *view = coverflow_home_view();
    int rest = home_rest(view, (int *)0);
    if (!rest) return;
    int sel = widget_get_prop_int(view, SEL, -1), count = widget_get_prop_int(view, COUNT, -1);
    if (count >= 0) prop(view, COUNT, count + d);
    if (sel > 0 || (sel == 0 && d > 0)) prop(view, SEL, sel += d);
    int row = I(widget_get_child(view, 0), W_H), y = I(view, SCROLL_Y);
    if (y > 0 || (d > 0 && (sel + 1) * row > rest)) y += d * row;
    scroll_view_set_offset(view, I(view, SCROLL_X), y < 0 ? 0 : y);
}

/* Shuffle Songs acts rather than opening a list, so has no chevron (navigation.c paint_chevrons):
 * as every other row draws one, its label takes the chevron's room, to the right column, and the
 * name fits Split's half. */
void home_in_place(void *list) {
    void *label = widget_lookup(list, "label_shuffle_songs", 1);
    if (!label) return;
    int w = I(P(label, W_PARENT), W_W) - geom_val(V_RIGHT_X) - I(label, W_X);
    if (w > I(label, W_W))
        widget_move_resize(label, I(label, W_X), I(label, W_Y), w, I(label, W_H));
}

/* smoothstep of num/den, clamped to 0..1, in 256ths. */
static int smooth(int num, int den) {
    int t = num <= 0 ? 0 : num >= den ? 256 : num * 256 / den;
    return t * t * (768 - 2 * t) >> 16;
}
/* One screen row of the ground at the alpha of 256 - k (k from smooth), over the screen's columns
 * x to x + w; canvas's origin is (ox, oy) on the screen. */
static void fade_row(void *canvas, int ox, int oy, unsigned ground, int k, int x, int y, int w) {
    if (k >= 256 || w <= 0) return;
    int a = 256 - k;
    stock_canvas_set_fill_color(canvas, ground | (unsigned)(a > 255 ? 255 : a) << 24);
    canvas_fill_rect(canvas, x - ox, y - oy, w, 1);
}
/* How far the glass's rounded edge dips at screen column x of its left corners. */
static int lip(int x) {
    int d = GLASS_R - x, r = GLASS_R;
    if (d <= 0) return 0;
    while (r * r > GLASS_R * GLASS_R - d * d) --r;
    return GLASS_R - r;
}

/* the corners' columns are faded this many at a time, at the middle one's dip */
#define FADE_COL 8
#define SPLIT_TOP_FADE 34  /* Split: the band from clear to the ground at the glass's top edge */
#define SPLIT_FOOT_FADE 56 /* and at its bottom edge */
#define FULL_TOP_CLEAR 14  /* Full: clear this far under the list's top, the ground 8 above it */
#define FULL_TOP_GROUND 8
#define FULL_FOOT_CLEAR 16 /* Full: clear this far above the rest's foot, the ground by y 316 */
#define FULL_FOOT_GROUND 316

/* OLED's fades over Home's main list, list's top at screen y list_y, its columns 0 to cols (Split's
 * left of the art): at the top once it has scrolled, at the foot while rows lie past the rest. In
 * Split they follow the glass's rounded edge, as far from it in a corner as on the straight part,
 * so a row dissolves the same way in both; in Full they run straight across. The selected row is
 * never faded. Each row of the band one fill per FADE_COL columns in a corner, inside the canvas's
 * clip. */
void home_fades(void *canvas, void *list, int list_y, int cols, int split) {
    void *view = list && widget_count_children(list) ? widget_get_child(list, 0) : (void *)0;
    int foot, rest = home_rest(view, &foot);
    if (!rest || !P(canvas, CANVAS_LCD)) return;
    int ox = I(canvas, CANVAS_X), oy = I(canvas, CANVAS_Y); /* its origin on the screen */
    int off = I(view, SCROLL_Y), above = off > 0, below = foot - off > rest;
    if (!above && !below) return;
    /* the selected row, on the screen: SEL counts the rows shown */
    int sel = widget_get_prop_int(view, SEL, -1), s0 = 1, s1 = 0;
    for (unsigned i = 0, n = widget_count_children(view); i < n && sel >= 0; ++i) {
        void *row = widget_get_child(view, i);
        if (widget_get_visible(row) && !sel--)
            s0 = list_y + I(row, W_Y) - off, s1 = s0 + I(row, W_H);
    }
    int clip[4]; /* rect_t, on the screen */
    canvas_get_clip_rect(canvas, clip);
    int y0 = clip[1] > 0 ? clip[1] : 0, y1 = clip[1] + clip[3] < 320 ? clip[1] + clip[3] : 320;
    unsigned fill = (unsigned)I(P(canvas, CANVAS_LCD), LCD_FILL_COLOR),
             ground = ui_ink(RGBA(0)) & 0xffffffu;
    int dip[GLASS_R / FADE_COL + 1]; /* each run of columns' lip, then the straight part's 0 */
    for (int i = 0; i <= GLASS_R / FADE_COL; ++i) dip[i] = lip(i * FADE_COL + FADE_COL / 2);
    for (int y = y0; y < y1; ++y) {
        /* not the selected row; in Full, only its window's rows */
        if ((y >= s0 && y < s1) || (!split && y < list_y)) continue;
        if (!split) {
            int clear = list_y + FULL_TOP_CLEAR, end = list_y + rest - FULL_FOOT_CLEAR;
            int k = above ? 256 - smooth(clear - y, FULL_TOP_CLEAR + FULL_TOP_GROUND) : 256;
            if (below) {
                int b = 256 - smooth(y - end, FULL_FOOT_GROUND - end);
                if (b < k) k = b;
            }
            fade_row(canvas, ox, oy, ground, k, 0, y, cols);
            continue;
        }
        for (int x = 0; x < cols; x += FADE_COL) {
            int d = dip[x < GLASS_R ? x / FADE_COL : GLASS_R / FADE_COL],
                w = x < GLASS_R ? FADE_COL : cols - x;
            int k = above ? smooth(y - d, SPLIT_TOP_FADE) : 256;
            if (below) {
                int b = smooth(320 - y - d, SPLIT_FOOT_FADE);
                if (b < k) k = b;
            }
            fade_row(canvas, ox, oy, ground, k, x, y, w < cols - x ? w : cols - x);
            if (x >= GLASS_R) break;
        }
    }
    stock_canvas_set_fill_color(canvas, fill);
}
