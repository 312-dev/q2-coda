/* A theme's one-line song rows (SONGS_INLINE, docs/oled-design.md#song-rows): a song plays in
 * place, so its row has no chevron, and what stands beside the title depends on the list. A mixed
 * list (Songs, a genre's songs: anything stock opens on Songs' page) shows the artist after the
 * title; a list reached through an artist (artistinfo_page, their songs) shows none (its page
 * drawn as the design's, library_artist.c, has no song list); an album
 * (albuminfo_page, playerjumpinfo_page, and an album's tracks in Cover Flow) shows each track's
 * number in a column before the title and its length on the right column; Folder view
 * (folder_page) reads as an album too (folder_row). Song Artist and Song Length, the theme's own
 * options, change that.
 *
 * navigation.c's background hook calls song_row for each row of such a list before the rows
 * paint: the title's box is fitted to what stands beside it, and the rest is drawn here, under
 * the rows' own text. A greyed row's offline mark (catalogue.c) takes the right column's place:
 * the length, the artist and the title end SONG_COL_GAP short of it. The artist is the text stock
 * bound to the row's second line, which a one-line row hides; the number and length come from the
 * row's record in p_deque_showlist, used only when its title is the row's. */
#include "navigation.h"

#define SONG_ARTIST_MIN 40 /* an inline artist with less room than this is dropped */
#define SONG_NUM_W 17      /* an album's track number, right-aligned this wide after the inset */
#define SONG_NUM_TITLE 50  /* ... and the title's first column on screen */
#define SONG_SMALL_DY 2 /* the small text's box below the row's, so its baseline is the title's */

extern void *coverflow_tracks(void *page);

/* The list w's window shows: SONGS_MIXED, SONGS_ARTIST, SONGS_ALBUM, SONGS_FOLDER, or 0, not a
 * song list. Cover Flow's album tracks (coverflow_tracks) read as an album; the other lists the
 * payload builds itself (Most Played, Long Songs) have their own rows. */
int song_list(void *w) {
    void *win = window_of(w);
    const char *name = win ? widget_get_prop_str(win, "name", "") : "";
    return !tk_strcmp(name, "allmusic_page")     ? SONGS_MIXED
           : !tk_strcmp(name, "artistinfo_page") ? (artist_local_on(win) ? 0 : SONGS_ARTIST)
           : !tk_strcmp(name, "albuminfo_page")  ? SONGS_ALBUM
           /* Look up Album's and Go to Album's (navigation.c qm_goto) */
           : !tk_strcmp(name, "playerjumpinfo_page") ? SONGS_ALBUM
           : !tk_strcmp(name, "folder_page")         ? SONGS_FOLDER
           : coverflow_tracks(win)                   ? SONGS_ALBUM
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

/* Folder view as an album (SONGS_FOLDER, docs/oled-design.md#song-rows): a song file's row reads
 * as a track, its number before the title and its length on the right column; a folder's (and any
 * other file's) has the theme's folder glyph before its name, and stock's chevron where stock gives
 * one. A row's title is the library's tag, found by the file's path; a file the library doesn't
 * know shows its file name without the extension, a leading track number or the "Artist - " every
 * such name in the folder starts with. */
#define FOLDER_ICON_X 24    /* the folder glyph's left, so its 20 px centre on x 34 */
#define FOLDER_ICON 20      /* its size (tools/art.py ROW_ICON_PX) */
#define FOLDER_TITLE 52     /* a folder's name's first column on screen */
#define FOLDER_CHEV_ROOM 20 /* a folder's chevron on the right column, and its gap */

typedef struct {
    char *title; /* what the row says: the tag's title or the trimmed file name; 0, not a song */
    int track;   /* the tag's track number, else the file name's; 0 for none */
    int own;     /* title is the file name's */
} ftrack_t;
/* The folder the rows were worked out for: its path's hash and showlist size, and the records */
static struct {
    unsigned key, n;
    void *first;
    ftrack_t *t;
} fold __attribute__((section(".scratch")));

/* A file name's end, before its extension (a dot after the last '/', not leading). */
static unsigned stem_len(const char *s) {
    const char *dot = strrchr(s, '.');
    unsigned n = strlen(s);
    return dot && dot > s ? (unsigned)(dot - s) : n;
}
/* s past a leading track number ("01. ", "1 - ", "03 "), *num that number; s itself where none
 * stands, or nothing would be left. */
static const char *strip_number(const char *s, unsigned n, int *num) {
    unsigned i = 0, v = 0;
    while (i < n && i < 3 && s[i] >= '0' && s[i] <= '9') v = v * 10 + (unsigned)(s[i++] - '0');
    unsigned j = i;
    if (j < n && (s[j] == '.' || s[j] == ')')) ++j;
    while (j < n && (s[j] == ' ' || s[j] == '-' || s[j] == '_')) ++j;
    if (!i || j == i || j >= n) return s;
    *num = (int)v;
    return s + j;
}

static int folder_tag(void *ctx, int cols, char **v, char **names) {
    unsigned at = (unsigned)strlen((const char *)g_folder_path) + 1;
    void *list = P(p_deque_showlist, 0);
    (void)ctx, (void)names;
    if (cols != 3 || !v[0] || strlen(v[0]) <= at || strrchr(v[0], '/') != v[0] + at - 1) return 0;
    for (unsigned j = 0; j < fold.n; ++j) {
        void *r = deque_at(list, j);
        const char *path = P(r, REC_PATH);
        if (I(r, REC_TYPE) != 8 || !path || strcmp(path, v[0])) continue;
        if (v[1] && *v[1] && !fold.t[j].title) fold.t[j].title = strdup(v[1]);
        if (v[2] && atoi(v[2]) > 0) fold.t[j].track = atoi(v[2]);
        break;
    }
    return 0;
}

/* The showlist's rows as folder_row shows them, worked out once per folder listing: the library's
 * tags for the folder's own files (one query, on the UI thread), then the file names of the rest.
 * ponytail: the query scans the songs under the folder by fileurl's order; a large card root with
 * few files of its own pays for every song under it. */
static const ftrack_t *folder_tracks(void) {
    void *list = P(p_deque_showlist, 0);
    const char *dir = (const char *)g_folder_path;
    unsigned n = list ? deque_size(list) : 0, len = strlen(dir);
    unsigned key = hash_bytes(FNV_SEED, (const unsigned char *)dir, len);
    void *first = n ? deque_at(list, 0) : (void *)0;
    if (fold.t && fold.key == key && fold.n == n && fold.first == first) return fold.t;
    for (unsigned j = 0; fold.t && j < fold.n; ++j) free(fold.t[j].title);
    free(fold.t);
    fold.t = n ? calloc(n, sizeof *fold.t) : (void *)0;
    fold.key = key, fold.n = fold.t ? n : 0, fold.first = first;
    if (!fold.t) return (void *)0;
    /* the folder's songs: fileurl from dir/ up to (not including) dir0, '0' being '/' + 1 */
    char sql[4 * 1024 + 128], *o = sql; /* the path twice, each ' doubled */
    o += tk_snprintf(o, 64, "select fileurl,name,track from songtable where fileurl>'");
    for (int k = 0; k < 2; ++k) {
        for (const char *c = dir; *c; ++c) {
            if (*c == '\'') *o++ = '\'';
            *o++ = *c;
        }
        o += tk_snprintf(o, 64, k ? "0'" : "/' and fileurl<'");
    }
    era_sql(sql, folder_tag, 0);
    /* the rest by file name: the number off, then the "Artist - " every one of them starts with */
    const char *base = (void *)0;
    unsigned cut = 0;
    for (unsigned j = 0; j < n; ++j) {
        void *r = deque_at(list, j);
        const char *name = P(r, REC_NAME);
        if (I(r, REC_TYPE) != 8 || !name || fold.t[j].title) continue;
        int num = 0;
        unsigned sl = stem_len(name);
        const char *t = strip_number(name, sl, &num);
        sl -= (unsigned)(t - name);
        if (!fold.t[j].track) fold.t[j].track = num;
        if (!(fold.t[j].title = calloc(1, sl + 1))) continue;
        memcpy(fold.t[j].title, t, sl);
        fold.t[j].own = 1;
        t = fold.t[j].title;
        if (!base) { /* the first name's longest "... - " with text after it */
            base = t;
            for (unsigned k = 0; k + 3 < sl; ++k)
                if (!strncmp(t + k, " - ", 3)) cut = k + 3;
        }
        /* ... cut back to the longest one this name shares */
        while (cut && (sl <= cut || strncmp(t, base, cut))) {
            for (--cut; cut >= 3 && strncmp(base + cut - 3, " - ", 3); --cut);
            if (cut < 3) cut = 0;
        }
    }
    for (unsigned j = 0; cut && j < n; ++j)
        if (fold.t[j].own)
            memmove(fold.t[j].title, fold.t[j].title + cut, strlen(fold.t[j].title + cut) + 1);
    return fold.t;
}

/* Folder view's row id (SONGS_FOLDER): as folder_tracks says, its title's box fitted to it */
static void folder_row(void *w, void *canvas, void *row, void *title, int id, rect_t r,
                       int selected) {
    void *list = P(p_deque_showlist, 0);
    const ftrack_t *ft = folder_tracks();
    if (!ft || (unsigned)id >= fold.n) return;
    void *rec = deque_at(list, (unsigned)id);
    const char *text = I(rec, REC_TYPE) == 8 ? ft[id].title : (const char *)0;
    char now[256];
    /* a song's title as worked out, once: stock binds the file name */
    if (text && !widget_get_text_utf8(title, now, sizeof now) && strcmp(now, text))
        widget_set_text_utf8(title, text);
    const theme_t *T = theme();
    int tpx = T->font_px[ROLE_TITLE], spx = T->font_px[ROLE_META];
    char len[16] = "";
    unsigned wlen[16], nlen = 0;
    if (text && song_length() != SONG_LENGTH_OFF) duration_text(len, sizeof len, rec);
    while (len[nlen] && nlen < sizeof wlen / sizeof *wlen - 1)
        wlen[nlen] = (unsigned char)len[nlen], ++nlen;
    wlen[nlen] = 0;
    /* a video (the Videos folder's, playback.c playback_video) has no track number: its title
     * on the text column, as any list's row (audit2 C3) */
    int left = !text                 ? FOLDER_TITLE
               : playback_video(rec) ? geom_val(V_TEXT_X)
                                     : SONG_NUM_TITLE,
        column = I(w, W_W) - 1 - geom_val(V_RIGHT_X) - HSCROLL_MARGIN,
        len_w = measure(canvas, wlen, nlen, spx),
        end = nlen   ? column - len_w - SONG_COL_GAP
              : text ? column
                     : column - FOLDER_CHEV_ROOM;
    const unsigned *name = widget_get_text(title);
    int ink = name ? measure(canvas, name, wide_len(name), tpx) : 0,
        room = end - left - HSCROLL_MARGIN;
    if (ink > room) ink = room;
    int x = r.x;
    for (void *p = P(title, W_PARENT); p && p != row; p = P(p, W_PARENT)) x += I(p, W_X);
    int bx = left - x, bw = ink + 2 * HSCROLL_MARGIN;
    if (I(title, W_X) != bx || I(title, W_W) != bw)
        widget_move_resize(title, bx, I(title, W_Y), bw, I(title, W_H));
    if (!text) {
        paint_art(w, canvas,
                  theme_art(selected       ? "folder_w"
                            : ipod_light() ? "folder_l"
                                           : "folder_d"),
                  FOLDER_ICON_X, r.y + (r.h - FOLDER_ICON) / 2);
        return;
    }
    unsigned ink0 = (unsigned)I(P(canvas, CANVAS_LCD), LCD_TEXT_COLOR), raw = st.raw_ink;
    unsigned color =
        selected ? (scheme()->sel_text ? scheme()->sel_text : 0xffffff) : scheme()->muted;
    st.raw_ink = selected;
    canvas_set_text_color(canvas, RGBA(color));
    if (ft[id].track > 0 && left == SONG_NUM_TITLE) {
        unsigned num[4], n = put_num(num, (unsigned)ft[id].track % 1000);
        num[n] = 0;
        small_text(canvas, num, geom_val(V_TEXT_X) + HSCROLL_MARGIN + SONG_NUM_W - 40, 40, 1, &r);
    }
    if (nlen) small_text(canvas, wlen, column - len_w, len_w, 1, &r);
    stock_canvas_set_text_color(canvas, ink0);
    st.raw_ink = raw;
}

void song_row(void *w, void *canvas, void *row, int id, rect_t r, int selected, int edge) {
    int ctx = song_list(w);
    void *title = (void *)0, *meta = (void *)0;
    song_labels(row, &title, &meta, 0);
    if (!ctx || !title || !widget_get_visible(title)) return;
    if (ctx == SONGS_FOLDER) {
        folder_row(w, canvas, row, title, id, r, selected);
        return;
    }
    /* Cover Flow's rows are its tracks one for one, by index */
    void *tracks = coverflow_tracks(window_of(w));
    void *rec = !tracks                             ? song_record(id, title)
                : (unsigned)id < deque_size(tracks) ? deque_at(tracks, (unsigned)id)
                                                    : (void *)0;
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
    /* the right column (in while the A-Z index shows, az_index.c), or SONG_COL_GAP short of a
     * greyed row's offline mark: the length, a right-hand artist and the title's room all end
     * there */
    int left = ctx == SONGS_ALBUM ? SONG_NUM_TITLE : geom_val(V_TEXT_X),
        column = I(w, W_W) - 1 - az_right(w) - HSCROLL_MARGIN,
        len_w = measure(canvas, wlen, nlen, spx);
    if (edge && edge - SONG_COL_GAP < column) column = edge - SONG_COL_GAP;
    int end = nlen ? column - len_w - SONG_COL_GAP : column;
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
    /* the box is this row's to size: catalogue_fit's width kept from an earlier fit is not */
    if (widget_get_prop_int(title, "_catw", 0)) widget_set_prop_int(title, "_catw", 0);
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
