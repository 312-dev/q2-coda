/* A list page's search bar (docs/oled-design.md#search-bar, approved as mock_search_filter v3 on
 * 2026-11-19; docs/internals.md#search-bar): on every music list page (SB_PAGES: Songs, Artists,
 * Albums, Genres, Playlists, Years and the rest, and inside a playlist, an artist, an album) a
 * search bar stands one wheel step above the first row. It is out of sight as the page opens: the
 * first row lit, nothing above it and no fade hinting at it. A turn up from the first row brings it
 * in, lit: the list moves down a row (SB_BAND) and the pill (search_strip.c search_pill_paint)
 * takes the band, "Search <the page's title>" in it, the rows' selection hidden as after a touch. A
 * turn down puts the list back with its first row lit; up does nothing more. Centre opens the
 * letter strip over the page's own rows (search_strip.c search_strip_scoped), which filters them as
 * it is typed; going back from it finds the bar still lit. One page's bar at a time; the page going
 * forgets it. */
#include "navigation.h"

#define SB_BAND 40 /* the list moves down this far while the bar shows; the pill is centred in it  \
                    */

static struct {
    void *page, *surface, *moved; /* the lit bar's page, its rows' surface, what moved down */
    void *also;                   /* ... and a row above the rows that moves with them (Songs' Shuffle) */
    int y, also_y;                /* moved's and also's own y at rest */
    int band;                     /* the band's top, in the page's coordinates */
} sb __attribute__((section(".scratch")));

/* The Library's music lists: stock's, then the payload's own (contexts.inc). */
static int sb_page(void *top) {
    static const char *const pages[] = { "allmusic_page",   "localclass_page",    "album_page",
                                         "artistinfo_page", "albuminfo_page",     "artistrows_page",
                                         "playlist_page",   "playlistsongs_page", "era_page",
                                         "era_albums_page", "ratings_page",       "rated_page",
                                         "mostplayed_page", "longsongs_page" };
    const char *name = top ? widget_get_prop_str(top, "name", "") : "";
    for (unsigned i = 0; name && i < sizeof pages / sizeof *pages; ++i)
        if (!tk_strcmp(name, pages[i])) return 1;
    return 0;
}

static int sb_gone(void *page, void *event) {
    if (page == sb.page) memset(&sb, 0, sizeof sb);
    return 0;
}

/* The bar out of sight, the list back in its place (a page's own search opening a row, the wheel
 * turned down). */
void search_bar_off(void) {
    if (!sb.page) return;
    if (sb.moved) widget_move(sb.moved, I(sb.moved, W_X), sb.y);
    if (sb.also) widget_move(sb.also, I(sb.also, W_X), sb.also_y), st.shuffle_on = sb.also;
    st.touch_mode = 0;
    widget_invalidate_force(sb.page, (void *)0);
    sb.page = sb.surface = sb.moved = sb.also = (void *)0;
}

/* navigation's wheel (input.c), a turn up that finds the first row of surface w selected on top, or
 * Songs' Shuffle row also lit over it (input.c shuffle_key, the row moving with the list): 1 when it
 * brought the bar in, above them both. */
int search_bar_up_with(void *top, void *w, void *also) {
    if (!theme()->entry_ipod || !w || !sb_page(top) || sb.page) return 0;
    void *moved = P(w, W_PARENT);
    if (!moved || moved == top) return 0;
    if (!widget_get_prop_int(top, "_sb_bound", 0)) {
        widget_on(top, EVT_DESTROY, sb_gone, top);
        widget_set_prop_int(top, "_sb_bound", 1);
    }
    sb.page = top, sb.surface = w, sb.moved = moved, sb.y = I(moved, W_Y);
    int y = 0;
    for (void *p = moved; p && p != top; p = P(p, W_PARENT)) y += I(p, W_Y);
    sb.band = y;
    widget_move(moved, I(moved, W_X), sb.y + SB_BAND);
    if ((sb.also = also)) {
        sb.also_y = I(also, W_Y);
        int ay = 0;
        for (void *p = also; p && p != top; p = P(p, W_PARENT)) ay += I(p, W_Y);
        if (ay < sb.band) sb.band = ay; /* the band above the row too */
        widget_move(also, I(also, W_X), sb.also_y + SB_BAND);
        st.shuffle_on = (void *)0; /* its lit state hidden while the bar has the wheel */
    }
    st.touch_mode = 1; /* the rows' selection hidden: the bar has the wheel */
    widget_invalidate_force(top, (void *)0);
    return 1;
}

int search_bar_up(void *top, void *w) { return search_bar_up_with(top, w, (void *)0); }

/* ringnav, before the list's own keys: the lit bar's. 1 when it took key. */
int search_bar_key(void *top, unsigned key) {
    if (!sb.page || top != sb.page) return 0;
    if (key == KEY_NEXT) {
        search_bar_off();
        return 1;
    }
    if (key == KEY_PREV) return 1; /* nothing above it */
    if (key != KEY_CENTER) return 0;
    char title[48] = "";
    if (st.bar_title) widget_get_text_utf8(st.bar_title, title, sizeof title);
    era_rows_finish(sb.page); /* a decade's rows made a pass at a time: the search reads them all */
    search_strip_scoped(sb.page, sb.surface, title);
    return 1;
}

/* ringnav_paint, after w's children: the lit bar in its band on its page. */
void search_bar_paint(void *w, void *canvas) {
    if (!sb.page || w != sb.page || !P(canvas, CANVAS_LCD)) return;
    char title[48] = "", words[64];
    if (st.bar_title) widget_get_text_utf8(st.bar_title, title, sizeof title);
    tk_snprintf(words, sizeof words, "Search %s", title);
    unsigned fill = (unsigned)I(P(canvas, CANVAS_LCD), LCD_FILL_COLOR), raw = ui_ink_raw(1);
    fill_box(canvas, &(rect_t){ 0, sb.band, 375, SB_BAND }, ui_ink(RGBA(0)), 0);
    search_pill_paint(canvas, sb.band + SB_BAND / 2, 1, words);
    ui_ink_raw(raw);
    stock_canvas_set_fill_color(canvas, fill);
}
