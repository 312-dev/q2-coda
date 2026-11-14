/* A theme's one-line song rows (SONGS_INLINE, docs/oled-design.md#song-rows): a song plays in
 * place, so its row has no chevron, and what stands beside the title depends on the list. A mixed
 * list (Songs, a genre's songs: anything stock opens on Songs' page) shows the artist after the
 * title; a list reached through an artist (artistinfo_page, their songs) shows none; an album
 * (albuminfo_page) shows each track's number in a column before the title and its length on the
 * right column. Song Artist and Song Length, the theme's own options, change that.
 *
 * navigation.c's background hook calls song_row for each row of such a list before the rows
 * paint: the title's box is fitted to what stands beside it, and the rest is drawn here, under
 * the rows' own text. The artist is the text stock bound to the row's second line, which a
 * one-line row hides; the number and length come from the row's record in p_deque_showlist, used
 * only when its title is the row's. */
#include "navigation.h"

#define SONG_ARTIST_MIN 40 /* an inline artist with less room than this is dropped */
#define SONG_NUM_W 14      /* an album's track number, right-aligned this wide after the inset */
#define SONG_NUM_TITLE 50  /* ... and the title's first column on screen */
#define SONG_COL_GAP 12    /* a title's ink and the text on the right column */
#define SONG_SMALL_DY 2 /* the small text's box below the row's, so its baseline is the title's */
#define HSCROLL_MARGIN                                                                             \
    2 /* hscroll_label's own, each side of its text (rows' boxes start on the inset) */

/* The list w's window shows: SONGS_MIXED, SONGS_ARTIST, SONGS_ALBUM, or 0, not a song list. A list
 * the payload builds itself (Most Played, Long Songs, Cover Flow's tracks) has its own rows. */
int song_list(void *w) {
    void *win = window_of(w);
    const char *name = win ? widget_get_prop_str(win, "name", "") : "";
    return !tk_strcmp(name, "allmusic_page")     ? SONGS_MIXED
           : !tk_strcmp(name, "artistinfo_page") ? SONGS_ARTIST
           : !tk_strcmp(name, "albuminfo_page")  ? SONGS_ALBUM
                                                 : 0;
}

/* The title's width, a title whose text is title px wide in room px, with an artist artist px
 * wide gap px after it (SONG_INLINE) or on the right column (SONG_RIGHT): the title keeps what it
 * needs and the artist takes what is left, dropped under SONG_ARTIST_MIN; on the right column the
 * artist takes at most half the room. *shown: the artist's width, 0 when dropped. */
int song_fit(int place, int room, int title, int gap, int artist, int *shown) {
    int a = 0;
    if (place == SONG_INLINE) {
        if (title > room) title = room;
        a = room - title - gap;
        if (a > artist) a = artist;
    } else if (place == SONG_RIGHT) {
        a = room - gap - (title < room / 2 ? title : room / 2);
        if (a > artist) a = artist;
        if (a >= SONG_ARTIST_MIN && title > room - a - gap) title = room - a - gap;
    }
    if (a < SONG_ARTIST_MIN || artist <= 0) a = 0;
    if (!a && title > room) title = room;
    *shown = a;
    return title;
}

/* The row's title (its first hscroll_label in pre-order) and the next one, stock's second line,
 * however deep stock nests it (view_artist, as row_meta finds it). */
static void song_labels(void *w, void **title, void **meta, int depth) {
    for (unsigned i = 0, n = widget_count_children(w); i < n && !*meta && depth < 4; ++i) {
        void *c = widget_get_child(w, i);
        if (tk_strcmp(widget_get_type(c), "hscroll_label"))
            song_labels(c, title, meta, depth + 1);
        else if (*title)
            *meta = c;
        else
            *title = c;
    }
}

/* The song record row id shows: id, or the one before it where the list holds a row of its own
 * first, a song whose title is the label's. */
static void *song_record(int id, void *title) {
    char text[256];
    void *list = P(p_deque_showlist, 0);
    if (!list || widget_get_text_utf8(title, text, sizeof text)) return (void *)0;
    for (int k = id; k >= 0 && k >= id - 1; --k) {
        if ((unsigned)k >= deque_size(list)) continue;
        void *r = deque_at(list, (unsigned)k);
        const char *t = P(r, REC_TITLE);
        if (I(r, REC_TYPE) == 8 && t && !strcmp(t, text)) return r;
    }
    return (void *)0;
}

static int measure(void *canvas, const unsigned *s, unsigned n, int px) {
    if (!n) return 0;
    canvas_set_font(canvas, theme_face(s, (int)n, &px), (unsigned)px);
    return (int)(canvas_measure_text(canvas, s, n) + 0.5f);
}

/* s in the small face, its box x to x + w, the text at its left (right: at its right), ending in
 * an ellipsis where it is wider. */
static void small_text(void *canvas, const unsigned *s, int x, int w, int right, const rect_t *r) {
    unsigned buf[128], n = wide_len(s);
    int px = theme()->font_px[ROLE_META];
    if (n > sizeof buf / sizeof *buf - 1) n = sizeof buf / sizeof *buf - 1;
    memcpy(buf, s, n * sizeof *buf);
    buf[n] = 0;
    while (n > 1 && measure(canvas, buf, n, px) > w) buf[--n - 1] = 0x2026; /* … */
    int ink = measure(canvas, buf, n, px);
    rect_t box = { right ? x + w - ink : x, r->y + SONG_SMALL_DY, ink + 1, r->h };
    int align_v = I(canvas, CANVAS_ALIGN_V), align_h = I(canvas, CANVAS_ALIGN_H);
    I(canvas, CANVAS_ALIGN_V) = 1; /* middle */
    I(canvas, CANVAS_ALIGN_H) = 2; /* left */
    canvas_draw_text_in_rect(canvas, buf, n, &box);
    I(canvas, CANVAS_ALIGN_V) = align_v;
    I(canvas, CANVAS_ALIGN_H) = align_h;
}

void song_row(void *w, void *canvas, void *row, int id, rect_t r, int selected) {
    int ctx = song_list(w);
    void *title = (void *)0, *meta = (void *)0;
    song_labels(row, &title, &meta, 0);
    if (!ctx || !title || !widget_get_visible(title)) return;
    void *rec = song_record(id, title);
    if (ctx == SONGS_ALBUM && !rec) return; /* not a track: Play All keeps its place */
    const theme_t *T = theme();
    const unsigned *name = widget_get_text(title), *artist = (void *)0;
    if (ctx == SONGS_MIXED && meta && widget_get_text(meta) && *widget_get_text(meta))
        artist = widget_get_text(meta);
    int how = song_artist(), lengths = song_length();
    char len[16] = "";
    unsigned wlen[16], nlen = 0;
    if (rec &&
        (lengths == SONG_LENGTH_ALL || (lengths == SONG_LENGTH_ALBUMS && ctx == SONGS_ALBUM)))
        duration_text(len, sizeof len, rec);
    while (len[nlen] && nlen < sizeof wlen / sizeof *wlen - 1)
        wlen[nlen] = (unsigned char)len[nlen], ++nlen;
    wlen[nlen] = 0;
    /* where the artist stands: a length takes the right column, so Right is inline beside it */
    int place = !artist || how == SONG_ARTIST_OFF || (how == SONG_ARTIST_SELECTED && !selected) ? 0
                : how == SONG_ARTIST_INLINE || nlen ? SONG_INLINE
                                                    : SONG_RIGHT;
    int tpx = T->font_px[ROLE_TITLE], spx = T->font_px[ROLE_META];
    int left = ctx == SONGS_ALBUM ? SONG_NUM_TITLE : geom_val(V_TEXT_X),
        column = I(w, W_W) - 1 - geom_val(V_RIGHT_X) - HSCROLL_MARGIN,
        len_w = measure(canvas, wlen, nlen, spx),
        end = nlen ? column - len_w - SONG_COL_GAP : column;
    static const unsigned spaces[] = { ' ', ' ', 0 };
    int gap = measure(canvas, spaces, 2, tpx), shown = 0;
    int ink =
        song_fit(place, end - left - HSCROLL_MARGIN, measure(canvas, name, wide_len(name), tpx),
                 gap, artist ? measure(canvas, artist, wide_len(artist), spx) : 0, &shown);
    /* the title's box: its text from left, as wide as its ink; a box changed under a paint
     * repaints once */
    int x = r.x;
    for (void *p = P(title, W_PARENT); p && p != row; p = P(p, W_PARENT)) x += I(p, W_X);
    int bx = left - x, bw = ink + 2 * HSCROLL_MARGIN;
    if (I(title, W_X) != bx || I(title, W_W) != bw)
        widget_move_resize(title, bx, I(title, W_Y), bw, I(title, W_H));
    unsigned text = (unsigned)I(P(canvas, CANVAS_LCD), LCD_TEXT_COLOR), raw = st.raw_ink;
    /* the scheme's muted, which Light Mode's flip turns for paper; on the selection its text,
     * final in each half */
    unsigned color =
        selected ? (scheme()->sel_text ? scheme()->sel_text : 0xffffff) : scheme()->muted;
    st.raw_ink = selected;
    canvas_set_text_color(canvas, RGBA(color));
    if (ctx == SONGS_ALBUM && I(rec, REC_TRACK) > 0) {
        unsigned num[4], n = put_num(num, (unsigned)I(rec, REC_TRACK) % 1000);
        num[n] = 0;
        /* its column's right edge; a number wider than the column runs left into the inset */
        small_text(canvas, num, geom_val(V_TEXT_X) + HSCROLL_MARGIN + SONG_NUM_W - 40, 40, 1, &r);
    }
    if (shown && place == SONG_INLINE)
        small_text(canvas, artist, left + HSCROLL_MARGIN + ink + gap, shown, 0, &r);
    if (shown && place == SONG_RIGHT) small_text(canvas, artist, end - shown, shown, 1, &r);
    if (nlen) small_text(canvas, wlen, column - len_w, len_w, 1, &r);
    stock_canvas_set_text_color(canvas, text);
    st.raw_ink = raw;
}
